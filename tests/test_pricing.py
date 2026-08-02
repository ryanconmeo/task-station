"""Per-message cost derivation (lib/pricing.py): the rate table keyed on
(model family/version, speed), the date-dependent Sonnet 5 intro window, the
cache-write TTL split, fast-mode sheets, the inference_geo uplift, and the
unknown-model → None contract.

Rates asserted here are the ones verified 2026-07-04 against the docs pricing
page; a change to lib/pricing.py's table must update these on purpose."""
import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import pricing  # noqa: E402


# The formula-exercising usage object from the WS1 brief (opus-4-8 → $12.405 std).
_USAGE = {
    "input_tokens": 1_000_000,
    "output_tokens": 100_000,
    "cache_read_input_tokens": 2_000_000,
    "cache_creation_input_tokens": 500_000,
    "cache_creation": {"ephemeral_1h_input_tokens": 200_000,
                       "ephemeral_5m_input_tokens": 300_000},
    "server_tool_use": {"web_search_requests": 3},
}

_INTRO_TS = datetime(2026, 7, 4, tzinfo=timezone.utc).timestamp()   # inside intro window
_POST_TS = datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp()   # after 2026-09-01


class RatesTest(unittest.TestCase):
    def test_fable_tier(self):
        r = pricing.rates_for("claude-fable-5")
        self.assertEqual((r["in"], r["out"]), (10.0, 50.0))
        # Uniform cache multipliers off the input rate.
        self.assertEqual((r["w5m"], r["w1h"], r["read"]), (12.5, 20.0, 1.0))

    def test_opus_current_tier(self):
        r = pricing.rates_for("claude-opus-4-8")
        self.assertEqual((r["in"], r["out"]), (5.0, 25.0))
        self.assertEqual((r["w5m"], r["w1h"], r["read"]), (6.25, 10.0, 0.5))

    def test_opus_legacy_tier(self):
        r = pricing.rates_for("claude-opus-4-1")
        self.assertEqual((r["in"], r["out"]), (15.0, 75.0))
        r0 = pricing.rates_for("claude-opus-4-0")
        self.assertEqual((r0["in"], r0["out"]), (15.0, 75.0))

    def test_haiku_tiers(self):
        self.assertEqual(pricing.rates_for("claude-haiku-4-5")["in"], 1.0)
        self.assertEqual(pricing.rates_for("claude-haiku-3-5")["in"], 0.8)

    def test_sonnet_intro_vs_standard_by_ts(self):
        self.assertEqual(pricing.rates_for("claude-sonnet-5", ts=_INTRO_TS)["in"], 2.0)
        self.assertEqual(pricing.rates_for("claude-sonnet-5", ts=_POST_TS)["in"], 3.0)
        # Sonnet 4.x always bills the standard sheet regardless of date.
        self.assertEqual(pricing.rates_for("claude-sonnet-4-5", ts=_INTRO_TS)["in"], 3.0)

    def test_unknown_model_is_none(self):
        self.assertIsNone(pricing.rates_for("gpt-4o"))
        self.assertIsNone(pricing.rates_for(""))


class MessageCostTest(unittest.TestCase):
    def test_opus_48_standard_matches_brief(self):
        cost = pricing.message_cost("claude-opus-4-8", _USAGE, ts=_INTRO_TS)
        self.assertAlmostEqual(cost, 12.405, places=6)

    def test_fast_mode_uses_fast_sheet(self):
        u = dict(_USAGE, speed="fast")
        cost = pricing.message_cost("claude-opus-4-8", u, ts=_INTRO_TS)
        # 10 + 5 + 2 + (0.2·20 + 0.3·12.5) + 0.03 = 24.78
        self.assertAlmostEqual(cost, 24.78, places=6)

    def test_fast_mode_unknown_for_non_opus(self):
        u = dict(_USAGE, speed="fast")
        self.assertIsNone(pricing.message_cost("claude-fable-5", u))

    def test_inference_geo_us_uplift(self):
        u = dict(_USAGE, inference_geo="us")
        cost = pricing.message_cost("claude-opus-4-8", u, ts=_INTRO_TS)
        # geo multiplies the token part (12.375) only; the $0.03 web fee is flat.
        self.assertAlmostEqual(cost, 12.375 * 1.1 + 0.03, places=6)

    def test_unknown_model_cost_is_none(self):
        self.assertIsNone(pricing.message_cost("some-other-llm", _USAGE))

    def test_cache_write_split_defaults_to_5m(self):
        # No ephemeral_1h → the whole cache write bills at the 5m rate.
        u = {"cache_creation_input_tokens": 1_000_000}
        cost = pricing.message_cost("claude-opus-4-8", u, ts=_INTRO_TS)
        self.assertAlmostEqual(cost, 1_000_000 / 1e6 * 6.25, places=6)


class ModelFamilyTest(unittest.TestCase):
    def test_families(self):
        self.assertEqual(pricing.model_family("claude-fable-5"), "fable")
        self.assertEqual(pricing.model_family("claude-opus-4-8"), "opus")
        self.assertEqual(pricing.model_family("claude-sonnet-5"), "sonnet")
        self.assertEqual(pricing.model_family("claude-haiku-4-5"), "haiku")
        self.assertEqual(pricing.model_family("mystery"), "mystery")


class ContextWindowTest(unittest.TestCase):
    def test_one_million_variant(self):
        self.assertEqual(pricing.context_window_for("claude-opus-4-8[1m]"), 1000000)
        self.assertEqual(pricing.context_window_for("claude-sonnet-5-1m"), 1000000)

    def test_standard_window(self):
        self.assertEqual(pricing.context_window_for("claude-opus-4-8"), 200000)
        self.assertEqual(pricing.context_window_for("claude-haiku-4-5-20251001"), 200000)
        self.assertEqual(pricing.context_window_for("claude-sonnet-5"), 200000)

    def test_empty_and_unknown(self):
        self.assertEqual(pricing.context_window_for(""), 200000)
        self.assertEqual(pricing.context_window_for(None), 200000)
        # a date-like id with digits must NOT false-match the 1m marker
        self.assertEqual(pricing.context_window_for("claude-haiku-4-5-20251001"), 200000)


if __name__ == "__main__":
    unittest.main()
