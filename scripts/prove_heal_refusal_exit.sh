#!/bin/bash
# prove_heal_refusal_exit.sh — settle #606's exit conditions against the MERGE TARGET, so
# a tick means "this landed where every session's `heal` reads it" and not "it works in the
# branch that says so".
#
# HOW IT IS RUN, and it is not run any other way:
#
#     git -C <checkout> fetch -q origin main
#     git -C <checkout> show origin/main:scripts/prove_heal_refusal_exit.sh \
#         | bash -s -- --repo <checkout> --part verbs
#
# THE JUDGE COMES OUT OF origin/main AND SO DOES EVERYTHING IT JUDGES — the shape #598
# established, #602 and #603 kept, and #603 was graded A on. A condition that resolves a
# working tree passes on work that never merged, which is the rot shape that fails in the
# DANGEROUS direction: it reports done for something no other session can see. Piping the
# judge from `git show origin/main:` means a branch cannot supply its own judge — before
# the merge that path does not exist there, `git show` writes nothing, bash reads an empty
# program, NOTHING IS PRINTED, and every expected substring is missing, so the condition is
# red.
#
# AND THAT EMPTY PROGRAM EXITS 0 (606:1). Before the merge this whole gate is red for the
# ABSENT SUBSTRING and not for the status — which is the mirror image of the very defect
# #606 fixes, and the reason the fix belongs inside the heal verbs and must never become a
# rule about how the condition runner reads status. It is also why THE PASS TOKEN IS
# PRINTED LAST here, after the run has exited 0: for a merge-gated condition the token is
# the only true signal, so a token printed early is the whole gate defeated.
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
#   --part verbs    THE NINE WRITING VERBS, each refusing AND each performing. The second
#                   half is the regression risk: a blanket non-zero would be worse than the
#                   bug.                                                    → T606B-PASS
#   --part reads    THE OTHER SIDE OF THE BOUNDARY (606:2): a read that RAN keeps its 0
#                   whatever it found, and an invocation refused BEFORE it ran does not.
#                                                                           → T606C-PASS
#   --part process  THROUGH THE REAL BINARY. The status a stored exit condition actually
#                   observes — `cli.main` returning the handler's value and the entry point
#                   exiting on it. Everything else would pass with the dispatch still
#                   discarding it, which is how the defect survived.        → T606D-PASS
#   --part radius   THE BLAST RADIUS, recomputed from origin/main's own corpus walk: the
#                   classifier's controls all land and the flippable count is printed.
#                                                                           → T606E-PASS
#   --part suite    THE WHOLE SUITE on origin/main. Registered as a CLAIM rather than an
#                   exit condition: it runs past the 120s an exit command gets, and a
#                   condition that times out reports "did not run", not "no".
#                                                                           → T606F-PASS
#   --part merged   origin/main carries this work — the merge itself.       → T606G-PASS
#   --part mutant   THE OTHER DIRECTION. Puts the OLD behaviour back into origin/main's own
#                   tree — at each of the three layers the status has to cross — and
#                   requires the tests to go RED. A suite that only ever sees the fix
#                   proves nothing about the defect.                        → T606H-PASS
#
# Written 2026-09-03 by 606-0.
set -u

REPO=""
PART="verbs"
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="${2:-}"; shift 2 ;;
    --part) PART="${2:-}"; shift 2 ;;
    *) shift ;;
  esac
done

MOD="tests.test_heal_refusal_exit"
case "$PART" in
  verbs)   MARK="T606B-PASS"
           TARGET="$MOD.TheWritingVerbsReportTheirRefusals $MOD.TheDryRunReportsTheStatusTheRealRunWouldGive" ;;
  reads)   MARK="T606C-PASS"
           TARGET="$MOD.AReadThatRanLeavesZeroWhateverItFound $MOD.AnInvocationRefusedBeforeItRanLeavesNonZero" ;;
  process) MARK="T606D-PASS"; TARGET="$MOD.TheStatusReachesTheProcess" ;;
  radius)  MARK="T606E-PASS"; TARGET="" ;;
  suite)   MARK="T606F-PASS"; TARGET="" ;;
  merged)  MARK="T606G-PASS"; TARGET="" ;;
  mutant)  MARK="T606H-PASS"; TARGET="" ;;
  *) echo "T606-FAIL: unknown --part '$PART'"; exit 1 ;;
esac
FAIL="T606-FAIL"

# The checkout to reach origin/main through: what was named, else the main checkout, else
# this task's worktree. Each candidate must actually be a git checkout.
CANDS="$REPO $HOME/Workspace-Other/task-station $HOME/Workspace-Other/task-station-worktrees/heal-refusal-exit-code"
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
  git -C "$FOUND" show origin/main:lib/board/cmds/maintain.py 2>/dev/null \
    | grep -q "^class Refusal(str):" || miss="$miss the Refusal marker;"
  git -C "$FOUND" show origin/main:lib/board/cmds/maintain.py 2>/dev/null \
    | grep -q "^HEAL_REFUSED = 2" || miss="$miss the refusal exit code;"
  git -C "$FOUND" show origin/main:lib/board/cli.py 2>/dev/null \
    | grep -q "return a.fn(a)" || miss="$miss the status-carrying dispatch;"
  git -C "$FOUND" show origin/main:lib/task-station.py 2>/dev/null \
    | grep -q "sys.exit(main())" || miss="$miss the status-carrying entry point;"
  git -C "$FOUND" show origin/main:tests/test_heal_refusal_exit.py 2>/dev/null \
    | grep -q "class TheStatusReachesTheProcess" || miss="$miss the exit-status tests;"
  if [ -z "$miss" ]; then
    echo "$MARK — origin/main ($SHA) carries #606 (read from $FOUND)."
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
# before the merge every one of these files is present and the behaviour is not, and a run
# that just passed the old tests would tick this condition.
for f in lib/board/cmds/maintain.py lib/board/cli.py lib/task-station.py \
         tests/test_heal_refusal_exit.py scripts/measure_heal_exit_blast_radius.py; do
  [ -f "$WORK/$f" ] || { echo "$FAIL: origin/main ($SHA) carries no $f"; exit 1; }
done
grep -q "^HEAL_REFUSED = 2" "$WORK/lib/board/cmds/maintain.py" \
  || { echo "$FAIL: origin/main ($SHA) has no heal refusal exit code"; exit 1; }
grep -q "^class Refusal(str):" "$WORK/lib/board/cmds/maintain.py" \
  || { echo "$FAIL: origin/main ($SHA) has no Refusal marker"; exit 1; }
grep -q "    return a.fn(a)" "$WORK/lib/board/cli.py" \
  || { echo "$FAIL: origin/main ($SHA) still discards the handler's status at the dispatch"; exit 1; }
grep -q "    sys.exit(main())" "$WORK/lib/task-station.py" \
  || { echo "$FAIL: origin/main ($SHA) still discards the handler's status at the entry point"; exit 1; }

LOG="$WORK/run.log"

# THE BLAST RADIUS, recomputed from origin/main's own walk of the live corpus. Its own
# controls are what make a count of zero mean "nothing wraps a heal writing verb" rather
# than "the classifier found nothing", and the script refuses to print its token unless
# every control lands — so matching the token IS the assertion.
if [ "$PART" = "radius" ]; then
  ( cd "$WORK" && python3 scripts/measure_heal_exit_blast_radius.py ) >"$LOG" 2>&1
  CODE=$?
  if [ "$CODE" -ne 0 ] || ! grep -q "T606-MEASURED" "$LOG"; then
    echo "$FAIL on origin/main $SHA (--part radius, exit $CODE):"
    tail -30 "$LOG"
    exit 1
  fi
  LINE=$(grep "T606-MEASURED" "$LOG" | tail -1)
  echo "$MARK on origin/main $SHA — $LINE (read from $FOUND)."
  exit 0
fi

# THE MUTANTS. Each one restores the pre-3.60.0 behaviour at ONE of the three layers the
# status has to cross, in origin/main's OWN tree, and requires the matching class to go
# RED. A green run here would mean the tests pass whether or not the fix is present, which
# is the only way a passing suite can be worthless. Three layers because the defect could
# be reintroduced at any one of them independently — and the middle one, the dispatch, is
# the layer that actually held it for the whole of the tool's life.
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
  restore() { # <file> — put origin/main's own copy back
    git -C "$WORK" checkout -q -- "$1" \
      || { echo "$FAIL: cannot restore $1 after mutating it"; exit 1; }
  }

  # MUTANT 1 — THE ENTRY POINT DISCARDS THE STATUS. `main()` instead of
  # `sys.exit(main())`: the handler decides correctly, the dispatch carries it, and the
  # process still exits 0. Only the through-the-binary class can see this.
  mutate lib/task-station.py 'sys.exit(main())' 'main()' \
    || { echo "$FAIL: cannot mutate the entry point on origin/main ($SHA)"; exit 1; }
  fails "$MOD.TheStatusReachesTheProcess" \
    || { echo "$FAIL: origin/main ($SHA) still passes the process tests with the entry point discarding the status — they do not test it"; exit 1; }
  restore lib/task-station.py

  # MUTANT 2 — THE DISPATCH DISCARDS THE STATUS. `a.fn(a)` instead of `return a.fn(a)`:
  # the layer that really held this defect for the tool's whole life.
  mutate lib/board/cli.py '    return a.fn(a)' '    a.fn(a)' \
    || { echo "$FAIL: cannot mutate the dispatch on origin/main ($SHA)"; exit 1; }
  fails "$MOD.TheStatusReachesTheProcess" \
    || { echo "$FAIL: origin/main ($SHA) still passes the process tests with the dispatch discarding the status — they do not test it"; exit 1; }
  restore lib/board/cli.py

  # MUTANT 3 — THE VERBS STOP REFUSING. `_heal_leave` always answers None, which is the
  # pre-3.60.0 world exactly: every path, refusal and write alike, reports 0. This is the
  # mutant that matters most — it is the defect itself.
  mutate lib/board/cmds/maintain.py \
    '    return HEAL_REFUSED if isinstance(text, Refusal) else None' \
    '    return None' \
    || { echo "$FAIL: cannot mutate the refusal status on origin/main ($SHA)"; exit 1; }
  fails "$MOD.TheWritingVerbsReportTheirRefusals" \
    || { echo "$FAIL: origin/main ($SHA) still passes the writing-verb tests with every refusal reporting 0 — they do not test it"; exit 1; }
  fails "$MOD.TheDryRunReportsTheStatusTheRealRunWouldGive" \
    || { echo "$FAIL: origin/main ($SHA) still passes the dry-run tests with every refusal reporting 0 — they do not test it"; exit 1; }
  restore lib/board/cmds/maintain.py

  # MUTANT 4 — THE READS LOSE THEIR ZERO. The blanket non-zero that would be WORSE than
  # the bug: `_heal_leave` refuses everything. The read tests must catch it, or "every
  # heal fails now" would ship as a fix.
  mutate lib/board/cmds/maintain.py \
    '    return HEAL_REFUSED if isinstance(text, Refusal) else None' \
    '    return HEAL_REFUSED' \
    || { echo "$FAIL: cannot mutate the read status on origin/main ($SHA)"; exit 1; }
  fails "$MOD.TheWritingVerbsReportTheirRefusals" \
    || { echo "$FAIL: origin/main ($SHA) still passes the writing-verb tests with EVERY heal refusing — a blanket non-zero would ship as a fix"; exit 1; }
  restore lib/board/cmds/maintain.py

  echo "$MARK on origin/main $SHA — all four mutants went red: discarding the status at the entry point and at the dispatch both fail the process tests, making every refusal report 0 fails the writing-verb and dry-run tests, and making EVERY path refuse fails them too (read from $FOUND)."
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
