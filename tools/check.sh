#!/usr/bin/env bash
# check.sh - the quality gates of mediatools (from templates/check.sh of the
# dev-conventions bundle v20).
#
# Usage:  tools/check.sh [--fast] [--only GATE]
#
#   --fast       skip the gate "tests" (quick checks while editing)
#   --only GATE  run one gate only (CI: one step per gate)
#   -h, --help   this text
#
# Gates, in this order: privacy, shell, format, lint, types, security, tests.
# Every gate prints one result line (PASS / FAIL / SKIP, one fact, duration);
# the first failing gate stops the run and shows the end of its output.
# The same script runs locally, in the pre-push hook and in CI, so a green
# local run predicts a green pipeline.
# Exit code: 0 all passed, 1 a gate failed, 2 wrong usage or missing tool.
#
# The tests of the GUI need Tk for Python (package python3-tk) and a
# display; the script restarts itself under xvfb-run (package xvfb), a
# virtual display without a window, so no test touches the real desktop.
# The integration tests need ffmpeg, rsync and rsnapshot; a missing one
# stops the run instead of silently lowering the coverage.
#
# Each tree is checked in full once: after a full green run the tree of the
# working copy, the Python version and a hash of the installed packages are
# stored in .git/checked-trees; for a known key only privacy and shell run
# again (the privacy scan also reads commit messages and tags). CHECK_FULL=1
# forces a full run; CI (CI=true) always runs in full.
#
# Runs as a normal user; it only reads the working copy (and writes the
# check cache inside .git).
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -f tools/output.sh ]]; then
    echo "check.sh: tools/output.sh is missing - copy tools/output.sh of the dev-conventions bundle" >&2
    exit 2
fi
# shellcheck source=tools/output.sh disable=SC1091  # path chosen at run time
source tools/output.sh
export PYTHONPATH="src:tests${PYTHONPATH:+:$PYTHONPATH}"

GATES=(privacy shell format lint types security tests)
declare -A ABOUT=(
    [privacy]="tools/privacy_scan.py · files, paths, history"
    [shell]="shellcheck"
    [format]="pycodestyle · max line 120"
    [lint]="pylint · 10.00/10, max line 120"
    [types]="mypy"
    [security]="bandit"
    [tests]="pytest + coverage · branch ≥ 90 %"
)

FAST=false
ONLY=""
ARGS=("$@")
while (($#)); do
    case "$1" in
        --fast) FAST=true ;;
        --only) ONLY="${2:-}"; shift ;;
        -h|--help) sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit 0 ;;
        *) echo "check.sh: unknown argument $1" >&2; exit 2 ;;
    esac
    shift
done
if [[ -n "$ONLY" && -z "${ABOUT[$ONLY]:-}" ]]; then
    echo "check.sh: unknown gate '$ONLY' - one of: ${GATES[*]}" >&2
    exit 2
fi
wanted() { [[ -z "$ONLY" || "$ONLY" == "$1" ]]; }
TESTS=false
if wanted tests && ! $FAST; then TESTS=true; fi

# Development venv: an active one is used as it is; otherwise .venv of the
# project is activated for this script only. CI has none and installs the
# tools directly. Nothing is created or installed here.
if [[ -z "${VIRTUAL_ENV:-}" && -f .venv/bin/activate ]]; then
    # shellcheck disable=SC1091  # path chosen at run time
    source .venv/bin/activate
    detail "venv: .venv (activated for this check)"
fi

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
if $TESTS; then
    missing=()
    for tool in ffmpeg ffprobe rsync rsnapshot; do
        command -v "$tool" >/dev/null || missing+=("$tool")
    done
    python3 -c 'import tkinter' 2>/dev/null || missing+=("python3-tk")
    # the tests always run on a virtual display, never on the real desktop
    if [[ "${MEDIATOOLS_XVFB:-}" != 1 ]]; then
        if command -v xvfb-run >/dev/null; then
            [[ -t 1 && -z "${NO_COLOR:-}" ]] && export FORCE_COLOR=1   # keep the colours
            exec env MEDIATOOLS_XVFB=1 xvfb-run -a "$0" "${ARGS[@]}"
        fi
        missing+=("xvfb")
    fi
    if ((${#missing[@]})); then
        echo "missing programs for the integration tests: ${missing[*]}" >&2
        echo "install with: sudo apt install ffmpeg rsync rsnapshot python3-tk xvfb" >&2
        exit 2
    fi
fi

# --- check cache (templates/check-cache.sh, block 1) --------------------------------
# key = tree of the working copy + Python version + installed packages; an
# unpacked archive without .git caches nothing
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
if [[ -z "$ONLY" && "${CI:-}" != "true" && "${CHECK_FULL:-0}" != "1" ]]; then
    KEY="$(check_key)" || KEY=""
fi

# --- the gates: each prints its own output; its last line is the fact -------------
shell_scripts() {
    local files=(install.sh tools/*.sh .githooks/pre-push src/mediatools/update/*.sh)
    shellcheck "${files[@]}"
    echo "${#files[@]} scripts, no finding"
}
run_gate() {
    case "$1" in
        privacy)  python3 tools/privacy_scan.py ;;
        shell)    shell_scripts ;;
        format)   pycodestyle --max-line-length=120 src tests tools && echo "no finding" ;;
        lint)     pylint --fail-under=10 src tests tools ;;
        types)    mypy ;;
        security) bandit -q -c pyproject.toml -r src && echo "no finding" ;;
        tests)    python3 -m pytest -q -p no:cacheprovider --cov=mediatools --cov-report=term ;;
    esac
}
# shortest useful fact of a gate's output
fact_of() {
    case "$1" in
        lint)  grep -o 'rated at [0-9.]*/10' "$LOG" | tail -n 1 ;;
        tests) printf '%s · coverage %s\n' "$(grep -oE '[0-9]+ passed' "$LOG" | tail -n 1)" \
                   "$(grep -oE 'Total coverage: [0-9.]+%' "$LOG" | tail -n 1 | cut -d' ' -f3)" ;;
        *)     grep -v '^[[:space:]]*$' "$LOG" | tail -n 1 | cut -c 1-44 ;;
    esac
}

# --- run ---------------------------------------------------------------------------
START=$SECONDS
LOG="$(mktemp)"
trap 'rm -f "$LOG"' EXIT
selected=("${GATES[@]}")
[[ -z "$ONLY" ]] || selected=("$ONLY")
title "Quality gates  $(git rev-parse --short HEAD 2>/dev/null || echo "no git") · Python $(python3 -c 'import platform; print(platform.python_version())')"
cached=false
if [[ -n "$KEY" && -f "$CHECKED" ]] && grep -qxF "$KEY" "$CHECKED"; then
    cached=true
    detail "check cache: tree ${KEY%% *} passed all gates before - privacy and shell only (CHECK_FULL=1 forces all)"
fi
n=0 failed="" passed=0 skipped=0
for gate in "${selected[@]}"; do
    n=$((n + 1))
    stage "[$n/${#selected[@]}] $gate" "${ABOUT[$gate]}"
    if $cached && [[ "$gate" != privacy && "$gate" != shell ]]; then
        skip "$gate" "tree checked before"
        skipped=$((skipped + 1))
        continue
    fi
    if $FAST && [[ "$gate" == tests ]]; then
        skip "tests" "--fast"
        skipped=$((skipped + 1))
        continue
    fi
    begin=$SECONDS code=0
    if [[ "$gate" == tests ]]; then
        run_gate "$gate" 2>&1 | tee "$LOG" || code=${PIPESTATUS[0]}
    else
        run_gate "$gate" >"$LOG" 2>&1 || code=$?
    fi
    if ((code == 0)); then
        fact="$(fact_of "$gate")" || fact=""
        pass "${fact:-$gate}" "$((SECONDS - begin)) s"
        passed=$((passed + 1))
    else
        fail "$gate" "$((SECONDS - begin)) s"
        [[ "$gate" == tests ]] || tail -n 30 "$LOG" | while IFS= read -r line; do detail "$line"; done
        failed="$gate"
        break
    fi
done

# --- check cache, block 3: remember a fully checked tree (last 50) ------------------
if [[ -z "$failed" && -n "$KEY" ]] && ! $cached && $TESTS; then
    { [[ -f "$CHECKED" ]] && tail -n 49 "$CHECKED"; echo "$KEY"; } > "$CHECKED.tmp"
    mv "$CHECKED.tmp" "$CHECKED"
fi

title "Result"
if [[ -z "$failed" ]]; then
    info "${G}${B}DONE${X}  $passed passed, $skipped skipped  $(ts "$((SECONDS - START)) s")"
    exit 0
fi
info "${R}${B}FAILED${X}  gate $failed  $(ts "$((SECONDS - START)) s")"
note "nothing was changed; fix the finding and run tools/check.sh again"
exit 1
