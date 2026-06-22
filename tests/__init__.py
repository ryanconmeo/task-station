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
