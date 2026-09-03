#!/usr/bin/env bash
# Stop hook: if this session edited files but never tracked a /todo task, refuse
# to end the turn (emit {"decision":"block","reason":...}) until a task is
# attached/created or the session is skipped. All the logic — incl. the anti-wedge
# block cap and self-heal — lives in task-station.py (stop-gate); this passes its stdout
# (a decision JSON blob, or nothing) straight through. Always exit 0.
input=$(cat)
# Suppressed inside delegate-spawned workers — task tracking is the hub's job.
[ -n "$TASK_STATION_SUPPRESS" ] && exit 0
# Shared helper (see hooks/_ts_lib.sh): every call below stays non-fatal, but a
# non-zero exit is now recorded to <data_dir>/logs/hook-health.log instead of being
# swallowed. The stubs keep this hook working verbatim if the lib is ever missing.
# shellcheck source=/dev/null
. "$(dirname "${BASH_SOURCE[0]:-$0}")/_ts_lib.sh" 2>/dev/null || true
if ! command -v ts_run >/dev/null 2>&1; then      # no lib → old behaviour verbatim
  ts_run() { shift; "$@" >/dev/null 2>&1 || true; }
  ts_capture() { shift; "$@" 2>/dev/null || true; }
fi
# Parse stdin with python3 (hard requirement) instead of jq; hookjson.py mirrors
# `jq -r '.path // default'` and is a silent no-op on malformed input.
session_id=$(printf '%s' "$input" | python3 "${CLAUDE_PLUGIN_ROOT}/lib/hookjson.py" session_id unknown)
python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" hook stop-gate --session "$session_id"
# ── Everything below the gate, in ONE python3 ────────────────────────────────────
# These seven best-effort steps used to be seven `ts_run` lines, i.e. seven python3
# start-ups (~90ms each) plus seven fresh imports of the engine — and, worse, seven
# cold starts of the in-process transcript caches, so the parsing the board refresh
# had just done was discarded and redone. lib/stop_steps.py runs the same seven, in
# the same order, in one interpreter:
#
#   stop-nudge           opt-in auto-checkpoint / staleness nudge. The ONLY step that
#                        prints: its additionalContext is read by the harness, which
#                        is why the shell used ts_capture (not ts_run) for it alone.
#   board-refresh        keep an already-open board.html fresh (gated: no flag or no
#                        existing board.html → nothing).
#   obsidian-flush       re-export tasks whose mid-turn vault write was sandbox-denied
#                        (hooks run UNSANDBOXED, so this write succeeds).
#   usage-flush          rescan open/active transcripts so /todo detail's %/$ is current.
#   subscriptions-check  diff subscribed peer feeds, mint memos for any that advanced.
#   recap-auto           once-a-week private recap, stamp-throttled, config-gated OFF.
#   hud-turn-end         freeze this turn's $ delta into the idle status bar.
#
# Per-step isolation is preserved INSIDE stop_steps.py: a step that raises is caught,
# recorded to <data_dir>/logs/hook-health.log under the same label ts_run used, and
# the rest still run. The runner is invoked through ts_capture — capture, because the
# stop-nudge step's stdout must still reach the harness — so a failure of the runner
# ITSELF is recorded too, under the label `stop-steps`. stop-gate above is deliberately
# NOT part of this: the harness reads its stdout for the block contract, so it keeps
# its own process, its own position, and its exact output.
ts_capture stop-steps python3 "${CLAUDE_PLUGIN_ROOT}/lib/stop_steps.py" --session "$session_id"
exit 0
