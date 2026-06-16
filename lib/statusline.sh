#!/usr/bin/env bash
# Standalone todo status line: reads the Claude Code session JSON on stdin and
# prints the current task segment. Self-contained — point settings.json
# statusLine.command at this (via the stable ~/.claude/todo-engine path).
input=$(cat)
sid=$(printf '%s' "$input" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("session_id",""))' 2>/dev/null)
[ -n "$sid" ] || exit 0
exec python3 "$(dirname "$0")/todo.py" whoami --session "$sid" --statusline --width "${CLAUDE_TODO_STATUSLINE_WIDTH:-0}"
