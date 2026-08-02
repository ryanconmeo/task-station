---
description: Unpin a task's pinned resume target so /todo reverts to most-recent-substantive. No arg = this session's task; with an arg (e.g. /unpin 13 or /unpin 1,2,5) = that task/those tasks by number.
argument-hint: "[task number(s), comma-separated — omit for the current session's task]"
allowed-tools: Bash
disable-model-invocation: true
---

!`if [ -n "$ARGUMENTS" ]; then python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" unpin --task "$ARGUMENTS"; else python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" unpin --session "${CLAUDE_SESSION_ID:-$CLAUDE_CODE_SESSION_ID}"; fi`

Relay the result line(s) above to the user verbatim (each confirms the unpin, says the task wasn't pinned, or that no task is attached). Do not re-render the /todo list.
