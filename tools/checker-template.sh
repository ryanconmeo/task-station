#!/bin/sh
# A DIRECTION CHECKER — the shape to copy for an exit condition or a claim.
#
# WHY THIS EXISTS. `test COUNT = LITERAL` is falsified by any legitimate release. On one
# real task five of seventeen claims went red in four days for that reason alone, and one
# of them hid three genuinely broken links behind its stale baseline: the claim was red
# already, so the new breakage changed nothing anybody could see. A gate that goes red
# when the work SUCCEEDS trains its reader to stop reading it, which costs more than the
# gate ever delivered.
#
# THE FIX IS NOT NEW MACHINERY. A claim and an exit condition are both already
# "<command> plus the substrings its output must contain", which is enough: put the
# COMPARISON inside the command, print a PASS token, and expect the token. So the shape
# below needs nothing from the engine that 3.0.0 did not already ship.
#
# THE THREE RULES, and each one was learned from a condition that lied:
#
#   1. A FLOOR OR A CEILING, NEVER EQUALITY. Write the invariant, not the snapshot. The
#      real requirement was never "124 lines"; it is "the facade must never grow back into
#      the monolith it replaced" — so the condition is a ceiling. A suite count is a floor:
#      a release RAISES it, and raising it must not turn the gate red.
#
#   2. PRINT THE MEASURED VALUE NEXT TO THE VERDICT. `PASS` alone tells the next reader
#      nothing about how much headroom is left, and a gate that is one commit from red
#      looks identical to one with room to spare. Print both, always.
#
#   3. PRINT A VERDICT ON EVERY PATH, INCLUDING THE BROKEN ONES. A missing file, a repo
#      that moved, a command that could not run — each must print FAIL, never nothing. A
#      condition whose command produces EMPTY OUTPUT goes red with no diagnostic, and
#      nothing in the transcript says which half failed. That is a real observed failure:
#      a path test joined by `&&` short-circuited a test run, the tests never ran, the
#      output was empty, and a finished track read as incomplete. `exit-add` now refuses
#      that shape by name (`path-test-and`) — but the discipline is what generalises.
#
# NEVER `&&` A PATH TEST IN FRONT OF THE REAL COMMAND. Check the path INSIDE the script
# and print FAIL yourself, the way the guards below do.
#
# REGISTER IT — the token is the whole assertion, and it is a positive one:
#   task-station exit-add --task <ref> --step <n> \
#       --cmd '~/.task-station/checker/c1-suite-green.sh' --expect 'C1-PASS'
#   task-station claims --task <ref> \
#       --register 'C1|~/.task-station/checker/c1-suite-green.sh|C1-PASS'
#
# Copy this file, rename the token, keep the shape.

ID=C0                                  # the claim/step id, so one token names one gate
REPO=${REPO:-$HOME/Workspace-Other/task-station}

# ---- a FLOOR: this number may grow, and must never shrink past the low-water mark ----
FLOOR=5091
OUT=$(cd "$REPO" 2>/dev/null && python3 -m unittest discover -t . -s tests 2>&1)
if [ -z "$OUT" ]; then
    # RULE 3: the command could not run. Say so — do not fall through silently.
    echo "$ID-FAIL nothing ran (repo=$REPO unreadable?) floor=$FLOOR"
    exit 1
fi
# Read the count out of the suite's own line. `discover -k <missing>` prints "Ran 0 tests"
# then "OK" and exits 0, so OK on its own is not evidence — the count is half the gate.
N=$(printf '%s' "$OUT" | sed -n 's/^Ran \([0-9][0-9]*\) tests.*/\1/p' | tail -1)
case "$OUT" in *"
OK"*) GREEN=1 ;; *) GREEN=0 ;; esac
if [ "$GREEN" = 1 ] && [ -n "$N" ] && [ "$N" -ge "$FLOOR" ]; then
    echo "$ID-PASS green n=$N floor=$FLOOR"          # RULE 2: the value, not just the verdict
else
    echo "$ID-FAIL green=$GREEN n=${N:-none} floor=$FLOOR"
    exit 1
fi

# ---- a CEILING: the mirror image, for anything that must not grow back ----
# CEIL=200
# N=$(wc -l < "$REPO/lib/task-station.py" 2>/dev/null | tr -d ' ')
# if [ -n "$N" ] && [ "$N" -le "$CEIL" ]; then
#     echo "$ID-PASS lines=$N ceiling=$CEIL"
# else
#     echo "$ID-FAIL lines=${N:-none} ceiling=$CEIL"   # unreadable file reads FAIL, never silent
#     exit 1
# fi
