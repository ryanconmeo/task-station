# channel.py
"""THE CHILD CONTROL CHANNEL — how a parent REACHES a running child.

A parent could already SEE a child: `scan`'s RUNNING column, `sessions`, the board's
live strip. And it could already leave one a note: `memo` writes correspondence onto the
task and the UserPromptSubmit rail surfaces it. Neither of those reaches anything.

WHY A MEMO IS NOT A DELIVERY. The memo nag fires when somebody TYPES. An invoked child
is handed exactly one prompt — the ask — and then works; nobody types into it again. So
a memo sent one minute after the launch waits for a turn that will never come, and the
parent's only remaining option is to sit still until the child stops on its own. Every
mid-flight fact — main moved, the spec changed, stop and hand back what you have — was
therefore undeliverable, which is the same thing as saying the loop had no control
plane at all.

THE TRANSPORT IS THE TURN BOUNDARY. The end of a turn is the one moment a RUNNING
session arrives at by itself, with no human in the loop, and the Stop hook can refuse to
let it pass (`{"decision":"block","reason":…}`). So an order queued for a session is
read at that session's next Stop, and the session cannot finish the turn without reading
it. This is a deliberately modest claim: the channel does not interrupt a turn in
flight, and it does not pretend to. What it guarantees is that delivery no longer
depends on a human, which is the whole of the gap.

WHY IT CANNOT WEDGE. An order blocks at most `ORDER_MAX_BLOCKS` times. Past that the
order stays pending and fully visible (`channel orders`, the settle command, every
count) and simply stops holding the turn hostage — the same anti-wedge rule the edit
gate has had since it existed, for the same reason: a gate that can trap a session is a
gate people turn off.

FIVE THINGS LIVE HERE, and the module is deliberately PURE over the task dict — it
mutates what it is handed and never saves, never writes an event, never loads a task.
The seams that call it persist and narrate. (`exits.py` is the same shape, and for the
same reason: a mover that reached into the seams would bind to one facade generation.)

  1. REACHABILITY (`live`, `live_sids`) — who is running right now, from the harness's
     OWN per-process record.
  2. ORDERS (`order_queue` … `order_settle`) — the queue, its delivery marks, and the
     Stop-gate text. THE PARENT REACHING DOWN.
  3. THE PICKUP RAIL (`pickup_file` … `pickup_take`) — the same transport, pointing the
     other way: THE CHILD REACHING UP. See the section head below.
  4. THE VERBS (`stand_down`, `announce_spec`, `on_memo`) — the three things a parent
     needs to say.
  5. THE PERMISSION BOUNDARY (`record_denial`, `launder_reason`) — the refusal that
     makes the channel safe to have at all.

--------------------------------------------------------------------------------------
LIVENESS COMES FROM PROCESS STATE, JOINED THROUGH THE ROSTER THE LAUNCH WROTE.

Claude Code writes one `~/.claude/sessions/<PID>.json` per running process, and that
file carries two independent pieces of evidence: the pid (probe it with `os.kill(pid,0)`)
and `messagingSocketPath`, the control socket the harness opened for that session. Both
are written by the harness at process start. Neither needs a task-station hook to have
run, and that distinction is the point of this module's fourth property.

The pre-existing answer (`live_sessions.running()`) joins a session to a task through
`store.links`. A link is written by an ATTACH — `/todo <n>`, `create`, `attach`, or
`invoke`'s pre-bind. So the link-joined view is blind to any running session that has
not been attached yet, and a child that has been launched but has not reached its hooks
is precisely the child a parent most needs to reach: it is the one sitting on a first-run
dialog, or the one three seconds old, or the one whose link a later `detach` cleared.

So the join here runs the other way: the TASK's own roster (`session_meta`, which
`invoke` writes BEFORE the child process exists) names the sessions that belong to it,
and process state says which of those are up. The link store is kept as a SECOND source,
never the only one — a session that walked in and attached itself is still found, because
adding a source must never remove one. Every row says which one found it (`via`).

RUNNING AND REACHABLE ARE DIFFERENT, and both are reported. `running` means the process
exists. `reachable` means it exists AND its control socket is there — i.e. the harness
still has a channel to it. Orders are queued to everything RUNNING, because the Stop
hook needs no socket; `reachable` is reported so a caller can tell a live session from
one whose socket has gone.

--------------------------------------------------------------------------------------
THE PERMISSION BOUNDARY, AND WHY IT IS THE LOAD-BEARING PART.

Permission boundaries in Claude Code are PER-SESSION, and a control channel is exactly
where that breaks. The moment a parent can send work to a child, a session denied an
action has an obvious workaround — ask the child — and the natural failure mode of the
whole loop is that privilege flows to whoever is least constrained. This is not
hypothetical: on 2026-08-16 a child session's kill of seven runaway agents was refused by
the permission classifier, it recognised that routing the kill through a peer would be
LAUNDERING, and it stopped and asked the human instead. That was the right call, and it
must not depend on each session making it.

So the rule is enforced HERE, at the channel, and not in the receiver's conscience:

    A SESSION THAT WAS DENIED AN ACTION MAY NOT ASK A PEER TO PERFORM IT.

Note what the rule is NOT. It is not "a restricted session may not order a wider one" —
that would refuse the review loop, where a plan-mode reviewer's entire job is to hand
findings to an implementer that can act on them. A reviewer was never DENIED an edit; it
was never granted one, by design. The trigger is a REFUSAL that actually happened.

WHERE THE REFUSALS COME FROM. A denial is recorded (`record_denial`) and thereafter
enforced forever. Two producers: task-station's own PreToolUse guard, which knows what
it blocked; and an explicit `channel deny`, which is how a session makes durable a
refusal task-station cannot observe — the harness's permission classifier says no to the
session, not to us. Self-reporting sounds weak until you notice what it buys: the record
is scoped to the SESSION *and* to its TASK, so one honest report binds every later
session on that task, including the one that would have forgotten. It is a ratchet, not
a cage.

MATCHING IS DELIBERATELY BROAD, AND THE ASYMMETRY IS THE REASON. A denied action matches
an order when its normalized text appears in the order, or when every token of it does
(so reordering and rewording do not get through). A false refusal costs one printed line
naming the denial, after which the human does it themselves — which is what a refused
action was always going to require. A false pass costs a laundered privilege, silently.
Those are not the same size of mistake, so the match errs toward refusing.
"""
import json
import os
import re
import time
import uuid

import live_sessions
import paths
import store as store_mod

# The order kinds. A closed list: each one has its own Stop-gate wording and its own
# settle contract, and a kind nobody has classified would inherit neither.
ORDER_MEMO, ORDER_STAND_DOWN, ORDER_SPEC = "memo", "stand-down", "spec"
ORDER_KINDS = (ORDER_MEMO, ORDER_STAND_DOWN, ORDER_SPEC)

# WHICH ORDERS MAY HOLD A TURN — and the answer is NOT "all of them".
#
# Measured while the loop was driven by hand: the Stop gate fires at EVERY turn end, so an
# unsettled order costs a round trip every single time. Holding an orchestrator's turn
# hostage for "your child closed" — a notice the system generated about another task's
# lifecycle, already durable on the memo ledger, already on the next prompt's rail — costs
# more than it delivers. Reserve the blocking rail for what actually changes what the
# receiver should be doing.
#
# THE DISCRIMINATOR IS AUTHORSHIP, NOT KIND, and that distinction is the whole of the fix.
# "stop rebasing, main moved" and "your child closed" are both memos, and they are not the
# same message: the first is a person telling THIS session a fact it cannot get any other
# way — nobody types into an invoked child again, which is why the channel exists at all —
# and the second is bookkeeping the loop emitted on its own. So a memo written by a session
# still blocks, and a memo minted by a lifecycle hook rides the ledger.
#
# A ROUTINE ORDER IS NOT UNDELIVERED. It stays in `orders_for`, `channel orders` lists it,
# the Stop gate marks it delivered and settles it, and the memo itself is on the task's own
# feed where every reader meets it. What it no longer does is stop a turn from ending.
ROUTINE_FIELD = "routine"

# The kinds that hold a turn WHOEVER wrote them. A stand-down says stop; a spec change says
# the target moved and DONE here is computed from that target. Neither is bookkeeping.
ALWAYS_BLOCKING = (ORDER_STAND_DOWN, ORDER_SPEC)

# How many turn-ends one unsettled order may hold. Past this it stays pending and
# visible but stops blocking — see the anti-wedge note in the module docstring.
ORDER_MAX_BLOCKS = 3

ORDERS_KEEP = 100          # bounded like every other per-task feed
ORDER_TEXT_MAX = 1200      # an order is a request; past this it is context

# The kinds whose settle MUST hand something back. A stand-down that needs nothing back
# discards whatever the child had not written down yet, which is the thing it exists to
# recover.
REPORT_REQUIRED = (ORDER_STAND_DOWN,)

DENIALS_FILE = "channel-denials.json"

# The addressee index: `{session_id: [task_id, …]}` for every session an order has
# ever been queued for. It exists to keep the Stop hook CHEAP. The gate has to answer
# "does this session have orders?" on every single turn end, and the only other way to
# answer it for a session with no link is to read every task in the store — on every
# turn, for every unattached session, on a machine that may never use the channel at
# all. With the index, an unused channel costs one absent-file stat.
#
# It is a CACHE, never the truth: entries are self-healing (a task with no pending
# order for that sid simply is not a hit), and the caller keeps its full scan as the
# correctness backstop — gated on this index being non-empty, so the scan can only
# ever cost anything on a machine that is actually using the channel.
ORDER_INDEX_FILE = "channel-orders.json"
ORDER_INDEX_SIDS = 500          # FIFO cap; eviction loses the fast path, never an order

_TOKEN_RE = re.compile(r"-?[a-z0-9][\w./-]*")


# ------------------------------------------------------------------ reachability ----

def pid_alive(pid):
    """Delegates to `live_sessions.pid_alive` — the canonical `os.kill(pid, 0)` probe.

    A one-line wrapper rather than a straight import so a test can fake liveness by
    patching EITHER module and get the same answer. Faking it is not optional: a test
    that needed real running processes would have to spawn them."""
    return live_sessions.pid_alive(pid)


def sessions_dir():
    """The harness's per-process session dir — `live_sessions`' resolution, unchanged
    (TASK_STATION_SESSIONS_DIR → CLAUDE_CONFIG_DIR/sessions → ~/.claude/sessions)."""
    return live_sessions.sessions_dir()


def _read_session_file(path):
    try:
        with open(path, encoding="utf-8") as f:
            obj = json.load(f)
    except (OSError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _socket_of(rec):
    """The session's control socket path when the harness recorded one AND it is still
    there, else None. A recorded path whose socket has gone means the process is up but
    nothing can reach it — which is a different fact from being dead, and is reported as
    one."""
    p = rec.get("messagingSocketPath")
    if not p or not isinstance(p, str):
        return None
    try:
        return p if os.path.exists(p) else None
    except OSError:
        return None


def process_rows():
    """Every RUNNING Claude Code process, as rows built only from evidence the harness
    and the OS provide:

        {session_id, pid, cwd, socket, running, reachable, status, updated_ts}

    Dead/stale files are skipped and NEVER deleted (a crash's leftovers are somebody
    else's to clean). Fail-open on an unreadable dir: an empty list degrades the channel
    to "nobody reported running", which is the same shape every other liveness caller
    in this codebase already tolerates."""
    d = sessions_dir()
    try:
        names = os.listdir(d)
    except OSError:
        return []
    rows = []
    for name in sorted(names):
        if not name.endswith(".json"):
            continue
        rec = _read_session_file(os.path.join(d, name))
        if rec is None:
            continue
        if not pid_alive(rec.get("pid")):
            continue
        sock = _socket_of(rec)
        try:
            pid = int(rec.get("pid"))
        except (TypeError, ValueError):
            continue
        rows.append({
            "session_id": rec.get("sessionId"),
            "pid": pid,
            "cwd": rec.get("cwd"),
            "socket": sock,
            "running": True,
            "reachable": bool(sock),
            "status": rec.get("status"),
            "updated_ts": (live_sessions._parse_ts(rec.get("updatedAt"))
                           or live_sessions._parse_ts(rec.get("startedAt"))),
        })
    return rows


def _backend():
    return store_mod.get_backend(os.path.join(paths.data_dir(), "store"))


def _linked_task_id(sid):
    """The task `sid`'s link points at, or None. Guarded — the link store is the SECOND
    source here, so a store hiccup must cost nothing."""
    if not sid:
        return None
    try:
        return _backend().get_link(sid)
    except Exception:                                   # noqa: BLE001
        return None


def roster(task):
    """The session ids this task itself claims — `session_meta` keys plus the
    append-only `sessions` list.

    THIS IS THE JOIN THAT NEEDS NO HOOK. `invoke` writes the roster entry (via
    `fresh_resume_command`) before the child process exists, so the task knows the sid it
    launched even if nothing else in the system does yet."""
    out = set()
    for sid in (task.get("session_meta") or {}):
        if sid:
            out.add(sid)
    for sid in (task.get("sessions") or []):
        if sid:
            out.add(sid)
    return out


def live(task):
    """This task's sessions that are RUNNING right now, most-recently-active first.

    Each row is a `process_rows()` row plus `via`: `roster` when the TASK named the
    session (the hook-independent join), `link` when only the link store did. Rows are
    deduplicated by session id, roster first — a session both sources agree on reads as
    `roster`, because that is the source that did not need a hook."""
    if not task:
        return []
    mine = roster(task)
    tid = task.get("id")
    rows, seen = [], set()
    for r in process_rows():
        sid = r.get("session_id")
        if not sid or sid in seen:
            continue
        if sid in mine:
            via = "roster"
        elif tid and _linked_task_id(sid) == tid:
            via = "link"
        else:
            continue
        seen.add(sid)
        row = dict(r)
        row["via"] = via
        rows.append(row)
    rows.sort(key=lambda r: r.get("updated_ts") or 0, reverse=True)
    return rows


def live_sids(task, exclude=None):
    """The set of session ids running on `task`, minus `exclude` (a sid or an iterable).

    Orders are queued to everything RUNNING rather than to everything REACHABLE: the
    transport is the Stop hook, which needs no socket. `live()` still reports the socket
    so a caller can say WHY it could or could not reach something."""
    skip = set()
    if isinstance(exclude, str):
        skip = {exclude}
    elif exclude:
        skip = {s for s in exclude if s}
    return {r["session_id"] for r in live(task) if r["session_id"] not in skip}


# ------------------------------------------------------------------------ orders ----

def orders(task):
    """Every order recorded on `task`, oldest first. Empty for a task written before the
    field existed (back-compat: the field is created on first queue)."""
    return list((task or {}).get("orders") or [])


def _trim(task):
    o = task.setdefault("orders", [])
    if len(o) > ORDERS_KEEP:
        # Drop the oldest SETTLED orders first; an unsettled order is never lost to a
        # trim, for the same reason an unacked memo is not.
        keep = [x for x in o if not x.get("settled_ts")]
        settled = [x for x in o if x.get("settled_ts")]
        room = max(0, ORDERS_KEEP - len(keep))
        task["orders"] = sorted(keep + settled[-room:], key=lambda x: x.get("ts") or 0)


def order_index_path():
    """`<data_dir>/channel-orders.json` — the addressee index (see the note by
    ORDER_INDEX_FILE for why it is a cache and not the truth)."""
    return os.path.join(paths.data_dir(), ORDER_INDEX_FILE)


def _load_index():
    try:
        with open(order_index_path(), encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def index_add(sid, task_id):
    """Record that `sid` has been sent an order on `task_id`. Best-effort and silent — a
    write that fails costs the fast path, and the caller's scan still finds the order."""
    if not sid or not task_id:
        return
    try:
        doc = _load_index()
        ids = [t for t in (doc.get(sid) or []) if isinstance(t, str)]
        if task_id not in ids:
            ids.append(task_id)
        doc[sid] = ids
        if len(doc) > ORDER_INDEX_SIDS:
            for k in list(doc)[: len(doc) - ORDER_INDEX_SIDS]:
                doc.pop(k, None)
        path = order_index_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp.%d" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except OSError:
        pass


def indexed_tasks(sid):
    """Task ids that have ever carried an order for `sid`, newest last."""
    return [t for t in (_load_index().get(sid) or []) if isinstance(t, str)]


def index_active():
    """True when ANY order has ever been queued on this machine.

    The one thing a Stop hook on an unrelated machine should have to pay for the whole
    channel: a single stat of an absent file."""
    try:
        return os.path.getsize(order_index_path()) > 2
    except OSError:
        return False


def order_queue(task, kind, text, to_sid, from_sid=None, from_task=None,
                routine=False):
    """Queue one order on `task` for session `to_sid`. Returns `(order, error)` — exactly
    one of the two is None. Mutates `task`; does NOT save.

    Refuses, with a reason naming the denial, when carrying it would perform an action
    the REQUESTING session was denied (see `launder_reason`). That check runs FIRST,
    before anything is written: a refusal that had already queued the order would be a
    log entry, not a boundary."""
    kind = str(kind or "").strip()
    if kind not in ORDER_KINDS:
        return None, ("channel: %r is not an order kind. They are %s."
                      % (kind, ", ".join(ORDER_KINDS)))
    text = " ".join(str(text or "").split())
    if not text:
        return None, "channel: an order needs text — the one thing the child must hear."
    if not to_sid:
        return None, "channel: an order is addressed to a session; none was named."
    if to_sid == from_sid:
        return None, "channel: a session cannot order itself."
    reason = launder_reason(text, from_sid=from_sid, from_task=from_task)
    if reason:
        return None, reason
    order = {"id": uuid.uuid4().hex, "ts": time.time(), "kind": kind,
             "text": text[:ORDER_TEXT_MAX], "to_sid": to_sid, "from_sid": from_sid,
             "from_task": from_task, "delivered_ts": None, "blocks": 0,
             "settled_ts": None, "settled_by": None, "report": None}
    # Written only when true, so an ordinary order's record is shaped exactly as before
    # and an older reader ignores the key.
    if routine:
        order[ROUTINE_FIELD] = True
    task.setdefault("orders", []).append(order)
    _trim(task)
    index_add(to_sid, task.get("id"))
    return order, None


def orders_for(task, sid, pending_only=True):
    """Orders addressed to `sid`, oldest first. `pending_only` (the default) drops the
    settled ones — the pending list is what every surface means by "waiting"."""
    out = [o for o in orders(task) if o.get("to_sid") == sid]
    if pending_only:
        out = [o for o in out if not o.get("settled_ts")]
    return out


def deliverable(task, sid):
    """The pending orders for `sid` that the Stop gate may still BLOCK on.

    An order past `ORDER_MAX_BLOCKS` is deliberately absent from this list and present
    in `orders_for` — it is still waiting, it has just stopped being allowed to hold the
    turn. So is a ROUTINE order (see `blocks_turn`): a lifecycle notice rides the memo
    ledger, and `notices` is where it shows up."""
    return [o for o in orders_for(task, sid)
            if blocks_turn(o) and int(o.get("blocks") or 0) < ORDER_MAX_BLOCKS]


def blocks_turn(order):
    """May this order hold a turn end?

    An ALWAYS_BLOCKING kind may, whoever wrote it. Anything else may only if a session
    deliberately wrote it — a routine lifecycle notice may not."""
    if order.get("kind") in ALWAYS_BLOCKING:
        return True
    return not order.get(ROUTINE_FIELD)


def notices(task, sid):
    """The pending orders for `sid` that ride the MEMO LEDGER instead of blocking.

    The other half of `deliverable`: everything waiting for this session that is not
    allowed to hold its turn. The Stop gate settles these rather than blocking on them,
    because the fact they carry is already durable somewhere the reader will meet it."""
    return [o for o in orders_for(task, sid) if not blocks_turn(o)]


def mark_delivered(task, orders_):     # noqa: A002 — shadowing is local and obvious
    """Stamp first delivery and count this block against each order. Mutates; no save.

    `delivered_ts` records the FIRST time the order actually reached a session, which is
    what makes "delivered" a fact rather than an intention. It is never advanced on a
    re-block: the second block is the same delivery, insisted upon."""
    now = time.time()
    for o in orders_:
        if not o.get("delivered_ts"):
            o["delivered_ts"] = now
        o["blocks"] = int(o.get("blocks") or 0) + 1
    return orders_


def order_by_prefix(task, prefix):
    """Resolve an id-prefix to one order on `task`. `(order, None)` on a unique match;
    `(None, error-line)` when it is missing, matches nothing, or is ambiguous — the same
    contract `_memo_by_prefix` has, so a reader who has learned one has learned both."""
    prefix = str(prefix or "").strip()
    if not prefix:
        return None, "channel: an --id (or id prefix) is required"
    hits = [o for o in orders(task) if (o.get("id") or "").startswith(prefix)]
    if not hits:
        return None, "channel: no order matching id %r on this task" % prefix
    if len(hits) > 1:
        return None, ("channel: id %r is ambiguous — candidates: %s"
                      % (prefix, ", ".join(o["id"][:8] for o in hits)))
    return hits[0], None


def order_settle(order, sid, report=None):
    """Settle one order. Returns `(status, error)` — `("settled", None)`,
    `("already", None)`, or `(None, error-line)`.

    A `stand-down` CANNOT be settled without a report, and that refusal is the whole
    second exit condition: standing a child down without getting back what it wrote
    throws away everything it had not yet committed to the record. Every other kind
    settles bare — a memo read is a memo read."""
    if order.get("settled_ts"):
        return "already", None
    if order.get("kind") in REPORT_REQUIRED and not str(report or "").strip():
        return None, ("channel settle: a %s needs --report '<what you wrote>' — standing "
                      "a session down without getting back what it has is how the work "
                      "is lost." % order.get("kind"))
    order["settled_ts"] = time.time()
    order["settled_by"] = sid
    if str(report or "").strip():
        order["report"] = " ".join(str(report).split())
    return "settled", None


_KIND_HEAD = {
    ORDER_MEMO: "MEMO",
    ORDER_STAND_DOWN: "STAND-DOWN",
    ORDER_SPEC: "SPEC CHANGED",
}


def settle_command(task, order, sid):
    """The exact one-liner that settles this order. Printed with every order everywhere,
    because an instruction whose next step has to be looked up is an instruction that
    gets guessed at."""
    ref = task.get("seq") or (task.get("id") or "")[:8]
    tail = " --report '<what you wrote>'" if order.get("kind") in REPORT_REQUIRED else ""
    return ("task-station channel settle --task %s --id %s --session %s%s"
            % (ref, (order.get("id") or "")[:8], sid, tail))


def order_line(task, order, sid, indent="  "):
    """One order as the gate and the `orders` view both render it."""
    head = _KIND_HEAD.get(order.get("kind"), (order.get("kind") or "?").upper())
    src = order.get("from_sid")
    who = (" from %s" % src[:8]) if src else ""
    return ("%s%s  %s%s\n%s    %s\n%s    settle: %s"
            % (indent, (order.get("id") or "")[:8], head, who,
               indent, order.get("text") or "",
               indent, settle_command(task, order, sid)))


def block_reason(task, orders_, sid):
    """The Stop-hook `reason` for a session with orders waiting — the text the child
    actually reads. Names the count, lists each order with its settle command, and says
    plainly that the turn will not end until they are settled."""
    ref = task.get("seq") or (task.get("id") or "")[:8]
    n = len(orders_)
    lines = [
        "[task-station] The control channel has %d order(s) for this session on task "
        "#%s. This turn does not end until each is settled — an order is how a parent "
        "reaches a session that is already running, so it is not optional context."
        % (n, ref),
    ]
    for o in orders_:
        lines.append(order_line(task, o, sid))
    lines.append("  Settle each one above. `task-station channel orders --task %s` "
                 "lists them again." % ref)
    return "\n".join(lines)


# ------------------------------------------------------------------ the pickup rail ----
#
# THE CHANNEL POINTING THE OTHER WAY. Everything above is a PARENT reaching DOWN to a
# child that is already running. This is a CHILD reaching UP to a parent that is already
# running, and it is the same rail for the same reason: the end of a turn is the one
# moment a live session arrives at by itself, with no human in the loop, and the Stop
# hook can refuse to let it pass.
#
# WHAT WAS ACTUALLY BROKEN. A child that finished wrote its report as a memo and the
# lifecycle minted a notice on the parent — and BOTH of those wait for somebody to type.
# `memo_pending_brief` and `child_reports_brief` ride UserPromptSubmit and SessionStart;
# an orchestrator driving a loop starts no new session and is typed into by nobody. So
# the parent's only remaining move was to poll `sessions --task <child>`, which answers a
# different question — is a process up — and answers it "busy" for a child that finished
# an hour ago and left its window open. MEASURED TWICE: #532 sat about an hour, #536 sat
# seven. Nothing was broken either time. There was simply no signal.
#
# WHY THIS IS NOT AN ORDER. An order is addressed to a SESSION (`to_sid`) and is queued
# only to sessions that are running RIGHT NOW — `_fanout` over `live_sids`. A child
# usually finishes when the parent is between sessions, mid-compaction, or momentarily
# unreadable, and an order queued to nobody is a fact that was never recorded at all. A
# pickup is addressed to the TASK. It is filed whether or not anything is up, it survives
# the parent's session ending and being resumed, and any session that later attaches to
# that parent meets it. That is the whole of "build the durable record and the gate".
#
# IT BLOCKS, AND THAT REVERSES A PRIOR RULING. `ROUTINE_FIELD` above says a lifecycle
# notice must not hold a turn, and that ruling stands for what it was written about:
# subscription diffs, close mirrors, correspondence the reader will meet on the ledger
# anyway. A child handing work back is not that. It is the one fact that changes what the
# parent should do next — stop waiting, start gating — and it is the fact the parent
# provably cannot get any other way while it is running. The routine memo still rides the
# ledger, unchanged; the pickup rides the gate.
#
# IT CANNOT WEDGE, by the same rule everything else here obeys: `PICKUP_MAX_BLOCKS`
# turn-ends, after which the row stays pending and fully visible (`pickup list`, every
# count, the SessionStart nag) and simply stops holding the turn. A NEW fact about the
# same child — a different headline, i.e. "conditions met" becoming "CLOSED" — re-arms
# the count, because that is a second thing the parent has not heard and not a repeat of
# the first.
#
# IT RETIRES ITSELF — BUT NOT ON "CLOSED", AND THAT DISTINCTION IS THE WHOLE RAIL. The
# obvious auto-retirement is "the child is closed, so there is nothing to pick up", and it
# is wrong in the exact case this exists for: `done` is what a finished child RUNS, so the
# commonest hand-back headline is literally "CLOSED — ready for the gate". A rail that
# retired on that would file a notice and cancel it before anyone read it — every stall it
# was built to stop, restored, with a mechanism in place claiming otherwise. (Caught by the
# end-to-end probe in tests/e2e_pickup_rail.sh, which is why that probe drives the CLI from
# outside instead of asserting on the same functions the unit suite already holds.)
#
# What "dealt with" actually means is that the PARENT ENGAGED — it graded the work, or it
# parked the child, or a session said so with `pickup take`. Those are the three, and each
# is recorded (`how`) rather than inferred, because "the parent picked this up" and "the
# child was parked and never came back" are different histories and a later reader auditing
# a silent loop needs to tell them apart. The seam does the reconciliation, since it is the
# half that may load a task.

PICKUPS_FIELD = "pickups"
PICKUPS_KEEP = 100             # bounded like every other per-task feed
PICKUP_MAX_BLOCKS = 3          # the anti-wedge cap, same value and same reason as orders
PICKUP_HEADLINE_MAX = 300      # a headline is a pointer to the report, not the report

# How a pickup retired. Recorded rather than inferred: "the parent picked this up" and
# "the child closed, so there was nothing left to pick up" are different histories, and a
# later reader auditing a silent loop needs to tell them apart.
PICKUP_TAKEN = "taken"         # a session ran `pickup take`
PICKUP_GRADED = "graded"       # the parent graded the work — the engagement itself
PICKUP_PARKED = "parked"       # the child is parked — never handed back to the loop
PICKUP_HOWS = (PICKUP_TAKEN, PICKUP_GRADED, PICKUP_PARKED)


def pickups(task):
    """Every pickup filed on `task`, oldest first. Empty for a task written before the
    field existed — the field is created on first file, exactly like `orders`."""
    return list((task or {}).get(PICKUPS_FIELD) or [])


def pickups_pending(task):
    """The pickups nobody has taken. What every surface means by "waiting"."""
    return [p for p in pickups(task) if not p.get("taken_ts")]


def pickups_blocking(task):
    """The pending pickups the Stop gate may still BLOCK on.

    A row past `PICKUP_MAX_BLOCKS` is deliberately absent here and present in
    `pickups_pending` — it is still waiting, it has just stopped being allowed to hold
    the turn. Same split as `deliverable` vs `orders_for`, so a reader who has learned
    one has learned both."""
    return [p for p in pickups_pending(task)
            if int(p.get("blocks") or 0) < PICKUP_MAX_BLOCKS]


def _trim_pickups(task):
    rows = task.setdefault(PICKUPS_FIELD, [])
    if len(rows) > PICKUPS_KEEP:
        keep = [p for p in rows if not p.get("taken_ts")]
        taken = [p for p in rows if p.get("taken_ts")]
        room = max(0, PICKUPS_KEEP - len(keep))
        task[PICKUPS_FIELD] = sorted(keep + taken[-room:], key=lambda p: p.get("ts") or 0)


def open_pickup_for(task, child_id):
    """The one UNTAKEN pickup on `task` for child `child_id`, or None.

    One open row per child, always. A child reports more than once — `exit-tick` when its
    conditions go green, `done` when it closes — and two rows for one child is two nags
    for one thing to do, which is how a rail earns being ignored."""
    for p in reversed(pickups(task)):
        if p.get("child_id") == child_id and not p.get("taken_ts"):
            return p
    return None


def pickup_file(parent, child, headline, memo_id=None, from_sid=None, ts=None):
    """File one pickup on `parent` saying `child` handed work back. Returns
    `(row, created)` — `created` is False when an open row for this child was UPDATED
    instead. Mutates `parent`; does NOT save. Never raises on ordinary input.

    `(None, False)` when there is nothing to file: no parent, no child, no headline.

    RE-FILING WITH A NEW HEADLINE RE-ARMS THE BLOCK COUNT and re-filing with the same one
    does not. The cap exists so a notice cannot trap a session, not so a parent can miss
    a second, different fact by having ignored the first."""
    if not isinstance(parent, dict) or not isinstance(child, dict):
        return None, False
    headline = " ".join(str(headline or "").split())[:PICKUP_HEADLINE_MAX]
    child_id = child.get("id")
    if not headline or not child_id:
        return None, False
    now = float(ts) if ts is not None else time.time()
    row = open_pickup_for(parent, child_id)
    if row is not None:
        if row.get("headline") != headline:
            row["headline"] = headline
            row["ts"] = now
            row["blocks"] = 0              # a NEW fact gets its own delivery budget
            row["delivered_ts"] = None
            if memo_id:
                row["memo_id"] = memo_id
        return row, False
    row = {"id": uuid.uuid4().hex, "ts": now,
           "child_id": child_id, "child_seq": child.get("seq"),
           "child_title": child.get("title"), "headline": headline,
           "memo_id": memo_id, "from_sid": from_sid,
           "delivered_ts": None, "blocks": 0,
           "taken_ts": None, "taken_by": None, "how": None}
    parent.setdefault(PICKUPS_FIELD, []).append(row)
    _trim_pickups(parent)
    return row, True


def pickup_by_prefix(task, prefix):
    """Resolve an id-prefix to one pickup on `task`. Same contract as
    `order_by_prefix` — `(row, None)` on a unique match, `(None, error-line)` otherwise."""
    prefix = str(prefix or "").strip()
    if not prefix:
        return None, "pickup: an --id (or id prefix) is required"
    hits = [p for p in pickups(task) if (p.get("id") or "").startswith(prefix)]
    if not hits:
        return None, "pickup: no pickup matching id %r on this task" % prefix
    if len(hits) > 1:
        return None, ("pickup: id %r is ambiguous — candidates: %s"
                      % (prefix, ", ".join(p["id"][:8] for p in hits)))
    return hits[0], None


def pickup_mark_delivered(task, rows):
    """Stamp first delivery and count this block against each pickup. Mutates; no save.

    `delivered_ts` records the FIRST time the notice actually reached a session, never
    advanced on a re-block — the second block is the same delivery, insisted upon. Same
    rule as `mark_delivered`, and deliberately the same words."""
    now = time.time()
    for p in rows:
        if not p.get("delivered_ts"):
            p["delivered_ts"] = now
        p["blocks"] = int(p.get("blocks") or 0) + 1
    return rows


def pickup_take(task, row, sid=None, how=PICKUP_TAKEN):
    """Retire one pickup. Returns `(status, error)` — `("taken", None)`,
    `("already", None)`, or `(None, error-line)`. Mutates; does NOT save.

    No report is required and that is deliberate: unlike a stand-down, nothing is lost by
    taking one. The child's own account is already durable on the child's memo ledger —
    the pickup is a POINTER to it, and `pickup_line` prints the command that reads it."""
    if not isinstance(row, dict):
        return None, "pickup: nothing to take."
    if how not in PICKUP_HOWS:
        return None, ("pickup: %r is not a disposition. They are %s."
                      % (how, ", ".join(PICKUP_HOWS)))
    if row.get("taken_ts"):
        return "already", None
    row["taken_ts"] = time.time()
    row["taken_by"] = sid
    row["how"] = how
    _ack_pickup_memo(task, row, sid, how)
    return "taken", None


def _ack_pickup_memo(task, row, sid, how):
    """Write the pickup's disposition onto the MEMO it points at.

    THE VERB THAT COMPLETES WRITES THE DISPOSITION. The rail already worked — every
    pickup on this programme's own record shows `delivered_ts` and `taken_ts` within
    about twenty seconds, so the parent genuinely was forced to look. What it never did
    was tell the OTHER ledger: a taken pickup wrote `task["pickups"]` and left the memo
    it was a pointer to sitting unacked, forever. Two ledgers describing one event, and
    neither able to close the other.

    Deliberately best-effort. A memo this cannot find must never fail the take — the
    pickup is the load-bearing half and an ack is bookkeeping. Imported locally because
    `memos` already imports this module, and a cycle here would break both."""
    mid = (row or {}).get("memo_id")
    if not mid:
        return
    try:
        import memos as _m
        for memo in (task.get("memos") or []):
            if memo.get("id") == mid:
                _m.memo_ack(task, memo, sid or "rail",
                            disposition={"kind": how, "by": "pickup"})
                return
    except Exception:              # noqa: BLE001 — bookkeeping must not break the rail
        return


def pickup_command(task, row):
    """The exact one-liner that takes this pickup. Printed with every pickup everywhere,
    for the same reason `settle_command` is: an instruction whose next step has to be
    looked up is an instruction that gets guessed at."""
    ref = task.get("seq") or (task.get("id") or "")[:8]
    return ("task-station pickup take --task %s --id %s"
            % (ref, (row.get("id") or "")[:8]))


def pickup_read_command(row):
    """How to read the child's actual report — the memo it filed on its OWN task.

    A notice that says a report exists without saying how to read it is half a rail, and
    the memo is on the CHILD's ledger, which nothing on the parent's surfaces shows."""
    ref = row.get("child_seq") or (row.get("child_id") or "")[:8]
    mid = (row.get("memo_id") or "")[:8]
    if mid:
        return "task-station memo show --task %s --id %s" % (ref, mid)
    return "task-station memo list --task %s" % ref


def pickup_line(task, row, indent="  "):
    """One pickup as the gate and the `pickup list` view both render it."""
    seq = row.get("child_seq")
    who = ("#%s" % seq) if seq is not None else (row.get("child_id") or "?")[:8]
    title = row.get("child_title") or ""
    head = "%s%s  CHILD %s%s" % (indent, (row.get("id") or "")[:8], who,
                                 (" — %s" % title) if title else "")
    return ("%s\n%s    %s\n%s    read:   %s\n%s    take:   %s"
            % (head, indent, row.get("headline") or "",
               indent, pickup_read_command(row),
               indent, pickup_command(task, row)))


def pickup_reason(task, rows):
    """The Stop-hook `reason` for a parent with children waiting to be picked up — the
    text the orchestrator actually reads.

    IT NAMES THE NEXT MOVE AND THE RAILS THAT ALREADY EXIST. `turn` is the command that
    gates and grades what came back; `ListAgents`/`SendMessage` are the harness's own
    liveness and reach, which is what a parent wants next if the child is still up. This
    rail deliberately builds neither — it delivers the fact that makes reaching for them
    worth doing."""
    ref = task.get("seq") or (task.get("id") or "")[:8]
    n = len(rows)
    lines = [
        "[task-station] %d CHILD(REN) OF #%s HAVE HANDED WORK BACK and nobody has picked "
        "it up. This turn does not end until each is taken. A child's report lands on the "
        "CHILD's task and its session may still be sitting there idle, so neither this "
        "task's ledger nor `sessions --task <child>` would tell you — polling liveness "
        "reads a finished child as busy, which is exactly how one sat for seven hours."
        % (n, ref),
    ]
    for p in rows:
        lines.append(pickup_line(task, p))
    lines.append("  Gate and grade what came back:  task-station turn --task %s" % ref)
    lines.append("  Still need the child's session? `ListAgents` says what is live and "
                 "`SendMessage` reaches it — neither needs this rail, and this rail does "
                 "not duplicate them.")
    lines.append("  `task-station pickup list --task %s` lists these again." % ref)
    return "\n".join(lines)


# ------------------------------------------------------------------- the verbs ----

def _fanout(task, kind, text, from_sid=None, from_task=None, routine=False):
    """Queue one order of `kind` for every RUNNING session on `task` except the sender.
    Returns `(orders, error)`; a laundering refusal stops the whole fan-out, because the
    boundary is about the REQUEST, not about who happens to be listening."""
    targets = sorted(live_sids(task, exclude=from_sid))
    if not targets:
        return [], None
    out = []
    for sid in targets:
        order, err = order_queue(task, kind, text, sid, from_sid=from_sid,
                                 from_task=from_task, routine=routine)
        if err:
            return [], err
        out.append(order)
    return out, None


def stand_down(task, why=None, from_sid=None, from_task=None):
    """Order every running session on `task` to wrap up AND HAND BACK WHAT IT WROTE.

    Not a kill. A kill recovers nothing, and the thing a parent actually needs when it
    stands a child down is the child's own account of where it got to — which is why the
    settle requires a report and the report goes back to whoever ordered it."""
    why = " ".join(str(why or "").split())
    text = ("wrap up now and hand back what you wrote: the state you are in, what "
            "landed, and what is unfinished.")
    if why:
        text += " why: %s" % why
    return _fanout(task, ORDER_STAND_DOWN, text, from_sid=from_sid, from_task=from_task)


def announce_spec(task, text, from_sid=None, from_task=None):
    """Tell every running session on `task` that its exit conditions moved.

    This matters because DONE here is COMPUTED from those conditions. A child working to
    the checklist it read at session start will finish work that no longer counts, and
    will do it without ever being told the target moved — the failure is silent on both
    sides, which is why the change is pushed rather than left to be noticed."""
    return _fanout(task, ORDER_SPEC, text, from_sid=from_sid, from_task=from_task)


def on_memo(task, memo):
    """Queue delivery of a freshly-sent memo to every running session on the task.

    FAIL-OPEN AND SILENT, because it runs inside `memo_send`, a pure mutator that many
    best-effort paths call (`report_to_parent`, the subscription sweep). A channel that
    could break a memo would be worse than one that occasionally fails to deliver it —
    the memo itself is already durably recorded either way. Returns the orders queued,
    which is `[]` when nobody is running, when the only live session is the sender, or
    when anything at all went wrong."""
    try:
        text = " ".join(str(memo.get("text") or "").split())
        if not text:
            return []
        mid = (memo.get("id") or "")[:8]
        body = "memo %s: %s" % (mid, text) if mid else "memo: %s" % text
        got, err = _fanout(task, ORDER_MEMO, body, from_sid=memo.get("from_sid"),
                           from_task=memo.get("from_task"),
                           routine=bool(memo.get(ROUTINE_FIELD)))
        return [] if err else got
    except Exception:                                   # noqa: BLE001
        return []


# --------------------------------------------------------- the permission boundary ----

def denials_path():
    """`<data_dir>/channel-denials.json` — station-scoped, beside `workers.json`.

    Station-scoped rather than per-task on purpose: a denial is a fact about what this
    machine's classifier refused, and the session that was refused may not be attached to
    anything at all."""
    return os.path.join(paths.data_dir(), DENIALS_FILE)


def _load_denials():
    try:
        with open(denials_path(), encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return {"sessions": {}, "tasks": {}}
    if not isinstance(doc, dict):
        return {"sessions": {}, "tasks": {}}
    for k in ("sessions", "tasks"):
        if not isinstance(doc.get(k), dict):
            doc[k] = {}
    return doc


def _save_denials(doc):
    """Atomic write (temp + os.replace), like every other store write here — a
    half-written boundary file would fail OPEN, which is the one direction this file
    must never fail in."""
    path = denials_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp.%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def record_denial(session, action, by=None, task=None):
    """Record that `session` was DENIED `action`, and thereafter refuse to carry it.

    Recorded against the SESSION and, when given, against its TASK. The task scope is
    what makes this structural rather than a matter of one session's discipline: the next
    session on that task inherits the refusal without having to rediscover it. Returns
    the stored entry (idempotent — the same action twice is one entry)."""
    action = " ".join(str(action or "").split())
    if not action:
        return None
    entry = {"action": action, "by": (str(by).strip() if by else None),
             "ts": time.time()}
    doc = _load_denials()
    for scope, key in (("sessions", session), ("tasks", task)):
        if not key:
            continue
        bucket = doc[scope].setdefault(str(key), [])
        if not any(d.get("action") == action for d in bucket if isinstance(d, dict)):
            bucket.append(entry)
    _save_denials(doc)
    return entry


def denials(session=None, task=None):
    """Every denial that binds `session` and/or `task`, deduplicated by action."""
    doc = _load_denials()
    out, seen = [], set()
    for scope, key in (("sessions", session), ("tasks", task)):
        if not key:
            continue
        for d in doc[scope].get(str(key)) or []:
            if not isinstance(d, dict):
                continue
            a = d.get("action")
            if not a or a in seen:
                continue
            seen.add(a)
            out.append(d)
    return out


def clear_denials():
    """Remove the whole ledger. For tests and for a deliberate operator reset — never
    called by any automatic path, because a boundary that expires on its own is not one."""
    try:
        os.unlink(denials_path())
    except OSError:
        pass


def _tokens(s):
    return _TOKEN_RE.findall(str(s or "").lower())


def _matches(action, text):
    """Does `text` ask for `action`? Normalized substring, or every token of the action
    present in the text (which is what stops a reorder or a reword from getting through).

    The token limb needs at least two tokens: a one-token denial ("kill") would otherwise
    refuse every order that used the word, and a record that vague is a bad record rather
    than a broad rule. See the asymmetry note in the module docstring for why the
    remaining error is left pointing at refusal."""
    a_norm = " ".join(_tokens(action))
    t_norm = " ".join(_tokens(text))
    if not a_norm:
        return False
    if a_norm in t_norm:
        return True
    a_toks = _tokens(action)
    if len(a_toks) < 2:
        return False
    return set(a_toks) <= set(_tokens(text))


def launder_reason(text, from_sid=None, from_task=None):
    """The refusal line when carrying `text` would perform something the requesting
    session was denied, else None.

    The message names the denied action, who denied it, and the one thing that is
    actually available — doing it yourself, or asking the human — because a refusal that
    does not say what to do next just gets worked around."""
    for d in denials(session=from_sid, task=from_task):
        if _matches(d.get("action"), text):
            who = d.get("by")
            src = (" by %s" % who) if who else ""
            return ("channel: REFUSED — this request performs an action this session was "
                    "denied%s: %r. A session may not ask a peer to do what it was itself "
                    "refused; that is laundering the permission boundary, and the "
                    "boundary is per-session for a reason. Do it in the session that "
                    "holds the permission, or ask the human." % (src, d.get("action")))
    return None
