"""task_intent() detection + cmd_prompt_context hard-directive wiring.

CRITICAL isolation: task-station.py freezes its store paths at IMPORT from
TASK_STATION_HOME (via paths.py). Set it to a tmpdir BEFORE importing so the
suite never touches the real ~/.claude/task-station-data store.
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

# Freeze the store under a throwaway dir BEFORE importing the module.
_TMP_HOME = tempfile.mkdtemp(prefix="ts-intent-")
os.environ["TASK_STATION_HOME"] = _TMP_HOME

import categories as cats  # noqa: E402

# task-station.py has a hyphen, so it can't be a normal import — load it by path.
_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


class _Args:
    def __init__(self, session=None, prompt=None):
        self.session = session
        self.prompt = prompt


class TaskIntentDetectTest(unittest.TestCase):
    def test_create_phrasings(self):
        for p in ["make this into a task", "create a task", "add a new task",
                  "track this", "save this as a task", "make it a task"]:
            self.assertEqual(cats.task_intent(p), "create", p)

    def test_attach_phrasings(self):
        for p in ["attach this to a task", "attach to task 215",
                  "add this to the existing task", "link it to a task"]:
            self.assertEqual(cats.task_intent(p), "attach", p)

    def test_none_phrasings(self):
        for p in ["what does create a task do?", "how do I attach to a task?",
                  "don't make a task", "reword this paragraph", "fix the bug",
                  "", "tasks are running slowly"]:
            self.assertIsNone(cats.task_intent(p), p)

    def test_meta_question_phrasings(self):
        # Questions ABOUT tracking must not false-positive as "create"/"attach"
        # even though they contain create/attach verbs.
        for p in ["did you open a new task-station for this inquiry yet?",
                  "have you made a task for this?",
                  "has anyone opened a new task for this?",
                  "is there a task open for this already?",
                  "were you going to create a task here?",
                  "haven't you started a new task for this?"]:
            self.assertIsNone(cats.task_intent(p), p)

    def test_create_attach_still_fire_around_meta_guard(self):
        self.assertEqual(cats.task_intent("open a new task for the auth bug"), "create")
        self.assertEqual(cats.task_intent("attach this to the existing task"), "attach")

    def test_attach_wins_over_create(self):
        # "add this to the existing task" contains "add … task" but the "to … task"
        # shape must classify it as attach, not create.
        self.assertEqual(cats.task_intent("add this to the existing task"), "attach")


class PromptContextDirectiveTest(unittest.TestCase):
    """For an explicit prompt the directive + the do-NOT-use TaskCreate warning
    must print in BOTH an unattached and an attached session."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        # Point the frozen module paths at this test's tmp store.
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")

    def tearDown(self):
        os.environ["TASK_STATION_HOME"] = _TMP_HOME
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, session, prompt):
        os.environ["TASK_STATION_PROMPT"] = prompt
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                ts.cmd_prompt_context(_Args(session=session, prompt=prompt))
        finally:
            os.environ.pop("TASK_STATION_PROMPT", None)
        return buf.getvalue()

    def test_directive_unattached(self):
        out = self._run("sess-unattached", "make this a task")
        self.assertIn("EXPLICIT TASK INTENT", out)
        self.assertIn("native task tools", out)   # steers away from native, toward task-station
        self.assertIn("create:", out)

    def test_directive_attached(self):
        task = ts.new_task("Existing work", "do the thing")
        ts.save_task(task)
        ts.set_link("sess-attached", task["id"])
        out = self._run("sess-attached", "make this a task")
        self.assertIn("EXPLICIT TASK INTENT", out)
        self.assertIn("native task tools", out)   # steers away from native, toward task-station
        # The already-attached note must mention --force for a separate task.
        self.assertIn("--force", out)

    def test_directive_fires_even_when_skipped(self):
        ts.set_link("sess-skip", ts.SKIP_SENTINEL)
        out = self._run("sess-skip", "make this a task")
        self.assertIn("EXPLICIT TASK INTENT", out)
        self.assertIn("native task tools", out)   # steers away from native, toward task-station

    def test_attach_directive_lists_open_tasks(self):
        task = ts.new_task("Open candidate", "summary")
        ts.save_task(task)
        out = self._run("sess-attach-intent", "attach this to a task")
        self.assertIn("EXPLICIT TASK INTENT", out)
        self.assertIn("attach:", out)
        self.assertIn("Open candidate", out)

    def test_no_directive_without_intent(self):
        out = self._run("sess-plain", "what does this code do?")
        self.assertNotIn("EXPLICIT TASK INTENT", out)


if __name__ == "__main__":
    unittest.main()
