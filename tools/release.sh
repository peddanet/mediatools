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
#                   gates. Then it asks "Release jetzt bauen?": n asks
#                   whether to merge it into develop only (collect further
#                   features first), j merges it and releases at once.
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
#                      answers "Release jetzt bauen?" with yes
#   -m, --message TEXT release message (one line); asked for if missing
#   -y, --yes          answer every question with yes (needs -m, except with
#                      --merge-only); without a part, develop releases a minor
#   --merge-only       feature branch: merge into develop, no release
#   -h, --help         this text
#
# Nothing leaves the machine before a question is answered with j (or
# --yes). If a step fails, the script stops and prints what is left to do.
#
# Runs as a normal user; needs git with access to origin and the GitHub CLI
# gh, logged in (gh auth login). CHECK_CMD replaces tools/check.sh,
# INSTALL_CMD replaces ./install.sh, RELEASE_WAIT (seconds, default 30) is
# the pause after a push before the checks are watched (GitHub registers
# them with a delay); the tests set them.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
TOOL="$(sed -n 's/^name = "\(.*\)"/\1/p' pyproject.toml | head -n 1)"
attr='s/^version = { *attr = "\([A-Za-z0-9_]*\)\.__version__" *}/\1/p'
PKG="$(sed -n "$attr" pyproject.toml | head -n 1)"
INIT="src/$PKG/__init__.py"
CHANGELOG="CHANGELOG.md"
CHECK_CMD="${CHECK_CMD:-tools/check.sh}"
INSTALL_CMD="${INSTALL_CMD:-./install.sh}"
WAIT="${RELEASE_WAIT:-30}"

die() { echo "release.sh: $*" >&2; exit 1; }
tree() { git rev-parse "$1^{tree}"; }

[[ -n "$TOOL" && -n "$PKG" && -f "$INIT" ]] \
    || die "pyproject.toml must name the project and read the version from src/<pkg>/__init__.py"
[[ -x tools/same-code.sh ]] || die "tools/same-code.sh is missing"

# ask QUESTION - succeeds for j/J/y/Y; --yes answers yes, end of input no.
ask() {
    local answer=""
    if $YES; then echo "$1 [j/N] j"; return 0; fi
    read -r -p "$1 [j/N] " answer || answer=""
    [[ "$answer" =~ ^[jJyY]$ ]]
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
    local number url
    number="$(gh pr list --head "$1" --base "$2" --state open --json number \
              --jq '.[0].number // empty')"
    if [[ -z "$number" ]]; then
        url="$(gh pr create --head "$1" --base "$2" --title "$3" \
               --body "Created by tools/release.sh")"
        number="${url##*/}"
    fi
    echo "$number"
}

# wait for the required checks of pull request $1; a lost connection while
# watching is retried, a red check stops the script
checks() {
    local attempt code
    echo "Pruefungen von PR #$1 ..."
    sleep "$WAIT"
    for attempt in 1 2 3 4 5; do
        if gh pr checks "$1" --watch; then
            return 0
        fi
        code=0
        gh pr checks "$1" >/dev/null 2>&1 || code=$?
        case "$code" in
            0) return 0 ;;
            8) echo "checks still pending (attempt $attempt/5), watching again" ;;
            *) die "required checks of pull request #$1 failed - nothing merged" ;;
        esac
    done
    die "checks of pull request #$1 did not finish - nothing merged"
}

# push branch $1, pull request into $2 (title $3), checks, merge with subject $3
integrate() {
    local number
    git push --quiet -u --force-with-lease origin "$1"
    number="$(pull_request "$1" "$2" "$3")"
    echo "Pull Request #$number: $1 -> $2"
    checks "$number"
    gh pr merge "$number" --merge --subject "$3" --body ""
    git fetch --quiet --tags origin
    git switch --quiet "$2"
    git merge --quiet --ff-only "origin/$2"
    git push --quiet origin --delete "$1" 2>/dev/null || true
}

# watch the CI run of tag $1 (release check: tag on main, tag = version)
tag_check() {
    local id="" attempt
    sleep "$WAIT"
    for attempt in 1 2 3 4 5 6 7 8 9 10; do
        id="$(gh run list --branch "$1" --limit 1 --json databaseId --jq '.[0].databaseId // empty')"
        [[ -n "$id" ]] && break
        echo "waiting for the CI run of $1 (attempt $attempt/10)"
        sleep 10
    done
    [[ -n "$id" ]] || die "no CI run appeared for $1 - check it on GitHub"
    gh run watch "$id" --exit-status >/dev/null || die "release check of $1 failed - see GitHub"
}

# --- starting point ------------------------------------------------------------
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
    echo "Feature $BRANCH: rebase auf develop"
    if ! git rebase --quiet develop; then
        git rebase --abort
        die "rebase of $BRANCH onto develop has conflicts - nothing changed; resolve with:
  git rebase develop   (fix the files, git add ..., git rebase --continue)
and start tools/release.sh again"
    fi
    $CHECK_CMD || die "quality gates failed on $BRANCH - develop is unchanged"
    if $MERGE_ONLY; then
        RELEASE=false
    elif [[ -n "$PART" ]] || ask "Release jetzt bauen?"; then
        RELEASE=true
    else
        RELEASE=false
    fi
    if ! $RELEASE && ! $MERGE_ONLY && ! ask "$BRANCH in develop mergen (Push, PR, Checks)?"; then
        echo "Nicht gepusht; develop ist unveraendert. Spaeter: tools/release.sh auf $BRANCH"
        exit 0
    fi
    integrate "$BRANCH" develop "Merge $BRANCH into develop"
    echo "$BRANCH ist in develop gemergt."
    echo "Lokalen Feature-Zweig loeschen mit: git branch -d $BRANCH"
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
        read -r -p "Release-Art patch/minor/major [minor]: " PART || PART=""
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
    read -r -p "Release-Nachricht (eine Zeile): " MESSAGE || MESSAGE=""
fi
[[ -n "${MESSAGE//[[:space:]]/}" ]] || die "the release message is empty"
SUBJECT="$TOOL $VERSION: $MESSAGE"
echo "Release: $LAST -> $TAG aus $BRANCH - $MESSAGE"

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
if ! $CHECK_CMD; then
    git checkout -- "$INIT" "$CHANGELOG"
    if [[ "$KIND" == develop ]]; then git switch --quiet develop; git branch -D "$RB" >/dev/null; fi
    die "quality gates failed - nothing was released; main and develop are as before"
fi
git commit --quiet -am "$TOOL $VERSION"

# --- release: pull request into main, tag ----------------------------------------
echo
if ! ask "$RB pushen, nach main mergen und $TAG veroeffentlichen?"; then
    echo "Nicht gepusht. Verwerfen mit: $DROP"
    exit 0
fi
integrate "$RB" main "$SUBJECT"
[[ "$(tree main)" == "$(tree "$RB")" ]] \
    || die "main after the merge differs from $RB - check on GitHub before tagging"
git tag -a "$TAG" -m "$SUBJECT"
git push --quiet origin "$TAG"
echo "Release-Check von $TAG ..."
tag_check "$TAG"
echo "Fertig: $TAG ist veroeffentlicht."

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
    echo "develop enthaelt zusaetzlich die noch nicht veroeffentlichten Features."
fi

# --- install ------------------------------------------------------------------------
echo
if ask "$TAG jetzt installieren (./install.sh $TAG)?"; then
    $INSTALL_CMD "$TAG"
    "$HOME/.local/bin/$TOOL" --version \
        || echo "Warnung: $HOME/.local/bin/$TOOL --version schlug fehl" >&2
    if [[ -x tools/after-install.sh ]]; then
        tools/after-install.sh "$TAG" || echo "Warnung: tools/after-install.sh schlug fehl" >&2
    fi
else
    echo "Spaeter installieren mit: ./install.sh $TAG"
fi
