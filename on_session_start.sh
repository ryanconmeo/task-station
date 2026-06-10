#!/usr/bin/env bash
# SessionStart hook: surface open tasks (or the already-attached task) so Claude
# can recognise a resume. Emitted as SessionStart additionalContext JSON.
input=$(cat)
# Suppressed inside delegate-spawned workers — task tracking is the hub's job.
[ -n "$CLAUDE_TODO_SUPPRESS" ] && exit 0
session_id=$(echo "$input" | jq -r '.session_id // "unknown"')
source=$(echo "$input" | jq -r '.source // ""')

# Self-heal the deployed slash commands: ~/.claude/commands/{todo,done}.md are
# COPIES of commands/*.md here (the module is the source of truth). If they
# drift — e.g. the module was updated but never re-copied — sessions render
# stale instructions, so re-sync them on every session start.
for f in "$HOME/.claude/todo/commands/"*.md; do
  [ -e "$f" ] || continue
  dest="$HOME/.claude/commands/$(basename "$f")"
  if [ ! -e "$dest" ] || ! cmp -s "$f" "$dest"; then
    mkdir -p "$HOME/.claude/commands"
    cp "$f" "$dest"
  fi
done

ctx=$(python3 "$HOME/.claude/todo/todo.py" session-start --session "$session_id" --source "$source")
# Auto-label the window for an attached task (todo-<seq> · <title>) — the hub can't
# be programmatically renamed, but its title CAN be set via the SessionStart hook.
title=$(python3 "$HOME/.claude/todo/todo.py" session-title --session "$session_id")
if [ -n "$ctx" ] || [ -n "$title" ]; then
  jq -n --arg c "$ctx" --arg t "$title" \
    '{hookSpecificOutput: ({hookEventName: "SessionStart"}
        + (if $c != "" then {additionalContext: $c} else {} end)
        + (if $t != "" then {sessionTitle: $t} else {} end))}'
fi
exit 0
