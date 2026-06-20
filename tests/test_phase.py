"""Task lifecycle PHASE — inquiry (◦, default) → active (●), independent of the
open/closed status. Covers the default, `create --active`, glyph rendering in
every view, auto-promotion (manual, PostToolUse edit, idempotence), the `phase`
command, `attach --note`, and back-compat for tasks written before phases."""
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


class PhaseTest(unittest.TestCase):
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
    def test_default_phase_is_inquiry(self):
        t = ts.new_task("A topic", "raised")
        self.assertEqual(t["phase"], "inquiry")
        self.assertEqual(ts.task_phase(t), "inquiry")

    def test_new_task_active_phase(self):
        t = ts.new_task("Working on it", "", phase="active")
        self.assertEqual(t["phase"], "active")

    def test_unknown_phase_falls_back_to_inquiry(self):
        t = ts.new_task("Bad phase", "", phase="bogus")
        self.assertEqual(t["phase"], "inquiry")

    # --------------------------------------------------------------- back-compat
    def test_missing_phase_reads_as_inquiry(self):
        t = self._seed("Legacy task")
        # Simulate a task written before phases existed.
        del t["phase"]
        ts.save_task(t)
        reloaded = ts.load_task(t["id"])
        self.assertNotIn("phase", reloaded)
        self.assertEqual(ts.task_phase(reloaded), "inquiry")
        self.assertEqual(ts.phase_glyph(reloaded), "◦")

    # -------------------------------------------------------------- create flags
    def test_cmd_create_default_inquiry(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_create(_Args(session="s1", title="Look into X", summary="",
                                color=None, effort=None, force=True,
                                no_attach=False, attach=False, active=False))
        t = ts.sorted_tasks()[0]
        self.assertEqual(ts.task_phase(t), "inquiry")

    def test_cmd_create_active_flag(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_create(_Args(session="s2", title="Build Y", summary="",
                                color=None, effort=None, force=True,
                                no_attach=False, attach=False, active=True))
        t = ts.sorted_tasks()[0]
        self.assertEqual(ts.task_phase(t), "active")

    # ------------------------------------------------------------ glyph: helpers
    def test_phase_glyph_open(self):
        inq = self._seed("Question")
        act = self._seed("Doing", phase="active")
        self.assertEqual(ts.phase_glyph(inq), "◦")
        self.assertEqual(ts.phase_glyph(act), "●")

    def test_phase_glyph_closed_muted(self):
        t = self._seed("Done thing", phase="active")
        t["status"] = "closed"
        ts.save_task(t)
        self.assertEqual(ts.phase_glyph(ts.load_task(t["id"])), " ")

    # ------------------------------------------------------------- glyph: ASCII
    def test_glyph_in_ascii_list(self):
        self._seed("Inquiry one")
        self._seed("Active one", phase="active")
        out = ts._format_list()
        # Both glyphs appear, plus the legend.
        self.assertIn("◦", out)
        self.assertIn("●", out)
        self.assertIn("◦ inquiry · ● active", out)
        # Leading glyph sits before the seq number on each row.
        for line in out.splitlines():
            if "Inquiry one" in line:
                self.assertTrue(line.lstrip().startswith("◦"))
            if "Active one" in line:
                self.assertTrue(line.lstrip().startswith("●"))

    # --------------------------------------------------------------- glyph: md
    def test_glyph_in_markdown(self):
        inq = self._seed("MD inquiry")
        act = self._seed("MD active", phase="active")
        out = ts._format_list_md()
        self.assertIn("| ◦ %d | MD inquiry" % inq["seq"], out)
        self.assertIn("| ● %d | MD active" % act["seq"], out)
        self.assertIn("_◦ inquiry · ● active_", out)

    # ------------------------------------------------------------ glyph: detail
    def test_glyph_in_detail(self):
        inq = self._seed("Detail inquiry")
        act = self._seed("Detail active", phase="active")
        self.assertIn("Phase:   ◦ inquiry", ts._format_detail(inq, "sx"))
        self.assertIn("Phase:   ● active", ts._format_detail(act, "sx"))

    # -------------------------------------------------------- auto-promote: edit
    def test_post_tool_edit_promotes_attached_inquiry(self):
        t = self._seed("Edited task")
        ts.set_link("sess-edit", t["id"])
        self.assertEqual(ts.task_phase(ts.load_task(t["id"])), "inquiry")
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_mark_edited(_Args(session="sess-edit"))
        self.assertEqual(ts.task_phase(ts.load_task(t["id"])), "active")

    def test_post_tool_edit_promote_idempotent(self):
        t = self._seed("Already active", phase="active")
        ts.set_link("sess-2", t["id"])
        log_before = len(ts.load_task(t["id"]).get("log", []))
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_mark_edited(_Args(session="sess-2"))
            ts.cmd_mark_edited(_Args(session="sess-2"))
        after = ts.load_task(t["id"])
        self.assertEqual(ts.task_phase(after), "active")
        # No spurious phase-transition log entries when already active.
        self.assertEqual(len(after.get("log", [])), log_before)

    def test_post_tool_edit_unattached_does_not_create_or_promote(self):
        before = len(ts.all_tasks())
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_mark_edited(_Args(session="lonely"))
        # No task created; the untracked-edit nudge fires instead.
        self.assertEqual(len(ts.all_tasks()), before)
        self.assertIn("NOT tracking a task", buf.getvalue())

    def test_post_tool_edit_skipped_session_silent(self):
        ts.set_link("skip-sess", ts.SKIP_SENTINEL)
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_mark_edited(_Args(session="skip-sess"))
        self.assertEqual(buf.getvalue().strip(), "")

    # ------------------------------------------------------- phase command
    def test_phase_command_sets_active(self):
        t = self._seed("Toggle me")
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_phase(_Args(task=str(t["seq"]), value="active"))
        self.assertEqual(ts.task_phase(ts.load_task(t["id"])), "active")
        self.assertIn("● active", buf.getvalue())

    def test_phase_command_back_to_inquiry(self):
        t = self._seed("Demote me", phase="active")
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_phase(_Args(task=str(t["seq"]), value="inquiry"))
        self.assertEqual(ts.task_phase(ts.load_task(t["id"])), "inquiry")

    def test_phase_command_reports_when_no_value(self):
        t = self._seed("Just report")
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_phase(_Args(task=str(t["seq"]), value=None))
        out = buf.getvalue()
        self.assertIn("phase: ◦ inquiry", out)
        # Reporting does not mutate.
        self.assertEqual(ts.task_phase(ts.load_task(t["id"])), "inquiry")

    def test_phase_command_unknown_value(self):
        t = self._seed("Bad value")
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_phase(_Args(task=str(t["seq"]), value="nonsense"))
        self.assertIn("unknown phase", buf.getvalue())
        self.assertEqual(ts.task_phase(ts.load_task(t["id"])), "inquiry")

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

    # ------------------------------------------------------- status independence
    def test_phase_does_not_touch_status(self):
        t = self._seed("Status check")
        self.assertEqual(t["status"], "open")
        ts.promote_active(t)
        ts.save_task(t)
        reloaded = ts.load_task(t["id"])
        self.assertEqual(reloaded["status"], "open")   # phase change left status alone
        self.assertEqual(ts.task_phase(reloaded), "active")

    def test_set_phase_idempotent_returns_false(self):
        t = self._seed("Idem", phase="active")
        self.assertFalse(ts.set_phase(t, "active"))
        self.assertTrue(ts.set_phase(t, "inquiry"))


if __name__ == "__main__":
    unittest.main()
