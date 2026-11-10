#!/usr/bin/env bash
# apply-release.sh - apply mediatools release patches from the download folder.
#
# One script for every release (replaces the per-version apply-mediatools-X.sh):
#
#   1. Looks for patch archives "mediatools-hotfix-X.Y.Z-patch.tar.gz" in the
#      download folder. Browser copies such as "... (1).tar.gz" are handled:
#      per version the most recently saved copy is used.
#   2. Compares them with the released version on main. Older or equal
#      versions are stale; newer ones are pending.
#   3. Asks which version to apply: a version means "every pending version up
#      to and including this one, in ascending order" (each patch is based on
#      the release before it). Default: all pending versions.
#   4. Per version (git-flow hotfix): patch -> hotfix branch -> PR into main
#      -> CI -> merge -> tag vX -> CI of the tag -> back-merge into develop.
#      Each version causes two local gate runs (pre-push hook) and three CI
#      runs on GitHub; each is announced with its purpose and URL, and a
#      summary table of all CI runs closes the output:
#        local  push hotfix/X           gates on this PC, one Python version
#        CI 1   PR hotfix/X -> main     merge result; required check for the merge
#        CI 2   tag vX                  released commit + release-check
#        local  push merge/hotfix-X     gates on this PC, one Python version
#        CI 3   PR merge/hotfix-X -> develop   merge result; required check
#      Merges into main and develop start no further run (no push trigger).
#      Reading GitHub calls are retried (5 attempts, 10 s apart), so a short
#      network loss does not stop the update; a red CI run still does.
#   5. Installs the newest release (install.sh), checks --version and asks a
#      running shrink to switch to it after its current file (mediatools reload).
#   6. Moves the used archives to ~/scripts/mediatools.d/releases/ and deletes
#      stale archives, browser copies and old apply scripts from the folder.
#
# Usage (normal user; git and gh act on your clone and with your GitHub
# login, nothing here needs root). The script ships inside the package:
#     mediatools update              # ask, then apply
#     mediatools update --list       # only show
#     mediatools update -y           # all pending versions, no question
#   options: --downloads DIR (default ~/Downloads),
#            --repo DIR (default: the clone around this file, else
#            ~/Projects/mediatools)
# A standalone copy (e.g. ~/Downloads/apply-release.sh) works the same way:
#     bash ~/Downloads/apply-release.sh
#
# The script runs from a temporary copy, because the branch switches below
# and the installation rewrite this file while bash reads it.
# Every step is logged; the first failing command stops the script and names
# the step. On failure nothing in the download folder is removed, so the same
# command can be repeated (an existing hotfix branch is rebuilt, an open pull
# request reused).
set -Eeuo pipefail

# -- run from a temporary copy ----------------------------------------------
# Only the copy made here is ever deleted: the check compares $0 with the
# copy's own path, so values inherited by a nested call (e.g. the tests run
# by the pre-push hook) cannot make the script delete the original.
if [ "${APPLY_RELEASE_COPY:-}" != "$0" ]; then
  copy=$(mktemp /tmp/apply-release.XXXXXX.sh)
  cp "$0" "$copy"
  APPLY_RELEASE_ORIGIN="$(readlink -f "$0")" APPLY_RELEASE_COPY="$copy" \
    exec bash "$copy" "$@"
fi
trap 'rm -f -- "$APPLY_RELEASE_COPY"' EXIT
export -n APPLY_RELEASE_ORIGIN APPLY_RELEASE_COPY   # keep them from git hooks

# -- options -------------------------------------------------------------------
DL="$HOME/Downloads"
REPO=""
LIST=0
YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --list) LIST=1 ;;
    -y|--yes) YES=1 ;;
    --downloads) DL="$2"; shift ;;
    --repo) REPO="$2"; shift ;;
    -h|--help) sed -n '2,/^set -Eeuo/{/^set -Eeuo/!p;}' "$APPLY_RELEASE_ORIGIN"; exit 0 ;;
    *) echo "unknown option: $1 (see --help)"; exit 2 ;;
  esac
  shift
done
if [ -z "$REPO" ]; then                     # the clone this script belongs to
  REPO=$(git -C "$(dirname "$APPLY_RELEASE_ORIGIN")" rev-parse --show-toplevel 2>/dev/null || true)
  [ -f "$REPO/src/mediatools/__init__.py" ] || REPO="$HOME/Projects/mediatools"
fi
ARCHIVE="$HOME/scripts/mediatools.d/releases"
LOG_DIR="$HOME/scripts/mediatools.d/logs"
VENV="$HOME/.venvs/mediatools"
NAME_RE='^mediatools-hotfix-([0-9]+\.[0-9]+\.[0-9]+)-patch( \([0-9]+\))?\.tar\.gz$'
STEP="start"

step() { STEP="$1"; echo; echo "=== $STEP"; }

# announce the local gate run of the pre-push hook before a branch push
local_gates() {
  echo "--- local gates (pre-push hook): tools/check.sh on this PC with $(python3 --version 2>&1);"
  echo "    GitHub starts no run for this push ($1 has no push trigger)"
}

# run a reading gh command; a failed attempt (network, API hiccup) is retried
# up to GH_TRIES times, GH_PAUSE seconds apart; output only of the good attempt
GH_TRIES=5
GH_PAUSE="${GH_PAUSE:-10}"
gh_read() {
  local attempt out
  for attempt in $(seq 1 "$GH_TRIES"); do
    if out=$(gh "$@"); then
      printf '%s\n' "$out"
      return 0
    fi
    echo "    gh $1 $2 failed (attempt $attempt/$GH_TRIES), retry in $GH_PAUSE s" >&2
    sleep "$GH_PAUSE"
  done
  echo "    gh $1 $2 failed $GH_TRIES times, giving up" >&2
  return 1
}

# CI runs of this invocation, one line each for the summary table
declare -a CI_ROWS=()

# announce a CI run, wait for GitHub to register it, follow it, record the result
#   $1 label (e.g. "1.2.13 CI 1/3")  $2 ref  $3 event  $4 commit  $5 what  $6 why
#   $7 PR url (empty: follow the run itself)
ci_run() {
  local label="$1" ref="$2" event="$3" sha="$4" what="$5" why="$6" pr="${7:-}" id url
  echo "--- $label: $event on $ref (commit ${sha:0:7})"
  echo "    checks:  $what"
  echo "    purpose: $why"
  wait_for_run "$ref" "$event" "$sha"
  id=$(gh_read run list --branch "$ref" --event "$event" --commit "$sha" --limit 1 \
         --json databaseId --jq '.[0].databaseId')
  url=$(gh_read run view "$id" --json url --jq .url)
  echo "    run:     $url"
  CI_ROWS+=("$label|$event $ref|running|$url")
  # Watching ends with a non-zero code both for a red run and for a lost
  # connection; the state of the run on GitHub decides which one it was.
  local rc=1 attempt state
  for attempt in $(seq 1 "$GH_TRIES"); do
    if [ -n "$pr" ]; then
      gh pr checks "$pr" --watch || true
    else
      gh run watch "$id" --exit-status || true
    fi
    state=$(gh_read run view "$id" --json status,conclusion --jq '.status + " " + .conclusion')
    case "$state" in
      "completed success") rc=0; break ;;
      completed*) rc=1; break ;;
    esac
    echo "    watching interrupted, run is '$state' (attempt $attempt/$GH_TRIES), retry in $GH_PAUSE s"
    sleep "$GH_PAUSE"
  done
  CI_ROWS[-1]="$label|$event $ref|${state#completed }|$url"
  return "$rc"
}

# table of all CI runs of this invocation (also printed when a step fails)
ci_summary() {
  [ ${#CI_ROWS[@]} -gt 0 ] || return 0
  echo
  echo "CI runs on GitHub:"
  printf '  %-16s %-42s %-10s %s\n' "run" "trigger" "result" "url"
  local row label trigger result url
  for row in "${CI_ROWS[@]}"; do
    IFS='|' read -r label trigger result url <<< "$row"
    printf '  %-16s %-42s %-10s %s\n' "$label" "$trigger" "$result" "$url"
  done
}
version_of() { basename "$1" | sed -E 's/^[a-z-]*-([0-9]+\.[0-9]+\.[0-9]+).*/\1/'; }
# true if version $1 is lower than version $2
version_lt() { [ "$1" != "$2" ] && [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -1)" = "$1" ]; }

# released version on main (origin/main if known)
released_version() {
  local ref=main
  git -C "$REPO" rev-parse -q --verify origin/main >/dev/null && ref=origin/main
  git -C "$REPO" show "$ref:src/mediatools/__init__.py" \
    | sed -n 's/^__version__ = "\(.*\)"$/\1/p'
}

# lines "version<TAB>path" of all patch archives, newest copy first per version
scan() {
  find "$DL" -maxdepth 1 -type f -name 'mediatools-hotfix-*-patch*.tar.gz' -printf '%T@\t%p\n' \
    | while IFS=$'\t' read -r mtime path; do
        [[ "$(basename "$path")" =~ $NAME_RE ]] && printf '%s\t%s\t%s\n' \
          "${BASH_REMATCH[1]}" "$mtime" "$path"
      done \
    | sort -t$'\t' -k1,1V -k2,2nr | cut -f1,3
}

# wait until GitHub has registered at least one run for a branch or tag
wait_for_run() {
  local ref="$1" event="$2" sha="${3:-}" n
  for _ in $(seq 1 30); do
    if [ -n "$sha" ]; then
      n=$(gh_read run list --branch "$ref" --event "$event" --commit "$sha" --limit 1 \
            --json databaseId --jq length)
    else
      n=$(gh_read run list --branch "$ref" --event "$event" --limit 1 --json databaseId \
            --jq length)
    fi
    [ "$n" -ge 1 ] && return 0
    sleep 10
  done
  echo "no CI run appeared for $ref ($event) within 5 minutes"
  return 1
}

# -- inventory -------------------------------------------------------------------
# root would leave root-owned files in your clone and home (tests set the variable)
[ "$(id -u)" -ne 0 ] || [ -n "${APPLY_RELEASE_ALLOW_ROOT:-}" ] \
  || { echo "do not run as root"; exit 1; }
[ -d "$REPO/.git" ] || { echo "no git clone at $REPO (use --repo DIR)"; exit 1; }
if [ "$LIST" -eq 0 ]; then
  mkdir -p "$LOG_DIR"
  LOG="$LOG_DIR/apply-release_$(date +%Y-%m-%d_%H-%M-%S).log"
  exec > >(tee -a "$LOG") 2>&1
  # shellcheck disable=SC2154  # rc is assigned inside the trap string
  trap 'rc=$?; ci_summary; echo; echo "FAILED in step: $STEP (line $LINENO, exit code $rc)"; echo "log: $LOG"' ERR
  git -C "$REPO" fetch -q origin
fi
CURRENT=$(released_version)
[ -n "$CURRENT" ] || { echo "cannot read the released version from main in $REPO"; exit 1; }

declare -a PENDING=() USE=() STALE=()
last=""
while IFS=$'\t' read -r version path; do
  if [ "$version" = "$last" ]; then
    STALE+=("$path")                         # older copy of the same version
  elif version_lt "$CURRENT" "$version"; then
    PENDING+=("$version"); USE+=("$path")
  else
    STALE+=("$path")                         # released already
  fi
  last="$version"
done < <(scan)

echo "released on main: $CURRENT   (clone: $REPO)"
echo "download folder:  $DL"
if [ ${#PENDING[@]} -eq 0 ]; then
  echo "pending patches:  none"
else
  for i in "${!PENDING[@]}"; do
    printf '  pending  %-8s %s\n' "${PENDING[$i]}" "$(basename "${USE[$i]}")"
  done
fi
for path in "${STALE[@]}"; do
  printf '  stale    %-8s %s\n' "$(version_of "$path")" "$(basename "$path")"
done
[ "$LIST" -eq 1 ] && exit 0

# -- choice ----------------------------------------------------------------------
TARGET=""
if [ ${#PENDING[@]} -gt 0 ]; then
  TARGET="${PENDING[-1]}"
  if [ "$YES" -eq 0 ]; then
    echo
    echo "Apply up to which version? Every pending version up to it is applied in order."
    read -r -p "version [${PENDING[*]}] (Enter = $TARGET, n = none): " answer
    case "$answer" in
      "") ;;
      n|N) TARGET="" ;;
      *) [[ " ${PENDING[*]} " == *" $answer "* ]] || { echo "not a pending version: $answer"; exit 1; }
         TARGET="$answer" ;;
    esac
  fi
fi

# -- apply -------------------------------------------------------------------------
apply_one() {
  local VERSION="$1" tar="$2" dir
  dir=$(mktemp -d /tmp/mediatools-patch.XXXXXX)
  step "$VERSION: 0 check the archive"
  echo "archive: $tar"
  tar -xzf "$tar" -C "$dir"
  grep -q "^+__version__ = \"$VERSION\"" "$dir"/*/*.patch \
    || { echo "$tar does not contain release $VERSION"; exit 1; }
  ! git ls-remote --exit-code --tags origin "v$VERSION" >/dev/null \
    || { echo "tag v$VERSION exists already"; exit 1; }

  step "$VERSION: 1 update main"
  git switch main
  git pull --ff-only

  step "$VERSION: 2 apply the patch on hotfix/$VERSION (rebuilt from main if it exists)"
  git am --abort 2>/dev/null || true         # leftover of a failed earlier attempt
  git switch -C "hotfix/$VERSION" main
  git am "$dir"/*/*.patch
  git log --oneline -1

  step "$VERSION: 3 push (pre-push hook runs all quality gates)"
  local_gates "hotfix/$VERSION"
  git fetch origin
  git push -u --force-with-lease origin "hotfix/$VERSION"

  step "$VERSION: 4 pull request into main"
  local pr
  pr=$(gh_read pr list --head "hotfix/$VERSION" --base main --state open --json url \
         --jq '.[0].url // empty')
  [ -n "$pr" ] || pr=$(gh pr create --base main --title "Hotfix $VERSION" --fill)
  echo "PR: $pr"
  sleep 15                                   # let GitHub register the new push
  ci_run "$VERSION CI 1/3" "hotfix/$VERSION" pull_request "$(git rev-parse HEAD)" \
    "merge result of hotfix/$VERSION and main, Python 3.10/3.12/3.13/3.14" \
    "required check: branch protection allows the merge into main only when green" "$pr"

  step "$VERSION: 5 merge into main"
  gh pr merge "$pr" --merge --delete-branch
  git switch main
  git pull --ff-only
  grep -qx "__version__ = \"$VERSION\"" src/mediatools/__init__.py

  step "$VERSION: 6 tag v$VERSION"
  git tag -a "v$VERSION" -m "mediatools $VERSION"
  git push origin "v$VERSION"                # tag push: the hook skips the gates
  ci_run "$VERSION CI 2/3" "v$VERSION" push "$(git rev-parse HEAD)" \
    "the released commit on main, all versions, then release-check (tag on main, tag = version)" \
    "release gate: the back-merge into develop starts only after it is green"

  step "$VERSION: 7 back-merge into develop (helper branch)"
  git switch -C "merge/hotfix-$VERSION"
  git fetch origin
  git merge --no-ff origin/develop -m "Merge develop into merge/hotfix-$VERSION"
  local_gates "merge/hotfix-$VERSION"
  git push -u --force-with-lease origin "merge/hotfix-$VERSION"
  pr=$(gh_read pr list --head "merge/hotfix-$VERSION" --base develop --state open --json url \
         --jq '.[0].url // empty')
  [ -n "$pr" ] || pr=$(gh pr create --base develop --title "Merge hotfix $VERSION into develop" --fill)
  echo "PR: $pr"
  ci_run "$VERSION CI 3/3" "merge/hotfix-$VERSION" pull_request "$(git rev-parse HEAD)" \
    "merge result of the release and develop, Python 3.10/3.12/3.13/3.14" \
    "required check: branch protection allows the merge into develop only when green" "$pr"
  gh pr merge "$pr" --merge --delete-branch
  git switch develop
  git pull --ff-only
  rm -rf "$dir"
}

APPLIED=()
if [ -n "$TARGET" ]; then
  step "preconditions"
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
  cd "$REPO"
  if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "uncommitted changes in $REPO"; exit 1
  fi
  for i in "${!PENDING[@]}"; do
    apply_one "${PENDING[$i]}" "${USE[$i]}"
    APPLIED+=("$i")
    [ "${PENDING[$i]}" = "$TARGET" ] && break
  done

  step "install the release"
  deactivate
  hash -r
  "$REPO/install.sh"
  installed=$("$HOME/scripts/mediatools" --version)
  echo "$installed"
  [ "$installed" = "mediatools $TARGET" ]
  # a shrink running in the background switches to the new release after its
  # current file (prints "nothing to reload" if none runs)
  "$HOME/scripts/mediatools" reload || true
  "$HOME/scripts/mediatools" status || true  # which release runs, and whether it switches
fi

# -- tidy the download folder --------------------------------------------------------
step "archive used patches, remove stale downloads"
mkdir -p "$ARCHIVE"
for i in "${APPLIED[@]}"; do
  mv -v -- "${USE[$i]}" "$ARCHIVE/mediatools-hotfix-${PENDING[$i]}-patch.tar.gz"
done
for path in "${STALE[@]}"; do
  rm -v -- "$path"
done
# per-version apply scripts of earlier releases and downloaded copies of this one
find "$DL" -maxdepth 1 -type f \( -name 'apply-mediatools-*.sh' -o -name 'apply-release*.sh' \) \
  ! -samefile "$APPLY_RELEASE_ORIGIN" -print0 | xargs -0 -r rm -v --
if [ "$(dirname "$APPLY_RELEASE_ORIGIN")" = "$(readlink -f "$DL")" ] \
   && [ -f "$REPO/src/mediatools/update/apply-release.sh" ]; then
  rm -v -- "$APPLY_RELEASE_ORIGIN"           # the clone has its own copy now
fi

step "done"
ci_summary
if [ ${#APPLIED[@]} -gt 0 ]; then
  echo "applied: $(for i in "${APPLIED[@]}"; do printf '%s ' "${PENDING[$i]}"; done)- installed $TARGET"
else
  echo "nothing applied"
fi
echo "log: $LOG"
