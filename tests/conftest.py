"""Pin Task Station's data home to a throwaway temp dir for the whole test
session BEFORE any test module imports task-station.py.

task-station.py freezes its store paths (DATA / TASKS_DIR / LINKS_DIR) at import
time from TASK_STATION_HOME, so the override must exist before collection or the
suite would read/write the real ~/.claude/task-station-data store. `setdefault`
means an explicitly-exported TASK_STATION_HOME (e.g. in CI) still wins. Tests that
need clean per-test isolation additionally repoint the module's path globals at
their own tmpdir in setUp."""
import os
import tempfile

os.environ.setdefault("TASK_STATION_HOME", tempfile.mkdtemp(prefix="task-station-tests-"))
