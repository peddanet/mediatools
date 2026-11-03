#!/usr/bin/env bash
# Quality gates for mediatools.
#
# Runs the same checks locally (pre-push hook) and in CI, so a green local
# run predicts a green pipeline. Every gate must pass; the script stops at
# the first failure and returns its exit code.
#
#   1. format   pycodestyle, max line length 100
#   2. lint     pylint incl. Google-style docstring checks, score 10.00/10
#   3. types    mypy (config in pyproject.toml)
#   4. security bandit (config in pyproject.toml)
#   5. tests    pytest with branch coverage, threshold in pyproject.toml
#
# Usage: tools/check.sh [--fast]
#   --fast  skip the test gate (used for quick checks while editing)
#
# Runs as a normal user; needs no root privileges.

set -euo pipefail

cd "$(dirname "$0")/.."
export PYTHONPATH="src:tests${PYTHONPATH:+:$PYTHONPATH}"

FAST=0
[[ "${1:-}" == "--fast" ]] && FAST=1

gate() {
    local name="$1"; shift
    printf '\n=== %-8s %s\n' "$name" "$*"
    "$@"
}

for tool in pycodestyle pylint mypy bandit; do
    command -v "$tool" >/dev/null || {
        echo "missing tool: $tool  ->  pip install -e '.[dev]'" >&2
        exit 2
    }
done

gate format   pycodestyle --max-line-length=100 src tests tools
gate lint     pylint --fail-under=10 src tests tools
gate types    mypy
gate security bandit -q -c pyproject.toml -r src
if (( FAST == 0 )); then
    gate tests python3 -m pytest -q -p no:cacheprovider \
        --cov=mediatools --cov-report=term
fi

printf '\nAll quality gates passed.\n'
