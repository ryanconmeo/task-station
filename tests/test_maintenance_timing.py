"""MAINTENANCE TIMING — the scheduler that decides WHEN, and what a machine may do unasked.

WHAT THESE COVER, one section per defect the feature was built from:

  1. THE DENOMINATOR. `--context-window` was 200,000 on a machine running a 1M model, so
     `--checkpoint-pct 65` fired at ~13% of the real budget on every session, silently. The
     stopgap of 1,000,000 is the same defect pointing the other way and costs more when it is
     wrong. So the window is DETECTED and the config is an OVERRIDE THAT SAYS SO — proved
     BOTH ways here, because "detection works" and "an override still wins" are two claims and
     shipping only the first would take a setting away from whoever set it.
  2. THE BOUNDARY. A count says an action is owed; it says nothing about whether now is a good
     time. `timing.boundary` composes two mechanisms that already shipped and adds nothing.
  3. THE CLASSES. A merge is NEVER automatic, and that is asserted from both sides — the
     classifier's table and the plan filter — because the two must agree and only a test can
     say so.
  4. THE HANDOFF FLOOR, including the refusal that is the whole point of it: due, affordable,
     safe, and STILL no prompt while the record is stale.
  5. DISMISSAL IDENTITY. A ruling survives its subject being edited, and says MOVED when the
     wording changes — with the negative control, because a dismissal that silenced a
     DIFFERENT subject would be strictly worse than the re-firing it replaces.
"""
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(BASE, "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

# The engine is loaded for its side effect: `board._shared` binds the cross-module
# facade (`g`/`set_g`) at engine import, and `sessions.detected_context_window` reaches
# its two detectors THROUGH that facade — which is exactly what makes them stubbable
# here without monkeypatching a module attribute the real caller never reads.
import importlib.util                                                # noqa: E402
_spec = importlib.util.spec_from_file_location("task_station",
                                               os.path.join(LIB, "task-station.py"))
_ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ts)

import heal                                                          # noqa: E402
import timing                                                        # noqa: E402
from board import sessions                                           # noqa: E402
from board.cmds.sub import _record_shape                             # noqa: E402


# ---------------------------------------------------------------------------------
# 1. THE WINDOW IS DETECTED, AND THE CONFIG IS AN OVERRIDE THAT SAYS WHICH SOURCE WON
# ---------------------------------------------------------------------------------

class WindowResolution(unittest.TestCase):
    """Step 1. Proved BOTH ways, and the divergence is proved too — because the failure this
    replaces was never a wrong calculation, it was a number nobody could question."""

    def setUp(self):
        self._env = dict(os.environ)
        os.environ.pop("TASK_STATION_CONTEXT_WINDOW", None)
        self._harness = sessions.harness_context_window
        self._model = sessions.g("session_model")
        self._sel = sessions.g("claude_code_model_selection")
        self._cfg = sessions._config_get

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        sessions.harness_context_window = self._harness
        sessions.set_g("session_model", self._model)
        sessions.set_g("claude_code_model_selection", self._sel)
        sessions._config_get = self._cfg

    def _stub(self, harness=None, model="", selection="", config=None):
        sessions.harness_context_window = lambda _s: harness
        sessions.set_g("session_model", lambda _s: model)
        sessions.set_g("claude_code_model_selection", lambda: selection)
        sessions._config_get = lambda _k: config

    def test_a_1M_session_resolves_1M_with_NO_config_at_all(self):
        """WAY ONE. Nothing configured anywhere, and the answer is still right."""
        self._stub(harness=1000000)
        res = sessions.window_resolution("sid")
        self.assertEqual(res["window"], 1000000)
        self.assertEqual(res["source"], sessions.SRC_HARNESS)
        self.assertIsNone(res["override"])

    def test_a_200k_session_resolves_200k_with_NO_config_at_all(self):
        """The other half of way one, and the one the 1,000,000 stopgap would have got
        wrong. Detection has to be right in BOTH directions or it is just a different
        constant."""
        self._stub(harness=200000)
        self.assertEqual(sessions.window_resolution("sid")["window"], 200000)

    def test_detection_reads_the_1m_marker_off_the_selection_when_harness_is_silent(self):
        self._stub(harness=None, model="claude-opus-5", selection="claude-opus-5[1m]")
        res = sessions.window_resolution("sid")
        self.assertEqual(res["window"], 1000000)
        self.assertEqual(res["source"], sessions.SRC_SELECTION)

    def test_detection_NEVER_inflates_across_model_families(self):
        """A `--model sonnet` session under an `opus[1m]` default must not be sized at 1M.
        Over-sizing is the direction that LOSES a checkpoint, so the conservative branch is
        the one that has to be pinned."""
        self._stub(harness=None, model="claude-sonnet-5", selection="claude-opus-5[1m]")
        self.assertEqual(sessions.window_resolution("sid")["window"], 200000)

    def test_an_explicit_override_STILL_wins(self):
        """WAY TWO. Demoting the config to an override must not quietly delete it: a number
        the user typed is user intent, and a detector does not get to overrule it."""
        self._stub(harness=1000000)
        os.environ["TASK_STATION_CONTEXT_WINDOW"] = "123456"
        res = sessions.window_resolution("sid")
        self.assertEqual(res["window"], 123456)
        self.assertEqual(res["source"], sessions.SRC_ENV)

    def test_the_override_names_what_it_overruled(self):
        """THE DEFECT MADE VISIBLE. 200,000 stored against a 1M session is exactly the state
        that cost ~13%-of-budget nudges for months, and it now reports itself."""
        self._stub(harness=1000000)
        os.environ["TASK_STATION_CONTEXT_WINDOW"] = "200000"
        res = sessions.window_resolution("sid")
        self.assertTrue(res["diverges"])
        self.assertEqual(res["detected"], 1000000)
        self.assertIn("DIVERGES", "\n".join(sessions.window_lines(res)))

    def test_the_stopgap_is_reported_as_wrong_too(self):
        """1,000,000 stored against a 200k session — the same finding with the sign flipped,
        and the more expensive one, because losing a checkpoint costs a record."""
        self._stub(harness=200000)
        os.environ["TASK_STATION_CONTEXT_WINDOW"] = "1000000"
        res = sessions.window_resolution("sid")
        self.assertTrue(res["diverges"])
        self.assertIn("above the real window", res["why"])

    def test_an_agreeing_override_does_not_cry_wolf(self):
        self._stub(harness=1000000)
        os.environ["TASK_STATION_CONTEXT_WINDOW"] = "1000000"
        self.assertFalse(sessions.window_resolution("sid")["diverges"])

    def test_a_zero_or_junk_override_is_not_an_override(self):
        for junk in ("0", "-5", "abc", ""):
            self._stub(harness=1000000)
            os.environ["TASK_STATION_CONTEXT_WINDOW"] = junk
            res = sessions.window_resolution("sid")
            self.assertEqual(res["window"], 1000000, junk)
            self.assertIsNone(res["override"], junk)

    def test_effective_context_window_is_the_resolution_and_cannot_drift(self):
        self._stub(harness=1000000)
        self.assertEqual(sessions.effective_context_window("sid"),
                         sessions.window_resolution("sid")["window"])

    def test_the_floor_says_it_is_a_floor_and_not_a_measurement(self):
        self._stub(harness=None, model="", selection="")
        res = sessions.window_resolution("sid")
        self.assertEqual(res["source"], sessions.SRC_DEFAULT)
        self.assertIn("not a measurement", res["why"])


# ---------------------------------------------------------------------------------
# 2. THE WORK-BOUNDARY SIGNAL
# ---------------------------------------------------------------------------------

class Boundary(unittest.TestCase):

    def test_nothing_in_flight_is_a_boundary(self):
        self.assertTrue(timing.boundary()["safe"])

    def test_each_kind_of_in_flight_work_closes_the_boundary(self):
        for kwargs, kind in (({"orders": 1}, timing.IN_FLIGHT_ORDER),
                             ({"pickups": 1}, timing.IN_FLIGHT_PICKUP),
                             ({"git_op": "a merge"}, timing.IN_FLIGHT_MERGE),
                             ({"untracked_edits": True}, timing.IN_FLIGHT_EDITS)):
            state = timing.boundary(**kwargs)
            self.assertFalse(state["safe"], kind)
            self.assertEqual([k for k, _w in state["in_flight"]], [kind])

    def test_every_in_flight_reason_is_reported_not_just_the_first(self):
        """A boundary report that named one blocker would send a session round the loop once
        per blocker — the same "fix it, get told the next one" that makes a gate hated."""
        state = timing.boundary(orders=2, pickups=1, git_op="a rebase")
        self.assertEqual(len(state["in_flight"]), 3)

    def test_a_half_done_merge_is_seen_in_a_real_tree(self):
        with tempfile.TemporaryDirectory() as d:
            gd = os.path.join(d, ".git")
            os.makedirs(gd)
            self.assertIsNone(timing.git_operation_in_progress(d))
            with open(os.path.join(gd, "MERGE_HEAD"), "w") as f:
                f.write("a commit id\n")
            self.assertEqual(timing.git_operation_in_progress(d), "a merge")

    def test_an_interrupted_rebase_is_a_DIRECTORY_and_is_still_seen(self):
        """Statting `rebase-merge` as a file would report a mid-rebase tree as clean — the
        exact false-clean this signal exists to prevent."""
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".git", "rebase-merge"))
            self.assertEqual(timing.git_operation_in_progress(d), "a rebase")

    def test_a_WORKTREE_gitdir_pointer_resolves(self):
        """Not an edge case: this feature was itself built in a worktree, where `.git` is a
        one-line file. A signal that could not read its own development tree would have
        shipped reporting every turn as clean."""
        with tempfile.TemporaryDirectory() as d:
            real = os.path.join(d, "gitdir")
            os.makedirs(real)
            with open(os.path.join(real, "CHERRY_PICK_HEAD"), "w") as f:
                f.write("x")
            tree = os.path.join(d, "tree")
            os.makedirs(tree)
            with open(os.path.join(tree, ".git"), "w") as f:
                f.write("gitdir: %s\n" % real)
            self.assertEqual(timing.git_operation_in_progress(tree), "a cherry-pick")

    def test_an_unreadable_tree_reports_nothing_in_flight_rather_than_raising(self):
        self.assertIsNone(timing.git_operation_in_progress("/nonexistent/%s" % os.getpid()))


# ---------------------------------------------------------------------------------
# 3. THE CLASSES — AND THE THINGS NO THRESHOLD REACHES
# ---------------------------------------------------------------------------------

class Classes(unittest.TestCase):

    def test_the_mechanical_heal_pass_is_AUTO(self):
        self.assertEqual(timing.classify(timing.ACTION_HEAL_MECHANICAL)[0], timing.AUTO)

    def test_a_merge_is_NEVER_auto(self):
        self.assertEqual(timing.classify(timing.ACTION_MERGE)[0], timing.NEVER)
        self.assertFalse(timing.may_run_unattended(timing.ACTION_MERGE))

    def test_a_handoff_is_NEVER_auto_because_it_ends_the_session(self):
        self.assertEqual(timing.classify(timing.ACTION_HANDOFF)[0], timing.NEVER)

    def test_an_unknown_action_is_NEVER_auto(self):
        """The default has to be refusal. An unclassified write is exactly the write nobody
        decided to allow, and defaulting to AUTO would make every future action opt-out."""
        self.assertEqual(timing.classify("something-nobody-classified")[0], timing.NEVER)
        self.assertFalse(timing.may_run_unattended("something-nobody-classified"))

    def test_every_classified_action_carries_a_why(self):
        for action in timing.CLASSES:
            self.assertTrue(timing.classify(action)[1].strip(), action)

    def test_the_plan_filter_and_the_classifier_agree_about_merges(self):
        """TWO INDEPENDENT READINGS OF ONE RULE. `heal.auto_ops` filters on the verb and
        `timing.classify` reads a table; if they ever disagreed, one of them would be
        performing a write the other had forbidden."""
        ops = [{"verb": "split", "manual": False},
               {"verb": "merge", "manual": False},
               {"verb": "disposition", "manual": False}]
        verbs = [o["verb"] for o in heal.auto_ops(ops)]
        self.assertNotIn("merge", verbs)
        self.assertEqual(sorted(verbs), ["disposition", "split"])
        self.assertFalse(timing.may_run_unattended(timing.ACTION_MERGE))

    def test_a_manual_op_is_not_auto_eligible(self):
        """An op whose parts could not be derived would be skipped by `apply` anyway.
        Counting it here would make an unattended pass claim work and then do none — the
        false `last heal just now` this codebase already refuses once."""
        self.assertEqual(heal.auto_ops([{"verb": "split", "manual": True}]), [])

    def test_held_ops_are_reported_not_dropped(self):
        ops = [{"verb": "merge", "manual": False}, {"verb": "split", "manual": False}]
        self.assertEqual([o["verb"] for o in heal.held_ops(ops)], ["merge"])


# ---------------------------------------------------------------------------------
# 4. THE HANDOFF FLOOR
# ---------------------------------------------------------------------------------

class HandoffFloor(unittest.TestCase):

    def test_under_the_trigger_nothing_is_due(self):
        self.assertEqual(timing.handoff_due("keep-going", True)[0], timing.HANDOFF_HOLD)

    def test_due_affordable_safe_and_ready_prompts(self):
        self.assertEqual(timing.handoff_due("relay", True)[0], timing.HANDOFF_PROMPT)

    def test_it_REFUSES_to_prompt_while_the_record_is_stale(self):
        """THE POINT OF THE WHOLE LIMB. Handing over a stale record is worse than handing
        over late: the successor inherits a confident summary of a state that has moved,
        believes it, and works from it. Late costs one session boundary."""
        state, why = timing.handoff_due("relay", False, ["checkpoint stale", "no NEXT"])
        self.assertEqual(state, timing.HANDOFF_BLOCKED)
        self.assertIn("2 gap(s)", why)
        self.assertIn("/todo save", why)

    def test_a_spent_reserve_is_MISSED_not_a_prompt(self):
        state, why = timing.handoff_due("compact", True)
        self.assertEqual(state, timing.HANDOFF_MISSED)
        self.assertIn("compaction", why)

    def test_a_due_handoff_at_a_NON_boundary_holds(self):
        """Even a due, affordable, ready handoff waits for the boundary — a handoff proposed
        mid-flight is one more thing to dismiss, which is how it becomes furniture."""
        self.assertEqual(timing.handoff_due("relay", True, boundary_safe=False)[0],
                         timing.HANDOFF_HOLD)

    def test_the_handoff_is_never_promoted_to_AUTO_by_any_of_this(self):
        self.assertEqual(timing.classify(timing.ACTION_HANDOFF)[0], timing.NEVER)


# ---------------------------------------------------------------------------------
#  the composed verdict
# ---------------------------------------------------------------------------------

class Schedule(unittest.TestCase):

    def test_the_auto_class_does_not_fire_off_a_boundary(self):
        sched = timing.schedule(timing.boundary(pickups=1), auto_ops=3)
        self.assertFalse(sched["auto_fires"])

    def test_the_auto_class_does_not_fire_with_nothing_to_do(self):
        """A pass performing zero operations that stamped a heal is the false
        `last heal just now` this codebase paid for once already."""
        self.assertFalse(timing.schedule(timing.boundary(), auto_ops=0)["auto_fires"])

    def test_it_fires_when_both_are_true(self):
        self.assertTrue(timing.schedule(timing.boundary(), auto_ops=2)["auto_fires"])

    def test_a_clean_boundary_is_still_printed(self):
        """Silence about a clean check reads identically to never having looked — the rule
        `save.gap_lines`, `heal.scan_lines` and `succession.report_lines` all keep."""
        lines = "\n".join(timing.schedule_lines(timing.schedule(timing.boundary())))
        self.assertIn("boundary", lines)
        self.assertIn("nothing owed", lines)


# ---------------------------------------------------------------------------------
# 5. A DISMISSAL KEYS ON THE SUBJECT, NOT ON THE SENTENCE
# ---------------------------------------------------------------------------------

class DismissalIdentity(unittest.TestCase):

    def _finding(self, detail):
        return {"check": "grew-with-candidates", "ref": "digest", "detail": detail}

    def _task_with_ruling(self, finding, why="ruled"):
        task = {"id": "t", "seq": 1}
        task["heal_dismissals"] = [heal.dismissal_entry(finding, why)]
        return task

    def test_a_ruling_survives_its_own_subject_being_reworded(self):
        """THE MEASURED DEFECT. On the real record `grew-with-candidates:digest` was ruled
        THE SAME WAY five separate times, re-firing only because a character count moved."""
        task = self._task_with_ruling(self._finding("digest 82,000 chars"))
        kept, dropped = heal.apply_dismissals([self._finding("digest 82,412 chars")], task)
        self.assertEqual(kept, [])
        self.assertEqual(len(dropped), 1)

    def test_a_moved_finding_reports_as_MOVED_rather_than_silently(self):
        """Still settled — but not silently settled. A silenced finding nobody can see is
        indistinguishable from a check that stopped running."""
        task = self._task_with_ruling(self._finding("about decision 436, 503"))
        _kept, dropped = heal.apply_dismissals([self._finding("about decision 436")], task)
        self.assertTrue(dropped[0]["moved"])
        self.assertIn("MOVED", "\n".join(
            heal.dismissal_lines(task, result={"findings": [], "dismissed": dropped})))

    def test_an_unmoved_finding_is_not_labelled_moved(self):
        ruled = self._finding("digest 82,000 chars")
        task = self._task_with_ruling(ruled)
        _kept, dropped = heal.apply_dismissals([dict(ruled)], task)
        self.assertFalse(dropped[0].get("moved"))

    def test_a_ruling_NEVER_silences_a_different_subject(self):
        """THE NEGATIVE CONTROL, and the one that matters most: a dismissal that reached
        past its own subject would be strictly worse than the re-firing it replaces."""
        task = self._task_with_ruling(self._finding("digest 82,000 chars"))
        other_ref = {"check": "grew-with-candidates", "ref": "state", "detail": "x"}
        other_check = {"check": "oversized", "ref": "digest", "detail": "x"}
        kept, dropped = heal.apply_dismissals([other_ref, other_check], task)
        self.assertEqual(len(kept), 2)
        self.assertEqual(dropped, [])

    def test_a_LEGACY_ledger_row_with_no_identity_still_silences(self):
        """THE MIGRATION IS THE DERIVATION. Every row ever written stores `check` and `ref`,
        so a ledger built up over months works on the first run of the new code."""
        task = {"id": "t", "seq": 1, "heal_dismissals": [
            {"check": "grew-with-candidates", "ref": "digest",
             "fingerprint": "an-old-wording", "why": "ruled", "ts": 1.0}]}
        kept, dropped = heal.apply_dismissals([self._finding("new wording")], task)
        self.assertEqual(kept, [])
        self.assertTrue(dropped[0]["moved"])

    def test_a_RETIRED_ruling_silences_nothing(self):
        task = self._task_with_ruling(self._finding("d"))
        task["heal_dismissals"][0]["retired"] = True
        kept, dropped = heal.apply_dismissals([self._finding("d")], task)
        self.assertEqual(len(kept), 1)
        self.assertEqual(dropped, [])

    def test_identity_is_stable_across_processes(self):
        """hashlib, not hash() — a per-process string seed would silently expire every
        dismissal on restart."""
        a = heal.finding_identity(self._finding("x"))
        b = heal.finding_identity(self._finding("completely different detail"))
        self.assertEqual(a, b)
        self.assertEqual(len(a), 40)

    def test_the_fingerprint_still_distinguishes_wordings(self):
        """It is no longer the key; it is still the WITNESS, and MOVED depends on it."""
        self.assertNotEqual(heal.finding_fingerprint(self._finding("a")),
                            heal.finding_fingerprint(self._finding("b")))


# ---------------------------------------------------------------------------------
#  THE SEAM — the half that gathers facts and performs writes, driven end to end
# ---------------------------------------------------------------------------------

class BoundaryPass(unittest.TestCase):
    """The AUTO class at a real turn end, against a real store.

    The policy above is pure and testable against dicts. THIS is the half that could not be
    checked that way: whether the gate is cheap on a quiet turn, whether the write actually
    lands, and whether it reports the undo rather than just performing it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._env = dict(os.environ)
        for key in ("TASK_STATION_HOME", "CLAUDE_CONFIG_DIR", "XDG_STATE_HOME"):
            os.environ[key] = self.tmp
        os.environ.pop("TASK_STATION_GATE", None)
        os.environ.pop("TASK_STATION_AUTO_CHECKPOINT", None)
        # The engine freezes its store paths at import, so the repoint idiom every
        # store-backed suite uses (tests/test_store_sqlite.py) is repeated here.
        _ts.DATA = self.tmp
        _ts.STORE = os.path.join(self.tmp, "store")
        _ts.TASKS_DIR = os.path.join(_ts.STORE, "tasks")
        _ts.LINKS_DIR = os.path.join(_ts.STORE, "links")
        _ts.store.reset_cache()
        import config
        self.config = config
        config.set("boundary_maintenance", True)
        self.task = self._create()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        _ts.store.reset_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _create(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            _ts.main(["create", "--session", "s1", "--color", "black", "--effort", "m",
                      "--title", "boundary", "--summary", "one"])
        return _ts.load_task(_ts.get_link("s1"))

    def _decide(self, text):
        buf = io.StringIO()
        with redirect_stdout(buf):
            _ts.main(["update", "--task", "1", "--decision", text])
        return _ts.load_task(_ts.get_link("s1"))

    def _nudge(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            _ts.main(["stop-nudge", "--session", "s1"])
        raw = buf.getvalue().strip()
        if not raw:
            return ""
        import json as _json
        return _json.loads(raw)["hookSpecificOutput"]["additionalContext"]

    def test_a_quiet_boundary_says_nothing_at_all(self):
        """SILENT WHEN HEALTHY. There is no all-clear line, ever — the rule every check in
        this codebase keeps, and the one a per-turn rail most needs."""
        self.assertEqual(self._nudge(), "")

    def test_the_auto_class_runs_at_the_boundary_and_reports_what_it_did(self):
        self._decide("A ruling that runs on and on. " * 400)
        out = self._nudge()
        self.assertIn("Work boundary", out)
        self.assertIn("REPORT, NOT A REQUEST", out)
        self.assertIn("split decision 1", out)

    def test_it_names_the_undo_and_the_backup_for_every_write(self):
        """The AUTO class's entire licence is that each write is reversible with a NAMED undo.
        Performing one and not printing the way back would be the class without its warrant."""
        self._decide("A ruling that runs on and on. " * 400)
        out = self._nudge()
        self.assertIn("--restore-decision", out)
        self.assertIn("bak-pre-heal.json", out)

    def test_the_write_actually_landed(self):
        self._decide("A ruling that runs on and on. " * 400)
        before = len(self.task.get("decisions") or [])
        self._nudge()
        after = _ts.load_task(_ts.get_link("s1"))
        self.assertGreater(len(after.get("decisions") or []), before + 1)
        self.assertTrue(after.get("last_heal_ts"))

    def test_it_fires_once_per_state_not_once_per_turn(self):
        """An unheeded line every turn trains you to ignore it; a REPEATED WRITE is worse
        still. Same watermark the prompt nudges use."""
        self._decide("A ruling that runs on and on. " * 400)
        self.assertIn("Work boundary", self._nudge())
        self.assertEqual(self._nudge(), "")

    def test_nothing_happens_while_a_pickup_is_outstanding(self):
        """NOT A QUIETER PASS — NOTHING. A maintenance line printed mid-flight is the
        furniture this feature exists to remove."""
        self._decide("A ruling that runs on and on. " * 400)
        import channel as _channel
        task = _ts.load_task(_ts.get_link("s1"))
        _channel.pickup_file(task, {"id": "child-id", "seq": 2},
                             "CLOSED — ready for the gate")
        _ts.save_task(task)
        self.assertEqual(self._nudge(), "")

    def test_nothing_happens_when_the_switch_is_off(self):
        self.config.set("boundary_maintenance", False)
        self._decide("A ruling that runs on and on. " * 400)
        self.assertEqual(self._nudge(), "")

    def test_the_record_shape_gates_the_scan(self):
        """A turn that wrote nothing to the record cannot have created a finding, so it must
        not pay for a corpus scan to discover that."""
        task = _ts.load_task(_ts.get_link("s1"))
        first = _record_shape(task)
        self.assertEqual(first, _record_shape(dict(task)))
        moved = self._decide("something new")
        self.assertNotEqual(first, _record_shape(moved))

    def test_the_timing_report_writes_nothing(self):
        self._decide("A ruling that runs on and on. " * 400)
        before = _ts.load_task(_ts.get_link("s1"))
        buf = io.StringIO()
        with redirect_stdout(buf):
            _ts.main(["timing", "--session", "s1"])
        after = _ts.load_task(_ts.get_link("s1"))
        self.assertEqual(len(before.get("decisions") or []),
                         len(after.get("decisions") or []))
        self.assertIn("boundary", buf.getvalue())
        self.assertIn("writes", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
