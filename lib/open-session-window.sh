#!/usr/bin/env bash
[[ "$OSTYPE" == darwin* ]] || exit 0
# Open a NEW Terminal.app window running the given command, and bring Terminal
# to the front. Used by `/todo <n> -s` to jump straight into a task's working
# session in a fresh window — the current window (where you typed /todo) is left
# untouched, so we never close the session out from under the caller.
#
# Usage: open-session-window.sh "<command to run in the new window>"
#
# The command is typically the task's resume one-liner, e.g.
#   white 2>/dev/null; cd /Users/me && claude --resume <session-id>
#
# Best-effort and macOS/Terminal.app-only: the caller (todo.py) treats a
# non-zero exit as "couldn't open a window" and falls back to printing the
# command for the user to run by hand.
set -u

cmd="${1:-}"
if [ -z "$cmd" ]; then
  echo "open-session-window: no command given" >&2
  exit 2
fi

# `do script` with no `in <tab>` target opens a FRESH window and runs the
# command there. We hand the command to AppleScript as an `on run argv`
# argument rather than interpolating it into the script body, so embedded
# quotes/spaces/`;`/`&&` in the resume one-liner need no escaping.
osascript - "$cmd" <<'APPLESCRIPT'
on run argv
  set theCmd to item 1 of argv
  tell application "Terminal"
    do script theCmd
    activate
  end tell
  return "opened"
end run
APPLESCRIPT
