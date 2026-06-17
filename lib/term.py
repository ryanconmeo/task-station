"""Detect the host terminal for tint + window control. Pure stdlib."""
import os

def detect():
    override = os.environ.get("TASK_STATION_TERM")
    if override:
        return override if override in ("iterm", "terminal", "none") else "none"
    if os.environ.get("LC_TERMINAL") == "iTerm2" or os.environ.get("TERM_PROGRAM") == "iTerm.app":
        return "iterm"
    if os.environ.get("TERM_PROGRAM") == "Apple_Terminal":
        return "terminal"
    return "none"
