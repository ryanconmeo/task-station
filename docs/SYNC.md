# Sync — one board across two machines

`task-station sync` exchanges tasks between the machines you work on. It is a
**directory of JSON files**, one per task, that each machine reads in full and writes
one corner of.

```
<sync root>/
  README.md
  owners/
    kosei/
      station-0/                 one MACHINE. Written by that machine ALONE.
        station.json             {"number": 0, "label": "Sams-MacBook-Pro"}
        tasks/<uuid>.json
        tasks/<uuid>.tombstone
      station-1/
    jpark/
      station-0/
```

## Why a merge conflict is impossible, not merely unlikely

A station writes to exactly ONE directory — its own `owners/<owner>/station-<n>/` —
and reads every other one. Two machines therefore never name the same path, so git
has nothing to three-way merge and `git pull` can never stop on a conflict.

That is a property of the layout, not of care taken at merge time.
`sync.own_write_path()` is the only way the code names a file to write, and it raises
`PartitionViolation` rather than writing outside the partition, so the invariant is
enforced at one choke point instead of being a rule people remember.
`tests/test_sync.py:NoSharedWritePathTest` asserts the two stations' written-path sets
have empty intersection — which is the claim itself, not a proxy for it.

## The three identifiers, and which one is safe to depend on

| id | scope | what it is for |
|---|---|---|
| `id` / `uuid` (uuid4) | global, synced | the ONLY join key for anything crossing machines |
| `handle` `<owner>-<uuid>` | global, synced, write-once | the human cross-machine reference |
| `seq` (`#444`) | **local only, never synced** | this machine's shortcut; it MAY differ per machine |

### The handle, and why it is not `<owner>-<seq>`

`seq` is handed out machine-locally, so an unsynced laptop and desktop both hand out
the same next number. That is why it never leaves the machine as an identifier, and
why relation edges are stripped of it on export and re-stamped with the local number
on import — a synced edge carrying the origin's `seq` would silently point at
whatever local task owns that number.

The handle is **stamped once at creation and never changed** — not even by a sync, and
not even when the task arrives carrying a name someone else minted. It needs **zero
coordination**: no allocator, no block claim, no bootstrap, no exhaustion policy. Two
machines can create tasks simultaneously while disconnected with no possibility of
collision, because neither is choosing from a shared space. (Block-allocated ranges —
Hi-Lo — were rejected for exactly that: they need a claim to *land* before a task can
be created, which fails precisely when you are offline on the second machine.)

**It is stored in full and displayed abbreviated, and the width is collision-driven.**

```
stored     kosei-e6440959-b7f1-4066-8d21-cd7512f4e9fd
displayed  kosei-e6440959
```

The display starts at 8 uuid characters and **lengthens exactly as far as ambiguity
forces** — the same rule git uses for abbreviated commit hashes. Eight is a floor, not
a maximum: measured on a real 371-task store, a 4-character prefix already had two
collision groups while 6 and 8 had none, so a fixed width is a bug waiting for the
store to grow. An abbreviated handle that names two tasks resolves to **nothing** —
returning the first would hand you a different task from the one you meant.

Set it and read it:

```sh
task-station config --self-alias kosei        # the owner half
task-station config --station-number 1        # which machine (from 0)
task-station config --station-label "desk"    # display only; nothing computes on it
```

## What the merge does, field by field

Measured on a real task record: it is mostly append-only lists plus a handful of
conflictable scalars, so the conflict surface is a few fields, not a task.

- **Machine-local fields never cross at all** — `seq`, the stream counter, the
  optimistic-lock rev, absolute file paths, the pinned session, per-machine
  usage/cost, and each machine's own heal bookkeeping.
- **Write-once fields never change** — `id`, `uuid`, `handle`, `created_ts`. A merge
  may FILL one that is missing; it may never alter one that is set.
- **Lists union by element identity**, and a matched element's FLAGS merge per field:
  one machine superseding a decision while the other pins it applies BOTH. A step
  ticked on either machine stays ticked.
- **Scalars take the newest by THAT FIELD'S OWN timestamp**, and the value they
  replace is pushed onto `<field>_history` — so a sync can never destroy something a
  human wrote. One `updated_ts` per task cannot say which field is newer, so the
  exporter derives per-field stamps by diffing against the payload it last wrote.

There is deliberately **no conflict prompt**. Intra-owner this is one person at two
times, not two people disagreeing, so the later draft is better-informed by
construction — and a sync that stops to ask a question is a sync that stops running.

## Sync does mechanics; heal does meaning

Three different levels, and only two of them are the transport's job:

1. git never conflicts — partitioning;
2. fields rarely conflict — the field-level merge;
3. **MEANING is checked by nothing.**

Two machines can each add a decision that CONTRADICTS the other. The union keeps both,
and the digest then briefs two contradictory current decisions as if both were true —
zero conflicts reported, record silently incoherent. So sync's semantic duty is to
FLAG, never to reconcile: it marks the task dirty so a heal comes due.

That is why the report is a **three-row verdict**. "0 conflicts" alone reads as "the
record is fine", which is precisely the mis-read the third row exists to prevent.

```
  Mechanical  clean — 0 conflicts possible (each station writes only its own partition)
  Judgment    1 task(s) merged · 3 field(s) taken · 4 unioned · 1 value(s) preserved
  Heal-due    1 task(s) flagged — run `/heal` : a union is a re-fragmentation event …
```

## Two destinations: backup and share

They are different things and the transport keeps them apart on purpose.

|  | **backup** | **share** |
|---|---|---|
| what it is for | durability | visibility |
| what goes in it | **every task, unfiltered** | **a chosen subset** |
| who reads it | you | whoever it is shared with |
| filtered? | **never** | **always, on write** |
| merged back? | yes | no — it is a one-way view |

**Backup is never filtered.** A task that never leaves the machine cannot be restored
onto a new one, so filtering the backup would kill the guarantee it exists to provide.

**Share is private by default, and the default is enforced when the file is written.**
A task reaches a share exchange only because a sharing rule on its brain names an
audience for it. With no rule there is **no file** — not a hidden one, not a redacted
one. That matters more than it sounds: a read-side filter leaves the bytes sitting in
a repository other people can read, and then every reader, every future reader and
every tool that walks the tree is a place the leak can happen. A write-side filter
means the bytes are not there at all.

```sh
task-station sync --init  ~/task-sync          # backup  — everything
task-station sync --init-share ~/task-share    # share   — only what you share
task-station brains share <brain> --with org   # the ONLY thing that widens it
```

`sync` runs both, **backup first** — durability before visibility, so if the process
dies between them the copy that survived is the one that loses nothing.

An exchange **declares its own kind** in `exchange.json`, written once at `--init` and
never rewritten. Aiming a backup run at a share exchange is refused rather than
silently un-filtered, and pointing both at one directory is refused outright — one
path that is both the unfiltered backup and the readable share is the single worst
misconfiguration available here, and it is one typo away.

### What a shared task actually publishes

An **allow-list**, not a deny-list — a deny-list leaks every field added after it was
written. At the default trail visibility a shared task publishes its identity, its
goal and its step counts:

```json
{"kind": "share", "audience": ["org"], "visibility": "private",
 "task": {"handle": "kosei-787bcc6b", "title": "SHARED-release-plan",
          "status": "open", "live": false,
          "digest": {"goal": "the team sees this", "state": "",
                     "decisions_tail": [], "steps_done": 0, "steps_total": 0}}}
```

Sharing a task is **not** sharing its trail. `trail_visibility` on the task governs
that separately — `private` (the default) publishes no state and no decisions,
`checkpoints` adds the digest, `full` adds the prompt trail.

Never published at any visibility: your **cost and token spend**, your **sessions**,
work-mix and usage, your **file paths and repo names**, and the summary/history/
glossary narrative. Those are not stripped afterwards — they are never built into a
share view in the first place, so no stripper bug can fail to remove them.

**Un-sharing takes it back.** Remove the rule and the next sync deletes the published
file and leaves a tombstone, so "I removed the rule" is true of the repository and not
only of the config.

## Everyday use

```sh
task-station sync --init ~/task-sync    # create the exchange + this station's corner
task-station sync --status              # who is in the exchange, and which one am I
task-station sync                       # pull, import, export, commit (push if remote)
task-station sync --dry-run             # say what it would do; write nothing
task-station sync --no-net              # never touch the network this run
```

Identity comes from runtime config, never from code — three keys in
`<data dir>/config.json`, each with an environment override:

| key | env | default |
|---|---|---|
| `self_alias` | `TASK_STATION_SELF_ALIAS` | the OS username |
| `station_number` | `TASK_STATION_STATION` | `0` |
| `station_label` | — | macOS `LocalHostName` |
| `sync_dir` | `TASK_STATION_SYNC_DIR` | unset — sync is OFF |

The label is decoration: the folder is the number, handles carry no station
component, and nothing computes on the label — which is what makes renaming a station
a one-field edit in one file that only that station writes.

Ritual: `sync` when you sit down, `sync` when you get up.

## Provisioning a remote — the deliberate step

**`--init` creates a LOCAL git repo and NO remote, and nothing in the code path ever
adds one.** With no remote, `sync` commits locally and sends nothing. Making the
exchange reachable from a second machine is a decision about where your data is
allowed to live, so it is a command you run, not a default.

The rule the layout is built for: **keep personal and work exchanges separate, and
keep backup separate from sharing.** Backup is durability — every task, no reader but
you, or a dead disk loses the ones that never left the machine. Sharing is visibility
— a chosen subset, readable by others. A write-filter belongs on the SHARING
destination and never on the backup one, or the durability guarantee dies with it.

Concretely that means a personal station backs up to a private repo on personal
infrastructure with no sharing remote at all, and a work station backs up to a private
repo on the employer's forge that is yours alone, plus a separate readable repo for
whatever you deliberately mark shared.

```sh
# 1. create a PRIVATE repo on the forge that suits that compartment, then:
task-station sync --init ~/task-sync
git -C ~/task-sync remote add origin <the private repo you just made>
git -C ~/task-sync push -u origin HEAD
```

On the SECOND machine, clone instead of init, and give it its own number — 0 is
already taken:

```sh
git clone <the same remote> ~/task-sync
python3 - <<'PY'
import json, os
p = os.path.expanduser("~/.task-station/config.json")
d = json.load(open(p)) if os.path.exists(p) else {}
d["station_number"] = 1
d["sync_dir"] = os.path.expanduser("~/task-sync")
json.dump(d, open(p, "w"), indent=2)
PY
task-station sync --status                       # expect: two partitions, one starred
```

Use a **separate exchange directory and a separate remote per compartment**, and point
`sync_dir` at whichever one that machine belongs to. Two compartments in one repo is
one access-control mistake away from the wrong reader.
