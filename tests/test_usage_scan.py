"""Transcript scanner (lib/usage.py): per-model roll-ups, sidechain (subagent)
bucket separation, prompt-row classification, incremental re-scan no-ops,
truncated-tail tolerance, and the multi-task span-attribution rule.

Builds synthetic JSONL fixtures matching the real transcript schema under a temp
projects dir and drives a real SqliteBackend (usage stores through the backend)."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

import store  # noqa: E402
import usage  # noqa: E402  (usage.py has no hyphen — normal import)

OPUS = "claude-opus-4-8"
HAIKU = "claude-haiku-4-5"


def _iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def _asst(model=OPUS, out=200, inp=1000, ts=1_000, **usage_extra):
    u = {"input_tokens": inp, "output_tokens": out,
         "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    u.update(usage_extra)
    return {"type": "assistant", "timestamp": _iso(ts), "cwd": "/proj",
            "entrypoint": "cli", "message": {"model": model, "usage": u}}


def _user(uuid, content, ts=1_000, **extra):
    o = {"type": "user", "uuid": uuid, "timestamp": _iso(ts),
         "message": {"role": "user", "content": content}}
    o.update(extra)
    return o


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="usage-scan-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        self.projects = os.path.join(self.tmp, "projects")
        self.bucket = os.path.join(self.projects, "-proj")
        os.makedirs(self.bucket, exist_ok=True)
        usage.PROJECTS_ROOT = self.projects
        usage.WORKERS_REGISTRY = None
        self.store = store.SqliteBackend(os.path.join(self.tmp, "store"))
        self.store.ensure()

    def tearDown(self):
        self.store.close()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_session(self, sid, lines):
        with open(os.path.join(self.bucket, sid + ".jsonl"), "w") as f:
            for o in lines:
                f.write(json.dumps(o) + "\n")

    def _write_subagent(self, sid, agent, lines):
        d = os.path.join(self.bucket, sid, "subagents")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, agent + ".jsonl"), "w") as f:
            for o in lines:
                f.write(json.dumps(o) + "\n")


class ScanTest(_Base):
    def test_per_model_sums_and_sidechain_skipped_in_parent(self):
        self._write_session("s1", [
            _asst(OPUS, out=200, inp=1000),
            {"type": "assistant", "isSidechain": True,
             "message": {"model": OPUS, "usage": {"output_tokens": 9999}}},
            _asst(OPUS, out=50, inp=100),
        ])
        usage.scan_session(self.store, "s1", "T1")
        row = self.store.get_session_usage("s1")
        m = row["models"][OPUS]
        self.assertEqual(m["msgs"], 2)                 # sidechain line NOT counted
        self.assertEqual(m["out"], 250)                # 200 + 50, not 9999
        self.assertEqual(m["in"], 1100)
        self.assertGreater(m["cost_usd"], 0)

    def test_subagent_traffic_lands_in_sidechain_bucket(self):
        self._write_session("s2", [_asst(OPUS, out=200)])
        self._write_subagent("s2", "agent-1", [
            {"type": "assistant", "timestamp": _iso(1000),
             "message": {"model": HAIKU, "usage": {"output_tokens": 50, "input_tokens": 10}}},
        ])
        usage.scan_session(self.store, "s2", "T1")
        row = self.store.get_session_usage("s2")
        self.assertIn(OPUS, row["models"])
        self.assertNotIn(HAIKU, row["models"])         # subagent model is NOT in parent
        self.assertEqual(row["sidechain"][HAIKU]["out"], 50)

    def test_prompt_rows_by_kind(self):
        self._write_session("s3", [
            _user("u1", "hello world"),
            _user("u2", [{"type": "tool_result", "content": "ignored"}]),   # tool result
            _user("u3", "compaction summary here", isCompactSummary=True),
            _user("u4", "<command-name>/todo</command-name>\n<command-args>save</command-args>"),
            _asst(OPUS, out=10),
        ])
        usage.scan_session(self.store, "s3", "T1")
        rows = {r["uuid"]: r for r in self.store.prompts_for_task("T1")}
        self.assertEqual(rows["u1"]["kind"], "prompt")
        self.assertNotIn("u2", rows)                   # tool_result excluded
        self.assertEqual(rows["u3"]["kind"], "compact")
        self.assertEqual(rows["u4"]["kind"], "command")
        self.assertIn("/todo", rows["u4"]["text"])
        self.assertIn("save", rows["u4"]["text"])

    def test_incremental_rescan_is_a_noop(self):
        self._write_session("s4", [_asst(OPUS, out=200)])
        usage.scan_session(self.store, "s4", "T1")
        usage.scan_session(self.store, "s4", "T1")      # unchanged file → no double count
        self.assertEqual(self.store.get_session_usage("s4")["models"][OPUS]["out"], 200)

    def test_incremental_appends_only_new_lines(self):
        path = os.path.join(self.bucket, "s5.jsonl")
        with open(path, "w") as f:
            f.write(json.dumps(_asst(OPUS, out=200)) + "\n")
        usage.scan_session(self.store, "s5", "T1")
        self.assertEqual(self.store.get_session_usage("s5")["models"][OPUS]["out"], 200)
        with open(path, "a") as f:
            f.write(json.dumps(_asst(OPUS, out=300)) + "\n")
        usage.scan_session(self.store, "s5", "T1")
        self.assertEqual(self.store.get_session_usage("s5")["models"][OPUS]["out"], 500)

    def test_truncated_tail_left_for_next_flush(self):
        path = os.path.join(self.bucket, "s6.jsonl")
        line1 = json.dumps(_asst(OPUS, out=200)) + "\n"
        partial = '{"type":"assistant","message":{"model":"%s","usage":{"output_tokens":300' % OPUS
        with open(path, "w") as f:
            f.write(line1 + partial)                    # no trailing newline → partial
        usage.scan_session(self.store, "s6", "T1")
        self.assertEqual(self.store.get_session_usage("s6")["models"][OPUS]["out"], 200)
        # Complete the truncated line on the next write; the next scan picks it up.
        with open(path, "w") as f:
            f.write(line1 + json.dumps(_asst(OPUS, out=300)) + "\n")
        usage.scan_session(self.store, "s6", "T1")
        self.assertEqual(self.store.get_session_usage("s6")["models"][OPUS]["out"], 500)

    def test_full_rescan_on_shrink(self):
        path = os.path.join(self.bucket, "s7.jsonl")
        with open(path, "w") as f:
            f.write(json.dumps(_asst(OPUS, out=200)) + "\n")
            f.write(json.dumps(_asst(OPUS, out=200)) + "\n")
        usage.scan_session(self.store, "s7", "T1")
        self.assertEqual(self.store.get_session_usage("s7")["models"][OPUS]["out"], 400)
        with open(path, "w") as f:                      # rotation → smaller file
            f.write(json.dumps(_asst(OPUS, out=200)) + "\n")
        usage.scan_session(self.store, "s7", "T1")
        self.assertEqual(self.store.get_session_usage("s7")["models"][OPUS]["out"], 200)


class AttributionTest(_Base):
    def _save_task(self, tid, updated_ts, spans):
        self.store.save_task({"id": tid, "updated_ts": updated_ts,
                              "sessions": ["shared"], "spans": spans,
                              "title": tid, "status": "open"})

    def test_span_match_wins_over_recency(self):
        self._save_task("A", 100, [[1000, 2000]])       # older, but owns ts=1500
        self._save_task("B", 200, [[3000, 4000]])       # newer
        self._write_session("shared", [_asst(OPUS, out=10, ts=1500)])
        usage.scan_session(self.store, "shared", "A")
        self.assertEqual(self.store.get_session_usage("shared")["task_id"], "A")

    def test_unmatched_falls_to_most_recent(self):
        self._save_task("A", 100, [[1000, 2000]])
        self._save_task("B", 200, [[3000, 4000]])
        self._write_session("shared", [_asst(OPUS, out=10, ts=9999)])   # outside both
        usage.scan_session(self.store, "shared", "A")
        self.assertEqual(self.store.get_session_usage("shared")["task_id"], "B")

    def test_helper_none_for_no_candidates(self):
        self.assertIsNone(usage.attribute_message(1500, []))

    def test_prompts_attributed_per_span_not_whole_session(self):
        # A shared session worked task A (spans 1000-2000) then task B (3000-4000):
        # each prompt files under ITS span's task, not the single session owner.
        self._save_task("A", 100, [[1000, 2000]])
        self._save_task("B", 200, [[3000, 4000]])
        self._write_session("shared", [
            _user("uA", "work on A please", ts=1500),
            _asst(OPUS, out=10, ts=1600),
            _user("uB", "now switch to B", ts=3500),
            _asst(OPUS, out=10, ts=3600),
        ])
        usage.scan_session(self.store, "shared", "A")
        a_uuids = [p["uuid"] for p in self.store.prompts_for_task("A")]
        b_uuids = [p["uuid"] for p in self.store.prompts_for_task("B")]
        self.assertEqual(a_uuids, ["uA"])
        self.assertEqual(b_uuids, ["uB"])

    def test_prompt_outside_every_span_keeps_session_owner(self):
        self._save_task("A", 100, [[1000, 2000]])
        self._save_task("B", 200, [[3000, 4000]])
        self._write_session("shared", [
            _user("uX", "no span holds this", ts=9999),
            _asst(OPUS, out=10, ts=9999),
        ])
        usage.scan_session(self.store, "shared", "A")
        owner = self.store.get_session_usage("shared")["task_id"]   # recency → B
        self.assertEqual([p["uuid"] for p in self.store.prompts_for_task(owner)], ["uX"])


if __name__ == "__main__":
    unittest.main()
