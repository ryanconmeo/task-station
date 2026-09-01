"""TYPED DECISIONS, INCREMENT 1 — a decision declares its KIND and its SUBJECT at write
time, and an UNTYPED one behaves byte-identically to how it always has.

THE MEASUREMENT THIS FEATURE ANSWERS. A decision element carried `{text, superseded_by,
pinned, owner}` and nothing else, so every consumer re-derived structure from English:
`heal.py` is 4,940 lines against `decisions.py`'s 905, and it grows one vocabulary per
prose shape. The ceiling on guessing was earned by trying — a pass over 82 decisions
classified by subject keyword spot-checked at roughly ONE IN THREE right, which is why
the keyword placement tier was demoted to an informational re-read list.

THE TWO INVARIANTS EVERY TEST HERE IS A TEST OF:

  * AN UNTYPED DECISION IS UNCHANGED, PERMANENTLY. Not for a migration window. A legacy
    plain string round-trips as the identical plain string, `kind()` is None, `subject()`
    is [], and nothing infers a default. Untyped is not a kind.
  * A SUBJECT IS A QUALIFIED REF AND NEVER A BARE INTEGER. `heal.subject_signals` already
    labels every work item `PR/story <n>` from one bare number, so PR 27 and story 27
    emit ONE SIGNAL and can therefore COLLIDE. The collision is LATENT, not live: a scan
    of 131 live decisions found zero work-item numbers carrying more than one noun, so
    what exists is the capacity. A declared bare number would make that collision
    structural, in a field nothing downstream is permitted to doubt — and a wrong subject
    is therefore strictly worse than none.

Increment 1 is the element fields ONLY. No consumer reads them yet, which is why nothing
here touches heal, the digest or any projection.
"""
import os
import sys
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO_ROOT, "lib"))

import decisions as dec      # noqa: E402

LEGACY = "chose sqlite over flat files"


class UntypedIsUnchanged(unittest.TestCase):
    """The migration contract, which is one rule for every consumer."""

    def test_legacy_string_round_trips_byte_identically(self):
        # The whole back-compat guarantee in one assertion: a bare string in, the SAME
        # bare string out, still a str and not a dict.
        out = dec.compact(dec.as_rich(LEGACY))
        self.assertEqual(out, LEGACY)
        self.assertIsInstance(out, str)

    def test_legacy_string_declares_nothing(self):
        self.assertIsNone(dec.kind(LEGACY))
        self.assertEqual(dec.subject(LEGACY), [])
        self.assertFalse(dec.is_typed(LEGACY))
        self.assertFalse(dec.has_subject(LEGACY))
        self.assertEqual(dec.kind_label(LEGACY), "")
        self.assertEqual(dec.subject_label(LEGACY), "")

    def test_rich_entry_without_the_fields_declares_nothing(self):
        entry = {"text": "x", "pinned": True}
        self.assertIsNone(dec.kind(entry))
        self.assertEqual(dec.subject(entry), [])

    def test_untyped_never_defaults_to_a_kind(self):
        # Named explicitly because "probably a process-note" is the exact failure the
        # migration contract forbids.
        for entry in (LEGACY, None, {"text": ""}, {"text": "y", "kind": ""}):
            self.assertIsNone(dec.kind(entry))

    def test_declaring_then_clearing_returns_the_plain_string(self):
        entries = [LEGACY]
        ok, err = dec.set_kind(entries, 1, dec.KIND_RULING)
        self.assertTrue(ok, err)
        self.assertIsInstance(entries[0], dict)
        ok, err = dec.clear_kind(entries, 1)
        self.assertTrue(ok, err)
        self.assertEqual(entries[0], LEGACY)
        self.assertIsInstance(entries[0], str)

    def test_a_neighbour_is_untouched_by_a_declaration(self):
        entries = [LEGACY, "second"]
        ok, err = dec.set_kind(entries, 2, dec.KIND_MEASUREMENT)
        self.assertTrue(ok, err)
        self.assertEqual(entries[0], LEGACY)
        self.assertIsInstance(entries[0], str)

    def test_selection_and_projection_are_unmoved_by_a_declaration(self):
        # Increment 1 changes no reader. `live` / `live_texts` / `total_chars` must give
        # the identical answer before and after a decision declares.
        entries = [LEGACY, "second"]
        before = (dec.live(entries), dec.live_texts(entries), dec.total_chars(entries),
                  dec.digest_order(entries))
        self.assertTrue(dec.set_kind(entries, 2, dec.KIND_INCIDENT)[0])
        self.assertTrue(dec.set_subject(entries, 2, ["task:596"])[0])
        after = (dec.live(entries), dec.live_texts(entries), dec.total_chars(entries),
                 dec.digest_order(entries))
        self.assertEqual([t for _i, t in before[0]], [LEGACY, "second"])
        self.assertEqual(before[1], after[1])
        self.assertEqual(before[2], after[2])
        self.assertEqual([i for i, _e in before[3]], [i for i, _e in after[3]])


class KindVocabularyIsClosed(unittest.TestCase):

    def test_the_five_kinds_and_no_others(self):
        self.assertEqual(set(dec.KINDS), {"ruling", "measurement", "incident",
                                          "release-record", "process-note"})

    def test_every_kind_round_trips(self):
        for k in dec.KINDS:
            entries = ["a decision"]
            ok, err = dec.set_kind(entries, 1, k)
            self.assertTrue(ok, err)
            self.assertEqual(dec.kind(entries[0]), k)
            self.assertEqual(dec.kind_label(entries[0]), k.upper())

    def test_an_invented_kind_is_refused_at_the_setter(self):
        entries = ["a decision"]
        ok, err = dec.set_kind(entries, 1, "finding")
        self.assertFalse(ok)
        self.assertIn("CLOSED", err)
        self.assertIn("release-record", err)          # the error names the vocabulary
        self.assertEqual(entries[0], "a decision")    # and wrote nothing

    def test_case_and_whitespace_are_normalised_not_rejected(self):
        entries = ["a decision"]
        ok, err = dec.set_kind(entries, 1, "  Release-Record ")
        self.assertTrue(ok, err)
        self.assertEqual(dec.kind(entries[0]), dec.KIND_RELEASE_RECORD)

    def test_an_unknown_stored_kind_reads_as_untyped(self):
        # A kind written by a NEWER version must never be mistaken for one this version
        # knows; untyped is the behaviour that is safe forever.
        self.assertIsNone(dec.kind({"text": "x", "kind": "postmortem"}))

    def test_an_unknown_stored_kind_survives_compaction(self):
        # ...and it is not DROPPED either: compact() preserves unknown keys, so reading
        # it as absent costs nothing on disk.
        entry = {"text": "x", "kind": "postmortem"}
        self.assertEqual(dec.compact(dec.as_rich(entry))["kind"], "postmortem")

    def test_re_declaring_a_kind_is_allowed(self):
        # The one place the `owner` precedent deliberately does not carry: a kind has no
        # second store to leave stale, and the author is the only correction mechanism
        # the design permits.
        entries = ["a decision"]
        self.assertTrue(dec.set_kind(entries, 1, dec.KIND_RULING)[0])
        ok, err = dec.set_kind(entries, 1, dec.KIND_MEASUREMENT)
        self.assertTrue(ok, err)
        self.assertEqual(dec.kind(entries[0]), dec.KIND_MEASUREMENT)

    def test_an_empty_kind_is_refused_and_names_the_inverse(self):
        entries = ["a decision"]
        ok, err = dec.set_kind(entries, 1, "")
        self.assertFalse(ok)
        self.assertIn("clear_kind", err)


class SubjectIsAQualifiedRef(unittest.TestCase):
    """The constraint that is not negotiable."""

    def test_a_bare_number_is_refused_and_the_error_names_the_collision(self):
        entries = ["a decision"]
        ok, err = dec.set_subject(entries, 1, ["27"])
        self.assertFalse(ok)
        self.assertIn("PR 27 and story 27", err)
        self.assertEqual(entries[0], "a decision")

    def test_pr_and_story_with_the_same_number_do_not_collide(self):
        entries = ["a", "b"]
        self.assertTrue(dec.set_subject(entries, 1, ["pr:task-station#27"])[0])
        self.assertTrue(dec.set_subject(entries, 2, ["story:atlas#27"])[0])
        self.assertNotEqual(dec.subject(entries[0]), dec.subject(entries[1]))

    def test_an_unqualified_pr_is_refused(self):
        entries = ["a decision"]
        ok, err = dec.set_subject(entries, 1, ["pr:27"])
        self.assertFalse(ok)
        self.assertIn("REPO-QUALIFIED", err)

    def test_a_repo_that_is_only_digits_is_refused(self):
        # `pr:27#3` would smuggle a bare number pair back in through the qualifier.
        entries = ["a decision"]
        self.assertFalse(dec.set_subject(entries, 1, ["pr:27#3"])[0])

    def test_every_subject_type_round_trips(self):
        cases = ["task:596", "step:29", "pr:task-station#42",
                 "story:atlas#2704", "release:3.44.0"]
        for ref in cases:
            entries = ["a decision"]
            ok, err = dec.set_subject(entries, 1, ref)
            self.assertTrue(ok, err)
            self.assertEqual(dec.subject(entries[0]), [ref])

    def test_an_unknown_subject_type_is_refused(self):
        entries = ["a decision"]
        ok, err = dec.set_subject(entries, 1, ["epic:12"])
        self.assertFalse(ok)
        self.assertIn("closed set", err)

    def test_a_non_numeric_step_is_refused(self):
        entries = ["a decision"]
        self.assertFalse(dec.set_subject(entries, 1, ["step:the-second-one"])[0])

    def test_prose_in_the_subject_field_is_refused(self):
        entries = ["a decision"]
        ok, err = dec.set_subject(entries, 1, ["task:" + "x" * 200])
        self.assertFalse(ok)
        self.assertIn("not prose", err)

    def test_one_bad_ref_refuses_the_WHOLE_declaration(self):
        # A partially accepted subject is the worst outcome: structural, trusted,
        # undoubtable and silently incomplete.
        entries = ["a decision"]
        ok, _err = dec.set_subject(entries, 1, ["task:596", "27", "release:3.44.0"])
        self.assertFalse(ok)
        self.assertEqual(entries[0], "a decision")

    def test_a_lone_string_is_accepted_as_a_subject_of_one(self):
        entries = ["a decision"]
        ok, err = dec.set_subject(entries, 1, "task:596")
        self.assertTrue(ok, err)
        self.assertEqual(dec.subject(entries[0]), ["task:596"])

    def test_refs_are_de_duplicated_and_keep_declared_order(self):
        entries = ["a decision"]
        ok, err = dec.set_subject(entries, 1, ["release:3.44.0", "task:596",
                                               "release:3.44.0"])
        self.assertTrue(ok, err)
        self.assertEqual(dec.subject(entries[0]), ["release:3.44.0", "task:596"])

    def test_set_subject_replaces_rather_than_appends(self):
        entries = ["a decision"]
        self.assertTrue(dec.set_subject(entries, 1, ["task:596"])[0])
        self.assertTrue(dec.set_subject(entries, 1, ["task:600"])[0])
        self.assertEqual(dec.subject(entries[0]), ["task:600"])

    def test_an_empty_list_is_refused_and_names_the_inverse(self):
        entries = ["a decision"]
        ok, err = dec.set_subject(entries, 1, [])
        self.assertFalse(ok)
        self.assertIn("clear_subject", err)

    def test_clear_subject_returns_the_plain_string(self):
        entries = ["a decision"]
        self.assertTrue(dec.set_subject(entries, 1, ["task:596"])[0])
        ok, err = dec.clear_subject(entries, 1)
        self.assertTrue(ok, err)
        self.assertEqual(entries[0], "a decision")

    def test_clear_subject_errors_when_there_is_nothing_to_retract(self):
        entries = ["a decision"]
        ok, err = dec.clear_subject(entries, 1)
        self.assertFalse(ok)
        self.assertIn("nothing to retract", err)

    def test_an_unparseable_stored_ref_reads_as_absent_not_as_a_guess(self):
        entry = {"text": "x", "subject": ["27", "task:596"]}
        self.assertEqual(dec.subject(entry), ["task:596"])

    def test_subject_label_is_load_free(self):
        entries = ["a decision"]
        self.assertTrue(dec.set_subject(entries, 1, ["pr:task-station#27",
                                                     "task:596"])[0])
        self.assertEqual(dec.subject_label(entries[0]), "pr:task-station#27, task:596")


class TheGuardsMatchThePrecedent(unittest.TestCase):
    """Same refusals `set_pin` and `set_owner` make, for the same reasons."""

    def _replaced(self, verb):
        entries = ["first", "second"]
        ok, err = verb(entries)
        self.assertTrue(ok, err)
        return entries

    def test_a_superseded_decision_cannot_be_typed(self):
        entries = self._replaced(lambda e: dec.mark_superseded(e, 1, 2))
        ok, err = dec.set_kind(entries, 1, dec.KIND_RULING)
        self.assertFalse(ok)
        self.assertIn("renders nowhere", err)

    def test_a_superseded_decision_cannot_be_given_a_subject(self):
        entries = self._replaced(lambda e: dec.mark_superseded(e, 1, 2))
        ok, err = dec.set_subject(entries, 1, ["task:596"])
        self.assertFalse(ok)
        self.assertIn("renders nowhere", err)

    def test_a_split_decision_cannot_be_typed(self):
        entries = self._replaced(lambda e: dec.mark_split(e, 1, [2]))
        self.assertFalse(dec.set_kind(entries, 1, dec.KIND_RULING)[0])

    def test_a_merged_decision_cannot_be_typed(self):
        entries = self._replaced(lambda e: dec.mark_merged(e, 1, 2))
        self.assertFalse(dec.set_kind(entries, 1, dec.KIND_RULING)[0])

    def test_a_bad_index_errors_rather_than_no_ops(self):
        for call in (lambda: dec.set_kind(["a"], 9, dec.KIND_RULING),
                     lambda: dec.set_subject(["a"], 9, ["task:1"]),
                     lambda: dec.clear_kind(["a"], 0),
                     lambda: dec.clear_subject(["a"], 0)):
            ok, err = call()
            self.assertFalse(ok)
            self.assertIn("no such decision", err)

    def test_declaration_coexists_with_pin_owner_and_supersession_marks(self):
        entries = ["first", "second"]
        self.assertTrue(dec.set_pin(entries, 1, True)[0])
        self.assertTrue(dec.set_kind(entries, 1, dec.KIND_RULING)[0])
        self.assertTrue(dec.set_subject(entries, 1, ["task:596"])[0])
        self.assertTrue(dec.is_pinned(entries[0]))
        self.assertEqual(dec.kind(entries[0]), dec.KIND_RULING)
        self.assertEqual(dec.text(entries[0]), "first")
        # ...and an owner stamp still works on a declared entry, keys side by side.
        self.assertTrue(dec.set_pin(entries, 1, False)[0])
        ok, err = dec.set_owner(entries, 1, "abc123", seq=600)
        self.assertTrue(ok, err)
        self.assertEqual(dec.owner(entries[0]), "abc123")
        self.assertEqual(dec.kind(entries[0]), dec.KIND_RULING)


if __name__ == "__main__":
    unittest.main()
