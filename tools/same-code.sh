#!/usr/bin/env bash
# same-code.sh - do two or more git refs contain bit-identical code?
#
# Compares the tree hashes of the given branches, tags or commits. A tree
# hash is git's fingerprint over all file names and contents; equal tree
# hashes mean identical code, even if the commit hashes differ (merge
# commits have their own hashes).
#
# Usage:  tools/same-code.sh main develop            -> JA / NEIN
#         tools/same-code.sh main develop v1.1.1
#
# Exit code 0: all identical (JA), 1: different (NEIN), 2: usage error.
# Runs as a normal user and only reads the repository.
set -euo pipefail

if (( $# < 2 )); then
    echo "usage: tools/same-code.sh REF REF [REF ...]" >&2
    exit 2
fi

first=""
same=1
for ref in "$@"; do
    tree="$(git rev-parse --verify --quiet "$ref^{tree}")" \
        || { echo "unknown ref: $ref" >&2; exit 2; }
    printf '%-24s %s\n' "$ref" "$tree"
    if [[ -z "$first" ]]; then
        first="$tree"
    elif [[ "$tree" != "$first" ]]; then
        same=0
    fi
done

if (( same )); then
    echo "Bitgleich: JA"
    exit 0
fi
echo "Bitgleich: NEIN - Unterschiede zu $1:"
for ref in "${@:2}"; do
    git diff --stat "$1" "$ref" | while IFS= read -r line; do
        printf '  [%s] %s\n' "$ref" "$line"
    done
done
exit 1
