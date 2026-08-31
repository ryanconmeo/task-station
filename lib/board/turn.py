# turn.py
"""THE DRIVEN TURN — one pass of the loop with no human between the steps.

Everything this composes already exists and is merged. `scan` says what may start,
`invoke` spawns a child pre-attached to its own task, `exit-tick` computes done from the
exit conditions, `grade` scores G1-G6 at A- per dimension with a retry/park budget,
`memo` carries durable correspondence, `channel` reaches a child that is still running.
What was missing is the PARENT actually running them in order: scan -> invoke ->
mechanical gate -> grade -> release, and halting at a human gate rather than retrying it.

WHY THIS IS A MODULE AND NOT A SKILL. Q4 (decided 2026-08-14) split the loop the way
this house always splits things: the ENGINE owns the deterministic primitives, the SKILL
owns the judgment. Which child may start, what state a child that stopped is in, whether
the mechanical gate is clean, whether a rejection may be retried or must park — all of
that is arithmetic over the record, and arithmetic in a prompt is arithmetic that drifts.
What grade each dimension earns is judgment, and no function here supplies it: the turn
emits the `grade` command with its dimensions unfilled.

THE MODULE IS PURE OVER TASK DICTS, exactly like `loop` and `exits`. It is handed the
population, the liveness set and the evidence; it never loads, never saves, never runs a
shell. That is what makes a turn cheap enough to run constantly — and it is the reason
`plan` can be tested against a board that does not exist.

--------------------------------------------------------------------------------------
THE SEVEN THINGS THAT WENT WRONG DRIVING THIS LOOP BY HAND, across seven children on
2026-08-19. Every one of them is a lie a turn can act on, so every one has a mechanism
here rather than a paragraph somewhere.

1. SILENT EXIT (`SILENT_EXIT`). A child finishes its work and exits saying nothing,
   because its exit conditions run against the MAIN checkout and cannot pass until its
   own PR merges. Three children did exactly this. Reading that as FAILED retries work
   that may be complete; reading it as UNKNOWN stalls the loop. It is a third state, and
   its action is to gate it WITH the missing-report finding.

2. THE HAND-BACK RAIL IS A MEMO (`report_memo`). The role report contract asked for a
   report and named no channel, so the compliant behaviour and the useless one were
   identical — a child writes a good report into a window the parent cannot see. A memo
   is durable, survives the session ending, and lands on the record the gate already
   loads. So a missing report memo is a GATE FINDING, and `invoke` now names the rail in
   the child's own prompt.

3. TREE, NOT ANCESTRY (`landed`, `landed_probe`). This repo squash-merges everything, so
   `git merge-base --is-ancestor` reports EVERY landed branch as unmerged. The failure
   direction is what makes it dangerous: a driven turn re-opens work already on main.
   The probe is an empty `git diff` between the branch and the merge target.

4. FOUR WAYS A GATE LIES, and all four are cheap to mechanise:
     * a false green on UNSTARTED work — nothing ran, so nothing may be graded;
     * an assertion satisfied by something else — `unittest discover -k <missing>` prints
       "Ran 0 tests" then "OK" and exits 0; a `tail -3` is swallowed by trailing stdout;
       a bare count substring-matches a bigger number ("5013" is inside "15013");
     * a FALSE RED from a stale INSTALLED plugin or a renamed test;
     * the squash case above.
   Hence `suite_green` (PIN A POSITIVE COUNT, never an absence), `condition_lint` (the
   shapes that lie are refused at registration) and `stale_install`.

5. SPAWN INTENT IS NOT LIVENESS (`SPAWN_FAILED`, `MANUAL`). A failed window-open still
   records the invoke and still mints a session, so a child can be "invoked" and never
   have taken a turn. Counting it as running stalls the loop; gating it grades nothing.
   Reconciling the two is what tells a re-launch apart from a wait.

6. CONCURRENCY IS EXPENSIVE HERE SPECIFICALLY (`plan`, one invoke per pass). Two
   children in flight means two version bumps and a rebase for whoever lands second;
   three means a three-way conflict. `loop_children_max` caps the total, and the turn
   spends what is left ONE CHILD AT A TIME — a stagger, not just a cap.

7. A ROUTINE NOTICE MUST NOT HOLD A TURN (see `channel.blocks_turn`). The channel's
   Stop gate fires at every turn end; holding a turn hostage for a subscription diff or a
   close mirror costs more than it delivers. The blocking rail is reserved for what
   changes what the receiver should be doing next.

8. …AND A CHILD HANDING WORK BACK IS EXACTLY THAT (`landed_work`, and the pickup rail in
   lib/board/channel.py). The two most expensive stalls this loop has had were not
   failures: #532 sat about an hour and #536 for seven, both finished, both with a live
   idle session, both with a parent polling `sessions --task <child>` and reading "busy".
   Two mechanisms, because there were two lies. WAIT no longer outranks the child's own
   evidence — an unacked report or a demonstrably green checklist takes a child out of
   WAIT whatever its liveness says. And the parent is no longer required to look: the
   terminal transition files a pickup on the parent, and the PARENT's Stop gate refuses
   to end a turn while one is unclaimed. That is finding 7's rule honoured rather than
   broken — a pickup is not bookkeeping the reader will meet anyway; it is the one fact
   the parent provably cannot get any other way while it is running.

Stdlib only. Imports `config`, `exits` and `loop`; nothing in the engine imports it back
— the CLI seam is the only caller.
"""
import json
import os
import re
import subprocess
import time

import config as _config
import exits as _exits
import gating as _gating
import loop as _loop

# ---------------------------------------------------------------- child states ----
#
# What the PARENT can say about one child right now. Eight values, and none of them is a
# synonym for another: each one has a different next action, which is the only test worth
# applying to a state vocabulary.

UNSTARTED = "unstarted"          # never invoked — there is nothing to grade
MANUAL = "manual-pending"        # the launch was handed to a human and is still waiting
SPAWN_FAILED = "spawn-failed"    # invoked, no process, no turn ever taken
RUNNING = "running"              # a live session is attached right now
REPORTED = "reported"            # stopped, and left its report on the memo ledger
DONE_PENDING_MERGE = "done-pending-merge"   # reported, and every unmet condition is a merge
SILENT_EXIT = "silent-exit"      # stopped, did work, said nothing (finding 1)
PARKED = "parked"                # the loop has stopped asking — never retried
SETTLED = "settled"              # closed; its dependents are released
CHILD_STATES = (UNSTARTED, MANUAL, SPAWN_FAILED, RUNNING, REPORTED, DONE_PENDING_MERGE,
                SILENT_EXIT, PARKED, SETTLED)

# ------------------------------------------------------------------- the actions ----
#
# The five steps of the turn, plus the three answers that are not steps. A driver reads
# `action` and runs `command`; nothing here needs a person to translate it, which is the
# whole content of "no human between the steps".

INVOKE = "invoke"        # start a child that is unblocked and unstarted
RELAUNCH = "relaunch"    # spawn intent without liveness — finding 5
WAIT = "wait"            # a child is running; the loop is working, not stuck
GATE = "gate"            # the MECHANICAL half: run its conditions, read the findings
GRADE = "grade"          # the JUDGMENT half: G1-G6 at the configured threshold
RETRY = "retry"          # rejected with budget left — the child iterates
PARK = "park"            # this does not come back to the loop
RELEASE = "release"      # accepted: close it, and its dependents unblock
ACTIONS = (INVOKE, RELAUNCH, WAIT, GATE, GRADE, RETRY, PARK, RELEASE)

# Actions that MOVE the loop. A `wait` is not progress — it is the honest report that
# somebody else is making it. Telling those apart is what makes `halt` meaningful.
PROGRESS = (INVOKE, RELAUNCH, GATE, GRADE, RETRY, PARK, RELEASE)

# ---------------------------------------------------------------------- the halts ----
#
# Why the turn stopped, when it emitted no progress action. `scan` already answers four
# of these for the WAVE; these answer it for the TURN, which is a different question:
# a wave can be `ready` while the turn can start nothing, because the budget is spent.

HALT_COMPLETE = "complete"   # every child settled — the loop is done
HALT_PARKED = "parked"       # a parked child waits for a person, not for the loop
HALT_WORKING = "working"     # children are running; come back
HALT_EMPTY = "empty"         # no children at all — the plan has not been built
HALT_BUDGET = "budget"       # work is ready and the children cap refuses it
HALT_BLOCKED = "blocked"     # unsettled children, none startable (a cycle or a dangler)

# ------------------------------------------------------------ reading the trail ----
#
# `invoke` writes the launch onto the CHILD as a `child`-kind event, and it writes the
# KIND of launch into the text, because a preview and a real launch used to write the
# identical line and one child read as two invokes. These are the two markers it uses;
# they live here rather than in the CLI seam so that the writer and the reader cannot
# drift apart, and the seam reads `MANUAL_MARK` back off this module.

LAUNCH_KIND = "child"
INVOKED_MARK = "invoked by #"
MANUAL_MARK = "MANUAL LAUNCH"
MANUAL_TAIL = "handed to a human"


def _events(task):
    raw = (task or {}).get("events")
    return [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []


def _memos(task):
    raw = (task or {}).get("memos")
    return [m for m in raw if isinstance(m, dict)] if isinstance(raw, list) else []


def _ts(rec):
    try:
        return float(rec.get("ts") or 0)
    except (TypeError, ValueError):
        return 0.0


def launches(task):
    """Every launch recorded on `task`, oldest first, as `{"ts", "manual", "text"}`.

    A MANUAL launch is one a human has to complete by hand (`--print-command`, or a
    window opener that failed and fell back to printing). It is kept separate from an
    ordinary invoke because the loop can re-launch its own failed spawn and must not
    re-launch a line somebody is about to run."""
    out = []
    for e in _events(task):
        if e.get("kind") != LAUNCH_KIND:
            continue
        text = str(e.get("text") or "")
        manual = MANUAL_MARK in text and MANUAL_TAIL in text
        if not manual and INVOKED_MARK not in text:
            continue
        out.append({"ts": _ts(e), "manual": manual, "text": text})
    return sorted(out, key=lambda r: r["ts"])


def last_launch(task):
    """The most recent launch, or None for a child nobody has invoked."""
    got = launches(task)
    return got[-1] if got else None


def report_memo(task, after=None, unacked_only=False):
    """The child's OWN hand-back memo on its own task, newest first, or None.

    `unacked_only` keeps only a report NOBODY has dispositioned. That is what the pickup
    rail turns on: an unacked report is work waiting to be picked up, while an acked one has
    already been engaged — and a child that is live again after an ack is working, not
    waiting.

    THE SENDER IS THE DISCRIMINATOR. Every memo lands on the same ledger, including the
    rejections the PARENT writes — counting one of those as the hand-back would mark
    every rejected child as having reported, which is the opposite of what the finding
    is for. A memo is the child's when it came from a session registered on this task,
    or when it declares this task as its origin."""
    own = set(str(s) for s in (task.get("sessions") or []))
    tid = task.get("id")
    best = None
    for m in _memos(task):
        if after is not None and _ts(m) < float(after):
            continue
        src, from_task = m.get("from_sid"), m.get("from_task")
        if from_task and from_task != tid:
            continue                       # somebody else's task wrote it
        if not (str(src) in own or (from_task and from_task == tid)):
            continue
        if not str(m.get("text") or "").strip():
            continue
        if unacked_only and (m.get("acks") or []):
            continue
        if best is None or _ts(m) >= _ts(best):
            best = m
    return best


# -- (c) THE RAIL WAS INVISIBLE IN ONE DIRECTION -------------------------------------
#
# Track A's rule 4 says a child hands back as a MEMO ON ITS OWN TASK, because that is
# durable, survives the session closing, and "lands where the gate looks". The first half
# was true and the second half was never implemented: the awaiting-your-ack nag fires only
# for the task the READING session is attached to, so a parent sitting on the orchestrator
# is never told that a memo arrived on a child. Measured once: a good report sat unread for
# seven hours with nothing broken and nothing lost.
#
# So the parent gets the same nag for its children's reports that it already gets for its
# own memos. Deliberately the SAME SHAPE and the same bounds as `memo_pending_brief` — a
# handful of lines, the body truncated rather than the line dropped, an overflow count, and
# the command that reads the full text. A notice that says a report exists without saying
# how to read it is half a rail.
#
# UNACKED ONLY. An acked report has been engaged; re-surfacing it every prompt is how a nag
# earns being ignored, which is the failure this codebase has had to fix four times over.

# The bounds, mirroring the memo nag's (`_shared.MEMO_PENDING_MAX` / `MEMO_LINE_MAX`) rather
# than importing them: this module stays stdlib-plus-config/exits/loop, which is what makes a
# turn testable with no store at all. Two constants that must agree by eye is the cheaper
# price than a dependency that inverts the module's direction.
CHILD_REPORT_MAX = 3        # report lines per block
CHILD_REPORT_LINE_MAX = 200  # preview chars per line


def child_reports(orch, children):
    """`[(child, memo), …]` for each child of `orch` carrying an UNACKED report memo,
    oldest report first. `children` is passed in — this module never loads."""
    tid = (orch or {}).get("id")
    out = []
    for child in (children or []):
        if not isinstance(child, dict) or _loop.parent_id(child) != tid:
            continue
        launch = last_launch(child)
        m = report_memo(child, after=(launch["ts"] if launch else None),
                        unacked_only=True)
        if m:
            out.append((child, m))
    return sorted(out, key=lambda pair: _ts(pair[1]))


def child_reports_brief(orch, children, max_items=None, line_max=None):
    """One bounded block naming the children whose reports nobody has engaged, or None.

    Rendered here rather than in the session seam because the RULE lives here: what counts
    as a child's report is `report_memo`'s sender discrimination, and a second copy of that
    in the nag path would answer differently the first time either was tuned."""
    pairs = child_reports(orch, children)
    if not pairs:
        return None
    max_items = CHILD_REPORT_MAX if max_items is None else max_items
    line_max = CHILD_REPORT_LINE_MAX if line_max is None else line_max
    shown = pairs[-max_items:]
    ref = orch.get("seq") or str(orch.get("id") or "")[:8]
    out = ["[task-station] %d CHILD report(s) on this plan await a disposition — filed on "
           "the CHILD's task, so nothing else on this session would tell you:" % len(pairs)]
    for child, m in shown:
        seq = child.get("seq")
        body = " ".join(str(m.get("text") or "").split())
        prefix = "  • #%s %s — " % (seq, m["id"][:8])
        budget = max(12, line_max - len(prefix))
        if len(body) > budget:
            body = body[:budget - 1].rstrip() + "…"
        out.append(prefix + body)
    extra = len(pairs) - len(shown)
    if extra > 0:
        out.append("  (+%d more)" % extra)
    out.append("Read one:  task-station memo show --task <child> --id <id8>   ·   "
               "then `turn --task %s` gates and grades it" % ref)
    return "\n".join(out)


def unacked(task):
    """Memos on `task` that NOBODY has dispositioned — B13's pending-ack debt.

    Twenty-two of these were outstanding on the day the loop ran, and the loop had no
    idea: an unacked memo is a fact somebody handed this task that nothing has engaged.
    It surfaces as a gate finding rather than as background noise."""
    return [m for m in _memos(task) if not (m.get("acks") or [])]


def worked_since(task, ts):
    """Did this child take a turn after `ts`?

    The evidence is anything the child itself writes: a checkpoint, a note, a memo, a
    grade, an exit-condition run. It is what separates a SILENT EXIT — worked, said
    nothing — from a SPAWN that never came up. Launch events are excluded, because the
    parent wrote those."""
    ts = float(ts or 0)
    for e in _events(task):
        if e.get("kind") == LAUNCH_KIND:
            continue
        if _ts(e) > ts:
            return True
    for m in _memos(task):
        if _ts(m) > ts:
            return True
    for g in _loop.grades(task):
        if _ts(g) > ts:
            return True
    last = _exits.last_run_ts(task)
    return bool(last and float(last) > ts)


# -- (d) LIVENESS IS NOT THE ONLY THING THAT OUTRANKS LIVENESS ------------------------
#
# An unacked report already beats a live session (see `child_state`). It is not enough on
# its own, because a report is a thing the child has to REMEMBER to file, and the two
# incidents that cost the most were both children that had demonstrably finished: #532
# for about an hour, #536 for seven. In both, the parent polled `sessions --task <child>`
# and read "busy", because a child that finishes and leaves its window open is a live
# process with nothing to do — and liveness cannot tell that apart from thinking.
#
# The child's OWN CHECKLIST can. `exits.satisfied` is already the predicate the wave scan
# releases dependent work on, and it is deliberately strict: at least one registered
# condition, EVERY registered condition MET, and no live step left both uncovered and
# unticked. A child that clears that bar has demonstrated its plan is finished against
# commands somebody wrote down in advance, which is a stronger claim than a memo saying so.
#
# FRESHNESS IS PART OF THE CLAIM. A verdict computed before this launch says nothing about
# this launch — a task whose conditions were green when the child started would otherwise
# read as finished the second it was invoked, which is the false-positive direction and
# the expensive one. So the verdict must post-date the launch that is being judged.

def landed_work(task, after=None):
    """Has this child's own checklist demonstrably gone green, and since `after`?

    The other half of "a finished child must not be waited on". `False` for a task that
    registers no conditions, that has any unmet or unrun one, that leaves a live step both
    uncovered and unticked, or whose green verdict predates the launch in question — every
    one of those is a claim nobody computed, and an uncomputed claim is not evidence."""
    if not _exits.satisfied(task):
        return False
    last = _exits.last_run_ts(task)
    if not last:
        return False
    return after is None or float(last) >= float(after)


def _reported_state(task):
    """REPORTED, or DONE_PENDING_MERGE when every unmet condition was DECLARED merge-gated.

    ONE FUNCTION BECAUSE THERE ARE TWO WAYS TO ARRIVE AT REPORTED, and until 3.45.0 only
    one of them asked the question. A child whose report is still UNACKED went through the
    first return and got the merge-gated reading; a child whose report the parent had
    already ACKED — graded once, still open, still waiting on the same merge — fell through
    to the second return and read as a plain REPORTED, indistinguishable from a child with
    ordinary red conditions. That is the same defect as the silent `exit-show`, in the
    verb that decides what the loop does next: the flag was stored, correct, and dropped on
    the path a child spends the LONGEST on, because "acked and still waiting for a human"
    is exactly the state a queued merge leaves behind.

    Nothing here softens the verdict. Both states are gated and both are gradeable; the
    difference is only which sentence the turn prints."""
    return DONE_PENDING_MERGE if _gating.pending_merge(_exits.merge_gate(task)) \
        else REPORTED


def child_state(task, live=(), worked=None):
    """Which of the eight states this child is in, right now.

    THE ORDER IS THE ARGUMENT. Closed beats everything (its dependents are already
    released). AN UNACKED REPORT BEATS LIVENESS — see below. Liveness then beats every
    stored flag, because a record survives a crash and a process does not. A park beats the
    launch trail, because a parked child must never be handed back to the loop whatever its
    trail says. Only then does the trail decide, and the trail decides by RECONCILING INTENT
    WITH EVIDENCE: a report memo means reported, other evidence means it worked and said
    nothing, no evidence at all means the spawn never came up.

    WHY THE REPORT MOVED ABOVE LIVENESS. It used to sit at the bottom, so a child that
    finished, filed its report and left its session idle in a worktree read as RUNNING, and
    the turn printed "a live session is attached — the loop is working, not stuck". That
    sentence was true and the conclusion was wrong: measured once, the work had been finished
    for SEVEN HOURS. An idle-but-alive child that has already reported was indistinguishable
    from one still thinking, and liveness cannot tell them apart because both are alive. The
    REPORT can: filing one is the child saying it is done.

    UNACKED, specifically. An acked report has already been engaged, so a child that is live
    again after one is working rather than waiting — otherwise a graded-and-retried child
    would be gated forever on the report it filed last time round. And a PARKED child is
    never dragged back by its report: that rule predates this one and outranks it.

    AND A GREEN CHECKLIST COUNTS THE SAME AS A REPORT (`landed_work`). Filing one is the
    child SAYING it is done; a satisfied set of exit conditions is the record PROVING it,
    against commands written down before the work started. A child can forget the first and
    cannot fake the second, so either one takes it out of WAIT. The gate still asks for the
    report — a proven-done child that filed nothing gets the `no-report` finding, exactly as
    a SILENT_EXIT does, because a parent that cannot read what happened has not been handed
    the work."""
    if _loop.is_closed(task):
        return SETTLED
    launch = last_launch(task)
    if launch and not _loop.parked(task) \
            and (report_memo(task, after=launch["ts"], unacked_only=True)
                 or landed_work(task, after=launch["ts"])):
        return _reported_state(task)
    if live and task.get("seq") in set(live):
        return RUNNING
    if _loop.parked(task):
        return PARKED
    if not launch:
        return UNSTARTED
    if report_memo(task, after=launch["ts"]):
        return _reported_state(task)
    did = worked_since(task, launch["ts"]) if worked is None else bool(worked)
    if did:
        return SILENT_EXIT
    return MANUAL if launch["manual"] else SPAWN_FAILED


# ------------------------------------------------------------ the mechanical gate ----
#
# Finding 4, in code. Every one of these is a way a gate reports something other than the
# state of the work, and the shape of the fix is always the same: assert a POSITIVE FACT
# with a number in it, never the absence of a bad one.

_RAN_RE = re.compile(r"^Ran (\d+) test", re.M)


def ran_count(output):
    """The N from unittest's `Ran N tests` line, or None when there is no such line.

    None and 0 are different answers and must stay different: 0 is a suite that ran
    nothing, None is output that never got as far as saying."""
    hits = _RAN_RE.findall(str(output or ""))
    return int(hits[-1]) if hits else None


def suite_green(output, minimum=1):
    """`(ok, why)` for a test-suite run — the POSITIVE COUNT assertion.

    `unittest discover -k <a name nothing matches>` prints "Ran 0 tests", then "OK", and
    exits 0. So does a renamed test class. An assertion on OK alone is therefore
    satisfied by the ABSENCE of the very test it was written to protect, which is the
    most expensive lie in this list: it reports green about work nobody did.

    UNCOUNTABLE IS NEVER ZERO, and it is never green either — output with no count at
    all (an import error, a crashed interpreter) fails with its own reason rather than
    being read as a pass."""
    text = str(output or "")
    n = ran_count(text)
    if n is None:
        return False, ("no 'Ran N tests' line in the output — uncountable is never zero, "
                       "and never green")
    if n < int(minimum):
        return False, ("Ran %d tests, and %d is below the %d this gate pins — a count of "
                       "0 with an OK is what a missing test looks like"
                       % (n, n, int(minimum)))
    if "FAILED" in text:
        return False, "Ran %d tests and the run FAILED" % n
    if "OK" not in text:
        return False, "Ran %d tests and the run never said OK" % n
    return True, "Ran %d tests, OK" % n


def landed(diff_output):
    """Has the branch's content reached the merge target? TREE, NOT ANCESTRY.

    An EMPTY tree diff is the whole answer. `git merge-base --is-ancestor` is the
    intuitive probe and it is wrong here: this repo squash-merges every branch, so the
    branch commit is never an ancestor of main and the ancestry probe calls EVERY landed
    branch unmerged. The failure direction is what makes it unacceptable in a driven
    turn — it re-opens work that already shipped."""
    return not str(diff_output or "").strip()


# -- REACHING A CHILD THAT IS ALIVE AND IDLE — adopt, do not build --------------------
#
# `channel` can reach a child that is TAKING TURNS: transport is the turn boundary, and the
# Stop hook can refuse to let a turn pass until an order is read. That is the right design
# for a child mid-flight and it has one hole, found live: an IDLE session never reaches a
# turn boundary, so it never reads anything. And `channel` offers reach / orders /
# stand-down / settle / deny — a parent can stand a child DOWN but cannot hand it WORK.
#
# So the parent's window was the only way in, and the mechanism that actually worked was the
# HARNESS's SendMessage — which is how a real parent woke a real idle child on 2026-08-21.
# That is decision 382 (BUILD vs ADOPT) confirmed by a second live case: the harness already
# owns cross-session delivery, an inbound socket here would be a second one, and the loop's
# job is to NAME the working path rather than re-implement it.
#
# It is printed as a tool call and never as a shell command, because that is what it is. The
# loop cannot run it; the reader can.

def reach_command(child_ref, seq=None):
    """How a parent reaches a child that is alive right now — the harness tool call, as
    text. `channel orders` is named too, because a child still taking turns reads that
    without a person in the loop."""
    return ("SendMessage(to: \"<the child's session or agent name>\", message: \"…\") — "
            "the harness rail, and the only one an IDLE session ever sees. A child still "
            "taking turns also reads `task-station channel orders --task %s`, which lands "
            "at its next turn boundary." % (child_ref if seq is None else seq))


def landed_probe(branch, merge="origin/main"):
    """The command whose EMPTY output means `branch` has landed on `merge`."""
    return "git diff --stat %s %s" % (merge, branch)


# The shapes that lie. Each one was observed on a real registered condition, and each is
# refused at REGISTRATION time (B7) rather than diagnosed months later from a green board.
_TAIL_RE = re.compile(r"\|\s*tail\b")
_BARE_COUNT_RE = re.compile(r"^\d[\d,]*$")
_ABSENCE_RE = re.compile(r"^(no|none|not|zero|nothing|0)\b", re.I)

# A PATH TEST SHORT-CIRCUITING THE COMMAND BEHIND IT. Observed live on 2026-08-21: step 5 of
# #531 was registered as `test -f skills/judge/SKILL.md && python3 -m unittest …`. The skill
# was renamed judge -> grade, the path test failed, `&&` short-circuited, THE TESTS NEVER RAN
# — and the command produced EMPTY OUTPUT, so nothing in the transcript said which half
# failed or why. `scan --run` reported a closed, fully-graded, released track at 4 of 5
# conditions met, and unattended the loop would have refused to release it and parked
# finished work: a false red with no diagnostic, which is the worst of the four ways a gate
# lies because there is nothing to read.
#
# WHY THIS SHAPE AND NOT EVERY `&&`. `cd <dir> && <cmd>` is the ordinary way to point a
# condition at a checkout, and `cd` FAILS LOUDLY — it prints its own complaint, so the
# transcript says what happened. A FILE TEST prints nothing at all; it only sets an exit
# status. That silence is the defect, so the check is narrow: a file-test operator, joined by
# `&&`, at a command position. String tests (`-z`, `-n`) are excluded — they read a variable
# the same command just set, which no rename can move — and `;` is excluded because it does
# not short-circuit: the command runs and prints whatever it prints.
_FILE_TEST_OPS = "efdsrwxhLpSbcgkuOGN"
_PATH_TEST_AND_RE = re.compile(
    r"(?:\A|[;&|]|\()\s*!?\s*(?:test|\[\[?)\s+!?\s*-[%s]\b[^&|;]*&&" % _FILE_TEST_OPS)


def condition_lint(cmd, expect):
    """The lying shapes in one exit condition, as findings. Empty when it is honest.

    Static: it reads the command, it never runs it. Registering a condition must not have
    side effects, and every shape below is visible without executing anything."""
    out = []
    cmd = str(cmd or "")
    if _PATH_TEST_AND_RE.search(cmd):
        out.append({"code": "path-test-and", "dim": "G1",
                    "line": "a path test guards the command with `&&`. Rename or move that "
                            "path and the test fails, `&&` short-circuits, the command "
                            "NEVER RUNS, and the output is EMPTY — so the condition goes "
                            "red and nothing says which half failed. Drop the guard and "
                            "let the command speak for itself, or make the path part of "
                            "what the command asserts."})
    if _TAIL_RE.search(cmd):
        out.append({"code": "tail-swallow", "dim": "G1",
                    "line": "the command ends in a `tail` — one extra line of trailing "
                            "stdout swallows the line the assertion is about, and the "
                            "gate goes red for a reason having nothing to do with the "
                            "work. Filter for the line (`rg '^(OK|FAILED|Ran …)'`) "
                            "instead of taking the last few."})
    for raw in (expect or []):
        e = str(raw).strip()
        if not e:
            continue
        if _BARE_COUNT_RE.match(e):
            out.append({"code": "bare-count", "dim": "G2",
                        "line": "expects the bare count %r — a substring of every bigger "
                                "number (%r is inside %r) and of any line that happens to "
                                "contain it. Pin the count with the words around it "
                                "(\"Ran %s tests\")." % (e, e, "1" + e, e)})
        elif _ABSENCE_RE.match(e):
            out.append({"code": "absence-assertion", "dim": "G1",
                        "line": "expects %r, which asserts an ABSENCE. Nothing printed at "
                                "all satisfies it, so it passes hardest exactly when the "
                                "command is broken. Pin a positive count instead." % e})
    return out


def shell_syntax_error(cmd):
    """The shell's own complaint about `cmd`, or None when it parses. NEVER EXECUTES.

    B7. The P7A registration shipped a command truncated at a quote: it was stored,
    looked registered, and could not run. `bash -n` reads the script and refuses to
    execute it, so this costs a parse and has no side effects at all. Fail-OPEN: no bash,
    or a bash that cannot be launched, is not evidence that a command is broken."""
    text = str(cmd or "").strip()
    if not text:
        return None
    try:
        p = subprocess.run(["bash", "-n"], input=text, capture_output=True,
                           text=True, timeout=10)
    except Exception:                                   # noqa: BLE001
        return None
    if p.returncode == 0:
        return None
    return " ".join((p.stderr or p.stdout or "shell syntax error").split()) or None


# B9: a recorded number must carry the command that measured it. Three digits or more,
# because that is what a gate number looks like (5013, 4471, 236) and what a step number,
# a version part or a phase index does not.
_NUMBER_RE = re.compile(r"\d{3,}")
_SKIP_BEFORE = re.compile(r"(#|\btask\s+|\bpr\s+|\bv|\.)$", re.I)


def _is_gate_number(text, match):
    """Is this digit run a MEASUREMENT, or an identifier that happens to be numeric?"""
    n = match.group(0)
    before = text[:match.start()]
    after = text[match.end():]
    if _SKIP_BEFORE.search(before):
        return False                       # #444, task 444, v444, 3.444
    if after[:1] == "." or after[:1] == "-":
        return False                       # a version or a date
    if len(n) == 4 and 1900 <= int(n) <= 2099:
        return False                       # a year
    return True


def number_without_command(steps):
    """Steps carrying a gate NUMBER and no command that measures it (B9).

    Phase 4's count went 58 -> 81 in a plan nobody re-ran, and the drift was invisible
    because the number was prose. The same number with its measuring command says so the
    next time anybody looks — which is exactly the difference between the 3.0.0
    migration's claims (honest for a year) and its steps (thirteen silently became
    true)."""
    out = []
    for i, step in enumerate(steps or [], 1):
        if not isinstance(step, dict) or _exits.has_condition(step):
            continue
        text = str(step.get("text") or "")
        for m in _NUMBER_RE.finditer(text):
            if not _is_gate_number(text, m):
                continue
            out.append({"code": "number-without-command", "dim": "G2", "step": i,
                        "line": "step %d records %s and no command that measures it — "
                                "a number in prose rots silently. `exit-add --task <ref> "
                                "--step %d --cmd '<the measuring command>' --expect "
                                "'%s'` makes it recompute."
                                % (i, m.group(0), i, m.group(0))})
            break
    return out


def stale_install(repo_version, installed_version):
    """A finding when the INSTALLED plugin is not the version under test (B14's half).

    Finding 4's FALSE RED, and the one that wastes the most time because the diagnosis
    looks like a real failure: the suite exercises the repo, the hooks and the MCP server
    exercise whatever `/plugin update` last cached, and a gate reading the second while
    grading the first reports red about work that is correct."""
    repo, got = str(repo_version or "").strip(), str(installed_version or "").strip()
    if not repo or not got or repo == got:
        return None
    return {"code": "stale-install", "dim": "G1",
            "line": "the INSTALLED plugin is %s and the tree under test is %s — a red "
                    "from a hook or the MCP server is the stale install talking, not the "
                    "work. `/plugin update` before believing it." % (got, repo)}


def repo_version(root=None):
    """The version in `.claude-plugin/plugin.json`, or None. Fail-open."""
    root = root or os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    try:
        with open(os.path.join(root, ".claude-plugin", "plugin.json"),
                  encoding="utf-8") as f:
            return str(json.load(f).get("version") or "") or None
    except Exception:                                   # noqa: BLE001
        return None


def _version_key(v):
    """Sort a dotted version NUMERICALLY. `sorted()` on the strings puts 3.9.0 after
    3.12.0, which would report the wrong installed version — and reporting the wrong one
    on a probe whose whole job is catching a stale install is worse than not probing."""
    out = []
    for part in str(v or "").split("."):
        digits = "".join(c for c in part if c.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def installed_version(home=None):
    """The newest task-station version cached under the plugins dir, or None. Fail-open —
    a machine with no installed copy is not a finding, it is a developer's checkout."""
    base = os.path.join(os.path.expanduser(
        home or os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")), "plugins", "cache")
    try:
        found = []
        for owner in os.listdir(base):
            path = os.path.join(base, owner, "task-station")
            if os.path.isdir(path):
                found += [d for d in os.listdir(path)
                          if os.path.isdir(os.path.join(path, d))]
        return max(found, key=_version_key) if found else None
    except Exception:                                   # noqa: BLE001
        return None


def gate(task, live=(), worked=None, landed=None, installed=None, version=None):
    """THE MECHANICAL HALF, as one structured answer. Runs nothing; reads the record.

    `{"seq", "state", "findings", "clean", "gradeable"}`. Every finding names the rubric
    dimension it lands on, so the judge is handed the dimension rather than left to map
    prose onto one.

    GRADEABLE IS NOT THE SAME AS CLEAN. Clean means the gate found nothing; gradeable
    means there is work to grade at all. Unstarted work is gradeable-false and that is
    the point — a grade on a child that never ran is the cheapest possible false green.

    `landed` is the answer to `landed_probe` for the child's branch: True (its content is
    on the merge target), False (it is not), or None (nobody probed). WHEN IT IS NOT
    TRUE, UNMET CONDITIONS ARE NOT A FAILURE — they are the pre-merge state finding 1
    describes, because the conditions run against the MAIN checkout and cannot go green
    until the child's own work lands there. Only a landed branch may be called failed."""
    state = child_state(task, live=live, worked=worked)
    findings = []
    if state == UNSTARTED:
        findings.append({"code": "unstarted", "dim": "G1",
                         "line": "nothing has been invoked on this task, so there is "
                                 "nothing to grade. A gate that scores unstarted work is "
                                 "a false green about work nobody did."})
    # THE FINDING IS KEYED ON THE FACT, NOT ON THE STATE NAME. SILENT_EXIT used to be the
    # only way to arrive here with no report, so testing the state was the same as testing
    # whether a report existed. It stopped being the same the moment a GREEN CHECKLIST could
    # also take a child out of WAIT (`landed_work`): such a child reads REPORTED — its next
    # action is gate-then-grade, which is what a state is for — while having filed nothing a
    # parent can read. Asking the question directly keeps SILENT_EXIT's behaviour identical
    # and stops the new path from buying a quiet pass on the hand-back rail.
    _launch = last_launch(task)
    _has_report = bool(_launch and report_memo(task, after=_launch["ts"]))
    if state in (SILENT_EXIT, REPORTED, DONE_PENDING_MERGE) and not _has_report:
        findings.append({"code": "no-report", "dim": "G4",
                         "line": "the child did the work and left no report memo on "
                                 "this task. The hand-back rail is a MEMO — "
                                 "durable, and on the record the gate loads — so a "
                                 "report written anywhere else is a report the parent "
                                 "cannot read. `memo send --task %s --text '<report>'`."
                                 % (task.get("seq") or "<ref>")})
    if state in (SPAWN_FAILED, MANUAL):
        findings.append({"code": "spawn-unreconciled", "dim": "G6",
                         "line": "an invoke is recorded and no session ever took a turn "
                                 "— a failed window-open still mints a session and still "
                                 "writes the event, so intent is not liveness. Re-launch "
                                 "rather than grade."})
    ex_state = _exits.state(task)
    if ex_state == _exits.NONE:
        findings.append({"code": "conditions-none", "dim": "G1",
                         "line": "no step carries an exit condition, so DONE here is "
                                 "asserted rather than computed. A task that has checked "
                                 "nothing can never settle, and can never release what "
                                 "depends on it."})
    elif ex_state == _exits.UNKNOWN:
        findings.append({"code": "conditions-unknown", "dim": "G1",
                         "line": "conditions are registered and have never run. A "
                                 "condition that did not run refutes nothing — "
                                 "`exit-tick --task %s` is the missing step."
                                 % (task.get("seq") or "<ref>")})
    elif ex_state == _exits.UNMET and _exits.merge_gate(task)["all_merge_gated"]:
        mg = _exits.merge_gate(task)
        findings.append({"code": "merge-gated", "dim": "G1",
                         "line": "DONE PENDING MERGE — %d of %d unmet condition(s) were "
                                 "DECLARED as reading the merge target, and none of the "
                                 "unmet ones can go green until this work lands there. "
                                 "This is not a red and it is not clean either: the work "
                                 "is finished and gradeable, and the release waits for the "
                                 "merge. Nobody in the loop can perform that merge."
                                 % (mg["merge_gated"], mg["unmet"])})
    elif ex_state == _exits.UNMET:
        if landed is True:
            findings.append({"code": "conditions-unmet", "dim": "G1",
                             "line": "the branch has landed and conditions are still "
                                     "unmet — this is a real red."})
        else:
            findings.append({"code": "pre-merge", "dim": "G1",
                             "line": "conditions are unmet, and the branch is %s. They "
                                     "run against the MAIN checkout, so a child's own "
                                     "work cannot turn them green until it merges: this "
                                     "is PRE-MERGE, not failed. Probe with `%s` — empty "
                                     "output means landed (tree, never ancestry: this "
                                     "repo squash-merges)."
                                     % ("not on the merge target" if landed is False
                                        else "unprobed",
                                        landed_probe("<branch>"))})
    for step in (task.get("steps") or []):
        cond = _exits.condition(step) if isinstance(step, dict) else None
        if cond:
            findings += condition_lint(cond["cmd"], cond["expect"])
    findings += number_without_command(task.get("steps") or [])
    debt = unacked(task)
    if debt:
        findings.append({"code": "pending-acks", "dim": "G4",
                         "line": "%d memo(s) on this task have been dispositioned by "
                                 "nobody. A fact handed to a task and never engaged is "
                                 "the same as one never sent." % len(debt)})
    stale = stale_install(version, installed)
    if stale:
        findings.append(stale)
    return {"seq": task.get("seq"), "state": state, "findings": findings,
            "clean": not findings,
            "gradeable": state in (REPORTED, DONE_PENDING_MERGE, SILENT_EXIT,
                                   PARKED, SETTLED)}


# ------------------------------------------------------------- rejection and park ----
#
# Finding 2's other half. A verdict recorded on the task and nowhere else is a verdict the
# child cannot read — and the child is a session that will not be typed into again, so the
# rail has to be one that survives its window closing.


def rejection_memo(v, ref=None, note=None, findings=None):
    """The rejection, as the memo text the child reads.

    NAMES THE DIMENSION AND ITS GRADE, and keeps the two ways of not passing apart: a
    dimension BELOW the threshold is the child's work to redo, an UNGRADED one is the
    judge's work to finish. "Rejected" on its own tells a child nothing it can act on."""
    head = "GATE REJECTED — task #%s" % (ref if ref is not None else "?")
    lines_ = [head]
    for key, grade in (v.get("failed") or []):
        lines_.append("  below %s — %s %s: %s"
                      % (v.get("threshold"), key, _loop.DIMENSION_TITLES.get(key, key),
                         grade))
    if v.get("missing"):
        lines_.append("  ungraded (the judge has work left): %s"
                      % ", ".join("%s %s" % (k, _loop.DIMENSION_TITLES.get(k, k))
                                  for k in v["missing"]))
    if note:
        lines_.append("  %s" % note)
    for f in (findings or []):
        lines_.append("  gate finding [%s] %s" % (f.get("dim") or "?", f.get("line")))
    lines_.append("  Fix the named dimension, then hand back a report AS A MEMO ON THIS "
                  "TASK — the gate reads this ledger, not your window.")
    return "\n".join(lines_)


def park_memo(reason, why, ref=None):
    """The park, as the memo text. Deliberately says NOTHING about iterating.

    A park is the loop declining to ask again — most often because the decision is not
    the loop's to make. Text that hinted at another attempt would invite exactly the one
    thing a park exists to prevent."""
    return ("GATE PARKED (%s) — task #%s\n  %s\n  This does not come back to the loop. "
            "It waits for a person." % (reason, ref if ref is not None else "?", why))


def retry_decision(task, v, retry_max=None, park=None):
    """`{"do", "reason", "left"}` — may this rejection be handed back, or must it park?

    THE ORDER IS THE POLICY. An explicit park wins, because the judge has just said this
    is not the loop's to solve. An EXISTING park wins next: a parked child is never
    retried, whatever its grade history says. Acceptance releases. Only then does the
    budget decide, and when it is spent the answer is a park with a named reason rather
    than one more attempt nobody expects to work.

    A HUMAN GATE IS A PARK WITH BUDGET LEFT, and that is the whole reason the taxonomy
    exists: iterating cannot resolve a decision that was never the loop's to make."""
    retry_max = _config.loop_retry_max() if retry_max is None else int(retry_max)
    left = _loop.retries_left(task, retry_max)
    if park:
        return {"do": PARK, "reason": park, "left": left}
    already = _loop.parked(task)
    if already:
        return {"do": PARK, "reason": already, "left": left}
    if (v or {}).get("accepted"):
        return {"do": RELEASE, "reason": None, "left": left}
    if left <= 0:
        return {"do": PARK, "reason": "retries-exhausted", "left": left}
    return {"do": RETRY, "reason": None, "left": left}


# ------------------------------------------------------------------- the turn ----


def _ref(task):
    return task.get("seq") if task.get("seq") is not None else (task.get("id") or "")[:8]


def _act(action, task, why, command, **extra):
    out = {"action": action, "seq": task.get("seq"), "id": task.get("id"),
           "title": task.get("title"), "why": why, "command": command}
    out.update(extra)
    return out


def _grade_command(child):
    dims = " ".join("--dim %s=?" % k for k in _loop.DIMENSION_KEYS)
    return ("task-station grade --task %s %s --note '<the judgement>'"
            % (_ref(child), dims))


def plan(orch, children, live=(), resolve=None, cap=None, retry_max=None, worked=None,
         ask=None, threshold=None):
    """ONE TURN, as an ordered agenda. Runs the scan itself; writes nothing.

    THE ORDER OF THE AGENDA IS LOAD-BEARING. What came back is gated FIRST, because
    grading a finished child can release a wave and hands its slot back to the budget;
    invoking first spends the slot the gate was about to return. Then the spawns that
    never came up, then one new child, then the waits.

    ONE INVOKE PER PASS, and this is the stagger rather than the cap. `loop_children_max`
    bounds how many children may be live at once; spending the whole remaining budget in
    a single pass is how three children end up rebasing each other's version bump. A wave
    of three therefore lands one child per turn.

    `halt` is set only when the turn emitted NO PROGRESS — and it says which of the six
    reasons, because "nothing to do" and "nothing I CAN do" call for opposite responses.
    """
    children = [c for c in (children or []) if isinstance(c, dict)]
    population = children + ([orch] if orch else [])
    if resolve is None:
        by_id = {t.get("id"): t for t in population}
        resolve = by_id.get
    live = set(live or ())
    report = _loop.scan(children, resolve, live=live)
    rows = {r["seq"]: r for r in report["rows"]}
    retry_max = _config.loop_retry_max() if retry_max is None else int(retry_max)
    threshold = threshold or _config.loop_accept_threshold()

    states, gates, actions = {}, {}, []
    back, relaunch, waits, ready = [], [], [], []
    for child in sorted(children, key=lambda c: (c.get("seq") is None, c.get("seq"))):
        seq = child.get("seq")
        st = child_state(child, live=live,
                         worked=(worked or {}).get(seq) if worked else None)
        states[seq] = st
        if st in (REPORTED, DONE_PENDING_MERGE, SILENT_EXIT):
            back.append(child)
        elif st in (SPAWN_FAILED, MANUAL):
            relaunch.append(child)
        elif st == RUNNING:
            waits.append(child)
        elif st == UNSTARTED and rows.get(seq, {}).get("ready"):
            ready.append(child)

    # THE CAP COUNTS WORKING CHILDREN, NOT LIVE PROCESSES — and it has to be computed
    # HERE, after the states, for that to be possible.
    #
    # `children_budget` counts process liveness, deliberately: a stored flag survives a
    # crash, so a cap counting records lets one crashed child spend a slot forever. That
    # reasoning is still right and is not touched. What it cannot see is the OTHER
    # direction — a child that finished, filed its report and left its window open is a
    # live process with nothing left to do, and it holds a slot for as long as somebody
    # leaves the window open. MEASURED: #532 sat "running" for hours with its work done,
    # and the orchestrator had to --force past the cap three times to keep the loop moving.
    # A cap that silently shrinks is worse than a smaller cap, because nobody configured it.
    #
    # So the budget is handed the same reconciliation every other answer here uses: a child
    # whose state is not RUNNING is not spending a slot, whatever its process is doing.
    # Liveness still decides for everything the states cannot speak about — a live seq that
    # is not a child of this orchestrator is passed through untouched, and
    # `children_budget` filters it out on its own.
    working = {q for q in set(live or ()) if states.get(q, RUNNING) == RUNNING}
    budget = _loop.children_budget(orch, population, live=working, cap=cap)

    # (1) what came back — the mechanical gate, then the judgement it feeds.
    for child in back:
        g = gate(child, live=live)
        gates[child.get("seq")] = g
        accepted = [e for e in _loop.grades(child) if e.get("accepted")]
        if accepted:
            actions.append(_act(
                RELEASE, child,
                "accepted at %s and still open — closing it settles it, and a settled "
                "predecessor releases everything that depends on it" % threshold,
                "task-station done --task %s" % _ref(child)))
            continue
        # A CHILD THAT IS STILL ALIVE GETS THE REACH PATH PRINTED WITH ITS GATE. It has
        # reported, so it is being gated rather than waited on — but its session is sitting
        # there, and the parent's next move after grading is usually to hand it the next
        # thing. `channel` cannot do that (see `reach_command`), so the harness rail is
        # named here, where the parent is already looking.
        st = states.get(child.get("seq"))
        alive = child.get("seq") in live
        why = ("stopped as %s — run its conditions before reading its report, because a "
               "report is not evidence" % g["state"])
        if st == DONE_PENDING_MERGE:
            why = ("DONE PENDING MERGE — it reported, and every unmet condition was "
                   "declared as reading the merge target, so nothing in the loop can turn "
                   "them green. Grade the work; the release waits for a human to merge")
        elif alive:
            why = ("reported and STILL ALIVE — an idle session is not a working one, and "
                   "an unacked report outranks liveness. Gate it rather than wait on it")
        actions.append(_act(
            GATE, child, why,
            "task-station exit-tick --task %s" % _ref(child),
            findings=g["findings"], state=g["state"], probe=landed_probe("<branch>"),
            reach=(reach_command(_ref(child), seq=_ref(child)) if alive else None)))
        d = retry_decision(child, {"accepted": False}, retry_max=retry_max)
        if d["do"] == PARK:
            actions.append(_act(
                PARK, child,
                "the retry budget is spent (%d graded attempt(s), loop_retry_max=%d) — "
                "one more pass is not a plan"
                % (_loop.attempts(child), retry_max),
                "task-station grade --task %s --park %s --why '<what is left and who "
                "owns it>'" % (_ref(child), d["reason"])))
        else:
            actions.append(_act(
                GRADE, child,
                "grade G1-G6 at %s per dimension — %d retr%s left before this must park"
                % (threshold, d["left"], "y" if d["left"] == 1 else "ies"),
                _grade_command(child), retries_left=d["left"]))

    # (2) spawn intent without liveness — finding 5.
    for child in relaunch:
        st = states[child.get("seq")]
        actions.append(_act(
            RELAUNCH, child,
            "an invoke is on the trail and nothing ever ran (%s) — the window-open "
            "failed or is still in somebody's hands; grading it would grade nothing" % st,
            "task-station invoke --task %s --from %s --role implementer --ask '%s'"
            % (_ref(child), _ref(orch) if orch else "<orch>", ask or "<the request>")))

    # (3) one new child, if the budget has room for it.
    if ready and not budget["over"]:
        child = ready[0]
        actions.append(_act(
            INVOKE, child,
            "unblocked and unstarted, and %d of %d child slot(s) are in use — one invoke "
            "per pass, because two children in flight is two version bumps and a rebase"
            % (len(budget["running"]), budget["max"]),
            "task-station invoke --task %s --from %s --role implementer --ask '%s'"
            % (_ref(child), _ref(orch) if orch else "<orch>", ask or "<the request>")))

    # (4) the honest report that somebody else is making progress.
    #
    # AND THE COMMAND IS NO LONGER A POLL. It used to be `scan`, which is how a parent
    # ended up asking the same question every few minutes and reading "busy" about a child
    # that had finished — the loop's most expensive habit, twice over. A WAIT here now
    # means the child has neither filed a report NOR turned its checklist green, i.e. there
    # is genuinely nothing yet; and when either of those changes the child files a PICKUP,
    # which the parent's own Stop gate will not let a turn end on. So the honest next step
    # is to do something else and be told, not to look again.
    for child in waits:
        # AND THE SECOND HALF OF THAT SENTENCE IS A TEST SOME CHILDREN CANNOT SIT. "Nor
        # turned its exit conditions green" reads as a thing the child is failing to do —
        # but when every unmet condition was DECLARED as reading the merge target, turning
        # them green is not in the child's power at all, and on a night when the one human
        # who may merge is asleep that is EVERY in-flight child at once. The state stays
        # RUNNING (see `gating.wait_note`: a live child that has not reported is still
        # working, and conditions are registered before the work is done, so promoting on
        # a red gated condition would call a child done on its first minute). What changes
        # is that the reader is told which of the two reds this is.
        gated = _gating.wait_note(_exits.merge_gate(child))
        why = ("a live session is attached AND it has neither reported nor turned its exit "
               "conditions green — the loop is working, not stuck")
        if gated:
            why = ("a live session is attached and it has not reported yet — the loop is "
                   "working, not stuck. %s" % gated)
        actions.append(_act(
            WAIT, child, why,
            "task-station pickup list --task %s" % (_ref(orch) if orch else _ref(child)),
            reach=reach_command(_ref(child), seq=_ref(child))))

    parked = [c for c in children if states.get(c.get("seq")) == PARKED]
    halt = None
    if not [a for a in actions if a["action"] in PROGRESS]:
        if report["stop"] == _loop.EMPTY:
            halt = HALT_EMPTY
        elif report["stop"] == _loop.COMPLETE:
            halt = HALT_COMPLETE
        elif ready and budget["over"]:
            halt = HALT_BUDGET
        elif parked:
            halt = HALT_PARKED
        elif waits:
            halt = HALT_WORKING
        else:
            halt = HALT_BLOCKED
    return {"generated_ts": time.time(), "scan": report, "actions": actions,
            "halt": halt, "budget": budget, "states": states, "gates": gates,
            "parked": [{"seq": _ref(c), "reason": _loop.parked(c)} for c in parked],
            "threshold": threshold,
            "retry_max": retry_max,
            "orch": _ref(orch) if orch else None}


_HALT_LINE = {
    HALT_COMPLETE: "COMPLETE — every child is settled. The loop is done.",
    HALT_EMPTY: "EMPTY — this orchestrator has no children. The plan has not been built "
                "yet; `decompose` builds it.",
    HALT_WORKING: "WORKING — children are running. Nothing to start, nothing stuck.",
    HALT_PARKED: "PARKED — what remains is waiting for a person, not for the loop.",
    HALT_BLOCKED: "BLOCKED — unsettled children and none startable. The scan names what "
                  "holds each one.",
}


def lines(p):
    """The text render of a turn — the same object `--json` prints, so the two cannot
    disagree about what was computed."""
    out = []
    scan = p.get("scan") or {}
    totals = scan.get("totals") or {}
    out.append("Turn — #%s  ·  %d child(ren): %d settled · %d ready · %d running · "
               "%d parked"
               % (p.get("orch"), totals.get("total", 0), totals.get("settled", 0),
                  totals.get("ready", 0), totals.get("running", 0),
                  totals.get("parked", 0)))
    b = p.get("budget") or {}
    out.append("  budget: %d of %d child slot(s) in use (loop_children_max=%s) · "
               "threshold %s · loop_retry_max=%s"
               % (len(b.get("running") or []), b.get("max", 0), b.get("max", 0),
                  p.get("threshold"), p.get("retry_max")))
    for a in p.get("actions") or []:
        out.append("  %-8s #%-4s %s" % (a["action"].upper(), a["seq"], a["title"]))
        out.append("      why: %s" % a["why"])
        out.append("      run: %s" % a["command"])
        if a.get("reach"):
            out.append("      reach: %s" % a["reach"])
        for f in (a.get("findings") or []):
            out.append("      finding [%s] %s: %s"
                       % (f.get("dim") or "?", f.get("code"), f.get("line")))
    halt = p.get("halt")
    if halt == HALT_BUDGET:
        b = p.get("budget") or {}
        out.append("  HALT: BUDGET — work is ready and %d of %d child slot(s) are in "
                   "use. Raise loop_children_max, or wait for one to finish."
                   % (len(b.get("running") or []), b.get("max", 0)))
    elif halt:
        out.append("  HALT: %s" % _HALT_LINE.get(halt, halt))
    if p.get("parked"):
        out.append("  parked: %s — a parked child is never handed back to the loop."
                   % ", ".join("#%s (%s)" % (r["seq"], r["reason"])
                               for r in p["parked"]))
    return out
