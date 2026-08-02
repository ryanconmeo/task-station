"""`task-station usage` CLI + the stats-line ledger segment + the config flags.

Drives cmd_usage end-to-end (scan a synthetic transcript via --refresh, then
render / --json / --flush) and checks task_stats_line gains the derived %/$ segment
while keeping the delegate-reported `workers $` cross-check. In-process _Args +
redirect_stdout, matching the existing CLI tests, under temp-home isolation."""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

import store  # noqa: E402
import config  # noqa: E402

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)

OPUS = "claude-opus-4-8"
FABLE = "claude-fable-5"


def _iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def _asst(model, out, inp=1000, ts_=1000):
    return {"type": "assistant", "timestamp": _iso(ts_), "cwd": "/proj",
            "entrypoint": "cli",
            "message": {"model": model,
                        "usage": {"input_tokens": inp, "output_tokens": out,
                                  "cache_read_input_tokens": 0,
                                  "cache_creation_input_tokens": 0}}}


class _Args:
    def __init__(self, **kw):
        d = dict(task=None, refresh=False, flush=False, quiet=False, as_json=False)
        d.update(kw)
        self.__dict__.update(d)


class _Base(unittest.TestCase):
    def setUp(self):
        for v in ("TASK_STATION_USAGE_TRACKING", "TASK_STATION_USAGE_PROMPTS",
                  "TASK_STATION_USAGE_BILLING_MODE"):
            os.environ.pop(v, None)
        self.tmp = tempfile.mkdtemp(prefix="usage-cli-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.PROJECTS_ROOT = os.path.join(self.tmp, "projects")
        ts.DELEGATE_REGISTRY = os.path.join(self.tmp, "workers.json")
        self.bucket = os.path.join(ts.PROJECTS_ROOT, "-proj")
        os.makedirs(self.bucket, exist_ok=True)
        store.reset_cache()

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_session(self, sid, lines):
        with open(os.path.join(self.bucket, sid + ".jsonl"), "w") as f:
            for o in lines:
                f.write(json.dumps(o) + "\n")

    def _seed_task_with_usage(self, sid="s1"):
        t = ts.new_task("Ledger demo", "summary")
        t["sessions"] = [sid]
        t["cost"] = {"total_usd": 5.0, "runs": 1}       # delegate-reported cross-check
        ts.save_task(t)
        ts.ensure_seqs()
        self._write_session(sid, [_asst(OPUS, out=800), _asst(FABLE, out=200)])
        return ts.load_task(t["id"])

    def _out(self, args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_usage(args)
        return buf.getvalue()


class RenderTest(_Base):
    def test_no_ledger_message(self):
        t = ts.new_task("Empty", "s")
        ts.save_task(t)
        ts.ensure_seqs()
        out = self._out(_Args(task=t["id"]))          # id ref — no seq round-trip needed
        self.assertIn("No usage tracked yet", out)

    def test_refresh_then_render_shows_mix_and_costs(self):
        t = self._seed_task_with_usage()
        out = self._out(_Args(task=str(t["seq"]), refresh=True))
        self.assertIn("opus", out)
        self.assertIn("fable", out)
        self.assertIn("derived", out)
        self.assertIn("$5.00 reported", out)            # delegate cross-check
        self.assertIn("Derivation:", out)

    def test_json_output(self):
        t = self._seed_task_with_usage()
        self._out(_Args(task=str(t["seq"]), refresh=True))  # populate ledger
        out = self._out(_Args(task=str(t["seq"]), as_json=True))
        data = json.loads(out)
        self.assertIn(OPUS, data["models"])
        self.assertGreater(data["total_cost_usd"], 0)
        self.assertEqual(data["reported_cost_usd"], 5.0)
        self.assertIn("derived_note", data)

    def test_unknown_task_ref(self):
        self.assertIn("No task matching", self._out(_Args(task="999")))

    def test_bare_usage_prints_hint(self):
        self.assertIn("usage: task-station usage", self._out(_Args()))


class FlushTest(_Base):
    def test_flush_quiet_is_silent_and_populates_ledger(self):
        t = self._seed_task_with_usage()
        self.assertEqual(self._out(_Args(flush=True, quiet=True)), "")
        row = ts._backend().get_session_usage("s1")
        self.assertIsNotNone(row)
        self.assertIn(OPUS, row["models"])

    def test_flush_reports_count_when_not_quiet(self):
        self._seed_task_with_usage()
        self.assertIn("Usage flushed", self._out(_Args(flush=True)))

    def test_flush_noop_when_tracking_off(self):
        self._seed_task_with_usage()
        os.environ["TASK_STATION_USAGE_TRACKING"] = "off"
        try:
            self.assertEqual(self._out(_Args(flush=True, quiet=True)), "")
            self.assertIsNone(ts._backend().get_session_usage("s1"))
        finally:
            os.environ.pop("TASK_STATION_USAGE_TRACKING", None)


class StatsLineTest(_Base):
    def test_segment_appended_when_ledger_has_data(self):
        t = self._seed_task_with_usage()
        self._out(_Args(task=str(t["seq"]), refresh=True))
        line = ts.task_stats_line(ts.load_task(t["id"]))
        self.assertIn("workers $5.00", line)            # reported cross-check preserved
        self.assertIn("derived", line)
        self.assertTrue("opus" in line or "fable" in line)

    def test_no_segment_for_new_task(self):
        self.assertEqual(ts.task_stats_line(ts.new_task("brand new", "")), "")

    def test_segment_absent_when_tracking_off(self):
        t = self._seed_task_with_usage()
        self._out(_Args(task=str(t["seq"]), refresh=True))
        os.environ["TASK_STATION_USAGE_TRACKING"] = "off"
        try:
            line = ts.task_stats_line(ts.load_task(t["id"]))
            self.assertNotIn("derived", line)
            self.assertIn("workers $5.00", line)         # cross-check still shown
        finally:
            os.environ.pop("TASK_STATION_USAGE_TRACKING", None)


class ConfigFlagTest(_Base):
    def test_usage_tracking_default_on(self):
        self.assertTrue(config.usage_tracking_enabled())

    def test_usage_prompts_default_on(self):
        self.assertTrue(config.usage_prompts_enabled())

    def test_billing_mode_default_api_and_settable(self):
        self.assertEqual(config.usage_billing_mode(), "api")
        config.set("usage_billing_mode", "subscription")
        self.assertEqual(config.usage_billing_mode(), "subscription")

    def test_billing_mode_unknown_falls_back_to_api(self):
        config.set("usage_billing_mode", "bogus")
        self.assertEqual(config.usage_billing_mode(), "api")

    def test_subscription_mode_relabels_derived_note(self):
        config.set("usage_billing_mode", "subscription")
        import usage
        note = usage._derived_note(False, set())
        self.assertIn("API-equivalent value", note)


if __name__ == "__main__":
    unittest.main()
