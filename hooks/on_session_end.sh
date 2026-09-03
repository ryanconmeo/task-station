#!/usr/bin/env bash
# SessionEnd hook: the EXACT end-of-session pass — stamp this session's roster row
# with WHY it ended, put one line on the attached task's feed, and stop the delegate
# workers this session spawned. The SessionStart orphan sweep stays exactly as it is:
# SessionEnd is NOT guaranteed to fire on a crash or a kill, so it is the clean path
# only and the sweep is the crash backstop (see lib/task-station.py's
# "SessionEnd: the EXACT end-of-session pass" block for the decision-36 amendment).
#
# BUDGET. All SessionEnd hooks SHARE a 1.5-second budget; the manifest raises ours
# with `"timeout": 10`, which is a ceiling and not an allowance — the work below is
# meant to finish in well under 2s, and the engine spends a subprocess only when this
# session actually spawned a worker.
#
# SessionEnd cannot block and its stdout goes nowhere, so everything here is masked
# exactly like the SessionStart hook's best-effort rail.
input=$(cat)
# Suppressed inside delegate-spawned workers — task tracking is the hub's job.
[ -n "$TASK_STATION_SUPPRESS" ] && exit 0
[ -n "$CLAUDE_PLUGIN_ROOT" ] || exit 0
# Shared helper (see hooks/_ts_lib.sh): the call stays non-fatal, but a non-zero exit
# is recorded to <data_dir>/logs/hook-health.log instead of vanishing. The stubs keep
# this hook working verbatim if the lib is ever missing.
# shellcheck source=/dev/null
. "$(dirname "${BASH_SOURCE[0]:-$0}")/_ts_lib.sh" 2>/dev/null || true
if ! command -v ts_run >/dev/null 2>&1; then      # no lib → old behaviour verbatim
  ts_run() { shift; "$@" >/dev/null 2>&1 || true; }
  ts_capture() { shift; "$@" 2>/dev/null || true; }
fi
# Parse stdin with python3 (hard requirement) instead of jq; hookjson.py mirrors
# `jq -r '.path // default'` and is a silent no-op on malformed input.
session_id=$(printf '%s' "$input" | python3 "$CLAUDE_PLUGIN_ROOT/lib/hookjson.py" session_id unknown)
# The reason field is `session_end_reason`; some builds send `reason` instead, so read
# the documented one first and fall back — one extra python3 only when it is absent.
reason=$(printf '%s' "$input" | python3 "$CLAUDE_PLUGIN_ROOT/lib/hookjson.py" session_end_reason)
if [ -z "$reason" ]; then
  reason=$(printf '%s' "$input" | python3 "$CLAUDE_PLUGIN_ROOT/lib/hookjson.py" reason other)
fi
ts_run session-end python3 "$CLAUDE_PLUGIN_ROOT/lib/task-station.py" hook session-end \
  --session "$session_id" --reason "$reason"
exit 0
