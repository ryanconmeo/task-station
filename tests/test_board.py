"""Visual HTML board (1.15.0): `task-station board` writes a self-contained HTML
file of all tasks (open + closed) — seqs, titles, briefing fields — with NO
server, NO deps, and NO external http(s) asset references. Empty store renders
without crashing."""
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

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class BoardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.store.reset_cache()

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        ts.store.reset_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self, title, color="green", effort="m", closed=False, status=None):
        t = ts.new_task(title, "summary for " + title, color=color, effort=effort)
        ts.save_task(t)
        ts.ensure_seqs()
        t = ts.load_task(t["id"])
        if closed:
            t["status"] = "closed"
        elif status:
            t["status"] = status
        ts.save_task(t)
        return ts.load_task(t["id"])

    def _run_board(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_board(_Args(open=False))
        path = buf.getvalue().strip().splitlines()[-1]
        with open(path, encoding="utf-8") as f:
            return path, f.read()

    def test_board_writes_self_contained_html(self):
        a = self._seed("Open feature task")
        b = self._seed("Done thing", closed=True)
        path, html = self._run_board()
        self.assertTrue(path.endswith("board.html"))
        self.assertTrue(os.path.exists(path))
        # valid-ish document shell + inline style, no external assets
        self.assertIn("<!doctype html>", html.lower())
        self.assertIn("<style>", html)
        for needle in ("<script", "<link ", "src=", "@import", "url(http"):
            self.assertNotIn(needle, html,
                             "board must be self-contained (found %r)" % needle)
        # both tasks present by seq + title; both sections shown
        self.assertIn(str(a["seq"]), html)
        self.assertIn("Open feature task", html)
        self.assertIn("Done thing", html)
        self.assertIn("Open", html)
        self.assertIn("Closed", html)

    def test_board_no_http_when_no_pr_urls(self):
        # With no PR URLs anywhere, a self-contained board references nothing remote.
        self._seed("Plain task")
        _, html = self._run_board()
        self.assertNotIn("http", html.lower())

    def test_board_surfaces_briefing_fields(self):
        t = self._seed("Briefed task")
        t["state"] = "next: ship the board"
        t["files"] = ["/repo/lib/render_board.py"]
        t["projects"] = ["task-station"]
        t["log"] = [{"ts": "t1", "note": "PR https://github.com/o/r/pull/3"}]
        ts.save_task(t)
        _, html = self._run_board()
        self.assertIn("next: ship the board", html)
        self.assertIn("render_board.py", html)
        self.assertIn("task-station", html)
        # PR link rendered as a real anchor (content, not an external asset)
        self.assertIn('href="https://github.com/o/r/pull/3"', html)

    def test_board_empty_store(self):
        path, html = self._run_board()
        self.assertTrue(os.path.exists(path))
        self.assertIn("No tasks yet", html)
        # still self-contained
        self.assertNotIn("<script", html)

    def test_render_html_directly_on_empty_list(self):
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
        import render_board
        html = render_board.render_html([])
        self.assertIn("No tasks yet", html)
        self.assertIn("</body></html>", html)


if __name__ == "__main__":
    unittest.main()
