#!/usr/bin/env bash
# Open a NEW terminal window running the given command, IN THE TERMINAL THE CALLER
# IS ACTUALLY SITTING IN, and bring that terminal to the front. Used by
# `/todo <n> -s` to jump straight into a task's working session in a fresh window —
# the current window (where you typed /todo) is left untouched, so we never close
# the session out from under the caller.
#
# Usage: open-session-window.sh "<command to run in the new window>"
#        open-session-window.sh --host        # just print the resolved host
#
# The command is typically the task's resume one-liner, e.g.
#   white 2>/dev/null; cd /Users/me && claude --resume <session-id>
#
# WHICH TERMINAL. Resolved by `core/termhost.py` — one table, ordered: an explicit
# $TASK_STATION_TERMINAL, then $LC_TERMINAL, then $TERM_PROGRAM, then each
# terminal's own marker ($KITTY_WINDOW_ID, $WEZTERM_PANE, …), then the PROCESS
# ANCESTRY, and only then nothing. The detection does NOT live in this file: it
# used to, close-session-window.sh had its own copy of it, and two copies of one
# rule is how they drift.
#
# AN UNRECOGNISED HOST OPENS NOTHING. This script exits non-zero and says which
# terminal it could not drive; the caller (task-station.py) prints the command for
# the user to run by hand. Opening a window in a DIFFERENT terminal is strictly
# worse: a window you cannot see reports success, which is exactly the failure this
# was rewritten for (2026-08-26 — a session in iTerm ran `tell application
# "Terminal"`, a stray window opened, and a human had to go and close it).
set -u

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
resolve() { PYTHONPATH="$here" python3 -m core.termhost "$@" 2>/dev/null; }

if [ "${1:-}" = "--host" ]; then
  resolve || { echo "termhost: could not resolve the host terminal" >&2; exit 2; }
  exit 0
fi

cmd="${1:-}"
if [ -z "$cmd" ]; then
  echo "open-session-window: no command given" >&2
  exit 2
fi

eval "$(resolve --shell)"
host="${TS_TERM_ID:-unknown}"
host_name="${TS_TERM_NAME:-unknown}"
host_how="${TS_TERM_HOW:-unresolved}"

# SAY WHICH ONE IT CHOSE, AND WHY. On stderr so it never contaminates a caller
# parsing stdout, and always — a correct choice costs one line, a wrong one is
# otherwise invisible until somebody finds the window.
say() { echo "open-session-window: $*" >&2; }

case "$host" in
  iterm2)
    say "opening a new window in $host_name ($host_how)"
    # A fresh window from the default profile, command run in its session. Passed
    # as an `on run argv` argument rather than interpolated into the script body,
    # so embedded quotes/spaces/`;`/`&&` in the resume one-liner need no escaping.
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
    ;;
  apple_terminal)
    say "opening a new window in $host_name ($host_how)"
    # `do script` with no `in <tab>` target opens a FRESH window and runs the
    # command there. Same `on run argv` argument-passing rationale as above.
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
    ;;
  wezterm|ghostty|kitty|alacritty)
    # These ship their own CLI; the argv comes from termhost's ARGV_SPAWN table so
    # this file holds no second copy of it.
    say "opening a new window in $host_name ($host_how)"
    argv_json="$(PYTHONPATH="$here" python3 -c '
import json, sys
sys.path.insert(0, sys.argv[1])
from core import termhost
print(json.dumps(termhost.spawn_plan(sys.argv[2])["argv"] or []))
' "$here" "$cmd" 2>/dev/null)"
    if [ -z "$argv_json" ] || [ "$argv_json" = "[]" ]; then
      say "no spawn command for $host_name — run it yourself:"
      say "  $cmd"
      exit 3
    fi
    PYTHONPATH="$here" python3 -c '
import json, os, sys
argv = json.loads(sys.argv[1])
os.execvp(argv[0], argv)
' "$argv_json"
    ;;
  *)
    # UNKNOWN, OR A TERMINAL WITH NO SPAWN WE KNOW. Refuse, loudly, with the
    # command. Never Terminal.app-by-default: see the header.
    say "cannot open a window in $host_name ($host_how)."
    say "Opening one in a DIFFERENT terminal would be worse — a window you cannot"
    say "see reports success. Run this yourself:"
    say "  $cmd"
    exit 3
    ;;
esac
