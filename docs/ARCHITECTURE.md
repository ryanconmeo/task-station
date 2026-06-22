# Task Station — Architecture

The internals the [README](../README.md) defers here. Task Station is **stdlib-only**
Python (3.9+) plus a few POSIX `bash` hooks — no third-party dependencies, no build
step. The engine (`lib/task-station.py`) is the CLI every command and hook calls;
`categories.py` is an optional plugin it imports defensively (delete it and the tracker
degrades to a plain, colourless board).

| Area | Files |
|---|---|
| Engine + render/resume/dedup | `lib/task-station.py` |
| Storage backends | `lib/store.py`, `lib/paths.py` |
| Categories + tint | `lib/categories.py`, `lib/term.py`, `lib/origin-tty.sh` |
| Hooks | `hooks/hooks.json`, `hooks/on_*.sh` |
| Delegation | `lib/delegate/{delegate.py,worktree-up.sh,POLICY-TEMPLATE.md}` |
| Repo index | `lib/repo_index.py`, `lib/stack_map.py` (generated) |
| Desktop bridge | `lib/mcp_server.py`, `lib/mcp_launcher.py`, `lib/setup.py` |

---

## (a) Storage & data model

A task is **one JSON dict**. The default backend (`store.py::SqliteBackend`) keeps the
full dict in a `data TEXT NOT NULL` column of a single indexed `<data_dir>/store/tasks.db`;
the typed columns alongside it (`seq`, `status`, `updated_ts`, …) exist only to index and
sort, so no field is ever dropped. `all_tasks()` runs on **every** user message via the
hooks, so listing/sorting are indexed queries rather than hundreds of file reads.

```
PRAGMA journal_mode=WAL        # concurrent CLI + Desktop + hooks don't lock each other
PRAGMA busy_timeout=5000
PRAGMA synchronous=NORMAL
tasks(id PK, seq, title, summary, status, color, effort,
      created_ts, updated_ts, pinned, sessions, session_meta, log, data)
links(session PK, task_id, n, edited, blocked)   # one row folds every per-session sidecar
```

The `links` table folds what the JSON store kept as sidecar files: the session→task
pointer, the `.n` miss counter, and the `.edited`/`.blocked` gate markers.
`JsonBackend` (file-per-task) is a belt-and-suspenders fallback used **only** if `sqlite3`
(stdlib) is somehow unavailable. **There is no migration, ever** — `SqliteBackend` uses an
existing `tasks.db` or creates a fresh empty one.

**Lifecycle — one field, three states:** `open ◦ → active ● → closed`. `task_status()`
treats any missing/unknown value as `open` (back-compat with pre-lifecycle tasks).
`set_status()` only moves between the settable board states (`open ⇄ active`) and is
idempotent; **closing goes through `/done`, not `set_status`**, so a typo can't mislabel a
task. `promote_active()` lifts `open → active` when work starts (a file edit in an attached
session, a `--worktree` delegation, the manual `status` command, or `create --active`) and
**never resurrects a closed task**.

**`seq` vs `uuid`:** every task has a `uuid4` `id` (the stable internal key) and a `seq` —
a permanent, never-reused, creation-order integer that is the number you see in `/todo` and
type as `/todo <n>`. `ensure_seqs()` backfills missing seqs idempotently in creation order;
a task keeps its number as others are added, closed, or reordered by recent activity.

**Data dir lives OUTSIDE the plugin cache.** `paths.data_dir()` resolves, in order:
`TASK_STATION_HOME` → `CLAUDE_CONFIG_DIR/task-station-data` → `XDG_STATE_HOME/task-station`
→ `~/.claude/task-station-data`. A plugin installs to a *versioned* cache dir that is
replaced on every `/plugin update`; anchoring state outside it is why updates never wipe
your board or history (see [README → Why Task Station](../README.md#why-task-station)).
Tests set `TASK_STATION_HOME` to a tmpdir for isolation.

## (b) Hooks

Declared in `hooks/hooks.json`. Every hook no-ops outside plugin context
(`CLAUDE_PLUGIN_ROOT` unset) and **early-exits when `TASK_STATION_SUPPRESS` is set** —
delegate spawns workers with `TASK_STATION_SUPPRESS=1` because task tracking and tinting are
the *hub's* job, not the worker's.

| Hook | Script | What it does |
|---|---|---|
| `SessionStart` | `on_session_start.sh` | Refresh the `~/.claude/task-station-engine` symlink → the active `lib/`; self-register the status-line segment; (opt-in) install bare `/todo` `/done` `/repos` aliases; emit the open-tasks / attached-task context + one-time setup nudge; set the session title; **tint the originating window** to the attached task's category (`session-tint`). |
| `UserPromptSubmit` | `on_user_prompt.sh` | Re-point the engine symlink (so bare aliases track an in-session `/plugin update`); **tint instantly when a known skill runs** (`prompt-tint` → escape written to the origin TTY); auto-title the tab `#<seq>: <title>`; inject the compact track-or-fold guidance. |
| `PostToolUse` (`Write\|Edit\|NotebookEdit`) | `on_post_tool.sh` | Attached session → auto-promote `open → active`; untracked session → a **one-shot** reminder the first time it edits a file (gated by the `edited` marker, ~one injection per session). |
| `Stop` | `on_stop.sh` | Refuse to end the turn while the session has edited files but tracked no task (`{"decision":"block"}`). Self-healing (attach/create/skip/`/done` clears it) and **capped at two blocks** so a non-complying loop can't wedge the session. |

The `PostToolUse` + `Stop` pair is the optional enforcement gate; the others are the
advisory rail. See [README → Commands & components](../README.md#commands--components).

## (c) Resume logic

`resume_command(task, current_session)` hands back a `cd <dir> && claude --resume <sid>`
one-liner for the session that actually holds the task's context. The guarantees:

- **Only this task's own sessions.** Every hub shares the `~/.claude/projects` bucket, so a
  whole-bucket fallback or `claude --continue` could resume an *unrelated* task — we never
  do that. Resume only ever targets a session recorded on this task.
- **cwd self-corrected from the transcript.** The resume directory is read from the
  transcript itself (`_session_cwd` after locating it via `_find_session_path`), not from
  whatever cwd `/todo` happened to capture — so a session launched from `~` but worked in a
  worktree still resumes in the right place.
- **Never taint the current session.** The conversation you jumped *from* is excluded hard;
  resuming it is the tainting bug we avoid.
- **Substance floor.** Among live transcripts it prefers the most recent with
  `≥ SUBSTANCE_FLOOR (3)` user messages, so a 1–2 message `/todo <n>` peek never displaces
  the real working session; only if none clear the floor does it take the most recent of any.
- **Pinning.** A `pinned_session` wins PK-style (resume that exact session, cwd
  self-corrected); a `pin --new` preborn pin with no transcript yet is honoured by emitting
  `--session-id <pin>` so the window that opens *becomes* that session.
- **Fresh fallback.** No findable live transcript → `cd <cwd> && claude` (fresh), **never
  `--continue`**.

**`-s` jump window** (`_open_jump_window` → `open-session-window.sh`): macOS/Terminal.app-only
and best-effort — it opens a **new** window running the resume one-liner and leaves the
current window untouched; any failure (not darwin, missing `osascript`, absent script) just
prints the command for you to run by hand.

## (d) "Fold don't fork" dedup

`create` resists spawning a near-duplicate of an existing **open** task. For each open
candidate it scores the normalised title tokens of the new title against the candidate:

```
score = max( jaccard = |A∩B| / |A∪B|,
             containment = |A∩B| / |A|   (only when |A∩B| ≥ 2) )
match when score ≥ 0.6
```

A **numeric-identity guard** runs first: if the new title carries numbers (a PR/bug/story #
via `_norm_nums`) and a candidate shares *none* of them, they're different work items and the
candidate is skipped — so "Auto-review PR 697" can't collide with an unrelated "Auto-review
PR 412" on the process words alone. On a match, `create` points you at `attach` instead;
`--force` overrides.

## (e) Categories & full-palette tint

Twelve slots in `categories.py`, each `{dot, tag, label}` plus a baked **Sands** palette:
`hex`/`hex_light`, `fg`, `bold`, `cursor`, `selbg`, and a 16-element `ansi` list.
`PERMANENT = black` (GENERAL can never be disabled). Presets `minimal/web/data/ops/full`
trim the enabled set; `SKILL_COLORS` maps a skill name → category for instant tinting.

`tint_escape(color, mode, term)` emits standard OSC so iTerm2 **and** Apple Terminal both
honour most of it (the `mode` argument is vestigial — profile mode was removed in 1.7.0 —
and ignored):

| Element | Escape |
|---|---|
| background | `ESC ] 11 ; <hex> BEL` |
| foreground | `ESC ] 10 ; <hex> BEL` |
| cursor | `ESC ] 12 ; <hex> BEL` |
| ANSI 0–15 | `ESC ] 4 ; <n> ; <hex> BEL` |
| selection | `ESC ] 17 ; <hex> BEL` |
| bold (iTerm only) | `ESC ] 1337 ; SetColors=bold=<hexNoHash> BEL` |

`term == "none"` or an unknown colour yields `""`; a slot that defines only a background
still emits just the background (back-compat for minimal taxonomies).

**Targeting the right window.** The hooks don't print escapes to stdout — they resolve the
*originating* TTY via `origin-tty.sh` and write there, so tinting is focus-proof.
Resolution order: `$CLAUDE_TTY` (export it in your shell rc — the most reliable) → on iTerm,
the session UUID in `$TERM_SESSION_ID` mapped to its `tty` via `osascript`.

**Overrides survive updates.** `_apply_overrides()` merges `config.json`'s `categories` over
the shipped defaults at import, so customisations outlive `/plugin update`. An override needs
only `{tag, label}` — the `dot` and the **full palette** (`fg`/`bold`/`cursor`/`ansi`/…) are
inherited from the slot; an explicit `dot` still wins; a brand-new key with no slot falls
back to the GENERAL dot. **Dark/light:** `hex_light` + `resolve_theme()` (`auto` follows the
OS via `defaults read -g AppleInterfaceStyle`, else a forced `dark`/`light`); the shipped
Sands palettes are theme-independent (`hex == hex_light`), so the setting mainly affects your
own `hex_light` overrides. See [README → Categories & terminal tint](../README.md#categories--terminal-tint).

## (f) In-project delegation

A hub session launched from `~` can't load a repo's `CLAUDE.md`, hooks, MCP servers, or
permissions. `lib/delegate/delegate.py` spawns a `claude` worker *inside* the target repo:

```
delegate.py run --repo <path>|--project <name> --task "<instructions>" \
  [--worktree NAME] [--branch BR] [--base REF] [--seq N] [--solo] [--label L] [--fresh] [--timeout S]
```

- **Worktree-isolated.** With `--worktree NAME` the worker runs in
  `<repo>-worktrees/<NAME>`, resolved-or-created on the fly by `worktree-up.sh` off the
  repo's **default branch** (`git symbolic-ref refs/remotes/origin/HEAD`, else `origin/main`).
  Mutations never touch your main checkout; `--project` deliberately refuses to resolve into
  a `*-worktrees` dir.
- **Crash-safe.** The worker's session UUID is **pre-registered in `workers.json` BEFORE
  launch** and passed as `--session-id`, so a timeout or kill never loses the conversation —
  the next call resumes the same session via the same `--seq`/`--project`.
- **One worker per (task, repo).** The registry is keyed so a re-delegation resumes rather
  than forks; resume one-liners surface in the task's detail view.
- **`acceptEdits` + allowlist inheritance.** Workers run `--permission-mode acceptEdits` and
  inherit the repo's own allowlist — a tool the repo hasn't allowlisted fails (a headless
  worker can't prompt; widen `.claude/settings.local.json` to fix). Workers also get
  `TASK_STATION_SUPPRESS=1` so their own hooks stay quiet.

## (g) Repo index

`/repos` (`repo_index.py`) builds a hub-side index used by delegate's `--project` shorthand.
Discovery and stack detection are **fully offline and deterministic**: a content-based scan
maps file extensions/filenames → stack labels via `stack_map.py` (distilled from GitHub
Linguist's `languages.yml` — a gitignored input; the generated module is committed and
import-free). `summary` + `keywords` are computed on-device by default.

LLM enrichment is the **only** egress path and is **off by default per repo**
(`manifest[name] = {index: true, enrich: false}`). Even when you opt a repo in with
`/repos enrich <name>` it is tightly bounded:

- **Fingerprint-gated** — `sha1(remote + sorted top-level entries + …)`; re-sent only when
  new or structurally changed, not on ordinary commits.
- **Name-only** — a bounded README excerpt + top-level file/dir **names**; arbitrary file
  *contents* are never read, and a **secret-name denylist** guards what's sent.
- **Kill-switches** — `--no-llm` (force deterministic), `--dry-run` (report, send nothing),
  and `TASK_STATION_REPO_ENRICH=off` / `repo_enrich:false` (hard-disable all egress). The
  index **always** builds deterministically; enrichment is a layer on top that never errors
  the build. See [README → Data & privacy](../README.md#data--privacy) and
  [PRIVACY.md](../PRIVACY.md).

## (h) Desktop MCP bridge

`lib/mcp_server.py` is a **dependency-free, hand-rolled MCP server** — stdio JSON-RPC built
from `json` + `sys` only (no SDK, no `pip`) — that exposes the task store to Claude Desktop
over the **same local `tasks.db`** the CLI uses (WAL makes concurrent Desktop + CLI access
safe). Five tools, plus a `todo` prompt and `task://<seq>` resources:

| Tool | Purpose |
|---|---|
| `list_tasks` | The board Chat sees (also drives the `todo` prompt). |
| `create_task` | Create from a Desktop chat (stores the conversation ref). |
| `get_task` | Full detail for a `seq`/id. |
| `set_status` | Move `open ⇄ active`/close. |
| `add_note` | Append to the activity log. |

`lib/mcp_launcher.py` is the **stable, self-resolving launcher** copied to
`<data_dir>/mcp-launcher.py` by `config --desktop-bridge on`. Desktop is pointed at *this*
version-independent path — never the volatile engine symlink — and on every launch it
resolves the installed `mcp_server.py` (`installed_plugins.json → installPath`, else the
highest cache version) and `os.execv`s it, passing stdio straight through. That's what keeps
Desktop working across `/plugin update` and concurrent sessions. `setup.py` merges a single
entry into Claude Desktop's config (backed up first) and is fully reversible
(`--desktop-bridge off`). See [README → Claude Desktop bridge](../README.md#claude-desktop-bridge).

## (i) Reversible `CLAUDE.md` policy block

`config --policy on` (`setup.py`) writes a delegation-policy block into your global
`~/.claude/CLAUDE.md`, fenced by sentinel comments:

```
<!-- BEGIN task-station:delegation-policy (managed — task-station config --policy) -->
…policy text…
<!-- END task-station:delegation-policy -->
```

The write is **add-or-replace and idempotent**, always takes a `.bak` first, and records the
exact inserted substring plus its `sha256` in the setup manifest. `--policy off` removes
**exactly** that span and restores the file — but it is **hash-verified**: if the block was
hand-edited since install (hash mismatch) removal refuses (a no-op that warns), so the tool
never clobbers your edits. 100% reversible by design.
