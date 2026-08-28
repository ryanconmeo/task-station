"""Storage primitives, the Tasktrail emitters, links/counters and ref resolution."""
from board import _shared
from board._shared import *          # constants + utilities (explicit __all__)
import re
from datetime import datetime, timezone

import decisions as _dec
import steps as _steps
import store

g, set_g = _shared.g, _shared.set_g

__all__ = [
    "task_status", "is_closed",
    "_backend", "_ensure_dirs", "_iso", "_iso_to_ts", "_local_iso",
    "load_task", "RevConflict", "save_task", "mutate", "create_with_seq",
    "delete_task", "all_tasks", "search_tasks", "ensure_handles", "task_handles",
    "_stream_alloc_n", "_stream_emit", "_stream_digest",
    "_stream_updated_data", "_stream_created_data",
    "sorted_tasks", "_max_seq", "ensure_seqs",
    "get_link", "set_link", "clear_link", "live_session_count",
    "get_count", "bump_count", "clear_count",
    "mark_edited", "has_edited", "get_blocked", "bump_blocked",
    "clear_edit_markers",
    "resolve_ref", "_parse_ordinal_ref", "_resolve_prompt_task_refs",
]


def task_status(task):
    """A task's lifecycle status, defaulting a missing/unknown value to open —
    so tasks written before this field existed read as open (back-compat)."""
    s = (task or {}).get("status")
    return s if s in (STATUS_OPEN, STATUS_ACTIVE, STATUS_CLOSED) else STATUS_DEFAULT


def is_closed(task):
    """True iff the task is done (status == closed)."""
    return task_status(task) == STATUS_CLOSED


# ---------------------------------------------------------------- storage ----
#
# The read/write layer lives in store.py — a SQLite backend (`<store>/tasks.db`)
# when sqlite3 is available, the original file-per-task JSON store as a fallback.
# The functions below keep their historical names/signatures so call sites (and
# the tests) don't change; each just delegates to the active backend. STORE is a
# module global the tests repoint, so resolve the backend per call against it.


def _backend():
    return store.get_backend(g("STORE"))


def _ensure_dirs():
    _backend().ensure()


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def _iso_to_ts(s):
    """Epoch seconds for an ISO string written by _iso, or None if unparseable."""
    try:
        return datetime.fromisoformat(s).timestamp()
    except (TypeError, ValueError):
        return None


def _local_iso(s):
    """An ISO timestamp written by _iso (UTC), converted to the system local
    timezone for display. Naive strings are treated as UTC. Returns the input
    unchanged if it can't be parsed, so old/foreign values render as-is."""
    if not s:
        return s
    try:
        dt = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return s
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().isoformat(timespec="seconds")


def load_task(task_id):
    return _backend().load_task(task_id)


RevConflict = store.RevConflict


def save_task(task, expected_rev=None):
    task["updated_at"] = _iso(task["updated_ts"])
    _backend().save_task(task, expected_rev=expected_rev)


def mutate(task_id, mutator_fn, retries=20):
    """Optimistic read-modify-write: load `task_id`, apply `mutator_fn(task)` in
    place, save guarded by the loaded rev, and on a concurrent-writer conflict
    reload + re-run the mutator (up to `retries`). THE required path for any
    concurrent task mutation — two writers appending to the same feed both survive
    instead of one clobbering the other (see CONTRIBUTING).

    `mutator_fn` MUST be pure (transform the given dict only; no I/O, no save, no
    reads of mutable external state) since a conflict re-runs it on fresh state.
    Returns the saved task (None if it doesn't exist)."""
    def _wrapped(t):
        mutator_fn(t)
        if t.get("updated_ts") is not None:
            t["updated_at"] = _iso(t["updated_ts"])
    return _backend().mutate(task_id, _wrapped, retries=retries)


def create_with_seq(task):
    """Transactionally allocate a unique seq and persist `task` in one step —
    the ONLY correct way to mint a new task's number. Replaces the racy
    ensure_seqs()+_max_seq()+1 read-then-write: two concurrent creators can never
    land on the same seq (BEGIN IMMEDIATE + a UNIQUE(seq) index + retry). Returns
    the task with its assigned `seq`; callers may keep mutating and save_task()."""
    task["updated_at"] = _iso(task["updated_ts"])
    return _backend().create_with_seq(task)


def delete_task(task_id):
    """Remove a task from the store entirely. Used to GC an untouched provisional
    auto-task (Workstream D) when its session is skipped or closed."""
    _backend().delete_task(task_id)


def all_tasks():
    return _backend().all_tasks()


def search_tasks(query, limit=50):
    """Ranked cross-task search via the active backend (FTS5 index, or the LIKE
    fallback). Returns [{"id", "snippet", "score"}, …] best-first; the caller loads
    + filters + renders. Defensive — a store/search hiccup returns no hits rather
    than breaking the command."""
    try:
        return _backend().search(query, limit)
    except Exception:
        return []


# ---- Tasktrail event ledger (A-2) -------------------------------------------
# Durable, append-only JSONL record of every mutation (lib/stream.py). The in-blob
# event feed is a bounded rolling window (EVENTS_KEEP); the stream is the durable
# record + the substrate for the published contract. Wiring posture mirrors the
# obsidian hooks: a stream failure must NEVER block the mutation that triggered it.

def _stream_alloc_n(task, persist):
    """Allocate this task's next monotonic counter `n`. When `persist` (the normal
    path), bump the value ON THE TASK via the optimistic-rev lock so concurrent
    emitters stay gapless, and reflect it on the in-hand dict. When not persisting
    (a task.deleted tombstone — the row is gone), bump the in-hand dict only."""
    if persist and task.get("id"):
        box = {"n": None}
        def _bump(t):
            t["stream_n"] = int(t.get("stream_n", 0)) + 1
            box["n"] = t["stream_n"]
        if g("mutate")(task["id"], _bump) is not None:
            task["stream_n"] = box["n"]
            return box["n"]
    task["stream_n"] = int(task.get("stream_n", 0)) + 1
    return task["stream_n"]


def _stream_emit(kind, task, data, session=None, persist=True):
    """Emit one Tasktrail event for `task`. Best-effort + fully guarded: a stream
    problem (config, disk, lock) is swallowed so it can never block the mutation
    that triggered it. No-op when the stream is disabled."""
    try:
        import config
        if not config.stream_enabled():
            return None
        import stream
        return stream.emit(kind, task, data,
                           lambda: _stream_alloc_n(task, persist),
                           actor_session=session, owner=_owner())
    except Exception:
        return None


def _stream_digest(task):
    """The machine-readable digest snapshot carried by task.checkpoint / task.snapshot.
    Model-curated digest fields only (never prompt text). `glossary` and `brief_path`
    are first-class in the contract but don't exist on the task yet — carry them
    generically WHEN PRESENT so the contract is forward-compatible."""
    d = {
        "title": (task.get("title") or "")[:EVENT_TEXT_MAX],
        "status": task_status(task),
        "goal": task.get("goal") or "",
        "state": task.get("state") or "",
        # ACTIVE steps only — a superseded step must no more ride a resume snapshot than
        # a superseded decision does. The elements themselves are carried unchanged, so
        # a task with nothing superseded emits byte-identically to before.
        "steps": [s for _i, s in _steps.live(task.get("steps"))],
        "summary": task.get("summary") or "",
        # Still-CURRENT decisions only, as plain strings: a superseded decision must
        # never ride a resume snapshot (that is the bug), and the plain-string
        # projection keeps the checkpoint contract byte-identical for older readers.
        "decisions": _dec.live_texts(task.get("decisions")),
        "prs": task.get("prs") or [],
        "stories": task.get("stories") or [],
    }
    if task.get("closed_ts"):
        d["closed"] = task["closed_ts"]
    for k in ("glossary", "brief_path"):
        if task.get(k) is not None:
            d[k] = task.get(k)
    return d


def _stream_updated_data(task, changed):
    """Payload for task.updated: the changed field NAMES plus the new values of the
    scalar fields, each capped at EVENT_TEXT_MAX (privacy — never a full body)."""
    fields = {}
    if "title" in changed:
        fields["title"] = (task.get("title") or "")[:EVENT_TEXT_MAX]
    # `summary↺` is --restore-summary: a different verb, the same field changing, so the
    # feed must carry the new value for it too.
    if {"summary", "summary+", "summary↺"} & set(changed):
        fields["summary"] = (task.get("summary") or "")[:EVENT_TEXT_MAX]
    if "state" in changed:
        fields["state"] = (task.get("state") or "")[:EVENT_TEXT_MAX]
    if "goal" in changed:
        fields["goal"] = (task.get("goal") or "")[:EVENT_TEXT_MAX]
    if "effort" in changed:
        fields["effort"] = task.get("effort")
    if "color" in changed:
        fields["color"] = task.get("color")
    return {"changed": list(changed), "fields": fields}


def _stream_created_data(task):
    d = {"title": (task.get("title") or "")[:EVENT_TEXT_MAX],
         "status": task.get("status")}
    for k in ("color", "effort", "goal"):
        if task.get(k) is not None:
            d[k] = task.get(k)
    return d


def sorted_tasks():
    """Not-closed (open + active) before closed; within each, most recent
    activity first."""
    return sorted(
        all_tasks(),
        key=lambda t: (1 if is_closed(t) else 0, -t.get("updated_ts", 0)),
    )


def _max_seq(tasks=None):
    tasks = tasks if tasks is not None else all_tasks()
    return max((t.get("seq") or 0 for t in tasks), default=0)


def ensure_seqs():
    """Backfill stable per-task sequence numbers, assigned in creation order.

    Every task gets a permanent `seq` the first time it's seen — the number a
    user sees in `/todo` and types as `/todo <n>`. Unlike the old render-time
    index, a task keeps its number even as others are added, closed, or reorder
    by recent activity. Idempotent: tasks that already have a seq keep it.
    """
    tasks = all_tasks()
    missing = [t for t in tasks if not t.get("seq")]
    if not missing:
        return
    n = _max_seq(tasks)
    for t in sorted(missing, key=lambda t: t.get("created_ts", 0)):
        n += 1
        t["seq"] = n
        save_task(t)


def ensure_handles(tasks=None):
    """Backfill the WRITE-ONCE `<owner>-<uuid>` handle onto every task that predates
    it, exactly once. Mirrors `ensure_seqs` — idempotent, and cheap after the first
    pass because it only writes the tasks that are missing one.

    THE STAMP IS THIS MACHINE'S OWNER, and that is correct for a backfill: a task that
    existed before handles did was created here. A task that arrived over sync already
    carries its origin owner's handle and `handles.ensure` refuses to touch it —
    write-once means write-once even when the writer was somebody else."""
    try:
        import handles as _handles
        import station as _station
    except Exception:
        return 0
    owner = _station.owner()
    wrote = 0
    for t in (all_tasks() if tasks is None else tasks):
        if _handles.ensure(t, owner):
            save_task(t)
            wrote += 1
    return wrote


def task_handles(tasks=None):
    """Every handle known to this machine — the pool ambiguity is judged against."""
    scan = tasks if tasks is not None else all_tasks()
    return [t.get("handle") for t in scan if t.get("handle")]


# ------------------------------------------------------------------ links ----

def get_link(session):
    return _backend().get_link(session)


def set_link(session, task_id):
    _backend().set_link(session, task_id)


def clear_link(session):
    _backend().clear_link(session)


def live_session_count(task):
    """How many of this task's recorded sessions are STILL attached to it.

    `task["sessions"]` is append-only — it keeps every session that ever touched
    the task, even ones that later attached elsewhere, closed, or were skipped —
    so a raw `len()` over-reports. The live count is the sessions whose link
    currently resolves back to this task; that's the real concurrent-session
    signal /todo surfaces."""
    return _backend().live_session_count(task)


def get_count(session):
    """How many prompts this session has gone without attaching to a task."""
    return _backend().get_count(session)


def bump_count(session):
    return _backend().bump_count(session)


def clear_count(session):
    _backend().clear_count(session)


def mark_edited(session):
    """Record that this session edited a file. Returns True only on the FIRST
    call (so the PostToolUse reminder is one-shot, not per-edit)."""
    return _backend().mark_edited(session)


def has_edited(session):
    return _backend().has_edited(session)


def get_blocked(session):
    return _backend().get_blocked(session)


def bump_blocked(session):
    return _backend().bump_blocked(session)


def clear_edit_markers(session):
    _backend().clear_edit_markers(session)


def resolve_ref(ref):
    """Resolve a /todo argument to a task dict.

    An all-digit ref is matched against tasks' stable `seq` numbers (the numbers
    shown in the listing). A `<seq>-<ordinal>` ref (`4-0`) names one specific HUB
    SESSION of task 4 and resolves to that TASK here — the ordinal is consumed by
    the jump path (see `_parse_ordinal_ref`), not by task resolution. Anything else
    — or a digit / `<seq>-<n>` string matching no seq — is matched against task ids
    by exact match or prefix, so a longer all-digit id prefix that happens to
    contain no hex letters (e.g. "03471986") still resolves correctly.

    ORDER is what keeps the session grammar from shadowing an id. A task id is a
    uuid4 STRING, so it does contain hyphens — the first always at index 8. The only
    ref that could be read both ways is therefore an 8-digit all-numeric first block
    plus an all-digit partial second block ("03471986-1234"). The `<seq>-<ordinal>`
    branch claims such a ref ONLY when a task really carries that seq (seqs are
    small; a 7-8 digit one does not occur), and otherwise falls through to the
    id-prefix branch with its behaviour unchanged.
    """
    ref = (ref or "").strip()
    if not ref:
        return None
    ensure_seqs()
    listing = sorted_tasks()
    if ref.isdigit():
        i = int(ref)
        for t in listing:
            if t.get("seq") == i:
                return t
        # No task with that number: fall through and treat as an id prefix.
    m = _ORDINAL_REF_RE.match(ref)
    if m:
        i = int(m.group(1))
        for t in listing:
            if t.get("seq") == i:
                return t
        # No task with that seq: fall through and treat the whole ref as an id prefix.
    for t in listing:
        if t["id"] == ref or t["id"].startswith(ref):
            return t
    return _resolve_handle_ref(ref, listing)


def _resolve_handle_ref(ref, listing):
    """A `<owner>-<uuid prefix>` HANDLE ref → its task, or None.

    LAST in the order, deliberately. A handle contains a hyphen and so does a uuid,
    so trying it first would let `kosei-…` shadow a seq or an id prefix that someone
    actually meant. Reached only when nothing else matched, it can never change how an
    existing ref resolves.

    AMBIGUITY RESOLVES TO NOTHING, not to a guess. An abbreviated handle that names two
    tasks is the one case abbreviation exists to make VISIBLE; silently picking the
    first would hand the caller a different task from the one they meant, which is
    strictly worse than "no such task"."""
    try:
        import handles as _handles
    except Exception:
        return None
    # Backfill LAZILY and only from the listing already in hand: a handle ref is the
    # one moment the pool has to be complete, and paying a full-table write on every
    # `/todo 12` to keep it complete would be a cost with no reader.
    if any(not t.get("handle") for t in listing):
        ensure_handles(listing)
    hits = _handles.resolve(ref, [t.get("handle") for t in listing if t.get("handle")])
    if len(hits) != 1:
        return None
    return next((t for t in listing if t.get("handle") == hits[0]), None)


def _parse_ordinal_ref(ref):
    """`(task, ordinal)` when `ref` is a `<seq>-<ordinal>` session handle naming a
    REAL task, else None.

    The ordinal comes back as an int and **0 is a perfectly valid ordinal** (it is
    the session that created the task), so every caller must test `is None` — never
    truthiness.

    Requiring the task to EXIST is what makes this safe to consult before id-prefix
    resolution: a ref that merely looks like the grammar but names no such seq (say
    the uuid prefix "03471986-1234") returns None, and the caller falls through to
    ordinary id resolution with its behaviour unchanged."""
    m = _ORDINAL_REF_RE.match((ref or "").strip())
    if not m:
        return None
    ensure_seqs()
    seq = int(m.group(1))
    for t in sorted_tasks():
        if t.get("seq") == seq:
            return t, int(m.group(2))
    return None


def _resolve_prompt_task_refs(prompt, limit=3):
    """Detect task references in the user's prompt and resolve them to real
    tasks, so the agent is handed the seq->id->title binding instead of guessing.
    Returns a printable block (str), or "" when nothing resolves."""
    if not prompt:
        return ""
    refs = []
    # "task 387", "task #387", "todo 387", "seq 387", "#387"
    for m in re.finditer(r"(?i)(?:\b(?:task|todo|seq)\s+#?|#)(\d{1,7})\b", prompt):
        refs.append(m.group(1))
    # bracketed hex short-id "[6e756ed6]"
    for m in re.finditer(r"\[([0-9a-f]{6,40})\]", prompt):
        refs.append(m.group(1))
    seen, out = set(), []
    for r in refs:
        t = resolve_ref(r)
        if not t or t["id"] in seen:
            continue
        seen.add(t["id"])
        seq = t.get("seq") or "?"
        status = t.get("status") or "open"
        out.append('  - #%s [%s] "%s" - %s - digest: task-station search --detail %s'
                   % (seq, t["id"][:8], t.get("title") or "", status, seq))
        if len(out) >= limit:
            break
    if not out:
        return ""
    return "[task-station] Your message references an existing task - resolved:\n" + "\n".join(out)
