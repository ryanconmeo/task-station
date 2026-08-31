#!/usr/bin/env python3
"""Settle #602's exit condition — THE HAND-BACK MEMO A CHILD ACTUALLY FILES IS DETECTED —
against the MERGE TARGET, so a tick means "this landed where every session's gate reads it"
and not "it works in the branch that says so".

HOW THIS IS RUN, and why it is not run any other way:

    git -C <main checkout> fetch -q origin main
    git -C <main checkout> show origin/main:scripts/prove_report_memo_detected.py \\
        | python3 - --repo <main checkout> --step 1

THE JUDGE COMES OUT OF origin/main, AND SO DOES EVERYTHING IT JUDGES — the shape #598's
judge established, kept deliberately identical. An exit condition that resolves a worktree
passes on work that never merged, which is the one rot shape that fails in the DANGEROUS
direction: it reports done for something no other session can see. Piping the judge from
`git show origin/main:` means a branch cannot supply its own judge — before the merge the
path does not exist there, `git show` writes nothing, python reads an empty program, and
NOTHING IS PRINTED, so every expected substring is missing and the condition is red.
THERE IS NO REDIRECTION FLAG. `--repo` names the checkout whose `origin/main` is the merge
target; it cannot be pointed at a working tree, because the only thing this reads is
`git archive origin/main`, an explicit remote-tracking ref.

WHAT IT ASSERTS, AND WHY IT DISCRIMINATES ON THE RULE RATHER THAN ON A LINE BEING PRESENT.
A grep for the new branch would go green on a branch that is never reached. This
MATERIALISES THE WHOLE TREE and runs the SHIPPED READER — the real `turn.child_state`,
`turn.gate` and `turn.plan` — over task dicts in the exact shape the live record carries,
including the one that caused the bug: a memo with `from_sid=None` and `from_task=None`,
which is what `memo send --task <n> --text '<report>'` writes when a model types it, there
being no session id for a typed command to stamp.

BOTH DIRECTIONS, because replacing a false positive with a false negative makes the rail
useless. Two checks assert the fixed direction (an unstamped hand-back is a report, and the
parent's line stops saying otherwise). TEN CONTROLS assert the finding still fires: no memo
at all, the gate's own rejection and park texts, a routine lifecycle notice, a memo from
before this launch, a memo from another task, a memo naming a foreign session, an empty one
— and a stamped hand-back, which must keep working exactly as it did. Reverting only the
changed logic must fail the two and pass all ten.

NOTHING TOUCHES THE REAL STORE: this step reads nothing but the materialised tree and
in-memory dicts, and pins TASK_STATION_HOME / CLAUDE_CONFIG_DIR / XDG_STATE_HOME at a
throwaway directory anyway — the same three-way pin `tests/conftest.py` uses, because
pinning only the first lets a fallback reach ~/.claude.

Exit status is 0 whatever it finds. The condition is decided by the SUBSTRINGS, and a
non-zero exit would make "the check could not run" and "the check says no" the same answer
— the distinction `exits.py` rule 2 exists to keep. THE PASS TOKEN IS PRINTED LAST, after
every assertion has computed, because the runner matches on a substring and DISCARDS the
exit code (#595): a token printed early passes on a crash.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

VERDICT = {
    1: ("STEP1-UNSTAMPED-HANDBACK-DETECTED", "STEP1-NOT-SETTLED"),
}

# What the materialised tree must contain before anything is claimed about it. A tree that
# archived cleanly but carries no `turn.py` is not a merge target worth judging, and saying
# so is more useful than eleven assertion failures.
REQUIRED = ("lib/task-station.py", "lib/board/turn.py", "lib/turn.py")


def materialise(repo, dest):
    """`git archive origin/main` into `dest`. True on success.

    NEVER the working tree. `git archive origin/main` resolves an explicit remote-tracking
    ref, so a branch checked out in that repo cannot answer for it, and a dirty worktree
    contributes nothing."""
    try:
        proc = subprocess.run(["git", "-C", repo, "archive", "origin/main"],
                              capture_output=True, timeout=120)
        if proc.returncode != 0 or not proc.stdout:
            return False
        tar = subprocess.run(["tar", "-x", "-C", dest], input=proc.stdout,
                             capture_output=True, timeout=120)
        return tar.returncode == 0
    except Exception:                                     # noqa: BLE001
        return False


def env_for(home):
    """A process environment pinned to a throwaway store, three ways.

    All three, because `paths.data_dir()` resolves TASK_STATION_HOME >
    CLAUDE_CONFIG_DIR/task-station-data > XDG_STATE_HOME/task-station > ~/.claude — so
    pinning the first alone still lets a fallback reach the real store."""
    env = dict(os.environ)
    env.update({"TASK_STATION_HOME": home, "CLAUDE_CONFIG_DIR": home,
                "XDG_STATE_HOME": home, "TASK_STATION_NO_AGENT_QUERY": "1"})
    return env


LAUNCH_TS = 1000.0


def _child(seq, memos, sessions=("child-sid",)):
    """A child task dict in the shape `turn` reads: invoked, gone, and carrying `memos`.

    No exit conditions on purpose — this step judges the hand-back rail, and a condition
    would add findings that say nothing about it.

    THE POST-LAUNCH SAVE IS LOAD-BEARING. A launch with no evidence after it is
    SPAWN_FAILED — a window that never came up — and the gate asks for a report only of a
    child that DID the work. Without it every control here would go green for the wrong
    reason: no `no-report` finding because there is no work to have reported."""
    return {"id": "c%s" % seq, "seq": seq, "title": "T", "status": "open",
            "related": [{"kind": "parent", "id": "orch", "seq": None}],
            "steps": [], "grades": [], "sessions": list(sessions),
            "events": [{"id": "e1", "kind": "child", "ts": LAUNCH_TS,
                        "text": "invoked by #10 as implementer: do the thing"},
                       {"id": "e2", "kind": "save", "ts": LAUNCH_TS + 500.0,
                        "text": "checkpoint"}],
            "memos": list(memos)}


def _memo(text="report", ts=2000.0, from_sid=None, from_task=None, **extra):
    m = {"id": "m1", "ts": ts, "from_sid": from_sid, "from_task": from_task,
         "text": text, "acks": []}
    m.update(extra)
    return m


def _orch():
    return {"id": "orch", "seq": 10, "title": "orchestrator", "status": "open",
            "related": [], "steps": [], "events": [], "memos": [], "grades": [],
            "sessions": []}


def step1(tree, home):
    """The reader's verdict, from the real `turn` loaded out of the materialised tree."""
    sys.path.insert(0, os.path.join(tree, "lib"))
    os.environ.update({k: v for k, v in env_for(home).items()
                       if k.startswith(("TASK_STATION", "CLAUDE_CONFIG", "XDG_"))})
    import turn                                            # noqa: PLC0415

    def no_report(child):
        """True when the SHIPPED gate raises the G4 no-report finding on this child."""
        return "no-report" in [f.get("code") for f in turn.gate(child)["findings"]]

    # THE LIVE-WITNESS SHAPE. #596's memo e2f12e39 and #598's 0c7cba32 both look exactly
    # like this on the record: filed after launch, on the child's own task, no sender
    # stamped — and both were reported as "left no report memo on this task".
    unstamped = _child(11, [_memo(text="the full report", ts=2500.0)])
    stamped = _child(12, [_memo(text="the full report", ts=2500.0,
                                from_sid="child-sid")])
    silent = _child(13, [])
    rejected = _child(14, [_memo(text=turn.rejection_memo(
        {"threshold": "A-", "failed": [("G4", "B")], "missing": []}, ref=14),
        ts=2500.0)])
    parked = _child(15, [_memo(text=turn.park_memo(
        "human-gate", "a person decides this", ref=15), ts=2500.0)])
    routine = _child(16, [_memo(text="CHILD #17 stood down — done", ts=2500.0,
                                **{"routine": True})])
    early = _child(17, [_memo(text="from the previous attempt", ts=LAUNCH_TS - 100.0)])
    foreign_task = _child(18, [_memo(text="a note from the parent", ts=2500.0,
                                     from_task="orch")])
    foreign_sid = _child(19, [_memo(text="a note from a peer", ts=2500.0,
                                    from_sid="somebody-else")])
    empty = _child(20, [_memo(text="   \n  ", ts=2500.0)])

    unstamped_line = "\n".join(turn.lines(turn.plan(_orch(), [unstamped], live=())))
    silent_line = "\n".join(turn.lines(turn.plan(_orch(), [silent], live=())))
    SAYS_NONE = "left no report memo on this task"

    return [
        # -- the fixed direction: the report a child actually files is seen ------------
        ("an UNSTAMPED hand-back after launch reads REPORTED",
         turn.child_state(unstamped, live=()) == turn.REPORTED),
        ("and the parent's line no longer says the child filed nothing",
         SAYS_NONE not in unstamped_line and not no_report(unstamped)),

        # -- the ten controls: the finding must still fire where it should -------------
        ("a child that filed NOTHING still raises no-report", no_report(silent)),
        ("and the parent's line still says so", SAYS_NONE in silent_line),
        ("an unstamped GATE REJECTED is not the child's report", no_report(rejected)),
        ("an unstamped GATE PARKED is not the child's report", no_report(parked)),
        ("an unstamped ROUTINE lifecycle notice is not a report", no_report(routine)),
        ("an unstamped memo from BEFORE this launch is not this attempt's",
         no_report(early)),
        ("a memo declaring another task as its origin is excluded",
         no_report(foreign_task)),
        ("a memo naming a session not registered here is excluded",
         no_report(foreign_sid)),
        ("an empty unstamped memo is not a report", no_report(empty)),
        ("a STAMPED hand-back from the child's own session still counts",
         turn.child_state(stamped, live=()) == turn.REPORTED
         and not no_report(stamped)),
    ]


STEPS = {1: step1}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True,
                    help="the checkout whose origin/main is the merge target")
    ap.add_argument("--step", type=int, choices=sorted(STEPS), required=True,
                    help="which of #602's steps to settle")
    a = ap.parse_args(argv)

    work = tempfile.mkdtemp(prefix="prove-report-memo-")
    tree, home = os.path.join(work, "tree"), os.path.join(work, "home")
    os.makedirs(tree)
    os.makedirs(home)
    try:
        if not materialise(a.repo, tree):
            print("DETECTION-NOT-IN-MAIN — `git archive origin/main` in %s produced "
                  "nothing, so nothing was decided." % a.repo)
            return 0
        missing = [p for p in REQUIRED if not os.path.exists(os.path.join(tree, p))]
        if missing:
            print("DETECTION-NOT-IN-MAIN — origin/main in %s does not carry %s, so "
                  "nothing was decided." % (a.repo, ", ".join(missing)))
            return 0
        try:
            checks = STEPS[a.step](tree, home)
        except Exception as exc:                          # noqa: BLE001
            print("%s — the surface raised while being exercised: %s: %s"
                  % (VERDICT[a.step][1], type(exc).__name__, exc))
            return 0
        # THE ANNOUNCEMENT COMES AFTER THE WORK. A line printed above the checks would be
        # a token the runner matches on while proving nothing, because a crash between the
        # two is invisible to a substring match that discards the exit code.
        print("DETECTION-IN-MAIN — the shipped reader exercised from "
              "`git archive origin/main` in %s" % a.repo)
        for name, ok in checks:
            print("  %-4s %s" % ("ok" if ok else "FAIL", name))
        passed, failed = VERDICT[a.step]
        bad = [n for n, ok in checks if not ok]
        if bad:
            print("%s — %d of %d check(s) failed: %s"
                  % (failed, len(bad), len(checks), "; ".join(bad)))
        else:
            print("%s — %d check(s) exercised against origin/main, x" % (passed, len(checks)))
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
