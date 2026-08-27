import os, tempfile

# Isolate the whole test run from the user's REAL store BEFORE any test module
# imports the engine. task-station.py freezes its store paths at import from
# paths.data_dir(), which resolves: TASK_STATION_HOME > CLAUDE_CONFIG_DIR/
# task-station-data > XDG_STATE_HOME/task-station > ~/.claude/task-station-data.
#
# Pinning TASK_STATION_HOME alone is NOT enough: several test tearDowns
# os.environ.pop('TASK_STATION_HOME') (and one pops all three), so after the first
# such tearDown the var is gone for every later test and data_dir() falls THROUGH
# to CLAUDE_CONFIG_DIR/XDG — i.e. straight into the real ~/.claude. So pin all
# three fallback inputs to ONE shared throwaway dir. setdefault means an explicit
# CI/dev override still wins. conftest.py (pytest-only) mirrors this exactly.
_tsd = tempfile.mkdtemp(prefix='task-station-tests-')
os.environ.setdefault('TASK_STATION_HOME', _tsd)
os.environ.setdefault('CLAUDE_CONFIG_DIR', _tsd)   # safety net: tearDowns pop TASK_STATION_HOME, so the
os.environ.setdefault('XDG_STATE_HOME', _tsd)      # fallback must NEVER reach the real ~/.claude

# #444 retired the preview engine: there is ONE board (tools/render_board.py) and
# no engine to select, so nothing pins TASK_STATION_BOARD_ENGINE any more. A stale value
# in a dev environment is inert — no code reads it.

# bg-aware resume (#464) probes `claude agents --json` for live background sessions.
# In tests that must be deterministic and never shell out to the real CLI (whose
# live-agent list on a dev box is arbitrary), suppress the probe — tests that need
# a specific live set stub ts._LIVE_BG_INDEX directly.
os.environ.setdefault('TASK_STATION_NO_AGENT_QUERY', '1')

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
