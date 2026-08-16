"""THE LOOP — wave planning over `depends-on`, the graded acceptance gate, and the
orchestrator guard.

WHAT THIS COVERS. The loop is the parent-runs-children machinery: compute what may start
now, spawn a child pre-attached to its own task, run the mechanical gate over what comes
back, grade it, accept or reject, repeat, halt cleanly at a human gate. The deterministic
half is `lib/loop.py` and it is what these tests are about — the judgment half is a skill
and cannot be unit-tested.

THE INVARIANTS, each one a way an autonomous loop goes wrong:

  1. A SETTLED PREDECESSOR RELEASES ITS DEPENDENTS, and "settled" means CLOSED or every
     exit condition MET. A task registering no conditions is never settled, so it cannot
     release work by having checked nothing.
  2. A CYCLE IS REPORTED, NEVER TRAVERSED. A planner that hangs on a stored cycle is
     worse than no planner; a zero-token scan must always terminate.
  3. A DANGLING DEPENDENCY DOES NOT DEADLOCK, AND IS NEVER SILENT. Nothing can settle a
     deleted task, so blocking on one is permanent — but releasing work whose stated
     prerequisite vanished, without saying so, is how a plan lies.
  4. ACCEPTANCE IS PER-DIMENSION AND COMPLETE. An average hides a failed dimension behind
     five strong ones; an ungraded dimension is not a pass.
  5. A PARKED CHILD IS NEVER RETRIED, and a human gate is a park.
  6. THE ORCHESTRATOR GUARD NAMES THE CHILD. A refusal that only refuses sends the reader
     to go read a rule; one that names the ready child makes the right thing the easy
     thing.

The graph tests build task dicts by hand — `loop` is pure, so they need no store at all,
which is exactly the property that makes the planner cheap enough to run constantly.
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(_REPO_ROOT, "lib")
sys.path.insert(0, LIB)

_TMP_HOME = tempfile.mkdtemp(prefix="ts-loop-")
os.environ["TASK_STATION_HOME"] = _TMP_HOME

import exits                  # noqa: E402
import loop                   # noqa: E402
import store                  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


def _t(tid, seq, title="T", status="open", deps=(), parent=None, conditions=None):
    """A hand-built task blob. `conditions` is `{step_n: met|unmet|None}` — the stored
    verdict a condition would have after a run, so a test can state the exit state it
    means without running anything."""
    related = [{"kind": "depends-on", "id": d, "seq": None} for d in deps]
    if parent:
        related.append({"kind": "parent", "id": parent, "seq": None})
    steps = []
    for n in sorted((conditions or {})):
        verdict = conditions[n]
        block = {"cmd": "check-%s" % n, "expect": ["OK"]}
        if verdict is not None:
            block["last"] = {"ts": 1000.0, "ok": verdict == "met", "status": "ran",
                             "missing": [] if verdict == "met" else ["OK"], "got": ""}
        steps.append({"text": "step %d" % n, "done": False, "exit": block})
    return {"id": tid, "seq": seq, "title": title, "status": status,
            "related": related, "steps": steps}


def _resolver(tasks):
    by_id = {t["id"]: t for t in tasks}
    return by_id.get


class _Args:
    def __init__(self, **kw):
        defaults = dict(session=None, task=None, all=False, run=False, as_json=False,
                        dim=None, threshold=None, note=None, park=None, why=None,
                        no_decision=False, from_ref=None, ask=None, role=None,
                        model=None, permission_mode=None, cwd=None,
                        print_command=True, verb=None)
        defaults.update(kw)
        self.__dict__.update(defaults)


# -- (1) the grade scale ---------------------------------------------------------

class GradeScaleTest(unittest.TestCase):
    def test_the_rubric_notes_unicode_minus_is_the_same_grade_as_a_hyphen(self):
        """The vault note is prose and writes `A−` (U+2212); the CLI is typed with a
        hyphen. A gate that rejected one of them would reject it for a reason having
        nothing to do with the work."""
        self.assertEqual(loop.normalize_grade("A−"), "A-")
        self.assertEqual(loop.normalize_grade("a-"), "A-")
        self.assertEqual(loop.normalize_grade(" b+ "), "B+")

    def test_a_non_grade_is_none_not_a_guess(self):
        for junk in ("", None, "excellent", "A++", "9"):
            self.assertIsNone(loop.normalize_grade(junk))

    def test_an_unrecognised_grade_never_meets_a_threshold(self):
        self.assertFalse(loop.meets("excellent", "A-"))
        self.assertFalse(loop.meets(None, "F"))

    def test_an_unrecognised_threshold_falls_back_to_the_default_not_to_anything_goes(self):
        """A typo'd threshold must never read as 'accept anything' — that is the one
        direction a broken gate must not fail."""
        self.assertTrue(loop.meets("A", "nonsense"))
        self.assertFalse(loop.meets("B", "nonsense"))

    def test_ordering_is_best_first(self):
        self.assertTrue(loop.meets("A", "B"))
        self.assertTrue(loop.meets("B", "B"))
        self.assertFalse(loop.meets("B-", "B"))


class ParseDimensionsTest(unittest.TestCase):
    def test_both_separators_and_lowercase_keys_work(self):
        dims, errors = loop.parse_dimensions(["G1=A", "g4:B+"])
        self.assertEqual(dims, {"G1": "A", "G4": "B+"})
        self.assertEqual(errors, [])

    def test_every_typo_is_collected_not_raised_on_the_first(self):
        _dims, errors = loop.parse_dimensions(["G9=A", "G1=amazing", "nonsense"])
        self.assertEqual(len(errors), 3)
        self.assertIn("not a rubric dimension", errors[0])
        self.assertIn("not a grade", errors[1])
        self.assertIn("expected 'G1=A-'", errors[2])


class VerdictTest(unittest.TestCase):
    def _full(self, **over):
        dims = {k: "A" for k in loop.DIMENSION_KEYS}
        dims.update(over)
        return dims

    def test_all_at_threshold_accepts(self):
        v = loop.verdict(self._full(), "A-")
        self.assertTrue(v["accepted"])
        self.assertEqual(v["failed"], [])

    def test_one_dimension_below_threshold_rejects_and_is_named(self):
        """Invariant 4: no averaging. Five A's do not carry a B."""
        v = loop.verdict(self._full(G4="B"), "A-")
        self.assertFalse(v["accepted"])
        self.assertEqual(v["failed"], [("G4", "B")])
        self.assertIn("G4 B", loop.verdict_line(v))

    def test_an_ungraded_dimension_is_not_a_pass(self):
        dims = self._full()
        dims.pop("G6")
        v = loop.verdict(dims, "A-")
        self.assertFalse(v["accepted"])
        self.assertEqual(v["missing"], ["G6"])
        self.assertIn("ungraded: G6", loop.verdict_line(v))

    def test_the_worst_grade_is_reported_on_acceptance_too(self):
        v = loop.verdict(self._full(G2="A-"), "A-")
        self.assertTrue(v["accepted"])
        self.assertEqual(v["worst"], "A-")


# -- (2) the grade ledger --------------------------------------------------------

class LedgerTest(unittest.TestCase):
    def test_a_grading_appends_and_counts_as_an_attempt(self):
        task = {}
        loop.record(task, {"G1": "A"}, "A-")
        loop.record(task, {"G1": "A"}, "A-")
        self.assertEqual(loop.attempts(task), 2)
        self.assertEqual(len(loop.grades(task)), 2)

    def test_a_park_is_not_an_attempt_and_is_never_an_acceptance(self):
        """A park is the loop declining to retry, not a try at the work — counting one
        would burn a retry the child never got."""
        task = {}
        entry, _v = loop.record(task, {k: "A" for k in loop.DIMENSION_KEYS}, "A-",
                                park="human-gate", note="Ryan rules on the predicate")
        self.assertFalse(entry["accepted"])
        self.assertEqual(loop.attempts(task), 0)
        self.assertEqual(loop.parked(task), "human-gate")

    def test_retries_left_never_goes_negative(self):
        task = {}
        for _ in range(5):
            loop.record(task, {"G1": "F"}, "A-")
        self.assertEqual(loop.retries_left(task, 2), 0)


# -- (3) waves over depends-on ---------------------------------------------------

class WaveTest(unittest.TestCase):
    def test_no_dependencies_is_wave_one(self):
        a = _t("a", 1)
        plan = loop.waves([a], _resolver([a]))
        self.assertEqual(plan["depth"]["a"], 1)

    def test_a_chain_deepens_one_wave_at_a_time(self):
        a, b, c = _t("a", 1), _t("b", 2, deps=["a"]), _t("c", 3, deps=["b"])
        plan = loop.waves([a, b, c], _resolver([a, b, c]))
        self.assertEqual([plan["depth"]["a"], plan["depth"]["b"], plan["depth"]["c"]],
                         [1, 2, 3])

    def test_a_closed_predecessor_does_not_deepen_the_wave(self):
        """Invariant 1. Waves measure what is still in the way; a finished predecessor
        is not in the way."""
        a = _t("a", 1, status="closed")
        b = _t("b", 2, deps=["a"])
        plan = loop.waves([a, b], _resolver([a, b]))
        self.assertEqual(plan["depth"]["b"], 1)

    def test_a_predecessor_with_every_exit_condition_met_also_releases(self):
        """The half that makes exit conditions worth having: a task can prove it is
        finished without anybody closing it."""
        a = _t("a", 1, conditions={1: "met", 2: "met"})
        b = _t("b", 2, deps=["a"])
        self.assertTrue(loop.settled(a))
        plan = loop.waves([a, b], _resolver([a, b]))
        self.assertEqual(plan["depth"]["b"], 1)

    def test_a_predecessor_registering_no_conditions_still_blocks(self):
        """The dangerous inverse — an empty checklist must not read as done."""
        a = _t("a", 1)
        b = _t("b", 2, deps=["a"])
        self.assertFalse(loop.settled(a))
        plan = loop.waves([a, b], _resolver([a, b]))
        self.assertEqual(plan["depth"]["b"], 2)

    def test_a_partially_instrumented_predecessor_still_blocks(self):
        """A predecessor that passed its one condition while seven steps answer nothing
        has not shown it is finished, so it must not release the wave behind it."""
        a = _t("a", 1, conditions={1: "met"})
        a["steps"].append({"text": "nothing checks this", "done": False})
        b = _t("b", 2, deps=["a"])
        self.assertFalse(loop.settled(a))
        plan = loop.waves([a, b], _resolver([a, b]))
        self.assertEqual(plan["depth"]["b"], 2)

    def test_one_unmet_condition_blocks_even_when_the_rest_passed(self):
        a = _t("a", 1, conditions={1: "met", 2: "unmet"})
        b = _t("b", 2, deps=["a"])
        plan = loop.waves([a, b], _resolver([a, b]))
        self.assertEqual(plan["depth"]["b"], 2)

    def test_a_dependency_outside_the_planned_set_still_blocks(self):
        """A wave planner that only looked inside its own sibling set would call a
        genuinely blocked child ready."""
        outsider = _t("x", 9)
        b = _t("b", 2, deps=["x"])
        plan = loop.waves([b], _resolver([b, outsider]))
        self.assertEqual(plan["depth"]["b"], 2)
        self.assertEqual([t["id"] for t in plan["blockers"]["b"]], ["x"])

    def test_a_cycle_is_reported_and_terminates(self):
        """Invariant 2 — the test that would hang if the traversal were naive."""
        a = _t("a", 1, deps=["b"])
        b = _t("b", 2, deps=["a"])
        plan = loop.waves([a, b], _resolver([a, b]))
        self.assertEqual(len(plan["cycles"]), 1)
        self.assertEqual(set(plan["cycles"][0]), {"a", "b"})
        self.assertNotIn("a", plan["depth"])
        self.assertNotIn("b", plan["depth"])

    def test_a_node_downstream_of_a_cycle_gets_no_wave_either(self):
        a = _t("a", 1, deps=["b"])
        b = _t("b", 2, deps=["a"])
        c = _t("c", 3, deps=["a"])
        plan = loop.waves([a, b, c], _resolver([a, b, c]))
        self.assertNotIn("c", plan["depth"])

    def test_the_same_cycle_found_twice_is_reported_once(self):
        a = _t("a", 1, deps=["b"])
        b = _t("b", 2, deps=["a"])
        plan = loop.waves([b, a], _resolver([a, b]))
        self.assertEqual(len(plan["cycles"]), 1)

    def test_a_dependency_on_a_deleted_task_is_reported_and_does_not_deadlock(self):
        """Invariant 3. Nothing can ever settle a deleted task, so blocking would be a
        permanent stall nobody can clear — but the vanished prerequisite is named."""
        b = _t("b", 2, deps=["ghost"])
        plan = loop.waves([b], _resolver([b]))
        self.assertEqual(plan["depth"]["b"], 1)
        self.assertEqual(plan["dangling"]["b"], ["ghost"])


# -- (3b) settled once a task has CHILDREN ---------------------------------------

class DeepSettledTest(unittest.TestCase):
    """The leaf rule is not enough the moment work moves to children — found the first
    time a track was decomposed. Task 531 finished its own five steps, retired the three
    that had become child tasks, and immediately read as *satisfied* while three children
    sat unbuilt. It would have released every dependent wave on the strength of work it
    had handed to somebody else."""

    def test_a_parent_with_an_unbuilt_child_is_not_settled(self):
        parent = _t("p", 1, conditions={1: "met"})
        kid = _t("k", 2, parent="p")
        self.assertTrue(loop.settled(parent))               # its OWN checklist is green
        deep = loop.settled_fn([parent, kid])
        self.assertFalse(deep(parent))                      # …and that is not enough

    def test_a_parent_becomes_settled_once_every_child_is(self):
        parent = _t("p", 1, conditions={1: "met"})
        kid = _t("k", 2, parent="p", conditions={1: "met"})
        self.assertTrue(loop.settled_fn([parent, kid])(parent))

    def test_closing_a_parent_still_wins_outright(self):
        """Closing is a human's assertion and it is allowed to end the argument —
        otherwise a task could never be closed while any child stayed open."""
        parent = _t("p", 1, status="closed")
        kid = _t("k", 2, parent="p")
        self.assertTrue(loop.settled_fn([parent, kid])(parent))

    def test_a_childless_task_is_unchanged(self):
        lone = _t("a", 1, conditions={1: "met"})
        self.assertEqual(loop.settled_fn([lone])(lone), loop.settled(lone))

    def test_the_rule_reaches_grandchildren(self):
        p = _t("p", 1, conditions={1: "met"})
        c = _t("c", 2, parent="p", conditions={1: "met"})
        g = _t("g", 3, parent="c")
        self.assertFalse(loop.settled_fn([p, c, g])(p))

    def test_a_parent_cycle_does_not_hang(self):
        a = _t("a", 1, parent="b", conditions={1: "met"})
        b = _t("b", 2, parent="a", conditions={1: "met"})
        self.assertIsInstance(loop.settled_fn([a, b])(a), bool)

    def test_an_unsettled_parent_still_blocks_its_dependents(self):
        p = _t("p", 1, conditions={1: "met"})
        kid = _t("k", 2, parent="p")
        dep = _t("d", 3, deps=["p"])
        plan = loop.waves([p, kid, dep], _resolver([p, kid, dep]),
                          is_settled=loop.settled_fn([p, kid, dep]))
        self.assertEqual(plan["depth"]["d"], 2)


class DescendantsTest(unittest.TestCase):
    def test_the_whole_subtree_comes_back_with_its_depth(self):
        root = _t("r", 1)
        c = _t("c", 2, parent="r")
        g = _t("g", 3, parent="c")
        got = loop.descendants(root, [root, c, g])
        self.assertEqual([(t["seq"], d) for t, d in got], [(2, 1), (3, 2)])

    def test_the_root_is_never_in_its_own_subtree(self):
        root = _t("r", 1)
        self.assertEqual(loop.descendants(root, [root]), [])

    def test_a_cycle_cannot_produce_an_infinite_walk(self):
        a = _t("a", 1, parent="b")
        b = _t("b", 2, parent="a")
        got = loop.descendants(a, [a, b])
        self.assertEqual([t["seq"] for t, _d in got], [2])


# -- (4) the scan report ---------------------------------------------------------

class ScanTest(unittest.TestCase):
    def test_no_nodes_reads_empty_not_complete(self):
        """An orchestrator with no children has a plan nobody has built — the opposite
        of a finished one, and reporting COMPLETE would say the work was done."""
        report = loop.scan([], _resolver([]))
        self.assertEqual(report["stop"], loop.EMPTY)

    def test_every_node_settled_reads_complete(self):
        a = _t("a", 1, status="closed")
        b = _t("b", 2, conditions={1: "met"})
        report = loop.scan([a, b], _resolver([a, b]))
        self.assertEqual(report["stop"], loop.COMPLETE)
        self.assertEqual(report["ready"], [])

    def test_unblocked_work_reads_ready_and_names_it(self):
        a = _t("a", 1, conditions={1: "unmet"})
        b = _t("b", 2, deps=["a"])
        report = loop.scan([a, b], _resolver([a, b]))
        self.assertEqual(report["stop"], loop.READY)
        self.assertEqual(report["ready"], [1])

    def test_unsettled_work_with_nothing_startable_reads_blocked(self):
        """`complete` and `blocked` are opposite situations that both look like 'nothing
        to do' from a distance, which is why the stopping condition is four values."""
        a = _t("a", 1, deps=["b"])
        b = _t("b", 2, deps=["a"])
        report = loop.scan([a, b], _resolver([a, b]))
        self.assertEqual(report["stop"], loop.BLOCKED)

    def test_the_report_names_nodes_that_can_never_report_themselves_done(self):
        a = _t("a", 1)
        b = _t("b", 2, conditions={1: "met"})
        report = loop.scan([a, b], _resolver([a, b]))
        self.assertEqual(report["exits"]["unregistered"], [1])
        self.assertEqual(report["exits"]["registered"], 1)

    def test_an_orchestrator_is_never_offered_as_ready(self):
        """It plans and grades; it holds no work. Offering it as the next thing to start
        would send somebody to the one task that refuses to do any — and `delegate run`
        would then refuse them, which is a loop with no exit."""
        orch = _t("o", 1, conditions={1: "unmet"})
        orch[loop.ORCHESTRATOR_FIELD] = True
        kid = _t("k", 2, parent="o")
        report = loop.scan([orch, kid], _resolver([orch, kid]))
        self.assertEqual(report["ready"], [2])
        row = [r for r in report["rows"] if r["seq"] == 1][0]
        self.assertTrue(row["orchestrator"])
        self.assertFalse(row["ready"])

    def test_a_grandchild_carries_its_distance_from_the_scanned_root(self):
        root, c, g = _t("r", 1), _t("c", 2, parent="r"), _t("g", 3, parent="c")
        every = [root, c, g]
        tree = loop.descendants(root, every)
        report = loop.scan([t for t, _d in tree], _resolver(every),
                           depths={t.get("id"): d for t, d in tree})
        depths = {r["seq"]: r["tree_depth"] for r in report["rows"]}
        self.assertEqual(depths, {2: 1, 3: 2})

    def test_a_parent_whose_child_is_unbuilt_keeps_the_scan_incomplete(self):
        p = _t("p", 1, conditions={1: "met"})
        kid = _t("k", 2, parent="p")
        report = loop.scan([p, kid], _resolver([p, kid]),
                           is_settled=loop.settled_fn([p, kid]))
        self.assertNotEqual(report["stop"], loop.COMPLETE)
        self.assertEqual(report["totals"]["settled"], 0)

    def test_a_blocked_row_names_what_holds_it(self):
        a = _t("a", 1, title="first")
        b = _t("b", 2, deps=["a"])
        report = loop.scan([a, b], _resolver([a, b]))
        row = [r for r in report["rows"] if r["seq"] == 2][0]
        self.assertFalse(row["ready"])
        self.assertEqual(row["blocked_by"], [{"seq": 1, "title": "first"}])


# -- (5) the orchestrator guard --------------------------------------------------

class OrchestratorGuardTest(unittest.TestCase):
    def test_an_unflagged_task_is_not_refused(self):
        a = _t("a", 1)
        self.assertIsNone(loop.orchestrator_refusal(a, [a]))

    def test_a_flagged_task_is_refused_and_names_the_ready_child(self):
        """Invariant 6 — the difference between a guard and an obstacle."""
        parent = _t("p", 444)
        parent[loop.ORCHESTRATOR_FIELD] = True
        child = _t("c", 531, parent="p", conditions={1: "unmet"})
        text = loop.orchestrator_refusal(parent, [parent, child])
        self.assertIn("orchestrator-only", text)
        self.assertIn("#531", text)
        self.assertIn("delegate run --seq 531", text)

    def test_a_flagged_task_with_no_children_says_there_is_nowhere_for_the_work_to_go(self):
        parent = _t("p", 444)
        parent[loop.ORCHESTRATOR_FIELD] = True
        text = loop.orchestrator_refusal(parent, [parent])
        self.assertIn("NO children", text)
        self.assertIn("--parent 444", text)

    def test_a_flagged_task_whose_children_are_all_blocked_points_at_the_scan(self):
        parent = _t("p", 444)
        parent[loop.ORCHESTRATOR_FIELD] = True
        a = _t("a", 1, parent="p", deps=["b"])
        b = _t("b", 2, parent="p", deps=["a"])
        text = loop.orchestrator_refusal(parent, [parent, a, b])
        self.assertIn("No child is ready", text)
        self.assertIn("scan --task 444", text)

    def test_the_refusal_always_says_how_to_override_it_deliberately(self):
        parent = _t("p", 444)
        parent[loop.ORCHESTRATOR_FIELD] = True
        text = loop.orchestrator_refusal(parent, [parent])
        self.assertIn("--orchestrator off", text)
        self.assertIn("--force", text)


class RoleTest(unittest.TestCase):
    def test_an_unknown_role_is_none_rather_than_a_default(self):
        """A typo'd role must be reported, never silently spawn a child with the wrong
        permissions."""
        self.assertIsNone(loop.role_spec("implementor"))

    def test_every_role_carries_a_model_and_a_permission_mode(self):
        for name in loop.ROLES:
            spec = loop.role_spec(name)
            self.assertTrue(spec["model"])
            self.assertTrue(spec["permission_mode"])

    def test_models_are_aliases_never_pinned_ids(self):
        """A pinned model id freezes one release into the store; an alias follows the
        current generation."""
        for name in loop.ROLES:
            self.assertNotIn("-", loop.role_spec(name)["model"])


# -- (6) the CLI surface ---------------------------------------------------------

# `cmd_update` reads a WIDE argparse namespace and touches several flags directly rather
# than through getattr, so a test namespace has to carry all of them. One place defines
# the defaults; `_update_args` overrides the two or three a test actually means.
_UPDATE_DEFAULTS = dict(
    task=None, title=None, summary=None, append_summary=None, restore_summary=None,
    state=None, goal=None, step_add=None, step_done=None, step_undone=None,
    step_supersede=None, step_restore=None, decision=None, supersedes=None, pin=False,
    pin_decision=None, unpin_decision=None, restore_decision=None, log=None, pr=None,
    pr_desc=None, story=None, story_desc=None, color=None, effort=None,
    trail_visibility=None, relate=None, depends_on=None, parent=None, absorbed_by=None,
    replaces=None, duplicates=None, unrelate=None, orchestrator=None, session=None,
)


def _update_args(**over):
    ns = dict(_UPDATE_DEFAULTS)
    ns.update(over)
    return _Args(**ns)


def _repoint(tmp):
    os.environ["TASK_STATION_HOME"] = tmp
    ts.DATA = tmp
    ts.STORE = os.path.join(tmp, "store")
    ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
    ts.LINKS_DIR = os.path.join(ts.STORE, "links")
    store.reset_cache()


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="loop-cli-")
        _repoint(self.tmp)

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _task(self, title, *step_texts):
        t = ts.new_task(title, "summary")
        t["steps"] = [{"text": s, "done": False} for s in step_texts]
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _out(self, fn, args):
        buf = io.StringIO()
        code = None
        with redirect_stdout(buf):
            try:
                fn(args)
            except SystemExit as exc:
                code = exc.code
        return buf.getvalue(), code

    def _full_dims(self, **over):
        dims = dict.fromkeys(loop.DIMENSION_KEYS, "A")
        dims.update(over)
        return ["%s=%s" % (k, v) for k, v in dims.items()]

    def test_grade_accepts_at_the_configured_threshold_and_exits_zero(self):
        t = self._task("child")
        out, code = self._out(ts.cmd_grade, _Args(task=str(t["seq"]),
                                                  dim=self._full_dims()))
        self.assertIsNone(code)
        self.assertIn("ACCEPTED", out)

    def test_grade_rejects_and_exits_one_while_retries_remain(self):
        t = self._task("child")
        out, code = self._out(ts.cmd_grade, _Args(task=str(t["seq"]),
                                                  dim=self._full_dims(G4="B")))
        self.assertEqual(code, 1)
        self.assertIn("REJECTED", out)
        self.assertIn("G4", out)

    def test_grade_exits_three_once_the_retry_budget_is_spent(self):
        """A distinct code so a driver can branch to PARK without parsing prose."""
        t = self._task("child")
        for _ in range(2):
            self._out(ts.cmd_grade, _Args(task=str(t["seq"]),
                                          dim=self._full_dims(G4="B")))
        _out, code = self._out(ts.cmd_grade, _Args(task=str(t["seq"]),
                                                   dim=self._full_dims(G4="B")))
        self.assertEqual(code, 3)

    def test_a_park_exits_four_and_needs_a_reason(self):
        t = self._task("child")
        out, code = self._out(ts.cmd_grade, _Args(task=str(t["seq"]),
                                                  park="human-gate"))
        self.assertEqual(code, 2)
        self.assertIn("--why", out)
        out, code = self._out(ts.cmd_grade, _Args(task=str(t["seq"]), park="human-gate",
                                                  why="Ryan rules on the predicate"))
        self.assertEqual(code, 4)
        self.assertIn("NEVER retried", out)

    def test_an_invented_park_reason_is_refused(self):
        t = self._task("child")
        out, code = self._out(ts.cmd_grade, _Args(task=str(t["seq"]), park="meh",
                                                  why="because"))
        self.assertEqual(code, 2)
        self.assertIn("human-gate", out)

    def test_grading_nothing_prints_the_rubric_instead_of_a_usage_dump(self):
        t = self._task("child")
        out, code = self._out(ts.cmd_grade, _Args(task=str(t["seq"])))
        self.assertEqual(code, 2)
        self.assertIn("G1", out)
        self.assertIn("Gate integrity", out)

    def test_the_grade_lands_on_the_task_as_a_decision(self):
        """The rubric is the same object at both scales: per-child grades accumulate into
        exactly the per-phase table the vault note carries."""
        t = self._task("child")
        self._out(ts.cmd_grade, _Args(task=str(t["seq"]), dim=self._full_dims(),
                                      note="clean gate"))
        again = ts.load_task(t["id"])
        self.assertEqual(len(loop.grades(again)), 1)
        self.assertTrue(any("ACCEPTED" in str(d) for d in again.get("decisions") or []))

    def test_no_decision_records_the_grade_without_touching_the_decision_log(self):
        t = self._task("child")
        self._out(ts.cmd_grade, _Args(task=str(t["seq"]), dim=self._full_dims(),
                                      no_decision=True))
        again = ts.load_task(t["id"])
        self.assertEqual(len(loop.grades(again)), 1)
        self.assertEqual(again.get("decisions") or [], [])

    def test_scan_of_a_childless_task_falls_back_to_the_task_itself(self):
        """`scan --task <leaf>` answering 'here is your own exit state' is useful; an
        empty report would read as a bug."""
        t = self._task("lonely", "do a thing")
        out, _ = self._out(ts.cmd_scan, _Args(task=str(t["seq"])))
        self.assertIn("1 node(s)", out)

    def test_scan_json_and_text_render_the_same_computation(self):
        parent = self._task("parent")
        child = self._task("child", "do a thing")
        self._out(ts.cmd_update, _update_args(task=str(child["seq"]),
                                             parent=str(parent["seq"])))
        out, _ = self._out(ts.cmd_scan, _Args(task=str(parent["seq"]), as_json=True))
        report = json.loads(out)
        self.assertEqual(report["stop"], loop.READY)
        self.assertEqual(report["ready"], [child["seq"]])
        text, _ = self._out(ts.cmd_scan, _Args(task=str(parent["seq"])))
        self.assertIn("STOP: READY", text)
        self.assertIn("#%s" % child["seq"], text)

    def test_invoke_pre_attaches_a_fresh_session_to_the_child(self):
        """B10 — the child reads its own context from its own record, so the ask carries
        the request only. This is the mechanical half of that claim."""
        parent = self._task("parent")
        child = self._task("child", "do a thing")
        out, code = self._out(ts.cmd_invoke,
                              _Args(task=str(child["seq"]), from_ref=str(parent["seq"]),
                                    ask="Land the port."))
        self.assertIsNone(code)
        self.assertIn("--session-id", out)
        again = ts.load_task(child["id"])
        sid = (again.get("sessions") or [None])[0]
        self.assertTrue(sid)
        self.assertEqual(ts.get_link(sid), child["id"])

    def test_invoke_applies_the_role_table(self):
        parent = self._task("parent")
        child = self._task("child")
        out, _ = self._out(ts.cmd_invoke,
                           _Args(task=str(child["seq"]), from_ref=str(parent["seq"]),
                                 ask="Read the tree.", role="scout"))
        self.assertIn("--model sonnet", out)
        self.assertIn("--permission-mode plan", out)

    def test_invoke_refuses_an_unknown_role(self):
        parent = self._task("parent")
        child = self._task("child")
        out, code = self._out(ts.cmd_invoke,
                              _Args(task=str(child["seq"]), from_ref=str(parent["seq"]),
                                    ask="x", role="implementor"))
        self.assertEqual(code, 2)
        self.assertIn("not a role", out)

    def test_invoke_refuses_to_invoke_itself(self):
        t = self._task("solo")
        out, code = self._out(ts.cmd_invoke, _Args(task=str(t["seq"]),
                                                   from_ref=str(t["seq"]), ask="x"))
        self.assertEqual(code, 2)
        self.assertIn("cannot invoke itself", out)

    def test_invoke_needs_an_ask(self):
        t = self._task("child")
        out, code = self._out(ts.cmd_invoke, _Args(task=str(t["seq"]), ask="   "))
        self.assertEqual(code, 2)
        self.assertIn("--ask", out)

    def test_a_long_ask_warns_that_it_is_context_and_still_runs(self):
        """A warning, never a refusal: a legitimate request can be long, and refusing one
        would be this command inventing a rule nobody agreed to."""
        parent = self._task("parent")
        child = self._task("child")
        out, code = self._out(ts.cmd_invoke,
                              _Args(task=str(child["seq"]), from_ref=str(parent["seq"]),
                                    ask="x" * (ts.ASK_CONTEXT_HINT + 1)))
        self.assertIsNone(code)
        self.assertIn("usually context, not a request", out)

    def test_invoking_a_non_child_warns_about_the_roll_up(self):
        parent = self._task("parent")
        stranger = self._task("stranger")
        out, _ = self._out(ts.cmd_invoke,
                           _Args(task=str(stranger["seq"]), from_ref=str(parent["seq"]),
                                 ask="do it"))
        self.assertIn("is not a child of", out)

    def test_orchestrator_check_is_silent_and_zero_when_delegation_is_allowed(self):
        t = self._task("ordinary")
        out, code = self._out(ts.cmd_orchestrator_check, _Args(task=str(t["seq"])))
        self.assertIsNone(code)
        self.assertEqual(out.strip(), "")

    def test_orchestrator_check_exits_three_with_the_refusal(self):
        """delegate.py branches on exactly this code — a broken check must be
        distinguishable from a refusal, or a failure to run would block every
        delegation."""
        parent = self._task("parent")
        self._out(ts.cmd_update, _update_args(task=str(parent["seq"]),
                                             orchestrator="on"))
        out, code = self._out(ts.cmd_orchestrator_check, _Args(task=str(parent["seq"])))
        self.assertEqual(code, 3)
        self.assertIn("orchestrator-only", out)

    def test_the_flag_can_be_cleared_again(self):
        parent = self._task("parent")
        self._out(ts.cmd_update, _update_args(task=str(parent["seq"]),
                                             orchestrator="on"))
        self._out(ts.cmd_update, _update_args(task=str(parent["seq"]),
                                             orchestrator="off"))
        self.assertFalse(loop.is_orchestrator(ts.load_task(parent["id"])))


# -- (7) the orchestrator recap ---------------------------------------------------

class RecapTest(CliTest):
    """Opening an orchestrator must REPORT its children's computed state, not make you
    know to ask for it. A record that has the answer and does not volunteer it is one
    somebody has to remember to interrogate — and the thing nobody remembers is exactly
    the thing that goes stale."""

    def _family(self):
        parent = self._task("The orchestrator")
        a = self._task("Child A", "do a thing")
        b = self._task("Child B", "do another")
        self._out(ts.cmd_update, _update_args(task=str(a["seq"]), parent=str(parent["seq"])))
        self._out(ts.cmd_update, _update_args(task=str(b["seq"]), parent=str(parent["seq"])))
        self._out(ts.cmd_update, _update_args(task=str(b["seq"]),
                                              depends_on=[str(a["seq"])]))
        return ts.load_task(parent["id"]), a, b

    def test_a_leaf_task_renders_exactly_as_before(self):
        """Parity: a task with no children must gain nothing, or every existing digest
        grows a section about a plan that does not exist."""
        lone = self._task("No children here")
        self.assertEqual(ts._children_recap(lone), [])

    def test_a_parent_reports_its_waves_and_what_is_ready(self):
        parent, a, b = self._family()
        block = "\n".join(ts._children_recap(parent))
        self.assertIn("Children (2)", block)
        self.assertIn("STOP: READY", block)
        self.assertIn("READY NOW: #%s" % a["seq"], block)
        self.assertIn("blocked by #%s" % a["seq"], block)

    def test_it_names_children_that_cannot_report_themselves_done(self):
        parent, _a, _b = self._family()
        self.assertIn("register NO exit condition", "\n".join(ts._children_recap(parent)))

    def test_a_closed_parent_gets_no_plan_block(self):
        """Its plan is history, not a next step."""
        parent, _a, _b = self._family()
        parent["status"] = "closed"
        ts.save_task(parent)
        self.assertEqual(ts._children_recap(ts.load_task(parent["id"])), [])

    def test_the_block_reaches_the_rendered_detail(self):
        parent, _a, _b = self._family()
        detail = ts._format_detail(ts.load_task(parent["id"]), None)
        self.assertIn("the computed plan", detail)

    def test_a_broken_scan_never_takes_the_recap_down(self):
        """A resume digest must never fail because a derived section raised."""
        parent, _a, _b = self._family()
        real = loop.descendants
        try:
            loop.descendants = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
            self.assertEqual(ts._children_recap(parent), [])
        finally:
            loop.descendants = real


class RoleNameTest(unittest.TestCase):
    def test_the_grading_role_is_named_grader(self):
        self.assertIn("grader", loop.ROLES)
        self.assertNotIn("judge", loop.ROLES)


# -- (8) liveness, the upward report, and decompose --------------------------------

class LivenessTest(unittest.TestCase):
    """A planner that keeps offering work already in flight is how the same child gets
    invoked twice — and a wave whose children are ALL running used to report `blocked`,
    which reads as "intervene" when the honest answer is "the loop is working, wait"."""

    def test_a_running_node_is_not_offered_as_ready(self):
        a = _t("a", 1)
        report = loop.scan([a], _resolver([a]), live={1})
        self.assertEqual(report["ready"], [])
        self.assertEqual(report["running"], [1])

    def test_everything_running_reads_working_not_blocked(self):
        a = _t("a", 1)
        self.assertEqual(loop.scan([a], _resolver([a]), live={1})["stop"], loop.WORKING)
        self.assertEqual(loop.scan([a], _resolver([a]))["stop"], loop.READY)

    def test_a_blocked_wave_with_nothing_running_still_reads_blocked(self):
        a = _t("a", 1, deps=["b"])
        b = _t("b", 2, deps=["a"])
        self.assertEqual(loop.scan([a, b], _resolver([a, b]), live=set())["stop"],
                         loop.BLOCKED)

    def test_no_live_information_changes_nothing(self):
        """Fail-open: the scan must answer with this column missing, not refuse."""
        a = _t("a", 1)
        self.assertEqual(loop.scan([a], _resolver([a]), live=None)["ready"], [1])

    def test_settled_still_beats_running(self):
        a = _t("a", 1, status="closed")
        row = [r for r in loop.scan([a], _resolver([a]), live={1})["rows"]][0]
        self.assertTrue(row["settled"])
        self.assertFalse(row["ready"])


class ParkReasonTest(unittest.TestCase):
    def test_too_large_is_a_park_reason(self):
        """The gate is where the judgement already is — cheaper than a heuristic on
        effort or step count, which fires on plenty of tasks that are merely detailed."""
        self.assertIn("too-large", loop.PARK_REASONS)


class DecomposeTest(CliTest):
    def _args(self, **over):
        ns = dict(task=None, session=None, into=None, chain=False, add=False)
        ns.update(over)
        return _Args(**ns)

    def test_it_creates_children_parents_them_and_flags_the_orchestrator(self):
        t = self._task("Big work")
        out, code = self._out(ts.cmd_decompose,
                              self._args(task=str(t["seq"]), into=["One", "Two"]))
        self.assertIsNone(code)
        parent = ts.load_task(t["id"])
        self.assertTrue(loop.is_orchestrator(parent))
        kids = loop.children(parent, ts.all_tasks())
        self.assertEqual([k["title"] for k in kids], ["One", "Two"])
        self.assertIn("ORCHESTRATOR-ONLY", out)

    def test_chain_orders_them_one_wave_at_a_time(self):
        t = self._task("Big work")
        self._out(ts.cmd_decompose,
                  self._args(task=str(t["seq"]), into=["One", "Two", "Three"], chain=True))
        parent = ts.load_task(t["id"])
        kids = loop.children(parent, ts.all_tasks())
        self.assertEqual(loop.dependencies(kids[0]), [])
        self.assertEqual(loop.dependencies(kids[1]), [kids[0]["id"]])
        self.assertEqual(loop.dependencies(kids[2]), [kids[1]["id"]])

    def test_decomposing_twice_is_refused_unless_asked_for(self):
        """Quiet when it goes wrong: the scan simply starts reporting duplicated work."""
        t = self._task("Big work")
        self._out(ts.cmd_decompose, self._args(task=str(t["seq"]), into=["One"]))
        out, code = self._out(ts.cmd_decompose, self._args(task=str(t["seq"]), into=["Two"]))
        self.assertEqual(code, 2)
        self.assertIn("already has", out)
        out, code = self._out(ts.cmd_decompose,
                              self._args(task=str(t["seq"]), into=["Two"], add=True))
        self.assertIsNone(code)
        self.assertEqual(len(loop.children(ts.load_task(t["id"]), ts.all_tasks())), 2)

    def test_it_refuses_with_nothing_to_decompose_into(self):
        t = self._task("Big work")
        out, code = self._out(ts.cmd_decompose, self._args(task=str(t["seq"])))
        self.assertEqual(code, 2)
        self.assertIn("--into", out)


class UpwardReportTest(CliTest):
    def _child_of(self, parent):
        c = self._task("The child", "do a thing")
        self._out(ts.cmd_update, _update_args(task=str(c["seq"]),
                                              parent=str(parent["seq"])))
        return ts.load_task(c["id"])

    def test_closing_a_child_tells_its_parent(self):
        """The push that replaces 'run a scan and find out'."""
        parent = self._task("The orchestrator")
        child = self._child_of(parent)
        seq = ts.report_to_parent(child, "CLOSED — ready for the gate")
        self.assertEqual(seq, parent["seq"])
        memos = ts.load_task(parent["id"]).get("memos") or []
        self.assertEqual(len(memos), 1)
        self.assertIn("CHILD #%s" % child["seq"], memos[0]["text"])

    def test_a_task_with_no_parent_reports_nothing(self):
        lone = self._task("No parent")
        self.assertIsNone(ts.report_to_parent(lone, "done"))

    def test_a_closed_parent_is_not_told(self):
        """Its plan is history; a memo there would nag a record nobody is working."""
        parent = self._task("The orchestrator")
        child = self._child_of(parent)
        parent["status"] = "closed"
        ts.save_task(parent)
        self.assertIsNone(ts.report_to_parent(child, "done"))

    def test_the_report_never_breaks_the_verb_that_called_it(self):
        parent = self._task("The orchestrator")
        child = self._child_of(parent)
        # Patch the EXACT namespace the call resolves in — the function's own globals.
        # Neither the facade's star-imported copy nor `sys.modules["board.memos"]` is
        # right: the facade purges and re-imports its seams per copy, so the module
        # under that key can be a different generation from the one this `ts` is bound
        # to, and `__module__` is only a string. `__globals__` is the binding itself.
        glb = ts.report_to_parent.__globals__
        real = glb["memo_send"]
        try:
            glb["memo_send"] = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
            self.assertIsNone(ts.report_to_parent(child, "done"))
        finally:
            glb["memo_send"] = real


if __name__ == "__main__":
    unittest.main()
