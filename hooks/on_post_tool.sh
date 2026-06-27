#!/usr/bin/env bash
# PostToolUse(Write|Edit|NotebookEdit) hook: the moment this session edits a file
# without a tracked task, inject a one-shot reminder to attach/create one. The
# one-shot gating lives in task-station.py (mark-edited), so this stays a thin pass-through
# of its stdout (a hookSpecificOutput JSON blob, or nothing).
input=$(cat)
# Suppressed inside delegate-spawned workers — task tracking is the hub's job.
[ -n "$TASK_STATION_SUPPRESS" ] && exit 0
session_id=$(echo "$input" | jq -r '.session_id // "unknown"')
python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" mark-edited --session "$session_id"
# Best-effort briefing capture: record the edited path on the attached task's
# `files` list (silent no-op if no attached task / no path). Never blocks the hook.
file_path=$(echo "$input" | jq -r '.tool_input.file_path // empty')
if [ -n "$file_path" ]; then
  python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" touch-file --session "$session_id" --file "$file_path" >/dev/null 2>&1 || true
fi
exit 0
