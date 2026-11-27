#!/usr/bin/env bash
# same-code.sh - do two or more git refs contain bit-identical code?
#
# Compares the tree hashes of the given branches, tags or commits. A tree
# hash is git's fingerprint over all file names and contents; equal tree
# hashes mean identical code, even if the commit hashes differ (merge
# commits have their own hashes).
#
# Usage:  tools/same-code.sh main develop            -> YES / NO
#         tools/same-code.sh main develop v1.1.1
#
# Exit code 0: all identical (YES), 1: different (NO), 2: usage error.
# Runs as a normal user and only reads the repository.
set -euo pipefail
if [[ ! -f "$(dirname "${BASH_SOURCE[0]}")/output.sh" ]]; then
    echo "same-code.sh: tools/output.sh is missing - copy tools/output.sh of the dev-conventions bundle" >&2
    exit 2
fi
# shellcheck source=tools/output.sh disable=SC1091  # path chosen at run time
source "$(dirname "${BASH_SOURCE[0]}")/output.sh"

if (( $# < 2 )); then
    echo "usage: tools/same-code.sh REF REF [REF ...]" >&2
    exit 2
fi

first=""
same=1
for ref in "$@"; do
    tree="$(git rev-parse --verify --quiet "$ref^{tree}")" \
        || { echo "unknown ref: $ref" >&2; exit 2; }
    detail "$(pad "$ref" 24) $tree"
    if [[ -z "$first" ]]; then
        first="$tree"
    elif [[ "$tree" != "$first" ]]; then
        same=0
    fi
done

if (( same )); then
    pass "Bit-identical: YES" "$# refs"
    exit 0
fi
fail "Bit-identical: NO - differences to $1:" "$# refs"
for ref in "${@:2}"; do
    git diff --stat "$1" "$ref" | while IFS= read -r line; do
        detail "[$ref] $line"
    done
done
exit 1
