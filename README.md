# Task Station

**Task Station** is an automatic, persistent task hub for Claude Code. Every session can attach to a task; tasks survive across sessions and are listed/resumed with `/todo`. Each task **pins to a resumable Claude session** — reopen the exact session behind it (or **re-pin a fresh session to save tokens**). Tasks are **auto-categorised and colour-tinted**, and Task Station is the **hub that launches parallel in-project workers**.

Run `/todo` and Claude renders your board as two tables — **open** first, then recently **closed**. Task numbers are stable ids assigned in creation order, so they look scattered: an old long-running task keeps its low number while newer tasks get higher ones.

**Open**

|   # | Task | Category | Effort | Activity |
| --: | --- | --- | --- | --- |
|  38 | Add dark mode toggle to the settings page | 🩷 [DESIGN] | ▰▰▰▱▱ M | 2h ago |
|  12 | Fix auth token refresh on expired sessions | 🔴 [BUG] | ▰▰▱▱▱ S | yesterday |
|   5 | Build cross-session task tracker | ⚪ [SKILLS] | ▰▰▰▰▱ L | 3d ago |

**Closed**

|   # | Task | Category | Effort | Activity |
| --: | --- | --- | --- | --- |
|  40 | Handle null avatar URLs in the header | 🔴 [BUG] | ▰▰▱▱▱ S | 4h ago |
|  37 | Paginate the activity feed endpoint | 🟢 [FEATURE] | ▰▰▰▱▱ M | 8h ago |
|  33 | Add CSV export to the reports dashboard | 🟢 [FEATURE] | ▰▰▰▰▱ L | yesterday |
|  28 | Pin CI node version and cache dependencies | 🔵 [DEVOPS] | ▰▰▰▱▱ M | 2d ago |
|   9 | Tidy up stale feature flags | ⚫ [GENERAL] | ▰▱▱▱▱ XS | 3d ago |

> … 16 older closed task(s) hidden · show more with `/todo closed N` or `/todo all` · reachable by number: `/todo <n>` or `/done <n>`

**Commands**

- `/todo <number>` — open and resume a task
- `/todo <number> -s` — jump straight into that task's pinned session, in a new window
- `/todo closed [N]` or `/todo all` — see more closed tasks
- `/done` — close the task this session is working on
- `/done <number>` — close any task by its number
- `/task-station:config` — view or change settings; run one-time setup (tint profiles, delegation policy)

Effort runs `▰▱▱▱▱` XS → `▰▰▰▰▰` XL, and each task is colour-tinted by category ([see the taxonomy](CATEGORIES.md)).

## Why Task Station (vs native Tasks)

Claude Code's native **Tasks** are the agent's *internal* scratchpad (stored in `~/.claude/tasks/`, no user-facing list). **Task Station is the human-facing console on top**: a persistent `/todo` dashboard you control, where each task pins to a resumable session you can reopen, auto-categorised + colour-tinted, with parallel worker delegation. They're complementary — native Tasks tracks the agent's steps; Task Station tracks *your* work across sessions.

## Data & privacy

- All task data is stored **locally** under `${CLAUDE_CONFIG_DIR:-~/.claude}/task-station-data/` (one JSON file per task, plus `config.json`).
- **No telemetry, no network calls, nothing is transmitted off your machine.**
- The delegate feature spawns local `claude -p` workers — that's your own Claude usage, no third party.

## What this plugin does / files it touches

### Hooks

Declared in `hooks/hooks.json`, run at your trust level:

- **`SessionStart`** (`hooks/on_session_start.sh`) — maintains the `~/.claude/task-station-engine` symlink; self-registers a status-line segment at `~/.claude/statusline.d/50-task-station.sh`; sets the window title for attached sessions; shows a one-time setup nudge on first run; and — **only if you have opted in** — installs bare command aliases under `~/.claude/commands/`.
- **`UserPromptSubmit`** (`hooks/on_user_prompt.sh`) — per-category terminal tint (when enabled) and injects compact task-tracking guidance into each prompt so Claude knows to attach or create a task.
- **`PostToolUse`** on `Write|Edit|NotebookEdit` (`hooks/on_post_tool.sh`) — fires a one-shot reminder the first time an untracked session edits a file. Part of the optional enforcement gate.
- **`Stop`** (`hooks/on_stop.sh`) — refuses to end the turn while a session has edited files but tracked no task (self-healing, capped at two blocks). The other half of the optional enforcement gate.

### Files and directories created or used

All paths are under your config dir (`${CLAUDE_CONFIG_DIR:-~/.claude}`) unless noted:

| Path | What it is |
|---|---|
| `~/.claude/task-station-data/` | Local task storage: one JSON per task, plus `config.json`, `workers.json`, and `pending-briefs/` |
| `~/.claude/task-station-engine` | Symlink to the plugin's `lib/` — a stable, version-independent handle refreshed every session |
| `~/.claude/statusline.d/50-task-station.sh` | Self-registered status-line segment (harmless if unused) |
| `~/.claude/commands/{todo,done}.md` | **Only if you run `task-station config --bare-cmds on`** (opt-in; marker-guarded, never clobbers a pre-existing command) |
| `~/.zshrc` (tint aliases) | **Only via the explicit `task-station config --tint-profiles` command you run** |
| `~/.claude/CLAUDE.md` (delegation policy block) | **Only via the explicit `task-station config --policy on` command you run** (fenced, 100% reversible with `--policy off`) |

The namespaced `/task-station:todo` and `/task-station:done` commands are registered by the plugin system automatically and always work out of the box. The bare `/todo` and `/done` aliases are **opt-in** — run `task-station config --bare-cmds on` to install them.

## How it works

- **Auto-attach.** On each user message, a `UserPromptSubmit` hook injects
  guidance telling Claude to either **attach** the session to a matching open
  task or **create** a new one. The per-prompt nudge is deliberately **compact**
  (open-task list, a one-line trackability test — concrete work that edits files /
  spans multiple steps, not a question or one-line fix — the attach/create
  commands, and a one-line colour legend); the full rules, TRACK/SKIP examples,
  and colour-picker guidance live in `task-station.py guidance`, fetched on demand, to
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
- **Skip.** `task-station.py skip --session <id>` marks a session intentionally
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
  column (`▰▰▱▱▱ S`) in the list and spelled out in the detail view. Claude sets it
  at `create` time (the auto-attach nudge asks for it); adjust later with
  `task-station.py update --task <n> --effort <xs|s|m|l|xl>`. `--effort` also accepts the
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
  in place. The underlying call is `task-station.py done --task <n|id>`.
- **Resume resolution.** `/todo <n>` resumes the task's **most-recent substantive
  session**, finding it by id across all project buckets and reading the launch
  directory from the transcript itself — so it self-corrects even if the recorded
  cwd was wrong (e.g. you launched from `~` but `cd`'d into a worktree), and a 1-2
  message "just looking" session never displaces real work. It only ever resumes
  one of the task's *own* sessions, and starts fresh rather than risk a different
  task's session. To override the heuristic, **`task-station.py pin --task <n> --session
  <id>`** locks a specific session (PK-style; `unpin` reverts). The printed resume
  one-liner also **re-tints the terminal to the task's colour** — it's prefixed with
  the category's zsh alias (e.g. `green 2>/dev/null; cd … && claude --resume …`), so
  pasting it into a fresh window restores the colour. The prefix is joined with `;`
  and swallows stderr, so it's a silent no-op for anyone who hasn't installed the
  colour aliases — the `cd` + resume always runs. (Omitted entirely when
  tinting is off — `"tint_terminal": false` in `config.json`.)

- **Categories & terminal colours (optional plugin).** If `categories.py` is
  present (it ships with the plugin), every task carries a `color` from a
  taxonomy (bug/red, code-review/orange, devops/blue, design/pink, …); `/todo`
  appends a `<emoji> [TAG]` after each task and prints a legend. Each colour
  name is also a zsh alias that switches the Terminal.app profile, so on attach /
  create / resume Claude runs `zsh -ic '<color>'` to tint the terminal to the
  task's category. **Skills tint immediately:** when a prompt invokes a slash
  command mapped in `SKILL_COLORS` (e.g. `/review` or `/security-review` → orange), the
  `UserPromptSubmit` hook tints the terminal synchronously *before Claude
  responds*, so the colour applies the instant the skill runs. **All of this is
  isolated in `lib/categories.py`** — `task-station.py` imports it defensively and runs
  as a plain, colourless tracker without it. The taxonomy ships as defaults in
  `lib/categories.py`; override or extend it without touching the shipped file
  via `task-station-data/config.json` (survives `/plugin update`) — see
  [`CATEGORIES.md`](CATEGORIES.md). Full taxonomy, wiring, tint modes, and the
  opt-out levels are in [`CATEGORIES.md`](CATEGORIES.md).

There is no auto-close: tasks stay open until you run `/done`. (The Claude Code
harness can't distinguish `/exit` from a crash or window-close, so closing is
kept explicit and deliberate.)

### Resume & re-pin (save tokens)

`/todo <n>` opens a task and resumes its pinned session (the most-recent substantive
one by default). `/todo <n> -s` does the same but jumps into it in a fresh Terminal.app
window rather than continuing in the current one.

The engine pins the most-recent substantive session to each task automatically. You can
**re-pin a new or fresh session to an existing task** using:

```bash
python3 "$HOME/.claude/task-station-engine/task-station.py" pin --task <n> --session <id>
# revert:
python3 "$HOME/.claude/task-station-engine/task-station.py" unpin --task <n>
```

This is the **token-saving move**: when a task's session has accumulated a bloated
context window (hundreds of messages, large file loads), re-pin a fresh session to it
instead of resuming the old one. The task stays linked to the same work — same number,
same history, same category — but resumes into a clean slate that doesn't reload the
stale context. Use `claude --resume <id>` from your shell to reopen the literal original
chat when you need it; Task Station's `/todo <n>` will follow the pin.

## Delegate — in-project workers

Task Station ships a second half in [`lib/delegate/`](lib/delegate/delegate.py): a helper
that spawns an **in-project Claude worker** and links it to a task. The two are
meant to be used **together** — don't run one without the other.

**Why it exists.** A session launched from outside a repo does *not* load that repo's
`./CLAUDE.md`, hooks, project-scoped `.mcp.json`, project-local skills, or
permissions/env — those load only in a `claude` process whose cwd is inside the repo.
`delegate` spawns exactly that process, keeps **one persistent worker per (task, repo)**,
resumes it across turns, and relays the result back.

### Zero-config usage: `--repo`

Point `delegate` at any git repo with `--repo` — no environment setup required:

```bash
# do work in a repo, linked to /todo task 5:
python3 "$HOME/.claude/task-station-engine/delegate/delegate.py" run \
  --repo /path/to/my-repo \
  --seq 5 \
  --task "Add input validation to the login form (src/auth/login.py). Accept criteria: …"

python3 "$HOME/.claude/task-station-engine/delegate/delegate.py" list   # known workers
```

**Prerequisites:** `claude` CLI on PATH, `git`, `python3` (stdlib only). The stable
symlink `~/.claude/task-station-engine` (maintained by the plugin's `SessionStart` hook) means
callers never need to chase a versioned cache path.

### Optional shorthand: `--project` + `TASK_STATION_WORKSPACE_DIRS`

If you keep repos in one or more parent directories, set `TASK_STATION_WORKSPACE_DIRS`
to a `:`-separated (`;` on Windows) list of those directories:

```bash
export TASK_STATION_WORKSPACE_DIRS="$HOME/Projects:$HOME/Work"
```

Then you can pass a short repo name instead of a full path:

```bash
python3 "$HOME/.claude/task-station-engine/delegate/delegate.py" run \
  --project my-repo \
  --task "…"
```

Without `TASK_STATION_WORKSPACE_DIRS`, `--project` errors and tells you to use `--repo`.

### `--seq` task-linking and the Workers section

Pass `--seq <n>` (the `/todo` task number) to link the worker to that task:
- The worker is named `task-station-<seq>-<project>` and keyed `<seq>:<project>` in the
  registry.
- The repo is recorded on the task; `/todo <n>`'s detail view shows a **Workers**
  section with a one-command resume per repo — drop straight into the right in-project
  worker from the task list.
- `--label <slug>` opens a second concurrent worker in the same repo.

For write work (`--worktree`), `--seq` is **auto-inherited** from the calling session's
attached task — you usually don't need to pass it manually. Use `--solo` to opt out for
ad-hoc work unrelated to the current task.

Workers run with `TASK_STATION_SUPPRESS=1`, so the `/todo` hooks stay silent inside
them — tracking is the hub's job, not the worker's.

### `--worktree` for isolation

Pass `--worktree <slug>` to run the worker in a dedicated git worktree (`<repo>-worktrees/<slug>/`),
created off the repo's **auto-detected default branch** (override with `--base <ref>`).
Use a descriptive slug (e.g. a ticket id + short description). The branch name defaults
to the slug; override with `--branch <name>`.

Omit `--worktree` only for read-only workers.

### Resume and persistent workers

One worker per (task, repo) — the same invocation **resumes** the session on the next
turn automatically. The worker's session id is pre-registered before launch, so a
mid-run timeout or kill still leaves the session resumable on disk.

- `delegate.py list` — show all known workers and their resume commands.
- `--fresh` — ignore the saved session and start a new one.
- The saved `dir` in the registry is the source of truth: a resumed worker re-enters
  the exact worktree it was created in. Passing a different `--worktree` on resume is
  refused; use `--fresh` to start over.

The registry lives at `<data_dir>/workers.json` (machine-local, not tracked by the plugin).

### The `delegating-work` skill

The plugin ships `skills/delegating-work/SKILL.md` — a Claude Code skill that teaches
the model *when* and *how* to delegate. After install it is active in every session,
so delegation works out of the box for genuine in-repo work — without invoking it for
every small edit or Q&A.

**Enabling stricter *auto*-delegation.** The skill is intentionally conservative. Teams
with stronger policies — mandatory worktrees for all write work, story/PR gates, a fixed
workspace dir — should layer those rules in their own `~/.claude/CLAUDE.md`. The plugin
ships a ready-to-adapt
[`lib/delegate/POLICY-TEMPLATE.md`](lib/delegate/POLICY-TEMPLATE.md) for exactly this.
Copy it, fill in your specifics, and paste it into your global `CLAUDE.md`. Without it,
`delegate.py` still works when invoked by hand — Claude just won't apply stricter rules
automatically.

### Status bar integration

`task-station.py whoami --session <id> --statusline` prints a ready-to-display, ANSI-colored one-line segment for the session's attached task — `#<seq>  <dot> [TAG]  <title>` — and nothing when the session has no task. Add `--width <N>` to truncate the title so the whole segment fits `N` columns (`0` = no limit). It's self-contained: it carries its own colors and knows nothing about the bar that renders it, so it drops into any status line (tmux, powerline, a custom prompt, or a Claude Code `statusLine` command).

```bash
$ task-station.py whoami --session 5c8edf12 --statusline --width 0
#42  🔵 [DEVOPS]  Wire up the deploy pipeline
```

### Status line (optional)

The plugin maintains a stable symlink `~/.claude/task-station-engine → <plugin>/lib/` (refreshed on every `SessionStart`) so callers outside the plugin context — delegate invocations, the status line — always find the engine without chasing a versioned cache path.

To show the current task in the Claude Code status bar, add one line to `settings.json`:

```json
"statusLine": { "type": "command", "command": "bash ~/.claude/task-station-engine/statusline.sh" }
```

`~/.claude/task-station-engine/statusline.sh` is the self-contained script (`lib/statusline.sh`) exposed through the stable symlink. It reads the session JSON on stdin (as Claude Code passes it) and delegates to `task-station.py whoami --statusline`. No `$CLAUDE_PLUGIN_ROOT` dependency — it works in any context once the symlink exists.

The symlink is written (or re-written) on the first `SessionStart` after install or update, so it self-heals across `/plugin update` without any manual step.

### Storage

One JSON file per task under `<data_dir>/tasks/<uuid>.json`; session→task links
under `<data_dir>/links/`. All writes are atomic. The data directory defaults to
`${CLAUDE_CONFIG_DIR:-~/.claude}/task-station-data/`; set `$TASK_STATION_HOME` to
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
/plugin marketplace add ryanconmeo/task-station
/plugin install task-station
```

That wires the namespaced `/task-station:todo` + `/task-station:done` commands and all four hooks automatically — no
`settings.json` edit required. Task data lands in
`${CLAUDE_CONFIG_DIR:-~/.claude}/task-station-data/` and **survives `/plugin update`**. To also install the bare `/todo` + `/done` aliases, run `task-station config --bare-cmds on`.

`categories.py` ships with the author's colour taxonomy and terminal tinting
**on** by default (macOS only). To adjust without editing the shipped file, drop
a `config.json` in the data directory — see [CATEGORIES.md](CATEGORIES.md)
for the JSON shape and opt-out levels.

**For in-project worker delegation** (optional, but the two halves are meant to be
used together): nothing extra to install — `lib/delegate/delegate.py` ships with
the plugin. To get *auto*-delegation, copy
[`lib/delegate/POLICY-TEMPLATE.md`](lib/delegate/POLICY-TEMPLATE.md)
into your global `~/.claude/CLAUDE.md` and customize the workspace paths. See the
[Delegate](#delegate--in-project-workers) section.

## Install

**Prerequisites:** [Claude Code](https://claude.ai/code), `jq`, `python3` (stdlib only).

    /plugin marketplace add ryanconmeo/task-station
    /plugin install task-station

That wires the namespaced `/task-station:todo` + `/task-station:done` commands and all four hooks automatically — no
`settings.json` edit, no command copy. Task data lives in
`${CLAUDE_CONFIG_DIR:-~/.claude}/task-station-data/` (override with `$TASK_STATION_HOME`)
and **survives `/plugin update`**. To also install the bare `/todo` + `/done` aliases, run `task-station config --bare-cmds on`.

The `PostToolUse` + `Stop` pair is the **enforcement gate** (see [How it works](#how-it-works)): a file edit in an untracked session triggers a one-shot reminder, and the `Stop` hook refuses to end the turn until a task is attached/created (or the session is skipped). Both are included in the plugin by default — remove them from `hooks/hooks.json` if you only want the advisory nudges — but together they're what makes tracking reliable instead of best-effort.

For *auto*-delegation, copy [`lib/delegate/POLICY-TEMPLATE.md`](lib/delegate/POLICY-TEMPLATE.md)
into your global `~/.claude/CLAUDE.md` and customize the workspace paths.

## Configure

All config lives in one file: `${CLAUDE_CONFIG_DIR:-~/.claude}/task-station-data/config.json`. Use the commands below to read and write it — never edit the file directly.

One slash command (run from a Claude Code session):

- **`/task-station:config`** — your *settings* (values the plugin owns in `config.json`) **and** the *doctor + installers* for things outside the plugin (your `CLAUDE.md` policy, Terminal tint profiles), with a status report of what's still unconfigured.

To run from a plain shell instead, the stable engine path is `~/.claude/task-station-engine/task-station.py` (a symlink the `SessionStart` hook keeps current; `$CLAUDE_PLUGIN_ROOT` isn't set in a shell, so use this path):

```bash
python3 "$HOME/.claude/task-station-engine/task-station.py" config     # same as /task-station:config
```

### `task-station config`

With no arguments, prints the unified board: current settings plus a status/doctor report (tint mode + detected terminal, tint-profiles, workspace dirs, whether the delegation policy is installed). Flags:

- `--workspace-dirs <a:b>` — set repo-root directories (`:` separated) for delegate's `--project` shorthand.
- `--categories edit` — prints the `config.json` path so you can open it and customize categories, `skill_colors`, etc.
- `--bare-cmds on|off` — install or remove the bare `/todo` + `/done` aliases.
- `--policy on|off` — adds or removes a 100%-reversible delegation-policy block in your `~/.claude/CLAUDE.md` (fenced, idempotent, hash-checked; `off` refuses if the block was hand-edited).
- `--tint-profiles` — **Terminal.app:** sets profile mode, appends per-category zsh aliases to `~/.zshrc`, and prints the manual steps to create matching Terminal.app profiles. **iTerm2:** no-op (prints "already zero-setup").
- `--data-dir` *(read-only)* — shows the data directory (set via `$TASK_STATION_HOME`).

### Baked defaults and env escapes

These are on by default. Each has a hidden env escape to turn it off — no config menu needed:

| Behavior | Default | Env escape to disable |
|---|---|---|
| Enforcement gate (file-edit → track-or-block) | on | `TASK_STATION_GATE=off` |
| Per-category terminal tint | on | `TASK_STATION_TINT=off` |
| Bare `/todo` + `/done` install | **off** (opt-in) | `TASK_STATION_BARE_CMDS=on` to enable |

**Terminal tint — two modes:**

- **auto** *(default, zero-setup)* — writes a direct escape sequence to set the background colour: iTerm2 uses `SetColors`, Terminal.app uses OSC 11. Works out of the box; no profiles or aliases needed.
- **profile** — runs `zsh -ic '<color>'` to switch Terminal.app profiles via named aliases. Enable with `task-station config --tint-profiles` (iTerm2: no-op, already zero-setup).

Tinting is auto-detected: the engine reads `$TERM_PROGRAM` / `$ITERM_SESSION_ID` to pick iTerm2 vs Terminal.app vs none. The window title `task-station-<seq> · <title>` and `/todo <n> -s` new-window jump are on by default on macOS (auto-detected).

**Bare commands:** `/todo` and `/done` are marker-guarded user-level commands that forward to the engine. They are **not installed by default** — run `task-station config --bare-cmds on` (or set `TASK_STATION_BARE_CMDS=on`) to opt in. The namespaced form `/task-station:todo` and `/task-station:done` always exist regardless and work out of the box.

## Update

```bash
/plugin update task-station
```

Task data in `task-station-data/` is untouched.

## Uninstall

```bash
/plugin uninstall task-station
```

Task data persists in `${CLAUDE_CONFIG_DIR:-~/.claude}/task-station-data/` — delete that
directory manually if you want to remove your task history. The worker registry
(`task-station-data/workers.json`) also lives there; back it up first if you want to keep it.

If you copied the delegation policy into your global `~/.claude/CLAUDE.md`,
delete that block too. Restart Claude Code.

## Files

**`lib/task-station.py`** — the engine: task storage, `/todo` and `/done`, the hooks' logic, plus the `whoami` (incl. the `--statusline` segment provider) and `update` commands.

**`lib/categories.py`** — optional colour-taxonomy + terminal-tint plugin; `task-station.py` runs fine without it. Ships with defaults; users customize via `task-station-data/config.json` without editing this file. See [`CATEGORIES.md`](CATEGORIES.md).

**`lib/paths.py`** — resolves the mutable data directory (`${CLAUDE_CONFIG_DIR:-~/.claude}/task-station-data/`, overridable with `$TASK_STATION_HOME`) and handles legacy migration detection.

**`hooks/on_session_start.sh`** — `SessionStart` hook. Surfaces open tasks (or the attached one) and auto-sets the window title to `task-station-<seq> · <title>`.

**`hooks/on_user_prompt.sh`** — `UserPromptSubmit` hook. Attaches/nudges the session and tints the terminal for skill-mapped prompts.

**`hooks/on_post_tool.sh`** — `PostToolUse(Write|Edit|NotebookEdit)` hook. Fires a one-shot reminder the first time an untracked session edits a file. Half of the optional enforcement gate.

**`hooks/on_stop.sh`** — `Stop` hook. Blocks the turn from ending while a session has edited files but tracked no task (self-healing, capped at two blocks so it can't wedge). The other half of the enforcement gate.

**`hooks/hooks.json`** — plugin hook manifest; declares all four hooks for the plugin system.

**`lib/close-session-window.sh`** — closes the Terminal.app window hosting a session; invoked by `/done`.

**`lib/open-session-window.sh`** — opens a fresh Terminal.app window running the task's resume command; invoked by `/todo <n> -s`.

**`lib/delegate/delegate.py`** — spawns/resumes in-project workers that carry the repo's full machinery (see [Delegate](#delegate--in-project-workers)).

**`lib/delegate/worktree-up.sh`** — creates a git worktree for a new branch; called by `delegate.py` when `--worktree` is used.

**`lib/delegate/POLICY-TEMPLATE.md`** — copy into your global `~/.claude/CLAUDE.md` to layer stricter auto-delegation policy on top of the bundled skill.

**`skills/delegating-work/SKILL.md`** — the bundled Claude Code skill; teaches the model when and how to delegate in-project work. Active in every session after install.

**`.claude-plugin/plugin.json`** — plugin metadata (name, version, author, license).

**`.claude-plugin/marketplace.json`** — marketplace listing metadata.

**`commands/todo.md`**, **`commands/done.md`** — the `/todo` and `/done` slash commands; registered automatically by the plugin.
