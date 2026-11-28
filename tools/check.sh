#!/usr/bin/env bash
# check.sh - the quality gates of mediatools (from templates/check.sh of the
# dev-conventions bundle v21).
#
# Usage:  tools/check.sh [--fast] [--only GATE] [--yes]
#
#   --fast       skip the gate "tests" (quick checks while editing)
#   --only GATE  run one gate only (CI: one step per gate)
#   --yes        no question: the tests take the free threads
#   -h, --help   this text
#
# Gates, in this order: privacy, shell, format, lint, types, security, tests.
# Every gate prints one result line (PASS / FAIL / SKIP, one fact, duration);
# the first failing gate stops the run and shows the end of its output.
# The same script runs locally, in the pre-push hook and in CI, so a green
# local run predicts a green pipeline.
# Exit code: 0 all passed, 1 a gate failed, 2 wrong usage or missing tool.
#
# Parallel tests (pytest-xdist, a dev dependency): in a terminal check.sh
# asks "N of M threads free (load L) - run the tests on N workers? [Y/n]";
# free = threads minus the load of the last minute (rounded), at least 1. Without a
# terminal (pre-push hook, release.sh --yes) or with --yes it takes the free
# threads without asking; in CI (CI=true) all threads of the runner.
# CHECK_WORKERS=N fixes the number, CHECK_WORKERS=0 or 1 runs them one after
# another. Without pytest-xdist the tests run one after another, with a note.
# The tests of unchanged bundle files are skipped (tools/bundle.sha256,
# tools/bundle_tests.py); CHECK_BUNDLE_TESTS=1 runs them anyway.
# The coverage table is printed only when the coverage is below the limit.
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
YES=false
XDIST=()
WORKERS=1
ARGS=("$@")
while (($#)); do
    case "$1" in
        --fast) FAST=true ;;
        --yes|-y) YES=true ;;
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
# workers - number of pytest workers (sets WORKERS; 1 = one after another)
workers() {
    local threads load free answer=""
    threads="$(nproc)"
    if [[ -n "${CHECK_WORKERS:-}" ]]; then
        [[ "$CHECK_WORKERS" =~ ^[0-9]+$ ]] || { note "CHECK_WORKERS=$CHECK_WORKERS is no number - one worker"; CHECK_WORKERS=1; }
        WORKERS=$((CHECK_WORKERS > 1 ? CHECK_WORKERS : 1))
        detail "tests: $WORKERS worker(s), set by CHECK_WORKERS"
    elif [[ -n "${CI:-}" ]]; then
        WORKERS=$threads
        detail "tests: $WORKERS workers (all threads of the CI runner)"
    else
        load="$(cut -d ' ' -f 1 /proc/loadavg)"
        free="$(awk -v t="$threads" -v l="$load" 'BEGIN { f = t - int(l + 0.5); print (f < 1 ? 1 : f) }')"
        WORKERS=$free
        if ! $YES && [[ -t 0 && -t 1 ]] && ((free > 1)); then
            attention "$free of $threads threads free - run the tests on $free workers?"
            read -r -p "  $free of $threads threads free (load $load) - run the tests on $free workers? [Y/n] " answer \
                || answer=n
            [[ ! "$answer" =~ ^[nN]$ ]] || WORKERS=1
        fi
        detail "tests: $WORKERS worker(s) - $free of $threads threads free (load $load)"
    fi
    if ((WORKERS > 1)) && ! python3 -c 'import xdist' 2>/dev/null; then
        note "pytest-xdist is not installed - the tests run one after another (.venv/bin/pip install -e '.[dev]')"
        WORKERS=1
    fi
}
# the coverage table only when the limit is missed (pyproject.toml: fail_under)
coverage_table() {
    grep -q 'FAIL Required test coverage' "$LOG" || return 0
    python3 -m coverage report 2>&1 | while IFS= read -r line; do detail "$line"; done
}
run_gate() {
    case "$1" in
        privacy)  python3 tools/privacy_scan.py ;;
        shell)    shell_scripts ;;
        format)   pycodestyle --max-line-length=120 src tests tools && echo "no finding" ;;
        lint)     pylint --fail-under=10 src tests tools ;;
        types)    mypy ;;
        security) bandit -q -c pyproject.toml -r src && echo "no finding" ;;
        # console_output_style=classic: no "[100%]" of pytest next to the progress bar;
        # --cov-report= : no table on a green run (coverage_table shows it below the limit)
        tests)    python3 -m pytest -q -p no:cacheprovider -o console_output_style=classic \
                      --cov=mediatools --cov-report= "${XDIST[@]}" ;;
    esac
}
# shortest useful fact of a gate's output
fact_of() {
    case "$1" in
        # "privacy scan: 413 term(s), 26 allowed phrase(s), 0 finding(s)" -> "413 terms, 26 allowed, 0 findings"
        privacy) local short
                 short="$(sed -nE 's/^privacy scan: ([0-9]+) term.*, ([0-9]+) allowed.*, ([0-9]+) finding.*/\1 terms, \2 allowed, \3 findings/p' \
                          "$LOG" | tail -n 1)"
                 [[ -n "$short" ]] || short="$(grep -v '^[[:space:]]*$' "$LOG" | tail -n 1 | cut -c 1-44)"
                 echo "$short" ;;
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
    if [[ "$gate" == tests ]]; then
        workers
        XDIST=()
        # loadscope: the tests of one class (or module) stay on one worker
        ((WORKERS == 1)) || XDIST=(-n "$WORKERS" --dist loadscope)
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
        if [[ "$gate" == tests ]]; then coverage_table; else
            tail -n 30 "$LOG" | while IFS= read -r line; do detail "$line"; done
        fi
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
