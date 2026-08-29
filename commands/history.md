---
description: Show a task's full trace — decisions + log + activity (read-only).
argument-hint: "[task # — omit for the current session's task]"
allowed-tools: Bash
disable-model-invocation: true
---

```!
IFS= read -r -d '' TS_ARGV <<'TS_ARGV_END'
$ARGUMENTS
TS_ARGV_END
TS_ARGV="${TS_ARGV%$'\n'}"
TS_RC=0
TS_OUT="$(python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" render --arg "${TS_ARGV:+$TS_ARGV }history" --session "${CLAUDE_SESSION_ID:-$CLAUDE_CODE_SESSION_ID}" 2>&1)" || TS_RC=$?
[ -n "$TS_OUT" ] && printf '%s\n' "$TS_OUT"
[ "$TS_RC" -eq 0 ] || printf '%s\n' "[task-station] THE SKILL WAS NOT INVOKED. /history exited $TS_RC without producing the prompt trail; nothing was read and nothing was changed. Any text above this line is the failure, not the prompt trail."
:
```

> **If the block above is not the command's own output** — it is empty, it is a raw shell error, or it carries `THE SKILL WAS NOT INVOKED` — then `/history` **DID NOT RUN**. Say exactly that to the user in one line, show the failure verbatim, and stop. Do not reconstruct the output by hand, and do not describe anything as done.

Print the History trace above verbatim and do nothing else. It is READ-ONLY — it did not attach/reopen the task.
