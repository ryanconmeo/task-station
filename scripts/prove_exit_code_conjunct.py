#!/usr/bin/env python3
"""Settle #595's exit condition — A GREEN CONDITION MEANS THE COMMAND SUCCEEDED — against
the MERGE TARGET, so a tick means "this landed where every session can see it" and not
"it works in the branch that says so".

HOW THIS IS RUN, and why it is not run any other way:

    git -C <main checkout> fetch -q origin main
    git -C <main checkout> show origin/main:scripts/prove_exit_code_conjunct.py \\
        | python3 - --repo <main checkout>

THE JUDGE COMES OUT OF origin/main, AND SO DOES EVERYTHING IT JUDGES. `--repo` names the
checkout whose `origin/main` is the merge target; it cannot be aimed at a working tree,
because the only thing this reads out of it is `git archive origin/main`, an explicit
remote-tracking ref. Before the merge, `git show origin/main:scripts/…` writes nothing,
python reads an empty program, NOTHING IS PRINTED, and the condition is red — so a branch
cannot supply its own judge. THERE IS NO REDIRECTION FLAG.

IT DISCRIMINATES ON THE RULE, NOT ON THE SOURCE. Every assertion below RUNS a real command
through the shipped `checker.verify` and `exits.evaluate` and reads the verdict that comes
back. A source grep for `returncode` would go green on a line that is never reached, and
that is precisely the class of false green this whole task exists to close.

THE DISCRIMINATOR IS ONE COMMAND: `echo <marker>; exit 1`.
  * under the OLD rule its verdict was PASS — the marker was in the output, and that was
    the whole test;
  * under the NEW rule its verdict is UNMET — it ran, and it failed.
So a checker that still discards the return code cannot print this script's pass marker,
and neither can one that has simply broken (the clean-exit controls below would go red).

BOTH DIRECTIONS ARE PROVED. A judge that only checked the failing case would go green on a
checker that failed everything, and "nothing passes any more" is a worse outcome than the
defect. So every failing case is paired with the identical command exiting 0.

NOTHING TOUCHES THE REAL STORE: TASK_STATION_HOME, CLAUDE_CONFIG_DIR and XDG_STATE_HOME are
all pinned to one throwaway directory before the engine is imported. Pinning only the first
lets a fallback reach ~/.claude — the same three-way pin tests/conftest.py uses.

EXIT STATUS. This script exits 0 when it passes and 1 when it does not, and that is a change
of practice its own subject makes safe. Earlier provers here exited 0 whatever they found,
because a non-zero exit would have made "the check could not run" and "the check says no"
the same answer. It no longer does: `status` already separates a command that RAN from one
that timed out or could not be launched, so a refutation exits non-zero and stays UNMET
while an unrunnable command stays UNKNOWN. THE PASS MARKER IS PRINTED LAST, after every
assertion has computed — because until this very change lands, the runner reading this
script matches on a substring and throws the exit code away, and a marker printed early
would pass on a crash.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

MARK = "T595-PASS"
PROBE = "T595-PROBE"


def fail(msg):
    print("T595-FAIL: %s" % msg)
    raise SystemExit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True,
                    help="the checkout whose origin/main is the merge target")
    a = ap.parse_args()

    repo = os.path.abspath(os.path.expanduser(a.repo))
    if not os.path.isdir(os.path.join(repo, ".git")):
        fail("no git checkout at %s" % repo)
    sha = subprocess.run(["git", "-C", repo, "rev-parse", "--short", "origin/main"],
                         capture_output=True, text=True).stdout.strip()
    if not sha:
        fail("cannot resolve origin/main in %s" % repo)

    work = tempfile.mkdtemp(prefix="t595-main-")
    home = tempfile.mkdtemp(prefix="t595-home-")
    try:
        tar = subprocess.run("git -C %s archive origin/main | tar -x -C %s"
                             % (repo, work), shell=True, capture_output=True, text=True)
        if tar.returncode != 0:
            fail("git archive origin/main failed: %s" % (tar.stderr or "").strip()[:200])
        if not os.path.isfile(os.path.join(work, "lib", "board", "checker.py")):
            fail("origin/main (%s) carries no lib/board/checker.py" % sha)

        for key in ("TASK_STATION_HOME", "CLAUDE_CONFIG_DIR", "XDG_STATE_HOME"):
            os.environ[key] = home
        sys.path.insert(0, os.path.join(work, "lib"))
        try:
            import checker
            import exits
            import steps as steps_mod
        except Exception as exc:
            fail("origin/main (%s) will not import: %s: %s"
                 % (sha, type(exc).__name__, exc))

        bad = []

        # (1) THE RUNNER REPORTS THE RETURN CODE AT ALL. Everything else rests on this.
        res = checker.run_command("echo %s; exit 1" % PROBE, 30)
        if not (isinstance(res, (tuple, list)) and len(res) >= 3):
            bad.append("run_command still returns %d values — the return code is "
                       "discarded at the source" % len(res))
        else:
            out, status, code = res[0], res[1], res[2]
            if PROBE not in out:
                bad.append("run_command lost the output")
            if status != "ran":
                bad.append("run_command called a command that ran %r" % status)
            if code != 1:
                bad.append("run_command reported code %r for `exit 1`" % code)

        # (2) A CLAIM: the marker is printed and the command fails. THE DISCRIMINATOR.
        def claim(cmd):
            t = {"id": "t595", "title": "probe"}
            checker.register(t, ["C1|%s|%s" % (cmd, PROBE)])
            return checker.verify(t, timeout=30)[0]

        r = claim("echo %s; exit 1" % PROBE)
        if r["ok"]:
            bad.append("a claim whose command printed its substring and EXITED 1 still "
                       "passes — the exit code is discarded")
        if r.get("status") != "ran":
            bad.append("a failing claim reported status %r; it ran, so it must refute "
                       "rather than go uncountable" % r.get("status"))
        if r.get("missing"):
            bad.append("the probe did not print its own substring (%r)" % r["missing"])

        r = claim("echo %s >&2; exit 7" % PROBE)
        if r["ok"]:
            bad.append("a claim whose substring landed on stderr and EXITED 7 still passes")

        # THE CONTROLS. Without these a checker that failed EVERYTHING would pass.
        if not claim("echo %s" % PROBE)["ok"]:
            bad.append("a claim whose command printed its substring and exited 0 does "
                       "NOT pass — the conjunct has broken the ordinary case")
        if not claim("echo %s >&2" % PROBE)["ok"]:
            bad.append("a substring on stderr with a clean exit no longer passes — "
                       "combining stdout and stderr was deliberate and must survive")

        # (3) AN EXIT CONDITION: the same rule, and it must not move a tick.
        def step(cmd):
            t = {"id": "t595", "steps": [{"text": "the step", "done": False}]}
            ok, err = exits.set_condition(t["steps"], 1, cmd, [PROBE])
            if not ok:
                fail("could not register the probe condition: %s" % err)
            results = exits.evaluate(t, timeout=30)
            moved = exits.apply_results(t, results)
            return t, results[0], moved

        t, r, moved = step("echo %s; exit 1" % PROBE)
        if r["ok"]:
            bad.append("an exit condition whose command printed its substring and "
                       "EXITED 1 is still met")
        if moved["ticked"]:
            bad.append("a FAILING command ticked its step")
        if steps_mod.is_done(t["steps"][0]):
            bad.append("a failing condition marked its step done")
        if exits.item_state(t["steps"][0]) != exits.UNMET:
            bad.append("a failing condition reads as %r; it ran, so it is unmet and not "
                       "unknown" % exits.item_state(t["steps"][0]))

        t, r, moved = step("echo %s" % PROBE)
        if not (r["ok"] and moved["ticked"] == [1]):
            bad.append("a clean command no longer ticks its step — the mechanism has "
                       "stopped working rather than become stricter")

        # (4) RULE 2 SURVIVES: a command that did NOT RUN still refutes nothing.
        t = {"id": "t595", "steps": [{"text": "the step", "done": False}]}
        exits.set_condition(t["steps"], 1, "unrunnable", [PROBE])
        results = exits.evaluate(t, run=lambda cmd, to: ("", "timeout"))
        moved = exits.apply_results(t, results)
        if exits.item_state(t["steps"][0]) != exits.UNKNOWN:
            bad.append("a timeout reads as %r rather than unknown — the conjunct has "
                       "swallowed rule 2" % exits.item_state(t["steps"][0]))
        if moved["ticked"] or moved["unticked"]:
            bad.append("a timeout moved a tick")

        # (5) NO EXEMPTION. An opt-out would make a green mean "the author said this one
        # may fail", which is the authorship the gate exists to doubt.
        src = open(os.path.join(work, "lib", "board", "exits.py")).read()
        src += open(os.path.join(work, "lib", "board", "checker.py")).read()
        for banned in ("allow_nonzero", "ignore_exit", "any_exit", "expect_exit",
                       "exit_ok", "nonzero_ok", "skip_returncode"):
            if banned in src:
                bad.append("origin/main carries an exemption hatch named %r" % banned)

        if bad:
            print("T595-FAIL on origin/main %s: %s" % (sha, " | ".join(bad[:6])))
            raise SystemExit(1)
        print("%s on origin/main %s — a claim and an exit condition are both UNMET when "
              "their command prints the expected substring and exits non-zero, both pass "
              "when it exits 0, a command that never ran is still UNKNOWN, and there is "
              "no exemption hatch." % (MARK, sha))
    finally:
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    main()
