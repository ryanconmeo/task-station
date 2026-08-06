"""`task-station brief path` — the model-authored brief flow.

The skill writes its own HTML now, so it needs the engine to hand it the output path,
create the directory, and record `brief_path` on the task — without reading a spec.
`brief render` keeps its own behaviour (tests/test_brief_cmd.py)."""
import importlib.util
import io
import json
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
import store            # noqa: E402
import obsidian_sync    # noqa: E402


class _Args:
    def __init__(self, **kw):
        self.__dict__.setdefault("action", "render")
        self.__dict__.setdefault("task", None)
        self.__dict__.setdefault("session", None)
        self.__dict__.setdefault("spec", None)
        self.__dict__.update(kw)


class _Exploding(io.StringIO):
    """stdin that fails the test loudly if `brief path` ever reads it."""

    def read(self, *a, **kw):
        raise AssertionError("brief path must not read stdin")


class BriefPathActionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        os.environ.pop("TASK_STATION_ARTIFACTS_ROOT", None)
        for k in ("TASK_STATION_STREAM", "TASK_STATION_STREAM_DIR"):
            os.environ.pop(k, None)
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        store.reset_cache()

    def tearDown(self):
        store.reset_cache()
        for k in ("TASK_STATION_HOME", "TASK_STATION_ARTIFACTS_ROOT",
                  "TASK_STATION_STREAM", "TASK_STATION_STREAM_DIR"):
            os.environ.pop(k, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _task(self):
        t = ts.new_task("Test Data Split", "summary", color="green")
        ts.save_task(t)
        ts.ensure_seqs()
        t = ts.load_task(t["id"])
        ts.add_glossary_term(t, "fixture", "test", "target", "a disposable row set")
        ts.save_task(t)
        return ts.load_task(t["id"])

    def _brief(self, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_brief(_Args(**kw))
        return buf.getvalue()

    # ---------------------------------------------------------------- the action ---

    def test_prints_the_derived_output_path(self):
        t = self._task()
        out = self._brief(action="path", task=str(t["seq"])).strip()
        self.assertEqual(out, ts.brief_output_path(t))

    def test_creates_the_parent_dir_but_not_the_file(self):
        t = self._task()
        out = self._brief(action="path", task=str(t["seq"])).strip()
        self.assertTrue(os.path.isdir(os.path.dirname(out)),
                        "brief path must makedirs the artifact dir")
        # it reserves the path; the model writes the HTML itself
        self.assertFalse(os.path.exists(out))

    def test_records_brief_path_on_the_task(self):
        t = self._task()
        out = self._brief(action="path", task=str(t["seq"])).strip()
        self.assertEqual(ts.load_task(t["id"]).get("brief_path"), out)

    def test_needs_no_spec_and_never_reads_stdin(self):
        t = self._task()
        old = sys.stdin
        sys.stdin = _Exploding()
        try:
            out = self._brief(action="path", task=str(t["seq"])).strip()
        finally:
            sys.stdin = old
        self.assertEqual(out, ts.brief_output_path(t))

    def test_is_idempotent(self):
        t = self._task()
        first = self._brief(action="path", task=str(t["seq"])).strip()
        second = self._brief(action="path", task=str(t["seq"])).strip()
        self.assertEqual(first, second)
        self.assertEqual(ts.load_task(t["id"]).get("brief_path"), second)

    def test_brief_path_carried_into_contract_v2_frontmatter(self):
        t = self._task()
        out = self._brief(action="path", task=str(t["seq"])).strip()
        note = obsidian_sync.render_note(ts.load_task(t["id"]))
        self.assertIn("brief_path:", note)
        self.assertIn(out, note)

    def test_no_task_reports(self):
        out = self._brief(action="path", session="ghost")
        self.assertIn("no task", out.lower())
        self.assertNotIn("spec", out.lower())

    # -------------------------------------------------------------- back-compat ---

    def test_render_is_still_the_default_action(self):
        """A bare `brief` (no action) keeps reading a spec — nothing about the render
        path moves."""
        t = self._task()
        p = os.path.join(self.tmp, "spec.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"title": "T", "decision": {"label": "Decision", "body": "b"},
                       "glossary": "auto", "provenance": "p"}, f)
        out = self._brief(task=str(t["seq"]), spec=p).strip()
        self.assertEqual(out, ts.brief_output_path(t))
        self.assertTrue(os.path.isfile(out), "render still writes the HTML itself")

    def test_path_action_is_wired_into_the_shared_arg_spec(self):
        """`/todo brief path` and `task-station brief path` parse identically — both go
        through _add_brief_args."""
        import argparse
        parser = argparse.ArgumentParser(prog="brief", add_help=False)
        ts._add_brief_args(parser)
        ns = parser.parse_args(["path", "--task", "7"])
        self.assertEqual(ns.action, "path")
        self.assertEqual(ns.task, "7")
        self.assertIsNone(ns.spec)

    def test_action_help_names_both_actions(self):
        import argparse
        parser = argparse.ArgumentParser(prog="brief", add_help=False)
        ts._add_brief_args(parser)
        action = next(a for a in parser._actions if a.dest == "action")
        self.assertIn("render", action.help)
        self.assertIn("path", action.help)


if __name__ == "__main__":
    unittest.main()
