---
description: Close the task this session is working on and detach the session. Reopen later with /todo.
allowed-tools: Bash
disable-model-invocation: true
---

!`python3 "$HOME/.claude/todo/todo.py" done --session "${CLAUDE_SESSION_ID}"; bash "$HOME/.claude/todo/close-session-window.sh" --detach --after 1 >/dev/null 2>&1`

Relay the result above to the user in one short line. If a task was closed, confirm it by name. If nothing was attached, let them know there was no active task to close.

(The terminal window for THIS session auto-closes ~1s after this runs, matched by tty.)
