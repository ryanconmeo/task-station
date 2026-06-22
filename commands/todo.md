---
description: List tracked tasks; /todo <n> opens & resumes one; /todo closed [N] / all list more closed.
argument-hint: "[task # · -s to jump (comma-separated #s jump several) · 'closed [N]' / 'all' to list closed]"
allowed-tools: Bash
disable-model-invocation: true
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" render --format md --session "${CLAUDE_SESSION_ID:-$CLAUDE_CODE_SESSION_ID}" --arg "$ARGUMENTS"`

The block above is the live output of the task tracker.

Each task is colour-coded by category (an `<emoji> [TAG]` after the title — the emoji dot carries the colour, the tag names it). When categorising a task, always pick the **most accurate** category from the full taxonomy (`$CLAUDE_PLUGIN_ROOT/CATEGORIES.md`) even if it isn't on the board yet: the board starts lean (just **CORE** — BUG · FEATURE · GENERAL) and a slot is **enabled automatically** the first time a task is assigned to it (unless `config --auto-categories off`). Each category ships a full **Sands** palette that the hooks apply to your terminal automatically via standard escape sequences (zero-setup; iTerm2 and Terminal.app both honored); customize colours without touching that file via `task-station-data/categories.json`.

A task's lifecycle is **one field, `status`, with three values: open (`○`) → active (`●`) → closed (`✕`)**. Each row carries a leading **STATUS column** (before `#`) holding the lifecycle glyph — `○` **open** (a topic merely raised), `●` **active** (work has actually started), or `✕` **closed** (closed rows live in their own section). A task auto-promotes `○ open → ● active` when you delegate `--worktree` for it, edit a file in an attached session, or set it manually with `status --task <ref> active`. New tasks start `○ open` (or `● active` via `create --active`); `/done` closes them (`✕`), and reopening a closed task returns it to `○ open`. The legend under the tables reads `● active · ○ open · ✕ closed`.

- If it is a **list**, the tracker has already rendered it as GitHub-flavored Markdown — two tables (Open then Closed) whose columns are a centered status glyph, then `#`/`Task`/`Category`/`Effort`/`Activity`, any "… older closed …" note, and a `**Commands**` section: an aligned command help block inside a ``` fence (command-and-description columns plus a `<n>` / `<n1, n2, …>` / `[N]` notation legend), not a bullet list or a table. **Print that block verbatim** to the user — do not re-transcribe it, rebuild the tables, renumber rows, reflow the aligned columns, or reword the commands. Do not take any other action.
- If it is a **task detail**, this session has just been attached to that task (reopened if it was closed). Do all of the following, in order:
  1. The terminal is tinted to the task's category automatically (full Sands palette via the hooks) — nothing to run by hand.
  2. Give the user a **detailed recap** — not a one-liner. Draw on the full summary and the recent-activity log:
     - State the overall **goal** in a sentence.
     - Break the summary's specific sub-items / acceptance criteria into a **bullet list** so nothing is buried.
     - Walk through what the **activity log** shows has happened so far, using its relative timestamps, so it's clear where the work left off and what the likely **next step** is.
  3. If the detail block includes a **resume command** (`cd … && claude --resume …`, or `cd … && claude --session-id …` when the task fresh-starts a clean session), surface it to the user **verbatim in a copyable code block** at the end of the recap, and explain it jumps straight back into the working session that holds this task's context (correct directory + session in one command).
  4. Ask what they'd like to do next, or just continue the work.

  Treat this task as the active context for the rest of the session.
- If it is a **session-jump** (the block starts with `[SESSION-JUMP]` — produced by `/todo <n> -s`, which accepts a comma-separated list like `/todo 1,2,5 -s` to jump into several tasks at once), the user wants to hop **straight into each task's working session in a fresh window**, not read a recap. Each session-jump task is now attached (reopened if it was closed). The tracker has *already* tried to open a new Terminal window per task running its resume command, leaving this window untouched. The output may contain **several** `[SESSION-JUMP]` blocks — one per task. Do only this, then stop:
  - For **each** block that contains `[JUMP-WINDOW-OPENED]`: a new Terminal window is already up and running that task's resume command. Reply with **each** `↪ …` line from the block(s), one per task, copied verbatim, and nothing else — no preamble, no recap, no extra words. Do **not** run any resume command yourself; they're already running in the new windows.
  - For **each** block that does **not** contain `[JUMP-WINDOW-OPENED]` (auto-open failed, or there's no recorded session yet): surface that task's one-liner **verbatim in a copyable code block** — either `cd … && claude --resume …` (resuming the working session) or `cd … && claude --session-id …` (fresh-starting a clean, auto-attaching session when there's no valid session to resume) — so the user can run it themselves, note in one line what it does, and stop. Do not attempt to open a window yourself. (A `No task matching '…'.` line for a bad ref should be surfaced verbatim too.)

  Either way, **skip the recap** — that's the whole point of `-s`. Treat this task as the active context for the rest of the session.

**Tracking & grouping (fold, don't fork).** Every topic is tracked from the first prompt — even a plain question becomes an `○ open` task (it auto-promotes to `● active` when work starts). Before creating a NEW task, scan the board (open + active): if the prompt continues an existing task, **attach to it and append the prompt as a note** (`attach --session <id> --task <ref> --note '<prompt>'`) rather than spawning a sibling — so related questions across sessions accumulate under one task. Only a genuinely new topic creates a task. A skipped session stays silent.

After any mutation (closing, attaching, creating, pinning, updating a task), confirm with the tool's result line(s) **only** — do **not** re-render this full `/todo` list unless the user explicitly asks to see it.

_tip: `/repos` sets up repo routing for delegating fuzzy tasks (enrichment is off by default)._
