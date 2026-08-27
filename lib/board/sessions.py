"""Session roster/transcripts, bg-aware resume, WS2/WS3, the orphan sweep + SessionEnd."""
from board import _shared
from board._shared import *          # constants + utilities (explicit __all__)
from board.state import *
from board.model import *
from board.memos import *
import atexit
import json
import os
import re
import sys
import uuid
from datetime import datetime

g, set_g = _shared.g, _shared.set_g

__all__ = [
    "add_ledger",
    "delta_brief", "memo_pending_brief",
    "_prompt_is_human",
    "_next_hub_ordinal", "ensure_ordinals", "hub_ordinal", "ordinal_label",
    "session_display", "session_title_label", "register_worker_session",
    "touch", "set_status", "close_task_inplace", "promote_active",
    "_project_dir_for",
    "_MSGCOUNT_MEM", "_REPLIES_MEM", "_SESSION_PATH_MEM", "_MSGCOUNT_ATEXIT",
    "_stat_key", "_mem_put", "_msgcount_cache_path", "_msgcount_disk",
    "_msgcount_flush", "_msgcount_persist_later", "_session_msgcount",
    "_session_msgcount_uncached", "_find_session_path",
    "estimate_session_tokens", "_extract_usage", "measure_context_tokens",
    "session_model", "_settings_model", "claude_code_model_selection",
    "_model_family_token", "_same_model_family", "_read_statusline_stdin",
    "persist_harness_context_window", "harness_context_window",
    "effective_context_window", "_session_cwd", "_load_delegate_registry",
    "_live_bg_index", "_is_live_bg", "bg_aware_resume", "bg_resume_hint",
    "worker_targets", "worker_lines", "_is_resumable", "_fresh_session_cwd",
    "fresh_resume_command", "_resume_target", "resume_command", "session_tree",
    "_live_session_index", "_live_note",
    "_mirror_child_close", "_delegate_module", "_reap_task_workers",
    "_live_session_ids", "_orphaned_workers", "sweep_orphan_workers",
    "_record_orphan_reap", "cmd_sweep_orphans",
    "_BoundedAgentsAdapter", "_end_reason", "_mark_session_ended",
    "_own_workers", "reap_own_workers", "cmd_session_end",
    "_session_task",
    "_prompt_ts", "_prompt_session_tag", "_assistant_text",
    "_last_bullet_reply", "_prompt_replies_all", "_prompt_replies",
    "_human_prompts_with_replies",
]


def add_ledger(task, action, worker_sid=None, actor_sid=None, detail=None):
    """Append one hub<->worker interaction to the task's provenance ledger:
    {ts, action, worker, actor, actor_ordinal, detail}. UNBOUNDED append-only
    (task #463 design decision: complete provenance is the point — NO trim; if
    blob growth ever bites, revisit with a separate ledger store, not a silent
    cap). `actor_ordinal` is resolved NOW (ordinals are stable/never reused, so
    freezing it at write time is safe and keeps reads cheap). Does NOT save — the
    caller persists."""
    e = {"ts": _now(), "action": action, "worker": worker_sid,
         "actor": actor_sid,
         "actor_ordinal": hub_ordinal(task, actor_sid) if actor_sid else None,
         "detail": (detail or "")[:EVENT_TEXT_MAX] or None}
    task.setdefault("ledger", []).append(e)
    return e


# -- delta-injection: per-session "while you were away" brief ------------------
# The events feed (task["events"], authored by the sibling core-model workstream)
# is the single delta source; each session_meta entry carries a `seen_ts`
# high-water mark. `delta_brief` reports OTHER sessions' events newer than that
# mark; `mark_seen` advances it. Both degrade to no-op on a task with no events
# feed (the field is simply absent), so bare/legacy tasks inject nothing.

def delta_brief(task, session, max_items=DELTA_MAX_ITEMS, max_chars=DELTA_MAX_CHARS):
    """One bounded "while you were away" block, or None when there's nothing to
    say. Selects `events` newer than this session's `seen_ts` (default: the
    session's `session_meta` attach `ts`, else 0) and NOT authored by this
    session (a missing/None `sid` counts as "other"). Rendered newest-last,
    capped to `max_items` newest events and `max_chars` total. Absent/empty
    events → None, so bare tasks stay silent."""
    events = task.get("events") or []
    if not events:
        return None
    meta = (task.get("session_meta") or {}).get(session) or {}
    seen = meta.get("seen_ts")
    if seen is None:
        seen = meta.get("ts") or 0
    # `memo` arrivals are owned by memo_pending_brief (ack-gated, re-surfaces until
    # acked); excluding them here avoids a double-announce. `memo-ack` still flows,
    # so a session sees when a peer acks.
    fresh = [ev for ev in events
             if (ev.get("ts") or 0) > seen and ev.get("sid") != session
             and ev.get("kind") != "memo"]
    if not fresh:
        return None
    fresh.sort(key=lambda e: e.get("ts") or 0)      # oldest → newest (newest-last)
    total = len(fresh)
    seq = task.get("seq", task["id"][:8])
    footer = "(full trail: /todo %s history)" % seq

    def _line(ev):
        text = (ev.get("text") or "").strip()
        who = session_display(task, ev.get("sid"))          # hub '<seq>-<n>' / worker slug (#463)
        who_tag = ("[%s] " % who) if who and who != "?" else ""
        return "  • %s (%s) %s%s" % (rel_time(ev.get("ts")), ev.get("kind", "?"),
                                     who_tag, text)

    shown = fresh[-max_items:]
    while True:
        head = ("[task-station] While you were away — %d update(s) to #%s [%s] "
                "by other sessions:" % (total, seq, task["id"][:8]))
        body = [head] + [_line(ev) for ev in shown]
        earlier = total - len(shown)
        if earlier > 0:
            body.append("  (+%d earlier)" % earlier)
        body.append(footer)
        text = "\n".join(body)
        if len(text) <= max_chars or len(shown) <= 1:
            return text
        shown = shown[1:]     # over budget: drop the OLDEST shown line, keep newest


def memo_pending_brief(task, session):
    """One bounded "memo(s) awaiting YOUR ack" block for `session`, or None when none
    are pending. UNLIKE delta_brief this is ack-gated, NOT seen_ts-gated — a pending
    memo re-surfaces on every injection until this session explicitly acks it, so a
    fact handed to a task is never silently missed. Renders newest-last, ≤
    MEMO_PENDING_MAX lines (≤ MEMO_LINE_MAX chars each), each with the shared ack
    ledger inline so peers can see who's already handled it (no double-implement).
    None on a task with no memos feed (back-compat).

    THIS is the one surface that quiets SETTLED memos (`memo_pending(..., quiet=True)` —
    see memos.memo_settled): a memo a peer already folded into a durable store, or that
    three distinct sessions have dispositioned, is not news this session must be handed on
    every prompt. Quieting is scoped to the nag and nowhere else — `memo show`, the detail
    view and the ack path all keep the full pending list — so a quieted memo is one line
    less on the rail, never one memo less on the record. When it hides anything, the count
    line says so and names the surface that lists them all."""
    pending = memo_pending(task, session, quiet=True)     # oldest-first, ack-gated
    if not pending:
        return None
    quieted = len(memo_pending(task, session)) - len(pending)
    head = ("[task-station] %d memo(s) awaiting YOUR ack on [%s]:"
            % (len(pending), task["id"][:8]))
    if quieted > 0:
        head += (" (%d settled memo(s) quieted — memo show lists all)" % quieted)
    shown = pending[-MEMO_PENDING_MAX:]                  # newest-last within the cap
    out = [head]
    for m in shown:
        body = " ".join((m.get("text") or "").split())
        ackers = [a.get("sid", "")[:8] for a in (m.get("acks") or []) if a.get("sid")]
        ledger = ", ".join(ackers) if ackers else "(none yet)"
        src = _memo_src_label(m.get("from_sid"), m.get("from_task"))
        # Reserve room for the ledger — truncate the BODY, never the whole line, so the
        # "acked by" signal (peers' acks → no double-implement) always survives.
        # A memo that declares corrections (or merely READS like one) is tagged right
        # here, so its weight is visible before it is opened — the nag is often the only
        # surface a fresh session sees.
        corrects = memo_corrections(m)
        if corrects:
            tag = " [CORRECTS: %s]" % ", ".join(corrects)
        elif correction_language(m.get("text")):
            tag = " [reads as a correction]"
        else:
            tag = ""
        prefix = '  • %s (%s, from %s)%s ' % (m["id"][:8], rel_time(m.get("ts")), src, tag)
        suffix = ' — acked by: %s' % ledger
        budget = MEMO_LINE_MAX - len(prefix) - len(suffix) - 2   # 2 = the wrapping quotes
        if budget < 12:                                          # pathological: keep it sane
            budget = 12
        if len(body) > budget:
            body = body[:budget - 1].rstrip() + "…"
        out.append('%s"%s"%s' % (prefix, body, suffix))
    extra = len(pending) - len(shown)
    if extra > 0:
        out.append("  (+%d more pending)" % extra)
    out.append("Read full body:  /todo memo show <id8>")
    # An ack must now say what it DID with the memo — a bare ack is rejected. That is the
    # whole fix: an ack is a receipt, and a receipt was once mistaken for an integration.
    out.append("Acknowledge:     /todo memo ack <id8> <decision text>   (auto-signs with "
               "this session)")
    out.append("  … or          /todo memo ack <id8> memory:<note-slug>   (folded into "
               "that memory note)")
    out.append("  … or          /todo memo ack <id8> noop:<reason>       (no durable "
               "change needed)")
    return "\n".join(out)


def _prompt_is_human(kind, text):
    """True when a captured prompt reads as a genuine HUMAN-typed message (board B8),
    False for a Claude/slash/hook-generated one — a slash `command`, a compaction
    `compact` summary, or text that opens with a `/` slash-command, a `<command-…>`
    managed wrapper, or a `<!--` HTML comment. The last case catches the EXPANDED body
    of a slash command (Claude Code records `/todo`, `/save`, etc. as a user turn whose
    text is the command file's markdown, which the bare aliases lead with a
    `<!-- task-station-managed: … -->` marker) — the noise that used to bloat the trail.

    Also filtered as GENERATED (they arrive as user-role turns but no human typed them):
    any leading `<tag …>` harness/managed wrapper (`<command-…>`, `<task-notification>`,
    `<system-reminder>`, skill guards like `<SUBAGENT-STOP>`, …), a Skill-tool injection
    (`Base directory for this skill: …`), and the `[Request interrupted by user]`
    marker the harness records on an interrupt."""
    if kind in ("command", "compact"):
        return False
    t = (text or "").lstrip()
    if t.startswith("/") or t.startswith("<!--"):
        return False
    if re.match(r"<[A-Za-z][\w.-]*[\s>]", t):          # any tag-like managed wrapper
        return False
    if t.startswith("Base directory for this skill:"):
        return False
    if t.startswith("[Request interrupted"):
        return False
    return True


# ---- session roster: hub ordinals + worker entries (task #463) ---------------
# Hub sessions are numbered <seq>-<n> (task["hub_ordinal_next"] is monotonic,
# never reused; -0 = the session that created the task). Workers keep their full
# descriptive name and NEVER get an ordinal. Everything lives inside the task
# blob (session_meta) — no store schema change; ensure_ordinals() is the lazy
# backfill for pre-roster tasks (oldest hub meta ts = the creator = 0).

def _next_hub_ordinal(task):
    n = int(task.get("hub_ordinal_next") or 0)
    task["hub_ordinal_next"] = n + 1
    return n

def ensure_ordinals(task):
    """Lazily backfill ordinals onto pre-roster hub session_meta entries, oldest
    ts first (the creator becomes -0). Returns True when anything changed (the
    caller persists). Idempotent; ignores role=='worker' entries."""
    meta = task.get("session_meta") or {}
    missing = [(sid, m) for sid, m in meta.items()
               if m.get("role") == "hub" and "ordinal" not in m]
    if not missing:
        return False
    missing.sort(key=lambda kv: kv[1].get("ts") or 0)
    for sid, m in missing:
        m["ordinal"] = _next_hub_ordinal(task)
    return True

def hub_ordinal(task, sid):
    m = (task.get("session_meta") or {}).get(sid) or {}
    if m.get("role") != "hub":
        return None
    if "ordinal" not in m:
        ensure_ordinals(task)
        m = (task.get("session_meta") or {}).get(sid) or {}
    return m.get("ordinal")

def ordinal_label(task, sid):
    """'<seq>-<n>' for a hub session of this task, or None (workers/unknown)."""
    n = hub_ordinal(task, sid)
    if n is None or task.get("seq") is None:
        return None
    return "%s-%s" % (task["seq"], n)

def session_title_label(task, sid):
    """The window/tab label for ONE session on `task` — '#<seq>-<ln>: <title>'.

    Two sessions on the same task must never produce the same label. The number
    is the roster line the board already prints everywhere (444-26, 566-1), so a
    human reading a tab and a peer reading `sessions` are naming the same thing.

    Returns `(label, ln)`. `ln` is the resolved '<seq>-<n>' string, or None when
    it could not be resolved — in which case the label FALLS BACK to the old,
    session-blind '#<seq>: <title>' rather than emitting a wrong or blank number.
    Callers must report which of the two they emitted; a title that silently
    loses its ln is the bug this exists to prevent.

    Resolution goes through ordinal_label(), the SAME lookup `whoami --porcelain`
    field 2 serves and delegate's `_spawner_ordinal` consumes. There is no second
    way to compute an ln. Workers have no ordinal by design (they carry a
    descriptive name instead), so a worker session takes the fallback.

    KNOWN LIMIT — the SessionStart hook sets a title ONCE, at session start, so a
    session whose ln changes afterwards keeps its original title. An ln is
    assigned at attach and does not normally move, so this is acceptable; a
    reader must simply not assume the title live-updates."""
    ln = ordinal_label(task, sid) if sid else None
    if ln:
        return ("#%s: %s" % (ln, task.get("title", "")), ln)
    return ("#%s: %s" % (task.get("seq", "?"), task.get("title", "")), None)


def session_display(task, sid):
    """Human handle for a session on this task: hub → '<seq>-<n>', worker → its
    slug name, else sid[:8]; '?' when absent."""
    if not sid:
        return "?"
    lab = ordinal_label(task, sid)
    if lab:
        return lab
    m = (task.get("session_meta") or {}).get(sid) or {}
    return m.get("name") or sid[:8]


def register_worker_session(task, sid, name=None, model=None, harness="claude",
                            status="running"):
    """Roster a WORKER session on the task record (full descriptive name, no
    ordinal). Re-registration updates status/model in place, preserving spawned_at."""
    if not sid:
        return
    meta = task.setdefault("session_meta", {})
    prev = meta.get(sid) or {}
    meta[sid] = {"ts": _now(), "role": "worker",
                 "name": name or prev.get("name"),
                 "model": model or prev.get("model"),
                 "harness": harness or prev.get("harness") or "claude",
                 "status": status,
                 "spawned_at": prev.get("spawned_at") or _now()}
    if sid not in task.setdefault("sessions", []):
        task["sessions"].append(sid)


def touch(task, session=None, note=None, reopen=False, register=True):
    task["updated_ts"] = _now()
    if reopen and is_closed(task):
        task["status"] = STATUS_OPEN          # reopening a closed task → open
        clear_closed(task)                    # …and it's no longer closed
    if session and register:
        if session not in task.get("sessions", []):
            task.setdefault("sessions", []).append(session)
        # Record where this session is running so /todo can later hand back a
        # `cd … && claude --resume …` one-liner that reopens it in the right dir.
        meta = task.setdefault("session_meta", {})
        prev = meta.get(session) or {}
        entry = {"cwd": os.getcwd(), "ts": _now(), "role": "hub",
                 "spawned_at": prev.get("spawned_at") or _now()}
        if prev.get("role") == "hub" and "ordinal" in prev:
            entry["ordinal"] = prev["ordinal"]        # re-touch keeps the number
        else:
            ensure_ordinals(task)                     # backfill olds FIRST so they
            entry["ordinal"] = _next_hub_ordinal(task)  # keep lower numbers
        if prev.get("preborn"):
            entry["preborn"] = True
        meta[session] = entry
    # Fold this activity moment into the time-in-task spans (idle-gap-capped).
    record_activity_span(task, task["updated_ts"])
    # register=False (cross-task scope updates) still ATTRIBUTES the log event to the
    # acting session — it just doesn't enroll that session as a worker on this task.
    add_log(task, note, session=session)


def set_status(task, status, note=None, session=None):
    """Move a task between the settable board states (open ⇄ active). Idempotent —
    returns True only if it changed, logging the transition so the activity trail
    shows when work began. Refuses anything outside open/active (returns False) —
    closing goes through /done, not here — so a typo never mislabels a task.
    `session` attributes the transition (log + status events) to its author."""
    if status not in STATUS_SETTABLE:
        return False
    prev = task_status(task)
    if prev == status:
        return False
    task["status"] = status
    if prev == STATUS_CLOSED:
        clear_closed(task)          # open/active from closed = a reopen
    if status == STATUS_ACTIVE:
        # Promotion to active = real work started (an edit here, or a delegate
        # --worktree worker via `status active`) → the digest may now be stale, and
        # this counts as one meaningful event toward the milestone staleness nudge.
        mark_digest_dirty(task)
        bump_digest_events(task)
    add_log(task, note or ("status → %s" % status), session=session)
    add_event(task, "status", "%s → %s" % (prev, status), session)
    return True


def close_task_inplace(task, note=None, session=None):
    """Close a task IN PLACE — the pure-dict half of the close path, safe to call
    inside a `store.mutate` mutator (no I/O, no save). Sets the status, stamps the
    real close moment, and logs + events the transition exactly as `/done` does.
    Returns True only if it changed; closing an already-closed task is a no-op.

    WHY THIS EXISTS rather than `set_status(task, STATUS_CLOSED)`: `set_status`
    deliberately REFUSES `closed` — it guards the settable open⇄active pair so a typo
    can never mislabel a task — and the two genuine close paths (`_close_one`,
    `cmd_done`) each hand-roll these same four lines before doing their OWN session
    and worker teardown. This is that shared core, named once.

    Callers that close as a SIDE EFFECT of a structural write (`--absorbed-by`,
    `--replaces`) use only this half and deliberately do NOT reap workers or detach
    sessions: neither verb means "this session is finished", and a structural
    declaration must not kill someone else's running worker."""
    prev = task_status(task)
    if prev == STATUS_CLOSED:
        return False
    task["status"] = STATUS_CLOSED
    stamp_closed(task)                      # the real moment it entered closed
    add_log(task, note or "closed", session=session)
    add_event(task, "status", "%s → %s" % (prev, STATUS_CLOSED), session)
    return True


def promote_active(task, note=None, session=None):
    """Promote an OPEN task to active because work has started. Idempotent — a
    no-op (returns False) when the task is already active or closed (an edit never
    resurrects a closed task)."""
    if task_status(task) != STATUS_OPEN:
        return False
    return set_status(task, STATUS_ACTIVE,
                      note=note or "auto-promoted to active (work started)",
                      session=session)


def _project_dir_for(cwd):
    """The session-transcript bucket Claude Code uses for a given launch cwd."""
    return os.path.join(g("PROJECTS_ROOT"), cwd.replace("/", "-"))


_MSGCOUNT_MEM = {}       # (path, mtime_ns, size) -> count             [this process]
_REPLIES_MEM = {}        # (path, mtime_ns, size) -> {uuid: reply}     [this process]
_SESSION_PATH_MEM = {}   # (projects_root, sid) -> resolved transcript [this process]
_MSGCOUNT_ATEXIT = False


def _stat_key(path):
    """(st_mtime_ns, st_size) — a file's version identity — or None when it can't be
    stat-ed (missing, unreadable, or not a path at all). None means "no valid cache
    key", and every caller then reads the file unconditionally, so behaviour is
    exactly what it was before any cache existed."""
    try:
        st = os.stat(path)
    except (OSError, TypeError, ValueError):
        return None
    return (st.st_mtime_ns, st.st_size)


def _mem_put(cache, key, value, cap):
    """Insert into an in-process cache, dropping the oldest-inserted entry at `cap`.
    Insertion order is dict order (3.7+), so this is a plain FIFO — good enough for
    caches whose working set is one board render, and it keeps a long-lived process
    (the MCP server) from growing a key per transcript append forever."""
    if key not in cache and len(cache) >= cap:
        cache.pop(next(iter(cache)), None)
    cache[key] = value
    return value


def _msgcount_cache_path():
    """Resolved per call, against the DATA global — tests repoint it per test, and a
    moved CLAUDE_CONFIG_DIR must take its cache with it."""
    return os.path.join(g("DATA"), CACHE_DIR, MSGCOUNT_CACHE_FILE)


def _msgcount_disk():
    """The on-disk count cache, as {"file", "entries", "dirty"}. Loaded once per
    process, and re-loaded if DATA is repointed (the old file is flushed first so
    nothing computed under it is lost).

    A malformed file, a foreign schema, a half-written entry: all silently yield an
    EMPTY cache. The cost of that is a recompute, which is the thing this file was
    only ever an optimisation for."""
    f = _msgcount_cache_path()
    cur = g("_MSGCOUNT_DISK")
    if cur is not None and cur.get("file") == f:
        return cur
    if cur is not None:
        _msgcount_flush()                     # DATA moved → persist the previous file first
    entries = {}
    try:
        with open(f, encoding="utf-8") as fh:
            raw = json.load(fh)
        got = raw.get("entries") if isinstance(raw, dict) else None
        for k, v in (got or {}).items():
            # Every field must be an int: a truncated write or a hand-edit can leave
            # a partial row, and one bad row must not poison the rest of the cache.
            if (isinstance(k, str) and isinstance(v, list) and len(v) == 4
                    and all(isinstance(n, int) and not isinstance(n, bool) for n in v)):
                entries[k] = list(v)
    except (OSError, ValueError, TypeError, AttributeError):
        entries = {}
    set_g("_MSGCOUNT_DISK", {"file": f, "entries": entries, "dirty": False})
    return g("_MSGCOUNT_DISK")


def _msgcount_flush():
    """Persist the count cache atomically. Best-effort by contract — it runs from an
    atexit handler inside a Stop hook, so every failure path is a silent return.

    Never CREATES the data dir: a cache flush is not what brings a store into being,
    and a test whose tmpdir has already been removed must not see it resurrected."""
    st = g("_MSGCOUNT_DISK")
    if not st or not st.get("dirty"):
        return
    st["dirty"] = False                       # one attempt per dirty batch
    tmp = None
    try:
        entries = st["entries"]
        if len(entries) > MSGCOUNT_CACHE_MAX:              # evict least-recently-used
            entries = dict(sorted(entries.items(), key=lambda kv: kv[1][3],
                                  reverse=True)[:MSGCOUNT_CACHE_MAX])
            st["entries"] = entries
        f = st["file"]
        if not os.path.isdir(os.path.dirname(os.path.dirname(f))):   # <data_dir> gone
            return
        os.makedirs(os.path.dirname(f), exist_ok=True)
        tmp = "%s.tmp.%d" % (f, os.getpid())
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"v": 1, "entries": entries}, fh)
        os.replace(tmp, f)
    except (OSError, ValueError, TypeError):
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _msgcount_persist_later():
    """Register the single atexit flush, lazily — a process that computes no new count
    never writes the cache file at all."""
    global _MSGCOUNT_ATEXIT
    if _MSGCOUNT_ATEXIT:
        return
    _MSGCOUNT_ATEXIT = True
    try:
        atexit.register(_msgcount_flush)
    except Exception:
        pass                                  # a cache that can't persist is still correct


def _session_msgcount(path):
    """Count of non-empty, non-system user messages in a transcript (0 if unreadable).

    Used to tell a real working session from an empty/stray one — size alone lies
    (a freshly-spawned empty session can still be several KB of system init).

    Cached on the transcript's (mtime, size) per the note above: in memory first,
    then <data_dir>/cache/msgcounts.json. The returned value — including the 0 for an
    unreadable file — is exactly what a direct parse returns. When the file cannot be
    stat-ed there is no key to cache under, so the parse runs as it always did."""
    key = _stat_key(path)
    if key is None:
        return g("_session_msgcount_uncached")(path)
    memkey = (path, key[0], key[1])
    hit = _MSGCOUNT_MEM.get(memkey)
    if hit is not None:
        return hit
    disk = _msgcount_disk()
    ent = disk["entries"].get(path)
    if ent is not None and ent[0] == key[0] and ent[1] == key[1]:
        now = int(_now())
        if now - ent[3] > MSGCOUNT_CACHE_TOUCH:     # keep LRU honest at ~1 write/day
            ent[3] = now
            disk["dirty"] = True
            _msgcount_persist_later()
        return _mem_put(_MSGCOUNT_MEM, memkey, ent[2], g("MSGCOUNT_MEM_MAX"))
    n = g("_session_msgcount_uncached")(path)
    disk["entries"][path] = [key[0], key[1], n, int(_now())]
    disk["dirty"] = True
    _msgcount_persist_later()
    return _mem_put(_MSGCOUNT_MEM, memkey, n, g("MSGCOUNT_MEM_MAX"))


def _session_msgcount_uncached(path):
    """The actual parse behind _session_msgcount — one full pass over the transcript.
    Call the cached front door instead unless you are deliberately measuring."""
    n = 0
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                msg = o.get("message")
                if not isinstance(msg, dict) or msg.get("role") != "user":
                    continue
                c = msg.get("content")
                if isinstance(c, str):
                    t = c
                elif isinstance(c, list):
                    t = " ".join(b.get("text", "") for b in c
                                 if isinstance(b, dict) and b.get("type") == "text")
                else:
                    t = ""
                t = t.strip()
                if t and not t.startswith("<"):
                    n += 1
    except OSError:
        return 0
    return n


def _find_session_path(sid):
    """Locate a session's transcript across ALL project buckets.

    A session's bucket is its LAUNCH cwd, which can differ from whatever cwd /todo
    happened to record (e.g. you launched from ~ but cd'd into a worktree before the
    task was touched). So we search every bucket by session id rather than trusting
    the recorded cwd. Returns the `.jsonl` path, or None.

    A resolved path is memoized per process (keyed by the projects root, which tests
    repoint) and re-checked with ONE os.path.exists on each hit — that turns a
    listdir-plus-N-stats scan into a single stat, which is what the board's thousands
    of lookups actually cost. Only SUCCESSES are cached, so a transcript that appears
    later is still found; a cached path that disappears falls back to a full rescan,
    and every consumer pairs the path with a content read that fails closed anyway."""
    memkey = (g("PROJECTS_ROOT"), sid)
    hit = _SESSION_PATH_MEM.get(memkey)
    if hit is not None and os.path.exists(hit):
        return hit
    try:
        buckets = os.listdir(g("PROJECTS_ROOT"))
    except OSError:
        return None
    for b in buckets:
        p = os.path.join(g("PROJECTS_ROOT"), b, sid + ".jsonl")
        if os.path.exists(p):
            return _mem_put(_SESSION_PATH_MEM, memkey, p, SESSION_PATH_MEM_MAX)
    return None


def estimate_session_tokens(session):
    """A cheap, rough proxy for how full the context window is: the session
    transcript's byte size // 4 (~chars-per-token). Uses os.path.getsize — no read —
    so it stays fast on a large transcript, and it errs slightly EARLY (which is what
    we want for a pre-compaction nudge). Returns 0 when the transcript can't be found.
    Feeds the proactive context-pressure Stop nudge (see cmd_stop_nudge)."""
    if not session:
        return 0
    path = g("_find_session_path")(session)
    if not path:
        return 0
    try:
        return os.path.getsize(path) // 4
    except OSError:
        return 0


def _extract_usage(o):
    """Pull the `usage` dict out of one decoded transcript line, whether it's nested
    under `message` (Claude Code records assistant turns as {"message": {..., "usage":
    {...}}}) or sits at the top level. None when there's no usage dict."""
    if not isinstance(o, dict):
        return None
    msg = o.get("message")
    if isinstance(msg, dict) and isinstance(msg.get("usage"), dict):
        return msg["usage"]
    if isinstance(o.get("usage"), dict):
        return o["usage"]
    return None


def measure_context_tokens(session):
    """The CURRENT context-window occupancy in tokens, read from the session's real
    transcript — the authoritative signal the proactive checkpoint_pct nudge uses.

    Claude Code writes a `usage` block on each assistant message; the live context
    size is the MOST RECENT one's input_tokens + cache_read_input_tokens +
    cache_creation_input_tokens (output_tokens are NOT resident context). We read only
    the file TAIL (last _USAGE_TAIL_BYTES) and scan its lines in REVERSE, so a huge
    transcript costs a small bounded read; a sliced-off partial first line and any
    half-written/malformed record are skipped rather than crashing. Returns 0 when
    nothing usable is found so callers fall back to estimate_session_tokens()."""
    if not session:
        return 0
    path = g("_find_session_path")(session)
    if not path:
        return 0
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > _USAGE_TAIL_BYTES:
                f.seek(size - _USAGE_TAIL_BYTES)   # tail only — the newest usage is here
            chunk = f.read()
    except OSError:
        return 0
    # Reverse-scan the tail: the first line that parses AND carries a usage block wins.
    for line in reversed(chunk.decode("utf-8", "replace").splitlines()):
        line = line.strip()
        if not line or '"usage"' not in line:      # cheap skip before the JSON parse
            continue
        try:
            usage = _extract_usage(json.loads(line))
        except ValueError:
            continue                               # malformed/partial line — skip it
        if not usage:
            continue
        total = 0
        for k in _CONTEXT_USAGE_KEYS:
            v = usage.get(k)
            if isinstance(v, (int, float)):
                total += int(v)
        if total > 0:
            return total
    return 0


def session_model(session):
    """The model id the session is currently running, read from the most recent
    assistant message in the transcript (`message.model`) — used to size the context
    window (pricing.context_window_for) so the checkpoint %/tokens are model-aware. Same
    bounded tail read as measure_context_tokens. Returns "" when nothing usable is found
    (callers then fall back to the 200k default)."""
    if not session:
        return ""
    path = g("_find_session_path")(session)
    if not path:
        return ""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > _USAGE_TAIL_BYTES:
                f.seek(size - _USAGE_TAIL_BYTES)
            chunk = f.read()
    except OSError:
        return ""
    for line in reversed(chunk.decode("utf-8", "replace").splitlines()):
        line = line.strip()
        if not line or '"model"' not in line:      # cheap skip before the JSON parse
            continue
        try:
            o = json.loads(line)
        except ValueError:
            continue
        msg = o.get("message") if isinstance(o, dict) else None
        model = msg.get("model") if isinstance(msg, dict) else None
        if isinstance(model, str) and model.strip():
            return model.strip()
    return ""


def _settings_model(path):
    """`.model` from a Claude Code settings.json at `path`, or "" if the file is
    missing / unparseable / carries no string model. Read defensively — settings
    files are user-editable and may be absent or malformed."""
    try:
        with open(path) as f:
            m = json.load(f).get("model")
    except (OSError, ValueError, AttributeError):
        return ""
    return m.strip() if isinstance(m, str) else ""


def claude_code_model_selection():
    """The effective Claude Code model SELECTION string — which, unlike the
    transcript's `message.model`, still carries the `[1m]` 1M-context marker (Claude
    Code strips it before calling the API, so the transcript never records it).

    First non-empty wins: ANTHROPIC_MODEL env → ~/.claude/settings.local.json →
    ~/.claude/settings.json. Returns "" when nothing is found (callers then fall back
    to the transcript model / default window)."""
    env = os.environ.get("ANTHROPIC_MODEL")
    if env and env.strip():
        return env.strip()
    home = os.path.expanduser("~")
    for name in ("settings.local.json", "settings.json"):
        m = _settings_model(os.path.join(home, ".claude", name))
        if m:
            return m
    return ""


def _model_family_token(model_id):
    """The family token ({"opus","sonnet","haiku","fable"}) found in a model id, or
    None when the id carries no recognizable family."""
    mid = (model_id or "").lower()
    for fam in _MODEL_FAMILIES:
        if fam in mid:
            return fam
    return None


def _same_model_family(a, b):
    """True only when both ids resolve to the SAME recognizable family. If EITHER id
    has no family token, be conservative and return False — so we never inflate the
    window on an unknown id (e.g. a `--model sonnet` session under an `opus[1m]`
    default must NOT be upgraded to 1M)."""
    fa, fb = _model_family_token(a), _model_family_token(b)
    return fa is not None and fa == fb


def _read_statusline_stdin():
    """The statusLine JSON piped on stdin (or {} when absent). NEVER blocks: reads only
    when stdin is a non-tty pipe; tolerates empty/malformed input. Used by the
    --statusline provider path to capture the harness context-window size."""
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return {}
        data = sys.stdin.read()
    except Exception:
        return {}
    if not data or not data.strip():
        return {}
    try:
        d = json.loads(data)
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def persist_harness_context_window(session, size):
    """Merge the harness-reported context-window SIZE into the per-session HUD snapshot
    (the same file harness_context_window reads and hud.observe writes). Best-effort and
    atomic; a non-positive / unparseable size, or a value equal to what is already
    stored, is a no-op (so the common per-render case does zero IO). This is the
    HUD-INDEPENDENT capture path: the statusline payload is the ONLY channel Claude Code
    exposes context_window_size on (no hook receives it), so the always-on --statusline
    provider persists it here even when the cost HUD is off."""
    if not session:
        return
    try:
        size = int(size)
    except (TypeError, ValueError):
        return
    if size <= 0:
        return
    try:
        import paths
        d = os.path.join(paths.data_dir(), "hud")
        p = os.path.join(d, "%s.json" % session)
        try:
            with open(p) as f:
                snap = json.load(f)
            if not isinstance(snap, dict):
                snap = {}
        except (OSError, ValueError):
            snap = {}
        if snap.get("context_window_size") == size:
            return
        snap["context_window_size"] = size
        os.makedirs(d, exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w") as f:
            json.dump(snap, f)
        os.replace(tmp, p)
    except OSError:
        pass


def harness_context_window(session):
    """The context-window size (tokens) the HARNESS ITSELF reported for `session`,
    read from the HUD snapshot (the statusline payload's
    `context_window.context_window_size`, persisted by hud.observe). This is the
    authoritative, 1M-aware window: a runtime `/model` selection carries a `[1m]`
    marker that the transcript (message.model) and settings.json/ANTHROPIC_MODEL never
    record, but the statusline payload's size does. Returns an int > 0, or None when
    the HUD has not captured a size yet (HUD off, or no render this session) — callers
    then fall back to model-id derivation. Never raises."""
    if not session:
        return None
    try:
        import paths
        p = os.path.join(paths.data_dir(), "hud", "%s.json" % session)
        with open(p) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    try:
        n = int(d.get("context_window_size"))
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def effective_context_window(session):
    """The context-window size (tokens) to size the checkpoint %/tokens math against
    for `session`. Precedence: an EXPLICIT override (TASK_STATION_CONTEXT_WINDOW / the
    `context_window` config key) always wins; else the HARNESS-reported window
    (harness_context_window — the statusline payload's context_window_size, which is
    already 1M-aware) is authoritative; else it is derived from the transcript model and
    UPGRADED to the Claude Code selection's 1M window only when the selection is
    genuinely larger AND of the same model family; else 200k. This is why an Opus-1M
    session (transcript records the marker-stripped `claude-opus-4-8`, and settings/env
    carry no marker for a runtime `/model` pick) is still sized at 1,000,000."""
    import config
    tm = g("session_model")(session)            # transcript base id, marker stripped
    win = config.context_window(tm)             # explicit override wins in here
    # An explicit override (env or config key, positive int) is user intent — never
    # inflate/deflate past it; return immediately.
    raw = os.environ.get("TASK_STATION_CONTEXT_WINDOW")
    if raw is None:
        raw = config.get("context_window", None)
    if raw is not None:
        try:
            if int(raw) > 0:
                return win
        except (TypeError, ValueError):
            pass
    # No user override: the harness's OWN reported window is authoritative — it already
    # reflects a 1M runtime selection that the transcript/settings never record.
    hw = harness_context_window(session)
    if hw and hw > 0:
        return hw
    # No HUD data: fall back to the CC model-selection marker, else the transcript model.
    cm = g("claude_code_model_selection")()     # selection, may carry [1m]
    if cm:
        import pricing
        if pricing.context_window_for(cm) > pricing.context_window_for(tm) \
                and _same_model_family(tm, cm):
            win = config.context_window(cm)
    return win


def _session_cwd(path):
    """The cwd a session was launched in, read from the transcript itself —
    authoritative (Claude Code records it on every entry), and decode-free (we never
    have to reverse the lossy bucket-name encoding). None if unreadable/absent."""
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    cwd = json.loads(line).get("cwd")
                except ValueError:
                    continue
                if cwd:
                    return cwd
    except OSError:
        return None
    return None


def _load_delegate_registry():
    try:
        with open(g("DELEGATE_REGISTRY")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# ---- bg-aware resume commands (task #464) ------------------------------------
# A bare `claude --resume <sid>` is REFUSED by Claude Code when <sid> is a
# currently-live `--bg`/background agent ("currently running as a background
# agent; use `claude agents` … or --fork-session"). So every resume one-liner we
# emit routes through bg_aware_resume(): live-bg → `claude agents` (ATTACH the exact
# live session — the whole point of -s/resume is to land IN it, not a copy),
# otherwise today's bare `--resume`. The confirmation text additionally surfaces the
# fork path (`--fork-session`) as an aside via bg_resume_hint() for anyone who wants
# to branch a copy instead.

def _live_bg_index():
    """{sessionId: row} of currently-live background agents from `claude agents
    --json` (via the delegate harness adapter), snapshotted for this process. {} on
    any failure so a missing/again-unavailable `claude` degrades to today's bare
    `--resume`. A task-station process is one CLI invocation (board/detail render),
    so a process-lifetime snapshot is the right granularity."""
    if g("_LIVE_BG_INDEX") is not None:
        return g("_LIVE_BG_INDEX")
    idx = {}
    # TASK_STATION_NO_AGENT_QUERY lets the test suite (and any headless caller that
    # must not shell out) skip the live `claude agents` probe deterministically.
    if not os.environ.get("TASK_STATION_NO_AGENT_QUERY"):
        try:
            dg = g("_delegate_module")()
            if dg is not None:
                idx = dg.harness.ClaudeAdapter().agents_index() or {}
        except Exception:
            idx = {}
    set_g("_LIVE_BG_INDEX", idx if isinstance(idx, dict) else {})
    return g("_LIVE_BG_INDEX")


def _is_live_bg(sid):
    """True when `sid` is a currently-live background agent — a background-KIND row
    with a pid; interactive sessions resume with a bare --resume. Tolerates a stored
    SHORT id via unique-prefix match (mirrors ClaudeAdapter.worker_status). False on
    absence/no-pid/any failure."""
    if not sid:
        return False
    idx = _live_bg_index()
    row = idx.get(sid)
    if row is None:
        hits = [r for s, r in idx.items() if s.startswith(sid)]
        row = hits[0] if len(hits) == 1 else None
    return bool(row) and row.get("kind") == "background" and bool(row.get("pid"))


def bg_aware_resume(sid, cwd=None):
    """The resume one-liner for `sid`, BG-aware.

    When `sid` is a currently-live background agent a bare `--resume` is REFUSED, and
    the POINT of a resume is to land in the EXACT live session — so emit `claude
    agents` to ATTACH it (never a `--fork-session` copy, which would silently branch a
    pointless duplicate). Otherwise return today's bare `[cd <cwd> && ]claude --resume
    <sid>`. `cwd` prefixes `cd <cwd> && ` on the bare-resume path only (attaching is
    global — no cd needed). None when `sid` is falsy."""
    if not sid:
        return None
    if _is_live_bg(sid):
        return "claude agents"
    prefix = ("cd %s && " % cwd) if cwd else ""
    return "%sclaude --resume %s" % (prefix, sid)


def bg_resume_hint(sid, cwd=None):
    """Human hint when `sid` is a live background agent: the resume attaches the live
    session (`claude agents`); mention forking a copy only as an ASIDE for anyone who
    wants to branch instead. None when `sid` isn't live-bg (nothing special to
    surface)."""
    if not _is_live_bg(sid):
        return None
    return ("attach the live session: `claude agents`  ·  to branch a copy instead: "
            "`claude --resume %s --fork-session`" % sid)


def worker_targets(task):
    """Structured resume entries for the in-project workers this task has delegated
    into: a list of `{label, command, note}` dicts (command is None when no worker
    dir is recorded yet). The SINGLE source for both `worker_lines()` (the terminal
    task-detail) and the HTML board's de-emphasised Workers subsection, so they match.

    Worker session-ids are read LIVE from the delegate registry (keyed
    <seq>:<project>[:<label>]) so they reflect delegate's own self-healing rather
    than a stale snapshot. Lists the default worker per repo plus any labelled
    concurrent ones. Returns [] when the task has no recorded projects."""
    projects = task.get("projects") or []
    if not projects:
        return []
    reg = _load_delegate_registry()
    seq = task.get("seq")
    out = []
    for p in projects:
        base = "%s:%s" % (seq, p)
        keys = sorted(k for k in reg if k == base or k.startswith(base + ":"))
        if not keys:
            out.append({"label": p, "command": None, "note": "no worker recorded yet"})
            continue
        for k in keys:
            e = reg.get(k, {})
            d, sid, model = e.get("dir"), e.get("session_id"), e.get("model")
            disp = p if k == base else "%s [%s]" % (p, e.get("label") or k.split(":", 2)[-1])
            if d and sid:
                out.append({"label": disp,
                            "command": bg_aware_resume(sid, d),
                            "note": None, "model": model, "session": sid})
            elif d:
                out.append({"label": disp, "command": "cd %s && claude" % d,
                            "note": "no active worker yet", "model": model, "session": sid})
            else:
                out.append({"label": disp, "command": None,
                            "note": "no worker recorded yet", "model": model, "session": sid})
    return out


def worker_lines(task, live=None):
    """The terminal-detail rendering of `worker_targets()` — aligned
    `    <repo>   <resume command>  · <model>` lines (model shown when the registry
    recorded one; a `(note)` suffix when there's no live worker yet). When `live`
    (a `{session_id: row}` index from _live_session_index()) is given, a worker
    whose session is actually running gets a `● busy/idle · age` annotation.
    Returns [] when the task has no recorded projects."""
    live = live or {}
    out = []
    for w in worker_targets(task):
        model = ("  · %s" % w["model"]) if w.get("model") else ""
        note = _live_note(live.get(w.get("session")))
        if w["command"] and not w["note"]:
            out.append("    %-22s %s%s%s" % (w["label"], w["command"], model, note))
        elif w["command"]:
            out.append("    %-22s %s%s   (%s)%s" % (w["label"], w["command"], model, w["note"], note))
        else:
            out.append("    %-22s (%s)" % (w["label"], w["note"]))
    return out


def _is_resumable(cmd):
    """True when `cmd` already targets a CONCRETE session — a live `--resume` or a
    pre-bound `--session-id`. The jump path uses this to decide whether to use the
    command as-is or mint a fresh session; a descriptive "start fresh" line (bare
    `claude`) or None is NOT resumable."""
    return bool(cmd) and ("--resume " in cmd or "--session-id " in cmd)


def _fresh_session_cwd(meta):
    """Best cwd for a freshly minted session: the most recently recorded session's
    cwd (so the new window opens where the work lives), else the process cwd."""
    for m in sorted((meta or {}).values(), key=lambda m: m.get("ts", 0), reverse=True):
        if m.get("cwd"):
            return m["cwd"]
    return os.getcwd()


def fresh_resume_command(task, preborn=False):
    """Mint a brand-new session id, pre-bind it to `task`, and return
    `(sid, "cd <cwd> && claude --session-id <sid>")`.

    Pre-binding = a hub `session_meta` entry + a session→task link, so when the
    emitted command launches the window, SessionStart sees the link and
    auto-attaches it. This MINTS a uuid, so it is called ONLY from paths that
    actually open a window (the `-s` jump, `pin --new`) — never from the pure
    display path (`resume_command`), which must not mint on every render.

    `preborn=True` marks the meta entry so `resume_command` will emit
    `--session-id <sid>` for it (used by `pin --new`) until its transcript exists."""
    new_sid = str(uuid.uuid4())
    meta = task.setdefault("session_meta", {})
    cwd = _fresh_session_cwd(meta)
    ensure_ordinals(task)
    entry = {"cwd": cwd, "ts": _now(), "role": "hub",
             "ordinal": _next_hub_ordinal(task), "spawned_at": _now()}
    if preborn:
        entry["preborn"] = True
    meta[new_sid] = entry
    if new_sid not in task.setdefault("sessions", []):
        task["sessions"].append(new_sid)
    save_task(task)
    set_link(new_sid, task["id"])
    return new_sid, "cd %s && claude --session-id %s" % (cwd, new_sid)


def _resume_target(task, current_session=None):
    """Structured form of the hub resume one-liner: a dict
    `{command, session, cwd, ts, pinned, fresh}` for the session that holds this
    task's context, or None when no sessions are recorded. `resume_command()` is a
    thin wrapper that returns just `.command`, so this is the SINGLE source of truth
    for the resume line — the terminal task-detail and the HTML board read the same
    computation (and the same self-corrected cwd). `ts` is the resumed transcript's
    last-activity epoch (mtime), or the recorded meta ts for the fresh-start/preborn
    fallbacks; `pinned` mirrors whether the task has a pinned session; `fresh` is True
    only for the no-live-transcript fresh-start fallback.

    GUARANTEE: only ever resumes one of THIS task's own recorded sessions — never
    another task's. (Critical: every hub shares the home bucket, so a whole-bucket
    fallback or `claude --continue` could resume an unrelated task. We never do that.)

    SELF-CORRECTING cwd: the resume directory comes from the *transcript itself* (its
    recorded launch cwd), located by searching every bucket for the session id — NOT
    from whatever cwd /todo happened to capture. So a session is still found and
    resumed correctly even if it was recorded against the wrong directory (e.g. you
    launched from ~ but cd'd into a worktree before the task was touched). Prefers the
    most recent SUBSTANTIVE session (so merely opening `/todo <n>` to look — a 1-2
    message session — never displaces the real working session); if none of the
    task's sessions have a findable live transcript, starts fresh."""
    meta = task.get("session_meta") or {}
    if not meta:
        return None
    pinned = bool(task.get("pinned_session"))
    # The resumed window tints itself on attach via the SessionStart hook
    # (cmd_session_tint → tint_escape), so the resume command stays a clean
    # `cd … && claude …` with no tint prefix.
    # An explicit pin wins (PK-style): always resume that exact session, with the cwd
    # self-corrected from its transcript. Falls through to the heuristic only if the
    # pinned session has no findable live transcript (so a stale pin can't strand you).
    pin = task.get("pinned_session")
    if pin:
        path = g("_find_session_path")(pin)
        if path and g("_session_msgcount")(path) >= 1:
            cwd = _session_cwd(path) or (meta.get(pin) or {}).get("cwd")
            if cwd:
                return {"command": bg_aware_resume(pin, cwd),
                        "session": pin, "cwd": cwd, "ts": os.path.getmtime(path),
                        "pinned": True, "fresh": False}
        # A pin deliberately pre-bound to an UNBORN session (`pin --new`) has no
        # transcript yet. Honour it anyway by emitting `--session-id <pin>` so the
        # window that opens BECOMES that session — stays PURE (the uuid already
        # exists; nothing is minted here). Once it's born, the branch above wins.
        pm = meta.get(pin) or {}
        if pm.get("preborn"):
            cwd = pm.get("cwd") or os.getcwd()
            return {"command": "cd %s && claude --session-id %s" % (cwd, pin),
                    "session": pin, "cwd": cwd, "ts": pm.get("ts"),
                    "pinned": True, "fresh": False}
    hubs = [(sid, m) for sid, m in meta.items() if m.get("role") == "hub"]
    pool = hubs or list(meta.items())
    # For each of THIS task's sessions, find its transcript ANYWHERE and read the
    # cwd from the transcript — independent of the (possibly wrong) recorded cwd.
    # SKIPPED sessions (link == SKIP_SENTINEL) are deliberately untracked and must
    # NEVER be a resume target, even with a live transcript.
    live = []
    for sid, m in pool:
        if get_link(sid) == SKIP_SENTINEL:
            continue
        path = g("_find_session_path")(sid)
        if not path:
            continue
        msgs = g("_session_msgcount")(path)
        if msgs >= 1:
            cwd = _session_cwd(path) or m.get("cwd")
            if cwd:
                live.append((sid, cwd, os.path.getmtime(path), msgs))
    # The current session is NEVER a valid `-s` target: resuming the very
    # conversation you jumped from is the tainting bug. Exclude it HARD (no
    # fallback to it) — if nothing else remains, fall through to fresh-start.
    live = [x for x in live if x[0] != current_session]
    if live:
        # Prefer SUBSTANTIVE sessions: a session that merely ran `/todo <n>` to look
        # has 1-2 messages and must not displace the real working session. Among
        # sessions past a small substance floor, take the most recent; only if none
        # clear the floor do we fall back to the most recent of any.
        cands = [x for x in live if x[3] >= SUBSTANCE_FLOOR] or live
        cands.sort(key=lambda x: x[2], reverse=True)   # newest transcript first
        sid, cwd, mtime, _ = cands[0]
        return {"command": bg_aware_resume(sid, cwd),
                "session": sid, "cwd": cwd, "ts": mtime,
                "pinned": pinned, "fresh": False}
    # No findable live transcript for any recorded session → fresh start
    # (NEVER --continue, which in the shared home bucket could resume a different task).
    pool.sort(key=lambda kv: kv[1].get("ts", 0), reverse=True)
    for sid, m in pool:
        if m.get("cwd"):
            return {"command": ("cd %s && claude   # no live session found — starting fresh; "
                                "re-attach with /todo %s" % (m["cwd"], task.get("seq", ""))),
                    "session": None, "cwd": m["cwd"], "ts": m.get("ts"),
                    "pinned": pinned, "fresh": True}
    return None


def resume_command(task, current_session=None):
    """`cd <dir> && claude …` for the HUB session that holds this task's context, or
    None when no sessions are recorded. Thin wrapper over `_resume_target` (which
    documents the full guarantee + self-correcting-cwd behaviour); returns only the
    command string so existing callers are unchanged."""
    r = _resume_target(task, current_session)
    return r["command"] if r else None


def session_tree(task):
    """The hub/worker session TREE for one task, for `/todo <n>` detail and the board.

    Returns `{"hubs": [hub…], "orphan_workers": [wk…]}` where
      hub = {"sid", "cwd", "ts", "pinned", "main", "live", "msgs", "preborn", "workers": [wk…]}
      wk  = {"sid", "project", "label", "dir", "model", "ts", "spawner"}

    Hubs are the `session_meta` entries with `role == "hub"`, newest-first by their
    recorded ts. `live` = a transcript is findable AND has ≥1 user message; `msgs` is
    that count (0 when no transcript). `main` = the pinned hub if one is pinned, else
    the newest live hub with `msgs >= SUBSTANCE_FLOOR` (fallback: newest live) — every
    other hub is a side-quest. Workers are the delegate-registry entries whose `seq`
    matches this task, nested under the hub whose sid == their `spawner` (WS2 field);
    a worker whose spawner is absent or names no known hub falls to `orphan_workers`.
    Degrades gracefully: absent registry / absent `spawner` → workers listed unnested."""
    meta = task.get("session_meta") or {}
    pinned = task.get("pinned_session")
    # Hub entries, newest-first.
    hub_items = [(sid, m) for sid, m in meta.items() if m.get("role") == "hub"]
    hub_items.sort(key=lambda kv: kv[1].get("ts", 0), reverse=True)
    hub_sids = {sid for sid, _ in hub_items}
    # Workers for this task (seq-matched), grouped by spawning hub.
    workers_by_spawner = {}
    orphan_workers = []
    seq = task.get("seq")
    if seq is not None:
        seq_s = str(seq)
        reg = _load_delegate_registry()
        for e in reg.values():
            if str(e.get("seq")) != seq_s:
                continue
            wk = {"sid": e.get("session_id"), "project": e.get("project"),
                  "label": e.get("label"), "dir": e.get("dir"),
                  "model": e.get("model"), "ts": e.get("ts"),
                  "spawner": e.get("spawner")}
            sp = e.get("spawner")
            if sp and sp in hub_sids:
                workers_by_spawner.setdefault(sp, []).append(wk)
            else:
                orphan_workers.append(wk)
    _wk_sort = lambda w: w.get("ts") or 0
    hubs = []
    for sid, m in hub_items:
        path = g("_find_session_path")(sid)
        msgs = g("_session_msgcount")(path) if path else 0
        hubs.append({
            "sid": sid, "cwd": m.get("cwd"), "ts": m.get("ts"),
            "pinned": (sid == pinned), "main": False,
            "live": bool(path) and msgs >= 1, "msgs": msgs,
            "preborn": bool(m.get("preborn")), "ordinal": m.get("ordinal"),
            "workers": sorted(workers_by_spawner.get(sid, []), key=_wk_sort, reverse=True),
        })
    # Classify the single "main" hub (the working session); the rest are side-quests.
    main = None
    if pinned:
        main = next((h for h in hubs if h["sid"] == pinned), None)
    if main is None:
        live_hubs = [h for h in hubs if h["live"]]        # hubs already newest-first
        subst = [h for h in live_hubs if h["msgs"] >= SUBSTANCE_FLOOR]
        main = (subst or live_hubs or [None])[0]
    if main is not None:
        main["main"] = True
    return {"hubs": hubs, "orphan_workers": sorted(orphan_workers, key=_wk_sort, reverse=True)}


def _live_session_index():
    """`{session_id: row}` for every ACTUALLY-running Claude session (from
    live_sessions.running()), or `{}` if the viewer can't be loaded/read. Guarded so
    a sessions-dir hiccup never breaks the /todo detail or board render."""
    try:
        import live_sessions
        return {r["session_id"]: r for r in live_sessions.running() if r.get("session_id")}
    except Exception:
        return {}


def _live_note(row):
    """The `  ● busy · 1m ago` annotation appended to a resume/worker line whose
    session is live, or '' when the row is None (session not running). The ● marks
    it as a real process; the busy/idle + age come straight from the sessions file."""
    if not row:
        return ""
    return "  ● %s · %s" % (row.get("status") or "live", rel_time(row.get("updated_ts")))


# --- WS2 child-close mirror --------------------------------------------------
# The per-task event feed + `add_event` (with its EVENTS_KEEP / EVENT_TEXT_MAX
# caps) are the canonical definitions up beside `add_log`.


def _mirror_child_close(task, session=None):
    """When a task that was `spawned-from` a parent closes, append a `child` event
    to each parent's feed so a session on the parent hears its child wrapped up.

    Fully graceful: no `related` list, a `related` entry that isn't a spawned-from
    edge, or a parent that no longer loads are each skipped — a bare task closing
    touches nothing. Saves each parent it updates."""
    for edge in task.get("related", []) or []:
        if edge.get("kind") != "spawned-from":
            continue
        parent = load_task(edge.get("id"))
        if not parent:
            continue
        add_event(parent, "child",
                  "child #%s closed: %s" % (task.get("seq"), task.get("title")),
                  session)
        save_task(parent)


def _delegate_module():
    """Import the sibling delegate module (lib/delegate/delegate.py) in-process,
    or None if it can't be imported. Used by the reap-on-close path; a missing/
    broken delegate must never break closing a task."""
    try:
        d = os.path.join(g("BASE"), "delegate")
        if d not in sys.path:
            sys.path.insert(0, d)
        import delegate as _dg
        return _dg
    except Exception:
        return None


def _reap_task_workers(task, session=None):
    """On close, stop this task's still-LIVE `--bg` worker sessions so finished
    workers don't linger/respawn in Agent View (#464/#465). Delegates the airtight
    reap to `delegate.reap_task_workers`, passing the task's session roster
    (`session_meta`) and the closing/current `session` sid so the reap can enforce its
    full safety predicate — a session is reaped ONLY IF it is a seq-matched registry
    worker AND role==worker in the roster AND task-station-named AND not busy AND not
    the current session (see delegate.reap_task_workers). Then records a `stop` ledger
    entry (actor = the closing hub `session`) and flips the roster status to `stopped`
    for each reaped worker. Mutates `task` in place (ledger + roster); the caller
    saves. Wholly best-effort: never raises — closing a task must not fail because
    reaping failed."""
    seq = task.get("seq")
    if seq is None:
        return
    try:
        dg = g("_delegate_module")()
        if dg is None:
            return
        roster = task.get("session_meta") or {}
        reaped = dg.reap_task_workers(seq, roster=roster, current_sid=session)
    except Exception:
        return
    for sid in (reaped or []):
        try:
            add_ledger(task, "stop", worker_sid=sid, actor_sid=session,
                       detail="reaped on close")
            if sid in (task.get("session_meta") or {}):
                register_worker_session(task, sid, status="stopped")
        except Exception:
            pass


# ---- SessionStart orphan sweep -----------------------------------------------
# A `--bg` worker outliving its hub is NORMAL (that is what makes it adoptable), but a
# hub that CRASHED leaves its workers running forever — burning tokens and RAM and
# cluttering Agent View. Once per SessionStart we stop exactly those: task-station
# workers whose SPAWNING HUB SESSION IS GONE.
#
# Liveness comes from live_sessions, the existing process-state layer: Claude Code
# drops one `<PID>.json` per RUNNING process and `running()` returns only rows whose
# pid passes `os.kill(pid, 0)`. Nothing here reads the file's `status` field —
# `busy|idle` describes the MODEL TURN, not whether the session exists, so a hub
# sitting idle waiting for the user to type is ALIVE and its workers survive.
#
# The kill is delegated to `delegate.reap_task_workers`, which already enforces the
# full identity predicate and de-registers each worker from the supervisor BEFORE
# killing its process group, so it cannot be respawned.

def _live_session_ids():
    """The set of session ids CURRENTLY running, or None meaning "unknown".

    None is the absence-of-evidence signal, and every caller must treat it as
    "reap nothing" — never as "everything is dead". It is returned when the sessions
    dir is unreadable, when `running()` fails, and when the live set comes back EMPTY:
    a machine actually running this sweep has at least its own process, so an empty
    listing points at a mis-resolved dir rather than a dead world."""
    try:
        import live_sessions
        if not os.listdir(live_sessions.sessions_dir()):
            return None
        rows = live_sessions.running()
    except Exception:
        return None
    sids = {r.get("session_id") for r in (rows or []) if r.get("session_id")}
    return sids or None


def _orphaned_workers(current_sid=None, now=None):
    """The `(seq, worker_sid)` pairs whose spawning hub is gone. Empty whenever
    anything at all is unknown.

    Reads only the delegate registry + the live-session set; performs no kill and no
    mutation, so it is the whole decision half of the sweep and is tested directly.

    Every gate below requires POSITIVE evidence, so each unknown fails safe:
    a registry entry with no recorded `name` (written before delegate started storing
    one) or no recorded `spawner` is skipped rather than guessed at — it simply never
    gets swept, and self-heals the next time that worker is registered."""
    live = _live_session_ids()
    if live is None:
        return []
    if current_sid:
        # The sweeping session is alive by definition, and this early in SessionStart
        # its own session file may not exist yet — never infer its death.
        live = set(live) | {current_sid}
    dg = g("_delegate_module")()
    if dg is None:
        return []
    try:
        reg = dg.load_reg()
    except Exception:
        return []
    if not isinstance(reg, dict):
        return []
    now = _now() if now is None else now
    out = []
    for e in reg.values():
        if not isinstance(e, dict):
            continue
        sid, seq = e.get("session_id"), e.get("seq")
        if not sid or seq is None:
            continue
        if current_sid and sid == current_sid:
            continue                        # (2) never the sweeping session itself
        if not dg._is_ts_worker_name(e.get("name")):
            continue                        # (1) only OUR workers, never a foreign agent
        spawner = e.get("spawner")
        if not spawner or spawner in live:
            continue                        # unknown provenance, or the hub is alive
        started = e.get("started_ts")
        if started is None:
            started = e.get("ts")
        try:
            if started is None or (now - float(started)) < ORPHAN_SWEEP_GRACE_SECS:
                continue                    # (5) too new / no age → leave it alone
        except (TypeError, ValueError):
            continue
        out.append((seq, sid))
    return out


def sweep_orphan_workers(current_sid=None, adapter=None, now=None):
    """Stop every task-station worker whose spawning hub session is gone.

    Returns the list of `(seq, sid)` pairs actually reaped — empty when there is
    nothing to do OR when anything is unknown. Each reap is recorded on its task as a
    `stop` ledger entry (actor = the sweeping session) and flips the roster status to
    `stopped`, so a sweep that stops work someone still needed is visible in
    `/todo <n>` rather than looking like a crash.

    Inherits the `reap_workers_on_done` kill switch for free (the reap itself is gated
    on it), so a user who turned reaping off gets no sweep either.

    Wholly best-effort: never raises. `adapter` is injectable for tests; production
    passes None and `reap_task_workers` builds the real one."""
    reaped = []
    try:
        cands = _orphaned_workers(current_sid=current_sid, now=now)
    except Exception:
        return reaped
    if not cands:
        return reaped
    dg = g("_delegate_module")()
    if dg is None:
        return reaped
    for seq, sid in cands:
        try:
            task = resolve_ref(str(seq))
            if not task:
                continue
            got = dg.reap_task_workers(seq, adapter=adapter,
                                       roster=task.get("session_meta") or {},
                                       current_sid=current_sid, only_sids=[sid])
        except Exception:
            continue                        # a failed reap never aborts the others
        for s in (got or []):
            reaped.append((seq, s))
            try:
                _record_orphan_reap(task["id"], s, current_sid)
            except Exception:
                pass                        # the worker is already stopped; logging is
                                            # best-effort and must not mask that
    return reaped


def _record_orphan_reap(task_id, worker_sid, actor_sid,
                        detail="reaped on session start: orphaned (spawning hub gone)"):
    """Log one reap onto its task: a `stop` ledger entry naming the worker and the
    session that stopped it, plus a roster flip to `stopped`.

    Goes through `mutate` (the required read-modify-write path) rather than
    load-then-save, so a sweep appending to the ledger can't clobber a concurrent
    writer on the same task. `detail` is the ONE thing that differs between the two
    reapers (SessionStart's crash sweep and SessionEnd's exact pass), and it is the
    only record of WHY a worker stopped — so it is a parameter, not a rewrite."""
    def _apply(t):
        add_ledger(t, "stop", worker_sid=worker_sid, actor_sid=actor_sid,
                   detail=detail)
        if worker_sid in (t.get("session_meta") or {}):
            register_worker_session(t, worker_sid, status="stopped")
        t["updated_ts"] = _now()
    g("mutate")(task_id, _apply)


def cmd_sweep_orphans(a):
    """`task-station sweep-orphans --session <sid>` — the SessionStart hook entry point.

    Prints one line per reap to STDERR (stdout carries the hook's JSON and must stay
    clean) and exits 0 no matter what: a sweep must never slow down or fail a session
    start."""
    try:
        reaped = sweep_orphan_workers(current_sid=getattr(a, "session", None))
    except Exception:
        return
    for seq, sid in reaped:
        sys.stderr.write("[task-station] reaped orphaned worker %s (task #%s) — its "
                         "spawning hub session is gone\n" % (sid[:8], seq))


# ---- SessionEnd: the EXACT end-of-session pass -------------------------------
#
# THIS PAIR AMENDS DECISION 36's W2. That decision was taken when SessionEnd could
# not be relied on, so the ONLY end-of-session machinery was the SessionStart orphan
# sweep above: notice at the START of the NEXT session that a hub is gone, and clean
# up then. SessionEnd verifiably exists (its contract re-verified against the live
# Claude Code docs on 2026-08-12), so the pair is now:
#
#   * SessionEnd — the EXACT path, this module's `session-end`. The session is ending
#     cleanly, right now, and it knows its own id: stamp its roster row with the
#     reason, put one line on the task's feed, and stop the workers IT spawned.
#   * The SessionStart sweep — the CRASH BACKSTOP, UNTOUCHED. SessionEnd is NOT
#     guaranteed to fire on a crash or a kill, so deleting the sweep because "the end
#     handles it now" would drop the only case the sweep was ever built for.
#
# The two are idempotent against each other by construction: the sweep only considers
# a worker whose spawner is NOT in the live set, and a worker this pass already reaped
# is gone from `claude agents --json` before the sweep ever looks at it.
#
# THE BUDGET IS THE OTHER CONSTRAINT. All SessionEnd hooks SHARE a 1.5-second budget
# (raised to 10s by the manifest's per-hook `timeout`, which is a ceiling and not an
# allowance). So the cheap store work runs unconditionally, and a subprocess is spent
# ONLY when the delegate registry says this session actually spawned a worker.

class _BoundedAgentsAdapter:
    """`claude agents --json` under SESSION_END_AGENTS_TIMEOUT, shaped exactly like
    `harness.ClaudeAdapter.agents_index` so `reap_task_workers` can't tell them apart.
    `{}` on ANY failure — absence of evidence, never evidence of death."""

    def agents_index(self, cwd=None):
        cmd = ["claude", "agents", "--json"]
        if cwd:
            cmd += ["--cwd", cwd]
        try:
            r = g("subprocess").run(cmd, capture_output=True, text=True,
                                    timeout=SESSION_END_AGENTS_TIMEOUT)
            rows = json.loads(r.stdout or "[]")
        except Exception:
            return {}
        return {row["sessionId"]: row for row in rows
                if isinstance(row, dict) and row.get("sessionId")}


def _end_reason(raw):
    """The session-end reason, normalised for storage.

    The documented set is `clear|resume|logout|prompt_input_exit|
    bypass_permissions_disabled|other`, but an UNKNOWN value is kept verbatim rather
    than folded into "other": this field's whole job is to say why a session ended,
    and mapping a newer build's new reason onto "other" would destroy the one fact
    worth recording. Whitespace-collapsed and length-capped so a garbage value can
    never bloat the record."""
    text = " ".join(str(raw or "").split())[:SESSION_END_REASON_MAX]
    return text or "other"


def _mark_session_ended(task_id, session, reason):
    """Stamp the session's roster row ENDED and put one `session-end` event on the
    task's feed. True when this call did it, False when the row was already stamped.

    ADDITIVE FIELDS ONLY (`ended_ts`, `end_reason`) on the existing `session_meta`
    row — the store format is frozen-additive, and every reader of that row keeps
    working unchanged.

    `updated_ts` is deliberately NOT bumped. A session ending is not work on the task:
    bumping it would reorder the board and reset every staleness window each time
    someone typed /clear, which would make "recently updated" mean "recently closed a
    terminal"."""
    state = {"marked": False}

    def _apply(t):
        state["marked"] = False           # a retry after a write conflict starts clean
        meta = t.setdefault("session_meta", {})
        # An attached session with no roster row is possible (a link written before
        # the roster existed). Recording the end on a bare row beats inventing a role.
        row = meta.setdefault(session, {})
        if row.get("ended_ts"):
            return                        # already stamped → idempotent no-op
        row["ended_ts"] = _now()
        row["end_reason"] = reason
        add_event(t, "session-end", "session %s ended (%s)" % (session[:8], reason),
                  session)
        state["marked"] = True

    g("mutate")(task_id, _apply)
    return state["marked"]


def _own_workers(session):
    """`{seq: [worker_sid, …]}` for the delegate workers THIS session spawned.

    Every gate wants POSITIVE evidence, exactly like `_orphaned_workers`: a registry
    entry with no recorded `spawner`, or one whose recorded `name` isn't task-station
    shaped, is skipped rather than guessed at. Empty whenever anything is unknown."""
    out = {}
    dg = g("_delegate_module")()
    if dg is None:
        return out
    try:
        reg = dg.load_reg()
    except Exception:
        return out
    if not isinstance(reg, dict):
        return out
    for e in reg.values():
        if not isinstance(e, dict):
            continue
        sid, seq = e.get("session_id"), e.get("seq")
        if not sid or seq is None or sid == session:
            continue                      # never the ending session itself
        if e.get("spawner") != session:
            continue                      # someone else's worker is never ours to stop
        if not dg._is_ts_worker_name(e.get("name")):
            continue                      # unidentifiable → left alone, as the sweep does
        out.setdefault(seq, []).append(sid)
    return out


def reap_own_workers(session, adapter=None, reason="other"):
    """Stop the workers THIS session spawned, as it ends. Returns the `(seq, sid)`
    pairs actually reaped.

    The kill is `delegate.reap_task_workers` — the SAME airtight predicate the close
    path and the crash sweep use (registry-registered for that seq AND role==worker in
    the roster AND task-station-named AND not busy AND not this session), narrowed by
    `only_sids` to the workers this session spawned. So a worker that is mid-turn is
    left running, and a hub session is never touched.

    Inherits the `reap_workers_on_done` kill switch for free (the reap itself is gated
    on it). Wholly best-effort: never raises."""
    reaped = []
    if not session:
        return reaped
    cands = _own_workers(session)
    if not cands:
        return reaped                     # nothing spawned → not one subprocess spent
    dg = g("_delegate_module")()
    if dg is None:
        return reaped
    if adapter is None:
        if os.environ.get("TASK_STATION_NO_AGENT_QUERY"):
            return reaped                 # the test/headless escape: never shell out
        adapter = _BoundedAgentsAdapter()
    for seq, sids in cands.items():
        try:
            task = resolve_ref(str(seq))
            if not task:
                continue
            got = dg.reap_task_workers(seq, adapter=adapter,
                                       roster=task.get("session_meta") or {},
                                       current_sid=session, only_sids=sids)
        except Exception:
            continue                      # a failed reap never aborts the others
        for s in (got or []):
            reaped.append((seq, s))
            try:
                _record_orphan_reap(
                    task["id"], s, session,
                    detail="reaped on session end (%s): its spawning session ended"
                           % reason)
            except Exception:
                pass                      # the worker IS stopped; logging is best-effort
    return reaped


def cmd_session_end(a):
    """`task-station session-end --session <sid> --reason <r>` — the SessionEnd hook
    entry point, and the exact half of the pair documented above.

    ONE pass, in cost order: stamp the roster row + feed (store-local, microseconds),
    then reap this session's own workers (a subprocess, and only when the registry
    says there is one). IDEMPOTENT — running it twice stamps once and appends one
    event — and it ALWAYS exits 0: SessionEnd cannot block, and a reaper that failed
    a session teardown would be worse than one that did nothing.

    Reaps and failures go to STDERR (which SessionEnd shows to the user only) so
    nothing this prints can be mistaken for hook output."""
    session = getattr(a, "session", None)
    if not session or session == "unknown":
        return
    reason = _end_reason(getattr(a, "reason", None))
    try:
        task = _session_task(session)     # None for unattached AND skipped sessions
    except Exception:
        task = None
    if task:
        try:
            _mark_session_ended(task["id"], session, reason)
        except Exception:
            pass                          # a record we could not write < a broken teardown
    try:
        reaped = g("reap_own_workers")(session, reason=reason)
    except Exception:
        reaped = []
    for seq, sid in reaped:
        sys.stderr.write("[task-station] stopped worker %s (task #%s) — its spawning "
                         "session ended (%s)\n" % (sid[:8], seq, reason))


def _session_task(session):
    """The task attached to `session`, or None (skipped/unattached both read None)."""
    link = get_link(session) if session else None
    if not link or link == SKIP_SENTINEL:
        return None
    return load_task(link)


def _prompt_ts(ts):
    """Epoch-seconds → a compact local 'YYYY-MM-DD HH:MM' for the prompt trail
    (prompts store ts as epoch seconds). '?' when absent/unparseable so a row with
    no timestamp still renders."""
    if ts is None:
        return "?"
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError, OverflowError):
        return "?"


def _prompt_session_tag(p):
    """The per-prompt session attribution: `hub <sid>` / `worker:<label> <sid>` /
    `<role> <sid>`, mirroring the board's session-role label. '<sid>' is the short
    (8-char) session id; a missing sid collapses to '—'."""
    role = p.get("role") or "unknown"
    if p.get("label"):
        role = "%s:%s" % (role, p["label"])
    return "%s %s" % (role, p.get("sid") or "—")


def _assistant_text(msg):
    """The concatenated text of an assistant transcript message — the `text` blocks of
    a list `content`, or a plain-string content. Tool-use blocks (no text) are skipped.
    '' when there's no text (a pure tool-use turn)."""
    if not isinstance(msg, dict):
        return ""
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _last_bullet_reply(text):
    """Claude's reply excerpt for the prompt view: the ENTIRE response tail from the
    LAST bullet line (`-`/`*`/`•`/`1.`) to the end — not just that single line. When the
    response has no bullets, the last non-empty paragraph (everything after the last
    blank line). The leading bullet marker and inline bold/code emphasis are stripped,
    intra-line whitespace collapsed; line breaks are KEPT (the board renders them; the
    terminal/markdown views flatten). '' when the response is empty."""
    raw = [ln.rstrip() for ln in (text or "").splitlines()]
    while raw and not raw[-1].strip():
        raw.pop()
    if not any(ln.strip() for ln in raw):
        return ""
    start = None
    for i, ln in enumerate(raw):
        if _BULLET_RE.match(ln):
            start = i
    if start is None:                       # no bullets → the last paragraph
        start = 0
        for i, ln in enumerate(raw):
            if not ln.strip():
                start = i + 1
    out = []
    for ln in raw[start:]:
        if not ln.strip():
            continue
        ln = _BULLET_RE.sub("", ln)         # only raw[start] can carry a marker
        ln = re.sub(r"\*\*|__|`", "", ln)   # drop md bold/code emphasis
        out.append(" ".join(ln.split()))
    return "\n".join(out)


def _prompt_replies_all(path):
    """{prompt_uuid: last-Claude-bullet} for EVERY human prompt in the transcript at
    `path` — the whole map, from one pass. `_prompt_replies` filters it down.

    Cached whole rather than per requested uuid-set, because a board render asks the
    same transcript for different subsets of its prompts (once per task that session
    touched) and the PARSE, not the filtering, is the expense. Whole-map is also
    exactly equivalent: a reply is bounded by its own turn — `last_text` resets at
    every real user line — so a prompt's reply never depends on which prompts were
    asked for.

    Best-effort: a read that fails partway returns what it had, as the per-call
    version did. An unchanged file fails the same way on a retry, so that partial
    result is cacheable."""
    replies = {}
    cur = None        # uuid of the human prompt whose reply we're collecting (or None)
    last_text = ""    # the last assistant text block seen in the current turn
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"type"' not in line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                t = o.get("type")
                if t == "user":
                    if o.get("isSidechain"):
                        continue
                    content = (o.get("message") or {}).get("content")
                    # a tool_result user line is a continuation, not a new human turn
                    if isinstance(content, list) and any(
                            isinstance(b, dict) and b.get("type") == "tool_result"
                            for b in content):
                        continue
                    if cur is not None and last_text:
                        replies[cur] = _last_bullet_reply(last_text)
                    u = o.get("uuid")
                    cur = u if u else None      # a line with no uuid can own no reply
                    last_text = ""
                elif t == "assistant" and cur is not None:
                    # `cur` is now set for EVERY prompt, not just requested ones, so
                    # this runs on assistant lines the per-call version skipped. A
                    # non-dict `message` (malformed transcript) would have raised
                    # straight out of a Stop hook; treat it as no text instead.
                    msg = o.get("message")
                    txt = _assistant_text(msg) if isinstance(msg, dict) else ""
                    if txt.strip():
                        last_text = txt
        if cur is not None and last_text:
            replies[cur] = _last_bullet_reply(last_text)
    except OSError:
        return replies
    return replies


def _prompt_replies(session_id, uuids):
    """{prompt_uuid: last-Claude-bullet} for the given user-prompt uuids, read from the
    session's transcript. For each prompt, the reply is the LAST assistant text block in
    the turn that FOLLOWS it (before the next real user turn) — tool-result/sidechain
    lines are skipped so a tool round-trip doesn't split the turn. Best-effort: '' /
    missing entries when the transcript is unavailable or the turn had no text.

    The transcript is parsed at most once per (file, version) per process — see the
    transcript-cache note above _session_msgcount. Only the requested uuids come back,
    so the returned dict is identical to what the uncached per-call parse produced."""
    want = {u for u in uuids if u}
    if not session_id or not want:
        return {}
    path = g("_find_session_path")(session_id)
    if not path:
        return {}
    key = _stat_key(path)
    if key is None:                             # unstat-able → no key, parse as before
        return {u: r for u, r in g("_prompt_replies_all")(path).items() if u in want}
    memkey = (path, key[0], key[1])
    full = _REPLIES_MEM.get(memkey)             # `{}` is a valid hit, hence `is None`
    if full is None:
        full = _mem_put(_REPLIES_MEM, memkey, g("_prompt_replies_all")(path),
                        g("REPLIES_CACHE_MAX"))
    return {u: r for u, r in full.items() if u in want}


def _human_prompts_with_replies(prompts):
    """Filter `prompts` to genuine human-typed ones (drops slash-commands, compaction
    summaries, and hook/managed wrappers via _prompt_is_human) and attach each one's
    last-Claude-bullet `reply`, read from the transcripts (one read per session). Returns
    the filtered rows (each a copy with a `reply` key), oldest-first order preserved."""
    human = [p for p in prompts if _prompt_is_human(p.get("kind"), p.get("text"))]
    by_session = {}
    for p in human:
        by_session.setdefault(p.get("session_id"), []).append(p.get("uuid"))
    replies = {}
    for sid, uuids in by_session.items():
        replies.update(_prompt_replies(sid, uuids))
    out = []
    for p in human:
        q = dict(p)
        q["reply"] = replies.get(p.get("uuid"), "")
        out.append(q)
    return out
