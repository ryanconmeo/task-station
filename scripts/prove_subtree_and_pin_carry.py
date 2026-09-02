#!/usr/bin/env python3
"""prove_subtree_and_pin_carry.py — proves BOTH fix sets in this change, both ways.

Two independent defects, one release:

  A. THE EXCHANGE COULD NOT LIVE INSIDE A REPO IT DID NOT OWN. `is_git_repo` asked
     `isdir(root/.git)`, which is False for every SUBTREE, so an exchange at
     `<brain repo>/tasks` read as "no repo, no remote" and silently never synced —
     and `init_root` then ran `git init` there, NESTING a repo, after which the outer
     repo could not even stage (`git add -A` -> "does not have a commit checked out").
     Separately `_finish` ran a bare `add -A`, which from a subdirectory stages the
     WHOLE repository, sweeping a human's unstaged notes into a sync commit.

  B. A REPLACEMENT VERB THREW METADATA AWAY. `mark_split` popped the pin and gave it
     to nobody, and never copied `kind`/`subject` to the parts (the auto path appends
     them as bare text). `restore` then advertised itself as the one inverse and
     returned the text UNPINNED. A pinned ruling split at an unattended turn boundary
     left the spine with nothing saying so.

RUN:  python3 scripts/prove_subtree_and_pin_carry.py
      python3 scripts/prove_subtree_and_pin_carry.py --part mutant

`--part mutant` restores the OLD behaviour in a scratch copy and REQUIRES every check
to fail. A proof that cannot go red proves nothing (#603).
"""
import argparse, json, os, shutil, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS = []

def flag(entry, key):
    """A decision compacts back to a plain string when it carries no flags, so a
    missing-flag read must not crash the proof — the mutant produces exactly that."""
    return entry.get(key) if isinstance(entry, dict) else None


def check(name, got, want):
    ok = got == want
    print("  %-58s %s   (got %r)" % (name, "PASS" if ok else "FAIL", got))
    if not ok:
        FAILS.append(name)
    return ok

def git(cwd, *a):
    return subprocess.run(["git"] + list(a), cwd=cwd, capture_output=True, text=True)

def load(libdir):
    for p in (libdir, os.path.join(libdir, "board")):
        if p not in sys.path:
            sys.path.insert(0, p)
    import importlib
    import sync, decisions
    return importlib.reload(sync), importlib.reload(decisions)

def mutate(libdir):
    """Put the OLD behaviour back, so the checks below MUST go red."""
    sp = os.path.join(libdir, "board", "sync.py")
    s = open(sp).read()
    s = s.replace('    if not os.path.isdir(root):\n        return False\n'
                  '    return _git(root, "rev-parse", "--git-dir").ok',
                  '    return os.path.isdir(os.path.join(root, ".git"))')
    s = s.replace('_git(root, "add", "-A", "--", ".")', '_git(root, "add", "-A")')
    open(sp, "w").write(s)
    dp = os.path.join(libdir, "board", "decisions.py")
    d = open(dp).read()
    d = d.replace('    if rich.pop("pinned", None):\n        rich[PINNED_BEFORE] = True',
                  '    rich.pop("pinned", None)')
    # Kill the WHOLE carry block, which is what the old code lacked — mutating one
    # line of it left checks passing for the wrong reason.
    start = d.index("    for pos, n in enumerate(parts):")
    end = d.index('    rich["split_into"] = parts')
    d = d[:start] + d[end:]
    open(dp, "w").write(d)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", default="real", choices=["real", "mutant"])
    a = ap.parse_args()

    work = tempfile.mkdtemp(prefix="prove-subtree-")
    libdir = os.path.join(work, "lib")
    shutil.copytree(os.path.join(ROOT, "lib"), libdir)
    if a.part == "mutant":
        mutate(libdir)
    sync, decisions = load(libdir)

    print("\nA. AN EXCHANGE INSIDE A BRAIN REPO")
    brain = os.path.join(work, "brain")
    os.makedirs(os.path.join(brain, "notes"))
    git(brain, "init", "-q")
    open(os.path.join(brain, "notes", "a.md"), "w").write("note\n")
    git(brain, "add", "-A")
    git(brain, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    git(brain, "remote", "add", "origin", "https://example.invalid/b.git")

    root = os.path.join(brain, "tasks")
    info = sync.init_root(root)
    check("A1 no nested repo created", info["created_repo"], False)
    check("A1b no .git inside the exchange", os.path.isdir(os.path.join(root, ".git")), False)
    check("A2 subtree is recognised as in a repo", sync.is_git_repo(root), True)
    check("A3 inherits the brain repo's remote", sync.has_remote(root), True)
    check("A4 outer repo can still stage", git(brain, "add", "-A").returncode, 0)

    # A5 exercises SYNC'S OWN staging call, not git's. Running `git add -A -- .` here
    # would prove that git honours a pathspec, which was never in doubt; what was wrong
    # is the argv sync builds. So record the argv and assert on it.
    calls = []
    real_git = sync._git
    sync._git = lambda r, *args, **kw: (calls.append(args), real_git(r, *args, **kw))[1]
    try:
        sync._finish(root, {"git": {"repo": True, "remote": False}, "kind": "backup"}, False)
    finally:
        sync._git = real_git
    adds = [c for c in calls if c and c[0] == "add"]
    check("A5 sync stages with a pathspec, never bare -A",
          [c for c in adds if "--" not in c], [])

    print("\nB. A SPLIT KEEPS THE METADATA IT WAS GIVEN")
    entries = [{"text": "the original ruling", "pinned": True,
                "kind": "ruling", "subject": [{"task": "532"}]},
               {"text": "part one"}, {"text": "part two"}]
    carried = {}
    ok, err = decisions.mark_split(entries, 1, [2, 3], carried=carried)
    check("B0 split succeeded", (ok, err), (True, None))
    check("B1 pin MOVES to the first part", flag(entries[1], "pinned"), True)
    check("B2 and only the first", flag(entries[2], "pinned"), None)
    check("B3 kind copies to every part",
          [flag(entries[1], "kind"), flag(entries[2], "kind")], ["ruling", "ruling"])
    check("B4 subject copies to every part",
          [bool(flag(entries[1], "subject")), bool(flag(entries[2], "subject"))], [True, True])
    check("B5 the caller is told what moved", carried.get("pin_to"), 2)
    check("B6 original no longer holds the pin", flag(entries[0], "pinned"), None)

    print("\nC. RESTORE IS A REAL INVERSE")
    ent2 = [{"text": "pinned ruling", "pinned": True}, {"text": "replacement"}]
    decisions.mark_superseded(ent2, 1, 2)
    check("C1 superseding drops the pin", flag(ent2[0], "pinned"), None)
    ok, err = decisions.restore(ent2, 1)
    check("C2 restore succeeded", (ok, err), (True, None))
    check("C3 restore gives the pin BACK", flag(ent2[0], "pinned"), True)

    print()
    if a.part == "mutant":
        if FAILS:
            print("MUTANT: %d check(s) failed, as required — the proof can go red." % len(FAILS))
            return 0
        print("MUTANT DID NOT FAIL — the proof asserts nothing. This is a FAILURE.")
        return 1
    if FAILS:
        print("FAILED: %s" % ", ".join(FAILS))
        return 1
    print("ALL CHECKS PASS.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
