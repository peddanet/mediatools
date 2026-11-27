#!/usr/bin/env bash
# review.sh - show two git refs side by side in Beyond Compare (four-eyes review)
#
# Writes the complete content of both refs as real files into two new
# folders below a new temporary directory (mktemp -d) and opens them in
# Beyond Compare's folder compare. Unlike "git difftool --dir-diff" there
# are no symbolic links into the working tree and no anonymous left/right
# folders: each folder name and each path field of Beyond Compare names
# the ref, the short commit hash and the commit subject.
#
# Usage:  tools/review.sh develop HEAD          left = old, right = new
#         tools/review.sh main feature/v9
#
# Exit code 0: Beyond Compare was started, or both refs have the same tree
# (then nothing is opened); 1: Beyond Compare is missing or reported an
# error (return code 100 or higher); 2: usage error or unknown ref.
# BCOMPARE replaces the program (the tests set it).
#
# Runs as a normal user: it reads the repository and writes only into the
# new temporary directory. That directory stays for the review because
# bcompare may return before its window is closed; /tmp is emptied at
# the next start of the computer.
set -euo pipefail
if [[ ! -f "$(dirname "${BASH_SOURCE[0]}")/output.sh" ]]; then
    echo "review.sh: tools/output.sh is missing - copy tools/output.sh of the dev-conventions bundle" >&2
    exit 2
fi
# shellcheck source=tools/output.sh disable=SC1091  # path chosen at run time
source "$(dirname "${BASH_SOURCE[0]}")/output.sh"

if (( $# != 2 )); then
    echo "usage: tools/review.sh LEFT-REF RIGHT-REF   (left = old, right = new)" >&2
    exit 2
fi
BCOMPARE="${BCOMPARE:-bcompare}"
cd "$(git rev-parse --show-toplevel)"

left="$(git rev-parse --verify --quiet "$1^{commit}")" \
    || { echo "unknown ref: $1" >&2; exit 2; }
right="$(git rev-parse --verify --quiet "$2^{commit}")" \
    || { echo "unknown ref: $2" >&2; exit 2; }
command -v "$BCOMPARE" >/dev/null \
    || { echo "review.sh: '$BCOMPARE' not found - Beyond Compare is not installed under this name" >&2; exit 1; }

# describe REF COMMIT - "<ref> @ <short hash>: <subject>"
describe() {
    printf '%s @ %s: %s' "$1" "$(git rev-parse --short "$2")" "$(git log -1 --format=%s "$2")"
}

# put_tree COMMIT DIR - write all files of COMMIT as real files into DIR;
# a temporary index keeps the index and the working tree untouched
put_tree() {
    local index="$out/.index"
    mkdir -p "$2"
    GIT_INDEX_FILE="$index" git read-tree "$1"
    GIT_INDEX_FILE="$index" git checkout-index --all --prefix="$2/"
    rm -f "$index"
}

left_title="left: $(describe "$1" "$left")"
right_title="right: $(describe "$2" "$right")"
title "Review (left = old, right = new)"
info "$left_title"
info "$right_title"

if [[ "$(git rev-parse "$left^{tree}")" == "$(git rev-parse "$right^{tree}")" ]]; then
    pass "No difference: both have the same tree" "Beyond Compare not opened"
    exit 0
fi
git --no-pager diff --stat "$left" "$right" | while IFS= read -r line; do detail "$line"; done

out="$(mktemp -d "${TMPDIR:-/tmp}/review.XXXXXX")"
left_dir="$out/left_${1//\//-}@$(git rev-parse --short "$left")"
right_dir="$out/right_${2//\//-}@$(git rev-parse --short "$right")"
put_tree "$left" "$left_dir"
put_tree "$right" "$right_dir"

stage "Opening Beyond Compare" "folder compare, real copies, no links"
info "left:  $left_dir"
info "right: $right_dir"
code=0
"$BCOMPARE" "$left_dir" "$right_dir" "-lefttitle=$left_title" "-righttitle=$right_title" \
    || code=$?
# return codes below 100 describe the comparison (same, different), not an error
if (( code >= 100 )); then
    printf '%s✘ review.sh: Beyond Compare ended with error code %s%s\n' "$R$B" "$code" "$X" >&2
    exit 1
fi
note "The compared folders stay until the next reboot: $out"
