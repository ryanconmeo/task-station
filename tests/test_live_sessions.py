"""live_session_count(task): counts only sessions whose link STILL resolves to
the task (append-only `sessions` over-reports), and the ` ⧉N` list marker /
`Live sessions:` detail line that surface it."""
import importlib.util
import os
import shutil
import sys
import tempfile
import unittest

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

# task-station.py has a hyphen, so it can't be a normal import — load it by path.
_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class LiveSessionCountTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        # Repoint the module's import-frozen path globals at this test's tmpdir
        # so each test gets a pristine, isolated store.
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self, title):
        t = ts.new_task(title, "summary")
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def test_counts_only_live_links(self):
        a = self._seed("Alpha")
        b = self._seed("Beta")
        # Two sessions touch A (recorded in sessions[] and linked to A).
        a.setdefault("sessions", []).extend(["s1", "s2"])
        ts.save_task(a)
        ts.set_link("s1", a["id"])
        ts.set_link("s2", a["id"])
        self.assertEqual(ts.live_session_count(ts.load_task(a["id"])), 2)

        # s2 re-attaches elsewhere: it stays in A.sessions (append-only) but its
        # link now points at B, so A's live count drops to 1.
        ts.set_link("s2", b["id"])
        self.assertEqual(ts.live_session_count(ts.load_task(a["id"])), 1)

        # s1 detaches entirely → 0 live, even though sessions[] still lists both.
        ts.clear_link("s1")
        reloaded = ts.load_task(a["id"])
        self.assertEqual(ts.live_session_count(reloaded), 0)
        self.assertEqual(len(reloaded["sessions"]), 2)  # append-only, unchanged

    def test_no_sessions_is_zero(self):
        a = self._seed("Lonely")
        self.assertEqual(ts.live_session_count(a), 0)

    def test_list_marker_only_when_more_than_one(self):
        a = self._seed("Multi")
        b = self._seed("Single")
        a.setdefault("sessions", []).extend(["x1", "x2"])
        ts.save_task(a)
        ts.set_link("x1", a["id"])
        ts.set_link("x2", a["id"])
        b.setdefault("sessions", []).append("y1")
        ts.save_task(b)
        ts.set_link("y1", b["id"])
        out = ts._format_list()
        self.assertIn("⧉2", out)          # the multi-session task is marked
        # The single-session task line carries no marker.
        single_line = [ln for ln in out.splitlines() if "Single" in ln][0]
        self.assertNotIn("⧉", single_line)

    def test_detail_shows_live_sessions_line(self):
        a = self._seed("Detailed")
        a.setdefault("sessions", []).extend(["d1", "d2"])
        ts.save_task(a)
        ts.set_link("d1", a["id"])
        # d2 is stale (never linked) — live should read 1, total 2.
        detail = ts._format_detail(ts.load_task(a["id"]), "d1")
        self.assertIn("Live sessions: 1", detail)
        self.assertIn("of 2 ever attached", detail)


if __name__ == "__main__":
    unittest.main()
