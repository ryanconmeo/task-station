#!/usr/bin/env python3
"""Settle ONE exit condition: can the PARENT of a CLOSED child re-home that child's still-
current rulings — and is the guard that used to refuse everybody still refusing everybody
else?

HOW THIS IS RUN, and why it is not run any other way:

    git -C <main checkout> fetch -q origin main
    git -C <main checkout> show origin/main:scripts/prove_closed_child_rehome.py \
        | python3 - --repo <main checkout> --child 586 --parent 444

THE JUDGE COMES OUT OF origin/main, AND SO DOES THE RULE IT JUDGES. An exit condition that
resolves a worktree passes on work that never merged — the only rot shape that fails in the
DANGEROUS direction, because it reports done for something no other session can see. Piping
the judge from `git show origin/main:` means a branch cannot supply its own judge: before
the merge the path does not exist there, `git show` writes nothing, python reads an empty
program, and NOTHING IS PRINTED — so every expected substring is missing and the condition
is red. `load_vocabulary` reads `decisions.py` and `ownership.py` the same way, so not one
line of the code doing the deciding can come from a checkout either.

THE WITNESS IS #586 ITSELF, NOT A CONSTRUCTED ONE. #586 is a real closed child of #444
carrying fourteen still-current rulings its close memo (824c8b98, on #444) offers to
re-home, and on 2026-08-30 that exact command was refused for BOTH the parent and the
session that authored the rulings — verified from both sides. A condition built on a task
made up for the purpose would prove the code compiles; this one proves the record that
exposed the defect is now reachable by the reader its close report addressed.

WHY TWO OF THE FOUR ARMS READ THE REAL RECORD WITH ONE FIELD CHANGED IN MEMORY. The fix is
narrow — the parent may re-home out of a CLOSED child and nothing else changed — and
narrowness is only provable by varying the clause under test. Arm B is #586's own record
read as OPEN: the same task, the same parent, the same caller, differing in the single
field the rule turns on, and it must still be refused. That is a controlled variation of
the live witness, not a substitute for it, and it stays true for as long as #586 does —
unlike a second live task, which would go red the day somebody closed it.

NOTHING HERE IS REDIRECTABLE. The store path is resolved to the one real store, with no
environment branch — a condition that can be pointed at a different store by exporting a
variable is a condition you can talk into passing. Nothing is written: `may_reassign_out`
is a pure predicate over a stored record, which is the reason the rule lives in `ownership`
rather than at the command seam.

THE THREE ASSERTIONS:

  REHOME-IN-MAIN        origin/main carries the rule at all — the merge gate.
  <child>-PARENT-ADMITTED  the parent of the real closed child is admitted out of it.
  GUARD6-INTACT         and everyone else is still refused: the same record read as OPEN
                        refuses the same parent, a caller attached elsewhere is refused,
                        and a caller attached to nothing is refused.

Exit status is 0 whatever it finds. The condition is decided by the SUBSTRINGS, and a
non-zero exit would make "the check could not run" and "the check says no" the same answer
— the distinction `exits.py` rule 2 exists to keep.
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
    carry `may_reassign_out`. A half-loaded vocabulary would let this print a verdict
    about a rule it never actually read."""
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
    if not hasattr(mods["ownership"], "may_reassign_out"):
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


def witness_problem(child, parent, own):
    """Why this pair cannot witness anything, or None.

    A VACUOUS PASS IS THE FAILURE MODE THIS EXISTS FOR. If #586 were reopened, or its
    parent edge removed, arm A would be asking a question with no content and would sail
    through — reporting green about a rule it never exercised."""
    if child is None:
        return "the child task is not in the store"
    if parent is None:
        return "the parent task is not in the store"
    if child.get("status") != own.CLOSED_STATUS:
        return ("the child is %r, not closed — this condition witnesses the CLOSED case"
                % child.get("status"))
    if parent.get("id") not in own.parent_ids(child):
        return "the child does not name that task as its parent"
    return None


def arms(child, parent_id, own):
    """The four questions, as `[(name, admitted, expected), …]`. Pure: no store, no
    session, no write — `may_reassign_out` is a predicate over a stored record."""
    as_open = dict(child)
    as_open["status"] = "open"
    return [
        ("A parent-of-closed", own.may_reassign_out(child, parent_id)[0], True),
        ("B same-record-read-as-open", own.may_reassign_out(as_open, parent_id)[0], False),
        ("C attached-elsewhere", own.may_reassign_out(child, "not-a-parent-of-this")[0],
         False),
        ("D attached-to-nothing", own.may_reassign_out(child, None)[0], False),
    ]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True,
                    help="the MAIN CHECKOUT whose origin/main is the merge target")
    ap.add_argument("--child", type=int, required=True,
                    help="the CLOSED child that witnesses this — a real one")
    ap.add_argument("--parent", type=int, required=True,
                    help="the task that child names as its parent")
    a = ap.parse_args(argv)

    mods = load_vocabulary(a.repo)
    if mods is None:
        print("REHOME-NOT-IN-MAIN — origin/main in %s does not carry "
              "ownership.may_reassign_out, so nothing was decided." % a.repo)
        return 0
    _dec, own = mods
    print("REHOME-IN-MAIN — judge and rule both read from origin/main in %s" % a.repo)

    child, parent = read_task(a.child), read_task(a.parent)
    problem = witness_problem(child, parent, own)
    if problem:
        print("WITNESS-NOT-VALID — #%s/#%s cannot witness this: %s"
              % (a.child, a.parent, problem))
        return 0
    pid = parent.get("id")
    results = arms(child, pid, own)
    print("witness #%s [%s] status=%s parent=#%s · %s"
          % (a.child, str(child.get("id") or "")[:8], child.get("status"), a.parent,
             " · ".join("%s=%s(want %s)" % (n, "admit" if got else "refuse",
                                            "admit" if want else "refuse")
                        for n, got, want in results)))
    admitted = dict((n, got) for n, got, _w in results)
    print(("%d-PARENT-ADMITTED" % a.child) if admitted["A parent-of-closed"] else
          ("%d-PARENT-STILL-REFUSED — the parent of a closed child cannot re-home out of "
           "it, which is the defect this condition exists to close" % a.child))
    leaks = [n for n, got, want in results if n != "A parent-of-closed" and got != want]
    print("GUARD6-INTACT" if not leaks else
          "GUARD6-LEAKED — %s admitted a caller that must be refused" % ", ".join(leaks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
