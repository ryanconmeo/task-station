"""cmd_prompt_context nudge collapse: first miss = full block (templates + legend),
intermediate misses = a single compact line (no open-task list / no attach-create
syntax), escalation = compact (open-task list + ⚠ warning + skip command +
guidance pointer — NO templates, NO legend)."""
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


class NudgeCollapseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        # An open, unattached task so the full block has an "Open tasks" section.
        ts.save_task(ts.new_task("Some open task", "do it"))
        ts.ensure_seqs()

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        os.environ.pop("TASK_STATION_PROMPT", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, session, prompt=""):
        os.environ["TASK_STATION_PROMPT"] = prompt
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_prompt_context(_Args(session=session))
        return buf.getvalue()

    def test_first_miss_is_full_block(self):
        out = self._run("s")  # n == 1
        self.assertIn("not attached to a tracked task yet", out)
        self.assertIn("Open tasks that may match", out)
        self.assertIn("attach --session", out)
        self.assertIn("create --session", out)
        self.assertNotIn("Still untracked", out)

    def test_intermediate_is_single_compact_line(self):
        self._run("s")             # n == 1 (full)
        out2 = self._run("s")      # n == 2 (compact)
        self.assertIn("Still untracked (msg 2)", out2)
        # The expensive parts of the full block must be ABSENT.
        self.assertNotIn("Open tasks that may match", out2)
        self.assertNotIn("attach --session", out2)
        self.assertNotIn("create --session", out2)
        self.assertNotIn("Colors:", out2)
        # It really is a single line.
        self.assertEqual(len([l for l in out2.splitlines() if l.strip()]), 1)
        out3 = self._run("s")      # n == 3 (still compact)
        self.assertIn("Still untracked (msg 3)", out3)
        self.assertNotIn("attach --session", out3)

    def test_intermediate_compact_line_carries_detected_category(self):
        # Category detection stays on for EVERY prompt (item 6): a `/review`
        # invocation maps to a category, so the compact line carries the one
        # detected-category hint — but still no heavy legend, still one line.
        if ts.cats is None or not hasattr(ts.cats, "color_for_prompt"):
            self.skipTest("categories plugin not available")
        color = ts.cats.color_for_prompt("/review")
        if not color:
            self.skipTest("/review not mapped to a category in this config")
        self._run("s")                       # n == 1 (full)
        out = self._run("s", prompt="/review")  # n == 2 (compact)
        self.assertIn("Still untracked (msg 2)", out)
        self.assertIn("--color %s" % color, out)
        self.assertIn("category '%s'" % color, out)
        self.assertNotIn("Legend:", out)
        self.assertNotIn("Colors:", out)
        self.assertEqual(len([l for l in out.splitlines() if l.strip()]), 1)

    def test_escalation_is_compact(self):
        """n >= NUDGE_ESCALATE_AFTER: open-task list + ⚠ warning + skip command +
        guidance pointer.  The heavy templates+legend are absent (token-saving
        guarantee)."""
        for _ in range(ts.NUDGE_ESCALATE_AFTER - 1):
            self._run("s")         # n == 1 .. NUDGE_ESCALATE_AFTER-1
        out = self._run("s")       # n == NUDGE_ESCALATE_AFTER
        # Core escalation signals.
        self.assertIn("messages in and still untracked", out)
        self.assertIn("skip --session", out)
        self.assertIn("guidance", out)           # one-liner pointer to full syntax
        # Open-task list still present (the "attach to one of these" candidates).
        self.assertIn("Open tasks that may match", out)
        # The compact line format is ABSENT (escalation is its own format).
        self.assertNotIn("Still untracked (msg", out)
        # Command templates and colour legend MUST be absent — that's the token saving.
        self.assertNotIn("attach: task-station", out)
        self.assertNotIn("create: task-station", out)
        self.assertNotIn("Colors:", out)

    def test_escalation_stays_compact_on_subsequent_turns(self):
        """Every turn past the threshold stays compact — no re-dump each time."""
        for _ in range(ts.NUDGE_ESCALATE_AFTER + 2):
            self._run("s")
        out = self._run("s")       # well past the threshold
        self.assertIn("messages in and still untracked", out)
        self.assertNotIn("attach: task-station", out)
        self.assertNotIn("create: task-station", out)
        self.assertNotIn("Colors:", out)

    def test_first_miss_has_templates_and_legend(self):
        """n == 1: the full education block must include the command templates.
        Colors: legend is checked separately only when the categories plugin is
        available and reports a non-empty compact_legend."""
        out = self._run("s")  # n == 1
        self.assertIn("attach: task-station", out)
        self.assertIn("create: task-station", out)
        if (ts.cats is not None and hasattr(ts.cats, "compact_legend")
                and ts.cats.compact_legend()):
            self.assertIn("Colors:", out)


if __name__ == "__main__":
    unittest.main()
