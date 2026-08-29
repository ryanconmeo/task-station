---
description: Pin THIS session as the current task's canonical resume target, so /todo always resumes here.
argument-hint: ""
allowed-tools: Bash
disable-model-invocation: true
---

```!
TS_RC=0
TS_OUT="$(python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" pin --session "${CLAUDE_SESSION_ID:-$CLAUDE_CODE_SESSION_ID}" 2>&1)" || TS_RC=$?
[ -n "$TS_OUT" ] && printf '%s\n' "$TS_OUT"
[ "$TS_RC" -eq 0 ] || printf '%s\n' "[task-station] THE SKILL WAS NOT INVOKED. /pin exited $TS_RC without producing the pin result; nothing was read and nothing was changed. Any text above this line is the failure, not the pin result."
:
```

> **If the block above is not the command's own output** — it is empty, it is a raw shell error, or it carries `THE SKILL WAS NOT INVOKED` — then `/pin` **DID NOT RUN**. Say exactly that to the user in one line, show the failure verbatim, and stop. Do not reconstruct the output by hand, and do not describe anything as done.

Relay the result line above to the user verbatim (it confirms the pin or says no task is attached). Do not re-render the /todo list.
