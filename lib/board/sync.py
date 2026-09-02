"""sync.py — the two-machine transport (task #532, the J-track, step 1).

WHAT THIS IS. One logical task board across several machines, offline-capable, with
NO possibility of a git merge conflict and no coordination before a task can be
created. The exchange is a directory — a git repo when you want history and a remote,
a plain folder when you do not — holding one JSON file per task.

    <sync root>/
      README.md
      owners/
        kosei/
          station-0/                 one MACHINE. Written by that machine ALONE.
            station.json             {"number": 0, "label": "Sams-MacBook-Pro"}
            tasks/<uuid>.json        a task's full synced state
            tasks/<uuid>.tombstone   {"deleted_ts": …} — a delete, propagated
          station-1/
        jpark/
          station-0/

WHY A CROSS-OWNER CONFLICT IS STRUCTURALLY IMPOSSIBLE, and this is the whole point:
a station writes to exactly ONE directory — its own `owners/<owner>/station-<n>/` —
and reads every other. Two writers therefore never name the same path, so git has
nothing to three-way merge and `git pull` can never stop on a conflict. It is not
that conflicts are rare or well-handled; there is no shared write target for one to
occur on. `own_write_path()` is the ONLY way this module names a file to write, and
it raises `PartitionViolation` rather than writing outside the partition — so the
invariant is enforced at the one choke point instead of being a rule people remember.

WHY NOT THE OBVIOUS ALTERNATIVES (all code-verified before this was written):
  • event-sourcing the journal — the stream is deliberately lossy (text capped, prompt
    text banned) so replay cannot reconstruct a task, and its per-task gapless counter
    lives ON the task dict, so syncing state corrupts the other machine's stream.
  • syncing `tasks.db` itself — WAL/-shm torn copies, whole-file last-writer-wins
    silently drops a machine's whole delta, and `UNIQUE(seq)` is unmergeable at file
    granularity.
  • Hi-Lo / block-allocated ids — needs a claim to LAND before a task can be created,
    which fails exactly when the second machine is offline, which is exactly when it
    is handing out numbers.

MECHANICAL CLEANLINESS IS NOT SEMANTIC CLEANLINESS. Three different levels, and only
two of them are this module's job: git never conflicts (partitioning), fields rarely
conflict (the field-level merge below), and MEANING is checked by nothing. Two
machines can each add a decision that CONTRADICTS the other; the union keeps both and
the digest then briefs two contradictory current decisions as if both were true. So
SYNC'S SEMANTIC DUTY IS TO FLAG, NEVER TO RECONCILE — it marks the task
`digest_dirty` so a heal comes due, and the report says so in its own row. A sync
union is literally a re-fragmentation event, which is what heal exists to undo.
That is why the report is a THREE-ROW VERDICT: "0 conflicts" alone reads as "the
record is fine", which is the precise mis-read the three rows exist to prevent.

THE MERGE IS PER FIELD, NOT PER RECORD. Measured on a real task blob: the record is
mostly APPEND-ONLY LISTS plus a handful of conflictable scalars, so the conflict
surface is a few fields, not a task. Rules, in `FIELD POLICY` below:
  • machine-local fields never cross at all (seq, stream counters, costs, file paths);
  • write-once fields never change (id, uuid, handle, created_ts);
  • lists UNION by element identity, and a matched element's FLAGS merge per field —
    one machine superseding a decision while the other pins it applies BOTH;
  • scalars take the newest BY THAT FIELD'S OWN TIMESTAMP and PRESERVE what they
    overwrote, so nothing a human wrote is ever destroyed by a sync.

PER-FIELD TIMESTAMPS ARE THE REAL WORK, and they are derived, not instrumented. One
`updated_ts` per task cannot say which FIELD is newer, and stamping every writer in
the codebase would be a hundred call sites that later drift. Instead the exporter
DIFFS the task against the payload it last wrote into its own partition and stamps
only what changed. The previous payload is already sitting there, so this costs one
file read and cannot fall out of step with the writers.

NO CONFLICT PROMPT, deliberately. Intra-owner this is ONE PERSON AT TWO TIMES, not
two people disagreeing, so the later draft is better-informed by construction — and a
sync that stops to ask a question is a sync that stops running.

NETWORK IS OPT-IN AND ABSENT BY DEFAULT. `run_sync` pulls and pushes only when the
sync root is a git repo that HAS a remote. With no remote it commits locally and says
so. Nothing here provisions a remote, and nothing here can: `sync --init` creates a
LOCAL repo, and adding a remote is a deliberate human step (see docs/SYNC.md).
"""
import hashlib
import json
import os
import re
import subprocess
import time

import decisions as _dec
import station as _station
from core.fsutil import atomic_write as _atomic_write

# TWO DESTINATIONS, AND THEY MUST NEVER BECOME ONE.
#
#   BACKUP  durability. EVERY task, no exceptions, no reader but the owner. A task
#           that never leaves the machine cannot be restored onto a new one, so the
#           backup path is NEVER filtered — filtering it kills the guarantee it
#           exists to provide.
#   SHARE   visibility. A CHOSEN SUBSET, readable by someone else. Filtered ON WRITE:
#           a task with no sharing rule has no file in the share exchange at all.
#
# Filtering on WRITE rather than on read is the safety property. A read-side filter
# means the bytes are sitting in the shared repository and something has to remember
# to hide them; every reader, every future reader, and every tool that walks the tree
# is then a place the leak can happen. A write-side filter means THE BYTES ARE NOT
# THERE, and no reader bug can leak what was never written.
KIND_BACKUP = "backup"
KIND_SHARE = "share"
KINDS = (KIND_BACKUP, KIND_SHARE)

# An exchange declares its OWN kind, in its own root, written at --init. The
# destination's bytes say what it is, so pointing a backup run at a share repo cannot
# silently un-filter it — the mistake that would put every private task into an
# org-readable repository in one command.
EXCHANGE_FILE = "exchange.json"

OWNERS_DIR = "owners"
TASKS_DIR = "tasks"
TASK_EXT = ".json"
TOMBSTONE_EXT = ".tombstone"
SYNC_ENV = "TASK_STATION_SYNC_DIR"
SHARE_ENV = "TASK_STATION_SHARE_DIR"

PAYLOAD_SCHEMA = 1
FIELD_TS = "field_ts"


class DestinationMismatch(RuntimeError):
    """A run was aimed at an exchange whose declared kind is not the one asked for.
    Raised, never coerced: writing unfiltered backup records into a SHARE exchange is
    the exact leak this module exists to prevent, and quietly doing the other thing
    instead would hide the misconfiguration that caused it."""


class PartitionViolation(RuntimeError):
    """A write was aimed outside this station's own partition. Raised rather than
    sanitized: silently redirecting a stray write into the right directory would hide
    the bug that produced it, and writing it where it was aimed is the one thing that
    can manufacture a cross-owner conflict."""


# ---------------------------------------------------------------- FIELD POLICY ----
#
# Every key of a task blob falls in exactly one bucket. A key in NO bucket is a
# scalar: newest-by-its-own-timestamp wins. That default is deliberate — a field
# added later syncs sensibly without anyone remembering to list it here.

# `origin_owner` is stamped on IMPORT and says which OWNER a task came from. It is
# what stops a station republishing somebody else's task into its own partition:
# receiving a peer's task must not make you a second publisher of it, or their edit
# and your stale copy start competing. Locally-created tasks carry no `origin_owner`,
# so they export; a task from ANOTHER OWNER never does. Two stations of the SAME owner
# both keep exporting, which is the point — that is the backup guarantee.

# NEVER LEAVES THE MACHINE and is never overwritten by an import. Two kinds live
# here. (a) Identity/allocators that MUST stay machine-local or the other machine's
# invariants break: `seq` (its own monotonic counter — a synced seq links an edge to
# whatever local task owns that number), `stream_n` (a per-machine gapless counter
# whose verifier flags a gap), `_rev` (the optimistic lock, transient). (b) Data that
# is TRUE ONLY HERE: absolute file paths, the pinned session, per-machine usage
# spans/runs/costs, and each machine's own reconcile bookkeeping — a heal is per
# record but "when did THIS machine last heal it" is per machine, and the import
# itself makes a heal due anyway.
LOCAL_ONLY = frozenset((
    "seq", "_rev", "stream_n", "_stream_alloc_n",
    "files", "brief_path", "pinned_session",
    "spans", "runs", "cost",
    "digest_dirty", "digest_events", "obsidian_dirty", "pressure_nudged",
    "last_heal_ts", "last_heal_kind", "last_heal_note",
    "decisions_at_last_heal", "healed_counts", "chars_at_last_heal",
))

# STAMPED ONCE, NEVER LWW-MUTATED. A merge may FILL one that is missing locally (an
# older task predating handles), but it may never change one that is set.
WRITE_ONCE = frozenset(("id", "uuid", "handle", "created_ts", "created_at",
                        "origin_owner"))

# Counters where both machines allocate independently and the safe answer is the
# high-water mark — taking the newest would re-issue a name the other machine used.
MAX_WINS = frozenset(("hub_ordinal_next",))

# The SEVEN conflictable narrative scalars. Taking the newest is right; DESTROYING
# what it replaced is not, so the overwritten value is pushed onto `<field>_history`
# — the same append-only shape `summary_history` already ships, which is why
# `--restore-summary` and `--restore-state` can bring either one back.
PRESERVED_SCALARS = ("title", "summary", "state", "status", "color", "effort", "goal")

# Lists that UNION. The value is the element's identity — the tuple of keys that says
# "this is the same element", not "this element is unchanged". Flags outside the key
# (done, pinned, superseded_by…) merge per field on a match. `None` means the elements
# are plain scalars and the value itself is the identity.
LIST_KEYS = {
    "sessions": None,
    "projects": None,
    "decisions": ("text",),
    "steps": ("text",),
    "log": ("ts", "note"),
    "history": ("ts", "text"),
    "events": ("id",),
    "memos": ("id",),
    "orders": ("id",),
    "grades": ("ts",),
    "handoffs": ("ts",),
    "ledger": ("ts", "action", "actor"),
    "heal_dismissals": ("fingerprint",),
    "summary_history": ("ts", "text"),
    "state_history": ("ts", "text"),
    "glossary": ("name",),
    "related": ("id", "kind"),
    "prs": ("url",),
    "stories": ("url",),
    "links": ("uuid8", "kind"),
}

# Dict-of-dicts keyed by something the OWNING machine alone writes (a session id), so
# a union by key is exactly right and there is nothing to arbitrate inside a value.
DICT_UNION = frozenset(("session_meta",))


# ------------------------------------------------------------------ the root ----

def sync_root():
    """The configured exchange directory, or None when sync is off (the default).
    env TASK_STATION_SYNC_DIR > config `sync_dir`. Never created as a side effect —
    `sync --init` is the one thing that brings it into existence."""
    env = os.environ.get(SYNC_ENV)
    if env and env.strip():
        return os.path.expanduser(env.strip())
    try:
        import config as _config
        v = _config.get("sync_dir")
    except Exception:
        v = None
    if v and str(v).strip():
        return os.path.expanduser(str(v).strip())
    return None


def share_root():
    """The SHARE exchange, or None when nothing is shared (the default, and the point).
    env TASK_STATION_SHARE_DIR > config `share_dir`."""
    env = os.environ.get(SHARE_ENV)
    if env and env.strip():
        return os.path.expanduser(env.strip())
    try:
        import config as _config
        v = _config.get("share_dir")
    except Exception:
        v = None
    if v and str(v).strip():
        return os.path.expanduser(str(v).strip())
    return None


def exchange_kind(root, default=KIND_BACKUP):
    """What an exchange says it IS, read from its own `exchange.json`. An exchange
    created before kinds existed carries no declaration and reads as `backup`, which
    is the safe default: it means the unfiltered records already in it stay where they
    are rather than a share filter being applied to a repository that never had one."""
    try:
        with open(os.path.join(root, EXCHANGE_FILE), encoding="utf-8") as f:
            k = (json.load(f) or {}).get("kind")
        return k if k in KINDS else default
    except Exception:
        return default


def write_exchange_kind(root, kind, now=None):
    """Stamp an exchange's kind. Written once at --init and never rewritten by a sync:
    changing a backup repository into a share one is a decision with a blast radius,
    not a side effect."""
    if kind not in KINDS:
        raise ValueError("exchange kind must be one of %s" % (KINDS,))
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, EXCHANGE_FILE)
    if os.path.exists(path):
        return exchange_kind(root)
    _atomic_write(path, json.dumps(
        {"kind": kind, "created_ts": time.time() if now is None else now}, indent=1) + "\n")
    return kind


def require_kind(root, kind):
    """The exchange at `root`, proved to be the kind the caller intends. Raises
    DestinationMismatch otherwise."""
    actual = exchange_kind(root)
    if actual != kind:
        raise DestinationMismatch(
            "%s is a %s exchange, but this run treats it as %s. Refusing: a %s run "
            "writes %s, and putting those in the other kind of repository is the leak "
            "this refusal exists to prevent."
            % (root, actual, kind, kind,
               "every task, unfiltered" if kind == KIND_BACKUP
               else "only explicitly shared tasks"))
    return actual


def destinations(backup=None, share=None):
    """Every configured destination, backup first, as
    `[{"root", "kind"}, …]`. Empty when nothing is configured — sync is OFF by default
    and sharing is off by default INSIDE that.

    REFUSES TO LET THE TWO BE THE SAME DIRECTORY. One path that is both the
    unfiltered backup and the readable share is the single worst misconfiguration
    available here, and it is one typo away, so it is checked rather than trusted."""
    b = backup if backup is not None else sync_root()
    sh = share if share is not None else share_root()
    if b and sh and os.path.realpath(os.path.expanduser(b)) == \
            os.path.realpath(os.path.expanduser(sh)):
        raise DestinationMismatch(
            "sync_dir and share_dir are the SAME directory (%s). Backup is every task "
            "and share is a chosen subset; one directory cannot be both, and treating "
            "it as both publishes every private task to whoever can read it." % b)
    out = []
    if b:
        out.append({"root": b, "kind": KIND_BACKUP})
    if sh:
        out.append({"root": sh, "kind": KIND_SHARE})
    return out


def owners_dir(root):
    return os.path.join(root, OWNERS_DIR)


def partition_dir(root, owner, n):
    """`<root>/owners/<owner>/station-<n>`. The alias is validated, not escaped: a
    `..` alias must fail loudly here rather than resolve into another partition."""
    return os.path.join(owners_dir(root), _station.require_alias(owner),
                        _station.dirname(n))


def own_partition_dir(root):
    return partition_dir(root, _station.owner(), _station.number())


def own_write_path(root, *parts):
    """THE ONLY WAY this module names a file it intends to write. Builds the path
    inside this station's own partition and then proves it is inside it — belt and
    braces, because `parts` can come from a task id and an id is data."""
    p = os.path.normpath(os.path.join(own_partition_dir(root), *parts))
    guard_own_path(root, p)
    return p


def guard_own_path(root, path):
    """Raise PartitionViolation unless `path` is inside this station's partition."""
    own = os.path.normpath(os.path.abspath(own_partition_dir(root)))
    target = os.path.normpath(os.path.abspath(path))
    if target != own and not target.startswith(own + os.sep):
        raise PartitionViolation(
            "refusing to write %s — outside this station's partition %s" % (target, own))
    return target


def list_partitions(root):
    """Every partition present in the exchange, sorted, as
    `[{"owner", "number", "path", "label", "own"}, …]`. Unreadable or oddly-named
    directories are skipped rather than raising — a peer can write anything into
    their own subtree and it must never break this machine's sync."""
    out = []
    me = (_station.owner(), _station.number())
    try:
        owners = sorted(os.listdir(owners_dir(root)))
    except Exception:
        return out
    for owner in owners:
        if not _station.valid_alias(owner):
            continue
        odir = os.path.join(owners_dir(root), owner)
        try:
            entries = sorted(os.listdir(odir))
        except Exception:
            continue
        for name in entries:
            n = _station.parse_dirname(name)
            if n is None:                     # station-0 parses to 0 — test `is None`
                continue
            path = os.path.join(odir, name)
            if not os.path.isdir(path):
                continue
            desc = _station.read_descriptor(path) or {}
            out.append({"owner": owner, "number": n, "path": path,
                        "label": desc.get("label") or name,
                        "own": (owner, n) == me})
    return out


def foreign_partitions(root):
    """Every partition this station READS but must never write."""
    return [p for p in list_partitions(root) if not p["own"]]


# --------------------------------------------------------------- the payload ----

def _norm_related(entries):
    """Relation edges, normalized to uuid for the wire. The stored edge carries the
    ORIGIN machine's `seq`, and the graph renderer prefers `seq` — so a synced edge
    would silently point at whatever local task happens to own that number. Dropping
    `seq` on export is the fix; the importer re-derives it locally."""
    out = []
    for e in entries or []:
        if not isinstance(e, dict) or not e.get("id"):
            continue
        out.append({k: v for k, v in e.items() if k != "seq"})
    return out


def export_payload(task, prev=None, now=None):
    """The wire form of one local task: machine-local fields dropped, relation edges
    normalized to uuid, and per-field timestamps stamped by DIFFING against `prev` —
    the payload this station last wrote for this task.

    First export (no `prev`) stamps every field at the task's own `updated_ts`, not
    at now: the fields are as old as the task says they are, and claiming they all
    changed this second would make a first sync beat a peer's genuinely newer edit."""
    now = time.time() if now is None else now
    body = {}
    for k, v in task.items():
        if k in LOCAL_ONLY:
            continue
        body[k] = _norm_related(v) if k == "related" else v
    prev_body = dict((prev or {}).get("task") or {})
    prev_ts = dict((prev or {}).get(FIELD_TS) or {})
    base = task.get("updated_ts") or now
    ts = {}
    for k in set(body) | set(prev_body):
        same = _canon(body.get(k)) == _canon(prev_body.get(k))
        if same and k in prev_ts:
            ts[k] = prev_ts[k]
        elif same:
            ts[k] = base
        else:
            ts[k] = base if prev is None else now
    return {"schema": PAYLOAD_SCHEMA, "owner": _station.owner(),
            "station": _station.number(), FIELD_TS: ts, "task": body}


# ------------------------------------------------------- the share-side filter ----
#
# PRIVATE BY DEFAULT, ENFORCED ON WRITE. A task reaches a share exchange only if a
# sharing rule on ITS BRAIN names an audience for it. No rule means no audience means
# NO FILE — not a hidden file, not a redacted file, no file. brains.json ships with no
# rules, so the default state of a brand-new install shares nothing at all, and the
# only way to widen that is an explicit `brains share` the owner ran.
#
# THE SHARE PAYLOAD IS A VIEW, NOT A RECORD. It carries the feed view-model built with
# `rich=False` — no sessions, no usage, no cost, no work-mix, no prompt trail, no
# resume path, because those are never built for it in the first place — and then goes
# through `feeds.strip_local_only`, the SAME stripper the feed export uses, so the
# trail-visibility policy has one implementation rather than two that can drift apart.
# It is deliberately not mergeable: sharing is one-way visibility, and a view that
# could merge back into the owner's record would be a write path nobody asked for.


def _task_tag(task):
    """The task's category TAG — what a tag-scoped sharing rule matches on."""
    try:
        import categories
        meta = categories.hub_meta(task.get("color")) if task.get("color") else None
        return (meta or {}).get("tag")
    except Exception:
        return None


def share_audience(task, cfg):
    """WHO may see this task, resolved from its brain's sharing rules. Empty list =
    nobody = it is never written to a share exchange.

    A task belonging to ANOTHER OWNER is never shared onward by this station either.
    Receiving someone's task does not make you entitled to republish it, and their
    sharing decision is not yours to widen."""
    if not exports_here(task):
        return []
    tid = task.get("uuid") or task.get("id")
    if not tid:
        return []
    try:
        import brains as _brains
        return list(_brains.shares_for(cfg, tid, _task_tag(task)) or [])
    except Exception:
        return []


# WHAT MAY LEAVE, BY NAME. An ALLOW-LIST, and the direction matters more than the
# contents: a deny-list leaks every field added after it was written, silently and by
# default, which is exactly the failure mode a privacy filter cannot have. A field
# absent from these tuples does not cross, and adding one is a visible edit here.
#
# Three levels, matching the task's own `trail_visibility` and the policy
# `feeds.strip_local_only` already enforces on feeds.
SHARE_IDENTITY = ("uuid8", "handle", "title", "status", "owner", "participants",
                  "brain", "shares", "shared_org", "category", "effort",
                  "updated_ts", "trail_visibility", "forked_from")
SHARE_DIGEST_ALWAYS = ("goal", "steps_done", "steps_total")
SHARE_DIGEST_CHECKPOINTS = ("state", "decisions_tail")
SHARE_CHECKPOINT_EXTRA = ("signals", "relations", "links")
SHARE_FULL_EXTRA = ("prompts",)

# NOT SHAREABLE AT ANY LEVEL, listed so the reasoning is on the record rather than in
# the absence of a name: `tokens` / `cost_usd` / `models` are the owner's spend,
# `summary` / `history` / `glossary` are narrative the trail-visibility levels govern
# elsewhere, `files` / `repos` are paths and project names, and `sessions` /
# `session_tree` / `usage` / `work_mix` / `resume` / `open_command` are machine-local
# by construction. None of them is ever built into a share view.


def share_view(ts, backend, task, cfg):
    """The sync-safe VIEW of one task, or None when it must not leave.

    THREE GATES, all of which must pass, and the first is the one that matters:

      1. AN AUDIENCE EXISTS. No sharing rule → None → no file is ever written. This is
         private-by-default, and it is enforced here rather than at read time so the
         bytes never exist to be leaked.
      2. THE VIEW IS BUILT WITHOUT THE SELF-ONLY BLOCK (`rich=False`) — sessions,
         usage, cost, work-mix, prompt trail and resume path are never constructed, so
         no stripper bug can fail to remove them.
      3. THE RESULT IS PROJECTED THROUGH AN ALLOW-LIST at the task's own trail
         visibility, and then still routed through `feeds.strip_local_only` — two
         independent filters, because one of them being wrong should not be enough."""
    audience = share_audience(task, cfg)
    if not audience:
        return None
    import feeds as _feeds
    vm = _feeds.self_view_model(ts, backend, task, set(), cfg, rich=False)
    vis = (vm.get("trail_visibility") or "private").lower()

    out = {k: vm[k] for k in SHARE_IDENTITY if k in vm}
    out["live"] = False                     # liveness is never federated
    digest = vm.get("digest") or {}
    d = {k: digest.get(k) for k in SHARE_DIGEST_ALWAYS}
    if vis in ("checkpoints", "full"):
        d.update({k: digest.get(k) for k in SHARE_DIGEST_CHECKPOINTS})
        for k in SHARE_CHECKPOINT_EXTRA:
            if k in vm:
                out[k] = vm[k]
    else:
        d["state"] = ""
        d["decisions_tail"] = []
    if vis == "full":
        for k in SHARE_FULL_EXTRA:
            if k in vm:
                out[k] = vm[k]
    out["digest"] = d
    stripped = _feeds.strip_local_only({"tasks": [out],
                                        "local_only": list(_feeds.LOCAL_ONLY_FIELDS)})
    return (stripped.get("tasks") or [None])[0]


def share_payload(ts, backend, task, cfg, now=None):
    """The file this station writes into a SHARE exchange for one task, or None."""
    view = share_view(ts, backend, task, cfg)
    if view is None:
        return None
    return {"schema": PAYLOAD_SCHEMA, "kind": KIND_SHARE,
            "owner": _station.owner(), "station": _station.number(),
            "audience": share_audience(task, cfg),
            "visibility": (task.get("trail_visibility") or "private"),
            "shared_ts": time.time() if now is None else now,
            "task": view}


# ----------------------------------------------------------- the share preview ----
#
# WIDENING IS THE ONE THING YOU CANNOT TAKE BACK. Un-sharing deletes the file, but it
# cannot un-read what somebody already read, so the moment that needs a human is the
# moment visibility GROWS — a task published for the first time, an audience gaining a
# member, or a trail visibility going up a level.
#
# So a share run that would WIDEN refuses, prints exactly what would become visible,
# and names the command that accepts it. NARROWING NEVER ASKS: taking something back
# is always safe, and a transport that stopped to confirm a retraction would be
# training people to confirm without reading.
#
# The baseline is THE PUBLISHED STATE ITSELF — what is already in this station's own
# partition — not a separate ledger of what was acknowledged. That means the record of
# "what I agreed to share" cannot drift from what is actually shared, because they are
# the same bytes.

VIS_RANK = {"private": 0, "checkpoints": 1, "full": 2}


def _vis_rank(v):
    return VIS_RANK.get(str(v or "private").lower(), 0)


def _widened(prev, new):
    """True when `new` makes a task MORE visible than `prev` — a wider audience, or a
    trail visibility a level up. A narrower audience or a lower level is not widening,
    and neither is an edit to a field that was already published."""
    if prev is None:
        return True
    gained = set(new.get("audience") or []) - set(prev.get("audience") or [])
    if gained:
        return True
    return _vis_rank(new.get("visibility")) > _vis_rank(prev.get("visibility"))


def share_plan(ts, root, cfg=None, now=None):
    """What a share run WOULD publish, without publishing any of it.

    Returns `{"publish": [...], "widening": [...], "retract": [...], "withheld": n,
    "first": bool}`. Each publish entry carries the handle, the title, the audience,
    the visibility and THE EXACT FIELD NAMES that would become visible — "exactly what
    becomes visible" has to mean the fields, not a count, or the preview is just a
    number to click past."""
    cfg = _brains_cfg() if cfg is None else cfg
    backend = ts._backend()
    published = {}
    try:
        tdir = own_write_path(root, TASKS_DIR)
        for name in sorted(os.listdir(tdir)):
            if name.endswith(TASK_EXT):
                p = read_payload(os.path.join(tdir, name))
                if p:
                    published[name[:-len(TASK_EXT)]] = p
    except (OSError, PartitionViolation):
        pass
    plan = {"publish": [], "widening": [], "retract": [], "withheld": 0,
            "first": not published}
    live = _local_index(ts)
    for uid, task in sorted(live.items()):
        payload = share_payload(ts, backend, task, cfg, now=now)
        if payload is None:
            plan["withheld"] += 1
            if uid in published:
                plan["retract"].append({"uuid": uid,
                                        "title": task.get("title") or uid[:8]})
            continue
        view = payload.get("task") or {}
        entry = {"uuid": uid,
                 "handle": view.get("handle") or "",
                 "title": view.get("title") or "",
                 "goal": (view.get("digest") or {}).get("goal") or "",
                 "audience": list(payload.get("audience") or []),
                 "visibility": payload.get("visibility") or "private",
                 "fields": sorted(view.keys()),
                 "new": uid not in published}
        plan["publish"].append(entry)
        if _widened(published.get(uid), payload):
            plan["widening"].append(entry)
    for uid, prev in sorted(published.items()):
        if uid not in live:
            plan["retract"].append({"uuid": uid,
                                    "title": (prev.get("task") or {}).get("title") or uid[:8]})
    return plan


def format_share_plan(plan, root, confirmed=False):
    """The preview a human reads before anything becomes visible."""
    out = ["SHARING PREVIEW — %s" % root, ""]
    if not plan["publish"] and not plan["retract"]:
        out.append("  Nothing is shared and nothing would become visible.")
        out.append("  %d task(s) withheld — no sharing rule names an audience for them, "
                   "which is the default." % plan["withheld"])
        return "\n".join(out)
    if plan["publish"]:
        out.append("  WOULD BE VISIBLE — %d task(s):" % len(plan["publish"]))
        for e in plan["publish"]:
            flag = "NEW  " if e["new"] else "     "
            out.append("    %s%s  %s" % (flag, e["handle"] or e["uuid"][:8], e["title"]))
            out.append("           to: %s   ·   trail: %s"
                       % (", ".join(e["audience"]) or "(nobody)", e["visibility"]))
            if e["goal"]:
                out.append("           goal (published verbatim): %s" % e["goal"])
            out.append("           fields: %s" % ", ".join(e["fields"]))
    if plan["retract"]:
        out.append("")
        out.append("  WOULD BE WITHDRAWN — %d task(s): %s"
                   % (len(plan["retract"]),
                      ", ".join(e["title"] for e in plan["retract"])))
    out.append("")
    out.append("  %d task(s) stay private (no sharing rule)." % plan["withheld"])
    if plan["widening"] and not confirmed:
        out.append("")
        out.append("  THIS WOULD WIDEN VISIBILITY on %d task(s) and has NOT been "
                   "performed." % len(plan["widening"]))
        out.append("  Publishing cannot be undone by un-publishing — a retraction "
                   "deletes the file, it does not un-read what was read.")
        out.append("  Re-run with --confirm-share to accept exactly the above.")
    return "\n".join(out)


def _canon(v):
    """A comparable, order-stable rendering of a field value — used only to answer
    'did this field change since the last export?'."""
    try:
        return json.dumps(v, sort_keys=True, default=str)
    except Exception:
        return repr(v)


def field_ts(payload, field, default=0.0):
    """When `field` last changed, per the payload that carries it. Falls back to the
    record's `updated_ts` so a payload written before per-field stamps existed still
    compares sensibly instead of losing every contest."""
    d = (payload or {}).get(FIELD_TS) or {}
    if field in d:
        try:
            return float(d[field])
        except (TypeError, ValueError):
            pass
    try:
        return float(((payload or {}).get("task") or {}).get("updated_ts") or default)
    except (TypeError, ValueError):
        return default


# ----------------------------------------------------------------- the merge ----

def _elem_key(field, el):
    """The identity of one list element — what makes two elements THE SAME element
    rather than two. Falls back to the whole element so an unknown shape unions
    safely (worst case a near-duplicate survives; nothing is ever lost)."""
    keys = LIST_KEYS.get(field, ())
    if keys is None:
        return _canon(el)
    if field == "decisions":
        return _dec.text(el).strip()
    if isinstance(el, dict) and keys:
        return tuple(_canon(el.get(k)) for k in keys)
    return _canon(el)


def _merge_elem(local, remote):
    """Merge the FLAGS of one matched list element. Union of keys; a boolean present
    on both is OR'd (one machine ticking a step and the other not must not untick
    it); any other clash keeps the local value. Returns (merged, clashed)."""
    if not isinstance(local, dict) or not isinstance(remote, dict):
        return local, False
    out = dict(local)
    clashed = False
    for k, rv in remote.items():
        if k not in out:
            out[k] = rv
        elif out[k] == rv:
            continue
        elif isinstance(out[k], bool) and isinstance(rv, bool):
            out[k] = out[k] or rv
        else:
            clashed = True
    return out, clashed


def _union_list(field, local, remote):
    """Union two lists by element identity, local order first then whatever the
    remote adds. Returns (merged, added_count, flag_merges, clashes)."""
    local = list(local or [])
    remote = list(remote or [])
    index = {}
    out = []
    for el in local:
        k = _elem_key(field, el)
        if k in index:
            continue
        index[k] = len(out)
        out.append(el)
    added = flagged = clashes = 0
    for el in remote:
        k = _elem_key(field, el)
        if k in index:
            merged, clash = _merge_elem(out[index[k]], el)
            if merged != out[index[k]]:
                out[index[k]] = merged
                flagged += 1
            clashes += 1 if clash else 0
        else:
            index[k] = len(out)
            out.append(el)
            added += 1
    return out, added, flagged, clashes


def history_field(field):
    """Where an overwritten `field` is preserved. `summary` keeps its existing
    `summary_history` name — the restore surface and 20 live tasks already use it."""
    return "%s_history" % field


def _preserve(task, field, value, src, now):
    """Push the value a merge is about to overwrite onto `<field>_history`, in the
    same `{text, ts, sid}` shape `summary_history` already carries so the existing
    reader and `--restore-*` work unchanged. `src` names the station it lost to."""
    text = value if isinstance(value, str) else _canon(value)
    if not str(text or "").strip():
        return False
    task.setdefault(history_field(field), []).append(
        {"text": text, "ts": now, "sid": None, "src": src})
    return True


def merge_task(local, remote_payload, now=None):
    """Merge one remote payload into a local task dict. PURE — it mutates and returns
    a COPY, and reads nothing but its arguments, so `store.mutate()` can re-run it on
    reloaded state after a rev conflict.

    Returns `(merged, report)`; `report` is
    `{"taken": [field…], "unioned": {field: n}, "preserved": [field…],
      "filled": [field…], "clashes": {field: n}, "changed": bool}`."""
    now = time.time() if now is None else now
    merged = json.loads(json.dumps(local, default=str))
    rtask = dict((remote_payload or {}).get("task") or {})
    src = "%s/%s" % ((remote_payload or {}).get("owner") or "?",
                     _station.dirname((remote_payload or {}).get("station") or 0))
    rep = {"taken": [], "unioned": {}, "preserved": [], "filled": [],
           "clashes": {}, "changed": False}
    local_payload = {FIELD_TS: local.get(FIELD_TS) or {}, "task": local}

    for field in sorted(set(rtask) | set(merged)):
        if field in LOCAL_ONLY or field == FIELD_TS:
            continue
        if field not in rtask:
            continue                                  # nothing to bring across
        rv = rtask[field]
        if field in WRITE_ONCE:
            if merged.get(field) in (None, "") and rv not in (None, ""):
                merged[field] = rv
                rep["filled"].append(field)
                rep["changed"] = True
            continue
        if field in MAX_WINS:
            try:
                hi = max(int(merged.get(field) or 0), int(rv or 0))
            except (TypeError, ValueError):
                continue
            if hi != merged.get(field):
                merged[field] = hi
                rep["taken"].append(field)
                rep["changed"] = True
            continue
        if field in DICT_UNION:
            cur = dict(merged.get(field) or {})
            added = 0
            for k, v in (rv or {}).items():
                if k not in cur:
                    cur[k] = v
                    added += 1
            if added:
                merged[field] = cur
                rep["unioned"][field] = rep["unioned"].get(field, 0) + added
                rep["changed"] = True
            continue
        if field in LIST_KEYS or isinstance(rv, list):
            out, added, flagged, clashes = _union_list(field, merged.get(field), rv)
            if added or flagged:
                merged[field] = out
                rep["unioned"][field] = rep["unioned"].get(field, 0) + added + flagged
                rep["changed"] = True
            if clashes:
                rep["clashes"][field] = clashes
            continue
        # scalar: newest by THAT FIELD's own timestamp, preserving what it replaces
        if _canon(merged.get(field)) == _canon(rv):
            continue
        if field_ts(remote_payload, field) <= field_ts(local_payload, field):
            continue
        if field in PRESERVED_SCALARS and _preserve(merged, field, merged.get(field),
                                                    src, now):
            rep["preserved"].append(field)
        merged[field] = rv
        rep["taken"].append(field)
        rep["changed"] = True

    # updated_ts/at are the record's high-water mark, never a contest.
    try:
        if float(rtask.get("updated_ts") or 0) > float(merged.get("updated_ts") or 0):
            merged["updated_ts"] = rtask.get("updated_ts")
            merged["updated_at"] = rtask.get("updated_at") or merged.get("updated_at")
    except (TypeError, ValueError):
        pass
    return merged, rep


def rederive_related_seqs(task, by_id):
    """Put a LOCAL `seq` back on each relation edge, from this machine's numbering.
    Export strips the origin's seq precisely so it cannot mislead; this is the other
    half. An edge whose target is not here yet simply carries no seq."""
    changed = False
    for e in task.get("related") or []:
        if not isinstance(e, dict):
            continue
        other = by_id.get(e.get("id"))
        want = other.get("seq") if other else None
        if e.get("seq") != want:
            if want is None:
                e.pop("seq", None)
            else:
                e["seq"] = want
            changed = True
    return changed


# -------------------------------------------------------------------- files ----

def read_payload(path):
    """A payload file → dict, or None when missing/unparseable. Never raises: one
    corrupt file in a peer's partition must degrade to skipping that task, not to a
    sync that refuses to run."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) and isinstance(d.get("task"), dict) else None
    except Exception:
        return None


def read_tombstone(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except Exception:
        return None


_UUID_RE = re.compile(r"^[0-9a-fA-F-]{8,64}$")


def _safe_stem(task_id):
    """A task id is data; it names a file, so it is checked before it does."""
    s = str(task_id or "").strip()
    if not _UUID_RE.match(s):
        raise PartitionViolation("task id %r cannot name a sync file" % (task_id,))
    return s


def write_own_payload(root, task, prev=None, now=None):
    """Write one task into THIS station's partition. Returns the payload written."""
    payload = export_payload(task, prev=prev, now=now)
    path = own_write_path(root, TASKS_DIR, _safe_stem(task.get("id")) + TASK_EXT)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _atomic_write(path, json.dumps(payload, indent=1, sort_keys=True,
                                   ensure_ascii=False, default=str) + "\n")
    return payload


def own_payload_path(root, task_id):
    return own_write_path(root, TASKS_DIR, _safe_stem(task_id) + TASK_EXT)


def write_own_tombstone(root, task_id, now=None):
    """Record a local delete so peers apply it. Tombstones are kept forever — a
    dropped tombstone resurrects the task on the next sync from a peer that still
    holds it."""
    now = time.time() if now is None else now
    path = own_write_path(root, TASKS_DIR, _safe_stem(task_id) + TOMBSTONE_EXT)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _atomic_write(path, json.dumps({"deleted_ts": now, "owner": _station.owner(),
                                    "station": _station.number()}) + "\n")
    live = own_payload_path(root, task_id)
    try:
        os.remove(live)
    except OSError:
        pass
    return path


def ensure_own_partition(root):
    """Create this station's partition and stamp its `station.json`. The descriptor
    is rewritten every time so a relabelled station self-corrects — it is one field
    in one file that only this station writes, which is what keeps renaming free."""
    p = own_partition_dir(root)
    os.makedirs(os.path.join(p, TASKS_DIR), exist_ok=True)
    _atomic_write(os.path.join(p, _station.STATION_FILE),
                  json.dumps(_station.descriptor(), indent=1) + "\n")
    return p


SHARE_README = """# task-station SHARE exchange

A **chosen subset**, readable by the people it is shared with. It is not a backup and
it is not a mirror of anybody's board.

A task appears here only because a sharing rule on its brain names an audience for it.
A task with no rule has **no file here at all** — not a hidden one, not a redacted one.
The filter runs when the file is written, so there is nothing here for a reader bug to
leak.

Each file is a read-only VIEW, cut to the task's own trail visibility. It is
deliberately not mergeable: sharing is one-way.

Do not point a backup sync at this directory. `exchange.json` says what this is, and
the transport refuses the mismatch.
"""

README = """# task-station sync repo

One JSON file per task, under `owners/<owner>/station-<n>/tasks/<uuid>.json`.

**Each station writes ONLY its own `station-<n>` directory** and reads every other
one. Two writers therefore never touch the same path, so this repo cannot produce a
merge conflict — that is the design, not a coincidence, and editing another owner's
subtree by hand is the one way to break it.

`<uuid>.tombstone` marks a deleted task and is kept forever; removing one resurrects
the task from any peer that still holds it.
"""


def init_root(root, kind=KIND_BACKUP):
    """Bring an exchange into existence: its declared KIND, the layout, this station's
    partition, a README that says which kind it is, and a LOCAL git repo when git is
    available. Creates no remote and contacts no network — adding a remote is a
    deliberate human step.

    The kind is stamped FIRST and never rewritten, so an existing exchange keeps the
    identity it already has and `--init` on it is a no-op rather than a conversion."""
    os.makedirs(owners_dir(root), exist_ok=True)
    actual = write_exchange_kind(root, kind)
    ensure_own_partition(root)
    readme = os.path.join(root, "README.md")
    if not os.path.exists(readme):
        _atomic_write(readme, SHARE_README if actual == KIND_SHARE else README)
    created_repo = False
    # NEVER NEST A REPO. An exchange placed inside an existing repo (a brain repo's
    # `tasks/` tree, decision 444:187) is already versioned by that repo and already
    # has its remote. `git init` there would create an embedded repo the outer one
    # cannot even stage — `git add -A` fails outright with "does not have a commit
    # checked out" — so the knowledge plane's own commits break. Checked via
    # is_git_repo, which asks git rather than looking for a .git directory.
    if not is_git_repo(root) and _git_available():
        created_repo = _git(root, "init", "-q").ok
    return {"root": root, "kind": actual, "requested_kind": kind,
            "partition": own_partition_dir(root),
            "git": is_git_repo(root),
            "created_repo": created_repo}


# ---------------------------------------------------------------------- git ----

class _Res(object):
    __slots__ = ("ok", "out")

    def __init__(self, ok, out):
        self.ok, self.out = ok, out


def _git_available():
    try:
        return subprocess.run(["git", "--version"], capture_output=True,
                              timeout=10).returncode == 0
    except Exception:
        return False


def _git(root, *args, **kw):
    try:
        r = subprocess.run(["git", "-C", root] + list(args), capture_output=True,
                           text=True, timeout=kw.get("timeout", 120))
        return _Res(r.returncode == 0, ((r.stdout or "") + (r.stderr or "")).strip())
    except Exception as e:
        return _Res(False, str(e))


def is_git_repo(root):
    """Is `root` INSIDE a git repo — not "is it a repo ROOT", which is a different
    question and the one this used to answer. `isdir(root/.git)` is False for every
    SUBTREE of a repo, so an exchange living at `<brain repo>/tasks` read as "no repo,
    no remote" and silently never synced. Worse, `init_root` then ran `git init` there
    and NESTED a repo, after which the outer repo could not commit at all. Ask git,
    which walks up; every other call here already goes through `git -C root`."""
    if not os.path.isdir(root):
        return False
    return _git(root, "rev-parse", "--git-dir").ok


def has_remote(root):
    """True when the exchange has somewhere to go. NOTHING in this module reaches the
    network unless this is True, and nothing here ever adds a remote."""
    if not is_git_repo(root):
        return False
    r = _git(root, "remote")
    return bool(r.ok and r.out.strip())


# ------------------------------------------------------------------ the run ----

def collect_remote(root):
    """`({uuid: payload}, {uuid: tombstone})` for the exchange.

    PAYLOADS come from FOREIGN partitions only — this station's own file is the thing
    being compared against, not a source. TOMBSTONES come from EVERY partition, THIS
    STATION'S INCLUDED, and that asymmetry is load-bearing: a task deleted here is
    still published by every peer that has not synced yet, so ignoring our own
    tombstone would re-import the task we just deleted. That is exactly what happened
    the first time this ran — the delete survived the store and died on the next
    sync."""
    payloads, tombs = {}, {}
    for part in list_partitions(root):
        tdir = os.path.join(part["path"], TASKS_DIR)
        try:
            names = sorted(os.listdir(tdir))
        except Exception:
            continue
        for name in names:
            path = os.path.join(tdir, name)
            if name.endswith(TASK_EXT):
                if part["own"]:
                    continue
                p = read_payload(path)
                if not p:
                    continue
                uid = (p.get("task") or {}).get("id")
                if not uid:
                    continue
                cur = payloads.get(uid)
                if cur is None or _newest(p) > _newest(cur):
                    payloads[uid] = p
            elif name.endswith(TOMBSTONE_EXT):
                t = read_tombstone(path)
                if t:
                    uid = name[:-len(TOMBSTONE_EXT)]
                    if uid not in tombs or (t.get("deleted_ts") or 0) > (
                            tombs[uid].get("deleted_ts") or 0):
                        tombs[uid] = t
    return payloads, tombs


def _newest(payload):
    try:
        return max([float(v) for v in (payload.get(FIELD_TS) or {}).values()] or [0.0])
    except Exception:
        return 0.0


def exports_here(task):
    """True when THIS station should publish `task` into its own partition. Every task
    this owner owns is published — backup is not sharing, and a task that never leaves
    the machine cannot be restored onto a new one. A task received from a DIFFERENT
    owner is not republished."""
    origin = (task.get("origin_owner") or "").strip()
    return not origin or origin == _station.owner()


def _local_index(ts):
    return {t.get("id"): t for t in ts.all_tasks() if t.get("id")}


# --------------------------------------------------------------- the thin relay ----
#
# "STATION X IS AT REV Y." That sentence is the entire relay protocol, and it is a
# FILE, not a service: after a sync each station writes the content revision of its own
# partition into `rev.json` inside that partition. A subscriber compares the revs it
# can see against the ones it has already pulled, and pulls only when they differ.
#
# WHY A FILE AND NOT A DAEMON. The programme's standing ruling is to ADOPT transport
# rather than build a second one: git already delivers bytes between machines, already
# has auth, and already works offline. A relay daemon would be a second replication
# engine to run, secure and debug, for the sole benefit of lower latency. So the
# DURABLE part is built here — the rev, the seen-ledger, and the changed-detection —
# and the delivery stays adopted. An optional push relay can later feed exactly this
# same rev signal without any of the below changing.
#
# THREE TIERS, AND THE FLOOR IS ALWAYS AVAILABLE:
#   manual        `sync` — always works, needs nothing, and is the floor.
#   on change     `sync --if-changed` — the cheap poll. Reads revs, syncs only if one
#                 moved. This is what a hook or a timer should call.
#   on ping       a relay pushing the same rev signal. Not built; not needed for
#                 correctness; the seam is here when it is.
#
# THE BOARD NEVER MAKES A NETWORK CALL, AND THAT IS TESTED RATHER THAN INTENDED.
# board.html is a static `file://` page; its refresh polls a LOCAL script sidecar. See
# tests/test_relay.py:BoardMakesNoNetworkCallsTest, which greps the RENDERED page for
# fetch/XHR/WebSocket/EventSource/sendBeacon — the artifact, not the source.

REV_FILE = "rev.json"
SEEN_FILE = "sync-seen.json"


def partition_rev(part_path):
    """A stable content revision for one partition — it changes exactly when that
    station's published task data changes, and not when a timestamp moves."""
    h = hashlib.sha1()
    tdir = os.path.join(part_path, TASKS_DIR)
    try:
        names = sorted(os.listdir(tdir))
    except OSError:
        return ""
    seen = 0
    for n in names:
        if not (n.endswith(TASK_EXT) or n.endswith(TOMBSTONE_EXT)):
            continue
        try:
            with open(os.path.join(tdir, n), "rb") as f:
                body = f.read()
        except OSError:
            continue
        seen += 1
        h.update(n.encode("utf-8"))
        h.update(hashlib.sha1(body).hexdigest().encode("ascii"))
    # An EMPTY partition has no rev, deliberately — not the hash of nothing. A station
    # that has published nothing yet has nothing to pull, and reporting it as "moved"
    # would make the very first `--check` on a fresh exchange claim work that does not
    # exist, which is how a cadence hook learns to be ignored.
    return h.hexdigest()[:16] if seen else ""


def write_own_rev(root, now=None):
    """Publish this station's rev — the ping, as a file. Written INSIDE this station's
    own partition, so it is subject to the same one-writer rule as everything else and
    cannot conflict."""
    rev = partition_rev(own_partition_dir(root))
    path = own_write_path(root, REV_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _atomic_write(path, json.dumps(
        {"rev": rev, "owner": _station.owner(), "station": _station.number(),
         "ts": time.time() if now is None else now}, indent=1) + "\n")
    return rev


def read_partition_rev(part_path):
    """A partition's published rev. Falls back to COMPUTING it when `rev.json` is
    absent — a station that has not published one yet is still readable, and a missing
    ping must never mean "nothing changed"."""
    try:
        with open(os.path.join(part_path, REV_FILE), encoding="utf-8") as f:
            rev = (json.load(f) or {}).get("rev")
        if rev:
            return str(rev)
    except Exception:
        pass
    return partition_rev(part_path)


def _seen_path():
    try:
        import paths as _paths
        return os.path.join(_paths.data_dir(), SEEN_FILE)
    except Exception:
        return None


def load_seen():
    """Which foreign revs this machine has already pulled. LOCAL state: it says what
    THIS machine has seen, so it never belongs in the shared exchange."""
    p = _seen_path()
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_seen(seen):
    p = _seen_path()
    if not p:
        return
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        _atomic_write(p, json.dumps(seen, indent=1, sort_keys=True) + "\n")
    except Exception:
        pass


def _seen_key(root, owner, number):
    return "%s|%s|%d" % (os.path.realpath(os.path.expanduser(root)), owner, int(number))


def check_changes(root, seen=None):
    """Foreign partitions whose rev has moved since this machine last pulled them:
    `[{"owner", "number", "label", "rev", "seen"}, …]`.

    A READ. It never records anything — a check that marked things seen would make the
    next real sync skip work it never did."""
    seen = load_seen() if seen is None else seen
    out = []
    for p in foreign_partitions(root):
        rev = read_partition_rev(p["path"])
        if not rev:
            continue
        was = seen.get(_seen_key(root, p["owner"], p["number"]))
        if rev != was:
            out.append({"owner": p["owner"], "number": p["number"],
                        "label": p["label"], "rev": rev, "seen": was})
    return out


def mark_seen(root, seen=None):
    """Record every foreign partition's CURRENT rev as pulled. Called only after a
    sync that actually ran, never after a check."""
    seen = load_seen() if seen is None else seen
    for p in foreign_partitions(root):
        rev = read_partition_rev(p["path"])
        if rev:
            seen[_seen_key(root, p["owner"], p["number"])] = rev
    save_seen(seen)
    return seen


def _brains_cfg():
    """The loaded brains.json — where every sharing rule lives. A hiccup degrades to
    the DEFAULT config, which has no rules at all, so a failure to read the sharing
    config shares NOTHING rather than sharing everything. The safe direction is not an
    accident here; it is the only acceptable one."""
    try:
        import brains as _brains
        import paths as _paths
        return _brains.load(_paths.data_dir())
    except Exception:
        try:
            import brains as _brains
            return _brains._default()
        except Exception:
            return {"brains": {}, "assign": {}}


def _export_share(ts, root, live, tombs, now, dry_run, rep, confirm=False):
    """Write this station's SHARE partition: the shared subset, and nothing else.

    Three outcomes per task, all counted:
      SHARED     an audience exists → the stripped view is written;
      WITHHELD   no audience → NO FILE IS WRITTEN. The default for every task.
      RETRACTED  an audience USED to exist and no longer does → the file is removed and
                 a tombstone left behind. Un-sharing has to actually take something
                 back, or "I removed the rule" would be a lie the repository contradicts."""
    cfg = _brains_cfg()
    backend = ts._backend()
    # THE WIDENING GATE. Computed BEFORE anything is written, so a run that would make
    # something newly visible writes nothing at all rather than writing most of it and
    # then asking. Narrowing is never gated — see the note above VIS_RANK.
    plan = share_plan(ts, root, cfg=cfg, now=now)
    rep["plan"] = plan
    if plan["widening"] and not confirm:
        rep["blocked"] = True
        rep["withheld"] = plan["withheld"]
        return
    published = set()
    for uid, task in sorted(live.items()):
        if uid in tombs:
            continue
        try:
            path = own_payload_path(root, uid)
        except PartitionViolation as e:
            rep["violations"].append(str(e))
            continue
        payload = share_payload(ts, backend, task, cfg, now=now)
        if payload is None:
            rep["withheld"] += 1
            if os.path.exists(path):
                if not dry_run:
                    write_own_tombstone(root, uid, now=now)
                rep["retracted"].append(_ref(task))
            continue
        published.add(uid)
        # Re-sharing has to CLEAR the retraction, or the exchange carries the task and
        # its tombstone at once and a peer is told two opposite things.
        tomb = path[:-len(TASK_EXT)] + TOMBSTONE_EXT
        if os.path.exists(tomb) and not dry_run:
            try:
                os.remove(tomb)
            except OSError:
                pass
        prev = read_payload(path)
        if prev is not None and _canon(prev.get("task")) == _canon(payload.get("task")) \
                and (prev.get("audience") or []) == (payload.get("audience") or []):
            rep["shared"] += 1
            continue
        if not dry_run:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            _atomic_write(path, json.dumps(payload, indent=1, sort_keys=True,
                                           ensure_ascii=False, default=str) + "\n")
        rep["shared"] += 1
        rep["exported"].append(_ref(task))
    # A file for a task that no longer exists locally is retracted too — the same
    # sweep the backup path runs, for the same reason.
    try:
        for name in sorted(os.listdir(own_write_path(root, TASKS_DIR))):
            if not name.endswith(TASK_EXT):
                continue
            uid = name[:-len(TASK_EXT)]
            if uid in published or uid in live:
                continue
            if not dry_run:
                write_own_tombstone(root, uid, now=now)
            rep["retracted"].append(uid[:8])
    except (OSError, PartitionViolation):
        pass


def run_sync(ts, root=None, now=None, dry_run=False, no_net=False, kind=None,
             confirm_share=False):
    """One full sync pass. Returns the report dict `format_report` renders.

    ORDER MATTERS. Pull, then IMPORT, then EXPORT: importing first means this
    station's own export already carries whatever it just learned, so a third station
    reaching either of us sees a consistent record after one round instead of two.

    Every step degrades rather than aborts — an unreachable remote, a corrupt peer
    file, a task that vanished mid-run. A transport that refuses to run when one
    thing is wrong is a transport that stops being run."""
    now = time.time() if now is None else now
    root = root or sync_root()
    rep = {"root": root, "now": now, "dry_run": bool(dry_run),
           "git": {"repo": False, "remote": False, "pull": None, "push": None,
                   "commit": None},
           "created": [], "merged": [], "deleted": [], "exported": [],
           "tombstoned": [], "violations": [], "unreadable": 0,
           "fields": {"taken": 0, "unioned": 0, "preserved": 0, "filled": 0},
           "clashes": {}, "heal_due": [], "partitions": [],
           "kind": kind or KIND_BACKUP, "shared": 0, "withheld": 0, "retracted": [],
           "blocked": False, "plan": None}
    if not root:
        rep["error"] = ("sync is OFF — no exchange directory configured. "
                        "`task-station sync --init <dir>` creates one, or set "
                        "`sync_dir` in config / %s in the environment." % SYNC_ENV)
        return rep
    # WHAT THIS EXCHANGE IS comes from the exchange itself, not from the caller's
    # intention. A caller that states a kind has it CHECKED against the declaration and
    # refused on a mismatch — writing unfiltered records into a share repository is the
    # leak, and it is one wrong flag away.
    try:
        rep["kind"] = require_kind(root, kind) if kind else exchange_kind(root)
    except DestinationMismatch as e:
        rep["error"] = str(e)
        return rep
    ensure_own_partition(root)
    rep["git"]["repo"] = is_git_repo(root)
    rep["git"]["remote"] = has_remote(root) and not no_net

    if rep["git"]["remote"] and not dry_run:
        r = _git(root, "pull", "--rebase", "--autostash", "-q")
        rep["git"]["pull"] = "ok" if r.ok else ("offline/failed: %s" % r.out[:200])

    local = _local_index(ts)
    rep["partitions"] = [{"owner": p["owner"], "number": p["number"],
                          "label": p["label"]} for p in list_partitions(root)]

    # -- A SHARE EXCHANGE IS EXPORT ONLY, and it exits here.
    #
    # Nothing in it is imported into the store: every file in it is a stripped,
    # one-way VIEW, and importing a view as if it were a record would fabricate tasks
    # out of somebody's redacted digest. Reading peers' shared views is a RENDER
    # concern (peer feeds), not a store concern, so it does not belong on this path.
    if rep["kind"] == KIND_SHARE:
        _export_share(ts, root, local, {}, now, dry_run, rep, confirm=confirm_share)
        return _finish(root, rep, dry_run)

    # -- LOCAL DELETES FIRST, and the order is the whole point. A uuid this station
    #    published and no longer holds was deleted HERE. Every peer still publishes
    #    it, so unless the tombstone exists before the import runs, the import brings
    #    the task straight back and the delete is silently undone.
    try:
        own_tasks_dir = own_write_path(root, TASKS_DIR)
        for name in sorted(os.listdir(own_tasks_dir)):
            if not name.endswith(TASK_EXT):
                continue
            uid = name[:-len(TASK_EXT)]
            if uid in local:
                continue
            if not dry_run:
                write_own_tombstone(root, uid, now=now)
            rep["tombstoned"].append(uid[:8])
    except (OSError, PartitionViolation):
        pass

    payloads, tombs = collect_remote(root)

    # -- tombstones first: a delete must beat an older edit, and win ties, or the
    #    task resurrects on the very next pass from whoever still holds it.
    for uid, tomb in sorted(tombs.items()):
        t = local.get(uid)
        if not t:
            continue
        try:
            if float(tomb.get("deleted_ts") or 0) >= float(t.get("updated_ts") or 0):
                if not dry_run:
                    ts.delete_task(uid)
                rep["deleted"].append(_ref(t))
                local.pop(uid, None)
        except (TypeError, ValueError):
            continue

    # -- import
    for uid, payload in sorted(payloads.items()):
        if uid in tombs:
            continue
        rtask = payload.get("task") or {}
        cur = local.get(uid)
        if cur is None:
            if not dry_run:
                fresh = {k: v for k, v in rtask.items() if k not in LOCAL_ONLY}
                fresh[FIELD_TS] = dict(payload.get(FIELD_TS) or {})
                fresh.setdefault("origin_owner", payload.get("owner") or "")
                created = ts._backend().create_with_seq(fresh)
                local[uid] = created
                _mark_dirty(ts, created, rep)
            rep["created"].append(rtask.get("title") or uid[:8])
            continue
        merged, sub = merge_task(cur, payload, now=now)
        if not sub["changed"]:
            continue
        merged[FIELD_TS] = _merge_field_ts(cur, payload)
        if not dry_run:
            saved = ts.save_task(merged) or merged
            local[uid] = merged
            _mark_dirty(ts, merged, rep)
        rep["merged"].append(_ref(cur))
        rep["fields"]["taken"] += len(sub["taken"])
        rep["fields"]["unioned"] += sum(sub["unioned"].values())
        rep["fields"]["preserved"] += len(sub["preserved"])
        rep["fields"]["filled"] += len(sub["filled"])
        for f, n in sub["clashes"].items():
            rep["clashes"][f] = rep["clashes"].get(f, 0) + n

    # -- relation edges: put THIS machine's seqs back on every imported edge
    if not dry_run:
        by_id = _local_index(ts)
        for t in by_id.values():
            if rederive_related_seqs(t, by_id):
                ts.save_task(t)

    # -- export: this station's own partition, and only ever this station's
    live = _local_index(ts) if not dry_run else local
    # THE SECOND SHARE GUARD, AND IT IS DELIBERATE. The run already returned above for
    # a share exchange; this catches any future path that reaches the export without
    # passing through that return. Do not "simplify" it away — a red probe that removed
    # only the first guard still came back GREEN, which is exactly what a second guard
    # is for and exactly why one of them alone is not enough to trust.
    if rep["kind"] == KIND_SHARE:
        _export_share(ts, root, live, tombs, now, dry_run, rep, confirm=confirm_share)
        return _finish(root, rep, dry_run)
    for uid, task in sorted(live.items()):
        if uid in tombs or not exports_here(task):
            continue
        try:
            path = own_payload_path(root, uid)
        except PartitionViolation as e:
            rep["violations"].append(str(e))
            continue
        prev = read_payload(path)
        payload = export_payload(task, prev=prev, now=now)
        if prev is not None and _canon(prev.get("task")) == _canon(payload.get("task")):
            continue
        if not dry_run:
            write_own_payload(root, task, prev=prev, now=now)
            if payload.get(FIELD_TS) != (task.get(FIELD_TS) or {}):
                task[FIELD_TS] = payload.get(FIELD_TS)
                ts.save_task(task)
        rep["exported"].append(_ref(task))

    return _finish(root, rep, dry_run)


def _finish(root, rep, dry_run):
    """Publish this station's rev, record what it pulled, commit, and push only when
    there is somewhere to push to. Shared by both kinds so the share path cannot drift
    into a different transport."""
    if not dry_run and not rep.get("blocked"):
        try:
            rep["rev"] = write_own_rev(root)
            mark_seen(root)
        except Exception:
            pass
    if rep["git"]["repo"] and not dry_run:
        # SCOPED TO THE EXCHANGE, NOT THE REPO. From a subdirectory, a bare
        # `add -A` stages the WHOLE repository (git >= 2.0), so an exchange inside a
        # brain repo would sweep the human's unstaged notes into a sync commit and
        # push them. The `-- .` pathspec confines staging to the exchange subtree.
        _git(root, "add", "-A", "--", ".")
        st = _git(root, "status", "--porcelain", "--", ".")
        if st.ok and st.out.strip():
            c = _git(root, "commit", "-q", "-m",
                     "%s: %s %s" % (rep.get("kind") or KIND_BACKUP,
                                    _station.owner(), _station.dirname()))
            rep["git"]["commit"] = "ok" if c.ok else c.out[:200]
        else:
            rep["git"]["commit"] = "nothing to commit"
        if rep["git"]["remote"]:
            p = _git(root, "push", "-q")
            rep["git"]["push"] = "ok" if p.ok else ("queued (offline/failed): %s"
                                                    % p.out[:200])
    return rep


def run_all(ts, backup=None, share=None, now=None, dry_run=False, no_net=False,
            confirm_share=False):
    """Sync every configured destination, BACKUP FIRST. Returns a list of reports.

    Backup first is deliberate: durability before visibility. If the process dies
    between the two, what survived is the copy that loses nothing."""
    try:
        dests = destinations(backup=backup, share=share)
    except DestinationMismatch as e:
        return [{"root": None, "error": str(e), "kind": None,
                 "git": {}, "partitions": []}]
    if not dests:
        return [{"root": None, "kind": KIND_BACKUP, "git": {}, "partitions": [],
                 "error": ("sync is OFF — no exchange directory configured. "
                           "`task-station sync --init <dir>` creates one, or set "
                           "`sync_dir` in config / %s in the environment." % SYNC_ENV)}]
    return [run_sync(ts, root=d["root"], now=now, dry_run=dry_run, no_net=no_net,
                     kind=d["kind"], confirm_share=confirm_share) for d in dests]


def _merge_field_ts(local, payload):
    """The merged per-field stamps: the later of the two for every field either side
    knows about, so the next comparison starts from the truth this merge established."""
    out = dict(local.get(FIELD_TS) or {})
    for k, v in (payload.get(FIELD_TS) or {}).items():
        try:
            if float(v) > float(out.get(k) or 0):
                out[k] = v
        except (TypeError, ValueError):
            continue
    return out


def _ref(task):
    seq = task.get("seq")
    return "#%s" % seq if seq is not None else (task.get("id") or "?")[:8]


def _mark_dirty(ts, task, rep):
    """A union is a RE-FRAGMENTATION event: the record now holds two machines' worth
    of decisions that nothing has checked for agreement. Sync's semantic duty is to
    FLAG that, never to reconcile it — so the task is marked dirty and heal comes
    due, and the report says so in its own row rather than reporting '0 conflicts'
    and letting that read as 'the record is fine'."""
    try:
        ts.mark_digest_dirty(task)
        ts.save_task(task)
    except Exception:
        pass
    rep["heal_due"].append(_ref(task))


# ------------------------------------------------------------------ report ----

def format_report(rep):
    """The THREE-ROW VERDICT. Mechanical says whether the transport itself was clean,
    Judgment says what the merge did to the record, and Heal-due says what a human
    (or /heal) still has to look at. Reporting only the first row is how '0 conflicts'
    comes to mean 'the record is fine' when it means no such thing."""
    if rep.get("error"):
        return "sync: %s" % rep["error"]
    out = []
    root = rep.get("root")
    parts = rep.get("partitions") or []
    mine = [p for p in parts if p]
    out.append("%s — %s" % ("Share" if rep.get("kind") == KIND_SHARE else "Sync", root))
    out.append("  %d partition(s): %s"
               % (len(mine), ", ".join("%s/%s" % (p["owner"], _station.dirname(p["number"]))
                                       for p in mine) or "none yet"))
    g = rep.get("git") or {}
    if not g.get("repo"):
        net = "plain directory (no git)"
    elif not g.get("remote"):
        net = "local git, NO remote — committed here, nothing sent"
    else:
        net = "git remote · pull %s · push %s" % (g.get("pull") or "-",
                                                  g.get("push") or "-")
    out.append("  transport: %s%s" % (net, "  [dry run]" if rep.get("dry_run") else ""))
    out.append("")

    viol = rep.get("violations") or []
    mech = ("REFUSED %d write(s) outside this station's partition" % len(viol)) if viol \
        else "clean — 0 conflicts possible (each station writes only its own partition)"
    f = rep.get("fields") or {}
    if rep.get("kind") == KIND_SHARE and rep.get("blocked"):
        # A blocked run wrote NOTHING. Saying "0 shared" and stopping there would read
        # as "there was nothing to share", which is the opposite of what happened.
        plan = rep.get("plan") or {}
        return "\n".join([
            format_share_plan(plan, rep.get("root")),
            "",
            "  Mechanical  nothing was written — this run is HELD, not failed",
            "  Judgment    %d task(s) would become visible, %d of them NEWLY "
            "(%d withheld)" % (len(plan.get("publish") or []),
                               len(plan.get("widening") or []),
                               plan.get("withheld", 0)),
            "  Heal-due    nothing to reconcile",
        ])
    if rep.get("kind") == KIND_SHARE:
        # The judgment a SHARE run makes is "who may see what", so that is what its
        # judgment row says. WITHHELD is stated as a number rather than left implicit:
        # "3 shared" alone never tells you whether the other forty were considered.
        judg = ("%d task(s) shared · %d WITHHELD (no sharing rule — private by "
                "default, and no file was written) · %d retracted"
                % (rep.get("shared", 0), rep.get("withheld", 0),
                   len(rep.get("retracted") or [])))
        healrow = ("nothing to reconcile — a share exchange is a one-way VIEW and is "
                   "never merged back")
    else:
        judg = ("%d task(s) merged · %d field(s) taken · %d unioned · %d value(s) preserved"
                % (len(rep.get("merged") or []), f.get("taken", 0), f.get("unioned", 0),
                   f.get("preserved", 0)))
        clash = rep.get("clashes") or {}
        if clash:
            judg += " · %d element flag clash(es) kept local" % sum(clash.values())
        heal = rep.get("heal_due") or []
        healrow = ("%d task(s) flagged — run `/heal` : a union is a re-fragmentation "
                   "event and MEANING is checked by nothing here" % len(heal)) if heal \
            else "nothing to reconcile"
    out.append("  Mechanical  %s" % mech)
    out.append("  Judgment    %s" % judg)
    out.append("  Heal-due    %s" % healrow)
    out.append("")
    if rep.get("kind") == KIND_SHARE:
        out.append("  out: %d written · %d retracted   (EXPORT ONLY — a share exchange "
                   "is never imported into the store)"
                   % (len(rep.get("exported") or []), len(rep.get("retracted") or [])))
    else:
        out.append("  in : %d created · %d merged · %d deleted"
                   % (len(rep.get("created") or []), len(rep.get("merged") or []),
                      len(rep.get("deleted") or [])))
        out.append("  out: %d exported · %d tombstoned"
                   % (len(rep.get("exported") or []), len(rep.get("tombstoned") or [])))
    for v in viol:
        out.append("  !! %s" % v)
    return "\n".join(out)
