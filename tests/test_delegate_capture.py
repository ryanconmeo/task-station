"""WS2 delegate capture — persist worker model & token usage per run.

Two isolated halves:
  * delegate.py: `_parse_result` now returns (text, sid, cost, model, usage) from a
    `claude -p --output-format json` blob (graceful Nones on older CLIs), and
    `_save_entry` persists `model` on the workers.json registry entry.
  * task-station.py: `add-cost` grows optional --model / --session / --usage-json /
    --seq-label and appends a per-run record to task['runs'] (append-only, capped),
    while the existing running-total `cost` field is untouched; `worker_lines` shows
    the recorded model.
Mirrors the import + temp-home isolation of tests/test_delegate.py and
tests/test_search_stats.py so it runs under both unittest (hub) and pytest (CI)."""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

# delegate.py lives in lib/delegate/; import it directly (it inserts lib/ itself).
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "lib", "delegate"
))
import delegate as _delegate_mod  # noqa: E402

# task-station.py has a hyphen → load via importlib spec (test_search_stats pattern).
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)
import store  # noqa: E402
_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


# ------------------------------------------------------------- _parse_result ---

class ParseResultTest(unittest.TestCase):
    def _blob(self, obj):
        # Prefix junk some shells inject; _parse_result must find the first brace.
        return "\x1b[0m warming up\n" + json.dumps(obj)

    def test_full_blob_extracts_model_and_usage(self):
        blob = self._blob({
            "result": "done", "session_id": "sess-xyz", "total_cost_usd": 0.1234,
            "usage": {"input_tokens": 100, "output_tokens": 200,
                      "cache_read_input_tokens": 50, "cache_creation_input_tokens": 10},
            "modelUsage": {"claude-opus-4-8": {
                "inputTokens": 100, "outputTokens": 200,
                "cacheReadInputTokens": 50, "cacheCreationInputTokens": 10,
                "costUSD": 0.1234}},
        })
        text, sid, cost, model, usage = _delegate_mod._parse_result(blob)
        self.assertEqual(text, "done")
        self.assertEqual(sid, "sess-xyz")
        self.assertAlmostEqual(cost, 0.1234)
        self.assertEqual(model, "claude-opus-4-8")
        self.assertEqual(usage, {"in": 100, "out": 200, "cache_read": 50, "cache_creation": 10})

    def test_picks_heaviest_model_when_several(self):
        blob = self._blob({
            "result": "ok", "session_id": "s",
            "modelUsage": {
                "claude-haiku-4-5": {"inputTokens": 5, "outputTokens": 5},
                "claude-opus-4-8": {"inputTokens": 1000, "outputTokens": 2000},
            },
        })
        _, _, _, model, _ = _delegate_mod._parse_result(blob)
        self.assertEqual(model, "claude-opus-4-8")

    def test_explicit_top_level_model_wins(self):
        blob = self._blob({
            "result": "ok", "session_id": "s", "model": "claude-sonnet-5",
            "modelUsage": {"claude-opus-4-8": {"inputTokens": 9999, "outputTokens": 9999}},
        })
        _, _, _, model, _ = _delegate_mod._parse_result(blob)
        self.assertEqual(model, "claude-sonnet-5")

    def test_older_cli_missing_keys_are_none(self):
        # No usage / modelUsage / total_cost_usd → graceful Nones, never fabricated.
        blob = self._blob({"result": "hi", "session_id": "s"})
        text, sid, cost, model, usage = _delegate_mod._parse_result(blob)
        self.assertEqual(text, "hi")
        self.assertEqual(sid, "s")
        self.assertIsNone(cost)
        self.assertIsNone(model)
        self.assertIsNone(usage)

    def test_non_json_output_is_passthrough(self):
        text, sid, cost, model, usage = _delegate_mod._parse_result("no json here")
        self.assertEqual(text, "no json here")
        self.assertEqual((sid, cost, model, usage), (None, None, None, None))

    def test_usage_present_without_modelusage(self):
        blob = self._blob({
            "result": "r", "session_id": "s",
            "usage": {"input_tokens": 3, "output_tokens": 4},
        })
        _, _, _, model, usage = _delegate_mod._parse_result(blob)
        self.assertIsNone(model)                                 # no modelUsage → unknown model
        self.assertEqual(usage, {"in": 3, "out": 4, "cache_read": 0, "cache_creation": 0})


# --------------------------------------------------------------- _save_entry ---

class SaveEntryModelTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._saved = (_delegate_mod.REG_DIR, _delegate_mod.REG)
        _delegate_mod.REG_DIR = self._tmp
        _delegate_mod.REG = os.path.join(self._tmp, "workers.json")

    def tearDown(self):
        _delegate_mod.REG_DIR, _delegate_mod.REG = self._saved
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_model_persisted_and_round_trips(self):
        reg = {}
        _delegate_mod._save_entry(reg, "324:Projectname", "Projectname", 324, None,
                                  "/tmp/wt", "sid-1", model="claude-opus-4-8")
        self.assertEqual(reg["324:Projectname"]["model"], "claude-opus-4-8")
        # save_reg wrote it to disk; load_reg reads it back with the model intact.
        self.assertEqual(_delegate_mod.load_reg()["324:Projectname"]["model"], "claude-opus-4-8")

    def test_empty_model_omitted(self):
        reg = {}
        _delegate_mod._save_entry(reg, "k", "Projectname", None, None, "/d", "s")
        self.assertNotIn("model", reg["k"])
        _delegate_mod._save_entry(reg, "k2", "Projectname", None, None, "/d", "s", model="")
        self.assertNotIn("model", reg["k2"])


# --------------------------------------------------- task-station runs record ---

def _repoint(tmp):
    os.environ["TASK_STATION_HOME"] = tmp
    ts.DATA = tmp
    ts.STORE = os.path.join(tmp, "store")
    ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
    ts.LINKS_DIR = os.path.join(ts.STORE, "links")
    ts.DELEGATE_REGISTRY = os.path.join(tmp, "workers.json")
    store.reset_cache()


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _repoint(self.tmp)

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)


class RecordRunTest(_Base):
    def test_record_run_appends_capped(self):
        t = ts.new_task("T", "")
        for i in range(ts.RUNS_CAP + 5):
            ts.record_run(t, session_id="s%d" % i, model="claude-fable-5",
                          cost_usd=0.01, usage={"in": i, "out": 0,
                                                "cache_read": 0, "cache_creation": 0})
        runs = t["runs"]
        self.assertEqual(len(runs), ts.RUNS_CAP)                  # capped at most-recent
        self.assertEqual(runs[-1]["session_id"], "s%d" % (ts.RUNS_CAP + 4))
        self.assertEqual(runs[0]["session_id"], "s5")             # oldest 5 dropped

    def test_run_cost_coercion(self):
        t = ts.new_task("T", "")
        ts.record_run(t, model="m", cost_usd="notanumber")
        self.assertIsNone(t["runs"][0]["cost_usd"])
        ts.record_run(t, model="m", cost_usd=0)
        self.assertIsNone(t["runs"][1]["cost_usd"])               # zero → None, not a misleading $0
        ts.record_run(t, model="m", cost_usd="1.5")
        self.assertAlmostEqual(t["runs"][2]["cost_usd"], 1.5)


class AddCostRunRecordTest(_Base):
    def _task(self, seq=42):
        t = ts.new_task("Delegated work", "")
        t["seq"] = seq
        ts.save_task(t)
        return t

    def test_detail_flags_append_run_record(self):
        t = self._task()
        usage = {"in": 100, "out": 200, "cache_read": 5, "cache_creation": 1}

        class A:
            task = "42"
            usd = "0.99"
            model = "claude-fable-5"
            session = "sess-1"
            seq_label = None
            usage_json = json.dumps(usage)
        ts.cmd_add_cost(A())

        got = ts.load_task(t["id"])
        runs = got.get("runs")
        self.assertEqual(len(runs), 1)
        r = runs[0]
        self.assertEqual(r["model"], "claude-fable-5")
        self.assertEqual(r["session_id"], "sess-1")
        self.assertAlmostEqual(r["cost_usd"], 0.99)
        self.assertEqual(r["usage"], usage)
        # Running total (task["cost"]) still accrues — the two are independent.
        total, nruns = ts.task_cost(got)
        self.assertAlmostEqual(total, 0.99)
        self.assertEqual(nruns, 1)

    def test_plain_add_cost_writes_no_run_record(self):
        # Back-compat: --usd alone (delegate on an older CLI with no model/usage) still
        # only accumulates the total; no runs list is created.
        t = self._task()

        class A:
            task = "42"
            usd = "0.50"
            model = None
            session = None
            seq_label = None
            usage_json = None
        ts.cmd_add_cost(A())

        got = ts.load_task(t["id"])
        self.assertNotIn("runs", got)
        self.assertAlmostEqual(ts.task_cost(got)[0], 0.50)

    def test_malformed_usage_json_still_records_run(self):
        t = self._task()

        class A:
            task = "42"
            usd = "0"
            model = "claude-opus-4-8"
            session = "s"
            seq_label = None
            usage_json = "{not valid json"
        ts.cmd_add_cost(A())

        r = ts.load_task(t["id"])["runs"][0]
        self.assertEqual(r["model"], "claude-opus-4-8")
        self.assertIsNone(r["usage"])                             # bad JSON → no token detail
        self.assertIsNone(r["cost_usd"])                          # --usd 0 → None

    def test_seq_label_recorded(self):
        t = self._task()

        class A:
            task = "42"
            usd = "0.10"
            model = "claude-sonnet-5"
            session = "s"
            seq_label = "docs"
            usage_json = None
        ts.cmd_add_cost(A())
        self.assertEqual(ts.load_task(t["id"])["runs"][0]["seq_label"], "docs")


class WorkerModelDisplayTest(_Base):
    def test_worker_lines_show_model(self):
        with open(ts.DELEGATE_REGISTRY, "w") as f:
            json.dump({"7:Projectname": {"project": "Projectname", "dir": "/tmp/wt",
                                  "session_id": "sid", "model": "claude-opus-4-8"}}, f)
        t = ts.new_task("x", "")
        t["seq"] = 7
        t["projects"] = ["Projectname"]
        lines = ts.worker_lines(t)
        self.assertTrue(any("claude-opus-4-8" in ln for ln in lines),
                        "expected the recorded model in a worker line: %r" % lines)

    def test_worker_lines_omit_model_when_absent(self):
        with open(ts.DELEGATE_REGISTRY, "w") as f:
            json.dump({"7:Projectname": {"project": "Projectname", "dir": "/tmp/wt",
                                  "session_id": "sid"}}, f)
        t = ts.new_task("x", "")
        t["seq"] = 7
        t["projects"] = ["Projectname"]
        lines = ts.worker_lines(t)
        self.assertEqual(len(lines), 1)
        self.assertNotIn(" · ", lines[0])


if __name__ == "__main__":
    unittest.main()
