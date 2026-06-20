"""render --format md emits two GitHub tables (Open then Closed) with the exact
columns, stable seq numbers, closed-limit handling + hidden-older note, and the
Commands footer as a Markdown bullet list — printed verbatim by the skill."""
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


class RenderMarkdownTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self, title, effort=None, color=None, closed=False):
        t = ts.new_task(title, "summary for " + title, color=color, effort=effort)
        ts.save_task(t)
        ts.ensure_seqs()
        t = ts.load_task(t["id"])
        if closed:
            t["status"] = "closed"
            ts.save_task(t)
        return ts.load_task(t["id"])

    def test_empty_store_message(self):
        out = ts._format_list_md()
        self.assertIn("No tasks yet", out)
        self.assertNotIn("|", out)

    def test_two_tables_columns_and_seq(self):
        a = self._seed("Open one", effort="m")
        b = self._seed("Closed one", effort="xl", closed=True)
        out = ts._format_list_md()
        # Section headers, Open before Closed.
        self.assertIn("### Open", out)
        self.assertIn("### Closed", out)
        self.assertLess(out.index("### Open"), out.index("### Closed"))
        # Exact header row + right-aligned separator.
        self.assertIn("| # | Task | Category | Effort | Activity |", out)
        self.assertIn("|--:|", out)
        # Stable seq numbers appear verbatim in their rows; the open row carries
        # the leading inquiry glyph in its `#` cell, the closed row mutes it.
        self.assertIn("| ◦ %d | Open one" % a["seq"], out)
        self.assertIn("| %d | Closed one" % b["seq"], out)
        # Effort gauge rendered in the cell.
        self.assertIn("▰", out)

    def test_commands_footer_as_bullets(self):
        self._seed("Some task")
        out = ts._format_list_md()
        self.assertIn("**Commands:**", out)
        # Footer rendered as bullets, not the dense `·`-separated ASCII line.
        self.assertIn("\n- /todo", out)
        self.assertNotIn("Commands:  /todo", out)

    def test_closed_limit_and_hidden_note(self):
        # 7 closed tasks, default cap is MAX_CLOSED_IN_LIST.
        for i in range(7):
            self._seed("Closed %d" % i, closed=True)
        out = ts._format_list_md(closed_limit=ts.MAX_CLOSED_IN_LIST)
        hidden = 7 - ts.MAX_CLOSED_IN_LIST
        self.assertIn("%d older closed task(s) hidden" % hidden, out)
        # `all` shows every closed task and drops the note.
        out_all = ts._format_list_md(closed_limit=None)
        self.assertNotIn("older closed task(s) hidden", out_all)

    def test_cmd_render_routes_md(self):
        a = self._seed("Routed task")
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_render(_Args(session="sess", arg="", format="md"))
        out = buf.getvalue()
        self.assertIn("### Open", out)
        self.assertIn("| # | Task | Category | Effort | Activity |", out)

    def test_pipe_in_title_escaped(self):
        self._seed("Title with | pipe")
        out = ts._format_list_md()
        self.assertIn(r"Title with \| pipe", out)

    def test_live_marker_in_md(self):
        a = self._seed("Busy task")
        a.setdefault("sessions", []).extend(["m1", "m2"])
        ts.save_task(a)
        ts.set_link("m1", a["id"])
        ts.set_link("m2", a["id"])
        out = ts._format_list_md()
        self.assertIn("⧉2", out)


if __name__ == "__main__":
    unittest.main()
