# steps.py
"""The step reconcile primitive — one owner of the `task["steps"]` element shape,
shared by task-station.py and heal.py.

WHY THIS EXISTS. Decisions got three reconcile verbs (supersede / split / merge);
steps got none. A step could only be ADDED, TICKED or UNTICKED, so a step that had
gone STALE — the plan moved on, or it names a vocabulary that was retired — had no
honest exit:

  * ticking it done is a LIE (nobody did it, and the progress counter then claims
    work that never happened),
  * deleting it destroys the record of what was once agreed,
  * and adding a "do not execute step 3" warning step is the anti-pattern — measured
    on one real task, three steps read as stale and one of them was itself a warning
    about another step.

So a step gets exactly ONE reconcile verb: SUPERSEDE. It is deliberately the same
shape as the decision verb — non-destructive, reversible, and it names what replaced
the step when something did.

NO STEP EDITING, DELIBERATELY. There is no `--step-edit`. Rewriting a step in place
mutates the record: the checklist would silently stop matching what was agreed, and
nothing would say it changed. Superseding the stale step and adding a corrected one
keeps both, in order, and says which replaced which.

THE COUNTER IS THE POINT. `progress()` excludes a superseded step from BOTH numbers.
A stale step left in the denominator makes a task read as permanently unfinished — the
`✓2/5` on every board, forever — which is exactly the pressure that made someone tick
a step nobody did.

ELEMENT SHAPE — dual, and permanently so. An element is EITHER:

    {"text": "write the tests", "done": false}                       (ordinary)
    {"text": "…", "done": true, "done_ts": 1754956800.0}             (ticked)
    {"text": "…", "done": false, "superseded": true,
     "superseded_by": 5}                                             (reconciled)

`superseded_by` is OPTIONAL: a step can be retired with nothing replacing it (the plan
simply dropped it), which reads as `SUPERSEDED (nothing replaced it)`. A legacy bare
STRING is also accepted on read — fork imports produced them — and is always current.

WHEN A TICK HAPPENED — `done_ts`, and it exists because nothing else on a task could
answer that. `updated_ts` moves for every field, the event feed is bounded so on a busy
task a tick ages out of it, and `done` is one boolean with no history. Without a
completion moment nobody can ask the one question that matters about a plan: has anything
actually been finished lately? `lib/checker.py` asks it (goal drift), and the rule it
follows is that a done step WITHOUT a stamp means "completed at an unknown time" — never
"completed long ago", which would report every step ticked before this shipped as
twenty-thousand-day-old drift.

BACK-COMPAT: all three extra keys are strictly ADDITIVE. An UNTICKED step is still stored
byte-identically to how every older version stored it, an older READER ignores the keys it
does not know, and a task written by an older version reads unchanged — its ticked steps
simply carry no stamp, which is the "unknown time" case above and is the COMMON one.

Indices are 1-BASED and are STABLE IDS, not positions: they are the numbers the
checklist prints and the numbers `--step-done` / `--step-supersede` take. Because a
superseded step keeps its slot, the ACTIVE checklist can show gaps (1, 3, 4) — that is
correct, and renumbering would silently repoint every command a reader had in hand.

Stdlib only (`time`, for the completion stamp, and nothing else) — this module stays a
leaf, exactly like decisions.py.
"""
import time

# The reconcile keys, in one place: what `restore` clears and what `compact` carries.
SUPERSEDED_KEYS = ("superseded", "superseded_by")

# The completion stamp's key, named once so the reader (`checker.done_ts`) and the writer
# (`set_done`) can never disagree about it.
DONE_TS_FIELD = "done_ts"


# -- element accessors: the ONLY sanctioned way to read an element ---------------

def text(step):
    """The human text of a step, for either shape. Never raises — an odd element is
    coerced to str rather than blowing up a render."""
    if isinstance(step, dict):
        return str(step.get("text") or "")
    return "" if step is None else str(step)


def is_done(step):
    """True iff this step is ticked. A legacy bare string is never done."""
    return bool(step.get("done")) if isinstance(step, dict) else False


def superseded_by(step):
    """The 1-based index of the step that REPLACED this one, or None when nothing was
    named. A garbled value reads as "no replacement named" rather than raising."""
    if not isinstance(step, dict):
        return None
    raw = step.get("superseded_by")
    if not raw:
        return None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def is_superseded(step):
    """True iff this step has LEFT the active checklist. What every present-tense
    surface omits (the detail checklist, the progress counter, the digest snapshot,
    the board view-model) and what `/todo <n> history` still shows, marked."""
    if not isinstance(step, dict):
        return False
    return bool(step.get("superseded")) or superseded_by(step) is not None


def replacement_label(step):
    """`SUPERSEDED by step 5` / `SUPERSEDED (nothing replaced it)`, or None when the
    step is still active. Owned here so the history view, the update result line and
    the tests all agree on ONE wording."""
    if not is_superseded(step):
        return None
    n = superseded_by(step)
    return ("SUPERSEDED by step %d" % n) if n else "SUPERSEDED (nothing replaced it)"


def _already_phrase(step):
    """`already superseded by step 5` — the lowercase mid-sentence phrase for a refusal,
    or None when the step is active."""
    if not is_superseded(step):
        return None
    n = superseded_by(step)
    return ("already superseded by step %d" % n) if n else "already superseded"


# -- shape conversion ------------------------------------------------------------

def as_rich(step):
    """A mutable dict view of an element (legacy string → `{"text": …, "done": False}`).
    Returns a COPY, so the caller mutates it and writes it back via `compact()`."""
    if isinstance(step, dict):
        return dict(step)
    return {"text": text(step), "done": False}


def compact(step):
    """The storage form of an element: the `{"text", "done"}` dict plus whatever
    reconcile keys it actually carries.

    Unlike a decision there is no string form to collapse back to — the frozen step
    shape has always been a dict — so the back-compat guarantee here is that an
    ORDINARY (untouched, unticked) step round-trips to exactly `{"text": …, "done": …}`,
    byte-identical to what every older version wrote. Every other key — the two reconcile
    keys, `done_ts`, and anything a NEWER version adds — is carried through unchanged
    rather than dropped, which is what makes the shape extensible without a migration.

    Falsy values are dropped, so a `done_ts` of exactly 0.0 would not survive. That is
    harmless and deliberate: epoch 0 is not a completion moment anybody recorded, and
    `checker.done_ts` rejects it on the read side for the same reason, so the two ends
    agree that a zero stamp means "no stamp"."""
    if not isinstance(step, dict):
        return {"text": text(step), "done": False}
    out = {"text": text(step), "done": bool(step.get("done"))}
    for k, v in step.items():
        if k in ("text", "done"):
            continue
        if v in (None, False, 0, "", []):
            continue
        out[k] = v
    return out


# -- selection -------------------------------------------------------------------

def live(steps):
    """Every step still on the ACTIVE checklist, as `(index1, step)` pairs. Indices are
    the ORIGINAL ones, so the numbers a reader sees keep working as command arguments
    even when a superseded step leaves a gap."""
    return [(i, s) for i, s in enumerate(steps or [], 1) if not is_superseded(s)]


def superseded(steps):
    """The inverse of `live`: every SUPERSEDED step as `(index1, step)` pairs. Nothing
    on the resume path renders these — they exist so `/todo <n> history` can prove the
    checklist trail is still complete."""
    return [(i, s) for i, s in enumerate(steps or [], 1) if is_superseded(s)]


def progress(steps):
    """`(done, total)` over the ACTIVE checklist only; `(0, 0)` when there is none.

    A superseded step is excluded from BOTH numbers. Excluding it from the numerator
    alone would be worse than not having the verb: the task would read `✓2/5` forever
    with two steps nobody can ever finish."""
    current = live(steps)
    return sum(1 for _i, s in current if is_done(s)), len(current)


def counts(steps):
    """`(active, superseded)` — the two population sizes, for a render that wants to
    say "and N left the checklist"."""
    all_steps = steps or []
    gone = len(superseded(all_steps))
    return len(all_steps) - gone, gone


# -- mutation (each returns `(ok, error_message)`) --------------------------------

def _check_index(steps, index1, flag):
    """Validate a 1-based step index. Returns (int, None) on a hit, (None, error-line)
    otherwise — a bad index is a CLEAR ERROR, never a silent no-op."""
    try:
        i = int(index1)
    except (TypeError, ValueError):
        return None, ("%s expects a 1-based step number (as numbered in `/todo <n>`), "
                      "got %r" % (flag, index1))
    total = len(steps or [])
    if i < 1 or i > total:
        return None, ("%s %d — no such step; this task has %d (see `/todo <n>` for the "
                      "numbered checklist)" % (flag, i, total))
    return i, None


def mark_superseded(steps, index1, by_index1=None, flag="--step-supersede"):
    """Retire step `index1` from the active checklist, optionally naming the step
    `by_index1` that replaced it.

    NON-DESTRUCTIVE: the step keeps its full text and its done-state, stays in
    `/todo <n> history` marked `SUPERSEDED by step <n>`, and comes back with
    `restore`. `by_index1` is optional because a plan that simply dropped a step has
    nothing to name, and inventing a replacement would be inventing history."""
    i, err = _check_index(steps, index1, flag)
    if err:
        return False, err
    how = _already_phrase(steps[i - 1])
    if how is not None:
        return False, ("%s %d — that step is %s; `--step-restore %d` puts it back first"
                       % (flag, i, how, i))
    by = None
    if by_index1 is not None:
        by, err = _check_index(steps, by_index1, flag)
        if err:
            return False, err
        if by == i:
            return False, "%s %d — a step cannot supersede itself" % (flag, i)
    rich = as_rich(steps[i - 1])
    rich["superseded"] = True
    if by is not None:
        rich["superseded_by"] = by
    steps[i - 1] = compact(rich)
    return True, None


def restore(steps, index1, flag="--step-restore"):
    """Clear the supersede mark on step `index1`, returning it to the active checklist
    with its text and done-state intact. The inverse of `mark_superseded`, which is what
    makes the verb safe to use on a judgement call.

    Errors (never a silent no-op) on a missing index or on a step that was not
    superseded — silently "restoring" an active step would report a reconcile that
    never happened."""
    i, err = _check_index(steps, index1, flag)
    if err:
        return False, err
    if not is_superseded(steps[i - 1]):
        return False, ("%s %d — that step is not superseded; there is nothing to restore"
                       % (flag, i))
    rich = as_rich(steps[i - 1])
    for key in SUPERSEDED_KEYS:
        rich.pop(key, None)
    steps[i - 1] = compact(rich)
    return True, None


def set_done(steps, index1, done, flag=None, now=None):
    """Tick/untick step `index1`, stamping WHEN. Returns `(ok, error_message)`.

    REFUSES on a superseded step, and says so: it is off the active checklist, so
    ticking it would put a completion into the record for work that was retired rather
    than done — the exact lie the supersede verb exists to avoid.

    THE STAMP IS SYMMETRIC. Ticking writes `done_ts`; UNTICKING DROPS IT. Leaving the
    old stamp behind on an unticked step would leave the record asserting a completion
    moment for work that is no longer claimed as done, and the next tick would then
    either overwrite it (fine) or — if nobody ever re-ticked — brief a resumed session
    with a date for something unfinished. Re-ticking always writes a FRESH stamp rather
    than restoring the old one: the honest answer to "when was this finished" is the
    last time somebody said it was.

    `now` is injectable so a caller with an authoritative clock (and the tests) can
    supply one; it defaults to the wall clock exactly as `heal.stamp_goal_touched`
    does."""
    if flag is None:
        flag = "--step-done" if done else "--step-undone"
    i, err = _check_index(steps, index1, flag)
    if err:
        return False, err
    step = steps[i - 1]
    how = _already_phrase(step)
    if how is not None:
        return False, ("%s %d — that step is %s and is off the active checklist; "
                       "`--step-restore %d` puts it back first" % (flag, i, how, i))
    rich = as_rich(step)
    rich["done"] = bool(done)
    if done:
        rich[DONE_TS_FIELD] = time.time() if now is None else now
    else:
        rich.pop(DONE_TS_FIELD, None)
    steps[i - 1] = compact(rich)
    return True, None
