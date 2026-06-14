# claude-todo

Persistent, cross-session task tracking for Claude Code **plus in-project worker
delegation** — two halves designed to be used together. Every session attaches to
a single trackable task; tasks survive across sessions and are queryable any time
with `/todo`. When a task needs a specific repo's machinery (its `CLAUDE.md`,
hooks, MCP, permissions), the bundled `delegate` helper spawns an in-project worker
linked to that task — see [Delegate](#delegate--in-project-workers). You'll want
both: the tracker knows *what* you're doing and *where*; delegate does the work
*there* with full project context.

```
Tasks (open first, then by recent activity):  •  /todo <n> = open detail & resume   ·   /done = close current task   ·   close any by id:  python3 …/todo.py done --task <n|id>

OPEN
  1  Build cross-session task tracker       ⚪ [SKILLS]     ▆ L   2h ago
  2  Fix auth refresh bug                   🔴 [BUG]        ▃ S   yesterday

CLOSED
  3  Migrate legacy billing to v2           🟤 [MIGRATION]  █ XL  3d ago

Effort:  ▁ XS  ▃ S  ▅ M  ▆ L  █ XL
Legend: 🔴 [BUG] bug · 🟠 [REVIEW] code review · 🟢 [VOLT] coding for Volt · 🔵 [DEVOPS] devops · 🩷 [DESIGN] design · ⚪ [SKILLS] skills and memories · 🟤 [MIGRATION] legacy data migration for Volt · …
```

## How it works

- **Auto-attach.** On each user message, a `UserPromptSubmit` hook injects
  guidance telling Claude to either **attach** the session to a matching open
  task or **create** a new one. The per-prompt nudge is deliberately **compact**
  (open-task list, a one-line trackability test — concrete work that edits files /
  spans multiple steps, not a question or one-line fix — the attach/create
  commands, and a one-line colour legend); the full rules, TRACK/SKIP examples,
  and colour-picker guidance live in `todo.py guidance`, fetched on demand, to
  keep the recurring token cost low. When Claude attaches or creates a task it
  announces it in one short line (e.g. "📋 Tracking this as a new task: …");
  after that the nudge goes silent.
- **Miss escalation.** Each message that goes by without the session attaching
  bumps a per-session counter; after a few unattached messages the nudge
  escalates ("N messages in and still untracked — attach now, or `skip`"). This
  closes the feedback loop so a real task can't silently stay untracked.
- **Enforcement gate (optional).** The nudges above are advisory — Claude can
  ignore them. The gate makes tracking reliable by hooking the real signal,
  *a file edit*. A `PostToolUse(Write|Edit|NotebookEdit)` hook fires a **one-shot**
  reminder the first time an untracked session edits a file (gated by an
  `.edited` marker, so it costs ~one injection per session, not one per edit).
  A `Stop` hook then **refuses to end the turn** — returning
  `{"decision":"block","reason":…}` — while the session has edited files but
  tracked no task. So a session that did real work literally can't finish
  without attaching/creating a task or running `skip`. It's **self-healing**
  (attaching, creating, skipping, or `/done` clears the markers, silencing the
  gate the instant work is tracked) and **anti-wedge** (capped at two blocks, so
  a non-complying loop gives up rather than locking the session). Both hooks are
  included in the plugin by default; remove them from `hooks/hooks.json` if you
  only want the advisory nudges.
- **Skip.** `todo.py skip --session <id>` marks a session intentionally
  untracked (e.g. a pure Q&A session); the nudge then stays silent for it.
  Attaching to or creating a task later resumes tracking.
- **Create dedup.** `create` refuses to make a near-duplicate of an existing
  open task (title overlap by Jaccard or containment) and points at the match to
  `attach` instead; pass `--force` to override.
- **One task per session.** A session→task link is recorded in the data directory.
  A `SessionStart` hook surfaces open tasks (or the already-attached one) so a
  resumed session recognises its task.
- **Activity tracking.** Every message bumps the attached task's `updated_at`,
  which drives the "recent activity" sort.
- **Effort estimate.** Each task carries an optional t-shirt size
  (`XS`/`S`/`M`/`L`/`XL`) capturing its complexity & scope, shown as a gauged
  column (`▃ S`) in the list and spelled out in the detail view. Claude sets it
  at `create` time (the auto-attach nudge asks for it); adjust later with
  `todo.py update --task <n> --effort <xs|s|m|l|xl>`. `--effort` also accepts the
  numeric 1–5 scale and words (`small`/`large`/…); unknown values are ignored
  rather than guessed, so a task simply shows `· --` until one is set.
- **Effort re-rates on scope change.** It isn't auto-derived from churn (that
  would measure activity, not size) — instead, whenever an `update` amends a
  task's title/summary/scope *without* also re-rating, it prints a one-line
  prompt to reconsider the effort (showing the current size). So as scope grows
  or shrinks, Claude bumps the size up or down to match — the estimate tracks
  reality at exactly the moments scope actually moves, with no nudge noise on
  otherwise-silent attached sessions.
- **`/todo`** lists all tasks (open first, then by recent activity). Each task
  shows its **stable number** (`seq`), assigned in creation order the first time
  it's seen and never reused — so a task keeps the same number even as others
  are added, closed, or reorder by recent activity.
  **`/todo <n>`** (or a task-id prefix) prints the task's detail and **adopts it
  into the current session** — your next message continues it. `<n>` matches the
  stable number, not a position in the list. If the task was closed, opening it
  reopens it.
  **`/todo <n> -s`** does the same attach/reopen but **immediately opens a fresh
  Terminal.app window** that runs the task's resume command (tint + `cd` +
  `claude --resume`), dropping you straight into its working session — **no
  recap**, and the window you typed `/todo` in is left untouched. The new window
  is opened by `open-session-window.sh` (macOS/Terminal-only; if it can't, the
  block falls back to printing the one-liner for you to run by hand). The `-s`
  flag may sit on either side of the number (`/todo -s <n>` works too).
- **`/done`** closes the task the current session is working on **and detaches
  the session** from it, so a follow-up message can't silently reopen it. To
  pick the task back up, use `/todo <n>`, which re-attaches and reopens it.
- **`/done <n>`** (or a task-id prefix) closes **any** task by its stable number
  or id — you don't have to be in that task's session. It detaches every session
  linked to the task and **leaves the current window open** (bare `/done` closes
  the current session's window; `/done <n>` does not, since you're still working
  here). Handy straight from the `/todo` list: see the numbers, close any of them
  in place. The underlying call is `todo.py done --task <n|id>`.
- **Resume resolution.** `/todo <n>` resumes the task's **most-recent substantive
  session**, finding it by id across all project buckets and reading the launch
  directory from the transcript itself — so it self-corrects even if the recorded
  cwd was wrong (e.g. you launched from `~` but `cd`'d into a worktree), and a 1-2
  message "just looking" session never displaces real work. It only ever resumes
  one of the task's *own* sessions, and starts fresh rather than risk a different
  task's session. To override the heuristic, **`todo.py pin --task <n> --session
  <id>`** locks a specific session (PK-style; `unpin` reverts). The printed resume
  one-liner also **re-tints the terminal to the task's colour** — it's prefixed with
  the category's zsh alias (e.g. `green 2>/dev/null; cd … && claude --resume …`), so
  pasting it into a fresh window restores the colour. The prefix is joined with `;`
  and swallows stderr, so it's a silent no-op for anyone who hasn't installed the
  colour aliases — the `cd` + resume always runs. (Omitted entirely when
  tinting is off — `"tint_terminal": false` in `categories.json`.)

- **Categories & terminal colours (optional plugin).** If `categories.py` is
  present (it ships with the plugin), every task carries a `color` from a
  taxonomy (bug/red, code-review/orange, devops/blue, design/pink, …); `/todo`
  appends a `<emoji> [TAG]` after each task and prints a legend. Each colour
  name is also a zsh alias that switches the Terminal.app profile, so on attach /
  create / resume Claude runs `zsh -ic '<color>'` to tint the terminal to the
  task's category. **Skills tint immediately:** when a prompt invokes a slash
  command mapped in `SKILL_COLORS` (e.g. `/volt:review-pr-auto` → orange), the
  `UserPromptSubmit` hook tints the terminal synchronously *before Claude
  responds*, so the colour applies the instant the skill runs. **All of this is
  isolated in `lib/categories.py`** — `todo.py` imports it defensively and runs
  as a plain, colourless tracker without it. The taxonomy ships as defaults in
  `lib/categories.py`; override or extend it without touching the shipped file
  via `todo-data/categories.json` (survives `/plugin update`) — see
  [`CATEGORIES.md`](CATEGORIES.md). If you like the tags but lack the profile
  aliases, set `"tint_terminal": false` in your `categories.json` to drop just
  the tint suggestions. Full taxonomy, wiring, and the three opt-out levels are
  in [`CATEGORIES.md`](CATEGORIES.md).

There is no auto-close: tasks stay open until you run `/done`. (The Claude Code
harness can't distinguish `/exit` from a crash or window-close, so closing is
kept explicit and deliberate.)

## Delegate — in-project workers

`claude-todo` ships a second half in [`lib/delegate/`](lib/delegate/delegate.py): a helper
that spawns an **in-project Claude worker** and links it to a task. The two are
meant to be used **together** — don't run one without the other.

**Why it exists.** A "hub" session launched from `~` (the way `/todo` is meant to
be driven) does *not* load any project's `./CLAUDE.md`, hooks, project-scoped
`.mcp.json`, project-local skills, or permissions/env — those load only in a
`claude` process whose cwd is inside the repo. `delegate` spawns exactly that
(`cd <repo> && claude -p …`), so the work runs with the project's full machinery,
keeps **one persistent worker per (task, repo)**, resumes it across turns, and
relays the result back to the hub.

```bash
# do work in a repo, linked to /todo task 38:
python3 "$CLAUDE_PLUGIN_ROOT/lib/delegate/delegate.py" run --project Volt --seq 38 \
  --task "<self-contained instructions>"
python3 "$CLAUDE_PLUGIN_ROOT/lib/delegate/delegate.py" list        # known workers
```

**How the two synergize:**
- Pass `--seq <n>` and the worker is named `todo-<seq>-<project>` (e.g.
  `todo-38-Volt`), keyed `<seq>:<project>` in the registry, and the repo is recorded
  on the task. `/todo <n>`'s detail then shows a **Workers** section with a
  one-command resume per repo — so from a task you drop straight into the right
  in-project worker. `--label <slug>` adds a second concurrent worker in the same
  repo (`todo-38-Volt-rbac`).
- Workers run with `CLAUDE_TODO_SUPPRESS=1`, so the `/todo` hooks stay silent inside
  them — tracking is the hub's job, not the worker's.
- The **hub** can't be renamed programmatically, but the `SessionStart` hook sets its
  **title** to `todo-<seq> · <title>`, and `todo.py whoami --session <id>` maps any
  session back to its task.

The registry lives at `<data_dir>/workers.json` (machine-local, not tracked by the
plugin). Used without `--seq`, `delegate` still works standalone (one worker per
repo, unnamed and unlinked).

**Enabling *auto*-delegation.** `delegate.py` is the *how*; the *when* — the rule
that makes Claude reach for it on its own — lives in your global `~/.claude/CLAUDE.md`
(loaded every session). This repo ships a ready-to-adapt
[`lib/delegate/POLICY-TEMPLATE.md`](lib/delegate/POLICY-TEMPLATE.md): copy it into your
`~/.claude/CLAUDE.md` and swap the placeholder workspace paths/project names for your
own. Without it, `delegate.py` still works when you invoke it by hand — Claude just
won't know *when* to use it automatically.

### Status bar integration

`todo.py whoami --session <id> --statusline` prints a ready-to-display, ANSI-colored one-line segment for the session's attached task — `#<seq>  <dot> [TAG]  <title>` — and nothing when the session has no task. Add `--width <N>` to truncate the title so the whole segment fits `N` columns (`0` = no limit). It's self-contained: it carries its own colors and knows nothing about the bar that renders it, so it drops into any status line (tmux, powerline, a custom prompt, or a Claude Code `statusLine` command).

```bash
$ todo.py whoami --session 5c8edf12 --statusline --width 0
#42  🔵 [DEVOPS]  Wire up the deploy pipeline
```

### Storage

One JSON file per task under `<data_dir>/tasks/<uuid>.json`; session→task links
under `<data_dir>/links/`. All writes are atomic. The data directory defaults to
`${CLAUDE_CONFIG_DIR:-~/.claude}/todo-data/`; set `$CLAUDE_TODO_HOME` to
override. It is machine-local and not tracked by the plugin — task data persists
across `/plugin update`.

### Limitations (Claude Code harness)

- `/todo` is a numbered text list, not an arrow-key picker — slash-command bash
  has no interactive TTY, so tools like `fzf` can't render.
- "Resume" adopts a task into your **current** session; it can't jump back into
  a different past chat transcript. To reopen the literal original chat, use
  `claude --resume <session-id>` from your shell.

## If you're Claude and someone asked you to install this

Run these commands:

```bash
/plugin marketplace add ryanconmeo/claude-todo
/plugin install claude-todo
```

That wires the `/todo` + `/done` commands and all four hooks automatically — no
`settings.json` edit required. Task data lands in
`${CLAUDE_CONFIG_DIR:-~/.claude}/todo-data/` and **survives `/plugin update`**.

`categories.py` ships with the author's colour taxonomy and terminal tinting
**on** by default (macOS only). To adjust without editing the shipped file, drop
a `categories.json` in the data directory — see [CATEGORIES.md](CATEGORIES.md)
for the JSON shape and opt-out levels.

**For in-project worker delegation** (optional, but the two halves are meant to be
used together): nothing extra to install — `lib/delegate/delegate.py` ships with
the plugin. To get *auto*-delegation, copy
[`lib/delegate/POLICY-TEMPLATE.md`](lib/delegate/POLICY-TEMPLATE.md)
into your global `~/.claude/CLAUDE.md` and customize the workspace paths. See the
[Delegate](#delegate--in-project-workers) section.

## Install

**Prerequisites:** [Claude Code](https://claude.ai/code), `jq`, `python3` (stdlib only).

    /plugin marketplace add ryanconmeo/claude-todo
    /plugin install claude-todo

That wires the `/todo` + `/done` commands and all four hooks automatically — no
`settings.json` edit, no command copy. Task data lives in
`${CLAUDE_CONFIG_DIR:-~/.claude}/todo-data/` (override with `$CLAUDE_TODO_HOME`)
and **survives `/plugin update`**.

The `PostToolUse` + `Stop` pair is the **enforcement gate** (see [How it works](#how-it-works)): a file edit in an untracked session triggers a one-shot reminder, and the `Stop` hook refuses to end the turn until a task is attached/created (or the session is skipped). Both are included in the plugin by default — remove them from `hooks/hooks.json` if you only want the advisory nudges — but together they're what makes tracking reliable instead of best-effort.

For *auto*-delegation, copy [`lib/delegate/POLICY-TEMPLATE.md`](lib/delegate/POLICY-TEMPLATE.md)
into your global `~/.claude/CLAUDE.md` and customize the workspace paths.

### Upgrading from the legacy clone

Existing tasks migrate automatically on first run (copied, not moved, so the old
`~/.claude/todo/` stays as a backup). Then remove the old wiring:
- delete the four `~/.claude/todo/*.sh` hook entries from `~/.claude/settings.json`
- delete `~/.claude/commands/{todo,done}.md` (the plugin ships its own)
- optionally `rm -rf ~/.claude/todo` once you've confirmed the migration

Re-run or inspect migration manually with `migrate`:
`python3 "$CLAUDE_PLUGIN_ROOT/lib/todo.py" migrate` (or `migrate --from <path>`).

## Update

```bash
/plugin update claude-todo
```

Task data in `todo-data/` is untouched.

## Uninstall

```bash
/plugin uninstall claude-todo
```

Task data persists in `${CLAUDE_CONFIG_DIR:-~/.claude}/todo-data/` — delete that
directory manually if you want to remove your task history. The worker registry
(`todo-data/workers.json`) also lives there; back it up first if you want to keep it.

If you are upgrading from the legacy clone (rather than uninstalling entirely),
also remove the old wiring:
- delete the four `~/.claude/todo/*.sh` hook entries from `~/.claude/settings.json`
- delete `~/.claude/commands/{todo,done}.md`
- optionally `rm -rf ~/.claude/todo`

If you copied the delegation policy into your global `~/.claude/CLAUDE.md`,
delete that block too. Restart Claude Code.

## Files

**`lib/todo.py`** — the engine: task storage, `/todo` and `/done`, the hooks' logic, plus the `whoami` (incl. the `--statusline` segment provider) and `update` commands.

**`lib/categories.py`** — optional colour-taxonomy + terminal-tint plugin; `todo.py` runs fine without it. Ships with defaults; users customize via `todo-data/categories.json` without editing this file. See [`CATEGORIES.md`](CATEGORIES.md).

**`lib/paths.py`** — resolves the mutable data directory (`${CLAUDE_CONFIG_DIR:-~/.claude}/todo-data/`, overridable with `$CLAUDE_TODO_HOME`) and handles legacy migration detection.

**`hooks/on_session_start.sh`** — `SessionStart` hook. Surfaces open tasks (or the attached one) and auto-sets the window title to `todo-<seq> · <title>`.

**`hooks/on_user_prompt.sh`** — `UserPromptSubmit` hook. Attaches/nudges the session and tints the terminal for skill-mapped prompts.

**`hooks/on_post_tool.sh`** — `PostToolUse(Write|Edit|NotebookEdit)` hook. Fires a one-shot reminder the first time an untracked session edits a file. Half of the optional enforcement gate.

**`hooks/on_stop.sh`** — `Stop` hook. Blocks the turn from ending while a session has edited files but tracked no task (self-healing, capped at two blocks so it can't wedge). The other half of the enforcement gate.

**`hooks/hooks.json`** — plugin hook manifest; declares all four hooks for the plugin system.

**`lib/close-session-window.sh`** — closes the Terminal.app window hosting a session; invoked by `/done`.

**`lib/open-session-window.sh`** — opens a fresh Terminal.app window running the task's resume command; invoked by `/todo <n> -s`.

**`lib/delegate/delegate.py`** — spawns/resumes in-project workers that carry the repo's full machinery (see [Delegate](#delegate--in-project-workers)).

**`lib/delegate/POLICY-TEMPLATE.md`** — copy into your global `~/.claude/CLAUDE.md` to enable *auto*-delegation.

**`.claude-plugin/plugin.json`** — plugin metadata (name, version, author, license).

**`.claude-plugin/marketplace.json`** — marketplace listing metadata.

**`commands/todo.md`**, **`commands/done.md`** — the `/todo` and `/done` slash commands; registered automatically by the plugin.
