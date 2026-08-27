"""Pin Task Station's data home to a throwaway temp dir for the whole test
session BEFORE any test module imports task-station.py.

task-station.py freezes its store paths (DATA / TASKS_DIR / LINKS_DIR) at import
time from paths.data_dir(), so the override must exist before collection or the
suite would read/write the real ~/.claude/task-station-data store.

paths.data_dir() resolves: TASK_STATION_HOME > CLAUDE_CONFIG_DIR/task-station-data
> XDG_STATE_HOME/task-station > ~/.claude/task-station-data. Pinning
TASK_STATION_HOME ALONE is not enough — several test tearDowns
os.environ.pop('TASK_STATION_HOME') (and one pops all three), so after the first
such pop data_dir() falls THROUGH to CLAUDE_CONFIG_DIR/XDG, i.e. the real
~/.claude. So pin all three fallback inputs to ONE shared throwaway dir.
`setdefault` means an explicitly-exported value (e.g. in CI) still wins. Tests
that need clean per-test isolation additionally repoint the module's path globals
in setUp. This mirrors tests/__init__.py (the `unittest` path)."""
import os
import tempfile

_tsd = tempfile.mkdtemp(prefix="task-station-tests-")
os.environ.setdefault("TASK_STATION_HOME", _tsd)
os.environ.setdefault("CLAUDE_CONFIG_DIR", _tsd)
os.environ.setdefault("XDG_STATE_HOME", _tsd)
# #444 retired the preview engine: ONE board, no engine to select, so there is
# nothing to pin here any more. A stale TASK_STATION_BOARD_ENGINE is inert — nothing reads it.
# bg-aware resume (#464) probes `claude agents --json`; suppress it in tests so no
# test shells out to the real CLI (nondeterministic live-agent list). Tests that
# need a live set stub ts._LIVE_BG_INDEX directly.
os.environ.setdefault("TASK_STATION_NO_AGENT_QUERY", "1")

# THE SUITE'S LAUNCHING DIRECTORY IS A TRUSTED PROJECT. Since 3.26.0 `invoke` REFUSES
# to default a child into a directory no session could start in (see #570: a defaulted
# cwd that could not hold trust killed two children at zero turns). Every `invoke` test
# that passes no `--cwd` therefore resolves to the process cwd, and with no
# `~/.claude.json` at all that is a refusal — so the ordinary condition is stated here
# ONCE rather than re-asserted in a dozen fixtures. Tests that are ABOUT an untrusted
# or missing config repoint CLAUDE_CONFIG_DIR themselves and are unaffected.
def _trust_cwd():
    import json
    cfg = os.path.join(os.environ['CLAUDE_CONFIG_DIR'], '.claude.json')
    doc = {}
    if os.path.exists(cfg):
        try:
            with open(cfg, encoding='utf-8') as f:
                doc = json.load(f) or {}
        except Exception:
            doc = {}
    projects = doc.setdefault('projects', {})
    for p in {os.getcwd(), os.path.realpath(os.getcwd())}:
        projects.setdefault(p, {'hasTrustDialogAccepted': True})
    os.makedirs(os.path.dirname(cfg), exist_ok=True)
    with open(cfg, 'w', encoding='utf-8') as f:
        json.dump(doc, f)


_trust_cwd()
