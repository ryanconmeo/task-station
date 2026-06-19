---
description: Close a task and detach. No arg = close THIS session's task (and close its window). With an arg (e.g. /done 13 or /done 1,2,5) = close that task — or several, comma-separated — by number/id, leave this window open. Reopen later with /todo.
argument-hint: "[task number(s), comma-separated — omit to close current session's task]"
allowed-tools: Bash
disable-model-invocation: true
---

!`export TASK_STATION_MIGRATE=1; if [ -n "$ARGUMENTS" ]; then python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" done --task "$ARGUMENTS"; else python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" done --session "${CLAUDE_SESSION_ID:-$CLAUDE_CODE_SESSION_ID}"; bash "${CLAUDE_PLUGIN_ROOT}/lib/close-session-window.sh" --detach --after 1 >/dev/null 2>&1; fi`

Relay the result above to the user. There is **one result line per task** — confirm **each** closed task by name, and surface any "already closed" / "no match" line verbatim. If nothing was attached (no-arg path), say so in one short line.

Confirm with the result line(s) **only**. Do **not** re-render the full `/todo` list after a close (or any mutation) — that just burns tokens. Re-run `/todo` only if the user explicitly asks to see the list.

(With NO argument this closes the current session's task and its terminal window auto-closes ~1s later. With an argument, it closes the named task(s) by number/id — a comma-separated list like `1,2,5` closes several at once — and leaves THIS window open.)
