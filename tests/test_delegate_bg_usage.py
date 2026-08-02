"""Cost phase (#463): delegate._transcript_usage_summary — the REPORTED per-worker
figure re-sourced by summing the worker transcript's assistant-message usage and
pricing it via lib/pricing (there is NO stdout result event under --bg). The
expected cost is computed from the REAL pricing module in-test (no hand-rolled
constants)."""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib", "delegate"))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import delegate as dg  # noqa: E402
import pricing         # noqa: E402


class TranscriptUsageSummaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-bgusage-")
        self.proj = os.path.join(self.tmp, "projects", "-work-repo")
        os.makedirs(self.proj)
        self._saved_root = dg.PROJECTS_ROOT
        dg.PROJECTS_ROOT = os.path.join(self.tmp, "projects")

    def tearDown(self):
        dg.PROJECTS_ROOT = self._saved_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, sid, lines):
        with open(os.path.join(self.proj, sid + ".jsonl"), "w") as f:
            for obj in lines:
                f.write(json.dumps(obj) + "\n")

    def _asst(self, model, usage, ts="2026-07-22T00:00:00Z"):
        return {"type": "assistant", "timestamp": ts,
                "message": {"model": model, "usage": usage}}

    def test_sums_usage_and_prices_via_pricing(self):
        sid = "wk-1"
        u1 = {"input_tokens": 100, "output_tokens": 200,
              "cache_read_input_tokens": 5, "cache_creation_input_tokens": 1}
        u2 = {"input_tokens": 10, "output_tokens": 20,
              "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        self._write(sid, [
            {"type": "user", "message": {"role": "user", "content": "hi"}},
            self._asst("claude-sonnet-5", u1),
            "MALFORMED-LINE-SKIP-ME",
            self._asst("claude-sonnet-5", u2),
        ])
        # a malformed line is written as a raw string above → invalid json → skipped
        with open(os.path.join(self.proj, sid + ".jsonl"), "a") as f:
            f.write("not json\n")

        usage, model, cost = dg._transcript_usage_summary(sid)
        self.assertEqual(model, "claude-sonnet-5")
        self.assertEqual(usage, {"in": 110, "out": 220, "cache_read": 5,
                                 "cache_creation": 1})
        expected = round(
            pricing.message_cost("claude-sonnet-5", u1, dg._iso_to_epoch("2026-07-22T00:00:00Z"))
            + pricing.message_cost("claude-sonnet-5", u2, dg._iso_to_epoch("2026-07-22T00:00:00Z")),
            6)
        self.assertAlmostEqual(cost, expected)

    def test_missing_transcript_is_none(self):
        self.assertEqual(dg._transcript_usage_summary("nope"),
                         (None, None, None))

    def test_unpriced_model_returns_cost_none_but_keeps_usage(self):
        sid = "wk-2"
        self._write(sid, [self._asst("some-unknown-model-9000",
                                     {"input_tokens": 5, "output_tokens": 7})])
        usage, model, cost = dg._transcript_usage_summary(sid)
        self.assertEqual(usage["in"], 5)
        self.assertEqual(usage["out"], 7)
        self.assertIsNone(cost)               # unknown model → unpriced, not fabricated

    def test_dominant_model_by_output_tokens(self):
        sid = "wk-3"
        self._write(sid, [
            self._asst("claude-haiku-4-5-20251001", {"input_tokens": 1, "output_tokens": 5}),
            self._asst("claude-opus-4-8", {"input_tokens": 1, "output_tokens": 500}),
        ])
        _usage, model, _cost = dg._transcript_usage_summary(sid)
        self.assertEqual(model, "claude-opus-4-8")   # most output tokens wins


if __name__ == "__main__":
    unittest.main()
