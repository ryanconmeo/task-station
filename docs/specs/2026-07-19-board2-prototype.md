# board2 — three-view Interbrain board prototype (task #444)

> **SUPERSEDED (2026-07-25) — the whole preview line was retired in #444; see
> [BOARD-RETIREMENT.md](BOARD-RETIREMENT.md).** Kept for the design history.

> **SUPERSEDED by [board3](2026-07-19-board3.md).** Ryan reviewed board2 against the
> real board: architecture approved, presentation rejected. board3 keeps this feed/data
> pipeline but replaces the shell — real-board house style, table + galaxy graph on one
> page, ported graph engine, actions everywhere. The `board2` subcommand + shell were
> removed; the exporter was renamed `lib/board2.py` → `lib/board3.py`. This doc is kept
> for the design history (the three-view exploration + the feed schema, now `schema: 2`).

**Status:** SUPERSEDED prototype (additive, ships alongside the current board; nothing
existing changes). Built by the 444 worker against `origin/main @ 1216f4f` (task-station
v1.97.0). Companion to the canonical [two-machine sync design](2026-07-18-two-machine-sync-design.md).

## What it is

A working PROTOTYPE of the reworked board — **board2** — that proves the **shell + feed**
architecture and the **Interbrain federation UX**, rendered against the user's REAL task
store with persistent DEMO data for fake peers. One new subcommand:

```
python3 lib/task-station.py board2 [--no-demo] [--no-open]
```

writes `<data_dir>/board2/` and opens it:

```
board2/
  index.html            the app shell (three views: table list · 2D graph · 3D graph)
  board2.rev.js         change-poll sidecar (window.__TSREV; same as board.rev.js)
  feeds/self.js         the user's REAL tasks → read-only view-model feed
  feeds/self-archive.js closed tasks beyond the newest 50 (lazy "show older" shard)
  feeds/demo/*.js       persistent fake-peer feeds (jpark · kosei · org · rnguyen-demo)
```

Feeds are `.js` files (not JSON): each assigns `window.__TSFEED_<alias>` and registers on
`window.__TSFEEDS`; the shell loads them via `<script src>` because `fetch()` is blocked on
`file://` while local `<script>` subresources load fine (the proven board.rev.js mechanism).
No external assets ever; stdlib-only Python; vanilla JS/CSS; every animation respects
`prefers-reduced-motion`.

## Vocabulary (used in code + UI copy)

Products **task-station / brain-station / Interbrain**. The event stream is the **tasklog**
(a rename of "journal/Tasktrail" — this prototype does NOT touch `lib/stream.py` or the
shipped spec; the name appears only in board2 UI copy). Federation terms: **sync repo,
relay, registry, boundaries, private/public/org brain, handle (`<alias>-<n>`), memo.**
Aliases follow the org identity system: self = `rnguyen` (Entra local-part), demo peers
`jpark` / `kosei`, org alias `org`.

## Architecture — shell + feed

- **Feed exporter** (`lib/board2.py`): read-only view-model of the store. Per task:
  `{uuid8, handle, title, status, live, category{tag,dot,hex,hex_dark}, effort, tokens,
  tokens_estimated, cost_usd, models[], updated_ts, relations[{uuid8,kind}] (UUID-
  NORMALIZED — never seq), signals{prs[],stories[]}, digest{goal,state,steps_done,
  steps_total,decisions_tail[]}, participants[alias…], owner, shared_org}`.
  - `handle` = display-only `rnguyen-<seq>`, computed at export (falls back to
    `rnguyen-<uuid8>` when a task has no seq). **NO store writes, ever** — the exporter
    never calls `ensure_seqs()`.
  - `tokens` = `total_in + total_out + Σ cache_read` from the usage ledger
    (`usage.task_usage` → `session_usage` joined on `task_id`). When the ledger is
    off/empty there is no span data to estimate from, so tokens fall back to the
    spans-based estimate ×0: `tokens = 0` with `tokens_estimated: true`.
  - Closed tasks beyond the newest 50 shard to `feeds/self-archive.js`, loaded lazily on
    the list's **"show older"** (proves the shard concept).
- **Shell** (`tools/board2_shell.py`): one `index.html` from static template strings
  (view-model→string discipline like `tools/render_board.py`). It reads `window.__TSFEEDS`,
  builds a unified task model keyed by uuid8, and renders the three views. **Liveness
  renders ONLY from the self feed** (`live` derives from local running sessions); foreign
  rows never show live and are read-only (🔒).

## The three views

**1 · Table list.** Columns: owner dot · handle (mono) · task · status pill · category ·
tokens (tabular) · activity. Foreign rows: read-only tint + 🔒 + memo button. Search filters
client-side across all mounted feeds (the index is built from feed data in JS — no
`data-search` attributes). Row expand = a synapse card (goal / NEXT / steps / decisions tail
/ signals / participants).

**2 · 2D graph.** Canvas force layout (physics/interaction reimplemented cleanly from the
shipped board's `_MG_ENHANCE_JS` — repulsion, spring, gravity, damped Euler, self-halting
rAF loop; pan/drag/hover/momentum). Node radius `r = clamp(7 + 11·log10(max(tokens,5e5)/
5e5), 7, 42)` px; a 500k/5M/50M bubble legend. Fill = owner (CVD-validated set, always paired
with the alias label). **Boundary hulls**: translucent padded rounded convex hull per owner;
the **org hull** additionally encloses every `shared_org` node across owners (overlap IS the
"org encompasses" visual). Edges: solid within-brain, dashed cross-brain (same PR/story id in
two feeds), **memo edges** as directional pulses sender→recipient (amber unacked / gray acked;
static dot-dash under reduced-motion). Multi-participant nodes render a two-color split ring.
Hover card (handle/owner/title/tokens/models/read-only badge/memo). **Pending-changes**:
click-select (shift = multi) → pulsing selection ring + a slide-in tray of staged actions
per selection (Promote to org ⇡ · Share to boundary ⇢ · Send memo ✉ · Edit) with staged
badges on the nodes; Apply(n)/Discard animate the badges away (instant under reduced-motion).

**3 · 3D graph.** Same data pipeline + features via a real yaw/pitch perspective projection
(orbit, momentum, zoom caps, fit) reimplemented from the same source. Parity: token sizing,
owner colors, dashed cross-brain edges, memo pulses, split rings, selection/tray. Hulls in 3D
are soft owner-colored halo spheres (no convex hulls). The `perf` toggle drops to on-demand
static frames (no continuous loop), mirroring the existing board.

## Real vs stubbed

| Area | State |
|---|---|
| Self feed (real store → read-only view-model) | **real** |
| Tokens/cost/models per task (usage ledger join) | **real** (est-zero fallback when ledger empty) |
| Liveness (self only) | **real** (local running sessions) |
| Archive shard + lazy "show older" | **real** |
| Demo peer feeds (jpark/kosei/org/rnguyen-demo) | **fixtures** — persistent, never in the store |
| Cross-brain edges + memo pulses to real tasks | **real signals** (seed rewrites sentinels → real ids/uuid8s) |
| Left-rail mounts | self=**real**; projectname/petpiano sub-brains + `project:projectname` boundary = **visual stubs** |
| Pending-changes actions (promote/share/memo/edit) | **stubbed** — animate staged badges; no store writes |
| Sync status (top bar) | **stubbed** label |
| Boundaries | **stubbed** (one `project:projectname` mount) |

## Feed schema

`window.__TSFEEDS` is an array of feed objects:

```js
{ schema:1, kind:"self"|"peer"|"org"|"archive", alias, owner, label,
  editable, color, color_dark, has_archive?, archive_src?,
  tasks:[ <task view-model, above> ],
  memos:[ {from:<uuid8>, to_uuid8:<uuid8>, acked:bool} ] }
```

`kind` drives the rail section (self → My brains, peer → Peers, org → Org) and read-only
tint (anything not `self`). `memos` carry directional pulses. A task's `relations` are
uuid8-only so an edge survives cross-machine sync (per the sync design's relation-edge
normalization).

## Demo data + cleanup

Committed fixtures live in `fixtures/demo-feeds/` (jpark ~10 tasks, kosei ~6, org ~5,
rnguyen-demo 4 `[DEMO]` tasks, token spread 500k–60M so bubble sizing reads).
`tools/seed_demo.py`:

- copies each fixture to `feeds/demo/<name>.js` — but only if not already present, so demo
  data is **persistent** across re-exports;
- on first copy, rewrites the sentinels `__XREF_PR_1/2__`, `__XREF_STORY_1__`,
  `__REAL_UUID8_1/2__` to REAL PR/story ids and task uuid8s found in the freshly exported
  self feed, so cross-brain edges + memo pulses form against the user's real tasks;
- gives two demo tasks `participants:["rnguyen","jpark"]` (the split-ring data), one carrying
  a real-signal share.

**Demo data NEVER enters the store** — the seeder only reads the exported self feed and
writes under `feeds/demo/`. **Cleanup**: delete `<data_dir>/board2/feeds/demo/` (or
`python3 tools/seed_demo.py --clean`, or run `board2 --no-demo` to skip seeding). The whole
prototype is disposable: `rm -rf <data_dir>/board2/`.

## What graduates later (WS-S3 / J1)

- The **self feed exporter** is the seed of the federation export surface: today it's a
  read-only view-model; under the sync design it becomes the owner-partitioned state written
  to the sync repo (uuid + handle already carried; relations already uuid-normalized).
- The **shell + feed** split is the durable architecture — real peers replace the demo feeds
  (same schema); the relay/registry populate `window.__TSFEEDS` instead of `<script src>`
  fixtures.
- **Pending-changes** actions graduate from staged-badge stubs to real mutations: Promote →
  org-brain write, Share → boundary membership, Send memo → the existing memo/correspondence
  path, Edit → the task update path.
- **Boundaries** become real membership sets; the `project:projectname` stub is the placeholder.
- Liveness stays per-machine (never federated), per the sync design's reconciliation #5.

## Hard constraints honored

Additive only (new files + one new subcommand); does not modify `write_board` /
`tools/render_board.py` / `maybe_refresh_board`, the store schema, `lib/stream.py`, the
Tasktrail spec, or hooks. No store writes. No external assets (same `_EXTERNAL_NEEDLES`
invariant, asserted for board2 output). stdlib-only Python; vanilla JS/CSS. Reduced-motion
respected across every animation.

## Tests

`tests/test_board2.py`: exporter structure (uuid-normalized relations, handle format +
seqless fallback, tokens join + est-zero fallback, digest/signals, archive split), seed
(signal rewrite, all feeds seeded + referenced, `--no-demo` skip, **store db hash unchanged**,
persistence, clean), and shell features (no external-asset needles, feeds via `<script src>`,
three view containers, radius formula, hull/legend/tray/perf markers, reduced-motion CSS +
JS guards).
