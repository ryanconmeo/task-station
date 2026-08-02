#!/usr/bin/env bash
# Switch THIS terminal window/session to a named color profile.
# Usage: set-term-profile.sh "Green Sands"
#
# Why a script (not a shell function): the tint aliases in ~/.zshrc are sometimes
# run from inside Claude's own bash environment, where ~/.zshrc functions aren't
# present (only aliases survive). A script on disk is always callable — same
# rationale as claude-win-theme.sh.
#
# Works in both terminals, AND from a captured-pipe context (Claude's Bash tool
# for /todo & /done, the task-station hooks) where stdout is NOT the visible session:
#   - iTerm2       -> proprietary OSC 1337 "SetProfile" escape, written to the
#                     ORIGINATING session's tty. Wrapped for tmux.
#   - Terminal.app -> AppleScript targeting the ORIGINATING window's tab.
#
# CRITICAL — target the originating window, never "front"/"current":
# When run from a captured pipe (the common case: /todo, /done, the hooks)
# there's no controlling tty, so older versions asked the terminal for its
# "current"/"front" window. That follows keyboard FOCUS — so if you'd clicked
# into a different window, the recolor landed on the WRONG one. We instead
# resolve the tty of the session Claude actually runs in, focus-independently:
#   1. $CLAUDE_TTY — exported by ~/.zshrc at shell start, inherited by Claude
#      and every subshell. Focus-proof and works for both terminals (and is the
#      correct pty under tmux). Empty only for sessions started before it existed.
#   2. iTerm2 fallback — map the UUID in $TERM_SESSION_ID (also inherited) to the
#      matching session's tty via AppleScript. Resolves already-running sessions.
#   3. Front/current window — legacy best-effort, only when nothing else resolves.
# iTerm2 sets LC_TERMINAL=iTerm2 and propagates it through tmux/ssh, so we detect
# it even when TERM_PROGRAM has been rewritten to "tmux".
profile="$1"
[ -n "$profile" ] || { echo "usage: set-term-profile.sh <profile name>" >&2; exit 2; }

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/origin-tty.sh"

is_iterm() {
  [ "$LC_TERMINAL" = "iTerm2" ] || [ "$TERM_PROGRAM" = "iTerm.app" ]
}

set_iterm() {
  # OSC 1337 ; SetProfile=<name> BEL
  local seq="\033]1337;SetProfile=${profile}\007"
  if [ -n "$TMUX" ]; then
    # tmux passthrough: ESC P tmux ; <body, each ESC doubled> ESC \
    # (requires: tmux set -g allow-passthrough on)
    seq="\033Ptmux;\033${seq}\033\\"   # wrap; the body's leading ESC is doubled
  fi
  # Interactive shell: stdout IS this session's own tty — emit it directly. The
  # escape only recolors the window whose tty receives it.
  if [ -t 1 ]; then
    printf '%b' "$seq"
    return
  fi
  # Captured pipe (Claude's Bash tool for /todo, /done, the task-station hooks): stdout
  # does NOT point at the visible session, so resolve the originating session's
  # tty (focus-independent) and write the escape straight to it.
  local dev
  dev=$(origin_tty)
  if [ -n "$dev" ] && [ -w "$dev" ]; then
    printf '%b' "$seq" > "$dev"
    return
  fi
  # Last resort: the front session's tty (follows focus — may be wrong window).
  dev=$(osascript -e 'tell application "iTerm2" to tell current session of current window to get tty' 2>/dev/null)
  if [ -n "$dev" ] && [ -w "$dev" ]; then
    printf '%b' "$seq" > "$dev"
  else
    printf '%b' "$seq"   # inert in a pipe, but harmless
  fi
}

set_terminal() {
  # Prefer the originating window's tab, matched by tty — focus-independent.
  local dev
  dev=$(origin_tty)
  if [ -n "$dev" ]; then
    if osascript -e "tell application \"Terminal\"
      repeat with w in windows
        repeat with t in tabs of w
          if (tty of t) is \"${dev}\" then
            set current settings of t to settings set \"${profile}\"
            return
          end if
        end repeat
      end repeat
    end tell" >/dev/null 2>&1; then
      return
    fi
  fi
  # Fallback: front window (follows focus — may be wrong window) when the
  # originating tty can't be resolved.
  osascript -e "tell application \"Terminal\" to set current settings of front window to settings set \"${profile}\"" >/dev/null 2>&1
}

if is_iterm; then
  set_iterm
elif [ "$TERM_PROGRAM" = "Apple_Terminal" ]; then
  set_terminal
else
  # Unknown host: iTerm escape is inert in terminals that don't grok it.
  set_iterm
fi
