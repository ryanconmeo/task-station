"""cmd_done --task accepts a comma-separated list: closes each, one line per
task, tolerating a bad ref in the middle. No windows are opened (pure data)."""
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

import store  # noqa: E402  (normal import — store.py has no hyphen)

# task-station.py has a hyphen, so it can't be a normal import — load it by path.
_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


def _repoint(tmp):
    """Point task-station.py's import-frozen path globals at a fresh tmp store so
    writes can NEVER reach the real ~/.claude store, regardless of how the test is
    invoked (flat-module discovery skips tests/__init__.py)."""
    os.environ["TASK_STATION_HOME"] = tmp
    ts.DATA = tmp
    ts.STORE = os.path.join(tmp, "store")
    ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
    ts.LINKS_DIR = os.path.join(ts.STORE, "links")
    ts.PROJECTS_ROOT = os.path.join(tmp, "projects")
    store.reset_cache()


class _Args:
    def __init__(self, task=None, session=None):
        self.task = task
        self.session = session


import config  # noqa: E402  (window-close opt-in is gated by this)


class _PopenRecorder:
    """Stand-in for subprocess.Popen: records every invocation instead of
    spawning a real process, so no terminal window is ever closed in tests."""
    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return None


class DoneWindowClosePolicyTest(unittest.TestCase):
    """The no-arg /done window-close is OPT-IN (default OFF). We cannot tell a
    human-typed /done from a model Skill-tool /done, so the destructive close is
    gated on config `done_closes_window` rather than intent-detected."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _repoint(self.tmp)
        os.environ.pop("TASK_STATION_DONE_CLOSES_WINDOW", None)
        self.rec = _PopenRecorder()
        self._orig_popen = ts.subprocess.Popen
        ts.subprocess.Popen = self.rec

    def tearDown(self):
        ts.subprocess.Popen = self._orig_popen
        store.reset_cache()
        os.environ.pop("TASK_STATION_DONE_CLOSES_WINDOW", None)
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed_attached(self, session, provisional=False):
        t = ts.new_task("Windowed task", "close me")
        if provisional:
            t["provisional"] = True
        ts.save_task(t)
        ts.ensure_seqs()
        ts.set_link(session, t["id"])
        return ts.load_task(t["id"])

    def _done_session(self, session):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_done(_Args(session=session))
        return buf.getvalue()

    # --- the helper in isolation -------------------------------------------
    def test_helper_no_spawn_when_config_off(self):
        ts._maybe_close_session_window("sess-1")
        self.assertEqual(self.rec.calls, [])

    def test_helper_spawns_when_config_on(self):
        config.set("done_closes_window", True)
        ts._maybe_close_session_window("sess-1")
        self.assertEqual(len(self.rec.calls), 1)
        argv = self.rec.calls[0][0][0]
        self.assertEqual(argv[0], "bash")
        self.assertTrue(argv[1].endswith("close-session-window.sh"))
        self.assertIn("--detach", argv)
        self.assertIn("--after", argv)

    # --- cmd_done --session (the /done no-arg path) ------------------------
    def test_session_close_no_window_when_config_off(self):
        # Regression for the incident: default OFF must NOT close the window.
        t = self._seed_attached("sess-off")
        self._done_session("sess-off")
        self.assertEqual(ts.load_task(t["id"])["status"], "closed")
        self.assertEqual(self.rec.calls, [])

    def test_session_close_closes_window_when_config_on(self):
        config.set("done_closes_window", True)
        self._seed_attached("sess-on")
        self._done_session("sess-on")
        self.assertEqual(len(self.rec.calls), 1)

    def test_provisional_discard_no_window_when_config_off(self):
        self._seed_attached("sess-prov", provisional=True)
        out = self._done_session("sess-prov")
        self.assertIn("Discarded provisional", out)
        self.assertEqual(self.rec.calls, [])

    def test_provisional_discard_closes_window_when_config_on(self):
        config.set("done_closes_window", True)
        self._seed_attached("sess-prov-on", provisional=True)
        out = self._done_session("sess-prov-on")
        self.assertIn("Discarded provisional", out)
        self.assertEqual(len(self.rec.calls), 1)

    # --- cmd_done --task NEVER closes a window, regardless of config -------
    def test_task_path_never_closes_window_config_on(self):
        config.set("done_closes_window", True)
        t = ts.new_task("Byref task", "x")
        ts.save_task(t)
        ts.ensure_seqs()
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_done(_Args(task=str(ts.load_task(t["id"])["seq"])))
        self.assertEqual(self.rec.calls, [])


class DoneMultiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _repoint(self.tmp)

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self, title, summary):
        t = ts.new_task(title, summary)
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _run(self, task):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_done(_Args(task=task))
        return buf.getvalue()

    def test_comma_list_closes_both(self):
        a = self._seed("First task", "do the thing")
        b = self._seed("Second task", "do another thing")
        out = self._run("%d,%d" % (a["seq"], b["seq"]))
        # One result line per task, both closed by name.
        self.assertIn("First task", out)
        self.assertIn("Second task", out)
        self.assertEqual(out.count("Closed task"), 2)
        self.assertEqual(ts.load_task(a["id"])["status"], "closed")
        self.assertEqual(ts.load_task(b["id"])["status"], "closed")

    def test_bad_ref_does_not_abort_others(self):
        a = self._seed("Alpha", "a")
        b = self._seed("Beta", "b")
        # Middle ref is bogus — it should be reported but not stop the others.
        out = self._run("%d,99999,%d" % (a["seq"], b["seq"]))
        self.assertIn("No task matching '99999'.", out)
        self.assertEqual(out.count("Closed task"), 2)
        self.assertEqual(ts.load_task(a["id"])["status"], "closed")
        self.assertEqual(ts.load_task(b["id"])["status"], "closed")

    def test_single_number_still_works(self):
        a = self._seed("Solo", "s")
        out = self._run(str(a["seq"]))
        self.assertEqual(out.count("Closed task"), 1)
        self.assertEqual(ts.load_task(a["id"])["status"], "closed")

    def test_already_closed_reported_per_task(self):
        a = self._seed("Once", "o")
        self._run(str(a["seq"]))                       # close it first
        out = self._run(str(a["seq"]))                 # closing again
        self.assertIn("already closed", out)

    def test_whitespace_and_empties_tolerated(self):
        a = self._seed("Spacey", "x")
        b = self._seed("Tidy", "y")
        out = self._run(" %d , , %d ," % (a["seq"], b["seq"]))
        self.assertEqual(out.count("Closed task"), 2)


if __name__ == "__main__":
    unittest.main()
