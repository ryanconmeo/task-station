"""Cost phase (#463, RESOLVED #4): the SEPARATE wasted/crashed cost category —
crashed/timed-out spend is recorded distinctly from the real-work total, never
skipped and never folded in. Covers add_cost/record_run/cmd_add_cost routing,
task_wasted_cost, the stats line, and task_usage's wasted_cost_usd."""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import types
import unittest

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)
import store  # noqa: E402
_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)
import usage  # noqa: E402


def _repoint(tmp):
    os.environ["TASK_STATION_HOME"] = tmp
    ts.DATA = tmp
    ts.STORE = os.path.join(tmp, "store")
    ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
    ts.LINKS_DIR = os.path.join(ts.STORE, "links")
    ts.DELEGATE_REGISTRY = os.path.join(tmp, "workers.json")
    store.reset_cache()


class WastedCategoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-wasted-")
        _repoint(self.tmp)

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_add_cost_routes_real_vs_wasted(self):
        t = ts.new_task("T", "")
        ts.add_cost(t, 1.00)                       # default real
        ts.add_cost(t, 0.25, category="wasted")
        ts.add_cost(t, 0.50)                       # real again
        self.assertEqual(ts.task_cost(t), (1.50, 2))          # real total untouched by wasted
        self.assertEqual(ts.task_wasted_cost(t), (0.25, 1))
        # the two buckets are stored distinctly
        self.assertAlmostEqual(t["cost"]["total_usd"], 1.50)
        self.assertAlmostEqual(t["cost"]["wasted_usd"], 0.25)

    def test_record_run_tags_category(self):
        t = ts.new_task("T", "")
        ts.record_run(t, model="m", cost_usd=0.1)                       # default real
        ts.record_run(t, model="m", cost_usd=0.2, category="wasted")
        self.assertEqual(t["runs"][0]["category"], "real")
        self.assertEqual(t["runs"][1]["category"], "wasted")

    def test_cmd_add_cost_wasted_category(self):
        t = ts.new_task("T", ""); t["seq"] = 7
        ts.save_task(t)
        ts.cmd_add_cost(types.SimpleNamespace(
            task="7", usd="0.30", model="claude-sonnet-5", session="wk-1",
            seq_label=None, usage_json=json.dumps({"in": 10, "out": 5}),
            category="wasted"))
        got = ts.load_task(t["id"])
        self.assertEqual(ts.task_cost(got), (0.0, 0))              # real untouched
        self.assertEqual(ts.task_wasted_cost(got), (0.30, 1))
        self.assertEqual(got["runs"][0]["category"], "wasted")

    def test_stats_line_shows_wasted_distinctly(self):
        t = ts.new_task("T", "")
        ts.add_cost(t, 2.00)                        # real
        ts.add_cost(t, 0.40, category="wasted")
        line = ts.task_stats_line(t)
        self.assertIn("workers $2.00", line)
        self.assertIn("wasted $0.40", line)

    def test_no_wasted_segment_when_none(self):
        t = ts.new_task("T", "")
        ts.add_cost(t, 1.00)
        self.assertNotIn("wasted", ts.task_stats_line(t))

    def test_task_usage_exposes_wasted(self):
        t = ts.new_task("T", ""); t["seq"] = 9
        ts.add_cost(t, 1.00)
        ts.add_cost(t, 0.15, category="wasted")
        ts.save_task(t)
        usage.PROJECTS_ROOT = os.path.join(self.tmp, "projects")
        usage.WORKERS_REGISTRY = ts.DELEGATE_REGISTRY
        u = usage.task_usage(ts._backend(), ts.load_task(t["id"]))
        self.assertAlmostEqual(u["reported_cost_usd"], 1.00)   # real-work reported
        self.assertAlmostEqual(u["wasted_cost_usd"], 0.15)     # separate figure


if __name__ == "__main__":
    unittest.main()
