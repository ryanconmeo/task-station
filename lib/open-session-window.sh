#!/usr/bin/env bash
# Open a NEW terminal window running the given command, IN THE TERMINAL THE CALLER
# IS ACTUALLY SITTING IN, and bring that terminal to the front. Used by
# `/todo <n> -s` and by `invoke`/the relay to jump straight into a task's working
# session in a fresh window — the current window (where you typed /todo) is left
# untouched, so we never close the session out from under the caller.
#
# Usage: open-session-window.sh "<command to run in the new window>"
#        open-session-window.sh --host              # just print the resolved host
#        open-session-window.sh --dry-run "<cmd>"   # write the launch script, open
#                                                   # nothing, and print two lines:
#                                                   #   1. the line the window would
#                                                   #      receive, verbatim
#                                                   #   2. the launch script's path
#        (--print-script and --emit-script are aliases for --dry-run)
#
# The command is typically the task's resume one-liner, e.g.
#   unset CLAUDE_...; cd /Users/me && claude --resume <session-id>
# — but from `invoke` it also carries the child's whole prompt, which is routinely
# several thousand characters. THAT LENGTH IS THE WHOLE POINT OF THIS FILE. See below.
#
# ---------------------------------------------------------------------------------
# WHY THE WINDOW IS HANDED A FILE AND NOT THE COMMAND ITSELF
#
# MEASURED 2026-08-27. An `invoke` whose command line was ~1045 characters was typed
# into a fresh iTerm2 window and cut off mid-word. A session was minted, the trail
# recorded "invoked", this script reported "opened a new window running it", and
# NOTHING RAN — the window sat at a prompt. Silent, and reported as success.
#
# The cut is NOT in osascript: a 20 000-character argv passed through the same
# `on run argv` path arrives at length 20 000 intact (re-measured 2026-08-27).
#
# The cut is the TTY LINE DISCIPLINE. `write text` (iTerm2) and `do script`
# (Terminal.app) TYPE the string into the session's terminal, exactly as if a human
# had. A shell sitting at its own line editor (zsh's ZLE, bash's readline) puts the
# tty in raw mode and reads any length. A shell that is NOT yet at its line editor —
# a window one millisecond old, still sourcing its rc files — leaves the tty in
# CANONICAL mode, where the kernel caps one line at 1024 bytes and DISCARDS the rest
# with no error to anybody. Reproduced exactly: a 1189-character line typed while the
# session was busy arrived as its first 1024 bytes and nothing else. 1024 BYTES, so a
# command carrying em dashes or any other multi-byte text is cut at fewer than 1024
# CHARACTERS — which is why the original measurement read as "~930 chars in".
#
# So the command is written to a private, self-deleting launch script and the window
# is handed ONE SHORT LINE — `source /path/to/that/script` — whose length does not
# depend on the command's. A fixed ~70-byte line can never reach the 1024-byte cap.
#
# DO NOT "SIMPLIFY" THIS BACK TO `write text theCmd` / `do script theCmd`. It will
# look correct, it will pass every test that checks what was SENT, and it will start
# silently truncating again the first time a prompt gets long and a window is slow to
# start. The defect was never visible on the sending side; that is what made it cost
# a whole invoke that reported success.
# ---------------------------------------------------------------------------------
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
#
# EVERY FAILURE IS LOUD, and that now covers the launch script too: if it cannot be
# written, nothing is opened, the exit is non-zero, and the message names the host
# and the reason. A half-open window — one that exists but was never given its
# command — is the same class of lie as a window in the wrong app.
set -u

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
resolve() { PYTHONPATH="$here" python3 -m core.termhost "$@" 2>/dev/null; }

dry=0
case "${1:-}" in
  --host)
    resolve || { echo "termhost: could not resolve the host terminal" >&2; exit 2; }
    exit 0
    ;;
  --dry-run|--print-script|--emit-script)
    dry=1
    shift
    ;;
esac

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

# CAN THIS HOST BE DRIVEN AT ALL — asked BEFORE anything is written to disk, so a
# refusal leaves nothing behind. Unchanged in substance from PR 20: refuse, name the
# terminal, hand back the command, never substitute a different app.
case "$host" in
  iterm2|apple_terminal|wezterm|ghostty|kitty|alacritty) ;;
  *)
    say "cannot open a window in $host_name ($host_how)."
    say "Opening one in a DIFFERENT terminal would be worse — a window you cannot"
    say "see reports success. Run this yourself:"
    say "  $cmd"
    exit 3
    ;;
esac

# ------------------------------------------------------------- the hand-over file
#
# WHERE. Task Station's own data dir (`core.paths.data_dir()/launch`, mode 0700) when
# it can be resolved, else $TMPDIR — which on macOS is already a per-user directory.
# NEVER a predictable /tmp name: the file carries a session id and, from `invoke`,
# the child's whole prompt.
#
# HOW LONG. The script deletes ITSELF as its first statement. Unlinking a file the
# shell has already opened is safe on POSIX — the inode survives until the fd closes
# — so the payload still runs, and nothing is left on disk even if the window is
# closed mid-command or the machine loses power a second later.
launch_dir() {
  PYTHONPATH="$here" python3 - <<'PY' 2>/dev/null
import os
from core import paths
d = os.path.join(paths.data_dir(), "launch")
os.makedirs(d, exist_ok=True)
os.chmod(d, 0o700)
print(d)
PY
}

write_launch_script() {
  # Prints the path on stdout; returns non-zero and prints nothing on failure.
  local dir f
  umask 077
  dir="$(launch_dir)"
  if [ -z "$dir" ] || [ ! -d "$dir" ]; then
    dir="${TMPDIR:-/tmp}"
  fi
  # The X's must be TRAILING: BSD `mktemp` leaves X's followed by a suffix
  # ALONE, so a `…-XXXXXXXXXX.sh` template silently produces that exact literal
  # name — predictable, and the one thing this file must not be.
  f="$(mktemp "$dir/open-window-XXXXXXXXXX" 2>/dev/null)" || return 1
  chmod 0600 "$f" 2>/dev/null || { rm -f -- "$f"; return 1; }
  {
    printf '%s\n' '#!/usr/bin/env bash'
    printf '%s\n' '# task-station launch script — written by lib/open-session-window.sh,'
    printf '%s\n' '# sourced by the new window, and deleted by its own first line. It exists'
    printf '%s\n' '# because typing a long command into a new window truncates at 1024 bytes'
    printf '%s\n' '# with no error. See the header of open-session-window.sh.'
    printf '%s\n' "rm -f -- $(printf '%q' "$f")"
    printf '%s\n' "$cmd"
  } >"$f" || { rm -f -- "$f"; return 1; }
  printf '%s\n' "$f"
}

script_path="$(write_launch_script)" || {
  say "cannot open a window in $host_name ($host_how): could not write the launch"
  say "script. NOTHING was opened — a window without its command is worse than no"
  say "window. Run this yourself:"
  say "  $cmd"
  exit 4
}

# THE ONE LINE THE WINDOW RECEIVES. Fixed length, whatever `$cmd` is. `source` and not
# `bash <file>` on purpose: the command is typed-equivalent, so its `cd` must still
# leave the human in the task's directory once `claude` exits, exactly as before.
runner="source $(printf '%q' "$script_path")"

drive_failed() {
  rm -f -- "$script_path"
  say "cannot open a window in $host_name ($host_how): $1"
  say "NOTHING was opened. Run this yourself:"
  say "  $cmd"
  exit 5
}

if [ "$dry" = 1 ]; then
  say "would open a new window in $host_name ($host_how)"
  printf '%s\n' "$runner"
  printf '%s\n' "$script_path"
  exit 0
fi

case "$host" in
  iterm2)
    say "opening a new window in $host_name ($host_how)"
    # A fresh window from the default profile; the runner line is written into its
    # session. Passed as an `on run argv` argument rather than interpolated into the
    # script body, so quotes and spaces in the path need no escaping.
    osascript - "$runner" <<'APPLESCRIPT' >/dev/null || drive_failed "the AppleScript failed."
on run argv
  set theLine to item 1 of argv
  tell application "iTerm"
    create window with default profile
    tell current session of current window
      write text theLine
    end tell
    activate
  end tell
  return "opened"
end run
APPLESCRIPT
    ;;
  apple_terminal)
    say "opening a new window in $host_name ($host_how)"
    # `do script` with no `in <tab>` target opens a FRESH window and runs the line
    # there. Same `on run argv` argument-passing rationale as above — and the same
    # 1024-byte tty cap, which is why it too gets the runner line and not the command.
    osascript - "$runner" <<'APPLESCRIPT' >/dev/null || drive_failed "the AppleScript failed."
on run argv
  set theLine to item 1 of argv
  tell application "Terminal"
    do script theLine
    activate
  end tell
  return "opened"
end run
APPLESCRIPT
    ;;
  wezterm|ghostty|kitty|alacritty)
    # These ship their own CLI; the argv comes from termhost's ARGV_SPAWN table so
    # this file holds no second copy of it. An argv is not typed and so is not
    # subject to the tty cap, but it gets the runner line too: one payload path for
    # every host is one thing to keep correct, and `$cmd` is not re-quoted anywhere.
    say "opening a new window in $host_name ($host_how)"
    argv_json="$(PYTHONPATH="$here" python3 -c '
import json, sys
sys.path.insert(0, sys.argv[1])
from core import termhost
print(json.dumps(termhost.spawn_plan(sys.argv[2])["argv"] or []))
' "$here" "$runner" 2>/dev/null)"
    if [ -z "$argv_json" ] || [ "$argv_json" = "[]" ]; then
      rm -f -- "$script_path"
      say "no spawn command for $host_name — run it yourself:"
      say "  $cmd"
      exit 3
    fi
    PYTHONPATH="$here" python3 -c '
import json, os, sys
argv = json.loads(sys.argv[1])
os.execvp(argv[0], argv)
' "$argv_json" || drive_failed "the terminal's own CLI could not be run."
    ;;
esac
