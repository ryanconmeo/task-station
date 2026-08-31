# exits.py
"""EXIT CONDITIONS — the runnable half of a plan item, so DONE is COMPUTED rather
than asserted.

WHY THIS EXISTS, measured on one real plan. The 3.0.0 migration document carried both
kinds of statement at once, and they aged in opposite directions:

  * its seventeen registered CLAIMS stayed honest for a year, because something RAN
    them — a claim that stopped being true said so the next time anybody looked;
  * its prose STEPS drifted. Thirteen of them silently became true and nobody noticed
    for weeks, because noticing would have required checking the record against the
    world, and nothing did.

`heal` cannot close that gap, and it is worth being exact about why: HEAL RECONCILES
THE RECORD AGAINST ITSELF, NEVER AGAINST REALITY. Its stale-step check needs a step to
DECLARE itself stale — to contain `STALE` or `do not execute`. A step reading "Phase 4
brain port" declares nothing at all. It just quietly became true.

So a step gets what a claim already had: a command that settles it, and the substrings
that must appear in that command's output. `exit-tick` RUNS them and ticks what passed.
The checklist then reports its own state, and "what is still open" becomes a
computation instead of a list somebody has to maintain.

THE SHAPE IS DELIBERATELY THE ONE `claims` ALREADY USES — `(command, expected
substrings)` judged against the command's EXIT STATUS as well, combined stdout+stderr,
one runner deciding what `ran` / `timeout` / `error` mean (`checker.run_command`,
shared rather than re-implemented here so the two can never drift apart). What is different is only WHERE it attaches: a claim is an
assertion about a DOCUMENT bound to the task, an exit condition is an assertion about
ONE STEP of the checklist. That is why this is a separate field and not a fourth claim
key — a claim answers "is this plan still true", an exit condition answers "is this item
finished", and only the second one can move a tick.

STORAGE — additive, on the step element itself:

    {"text": "…", "done": false,
     "exit": {"cmd": "…", "expect": ["…"], "added_ts": 1.7e9,
              "last": {"ts": …, "ok": …, "status": "ran", "code": 0,
                       "missing": [], "got": "…"}}}

On the step, not in a parallel task-level table keyed by index, because the two would
have to be kept in step (pun intended) through every `--step-supersede`,
`--step-restore` and future insert — and the first time they disagreed, a condition
would silently gate the wrong item. `steps.compact` carries unknown keys through
unchanged, which is exactly the extension point this uses, so an older reader ignores
the key and an ordinary step still round-trips byte-identically.

THE FOUR RULES, inherited from the checker and not negotiable:

  1. NOTHING RUNS AT SESSION START. These are arbitrary shell commands the user
     registered. Running them on the session-start path would put an unbounded,
     user-defined cost — with whatever side effects those commands have — in front of
     every session. Verification is on demand, from the CLI, and that is the whole
     contract.
  2. A CONDITION THAT DID NOT RUN REFUTES NOTHING. A timeout or a launch failure is
     `unknown`, never `unmet`, and MUST NEVER move a tick in either direction. This is
     the checker's "uncountable is never zero" rule, and it is the rule that makes
     ticking safe to automate: the failure mode to design against is a green board, and
     the second failure mode is a red one caused by a missing binary.
  3. NO EXPECTED SUBSTRING IS REFUSED. A condition asserting nothing passes forever
     whatever the command prints — the one failure a verification mechanism must not
     have, because it reports green while proving nothing.
  4. TICKING IS AUTOMATIC, UNTICKING IS OPT-IN. A passing condition ticks its step: that
     is the drift this module exists to kill. A FAILING condition on an already-ticked
     step is reported as a REGRESSION and, by default, changes nothing — because
     rewriting a human's record of completed work on the strength of one command's exit
     status is a bigger claim than this module gets to make unasked. `--untick` opts
     into the symmetric behaviour for a plan that wants it.
  5. A GREEN CONDITION MEANS THE COMMAND SUCCEEDED (3.49.0). `returncode == 0` is a
     REQUIRED CONJUNCT alongside the expected substring, and the two ask different
     questions: the substring asks whether the command SAID the thing, the exit status
     asks whether it WORKED. Until 3.49.0 only the first was asked, so
     `echo T-PASS; exit 1` was a pass and a condition could be satisfied by a command
     that failed — the substrate under four separately-patched incidents in which a
     condition asserted that some text appeared in the output of a command run
     somewhere, against something. There is NO exemption flag, deliberately: a
     condition that legitimately exits non-zero while printing its token is a condition
     to REWRITE, because the moment an author can declare their own condition exempt
     the invariant means nothing. A non-zero exit is `unmet`, never `unknown` — rule 2
     protects commands that did not RUN, and this one ran.

Stdlib only. Imports `checker`, `config` and `steps`; nothing imports it back.
"""
import time

import checker as _checker
import config as _config
import gating as _gating
import steps as _steps

# The per-step key this module owns. Additive: every other reader ignores it.
EXIT_FIELD = "exit"

# A condition's state, as four words that are deliberately NOT three:
#   met       — it ran and every expected substring was there
#   unmet     — it ran and something was missing
#   unknown   — registered, but never run / timed out / could not be launched
#   none      — no condition registered on this step at all
# `unknown` and `none` are separate on purpose. "Registered but never run" is a
# statement about this session; "nothing registered" is a statement about the plan.
MET, UNMET, UNKNOWN, NONE = "met", "unmet", "unknown", "none"


# -- reading -------------------------------------------------------------------

def condition(step):
    """The well-formed exit condition on `step`, or None.

    FILTERS GARBAGE RATHER THAN RAISING, exactly like `checker.claim_items`: a
    condition with no command cannot be run, and a store this module did not write is
    never a reason to break a render."""
    if not isinstance(step, dict):
        return None
    raw = step.get(EXIT_FIELD)
    if not isinstance(raw, dict):
        return None
    cmd = str(raw.get("cmd") or "").strip()
    if not cmd:
        return None
    expect = [str(e) for e in (raw.get("expect") or []) if str(e).strip()]
    if not expect:
        return None
    out = {"cmd": cmd, "expect": expect, "merge_gated": bool(raw.get("merge_gated"))}
    if raw.get("added_ts"):
        out["added_ts"] = raw["added_ts"]
    last = raw.get("last")
    out["last"] = last if isinstance(last, dict) else {}
    return out


def has_condition(step):
    """True iff `step` carries a runnable exit condition."""
    return condition(step) is not None


# -- MERGE-GATED: the condition that cannot pass until a human merges ------------------
#
# THE STATE THAT WAS UNSAYABLE. Exit conditions run against the MAIN checkout, so a child's
# own work cannot turn them green until its PR lands there. That is by design — it is what
# stops a child grading its own unmerged branch — and it means a child can be genuinely
# FINISHED while every condition it registered is red. Observed on a real child: work done,
# release cut, PR open, report filed, and the gate read 0 of 6 conditions met for seven
# hours. The loop's vocabulary (running / ready / settled / parked) had no word for
# "finished, waiting on a human to merge", so the honest state could not be recorded and
# the dishonest one — unfinished — was the only thing sayable.
#
# WHY A DECLARATION AND NOT AN INFERENCE. `turn.landed` can probe whether a branch has
# landed, but it needs a branch name nobody stored and a repo nobody named, and its
# unprobed answer is the common one. A condition's AUTHOR knows at registration time
# whether it reads the merge target — they wrote `git show origin/main:…`. So the author
# says so once (`exit-add --merge-gated`) and every later reader gets it for free. An
# inference that is usually "unprobed" is not a state; a declaration is.
#
# IT NEVER SOFTENS A VERDICT. A merge-gated condition that is UNMET is still unmet, still a
# gate finding, and still blocks the release — closing a task whose work has not landed
# would settle a predecessor that cannot yet release anything. All the flag changes is WHAT
# THE REPORT SAYS: `merge-gated` and DONE PENDING MERGE, rather than a red the reader is
# left to explain.

def merge_gated(step):
    """True iff this step's condition was DECLARED as gated on a merge."""
    cond = condition(step)
    return bool(cond and cond.get("merge_gated"))


def merge_gate(task):
    """`{"declared", "unmet", "merge_gated", "all_merge_gated"}` over the LIVE steps.

    THE RULES THEMSELVES LIVE IN `gating`, and the move is not cosmetic: this module
    imports `checker`, which imports `heal`, which reads the store — so nothing here can be
    executed out of a git object, and an exit condition that must resolve `origin/main`
    could never exercise the rule it was asserting. `gating` imports nothing at all. This
    function is now only the part that needs a task dict: turning stored steps into the
    `(state, merge_gated)` pairs the leaf decides on.

    `declared` is new and additive — every declared condition, met or not — because a
    surface that counted only the red ones would go silent the moment the work landed."""
    return _gating.tally(
        (item_state(s), merge_gated(s))
        for _n, s in _steps.live((task or {}).get("steps") or []))


def item_state(step):
    """One of MET / UNMET / UNKNOWN / NONE for one step, read from the STORED last
    result. Runs nothing — this is the read every cheap surface uses."""
    cond = condition(step)
    if cond is None:
        return NONE
    last = cond.get("last") or {}
    if not last or last.get("status") != "ran":
        return UNKNOWN
    return MET if last.get("ok") else UNMET


def items(task):
    """Every LIVE step carrying a condition, as
    `{"n", "text", "done", "cmd", "expect", "state", "merge_gated", "last"}`.

    `merge_gated` is carried HERE rather than re-read per surface, because every reader
    that renders a condition is a reader that must be able to say whether it was declared
    — and the one that could not is exactly how this key came to be added.

    Superseded steps are excluded, and that is a correctness point rather than tidiness:
    a retired step is off the active checklist, so letting its condition gate anything
    would let a plan that was explicitly abandoned keep blocking the work that replaced
    it. `n` is the 1-based STABLE index the checklist prints, so it is the number the
    reader types back into `exit-add --step`."""
    out = []
    for n, step in _steps.live((task or {}).get("steps") or []):
        cond = condition(step)
        if cond is None:
            continue
        out.append({"n": n, "text": _steps.text(step), "done": _steps.is_done(step),
                    "cmd": cond["cmd"], "expect": cond["expect"],
                    "state": item_state(step), "merge_gated": bool(cond["merge_gated"]),
                    "last": cond.get("last") or {}})
    return out


def summary(task):
    """`{"total", "met", "unmet", "unknown"}` over the registered conditions, from
    stored results. `total` is how many conditions exist, NOT how many steps there are:
    a plan with three steps and one condition can only ever report on the one."""
    counts = {"total": 0, MET: 0, UNMET: 0, UNKNOWN: 0}
    for item in items(task):
        counts["total"] += 1
        counts[item["state"]] += 1
    return counts


def state(task):
    """The task-level rollup: MET when every registered condition is met, UNMET when
    any ran and failed, UNKNOWN when some are registered but unrun, NONE when the task
    registers nothing.

    A TASK WITH NO CONDITIONS IS `none`, NEVER `met`. Reporting "all conditions
    satisfied" for a plan that registered none is the single most dangerous line this
    module could print — it is the green board with nothing behind it, and it is what
    would let the wave scan release dependent work on the strength of an empty
    checklist."""
    counts = summary(task)
    if not counts["total"]:
        return NONE
    if counts[UNMET]:
        return UNMET
    if counts[UNKNOWN]:
        return UNKNOWN
    return MET


def coverage(task):
    """`{"live", "covered", "uncovered", "uncovered_open"}` — how much of the ACTIVE
    checklist can settle itself.

    `uncovered_open` is the number that matters: live steps with no condition AND no
    tick. Those are the items nothing can compute, and their existence is what stops a
    partially-instrumented task from claiming it is finished."""
    live = _steps.live((task or {}).get("steps") or [])
    covered = [(n, s) for n, s in live if has_condition(s)]
    uncovered = [(n, s) for n, s in live if not has_condition(s)]
    return {"live": len(live), "covered": len(covered), "uncovered": len(uncovered),
            "uncovered_open": len([1 for _n, s in uncovered if not _steps.is_done(s)])}


def satisfied(task):
    """True only when the task registers at least one condition, EVERY registered
    condition is MET, and no live step is left both uncovered and unticked. The
    predicate the wave scan gates on.

    THE THIRD CLAUSE IS THE ONE WORTH ARGUING ABOUT, and it is the empty-registration
    rule in weaker form. Without it, a task with eight steps could register one
    condition, pass it, and report itself finished — releasing every dependent wave on
    the strength of one eighth of its plan. Partial instrumentation must not buy a
    green, or the incentive is to instrument the easy step and stop.

    An uncovered step that is TICKED is tolerated rather than fatal: that is a human's
    assertion, which is exactly what this mechanism is replacing, but refusing to
    proceed past one would make the whole feature unadoptable on any plan that predates
    it. So the rule bites on what is genuinely unanswered — uncovered AND unfinished."""
    return state(task) == MET and not coverage(task)["uncovered_open"]


def last_run_ts(task):
    """When any condition on this task was last RUN, or None. What lets a surface say
    how fresh a verdict is — a `met` computed three weeks ago is a different claim from
    one computed a minute ago, and only one of them should release a dependent wave."""
    stamps = [(i["last"] or {}).get("ts") for i in items(task)]
    stamps = [s for s in stamps if s]
    return max(stamps) if stamps else None


# -- writing -------------------------------------------------------------------

def set_condition(steps, index1, cmd, expect, now=None, flag="exit-add",
                  merge_gated=False):
    """Attach (or replace) the exit condition on step `index1`. `(ok, error)`.

    UPSERTS, like `claims --register`: re-running it on a step rewrites that step's
    condition and leaves every other step alone, which is what makes the command safe
    to re-run while a plan is being written. Keeps no history of the previous command —
    a condition is a statement of PRESENT structure, the same reasoning that makes a
    relation edge correctable rather than superseded.

    REFUSES a superseded step: it is off the active checklist, so a condition there
    could never tick anything, and storing one would read later as a gate that silently
    does nothing.

    Does NOT save; the caller persists — the contract every mutator in this codebase
    keeps."""
    i, err = _steps._check_index(steps, index1, flag)
    if err:
        return False, err
    if _steps.is_superseded(steps[i - 1]):
        return False, ("%s %d — that step is superseded and off the active checklist, so "
                       "a condition on it could never tick anything; `--step-restore %d` "
                       "puts it back first" % (flag, i, i))
    command = str(cmd or "").strip()
    if not command:
        return False, "%s needs --cmd '<the shell command that settles this step>'." % flag
    wanted = [str(e).strip() for e in (expect or []) if str(e).strip()]
    if not wanted:
        return False, ("%s step %d — no --expect substring, so the condition would pass "
                       "whatever the command printed. Name at least one string that must "
                       "appear in its output." % (flag, i))
    rich = _steps.as_rich(steps[i - 1])
    rich[EXIT_FIELD] = {"cmd": command, "expect": wanted,
                        "merge_gated": bool(merge_gated),
                        "added_ts": time.time() if now is None else now}
    steps[i - 1] = _steps.compact(rich)
    return True, None


def clear_condition(steps, index1, flag="exit-rm"):
    """Drop the exit condition on step `index1`, leaving the step and its tick intact.
    `(ok, error)`.

    Errors rather than silently succeeding when there was nothing to clear: "removed"
    after a typo reads as success, and the reader then believes a gate is gone when it
    is still armed. Does NOT save."""
    i, err = _steps._check_index(steps, index1, flag)
    if err:
        return False, err
    if not has_condition(steps[i - 1]):
        return False, ("%s %d — that step has no exit condition; there is nothing to "
                       "remove" % (flag, i))
    rich = _steps.as_rich(steps[i - 1])
    rich.pop(EXIT_FIELD, None)
    steps[i - 1] = _steps.compact(rich)
    return True, None


# -- running -------------------------------------------------------------------
#
# ONE RUNNER, shared with claims. `checker.run_command` decides what "ran", "timeout"
# and "error" mean and combines stdout with stderr; re-implementing eight lines of
# subprocess handling here is exactly how the two surfaces would end up disagreeing
# about whether a command that wrote to stderr and exited 0 had passed.
#
# NEVER ON THE SESSION-START PATH. See rule 1 in the module docstring.


def command_timeout():
    """Seconds one exit-condition command may run. Its own config key rather than the
    claim timeout, because the two are tuned against different things: a claim
    legitimately runs a whole suite, an exit condition is usually a grep."""
    return _config.exit_command_timeout()


def evaluate(task, only=None, timeout=None, now=None, run=None):
    """RUN the registered conditions and record each outcome on its step. Returns the
    results list; does NOT tick anything and does NOT save.

    A result is `{"n", "text", "cmd", "expect", "ok", "status", "code", "missing",
    "got", "was_done"}`. `missing` names the expected substrings that did not appear, so
    a failure says what was actually wrong instead of dumping output at the reader,
    `code` is the command's exit status, and `status` separates a command that RAN and
    disagreed from one that never ran at all — the distinction rule 2 turns on.
    `was_done` is the tick state BEFORE this run, so the caller can tell a fresh
    completion from a regression.

    `ok` REQUIRES BOTH (rule 5): exit status 0 AND every expected substring. A non-zero
    exit is UNMET rather than UNKNOWN, because the command ran — it just failed, and a
    failing command cannot tick a step no matter what it printed on the way down.

    `only` restricts to one step number or a list of them. `run` is injected by the
    tests so the suite never spawns a shell."""
    now = time.time() if now is None else now
    timeout = command_timeout() if timeout is None else timeout
    run = _checker.run_command if run is None else run
    wanted = None
    if only is not None:
        wanted = {int(only)} if isinstance(only, int) else {int(o) for o in only}
    steps = (task or {}).get("steps") or []
    results = []
    for n, step in _steps.live(steps):
        cond = condition(step)
        if cond is None or (wanted is not None and n not in wanted):
            continue
        out, status, code = _checker.invoke(run, cond["cmd"], timeout)
        missing = [e for e in cond["expect"] if e not in out]
        ok = bool(status == "ran" and code == 0 and not missing)
        got = ("(no output — timed out after %ss)" % timeout) if status == "timeout" \
            else _checker.output_tail(out)
        rich = _steps.as_rich(step)
        block = dict(rich.get(EXIT_FIELD) or {})
        block["last"] = {"ts": now, "ok": ok, "status": status, "code": code,
                         "missing": missing, "got": got}
        rich[EXIT_FIELD] = block
        steps[n - 1] = _steps.compact(rich)
        results.append({"n": n, "text": _steps.text(step), "cmd": cond["cmd"],
                        "expect": cond["expect"], "ok": ok, "status": status,
                        "code": code, "missing": missing, "got": got,
                        "was_done": _steps.is_done(step)})
    return results


def apply_results(task, results, untick=False, now=None):
    """Move the ticks the results justify. Returns
    `{"ticked", "unticked", "regressed", "unknown"}`, each a list of step numbers.

    THE ASYMMETRY IS THE DESIGN, not an omission (rule 4):

      * a PASSING condition on an unticked step TICKS it — the drift this module exists
        to kill, and the direction where being wrong costs a re-tick;
      * a FAILING condition on a TICKED step is reported as `regressed` and left alone
        unless `untick=True`. Untickng is destructive to somebody's record of finished
        work, and one command's exit status is not enough to justify doing that
        unasked — a missing binary, a moved file or a changed grep would all present
        identically;
      * a condition that did NOT RUN moves nothing in either direction and lands in
        `unknown` (rule 2). It has refuted nothing.

    Does NOT save; the caller persists."""
    steps = (task or {}).get("steps") or []
    moved = {"ticked": [], "unticked": [], "regressed": [], "unknown": []}
    for r in results or []:
        n = r.get("n")
        if r.get("status") != "ran":
            moved["unknown"].append(n)
            continue
        if r.get("ok") and not r.get("was_done"):
            ok, _err = _steps.set_done(steps, n, True, flag="exit-tick", now=now)
            if ok:
                moved["ticked"].append(n)
        elif not r.get("ok") and r.get("was_done"):
            moved["regressed"].append(n)
            if untick:
                ok, _err = _steps.set_done(steps, n, False, flag="exit-tick")
                if ok:
                    moved["unticked"].append(n)
    return moved
