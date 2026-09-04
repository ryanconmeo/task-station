# gating.py
"""MERGE-GATED — the one vocabulary that separates "finished, waiting on a human" from
"failed", in a leaf that an exit condition can execute out of `origin/main`.

WHY THIS IS ITS OWN MODULE, and the reason is a measured failure rather than tidiness.
The flag `exit-add --merge-gated` writes has been stored, load-bearing and CORRECT since
3.39.0 — and INVISIBLE. `exit-show` rendered a declared condition byte-identically to an
undeclared one, and `turn` dropped the declaration on two of its six classification paths.
Measured on 2026-08-30: a competent parent read `exit-show --task 591`, saw no marking
anywhere, concluded that zero of the five conditions were declared, and sent the child a
message telling it to declare a flag it had already declared. All five carried
`merge_gated=True`. THE RECORD WAS RIGHT AND THE SURFACE WAS SILENT, which is how a
correct store produces a wrong decision.

WHAT IT COSTS ON A NIGHT NOBODY CAN MERGE, which is the case this exists for. Exit
conditions run against the MAIN checkout by design — that is what stops a child grading
its own unmerged branch. So while the one human who may merge is asleep, EVERY condition
on EVERY in-flight child is red, for one reason, and that reason is not a defect. Without
a mark, "red because unmerged" and "red because broken" are the same picture, and the only
reading available to a reader is the wrong one.

WHY THE RULES LIVE HERE AND NOT BESIDE THEIR CALLERS. Stdlib only — this module imports
NOTHING, not even from this package. That is the whole point: an exit condition proves
itself by `git show origin/main:<path> | python3 -` and cannot import `checker` (which
pulls `heal`, which pulls the store) or `turn` out of a git object. It can import a leaf.
`timing.py` (3.44.0) and `ownership.may_reassign_out` (3.43.0) were moved for exactly this
reason and said so; this is the third time, so it is the house pattern rather than a
one-off.

THE FLAG NEVER SOFTENS A VERDICT, and every function here is written to that rule. A
merge-gated condition that is UNMET is still unmet, still a gate finding, and still blocks
the release — closing a task whose work has not landed would settle a predecessor that
cannot yet release anything. All the flag changes is WHAT THE READER IS TOLD.

AND IT IS NEVER AN INFERENCE. Nothing here guesses, and since 3.66.0 the boolean this
module is handed comes from one of two places, neither of which is a heuristic:

  * COMPUTED, for a condition that declares a `repo` and a `ref`. A ref that resolves
    under `refs/remotes/` is a merge target by definition — its author cannot move it
    without pushing, and somebody has to merge. `treeref.merge_gated` is the whole rule,
    it is a string test on a name the runner resolved, and no flag feeds it.
  * DECLARED BY THE AUTHOR, for a condition that declares no tree. That is every
    condition written before 3.66.0, it is honoured permanently, and it is the negative
    control every consumer of this module is tested against — an undeclared condition is
    treated exactly as it was before this module existed.

The two can never disagree in the store, because `exit-add` refuses `--merge-gated`
alongside `--ref` rather than picking a winner.
"""

# The state string this module compares against. It MIRRORS `exits.UNMET` and cannot
# import it — a leaf that imported `exits` would drag `checker` and `heal` in behind it and
# stop being loadable from a git object. The string is the stored wire value, so the two
# can only drift if somebody changes the wire format, which would break the store first.
UNMET = "unmet"

# The word every surface prints. One spelling, in one place, so `exit-show`, `turn` and the
# gate cannot describe the same declaration three ways.
MARK = "merge-gated"


def tally(conditions):
    """`{"declared", "unmet", "merge_gated", "all_merge_gated"}` over one task's live
    conditions.

    `conditions` is an iterable of `(state, merge_gated)` pairs — plain data, so this is
    callable from a git object with no store, no task dict and no imports.

    `all_merge_gated` IS THE LOAD-BEARING ONE and it is False in two cases that look
    different and are the same mistake:
      * NOTHING IS UNMET. A task with no unmet conditions is not "pending merge", it is
        just fine, and saying otherwise would park a green task behind a merge nobody
        owes it.
      * ONE ORDINARY UNMET CONDITION SITS ALONGSIDE THE GATED ONES. Something a merge
        cannot fix means the work is not finished, and one such condition outranks any
        number of gated ones. This is the negative control that stops the flag becoming a
        way to buy a soft reading for a genuine red.

    `declared` counts EVERY declared condition, met or not, because the declaration is a
    property of the condition and not of its last result — a surface that marked only the
    red ones would go silent again the moment the work landed."""
    declared = unmet = gated = 0
    for state, merge_gated in conditions:
        if merge_gated:
            declared += 1
        if state == UNMET:
            unmet += 1
            if merge_gated:
                gated += 1
    return {"declared": declared, "unmet": unmet, "merge_gated": gated,
            "all_merge_gated": bool(unmet) and gated == unmet}


def pending_merge(t):
    """True iff every unmet condition in this tally was DECLARED merge-gated.

    The resolution rule, named rather than spelled out at each call site: `turn` reads it
    to say DONE PENDING MERGE instead of calling a finished child unfinished, and the gate
    reads it to raise `merge-gated` instead of a red."""
    return bool(t.get("all_merge_gated"))


def step_note(merge_gated):
    """The line a surface prints UNDER one step, or None for an undeclared condition.

    Under, rather than inline on the step line, on purpose: the step line already carries
    the command, and a command is long. A mark squeezed in beside it is the first thing a
    terminal truncates, and a mark that can be truncated away is the defect this module
    exists to fix, wearing a different hat."""
    if not merge_gated:
        return None
    return ("%s — it reads the merge target, so it can only be green once this work has "
            "landed there." % MARK)


def header_notes(t):
    """The 0, 1 or 2 lines a summary header adds for this tally.

    NOTHING IS PRINTED WHEN NOTHING IS DECLARED. A surface that said "0 merge-gated" on
    every task in the board would train every reader to skip the line, and the line is the
    whole feature."""
    out = []
    if t["declared"]:
        out.append("%d of them %s %s — declared as reading the merge target, so %s go "
                   "green until this work lands on main"
                   % (t["declared"], "is" if t["declared"] == 1 else "are", MARK.upper(),
                      "it cannot" if t["declared"] == 1 else "they cannot"))
    if pending_merge(t):
        out.append("→ EVERY unmet condition is %s: this reads DONE PENDING MERGE, not "
                   "failed. Nothing in the loop can turn them green, because nobody in "
                   "the loop can perform the merge." % MARK)
    return out


def wait_note(t):
    """The clause `turn` appends to a WAIT line, or None.

    WHY A WAITING CHILD IS NOT PROMOTED, and this is the sharpest rule in the module. A
    live child that has not reported is STILL RUNNING even when every condition it
    registered is declared and red — conditions are registered BEFORE the work is done, so
    "all gated and red" is the ordinary state of a child on its first minute. Promoting on
    that alone would report a child done at the moment it started, which is the false green
    this whole mechanism exists to prevent.

    What is wrong today is not the classification but the SENTENCE. The WAIT line says the
    child "has neither reported nor turned its exit conditions green" — and for a
    merge-gated child the second half names something that CANNOT happen before a human
    merges. So the reader is handed a test the child is not able to sit. This says so."""
    if not pending_merge(t):
        return None
    return ("Its %d unmet condition(s) are ALL %s, so a green checklist is not a signal "
            "this child can produce before the merge. The report is the only one that "
            "will arrive." % (t["unmet"], MARK))
