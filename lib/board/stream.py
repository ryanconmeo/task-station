"""Tasktrail — a durable, append-only JSONL event ledger (A-2).

The bounded in-blob event feed (EVENTS_KEEP=100) is a rolling window for the
delta-brief; it is NOT a durable record and NOT a publishable contract. Tasktrail
is: one JSON line per mutation, appended under O_APPEND to a monthly shard, with a
manifest describing the stream. It is the durability fix for the 100-event cap and
the substrate for the future published contract.

Design posture:
  * INTERNAL + default-ON, but strictly LOCAL — every write lands under
    <data_dir>/stream/ (zero egress). An external tee (config `stream_dir`) is the
    ONLY write outside data_dir and is default-OFF.
  * emit() is best-effort on the WRITE path: any failure returns None and NEVER
    propagates, so a stream problem can never block a task mutation (matches the
    hook-safety posture of _obsidian_sync). The READ paths (read_events/verify/
    stub_task) do NOT swallow — a corrupt ledger must surface on the stream CLI.
  * The per-task monotonic counter `n` is owned + persisted by the engine (on the
    task dict, concurrency-safe via the optimistic rev lock); emit() takes an
    `alloc_n` callback and allocates the next n INSIDE the shard lock so file order
    matches n order even across processes.

Privacy: prompt text is NEVER written to the stream. Free text on non-digest events
is capped by the caller (EVENT_TEXT_MAX); the checkpoint/snapshot digest is
model-curated and carried whole.
"""
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone

import paths
import config

try:
    import fcntl
except Exception:                       # pragma: no cover - POSIX only in practice
    fcntl = None

SPEC_VERSION = "1.0"


# --------------------------------------------------------------- locations ----

def _base(base=None):
    """The stream root. Defaults to <data_dir>/stream; an explicit `base` (used by
    the tee and by tests) overrides it."""
    return base if base is not None else os.path.join(paths.data_dir(), "stream")


def events_dir(base=None):
    return os.path.join(_base(base), "events")


def manifest_path(base=None):
    return os.path.join(_base(base), "tasktrail.json")


def _shard_path(base, ts):
    """Monthly shard for a UTC-ISO timestamp: <events>/YYYY-MM.jsonl."""
    return os.path.join(events_dir(base), ts[:7] + ".jsonl")


def _iso_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _producer():
    """`task-station/<version>` read from the plugin manifest, or task-station/0
    when it can't be resolved (mirrors the engine footer / MCP serverInfo)."""
    # lib/board/ is one level deeper than lib/
    root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
    try:
        with open(os.path.join(root, ".claude-plugin", "plugin.json"),
                  encoding="utf-8") as f:
            ver = json.load(f).get("version") or "0"
    except Exception:
        ver = "0"
    return "task-station/%s" % ver


# --------------------------------------------------------------- manifest -----

def _load_manifest(base=None):
    try:
        with open(manifest_path(base), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_manifest(base, manifest):
    root = _base(base)
    os.makedirs(root, exist_ok=True)
    tmp = manifest_path(base) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")
    os.replace(tmp, manifest_path(base))


def _ensure_manifest(base, owner=None):
    """Create the manifest once (generation 1). Never overwrites the generation (that
    is owned by redact via bump_generation). When an `owner` handle is set it is
    recorded on creation and kept current on an existing manifest (patched only when
    it actually differs, so an unset owner leaves the manifest BYTE-IDENTICAL)."""
    if os.path.exists(manifest_path(base)):
        if owner:
            m = _load_manifest(base)
            if m.get("owner") != owner:
                m["owner"] = owner
                _write_manifest(base, m)
        return
    m = {
        "spec_version": SPEC_VERSION,
        "producer": _producer(),
        "generation": 1,
    }
    if owner:
        m["owner"] = owner
    _write_manifest(base, m)


def bump_generation(base=None):
    """Increment the manifest generation (redact bumps it after rewriting shards).
    Returns the new generation. A READ/maintenance path — errors surface."""
    m = _load_manifest(base) or {
        "spec_version": SPEC_VERSION, "producer": _producer(), "generation": 1}
    m["generation"] = int(m.get("generation", 1)) + 1
    _write_manifest(base, m)
    return m["generation"]


# ------------------------------------------------------------------ lock ------

@contextmanager
def _lock(base):
    """Serialize the allocate-n + append critical section across processes so shard
    file order matches n order. A best-effort advisory flock; degrades to a no-op
    where fcntl is unavailable (O_APPEND still prevents line interleaving)."""
    root = _base(base)
    os.makedirs(root, exist_ok=True)
    if fcntl is None:
        yield
        return
    fd = os.open(os.path.join(root, ".lock"), os.O_WRONLY | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _append(base, ts, line_bytes):
    """One atomic O_APPEND write of a full line to the month shard."""
    os.makedirs(events_dir(base), exist_ok=True)
    fd = os.open(_shard_path(base, ts),
                 os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line_bytes)
    finally:
        os.close(fd)


# ------------------------------------------------------------------ emit ------

def _line(env):
    return (json.dumps(env, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")


def emit(kind, task, data, alloc_n, actor_session=None, owner=None):
    """Append one event to the ledger. Returns the envelope dict, or None when the
    stream is off or on ANY failure (best-effort — never raises into a mutation).

    `alloc_n` is a zero-arg callable returning the next per-task counter value; it
    is invoked INSIDE the shard lock so allocation and the append are serialized.
    `owner` (default None) is a shared-vault owner handle: when set it is carried on
    the event actor AND the manifest so a shared stream attributes each event; unset
    ⇒ the envelope is BYTE-IDENTICAL to today (no `owner` key)."""
    if not config.stream_enabled():
        return None
    try:
        base = None
        _ensure_manifest(_base(base), owner=owner)
        tee = config.stream_dir()
        with _lock(_base(base)):
            n = alloc_n()
            ts = _iso_now()
            actor = {"session": actor_session}
            if owner:
                actor["owner"] = owner
            env = {
                "v": 1,
                "ts": ts,
                "n": n,
                "event": kind,
                "task": {"uuid": task.get("uuid") or task.get("id"),
                         "seq": task.get("seq")},
                "actor": actor,
                "data": data or {},
            }
            line = _line(env)
            _append(_base(base), ts, line)
            if tee:
                _ensure_manifest(tee, owner=owner)
                _append(tee, ts, line)
        return env
    except Exception:
        return None


# --------------------------------------------------------------- read paths ---
# These NEVER swallow: a corrupt/unreadable ledger must surface on the stream CLI.

def _shard_names(base=None):
    ed = events_dir(base)
    if not os.path.isdir(ed):
        return []
    return sorted(n for n in os.listdir(ed) if n.endswith(".jsonl"))


def read_events(base=None):
    """Yield every envelope across shards in chronological shard order, then file
    (append) order within a shard. Raises on a corrupt line — callers on the CLI
    surface it rather than hide a broken ledger."""
    ed = events_dir(base)
    for name in _shard_names(base):
        with open(os.path.join(ed, name), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                yield json.loads(line)


def verify(base=None):
    """Check per-task `n` continuity (gapless 1..N, no dupes) and shard order (n
    non-decreasing per task in file order). Returns {ok, issues, tasks, events}."""
    per_task = {}
    total = 0
    for env in read_events(base):
        total += 1
        u = (env.get("task") or {}).get("uuid")
        per_task.setdefault(u, []).append(env.get("n"))
    issues = []
    for u, ns in sorted(per_task.items(), key=lambda kv: str(kv[0])):
        if ns != sorted(ns):
            issues.append("task %s: events out of order in shards: %s" % (u, ns))
        s = sorted(ns)
        if s != list(range(1, len(s) + 1)):
            issues.append("task %s: n not gapless (expected 1..%d): %s"
                          % (u, len(s), ns))
    return {"ok": not issues, "issues": issues,
            "tasks": len(per_task), "events": total}


def stub_task(target_uuid, base=None):
    """Redact one task: rewrite every shard, replacing that task's event payloads
    with the stub {"redacted": true} while preserving each envelope (v/ts/n/event/
    task/actor). Idempotent — already-stubbed lines are left untouched and not
    recounted. Returns the number of payloads newly stubbed. Never touches the
    task.redacted marker rows. A maintenance path — errors surface."""
    ed = events_dir(base)
    stub = {"redacted": True}
    stubbed = 0
    for name in _shard_names(base):
        p = os.path.join(ed, name)
        out = []
        changed = False
        with open(p, encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                env = json.loads(s)
                same = (env.get("task") or {}).get("uuid") == target_uuid
                if same and env.get("event") != "task.redacted" and env.get("data") != stub:
                    env["data"] = dict(stub)
                    changed = True
                    stubbed += 1
                out.append(json.dumps(env, ensure_ascii=False, sort_keys=True))
        if changed:
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write("\n".join(out) + "\n")
            os.replace(tmp, p)
    return stubbed
