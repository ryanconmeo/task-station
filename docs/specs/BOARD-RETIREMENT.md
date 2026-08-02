# Board preview engine — retirement record (#444)

Replaces the old feature-parity ledger, which was a *switchover gate* pointing the wrong
way: it tracked a preview engine's convergence toward replacing the shipping board. That
switchover was reverted in 2.0.1 (`dc80ace`) and is not coming back. This file records
what happened instead, so nobody re-derives it from a stale table.

## What the preview was

A third prototype iteration of the board (`lib/board3.py` + `tools/board3_shell.py`,
reachable via `config --board-engine v2`, `board --v2`, or a deprecated `board3`
subcommand). It rendered a client-side app: a federated table plus a 2D/3D canvas galaxy
graph on one page, driven by `.js` view-model feeds.

**Two naming axes were never unified**, which is most of why this got confusing:

| Axis | Meant | Values |
|---|---|---|
| prototype iteration | which *design attempt* | board2, **board3** |
| shipping generation | which *engine ships* | v1 (classic), **v2** |

So "board3" and "v2" pointed at the same code while counting different things, and
2.0.1's revert froze the mismatch in place. Both vocabularies are now gone: there is
**one board**.

## Why it was retired

1. **It was not the default and had not been since 2.0.1.** The docs still claimed the
   opposite for five releases.
2. **Its federation UI had already been ported into the shipping board** by F1/F2/F3 —
   peer rows, the focus strip, galaxy drill-down. The preview was no longer where the
   federation work lived.
3. **It was a duplicate.** `lib/task-station.py` carried a SECOND implementation of the
   feed format (serializer comment, parser, and loader), and the two had already
   diverged on the feed root: the preview wrote `<data_dir>/board3/feeds/demo/` while the
   board read `<data_dir>/feeds/demo/`.
4. **It was behind.** `lib/board3.py` last changed in run 3 (`5c4bd19`) and never
   received 2.4.x Agent View (`920a570`), so the "preview" showed less than the board.

## What was kept

**The feed / view-model layer**, promoted to its own module: **`lib/feeds.py`**. It is
now the ONE owner of the format — writer, wire form, parser, and loader — because that
layer is the seam the two-machine sync transport (J-track) will consume. See
`SHARING-NOTES.md` for the sharing/sync seams.

| Kept | Now lives in |
|---|---|
| feed serialization (`_feed_js`) + its inverse (`parse_feed_file`) | `lib/feeds.py` |
| peers-then-demo load order (`peer_feed_files`) | `lib/feeds.py` |
| per-task read-only view-model (`self_view_model`, `build_self_feed`) | `lib/feeds.py` |
| `strip_local_only` — the `sync_safe` gate + `trail_visibility` | `lib/feeds.py` |
| `_pr_signal_id` — the F6 cross-link join key (frozen format) | `lib/feeds.py` |
| `_feed_content_rev` — F5 subscription diffing | `lib/feeds.py` |
| brains & sharing resolution onto feeds | `lib/brains.py` → `lib/feeds.py` |
| demo peer fixtures (the stand-in for real peers pre-transport) | `tools/seed_demo.py` |

### The demo fixtures had to be converted (the retirement's one forced change)

The four fixtures in `fixtures/demo-feeds/` were authored for the preview's **client-side**
rendering: an IIFE that built the feed from a local category map and assigned it
(`window.__TSFEED_x = feed;` — where `feed` is a JS *variable*). A browser evaluates that
happily. The server-side F1 path cannot: `feeds.parse_feed_file` needs the canonical
`= {json};` form and correctly SKIPS anything else rather than crashing. So with the shell
gone, **`seed_demo.py` switched federation on and rendered zero peer rows** — the demo
brains existed on disk and appeared nowhere.

This was latent before the retirement (on `main` the same check also yields zero peers,
because the old seeder wrote `<data_dir>/board3/feeds/demo/` while the board read
`<data_dir>/feeds/demo/` — a path bug hiding a wire-form bug). Retiring the only
client-side consumer is what made the canonical form mandatory, so #444 converted all four
to it: one-line canonical assignment, pure data, sentinels preserved verbatim for the
seeder's text rewrite. Two things had to change beyond the form itself:

- **`rnguyen-demo.js` was `kind: "self"`**, which `foreign_view_models` deliberately drops
  ("the local brain, already rendered from the store"). It is now `kind: "peer"` — accurate,
  since those demo tasks are not in the store and must render read-only. Its tasks also
  carry `brain: "demo"` now, matching the feed's own `My brain · demo` identity instead of
  colliding with the real `main` brain.
- **Category objects gained `key`.** `_foreign_view_model` reads `category.key` for the row
  and graph-node accent (`color = cat.get("key")`); the old objects carried only
  tag/dot/hex/hex_dark, so foreign rows would have rendered uncoloured. Each `key` is
  derived from the fixture's own hex, which maps 1:1 onto the `_CAT_HEX` slots.

`tests/test_feeds.py:ShippedFixtureTest` now parses every shipped fixture and asserts the
form, so this cannot regress silently. `tests/test_board_behavior.py:DemoFeedFederationTest`
drives the real seeder end to end — every synthetic-feed test passed throughout the outage,
which is exactly why a fixture-level guard was needed.

The feed root is unified to **`<data_dir>/feeds/`** (`self.js`, `self-archive.js`,
`peers/*.js`, `demo/*.js`). `<data_dir>/board3/` is gone. `/todo board` writes the self
feed on every render, so the root is always current for the seeder and the transport.

## What was intentionally dropped

- **The app shell** (`tools/board3_shell.py`) — a second HTML renderer to keep in sync
  with the house style.
- **The 3-view UI** (list · 2D graph · 3D) — the board has one page with a canvas graph.
- **The staging tray** + staged-CLI Apply.
- **The mounts rail** (peers/org/brains sidebar) — superseded by the F2 focus strip.
- **The brains manager panel** — `task-station brains …` is the only write path anyway,
  since a `file://` page cannot write.
- **The client-side table renderer** — the board renders rows SERVER-SIDE, which is the
  first-load guarantee the preview default broke (see `BOARD-BEHAVIOR.md` B1).

Config surface removed with it: `--board-engine` / `config.board_engine()`, `board --v2`,
`board --classic`, `board --demo`, and the `board3` subcommand. A persisted
`board_engine` key in an existing config is inert — nothing reads it. Passing
`--board-engine` prints a one-line retirement notice and exits 0.

## Where things stand

One board (`tools/render_board.py`) + one feed layer (`lib/feeds.py`). Federation lives
in the board, gated by `config --interbrain` (`on` · `off` · `auto`). The behavioral law
for the board is `BOARD-BEHAVIOR.md` — including the interbrain-off parity rule, which
this retirement did not weaken.

History, not deleted: `2026-07-19-board2-prototype.md` and `2026-07-19-board3.md` carry
`SUPERSEDED` headers pointing here.
