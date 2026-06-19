# Changelog

All notable changes to Task Station are documented here. This project adheres to
[Semantic Versioning](https://semver.org).

## [1.0.7] — 2026-06-19

### Added
- `render --format md` emits the `/todo` list as GitHub-flavored Markdown tables
  (Open then Closed) directly, so the skill prints them verbatim instead of
  hand-transcribing the ASCII block (table cells are `|`/newline-escaped).
- Live attached-session marker: tasks with more than one currently-attached session
  show a ` ⧉N` count (sessions whose link still resolves to the task) in both ASCII
  and Markdown list output.

### Changed
- The per-message unattached-session nudge is collapsed: the full block (open-task
  list, attach/create syntax, colour legend) prints only on the first miss and at
  escalation; intermediate misses get a single compact line — a large recurring
  token saving. Per-prompt category detection is preserved in the compact line.
- `update`, `pin`, and `unpin` accept comma-separated task lists, mirroring `done`'s
  batch contract (one result line per ref; a bad ref is reported but doesn't abort).
- Skill docs: after a close/mutation, confirm with the result line(s) only — don't
  re-render the full `/todo` list unless asked.

## [1.0.6] — 2026-06-19

### Added
- prompt-context now detects explicit create/attach-a-task phrasing and hard-steers
  to task-station over the native TaskCreate tool. A new `task_intent()` detector in
  `categories.py` recognises imperatives like "make this a task" / "attach this to a
  task" (ignoring questions about the concept and negations); when one fires,
  `prompt-context` prints a hard directive — even in a skipped or already-attached
  session — telling Claude to use task-station's `create`/`attach` now and NOT the
  built-in/native (ephemeral session-todo) `TaskCreate` tool. `guidance` carries the
  same one-line warning.

## [1.0.5] — 2026-06-18

### Added
- OS-appearance-aware tinting: each category now ships a light **and** a dark
  palette, auto-detected on macOS (`defaults read -g AppleInterfaceStyle`). Use
  `config --tint-theme auto|dark|light` to override the auto-detection.

### Changed
- Darkened the white/neutral dark-mode tint (`#2b2b30` → `#202024`); it was too
  bright on dark backgrounds.
- README: documented that `/todo` output enters the session as context, giving Claude a
  cross-project big-picture view for large multi-domain work.

## [1.0.4] — 2026-06-18

### Added
- `/done` and `/todo … -s` accept comma-separated task numbers (multi-close /
  multi-jump): `/done 1,2,5` closes each task with one result line apiece, and
  `/todo 1,2,5 -s` attaches and opens a window per task. A bad ref in the list is
  reported but doesn't abort the others; a single number works as before.

### Fixed
- Bare `/todo`/`/done` now follow plugin updates without a restart: the engine
  symlink is re-pointed on every prompt (idempotent), not just at session start,
  so an in-session `/plugin update` no longer leaves them on stale code.

### Changed
- README reorganized — `/todo` table preview and a new **Key Features** section
  first, then a linked **Table of Contents**, with **Install** and a dedicated
  **Commands** section moved up.

## [1.0.3] — 2026-06-18

### Changed
- The `/todo` block now prints an authoritative `Commands:` footer (single source
  of truth) listing every command, and the command reminder is relayed from it
  rather than hardcoded in the command instructions.

## [1.0.2] — 2026-06-18

### Added
- Opt-in `/todo` update check (default **off**). Enable with
  `task-station config --update-check on`: the `/todo` list view shows a one-line
  footer when a newer Task Station version is published. When off there are zero
  network calls; when on it makes at most one `git ls-remote` version check to
  GitHub per day (cached locally under `task-station-data/update-check.json`),
  with a hard timeout. Offline or any failure is silent, and no task data is ever
  sent.
- The `/todo` list now also surfaces `/todo <n> -s` (jump to a task's pinned
  session) and `/task-station:config` in its command reminder, matching the README.

## [1.0.1] — 2026-06-18

### Added
- `/todo closed [N]` and `/todo all` listing modes. `/todo closed` shows the 20
  most recent closed tasks, `/todo closed N` shows N, and `/todo all` shows every
  task. The bare `/todo` list still shows only the most recent few closed; the
  "older closed hidden" footer now points at these commands.

### Changed
- Collapsed `/task-station:setup` into `/task-station:config` — `config` now owns
  `--policy` and `--tint-profiles` and shows a status view with no args; the
  `setup` command is removed.
- Default `brown` category is now `[DATABASE]` ("database"); data-migration tasks
  still auto-classify there.
- The "fixing PR review feedback" category moved from gold to **yellow**
  (`[FIX PR]`); gold is now a reserved slot.

### Fixed
- `/done` now closes **iTerm2** windows, not just Terminal.app.
- Command bodies fall back to `CLAUDE_CODE_SESSION_ID` when `CLAUDE_SESSION_ID` is
  unset.

## [1.0.0] — 2026-06-17

Initial public release as Task Station.

### Added
- `/todo` and `/done` slash commands (list, open+resume, close), plus the
  namespaced `/task-station:todo` / `:done` and `/task-station:config` / `:setup`.
- Persistent, cross-session task tracking with one JSON file per task under
  `${CLAUDE_CONFIG_DIR:-~/.claude}/todo-data/`. All state is local.
- Auto-attach nudging + an optional enforcement gate (PostToolUse + Stop hooks)
  that keeps real work from going untracked.
- Category colours with per-category terminal tinting: zero-setup **auto** mode
  (iTerm2 `SetColors` / Terminal.app OSC 11) or **profile** mode (named profiles).
  Tinting targets the originating window, focus-independently.
- `todo config` (settings) and `todo setup` (doctor + installers): a 100%-reversible
  delegation-policy block for your `CLAUDE.md`, and a Terminal.app tint-profile helper.
- In-project worker delegation (`lib/delegate/`) + a `delegating-work` skill.
- Opt-in bare `/todo` + `/done` aliases (`todo config --bare-cmds on`).
- Session pinning (`todo.py pin`/`unpin`) to re-pin a task to a fresh session and
  save tokens when a context window grows stale.
