#!/bin/bash
# prove_spawn_reports_truth.sh — settle #603's exit conditions against the MERGE TARGET,
# so a tick means "this landed where every session's relay reads it" and not "it works in
# the branch that says so".
#
# HOW IT IS RUN, and it is not run any other way:
#
#     git -C <checkout> fetch -q origin main
#     git -C <checkout> show origin/main:scripts/prove_spawn_reports_truth.sh \
#         | bash -s -- --repo <checkout> --part confirm
#
# THE JUDGE COMES OUT OF origin/main AND SO DOES EVERYTHING IT JUDGES — the shape #598
# established and #602 kept. A condition that resolves a working tree passes on work that
# never merged, which is the rot shape that fails in the DANGEROUS direction: it reports
# done for something no other session can see. Piping the judge from `git show origin/main:`
# means a branch cannot supply its own judge — before the merge that path does not exist
# there, `git show` writes nothing, bash reads an empty program, NOTHING IS PRINTED, and
# every expected substring is missing, so the condition is red.
#
# `--repo` names a checkout only so this can reach ITS `origin/main`. Prefer the MAIN
# checkout; a linked worktree is an acceptable fallback because it shares the same object
# store and the same remote-tracking ref — what is read is `origin/main` either way, so the
# fallback cannot make unmerged work pass. (Deliberately NOT `cd <worktree> || cd <main>`,
# which succeeds merely because a directory exists.)
#
# WHAT IT ASSERTS. It materialises origin/main's `lib` and `tests`, REQUIRES that tree to
# carry the code under test — not merely to exist — and then RUNS THE SHIPPED TESTS against
# the shipped engine. Behaviour, not a grep for a line that may never be reached.
#
#   --part confirm  the claim and the ledger (defects 1 and 2): `relay --spawn` says a
#                   window opened only when a session registered, and writes no handoff
#                   until one exists.                                      → T603A-PASS
#   --part cwd      the successor's directory (defect 3): an orchestrator's successor
#                   starts at the hub, and the roster records where the window opens.
#                                                                          → T603B-PASS
#   --part modules  the four test modules that carry this work, on origin/main — the
#                   succession relay, the `invoke` it deliberately did not change, and the
#                   two spawn files it sits between.                       → T603D-PASS
#   --part suite    the WHOLE suite on origin/main. Registered as a CLAIM rather than an
#                   exit condition: it runs past the 120s an exit command gets, and a
#                   condition that times out reports "did not run", not "no".
#                                                                          → T603C-PASS
#   --part merged   origin/main carries this work — the merge itself.      → T603E-PASS
#   --part mutant   THE OTHER DIRECTION. Puts the OLD behaviour back into origin/main's
#                   own tree — confirmation that always says yes, and a successor cwd
#                   that always inherits — and requires the tests to go RED. A suite that
#                   only ever sees the fix proves nothing about the defect.
#                                                                          → T603F-PASS
#
# THE PASS TOKEN IS PRINTED LAST, after the run has exited 0, because the runner matches on
# a substring; a token printed early passes on a crash (#595).
#
# Written 2026-08-31 by 603-0.
set -u

REPO=""
PART="confirm"
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="${2:-}"; shift 2 ;;
    --part) PART="${2:-}"; shift 2 ;;
    *) shift ;;
  esac
done

case "$PART" in
  confirm) MARK="T603A-PASS"; TARGET="tests.test_succession.SpawnReportsWhatHappened" ;;
  cwd)     MARK="T603B-PASS"; TARGET="tests.test_succession.SuccessorStartsAtTheHub" ;;
  modules) MARK="T603D-PASS"
           TARGET="tests.test_succession tests.test_invoke_hardening tests.test_spawn_cwd_poison tests.test_spawn_resolver" ;;
  suite)   MARK="T603C-PASS"; TARGET="" ;;
  merged)  MARK="T603E-PASS"; TARGET="" ;;
  mutant)  MARK="T603F-PASS"; TARGET="" ;;
  *) echo "T603-FAIL: unknown --part '$PART'"; exit 1 ;;
esac
FAIL="T603-FAIL"

# The checkout to reach origin/main through: what was named, else the main checkout, else
# this task's worktree. Each candidate must actually be a git checkout.
CANDS="$REPO $HOME/Workspace-Other/task-station $HOME/Workspace-Other/task-station-worktrees/spawn-reports-truth"
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

# THE MERGE ITSELF, asked of the REF and not of a branch name: does origin/main carry
# this work? Resolved at RUN TIME from the remote-tracking ref, never a pinned sha — a
# hardcoded one answers about the day it was written. Asked through the CONTENT rather
# than `merge-base --is-ancestor <branch>`, because the branch is usually deleted on
# merge and a missing ref would then read as "not landed" forever.
if [ "$PART" = "merged" ]; then
  miss=""
  git -C "$FOUND" show origin/main:lib/board/live_sessions.py 2>/dev/null \
    | grep -q "def await_registration" || miss="$miss the registration check;"
  git -C "$FOUND" show origin/main:lib/board/cmds/loop.py 2>/dev/null \
    | grep -q "def _successor_cwd" || miss="$miss the successor cwd;"
  git -C "$FOUND" show origin/main:tests/test_succession.py 2>/dev/null \
    | grep -q "class SpawnReportsWhatHappened" || miss="$miss the spawn-truth tests;"
  if [ -z "$miss" ]; then
    echo "$MARK — origin/main ($SHA) carries #603's work (read from $FOUND)."
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
# running in — an archived tree has no `.git`, so they fail for a missing directory
# rather than for the behaviour under test. `--shared` borrows the objects instead of
# copying them, and the checkout is the RESOLVED SHA of origin/main, so what runs is the
# merge target and not whatever branch the source checkout has open.
git clone -q --shared --no-checkout "$FOUND" "$WORK" 2>/dev/null \
  || { echo "$FAIL: cannot clone $FOUND"; exit 1; }
FULL=$(git -C "$FOUND" rev-parse origin/main 2>/dev/null)
git -C "$WORK" checkout -q --detach "$FULL" 2>/dev/null \
  || { echo "$FAIL: cannot check out origin/main ($SHA) in the probe clone"; exit 1; }

# REQUIRE THE TREE TO CARRY THE CODE UNDER TEST. A tree that merely exists proves nothing:
# before the merge these files are present but the behaviour is not, and a run that just
# passed the old tests would tick this condition.
for f in lib/board/live_sessions.py lib/board/cmds/loop.py tests/test_succession.py; do
  [ -f "$WORK/$f" ] || { echo "$FAIL: origin/main ($SHA) carries no $f"; exit 1; }
done
grep -q "def await_registration" "$WORK/lib/board/live_sessions.py" \
  || { echo "$FAIL: origin/main ($SHA) has no spawn-registration check"; exit 1; }
grep -q "def _successor_cwd" "$WORK/lib/board/cmds/loop.py" \
  || { echo "$FAIL: origin/main ($SHA) resolves no successor cwd"; exit 1; }
case "$PART" in
  confirm|cwd)
    CLASS=${TARGET##*.}
    grep -q "class $CLASS" "$WORK/tests/test_succession.py" \
      || { echo "$FAIL: origin/main ($SHA) has no $CLASS"; exit 1; } ;;
esac

LOG="$WORK/run.log"

# THE MUTANTS. Each one restores the pre-#603 behaviour in origin/main's OWN tree and
# requires the matching class to go RED. A green run here would mean the tests pass
# whether or not the fix is present, which is the only way a passing suite can be
# worthless.
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
  mutate lib/board/cmds/loop.py \
    "return bool(live_sessions.await_registration(sid))" "return True" \
    || { echo "$FAIL: cannot mutate the confirmation on origin/main ($SHA)"; exit 1; }
  fails tests.test_succession.SpawnReportsWhatHappened \
    || { echo "$FAIL: origin/main ($SHA) still passes the spawn-truth tests with the confirmation stubbed to always say yes — they do not test it"; exit 1; }
  mutate lib/board/cmds/loop.py \
    "if _loop.is_orchestrator(task):" "if False:" \
    || { echo "$FAIL: cannot mutate the successor cwd on origin/main ($SHA)"; exit 1; }
  fails tests.test_succession.SuccessorStartsAtTheHub \
    || { echo "$FAIL: origin/main ($SHA) still passes the hub-cwd tests with the orchestrator branch removed — they do not test it"; exit 1; }
  echo "$MARK on origin/main $SHA — both mutants went red: stubbing the registration check fails the spawn-truth tests, and removing the orchestrator branch fails the hub-cwd tests (read from $FOUND)."
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
