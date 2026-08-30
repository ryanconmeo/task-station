"""DECISION OWNERSHIP — a ruling lives where it was typed and renders where it belongs.

THE MEASUREMENT THIS FEATURE ANSWERS. `heal` could merge and split but not MOVE, so every
ruling stayed on the task where a session happened to type it. On task #444 that meant
31,072 chars of one child's subject, 12,612 of a second's and 3,737 of a third's — 47,421
chars in all — sitting on the parent. A heal on 2026-08-29 split eight oversized decisions
and cut the longest entry from 8,095 to 3,581 chars and THE TOTAL BARELY MOVED, which is
the evidence that consolidation cannot fix this: the content is load-bearing, and it is in
the wrong PLACE.

SO OWNERSHIP MOVES AND THE DECISION DOES NOT. Every test here is a test of that sentence:
the entry stays on the source task, at its original index, with its text byte-identical,
and only which task RENDERS it changes.

THE INVARIANTS UNDER TEST THROUGHOUT:
  * ONE COPY. A reassign never duplicates text and never renumbers — the memo on #586 is
    explicit that the numbers are LOG indices, they SKIP where a decision was replaced,
    and a gap IS information.
  * THE STUB IS NOT OPTIONAL. A reassign that would leave none is refused; without it the
    verb is a delete with extra steps.
  * REVERSIBLE BY ONE COMMAND, and the command is printed at the moment of the write.
  * BOTH DIRECTIONS OR NEITHER for a cross-task supersession — one side alone is a
    contradiction that is invisible from exactly one side.
  * A CLOSE NEVER LEAVES A RULING COLD, and what it does NOT move it NAMES.

Isolation copies the `_repoint` idiom from tests/test_heal.py.
"""
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(_REPO_ROOT, "lib")
sys.path.insert(0, LIB)

_TMP_HOME = tempfile.mkdtemp(prefix="ts-own-")
os.environ["TASK_STATION_HOME"] = _TMP_HOME

import decisions as dec      # noqa: E402
import heal                  # noqa: E402
import ownership as own      # noqa: E402
import store                 # noqa: E402

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


RULING = ("Sync transport uses a J-track. The station gossips over the channel and the "
          "child owns the wire format.")


# ---------------------------------------------------------------- the element ---

class ElementShape(unittest.TestCase):
    """`decisions` owns the element keys. Back-compat is the hard rule here: hundreds of
    live tasks were written before any of this existed and must render identically."""

    def test_an_unowned_decision_stores_byte_identically_to_before(self):
        # The whole back-compat guarantee in one assertion: a decision that carries no
        # ownership is still a PLAIN STRING, so an older reader is untouched.
        entries = [RULING]
        self.assertEqual(dec.compact(dec.as_rich(entries[0])), RULING)
        self.assertIsNone(dec.owner(entries[0]))
        self.assertTrue(dec.renders_full(entries[0], "any-task"))

    def test_a_legacy_string_renders_in_full_everywhere(self):
        self.assertTrue(dec.renders_full("a legacy decision", "t1"))
        self.assertFalse(dec.is_owned("a legacy decision"))

    def test_set_owner_keeps_the_text_byte_identical(self):
        entries = [RULING]
        ok, err = dec.set_owner(entries, 1, "child-id", seq=532)
        self.assertTrue(ok, err)
        self.assertEqual(dec.text(entries[0]), RULING)   # ONE COPY, unchanged

    def test_the_holder_stops_rendering_it_and_the_owner_starts(self):
        entries = [RULING]
        dec.set_owner(entries, 1, "child-id", seq=532)
        self.assertFalse(dec.renders_full(entries[0], "parent-id"))
        self.assertTrue(dec.renders_full(entries[0], "child-id"))

    def test_a_stub_is_derived_from_the_first_sentence(self):
        entries = [RULING]
        dec.set_owner(entries, 1, "child-id", seq=532)
        self.assertEqual(dec.stub(entries[0]), "Sync transport uses a J-track")
        self.assertEqual(dec.owner_label(entries[0]), "#532")

    def test_a_short_single_sentence_ruling_stubs_to_itself(self):
        # DELIBERATE, not an accident: there is nothing to compress, and inventing a
        # shorter reference than the ruling itself would say less than the truth. The
        # reduction this feature exists for comes from the LONG entries.
        entries = ["never renumber a decision"]
        dec.set_owner(entries, 1, "c", seq=1)
        self.assertEqual(dec.stub(entries[0]), "never renumber a decision")

    def test_a_long_unbroken_ruling_is_clipped_on_a_word_boundary(self):
        body = "the quick brown fox jumps over the lazy dog " * 8
        line = dec.derive_stub(body)
        self.assertLessEqual(len(line), dec.STUB_CHARS + 1)   # +1 for the ellipsis
        self.assertTrue(line.endswith("\u2026"))
        self.assertFalse(line.rstrip("\u2026").endswith(" "))

    def test_an_explicit_stub_wins_over_the_derived_one(self):
        entries = [RULING]
        dec.set_owner(entries, 1, "c", seq=1, stub_text="the J-track wire format")
        self.assertEqual(dec.stub(entries[0]), "the J-track wire format")

    def test_a_decision_with_no_text_is_refused_because_no_stub_can_be_written(self):
        # THE STUB IS NOT OPTIONAL. Without it the reassign is a delete with extra steps.
        entries = [""]
        ok, err = dec.set_owner(entries, 1, "c", seq=1)
        self.assertFalse(ok)
        self.assertIn("delete with extra steps", err)

    def test_a_pinned_decision_cannot_be_reassigned(self):
        entries = [RULING]
        dec.set_pin(entries, 1, True)
        ok, err = dec.set_owner(entries, 1, "c", seq=1)
        self.assertFalse(ok)
        self.assertIn("PINNED", err)
        self.assertIn("--unpin-decision 1", err)

    def test_a_replaced_decision_cannot_be_reassigned(self):
        entries = [RULING, "the correction"]
        dec.mark_superseded(entries, 1, 2)
        ok, err = dec.set_owner(entries, 1, "c", seq=1)
        self.assertFalse(ok)
        self.assertIn("renders nowhere", err)

    def test_an_already_owned_decision_is_not_silently_repointed(self):
        entries = [RULING]
        dec.set_owner(entries, 1, "c1", seq=5)
        ok, err = dec.set_owner(entries, 1, "c2", seq=6)
        self.assertFalse(ok)
        self.assertIn("already owned by #5", err)
        self.assertIn("--unassign", err)

    def test_clear_owner_returns_it_and_drops_the_stub(self):
        entries = [RULING]
        dec.set_owner(entries, 1, "c", seq=5)
        ok, err = dec.clear_owner(entries, 1)
        self.assertTrue(ok, err)
        self.assertEqual(entries[0], RULING)             # compacted back to a plain str
        self.assertIsNone(dec.owner(entries[0]))

    def test_clearing_an_unowned_decision_errors_rather_than_reporting_success(self):
        entries = [RULING]
        ok, err = dec.clear_owner(entries, 1)
        self.assertFalse(ok)
        self.assertIn("nothing to bring back", err)


# ------------------------------------------------- cross-task supersession ---

class CrossTaskSupersession(unittest.TestCase):
    """A child ruling can refute a parent one. The pointer has to exist in BOTH
    directions or the contradiction is invisible from one side — and which side that is
    depends only on where the reader happens to be standing."""

    def test_a_bare_number_is_refused_because_numbers_are_per_task(self):
        entries = [RULING]
        ok, err = dec.mark_superseded_across(entries, 1, {"n": 4})
        self.assertFalse(ok)
        self.assertIn("per-task", err)

    def test_the_source_reads_as_replaced_and_names_the_other_task(self):
        entries = [RULING]
        dec.mark_superseded_across(entries, 1, {"task": "c", "seq": 532, "n": 14})
        self.assertTrue(dec.is_replaced(entries[0]))
        self.assertNotIn(entries[0], [e for _i, e in dec.live(entries)])
        self.assertEqual(dec.replacement_label(entries[0]),
                         "SUPERSEDED by #532 decision 14")

    def test_a_cross_task_target_never_collides_with_a_local_index(self):
        # Consumers test replacement targets for membership in a set of LOCAL indices.
        # A string target can only ever read as "not one of mine", which is true.
        entries = [RULING]
        dec.mark_superseded_across(entries, 1, {"task": "c", "seq": 1, "n": 1})
        _kind, targets = dec.replacement(entries[0])
        self.assertNotIn(1, targets)

    def test_superseding_across_clears_the_pin(self):
        entries = [RULING]
        dec.set_pin(entries, 1, True)
        dec.mark_superseded_across(entries, 1, {"task": "c", "seq": 2, "n": 1})
        self.assertFalse(dec.is_pinned(entries[0]))

    def test_the_refuter_records_what_it_refuted(self):
        entries = ["the correction"]
        dec.add_supersedes_across(entries, 1, {"task": "p", "seq": 444, "n": 30})
        self.assertEqual([dec.ref_label(r) for r in dec.supersedes_across(entries[0])],
                         ["#444 decision 30"])

    def test_recording_the_same_ref_twice_stores_it_once(self):
        entries = ["the correction"]
        ref = {"task": "p", "seq": 444, "n": 30}
        dec.add_supersedes_across(entries, 1, ref)
        dec.add_supersedes_across(entries, 1, ref)
        self.assertEqual(len(dec.supersedes_across(entries[0])), 1)

    def test_restore_clears_the_cross_task_mark(self):
        entries = [RULING]
        dec.mark_superseded_across(entries, 1, {"task": "c", "seq": 2, "n": 1})
        ok, err = dec.restore(entries, 1)
        self.assertTrue(ok, err)
        self.assertFalse(dec.is_replaced(entries[0]))

    def test_supersede_across_writes_both_sides_or_neither(self):
        src = {"id": "P", "seq": 444, "decisions": [RULING]}
        ref = {"id": "C", "seq": 532, "decisions": ["CBOR, not the line protocol"]}
        ok, err = own.supersede_across(src, 1, ref, 1)
        self.assertTrue(ok, err)
        self.assertTrue(dec.is_replaced(src["decisions"][0]))
        self.assertEqual(dec.supersedes_across(ref["decisions"][0])[0]["seq"], 444)

    def test_a_refusal_on_the_source_leaves_the_refuter_untouched(self):
        # An already-replaced source must not leave a back-pointer claiming a refutation
        # that never landed: the SOURCE is validated first, so a refusal changes nothing.
        src = {"id": "P", "seq": 444, "decisions": [RULING, "already replaced it"]}
        dec.mark_superseded(src["decisions"], 1, 2)
        ref = {"id": "C", "seq": 532, "decisions": ["CBOR"]}
        ok, err = own.supersede_across(src, 1, ref, 1)
        self.assertFalse(ok)
        self.assertIn("already superseded", err)
        self.assertEqual(dec.supersedes_across(ref["decisions"][0]), [])


# ------------------------------------------------------- the ownership index ---

class OwnerIndex(unittest.TestCase):
    """The index is DERIVED and the source entry is the FACT. Every read verifies one
    against the other, so the index can never invent a ruling."""

    def _pair(self):
        src = {"id": "P", "seq": 444, "decisions": [RULING]}
        owner = {"id": "C", "seq": 532}
        return src, owner

    def test_a_reassign_writes_the_pointer_and_the_owner_renders_in_full(self):
        src, owner = self._pair()
        ok, err = own.reassign(src, owner, 1)
        self.assertTrue(ok, err)
        rows = own.owned_elsewhere(owner, lambda i: src if i == "P" else None)
        self.assertEqual([r["ref"] for r in rows], ["444:1"])
        self.assertEqual(dec.text(rows[0]["entry"]), RULING)

    def test_the_holder_renders_a_stub_and_pays_no_loads_to_do_it(self):
        src, owner = self._pair()
        own.reassign(src, owner, 1)
        stubs = own.held_stubs(src)
        self.assertEqual(len(stubs), 1)
        line = own.stub_line(*stubs[0])
        self.assertIn("Sync transport uses a J-track", line)   # WHAT it is
        self.assertIn("owned by #532", line)                   # WHO owns it
        self.assertTrue(line.strip().startswith("1."))         # the number that addresses it

    def test_a_pointer_the_source_does_not_confirm_renders_nothing(self):
        _src, owner = self._pair()
        own.index_add(owner, {"id": "P", "seq": 444}, 7)
        other = {"id": "P", "seq": 444, "decisions": ["somebody else's ruling"]}
        self.assertEqual(own.owned_elsewhere(owner, lambda i: other), [])

    def test_a_replaced_ruling_is_dropped_from_the_owner_render(self):
        src, owner = self._pair()
        own.reassign(src, owner, 1)
        src["decisions"].append("the correction")
        dec.mark_superseded(src["decisions"], 1, 2)
        self.assertEqual(own.owned_elsewhere(owner, lambda i: src), [])

    def test_a_missing_source_task_renders_nothing_rather_than_raising(self):
        _src, owner = self._pair()
        own.index_add(owner, {"id": "GONE", "seq": 9}, 1)
        self.assertEqual(own.owned_elsewhere(owner, lambda i: None), [])

    def test_unassign_removes_the_pointer_and_the_stub_together(self):
        src, owner = self._pair()
        own.reassign(src, owner, 1)
        ok, err = own.unassign(src, owner, 1)
        self.assertTrue(ok, err)
        self.assertEqual(src["decisions"][0], RULING)
        self.assertEqual(own.index_entries(owner), [])

    def test_a_ruling_comes_home_even_when_its_owner_is_gone(self):
        src, owner = self._pair()
        own.reassign(src, owner, 1)
        ok, _err = own.unassign(src, None, 1)
        self.assertTrue(ok)
        self.assertEqual(src["decisions"][0], RULING)

    def test_a_task_cannot_reassign_a_ruling_to_itself(self):
        src, _owner = self._pair()
        ok, err = own.reassign(src, src, 1)
        self.assertFalse(ok)
        self.assertIn("cannot be reassigned to itself", err)

    def test_the_undo_command_names_the_indices_it_actually_touched(self):
        src, _owner = self._pair()
        self.assertEqual(own.undo_command(src, [3, 7]),
                         "heal --task 444 --unassign 3,7")

    def test_parse_ref_reads_both_forms(self):
        self.assertEqual(own.parse_ref("532:14"), ("532", 14))
        self.assertEqual(own.parse_ref("14"), (None, 14))
        self.assertEqual(own.parse_ref("nonsense"), (None, None))

    def test_inherited_pins_come_from_the_parent_and_are_not_the_childs(self):
        parent = {"id": "P", "seq": 444, "decisions": ["never renumber a decision"]}
        dec.set_pin(parent["decisions"], 1, True)
        child = {"id": "C", "seq": 532,
                 "related": [{"id": "P", "seq": 444, "kind": "parent"}]}
        rows = own.inherited_pins(child, lambda i: parent if i == "P" else None)
        self.assertEqual([r["ref"] for r in rows], ["444:1"])

    def test_a_pin_the_child_already_owns_is_not_shown_twice(self):
        parent = {"id": "P", "seq": 444, "decisions": ["a ruling"]}
        dec.set_pin(parent["decisions"], 1, True)
        parent["decisions"][0] = dict(parent["decisions"][0], owner="C")
        child = {"id": "C", "seq": 532,
                 "related": [{"id": "P", "seq": 444, "kind": "parent"}]}
        self.assertEqual(own.inherited_pins(child, lambda i: parent), [])


# ------------------------------------------------------- closing a child ---

class ClosingAChild(unittest.TestCase):
    """The inverse of collapse-to-reference. Without it a ruling goes COLD the instant
    its child closes — strictly worse than the problem ownership exists to fix."""

    def _family(self):
        parent = {"id": "P", "seq": 444, "decisions": ["a parent ruling"]}
        child = {"id": "C", "seq": 532, "decisions": [],
                 "related": [{"id": "P", "seq": 444, "kind": "parent"}]}
        return parent, child

    def test_a_ruling_the_child_owns_is_RELEASED_back_to_its_holder(self):
        parent, child = self._family()
        own.reassign(parent, child, 1)
        self.assertFalse(dec.renders_full(parent["decisions"][0], "P"))
        plan = own.close_plan(child, lambda i: parent if i == "P" else None)
        own.apply_close_plan(child, plan)
        self.assertTrue(dec.renders_full(parent["decisions"][0], "P"))

    def test_the_childs_own_PINNED_ruling_is_RE_HOMED_to_the_parent(self):
        parent, child = self._family()
        child["decisions"] = ["the wire format is CBOR"]
        dec.set_pin(child["decisions"], 1, True)
        plan = own.close_plan(child, lambda i: parent if i == "P" else None)
        own.apply_close_plan(child, plan)
        self.assertEqual(dec.owner(child["decisions"][0]), "P")
        rows = own.owned_elsewhere(parent, lambda i: child if i == "C" else None)
        self.assertEqual([dec.text(r["entry"]) for r in rows],
                         ["the wire format is CBOR"])

    def test_a_re_home_clears_the_pin_so_a_closed_child_cannot_reorder_the_parent(self):
        parent, child = self._family()
        child["decisions"] = ["the wire format is CBOR"]
        dec.set_pin(child["decisions"], 1, True)
        plan = own.close_plan(child, lambda i: parent if i == "P" else None)
        own.apply_close_plan(child, plan)
        self.assertFalse(dec.is_pinned(child["decisions"][0]))

    def test_the_undecidable_remainder_is_NAMED_rather_than_moved(self):
        parent, child = self._family()
        child["decisions"] = ["ran the fixture at 11am", "and again at noon"]
        plan = own.close_plan(child, lambda i: parent if i == "P" else None)
        self.assertEqual([i for i, _e in plan["reported"]], [1, 2])
        own.apply_close_plan(child, plan)
        # NOT moved: dumping a whole child log onto the parent in full is the exact
        # bloat this mechanism removes.
        self.assertIsNone(dec.owner(child["decisions"][0]))
        report = "\n".join(own.close_report_lines(child, plan))
        self.assertIn("were NOT re-homed: 1, 2", report)
        self.assertIn("not mechanically decidable", report)
        self.assertIn("--reassign", report)          # and one command moves any of them

    def test_a_child_with_no_parent_re_homes_nothing_and_says_so(self):
        child = {"id": "C", "seq": 532, "decisions": ["a ruling"]}
        dec.set_pin(child["decisions"], 1, True)
        plan = own.close_plan(child, lambda i: None)
        self.assertEqual(plan["rehomed"], [])
        self.assertEqual(len(plan["reported"]), 1)

    def test_a_replaced_ruling_is_not_re_homed(self):
        parent, child = self._family()
        child["decisions"] = ["wrong", "right"]
        dec.mark_superseded(child["decisions"], 1, 2)
        dec.set_pin(child["decisions"], 2, True)
        plan = own.close_plan(child, lambda i: parent if i == "P" else None)
        self.assertEqual([r["n"] for r in plan["rehomed"]], [2])


# ------------------------------------------------------------- the lint ---

class PlacementCheck(unittest.TestCase):
    """What turns the placement rule from etiquette into something reported."""

    def test_a_bare_hash_in_range_is_not_read_as_a_task(self):
        # `#2` on a task with four decisions is a decision reference, which is what a
        # decision log exists to contain.
        self.assertEqual(heal.task_citations("see #2 and #3", total=4), {})

    def test_an_out_of_range_hash_is_read_as_a_task(self):
        self.assertEqual(heal.task_citations("see #532", total=4), {532: 1})

    def test_a_word_qualified_hash_is_read_as_a_task_even_in_range(self):
        self.assertEqual(heal.task_citations("task #2 owns it", total=4), {2: 1})

    def test_a_memo_or_pr_number_is_neither(self):
        self.assertEqual(heal.task_citations("memo #3 and PR #12", total=99), {})

    def test_two_mentions_are_a_cross_reference_and_do_not_fire(self):
        task = {"id": "P", "seq": 444,
                "decisions": ["#532 asked for this and #532 got it"]}
        self.assertEqual(heal.placement(task), [])

    def test_three_mentions_of_one_task_and_no_other_is_a_finding(self):
        task = {"id": "P", "seq": 444,
                "decisions": ["#532 framing. #532 retries. #532 backoff."]}
        found = heal.placement(task)
        self.assertEqual([f["check"] for f in found], ["placement"])
        self.assertIn("names #532 3 times", found[0]["detail"])
        self.assertIn("heal --task 444 --reassign 1 --to 532", found[0]["detail"])

    def test_a_decision_naming_two_tasks_has_no_single_subject(self):
        task = {"id": "P", "seq": 444,
                "decisions": ["#532 x. #532 y. #532 z. #535 w."]}
        self.assertEqual(heal.placement(task), [])

    def test_a_ruling_already_owned_elsewhere_is_settled(self):
        task = {"id": "P", "seq": 444,
                "decisions": ["#532 framing. #532 retries. #532 backoff."]}
        dec.set_owner(task["decisions"], 1, "C", seq=532)
        self.assertEqual([f for f in heal.placement(task)
                          if "names #532" in f["detail"]], [])

    def test_an_owner_that_no_longer_exists_means_the_ruling_renders_nowhere(self):
        task = {"id": "P", "seq": 444, "decisions": [RULING]}
        dec.set_owner(task["decisions"], 1, "GONE", seq=99)
        found = heal.placement(task, load=lambda i: None)
        self.assertEqual(len(found), 1)
        self.assertIn("renders NOWHERE", found[0]["detail"])

    def test_an_owner_that_is_CLOSED_is_reported(self):
        task = {"id": "P", "seq": 444, "decisions": [RULING]}
        dec.set_owner(task["decisions"], 1, "C", seq=532)
        closed = {"id": "C", "seq": 532, "status": "closed"}
        found = heal.placement(task, load=lambda i: closed)
        self.assertIn("which is CLOSED", found[0]["detail"])

    def test_an_index_pointer_the_source_denies_is_reported(self):
        owner = {"id": "C", "seq": 532, "decisions": []}
        own.index_add(owner, {"id": "P", "seq": 444}, 1)
        src = {"id": "P", "seq": 444, "decisions": ["somebody else's ruling"]}
        found = heal.placement(owner, load=lambda i: src)
        self.assertIn("does not say so", found[0]["detail"])

    def test_a_pointer_at_a_RETIRED_ruling_is_spent_not_drifted(self):
        # Marking this a defect would fire on every correctly-retired ruling a task ever
        # owned, which is how a check teaches people to skip it.
        owner = {"id": "C", "seq": 532, "decisions": []}
        own.index_add(owner, {"id": "P", "seq": 444}, 1)
        src = {"id": "P", "seq": 444, "decisions": ["a ruling", "the correction"]}
        dec.mark_superseded(src["decisions"], 1, 2)
        self.assertEqual(heal.placement(owner, load=lambda i: src), [])

    def test_the_check_is_registered_so_a_clean_scan_proves_it_ran(self):
        self.assertIn("placement", heal.CHECK_ORDER)
        self.assertIn("placement", heal.CHECK_TITLES)

    def test_without_a_loader_it_reports_only_what_one_blob_proves(self):
        task = {"id": "P", "seq": 444, "decisions": [RULING]}
        dec.set_owner(task["decisions"], 1, "GONE", seq=99)
        self.assertEqual(heal.placement(task), [])       # no store reads at session start


# --------------------------------------------------------- the CLI, end to end ---

class TheVerbEndToEnd(unittest.TestCase):
    """The engine as a subprocess — the guards, the writes and the renders as a caller
    actually meets them."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._prev = os.environ.get("TASK_STATION_HOME")
        os.environ["TASK_STATION_HOME"] = self.tmp

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("TASK_STATION_HOME", None)
        else:
            os.environ["TASK_STATION_HOME"] = self._prev
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *args):
        eng = os.path.join(_REPO_ROOT, "lib", "task-station.py")
        return subprocess.run([sys.executable, eng] + list(args),
                              capture_output=True, text=True).stdout

    def _family(self):
        self._run("create", "--title", "PARENT", "--session", "sp")
        self._run("create", "--title", "CHILD", "--session", "sc")
        self._run("update", "--task", "2", "--session", "sc", "--parent", "1")
        self._run("update", "--task", "1", "--session", "sp", "--decision", RULING)

    def test_only_a_session_attached_to_the_source_may_reassign_out_of_it(self):
        self._family()
        out = self._run("heal", "--task", "1", "--session", "sc",
                        "--reassign", "1", "--to", "2")
        self.assertIn("not attached", out)
        self.assertIn("Nothing was changed", out)
        # and it really changed nothing
        self.assertIn(RULING, self._run("search", "--detail", "1"))

    def test_a_reassign_with_no_session_at_all_is_refused(self):
        self._family()
        out = self._run("heal", "--task", "1", "--reassign", "1", "--to", "2")
        self.assertIn("not attached", out)

    def test_the_reassign_prints_the_one_command_that_undoes_it(self):
        self._family()
        out = self._run("heal", "--task", "1", "--session", "sp",
                        "--reassign", "1", "--to", "2")
        self.assertIn("heal --task 1 --unassign 1", out)
        self.assertIn("did NOT move", out)

    def test_the_parent_renders_a_stub_and_the_child_renders_the_prose(self):
        self._family()
        self._run("heal", "--task", "1", "--session", "sp",
                  "--reassign", "1", "--to", "2")
        parent = self._run("search", "--detail", "1")
        child = self._run("search", "--detail", "2")
        self.assertIn("owned by #2", parent)
        self.assertNotIn("gossips over the channel", parent)   # prose left the render
        self.assertIn("gossips over the channel", child)       # and arrived here
        self.assertIn("1:1", child)                            # addressed across tasks

    def test_the_full_text_is_still_on_the_source_and_history_proves_it(self):
        self._family()
        self._run("heal", "--task", "1", "--session", "sp",
                  "--reassign", "1", "--to", "2")
        hist = self._run("history", "--task", "1")
        self.assertIn(RULING, hist)                 # ONE COPY, still here, in full
        self.assertIn("owned by #2", hist)

    def test_a_batch_with_one_bad_number_changes_nothing(self):
        self._family()
        self._run("update", "--task", "1", "--session", "sp", "--decision", "second")
        out = self._run("heal", "--task", "1", "--session", "sp",
                        "--reassign", "1,99", "--to", "2")
        self.assertIn("Nothing was changed", out)
        self.assertIn(RULING, self._run("search", "--detail", "1"))

    def test_one_stub_cannot_describe_several_rulings(self):
        self._family()
        self._run("update", "--task", "1", "--session", "sp", "--decision", "second")
        out = self._run("heal", "--task", "1", "--session", "sp", "--reassign", "1,2",
                        "--to", "2", "--stub", "one line")
        self.assertIn("names none of them", out)

    def test_unassign_brings_it_home_and_the_stub_goes(self):
        self._family()
        self._run("heal", "--task", "1", "--session", "sp",
                  "--reassign", "1", "--to", "2")
        out = self._run("heal", "--task", "1", "--session", "sp", "--unassign", "1")
        self.assertIn("back to #1", out)
        detail = self._run("search", "--detail", "1")
        self.assertIn("gossips over the channel", detail)
        self.assertNotIn("owned by #2", detail)

    def test_a_child_inherits_the_parents_pins(self):
        self._family()
        self._run("update", "--task", "1", "--session", "sp",
                  "--decision", "never renumber a decision", "--pin")
        child = self._run("search", "--detail", "2")
        self.assertIn("Inherited pins", child)
        self.assertIn("never renumber a decision", child)
        self.assertIn("NOT this task's to change", child)

    def test_a_cross_task_supersession_writes_both_directions(self):
        self._family()
        self._run("update", "--task", "2", "--session", "sc",
                  "--decision", "CBOR, not the line protocol", "--supersedes", "1:1")
        parent = self._run("history", "--task", "1")
        child = self._run("history", "--task", "2")
        self.assertIn("SUPERSEDED by #2 decision 1", parent)     # forward
        self.assertIn("supersedes #1 decision 1", child)         # and back
        self.assertNotIn(RULING, self._run("search", "--detail", "1"))

    def test_undoing_a_cross_task_supersession_clears_both_sides(self):
        self._family()
        self._run("update", "--task", "2", "--session", "sc",
                  "--decision", "CBOR", "--supersedes", "1:1")
        out = self._run("update", "--task", "1", "--session", "sp",
                        "--restore-decision", "1")
        self.assertIn("both sides", out)
        self.assertNotIn("supersedes #1 decision 1", self._run("history", "--task", "2"))
        self.assertIn(RULING, self._run("search", "--detail", "1"))

    def test_a_bare_supersedes_number_still_means_this_task(self):
        self._family()
        self._run("update", "--task", "1", "--session", "sp",
                  "--decision", "the correction", "--supersedes", "1")
        self.assertIn("SUPERSEDED by decision 2", self._run("history", "--task", "1"))

    def test_supersedes_naming_an_unknown_task_is_reported_not_swallowed(self):
        self._family()
        out = self._run("update", "--task", "1", "--session", "sp",
                        "--decision", "x", "--supersedes", "nosuchtask:1")
        self.assertIn("no task matching", out)

    def test_closing_the_child_releases_what_it_owned(self):
        self._family()
        self._run("heal", "--task", "1", "--session", "sp",
                  "--reassign", "1", "--to", "2")
        out = self._run("done", "--task", "2", "--session", "sp")
        self.assertIn("released 1 ruling(s)", out)
        self.assertIn("gossips over the channel", self._run("search", "--detail", "1"))

    def test_closing_a_child_re_homes_its_pinned_ruling_and_names_the_rest(self):
        self._family()
        self._run("update", "--task", "2", "--session", "sc",
                  "--decision", "the wire format is CBOR", "--pin")
        self._run("update", "--task", "2", "--session", "sc",
                  "--decision", "ran the fixture at 11am")
        out = self._run("done", "--task", "2", "--session", "sp")
        self.assertIn("re-homed decision(s) 1 to #1", out)
        self.assertIn("were NOT re-homed: 2", out)
        self.assertIn("the wire format is CBOR", self._run("search", "--detail", "1"))

    def test_reassign_refuses_to_ride_along_with_another_heal_mode(self):
        self._family()
        out = self._run("heal", "--task", "1", "--session", "sp", "--scan",
                        "--reassign", "1", "--to", "2")
        self.assertIn("run them separately", out)

    def test_an_ordinary_task_renders_exactly_as_it_did_before(self):
        # DATA-GATED: a task with no ownership record and no parent pays nothing and
        # prints nothing new. This is what keeps hundreds of live tasks untouched.
        self._run("create", "--title", "SOLO", "--session", "s1")
        self._run("update", "--task", "1", "--session", "s1", "--decision", "a ruling")
        detail = self._run("search", "--detail", "1")
        self.assertIn("a ruling", detail)
        self.assertNotIn("reference stubs", detail)
        self.assertNotIn("Inherited pins", detail)
        self.assertNotIn("this task OWNS", detail)


# ------------------------------------------- the exit condition's arithmetic ---

_judge_spec = importlib.util.spec_from_file_location(
    "prove_ownership_reduction",
    os.path.join(_REPO_ROOT, "scripts", "prove_ownership_reduction.py"))
judge = importlib.util.module_from_spec(_judge_spec)
_judge_spec.loader.exec_module(judge)


class TheReductionJudge(unittest.TestCase):
    """`scripts/prove_ownership_reduction.py` settles step 7's exit condition. It runs
    piped out of `origin/main`, which is what makes it un-fakeable from a branch — and
    which also means its ARITHMETIC has to be provable here, on constructed inputs,
    rather than only against whatever the live store happens to hold today.

    THE FLOOR ALONE WOULD REWARD THE WRONG MOVE: supersede forty rulings and the corpus
    drops. So the same judge asserts the record did not shrink to get there, and half of
    these tests are about that half."""

    def _task(self, texts, owned=(), pinned=(), superseded=()):
        t = {"id": "T", "seq": 444, "decisions": list(texts)}
        for n in owned:
            dec.set_owner(t["decisions"], n, "OTHER", seq=999)
        for n in pinned:
            dec.set_pin(t["decisions"], n, True)
        for n, by in superseded:
            dec.mark_superseded(t["decisions"], n, by)
        return t

    def test_a_stub_costs_its_own_line_and_not_the_prose(self):
        long_ruling = "x" * 3000
        before = judge.measure(self._task([long_ruling, "b"]), dec, own)
        after = judge.measure(self._task([long_ruling, "b"], owned=[1]), dec, own)
        self.assertEqual(before["corpus"], 3001)
        self.assertLess(after["corpus"], 200)          # the stub line, nothing more
        self.assertEqual(after["stubs"], 1)
        self.assertEqual(before["saved"], 0)           # nothing relocated, nothing saved

    def test_the_saving_is_the_prose_that_left_minus_the_stub_that_replaced_it(self):
        long_ruling = "x" * 3000
        m = judge.measure(self._task([long_ruling, "b"], owned=[1]), dec, own)
        self.assertEqual(m["relocated"], 3000)
        self.assertEqual(m["saved"], 3000 - m["stub_cost"])
        self.assertGreater(m["saved"], 2800)

    def test_a_saving_that_clears_the_floor_reads_REDUCED(self):
        m = {"corpus": 100000, "entries": 577, "stubs": 4, "full": 106, "live": 110,
             "relocated": 60000, "stub_cost": 400, "saved": 59600}
        saved, reduced, intact, _why = judge.verdict(m, 577, 45000)
        self.assertEqual(saved, 59600)
        self.assertTrue(reduced)
        self.assertTrue(intact)

    def test_a_saving_short_of_the_floor_does_not(self):
        m = {"corpus": 150000, "entries": 577, "stubs": 1, "full": 109, "live": 110,
             "relocated": 10000, "stub_cost": 100, "saved": 9900}
        _saved, reduced, _intact, _why = judge.verdict(m, 577, 45000)
        self.assertFalse(reduced)

    def test_a_reduction_by_RETIREMENT_relocates_nothing_and_never_reads_REDUCED(self):
        # 90,000 chars gone and every one of them by superseding a ruling. The corpus
        # collapsed; ownership saved nothing, so the token this condition expects never
        # appears. This is the move a snapshot floor would have rewarded.
        m = {"corpus": 70000, "entries": 577, "stubs": 0, "full": 70, "live": 70,
             "relocated": 0, "stub_cost": 0, "saved": 0}
        _saved, reduced, intact, why = judge.verdict(m, 577, 45000)
        self.assertFalse(reduced)
        self.assertFalse(intact)
        self.assertIn("no reference stub", " ".join(why))

    def test_a_shrinking_append_only_log_is_refused(self):
        m = {"corpus": 70000, "entries": 500, "stubs": 4, "full": 106, "live": 110,
             "relocated": 60000, "stub_cost": 400, "saved": 59600}
        _saved, _reduced, intact, why = judge.verdict(m, 577, 45000)
        self.assertFalse(intact)
        self.assertIn("SHRANK", " ".join(why))

    def test_a_task_that_GREW_still_reads_REDUCED_when_ownership_did_its_work(self):
        # The false red this replaced: #444 is live and keeps being written to, so a
        # snapshot floor went red on a perfectly good relocation pass.
        m = {"corpus": 180000, "entries": 640, "stubs": 12, "full": 130, "live": 142,
             "relocated": 60000, "stub_cost": 1200, "saved": 58800}
        _saved, reduced, intact, _why = judge.verdict(m, 577, 45000)
        self.assertTrue(reduced)
        self.assertTrue(intact)

    def test_ordinary_supersede_work_in_the_same_pass_is_not_read_as_gutting(self):
        # The other false red: the live count legitimately falls when a reconcile
        # supersedes or merges, and it was already off by one before the feature shipped.
        m = {"corpus": 90000, "entries": 590, "stubs": 9, "full": 88, "live": 97,
             "relocated": 60000, "stub_cost": 900, "saved": 59100}
        _saved, reduced, intact, why = judge.verdict(m, 577, 45000)
        self.assertTrue(reduced)
        self.assertTrue(intact, why)

    def test_rulings_owned_from_ELSEWHERE_cannot_manufacture_a_reduction(self):
        # They render here, so counting them would let a task "reduce" its digest by
        # taking on more of somebody else's prose.
        t = self._task(["a" * 500, "b" * 500])
        t["owned_decisions"] = [{"task": "P", "seq": 1, "n": 1}]
        m = judge.measure(t, dec, own)
        self.assertEqual(m["corpus"], 1000)
        self.assertEqual(m["saved"], 0)

    def test_the_judge_reads_its_vocabulary_from_origin_main_only(self):
        # No worktree fallback anywhere: an unknown repo yields no vocabulary, and the
        # caller gets OWNERSHIP-NOT-IN-MAIN rather than a measurement.
        self.assertIsNone(judge.load_vocabulary("/nonexistent/repo"))

    def test_the_store_path_carries_no_environment_branch(self):
        # A condition that can be pointed at a different store by exporting a variable
        # is a condition you can talk into passing.
        with open(os.path.join(_REPO_ROOT, "scripts",
                               "prove_ownership_reduction.py")) as fh:
            src = fh.read()
        self.assertNotIn("environ.get(\"TASK_STATION_HOME\")", src)
        self.assertNotIn("environ.get(\"CLAUDE_CONFIG_DIR\")", src)


if __name__ == "__main__":
    unittest.main()
