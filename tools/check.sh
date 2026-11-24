#!/usr/bin/env bash
# Quality gates for mediatools.
#
# Runs the same checks locally (pre-push hook) and in CI, so a green local
# run predicts a green pipeline. Every gate must pass; the script stops at
# the first failure and returns its exit code.
#
#   0. privacy  no locally listed term in files, paths or commit messages
#   0. shell    shellcheck for every shell script
#   1. format   pycodestyle, max line length 100
#   2. lint     pylint incl. Google-style docstring checks, score 10.00/10
#   3. types    mypy (config in pyproject.toml)
#   4. security bandit (config in pyproject.toml)
#   5. tests    pytest with branch coverage, threshold in pyproject.toml
#
# The tests of the GUI need Tk for Python (package python3-tk) and a
# display; the script always restarts itself under xvfb-run (package
# xvfb), a virtual display without a window, so no test touches the real
# desktop - also when one is running.
#
# Usage: tools/check.sh [--fast]
#   --fast  skip the test gate (used for quick checks while editing)
#
# Each tree is checked in full once: after a full green run the tree of the
# working copy, the Python version and a hash of the installed packages are
# stored in .git/checked-trees; for a known key only privacy and shell run
# again (the privacy scan also reads commit messages and tags). CHECK_FULL=1
# forces a full run; CI (CI=true) always runs in full.
#
# Runs as a normal user; needs no root privileges.

set -euo pipefail

cd "$(dirname "$0")/.."
export PYTHONPATH="src:tests${PYTHONPATH:+:$PYTHONPATH}"

# Development venv: an active one is used as it is; otherwise the first of
# .venv in the project and ~/.venvs/<tool> (older layout, transition) is
# activated for this script only - the activation ends with the script, the
# calling shell stays as it was. CI has neither and installs the tools
# directly. Nothing is created or installed here.
if [[ -z "${VIRTUAL_ENV:-}" ]]; then
    for venv in .venv "$HOME/.venvs/mediatools"; do
        if [[ -f "$venv/bin/activate" ]]; then
            # shellcheck disable=SC1091  # path chosen at run time
            source "$venv/bin/activate"
            echo "venv: $venv (activated for this check)"
            [[ "$venv" == .venv ]] || echo "note: $venv lies at the first level of the home" \
                "directory; move it to .venv in the project (see CONTRIBUTING.md)"
            break
        fi
    done
fi

FAST=0
[[ "${1:-}" == "--fast" ]] && FAST=1

gate() {
    local name="$1"; shift
    printf '\n=== %-8s %s\n' "$name" "$*"
    "$@"
}

for tool in pycodestyle pylint mypy bandit; do
    command -v "$tool" >/dev/null || {
        echo "missing tool: $tool - create the development venv once (normal user):" >&2
        echo "  python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'" >&2
        exit 2
    }
done
command -v shellcheck >/dev/null || {
    echo "missing tool: shellcheck  ->  sudo apt install shellcheck" >&2
    exit 2
}

if (( FAST == 0 )); then
    # integration tests are skipped when these programs are missing, which
    # would lower the coverage; demand a complete test environment instead
    missing=()
    for tool in ffmpeg ffprobe rsync rsnapshot; do
        command -v "$tool" >/dev/null || missing+=("$tool")
    done
    python3 -c 'import tkinter' 2>/dev/null || missing+=("python3-tk")
    # the tests always run on a virtual display, never on the real desktop
    if [[ "${MEDIATOOLS_XVFB:-}" != 1 ]]; then
        if command -v xvfb-run >/dev/null; then
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

# Each tree is checked in full once (dev-conventions templates/check-cache.sh,
# block 1): key = tree of the working copy + Python version + installed packages.
# (deviation from the template: an unpacked archive without .git must not
# end the script here - then nothing is cached)
CHECKED="$(git rev-parse --git-dir 2>/dev/null || echo .git)/checked-trees"
check_key() {
    local idx tree
    idx="$(mktemp)"
    cp "$(git rev-parse --git-dir)/index" "$idx" 2>/dev/null || true
    tree="$(GIT_INDEX_FILE="$idx" git add -A >/dev/null 2>&1 \
            && GIT_INDEX_FILE="$idx" git write-tree)" || tree=""
    rm -f "$idx"
    [[ -n "$tree" ]] || return 1
    printf '%s py%s deps-%s\n' "$tree" \
        "$(python3 -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')" \
        "$(python3 -m pip list --format=freeze 2>/dev/null | sha256sum | cut -c1-12)"
}
KEY=""
if [[ "${CI:-}" != "true" && "${CHECK_FULL:-0}" != "1" ]]; then
    KEY="$(check_key)" || KEY=""
fi

gate privacy  python3 tools/privacy_scan.py
gate shell    shellcheck install.sh tools/*.sh .githooks/pre-push src/mediatools/update/*.sh

# check cache, block 2: a tree that passed all gates before skips the rest
if [[ -n "$KEY" && -f "$CHECKED" ]] && grep -qxF "$KEY" "$CHECKED"; then
    printf '\nThis tree passed all gates before (%s).\n' "${KEY%% *}"
    printf 'format, lint, types, security and tests skipped - CHECK_FULL=1 forces them.\n'
    printf '\nAll quality gates passed.\n'
    exit 0
fi
gate format   pycodestyle --max-line-length=100 src tests tools
gate lint     pylint --fail-under=10 src tests tools
gate types    mypy
gate security bandit -q -c pyproject.toml -r src
if (( FAST == 0 )); then
    gate tests python3 -m pytest -q -p no:cacheprovider \
        --cov=mediatools --cov-report=term
    # check cache, block 3: remember this fully checked tree (last 50)
    if [[ -n "$KEY" ]]; then
        { [[ -f "$CHECKED" ]] && tail -n 49 "$CHECKED"; echo "$KEY"; } > "$CHECKED.tmp"
        mv "$CHECKED.tmp" "$CHECKED"
    fi
fi

printf '\nAll quality gates passed.\n'
