#!/usr/bin/env bash
# install.sh - install a mediatools release for the current user.
#
# The code is always taken from a release tag (vX.Y.Z) on GitHub, never
# from the working tree of a clone, so "mediatools --version" shows exactly
# the installed release. The release goes into a virtual environment of its
# own and the command is linked into BIN_DIR (XDG layout):
#
#   ${MEDIATOOLS_HOME:-~/.local/share/mediatools}/venv   the installed release
#   ${BIN_DIR:-~/.local/bin}/mediatools                    link to venv/bin/mediatools
#
# Usage:  ./install.sh              install or update to the newest release
#         ./install.sh v1.2.0       install a given release (also a downgrade)
#         ./install.sh --list       list the available releases
#
# Moving from the layout before 1.14.0 (~/scripts/mediatools.d/venv and the
# link ~/scripts/mediatools), only for what this script created itself:
#   * the old venv is removed - unless a running process still uses it
#     (e.g. a shrink); then it stays and the next run removes it,
#   * the old link is first pointed to the new release (so it keeps working
#     until ~/.local/bin is in PATH, which Ubuntu adds at the next login);
#     a later run removes it once ~/.local/bin is in PATH and neither the
#     menu entry nor the systemd unit names it any more,
#   * the menu entry (~/.local/share/applications/mediatools.desktop) and
#     the systemd user unit of the snapshot trigger are rewritten to the new
#     link if they named the old one.
#
# Runs as a normal user and writes only below the data folder, BIN_DIR,
# ~/scripts (old layout), the menu entry and ~/.config/systemd/user (unit
# rewrite). Needs
# git, python3 >= 3.12 with the venv module (Debian/Ubuntu package
# python3-venv) and network access to GitHub and PyPI (build backend).
# PIP_INSTALL_CMD replaces the pip step (tests): called with VENV and TAG.
set -euo pipefail

REPO_URL="${MEDIATOOLS_REPO:-https://github.com/peddanet/mediatools.git}"
xdg_data="${XDG_DATA_HOME:-}"
case "$xdg_data" in /*) ;; *) xdg_data="$HOME/.local/share" ;; esac
DATA_DIR="${MEDIATOOLS_HOME:-$xdg_data/mediatools}"
VENV="$DATA_DIR/venv"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
LINK="$BIN_DIR/mediatools"
OLD_DIR="$HOME/scripts/mediatools.d"
OLD_VENV="$OLD_DIR/venv"
OLD_LINK="$HOME/scripts/mediatools"
UNIT="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/mediatools-snapshot.service"
DESKTOP="$xdg_data/applications/mediatools.desktop"

die() { echo "install.sh: $*" >&2; exit 1; }

# release tags vX.Y.Z of the repository, oldest first (version order)
releases() {
  git ls-remote --tags --refs "$REPO_URL" 'v*' \
    | sed 's#.*refs/tags/##' \
    | { grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' || true; } \
    | sort -V
}

# true if a running process was started from below $1 (pgrep: procps)
in_use() {
  pgrep -f -- "$(readlink -f "$1")/" >/dev/null
}

# true if the directory $1 is in PATH
in_path() { case ":$PATH:" in *":$1:"*) return 0 ;; *) return 1 ;; esac; }

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

python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))' \
  || die "Python >= 3.12 required"

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
if [ -n "${PIP_INSTALL_CMD:-}" ]; then
  $PIP_INSTALL_CMD "$VENV" "$TAG"
else
  "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check \
    --force-reinstall --no-deps "mediatools @ git+$REPO_URL@$TAG"
fi

installed="$("$VENV/bin/mediatools" --version)"
[ "$installed" = "mediatools ${TAG#v}" ] \
  || die "tag $TAG installed, but the program reports \"$installed\""

ln -sfn "$VENV/bin/mediatools" "$LINK"
echo "$installed  ->  $LINK"

# -- leave the layout before 1.14.0 (only what this script created) ------------
if [ -z "${MEDIATOOLS_HOME:-}" ] && [ "$OLD_VENV" != "$VENV" ]; then
  if [ -L "$OLD_LINK" ]; then
    target="$(readlink "$OLD_LINK")"
    if [ "$target" = "$OLD_VENV/bin/mediatools" ]; then
      ln -sfn "$VENV/bin/mediatools" "$OLD_LINK"
      echo "old link now points to the new release: $OLD_LINK"
    elif [ "$target" = "$VENV/bin/mediatools" ] && in_path "$BIN_DIR" \
        && ! grep -qsF "$OLD_LINK" "$UNIT" "$DESKTOP"; then
      rm -f -- "$OLD_LINK"
      echo "removed the old link (the command is $LINK now): $OLD_LINK"
    fi
  fi
  if [ -f "$OLD_VENV/pyvenv.cfg" ] && [ -e "$OLD_VENV/bin/mediatools" ]; then
    if in_use "$OLD_VENV"; then
      echo "NOTE: the old venv is still used by a running process: $OLD_VENV"
      echo "      it stays; stop that process (e.g. 'mediatools stop') and run ./install.sh again"
    else
      rm -rf -- "$OLD_VENV"
      echo "removed the old venv: $OLD_VENV"
      rmdir -- "$OLD_DIR" 2>/dev/null && echo "removed the empty folder: $OLD_DIR"
    fi
  fi
  if [ -f "$DESKTOP" ] && grep -qF "Exec=\"$OLD_LINK\" " "$DESKTOP"; then
    sed -i "s#^Exec=\"$OLD_LINK\" #Exec=\"$LINK\" #" "$DESKTOP"
    echo "menu entry now starts $LINK: $DESKTOP"
  fi
  if [ -f "$UNIT" ] && grep -qF "ExecStart=$OLD_LINK " "$UNIT"; then
    sed -i "s#^ExecStart=$OLD_LINK #ExecStart=$LINK #" "$UNIT"
    echo "snapshot trigger now starts $LINK: $UNIT"
    if command -v systemctl >/dev/null && systemctl --user daemon-reload 2>/dev/null; then
      echo "systemd user units reloaded"
    else
      echo "NOTE: reload the user units once: systemctl --user daemon-reload"
    fi
  fi
fi

resolved="$(command -v mediatools || true)"
if ! in_path "$BIN_DIR"; then
  echo "NOTE: $BIN_DIR is not in PATH yet - Ubuntu adds it at the next login"
  echo "      (if it existed then); until then use $LINK"
elif [ -n "$resolved" ] && [ "$resolved" != "$LINK" ]; then
  echo "NOTE: in this shell 'mediatools' resolves to $resolved"
  echo "      (e.g. an active development venv); the release is $LINK"
fi
for tool in ffmpeg ffprobe rsync rsnapshot gio vlc; do
  command -v "$tool" >/dev/null 2>&1 || echo "  optional program missing: $tool (see README)"
done
