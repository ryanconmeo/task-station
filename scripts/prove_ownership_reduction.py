#!/usr/bin/env python3
"""Settle ONE exit condition: did decision ownership actually reduce a task's decision
corpus, and was that reduction achieved by RELOCATION rather than by gutting the record?

HOW THIS IS MEANT TO BE RUN, and why it is not run any other way:

    git -C <main checkout> fetch -q origin main
    git -C <main checkout> show origin/main:scripts/prove_ownership_reduction.py \
        | python3 - --task 444 --baseline-chars 160245 --baseline-live 110 \
                    --baseline-entries 577 --floor 45000

THE JUDGE COMES OUT OF origin/main, AND THAT IS THE WHOLE POINT. An exit condition that
resolves a worktree passes on work that never merged — the only rot shape that fails in
the DANGEROUS direction, because it reports done for something no other session can see.
Piping the script from `git show origin/main:` means a branch cannot supply its own judge:
before the merge the path does not exist there, `git show` writes nothing, python reads an
empty program, and NOTHING IS PRINTED — so every expected substring is missing and the
condition is red. There is no flag, no environment variable and no fallback that changes
that, deliberately: a condition you can talk into passing is not a condition.

The ENGINE it measures with is the INSTALLED one (`~/.claude/task-station-engine`), not a
checkout, because the question is what a real session's digest actually costs — and a real
session loads the installed engine. If the feature is not in that engine, the ownership
accessors are missing and this says so rather than measuring with half a vocabulary.

THE TWO ASSERTIONS, and why the second one exists. A reduction is easy to fake: supersede
forty rulings and the corpus drops. That is not this feature working, it is the record
being gutted, so the corpus floor alone would reward exactly the wrong move.

  444-REDUCED        the live corpus this task RENDERS fell by at least --floor chars.
  444-RECORD-INTACT  and the log did not shrink to get there: the append-only log is no
                     shorter, no fewer rulings are still current, and at least one
                     reference stub exists — so the drop came from ownership moving, which
                     is the only mechanism that reduces a render without retiring a ruling.

Exit status is 0 whatever it finds. The condition is decided by the SUBSTRINGS, and a
non-zero exit would make "the check could not run" and "the check says no" the same
answer — the distinction `exits.py` rule 2 exists to keep.
"""
import argparse
import json
import os
import sqlite3
import sys

ENGINE = os.path.expanduser("~/.claude/task-station-engine")


def load_engine():
    """Import the INSTALLED engine's decision + ownership vocabulary, or return None.

    Never falls back to a checkout. A repo copy would answer with whatever the caller
    happened to have on disk, which is the substitution this whole script exists to make
    impossible one level up."""
    lib = os.path.join(ENGINE, "lib")
    if not os.path.isdir(lib):
        return None
    for p in (lib, os.path.join(lib, "board")):
        if p not in sys.path:
            sys.path.insert(0, p)
    try:
        import decisions
        import ownership
    except Exception:
        return None
    if not hasattr(ownership, "reassign") or not hasattr(decisions, "owner"):
        return None
    return decisions, ownership


def store_path():
    """The live store, resolved the way the engine resolves it."""
    home = os.environ.get("TASK_STATION_HOME")
    if home:
        return os.path.join(home, "store", "tasks.db")
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    if cfg:
        return os.path.join(cfg, "task-station-data", "store", "tasks.db")
    return os.path.expanduser("~/.task-station/store/tasks.db")


def read_task(seq):
    db = store_path()
    if not os.path.exists(db):
        return None
    conn = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    try:
        row = conn.execute("SELECT data FROM tasks WHERE seq=?", (int(seq),)).fetchone()
    finally:
        conn.close()
    return json.loads(row[0]) if row else None


def measure(task, dec, own):
    """What this task's digest COSTS in decision prose, and what the log still holds.

    `corpus` counts only the rulings this task renders IN FULL — a stub costs its own
    length and nothing more, which is exactly the saving ownership buys. `owned_elsewhere`
    is deliberately NOT counted: those render here too, so a reduction achieved by
    reassigning rulings TO this task would be no reduction at all."""
    tid = task.get("id")
    entries = task.get("decisions") or []
    live = dec.live(entries)
    full = [e for _i, e in live if dec.renders_full(e, tid)]
    stubs = [(i, e) for i, e in live if not dec.renders_full(e, tid)]
    corpus = sum(len(dec.text(e)) for e in full)
    corpus += sum(len(own.stub_line(i, e)) for i, e in stubs)
    return {"corpus": corpus, "live": len(live), "entries": len(entries),
            "stubs": len(stubs), "full": len(full)}


def main(argv=None):
    ap = argparse.ArgumentParser()
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

    mods = load_engine()
    if mods is None:
        print("ENGINE-WITHOUT-OWNERSHIP %s — the installed engine does not carry the "
              "ownership vocabulary, so nothing here was measured." % ENGINE)
        return 0
    dec, own = mods
    print("OWNERSHIP-IN-MAIN — judge read from origin/main, measured with %s" % ENGINE)

    task = read_task(a.task)
    if task is None:
        print("NO-SUCH-TASK #%d in %s" % (a.task, store_path()))
        return 0
    m = measure(task, dec, own)
    delta = a.baseline_chars - m["corpus"]
    print("#%d corpus=%d baseline=%d delta=%d floor=%d · rendered-in-full=%d stubs=%d "
          "live=%d entries=%d"
          % (a.task, m["corpus"], a.baseline_chars, delta, a.floor,
             m["full"], m["stubs"], m["live"], m["entries"]))
    if delta >= a.floor:
        print("%d-REDUCED" % a.task)
    else:
        print("%d-NOT-REDUCED — %d chars short of the %d-char floor"
              % (a.task, a.floor - delta, a.floor))
    intact = (m["entries"] >= a.baseline_entries
              and m["live"] >= a.baseline_live
              and m["stubs"] >= 1)
    if intact:
        print("%d-RECORD-INTACT" % a.task)
    else:
        why = []
        if m["entries"] < a.baseline_entries:
            why.append("the append-only log SHRANK (%d < %d)"
                       % (m["entries"], a.baseline_entries))
        if m["live"] < a.baseline_live:
            why.append("%d ruling(s) stopped being current — a reduction by RETIREMENT, "
                       "not by ownership" % (a.baseline_live - m["live"]))
        if m["stubs"] < 1:
            why.append("no reference stub exists, so no ruling was reassigned at all")
        print("%d-RECORD-GUTTED — %s" % (a.task, "; ".join(why)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
