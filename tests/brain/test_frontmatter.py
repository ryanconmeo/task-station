"""core.frontmatter — emitter/parser round-trips for the generic ``---`` block.

PROVENANCE: the pure half of the source's ``tests/test_note_io.py`` @ 0.14.0
(class ``FrontmatterRoundTripTest``). Only ONE of its three cases is
note-semantics-free — the hostile-scalar round trip — and that is ported verbatim
below. Its other two cases drive ``write_note`` (a hostile description through a
real note file, and "a raw ': ' is quoted on disk"), so they port alongside
``lib/brain/notes.py`` in Phase 4 chunk 2, not here.

ADDED (each marked): direct coverage for dump_frontmatter / parse_note /
render_note / emit_value / parse_value and the new ``order`` parameter. The source
only exercised those through the note writer's baked-in key order, and ``order``
is this port's one signature change — it needs its own proof.
"""
import unittest

from tests.brain.base import BrainTestCase  # noqa: F401 — puts lib/ on sys.path

import core.frontmatter as fm


class ScalarRoundTripTest(unittest.TestCase):
    def test_scalar_round_trip_hostile(self):
        hostile = [
            "plain",
            "has: a colon",
            "trailing space ",
            " leading space",
            'quote " inside',
            "hash # comment-ish",
            "yaml {braces} and [brackets]",
            "single ' quote",
            "emoji 🚀 ✅ 🧠",
            "combo: \"q\" # 🚀 {x}",
            "",
        ]
        for v in hostile:
            emitted = fm.emit_scalar(v)
            self.assertEqual(fm.parse_scalar(emitted), v, f"round-trip failed for {v!r}")


class ValueRoundTripTest(unittest.TestCase):
    """ADDED — the structured (list/dict) values, emitted as single-line JSON."""

    def test_string_list_round_trip(self):
        v = ["billing", "ledger"]
        self.assertEqual(fm.parse_value(fm.emit_value(v)), v)

    def test_record_list_round_trip(self):
        v = [{"alias": "a", "ts": "2026-08-13", "extent": "created"}]
        self.assertEqual(fm.parse_value(fm.emit_value(v)), v)

    def test_emitted_structure_is_single_line(self):
        emitted = fm.emit_value([{"alias": "a", "ts": "2026-08-13"}, "x"])
        self.assertNotIn("\n", emitted)

    def test_lenient_flow_list_when_not_strict_json(self):
        # a hand-authored [foo, bar] is not JSON — degrade to a string list
        self.assertEqual(fm.parse_value("[foo, bar]"), ["foo", "bar"])

    def test_bracketed_prose_stays_a_scalar(self):
        # '[draft] ...' opens with '[' but is not a list — must stay a string
        self.assertEqual(fm.parse_value("[draft] a description"), "[draft] a description")


class DumpFrontmatterTest(unittest.TestCase):
    """ADDED — block-level emit, including the ``order`` parameter this port added
    in place of the source's hard-coded note key order."""

    def test_block_is_fenced(self):
        out = fm.dump_frontmatter({"name": "n"})
        self.assertEqual(out, "---\nname: n\n---\n")

    def test_colon_value_is_quoted(self):
        # the invariant the source asserted on disk, asserted at the block level
        out = fm.dump_frontmatter({"description": "a: b"})
        self.assertIn('description: "a: b"', out.splitlines())

    def test_no_order_is_insertion_order(self):
        out = fm.dump_frontmatter({"z": "1", "a": "2"})
        self.assertEqual(out.splitlines()[1:3], ["z: 1", "a: 2"])

    def test_order_first_then_insertion_order(self):
        out = fm.dump_frontmatter({"z": "1", "name": "n", "description": "d"},
                                  order=["name", "description"])
        self.assertEqual(out.splitlines()[1:4], ["name: n", "description: d", "z: 1"])

    def test_order_keys_absent_from_fm_are_skipped(self):
        out = fm.dump_frontmatter({"name": "n"}, order=["name", "verified", "source"])
        self.assertEqual(out, "---\nname: n\n---\n")


class ParseNoteTest(unittest.TestCase):
    """ADDED — document split, and the two documented tolerances."""

    def test_no_fence_returns_text_unchanged(self):
        got_fm, body = fm.parse_note("no fence here\n")
        self.assertEqual(got_fm, {})
        self.assertEqual(body, "no fence here\n")

    def test_indented_lines_are_not_keys(self):
        # the harness memory-note shape: a nested block's indented lines are
        # ignored for keying (the parser never guesses structure)
        text = ("---\nname: x\ndescription: d\nmetadata:\n  type: reference\n---\n"
                "\nbody\n")
        got_fm, body = fm.parse_note(text)
        self.assertEqual(got_fm["name"], "x")
        self.assertEqual(got_fm["metadata"], "")
        self.assertNotIn("type", got_fm)
        self.assertEqual(body, "body\n")

    def test_unterminated_fence_yields_empty_body(self):
        got_fm, body = fm.parse_note("---\nname: x\n")
        self.assertEqual(got_fm["name"], "x")
        self.assertEqual(body, "")


class RenderNoteTest(unittest.TestCase):
    """ADDED — render/parse is an inverse pair for a hostile document."""

    def test_render_parse_round_trip(self):
        original = {"name": "n",
                    "description": 'Dev SQL: use the "per-IP" rule only 🧠 {off-VPN}',
                    "tags": ["billing", "ledger"]}
        text = fm.render_note(original, "body with [[wikilink]]")
        got_fm, body = fm.parse_note(text)
        self.assertEqual(got_fm, original)
        self.assertEqual(body, "body with [[wikilink]]\n")

    def test_empty_body_emits_no_body_block(self):
        self.assertEqual(fm.render_note({"name": "n"}, ""), "---\nname: n\n---\n\n")

    def test_order_is_passed_through(self):
        text = fm.render_note({"z": "1", "name": "n"}, "b", order=["name"])
        self.assertEqual(text.splitlines()[1:3], ["name: n", "z: 1"])


if __name__ == "__main__":
    unittest.main()
