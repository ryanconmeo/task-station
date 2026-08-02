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
python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" stop-gate --session "$session_id"
# Opt-in auto-checkpoint: at most ONE non-blocking nudge (additionalContext, not a
# block), gated in task-station.py so today's Stop behaviour is unchanged by default:
#   • PROACTIVE — when the session is estimated past --checkpoint-at tokens, prompt a
#     full structured /todo save NOW (before auto-compaction); fires once per episode.
#   • else a LIGHT staleness nudge when the digest is merely stale.
# Prints nothing unless auto-checkpoint is on AND a task is attached. Best-effort —
# never disrupts Stop. Safe alongside stop-gate: the gate's block only fires on an
# UNATTACHED edit session, while this nudge only fires when a task IS attached.
ts_capture stop-nudge python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" stop-nudge --session "$session_id"
# Opt-in board auto-refresh: keep an already-open board.html fresh so its meta-refresh
# shows current state. Strictly gated + silent in task-station.py (no flag → nothing;
# no existing board.html → nothing); best-effort here so the Stop hook is never disrupted.
ts_run board-refresh python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" board --refresh-if-live
# Obsidian sandbox auto-flush (Fix B): re-export any tasks whose mid-turn export was
# SANDBOX-DENIED (a vault under ~/Documents/iCloud is unwritable from a project
# session). Hooks run UNSANDBOXED — same trust level as monitors — so THIS write
# succeeds, healing the vault with zero config. Independent of the stop-gate above:
# never blocks or delays the turn. --quiet ⇒ a silent, cheap no-op when export is off
# or nothing is pending (it self-gates on a dirty task before doing any work).
ts_run obsidian-flush python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" obsidian --flush --quiet
# Usage ledger auto-flush (WS1): incrementally rescan open/active tasks' transcripts
# so the derived per-model %/$ on /todo detail stays current. Reads only local
# session files; self-gates on `usage_tracking` (a cheap no-op when off) and
# swallows all errors (stale numbers never break Stop). Suppressed in workers above.
ts_run usage-flush python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" usage --flush --quiet
# F5 subscriptions (correspondence): diff subscribed peer feeds and mint memos for any
# that advanced. --throttle self-gates in task-station.py (skips if it ran within the
# interval) and stays silent; fail-open (never disrupts Stop). Suppressed in workers above.
ts_run subscriptions-check python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" subscriptions check --throttle --session "$session_id"
# Private weekly recap auto-generate (task 444): once-per-week, throttled by a stamp
# file, gated on the `recap` config toggle (default OFF → nothing). Runs AFTER the
# usage flush so last week's numbers are current. Strictly local (writes only under
# <data_dir>/recaps/), fail-open, and zero tokens unless a curator is configured.
ts_run recap-auto python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" recap --auto-if-due --quiet
# Cost HUD (WS7): freeze the just-ended turn's $ delta into the session's snapshot
# so the idle status bar shows the finished turn until the next prompt re-baselines.
# Self-gates on `--hud` (cheap no-op when off); best-effort — never disrupts Stop.
ts_run hud-turn-end python3 "${CLAUDE_PLUGIN_ROOT}/lib/hud.py" turn-end --session "$session_id"
exit 0
