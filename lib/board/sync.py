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
import json
import os
import re
import subprocess
import time

import decisions as _dec
import station as _station
from core.fsutil import atomic_write as _atomic_write

OWNERS_DIR = "owners"
TASKS_DIR = "tasks"
TASK_EXT = ".json"
TOMBSTONE_EXT = ".tombstone"
SYNC_ENV = "TASK_STATION_SYNC_DIR"

PAYLOAD_SCHEMA = 1
FIELD_TS = "field_ts"


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


README = """# task-station sync repo

One JSON file per task, under `owners/<owner>/station-<n>/tasks/<uuid>.json`.

**Each station writes ONLY its own `station-<n>` directory** and reads every other
one. Two writers therefore never touch the same path, so this repo cannot produce a
merge conflict — that is the design, not a coincidence, and editing another owner's
subtree by hand is the one way to break it.

`<uuid>.tombstone` marks a deleted task and is kept forever; removing one resurrects
the task from any peer that still holds it.
"""


def init_root(root):
    """Bring the exchange directory into existence: the layout, this station's
    partition, a README, and a LOCAL git repo when git is available. Creates no
    remote and contacts no network — adding a remote is a deliberate human step."""
    os.makedirs(owners_dir(root), exist_ok=True)
    ensure_own_partition(root)
    readme = os.path.join(root, "README.md")
    if not os.path.exists(readme):
        _atomic_write(readme, README)
    created_repo = False
    if not os.path.isdir(os.path.join(root, ".git")) and _git_available():
        created_repo = _git(root, "init", "-q").ok
    return {"root": root, "partition": own_partition_dir(root),
            "git": os.path.isdir(os.path.join(root, ".git")),
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
    return os.path.isdir(os.path.join(root, ".git"))


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


def run_sync(ts, root=None, now=None, dry_run=False, no_net=False):
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
           "clashes": {}, "heal_due": [], "partitions": []}
    if not root:
        rep["error"] = ("sync is OFF — no exchange directory configured. "
                        "`task-station sync --init <dir>` creates one, or set "
                        "`sync_dir` in config / %s in the environment." % SYNC_ENV)
        return rep
    ensure_own_partition(root)
    rep["git"]["repo"] = is_git_repo(root)
    rep["git"]["remote"] = has_remote(root) and not no_net

    if rep["git"]["remote"] and not dry_run:
        r = _git(root, "pull", "--rebase", "--autostash", "-q")
        rep["git"]["pull"] = "ok" if r.ok else ("offline/failed: %s" % r.out[:200])

    local = _local_index(ts)

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
    rep["partitions"] = [{"owner": p["owner"], "number": p["number"],
                          "label": p["label"]} for p in list_partitions(root)]

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

    # -- commit; push only when there is somewhere to push to
    if rep["git"]["repo"] and not dry_run:
        _git(root, "add", "-A")
        st = _git(root, "status", "--porcelain")
        if st.ok and st.out.strip():
            c = _git(root, "commit", "-q", "-m",
                     "sync: %s %s" % (_station.owner(), _station.dirname()))
            rep["git"]["commit"] = "ok" if c.ok else c.out[:200]
        else:
            rep["git"]["commit"] = "nothing to commit"
        if rep["git"]["remote"]:
            p = _git(root, "push", "-q")
            rep["git"]["push"] = "ok" if p.ok else ("queued (offline/failed): %s"
                                                    % p.out[:200])
    return rep


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
    out.append("Sync — %s" % root)
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
    out.append("  in : %d created · %d merged · %d deleted"
               % (len(rep.get("created") or []), len(rep.get("merged") or []),
                  len(rep.get("deleted") or [])))
    out.append("  out: %d exported · %d tombstoned"
               % (len(rep.get("exported") or []), len(rep.get("tombstoned") or [])))
    for v in viol:
        out.append("  !! %s" % v)
    return "\n".join(out)
