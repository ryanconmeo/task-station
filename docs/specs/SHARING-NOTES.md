# SHARING-NOTES.md — brains & sharing dev seams (for the many iterations coming)

Sharing/brains is expected to change a lot. This doc maps the **seams** so each iteration
is a small diff, not a rework. Read it before touching brains/sharing.

## The model (vocabulary — keep it consistent everywhere)

- **Brain** — where a task LIVES. One owner, one brain per task; default `main`.
  Organizational, not visibility.
- **Sharing rule** — an audience GRANT on a brain: "brain X is visible to audience A",
  optionally scoped to a category tag. Never a rail section — it's visibility.
- **Promotion** — copies a curated version of a task to the ORG brain (distinct from a
  share grant). Not yet a CLI.
- **Audience** — a peer alias (`jpark`) or `org`. Resolved list per task = `shares`.

## Seam 1 — the config model: `lib/brains.py`

The ONLY place that reads/writes the brains config. Sidecar `<data_dir>/brains.json`
(NEVER `tasks.db`). Pure ops on a loaded dict + resolution:

- Schema: `{"version", "brains": {name: {archived, shares: [{with, tag}]}}, "assign": {uuid: brain}}`.
- Ops (return True on change): `add · rename · archive · share · unshare · assign`.
- Resolution (read side): `brain_for(cfg, uuid)` · `shares_for(cfg, uuid, tag)` (tag-scoped
  rules match the task's category tag; untagged rules always apply) · `list_brains(cfg)`.
- To change the rule shape (e.g. add expiry, direction, or a "public" audience): edit the
  schema + `share/unshare/shares_for` here; everything downstream reads resolved `shares`.

## Seam 2 — the CLI: `task-station brains` (`cmd_brains` in lib/task-station.py)

Thin dispatcher over `brains.py`. Actions: `list/add/rename/archive/share/unshare/assign/
show`. `assign` resolves a task-ref via `resolve_ref`. New op ⇒ add a `brains.py` function
+ a branch here + a `board_rows`/help line. This is the ONLY write path (the UI can't
write from `file://` — it emits these commands).

## Seam 3 — the feed layer: `lib/feeds.py` ← **the sync seam**

**The ONE owner of the feed format**: writer, wire form, parser, and loader. When the
two-machine sync transport (J-track) lands, THIS is the module it produces and consumes —
so changes here are protocol changes. There is deliberately no second implementation.

- **Wire form** — `_feed_js(alias, feed)` → `window.__TSFEED_<alias> = {json};` plus a
  `window.__TSFEEDS` registry push. `.js`, not json, because `file://` blocks local
  `fetch()` but loads local `<script src>`. `parse_feed_file(path)` is its exact inverse
  (round-trip asserted in `tests/test_feeds.py`) — the same bytes serve browser and server.
- **Feed root** — `feeds_dir(data_dir)` = `<data_dir>/feeds/`: `self.js`,
  `self-archive.js` (closed past 50), `peers/*.js` (what sync will deliver),
  `demo/*.js` (fixtures). `peer_feed_files()` fixes the load order: **peers then demo**,
  each sorted, self/archive excluded. One root — there is no second one.
- **Producer** — `export_self_feed(ts, data_dir)`, called on every `/todo board` write, so
  the root is always current. Pure READ of the store; never writes `tasks.db`.
- **Per-task resolution** — `self_view_model` resolves `brain` + `shares` from `cfg` (via
  `brains.py`) and derives `shared_org = "org" in shares`. Feed schema fields (bump
  `FEED_SCHEMA` when you change them): `brain`, `shares` (resolved audience list),
  `shared_org`, `participants`, plus feed-level `local_only`. Foreign feeds carry their own
  `brain`/`shares` for the "Shared with" line.
- **`strip_local_only(feed)`** is the `sync_safe` export gate — route ANY share/sync export
  through it so local-only fields never leave the machine (details under Seam 6).
- **`_pr_signal_id`** is the F6 cross-link join key and its format is **FROZEN**:
  `lib/artifacts.py:pr_signal_id` must agree, or PR auto-linking silently stops matching.
- **`_feed_content_rev`** stamps the feed's own `rev` so a subscriber can diff it (F5).

## Seam 4 — the UI: the board itself (`tools/render_board.py`)

Federation renders SERVER-SIDE, through the same row/section/graph builders as local
tasks — there is no separate client app (the preview shell was retired in #444; see
`BOARD-RETIREMENT.md`). Peer/org feeds → `_foreign_view_model` → the shared `_row`
builder, flagged `foreign` and read-only: owner chip + 🔒, memo-only, never
sessions/prompts/resume. Graph gets foreign nodes + dashed `xbrain` cross-signal edges via
`_augment_graph_foreign`.

Because a `file://` page cannot write, **the board never mutates brains config** — Seam 2's
CLI is the only write path. Any UI over it can only print commands to run.

## Interbrain on/off + org label

- `config.interbrain_mode()` (`on`/`off`/`auto`) — `auto` resolves in `ts._interbrain_on`
  (on when >1 brain OR any peer feed exists). **When it resolves OFF the board renders
  byte-parity with the pre-federation board** (the parity law, `BOARD-BEHAVIOR.md` B10) and
  says so in one dim help-panel line pointing at `--interbrain on` — the off state is
  visible, not silent.
- `config.org_label()` — the display label for the org brain everywhere it appears (chips,
  focus strip, graph legend). `seed_demo` sets the org feed's label. Change it in config;
  the render follows.

## Seam 5 — brains as DEFINABLE structures + AUTO-attach (F4, `lib/brains.py`)

Schema v2 (migrates v1 in place on load): each brain carries `{name, description, purpose,
keywords[], repos[], category_affinity[], created_ts}` beyond `archived`/`shares`. Ops:
`add(now=,**fields) · edit(**fields)` (list fields REPLACE; empty string clears). The
assignment side gained `pinned` (`{uuid: true}`): `assign(…, pinned=True)` is the manual
override; `auto_assign` (the scorer's writer) writes ONLY while a task is on `main` and not
pinned — it never yanks a task out of a scored brain.

- **Scoring** — `score_brains(cfg, signals, threshold=3)` is PURE. signals = `{repos[],
  cwd, text, category, skill}`. Weights: repo/cwd 4 · keyword 2·cap 6 · category_affinity 2
  · skill 1. Best non-archived non-main total ≥ threshold wins, else `main`. Returns the
  full audit table (`brains suggest --task <n>` prints it). To retune: change the `W_*`
  constants / `SCORE_THRESHOLD` here — everything downstream reads `winner`/`scores`.
- **Derived block** — `derive(cfg, name, tasks)` is PURE, computed at READ, NEVER stored:
  `{open_count, active_count, recent_focus, dominant_categories, top_signals,
  last_activity_ts}`. task views come from `ts._brain_task_views()` (store read). Consumed
  by `brains show`, the self-feed `brains[].derived`, and the focus-strip counts.
- **Wiring** — `ts.auto_attach_brain(task, session)` (in task-station.py) builds signals
  via `_brain_signals` and calls the scorer + `auto_assign`; invoked from `cmd_create`
  (new task) and `cmd_attach` (attach-with-edit re-score). Session skill hint reads
  `TASK_STATION_SKILL_HINT` (optional).

## Seam 6 — correspondence: link · fork · subscribe · trail_visibility (F5)

All read CANONICAL peer feeds (`window.__TSFEED_<alias> = {json};` — real sync's form) via
`_all_peer_feeds` / `_resolve_peer_ref` (task-station.py), which load through
`feeds.peer_feed_files` + `feeds.parse_feed_file`. The shipped demo fixtures are canonical
too (as of #444), so they are forkable/subscribable in hand; any legacy non-canonical file
is SKIPPED, not fatal — silently, so assert that new feed sources parse. Task-dict fields
(additive JSON, no store schema change):

- `links: [{alias, uuid8, handle, kind, ts}]` — `add_link` (idempotent). `cmd_link` records
  a manual pair; renders on the detail (`_brief_detail` `linked`/`forked from` rows,
  `.lchip`), the row, and the graph (a dashed `xbrain` PAIR edge added in
  `_augment_graph_foreign`, foreign endpoint created on demand). **Pair-edge styling reuses
  the fully-wired `xbrain` kind** (CLS/EDGEVAR/CSS/canvas-dash) — a new kind would need all
  four touched.
- `forked_from: {alias, uuid8, handle, title, at_rev, ts}` — `cmd_fork` copies the peer
  node's digest (goal/state/decisions + any summary/glossary/steps the feed carries),
  records provenance, auto-links, and auto-attaches to a brain (F4). Peer feed never mutated.
- Subscriptions live ON a link: `link.subscribe = {on:[…], last_rev}`. `cmd_subscribe` sets
  a baseline rev (only FUTURE changes mint). `_subscriptions_check` diffs each peer feed's
  rev (`_feed_rev` = feed `rev` or content hash) vs `last_rev`, mints ONE memo per rev
  (idempotent — `last_rev` bumped immediately), throttled on the on_stop-hook path
  (`--throttle`, `_subs_throttled`, `subscriptions.last`). The self feed stamps its own
  `rev` (`feeds._feed_content_rev`) so a peer can diff it.
- `trail_visibility: private(default)|checkpoints|full` — `update --trail-visibility`.
  Enforced at export by **`feeds.strip_local_only`** (the sync_safe gate): private drops
  `prompts` + blanks digest `state`/`decisions_tail`; checkpoints keeps the digest, drops
  prompts; full keeps prompts. `resume` (machine-local path) is ALWAYS dropped. The self VM
  carries `trail_visibility`; foreign VMs pass through peer `prompts` (a full-visibility
  peer's trail) so the detail renders whatever visibility granted.

## Seam 7 — artifact capture + cross-person auto-link (F6)

- **`lib/artifacts.py`** — the ONE forge-agnostic PR/work-item URL pattern list (GitHub /
  ADO / GitLab / Bitbucket). `scan(text) → [{url,id,repo,kind}]`; `pr_signal_id` MUST agree
  with `feeds._pr_signal_id` (the feeds' join key — a FROZEN format). Add a forge = add a
  pattern.
- **Capture** — `cmd_capture_artifacts` (PostToolUse via on_post_tool.sh, fed the whole hook
  payload on stdin; TASK_STATION_SUPPRESS + attached-task gated, fail-open) scans a tool
  RESULT, appends deduped urls to `prs[]`/`stories[]` + one capped `artifact` event.
- **Cross-person auto-link** — `_autolink_task_signals(task)` runs after capture AND after a
  manual `update --pr/--story`: for every PR/story signal on the task, scans peer feeds for
  a task carrying the SAME signal id → `add_link(kind="signal")` + a two-way-worded memo.
  Idempotent (add_link dedups the pair, so no repeat memo). The graph pair edge follows.

## Known next-iteration knobs (not yet built)

**The J-track transport** — two-machine sync producing/consuming `lib/feeds.py`'s format
into `feeds/peers/` — is the big one; everything below assumes it. Also: promotion CLI
(curated org copy) · tag-scoped audience chips (only untagged today) · unshare-by-tag ·
richer audience types (boundary sets beyond peers/org) · real peers replacing demo
fixtures (same feed schema) · reciprocal cross-person memo on the PEER's task once a
writable transport exists (today: one-sided, worded bidirectionally).

DONE in #444 (was listed here): the shipped demo fixtures are now CANONICAL, so they are
server-parseable — forkable and subscribable in-hand, and they render as foreign rows. Any
new fixture must use the same one-line `window.__TSFEED_<alias> = {json};` form; pure data,
no logic. `tests/test_feeds.py:ShippedFixtureTest` enforces it.
