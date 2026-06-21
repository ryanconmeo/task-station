"""_format_list() prints the authoritative Commands: footer (single source of truth)."""
import importlib.util
import os
import shutil
import sys
import tempfile
import unittest

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

# task-station.py has a hyphen, so it can't be a normal import — load it by path.
_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


class RenderFooterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_footer_lists_all_commands(self):
        ts.save_task(ts.new_task("First task", "do the thing"))
        ts.save_task(ts.new_task("Second task", "do another thing"))
        out = ts._format_list()
        self.assertIn("Commands:", out)
        self.assertIn("/todo <n[,n…]> -s", out)
        self.assertIn("/done <n[,n…]>", out)
        self.assertIn("/task-station:config", out)

    def test_commands_footer_md_is_verbatim_table(self):
        # The Markdown footer is now a fixed Commands mini-table, decoupled from
        # the ASCII commands_footer() (no longer derived by splitting it).
        expected = (
            "**Commands**\n"
            "\n"
            "| Command | Action |\n"
            "|---|---|\n"
            "| `/todo [<n>]` | list board / open & resume a task |\n"
            "| `/todo <n> -s` | jump into the task's session (new window) |\n"
            "| `/todo closed [N]` · `all` | list closed tasks |\n"
            "| `/done [<n,…>]` | close current / by number |\n"
            "| `/task-station:config` | settings |"
        )
        self.assertEqual(ts.commands_footer_md(), expected)

    def test_commands_footer_md_decoupled_from_ascii(self):
        # It does not contain the ASCII one-liner's dense `·`-separated body.
        self.assertNotIn("Commands:  /todo", ts.commands_footer_md())
        self.assertNotIn("\n- ", ts.commands_footer_md())


if __name__ == "__main__":
    unittest.main()
