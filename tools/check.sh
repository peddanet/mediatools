#!/usr/bin/env bash
# Quality gates for mediatools.
#
# Runs the same checks locally (pre-push hook) and in CI, so a green local
# run predicts a green pipeline. Every gate must pass; the script stops at
# the first failure and returns its exit code.
#
#   0. privacy  no locally listed term in files, paths or commit messages
#   1. format   pycodestyle, max line length 100
#   2. lint     pylint incl. Google-style docstring checks, score 10.00/10
#   3. types    mypy (config in pyproject.toml)
#   4. security bandit (config in pyproject.toml)
#   5. tests    pytest with branch coverage, threshold in pyproject.toml
#
# The tests of the GUI need Tk for Python (package python3-tk) and a
# display; without DISPLAY the script restarts itself under xvfb-run
# (package xvfb), a virtual display without a window.
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

if (( FAST == 0 )); then
    # integration tests are skipped when these programs are missing, which
    # would lower the coverage; demand a complete test environment instead
    missing=()
    for tool in ffmpeg ffprobe rsync rsnapshot; do
        command -v "$tool" >/dev/null || missing+=("$tool")
    done
    python3 -c 'import tkinter' 2>/dev/null || missing+=("python3-tk")
    if [[ -z "${DISPLAY:-}" ]]; then
        if command -v xvfb-run >/dev/null && [[ -z "${MEDIATOOLS_XVFB:-}" ]]; then
            exec env MEDIATOOLS_XVFB=1 xvfb-run -a "$0" "$@"
        fi
        missing+=("xvfb")
    fi
    if (( ${#missing[@]} )); then
        echo "missing programs for the integration tests: ${missing[*]}" >&2
        echo "install with: sudo apt install ffmpeg rsync rsnapshot python3-tk xvfb" >&2
        exit 2
    fi
fi

gate privacy  python3 tools/privacy_scan.py
gate format   pycodestyle --max-line-length=100 src tests tools
gate lint     pylint --fail-under=10 src tests tools
gate types    mypy
gate security bandit -q -c pyproject.toml -r src
if (( FAST == 0 )); then
    gate tests python3 -m pytest -q -p no:cacheprovider \
        --cov=mediatools --cov-report=term
fi

printf '\nAll quality gates passed.\n'
