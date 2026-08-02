"""The reconcile pass — `heal`: the two new verbs, the nine scan checks, the gates.

task-station had capture (`save`) and no reconcile. A task's `decisions` list is
append-only, so it only grows; the digest truncates it by AGE; and nothing in the record
ever said "these sixteen entries are now four". Measured on one real task: 72 decisions,
68 still current, ~96,000 chars — and the longest single decision was 27,707 chars
BECAUSE the digest truncates by age, so its author front-loaded everything into one entry
hoping it would land in the visible window. Truncation manufactured the bloat it hid.

Supersession (2.9.0) only covers "this was WRONG". Two more shapes needed verbs:
  * SPLIT — a compound decision mixing still-valid rulings with refuted ones. Superseding
    it destroys the good content; keeping it briefs the bad.
  * MERGE — decisions that are TRUE but no longer load-bearing (release records,
    iteration steps). Nothing refuted them, so calling them wrong would be a lie.

THE HARD RULE UNDER TEST THROUGHOUT: non-destructive. No verb deletes a decision. Each
one MARKS the original, drops it from the default digest, keeps it in `history` labelled
with what replaced it, and is reversible via `update --restore-decision <n>`. Every verb
here has a test for the marking, a test for reversibility, and a test proving the
original is still retrievable from `history` afterwards.

Isolation copies the `_repoint` idiom from tests/test_supersession.py.
"""
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(_REPO_ROOT, "lib")
sys.path.insert(0, LIB)

_TMP_HOME = tempfile.mkdtemp(prefix="ts-heal-")
os.environ["TASK_STATION_HOME"] = _TMP_HOME

import decisions as dec   # noqa: E402
import heal               # noqa: E402
import steps              # noqa: E402
import store              # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


def _repoint(tmp):
    os.environ["TASK_STATION_HOME"] = tmp
    ts.DATA = tmp
    ts.STORE = os.path.join(tmp, "store")
    ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
    ts.LINKS_DIR = os.path.join(ts.STORE, "links")
    store.reset_cache()


# A decision pair the prose check MUST report: a later decision saying an EARLIER one was
# wrong, with nothing linking them. Since 2.13.0 the language alone is not enough — the
# prose has to name a decision-shaped target — so every fixture that needs the check to
# fire uses this shape rather than a bare "was wrong".
UNLINKED_PROSE = ["go with flat files",
                  "decision 1 was wrong — sqlite instead, for the FTS index"]

# Paths a task might have RECORDED, in the two categories the drift check must now tell
# apart. The repo-shaped ones are drift when they vanish — someone deleted a worktree or
# renamed a file, and a resumed session is pointed at nothing. The ephemeral ones are
# NOT: a session scratchpad and a system temp directory are erased by construction, and
# on one real task all seven drift findings were auto-captured worker briefs under a
# wiped scratchpad. None of these exists, which is the point.
MISSING_REPO_FILE = "/Users/nobody/Workspace/projectname/lib/gone.py"
MISSING_REPO_WORKTREE = "/Users/nobody/Workspace/projectname-worktrees/wt-gone"
SCRATCHPAD_FILE = ("/private/tmp/claude-501/6f1a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8/"
                   "scratchpad/BRIEF-worker.md")
SYSTEM_TEMP_FILE = "/tmp/ts-worker-9182/notes.md"


class _Args:
    def __init__(self, **kw):
        defaults = dict(session=None, task=None, scan=False, apply=False, all=False,
                        verbose=False,
                        split=None, merge=None, into=None, mark_healed=False, note=None,
                        dispose_acks=None, decision=None, memory=None, noop=None)
        defaults.update(kw)
        self.__dict__.update(defaults)


class _UpdateArgs:
    def __init__(self, **kw):
        defaults = dict(task=None, title=None, summary=None, append_summary=None,
                        state=None, goal=None, step_add=None, step_done=None,
                        step_undone=None, step_supersede=None, step_restore=None,
                        decision=None, supersedes=None, pin=False,
                        pin_decision=None, unpin_decision=None, restore_decision=None,
                        log=None, pr=None, pr_desc=None, story=None, story_desc=None,
                        color=None, effort=None, trail_visibility=None, relate=None,
                        session=None)
        defaults.update(kw)
        self.__dict__.update(defaults)


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _repoint(self.tmp)

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _task(self, title="A task", decisions=None, **fields):
        t = ts.new_task(title, "summary")
        if decisions is not None:
            t["decisions"] = list(decisions)
        t.update(fields)
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _out(self, fn, args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            fn(args)
        return buf.getvalue()

    def _sub(self, fn, args, rest=""):
        """A `/todo <kw>` subcommand handler — those take `(args, rest)`, not one arg."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            fn(args, rest)
        return buf.getvalue()

    def _heal(self, t, **kw):
        return self._out(ts.cmd_heal, _Args(task=str(t["seq"]), **kw))

    def _update(self, t, **kw):
        return self._out(ts.cmd_update, _UpdateArgs(task=str(t["seq"]), **kw))

    def _reload(self, t):
        return ts.load_task(t["id"])

    def _memo_with_ack(self, task, text="a fact", sid="sess1234", disposition=None,
                       corrects=None):
        """A memo carrying one ack — with NO disposition by default, which is the
        shape every ack recorded before 2.9.0 has."""
        memo = ts.memo_send(task, text, from_sid="sender01", corrects=corrects)
        ack = {"sid": sid, "ts": ts._now()}
        if disposition:
            ack["disposition"] = dict(disposition)
        memo.setdefault("acks", []).append(ack)
        ts.save_task(task)
        return memo


# ---------------------------------------------------------------------------
# The two new verbs in the primitive (lib/decisions.py).
# ---------------------------------------------------------------------------

class TestSplitVerb(_Base):
    def test_split_marks_the_original_and_names_the_parts(self):
        entries = ["compound: A and B", "atomic A", "atomic B"]
        ok, err = dec.mark_split(entries, 1, [2, 3])
        self.assertTrue(ok, err)
        self.assertEqual(dec.replacement(entries[0]), (dec.REPLACED_SPLIT, [2, 3]))
        self.assertEqual(dec.replacement_label(entries[0]),
                         "SPLIT into decisions 2, 3")

    def test_split_does_not_delete_the_original_text(self):
        entries = ["the whole compound ruling", "part one", "part two"]
        dec.mark_split(entries, 1, [2, 3])
        self.assertEqual(len(entries), 3)                       # nothing removed
        self.assertEqual(dec.text(entries[0]), "the whole compound ruling")

    def test_a_split_decision_leaves_the_default_digest(self):
        entries = ["compound", "part one", "part two"]
        dec.mark_split(entries, 1, [2, 3])
        self.assertNotIn("compound", dec.live_texts(entries))
        self.assertEqual(dec.live_texts(entries), ["part one", "part two"])

    def test_split_is_reversible(self):
        entries = ["compound", "part one", "part two"]
        dec.mark_split(entries, 1, [2, 3])
        ok, err = dec.restore(entries, 1)
        self.assertTrue(ok, err)
        self.assertFalse(dec.is_replaced(entries[0]))
        self.assertIn("compound", dec.live_texts(entries))
        # And it collapses back to the legacy plain-string shape exactly.
        self.assertEqual(entries[0], "compound")

    def test_split_original_is_still_retrievable_from_history(self):
        t = self._task(decisions=["a compound ruling: X is fine but Y was wrong",
                                  "X is fine", "Y was wrong"])
        entries = t["decisions"]
        dec.mark_split(entries, 1, [2, 3])
        ts.save_task(t)
        view = ts._format_history(self._reload(t))
        self.assertIn("a compound ruling: X is fine but Y was wrong", view)
        self.assertIn("SPLIT into decisions 2, 3", view)
        # …and it is GONE from the default digest, which is the whole point.
        detail = ts._format_detail(self._reload(t), None)
        self.assertNotIn("a compound ruling", detail)

    def test_split_clears_the_pin(self):
        entries = ["compound", "part one", "part two"]
        dec.set_pin(entries, 1, True)
        dec.mark_split(entries, 1, [2, 3])
        self.assertFalse(dec.is_pinned(entries[0]))

    def test_split_needs_at_least_one_target(self):
        entries = ["compound", "other"]
        ok, err = dec.mark_split(entries, 1, [])
        self.assertFalse(ok)
        self.assertIn("name at least one decision", err)

    def test_split_cannot_target_itself(self):
        entries = ["compound", "other"]
        ok, err = dec.mark_split(entries, 1, [1])
        self.assertFalse(ok)
        self.assertIn("cannot split into itself", err)

    def test_split_rejects_an_out_of_range_target(self):
        entries = ["compound", "other"]
        ok, err = dec.mark_split(entries, 1, [9])
        self.assertFalse(ok)
        self.assertIn("no such decision", err)

    def test_splitting_an_already_replaced_decision_is_a_loud_error(self):
        entries = ["first", "second", "third"]
        dec.mark_superseded(entries, 1, 2)
        ok, err = dec.mark_split(entries, 1, [3])
        self.assertFalse(ok)
        self.assertIn("already superseded", err)


class TestMergeVerb(_Base):
    def test_merge_marks_each_original_and_names_the_absorber(self):
        entries = ["1.0.0 SHIPPED", "1.1.0 SHIPPED", "release trail"]
        for i in (1, 2):
            ok, err = dec.mark_merged(entries, i, 3)
            self.assertTrue(ok, err)
        self.assertEqual(dec.replacement(entries[0]), (dec.REPLACED_MERGED, [3]))
        self.assertEqual(dec.replacement_label(entries[1]),
                         "MERGED into decision 3")

    def test_merge_does_not_delete_the_originals(self):
        entries = ["step one", "step two", "the summary"]
        dec.mark_merged(entries, 1, 3)
        dec.mark_merged(entries, 2, 3)
        self.assertEqual(len(entries), 3)
        self.assertEqual(dec.text(entries[0]), "step one")
        self.assertEqual(dec.text(entries[1]), "step two")

    def test_merged_decisions_leave_the_default_digest(self):
        entries = ["step one", "step two", "the summary"]
        dec.mark_merged(entries, 1, 3)
        dec.mark_merged(entries, 2, 3)
        self.assertEqual(dec.live_texts(entries), ["the summary"])

    def test_merge_is_reversible(self):
        entries = ["step one", "step two", "the summary"]
        dec.mark_merged(entries, 1, 3)
        ok, err = dec.restore(entries, 1)
        self.assertTrue(ok, err)
        self.assertEqual(entries[0], "step one")               # legacy shape restored
        self.assertIn("step one", dec.live_texts(entries))

    def test_merge_originals_are_still_retrievable_from_history(self):
        t = self._task(decisions=["2.8.0 SHIPPED: hook health",
                                  "2.9.0 SHIPPED: supersession",
                                  "Release trail — reconciled from 2 decisions"])
        entries = t["decisions"]
        dec.mark_merged(entries, 1, 3)
        dec.mark_merged(entries, 2, 3)
        ts.save_task(t)
        view = ts._format_history(self._reload(t))
        self.assertIn("2.8.0 SHIPPED: hook health", view)
        self.assertIn("2.9.0 SHIPPED: supersession", view)
        self.assertEqual(view.count("MERGED into decision 3"), 2)
        detail = ts._format_detail(self._reload(t), None)
        self.assertNotIn("2.8.0 SHIPPED", detail)

    def test_merge_cannot_target_itself(self):
        entries = ["a", "b"]
        ok, err = dec.mark_merged(entries, 1, 1)
        self.assertFalse(ok)
        self.assertIn("cannot merge into itself", err)

    def test_merging_an_already_merged_decision_is_a_loud_error(self):
        entries = ["a", "b", "c"]
        dec.mark_merged(entries, 1, 3)
        ok, err = dec.mark_merged(entries, 1, 2)
        self.assertFalse(ok)
        self.assertIn("already merged", err)


class TestReplacementCore(_Base):
    def test_a_replaced_decision_cannot_be_pinned_whatever_the_verb(self):
        for verb, mark in (("split", lambda e: dec.mark_split(e, 1, [2])),
                           ("merge", lambda e: dec.mark_merged(e, 1, 2))):
            entries = ["original", "replacement"]
            mark(entries)
            ok, err = dec.set_pin(entries, 1, True)
            self.assertFalse(ok, verb)
            self.assertIn("cannot be pinned", err)

    def test_restoring_a_current_decision_is_a_loud_error(self):
        entries = ["still current"]
        ok, err = dec.restore(entries, 1)
        self.assertFalse(ok)
        self.assertIn("not replaced", err)

    def test_legacy_plain_strings_are_always_current(self):
        self.assertFalse(dec.is_replaced("a legacy decision"))
        self.assertIsNone(dec.replacement("a legacy decision"))
        self.assertIsNone(dec.replacement_label("a legacy decision"))

    def test_a_garbled_mark_fails_open_and_does_not_hide_the_decision(self):
        # A mark we cannot parse must never silently drop a decision from the digest.
        for bad in ({"text": "x", "split_into": []}, {"text": "x", "split_into": "junk"},
                    {"text": "x", "merged_into": "junk"}, {"text": "x", "merged_into": 0}):
            self.assertFalse(dec.is_replaced(bad), bad)
            self.assertEqual(dec.live_texts([bad]), ["x"], bad)

    def test_total_chars_counts_only_current_decisions(self):
        entries = ["aaaa", "bb", "cc"]
        self.assertEqual(dec.total_chars(entries), 8)
        dec.mark_merged(entries, 1, 3)
        self.assertEqual(dec.total_chars(entries), 4)

    def test_history_header_names_each_verb_when_kinds_are_mixed(self):
        t = self._task(decisions=["s", "sp", "m", "new", "parts", "summary"])
        entries = t["decisions"]
        dec.mark_superseded(entries, 1, 4)
        dec.mark_split(entries, 2, [5])
        dec.mark_merged(entries, 3, 6)
        ts.save_task(t)
        view = ts._format_history(self._reload(t))
        self.assertIn("3 replaced (1 superseded · 1 split · 1 merged)", view)

    def test_history_header_is_unchanged_when_only_supersession_was_used(self):
        # Back-compat on the rendered wording: a task reconciled only by supersede must
        # read exactly as it did before split/merge existed.
        t = self._task(decisions=["old", "new"])
        entries = t["decisions"]
        dec.mark_superseded(entries, 1, 2)
        ts.save_task(t)
        self.assertIn("1 superseded", ts._format_history(self._reload(t)))


class TestRestoreThroughTheCli(_Base):
    def test_restore_decision_undoes_a_split(self):
        t = self._task(decisions=["compound", "part one", "part two"])
        entries = t["decisions"]
        dec.mark_split(entries, 1, [2, 3])
        ts.save_task(t)
        out = self._update(t, restore_decision=[1])
        self.assertNotIn("no such decision", out)
        self.assertFalse(dec.is_replaced(self._reload(t)["decisions"][0]))

    def test_restore_decision_undoes_a_merge(self):
        t = self._task(decisions=["step", "summary"])
        entries = t["decisions"]
        dec.mark_merged(entries, 1, 2)
        ts.save_task(t)
        self._update(t, restore_decision=[1])
        self.assertIn("step", dec.live_texts(self._reload(t)["decisions"]))

    def test_restore_decision_on_a_current_decision_reports_loudly(self):
        t = self._task(decisions=["current"])
        out = self._update(t, restore_decision=[1])
        self.assertIn("not replaced", out)


# ---------------------------------------------------------------------------
# Layer 1 — the eight deterministic checks. None of them may mutate the task.
# ---------------------------------------------------------------------------

class TestScanChecks(_Base):
    # -- 1: undispositioned acks --------------------------------------------
    def test_undispositioned_ack_is_flagged(self):
        t = self._task()
        self._memo_with_ack(t, sid="oldsess1")
        hits = heal.undispositioned_acks(self._reload(t))
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["check"], "ack-undispositioned")
        self.assertIn("oldsess", hits[0]["ref"])

    def test_an_ack_with_a_disposition_is_clean(self):
        t = self._task()
        self._memo_with_ack(t, disposition={"kind": "noop", "value": "nothing needed"})
        self.assertEqual(heal.undispositioned_acks(self._reload(t)), [])

    # -- 2: corrections whose target was never updated ----------------------
    def test_a_corrects_decision_target_still_current_is_flagged(self):
        t = self._task(decisions=["the thing being corrected"])
        self._memo_with_ack(t, corrects=["decision:1"])
        hits = heal.unfulfilled_corrections(self._reload(t))
        self.assertEqual(len(hits), 1)
        self.assertIn("decision 1", hits[0]["detail"])

    def test_a_corrects_decision_target_that_was_superseded_is_clean(self):
        t = self._task(decisions=["wrong", "right"])
        entries = t["decisions"]
        dec.mark_superseded(entries, 1, 2)
        ts.save_task(t)
        self._memo_with_ack(t, corrects=["decision:1"])
        self.assertEqual(heal.unfulfilled_corrections(self._reload(t)), [])

    def test_a_corrects_slug_target_needs_a_decision_or_memory_disposition(self):
        t = self._task()
        self._memo_with_ack(t, corrects=["some-memory-slug"])
        self.assertEqual(len(heal.unfulfilled_corrections(self._reload(t))), 1)

    def test_a_corrects_slug_disposed_via_memory_is_clean(self):
        t = self._task()
        self._memo_with_ack(t, corrects=["some-memory-slug"],
                            disposition={"kind": "memory", "value": "some-memory-slug"})
        self.assertEqual(heal.unfulfilled_corrections(self._reload(t)), [])

    def test_a_memo_with_no_corrects_is_never_flagged(self):
        t = self._task()
        self._memo_with_ack(t, disposition={"kind": "noop", "value": "fine"})
        self.assertEqual(heal.unfulfilled_corrections(self._reload(t)), [])

    # -- 3: prose pretending to be structure --------------------------------
    def test_unlinked_supersession_language_is_flagged(self):
        t = self._task(decisions=UNLINKED_PROSE)
        hits = heal.prose_supersession(self._reload(t))
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["ref"], "decision 2")
        self.assertIn("was wrong", hits[0]["detail"])
        self.assertIn("decision 1", hits[0]["detail"])     # names the target it should link

    def test_supersession_language_that_IS_linked_is_clean(self):
        t = self._task(decisions=["old", "decision 1 was wrong; this replaces it"])
        entries = t["decisions"]
        dec.mark_superseded(entries, 1, 2)
        ts.save_task(t)
        # Decision 2 carries the language AND names decision 1, but it IS the recorded
        # replacement, so this is already structure rather than prose pretending to be it.
        self.assertEqual(heal.prose_supersession(self._reload(t)), [])

    def test_a_replaced_decision_with_the_language_is_not_double_reported(self):
        t = self._task(decisions=["this was wrong", "the fix"])
        entries = t["decisions"]
        dec.mark_superseded(entries, 1, 2)
        ts.save_task(t)
        self.assertEqual(heal.prose_supersession(self._reload(t)), [])

    def test_ordinary_decisions_produce_no_prose_findings(self):
        t = self._task(decisions=["chose sqlite over flat files for the store"])
        self.assertEqual(heal.prose_supersession(self._reload(t)), [])

    # -- 3b: the four FALSE-POSITIVE shapes measured on a real task ----------
    # The language-only version of this check fired 5 times on one task and 4 were
    # false. Each of the four shapes below is a decision that TALKS about supersession
    # without any decision needing a link, and each one must now be silent — a check
    # that is 80% wrong is one the reader learns to skip.

    def test_a_decision_describing_the_supersede_feature_is_not_reported(self):
        t = self._task(decisions=[
            "the digest no longer truncates by age: a decision marked SUPERSEDED leaves "
            "every present-tense surface and survives only in history"])
        self.assertEqual(heal.prose_supersession(self._reload(t)), [])

    def test_a_rule_that_supersedes_another_rule_is_not_reported(self):
        t = self._task(decisions=[
            "branch naming: the story id, never the board sequence",
            "this naming rule supersedes the earlier one — the sequence collides with "
            "work-item numbers"])
        self.assertEqual(heal.prose_supersession(self._reload(t)), [])

    def test_a_correction_to_a_memory_note_is_not_reported(self):
        t = self._task(decisions=[
            "corrected the memory note `projectname-env-gating`: it was wrong about the "
            "test runner being available"])
        self.assertEqual(heal.prose_supersession(self._reload(t)), [])

    def test_a_corrected_memo_is_not_reported(self):
        t = self._task(decisions=["shipped the ack ledger",
                                  "memo #1 was corrected by the follow-up memo"])
        # `#1` is numbered, but the word in front of it says MEMO — so it is not a
        # decision-shaped target and there is no missing --supersedes link.
        self.assertEqual(heal.prose_supersession(self._reload(t)), [])

    def test_prose_about_an_already_replaced_decision_is_clean(self):
        # The complaint is that "whatever this contradicts is still briefing every
        # session". Once decision 1 is superseded it briefs nobody, so linking the prose
        # would change nothing — and re-reporting it makes a healed task read as dirty.
        t = self._task(decisions=UNLINKED_PROSE + ["sqlite it is: the FTS index needs a "
                                                   "real query engine"])
        entries = t["decisions"]
        dec.mark_superseded(entries, 1, 3)
        ts.save_task(t)
        self.assertEqual(heal.prose_supersession(self._reload(t)), [])

    def test_a_config_file_that_is_no_longer_read_is_not_reported(self):
        t = self._task(decisions=[
            "the config file `settings.json` is no longer read at startup — the "
            "environment wins"])
        self.assertEqual(heal.prose_supersession(self._reload(t)), [])

    def test_a_forward_or_out_of_range_number_is_not_a_decision_reference(self):
        # "task #444" is a task, not a decision; and a decision cannot refute one that
        # did not exist yet, so a forward reference is not a supersession claim.
        t = self._task(decisions=["decision 9 was wrong (there is no decision 9)",
                                  "the plan for task #444 was retracted"])
        self.assertEqual(heal.prose_supersession(self._reload(t)), [])

    def test_decision_refs_reads_the_shapes_that_name_a_decision(self):
        self.assertEqual(heal.decision_refs("decision 3 was wrong", own_index=5,
                                            total=5), [3])
        self.assertEqual(heal.decision_refs("entry 2 no longer holds", own_index=4,
                                            total=4), [2])
        self.assertEqual(heal.decision_refs("corrected in #1", own_index=3, total=3), [1])
        self.assertEqual(heal.decision_refs("decisions 1 and 2 were wrong", own_index=3,
                                            total=3), [1, 2])
        self.assertEqual(heal.decision_refs("memo #2 was corrected", own_index=4,
                                            total=4), [])
        self.assertEqual(heal.decision_refs("the rule was wrong", own_index=2,
                                            total=2), [])

    # -- 4: oversized decisions --------------------------------------------
    def test_an_oversized_decision_is_flagged_as_a_split_candidate(self):
        big = "x" * (heal.OVERSIZE_CHARS + 1)
        t = self._task(decisions=["small", big])
        hits = heal.oversized(self._reload(t))
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["ref"], "decision 2")
        self.assertIn("split", hits[0]["detail"])

    def test_a_decision_at_the_threshold_is_clean(self):
        t = self._task(decisions=["x" * heal.OVERSIZE_CHARS])
        self.assertEqual(heal.oversized(self._reload(t)), [])

    def test_an_oversized_but_already_replaced_decision_is_not_flagged(self):
        big = "y" * (heal.OVERSIZE_CHARS + 50)
        t = self._task(decisions=[big, "the replacement"])
        entries = t["decisions"]
        dec.mark_superseded(entries, 1, 2)
        ts.save_task(t)
        self.assertEqual(heal.oversized(self._reload(t)), [])

    # -- 5: drift ----------------------------------------------------------
    #
    # These fixtures name REPO-shaped paths rather than the test's own temp directory,
    # because a temp directory is now (correctly) classified as expected-ephemeral —
    # see TestEphemeralPathsAreNotDrift. A path that vanishes from `/var/folders` or
    # `/tmp` is the system doing its job; a path that vanishes from a checkout is drift.
    def test_a_vanished_recorded_file_is_drift(self):
        t = self._task(files=[MISSING_REPO_FILE])
        hits = heal.drift(self._reload(t))
        self.assertEqual(len(hits), 1)
        self.assertIn("no longer exists", hits[0]["detail"])

    def test_a_present_file_is_not_drift(self):
        # `exists` is injected rather than writing a real file: the only directory a
        # test may certainly write to is a temp one, and a temp path would now be
        # skipped as ephemeral, so the assertion would pass for the wrong reason.
        t = self._task(files=[MISSING_REPO_FILE])
        self.assertEqual(heal.drift(self._reload(t), exists=lambda p: True), [])

    def test_a_vanished_session_worktree_is_drift(self):
        t = self._task(session_meta={"s1": {"cwd": MISSING_REPO_WORKTREE}})
        hits = heal.drift(self._reload(t))
        self.assertEqual(len(hits), 1)
        self.assertIn("worktree", hits[0]["detail"])

    def test_a_branch_the_probe_cannot_resolve_is_drift(self):
        t = self._task(state="NEXT: push. On branch heal-wip now.")
        self.assertEqual(heal.mentioned_branches(self._reload(t)), ["heal-wip"])
        hits = heal.drift(self._reload(t), branch_probe=lambda name: False)
        self.assertEqual(len(hits), 1)
        self.assertIn("branch heal-wip", hits[0]["ref"])

    def test_an_unknown_branch_state_is_never_reported_as_gone(self):
        # None means "no usable repo was found". A false "your branch is gone" is far
        # worse than a missed one, so unknown must stay silent.
        t = self._task(state="on branch heal-wip")
        self.assertEqual(heal.drift(self._reload(t), branch_probe=lambda n: None), [])
        self.assertEqual(heal.drift(self._reload(t)), [])      # default: no probe at all

    def test_a_resolvable_branch_is_not_drift(self):
        t = self._task(state="on branch heal-wip")
        self.assertEqual(heal.drift(self._reload(t), branch_probe=lambda n: True), [])

    def test_a_branch_probe_that_raises_degrades_to_unknown(self):
        def boom(name):
            raise RuntimeError("git exploded")
        t = self._task(state="on branch heal-wip")
        self.assertEqual(heal.drift(self._reload(t), branch_probe=boom), [])

    def test_branch_names_keep_their_slashes(self):
        t = self._task(state="work on branch origin/dev and branch feature/x")
        self.assertEqual(heal.mentioned_branches(self._reload(t)),
                         ["origin/dev", "feature/x"])

    # -- 5b: the drift check must not cry wolf on prose ---------------------
    # On one real task, 5 of the drift check's 7 findings were the English word
    # following "branch" — `branch prefix`, `branch off`, `branch while`, `branch
    # names`, `branch with` — and only 2 were genuinely dead paths. A check wrong 5
    # times out of 7 trains the reader to skip the 2 that matter.

    def test_prose_containing_the_word_branch_is_not_a_branch_name(self):
        # The exact five false positives from the real task, in one sentence.
        prose = ("Use the ADO story id as the branch prefix, branch off origin/dev, "
                 "do not branch while a worker is running, keep branch names short, "
                 "and never branch with an unstaged tree.")
        t = self._task(state=prose)
        self.assertEqual(heal.mentioned_branches(self._reload(t)), [])
        # NOTE the accepted false negative: `origin/dev` is real, but it is the SECOND
        # token after "branch" and only the first is a candidate. Missing it costs one
        # confused resume; scanning further into prose is how the wolf-crying started.

    def test_prose_alone_produces_no_drift_finding_even_with_a_dead_probe(self):
        # The end-to-end direction: a probe that answers "gone" to EVERYTHING must
        # still report nothing, because nothing in this prose is a candidate.
        t = self._task(state="branch off the integration branch when work starts")
        self.assertEqual(heal.drift(self._reload(t), branch_probe=lambda n: False), [])

    def test_a_real_missing_branch_is_still_reported(self):
        # The other direction — the guard must not have silenced the check itself.
        t = self._task(state="NEXT: push. On branch 2707-rollup-cache now.")
        self.assertEqual(heal.mentioned_branches(self._reload(t)), ["2707-rollup-cache"])
        hits = heal.drift(self._reload(t), branch_probe=lambda n: False)
        self.assertEqual(len(hits), 1)
        self.assertIn("branch 2707-rollup-cache", hits[0]["ref"])

    def test_ref_shape_is_a_separator_or_a_digit(self):
        for name in ("heal-wip", "feature/x", "origin/dev", "wip_2", "v2", "2707"):
            t = self._task(state="on branch %s now" % name)
            self.assertEqual(heal.mentioned_branches(self._reload(t)), [name],
                             "%r should read as a ref" % name)
        for word in ("prefix", "off", "while", "names", "with", "there", "cleanly"):
            t = self._task(state="branch %s now" % word)
            self.assertEqual(heal.mentioned_branches(self._reload(t)), [],
                             "%r should read as English" % word)

    def test_a_backticked_name_is_trusted_even_when_it_looks_like_a_word(self):
        # The escape hatch: backticks are the author marking up a literal, which is as
        # close to a structured field as narrative prose gets.
        t = self._task(state="cut it from branch `integration` today")
        self.assertEqual(heal.mentioned_branches(self._reload(t)), ["integration"])

    def test_conventional_bare_branch_names_still_resolve(self):
        # `main` / `dev` are single alphabetic words, so they need the allowlist. English
        # puts them BEFORE the word branch ("the production branch"), never after it.
        for name in ("main", "master", "dev", "develop", "trunk", "staging", "production"):
            t = self._task(state="branched off branch %s" % name)
            self.assertEqual(heal.mentioned_branches(self._reload(t)), [name])

    def test_a_trailing_sentence_period_is_not_part_of_the_name(self):
        t = self._task(state="Work landed on branch heal-wip.")
        self.assertEqual(heal.mentioned_branches(self._reload(t)), ["heal-wip"])

    # -- 6: link rot -------------------------------------------------------
    def test_link_rot_reports_nothing_without_a_probe(self):
        t = self._task(prs=[{"url": "https://example.invalid/pr/1", "desc": ""}])
        self.assertEqual(heal.link_rot(self._reload(t)), [])

    def test_every_link_is_unknown_without_a_probe(self):
        t = self._task(prs=[{"url": "https://example.invalid/pr/1", "desc": ""}],
                       stories=["https://example.invalid/story/2"])
        states = heal.link_states(self._reload(t))
        self.assertEqual([s for _k, _u, s in states], [None, None])

    def test_a_probe_saying_dead_is_reported(self):
        t = self._task(prs=[{"url": "https://example.invalid/pr/1", "desc": ""}])
        hits = heal.link_rot(self._reload(t), probe=lambda url: False)
        self.assertEqual(len(hits), 1)
        self.assertIn("does not resolve", hits[0]["detail"])

    def test_a_probe_that_raises_degrades_to_unknown_not_dead(self):
        def boom(url):
            raise RuntimeError("network down")
        t = self._task(prs=[{"url": "https://example.invalid/pr/1", "desc": ""}])
        self.assertEqual(heal.link_rot(self._reload(t), probe=boom), [])
        self.assertEqual([s for _k, _u, s in
                          heal.link_states(self._reload(t), probe=boom)], [None])

    def test_a_probe_returning_a_non_boolean_is_unknown(self):
        t = self._task(prs=[{"url": "https://example.invalid/pr/1", "desc": ""}])
        self.assertEqual(heal.link_rot(self._reload(t), probe=lambda u: "maybe"), [])

    def test_a_live_link_is_never_reported(self):
        t = self._task(prs=[{"url": "https://example.invalid/pr/1", "desc": ""}])
        self.assertEqual(heal.link_rot(self._reload(t), probe=lambda u: True), [])

    def test_legacy_bare_string_links_are_read(self):
        t = self._task(prs=["https://example.invalid/pr/9"])
        self.assertEqual(heal.stored_links(self._reload(t)),
                         [("PR", "https://example.invalid/pr/9")])

    # -- 7: the health metric ----------------------------------------------
    def test_health_counts_current_decisions_and_chars(self):
        t = self._task(decisions=["aaa", "bb", "c"])
        h = heal.health(self._reload(t))
        self.assertEqual(h["decisions_total"], 3)
        self.assertEqual(h["decisions_current"], 3)
        self.assertEqual(h["chars"], 6)
        self.assertEqual(h["longest"], 3)
        self.assertIsNone(h["since_heal"])

    def test_health_excludes_replaced_decisions_from_the_cost(self):
        t = self._task(decisions=["a" * 100, "b"])
        entries = t["decisions"]
        dec.mark_superseded(entries, 1, 2)
        ts.save_task(t)
        h = heal.health(self._reload(t))
        self.assertEqual(h["decisions_current"], 1)
        self.assertEqual(h["decisions_replaced"], 1)
        self.assertEqual(h["chars"], 1)

    def test_health_reports_time_since_the_last_heal(self):
        now = time.time()
        t = self._task(decisions=["a"], last_heal_ts=now - 3 * 86400)
        h = heal.health(self._reload(t), now=now)
        self.assertAlmostEqual(h["since_heal"], 3 * 86400, delta=5)

    def test_new_since_heal_counts_decisions_added_after_the_last_heal(self):
        t = self._task(decisions=["a", "b", "c"], last_heal_ts=time.time(),
                       decisions_at_last_heal=1)
        self.assertEqual(heal.health(self._reload(t))["new_since_heal"], 2)

    # -- 8: stale steps ----------------------------------------------------
    def test_a_stale_step_is_reported(self):
        t = self._task(steps=[{"text": "STALE — do not execute", "done": False},
                              {"text": "a normal step", "done": False}])
        hits = heal.stale_steps(self._reload(t))
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["ref"], "step 1")
        self.assertIn("--step-supersede 1", hits[0]["detail"])
        self.assertNotIn("REPORT ONLY", hits[0]["detail"])

    def test_an_already_superseded_stale_step_is_not_re_reported(self):
        # Re-flagging a step that was just retired would make a freshly-healed task read
        # as dirty — the same lesson as the merge summary that re-tripped check 3.
        t = self._task(steps=[{"text": "STALE — do not execute", "done": False}])
        entries = t["steps"]
        steps.mark_superseded(entries, 1)
        ts.save_task(t)
        self.assertEqual(heal.stale_steps(self._reload(t)), [])

    def test_stale_steps_are_never_mutated_only_reported(self):
        t = self._task(steps=[{"text": "READ-ME-FIRST then stop", "done": False}])
        heal.scan(self._reload(t))
        after = self._reload(t)
        self.assertEqual(after["steps"][0]["text"], "READ-ME-FIRST then stop")
        self.assertFalse(after["steps"][0]["done"])

    def test_ordinary_steps_are_clean(self):
        t = self._task(steps=[{"text": "write the tests", "done": False}])
        self.assertEqual(heal.stale_steps(self._reload(t)), [])

    # -- 8b: DESCRIBING staleness is not DECLARING it ------------------------
    # Measured after a full heal on one real task: the ONLY two findings left were
    # false, so `Heal due?` stayed YES on a task with nothing left to do — the exact
    # false alarm the heal stamp was added to kill. Both are the mistake check 3 already
    # fixed: a keyword says nothing about what the keyword is ABOUT, so the word in
    # front of it has to be read (`heal.qualifier`).

    def test_a_step_naming_a_stale_file_to_delete_is_not_reported(self):
        # 'stale' describes the FILE — the step is not declaring itself obsolete.
        t = self._task(steps=[{"text": "delete stale tracked BRIEF-x.md",
                               "done": False}])
        self.assertEqual(heal.stale_steps(self._reload(t)), [])

    def test_a_step_describing_the_supersession_it_performed_is_not_reported(self):
        # This is the CORRECTED replacement written during a heal; it mentions the
        # ancestor it superseded. Reporting it makes the heal that fixed the task the
        # reason the task still reads as dirty.
        t = self._task(steps=[{"text": "the names in the superseded ancestor are "
                                       "REJECTED", "done": False}])
        self.assertEqual(heal.stale_steps(self._reload(t)), [])

    def test_a_step_warning_away_from_a_rejected_approach_is_not_reported(self):
        t = self._task(steps=[{"text": "WS-S3 was a REJECTED dead end", "done": False}])
        self.assertEqual(heal.stale_steps(self._reload(t)), [])

    def test_an_already_superseded_declaring_step_is_never_reported(self):
        # The live-step gate holds for a step that really does declare itself dead: it
        # has already left the checklist, so there is nothing left to retire.
        t = self._task(steps=[{"text": "this step is obsolete, do not execute",
                               "done": False}])
        entries = t["steps"]
        steps.mark_superseded(entries, 1)
        ts.save_task(t)
        self.assertEqual(heal.stale_steps(self._reload(t)), [])

    def test_a_read_me_first_warning_about_other_steps_is_still_reported(self):
        t = self._task(steps=[{"text": "READ-ME-FIRST: steps 3/4/5 above are STALE — "
                                       "do not execute them as written", "done": False}])
        hits = heal.stale_steps(self._reload(t))
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["ref"], "step 1")
        self.assertIn("--step-supersede 1", hits[0]["detail"])

    def test_a_step_declaring_itself_obsolete_is_still_reported(self):
        t = self._task(steps=[{"text": "this step is obsolete, do not execute",
                               "done": False}])
        hits = heal.stale_steps(self._reload(t))
        self.assertEqual(len(hits), 1)
        self.assertIn("--step-supersede 1", hits[0]["detail"])

    def test_only_the_declaring_step_is_reported_among_the_describing_ones(self):
        # The whole measured shape in one checklist: three steps that DESCRIBE
        # staleness, one that DECLARES it. Exactly one finding.
        t = self._task(steps=[
            {"text": "delete stale tracked BRIEF-x.md", "done": False},
            {"text": "the names in the superseded ancestor are REJECTED", "done": False},
            {"text": "WS-S3 was a REJECTED dead end", "done": False},
            {"text": "READ-ME-FIRST: steps 3/4/5 above are STALE — do not execute them "
                     "as written", "done": False}])
        self.assertEqual([h["ref"] for h in heal.stale_steps(self._reload(t))],
                         ["step 4"])

    # -- the guard itself, shared by checks 3 and 8 --------------------------
    def test_the_qualifier_is_the_word_standing_in_front_of_the_match(self):
        self.assertEqual(heal.qualifier("memo #3 was corrected", 5), "memo")
        self.assertEqual(heal.qualifier("delete stale tracked BRIEF-x.md", 7), "delete")
        self.assertEqual(heal.qualifier("the superseded ancestor", 4), "the")
        self.assertEqual(heal.qualifier("steps 3 and 4 are STALE", 18), "are")
        self.assertEqual(heal.qualifier("STALE — do not execute", 0), "")
        self.assertEqual(heal.qualifier("STALE — do not execute", 8), "")
        self.assertEqual(heal.qualifier("do the thing\nSTALE — skip", 13), "")

    def test_declaring_hits_drops_a_keyword_that_qualifies_another_noun(self):
        lang = heal.STALE_STEP_LANGUAGE
        self.assertEqual(heal.declaring_hits("STALE — do not execute", lang),
                         ["STALE", "do not execute"])
        self.assertEqual(heal.declaring_hits("steps 3 and 4 are STALE", lang), ["STALE"])
        self.assertEqual(heal.declaring_hits("delete stale tracked BRIEF-x.md", lang), [])
        self.assertEqual(heal.declaring_hits("the superseded ancestor", lang), [])
        # The over-eager matcher still says that last one carries the vocabulary — which
        # is exactly why the second condition had to exist.
        self.assertEqual(heal.matched_language("delete stale tracked BRIEF-x.md", lang),
                         ["STALE"])

    def test_a_later_clause_can_declare_what_an_earlier_one_only_described(self):
        # A pattern counts on its FIRST DECLARING occurrence, not its first occurrence:
        # mentioning a stale file does not buy the step immunity.
        t = self._task(steps=[{"text": "delete stale tracked BRIEF-x.md — this step is "
                                       "obsolete, do not execute", "done": False}])
        self.assertEqual(len(heal.stale_steps(self._reload(t))), 1)


# ---------------------------------------------------------------------------
# EPHEMERAL PATHS ARE NOT DRIFT.
#
# On one real task the drift check reported SEVEN findings and every one was a worker
# brief under `/private/tmp/…/<session-uuid>/scratchpad/` that task-station itself had
# auto-captured as an artifact. The session scratchpad is wiped when the session ends BY
# CONSTRUCTION, so "the digest points a resumed session at somewhere it cannot go" was
# literally true and practically useless — nobody resumes a task by opening a worker
# brief out of a deleted temp directory. Seven of them made a heal DUE on a task with
# nothing wrong with it, which is the cry-wolf failure this module has already fixed four
# times, arriving from a new direction.
# ---------------------------------------------------------------------------

class TestEphemeralPathsAreNotDrift(_Base):
    def test_ephemeral_path_reads_the_shapes_that_are_erased_by_design(self):
        self.assertTrue(heal.ephemeral_path(SCRATCHPAD_FILE))
        self.assertTrue(heal.ephemeral_path(SYSTEM_TEMP_FILE))
        self.assertTrue(heal.ephemeral_path("/private/tmp/x/y.md"))
        self.assertTrue(heal.ephemeral_path("/var/folders/ab/cd/T/tmp9/brief.md"))
        self.assertTrue(heal.ephemeral_path("/var/tmp/notes.md"))
        # a scratchpad SEGMENT is enough wherever it lives — that is what makes it
        # session-scoped, not the temp root it usually sits under
        self.assertTrue(heal.ephemeral_path("/Users/dev/sessions/s1/scratchpad/b.md"))

    def test_a_repo_path_is_never_read_as_ephemeral(self):
        self.assertFalse(heal.ephemeral_path(MISSING_REPO_FILE))
        self.assertFalse(heal.ephemeral_path(MISSING_REPO_WORKTREE))
        self.assertFalse(heal.ephemeral_path(""))
        # LOOKING temporary is not enough: nothing is inferred from the name, because a
        # false ephemeral would silently swallow the finding that matters.
        self.assertFalse(heal.ephemeral_path("/Users/dev/Workspace/temporary/x.py"))
        self.assertFalse(heal.ephemeral_path("/Users/dev/tmpwork/x.py"))

    def test_a_real_missing_repo_path_is_still_reported(self):
        t = self._task(files=[MISSING_REPO_FILE])
        hits = heal.drift(self._reload(t))
        self.assertEqual(len(hits), 1)
        self.assertIn("no longer exists", hits[0]["detail"])

    def test_a_missing_scratchpad_path_is_not_reported(self):
        t = self._task(files=[SCRATCHPAD_FILE])
        self.assertEqual(heal.drift(self._reload(t)), [])

    def test_a_missing_system_temp_path_is_not_reported(self):
        t = self._task(files=[SYSTEM_TEMP_FILE])
        self.assertEqual(heal.drift(self._reload(t)), [])

    def test_an_ephemeral_session_worktree_is_not_reported_either(self):
        t = self._task(session_meta={"s1": {"cwd": "/private/tmp/sess-1/scratchpad"}})
        self.assertEqual(heal.drift(self._reload(t)), [])

    def test_the_real_one_is_still_found_among_the_ephemeral_ones(self):
        # The direction that proves the exclusion did not silence the check itself.
        t = self._task(files=[SCRATCHPAD_FILE, MISSING_REPO_FILE, SYSTEM_TEMP_FILE])
        hits = heal.drift(self._reload(t))
        self.assertEqual([h["ref"] for h in hits], [MISSING_REPO_FILE])

    def test_they_are_counted_separately_and_are_not_findings(self):
        t = self._task(decisions=["a call"],
                       files=[SCRATCHPAD_FILE, SYSTEM_TEMP_FILE])
        result = heal.scan(self._reload(t))
        self.assertEqual(result["findings"], [])
        self.assertEqual(len(result["ephemeral"]), 2)

    def test_they_never_make_a_heal_due(self):
        t = self._task(decisions=["a call"],
                       files=[SCRATCHPAD_FILE, SYSTEM_TEMP_FILE])
        result = heal.scan(self._reload(t))
        self.assertFalse(heal.due(self._reload(t), result=result)[0])

    def test_an_ephemeral_path_that_still_exists_is_not_counted(self):
        p = os.path.join(self.tmp, "scratchpad", "brief.md")
        os.makedirs(os.path.dirname(p))
        with open(p, "w") as f:
            f.write("x")
        t = self._task(files=[p])
        self.assertEqual(heal.vanished_ephemeral(self._reload(t)), [])

    def test_the_scan_counts_them_on_one_line_and_never_lists_them(self):
        # One line, not seven bullets. Seven bullets naming seven wiped files read
        # exactly like seven defects, which is how this got reported in the first place.
        t = self._task(decisions=["a call"],
                       files=[SCRATCHPAD_FILE, SYSTEM_TEMP_FILE])
        out = self._heal(t, scan=True)
        self.assertIn("Expected-ephemeral paths", out)
        self.assertIn("EXPECTED", out)
        self.assertNotIn(SCRATCHPAD_FILE, out)
        self.assertNotIn("no longer exists", out)
        self.assertNotIn("YES", out.rsplit("Heal due?", 1)[-1])


class TestScanIsReadOnly(_Base):
    def test_the_scan_never_modifies_the_task(self):
        big = "z" * (heal.OVERSIZE_CHARS + 10)
        t = self._task(decisions=["was wrong earlier", big],
                       steps=[{"text": "STALE step", "done": False}],
                       files=[MISSING_REPO_FILE],
                       prs=[{"url": "https://example.invalid/pr/1", "desc": ""}])
        self._memo_with_ack(t, corrects=["decision:1"])
        before = json.dumps(store.strip_rev(self._reload(t)), sort_keys=True, default=str)
        result = heal.scan(self._reload(t))
        after = json.dumps(store.strip_rev(self._reload(t)), sort_keys=True, default=str)
        self.assertEqual(before, after)
        self.assertTrue(result["findings"])

    def test_the_scan_runs_every_check_and_reports_each_one(self):
        t = self._task(decisions=["a"])
        lines = "\n".join(heal.scan_lines(heal.scan(self._reload(t))))
        for _slug, title in heal.CHECKS:
            self.assertIn(title, lines)

    def test_findings_are_grouped_in_check_order(self):
        big = "q" * (heal.OVERSIZE_CHARS + 5)
        t = self._task(decisions=[big, "decision 1 is no longer true"])
        self._memo_with_ack(t)
        order = [f["check"] for f in heal.scan(self._reload(t))["findings"]]
        self.assertEqual(order, sorted(order, key=heal.CHECK_ORDER.index))


# ---------------------------------------------------------------------------
# MERGE CANDIDATES — proposals the scan offers, and never performs.
#
# The judgment list says "MERGE what is TRUE BUT NO LONGER LOAD-BEARING" and then left
# the reconciler to go and find them. On one real 99-decision task a human found all
# sixteen by matching how each entry OPENS. That much is mechanical, so the scan offers
# it — as a proposal. It must never count as an issue: a well-reconciled task carrying
# four release records is not broken, and saying it is would be the cry-wolf failure
# this module has already had to fix four times.
# ---------------------------------------------------------------------------

class TestMergeCandidates(_Base):
    FIVE_RELEASES = ["2.9.0 SHIPPED: supersession, pins and memo dispositions",
                     "2.10.0 SHIPPED: the board story column",
                     "2.11.0 SHIPPED: category subgroups",
                     "2.12.0 SHIPPED: the drift check earns its findings",
                     "2.13.0 SHIPPED: the heal stamp and the step verb"]
    THREE_UNRELATED = ["chose sqlite over flat files for the store",
                       "terminal tint uses the full sands palette",
                       "delegation spawns one worker per repository"]

    def test_five_decisions_sharing_a_version_shape_are_one_candidate_group(self):
        t = self._task(decisions=self.FIVE_RELEASES)
        cands = heal.merge_candidates(self._reload(t))
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["indices"], [1, 2, 3, 4, 5])
        self.assertIn("<version>", cands[0]["shape"])

    def test_three_unrelated_decisions_are_not_grouped(self):
        t = self._task(decisions=self.THREE_UNRELATED)
        self.assertEqual(heal.merge_candidates(self._reload(t)), [])

    def test_a_group_already_merged_away_is_not_proposed_again(self):
        t = self._task(decisions=self.FIVE_RELEASES + ["the release trail, reconciled"])
        entries = t["decisions"]
        for i in range(1, 6):
            dec.mark_merged(entries, i, 6)
        ts.save_task(t)
        self.assertEqual(heal.merge_candidates(self._reload(t)), [])

    def test_two_sharing_a_shape_are_below_the_bar(self):
        t = self._task(decisions=self.FIVE_RELEASES[:2])
        self.assertEqual(heal.merge_candidates(self._reload(t)), [])

    def test_candidates_are_not_findings_and_never_make_a_heal_due(self):
        t = self._task(decisions=self.FIVE_RELEASES + self.THREE_UNRELATED)
        result = heal.scan(self._reload(t))
        self.assertEqual(result["findings"], [])
        self.assertEqual(len(result["merge_candidates"]), 1)
        self.assertFalse(heal.due(self._reload(t), result=result)[0])

    def test_a_candidate_group_is_proposed_and_never_merged_on_its_own(self):
        # A shape the eye matches is weaker evidence than a signature `--apply` acts on:
        # these three are OFFERED, and the mechanical plan leaves them exactly as they
        # are. Choosing the surviving summary is the reconciler's call.
        t = self._task(decisions=["MY PROCESS ERROR: renamed the file without checking",
                                  "MY PROCESS ERROR: shipped before the tests passed",
                                  "MY PROCESS ERROR: forgot to stamp the heal"])
        cands = heal.merge_candidates(self._reload(t))
        self.assertEqual([c["indices"] for c in cands], [[1, 2, 3]])
        self.assertEqual([o for o in heal.plan(self._reload(t))
                          if o["verb"] == "merge"], [])
        self._heal(t, apply=True)
        self.assertEqual(len(dec.live(self._reload(t)["decisions"])), 3)

    def test_the_version_number_itself_is_not_part_of_the_shape(self):
        # It is the one thing every release record has a DIFFERENT value of.
        self.assertEqual(heal.leading_shape("2.13.1 SHIPPED: the memo backstop"),
                         heal.leading_shape("2.9.0 SHIPPED: supersession"))

    def test_a_decision_too_thin_to_fingerprint_has_no_shape(self):
        self.assertIsNone(heal.leading_shape(""))
        self.assertIsNone(heal.leading_shape("done"))

    def test_the_scan_report_names_them_as_proposals_not_findings(self):
        t = self._task(decisions=self.FIVE_RELEASES + self.THREE_UNRELATED)
        out = self._heal(t, scan=True)
        self.assertIn("Merge candidates", out)
        self.assertIn("PROPOSALS", out)
        self.assertIn("decisions 1, 2, 3, 4, 5", out)
        self.assertNotIn("YES", out.rsplit("Heal due?", 1)[-1])


# ---------------------------------------------------------------------------
# PINNED DECISIONS — informational, and the most expensive place for a stale line.
#
# A pin puts an entry at the head of EVERY session's digest. On one real task a pinned
# decision still named two codenames a later decision had retired, and it had been
# briefing every session with them for days — nothing in the scan would ever have said
# so, because no check asks whether a decision is still ACCURATE. So the set is named,
# with ages, and it never counts as a defect: being pinned is not a problem.
# ---------------------------------------------------------------------------

class TestPinnedReReview(_Base):
    def _dated(self, task, text, days=0, pin=False):
        """Append a decision the way the CLI does — through the event feed, which is the
        only thing that can date it — then backdate that event by `days`."""
        ts.append_decision(task, text)
        n = len(task["decisions"])
        task["events"][-1]["ts"] = ts._now() - days * 86400
        if pin:
            dec.set_pin(task["decisions"], n, True)
        ts.save_task(task)
        return n

    def test_pinned_decisions_are_listed_with_their_ages(self):
        t = self._reload(self._task())
        self._dated(t, "SPINE: every worker gets its own worktree", days=9, pin=True)
        self._dated(t, "SPINE: LEGACY names never enter Projectname", days=4, pin=True)
        self._dated(t, "bumped the digest preview to 120 chars", days=1)
        pins = heal.pinned_review(self._reload(t))
        self.assertEqual([p["index"] for p in pins], [1, 2])
        self.assertEqual([int(p["age"] // 86400) for p in pins], [9, 4])

    def test_an_unpinned_decision_is_not_listed(self):
        t = self._reload(self._task())
        self._dated(t, "SPINE: every worker gets its own worktree", days=9, pin=True)
        self._dated(t, "bumped the digest preview to 120 chars", days=1)
        pins = heal.pinned_review(self._reload(t))
        self.assertEqual([p["index"] for p in pins], [1])
        self.assertNotIn("digest preview", " ".join(p["preview"] for p in pins))

    def test_a_pin_the_event_feed_cannot_date_reads_as_unknown_not_as_new(self):
        # The feed is bounded and the log is a list of strings, so an old append has no
        # timestamp anywhere. Unknown is the honest answer; a made-up age is not.
        t = self._task(decisions=["SPINE: written long before this feed existed"])
        dec.set_pin(t["decisions"], 1, True)
        ts.save_task(t)
        pins = heal.pinned_review(self._reload(t))
        self.assertEqual(len(pins), 1)
        self.assertIsNone(pins[0]["age"])
        self.assertIn("age unknown",
                      "\n".join(heal.pinned_lines({"pinned_review": pins})))

    def test_pinned_decisions_never_make_a_heal_due(self):
        t = self._task(decisions=["SPINE: one worktree per worker",
                                  "SPINE: LEGACY names never enter Projectname"])
        entries = t["decisions"]
        dec.set_pin(entries, 1, True)
        dec.set_pin(entries, 2, True)
        ts.save_task(t)
        result = heal.scan(self._reload(t))
        self.assertEqual(result["findings"], [])
        self.assertEqual(len(result["pinned_review"]), 2)
        self.assertFalse(heal.due(self._reload(t), result=result)[0])

    def test_the_scan_report_names_the_pinned_set_and_why_it_matters(self):
        t = self._reload(self._task())
        self._dated(t, "SPINE: every worker gets its own worktree", days=9, pin=True)
        self._dated(t, "SPINE: LEGACY names never enter Projectname", days=4, pin=True)
        out = self._heal(t, scan=True)
        self.assertIn("Pinned decisions", out)
        self.assertIn("brief EVERY session", out)
        self.assertIn("9d ago", out)
        self.assertNotIn("YES", out.rsplit("Heal due?", 1)[-1])

    def test_the_judgment_list_tells_the_reader_to_re_read_the_pins(self):
        t = self._task(decisions=["SPINE: one worktree per worker"])
        dec.set_pin(t["decisions"], 1, True)
        ts.save_task(t)
        self.assertIn("RE-READ every PINNED decision", self._heal(t))


# ---------------------------------------------------------------------------
# A CONSOLIDATION THAT HAS RE-FRAGMENTED — the shape grouping, extended, and the one
# place where a shape match IS a finding.
#
# A real task scanned CLEAN on all eight checks. The judgement half then found a decision
# reading "CONSOLIDATED — THE 2.7.0-2.11.0 RELEASE LINE … (replaces the five per-release
# records)" with FOUR more release-shaped decisions appended after it over the following
# day. Nobody undid the merge — the shape grew back around the entry that had just
# declared itself the single record of it, and nothing noticed.
#
# Four release records are a PROPOSAL because nobody has ruled on them. This is a FINDING
# because somebody has: the record now contradicts a ruling it already carries, and says
# two different things about how many entries that subject has. It still never proposes
# the merge — naming the surviving summary is judgement.
# ---------------------------------------------------------------------------

class TestRefragmentedConsolidation(_Base):
    RELEASES = ["2.7.0 SHIPPED: the store moved to sqlite",
                "2.8.0 SHIPPED: the board story column",
                "2.9.0 SHIPPED: supersession, pins and memo dispositions"]
    CONSOLIDATION = ("CONSOLIDATED — THE 2.7.0-2.9.0 RELEASE LINE: three releases, one "
                     "record (replaces the three per-release records). Full text of each "
                     "original: `/todo history`.")
    NEWER = ["2.10.0 SHIPPED: category subgroups",
             "2.11.0 SHIPPED: the drift check earns its findings",
             "2.12.0 SHIPPED: the heal stamp and the step verb"]

    def _consolidated(self, newer=()):
        """A task whose release line WAS consolidated — the three per-release records
        merged into one summary at decision 4 — with `newer` release-shaped decisions
        appended after it. That is the incident, reproduced."""
        t = self._task(decisions=self.RELEASES + [self.CONSOLIDATION] + list(newer))
        entries = t["decisions"]
        into = len(self.RELEASES) + 1
        for i in range(1, into):
            ok, err = dec.mark_merged(entries, i, into)
            self.assertTrue(ok, err)
        ts.save_task(t)
        return self._reload(t)

    def test_a_consolidation_the_log_grew_back_around_is_reported(self):
        hits = heal.refragmented_consolidations(self._consolidated(self.NEWER))
        self.assertEqual([h["ref"] for h in hits], ["decision 4"])
        self.assertIn("the consolidation at 4 has re-fragmented", hits[0]["detail"])
        self.assertIn("3 newer decision(s)", hits[0]["detail"])
        self.assertIn("5, 6, 7", hits[0]["detail"])

    def test_the_same_consolidation_with_no_newer_siblings_is_clean(self):
        self.assertEqual(heal.refragmented_consolidations(self._consolidated()), [])

    def test_a_sibling_recorded_BEFORE_the_consolidation_does_not_count(self):
        # Restoring one of the merged records puts a live release-shaped decision back on
        # the log — but at index 1, so the consolidation still covers it. Only accretion
        # AFTER the consolidation undoes it.
        t = self._consolidated()
        ok, err = dec.restore(t["decisions"], 1)
        self.assertTrue(ok, err)
        ts.save_task(t)
        self.assertEqual(heal.refragmented_consolidations(self._reload(t)), [])

    def test_already_merged_siblings_do_not_count(self):
        # The fix has to make the finding go quiet. A finding that survives its own
        # remedy is one nobody trusts.
        t = self._consolidated(self.NEWER)
        entries = t["decisions"]
        entries.append("CONSOLIDATED — the 2.7.0-2.12.0 release line, one record again")
        into = len(entries)
        for i in (4, 5, 6, 7):
            ok, err = dec.mark_merged(entries, i, into)
            self.assertTrue(ok, err)
        ts.save_task(t)
        self.assertEqual(heal.refragmented_consolidations(self._reload(t)), [])

    def test_a_decision_that_merely_mentions_consolidation_is_not_treated_as_one(self):
        # Decision 3 names decisions 1 and 2 and uses the word — and decision 4 shares
        # their shape, so every OTHER condition holds. The declare-vs-describe gate is
        # the only thing standing between this and a false finding.
        t = self._task(decisions=[
            "2.7.0 SHIPPED: the store moved to sqlite",
            "2.8.0 SHIPPED: the board story column",
            "the merge verb writes a consolidation into the record, which is why nothing "
            "merges decisions 1 and 2 from their shape alone",
            "2.9.0 SHIPPED: supersession and pins"])
        self.assertEqual(heal.refragmented_consolidations(self._reload(t)), [])

    def test_a_hand_written_consolidation_naming_its_indices_in_prose_is_recognised(self):
        # The same claim without the merge marks. The prose source needs BOTH conditions:
        # the declaration, and decision-shaped targets it actually names.
        t = self._task(decisions=[
            "2.7.0 SHIPPED: the store moved to sqlite",
            "2.8.0 SHIPPED: the board story column",
            "CONSOLIDATED — this decision consolidates decisions 1 and 2 into one "
            "release record",
            "2.9.0 SHIPPED: supersession and pins"])
        hits = heal.refragmented_consolidations(self._reload(t))
        self.assertEqual([h["ref"] for h in hits], ["decision 3"])
        self.assertIn("1 newer decision(s)", hits[0]["detail"])

    def test_declares_consolidation_tells_declaring_from_describing(self):
        for declaring in ("CONSOLIDATED — the 2.7.0-2.11.0 release line",
                          "this decision consolidates 4, 9 and 17",
                          "one reconciled record of 5 decisions",
                          "the release line in one record: replaces the five entries"):
            self.assertTrue(heal.declares_consolidation(declaring), declaring)
        for describing in ("a consolidation of the release trail is judgement",
                           "a wrong merge writes a false consolidation into the record",
                           "sqlite replaces flat files as the store"):
            self.assertFalse(heal.declares_consolidation(describing), describing)

    def test_a_consolidation_that_was_itself_replaced_is_not_reported(self):
        # A replaced decision briefs nobody, so there is no contradiction left to report.
        t = self._consolidated(self.NEWER)
        ok, err = dec.mark_superseded(t["decisions"], 4, 7)
        self.assertTrue(ok, err)
        ts.save_task(t)
        self.assertEqual(heal.refragmented_consolidations(self._reload(t)), [])

    def test_a_decision_too_thin_to_fingerprint_never_matches_another_thin_one(self):
        t = self._task(decisions=["a", "b", "one reconciled record of 2 decisions", "c"])
        entries = t["decisions"]
        dec.mark_merged(entries, 1, 3)
        dec.mark_merged(entries, 2, 3)
        ts.save_task(t)
        self.assertEqual(heal.refragmented_consolidations(self._reload(t)), [])

    def test_it_is_a_finding_that_counts_as_an_issue_and_may_make_a_heal_due(self):
        t = self._consolidated(self.NEWER)
        result = heal.scan(t)
        self.assertEqual([f["check"] for f in result["findings"]], ["refragmented"])
        self.assertTrue(heal.due(t, result=result)[0])

    def test_the_re_fragmentation_is_reported_and_never_merged_for_you(self):
        # The strays may well ALSO be a mechanical release cluster — that is the plan's
        # business. What must never happen is the plan folding the CONSOLIDATION in on
        # its own: choosing the surviving summary is judgement.
        t = self._consolidated(self.NEWER)
        for op in heal.plan(t):
            self.assertNotIn(4, op.get("indices") or [])
        detail = heal.refragmented_consolidations(t)[0]["detail"]
        self.assertIn("NOT proposed for you", detail)
        self.assertIn("judgement", detail)

    def test_a_freshly_applied_merge_never_reports_its_own_summary(self):
        # `--apply` writes the summary itself, and it declares itself a consolidation on
        # purpose. It must not then trip the check that reads consolidations.
        t = self._task(decisions=["2.7.0 SHIPPED: sqlite", "2.8.0 SHIPPED: the board",
                                  "2.9.0 SHIPPED: supersession"])
        self._heal(t, apply=True)
        after = self._reload(t)
        self.assertEqual(heal.refragmented_consolidations(after), [])
        self.assertEqual(heal.scan(after)["findings"], [])

    def test_the_scan_report_names_the_check_and_both_numbers(self):
        out = self._heal(self._consolidated(self.NEWER), scan=True)
        self.assertIn("Re-fragmented consolidations", out)
        self.assertIn("the consolidation at 4 has re-fragmented", out)
        self.assertIn("3 newer decision(s)", out)
        self.assertIn("decision(s) 5, 6, 7", out)
        self.assertIn("YES", out.rsplit("Heal due?", 1)[-1])


# ---------------------------------------------------------------------------
# WHAT HAS ACCRUED — and the one gap a scan STRUCTURALLY cannot cover.
#
# On the same task, a RELEASE had shipped and was recorded NOWHERE: no decision, no log
# entry, no PR link. Nothing contradicted anything, because the work had happened entirely
# outside what the record holds — so there was nothing to cross-reference. There is
# deliberately NO check for that (it would be the fifth confidently-wrong check this
# subsystem has shipped). Instead the counts say how much HAS been recorded since the
# stamp, and the report names the gap in words.
#
# Informational throughout: never a finding, never an issue, never makes a heal due.
# ---------------------------------------------------------------------------

class TestAccrualAndTheGapNoScanCanCover(_Base):
    def _stamped(self, **fields):
        """A task carrying a real stamp — written by `stamp_healed`, so it has the
        four-counter baseline the accrual counts subtract from."""
        t = self._task(**fields)
        heal.stamp_healed(t, kind=heal.HEAL_KIND_MARK, note="read the whole log")
        ts.save_task(t)
        return self._reload(t)

    def test_the_counts_are_measured_from_the_stamp(self):
        t = self._stamped(decisions=["chose sqlite for the FTS index"])
        ts.append_decision(t, "2.16.0 SHIPPED: the save gap report")
        ts.append_history(t, "2.16.0 shipped")
        ts.add_pr(t, "https://example.invalid/pr/12")
        ts.append_step(t, "write the release note")
        ts.save_task(t)
        a = heal.accrual(self._reload(t))
        self.assertTrue(a["known"])
        self.assertFalse(a["never"])
        self.assertEqual((a["decisions"], a["history"], a["links"], a["steps"]),
                         (1, 1, 1, 1))

    def test_the_stamp_records_the_baseline_so_the_next_scan_counts_from_it(self):
        t = self._task(decisions=["a"])
        heal.stamp_healed(t)
        self.assertEqual(t[heal.ACCRUAL_COUNTS_FIELD]["decisions"], 1)
        ts.save_task(t)
        self.assertEqual(heal.accrual(self._reload(t))["decisions"], 0)

    def test_a_never_healed_task_reports_the_totals_and_says_which_they_are(self):
        t = self._task(decisions=["chose sqlite", "terminal tint uses the sands palette"])
        a = heal.accrual(self._reload(t))
        self.assertTrue(a["never"])
        self.assertEqual(a["decisions"], 2)
        line = heal.accrual_line(a)
        self.assertIn("no heal has ever been recorded", line)
        self.assertIn("since the task was created", line)

    def test_a_stamp_with_no_baseline_reads_as_unknown_not_as_zero(self):
        # The pre-2.17.0 stamp shape: a timestamp and a decision count, no snapshot.
        # Four zeros would read as "nothing has happened"; the truth is "nobody
        # recorded the baseline".
        t = self._task(decisions=["a", "b"], last_heal_ts=time.time() - 3600,
                       decisions_at_last_heal=1)
        a = heal.accrual(self._reload(t))
        self.assertFalse(a["known"])
        self.assertIsNone(a["decisions"])
        line = heal.accrual_line(a)
        self.assertIn("recorded no baseline", line)
        self.assertNotIn("+0", line)

    def test_a_garbled_baseline_reads_as_unknown_rather_than_crashing(self):
        t = self._task(decisions=["a"], last_heal_ts=time.time(),
                       healed_counts={"decisions": "not a number"})
        a = heal.accrual(self._reload(t))
        self.assertFalse(a["known"])
        self.assertIsNone(a["steps"])

    def test_accrual_is_never_a_finding_and_never_makes_a_heal_due(self):
        t = self._stamped(decisions=["chose sqlite for the FTS index"])
        for i in range(4):
            ts.append_decision(t, "scrub iteration %d, recorded" % i)
        ts.save_task(t)
        result = heal.scan(self._reload(t))
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["accrual"]["decisions"], 4)
        self.assertFalse(heal.due(self._reload(t), result=result)[0])

    def test_the_scan_names_the_gap_even_when_nothing_has_accrued(self):
        # THE POINT OF THE WHOLE SECTION. A clean scan is where the pass STOPS, so a gap
        # named only when something happened to accrue is one nobody reads on the run
        # that mattered — the incident scanned clean.
        t = self._stamped(decisions=["chose sqlite for the FTS index"])
        out = self._heal(t, scan=True)
        self.assertIn("Accrued since last heal", out)
        self.assertIn("+0 decisions", out)
        self.assertIn("VERIFY WHAT ACTUALLY SHIPPED", out)
        self.assertIn("STRUCTURALLY cannot cover", out)
        self.assertNotIn("YES", out.rsplit("Heal due?", 1)[-1])

    def test_the_judgment_list_names_it_as_the_one_gap_the_layer_cannot_cover(self):
        t = self._stamped(decisions=["chose sqlite for the FTS index"])
        out = self._heal(t)
        self.assertIn("VERIFY THAT EVERYTHING WHICH ACTUALLY SHIPPED", out)
        self.assertIn("Accrued since last heal", out)
        self.assertIn("STRUCTURALLY cannot cover", out)

    def test_the_apply_report_does_not_read_a_clean_scan_as_a_complete_record(self):
        t = self._task(decisions=["2.7.0 SHIPPED: sqlite", "2.8.0 SHIPPED: the board"])
        out = self._heal(t, apply=True)
        self.assertIn("STILL OUTSTANDING: nothing the scan can see", out)
        self.assertIn("shipped since the last heal has a decision", out)


# ---------------------------------------------------------------------------
# The gate file + the nag.
# ---------------------------------------------------------------------------

class TestGateAndNag(_Base):
    def test_scan_writes_a_per_task_gate_file(self):
        t = self._task(decisions=["was wrong"])
        self._heal(t, scan=True)
        self.assertTrue(os.path.exists(heal.gate_path(t["id"])))
        self.assertEqual(heal.read_gate(t["id"])["task"], t["id"])

    def test_an_unreadable_gate_reads_as_empty(self):
        os.makedirs(heal.gate_dir(), exist_ok=True)
        with open(heal.gate_path("bogus"), "w") as f:
            f.write("{not json")
        self.assertEqual(heal.read_gate("bogus"), {})

    def test_the_nag_fires_when_the_scan_found_something(self):
        t = self._task(decisions=UNLINKED_PROSE)
        line = heal.nag(self._reload(t))
        self.assertIsNotNone(line)
        self.assertIn("under-reconciled", line)
        self.assertIn("DRY RUN", line)

    def test_the_nag_self_caps_on_an_unchanged_state(self):
        t = self._task(decisions=UNLINKED_PROSE)
        self.assertIsNotNone(heal.nag(self._reload(t)))
        self.assertIsNone(heal.nag(self._reload(t)))     # same state → silent
        self.assertIsNone(heal.nag(self._reload(t)))

    def test_the_nag_re_arms_when_the_state_changes(self):
        t = self._task(decisions=UNLINKED_PROSE)
        self.assertIsNotNone(heal.nag(self._reload(t)))
        self.assertIsNone(heal.nag(self._reload(t)))
        t2 = self._reload(t)
        t2["decisions"].append("decision 2 is no longer true either")
        ts.save_task(t2)
        self.assertIsNotNone(heal.nag(self._reload(t)))   # new state → fires again

    def test_the_nag_re_arms_after_clearing_the_gate(self):
        t = self._task(decisions=UNLINKED_PROSE)
        self.assertIsNotNone(heal.nag(self._reload(t)))
        self.assertIsNone(heal.nag(self._reload(t)))
        heal.clear_gate(t["id"])
        self.assertIsNotNone(heal.nag(self._reload(t)))

    def test_a_clean_task_never_nags(self):
        t = self._task(decisions=["chose sqlite over flat files"])
        self.assertIsNone(heal.nag(self._reload(t)))

    def test_a_closed_task_never_nags(self):
        t = self._task(decisions=UNLINKED_PROSE, status=ts.STATUS_CLOSED)
        self.assertIsNone(heal.nag(self._reload(t)))

    def test_the_nag_does_not_mutate_the_task(self):
        t = self._task(decisions=UNLINKED_PROSE)
        before = json.dumps(store.strip_rev(self._reload(t)), sort_keys=True, default=str)
        heal.nag(self._reload(t))
        after = json.dumps(store.strip_rev(self._reload(t)), sort_keys=True, default=str)
        self.assertEqual(before, after)

    def test_the_session_start_hook_surfaces_the_nag(self):
        t = self._task(decisions=UNLINKED_PROSE)
        sid = "sess-heal-nag"
        ts.set_link(sid, t["id"])
        out = self._out(ts.cmd_session_start,
                        _Args(session=sid, source="startup"))
        self.assertIn("under-reconciled", out)


class TestDueLimbs(_Base):
    def test_due_when_the_scan_found_anything(self):
        t = self._task(decisions=UNLINKED_PROSE)
        is_due, reasons = heal.due(self._reload(t))
        self.assertTrue(is_due)
        self.assertTrue(any("scan found" in r for r in reasons))

    def test_due_on_ten_new_decisions_since_the_last_heal(self):
        t = self._task(decisions=["d%d" % i for i in range(10)],
                       last_heal_ts=time.time(), decisions_at_last_heal=0)
        is_due, reasons = heal.due(self._reload(t))
        self.assertTrue(is_due)
        self.assertTrue(any("new decision" in r for r in reasons))

    def test_not_due_on_nine_new_decisions(self):
        t = self._task(decisions=["d%d" % i for i in range(9)],
                       last_heal_ts=time.time(), decisions_at_last_heal=0)
        self.assertFalse(heal.due(self._reload(t))[0])

    def test_due_when_an_undispositioned_ack_exists(self):
        t = self._task()
        self._memo_with_ack(t)
        is_due, reasons = heal.due(self._reload(t))
        self.assertTrue(is_due)
        self.assertTrue(any("no disposition" in r for r in reasons))

    def test_due_after_seven_days_on_an_active_task(self):
        now = time.time()
        t = self._task(decisions=["fine"], status=ts.STATUS_ACTIVE,
                       last_heal_ts=now - 8 * 86400, decisions_at_last_heal=1)
        is_due, reasons = heal.due(self._reload(t), now=now)
        self.assertTrue(is_due)
        self.assertTrue(any("days since" in r for r in reasons))

    def test_the_age_limb_does_not_fire_on_a_non_active_task(self):
        now = time.time()
        t = self._task(decisions=["fine"], status=ts.STATUS_OPEN,
                       last_heal_ts=now - 30 * 86400, decisions_at_last_heal=1)
        self.assertFalse(heal.due(self._reload(t), now=now)[0])

    def test_a_freshly_healed_clean_task_is_not_due(self):
        now = time.time()
        t = self._task(decisions=["fine"], status=ts.STATUS_ACTIVE,
                       last_heal_ts=now, decisions_at_last_heal=1)
        self.assertFalse(heal.due(self._reload(t), now=now)[0])


# ---------------------------------------------------------------------------
# Layer 2 — the plan, the dry-run default, the backup, and --apply.
# ---------------------------------------------------------------------------

class TestPlan(_Base):
    def test_an_oversized_decision_plans_a_split_on_its_own_paragraphs(self):
        body = "\n\n".join("paragraph %d %s" % (i, "w" * 900) for i in range(5))
        t = self._task(decisions=[body])
        ops = heal.plan(self._reload(t))
        splits = [o for o in ops if o["verb"] == "split"]
        self.assertEqual(len(splits), 1)
        self.assertEqual(splits[0]["index"], 1)
        self.assertEqual(len(splits[0]["parts"]), 5)
        self.assertFalse(splits[0]["manual"])

    def test_an_oversized_decision_with_no_structure_is_left_to_judgment(self):
        t = self._task(decisions=["z" * (heal.OVERSIZE_CHARS + 10)])
        ops = heal.plan(self._reload(t))
        splits = [o for o in ops if o["verb"] == "split"]
        self.assertEqual(splits[0]["parts"], [])
        self.assertTrue(splits[0]["manual"])

    def test_split_parts_caps_the_fan_out(self):
        body = "\n\n".join("para %d" % i for i in range(40))
        parts = heal.split_parts(body, max_parts=4, limit=10)
        self.assertEqual(len(parts), 4)
        self.assertIn("para 39", parts[-1])      # the tail collapsed into the last part

    def test_split_parts_falls_back_to_bullets_then_sentences(self):
        bullets = "- one thing\n- two thing\n- three thing"
        self.assertEqual(len(heal.split_parts(bullets)), 3)
        sentences = "First call. Second call. Third call."
        self.assertEqual(len(heal.split_parts(sentences, limit=12)), 3)

    def test_split_parts_returns_nothing_for_an_unstructured_body(self):
        self.assertEqual(heal.split_parts("oneword"), [])
        self.assertEqual(heal.split_parts(""), [])

    def test_a_release_cluster_is_detected_at_two(self):
        t = self._task(decisions=["2.8.0 SHIPPED: hook health",
                                  "2.9.0 SHIPPED: supersession"])
        clusters = heal.merge_clusters(self._reload(t))
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0][1], [1, 2])

    def test_a_stem_cluster_needs_three_before_it_counts(self):
        two = self._task(decisions=["scrub iteration alpha step one",
                                    "scrub iteration alpha step two"])
        self.assertEqual(heal.merge_clusters(self._reload(two)), [])
        three = self._task(title="t3", decisions=["scrub iteration alpha step one",
                                                 "scrub iteration alpha step two",
                                                 "scrub iteration alpha step three"])
        clusters = heal.merge_clusters(self._reload(three))
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0][1], [1, 2, 3])

    def test_unrelated_decisions_are_never_clustered(self):
        t = self._task(decisions=["chose sqlite over flat files for the store",
                                  "terminal tint uses the full sands palette",
                                  "delegation spawns one worker per repository"])
        self.assertEqual(heal.merge_clusters(self._reload(t)), [])

    def test_already_replaced_decisions_are_not_clustered(self):
        t = self._task(decisions=["1.0.0 SHIPPED: a", "1.1.0 SHIPPED: b", "summary"])
        entries = t["decisions"]
        dec.mark_merged(entries, 1, 3)
        ts.save_task(t)
        self.assertEqual(heal.merge_clusters(self._reload(t)), [])

    def test_a_generated_merge_summary_does_not_re_trip_the_prose_check(self):
        # A decision this pass WRITES must not be flagged by the scan that wrote it.
        # An earlier draft of the summary said "no longer load-bearing" — itself one of
        # SUPERSESSION_LANGUAGE — so every healed task immediately read as dirty again.
        summary = heal._merge_summary("release record", [1, 2], {"seq": 7})
        self.assertEqual(heal.matched_language(summary, heal.SUPERSESSION_LANGUAGE), [])

    def test_a_freshly_applied_heal_leaves_no_new_findings_of_its_own(self):
        t = self._task(decisions=["1.0.0 SHIPPED: a", "1.1.0 SHIPPED: b"])
        self._heal(t, apply=True)
        after = heal.scan(self._reload(t))
        self.assertEqual(after["findings"], [])
        self.assertFalse(heal.due(self._reload(t))[0])

    def test_every_undispositioned_ack_plans_a_retro_disposition(self):
        t = self._task()
        self._memo_with_ack(t, sid="aaaa1111")
        ops = heal.plan(self._reload(t))
        disp = [o for o in ops if o["verb"] == "disposition"]
        self.assertEqual(len(disp), 1)
        self.assertEqual(disp[0]["kind"], "noop")
        self.assertEqual(disp[0]["sid"], "aaaa1111")


class TestDryRunIsTheDefault(_Base):
    def _snapshot(self, t):
        return json.dumps(store.strip_rev(self._reload(t)), sort_keys=True, default=str)

    def test_a_bare_heal_changes_nothing(self):
        body = "\n\n".join("para %d %s" % (i, "w" * 900) for i in range(5))
        t = self._task(decisions=[body, "1.0.0 SHIPPED: x", "1.1.0 SHIPPED: y"])
        self._memo_with_ack(t)
        before = self._snapshot(t)
        out = self._heal(t)
        self.assertIn("[HEAL]", out)
        self.assertIn("DRY RUN", out)
        self.assertEqual(before, self._snapshot(t))

    def test_the_dry_run_prints_the_plan_it_would_perform(self):
        t = self._task(decisions=["1.0.0 SHIPPED: x", "1.1.0 SHIPPED: y"])
        out = self._heal(t)
        self.assertIn("MECHANICAL PLAN", out)
        self.assertIn("merge  decisions 1, 2", out)

    def test_the_dry_run_lists_the_full_current_decision_set(self):
        t = self._task(decisions=["alpha call", "beta call"])
        out = self._heal(t)
        self.assertIn("CURRENT DECISIONS", out)
        self.assertIn("alpha call", out)
        self.assertIn("beta call", out)

    def test_scan_only_changes_nothing_either(self):
        t = self._task(decisions=["the earlier call was wrong"])
        before = self._snapshot(t)
        out = self._heal(t, scan=True)
        self.assertIn("[HEAL-SCAN]", out)
        self.assertEqual(before, self._snapshot(t))

    def test_the_dry_run_writes_no_backup(self):
        t = self._task(decisions=["was wrong"])
        self._heal(t)
        self.assertFalse(os.path.exists(heal.backup_path(t["id"])))


class TestApply(_Base):
    def test_apply_writes_the_backup_before_mutating(self):
        t = self._task(decisions=["1.0.0 SHIPPED: x", "1.1.0 SHIPPED: y"])
        out = self._heal(t, apply=True)
        path = heal.backup_path(t["id"])
        self.assertTrue(os.path.exists(path))
        self.assertIn(path, out)
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
        # The backup holds the PRE-heal blob: two decisions, neither one merged.
        self.assertEqual(len(saved["decisions"]), 2)
        self.assertFalse(dec.is_replaced(saved["decisions"][0]))

    def test_apply_refuses_when_the_backup_cannot_be_written(self):
        t = self._task(decisions=["1.0.0 SHIPPED: x", "1.1.0 SHIPPED: y"])
        before = json.dumps(store.strip_rev(self._reload(t)), sort_keys=True, default=str)
        real = heal.backup
        heal.backup = lambda task, strip=None: None
        try:
            out = self._heal(t, apply=True)
        finally:
            heal.backup = real
        self.assertIn("REFUSED", out)
        self.assertEqual(before, json.dumps(store.strip_rev(self._reload(t)),
                                            sort_keys=True, default=str))

    def test_apply_merges_a_cluster_and_shrinks_the_digest(self):
        t = self._task(decisions=["1.0.0 SHIPPED: a", "1.1.0 SHIPPED: b",
                                  "1.2.0 SHIPPED: c"])
        before = len(dec.live(self._reload(t)["decisions"]))
        self._heal(t, apply=True)
        after = self._reload(t)
        self.assertEqual(before, 3)
        self.assertEqual(len(dec.live(after["decisions"])), 1)   # the summary only
        self.assertEqual(len(after["decisions"]), 4)             # nothing deleted

    def test_apply_splits_an_oversized_decision(self):
        body = "\n\n".join("paragraph %d %s" % (i, "w" * 900) for i in range(5))
        t = self._task(decisions=[body])
        self._heal(t, apply=True)
        after = self._reload(t)
        self.assertEqual(len(after["decisions"]), 6)             # original + 5 parts
        self.assertEqual(dec.replacement(after["decisions"][0]),
                         (dec.REPLACED_SPLIT, [2, 3, 4, 5, 6]))

    def test_apply_keeps_every_original_in_history_marked(self):
        t = self._task(decisions=["1.0.0 SHIPPED: a", "1.1.0 SHIPPED: b"])
        self._heal(t, apply=True)
        view = ts._format_history(self._reload(t))
        self.assertIn("1.0.0 SHIPPED: a", view)
        self.assertIn("1.1.0 SHIPPED: b", view)
        self.assertEqual(view.count("MERGED into decision 3"), 2)

    def test_apply_never_deletes_a_decision(self):
        # Exercises BOTH mutating verbs at once: the body is over the oversize limit so
        # it plans a split, and the two release records plan a merge.
        body = "\n\n".join("para %d %s" % (i, "w" * 900) for i in range(5))
        t = self._task(decisions=[body, "1.0.0 SHIPPED: a", "1.1.0 SHIPPED: b"])
        originals = [dec.text(d) for d in self._reload(t)["decisions"]]
        self._heal(t, apply=True)
        after = [dec.text(d) for d in self._reload(t)["decisions"]]
        for o in originals:
            self.assertIn(o, after)

    def test_apply_is_reversible_through_restore(self):
        t = self._task(decisions=["1.0.0 SHIPPED: a", "1.1.0 SHIPPED: b"])
        self._heal(t, apply=True)
        self.assertEqual(len(dec.live(self._reload(t)["decisions"])), 1)
        self._update(t, restore_decision=[1, 2])
        live = dec.live_texts(self._reload(t)["decisions"])
        self.assertIn("1.0.0 SHIPPED: a", live)
        self.assertIn("1.1.0 SHIPPED: b", live)

    def test_apply_retro_disposes_undispositioned_acks(self):
        t = self._task(decisions=["fine"])
        self._memo_with_ack(t, sid="aaaa1111")
        self._heal(t, apply=True)
        after = self._reload(t)
        disp = after["memos"][0]["acks"][0]["disposition"]
        self.assertEqual(disp["kind"], "noop")
        self.assertIn("predates", disp["value"])
        self.assertEqual(heal.undispositioned_acks(after), [])

    def test_apply_never_touches_the_log_trail_or_history(self):
        t = self._task(decisions=["1.0.0 SHIPPED: a", "1.1.0 SHIPPED: b"])
        t2 = self._reload(t)
        ts.append_history(t2, "a milestone worth keeping")
        ts.save_task(t2)
        before = list(self._reload(t)["history"])
        self._heal(t, apply=True)
        self.assertEqual(self._reload(t)["history"], before)

    def test_apply_stamps_the_heal_so_the_next_one_is_not_immediately_due(self):
        t = self._task(decisions=["1.0.0 SHIPPED: a", "1.1.0 SHIPPED: b"])
        self._heal(t, apply=True)
        after = self._reload(t)
        self.assertIsNotNone(after.get("last_heal_ts"))
        self.assertEqual(after["decisions_at_last_heal"], len(after["decisions"]))
        self.assertEqual(heal.health(after)["new_since_heal"], 0)
        self.assertEqual(after["last_heal_kind"], heal.HEAL_KIND_APPLY)

    def test_the_applied_block_says_the_heal_was_stamped(self):
        t = self._task(decisions=["1.0.0 SHIPPED: a", "1.1.0 SHIPPED: b"])
        out = self._heal(t, apply=True)
        self.assertIn("STAMPED", out)
        self.assertIn("counts new decisions from HERE", out)

    def test_apply_reports_what_it_did_and_that_nothing_was_deleted(self):
        t = self._task(decisions=["1.0.0 SHIPPED: a", "1.1.0 SHIPPED: b"])
        out = self._heal(t, apply=True)
        self.assertIn("APPLIED", out)
        self.assertIn("NOTHING was deleted", out)
        self.assertIn("--restore-decision", out)

    def test_apply_skips_a_manual_split_and_says_so(self):
        # A plan holding ONLY manual operations performs nothing, so it refuses and
        # leaves no stamp — but it still has to say what it would not do, or the reader
        # cannot tell "there is nothing here" from "the machine cannot cut this one".
        t = self._task(decisions=["z" * (heal.OVERSIZE_CHARS + 10)])
        out = self._heal(t, apply=True)
        self.assertIn("skipped", out)
        self.assertIn("need authoring by hand", out)
        self.assertFalse(dec.is_replaced(self._reload(t)["decisions"][0]))
        self.assertIsNone(self._reload(t).get("last_heal_ts"))


# ---------------------------------------------------------------------------
# `--apply` REPORTS WHAT IT DID — it does not reprint what you just read.
#
# Measured on one small task: the dry run was 4,021 chars and `--apply` was 4,174 —
# effectively the same block twice. On a real 40-decision task the dry run is ~47,000
# chars (~12,000 tokens) and 94% of that is the decision list, so the obvious two-step
# (`heal`, then `heal --apply`) paid for it TWICE for ONE heal, and the second copy told
# the caller nothing it had not just read.
# ---------------------------------------------------------------------------

class TestApplyReportsOnlyWhatItDid(_Base):
    def _noisy(self, prefix=()):
        """A task whose dry run is dominated by its decision list — the real shape.
        The six release records give `--apply` exactly one operation to perform; the
        twelve others exist to be EXPENSIVE to reprint, which is the whole point."""
        decisions = list(prefix)
        decisions += ["1.%d.0 SHIPPED: %s" % (i, "detail " * 30) for i in range(6)]
        decisions += ["the call on %s — %s" % (topic, "reasoning " * 60)
                      for topic in ("storage", "auth", "caching", "retries", "logging",
                                    "naming", "packaging", "telemetry", "backups",
                                    "quotas", "indexing", "rollout")]
        return self._task(title="A long log", decisions=decisions)

    def test_apply_is_a_small_fraction_of_the_dry_run_on_the_same_task(self):
        t = self._noisy()
        dry = self._heal(t)                       # the dry run changes nothing…
        applied = self._heal(t, apply=True)       # …so this runs on the same task
        self.assertLess(len(applied), len(dry) * 0.3,
                        "--apply reprinted the dry run: %d chars against %d"
                        % (len(applied), len(dry)))

    def test_apply_does_not_reprint_the_decision_list(self):
        t = self._noisy()
        out = self._heal(t, apply=True)
        self.assertNotIn("CURRENT DECISIONS", out)
        self.assertNotIn("reasoning reasoning", out)      # no decision body, either

    def test_apply_does_not_reprint_the_scan_or_the_judgment_list(self):
        t = self._noisy()
        out = self._heal(t, apply=True)
        self.assertNotIn("SCAN (layer 1", out)
        self.assertNotIn("NOW DO THE JUDGMENT WORK", out)
        self.assertNotIn("Merge candidates", out)         # the proposals section
        self.assertNotIn("Undispositioned acks", out)     # the per-check roll-call

    def test_apply_reports_the_operations_the_backup_and_the_new_health(self):
        t = self._noisy()
        out = self._heal(t, apply=True)
        self.assertIn("APPLIED 1 operation(s), skipped 0", out)
        self.assertIn("merged", out)
        self.assertIn(heal.backup_path(t["id"]), out)
        self.assertIn("HEALTH NOW:", out)
        self.assertIn("current decision(s)", out)

    def test_apply_says_when_nothing_is_still_outstanding(self):
        t = self._noisy()
        out = self._heal(t, apply=True)
        self.assertIn("STILL OUTSTANDING: nothing", out)

    def test_apply_says_when_judgment_work_is_still_outstanding(self):
        # The merge is mechanical and lands; the unlinked prose supersession is not, so
        # it survives the apply — and the caller has to be told, in one line, not by
        # being handed the whole block again.
        t = self._noisy(prefix=UNLINKED_PROSE)
        out = self._heal(t, apply=True)
        self.assertIn("STILL OUTSTANDING:", out)
        self.assertIn("issue(s)", out)
        self.assertNotIn("STILL OUTSTANDING: nothing", out)

    def test_verbose_restores_the_full_block(self):
        t = self._noisy()
        out = self._heal(t, apply=True, verbose=True)
        self.assertIn("CURRENT DECISIONS", out)
        self.assertIn("NOW DO THE JUDGMENT WORK", out)
        self.assertIn("APPLIED", out)

    def test_verbose_changes_the_rendering_and_nothing_else(self):
        quiet, loud = self._noisy(), self._noisy()
        self._heal(quiet, apply=True)
        self._heal(loud, apply=True, verbose=True)
        self.assertEqual(len(dec.live(self._reload(quiet)["decisions"])),
                         len(dec.live(self._reload(loud)["decisions"])))
        self.assertEqual(self._reload(quiet)["last_heal_kind"],
                         self._reload(loud)["last_heal_kind"])


# ---------------------------------------------------------------------------
# AN `--apply` THAT DID NOTHING MUST NOT STAMP.
#
# It used to: a bare `--apply` with an empty mechanical plan performed zero operations,
# said so honestly — and stamped the heal anyway, so the record claimed the task had been
# reconciled when nothing had happened. That is the exact mistake someone makes when they
# assume `--apply` IS the heal. A stamp that is sometimes a lie is worse than the
# always-on alarm the stamp was added to fix, because it makes every other stamp
# unreadable too.
# ---------------------------------------------------------------------------

class TestApplyThatPerformedNothingRefuses(_Base):
    JUDGEMENT_ONLY = ["chose sqlite over flat files"]

    def test_a_bare_apply_with_an_empty_plan_does_not_stamp(self):
        t = self._task(decisions=self.JUDGEMENT_ONLY)
        out = self._heal(t, apply=True)
        self.assertIn("REFUSED", out)
        after = self._reload(t)
        self.assertIsNone(after.get("last_heal_ts"))
        self.assertTrue(heal.health(after)["never_healed"])

    def test_the_refusal_names_the_two_real_options(self):
        t = self._task(decisions=self.JUDGEMENT_ONLY)
        out = self._heal(t, apply=True)
        self.assertIn("TWO REAL OPTIONS", out)
        self.assertIn("--dispose-acks", out)             # 1: name an operation
        self.assertIn("--merge", out)
        self.assertIn("--mark-healed", out)              # 2: it was judgement alone
        self.assertIn("--note", out)

    def test_the_refusal_changes_nothing_at_all_and_writes_no_backup(self):
        t = self._task(decisions=self.JUDGEMENT_ONLY)
        before = json.dumps(store.strip_rev(self._reload(t)), sort_keys=True, default=str)
        self._heal(t, apply=True)
        self.assertEqual(before, json.dumps(store.strip_rev(self._reload(t)),
                                            sort_keys=True, default=str))
        self.assertFalse(os.path.exists(heal.backup_path(t["id"])))

    def test_one_real_operation_still_stamps(self):
        t = self._task(decisions=["1.0.0 SHIPPED: a", "1.1.0 SHIPPED: b"])
        out = self._heal(t, apply=True)
        self.assertNotIn("REFUSED", out)
        self.assertIn("APPLIED 1 operation(s)", out)
        after = self._reload(t)
        self.assertIsNotNone(after.get("last_heal_ts"))
        self.assertEqual(after["last_heal_kind"], heal.HEAL_KIND_APPLY)

    def test_a_retro_disposition_is_a_real_operation_too(self):
        t = self._task(decisions=["a call"])
        self._memo_with_ack(t, sid="aaaa1111")
        out = self._heal(t, apply=True)
        self.assertNotIn("REFUSED", out)
        self.assertIsNotNone(self._reload(t).get("last_heal_ts"))

    def test_the_dry_run_warns_that_a_bare_apply_would_be_refused(self):
        t = self._task(decisions=self.JUDGEMENT_ONLY)
        out = self._heal(t)
        self.assertIn("REFUSES", out)
        self.assertIn("--mark-healed", out)

    def test_mark_healed_is_the_honest_record_of_that_pass(self):
        t = self._task(decisions=self.JUDGEMENT_ONLY)
        self._heal(t, apply=True)                        # refused: nothing stamped
        self.assertIsNone(self._reload(t).get("last_heal_ts"))
        out = self._heal(t, mark_healed=True,
                         note="read every decision; all still current")
        self.assertIn("MARKED HEALED", out)
        after = self._reload(t)
        self.assertIsNotNone(after.get("last_heal_ts"))
        self.assertEqual(after["last_heal_kind"], heal.HEAL_KIND_MARK)

    def test_scan_and_apply_together_are_refused_rather_than_silently_scanning(self):
        # `/heal` runs --scan for the caller, so an --apply typed alongside it would be
        # swallowed by the read-only path and the caller would think a heal had run.
        t = self._task(decisions=["1.0.0 SHIPPED: a", "1.1.0 SHIPPED: b"])
        out = self._heal(t, scan=True, apply=True)
        self.assertIn("read-only", out)
        self.assertEqual(len(dec.live(self._reload(t)["decisions"])), 2)
        self.assertIsNone(self._reload(t).get("last_heal_ts"))


class TestApplyPrimitive(_Base):
    def test_apply_appends_replacements_before_marking_originals(self):
        task = {"id": "x", "decisions": ["compound one", "1.0.0 SHIPPED: a",
                                         "1.1.0 SHIPPED: b"]}
        ops = [{"verb": "split", "index": 1, "parts": ["p1", "p2"], "manual": False,
                "why": "test"},
               {"verb": "merge", "indices": [2, 3], "into": "release trail",
                "manual": False, "why": "test"}]
        lines, applied, skipped = heal.apply(task, ops)
        self.assertEqual((applied, skipped), (2, 0))
        # 3 originals + 2 split parts + 1 merge summary
        self.assertEqual(len(task["decisions"]), 6)
        self.assertEqual(dec.replacement(task["decisions"][0]),
                         (dec.REPLACED_SPLIT, [4, 5]))
        self.assertEqual(dec.replacement(task["decisions"][1]),
                         (dec.REPLACED_MERGED, [6]))
        self.assertTrue(any("split decision 1" in ln for ln in lines))

    def test_apply_on_an_empty_op_list_does_nothing(self):
        task = {"id": "x", "decisions": ["a"]}
        self.assertEqual(heal.apply(task, []), ([], 0, 0))
        self.assertEqual(task["decisions"], ["a"])

    def test_apply_reports_an_unknown_verb_rather_than_guessing(self):
        task = {"id": "x", "decisions": ["a"]}
        lines, applied, skipped = heal.apply(task, [{"verb": "delete", "manual": False}])
        self.assertEqual((applied, skipped), (0, 1))
        self.assertIn("unknown operation", lines[0])

    def test_a_disposition_is_never_invented_for_a_missing_ack(self):
        task = {"id": "x", "decisions": [], "memos": []}
        lines, applied, skipped = heal.apply(
            task, [{"verb": "disposition", "memo": "nope", "sid": "nope",
                    "kind": "noop", "value": "r", "manual": False}])
        self.assertEqual((applied, skipped), (0, 1))
        self.assertIn("could not find ack", lines[0])


# ---------------------------------------------------------------------------
# Scope, targeting, and the explicit verbs on the CLI.
# ---------------------------------------------------------------------------

class TestScopeAndTargeting(_Base):
    def test_all_warns_loudly_about_its_scope_before_anything_happens(self):
        a = self._task(title="first", decisions=["was wrong"])
        b = self._task(title="second", decisions=["also wrong"])
        out = self._out(ts.cmd_heal, _Args(all=True))
        self.assertIn("SCOPE", out)
        self.assertIn("2 open/active task(s)", out)
        self.assertIn("#%s" % a["seq"], out)
        self.assertIn("#%s" % b["seq"], out)
        self.assertIn("Nothing will be changed", out)

    def test_all_with_apply_says_it_will_change_every_task(self):
        self._task(title="first", decisions=["was wrong"])
        out = self._out(ts.cmd_heal, _Args(all=True, apply=True))
        self.assertIn("SCOPE", out)
        self.assertIn("BACKED UP", out)

    def test_a_bare_heal_with_no_attached_task_explains_the_options(self):
        out = self._out(ts.cmd_heal, _Args(session="unattached-sid"))
        self.assertIn("No task attached", out)
        self.assertIn("--all", out)

    def test_an_unknown_task_ref_reports_no_match(self):
        out = self._out(ts.cmd_heal, _Args(task="99999"))
        self.assertIn("No task matching", out)

    def test_heal_targets_the_attached_task_by_default(self):
        t = self._task(decisions=["alpha decision"])
        sid = "sess-attached"
        ts.set_link(sid, t["id"])
        out = self._out(ts.cmd_heal, _Args(session=sid))
        self.assertIn("alpha decision", out)


class TestExplicitVerbsOnTheCli(_Base):
    def test_heal_split_marks_the_named_decision(self):
        t = self._task(decisions=["compound", "part one", "part two"])
        out = self._heal(t, split=1, into="2,3")
        self.assertIn("split decision 1 into 2, 3", out)
        after = self._reload(t)
        self.assertEqual(dec.replacement(after["decisions"][0]),
                         (dec.REPLACED_SPLIT, [2, 3]))
        self.assertEqual(len(after["decisions"]), 3)      # nothing deleted

    def test_heal_split_without_into_explains_what_is_missing(self):
        t = self._task(decisions=["compound", "other"])
        out = self._heal(t, split=1)
        self.assertIn("--into", out)
        self.assertFalse(dec.is_replaced(self._reload(t)["decisions"][0]))

    def test_heal_merge_marks_every_member(self):
        t = self._task(decisions=["one", "two", "summary"])
        out = self._heal(t, merge="1,2", into="3")
        self.assertIn("merged 1, 2 into 3", out)
        after = self._reload(t)
        self.assertEqual(dec.live_texts(after["decisions"]), ["summary"])

    def test_heal_merge_requires_exactly_one_into(self):
        t = self._task(decisions=["one", "two", "summary"])
        out = self._heal(t, merge="1,2", into="3,1")
        self.assertIn("--into <n>", out)

    def test_the_explicit_verbs_report_a_bad_index_loudly(self):
        t = self._task(decisions=["only one"])
        out = self._heal(t, split=9, into="1")
        self.assertIn("no such decision", out)

    def test_split_int_list_parses_the_shapes_the_cli_accepts(self):
        self.assertEqual(ts._split_int_list("3,7, 9"), [3, 7, 9])
        self.assertEqual(ts._split_int_list("4"), [4])
        self.assertEqual(ts._split_int_list([1, 2]), [1, 2])
        self.assertEqual(ts._split_int_list(None), [])
        self.assertEqual(ts._split_int_list("a,b"), [])


class TestTodoRouting(_Base):
    def test_heal_is_a_todo_subcommand(self):
        self.assertIn("heal", ts._TODO_SUBCMDS)

    def test_todo_heal_runs_a_dry_run(self):
        t = self._task(decisions=["alpha decision"])
        sid = "sess-todo-heal"
        ts.set_link(sid, t["id"])
        out = self._out(ts.cmd_render,
                        _Args(session=sid, arg="heal", format=None))
        self.assertIn("[HEAL]", out)
        self.assertIn("DRY RUN", out)

    def test_todo_heal_scan_routes_the_flag_through(self):
        t = self._task(decisions=["alpha"])
        sid = "sess-todo-scan"
        ts.set_link(sid, t["id"])
        out = self._out(ts.cmd_render,
                        _Args(session=sid, arg="heal --scan", format=None))
        self.assertIn("[HEAL-SCAN]", out)

    def test_todo_heal_takes_a_bare_task_number(self):
        t = self._task(decisions=["alpha decision"])
        out = self._out(ts.cmd_render,
                        _Args(session="other-sid", arg="heal %s" % t["seq"],
                              format=None))
        self.assertIn("alpha decision", out)

    def test_heal_appears_in_the_command_help(self):
        self.assertTrue(any(c == "/todo heal" for c, _ in ts._COMMANDS_HELP))

    def test_the_model_facing_guidance_documents_heal_and_restore(self):
        # guidance is how the model learns the CLI — a verb missing from it is a verb
        # that never gets used.
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_guidance(_Args())
        out = buf.getvalue()
        self.assertIn("heal", out)
        self.assertIn("--restore-decision", out)
        self.assertIn("--split", out)
        self.assertIn("--merge", out)

    def test_the_bare_alias_loop_installs_heal(self):
        path = os.path.join(_REPO_ROOT, "hooks", "on_session_start.sh")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        m = re.search(r"for c in ([^;]+); do", text)
        self.assertIsNotNone(m)
        members = [w.strip("'\"") for w in m.group(1).split()]
        self.assertIn("heal", members)

    def test_the_heal_command_file_exists_and_declares_its_tools(self):
        path = os.path.join(_REPO_ROOT, "commands", "heal.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("allowed-tools: Bash", text)
        self.assertIn("argument-hint:", text)
        self.assertIn("task-station.py\" heal", text)
        self.assertIn("dry run", text.lower())

    def test_the_heal_skill_exists_and_names_the_three_verbs(self):
        path = os.path.join(_REPO_ROOT, "skills", "heal", "SKILL.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("name: heal", text)
        for verb in ("supersede", "split", "merge"):
            self.assertIn(verb, text)
        self.assertIn("NON-DESTRUCTIVE", text)

    def test_the_heal_skill_documents_the_gaps_this_release_closed(self):
        # The docs were AHEAD of the code once — the skill claimed heal retro-disposes
        # stale acks while no flag existed. These assertions keep the two in step.
        path = os.path.join(_REPO_ROOT, "skills", "heal", "SKILL.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for flag in ("--mark-healed", "--dispose-acks", "--step-supersede",
                     "--step-restore"):
            self.assertIn(flag, text)

    def test_the_heal_command_file_documents_the_new_flags(self):
        path = os.path.join(_REPO_ROOT, "commands", "heal.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for flag in ("--mark-healed", "--dispose-acks", "--step-supersede"):
            self.assertIn(flag, text)

    def _skill(self):
        path = os.path.join(_REPO_ROOT, "skills", "heal", "SKILL.md")
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_the_skill_prescribes_the_scan_first_and_never_opens_on_the_dry_run(self):
        # A CLI is one-shot and cannot hold a conversation, so the ORDER is the skill's
        # job. The scan is ~700 tokens and invokes no model; the dry run is the
        # expensive one and must never be the opening move.
        text = self._skill()
        self.assertIn("heal --scan", text)
        self.assertIn("FIRST", text)
        self.assertIn("Never open with the dry run", text)

    def test_the_skill_reads_the_dry_run_once_and_says_why(self):
        text = self._skill()
        self.assertIn("ONCE", text)
        self.assertIn("94%", text)

    def test_the_skill_confirms_one_compact_plan_before_executing(self):
        text = self._skill()
        self.assertIn("numbered plan", text.lower())
        self.assertIn("Ask for confirmation ONCE", text)
        self.assertIn("change nothing", text.lower())

    def test_the_skill_says_the_user_never_types_the_flags(self):
        text = self._skill()
        self.assertIn("should never need to type", text)
        for flag in ("--apply", "--merge", "--split", "--dispose-acks", "--mark-healed"):
            self.assertIn(flag, text)

    def test_the_skill_stamps_a_clean_scan_without_reading_the_dry_run(self):
        text = self._skill()
        self.assertIn("--mark-healed --note", text)
        self.assertIn("Do not read the dry run at all", text)

    def test_the_command_file_opens_with_the_scan_not_the_dry_run(self):
        path = os.path.join(_REPO_ROOT, "commands", "heal.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("heal --scan", text)
        self.assertIn("[HEAL-SCAN]", text)
        # the argument hint must stop reading like a menu of raw operation flags
        hint = [ln for ln in text.splitlines() if ln.startswith("argument-hint:")][0]
        for flag in ("--apply", "--merge", "--split", "--mark-healed"):
            self.assertNotIn(flag, hint)

    def test_the_architecture_doc_covers_the_reconcile_model(self):
        path = os.path.join(_REPO_ROOT, "docs", "ARCHITECTURE.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("reconcile model", text)
        self.assertIn("split_into", text)
        self.assertIn("merged_into", text)

    def test_the_architecture_doc_covers_the_stamp_the_steps_and_the_retro_fills(self):
        path = os.path.join(_REPO_ROOT, "docs", "ARCHITECTURE.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("last_heal_ts", text)
        self.assertIn("--mark-healed", text)
        self.assertIn("--step-supersede", text)
        self.assertIn("steps.py", text)
        self.assertIn("retro", text)

    def test_the_skill_covers_the_refragmentation_check_and_the_uncoverable_gap(self):
        text = self._skill()
        self.assertIn("Re-fragmented consolidations", text)
        self.assertIn("everything which actually shipped", text)
        self.assertIn("cannot cover", text)

    def test_the_command_file_covers_them_too(self):
        path = os.path.join(_REPO_ROOT, "commands", "heal.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("Re-fragmented consolidations", text)
        self.assertIn("everything which actually shipped", text)
        self.assertIn("cannot cover", text)

    def test_the_architecture_doc_covers_the_refragmentation_check_and_the_gap(self):
        path = os.path.join(_REPO_ROOT, "docs", "ARCHITECTURE.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("refragmented_consolidations", text)
        self.assertIn("healed_counts", text)
        self.assertIn("re-fragment", text)
        self.assertIn("everything which actually shipped", text)

    def test_the_architecture_doc_covers_the_orchestration_and_the_ephemeral_rule(self):
        path = os.path.join(_REPO_ROOT, "docs", "ARCHITECTURE.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("scratchpad", text.lower())
        self.assertIn("ephemeral", text.lower())
        self.assertIn("--verbose", text)

    def test_the_guidance_names_the_step_verb_and_the_stamp(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_guidance(_Args())
        out = buf.getvalue()
        self.assertIn("--step-supersede", out)
        self.assertIn("--mark-healed", out)
        self.assertIn("--dispose-acks", out)


# ---------------------------------------------------------------------------
# The two gates: they WARN and never block, and never run the heal themselves.
# ---------------------------------------------------------------------------

class TestSaveGate(_Base):
    def test_save_says_heal_first_when_one_is_due(self):
        t = self._task(decisions=UNLINKED_PROSE)
        sid = "sess-save-gate"
        ts.set_link(sid, t["id"])
        out = self._sub(ts._todo_save, _Args(session=sid))
        self.assertIn("heal first", out)

    def test_save_still_prints_the_whole_capture_block(self):
        # The gate must WARN, not block: every slot of the checklist still renders.
        t = self._task(decisions=UNLINKED_PROSE)
        sid = "sess-save-gate2"
        ts.set_link(sid, t["id"])
        out = self._sub(ts._todo_save, _Args(session=sid))
        self.assertIn("[SAVE]", out)
        self.assertIn("CAPTURE CHECKLIST", out)
        self.assertIn("COLD-READ CHECK", out)

    def test_save_names_the_wholesale_replacement_risk(self):
        t = self._task(decisions=UNLINKED_PROSE)
        sid = "sess-save-gate3"
        ts.set_link(sid, t["id"])
        out = self._sub(ts._todo_save, _Args(session=sid))
        self.assertIn("about to REPLACE", out)

    def test_save_is_silent_on_a_reconciled_task(self):
        t = self._task(decisions=["chose sqlite over flat files"])
        sid = "sess-save-clean"
        ts.set_link(sid, t["id"])
        out = self._sub(ts._todo_save, _Args(session=sid))
        self.assertNotIn("heal first", out)

    def test_save_does_not_run_the_heal(self):
        # The gate FIRES here (the prose decision is a finding) and there IS a
        # mergeable cluster — so if `save` ran the heal, the count would drop.
        t = self._task(decisions=UNLINKED_PROSE + ["1.0.0 SHIPPED: a",
                                                   "1.1.0 SHIPPED: b"])
        sid = "sess-save-nomutate"
        ts.set_link(sid, t["id"])
        out = self._sub(ts._todo_save, _Args(session=sid))
        self.assertIn("heal first", out)
        after = self._reload(t)
        self.assertEqual(len(dec.live(after["decisions"])), 4)   # nothing merged
        self.assertEqual(len(after["decisions"]), 4)             # nothing appended
        self.assertFalse(os.path.exists(heal.backup_path(t["id"])))


class TestDoneGate(_Base):
    def test_done_warns_when_the_record_is_unreconciled(self):
        t = self._task(decisions=UNLINKED_PROSE)
        out = self._out(ts.cmd_done, _Args(task=str(t["seq"])))
        self.assertIn("heal first", out)

    def test_done_still_closes_the_task(self):
        t = self._task(decisions=UNLINKED_PROSE)
        out = self._out(ts.cmd_done, _Args(task=str(t["seq"])))
        self.assertIn("Closed task", out)
        self.assertTrue(ts.is_closed(self._reload(t)))

    def test_done_via_the_session_path_also_warns_and_closes(self):
        t = self._task(decisions=UNLINKED_PROSE)
        sid = "sess-done-gate"
        ts.set_link(sid, t["id"])
        out = self._out(ts.cmd_done, _Args(session=sid))
        self.assertIn("heal first", out)
        self.assertIn("Closed task", out)
        self.assertTrue(ts.is_closed(self._reload(t)))

    def test_done_is_silent_on_a_reconciled_task(self):
        t = self._task(decisions=["chose sqlite over flat files"])
        out = self._out(ts.cmd_done, _Args(task=str(t["seq"])))
        self.assertNotIn("heal first", out)
        self.assertIn("Closed task", out)


# ---------------------------------------------------------------------------
# The heal STAMP — what makes "heal due?" mean something.
#
# Measured on one real task: after 17 merges, 5 supersedes and a split, the scan STILL
# reported `last heal never` and `97 new decision(s) since the last heal`. So the answer
# to "heal due?" was permanently YES and the count was the whole decision total — an
# alarm that is always on is the one people learn to ignore.
# ---------------------------------------------------------------------------

class TestHealStamp(_Base):
    def test_scan_never_stamps_a_heal(self):
        # --scan is READ-ONLY, and that is its whole contract. Stamping from a read
        # would claim a reconcile nobody performed.
        t = self._task(decisions=UNLINKED_PROSE)
        self._heal(t, scan=True)
        after = self._reload(t)
        self.assertIsNone(after.get("last_heal_ts"))
        self.assertNotIn("last_heal_kind", after)
        self.assertTrue(heal.health(after)["never_healed"])

    def test_mark_healed_records_the_judgement_only_pass(self):
        t = self._task(decisions=["chose sqlite over flat files"])
        out = self._heal(t, mark_healed=True, note="read every decision; all still true")
        self.assertIn("MARKED HEALED", out)
        after = self._reload(t)
        self.assertIsNotNone(after.get("last_heal_ts"))
        self.assertEqual(after["last_heal_kind"], heal.HEAL_KIND_MARK)
        self.assertEqual(after["last_heal_note"], "read every decision; all still true")
        self.assertFalse(heal.health(after)["never_healed"])

    def test_mark_healed_performs_no_operation(self):
        # There IS a mergeable cluster here; --mark-healed must not touch it.
        t = self._task(decisions=["1.0.0 SHIPPED: a", "1.1.0 SHIPPED: b"])
        self._heal(t, mark_healed=True)
        after = self._reload(t)
        self.assertEqual(len(after["decisions"]), 2)
        self.assertEqual(len(dec.live(after["decisions"])), 2)

    def test_mark_healed_shows_the_note_in_the_scan(self):
        t = self._task(decisions=["a call"])
        self._heal(t, mark_healed=True, note="nothing needed changing")
        out = self._heal(t, scan=True)
        self.assertIn("nothing needed changing", out)
        self.assertIn("marked", out)

    def test_mark_healed_backs_the_blob_up_first(self):
        t = self._task(decisions=["a call"])
        self._heal(t, mark_healed=True)
        self.assertTrue(os.path.exists(heal.backup_path(t["id"])))

    def test_mark_healed_refuses_when_the_backup_cannot_be_written(self):
        t = self._task(decisions=["a call"])
        real = heal.backup
        heal.backup = lambda task, strip=None: None
        try:
            out = self._heal(t, mark_healed=True)
        finally:
            heal.backup = real
        self.assertIn("REFUSED", out)
        self.assertIsNone(self._reload(t).get("last_heal_ts"))

    def test_mark_healed_cannot_be_combined_with_scan_or_apply(self):
        t = self._task(decisions=["a call"])
        for kw in ({"scan": True}, {"apply": True},
                   {"dispose_acks": "all", "noop": "why"}):
            out = self._heal(t, mark_healed=True, **kw)
            self.assertIn("cannot be combined", out)
            self.assertIsNone(self._reload(t).get("last_heal_ts"))

    def test_new_since_heal_counts_only_decisions_added_after_the_stamp(self):
        t = self._task(decisions=["one", "two"])
        self._heal(t, mark_healed=True)
        t2 = self._reload(t)
        t2["decisions"].append("three")
        ts.save_task(t2)
        h = heal.health(self._reload(t))
        self.assertEqual(h["new_since_heal"], 1)
        self.assertEqual(h["decisions_total"], 3)

    def test_a_never_healed_task_reports_that_plainly_not_a_count_of_new(self):
        # The wrong answer is "12 new decision(s) since the last heal" — false twice
        # over: nothing is new, and there was no last heal.
        t = self._task(decisions=["d%d" % i for i in range(12)])
        h = heal.health(self._reload(t))
        self.assertIsNone(h["new_since_heal"])
        self.assertTrue(h["never_healed"])
        _is_due, reasons = heal.due(self._reload(t))
        self.assertTrue(any("no heal has ever been recorded" in r for r in reasons))
        self.assertFalse(any("new decision(s) since the last heal" in r for r in reasons))
        self.assertIn("never", heal.health_line(h))

    def test_a_stamped_task_reports_a_real_count_of_new_decisions(self):
        now = time.time()
        t = self._task(decisions=["d%d" % i for i in range(12)],
                       last_heal_ts=now, decisions_at_last_heal=0)
        _is_due, reasons = heal.due(self._reload(t), now=now)
        self.assertTrue(any("12 new decision(s) since the last heal" in r
                            for r in reasons))

    def test_a_legacy_blob_with_no_stamp_still_reads(self):
        # A task written by a version that never heard of the stamp must scan, render and
        # report — as never healed — without growing any field.
        t = self._task(decisions=["plain one", "plain two"])
        blob = self._reload(t)
        for key in ("last_heal_ts", "decisions_at_last_heal", "last_heal_kind",
                    "last_heal_note"):
            self.assertNotIn(key, blob)
        h = heal.health(blob)
        self.assertTrue(h["never_healed"])
        self.assertIsNone(h["since_heal"])
        self.assertIsNone(h["new_since_heal"])
        out = self._heal(t, scan=True)
        self.assertIn("[HEAL-SCAN]", out)
        self.assertIn("never", out)

    def test_a_garbled_stamp_reads_as_never_healed_rather_than_crashing(self):
        t = self._task(decisions=["one"], last_heal_ts="not-a-number",
                       decisions_at_last_heal="nope")
        h = heal.health(self._reload(t))
        self.assertTrue(h["never_healed"])
        self.assertIsNone(h["new_since_heal"])

    def test_a_fresh_stamp_does_not_inherit_an_older_why(self):
        # A zero-operation --apply now REFUSES and does not stamp, so it cannot be what
        # clears the note; the clearing happens on a stamp that actually performs work.
        t = self._task(decisions=["a", "b", "summary"])
        self._heal(t, mark_healed=True, note="checked everything")
        self.assertEqual(self._reload(t)["last_heal_note"], "checked everything")
        self._heal(t, apply=True, merge="1,2", into="3")
        self.assertNotIn("last_heal_note", self._reload(t))

    def test_the_dry_run_tells_the_reader_how_to_record_a_judgement_only_heal(self):
        t = self._task(decisions=["chose sqlite over flat files"])
        out = self._heal(t)
        self.assertIn("--mark-healed", out)


# ---------------------------------------------------------------------------
# RETRO-DISPOSITION of pre-2.9.0 acks — visibly retroactive, never forged.
# ---------------------------------------------------------------------------

class TestRetroDisposeAcks(_Base):
    def test_dispose_all_retro_fills_every_undispositioned_ack(self):
        t = self._task(decisions=["a call"])
        self._memo_with_ack(t, sid="aaaa1111")
        self._memo_with_ack(t, sid="bbbb2222", text="another fact")
        out = self._heal(t, apply=True, dispose_acks="all",
                         noop="the acking sessions are gone; nothing durable was needed")
        self.assertIn("retro-dispositioned", out)
        after = self._reload(t)
        self.assertEqual(heal.undispositioned_acks(after), [])
        for m in after["memos"]:
            disp = m["acks"][0]["disposition"]
            self.assertEqual(disp["kind"], "noop")
            self.assertIn("acking sessions are gone", disp["value"])

    def test_a_retro_disposition_is_marked_retroactive_and_names_who_and_when(self):
        t = self._task(decisions=["a call"])
        self._memo_with_ack(t, sid="aaaa1111")
        self._heal(t, apply=True, dispose_acks="all", noop="nothing was needed",
                   session="sess-reconciler")
        disp = self._reload(t)["memos"][0]["acks"][0]["disposition"]
        self.assertTrue(heal.is_retro(disp))
        self.assertEqual(disp["retro_by"], "sess-reconciler")
        self.assertTrue(disp["retro_ts"] > 0)
        self.assertIn("no longer exists", disp["retro_why"])

    def test_a_retro_disposition_never_rewrites_the_original_ack(self):
        t = self._task(decisions=["a call"])
        self._memo_with_ack(t, sid="aaaa1111")
        before = self._reload(t)["memos"][0]["acks"][0]
        self._heal(t, apply=True, dispose_acks="all", noop="nothing was needed")
        after = self._reload(t)["memos"][0]["acks"][0]
        self.assertEqual(after["sid"], before["sid"])
        self.assertEqual(after["ts"], before["ts"])

    def test_a_retro_fill_is_told_apart_from_a_disposition_the_acker_chose(self):
        chosen = self._task(title="chosen")
        self._memo_with_ack(chosen, sid="cccc3333",
                            disposition={"kind": "noop", "value": "nothing needed"})
        retro = self._task(title="retro")
        self._memo_with_ack(retro, sid="dddd4444")
        self._heal(retro, apply=True, dispose_acks="all", noop="nothing was needed")
        chosen_line = ts._memo_ledger(self._reload(chosen)["memos"][0])
        retro_line = ts._memo_ledger(self._reload(retro)["memos"][0])
        self.assertEqual(chosen_line, "cccc3333→noop")            # no tag
        self.assertEqual(retro_line, "dddd4444→noop (retro)")     # tagged
        full = ts._format_memo_full(self._reload(retro),
                                    self._reload(retro)["memos"][0])
        self.assertIn("is RETRO", full)
        self.assertIn("The ack itself", full)

    def test_an_existing_disposition_is_never_overwritten(self):
        t = self._task(decisions=["a call"])
        self._memo_with_ack(t, sid="cccc3333",
                            disposition={"kind": "memory", "value": "some-slug"})
        out = self._heal(t, apply=True, dispose_acks="all", noop="nothing was needed")
        self.assertIn("no undispositioned ack", out)
        disp = self._reload(t)["memos"][0]["acks"][0]["disposition"]
        self.assertEqual(disp["kind"], "memory")
        self.assertFalse(heal.is_retro(disp))

    def test_naming_a_subset_leaves_the_others_flagged(self):
        t = self._task(decisions=["a call"])
        first = self._memo_with_ack(t, sid="aaaa1111")
        self._memo_with_ack(t, sid="bbbb2222", text="another fact")
        self._heal(t, apply=True, dispose_acks=first["id"][:8],
                   noop="only this one was recoverable")
        after = self._reload(t)
        remaining = heal.undispositioned_acks(after)
        self.assertEqual(len(remaining), 1)
        self.assertIn("bbbb2222", remaining[0]["ref"])
        self.assertTrue(heal.is_retro(after["memos"][0]["acks"][0]["disposition"]))

    def test_the_scan_stops_flagging_what_was_disposed(self):
        t = self._task(decisions=["a call"])
        self._memo_with_ack(t, sid="aaaa1111")
        self.assertEqual(len(heal.undispositioned_acks(self._reload(t))), 1)
        self._heal(t, apply=True, dispose_acks="all", noop="nothing was needed")
        out = self._heal(t, scan=True)
        self.assertIn("Undispositioned acks", out)              # the check still runs…
        self.assertNotIn("acked with NO disposition", out)      # …and has nothing to say

    def test_a_memory_disposition_records_the_slug(self):
        t = self._task(decisions=["a call"])
        self._memo_with_ack(t, sid="aaaa1111")
        self._heal(t, apply=True, dispose_acks="all", memory="projectname-env-gating")
        disp = self._reload(t)["memos"][0]["acks"][0]["disposition"]
        self.assertEqual((disp["kind"], disp["value"]),
                         ("memory", "projectname-env-gating"))
        self.assertTrue(heal.is_retro(disp))

    def test_a_decision_disposition_records_it_without_minting_a_decision(self):
        # A heal must not append a decision dated to a session that no longer exists.
        t = self._task(decisions=["a call"])
        self._memo_with_ack(t, sid="aaaa1111")
        self._heal(t, apply=True, dispose_acks="all",
                   decision="became the branch-naming rule")
        after = self._reload(t)
        disp = after["memos"][0]["acks"][0]["disposition"]
        self.assertEqual(disp["kind"], "decision")
        self.assertEqual(disp["value"], "became the branch-naming rule")
        self.assertEqual(len(after["decisions"]), 1)      # nothing was minted

    def test_no_disposition_is_an_error_naming_all_three(self):
        t = self._task(decisions=["a call"])
        self._memo_with_ack(t, sid="aaaa1111")
        out = self._heal(t, apply=True, dispose_acks="all")
        self.assertIn("--decision", out)
        self.assertIn("--memory", out)
        self.assertIn("--noop", out)
        self.assertEqual(len(heal.undispositioned_acks(self._reload(t))), 1)

    def test_two_dispositions_are_refused(self):
        t = self._task(decisions=["a call"])
        self._memo_with_ack(t, sid="aaaa1111")
        out = self._heal(t, apply=True, dispose_acks="all", noop="why", memory="slug")
        self.assertIn("exactly ONE disposition", out)
        self.assertEqual(len(heal.undispositioned_acks(self._reload(t))), 1)

    def test_a_noop_with_no_reason_is_refused(self):
        t = self._task(decisions=["a call"])
        self._memo_with_ack(t, sid="aaaa1111")
        out = self._heal(t, apply=True, dispose_acks="all", noop="   ")
        self.assertIn("requires a reason", out)
        self.assertEqual(len(heal.undispositioned_acks(self._reload(t))), 1)

    def test_an_unknown_memo_id_changes_nothing(self):
        t = self._task(decisions=["a call"])
        self._memo_with_ack(t, sid="aaaa1111")
        out = self._heal(t, apply=True, dispose_acks="deadbeef", noop="why")
        self.assertIn("no memo with that id", out)
        self.assertEqual(len(heal.undispositioned_acks(self._reload(t))), 1)

    def test_a_bad_id_alongside_a_good_one_is_all_or_nothing(self):
        t = self._task(decisions=["a call"])
        first = self._memo_with_ack(t, sid="aaaa1111")
        out = self._heal(t, apply=True, noop="why",
                         dispose_acks="%s,deadbeef" % first["id"][:8])
        self.assertIn("Nothing was changed", out)
        self.assertEqual(len(heal.undispositioned_acks(self._reload(t))), 1)

    def test_the_dry_run_shows_the_disposition_it_would_record_and_changes_nothing(self):
        t = self._task(decisions=["a call"])
        self._memo_with_ack(t, sid="aaaa1111")
        out = self._heal(t, dispose_acks="all", noop="nothing was needed")
        self.assertIn("DRY RUN", out)
        self.assertIn("(RETRO)", out)
        self.assertEqual(len(heal.undispositioned_acks(self._reload(t))), 1)

    def test_dispose_acks_cannot_sweep_the_board(self):
        self._task(decisions=["a call"])
        out = self._out(ts.cmd_heal, _Args(all=True, apply=True, dispose_acks="all",
                                           noop="why"))
        self.assertIn("cannot be combined with --all", out)

    def test_dispose_acks_is_refused_on_a_read_only_scan(self):
        t = self._task(decisions=["a call"])
        self._memo_with_ack(t, sid="aaaa1111")
        out = self._heal(t, scan=True, dispose_acks="all", noop="why")
        self.assertIn("read-only", out)
        self.assertEqual(len(heal.undispositioned_acks(self._reload(t))), 1)

    def test_the_blanket_plan_also_marks_its_retro_dispositions(self):
        # `heal --apply` with no explicit selection still retro-noops every ack — and
        # that fallback must be just as visibly retroactive.
        t = self._task(decisions=["a call"])
        self._memo_with_ack(t, sid="aaaa1111")
        self._heal(t, apply=True)
        disp = self._reload(t)["memos"][0]["acks"][0]["disposition"]
        self.assertTrue(heal.is_retro(disp))
        self.assertIn("predates", disp["value"])

    def test_the_ack_finding_names_the_command_that_fixes_it(self):
        t = self._task(decisions=["a call"])
        memo = self._memo_with_ack(t, sid="aaaa1111")
        hits = heal.undispositioned_acks(self._reload(t))
        self.assertIn("--dispose-acks %s" % memo["id"][:8], hits[0]["detail"])


# ---------------------------------------------------------------------------
# Back-compat: a task written before any of this must render identically.
# ---------------------------------------------------------------------------

class TestBackCompat(_Base):
    def test_a_legacy_task_blob_is_untouched_by_a_scan(self):
        t = self._task(decisions=["plain string one", "plain string two"])
        heal.scan(self._reload(t))
        after = self._reload(t)
        self.assertEqual(after["decisions"], ["plain string one", "plain string two"])

    def test_marking_and_restoring_round_trips_to_the_legacy_shape(self):
        for mark in (lambda e: dec.mark_superseded(e, 1, 2),
                     lambda e: dec.mark_split(e, 1, [2]),
                     lambda e: dec.mark_merged(e, 1, 2)):
            entries = ["original", "replacement"]
            mark(entries)
            self.assertIsInstance(entries[0], dict)
            dec.restore(entries, 1)
            self.assertEqual(entries[0], "original")      # byte-identical legacy shape

    def test_a_task_with_no_decisions_scans_clean_and_grows_no_fields(self):
        t = self._task()
        result = heal.scan(self._reload(t))
        self.assertEqual(result["findings"], [])
        self.assertFalse(self._reload(t).get("decisions"))

    def test_the_digest_renders_a_split_task_without_the_original(self):
        t = self._task(decisions=["compound old", "part one", "part two"])
        entries = t["decisions"]
        dec.mark_split(entries, 1, [2, 3])
        ts.save_task(t)
        detail = ts._format_detail(self._reload(t), None)
        self.assertNotIn("compound old", detail)
        self.assertIn("part one", detail)


# ---------------------------------------------------------------------------
# THE WHOLE PASS, RENDERED — the reconciliation run that exposed all four gaps.
#
# One task carrying every shape at once: decisions where the prose claims a supersession
# nothing recorded, a stale step, and a memo acked the pre-2.9.0 way (a bare receipt).
# Scan it, work it with the real commands, scan again — and read the output. A unit test
# per gap proves each mechanism; this proves the REPORT a reader actually sees.
# ---------------------------------------------------------------------------

class TestTheWholePassRendered(_Base):
    def _build(self):
        t = self._task(
            title="Reconcile me",
            decisions=UNLINKED_PROSE + ["the store lives under `<data_dir>/store`"],
            steps=[{"text": "write the tests", "done": False},
                   {"text": "STALE — do not execute: it uses the retired vocabulary",
                    "done": False},
                   {"text": "ship it", "done": False}])
        # The pre-2.9.0 ack shape: a bare {sid, ts} receipt with NO disposition. The CLI
        # cannot produce one any more (an ack now requires a disposition), so it is
        # constructed in the blob exactly as those sessions left it.
        t2 = self._reload(t)
        memo = ts.memo_send(t2, "the vocabulary changed", from_sid="sender01")
        memo.setdefault("acks", []).append({"sid": "aaaa1111", "ts": ts._now()})
        ts.save_task(t2)
        return self._reload(t), memo

    def test_the_first_scan_reports_all_four_shapes(self):
        t, memo = self._build()
        out = self._heal(t, scan=True)
        self.assertIn("acked with NO disposition", out)                 # gap 2
        self.assertIn("--dispose-acks %s" % memo["id"][:8], out)
        self.assertIn("--step-supersede 2", out)                        # gap 3
        self.assertIn("decision 2", out)                                # gap 4 (true +)
        self.assertIn("last heal never", out)                           # gap 1
        self.assertIn("Heal due?", out)
        self.assertIn("YES", out)

    def test_the_second_scan_is_clean_and_the_heal_is_no_longer_due(self):
        t, _memo = self._build()
        # 1. retro-dispose the ack the machine cannot know the intent of…
        self._heal(t, apply=True, dispose_acks="all",
                   noop="the acking session no longer exists; nothing durable was needed")
        # 2. …retire the stale step, naming the corrected one as its replacement…
        self._update(t, step_add=["use the current vocabulary"], step_supersede=[2])
        # 3. …and turn the prose supersession into structure.
        self._update(t, decision=["sqlite it is: the FTS index needs a real query engine"],
                     supersedes=[1])
        out = self._heal(t, scan=True)
        self.assertNotIn("acked with NO disposition", out)
        self.assertNotIn("reads as stale", out)
        self.assertNotIn("in prose", out)
        self.assertIn("Heal due?", out)
        self.assertNotIn("YES", out.rsplit("Heal due?", 1)[-1])
        self.assertNotIn("last heal never", out)

    def test_the_step_left_the_checklist_and_the_counter_but_not_the_history(self):
        t, _memo = self._build()
        self._update(t, step_add=["use the current vocabulary"], step_supersede=[2])
        after = self._reload(t)
        self.assertEqual(ts.step_progress(after), (0, 3))      # 4 steps, one retired
        detail = ts._format_detail(after, None)
        self.assertIn("Steps (0/3 done)", detail)
        self.assertNotIn("retired vocabulary", detail)
        self.assertIn("use the current vocabulary", detail)
        self.assertIn("1 superseded step(s)", detail)
        view = ts._format_history(after)
        self.assertIn("retired vocabulary", view)             # nothing was deleted
        self.assertIn("SUPERSEDED by step 4", view)

    def test_history_still_shows_every_mark_after_the_whole_pass(self):
        t, _memo = self._build()
        self._heal(t, apply=True, dispose_acks="all", noop="nothing durable was needed")
        self._update(t, step_add=["use the current vocabulary"], step_supersede=[2])
        self._update(t, decision=["sqlite it is: the FTS index needs a real query engine"],
                     supersedes=[1])
        view = ts._format_history(self._reload(t))
        self.assertIn("SUPERSEDED by decision 4", view)        # the decision mark
        self.assertIn("SUPERSEDED by step 4", view)            # the step mark
        self.assertIn("→noop (retro)", view)                   # the retro-filled ack
        self.assertIn("go with flat files", view)              # …and every original text
        self.assertIn("retired vocabulary", view)


if __name__ == "__main__":
    unittest.main()


class HealVerbCombinationGuard(unittest.TestCase):
    """`--into` is a single option, so `heal --split N --into A --merge X,Y --into B`
    silently keeps only the LAST --into and links the other verb to the wrong target.
    Refusing is the fix: each verb needs its own --into, so each needs its own run."""

    def test_split_and_merge_together_are_refused_not_mislinked(self):
        with tempfile.TemporaryDirectory() as home:
            env = dict(os.environ, TASK_STATION_HOME=home)
            eng = os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "lib", "task-station.py")
            run = lambda *args: subprocess.run(
                [sys.executable, eng] + list(args), env=env,
                capture_output=True, text=True)
            run("create", "--title", "guard", "--active")
            for i in range(1, 5):
                run("update", "--task", "1", "--decision", "D%d" % i)
            out = run("heal", "--task", "1", "--apply",
                      "--split", "1", "--into", "3", "--merge", "2", "--into", "4")
            self.assertIn("each need their own --into", out.stdout)
            # and nothing was changed by the refusal
            hist = run("render", "--session", "c" * 8 + "-0000-0000-0000-000000000000",
                       "--arg", "1 history")
            self.assertNotIn("SPLIT into", hist.stdout)
            self.assertNotIn("MERGED into", hist.stdout)


class HealVerbPathStamps(unittest.TestCase):
    """A split or a merge IS reconciliation, so the verb path must stamp. It used to
    return without stamping, which is why seventeen merges on a real task still left it
    reading `last heal never` — the stamp lived only on the generic --apply path."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._prev = os.environ.get("TASK_STATION_HOME")
        os.environ["TASK_STATION_HOME"] = self._tmp.name

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("TASK_STATION_HOME", None)
        else:
            os.environ["TASK_STATION_HOME"] = self._prev
        self._tmp.cleanup()

    def _run(self, *args):
        eng = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "lib", "task-station.py")
        return subprocess.run([sys.executable, eng] + list(args),
                              capture_output=True, text=True).stdout

    def test_a_merge_stamps_the_heal(self):
        self._run("create", "--title", "verb stamp", "--active")
        for t in ("a", "b", "the surviving summary"):
            self._run("update", "--task", "1", "--decision", t)
        self.assertIn("never", self._run("heal", "--task", "1", "--scan"))
        self._run("heal", "--task", "1", "--apply", "--merge", "1,2", "--into", "3")
        scan = self._run("heal", "--task", "1", "--scan")
        self.assertNotIn("last heal never", scan)
        self.assertIn("last heal", scan)

    def test_a_split_stamps_the_heal(self):
        self._run("create", "--title", "verb stamp", "--active")
        for t in ("the compound one", "part A", "part B"):
            self._run("update", "--task", "1", "--decision", t)
        self._run("heal", "--task", "1", "--apply", "--split", "1", "--into", "2,3")
        self.assertNotIn("last heal never", self._run("heal", "--task", "1", "--scan"))
