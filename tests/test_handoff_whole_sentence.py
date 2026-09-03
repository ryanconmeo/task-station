"""A HANDOFF IS NEVER CUT — and from 3.61.0 that is true by construction (3.58.0, #619).

WHAT THIS FILE USED TO PIN, and why it changed shape rather than being deleted. The relay
built the successor's prompt as a COMMAND-LINE ARGUMENT, so `succession` carried a budget
and four clips to make any record fit one: PROMPT_BUDGET 1600, NEXT_CHARS 320,
STEP_CAP/STEP_CHARS 5/60, BLOCKER_CAP/BLOCKER_CHARS 5/70. Every one of those numbers was
correct reasoning from a false premise, and four separate fixes tuned them — 3.58.0's
included, which sent the first-move SENTENCE instead of a 320-character prefix so the cut
would at least land on a boundary. This file's old tests pinned that cap at 320 and asked
the sentence-splitter not to break on `3.57.0`.

THE GUARANTEE SURVIVES THE CONSTANTS. What 3.58.0 was actually promising is that a handoff
never arrives cut mid-word. The handoff is a FILE now and the launch argument is a pointer
to it, so there is no length to fit inside: the caps are DELETED rather than widened, and
the promise is checked the only way it can now be broken — by finding a truncation marker
anywhere in the file a successor is sent to read.

MEASURED, on #444's own record: the written handoff is 27,891 characters with ZERO
ellipses. The argv prompt it replaced was 1,387 characters with SIX.

AND THE SENTENCE-BOUNDARY MACHINERY IS GONE WITH THE CAP IT SERVED. `save.next_line`
existed for exactly one caller — this relay, needing the first-move sentence to fit inside
a 320-character launch argument. That constraint is deleted, so the function is too, along
with `_ABBREV_FLOOR` and the tests that pinned its boundary rules. Leaving a
sentence-boundary helper behind in `save.py` would be a FOSSIL of the argv: the next reader
who found it would reasonably infer the relay still trims, which is the same confusion the
caps created one module over. Git keeps it if a caller ever appears.
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

import succession as _succ        # noqa: E402

# Every marker that means "something was removed here". A file carrying any of them is a
# handoff that was cut, whatever produced it.
TRUNCATION_MARKERS = ("…", "...", "(trimmed", "more on the checklist",
                      "names them all", "[truncated")

# #444's real state line is 3,259 characters and its first line 541 — both far past every
# cap that used to exist, which is what made this the record the defect was reported from.
LONG_STATE = ("NEXT: FIX THE TRUNCATED HANDOFF — it is small and well understood. "
              + "Standing detail that the digest already carries. " * 60).strip()


class _HandoffTest(unittest.TestCase):
    """The generator is pure over a task dict, so these build one directly — no store,
    no session, no relay. The WRITER is the shipped one, pointed at a temp directory."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-handoff-file-")

    def _task(self, state=LONG_STATE, steps=20, chars=200):
        return {"seq": 619, "id": "a" * 32, "state": state,
                "steps": [{"text": "step %d %s" % (i, "y" * chars), "done": False}
                          for i in range(steps)]}

    def _written(self, task, **kw):
        """The handoff as the successor will read it: generated, written by the shipped
        writer, and read back off disk rather than asserted on in memory."""
        prompt = _succ.continuation_prompt(task, predecessor="619-0", successor="619-1",
                                           **kw)
        path = _succ.write_handoff(task, prompt, root=self.tmp)
        with open(path, encoding="utf-8") as fh:
            return fh.read()


class TestTheCapsAreGone(_HandoffTest):
    """A number that does not exist is the only one nobody can tune again."""

    def test_not_one_of_the_five_caps_or_the_clip_remains(self):
        for gone in ("PROMPT_BUDGET", "NEXT_CHARS", "STEP_CHARS", "STEP_CAP",
                     "BLOCKER_CHARS", "BLOCKER_CAP", "_clip"):
            self.assertFalse(hasattr(_succ, gone),
                             "%s is back — a handoff written to a file has no budget "
                             "to bound" % gone)


class TestNothingIsCutAnywhereInTheWrittenFile(_HandoffTest):

    def test_a_pathological_record_carries_no_truncation_marker_at_all(self):
        """Twenty 200-character steps, a 3,000-character state and twelve record gaps —
        each section past every cap that used to apply. THE ASSERTION IS THE ABSENCE of
        any marker meaning "something was removed", because that is the observable the
        four defect reports were made of."""
        text = self._written(self._task(),
                             blockers=["gap %d %s" % (i, "z" * 90) for i in range(12)],
                             rep={"window": 1000000, "used_pct": 81})
        self.assertGreater(len(text), 6000)
        for marker in TRUNCATION_MARKERS:
            self.assertNotIn(marker, text, "%r in the written handoff" % marker)

    def test_the_state_line_arrives_character_for_character(self):
        """The half of the complaint that was visible: a prompt ending "…the balance
        sheet recon" reads as a corrupted instruction. Identity is the only check that
        cannot be satisfied by a cleverer cut."""
        text = self._written(self._task())
        self.assertGreater(len(LONG_STATE), 2900)
        self.assertIn(LONG_STATE, text)

    def test_every_open_step_is_named_and_none_is_shortened(self):
        task = self._task()
        text = self._written(task)
        for step in task["steps"]:
            self.assertIn(step["text"], text)

    def test_every_record_gap_is_named(self):
        """The gaps are the FORCED path's whole point — a successor that cannot tell how
        incomplete its record is cannot close the difference. Five of twelve was the old
        behaviour, with a line saying so."""
        gaps = ["gap %d %s" % (i, "z" * 90) for i in range(12)]
        text = self._written(self._task(), blockers=gaps)
        for gap in gaps:
            self.assertIn(gap, text)

    def test_the_file_ends_on_a_complete_line(self):
        """A handoff cut by the writer rather than the generator would look exactly like
        one cut by the generator. The written file ends with the generated text's own
        last line and a single newline."""
        task = self._task()
        prompt = _succ.continuation_prompt(task, predecessor="619-0", successor="619-1",
                                           rep={"window": 1000000, "used_pct": 81})
        path = _succ.write_handoff(task, prompt, root=self.tmp)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.endswith("\n"))
        self.assertEqual(text.splitlines()[-1], prompt.splitlines()[-1])
        self.assertIn("81%", text)          # the tail the old clamp ate first


class TestTheWriterRefusesRatherThanDegrading(_HandoffTest):
    """A failed write RAISES. `relay` turns that into a refusal; what is pinned here is
    that the writer never returns a path it did not write, because a caller that got a
    plausible path back would spawn a successor pointed at nothing."""

    def test_an_unwritable_root_raises(self):
        blocked = os.path.join(self.tmp, "in-the-way")
        with open(blocked, "w") as fh:
            fh.write("a file where the directory has to be\n")
        with self.assertRaises(OSError):
            _succ.write_handoff(self._task(), "anything", root=blocked)


if __name__ == "__main__":
    unittest.main()
