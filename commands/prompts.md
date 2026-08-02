---
description: Show a task's prompt trail — your human prompts + Claude's reply, timestamped, session-attributed (read-only).
argument-hint: "[task # — omit for the current session's task]"
allowed-tools: Bash
disable-model-invocation: true
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" render --arg "${ARGUMENTS:+$ARGUMENTS }prompts" --session "${CLAUDE_SESSION_ID:-$CLAUDE_CODE_SESSION_ID}"`

Print the Prompts trail above verbatim and do nothing else. It is READ-ONLY — it did not attach/reopen the task. By default it is the curated view: only the genuine human-typed prompts (slash commands, compaction rows, and hook/managed wrappers filtered out), each followed by Claude's last-bullet reply (`↳ …`), oldest first (hub + every delegated worker). For the complete raw trail (every kind, no replies) run `task-station prompts --task <n> --all`; for the shareable Markdown artifact run `task-station prompts --task <n> --md`.
