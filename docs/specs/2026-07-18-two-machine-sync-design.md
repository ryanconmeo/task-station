# Two-machine sync — design (v1)

**Status:** accepted 2026-07-18 — **partly SUPERSEDED, see the box below.** Implemented
in 3.30.0 as `lib/board/sync.py` + `lib/board/station.py`; the shipped behaviour is
documented in [docs/SYNC.md](../SYNC.md), which is the one to read first.

> **Three clauses of this document were corrected after it was ratified. The
> implementation follows the corrections, not the text below.**
>
> 1. **The handle is `<owner>-<uuid>`, NOT `<owner>-<origin-seq>`** (stored full,
>    displayed as the shortest unambiguous prefix). `<owner>-<seq>` does not meet
>    "there cannot be a conflict": the collision axis is the STATION, not the owner —
>    one owner on two unsynced machines hands out the same next number, and two
>    different tasks both become `kosei-512`.
> 2. **A partition is `owners/<owner>/station-<n>/`, numbered from 0** — not one
>    directory per owner. Same reason: two machines of one owner need two write
>    targets, or they are back to sharing a path.
> 3. **The merge is FIELD-level, not record-level LWW.** Lists union, element flags
>    merge per field, and a scalar takes the newest by that field's OWN timestamp
>    while preserving what it replaced. Record-level last-writer-wins would drop a
>    whole machine's offline work on one field's contest.
>
> Still exactly right, and unchanged: full-state JSON over an owner-partitioned git
> exchange, SQLite staying authoritative, the journal not being the sync substrate,
> machine-local `seq`/`stream_n`/`_rev`, relation-edge uuid normalization, tombstones,
> and the rejected-alternatives analysis.

**Problem:** one logical task board across two Macs (same user, both on Tailscale), offline-capable,
no id conflicts, a human-readable cross-machine reference, per-task context that travels, no data loss.

## Decision

Sync **full-state task JSON, not events**: a git-backed exchange directory with
**owner-partitioned writes** (each machine writes only its own subtree ⇒ git never conflicts),
merged into each machine's authoritative local SQLite by a new `task-station sync` command.
Tasktrail is **not** the sync substrate and stays per-machine, one-way, untouched.
The cross-machine human reference is a **write-once handle `<owner>-<origin-seq>`**
(owner names are Ryan-chosen per-machine handles — illustrated here as `studio`/`air`, placeholders until picked).

## Rejected alternatives (code-verified)

- **Event-sourcing over Tasktrail** — two disqualifiers: (a) the stream is deliberately lossy
  (`task.updated` scalars capped at `EVENT_TEXT_MAX = 160`, prompt text banned, digests curated)
  so replay can never reconstruct a full task dict; (b) the per-task gapless counter `stream_n`
  lives **on the task dict** (`_stream_alloc_n`), so any state sync propagates it and corrupts the
  other machine's stream (`verify()` flags gap/dupe). Fixing that means HLCs + a new
  store-hydration consumer + revving the published 1.0 contract. Highest-debt path.
- **File-syncing `tasks.db`** (Dropbox/iCloud/Syncthing/git) — WAL/`-shm` torn copies, whole-file
  LWW silently drops one machine's delta, `UNIQUE(seq)` unmergeable at file granularity.
- **Turso/libSQL replicas** — third-party dep + fork-safety violation, infra, and row replication
  still detonates on `UNIQUE(seq)`.
- **Replacing SQLite with git-JSON as the primary store** — violates "task data is NEVER
  migrated", forfeits FTS/index performance.

Why the pick wins: the store already *is* "one JSON dict per task, keyed by stable uuid4, with
`updated_ts`" — the sync unit exists. Reuses `config.owner()`, the `stream_dir` external-dir
config pattern, `store.mutate()` rev-lock, `create_with_seq()`. No schema change, no migration,
no contract change, no deps. Owner-partitioned paths ⇒ zero git merge conflicts, plus free
history (a clobbered edit is `git show`-recoverable, not lost).

## The three ids

| id | scope | role |
|---|---|---|
| `id` (uuid4) | global, synced | the **only** join key for anything that crosses machines |
| `handle` `<owner>-<origin-seq>` | global, synced, **write-once** | the human cross-machine reference; prefix names the origin machine |
| `seq` (`#N`) | **local only**, never synced | this machine's ergonomic shortcut; MAY differ per machine (spec already blesses this) |

The handle is stamped **once at creation** on the origin machine (its local seq is its own
monotonic counter ⇒ collision-free across owners with zero coordination — the node-prefixed
counter pattern), then immutable and excluded from LWW. Backfill: existing tasks are stamped
`<this-machine's-owner>-<seq>` one time.

**Handle surfacing (per 1.96.0 reconciliation):** fold the handle into each board row's
data-search blob, the expanded-detail header, the terminal `Task` line, and ref resolution
(`resolve_ref` + memo/`--task` paths) so `task-station <cmd> --task <owner>-445` works on both
machines. All other seq ergonomics (`#N`, graph `t:<seq>` ids, rellinks, story hubs) are
render-local and stay seq-based.

## Exchange layout + transport

A **private GitHub repo** (the personal-repo pattern — task data is personal, like
`dotfiles`/`claude-config`; ADO is reserved for company-brain, never task-station or its
data). Alt: bare repo over Tailscale SSH. Owner names below are **placeholders** until the
per-machine handles are chosen:

```
task-sync/
  <owner-a>/tasks/<uuid>.json         # each machine writes ONLY its own owner dir
  <owner-a>/tasks/<uuid>.tombstone    # {"deleted_ts": <epoch>}
  <owner-b>/tasks/<uuid>.json
```

Config: `owner` (exists) must be set and distinct per machine; new `sync_dir` key (clone of the
`stream_dir` pattern; default OFF; env escape `TASK_STATION_SYNC_DIR`). `sync` **refuses** when
owner is unset or equals a foreign subdir's owner (prevents handle-prefix collision).

## Merge policy — one pure function `merge_task(local, remote)`

- **Machine-local, never synced** (stripped on export, re-derived on import): `seq`, `stream_n`,
  `_rev`. This one exclusion dissolves both structural obstacles: each machine's stream stays
  independently gapless; each machine's seq space stays collision-free.
- **Write-once** (synced, never LWW-mutated): `id`, `handle`, `created_ts`.
- **Scalars/context** (title, status, goal, state, steps, summary, decisions, prs, stories,
  glossary, brief_path, color, effort, closed_ts, pinned, …): LWW by `updated_ts`,
  deterministic owner tie-break.
- **Append-only lists** (`log`, in-blob event feed, `sessions`/`session_meta`): union, dedup by
  (ts, content), re-sort, re-trim to `EVENTS_KEEP` — offline work on both machines all survives.
- **Relation-edge normalization (387 reconciliation #1 — required):** stored `related[]` entries
  carry `{seq, kind, id?}` and `build_board_graph` **prefers seq**; a synced edge carrying the
  origin machine's seq would silently link to whatever local task owns that number. The merge
  MUST normalize relation edges to task **uuid** (strip or rewrite `seq`); each machine
  re-derives display seqs. The same audit applies to any other seq-bearing field crossing the
  wire.
- Imports go through `store.mutate()` (rev-guarded — an import can't clobber a concurrent live
  session's write); unknown uuid ⇒ `create_with_seq()` (fresh local seq, handle preserved);
  imported-new tasks get a `task.snapshot` in the local stream via existing `stream --backfill`.

## `task-station sync` flow

1. `git pull --rebase --autostash` (offline ⇒ skip, proceed; push queues).
2. Import every foreign `<uuid>.json` (insert or merge as above).
3. Apply tombstones: `deleted_ts` newer than local `updated_ts` ⇒ delete locally; tombstone wins
   ties (no resurrection); tombstones kept forever.
4. Export every local task whose state differs from own-dir; write tombstones for local deletes.
5. `git add -A && commit && push` (best-effort).
6. Post: Obsidian mirror self-repairs from the store via existing hooks; stream got its
   snapshots in step 2. Zero new code either side.

## What stays per-machine (387 reconciliation #2 — user-facing consequence)

Usage/prompt ledger (`session_usage` + `prompts` tables), transcripts, the Tasktrail stream,
`board.html`, the engine symlink. **Costs, work-mix, and prompt trails for studio sessions will
not render on air** (and vice versa) — the synced JSON carries state/steps/decisions/summary/
memos, not the ledger. `sessions[]` syncs (union) so each machine resumes its **own** sessions;
cross-machine `claude --resume` can never work (transcripts are local) — `/todo save` digests
are the portable context. Consider tagging hub session cards with the owner.
**Liveness is never synced** (387 reconciliation #5): live/running derive from local
`live_sids` only — a task "live" on studio is *not* live on air; the handle prefix already
communicates origin.
**Per-prompt attribution is sync-safe** (387 reconciliation #3): candidates require the scanning
session id in `sessions[]`; foreign sids never resolve locally and stub gracefully
(`_stub_row`, path=None) — verify with a test, no design change needed.

## Persistent context

`/todo save` writes the curated digest onto the task dict ⇒ it rides the JSON.
Ritual: leaving — `/todo save` + `sync`; arriving — `sync` + `/todo <handle>`.

## New code surface

- **`lib/sync.py`** (new, ~250–350 lines): `merge_task()` (pure), `export_all()`,
  `import_foreign()`, tombstones, `run_sync()` (git via stdlib `subprocess`).
- **`lib/config.py`**: `sync_dir` (~15 lines, clone `stream_dir`).
- **`lib/task-station.py`**: `sync` subcommand; handle stamp at create + one-time backfill;
  `resolve_ref` handle match (exact + prefix); handle in board row/detail/terminal;
  delete ⇒ tombstone.
- **Zero changes:** Tasktrail/`stream.py`, `obsidian_sync.py`, the published spec, DB schema.
- Handle lives in the JSON `data` blob for v1 (resolve already scans the listing; index only if
  latency ever shows).

## Tests

merge_task LWW both directions + deterministic tie-break; list union/dedup/retrim;
machine-local fields never cross; write-once immutability; **relation-edge uuid normalization**;
tombstone propagate/tie/no-resurrect; handle stamp/backfill/resolve(+prefix)/import-preserved;
rev-guarded import vs concurrent mutate; offline pull/push degradation; foreign-sid stub-row
gracefulness; fork-safety (no personal paths, sync_dir default OFF, no-op unconfigured).

## Phasing

**v1 (one story/PR):** `sync_dir` + handle (stamp/backfill/resolve/display) + `lib/sync.py` +
manual `task-station sync` + tests + one docs section (id roles + LWW rule).
**Not built (rejected):** CRDTs/HLCs/per-field timestamps; any Tasktrail change or event-replay
consumer; global-seq convergence; a sync daemon (later one-liner: launchd timer or
SessionStart-hook auto-sync); syncing briefs/prompt history/session_usage; server/network-DB
modes; the cross-machine merged stream for the brain (separate per-owner-tee story — orthogonal).

## Companion fold-in (from memo ac07f1f7): ADO artifact capture

ADO artifacts created via the ado MCP are invisible to the board (e.g. a PR created in a tracked
session got no task event and its number was unsearchable). Shape, keeping task-station neutral:
a generic post-tool-use capture that detects PR/work-item URLs in MCP tool **results** ⇒ appends
to the task's `prs[]`/`stories[]` + emits a task event `{url, id, repo}` (both already
searchable + board-rendered); ADO-specific field mapping lives company-brain-side. Separate
work item under the same integration umbrella.

## Risks

LWW scalar clobber when deep-editing the same task on both machines between syncs (log lines
survive; loser recoverable from git history) — sync at session start/end. Clock skew decides
ties (NTP ⇒ negligible; deterministic tie-break prevents flapping). seq divergence is permanent
by design — downstream must key on uuid or handle (lint the personal-brain integration once).
Tombstone beats older edits and wins ties. **Do not** run the rsync baton-pass stopgap and v1
together — pick one substrate.

## Stopgap (pre-v1): single-writer baton pass

```bash
# on the machine you're LEAVING (quit Claude sessions first):
sqlite3 ~/.claude/task-station-data/tasks.db "PRAGMA wal_checkpoint(TRUNCATE);"
# on the machine you're ARRIVING at (over Tailscale):
rsync -a --delete other-mac:~/.claude/task-station-data/ ~/.claude/task-station-data/
```

Ritual = "pull before you start"; never both machines live at once.
