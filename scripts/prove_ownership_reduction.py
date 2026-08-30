#!/usr/bin/env python3
"""Settle ONE exit condition: is decision ownership in `origin/main`, did it actually
reduce a task's decision corpus, and was that reduction achieved by RELOCATION rather than
by gutting the record?

HOW THIS IS RUN, and why it is not run any other way:

    git -C <main checkout> fetch -q origin main
    git -C <main checkout> show origin/main:scripts/prove_ownership_reduction.py \
        | python3 - --repo <main checkout> --task 444 \
                    --baseline-chars 160245 --baseline-live 110 \
                    --baseline-entries 577 --floor 45000

THE JUDGE COMES OUT OF origin/main, AND SO DOES THE VOCABULARY IT JUDGES WITH. An exit
condition that resolves a worktree passes on work that never merged — the only rot shape
that fails in the DANGEROUS direction, because it reports done for something no other
session can see. Piping the judge from `git show origin/main:` means a branch cannot
supply its own judge: before the merge the path does not exist there, `git show` writes
nothing, python reads an empty program, and NOTHING IS PRINTED — so every expected
substring is missing and the condition is red. `load_vocabulary` then reads
`decisions.py` and `ownership.py` the same way, so not one line of the code doing the
measuring can come from a checkout either.

NOTHING HERE IS REDIRECTABLE. The store path is resolved to the one real store, with no
environment branch — deliberately, because a condition that can be pointed at a different
store by exporting a variable is a condition you can talk into passing, and that is not a
condition. The arithmetic in `measure` is unit-tested directly instead
(tests/test_decision_ownership.py), which is the stronger check anyway: it proves the
floor and the anti-gutting rule on constructed inputs rather than on whatever the live
store happens to hold today.

THE THREE ASSERTIONS, and why the third exists. A reduction is easy to fake: supersede
forty rulings and the corpus drops. That is not this feature working, it is the record
being gutted, so a corpus floor alone would reward exactly the wrong move.

  OWNERSHIP-IN-MAIN   the ownership verb is in `origin/main` — the merge gate.
  <task>-REDUCED      the live corpus that task RENDERS fell by at least --floor chars.
  <task>-RECORD-INTACT and the log did not shrink to get there: the append-only log is no
                      shorter, no fewer rulings are still current, and at least one
                      reference stub exists — so the drop came from ownership MOVING,
                      which is the only mechanism that reduces a render without retiring
                      a ruling.

Exit status is 0 whatever it finds. The condition is decided by the SUBSTRINGS, and a
non-zero exit would make "the check could not run" and "the check says no" the same
answer — the distinction `exits.py` rule 2 exists to keep.
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys
import types

# The one real store. No environment branch, on purpose — see the module docstring.
STORE_DB = os.path.expanduser("~/.task-station/store/tasks.db")

# What is read out of origin/main, in dependency order. Both are stdlib-only leaves:
# `decisions` imports nothing at all and `ownership` imports only `decisions`, which is
# what makes reading them straight out of the merge target safe and simple.
VOCABULARY = (("decisions", "lib/board/decisions.py"),
              ("ownership", "lib/board/ownership.py"))


def show(repo, path):
    """`git show origin/main:<path>` as text, or None. Never falls back to the working
    tree: `origin/main:` is an explicit remote-tracking ref, so a branch checked out in
    that repo cannot answer for it."""
    try:
        r = subprocess.run(["git", "-C", repo, "show", "origin/main:%s" % path],
                           capture_output=True, text=True, timeout=30)
    except Exception:
        return None
    return r.stdout if r.returncode == 0 and r.stdout.strip() else None


def load_vocabulary(repo):
    """Import `decisions` + `ownership` FROM origin/main, or return None.

    Returns None — never a partial vocabulary — when either module is missing or does not
    carry the verb. Measuring with half a vocabulary would silently count an owned ruling
    as one this task renders, which is the one arithmetic error that would make the
    condition report a reduction that did not happen."""
    mods = {}
    for name, path in VOCABULARY:
        src = show(repo, path)
        if src is None:
            return None
        mod = types.ModuleType(name)
        mod.__dict__["__name__"] = name
        sys.modules[name] = mod
        try:
            exec(compile(src, "origin/main:%s" % path, "exec"), mod.__dict__)
        except Exception:
            return None
        mods[name] = mod
    if not hasattr(mods["ownership"], "reassign") or not hasattr(mods["decisions"], "owner"):
        return None
    return mods["decisions"], mods["ownership"]


def read_task(seq):
    if not os.path.exists(STORE_DB):
        return None
    conn = sqlite3.connect("file:%s?mode=ro" % STORE_DB, uri=True)
    try:
        row = conn.execute("SELECT data FROM tasks WHERE seq=?", (int(seq),)).fetchone()
    finally:
        conn.close()
    return json.loads(row[0]) if row else None


def measure(task, dec, own):
    """What this task's digest COSTS in decision prose, and what the log still holds.

    `corpus` counts the rulings this task renders IN FULL, plus each stub line — a stub
    costs its own length and nothing more, which is exactly the saving ownership buys.
    Rulings this task owns that live ELSEWHERE are deliberately not counted: they render
    here too, so counting them would let a reduction be manufactured by reassigning
    rulings TO this task, which reduces nothing."""
    tid = task.get("id")
    entries = task.get("decisions") or []
    live = dec.live(entries)
    full = [e for _i, e in live if dec.renders_full(e, tid)]
    stubs = [(i, e) for i, e in live if not dec.renders_full(e, tid)]
    corpus = sum(len(dec.text(e)) for e in full)
    corpus += sum(len(own.stub_line(i, e)) for i, e in stubs)
    return {"corpus": corpus, "live": len(live), "entries": len(entries),
            "stubs": len(stubs), "full": len(full)}


def verdict(m, baseline_chars, baseline_live, baseline_entries, floor):
    """`(delta, reduced, intact, why_not_intact)` — the arithmetic, separated from the
    printing so the tests can assert on it without a store or a repo."""
    delta = baseline_chars - m["corpus"]
    why = []
    if m["entries"] < baseline_entries:
        why.append("the append-only log SHRANK (%d < %d)"
                   % (m["entries"], baseline_entries))
    if m["live"] < baseline_live:
        why.append("%d ruling(s) stopped being current — a reduction by RETIREMENT, not "
                   "by ownership" % (baseline_live - m["live"]))
    if m["stubs"] < 1:
        why.append("no reference stub exists, so no ruling was reassigned at all")
    return delta, delta >= floor, not why, why


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True,
                    help="the MAIN CHECKOUT whose origin/main is the merge target")
    ap.add_argument("--task", type=int, required=True)
    ap.add_argument("--baseline-chars", type=int, required=True,
                    help="the live decision corpus before any reassignment")
    ap.add_argument("--baseline-live", type=int, required=True,
                    help="how many rulings were still current then — the anti-gutting floor")
    ap.add_argument("--baseline-entries", type=int, required=True,
                    help="how long the append-only log was then; it may only grow")
    ap.add_argument("--floor", type=int, required=True,
                    help="chars the corpus must fall by, at minimum")
    a = ap.parse_args(argv)

    mods = load_vocabulary(a.repo)
    if mods is None:
        print("OWNERSHIP-NOT-IN-MAIN — origin/main in %s does not carry the ownership "
              "verb, so nothing was measured." % a.repo)
        return 0
    dec, own = mods
    print("OWNERSHIP-IN-MAIN — judge and vocabulary both read from origin/main in %s"
          % a.repo)

    task = read_task(a.task)
    if task is None:
        print("NO-SUCH-TASK #%d in %s" % (a.task, STORE_DB))
        return 0
    m = measure(task, dec, own)
    delta, reduced, intact, why = verdict(m, a.baseline_chars, a.baseline_live,
                                          a.baseline_entries, a.floor)
    print("#%d corpus=%d baseline=%d delta=%d floor=%d · rendered-in-full=%d stubs=%d "
          "live=%d entries=%d"
          % (a.task, m["corpus"], a.baseline_chars, delta, a.floor,
             m["full"], m["stubs"], m["live"], m["entries"]))
    print(("%d-REDUCED" % a.task) if reduced else
          ("%d-NOT-REDUCED — %d chars short of the %d-char floor"
           % (a.task, a.floor - delta, a.floor)))
    print(("%d-RECORD-INTACT" % a.task) if intact else
          ("%d-RECORD-GUTTED — %s" % (a.task, "; ".join(why))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
