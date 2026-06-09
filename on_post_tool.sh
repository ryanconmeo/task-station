#!/usr/bin/env bash
# PostToolUse(Write|Edit|NotebookEdit) hook: the moment this session edits a file
# without a tracked task, inject a one-shot reminder to attach/create one. The
# one-shot gating lives in todo.py (mark-edited), so this stays a thin pass-through
# of its stdout (a hookSpecificOutput JSON blob, or nothing).
input=$(cat)
# Suppressed inside delegate-spawned workers — task tracking is the hub's job.
[ -n "$CLAUDE_TODO_SUPPRESS" ] && exit 0
session_id=$(echo "$input" | jq -r '.session_id // "unknown"')
python3 "$HOME/.claude/todo/todo.py" mark-edited --session "$session_id"
exit 0
