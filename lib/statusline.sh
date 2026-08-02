#!/usr/bin/env bash
# Standalone task-station status line: reads the Claude Code session JSON on stdin and
# prints the current task segment. Self-contained — point settings.json
# statusLine.command at this (via the stable ~/.claude/task-station-engine path).
input=$(cat)
sid=$(printf '%s' "$input" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("session_id",""))' 2>/dev/null)
[ -n "$sid" ] || exit 0
# Honor the composition contract's width hint (CLAUDE_STATUSLINE_WIDTH); fall back
# to the legacy TASK_STATION_STATUSLINE_WIDTH, then 0 (no limit). See docs/STATUSLINE.md.
width="${CLAUDE_STATUSLINE_WIDTH:-${TASK_STATION_STATUSLINE_WIDTH:-0}}"
exec python3 "$(dirname "$0")/task-station.py" whoami --session "$sid" --statusline --width "$width"
