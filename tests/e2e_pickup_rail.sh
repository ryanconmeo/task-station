#!/usr/bin/env bash
# END-TO-END PROBE FOR THE PICKUP RAIL — the CLI from outside, not the source.
#
# WHY A SHELL SCRIPT AND NOT ANOTHER UNITTEST. The unit suite exercises `cmd_stop_gate`
# in-process, with the facade's seams already imported. The hook that actually runs in
# production is `python3 lib/task-station.py stop-gate --session <sid>` in a FRESH
# interpreter, and its contract is a JSON document on STDOUT that the harness parses.
# A rail that worked in-process and printed nothing from a cold start would pass every
# test and deliver nothing, which is the shape of failure this whole task exists about.
#
# Self-contained: builds its own store in a temp dir, cleans up, and prints PICKUP-RAIL-OK
# on success. Exits non-zero and says which step failed otherwise.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TS="python3 $ROOT/lib/task-station.py"
HOME_DIR="$(mktemp -d)"
export TASK_STATION_HOME="$HOME_DIR"
export TASK_STATION_SESSIONS_DIR="$HOME_DIR/sessions"
mkdir -p "$TASK_STATION_SESSIONS_DIR"
trap 'rm -rf "$HOME_DIR"' EXIT

fail() { echo "PICKUP-RAIL-FAIL: $*"; exit 1; }

PSID="parent-session-0001"
$TS create --session "$PSID" --color black --effort m \
    --title "orchestrator" --summary "drives the loop" >/dev/null || fail "create parent"
$TS decompose --task 1 --into "the child" >/dev/null || fail "decompose"

# The child reaches a terminal state. `done` is the real verb; it calls report_to_parent.
$TS done --task 2 --session "child-session-0001" >/dev/null 2>&1 || true

# 1. THE DURABLE RECORD exists on the PARENT, with nothing running anywhere.
$TS pickup list --task 1 | grep -q "child #2" || fail "no pickup filed on the parent"

# 2. THE GATE. A cold `stop-gate` for a session linked to the parent must emit a
#    decision:block naming the hand-back.
OUT="$($TS stop-gate --session "$PSID")"
printf '%s' "$OUT" | grep -q '"decision": "block"' || fail "the gate did not block: $OUT"
printf '%s' "$OUT" | grep -q 'HANDED WORK BACK' || fail "the block does not name the hand-back"
printf '%s' "$OUT" | grep -q 'pickup take --task 1' || fail "the block does not carry its own next step"

# 3. A CLOSED CHILD IS NOT A DEALT-WITH CHILD. `done` is the verb a finished child runs,
#    so retiring on closure would cancel the notice before anyone read it. It must still
#    be waiting, and the gate must still block.
$TS pickup list --task 1 | grep -q "waiting" || fail "closing the child silently voided the pickup"
[ -n "$($TS stop-gate --session "$PSID")" ] || fail "the gate stopped blocking on a closed-but-ungraded child"

# 4. THE EXPLICIT VERB frees the turn.
ID="$($TS pickup list --task 1 --json | python3 -c 'import json,sys; print([p["id"][:8] for p in json.load(sys.stdin) if not p["taken_ts"]][0])')"
$TS pickup take --task 1 --id "$ID" --session "$PSID" | grep -q "taken" || fail "take did not take"
[ -z "$($TS stop-gate --session "$PSID")" ] || fail "the gate still blocks after the pickup was taken"

# 5. IT RETIRES ITSELF ON ENGAGEMENT — the parent grading the work is the engagement.
$TS decompose --task 1 --into "a second child" --add >/dev/null || fail "decompose 2"
$TS done --task 3 --session "child-session-0002" >/dev/null 2>&1 || true
[ -n "$($TS stop-gate --session "$PSID")" ] || fail "the second hand-back did not block"
$TS grade --task 3 --session "$PSID" \
   --dim G1=A --dim G2=A --dim G3=A --dim G4=A --dim G5=A --dim G6=A \
   --note 'e2e' >/dev/null 2>&1 || fail "grade"
[ -z "$($TS stop-gate --session "$PSID")" ] || fail "grading the child did not retire its pickup"
$TS pickup list --task 1 --all | grep -q "graded" || fail "the ledger does not say WHY it retired"

echo "PICKUP-RAIL-OK"
