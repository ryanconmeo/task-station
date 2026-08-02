#!/usr/bin/env bash
# Resolve the tty of the session Claude runs in (the ORIGINATING window),
# focus-independently. `source` it to get the origin_tty() function; run it to
# print the tty (empty + exit 1 if undeterminable). See task-station task 119.
_oitty_is_iterm() { [ "${LC_TERMINAL:-}" = "iTerm2" ] || [ "${TERM_PROGRAM:-}" = "iTerm.app" ]; }
origin_tty() {
  # 1. $CLAUDE_TTY — exported by the user's shell rc; focus-proof, terminal-agnostic.
  if [ -n "${CLAUDE_TTY:-}" ] && [ -e "${CLAUDE_TTY}" ]; then printf '%s' "$CLAUDE_TTY"; return 0; fi
  # 2. iTerm2: map the UUID in $TERM_SESSION_ID to its session's tty (focus-proof).
  if _oitty_is_iterm && [ -n "${TERM_SESSION_ID:-}" ]; then
    local uuid="${TERM_SESSION_ID#*:}" dev
    dev=$(osascript -e "tell application \"iTerm2\"
      repeat with w in windows
        repeat with t in tabs of w
          repeat with s in sessions of t
            if (id of s) is \"${uuid}\" then return (tty of s)
          end repeat
        end repeat
      end repeat
    end tell" 2>/dev/null)
    [ -n "$dev" ] && { printf '%s' "$dev"; return 0; }
  fi
  return 1
}
if [ "${BASH_SOURCE[0]:-$0}" = "$0" ]; then origin_tty; fi
