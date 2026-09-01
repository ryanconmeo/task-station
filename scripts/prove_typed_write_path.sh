#!/bin/bash
# prove_typed_write_path.sh — settle #596 increment 2's exit conditions against the MERGE
# TARGET, so a tick means "this landed where every session's `update` reads it" and not
# "it works in the branch that says so".
#
# HOW IT IS RUN, and it is not run any other way:
#
#     git -C <checkout> fetch -q origin main
#     git -C <checkout> show origin/main:scripts/prove_typed_write_path.sh \
#         | bash -s -- --repo <checkout> --part ask
#
# THE JUDGE COMES OUT OF origin/main AND SO DOES EVERYTHING IT JUDGES — the shape #598
# established, #602 kept and #603 was graded A on. A condition that resolves a working tree
# passes on work that never merged, which is the rot shape that fails in the DANGEROUS
# direction: it reports done for something no other session can see. Piping the judge from
# `git show origin/main:` means a branch cannot supply its own judge — before the merge that
# path does not exist there, `git show` writes nothing, bash reads an empty program, NOTHING
# IS PRINTED, and every expected substring is missing, so the condition is red.
#
# `--repo` names a checkout only so this can reach ITS `origin/main`. Prefer the MAIN
# checkout; a linked worktree is an acceptable fallback because it shares the same object
# store and the same remote-tracking ref — what is read is `origin/main` either way, so the
# fallback cannot make unmerged work pass.
#
# WHAT IT ASSERTS. It materialises origin/main, REQUIRES that tree to carry the code under
# test — not merely to exist — and then RUNS THE SHIPPED TESTS against the shipped engine.
# Behaviour, not a grep for a line that may never be reached.
#
#   --part ask       THE ASK-SITE: --kind/--subject bind the LAST --decision (the rule
#                    --supersedes has been got wrong on twice), the existing-entry and
#                    clear forms, and the two `--kind` vocabularies staying separate.
#                                                                          → T596B-PASS
#   --part advisory  THE LAW: the declaration prompt nags and NEVER refuses. A long
#                    undeclared decision is stored in full at exit 0, an untyped entry is
#                    still a plain string, and a refused subject keeps its decision.
#                                                                          → T596C-PASS
#   --part writers   THE THREE MACHINE WRITERS declare their own kind — gate grade =
#                    measurement, heal's merge summary = release-record, memo promotion =
#                    process-note.                                         → T596D-PASS
#   --part element   INCREMENT 1 STILL HOLDS: the element tests, unbroken by the callers.
#                                                                          → T596E-PASS
#   --part suite     THE WHOLE SUITE on origin/main. Registered as a CLAIM rather than an
#                    exit condition: it runs past the 120s an exit command gets, and a
#                    condition that times out reports "did not run", not "no".
#                                                                          → T596F-PASS
#   --part merged    origin/main carries this work — the merge itself.      → T596G-PASS
#   --part mutant    THE OTHER DIRECTION. Puts the OLD behaviour back into origin/main's
#                    own tree — the declaration dropped at the ask-site, and each machine
#                    writer's kind removed — and requires the tests to go RED. A suite
#                    that only ever sees the fix proves nothing about the defect.
#                                                                          → T596H-PASS
#
# THE PASS TOKEN IS PRINTED LAST, after the run has exited 0, because the runner matches on
# a substring; a token printed early passes on a crash (#595).
#
# Written 2026-09-01 by 596-1 for increment 2. Increment 1 shipped as 3.46.0 and registered
# its own condition on the element; this one does not restate it, it RE-RUNS it (--part
# element), because a caller that broke the element is the failure this increment could
# plausibly cause.
set -u

REPO=""
PART="ask"
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="${2:-}"; shift 2 ;;
    --part) PART="${2:-}"; shift 2 ;;
    *) shift ;;
  esac
done

case "$PART" in
  ask)      MARK="T596B-PASS"
            TARGET="tests.test_typed_write_path.TheAttachRuleIsPinsAndItIsTested tests.test_typed_write_path.DeclaringAnEntryThatAlreadyExists tests.test_typed_write_path.TheOtherKindFlagIsUntouched" ;;
  advisory) MARK="T596C-PASS"
            TARGET="tests.test_typed_write_path.TheAdvisoryNeverRefuses tests.test_typed_write_path.AMachineWriterNeverLosesItsDecisionOverAClassification" ;;
  writers)  MARK="T596D-PASS"
            TARGET="tests.test_typed_write_path.TheMachineWritersDeclareTheirOwnKind" ;;
  element)  MARK="T596E-PASS"; TARGET="tests.test_decision_declaration" ;;
  suite)    MARK="T596F-PASS"; TARGET="" ;;
  merged)   MARK="T596G-PASS"; TARGET="" ;;
  mutant)   MARK="T596H-PASS"; TARGET="" ;;
  *) echo "T596-FAIL: unknown --part '$PART'"; exit 1 ;;
esac
FAIL="T596-FAIL"

# The checkout to reach origin/main through: what was named, else the main checkout, else
# this task's worktree. Each candidate must actually be a git checkout.
CANDS="$REPO $HOME/Workspace-Other/task-station $HOME/Workspace-Other/task-station-worktrees/typed-decisions-2"
FOUND=""
for c in $CANDS; do
  [ -n "$c" ] || continue
  git -C "$c" rev-parse --git-dir >/dev/null 2>&1 || continue
  FOUND="$c"; break
done
[ -n "$FOUND" ] || { echo "$FAIL: no task-station checkout among: $CANDS"; exit 1; }

git -C "$FOUND" fetch -q origin main 2>/dev/null \
  || { echo "$FAIL: cannot fetch origin/main from $FOUND"; exit 1; }
SHA=$(git -C "$FOUND" rev-parse --short origin/main 2>/dev/null)
[ -n "$SHA" ] || { echo "$FAIL: cannot resolve origin/main in $FOUND"; exit 1; }

# THE MERGE ITSELF, asked of the REF and not of a branch name: does origin/main carry this
# work? Resolved at RUN TIME from the remote-tracking ref, never a pinned sha. Asked through
# the CONTENT rather than `merge-base --is-ancestor <branch>`, because the branch is usually
# deleted on merge and a missing ref would then read as "not landed" forever.
if [ "$PART" = "merged" ]; then
  miss=""
  git -C "$FOUND" show origin/main:lib/board/decisions.py 2>/dev/null \
    | grep -q "def declaration_advisory" || miss="$miss the declaration advisory;"
  git -C "$FOUND" show origin/main:lib/board/cli.py 2>/dev/null \
    | grep -q '"--kind-decision"' || miss="$miss the ask-site flags;"
  git -C "$FOUND" show origin/main:lib/board/model.py 2>/dev/null \
    | grep -q "def append_decision(task, text, session=None, kind=None)" \
    || miss="$miss the machine-writer kind;"
  git -C "$FOUND" show origin/main:tests/test_typed_write_path.py 2>/dev/null \
    | grep -q "class TheMachineWritersDeclareTheirOwnKind" || miss="$miss the write-path tests;"
  if [ -z "$miss" ]; then
    echo "$MARK — origin/main ($SHA) carries #596 increment 2 (read from $FOUND)."
    exit 0
  fi
  echo "$FAIL: origin/main ($SHA) is missing$miss so this has not landed."
  exit 1
fi

TMP=$(mktemp -d) || { echo "$FAIL: no tmpdir"; exit 1; }
trap 'rm -rf "$TMP"' EXIT
WORK="$TMP/tree"
# A CLONE RATHER THAN AN ARCHIVE, and the whole tree rather than `lib tests`. The suite
# reaches tools/ and scripts/, and several of its tests ask git about the repo they are
# running in — an archived tree has no `.git`, so `test_ship_script.py` fails for a missing
# directory rather than for the behaviour under test. `--shared` borrows the objects instead
# of copying them, and the checkout is the RESOLVED SHA of origin/main, so what runs is the
# merge target and not whatever branch the source checkout has open.
git clone -q --shared --no-checkout "$FOUND" "$WORK" 2>/dev/null \
  || { echo "$FAIL: cannot clone $FOUND"; exit 1; }
FULL=$(git -C "$FOUND" rev-parse origin/main 2>/dev/null)
git -C "$WORK" checkout -q --detach "$FULL" 2>/dev/null \
  || { echo "$FAIL: cannot check out origin/main ($SHA) in the probe clone"; exit 1; }

# REQUIRE THE TREE TO CARRY THE CODE UNDER TEST. A tree that merely exists proves nothing:
# before the merge these files are all present but the behaviour is not, and a run that just
# passed the old tests would tick this condition.
for f in lib/board/decisions.py lib/board/cli.py lib/board/model.py \
         lib/board/cmds/manage.py lib/board/cmds/loop.py lib/board/memos.py \
         lib/board/heal.py tests/test_typed_write_path.py; do
  [ -f "$WORK/$f" ] || { echo "$FAIL: origin/main ($SHA) carries no $f"; exit 1; }
done
grep -q "def declaration_advisory" "$WORK/lib/board/decisions.py" \
  || { echo "$FAIL: origin/main ($SHA) has no declaration advisory"; exit 1; }
grep -q '"--kind-decision"' "$WORK/lib/board/cli.py" \
  || { echo "$FAIL: origin/main ($SHA) has no ask-site declaration flags on update"; exit 1; }
grep -q "def append_decision(task, text, session=None, kind=None)" "$WORK/lib/board/model.py" \
  || { echo "$FAIL: origin/main ($SHA) has no machine-writer kind on append_decision"; exit 1; }

LOG="$WORK/run.log"

# THE MUTANTS. Each one restores the pre-increment-2 behaviour in origin/main's OWN tree and
# requires the matching class to go RED. A green run here would mean the tests pass whether
# or not the fix is present, which is the only way a passing suite can be worthless.
if [ "$PART" = "mutant" ]; then
  mutate() {  # <file> <needle> <replacement>
    WORK="$WORK" F="$1" A="$2" B="$3" python3 - <<'PYM'
import os
path = os.path.join(os.environ["WORK"], os.environ["F"])
src = open(path).read()
if os.environ["A"] not in src:
    raise SystemExit("MUTANT-ANCHOR-MISSING %s :: %s" % (os.environ["F"], os.environ["A"]))
open(path, "w").write(src.replace(os.environ["A"], os.environ["B"], 1))
PYM
  }
  fails() {   # <dotted test target> — 0 when the run FAILED, which is what we want
    ( cd "$WORK" && python3 -m unittest "$1" ) >>"$LOG" 2>&1 && return 1 || return 0
  }
  # MUTANT 1 — THE ASK-SITE DROPS THE DECLARATION. `set_kind` is never called from the
  # attach path, so `--kind` is accepted and silently does nothing: the pre-increment-2
  # world, where the field existed and no caller reached it.
  mutate lib/board/cmds/manage.py \
    'ok, err = _dec.set_kind(entries, last_decision_idx, a.kind, flag="--kind")' \
    'ok, err = (True, None)' \
    || { echo "$FAIL: cannot mutate the ask-site on origin/main ($SHA)"; exit 1; }
  fails tests.test_typed_write_path.TheAttachRuleIsPinsAndItIsTested \
    || { echo "$FAIL: origin/main ($SHA) still passes the attach-rule tests with --kind stubbed to a no-op — they do not test it"; exit 1; }

  # MUTANT 2 — THE ADVISORY NEVER FIRES. Returning None makes the prompt invisible, which
  # is the state before this increment. The tests must notice the missing nag; the point of
  # the nag is that it is the ONLY thing asking, so a silent one asks nothing.
  mutate lib/board/decisions.py \
    'def declaration_advisory(entry, index1=None, limit=LONG_DECISION_CHARS):' \
    'def declaration_advisory(entry, index1=None, limit=LONG_DECISION_CHARS):
    return None' \
    || { echo "$FAIL: cannot mutate the advisory on origin/main ($SHA)"; exit 1; }
  fails tests.test_typed_write_path.TheAdvisoryNeverRefuses \
    || { echo "$FAIL: origin/main ($SHA) still passes the advisory tests with the prompt silenced — they do not test it"; exit 1; }

  # MUTANT 3 — THE MACHINE WRITERS STOP DECLARING. `append_decision` ignores its `kind`, so
  # all three writers fall back to the untyped string they wrote before. This is the mutant
  # that matters most: the three declarations are the only kinds anything writes today.
  mutate lib/board/model.py \
    '    if kind:
        _dec.set_kind(entries, len(entries), kind, flag="kind=")' \
    '    if False:
        pass' \
    || { echo "$FAIL: cannot mutate append_decision on origin/main ($SHA)"; exit 1; }
  fails tests.test_typed_write_path.TheMachineWritersDeclareTheirOwnKind \
    || { echo "$FAIL: origin/main ($SHA) still passes the machine-writer tests with the kind dropped from append_decision — they do not test it"; exit 1; }

  echo "$MARK on origin/main $SHA — all three mutants went red: stubbing the ask-site fails the attach-rule tests, silencing the prompt fails the advisory tests, and dropping the kind from append_decision fails the machine-writer tests (read from $FOUND)."
  exit 0
fi

if [ -n "$TARGET" ]; then
  ( cd "$WORK" && python3 -m unittest $TARGET ) >"$LOG" 2>&1
else
  ( cd "$WORK" && python3 -m unittest discover -s tests -t . ) >"$LOG" 2>&1
fi
CODE=$?
RAN=$(grep -oE "Ran [0-9]+ tests?" "$LOG" | tail -1)

if [ "$CODE" -ne 0 ]; then
  echo "$FAIL on origin/main $SHA (--part $PART, exit $CODE):"
  tail -30 "$LOG"
  exit 1
fi
echo "$MARK on origin/main $SHA — ${RAN:-the run}, all green (--part $PART, read from $FOUND)."
