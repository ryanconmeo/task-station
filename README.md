# Task Station

**Task Station** is an automatic, persistent task hub for Claude Code. Every session can attach to a task; tasks survive across sessions and are listed/resumed with `/todo`. Each task **pins to a resumable Claude session** — reopen the exact session behind it (or **re-pin a fresh session to save tokens**). Tasks are **auto-categorised and colour-tinted**, and Task Station is the **hub that launches parallel in-project workers**.

Run `/todo` and Claude renders your board as two tables — **open** first, then recently **closed**. Task numbers are stable ids assigned in creation order, so they look scattered: an old long-running task keeps its low number while newer tasks get higher ones.

**Open**

|   # | Task | Category | Effort | Activity |
| --: | --- | --- | --- | --- |
|  38 | Add dark mode toggle to the settings page | 🎨 [DESIGN] | ▰▰▰▱▱ M | 2h ago |
|  12 | Fix auth token refresh on expired sessions | 🔴 [BUG] | ▰▰▱▱▱ S | yesterday |
|   5 | Build cross-session task tracker | 🪩 [AI CONFIG] | ▰▰▰▰▱ L | 3d ago |

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
- `/todo <number[,…]> -s` — jump straight into a task's pinned session in a new window (a comma list jumps several)
- `/todo closed [N]` or `/todo all` — see more closed tasks
- `/done` — close the task this session is working on
- `/done <number[,…]>` — close any task by its number (a comma list closes several)
- `/task-station:config` — view or change settings; run one-time setup (tint profiles, delegation policy)

Effort runs `▰▱▱▱▱` XS → `▰▰▰▰▰` XL, and each task is colour-tinted by category ([see the taxonomy](CATEGORIES.md)).

A task's lifecycle is **one `status` field with three values: open (`◦`) → active (`●`) → closed**. Each not-closed row leads with a status glyph — `◦` **open** (a topic merely raised) or `●` **active** (work has started); a topic shows up the moment you raise it and auto-promotes `◦ → ●` when you act on it. Closed tasks move to their own section.

## Key Features

- **Three-state status — open (`◦`) → active (`●`) → closed** — a topic you merely raise is tracked immediately as `open`; it graduates to `active` when work actually starts (you delegate `--worktree`, edit a file in the session, or set it manually), and `/done` closes it. Status is a single field (no separate open/closed flag), renders as a leading glyph on every not-closed row, and replaces the old "pure Q&A → stay silent" behaviour. Related questions across sessions **fold into one task** instead of forking siblings.
- **Persistent, cross-session tasks** — a `/todo` board that survives restarts; each task carries a stable number, summary, activity log, and effort estimate.
- **Resumable, re-pinnable sessions** — every task pins to a Claude session you can reopen, or **re-pin a fresh session to save tokens** when context grows bloated.
- **Big-picture context for Claude** — running `/todo` pulls your whole board into the session, so Claude can reason across *all* your tracked tasks at once. That cross-project view is the leverage behind large, multi-domain work — e.g. migrating data or wiring separate domains/systems together.
- **Auto-categorised + colour-tinted** — tasks are tagged by category (bug/red, devops/blue, design/white, personal/pink, …) and the terminal tints to match. The active set is presettable (`minimal`/`web`/`data`/`ops`/`full`).
- **Closed-task listing** — `/todo closed [N]` and `/todo all` page through your history.
- **Multi-task `/done` and `/todo -s`** — close or jump into several tasks at once with a comma-separated list (`/done 1,2,5`, `/todo 1,2,5 -s`).
- **Optional enforcement gate** — a file edit in an untracked session can be made to block the turn from ending until work is tracked (self-healing, opt-out).
- **Parallel in-project worker delegation** — spawn `claude` workers inside a repo (with its CLAUDE.md, hooks, MCP, skills), one per task, resumable across turns.
- **Opt-in update check** — off by default; when on, at most one version check to GitHub per day, sending no task data.
- **Local-only, no telemetry** — all data lives under your config dir; by default there are zero network calls.

## Table of Contents

- [Why Task Station (vs native Tasks)](#why-task-station-vs-native-tasks)
- [Install](#install)
- [Commands](#commands)
- [Categories & terminal tint](#categories--terminal-tint)
- [Delegate — in-project workers](#delegate--in-project-workers)
- [Desktop bridge (MCP)](#desktop-bridge-mcp)
- [Configure](#configure)
- [Data & privacy](#data--privacy)
- [How it works](#how-it-works)
- [Update](#update)
- [Uninstall](#uninstall)
- [If you're Claude and someone asked you to install this](#if-youre-claude-and-someone-asked-you-to-install-this)

## Why Task Station (vs native Tasks)

Claude Code's native **Tasks** are the agent's *internal* scratchpad (stored in `~/.claude/tasks/`, no user-facing list). **Task Station is the human-facing console on top**: a persistent `/todo` dashboard you control, where each task pins to a resumable session you can reopen, auto-categorised + colour-tinted, with parallel worker delegation. They're complementary — native Tasks tracks the agent's steps; Task Station tracks *your* work across sessions.

Because `/todo` output lands in the session as context, Task Station doubles as a **shared map between you and Claude**: the board isn't just for you to read — Claude sees every tracked task and can connect them. Surface the board and Claude can plan and drive work that spans many tasks and repos at once (a migration touching several domains, say), instead of treating each task in isolation.

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

## Commands

Every command works in two forms: the namespaced `/task-station:todo` / `/task-station:done` (registered automatically, always available) and the bare `/todo` / `/done` aliases (**opt-in** — `task-station config --bare-cmds on`).

- **`/todo`** — list all tasks (not-closed first, then by recent activity) as two Markdown tables, rendered directly by the engine (`render --format md`). Each not-closed row leads with the **status glyph** (`◦` open / `●` active) before the number, then the task's stable number, category `<emoji> [TAG]`, effort gauge, last activity, and a `⧉N` marker when more than one live session is attached to the same task. Closed tasks fill the second table.
- **`/todo <n>`** — open a task by its stable number (or an id prefix) and **resume it into the current session**; your next message continues it. Reopens the task if it was closed.
- **`/todo <n> -s`** — same attach/reopen, but jump **straight into the task's pinned session in a fresh Terminal window** (no recap; the window you typed in is left untouched). The `-s` may sit on either side of the number (`/todo -s <n>` works too). **Never taints the wrong conversation:** the session you typed `-s` in and any **skipped** sessions are excluded as resume targets — if no valid session remains, `-s` **fresh-starts** a clean, auto-attaching session (`claude --session-id <uuid>`, pre-bound to the task) rather than resuming the conversation you jumped from.
- **`/todo 1,2,5 -s`** — **multi-jump**: a comma-separated list jumps into several tasks at once, opening **one window per task**. A bad ref in the list is reported but doesn't abort the others.
- **`/todo closed [N]`** / **`/todo all`** — page through closed tasks: `closed` shows the 20 most recent, `closed N` shows N, `all` shows every closed task.
- **`/done`** — close the task **this session** is working on and detach it; the session's terminal window auto-closes ~1s later.
- **`/done <n>`** — close **any** task by its stable number or id from anywhere — you don't have to be in that task's session. Leaves this window open.
- **`/done 1,2,5`** — **multi-close**: a comma-separated list closes several tasks at once, printing **one result line per task**. A bad ref is reported but doesn't abort the others.
- **`/repos`** / **`/repos show`** — print the hub **repo index** (one block per repo under your workspace roots). **`/repos <term>`** ranks repos by relevance to route a fuzzy task; **`/repos --refresh`** rescans; **`/repos --json`** emits the structured list. See [Repo index for routing](#repo-index-for-routing). (Bare `/repos` is part of the same opt-in as `/todo`/`/done`.)
- **`/task-station:config`** — view or change settings; run one-time setup (tint profiles, delegation policy, bare-command install). See [Configure](#configure).

### Status controls

A task's **status** moves through `open (◦) → active (●) → closed`; these set the board states explicitly (closing goes through `/done`):

- **`status --task <ref> [open|active]`** — show or set a task's status. With no value it reports the current status; `open`/`active` sets it (idempotent). Closing is via `/done`, not here.
- **`create --active`** — start a task `active` (`●`) instead of the default `open` (`◦`) — for when work has already begun.
- **`attach --note '<text>'`** — append a timestamped note to the task's activity log while (re)attaching. This is how the **fold, don't fork** rule works: a follow-up question on a task already on the board attaches with `--note` instead of spawning a sibling task.
- **Auto-promotion** `open → active` happens (idempotently) on: `delegate … --worktree` for the task, a **file edit** in an attached session (`PostToolUse`), or the manual `status`/`create --active` above. A closed task is never resurrected by an edit; `/done` closes from open or active, and reopening a closed task returns it to `open`.

### Session & resume controls

Claude attaches/creates tasks through the engine; these flags govern **which session a task resumes into** so `/todo <n> -s` always lands in the right place:

- **`create --no-attach`** — create a task with **empty sessions** and no session→task link. The supported "spin off a task for later" primitive: `/todo <n> -s` later **fresh-starts** a clean session. (`--session` is optional here.)
- **`create … --session <busy-session>`** — when the session is itself a **substantive tracked** conversation (≥ 3 messages, linked to a task), `create` **defaults to no-attach** and warns, so a busy parent conversation is never silently made the new task's resume target. Pass **`--attach`** to force binding it.
- **`detach --session <s> [--task <t>]`** — remove a session from a task's resume candidates: drops it from `sessions[]`/`session_meta`, clears `pinned_session` if it pointed there, and clears the session→task link. `--task` selects the task; without it, the session's linked task is used. Idempotent.
- **`pin --new [--task <t>]`** — pin an **unborn** session: mints a fresh uuid and records it so `/todo` emits `claude --session-id <uuid>` — opening it *becomes* the task's pinned session. Use it to deliberately start the task in a clean session next time (re-pin a fresh session to save tokens) without first having that session exist.

## Categories & terminal tint

If `categories.py` is present (it ships with the plugin), every task carries a `color` from a taxonomy (bug/red, code-review/orange, devops/blue, design/white, personal/pink, AI-config/silver, …); `/todo` appends a `<emoji> [TAG]` after each task and prints a legend. Each colour name is also a zsh alias that switches the Terminal.app profile, so on attach / create / resume Claude runs `zsh -ic '<color>'` to tint the terminal to the task's category.

**The dot is slot-canonical** — each colour slot owns its emoji, so a custom category (or an override) supplies only `tag` + `label`; the icon follows from the colour. **Presets & enabled set:** the active categories are seeded-but-removable — `config --categories preset <minimal|web|data|ops|full>` switches the set, and `config --enable/--disable <key>` toggles individual slots. Every preset keeps the universal core (BUG · AI CONFIG · PERSONAL · GENERAL); `⚫ GENERAL` is permanent. See [`CATEGORIES.md`](CATEGORIES.md).

**Skills tint immediately:** when a prompt invokes a slash command mapped in `SKILL_COLORS` (e.g. `/review` or `/security-review` → orange), the `UserPromptSubmit` hook tints the terminal synchronously *before Claude responds*, so the colour applies the instant the skill runs.

**All of this is isolated in `lib/categories.py`** — `task-station.py` imports it defensively and runs as a plain, colourless tracker without it. The taxonomy ships as defaults in `lib/categories.py`; override or extend it without touching the shipped file via `task-station-data/config.json` (survives `/plugin update`) — see [`CATEGORIES.md`](CATEGORIES.md). Full taxonomy, wiring, tint modes, and the opt-out levels are in [`CATEGORIES.md`](CATEGORIES.md).

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

### Repo index for routing

A hub `claude` session launched from `~` **can't auto-load anything inside a repo** —
so when you hand it a fuzzy task ("fix the billing rounding bug") it has no way to know
which repo that lives in. The **repo index** solves the routing half of delegation: an
on-demand, hub-side map of the repos under your workspace roots, so the hub can pick the
right repo(s) *before* spinning up a worktree.

```bash
/repos                    # print the index (one block per repo) — or first-run setup
/repos --refresh          # rescan the roots + rewrite the index (sends NOTHING by default)
/repos billing invoice    # rank repos by relevance to these terms
/repos config             # list the include/exclude manifest (every repo + its flags)
/repos exclude marketing  # drop a repo from the index (index:false)
/repos enrich volt-api    # opt ONE repo in to model enrichment (the only egress path)
/repos --refresh --dry-run # report which enrich:true repos WOULD be sent — send nothing
/repos --json             # structured list, for tooling
```

> **Enrichment is opt-in.** A normal `/repos --refresh` sends **nothing** off-machine. See [Privacy / data egress](#privacy--data-egress).

It lives next to the task store at `<data_dir>/repos.{md,json}` (plus a small
`.repos-cache.json`) — **not** in `tasks.db` (repos aren't tasks) and **not** as per-repo
committed files. There is **no SessionStart injection**; it's read only when you ask.
Discovery roots come from `--workspace-dirs` / `TASK_STATION_WORKSPACE_DIRS`, defaulting to
`~/Workspace` + `~/Workspace-Other`.

**Cards are fully auto-filled** — overrides are optional, not required:

- **Deterministic (no model):** name, absolute path, `origin` remote, `ado_project` (Azure
  DevOps `…/_git/` project, or GitHub `owner/repo`), and `status` (`active`/`stale`/`unknown`
  from the last commit date vs `REPO_STALE_MONTHS`, default 6).
- **`stack` is detected by content**, not just root manifests: a `git ls-files` **extension
  histogram** (`.py`→python, `.cs`→dotnet, `.sql`→sql, `.ts`→typescript, `.go`→go, `.tf`→terraform,
  …) **unioned** with **config/tooling signals** (`Dockerfile`→docker, `.github/workflows/`→github-actions,
  Flyway config / `*.sql` migrations→flyway, `*.tf`→terraform) and the root manifests. So a
  SQL/Flyway repo resolves to `sql, flyway` and a manifest-less `lib/`-only repo to `python, shell`,
  where the old root-manifest-only check found nothing.
  - The extension/filename lookup is **GitHub-Linguist-derived**: `lib/stack_map.py`
    (`EXT_TO_STACK` + `FILENAME_TO_STACK`, ~900 extensions) is generated from Linguist's
    `languages.yml`, so the whole programming-language long tail is covered (Swift, Kotlin, Ruby,
    PHP, …) while the ergonomic labels above are preserved by an alias overlay. Prose/markup/data-
    ambiguous extensions are excluded so doc/data formats can't be misread as obscure programming
    languages (`.md` is Markdown, not GCC Machine Description). The committed module is pure stdlib;
    regenerate it with `python3 tools/gen_stack_map.py` (the source `languages.yml` is vendored but
    gitignored).
- **`summary` + `keywords` are deterministic by default**, and **opt-in** for model enrichment.
  By default they're filled offline from the README's first paragraph — **no model call**. A repo's
  content is sent to a cheap model (Haiku, via the headless `claude -p … --output-format json` CLI)
  **only** when you flip its manifest `enrich` flag on (`/repos enrich <name>`). Even then the call is
  **fingerprint-gated**: each repo has a `fingerprint = sha1(remote + sorted top-level entries +
  sha1(README) + sha1(each root manifest))[:12]` that moves only on identity/structure change — not on
  ordinary commits — so an enriched repo is re-sent only when new or structurally changed; the rest are
  served from `.repos-cache.json`. If the call fails for any reason (CLI not on `PATH`, no network,
  timeout, malformed JSON) it falls back to the **deterministic** README-derived summary — the index
  **always** builds and the command never errors out. `--no-llm` forces the deterministic path even for
  `enrich:true` repos; `TASK_STATION_REPO_ENRICH=off` (or `repo_enrich:false`) hard-disables all egress
  globally. See [Privacy / data egress](#privacy--data-egress).
- **Precedence: override > model > deterministic-fallback.** Hand-authored prose
  (`summary`/`keywords`/`domain`, plus a `status` override) in `<data_dir>/repos.overrides.json`
  (keyed by repo name) **wins** and **survives every refresh** — discovery never writes it.

The `delegating-work` skill uses this automatically: when the target repo is ambiguous it
runs `repos --refresh --quiet`, ranks repos by the task's own words, and picks before
resolving a worktree.

> **Scaling.** The schema is built for 100+ repos: `match()` already returns a ranked list, so it
> doubles as a stage-1 top-K pre-filter (only the top cards' prose need be read into context), and the
> fingerprint cache already avoids redundant model work — a future `--refresh` debounce is the only
> remaining additive piece.

### Privacy / data egress

The repo index is **local-first and opt-in for any model egress.** What leaves your machine is fully
under your control:

- **Enrichment is OFF by default, per repo.** A normal `/repos --refresh` makes **zero** model calls
  and sends **nothing** off-machine — it builds the index entirely offline (filesystem + `git` +
  README first paragraph). A repo's content is sent to the model **only** when you opt that repo in
  with `/repos enrich <name>`.
- **The manifest is the single include/exclude surface.** `task-station-data/repos.config.json` is
  auto-maintained: every discovered repo appears with `{ index: true, enrich: false }` defaults; new
  repos are added on refresh and vanished ones pruned. Flip flags by name — no JSON editing:
  - `/repos config` — list every repo and its flags.
  - `/repos include <name>` / `/repos exclude <name>` — `index` controls whether a repo appears in the
    index at all.
  - `/repos enrich <name> [on|off]` — `enrich` controls model egress; it's the **only** path by which a
    repo's content reaches the model.
- **`.task-station-ignore` marker.** Drop an empty file by that name at a repo root and the repo is
  excluded from discovery entirely, regardless of the manifest. It travels with the repo, so a repo
  owner can self-exclude.
- **Bounded, transparent input.** When enrichment runs for an `enrich:true` repo, the prompt is bounded
  to: repo name, `ado_project`, detected stack, the README top (~80 lines), and a `git ls-files`
  **name** sketch. Arbitrary file **contents** are **never** read — and a denylist guard keeps
  secret-bearing names (`.env`, `*.pem`, `*.key`, `secrets*`, `credentials*`, `.npmrc`, private keys, …)
  out of the prompt entirely. `--refresh` prints exactly which repos are having content sent
  (`enriching (sending README+tree NAMES): …`), and `--refresh --dry-run` reports what *would* be sent
  without sending anything.
- **Deterministic refreshes don't clobber.** A deterministic refresh preserves an existing
  summary/keywords (model- or override-derived) rather than overwriting it; use `--re-summarize` to
  force regeneration. `--no-llm` forces the deterministic path even for `enrich:true` repos, and
  `TASK_STATION_REPO_ENRICH=off` hard-disables all egress globally.

> **Honest nuance: listing ≠ sending, but indexing ≠ fully private.** An `enrich:false` repo never has
> its README/tree sent during `--refresh`. But an `index:true` repo's deterministic **card** (name,
> path, stack, status, README-derived summary) lives in `repos.md`/`repos.json`, which the hub reads
> **into the model's context at routing time** to pick a repo for a task. So an indexed repo's card
> still reaches the model when you route work. To keep a repo **fully** off the model, `exclude` it
> (`index:false`) or drop a `.task-station-ignore` marker — then it never enters the index at all.

## Desktop bridge (MCP)

Task Station ships an **MCP server** so **Claude Desktop** (or any MCP client) can create, read, and
update tasks in the **same local store the CLI uses** — one `tasks.db`, two front doors. Raise a task
in a Desktop conversation and it shows up in `/todo` in Claude Code, and vice-versa. WAL is already
on, so concurrent Desktop + CLI access is safe.

The `mcp` SDK is an **optional, server-only dependency**: the core plugin, the hooks, and the whole
test suite stay stdlib-only. You only need it to *run* the bridge.

**One-time setup**

1. Install the SDK (the only extra dependency):

   ```
   pip install mcp
   ```

2. Add the server to your Claude Desktop config
   (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS). Point it at the
   **stable engine symlink** — `~/.claude/task-station-engine` always tracks the active install, so
   the path survives `/plugin update`:

   ```json
   {
     "mcpServers": {
       "task-station": {
         "command": "python3",
         "args": ["/Users/YOU/.claude/task-station-engine/mcp_server.py"]
       }
     }
   }
   ```

   Replace `/Users/YOU` with your home directory — Claude Desktop does **not** expand `~` in `args`,
   so use the absolute path. (A ready-to-edit snippet lives at
   [`claude_desktop_config.json`](claude_desktop_config.json).) Restart Claude Desktop to load it.

   The bridge writes where the CLI reads automatically — it honors `TASK_STATION_HOME` /
   `CLAUDE_CONFIG_DIR` exactly like the CLI. To point Desktop at a non-default store, add
   `"env": { "TASK_STATION_HOME": "/path/to/store" }` to the server block.

**What it exposes**

| Kind | Name | What it does |
|------|------|--------------|
| Tool | `list_tasks(status="all-open")` | The Markdown board (byte-for-byte the CLI's `--format md`). `status`: `all-open` (default, open+active) · `open` · `active` · `closed` · `all`. |
| Tool | `create_task(title, summary, category?, effort?, source?)` | Creates an `open (◦)` task. `source` records the originating Desktop conversation ref/URL (surfaced in `get_task`). |
| Tool | `get_task(ref)` | Full detail by task number or id: status, category, effort, **source link**, and activity log. |
| Tool | `set_status(ref, status)` | Moves a task along `open → active → closed`. |
| Tool | `add_note(ref, text)` | Appends a timestamped note to the task's activity log. |
| Prompt | `todo` | The rendered board — the Desktop analog of `/todo`. |
| Resource | `task://<seq>` | A single task's full detail; attach one to a Desktop conversation via the **+** menu. |

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
- `--categories` — show the current enabled category set + available presets. `--categories preset <minimal|web|data|ops|full>` switches the active set; `--categories edit` prints the `config.json` path so you can open it and customize categories, `skill_colors`, etc.
- `--enable <key>` / `--disable <key>` — toggle a single category slot on/off (accepts a key, emoji, or `[TAG]`). Disabling `⚫ GENERAL` is refused — it's permanent.
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
| Auto terminal title `#<seq>: <title>` on attach | on | `TASK_STATION_TITLE=off` (or `config --title off`) |
| Bare `/todo` + `/done` install | **off** (opt-in) | `TASK_STATION_BARE_CMDS=on` to enable |

**Terminal tint — two modes:**

- **auto** *(default, zero-setup)* — writes a direct escape sequence to set the background colour: iTerm2 uses `SetColors`, Terminal.app uses OSC 11. Works out of the box; no profiles or aliases needed.
- **profile** — runs `zsh -ic '<color>'` to switch Terminal.app profiles via named aliases. Enable with `task-station config --tint-profiles` (iTerm2: no-op, already zero-setup).

Tinting is auto-detected: the engine reads `$TERM_PROGRAM` / `$ITERM_SESSION_ID` to pick iTerm2 vs Terminal.app vs none. Once a session is attached, the terminal tab/window title is auto-set to `#<seq>: <title>` (the same string feeds the Claude session name at start); the `/todo <n> -s` new-window jump is on by default on macOS (auto-detected). Disable the title with `task-station config --title off` (or `TASK_STATION_TITLE=off`).

**Bare commands:** `/todo` and `/done` are marker-guarded user-level commands that forward to the engine. They are **not installed by default** — run `task-station config --bare-cmds on` (or set `TASK_STATION_BARE_CMDS=on`) to opt in. The namespaced form `/task-station:todo` and `/task-station:done` always exist regardless and work out of the box.

## Data & privacy

- All task data is stored **locally** under `${CLAUDE_CONFIG_DIR:-~/.claude}/task-station-data/` (a single indexed SQLite DB `store/tasks.db`, plus `config.json`).
- **No telemetry. By default there are no network calls.** An optional update check (off by default; enable with `task-station config --update-check on`) makes at most one version request to GitHub per day — it sends no task data.
- The delegate feature spawns local `claude -p` workers — that's your own Claude usage, no third party.

## How it works

### Hooks

Declared in `hooks/hooks.json`, run at your trust level:

- **`SessionStart`** (`hooks/on_session_start.sh`) — maintains the `~/.claude/task-station-engine` symlink; self-registers a status-line segment at `~/.claude/statusline.d/50-task-station.sh`; sets the session name `#<seq>: <title>` for attached sessions; shows a one-time setup nudge on first run; and — **only if you have opted in** — installs bare command aliases under `~/.claude/commands/`.
- **`UserPromptSubmit`** (`hooks/on_user_prompt.sh`) — re-points the engine symlink at the active install (so the bare `/todo`/`/done` follow `/plugin update` without a restart), applies the per-category terminal tint (when enabled), auto-sets the terminal tab/window title to `#<seq>: <title>` for attached sessions (when enabled), and injects compact task-tracking guidance into each prompt so Claude knows to attach or create a task.
- **`PostToolUse`** on `Write|Edit|NotebookEdit` (`hooks/on_post_tool.sh`) — for an **attached** session, a file edit auto-promotes an open task to active (work has started); for an **untracked** session it fires a one-shot reminder the first time it edits a file (part of the optional enforcement gate).
- **`Stop`** (`hooks/on_stop.sh`) — refuses to end the turn while a session has edited files but tracked no task (self-healing, capped at two blocks). The other half of the optional enforcement gate.

### Tracking, gating, and tasks

- **Auto-track as open, fold don't fork.** On each user message, a
  `UserPromptSubmit` hook injects guidance telling Claude to track the topic from
  the **first prompt** — even a plain question becomes an `◦ open` task (it
  auto-promotes to `● active` when work starts). Before creating, Claude scans the
  board: if the prompt continues an existing task it **attaches and appends the
  prompt as a note** (`attach --note`) rather than spawning a sibling, so related
  questions accumulate under one task. Only a genuinely new topic creates a task;
  a **skipped** session stays silent. The per-prompt nudge is deliberately
  **compact** (on-board task list, the track-as-open + fold rule, the attach/create
  commands, a one-line colour legend); the full rules and colour-picker guidance
  live in `task-station.py guidance`, fetched on demand, to keep the recurring
  token cost low. When Claude attaches or creates a task it announces it in one
  short line (e.g. "📋 Tracking this as a new task: …").
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
  recap**, and the window you typed `/todo` in is left untouched. A
  comma-separated list (`/todo 1,2,5 -s`) jumps into several tasks at once,
  opening one window per task. The new window
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
  here). A comma-separated list (`/done 1,2,5`) closes several tasks at once,
  printing one result line per task; a bad ref is reported but doesn't abort the
  others. Handy straight from the `/todo` list: see the numbers, close any of them
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

### Files and directories created or used

All paths are under your config dir (`${CLAUDE_CONFIG_DIR:-~/.claude}`) unless noted:

| Path | What it is |
|---|---|
| `~/.claude/task-station-data/` | Local task storage: an indexed SQLite DB (`store/tasks.db`), plus `config.json`, `workers.json`, and `pending-briefs/` |
| `~/.claude/task-station-engine` | Symlink to the plugin's `lib/` — a stable, version-independent handle refreshed every session and re-pointed on each prompt |
| `~/.claude/statusline.d/50-task-station.sh` | Self-registered status-line segment (harmless if unused) |
| `~/.claude/commands/{todo,done,repos}.md` | **Only if you run `task-station config --bare-cmds on`** (opt-in; marker-guarded, never clobbers a pre-existing command) |
| `<data_dir>/repos.{md,json}` + `.repos-cache.json` | Hub repo index + enrichment cache, written on demand by `/repos --refresh`; `repos.overrides.json` (hand-authored, never written by discovery) is read if present |
| `~/.zshrc` (tint aliases) | **Only via the explicit `task-station config --tint-profiles` command you run** |
| `~/.claude/CLAUDE.md` (delegation policy block) | **Only via the explicit `task-station config --policy on` command you run** (fenced, 100% reversible with `--policy off`) |

The namespaced `/task-station:todo` and `/task-station:done` commands are registered by the plugin system automatically and always work out of the box. The bare `/todo` and `/done` aliases are **opt-in** — run `task-station config --bare-cmds on` to install them.

### Storage

A single indexed SQLite database at `<data_dir>/store/tasks.db` holds tasks and
session→task links — queried by index, so listing, counting, and the per-prompt
"is this session tracked?" check stay fast as the board grows instead of scanning a
file per task. All writes are transactional (WAL mode). If `sqlite3` is somehow
unavailable (it's in the Python standard library, so effectively never), it falls
back to a JSON-file store. The data directory defaults to
`${CLAUDE_CONFIG_DIR:-~/.claude}/task-station-data/`; set `$TASK_STATION_HOME` to
override. It is machine-local and not tracked by the plugin — task data persists
across `/plugin update`.

### Status line (optional)

`task-station.py whoami --session <id> --statusline` prints a ready-to-display, ANSI-colored one-line segment for the session's attached task — `#<seq>  <dot> [TAG]  <title>` — and nothing when the session has no task. Add `--width <N>` to truncate the title so the whole segment fits `N` columns (`0` = no limit). It's self-contained: it carries its own colors and knows nothing about the bar that renders it, so it drops into any status line (tmux, powerline, a custom prompt, or a Claude Code `statusLine` command).

```bash
$ task-station.py whoami --session 5c8edf12 --statusline --width 0
#42  🔵 [DEVOPS]  Wire up the deploy pipeline
```

The plugin maintains a stable symlink `~/.claude/task-station-engine → <plugin>/lib/` (refreshed on every `SessionStart` and re-pointed on each prompt) so callers outside the plugin context — delegate invocations, the status line — always find the engine without chasing a versioned cache path.

To show the current task in the Claude Code status bar, add one line to `settings.json`:

```json
"statusLine": { "type": "command", "command": "bash ~/.claude/task-station-engine/statusline.sh" }
```

`~/.claude/task-station-engine/statusline.sh` is the self-contained script (`lib/statusline.sh`) exposed through the stable symlink. It reads the session JSON on stdin (as Claude Code passes it) and delegates to `task-station.py whoami --statusline`. No `$CLAUDE_PLUGIN_ROOT` dependency — it works in any context once the symlink exists.

### Files (what each ships)

**`lib/task-station.py`** — the engine: task storage, `/todo` and `/done`, the hooks' logic, plus the `whoami` (incl. the `--statusline` segment provider) and `update` commands.

**`lib/mcp_server.py`** — the [Desktop bridge (MCP)](#desktop-bridge-mcp): an MCP server exposing the task store to Claude Desktop / any MCP client over the *same* `tasks.db` the CLI uses. Stdlib tool logic drives the engine; the FastMCP wrapper is lazily imported, so `mcp` is an optional, server-only dependency (`pip install mcp` to run it). Exposed via the stable `~/.claude/task-station-engine/mcp_server.py` symlink.

**`lib/store.py`** — the storage backend behind `task-station.py` (indexed SQLite `tasks.db` by default, JSON file-per-task fallback). Parameterised by store dir; never reads the environment itself.

**`lib/categories.py`** — optional colour-taxonomy + terminal-tint plugin; `task-station.py` runs fine without it. Ships with defaults; users customize via `task-station-data/config.json` without editing this file. See [`CATEGORIES.md`](CATEGORIES.md).

**`lib/paths.py`** — resolves the mutable data directory (`${CLAUDE_CONFIG_DIR:-~/.claude}/task-station-data/`, overridable with `$TASK_STATION_HOME`) and handles legacy migration detection.

**`lib/repo_index.py`** — the hub [repo index](#repo-index-for-routing): deterministic discovery (name/path/remote/`ado_project`/`stack`/`status`), hand-authored overrides merge, the relevance ranker, and `build_index` (writes `<data_dir>/repos.{md,json}`). Powers the `repos` subcommand and the `delegating-work` routing step.

**`hooks/on_session_start.sh`** — `SessionStart` hook. Surfaces open tasks (or the attached one) and auto-sets the session name to `#<seq>: <title>`.

**`hooks/on_user_prompt.sh`** — `UserPromptSubmit` hook. Re-points the engine symlink, attaches/nudges the session, tints the terminal for skill-mapped prompts, and auto-sets the terminal tab/window title to `#<seq>: <title>` once attached.

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

### Limitations (Claude Code harness)

- `/todo` is a numbered text list, not an arrow-key picker — slash-command bash
  has no interactive TTY, so tools like `fzf` can't render.
- "Resume" adopts a task into your **current** session; it can't jump back into
  a different past chat transcript. To reopen the literal original chat, use
  `claude --resume <session-id>` from your shell.

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
