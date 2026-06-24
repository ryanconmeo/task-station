# Changelog

All notable changes to Task Station are documented here. This project adheres to
[Semantic Versioning](https://semver.org).

## [1.13.0] — 2026-06-24

### Added
- **Composable status-line convention ([docs/STATUSLINE.md](docs/STATUSLINE.md)).**
  A small, vendor-neutral convention for composing multiple segments under Claude
  Code's single `statusLine.command`: **providers** are executables in
  `${CLAUDE_CONFIG_DIR:-~/.claude}/statusline.d/` that speak the statusLine
  stdin-JSON contract (empty output / non-zero exit ⇒ skipped); **hosts** own
  `statusLine.command`, run every provider with the JSON on stdin, and join the
  non-empty segments. Reference-implemented here, intended for extraction into a
  neutral repo + an upstream feature request.
- **`config --statusline on` (opt-in, default off).** Installs a self-sufficient
  task-station status-bar **host** (it embeds the ~30-line compose routine —
  `lib/statusline-host.sh` — and needs no external conductor) when nothing else
  owns the bar, registers a segment **provider** (`statusline.d/50-task-station.sh`)
  either way, and **never clobbers** an existing/foreign `statusLine`. Writes to
  `settings.json` are backed up first and fully reversible (`--statusline off`
  removes only what we own). Provider + host honor `CLAUDE_STATUSLINE_WIDTH` /
  `CLAUDE_STATUSLINE_SEP`.

### Changed
- **The `statusline.d/` provider drop-in is now written only when `--statusline`
  is on** (was unconditional in the SessionStart hook), so task-station no longer
  writes into a user's `statusline.d/` unbidden.

## [1.12.0] — 2026-06-23

### Added
- **`guidance` now emits the full command reference.** Alongside the existing
  track/attach/skip how-to, `task-station guidance` prints a compact reference for
  every subcommand — purpose plus key flags, the lifecycle (open ○ → active ● →
  closed ✕), and the ref forms — so the model-facing guidance is the single source
  of truth for the command set instead of each session reinventing a command.
- **Hidden `delete --task <ref>` maintenance command.** A real hard-delete that
  removes a single task's record and detaches any session linked to it. Hidden from
  `--help`, the config board, and the README (documented only in `guidance`): the
  lifecycle is normally close-not-delete — prefer `done`/close.

### Fixed
- **Category tint now applies IMMEDIATELY on create/attach/recategorize.** Assigning
  a colour (via `create`, `attach`, `update --color`, or guaranteed-tracking
  auto-create) previously only tinted the terminal on the *following* prompt, since
  nothing emitted the escape at assign time. The colour is now emitted best-effort
  to the originating TTY the moment it is set; if the TTY can't be resolved or
  tinting is off it is a silent no-op and the per-prompt hook tints as before.

## [1.11.0] — 2026-06-23

### Added
- **`--tint [on|off]` (default on).** A persisted config flag for the full-palette
  terminal tint, so tinting can be controlled without an env var. The
  `TASK_STATION_TINT` env var still wins over the config setting (on/off/1/0/
  true/false). Every Python tint emitter now consults this flag.
- **`--reset` factory-reset action (confirm-gated).** Bare `task-station config
  --reset` explains what it will do and resets nothing; `--reset confirm` wipes the
  board-managed settings in `config.json` back to defaults. **Tasks are never
  touched** (`tasks.db` is a separate file), and externally-installed integrations
  (bare command files, the Desktop bridge entry, the `CLAUDE.md` delegation block)
  are *reported* with their off-commands rather than silently removed.

### Changed
- **`task-station config` board redesigned.** One stanza per setting — an aligned
  `<flag> <value> <options>` line, then the description on its own line with the
  factory default shown inline as `(default: X)` — replacing the old wrapped-text
  blob. The former separate `status`, `--workspace-dirs`, and `--data-dir` blocks
  are folded into the single list; `--tint-theme` now shows just the appearance
  mode (`auto`/`dark`/`light`), not the resolved theme name; the `category
  overrides` row is relabelled `--category-overrides`. No more `* = default`
  markers — the value column always shows the current value.

## [1.10.0] — 2026-06-23

### Added
- **`--guaranteed-tracking` (opt-in, default off).** Hook-side deterministic
  create+attach of a *provisional* task on a fresh, unattached, non-skipped
  session — the `UserPromptSubmit` hook tracks the topic itself instead of only
  nudging the model. **Fold-don't-fork**: a similar open task is attached to (with
  the prompt filed as a note) rather than forked into a sibling. **Auto-GC**: a
  provisional task that's never engaged is deleted when the session is skipped or
  closed, so pure Q&A leaves no litter. Engagement (update title/summary/colour,
  file edit, folded note) sheds the provisional flag. Default off → the
  conservative install behaves exactly as the firmer nudge.

### Changed
- **`--policy` renamed to `--strict-delegation`** (hidden `--policy` alias kept for
  back-compat; the managed `CLAUDE.md` block markers are unchanged so blocks
  installed under the old name remain detectable/removable). Config board and
  README clarify available-vs-enforced delegation.
- **Firmer untracked-session nudge.** The default nudge now directs tracking even
  for plain questions and no longer advertises `skip` as an easy out; the
  escalation block still offers `skip` for genuinely throwaway sessions.

### Fixed
- **Intent detector no longer false-positives "create" on meta-questions about
  tasks.** Added past-tense/perfect/existential interrogatives (`did`, `have you`,
  `has`, `is there`, `was`, `were`, `didn't`, …) to the question guard, so
  "did you open a new task for this?" is correctly read as a question, not a
  create imperative.

## [1.9.1] — 2026-06-22

### Fixed
- **`/todo <n> -s` no longer re-tints the invoking window.** A session-jump opens
  the task in a NEW window and must leave the window you typed it in untouched.
  `_jump_one` was attaching (`set_link`) the **invoking** session to the jumped
  task; combined with the 1.9.0 prompt-tint fallback (which repaints the current
  window to its attached task's colour on any non-skill prompt), the invoking
  window wrongly repainted to the jumped task's colour. Now `-s` attaches only the
  **target** session — the resumed recorded session or the freshly-minted one — so
  only the new window carries the jumped task's tint. Belt-and-suspenders:
  `cmd_prompt_tint` also skips the attached-task fallback for a `/todo … -s` /
  `--session` prompt, so even the immediate jump prompt never repaints the current
  window. Plain `/todo <n>` (non-jump) still attaches the invoking session and
  repaints the current window, unchanged.

### Documentation
- **Comprehensive README refresh.** Re-audited every section against the shipped
  code — the appearance-aware theme system, auto-enabling categories, the full
  config flag table, the `/todo` board render, and the MCP bridge tools. Corrected
  the version badge (now `1.9.1`), the `--theme` default (`sands`), and normalised
  the open-status glyph to `○` for consistency with the board.

## [1.9.0] — 2026-06-22

### Added
- **Appearance-aware theme system.** The 12-category taxonomy (dot/[TAG]/label) is
  unchanged; colour now comes from a **THEME**, and every theme has **two variants —
  `dark` and `light`** — each a full per-category palette (background, foreground,
  bold, cursor, selection + the 16 ANSI colours). One theme ships, **`sands`**, with a
  **Dark Sands** (muted) variant and a **Light Sands** (vibrant) variant. The **OS
  appearance picks the variant**, so out of the box the terminal follows the OS — dark
  mode → Dark Sands, light mode → Light Sands — re-resolved every prompt/attach.
  Variants display as `{Dark|Light} {Theme}`. Tinting uses standard OSC escapes (OSC
  11/10/12, OSC 4 for the 16 ANSI slots, OSC 17 for selection, plus an iTerm-only
  `SetColors=bold`).
- **`config --tint-theme auto|dark|light`** (default `auto`) — the appearance control:
  which variant renders. `auto` detects the OS (macOS `AppleInterfaceStyle`;
  non-macOS/failure → dark); `dark`/`light` force it.
- **`config --theme`** — verb-first grammar for the active theme (mainly for custom
  themes, since one ships):
  - `config --theme` (or `list`) lists themes + active + each theme's variant labels +
    the current tint-theme and resolved variant.
  - `config --theme <name>` selects a theme.
  - `config --theme save <name>` snapshots **both variants** (dark + light) of the
    active theme into `config.json` as a fully self-contained theme (independent of the
    current appearance); rejects reserved names `save·edit·preview·list·show·default`
    and names not matching `^[a-z0-9][a-z0-9_-]*$`.
  - `config --theme edit` prints the `config.json` path.
  - `config --theme preview` renders a self-contained HTML gallery — **both variants**
    of every theme — to `<data_dir>/themes-preview.html`.
  - `--theme`, `--tint-theme`, and the resolved variant (e.g. `auto → Dark Sands`) all
    appear on the `config` board.
- **User themes survive updates.** `config.json` `themes` deep-merge over the shipped
  THEMES, **variant-nested** (theme → `dark`|`light` → category → field); brand-new
  named themes are allowed (a missing variant falls back to `sands`) — so
  customisations persist across `/plugin update`.
- **`tools/render_palettes.py`** — the data-driven preview generator (HTML to stdout
  or `--out`), rendering both variants of each theme; backs `config --theme preview`.

### Changed
- **In-session re-tint.** When a prompt invokes no skill, `prompt-tint` now falls
  back to the **attached task's** category colour (like the on-attach tint), so a
  plain `/todo <n>` repaints the current window to the active task's tint. Honours
  `TASK_STATION_TINT=off` and `TINT_TERMINAL`.

## [1.8.0] — 2026-06-21

### Added
- **Auto-enable categories — the board grows itself.** The categoriser now always
  considers the **full 12-slot taxonomy**, so it can pick the most accurate
  category even if that slot isn't on the board. When `auto_categories` is on (the
  default) and a task is assigned (via `create --color`, `attach --color`,
  `update --color`, or the Desktop bridge's create tool) to a category not in the
  enabled set, that slot is **enabled automatically** — persisted to
  `enabled_categories` and surfaced with a one-line `enabled new category 🔵 [INFRA]`
  notice. The enabled set governs **display only**; assignment can target any slot.
- **`--auto-categories on|off`** (plus `--auto-categories-get`) and the env escape
  **`TASK_STATION_AUTO_CATEGORIES=off`** to freeze the enabled set. With it off,
  assignment no longer grows the board and the legend/picker restrict to enabled
  slots (the prior behaviour). Shown as a row on the `config` board.

### Changed
- **Lean CORE default.** When `enabled_categories` is unconfigured, the enabled set
  is now **CORE = 🔴 BUG · 🟢 FEATURE · ⚫ GENERAL** (was: all 12). A fresh board
  starts small and fills in via auto-enable as you categorise. `⚫ GENERAL` stays
  permanent. The `config` board summary reads `N/12 (default: CORE)` / `N/12
  (custom)`.

### Removed
- **Category presets are gone.** The `PRESETS` map, `preset_keys()`, the
  `config --categories preset <name>` subcommand (and its `minimal|web|data|ops|full`
  argument), and the preset listing on the `--categories` board were removed in
  favour of the lean default + auto-enable. `--categories` (show set), `--enable`,
  and `--disable` are unchanged.

## [1.7.0] — 2026-06-21

### Added
- **Full-palette escape tint — every category now tints the WHOLE terminal, not
  just the background.** Each of the 12 category slots bakes in a complete
  **Sands** palette (background, foreground, bold, cursor, selection, and all 16
  ANSI colors), shipped as the new defaults. `categories.tint_escape` emits it as
  standard OSC escapes — background (OSC 11), foreground (OSC 10), cursor
  (OSC 12), the 16 ANSI colors (OSC 4), selection (OSC 17) — plus one iTerm-only
  extra for the bold colour (`1337;SetColors=bold`). iTerm2 and Terminal.app both
  honor it; still zero-setup, no profiles or shell aliases. A category that
  defines only a background still emits just that (back-compat for minimal
  taxonomies), and a user override that sets only `{tag,label}` inherits the full
  palette from its slot.
- **Tint on attach/resume, not just first prompt.** The SessionStart hook now
  emits the tint escape for the attached task's category (new `session-tint`
  command), so a resumed/attached window tints immediately.
- **Width-aware, wrap-safe `task-station config` board (release prep).** The
  no-arg board is now a single unified view: short-valued settings render as a
  4-column aligned grid (SETTING / VALUE / OPTIONS / WHAT IT DOES) whose first
  three columns are sized to their widest cell per render, while the description
  column takes the remaining terminal width and wraps with a hanging indent under
  WHAT IT DOES — so long descriptions never break the grid. Long PATH-valued
  settings (`--workspace-dirs`, `--data-dir`) print as their own full-width
  two-line blocks below the grid, and the store path drops to its own line when
  it would overflow. Alignment holds at COLUMNS=60/80/120.
- **`term.width()`** — terminal columns via `shutil.get_terminal_size()` (honors
  `$COLUMNS`, falls back to 80, clamped to a minimum of 60). Pure stdlib.

### Changed
- **Category taxonomy rebalance (slots/keys/palettes unchanged).** Five category
  slots were renamed/clarified for everyday work — only `tag`/`label` (and one
  dot) changed; colour keys, hexes and palettes are untouched, so existing tasks
  and `config.json` overrides keep working: `purple` SPECIAL → **RESEARCH**
  ("spikes / investigation"); `gold` GOLD/reserved → **DOCS** 📖
  ("documentation, writing") — gold is now a real category, no longer a hidden
  "reserved" slot; `blue` DEVOPS → **INFRA** ("CI/CD, pipelines, cloud, deploy");
  `brown` DATABASE → **DATA** ("databases, schemas, ETL, migrations"); `silver`
  AI CONFIG → **TOOLING** ("dev/AI tooling, config, env"). Presets and the
  enabled-set default are unchanged (presets key on colour, not tag). The
  legend/picker no longer special-case a "reserved" label.
- **One config board, no duplication.** The separate `setup.status()` block
  printed after the no-arg board is gone; its facts (tint + terminal, policy,
  desktop-bridge) are folded into a compact `status` section at the bottom of the
  same board, keeping the actionable hints (`--policy on`, …). The tint line now
  reads `escape (full palette) · terminal <iterm|terminal|none>`. `setup.status()`
  itself is unchanged and still used by the install flow.

### Removed (breaking)
- **Profile-switching tint mode is gone.** `task-station config --tint-profiles`,
  the `tint_mode == "profile"` path, `setup.install_tint_profiles()`,
  `categories.tint_command()`, the bundled `lib/install-tint-profiles.sh`, and the
  `zsh -ic '<color>'` alias hints (resume-command prefix, task-detail line,
  prompt-context/guidance) are all removed. Tinting is now always the direct
  full-palette escape. No `~/.zshrc` aliases or Terminal.app profiles are written
  or referenced anymore. If you previously ran `--tint-profiles`, the generated
  aliases are now inert and can be deleted by hand.

## [1.6.4] — 2026-06-20

### Changed
- **Accurate Claude Desktop docs — plugin commands + connector tools, on-demand
  only.** The README now states the confirmed reality: Task Station works in
  Desktop two ways — as a **plugin** (slash commands like `/todo` in Chat) and
  as a **connector** (`config --desktop-bridge on` → conversational
  create/list/track tools + the `todo` prompt + task resources). Desktop runs
  plugin *commands* but **not** *hooks*, so Desktop tracking is **on-demand**
  (type `/todo` or say "track this"), **not** automatic; added a
  surface×capability matrix and noted Desktop Custom Instructions as the only
  proactive lever.

### Removed
- **The inert `initialize` `instructions` field (added in 1.6.3).** Claude
  Desktop silently drops MCP server `instructions`, so the 1.6.3 auto-track
  nudge never reached the model. Removed it; `capabilities` / `serverInfo` /
  `protocolVersion` and all tools/prompts/resources are unchanged.

## [1.6.3] — 2026-06-20

### Added
- **Claude Desktop now auto-tracks substantive topics as tasks — the Desktop
  analog of the CLI's prompt-context auto-track.** Desktop has no
  `UserPromptSubmit` hook, so the CLI's "track every substantive topic as an
  open(◦) task" can't fire there. The MCP `initialize` response now carries an
  **`instructions`** string (which clients fold into the model's context)
  telling Desktop's Claude: when the user raises substantive work, first
  `list_tasks` and `add_note` onto a matching open task (fold — don't
  duplicate), else `create_task` with a clear title, 1–3 sentence summary,
  category, and a `source` identifying the Desktop conversation; skip trivial
  one-offs and casual chat. It's a model-driven nudge, not a hard hook, and
  only fires on substantive work — so the board doesn't flood. Tools, prompts,
  and resources are unchanged.

## [1.6.2] — 2026-06-20

### Changed
- **In Claude Desktop Chat, the task board now renders verbatim as a table.**
  Previously, when `list_tasks` / the `todo` prompt returned the Markdown board,
  Chat paraphrased it into prose (nothing told it otherwise — unlike the CLI
  `/todo` skill, which says "print verbatim"). The `list_tasks` tool result and
  the `todo` prompt content now **prepend a short instruction line** — `Display
  this task board to the user EXACTLY as written below … render the tables
  verbatim, do not summarize, reword, or re-rank.` — ahead of the board, so Chat
  shows the same ◦/● tables as the CLI. The board BODY is unchanged: still
  byte-equal to the CLI `render --format md`.

### Added
- **The `todo` prompt is discoverable in Desktop's prompt picker.** `prompts/list`
  now gives `todo` a human title ("Task Station: todo") and description ("Show
  your task-station board (open · active · closed)"). Each tool also carries a
  crisp, action-leading description (`list_tasks` "Show the user's task board",
  `create_task` "Create / track a new open(◦) task", …) so Claude picks the right
  one from natural language.

## [1.6.1] — 2026-06-20

### Changed
- **The Desktop bridge now points Claude Desktop at a stable, self-resolving
  launcher instead of the volatile engine symlink.** Previously `--desktop-bridge
  on` wired Desktop to `~/.claude/task-station-engine/mcp_server.py`, but that
  symlink is re-pointed by *every* CLI session to *that session's* plugin version
  — so an older session (e.g. a 1.2.2 version with no `mcp_server.py`) could
  silently break Desktop. `on` now generates `<data_dir>/mcp-launcher.py` (a
  stable, version-independent path) and points Desktop at `python3
  <data_dir>/mcp-launcher.py`. At run time the launcher resolves the **installed**
  task-station version itself — reading `plugins/installed_plugins.json` →
  `task-station@ryanconmeo` `installPath` → `<installPath>/lib/mcp_server.py`,
  falling back to the **highest** `plugins/cache/ryanconmeo/task-station/*/lib/mcp_server.py`
  that exists — and `os.execv`s it with the same interpreter, passing stdio
  straight through. Robust across `/plugin update` and concurrent CLI sessions.
  The launcher is stdlib-only (system `python3` 3.9+) and is regenerated on every
  `on` (idempotent); `off` removes only our config entry and leaves the (inert)
  launcher file in place.

### Added
- **`TASK_STATION_DESKTOP_CONFIG` override.** When set, the `--desktop-bridge` CLI
  path resolves the Desktop config from that path instead of the real
  `~/Library/Application Support/Claude/claude_desktop_config.json` — so tests and
  safe manual checks never touch the live Desktop config.

## [1.6.0] — 2026-06-20

### Changed
- **The Desktop bridge is now DEPENDENCY-FREE and self-installing.** `lib/mcp_server.py`
  no longer needs the `mcp`/FastMCP SDK (which required Python 3.10+ and a
  `pip install`). The MCP protocol is hand-rolled in the standard library
  (`json` + `sys` only): a minimal **stdio JSON-RPC 2.0 server** that runs on the
  **system `python3` (3.9+)** with zero install. It handles `initialize`
  (advertising `tools`/`prompts`/`resources` + `protocolVersion` + `serverInfo`),
  `notifications/initialized`, `ping`, `tools/list`, `tools/call`, `prompts/list`
  + `prompts/get` (the `todo` board), and `resources/list` + `resources/read`
  (`task://<seq>` → full detail); unknown methods return JSON-RPC `-32601` and a
  malformed line never crashes the loop. The five stdlib logic fns
  (`_list_tasks`/`_create_task`/`_get_task`/`_set_status`/`_add_note`) are reused
  verbatim — only the FastMCP transport was replaced. There is **no `mcp` import
  anywhere** in the codebase.

### Added
- **`task-station config --desktop-bridge on|off`** — a self-installer that wires
  the bridge into Claude Desktop with no manual JSON. `on` locates (or creates)
  `~/Library/Application Support/Claude/claude_desktop_config.json`, backs it up
  (`.bak-desktop-bridge`), and **merges** a `task-station` server entry
  (`command: python3`, `args: [<~/.claude/task-station-engine/mcp_server.py>]`)
  without clobbering other servers — idempotent, then prompts to restart Desktop.
  `off` removes only our entry. The no-arg `config` view shows the bridge status
  (installed? path?).

## [1.5.0] — 2026-06-20

### Added
- **Desktop bridge — an MCP server over the SHARED store.** Claude Desktop (and
  any MCP client) can now create / read / update tasks in the *same* local
  `tasks.db` the CLI uses — one store, two front doors. New `lib/mcp_server.py`
  drives the existing engine (`paths.py` + `store.py` + `task-station.py`), so
  store paths, seq numbering, lifecycle rules, and the `--format md` render are
  reused verbatim — no forked logic. WAL is already on, so concurrent Desktop +
  CLI access is safe.
  - **Tools:** `list_tasks` (the Markdown board, byte-for-byte the CLI render),
    `create_task` (makes an `open (◦)` task; `category`/`effort`/`source`),
    `get_task` (full detail incl. the source link), `set_status`
    (open → active → closed), `add_note` (timestamped activity-log entry).
  - **Prompt:** `todo` — the rendered board, the Desktop analog of `/todo`.
  - **Resources:** each task at `task://<seq>` returns its full detail, so a task
    can be attached to a Desktop conversation via the + menu.
  - **Source-conversation link.** `create_task(..., source=…)` records the
    originating Desktop conversation ref/URL on the task; `get_task` surfaces it
    — the Desktop ↔ Code provenance link.
  - **`mcp` is an OPTIONAL, server-only dependency.** The tool logic is plain
    stdlib; the FastMCP wrapper is lazily imported only inside `main()`. The core
    plugin and the whole test suite stay stdlib-only — you only need
    `pip install mcp` to *run* the bridge. Wire it up via the stable
    `~/.claude/task-station-engine/mcp_server.py` symlink (survives
    `/plugin update`) — see the README "Desktop bridge (MCP)" section.

## [1.4.0] — 2026-06-20

### Added
- **Three-state task `status` — `open (◦)` → `active (●)` → `closed`.** The lifecycle
  is now ONE field: a topic you merely raise starts `open` and shows on the board
  immediately as `◦`; it graduates to `active` (`●`) when work actually starts; `/done`
  closes it. A leading single-width glyph renders at the very front of every not-closed
  `/todo` row — ASCII list, Markdown table (`#` cell), and the detail view — distinct
  from the category emoji, with a `◦ open · ● active` legend. Closed tasks keep their
  own section and mute the glyph. `sorted_tasks` lists not-closed (open + active) first
  by recent activity, then closed.
- **Auto-promote `open → active` when work begins** (idempotent; never resurrects a
  closed task), on any of:
  - `delegate … --worktree` for the task (write work starts);
  - a **file edit** in an attached session — `hooks/on_post_tool.sh` (PostToolUse)
    flips an attached open task to active;
  - manual **`status --task <ref> [open|active]`** (no value → report the status;
    closing is via `/done`);
  - **`create --active`** to start a task active.
- **Auto-track as `open` from the first prompt** — replaces the old "pure Q&A → stay
  silent" behaviour. For an unattached, non-skipped session the model now creates an
  `open` task for the topic (model-driven: good title + category). Skipped sessions
  still stay silent.
- **Grouping — "fold, don't fork".** Before creating a new task the model scans the
  board (open + active) and, if the prompt continues an existing task, **attaches and
  appends the prompt as a note** instead of spawning a sibling — so related questions
  across sessions accumulate under one task. New **`attach --note '<text>'`** appends a
  timestamped entry to the task's activity log.

### Changed
- **`status` is a single three-value field** (`open`/`active`/`closed`); everywhere the
  code treated `status == "open"` as "on the board / not done" now means "not closed"
  (`open` or `active`). `/done` closes from open or active; reopening a closed task
  returns it to `open`. Back-compat: pre-existing `open`/`closed` tasks read unchanged
  (a missing status reads as `open`); no data migration.
- `cmd_prompt_context` / `commands/todo.md` / `guidance` guidance rewritten around
  track-as-open + fold-don't-fork (was: attach only on concrete work, else silent).

## [1.3.0] — 2026-06-20

### Fixed
- **`-s` no longer resumes the wrong conversation.** When a task was spun off from a
  busy session — or you jumped into it from the very session it was created in — the
  most-recent-substantive heuristic could pick **the current conversation** as the
  resume target (the `cands = … or cands` fallback re-added the session that had just
  been excluded). The current session is now excluded **hard**: if no other live
  candidate remains, `-s` **fresh-starts** instead of tainting into the conversation
  you jumped from.
- **Skipped sessions are excluded from `-s` candidacy.** A session marked untracked
  (`skip`, link `__skip__`) is never offered as a resume target, even with a live
  transcript.

### Added
- **`-s` fresh-start auto-attaches.** When there's no valid session to resume, the
  jump path mints a brand-new session id, pre-binds it to the task (link + hub
  `session_meta`), and emits `cd <dir> && claude --session-id <uuid>` — so the new
  window **auto-attaches** to the task on launch (SessionStart sees the link) rather
  than opening a bare, untracked `claude`. `resume_command` stays **pure** for the
  `/todo <n>` display render (no uuid minted per render); the mint happens only in the
  jump / pin paths via `fresh_resume_command(task)`.
- **`create --no-attach`** — create a task with **empty `sessions[]`** and no
  session→task link (the clean "spin off a task for later" primitive). `/todo <n> -s`
  then fresh-starts a clean session. `--session` is now optional.
- **`create` from a substantive tracked session defaults to `--no-attach`.** Running
  `create` with a `--session` that is itself a real, tracked working conversation
  (≥ 3 messages) no longer binds that busy conversation as the new task's resume
  target — it defaults to no-attach and warns. Pass **`--attach`** to force the old
  bind-this-session behaviour.
- **`detach --session <s> [--task <t>]`** — remove a session from a task's
  `sessions[]`/`session_meta`, clear `pinned_session` if it pointed at it, and clear
  the session→task link. `--task` selects the task; without it, the session's linked
  task is used. Idempotent.
- **`pin --new [--task <t>]`** — pin an **unborn** session: mints a uuid, records it
  (and links it), and `/todo` emits `claude --session-id <uuid>` so opening it
  *becomes* the task's pinned session — bypassing the stale-pin "ignored when no live
  transcript" guard for this intentional case.

## [1.2.2] — 2026-06-20

### Added
- **Auto terminal title `#<seq>: <title>` on attach.** Once a session is attached to
  a task, the terminal tab/window title is set to `#<seq>: <title>` (e.g.
  `#214: task-station: token-efficiency + SQLite store`) — a literal `#`, no
  `task-station` prefix. A new `prompt-title` emitter (run by the `UserPromptSubmit`
  hook every prompt) writes the OSC title escape (`\033]0;…\007`) to the originating
  TTY via the same `origin-tty.sh` rail the tint uses, so the title sets on the first
  prompt after attaching and self-heals each prompt. Unattached / skipped sessions
  are left untouched.
- **`config --title on|off`** (default on) toggles the auto title, mirroring the tint's
  env escape — `TASK_STATION_TITLE=off` (or `config --title off`) suppresses it.

### Changed
- **SessionStart session name reformatted to `#<seq>: <title>`** (was
  `task-station-<seq> · <title>`), matching the new terminal title.

## [1.2.1] — 2026-06-20

### Changed
- **Swapped the `white` ↔ `silver` category slots.** 🎨 **DESIGN** now lives on the
  `white` slot (→ **White Sands** profile + white hex) and 🪩 **AI CONFIG** on the
  `silver` slot (→ **Silver Sands** profile + silver hex). Each slot keeps its own
  `key`/alias/profile and tint hex; only the dot/tag/label moved between them, so
  the two categories simply trade profiles — no `tint`-override field. `CORE` and
  the `web` preset were re-pointed (`white`↔`silver`) so AI CONFIG stays core and
  both AI CONFIG + DESIGN stay in `web`. The Claude-tooling `SKILL_COLORS` entry
  now maps to `silver`, so those skills keep tinting AI CONFIG (Silver Sands).
  - **Stored tasks are re-keyed `white`↔`silver` on upgrade** so they follow their
    category to the new slot (a live-data migration handled separately from this
    change, which ships only the new defaults, tests, and docs).

## [1.2.0] — 2026-06-20

### Changed
- **Redesigned shipped category defaults.** `yellow` tag `FIX PR` → **`FIX`**;
  `white` `SKILLS`/⚪ → **`AI CONFIG`/🪩** (disco ball), label "AI tooling & config";
  and the `pink`↔`silver` roles swapped so **`pink` = `PERSONAL`/🩷** and
  **`silver` = `DESIGN`/🎨** (palette). Tint hexes are kept *by slot* (pink keeps
  its pink tint, silver its neutral grey), so existing tints stay sensible.
  - **Stored tasks re-render with the new labels.** A task's stored `color` key is
    **unchanged** — only its *rendered* tag/label/emoji updates. So tasks coloured
    `pink` now show 🩷 `[PERSONAL]`, `silver` → 🎨 `[DESIGN]`, `white` → 🪩
    `[AI CONFIG]`, and `yellow` → 🟡 `[FIX]`. No data migration; nothing on disk
    changes.

### Added
- **Slot-determines-emoji.** The dot is now *canonical per colour slot* — you pick
  the colour, the colour determines the icon. A category override / new category
  needs only `tag` + `label`; the `dot` (and tint hexes) are inherited from the
  slot automatically. An explicit `dot` is still honoured for power users.
- **Seeded-but-removable enabled set.** A new `enabled_categories` config key
  controls which slots are "on" — the legend, auto-classification nudge, and picker
  consider only enabled categories. Unconfigured ⇒ the full set (back-compat).
  **⚫ GENERAL is permanent** — always enabled, cannot be disabled.
- **Category presets.** `config --categories preset <minimal|web|data|ops|full>`
  applies a named enabled-set. Every preset contains the universal core
  (`BUG`, `AI CONFIG`, `PERSONAL`, `GENERAL`). `config --categories` (no arg) shows
  the current enabled set + available presets. `config --enable <key>` /
  `--disable <key>` toggle individual slots (disabling `GENERAL` is refused).

## [1.1.0] — 2026-06-19

### Changed
- **Repo enrichment is now OPT-IN per repo (behavior change).** Previously
  `repos --refresh` sent each repo's README + file tree to a model (Haiku) by
  default. Now a repo's content reaches the model **only** when its manifest
  `enrich` flag is `true`, and that flag **defaults to `false`** for every repo.
  A normal `repos --refresh` therefore sends **nothing** off-machine — it fills
  `summary` deterministically (README first paragraph) plus the existing
  stack/status/path detection. `--no-llm` still forces the deterministic path even
  for `enrich:true` repos.
- **Deterministic refreshes preserve existing summaries.** A deterministic refresh
  no longer overwrites a non-empty `summary`/`keywords` (model- or override-derived)
  with the README paragraph; it only fills repos that lack one. Force regeneration
  with the new `--re-summarize`.

### Added
- **Auto-maintained include/exclude manifest** at `task-station-data/repos.config.json`,
  a map keyed by repo name of `{ index: bool=true, enrich: bool=false }`.
  `repos --refresh` reconciles it: newly-discovered repos are added with safe
  defaults; vanished repos are pruned. It is the single surface where every
  discovered repo name appears, so you never type a name from memory — just flip
  flags. Only `index:true` repos reach `repos.md`/`repos.json`; only `enrich:true`
  repos are eligible for model egress.
- **Toggle commands (no JSON editing):** `repos include <name>` / `repos exclude <name>`
  set `index`; `repos enrich <name> [on|off]` sets `enrich`; `repos config` prints the
  full manifest. Names or paths are accepted; unknown names get a clear message.
- **`.task-station-ignore` marker file** at a repo root fully excludes that repo from
  discovery/index (as if `index:false`), regardless of the manifest — a repo-owner
  self-exclude that travels with the repo.
- **First-run onboarding on `/repos`** (not `/todo`): `repos --detect-roots` proposes
  candidate roots (`~/Workspace`, `~/Workspace-Other`, plus any `~` dir with ≥2 git
  repos); `commands/repos.md` walks you through confirming and persisting them with
  `repos --set-roots <p1,p2,...>`, reassuring that enrichment is off by default.
  `commands/todo.md` gains a single subtle one-line pointer to `/repos`.
- **Egress transparency + hygiene:** `repos --refresh` prints exactly which repos are
  having content sent (`enriching (sending README+tree NAMES): …`); `--dry-run` reports
  what *would* be sent without sending. The enrichment input is bounded to repo name,
  ado_project, stack, README top (~80 lines), and a `git ls-files` **name** sketch —
  arbitrary file **contents** are never read, and a denylist guard keeps secret-bearing
  names (`.env`, `*.pem`, `*.key`, `secrets*`, `credentials*`, `.npmrc`, …) out of the
  prompt entirely.

## [1.0.11] — 2026-06-19

### Fixed
- **Exclude prose/markup-ambiguous extensions from stack detection.** The
  Linguist-derived `lib/stack_map.py` kept only `type: programming`, so `.md`
  (claimed by Markdown=prose AND GCC Machine Description=programming) mapped to
  `gcc-machine-description` — polluting every repo with a `README.md`. The
  generator now parses ALL languages with their `type` and drops any extension
  a prose/markup/data language also claims (`.md`/`.rst`/`.txt`/`.json`/`.yaml`/
  `.xml`/…), UNLESS a curated programming language owns it (so `.ts`/`.tsx`/`.rs`,
  which XML lists incidentally, survive). Remaining programming-only collisions
  resolve via a small tie-break dict (`.h`→`c`, `.m`→`objective-c`).
- **Collapse the TSX/JSX variants** onto the ergonomic labels via the alias
  overlay (`TSX`→`typescript`, `JSX`→`node`) so `tsx` no longer appears alongside
  `typescript`. Correct niche detections are kept (e.g. `.com`→
  `digital-command-language`). `EXT_TO_STACK` drops from 954 to 905 extensions;
  the generator stays deterministic and stdlib-only.

## [1.0.10] — 2026-06-19

### Changed
- **Stack detection is now GitHub-Linguist-derived.** The repo index's hand-rolled
  ~18-entry extension→stack list is replaced by `lib/stack_map.py`, a generated map
  (`EXT_TO_STACK` + `FILENAME_TO_STACK`, ~950 extensions) distilled from GitHub
  Linguist's `languages.yml` — the data behind GitHub's per-repo language bar.
  Coverage jumps from a handful of stacks to the full programming-language long tail
  (Swift, Kotlin, Ruby, PHP, Scala, …) while the ergonomic labels the tool already
  uses are preserved via an alias overlay (`python`/`node`/`dotnet`/`sql`/`typescript`/
  `go`/`rust`/`terraform`/`docker`). Swift repos (`.swift`) are now detected.
  - The combination logic is unchanged — the `git ls-files` histogram + threshold,
    the flyway / github-actions / terraform config signals, and root manifests all
    still apply; only the extension lookup got vastly wider.
  - `lib/stack_map.py` is committed and pure stdlib (plain dict literals, no runtime
    YAML, no imports). Regenerate with `python3 tools/gen_stack_map.py`. The source
    `languages.yml` is vendored locally but gitignored (MIT-licensed, not committed).

## [1.0.9] — 2026-06-19

### Added
- **Repo index for hub task routing** — a hub `claude` session launched from `~`
  can't auto-load anything inside a repo, so `/repos` gives it an on-demand, hub-side
  map of the repos under your workspace roots to route a fuzzy task to the right
  repo(s) at delegation time. `/repos` / `/repos show` print the index, `/repos
  <term>` ranks repos by token overlap (name/keywords/domain/stack/ado_project/path),
  `/repos --refresh [--force] [--quiet]` rescans, and `/repos --json` emits the
  structured list. Backed by new `lib/repo_index.py`.
  - Repo cards are **fully auto-filled** — no manual overrides needed (overrides remain an
    optional escape hatch).
  - Deterministic discovery (no model): per repo it derives name, abs path, `origin`
    remote, `ado_project` (Azure DevOps `…/_git/` project or GitHub `owner/repo`), and
    `status` (`active`/`stale`/`unknown` from the last commit vs `REPO_STALE_MONTHS`,
    default 6).
  - **Stack detected by CONTENT, not just root manifests**: a `git ls-files` extension
    histogram (`.py`→python, `.cs`→dotnet, `.sql`→sql, `.ts`→typescript, `.go`→go,
    `.tf`→terraform, …, kept above a small threshold or if dominant) **unioned** with
    config/tooling signals (`Dockerfile`→docker, `.github/workflows/`→github-actions,
    flyway config / `*__*.sql` migrations→flyway, `*.tf`→terraform) and the root manifests.
    SQL/Flyway and manifest-less repos now get a real stack (e.g. ConnxLandingZone→`sql,
    flyway`; a `lib/`-only repo→`python,shell`).
  - **`summary` + `keywords` are auto-filled by a fingerprint-gated, best-effort model call**
    that **degrades gracefully**. Each repo carries a `fingerprint` =
    `sha1(remote + sorted top-level entries + sha1(README) + sha1(each root manifest))[:12]`
    that moves only on identity/structure change, not on ordinary commits. On `--refresh`
    the model (cheap Haiku via the headless `claude -p … --output-format json` CLI) is
    invoked **only** for a repo that is new or whose fingerprint changed **and** has no
    override summary; results are cached in `<data_dir>/.repos-cache.json`, so steady-state
    refreshes make **zero** model calls. If the call fails for any reason (CLI absent, no
    network, timeout, bad JSON), it falls back to a **deterministic** README-derived summary
    + keywords — the index always builds and never raises out of the command.
  - **`/repos --refresh --no-llm`** (and the `repo_enrich` config toggle / `TASK_STATION_REPO_ENRICH=off`,
    default ON) forces the deterministic-only path.
  - **Precedence for summary/keywords: override > model > deterministic-fallback.**
    Hand-authored prose (`summary`/`keywords`/`domain`, plus a `status` override) lives in
    `<data_dir>/repos.overrides.json` keyed by repo name — overrides **win** and **survive**
    every regeneration; discovery never writes them.
  - The index lives next to the task store at `<data_dir>/repos.{md,json}` (+ the
    `.repos-cache.json` enrichment cache) — **not** in `tasks.db` (repos aren't tasks) and
    **not** as per-repo committed files. Discovery roots come from `--workspace-dirs` /
    `TASK_STATION_WORKSPACE_DIRS`, defaulting to `~/Workspace` + `~/Workspace-Other`.
  - The `delegating-work` skill gains a "resolve the target repo" step that uses the
    index when the target repo is ambiguous — on-demand only, no SessionStart injection.
  - Forward-compatible for scale: `match()` already doubles as a stage-1 top-K pre-filter,
    and the fingerprint cache already avoids redundant model work (a future `--refresh`
    debounce is the only remaining additive piece).

## [1.0.8] — 2026-06-19

### Changed
- Storage is now a single indexed SQLite database (`store/tasks.db`, WAL mode) instead
  of one JSON file per task plus per-session link files. Listing, counting, and the
  per-prompt tracked-session check are indexed queries, so they stay fast as the board
  grows rather than scanning every task file on each hook invocation. Falls back to the
  JSON-file store if `sqlite3` is unavailable (stdlib, so effectively never).
- A fresh install starts directly on SQLite — there is no migration step baked into the
  plugin (new users have nothing to migrate).

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
