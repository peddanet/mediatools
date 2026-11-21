#!/usr/bin/env bash
# after-install.sh - called by tools/release.sh after ./install.sh vX.Y.Z.
#
# Asks a shrink running in the background to switch to the installed
# release after its current file (prints "nothing to reload" if none runs)
# and shows which release runs. Runs as a normal user.
set -uo pipefail
program="$HOME/.local/bin/mediatools"
"$program" reload || true
"$program" status || true
