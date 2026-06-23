"""`guidance` now prints the full command reference (1.12.0) — the model-facing
source of truth for the command set, in ADDITION to the existing track/attach/skip
nudge. These assert both halves are present in the default (no-arg) output."""
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
    pass


class Guidance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.PROJECTS_ROOT = os.path.join(self.tmp, "projects")
        store.reset_cache()
        importlib.reload(categories)   # clean config — TINT_TERMINAL True (other modules mutate it)
        self._saved_prompt = os.environ.get("TASK_STATION_PROMPT")
        os.environ.pop("TASK_STATION_PROMPT", None)

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        if self._saved_prompt is None:
            os.environ.pop("TASK_STATION_PROMPT", None)
        else:
            os.environ["TASK_STATION_PROMPT"] = self._saved_prompt
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_guidance(_Args())
        return buf.getvalue()

    def test_reference_section_present(self):
        out = self._run()
        # The compact command reference lists every subcommand by name.
        self.assertIn("Commands", out)
        for cmd in ("create", "attach", "detach", "update", "status",
                    "pin", "unpin", "done", "skip", "whoami", "render",
                    "bump", "config", "repos"):
            self.assertIn(cmd, out)

    def test_lifecycle_and_ref_forms_documented(self):
        out = self._run()
        for glyph in ("○", "●", "✕"):
            self.assertIn(glyph, out)
        self.assertIn("seq number or id-prefix", out)
        self.assertIn("session uuid", out)

    def test_delete_documented_under_maintenance(self):
        out = self._run()
        self.assertIn("Maintenance", out)
        self.assertIn("delete", out)
        self.assertIn("HARD-delete", out)

    def test_original_nudge_content_preserved(self):
        """The reference is ADDITIONAL — the track/fold-don't-fork nudge stays."""
        out = self._run()
        self.assertIn("Every topic gets tracked", out)
        self.assertIn("FOLD, DON'T FORK", out)
        self.assertIn("skip", out)


if __name__ == "__main__":
    unittest.main()
