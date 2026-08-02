---
description: Show a task's full trace — decisions + log + activity (read-only).
argument-hint: "[task # — omit for the current session's task]"
allowed-tools: Bash
disable-model-invocation: true
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" render --arg "${ARGUMENTS:+$ARGUMENTS }history" --session "${CLAUDE_SESSION_ID:-$CLAUDE_CODE_SESSION_ID}"`

Print the History trace above verbatim and do nothing else. It is READ-ONLY — it did not attach/reopen the task.
