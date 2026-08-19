"""The memo domain: correspondence records, acks/dispositions, formatters and peer feeds."""
from board import _shared
from board._shared import *          # constants + utilities (explicit __all__)
from board.state import *
from board.model import *
import hashlib
import json
import os
import loop as _loop

import channel as _channel
import decisions as _dec
import heal as _heal
import paths

g, set_g = _shared.g, _shared.set_g

__all__ = [
    "report_to_parent",
    "_memo_src_label", "_memo_pending_for", "MEMO_QUIET_AFTER",
    "_memo_dispositioned_by", "memo_settled", "memo_pending", "_trim_memos",
    "memo_corrections", "memo_send",
    "memo_ack_disposition", "memo_ack", "_memo_by_prefix",
    "mark_seen",
    "_memo_ledger", "_memo_line", "_memo_detail_lines",
    "_format_memo_list", "_format_memo_full",
    "_all_peer_feeds", "_feed_rev", "_resolve_peer_ref", "add_link",
    "_feed_task", "_subscription_memo_text", "_subs_throttled",
]


# -- memo correspondence -------------------------------------------------------
# A memo hands a fact/decision to a task's working session(s) without copy-paste.
# The record lives in task["memos"]; a capped preview rides the event feed (its id
# SHARED with the memo). Each session explicitly acks on a shared, visible ledger so
# multiple sessions on one task never double-implement. A memo is NOT a decision;
# acking MAY optionally promote it to one.

def _memo_src_label(from_sid, from_task):
    """Compact 'who sent it' tag for a memo preview: '9e01f2·#41' | '9e01f2' | 'desktop'.
    `from_sid` is a session id or a free-text Desktop/MCP signature (display [:6]);
    `from_task` is the sender's attached task id, rendered as its #seq when it loads."""
    who = (from_sid[:6] if from_sid else "desktop")
    if from_task:
        ft = load_task(from_task)
        tag = "#%s" % (ft.get("seq", from_task[:6]) if ft else from_task[:6])
        return "%s·%s" % (who, tag)
    return who


def _memo_pending_for(memo, session):
    """A memo is pending for session S iff S is set, S didn't send it, and S hasn't
    acked it (ack-gated; ignores any seen_ts high-water mark)."""
    if not session or session == memo.get("from_sid"):
        return False
    return session not in {a.get("sid") for a in (memo.get("acks") or [])}


# -- settled memos: when the ROOM has answered, one session's silence isn't news ----
#
# `_memo_pending_for` is per-session and unbounded, and that is right for the ledger: a
# memo this session never acked IS unacked by this session, forever. It is wrong for the
# per-prompt NAG. A long-lived task collects memos faster than any one session acks them,
# so a session that opens on day nine inherits every memo it personally never saw — a real
# session opened to 22 of them, every single one already dispositioned by peer hubs. A nag
# that lists two dozen items nobody needs to act on is not a signal, and the cost is that
# the one memo that DOES need this session is buried in it.
#
# So the nag (and ONLY the nag) drops memos the room has already settled. Settled is not
# "old" and not "seen" — it is a durable-store fact or a quorum:
#
#   (a) ANY session acked it with a DECISION or MEMORY disposition. Those two are the
#       dispositions that say a durable store was updated, and a durable store is shared:
#       once the fact is in the decision log or a memory note, a second session
#       re-integrating it produces the double-implement the ack ledger exists to prevent.
#   (b) `after` DISTINCT sessions have dispositioned it at all — noop included. One noop
#       is one session's judgement call; three independent ones are the room's.
#
# A RETRO-filled disposition counts in both limbs. It is a later reconcile pass's
# reasonable guess rather than the acking session's own answer (which is why every ledger
# surface tags it "(retro)"), but the question here is only whether ANOTHER session still
# needs to be nagged — and the cost of being wrong is one memo that stays fully visible in
# `memo show`, in the detail view, in every count, and remains ackable. Quieting hides a
# memo from ONE nag; it never removes it from anything.
#
# A BARE ack — no disposition at all — deliberately counts toward NEITHER limb. It records
# only that a session saw the memo, which is the exact shape the disposition requirement
# was added to stop treating as an integration. `heal --dispose-acks` is how a bare ack
# becomes a settling one.

MEMO_QUIET_AFTER = 3      # distinct dispositioning sessions that settle a memo (limb b)

_MEMO_DURABLE_KINDS = ("decision", "memory")     # limb (a): a durable store took it


def _memo_dispositioned_by(memo):
    """`(sessions, kinds)` for a memo's ack ledger: the DISTINCT session ids that acked it
    with a disposition, and the set of disposition kinds recorded. Acks with no sid, or
    with no disposition (or a disposition carrying no `kind`), are skipped — see the note
    above on why a bare ack settles nothing."""
    sessions, kinds = set(), set()
    for a in ((memo or {}).get("acks") or []):
        sid = a.get("sid")
        kind = (a.get("disposition") or {}).get("kind")
        if not sid or not kind:
            continue
        sessions.add(sid)
        kinds.add(kind)
    return sessions, kinds


def memo_settled(memo, after=MEMO_QUIET_AFTER):
    """True when the room has dealt with this memo: a decision/memory disposition from ANY
    session, or dispositions from >= `after` DISTINCT sessions (any kind, noop included).

    Pure and side-effect-free — it reads the memo's ack ledger exactly as stored, so it
    holds for a legacy memo (no acks → False) and for a retro-dispositioned one alike. A
    non-positive/unparseable `after` falls back to MEMO_QUIET_AFTER: a zero would settle
    every memo on sight, which is the "quiets everything" footgun the positive-only config
    contract refuses everywhere else."""
    try:
        after = int(after)
    except (TypeError, ValueError):
        after = MEMO_QUIET_AFTER
    if after <= 0:
        after = MEMO_QUIET_AFTER
    sessions, kinds = _memo_dispositioned_by(memo)
    if kinds & set(_MEMO_DURABLE_KINDS):
        return True
    return len(sessions) >= after


def _memo_quiet_settings():
    """`(enabled, after)` for the nag's quieting, read fresh from `config` (a file read,
    which tests repoint and a tuning change lands in without a restart).

    A raising/absent config falls back to the SHIPPED DEFAULTS — quieting ON at
    MEMO_QUIET_AFTER — rather than to the unquieted list. This is the deliberate opposite
    of the prompt rail's usual fail-open, because "open" here means MORE noise, not less:
    the failure mode being fixed is a nag nobody reads. Nothing is lost either way — a
    quieted memo is still in `memo show`, still in the detail view, still ackable."""
    try:
        import config as _cfg
        return bool(_cfg.memo_quiet_enabled()), _cfg.memo_quiet_after()
    except Exception:
        return True, MEMO_QUIET_AFTER


def memo_pending(task, session, quiet=False):
    """Memos on `task` still awaiting `session`'s ack, oldest-first. Empty for a task
    with no memos feed (back-compat) or for the sender's own session.

    `quiet=True` ADDITIONALLY drops memos the room has settled (see `memo_settled`), and
    is the awaiting-your-ack NAG's view — nothing else passes it. Every other caller
    (`memo show`, the detail view's "Memos:" section, `memo ack`, the trim, every count)
    keeps seeing the full per-session pending list, unchanged. The quieting is opt-in at
    the call site on purpose: this must never become a silent global change to what
    "pending" means, because "pending for me" is the ledger's own claim."""
    pending = [m for m in (task.get("memos") or []) if _memo_pending_for(m, session)]
    if not quiet:
        return pending
    enabled, after = _memo_quiet_settings()
    if not enabled:
        return pending
    return [m for m in pending if not memo_settled(m, after)]


def _trim_memos(task):
    """Bound task["memos"]. Beyond MEMOS_KEEP, drop the oldest memos that are pending
    for NO session registered on the task (an unacked memo is otherwise never lost).
    Past MEMOS_HARD_CAP, drop oldest unconditionally. Does NOT save."""
    memos = task.get("memos") or []
    sessions = task.get("sessions") or []

    def fully_acked(m):
        if not sessions:            # nobody registered to ack yet — never soft-trim
            return False
        return not any(_memo_pending_for(m, s) for s in sessions)

    i = 0
    while len(memos) > MEMOS_KEEP and i < len(memos):
        if fully_acked(memos[i]):
            del memos[i]
        else:
            i += 1
    if len(memos) > MEMOS_HARD_CAP:
        del memos[: len(memos) - MEMOS_HARD_CAP]


def memo_corrections(memo):
    """The `--corrects` targets a memo declares, as a list of strings. Empty for an
    ordinary memo and for every memo written before the field existed (back-compat)."""
    if not isinstance(memo, dict):
        return []
    return [str(c) for c in (memo.get("corrects") or []) if str(c).strip()]


def memo_send(task, text, from_sid=None, corrects=None):
    """Post correspondence onto `task`. Creates task["memos"] on first send. Records a
    memo dict + a capped 'memo' preview event whose id is SHARED with the memo, then
    trims. Returns the memo dict. Does NOT save — the caller persists.

    `corrects` declares what this memo REPLACES — a memory-note slug, `decision:<n>` on
    the target task, or another memo's id8. A memo that declares corrections cannot be
    acked without a disposition that engages them (see memo_ack_disposition): the
    incident behind this was a correction acked as a receipt and never integrated, so
    the durable layer kept saying the opposite. Omitted entirely when empty, so an
    ordinary memo's record is shaped exactly as before."""
    text = (text or "").strip()
    from_task = (get_link(from_sid) if from_sid else None)
    memo = {"id": None, "ts": _now(), "from_sid": from_sid, "from_task": from_task,
            "text": text, "acks": []}
    targets = [str(c).strip() for c in (corrects or []) if str(c).strip()]
    if targets:
        memo["corrects"] = targets
    # The preview event's id IS the memo id — one identity across the feed + the record.
    ev = add_event(task, "memo", "", from_sid)
    memo["id"] = ev["id"]
    src = _memo_src_label(from_sid, from_task)
    preview = " ".join(memo["text"].split())
    ev["text"] = ("memo %s from %s: %s" % (ev["id"][:8], src, preview))[:EVENT_TEXT_MAX]
    task.setdefault("memos", []).append(memo)
    _trim_memos(task)
    # THE MEMO IS NOW DELIVERED, not merely recorded. A running session is reached at its
    # next turn boundary through the control channel; a task nobody is working on queues
    # nothing and behaves exactly as it always did. Fail-open and silent by construction
    # (`channel.on_memo` swallows everything): this is a pure mutator that several
    # best-effort paths call, and a memo that could not be delivered must still be a memo.
    _channel.on_memo(task, memo)
    return memo


def memo_ack_disposition(decision=None, memory=None, noop=None):
    """Resolve the ack disposition from the three mutually-exclusive flags.

    Returns `(disposition, error)` where disposition is `{"kind","value"}` and exactly
    one of the two is None. This is the M1 gate: a BARE ack — the shape that let a
    correction be acknowledged without ever reaching the durable layer — is now an
    error naming all three options. `--noop` is a legitimate answer, but it must be
    said out loud and with a reason, which is the whole point: it removes the
    incentive to batch-ack, since each memo now needs its own disposition."""
    given = [k for k, v in (("decision", decision is not None),
                            ("memory", memory is not None),
                            ("noop", noop is not None)) if v]
    if not given:
        return None, "memo ack: " + MEMO_DISPOSITION_HELP
    if len(given) > 1:
        return None, ("memo ack: pass exactly ONE disposition, got %s — an ack records "
                      "one thing done with the memo." % ", ".join("--" + g for g in given))
    kind = given[0]
    if kind == "memory":
        slug = str(memory).strip()
        if not slug:
            return None, "memo ack: --memory needs the slug of the note it was folded into"
        return {"kind": "memory", "value": slug}, None
    if kind == "noop":
        reason = str(noop).strip()
        if not reason:
            return None, ("memo ack: --noop requires a reason — say WHY no durable change "
                          "is needed, or use --decision/--memory instead")
        return {"kind": "noop", "value": reason}, None
    # --decision may be a bare flag (const True → promote the memo body verbatim) or TEXT.
    return {"kind": "decision",
            "value": (decision if isinstance(decision, str) else "")}, None


def memo_ack(task, memo, session, promote=False, decision_text=None, disposition=None):
    """Record `session`'s ack on the memo's shared ledger. Idempotent — a duplicate sid
    returns "already" with no second entry and no second event. A fresh ack appends
    {sid, ts} and posts a 'memo-ack' event. `promote` (from `--decision [TEXT]`)
    promotes the memo to a decision on a fresh ack, using `decision_text` or the memo
    body, attributed to the acking session. Returns "acked" | "already". No save.

    `disposition` ({"kind","value"} from memo_ack_disposition) is recorded ON the ack
    entry, so the ledger shows not just WHO acked but what each of them DID with it.
    Enforcement of "exactly one disposition" lives at the CLI boundary (cmd_memo), not
    here: this stays a permissive mutator so the MCP/Desktop ack surface and internal
    callers keep working."""
    acks = memo.setdefault("acks", [])
    if any(a.get("sid") == session for a in acks):
        return "already"
    entry = {"sid": session, "ts": _now()}
    if disposition:
        entry["disposition"] = dict(disposition)
    acks.append(entry)
    add_event(task, "memo-ack",
              "memo %s acked by %s%s" % ((memo.get("id") or "")[:8], (session or "?")[:8],
                                         (" (%s)" % disposition["kind"]) if disposition else ""),
              session)
    if promote:
        append_decision(task, (decision_text or "").strip() or memo.get("text", ""), session)
    return "acked"


def _memo_by_prefix(task, prefix):
    """Resolve an id-prefix to a single memo on `task`. Returns (memo, None) on a unique
    match; (None, error-line) when the id is missing, matches nothing, or is ambiguous
    (the error lists the candidate id8s)."""
    prefix = (prefix or "").strip()
    if not prefix:
        return None, "memo: an --id (or id prefix) is required"
    hits = [m for m in (task.get("memos") or []) if (m.get("id") or "").startswith(prefix)]
    if not hits:
        return None, "memo: no memo matching id %r on this task" % prefix
    if len(hits) > 1:
        return None, ("memo: id %r is ambiguous — candidates: %s"
                      % (prefix, ", ".join(m["id"][:8] for m in hits)))
    return hits[0], None


def mark_seen(task, session):
    """Advance this session's delta high-water mark to now so already-surfaced
    events never re-inject. Creates a minimal session_meta entry for an unknown
    sid rather than KeyError-ing. Does NOT save — the caller persists."""
    if not session:
        return
    task.setdefault("session_meta", {}).setdefault(session, {})["seen_ts"] = _now()


def _memo_ledger(memo):
    """The inline ack roster for a memo: 'ab12cd34, 9e01f2aa' | '(none yet)'. An ack
    carrying a disposition renders it inline ('ab12cd34→memory'), so the ledger shows
    what each session DID with the memo, not merely that it saw it.

    A RETRO-filled disposition reads 'ab12cd34→noop (retro)'. That tag is the whole
    point: a disposition a later reconcile pass filled in is a reasonable guess about a
    session that no longer exists, and it must never read as the acking session's own
    answer."""
    out = []
    for a in (memo.get("acks") or []):
        if not a.get("sid"):
            continue
        disp = a.get("disposition") or {}
        kind = disp.get("kind")
        if not kind:
            out.append(a["sid"][:8])
            continue
        out.append("%s→%s%s" % (a["sid"][:8], kind,
                                " (retro)" if _heal.is_retro(disp) else ""))
    return ", ".join(out) if out else "(none yet)"


def _memo_line(memo, viewer=None, width=100):
    """One rendered memo row: [⚑ unacked by you ·] id8 · age · from · preview · ledger.
    The ⚑ flag shows only when the memo is still pending for `viewer`. A memo that
    declares `--corrects` carries a CORRECTS tag so its weight is visible in the roster
    without opening it."""
    body = " ".join((memo.get("text") or "").split())
    if len(body) > width:
        body = body[:width - 1].rstrip() + "…"
    src = _memo_src_label(memo.get("from_sid"), memo.get("from_task"))
    flag = "⚑ unacked by you · " if (viewer and _memo_pending_for(memo, viewer)) else ""
    corrects = memo_corrections(memo)
    tag = (" [CORRECTS: %s]" % ", ".join(corrects)) if corrects else ""
    return ('  • %s%s (%s, from %s)%s "%s" — acked by: %s'
            % (flag, memo["id"][:8], rel_time(memo.get("ts")), src, tag, body,
               _memo_ledger(memo)))


def _memo_detail_lines(task, viewer):
    """The default-detail "Memos:" section: everything still unacked-by-viewer first
    (flagged), then the last few already-handled memos. Empty on a task with no memos."""
    memos = task.get("memos") or []
    if not memos:
        return []
    pending = [m for m in memos if _memo_pending_for(m, viewer)]
    handled = [m for m in memos if not _memo_pending_for(m, viewer)]
    out = ["", "Memos:"]
    for m in pending:
        out.append(_memo_line(m, viewer))
    for m in handled[-3:]:
        out.append(_memo_line(m, viewer))
    return out


def _format_memo_list(task, viewer=None):
    """`memo show` (no --id): the memo roster for one task, newest-last."""
    memos = task.get("memos") or []
    if not memos:
        return "(no memos)"
    seq = task.get("seq", task["id"][:8])
    out = ["Memos on task #%s [%s] (%d):" % (seq, task["id"][:8], len(memos))]
    for m in memos:
        out.append(_memo_line(m, viewer))
    return "\n".join(out)


def _format_memo_full(task, memo):
    """`memo show --id <prefix>`: the FULL, uncapped memo body + its ack ledger. A memo
    declaring `--corrects` shows its targets PROMINENTLY, above the body — the reader
    must see what this replaces before deciding what to do about it. The
    correction-language backstop fires here too, so a memo that reads like a correction
    but declares nothing still tells the reader to go check the durable stores."""
    src = _memo_src_label(memo.get("from_sid"), memo.get("from_task"))
    out = ["Memo %s on task #%s [%s]"
           % (memo["id"][:8], task.get("seq", task["id"][:8]), task["id"][:8]),
           "From:    %s (%s)" % (src, rel_time(memo.get("ts")))]
    corrects = memo_corrections(memo)
    if corrects:
        out.append("CORRECTS: %s" % ", ".join(corrects))
        out.append("         ↳ acking this REQUIRES a disposition that engages the "
                   "target (--decision / --memory / --noop \"<reason>\").")
    elif correction_language(memo.get("text")):
        out.append("⚠ reads like a correction but declares no --corrects target — if it "
                   "changes anything durable, update that store as part of acking.")
    out.append("Acked:   %s" % _memo_ledger(memo))
    # RETRO-filled dispositions get their provenance spelled out here — who filled it in,
    # when, and why — next to the ack's OWN unchanged timestamp. A reader must be able to
    # tell a later pass's reasonable guess from what the acking session actually said.
    for a in (memo.get("acks") or []):
        disp = a.get("disposition") or {}
        if not _heal.is_retro(disp):
            continue
        out.append("         ↳ %s→%s is RETRO: filled in by %s %s — %s. The ack itself "
                   "(%s) is unchanged. Recorded: %s"
                   % ((a.get("sid") or "?")[:8], disp.get("kind"),
                      str(disp.get("retro_by") or "?")[:8],
                      rel_time(disp.get("retro_ts")),
                      disp.get("retro_why") or "no reason recorded",
                      rel_time(a.get("ts")), disp.get("value") or "(no value)"))
    out.append("")
    out.append(memo.get("text") or "")
    return "\n".join(out)


def _all_peer_feeds(data_dir=None):
    """Every CANONICAL peer/org feed as a parsed dict (self/archive excluded), feed-file
    order. A non-canonical file parses to None and is skipped. Never raises."""
    import feeds as _feeds
    dd = data_dir or paths.data_dir()
    out = []
    for path in _feeds.peer_feed_files(dd):
        feed = _feeds.parse_feed_file(path)
        if not feed or (feed.get("kind") or "") in ("self", "archive"):
            continue
        out.append(feed)
    return out


def _feed_rev(feed):
    """A peer feed's revision — its own `rev` if present, else a stable content hash over
    its tasks (so subscriptions can diff even feeds that don't stamp one)."""
    if not feed:
        return ""
    r = feed.get("rev")
    if r:
        return str(r)
    try:
        basis = json.dumps(feed.get("tasks") or [], sort_keys=True, default=str)
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]
    except Exception:
        return ""


def _resolve_peer_ref(ref, data_dir=None):
    """Resolve a peer task ref → (feed, ftask) or (None, None). Accepts the handle
    (`jpark-201`), the `<alias>-<uuid8>` form, a bare uuid8, or `<alias>-<uuid8-prefix>`.
    Scans every canonical peer feed; first match wins."""
    ref = (ref or "").strip()
    if not ref:
        return None, None
    for feed in _all_peer_feeds(data_dir):
        alias = feed.get("alias") or feed.get("owner") or ""
        for ftask in feed.get("tasks") or []:
            handle = ftask.get("handle") or ""
            u8 = (ftask.get("uuid8") or "")[:8]
            if ref == handle or (u8 and ref == u8) or (alias and ref == "%s-%s" % (alias, u8)):
                return feed, ftask
            if alias and ref.startswith(alias + "-"):
                suf = ref[len(alias) + 1:]
                if suf and (suf == u8 or (u8 and u8.startswith(suf))
                            or handle.endswith("-" + suf)):
                    return feed, ftask
    return None, None


def add_link(task, alias, uuid8, handle=None, kind="link"):
    """Record a correspondence link to a peer task on `task["links"]` (created on first
    link). Idempotent — a duplicate (alias, uuid8) is a no-op returning False. Does NOT
    save. `kind` distinguishes a manual link from a fork's auto-link."""
    alias = (alias or "").strip()
    uuid8 = (uuid8 or "")[:8]
    if not alias or not uuid8:
        return False
    links = task.setdefault("links", [])
    for l in links:
        if l.get("alias") == alias and (l.get("uuid8") or "")[:8] == uuid8:
            return False
    links.append({"alias": alias, "uuid8": uuid8,
                  "handle": handle or ("%s-%s" % (alias, uuid8)),
                  "kind": kind, "ts": _now()})
    return True


def _feed_task(feed, uuid8):
    """The task dict in `feed` whose uuid8 matches (prefix-tolerant), or None."""
    u8 = (uuid8 or "")[:8]
    for ftask in (feed or {}).get("tasks") or []:
        fu = (ftask.get("uuid8") or "")[:8]
        if fu and (fu == u8 or fu.startswith(u8) or u8.startswith(fu)):
            return ftask
    return None


def _subscription_memo_text(link, feed, ftask):
    """Build the capped memo body for a subscribed peer feed that advanced. Includes only
    the watched kinds (checkpoint/decision/trail). Format: `<handle> new <kind>: …`."""
    handle = link.get("handle") or link.get("alias") or "peer"
    on = (link.get("subscribe") or {}).get("on") or []
    if not ftask:
        return "%s: feed advanced (rev %s)" % (handle, _feed_rev(feed))
    digest = ftask.get("digest") or {}
    parts = []
    if "checkpoint" in on and (digest.get("state") or "").strip():
        parts.append("checkpoint: %s" % digest["state"].strip())
    if "decision" in on and (digest.get("decisions_tail") or []):
        parts.append("decision: %s" % _dec.text(digest["decisions_tail"][-1]).strip())
    if "trail" in on and (ftask.get("prompts") or []):
        p = ftask["prompts"][-1]
        txt = p if isinstance(p, str) else (p.get("text") or "")
        if txt:
            parts.append("trail: %s" % txt.strip())
    if not parts:
        parts.append("update: %s" % (digest.get("state") or ftask.get("title") or "advanced"))
    return ("%s new %s" % (handle, "; ".join(parts)))[:MEMO_LINE_MAX]


def _subs_throttled():
    """True when a throttled check ran within _SUBS_CHECK_INTERVAL (so the on_stop hook
    path stays cheap). Stamps the run time on a False. Fail-open (never blocks)."""
    p = os.path.join(paths.data_dir(), "subscriptions.last")
    now = _now()
    try:
        with open(p, encoding="utf-8") as f:
            last = float((f.read() or "0").strip() or 0)
    except Exception:
        last = 0
    if now - last < _SUBS_CHECK_INTERVAL:
        return True
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(str(now))
    except Exception:
        pass
    return False

# ------------------------------------------------- the child reports upward ----
#
# GAP: nothing PUSHED. A child could finish, and its parent learned only when somebody
# thought to run a scan. That is the same failure the exit conditions exist to remove —
# a record holding the answer and waiting to be interrogated — and it is the one that
# makes an orchestrator session something you have to babysit.
#
# The channel is the MEMO, not a new mechanism: memos already carry an ack ledger, and
# the prompt rail already surfaces "N memo(s) awaiting YOUR ack" at the top of the
# parent's next turn. So a child reaching a terminal state writes one memo onto its
# parent, and the parent is told the next time it is used. Nothing new to learn, nothing
# new to keep alive, and the notice is a durable record rather than a notification that
# scrolls away.
#
# ONLY TERMINAL STATES, and only on a TRANSITION. A memo per exit-tick would train the
# reader to ignore the rail, which costs more than the signal is worth.

def report_to_parent(task, headline, session=None):
    """Write one memo onto `task`'s parent saying a terminal state was reached. Returns
    the parent's seq (or None when there is no parent, or the send failed).

    FAIL-OPEN AND SILENT. This runs on the tail of `done` and `exit-tick`; a memo that
    cannot be written must never turn a successful close into an error, so every failure
    returns None and the caller says nothing."""
    try:
        pid = _loop.parent_id(task)
        if not pid:
            return None
        parent = load_task(pid)
        if not parent or is_closed(parent):
            return None
        memo_send(parent, "CHILD #%s — %s" % (task.get("seq"), headline),
                  from_sid=session)
        parent["updated_ts"] = _now()
        save_task(parent)
        return parent.get("seq")
    except Exception:              # noqa: BLE001 — a report must never break the verb
        return None

