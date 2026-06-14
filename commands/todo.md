---
description: List tracked tasks; /todo <n> opens & resumes one.
argument-hint: "[task # · add -s to jump to its window]"
allowed-tools: Bash
disable-model-invocation: true
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/lib/todo.py" render --session "${CLAUDE_SESSION_ID}" --arg "$ARGUMENTS"`

The block above is the live output of the task tracker.

Each task is colour-coded by category (an `<emoji> [TAG]` after the title — the emoji dot carries the colour, the tag names it). The categories map to zsh aliases that tint the Terminal.app profile — see `$CLAUDE_PLUGIN_ROOT/CATEGORIES.md` for the full taxonomy; customize colours without touching that file via `todo-data/categories.json`.

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
- If it is a **session-jump** (the block starts with `[SESSION-JUMP]` — produced by `/todo <n> -s`), the user wants to hop **straight into this task's working session in a fresh window**, not read a recap. This session is now attached to the task (reopened if it was closed). The tracker has *already* tried to open a new Terminal window running the resume command, leaving this window untouched. Do only this, then stop:
  - If the block contains `[JUMP-WINDOW-OPENED]`: a new Terminal window is already up and running the resume command. Reply with **only the `↪ …` line from the block, copied verbatim, and nothing else** — no preamble, no recap, no extra words. Do **not** run the resume command yourself; it's already running in the new window.
  - If the block does **not** contain `[JUMP-WINDOW-OPENED]` (auto-open failed, or there's no recorded session yet): surface the `cd … && claude --resume …` one-liner **verbatim in a copyable code block** so the user can run it themselves, note in one line what it does, and stop. Do not attempt to open a window yourself.

  Either way, **skip the recap** — that's the whole point of `-s`. Treat this task as the active context for the rest of the session.
