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
