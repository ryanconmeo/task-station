"""Detect the host terminal for tint + window control. Pure stdlib."""
import os
import shutil

def width():
    """Terminal columns for width-aware rendering. shutil.get_terminal_size()
    already honors $COLUMNS, falling back to 80 when there's no tty; we clamp to a
    minimum of 60 so the config board's columns never collapse. Pure stdlib."""
    return max(60, shutil.get_terminal_size((80, 24)).columns)

def detect():
    """Classify the host terminal for tint escapes. Returns one of:

      "iterm"    iTerm2 — standard OSC + its proprietary OSC-1337 SetColors (bold;
                 it also tints the tab). LC_TERMINAL survives tmux/ssh.
      "terminal" Apple Terminal.app — standard OSC only (no SetColors).
      "osc"      any other xterm-compatible terminal that honours OSC 11 background
                 (WezTerm, VS Code, Ghostty, Windows Terminal, kitty, Alacritty, and
                 the xterm-256color fallback). Standard OSC, no bold extra.
      "none"     tinting suppressed — no positive signal, or forced off.

    Detection is env-based (no tty round-trip). $TASK_STATION_TERM overrides
    everything; then iTerm/Apple markers; then each other terminal's own env
    marker ($TERM is usually a generic "xterm-256color" there so we can't lean on
    it alone); finally a plain xterm* / *-256color $TERM as the OSC fallback. An
    environment with no positive signal stays "none" so we never spray OSC at a
    terminal (or dumb pipe) that would print it as garbage."""
    override = os.environ.get("TASK_STATION_TERM")
    if override:
        return override if override in ("iterm", "terminal", "osc", "none") else "none"
    if os.environ.get("LC_TERMINAL") == "iTerm2" or os.environ.get("TERM_PROGRAM") == "iTerm.app":
        return "iterm"
    if os.environ.get("TERM_PROGRAM") == "Apple_Terminal":
        return "terminal"
    # Other OSC-11-capable terminals, each by its own marker env var.
    if os.environ.get("TERM_PROGRAM") in ("WezTerm", "vscode", "ghostty"):
        return "osc"                                    # WezTerm / VS Code / Ghostty
    if os.environ.get("WT_SESSION"):
        return "osc"                                    # Windows Terminal
    if os.environ.get("KITTY_WINDOW_ID"):
        return "osc"                                    # kitty
    if os.environ.get("ALACRITTY_WINDOW_ID") or os.environ.get("ALACRITTY_SOCKET"):
        return "osc"                                    # Alacritty
    term = os.environ.get("TERM", "")
    if term.startswith("xterm") or term.endswith("256color"):
        return "osc"                                    # generic xterm-compatible fallback
    return "none"

def tmux_wrap(seq):
    """Wrap a terminal escape in tmux's DCS passthrough when running inside tmux,
    else return it unchanged. Under tmux an OSC escape written to the pane's pty is
    swallowed unless wrapped as `ESC P tmux ; <body, each ESC doubled> ESC \\`
    (requires `tmux set -g allow-passthrough on`). Gated on $TMUX; a no-op (returns
    `seq` verbatim, incl. "") otherwise. Mirrors set-term-profile.sh's wrapping."""
    if not seq or not os.environ.get("TMUX"):
        return seq
    return "\033Ptmux;" + seq.replace("\033", "\033\033") + "\033\\"
