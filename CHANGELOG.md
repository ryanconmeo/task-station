# Changelog

All notable changes to Task Station are documented here. This project adheres to
[Semantic Versioning](https://semver.org).

## [1.4.0] — 2026-06-20

### Added
- **Task lifecycle PHASE — `◦ inquiry` → `● active`.** Every task now carries a
  `phase` (default `inquiry`), independent of its open/closed `status`. A topic you
  merely raise shows on the board immediately as `◦`; it graduates to `●` when work
  actually starts. A leading single-width glyph renders at the very front of every
  `/todo` row — ASCII list, Markdown table (`#` cell), and the detail view — distinct
  from the category emoji, with a `◦ inquiry · ● active` legend. Closed rows mute the
  glyph (phase matters mainly for open work).
- **Auto-promote `◦ → ●` when work begins** (idempotent), on any of:
  - `delegate … --worktree` for the task (write work starts);
  - a **file edit** in an attached session — `hooks/on_post_tool.sh` (PostToolUse)
    flips an attached inquiry task to active;
  - manual **`phase --task <ref> [active|inquiry]`** (no value → report the phase);
  - **`create --active`** to start a task active.
- **Auto-track as inquiry from the first prompt** — replaces the old "pure Q&A → stay
  silent" behaviour. For an unattached, non-skipped session the model now creates an
  `inquiry` task for the topic (model-driven: good title + category). Skipped sessions
  still stay silent.
- **Grouping — "fold, don't fork".** Before creating a new inquiry task the model
  scans the open board and, if the prompt continues an existing open task, **attaches
  and appends the prompt as a note** instead of spawning a sibling — so related
  questions across sessions accumulate under one task. New **`attach --note '<text>'`**
  appends a timestamped entry to the task's activity log.

### Changed
- `cmd_prompt_context` / `commands/todo.md` / `guidance` guidance rewritten around
  track-as-inquiry + fold-don't-fork (was: attach only on concrete work, else silent).

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
