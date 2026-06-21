"""Task lifecycle as ONE field — `status` with three values: open (○) → active
(●) → closed. Covers the default, `create --active`, glyph rendering in every
view, auto-promotion (manual `status` cmd, PostToolUse edit, idempotence, no
closed-resurrection), done→closed / reopen→open, `attach --note`, and back-compat
for tasks written with the old open/closed-only field."""
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


class StatusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        os.environ.pop("TASK_STATION_GATE", None)
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self, title, **kw):
        t = ts.new_task(title, "summary for " + title, **kw)
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    # ----------------------------------------------------------- field + default
    def test_default_status_is_open(self):
        t = ts.new_task("A topic", "raised")
        self.assertEqual(t["status"], "open")
        self.assertEqual(ts.task_status(t), "open")
        self.assertTrue(ts.is_on_board(t))
        self.assertFalse(ts.is_closed(t))

    def test_new_task_active_status(self):
        t = ts.new_task("Working on it", "", status="active")
        self.assertEqual(t["status"], "active")
        self.assertTrue(ts.is_on_board(t))

    def test_new_task_rejects_non_board_status(self):
        # new_task only ever starts on the board; a bogus/closed value → open.
        self.assertEqual(ts.new_task("x", "", status="closed")["status"], "open")
        self.assertEqual(ts.new_task("y", "", status="bogus")["status"], "open")

    # --------------------------------------------------------------- back-compat
    def test_missing_status_reads_as_open(self):
        t = self._seed("Legacy task")
        del t["status"]
        ts.save_task(t)
        reloaded = ts.load_task(t["id"])
        self.assertNotIn("status", reloaded)
        self.assertEqual(ts.task_status(reloaded), "open")
        self.assertEqual(ts.status_glyph(reloaded), "○")

    def test_legacy_open_and_closed_still_read(self):
        o = self._seed("legacy open")           # status open
        c = self._seed("legacy closed"); c["status"] = "closed"; ts.save_task(c)
        self.assertTrue(ts.is_on_board(ts.load_task(o["id"])))
        self.assertTrue(ts.is_closed(ts.load_task(c["id"])))

    # -------------------------------------------------------------- create flags
    def test_cmd_create_default_open(self):
        with redirect_stdout(io.StringIO()):
            ts.cmd_create(_Args(session="s1", title="Look into X", summary="",
                                color=None, effort=None, force=True,
                                no_attach=False, attach=False, active=False))
        self.assertEqual(ts.task_status(ts.sorted_tasks()[0]), "open")

    def test_cmd_create_active_flag(self):
        with redirect_stdout(io.StringIO()):
            ts.cmd_create(_Args(session="s2", title="Build Y", summary="",
                                color=None, effort=None, force=True,
                                no_attach=False, attach=False, active=True))
        self.assertEqual(ts.task_status(ts.sorted_tasks()[0]), "active")

    # ------------------------------------------------------------ glyph: helpers
    def test_status_glyph_board(self):
        self.assertEqual(ts.status_glyph(self._seed("q")), "○")
        self.assertEqual(ts.status_glyph(self._seed("d", status="active")), "●")

    def test_status_glyph_closed_muted(self):
        t = self._seed("Done thing", status="active")
        t["status"] = "closed"; ts.save_task(t)
        self.assertEqual(ts.status_glyph(ts.load_task(t["id"])), " ")

    # ------------------------------------------------------------- glyph: ASCII
    def test_glyph_in_ascii_list(self):
        self._seed("Open one")
        self._seed("Active one", status="active")
        out = ts._format_list()
        self.assertIn("○", out)
        self.assertIn("●", out)
        self.assertIn("○ open · ● active", out)
        for line in out.splitlines():
            if "Open one" in line:
                self.assertTrue(line.lstrip().startswith("○"))
            if "Active one" in line:
                self.assertTrue(line.lstrip().startswith("●"))

    def test_ascii_open_and_active_share_board_section(self):
        # Both not-closed tasks sit under a single OPEN section, before CLOSED.
        self._seed("board open")
        self._seed("board active", status="active")
        c = self._seed("done one"); c["status"] = "closed"; ts.save_task(c)
        out = ts._format_list()
        self.assertEqual(out.count("\nOPEN"), 1)
        self.assertIn("\nCLOSED", out)
        self.assertLess(out.index("OPEN"), out.index("CLOSED"))
        # The active row appears within the board section (before CLOSED).
        self.assertLess(out.index("board active"), out.index("CLOSED"))

    # --------------------------------------------------------------- glyph: md
    def test_glyph_in_markdown(self):
        o = self._seed("MD open")
        a = self._seed("MD active", status="active")
        out = ts._format_list_md()
        # The glyph lives in the leading STATUS column; the `#` cell is bare seq.
        self.assertIn("| ○ | %d | MD open" % o["seq"], out)
        self.assertIn("| ● | %d | MD active" % a["seq"], out)
        self.assertIn("_● active · ○ open · (closed below)_", out)

    def test_md_active_in_open_section(self):
        self._seed("MD active2", status="active")
        out = ts._format_list_md()
        self.assertIn("### Open", out)   # board section holds active tasks too

    # ------------------------------------------------------------ glyph: detail
    def test_glyph_in_detail(self):
        o = self._seed("Detail open")
        a = self._seed("Detail active", status="active")
        self.assertIn("○ OPEN", ts._format_detail(o, "sx"))
        self.assertIn("● ACTIVE", ts._format_detail(a, "sx"))

    def test_detail_closed_has_no_glyph(self):
        c = self._seed("Detail closed"); c["status"] = "closed"; ts.save_task(c)
        det = ts._format_detail(ts.load_task(c["id"]), "sx")
        self.assertIn("CLOSED", det)
        self.assertNotIn("○ CLOSED", det)
        self.assertNotIn("● CLOSED", det)

    # -------------------------------------------------------- auto-promote: edit
    def test_post_tool_edit_promotes_attached_open(self):
        t = self._seed("Edited task")
        ts.set_link("sess-edit", t["id"])
        self.assertEqual(ts.task_status(ts.load_task(t["id"])), "open")
        with redirect_stdout(io.StringIO()):
            ts.cmd_mark_edited(_Args(session="sess-edit"))
        self.assertEqual(ts.task_status(ts.load_task(t["id"])), "active")

    def test_post_tool_edit_promote_idempotent(self):
        t = self._seed("Already active", status="active")
        ts.set_link("sess-2", t["id"])
        log_before = len(ts.load_task(t["id"]).get("log", []))
        with redirect_stdout(io.StringIO()):
            ts.cmd_mark_edited(_Args(session="sess-2"))
            ts.cmd_mark_edited(_Args(session="sess-2"))
        after = ts.load_task(t["id"])
        self.assertEqual(ts.task_status(after), "active")
        self.assertEqual(len(after.get("log", [])), log_before)

    def test_post_tool_edit_does_not_resurrect_closed(self):
        t = self._seed("Closed task"); t["status"] = "closed"; ts.save_task(t)
        ts.set_link("sess-3", t["id"])   # defensive: a stale link to a closed task
        with redirect_stdout(io.StringIO()):
            ts.cmd_mark_edited(_Args(session="sess-3"))
        self.assertEqual(ts.task_status(ts.load_task(t["id"])), "closed")

    def test_post_tool_edit_unattached_does_not_create_or_promote(self):
        before = len(ts.all_tasks())
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_mark_edited(_Args(session="lonely"))
        self.assertEqual(len(ts.all_tasks()), before)
        self.assertIn("NOT tracking a task", buf.getvalue())

    def test_post_tool_edit_skipped_session_silent(self):
        ts.set_link("skip-sess", ts.SKIP_SENTINEL)
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_mark_edited(_Args(session="skip-sess"))
        self.assertEqual(buf.getvalue().strip(), "")

    # ------------------------------------------------------------ status command
    def test_status_command_sets_active(self):
        t = self._seed("Toggle me")
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_status(_Args(task=str(t["seq"]), value="active"))
        self.assertEqual(ts.task_status(ts.load_task(t["id"])), "active")
        self.assertIn("● active", buf.getvalue())

    def test_status_command_back_to_open(self):
        t = self._seed("Demote me", status="active")
        with redirect_stdout(io.StringIO()):
            ts.cmd_status(_Args(task=str(t["seq"]), value="open"))
        self.assertEqual(ts.task_status(ts.load_task(t["id"])), "open")

    def test_status_command_reports_when_no_value(self):
        t = self._seed("Just report")
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_status(_Args(task=str(t["seq"]), value=None))
        self.assertIn("status: ○ open", buf.getvalue())
        self.assertEqual(ts.task_status(ts.load_task(t["id"])), "open")

    def test_status_command_closed_value_points_to_done(self):
        t = self._seed("Try close via status")
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_status(_Args(task=str(t["seq"]), value="closed"))
        self.assertIn("/done", buf.getvalue())
        self.assertEqual(ts.task_status(ts.load_task(t["id"])), "open")

    def test_status_command_unknown_value(self):
        t = self._seed("Bad value")
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_status(_Args(task=str(t["seq"]), value="nonsense"))
        self.assertIn("unknown status", buf.getvalue())
        self.assertEqual(ts.task_status(ts.load_task(t["id"])), "open")

    def test_status_command_on_closed_task_refuses(self):
        t = self._seed("Closed one"); t["status"] = "closed"; ts.save_task(t)
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_status(_Args(task=str(t["seq"]), value="active"))
        self.assertIn("closed", buf.getvalue().lower())
        self.assertEqual(ts.task_status(ts.load_task(t["id"])), "closed")

    # ------------------------------------------------------------ done / reopen
    def test_done_closes_from_active(self):
        t = self._seed("Active to close", status="active")
        ts.set_link("done-sess", t["id"])
        with redirect_stdout(io.StringIO()):
            ts.cmd_done(_Args(session="done-sess", task=None))
        self.assertEqual(ts.task_status(ts.load_task(t["id"])), "closed")

    def test_reopen_closed_goes_to_open(self):
        t = self._seed("Reopen me", status="active")
        t["status"] = "closed"; ts.save_task(t)
        with redirect_stdout(io.StringIO()):
            ts.cmd_attach(_Args(session="re-sess", task=str(t["seq"]),
                                color=None, note=None))
        # Reopening a closed task resets it to open (not back to active).
        self.assertEqual(ts.task_status(ts.load_task(t["id"])), "open")

    # ------------------------------------------------------------ attach --note
    def test_attach_note_appends_to_log(self):
        t = self._seed("Folding target")
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_attach(_Args(session="folder", task=str(t["seq"]),
                                color=None, note="a follow-up question about X"))
        notes = [e.get("note", "") for e in ts.load_task(t["id"]).get("log", [])]
        self.assertTrue(any("a follow-up question about X" in n for n in notes))
        self.assertIn("note appended", buf.getvalue())

    def test_attach_without_note_still_works(self):
        t = self._seed("Plain attach")
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_attach(_Args(session="plain", task=str(t["seq"]),
                                color=None, note=None))
        self.assertEqual(ts.get_link("plain"), t["id"])
        self.assertNotIn("note appended", buf.getvalue())

    # -------------------------------------------------------------- helper logic
    def test_set_status_idempotent_and_range(self):
        t = self._seed("Idem", status="active")
        self.assertFalse(ts.set_status(t, "active"))     # no-op
        self.assertTrue(ts.set_status(t, "open"))        # changed
        self.assertFalse(ts.set_status(t, "closed"))     # closing not settable here

    def test_promote_active_only_from_open(self):
        opn = self._seed("o")
        act = self._seed("a", status="active")
        clo = self._seed("c"); clo["status"] = "closed"
        self.assertTrue(ts.promote_active(opn))          # open → active
        self.assertFalse(ts.promote_active(act))         # already active
        self.assertFalse(ts.promote_active(clo))         # closed stays closed
        self.assertEqual(clo["status"], "closed")


if __name__ == "__main__":
    unittest.main()
