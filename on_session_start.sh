#!/usr/bin/env bash
# SessionStart hook: surface open tasks (or the already-attached task) so Claude
# can recognise a resume. Emitted as SessionStart additionalContext JSON.
input=$(cat)
session_id=$(echo "$input" | jq -r '.session_id // "unknown"')
source=$(echo "$input" | jq -r '.source // ""')

ctx=$(python3 "$HOME/.claude/todo/todo.py" session-start --session "$session_id" --source "$source")
[ -n "$ctx" ] && jq -n --arg c "$ctx" \
  '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $c}}'
exit 0
