# paths.py
"""Resolve the stable, version-independent home for Task Station's mutable state.

Must NOT live inside the plugin dir: a plugin installs to a versioned cache that
is replaced on every `/plugin update`, which would destroy task history. Anchored
to CLAUDE_CONFIG_DIR (Claude Code's own relocation primitive) so it tracks a moved
~/.claude, with an explicit override and an XDG courtesy fallback.
"""
import os


def data_dir():
    override = os.environ.get("CLAUDE_TODO_HOME")
    if override:
        return os.path.expanduser(override)
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    if cfg:
        return os.path.join(os.path.expanduser(cfg), "todo-data")
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return os.path.join(os.path.expanduser(xdg), "task-station")
    return os.path.expanduser("~/.claude/todo-data")
