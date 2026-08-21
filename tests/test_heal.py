"""The reconcile pass — `heal`: the two new verbs, the ten scan checks, the gates.

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
        # `ref` is the POSITIONAL task — `heal --scan 12`. It defaults to None here for
        # the same reason the subparser gives it `nargs="?"`: every existing caller names
        # its task with --task, and the positional must change nothing for them.
        defaults = dict(session=None, task=None, ref=None, scan=False, apply=False,
                        all=False, verbose=False,
                        split=None, merge=None, into=None, mark_healed=False, note=None,
                        dispose_acks=None, decision=None, memory=None, noop=None,
                        # 2.26.0: the adjudication ledger, the cheap candidate view, the
                        # goal re-read stamp and the opt-in link probe. Defaulted here for
                        # the same reason `ref` is — every existing caller names none of
                        # them, and they must change nothing for those callers.
                        dismiss=None, undismiss=None, why=None, dismissals=False,
                        candidates=False, goal_reviewed=False, probe_links=False)
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
# Layer 1 — the nine deterministic checks. None of them may mutate the task.
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
        # The four appended decisions deliberately share NO opening shape and no
        # subject signal: a same-shaped run would form a merge-candidate group and
        # legitimately trip the grew-with-candidates finding (its own tests cover
        # that), which would test the size objective here instead of accrual.
        t = self._stamped(decisions=["chose sqlite for the FTS index"])
        for text in ("picked the FTS tokenizer", "renamed the export column",
                     "wired the feed cache", "documented the retry rule"):
            ts.append_decision(t, text)
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

    def test_the_skill_executes_the_plan_without_stopping_to_ask(self):
        # This test used to assert the OPPOSITE — "Ask for confirmation ONCE" — and it
        # is inverted rather than deleted, because the gate leaving is a behaviour worth
        # a regression test of its own: the ask must not creep back in as a "quick
        # check" on some later pass. What replaces it is the undo trail, so this asserts
        # both halves of the trade.
        text = self._skill()
        self.assertNotIn("Ask for confirmation", text)
        self.assertIn("no approval gate", text.lower())
        self.assertIn("undo trail", text.lower())

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


# ---------------------------------------------------------------------------
# THE POSITIONAL TASK REF — the form a person actually types.
#
# `commands/heal.md` runs `task-station.py heal --scan --session <sid> $ARGUMENTS`, so
# `/heal 12` reaches the CLI as a bare `12`. The heal subparser had no positional, so
# argparse exited with `unrecognized arguments: 12` and the whole command died before the
# scan ran. The fix cannot live in the command file — `$ARGUMENTS` legitimately carries
# `--task <n>`, `--all`, or nothing at all, and no word inserted there parses all four —
# so the CLI takes an optional positional and folds it into --task, refusing the two
# combinations it cannot resolve rather than guessing which record was meant.
# ---------------------------------------------------------------------------

class TestThePositionalTaskRef(_Base):
    def test_a_bare_positional_ref_behaves_exactly_like_task(self):
        # The whole point: `heal --scan 12` IS `heal --scan --task 12`. Byte-identical,
        # because they run the same scan on the same task through the same resolver —
        # nothing in this report is wall-clock-derived on a never-healed task.
        t = self._task(decisions=["chose sqlite for the FTS index"])
        positional = self._out(ts.cmd_heal, _Args(ref=str(t["seq"]), scan=True))
        flagged = self._out(ts.cmd_heal, _Args(task=str(t["seq"]), scan=True))
        # The equality below is the real assertion, so it needs a companion that proves
        # both sides are a REPORT ABOUT THIS TASK — two identical empty strings, or two
        # reports about the wrong task, would satisfy equality on their own. It must be
        # something a --scan actually prints: the report carries check results and
        # counts, never decision BODIES, so the decision text is not available here.
        header = "[HEAL-SCAN] Task #%s" % t["seq"]
        self.assertIn(header, positional)
        self.assertIn(header, flagged)
        self.assertIn("Undispositioned acks", positional)
        self.assertEqual(positional, flagged)

    def test_the_task_flag_still_works_untouched(self):
        t = self._task(decisions=["alpha decision"])
        out = self._out(ts.cmd_heal, _Args(task=str(t["seq"]), scan=True))
        self.assertIn("[HEAL-SCAN]", out)
        self.assertIn("#%s" % t["seq"], out)

    def test_all_still_sweeps_the_board(self):
        a = self._task(title="first", decisions=["one call"])
        b = self._task(title="second", decisions=["another call"])
        out = self._out(ts.cmd_heal, _Args(all=True, scan=True))
        self.assertIn("SCOPE", out)
        self.assertIn("#%s" % a["seq"], out)
        self.assertIn("#%s" % b["seq"], out)

    def test_a_positional_ref_with_all_is_refused_not_silently_ignored(self):
        # Two different scopes. Dropping either one without a word is how somebody comes
        # to believe they healed ONE task when they swept the whole board.
        t = self._task(decisions=["a call"])
        out = self._out(ts.cmd_heal, _Args(ref=str(t["seq"]), all=True, scan=True))
        self.assertIn("cannot be combined", out)
        self.assertIn("Nothing was read", out)
        self.assertNotIn("[HEAL-SCAN]", out)

    def test_a_positional_ref_naming_a_different_task_than_task_is_refused(self):
        # No precedence rule, deliberately: a silent winner reconciles a record the
        # caller did not mean, and the refusal has to name BOTH so the typo is visible.
        a = self._task(title="first", decisions=["one call"])
        b = self._task(title="second", decisions=["another call"])
        out = self._out(ts.cmd_heal, _Args(task=str(a["seq"]), ref=str(b["seq"]),
                                           scan=True))
        self.assertIn("different tasks", out)
        self.assertIn(str(a["seq"]), out)
        self.assertIn(str(b["seq"]), out)
        self.assertNotIn("[HEAL-SCAN]", out)

    def test_the_same_ref_in_both_places_is_accepted(self):
        # `/todo heal 12` fills both slots from one word, so two spellings of ONE task
        # is not a conflict.
        t = self._task(decisions=["alpha decision"])
        out = self._out(ts.cmd_heal, _Args(task=str(t["seq"]), ref=str(t["seq"]),
                                           scan=True))
        self.assertIn("[HEAL-SCAN]", out)
        self.assertNotIn("different tasks", out)

    def test_the_same_id_prefix_in_either_case_is_still_one_task(self):
        t = self._task(decisions=["alpha decision"])
        out = self._out(ts.cmd_heal, _Args(task=t["id"][:8], ref=t["id"][:8].upper(),
                                           scan=True))
        self.assertIn("[HEAL-SCAN]", out)
        self.assertNotIn("different tasks", out)

    def test_an_id_prefix_positional_routes_through_the_same_resolver_as_task(self):
        # The ref is folded into --task and resolved ONCE, by `_heal_targets`. So every
        # shape --task accepts — a seq, an id prefix — behaves identically positionally,
        # and there is no second lookup to drift out of step with the first.
        t = self._task(title="resolve me", decisions=["alpha decision"])
        out = self._out(ts.cmd_heal, _Args(ref=t["id"][:8], scan=True))
        self.assertIn("resolve me", out)

    def test_no_ref_at_all_still_falls_back_to_the_attached_task(self):
        t = self._task(decisions=["alpha decision"])
        sid = "sess-positional-fallback"
        ts.set_link(sid, t["id"])
        out = self._out(ts.cmd_heal, _Args(session=sid, scan=True))
        self.assertIn("[HEAL-SCAN]", out)
        self.assertIn("#%s" % t["seq"], out)

    def test_an_unknown_positional_ref_reports_no_match(self):
        self._task(decisions=["alpha decision"])
        out = self._out(ts.cmd_heal, _Args(ref="99999", scan=True))
        self.assertIn("No task matching", out)

    def test_the_fold_leaves_task_naming_the_ref(self):
        # The unit underneath the CLI: it COPIES, it never resolves.
        a = _Args(ref="12")
        self.assertIsNone(ts._heal_positional_ref(a))
        self.assertEqual(a.task, "12")

    def test_the_command_files_argument_hint_documents_the_bare_ref(self):
        path = os.path.join(_REPO_ROOT, "commands", "heal.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        hint = [ln for ln in text.splitlines() if ln.startswith("argument-hint:")][0]
        self.assertIn("<n>", hint)
        self.assertIn("--all", hint)
        # …and it still must not read as a menu of raw operation flags.
        for flag in ("--apply", "--merge", "--split", "--mark-healed"):
            self.assertNotIn(flag, hint)


class HealPositionalRefOnTheRealCli(unittest.TestCase):
    """The bug was in ARGPARSE, so the regression test has to reach argparse: an
    in-process `_Args` can never reproduce `unrecognized arguments: 444`. This runs the
    real CLI the way `/heal 444` does."""

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
                              capture_output=True, text=True)

    def _seed(self):
        self._run("create", "--title", "positional", "--active")
        self._run("update", "--task", "1", "--decision", "chose sqlite for the index")

    def test_the_cli_accepts_the_task_as_a_positional(self):
        self._seed()
        r = self._run("heal", "--scan", "1")
        self.assertNotIn("unrecognized arguments", r.stderr)
        self.assertEqual(r.returncode, 0)
        self.assertIn("[HEAL-SCAN]", r.stdout)

    def test_the_positional_and_the_flag_produce_the_same_report(self):
        self._seed()
        self.assertEqual(self._run("heal", "--scan", "1").stdout,
                         self._run("heal", "--scan", "--task", "1").stdout)

    def test_the_cli_refuses_a_positional_alongside_all(self):
        self._seed()
        r = self._run("heal", "--scan", "1", "--all")
        self.assertIn("cannot be combined", r.stdout)
        self.assertNotIn("[HEAL-SCAN]", r.stdout)


# ---------------------------------------------------------------------------
# CHECK 10 — a LIVE STEP that restates a SUPERSEDED decision.
#
# A real task scanned CLEAN on every check and printed `Heal due? no` while five live
# steps named the two largest work items on it, both retired by decisions that same task
# had already superseded. Nothing was inconsistent — a decision that was right when
# written and was later refuted by reality leaves nothing to cross-reference — and the
# checklist, which is what a cold session reads FIRST, went on reading as the plan.
# ---------------------------------------------------------------------------

# A superseded decision and the live step that still orders the work it retired. Written
# so the arithmetic is checkable by hand: the decision's significant words are
# {build, nightly, export, pipeline, python, schedule, cron, box} and the step's are
# {write, nightly, export, pipeline, python, schedule, cron} — 6 shared of 9 distinct,
# a Jaccard of 0.67 against a 0.30 threshold.
RETIRED_DECISION = ("Build the nightly export pipeline in python and schedule it with "
                    "cron on the build box")
RESTATING_STEP = "Write the nightly export pipeline in python and schedule it with cron"


class TestStepsRestatingASupersededDecision(_Base):
    def _task_with_retired_work(self, step_text=RESTATING_STEP, extra_steps=()):
        """A task whose decision 1 was SUPERSEDED by decision 2, and whose checklist
        still carries `step_text`."""
        t = self._task(decisions=[RETIRED_DECISION,
                                  "the managed scheduler runs it instead; nothing to build"],
                       steps=[{"text": step_text, "done": False}]
                             + [{"text": s, "done": False} for s in extra_steps])
        dec.mark_superseded(t["decisions"], 1, 2)
        ts.save_task(t)
        return self._reload(t)

    def test_a_live_step_restating_a_superseded_decision_is_reported(self):
        hits = heal.steps_restating_superseded(self._task_with_retired_work())
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["ref"], "step 1")
        self.assertIn("restates decision 1", hits[0]["detail"])

    def test_the_finding_names_the_concrete_verb_that_retires_the_step(self):
        hits = heal.steps_restating_superseded(self._task_with_retired_work())
        self.assertIn("--step-add", hits[0]["detail"])
        self.assertIn("--step-supersede 1", hits[0]["detail"])

    def test_the_finding_says_it_is_provisional_rather_than_claiming_certainty(self):
        # Text overlap CANNOT separate a step that still orders retired work from the
        # step written to record the retirement. Saying so is what stops this becoming
        # the fifth confidently-wrong check this subsystem has shipped.
        hits = heal.steps_restating_superseded(self._task_with_retired_work())
        self.assertIn("PROVISIONAL", hits[0]["detail"])
        self.assertIn("READ THE TWO TOGETHER", hits[0]["detail"])

    def test_it_joins_findings_and_counts_as_an_issue(self):
        # Unlike the merge candidates and the goal review, this one IS a defect: the log
        # already ruled that work refuted, and the checklist still orders it.
        t = self._task_with_retired_work()
        result = heal.scan(t)
        self.assertIn("step-restates-superseded",
                      [f["check"] for f in result["findings"]])
        is_due, reasons = heal.due(t, result=result)
        self.assertTrue(is_due)
        self.assertTrue(any("issue(s)" in r for r in reasons))

    def test_a_step_restating_a_STILL_CURRENT_decision_is_not_reported(self):
        # Nothing has been refuted, so a step agreeing with a live decision is the
        # checklist working exactly as intended.
        t = self._task(decisions=[RETIRED_DECISION],
                       steps=[{"text": RESTATING_STEP, "done": False}])
        self.assertEqual(heal.steps_restating_superseded(self._reload(t)), [])

    def test_a_step_restating_a_MERGED_decision_is_not_reported(self):
        # SUPERSEDED is the only verb that means "this was WRONG". A merged decision is
        # still true, so a step restating it is not ordering refuted work.
        t = self._task(decisions=[RETIRED_DECISION, "one reconciled record of the trail"],
                       steps=[{"text": RESTATING_STEP, "done": False}])
        dec.mark_merged(t["decisions"], 1, 2)
        ts.save_task(t)
        self.assertEqual(heal.steps_restating_superseded(self._reload(t)), [])

    def test_an_already_superseded_step_is_not_re_reported(self):
        # Same rule as the stale-step check, same reason: re-flagging a step that was
        # just retired makes a freshly-healed task read as dirty.
        t = self._task_with_retired_work()
        steps.mark_superseded(t["steps"], 1)
        ts.save_task(t)
        self.assertEqual(heal.steps_restating_superseded(self._reload(t)), [])

    def test_the_step_that_RECORDS_the_retirement_is_never_reported(self):
        # The declare-vs-describe failure in a new costume — and the guard cannot answer
        # it, because both steps name the same work. So any step carrying correction
        # vocabulary at all is skipped, and the assertion below proves it was the
        # SILENCER and not the threshold: the overlap clears the bar.
        text = ("the nightly export pipeline in python was superseded by the managed "
                "scheduler")
        self.assertGreaterEqual(heal.word_overlap(text, RETIRED_DECISION),
                                heal.STEP_RESTATEMENT_OVERLAP)
        t = self._task_with_retired_work(step_text=text)
        self.assertEqual(heal.steps_restating_superseded(t), [])

    def test_an_unrelated_step_is_clean(self):
        t = self._task_with_retired_work(
            step_text="Ask the reviewer for a second opinion on the migration plan")
        self.assertEqual(heal.steps_restating_superseded(t), [])

    def test_texts_too_thin_to_compare_are_skipped_however_well_they_match(self):
        # Two five-word fragments that agree completely tell you nothing — the ratio is
        # noise below a handful of words, exactly as `_stem` refuses to fingerprint on
        # fewer than STEM_WORDS.
        thin = "adopt redis queue workers locally"
        self.assertEqual(heal.word_overlap(thin, thin), 1.0)
        t = self._task(decisions=[thin, "the managed queue is used instead"],
                       steps=[{"text": thin, "done": False}])
        dec.mark_superseded(t["decisions"], 1, 2)
        ts.save_task(t)
        self.assertEqual(heal.steps_restating_superseded(self._reload(t)), [])

    def test_one_finding_per_step_naming_the_strongest_match(self):
        # A step overlapping two superseded decisions is ONE thing to read, not two.
        t = self._task(decisions=[RESTATING_STEP,
                                  "Write the nightly export pipeline by hand every "
                                  "friday afternoon",
                                  "the managed scheduler runs it instead"],
                       steps=[{"text": RESTATING_STEP, "done": False}])
        dec.mark_superseded(t["decisions"], 1, 3)
        dec.mark_superseded(t["decisions"], 2, 3)
        ts.save_task(t)
        hits = heal.steps_restating_superseded(self._reload(t))
        self.assertEqual(len(hits), 1)
        self.assertIn("restates decision 1", hits[0]["detail"])

    def test_the_check_never_mutates_the_task(self):
        t = self._task_with_retired_work()
        before = json.dumps(store.strip_rev(t), sort_keys=True, default=str)
        heal.scan(t)
        after = json.dumps(store.strip_rev(self._reload(t)), sort_keys=True, default=str)
        self.assertEqual(before, after)

    def test_the_scan_report_names_the_check_and_its_provisional_finding(self):
        t = self._task_with_retired_work()
        out = self._heal(t, scan=True)
        self.assertIn("Steps restating a superseded decision", out)
        self.assertIn("PROVISIONAL", out)
        self.assertIn("YES", out.rsplit("Heal due?", 1)[-1])

    def test_word_overlap_is_jaccard_over_significant_words(self):
        self.assertEqual(heal.word_overlap("alpha beta gamma", "alpha beta gamma"), 1.0)
        self.assertEqual(heal.word_overlap("alpha beta", "gamma delta"), 0.0)
        self.assertEqual(heal.word_overlap("", "alpha beta"), 0.0)
        # symmetric, and stopwords carry no weight either way
        self.assertEqual(heal.word_overlap("alpha and beta", "beta with alpha"), 1.0)
        self.assertEqual(heal.word_overlap("alpha beta gamma", "alpha beta"),
                         heal.word_overlap("alpha beta", "alpha beta gamma"))


# ---------------------------------------------------------------------------
# THE GOAL REVIEW — a proposal, never a finding.
#
# The goal line is the one field that says what DONE looks like, and nothing else on the
# task claims to say it — so there is no second thing to cross-reference it against and
# no check can ever raise a goal describing a mission already accomplished. On one real
# task that is exactly what sat there, for days, while every check reported clean. The
# honest measure is how many decisions have landed since the goal was last written: a
# reason to LOOK, and never proof of anything.
# ---------------------------------------------------------------------------

class TestGoalReview(_Base):
    def test_update_goal_records_the_baseline(self):
        t = self._task(decisions=["one call", "another call"])
        self._update(t, goal="the exporter runs nightly without a human")
        snap = self._reload(t)[heal.GOAL_TOUCHED_FIELD]
        self.assertEqual(snap["decisions"], 2)
        self.assertTrue(snap["ts"])

    def test_the_proposal_counts_the_decisions_that_landed_since(self):
        t = self._task(decisions=["one call"])
        self._update(t, goal="the exporter runs nightly without a human")
        t = self._reload(t)
        for text in ("a second call", "a third call"):
            ts.append_decision(t, text)
        ts.save_task(t)
        g = heal.goal_review(self._reload(t))
        self.assertTrue(g["known"])
        self.assertEqual(g["since"], 2)
        self.assertIn("exporter runs nightly", g["preview"])

    def test_no_baseline_reads_as_cannot_be_counted_never_as_zero(self):
        # EVERY task that predates the snapshot takes this path, so it is the common
        # case. A zero would read as "nothing has happened since"; the truth is "nobody
        # recorded when the goal was written".
        t = self._task(decisions=["one call", "another call"],
                       goal="the exporter runs nightly without a human")
        g = heal.goal_review(self._reload(t))
        self.assertFalse(g["known"])
        self.assertIsNone(g["since"])
        line = "\n".join(heal.goal_review_lines({"goal_review": g}))
        self.assertIn("CANNOT BE COUNTED", line)
        self.assertNotIn("0 decision", line)

    def test_a_garbled_baseline_reads_as_unknown_rather_than_crashing(self):
        t = self._task(decisions=["one call"], goal="ship the exporter",
                       goal_touched={"decisions": "not a number"})
        g = heal.goal_review(self._reload(t))
        self.assertFalse(g["known"])
        self.assertIsNone(g["since"])

    def test_a_blank_goal_reports_nothing_at_all(self):
        # "0 decisions since the goal was last written" about a goal nobody wrote is a
        # number about nothing.
        t = self._task(decisions=["one call"])
        self.assertEqual(heal.goal_review(self._reload(t)), {})
        self.assertEqual(heal.goal_review_lines({"goal_review": {}}), [])

    def test_rewriting_the_IDENTICAL_goal_does_not_re_baseline_it(self):
        # The rule `--state` already follows: re-writing the same line does not make a
        # goal reality has overtaken any fresher, and re-baselining on a no-op write
        # would hide the very drift this measures.
        t = self._task(decisions=["one call"])
        self._update(t, goal="the exporter runs nightly without a human")
        t = self._reload(t)
        ts.append_decision(t, "a second call")
        ts.save_task(t)
        self._update(t, goal="the exporter runs nightly without a human")
        self.assertEqual(heal.goal_review(self._reload(t))["since"], 1)

    def test_a_goal_that_actually_changes_re_baselines(self):
        t = self._task(decisions=["one call"])
        self._update(t, goal="the exporter runs nightly without a human")
        t = self._reload(t)
        ts.append_decision(t, "a second call")
        ts.save_task(t)
        self._update(t, goal="the exporter is retired and the managed job owns it")
        self.assertEqual(heal.goal_review(self._reload(t))["since"], 0)

    def test_a_goal_set_at_creation_is_baselined_too(self):
        # Uncountable is the honest answer when nobody recorded the baseline. It is the
        # WRONG answer when we are standing at the baseline: a task created with a goal
        # knows exactly when it was written and that no decision predates it.
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_create(_Args(session=None, title="Seeded goal", summary="s",
                                color=None, effort=None,
                                goal="the exporter runs nightly without a human",
                                step=None, force=True, no_attach=True, attach=False,
                                active=False))
        t = [x for x in ts.all_tasks() if x["title"] == "Seeded goal"][0]
        g = heal.goal_review(t)
        self.assertTrue(g["known"])
        self.assertEqual(g["since"], 0)

    def test_the_goal_review_is_never_a_finding_and_never_makes_a_heal_due(self):
        t = self._task(decisions=["one call"])
        self._update(t, goal="the exporter runs nightly without a human")
        t = self._reload(t)
        for i in range(6):
            ts.append_decision(t, "call number %d, recorded" % i)
        ts.save_task(t)
        t = self._reload(t)
        result = heal.scan(t)
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["goal_review"]["since"], 6)
        self.assertFalse(heal.due(t, result=result)[0])

    def test_the_scan_report_names_it_as_a_proposal_and_says_what_to_ask(self):
        t = self._task(decisions=["one call"])
        self._update(t, goal="the exporter runs nightly without a human")
        out = self._heal(self._reload(t), scan=True)
        self.assertIn("Goal review", out)
        self.assertIn("PROPOSAL", out)
        self.assertIn("--goal", out)
        self.assertNotIn("YES", out.rsplit("Heal due?", 1)[-1])

    def test_a_task_with_no_goal_prints_no_goal_section(self):
        t = self._task(decisions=["one call"])
        self.assertNotIn("Goal review", self._heal(t, scan=True))


# ---------------------------------------------------------------------------
# THE CLOSING VERDICT — mechanical and judgment are two different questions.
#
# `Heal due? no` reads as "this task is a complete record". It has only ever meant "the
# cross-referencing checks found nothing", and this module's own history is the proof:
# one task scanned clean while a shipped release sat recorded nowhere on it, another
# while its goal described a mission already accomplished.
# ---------------------------------------------------------------------------

class TestTheClosingVerdict(_Base):
    def test_a_clean_scan_says_mechanical_clean_and_judgment_not_run(self):
        t = self._task(decisions=["chose sqlite for the FTS index"])
        out = self._heal(t, scan=True)
        self.assertIn("Mechanical", out)
        self.assertIn("clean", out)
        self.assertIn("Judgment", out)
        self.assertIn("NOT RUN", out)
        self.assertNotIn("YES", out.rsplit("Heal due?", 1)[-1])

    def test_the_judgment_line_names_what_the_scan_structurally_cannot_see(self):
        t = self._task(decisions=["chose sqlite for the FTS index"])
        line = heal.judgment_line(self._reload(t))
        self.assertIn("NOT RUN", line)
        for word in ("GOAL", "LIVE STEPS", "PINNED"):
            self.assertIn(word, line)

    def test_the_mechanical_line_counts_the_findings(self):
        t = self._task(decisions=UNLINKED_PROSE)
        result = heal.scan(self._reload(t))
        self.assertEqual(heal.mechanical_line(result),
                         "%d issue(s)" % len(result["findings"]))
        self.assertIn("issue(s)", self._heal(t, scan=True))

    def test_a_recorded_judgement_pass_is_cited_instead_of_claiming_not_run(self):
        # `--mark-healed --note` is the only evidence a task can hold that somebody read
        # the record and ruled on it, so it is the only thing this line will cite.
        t = self._task(decisions=["chose sqlite for the FTS index"])
        self._heal(t, mark_healed=True, note="re-read the goal, the steps and both pins")
        out = self._heal(self._reload(t), scan=True)
        self.assertIn("last recorded", out)
        self.assertIn("re-read the goal, the steps and both pins", out)
        self.assertNotIn("NOT RUN", out)

    def test_a_recorded_pass_is_still_caveated_by_what_landed_after_it(self):
        # That pass ruled on the record as it stood THEN, and the newest evidence is
        # exactly what retires a goal, a step or a pinned line.
        t = self._task(decisions=["chose sqlite for the FTS index"])
        self._heal(t, mark_healed=True, note="read the whole log")
        t = self._reload(t)
        for text in ("a later call", "a later still call"):
            ts.append_decision(t, text)
        ts.save_task(t)
        line = heal.judgment_line(self._reload(t))
        self.assertIn("last recorded", line)
        self.assertIn("2 decision(s) have landed since", line)

    def test_a_stamp_with_no_note_is_not_evidence_that_the_judgement_happened(self):
        # An --apply stamps for performing MECHANICAL operations. That is a different
        # claim, and reading it as a judgement pass is what this whole split prevents.
        t = self._task(decisions=["one", "two", "the surviving summary"])
        self._heal(t, apply=True, merge="1,2", into="3")
        self.assertIn("NOT RUN", heal.judgment_line(self._reload(t)))

    def test_the_dry_run_closes_on_the_same_three_rows(self):
        t = self._task(decisions=["chose sqlite for the FTS index"])
        out = self._heal(t)
        self.assertIn("Mechanical", out)
        self.assertIn("Judgment", out)
        self.assertIn("Heal due?", out)

    def test_the_zero_operation_refusal_closes_on_them_too(self):
        t = self._task(decisions=["chose sqlite for the FTS index"])
        out = self._heal(t, apply=True)
        self.assertIn("REFUSED", out)
        self.assertIn("Mechanical", out)
        self.assertIn("Judgment", out)

    def test_due_keeps_its_signature_and_return_shape(self):
        # The nag, the gate file and `gate_line` all read this. The split above is a
        # RENDERING change and must stay one.
        t = self._task(decisions=["chose sqlite for the FTS index"])
        is_due, reasons = heal.due(self._reload(t))
        self.assertIsInstance(is_due, bool)
        self.assertIsInstance(reasons, list)

    def test_the_summary_rows_are_one_implementation_shared_by_every_surface(self):
        t = self._task(decisions=["chose sqlite for the FTS index"])
        rows = heal.summary_lines(self._reload(t), heal.scan(self._reload(t)))
        self.assertEqual(len(rows), 3)
        scan_out = self._heal(t, scan=True)
        for row in rows:
            self.assertIn(row, scan_out)


# ---------------------------------------------------------------------------
# THE JUDGMENT BRIEF carries the goal and the checklist.
#
# All three reconcile verbs are DECISION verbs, so the block briefed the pass on the
# decision set alone — while the goal and the checklist are what a cold session reads
# FIRST. They are printed beside the NEWEST decisions because those are the evidence
# that retires them.
# ---------------------------------------------------------------------------

class TestTheBlockBriefsTheGoalAndTheChecklist(_Base):
    def _built(self):
        t = self._task(decisions=["chose sqlite for the FTS index",
                                  "the managed scheduler owns the nightly run"],
                       goal="the exporter runs nightly without a human",
                       steps=[{"text": "write the exporter tests", "done": False},
                              {"text": "a retired checklist item", "done": False}])
        steps.mark_superseded(t["steps"], 2)
        ts.save_task(t)
        return self._reload(t)

    def test_the_dry_run_prints_the_goal_line_with_its_question(self):
        out = self._heal(self._built())
        self.assertIn("THE GOAL LINE", out)
        self.assertIn("the exporter runs nightly without a human", out)
        self.assertIn("does the newest evidence above retire this?", out)

    def test_the_dry_run_prints_the_live_checklist_and_omits_retired_steps(self):
        out = self._heal(self._built())
        self.assertIn("THE LIVE CHECKLIST", out)
        self.assertIn("write the exporter tests", out)
        self.assertNotIn("a retired checklist item", out)

    def test_a_task_with_no_goal_says_so_rather_than_printing_a_blank(self):
        t = self._task(decisions=["chose sqlite for the FTS index"])
        out = self._heal(t)
        self.assertIn("THE GOAL LINE", out)
        self.assertIn("(none set)", out)

    def test_the_judgment_list_tells_the_pass_to_re_read_both(self):
        out = self._heal(self._built())
        self.assertIn("RE-READ THE GOAL LINE AND EVERY LIVE STEP", out)
        self.assertIn("--goal '<what done looks like now>'", out)
        self.assertIn("--step-supersede", out)

    def test_the_skill_documents_the_goal_and_the_checklist_not_only_decisions(self):
        path = os.path.join(_REPO_ROOT, "skills", "heal", "SKILL.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("goal", text.lower())
        self.assertIn("does the newest evidence retire this", text.lower())
        self.assertIn("GOAL REVIEW", text)
        self.assertIn("Steps restating a superseded decision", text)
        self.assertIn("Judgment", text)


# ---------------------------------------------------------------------------
# THE UNDO TRAIL — what replaces the approval gate.
#
# `/heal` used to stop after the plan and ask before applying anything, and that question
# was where a wrong call got caught. It is gone: the pass runs scan → judge → apply →
# verify in one command, because stopping between steps is the cost the user feels.
#
# REMOVING A GATE IS ONLY DEFENSIBLE IF REVERSING A WRONG CALL IS AS CHEAP AS APPROVING
# ONE WAS. So every write now prints the exact command that takes it back, generated from
# the indices it really touched. "Every heal is reversible" was already printed and is not
# enough: acting on it means first working out which decision numbers moved, which is
# precisely the work the gate used to make unnecessary.
# ---------------------------------------------------------------------------

class TestTheUndoTrailOnApply(_Base):
    RELEASES = ["2.7.0 SHIPPED: the store moved to sqlite",
                "2.8.0 SHIPPED: the board view-model",
                "2.9.0 SHIPPED: decision supersession"]

    def test_a_performed_merge_records_the_exact_reversal(self):
        t = self._task(decisions=list(self.RELEASES))
        ops = heal.plan(self._reload(t))
        heal.apply(self._reload(t), ops)
        merges = [o for o in ops if o.get("verb") == "merge"]
        self.assertTrue(merges, "the release signature should have produced a merge op")
        undo = merges[0]["undo"]
        # REPEATED flags, not a comma list: `--restore-decision` is action="append",
        # so a comma-joined command is one argparse rejects.
        self.assertIn("--restore-decision 1 --restore-decision 2 --restore-decision 3",
                      undo)
        self.assertIn("--task %s" % t["seq"], undo)

    def test_a_performed_split_records_the_exact_reversal(self):
        body = "\n\n".join("paragraph %d %s" % (i, "x" * 1200) for i in range(4))
        t = self._task(decisions=[body])
        ops = heal.plan(self._reload(t))
        heal.apply(self._reload(t), ops)
        splits = [o for o in ops if o.get("verb") == "split" and not o.get("manual")]
        self.assertTrue(splits)
        self.assertIn("--restore-decision 1", splits[0]["undo"])

    def test_a_retro_disposition_says_no_verb_reverses_it(self):
        # The one write with no inverse — a heal never overwrites a disposition, so
        # nothing can clear one. Naming the gap beats inventing a command that does
        # nothing, which the reader would only discover at the moment they need it.
        t = self._task(decisions=["a call"])
        self._memo_with_ack(t)
        t = self._reload(t)
        ops = heal.disposition_ops(t)
        heal.apply(t, ops)
        self.assertIn("NO VERB REVERSES THIS ONE", ops[0]["undo"])

    def test_an_operation_that_was_not_performed_promises_no_undo(self):
        # A skipped op must never offer a reversal: a command that would undo nothing is
        # worse than silence, because it reads as a guarantee.
        t = self._task(decisions=["a call"])
        ops = [{"verb": "split", "index": 1, "parts": [], "manual": True, "why": "no structure"}]
        lines, applied, skipped = heal.apply(self._reload(t), ops)
        self.assertEqual((applied, skipped), (0, 1))
        self.assertIsNone(ops[0].get("undo"))
        self.assertEqual(heal.undo_lines(ops), [])

    def test_undo_lines_are_silent_when_nothing_was_performed(self):
        self.assertEqual(heal.undo_lines([]), [])
        self.assertEqual(heal.undo_lines(None), [])

    def test_restore_flags_repeat_rather_than_comma_joining(self):
        self.assertEqual(heal._restore_flags([3, 7]),
                         "--restore-decision 3 --restore-decision 7")
        self.assertEqual(heal._restore_flags([2], flag="--step-restore"),
                         "--step-restore 2")

    def test_the_apply_report_carries_the_undo_section_and_the_backup_fallback(self):
        t = self._task(decisions=list(self.RELEASES))
        out = self._heal(t, apply=True)
        self.assertIn("UNDO", out)
        self.assertIn("--restore-decision 1", out)
        self.assertIn("WHOLE-TASK FALLBACK", out)
        self.assertIn(heal.backup_path(t["id"]), out)

    def test_the_apply_report_shows_health_before_and_after(self):
        t = self._task(decisions=list(self.RELEASES))
        out = self._heal(t, apply=True)
        self.assertIn("HEALTH BEFORE:", out)
        self.assertIn("HEALTH NOW:", out)

    def test_verbose_carries_the_undo_section_too(self):
        t = self._task(decisions=list(self.RELEASES))
        out = self._heal(t, apply=True, verbose=True)
        self.assertIn("UNDO", out)
        self.assertIn("--restore-decision 1", out)

    def test_the_named_reversal_actually_restores_the_decision(self):
        # The strongest form of this test: run the command the report printed and check
        # the decision came back. An undo line that does not work is a worse lie than no
        # undo line at all.
        t = self._task(decisions=list(self.RELEASES))
        out = self._heal(t, apply=True)
        self.assertIn("--restore-decision 1", out)
        self.assertTrue(dec.is_replaced(self._reload(t)["decisions"][0]))
        self._update(t, restore_decision=[1])
        self.assertFalse(dec.is_replaced(self._reload(t)["decisions"][0]))


class TestTheUndoTrailOnTheJudgementVerbs(_Base):
    """`update --supersedes` and `update --step-supersede` write IMMEDIATELY: they never
    pass through `--apply`, so they take no backup, and with the approval gate gone there
    is nothing at all standing in front of them. They are the writes most in need of a
    named undo, and they had none."""

    def test_a_supersede_prints_the_command_that_restores_the_decision(self):
        t = self._task(decisions=["go with flat files"])
        out = self._update(t, decision=["sqlite it is — the FTS index needs a query engine"],
                           supersedes=[1])
        self.assertIn("UNDO", out)
        self.assertIn("--restore-decision 1", out)
        self.assertIn("--task %s" % t["seq"], out)

    def test_the_printed_supersede_reversal_actually_works(self):
        t = self._task(decisions=["go with flat files"])
        self._update(t, decision=["sqlite it is"], supersedes=[1])
        self.assertTrue(dec.is_replaced(self._reload(t)["decisions"][0]))
        self._update(t, restore_decision=[1])
        self.assertFalse(dec.is_replaced(self._reload(t)["decisions"][0]))

    def test_a_step_supersede_prints_the_command_that_restores_the_step(self):
        t = self._task(steps=[{"text": "the stale step", "done": False}])
        out = self._update(t, step_add=["the corrected step"], step_supersede=[1])
        self.assertIn("UNDO", out)
        self.assertIn("--step-restore 1", out)

    def test_the_printed_step_reversal_actually_works(self):
        t = self._task(steps=[{"text": "the stale step", "done": False}])
        self._update(t, step_add=["the corrected step"], step_supersede=[1])
        self.assertTrue(steps.is_superseded(self._reload(t)["steps"][0]))
        self._update(t, step_restore=[1])
        self.assertFalse(steps.is_superseded(self._reload(t)["steps"][0]))

    def test_an_update_with_no_reconcile_verb_prints_no_undo_section(self):
        # Proportional noise: a plain field write has nothing to reverse, and an UNDO
        # heading on every update is one nobody reads on the update that needed it.
        t = self._task(decisions=["a call"])
        self.assertNotIn("UNDO", self._update(t, state="NEXT: keep going"))

    def test_the_summary_replacement_still_names_its_own_reversal(self):
        # Already existed — the undo trail FILLS the gaps rather than duplicating what
        # the write path already prints.
        t = self._task()
        self._update(t, summary="the first summary")
        out = self._update(t, summary="the second summary")
        self.assertIn("--restore-summary", out)


class TestRemovingTheGateDidNotWidenWhatGetsApplied(_Base):
    """The failure this class exists to prevent. "Do everything" means "do not stop to
    ask", NOT "apply more". A merge candidate is a group of decisions that merely OPEN
    the same way; performing one from its shape alone writes a false consolidation into
    the record, where it then reads as reconciled fact."""

    # Three decisions sharing a leading SHAPE (three significant words) but not a STEM
    # (four) and not the named release signature — so `merge_candidates` proposes them
    # and `merge_clusters` must not touch them.
    SHAPED = ["my process error alpha, recorded here",
              "my process error beta, recorded here",
              "my process error gamma, recorded here"]

    def test_the_group_is_proposed(self):
        t = self._task(decisions=list(self.SHAPED))
        cands = heal.merge_candidates(self._reload(t))
        self.assertEqual([c["indices"] for c in cands], [[1, 2, 3]])

    def test_but_it_is_never_planned_as_an_operation(self):
        t = self._task(decisions=list(self.SHAPED))
        self.assertEqual(heal.merge_clusters(self._reload(t)), [])
        self.assertEqual([o["verb"] for o in heal.plan(self._reload(t))], [])

    def test_and_apply_refuses_rather_than_merging_them(self):
        t = self._task(decisions=list(self.SHAPED))
        out = self._heal(t, apply=True)
        self.assertIn("REFUSED", out)
        self.assertEqual(len(dec.live(self._reload(t)["decisions"])), 3)

    def test_the_scan_still_calls_them_proposals_not_findings(self):
        t = self._task(decisions=list(self.SHAPED))
        result = heal.scan(self._reload(t))
        self.assertEqual(result["findings"], [])
        self.assertTrue(result["merge_candidates"])


class TestBothSurfacesDroppedTheApprovalGate(_Base):
    def _read(self, *parts):
        with open(os.path.join(_REPO_ROOT, *parts), encoding="utf-8") as f:
            return f.read()

    def _description(self, text):
        """The YAML frontmatter `description:` line — the one sentence a reader sees
        before they open either file, and the place the old promise survived longest."""
        return [ln for ln in text.splitlines() if ln.startswith("description:")][0]

    def test_the_skill_description_no_longer_promises_a_confirmation(self):
        d = self._description(self._read("skills", "heal", "SKILL.md"))
        self.assertNotIn("asks once", d)
        self.assertNotIn("before changing anything", d)
        self.assertIn("undo", d.lower())

    def test_the_command_description_no_longer_promises_a_confirmation(self):
        d = self._description(self._read("commands", "heal.md"))
        self.assertNotIn("asks once", d)
        self.assertNotIn("before changing anything", d)
        self.assertIn("undo", d.lower())

    def test_the_skill_body_has_no_confirmation_step_left(self):
        text = self._read("skills", "heal", "SKILL.md")
        self.assertNotIn("Ask for confirmation", text)
        self.assertNotIn("On refusal, change nothing", text)
        self.assertIn("no approval gate", text.lower())

    def test_the_command_body_has_no_confirmation_step_left(self):
        text = self._read("commands", "heal.md")
        self.assertNotIn("Ask once", text)
        self.assertNotIn("On yes", text)

    def test_both_surfaces_require_the_undo_trail_to_be_surfaced(self):
        for text in (self._read("skills", "heal", "SKILL.md"),
                     self._read("commands", "heal.md")):
            self.assertIn("--restore-decision", text)
            self.assertIn("--step-restore", text)
            self.assertIn("--restore-summary", text)

    def test_the_skill_still_forbids_merging_a_candidate_from_its_shape(self):
        text = self._read("skills", "heal", "SKILL.md")
        self.assertIn("MERGE CANDIDATES stay proposals", text)
        self.assertIn("false consolidation", text)

    def test_the_skill_still_stops_early_on_a_clean_scan(self):
        text = self._read("skills", "heal", "SKILL.md")
        self.assertIn("--mark-healed --task <n> --note", text)
        self.assertIn("Do not read the dry run at all", text)


# ---------------------------------------------------------------------------
# THE THIRD DISCRIMINATOR — reports-another-decision.
#
# `qualifier` reads the word standing in FRONT of a match, which answers "what noun is this
# keyword about". It cannot answer the second question the same sentence raises: WHO does the
# sentence say did it? On one real task 8 of 17 findings were decisions MINUTING another
# decision's work — "corrected by decision 184", "decision 173 investigated", "why decision
# 150 is NOT superseded" — every one of which satisfies check 3's two older conditions
# perfectly. Eight false findings beside nine real ones is how a reader learns to skip all
# seventeen.
#
# The shapes below are the real ones, verbatim from the spec that ordered this fix.
# ---------------------------------------------------------------------------

class TestReportsAnotherDecision(_Base):
    def _prose(self, text):
        """A task whose SECOND decision carries `text` — so a reference to decision 1 is
        backwards-looking and in range, which is what check 3 requires before it looks at
        anything else."""
        t = self._task(decisions=["go with flat files", text])
        return self._reload(t)

    def test_corrected_by_decision_N_is_reporting_not_declaring(self):
        # `by` makes decision 1 the AGENT of the correction: this entry is the minute of a
        # ruling already taken, not a claim that something is unlinked.
        t = self._prose("the export path was corrected by decision 1, which is why the "
                        "flat-file reader is gone")
        self.assertEqual(heal.prose_supersession(t), [])

    def test_decision_N_investigated_is_reporting_not_declaring(self):
        # Carries the correction vocabulary AND names an earlier live decision, so both of
        # check 3's older conditions hold — the reference is the SUBJECT of a reporting verb,
        # and that is the only thing keeping this quiet.
        t = self._prose("decision 1 investigated the store choice and corrected the numbers")
        self.assertEqual(heal.prose_supersession(t), [])

    def test_why_decision_N_is_NOT_superseded_is_reporting_not_declaring(self):
        # The sentence DENIES the condition. Reading it as a claim of supersession is
        # exactly backwards.
        t = self._prose("why decision 1 is NOT superseded: the flat-file reader still ships "
                        "and nothing has refuted it")
        self.assertEqual(heal.prose_supersession(t), [])

    def test_superseded_by_decision_N_is_reporting_not_declaring(self):
        t = self._prose("the reader was superseded by decision 1 during the scrub")
        self.assertEqual(heal.prose_supersession(t), [])

    def test_the_true_positive_still_fires(self):
        # The whole point of the discriminator is that it kills the eight and keeps the one.
        hits = heal.prose_supersession(self._prose(
            "decision 1 was wrong — sqlite instead, for the FTS index"))
        self.assertEqual([h["ref"] for h in hits], ["decision 2"])

    def test_a_form_of_to_be_after_the_reference_is_still_a_declaration(self):
        # `was`/`is` are deliberately NOT reporting verbs: they are the predicate that makes
        # the sentence a statement ABOUT the named decision, which is the finding worth
        # having.
        for body in ("decision 1 was wrong about the store",
                     "decision 1 is no longer true — the FTS index needs sqlite"):
            self.assertEqual([h["ref"] for h in heal.prose_supersession(self._prose(body))],
                             ["decision 2"], body)

    def test_a_relative_clause_still_reads_as_reporting(self):
        # `decision 1, WHICH investigated …` says 1 did it just as plainly as `decision 1
        # investigated …`. The reading looks one word further, and no further than that.
        t = self._prose("decision 1, which investigated the store choice, corrected nothing "
                        "— the flat-file reader still ships")
        self.assertEqual(heal.prose_supersession(t), [])

    def test_the_supersession_vocabulary_is_never_a_reporting_verb(self):
        # `decision 4 superseded by this one` is the check's BEST true positive — prose
        # claiming a supersession the structure does not record. One word cannot tell it from
        # the active `decision 4 superseded the flat-file rule`, so those verbs stay out of
        # the reporting set and the passive form is caught by the `by` in FRONT instead.
        hits = heal.prose_supersession(self._prose(
            "decision 1 superseded — this entry replaces it and nothing links them"))
        self.assertEqual([h["ref"] for h in hits], ["decision 2"])

    def test_reported_decision_refs_reads_each_shape(self):
        for body in ("corrected by decision 184",
                     "decision 173 investigated",
                     "why decision 150 is NOT superseded",
                     "the reader was superseded by decision 12"):
            self.assertTrue(heal.reported_decision_refs(body), body)
        for body in ("decision 4 was wrong",
                     "decision 4 is superseded by this one",
                     "entry 2 no longer holds"):
            self.assertEqual(heal.reported_decision_refs(body), set(), body)

    def test_the_discriminator_is_scoped_to_the_clause_not_the_whole_entry(self):
        # A negated keyword five sentences away says nothing about this reference. The window
        # is bounded so a page-long decision cannot silence itself by accident.
        body = ("decision 1 was wrong — sqlite instead. Separately, the config file is not "
                "superseded and stays exactly where it is. " + "filler. " * 30)
        self.assertEqual([h["ref"] for h in heal.prose_supersession(self._prose(body))],
                         ["decision 2"])


# ---------------------------------------------------------------------------
# THE OVERSIZED TIERS — one advisory, two multiples of it.
#
# The write path nudges at 600 chars (`decisions.LONG_DECISION_CHARS`) and this check used to
# report clean up to 4,000 — 2.4× an advisory it never referenced — on a task whose decisions
# AVERAGE ~1,400. So it was neither the advisory nor a measurement. Both thresholds now
# derive from that one constant: >2× is worth READING (a proposal, capped, never an issue),
# >6× is a FINDING, which is where an entry stops being supersedable a piece at a time.
# ---------------------------------------------------------------------------

class TestOversizedTiers(_Base):
    def test_one_advisory_is_the_single_source_of_truth(self):
        self.assertEqual(heal.WRITE_ADVISORY_CHARS, dec.LONG_DECISION_CHARS)
        self.assertEqual(heal.OVERSIZE_PROPOSAL_CHARS, 2 * heal.WRITE_ADVISORY_CHARS)
        self.assertEqual(heal.OVERSIZE_CHARS, 6 * heal.WRITE_ADVISORY_CHARS)

    def test_over_twice_the_advisory_is_a_proposal_and_not_a_finding(self):
        t = self._task(decisions=["p" * (heal.OVERSIZE_PROPOSAL_CHARS + 100)])
        result = heal.scan(self._reload(t))
        self.assertEqual(result["findings"], [])
        self.assertEqual([r["index"] for r in result["oversized_proposals"]["shown"]], [1])
        self.assertFalse(heal.due(self._reload(t), result=result)[0])

    def test_at_the_proposal_threshold_exactly_nothing_is_said(self):
        t = self._task(decisions=["p" * heal.OVERSIZE_PROPOSAL_CHARS])
        result = heal.scan(self._reload(t))
        self.assertEqual(result["oversized_proposals"]["shown"], [])
        self.assertEqual(result["findings"], [])

    def test_over_six_times_the_advisory_is_a_finding_naming_the_split_verb(self):
        t = self._task(decisions=["f" * (heal.OVERSIZE_CHARS + 10)])
        hits = heal.oversized(self._reload(t))
        self.assertEqual([h["ref"] for h in hits], ["decision 1"])
        self.assertIn("heal --split 1", hits[0]["detail"])
        self.assertIn("600-char write advisory", hits[0]["detail"])

    def test_a_finding_is_not_also_listed_as_a_proposal(self):
        # The same entry in an issue list AND a proposal list reads as two problems, and a
        # reader who fixes one is then told the other is outstanding.
        t = self._task(decisions=["f" * (heal.OVERSIZE_CHARS + 10)])
        result = heal.scan(self._reload(t))
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(result["oversized_proposals"]["total"], 0)

    def test_the_proposal_list_is_worst_first_and_says_how_many_it_dropped(self):
        sizes = [heal.OVERSIZE_PROPOSAL_CHARS + n for n in (10, 20, 30, 40, 50, 60, 70)]
        t = self._task(decisions=["x" * n for n in sizes])
        p = heal.oversized_proposals(self._reload(t))
        self.assertEqual([r["index"] for r in p["shown"]], [7, 6, 5, 4, 3])
        self.assertEqual(p["more"], 2)
        self.assertEqual(p["total"], 7)
        line = "\n".join(heal.oversized_proposal_lines({"oversized_proposals": p}))
        self.assertIn("PROPOSALS", line)
        self.assertIn("+2 more", line)

    def test_the_plan_splits_the_finding_tier_only(self):
        # A proposal must never become an op: `--apply` splitting every 1,300-char decision
        # on the board is the cry-wolf failure with a write attached.
        body = "\n\n".join("para %d %s" % (i, "w" * 300)
                           for i in range(4))          # ~1,240 chars: proposal tier
        t = self._task(decisions=[body])
        self.assertEqual([o["verb"] for o in heal.plan(self._reload(t))], [])


# ---------------------------------------------------------------------------
# THE FIRST OUTWARD CHECK — cited commits.
#
# Every other check cross-references the record with ITSELF, so a rebase or a force-push
# that erased a cited commit leaves nothing to find. This one asks a git repo — and it only
# ever asks about DECLARED citations, because a 7-40 char hex token is also a task id, a memo
# id8, a heal fingerprint and a tree hash, and "your commit vanished" about a task id is a
# finding nobody can act on.
# ---------------------------------------------------------------------------

class TestCitedCommits(_Base):
    def _repo_task(self, decisions, **fields):
        """A task recording a file whose DIRECTORY is the test's own temp dir — which really
        exists, so `task_repos` finds one directory to probe. Whether it is a repo, and
        whether the sha resolves, is decided by the injected `run`: never real git."""
        return self._task(decisions=decisions,
                          files=[os.path.join(self.tmp, "thing.py")], **fields)

    def test_a_declared_citation_is_parsed_with_where_it_came_from(self):
        t = self._task(decisions=["merged commit 4412760 into main after the scrub"])
        self.assertEqual(heal.commit_citations(self._reload(t)),
                         [("4412760", "decision 1")])

    def test_the_at_form_is_parsed(self):
        t = self._task(decisions=["the release line stands at main @ 022ace9f"])
        self.assertEqual([s for s, _w in heal.commit_citations(self._reload(t))],
                         ["022ace9f"])

    def test_a_log_entry_is_read_too(self):
        t = self._task(decisions=["a call"])
        t = self._reload(t)
        ts.append_history(t, "pushed 022ace9 to origin/main")
        ts.save_task(t)
        self.assertEqual(heal.commit_citations(self._reload(t)),
                         [("022ace9", "log entry 1")])

    def test_a_bare_hex_token_is_NEVER_a_citation(self):
        # Task ids and fingerprints are hex too. A false "history was rewritten" sends
        # somebody hunting through reflogs for a commit that is sitting right there.
        t = self._task(decisions=["the task id 3140ac00 is hex and so is a fingerprint "
                                  "ab12cd34ef56, and neither is a commit"])
        self.assertEqual(heal.commit_citations(self._reload(t)), [])

    def test_a_hex_looking_english_word_is_not_a_sha(self):
        # `defaced`, `acceded` and `beefed` are spelled entirely in hex letters. The digit
        # gate is what stops "the commit defaced the config" from being a citation.
        t = self._task(decisions=["the commit defaced the config and acceded to the rename"])
        self.assertEqual(heal.commit_citations(self._reload(t)), [])
        self.assertFalse(heal._sha_shaped("defaced"))
        self.assertTrue(heal._sha_shaped("4412760"))

    def test_one_sha_cited_five_times_is_one_thing_to_check(self):
        t = self._task(decisions=["commit 4412760 shipped it",
                                  "sha 4412760 is the one to cherry-pick"])
        self.assertEqual(len(heal.commit_citations(self._reload(t))), 1)

    def test_no_prober_means_every_citation_is_UNKNOWN_and_nothing_is_reported(self):
        t = self._task(decisions=["merged commit 4412760 into main"])
        result = heal.scan(self._reload(t))
        self.assertEqual([c["state"] for c in result["commits"]], [None])
        self.assertEqual([f for f in result["findings"] if f["check"] == "cited-commit"], [])

    def test_a_sha_that_resolves_nowhere_is_a_finding(self):
        t = self._task(decisions=["merged commit 4412760 into main"])
        hits = heal.commit_rot(self._reload(t), probe=lambda sha: False)
        self.assertEqual(len(hits), 1)
        self.assertIn("resolves in none of the task's repos", hits[0]["detail"])
        self.assertIn("4412760", hits[0]["ref"])

    def test_a_sha_that_resolves_is_silent_and_so_is_UNKNOWN(self):
        t = self._task(decisions=["merged commit 4412760 into main"])
        self.assertEqual(heal.commit_rot(self._reload(t), probe=lambda sha: True), [])
        self.assertEqual(heal.commit_rot(self._reload(t), probe=lambda sha: None), [])

    def test_a_prober_that_raises_reads_as_UNKNOWN_never_as_gone(self):
        def boom(_sha):
            raise RuntimeError("git exploded")
        t = self._task(decisions=["merged commit 4412760 into main"])
        self.assertEqual(heal.commit_rot(self._reload(t), probe=boom), [])

    def test_the_prober_asks_git_cat_file_and_reads_its_answer(self):
        asked = []

        def run(args, timeout=None):
            asked.append(args)
            return ("cat-file" not in args), ""      # a repo, but no such commit

        t = self._repo_task(["merged commit 4412760 into main"])
        probe = heal.commit_prober(self._reload(t), run=run)
        self.assertIs(probe("4412760"), False)
        self.assertTrue(any("cat-file" in a for a in asked))
        self.assertTrue(any("4412760^{commit}" in a for a in asked))

    def test_no_usable_repo_is_UNKNOWN_rather_than_gone(self):
        t = self._task(decisions=["merged commit 4412760 into main"])
        probe = heal.commit_prober(self._reload(t), exists=lambda p: False)
        self.assertIsNone(probe("4412760"))

    def test_the_scan_wires_it_through_and_the_finding_counts(self):
        t = self._task(decisions=["merged commit 4412760 into main"])
        result = heal.scan(self._reload(t), commit_probe=lambda sha: False)
        self.assertEqual([f["check"] for f in result["findings"]], ["cited-commit"])
        self.assertTrue(heal.due(self._reload(t), result=result)[0])


class TestTheOptInLinkProbe(_Base):
    """`link_states` has always taken a probe and never been given one. `--probe-links` wires
    a real HTTP HEAD — and ONLY an explicit 404/410 counts as dead, because a private ADO PR
    answers 401 to an unauthenticated request and "your PR link is dead" about a live PR is
    the most expensive false positive this module could print."""

    def setUp(self):
        super(TestTheOptInLinkProbe, self).setUp()
        import urllib.request
        self._real = urllib.request.urlopen

    def tearDown(self):
        import urllib.request
        urllib.request.urlopen = self._real
        super(TestTheOptInLinkProbe, self).tearDown()

    def _patch(self, fn):
        import urllib.request
        urllib.request.urlopen = fn

    def test_a_404_is_the_only_thing_that_counts_as_dead(self):
        import urllib.error

        def gone(req, timeout=None):
            raise urllib.error.HTTPError(getattr(req, "full_url", "u"), 404, "gone",
                                         None, None)
        self._patch(gone)
        self.assertIs(heal.link_prober()("https://example.invalid/pr/1"), False)

    def test_a_401_or_403_is_UNKNOWN_not_dead(self):
        import urllib.error
        for code in (401, 403, 405, 500):
            def refused(req, timeout=None, _c=code):
                raise urllib.error.HTTPError(getattr(req, "full_url", "u"), _c, "no",
                                             None, None)
            self._patch(refused)
            self.assertIsNone(heal.link_prober()("https://example.invalid/pr/1"), code)

    def test_any_exception_is_UNKNOWN(self):
        def boom(req, timeout=None):
            raise OSError("dns is down")
        self._patch(boom)
        self.assertIsNone(heal.link_prober()("https://example.invalid/pr/1"))

    def test_a_200_resolves(self):
        class Fake(object):
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        self._patch(lambda req, timeout=None: Fake())
        self.assertIs(heal.link_prober()("https://example.invalid/pr/1"), True)

    def test_a_non_http_url_is_UNKNOWN_without_asking_anything(self):
        def never(req, timeout=None):
            raise AssertionError("must not probe a non-http url")
        self._patch(never)
        self.assertIsNone(heal.link_prober()("ssh://git@host/repo.git"))

    def test_off_by_default_the_scan_probes_nothing(self):
        def never(req, timeout=None):
            raise AssertionError("the default scan must make no network call")
        self._patch(never)
        t = self._task(decisions=["a call"],
                       prs=[{"url": "https://example.invalid/pr/1", "desc": ""}])
        result = heal.scan(self._reload(t))
        self.assertEqual([l["state"] for l in result["links"]], [None])
        self.assertEqual(result["findings"], [])


# ---------------------------------------------------------------------------
# THE DISMISSAL LEDGER — adjudicating ONE state, never a category.
#
# On one real task the scan stood at 17 findings and 9 were dead paths a human had already
# read and ruled on. The scan had no way to know that, so it reported all 9 again next pass.
# A report that repeats what its reader already answered is one they stop opening — the same
# cost as crying wolf, arriving by a different route.
#
# The fingerprint covers the finding's MATCHED TEXT, so a ruling survives a re-scan and does
# NOT survive the matched text changing. That is the design: "this sentence is fine" is not a
# ruling about the next sentence.
# ---------------------------------------------------------------------------

class TestTheDismissalLedger(_Base):
    SELECTOR = "prose-supersession:decision 2"

    def _prose_task(self):
        return self._reload(self._task(decisions=UNLINKED_PROSE))

    def _dismiss(self, t, selector=None, why="adjudicated: the prose is a minute, not a "
                                            "claim", **kw):
        return self._heal(t, apply=True, dismiss=[selector or self.SELECTOR], why=why, **kw)

    def test_a_dismissed_finding_leaves_the_findings_the_count_and_the_due_calculus(self):
        t = self._prose_task()
        self.assertTrue(heal.due(t)[0])
        out = self._dismiss(t)
        self.assertIn("DISMISSED prose-supersession:decision 2", out)
        after = self._reload(t)
        result = heal.scan(after)
        self.assertEqual(result["findings"], [])
        self.assertEqual(len(result["dismissed"]), 1)
        self.assertEqual(heal.mechanical_line(result), "clean")
        self.assertFalse(heal.due(after, result=result)[0])

    def test_a_dismissal_with_no_why_is_refused_and_changes_nothing(self):
        t = self._prose_task()
        out = self._heal(t, apply=True, dismiss=[self.SELECTOR])
        self.assertIn("needs --why", out)
        self.assertEqual(heal.dismissal_ledger(self._reload(t)), [])
        self.assertTrue(heal.scan(self._reload(t))["findings"])

    def test_the_scan_prints_one_informational_dismissed_line(self):
        t = self._prose_task()
        self._dismiss(t)
        out = self._heal(self._reload(t), scan=True)
        self.assertIn("Dismissed", out)
        self.assertIn("heal --dismissals", out)
        self.assertNotIn("YES", out.rsplit("Heal due?", 1)[-1])

    def test_a_dismissal_survives_a_re_scan(self):
        t = self._prose_task()
        self._dismiss(t)
        for _ in range(3):
            self.assertEqual(heal.scan(self._reload(t))["findings"], [])

    def test_changing_the_matched_text_makes_the_finding_RE_REPORT(self):
        # The ruling covered the sentence that was there. Rewriting the entry — here, from
        # "was wrong" to "no longer holds" — changes what the check matched, so nobody has
        # ruled on the new one.
        t = self._prose_task()
        self._dismiss(t)
        edited = self._reload(t)
        edited["decisions"][1] = "decision 1 no longer holds — sqlite, for the FTS index"
        ts.save_task(edited)
        result = heal.scan(self._reload(t))
        self.assertEqual([f["check"] for f in result["findings"]], ["prose-supersession"])
        self.assertEqual(result["dismissed"], [])

    def test_an_unambiguous_substring_of_a_ref_resolves(self):
        t = self._prose_task()
        out = self._dismiss(t, selector="prose-supersession:decision")
        self.assertIn("DISMISSED prose-supersession:decision 2", out)

    def test_an_ambiguous_ref_is_refused_with_the_list(self):
        big = "b" * (heal.OVERSIZE_CHARS + 10)
        t = self._reload(self._task(decisions=[big, big]))
        out = self._dismiss(t, selector="oversized:decision")
        self.assertIn("ambiguous", out)
        self.assertIn("oversized:decision 1", out)
        self.assertIn("oversized:decision 2", out)
        self.assertEqual(heal.dismissal_ledger(self._reload(t)), [])

    def test_dismissing_something_that_is_not_a_current_finding_is_refused(self):
        t = self._prose_task()
        out = self._dismiss(t, selector="oversized:decision 1")
        self.assertIn("no finding for check", out)
        self.assertIn("prose-supersession:decision 2", out)     # what WOULD work
        self.assertEqual(heal.dismissal_ledger(self._reload(t)), [])

    def test_a_selector_with_no_check_is_refused_rather_than_guessed(self):
        t = self._prose_task()
        out = self._dismiss(t, selector="decision 2")
        self.assertIn("'<check>:<ref>'", out)
        self.assertEqual(heal.dismissal_ledger(self._reload(t)), [])

    def test_dismissing_the_same_finding_twice_says_so(self):
        t = self._prose_task()
        self._dismiss(t)
        out = self._dismiss(self._reload(t))
        self.assertIn("already dismissed", out)
        self.assertEqual(len(heal.dismissal_ledger(self._reload(t))), 1)

    def test_undismiss_restores_full_reporting_and_retires_rather_than_deletes(self):
        t = self._prose_task()
        self._dismiss(t)
        out = self._heal(self._reload(t), apply=True, undismiss=[self.SELECTOR])
        self.assertIn("UNDISMISSED", out)
        after = self._reload(t)
        self.assertEqual([f["check"] for f in heal.scan(after)["findings"]],
                         ["prose-supersession"])
        ledger = heal.dismissal_ledger(after)
        self.assertEqual(len(ledger), 1)                 # nothing was deleted
        self.assertTrue(ledger[0]["retired"])
        self.assertEqual(heal.active_dismissals(after), [])

    def test_re_dismissing_after_an_undismiss_appends_a_second_ruling(self):
        # Two rulings made at two moments, possibly for two reasons. Reviving the first
        # would put one session's why on another session's decision.
        t = self._prose_task()
        self._dismiss(t, why="first call")
        self._heal(self._reload(t), apply=True, undismiss=[self.SELECTOR])
        self._dismiss(self._reload(t), why="second call, after re-reading it")
        ledger = heal.dismissal_ledger(self._reload(t))
        self.assertEqual(len(ledger), 2)
        self.assertEqual([e.get("why") for e in ledger],
                         ["first call", "second call, after re-reading it"])

    def test_the_listing_shows_the_why_the_date_and_whether_it_still_silences(self):
        t = self._prose_task()
        self._dismiss(t, why="the prose minutes decision 1, it does not contradict it")
        out = self._heal(self._reload(t), dismissals=True)
        self.assertIn("DISMISSALS", out)
        self.assertIn("the prose minutes decision 1", out)
        self.assertIn("in force", out)
        self.assertIn(time.strftime("%Y"), out)

    def test_the_listing_calls_an_expired_ruling_expired(self):
        t = self._prose_task()
        self._dismiss(t)
        edited = self._reload(t)
        edited["decisions"][1] = "decision 1 no longer holds — sqlite, for the FTS index"
        ts.save_task(edited)
        out = self._heal(self._reload(t), dismissals=True)
        self.assertIn("EXPIRED", out)

    def test_an_empty_ledger_says_so_plainly(self):
        t = self._prose_task()
        self.assertIn("none", self._heal(t, dismissals=True))

    def test_a_dismiss_without_apply_is_refused_rather_than_silently_doing_nothing(self):
        t = self._prose_task()
        out = self._heal(t, dismiss=[self.SELECTOR], why="a reason")
        self.assertIn("Nothing was changed", out)
        self.assertEqual(heal.dismissal_ledger(self._reload(t)), [])

    def test_a_dismiss_with_scan_is_refused(self):
        t = self._prose_task()
        out = self._heal(t, scan=True, dismiss=[self.SELECTOR], why="a reason")
        self.assertIn("read-only", out)
        self.assertEqual(heal.dismissal_ledger(self._reload(t)), [])

    def test_a_dismiss_with_all_is_refused(self):
        t = self._prose_task()
        out = self._out(ts.cmd_heal, _Args(all=True, apply=True,
                                           dismiss=[self.SELECTOR], why="a reason"))
        self.assertIn("ONE task", out)
        self.assertEqual(heal.dismissal_ledger(self._reload(t)), [])

    def test_a_dismissal_NEVER_stamps_a_heal(self):
        # Adjudicating a false positive is not reconciling a task. A stamp that said
        # otherwise would make every other stamp unreadable.
        t = self._prose_task()
        out = self._dismiss(t)
        self.assertIn("NO HEAL WAS STAMPED", out)
        self.assertIsNone(self._reload(t).get("last_heal_ts"))

    def test_the_dismissal_write_path_refuses_to_share_a_run_with_the_verbs(self):
        t = self._reload(self._task(decisions=["a", "b", "the surviving summary"]))
        out = self._heal(t, apply=True, dismiss=[self.SELECTOR], why="r",
                         merge="1,2", into="3")
        self.assertIn("run them separately", out)
        self.assertEqual(len(dec.live(self._reload(t)["decisions"])), 3)

    def test_the_listing_and_the_candidates_view_are_reads_and_refuse_to_write(self):
        t = self._prose_task()
        for kw in ({"dismissals": True}, {"candidates": True}):
            kw2 = dict(kw)
            kw2["apply"] = True
            self.assertIn("is a READ", self._heal(t, **kw2))

    def test_the_fingerprint_is_stable_across_processes(self):
        # hashlib, not hash(): a per-process string seed would make every stored ruling
        # silently expire on restart.
        f = {"check": "oversized", "ref": "decision 1", "detail": "3610 chars"}
        self.assertEqual(heal.finding_fingerprint(f), heal.finding_fingerprint(dict(f)))
        self.assertEqual(len(heal.finding_fingerprint(f)), 40)
        self.assertNotEqual(heal.finding_fingerprint(f),
                            heal.finding_fingerprint(dict(f, detail="3611 chars")))


# ---------------------------------------------------------------------------
# MERGE AT SCALE — completion, subject, size, and the cheap view.
#
# The shape tier matches how an entry OPENS, so it knows neither whether the work is FINISHED
# nor what the entry is ABOUT. Four additions: a completion signal from the checklist, subject
# grouping, a size objective the stamp baselines, and a candidates-only view that leaves the
# corpus behind.
# ---------------------------------------------------------------------------

class TestStepReferences(_Base):
    def test_only_the_explicit_shapes_are_read(self):
        self.assertEqual(heal.step_refs("step 29 is the rename", total=40), [29])
        self.assertEqual(heal.step_refs("steps 3-6 are the scrub", total=40), [3, 4, 5, 6])
        self.assertEqual(heal.step_refs("steps 3, 4 and 5 landed", total=40), [3, 4, 5])
        self.assertEqual(heal.step_refs("steps 3 to 5 landed", total=40), [3, 4, 5])

    def test_a_bare_number_is_never_a_step_reference(self):
        # A decision's prose is full of bare numbers — char counts, versions, percentages.
        # Reading one as "step 4" would make a merge proposal depend on a character count.
        self.assertEqual(heal.step_refs("4 of them, at 1400 chars each", total=40), [])

    def test_out_of_range_numbers_are_dropped(self):
        self.assertEqual(heal.step_refs("step 99 is the rename", total=3), [])

    def test_an_absurd_range_is_not_expanded(self):
        refs = heal.step_refs("steps 1-9999 are the plan")
        self.assertNotIn(500, refs)
        self.assertEqual(refs, [1, 9999])


class TestCompletedSubjects(_Base):
    def _task_with_steps(self, decisions, steps):
        return self._reload(self._task(decisions=decisions, steps=steps))

    def test_a_decision_whose_steps_are_all_done_is_a_completed_subject(self):
        t = self._task_with_steps(
            ["hold the rename until steps 1 and 2 land"],
            [{"text": "write the migration", "done": True},
             {"text": "run it", "done": True}])
        self.assertEqual(heal.completed_subjects(t), {1: [1, 2]})

    def test_one_open_step_is_enough_to_keep_it_load_bearing(self):
        t = self._task_with_steps(
            ["hold the rename until steps 1 and 2 land"],
            [{"text": "write the migration", "done": True},
             {"text": "run it", "done": False}])
        self.assertEqual(heal.completed_subjects(t), {})

    def test_a_superseded_step_counts_as_finished(self):
        # A retired step is work the checklist has withdrawn; the decision that planned it is
        # no more load-bearing than if it had been ticked.
        t = self._task_with_steps(
            ["hold the rename until steps 1 and 2 land"],
            [{"text": "write the migration", "done": True},
             {"text": "run it", "done": False}])
        ok, err = steps.mark_superseded(t["steps"], 2, None)
        self.assertTrue(ok, err)
        ts.save_task(t)
        self.assertEqual(heal.completed_subjects(self._reload(t)), {1: [1, 2]})

    def test_a_decision_naming_no_step_is_never_tagged(self):
        t = self._task_with_steps(["chose sqlite over flat files"],
                                  [{"text": "done thing", "done": True}])
        self.assertEqual(heal.completed_subjects(t), {})


class TestSubjectCandidates(_Base):
    VERSIONED = ["2.13.1 shipped the memo backstop and the nag cap",
                 "the release notes for 2.13.1 were rewritten after the scrub"]

    def test_two_decisions_sharing_a_release_version_are_one_group(self):
        t = self._reload(self._task(decisions=self.VERSIONED))
        groups = heal.subject_candidates(t)
        self.assertEqual([g["indices"] for g in groups], [[1, 2]])
        self.assertIn("version 2.13.1", groups[0]["signals"])

    def test_two_decisions_sharing_a_PR_number_are_one_group(self):
        t = self._reload(self._task(decisions=["PR 1204 carries the store change",
                                               "reviewed PR 1204 and asked for the rename"]))
        self.assertEqual([g["indices"] for g in heal.subject_candidates(t)], [[1, 2]])

    def test_two_decisions_naming_the_same_finished_step_are_tagged_completed(self):
        t = self._reload(self._task(
            decisions=["step 1 holds the rename until the export lands",
                       "step 1 also blocks the schema change"],
            steps=[{"text": "hold the rename", "done": True}]))
        groups = heal.subject_candidates(t)
        self.assertEqual([g["indices"] for g in groups], [[1, 2]])
        self.assertIn("completed-subject", groups[0]["tags"])

    def test_an_open_step_leaves_the_group_untagged(self):
        t = self._reload(self._task(
            decisions=["step 1 holds the rename until the export lands",
                       "step 1 also blocks the schema change"],
            steps=[{"text": "hold the rename", "done": False}]))
        self.assertEqual(heal.subject_candidates(t)[0]["tags"], [])

    def test_grouping_is_transitive_so_one_subject_is_one_group(self):
        t = self._reload(self._task(
            decisions=["2.13.1 shipped, and step 1 is done",
                       "2.13.1 needed a follow-up note",
                       "step 1 was the last blocker"],
            steps=[{"text": "hold the rename", "done": True}]))
        self.assertEqual([g["indices"] for g in heal.subject_candidates(t)], [[1, 2, 3]])

    def test_two_is_enough_here_where_the_shape_tier_needs_three(self):
        t = self._reload(self._task(decisions=self.VERSIONED))
        self.assertEqual(heal.merge_candidates(t), [])          # shape tier: silent
        self.assertEqual(len(heal.subject_candidates(t)), 1)    # subject tier: proposes

    def test_unrelated_decisions_are_never_grouped(self):
        t = self._reload(self._task(decisions=["chose sqlite over flat files",
                                               "terminal tint uses the sands palette"]))
        self.assertEqual(heal.subject_candidates(t), [])

    def test_distinct_versions_do_not_share_a_subject(self):
        t = self._reload(self._task(decisions=["2.9.0 SHIPPED: pins", "2.10.0 SHIPPED: the "
                                               "board column"]))
        self.assertEqual(heal.subject_candidates(t), [])

    def test_a_merged_away_group_is_not_proposed_again(self):
        t = self._task(decisions=self.VERSIONED + ["2.13.1, one reconciled record"])
        for i in (1, 2):
            dec.mark_merged(t["decisions"], i, 3)
        ts.save_task(t)
        self.assertEqual(heal.subject_candidates(self._reload(t)), [])

    def test_they_are_proposals_and_never_findings(self):
        t = self._reload(self._task(decisions=self.VERSIONED))
        result = heal.scan(t)
        self.assertEqual(result["findings"], [])
        self.assertEqual(len(result["subject_candidates"]), 1)
        self.assertFalse(heal.due(t, result=result)[0])

    def test_the_scan_renders_them_above_the_shape_tier_and_says_PROPOSALS(self):
        t = self._reload(self._task(decisions=self.VERSIONED))
        out = self._heal(t, scan=True)
        self.assertIn("Subject candidates", out)
        self.assertIn("PROPOSALS", out)
        self.assertIn("decisions 1, 2", out)

    def test_candidate_groups_puts_the_subject_tier_first(self):
        t = self._reload(self._task(decisions=self.VERSIONED + [
            "MY PROCESS ERROR: renamed without checking",
            "MY PROCESS ERROR: shipped before the tests",
            "MY PROCESS ERROR: forgot the stamp"]))
        tiers = [g["tier"] for g in heal.candidate_groups(t)]
        self.assertEqual(tiers[0], "subject")
        self.assertIn("shape", tiers)


class TestTheSizeObjective(_Base):
    def _stamped(self, decisions):
        t = self._task(decisions=decisions)
        heal.stamp_healed(t)
        ts.save_task(t)
        return self._reload(t)

    def test_the_stamp_snapshots_the_digest_chars(self):
        t = self._stamped(["one call of some length"])
        self.assertEqual(t[heal.CHARS_AT_LAST_HEAL], dec.total_chars(t["decisions"]))

    def test_no_baseline_is_UNKNOWN_never_a_zero_delta(self):
        t = self._reload(self._task(decisions=["one call"]))
        size = heal.size_objective(t)
        self.assertFalse(size["known"])
        self.assertIsNone(size["delta"])
        self.assertIn("no baseline", heal.size_line(size))

    def test_the_delta_is_reported_against_the_baseline(self):
        t = self._stamped(["one call"])
        ts.append_decision(t, "a second call, longer than the first")
        ts.save_task(t)
        size = heal.size_objective(self._reload(t))
        self.assertTrue(size["known"])
        self.assertGreater(size["delta"], 0)
        line = heal.size_line(size)
        self.assertIn("at last heal", line)
        self.assertIn("+%d" % size["delta"], line)

    def test_a_merge_makes_the_number_go_DOWN(self):
        # The objective, stated as a test: reconciling is supposed to shrink the digest. The
        # originals are padded because a merge of three ONE-LINE records genuinely costs more
        # than it saves — the summary carries its own explanation — and a test that hid that
        # would be asserting the wrong thing.
        t = self._task(decisions=[
            "2.7.0 SHIPPED: the store moved to sqlite. " + "The reasoning ran on. " * 12,
            "2.8.0 SHIPPED: the board story column. " + "With all its detail. " * 12,
            "2.9.0 SHIPPED: supersession and pins. " + "And the dispositions. " * 12])
        before = dec.total_chars(self._reload(t)["decisions"])
        self._heal(t, apply=True)
        self.assertLess(dec.total_chars(self._reload(t)["decisions"]), before)

    def test_the_scan_always_prints_the_row_even_unbaselined(self):
        t = self._reload(self._task(decisions=["one call"]))
        out = self._heal(t, scan=True)
        self.assertIn("Digest size", out)
        self.assertIn("no baseline", out)


class TestGrewWithCandidatesOutstanding(_Base):
    """The ONE finding the merge work can make, and the only new way it can make a heal due.
    Growth alone is not a defect (a working task records work) and candidates alone are not a
    defect (nobody has ruled on them). The conjunction says the record is getting more
    expensive to brief in exactly the place a named verb was waiting."""

    SHARED = ["2.13.1 shipped the memo backstop",
              "the 2.13.1 notes needed a follow-up"]

    def _grown(self, decisions, extra="a new call that grows the digest a little"):
        t = self._task(decisions=decisions)
        heal.stamp_healed(t)
        ts.save_task(t)
        t = self._reload(t)
        if extra:
            ts.append_decision(t, extra)
            ts.save_task(t)
        return self._reload(t)

    def test_growth_with_candidates_outstanding_is_a_finding_and_makes_a_heal_due(self):
        t = self._grown(list(self.SHARED))
        result = heal.scan(t)
        self.assertEqual([f["check"] for f in result["findings"]], ["grew-with-candidates"])
        self.assertIn("has GROWN", result["findings"][0]["detail"])
        self.assertTrue(heal.due(t, result=result)[0])

    def test_growth_with_nothing_to_merge_is_not_a_finding(self):
        t = self._grown(["chose sqlite over flat files for the store"])
        self.assertEqual(heal.scan(t)["findings"], [])

    def test_candidates_with_no_growth_are_not_a_finding(self):
        t = self._grown(list(self.SHARED), extra=None)
        self.assertEqual(heal.scan(t)["findings"], [])

    def test_no_baseline_is_never_a_finding(self):
        t = self._reload(self._task(decisions=list(self.SHARED)))
        self.assertEqual(heal.scan(t)["findings"], [])

    def test_it_is_ONE_finding_about_the_record_not_one_per_group(self):
        t = self._grown(self.SHARED + ["PR 1204 landed", "PR 1204 was reverted"])
        hits = [f for f in heal.scan(t)["findings"] if f["check"] == "grew-with-candidates"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["ref"], "digest")

    def test_the_finding_names_the_candidates_view_and_refuses_to_merge_for_you(self):
        detail = heal.scan(self._grown(list(self.SHARED)))["findings"][0]["detail"]
        self.assertIn("heal --candidates", detail)
        self.assertIn("not proposed for you", detail)


class TestTheCandidatesView(_Base):
    def _task_with_group(self):
        return self._reload(self._task(
            title="Candidates",
            goal="the exporter runs nightly without a human",
            decisions=["2.13.1 shipped the memo backstop",
                       "the 2.13.1 notes needed a follow-up",
                       "UNRELATED: terminal tint uses the sands palette"]))

    def test_it_prints_the_goal_the_pins_and_the_group_members_in_full(self):
        t = self._task_with_group()
        out = self._heal(t, candidates=True)
        self.assertIn("[HEAL-CANDIDATES]", out)
        self.assertIn("THE GOAL LINE", out)
        self.assertIn("exporter runs nightly", out)
        self.assertIn("PINNED DECISIONS", out)
        self.assertIn("2.13.1 shipped the memo backstop", out)
        self.assertIn("the 2.13.1 notes needed a follow-up", out)
        self.assertIn("heal --merge 1,2 --into", out)

    def test_it_does_NOT_print_the_corpus(self):
        # The whole point: the full dry run is ~94% decision list, and a reader working the
        # merge candidates needs the groups, not the corpus.
        out = self._heal(self._task_with_group(), candidates=True)
        self.assertNotIn("terminal tint uses the sands palette", out)
        self.assertNotIn("NOW DO THE JUDGMENT WORK", out)

    def test_no_groups_says_so_and_calls_it_healthy(self):
        t = self._reload(self._task(decisions=["chose sqlite over flat files"]))
        out = self._heal(t, candidates=True)
        self.assertIn("NO CANDIDATE GROUPS", out)
        self.assertIn("healthy", out)

    def test_it_is_read_only_and_stamps_nothing(self):
        t = self._task_with_group()
        self._heal(t, candidates=True)
        after = self._reload(t)
        self.assertIsNone(after.get("last_heal_ts"))
        self.assertEqual(len(after["decisions"]), 3)


# ---------------------------------------------------------------------------
# THE GOAL-REVIEW DUE LIMB — the count finally does something.
#
# `goal_review` counted what had landed since the goal was written and could never act on it,
# so a goal nobody had re-read across forty decisions printed the same row as one written this
# morning. Past the threshold that count IS the due reason — while still never being an
# ISSUE, because an untouched goal is not a defect. RE-READING is the service, so
# `--goal-reviewed` resets it without rewriting a sentence that is still true, and
# `--mark-healed` deliberately does not.
# ---------------------------------------------------------------------------

class TestTheGoalReviewDueLimb(_Base):
    GOAL = "the exporter runs nightly without a human"

    def _aged_goal(self, n):
        """A task whose goal was written `n` decisions ago, through the real write path."""
        t = self._task(decisions=["the first call"])
        self._update(t, goal=self.GOAL)
        t = self._reload(t)
        for i in range(n):
            ts.append_decision(t, "call number %d, recorded in full" % i)
        ts.save_task(t)
        return self._reload(t)

    def test_the_threshold_defaults_to_25(self):
        self.assertEqual(heal.GOAL_REVIEW_DUE, 25)
        self.assertEqual(heal.goal_review_due(), 25)

    def test_at_the_threshold_the_heal_is_due_and_the_reason_is_the_count(self):
        t = self._aged_goal(heal.GOAL_REVIEW_DUE)
        is_due, reasons = heal.due(t)
        self.assertTrue(is_due)
        self.assertTrue(any("since the goal line was last reviewed" in r for r in reasons))

    def test_below_the_threshold_it_is_silent(self):
        t = self._aged_goal(heal.GOAL_REVIEW_DUE - 1)
        self.assertFalse(any("goal line was last reviewed" in r
                             for r in heal.due(t)[1]))

    def test_it_is_never_an_ISSUE_even_when_it_makes_a_heal_due(self):
        t = self._aged_goal(heal.GOAL_REVIEW_DUE)
        result = heal.scan(t)
        self.assertEqual(result["findings"], [])
        self.assertEqual(heal.mechanical_line(result), "clean")

    def test_no_baseline_means_the_limb_stays_silent(self):
        # Every task written before the baseline existed takes this path, so it is the
        # common case — and a permanently-due board would be the always-on alarm again.
        t = self._task(decisions=["d%d" % i for i in range(40)], goal=self.GOAL)
        self.assertFalse(any("goal line was last reviewed" in r
                             for r in heal.due(self._reload(t))[1]))

    def test_the_tunable_overrides_the_default(self):
        os.environ["TASK_STATION_HEAL_GOAL_REVIEW_DUE"] = "3"
        try:
            self.assertEqual(heal.goal_review_due(), 3)
            t = self._aged_goal(3)
            self.assertTrue(any("since the goal line was last reviewed" in r
                                for r in heal.due(t)[1]))
        finally:
            os.environ.pop("TASK_STATION_HEAL_GOAL_REVIEW_DUE", None)

    def test_a_nonpositive_override_falls_back_rather_than_making_everything_due(self):
        for bad in ("0", "-5", "nonsense"):
            os.environ["TASK_STATION_HEAL_GOAL_REVIEW_DUE"] = bad
            try:
                self.assertEqual(heal.goal_review_due(), 25, bad)
            finally:
                os.environ.pop("TASK_STATION_HEAL_GOAL_REVIEW_DUE", None)

    def test_goal_reviewed_resets_the_count_WITHOUT_rewriting_the_goal(self):
        t = self._aged_goal(heal.GOAL_REVIEW_DUE)
        out = self._heal(t, goal_reviewed=True)
        self.assertIn("GOAL REVIEW RECORDED", out)
        after = self._reload(t)
        self.assertEqual(after["goal"], self.GOAL)              # untouched
        g = heal.goal_review(after)
        self.assertEqual(g["since_review"], 0)
        self.assertEqual(g["since"], heal.GOAL_REVIEW_DUE)      # the WRITE baseline stands
        self.assertTrue(g["reviewed_only"])
        self.assertFalse(any("goal line was last reviewed" in r
                             for r in heal.due(after)[1]))

    def test_mark_healed_does_NOT_silently_reset_it(self):
        t = self._aged_goal(heal.GOAL_REVIEW_DUE)
        self._heal(t, mark_healed=True, note="read the whole log")
        after = self._reload(t)
        self.assertNotIn(heal.GOAL_REVIEWED_FIELD, after)
        self.assertTrue(any("since the goal line was last reviewed" in r
                            for r in heal.due(after)[1]))

    def test_rewriting_the_goal_also_clears_the_limb(self):
        t = self._aged_goal(heal.GOAL_REVIEW_DUE)
        self._update(t, goal="the exporter is retired and the managed job owns it")
        self.assertFalse(any("goal line was last reviewed" in r
                             for r in heal.due(self._reload(t))[1]))

    def test_goal_reviewed_on_a_task_with_no_goal_is_refused(self):
        t = self._task(decisions=["one call"])
        out = self._heal(t, goal_reviewed=True)
        self.assertIn("REFUSED", out)
        self.assertNotIn(heal.GOAL_REVIEWED_FIELD, self._reload(t))

    def test_goal_reviewed_pairs_with_mark_healed_and_refuses_everything_else(self):
        t = self._aged_goal(2)
        out = self._heal(t, goal_reviewed=True, mark_healed=True, note="read it all")
        self.assertIn("GOAL REVIEW RECORDED", out)
        self.assertIn("MARKED HEALED", out)
        after = self._reload(t)
        self.assertIn(heal.GOAL_REVIEWED_FIELD, after)
        self.assertIsNotNone(after.get("last_heal_ts"))
        self.assertIn("cannot be combined",
                      self._heal(self._reload(t), goal_reviewed=True, apply=True))

    def test_the_rendered_row_no_longer_claims_it_can_never_make_a_heal_due(self):
        # A heading that says one thing while the verdict says another is worse than either.
        t = self._aged_goal(2)
        rows = "\n".join(heal.goal_review_lines(heal.scan(t)))
        self.assertIn("PROPOSAL", rows)
        self.assertNotIn("never make a heal due", rows)
        self.assertIn("25 decision(s)", rows)          # the count the verdict would use
        self.assertIn("--goal-reviewed", rows)         # and the verb that answers it
        self.assertIn("Goal review", self._heal(t, scan=True))

    def test_a_recorded_re_read_is_rendered_as_one(self):
        t = self._aged_goal(4)
        self._heal(t, goal_reviewed=True)
        rows = "\n".join(heal.goal_review_lines(heal.scan(self._reload(t))))
        self.assertIn("re-read (not rewritten)", rows)


class TestBothSurfacesTeachTheNewMoves(unittest.TestCase):
    """A verb the skill does not teach is a verb that never gets used — the reason
    `test_the_model_facing_guidance_documents_heal_and_restore` exists. These are the five
    moves added in this pass, and each one has to be reachable from the prose a model reads."""

    def _read(self, *parts):
        with open(os.path.join(_REPO_ROOT, *parts), encoding="utf-8") as f:
            return f.read()

    def test_the_skill_teaches_every_new_flag(self):
        text = self._read("skills", "heal", "SKILL.md")
        for flag in ("--dismiss", "--undismiss", "--dismissals", "--candidates",
                     "--goal-reviewed", "--probe-links"):
            self.assertIn(flag, text, flag)

    def test_the_command_file_teaches_the_ones_a_pass_actually_runs(self):
        text = self._read("commands", "heal.md")
        for flag in ("--dismiss", "--candidates", "--goal-reviewed", "--probe-links"):
            self.assertIn(flag, text, flag)

    def test_both_surfaces_say_the_why_is_mandatory(self):
        for text in (self._read("skills", "heal", "SKILL.md"),
                     self._read("commands", "heal.md")):
            self.assertIn("--why", text)
            self.assertIn("mandatory", text.lower())

    def test_the_skill_states_the_size_objective_and_both_size_tiers(self):
        text = self._read("skills", "heal", "SKILL.md")
        self.assertIn("at last heal", text)
        self.assertIn("3,600", text)
        self.assertIn("1,400", text)          # the measured average both tiers answer to

    def test_the_skill_teaches_the_completed_subject_tag(self):
        text = self._read("skills", "heal", "SKILL.md")
        self.assertIn("COMPLETED-SUBJECT", text)
        self.assertIn("SUBJECT CANDIDATES", text)

    def test_the_cli_help_names_the_new_flags(self):
        # The one-line command help is where somebody reading `task-station` finds them.
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_guidance(_Args())
        out = buf.getvalue()
        for flag in ("--dismiss", "--candidates", "--goal-reviewed", "--probe-links"):
            self.assertIn(flag, out, flag)


# ---------------------------------------------------------------------------
# WHICH REPOS A PROBER MAY ASK — the folder rename that made the answer wrong.
#
# `task_repos` derived its list from `recorded_paths` alone: the FILE and WORKTREE paths a
# task wrote down. Rename the folder those paths point into and every one of them dies at
# once — while the ones pointing at something UNRELATED (a notes vault, the installed
# plugin cache) survive. The list is then non-empty and holds the wrong repo, so the
# probers answer a confident False about a branch and a commit that are sitting right
# there. Measured on one real task: 16 of 27 findings were false, and the docstring's own
# promise — "an empty list is what makes both probers return UNKNOWN rather than False" —
# was kept for the empty case and broken for the WRONG-REPO case, which is worse because
# it looks answered.
#
# TWO REPAIRS, and they cover the two halves of the same mistake:
#   * WIDEN THE SCOPE. The repos a task NAMES (`projects`) are resolved through the repo
#     index (`repos.json`), which records where each repo IS now rather than where a path
#     recorded months ago said it was. A rename moves the index entry; it does not delete it.
#   * NARROW THE CLAIM. When a named repo has no LOCAL clone to ask, the prober cannot see
#     the whole scope it is implicitly claiming to have searched, so a negative is UNKNOWN
#     rather than rot. The 16th false finding on that task was exactly this: a sha in an
#     ADO repo nobody had cloned here.
# ---------------------------------------------------------------------------

class TestWhichReposAProberAsks(_Base):
    """Every fixture here is the RENAME: the recorded code paths are gone, one unrelated
    recorded path survives (so the list is non-empty and the old code answers False), and
    the repo the task names is findable only through the index."""

    def setUp(self):
        super(TestWhichReposAProberAsks, self).setUp()
        # The survivor: an unrelated recorded path that really exists. On the real task
        # this was the notes vault.
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)
        # Where the named repo actually lives NOW. Nothing recorded points here.
        self.code = os.path.join(self.tmp, "task-station")
        os.makedirs(self.code)
        # Where every recorded code path still points. It does not exist.
        self.gone = os.path.join(self.tmp, "claude-todo")

    def _index(self, entries):
        """Write `repos.json` where `paths.data_dir()` reads it — which `_repoint` has
        already pinned to this test's temp dir."""
        with open(os.path.join(self.tmp, "repos.json"), "w") as fh:
            json.dump(entries, fh)

    def _renamed(self, decisions=None, projects=("task-station",), **fields):
        return self._reload(self._task(
            decisions=decisions or ["merged commit 4412760 into main"],
            files=[os.path.join(self.gone, "lib", "heal.py"),
                   os.path.join(self.vault, "INDEX.md")],
            projects=list(projects), **fields))

    def _run_only(self, *live):
        """A git stub that answers as if `live` are the only real repos, and as if the ref
        or object asked about resolves in each of them. Never runs git."""
        live = set(live)
        seen = []

        def run(args, timeout=None):
            seen.append(args)
            d = args[args.index("-C") + 1] if "-C" in args else None
            return (d in live), ""
        return run, seen

    # -- widening the scope -------------------------------------------------------

    def test_the_repo_the_task_NAMES_is_asked_even_when_no_recorded_path_survives(self):
        self._index([{"name": "task-station", "path": self.code}])
        t = self._renamed()
        run, _seen = self._run_only(self.code, self.vault)
        self.assertIn(self.code, heal.task_repos(t, run=run))

    def test_a_named_repo_missing_from_the_index_is_simply_not_asked(self):
        # Fail-open: no index entry is not an error, it is one fewer repo to ask.
        self._index([{"name": "something-else", "path": self.code}])
        t = self._renamed()
        run, _seen = self._run_only(self.code, self.vault)
        self.assertNotIn(self.code, heal.task_repos(t, run=run))

    def test_an_unreadable_index_never_raises(self):
        with open(os.path.join(self.tmp, "repos.json"), "w") as fh:
            fh.write("{not json")
        t = self._renamed()
        run, _seen = self._run_only(self.vault)
        self.assertEqual(heal.task_repos(t, run=run), [self.vault])

    def test_a_branch_that_resolves_in_the_named_repo_is_NOT_reported_gone(self):
        # The live case: branch monorepo-3.0.0 resolved at a sha locally and on origin, and
        # the scan called it gone because it asked the vault.
        self._index([{"name": "task-station", "path": self.code}])
        t = self._renamed(decisions=["cut it on branch monorepo-3.0.0"])
        run, _seen = self._run_only(self.code, self.vault)

        def only_code_has_it(args, timeout=None):
            ok, out = run(args, timeout=timeout)
            if "rev-parse" in args and "--verify" in args:
                d = args[args.index("-C") + 1]
                return (d == self.code), ""
            return ok, out
        self.assertIs(heal.branch_prober(t, run=only_code_has_it)("monorepo-3.0.0"), True)

    def test_a_commit_that_resolves_in_the_named_repo_is_NOT_reported_rewritten(self):
        self._index([{"name": "task-station", "path": self.code}])
        t = self._renamed()
        run, _seen = self._run_only(self.code, self.vault)

        def only_code_has_it(args, timeout=None):
            ok, out = run(args, timeout=timeout)
            if "cat-file" in args:
                d = args[args.index("-C") + 1]
                return (d == self.code), ""
            return ok, out
        probe = heal.commit_prober(t, run=only_code_has_it)
        self.assertIs(probe("4412760"), True)
        self.assertEqual(heal.commit_rot(t, probe=probe), [])

    # -- narrowing the claim ------------------------------------------------------

    def test_a_named_repo_with_no_local_clone_makes_a_negative_UNKNOWN(self):
        """The 16th false finding: a sha cited from an ADO repo with no clone on this
        machine. The prober searched everything it could reach, found nothing, and called
        it rot — a claim about repos it never opened."""
        self._index([{"name": "task-station", "path": self.code}])
        t = self._renamed(projects=("task-station", "org-brain-profile"))
        run, _seen = self._run_only(self.code, self.vault)

        def resolves_nowhere(args, timeout=None):
            ok, out = run(args, timeout=timeout)
            if "cat-file" in args or "--verify" in args:
                return False, ""
            return ok, out
        probe = heal.commit_prober(t, run=resolves_nowhere)
        self.assertIsNone(probe("4412760"))
        self.assertEqual(heal.commit_rot(t, probe=probe), [])
        self.assertIsNone(heal.branch_prober(t, run=resolves_nowhere)("some-branch"))

    def test_with_every_named_repo_local_a_negative_is_still_a_finding(self):
        """The narrowing must not switch the check off. Every named repo is reachable
        here, so "resolves in none of them" is a claim the prober is entitled to make."""
        self._index([{"name": "task-station", "path": self.code}])
        t = self._renamed()
        run, _seen = self._run_only(self.code, self.vault)

        def resolves_nowhere(args, timeout=None):
            ok, out = run(args, timeout=timeout)
            if "cat-file" in args:
                return False, ""
            return ok, out
        probe = heal.commit_prober(t, run=resolves_nowhere)
        self.assertIs(probe("4412760"), False)
        self.assertEqual(len(heal.commit_rot(t, probe=probe)), 1)

    def test_a_task_naming_no_repos_at_all_is_unchanged(self):
        """`projects` is empty on most tasks. Nothing about them may move."""
        self._index([{"name": "task-station", "path": self.code}])
        t = self._renamed(projects=())
        run, _seen = self._run_only(self.code, self.vault)
        self.assertEqual(heal.task_repos(t, run=run), [self.vault])
        self.assertIs(heal.commit_prober(t, run=run)("4412760"), True)

    def test_no_usable_repo_at_all_is_still_UNKNOWN(self):
        self._index([{"name": "task-station", "path": self.code}])
        t = self._renamed()
        self.assertIsNone(heal.commit_prober(t, exists=lambda p: False)("4412760"))


# ---------------------------------------------------------------------------
# AN IDENTICAL FINDING IS ONE FINDING — the rows nobody could adjudicate.
#
# `--dismiss` refuses an ambiguous selector rather than guessing, which is right. But two
# findings can be BYTE-IDENTICAL — five sessions recording the same worktree cwd produce
# five identical drift rows — and "name one exactly" is then an instruction nobody can
# follow. On one real task 7 findings were permanently unadjudicatable, which was 100% of
# its remaining mechanical issues.
#
# The rows were never five things: one path is gone once. So they collapse to ONE row
# carrying `occurrences`, and the count stops being inflated by how many sessions happened
# to sit in the same directory. `occurrences` is deliberately OUTSIDE the fingerprint —
# a sixth session must not expire a ruling already made about that path.
#
# The ordinal handle is the second half, for the residual case dedupe cannot reach: the
# same ref reported with DIFFERENT details (a path recorded both as a file and as a
# worktree). `<check>:<ref>#<n>` names one, and it is tried only AFTER the whole ref has
# failed, so a ref that genuinely ends in `#2` still wins.
# ---------------------------------------------------------------------------

class TestIdenticalFindingsAreAdjudicable(_Base):
    GONE = "/Users/nobody/Workspace/gone-worktrees/wt"

    def _five_sessions_one_cwd(self):
        """The live shape: several sessions recorded the SAME vanished cwd."""
        t = self._task()
        t["session_meta"] = {"sess%d" % i: {"cwd": self.GONE} for i in range(5)}
        ts.save_task(t)
        return self._reload(t)

    def test_five_identical_rows_are_one_finding(self):
        t = self._five_sessions_one_cwd()
        rows = [f for f in heal.scan(t)["findings"] if f["check"] == "drift"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["occurrences"], 5)

    def test_the_collapsed_row_is_dismissable(self):
        t = self._five_sessions_one_cwd()
        result = heal.scan(t)
        entry, err = heal.dismiss(t, result["findings"], "drift:%s" % self.GONE,
                                  "the folder was renamed", sid="sess1234")
        self.assertIsNone(err)
        self.assertIsNotNone(entry)
        ts.save_task(t)                    # `dismiss` does not persist — the caller does
        self.assertEqual(heal.scan(self._reload(t))["findings"], [])

    def test_the_report_says_a_row_was_collapsed(self):
        """Folding five rows into one without saying so loses the fact. "one worktree is
        gone" and "five sessions all sat in it" are different things about one path."""
        t = self._five_sessions_one_cwd()
        lines = "\n".join(heal.scan_lines(heal.scan(t)))
        self.assertIn("recorded 5×", lines)

    def test_a_single_row_is_not_annotated(self):
        t = self._task(files=[self.GONE + "/only.py"])
        lines = "\n".join(heal.scan_lines(heal.scan(self._reload(t))))
        self.assertIn(self.GONE, lines)
        self.assertNotIn("×", lines)              # no multiplicity annotation at all

    def test_occurrences_is_not_in_the_fingerprint(self):
        f = {"check": "drift", "ref": "/x", "detail": "gone"}
        self.assertEqual(heal.finding_fingerprint(f),
                         heal.finding_fingerprint(dict(f, occurrences=7)))

    def test_a_single_row_still_reports_one_occurrence(self):
        t = self._task(files=[self.GONE + "/only.py"])
        rows = [f for f in heal.scan(self._reload(t))["findings"] if f["check"] == "drift"]
        self.assertEqual([r.get("occurrences") for r in rows], [1])

    def test_the_ordinal_handle_names_one_of_two_rows_sharing_a_ref(self):
        rows = [{"check": "drift", "ref": "/p", "detail": "recorded file no longer exists"},
                {"check": "drift", "ref": "/p", "detail": "recorded worktree is gone"}]
        first, err = heal._match_rows(rows, "drift", "/p#1", "finding")
        self.assertIsNone(err)
        self.assertEqual(first["detail"], "recorded file no longer exists")
        second, err = heal._match_rows(rows, "drift", "/p#2", "finding")
        self.assertIsNone(err)
        self.assertEqual(second["detail"], "recorded worktree is gone")

    def test_the_ambiguity_refusal_offers_the_handles(self):
        rows = [{"check": "drift", "ref": "/p", "detail": "one"},
                {"check": "drift", "ref": "/p", "detail": "two"}]
        row, err = heal._match_rows(rows, "drift", "/p", "finding")
        self.assertIsNone(row)
        self.assertIn("/p#1", err)
        self.assertIn("/p#2", err)

    def test_an_out_of_range_ordinal_is_refused_and_says_the_range(self):
        rows = [{"check": "drift", "ref": "/p", "detail": "one"},
                {"check": "drift", "ref": "/p", "detail": "two"}]
        row, err = heal._match_rows(rows, "drift", "/p#9", "finding")
        self.assertIsNone(row)
        self.assertIn("9", err)
        self.assertIn("2", err)

    def test_a_ref_that_really_ends_in_a_hash_number_wins_over_the_handle(self):
        # Link-rot refs are URLs and a URL fragment can be `#2`. The whole ref is tried
        # FIRST, so the real ref resolves and the handle never gets a look in.
        rows = [{"check": "link-rot", "ref": "https://x/y#2", "detail": "dead"},
                {"check": "link-rot", "ref": "https://x/y", "detail": "dead"}]
        row, err = heal._match_rows(rows, "link-rot", "https://x/y#2", "finding")
        self.assertIsNone(err)
        self.assertEqual(row["ref"], "https://x/y#2")
