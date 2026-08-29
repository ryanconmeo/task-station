---
description: Unpin a task's pinned resume target so /todo reverts to most-recent-substantive. No arg = this session's task; with an arg (e.g. /unpin 13 or /unpin 1,2,5) = that task/those tasks by number.
argument-hint: "[task number(s), comma-separated — omit for the current session's task]"
allowed-tools: Bash
disable-model-invocation: true
---

```!
IFS= read -r -d '' TS_ARGV <<'TS_ARGV_END'
$ARGUMENTS
TS_ARGV_END
TS_ARGV="${TS_ARGV%$'\n'}"
TS_RC=0
TS_OUT="$(if [ -n "$TS_ARGV" ]; then python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" unpin --task "$TS_ARGV"; else python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" unpin --session "${CLAUDE_SESSION_ID:-$CLAUDE_CODE_SESSION_ID}"; fi 2>&1)" || TS_RC=$?
[ -n "$TS_OUT" ] && printf '%s\n' "$TS_OUT"
[ "$TS_RC" -eq 0 ] || printf '%s\n' "[task-station] THE SKILL WAS NOT INVOKED. /unpin exited $TS_RC without producing the unpin result; nothing was read and nothing was changed. Any text above this line is the failure, not the unpin result."
:
```

> **If the block above is not the command's own output** — it is empty, it is a raw shell error, or it carries `THE SKILL WAS NOT INVOKED` — then `/unpin` **DID NOT RUN**. Say exactly that to the user in one line, show the failure verbatim, and stop. Do not reconstruct the output by hand, and do not describe anything as done.

Relay the result line(s) above to the user verbatim (each confirms the unpin, says the task wasn't pinned, or that no task is attached). Do not re-render the /todo list.
