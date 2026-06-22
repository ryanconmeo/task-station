"""Detect the host terminal for tint + window control. Pure stdlib."""
import os
import shutil

def width():
    """Terminal columns for width-aware rendering. shutil.get_terminal_size()
    already honors $COLUMNS, falling back to 80 when there's no tty; we clamp to a
    minimum of 60 so the config board's columns never collapse. Pure stdlib."""
    return max(60, shutil.get_terminal_size((80, 24)).columns)

def detect():
    override = os.environ.get("TASK_STATION_TERM")
    if override:
        return override if override in ("iterm", "terminal", "none") else "none"
    if os.environ.get("LC_TERMINAL") == "iTerm2" or os.environ.get("TERM_PROGRAM") == "iTerm.app":
        return "iterm"
    if os.environ.get("TERM_PROGRAM") == "Apple_Terminal":
        return "terminal"
    return "none"
