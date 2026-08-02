"""seq visibility — the human-facing #<seq> must appear everywhere task-station
injects the board into an agent's context, and a spoken/typed reference like
"find task 387" must resolve deterministically instead of forcing the model to
guess/scan the injected board text."""
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


class SeqVisibility(unittest.TestCase):
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

    # ------------------------------------------------- CHANGE 1: board visibility
    def test_session_start_shows_seq_for_open_task(self):
        task = self._seed("Rework the ingest pipeline")
        seq = task["seq"]
        out = self._out(ts.cmd_session_start, _Args(session="fresh-session", source="startup"))
        self.assertIn("#%s" % seq, out)
        self.assertIn(task["id"][:8], out)

    # ------------------------------------------------- CHANGE 2: deterministic resolver
    def test_prompt_context_resolves_task_reference_by_seq(self):
        task = self._seed("Migrate the billing job")
        seq = task["seq"]
        os.environ["TASK_STATION_PROMPT"] = "can you find task %s and check on it" % seq
        out = self._out(ts.cmd_prompt_context, _Args(session="ref-session"))
        self.assertIn(task["id"][:8], out)
        self.assertIn(task["title"], out)
        self.assertIn("search --detail %s" % seq, out)

    def test_resolver_silent_when_no_task_number_in_prompt(self):
        self._seed("Some other task")
        os.environ["TASK_STATION_PROMPT"] = "just talking about widgets, no task refs here"
        out = self._out(ts.cmd_prompt_context, _Args(session="silent-session"))
        self.assertNotIn("references an existing task", out)

    def test_resolver_silent_when_number_matches_no_task(self):
        os.environ["TASK_STATION_PROMPT"] = "find task 999999"
        out = self._out(ts.cmd_prompt_context, _Args(session="silent-session-2"))
        self.assertNotIn("references an existing task", out)


if __name__ == "__main__":
    unittest.main()
