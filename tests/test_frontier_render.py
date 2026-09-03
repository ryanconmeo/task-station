"""The frontier-bounded tiered render (3.56.0) — #580's answer.

#580 halved #444's digest by hand (412,114 -> 199,967) and then measured that its own
60,000 target was UNREACHABLE by consolidation: the remaining ~190,000 is load-bearing.
It left two options, and both are worse than this one. Accepting the floor makes every
session pay ~50,000 tokens to start. MOVING subject-owned decisions onto their children
automates exactly the misclassification #586 measured at one-in-three.

So bound the RENDER, not the record. Nothing moves, nothing is deleted, nothing is
reclassified — and decision 93's ban on hiding current decisions is respected, because a
numbered stub that says what it is and can be opened is not hidden. It is the same shape
the codebase already accepted for inherited pins (view.py, "one hop away instead of
48,000 chars in front of every session").
"""
import os
import sys
import tempfile
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(_REPO_ROOT, "lib")
sys.path.insert(0, LIB)
sys.path.insert(0, os.path.join(LIB, "board"))
os.environ.setdefault("TASK_STATION_HOME", tempfile.mkdtemp(prefix="ts-frontier-"))

import decisions as dec          # noqa: E402


LONG = "x" * 900
SHORT = "y" * 50


class TestTheTripWire(unittest.TestCase):
    """SIZE decides WHETHER to tier. It never decides WHAT to tier."""

    def test_under_budget_nothing_is_tiered(self):
        ents = [{"text": LONG}, {"text": LONG}]
        full, stub, tiered = dec.frontier_tiers(ents, budget=999999)
        self.assertFalse(tiered)
        self.assertEqual(len(full), 2)
        self.assertEqual(stub, [])

    def test_budget_zero_disables_tiering_entirely(self):
        # The escape hatch. A knob whose off-switch silently falls back to the default
        # is the worst kind: the user turns it off, the tool agrees, nothing changes.
        ents = [{"text": LONG}, {"text": LONG}]
        full, stub, tiered = dec.frontier_tiers(ents, budget=0)
        self.assertFalse(tiered)
        self.assertEqual(stub, [])

    def test_over_budget_tiering_switches_on(self):
        ents = [{"text": LONG}, {"text": LONG}]
        _full, stub, tiered = dec.frontier_tiers(ents, budget=100)
        self.assertTrue(tiered)
        self.assertTrue(stub)


class TestDeclarationDecidesTheTier(unittest.TestCase):
    """Four ways into the FULL tier, and every one is a fact the AUTHOR recorded."""

    def test_pinned_stays_full(self):
        self.assertTrue(dec.renders_in_full({"text": LONG, "pinned": True}))

    def test_declared_ruling_stays_full(self):
        self.assertTrue(dec.renders_in_full({"text": LONG, "kind": "ruling"}))

    def test_short_stays_full_because_a_stub_would_save_nothing(self):
        self.assertTrue(dec.renders_in_full({"text": SHORT}))

    def test_long_undeclared_is_stubbed(self):
        self.assertFalse(dec.renders_in_full({"text": LONG}))

    def test_neither_age_nor_order_is_consulted(self):
        # Two identical undeclared entries must tier identically no matter where they
        # sit. Age is forbidden by decision 93 AND unavailable — 14 of 613 are dateable.
        ents = [{"text": LONG}, {"text": LONG}, {"text": LONG}]
        _full, stub, _t = dec.frontier_tiers(ents, budget=100)
        self.assertEqual(len(stub), 3)


class TestOpenSubjectKeepsADecisionFull(unittest.TestCase):

    def test_open_subject_renders_full(self):
        e = {"text": LONG, "subject": [{"task": "532"}]}
        self.assertTrue(dec.renders_in_full(e, is_open=lambda ref: True))

    def test_finished_subject_is_stubbed(self):
        e = {"text": LONG, "subject": [{"task": "532"}]}
        self.assertFalse(dec.renders_in_full(e, is_open=lambda ref: False))

    def test_an_unresolvable_subject_fails_TOWARDS_full(self):
        # The two mistakes are not symmetric: a finished decision shown costs scrolling;
        # a live one stubbed costs the reader a constraint they were about to violate.
        e = {"text": LONG, "subject": [{"task": "999"}]}

        def boom(_ref):
            raise RuntimeError("store hiccup")

        self.assertTrue(dec.renders_in_full(e, is_open=boom))
        self.assertTrue(dec.renders_in_full(e, is_open=None))


class TestNothingIsLost(unittest.TestCase):

    def test_every_current_decision_appears_in_exactly_one_tier(self):
        ents = [{"text": LONG}, {"text": LONG, "pinned": True},
                {"text": SHORT}, {"text": LONG, "kind": "ruling"}]
        full, stub, _t = dec.frontier_tiers(ents, budget=100)
        idx = sorted([i for i, _ in full] + [i for i, _ in stub])
        self.assertEqual(idx, [1, 2, 3, 4])

    def test_replaced_decisions_stay_out_of_both_tiers(self):
        # A replaced decision is not "old", it is no longer true. It was already absent
        # from digest_order and must not reappear as a stub.
        ents = [{"text": LONG, "superseded_by": 2}, {"text": LONG}]
        full, stub, _t = dec.frontier_tiers(ents, budget=100)
        self.assertEqual([i for i, _ in full] + [i for i, _ in stub], [2])

    def test_a_stub_is_derived_and_non_empty(self):
        body = "THE SUBJECT IS STATED FIRST. Then a great deal of elaboration follows."
        self.assertTrue(dec.derive_stub(body, dec.DIGEST_STUB_CHARS).startswith("THE SUBJECT"))


if __name__ == "__main__":
    unittest.main()
