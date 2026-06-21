import os, tempfile
# task-station.py freezes its store paths at import from TASK_STATION_HOME, and
# conftest.py only runs under pytest — so set this here too, before any test module
# imports the engine, to isolate  runs from the real store.
os.environ.setdefault('TASK_STATION_HOME', tempfile.mkdtemp(prefix='task-station-tests-'))
