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


if __name__ == "__main__":
    unittest.main()
