# loop.py
"""THE LOOP — the deterministic half of orchestration: wave planning over `depends-on`,
and the mechanics of the graded acceptance gate.

WHAT THE LOOP IS. A parent task (the orchestrator) has child tasks. Each child holds its
own context, does its own work, and reports back. The parent decides what may start now,
runs the mechanical gate over what came back, grades it, and either accepts it —
releasing whatever depended on it — or rejects it with the failed dimension named. It
repeats until nothing is left to release, and it halts at a human gate rather than
retrying one.

THE SPLIT THIS MODULE SITS ON (Q4, decided 2026-08-14 — the house pattern `heal` and
`save` already use: engine scan + model judgment). THE ENGINE OWNS EVERY DETERMINISTIC
PRIMITIVE — wave computation, the exit-condition rollup, the stopping condition, grade
arithmetic and grade recording. THE SKILL OWNS THE JUDGMENT — reading a child's report
and scoring the six rubric dimensions, which no amount of code can do. There is
deliberately no `task-station orchestrate` monolith: a single command that both computed
and judged would have to call a model to answer "what is unblocked", and that question
has an exact answer that costs nothing.

WHY WAVES AND NOT A QUEUE. `depends-on` is already a typed edge in the store (shipped
2.24.0), and nothing had ever computed over it — on one real board an orchestrator had
the title and none of the edges. A wave is just the honest reading of that graph: wave 1 is
everything with no unsettled predecessor, wave N is everything whose predecessors all
finished in earlier waves. It is not a schedule and it does not start anything; it
answers "what could start now", which is the question a parent has to answer before it
can spawn anything.

WHAT "SETTLED" MEANS, and why it is not "closed". A predecessor releases its dependents
when it is CLOSED or when every exit condition it registered is MET. The second half is
the point of the whole exercise: a task whose plan items each carry a runnable command
can prove it is finished without anybody closing it, and — far more importantly — a task
that registered NO conditions can never satisfy the test by accident. `exits.state`
returns `none` for an empty registration, and `none` is not `met`. A green board with
nothing behind it is the failure this design is built against.

THE RUBRIC IS DATA HERE, NOT DOCTRINE. The six dimensions and the grade scale live in
this module so the arithmetic (is B+ above the threshold? is any dimension missing?) is
mechanical and testable; what each dimension MEANS lives in the vault note the skill
reads. The threshold is a config key (`loop_accept_threshold`, default `A-`) and is applied
per-dimension, so an installation can tune it without touching this module.

Stdlib only, and PURE: every function takes the tasks it needs and returns a value.
Nothing here loads the store, spawns a session or prints — the command seam does that,
which is what lets the whole scan be tested against hand-built task dicts.
"""
import contextlib
import json
import os
import time
import uuid

import config as _config
import exits as _exits
import paths

try:                      # the machine-wide build lock's critical section
    import fcntl
except ImportError:       # pragma: no cover — no fcntl (Windows); see _slots_locked
    fcntl = None

# ---------------------------------------------------------------- the rubric ----
#
# Six dimensions, graded per unit of work. The text is the QUESTION each one answers —
# short on purpose. The engine needs only the arithmetic — is this grade above the
# threshold, is any dimension missing — and what each dimension MEANS belongs with
# whoever maintains the rubric, not in two places that can drift apart.

DIMENSIONS = (
    ("G1", "Gate integrity",
     "did verification run FROM OUTSIDE — suite unmodified, fresh clone, live URL?"),
    ("G2", "Measurement fidelity",
     "were the numbers re-derived at execution time, WITH their measuring commands?"),
    ("G3", "Contract preservation",
     "frozen surfaces untouched; behaviours covered by behavioural tests?"),
    ("G4", "Finding capture",
     "deviations ledgered with WHY, corrections folded back same-session?"),
    ("G5", "Scope & ask-gate discipline",
     "settled rulings honored, ask gates hit BEFORE the work, no creep?"),
    ("G6", "Ops efficiency",
     "worker discipline held, wall-clock lost to infra bounded, recovery diagnosed?"),
)
DIMENSION_KEYS = tuple(d[0] for d in DIMENSIONS)
DIMENSION_TITLES = {k: t for k, t, _q in DIMENSIONS}
DIMENSION_QUESTIONS = {k: q for k, _t, q in DIMENSIONS}

# Best to worst. A grade not on this list is REFUSED rather than ranked — an
# unrecognised grade that sorted last would silently reject good work, and one that
# sorted first would silently accept anything.
GRADE_ORDER = ("A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D+", "D", "F")

# The rubric note is written with a UNICODE MINUS (`A−`, U+2212) because it is prose;
# the CLI is typed with a hyphen. Both mean the same grade, and a gate that rejected one
# of them would reject it for a reason having nothing to do with the work.
_MINUS_FORMS = ("−", "–", "—")

# A parked child is one the loop must NOT retry. The taxonomy is deliberately tiny —
# these are the three reasons a retry is the wrong move, and everything else is a
# reject-and-iterate.
# `too-large` is the fourth because the gate is WHERE THE JUDGEMENT ALREADY IS. Somebody
# is looking hard at the work at exactly that moment, which makes it the cheapest place
# to notice that a child is not one session's worth — cheaper than a heuristic on effort
# or step count, which fires on plenty of tasks that are simply detailed. Parking rather
# than rejecting is deliberate: iterating will not help, so the loop must stop asking.
PARK_REASONS = ("human-gate", "blocked-external", "retries-exhausted", "too-large")


def normalize_grade(raw):
    """A typed grade → its canonical form (`a-` → `A-`), or None when it is not a grade.

    None rather than a guess: `verdict` treats an ungradeable dimension as NOT passing,
    so a typo surfaces as "G4 is not a grade" instead of quietly deciding the gate."""
    s = str(raw or "").strip().upper()
    for m in _MINUS_FORMS:
        s = s.replace(m, "-")
    s = s.replace(" ", "")
    return s if s in GRADE_ORDER else None


def grade_rank(raw):
    """Position in `GRADE_ORDER` — LOWER IS BETTER — or None for a non-grade. `A` is 0,
    so `rank(x) <= rank(threshold)` reads as "at least as good as"."""
    g = normalize_grade(raw)
    return GRADE_ORDER.index(g) if g else None


def meets(raw, threshold):
    """True iff grade `raw` is at least as good as `threshold`. An unrecognised grade
    NEVER meets a threshold, and an unrecognised THRESHOLD is treated as the default
    `A-` — a threshold nobody can parse must not become "accept anything"."""
    r = grade_rank(raw)
    t = grade_rank(threshold)
    if t is None:
        t = GRADE_ORDER.index("A-")
    return r is not None and r <= t


def parse_dimensions(pairs):
    """`["G1=A", "g4:B+"]` → `({"G1": "A", "G4": "B+"}, errors)`.

    Accepts `=` or `:` because both get typed, and upper-cases the key so `g1` works.
    Every refusal is COLLECTED rather than raised on the first one: somebody grading six
    dimensions in a single command should see all their typos at once, not one per
    re-run."""
    out, errors = {}, []
    for raw in (pairs or []):
        text = str(raw or "").strip()
        if not text:
            continue
        sep = "=" if "=" in text else (":" if ":" in text else None)
        if not sep:
            errors.append("--dim %r — expected 'G1=A-' (dimension, then the grade)." % raw)
            continue
        key, _, value = text.partition(sep)
        key = key.strip().upper()
        if key not in DIMENSION_KEYS:
            errors.append("--dim %r — %s is not a rubric dimension; they are %s."
                          % (raw, key or "''", ", ".join(DIMENSION_KEYS)))
            continue
        grade = normalize_grade(value)
        if grade is None:
            errors.append("--dim %r — %r is not a grade; they are %s."
                          % (raw, value.strip(), " ".join(GRADE_ORDER)))
            continue
        out[key] = grade
    return out, errors


def verdict(dims, threshold):
    """Score a full set of dimension grades. Returns
    `{"accepted", "threshold", "failed", "missing", "worst"}`.

    TWO WAYS TO NOT PASS, and they are reported separately because they call for
    different actions: `failed` names dimensions graded BELOW the threshold (the child
    iterates on that dimension), `missing` names dimensions nobody graded at all (the
    JUDGE has work left to do). An incomplete grading is never an acceptance — the
    uncountable rule: a dimension with no grade has not been shown to pass."""
    graded = {k: v for k, v in (dims or {}).items() if k in DIMENSION_KEYS}
    missing = [k for k in DIMENSION_KEYS if k not in graded]
    failed = [(k, graded[k]) for k in DIMENSION_KEYS
              if k in graded and not meets(graded[k], threshold)]
    ranks = [grade_rank(v) for v in graded.values() if grade_rank(v) is not None]
    worst = GRADE_ORDER[max(ranks)] if ranks else None
    return {"accepted": not failed and not missing, "threshold": threshold,
            "failed": failed, "missing": missing, "worst": worst}


def verdict_line(v):
    """The one-line reading of a verdict, for a decision entry and for stdout.

    Says WHICH dimension failed and at what grade. "Rejected" on its own tells a child
    nothing it can act on; "REJECTED — G4 B (below A-)" names the work."""
    if v.get("accepted"):
        return "ACCEPTED — every dimension at or above %s (worst: %s)" % (
            v.get("threshold"), v.get("worst") or "n/a")
    parts = []
    if v.get("failed"):
        parts.append("below %s: %s" % (v.get("threshold"),
                                       ", ".join("%s %s" % (k, g) for k, g in v["failed"])))
    if v.get("missing"):
        parts.append("ungraded: %s" % ", ".join(v["missing"]))
    return "REJECTED — " + "; ".join(parts)


# ------------------------------------------------------------- grade recording ----
#
# Grades accumulate ON THE CHILD TASK, so the per-child history is exactly the same
# object as the per-phase table in the rubric note: the rubric is one thing at both
# scales. The ledger is append-only and bounded by nothing, because a task that has been
# graded fifty times is telling you something and truncating it would hide it.

GRADES_FIELD = "grades"


def grades(task):
    """The grade ledger, oldest first. Never raises on a garbled field."""
    raw = (task or {}).get(GRADES_FIELD)
    return [g for g in raw if isinstance(g, dict)] if isinstance(raw, list) else []


def attempts(task):
    """How many times this task has been GRADED (parks excluded).

    Parks are excluded because a park is not an attempt at the work — it is the loop
    declining to retry. Counting one would burn a retry the child never got."""
    return len([g for g in grades(task) if not g.get("park")])


def retries_left(task, maximum):
    """Attempts remaining before the loop must park this child. Never negative."""
    return max(0, int(maximum) - attempts(task))


def parked(task):
    """The park reason on the most recent ledger entry, or None. A PARKED CHILD IS NEVER
    RETRIED — the blocker taxonomy's one hard rule, and the reason `human-gate` is a
    park rather than a very low grade."""
    ledger = grades(task)
    return (ledger[-1].get("park") or None) if ledger else None


def record(task, dims, threshold, note=None, session=None, park=None, now=None,
           handoff=None):
    """Append one grading to the ledger and return `(entry, verdict)`.

    A PARK IS RECORDED WITH ITS REASON AND NO ACCEPTANCE: parking is the loop saying
    "this does not come back to me", which is a different statement from a failing
    grade and must not read as one later. Does NOT save; the caller persists.

    `handoff` is the 1-based index of the session HANDOFF this grading judged
    (lib/succession.py), when it judged one. Purely additive and written only when
    supplied — a grade of ordinary work must not carry a key claiming it graded a relay.
    Without the link a task that relayed three times could not say which verdict belonged
    to which handoff, so the second one would inherit the first one's grade for free."""
    v = verdict(dims or {}, threshold)
    entry = {"ts": time.time() if now is None else now,
             "dims": dict(dims or {}), "threshold": threshold,
             "accepted": bool(v["accepted"]) and not park,
             "verdict": verdict_line(v)}
    if handoff is not None:
        try:
            entry["handoff"] = int(handoff)
        except (TypeError, ValueError):
            pass
    if park:
        entry["park"] = park
        entry["accepted"] = False
    if note:
        entry["note"] = str(note)
    if session:
        entry["session"] = str(session)
    ledger = task.setdefault(GRADES_FIELD, [])
    if not isinstance(ledger, list):
        ledger = []
        task[GRADES_FIELD] = ledger
    ledger.append(entry)
    return entry, v


# ----------------------------------------------------------------- the graph ----

DEPENDS_ON = "depends-on"
PARENT = "parent"


def _rel_ids(task, kind):
    """Ids this task's OWN edges of `kind` point at. Only the stored side is read: the
    subordinate stores the edge, so a dependent holds its own `depends-on` list and a
    child holds its own `parent` — reverse directions are derived by scanning, never
    stored, and reading them here would double-count."""
    out = []
    for r in (task or {}).get("related") or []:
        if isinstance(r, dict) and r.get("kind") == kind and r.get("id"):
            out.append(r["id"])
    return out


def dependencies(task):
    """The ids this task DEPENDS ON — the tasks that must land first."""
    return _rel_ids(task, DEPENDS_ON)


def parent_id(task):
    """This task's parent id, or None. At most one by construction (`--parent`
    replaces)."""
    ids = _rel_ids(task, PARENT)
    return ids[0] if ids else None


def children(task, tasks):
    """Tasks whose stored `parent` edge points at `task`, sorted by seq — the derived
    reverse direction, computed here rather than stored so it can never be stale."""
    tid = (task or {}).get("id")
    kids = [t for t in (tasks or []) if t.get("id") != tid and parent_id(t) == tid]
    return sorted(kids, key=lambda t: t.get("seq") if t.get("seq") is not None else 1 << 30)


def is_closed(task):
    """Closed by status. Kept local so this module stays pure — `state.is_closed` reads
    the same field, and the seam passes tasks in rather than this module loading them."""
    return str((task or {}).get("status") or "").lower() == "closed"


def settled(task):
    """The LEAF rule: this task alone no longer blocks its dependents — it is CLOSED, or
    every exit condition it registered is MET.

    THE EMPTY-REGISTRATION CASE IS THE WHOLE POINT — `exits.satisfied` is False for a
    task with no conditions, so an unfinished task with an empty checklist blocks its
    dependents exactly as it should, instead of releasing them because there was nothing
    to check.

    This rule is not enough on its own once a task has CHILDREN — see `settled_fn`, which
    is what every scan actually gates on."""
    return is_closed(task) or _exits.satisfied(task)


def settled_fn(tasks):
    """A memoized `settled(task)` that accounts for the whole SUBTREE.

    WHY THE LEAF RULE IS NOT ENOUGH, found the first time a track was decomposed. Task
    531 finished its own five steps, retired the three that had become child tasks, and
    immediately read as *satisfied* — every condition it registered was met — while three
    children sat unbuilt. It would have released every dependent wave on the strength of
    work it had handed to somebody else. That is the empty-registration failure again,
    one level up: a parent's own checklist stops being evidence the moment the work moves
    to its children.

    So: CLOSED still wins outright (closing is a human's assertion and it is allowed to
    end an argument), and otherwise a task is settled only when its own conditions are met
    AND every child is settled, recursively. A task with no children is unchanged — the
    `all()` over an empty list is True — so this costs nothing on a flat board.

    A parent cycle (impossible by construction, not by guarantee) returns True for the
    revisited node rather than recursing forever: a hang here takes the whole scan down."""
    kids = {}
    for t in (tasks or []):
        p = parent_id(t)
        if p:
            kids.setdefault(p, []).append(t)
    memo = {}

    def _own_work_done(task):
        """Is THIS task's own checklist finished — ignoring its children?

        Not the same question as `exits.satisfied`, and the difference is the whole
        point: an orchestrator registers no conditions because it HOLDS NO WORK, and
        `satisfied` correctly refuses to call an empty registration met. Here the
        children are what stand in for the evidence, so the own-work test asks only
        that nothing is outstanding: no condition refuted or unrun, and no live step
        left both uncovered and unticked."""
        st = _exits.state(task)
        if st in (_exits.UNMET, _exits.UNKNOWN):
            return False
        return not _exits.coverage(task)["uncovered_open"]

    def _settled(task, guard=frozenset()):
        tid = (task or {}).get("id")
        if tid in memo:
            return memo[tid]
        if tid in guard:
            return True
        if is_closed(task):
            memo[tid] = True
            return True
        children_of = kids.get(tid, ())
        if not children_of:
            # LEAF — unchanged: an empty registration is never met, so a task with
            # nothing checked and nothing under it can only be finished by closing it.
            memo[tid] = _exits.satisfied(task)
            return memo[tid]
        # HAS CHILDREN — the children ARE the evidence. Requiring an orchestrator to
        # also register conditions of its own made it permanently unsettled: it holds
        # no work, so it registers nothing, and `none` is not `met`. The loop could
        # therefore never terminate at ANY orchestrator node — a whole programme could
        # finish and the node above it would still read unfinished forever.
        ok = _own_work_done(task) and all(_settled(c, guard | {tid}) for c in children_of)
        memo[tid] = ok
        return ok

    return _settled


def descendants(task, tasks):
    """`[(task, depth)…]` for every task in this one's subtree, breadth-first, depth 1 for
    a direct child.

    THE SCAN WALKS THE SUBTREE, NOT THE CHILD ROW. An orchestrator whose child has become
    an orchestrator itself would otherwise report that child as the startable unit, when
    the thing anybody can actually pick up is two levels down. A scan that stops at depth
    one is the silo Open tail again: correct, current, and not where the work is."""
    seen = {(task or {}).get("id")}
    out, frontier, depth = [], [task], 0
    while frontier:
        depth += 1
        nxt = []
        for node in frontier:
            for kid in children(node, tasks):
                if kid.get("id") in seen:
                    continue
                seen.add(kid.get("id"))
                out.append((kid, depth))
                nxt.append(kid)
        frontier = nxt
    return out


def waves(nodes, resolve, is_settled=None):
    """Assign each node a wave number over the `depends-on` graph.

    Returns `{"waves": {n: [task…]}, "depth": {id: n}, "cycles": [[id…]],
    "blockers": {id: [blocking-task…]}, "dangling": {id: [missing-id…]}}`.

    `resolve(task_id)` returns the task blob or None, so a dependency OUTSIDE the node
    set still counts — a child that depends on some unrelated task is genuinely blocked,
    and a wave planner that only looked inside its own sibling set would call it ready.

    THREE EDGE CASES, each decided rather than defaulted:

      * a dependency that is already SETTLED does not deepen the wave. Waves measure
        what is still in the way, so a finished predecessor is not in the way;
      * a CYCLE is collected and reported, never traversed. Its members get no wave (the
        answer to "when can this start" is genuinely "never, as written"), and the scan
        prints the cycle so somebody can break it. A recursion that followed the edge
        would hang, which is the one behaviour a zero-token scan must not have;
      * a dependency on a task that NO LONGER EXISTS is `dangling`: it does not block
        (nothing can ever settle a deleted task, so blocking would be a permanent
        deadlock nobody can clear), and it is REPORTED, because silently releasing work
        whose stated prerequisite has vanished is how a plan lies."""
    is_settled = settled if is_settled is None else is_settled
    depth, cycles, blockers, dangling = {}, [], {}, {}
    on_stack, no_wave = [], set()

    def visit(task):
        tid = task.get("id")
        if tid in depth:
            return depth[tid]
        if tid in no_wave:            # already known to be a cycle member or downstream
            return None
        if tid in on_stack:                       # a cycle — record it, do not follow it
            cut = list(on_stack[on_stack.index(tid):])
            # Dedup on the MEMBER SET, not the list: the same cycle discovered from two
            # different entry points comes back rotated, and reporting one loop twice
            # reads as two problems.
            if not any(set(cut) == set(c) for c in cycles):
                cycles.append(cut)
            no_wave.update(cut)
            return None
        on_stack.append(tid)
        deepest, blocking, poisoned = 0, [], False
        for dep_id in dependencies(task):
            dep = resolve(dep_id)
            if dep is None:
                dangling.setdefault(tid, []).append(dep_id)
                continue
            if is_settled(dep):
                continue
            blocking.append(dep)
            d = visit(dep)
            if d is None:              # the dependency is in (or downstream of) a cycle
                poisoned = True
                continue
            deepest = max(deepest, d)
        on_stack.pop()
        if blocking:
            blockers[tid] = blocking
        if poisoned:
            no_wave.add(tid)
            return None
        depth[tid] = deepest + 1
        return depth[tid]

    for node in nodes or []:
        visit(node)
    grouped = {}
    in_cycle = {tid for cyc in cycles for tid in cyc}
    for node in nodes or []:
        tid = node.get("id")
        if tid in in_cycle or tid not in depth:
            continue
        grouped.setdefault(depth[tid], []).append(node)
    return {"waves": grouped, "depth": depth, "cycles": cycles,
            "blockers": blockers, "dangling": dangling}


# ------------------------------------------------------------------ the scan ----

COMPLETE, READY, BLOCKED, EMPTY = "complete", "ready", "blocked", "empty"
def _succession():
    """`lib/succession.py`, imported at call time rather than at module scope.

    Succession is DOWNSTREAM of this module: it scores a handoff against this rubric and
    reads this grade ledger, so `succession → loop` is the honest direction and a
    top-level import here would close the ring. Deferred, there is no ring at all — by
    the time any row is built, whichever module was imported first is fully loaded, and
    `sys.modules` makes every call after the first a dict lookup."""
    import succession
    return succession


# A fifth value, and it is not a nicety. Without it a wave with three children ALREADY
# RUNNING and nothing else startable reports `blocked` — which reads as "somebody must
# intervene" when the honest answer is "the loop is working, wait." Telling those two
# apart is the difference between a planner you trust and one you learn to ignore.
WORKING = "working"


def node_report(task, plan, is_settled=None, tree_depth=None, live=None):
    """One node's row in the scan: its wave, its exit-condition rollup, whether it is
    ready, and what is holding it if not."""
    is_settled = settled if is_settled is None else is_settled
    tid = task.get("id")
    counts = _exits.summary(task)
    cover = _exits.coverage(task)
    blocking = [b for b in plan["blockers"].get(tid, []) if not is_settled(b)]
    in_cycle = any(tid in cyc for cyc in plan["cycles"])
    done = is_settled(task)
    orch = is_orchestrator(task)
    # A node is RUNNING when a live Claude session is attached to it right now.
    running = bool(live and task.get("seq") in live)
    return {
        "seq": task.get("seq"), "id": tid, "title": task.get("title"),
        "status": task.get("status"), "wave": plan["depth"].get(tid),
        "exit_state": _exits.state(task), "exits": counts, "coverage": cover,
        "exits_run_ts": _exits.last_run_ts(task),
        "settled": done, "orchestrator": orch, "tree_depth": tree_depth,
        "running": running,
        # AN ORCHESTRATOR IS NEVER "READY". It plans and grades; it holds no work, so
        # offering it as the next thing to start would send somebody to the one task that
        # refuses to do any. Its children carry the work and appear on their own rows.
        # RUNNING EXCLUDES A NODE FROM `ready`, and that is the point of knowing. "Ready"
        # answers "what should I START", and something already under way is not an answer
        # to it — a planner that keeps offering work in flight is how the same child gets
        # invoked twice.
        "ready": bool(not done and not blocking and not in_cycle and not orch
                      and not running),
        "in_cycle": in_cycle,
        "blocked_by": [{"seq": b.get("seq"), "title": b.get("title")} for b in blocking],
        "dangling": list(plan["dangling"].get(tid, [])),
        "grades": len(grades(task)), "parked": parked(task),
        # SESSION HANDOFFS, because "graded BY THE PARENT" needs the parent to know one
        # happened. A relay is internal to a child's own life and creates no new task, so
        # without these two counts an orchestrator's only view of a handoff was no view at
        # all — and an ungraded handoff is precisely the kind of skipped gate the scan
        # exists to surface. The import is LOCAL: succession reads this module's rubric
        # (the natural direction — it is downstream of the grade ledger), so importing it
        # at the top here would close the loop. Deferred to call time, it cannot.
        "handoffs": len(_succession().handoffs(task)),
        "handoffs_ungraded": len(_succession().ungraded_handoffs(task)),
    }


def scan(nodes, resolve, now=None, is_settled=None, depths=None, live=None):
    """The whole deterministic answer, as one structured report.

    `nodes` is the population being planned (an orchestrator's children, or the open
    board); `resolve(id)` reaches anything they depend on. Returns the dict the text
    renderer and `--json` both read, so the two can never disagree about what was
    computed.

    THE STOPPING CONDITION IS COMPUTED HERE, and it has four values rather than a
    boolean, because "nothing to do" and "nothing I CAN do" are opposite situations:
    `complete` (every node settled — the loop is done), `ready` (work is unblocked),
    `blocked` (unsettled nodes remain and none is startable — a cycle, an external
    blocker, or a park, and the report names which), `empty` (no nodes at all — an
    orchestrator with no children, which is a plan that has not been built yet)."""
    nodes = list(nodes or [])
    depths = depths or {}
    plan = waves(nodes, resolve, is_settled=is_settled)
    rows = [node_report(t, plan, is_settled=is_settled,
                        tree_depth=depths.get(t.get("id")), live=live) for t in nodes]
    ready = [r for r in rows if r["ready"]]
    unsettled = [r for r in rows if not r["settled"]]
    if not rows:
        stop = EMPTY
    elif not unsettled:
        stop = COMPLETE
    elif ready:
        stop = READY
    elif any(r["running"] for r in rows):
        stop = WORKING
    else:
        stop = BLOCKED
    totals = {"total": len(rows), "settled": len(rows) - len(unsettled),
              "ready": len(ready), "parked": len([r for r in rows if r["parked"]]),
              "running": len([r for r in rows if r["running"]])}
    exits_roll = {"registered": sum(1 for r in rows if r["exit_state"] != _exits.NONE),
                  "unregistered": [r["seq"] for r in rows
                                   if r["exit_state"] == _exits.NONE],
                  "unmet": [r["seq"] for r in rows if r["exit_state"] == _exits.UNMET],
                  "unknown": [r["seq"] for r in rows
                              if r["exit_state"] == _exits.UNKNOWN]}
    wave_rows = {}
    for r in rows:
        if r["wave"]:
            wave_rows.setdefault(r["wave"], []).append(r)
    return {"generated_ts": time.time() if now is None else now,
            "stop": stop, "totals": totals, "exits": exits_roll,
            "running": [r["seq"] for r in rows if r["running"]],
            "waves": {n: wave_rows[n] for n in sorted(wave_rows)},
            "cycles": plan["cycles"], "rows": rows,
            "ready": [r["seq"] for r in ready]}


# ---------------------------------------------------- the orchestrator flag ----
#
# B6 / O-7. An ORCHESTRATOR-ONLY task plans and grades; it does not hold work. The rule
# was written down twice and broken twice on 2026-08-11, both times the same way: a hub
# session sitting on the parent task delegated a worker FROM the parent, so the work
# landed with no child owning it, no child digest to resume from, and nothing for the
# gate to grade.
#
# Prose could not enforce it, so the flag does. It is EXPLICIT rather than inferred from
# "has children": plenty of parents legitimately hold their own work, and a rule that
# fired on every parent task would be turned off within a day — which is worse than no
# rule, because a disabled guard still reads like a guarantee.

ORCHESTRATOR_FIELD = "orchestrator"


def is_orchestrator(task):
    """True when this task is flagged orchestrator-only."""
    return bool((task or {}).get(ORCHESTRATOR_FIELD))


def orchestrator_refusal(task, tasks, verb="delegate run"):
    """The refusal text for delegating FROM `task`, or None when it is allowed.

    NAMES THE CHILD THAT SHOULD OWN THE WORK, which is the whole difference between a
    guard and an obstacle: "refused" makes somebody go read the rule, while "refused —
    these two children are ready, run it there" makes the right thing the easy thing. Ready
    children come from the same wave computation the scan prints, so the two can never
    recommend different work."""
    if not is_orchestrator(task):
        return None
    every = list(tasks or [])
    by_id = {t.get("id"): t for t in every}
    kids = children(task, every)
    report = scan(kids, by_id.get)
    ref = task.get("seq") or (task.get("id") or "")[:8]
    lines = ["%s refused: task #%s is flagged orchestrator-only." % (verb, ref),
             "An orchestrator plans and grades; the work belongs to a child task, which "
             "holds its own context and can be graded on its own."]
    if report["ready"]:
        lines.append("Ready child task(s) right now: %s"
                     % ", ".join("#%s" % s for s in report["ready"]))
        lines.append("  %s --seq %s   (or: task-station invoke --task %s --from %s "
                     "--ask '<the request>')"
                     % (verb, report["ready"][0], report["ready"][0], ref))
    elif kids:
        lines.append("No child is ready — `task-station scan --task %s` says why "
                     "(STOP: %s)." % (ref, report["stop"].upper()))
    else:
        lines.append("This task has NO children yet, so there is nowhere for the work to "
                     "go. Create the child and parent it: "
                     "`update --task <child> --parent %s`." % ref)
    lines.append("If this really is hub work, clear the flag deliberately "
                 "(`update --task %s --orchestrator off`) or pass --force to record an "
                 "override on the task." % ref)
    return "\n".join(lines)


# ------------------------------------------------------------------- the roles ----
#
# B16 in its cheapest honest form: roles as DATA, never as new architecture. Four of
# them, deliberately few — every extra role adds another brief boundary, and the brief
# boundary is what the loop exists to remove.
#
# A3 MAKES THE TABLE CONFIGURATION rather than a constant. `ROLE_DEFAULTS` is what the
# plugin ships; `roles()` is what a station actually runs — the defaults with per-role,
# per-FIELD overrides from `config.json` merged over them. Per-field because retuning one
# model must not mean restating the grant and the contract: a restatement drifts, and the
# thing it drifts on is a child's permissions.
#
# WHICH IS ALSO WHY EVERY OVERRIDE IS VALIDATED. A config table that cannot be checked is
# worse than a constant, because what it silently gets wrong is what a child is allowed to
# do. So a field name that does not exist, a permission mode the CLI would reject, an
# effort outside its vocabulary, or a grant that is not a list of tool names all REFUSE
# the override — the shipped role stands, or a station-declared role is dropped entirely —
# and every refusal is REPORTED (`role_problems()`, rendered on the config board). Half an
# override applied is the one outcome nobody could debug.
#
# MODELS ARE NAMED BY ALIAS (`opus`, `sonnet`, `haiku`), never by a pinned id, so a role
# follows the current generation instead of freezing one release's model into the store.
# The `[1m]` window the parent is running is added back at invoke time — see
# `board/workspace.inherited_model`, and #541 for why a bare alias is a 200k child.
#
# THE TOOL GRANT IS A DENY LIST, NOT AN ALLOW LIST. `claude --tools` / `--allowed-tools`
# would REPLACE the human's tool set, dropping the MCP servers they configured; a deny
# list NARROWS it. That is the same "a role may restrict and may never replace" rule 3.7.0
# settled for the permission mode, applied to the other flag a role sets.
#
# THE REPORT CONTRACT is what the child must hand back. It travels in the child's prompt,
# because a contract the child is never told about is decoration.

ROLE_FIELDS = ("model", "permission_mode", "effort", "deny_tools", "report", "why")

# Everything a role must name before it may spawn anything. `why` is prose for the board
# and is optional; the other five decide what the child IS.
ROLE_REQUIRED_FIELDS = ("model", "permission_mode", "effort", "deny_tools", "report")

# The permission modes Claude Code's `--permission-mode` accepts. A closed list because a
# mode outside it is not a stricter role, it is a child that fails to launch. The
# RESTRICTING subset — which of these actually narrows, and so is the only kind `invoke`
# emits — is `board/workspace.RESTRICTING_MODES`: this list answers "would the CLI take
# it", that one answers "does it narrow", and they are different questions.
PERMISSION_MODES = ("plan", "acceptEdits", "default", "bypassPermissions")

# `claude --effort` levels, verbatim.
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

# WHAT A CLAIM IS, SAID IN THE CONTRACT ITSELF — because for three children in a row it
# was said nowhere. #567, #569 and #570 each did A-grade work, each ran real commands and
# quoted their real output in the report, and each was held to A- on the same dimension
# for the same single reason: no claims registered. #570 named the defect outright rather
# than guessing — "I did not want to guess at the shape mid-flight. If the report contract
# wants them, tell me what to register and I will." Three identical misses across three
# children and two subject areas is a contract defect, not three lapses of judgement, and
# declining to invent a claim was the CORRECT instinct: a claim invented to satisfy a
# contract is worse than no claim.
#
# So this says three things, in this order, and each one is load-bearing:
#   1. WHAT A CLAIM IS FOR — a command plus its expected output, re-runnable LATER. The
#      child that does not know this cannot tell a claim from a summary, and correctly
#      declines rather than filing a summary.
#   2. THE DEFAULT SHAPE — the commands you already ran, with the substring you already
#      asserted on. This is a floor, not a research project. Every one of the three
#      children had already produced the material; nobody told them the rail existed.
#   3. WHEN NOT TO REGISTER ONE — because a contract that only ever says MORE gets padded,
#      and a registered claim nobody can re-run is worse than none.
#
# `<n>` IS SUBSTITUTED with the child's own task ref at prompt time — see
# `cmds/loop._child_prompt`. A contract carrying a placeholder the child has to resolve
# for itself is one more thing to get wrong on the way to doing what was asked.
CLAIMS_CONTRACT = (
    "CLAIMS — a claim is A COMMAND PLUS THE OUTPUT SUBSTRING IT MUST PRINT, registered "
    "on your task so a LATER session can re-run it and find out whether what you proved "
    "is STILL true. It is not a summary, not a test name, and not a restatement of the "
    "work. THE DEFAULT SHAPE, which is the floor and not a research project: register "
    "the commands you ALREADY RAN to verify yourself, each with the output substring you "
    "ALREADY asserted on — `task-station claims --task <n> --register "
    "'C1|<command>|<expected substring>'`, repeatable. Write each one as a DIRECTION, "
    "not a literal: put the floor or the ceiling in the COMMAND and expect its pass "
    "token, because an expectation like `5374 tests` is falsified by the next honest "
    "release. SKIP one — and say in the report which and why, the same discipline the "
    "unverified list uses — when its command cannot run unattended, depends on a "
    "human-only step (an interactive command, a merge, an approval), or asserts only "
    "what a permanent test already covers. If that is true of ALL of them, record it: "
    "`task-station claims --task <n> --none '<why nothing here can be re-run>'`. "
    "Registering nothing and saying nothing is the one option that is not open: "
    "`claims verify` exits 3 on it, and the gate reads that as a finding."
)

ROLE_DEFAULTS = {
    "scout": {
        "model": "sonnet", "permission_mode": "plan", "effort": "medium",
        "deny_tools": ["Edit", "Write", "NotebookEdit"],
        "report": ("the files and symbols that matter with one line each on why, and "
                   "the questions reading could not answer — no edits, no plan"),
        "why": "read-only breadth — cheap and parallel, never edits"},
    "implementer": {
        "model": "opus", "permission_mode": "acceptEdits", "effort": "high",
        "deny_tools": [],
        "report": ("what changed file by file, the verification you ran with its actual "
                   "output, and anything you left undone. " + CLAIMS_CONTRACT),
        "why": "the worktree worker — one per task+repo"},
    "reviewer": {
        "model": "opus", "permission_mode": "plan", "effort": "high",
        "deny_tools": ["Edit", "Write", "NotebookEdit"],
        "report": ("each finding as file:line with the failure it would cause and a "
                   "CONFIRMED or PLAUSIBLE verdict — findings only, never a fix"),
        "why": "FRESH context, adversarial, never the implementer's session"},
    "grader": {
        "model": "opus", "permission_mode": "default", "effort": "high",
        "deny_tools": ["Edit", "Write", "NotebookEdit"],
        "report": ("a grade for each of G1-G6 with the evidence behind it, then the "
                   "accept or reject verdict and the dimension that decided it"),
        "why": "grades G1-G6; the quality of this call bounds the whole loop"},
}


def _role_field_errors(name, over):
    """Everything wrong with ONE role's override, as reportable sentences. Empty when
    the override is safe to apply.

    Collected rather than raised on the first: a station fixing its config wants the
    whole list, and a validator that stops at the first problem trains you to re-run it."""
    out = []
    for field in sorted(over):
        val = over[field]
        if field not in ROLE_FIELDS:
            out.append("role %r: `%s` is not a role setting (they are %s) — the override "
                       "is refused and the shipped role stands."
                       % (name, field, ", ".join(ROLE_FIELDS)))
            continue
        if field == "deny_tools":
            if not isinstance(val, list) or any(not str(t or "").strip() for t in val):
                out.append("role %r: `deny_tools` must be a list of tool names (it is the "
                           "grant, expressed as what the role may NOT use) — got %r."
                           % (name, val))
            continue
        if field == "permission_mode" and val not in PERMISSION_MODES:
            out.append("role %r: `permission_mode` %r is not one Claude Code accepts (%s)."
                       % (name, val, ", ".join(PERMISSION_MODES)))
            continue
        if field == "effort" and val not in EFFORT_LEVELS:
            out.append("role %r: `effort` %r is not one Claude Code accepts (%s)."
                       % (name, val, ", ".join(EFFORT_LEVELS)))
            continue
        if field in ("model", "report", "why") and not str(val or "").strip():
            out.append("role %r: `%s` cannot be empty." % (name, field))
    return out


def _effective_roles():
    """`(table, problems)` — the role table this station runs, and every override that
    was refused on the way to it.

    ONE function computes both so a caller can never read the table without the reasons
    being available beside it. `roles()` and `role_problems()` are the two halves."""
    table = {name: _copy_role(spec) for name, spec in ROLE_DEFAULTS.items()}
    problems = []
    raw = _config.get("roles")
    if raw is None:
        return table, problems
    if not isinstance(raw, dict):
        problems.append("config `roles` is %s, not a table of role -> settings — the "
                        "shipped roles stand." % type(raw).__name__)
        return table, problems
    for name in sorted(raw):
        over = raw[name]
        shipped = ROLE_DEFAULTS.get(name)
        if not isinstance(over, dict):
            problems.append("role %r: expected a table of settings, got %s — %s."
                            % (name, type(over).__name__,
                               "the shipped role stands" if shipped else "dropped"))
            continue
        bad = _role_field_errors(name, over)
        if bad:
            problems.extend(bad)
            if shipped is None:
                problems.append("role %r is station-declared and its settings are "
                                "unusable, so it is DROPPED — a role that does not name "
                                "its own permissions must never be guessed at." % name)
            continue
        merged = _copy_role(shipped) if shipped else {}
        merged.update(over)
        missing = [f for f in ROLE_REQUIRED_FIELDS if f not in merged]
        if missing:
            problems.append("role %r declares no %s — a role that does not name its own "
                            "model, permissions, effort, grant and report contract is "
                            "DROPPED rather than half-invented."
                            % (name, ", ".join(missing)))
            continue
        table[name] = merged
    return table, problems


def _copy_role(spec):
    """A role dict nobody can mutate through — the list field is copied too, so a caller
    holding a spec cannot edit the shipped table by appending to its grant."""
    out = dict(spec or {})
    out["deny_tools"] = list(out.get("deny_tools") or [])
    return out


def roles():
    """The EFFECTIVE role table: the shipped defaults with this station's validated
    overrides merged over them. Read on every call rather than cached, so a config edit
    takes effect in the next command instead of the next process."""
    return _effective_roles()[0]


def role_problems():
    """Every override `roles()` refused, as sentences a station can act on. Rendered on
    the config board: a refused override that reported nothing would look applied."""
    return _effective_roles()[1]


def role_spec(name):
    """The role's `{model, permission_mode, effort, deny_tools, report, why}`, or None for
    an unknown name. None rather than a default, so a typo'd role is reported instead of
    silently spawning a child with the wrong permissions."""
    table = roles()
    return _copy_role(table[name]) if name in table else None


# ------------------------------------------------------- the concurrency budgets ----
#
# Q2, decided 2026-08-14, and A3's job is that both numbers now MEAN something. Each was
# a config key nothing read, which is a comment with a default value.
#
# BOTH ARE MACHINE-SCOPED, not per-task: two orchestrators run on one machine, and a
# per-task cap would let them sum to a load neither one asked for. They differ in what
# they count, so they are enforced in different places — children at invoke time, builds
# through a lock in the data dir.


def children_budget(orch, tasks, live=None, cap=None):
    """`{"max", "running", "over"}` for one orchestrator — how many of its children hold
    a RUNNING session right now, against `loop_children_max`.

    THE COUNT IS PROCESS LIVENESS, not a stored flag: the same derivation the scan's
    RUNNING column uses. A record survives a crash, so a cap counting records would let
    one crashed child spend a slot forever, and a loop that cannot spawn is worse than a
    loop that spawns one too many.

    No orchestrator means no budget. The cap is a property of a LOOP — with no `--from`
    there is no sibling set to count, and inventing one would refuse a bare `invoke` for
    a reason nobody configured."""
    limit = int(_config.loop_children_max() if cap is None else cap)
    seqs = set(live or ())
    running = [k.get("seq") for k in (children(orch, tasks) if orch else [])
               if k.get("seq") is not None and k.get("seq") in seqs]
    return {"max": limit, "running": running, "over": len(running) >= limit}


# -- the machine-wide build slot --------------------------------------------------
#
# `loop_builds_max` (default 1) is the number of build / full-suite runs allowed ON THIS
# MACHINE. The default is not timidity: this machine OOMs on concurrent builds, and this
# repo's own load-dependent flakes made a parallel suite run a source of FALSE RED — a
# gate that reports red for a reason having nothing to do with the work is worse than no
# gate. A full suite run counts as a build.
#
# So the lock lives in the DATA DIR, which is per-machine and shared by every orchestrator
# on it, and NEVER on a task. Its critical section is an `fcntl.flock` over a dedicated
# lockfile beside the slot file — the same shape `lib/delegate` already uses for its
# registry, and a separate lockfile survives the atomic replace of the file it guards.
#
# A HOLDER WHOSE PROCESS IS GONE IS RECLAIMED. A lock that outlives a crash is a machine
# nobody can build on again, and the pid is the one fact that answers "is that build still
# happening" without trusting the crashed process to have cleaned up after itself.

BUILD_SLOTS_FILE = "build-slots.json"


def build_slots_path():
    """The machine's build-slot file. Under the data dir on purpose: two orchestrators
    share it, which is the whole point of the cap."""
    return os.path.join(paths.data_dir(), BUILD_SLOTS_FILE)


@contextlib.contextmanager
def _slots_locked():
    """Exclusive access to the slot file for a read-modify-write.

    Degrades to an unguarded section where `fcntl` is unavailable rather than refusing to
    build at all: the accounting still holds for one process, and a machine that cannot
    flock is not a machine this plugin should render unable to run its tests."""
    os.makedirs(paths.data_dir(), exist_ok=True)
    fh = open(build_slots_path() + ".lock", "w")
    try:
        if fcntl is not None:
            fcntl.flock(fh, fcntl.LOCK_EX)
        yield
    finally:
        try:
            if fcntl is not None:
                fcntl.flock(fh, fcntl.LOCK_UN)
        finally:
            fh.close()


def _read_slots():
    try:
        with open(build_slots_path(), encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _write_slots(doc):
    path = build_slots_path()
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


def _pid_alive(pid):
    """True when a process with `pid` exists. `os.kill(pid, 0)` delivers no signal — it
    runs the existence check only. A process owned by another user still counts as
    running (PermissionError); anything unparseable reads as dead, because a slot whose
    holder cannot be identified must not hold the machine."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _live_slots(doc):
    return {tok: e for tok, e in (doc or {}).items()
            if isinstance(e, dict) and _pid_alive(e.get("pid"))}


def build_slot_holders():
    """Who holds a build slot right now, oldest first — `{token, label, pid, started_ts}`.
    Reclaims dead holders as it reads, so a refusal can never name a process that is
    already gone."""
    with _slots_locked():
        doc = _read_slots()
        live = _live_slots(doc)
        if len(live) != len(doc):
            _write_slots(live)
    rows = [dict(e, token=tok) for tok, e in live.items()]
    return sorted(rows, key=lambda r: (r.get("started_ts") or 0, r.get("token") or ""))


def acquire_build_slot(label, pid=None, wait=0, poll=0.2):
    """Take one of the machine's `loop_builds_max` build slots, or None.

    `wait` seconds of polling before giving up — a contended slot usually frees when the
    other suite finishes, and waiting is the difference between a slower loop and a loop
    that reports a red nobody caused. `wait=0` asks once.

    The token is what `release_build_slot` needs; hold it in a `finally`."""
    deadline = time.time() + max(0, wait or 0)
    holder_pid = int(pid if pid is not None else os.getpid())
    while True:
        with _slots_locked():
            doc = _read_slots()
            live = _live_slots(doc)
            reclaimed = len(live) != len(doc)
            if len(live) < int(_config.loop_builds_max()):
                token = "%d-%s" % (holder_pid, uuid.uuid4().hex[:8])
                live[token] = {"label": str(label or "")[:160], "pid": holder_pid,
                               "started_ts": time.time()}
                _write_slots(live)
                return token
            if reclaimed:
                _write_slots(live)
        left = deadline - time.time()
        if left <= 0:
            return None
        time.sleep(min(poll, left))


def release_build_slot(token):
    """Give the slot back. False when nobody held that token — releasing twice, or
    releasing after a reclaim, is harmless rather than an error, because the caller that
    has to do this in a `finally` cannot know which."""
    if not token:
        return False
    with _slots_locked():
        doc = _read_slots()
        if token not in doc:
            return False
        del doc[token]
        _write_slots(doc)
    return True
