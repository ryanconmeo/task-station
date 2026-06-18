---
description: Close a task and detach. No arg = close THIS session's task (and close its window). With an arg (e.g. /done 13) = close that task by number/id, leave this window open. Reopen later with /todo.
argument-hint: "[task number or id — omit to close the current session's task]"
allowed-tools: Bash
disable-model-invocation: true
---

!`if [ -n "$ARGUMENTS" ]; then python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" done --task "$ARGUMENTS"; else python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" done --session "${CLAUDE_SESSION_ID:-$CLAUDE_CODE_SESSION_ID}"; bash "${CLAUDE_PLUGIN_ROOT}/lib/close-session-window.sh" --detach --after 1 >/dev/null 2>&1; fi`

Relay the result above to the user in one short line. If a task was closed, confirm it by name. If nothing matched / nothing was attached, say so.

(With NO argument this closes the current session's task and its terminal window auto-closes ~1s later. With an argument, it closes the named task by number/id and leaves THIS window open.)
