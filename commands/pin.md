---
description: Pin THIS session as the current task's canonical resume target, so /todo always resumes here.
argument-hint: ""
allowed-tools: Bash
disable-model-invocation: true
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" pin --session "${CLAUDE_SESSION_ID:-$CLAUDE_CODE_SESSION_ID}"`

Relay the result line above to the user verbatim (it confirms the pin or says no task is attached). Do not re-render the /todo list.
