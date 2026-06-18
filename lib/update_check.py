"""Opt-in, default-OFF update check for the `/todo` list footer.

Privacy contract:
- When the user has NOT opted in (the default), `nudge_line()` returns "" before
  any network or filesystem access — ZERO network activity.
- When opted in, at most ONE `git ls-remote` version probe per 24h, cached in
  `<data_dir>/update-check.json`, with a hard ~2.5s timeout.
- Any failure (offline, timeout, parse error, …) is silent: no error, no nudge.
- NO task data ever leaves the machine — only `git ls-remote --tags <repo>` runs.
"""
import json, os, re, subprocess, time

import config
import paths

CACHE_NAME = "update-check.json"
CACHE_TTL = 24 * 60 * 60  # seconds
NET_TIMEOUT = 2.5  # seconds — hard cap on the version probe
_TAG_RE = re.compile(r"refs/tags/v(\d+)\.(\d+)\.(\d+)$")


def _plugin_json_path():
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if root:
        return os.path.join(root, ".claude-plugin", "plugin.json")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        ".claude-plugin", "plugin.json")


def _load_plugin():
    try:
        with open(_plugin_json_path()) as f:
            return json.load(f)
    except Exception:
        return {}


def _parse_semver(s):
    """'1.2.3' -> (1, 2, 3); None on anything malformed."""
    try:
        parts = str(s).strip().lstrip("v").split(".")
        if len(parts) != 3:
            return None
        return tuple(int(p) for p in parts)
    except Exception:
        return None


def _cache_path():
    return os.path.join(paths.data_dir(), CACHE_NAME)


def _read_cache():
    try:
        with open(_cache_path()) as f:
            return json.load(f)
    except Exception:
        return {}


def _write_cache(latest):
    try:
        data = paths.data_dir()
        os.makedirs(data, exist_ok=True)
        tmp = _cache_path() + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"checked_at": int(time.time()), "latest": latest}, f)
        os.replace(tmp, _cache_path())
    except Exception:
        pass


def _fetch_latest(repo):
    """ONE `git ls-remote --tags` probe; highest vX.Y.Z tag, or None on any failure."""
    try:
        out = subprocess.run(
            ["git", "ls-remote", "--tags", repo],
            capture_output=True, text=True, timeout=NET_TIMEOUT,
        )
        if out.returncode != 0:
            return None
        best = None
        for line in out.stdout.splitlines():
            m = _TAG_RE.search(line.strip())
            if not m:
                continue
            ver = tuple(int(g) for g in m.groups())
            if best is None or ver > best:
                best = ver
        if best is None:
            return None
        return "%d.%d.%d" % best
    except Exception:
        return None


def nudge_line():
    """One-line update footer, or "" when off / up-to-date / on any failure.

    Never raises and never blocks longer than the network timeout.
    """
    try:
        if not config.update_check_enabled():
            return ""

        plugin = _load_plugin()
        installed = _parse_semver(plugin.get("version"))
        if installed is None:
            return ""

        cache = _read_cache()
        latest = cache.get("latest")
        checked_at = cache.get("checked_at", 0)
        fresh = isinstance(checked_at, (int, float)) and (time.time() - checked_at) < CACHE_TTL

        if not fresh:
            repo = plugin.get("repository")
            fetched = _fetch_latest(repo) if repo else None
            if fetched is not None:
                latest = fetched
                _write_cache(latest)
            # else: fall back to whatever was cached (possibly None) — silently.

        latest_t = _parse_semver(latest)
        if latest_t is None or latest_t <= installed:
            return ""
        return ("⬆ Task Station v%s available — update: "
                "/plugin update task-station@ryanconmeo" % latest)
    except Exception:
        return ""
