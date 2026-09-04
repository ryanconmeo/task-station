#!/usr/bin/env python3
"""#604's BLAST RADIUS: how the STORED CORPUS decides WHICH TREE each of its commands
reads — measured the way #595 and #606 measured theirs, from ONE invocation, so the
before and the after cannot be confounded by two runs.

WHY ONE INVOCATION MATTERS. #595's number ("234 commands, 209 green under both rules, 0
flipped") is only trustworthy because both verdicts came out of the same walk of the same
store. Two runs measure two stores: a task edited between them silently moves the delta,
and the direction it moves is the reassuring one. So this reads the corpus ONCE, into
memory, and computes BOTH verdicts off that one snapshot.

WHAT IS MEASURED. Every exit condition and every claim the store holds, closed tasks
included, classified by HOW IT DECIDES ITS TREE:

  * DECLARES   — the stored record carries `repo` + `ref` (3.66.0). The runner resolves
                 that ref and evaluates the command in a detached checkout of it. This is
                 the ONLY class whose execution context changes, and before this work
                 landed the class is empty by construction.
  * PROSE      — the command text names the repo or the ref ITSELF: `git -C <path>`,
                 `cd <path>`, `git show origin/main:…`. The tree is decided, but by a
                 string nothing can read as data — so `merge-gated` cannot be computed
                 from it and no surface can say which tree a green came from.
  * CHECKER    — the command hands the decision off to a script under
                 ~/.task-station/checker, hub-side user data the store cannot see. Those
                 scripts, and the `*-repo.sh` resolvers most of them call, exist ONLY
                 because a condition inherits its tree; each resolver is a private
                 re-implementation of `treeref`.
  * INHERITS   — none of the above. The command runs in whatever directory the runner
                 happened to be in, and nothing anywhere records which one that was.

WHY THE DELTA IS COMPUTED BY CLASSIFICATION AND NOT BY RE-RUNNING 200 COMMANDS. #595
could re-run its corpus because its change was a rule inside the CHECKER: the same output,
judged twice. #604's change is in the runner's CHOICE OF DIRECTORY, so a stored command
can only change behaviour if it declares a tree — and re-running an arbitrary corpus of
shell (full suites, merge gates, network fetches) to learn that is unbounded and, for the
commands with side effects, destructive.

THE CLASSIFIER IS PROVED NOT VACUOUS IN THE SAME RUN. "Zero found" is worthless from an
instrument that finds nothing, so the run also classifies POSITIVE and NEGATIVE controls
for every class and REFUSES to report unless every one lands where it must.

AND THE RESOLVER INVENTORY IS MEASURED, NOT REMEMBERED. `--resolvers` lists every
`*-repo.sh` in the checker directory, says which take an environment override as a
candidate — the redirection #604's step 5 is about — and counts the live conditions and
claims still pointing at each one. Those scripts are hub-side user data and are NOT this
script's to delete (604:3); it reports, and a human acts after the runner change is live.

THE PASS TOKEN IS PRINTED LAST, after every count has computed, because a runner matches
on a substring and a token printed early passes on a crash (#595, #606:1).
"""
import argparse
import json
import os
import re
import sys

MARK = "T604-MEASURED"
FAIL = "T604-MEASURE-FAIL"

# The four classes, most-specific first. The order IS the rule: a command that declares a
# tree is classified as declaring it even if its text also happens to mention a path.
DECLARES, PROSE, CHECKER, INHERITS = "declares", "prose", "checker", "inherits"

# A command NAMES A REPO when it carries one of these — a path that decides which tree it
# reads. Each is a real shape from the corpus, not a hypothetical.
_REPO_RES = (
    re.compile(r"\bgit\s+(?:-c\s+\S+\s+)*-C\s+\S"),          # git -C <path>
    re.compile(r"(?:^|[;&|(]|\bthen\b|\bdo\b)\s*cd\s+\S"),    # cd <path>
    re.compile(r"(?:\$HOME|~|/Users/)[^\s'\"]*/(?:Workspace|workspace)[^\s'\"]*"),
)
# A command NAMES A REF when it spells one into the text.
_REF_RES = (
    re.compile(r"\borigin/\w"),                                  # a remote-tracking ref
    re.compile(r"\bgit\s+(?:-C\s+\S+\s+)?show\s+\S+:"),        # git show <ref>:<path>
    re.compile(r"\bgit\s+(?:-C\s+\S+\s+)?archive\s+\S"),       # git archive <ref>
)

# A command HANDS THE DECISION OFF when it invokes a script under the checker directory:
# the tree is then chosen inside hub-side user data the store cannot see, which is the
# whole reason a stored green could not say which tree it read.
_CHECKER_RE = re.compile(r"[\w./$~{}-]*\.task-station/checker/([\w.-]+\.(?:sh|py))")
# …and a RESOLVER is specifically a `*-repo.sh` under that directory (604:5's subject).
_RESOLVER_RE = re.compile(r"[\w./$~{}-]*checker/([\w.-]+-repo\.sh)")

# The checker directory those resolvers live in. Hub-side user data, NOT repo-tracked.
CHECKER_DIR = os.path.join(os.path.expanduser("~"), ".task-station", "checker")

# The environment names a resolver may take as a candidate tree. Taking ANY of them is the
# redirection #604 removes: a condition whose tree can be moved by an env var is one that
# can be talked into passing, and both runners STORE their verdict.
OVERRIDE_ENVS = ("TS_REPO", "TASK_STATION_REPO", "TASK_STATION_REF", "REPO")


def names_repo(cmd):
    return any(rx.search(str(cmd or "")) for rx in _REPO_RES)


def names_ref(cmd):
    return any(rx.search(str(cmd or "")) for rx in _REF_RES)


def classify(cmd, declares):
    """Which class one stored command falls in. `declares` is whether the RECORD carries
    a repo+ref, which is data and beats anything the text says.

    ORDER IS THE RULE, most-specific first. A checker hand-off is checked BEFORE the prose
    shapes because such a command usually names neither a repo nor a ref — that is exactly
    its property, and calling it "inherits" would hide the indirection this measurement is
    about."""
    if declares:
        return DECLARES
    text = str(cmd or "")
    if _CHECKER_RE.search(text):
        return CHECKER
    if names_repo(text) or names_ref(text):
        return PROSE
    return INHERITS


def checker_scripts_named(cmd):
    """Every checker-directory script one stored command invokes."""
    return sorted(set(_CHECKER_RE.findall(str(cmd or ""))))


# THE CONTROLS. Each class needs a case that MUST land in it, or the counts below are an
# instrument reading zero because it reads nothing.
CONTROLS = [
    # (command, declares, expected class)
    ("$HOME/.task-station/checker/t627-c1-name.sh", False, CHECKER),
    ("sh /Users/x/.task-station/checker/t566-s3-repo-rules-rehomed.sh", False, CHECKER),
    ("git -C /repo show origin/main:lib/x.py", False, PROSE),
    ("cd ~/Workspace-Other/task-station && python3 -m unittest", False, PROSE),
    ("git show origin/main:scripts/prove_x.sh", False, PROSE),
    ("echo hi && grep -c foo $HOME/Workspace-Other/task-station/README.md", False, PROSE),
    ("python3 -m unittest discover -s tests -t .", False, INHERITS),
    ("grep -c 'def write_handoff' lib/board/succession.py", False, INHERITS),
    ("echo T-PASS", False, INHERITS),
    ("task-station search --detail 604", False, INHERITS),
    ("git log --oneline -1", False, INHERITS),       # git, but names no repo and no ref
    # A declaration BEATS the text, which is the whole point of it being data.
    ("python3 -m unittest discover -s tests -t .", True, DECLARES),
    ("git -C /repo show origin/main:lib/x.py", True, DECLARES),
]


def check_controls():
    bad = []
    for cmd, declares, want in CONTROLS:
        got = classify(cmd, declares)
        if got != want:
            bad.append("control misclassified as %s (wanted %s): %s" % (got, want, cmd))
    return bad


def load_corpus(lib):
    """The whole stored corpus, read ONCE: every exit condition and every claim on every
    task the store holds, closed ones included. Closed tasks are IN on purpose — a
    condition on a closed task is still a stored command a later session can re-run, and
    excluding them is how a corpus measurement quietly shrinks to the convenient half."""
    # THROUGH THE FACADE, not straight at the seams: `lib/task-station.py` is what calls
    # `_shared.bind(globals())`, and without that every seam's `g("STORE")` reads a None
    # namespace. Loaded by literal path, the way the suite loads it, because the file name
    # has a hyphen in it and cannot be imported.
    import importlib.util
    sys.path.insert(0, lib)
    spec = importlib.util.spec_from_file_location(
        "ts_t604_facade", os.path.join(lib, "task-station.py"))
    ts = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ts)
    import board.state as state          # noqa: E402
    import board.exits as exits          # noqa: E402
    import board.checker as checker      # noqa: E402
    rows = []
    for task in state.all_tasks():
        ref = task.get("seq") or (task.get("id") or "")[:8]
        for item in exits.items(task):
            rows.append({"kind": "condition", "task": ref, "id": "step %s" % item["n"],
                         "cmd": item["cmd"], "merge_gated": bool(item["merge_gated"]),
                         "declares": bool(item.get("decl")),
                         "recorded_tree": bool(item.get("tree"))})
        for claim in checker.claim_items(task):
            last = {r.get("id"): r for r in
                    (checker.last_verify(task).get("results") or [])
                    if isinstance(r, dict)}
            rows.append({"kind": "claim", "task": ref, "id": claim["id"],
                         "cmd": claim["cmd"], "merge_gated": False,
                         "declares": bool(claim.get("decl")),
                         "recorded_tree": bool(
                             (last.get(claim["id"]) or {}).get("tree"))})
    for r in rows:
        r["class"] = classify(r["cmd"], r["declares"])
        r["scripts"] = checker_scripts_named(r["cmd"])
        r["names_repo"] = names_repo(r["cmd"])
        r["names_ref"] = names_ref(r["cmd"])
    return rows


def _read(name):
    try:
        return open(os.path.join(CHECKER_DIR, name), encoding="utf-8",
                    errors="replace").read()
    except OSError:
        return ""


def _code(body):
    """`body` with whole-line comments removed.

    NOT COSMETIC — this is a false positive the first run of this script actually made.
    `t622-repo.sh` and `t623-repo.sh` each carry a paragraph explaining that they USED to
    take `${TS_REPO:-}` and no longer do, and a naive grep read that explanation as the
    override it was describing. Two resolvers were reported as redirectable when their
    whole point is that they are not. A shebang is kept: it is not a comment about the
    code, it IS code."""
    out = []
    for line in str(body or "").splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#") and not stripped.startswith("#!"):
            continue
        out.append(line)
    return "\n".join(out)


def _reaches(names, depth=4):
    """Every checker script reachable from `names`, following one script calling another.

    TRANSITIVE, BOUNDED. A stored command names a per-step checker; that checker may name
    a helper; the helper names the resolver. #604's own conditions are two hops deep, and
    a one-hop walk reports their resolver as having no callers at all — the reassuring
    answer, and the one that reads as permission to delete it."""
    seen, frontier = set(names), list(names)
    for _ in range(depth):
        nxt = []
        for name in frontier:
            for found in _CHECKER_RE.findall(_read(name)):
                if found not in seen:
                    seen.add(found)
                    nxt.append(found)
        if not nxt:
            break
        frontier = nxt
    return seen


def resolver_inventory(rows):
    """Every `*-repo.sh` in the checker directory, with the two facts a human needs to
    decide its fate: does it take an ENVIRONMENT OVERRIDE as a candidate tree, and which
    live stored commands still reach it.

    THE CALLER COUNT FOLLOWS ONE LEVEL OF INDIRECTION, and it has to. Almost no stored
    command names a resolver directly — it names a per-step checker script, and THAT
    script calls the resolver. Counting only direct mentions reports every resolver as
    unused, which is the reassuring answer and the wrong one; it would read as permission
    to delete eleven scripts that eleven live conditions depend on.

    REPORTS, NEVER DELETES (604:3). These scripts are hub-side user data outside this
    repo, and removing one before the runner change is merged AND live turns the
    conditions that reach it red for the wrong reason."""
    reached = {}                        # resolver -> [stored command refs]
    for r in rows:
        where = "#%s %s" % (r["task"], r["id"])
        found = set(_RESOLVER_RE.findall(r["cmd"]))
        for script in _reaches(r["scripts"]):
            found |= set(_RESOLVER_RE.findall(_read(script)))
        for name in sorted(found):
            reached.setdefault(name, []).append(where)
    out = []
    try:
        names = sorted(n for n in os.listdir(CHECKER_DIR) if n.endswith("-repo.sh"))
    except OSError:
        names = []
    for name in names:
        body = _code(_read(name))
        envs = [e for e in OVERRIDE_ENVS
                if re.search(r"\$\{?%s\b" % re.escape(e), body)]
        out.append({"name": name, "overrides": envs,
                    "callers": reached.get(name, [])})
    # A resolver something reaches for but the directory does not hold is a DANGLING call,
    # and it is worth saying so: that condition can only ever fail to launch.
    for name in sorted(set(reached) - {r["name"] for r in out}):
        out.append({"name": name, "overrides": [], "callers": reached[name],
                    "missing": True})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lib", default=None,
                    help="the engine's lib/ to measure with (default: this script's)")
    ap.add_argument("--resolvers", action="store_true",
                    help="also inventory the hand-written *-repo.sh resolvers under "
                         "~/.task-station/checker and who still calls them")
    ap.add_argument("--json", dest="as_json", default=None, metavar="PATH",
                    help="write the whole measurement to PATH as JSON")
    a = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    lib = os.path.abspath(a.lib or os.path.join(here, os.pardir, "lib"))
    if not os.path.isdir(lib):
        print("%s: no lib/ at %s" % (FAIL, lib))
        return 1

    bad = check_controls()
    if bad:
        print("%s: the classifier is not trustworthy —" % FAIL)
        for b in bad:
            print("  %s" % b)
        return 1

    rows = load_corpus(lib)
    conditions = [r for r in rows if r["kind"] == "condition"]
    claims = [r for r in rows if r["kind"] == "claim"]
    by_class = {c: [r for r in rows if r["class"] == c]
                for c in (DECLARES, PROSE, CHECKER, INHERITS)}

    print("#604 BLAST RADIUS — the whole stored corpus, read ONCE.")
    print("")
    print("  %-38s %d" % ("stored commands", len(rows)))
    print("  %-38s %d" % ("  exit conditions", len(conditions)))
    print("  %-38s %d" % ("  claims", len(claims)))
    print("  %-38s %d" % ("  (of the conditions, merge-gated)",
                          len([r for r in conditions if r["merge_gated"]])))
    print("")
    print("  HOW EACH ONE DECIDES WHICH TREE IT READS:")
    for cls, gloss in ((DECLARES, "repo+ref as DATA — the runner checks the ref out"),
                       (PROSE, "the command text names the repo or the ref itself"),
                       (CHECKER, "hands off to a hub-side script the store cannot see"),
                       (INHERITS, "runs in whatever directory the runner was in")):
        print("  %-10s %4d   %s" % (cls, len(by_class[cls]), gloss))
    print("       of the %d in prose: %d name a repo, %d name a ref, %d name both."
          % (len(by_class[PROSE]),
             len([r for r in by_class[PROSE] if r["names_repo"]]),
             len([r for r in by_class[PROSE] if r["names_ref"]]),
             len([r for r in by_class[PROSE] if r["names_repo"] and r["names_ref"]])))
    print("")
    print("  THE VERDICT UNDER THE OLD RULE (the runner owns no tree at all):")
    print("    all %d run in the inherited cwd. %d of them decide a tree anyway, in "
          "prose or inside a hub-side script — but no surface can READ that decision, so "
          "merge-gated is the author's word and a green names no tree."
          % (len(rows), len(by_class[PROSE]) + len(by_class[CHECKER])))
    print("  THE VERDICT UNDER THE NEW RULE (the runner resolves a declared ref):")
    print("    %d change execution context — exactly the ones that DECLARE a tree; the "
          "other %d are byte-for-byte unaffected and stay that way permanently."
          % (len(by_class[DECLARES]), len(rows) - len(by_class[DECLARES])))
    print("    %d stored verdict(s) record which commit produced them."
          % len([r for r in rows if r["recorded_tree"]]))
    print("")
    for cls in (DECLARES, CHECKER):
        if not by_class[cls]:
            continue
        print("  %s (%d):" % (cls.upper(), len(by_class[cls])))
        for r in by_class[cls]:
            print("    #%-5s %-9s %s" % (r["task"], r["id"], r["cmd"][:120]))
    print("")
    print("  CLASSIFIER CONTROLS: %d cases, every one landed in the class it must."
          % len(CONTROLS))

    inventory = None
    if a.resolvers:
        inventory = resolver_inventory(rows)
        print("")
        print("  THE HAND-WRITTEN RESOLVERS in %s — REPORTED, NOT TOUCHED (604:3):"
              % CHECKER_DIR)
        for r in inventory:
            flag = ("MISSING from the directory" if r.get("missing")
                    else ("TAKES AN OVERRIDE: %s" % ", ".join(r["overrides"])
                          if r["overrides"] else "no environment override"))
            print("    %-22s %-34s %d live caller(s)%s"
                  % (r["name"], flag, len(r["callers"]),
                     (" — " + ", ".join(r["callers"])) if r["callers"] else ""))
        print("    %d resolver(s); %d take an environment override."
              % (len(inventory), len([r for r in inventory if r["overrides"]])))

    if a.as_json:
        with open(a.as_json, "w", encoding="utf-8") as f:
            json.dump({"rows": rows, "resolvers": inventory,
                       "counts": {"commands": len(rows), "conditions": len(conditions),
                                  "claims": len(claims),
                                  **{c: len(by_class[c]) for c in by_class}}},
                      f, indent=2)
        print("  full measurement written to %s" % a.as_json)

    print("")
    print("%s: %d stored commands (%d conditions, %d claims); %d declare a repo+ref, "
          "%d name a tree in prose, %d hand off to a hub-side script, %d inherit the "
          "runner's cwd."
          % (MARK, len(rows), len(conditions), len(claims), len(by_class[DECLARES]),
             len(by_class[PROSE]), len(by_class[CHECKER]), len(by_class[INHERITS])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
