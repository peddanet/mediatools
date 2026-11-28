#!/usr/bin/env bash
# output.sh - shared console output of all tool projects (sourced, not run).
#
#   source "$(dirname "${BASH_SOURCE[0]}")/output.sh"
#
# Functions: title (block), stage (gate / CI job / step), pass, fail, skip
# (result line: text, duration or fact on the right), info, detail, note,
# bar (test progress), ts (dim time stamp), ci (one CI line), timing (one
# row of the time measurement), attention (before a question: bell and
# desktop notice). templates/output-demo.sh shows each look.
#
# Colours: never when NO_COLOR is set (it wins over FORCE_COLOR); else when
# FORCE_COLOR is set or standard output is a terminal. Log files are plain
# text: write them with NO_COLOR=1 or strip the codes. Widths count
# characters, not bytes, and ignore colour codes.
# shellcheck disable=SC2034  # colour variables are used by the sourcing script
if [[ -n "${NO_COLOR:-}" ]]; then
    COLOR=0
elif [[ -n "${FORCE_COLOR:-}" || -t 1 ]]; then
    COLOR=1
else
    COLOR=0
fi
if (( COLOR )); then
    B=$'\e[1m' D=$'\e[2m' R=$'\e[31m' G=$'\e[32m' Y=$'\e[33m'
    BL=$'\e[34m' C=$'\e[36m' X=$'\e[0m'
else
    B="" D="" R="" G="" Y="" BL="" C="" X=""
fi

visible() {  # the text without colour codes
    printf '%s' "$1" | sed 's/\x1b\[[0-9;]*m//g'
}
pad() {     # text padded with spaces to a width in characters
    local LC_ALL=C.UTF-8 plain   # count characters, not bytes ("·", "≥", "…")
    plain="$(visible "$1")"
    printf '%s%*s' "$1" $(($2 > ${#plain} ? $2 - ${#plain} : 0)) ""
}
title() {   # level 1: one block of the whole run
    local LC_ALL=C.UTF-8 plain
    plain="$(visible "$1")"
    printf '\n%s%s══ %s %s%s\n' "$B" "$C" "$1" \
        "$(printf '═%.0s' $(seq 1 $((68 - ${#plain}))))" "$X"
}
stage() {   # level 2: one gate / one CI job / one step
    printf '\n%s%s▶ %s%s %s%s%s\n' "$B" "$BL" "$1" "$X" "$D" "${2:-}" "$X"
}
info() { printf '  %s\n' "$1"; }
detail() { printf '  %s%s%s\n' "$D" "$1" "$X"; }
pass() { printf '  %s✔ PASS%s  %s %s%s%s\n' "$G$B" "$X" "$(pad "$1" 44)" "$D" "$2" "$X"; }
fail() { printf '  %s✘ FAIL%s  %s %s%s%s\n' "$R$B" "$X" "$(pad "$1" 44)" "$D" "$2" "$X"; }
skip() { printf '  %s– SKIP%s  %s %s%s%s\n' "$Y$B" "$X" "$(pad "$1" 44)" "$D" "$2" "$X"; }
note() { printf '  %s! %s%s\n' "$Y" "$1" "$X"; }
bar() {     # tests progress line: done total elapsed [slow test]
    local done=$1 total=$2 width=30 filled i full="" empty=""
    filled=$((done * width / total))
    for ((i = 0; i < filled; i++)); do full+="█"; done
    for ((i = filled; i < width; i++)); do empty+="░"; done
    printf '  %s%s%s%s%s %3d %%  %3d/%d  %s%s%s\n' "$G" "$full" "$D" "$empty" "$X" \
        $((done * 100 / total)) "$done" "$total" "$D" "$3" "$X"
}
ts() { printf '%s%s%s' "$D" "$1" "$X"; }
ci() {      # one CI line: time job step result
    printf '  %s  %s %s %s\n' "$(ts "$1")" "$(pad "$2" 14)" "$(pad "$3" 22)" "$4"
}
timing() {  # one row of the time measurement table
    printf '  %s %s%6s%s\n' "$(pad "$1" 44)" "$D" "$2" "$X"
}
attention() {  # before a question read from a terminal: bell, desktop notice
    [[ -t 0 && -z "${CI:-}" ]] || return 0
    { printf '\a' >/dev/tty; } 2>/dev/null || true
    [[ -z "${NO_NOTIFY:-}" && -n "${WAYLAND_DISPLAY:-}${DISPLAY:-}" ]] || return 0
    command -v notify-send >/dev/null 2>&1 || return 0
    # -u critical: the notice stays until it is clicked away (libnotify-bin)
    notify-send -u critical -a "${0##*/}" "${0##*/} waits for an answer" "$1" \
        >/dev/null 2>&1 || true
}
