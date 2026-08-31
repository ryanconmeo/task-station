#!/usr/bin/env python3
"""Settle the two exit conditions of #598 — MERGE-GATED IS VISIBLE WHERE IT IS READ —
against the MERGE TARGET, so a tick means "this landed where every session can see it" and
not "it works in the branch that says so".

HOW THIS IS RUN, and why it is not run any other way:

    git -C <main checkout> fetch -q origin main
    git -C <main checkout> show origin/main:scripts/prove_merge_gated_visible.py \\
        | python3 - --repo <main checkout> --step 1

THE JUDGE COMES OUT OF origin/main, AND SO DOES EVERYTHING IT JUDGES. An exit condition
that resolves a worktree passes on work that never merged — the only rot shape that fails
in the DANGEROUS direction, because it reports done for something no other session can
see. Piping the judge from `git show origin/main:` means a branch cannot supply its own
judge: before the merge the path does not exist there, `git show` writes nothing, python
reads an empty program, and NOTHING IS PRINTED — so every expected substring is missing and
the condition is red. THERE IS NO REDIRECTION FLAG. `--repo` names the checkout whose
`origin/main` is the merge target; it cannot be pointed at a working tree, because the only
thing this reads is `git archive origin/main`, an explicit remote-tracking ref.

WHAT IS DIFFERENT FROM #591'S JUDGE, and it is a strengthening rather than a variation.
That one executed a LEAF out of a git object and read its callers as source, because the
callers could not be loaded. This one MATERIALISES THE WHOLE TREE — `git archive
origin/main` into a throwaway directory — and then runs the SHIPPED SURFACES from it: the
real `task-station exit-show` CLI against a throwaway store, and the real `turn.child_state`
and `turn.plan` over hand-built task dicts. So what is asserted is what a reader will
actually see, not a rule that a caller is separately grepped for. A source grep can go green
on a line that is present and never reached; running the command cannot.

NOTHING TOUCHES THE REAL STORE. Every task-station process this spawns has
TASK_STATION_HOME, CLAUDE_CONFIG_DIR and XDG_STATE_HOME all pinned to one throwaway
directory, which is the same three-way pin `tests/conftest.py` uses and for the same
reason: pinning only the first lets a fallback reach ~/.claude.

WHAT EACH STEP ASSERTS, and each is proved BOTH WAYS — a declared case AND an undeclared
control — because a mark that appears without a declaration would be worse than the silence
it replaces:

  STEP 1  `exit-show` MARKS each merge-gated condition per step and COUNTS them in its
          header, says DONE PENDING MERGE when every unmet condition is declared, and
          prints NOT ONE WORD about merge-gating on a task that declared none.
  STEP 2  `turn` resolves DONE PENDING MERGE wherever a merge-gated child has reported —
          including the ACKED-report path that dropped it — keeps a live unreported child
          RUNNING (the false green this must never buy), and names the merge in its WAIT
          line instead of asking for a green the child cannot produce.

Exit status is 0 whatever it finds. The condition is decided by the SUBSTRINGS, and a
non-zero exit would make "the check could not run" and "the check says no" the same answer
— the distinction `exits.py` rule 2 exists to keep. THE PASS TOKEN IS PRINTED LAST, after
every assertion has computed, because the runner matches on a substring and discards the
exit code: a token printed early passes on a crash.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

VERDICT = {
    1: ("STEP1-EXIT-SHOW-MARKS-MERGE-GATED", "STEP1-NOT-SETTLED"),
    2: ("STEP2-TURN-RESOLVES-DONE-PENDING-MERGE", "STEP2-NOT-SETTLED"),
}

# What the materialised tree must contain before anything is claimed about it. A tree that
# archived cleanly but carries no `gating.py` is origin/main BEFORE this work landed, and
# saying so is more useful than eleven assertion failures.
REQUIRED = ("lib/task-station.py", "lib/board/gating.py", "lib/gating.py",
            "lib/board/turn.py", "lib/board/exits.py", "lib/board/cmds/loop.py")


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


def run_cli(tree, home, *args):
    """One `task-station` invocation out of the materialised tree. Combined output."""
    try:
        p = subprocess.run([sys.executable, os.path.join(tree, "lib", "task-station.py")]
                           + list(args), capture_output=True, text=True, timeout=120,
                           env=env_for(home), cwd=tree)
        return (p.stdout or "") + (p.stderr or "")
    except Exception as exc:                              # noqa: BLE001
        return "COULD-NOT-RUN: %s: %s" % (type(exc).__name__, exc)


# ---------------------------------------------------------------- step 1: the surface ----

def _register(tree, home, ref, gated):
    """A task with one condition per entry in `gated`, each declared or not, all RED.

    Red by construction rather than by luck: the command prints NOPE and the condition
    expects OK, so `exit-tick` settles every one of them unmet without touching a network,
    a repo or a clock."""
    run_cli(tree, home, "create", "--title", "judge-%s" % ref)
    args = ["update", "--task", ref]
    for i, _g in enumerate(gated, start=1):
        args += ["--step-add", "step %d" % i]
    run_cli(tree, home, *args)
    for i, g in enumerate(gated, start=1):
        extra = ["--merge-gated"] if g else []
        run_cli(tree, home, "exit-add", "--task", ref, "--step", str(i),
                "--cmd", "echo NOPE", "--expect", "OK", *extra)
    run_cli(tree, home, "exit-tick", "--task", ref)
    return run_cli(tree, home, "exit-show", "--task", ref)


def step1(tree, home):
    """The reader's surface, end to end through the real CLI."""
    # #1 — two declared, one not. Every condition red.
    mixed = _register(tree, home, "1", [True, True, False])
    # #2 — the permanent negative control: nothing declared anywhere.
    plain = _register(tree, home, "2", [False, False])
    # #3 — declared, and every unmet one declared, so the header may say the sentence.
    allg = _register(tree, home, "3", [True, True])

    return [
        ("a declared condition is marked on its own step",
         mixed.count("merge-gated — it reads the merge target") == 2),
        ("an undeclared condition beside it is NOT marked",
         mixed.count("merge-gated — it reads the merge target") != 3),
        ("the header counts the declared ones", "2 of them are MERGE-GATED" in mixed),
        ("a mixed red is NOT called done pending merge",
         "DONE PENDING MERGE" not in mixed),
        ("all-declared-and-red says DONE PENDING MERGE", "DONE PENDING MERGE" in allg),
        ("and says why nothing in the loop can turn them green",
         "nobody in the loop can perform the merge" in allg),
        ("an all-undeclared task says NOTHING about merge-gating",
         "merge-gated" not in plain.lower() and "MERGE-GATED" not in plain),
        ("and still renders its conditions", "0 met · 2 unmet · 0 not run" in plain),
    ]


# ------------------------------------------------------------- step 2: the resolution ----

def _child(seq, declared, acked=None, reported=True, n=2):
    """A child task dict in the shape `turn` reads: launched, conditions registered and
    RED, and a report memo that is present / acked / absent."""
    block = {"cmd": "c", "expect": ["OK"],
             "last": {"ts": 1000.0, "ok": False, "status": "ran",
                      "missing": ["OK"], "got": ""}}
    if declared:
        block["merge_gated"] = True
    memos = []
    if reported:
        memos = [{"id": "m1", "ts": 2000.0, "from_sid": "child-sid", "from_task": None,
                  "text": "report", "acks": ([{"sid": "p", "ts": 2100.0}] if acked
                                             else [])}]
    return {"id": "c%s" % seq, "seq": seq, "title": "T", "status": "open",
            "related": [{"kind": "parent", "id": "orch", "seq": None}],
            "steps": [{"text": "s%d" % i, "done": False, "exit": dict(block)}
                      for i in range(1, n + 1)],
            "events": [{"id": "e1", "kind": "child", "ts": 1000.0,
                        "text": "invoked by #10 as implementer: do the thing"}],
            "memos": memos, "grades": [], "sessions": ["child-sid"]}


def _orch():
    return {"id": "orch", "seq": 10, "title": "orchestrator", "status": "open",
            "related": [], "steps": [], "events": [], "memos": [], "grades": [],
            "sessions": []}


def step2(tree, home):
    """The loop's verdict, from the real `turn` loaded out of the materialised tree."""
    sys.path.insert(0, os.path.join(tree, "lib"))
    os.environ.update({k: v for k, v in env_for(home).items()
                       if k.startswith(("TASK_STATION", "CLAUDE_CONFIG", "XDG_"))})
    import turn                                            # noqa: PLC0415

    unacked = turn.child_state(_child(11, True), live=())
    acked = turn.child_state(_child(12, True, acked=True), live=())
    acked_plain = turn.child_state(_child(13, False, acked=True), live=())
    live_unreported = turn.child_state(_child(14, True, reported=False), live={14})
    mixed = _child(15, True)
    mixed["steps"][0]["exit"].pop("merge_gated")           # one red a merge cannot fix
    gated_wait = "\n".join(turn.lines(turn.plan(
        _orch(), [_child(16, True, reported=False)], live={16})))
    plain_wait = "\n".join(turn.lines(turn.plan(
        _orch(), [_child(17, False, reported=False)], live={17})))

    return [
        ("an unacked report on a gated child reads DONE PENDING MERGE",
         unacked == turn.DONE_PENDING_MERGE),
        ("an ACKED report on a gated child STILL reads DONE PENDING MERGE",
         acked == turn.DONE_PENDING_MERGE),
        ("an ACKED report with undeclared reds is unchanged", acked_plain == "reported"),
        ("one undeclared red among gated ones is NOT done pending merge",
         turn.child_state(mixed, live=()) == "reported"),
        ("a LIVE child that has not reported is still RUNNING",
         live_unreported == "running"),
        ("the WAIT line names the merge", "merge-gated" in gated_wait),
        ("and stops asking for a green the child cannot produce",
         "nor turned its exit conditions green" not in gated_wait),
        ("an undeclared child's WAIT line is UNCHANGED",
         "nor turned its exit conditions green" in plain_wait
         and "merge-gated" not in plain_wait),
    ]


STEPS = {1: step1, 2: step2}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True,
                    help="the checkout whose origin/main is the merge target")
    ap.add_argument("--step", type=int, choices=sorted(STEPS), required=True,
                    help="which of #598's two steps to settle")
    a = ap.parse_args(argv)

    work = tempfile.mkdtemp(prefix="prove-merge-gated-")
    tree, home = os.path.join(work, "tree"), os.path.join(work, "home")
    os.makedirs(tree)
    os.makedirs(home)
    try:
        if not materialise(a.repo, tree):
            print("GATING-NOT-IN-MAIN — `git archive origin/main` in %s produced nothing, "
                  "so nothing was decided." % a.repo)
            return 0
        missing = [p for p in REQUIRED if not os.path.exists(os.path.join(tree, p))]
        if missing:
            print("GATING-NOT-IN-MAIN — origin/main in %s does not carry %s, so nothing "
                  "was decided." % (a.repo, ", ".join(missing)))
            return 0
        try:
            checks = STEPS[a.step](tree, home)
        except Exception as exc:                          # noqa: BLE001
            print("%s — the surface raised while being exercised: %s: %s"
                  % (VERDICT[a.step][1], type(exc).__name__, exc))
            return 0
        # THE ANNOUNCEMENT COMES AFTER THE WORK, not before it. This line used to sit
        # above the checks, mirroring #591's judge — and a crash between the two would
        # have printed a token the runner matches on while proving nothing.
        print("GATING-IN-MAIN — surfaces exercised from `git archive origin/main` in %s"
              % a.repo)
        for name, ok in checks:
            print("  %-4s %s" % ("ok" if ok else "FAIL", name))
        passed, failed = VERDICT[a.step]
        bad = [n for n, ok in checks if not ok]
        if bad:
            print("%s — %d of %d check(s) failed: %s"
                  % (failed, len(bad), len(checks), "; ".join(bad)))
        else:
            print("%s — %d check(s) exercised against origin/main, each with its "
                  "undeclared control" % (passed, len(checks)))
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
