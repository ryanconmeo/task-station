"""Glossary storage + CRUD (Task 2) and context injection (Task 3).

Terms {name, layer, state, def} ride the task dict (no migration); name is unique
per task case-insensitively; mutations go through mutate() and flow into the
Tasktrail stream as task.updated; glossary text is folded into task_search_text.
`cmd_glossary` covers list/add/edit/rm with --task/--session resolution + --rename.
Stdlib-only."""
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
import config  # noqa: E402
import store   # noqa: E402
import stream  # noqa: E402


class _Args:
    def __init__(self, **kw):
        self.__dict__.setdefault("action", "list")
        self.__dict__.setdefault("args", [])
        self.__dict__.setdefault("task", None)
        self.__dict__.setdefault("session", None)
        self.__dict__.setdefault("layer", None)
        self.__dict__.setdefault("state", None)
        self.__dict__.setdefault("definition", None)
        self.__dict__.setdefault("rename", None)
        self.__dict__.update(kw)


class _GlossBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        for k in ("TASK_STATION_STREAM", "TASK_STATION_STREAM_DIR"):
            os.environ.pop(k, None)
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        store.reset_cache()

    def tearDown(self):
        store.reset_cache()
        for k in ("TASK_STATION_HOME", "TASK_STATION_STREAM", "TASK_STATION_STREAM_DIR"):
            os.environ.pop(k, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _task(self, title="Collation Strategy", color="green", **kw):
        t = ts.new_task(title, "summary for " + title, color=color, **kw)
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _attach(self, session, task):
        ts.set_link(session, task["id"])
        return ts.load_task(task["id"])

    def _gloss(self, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_glossary(_Args(**kw))
        return buf.getvalue()


class GlossaryCrudTest(_GlossBase):
    def test_add_then_list(self):
        t = self._task()
        self._gloss(task=str(t["seq"]), action="add",
                    args=["Binary-Default (BIN2) Store", "db", "target", "All text case-sensitive."])
        out = self._gloss(task=str(t["seq"]))
        self.assertIn("Binary-Default (BIN2) Store", out)
        self.assertIn("db·target", out)
        self.assertIn("All text case-sensitive.", out)
        r = ts.load_task(t["id"])
        self.assertEqual(len(r["glossary"]), 1)
        self.assertEqual(r["glossary"][0],
                         {"name": "Binary-Default (BIN2) Store", "layer": "db",
                          "state": "target", "def": "All text case-sensitive."})

    def test_case_insensitive_upsert(self):
        t = self._task()
        self._gloss(task=str(t["seq"]), action="add", args=["Foo Bar", "db", "today", "first def"])
        self._gloss(task=str(t["seq"]), action="add", args=["foo BAR", "app", "target", "second def"])
        r = ts.load_task(t["id"])
        self.assertEqual(len(r["glossary"]), 1)                 # upserted, not duplicated
        self.assertEqual(r["glossary"][0]["name"], "Foo Bar")   # canonical casing preserved
        self.assertEqual(r["glossary"][0]["layer"], "app")
        self.assertEqual(r["glossary"][0]["state"], "target")
        self.assertEqual(r["glossary"][0]["def"], "second def")

    def test_remove_case_insensitive(self):
        t = self._task()
        self._gloss(task=str(t["seq"]), action="add", args=["Term A", "db", "today", "d"])
        self._gloss(task=str(t["seq"]), action="add", args=["Term B", "app", "target", "d2"])
        out = self._gloss(task=str(t["seq"]), action="rm", args=["term a"])
        self.assertIn("Removed", out)
        r = ts.load_task(t["id"])
        self.assertEqual([e["name"] for e in r["glossary"]], ["Term B"])

    def test_rm_missing_reports(self):
        t = self._task()
        out = self._gloss(task=str(t["seq"]), action="rm", args=["nope"])
        self.assertIn("no term", out.lower())

    def test_edit_fields_and_rename(self):
        t = self._task()
        self._gloss(task=str(t["seq"]), action="add", args=["Old Name", "db", "today", "d"])
        self._gloss(task=str(t["seq"]), action="edit", args=["Old Name"],
                    state="shipped", rename="New Name")
        r = ts.load_task(t["id"])
        self.assertEqual(r["glossary"][0]["name"], "New Name")
        self.assertEqual(r["glossary"][0]["state"], "shipped")
        self.assertEqual(r["glossary"][0]["layer"], "db")   # untouched

    def test_edit_missing_reports(self):
        t = self._task()
        out = self._gloss(task=str(t["seq"]), action="edit", args=["ghost"], state="x")
        self.assertIn("no term", out.lower())

    def test_rename_collision_rejected(self):
        t = self._task()
        self._gloss(task=str(t["seq"]), action="add", args=["Alpha", "db", "today", "a"])
        self._gloss(task=str(t["seq"]), action="add", args=["Beta", "db", "today", "b"])
        out = self._gloss(task=str(t["seq"]), action="edit", args=["Alpha"], rename="beta")
        self.assertIn("already exists", out.lower())
        r = ts.load_task(t["id"])
        self.assertEqual(sorted(e["name"] for e in r["glossary"]), ["Alpha", "Beta"])

    def test_list_empty_format(self):
        t = self._task()
        out = self._gloss(task=str(t["seq"]))
        self.assertIn("(empty)", out)
        self.assertIn("glossary add", out)

    def test_list_format_pill_and_bullet(self):
        t = self._task()
        self._gloss(task=str(t["seq"]), action="add", args=["Collation Gate", "CI", "shipped", "a build check"])
        out = self._gloss(task=str(t["seq"]))
        self.assertIn("• Collation Gate [CI·shipped] — a build check", out)

    def test_add_via_session(self):
        t = self._task()
        self._attach("sess-1", t)
        self._gloss(session="sess-1", action="add", args=["S Term", "db", "today", "d"])
        r = ts.load_task(t["id"])
        self.assertEqual(r["glossary"][0]["name"], "S Term")

    def test_no_task_reports(self):
        out = self._gloss(session="ghost-session", action="add", args=["X", "db", "today", "d"])
        self.assertIn("no task", out.lower())

    def test_search_text_includes_glossary(self):
        t = self._task()
        self._gloss(task=str(t["seq"]), action="add",
                    args=["Zebrafish Protocol", "db", "today", "a unique term xyzzy"])
        blob = store.task_search_text(ts.load_task(t["id"]))
        self.assertIn("Zebrafish Protocol", blob)
        self.assertIn("xyzzy", blob)

    def test_add_emits_task_updated_stream(self):
        t = self._task()
        self._gloss(task=str(t["seq"]), action="add", args=["Streamed", "db", "today", "d"])
        updates = [e for e in stream.read_events() if e["event"] == "task.updated"]
        self.assertTrue(updates, "expected a task.updated event for the glossary add")
        self.assertIn("glossary", updates[-1]["data"]["changed"])


class GlossaryContextTest(_GlossBase):
    def test_context_empty_when_no_terms(self):
        self.assertEqual(ts.glossary_context(self._task()), "")

    def test_context_empty_when_task_none(self):
        self.assertEqual(ts.glossary_context(None), "")

    def test_context_block_shape(self):
        t = self._task()
        ts.add_glossary_term(t, "Binary-Default (BIN2) Store", "db", "target",
                             "All text case-sensitive.")
        block = ts.glossary_context(t)
        self.assertIn("GLOSSARY (task %s)" % t["seq"], block)
        self.assertIn("• Binary-Default (BIN2) Store [db·target] — All text case-sensitive.", block)
        # a one-line capture instruction mentioning 'glossary add'
        self.assertIn('glossary add "<name>" <layer> <state> "<def>"', block)

    def _prompt_context(self, session, prompt="just working on it"):
        os.environ["TASK_STATION_PROMPT"] = prompt
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                ts.cmd_prompt_context(_Args(session=session, prompt=prompt))
        finally:
            os.environ.pop("TASK_STATION_PROMPT", None)
        return buf.getvalue()

    def test_prompt_context_injects_when_attached_with_terms(self):
        t = self._task()
        ts.add_glossary_term(t, "Collation Gate", "CI", "shipped", "a build check")
        ts.save_task(t)
        self._attach("sess-ctx", t)
        out = self._prompt_context("sess-ctx")
        self.assertIn("GLOSSARY (task %s)" % t["seq"], out)
        self.assertIn("Collation Gate", out)

    def test_prompt_context_silent_when_no_terms(self):
        t = self._task()
        self._attach("sess-ctx2", t)
        out = self._prompt_context("sess-ctx2")
        self.assertNotIn("GLOSSARY", out)

    def test_glossary_context_subcommand_prints_block(self):
        t = self._task()
        ts.add_glossary_term(t, "Term X", "app", "today", "def x")
        ts.save_task(t)
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_glossary_context(_Args(task=str(t["seq"])))
        self.assertIn("GLOSSARY", buf.getvalue())
        self.assertIn("Term X", buf.getvalue())

    def test_glossary_context_subcommand_empty_is_silent(self):
        t = self._task()
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_glossary_context(_Args(task=str(t["seq"])))
        self.assertEqual(buf.getvalue().strip(), "")


if __name__ == "__main__":
    unittest.main()
