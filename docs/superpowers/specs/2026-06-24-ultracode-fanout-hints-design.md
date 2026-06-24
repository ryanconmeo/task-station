# ultracode fan-out hints — design

*Status: shipped in 1.14.0 (2026-06-24).*

## Purpose

"**ultracode**" is Claude Code's built-in multi-agent **Workflow / dynamic-
orchestration** feature: when the user opts in (the harness recognises the
keyword `ultracode`), Claude fans subagents out across a task. Multi-agent
breadth is powerful for **read / analyze / design / review** work but a poor fit
for repo writes — hub subagents have no repo `CLAUDE.md`, hooks, MCP servers, or
build/test environment, and parallel writers conflict. Task Station already has
the sanctioned path for repo writes: **delegation** (a worktree worker off the
repo's base branch, with a story + PR).

Task Station therefore **hints** about ultracode — it never fires orchestration
itself. The hint appears **only when a task genuinely warrants breadth**, and
**only for read/analyze/design/review phases**. It is never shown for trivial
work and never suggests pointing a workflow at repo writes.

## Principle

- **Surface breadth where it pays; keep writes on the delegation rails.**
- **Hint, don't orchestrate.** Task Station emits advisory/steering text only —
  the human opts into ultracode by typing the keyword; the harness orchestrates.
- **Derive, don't store.** Worthiness is computed from a task's existing
  effort + category. No new task field.

## The derived signal — `fanout_worthy(task)`

Pure, testable helper (`lib/task-station.py`, near the effort helpers). Reads
only the task's `effort` + `color` (category):

- **TRUE** if effort ∈ {**L**, **XL**} (any category), **OR** the category is one
  of the **breadth set** — **REVIEW** (`orange`) · **RESEARCH** (`purple`) ·
  **DATA** (`brown`, databases / schemas / ETL / migrations) — **AND**
  effort ∈ {**M**, **L**, **XL**}.
- **FALSE** if effort ∈ {xs, s}, if effort is unset/None, or for a plain open (○)
  question / untracked task (no effort).

Categories are referenced by **slot KEY** (`orange`/`purple`/`brown`), not tag, so
a re-skinned tag/label can't break the gate. Effort uses the `EFFORT_ORDER`
ordinal. Recompute everywhere; never persist.

## Gate

Every emission is additionally gated on `config.ultracode_hints_enabled()`
(default **ON**; env `TASK_STATION_ULTRACODE_HINTS` on/off/1/0/true/false wins
over the persisted `ultracode_hints` flag — mirrors `tint_enabled()` /
`statusline_enabled()`). Reset by `config --reset` (in `RESET_KEYS`).

## Audience split

| Mode | Audience | Where | What |
|---|---|---|---|
| **Default** (no signal) | **Human** — advisory | task detail recap (`/todo <n>`) + SessionStart attached-task line | "consider running with `ultracode`"; the human opts in by typing the keyword. **Never** instructs the model to auto-fire a workflow. |
| **ultracode turn** (signal present) | **Model** — steering | per-prompt hook (`cmd_prompt_context`), when attached to a worthy task | The harness is already orchestrating; steer breadth to think-phases and route repo writes through delegation. |

The human advisory is on **low-frequency** surfaces only (detail + SessionStart),
never the per-prompt hook in default mode (too frequent). The model steering is
**per-prompt** but only on an ultracode turn, in addition to the normal
activity-touch (existing behaviour is never suppressed).

## The two copy blocks

**Human advisory** (`ultracode_advisory(task)` → detail + SessionStart):

> ultracode: this task is fan-out-worthy (effort `<EFFORT>`). For its
> read/analyze/design/review phases, running it with `ultracode` gives multi-agent
> breadth (if your Claude Code supports it). Repo edits still go through delegation
> (worktree + story/PR) — never point a workflow at writes.

**Model steering** (`ultracode_steering()` → `cmd_prompt_context` on signal):

> ultracode active on a fan-out-worthy task: fan subagents out for
> read/analyze/design/review/verify ONLY (hub context — no repo CLAUDE.md/hooks/build
> env). Route every repo MUTATION through task-station delegation (a worktree worker
> off the repo's base branch, with story + PR). Never edit/build/test in workflow
> subagents.

Both blocks carry the delegation-boundary wording (`delegation` … `never`).

## Signal detection — `ultracode_signal(prompt)`

- **DEFINITIVE (implemented):** the word-boundary token `ultracode`
  (case-insensitive) in the prompt (`os.environ["TASK_STATION_PROMPT"]`, passed by
  `hooks/on_user_prompt.sh`). This matches the harness's own opt-in trigger.
- **Standing-mode (deliberately NOT implemented):** the `UserPromptSubmit` hook
  input — and the env it forwards — carries **no reliable "standing ultracode
  mode" field or env var**. Rather than invent a fragile detector that would
  mis-steer the model, standing mode is not detected; those users still get the
  **human advisory** (which is independent of the signal). Documented inline in
  `ultracode_signal`.

## Explicitly out of scope

- **No model auto-fire** — Task Station never instructs the model to start a
  workflow; the human opts in.
- **No ultracode for writes** — the hint is read/analyze/design/review/verify
  only.
- **No bypass of the write path** — repo mutations always go through delegation
  (worktree + story/PR), never workflow subagents.
- **No per-task state** — worthiness is derived, never stored.

## Components

| Component | File |
|---|---|
| `fanout_worthy(task)`, `ultracode_signal(prompt)`, `ultracode_advisory(task)`, `ultracode_steering()` | `lib/task-station.py` |
| `ultracode_hints_enabled()`, board row, `cmd_config` dispatch, `RESET_KEYS` | `lib/config.py` |
| `--ultracode-hints [on\|off]` / `--ultracode-hints-get` argparse | `lib/task-station.py` (`main`) |
| Human advisory on detail | `_format_detail` |
| Human advisory on SessionStart | `cmd_session_start` |
| Model steering on ultracode turn | `cmd_prompt_context` |

## Tests (`tests/test_ultracode.py`)

- `fanout_worthy` matrix: xs/s → False (any category); unset effort → False; L/XL
  any category → True; REVIEW/RESEARCH/DATA at M → True; same categories at s
  → False; non-breadth category at M → False; open question with no effort → False.
- `ultracode_signal`: "please ultracode this" / "Ultracode" → True; "let's discuss
  the workflow" / "" → False.
- `ultracode_hints_enabled`: default True; env off overrides; config off persists.
- Human advisory: present in the detail render for a worthy task with hints on;
  absent when not worthy and absent when hints off.
- Steering: present in `cmd_prompt_context` output when attached to a worthy task +
  signal in prompt + hints on; absent without the signal, absent for a non-worthy
  task, absent when hints off.
- Both copy blocks contain the delegation-boundary wording (`delegation`, `never`).
