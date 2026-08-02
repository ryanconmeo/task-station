"""WS7 usage-data layer: the never-`$n/a` cost fallback chain (`_stats_cost`), the
`__v` phase-segment filter, and the CLI usage render's freedom from the `$n/a`
literal. These are pure/near-pure units — no store, no transcripts — so they load the
hyphenated module directly and call the helpers with hand-built ledger dicts."""
import importlib.util
import os
import sys
import unittest

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


class StatsCostTest(unittest.TestCase):
    """The four-rung fallback chain: derived → reported → floor → $0.00, never n/a."""

    def test_fully_priced_is_derived(self):
        sc = ts._stats_cost({"total_cost_usd": 4.12, "reported_cost_usd": 0.0,
                             "any_unpriced": False})
        self.assertEqual(sc, {"text": "$4.12", "kind": "derived", "usd": 4.12})

    def test_unpriced_with_reported_is_reported(self):
        sc = ts._stats_cost({"total_cost_usd": 0.0, "reported_cost_usd": 3.5,
                             "any_unpriced": True})
        self.assertEqual(sc["kind"], "reported")
        self.assertEqual(sc["text"], "$3.50 reported")

    def test_unpriced_priced_subtotal_is_floor(self):
        sc = ts._stats_cost({"total_cost_usd": 1.25, "reported_cost_usd": 0.0,
                             "any_unpriced": True})
        self.assertEqual(sc["kind"], "floor")
        self.assertEqual(sc["text"], "≥$1.25")          # ≥$1.25

    def test_reported_wins_over_floor_when_no_priced_total(self):
        # any_unpriced, total 0, reported present → reported (not floor / not n/a).
        sc = ts._stats_cost({"total_cost_usd": 0.0, "reported_cost_usd": 2.0,
                             "any_unpriced": True})
        self.assertEqual(sc["kind"], "reported")

    def test_nothing_at_all_is_zero(self):
        sc = ts._stats_cost({"total_cost_usd": 0.0, "reported_cost_usd": 0.0,
                             "any_unpriced": True})
        self.assertEqual(sc, {"text": "$0.00", "kind": "derived", "usd": 0.0})

    def test_no_literal_na_in_any_branch(self):
        for data in ({"any_unpriced": True, "total_cost_usd": 0.0, "reported_cost_usd": 0.0},
                     {"any_unpriced": True, "total_cost_usd": 1.0, "reported_cost_usd": 0.0},
                     {"any_unpriced": True, "total_cost_usd": 0.0, "reported_cost_usd": 9.9},
                     {"any_unpriced": False, "total_cost_usd": 7.0, "reported_cost_usd": 1.0}):
            self.assertNotIn("n/a", ts._stats_cost(data)["text"])

    def test_garbage_inputs_degrade_to_zero(self):
        sc = ts._stats_cost({"total_cost_usd": None, "reported_cost_usd": "x",
                             "any_unpriced": False})
        self.assertEqual(sc["text"], "$0.00")


class PhaseSegmentVersionFilterTest(unittest.TestCase):
    def test_phase_segments_never_emit_version_stamp(self):
        rows = [{"phases": {"__v": 2,
                            "implementation": {"out": 300, "cost_usd": 3.0},
                            "research": {"out": 100, "cost_usd": 1.0},
                            "bogus": {"out": 50}}}]
        segs = ts._phase_segments(rows)
        labels = {s["label"] for s in segs}
        self.assertNotIn("__v", labels)                      # version stamp filtered
        self.assertNotIn("bogus", labels)                    # non-phase key filtered
        self.assertEqual(labels, {"implementation", "research"})
        self.assertAlmostEqual(sum(s["pct"] for s in segs), 1.0, places=6)

    def test_empty_when_only_version_stamp(self):
        self.assertEqual(ts._phase_segments([{"phases": {"__v": 2}}]), [])


class RenderUsageNoNaTest(unittest.TestCase):
    """The terminal `usage --task` render never prints the `$n/a` literal — an
    unknown-model session shows `(unpriced)` / `—`, and the totals fall back to the
    reported $ (or a `≥` floor)."""

    def _data(self, **kw):
        base = {
            "models": {"unknown-x": {"in": 0, "out": 100, "cache_read": 0,
                                     "cache_w5m": 0, "cache_w1h": 0, "web": 0,
                                     "msgs": 1, "cost_usd": None, "pct": 1.0}},
            "phases": {},
            "sessions": [{"sid": "s1abcd", "role": "hub", "label": None, "in": 0,
                          "out": 100, "cache_read": 0, "cost_usd": None}],
            "total_cost_usd": 0.0, "reported_cost_usd": 4.0, "any_unpriced": True,
            "derived_note": "note without the literal",
        }
        base.update(kw)
        return base

    def test_unpriced_render_has_no_na(self):
        txt = ts._render_usage({"id": "abcd1234ef", "seq": 1, "title": "t"}, self._data())
        self.assertNotIn("n/a", txt)
        self.assertIn("(unpriced)", txt)                     # per-model marker
        self.assertIn("$4.00 reported", txt)                 # totals reported fallback
        # per-session unpriced cost renders as an em-dash, not $n/a.
        self.assertIn("—", txt)

    def test_floor_render_when_partial_priced(self):
        txt = ts._render_usage({"id": "abcd1234ef", "seq": 1, "title": "t"},
                               self._data(total_cost_usd=2.5, reported_cost_usd=0.0))
        self.assertNotIn("n/a", txt)
        self.assertIn("≥$2.50 derived", txt)            # ≥$2.50 floor


if __name__ == "__main__":
    unittest.main()
