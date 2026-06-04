---
description: List tracked tasks (open first, then by recent activity); /todo <n> opens a task's detail and resumes it in this session.
argument-hint: "[number or task-id — omit to list all]"
allowed-tools: Bash
disable-model-invocation: true
---

!`python3 "$HOME/.claude/todo/todo.py" render --session "${CLAUDE_SESSION_ID}" --arg "$ARGUMENTS"`

The block above is the live output of the task tracker.

Each task is colour-coded by category (an `<emoji> [TAG]` after the title — the emoji dot carries the colour, the tag names it). The categories map to zsh aliases that tint the Terminal.app profile — see `~/.claude/todo/CATEGORIES.md`.

- If it is a **list**, present it to the user as-is (a numbered list, open tasks first, then closed, each sorted by most recent activity), keeping the category `<emoji> [TAG]`s intact. Remind them they can run `/todo <number>` to open and resume a task, and `/done` to close the task this session is working on. Do not take any other action.
- If it is a **task detail**, this session has just been attached to that task (reopened if it was closed). The detail prints the task's category and a `zsh -ic '<color>'` line — **run that command** to tint this terminal to the task's colour. Then give the user a brief, friendly recap of the task from the summary and recent activity, and ask what they'd like to do next or continue the work. Treat this task as the active context for the rest of the session.
