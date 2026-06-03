#!/usr/bin/env bash
# UserPromptSubmit hook: keep the attached task's activity fresh (and reopen it
# if it was closed), or — if this session has no task yet — inject guidance that
# tells Claude how to attach/create one. stdout is injected as model context.
input=$(cat)
session_id=$(echo "$input" | jq -r '.session_id // "unknown"')
prompt=$(echo "$input" | jq -r '.prompt // ""')

TODO_PROMPT="$prompt" python3 "$HOME/.claude/todo/todo.py" prompt-context --session "$session_id"
