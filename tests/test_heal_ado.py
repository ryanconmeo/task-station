"""heal_ado — reconciling a task against the WORK ITEMS it claims, not its own log.

WHAT THESE PIN, and why each is a case rather than a preference.

Two measured failures produced this module and both are reproduced here as fixtures
rather than described in prose, because a docstring cannot fail:

  * STORY 3614. 33 acceptance criteria; criteria 2, 23, 24 and 28 specify a per-ROW
    converging applier with a row ledger. Task 503's record described the story as
    "seeds out of chain" — one criterion out of 33 — and a relayed session built a
    file-level checksum ledger from scratch that the story already specified better.
    `ThirtyThreeCriteriaTwoAcknowledged` is that shape.

  * STORY 3070. An OPEN, unowned story inside Feature 3064 — task 506's own Feature —
    for 25 days, absent from 506's hand-maintained list because it was filed after
    the task was created. `TheFeatureIsAskedWhatItsChildrenAre` is that shape.

Every test runs against an injected prober. No network, no `az`, no subprocess.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))

import board.heal_ado as heal_ado


# --------------------------------------------------------------------------- fixtures
def _story(wid, title="A story", state="Active", ac="", desc="", parent=None,
           children=()):
    node = {"id": wid, "type": "User Story", "title": title, "state": state,
            "url": "https://dev.azure.com/o/_workitems/edit/%d" % wid,
            "children": [dict(c) for c in children], "prs": []}
    if ac:
        node["acceptance_criteria"] = ac
    if desc:
        node["description"] = desc
    tree = {"root": node}
    if parent:
        tree["parent"] = parent
    return tree


def _prober(trees):
    """An injected prober over a dict of `{(id, depth): tree}` or `{id: tree}`."""
    def probe(wid, depth=0):
        if (wid, depth) in trees:
            return trees[(wid, depth)]
        return trees.get(wid)
    return probe


def _task(stories=(), decisions=(), steps=(), **extra):
    t = {"id": "abc12345", "seq": 1, "title": "T",
         "stories": [{"url": "https://dev.azure.com/o/_workitems/edit/%d" % s[0],
                      "desc": s[1]} for s in stories],
         "decisions": list(decisions), "steps": list(steps)}
    t.update(extra)
    return t


THIRTY_THREE = "\n".join(
    "%d. %s" % (i, txt) for i, txt in enumerate([
        "packages test-data holds fixtures and scenarios in one file format per domain folder",
        "applying a fixture converges to the declared state a row named in the file is created "
        "or updated and a row the file no longer names is deleted",
        "on dev and test a fixture write succeeds for the deploy pipeline identity and is "
        "refused for any developer token",
    ], start=1))


# ------------------------------------------------------ the measure the module rests on
class CoverageIsAsymmetric(unittest.TestCase):
    """`heal.word_overlap` is Jaccard and systematically under-reports a short text
    inside a long one. That under-report is the exact direction that would hide a
    criterion inside a wall of decisions, so coverage divides by the CRITERION."""

    def test_short_inside_long_scores_full(self):
        crit = "the applier converges every row"
        long = ("A very long decision about many other things entirely, in which we "
                "also note that the applier converges every row it touches, plus "
                "twelve further paragraphs of unrelated material about pipelines.")
        self.assertEqual(heal_ado.coverage(crit, long), 1.0)

    def test_jaccard_would_have_missed_it(self):
        from board import heal
        crit = "the applier converges every row"
        long = ("A very long decision about many other things entirely, in which we "
                "also note that the applier converges every row it touches, plus "
                "twelve further paragraphs of unrelated material about pipelines.")
        self.assertLess(heal.word_overlap(crit, long), 0.35)

    def test_nothing_shared_is_zero(self):
        self.assertEqual(heal_ado.coverage("alpha beta gamma", "wholly other words"), 0.0)

    def test_empty_needle_is_zero_not_one(self):
        self.assertEqual(heal_ado.coverage("", "anything"), 0.0)


# ------------------------------------------------------------------- reading criteria
class CriteriaAreReadInBothShapes(unittest.TestCase):
    def test_numbered_keeps_the_storys_own_numbers(self):
        got = heal_ado.criteria_of({"acceptance_criteria": "1. alpha\n2. beta\n3. gamma"})
        self.assertEqual([c["n"] for c in got], [1, 2, 3])
        self.assertTrue(all(c["numbered"] for c in got))

    def test_a_list_starting_at_five_keeps_five(self):
        got = heal_ado.criteria_of({"acceptance_criteria": "5. alpha\n6. beta"})
        self.assertEqual([c["n"] for c in got], [5, 6])

    def test_bulleted_criteria_are_ordinals_and_say_so(self):
        """Several stories on this board bullet their criteria. ADO renders no number,
        so inventing "criterion 3" would send a reader after a label that is not
        there."""
        got = heal_ado.criteria_of({"acceptance_criteria": "- alpha\n- beta"})
        self.assertEqual([c["n"] for c in got], [1, 2])
        self.assertFalse(any(c["numbered"] for c in got))
        self.assertEqual(heal_ado.criterion_label(got[1]), "item 2")

    def test_numbered_label_reads_as_a_criterion(self):
        got = heal_ado.criteria_of({"acceptance_criteria": "23. alpha beta"})
        self.assertEqual(heal_ado.criterion_label(got[0]), "criterion 23")

    def test_continuation_lines_join_their_criterion(self):
        got = heal_ado.criteria_of(
            {"acceptance_criteria": "1. first part\n   second part\n2. next"})
        self.assertIn("second part", got[0]["text"])
        self.assertEqual(len(got), 2)

    def test_prose_with_no_enumeration_is_one_entry_not_zero(self):
        got = heal_ado.criteria_of({"acceptance_criteria": "just a paragraph of prose"})
        self.assertEqual(len(got), 1)
        self.assertFalse(got[0]["numbered"])

    def test_empty_field_is_no_criteria(self):
        self.assertEqual(heal_ado.criteria_of({}), [])


# --------------------------------------------------------------------- the 3614 shape
class ThirtyThreeCriteriaTwoAcknowledged(unittest.TestCase):
    """The failure this module was written for, reduced to its essentials: a record
    that describes a work item and a work item that specifies it."""

    def _run(self, decisions, desc="seeds out of chain"):
        task = _task(stories=[(3614, desc)], decisions=list(decisions))
        probe = _prober({3614: _story(3614, "Test data platform", ac=THIRTY_THREE)})
        return heal_ado.reconcile(task, probe=probe)

    def _padding(self, n):
        """Enough decisions to clear MIN_DECISIONS_TO_JUDGE without covering anything."""
        return ["Unrelated ruling number %d about pipeline scheduling windows" % i
                for i in range(n)]

    def test_untouched_criteria_are_reported_as_a_finding(self):
        r = self._run(self._padding(10))
        checks = {f["check"] for f in r["findings"]}
        self.assertIn("ado-criteria-unacknowledged", checks)

    def test_the_finding_says_how_many_of_how_many(self):
        r = self._run(self._padding(10))
        f = [x for x in r["findings"] if x["check"] == "ado-criteria-unacknowledged"][0]
        self.assertIn("of 3", f["detail"])
        self.assertEqual(f["ref"], "story 3614")

    def test_a_criterion_the_log_actually_states_is_acknowledged(self):
        r = self._run(self._padding(10) + [
            "Applying a fixture converges to the declared state: a row named in the "
            "file is created or updated, and a row the file no longer names is deleted."])
        bands = r["items"][0]["bands"]
        self.assertIn(2, [e["n"] for e in bands["acknowledged"]])

    def test_a_superseded_mention_does_not_acknowledge(self):
        """A criterion whose only mention was superseded is NOT acknowledged — that is
        the record saying it changed its mind, which is what a reconcile wants
        surfaced rather than absorbed."""
        text = ("Applying a fixture converges to the declared state: a row named in "
                "the file is created or updated, and a row the file no longer names "
                "is deleted.")
        live = self._run(self._padding(10) + [text])
        dead = self._run(self._padding(10) + [{"text": text, "superseded_by": 3}])
        self.assertIn(2, [e["n"] for e in live["items"][0]["bands"]["acknowledged"]])
        self.assertNotIn(2, [e["n"] for e in dead["items"][0]["bands"]["acknowledged"]])

    def test_a_thin_record_is_not_accused_of_ignoring_anything(self):
        """Attaching a story must not manufacture 33 findings on day one."""
        r = self._run(["only one decision so far"])
        checks = {f["check"] for f in r["findings"]}
        self.assertNotIn("ado-criteria-unacknowledged", checks)
        self.assertNotIn("ado-criteria-conflict", checks)

    def test_legacy_string_decisions_do_not_crash_the_pass(self):
        """A decision is stored as a bare STRING until something marks it. Reaching
        for `.get("text")` on the log crashes on the first legacy entry."""
        r = self._run(["a plain string decision"] * 10)
        self.assertTrue(r["ran"])


# ------------------------------------------------------------- the conflict band
class TheConflictBandIsACandidateNotAVerdict(unittest.TestCase):
    """Whether a decision CONTRADICTS a criterion or merely words it differently is a
    judgement. This layer is deterministic and zero-token by contract, so it puts the
    pair side by side and makes the skill rule on it."""

    AC = ("1. The applier converges the target row by row: a row named in the fixture "
          "file is created or updated and a row the file no longer names is deleted.")

    def _run(self, extra):
        task = _task(stories=[(1, "x")], decisions=[
            "Unrelated ruling %d about scheduling" % i for i in range(10)] + [extra])
        return heal_ado.reconcile(task, probe=_prober({1: _story(1, ac=self.AC)}))

    # The 3614 contradiction itself, in one sentence each: the criterion decides
    # convergence per ROW, the decision decides it per FILE. Coverage 0.46 — the log
    # is plainly in this territory and plainly not saying the same thing.
    CHECKSUM_LEDGER = ("Convergence is decided per FILE, not per row: the ledger "
                       "stores one checksum for each fixture file, and the applier "
                       "re-applies the whole file when that checksum differs. Rows "
                       "are created and updated wholesale.")

    def test_same_subject_different_mechanism_lands_in_conflict(self):
        bands = self._run(self.CHECKSUM_LEDGER)["items"][0]["bands"]
        self.assertEqual(len(bands["conflict"]), 1)
        self.assertEqual(bands["conflict"][0]["n"], 1)
        self.assertEqual(bands["acknowledged"], [])

    def test_the_finding_names_the_decision_to_judge_against(self):
        r = self._run(self.CHECKSUM_LEDGER)
        self.assertIsNotNone(r["items"][0]["bands"]["conflict"][0]["decision"])

    def test_it_never_claims_to_have_decided(self):
        r = self._run(self.CHECKSUM_LEDGER)
        f = [x for x in r["findings"] if x["check"] == "ado-criteria-conflict"][0]
        self.assertIn("Judge", f["detail"])
        self.assertNotIn("contradicts the criterion.", f["detail"])

    def test_a_decision_that_restates_the_criterion_is_acknowledged_not_conflict(self):
        """The band must not fire on agreement — a check that flags every decision
        touching a story is a check people learn to skip."""
        bands = self._run(
            "The applier converges the target row by row: a row named in the fixture "
            "file is created or updated, and a row the file no longer names is "
            "deleted.")["items"][0]["bands"]
        self.assertEqual(bands["conflict"], [])
        self.assertEqual(len(bands["acknowledged"]), 1)


# ------------------------------------------------------------- the lossy description
class TheRecordsOwnWordsAreCheckedAgainstTheSource(unittest.TestCase):
    """MEASURED on task 503: eleven of its stories carried the identical desc "WS4 —
    deterministic ADF naming; must land BEFORE any Datawiz regenerate", 3614 among
    them — a copy-paste covering NONE of the source's vocabulary, sitting exactly
    where a reader looks for what the story is."""

    def test_a_copy_pasted_description_is_a_finding(self):
        task = _task(stories=[(3614, "WS4 — deterministic ADF naming; must land "
                                     "BEFORE any Datawiz regenerate")])
        r = heal_ado.reconcile(task, probe=_prober({
            3614: _story(3614, "Test data platform: pipeline-owned fixtures and "
                               "per-run disposable data")}))
        f = [x for x in r["findings"] if x["check"] == "ado-summary-lossy"]
        self.assertEqual(len(f), 1)
        self.assertIn("0%", f[0]["detail"])

    def test_a_faithful_description_is_not_a_finding(self):
        task = _task(stories=[(1, "Test data platform: pipeline-owned fixtures")])
        r = heal_ado.reconcile(task, probe=_prober({
            1: _story(1, "Test data platform: pipeline-owned fixtures")}))
        self.assertEqual([x for x in r["findings"]
                          if x["check"] == "ado-summary-lossy"], [])

    def test_no_description_at_all_is_a_finding_when_the_source_is_substantial(self):
        """A one-line summary of a 33-criterion story is a POINTER, not a scope — and
        an empty line is not even a pointer."""
        task = _task(stories=[(1, "")])
        ac = "\n".join("%d. criterion body number %d here" % (i, i) for i in range(1, 9))
        r = heal_ado.reconcile(task, probe=_prober({1: _story(1, ac=ac)}))
        f = [x for x in r["findings"] if x["check"] == "ado-summary-lossy"]
        self.assertEqual(len(f), 1)
        self.assertIn("8 criteria", f[0]["detail"])

    def test_no_description_on_a_thin_work_item_is_left_alone(self):
        task = _task(stories=[(1, "")])
        r = heal_ado.reconcile(task, probe=_prober({1: _story(1, ac="1. one thing")}))
        self.assertEqual([x for x in r["findings"]
                          if x["check"] == "ado-summary-lossy"], [])


# ----------------------------------------------------------------- the 3070 shape
class TheFeatureIsAskedWhatItsChildrenAre(unittest.TestCase):
    """Story 3070 sat inside Feature 3064 — task 506's OWN Feature — for 25 days,
    unowned, because 506's list was assembled from the stories visible on the day it
    was created. Nothing ever asked the Feature what its children were."""

    def _trees(self, child_state="New", extra_children=()):
        feature = {"id": 3064, "type": "Feature", "title": "Standardized release "
                   "pipeline", "state": "Active", "url": "u", "children": [], "prs": []}
        kids = [{"id": 3070, "title": "Runtime environment configuration for the web "
                 "app", "state": child_state, "url": "u3070"}] + list(extra_children)
        return {(3202, 0): _story(3202, "Promotion", parent=feature),
                (3064, 1): {"root": dict(feature, children=kids)}}

    def test_an_open_unowned_child_is_found(self):
        task = _task(stories=[(3202, "Promotion pipeline")])
        r = heal_ado.reconcile(task, probe=_prober(self._trees()))
        f = [x for x in r["findings"] if x["check"] == "ado-sibling-missing"]
        self.assertEqual(len(f), 1)
        self.assertIn("3070", f[0]["detail"])
        self.assertEqual(f[0]["ref"], "feature 3064")

    def test_a_finished_child_is_not_an_omission(self):
        for state in ("Closed", "Done", "Removed", "Resolved", "Ready for UAT"):
            task = _task(stories=[(3202, "Promotion pipeline")])
            r = heal_ado.reconcile(task, probe=_prober(self._trees(child_state=state)))
            self.assertEqual([x for x in r["findings"]
                              if x["check"] == "ado-sibling-missing"], [],
                             "%s child should not be reported" % state)

    def test_a_child_another_task_owns_is_not_an_orphan(self):
        task = _task(stories=[(3202, "Promotion pipeline")])
        r = heal_ado.reconcile(task, probe=_prober(self._trees()),
                               owned_elsewhere={3070})
        self.assertEqual([x for x in r["findings"]
                          if x["check"] == "ado-sibling-missing"], [])

    def test_children_group_under_one_finding_per_feature(self):
        """Per-child findings put 34 rows on task 503 and buried the one that
        mattered."""
        extra = [{"id": 9001, "title": "Another", "state": "Active", "url": "u"},
                 {"id": 9002, "title": "More", "state": "Active", "url": "u"}]
        task = _task(stories=[(3202, "Promotion pipeline")])
        r = heal_ado.reconcile(task, probe=_prober(self._trees(extra_children=extra)))
        f = [x for x in r["findings"] if x["check"] == "ado-sibling-missing"]
        self.assertEqual(len(f), 1)
        for wid in ("3070", "9001", "9002"):
            self.assertIn(wid, f[0]["detail"])

    def test_a_child_the_task_already_claims_is_not_reported(self):
        task = _task(stories=[(3202, "Promotion pipeline"), (3070, "the runtime config")])
        trees = self._trees()
        trees[(3070, 0)] = _story(3070, "Runtime environment configuration")
        r = heal_ado.reconcile(task, probe=_prober(trees))
        self.assertEqual([x for x in r["findings"]
                          if x["check"] == "ado-sibling-missing"], [])


# ------------------------------------------------------ silence is never a clean result
class UnreadableIsAFindingNotASilence(unittest.TestCase):
    def test_a_work_item_that_will_not_read_is_reported(self):
        task = _task(stories=[(4234, "something")])
        r = heal_ado.reconcile(task, probe=_prober({}))
        f = [x for x in r["findings"] if x["check"] == "ado-unreachable"]
        self.assertEqual(len(f), 1)
        self.assertIn("unverified", f[0]["detail"])

    def test_no_prober_reports_that_it_did_not_run(self):
        """"the source was not read" and "the record agrees with the source" must
        never render the same. That distinction is the whole reason this exists."""
        task = _task(stories=[(1, "x"), (2, "y")])
        r = heal_ado.reconcile(task, probe=None)
        self.assertFalse(r["ran"])
        self.assertEqual(r["findings"], [])
        self.assertEqual(r["claimed"], 2)
        self.assertIn("--probe-ado", heal_ado.report(r))
        self.assertIn("unverified", heal_ado.report(r))

    def test_a_task_claiming_nothing_is_simply_clean(self):
        r = heal_ado.reconcile(_task(), probe=_prober({}))
        self.assertTrue(r["ran"])
        self.assertEqual(r["findings"], [])


# ------------------------------------------------------------------ scan integration
class ScanWiresItAndSaysWhenItDidNot(unittest.TestCase):
    def test_scan_without_a_prober_marks_the_five_checks_not_probed(self):
        """A row reading "Criteria no decision acknowledges … clean" on a scan that
        never opened a work item would be the same mistake in the REPORT that produced
        the incident."""
        from board import heal
        task = _task(stories=[(3614, "x")], decisions=["a"] * 10)
        lines = "\n".join(heal.scan_lines(heal.scan(task)))
        self.assertIn("not probed", lines)
        self.assertNotIn("Criteria no decision acknowledges clean", lines)

    def test_scan_with_a_prober_merges_the_findings(self):
        from board import heal
        task = _task(stories=[(3614, "WS4 — deterministic ADF naming")],
                     decisions=["Unrelated %d" % i for i in range(10)])
        probe = _prober({3614: _story(3614, "Test data platform", ac=THIRTY_THREE)})
        result = heal.scan(task, ado_probe=probe)
        checks = {f["check"] for f in result["findings"]}
        self.assertIn("ado-criteria-unacknowledged", checks)
        self.assertIn("ado-summary-lossy", checks)
        self.assertTrue(result["ado"]["ran"])

    def test_ado_findings_are_dismissible_like_any_other(self):
        """Registered in `heal.CHECKS` so they sort, title, dedupe and DISMISS through
        the same machinery. A second finding vocabulary would be a second thing to
        keep in step."""
        from board import heal
        for slug in ("ado-unreachable", "ado-summary-lossy",
                     "ado-criteria-unacknowledged", "ado-criteria-conflict",
                     "ado-sibling-missing"):
            self.assertIn(slug, heal.CHECK_ORDER)
            self.assertIn(slug, heal.CHECK_TITLES)

    def test_the_report_section_carries_the_criterion_text(self):
        """A count with no text under it cannot be ruled on."""
        from board import heal
        task = _task(stories=[(3614, "x")],
                     decisions=["Unrelated %d" % i for i in range(10)])
        probe = _prober({3614: _story(3614, "Test data platform", ac=THIRTY_THREE)})
        body = "\n".join(heal.ado_lines(heal.scan(task, ado_probe=probe)))
        self.assertIn("UNTOUCHED", body)
        self.assertIn("converges", body)


if __name__ == "__main__":
    unittest.main()
