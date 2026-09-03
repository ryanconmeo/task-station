#!/usr/bin/env bash
# UserPromptSubmit hook: keep the attached task's activity fresh (and reopen it
# if it was closed), or — if this session has no task yet — inject guidance that
# tells Claude how to attach/create one. stdout is injected as model context.
#
# Before any of that, if this prompt INVOKES A SKILL with a known category
# (e.g. /review → orange), tint the terminal RIGHT NOW —
# synchronously, before Claude responds — so the colour applies immediately
# instead of waiting for Claude to read the guidance and run the alias itself.
input=$(cat)
# Suppressed inside delegate-spawned workers — task tracking + tinting is the hub's job.
[ -n "$TASK_STATION_SUPPRESS" ] && exit 0
# No-op outside plugin context (CLAUDE_PLUGIN_ROOT is set only when the plugin runs us);
# guards against a stray registration resolving to /lib/task-station.py and exiting non-zero.
[ -n "${CLAUDE_PLUGIN_ROOT:-}" ] || exit 0
# Shared helper (see hooks/_ts_lib.sh): non-fatal as before, but a non-zero exit is
# now recorded to <data_dir>/logs/hook-health.log instead of vanishing. The stubs
# keep this hook working verbatim if the lib is ever missing.
# shellcheck source=/dev/null
. "$(dirname "${BASH_SOURCE[0]:-$0}")/_ts_lib.sh" 2>/dev/null || true
if ! command -v ts_run >/dev/null 2>&1; then      # no lib → old behaviour verbatim
  ts_run() { shift; "$@" >/dev/null 2>&1 || true; }
  ts_capture() { shift; "$@" 2>/dev/null || true; }
fi
# Eagerly re-point the engine symlink at the active install so the bare /todo,/done
# aliases track an in-session /plugin update without a restart. Idempotent and cheap:
# a readlink to compare, then a rare `ln -sfn` only when the target differs. The
# readlink stays masked — exit 1 on an absent link is normal, not a failure.
_cfg="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
_want="$CLAUDE_PLUGIN_ROOT/lib"
[ "$(readlink "$_cfg/task-station-engine" 2>/dev/null)" != "$_want" ] && ts_run engine-symlink ln -sfn "$_want" "$_cfg/task-station-engine"
# Parse the hook's JSON stdin with python3 (a hard requirement) instead of jq —
# lib/hookjson.py mirrors `jq -r '.path // default'` and is a silent no-op on
# malformed input. See hooks: jq is no longer required.
session_id=$(printf '%s' "$input" | python3 "${CLAUDE_PLUGIN_ROOT}/lib/hookjson.py" session_id unknown)
prompt=$(printf '%s' "$input" | python3 "${CLAUDE_PLUGIN_ROOT}/lib/hookjson.py" prompt)

tint=$(ts_capture prompt-tint env TASK_STATION_PROMPT="$prompt" python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" hook prompt-tint --session "$session_id")
if [ -n "$tint" ]; then
  # Full-palette escape → write it straight to the originating window (task 119).
  # origin-tty.sh exits 1 when the tty is undeterminable (normal), and the write is
  # a redirect — both stay masked exactly as before.
  _dev=$(bash "${CLAUDE_PLUGIN_ROOT}/lib/origin-tty.sh" 2>/dev/null)
  printf '%s' "$tint" > "${_dev:-/dev/tty}" 2>/dev/null
fi

# Auto-set the tab/window title to '#<seq>: <title>' once attached — write the OSC
# escape to the originating TTY (same rail as the tint; reuse _dev if resolved above).
title=$(ts_capture prompt-title python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" hook prompt-title --session "$session_id")
if [ -n "$title" ]; then
  _dev=${_dev:-$(bash "${CLAUDE_PLUGIN_ROOT}/lib/origin-tty.sh" 2>/dev/null)}
  printf '%s' "$title" > "${_dev:-/dev/tty}" 2>/dev/null
fi

TASK_STATION_PROMPT="$prompt" python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" hook prompt-context --session "$session_id"

# Cost HUD (WS7): finalize the just-ended turn's $ delta and re-baseline the new
# turn (the UserPromptSubmit payload carries no cost, so the baseline is the last
# cost the HUD render observed — snapshot lives under <data_dir>/hud/, not /tmp).
# Self-gates on `--hud` (a cheap no-op when off); errors never disrupt the prompt.
ts_run hud-turn-start python3 "${CLAUDE_PLUGIN_ROOT}/lib/hud.py" turn-start --session "$session_id"
