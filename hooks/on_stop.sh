#!/usr/bin/env bash
# Stop hook: if this session edited files but never tracked a /todo task, refuse
# to end the turn (emit {"decision":"block","reason":...}) until a task is
# attached/created or the session is skipped. All the logic — incl. the anti-wedge
# block cap and self-heal — lives in todo.py (stop-gate); this passes its stdout
# (a decision JSON blob, or nothing) straight through. Always exit 0.
input=$(cat)
# Suppressed inside delegate-spawned workers — task tracking is the hub's job.
[ -n "$CLAUDE_TODO_SUPPRESS" ] && exit 0
session_id=$(echo "$input" | jq -r '.session_id // "unknown"')
python3 "${CLAUDE_PLUGIN_ROOT}/lib/todo.py" stop-gate --session "$session_id"
exit 0
