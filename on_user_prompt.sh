#!/usr/bin/env bash
# UserPromptSubmit hook: keep the attached task's activity fresh (and reopen it
# if it was closed), or — if this session has no task yet — inject guidance that
# tells Claude how to attach/create one. stdout is injected as model context.
#
# Before any of that, if this prompt INVOKES A SKILL with a known category
# (e.g. /volt:review-pr-auto → orange), tint the terminal RIGHT NOW —
# synchronously, before Claude responds — so the colour applies immediately
# instead of waiting for Claude to read the guidance and run the alias itself.
input=$(cat)
# Suppressed inside delegate-spawned workers — task tracking + tinting is the hub's job.
[ -n "$CLAUDE_TODO_SUPPRESS" ] && exit 0
session_id=$(echo "$input" | jq -r '.session_id // "unknown"')
prompt=$(echo "$input" | jq -r '.prompt // ""')

color=$(TODO_PROMPT="$prompt" python3 "$HOME/.claude/todo/todo.py" prompt-color)
[ -n "$color" ] && zsh -ic "$color" >/dev/null 2>&1

TODO_PROMPT="$prompt" python3 "$HOME/.claude/todo/todo.py" prompt-context --session "$session_id"
