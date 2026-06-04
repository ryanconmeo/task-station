# claude-todo

Persistent, cross-session task tracking for Claude Code. Every session attaches
to a single trackable task; tasks survive across sessions and are queryable any
time with `/todo`.

```
Tasks (open first, then by recent activity):  •  /todo <n> = open detail & resume   ·   /done = close current task

OPEN
  1  Build cross-session task tracker       ⚪ [SKILLS]     2h ago
  2  Fix auth refresh bug                   🔴 [BUG]        yesterday

CLOSED
  3  Migrate costbar to v2                  🟤 [MIGRATION]  3d ago

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
- **`/todo`** lists all tasks (open first, then by recent activity).
  **`/todo <n>`** (or a task-id prefix) prints the task's detail and **adopts it
  into the current session** — your next message continues it. If the task was
  closed, opening it reopens it.
- **`/done`** closes the task the current session is working on **and detaches
  the session** from it, so a follow-up message can't silently reopen it. To
  pick the task back up, use `/todo <n>`, which re-attaches and reopens it.

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
`~/.claude/commands/done.md` — copy them there if cloning standalone.

Requires `python3` (stdlib only) and `jq`.
