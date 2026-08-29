---
description: Close a task and detach. No arg = close THIS session's task (leaves the window open by default; auto-close is opt-in via config --done-closes-window on). With an arg (e.g. /done 13 or /done 1,2,5) = close that task — or several, comma-separated — by number/id, leave this window open. Reopen later with /todo.
argument-hint: "[task number(s), comma-separated — omit to close current session's task]"
allowed-tools: Bash
disable-model-invocation: true
---

```!
IFS= read -r -d '' TS_ARGV <<'TS_ARGV_END'
$ARGUMENTS
TS_ARGV_END
TS_ARGV="${TS_ARGV%$'\n'}"
TS_RC=0
TS_OUT="$(if [ -n "$TS_ARGV" ]; then python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" done --task "$TS_ARGV"; else python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" done --session "${CLAUDE_SESSION_ID:-$CLAUDE_CODE_SESSION_ID}"; fi 2>&1)" || TS_RC=$?
[ -n "$TS_OUT" ] && printf '%s\n' "$TS_OUT"
[ "$TS_RC" -eq 0 ] || printf '%s\n' "[task-station] THE SKILL WAS NOT INVOKED. /done exited $TS_RC without producing the close result; nothing was read and nothing was changed. Any text above this line is the failure, not the close result."
:
```

> **If the block above is not the command's own output** — it is empty, it is a raw shell error, or it carries `THE SKILL WAS NOT INVOKED` — then `/done` **DID NOT RUN**. Say exactly that to the user in one line, show the failure verbatim, and stop. Do not reconstruct the output by hand, and do not describe anything as done.

Relay the result above to the user. There is **one result line per task** — confirm **each** closed task by name, and surface any "already closed" / "no match" line verbatim. If nothing was attached (no-arg path), say so in one short line.

Confirm with the result line(s) **only**. Do **not** re-render the full `/todo` list after a close (or any mutation) — that just burns tokens. Re-run `/todo` only if the user explicitly asks to see the list.

(With NO argument this closes the current session's task and leaves the terminal window open; it auto-closes ~1s later ONLY if you've enabled `config --done-closes-window on` (default is to leave the window open). With an argument, it closes the named task(s) by number/id — a comma-separated list like `1,2,5` closes several at once — and always leaves THIS window open.)
