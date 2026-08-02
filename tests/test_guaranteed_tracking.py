"""Workstream D — --guaranteed-tracking (opt-in, default OFF) hook-side
deterministic create+attach of a provisional task, fold-don't-fork dedup, and
auto-GC of untouched provisional tasks on skip/close.

CRITICAL isolation: task-station.py freezes its store paths at IMPORT from
TASK_STATION_HOME (via paths.py). Set it to a tmpdir BEFORE importing so the
suite never touches the real store. Mirrors tests/test_task_intent.py.
"""
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

_TMP_HOME = tempfile.mkdtemp(prefix="ts-gtrack-")
os.environ["TASK_STATION_HOME"] = _TMP_HOME

import categories as cats  # noqa: E402

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


class _Args:
    def __init__(self, session=None, prompt=None, task=None, title=None,
                 summary=None, append_summary=None, color=None, effort=None):
        self.session = session
        self.prompt = prompt
        self.task = task
        self.title = title
        self.summary = summary
        self.append_summary = append_summary
        self.color = color
        self.effort = effort


class GuaranteedTrackingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        # Point the frozen module paths at this test's tmp store.
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        import store
        store.reset_cache()

    def tearDown(self):
        os.environ["TASK_STATION_HOME"] = _TMP_HOME
        os.environ.pop("TASK_STATION_GUARANTEED_TRACKING", None)
        os.environ.pop("TASK_STATION_PROMPT", None)
        import store
        store.reset_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers --
    def _prompt(self, session, prompt, on=True):
        os.environ["TASK_STATION_GUARANTEED_TRACKING"] = "on" if on else "off"
        os.environ["TASK_STATION_PROMPT"] = prompt
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_prompt_context(_Args(session=session, prompt=prompt))
        return buf.getvalue()

    def _capture(self, fn, a):
        buf = io.StringIO()
        with redirect_stdout(buf):
            fn(a)
        return buf.getvalue()

    # -- OFF: no auto-create --
    def test_off_no_task_created(self):
        out = self._prompt("sess-off", "fix the broken login redirect", on=False)
        self.assertEqual(ts.all_tasks(), [])
        self.assertIsNone(ts.get_link("sess-off"))
        self.assertIn("not attached", out.lower())

    # -- ON: exactly one provisional OPEN task, session attached --
    def test_on_creates_one_provisional_open_task(self):
        out = self._prompt("sess-on", "fix the broken login redirect")
        tasks = ts.all_tasks()
        self.assertEqual(len(tasks), 1)
        t = tasks[0]
        self.assertTrue(t.get("provisional"))
        self.assertEqual(t.get("status"), ts.STATUS_OPEN)
        self.assertEqual(ts.get_link("sess-on"), t["id"])
        self.assertIn("Auto-tracked", out)

    # -- ON + similar open task: attach (fold), no sibling --
    def test_on_folds_into_similar_open_task(self):
        prompt = "fix the broken login redirect bug"
        seed = ts.seed_title(prompt)
        existing = ts.new_task(seed, "pre-existing", status=ts.STATUS_OPEN)
        existing["seq"] = 1
        ts.save_task(existing)
        out = self._prompt("sess-fold", prompt)
        tasks = ts.all_tasks()
        self.assertEqual(len(tasks), 1)                 # no sibling
        self.assertEqual(ts.get_link("sess-fold"), existing["id"])
        folded = ts.load_task(existing["id"])
        notes = " ".join(e.get("note", "") for e in folded.get("log", []))
        self.assertIn(prompt, notes)                    # prompt folded as a note
        self.assertIn("folded", out.lower())

    # -- ON + explicit create intent: directive path, no auto-create --
    def test_on_explicit_intent_does_not_double_create(self):
        out = self._prompt("sess-intent", "create a task for the deploy script")
        self.assertEqual(ts.all_tasks(), [])            # directive only — model acts
        self.assertIn("EXPLICIT TASK INTENT", out)

    # -- skip on a still-provisional auto-task GCs it --
    def test_skip_gcs_provisional(self):
        self._prompt("sess-skip", "investigate the flaky timer test")
        tid = ts.get_link("sess-skip")
        self.assertIsNotNone(ts.load_task(tid))
        out = self._capture(ts.cmd_skip, _Args(session="sess-skip"))
        self.assertIsNone(ts.load_task(tid))            # gone from the store
        self.assertEqual(ts.all_tasks(), [])
        self.assertIn("Removed", out)

    # -- done/close on a still-provisional auto-task GCs it (not left closed) --
    def test_done_gcs_provisional(self):
        self._prompt("sess-done", "ask about the cache eviction policy")
        tid = ts.get_link("sess-done")
        out = self._capture(ts.cmd_done, _Args(session="sess-done"))
        self.assertIsNone(ts.load_task(tid))            # GC'd, not a closed husk
        self.assertEqual(ts.all_tasks(), [])
        self.assertIn("Discarded provisional", out)

    # -- update --title clears provisional: done then CLOSES normally; skip keeps --
    def test_update_clears_provisional_then_done_closes(self):
        self._prompt("sess-real", "scaffold the new billing module")
        tid = ts.get_link("sess-real")
        seq = ts.load_task(tid).get("seq")
        ts.cmd_update(_Args(task=str(seq), title="Billing module rewrite"))
        self.assertFalse(ts.load_task(tid).get("provisional"))
        self._capture(ts.cmd_done, _Args(session="sess-real"))
        closed = ts.load_task(tid)
        self.assertIsNotNone(closed)                    # retained, not GC'd
        self.assertEqual(closed.get("status"), ts.STATUS_CLOSED)

    def test_update_clears_provisional_then_skip_keeps(self):
        self._prompt("sess-real2", "design the retry/backoff strategy")
        tid = ts.get_link("sess-real2")
        seq = ts.load_task(tid).get("seq")
        ts.cmd_update(_Args(task=str(seq), title="Retry/backoff strategy"))
        self._capture(ts.cmd_skip, _Args(session="sess-real2"))
        kept = ts.load_task(tid)
        self.assertIsNotNone(kept)                      # not deleted
        self.assertNotEqual(kept.get("status"), ts.STATUS_CLOSED)


if __name__ == "__main__":
    unittest.main()
