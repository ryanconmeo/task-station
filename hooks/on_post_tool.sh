#!/usr/bin/env bash
# PostToolUse(Write|Edit|NotebookEdit) hook: the moment this session edits a file
# without a tracked task, inject a one-shot reminder to attach/create one. The
# one-shot gating lives in task-station.py (mark-edited), so this stays a thin pass-through
# of its stdout (a hookSpecificOutput JSON blob, or nothing).
input=$(cat)
# Suppressed inside delegate-spawned workers — task tracking is the hub's job.
[ -n "$TASK_STATION_SUPPRESS" ] && exit 0
# Shared helper (see hooks/_ts_lib.sh): still non-fatal, but a non-zero exit is now
# recorded to <data_dir>/logs/hook-health.log. The stubs keep this hook working
# verbatim if the lib is ever missing.
# shellcheck source=/dev/null
. "$(dirname "${BASH_SOURCE[0]:-$0}")/_ts_lib.sh" 2>/dev/null || true
if ! command -v ts_run >/dev/null 2>&1; then      # no lib → old behaviour verbatim
  ts_run() { shift; "$@" >/dev/null 2>&1 || true; }
  ts_capture() { shift; "$@" 2>/dev/null || true; }
fi
# Parse stdin with python3 (hard requirement) instead of jq; hookjson.py mirrors
# `jq -r '.path // default'` and is a silent no-op on malformed input.
session_id=$(printf '%s' "$input" | python3 "${CLAUDE_PLUGIN_ROOT}/lib/hookjson.py" session_id unknown)
python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" hook mark-edited --session "$session_id"
# Best-effort briefing capture: record the edited path on the attached task's
# `files` list (silent no-op if no attached task / no path). Never blocks the hook.
file_path=$(printf '%s' "$input" | python3 "${CLAUDE_PLUGIN_ROOT}/lib/hookjson.py" tool_input.file_path)
if [ -n "$file_path" ]; then
  ts_run touch-file python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" hook touch-file --session "$session_id" --file "$file_path"
fi
# F6 artifact capture: scan this tool's RESULT for PR/work-item URLs and record them on the
# attached task (deduped) + auto-link across peers. Fed the whole hook payload on stdin;
# self-gates on an attached task and TASK_STATION_SUPPRESS. Best-effort — never blocks.
printf '%s' "$input" | ts_run capture-artifacts python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" hook capture-artifacts --session "$session_id"
exit 0
