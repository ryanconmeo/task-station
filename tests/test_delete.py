"""Hidden `delete` command (1.12.0): hard-removes ONE task, detaches any session
linked to it, and leaves every other task untouched. Lifecycle is normally
close-not-delete; this is the maintenance escape hatch."""
import importlib
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

import store        # noqa: E402

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


class _Args:
    def __init__(self, task):
        self.task = task


class Delete(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.PROJECTS_ROOT = os.path.join(self.tmp, "projects")
        store.reset_cache()

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _attach(self, title, session):
        """Create a task and attach a session the way create/attach do (records the
        session in task['sessions'] so delete's link-clearing path sees it)."""
        t = ts.new_task(title, "x")
        ts.touch(t, session=session, note="created")
        ts.save_task(t)
        ts.set_link(session, t["id"])
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _run(self, ref):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_delete(_Args(task=ref))
        return buf.getvalue()

    def test_removes_the_task(self):
        t = self._attach("doomed", "sess-1")
        out = self._run(str(t["seq"]))
        self.assertIsNone(ts.load_task(t["id"]))
        self.assertIn("Deleted task", out)
        self.assertIn("#%s" % t["seq"], out)
        self.assertIn("doomed", out)

    def test_clears_session_link(self):
        t = self._attach("doomed", "sess-link")
        self.assertEqual(ts.get_link("sess-link"), t["id"])
        self._run(t["id"][:8])
        # Session no longer resolves to the deleted task.
        self.assertIsNone(ts.get_link("sess-link"))

    def test_nonexistent_ref_reports_no_match(self):
        out = self._run("9999")
        self.assertIn("No task matching", out)

    def test_deletes_only_one_task(self):
        keep = self._attach("keeper", "sess-keep")
        drop = self._attach("dropme", "sess-drop")
        self._run(str(drop["seq"]))
        self.assertIsNone(ts.load_task(drop["id"]))
        # The other task and its link survive intact.
        self.assertIsNotNone(ts.load_task(keep["id"]))
        self.assertEqual(ts.get_link("sess-keep"), keep["id"])


if __name__ == "__main__":
    unittest.main()
