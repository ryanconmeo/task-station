"""1.41.0 — the "📋 Tracking" line must come from the TOOL, never be self-authored.

create/attach prefix their own success confirmations with "📋 " (so the friendly
tracking line IS the command's stdout), and the per-prompt nudge + `guidance` now
tell Claude to RUN create/attach and RELAY the tool's result line — not to author a
"📋 Tracking: <title>" line itself (which could display "Tracking" while the board
records nothing). Existing substring tests ("Created task"/"Attached to task"/⚠/
duplicate lines) must still pass — these assert the substrings survive the prefix."""
import importlib
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

import store        # noqa: E402
import categories   # noqa: E402

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class TrackingConfirmation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        os.environ.pop("TASK_STATION_GATE", None)
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.PROJECTS_ROOT = os.path.join(self.tmp, "projects")
        store.reset_cache()
        importlib.reload(categories)
        self._saved_prompt = os.environ.get("TASK_STATION_PROMPT")

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        if self._saved_prompt is None:
            os.environ.pop("TASK_STATION_PROMPT", None)
        else:
            os.environ["TASK_STATION_PROMPT"] = self._saved_prompt
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self, title, **kw):
        t = ts.new_task(title, "summary for " + title, **kw)
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _out(self, fn, args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            fn(args)
        return buf.getvalue()

    # ---------------------------------------------------- create success → 📋
    def test_create_attached_confirmation_carries_badge(self):
        out = self._out(ts.cmd_create,
                        _Args(session="s-attach", title="Wire the board", summary="x",
                              color=None, effort=None, force=True,
                              no_attach=False, attach=True, active=False))
        self.assertIn("📋", out)
        self.assertIn("Created and attached to task", out)   # substring preserved
        # the badge precedes the wording on the success line
        self.assertIn("📋 Created and attached to task", out)

    def test_create_unattached_confirmation_carries_badge(self):
        out = self._out(ts.cmd_create,
                        _Args(session=None, title="Later thing", summary="x",
                              color=None, effort=None, force=True,
                              no_attach=True, attach=False, active=False))
        self.assertIn("📋 Created task", out)                 # substring preserved + badge
        self.assertIn("(unattached)", out)

    # ---------------------------------------------------- attach success → 📋
    def test_attach_confirmation_carries_badge(self):
        t = self._seed("Folding target")
        out = self._out(ts.cmd_attach,
                        _Args(session="s-att", task=str(t["seq"]), color=None, note=None))
        self.assertIn("📋 Attached to task", out)             # substring preserved + badge

    # ------------------------------------- warning/duplicate lines stay unbadged
    def test_duplicate_warning_not_badged(self):
        self._seed("Build the parser")
        out = self._out(ts.cmd_create,
                        _Args(session="dup", title="Build the parser", summary="x",
                              color=None, effort=None, force=False,
                              no_attach=False, attach=False, active=False))
        self.assertIn("Not created — likely a duplicate", out)
        self.assertNotIn("📋", out)                            # a refusal is not a tracking line

    # --------------------------------------- nudge relays, never self-authors
    def test_nudge_says_relay_not_self_author(self):
        os.environ["TASK_STATION_PROMPT"] = "tell me about widgets"
        out = self._out(ts.cmd_prompt_context, _Args(session="fresh-nudge"))
        # full block on the first miss
        self.assertIn("not attached to a tracked task yet", out)
        # NEW relay/never-fabricate wording present...
        self.assertIn("relay", out)
        self.assertIn("result line", out)
        self.assertIn("RUNNING the create/attach command", out)
        # ...and the OLD "tell the user to print 📋 Tracking: <title>" phrasing is gone.
        self.assertNotIn('Tell the user in one short line ("📋 Tracking:', out)

    # ----------------------------------- guidance relays, never self-authors
    def test_guidance_says_relay_not_self_author(self):
        out = self._out(ts.cmd_guidance, _Args())
        self.assertIn("tool's OWN result line", out)
        self.assertIn("NEVER fabricate", out)
        # old self-authoring instruction removed
        self.assertNotIn("Tracking this as a new task", out)


if __name__ == "__main__":
    unittest.main()
