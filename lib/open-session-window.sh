#!/usr/bin/env bash
# Open a NEW terminal window running the given command, and bring that terminal
# app to the front. Used by `/todo <n> -s` to jump straight into a task's
# working session in a fresh window — the current window (where you typed
# /todo) is left untouched, so we never close the session out from under the
# caller.
#
# Usage: open-session-window.sh "<command to run in the new window>"
#
# The command is typically the task's resume one-liner, e.g.
#   white 2>/dev/null; cd /Users/me && claude --resume <session-id>
#
# Terminal app: we open the window in whichever app the caller is running in —
# iTerm2 when launched from iTerm2 (detected via $LC_TERMINAL / $TERM_PROGRAM),
# otherwise Terminal.app. Best-effort and macOS-only: the caller (todo.py)
# treats a non-zero exit as "couldn't open a window" and falls back to printing
# the command for the user to run by hand.
set -u
[[ "$OSTYPE" == darwin* ]] || exit 0

cmd="${1:-}"
if [ -z "$cmd" ]; then
  echo "open-session-window: no command given" >&2
  exit 2
fi

# Detect the launching terminal. iTerm2 sets LC_TERMINAL=iTerm2 and
# TERM_PROGRAM=iTerm.app; Terminal.app sets TERM_PROGRAM=Apple_Terminal. These
# are inherited by the claude process and on down to here, so they identify the
# app the user is actually sitting in. Anything we don't recognise as iTerm2
# falls back to Terminal.app.
if [ "${LC_TERMINAL:-}" = "iTerm2" ] || [ "${TERM_PROGRAM:-}" = "iTerm.app" ]; then
  # iTerm2: make a fresh window from the default profile and run the command in
  # its session. The command is passed as an `on run argv` argument rather than
  # interpolated into the script body, so embedded quotes/spaces/`;`/`&&` in the
  # resume one-liner need no escaping.
  osascript - "$cmd" <<'APPLESCRIPT'
on run argv
  set theCmd to item 1 of argv
  tell application "iTerm"
    create window with default profile
    tell current session of current window
      write text theCmd
    end tell
    activate
  end tell
  return "opened"
end run
APPLESCRIPT
else
  # Terminal.app: `do script` with no `in <tab>` target opens a FRESH window and
  # runs the command there. Same `on run argv` argument-passing rationale as
  # the iTerm2 branch above.
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
fi
