#!/usr/bin/env bash
# FileChanged hook: one of the station's own config files changed on disk (an
# external editor counts), so the checker's pointer/drift nags must re-evaluate
# instead of trusting the fingerprint they took against the OLD config.
#
# There is nothing to say to the model here — FileChanged cannot inject context — so
# this hook prints nothing at all. The re-armed gate IS the mechanism: the next
# session start re-runs the checks and speaks if the new config broke something.
#
# THE MATCHER IS BASENAME-LEVEL, so any project's `config.json` fires this. The engine
# filters on the FULL path (inside <data_dir> or nothing happens) — see cmd_file_changed.
input=$(cat)
# Suppressed inside delegate-spawned workers — task tracking is the hub's job.
[ -n "$TASK_STATION_SUPPRESS" ] && exit 0
[ -n "$CLAUDE_PLUGIN_ROOT" ] || exit 0
# shellcheck source=/dev/null
. "$(dirname "${BASH_SOURCE[0]:-$0}")/_ts_lib.sh" 2>/dev/null || true
if ! command -v ts_run >/dev/null 2>&1; then      # no lib → old behaviour verbatim
  ts_run() { shift; "$@" >/dev/null 2>&1 || true; }
  ts_capture() { shift; "$@" 2>/dev/null || true; }
fi
# Parse stdin with python3 (hard requirement) instead of jq.
session_id=$(printf '%s' "$input" | python3 "$CLAUDE_PLUGIN_ROOT/lib/hookjson.py" session_id unknown)
changed=$(printf '%s' "$input" | python3 "$CLAUDE_PLUGIN_ROOT/lib/hookjson.py" file_path)
[ -n "$changed" ] || exit 0                       # nothing named → nothing to do
change_type=$(printf '%s' "$input" | python3 "$CLAUDE_PLUGIN_ROOT/lib/hookjson.py" change_type modified)
ts_run file-changed python3 "$CLAUDE_PLUGIN_ROOT/lib/task-station.py" file-changed \
  --session "$session_id" --file "$changed" --change "$change_type"
exit 0
