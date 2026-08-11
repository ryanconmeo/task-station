# BOARD-BEHAVIOR — the behavioral gate for `/todo board`

Status: **active gate** (task #444). The board's acceptance contract.

## Why this exists

2.0.0 made an opt-in preview engine the `/todo board` default. It passed a *markup-parity*
gate yet shipped real **behavioral** regressions in the hands of a user: an empty board on
first load, the closed-list cap gone, a graph that crashed on galaxy blobs, diverged
categories/settings, no title marquee, no per-session resume, and a sidebar that ate
horizontal space. Markup-grep parity is not behavior parity. 2.0.1 reverted the default;
#444 retired the preview outright (`BOARD-RETIREMENT.md`).

**The law.** `tools/render_board.py` is THE board and evolves in place — there is one
renderer and no engine flag. Every change to it MUST satisfy every behavior below.
**Interbrain OFF ⇒ the board is behavior-identical to the pre-federation board**, modulo
the additions enumerated in B14 — that list is exhaustive and grows only deliberately.
Verification is BEHAVIORAL (what a user observes / what the served HTML actually
contains), never markup-grep of an incidental class name.

## How to read this

Each behavior lists: **what** the user must observe, **verify** (a manual recipe), and
**assert** (the cheap automated check that guards it, or `— none (manual only)`).

Manual harness for every recipe below (unless stated otherwise):

```bash
python3 lib/task-station.py board --open
# or, for the served-HTML asserts, capture the file without opening:
python3 lib/task-station.py board   # prints board.html path
```

Automated asserts live in `tests/test_board_behavior.py` unless another file is named.
Run: `python3 -m pytest tests/test_board_behavior.py -q`.

---

## B1 — Rows render on first load with NO interaction

- **What:** Opening `board.html` shows every task's row immediately. No click, no button,
  no JS run is required to see the task list. (2.0.0's preview default showed an empty
  board until a control was clicked — this is the headline regression.)
- **Verify:** Open the board, disable JavaScript in the browser, reload. All open and
  closed rows are still present and readable.
- **Assert:** ✅ `test_first_load_rows_present` — the served HTML contains one
  `<details class="row …">` per task with the task title as text, WITHOUT executing any
  script (server-rendered rows == the first-load guarantee).

## B2 — Closed list shows the 5 newest + a "show N more" expander

- **What:** The Closed section renders the 5 most-recent closed tasks inline; the rest are
  folded into a native `<details>` whose summary reads "see more (N more)".
- **Verify:** With ≥6 closed tasks, only 5 show; the "see more (N more)" row reveals the
  rest on click (works with JS disabled — it's a native `<details>`).
- **Assert:** ✅ `test_closed_cap_and_show_more` — with 6+ closed VMs the HTML contains
  `id="closed-extra"` with `data-more="<count-over-5>"` and a `see more (N more)` summary.

## B3 — Task titles marquee/slide on hover to reveal the full title

- **What:** A truncated title cell slides horizontally on hover so the full title is
  readable without expanding the row. The full title is ALSO in the expanded Overview.
- **Verify:** Hover a long title in the collapsed row; the text slides to reveal its tail.
- **Assert:** ✅ `test_title_marquee_hook_present` — title cells carry the marquee CSS
  class (`.ttl`) and the served page carries the hover-slide behavior (`stopScroll`).

## B4 — Each session row under a task expands to its resume command

- **What:** Expanding a task's Sessions section shows, per hub/session, a copyable
  `cd … && claude --resume …` one-liner. Small tasks fall back to the hub resume line.
- **Verify:** Expand a task with a recorded session; each session block shows a resume
  command; the copy button copies it.
- **Assert:** ✅ `test_session_resume_block_present` — a task VM with a resume target
  renders a `cd ` … `claude --resume` code block in its detail.

## B5 — Categories panel + config/settings rows are complete and current

- **What:** The bottom Categories panel lists one expandable row per used category (pill =
  tag, description, per-state counts), and the Help/Commands panel shows every config row
  (`board_rows()`) — one row per live setting, no stale or retired flags.
- **Verify:** Compare the Categories list and the Commands & config panel against
  `/todo config`; tags, labels, counts, and config options match, and a retired flag
  (e.g. the removed `--board-engine`) appears in NEITHER.
- **Assert:** ✅ covered by `tests/test_board.py` category + help-panel tests;
  `tests/test_config.py:test_board_shows_interbrain_row_but_not_board_engine` pins the
  retired flag's absence.

## B6 — Graph renders with blobs/hulls toggled BOTH ways

- **What:** The task graph renders and stays interactive whether owner/category hulls
  ("blobs") are shown or hidden. Toggling never blanks or crashes the canvas.
- **Verify:** Open the graph, toggle the hull/blob filter on and off repeatedly; nodes and
  edges keep rendering; no console error, no blank canvas. (2.0.0's preview galaxy blobs
  crashed the graph — the board must not.)
- **Assert:** partial — `test_render_graph.py` / `test_universal_graph.py` pin the graph
  DATA (nodes/edges) the renderer consumes; the toggle-both-ways interaction is **manual
  only** (no headless DOM in-suite).

## B7 — Refuse-downgrade honored

- **What:** A passive/auto refresh (`board --refresh-if-live`, `guard_downgrade=True`)
  must NOT overwrite a `board.html` stamped by a NEWER plugin version. An explicit `board`
  always writes. A refused write touches NOTHING — not `board.html`, not `board.rev.js`,
  not `feeds/`.
- **Verify:** Render with a high version stamp, then run a passive refresh under a lower
  version; the file is left untouched.
- **Assert:** ✅ `test_refuse_downgrade` (this file), guarded by `_semver_gt` +
  `_existing_board_version`; `tests/test_feeds.py:test_refused_downgrade_writes_nothing`
  pins that the feed export is inside the guard.

## B8 — Rev sidecar auto-reload works

- **What:** `board.html` loads the sibling `board.rev.js` (`window.__TSREV`); when
  auto-refresh is on and the data changes (rev differs), the page reloads and restores
  open rows / scroll / filters. A `file://` page uses a `<script>` sidecar (not `fetch`,
  which file:// blocks).
- **Verify:** With auto-refresh on, mutate a task; the open board reloads within ~2s and
  keeps expanded rows open. An unchanged data set does NOT reload.
- **Assert:** ✅ `test_rev_sidecar_written` — `write_board` writes `board.rev.js` with a
  `window.__TSREV="<rev>";` matching the `rev` embedded in `board.html`.

## B9 — Per-session expand/resume + open-state persistence

- **What:** Expanded rows and sections persist their open/closed state across the
  auto-refresh reload (via the generic `details[data-key]` handler + `localStorage`).
- **Verify:** Expand some rows, trigger a data-change reload; the same rows stay open.
- **Assert:** partial — `data-key` presence on rows/sections is server-side assertable
  (`test_first_load_rows_present` checks `data-key="row:…"`); the restore itself is
  **manual only**.

---

## Interbrain behaviors (F1/F2)

## B10 — Interbrain OFF ⇒ byte/behavior parity with the pre-F1 board

- **What:** With `interbrain` off, `write_board` produces ZERO foreign view-models and the
  render is behavior-identical to the pre-federation board (modulo the version/timestamp
  stamp and the B14 additions).
- **Verify:** Set `TASK_STATION_INTERBRAIN=off`, render; the HTML contains no owner chips,
  no 🔒 markers, no foreign rows — identical structure to a peer-free board.
- **Assert:** ✅ `test_off_with_feeds_equals_no_feeds` — the normalized
  (version/timestamp-stripped) HTML with interbrain off + a peer feed present equals the
  render with no feed present; `test_off_has_no_foreign_or_strip` pins the absence of the
  strip / owner chips / foreign rows / `data-owner`.

## B11 — Foreign rows are read-only: owner chip + lock + memo-only

- **What:** With interbrain on and peer feeds present, each foreign task renders through
  the SAME row builder as local tasks with: an owner dot + alias chip before the title, a 🔒 marker,
  memo-only actions (open/resume shown disabled with a "read-only · foreign brain"
  tooltip), and NO sessions/prompts/resume sections.
- **Verify:** `python3 tools/seed_demo.py`, then render with interbrain on: rows for
  jpark / kosei / rnguyen-demo appear with the owner chip + lock, no resume command, and a
  memo affordance; org renders as the org brain.
- **Assert:** ✅ `test_foreign_row_memo_only` — a foreign row renders the owner chip + 🔒 +
  the "read-only · foreign brain" tooltip + a Memo affordance and contains NO
  `claude --resume` block; `test_feed_to_vm` pins the feed→VM mapping.
- **Assert (shipped fixtures):** ✅ `DemoFeedFederationTest` (same file) drives the REAL
  `tools/seed_demo.py` output rather than an in-process feed — peer rows for every demo
  brain, the org chip for org, foreign graph nodes, `auto` flipping on, and off still hiding
  everything. This exists because every synthetic-feed assert above passed for five
  releases while the shipped fixtures rendered NOTHING (they were client-side IIFE, skipped
  by the server parser — see `BOARD-RETIREMENT.md`). Fixture form itself is pinned by
  `tests/test_feeds.py:ShippedFixtureTest`. **A synthetic feed is not evidence that the
  shipped feeds work.**

## B12 — Foreign nodes + dashed shared-signal edges in the graph

- **What:** Foreign tasks enter the graph as owner-coloured nodes; cross-brain shared
  PR/story signals draw dashed edges. No graph redesign (drill-down/galaxies are run 2).
- **Verify:** With a peer feed sharing a PR/story with a local task, the graph shows the
  foreign node and a dashed edge to the local counterpart.
- **Assert:** ✅ `test_foreign_owner_node_and_xbrain_edge` — `mg-data` task nodes carry an
  `owner` field, a foreign owner-coloured node exists, and a cross-brain `xbrain` edge
  (rendered dashed) links it to the local task.

## B13 — Focus strip filters table + graph; persisted

- **What:** A compact top-bar chip strip (`Everything · my brains · <peer> · <org>`)
  focuses exactly one brain/person or none (= Everything), filtering BOTH the table and
  the graph, persisted in `localStorage` (Everything default). No sidebar; no horizontal
  real-estate loss.
- **Verify:** Click a focus chip; only that brain/person's rows + nodes remain; reload —
  the focus persists. "Everything" restores all.
- **Assert:** ✅ `test_strip_and_mgdata_fields` — the header contains the focus strip with
  an "Everything" chip + a peer chip; rows carry `data-owner`/`data-brain`; `mg-data`
  nodes carry `owner`/`brain`; the focus JS (`ts-board-focus`) is present.
  `test_self_handle_chip_when_on` pins the self handle chip. The live filter application +
  persistence are **manual only** (JS).

## B14 — Interbrain OFF says so (the enumerated parity exception)

- **What:** When federation resolves OFF, the Help panel's Commands note carries **exactly
  one** additional dim line: *"Interbrain federation is off — peer and org brains are not
  shown. Turn it on with `/todo config --interbrain on`."* When federation is ON the line is
  absent. Nothing else about the off render changes.
- **Why it exists (#444):** `--interbrain auto` resolves to off on a single brain with no
  peer feeds — the overwhelmingly common case — and the board previously said *nothing*, so
  shipped federation work was undiscoverable. Silence was the bug.
- **Where:** the help/commands panel deliberately, NOT the task sections — so the blast
  radius stays off the rows, sections, and graph that B10's parity compare covers most
  sharply. The line depends only on the resolved on/off state, never on *why*, so both off
  renders (peer feed present vs absent) stay byte-identical and B10 still holds.
- **This list is exhaustive.** It is the complete set of intentional divergences from the
  pre-F1 render when interbrain is off. Adding another requires a new B-number here plus a
  stated justification — **never** weaken or skip
  `test_off_with_feeds_equals_no_feeds` to make a change pass.
- **Verify:** Render with `TASK_STATION_INTERBRAIN=off`; the Help → Commands panel shows the
  line. Render with `=on`; it is gone.
- **Assert:** ✅ `test_off_shows_federation_hint` / `test_on_hides_federation_hint`;
  `test_off_with_feeds_equals_no_feeds` (unchanged) still pins B10 parity across both off
  renders.

## Knowledge-plane behaviors (step 78)

## B15 — Two stacked planes, moved between by a camera PAN (3D only)

- **What:** With a vault configured and `--knowledge-plane` resolving on, the 3D graph
  shows **two literal stacked planes**: the task sphere where it has always been, and the
  knowledge corpus — the whole vault, every render — as a flat plane a clear gap ABOVE it.
  An `↑ Notes layer` / `↓ Task layer` control sits beside 2D/3D and auto-rotate; clicking
  it, or pressing ↑/↓ with the canvas focused, moves the CAMERA between them. Yaw, pitch,
  zoom and the drawn population are all unchanged by that move — it is a pan, not a zoom,
  a level change or a filter. Selecting a task that cites notes rises to the corpus and
  highlights what it cited; nothing is ever hidden. The plane the camera is not on draws
  low-poly (dots and plates, no labels). In **2D** the plane is not drawn and the control
  is hidden, exactly as auto-rotate hides itself: two stacked planes seen from directly
  above are one plane.
- **The gap:** exactly three edge kinds may join the planes — `cites`, `distilled-from`,
  `references`. Everything else (lineage, membership, hubs, signal spokes, cross-brain)
  stays inside its own plane. They are drawn as the quietest lines on the canvas, because
  they are provenance rather than dependency.
- **Off is unchanged:** with no vault (or `--knowledge-plane off`) the graph panel is
  byte-identical to the board with the feature hard off — no control, no note node, no
  `plane` key in `mg-data`. The corpus is never written to; this switch is read-only and
  separate from `--knowledge-graph`.
- **Verify:** `/todo config --obsidian-vault <path>` on a vault with notes, then render
  the board. In 3D: the corpus sits above the sphere; press ↑ and the camera rises without
  the sphere changing size; press ↓ and it returns. Switch to 2D: the control disappears
  and only the task plane draws. Run `/todo config --knowledge-plane off` and re-render:
  the graph is exactly the single-plane one.
- **Assert:** ✅ `tests/test_two_plane_view.py` — placement (flat, above, gap sized off the
  task layout), the gap rule (an illegal crossing is DROPPED by the renderer), the pan
  (nothing in it writes yaw/pitch/zoom; reduced motion snaps), the global corpus (every
  note drawn however few are cited), and parity (no-vault panel == hard-off panel, byte
  for byte). The canvas interaction itself is the documented no-unit-test carve-out:
  **the visual is manual only.**

---

## Change protocol

- Adding/altering a board behavior ⇒ update this file (the observable **what** + a manual
  recipe) in the SAME change, and add or update the cheapest honest automated assert.
- A behavior with no automated assert MUST say `manual only` and explain why (usually:
  requires a headless DOM the stdlib-only suite doesn't run).
- Never downgrade an assert to markup-grep of an incidental class when a served-content
  check is available. Prefer "the HTML a no-JS user receives contains X".
