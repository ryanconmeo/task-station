#!/usr/bin/env bash
# Close the Terminal.app window that hosts THIS session, matched by tty.
#
# Why the ancestor walk: Claude Code runs commands without a controlling tty
# ($$ -> /dev/??), and the session may be wrapped by `script` (logging) on its
# own pty. The real Terminal window's tty is the OUTERMOST real tty in the
# process ancestry (login/-zsh/script), not Claude's pty. We walk up the parent
# chain and keep the last /dev/ttys* we see.
#
# Usage: close-session-window.sh [--dry-run]
set -u

dry=0
[ "${1:-}" = "--dry-run" ] && dry=1

p=$$
win_tty=""
for _ in $(seq 1 20); do
  t=$(ps -o tty= -p "$p" 2>/dev/null | tr -d ' ')
  case "$t" in ttys*) win_tty="$t" ;; esac
  pp=$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ')
  { [ -z "$pp" ] || [ "$pp" = 0 ] || [ "$pp" = 1 ]; } && break
  p="$pp"
done

if [ -z "$win_tty" ]; then
  echo "close-session-window: could not determine window tty" >&2
  exit 1
fi

if [ "$dry" = 1 ]; then
  echo "would close Terminal window with tty /dev/$win_tty"
  osascript -e 'tell application "Terminal" to get tty of tabs of windows' 2>/dev/null \
    | tr ',' '\n' | grep -q "/dev/$win_tty" \
    && echo "match: a Terminal tab reports /dev/$win_tty" \
    || echo "no match among open Terminal tabs"
  exit 0
fi

osascript \
  -e 'tell application "Terminal"' \
  -e 'repeat with w in windows' \
  -e 'repeat with t in tabs of w' \
  -e "if tty of t is \"/dev/$win_tty\" then" \
  -e 'close w saving no' \
  -e 'return' \
  -e 'end if' \
  -e 'end repeat' \
  -e 'end repeat' \
  -e 'end tell'
