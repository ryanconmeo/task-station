"""prompt-title emits the OSC tab/window-title escape `\\033]0;#<seq>: <title>\\007`
for an attached session, and NOTHING when unattached / skipped / disabled. Also
locks cmd_session_title's reformatted `#<seq>: <title>` output (was the old
`task-station-<seq> · <title>`)."""
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
    def __init__(self, session=None):
        self.session = session


class PromptTitleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        os.environ.pop("TASK_STATION_TITLE", None)

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        os.environ.pop("TASK_STATION_TITLE", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self, title, summary):
        t = ts.new_task(title, summary)
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _run(self, session):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_prompt_title(_Args(session=session))
        return buf.getvalue()

    def test_attached_emits_exact_osc(self):
        t = self._seed("token-efficiency + SQLite store", "x")
        ts.set_link("sess-1", t["id"])
        out = self._run("sess-1")
        self.assertEqual(out, "\033]0;#%s: %s\007" % (t["seq"], t["title"]))
        # bytes spell out: OSC introducer, then BEL terminator, with a literal '#'
        self.assertTrue(out.startswith("\033]0;#"))
        self.assertTrue(out.endswith("\007"))
        self.assertNotIn("task-station", out)

    def test_uses_live_seq_and_title(self):
        t = self._seed("Original title", "x")
        ts.set_link("sess-2", t["id"])
        # rename underneath: the escape must reflect the CURRENT title + stable seq
        t["title"] = "Renamed title"
        ts.save_task(t)
        out = self._run("sess-2")
        self.assertEqual(out, "\033]0;#%s: Renamed title\007" % t["seq"])

    def test_unattached_emits_nothing(self):
        self.assertEqual(self._run("nobody"), "")

    def test_skipped_emits_nothing(self):
        ts.set_link("sess-skip", ts.SKIP_SENTINEL)
        self.assertEqual(self._run("sess-skip"), "")

    def test_disabled_via_env_emits_nothing(self):
        t = self._seed("Anything", "x")
        ts.set_link("sess-3", t["id"])
        os.environ["TASK_STATION_TITLE"] = "off"
        self.assertEqual(self._run("sess-3"), "")

    def test_session_title_reformatted(self):
        t = self._seed("token-efficiency + SQLite store", "x")
        ts.set_link("sess-4", t["id"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_session_title(_Args(session="sess-4"))
        self.assertEqual(buf.getvalue().strip(), "#%s: %s" % (t["seq"], t["title"]))


if __name__ == "__main__":
    unittest.main()
