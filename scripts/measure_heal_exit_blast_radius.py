#!/usr/bin/env python3
"""#606's BLAST RADIUS: how much of the STORED CORPUS changes verdict when a heal verb
that REFUSES starts exiting non-zero — measured the way #595 measured its own, from ONE
invocation, so the before and the after cannot be confounded by two runs.

WHY ONE INVOCATION MATTERS. #595's number ("234 commands, 209 green under both rules, 0
flipped") is only trustworthy because both verdicts came out of the same walk of the same
store. Two runs measure two stores: a task edited between them silently moves the delta,
and the direction that moves is the reassuring one. So this reads the corpus ONCE, into
memory, and computes BOTH verdicts off that one snapshot.

WHAT IS MEASURED, and why it is not "run all 234 commands". #595 could re-run its corpus
because its change was a rule inside the CHECKER: the same output, judged twice. #606's
change is inside the heal VERBS, so a stored command can only change verdict if it
actually reaches one — and re-running 234 arbitrary stored shell commands (full suites,
network fetches, merge gates) to learn that is both unbounded and, for the writing verbs
themselves, destructive. The delta is therefore computed by REACHABILITY:

  * a stored command that never invokes `task-station heal` with a WRITING verb cannot
    change status, whatever it prints — the code it would have to reach is not reached;
  * a stored command that DOES invoke one is a candidate flip, and is named in full.

THE CLASSIFIER IS PROVED NOT VACUOUS IN THE SAME RUN. "Zero found" is worthless from an
instrument that finds nothing, so the run also classifies POSITIVE and NEGATIVE controls
and refuses to report unless every one lands where it should. That is the whole reason
this is a script and not a grep.

AND THE DEFECT ITSELF IS MEASURED, not asserted. `--probe` runs each of the nine writing
verbs against a THROWAWAY store, in a refusing form and (where one exists) a succeeding
form, and prints the real exit codes. Run before the change it shows the bug; run after it
shows the fix. It never touches the real store: TASK_STATION_HOME, CLAUDE_CONFIG_DIR and
XDG_STATE_HOME are all pinned, the three-way pin tests/conftest.py uses — pinning only the
first lets a fallback reach ~/.claude.

THE PASS TOKEN IS PRINTED LAST, after every count has computed, because a runner matches
on a substring and a token printed early passes on a crash (#595, #606:1).
"""
import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile

MARK = "T606-MEASURED"
FAIL = "T606-MEASURE-FAIL"

# THE NINE WRITING VERBS, enumerated from lib/board/cli.py's heal parser and ruled on as
# the boundary by 606:2. A companion flag (--into, --to, --stub, --why, --note,
# --decision/--memory/--noop) is NOT a verb: it cannot invoke heal on its own, and
# counting it would classify a read as a write.
WRITING_VERBS = ("--split", "--merge", "--reassign", "--unassign", "--dismiss",
                 "--undismiss", "--apply", "--mark-healed", "--dispose-acks",
                 "--goal-reviewed")
# THE READ-ONLY MODES, kept here so the report can say which reads the corpus does wrap
# rather than lumping them in with "no heal at all". These KEEP exit 0 whatever they find
# (606:2): a scan that finds problems has succeeded at scanning.
READING_MODES = ("--scan", "--dismissals", "--candidates", "--probe-links", "--probe-ado")

# Shell operators that end one command and begin another. `shlex` with
# `punctuation_chars` emits these as their own tokens, so a `&&`-chained condition is
# split into the commands it really runs instead of being read as one long argv.
SEPARATORS = {";", "|", "||", "&", "&&", "(", ")", "\n", "{", "}"}


def segments(cmd):
    """`cmd` split into the argv-ish runs a shell would execute, lossily but
    conservatively. Unparseable input yields ONE segment of whitespace-split words rather
    than nothing: a command this cannot lex must still be classified, and classifying it
    as "no heal here" on a lexer error is how a real flip goes unseen."""
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        toks = list(lex)
    except ValueError:
        toks = cmd.split()
    out, cur = [], []
    for t in toks:
        if t in SEPARATORS or set(t) <= {"&", "|", ";"} and t:
            if cur:
                out.append(cur)
            cur = []
            continue
        cur.append(t)
    if cur:
        out.append(cur)
    return out


def heal_calls(cmd):
    """Every `task-station heal …` invocation inside `cmd`, as
    `{"verbs": [...], "reads": [...], "dry_run": bool}`.

    The subcommand is taken as the first non-flag word after the executable, so
    `task-station --foo heal` and `python3 lib/task-station.py heal` both resolve. An
    executable is any token whose basename is `task-station`, `task-station.py` or
    `todo` — the three spellings the stored corpus actually uses."""
    found = []
    for seg in segments(cmd):
        for i, tok in enumerate(seg):
            base = os.path.basename(tok)
            if base not in ("task-station", "task-station.py", "todo"):
                continue
            rest = seg[i + 1:]
            sub = next((w for w in rest if not w.startswith("-")), None)
            if sub != "heal":
                continue
            found.append({
                "verbs": [f for f in WRITING_VERBS if f in rest],
                "reads": [f for f in READING_MODES if f in rest],
                "dry_run": "--dry-run" in rest,
                "argv": rest,
            })
    return found


# THE CONTROLS. Positive cases MUST classify as writing and negative cases MUST NOT, or
# the count below is an instrument reading zero because it reads nothing.
POSITIVE = [
    "task-station heal --task 12 --apply",
    "task-station heal --task 12 --merge 2,3 --into 5",
    "task-station heal --task 12 --split 4 --into 5,6 --dry-run",
    "task-station heal --reassign 30 --to 606",
    "task-station heal --unassign 30",
    "task-station heal --apply --dismiss 'drift:branch x' --why 'no'",
    "task-station heal --apply --undismiss 'drift:branch x'",
    "task-station heal --mark-healed --note 'read it'",
    "task-station heal --apply --dispose-acks all --noop 'nothing needed'",
    "task-station heal --goal-reviewed",
    "python3 lib/task-station.py heal --task 12 --apply && echo OK",
    "set -e; /usr/local/bin/task-station heal --task 12 --apply | tee /tmp/x",
]
NEGATIVE = [
    "task-station heal --scan --task 12",
    "task-station heal --dismissals --task 12",
    "task-station heal --candidates --task 12",
    "task-station heal --task 12",
    "task-station heal --scan --probe-links --task 12",
    "task-station search --detail 606",
    "python3 -m unittest discover -s tests -t .",
    "git -C /repo show origin/main:scripts/prove_x.sh | bash -s -- --part mutant",
    "echo 'heal --apply is not a command here'",
    "task-station claims --task 606 --verify",
]


def check_controls():
    bad = []
    for c in POSITIVE:
        calls = heal_calls(c)
        if not any(k["verbs"] for k in calls):
            bad.append("positive control NOT classified as writing: %s" % c)
    for c in NEGATIVE:
        calls = heal_calls(c)
        if any(k["verbs"] for k in calls):
            bad.append("negative control classified as writing: %s" % c)
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
        "ts_measure_facade", os.path.join(lib, "task-station.py"))
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
                         "cmd": item["cmd"], "merge_gated": item["merge_gated"]})
        for claim in checker.claim_items(task):
            rows.append({"kind": "claim", "task": ref, "id": claim["id"],
                         "cmd": claim["cmd"], "merge_gated": False})
    return rows


# ------------------------------------------------------------------ probing ---
#
# The nine verbs, each in a form that must REFUSE and (where the verb has one) a form that
# must SUCCEED. Both directions on purpose: a blanket non-zero would be worse than the bug
# it replaces, so the succeeding half is the regression this measurement watches.

def probe(lib):
    home = tempfile.mkdtemp(prefix="t606-home-")
    env = dict(os.environ)
    env.update({"TASK_STATION_HOME": home, "CLAUDE_CONFIG_DIR": home,
                "XDG_STATE_HOME": home, "TASK_STATION_NO_AGENT_QUERY": "1"})
    cli = os.path.join(lib, "task-station.py")

    def run(args):
        p = subprocess.run([sys.executable, cli] + args, capture_output=True, text=True,
                           env=env, cwd=lib)
        return p.returncode, (p.stdout or "") + (p.stderr or "")

    sid = "t606-session"
    rc, out = run(["create", "--session", sid, "--title", "probe target",
                   "--summary", "for #606's blast radius probe"])
    if rc != 0:
        return None, "cannot create the probe task: %s" % out.strip()[:400]
    # `update` takes --task and will not read the attachment, so the fixture names it.
    # It is verified rather than assumed: a probe whose decisions never landed reports
    # "no decisions to reconcile" for every verb, which reads exactly like a refusal and
    # is really an empty fixture — this run had that bug and it is what the check catches.
    for text in ("first ruling, which exists to be split", "second ruling",
                 "third ruling", "fourth ruling"):
        rc, out = run(["update", "--task", "1", "--session", sid, "--decision", text])
        if rc != 0:
            return None, "cannot append a probe decision: %s" % out.strip()[:400]
    rc, out = run(["update", "--task", "1", "--session", sid,
                   "--goal", "DONE = the probe answered"])
    if rc != 0:
        return None, "cannot set the probe goal: %s" % out.strip()[:400]
    rc, out = run(["search", "--detail", "1"])
    if "first ruling, which exists to be split" not in out or "DONE = the probe" not in out:
        return None, ("the probe fixture is EMPTY — four decisions and a goal were "
                      "written and the task shows neither, so every verb below would "
                      "refuse for the wrong reason:\n%s" % out.strip()[:600])

    # EVERY CASE CARRIES THE SUBSTRING ITS OUTPUT MUST HOLD, and that is not decoration.
    # A case labelled REFUSES whose fixture went missing refuses for the wrong reason and
    # still reads as a refusal — this run had exactly that (an `update` without `--task`
    # wrote no decisions, so every verb answered "task #1 has no decisions"). The
    # substring is what makes the label mean the refusal it names.
    cases = [
        # (label, argv, EXPECT_REFUSAL, the substring the output must carry)
        ("--merge with an unreadable member",
         ["heal", "--session", sid, "--merge", "2,foo", "--into", "4"], True,
         "'foo' is not a decision number"),
        ("--merge with an out-of-range member",
         ["heal", "--session", sid, "--merge", "2,99", "--into", "4"], True,
         "--merge 99 — no such decision"),
        ("--split naming no --into",
         ["heal", "--session", sid, "--split", "1"], True,
         "pass `--into"),
        ("--split with an unreadable subject",
         ["heal", "--session", sid, "--split", "foo", "--into", "2"], True,
         "'foo' is not a decision number"),
        ("--reassign with an unreadable member",
         ["heal", "--session", sid, "--reassign", "1,foo", "--to", "1"], True,
         "'foo' is not a decision number"),
        ("--reassign naming no --to",
         ["heal", "--session", sid, "--reassign", "1"], True,
         "pass `--to <task>`"),
        ("--unassign naming nothing owned",
         ["heal", "--session", sid, "--unassign", "1"], True,
         "there is nothing to bring back"),
        ("--dismiss with no --why",
         ["heal", "--session", sid, "--apply", "--dismiss", "drift:branch x"], True,
         "needs --why"),
        ("--dismiss without --apply",
         ["heal", "--session", sid, "--dismiss", "drift:branch x", "--why", "no"], True,
         "would have silently done nothing"),
        ("--undismiss naming no ruling",
         ["heal", "--session", sid, "--apply", "--undismiss", "drift:branch x"], True,
         "no active dismissal"),
        ("--apply with nothing to perform",
         ["heal", "--session", sid, "--apply"], True,
         "TWO REAL OPTIONS"),
        ("--mark-healed combined with --apply",
         ["heal", "--session", sid, "--mark-healed", "--apply"], True,
         "cannot be combined with --scan, --apply"),
        ("--dispose-acks with no ack to fill",
         ["heal", "--session", sid, "--apply", "--dispose-acks", "all",
          "--noop", "nothing was needed"], True,
         "no undispositioned ack to retro-fill"),
        ("--goal-reviewed combined with --scan",
         ["heal", "--session", sid, "--goal-reviewed", "--scan"], True,
         "cannot be combined with --scan"),
        ("--split previewed with --dry-run on an illegal batch",
         ["heal", "--session", sid, "--split", "1,2", "--into", "3", "--dry-run"], True,
         "name exactly ONE decision to split"),
        # …and the other direction, which is the regression risk: a blanket non-zero
        # would be worse than the bug, so every one of these must stay 0.
        ("--split previewed with --dry-run on a LEGAL batch",
         ["heal", "--session", sid, "--split", "1", "--into", "2,3", "--dry-run"], False,
         "--dry-run: nothing was changed. The batch is legal"),
        ("--goal-reviewed on a task with a goal",
         ["heal", "--session", sid, "--goal-reviewed"], False,
         "GOAL REVIEW RECORDED"),
        ("--split that WRITES",
         ["heal", "--session", sid, "--split", "1", "--into", "2,3"], False,
         "split decision 1 into 2, 3"),
        ("--merge that WRITES",
         ["heal", "--session", sid, "--merge", "2,3", "--into", "4"], False,
         "merged 2, 3 into 4"),
        ("--mark-healed that WRITES",
         ["heal", "--session", sid, "--mark-healed", "--note", "read it all"], False,
         "MARKED HEALED"),
        ("--scan (READ-ONLY: exit 0 whatever it finds)",
         ["heal", "--session", sid, "--scan"], False,
         "[HEAL-SCAN]"),
        ("bare heal, the dry run (READ-ONLY)",
         ["heal", "--session", sid], False,
         "[HEAL]"),
        ("--dismissals (READ-ONLY)",
         ["heal", "--session", sid, "--dismissals"], False,
         "DISMISSALS"),
        ("--candidates (READ-ONLY)",
         ["heal", "--session", sid, "--candidates"], False,
         "[HEAL-CANDIDATES]"),
    ]
    rows = []
    for label, argv, want_refusal, expect in cases:
        rc, out = run(argv)
        rows.append({"label": label, "argv": argv, "rc": rc,
                     "want_refusal": want_refusal, "expect": expect,
                     "said_it": expect in out,
                     "honest": (rc != 0) == want_refusal and expect in out,
                     "first": (out.strip().splitlines() or [""])[0][:120]})
    return rows, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lib", default=None,
                    help="the engine's lib/ to measure with (default: this script's)")
    ap.add_argument("--probe", action="store_true",
                    help="also run the nine writing verbs against a THROWAWAY store and "
                         "print their real exit codes")
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

    writing, reading = [], []
    for r in rows:
        calls = heal_calls(r["cmd"])
        if not calls:
            continue
        if any(k["verbs"] for k in calls):
            r["heal"] = sorted({v for k in calls for v in k["verbs"]})
            writing.append(r)
        else:
            r["heal"] = sorted({v for k in calls for v in k["reads"]}) or ["(bare heal)"]
            reading.append(r)

    print("#606 BLAST RADIUS — the whole stored corpus, read ONCE.")
    print("")
    print("  %-34s %d" % ("stored commands", len(rows)))
    print("  %-34s %d" % ("  exit conditions", len(conditions)))
    print("  %-34s %d" % ("  claims", len(claims)))
    print("  %-34s %d" % ("  (of those, merge-gated)",
                          len([r for r in conditions if r["merge_gated"]])))
    print("")
    print("  THE VERDICT UNDER THE OLD RULE (a heal refusal exits 0):")
    print("    every one of the %d reports the status heal gives it today." % len(rows))
    print("  THE VERDICT UNDER THE NEW RULE (a heal refusal exits non-zero):")
    print("    identical for %d; only a command that REACHES a writing verb can differ."
          % (len(rows) - len(writing)))
    print("")
    print("  %-34s %d" % ("wrap a heal WRITING verb (FLIPPABLE)", len(writing)))
    for r in writing:
        print("    #%s %s [%s] %s" % (r["task"], r["id"], ",".join(r["heal"]), r["cmd"]))
    print("  %-34s %d" % ("wrap a heal READ (exit 0 either way)", len(reading)))
    for r in reading:
        print("    #%s %s [%s] %s" % (r["task"], r["id"], ",".join(r["heal"]), r["cmd"]))
    print("")
    print("  CLASSIFIER CONTROLS: %d positive, %d negative, all landed as they must."
          % (len(POSITIVE), len(NEGATIVE)))

    probes, perr = (None, None)
    if a.probe:
        probes, perr = probe(lib)
        print("")
        if perr:
            print("%s: %s" % (FAIL, perr))
            return 1
        print("  THE VERBS THEMSELVES, against a throwaway store (%s):" % lib)
        for p in probes:
            print("    exit %-3d %-12s %-6s %-6s %s"
                  % (p["rc"], "REFUSES" if p["want_refusal"] else "writes/reads",
                     "said-it" if p["said_it"] else "WRONG-TEXT",
                     "ok" if p["honest"] else "LIES", p["label"]))
        dishonest = [p for p in probes if not p["honest"]]
        print("")
        print("  %d of %d probes report their outcome honestly; %d do not."
              % (len(probes) - len(dishonest), len(probes), len(dishonest)))

    if a.as_json:
        with open(a.as_json, "w", encoding="utf-8") as f:
            json.dump({"rows": rows, "writing": writing, "reading": reading,
                       "counts": {"commands": len(rows), "conditions": len(conditions),
                                  "claims": len(claims), "flippable": len(writing)},
                       "probes": probes}, f, indent=2)
        print("  full measurement written to %s" % a.as_json)

    print("")
    print("%s: %d stored commands (%d conditions, %d claims); %d wrap a heal WRITING "
          "verb and can flip; %d wrap a heal READ and cannot."
          % (MARK, len(rows), len(conditions), len(claims), len(writing), len(reading)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
