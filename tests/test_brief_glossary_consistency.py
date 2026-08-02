"""Task 8: cross-surface consistency for /glossary + /brief.

Verifies the checklist wiring: bare-alias loop covers both commands; artifacts_root
is a first-class config flag (set/get/clear + reset); /todo glossary and /todo brief
route through _TODO_SUBCMDS for parity; README documents both commands + the
artifacts_root config row. Stdlib-only."""
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(_REPO_ROOT, "lib")
sys.path.insert(0, LIB)

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)
import config  # noqa: E402
import store   # noqa: E402


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class BareAliasLoopTest(unittest.TestCase):
    def test_loop_includes_glossary_and_brief(self):
        text = _read(os.path.join(_REPO_ROOT, "hooks", "on_session_start.sh"))
        m = re.search(r"for c in ([^;]+); do", text)
        self.assertIsNotNone(m)
        members = [w.strip("'\"") for w in m.group(1).split()]
        for name in ("todo", "done", "repos", "pin", "save", "history", "glossary", "brief"):
            self.assertIn(name, members)


class ReadmeRowsTest(unittest.TestCase):
    def setUp(self):
        self.readme = _read(os.path.join(_REPO_ROOT, "README.md"))

    def test_command_rows_present(self):
        self.assertIn("`/task-station:glossary`", self.readme)
        self.assertIn("`/task-station:brief`", self.readme)

    def test_artifacts_root_config_row_present(self):
        self.assertIn("--artifacts-root", self.readme)


class ArtifactsRootConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        os.environ.pop("TASK_STATION_ARTIFACTS_ROOT", None)

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        os.environ.pop("TASK_STATION_ARTIFACTS_ROOT", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_set_get_clear(self):
        with redirect_stdout(io.StringIO()):
            config.cmd_config(_Args(artifacts_root="/tmp/arts", artifacts_root_get=False))
        self.assertEqual(config.get("artifacts_root"), "/tmp/arts")
        # clear (no value → const "") restores the derived default
        with redirect_stdout(io.StringIO()):
            config.cmd_config(_Args(artifacts_root="", artifacts_root_get=False))
        self.assertIsNone(config.get("artifacts_root"))

    def test_get_prints_effective(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(_Args(artifacts_root=None, artifacts_root_get=True))
        self.assertEqual(buf.getvalue().strip(), config.artifacts_root())

    def test_reset_clears_artifacts_root(self):
        config.set("artifacts_root", "/tmp/arts")
        self.assertIn("artifacts_root", config.RESET_KEYS)
        config.reset_settings()
        self.assertIsNone(config.get("artifacts_root"))

    def test_board_lists_artifacts_root(self):
        self.assertIn("--artifacts-root", config.render_board())


class TodoRoutingParityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        os.environ.pop("TASK_STATION_ARTIFACTS_ROOT", None)
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        store.reset_cache()

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        os.environ.pop("TASK_STATION_ARTIFACTS_ROOT", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _task(self):
        t = ts.new_task("Routing task", "s", color="green")
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _render(self, session, arg):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_render(_Args(session=session, arg=arg, format=None))
        return buf.getvalue()

    def test_todo_glossary_routes(self):
        self.assertIn("glossary", ts._TODO_SUBCMDS)
        t = self._task()
        ts.set_link("sess-r", t["id"])
        self._render("sess-r", 'glossary add "Routed Term" db today "a def"')
        r = ts.load_task(t["id"])
        self.assertEqual(r["glossary"][0]["name"], "Routed Term")
        out = self._render("sess-r", "glossary")
        self.assertIn("Routed Term", out)

    def test_todo_brief_routes(self):
        self.assertIn("brief", ts._TODO_SUBCMDS)
        t = self._task()
        ts.set_link("sess-b", t["id"])
        spec = os.path.join(self.tmp, "s.json")
        with open(spec, "w", encoding="utf-8") as f:
            json.dump({"title": "T", "decision": {"body": "d"},
                       "glossary": "auto", "provenance": "p"}, f)
        out = self._render("sess-b", "brief render --spec %s" % spec)
        self.assertIn("brief.html", out)
        self.assertTrue(os.path.isfile(ts.load_task(t["id"])["brief_path"]))


if __name__ == "__main__":
    unittest.main()
