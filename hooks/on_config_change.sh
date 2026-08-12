#!/usr/bin/env bash
# ConfigChange hook: a settings file just changed and has NOT taken effect yet —
# check the paths it declares, and say so when one of them no longer exists.
#
# WHY THIS ONE IS NOT MASKED. Every other task-station hook routes its work through
# ts_run, whose whole point is that the exit code cannot escape. Here the exit code IS
# the contract: 2 blocks the change. So the engine is called directly, its stderr is
# captured, and only the two sanctioned codes are honoured —
#
#   0  → nothing to say, or WARN mode (the engine wrote the hook-health record)
#   2  → BLOCK, enforce mode only (the record was written FIRST — a block surfaces no
#        transcript message, so that record is the user's only trace)
#   *  → our own breakage: recorded to hook-health, and we exit 0. A broken validator
#        must never be able to refuse a user's config save.
#
# Wired for user_settings / project_settings / local_settings only. policy_settings is
# deliberately NOT wired (it cannot be blocked), and `skills` is out of scope.
input=$(cat)
# Suppressed inside delegate-spawned workers — task tracking is the hub's job.
[ -n "$TASK_STATION_SUPPRESS" ] && exit 0
[ -n "$CLAUDE_PLUGIN_ROOT" ] || exit 0
# shellcheck source=/dev/null
. "$(dirname "${BASH_SOURCE[0]:-$0}")/_ts_lib.sh" 2>/dev/null || true
# Parse stdin with python3 (hard requirement) instead of jq.
session_id=$(printf '%s' "$input" | python3 "$CLAUDE_PLUGIN_ROOT/lib/hookjson.py" session_id unknown)
source=$(printf '%s' "$input" | python3 "$CLAUDE_PLUGIN_ROOT/lib/hookjson.py" config_source)
changed=$(printf '%s' "$input" | python3 "$CLAUDE_PLUGIN_ROOT/lib/hookjson.py" changed_file)
[ -n "$changed" ] || exit 0                       # nothing named → nothing to check

err=$(mktemp "${TMPDIR:-/tmp}/ts-hook.XXXXXX" 2>/dev/null) || err=""
rc=0
if [ -n "$err" ]; then
  python3 "$CLAUDE_PLUGIN_ROOT/lib/task-station.py" config-change \
    --session "$session_id" --source "$source" --file "$changed" 2>"$err" || rc=$?
else
  python3 "$CLAUDE_PLUGIN_ROOT/lib/task-station.py" config-change \
    --session "$session_id" --source "$source" --file "$changed" || rc=$?
fi

if [ "$rc" = 2 ]; then
  # Replay the reason on stderr. The transcript stays silent either way, but a user
  # watching the terminal gets to see which path we refused the save over.
  [ -n "$err" ] && cat "$err" >&2
  [ -n "$err" ] && rm -f "$err" 2>/dev/null
  exit 2
fi
if [ "$rc" != 0 ] && command -v ts_health_record >/dev/null 2>&1; then
  ts_health_record config-change-hook "$rc" "$err"    # our bug, recorded, never fatal
fi
[ -n "$err" ] && rm -f "$err" 2>/dev/null
exit 0
