"""Unit tests for lib/stream.py — the pure JSONL event-ledger primitive (A-2).

Exercises stream.py in isolation (no engine): envelope shape, manifest, shard
naming, the append/tee/verify/redact primitives. The `alloc_n` counter is passed
in by the caller (the engine owns the per-task persisted counter), so here it is a
trivial in-memory lambda."""
import importlib
import json
import os
import shutil
import sys
import tempfile
import unittest

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

import stream  # noqa: E402


def _read(path, mode="rb"):
    with open(path, mode) as f:
        return f.read()


class _Counter:
    """Stand-in for the engine's per-task persisted monotonic counter."""
    def __init__(self):
        self.by_uuid = {}

    def alloc(self, uuid):
        n = self.by_uuid.get(uuid, 0) + 1
        self.by_uuid[uuid] = n
        return n


class StreamLib(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        os.environ.pop("TASK_STATION_STREAM", None)
        os.environ.pop("TASK_STATION_STREAM_DIR", None)
        importlib.reload(stream)  # re-bind paths/config after env change
        self.ctr = _Counter()

    def tearDown(self):
        for k in ("TASK_STATION_HOME", "TASK_STATION_STREAM", "TASK_STATION_STREAM_DIR"):
            os.environ.pop(k, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _emit(self, kind, task, data, session=None):
        u = task.get("uuid") or task.get("id")
        return stream.emit(kind, task, data, lambda: self.ctr.alloc(u), actor_session=session)

    def _task(self, uuid="u1", seq=1):
        return {"uuid": uuid, "id": uuid, "seq": seq}

    def test_emit_writes_one_jsonl_line_with_envelope(self):
        env = self._emit("task.created", self._task(), {"title": "hi"}, session="sess-1")
        base = os.path.join(self.tmp, "stream")
        shards = os.listdir(os.path.join(base, "events"))
        self.assertEqual(len(shards), 1)
        self.assertTrue(shards[0].endswith(".jsonl"))
        lines = _read(os.path.join(base, "events", shards[0]), "r").splitlines()
        self.assertEqual(len(lines), 1)
        rec = json.loads(lines[0])
        self.assertEqual(rec["v"], 1)
        self.assertEqual(rec["event"], "task.created")
        self.assertEqual(rec["n"], 1)
        self.assertEqual(rec["task"], {"uuid": "u1", "seq": 1})
        self.assertEqual(rec["actor"], {"session": "sess-1"})
        self.assertEqual(rec["data"], {"title": "hi"})
        self.assertIn("ts", rec)
        self.assertEqual(rec, env)

    def test_shard_name_is_year_month_from_ts(self):
        env = self._emit("task.created", self._task(), {})
        ym = env["ts"][:7]
        self.assertTrue(os.path.exists(
            os.path.join(self.tmp, "stream", "events", ym + ".jsonl")))

    def test_manifest_created_with_spec_and_generation(self):
        self._emit("task.created", self._task(), {})
        m = json.loads(_read(os.path.join(self.tmp, "stream", "tasktrail.json"), "r"))
        self.assertEqual(m["spec_version"], "1.0")
        self.assertEqual(m["generation"], 1)
        self.assertTrue(m["producer"].startswith("task-station/"))

    def test_per_task_n_increments(self):
        for _ in range(3):
            self._emit("task.event", self._task(), {"kind": "log"})
        ns = [json.loads(l)["n"] for l in self._all_lines()]
        self.assertEqual(ns, [1, 2, 3])

    def test_two_tasks_have_independent_counters(self):
        self._emit("task.created", self._task("a", 1), {})
        self._emit("task.created", self._task("b", 2), {})
        self._emit("task.event", self._task("a", 1), {})
        recs = [json.loads(l) for l in self._all_lines()]
        by = {}
        for r in recs:
            by.setdefault(r["task"]["uuid"], []).append(r["n"])
        self.assertEqual(by["a"], [1, 2])
        self.assertEqual(by["b"], [1])

    def test_read_events_yields_in_shard_order(self):
        self._emit("task.created", self._task(), {})
        self._emit("task.event", self._task(), {})
        evs = list(stream.read_events())
        self.assertEqual([e["n"] for e in evs], [1, 2])

    def test_verify_passes_on_gapless(self):
        for _ in range(4):
            self._emit("task.event", self._task(), {})
        res = stream.verify()
        self.assertTrue(res["ok"], res)

    def test_verify_flags_a_gap(self):
        self._emit("task.event", self._task(), {})
        self.ctr.by_uuid["u1"] = 2   # skip n=2 -> next emit is n=3 (a gap)
        self._emit("task.event", self._task(), {})
        res = stream.verify()
        self.assertFalse(res["ok"])

    def test_tee_writes_byte_identical_lines_and_manifest(self):
        tee = os.path.join(self.tmp, "external")
        os.environ["TASK_STATION_STREAM_DIR"] = tee
        importlib.reload(stream)
        self.ctr = _Counter()
        self._emit("task.created", self._task(), {"title": "hi"})
        base = os.path.join(self.tmp, "stream")
        b_shard = os.listdir(os.path.join(base, "events"))[0]
        t_shard = os.listdir(os.path.join(tee, "events"))[0]
        self.assertEqual(b_shard, t_shard)
        self.assertEqual(_read(os.path.join(base, "events", b_shard)),
                         _read(os.path.join(tee, "events", t_shard)))
        self.assertEqual(_read(os.path.join(base, "tasktrail.json")),
                         _read(os.path.join(tee, "tasktrail.json")))

    def test_off_writes_nothing(self):
        os.environ["TASK_STATION_STREAM"] = "off"
        importlib.reload(stream)
        r = self._emit("task.created", self._task(), {})
        self.assertIsNone(r)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "stream")))

    def test_stub_task_replaces_payload_and_keeps_envelope(self):
        self._emit("task.created", self._task("a", 1), {"title": "secret"})
        self._emit("task.event", self._task("a", 1), {"kind": "log", "text": "x"})
        self._emit("task.created", self._task("b", 2), {"title": "keep"})
        n = stream.stub_task("a")
        self.assertEqual(n, 2)
        recs = [json.loads(l) for l in self._all_lines()]
        for r in recs:
            if r["task"]["uuid"] == "a":
                self.assertEqual(r["data"], {"redacted": True})
                self.assertIn("n", r)            # envelope preserved
            else:
                self.assertEqual(r["data"], {"title": "keep"})
        # idempotent
        self.assertEqual(stream.stub_task("a"), 0)

    def test_bump_generation(self):
        self._emit("task.created", self._task(), {})
        self.assertEqual(stream.bump_generation(), 2)
        m = json.loads(_read(os.path.join(self.tmp, "stream", "tasktrail.json"), "r"))
        self.assertEqual(m["generation"], 2)

    def _all_lines(self):
        base = os.path.join(self.tmp, "stream", "events")
        out = []
        for name in sorted(os.listdir(base)):
            out += _read(os.path.join(base, name), "r").splitlines()
        return out


if __name__ == "__main__":
    unittest.main()
