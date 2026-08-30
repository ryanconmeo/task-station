#!/usr/bin/env python3
"""Settle the five exit conditions of #591 — the maintenance scheduler — against the MERGE
TARGET, so a tick means "this landed where every session can see it" and not "it works in the
branch that says so".

HOW THIS IS RUN, and why it is not run any other way:

    git -C <main checkout> fetch -q origin main
    git -C <main checkout> show origin/main:scripts/prove_maintenance_timing.py \\
        | python3 - --repo <main checkout> --step 1

THE JUDGE COMES OUT OF origin/main, AND SO DOES THE RULE IT JUDGES. An exit condition that
resolves a worktree passes on work that never merged — the only rot shape that fails in the
DANGEROUS direction, because it reports done for something no other session can see. Piping
the judge from `git show origin/main:` means a branch cannot supply its own judge: before the
merge the path does not exist there, `git show` writes nothing, python reads an empty program,
and NOTHING IS PRINTED — so every expected substring is missing and the condition is red.
`load_rules` reads `lib/board/timing.py` the same way, so not one line of the code doing the
deciding can come from a checkout either. THERE IS NO REDIRECTION FLAG. `--repo` names the
checkout whose `origin/main` is the merge target; it cannot point this at a working tree,
because every read is `origin/main:<path>` and that is an explicit remote-tracking ref.

WHY THE RULES ARE IN A LEAF. `lib/board/timing.py` imports `os` and `time` and nothing else,
which is the whole reason the five rules were put there rather than beside their callers. A
judge cannot import `sessions` (transcripts, HUD files, config) or `heal` (the store) out of a
git object; it can import a leaf. 3.43.0 made the same move for the same reason and said so:
`ownership.may_reassign_out` became a pure predicate "because an exit condition has to be able
to ask it of origin/main".

WHAT EACH STEP ASSERTS, and each one is proved BOTH WAYS where "both ways" exists — a rule
verified only in the direction you want is a rule you have talked into agreeing:

  STEP 1  the window is DETECTED and the config is an OVERRIDE THAT SAYS WHICH SOURCE WON.
          Detection is right on a 1M session AND on a 200k one (the 1,000,000 stopgap fails
          the second); an explicit override still wins; and an override that DISAGREES with
          the session reports itself, in both directions, because the failure this replaces
          was never arithmetic — it was a number nobody could question.
  STEP 2  the work boundary is COMPOSED from what already shipped, and each of the four ways
          work can be in flight closes it — asserted one at a time, so a signal that ignored
          three of them could not pass by getting the fourth right.
  STEP 3  the AUTO class runs at that boundary and A MERGE IS NEVER IN IT — excluded by verb,
          with the negative control that an unclassified action is refused rather than allowed.
  STEP 4  the handoff has a FLOOR, and the refusal that is the point of it: due, affordable,
          at a boundary, and STILL no prompt while the record is stale.
  STEP 5  a dismissal keys on the SUBJECT, not the sentence — with the negative control, since
          a ruling that reached past its own subject would be worse than the re-firing it
          replaced.

THE WIRING IS CHECKED AS WELL AS THE RULE, and the difference is stated rather than blurred:
the rules are EXECUTED out of origin/main, while the DELEGATION — that `sessions`, `heal` and
the command seam actually call them, and that the two new commands are registered — is read as
source out of origin/main. A rule nothing calls is a rule that ships dark, and executing it
would not notice. Every wiring assertion pins a POSITIVE presence, never an absence.

Exit status is 0 whatever it finds. The condition is decided by the SUBSTRINGS, and a non-zero
exit would make "the check could not run" and "the check says no" the same answer — the
distinction `exits.py` rule 2 exists to keep.
"""
import argparse
import os
import subprocess
import sys
import tempfile
import types

RULES_PATH = "lib/board/timing.py"

# The delegation each step needs to see in origin/main's source, as (path, [substrings]).
# POSITIVE PRESENCES ONLY: "the seam calls the rule" is checkable, "nothing calls it wrongly"
# is not, and a gate that pins an absence goes green the day the file is renamed.
WIRING = {
    1: [("lib/board/sessions.py", ["def window_resolution(", "_timing.resolve_window(",
                                   "def detected_context_window("]),
        ("lib/board/cli.py", ['sub.add_parser("window"']),
        ("lib/board/cmds/sub.py", ["def cmd_window("])],
    2: [("lib/board/cmds/sub.py", ["def _boundary_facts(", "pickups_pending",
                                   "orders_for", "git_operation_in_progress"])],
    3: [("lib/board/heal.py", ["AUTO_VERBS = _timing.AUTO_VERBS"]),
        ("lib/board/cmds/sub.py", ["def _boundary_maintain(", "_heal.auto_ops("]),
        ("lib/board/config.py", ["def boundary_maintenance_enabled("])],
    4: [("lib/board/cmds/sub.py", ["_timing.handoff_due(", "HANDOFF_BLOCKED"])],
    5: [("lib/board/heal.py", ["_timing.dismissal_identity(", "_timing.dismissal_state(",
                               "def entry_identity("])],
}

VERDICT = {
    1: ("STEP1-WINDOW-DETECTED-AND-OVERRIDE-NAMED",
        "STEP1-NOT-SETTLED"),
    2: ("STEP2-BOUNDARY-COMPOSED", "STEP2-NOT-SETTLED"),
    3: ("STEP3-AUTO-CLASS-EXCLUDES-MERGE", "STEP3-NOT-SETTLED"),
    4: ("STEP4-HANDOFF-FLOOR-REFUSES-STALE", "STEP4-NOT-SETTLED"),
    5: ("STEP5-DISMISSAL-KEYS-ON-IDENTITY", "STEP5-NOT-SETTLED"),
}


def show(repo, path):
    """`git show origin/main:<path>` as text, or None. Never falls back to the working tree:
    `origin/main:` is an explicit remote-tracking ref, so a branch checked out in that repo
    cannot answer for it."""
    try:
        r = subprocess.run(["git", "-C", repo, "show", "origin/main:%s" % path],
                           capture_output=True, text=True, timeout=30)
    except Exception:
        return None
    return r.stdout if r.returncode == 0 and r.stdout.strip() else None


def load_rules(repo):
    """Import the rules module FROM origin/main, or return None.

    Returns None — never a partial module — when the file is missing or does not carry every
    rule this judge asks about. A half-loaded module would let this print a verdict about a
    rule it never actually read."""
    src = show(repo, RULES_PATH)
    if src is None:
        return None
    mod = types.ModuleType("timing")
    mod.__dict__["__name__"] = "timing"
    try:
        exec(compile(src, "origin/main:%s" % RULES_PATH, "exec"), mod.__dict__)
    except Exception:
        return None
    for name in ("resolve_window", "boundary", "git_operation_in_progress", "classify",
                 "may_run_unattended", "auto_ops", "held_ops", "handoff_due",
                 "dismissal_identity", "dismissal_state", "schedule"):
        if not hasattr(mod, name):
            return None
    return mod


def wiring_gaps(repo, step):
    """Which delegations this step expects and origin/main does not carry, in words."""
    gaps = []
    for path, needles in WIRING.get(step, []):
        src = show(repo, path)
        if src is None:
            gaps.append("%s is not in origin/main" % path)
            continue
        for needle in needles:
            if needle not in src:
                gaps.append("%s does not carry %r" % (path, needle))
    return gaps


# ------------------------------------------------------------------ the five steps ----

def step1(t):
    """Detection both ways, an override that still wins, and a divergence that reports."""
    src = ("harness", "selection", "model")
    one_m = t.resolve_window(1000000, "harness", detected_sources=src)
    small = t.resolve_window(200000, "harness", detected_sources=src)
    override = t.resolve_window(1000000, "harness", override=200000,
                                override_source="the config key", detected_sources=src)
    stopgap = t.resolve_window(200000, "harness", override=1000000,
                               override_source="the config key", detected_sources=src)
    agree = t.resolve_window(1000000, "harness", override=1000000,
                             override_source="the config key", detected_sources=src)
    return [
        ("1M session, no config", one_m["window"] == 1000000 and not one_m["diverges"]),
        ("200k session, no config", small["window"] == 200000),
        ("explicit override still wins", override["window"] == 200000),
        ("a 200k override on a 1M session reports itself",
         override["diverges"] and "below the real window" in (override["why"] or "")),
        ("the 1,000,000 stopgap on a 200k session reports itself too",
         stopgap["diverges"] and "above the real window" in (stopgap["why"] or "")),
        ("an agreeing override does not cry wolf", not agree["diverges"]),
    ]


def step2(t):
    """The boundary is composed, and every kind of in-flight work closes it."""
    checks = [("nothing in flight is a boundary", t.boundary()["safe"])]
    for kwargs, kind in (({"orders": 1}, t.IN_FLIGHT_ORDER),
                         ({"pickups": 1}, t.IN_FLIGHT_PICKUP),
                         ({"git_op": "a merge"}, t.IN_FLIGHT_MERGE),
                         ({"untracked_edits": True}, t.IN_FLIGHT_EDITS)):
        state = t.boundary(**kwargs)
        checks.append(("%s closes it" % kind,
                       not state["safe"] and
                       [k for k, _w in state["in_flight"]] == [kind]))
    every = t.boundary(orders=1, pickups=1, git_op="a rebase")
    checks.append(("all in-flight reasons are reported, not just the first",
                   len(every["in_flight"]) == 3))
    with tempfile.TemporaryDirectory() as d:
        gd = os.path.join(d, ".git")
        os.makedirs(gd)
        clean = t.git_operation_in_progress(d) is None
        os.makedirs(os.path.join(gd, "rebase-merge"))
        rebase = t.git_operation_in_progress(d) == "a rebase"
        real = os.path.join(d, "gd2")
        os.makedirs(real)
        open(os.path.join(real, "MERGE_HEAD"), "w").write("x")
        tree = os.path.join(d, "tree")
        os.makedirs(tree)
        open(os.path.join(tree, ".git"), "w").write("gitdir: %s\n" % real)
        worktree = t.git_operation_in_progress(tree) == "a merge"
    checks.append(("a clean tree reads clean", clean))
    checks.append(("an interrupted rebase (a DIRECTORY) is seen", rebase))
    checks.append(("a WORKTREE .git pointer resolves", worktree))
    return checks


def step3(t):
    """The AUTO class, and the merge that is never in it."""
    ops = [{"verb": "split", "manual": False}, {"verb": "merge", "manual": False},
           {"verb": "disposition", "manual": False}, {"verb": "split", "manual": True}]
    auto = [o["verb"] for o in t.auto_ops(ops)]
    held = [o["verb"] for o in t.held_ops(ops)]
    return [
        ("the mechanical pass is AUTO",
         t.classify(t.ACTION_HEAL_MECHANICAL)[0] == t.AUTO),
        ("a merge is NEVER auto",
         t.classify(t.ACTION_MERGE)[0] == t.NEVER and
         not t.may_run_unattended(t.ACTION_MERGE)),
        ("the plan filter excludes it by verb", "merge" not in auto),
        ("splits and dispositions are eligible", sorted(auto) == ["disposition", "split"]),
        ("a manual op is not eligible", len(auto) == 2),
        ("held ops are reported, not dropped", held == ["merge"]),
        ("an unclassified action is refused, not allowed",
         not t.may_run_unattended("something-nobody-classified")),
        ("the auto class does not fire off a boundary",
         not t.schedule(t.boundary(pickups=1), auto_ops=3)["auto_fires"]),
        ("it does not fire with nothing to do",
         not t.schedule(t.boundary(), auto_ops=0)["auto_fires"]),
    ]


def step4(t):
    """The floor, and the refusal that is the point of it."""
    blocked, why = t.handoff_due("relay", False, ["a", "b"])
    return [
        ("under the trigger nothing is due",
         t.handoff_due("keep-going", True)[0] == t.HANDOFF_HOLD),
        ("due + affordable + safe + ready prompts",
         t.handoff_due("relay", True)[0] == t.HANDOFF_PROMPT),
        ("it REFUSES to prompt while the record is stale",
         blocked == t.HANDOFF_BLOCKED and "/todo save" in why),
        ("a spent reserve is MISSED, not a prompt",
         t.handoff_due("compact", True)[0] == t.HANDOFF_MISSED),
        ("a due handoff at a non-boundary holds",
         t.handoff_due("relay", True, boundary_safe=False)[0] == t.HANDOFF_HOLD),
        ("and it is never promoted to AUTO",
         t.classify(t.ACTION_HANDOFF)[0] == t.NEVER),
    ]


def step5(t):
    """Identity, MOVED, and the negative control."""
    same = t.dismissal_state("chk", "digest", "fp1", "chk", "digest", "fp1")
    moved = t.dismissal_state("chk", "digest", "fp1", "chk", "digest", "fp2")
    other_ref = t.dismissal_state("chk", "digest", "fp1", "chk", "state", "fp1")
    other_check = t.dismissal_state("chk", "digest", "fp1", "other", "digest", "fp1")
    return [
        ("the same subject and wording is silenced", same == t.DISMISSAL_SILENCED),
        ("a reworded finding stays settled", moved in (t.DISMISSAL_SILENCED,
                                                       t.DISMISSAL_MOVED)),
        ("and is reported as MOVED rather than silently", moved == t.DISMISSAL_MOVED),
        ("a ruling never reaches another ref", other_ref == t.DISMISSAL_NOT_COVERED),
        ("a ruling never reaches another check", other_check == t.DISMISSAL_NOT_COVERED),
        ("identity ignores the wording entirely",
         t.dismissal_identity("chk", "digest") == t.dismissal_identity("chk", "digest")),
        ("and still separates two subjects",
         t.dismissal_identity("chk", "digest") != t.dismissal_identity("chk", "state")),
    ]


STEPS = {1: step1, 2: step2, 3: step3, 4: step4, 5: step5}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True,
                    help="the checkout whose origin/main is the merge target")
    ap.add_argument("--step", type=int, choices=sorted(STEPS), required=True,
                    help="which of #591's five steps to settle")
    a = ap.parse_args(argv)

    rules = load_rules(a.repo)
    if rules is None:
        print("TIMING-NOT-IN-MAIN — origin/main in %s does not carry %s with the five "
              "rules, so nothing was decided." % (a.repo, RULES_PATH))
        return 0
    print("TIMING-IN-MAIN — judge and rules both read from origin/main in %s" % a.repo)

    try:
        checks = STEPS[a.step](rules)
    except Exception as exc:                              # noqa: BLE001
        print("STEP%d-NOT-SETTLED — the rule raised while being exercised: %s: %s"
              % (a.step, type(exc).__name__, exc))
        return 0
    for name, ok in checks:
        print("  %-4s %s" % ("ok" if ok else "FAIL", name))
    gaps = wiring_gaps(a.repo, a.step)
    for gap in gaps:
        print("  FAIL wiring: %s" % gap)
    passed, failed = VERDICT[a.step]
    bad = [n for n, ok in checks if not ok]
    if bad or gaps:
        print("%s — %d rule check(s) and %d wiring check(s) failed: %s"
              % (failed, len(bad), len(gaps), "; ".join(bad + gaps)))
    else:
        print("%s — %d rule check(s) exercised out of origin/main, %d delegation(s) "
              "present" % (passed, len(checks), sum(len(n) for _p, n in WIRING.get(a.step, []))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
