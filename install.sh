#!/usr/bin/env bash
# install.sh - install a mediatools release for the current user.
#
# The code is always taken from a release tag (vX.Y.Z) on GitHub, never
# from the working tree of a clone, so "mediatools --version" shows exactly
# the installed release. The release goes into a virtual environment of its
# own and the command is linked into BIN_DIR:
#
#   ${MEDIATOOLS_HOME:-~/scripts/mediatools.d}/venv     the installed release
#   ${BIN_DIR:-~/scripts}/mediatools                     link to venv/bin/mediatools
#
# Usage:  ./install.sh              install or update to the newest release
#         ./install.sh v1.2.0       install a given release (also a downgrade)
#         ./install.sh --list       list the available releases
#         BIN_DIR=~/.local/bin ./install.sh
#
# Runs as a normal user and writes only below MEDIATOOLS_HOME and BIN_DIR.
# Needs git, python3 >= 3.10 with the venv module (Debian/Ubuntu package
# python3-venv) and network access to GitHub and PyPI (build backend).
set -euo pipefail

REPO_URL="${MEDIATOOLS_REPO:-https://github.com/peddanet/mediatools.git}"
DATA_DIR="${MEDIATOOLS_HOME:-$HOME/scripts/mediatools.d}"
VENV="$DATA_DIR/venv"
BIN_DIR="${BIN_DIR:-$HOME/scripts}"
LINK="$BIN_DIR/mediatools"

die() { echo "install.sh: $*" >&2; exit 1; }

# release tags vX.Y.Z of the repository, oldest first (version order)
releases() {
  git ls-remote --tags --refs "$REPO_URL" 'v*' \
    | sed 's#.*refs/tags/##' \
    | { grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' || true; } \
    | sort -V
}

case "${1:-}" in
  -h|--help) awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"
             exit 0 ;;
  --list)    releases || die "cannot read the release tags of $REPO_URL"; exit 0 ;;
  -*)        die "unknown option: $1 (see --help)" ;;
esac

command -v git >/dev/null || die "git is required (sudo apt install git)"
available="$(releases)" || die "cannot read the release tags of $REPO_URL"
[ -n "$available" ] || die "no release tag vX.Y.Z found at $REPO_URL"
TAG="${1:-$(tail -n 1 <<<"$available")}"
grep -qxF "$TAG" <<<"$available" || die "unknown release: $TAG (see ./install.sh --list)"

# checked before anything is installed: replace only what this script
# (or the former clone launcher) created
mkdir -p "$BIN_DIR"
if [ -e "$LINK" ] || [ -L "$LINK" ]; then
  if [ -L "$LINK" ] && [ "$(readlink "$LINK")" = "$VENV/bin/mediatools" ]; then
    :
  elif [ -f "$LINK" ] && ! [ -L "$LINK" ] && grep -q "mediatools launcher" "$LINK"; then
    echo "replacing the former launcher that ran a clone: $LINK"
  else
    die "refusing to overwrite foreign file: $LINK"
  fi
fi

python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' \
  || die "Python >= 3.10 required"

# a venv whose interpreter vanished (system Python upgraded) is rebuilt
if [ -d "$VENV" ] && ! "$VENV/bin/python" -c '' 2>/dev/null; then
  echo "rebuilding the virtual environment (its Python is gone): $VENV"
  rm -rf "$VENV"
fi
if [ ! -x "$VENV/bin/python" ]; then
  mkdir -p "$DATA_DIR"
  python3 -m venv "$VENV" \
    || die "cannot create $VENV - install the venv module: sudo apt install python3-venv"
fi

echo "installing mediatools $TAG from $REPO_URL"
"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check \
  --force-reinstall --no-deps "mediatools @ git+$REPO_URL@$TAG"

installed="$("$VENV/bin/mediatools" --version)"
[ "$installed" = "mediatools ${TAG#v}" ] \
  || die "tag $TAG installed, but the program reports \"$installed\""

ln -sfn "$VENV/bin/mediatools" "$LINK"
echo "$installed  ->  $LINK"

resolved="$(command -v mediatools || true)"
if [ -z "$resolved" ]; then
  echo "NOTE: $BIN_DIR is not in PATH - add it in ~/.profile and log in again"
elif [ "$resolved" != "$LINK" ]; then
  echo "NOTE: in this shell 'mediatools' resolves to $resolved"
  echo "      (e.g. an active development venv); the release is $LINK"
fi
for tool in ffmpeg ffprobe rsync rsnapshot gio vlc; do
  command -v "$tool" >/dev/null 2>&1 || echo "  optional program missing: $tool (see README)"
done
