"""A HANDOFF IS ADDRESSED TO ONE SESSION, so it is stored under that session's name.

WHAT WENT WRONG. `write_handoff` computed one path per TASK — `<seq>-CONTINUATION.md` —
and opened it `"w"`, an unconditional truncate. Two relays on one task therefore wrote one
file, and the second replaced a handoff the first successor had not read yet. Nothing in
the relay could have closed that window by ordering its own writes differently: the read
happens in ANOTHER PROCESS, minutes later, after the successor's own SessionStart, because
every relayed session is launched pre-attached with nothing loaded and told to read first.

IT HAD ALREADY HAPPENED TWICE. `<data>/handoff/` carried `444-CONTINUATION.hand-2026-08-31
.md` and `444-CONTINUATION.md.bak-2026-08-31` — two files renamed BY HAND to free the
generated path for the next relay. A defect that has forced two manual workarounds is not
a risk.

WHAT THIS FILE PINS, one class per guarantee:

  1. THE NAME CARRIES THE SUCCESSOR (`TheNameCarriesTheSuccessor`). Two successors on one
     task name two files, and the first is byte-identical after the second is written.
     Not a lock — the writers were never the problem. Not a timestamp suffix — that leaves
     the reader with the same question, which of these is mine.

  2. THE STABLE NAME IS A POINTER (`TheStableNameIsAPointer`). `<seq>-CONTINUATION.md`
     keeps resolving, because pinned decision 444:511 tells a cold session to open exactly
     that path by hand. It resolves to the NEWEST handoff and stores none: a second copy
     would reintroduce the staleness the per-successor name just removed.

  3. THE HEADER NAMES ITS READER (`TheHeaderNamesItsReader`). Successor, predecessor and
     WRITE TIME, in prose, in the header block — enough for a human who typed the path to
     tell a live handoff from a spent one without consulting the roster, and no
     machine-readable preamble, because the reader is a model reading prose.

THE ASSERTIONS ARE ABOUT WHAT A READER GETS, never about the shape of the writer: every
one of them writes real files with the shipped functions and reads them back off disk.
"""
import os
import sys
import tempfile
import time
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(_REPO_ROOT, "lib")
sys.path.insert(0, LIB)
sys.path.insert(0, os.path.join(LIB, "board"))
os.environ.setdefault("TASK_STATION_HOME", tempfile.mkdtemp(prefix="ts-handoff-per-"))

import succession as _succ        # noqa: E402

FIRST = "aaaa1111-0000-0000-0000-00000000000a"
SECOND = "bbbb2222-0000-0000-0000-00000000000b"


class _PerSuccessorTest(unittest.TestCase):
    """A real handoff directory on disk, and a task dict the generator is pure over."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-per-successor-")

    def _task(self, seq=622):
        return {"seq": seq, "id": "c" * 32,
                "state": "NEXT: land the path change in lib/board/succession.py.",
                "steps": [{"text": "wire the name", "done": False}]}

    def _write(self, task, sid, successor):
        """One relay's write, end to end through both shipped functions."""
        prompt = _succ.continuation_prompt(task, predecessor="622-0",
                                           successor=successor)
        return _succ.write_handoff(task, prompt, sid, root=self.tmp)

    def _read(self, path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def _stable(self, task):
        return _succ.stable_handoff_path(task, root=self.tmp)

    def _link(self, task, path):
        return _succ.link_handoff(task, path, root=self.tmp)


class TheNameCarriesTheSuccessor(_PerSuccessorTest):

    def test_two_successors_on_one_task_get_two_files(self):
        task = self._task()
        first = self._write(task, FIRST, "622-1")
        second = self._write(task, SECOND, "622-2")
        self.assertNotEqual(first, second)
        self.assertTrue(os.path.isfile(first), first)
        self.assertTrue(os.path.isfile(second), second)

    def test_the_first_successors_handoff_survives_the_second_relay(self):
        """THE DEFECT ITSELF, asserted directly and on the bytes. Before this change the
        second write truncated the first file and the first successor — which had not
        read yet — woke to a handoff addressed to somebody else."""
        task = self._task()
        first = self._write(task, FIRST, "622-1")
        before = open(first, "rb").read()
        self._write(task, SECOND, "622-2")
        self.assertEqual(open(first, "rb").read(), before)

    def test_each_file_is_addressed_to_the_session_it_is_named_after(self):
        """The name is only useful if it agrees with the contents — a reader checks the
        path against its own session id and stops there."""
        task = self._task()
        for sid, who in ((FIRST, "622-1"), (SECOND, "622-2")):
            path = self._write(task, sid, who)
            self.assertIn(sid[:8], os.path.basename(path))
            self.assertIn("you are session %s" % who, self._read(path))

    def test_a_handoff_addressed_to_nobody_is_refused(self):
        """No successor means no name, and the per-task name is what collided. A default
        here would put it back on the one path that has no session yet."""
        for nobody in (None, ""):
            with self.assertRaises(ValueError):
                _succ.write_handoff(self._task(), "anything", nobody, root=self.tmp)


class TheStableNameIsAPointer(_PerSuccessorTest):

    def test_the_stable_name_reads_as_the_newest_handoff(self):
        task = self._task()
        self._link(task, self._write(task, FIRST, "622-1"))
        second = self._write(task, SECOND, "622-2")
        self._link(task, second)
        self.assertEqual(self._read(self._stable(task)), self._read(second))
        self.assertIn("you are session 622-2", self._read(self._stable(task)))

    def test_it_stores_no_second_copy_of_the_handoff(self):
        """THE OBSERVABLE IS THAT THERE IS NOTHING TO DRIFT: the stable name follows the
        file it points at, so a change to the handoff is a change to what the stable
        name reads. A copy would answer the old text here, which is exactly the
        staleness the per-successor name removed."""
        task = self._task()
        path = self._write(task, FIRST, "622-1")
        self._link(task, path)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("A LINE ADDED AFTER THE POINTER WAS MADE.\n")
        self.assertIn("A LINE ADDED AFTER THE POINTER WAS MADE",
                      self._read(self._stable(task)))

    def test_a_handoff_written_before_this_release_is_moved_aside_not_destroyed(self):
        """Every task that has relayed carries a REAL FILE at the stable name, and it is
        the only copy of that handoff. Replacing it with a pointer must not delete it."""
        task = self._task()
        stable = self._stable(task)
        os.makedirs(os.path.dirname(stable), exist_ok=True)
        with open(stable, "w", encoding="utf-8") as fh:
            fh.write("THE OLD HANDOFF, whose only copy this is.\n")
        self._link(task, self._write(task, FIRST, "622-1"))
        survivors = [self._read(os.path.join(self.tmp, n))
                     for n in os.listdir(self.tmp)
                     if not os.path.islink(os.path.join(self.tmp, n))]
        self.assertTrue(any("THE OLD HANDOFF" in s for s in survivors), survivors)

    def test_a_stable_name_that_cannot_be_pointed_raises(self):
        """The caller degrades to a NAMED SKIP, which it can only do if the failure
        reaches it. Something else occupying the name is the case that cannot be fixed
        by retrying, so it must not be swallowed here."""
        task = self._task()
        os.makedirs(self._stable(task), exist_ok=True)
        with open(os.path.join(self._stable(task), "in-the-way"), "w") as fh:
            fh.write("a directory where the pointer has to go\n")
        with self.assertRaises(OSError):
            self._link(task, self._write(task, FIRST, "622-1"))


class TheHeaderNamesItsReader(_PerSuccessorTest):

    NOW = 1756900000.0        # a fixed clock, so the stamp is a fact and not a race

    def _header(self, **kw):
        text = _succ.continuation_prompt(self._task(), predecessor="622-0",
                                         successor="622-1", now=self.NOW, **kw)
        return text.splitlines()

    def test_the_header_carries_the_successor_the_predecessor_and_the_write_time(self):
        head = "\n".join(self._header()[:4])
        self.assertIn("you are session 622-1", head)
        self.assertIn("succeeding 622-0", head)
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(self.NOW))
        self.assertIn(stamp, head)

    def test_the_write_time_tells_a_hand_reader_the_file_may_be_spent(self):
        """The whole point of the stamp: a human who typed the stable path is told what
        to do when the time is old, rather than being left to compare it to a roster."""
        head = "\n".join(self._header()[:4]).lower()
        self.assertIn("spent", head)

    def test_it_adds_no_machine_readable_preamble(self):
        """A successor is a model reading prose. Front-matter, a checksum or a typed
        header would be machinery serving no constraint (444:642)."""
        lines = self._header()
        self.assertFalse(lines[0].startswith("---"), lines[0])
        self.assertTrue(lines[0].startswith("RELAY on task #622"), lines[0])


if __name__ == "__main__":
    unittest.main()
