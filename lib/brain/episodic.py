"""brain-station — the ONE episodic access point (a task-station consumer).

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 3) from the brain source tree's
``scripts/episodic.py`` @ 0.14.0. Behaviour is unchanged; the imports are now
relative siblings and the ``sys.path`` self-bootstrap is gone.

This module is the *only* place in the brain plane that knows how to read
task-station's episodic layer. It is a strict ONE-WAY consumer over the
published **Tasktrail** contract: task-station is the producer and stays
consumer-agnostic — ALL the glue lives here, none of it there. In this monorepo
that separation is also a LAYER rule: the brain reaches board DATA through
config-resolved paths only (``episodic_stream``, ``vault``) and never imports a
board module.

The contract consumed (task-station 1.84.0):
  * an append-only JSONL event ledger at ``<stream>/events/YYYY-MM.jsonl``
  * a manifest ``<stream>/tasktrail.json`` (older producers: ``taskstream.json``)
    holding ``{spec_version, producer, generation}``
  * envelope ``{v, ts, n, event, task:{uuid,seq}, actor:{session}, data}``
  * event kinds ``task.created/updated/status/checkpoint/event/relation/
    deleted/redacted/snapshot``; a checkpoint's ``data`` is the digest, which
    may carry ``glossary[]`` (``{name,layer,state,def}``) and ``brief_path``.
  * exported markdown mirror notes carry flat YAML frontmatter:
    ``managed-by/seq/status/updated/title/schema-version/uuid/closed`` (+ optional
    ``glossary``/``brief_path``).

Consumer obligations honoured here: tolerate unknown event kinds; warn ONCE (to
error.log) on an unknown MAJOR schema-version; honor tombstones
(deleted/redacted); full re-sync on a manifest ``generation`` bump.

Backend preference (first that is present wins):
  1. the Tasktrail event stream (fold per-task state from the newest shards)
  2. the exported markdown mirror (vault ``task-station/`` dir)
  3. neither -> episodic layer unavailable (``recent_tasks`` returns ``None``)

Layer rule: brain may import core and its own siblings, never board. Stdlib +
``brain.config`` / ``brain.errorlog`` / ``brain.notes`` only.

Pure stdlib, Python 3.9+.
"""
import datetime
import json
import re
from pathlib import Path

from . import config
from . import errorlog
from . import notes

# We understand Tasktrail MAJOR version 1. A stream/envelope claiming a higher
# major is read best-effort with a single warning (never a crash).
SUPPORTED_MAJOR = 1

# Manifest filename varies by producer version — prefer the newer name.
MANIFEST_NAMES = ("tasktrail.json", "taskstream.json")

# Bound the fold: read the newest few shards, fold the last N events of them.
NEWEST_SHARDS = 3
TAIL_EVENTS = 5000

_STATE_NAME = "episodic-cursor.json"

# Statuses that mean a task is finished (for the CLOSED filter ingest uses).
CLOSED_STATUSES = {"closed", "done", "complete", "completed", "archived", "cancelled"}

# Fields we fold out of event ``data`` (and read from mirror frontmatter).
_FIELD_KEYS = ("title", "status", "summary", "closed", "glossary", "brief_path")

_WARNED = set()  # warn-once dedup for schema-version surprises (per process)


# --------------------------------------------------------------------------- #
# config resolution
# --------------------------------------------------------------------------- #
def _cfg(cfg):
    return cfg if cfg is not None else config.load()


def stream_dir(cfg=None):
    """The configured Tasktrail stream dir (or ``None`` if unresolvable)."""
    return _cfg(cfg).get("episodic_stream")


def mirror_dirs(cfg=None):
    """Candidate exported-markdown mirror dirs. The vault ``task-station/``
    mirror is the one the consumer always knows about; centralised here so no
    other module hardcodes the ``task-station/`` path."""
    cfg = _cfg(cfg)
    return [cfg["vault"] / "task-station"]


def episodic_roots(cfg=None):
    """Existing mirror dirs to fold into markdown search (the search CLI's
    ``--episodic``). Empty when no mirror is present."""
    return [d for d in mirror_dirs(cfg) if d.exists()]


# --------------------------------------------------------------------------- #
# timestamps + versions
# --------------------------------------------------------------------------- #
def _parse_ts(ts):
    """Lenient timestamp parse: epoch seconds, ISO-8601 (``Z`` ok), or date."""
    if ts is None:
        return None
    try:
        return datetime.datetime.fromtimestamp(float(ts))
    except (TypeError, ValueError):
        pass
    try:
        return datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _ts_key(ts):
    return _parse_ts(ts) or datetime.datetime.min


def _major(version):
    if version is None:
        return None
    try:
        return int(str(version).split(".")[0])
    except (ValueError, IndexError):
        return None


def _warn_once(msg):
    if msg in _WARNED:
        return
    _WARNED.add(msg)
    errorlog.record("episodic:schema", msg)


# --------------------------------------------------------------------------- #
# state file (cursor + generation) — drives full re-sync on a generation bump
# --------------------------------------------------------------------------- #
def _state_file():
    return config.state_dir() / _STATE_NAME


def _load_state():
    try:
        return json.loads(_state_file().read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(generation, cursor):
    try:
        _state_file().write_text(json.dumps({"generation": generation, "cursor": cursor}))
    except OSError as e:
        errorlog.record("episodic:state", e)


# --------------------------------------------------------------------------- #
# stream backend
# --------------------------------------------------------------------------- #
def _read_manifest(stream):
    for name in MANIFEST_NAMES:
        p = Path(stream) / name
        if p.exists():
            try:
                return json.loads(p.read_text())
            except (OSError, json.JSONDecodeError) as e:
                errorlog.record("episodic:manifest", e)
                return None
    return None


def _shard_files(stream):
    ev = Path(stream) / "events"
    if not ev.exists():
        return []
    # YYYY-MM.jsonl sorts lexically == chronologically.
    return sorted(ev.glob("*.jsonl"))


def _tail_events(shards):
    """Load events from the newest shards, chronologically ordered, capped at
    the last ``TAIL_EVENTS``. Malformed lines are logged and skipped."""
    picked = shards[-NEWEST_SHARDS:] if NEWEST_SHARDS else shards
    evs = []
    for shard in picked:
        try:
            text = shard.read_text(errors="ignore")
        except OSError as e:
            errorlog.record("episodic:shard", e)
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                evs.append(json.loads(line))
            except json.JSONDecodeError as e:
                errorlog.record("episodic:event", e)
    evs.sort(key=lambda e: (_ts_key(e.get("ts")), e.get("n") or 0))
    return evs[-TAIL_EVENTS:]


def _new_rec(uuid, seq):
    return {"uuid": uuid, "seq": seq, "status": None, "title": None,
            "summary": None, "closed": None, "glossary": [], "brief_path": None,
            "updated": None, "deleted": False}


def _apply_data(rec, data):
    """Merge known fields from an event's ``data`` digest into the folded record.
    Only keys actually present are touched, so a partial ``updated`` delta never
    clobbers fields it did not mention."""
    if not isinstance(data, dict):
        return
    for k in _FIELD_KEYS:
        if k in data and data[k] is not None:
            rec[k] = data[k]
    # a status delta may carry the new value under 'to' instead of 'status'
    if "status" not in data and data.get("to") is not None:
        rec["status"] = data["to"]


def _fold_one(tasks, ev):
    if not isinstance(ev, dict):
        return
    v = ev.get("v")
    if isinstance(v, int) and v > SUPPORTED_MAJOR:
        _warn_once(f"unknown Tasktrail envelope major v={v} (consumer supports {SUPPORTED_MAJOR}); "
                   "reading best-effort")
    task = ev.get("task") or {}
    uuid, seq = task.get("uuid"), task.get("seq")
    if uuid is None and seq is None:
        return
    key = uuid or f"seq:{seq}"
    rec = tasks.get(key)
    if rec is None:
        rec = _new_rec(uuid, seq)
        tasks[key] = rec
    if uuid is not None:
        rec["uuid"] = uuid
    if seq is not None:
        rec["seq"] = seq

    kind = ev.get("event") or ""
    short = kind.split(".", 1)[1] if "." in kind else kind
    if short in ("deleted", "redacted"):
        rec["deleted"] = True  # tombstone — honored (dropped from output)
    elif short in ("created", "snapshot", "updated", "checkpoint", "status", "event", "relation"):
        _apply_data(rec, ev.get("data") or {})
    # any other (unknown) kind is tolerated: we still advance the timestamp below.
    ts = ev.get("ts")
    if ts is not None:
        rec["updated"] = ts


def _stream_tasks(cfg):
    """Fold per-task state from the Tasktrail stream. Returns a ``{key: rec}``
    dict (tombstoned tasks dropped), or ``None`` when there is no stream backend."""
    stream = stream_dir(cfg)
    if not stream or not Path(stream).exists():
        return None
    shards = _shard_files(stream)
    manifest = _read_manifest(stream)
    if manifest is None and not shards:
        return None  # dir exists but holds no usable ledger

    if isinstance(manifest, dict):
        mmaj = _major(manifest.get("spec_version"))
        if mmaj is not None and mmaj > SUPPORTED_MAJOR:
            _warn_once(f"unknown Tasktrail spec_version {manifest.get('spec_version')!r} "
                       f"(consumer supports {SUPPORTED_MAJOR}.x); reading best-effort")

    generation = manifest.get("generation") if isinstance(manifest, dict) else None
    prev = _load_state()
    if prev.get("generation") is not None and prev.get("generation") != generation:
        # A generation bump means the producer rebuilt the ledger: discard the
        # stale cursor and re-fold from scratch (full re-sync). Our fold is a
        # fresh bounded tail every call, so the re-read *is* the re-sync; we log
        # it and reset the persisted cursor.
        errorlog.record("episodic:resync",
                        f"generation {prev.get('generation')} -> {generation}: full re-sync")

    events = _tail_events(shards)
    tasks = {}
    cursor = None
    for ev in events:
        _fold_one(tasks, ev)
        n = ev.get("n")
        if isinstance(n, int):
            cursor = n if cursor is None else max(cursor, n)
    _save_state(generation, cursor)
    return {k: r for k, r in tasks.items() if not r.get("deleted")}


# --------------------------------------------------------------------------- #
# mirror backend (exported markdown, frontmatter contract)
# --------------------------------------------------------------------------- #
def _parse_glossary_section(body):
    """Best-effort glossary from a ``## Glossary`` markdown section. Recognises a
    definition list (``term`` line followed by ``: definition``) and bullet form
    (``- **term** — def``). Returns ``[{name, def}]`` (possibly empty)."""
    m = re.search(r"(?ms)^##[ \t]+glossary[ \t]*$\n(.*?)(?=^##[ \t]|\Z)", body, re.I)
    if not m:
        return []
    out, pending = [], None
    for raw in m.group(1).splitlines():
        line = raw.strip()
        if not line:
            pending = None
            continue
        b = re.match(r"^-\s+\*\*(.+?)\*\*\s*[—:-]\s*(.*)$", line)
        if b:
            out.append({"name": b.group(1).strip(), "def": b.group(2).strip()})
            pending = None
        elif line.startswith(":") and pending is not None:
            out.append({"name": pending, "def": line[1:].strip()})
            pending = None
        else:
            pending = line
    return out


def _parse_mirror_note(path):
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return None
    fm, body = notes.parse_note(text)
    # A task export is identified by managed-by (task-station) or a seq field.
    if "seq" not in fm and "managed-by" not in fm:
        return None
    rec = _new_rec(fm.get("uuid"), fm.get("seq"))
    for k in ("status", "title", "closed", "brief_path", "updated"):
        if fm.get(k):
            rec[k] = fm[k]
    if fm.get("summary"):
        rec["summary"] = fm["summary"]
    rec["glossary"] = _parse_glossary_section(body)
    return rec


def _mirror_tasks(cfg):
    """Read tasks from the exported markdown mirror. Returns ``{key: rec}`` (a
    present-but-empty mirror dir yields ``{}``), or ``None`` when no mirror dir
    exists at all."""
    roots = [d for d in mirror_dirs(cfg) if d.exists()]
    if not roots:
        return None
    tasks = {}
    for root in roots:
        for f in sorted(Path(root).rglob("*.md")):
            rec = _parse_mirror_note(f)
            if rec is None:
                continue
            key = rec["uuid"] or f"seq:{rec['seq']}"
            tasks[key] = rec
    return tasks


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def _key(rec):
    """Stable merge key: uuid when known, else the (stringified) seq."""
    return rec.get("uuid") or f"seq:{rec.get('seq')}"


def _tasks(cfg):
    """Fold tasks from BOTH backends per the Tasktrail bootstrap contract:
    notes (the mirror) provide full state; stream events are the deltas folded
    on top (non-empty stream fields win — the stream is the newer truth, but a
    thin event history must not erase state the notes carry, e.g. titles).
    Returns ``None`` if neither backend is present."""
    stream = _stream_tasks(cfg)
    mirror = _mirror_tasks(cfg)
    if stream is None and mirror is None:
        return None
    if stream is None:
        return mirror
    if not mirror:
        return stream
    merged = {}
    for rec in mirror.values():
        merged[_key(rec)] = dict(rec)
    for rec in stream.values():
        k = _key(rec)
        base = merged.get(k)
        if base is None:
            # match a mirror rec by seq when the mirror predates uuids
            sk = f"seq:{rec.get('seq')}"
            base = merged.pop(sk, None)
        if base is None:
            merged[k] = dict(rec)
            continue
        for field, val in rec.items():
            if val not in (None, "", [], {}):
                base[field] = val
        merged[k] = base
    # stream tombstones already dropped their recs; drop mirror leftovers too
    return {k: r for k, r in merged.items() if not r.get("deleted")}


def is_closed(rec):
    """True when a folded record represents a finished task."""
    status = (rec.get("status") or "").strip().lower()
    return status in CLOSED_STATUSES or bool(rec.get("closed"))


def recent_tasks(days=14, cfg=None):
    """Tasks updated within the last ``days``, newest first. Returns ``None``
    when the episodic layer is unavailable (neither backend present) — callers
    treat that as "fine, task-station just isn't wired up"."""
    cfg = _cfg(cfg)
    tasks = _tasks(cfg)
    if tasks is None:
        return None
    cutoff = datetime.datetime.now() - datetime.timedelta(days=days)

    def when(r):
        return _parse_ts(r.get("updated")) or datetime.datetime.min

    recs = [r for r in tasks.values() if when(r) >= cutoff]
    recs.sort(key=when, reverse=True)
    return recs


def task_detail(seq_or_uuid, cfg=None):
    """A single folded task by seq or uuid, or ``None`` (unavailable / not found)."""
    cfg = _cfg(cfg)
    tasks = _tasks(cfg)
    if not tasks:
        return None
    needle = str(seq_or_uuid)
    for r in tasks.values():
        if r.get("uuid") == seq_or_uuid or r.get("uuid") == needle or str(r.get("seq")) == needle:
            return r
    return None
