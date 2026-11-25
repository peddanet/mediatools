#!/usr/bin/env bash
# apply-release.sh - "mediatools update": install a newer release, then apply
# and release delivered patch archives (dev-conventions handover, chapter 12).
#
# Step 1 (everyone): compare the installed release with the newest tag
#   vX.Y.Z of the repository (git ls-remote, no clone needed). If it is
#   newer, install it - with ./install.sh of the clone if there is one, else
#   with pip into the venv of the running program - and ask a running shrink
#   to switch to it after its current file (mediatools reload).
# Step 2 (developer, needs the clone): patch archives in the download folder,
#   "mediatools-feature-<name>.tar.gz" or "mediatools-hotfix-<name>.tar.gz"
#   (browser copies "... (1).tar.gz" or "...1.tar.gz" are recognised by the
#   branch in their patch.info: the newest copy is used; a file with
#   the same content as a newer one or as an archive already applied is
#   never offered, whatever its name). Each holds
#   the patches and patch.info (branch, base, base_tree or blob lines, tree,
#   release = patch|minor|major|none, message). Per archive one question;
#   then: guard (base branch equals origin and has the expected tree or
#   blobs) -> git switch -c <branch> <base> -> git am -> guard (result tree
#   or blobs) -> tools/release.sh <part> -m <message> --yes (release=none:
#   --merge-only --yes), which pushes, opens the pull requests, waits for
#   the checks, merges, tags, merges back, installs and reloads. Archives
#   with release=none are applied one after the other; the run ends after
#   the first archive that releases (the next one is built on its result).
#   Used archives move to the releases folder. Removed at the end, and shown
#   as such: "released" - old versioned archives
#   "mediatools-hotfix-X.Y.Z-patch.tar.gz" of versions already released;
#   "old copy" - older copies of the same archive name or the same content;
#   "applied" - same content as an archive in the releases folder.
#
# Usage (normal user; git and gh act on your clone and with your GitHub
# login, nothing here needs root). The script ships inside the package:
#     mediatools update              # step 1, then ask per patch archive
#     mediatools update --list       # only show
#     mediatools update -y           # no questions
#   options: --downloads DIR (default ~/Downloads),
#            --repo DIR (default: the clone around this file, else
#            ~/Projects/mediatools)
# Environment (set by "mediatools update"): MEDIATOOLS_VENV (venv of the
# running program), MEDIATOOLS_VERSION (its version); MEDIATOOLS_REPO
# replaces the repository URL (tests).
#
# The script runs from a temporary copy, because the branch switches below
# and the installation rewrite this file while bash reads it. Every step is
# logged; the first failing command stops the script and names the step. On
# failure nothing in the download folder is removed, so the same command can
# be repeated.
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
CLONE=0
[ -d "$REPO/.git" ] && [ -f "$REPO/src/mediatools/__init__.py" ] && CLONE=1
# folders as in mediatools.core.datadir.DataDir: one folder from
# $MEDIATOOLS_HOME, the old folder while it still holds data, else XDG
LEGACY="$HOME/scripts/mediatools.d"
xdg_dir() {                                # $1 = value of the variable, $2 = default
  case "$1" in /*) echo "$1/mediatools" ;; *) echo "$2/mediatools" ;; esac
}
if [ -n "${MEDIATOOLS_HOME:-}" ]; then
  STATE_DIR="$MEDIATOOLS_HOME"; DATA_DIR="$MEDIATOOLS_HOME"
elif [ ! -e "$LEGACY/WHERE-IS-THE-DATA.txt" ] \
     && { [ -e "$LEGACY/logs" ] || [ -e "$LEGACY/state" ] || [ -e "$LEGACY/mediatools.ini" ]; }; then
  STATE_DIR="$LEGACY"; DATA_DIR="$LEGACY"
else
  STATE_DIR=$(xdg_dir "${XDG_STATE_HOME:-}" "$HOME/.local/state")
  DATA_DIR=$(xdg_dir "${XDG_DATA_HOME:-}" "$HOME/.local/share")
fi
ARCHIVE="$DATA_DIR/releases"
LOG_DIR="$STATE_DIR/logs"
LOG=""
NAME_RE='^mediatools-(feature|hotfix)-([A-Za-z0-9][A-Za-z0-9._-]*)( \([0-9]+\))?\.tar\.gz$'
OLD_RE='^mediatools-hotfix-([0-9]+\.[0-9]+\.[0-9]+)-patch( \([0-9]+\))?\.tar\.gz$'
STEP="start"

step() { STEP="$1"; echo; echo "=== $STEP"; }
# true if version $1 is lower than version $2
version_lt() { [ "$1" != "$2" ] && [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -1)" = "$1" ]; }
# value of key $2 in the patch.info file $1
info() { sed -n "s/^$2=//p" "$1" | head -n 1; }

# the installed release: the link of install.sh, the old link, else the caller
installed_version() {
  local program
  for program in "$HOME/.local/bin/mediatools" "$HOME/scripts/mediatools"; do
    if [ -x "$program" ]; then
      "$program" --version 2>/dev/null | awk '{print $2}' && return 0
    fi
  done
  echo "${MEDIATOOLS_VERSION:-0}"
}

# the repository URL: MEDIATOOLS_REPO, origin of the clone, or GitHub
repo_url() {
  if [ -n "${MEDIATOOLS_REPO:-}" ]; then echo "$MEDIATOOLS_REPO"; return; fi
  if [ "$CLONE" -eq 1 ]; then git -C "$REPO" remote get-url origin && return; fi
  echo "https://github.com/peddanet/mediatools.git"
}

# newest release tag vX.Y.Z of the repository (empty if none or unreachable)
newest_release() {
  git ls-remote --tags --refs "$1" 'v*' 2>/dev/null | sed 's#.*refs/tags/##' \
    | { grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' || true; } | sort -V | tail -n 1
}

# every archive in the download folder, newest first, one line
# "status<TAB>kind<TAB>name<TAB>path"; status: "pending" for the newest copy
# of each archive name, "old copy" for an older copy of the same name or a
# file with the same content as a newer one, "applied" for the same content
# as an archive in the releases folder, "released" for a versioned archive
# of a version already released ($1 = installed version)
classify() {
  local released="$1" applied="" seen_sums="" seen_names="" path base sum key branch
  if [ -d "$ARCHIVE" ]; then
    applied="$(find "$ARCHIVE" -maxdepth 1 -type f -name '*.tar.gz' -print0 \
               | xargs -0 -r sha256sum | cut -c 1-64 | tr '\n' ' ')"
  fi
  while IFS=$'\t' read -r _mtime path; do
    base="$(basename "$path")"
    if [[ "$base" =~ $OLD_RE ]]; then
      version_lt "$released" "${BASH_REMATCH[1]}" || printf 'released\t-\t-\t%s\n' "$path"
      continue
    fi
    [[ "$base" =~ $NAME_RE ]] || continue
    key="${BASH_REMATCH[1]}"$'\t'"${BASH_REMATCH[2]}"
    # the branch in patch.info names the archive, not the file name the
    # browser gave it ("... (1).tar.gz", "...1.tar.gz")
    branch="$(tar -xzOf "$path" --wildcards '*/patch.info' 2>/dev/null \
              | sed -n 's/^branch=//p' | head -n 1 || true)"
    if [[ "$branch" =~ ^(feature|hotfix)/([A-Za-z0-9][A-Za-z0-9._-]*)$ ]]; then
      key="${BASH_REMATCH[1]}"$'\t'"${BASH_REMATCH[2]}"
    fi
    sum="$(sha256sum -- "$path" | cut -c 1-64)"
    if [[ " $applied " == *" $sum "* ]]; then
      printf 'applied\t%s\t%s\n' "$key" "$path"
    elif [[ " $seen_sums " == *" $sum "* || "$seen_names" == *$'\n'"$key"$'\n'* ]]; then
      printf 'old copy\t%s\t%s\n' "$key" "$path"
    else
      printf 'pending\t%s\t%s\n' "$key" "$path"
      seen_names="${seen_names:-$'\n'}$key"$'\n'
    fi
    seen_sums="$seen_sums $sum"
  done < <(find "$DL" -maxdepth 1 -type f -name 'mediatools-*.tar.gz' -printf '%T@\t%p\n' \
           | sort -t$'\t' -k1,1nr)
}

# -- start ---------------------------------------------------------------------
[ "$(id -u)" -ne 0 ] || [ -n "${APPLY_RELEASE_ALLOW_ROOT:-}" ] \
  || { echo "do not run as root"; exit 1; }
if [ "$LIST" -eq 0 ]; then
  mkdir -p "$LOG_DIR"
  LOG="$LOG_DIR/apply-release_$(date +%Y-%m-%d_%H-%M-%S).log"
  exec > >(tee -a "$LOG") 2>&1
  # shellcheck disable=SC2154  # rc is assigned inside the trap string
  trap 'rc=$?; echo; echo "FAILED in step: $STEP (line $LINENO, exit code $rc)"; echo "log: $LOG"' ERR
fi

# -- step 1: newer release ------------------------------------------------------
URL="$(repo_url)"
INSTALLED="$(installed_version)"
NEWEST="$(newest_release "$URL")"
echo "installed release: $INSTALLED   (repository: $URL)"
if [ -z "$NEWEST" ]; then
  echo "newest release:    unknown (repository not reachable) - step 1 skipped"
elif version_lt "$INSTALLED" "${NEWEST#v}"; then
  echo "newest release:    $NEWEST - newer"
  if [ "$LIST" -eq 0 ]; then
    step "1 install $NEWEST"
    if [ "$CLONE" -eq 1 ] && [ -x "$REPO/install.sh" ]; then
      "$REPO/install.sh" "$NEWEST"
      program="$HOME/.local/bin/mediatools"
    else
      [ -n "${MEDIATOOLS_VENV:-}" ] || { echo "no venv of the running program known"; exit 1; }
      "$MEDIATOOLS_VENV/bin/python" -m pip install --quiet --disable-pip-version-check \
        --force-reinstall --no-deps "mediatools @ git+$URL@$NEWEST"
      program="$MEDIATOOLS_VENV/bin/mediatools"
    fi
    [ "$("$program" --version)" = "mediatools ${NEWEST#v}" ]
    INSTALLED="${NEWEST#v}"
    "$program" reload || true                # a running shrink switches after its file
  fi
else
  echo "newest release:    $NEWEST - installed"
fi

# -- step 2: patch archives -----------------------------------------------------
mapfile -t CLASSES < <(classify "$INSTALLED")
FOUND=(); LEFTOVER=()
for (( i=${#CLASSES[@]}-1; i>=0; i-- )); do      # pending archives oldest first
  IFS=$'\t' read -r status kind name path <<< "${CLASSES[i]}"
  [ "$status" = pending ] && FOUND+=("$kind"$'\t'"$name"$'\t'"$path")
done
for line in "${CLASSES[@]}"; do
  IFS=$'\t' read -r status _kind _name path <<< "$line"
  [ "$status" = pending ] || LEFTOVER+=("$status"$'\t'"$path")
done
echo "download folder:   $DL"
[ ${#FOUND[@]} -gt 0 ] || echo "patch archives:    none"
for line in "${FOUND[@]}"; do
  IFS=$'\t' read -r kind name path <<< "$line"
  printf '  pending  %-8s %-28s %s\n' "$kind" "$name" "$(basename "$path")"
done
for line in "${LEFTOVER[@]}"; do
  IFS=$'\t' read -r reason path <<< "$line"
  printf '  %-8s %s (removed at the end)\n' "$reason" "$(basename "$path")"
done
for path in "$DL"/mediatools-hotfix-*-patch*.tar.gz; do
  if [ -f "$path" ] && [[ "$(basename "$path")" =~ $OLD_RE ]] \
     && version_lt "$INSTALLED" "${BASH_REMATCH[1]}"; then
    printf '  refused  %s (old format of releases before 1.14.0 - apply it with that release)\n' \
           "$(basename "$path")"
  fi
done
[ "$LIST" -eq 1 ] && exit 0

APPLIED=()
if [ ${#FOUND[@]} -gt 0 ]; then
  [ "$CLONE" -eq 1 ] || { echo "patch archives need the clone ($REPO, or --repo DIR)"; exit 1; }
  cd "$REPO"
  git am --abort 2>/dev/null || true         # leftover of an interrupted earlier run
  [ -z "$(git status --porcelain)" ] || { echo "uncommitted changes in $REPO"; exit 1; }
fi
for line in "${FOUND[@]}"; do
  IFS=$'\t' read -r kind name path <<< "$line"
  dir=$(mktemp -d /tmp/mediatools-patch.XXXXXX)
  step "2 $(basename "$path")"
  tar -xzf "$path" -C "$dir"
  infofile="$(find "$dir" -name patch.info | head -n 1)"
  [ -n "$infofile" ] || { echo "no patch.info in $path"; exit 1; }
  branch="$(info "$infofile" branch)"; base="$(info "$infofile" base)"
  release="$(info "$infofile" release)"; message="$(info "$infofile" message)"
  [ "$branch" = "$kind/$name" ] || { echo "patch.info names $branch, the archive $kind/$name"; exit 1; }
  case "$release" in patch|minor|major|none) ;; *) echo "release=$release?"; exit 1 ;; esac
  echo "branch $branch from $base, release: $release - $message"
  if [ "$YES" -eq 0 ]; then
    if [ "$release" = none ]; then question="in $base mergen (ohne Release)"; else
      question="einspielen und als $release veroeffentlichen"; fi
    read -r -p "$branch $question? [j/N] " answer || answer=""
    [[ "$answer" =~ ^[jJyY]$ ]] || { echo "skipped: $branch"; rm -rf "$dir"; continue; }
  fi

  git fetch --quiet --tags origin
  git switch --quiet "$base"
  git merge --quiet --ff-only "origin/$base"
  want="$(info "$infofile" base_tree)"
  if [ -n "$want" ]; then
    [ "$(git rev-parse "$base^{tree}")" = "$want" ] \
      || { echo "STOPP: $base differs from the base of the patch (tree $want)"; exit 1; }
  fi
  while read -r bpath before _after; do
    [ "$(git rev-parse "$base:$bpath")" = "$before" ] \
      || { echo "STOPP: $bpath on $base differs from the base of the patch"; exit 1; }
  done < <(sed -n 's/^blob=//p' "$infofile")
  if git rev-parse --quiet --verify "refs/heads/$branch" >/dev/null; then
    echo "STOPP: branch $branch exists already - delete it: git branch -D $branch"; exit 1
  fi
  git switch --quiet -c "$branch" "$base"
  if ! git am --quiet "$(dirname "$infofile")"/*.patch; then
    git am --abort 2>/dev/null || true
    git switch --quiet "$base"
    git branch --quiet -D "$branch"
    echo "STOPP: the patches do not apply onto $base; nothing changed"; exit 1
  fi
  want="$(info "$infofile" tree)"
  if [ -n "$want" ]; then
    [ "$(git rev-parse "HEAD^{tree}")" = "$want" ] \
      || { echo "STOPP: result differs from the patch (tree $want) - check $branch"; exit 1; }
  fi
  while read -r bpath _before after; do
    [ "$(git rev-parse "HEAD:$bpath")" = "$after" ] \
      || { echo "STOPP: $bpath after the patch differs - check $branch"; exit 1; }
  done < <(sed -n 's/^blob=//p' "$infofile")
  git log --oneline "$base..HEAD"

  if [ "$release" = none ]; then
    tools/release.sh --merge-only --yes
  else
    tools/release.sh "$release" -m "$message" --yes
  fi
  mkdir -p "$ARCHIVE"
  mv -v -- "$path" "$ARCHIVE/mediatools-$kind-$name.tar.gz"
  APPLIED+=("$branch")
  rm -rf "$dir"
  if [ "$release" != none ]; then
    [ "${line}" = "${FOUND[-1]}" ] || echo "further archives: run 'mediatools update' again"
    break
  fi
done

# -- tidy the download folder --------------------------------------------------------
if [ ${#LEFTOVER[@]} -gt 0 ]; then
  step "remove downloads no longer needed (released, old copies)"
  for line in "${LEFTOVER[@]}"; do rm -v -- "${line#*$'\t'}"; done
fi
# per-version apply scripts of earlier releases and downloaded copies of this one
find "$DL" -maxdepth 1 -type f \( -name 'apply-mediatools-*.sh' -o -name 'apply-release*.sh' \) \
  ! -samefile "$APPLY_RELEASE_ORIGIN" -print0 | xargs -0 -r rm -v --

step "done"
if [ ${#APPLIED[@]} -gt 0 ]; then echo "applied: ${APPLIED[*]}"; else echo "no patch applied"; fi
echo "log: $LOG"
