#!/usr/bin/env bash
[[ "$OSTYPE" == darwin* ]] || exit 0
# Close the terminal window (Terminal.app OR iTerm2) that hosts THIS session,
# matched by tty. The host app is detected the same way open-session-window.sh
# detects it (LC_TERMINAL / TERM_PROGRAM), so /done closes iTerm2 windows too.
#
# Why the ancestor walk: Claude Code runs commands without a controlling tty
# ($$ -> /dev/??), and the session may be wrapped by `script` (logging) on its
# own pty. The real Terminal window's tty is the OUTERMOST real tty in the
# process ancestry (login/-zsh/script), not Claude's pty. We walk up the parent
# chain and keep the last /dev/ttys* we see.
#
# Why --detach: /done runs this from a throwaway, non-interactive slash-command
# shell. A plain `... &` background job lives in that shell's process group, so
# when the command shell is torn down right after it returns, the still-sleeping
# job is killed before it can close anything (symptom: nothing happens, no
# dialog). --detach resolves the tty *synchronously* (while we still have it),
# then re-execs this script under a fresh session (os.setsid, double-fork) that
# is reparented to launchd and survives the teardown. The detached child is
# handed the tty via --tty, so it needs no controlling terminal of its own.
#
# NOTE: For the window to actually close WITHOUT a confirmation dialog, set the
# terminal's close prompt to "Never" — Terminal.app: Settings -> Profiles ->
# <profile> -> Shell -> "Prompt before closing"; iTerm2: Settings -> Profiles ->
# <profile> -> Session -> "Prompt before closing". Otherwise `close` pops a
# "terminate running processes?" dialog and the window stays open.
#
# Usage:
#   close-session-window.sh                  close this window now (foreground)
#   close-session-window.sh --after N        wait N seconds, then close
#   close-session-window.sh --detach [--after N]
#                                            resolve tty now, close from a
#                                            detached process that survives the
#                                            caller exiting (use this from /done)
#   close-session-window.sh --tty ttysNNN    close a specific tty (skip detection)
#   close-session-window.sh --dry-run        report the tty + match, close nothing
set -u

dry=0
detach=0
after=0
win_tty=""

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) dry=1 ;;
    --detach)  detach=1 ;;
    --after)   after="${2:-0}"; shift ;;
    --tty)     win_tty="${2:-}"; win_tty="${win_tty#/dev/}"; shift ;;
    *) echo "close-session-window: unknown arg '$1'" >&2; exit 2 ;;
  esac
  shift
done

# Resolve the window tty by walking the process ancestry, unless one was passed
# in explicitly (the detached child gets it via --tty so it needs no tty itself).
if [ -z "$win_tty" ]; then
  p=$$
  for _ in $(seq 1 20); do
    t=$(ps -o tty= -p "$p" 2>/dev/null | tr -d ' ')
    case "$t" in ttys*) win_tty="$t" ;; esac
    pp=$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ')
    { [ -z "$pp" ] || [ "$pp" = 0 ] || [ "$pp" = 1 ]; } && break
    p="$pp"
  done
fi

if [ -z "$win_tty" ]; then
  echo "close-session-window: could not determine window tty" >&2
  exit 1
fi

# Detect the host terminal app the same way open-session-window.sh does. iTerm2
# sets LC_TERMINAL=iTerm2 and TERM_PROGRAM=iTerm.app; both are inherited down to
# here AND across the detached --tty re-exec (the child inherits this env), so
# the close targets the right app whether run directly or detached. Anything we
# don't recognise as iTerm2 falls back to Terminal.app.
is_iterm=0
if [ "${LC_TERMINAL:-}" = "iTerm2" ] || [ "${TERM_PROGRAM:-}" = "iTerm.app" ]; then
  is_iterm=1
fi

if [ "$dry" = 1 ]; then
  if [ "$is_iterm" = 1 ]; then
    echo "would close iTerm window with tty /dev/$win_tty"
    osascript -e 'tell application "iTerm" to get tty of sessions of tabs of windows' 2>/dev/null \
      | tr ',' '\n' | grep -q "/dev/$win_tty" \
      && echo "match: an iTerm session reports /dev/$win_tty" \
      || echo "no match among open iTerm sessions"
  else
    echo "would close Terminal window with tty /dev/$win_tty"
    osascript -e 'tell application "Terminal" to get tty of tabs of windows' 2>/dev/null \
      | tr ',' '\n' | grep -q "/dev/$win_tty" \
      && echo "match: a Terminal tab reports /dev/$win_tty" \
      || echo "no match among open Terminal tabs"
  fi
  exit 0
fi

# --detach: hand off to a process in its own session so it outlives this shell.
# We've already resolved win_tty above, so the child closes by --tty and never
# needs a controlling terminal of its own. Double-fork + setsid orphans it onto
# launchd; it sleeps `after` seconds, then re-execs this script to do the close.
if [ "$detach" = 1 ]; then
  self="$0"
  case "$self" in /*) : ;; *) self="$PWD/$self" ;; esac
  python3 - "$self" "$win_tty" "$after" >/dev/null 2>&1 <<'PY' &
import os, sys, time, subprocess
self, tty, after = sys.argv[1], sys.argv[2], float(sys.argv[3])
if os.fork() > 0: os._exit(0)   # parent of session leader exits
os.setsid()                      # new session, no controlling tty
if os.fork() > 0: os._exit(0)   # ensure we can't reacquire a tty
time.sleep(after)
subprocess.run(["bash", self, "--tty", tty])
PY
  exit 0
fi

# Optional in-process delay (used when not detaching).
if [ "$after" != 0 ]; then
  sleep "$after"
fi

if [ "$is_iterm" = 1 ]; then
  # iTerm2: a window owns tabs, each tab owns sessions, and the tty lives on the
  # session. Walk all three and close the window whose session has our tty.
  osascript \
    -e 'tell application "iTerm"' \
    -e 'repeat with w in windows' \
    -e 'repeat with t in tabs of w' \
    -e 'repeat with s in sessions of t' \
    -e "if tty of s is \"/dev/$win_tty\" then" \
    -e 'close w' \
    -e 'return "closed"' \
    -e 'end if' \
    -e 'end repeat' \
    -e 'end repeat' \
    -e 'end repeat' \
    -e 'return "no-match"' \
    -e 'end tell'
else
  osascript \
    -e 'tell application "Terminal"' \
    -e 'repeat with w in windows' \
    -e 'repeat with t in tabs of w' \
    -e "if tty of t is \"/dev/$win_tty\" then" \
    -e 'close w saving no' \
    -e 'return "closed"' \
    -e 'end if' \
    -e 'end repeat' \
    -e 'end repeat' \
    -e 'return "no-match"' \
    -e 'end tell'
fi
