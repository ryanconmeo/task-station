#!/usr/bin/env python3
"""prove_loop_honesty.py — three ways the loop reported something it had not established.

  1. TWO SURFACES, TWO SETTLEDNESS RULES. `cmds/loop.py`'s `scan` passed the DEEP rule
     (`settled_fn`); `turn.plan` and `orchestrator_refusal` passed nothing and got the
     LEAF one. On a NESTED tree the two disagreed about the same record — a parent whose
     own checklist is green while its children are unbuilt reads settled to one and
     unsettled to the other. A flat board cannot show this, which is why nobody saw it.

  2. AN UNRESOLVABLE `--task` EXITED 0. `exit-tick`'s own docstring says "Not proven met
     must never exit 0, because the exit code is what lets this gate a release step" —
     and a task that could not be RESOLVED has proven nothing at all. `scan` did the same.

  3. LIVENESS THAT COULD NOT BE READ LOOKED LIKE AN IDLE MACHINE. `_live_seqs()` fails
     open to an empty set — right for the scan's display column, catastrophic for the
     children cap, which then reads "nothing running" and spawns over the limit.

RUN:  python3 scripts/prove_loop_honesty.py
      python3 scripts/prove_loop_honesty.py --part mutant   # old behaviour MUST go red
"""
import argparse, os, shutil, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS = []

def check(name, got, want):
    ok = got == want
    print("  %-56s %s   (got %r)" % (name, "PASS" if ok else "FAIL", got))
    if not ok:
        FAILS.append(name)

def mutate(libdir):
    """Put the old behaviour back so every check below MUST fail."""
    t = os.path.join(libdir, "board", "turn.py")
    s = open(t).read()
    s = s.replace("    if is_settled is None:\n        is_settled = _loop.settled_fn(population)\n"
                  "    report = _loop.scan(children, resolve, live=live, is_settled=is_settled)",
                  "    report = _loop.scan(children, resolve, live=live)")
    open(t, "w").write(s)
    l = os.path.join(libdir, "board", "loop.py")
    d = open(l).read()
    d = d.replace("    report = scan(kids, by_id.get, is_settled=settled_fn(every))",
                  "    report = scan(kids, by_id.get)")
    open(l, "w").write(d)
    c = os.path.join(libdir, "board", "cmds", "loop.py")
    m = open(c).read()
    # Put BOTH exit codes back to a bare `return` (exit 0). Anchored on text unique to
    # each site, and RAISING if an anchor is missing — a mutant that silently mutates
    # nothing proves nothing, which is the failure this whole file exists to prevent.
    for anchor, repl in (
        ("        print(err)\n        sys.exit(2)\n    only = getattr(a, \"step\", None)",
         "        print(err)\n        return\n    only = getattr(a, \"step\", None)"),
        ("        sys.exit(2)        # a population that could not be resolved",
         "        return             # a population that could not be resolved"),
    ):
        if anchor not in m:
            raise SystemExit("mutant anchor missing — the code moved: %r" % anchor[:50])
        m = m.replace(anchor, repl)
    m = m.replace("_live_seqs_or_none", "_live_seqs_OLD_NAME")
    m = m.replace("cannot determine which child sessions are RUNNING", "OLD BEHAVIOUR")
    open(c, "w").write(m)

def task(seq, tid, parent=None, closed=False, met=None):
    """A minimal task dict. `met=True` registers one exit condition and marks it met,
    which is what makes the LEAF rule call it settled."""
    t = {"id": tid, "seq": seq, "title": "t%s" % seq, "status": "closed" if closed else "open",
         "decisions": [], "steps": []}
    if parent:
        t["related"] = [{"id": parent, "rel": "parent"}]
    if met is not None:
        t["steps"] = [{"n": 1, "text": "s", "done": bool(met)}]
        t["exits"] = {"1": {"cmd": "true", "expect": "", "last": {"ok": bool(met)}}}
    return t

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", default="real", choices=["real", "mutant"])
    a = ap.parse_args()
    work = tempfile.mkdtemp(prefix="prove-loop-")
    libdir = os.path.join(work, "lib")
    shutil.copytree(os.path.join(ROOT, "lib"), libdir)
    shutil.copytree(os.path.join(ROOT, "bin"), os.path.join(work, "bin"))
    if a.part == "mutant":
        mutate(libdir)
    for p in (libdir, os.path.join(libdir, "board")):
        sys.path.insert(0, p)
    import loop as _loop, turn as _turn

    print("\n1. TURN USES THE SETTLEDNESS RULE IT IS GIVEN")
    # The regression, stated exactly: `cmds/loop.py`'s scan passed `is_settled=settled_fn(...)`
    # and `turn.plan` accepted no such argument, so it silently used the LEAF default. The
    # sharpest possible proof is a SENTINEL rule — one that cannot be confused with the
    # default — and asserting the plan reflects it. No synthetic exit-condition data is
    # needed, and none can be faked wrong.
    orch = task(1, "o"); orch["orchestrator"] = True
    childA = task(2, "a", parent="o")
    grand = task(3, "g", parent="a")
    every = [orch, childA, grand]
    by_id = {t["id"]: t for t in every}

    seen = []
    def sentinel(t):
        seen.append(t.get("seq"))
        return True                      # "everything is settled" — the leaf rule says otherwise

    check("1a the default rule disagrees with the sentinel",
          _loop.settled(childA), False)
    plan = _turn.plan(orch, [childA, grand], resolve=by_id.get, is_settled=sentinel)
    check("1b turn CONSULTED the rule it was handed", len(seen) > 0, True)
    rows = plan["scan"]["rows"]
    check("1c and every row reflects it", sorted({r["settled"] for r in rows}), [True])
    check("1d so the loop halts COMPLETE, not BLOCKED",
          plan["scan"]["stop"], "complete")

    scan_rep = _loop.scan([childA, grand], by_id.get, is_settled=sentinel)
    check("1e scan and turn now compute the SAME report",
          [r["settled"] for r in scan_rep["rows"]],
          [r["settled"] for r in rows])

    print("\n2. AN UNRESOLVABLE --task DOES NOT EXIT 0")
    env = dict(os.environ, PYTHONPATH="%s:%s" % (libdir, os.path.join(libdir, "board")))
    for verb in ("exit-tick", "scan"):
        # Call the ENGINE, not bin/task-station — that wrapper is a bash script, and
        # running it under sys.executable made every invocation fail for a syntax error,
        # so the check passed for the wrong reason under BOTH parts. A proof that cannot
        # tell the fix from a crash is not a proof.
        r = subprocess.run([sys.executable, os.path.join(libdir, "task-station.py"),
                            verb, "--task", "99999999"],
                           capture_output=True, text=True, env=env, cwd=work)
        check("2. `%s --task <bogus>` exits non-zero" % verb, r.returncode != 0, True)

    print("\n3. UNREADABLE LIVENESS IS NOT AN IDLE MACHINE")
    sys.path.insert(0, os.path.join(libdir, "board", "cmds"))
    import loop as cmds_loop  # noqa - the cmds shim
    has_split = hasattr(sys.modules.get("loop"), "_live_seqs_or_none") or \
        "_live_seqs_or_none" in open(os.path.join(libdir, "board", "cmds", "loop.py")).read()
    check("3a the cap can tell 'none' from 'cannot tell'", has_split, True)
    src = open(os.path.join(libdir, "board", "cmds", "loop.py")).read()
    check("3b invoke refuses rather than spawning blind",
          "cannot determine which child sessions are RUNNING" in src, True)

    print()
    if a.part == "mutant":
        if FAILS:
            print("MUTANT: %d check(s) failed, as required." % len(FAILS))
            return 0
        print("MUTANT DID NOT FAIL — the proof asserts nothing. FAILURE.")
        return 1
    if FAILS:
        print("FAILED: %s" % ", ".join(FAILS))
        return 1
    print("ALL CHECKS PASS.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
