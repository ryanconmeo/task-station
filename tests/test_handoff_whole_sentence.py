"""The relay sends the move whole instead of clipping the state mid-word (3.58.0).

`continuation_prompt` called `leads_with_next(state)` — which returns a BOOLEAN — and
then clipped the ENTIRE state at 320 characters. So it learned that a first-move sentence
was there and threw the boundary away, spending most of its budget on standing detail the
successor reads in the digest anyway, and cutting mid-word on the way.

THE CAP DOES NOT MOVE, and that is the point. #583's guard pins NEXT_CHARS at 320 because
raising it turns a relay prompt into the context dump the design exists to avoid. Sending
the MOVE instead of a prefix of the whole state makes a long state's prompt SHORTER, so
the fix needs no cap at all: measured on #444's real 3,259-char state line, the prompt goes
from 320 chars ending in an ellipsis to 120 chars ending in a full stop.
"""
import os
import sys
import tempfile
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(_REPO_ROOT, "lib")
sys.path.insert(0, LIB)
sys.path.insert(0, os.path.join(LIB, "board"))
os.environ.setdefault("TASK_STATION_HOME", tempfile.mkdtemp(prefix="ts-handoff-"))

import save as _save              # noqa: E402
import succession as _succ        # noqa: E402


def _prompt_line(state):
    """What the relay would put on the state line."""
    return _succ._clip(_save.next_line(state) or state, _succ.NEXT_CHARS)


class TestTheCapIsUntouched(unittest.TestCase):
    """The fix must not be a widening — #583 fails anyone who raises these."""

    def test_the_pinned_numbers_are_unchanged(self):
        self.assertEqual(_succ.NEXT_CHARS, 320)
        self.assertEqual(_succ.PROMPT_BUDGET, 1600)


class TestTheMoveTravelsWhole(unittest.TestCase):

    def test_a_long_state_yields_a_complete_sentence_with_no_ellipsis(self):
        state = ("NEXT: FIX THE TRUNCATED HANDOFF — it is small and well understood. "
                 + "Standing detail that the digest already carries. " * 60)
        self.assertGreater(len(state), 2000)
        line = _prompt_line(state)
        self.assertFalse(line.endswith("…"))
        self.assertTrue(line.endswith("."))
        self.assertLess(len(line), _succ.NEXT_CHARS)

    def test_the_standing_report_after_the_move_is_dropped(self):
        state = "NEXT: do the one thing that matters. Standing: everything else is fine."
        self.assertEqual(_prompt_line(state), "NEXT: do the one thing that matters.")

    def test_only_the_first_paragraph_is_considered(self):
        state = "NEXT: do the thing.\n\nA whole second paragraph of standing report."
        self.assertEqual(_prompt_line(state), "NEXT: do the thing.")


class TestItDoesNotSplitOnThingsThatAreNotSentences(unittest.TestCase):
    """A move cut at `3.` is worse than no fix at all."""

    def test_a_version_number_does_not_end_the_sentence(self):
        self.assertEqual(_prompt_line("NEXT: ship 3.57.0 to main, then verify. Standing."),
                         "NEXT: ship 3.57.0 to main, then verify.")

    def test_a_filename_does_not_end_the_sentence(self):
        self.assertEqual(_prompt_line("NEXT: patch succession.py and re-run. Standing."),
                         "NEXT: patch succession.py and re-run.")

    def test_an_early_abbreviation_does_not_end_the_sentence(self):
        # `e.g.` DOES have a space after it, so the whitespace rule cannot catch it —
        # the length floor is what does.
        line = _prompt_line("NEXT: fix e.g. this one thing. Then everything else.")
        self.assertTrue(line.startswith("NEXT: fix e.g. this one thing"))


class TestItCanNeverGrowUnbounded(unittest.TestCase):

    def test_a_move_with_no_terminator_is_still_clipped(self):
        line = _prompt_line("NEXT: " + "x" * 500)
        self.assertEqual(len(line), _succ.NEXT_CHARS)
        self.assertTrue(line.endswith("…"))

    def test_a_state_with_no_next_prefix_falls_back_to_the_bounded_clip(self):
        state = "Standing report with no move at all. " * 40
        line = _prompt_line(state)
        self.assertEqual(_save.next_line(state), "")
        self.assertLessEqual(len(line), _succ.NEXT_CHARS)


class TestTheClipNeverCutsAWordInHalf(unittest.TestCase):

    def test_it_breaks_at_a_word_boundary(self):
        text = "alpha bravo charlie delta echo foxtrot golf hotel india juliet"
        out = _succ._clip(text, 30)
        self.assertTrue(out.endswith("…"))
        self.assertNotIn("charli…", out)          # no half word
        self.assertTrue(out[:-1].rstrip().split()[-1] in text.split())

    def test_a_single_enormous_word_still_gets_bounded(self):
        out = _succ._clip("x" * 100, 30)
        self.assertEqual(len(out), 30)


if __name__ == "__main__":
    unittest.main()
