#!/usr/bin/env python3
# mcp_launcher.py
"""Stable, self-resolving launcher for the task-station Desktop bridge.

Claude Desktop is pointed at a COPY of this file living at a STABLE path under the
data dir (`<data_dir>/mcp-launcher.py`) — NOT at the volatile
`~/.claude/task-station-engine` symlink, whose target every CLI session re-points
to *that session's* plugin version (an old session can point it at a version with
no `mcp_server.py`, breaking Desktop).

At run time this launcher resolves the INSTALLED task-station plugin's
`mcp_server.py` itself and `os.execv`s it with the same interpreter, passing stdio
straight through — so it IS the MCP server, just version-resolved on every launch.
Robust across `/plugin update` and concurrent CLI sessions.

Resolution order:
  1. `<CLAUDE_CONFIG_DIR or ~/.claude>/plugins/installed_plugins.json` →
     plugins["task-station@ryanconmeo"][0]["installPath"] → <installPath>/lib/mcp_server.py
  2. Fallback: highest version under
     `<config>/plugins/cache/ryanconmeo/task-station/*/lib/mcp_server.py` that exists.

Stdlib only; runs on the system `python3` (3.9+).
"""
import glob
import json
import os
import sys

PLUGIN_KEY = "task-station@ryanconmeo"


def _config_dir():
    """Claude Code's config dir — the relocation primitive the rest of the plugin
    tracks too (CLAUDE_CONFIG_DIR, else ~/.claude)."""
    return os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude"))


def _from_installed_plugins(config_dir):
    """`<installPath>/lib/mcp_server.py` from installed_plugins.json, or None when
    the file/entry/installPath is missing, unparseable, or the target is absent."""
    manifest = os.path.join(config_dir, "plugins", "installed_plugins.json")
    try:
        with open(manifest) as f:
            data = json.load(f)
        entry = (data.get("plugins", {}).get(PLUGIN_KEY) or [None])[0] or {}
        install_path = entry.get("installPath")
        if not install_path:
            return None
        cand = os.path.join(os.path.expanduser(install_path), "lib", "mcp_server.py")
        return cand if os.path.exists(cand) else None
    except Exception:
        return None


def _version_key(path):
    """A sortable key from the `<version>` dir in
    `.../task-station/<version>/lib/mcp_server.py` — e.g. "1.6.0" → (1, 6, 0).
    Non-numeric chunks degrade to 0 so a malformed dir never crashes the max()."""
    version = os.path.basename(os.path.dirname(os.path.dirname(path)))
    parts = []
    for chunk in version.split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    return parts


def _from_cache(config_dir):
    """The highest cached version that actually has `lib/mcp_server.py`, or None."""
    pattern = os.path.join(config_dir, "plugins", "cache", "ryanconmeo",
                           "task-station", "*", "lib", "mcp_server.py")
    cands = [p for p in glob.glob(pattern) if os.path.exists(p)]
    if not cands:
        return None
    return max(cands, key=_version_key)


def resolve_server(config_dir=None):
    """Abs path to the installed `mcp_server.py`: installed_plugins.json first, then
    the highest cache version. Raises RuntimeError if neither path resolves."""
    config_dir = config_dir or _config_dir()
    server = _from_installed_plugins(config_dir) or _from_cache(config_dir)
    if not server:
        raise RuntimeError(
            "task-station: could not resolve mcp_server.py under %s — no usable "
            "installed_plugins.json entry and no cache version with the file. "
            "Re-run `task-station config --desktop-bridge on` after the plugin "
            "is installed." % config_dir)
    return server


def main():
    """Resolve, then become the real MCP server (same interpreter, same stdio)."""
    server = resolve_server()
    os.execv(sys.executable, [sys.executable, server] + sys.argv[1:])


if __name__ == "__main__":
    main()
