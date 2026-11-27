#!/usr/bin/env bash
# release.sh - integrate a branch and publish releases of this project (git-flow),
# GitHub variant: main and develop are protected, every merge goes through a
# pull request whose required checks must be green.
#
# Generic for Python projects with a src layout whose pyproject.toml reads
# the version from the package (version = { attr = "<pkg>.__version__" }).
# Same command line and questions as the Forgejo variant (dev-conventions
# handover, chapter 7 and 11).
#
# Usage:  tools/release.sh [patch|minor|major] [-m MESSAGE] [--yes] [--merge-only]
#
# The script looks at the current branch and does what fits it:
#
#   feature/<name>  rebases the branch onto develop and runs the quality
#                   gates. Then it asks "Build the release now?": n asks
#                   whether to merge it into develop only (collect further
#                   features first), y merges it and releases at once.
#                   Merging means: push the branch, pull request into
#                   develop, wait for the required checks, merge.
#   develop         releases the features collected on develop: asks for
#                   the version part (default minor) and the release message.
#   hotfix/<name>   releases an urgent fix, always as a patch, into main and
#                   back into develop. Branch it from main (git switch -c
#                   hotfix/<name> main) - it must not contain unreleased
#                   features of develop.
#   main            refused; main only receives releases.
#
# A release:
#
#   1. computes the version from the newest tag vX.Y.Z on main and the part
#      (patch 1.1.2 -> 1.1.3, minor -> 1.2.0, major -> 2.0.0); never edit
#      __version__ by hand,
#   2. sets __version__ and the CHANGELOG heading "## [X.Y.Z] - <date>"
#      (CHANGELOG.md must describe the change under "## [Unreleased]") on a
#      short-lived branch release/X.Y.Z (develop) or on the hotfix branch,
#   3. runs all quality gates (tools/check.sh) BEFORE anything is pushed,
#   4. asks once, then: pull request into main, required checks, merge with
#      the message "<tool> X.Y.Z: <release message>", annotated tag vX.Y.Z
#      with the same text on that merge, release check of the tag,
#   5. brings develop up to main through the helper branch merge/vX.Y.Z and
#      a pull request, and checks with tools/same-code.sh that main, develop
#      and the tag hold bit-identical code (after a hotfix while develop
#      collects features: main and the tag),
#   6. asks before ./install.sh vX.Y.Z, shows the installed version and runs
#      tools/after-install.sh vX.Y.Z if the project has one.
#
# Options:
#   patch|minor|major  version part; given on a feature branch it also
#                      answers "Build the release now?" with yes
#   -m, --message TEXT release message (one line); asked for if missing
#   -y, --yes          answer every question with yes (needs -m, except with
#                      --merge-only); without a part, develop releases a minor
#   --merge-only       feature branch: merge into develop, no release
#   -h, --help         this text
#
# Nothing leaves the machine before a question is answered with y (or
# --yes). If a step fails, the script stops and prints what is left to do.
#
# Runs as a normal user; needs git with access to origin and the GitHub CLI
# gh, logged in (gh auth login). CHECK_CMD replaces tools/check.sh,
# INSTALL_CMD replaces ./install.sh; the tests set them.
#
# Waiting for GitHub: GitHub registers the checks of a push with a delay.
# After every push the script asks GitHub every RELEASE_POLL seconds
# (default 5) whether the checks of the pull request - or the CI run of
# the tag - are registered, at most RELEASE_WAIT seconds (default 600).
# Then it watches them until they finish, without a time limit.
#
# Retries: GitHub answers now and then with a passing server error, or
# refuses a merge right after green checks. gh pr create and gh pr merge
# are therefore tried up to five times, RELEASE_RETRY_PAUSE seconds apart
# (default 10); without a pull request number the script stops.
#
# Time measurement: every step that can take long (gates, push,
# registration, checks, merge, release check, install, each answer to a
# question) prints its duration when it ends; the end of the run, also
# after a stop, lists all steps with their duration and the total
# ("Time measurement release.sh").
#
# Waiting for the registration shows every new answer of gh (e.g. "no checks
# reported on the ... branch") and a line every RELEASE_HEARTBEAT seconds
# (default 30) with the time waited so far, so a silent wait cannot happen.
#
# Watching the checks: one line per change of a check (time, check, state);
# while a check runs, a progress line per job whenever its step changes:
# bar of the finished steps, "done/total", the running step (e.g. "Run
# actions/setup-python@v6", "gate: tests") and the seconds since the job
# started. While nothing changes, every RELEASE_HEARTBEAT seconds the
# progress lines of the running jobs are printed again with the new time,
# so a hanging step shows. A red check prints its failed log (gh run view
# --log-failed, last 40 lines). The CI run of a tag is watched the same way.
#
# Only the REQUIRED checks of a pull request count (gh pr checks --required):
# when develop holds nothing new, merge/vX.Y.Z is the tagged commit itself,
# and its finished tag run (release check, skipped gates) would otherwise
# look like finished checks of the pull request (session-snap 2.1.4).
#
# gh versions: the script uses only "gh pr checks" without --json (its
# tab-separated output; Ubuntu 24.04 ships gh 2.45, which has no --json for
# this command) and "gh run view/list --json" (in gh since 2.x); job
# details are read with python3.
#
# Output: tools/output.sh (copied from tools/ of the dev-conventions
# bundle) must lie next to this script; it gives every
# project the same look (titles, PASS/FAIL lines, CI lines, colours).
set -euo pipefail
if [[ ! -f "$(dirname "${BASH_SOURCE[0]}")/output.sh" ]]; then
    echo "release.sh: tools/output.sh is missing - copy tools/output.sh of the dev-conventions bundle" >&2
    exit 2
fi
# shellcheck source=tools/output.sh disable=SC1091  # path chosen at run time
source "$(dirname "${BASH_SOURCE[0]}")/output.sh"

cd "$(git rev-parse --show-toplevel)"
TOOL="$(sed -n 's/^name = "\(.*\)"/\1/p' pyproject.toml | head -n 1)"
attr='s/^version = { *attr = "\([A-Za-z0-9_]*\)\.__version__" *}/\1/p'
PKG="$(sed -n "$attr" pyproject.toml | head -n 1)"
INIT="src/$PKG/__init__.py"
CHANGELOG="CHANGELOG.md"
CHECK_CMD="${CHECK_CMD:-tools/check.sh}"
INSTALL_CMD="${INSTALL_CMD:-./install.sh}"
WAIT="${RELEASE_WAIT:-600}"
POLL="${RELEASE_POLL:-5}"
RETRY_PAUSE="${RELEASE_RETRY_PAUSE:-10}"
HEARTBEAT="${RELEASE_HEARTBEAT:-30}"

die() { printf '%s✘ release.sh: %s%s\n' "$R$B" "$*" "$X" >&2; exit 1; }
tree() { git rev-parse "$1^{tree}"; }

[[ "$WAIT" =~ ^[0-9]+$ && "$POLL" =~ ^[0-9]+$ && "$RETRY_PAUSE" =~ ^[0-9]+$ ]] \
    || die "RELEASE_WAIT, RELEASE_POLL and RELEASE_RETRY_PAUSE must be whole seconds"
INTERVAL=$((POLL > 0 ? POLL : 1))
[[ -n "$TOOL" && -n "$PKG" && -f "$INIT" ]] \
    || die "pyproject.toml must name the project and read the version from src/<pkg>/__init__.py"
[[ -x tools/same-code.sh ]] || die "tools/same-code.sh is missing"

# --- time measurement --------------------------------------------------------------
STEPS=()
START=$SECONDS

# record LABEL SECONDS - print the duration of a finished step and remember it
record() {
    detail "[$(date +%T)] $1: $2 s"
    STEPS+=("$1"$'\t'"$2 s")
}

# timed LABEL COMMAND... - run a command, note its duration, keep its exit code
timed() {
    local label="$1" begin=$SECONDS code=0
    shift
    "$@" || code=$?
    record "$label" $((SECONDS - begin))
    return "$code"
}

# summary - list all noted steps and the total; runs at every end of the script
summary() {
    local line
    ((${#STEPS[@]})) || return 0
    title "Time measurement release.sh"
    for line in "${STEPS[@]}"; do timing "${line%%$'\t'*}" "${line#*$'\t'}"; done
    timing "${B}Total${X}" "$((SECONDS - START)) s"
}
# finish - result block at every end after the start: DONE or FAILED
DONE_TEXT="" STARTED=false
finish() {
    local code=$1
    $STARTED || return 0
    title "Result"
    if ((code == 0)); then
        info "${G}${B}DONE${X}  ${DONE_TEXT:-nothing left to do}"
    else
        info "${R}${B}FAILED${X}  the message above names what is unchanged and how to undo"
    fi
}
on_exit() {
    local code=$?
    summary
    finish "$code"
}
trap on_exit EXIT

# ask QUESTION - succeeds for y/Y (also j/J); --yes answers yes, end of input no.
ask() {
    local answer="" begin=$SECONDS
    if $YES; then echo "$1 [y/N] y"; return 0; fi
    read -r -p "$1 [y/N] " answer || answer=""
    record "Answer to: $1" $((SECONDS - begin))
    [[ "$answer" =~ ^[jJyY]$ ]]
}

# poll COMMAND... - repeat the command every $POLL s until it succeeds;
# fails when it has not succeeded after $WAIT s (it runs at least once)
poll() {
    local begin=$SECONDS
    until "$@"; do
        ((SECONDS - begin < WAIT)) || return 1
        sleep "$POLL"
    done
}

# --- options -----------------------------------------------------------------
PART="" MESSAGE="" YES=false MERGE_ONLY=false
while (($#)); do
    case "$1" in
        patch|minor|major) PART="$1" ;;
        -m|--message) (($# >= 2)) || die "-m needs a text"; MESSAGE="$2"; shift ;;
        -y|--yes) YES=true ;;
        --merge-only) MERGE_ONLY=true ;;
        -h|--help) awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"
                   exit 0 ;;
        *) die "usage: tools/release.sh [patch|minor|major] [-m MESSAGE] [--yes] [--merge-only]" ;;
    esac
    shift
done
if $MERGE_ONLY && [[ -n "$PART" ]]; then die "--merge-only and a version part exclude each other"; fi
if $YES && ! $MERGE_ONLY && [[ -z "$MESSAGE" ]]; then
    die "--yes needs the release message: -m \"...\""
fi

# --- GitHub helpers ----------------------------------------------------------------
# number of the open pull request from $1 into $2; created with title $3 if none
pull_request() {
    local number="" url attempt
    for attempt in 1 2 3 4 5; do
        number="$(gh pr list --head "$1" --base "$2" --state open --json number \
                  --jq '.[0].number // empty' || true)"
        [[ -n "$number" ]] && break
        # GitHub answers now and then with a passing server error
        # ("GraphQL: Something went wrong"); a pull request it created
        # anyway is found by the list above in the next attempt
        if url="$(gh pr create --head "$1" --base "$2" --title "$3" \
                  --body "Created by tools/release.sh")"; then
            number="${url##*/}"
            break
        fi
        note "creating the pull request failed (attempt $attempt/5), trying again" >&2
        sleep "$RETRY_PAUSE"
    done
    [[ "$number" =~ ^[0-9]+$ ]] || die "no pull request for $1 -> $2 - nothing merged"
    echo "$number"
}

# merge pull request $1 with subject $2; GitHub needs a moment after green
# checks before it reports the pull request as mergeable, so a refusal is
# retried a few times before the script stops
merge_pull_request() {
    local attempt
    for attempt in 1 2 3 4 5; do
        gh pr merge "$1" --merge --subject "$2" --body "" && return 0
        note "merging pull request #$1 failed (attempt $attempt/5), trying again" >&2
        sleep "$RETRY_PAUSE"
    done
    die "pull request #$1 could not be merged - see GitHub; nothing else changed"
}

# pr_checks PR - ask GitHub for the required checks of pull request $1
# (--required, in gh since 2.x: the head commit can also carry finished
# checks of another run, e.g. the release check of a tag when merge/vX.Y.Z
# is the tagged commit itself; they must not count). PR_LINES gets
# one line "name<TAB>state<TAB>elapsed<TAB>link" per check (state: pass, fail,
# pending, skipping, cancel), as gh prints them without a terminal; this form
# exists in every gh version, --json does not (gh 2.45 of Ubuntu 24.04).
# gh exits 1 (a check failed) or 8 (pending) although it answered, so its
# exit status is not used. Without check lines CHECKS_ERROR keeps the first
# line of what gh said (e.g. "no checks reported on the 'feature/x' branch").
PR_LINES="" CHECKS_ERROR="" PROGRESS_SHOWN=0
declare -A STEP_AT=() FIRST_SEEN=()
pr_checks() {
    local out
    out="$(gh pr checks "$1" --required 2>&1 </dev/null)" || true
    PR_LINES="$(printf '%s\n' "$out" \
        | awk -F'\t' 'NF >= 4 && $2 ~ /^(pass|fail|pending|skipping|cancel)$/')" || true
    CHECKS_ERROR=""
    [[ -n "$PR_LINES" ]] || CHECKS_ERROR="$(printf '%s\n' "$out" | sed -n '/[^[:space:]]/{p;q}')"
}

# run_ids - the CI runs named in the links of PR_LINES whose check is pending
run_ids() {
    printf '%s\n' "$PR_LINES" | awk -F'\t' '$2 == "pending" { print $4 }' \
        | sed -n 's|.*/actions/runs/\([0-9][0-9]*\).*|\1|p' | sort -u
}

# job_progress RUN - one line per job of CI run $1:
# "name<TAB>finished steps<TAB>all steps<TAB>running step<TAB>seconds since start<TAB>status"
# (an unknown value is "-", because read with IFS=tab skips empty fields)
job_progress() {
    gh run view "$1" --json jobs 2>/dev/null </dev/null | python3 -c '
import datetime, json, sys
try:
    jobs = json.load(sys.stdin).get("jobs") or []
except ValueError:
    jobs = []
now = datetime.datetime.now(datetime.timezone.utc)
for job in jobs:
    steps = job.get("steps") or []
    done = sum(1 for s in steps if s.get("status") == "completed")
    step = next((s.get("name", "") for s in steps if s.get("status") == "in_progress"), "")
    if not step:
        step = "queued for a runner" if job.get("status") in ("queued", "waiting", "pending") \
            else next((s.get("name", "") for s in steps if s.get("status") != "completed"), "")
    if not step and job.get("status") == "in_progress":
        step = "starting"
    seconds = "-"  # never an empty field: read with IFS=tab would skip it
    started = job.get("startedAt") or ""
    if started and not started.startswith("0001"):
        begin = datetime.datetime.fromisoformat(started.replace("Z", "+00:00"))
        seconds = str(max(0, int((now - begin).total_seconds())))
    print("\t".join([job.get("name") or "-", str(done), str(len(steps)), step or "-", seconds,
                     job.get("status") or "-"]))
' 2>/dev/null || true
}

# progress JOB DONE TOTAL STEP SECONDS - one CI line: bar of the finished
# steps, done/total, the running step and the seconds since the job started
progress() {
    local width=12 filled=0 full="" empty="" i
    (($3 > 0)) && filled=$(($2 * width / $3))
    for ((i = 0; i < filled; i++)); do full+="█"; done
    for ((i = filled; i < width; i++)); do empty+="░"; done
    local since=""
    [[ "$5" == "-" ]] || since="  ${D}$5 s$X"
    ci "$(date +%T)" "$1" "$4" "$G$full$D$empty$X $2/$3$since"
}

# show_progress FORCE RUN... - a progress line for every job of the runs
# whose step changed; FORCE=1 prints every job that has not finished (the
# heartbeat). PROGRESS_SHOWN=1 when a line was printed.
show_progress() {
    local force=$1 run name finished total step seconds status
    shift
    PROGRESS_SHOWN=0
    for run in "$@"; do
        while IFS=$'\t' read -r name finished total step seconds status; do
            [[ -n "$name" && "$status" != completed ]] || continue
            # gh without a start time of the job: count from the first sight of the running job
            if [[ "$seconds" == "-" && "$step" != "queued for a runner" ]]; then
                FIRST_SEEN[$name]="${FIRST_SEEN[$name]:-$SECONDS}"
                seconds=$((SECONDS - FIRST_SEEN[$name]))
            fi
            if [[ "$force" == 1 || "${STEP_AT[$name]:-}" != "$finished/$total $step" ]]; then
                STEP_AT[$name]="$finished/$total $step"
                progress "$name" "$finished" "$total" "$step" "$seconds"
                PROGRESS_SHOWN=1
            fi
        done < <(job_progress "$run")
    done
}

# wait_registered PR - wait until GitHub lists checks for pull request $1,
# at most WAIT s; every new answer of gh is shown, and a line every
# HEARTBEAT s with the time waited so far
wait_registered() {
    local begin=$SECONDS last=$SECONDS shown="-"
    while true; do
        pr_checks "$1"
        [[ -z "$PR_LINES" ]] || return 0
        if [[ "$CHECKS_ERROR" != "$shown" ]]; then
            shown="$CHECKS_ERROR" last=$SECONDS
            ci "$(date +%T)" "PR #$1" "" "${D}… gh: ${CHECKS_ERROR:-no answer}${X}"
        elif ((SECONDS - last >= HEARTBEAT)); then
            ci "$(date +%T)" "PR #$1" "" "${D}… still waiting, $((SECONDS - begin)) s of at most $WAIT s${X}"
            last=$SECONDS
        fi
        ((SECONDS - begin < WAIT)) || return 1
        sleep "$POLL"
    done
}

# failed_log - last 40 lines of the failed log of every red check in PR_LINES
failed_log() {
    local id
    for id in $(printf '%s\n' "$PR_LINES" | awk -F'\t' '$2 == "fail" { print $4 }' \
            | sed -n 's|.*/actions/runs/\([0-9][0-9]*\).*|\1|p' | sort -u); do
        note "failed log of run $id (last 40 lines):"
        gh run view "$id" --log-failed 2>/dev/null </dev/null | tail -n 40 | while IFS= read -r line; do
            detail "$line"
        done || true
    done
}

# watch_checks PR - follow the checks of pull request $1 until all finished:
# one line per change of a check, progress lines of the running jobs (see
# the header), at most five answers in a row without checks; succeeds when
# no check failed
watch_checks() {
    local name state elapsed link errors=0 last=$SECONDS begin=$SECONDS pending failed force
    local -a runs=()
    local -A seen=()
    while true; do
        pr_checks "$1"
        if [[ -z "$PR_LINES" ]]; then
            errors=$((errors + 1))
            if ((errors >= 5)); then
                fail "checks of PR #$1" "no checks in 5 answers"
                detail "gh: ${CHECKS_ERROR:-no answer}"
                return 2
            fi
            sleep "$INTERVAL"
            continue
        fi
        errors=0 pending=0 failed=0
        while IFS=$'\t' read -r name state elapsed link; do
            : "$elapsed" "$link"
            if [[ "${seen[$name]:-}" != "$state" ]]; then
                seen[$name]="$state"
                last=$SECONDS
                case "$state" in
                    pass)     ci "$(date +%T)" "$name" "" "${G}✔ pass${X}" ;;
                    fail)     ci "$(date +%T)" "$name" "" "${R}✘ fail${X}" ;;
                    cancel)   ci "$(date +%T)" "$name" "" "${R}✘ cancelled${X}" ;;
                    skipping) ci "$(date +%T)" "$name" "" "${Y}– skipped${X}" ;;
                    *)        ci "$(date +%T)" "$name" "" "${D}… $state${X}" ;;
                esac
            fi
            case "$state" in
                pass|skipping) ;;
                fail|cancel) failed=$((failed + 1)) ;;
                *) pending=$((pending + 1)) ;;
            esac
        done <<< "$PR_LINES"
        if ((pending == 0)); then
            ((failed == 0)) && return 0
            failed_log
            return 1
        fi
        force=0
        ((SECONDS - last >= HEARTBEAT)) && force=1
        mapfile -t runs < <(run_ids)
        PROGRESS_SHOWN=0
        ((${#runs[@]} == 0)) || show_progress "$force" "${runs[@]}"
        if ((PROGRESS_SHOWN)); then
            last=$SECONDS
        elif ((force)); then
            ci "$(date +%T)" "…" "" "${D}still running ($pending pending, $((SECONDS - begin)) s)${X}"
            last=$SECONDS
        fi
        sleep "$INTERVAL"
    done
}

# wait for the required checks of pull request $1: first until GitHub has
# registered them, then until they finish; a red check stops the script
checks() {
    local begin code=0
    info "Checks of PR #$1 ..."
    timed "Registration of the checks of PR #$1" wait_registered "$1" \
        || die "GitHub registered no checks for pull request #$1 within $WAIT s (last answer of gh: ${CHECKS_ERROR:-none}) - nothing merged; look at the pull request on GitHub"
    begin=$SECONDS
    watch_checks "$1" || code=$?
    record "Checks of PR #$1" $((SECONDS - begin))
    case "$code" in
        0) return 0 ;;
        1) die "required checks of pull request #$1 failed - nothing merged" ;;
        *) die "checks of pull request #$1 did not finish - nothing merged" ;;
    esac
}

# push branch $1, pull request into $2 (title $3), checks, merge with subject $3
integrate() {
    local number
    timed "Push $1" git push --quiet -u --force-with-lease origin "$1"
    number="$(pull_request "$1" "$2" "$3")"
    info "Pull Request #$number: $1 -> $2"
    checks "$number"
    timed "Merge of PR #$number" merge_pull_request "$number" "$3"
    git fetch --quiet --tags origin
    git switch --quiet "$2"
    git merge --quiet --ff-only "origin/$2"
    git push --quiet origin --delete "$1" 2>/dev/null || true
}

# tag_run TAG - id of the newest CI run of tag $1 (empty while none is registered)
tag_run() {
    gh run list --branch "$1" --limit 1 --json databaseId --jq '.[0].databaseId // empty'
}

# tag_registered TAG - succeeds once GitHub lists a CI run for tag $1
tag_registered() { [[ -n "$(tag_run "$1" 2>/dev/null)" ]]; }

# watch_run ID - follow CI run $1 until it ends: one line per change of its
# status, progress lines of its jobs (see the header), the failed log when
# it failed
watch_run() {
    local state previous="" errors=0 last=$SECONDS begin=$SECONDS force
    while true; do
        if ! state="$(gh run view "$1" --json status,conclusion --jq '.status + " " + .conclusion' 2>/dev/null)"; then
            errors=$((errors + 1))
            ((errors < 5)) || { fail "CI run $1" "GitHub did not answer 5 times"; return 2; }
            sleep "$INTERVAL"
            continue
        fi
        errors=0
        if [[ "$state" != "$previous" ]]; then
            previous="$state" last=$SECONDS
            case "$state" in
                "completed success") ci "$(date +%T)" "run $1" "" "${G}✔ success${X}" ;;
                completed*)          ci "$(date +%T)" "run $1" "" "${R}✘ ${state#completed }${X}" ;;
                *)                   ci "$(date +%T)" "run $1" "" "${D}… ${state% }${X}" ;;
            esac
        fi
        case "$state" in
            "completed success") return 0 ;;
            completed*)
                note "failed log of run $1 (last 40 lines):"
                gh run view "$1" --log-failed 2>/dev/null | tail -n 40 | while IFS= read -r line; do
                    detail "$line"
                done || true
                return 1 ;;
        esac
        force=0
        ((SECONDS - last >= HEARTBEAT)) && force=1
        show_progress "$force" "$1"
        if ((PROGRESS_SHOWN)); then
            last=$SECONDS
        elif ((force)); then
            ci "$(date +%T)" "…" "" "${D}still running ($((SECONDS - begin)) s)${X}"
            last=$SECONDS
        fi
        sleep "$INTERVAL"
    done
}

# watch the CI run of tag $1 (release check: tag on main, tag = version)
tag_check() {
    local id
    timed "Registration of the release check of $1" poll tag_registered "$1" \
        || die "no CI run appeared for $1 within $WAIT s - check it on GitHub"
    id="$(tag_run "$1")"
    timed "Release check of $1" watch_run "$id" || die "release check of $1 failed - see GitHub"
}

# --- starting point ------------------------------------------------------------
STARTED=true
[[ -z "$(git status --porcelain)" ]] || die "uncommitted changes - commit or stash them first"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
case "$BRANCH" in
    feature/?*) KIND=feature ;;
    hotfix/?*)  KIND=hotfix ;;
    develop)    KIND=develop ;;
    main)       die "main only receives releases - use feature/<name>, develop or hotfix/<name>" ;;
    *)          die "branch $BRANCH: expected feature/<name>, develop or hotfix/<name>" ;;
esac
if [[ "$KIND" == hotfix && -n "$PART" && "$PART" != patch ]]; then
    die "a hotfix is always a patch release"
fi
if $MERGE_ONLY && [[ "$KIND" != feature ]]; then die "--merge-only is for feature branches"; fi
command -v gh >/dev/null || die "the GitHub CLI gh is required (sudo apt install gh)"
gh auth status >/dev/null 2>&1 || die "gh is not logged in - run: gh auth login"

git fetch --quiet --tags origin
for branch in main develop; do
    git switch --quiet "$branch"
    git merge --quiet --ff-only "origin/$branch" \
        || die "$branch differs from origin/$branch - clean that up first"
done
git switch --quiet "$BRANCH"
git merge-base --is-ancestor main develop \
    || die "develop does not contain main (hotfix not merged back?) - merge main into develop first"

LAST="$(git describe --tags --abbrev=0 --match 'v[0-9]*.[0-9]*.[0-9]*' main 2>/dev/null)" \
    || die "no release tag vX.Y.Z on main"
grep -q "^__version__ = \"${LAST#v}\"$" "$INIT" \
    || die "$INIT does not say ${LAST#v} (version of $LAST) - was it changed by hand?"

# --- feature: rebase, gates, merge into develop ----------------------------------
if [[ "$KIND" == feature ]]; then
    title "Integrate $BRANCH"
    info "Feature $BRANCH: rebase onto develop"
    if ! git rebase --quiet develop; then
        git rebase --abort
        die "rebase of $BRANCH onto develop has conflicts - nothing changed; resolve with:
  git rebase develop   (fix the files, git add ..., git rebase --continue)
and start tools/release.sh again"
    fi
    # shellcheck disable=SC2086  # CHECK_CMD may carry arguments
    timed "Gates $BRANCH" $CHECK_CMD || die "quality gates failed on $BRANCH - develop is unchanged"
    if $MERGE_ONLY; then
        RELEASE=false
    elif [[ -n "$PART" ]] || ask "Build the release now?"; then
        RELEASE=true
    else
        RELEASE=false
    fi
    if ! $RELEASE && ! $MERGE_ONLY && ! ask "Merge $BRANCH into develop (push, PR, checks)?"; then
        note "Not pushed; develop is unchanged. Later: tools/release.sh on $BRANCH"
        exit 0
    fi
    integrate "$BRANCH" develop "Merge $BRANCH into develop"
    pass "$BRANCH is merged into develop" ""
    DONE_TEXT="$BRANCH is merged into develop"
    note "Delete the local feature branch with: git branch -d $BRANCH"
    $RELEASE || exit 0
    KIND=develop
    BRANCH=develop
fi

# --- release: version, message, gates --------------------------------------------
if [[ "$KIND" == develop ]]; then
    [[ "$(tree develop)" != "$(tree main)" ]] \
        || die "develop holds the same code as main - nothing to release"
else
    shared="$(comm -12 <(git rev-list main..develop | sort) <(git rev-list "main..$BRANCH" | sort))"
    [[ -z "$shared" ]] || die "$BRANCH contains unreleased commits of develop - a hotfix
must start from main: git switch -c hotfix/<name> main  (then git cherry-pick the fix)"
    PART="patch"
fi
if [[ -z "$PART" ]]; then
    if $YES; then PART=minor; else
        begin=$SECONDS
        read -r -p "Release part patch/minor/major [minor]: " PART || PART=""
        record "Answer to: release part" $((SECONDS - begin))
        PART="${PART:-minor}"
    fi
fi
IFS=. read -r MAJOR MINOR PATCH <<<"${LAST#v}"
case "$PART" in
    patch) PATCH=$((PATCH + 1)) ;;
    minor) MINOR=$((MINOR + 1)); PATCH=0 ;;
    major) MAJOR=$((MAJOR + 1)); MINOR=0; PATCH=0 ;;
    *)     die "release part must be patch, minor or major, not '$PART'" ;;
esac
VERSION="$MAJOR.$MINOR.$PATCH"
TAG="v$VERSION"
git ls-remote --exit-code --tags origin "refs/tags/$TAG" >/dev/null 2>&1 \
    && die "tag $TAG exists already on origin"
git rev-parse --quiet --verify "refs/tags/$TAG" >/dev/null && die "tag $TAG exists already"
awk '/^## \[Unreleased\]/ { inside = 1; next } /^## \[/ { inside = 0 }
     inside && NF { found = 1 } END { exit !found }' "$CHANGELOG" \
    || die "$CHANGELOG: nothing under ## [Unreleased] - describe the change first"
if [[ -z "$MESSAGE" ]]; then
    begin=$SECONDS
    read -r -p "Release message (one line): " MESSAGE || MESSAGE=""
    record "Answer to: release message" $((SECONDS - begin))
fi
[[ -n "${MESSAGE//[[:space:]]/}" ]] || die "the release message is empty"
SUBJECT="$TOOL $VERSION: $MESSAGE"
title "Release $TAG"
info "Release: $LAST -> $TAG from $BRANCH - $MESSAGE"

if [[ "$KIND" == develop ]]; then
    RB="release/$VERSION"
    git rev-parse --quiet --verify "refs/heads/$RB" >/dev/null \
        && die "branch $RB exists already (left by an earlier run?) - delete it: git branch -D $RB"
    git switch --quiet -c "$RB" develop
    DROP="git switch develop && git branch -D $RB"
else
    RB="$BRANCH"
    DROP="git switch $BRANCH && git reset --hard origin/$BRANCH 2>/dev/null || git reset --hard HEAD~1"
fi
sed -i "s/^__version__ = .*/__version__ = \"$VERSION\"/" "$INIT"
sed -i "0,/^## \[Unreleased\]$/s//## [Unreleased]\n\n## [$VERSION] - $(date +%F)/" "$CHANGELOG"
# shellcheck disable=SC2086  # CHECK_CMD may carry arguments
if ! timed "Gates $RB" $CHECK_CMD; then
    git checkout -- "$INIT" "$CHANGELOG"
    if [[ "$KIND" == develop ]]; then git switch --quiet develop; git branch -D "$RB" >/dev/null; fi
    die "quality gates failed - nothing was released; main and develop are as before"
fi
git commit --quiet -am "$TOOL $VERSION"

# --- release: pull request into main, tag ----------------------------------------
echo
if ! ask "Push $RB, merge it into main and publish $TAG?"; then
    note "Not pushed. Discard with: $DROP"
    exit 0
fi
integrate "$RB" main "$SUBJECT"
[[ "$(tree main)" == "$(tree "$RB")" ]] \
    || die "main after the merge differs from $RB - check on GitHub before tagging"
git tag -a "$TAG" -m "$SUBJECT"
timed "Push $TAG" git push --quiet origin "$TAG"
info "Release check of $TAG ..."
tag_check "$TAG"
pass "$TAG is published" ""
DONE_TEXT="$TAG is published"

# --- back into develop -------------------------------------------------------------
MB="merge/$TAG"
git switch --quiet -C "$MB" main
if ! git merge --quiet --no-ff develop -m "Merge develop into $MB" >/dev/null; then
    git merge --abort
    git switch --quiet main
    die "$TAG is released, but merging it back into develop has conflicts. Finish by hand:
  git switch $MB && git merge --no-ff develop   (fix the files, git add ..., git commit)
  git push -u origin $MB && gh pr create --base develop --head $MB --fill
then merge the pull request once its checks are green"
fi
integrate "$MB" develop "Merge $TAG into develop"
git branch -q -D "$MB" "$RB" 2>/dev/null || true

# --- bit-identical? --------------------------------------------------------------
echo
if [[ "$(tree develop)" == "$(tree main)" ]] || [[ "$KIND" == develop ]]; then
    tools/same-code.sh main develop "$TAG" || die "main, develop and $TAG differ - check them"
else
    tools/same-code.sh main "$TAG" || die "main and $TAG differ - check them"
    note "develop also holds the features that are not released yet."
fi

# --- install ------------------------------------------------------------------------
echo
if ask "Install $TAG now (./install.sh $TAG)?"; then
    # shellcheck disable=SC2086  # INSTALL_CMD may carry arguments
    timed "Installation $TAG" $INSTALL_CMD "$TAG"
    "$HOME/.local/bin/$TOOL" --version \
        || note "Warning: $HOME/.local/bin/$TOOL --version failed" >&2
    if [[ -x tools/after-install.sh ]]; then
        tools/after-install.sh "$TAG" || note "Warning: tools/after-install.sh failed" >&2
    fi
else
    note "Install later with: ./install.sh $TAG"
fi
