"""update / pin / unpin / add-project accept a comma-separated --task list (the
same contract as `done`): one result line per ref, the same flags applied to each
task, a bad ref reported but not aborting the rest."""
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


class _Args:
    def __init__(self, **kw):
        defaults = dict(task=None, session=None, title=None, summary=None,
                        append_summary=None, color=None, effort=None, project=None)
        defaults.update(kw)
        self.__dict__.update(defaults)


class BatchSubcommandsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        # Keep session-transcript probing off the real ~/.claude/projects store.
        ts.PROJECTS_ROOT = os.path.join(self.tmp, "projects")

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self, title):
        t = ts.new_task(title, "summary")
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _out(self, fn, args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            fn(args)
        return buf.getvalue()

    # ---- update --------------------------------------------------------------
    def test_update_comma_list_applies_to_each(self):
        a = self._seed("Alpha")
        b = self._seed("Beta")
        out = self._out(ts.cmd_update, _Args(task="%d,%d" % (a["seq"], b["seq"]), effort="l"))
        self.assertEqual(out.count("updated task"), 2)
        self.assertEqual(ts.load_task(a["id"])["effort"], "L")
        self.assertEqual(ts.load_task(b["id"])["effort"], "L")

    def test_update_bad_ref_does_not_abort_others(self):
        a = self._seed("Alpha")
        b = self._seed("Beta")
        out = self._out(ts.cmd_update, _Args(task="%d,99999,%d" % (a["seq"], b["seq"]), title="X"))
        self.assertIn("no task matching '99999'", out)
        self.assertEqual(out.count("updated task"), 2)
        self.assertEqual(ts.load_task(a["id"])["title"], "X")
        self.assertEqual(ts.load_task(b["id"])["title"], "X")

    def test_update_single_ref_still_works(self):
        a = self._seed("Solo")
        out = self._out(ts.cmd_update, _Args(task=str(a["seq"]), summary="new"))
        self.assertEqual(out.count("updated task"), 1)
        self.assertEqual(ts.load_task(a["id"])["summary"], "new")

    # ---- pin / unpin ---------------------------------------------------------
    def test_pin_comma_list(self):
        a = self._seed("Alpha")
        b = self._seed("Beta")
        out = self._out(ts.cmd_pin, _Args(task="%d,%d" % (a["seq"], b["seq"]), session="sess1"))
        self.assertEqual(out.count("Pinned task"), 2)
        self.assertEqual(ts.load_task(a["id"])["pinned_session"], "sess1")
        self.assertEqual(ts.load_task(b["id"])["pinned_session"], "sess1")

    def test_unpin_comma_list(self):
        a = self._seed("Alpha")
        b = self._seed("Beta")
        self._out(ts.cmd_pin, _Args(task="%d,%d" % (a["seq"], b["seq"]), session="sess1"))
        out = self._out(ts.cmd_unpin, _Args(task="%d,%d" % (a["seq"], b["seq"])))
        self.assertEqual(out.count("Unpinned task"), 2)
        self.assertNotIn("pinned_session", ts.load_task(a["id"]))
        self.assertNotIn("pinned_session", ts.load_task(b["id"]))

    def test_pin_bad_ref_does_not_abort_others(self):
        a = self._seed("Alpha")
        b = self._seed("Beta")
        out = self._out(ts.cmd_pin, _Args(task="%d,99999,%d" % (a["seq"], b["seq"]), session="s"))
        self.assertIn("no task matching '99999'", out)
        self.assertEqual(out.count("Pinned task"), 2)

    # ---- bare /pin: pin THIS session to its attached task (1.24.0) ------------
    def test_pin_bare_session_pins_attached_task(self):
        a = self._seed("Attached")
        ts.set_link("mysess", a["id"])
        out = self._out(ts.cmd_pin, _Args(task=None, session="mysess"))
        self.assertIn("Pinned task", out)
        self.assertEqual(ts.load_task(a["id"])["pinned_session"], "mysess")

    def test_pin_bare_session_unattached_says_nothing_to_pin(self):
        out = self._out(ts.cmd_pin, _Args(task=None, session="orphan"))
        self.assertIn("No task is attached to this session — nothing to pin.", out)

    def test_pin_no_task_no_session_errors_on_stderr(self):
        errbuf = io.StringIO()
        with redirect_stderr(errbuf):
            ts.cmd_pin(_Args(task=None, session=None))
        self.assertIn("no task given", errbuf.getvalue())

    # ---- bare /unpin: unpin THIS session's attached task (inverse of bare /pin) --
    def test_unpin_bare_session_unpins_attached_task(self):
        a = self._seed("Attached")
        ts.set_link("mysess", a["id"])
        self._out(ts.cmd_pin, _Args(task=None, session="mysess"))
        self.assertEqual(ts.load_task(a["id"])["pinned_session"], "mysess")
        out = self._out(ts.cmd_unpin, _Args(task=None, session="mysess"))
        self.assertIn("Unpinned task", out)
        self.assertNotIn("pinned_session", ts.load_task(a["id"]))

    def test_unpin_bare_session_unattached_says_nothing_to_unpin(self):
        out = self._out(ts.cmd_unpin, _Args(task=None, session="orphan"))
        self.assertIn("No task is attached to this session — nothing to unpin.", out)

    def test_unpin_no_task_no_session_errors_on_stderr(self):
        errbuf = io.StringIO()
        with redirect_stderr(errbuf):
            ts.cmd_unpin(_Args(task=None, session=None))
        self.assertIn("no task given", errbuf.getvalue())

    def test_todo_unpin_bare_routes_to_attached_task(self):
        a = self._seed("Attached")
        ts.set_link("s9", a["id"])
        self._out(ts.cmd_pin, _Args(task=None, session="s9"))
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts._todo_unpin(_Args(session="s9"), "")
        self.assertIn("Unpinned task", buf.getvalue())
        self.assertNotIn("pinned_session", ts.load_task(a["id"]))

    # ---- add-project (success silent, errors on stderr) ----------------------
    def test_add_project_comma_list(self):
        a = self._seed("Alpha")
        b = self._seed("Beta")
        out = self._out(ts.cmd_add_project, _Args(task="%d,%d" % (a["seq"], b["seq"]), project="repo"))
        self.assertEqual(out.strip(), "")   # success is silent
        self.assertIn("repo", ts.load_task(a["id"])["projects"])
        self.assertIn("repo", ts.load_task(b["id"])["projects"])

    def test_add_project_bad_ref_reported_on_stderr(self):
        a = self._seed("Alpha")
        errbuf = io.StringIO()
        with redirect_stderr(errbuf):
            ts.cmd_add_project(_Args(task="%d,99999" % a["seq"], project="repo"))
        self.assertIn("no task matching '99999'", errbuf.getvalue())
        self.assertIn("repo", ts.load_task(a["id"])["projects"])


if __name__ == "__main__":
    unittest.main()
