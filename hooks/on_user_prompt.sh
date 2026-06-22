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
# Eagerly re-point the engine symlink at the active install so the bare /todo,/done
# aliases track an in-session /plugin update without a restart. Idempotent and cheap:
# a readlink to compare, then a rare `ln -sfn` only when the target differs.
_cfg="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
_want="$CLAUDE_PLUGIN_ROOT/lib"
[ "$(readlink "$_cfg/task-station-engine" 2>/dev/null)" != "$_want" ] && ln -sfn "$_want" "$_cfg/task-station-engine" 2>/dev/null
session_id=$(echo "$input" | jq -r '.session_id // "unknown"')
prompt=$(echo "$input" | jq -r '.prompt // ""')

tint=$(TASK_STATION_PROMPT="$prompt" python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" prompt-tint --session "$session_id" 2>/dev/null)
if [ -n "$tint" ]; then
  # Full-palette escape → write it straight to the originating window (task 119).
  _dev=$(bash "${CLAUDE_PLUGIN_ROOT}/lib/origin-tty.sh" 2>/dev/null)
  printf '%s' "$tint" > "${_dev:-/dev/tty}" 2>/dev/null
fi

# Auto-set the tab/window title to '#<seq>: <title>' once attached — write the OSC
# escape to the originating TTY (same rail as the tint; reuse _dev if resolved above).
title=$(python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" prompt-title --session "$session_id" 2>/dev/null)
if [ -n "$title" ]; then
  _dev=${_dev:-$(bash "${CLAUDE_PLUGIN_ROOT}/lib/origin-tty.sh" 2>/dev/null)}
  printf '%s' "$title" > "${_dev:-/dev/tty}" 2>/dev/null
fi

TASK_STATION_PROMPT="$prompt" python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" prompt-context --session "$session_id"
