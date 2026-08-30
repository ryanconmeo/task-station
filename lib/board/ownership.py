# ownership.py
"""DECISION OWNERSHIP — a ruling lives where it was typed, and renders where it belongs.

THE PROBLEM, MEASURED ON ONE REAL PROGRAMME. `heal` can merge and split, but it cannot
MOVE, so every ruling stays on the task where a session happened to type it. Task #444
carried 31,072 chars of sync-and-station design that is #532's subject, 12,612 chars of
graph rulings belonging to paused display work, and 3,737 chars of knowledge-plane model
that is #535's. A heal on 2026-08-29 split eight oversized decisions and cut the longest
entry from 8,095 to 3,581 chars — and the TOTAL BARELY MOVED. That is the evidence that
consolidation cannot fix this: the content is load-bearing, it is not redundant, and it
is in the WRONG PLACE. No amount of rewriting fixes a place.

SO OWNERSHIP MOVES AND THE DECISION DOES NOT. One copy, one store, no duplication. The
entry stays where it was written, in full, at its original index, and gains an `owner`
naming the task that renders it. The holder renders a one-line REFERENCE STUB carrying
the title, the owner and the pointer; only the prose leaves its render, never the
knowledge that a ruling exists. It is the same shape the knowledge plane already ships —
upload, reconcile, approve, and the private node COLLAPSES TO REFERENCE — applied to the
task plane.

WHY THIS ANSWERS THE THREE WORRIES THIS DESIGN WAS CHALLENGED ON:

  * "A CHILD ENDS UP WITH EVERYTHING AND THE PARENT NO LONGER KNOWS WHAT TO DO." The stub
    is mandatory and enforced in `decisions.set_owner`; a reassign that would leave none
    is refused, because that is a delete with extra steps. The holder's PLAN — its goal,
    state and steps — is never touched by a reassign. Only long prose relocates.
  * "THE PARENT NOT KNOWING WHEN OR WHERE TO UPDATE." The rule becomes mechanical instead
    of a judgement every session makes fresh: WRITE IT WHERE THE WORK HAPPENS, OWN IT BY
    THE TASK WHOSE GOAL IT CONSTRAINS. The test is one question — would this still matter
    if this child were deleted? Yes means the parent owns it, no means the child does.
    `heal`'s placement check turns that from etiquette into something reported.
  * "CONSISTENT NO MATTER WHERE THE WORK IS DONE OR WHICH SESSION PERFORMS IT." Ownership
    is DATA, not convention, so two sessions cannot disagree about it. It is also the
    shape a field-level merge needs: deterministic only when ownership is explicit.

TWO STORES, AND WHICH ONE IS AUTHORITATIVE. The `owner` on the source entry IS the fact.
The owner task additionally carries `owned_decisions` — a pure INDEX of `{task, seq, n}`
pointers with no prose in it — so a child can render what it owns by loading only the
tasks that actually hold its rulings (in practice, its parent) instead of scanning the
board. EVERY READ VERIFIES THE INDEX AGAINST THE SOURCE and silently drops a pointer the
source does not confirm, so the index can never invent a ruling; `heal`'s placement check
reports the drift rather than letting it sit.

WRITE ORDER IS A CORRECTNESS RULE, NOT A STYLE. Two tasks are written per reassign and
either write can fail. The ordering rule is: THE WRITE THAT COULD MAKE A RULING INVISIBLE
GOES LAST.

  * reassign  — index first, then the source. A failed source write leaves an index
    pointer the read side drops: nothing changes anywhere, which is the status quo.
    The other order would leave the holder showing a stub while no task renders the
    prose, and an invisible ruling is the failure this whole mechanism exists to prevent.
  * unassign  — source first, then the index. The prose is back on the holder before the
    pointer goes, so the same rule holds in reverse.

Stdlib only. Imports `decisions`; nothing in the engine imports it back.
"""
import time

import decisions as _dec

# The task-level index the OWNER carries: `[{"task", "seq", "n", "ts"}, …]`.
INDEX_FIELD = "owned_decisions"


# -- the cross-task reference, as an argument ------------------------------------
#
# `532:14`. Decision numbers are PER-TASK and always have been, so a bare number is
# unambiguous only while ownership cannot cross tasks. The moment it can, `--supersedes 4`
# has to mean "decision 4 of the task I am attached to" and nothing else, and anything
# aimed elsewhere has to say where. The colon form is the smallest thing that says it.

def parse_ref(raw):
    """`"532:14"` → `("532", 14)`; `"14"` → `(None, 14)`; anything else → `(None, None)`.

    The task half is returned UNRESOLVED, as the string the caller typed, because this
    module does not know how a ref resolves to a task — `state.resolve_ref` does, and it
    accepts a seq, an id prefix or a handle. Splitting on the LAST colon so an id-prefix
    ref containing one is still read correctly."""
    s = str(raw or "").strip()
    if not s:
        return None, None
    if ":" in s:
        who, _sep, num = s.rpartition(":")
        try:
            return (who.strip() or None), int(num.strip())
        except (TypeError, ValueError):
            return None, None
    try:
        return None, int(s)
    except (TypeError, ValueError):
        return None, None


def make_ref(task, index1):
    """The stored cross-task reference for decision `index1` of `task`."""
    ref = {"task": task.get("id"), "n": int(index1)}
    if task.get("seq") is not None:
        ref["seq"] = task.get("seq")
    return ref


# -- the owner's index -----------------------------------------------------------

def index_entries(task):
    """The owner task's index pointers, as cleaned `{"task", "seq", "n"}` dicts, in
    stored order. Garbage is filtered rather than raised on: an index is derived data,
    and a malformed pointer must degrade to "not indexed", never to a broken render."""
    out = []
    for raw in ((task or {}).get(INDEX_FIELD) or []):
        if not isinstance(raw, dict):
            continue
        tid = str(raw.get("task") or "").strip()
        try:
            n = int(raw.get("n"))
        except (TypeError, ValueError):
            continue
        if not tid or n < 1:
            continue
        row = {"task": tid, "n": n}
        try:
            if raw.get("seq") is not None:
                row["seq"] = int(raw["seq"])
        except (TypeError, ValueError):
            pass
        if row not in out:
            out.append(row)
    return out


def index_add(owner_task, source_task, index1, now=None):
    """Record on `owner_task` that it owns decision `index1` of `source_task`.
    Idempotent — the same pointer twice is stored once."""
    rows = index_entries(owner_task)
    row = make_ref(source_task, index1)
    if row not in rows:
        row = dict(row)
        row["ts"] = time.time() if now is None else now
        rows.append(row)
        owner_task[INDEX_FIELD] = rows
        return True
    owner_task[INDEX_FIELD] = rows
    return False


def index_remove(owner_task, source_id, index1):
    """Drop the pointer to decision `index1` of `source_id`. Returns whether one went."""
    rows = index_entries(owner_task)
    kept = [r for r in rows
            if not (r["task"] == str(source_id) and r["n"] == int(index1))]
    owner_task[INDEX_FIELD] = kept
    if not kept:
        owner_task.pop(INDEX_FIELD, None)
    return len(kept) != len(rows)


# -- reading: what each side renders ---------------------------------------------

def held_stubs(task):
    """Decisions this task HOLDS but no longer renders, as `(index1, entry)` pairs in
    log order — what it prints as one-line reference stubs.

    ZERO LOADS, and that is the requirement rather than an optimisation: a task with
    twelve children must not pay twelve digest loads to render its own decision list.
    Every field a stub needs — the title, the owner's seq, the pointer — is on the entry
    this task already holds."""
    tid = (task or {}).get("id")
    return [(i, e) for i, e in enumerate((task or {}).get("decisions") or [], 1)
            if not _dec.is_replaced(e) and _dec.is_owned(e)
            and not _dec.renders_full(e, tid)]


def stub_line(index1, entry):
    """`  30. ⇢ <title>  — owned by #532` — one line, and it carries all three things a
    reference owes a reader: WHAT the ruling is, WHO owns it now, and the number that
    still addresses it here."""
    return "  %2d. ⇢ %s  — owned by %s" % (index1, _dec.stub(entry),
                                           _dec.owner_label(entry) or "another task")


def owned_elsewhere(task, load):
    """Every ruling this task OWNS that lives on another task, as
    `[{"source", "seq", "n", "entry", "ref"}, …]`, source-then-index ordered.

    `load(task_id)` is injected so this module performs no I/O of its own and the tests
    never need a store. IT LOADS ONLY THE DISTINCT TASKS THE INDEX NAMES — one, in the
    ordinary case where a child owns rulings written on its parent — never the board.

    EVERY POINTER IS VERIFIED AGAINST THE SOURCE and dropped when the source does not
    confirm it: a decision that is gone, replaced, or owned by somebody else renders
    nothing here. The index is derived data and the entry is the fact, so a disagreement
    resolves to the fact every time. `heal`'s placement check reports what was dropped —
    silence about drift is how an index starts being trusted while it is wrong."""
    tid = (task or {}).get("id")
    rows, cache = [], {}
    for ptr in index_entries(task):
        src = cache.get(ptr["task"], False)
        if src is False:
            src = cache[ptr["task"]] = load(ptr["task"])
        if not src:
            continue
        entries = src.get("decisions") or []
        if not (1 <= ptr["n"] <= len(entries)):
            continue
        entry = entries[ptr["n"] - 1]
        if _dec.owner(entry) != tid or _dec.is_replaced(entry):
            continue
        rows.append({"source": src, "seq": src.get("seq"), "n": ptr["n"],
                     "entry": entry, "ref": _dec.ref_handle(make_ref(src, ptr["n"]))})
    rows.sort(key=lambda r: (r["seq"] if r["seq"] is not None else (1 << 30), r["n"]))
    return rows


# Relation kinds that point a CHILD at its parent. Both are stored on the child's own
# `related` list, so resolving a parent costs no scan — which is what keeps the
# inherited-pin render to a single load.
PARENT_KINDS = ("parent", "spawned-from")


def parent_ids(task):
    """The task ids this task names as its parent, in `PARENT_KINDS` precedence order."""
    out = []
    for kind in PARENT_KINDS:
        for e in ((task or {}).get("related") or []):
            tid = e.get("id")
            if e.get("kind") == kind and tid and tid not in out:
                out.append(tid)
    return out


def inherited_pins(task, load):
    """The parent's PINNED decisions, as `[{"source", "seq", "n", "entry", "ref"}, …]` —
    the rulings that bind this task by INHERITANCE rather than by authorship.

    A pin is the parent's declaration that a ruling briefs every session; a child working
    under it is exactly such a session. Without this a reassign would be a downgrade for
    the child: it would gain its own rulings and lose sight of the programme's. One load
    per parent, and the ordinary task has one parent.

    Pins the child ALREADY renders — because it owns them — are excluded, so nothing is
    printed twice."""
    tid = (task or {}).get("id")
    rows = []
    for pid in parent_ids(task):
        parent = load(pid)
        if not parent:
            continue
        for i, e in _dec.live(parent.get("decisions") or []):
            if not _dec.is_pinned(e):
                continue
            if _dec.owner(e) == tid:        # already rendered in full as its own
                continue
            rows.append({"source": parent, "seq": parent.get("seq"), "n": i,
                         "entry": e, "ref": _dec.ref_handle(make_ref(parent, i))})
    return rows


# -- writing: the reassign verb and its inverse ----------------------------------

def reassign(source_task, owner_task, index1, stub_text=None, now=None,
             flag="--reassign"):
    """Move OWNERSHIP of decision `index1` from `source_task` to `owner_task`, in place
    on both dicts. `(ok, error)`. Does NOT save — the caller persists, INDEX FIRST (see
    the module docstring's write-order rule).

    Refuses a self-reassign: a task already renders its own rulings in full, so the
    operation has no meaning and would leave an index pointer at a ruling that renders
    twice. Every other refusal lives in `decisions.set_owner`, next to the element keys
    it protects."""
    if not owner_task or not source_task:
        return False, "%s — both the source and the owner task must exist" % flag
    if owner_task.get("id") == source_task.get("id"):
        return False, ("%s %s — a task already renders its own decisions in full, so it "
                       "cannot be reassigned to itself" % (flag, index1))
    entries = source_task.get("decisions") or []
    ok, err = _dec.set_owner(entries, index1, owner_task.get("id"),
                             seq=owner_task.get("seq"), stub_text=stub_text, flag=flag)
    if not ok:
        return False, err
    source_task["decisions"] = entries
    index_add(owner_task, source_task, index1, now=now)
    return True, None


def unassign(source_task, owner_task, index1, flag="--unassign"):
    """The ONE inverse of `reassign` — return decision `index1` to the task that holds
    it. `(ok, error)`. Does NOT save; the caller persists, SOURCE FIRST.

    `owner_task` may be None when the owner no longer exists: the ruling still comes
    home, because a missing owner is precisely the case where leaving it owned would
    strand the prose on a task nothing can render."""
    entries = (source_task or {}).get("decisions") or []
    ok, err = _dec.clear_owner(entries, index1, flag=flag)
    if not ok:
        return False, err
    source_task["decisions"] = entries
    if owner_task:
        index_remove(owner_task, source_task.get("id"), index1)
    return True, None


def undo_command(source_task, indices):
    """The exact one-command reversal of a reassign, ready to paste. Generated from the
    indices the write ACTUALLY touched — the same contract `heal`'s undo trail keeps,
    and for the same reason: a gate is only safe to remove when reversing a wrong call
    costs what approving one did."""
    ref = source_task.get("seq") or str(source_task.get("id") or "")[:8]
    return "heal --task %s --unassign %s" % (ref, ",".join(str(n) for n in indices))


# -- cross-task supersession (both halves, or neither) ---------------------------

def supersede_across(source_task, source_index1, refuter_task, refuter_index1,
                     flag="--supersedes"):
    """Record that decision `refuter_index1` of `refuter_task` REFUTES decision
    `source_index1` of `source_task`. `(ok, error)`. Mutates both dicts; does not save.

    WRITES BOTH DIRECTIONS OR NEITHER. The source learns what refuted it (so a reader of
    the source is not briefed by a ruling that is now wrong) and the refuter learns what
    it refuted (so a reader of the child can see it is overriding the programme, not
    merely adding to it). Half of this pair is worse than none: it produces a
    contradiction that is invisible from exactly one side, and which side that is depends
    on where the reader happens to be standing.

    The SOURCE is written last, for the write-order reason in the module docstring: a
    failed source write leaves a back-pointer naming a ruling that is still live, which
    reads as an over-claim a human can see, rather than a live ruling silently hidden."""
    if not source_task or not refuter_task:
        return False, "%s — both tasks must exist" % flag
    if (source_task.get("id") == refuter_task.get("id")
            and int(source_index1) == int(refuter_index1)):
        return False, "%s — a decision cannot supersede itself" % flag
    ref_to_source = _dec._clean_ref(make_ref(source_task, source_index1))
    if ref_to_source is None:
        return False, "%s — the superseded task has no id to reference" % flag
    # Validate the SOURCE side first so a refusal changes nothing at all.
    src_entries = source_task.get("decisions") or []
    i, err = _dec._check_index(src_entries, source_index1, flag)
    if err:
        return False, err
    err = _dec._check_unreplaced(src_entries, i, flag)
    if err:
        return False, err
    ok, err = _dec.add_supersedes_across(refuter_task.get("decisions") or [],
                                         refuter_index1,
                                         ref_to_source, flag=flag)
    if not ok:
        return False, err
    ok, err = _dec.mark_superseded_across(src_entries, source_index1,
                                          make_ref(refuter_task, refuter_index1),
                                          flag=flag)
    if not ok:
        return False, err
    source_task["decisions"] = src_entries
    return True, None


# -- closing a child: release, re-home, and REPORT the rest ----------------------
#
# THE INVERSE OF COLLAPSE-TO-REFERENCE, and the step this design itself flags as the one
# most likely to be forgotten. Without it a ruling goes cold the instant its child closes,
# which is strictly worse than the problem being fixed: today a ruling is merely in the
# wrong place; after a bad close it is nowhere.
#
# DECIDING WHETHER A CLOSED CHILD'S RULING STILL BINDS IS HARD, and hand-waving it is
# explicitly not allowed. So this splits the question into the part that is mechanical and
# the part that is not, and refuses to pretend the second part is the first:
#
#   RELEASED  — every ruling the closing task owns that LIVES ON ANOTHER TASK returns to
#               that task, which renders it in full again. Exact, mechanical, reversible,
#               and it costs the holder nothing it was not already carrying. This is the
#               inverse operation, and it always runs.
#   RE-HOMED  — the closing task's own still-current PINNED decisions become the parent's
#               to render. A pin is the task's own declaration that a ruling briefs every
#               session; it is the strongest "still binds" signal that exists in the data,
#               and pinned sets are small.
#   REPORTED  — everything else: still-current, unpinned, written on the closing task. It
#               is genuinely undecidable whether these still bind, and the honest move is
#               to NAME them with the command that re-homes them, not to move them
#               silently. Re-homing all of them would dump a whole child log onto the
#               parent in full — the exact bloat this mechanism exists to remove — and
#               dropping them silently is the failure this section exists to prevent.
#               They are named, they stay in `history`, and one command moves any of them.

def close_plan(task, load):
    """What closing `task` does to the rulings it owns — `{"released", "rehomed",
    "reported", "parent"}` — computed and NOT applied. `load(task_id)` is injected.

    `released` and `rehomed` are lists of `{"source"/"target", "seq", "n", "entry"}`;
    `reported` is a list of `(index1, entry)` on this task's own log. A task with no
    parent re-homes nothing (there is nowhere to re-home TO) and everything current
    lands in `reported`, which is the honest answer rather than a silent drop."""
    released = [{"source": r["source"], "seq": r["seq"], "n": r["n"],
                 "entry": r["entry"]}
                for r in owned_elsewhere(task, load)]
    parent = None
    for pid in parent_ids(task):
        parent = load(pid)
        if parent:
            break
    rehomed, reported = [], []
    tid = (task or {}).get("id")
    for i, e in _dec.live((task or {}).get("decisions") or []):
        if _dec.is_owned(e) and not _dec.renders_full(e, tid):
            continue                       # already somebody else's to render
        if parent and _dec.is_pinned(e):
            rehomed.append({"target": parent, "seq": parent.get("seq"), "n": i,
                            "entry": e})
        else:
            reported.append((i, e))
    return {"released": released, "rehomed": rehomed, "reported": reported,
            "parent": parent}


def apply_close_plan(task, plan, now=None):
    """Perform `plan`'s released + re-homed halves in place on the task dicts it names.
    Returns `(touched_tasks, lines)` — the OTHER task dicts that now need saving, and one
    human line per group. `reported` is deliberately not applied; it is reported.

    A RE-HOME MUST CLEAR THE PIN. The parent renders the ruling in full from now on, and
    a pin is an instruction about ONE task's digest order; carrying a closed child's pin
    into the parent's spine would let a finished child permanently reorder the
    programme's reading. The ruling survives; the ordering claim does not."""
    touched, lines = [], []
    for row in plan.get("released") or []:
        src = row["source"]
        ok, _err = unassign(src, task, row["n"])
        if ok:
            if src not in touched:
                touched.append(src)
    if plan.get("released"):
        lines.append("released %d ruling(s) back to the task that holds them — %s "
                     "render(s) them in full again"
                     % (len(plan["released"]),
                        ", ".join(sorted({"#%s" % (r["seq"] or "?")
                                          for r in plan["released"]}))))
    parent = plan.get("parent")
    done = []
    for row in plan.get("rehomed") or []:
        entries = task.get("decisions") or []
        rich = _dec.as_rich(entries[row["n"] - 1])
        rich.pop("pinned", None)            # see the docstring: the ruling survives, the
        entries[row["n"] - 1] = _dec.compact(rich)   # ordering claim does not
        ok, _err = reassign(task, parent, row["n"], now=now)
        if ok:
            done.append(row["n"])
    if done:
        if parent not in touched:
            touched.append(parent)
        lines.append("re-homed decision(s) %s to #%s — they were PINNED, which is this "
                     "task's own declaration that they brief every session, so they are "
                     "the rulings that most clearly still bind (`%s` undoes it)"
                     % (", ".join(str(n) for n in done), parent.get("seq"),
                        undo_command(task, done)))
    return touched, lines


def close_report_lines(task, plan):
    """The close-time report — including, explicitly, what was NOT moved.

    The un-moved half is the point. A close that silently kept a child's rulings out of
    the parent's sight reads exactly like a close that had nothing to keep, and the
    reader only finds out at the moment they needed the ruling."""
    out = []
    reported = plan.get("reported") or []
    if not reported:
        return out
    ref = task.get("seq") or str(task.get("id") or "")[:8]
    nums = ", ".join(str(i) for i, _e in reported[:12])
    more = "" if len(reported) <= 12 else " (+%d more)" % (len(reported) - 12)
    out.append("  %d still-current ruling(s) on this task were NOT re-homed: %s%s."
               % (len(reported), nums, more))
    out.append("  Nothing marks them as still binding beyond this task, and whether they "
               "do is not mechanically decidable — so they are NAMED here rather than "
               "moved. They stay in `task-station history %s`; "
               "`heal --task %s --reassign <n,…> --to <parent>` re-homes any of them."
               % (ref, ref))
    return out
