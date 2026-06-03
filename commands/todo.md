---
description: List tracked tasks (open first, then by recent activity); /todo <n> opens a task's detail and resumes it in this session.
argument-hint: "[number or task-id — omit to list all]"
allowed-tools: Bash
disable-model-invocation: true
---

!`python3 "$HOME/.claude/todo/todo.py" render --session "${CLAUDE_SESSION_ID}" --arg "$ARGUMENTS"`

The block above is the live output of the task tracker.

- If it is a **list**, present it to the user as-is (a numbered list, open tasks first, then closed, each sorted by most recent activity). Remind them they can run `/todo <number>` to open and resume a task, and `/done` to close the task this session is working on. Do not take any other action.
- If it is a **task detail**, this session has just been attached to that task (reopened if it was closed). Give the user a brief, friendly recap of the task from the summary and recent activity, then ask what they'd like to do next or continue the work. Treat this task as the active context for the rest of the session.
