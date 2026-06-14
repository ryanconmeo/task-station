# Design: Repackage `claude-todo` as a native Claude Code plugin

- **Date:** 2026-06-14
- **Status:** Approved design → implementation plan to follow
- **Branch:** `plugin-rework`
- **Scope decision:** tracker + categories + delegate, one plugin
- **Audience decision:** private now, public-ready later (build portable; don't publish yet)
- **Migration decision:** best-practice — auto-migrate on first run, explicit command as fallback
- **Sequencing:** lands **before** the Obsidian integration (`/todo` task `baf3fdf4`), because that
  feature builds on the relocated state dir and engine path this rework establishes.

## Problem

`claude-todo` today installs as a cloned git repo at `~/.claude/todo/` with a multi-step manual
setup: `cp` the slash commands into `~/.claude/commands/`, hand-merge four hook entries into
`~/.claude/settings.json`, and keep them in sync via a `SessionStart` self-heal. Every path is
hardcoded to `$HOME/.claude/todo/…`, and **mutable state lives inside the repo dir**
(`store/tasks/*.json`, `store/links/`, `pending-briefs/`, `delegate/workers.json`, per-session
`.edited` markers).

Claude Code's native plugin format removes nearly all of that ceremony — one-step
`/plugin install`, declarative `hooks/hooks.json`, auto-discovered `commands/` and `skills/`,
`${CLAUDE_PLUGIN_ROOT}` for portable paths, versioned updates. The catch: a plugin installs into
a **versioned, replace-on-update cache dir**
(`~/.claude/plugins/cache/<marketplace>/claude-todo/<version>/`). Co-locating mutable state with
code — which `todo.py` does today via `STORE = dirname(__file__)/store` (`todo.py:33-35`) — would
**destroy task history on every `/plugin update`**. Relocating state to a stable, version-independent
home is the one hard problem; everything else is mechanical.

## Goals

1. Install/update via `/plugin marketplace add ryanconmeo/claude-todo` → `/plugin install` /
   `/plugin update`, with the clone path kept only as a documented fallback.
2. Hooks wired declaratively (`hooks/hooks.json`), no `settings.json` merge, no command-copy step.
3. Zero hardcoded user paths — code resolves itself via `${CLAUDE_PLUGIN_ROOT}` / `__file__`.
4. Task history and worker registry survive updates and migrate seamlessly from the legacy clone.
5. Category taxonomy / tint config survives updates (user edits no longer wiped).
6. macOS-only behaviour (Terminal.app tint + window control) degrades gracefully off-mac.
7. Retain `/todo` and `/done` and every existing parameter (`/todo <n>`, `/todo <n> -s`,
   `/done`, `/done <n>`) with identical behaviour.

## Non-goals

- Publishing to a public marketplace now (build for it; defer the push).
- Splitting delegate into its own plugin now (noted as the natural public-time split).
- Rewriting the engine (no SQLite, no MCP-server rewrite — the file-per-task, hook-driven design
  is the value and stays).
- The Obsidian integration itself (separate, sequenced after).

## Target architecture

```
claude-todo/                       # GitHub repo doubles as the marketplace
  .claude-plugin/
    plugin.json                    # manifest: name, version, author, license, keywords
    marketplace.json               # lists this plugin so `/plugin marketplace add` works
  commands/
    todo.md  done.md               # auto-discovered; bodies call ${CLAUDE_PLUGIN_ROOT}/lib/todo.py
  hooks/
    hooks.json                     # 4 hooks declared with ${CLAUDE_PLUGIN_ROOT}
    on_session_start.sh on_user_prompt.sh on_post_tool.sh on_stop.sh
  skills/
    using-claude-todo/SKILL.md     # (optional) self-documenting usage skill
  lib/
    todo.py  categories.py
    delegate/delegate.py
    close-session-window.sh
  README.md  CATEGORIES.md  LICENSE
  docs/specs/…                     # this doc + the implementation plan
```

State lives **outside** the plugin, at the data dir (below), holding `store/`, `pending-briefs/`,
`workers.json`, and `config`.

### Component responsibilities

- **`plugin.json` / `marketplace.json`** — identity + discovery. Pure metadata.
- **`hooks/hooks.json`** — declares the four hook bindings (`SessionStart`, `UserPromptSubmit`,
  `PostToolUse(Write|Edit|NotebookEdit)`, `Stop`), each invoking a `${CLAUDE_PLUGIN_ROOT}/hooks/*.sh`.
  Replaces the manual `settings.json` merge.
- **`hooks/*.sh`** — unchanged logic, but every `$HOME/.claude/todo/…` becomes
  `${CLAUDE_PLUGIN_ROOT}/…`, and the SessionStart command-copy block (`on_session_start.sh:14-21`)
  is **deleted** (plugin auto-discovers `commands/`).
- **`lib/todo.py`** — the engine. Gains a **data-dir resolver** (below); `STORE`/`TASKS_DIR`/
  `LINKS_DIR`/`pending-briefs` all route through it instead of `dirname(__file__)`. Reads sibling
  `categories.py` via `__file__` (unchanged). Runs the auto-migration on startup.
- **`lib/categories.py`** — ships the author's taxonomy as **defaults**; merges user overrides
  from the data-dir `config` so edits survive updates.
- **`lib/delegate/delegate.py`** — unchanged behaviour; `workers.json` path routes through the
  data-dir resolver. Still auto-inherits `--seq` from the attached task.
- **`commands/{todo,done}.md`** — bodies swap `$HOME/.claude/todo/todo.py` →
  `${CLAUDE_PLUGIN_ROOT}/lib/todo.py`. Front-matter and all argument handling unchanged.

### Data-dir resolver (the linchpin)

A single function, used everywhere state is read/written:

```
CLAUDE_TODO_HOME                       # explicit override, if set
  else ${CLAUDE_CONFIG_DIR:-$HOME/.claude}/todo-data
  else (only if CLAUDE_CONFIG_DIR unset and XDG_STATE_HOME set) $XDG_STATE_HOME/claude-todo
```

`CLAUDE_CONFIG_DIR` is a first-class Claude Code primitive (users relocate `~/.claude` with it), so
anchoring to it is correct. `~/.claude/todo-data/` is discoverable and idiomatic. The XDG branch is
a courtesy fallback. The directory is created on demand; all writes stay atomic (unchanged).

### Category config externalization

`categories.py` keeps `CATEGORIES`, `TINT_TERMINAL`, `SKILL_COLORS` as **defaults**, then at import
merges a user `config` file from the data dir (e.g. `todo-data/config` — TOML or a small JSON/py
mapping; format chosen at implementation). Absent config → today's exact behaviour. This is the only
behavioural refactor in `categories.py`; the public functions are untouched.

### macOS gating

`open-session-window.sh` / `close-session-window.sh` and the `zsh -ic '<color>'` tint suggestions
are Terminal.app/macOS-specific. Gate them behind an OS check (`uname`/`$OSTYPE`); off-mac, the tint
prefix is omitted and `-s` falls back to printing the resume one-liner (the existing fallback path).
No new behaviour — just don't emit mac-only commands on non-mac.

## Migration (auto + explicit)

**Auto (default, runs from the engine on startup):** if the resolved data dir has no `store/` and a
legacy `~/.claude/todo/store` exists, **copy** (not move) it into the data dir once, plus
`delegate/workers.json` and `pending-briefs/`, and drop a `.migrated` marker so it never re-runs.
Copy-not-move leaves the old clone intact as a backup. Idempotent and silent — the user's open tasks
simply appear in the new install.

**Explicit (`todo.py migrate`, fallback/repair):** same operation, runnable on demand, supports a
`--from <path>` and re-run after the marker is cleared — for custom data dirs, inspection, or recovery.

This is the practical-vs-complete answer: auto covers ~99% with zero action; the command exists for
the long tail.

## Compatibility & rollout

- The four legacy `settings.json` hook entries pointing at `~/.claude/todo/*.sh` must be **removed**
  on cutover (README uninstall step), or they'll double-fire alongside the plugin's hooks.
- Legacy `~/.claude/commands/{todo,done}.md` copies should be removed so the plugin's auto-discovered
  commands are the only ones.
- `delegate/POLICY-TEMPLATE.md` stays a **documented opt-in**: auto-delegation needs a rule in the
  user's global `~/.claude/CLAUDE.md`, which a plugin cannot write. Honest limitation, unchanged.

## Risks

1. **`/plugin update` wipes the cache dir** — the entire reason for the data-dir move. Mitigation:
   verification step explicitly simulates an update and confirms `todo-data/` survives.
2. **Double-firing hooks during transition** — if a user installs the plugin without removing the
   legacy `settings.json` entries. Mitigation: explicit uninstall-old step + a one-line note the
   SessionStart hook can emit if it detects the legacy install alongside the plugin.
3. **`${CLAUDE_PLUGIN_ROOT}` in command bodies** — verified working (a shipped plugin invokes
   `python3 - "${CLAUDE_PLUGIN_ROOT}/assets/…"` from a command), so low risk.
4. **macOS coupling leaking off-mac** — mitigated by the gating goal; must be tested on a
   non-mac path (or simulated by forcing the OS check).

## Alternatives considered (rated 1–10)

Scored for fit with *private-now/public-ready, one plugin, best-practice migration.*

**A — Packaging model:** A1 native plugin + marketplace **(10, chosen)** · A2 thin wrapper keeping
settings.json merge (4) · A3 polish the clone, no plugin (3) · A4 MCP/CLI rewrite (2).

**B — State location:** B1 `${CLAUDE_CONFIG_DIR:-~/.claude}/todo-data` + `$CLAUDE_TODO_HOME` override
**(10, chosen)** · B2 strict XDG (7, used as fallback) · B4 SQLite (5) · B3 keep in plugin dir (1).

**C — Path resolution:** C1 `${CLAUDE_PLUGIN_ROOT}` + `__file__` **(10, chosen)** · C3 symlink shim
(4) · C2 keep hardcoded paths (2).

**D — Category config survival:** D1 shipped defaults + data-dir override **(9, chosen)** · D3 env var
→ external py file (6) · D2 edit in place, wiped on update (3).

**E — Migration:** E1 idempotent auto-migrate on first run **(9, chosen default)** · E2 explicit
`migrate` command (7, kept as fallback) · E4 fresh start (5) · E3 manual cp docs (4).

**F — Delegate packaging:** F1 one plugin, `workers.json`→data dir, policy as opt-in **(8, chosen)**
· F2 two plugins core+addon (7, revisit at publish) · F3 drop delegate (2).

## Success criteria

- `/plugin marketplace add` + `/plugin install` wires hooks and commands with no manual
  `settings.json` edit and no command copy.
- `/todo`, `/todo <n>`, `/todo <n> -s`, `/done`, `/done <n>` behave identically to today.
- After install on this machine, the 6 existing open tasks appear via auto-migration.
- A simulated `/plugin update` preserves all task data and worker registry.
- No `$HOME/.claude/todo` literal remains in shipped code paths.
- Off-mac, hooks and commands run without erroring on Terminal.app-only commands.
