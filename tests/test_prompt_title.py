"""prompt-title emits the OSC tab/window-title escape `\\033]0;#<seq>-<ln>: <title>\\007`
for an attached session, and NOTHING when unattached / skipped / disabled. Also
locks cmd_session_title's `#<seq>-<ln>: <title>` output (was `#<seq>: <title>`,
and before that `task-station-<seq> · <title>`).

The `-<ln>` half — the session's roster line — is what makes two sessions on one
task distinguishable; test_session_title_ln.py owns the collision and fallback
cases. Here the sessions are ROSTERED (via touch) so these lock the normal path."""
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
    def __init__(self, session=None):
        self.session = session


class PromptTitleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _repoint(self.tmp)
        os.environ.pop("TASK_STATION_TITLE", None)

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        os.environ.pop("TASK_STATION_TITLE", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self, title, summary, session=None):
        t = ts.new_task(title, summary)
        ts.save_task(t)
        ts.ensure_seqs()
        if session:
            # Roster the session as a hub so it HAS a roster ln (#569) — a title
            # is per-session now, and touch() is what mints the number.
            t = ts.load_task(t["id"])
            ts.touch(t, session=session, note="attached")
            ts.save_task(t)
        return ts.load_task(t["id"])

    def _ln(self, task, session):
        return ts.ordinal_label(ts.load_task(task["id"]), session)

    def _run(self, session):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_prompt_title(_Args(session=session))
        return buf.getvalue()

    def test_attached_emits_exact_osc(self):
        t = self._seed("token-efficiency + SQLite store", "x", session="sess-1")
        ts.set_link("sess-1", t["id"])
        out = self._run("sess-1")
        self.assertEqual(out, "\033]0;#%s: %s\007" % (self._ln(t, "sess-1"), t["title"]))
        # bytes spell out: OSC introducer, then BEL terminator, with a literal '#'
        self.assertTrue(out.startswith("\033]0;#"))
        self.assertTrue(out.endswith("\007"))
        self.assertNotIn("task-station", out)

    def test_uses_live_seq_and_title(self):
        t = self._seed("Original title", "x", session="sess-2")
        ts.set_link("sess-2", t["id"])
        # rename underneath: the escape must reflect the CURRENT title + stable ln
        t["title"] = "Renamed title"
        ts.save_task(t)
        out = self._run("sess-2")
        self.assertEqual(out, "\033]0;#%s: Renamed title\007" % self._ln(t, "sess-2"))

    def test_unattached_emits_nothing(self):
        self.assertEqual(self._run("nobody"), "")

    def test_skipped_emits_nothing(self):
        ts.set_link("sess-skip", ts.SKIP_SENTINEL)
        self.assertEqual(self._run("sess-skip"), "")

    def test_disabled_via_env_emits_nothing(self):
        t = self._seed("Anything", "x", session="sess-3")
        ts.set_link("sess-3", t["id"])
        os.environ["TASK_STATION_TITLE"] = "off"
        self.assertEqual(self._run("sess-3"), "")

    def test_session_title_reformatted(self):
        t = self._seed("token-efficiency + SQLite store", "x", session="sess-4")
        ts.set_link("sess-4", t["id"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_session_title(_Args(session="sess-4"))
        self.assertEqual(buf.getvalue().strip(),
                         "#%s: %s" % (self._ln(t, "sess-4"), t["title"]))


if __name__ == "__main__":
    unittest.main()
