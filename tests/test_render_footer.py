"""_format_list() prints the authoritative Commands: footer (single source of truth)."""
import importlib.util
import os
import shutil
import sys
import tempfile
import unittest

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

import store  # noqa: E402  (normal import — store.py has no hyphen)

# task-station.py has a hyphen, so it can't be a normal import — load it by path.
_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


def _repoint(tmp):
    """Point task-station.py's import-frozen path globals at a fresh tmp store so
    writes can NEVER reach the real ~/.claude store, regardless of how the test is
    invoked (flat-module discovery skips tests/__init__.py)."""
    os.environ["TASK_STATION_HOME"] = tmp
    ts.DATA = tmp
    ts.STORE = os.path.join(tmp, "store")
    ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
    ts.LINKS_DIR = os.path.join(ts.STORE, "links")
    ts.PROJECTS_ROOT = os.path.join(tmp, "projects")
    store.reset_cache()


class RenderFooterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _repoint(self.tmp)

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_footer_lists_all_commands(self):
        # The ASCII list footer is now the aligned help block (no fence).
        ts.save_task(ts.new_task("First task", "do the thing"))
        ts.save_task(ts.new_task("Second task", "do another thing"))
        out = ts._format_list()
        self.assertIn("/todo                   show the board", out)
        self.assertIn("/todo <n1, n2, …> -s    jump into task session(s), in a new window", out)
        self.assertIn("/done <n1, n2, …>       close tasks by number", out)
        self.assertIn("/task-station:config    open settings", out)
        self.assertIn("<n> a task number  ·  <n1, n2, …> one or more  ·  [N] optional count", out)

    def test_commands_footer_md_is_verbatim_fenced_block(self):
        # The Markdown footer is the aligned help block under a **Commands**
        # heading, wrapped in a ``` fence so it renders monospace verbatim.
        expected = (
            "**Commands**\n"
            "\n"
            "```\n"
            "/todo                   show the board\n"
            "/todo <n>               open & resume a task\n"
            "/todo <n1, n2, …> -s    jump into task session(s), in a new window\n"
            "/todo closed [N]        list recent closed (default 20)\n"
            "/todo all               show every task (all open + closed)\n"
            "/done                   close the current task\n"
            "/done <n1, n2, …>       close tasks by number\n"
            "/task-station:config    open settings\n"
            "\n"
            "<n> a task number  ·  <n1, n2, …> one or more  ·  [N] optional count\n"
            "```"
        )
        self.assertEqual(ts.commands_footer_md(), expected)

    def test_commands_footer_md_decoupled_and_consistent(self):
        md = ts.commands_footer_md()
        # No bullets, no old dense one-liner.
        self.assertNotIn("\n- ", md)
        self.assertNotIn("Commands:  /todo", md)
        # The fenced body is exactly the ASCII footer (one source of truth).
        body = md.split("```\n", 1)[1].rsplit("\n```", 1)[0]
        self.assertEqual(body, ts.commands_footer())


if __name__ == "__main__":
    unittest.main()
