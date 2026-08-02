"""Task 6: golden-file test for the deterministic brief renderer.

Renders the collation brief-spec (which exercises EVERY section — decision,
transition, glossary auto, one svg diagram, one_rule, a 2-phase plan, an ADO
Feature→Story→PR tree, and provenance) and byte-compares it to a committed golden
HTML. On the FIRST run (golden absent) it writes the golden and passes; every
subsequent run asserts byte-equality, catching any accidental style/render drift."""
import importlib.util
import json
import os
import sys
import unittest

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)
import brief  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
SPEC_PATH = os.path.join(FIX, "collation_brief_spec.json")
GOLDEN_PATH = os.path.join(FIX, "collation_brief_golden.html")

# The task's canonical vocabulary (glossary "auto" pulls this in at render time).
GLOSSARY = [
    {"name": "Mixed-Collation Store", "layer": "db", "state": "today",
     "def": "Keys case-sensitive, everything else not. Where the two meet is what throws the errors."},
    {"name": "Binary-Default (BIN2) Store", "layer": "db", "state": "target",
     "def": "All text case-sensitive by default. The error class disappears — but search must be made case-insensitive on purpose."},
    {"name": "Inline Collation", "layer": "app", "state": "today",
     "def": "Each query hand-adds case-handling — ~400 scattered spots. Easy to miss; the cause of the repeats."},
    {"name": "Centralized Search Layer", "layer": "app", "state": "target",
     "def": "One module all search flows through. Case-insensitivity lives in exactly one place."},
    {"name": "Collation Gate", "layer": "CI", "state": "shipped",
     "def": "A build check that blocks anyone reintroducing the problem or bypassing the layer."},
    {"name": "Persisted CI Index", "layer": "db", "state": "",
     "def": "A stored, indexed case-insensitive copy of a column — keeps search fast on a Binary-Default (BIN2) Store."},
]


class GoldenBriefTest(unittest.TestCase):
    def test_collation_brief_matches_golden(self):
        with open(SPEC_PATH, encoding="utf-8") as f:
            spec = json.load(f)
        html = brief.render_brief(spec, GLOSSARY)

        if not os.path.exists(GOLDEN_PATH):
            with open(GOLDEN_PATH, "w", encoding="utf-8") as f:
                f.write(html)
            return  # first run: golden written, nothing to compare against yet

        with open(GOLDEN_PATH, encoding="utf-8") as f:
            expected = f.read()
        self.assertEqual(
            html, expected,
            "brief render drifted from the golden. If the change is intentional, "
            "delete tests/fixtures/collation_brief_golden.html and re-run to regenerate.")

    def test_golden_exercises_every_section(self):
        # guard: the golden must actually contain each frozen section
        with open(GOLDEN_PATH, encoding="utf-8") as f:
            g = f.read()
        for marker in ('class="banner"', "Where we are", "The vocabulary",
                       "<figure>", "The one rule", "The plan",
                       "<h2>ADO structure</h2>", 'class="foot"'):
            self.assertIn(marker, g, "golden missing section marker: %r" % marker)


if __name__ == "__main__":
    unittest.main()
