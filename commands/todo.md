---
description: List tracked tasks (open first, then by recent activity); /todo <n> opens a task's detail and resumes it in this session.
argument-hint: "[number or task-id; add -s to jump to the working session and skip the recap — omit to list all]"
allowed-tools: Bash
disable-model-invocation: true
---

!`python3 "$HOME/.claude/todo/todo.py" render --session "${CLAUDE_SESSION_ID}" --arg "$ARGUMENTS"`

The block above is the live output of the task tracker.

Each task is colour-coded by category (an `<emoji> [TAG]` after the title — the emoji dot carries the colour, the tag names it). The categories map to zsh aliases that tint the Terminal.app profile — see `~/.claude/todo/CATEGORIES.md`.

- If it is a **list**, present it to the user as a **clean Markdown table** — never a plain numbered list. Render **two separate tables**, an **Open** one first then a **Closed** one, each preserving the tracker's ordering (open first, then by most recent activity). The `#` column must show each task's **exact number from the tracker output, verbatim** — numbers are stable ids, never renumber or resequence them. Use exactly these columns: `#` (task number, right-aligned), `Task` (title), `Category` (the `<emoji> [TAG]` kept intact), `Effort` (the `▰▱` bar + size), `Activity` (the relative timestamp). If the tracker notes hidden older closed tasks, repeat that note after the Closed table. After the tables, remind them they can run `/todo <number>` to open and resume a task, `/done` to close the task this session is working on, and `/done <number>` to close any task by its number. Do not take any other action.
- If it is a **task detail**, this session has just been attached to that task (reopened if it was closed). Do all of the following, in order:
  1. The detail prints the task's category and a `zsh -ic '<color>'` line — **run that command** to tint this terminal to the task's colour.
  2. Give the user a **detailed recap** — not a one-liner. Draw on the full summary and the recent-activity log:
     - State the overall **goal** in a sentence.
     - Break the summary's specific sub-items / acceptance criteria into a **bullet list** so nothing is buried.
     - Walk through what the **activity log** shows has happened so far, using its relative timestamps, so it's clear where the work left off and what the likely **next step** is.
  3. If the detail block includes a **resume command** (`cd … && claude --resume …`), surface it to the user **verbatim in a copyable code block** at the end of the recap, and explain it jumps straight back into the working session that holds this task's context (correct directory + session in one command).
  4. Ask what they'd like to do next, or just continue the work.

  Treat this task as the active context for the rest of the session.
- If it is a **session-jump** (the block starts with `[SESSION-JUMP]` — produced by `/todo <n> -s`), the user wants to hop **straight back into this task's working session**, not read a recap. This session is now attached to the task (reopened if it was closed). Do only this, then stop:
  1. Run the `zsh -ic '<color>'` tint line so this terminal matches the task's category.
  2. Surface the `cd … && claude --resume …` resume command **verbatim in a copyable code block**, and note in one line that it drops them straight into the main connected session that holds this task's context.
  3. **Do not** print a goal / summary / acceptance-criteria / activity-log recap — skipping that is the entire point of `-s`. A single short acknowledgement line is fine; nothing more.

  Treat this task as the active context for the rest of the session.
