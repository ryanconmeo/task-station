#!/usr/bin/env bash
# claims.sh — the re-runnable assertions behind task #572's registered claims.
#
# WHY A SCRIPT AND NOT FOUR SHELL ONE-LINERS. A claim is a COMMAND PLUS THE OUTPUT
# SUBSTRING IT MUST PRINT, and a later session re-runs it to find out whether what was
# proved is STILL true. Two things make a one-liner a bad home for that: the register
# format needs every literal pipe escaped, and — the real reason — a claim must be written
# as a DIRECTION, not a literal. `Ran 5615 tests` is refuted by the next honest release;
# "at least as many as when this landed" is refuted only by tests being deleted, which is
# the thing worth catching. So the FLOOR lives in the COMMAND, here, where it is readable
# and can be raised deliberately, and the claim expects a pass token.
#
# Usage:  bash tests/claims.sh <suite|rail|pickup|nowait|pushlimb>
# Each prints exactly one token on the last line. Never exits non-zero for a failed
# assertion — the token is the verdict, and `claims verify` reads the token.
set -u
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || exit 1

# The floors, as of 3.34.0. Raise them deliberately; never lower one to make a claim pass.
SUITE_FLOOR=5618
PICKUP_FLOOR=47

_ran() {   # (output) -> the "Ran N tests" count, or 0. Never an absence: `Ran 0 tests`
           # followed by `OK` is what a renamed test class prints, and it exits 0.
  printf '%s' "$1" | sed -n 's/^Ran \([0-9][0-9]*\) tests.*/\1/p' | tail -1
}

case "${1:-}" in
  suite)
    # NOT `| tail -n`: the suite's own tests print to stdout, and that interleaves with
    # unittest's stderr summary — the `Ran N tests` line is not reliably in the last few
    # lines. Grep the whole output for the two facts instead.
    out="$(python3 -m unittest discover -t . -s tests 2>&1)"
    n="$(_ran "$out")"; n="${n:-0}"
    if [ "$n" -ge "$SUITE_FLOOR" ] && printf '%s' "$out" | grep -qx "OK"; then
      echo "SUITE-FLOOR-OK ($n >= $SUITE_FLOOR)"
    else
      echo "SUITE-REGRESSED (ran $n, floor $SUITE_FLOOR)"
    fi
    ;;
  pickup)
    out="$(python3 -m unittest tests.test_pickup_rail 2>&1)"
    n="$(_ran "$out")"; n="${n:-0}"
    if [ "$n" -ge "$PICKUP_FLOOR" ] && printf '%s' "$out" | grep -qx "OK"; then
      echo "PICKUP-SUITE-FLOOR-OK ($n >= $PICKUP_FLOOR)"
    else
      echo "PICKUP-SUITE-REGRESSED (ran $n, floor $PICKUP_FLOOR)"
    fi
    ;;
  rail)
    bash tests/e2e_pickup_rail.sh
    ;;
  nowait)
    # THE #532 SHAPE, ASSERTED IN BOTH DIRECTIONS. A live child whose checklist has gone
    # green since its launch must NOT be waited on; a live child with a condition that has
    # never run still must be, because the false-positive direction is the expensive one.
    python3 - <<'PY'
import sys
sys.path.insert(0, "lib")
import turn

def child(ran):
    cond = {"cmd": "x", "expect": ["OK"]}
    if ran:
        cond["last"] = {"ts": 2000.0, "ok": True, "status": "ran",
                        "missing": [], "got": ""}
    return {"id": "c", "seq": 9, "title": "kid", "status": "open", "related": [],
            "steps": [{"text": "s", "done": False, "exit": cond}],
            "events": [{"id": "e", "kind": "child", "ts": 1000.0,
                        "text": "invoked by #1 as implementer: go"}],
            "memos": [], "grades": [], "sessions": []}

landed = turn.child_state(child(True), live={9})
working = turn.child_state(child(False), live={9})
ok = landed == turn.REPORTED and working == turn.RUNNING
print("GREEN-CHECKLIST-BEATS-LIVENESS-OK" if ok
      else "LIVENESS-STILL-WINS (landed=%s working=%s)" % (landed, working))
PY
    ;;
  pushlimb)
    # THE PUSH IS WIRED, asserted against the LOADED module rather than by grepping a
    # file: a definition nothing calls is the exact shape of a rail that ships and never
    # fires. Settles in milliseconds, which is why it is an exit condition while the whole
    # suite is a claim — `exits.command_timeout` is tuned for a grep, deliberately.
    python3 -c 'import inspect, sys; sys.path.insert(0, "lib"); import board.cmds.sub as sub; print("PUSH-LIMB-WIRED-OK" if "_pickup_block(" in inspect.getsource(sub.cmd_stop_gate) else "PUSH-LIMB-MISSING")'
    ;;
  *)
    echo "usage: claims.sh <suite|rail|pickup|nowait|pushlimb>"; exit 2 ;;
esac
