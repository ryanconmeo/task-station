# Task Station

> Never lose your place in Claude Code — every task on one board, each wired to the session that holds its context, so you pick up exactly where you left off.

<p>
  <img alt="version" src="https://img.shields.io/badge/version-3.22.0-blue">
  <img alt="license" src="https://img.shields.io/badge/license-MIT-green">
  <img alt="Claude Code plugin" src="https://img.shields.io/badge/Claude%20Code-plugin-da7756">
  <img alt="CI" src="https://github.com/ryanconmeo/task-station/actions/workflows/ci.yml/badge.svg">
</p>

Your work in Claude Code scatters across sessions. Close the terminal and the task, its directory, and the conversation that built up its context are gone — native Tasks live inside a single session and don't link back. Task Station remembers. It keeps one durable board of everything you're working on, each task wired to the exact session that holds its context, so days later you reopen a task and land right back in the work. `/todo 286` brings the task back in your current session — a full recap from its digest, plus the `cd … && claude --resume …` one-liner into the session that holds its context. `/todo 286 -s` makes that jump for you, reopening the working session in a new window with directory and conversation recovered. It **complements** Claude's native Tasks; it doesn't replace them.

## Highlights

- **Pick up exactly where you left off.** `/todo <n>` reopens the task and recaps it from its digest, surfacing the exact `cd … && claude --resume …` back to the session that holds its context. `/todo <n> -s` makes the jump for you, reopening the working session — right directory, full conversation — in a new window.
- **See your whole workload at a glance.** `/todo board` writes a self-contained HTML board with search, filters, an OS-aware theme toggle, and change-driven refresh — no server, no dependencies, no external assets.
- **A resume loads a briefing, not a transcript.** Each task carries a stored [digest](#structured-digest) — goal, current state, a steps checklist, decisions, PRs, and stories — so reopening loads context, not raw history. `/todo save` hardens the handoff by reporting the **gap** — which named slots are empty or stale, what has landed since the last checkpoint, and what the digest costs to load — so you amend what is missing rather than rewriting what is already right; a mechanical cold-read check then catches anything a fresh instance would miss. It never echoes the digest back at you (`--verbose` if you want it). The working session's full transcript stays recoverable via `/todo <n> -s` as a backstop.
- **Checkpoint anytime; resumes stay cheap.** `/todo save` captures the task's full context as a lean snapshot, while the growing decision/log history stays off the resume path — pull it on demand with `/todo <n> history`. A resume costs about the same on day 30 as on day 1.
- **Ship repo changes without leaving your hub.** Delegate to a worktree-isolated worker that runs inside the target repo with its own `CLAUDE.md`, hooks, and environment — one worker per task and repo.
- **Your terminal tells you what you're in.** Every category owns a full colour palette; the terminal tints the instant a task or skill runs, so your current context is always visible at a glance.
- **A knowledge plane, in the same plugin.** [brain-station](#the-knowledge-plane-brain-station) pairs the board (the record of *work*) with a personal Markdown wiki (the record of *what is true*): search with citations, one-command capture, self-healing, hook-driven context injection, and an optional PR-gated org tier. Entirely opt-in — the board never notices if you skip it.
- **Your board survives every update.** State lives in a local SQLite store outside the plugin cache, so `/plugin update` never wipes your board or history.
- **Private by default.** Everything stays on your machine — no telemetry; the version check and repo enrichment are opt-in and send no task data.

## Install

```text
/plugin marketplace add ryanconmeo/task-station
/plugin install task-station@ryanconmeo
```

Requires the `python3` that ships with macOS/Linux (3.9+) — no pip, no dependencies.

## Quickstart

```text
/todo                      # show the board (empty at first)
> add the login bug to my tasks
  → Task [a1b2c3d4] created: "Fix login redirect bug"  ○ open
> /todo                    # it's tracked, with a category + effort
> /done                    # close the current task when finished
```

That's the loop. Tasks are created from natural language ("track this", "make a task for…"), auto-categorised and colour-tinted, and every session that touches one is remembered for resume. Reopen any task later with `/todo <n>`.

## The board

Running `/todo` prints your board in the terminal:

**Open**

|  | # | Task | Category | Effort | Activity |
|:-:|--:|------|----------|--------|----------|
| ● | 142 | Fix OAuth redirect loop on Safari | 🔴 [BUG] | `▰▰▱▱▱ S` | just now |
| ○ | 138 | Ship the dark-mode toggle | 🟢 [FEATURE] | `▰▰▰▱▱ M` | 2h ago |
| ○ | 131 | Address review feedback on PR 284 | 🟡 [FIX] | `▰▰▱▱▱ S` | 5h ago |
| ○ | 119 | Migrate billing schema to Postgres | 🟤 [DATA] | `▰▰▰▰▱ L` | 1d ago |

**Closed**

|  | # | Task | Category | Effort | Activity |
|:-:|--:|------|----------|--------|----------|
| ✕ | 134 | Add retry/backoff to webhook dispatch | 🔵 [INFRA] | `▰▰▱▱▱ S` | 1d ago |

*● active · ○ open · ✕ closed*

A task's lifecycle is one field with three states: open (`○`) → active (`●`) → closed (`✕`). A task auto-promotes to *active* the first time you edit a file in its session (or delegate a worktree for it). `/done` closes it; reopening a closed task returns it to *open*.

## Commands

| Command | What it does |
|---|---|
| `/task-station:todo` | Show the board. `/todo <n>` reopens a task (recap + the resume command); `/todo <n> history` shows the task's full trace (decisions + log + activity, read-only); `/todo <n,…> -s` jumps into the working session(s) in a new window; `/todo closed [N]` / `/todo all` list closed tasks; `/todo board` opens the HTML board; `/todo save` checkpoints the current task for a seamless resume. `pin` · `done [n,…]` · `config [flags]` · `save` · `history [n]` are also available as `/todo` subcommands (the standalone commands still work). |
| `/task-station:search` | Search across all tasks. `search <terms>` (or `/todo search <terms>`) prints a ranked hit list — `#seq`, status dot, title, and a match snippet each; `--open` / `--closed` / `--all` (default all) filter by status; `--detail <seq>` prints one task's full read-only digest. |
| `/task-station:done` | Close the current task; `/done <n,…>` closes by number. |
| `/task-station:pin` | Pin THIS session as the task's canonical resume target, so `/todo` always resumes here. |
| `/task-station:repos` | Set up repo routing so delegation lands in the right project. |
| `/task-station:native` | List Claude Code's in-session native tasks (read-only); `adopt <list>:<id>` promotes one to a durable task. Also `/todo native` · `/todo adopt`. |
| `/task-station:config` | Open the settings console (categories, themes, delegation, bridge, status bar). |
| `/task-station:save` | Checkpoint the current task for a seamless resume (same as `/todo save`). |
| `/task-station:heal` | **Reconcile** the task's append-only decision log into current state — the counterpart to `save`'s capture. **You never need to name a flag** — the skill drives the whole pass: it runs the cheap scan first, reads the dry run at most once, shows you a compact numbered plan, and asks once before changing anything. Under it: `--scan` runs the deterministic, zero-token scan (eight checks + a health metric) and never modifies the task; bare `heal` is a **dry run** that prints the plan and changes nothing; `--apply` performs the mechanical plan after backing the task blob up, and prints **only what it did** (`--verbose` for the full block, which is ~94% decision text you have already read); `--all` sweeps the board. Three verbs: **supersede** what is wrong, **split** what is compound, **merge** what is true but no longer load-bearing (`update --step-supersede N` is the same idea for a stale **step**). `--dispose-acks <id8,…|all>` retro-fills the dispositions of acks recorded before one was required — visibly retroactive, never overwriting what the acking session chose. An `--apply` that performed at least one operation **stamps** the heal; one that would perform none is **refused** rather than recording a reconcile that never happened, and `--mark-healed [--note '…']` records the judgement-only pass — so "never healed" stops being a permanent false alarm without the stamp ever becoming a lie. Drift ignores **session scratchpads and system temp paths**, which are erased by design; a vanished repo path is still reported. Nothing is ever deleted — every original stays in `history` marked with what replaced it, and `update --restore-decision <n>` / `--step-restore <n>` reverses any of them. Also `/todo heal`. |
| `/task-station:history [n]` | Show a task's full trace — decisions + log + activity, read-only. No arg = the current session's attached task; same as `/todo <n> history`. |
| `/task-station:glossary` | The task's canonical vocabulary. `glossary` lists terms; `add "<name>" <layer> <state> "<def>"` upserts one (name unique per task); `edit "<name>" [--layer\|--state\|--def\|--rename]`; `rm "<name>"`; `glossary <task#>` lists another task's. Terms are auto-injected into every attached session. Also `/todo glossary`. |
| `/task-station:brief` | Author an HTML design brief for a task — one a reader gets in one pass. Sections are derived from the material (there is no fixed list); detail is carried by tables, bespoke SVG diagrams, and code rather than prose; implementation detail collapses; a `Limits` section is mandatory. Written against a shipped stylesheet (`skills/brief/assets/brief.css`, both themes) and diagram catalogue, under the artifacts dir; `brief path` records the path on the task. `brief render --spec <file>` (brief-spec JSON → frozen template) is retained for back-compat. Also `/todo brief`. |

`save`, `history`, `pin`, `done`, and `config` are each available standalone as `/task-station:<name>` too — not just as `/todo` subcommands. Bare `/todo`, `/done`, `/pin`, `/repos`, `/save`, and `/history` aliases are opt-in — enable with `config --bare-cmds on`. The `/task-station:` forms always work.

### The `task-station` CLI

While the plugin is enabled, Claude Code puts its `bin/` on the Bash tool's PATH, so the engine is callable as a plain command:

```
task-station <command> [flags]      # e.g. task-station render --session <id>, task-station guidance
```

This is the short form of the long `python3 <plugin-cache>/lib/task-station.py <command>` invocation — same engine, no version-pinned cache path to spell out. The slash commands and hooks drive it for you; the CLI is there for scripts, ad-hoc calls, and the guidance/help text Claude follows. `task-station guidance` prints the full command reference — every subcommand with its flags, the model-facing source of truth for the command set.

## Search

`task-station search <terms>` (also `/todo search <terms>`) finds tasks across the whole store — matching each task's title, summary, goal, next-step, decisions, checklist steps, activity log, dated history, and linked repos/PRs/stories. The output is a token-economical **tier-1** hit list: one line per hit (`#seq`, status dot, title) with a one-line match-context snippet, ranked most-relevant-first.

- `--open` / `--closed` / `--all` — filter by status (default `all`).
- `--detail <seq>` — print that task's full digest (read-only; the same view `/todo <n>` shows, without attaching). For the complete trail use `/todo <n> history`.

Search is backed by a SQLite **FTS5** index kept in sync as tasks are written and backfilled once for pre-existing stores; on the rare sqlite3 build without FTS5 it degrades transparently to a ranked substring scan, so it always works. The Claude Desktop bridge exposes the same thing as a `search_tasks` tool.

## Time & cost stats

Each task's detail view (and the HTML board row expansion, and the Desktop `get_task` view) shows a compact **Stats** line — `time ~Xh Ym across N sessions · workers $X.XX`. Active time is derived from the task's activity timestamps with a 30-minute idle-gap cap (a bump after a longer gap starts a new span, so time spent away isn't counted). Worker cost is accounted **per task, per hub session (by ordinal), and per worker session**, priced per-model including cache-read/write tokens. Because a `--bg` worker has no stdout stream, its reported cost is sourced by summing its transcript's assistant-message usage (the *derived* ledger channel prices the same transcript independently as a cross-check). Tokens burned by runs that **crashed / timed-out / failed** are recorded in a **separate `wasted` category** — shown distinctly (`… · wasted $Y.YY (N runs)`) so the real-work figure stays historically comparable and nothing is silently dropped. A brand-new task with neither yet simply omits the line.

## The HTML board

`/todo board` writes a single-file `board.html` to your data dir and opens it. It is fully self-contained — inline `<script>`/`<style>` only, no server, no dependencies, no external assets:

- **Open / Closed sections** with status · # · task · category · effort · activity.
- **Families nest.** A child task renders directly under its parent, indented, with a connector column — and the family is placed by its **most recent member**, so recency still orders the board, a family at a time. Each relation kind says what it is: `⤶ 6 children` · `⤷ parent #12` · `⇠ waits on #19`. `group families` in the filter bar switches back to flat activity order and remembers the choice.
- **Expandable rows** that reveal the full digest — goal, state, steps checklist, decisions, files, PRs and stories (one per line), and repos — plus copy buttons on the open and resume commands.
- **Search + category/status filters** that live-filter rows with no reload, and a reset that clears everything.
- **Light / dark / auto theme toggle** — *auto* follows the OS appearance live and re-resolves the moment your system flips. Your choice persists across reloads.
- **Per-category colours** on each row's stripe and tag, and **effort colour-coding** by tier (XL → XS).
- **Change-driven refresh** (opt-in) — an open tab reloads only when a task actually changes, and also when you return from inactivity. It polls a tiny local script sidecar (not `fetch`, so it works on `file://` in Safari and Chrome) on a gentle idle cadence, pausing while you interact. A manual refresh control forces a reload on demand.
- **Footer** with the installed version and a link to the GitHub repo.

Pick which browser opens it with `config --board-browser "<App>"` (or `TASK_STATION_BROWSER`); otherwise it uses your system default. Turn on live refresh with `config --board-autorefresh on`.

## Structured digest

Every task carries a small, stored digest so a resume loads a briefing and the board is a real tracker. It is first-class data written as the work is summarised (by the model, via CLI flags) — never derived at render time, and with no LLM or network involved. The fields:

| Field | What it is | Set with |
|---|---|---|
| **goal** | One line — what "done" looks like. | `create --goal` · `update --goal` |
| **state** | Current standing + the next step. | `update --state` |
| **steps** | A `{text, done}` checklist with stable indices and an `N/M` rollup. A step that has gone **stale** is retired with `--step-supersede N` (non-destructive: it leaves the checklist and both sides of the `N/M`, stays in `/todo <n> history` marked with what replaced it, and `--step-restore N` undoes it). There is deliberately no step *edit*. | `create --step` · `update --step-add` · `--step-done N` · `--step-undone N` · `--step-supersede N` · `--step-restore N` |
| **summary** | The **current-snapshot** description — the present truth, kept lean (not a running log). | `update --summary` (replaces) · `--append-summary` (adds) |
| **decisions** | Append-only log of choices made (the why-trail). | `update --decision` |
| **log** | Append-only, dated milestones/findings (`{ts, text}`) — kept **off** the default resume path. | `update --log` |
| **prs** | Stored PR links `{url, desc}`, merged with links auto-extracted from the log (deduped by url). | `update --pr <url> [--pr-desc <text>]` |
| **stories** | Stored work-items `{url, desc}` (no auto-extraction). | `update --story <url> [--story-desc <text>]` |

**Lean current snapshot vs. on-demand history.** The default detail view (`/todo <n>`, and the resume recap) renders only the **current snapshot** — goal → state → steps → artifacts → summary — with decisions capped to the most recent few (a `… +K earlier — /todo <n> history` pointer when there are more) and recent activity capped to a short tail. The append-only `decisions` + `log` trail never loads on a normal resume, so resumes stay cheap. Pull the **full record** any time with `/todo <n> history` — a read-only time-machine showing the complete decisions log, the complete dated `log`, and the full activity log (the working session's full transcript is still one command away via `/todo <n> -s`). The HTML board mirrors the snapshot in each row's expansion.

**Content hygiene:** `summary` is the **current** description — rewrite it to the present truth (`--summary` replaces it wholesale), and never let it become a running log. The trail goes in `--decision` (why) and `--log` (dated milestones/findings); both are retrievable via `/todo <n> history`.

**A replaced summary is never lost.** `--summary` overwrites the first field a resuming session reads, so the text it replaces is preserved append-only: `update --task <n> --restore-summary` puts the previous one back (`--restore-summary <k>` for an older version), and `/todo <n> history` lists every version. The restore is itself reversible — nothing here is ever deleted, the same rule the decision verbs follow.

### Automatic checkpointing (opt-in)

The task's [digest](#structured-digest) is durable memory that lives **outside** the context window — so the way to survive compaction is to keep that digest current from full context, then resume from it. Context compaction is where a resume silently goes stale: the harness summarises the conversation out from under the digest, and its summary is generic, not task-shaped. Turn on `config --auto-checkpoint on` and Task Station treats **`/todo save` as the structured compaction** — a task-shaped checkpoint you resume from with a fresh session and `/todo <n>`:

- **A proactive nudge to checkpoint *before* compaction.** As a session fills, Task Station prompts a full structured `/todo save` from full context — a better, task-shaped compaction than the generic auto-summary — so a fresh `/todo <n>` resumes from your digest, not the harness summary. The default trigger is a **percentage of your real context window**, measured from the transcript's actual `usage` block: it fires once the measured context reaches `--checkpoint-pct` % (default `65`) of `--context-window` (default `200000`; raise it for a bigger window). The nudge names the measured pressure — e.g. *context ~68% (~136k/200k tokens)*. Set `--checkpoint-pct 0`/`off` to disable it. A legacy absolute fallback, `--checkpoint-at <tokens>` (default `off`), fires off a transcript-size token *estimate* when a real measurement isn't available — prefer the pct trigger. It fires once per pressure episode — it won't spam you if you defer. Auto-compaction can't be reliably prevented; the win isn't blocking compaction, it's keeping the durable digest current so the *resume* is structured.
- **The harness summary is stashed for free — as a fallback.** If auto-compaction wins the race, Claude's compaction summary is written straight into the task's history — no model tokens spent — and stays retrievable with `/todo <n> history`, so nothing is lost even if the structured digest hadn't caught up.
- **A post-compaction nudge keeps the digest current.** The next turn after a compaction reminds the model that the digest is its source of truth and asks it to refresh what advanced.
- **A milestone staleness nudge keeps it honest between checkpoints.** Once a batch of real work has landed since the last refresh — `--checkpoint-milestone-edits` meaningful events (file edits / status promotions), default `5` — a gentle, non-blocking reminder asks for a one-line `--state` update before finishing, and stops the moment you refresh. Set it to `0`/`off` to nudge on *any* staleness instead.

It's deliberately cheap: no full `/todo save` on every turn — a proactive checkpoint only as the session fills, a free stash as the fallback, plus a couple of well-timed nudges. **Off by default** — nothing changes until you opt in.

### Checkpointing best practices

A checkpoint is only as good as *when* and *what* you capture. The digest is durable memory that lives outside the window — these are the house rules the nudges are built to encode:

- **Save before the window gets heavy (~60–70%), never after auto-compaction.** A digest written post-compact is a summary of a summary — the detail is already gone. The proactive percentage nudge (`--checkpoint-pct`, default `65`% of `--context-window`) fires precisely while full context is still intact; act on it rather than deferring into a compaction.
- **Checkpoint at milestones, not just at session end.** A `/todo save` after each decision or working block turns a crash or OOM into minutes lost, not a session. The milestone staleness nudge (`--checkpoint-milestone-edits`, default `5` events) encodes exactly this — a batch of real work has landed, so refresh before moving on.
- **Make `NEXT:` a concrete first move.** The state line carries the resume: `NEXT: run V102 against local, then update the PR description` — never `NEXT: continue the work`. A fresh session should be able to *act* on it without re-deriving where you were mid-thought.
- **Keep the summary present-truth only.** The summary is re-injected on every resume, so any running history inside it is a token tax you pay forever. Rewrite it to what's true *now*; the why-trail and dated milestones belong in `--decision` / `--log`, retrievable on demand via `/todo <n> history`.
- **Always record what you tried and rejected — with the why.** A dead end and its reason is the one thing a fresh session cannot cheaply rediscover; without it, the resume happily re-walks it. That trail lives in `--decision`.
- **`/save` + a fresh session beats limping through repeated compactions.** A fresh session resuming from a curated digest is both cheaper and sharper than a monster session dragging degraded auto-summaries behind it. When context is heavy and the digest is current, resume clean rather than grinding on.

## Categories & terminal tint

Twelve colour categories, each with a tag and a full terminal palette:

| | | | | | |
|---|---|---|---|---|---|
| 🔴 BUG | 🟢 FEATURE | 🩷 PERSONAL | 🟠 REVIEW | 🔵 INFRA | 🎨 DESIGN |
| 🟡 FIX | 🟣 RESEARCH | 🪩 TOOLING | ⚫ GENERAL | 🟤 DATA | 📖 DOCS |

The board **starts lean and grows by itself.** A fresh install shows only the CORE set (🔴 BUG · 🟢 FEATURE · ⚫ GENERAL). The categoriser always considers the full taxonomy, and the first time a task is assigned to a category that isn't on the board, that slot is enabled automatically. So the board converges on exactly the categories you use, with zero setup. Prefer a fixed set? `config --auto-categories off` freezes it; then toggle slots by hand with `config --enable` / `--disable`.

Tinting is **zero-setup** on any xterm-compatible terminal: Task Station writes standard OSC escapes directly to the window — background, foreground, cursor, the full 16-colour ANSI palette, and (on iTerm2) bold. No profiles or shell aliases required. Detected terminals include iTerm2, Apple Terminal, WezTerm, VS Code, Ghostty, Windows Terminal, kitty, and Alacritty, with a plain `xterm-256color` fallback; escapes are automatically wrapped in tmux's passthrough when running under tmux (needs `tmux set -g allow-passthrough on`). See [Platform support](#platform-support).

A **theme** is a full-palette colour set with two variants — `dark` and `light` — and the OS appearance picks which renders. One theme ships, **Sands** (Dark Sands / Light Sands), so the terminal follows the OS out of the box. Customise tags, labels, and palettes in `config.json`; your edits survive plugin updates. See [CATEGORIES.md](CATEGORIES.md).

## In-project delegation

A hub session launched from `~` can't load a repo's `CLAUDE.md`, hooks, MCP servers, or permissions — those only load inside the repo. Task Station delegates the work to a `claude` worker spawned *in* the repo:

- **Visible in Agent View** — a `claude` worker spawns as a `claude --bg` background agent, so it shows up as a row in Claude Code's Agent View: select it and **attach** to watch its prompts and output live. It also keeps running if the hub session dies (detached), and task-station reads its status from `claude agents --json`.
- **Worktree-isolated** — every mutation runs in a sibling `<repo>-worktrees/<slug>`, off the repo's base branch, never your main checkout.
- **Fail-closed, never blocks** — a `--bg` worker spawns with `--permission-mode dontAsk` (+ `--allowedTools` for the author-only edit toolset), so a non-allowlisted tool (git, network, arbitrary Bash) is **auto-denied** rather than prompting — the unattended worker never stalls, and behaves like the old `-p` workers with **no `--dangerously-skip-permissions`**. Opt in to `--delegate-bypass-permissions on` (default **off**) if you want worktree workers to run anything unattended — that path needs a one-time `claude --dangerously-skip-permissions` acceptance.
- **Crash-safe** — the worker's session id is registered right after launch, so a timeout or kill never loses the conversation; the next call resumes it (`claude --resume <id>`), and another hub can re-adopt it from Agent View.
- **Hub ordinals + interaction ledger** — each hub session that touches a task gets a stable number `<seq>-<n>` (`-0` = the creator), shown in the statusline, `/todo` detail/board, and hooks. Every hub↔worker interaction (spawn / resume / finish / crash / timeout …) is appended to an unbounded provenance **ledger** on the task, so any hub sees the full history of every worker.
- **One worker per task and repo** — resume one-liners + roster show up in the task's detail view.
- **Sonnet by default** — workers do author-only mechanical edits, so they default to the `sonnet` model rather than your account default. Override per launch with `delegate run --model <name>` (e.g. `--model opus` for genuinely hard work).
- **`--harness {claude,codex}`** — the worker CLI. `claude` (default) is the full Agent-View `--bg` path above; `codex` runs `codex exec --json` as a detached fallback (no Agent-View row — task-station renders its own board; tracking is intact, display-loss only; codex tokens are recorded unpriced).

Delegation is *always available* — the `delegating-work` skill and `delegate.py` ship with the plugin and work regardless of any flag. `config --strict-delegation on` *enforces* it by writing a standing, reversible managed block into your `CLAUDE.md` so Claude delegates by default and follows the guardrails (worktree isolation, self-contained briefs, a required story and PR). Pair it with repo routing via `/repos`. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

### Worker notifications (opt-in)

A delegated worker can run for minutes. Task Station can ping you when a run **finishes** or **fails / times out**, on two independent, opt-in channels:

```text
task-station config --notify on                          # macOS banner (default off)
task-station config --notify-webhook "https://…"         # POST each event as JSON
```

- **macOS banner** (`--notify on`, macOS only) — a `task-station worker` notification reading `<repo>/<label>: finished|failed`. Fire-and-forget; failures are silent.
- **Webhook** (`--notify-webhook <url>`) — a `POST` with a JSON body `{event, task_seq, repo, label, worktree, cost_usd, ts}` where `event` is `worker_finished` or `worker_failed`. Any plain-JSON receiver works (Slack, Teams, or an [ntfy.sh](https://ntfy.sh) topic URL). 3s timeout, failures logged to stderr and swallowed.

Both channels are best-effort by design — a missing tool or a down webhook can never break a delegation. The env vars `TASK_STATION_NOTIFY` and `TASK_STATION_NOTIFY_WEBHOOK` override the stored config.

## The loop — plans that check themselves

A plan's items normally *assert* they are done. Task Station lets each item **prove** it: a checklist step can carry a runnable command plus the substrings its output must contain — the same shape `claims` uses for a whole document, aimed at one step.

```text
task-station exit-add  --task 12 --step 3 --cmd 'grep -c TODO src/*.py' --expect '0'
task-station exit-show --task 12                 # what is registered, how it last went — runs nothing
task-station exit-tick --task 12                 # RUNS them; ticks what passed; exit 1 if anything is not met
```

`exit-tick` moves the tick, so **done is computed, not asserted**. Three rules make that safe:

- **A condition with no `--expect` is refused** — it would pass forever whatever the command printed.
- **A condition that did not run refutes nothing.** A timeout or a missing binary is *unknown*, never *unmet*, and never moves a tick in either direction.
- **A condition that cannot run, or that can be satisfied by something other than the work, is refused before it is stored.** The shell is asked to *parse* the command (`bash -n` — never to run it; registering a condition must have no side effects), and the shape is linted for the three ways an assertion lies: a trailing `tail -N` (one extra line of stdout swallows the line the assertion is about), a **bare count** as an expected substring (`5013` is inside `15013`), and an **absence assertion** (`no failures`, `0 errors`) which nothing printed at all satisfies — so it passes hardest exactly when the command is broken. `--force` registers anyway and prints what it overrode.
- **Ticking is automatic; unticking is opt-in** (`--untick`). A failing condition on already-ticked work is reported as a **regression** — a real regression and a moved file look identical from here, and rewriting your record of finished work on that evidence is a bigger claim than a tick.

### Waves, and what is unblocked now

Once items can settle themselves, "what can start?" becomes a computation over the `depends-on` edges you already store:

```text
task-station scan --task 12          # waves over depends-on + the stopping condition
task-station scan --task 12 --json   # the same object, for a driver
```

`scan` calls **no model** and — without `--run` — **no shell**: it reads the verdicts `exit-tick` stored. A predecessor releases its dependents when it is **closed** or **every exit condition it registered is met**; a task registering *no* conditions is never settled, so an empty checklist can't release work by having checked nothing. Cycles are reported, never traversed; a dependency on a deleted task doesn't deadlock but is named. The stopping condition is four values — `ready` · `complete` · `blocked` · `empty` — because "nothing to do" and "nothing I *can* do" are opposite situations.

### Children, and the gate

```text
task-station invoke --task 17 --from 12 --ask '<the request>' [--role implementer]
task-station grade  --task 17 --dim G1=A --dim G2=A- … [--park human-gate --why '…']
```

`invoke` spawns a session **already attached to the child's own task**, so its SessionStart injects that task's digest and the ask carries the request only — there is no brief to get wrong. `--role` (scout · implementer · reviewer · grader) reads **the role table, which is configuration** — on the config board as `--roles`, retunable per role and per field under `"roles"` in `config.json`, and a station can declare a role of its own. Each role carries a model, a permission mode, an effort level, a **tool grant** and a **report contract**. The permission mode is emitted **only when it narrows** what the child may do, and the grant is a *deny* list for the same reason: a role may restrict and may never replace, so an allow list — which would drop the MCP servers you configured — is never emitted, and a role denying nothing emits no flag at all. The contract travels in the child's prompt, because what the child owes you back should be stated rather than assumed. A role's bare model alias also reclaims the parent's `[1m]` window when both name the same family — otherwise an orchestrator at 1M hands its child one fifth of the context without anyone asking. An override naming a field, mode or effort the CLI would reject is refused whole and reported on the board: half an applied override is the one outcome nobody could debug.

**Both concurrency budgets are enforced.** `loop_children_max` (default 3) is checked at invoke time against the orchestrator's children that hold a *running* session — over it, `invoke` exits 3 and writes nothing, while `--force` launches anyway and records that it did. `loop_builds_max` (default 1) is a lock in the data dir, so it is **machine-wide**: a suite run is a build, `exit-tick` and `scan --run` take a slot and wait before refusing, and two orchestrators on one machine contend for the same one. A holder whose process is gone is reclaimed, because a lock that survives a crash is a machine nobody can build on again.

`--dry-run` prints the command it *would* run and **writes nothing at all** — no session minted, no event on either task, no window. `--print-command` is not that: it is a real launch you complete by hand, so it still pre-attaches the session and records itself as a **manual launch**, which is what keeps one previewed-then-launched child from reading as two invokes. Before opening a window, `invoke` clears the first-run gates a loop cannot type through — the trust dialog, and the workspace's own `.mcp.json` approval — but **trust is only ever inherited**: a linked git worktree qualifies when its main checkout is already trusted on this machine, and nothing else ever does. Every refusal prints its reason and the invoke continues, so you answer one dialog rather than hitting a dead end.

`grade` records one pass of the acceptance gate against six rubric dimensions. **Acceptance is per-dimension** (default `A-`), never an average — an average lets a failed gate-integrity dimension hide behind five strong ones — and an ungraded dimension is not a pass. Exit codes let a driver branch: `0` accepted · `1` rejected with retries left · `3` retry budget spent · `4` parked · `2` bad command. **A parked child is never retried**, which is how a human gate halts the loop cleanly instead of being re-asked with a better prompt.

The **judgment** — what grade each dimension earns — is the `grade` skill's, not a flag's. The engine owns only what is deterministic.

A rejection also **goes back to the child as a memo on its own task**, naming the failed dimension and its grade, and naming ungraded dimensions separately — those call for different work (a low grade is the child's to redo, an ungraded one is the judge's to finish). A verdict recorded on the task and nowhere else is a verdict the child never reads: nobody types into an invoked child again, and by gate time it has usually stopped, so a memo — durable, on the record its SessionStart reads — is the only rail that reaches it. `--no-memo` opts out.

### One driven turn

```text
task-station turn --task 12          # what the loop does now, in order, with each command
task-station turn --task 12 --json   # the same object, for a driver
```

`turn` composes the verbs above into **one pass with nobody deciding what comes next**: gate what came back, grade it or park it, release what was accepted, re-launch a spawn that never came up, invoke one new child, wait on the rest. Like `scan` it calls **no model**, runs **no shell**, and writes **nothing** — every step it names is a separate recorded command, and the blanks it leaves (`--dim G1=?`, `--ask '<the request>'`) are exactly the judgement it does not have.

**The order is load-bearing.** What came back is gated *first*, because grading a finished child can release a wave and hands its slot back to the budget — invoking first spends the slot the gate was about to return. And it invokes **one child per pass**: a stagger, not just a cap, because two children in flight in one repo means two version bumps and a rebase for whoever lands second.

A child that stopped is one of eight states, no two of them synonyms. The one worth naming is **silent-exit** — it worked, it is gone, and it left no report. Calling that *failed* retries work that may be complete; calling it *unknown* stalls the loop; and it has a structural cause, since exit conditions run against the main checkout and a child's own work cannot turn them green until it merges. **Spawn intent is not liveness** either: a failed window-open still records the invoke and still mints a session, so the turn reconciles the trail against process liveness *and* against evidence the child actually took a turn — a dead spawn is re-launched, never graded.

`turn` halts with exit `3` and one of six named reasons — `complete` · `empty` · `working` · `budget` · `parked` · `blocked` — because "nothing to do" and "nothing I *can* do" call for opposite responses. Design and rationale: [`docs/specs/LOOP-GATE.md`](docs/specs/LOOP-GATE.md).

### Session succession — the relay

A long session fills up, and the default outcome is the harness's own auto-compaction: a model-authored summary nobody audited, landing when the window says so rather than when the work has a clean seam. `relay` is the alternative — stop deliberately, and hand the task to a fresh session that reads the digest.

```text
task-station relay --task 12            # the report: where this session stands (writes nothing)
task-station relay --task 12 --spawn    # hand off — a successor on THIS task
```

**Bare `relay` is the report, and the report is free.** No session minted, no event, no field touched — `invoke` needed a `--dry-run` flag to offer that; here the preview is the default and the flag is what opens a window, which is the right way round for a verb whose job is to end the session that typed it.

The policy is **two numbers, both printed**. A **trigger** — a share of the window (`succession_pct`, default 65, the same point the checkpoint nudge fires at) — and a **reserve** in absolute tokens (`succession_reserve`, default 40,000): what the handoff sequence itself costs, since reconciling the record, checkpointing from full context and generating the prompt is real work done inside the session that is running out of room. So three verdicts: **keep-going** below the trigger, **relay** above it with the reserve intact, **compact** above it with the reserve spent — a checkpoint written with no headroom is thinner than the compaction it was meant to beat. A fourth value, **unknown**, is what an unmeasurable session reports; it is deliberately *not* keep-going, because a policy that never ran has not decided anything.

**Due and ready are separate facts.** The verdict says whether this session should hand off; `ready` says whether its record can survive one — every named slot filled, a state line leading with `NEXT:`, and a checkpoint both taken and current. Gaps don't make the relay less due, they make it lossy, so both are reported and `--spawn` refuses until they close. `--force` overrides that (at 95% a degraded handoff beats none) and the gaps then travel **inside the continuation prompt**, so the successor knows what it is missing.

The successor is attached to **the same task** — no child, no new record — and runs the predecessor's own model selection, `[1m]` marker included. Its prompt is generated from the **record**, never the transcript, and carries the request only: the digest it is about to read already holds the goal, the summary, the decisions and the checklist, so restating them would be a second copy that can drift.

Every handoff lands on a ledger the parent grades **through the same `grade` verb and the same six dimensions** as any other child work (`grade --task 12 --handoff 1 --dim G1=A …`), carrying the mechanical evidence — who handed to whom, at what occupancy, against which window, and whether it was forced. A relay is internal to one task's life and creates no child, so without that ledger a thin handoff was invisible to every surface an orchestrator looks at; the scan row now carries the count and how many are still ungraded.
### Reaching a running child

`invoke` starts a child and `scan` watches one. Neither can say a word to one that is
already going: a memo lands on the record and is surfaced when somebody **types**, and an
invoked child is handed exactly one prompt and then works. So every mid-flight fact — main
moved, the spec changed, stop and hand back what you have — used to be undeliverable.

```text
task-station channel reach      --task 17                       # who is running, and by which source
task-station channel stand-down --task 17 --why 'main moved under you'
task-station channel orders     --task 17                       # the queue: pending · delivered · settled
task-station channel settle     --task 17 --id ab12cd34 --session <sid> --report '<what you wrote>'
task-station channel deny       --session <sid> --action 'kill -9 40311 40312' --by 'permission classifier'
```

**The transport is the turn boundary.** The end of a turn is the one moment a running
session arrives at by itself, with no human in the loop, and the Stop hook can refuse to
let it pass — so an order is read at the child's next Stop and the turn does not end until
it is settled. A memo to a task with a live session now rides the same rail automatically;
a task nobody is working on queues nothing and behaves exactly as before. Editing an exit
condition mid-flight is pushed too, because **done here is computed** from those
conditions and a child working to the old checklist would finish something that no longer
counts. An order blocks at most three times, then stays pending and visible without
holding the turn hostage.

**A stand-down is not a kill.** Settling one *requires* `--report`, and the report goes
back to whoever ordered it as a memo — the point of standing a child down is getting its
own account of where it got to, and a stand-down that needs nothing back discards
everything the child had not yet written down.

**Liveness comes from process state, not from a hook.** A row is built from the harness's
own per-process record — a live pid, plus the control socket the harness opened for that
session — and joined to the task through the roster the **launch** wrote, not through the
session→task link an *attach* writes. A child that has been launched but has not reached
its hooks yet is exactly the child a parent most needs to reach. The link store stays as a
second source, so a session that walked in and attached itself is still found; every row
says which one found it.

**What it does not reach — and what that implies.** The transport is the Stop hook, so
the channel reaches anything whose Stop hook runs: an interactive session, and a child
`invoke` opened. It does **not** reach a `delegate`-spawned worker, because `on_stop.sh`
exits immediately on `TASK_STATION_SUPPRESS` — task tracking inside a worker is the hub's
job, and that predates this. A memo to such a worker's task is still recorded and still
reaches the hub. This matters more than it sounds: a delegated worker already cannot
verify its own work, and now it also cannot be reached mid-flight. Those two together are
the argument for keeping delegation to **mechanical edits** and running anything needing
judgement as an `invoke`d child instead. Reaching a suppressed worker needs its own
decision about what a worker's Stop hook may do, and that decision is not smuggled in
here.

**The permission boundary is enforced at the channel.** Permissions in Claude Code are
per-session, and a control channel is exactly where that breaks: the moment a parent can
send work to a child, a session denied an action has an obvious workaround. So a session
that was **denied** an action may not ask a peer to perform it — refused at the channel,
naming the denial, never left to the receiver. `channel deny` is how a refusal the harness
handed down becomes durable, and it binds the session **and its task**, so a successor
session inherits the refusal instead of having to rediscover it. Note what the rule is
not: a plan-mode reviewer handing findings to an implementer is the loop working — it was
never *denied* an edit, it was never granted one.

### The orchestrator flag

```text
task-station update --task 12 --orchestrator on
```

A task flagged orchestrator-only plans and grades; it does not hold work. `delegate run --seq 12` then **refuses and names the child that should own the work**, with the exact command to run there. Explicit rather than inferred from "has children" — plenty of parents legitimately hold their own work, and a guard that fires on every parent gets switched off. `delegate run --force` overrides it deliberately and writes the override onto the task.

Tunables (config.json keys / env, no board row): `exit_command_timeout` (120s) · `loop_accept_threshold` (`A-`) · `loop_retry_max` (2) · `loop_children_max` (3) · `loop_builds_max` (1, **machine-wide** — two orchestrators share it) · `succession_pct` (65) · `succession_reserve` (40,000 tokens).

## Fan-out hints (ultracode)

Some tasks want **breadth** — many subagents reading, analysing, designing, or reviewing in parallel. Claude Code's built-in `ultracode` mode does exactly that, and Task Station knows which tasks would benefit: on a fan-out-worthy task (effort L/XL, or RESEARCH / REVIEW / DATA at M+) the detail recap and the SessionStart line surface a one-line suggestion to run it with `ultracode`. The human opts in by typing the keyword — Task Station never fires orchestration itself, and trivial work never triggers the hint.

ultracode and delegation are different tools on purpose: ultracode is opt-in breadth for **read / analyze / design / review** (hub subagents, no repo environment), while delegation is the sanctioned path for **repo writes** (a worktree worker with a story and PR). Opt out with `config --ultracode-hints off`.

## Native Tasks

Claude Code 2.1+ has its own in-session **Tasks** (`~/.claude/tasks/`). They're great for orchestrating *within* a session, but they're per-session and siloed — no cross-session board, no resume link. Task Station reads them (never writes them) so nothing is invisible, and lets you **adopt** one worth tracking durably.

```text
/todo native                      # list Claude Code's recent native task lists (read-only)
/todo adopt <list-prefix>:<id>    # promote a native task into a durable station task
```

`native` (also `task-station native`) shows one section per recent list — short list id, relative time, and each item as `✓ / ◐ / ○ <id> <subject>`. "Recent" means the list was touched in the last 14 days *or* still has an open item. When any recent list has open items, the terminal board grows a one-line **NATIVE** footer pointer so you don't forget them; the full dump stays behind `/todo native` to keep the board lean.

`adopt --native <list-prefix>:<id>` (also `/todo adopt …`) creates a station task from the native item's subject and description (colour GENERAL, effort S), recording provenance (`adopted from native task <list>:<id>`) in the summary and activity log. The native store is **never** modified — adoption is a one-way, read-only promotion.

Positioning: **native Tasks = in-session orchestration; Task Station = the durable cross-session console.** Adopt promotes work worth tracking beyond the session.

## Claude Desktop bridge

Task Station ships a dependency-free MCP server (stdio JSON-RPC, no SDK, no pip) that puts the same task board in Claude Desktop and Claude Code, backed by one shared local store. Create a task in a Desktop chat and it's waiting in the CLI; close it in the CLI and Desktop sees it closed.

```text
task-station config --desktop-bridge on   # then restart Claude Desktop
```

This safely merges one entry into your existing Desktop config (backed up first) and is fully reversible (`--desktop-bridge off`). The bridge advertises **16 tools** — eleven board tools (`list_tasks` · `create_task` · `search_tasks` · `get_task` · `get_prompts` · `set_status` · `add_note` · `list_sessions` · `send_memo` · `list_memos` · `ack_memo`) and, when a brain is configured, five [knowledge-plane](#the-knowledge-plane-brain-station) tools (`brain_search` · `brain_status` · `brain_save` · `brain_log` · `brain_recent_tasks`) — plus a `todo` prompt and `task://<seq>` resources. Brain tools mount lazily: any brain-side failure leaves the board serving alone. In Claude Code the plugin's `.mcp.json` registers the same server, so CLI and Desktop share one server and one store.

## Status bar

```text
task-station config --statusline on    # opt-in; default off, fully reversible
```

Installs an opt-in, self-sufficient status bar showing your attached task's segment in the Claude Code status line. It is non-destructive — it never clobbers an existing `statusLine`: it installs itself as host when nothing owns the bar, registers only its segment provider when a conformant host already exists, and leaves a hand-written `statusLine` untouched. It composes any provider dropped into `${CLAUDE_CONFIG_DIR:-~/.claude}/statusline.d/`. Full spec: [docs/STATUSLINE.md](docs/STATUSLINE.md).

## Obsidian export

Opt-in, **one-way** mirror of your tasks into an [Obsidian](https://obsidian.md) vault as Markdown notes. Task Station stays the source of truth — it only ever *writes* derived notes; nothing is read back. Off by default.

```text
task-station config --obsidian-vault "~/Documents/Obsidian Vault"   # turn on
task-station config --obsidian-vault                                # (no value) turn off
task-station obsidian --sync-all                                    # backfill every existing task
task-station obsidian --status                                      # what's configured / how many notes
```

**What gets written, and where.** Everything the plugin owns lives under one folder — `<vault>/Claude/task-station/` (named after the plugin), so removing the integration is a single folder delete:

```text
<vault>/Claude/task-station/
├── 12-fix-the-login-bug.md      # one note per task: <seq>-<slug>.md
├── 42-add-export.md
├── categories/                  # one hub page per category (graph clustering; on by default)
│   ├── bug.md
│   ├── feature.md
│   └── personal/                # sub-hubs: emergent within-category clusters (on by default)
│       └── hammerspoon.md       #   e.g. many "hammerspoon …" tasks auto-cluster here
├── Task Board.base              # a managed Obsidian Bases view (written once)
└── .task-station-index.json     # internal: keeps note filenames stable across renames
```

Each note has flat YAML frontmatter (`managed-by: task-station`, `seq`, `status`, `category`, `effort`, `repos`, `story`, `pr`, `created`/`updated`/`done`, `title`) and a body of `## Goal`, `## State`, `## Summary`, `## Decisions`, `## History`. Notes are re-exported on **create / update / done / `/todo save`** (plain activity bumps and attaches are excluded as too noisy); `--sync-all` rewrites all of them.

**Category hubs (on by default).** So task notes aren't orphan nodes in a graph view, each note gets one resolvable wikilink to its category hub in `## Related` — `[[categories/bug|BUG]]` — and the sync maintains a hub page per category under `categories/` (generic export: `<dir>/categories/`): a machine-managed page listing every task in that category (`[[<stem>|<title>]]`, open+active first, then closed), fully regenerated each sync like `index.md`. Both directions link, so a graph view clusters per category. A category with no tasks has its hub pruned; a two-owner vault (`config --owner`) nests each owner's `categories/` under their subtree. Turn it off with `config --obsidian-category-hubs off` — that drops the category links and prunes the hub pages on the next sync.

**Emergent sub-groups (on by default).** Within a busy category, task-station notices when several tasks share a distinctive word and auto-clusters them into a **sub-hub** — e.g. a pile of `hammerspoon …` tasks under PERSONAL gathers into `categories/personal/hammerspoon.md`, backfilled and maintained on every sync. Detection is deterministic and local (no LLM): a lowercase word (≥ 4 chars) becomes a sub-group when it appears in **≥ 3** of the category's tasks, is **distinctive** (in < 10% of tasks outside the category, so generic words like *update*/*review* never cluster), and isn't a common stopword. Each task joins at most one sub-group (its highest-frequency matching word; ties alphabetical), and its note links the **most specific** hub — `[[categories/personal/hammerspoon|HAMMERSPOON]]` instead of the bare category — while the sub-hub links back up to the category, so a graph view reads as a clean **task → group → category** tree. The parent hub gains a `### Groups` section linking its sub-hubs; a group that drops below 3 members dissolves (its page pruned, members reverting to the plain category link). Nested inside category hubs (only active when those are on); turn it off with `config --obsidian-subgroups off`.

**Filenames are stable.** The note file is `<seq>-<slug>.md`, but the chosen name is remembered — renaming a task's title keeps its original note file instead of orphaning it.

**Task Board.base** is a minimal [Bases](https://help.obsidian.md/bases) table of open tasks (seq/title/category/effort/updated), written **once** and never overwritten, so your edits to it survive a resync. If your Obsidian build's Bases schema differs, the equivalent [Dataview](https://blacksmithgu.github.io/obsidian-dataview/) query is:

````text
```dataview
TABLE seq, category, effort, updated
FROM "Claude/task-station"
WHERE managed-by = "task-station" AND status = "open"
SORT seq ASC
```
````

**Daily note (opt-in).** With `--obsidian-daily-note on`, closing a task and each `/todo save` checkpoint append a line to the vault's daily note (`<vault>/<YYYY-MM-DD>.md`) under a configurable heading (`--obsidian-daily-heading`, default `## Claude sessions`):

```text
- 14:30 · [[12-fix-the-login-bug]] — closed: Fix the login bug
```

**Sync-service caveat.** Exports are last-writer-wins with short read-modify-write windows and atomic replaces, but they are *not* conflict-aware. If the same vault is edited on two machines through **iCloud Drive + Obsidian Sync at once** (double-sync), a race can drop an update. Keep the vault under a single sync mechanism, and treat the notes as a generated view — the store (`tasks.db`) is authoritative, so `--sync-all` always repairs the vault. A missing vault path (unmounted drive, typo) is skipped with a one-line stderr note; it never crashes the engine.

**Protected folders & the sandbox — it Just Works.** macOS gates `~/Documents`, `~/Desktop`, `~/Downloads`, and `~/Library/Mobile Documents` (iCloud) behind TCC, and Claude Code's **Bash-tool sandbox** only allows writes under the session cwd + `$TMPDIR`. So a vault in `~/Documents` is *unwritable* from a project session (writable only when the session cwd is `~`): the mid-turn atomic write is denied (`os.replace` → `EPERM`). **You don't need to do anything about this.** A denied in-session export silently marks the task **pending-resync** (an `obsidian_dirty` flag, mirroring the digest-staleness flag), and the **Stop and SessionStart hooks — which run *unsandboxed*** — auto-flush those pending tasks at end-of-turn and next-session start. The write from a hook succeeds where the sandboxed hot path couldn't, so the vault self-heals within seconds, with zero configuration and zero noise. `obsidian --status` shows the pending count; `obsidian --flush` drains it on demand (cheaper than `--sync-all`).

**Optional — instant inline exports.** If you'd rather the *in-session* export write immediately (no end-of-turn wait), widen the sandbox write-allowlist for your vault:

```text
task-station config --obsidian-sandbox on      # adds the vault to sandbox.filesystem.allowWrite
task-station config --obsidian-sandbox off     # reverse (precise; leaves your other settings alone)
```

This edits your **own** `~/.claude/settings.json` (a plugin can't ship sandbox config), adding just your vault path to `sandbox.filesystem.allowWrite` — it never touches `sandbox.enabled` or other keys. Equivalent manual edit (paths accept `~/` or absolute; merged across user/project/local scopes):

```json
{
  "sandbox": {
    "filesystem": {
      "allowWrite": ["~/Documents/Obsidian Vault"]
    }
  }
}
```

Only if the vault path is **genuinely** unreachable — gone/unmounted, or even the *unsandboxed* hook is denied — does a task stay dirty after a flush; **then** one deduped hint fires with the fixes: grant Claude Code / your terminal **Full Disk (or Documents) access** in *System Settings → Privacy & Security*, point the vault outside a protected folder, run `--obsidian-sandbox on`, or drain manually with `obsidian --flush` from an unsandboxed shell. Setting `--obsidian-vault` to a protected path prints a one-line non-fatal heads-up (the path is still accepted).

## Using Task Station as an episodic memory layer

Task Station is the **episodic layer** of a memory stack: it records *what happened* — one durable note per task, with the goal, decisions, history, derived model/cost usage, and (opt-in) the exact prompt trail. Point any second brain at it — a private markdown vault, a Basic Memory graph, a Dataview dashboard, or your own consolidation job — and layer *semantic* distillation on top. Task Station only ever **exports**; it never reads notes back. Direction is **one-way, pull**: you (or a scheduled job) pull a snapshot; a consumer ingests it.

**Two ways to pull, one shared contract.**

1. **A durable snapshot — `task-station export`.** Writes the per-task notes plus a wikilinked `index.md` into *any* directory, with **no vault configuration required**:

   ```text
   task-station export --dir /path/to/brain --all --include usage,prompts,history
   task-station export --dir /path/to/brain --status open        # just the open tasks
   task-station export --dir /path/to/brain --since 2026-07-01    # only recently-updated
   task-station export --dir /path/to/brain --task 12             # one task
   ```

   A consolidation job typically runs `export --dir <brain>/raw --since <last-run>` on a schedule, then distils the raw notes into its own semantic layer. `--include` chooses the sections (`usage`, `prompts`, `history`; default `usage,history`). Prompt export is **opt-in** — `--include prompts` (or, for the Obsidian vault, `--obsidian-prompts on`) — because a snapshot may leave the machine; usage is same-machine-derived and on by default.

2. **A live query — the MCP bridge.** For up-to-the-moment reads over the same shared store rather than a point-in-time snapshot, the [Desktop bridge](#claude-desktop-bridge) exposes `search_tasks` (ranked find), `get_task` (full detail), and `get_prompts` (a task's prompt trail, when capture is on). Export for durable ingestion; query for interactive lookups.

**The interchange contract.** Every note is plain Markdown with Obsidian-compatible `[[wikilinks]]` and **flat YAML frontmatter** (scalars and lists only — [Bases](https://help.obsidian.md/bases)/[Dataview](https://blacksmithgu.github.io/obsidian-dataview/)-queryable, and Basic-Memory-compatible):

| key | type | meaning |
|---|---|---|
| `managed-by` | scalar | always `task-station` — filter your view on this. |
| `seq`, `status`, `category`, `effort` | scalar | the task's number, state, category label, effort. |
| `repos`, `story`, `pr` | list | repos touched, story/PR URLs. |
| `created`, `updated`, `done` | date | ISO local dates (`done` empty while open). |
| `models` | list | model ids used (e.g. `claude-fable-5`). |
| `cost-usd` | scalar | derived $ from local token counts × published rates. |
| `time-spent` | scalar | active minutes on the task (idle-gap-capped). |
| `title` | scalar | wikilink-safe title, usable as a link alias. |

The body sections are `## Goal`, `## State`, `## Summary`, `## Decisions`, `## History`, `## Usage` (model mix %, tokens, `$ derived · $ reported`, work-phase mix), and — when enabled — `## Prompts` (the full timestamped trail). Task Station is generic: the export points anywhere and carries **no** integration-specific naming, so it composes with any downstream tooling you choose.

## The knowledge plane (brain-station)

The export contract above feeds *any* second brain — but since 3.0.0 the plugin ships
one. **brain-station** is the knowledge plane to the board's episodic plane: where a task
records what *happened*, a brain note records what is *true* — one fact per note, plain
Markdown with frontmatter (`name` / `description` / `type` / `scope` / `verified:`), so
validity is a dated field you can lint, not a vibe. Task notes accumulate; knowledge
notes converge. Everything is opt-in and stdlib-only, like the rest of the plugin.

```text
/brain-init                # first run: preflight, scaffold the vault, write the config
/brain what do we know about the deploy pipeline?
/brain-save                # distill this conversation's durable takeaways into notes
/brain-heal                # self-healing pass: tier-lint, lint fixes, episode ingest
/brain-promote <note>      # personal note → org-brain PR (when an org tier is linked)
/ado 1234                  # read an Azure DevOps work-item tree in one zero-token call
```

- **`/brain-init` scaffolds everything** from the bundled vault template: the vault
  (default `~/brains/brain`), its `CLAUDE.md` schema rules, an `INDEX.md` catalog, and
  the runtime config. It is idempotent and reversible, and it can migrate-then-link your
  existing agent-memory directory into the vault so memory and knowledge share one graph.
- **Hooks keep the brain ambient.** On each prompt, relevant notes are injected as
  context (throttled per topic, keyword-gated, `inject_context` to disable); a Stop-hook
  distiller (opt-in, `auto_distill`) captures durable facts from the session; a daily
  dirty-gated heal nag keeps the vault reconciled; and a `PreToolUse(Bash)` **secret
  guard** denies commands that would echo a secret into the transcript (it fails open —
  a guard bug never blocks your shell).
- **Federation is layered, never magic.** A personal note marked `scope: team` becomes a
  promotion candidate; `/brain-promote` converts it to the org schema and lands it as a
  PR a lead approves. An org-brain clone joins search read-only; peers' published
  subsets can be subscribed and searched, and a peer's copy never beats your own note.
- **Configuration** resolves `~/.claude/brain-station.json` → `~/brains/config.json`
  (vault, memory, org-brain clone, publish mirror, inject/distill toggles), and every
  key has a `TASK_STATION_BRAIN_*` environment override. Org values — labels, keywords,
  forge coordinates — arrive at runtime from an org profile
  (`python3 -m brain.init_home --profile <org.json>`), never from code.
- **`task-station org-setup` writes that profile for you.** Four **read-only** scans over
  systems your org already runs — `INFORMATION_SCHEMA` schema names and migration header
  comments, directory **group display names only**, repo and project names, and the leading
  segment of existing wiki page names — plus the six answers no scan can discover (org slug,
  org brain repo, per-person mirror template, forge + URL, vertical pack, promotion
  approvers). It validates *before* it writes, because a config the platform refuses to
  parse means no rules at all, not default rules. Full write-up:
  [docs/ORG-SETUP.md](docs/ORG-SETUP.md).
- **The same MCP server serves both planes** — see [the Desktop bridge](#claude-desktop-bridge):
  five `brain_*` tools mount beside the board's eleven, in Claude Code (via the plugin's
  `.mcp.json`) and Claude Desktop alike.

Full reference — configuration, hooks, federation, the `python3 -m brain.*` command
line — in [docs/BRAIN.md](docs/BRAIN.md); note naming rules in
[docs/brain-naming.md](docs/brain-naming.md).

## Configuration

`task-station config` (no args) prints a settings + status board, and `/task-station:config` is the console for it. Key flags:

| Flag | Values | Default | Purpose |
|---|---|---|---|
| `--categories [edit]` | — | CORE | Show the active category set (`edit` prints the config path). |
| `--auto-categories` | on/off | on | Auto-enable a slot the first time a task uses it. |
| `--enable` / `--disable <key>` | category | — | Toggle a single category (GENERAL is permanent). |
| `--theme` | name / verb | sands | Active colour theme; mainly for custom themes. |
| `--tint-theme` | auto/dark/light | auto | Which variant renders — `auto` follows the OS. |
| `--tint` | on/off | on | Full-palette terminal tint. |
| `--title` | on/off | on | Auto terminal title `#<seq>: <title>`. |
| `--bare-cmds` | on/off | off | Install bare `/todo`, `/done`, `/pin`, `/repos` aliases. |
| `--board-autorefresh` | on/off | off | Keep an open board tab live (reloads only on a real change). |
| `--board-browser ["<App>"]` | app name | system default | Which browser the board opens in. |
| `--update-check` | on/off | off | Opt-in daily version check (no task data sent). |
| `--guaranteed-tracking` | on/off | off | Deterministically track every new session (vs the default nudge). |
| `--ultracode-hints` | on/off | on | Suggest `ultracode` breadth on fan-out-worthy tasks. |
| `--notify` | on/off | off | macOS banner when a delegated worker run finishes or fails. |
| `--notify-webhook ["<url>"]` | url | unset | POST worker finished/failed events as JSON (Slack/Teams/ntfy). |
| `--strict-delegation` | on/off | off | Write delegation rules into `CLAUDE.md` (reversible). |
| `--desktop-bridge` | on/off | off | Wire the MCP server into Claude Desktop. |
| `--statusline` | on/off | off | Opt-in status bar. |
| `--worktree-hook` | on/off | off | Opt-in `WorktreeCreate` provisioner: new worktrees get the main checkout's `.claude/settings.local.json` + a trust entry. While it's on, this hook **replaces** worktree creation machine-wide; `off` is the one-command reverse. |
| `--config-change-enforce` | on/off | off | Block a settings save that declares a path which no longer exists, instead of only recording it to `hook-health`. |
| `--obsidian-vault ["<path>"]` | path | off | Export tasks (one-way) into this Obsidian vault; no value = off. |
| `--obsidian-daily-note` | on/off | off | Append a daily-note line on task close + `/todo save`. |
| `--obsidian-daily-heading ["<h>"]` | heading | `## Claude sessions` | Heading the daily-note lines go under. |
| `--obsidian-prompts` | on/off | off | Write the full `## Prompts` trail into exported vault notes (prompt export is opt-in). |
| `--obsidian-category-hubs` | on/off | **on** | Category hub pages + a `[[categories/<slug>]]` link per note, so the graph clusters by category. Off drops the links and prunes the hubs on next sync. |
| `--obsidian-subgroups` | on/off | **on** | Emergent sub-hubs: distinctive recurring title words auto-cluster into `categories/<cat-slug>/<token>.md`, and member notes link the sub-hub. Nested in category hubs; off prunes the sub-hubs and reverts members. |
| `--workspace-dirs <a:b>` | paths | unset | Repo roots for delegate's `--project` shorthand. |
| `--artifacts-root [<path>]` | path | `<data-dir>/artifacts` | Root dir for rendered `/brief` artifacts. Derives from the data dir by default (never a hardcoded `~/` path); `TASK_STATION_ARTIFACTS_ROOT` env wins; no value clears the override. |
| `--reset [confirm]` | — | — | Reset all settings to factory defaults (your tasks are never touched). |

Your **data dir** holds `tasks.db` and `config.json` — set it via `$TASK_STATION_HOME` (defaults to `~/.claude/task-station-data`), outside the plugin cache, so updates never touch it.

### Guaranteed tracking (opt-in)

By default a fresh, unattached session gets a *nudge* — Claude is told to track the topic but decides how (and may skip genuinely throwaway chatter). Turn on `config --guaranteed-tracking on` and the `UserPromptSubmit` hook makes it deterministic: on the first prompt of an untracked session it auto-creates a provisional task and attaches the session. It **folds, doesn't fork** (a similar open task is attached to, with your prompt filed as a note), and it **auto-GCs** — a provisional task that's never engaged is deleted when the session is skipped or closed, so pure Q&A leaves no litter. The moment you genuinely engage, the task sheds its provisional flag.

## How it works

Tasks live in a local SQLite database (WAL mode, indexed), read on every prompt via hooks. A task is one record with a three-state lifecycle and a stable `seq` number you never lose. Hooks drive the automation: `SessionStart` announces and tints, `UserPromptSubmit` tints on a known skill and keeps the task fresh, `PostToolUse` promotes a task to active on an edit, and `Stop` blocks ending a turn with untracked edits. With [automatic checkpointing](#automatic-checkpointing-opt-in) on, `Stop` proactively prompts a full structured `/todo save` once the measured context reaches `--checkpoint-pct` % of `--context-window` (before compaction), `PostCompact` stashes the compaction summary as a fallback, and `Stop`/`SessionStart` add the digest-freshness nudges. Three more events round out the edges: `SessionEnd` records *why* a session ended and stops the workers it spawned (the `SessionStart` sweep stays as the crash backstop), `ConfigChange` reports settings paths that no longer resolve, and `FileChanged` re-arms the pointer/drift checks when a station config file changes on disk. A ninth, `WorktreeCreate`, is installable rather than shipped (`--worktree-hook`), because that hook replaces worktree creation. Under the hood the code is stdlib-only Python in three packages — `lib/core/` (shared plumbing), `lib/board/` (the engine), `lib/brain/` (the [knowledge plane](#the-knowledge-plane-brain-station)) — fronted by the `lib/task-station.py` facade; the slash commands, the hooks, and the [`task-station` CLI](#the-task-station-cli) on `bin/` all invoke it. Where both planes want the same hook event, one registered command (`lib/hookmux.py`) runs both planes' handlers in order — board first — and merges their output, so a failure in one plane never breaks the session. The deeper mechanics — resume/cwd recovery, the "fold don't fork" dedup, the worker registry, repo-index enrichment, the Desktop launcher — are documented in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## FAQ

**Do I have to run Claude from the same directory each time?**
No. The board is global — your tasks and config live in one store (`~/.claude/task-station-data`, or `$TASK_STATION_HOME` if set), not in the current folder, and the hooks run in every session wherever you launched it. Each session's working directory is captured from its transcript, so tasks started in different folders all land on one board and `/todo <n> -s` returns to each task's own directory. (The only caveat: point `$TASK_STATION_HOME` at different paths in different shells and you'll get separate boards — leave it default for one.)

**Where do I run the hub for a multi-repo project?**
From a directory outside any single repo — your home `~`, or a parent folder above your repos. Claude Code loads a repo's `CLAUDE.md`, hooks, MCP servers, and permissions only when its cwd is inside that repo, so a hub launched inside one repo biases your orchestrator toward it; a hub at `~` stays a neutral coordinator that drives every repo equally. Point `config --workspace-dirs` at your repo-root folders and run `/repos` once to index them; a change spanning several repos then spawns one worker per repo — each in its own repo's worktree, with its own story and PR — coordinated from the hub. The repos themselves can live anywhere on disk; the hub reaches them by path.

**How does delegation pick the right repo — `--repo` vs `--project`?**
`delegate run --repo /abs/path/to/repo` works from any hub directory with zero config — the absolute path is all that matters. `--project <name>` is shorthand that resolves a name against the folders in `config --workspace-dirs`, and `/repos` builds a name/keyword/path index so a fuzzy task routes to the right repo without spelling out the path. Either way the worker runs in a git worktree inside the target repo, off its base branch, loading that repo's own environment — your main checkout is never touched. See [In-project delegation](#in-project-delegation).

## Data & privacy

Everything is local. No telemetry, ever. The opt-in update check makes at most one `git ls-remote` per day and sends no task data. Repo-index LLM enrichment is off by default, fingerprint-gated, limited to file *names* with a secret denylist, and hard-disabled by `TASK_STATION_REPO_ENRICH=off`. The [knowledge plane](#the-knowledge-plane-brain-station) follows the same rule: notes are plain Markdown in directories you chose, and its only network operations are the ones you configure explicitly (the promote PR, org-pull, publish). See [PRIVACY.md](PRIVACY.md).

## Platform support

The tracker (board, categories, resume, delegation) is cross-platform — anywhere `python3` 3.9+ runs. The terminal-facing niceties degrade gracefully:

| Feature | macOS | Linux | Windows |
|---|---|---|---|
| **Terminal tint** (colour the window per category) | ✅ iTerm2 (+ tab) / Apple Terminal / any OSC-11 terminal | ✅ any OSC-11 terminal (WezTerm, VS Code, Ghostty, kitty, Alacritty, xterm) | ✅ Windows Terminal & other OSC-11 terminals |
| **Statusline segment** (opt-in) | ✅ | ✅ | ✅ |
| **Window title** (`#<seq>: <title>`) | ✅ | ✅ | ✅ |
| **`-s` jump window** (open the task's session in a new window) | ✅ Terminal.app / iTerm2 | ⚠️ prints the resume one-liner to run by hand | ⚠️ prints the resume one-liner to run by hand |
| **Auto-close on `/done`** | ✅ Terminal.app / iTerm2 | ⚠️ no-op | ⚠️ no-op |
| **Visual board** (`board`, opens `board.html`) | ✅ auto-opens | ⚠️ writes the file; open it yourself | ⚠️ writes the file; open it yourself |

Tinting targets the *originating* window focus-independently on macOS (via `$CLAUDE_TTY` / iTerm's session UUID). On other platforms it tints the current tty; export `$CLAUDE_TTY` in your shell rc for the most reliable targeting everywhere. Under tmux, escapes are wrapped in the DCS passthrough (`tmux set -g allow-passthrough on`).

## Troubleshooting

- **Terminal doesn't tint** — Task Station tints any xterm-compatible terminal (see [Platform support](#platform-support)); an unrecognised terminal stays quiet rather than print garbage. Force detection with `TASK_STATION_TERM=osc` (or `iterm`/`terminal`/`none`); export `$CLAUDE_TTY` in your shell rc for the most reliable targeting; under tmux enable `allow-passthrough`. Disable entirely with `TASK_STATION_TINT=off`.
- **Worker fails: tool "not granted"** — add it to that repo's (or worktree's) `.claude/settings.local.json` allowlist; headless workers can't prompt.
- **`/plugin update` did nothing** — updates are version-gated; if the version string is unchanged, re-add the marketplace to force a refresh.
- **Turn won't end** — a Stop-gate is asking you to track edited files; attach/create a task, or skip with the offered command (or `TASK_STATION_GATE=off`).

## Contributing

Issues and PRs welcome. Task Station is stdlib-only (no third-party dependencies). See [CONTRIBUTING.md](CONTRIBUTING.md) for running tests and regenerating the stack map.

## License

[MIT](LICENSE) © Ryan Nguyen
