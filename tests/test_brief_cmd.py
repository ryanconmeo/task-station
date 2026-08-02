"""Task 5: the `brief render` command.

Resolves the task, reads a model-supplied brief-spec (JSON) from --spec FILE or
stdin, renders via lib/brief.py to brief_output_path (makedirs), persists
task['brief_path'] through mutate(), and prints the path. The persisted brief_path
is then carried into the contract-v2 note frontmatter automatically."""
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


_SPEC = {
    "title": "Collation — Strategy",
    "subtitle": "Projectname · 2026",
    "decision": {"label": "Decision", "body": "Rebuild + Centralize the Store."},
    "glossary": "auto",
    "provenance": "PR 931.",
}


class BriefCmdTest(unittest.TestCase):
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
        t = ts.new_task("Collation Strategy", "summary", color="green")
        ts.save_task(t)
        ts.ensure_seqs()
        t = ts.load_task(t["id"])
        ts.add_glossary_term(t, "Store", "db", "target", "the data store")
        ts.save_task(t)
        return ts.load_task(t["id"])

    def _write_spec(self, spec=None):
        p = os.path.join(self.tmp, "spec.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(spec or _SPEC, f)
        return p

    def _brief(self, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_brief(_Args(**kw))
        return buf.getvalue()

    def test_render_from_spec_file_writes_and_persists(self):
        t = self._task()
        out = self._brief(task=str(t["seq"]), spec=self._write_spec()).strip()
        # printed path == the derived output path, and the file exists
        self.assertEqual(out, ts.brief_output_path(t))
        self.assertTrue(os.path.isfile(out))
        with open(out, encoding="utf-8") as f:
            html = f.read()
        self.assertIn("<title>Collation — Strategy</title>", html)
        self.assertIn("<h1>Collation — Strategy</h1>", html)
        # the glossary term is mechanically highlighted
        self.assertIn('<span class="term">Store</span>', html)
        # brief_path persisted on the task record
        reloaded = ts.load_task(t["id"])
        self.assertEqual(reloaded.get("brief_path"), out)

    def test_render_from_stdin(self):
        t = self._task()
        old = sys.stdin
        sys.stdin = io.StringIO(json.dumps(_SPEC))
        try:
            out = self._brief(task=str(t["seq"])).strip()
        finally:
            sys.stdin = old
        self.assertTrue(os.path.isfile(out))
        with open(out, encoding="utf-8") as f:
            self.assertIn("term", f.read())

    def test_brief_path_carried_into_contract_v2_frontmatter(self):
        t = self._task()
        self._brief(task=str(t["seq"]), spec=self._write_spec())
        note = obsidian_sync.render_note(ts.load_task(t["id"]))
        self.assertIn("brief_path:", note)
        self.assertIn(ts.brief_output_path(t), note)

    def test_no_task_reports(self):
        out = self._brief(session="ghost", spec=self._write_spec())
        self.assertIn("no task", out.lower())

    def test_invalid_json_reports(self):
        t = self._task()
        p = os.path.join(self.tmp, "bad.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write("{not json")
        out = self._brief(task=str(t["seq"]), spec=p)
        self.assertIn("json", out.lower())

    def test_empty_spec_reports(self):
        t = self._task()
        p = os.path.join(self.tmp, "empty.json")
        open(p, "w").close()
        out = self._brief(task=str(t["seq"]), spec=p)
        self.assertIn("empty", out.lower())


if __name__ == "__main__":
    unittest.main()
