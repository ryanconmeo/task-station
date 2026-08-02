# TASKTRAIL

**spec_version 1.0** · a durable, append-only event contract for task episodic memory.

> **Name note:** "TaskStream" was considered and dropped due to existing commercial
> use (taskstream.com is Watermark's higher-ed software; a `task-stream` npm package
> also exists). The contract brand is **Tasktrail** (`tasktrail` in machine
> identifiers).

Tasktrail is the published, consumer-agnostic contract behind task-station's
durable event ledger. A **producer** (task-station) emits raw episodic material — one
JSON line per task mutation, plus full-state task notes — into a directory. A
**consumer** (any second brain: Obsidian, a plain digest, your own) reads it. The
direction is one-way, pull. Nothing here is task-station-specific; any tool could
produce or consume this format.

`spec_version` is SemVer'd **independently of the plugin version**: the plugin ships
many releases without touching the contract, and the contract can rev without a
lockstep plugin bump. The manifest records the `spec_version` a bundle conforms to;
the current one is **1.0**.

> Machine-readable schemas: [`spec/tasktrail.event.schema.json`](../../spec/tasktrail.event.schema.json)
> and [`spec/task-note.frontmatter.schema.json`](../../spec/task-note.frontmatter.schema.json)
> (JSON Schema 2020-12). Validate a bundle with the stdlib-only
> [`spec/validate.py`](../../spec/validate.py). A golden bundle lives in
> [`spec/fixtures/tasktrail/`](../../spec/fixtures/tasktrail/).

## File convention

A Tasktrail bundle is one directory named `tasktrail/`:

```
tasktrail/
  tasktrail.json         # manifest — spec_version, producer, generation[, owner]
  events/                # append-only deltas
    2025-06.jsonl        # one monthly shard, one event per line
    2025-07.jsonl
  notes/                 # full current state, one Markdown note per task
    1-ship-stream-contract.md
  index.md               # human wikilinked listing of the notes
  categories/            # OPTIONAL — category-hub pages (a graph-clustering aid)
    general.md
    personal/            #   OPTIONAL nested sub-hubs — emergent within-category clusters
      hammerspoon.md
  stories/               # OPTIONAL — story-hub pages (cross-category, one per shared story id)
    1234.md
```

`events/` is the delta log; `notes/` is the materialised current state. A consumer
**bootstraps** from `notes/` (full state) then **tails** `events/` from a saved
cursor for deltas — see *Bootstrap*.

A producer MAY also emit an **optional `categories/`** directory of machine-managed
category-hub pages (one per category, each linking its tasks — a graph-clustering aid
mirrored from the producer's own taxonomy), optionally with **nested sub-directories**
of sub-hub pages for emergent within-category clusters (e.g. `categories/personal/hammerspoon.md`);
a consumer that reads only `notes/` + `events/` MUST ignore the whole `categories/` tree,
sub-directories included, exactly as it ignores any other unknown file.

A producer MAY likewise emit an **optional `stories/`** directory of machine-managed
story-hub pages — one `stories/<id>.md` per story id (from a note's structured `story`
field) referenced by one or more tasks, listing those tasks across categories (an
orthogonal, cross-category clustering axis) with the work-item link when known; a
consumer that reads only `notes/` + `events/` MUST ignore the whole `stories/` tree
exactly as it ignores the `categories/` tree.

The **manifest** (`tasktrail.json`) is a JSON object:

| key | type | meaning |
|---|---|---|
| `spec_version` | string `"MAJOR.MINOR"` | contract version this bundle conforms to |
| `producer` | string | e.g. `task-station/1.82.2` |
| `generation` | integer ≥ 1 | bumped on every redaction (see *Redaction*) |
| `owner` | string (optional) | shared-vault owner handle, when scoped |

## Event envelope

Every line in a shard is a JSON object with **sorted keys**:

```json
{"v":1,"ts":"2025-06-10T09:00:00+00:00","n":1,"event":"task.created",
 "task":{"uuid":"t-0001","seq":1},"actor":{"session":"s-1111"},
 "data":{"title":"Ship stream contract","status":"open","goal":"Publish v1.0"}}
```

| field | type | meaning |
|---|---|---|
| `v` | `1` | envelope version (distinct from `spec_version`; bumped only on a breaking envelope change) |
| `ts` | string | **UTC ISO8601, seconds precision**, always the `+00:00` offset |
| `n` | integer ≥ 1 | per-task monotonic counter (see *Ordering*) |
| `event` | string | one of the types below |
| `task.uuid` | string | **stable task id** — the identity key (see *ID stability*) |
| `task.seq` | integer \| null | human-facing per-vault number; cosmetic, may change/absent |
| `actor.session` | string \| null | originating Claude session, or null for a non-session actor |
| `actor.owner` | string (optional) | shared-vault owner handle, when stamped |
| `data` | object | event-kind-specific payload (below), or the redaction stub |

### Event types (all of them, as implemented)

| `event` | `data` shape |
|---|---|
| `task.created` | `{title, status, goal?, color?, effort?}` |
| `task.updated` | `{changed:[field…], fields:{title?,summary?,state?,goal?,effort?,color?}}` — changed scalar values, each capped at 160 chars |
| `task.status` | `{status, closed_ts}` — `closed_ts` is a number when closing, null on reopen |
| `task.event` | `{kind, text}` — a logged activity line (text capped) |
| `task.relation` | `{kind, other:{uuid, seq}}` — one edge per related task |
| `task.checkpoint` | **digest** (below) — a model-curated full snapshot at a save |
| `task.snapshot` | **digest** — a backfilled snapshot for a task not yet in the stream |
| `task.deleted` | `{}` — a tombstone (see *Tombstones*) |
| `task.redacted` | `{generation}` — a redaction marker (see *Redaction*) |

**Privacy:** raw prompt text is NEVER written to the stream. Free text on
non-digest events is capped by the producer; the digest is model-curated.

### The digest (checkpoint / snapshot `data`)

```json
{"goal":"","state":"","steps":[],"summary":"","decisions":[],"prs":[],"stories":[],
 "glossary":[{"name":"Shard","layer":"raw","state":"stable","def":"…"}],
 "brief_path":"briefs/ship-stream-contract.md"}
```

`goal`, `state`, `summary` are strings; `steps`, `prs`, `stories` arrays;
`decisions` a string array. `glossary` and `brief_path` are **first-class optional**
fields — see below.

## First-class optional fields: `glossary[]` and `brief_path`

Both are optional task fields carried WHEN PRESENT and absent otherwise (no empty
placeholder). They appear in two places:

- **Checkpoint/snapshot digest** — structured and authoritative:
  - `glossary` = an array of terms, each `{name, layer, state, def}` (all strings;
    `layer` = which layer of the consumer's brain the term belongs to, e.g. `raw`/`wiki`;
    `state` = the term's lifecycle, e.g. `draft`/`stable`/`deprecated`).
  - `brief_path` = a string path (relative to the note/export root) to a rendered
    brief artifact.
- **Note frontmatter** — `brief_path` is a plain quoted scalar. `glossary`, however,
  is rendered by the note's generic flat-YAML renderer, which **flattens each term
  object to a single stringified scalar** — so glossary terms are NOT machine-parseable
  from frontmatter. Consumers that need the structured `{name,layer,state,def}` shape
  MUST read it from the checkpoint/snapshot digest, not the note. This is a known
  asymmetry, not a bug in the reader.

## Ordering, `n`, and gap detection

Within a task, events carry a monotonic `n` starting at **1**, allocated inside a
cross-process lock so **file (append) order matches `n` order**. Global read order is:
shard name ascending (`2025-06` before `2025-07`), then append order within a shard.

A conformant stream is **gapless**: for each `uuid`, the multiset of `n` values is
exactly `1..N` with none missing or duplicated, and non-decreasing in file order.
`validate.py` (and the producer's `stream --verify`) check this; a gap or reordering
is a corruption and MUST be reported, never silently healed.

## Bootstrap and cursors

- **Bootstrap** = read `notes/` — each note's frontmatter is the full current state of
  one task (`uuid`, `status`, `title`, …). This is the fast path to current state.
- **Tail** = read `events/` from a saved **cursor** for incremental deltas. A cursor is
  `(last shard read, byte/line offset, manifest generation)`. A consumer replays events
  after its cursor, applies them, and advances the cursor. Because shards are monthly
  and append-only, a cursor is stable across runs.
- **Re-sync trigger:** if the manifest `generation` is higher than the cursor's, the
  consumer MUST discard derived state and re-bootstrap (see *Redaction*).

## Tombstones

`task.deleted` is a tombstone: the task's store row is gone. On seeing it a consumer
MUST **drop the task** from derived state (and remove any note it materialised). The
tombstone is emitted with `persist=false` — the final `n` rides on the in-hand event.
The producer also prunes the deleted task's note from `notes/`, so a fresh bootstrap
never re-creates it.

## Redaction and `generation`

Redaction is the right-to-be-forgotten path (distinct from delete: the task still
exists). On redacting a task the producer:

1. rewrites **every** shard, replacing that task's event payloads with the stub
   `{"redacted": true}` (idempotent; already-stubbed lines untouched; the
   `task.redacted` marker row is never stubbed);
2. bumps the manifest `generation` by 1;
3. appends a `task.redacted` event carrying the new `{generation}`;
4. prunes the task's note so its content is forgotten from `notes/` too.

A consumer detects the **generation bump** (manifest `generation` > cursor generation)
and MUST **full re-sync**: discard derived state and re-bootstrap, so the redacted
content is absent from its output. Envelopes (`v/ts/n/event/task/actor`) survive
redaction — only `data` is stubbed — so ordering and gaplessness are preserved.

## Timezone

All `ts` are **UTC ISO8601, seconds precision, `+00:00`**. Note-frontmatter dates
(`created`/`updated`/`closed`) are calendar dates (`YYYY-MM-DD`) in the producer's
local zone and are display-only; the event `ts` is the machine-authoritative time.

## ID stability

`task.uuid` is the **stable identity** across the whole stream and the note frontmatter
`uuid` — it never changes for the life of a task and is the only safe join key. `seq` is
a human-facing convenience that MAY change and MUST NOT be used as an identity key.

## Dedup and update-vs-append

- The log is **append-only**: a correction is a NEW event, never an in-place edit of a
  prior line (redaction, which rewrites `data` to a stub, is the sole exception and is
  gated by the generation bump).
- **Idempotent replay:** applying the same event twice MUST be a no-op. `(uuid, n)` is
  the natural dedup key — a consumer that has already applied `n` for a `uuid` skips it.
- `task.updated` carries only the CHANGED fields; a consumer merges them onto its
  current task state (last-writer-wins by `n`). Full state always comes from the latest
  `task.checkpoint`/`task.snapshot` or the note.

## Evolution (additive-only within a major)

Within `spec_version` major **1**, changes are **additive only**: new optional envelope
keys, new optional `data` keys, new `event` types. A consumer MUST ignore unknown keys
and unknown `event` types (skip, don't fail). Removing/renaming a field or changing a
type is a **major** bump. Producers SHOULD write, and validators SHOULD accept, unknown
additive fields. The envelope `v` is bumped only for a breaking envelope-shape change,
orthogonal to `spec_version`.

## Reference consumers

1. **Obsidian export** — the producer's own `task-station export` / vault sync writes
   the `notes/` + `index.md` half directly as Obsidian-compatible Markdown with
   resolvable `[[wikilinks]]`. (`lib/export.py`, `lib/obsidian_sync.py`.)
2. **tasktrail-digest** — [`spec/consumers/tasktrail-digest.py`](../../spec/consumers/tasktrail-digest.py),
   a stdlib-only, non-Obsidian consumer: bootstraps from `notes/`, tails `events/` with
   a cursor/state file, honours tombstones and generation-bump re-sync, and compiles a
   plain `digest.md` (open tasks, recent closures, per-task one-liners) with zero
   wikilink/frontmatter assumptions. Idempotent: same input → byte-identical output.

## Governance

Tasktrail is currently maintained by the task-station maintainers via the normal PR
process on this repository: changes to the spec, schemas, or fixtures land as PRs and
are reviewed like any other change. `spec_version` is bumped in the same PR that changes
the contract, and `validate.py` + the fixtures are updated in lockstep. **Intent:** if
independent adopters emerge (other producers or consumers), the declared direction is to
move the spec, schemas, and conformance suite to a neutral, vendor-independent home with
shared governance, so no single implementation owns the contract.

## Lineage — Karpathy's llm-wiki

Tasktrail follows the producer/consumer split articulated in Andrej Karpathy's
*llm-wiki* idea: a **producer emits raw episodic material into the consumer's RAW
layer**, and the **consumer alone** curates that raw material up into its wiki/index —
its taxonomy, its links, its distillations. The producer NEVER writes the consumer's
wiki or index; **taxonomy is consumer territory**. In Tasktrail terms: task-station
emits events and full-state notes (raw episodic material); a second brain ingests them
into its own raw layer and decides how to organise, link, and distil them. The
`glossary[]`/`brief_path` fields are producer-supplied *hints* for that curation, not an
imposed taxonomy — the consumer is free to use, remap, or ignore them.
