"""EXIT CONDITIONS — a plan item that settles itself, so DONE is computed not asserted.

WHAT THIS COVERS AND WHY IT MATTERS. `claims` proved that a registered command keeps an
assertion honest: seventeen of them stayed true for a year because something ran them. In
the same document, thirteen prose STEPS silently became true and nobody noticed for
weeks. `heal` structurally cannot catch that — it reconciles the record against itself,
never against reality, and a step reading "Phase 4 brain port" declares nothing to
reconcile.

So the tests here are ultimately about four invariants, each of which is a way this
mechanism could be worse than not existing:

  1. A CONDITION THAT ASSERTS NOTHING IS REFUSED. One with no expected substring passes
     forever whatever the command prints — green while proving nothing.
  2. A CONDITION THAT DID NOT RUN REFUTES NOTHING. A timeout or a missing binary is
     `unknown`, never `unmet`, and must move no tick in either direction. (The checker's
     "uncountable is never zero" rule, one layer down.)
  3. AN EMPTY REGISTRATION IS NEVER SATISFIED. `state` is `none`, not `met` — otherwise
     a task with no conditions would release every dependent wave by having checked
     nothing, which is the green-board-with-nothing-behind-it failure.
  4. TICKING IS AUTOMATIC, UNTICKING IS OPT-IN. A passing condition ticks. A failing one
     on already-ticked work is a REGRESSION report, not a silent rewrite of somebody's
     record — a missing binary and a real regression present identically.

Isolation copies the `_repoint` idiom from tests/test_checker.py. No test here spawns a
shell: `evaluate` takes an injectable `run`, and the CLI tests use commands (`echo`) that
are cheap and deterministic.
"""
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(_REPO_ROOT, "lib")
sys.path.insert(0, LIB)

_TMP_HOME = tempfile.mkdtemp(prefix="ts-exits-")
os.environ["TASK_STATION_HOME"] = _TMP_HOME

import exits                  # noqa: E402
import gating                 # noqa: E402
import steps as steps_mod     # noqa: E402
import store                  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


class _Args:
    def __init__(self, **kw):
        defaults = dict(session=None, task=None, step=None, cmd=None, expect=None,
                        dry_run=False, untick=False, timeout=None)
        defaults.update(kw)
        self.__dict__.update(defaults)


def _repoint(tmp):
    os.environ["TASK_STATION_HOME"] = tmp
    ts.DATA = tmp
    ts.STORE = os.path.join(tmp, "store")
    ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
    ts.LINKS_DIR = os.path.join(ts.STORE, "links")
    store.reset_cache()


def _runner(mapping):
    """A stand-in for the shell: maps a command string to `(output, status)`. Every unit
    test injects one, so the suite proves the LOGIC without depending on any binary being
    present on the machine running it."""
    def run(cmd, _timeout):
        return mapping.get(cmd, ("", "ran"))
    return run


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="exits-test-")
        _repoint(self.tmp)

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _task(self, *step_texts, **fields):
        t = ts.new_task(fields.pop("title", "A task"), "summary")
        t["steps"] = [{"text": s, "done": False} for s in step_texts]
        t.update(fields)
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


# -- (1) registration: what is refused, and why ---------------------------------

class RegistrationTest(_Base):
    def test_a_condition_asserting_nothing_is_refused(self):
        """No expected substring = passes forever whatever the command prints. That is
        the one failure a verification mechanism must not have, so it is refused at
        registration rather than discovered later as a green that meant nothing."""
        t = self._task("build it")
        ok, err = exits.set_condition(t["steps"], 1, "echo hi", [])
        self.assertFalse(ok)
        self.assertIn("no --expect", err)
        self.assertIn("pass whatever the command printed", err)

    def test_blank_expectations_do_not_count_as_expectations(self):
        t = self._task("build it")
        ok, err = exits.set_condition(t["steps"], 1, "echo hi", ["  ", ""])
        self.assertFalse(ok)
        self.assertIn("no --expect", err)

    def test_empty_command_is_refused(self):
        t = self._task("build it")
        ok, err = exits.set_condition(t["steps"], 1, "   ", ["x"])
        self.assertFalse(ok)
        self.assertIn("--cmd", err)

    def test_out_of_range_step_is_an_error_not_a_silent_noop(self):
        t = self._task("build it")
        ok, err = exits.set_condition(t["steps"], 7, "echo hi", ["hi"])
        self.assertFalse(ok)
        self.assertIn("no such step", err)

    def test_superseded_step_is_refused_and_says_how_to_undo_it(self):
        """A retired step is off the active checklist, so a condition there could never
        tick anything — storing one would read later as a gate that silently does
        nothing."""
        t = self._task("old plan", "new plan")
        steps_mod.mark_superseded(t["steps"], 1, 2)
        ok, err = exits.set_condition(t["steps"], 1, "echo hi", ["hi"])
        self.assertFalse(ok)
        self.assertIn("superseded", err)
        self.assertIn("--step-restore 1", err)

    def test_registration_upserts_rather_than_appending(self):
        t = self._task("build it")
        exits.set_condition(t["steps"], 1, "echo one", ["one"])
        exits.set_condition(t["steps"], 1, "echo two", ["two"])
        cond = exits.condition(t["steps"][0])
        self.assertEqual(cond["cmd"], "echo two")
        self.assertEqual(cond["expect"], ["two"])

    def test_clearing_a_condition_that_is_not_there_is_an_error(self):
        """'Removed' after a typo reads as success, and the reader then believes a gate
        is gone while it is still armed."""
        t = self._task("build it")
        ok, err = exits.clear_condition(t["steps"], 1)
        self.assertFalse(ok)
        self.assertIn("no exit condition", err)

    def test_clearing_keeps_the_step_and_its_tick(self):
        t = self._task("build it")
        exits.set_condition(t["steps"], 1, "echo one", ["one"])
        steps_mod.set_done(t["steps"], 1, True)
        ok, _ = exits.clear_condition(t["steps"], 1)
        self.assertTrue(ok)
        self.assertTrue(steps_mod.is_done(t["steps"][0]))
        self.assertEqual(steps_mod.text(t["steps"][0]), "build it")
        self.assertIsNone(exits.condition(t["steps"][0]))


# -- (2) reading a garbled store never raises ------------------------------------

class ShapeTest(_Base):
    def test_garbage_is_filtered_not_raised(self):
        """A store this module did not write is never a reason to break a render — the
        same contract `checker.claim_items` keeps."""
        for junk in (None, "a string", {"cmd": "", "expect": ["x"]},
                     {"cmd": "echo hi", "expect": []}, {"cmd": "echo hi"}):
            self.assertIsNone(exits.condition({"text": "s", "done": False, "exit": junk}))
        self.assertIsNone(exits.condition("a legacy bare string step"))

    def test_an_ordinary_step_still_round_trips_byte_identically(self):
        """The back-compat guarantee: adding this feature must not change how an
        untouched step is stored, or every older reader sees a different record."""
        plain = {"text": "build it", "done": False}
        self.assertEqual(steps_mod.compact(plain), plain)

    def test_the_condition_survives_supersede_and_restore(self):
        t = self._task("build it", "and again")
        exits.set_condition(t["steps"], 1, "echo one", ["one"])
        steps_mod.mark_superseded(t["steps"], 1, 2)
        steps_mod.restore(t["steps"], 1)
        self.assertEqual(exits.condition(t["steps"][0])["cmd"], "echo one")

    def test_superseded_steps_are_excluded_from_items(self):
        t = self._task("old", "new")
        exits.set_condition(t["steps"], 1, "echo one", ["one"])
        exits.set_condition(t["steps"], 2, "echo two", ["two"])
        steps_mod.mark_superseded(t["steps"], 1, 2)
        self.assertEqual([i["n"] for i in exits.items(t)], [2])


# -- (3) the rollup: an empty registration is never satisfied ---------------------

class StateTest(_Base):
    def test_no_conditions_reads_none_never_met(self):
        """THE dangerous line this module could print. `satisfied` gates the wave
        planner, so `none` reading as `met` would release dependent work on the strength
        of an empty checklist."""
        t = self._task("build it")
        self.assertEqual(exits.state(t), exits.NONE)
        self.assertFalse(exits.satisfied(t))

    def test_registered_but_never_run_is_unknown_not_unmet(self):
        t = self._task("build it")
        exits.set_condition(t["steps"], 1, "echo one", ["one"])
        self.assertEqual(exits.item_state(t["steps"][0]), exits.UNKNOWN)
        self.assertEqual(exits.state(t), exits.UNKNOWN)
        self.assertFalse(exits.satisfied(t))

    def test_all_met_is_satisfied(self):
        t = self._task("a", "b")
        exits.set_condition(t["steps"], 1, "one", ["ok"])
        exits.set_condition(t["steps"], 2, "two", ["ok"])
        exits.evaluate(t, run=_runner({"one": ("ok", "ran"), "two": ("ok", "ran")}))
        self.assertEqual(exits.state(t), exits.MET)
        self.assertTrue(exits.satisfied(t))
        self.assertEqual(exits.summary(t), {"total": 2, "met": 2, "unmet": 0, "unknown": 0})

    def test_partial_instrumentation_does_not_buy_a_green(self):
        """The empty-registration rule in weaker form. Without this, a task with eight
        steps could register ONE condition, pass it, and report itself finished —
        releasing every dependent wave on the strength of an eighth of its plan."""
        t = self._task("instrumented", "not instrumented")
        exits.set_condition(t["steps"], 1, "one", ["ok"])
        exits.evaluate(t, run=_runner({"one": ("ok", "ran")}))
        self.assertEqual(exits.state(t), exits.MET)      # every REGISTERED one passed
        self.assertFalse(exits.satisfied(t))             # …but step 2 answers nothing
        self.assertEqual(exits.coverage(t)["uncovered_open"], 1)

    def test_an_uncovered_step_that_is_ticked_is_tolerated(self):
        """Refusing to proceed past a hand-ticked step would make the feature
        unadoptable on any plan that predates it. The rule bites on what is genuinely
        unanswered — uncovered AND unfinished."""
        t = self._task("instrumented", "hand-ticked")
        exits.set_condition(t["steps"], 1, "one", ["ok"])
        steps_mod.set_done(t["steps"], 2, True)
        exits.evaluate(t, run=_runner({"one": ("ok", "ran")}))
        self.assertTrue(exits.satisfied(t))
        self.assertEqual(exits.coverage(t)["uncovered_open"], 0)

    def test_a_superseded_uncovered_step_does_not_block_satisfaction(self):
        """A retired step is off the active checklist, so it cannot be the reason a task
        can never report itself done."""
        t = self._task("instrumented", "dropped from the plan")
        exits.set_condition(t["steps"], 1, "one", ["ok"])
        steps_mod.mark_superseded(t["steps"], 2)
        exits.evaluate(t, run=_runner({"one": ("ok", "ran")}))
        self.assertTrue(exits.satisfied(t))

    def test_one_unmet_beats_one_unknown_in_the_rollup(self):
        """A task with something REFUTED reads `unmet` even when another condition never
        ran: the strongest negative evidence is the honest headline."""
        t = self._task("a", "b")
        exits.set_condition(t["steps"], 1, "one", ["ok"])
        exits.set_condition(t["steps"], 2, "two", ["ok"])
        exits.evaluate(t, run=_runner({"one": ("nope", "ran"), "two": ("", "timeout")}))
        self.assertEqual(exits.state(t), exits.UNMET)


# -- (4) evaluation + the tick rules ---------------------------------------------

class EvaluateTest(_Base):
    def test_missing_substrings_are_named_not_dumped(self):
        t = self._task("build it")
        exits.set_condition(t["steps"], 1, "c", ["ALPHA", "BETA"])
        res = exits.evaluate(t, run=_runner({"c": ("ALPHA only", "ran")}))
        self.assertEqual(res[0]["missing"], ["BETA"])
        self.assertFalse(res[0]["ok"])

    def test_a_passing_condition_ticks_its_step(self):
        """The drift this whole module exists to kill: nobody has to notice."""
        t = self._task("build it")
        exits.set_condition(t["steps"], 1, "c", ["DONE"])
        res = exits.evaluate(t, run=_runner({"c": ("all DONE", "ran")}))
        moved = exits.apply_results(t, res)
        self.assertEqual(moved["ticked"], [1])
        self.assertTrue(steps_mod.is_done(t["steps"][0]))

    def test_a_failing_condition_on_ticked_work_reports_but_does_not_untick(self):
        t = self._task("build it")
        exits.set_condition(t["steps"], 1, "c", ["DONE"])
        steps_mod.set_done(t["steps"], 1, True)
        res = exits.evaluate(t, run=_runner({"c": ("broken", "ran")}))
        moved = exits.apply_results(t, res)
        self.assertEqual(moved["regressed"], [1])
        self.assertEqual(moved["unticked"], [])
        self.assertTrue(steps_mod.is_done(t["steps"][0]))

    def test_untick_is_opt_in(self):
        t = self._task("build it")
        exits.set_condition(t["steps"], 1, "c", ["DONE"])
        steps_mod.set_done(t["steps"], 1, True)
        res = exits.evaluate(t, run=_runner({"c": ("broken", "ran")}))
        moved = exits.apply_results(t, res, untick=True)
        self.assertEqual(moved["unticked"], [1])
        self.assertFalse(steps_mod.is_done(t["steps"][0]))

    def test_a_timeout_moves_no_tick_in_either_direction(self):
        """Invariant 2. A command that did not run has refuted nothing — and has proved
        nothing either."""
        t = self._task("unticked", "ticked")
        exits.set_condition(t["steps"], 1, "a", ["X"])
        exits.set_condition(t["steps"], 2, "b", ["X"])
        steps_mod.set_done(t["steps"], 2, True)
        res = exits.evaluate(t, run=_runner({"a": ("", "timeout"), "b": ("", "timeout")}))
        moved = exits.apply_results(t, res, untick=True)
        self.assertEqual(moved["unknown"], [1, 2])
        self.assertEqual(moved["ticked"], [])
        self.assertEqual(moved["unticked"], [])
        self.assertFalse(steps_mod.is_done(t["steps"][0]))
        self.assertTrue(steps_mod.is_done(t["steps"][1]))

    def test_a_launch_error_is_unknown_and_keeps_its_message(self):
        t = self._task("build it")
        exits.set_condition(t["steps"], 1, "c", ["X"])
        res = exits.evaluate(t, run=_runner({"c": ("no such file", "error")}))
        self.assertEqual(res[0]["status"], "error")
        self.assertEqual(exits.item_state(t["steps"][0]), exits.UNKNOWN)

    def test_only_restricts_to_one_step(self):
        t = self._task("a", "b")
        exits.set_condition(t["steps"], 1, "one", ["ok"])
        exits.set_condition(t["steps"], 2, "two", ["ok"])
        res = exits.evaluate(t, only=[2], run=_runner({"one": ("ok", "ran"),
                                                       "two": ("ok", "ran")}))
        self.assertEqual([r["n"] for r in res], [2])
        self.assertEqual(exits.item_state(t["steps"][0]), exits.UNKNOWN)

    def test_the_result_is_stored_so_a_later_read_costs_nothing(self):
        """The scan reads stored verdicts rather than re-running commands; without this
        the planner would be as expensive as the conditions it plans over."""
        t = self._task("build it")
        exits.set_condition(t["steps"], 1, "c", ["OK"])
        exits.evaluate(t, run=_runner({"c": ("OK", "ran")}), now=1000.0)
        ts.save_task(t)
        again = ts.load_task(t["id"])
        self.assertEqual(exits.state(again), exits.MET)
        self.assertEqual(exits.last_run_ts(again), 1000.0)


# -- (5) the CLI surface ----------------------------------------------------------

class CliTest(_Base):
    def test_show_on_a_task_with_nothing_registered_says_how_to_register(self):
        t = self._task("build it")
        out, _ = self._out(ts.cmd_exit_show, _Args(task=str(t["seq"])))
        self.assertIn("no exit conditions registered", out)
        self.assertIn("exit-add", out)

    def test_add_then_tick_exits_zero_when_everything_is_met(self):
        t = self._task("build it")
        self._out(ts.cmd_exit_add, _Args(task=str(t["seq"]), step=1,
                                         cmd="echo SHIPPED", expect=["SHIPPED"]))
        out, code = self._out(ts.cmd_exit_tick, _Args(task=str(t["seq"])))
        self.assertIsNone(code)                      # exit 0 = every condition met
        self.assertIn("1/1 met", out)
        self.assertIn("ticked: step 1", out)
        self.assertTrue(steps_mod.is_done(ts.load_task(t["id"])["steps"][0]))

    def test_tick_exits_one_when_anything_is_not_met(self):
        """The exit code is what lets this GATE a release step rather than only inform a
        reader, so 'not proven met' must never exit 0."""
        t = self._task("build it")
        self._out(ts.cmd_exit_add, _Args(task=str(t["seq"]), step=1,
                                         cmd="echo nope", expect=["SHIPPED"]))
        out, code = self._out(ts.cmd_exit_tick, _Args(task=str(t["seq"])))
        self.assertEqual(code, 1)
        self.assertIn("missing from the output: SHIPPED", out)

    def test_dry_run_reports_without_moving_a_tick(self):
        t = self._task("build it")
        self._out(ts.cmd_exit_add, _Args(task=str(t["seq"]), step=1,
                                         cmd="echo SHIPPED", expect=["SHIPPED"]))
        out, _ = self._out(ts.cmd_exit_tick, _Args(task=str(t["seq"]), dry_run=True))
        self.assertIn("nothing was ticked", out)
        self.assertFalse(steps_mod.is_done(ts.load_task(t["id"])["steps"][0]))

    def test_tick_on_a_task_with_no_conditions_says_so_and_does_not_fail(self):
        t = self._task("build it")
        out, code = self._out(ts.cmd_exit_tick, _Args(task=str(t["seq"])))
        self.assertIsNone(code)
        self.assertIn("registers no exit conditions", out)

    def test_add_refuses_a_condition_with_no_expectation_from_the_cli(self):
        t = self._task("build it")
        out, code = self._out(ts.cmd_exit_add, _Args(task=str(t["seq"]), step=1,
                                                     cmd="echo hi", expect=None))
        self.assertEqual(code, 2)
        self.assertIn("pass whatever the command printed", out)

    def test_an_unknown_task_ref_reports_rather_than_raising(self):
        out, _ = self._out(ts.cmd_exit_show, _Args(task="99999"))
        self.assertIn("No task matching", out)

    def test_show_marks_met_unmet_and_never_run_distinctly(self):
        t = self._task("a", "b", "c")
        for n, cmd, want in ((1, "echo YES", "YES"), (2, "echo no", "YES"),
                             (3, "echo YES", "YES")):
            self._out(ts.cmd_exit_add, _Args(task=str(t["seq"]), step=n,
                                             cmd=cmd, expect=[want]))
        self._out(ts.cmd_exit_tick, _Args(task=str(t["seq"]), step=1))
        self._out(ts.cmd_exit_tick, _Args(task=str(t["seq"]), step=2))
        out, _ = self._out(ts.cmd_exit_show, _Args(task=str(t["seq"])))
        self.assertIn("1 met · 1 unmet · 1 not run", out)


# -- (6) MERGE-GATED IS VISIBLE WHERE CONDITIONS ARE READ -------------------------
#
# THE DEFECT THESE PIN, measured on 2026-08-30 against #591. Five conditions carried
# `merge_gated=True` in the store, `exit-add` had printed the MERGE-GATED banner at
# registration, and `exit-show` rendered them BYTE-IDENTICALLY to five undeclared ones.
# A parent read that surface, concluded none were declared, and told the child to declare
# a flag it had already declared. The flag was stored, load-bearing and unreadable.
#
# WHY IT IS NOT COSMETIC. Conditions run against the MAIN checkout, so while the one human
# who may merge is away, EVERY condition on EVERY in-flight child is red for that reason
# and no other. Without a mark, "red because unmerged" and "red because broken" are the
# same picture, and only the wrong reading is available.
#
# EVERY TEST HERE HAS ITS NEGATIVE CONTROL, because the one thing worse than an invisible
# declaration is a mark that appears without one.

class MergeGatedIsVisible(_Base):

    def _declared(self, *steps_):
        """A task whose steps each carry a condition, `(step_text, merge_gated)`."""
        t = self._task(*[text for text, _g in steps_])
        for n, (_text, gated) in enumerate(steps_, start=1):
            exits.set_condition(t["steps"], n, "check-%d" % n, ["OK"],
                                merge_gated=gated)
        return t

    def _red(self, t, *ns):
        for n in ns:
            t["steps"][n - 1]["exit"]["last"] = {"ts": 1000.0, "ok": False,
                                                 "status": "ran", "missing": ["OK"],
                                                 "got": ""}

    # -- the leaf ----------------------------------------------------------------

    def test_the_tally_counts_declared_conditions_whether_or_not_they_are_red(self):
        """The declaration is a property of the CONDITION, not of its last result — a
        surface that counted only the red ones would go silent the moment work landed."""
        t = gating.tally([("met", True), ("unmet", True), ("unknown", True)])
        self.assertEqual(t["declared"], 3)
        self.assertEqual(t["unmet"], 1)
        self.assertEqual(t["merge_gated"], 1)

    def test_nothing_unmet_is_never_pending_merge(self):
        """A task with no unmet conditions is not waiting on a merge, it is just fine."""
        t = gating.tally([("met", True), ("met", True)])
        self.assertFalse(gating.pending_merge(t))
        self.assertFalse([n for n in gating.header_notes(t) if "DONE PENDING MERGE" in n])
        self.assertIsNone(gating.wait_note(t))

    def test_one_ordinary_unmet_condition_outranks_any_number_of_gated_ones(self):
        """Something a merge cannot fix means the work is not finished. This is the
        negative control that stops the flag buying a soft reading for a real red."""
        t = gating.tally([("unmet", True), ("unmet", True), ("unmet", False)])
        self.assertFalse(gating.pending_merge(t))
        self.assertIsNone(gating.wait_note(t))

    def test_an_undeclared_condition_gets_no_mark_at_all(self):
        self.assertIsNone(gating.step_note(False))
        self.assertEqual(gating.header_notes(gating.tally([("unmet", False)])), [])

    def test_the_leaf_imports_nothing_so_it_can_be_run_out_of_a_git_object(self):
        """The whole reason these rules are not beside their callers: `exits` imports
        `checker`, which imports `heal`, which reads the store — none of that loads from
        `git show origin/main:`. A leaf does. Same move as timing.py in 3.44.0."""
        src = open(os.path.join(LIB, "board", "gating.py")).read()
        offenders = [ln for ln in src.splitlines()
                     if ln.startswith("import ") or ln.startswith("from ")]
        self.assertEqual(offenders, [], "gating.py must import nothing: %s" % offenders)

    # -- the surface -------------------------------------------------------------

    def test_show_marks_a_declared_condition_per_step(self):
        t = self._declared(("a", True), ("b", False))
        ts.save_task(t)
        out, _ = self._out(ts.cmd_exit_show, _Args(task=str(t["seq"])))
        self.assertEqual(out.count("merge-gated — it reads the merge target"), 1)

    def test_show_counts_declared_conditions_in_its_header(self):
        t = self._declared(("a", True), ("b", True), ("c", False))
        ts.save_task(t)
        out, _ = self._out(ts.cmd_exit_show, _Args(task=str(t["seq"])))
        self.assertIn("2 of them are MERGE-GATED", out)

    def test_show_says_DONE_PENDING_MERGE_when_every_unmet_one_is_declared(self):
        t = self._declared(("a", True), ("b", True))
        self._red(t, 1, 2)
        ts.save_task(t)
        out, _ = self._out(ts.cmd_exit_show, _Args(task=str(t["seq"])))
        self.assertIn("DONE PENDING MERGE", out)

    def test_show_does_NOT_say_DONE_PENDING_MERGE_on_a_mixed_red(self):
        """One red a merge cannot fix, and the softer reading is off."""
        t = self._declared(("a", True), ("b", False))
        self._red(t, 1, 2)
        ts.save_task(t)
        out, _ = self._out(ts.cmd_exit_show, _Args(task=str(t["seq"])))
        self.assertNotIn("DONE PENDING MERGE", out)

    def test_an_all_undeclared_task_renders_exactly_as_it_did_before(self):
        """The permanent negative control: nothing declared, nothing printed. A surface
        that said `0 merge-gated` on every task would train the reader to skip the line,
        and the line is the whole feature."""
        t = self._declared(("a", False), ("b", False))
        self._red(t, 1, 2)
        ts.save_task(t)
        out, _ = self._out(ts.cmd_exit_show, _Args(task=str(t["seq"])))
        self.assertNotIn("merge-gated", out.lower())

    def test_items_carries_the_declaration_so_no_surface_has_to_re_read_it(self):
        t = self._declared(("a", True), ("b", False))
        self.assertEqual([i["merge_gated"] for i in exits.items(t)], [True, False])


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# A CONDITION WRITTEN AS A DIRECTION — the shipped answer to "or docs plus a template".
#
# `test COUNT = LITERAL` is falsified by any legitimate release: five of one task's
# seventeen claims went red in four days for that reason alone, and one hid three genuinely
# broken links behind its stale baseline. The engine needed NOTHING new for the fix — a
# claim and an exit condition are already "<command> plus the substrings its output must
# contain", so the comparison goes in the command and the expectation is its PASS token.
# What was missing was a worked example and a pointer to it from where an author registers.
#
# So the template is the shipped artefact, and it is tested like one. A template that does
# not run is worse than no template: it teaches the shape AND a bug.
# ---------------------------------------------------------------------------

class DirectionTemplateTest(unittest.TestCase):
    TEMPLATE = os.path.join(_REPO_ROOT, "tools", "checker-template.sh")

    def test_the_template_exists_and_is_executable(self):
        self.assertTrue(os.path.exists(self.TEMPLATE), self.TEMPLATE)
        self.assertTrue(os.access(self.TEMPLATE, os.X_OK), "not executable")

    def test_the_shell_can_parse_it(self):
        import subprocess
        p = subprocess.run(["sh", "-n", self.TEMPLATE], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_an_unrunnable_command_prints_FAIL_rather_than_nothing(self):
        """RULE 3, and the reason this test is here rather than in a doc: an exit condition
        whose command prints NOTHING goes red with no diagnostic, and the transcript cannot
        say which half failed. Point the template at a repo that is not there and it must
        still print a verdict."""
        import subprocess
        env = dict(os.environ, REPO="/nonexistent-repo-for-this-test")
        p = subprocess.run(["sh", self.TEMPLATE], capture_output=True, text=True, env=env)
        self.assertTrue(p.stdout.strip(), "printed nothing at all")
        self.assertIn("FAIL", p.stdout)
        self.assertNotEqual(p.returncode, 0)

    def test_the_template_carries_a_floor_and_never_an_equality(self):
        self.assertTrue(os.path.exists(self.TEMPLATE), self.TEMPLATE)
        body = open(self.TEMPLATE).read()
        self.assertIn("-ge \"$FLOOR\"", body)          # a floor, not `= LITERAL`
        self.assertIn("ceiling", body.lower())          # …and the mirror image is shown
        self.assertIn("path-test-and", body)            # the `&&` guard is named, not implied

    def test_both_registration_surfaces_point_at_it(self):
        """The pointer is the whole delivery mechanism. If it rots, the template is a file
        nobody is told about — so the help text is asserted, not trusted."""
        cli = open(os.path.join(_REPO_ROOT, "lib", "board", "cli.py")).read()
        self.assertEqual(cli.count("tools/checker-template.sh"), 2, cli.count(
            "tools/checker-template.sh"))
        self.assertIn("DIRECTION, NOT A LITERAL", cli)


# ---------------------------------------------------------------------------
# RULE 5 — A GREEN CONDITION MEANS THE COMMAND SUCCEEDED.
#
# `returncode == 0` is a required conjunct alongside the expected substring. Before
# 3.49.0 only the substring was asked, so `echo T-PASS; exit 1` ticked a step, and a
# condition could be satisfied by a command that failed. The two questions are
# different and both have to answer yes: the substring asks whether the command SAID
# the thing, the exit status asks whether it WORKED.
#
# The shell tests here spawn a REAL shell on purpose. Only the real subprocess path can
# prove the return code is read at all — a fake runner returns whatever it was written
# to return, and a source grep goes green on a line that is never reached.
# ---------------------------------------------------------------------------

class TestTheExitCodeConjunct(_Base):
    def _one(self, cmd, expect):
        t = self._task("step one")
        exits.set_condition(t["steps"], 1, cmd, [expect])
        return t

    def test_a_failing_command_that_prints_the_substring_is_UNMET(self):
        t = self._one("echo T-PASS; exit 1", "T-PASS")
        res = exits.evaluate(t)
        self.assertFalse(res[0]["ok"])
        self.assertEqual(res[0]["code"], 1)
        self.assertEqual(res[0]["missing"], [])       # it printed exactly what was asked
        self.assertEqual(exits.item_state(t["steps"][0]), exits.UNMET)

    def test_a_failing_command_is_UNMET_and_never_UNKNOWN(self):
        # Rule 2 shelters commands that did NOT RUN. This one ran and disagreed, so it
        # must refute — otherwise every failing command hides behind "nothing was proved"
        # and the invariant buys nothing.
        t = self._one("echo T-PASS >&2; exit 7", "T-PASS")
        exits.evaluate(t)
        self.assertEqual(exits.item_state(t["steps"][0]), exits.UNMET)
        self.assertEqual(exits.state(t), exits.UNMET)

    def test_a_failing_command_does_not_tick_its_step(self):
        # The whole point: a tick is a statement that the step is FINISHED, and a command
        # that failed has not shown that however good its output looked.
        t = self._one("echo T-PASS; exit 1", "T-PASS")
        moved = exits.apply_results(t, exits.evaluate(t))
        self.assertEqual(moved["ticked"], [])
        self.assertFalse(steps_mod.is_done(t["steps"][0]))

    def test_a_clean_command_still_ticks_its_step(self):
        t = self._one("echo T-PASS", "T-PASS")
        moved = exits.apply_results(t, exits.evaluate(t))
        self.assertEqual(moved["ticked"], [1])

    def test_the_exit_code_is_stored_on_the_step(self):
        # A later reader has to be able to say WHY a condition is red without re-running
        # it — and "the substring was missing" and "the command failed" are different
        # findings that lead to different fixes.
        t = self._one("echo T-PASS; exit 3", "T-PASS")
        exits.evaluate(t)
        self.assertEqual(exits.condition(t["steps"][0])["last"]["code"], 3)

    def test_a_command_that_starts_green_and_turns_failing_is_a_REGRESSION(self):
        t = self._one("echo T-PASS", "T-PASS")
        exits.apply_results(t, exits.evaluate(t))
        self.assertTrue(steps_mod.is_done(t["steps"][0]))
        t["steps"][0]["exit"]["cmd"] = "echo T-PASS; exit 1"
        moved = exits.apply_results(t, exits.evaluate(t))
        self.assertEqual(moved["regressed"], [1])
        self.assertTrue(steps_mod.is_done(t["steps"][0]))   # rule 4 still holds

    def test_a_timeout_is_still_UNKNOWN_and_moves_nothing(self):
        # The conjunct must not swallow rule 2 on its way past.
        t = self._one("sleep", "T-PASS")
        res = exits.evaluate(t, run=_runner({"sleep": ("", "timeout")}))
        self.assertEqual(res[0]["status"], "timeout")
        self.assertIsNone(res[0]["code"])
        self.assertEqual(exits.item_state(t["steps"][0]), exits.UNKNOWN)

    def test_an_injected_two_tuple_runner_still_means_success(self):
        t = self._one("c", "OK")
        res = exits.evaluate(t, run=_runner({"c": ("OK", "ran")}))
        self.assertTrue(res[0]["ok"])
        self.assertEqual(res[0]["code"], 0)

    def test_an_injected_three_tuple_runner_is_judged_on_its_code(self):
        t = self._one("c", "OK")
        res = exits.evaluate(t, run=lambda cmd, to: ("OK", "ran", 2))
        self.assertFalse(res[0]["ok"])
        self.assertEqual(res[0]["code"], 2)
