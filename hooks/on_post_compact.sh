#!/usr/bin/env bash
# PostCompact hook (opt-in auto-checkpoint): durably stash the harness's compaction
# summary into the attached task's history — zero model tokens, retrievable via
# `/todo <n> history`. Best-effort; never disrupts compaction. Gated on
# `config --auto-checkpoint-get` (off by default → this does nothing).
input=$(cat)
# Suppressed inside delegate-spawned workers — task tracking is the hub's job.
[ -n "$TASK_STATION_SUPPRESS" ] && exit 0
[ -n "$CLAUDE_PLUGIN_ROOT" ] || exit 0
# Shared helper (see hooks/_ts_lib.sh): still non-fatal, but a non-zero exit is now
# recorded to <data_dir>/logs/hook-health.log. The stubs keep this hook working
# verbatim if the lib is ever missing.
# shellcheck source=/dev/null
. "$(dirname "${BASH_SOURCE[0]:-$0}")/_ts_lib.sh" 2>/dev/null || true
if ! command -v ts_run >/dev/null 2>&1; then      # no lib → old behaviour verbatim
  ts_run() { shift; "$@" >/dev/null 2>&1 || true; }
  ts_capture() { shift; "$@" 2>/dev/null || true; }
fi
# Only act when auto-checkpoint is opted in — otherwise exactly today's behaviour.
if [ "$(ts_capture auto-checkpoint-get python3 "$CLAUDE_PLUGIN_ROOT/lib/task-station.py" config --auto-checkpoint-get)" != "on" ]; then
  exit 0
fi
# Parse stdin with python3 (hard requirement) instead of jq; hookjson.py mirrors
# `jq -r '.path // default'` and is a silent no-op on malformed input.
session_id=$(printf '%s' "$input" | python3 "$CLAUDE_PLUGIN_ROOT/lib/hookjson.py" session_id unknown)
trigger=$(printf '%s' "$input" | python3 "$CLAUDE_PLUGIN_ROOT/lib/hookjson.py" trigger unknown)
# Pipe the compaction summary on stdin (read in Python) so a large summary never
# hits argv limits. Still silent to the user — ts_run inherits the pipe's stdin and
# records a failure of the FINAL stage rather than swallowing it.
printf '%s' "$input" | python3 "$CLAUDE_PLUGIN_ROOT/lib/hookjson.py" compact_summary 2>/dev/null \
  | ts_run post-compact python3 "$CLAUDE_PLUGIN_ROOT/lib/task-station.py" hook post-compact \
      --session "$session_id" --trigger "$trigger"
exit 0
