# Task Station

> A persistent task hub for Claude Code. Every task is a **resumable, colour‑tinted session** — auto‑categorised on a self‑growing board, re‑pinnable, tinted to an OS‑aware theme, with parallel in‑project worker delegation and a Claude Desktop bridge.

<p>
  <img alt="version" src="https://img.shields.io/badge/version-1.14.0-blue">
  <img alt="license" src="https://img.shields.io/badge/license-MIT-green">
  <img alt="Claude Code plugin" src="https://img.shields.io/badge/Claude%20Code-plugin-da7756">
  <img alt="CI" src="https://github.com/ryanconmeo/task-station/actions/workflows/ci.yml/badge.svg">
</p>

Claude Code forgets. Close a session and the thread of *what you were doing* is gone — Claude's native Tasks are ephemeral, per‑conversation, and vanish on exit. **Task Station is the durable layer on top:** a board that survives `/plugin update`, where each entry remembers the exact session that holds its context and resumes it with one command — while your terminal quietly tints to the category of whatever you're working on.

<!-- DEMO: replace with an asciinema/GIF of `/todo` → tinted terminal → resume across sessions.
     Drop the file at docs/media/demo.gif and uncomment:
<p align="center"><img src="docs/media/demo.gif" alt="Task Station demo" width="800"></p>
-->

Running `/todo` prints your board — rendered exactly as it appears in the terminal:

**Open**

|  | # | Task | Category | Effort | Activity |
|:-:|--:|------|----------|--------|----------|
| ● | 142 | Fix OAuth redirect loop on Safari | 🔴 [BUG] | `▰▰▱▱▱ S` | just now |
| ○ | 138 | Ship the dark-mode toggle ⧉2 | 🟢 [FEATURE] | `▰▰▰▱▱ M` | 2h ago |
| ○ | 131 | Address review feedback on PR 284 | 🟡 [FIX] | `▰▰▱▱▱ S` | 5h ago |
| ○ | 119 | Migrate billing schema to Postgres | 🟤 [DATA] | `▰▰▰▰▱ L` | 1d ago |
| ○ | 88 | Publish task-station to the marketplace | 🪩 [TOOLING] | `▰▰▰▱▱ M` | 3d ago |

**Closed**

|  | # | Task | Category | Effort | Activity |
|:-:|--:|------|----------|--------|----------|
| ✕ | 134 | Add retry/backoff to webhook dispatch | 🔵 [INFRA] | `▰▰▱▱▱ S` | 1d ago |
| ✕ | 127 | Rework the settings page layout | 🎨 [DESIGN] | `▰▰▰▱▱ M` | 2d ago |

*… 42 older closed task(s) hidden — show more with `/todo closed N` or `/todo all`.*

*● active · ○ open · ✕ closed*

**Commands**

```
/todo                   show the board
/todo <n>               open & resume a task
/todo <n1, n2, …> -s    jump into task session(s), in a new window
/todo closed [N]        list recent closed (default 20)
/todo all               show every task (all open + closed)
/done                   close the current task
/done <n1, n2, …>       close tasks by number
/task-station:config    open settings

<n> a task number  ·  <n1, n2, …> one or more  ·  [N] optional count
```

## Why Task Station

- **Tasks that outlive the session.** State lives outside the plugin cache, so `/plugin update` never wipes your board or history.
- **One‑command resume.** `/todo 286` reopens the task *and* the session that holds its context — cwd auto‑corrected from the transcript, never tainting your current conversation. `/todo 286 -s` jumps straight into it in a fresh window.
- **Your terminal as ambient state.** Each category owns a full colour palette from an **OS‑aware theme** — Dark Sands in dark mode, Light Sands in light mode, switching with your appearance — and the terminal tints the instant a skill runs (`/review` → orange) so you always know what you're in.
- **Never lose untracked work.** Edit a file and the task auto‑promotes to *active*; a Stop‑gate reminds you before a turn ends with untracked edits.
- **Delegate into your repos.** Spin up crash‑safe, worktree‑isolated workers that run inside a target repo with its own `CLAUDE.md`, hooks, MCP servers and permissions — one persistent worker per (task, repo).
- **Code ↔ Desktop, one board.** A dependency‑free MCP bridge shares a single task store between Claude Code and Claude Desktop. Create a task in a Desktop chat; it's there in the CLI, and vice‑versa.
- **Private by default.** Everything stays on your machine. No telemetry; the version check and repo enrichment are opt‑in and send no task data.

## Install

```text
/plugin marketplace add ryanconmeo/task-station
/plugin install task-station@ryanconmeo
```

Requires the `python3` that ships with macOS/Linux (3.9+) — **no pip, no dependencies**. To mirror your board into Claude Desktop, see [Claude Desktop bridge](#claude-desktop-bridge).

## Quickstart

```text
/todo                      # show the board (empty at first)
> add login bug to my tasks
  → Task [a1b2c3d4] created: "Fix login redirect bug"  ○ open
> /todo                    # it's tracked, with a category + effort
> /done                    # close the current task when finished
```

That's the loop. Tasks are created from natural language ("track this", "make a task for…"), auto‑categorised and colour‑tinted, and every session that touches one is remembered for resume. Re‑open any task later with `/todo <n>`.

## Commands & components

### Slash commands
| Command | What it does |
|---|---|
| `/task-station:todo` (or `/todo`) | Show the board; `/todo <n>` open & resume a task; `/todo <n> -s` jump into its session in a new window; `/todo closed [N]` / `all` list closed. |
| `/task-station:done` (or `/done`) | Close the current task; `/done <n[,n…]>` close by number. |
| `/task-station:repos` (or `/repos`) | Set up repo routing for delegating tasks to the right project. |
| `/task-station:config` | Settings + status board (categories, theme & tint, delegation, Desktop bridge, status bar). |

> Bare `/todo` and `/done` aliases are opt‑in — enable with `task-station config --bare-cmds on`. The namespaced forms always work.

> Running `task-station guidance` prints the **full command reference** — every subcommand with its purpose and key flags, the model‑facing source of truth for the command set. Point an agent (or yourself) there instead of reinventing a command.

### Skills
| Skill | Triggers when |
|---|---|
| `delegating-work` | A task targets a specific repo and needs that repo's own machinery — spawns an in‑project worker. |

### Hooks (the automation)
| Hook | Effect |
|---|---|
| `SessionStart` | Announces the attached task / lists open tasks; tints the terminal at attach. |
| `UserPromptSubmit` | Tints the instant a known skill runs; auto‑titles the tab `#<seq>: <title>`; keeps the task fresh or nudges you to track. |
| `PostToolUse` (edits) | Auto‑promotes an attached task to *active*; nudges if you're editing untracked. |
| `Stop` | Blocks ending a turn with untracked edits (self‑healing, capped). |

### MCP bridge tools (Claude Desktop)
`list_tasks` · `create_task` · `get_task` · `set_status` · `add_note` — plus a `todo` prompt and `task://<seq>` resources. All backed by the same local store as the CLI.

## Categories & terminal tint

Twelve colour categories, each with a tag and a full terminal palette:

| | Category | | Category | | Category |
|---|---|---|---|---|---|
| 🔴 | BUG | 🟢 | FEATURE | 🩷 | PERSONAL |
| 🟠 | REVIEW | 🔵 | INFRA | 🎨 | DESIGN |
| 🟡 | FIX | 🟣 | RESEARCH | 🪩 | TOOLING |
| ⚫ | GENERAL | 🟤 | DATA | 📖 | DOCS |

The **board starts lean and grows by itself.** On a fresh install only the **CORE** set (`🔴 BUG`, `🟢 FEATURE`, `⚫ GENERAL`) shows — you're not staring at twelve slots you don't use yet. The full taxonomy is always available to the categoriser, and **auto-enable** does the rest: the first time a task is assigned to a category that isn't on the board, that slot is enabled automatically (with a one-line `enabled new category 🔵 [INFRA]` notice) and stays on the board thereafter. So the board converges on exactly the categories *you* actually use, with zero setup. Prefer a fixed set? `config --auto-categories off` (or `TASK_STATION_AUTO_CATEGORIES=off`) freezes it; then toggle slots by hand with `config --enable`/`--disable`. Customise tags, labels and palettes in `config.json` — your edits survive plugin updates. See [CATEGORIES.md](CATEGORIES.md).

Tinting is **zero‑setup** on iTerm2 and Apple Terminal: Task Station writes OSC escapes directly to the originating window — background, foreground, cursor, the full 16‑colour ANSI palette, and (on iTerm) bold. No profiles or shell aliases required.

## Themes

A **theme** is a full‑palette colour set with **two variants — `dark` and `light`** — and the **OS appearance picks which one renders**. For every category each variant supplies a complete terminal palette (background, foreground, bold, cursor, selection, and the 16 ANSI colours). One theme ships — **`sands`** — with a **Dark Sands** (muted) variant and a **Light Sands** (vibrant) variant. So out of the box the terminal **follows the OS**: dark mode → Dark Sands, light mode → Light Sands, switching automatically (re‑resolved every prompt/attach).

```text
task-station config --tint-theme            # auto (default) — follow the OS appearance
task-station config --tint-theme dark       # force the dark variant (Dark Sands)
task-station config --tint-theme light      # force the light variant (Light Sands)

task-station config --theme                 # list themes + the active one + resolved variant
task-station config --theme save my-theme   # snapshot BOTH variants as a new self-contained theme
task-station config --theme <name>          # select a (custom) theme
task-station config --theme edit            # print config.json to hand-edit themes
task-station config --theme preview         # render an HTML gallery (both variants of each theme)
```

`--tint-theme` controls the **appearance** (which variant renders); `--theme` selects **which theme** supplies the palettes (mainly for custom themes — one theme ships). A theme's variants display as **"{Dark\|Light} {Theme}"** — e.g. `Dark Sands` / `Light Sands`, or `Dark Ocean` / `Light Ocean` for a custom `ocean`.

**Customisations survive updates.** Any colour you change — and any brand‑new named theme you add — lives under `themes` in `config.json`, **variant‑nested** (`theme → dark|light → category → field`), and is **deep‑merged** over the shipped theme, so it persists across `/plugin update`. A custom theme that defines only one variant falls back to `sands` for the other. `--theme save` captures **both** variants (fully self‑contained, independent of the current appearance). Reserved names (`save`, `edit`, `preview`, `list`, `show`, `default`) can't be saved; theme names must match `^[a-z0-9][a-z0-9_-]*$`. See [CATEGORIES.md](CATEGORIES.md).

## In‑project delegation

A hub session launched from `~` can't load a repo's `CLAUDE.md`, hooks, MCP servers or permissions — those only load inside the repo. Task Station delegates the work to a `claude` worker spawned *in* the repo:

- **Worktree‑isolated** — every mutation runs in a sibling `<repo>-worktrees/<slug>`, never your main checkout.
- **Crash‑safe** — the worker's session id is registered *before* launch, so a timeout or kill never loses the conversation; the next call resumes it.
- **One worker per (task, repo)** — resume one‑liners show up in the task's detail view.

**Available vs enforced.** Delegation is *always available* — the delegating‑work skill and `delegate.py` ship with the plugin and work regardless of any flag, decided ad hoc per task. `config --strict-delegation on` *enforces* it: it writes a standing, reversible managed block into your `CLAUDE.md` so Claude delegates by **default** and follows the guardrails (worktree isolation, self‑contained briefs, require a story/PR). Off (the default) leaves delegation available but unenforced.

Pair it with the privacy‑first repo index (`/repos`). Details in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Fan-out hints (ultracode)

Some tasks are big enough to want **breadth** — many subagents reading, analysing, designing or reviewing in parallel. Claude Code's built‑in **`ultracode`** multi‑agent mode does exactly that, and Task Station knows which of your tasks would benefit. On a **fan‑out‑worthy** task — effort **L/XL**, or **RESEARCH / REVIEW / DATA** at **M+** — the task's detail recap and its SessionStart line surface a one‑line advisory suggesting you run it with `ultracode`. The **human opts in** by typing the keyword; Task Station never fires orchestration itself, and trivial work (XS/S, plain questions) never triggers the hint.

**ultracode and delegation are different tools, on purpose.** ultracode is **human‑opt‑in breadth** for **read / analyze / design / review** — hub subagents with no repo `CLAUDE.md`, hooks or build env. **Delegation** (above) is the sanctioned path for **repo writes** — a worktree worker off the repo's base branch, with a story + PR. So when ultracode is active on a worthy task, Task Station steers the model to keep every repo mutation on the delegation path and never edit/build/test in a workflow subagent. Opt out any time with `config --ultracode-hints off` (default on). Design notes: [docs/superpowers/specs/2026-06-24-ultracode-fanout-hints-design.md](docs/superpowers/specs/2026-06-24-ultracode-fanout-hints-design.md).

## Claude Desktop bridge

Task Station ships a **dependency‑free, hand‑rolled MCP server** (stdio JSON‑RPC, no SDK, no `pip`) that puts the *same* task board in Claude Desktop and Claude Code, backed by one shared local store. Create a task in a Desktop chat and it's waiting for you in the CLI; close it in the CLI and Desktop sees it closed. A self‑resolving launcher pinned to a stable path keeps Desktop working across plugin updates and concurrent CLI sessions.

```text
task-station config --desktop-bridge on   # then restart Claude Desktop
```

This safely merges one entry into your existing Desktop config (backed up first) and is fully reversible (`--desktop-bridge off`).

## Status bar

```text
task-station config --statusline on    # opt-in; default off, fully reversible
```

Installs an **opt-in, self-sufficient status bar** that shows your attached task's segment in the Claude Code status line. It is **default off**, **reversible** (`--statusline off`), and **non-destructive** — it never clobbers an existing `statusLine`:

- nothing owns the bar yet → task-station installs itself as the **host** (a tiny embedded compose routine — no external conductor needed);
- another conformant host already owns it → task-station only **registers its segment provider**, which that host composes automatically;
- a foreign/hand-written `statusLine` is present → task-station **leaves it untouched** and registers its provider for you to wire in.

The bar follows a small, vendor-neutral **composition convention**: any provider dropped into `${CLAUDE_CONFIG_DIR:-~/.claude}/statusline.d/` is composed alongside task-station's own segment, joined with a separator (`CLAUDE_STATUSLINE_SEP`, default `  │  `). Both the host and the provider honor `CLAUDE_STATUSLINE_WIDTH`. Full spec: [docs/STATUSLINE.md](docs/STATUSLINE.md).

## Configuration

`task-station config` (no args) prints a settings + status board. Flags:

| Flag | Values | Default | Purpose |
|---|---|---|---|
| `--workspace-dirs <a:b>` | paths | unset | Repo roots for delegate's `--project` shorthand. |
| `--categories [edit]` | — | CORE | Show the active category set (`edit` prints the config path). |
| `--auto-categories [on\|off]` | on/off | on | Auto-enable a slot the first time a task is assigned to it (board grows itself). |
| `--enable` / `--disable <key>` | category | — | Toggle a single category (GENERAL is permanent). |
| `--tint-theme [auto\|dark\|light]` | auto/dark/light | auto | Appearance: which variant renders — `auto` follows the OS (dark=Dark Sands, light=Light Sands). |
| `--theme [<name>\|save <name>\|edit\|preview\|list]` | theme name / verb | sands | Active colour theme (dark+light variants); mainly for custom themes (see [Themes](#themes)). |
| `--tint [on\|off]` | on/off | on | Full-palette terminal tint via escape codes. The `TASK_STATION_TINT` env var overrides the config setting. |
| `--title [on\|off]` | on/off | on | Auto terminal title `#<seq>: <title>`. |
| `--bare-cmds [on\|off]` | on/off | off | Install bare `/todo` + `/done` aliases. |
| `--update-check [on\|off]` | on/off | off | Opt‑in daily version check (no task data sent). |
| `--desktop-bridge [on\|off]` | on/off | off | Wire the MCP server into Claude Desktop. |
| `--statusline [on\|off]` | on/off | off | Opt-in self-sufficient status bar (composes `statusline.d/` providers); installs into `settings.json`, reversible, never clobbers an existing `statusLine`. See [docs/STATUSLINE.md](docs/STATUSLINE.md). |
| `--guaranteed-tracking [on\|off]` | on/off | off | Deterministic auto-create+attach a provisional task on a fresh session (vs the default nudge); auto-GC'd if skipped/closed untouched. |
| `--strict-delegation [on\|off]` | on/off | off | Writes standing delegation rules to `CLAUDE.md` (reversible managed block). Hidden `--policy` alias kept for back-compat. |
| `--ultracode-hints [on\|off]` | on/off | on | Suggest `ultracode` multi-agent breadth on fan-out-worthy tasks (L/XL, or RESEARCH/REVIEW/DATA at M+) for read/think phases only — never repo writes. `TASK_STATION_ULTRACODE_HINTS` overrides. See [Fan‑out hints](#fan-out-hints-ultracode). |
| `--reset [confirm]` | — | — | Reset all settings to factory defaults — asks to confirm (re-run `--reset confirm` to proceed). Your tasks are never touched. |

`task-station config` (no args) renders one **stanza per setting** — an aligned `<flag> <value> <options>` line, then the description with its default shown inline as `(default: X)` (the `--category-overrides` row is the relabelled former *category overrides*; the former `status`, `--workspace-dirs`, and `--data-dir` blocks are folded into this single list).

**Data dir** (set via `$TASK_STATION_HOME`, defaults to `~/.claude/task-station-data`) holds your `tasks.db` and config — outside the plugin cache, so updates never touch it.

### Guaranteed tracking (1.10.0, opt‑in)

By default a fresh, unattached session gets a **nudge** — Claude is told to track the topic but decides how (and may `skip` genuinely throwaway chatter). Turn on `config --guaranteed-tracking on` and the `UserPromptSubmit` hook makes it **deterministic**: on the first prompt of an untracked session it auto‑creates a *provisional* open task and attaches the session — no model decision required. It **folds, doesn't fork** (if a similar open task already exists it attaches to that one and files your prompt as a note instead of creating a sibling), and it **auto‑GCs**: a provisional task that's never engaged is deleted when the session is `skip`ped or closed, so pure Q&A leaves no litter. The moment you genuinely engage (update its title/summary/colour, edit a file, fold in a note) the task sheds its provisional flag and behaves like any normal task. **Off by default** — the conservative install behaves exactly as the firmer nudge.

## How it works

Tasks are stored in a local SQLite database (WAL mode, indexed) read on every prompt via hooks. A task is one record with a three‑state lifecycle (`open ○ → active ● → closed ✕`) and a stable `seq` number you never lose. The deeper mechanics — resume/cwd recovery, the dedup "fold don't fork" logic, worker registry, repo‑index enrichment, the Desktop launcher — are documented in **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.

## Data & privacy

Everything is local. No telemetry, ever. The opt‑in update check makes at most one `git ls-remote` per day and sends no task data. Repo‑index LLM enrichment is **off by default**, fingerprint‑gated, limited to file *names* with a secret denylist, and hard‑disabled by `TASK_STATION_REPO_ENRICH=off`. See [PRIVACY.md](PRIVACY.md).

## Troubleshooting

- **Terminal doesn't tint** — only iTerm2 / Apple Terminal are supported; export `$CLAUDE_TTY` in your shell rc for the most reliable targeting. Disable with `TASK_STATION_TINT=off`.
- **Worker fails: tool "not granted"** — add it to that repo's (or worktree's) `.claude/settings.local.json` allowlist; headless workers can't prompt.
- **`/plugin update` did nothing** — updates are version‑gated; if the version string is unchanged, re‑add the marketplace to force a refresh.
- **Turn won't end** — a Stop‑gate is asking you to track edited files; attach/create a task, or skip with the offered command (or `TASK_STATION_GATE=off`).

## Contributing

Issues and PRs welcome. Task Station is **stdlib‑only** (no third‑party deps). See [CONTRIBUTING.md](CONTRIBUTING.md) for running tests and regenerating the stack map.

## License

[MIT](LICENSE) © Ryan Nguyen
