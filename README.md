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
Tasks (open first, then by recent activity):  •  /todo <n> = open detail & resume   ·   /done = close current task

OPEN
  1  Build cross-session task tracker       ⚪ [SKILLS]     ▆ L   2h ago
  2  Fix auth refresh bug                   🔴 [BUG]        ▃ S   yesterday

CLOSED
  3  Migrate costbar to v2                  🟤 [MIGRATION]  █ XL  3d ago

Effort:  ▁ XS  ▃ S  ▅ M  ▆ L  █ XL
Legend: 🔴 [BUG] bug · 🟠 [REVIEW] code review · 🟢 [VOLT] coding for Volt · 🔵 [DEVOPS] devops · 🩷 [DESIGN] design · ⚪ [SKILLS] skills and memories · 🟤 [MIGRATION] legacy data migration for Volt · …
```

## How it works

- **Auto-attach.** On each user message, a `UserPromptSubmit` hook injects
  guidance telling Claude to either **attach** the session to a matching open
  task or **create** a new one. The nudge spells out a concrete test for what
  counts as trackable (a concrete task that edits files / spans multiple steps,
  not a question or one-line fix) so the decision isn't left to a vague "skip
  trivial questions". When Claude attaches or creates a task it announces it in
  one short line (e.g. "📋 Tracking this as a new task: …"); after that the nudge
  goes silent.
- **Miss escalation.** Each message that goes by without the session attaching
  bumps a per-session counter; after a few unattached messages the nudge
  escalates ("N messages in and still untracked — attach now, or `skip`"). This
  closes the feedback loop so a real task can't silently stay untracked.
- **Skip.** `todo.py skip --session <id>` marks a session intentionally
  untracked (e.g. a pure Q&A session); the nudge then stays silent for it.
  Attaching to or creating a task later resumes tracking.
- **Create dedup.** `create` refuses to make a near-duplicate of an existing
  open task (title overlap by Jaccard or containment) and points at the match to
  `attach` instead; pass `--force` to override.
- **One task per session.** `store/links/<session_id>` records the attachment.
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
- **`/done`** closes the task the current session is working on **and detaches
  the session** from it, so a follow-up message can't silently reopen it. To
  pick the task back up, use `/todo <n>`, which re-attaches and reopens it.
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
  `TINT_TERMINAL = False`.)

- **Categories & terminal colours (optional plugin).** If `categories.py` is
  present, every task carries a `color` from a taxonomy (bug/red,
  code-review/orange, devops/blue, design/pink, …); `/todo` appends a
  `<emoji> [TAG]` after each task and prints a legend. Each colour name is also a
  zsh alias that switches the Terminal.app profile, so on attach / create /
  resume Claude runs `zsh -ic '<color>'` to tint the terminal to the task's
  category. **Skills tint immediately:** when a prompt invokes a slash command
  mapped in `SKILL_COLORS` (e.g. `/volt:review-pr-auto` → orange), the
  `UserPromptSubmit` hook tints the terminal synchronously *before Claude
  responds*, so the colour applies the instant the skill runs. **All of this is
  isolated in `categories.py`** — `todo.py` imports it
  defensively and runs as a plain, colourless tracker without it. If you like the
  tags but lack the profile aliases, keep the file and set `TINT_TERMINAL = False`
  to drop just the tint suggestions. Full taxonomy, wiring, and the three opt-out
  levels are in [`CATEGORIES.md`](CATEGORIES.md).

There is no auto-close: tasks stay open until you run `/done`. (The Claude Code
harness can't distinguish `/exit` from a crash or window-close, so closing is
kept explicit and deliberate.)

## Delegate — in-project workers

`claude-todo` ships a second half in [`delegate/`](delegate/delegate.py): a helper
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
python3 ~/.claude/todo/delegate/delegate.py run --project Volt --seq 38 \
  --task "<self-contained instructions>"
python3 ~/.claude/todo/delegate/delegate.py list        # known workers
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

The registry lives at `delegate/workers.json` (machine-local, git-ignored). Used
without `--seq`, `delegate` still works standalone (one worker per repo, unnamed and
unlinked).

**Enabling *auto*-delegation.** `delegate.py` is the *how*; the *when* — the rule
that makes Claude reach for it on its own — lives in your global `~/.claude/CLAUDE.md`
(loaded every session). This repo ships a ready-to-adapt
[`delegate/POLICY-TEMPLATE.md`](delegate/POLICY-TEMPLATE.md): copy it into your
`~/.claude/CLAUDE.md` and swap the placeholder workspace paths/project names for your
own. Without it, `delegate.py` still works when you invoke it by hand — Claude just
won't know *when* to use it automatically.

### Storage

One JSON file per task under `store/tasks/<uuid>.json`; session→task links under
`store/links/`. All writes are atomic. `store/` is git-ignored — task data is
private and machine-local.

### Limitations (Claude Code harness)

- `/todo` is a numbered text list, not an arrow-key picker — slash-command bash
  has no interactive TTY, so tools like `fzf` can't render.
- "Resume" adopts a task into your **current** session; it can't jump back into
  a different past chat transcript. To reopen the literal original chat, use
  `claude --resume <session-id>` from your shell.

## If you're Claude and someone asked you to install this

Run these commands:

```bash
git clone https://github.com/ryanconmeo/claude-todo "$HOME/.claude/todo"
```

The clone ships `categories.py` with the author's colour taxonomy and terminal
tinting **on**. It is an optional plugin — adjust it to taste:

```bash
# Don't want categories/colours at all → plain tracker:
rm "$HOME/.claude/todo/categories.py"
# Want the tags but you don't have <Color> Sands Terminal profiles + aliases →
# keep the file and turn off tinting:
#   edit categories.py and set  TINT_TERMINAL = False
# Want your own categories → edit the CATEGORIES dict in categories.py.
```

If `~/.claude/` is a git repo with an allowlist-style `.gitignore` (`*` plus
`!file` entries), the `todo/` subdirectory is already ignored — for parity with
the other modules you may still add it under the "their own git repos" section:

```bash
if [ -d "$HOME/.claude/.git" ]; then
  grep -qxF 'todo/' "$HOME/.claude/.gitignore" 2>/dev/null || echo 'todo/' >> "$HOME/.claude/.gitignore"
fi
```

Then merge the following into `~/.claude/settings.json`. Use the `update-config`
skill if available — otherwise show the user this snippet and ask them to add it
manually, then restart Claude Code.

**The `hooks` arrays must be merged, not replaced.** If `UserPromptSubmit` or
`SessionStart` hooks already exist from other modules, append these entries to
the existing arrays rather than overwriting them.

```json
"hooks": {
  "UserPromptSubmit": [
    { "matcher": "", "hooks": [
      { "type": "command", "command": "bash $HOME/.claude/todo/on_user_prompt.sh" }
    ]}
  ],
  "SessionStart": [
    { "matcher": "", "hooks": [
      { "type": "command", "command": "bash $HOME/.claude/todo/on_session_start.sh" }
    ]}
  ]
}
```

The `/todo` and `/done` commands live in `~/.claude/commands/todo.md` and
`~/.claude/commands/done.md`; the clone ships copies under `commands/` — copy them
into `~/.claude/commands/` if cloning standalone.

**For in-project worker delegation** (optional, but the two halves are meant to be
used together): nothing extra to install — `delegate/delegate.py` ships with the
clone. To get *auto*-delegation, copy [`delegate/POLICY-TEMPLATE.md`](delegate/POLICY-TEMPLATE.md)
into your global `~/.claude/CLAUDE.md` and customize the workspace paths. See the
[Delegate](#delegate--in-project-workers) section.

## Install

**Prerequisites:** [Claude Code](https://claude.ai/code), `git`, `jq`, `python3` (stdlib only).

```bash
git clone https://github.com/ryanconmeo/claude-todo "$HOME/.claude/todo"
cp "$HOME/.claude/todo/commands/"{todo,done}.md "$HOME/.claude/commands/"
```

If `~/.claude/` is a git repo with a permissive `.gitignore`, append `todo/` to it:

```bash
if [ -d "$HOME/.claude/.git" ]; then
  grep -qxF 'todo/' "$HOME/.claude/.gitignore" 2>/dev/null || echo 'todo/' >> "$HOME/.claude/.gitignore"
fi
```

Merge the `settings.json` hooks snippet shown above, then restart Claude Code. For
*auto*-delegation, also copy [`delegate/POLICY-TEMPLATE.md`](delegate/POLICY-TEMPLATE.md)
into your global `~/.claude/CLAUDE.md` and customize the workspace paths.

**No git?** Fetch the files with curl instead:

```bash
mkdir -p "$HOME/.claude/todo/delegate" "$HOME/.claude/commands" && cd "$HOME/.claude/todo"
base=https://raw.githubusercontent.com/ryanconmeo/claude-todo/main
for f in todo.py categories.py on_session_start.sh on_user_prompt.sh close-session-window.sh CATEGORIES.md; do
  curl -fsSL "$base/$f" -o "$f"
done
curl -fsSL "$base/delegate/delegate.py"        -o delegate/delegate.py
curl -fsSL "$base/delegate/POLICY-TEMPLATE.md" -o delegate/POLICY-TEMPLATE.md
curl -fsSL "$base/commands/todo.md" -o "$HOME/.claude/commands/todo.md"
curl -fsSL "$base/commands/done.md" -o "$HOME/.claude/commands/done.md"
chmod +x on_session_start.sh on_user_prompt.sh close-session-window.sh
```

## Update

```bash
cd "$HOME/.claude/todo" && git pull
```

## Uninstall

```bash
rm -rf "$HOME/.claude/todo"        # repo + delegate + task data (store/) + worker registry
rm -f  "$HOME/.claude/commands/todo.md" "$HOME/.claude/commands/done.md"
```

Then, in `~/.claude/settings.json`, remove **just** claude-todo's two hook entries —
the `UserPromptSubmit` entry that runs `todo/on_user_prompt.sh` and the `SessionStart`
entry that runs `todo/on_session_start.sh` — leaving any other modules' entries in
those arrays intact. If you copied the delegation policy into your global
`~/.claude/CLAUDE.md`, delete that block too. Restart Claude Code.

> Removing the repo also deletes your task history (`store/`) and the worker registry
> (`delegate/workers.json`). Back them up first if you want to keep them.

## Files

**`todo.py`** — the engine: task storage, `/todo` and `/done`, the hooks' logic, plus the `whoami` and `update` commands.

**`categories.py`** — optional colour-taxonomy + terminal-tint plugin; `todo.py` runs fine without it. See [`CATEGORIES.md`](CATEGORIES.md).

**`on_session_start.sh`** — `SessionStart` hook. Surfaces open tasks (or the attached one) and auto-sets the window title to `todo-<seq> · <title>`.

**`on_user_prompt.sh`** — `UserPromptSubmit` hook. Attaches/nudges the session and tints the terminal for skill-mapped prompts.

**`close-session-window.sh`** — closes the Terminal.app window hosting a session; invoked by `/done`.

**`delegate/delegate.py`** — spawns/resumes in-project workers that carry the repo's full machinery (see [Delegate](#delegate--in-project-workers)).

**`delegate/POLICY-TEMPLATE.md`** — copy into your global `~/.claude/CLAUDE.md` to enable *auto*-delegation.

**`commands/todo.md`**, **`commands/done.md`** — the `/todo` and `/done` slash commands; copy into `~/.claude/commands/`.
