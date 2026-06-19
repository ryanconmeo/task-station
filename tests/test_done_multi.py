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

# task-station.py has a hyphen, so it can't be a normal import — load it by path.
_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


class _Args:
    def __init__(self, task=None, session=None):
        self.task = task
        self.session = session


class DoneMultiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp

    def tearDown(self):
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
