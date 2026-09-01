#!/bin/bash
# prove_heal_verb_parser.sh — settle #594's exit conditions against the MERGE TARGET, so a
# tick means "this landed where every session's heal reads it" and not "it works in the
# branch that says so".
#
# HOW IT IS RUN, and it is not run any other way:
#
#     git -C <checkout> fetch -q origin main
#     git -C <checkout> show origin/main:scripts/prove_heal_verb_parser.sh \
#         | bash -s -- --repo <checkout> --part refuses
#
# THE JUDGE COMES OUT OF origin/main AND SO DOES EVERYTHING IT JUDGES — the shape #598
# established and #602 and #603 kept. A condition that resolves a working tree passes on
# work that never merged, which is the rot shape that fails in the DANGEROUS direction: it
# reports done for something no other session can see. Piping the judge from
# `git show origin/main:` means a branch cannot supply its own judge — before the merge
# that path does not exist there, `git show` writes nothing, bash reads an empty program,
# NOTHING IS PRINTED, and every expected substring is missing, so the condition is red.
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
#   --part refuses  the defect itself: --split/--merge/--into refuse a batch they cannot
#                   fully read, name what they are about to mark, take the qualified
#                   form, and have --dry-run.                              → T594A-PASS
#   --part verbs    the whole heal-verb surface on origin/main — the two consolidation
#                   verbs, the CLI seam they are driven through, and the ownership verb
#                   whose parser they now share.                           → T594B-PASS
#   --part suite    the WHOLE suite on origin/main. Registered as a CLAIM rather than an
#                   exit condition: it runs past the 120s an exit command gets, and a
#                   condition that times out reports "did not run", not "no".
#                                                                          → T594C-PASS
#   --part merged   origin/main carries this work — the merge itself.      → T594D-PASS
#   --part mutant   THE OTHER DIRECTION. Puts the OLD DROPPING PARSER back into
#                   origin/main's own tree — the `int()`-or-`continue` loop, restored on
#                   the exact function the fix routes through — and requires the tests to
#                   go RED. A suite that only ever sees the fix proves nothing about the
#                   defect, and this is what makes the both-ways proof re-runnable forever
#                   instead of a one-time claim in a report.                → T594E-PASS
#
# THE PASS TOKEN IS PRINTED LAST, after the run has exited 0, because the runner matches on
# a substring; a token printed early passes on a crash (#595).
#
# Written 2026-09-01 by 594-0.
set -u

REPO=""
PART="refuses"
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="${2:-}"; shift 2 ;;
    --part) PART="${2:-}"; shift 2 ;;
    *) shift ;;
  esac
done

case "$PART" in
  refuses) MARK="T594A-PASS"
           TARGET="tests.test_heal.TestWritingVerbsRefuseWhatTheyCannotRead" ;;
  verbs)   MARK="T594B-PASS"
           TARGET="tests.test_heal.TestWritingVerbsRefuseWhatTheyCannotRead tests.test_heal.TestExplicitVerbsOnTheCli tests.test_heal.TestSplitVerb tests.test_heal.TestMergeVerb tests.test_decision_ownership" ;;
  suite)   MARK="T594C-PASS"; TARGET="" ;;
  merged)  MARK="T594D-PASS"; TARGET="" ;;
  mutant)  MARK="T594E-PASS"; TARGET="" ;;
  *) echo "T594-FAIL: unknown --part '$PART'"; exit 1 ;;
esac
FAIL="T594-FAIL"

# The checkout to reach origin/main through: what was named, else the main checkout, else
# this task's worktree. Each candidate must actually be a git checkout.
CANDS="$REPO $HOME/Workspace-Other/task-station $HOME/Workspace-Other/task-station-worktrees/heal-verb-parser"
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
# work? Resolved at RUN TIME from the remote-tracking ref, never a pinned sha — a hardcoded
# one answers about the day it was written. Asked through the CONTENT rather than
# `merge-base --is-ancestor <branch>`, because the branch is usually deleted on merge and a
# missing ref would then read as "not landed" forever.
if [ "$PART" = "merged" ]; then
  miss=""
  git -C "$FOUND" show origin/main:lib/board/cmds/maintain.py 2>/dev/null \
    | grep -q "def _verb_preview" || miss="$miss the split/merge preview;"
  # THE REMOVAL IS PART OF THE WORK, so it is asserted as a removal. A fix that added the
  # strict parser and left the dropping one beside it would be one `getattr` away from the
  # defect coming back on the next verb.
  git -C "$FOUND" show origin/main:lib/board/cmds/maintain.py 2>/dev/null \
    | grep -q "^def _split_int_list" && miss="$miss the dropping parser is still there;"
  git -C "$FOUND" show origin/main:tests/test_heal.py 2>/dev/null \
    | grep -q "class TestWritingVerbsRefuseWhatTheyCannotRead" \
    || miss="$miss the refusal tests;"
  if [ -z "$miss" ]; then
    echo "$MARK — origin/main ($SHA) carries #594's work (read from $FOUND)."
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
# running in — an archived tree has no `.git`, so they fail for a missing directory rather
# than for the behaviour under test (a real red #603 hit). `--shared` borrows the objects
# instead of copying them, and the checkout is the RESOLVED SHA of origin/main, so what
# runs is the merge target and not whatever branch the source checkout has open.
git clone -q --shared --no-checkout "$FOUND" "$WORK" 2>/dev/null \
  || { echo "$FAIL: cannot clone $FOUND"; exit 1; }
FULL=$(git -C "$FOUND" rev-parse origin/main 2>/dev/null)
git -C "$WORK" checkout -q --detach "$FULL" 2>/dev/null \
  || { echo "$FAIL: cannot check out origin/main ($SHA) in the probe clone"; exit 1; }

# REQUIRE THE TREE TO CARRY THE CODE UNDER TEST. A tree that merely exists proves nothing:
# before the merge these files are present but the behaviour is not, and a run that just
# passed the old tests would tick this condition.
for f in lib/board/cmds/maintain.py lib/board/decisions.py tests/test_heal.py; do
  [ -f "$WORK/$f" ] || { echo "$FAIL: origin/main ($SHA) carries no $f"; exit 1; }
done
grep -q "def _verb_preview" "$WORK/lib/board/cmds/maintain.py" \
  || { echo "$FAIL: origin/main ($SHA) has no split/merge preview"; exit 1; }
grep -q "^def _split_int_list" "$WORK/lib/board/cmds/maintain.py" \
  && { echo "$FAIL: origin/main ($SHA) still carries the dropping list parser"; exit 1; }
grep -q "class TestWritingVerbsRefuseWhatTheyCannotRead" "$WORK/tests/test_heal.py" \
  || { echo "$FAIL: origin/main ($SHA) has no refusal tests"; exit 1; }

LOG="$WORK/run.log"

# THE MUTANT. It restores the pre-#594 behaviour in origin/main's OWN tree — the dropping
# parser, put back on the one function every writing verb's list goes through — and
# requires the refusal tests to go RED. A green run here would mean the tests pass whether
# or not the fix is present, which is the only way a passing suite can be worthless.
if [ "$PART" = "mutant" ]; then
  ANCHOR='def _split_decision_refs(raw, task, flag):'
  MUTANT='def _split_decision_refs(raw, task, flag):
    if raw is None:
        return [], None
    items = raw if isinstance(raw, (list, tuple)) else str(raw).replace(" ", ",").split(",")
    out = []
    for v in items:
        try:
            out.append(int(str(v).strip()))
        except (TypeError, ValueError):
            continue
    return out, None


def _split_decision_refs_unused(raw, task, flag):'
  WORK="$WORK" A="$ANCHOR" B="$MUTANT" python3 - <<'PYM'
import os
path = os.path.join(os.environ["WORK"], "lib/board/cmds/maintain.py")
src = open(path).read()
if os.environ["A"] not in src:
    raise SystemExit("MUTANT-ANCHOR-MISSING :: %s" % os.environ["A"])
open(path, "w").write(src.replace(os.environ["A"], os.environ["B"], 1))
PYM
  [ $? -eq 0 ] || { echo "$FAIL: cannot put the dropping parser back on origin/main ($SHA)"; exit 1; }
  ( cd "$WORK" && python3 -m unittest tests.test_heal.TestWritingVerbsRefuseWhatTheyCannotRead ) >"$LOG" 2>&1
  if [ $? -eq 0 ]; then
    echo "$FAIL: origin/main ($SHA) still passes the refusal tests with the OLD DROPPING PARSER put back — they do not test it"
    exit 1
  fi
  DROPPED=$(grep -cE "^(FAIL|ERROR):" "$LOG")
  echo "$MARK on origin/main $SHA — the mutant went red: restoring the dropping list parser fails $DROPPED of the refusal tests, so they measure the defect and not the weather (read from $FOUND)."
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
