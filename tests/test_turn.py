"""THE DRIVEN TURN — one full pass of the loop with no human between the steps.

WHAT THIS COVERS. A3 and the A-spine built the pieces: `scan` says what may start,
`invoke` spawns a child pre-attached to its own task, `exit-tick` computes done, `grade`
scores G1-G6 at A- per dimension, `memo` carries correspondence, `channel` reaches a
running child. A4 COMPOSES them — the parent runs scan -> invoke -> mechanical gate ->
grade -> release itself, and halts at a human gate instead of retrying it.

THE SEVEN THINGS OBSERVED WHILE DRIVING THAT LOOP BY HAND across seven children on
2026-08-19. Each one is a way an unattended turn acts on a lie, and each has a test here:

  1. SILENT EXIT. A child finishes and exits saying nothing, because its exit conditions
     run against the MAIN checkout and cannot pass until its own PR merges. Three
     children did exactly this. "Failed" and "unknown" are both wrong, so `silent-exit`
     is its own state with its own action.
  2. THE HAND-BACK RAIL IS A MEMO, NOT THE CHANNEL. The report contract named no channel,
     so the compliant behaviour and the useless one were identical — the report went into
     a window the parent cannot see. A memo is durable, survives the session ending, and
     lands where the gate already looks. A missing report memo is a GATE FINDING.
  3. TREE, NOT ANCESTRY. This repo squash-merges, so `git merge-base --is-ancestor`
     calls every landed branch unmerged — and the failure direction makes a driven turn
     RE-OPEN work already on main. The probe is an empty `git diff`.
  4. FOUR WAYS A GATE LIES: a false green on unstarted work; an assertion satisfied by
     something else (`Ran 0 tests / OK`, a `tail -3` swallowed by trailing stdout, a bare
     count substring-matching a bigger number); a FALSE RED from a stale installed
     plugin; and the squash case above. PIN A POSITIVE COUNT, NEVER AN ABSENCE.
  5. A FAILED WINDOW-OPEN still records the invoke and mints a session, so spawn INTENT
     is not liveness. Reconcile the two or the RUNNING column lies in both directions.
  6. CONCURRENCY. Two children in flight means two version bumps and a rebase for
     whoever lands second; three means a three-way conflict. The turn spends the
     children budget one child at a time.
  7. AN ORDER HELD ON A ROUTINE NOTICE IS EXPENSIVE. The channel gate fires at every
     turn end; holding a turn hostage for "your child closed" costs more than it
     delivers. The blocking rail is reserved for a stand-down.

`turn` is PURE over task dicts, like `loop` and `exits` — it is handed the population,
the liveness set and the evidence, and it computes. That is what makes a turn cheap
enough to run constantly, and testable with no store at all.
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

_TMP_HOME = tempfile.mkdtemp(prefix="ts-turn-")
os.environ["TASK_STATION_HOME"] = _TMP_HOME

import channel                 # noqa: E402
import exits                   # noqa: E402
import loop                    # noqa: E402
import store                   # noqa: E402
import turn                    # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)

LOOP_GATE_SPEC = os.path.join(_REPO_ROOT, "docs", "specs", "LOOP-GATE.md")

# The backlog items A4 owes an answer for. They are task 444's numbering, read off its
# record — not invented here — and step 4 of this task is satisfied only when each is
# either MECHANISED or DEFERRED WITH A REASON.
BACKLOG = ("B5", "B7", "B8", "B9", "B13", "B14")


def _t(tid, seq, title="T", status="open", deps=(), parent=None, conditions=None,
       steps=None, events=None, memos=None, grades=None, sessions=()):
    """A hand-built task blob — the same shape `test_loop` builds, plus the three feeds a
    turn reads: events (spawn intent), memos (the hand-back rail) and grades."""
    related = [{"kind": "depends-on", "id": d, "seq": None} for d in deps]
    if parent:
        related.append({"kind": "parent", "id": parent, "seq": None})
    step_list = list(steps or [])
    for n in sorted((conditions or {})):
        verdict = conditions[n]
        block = {"cmd": "check-%s" % n, "expect": ["OK"]}
        if verdict is not None:
            block["last"] = {"ts": 1000.0, "ok": verdict == "met", "status": "ran",
                             "missing": [] if verdict == "met" else ["OK"], "got": ""}
        step_list.append({"text": "step %d" % n, "done": False, "exit": block})
    return {"id": tid, "seq": seq, "title": title, "status": status,
            "related": related, "steps": step_list,
            "events": list(events or []), "memos": list(memos or []),
            "grades": list(grades or []), "sessions": list(sessions)}


def _launch(ts_=1000.0, manual=False, by=1):
    """One invoke event as `_record_launch` writes it."""
    detail = ("MANUAL LAUNCH — handed to a human by #%s" % by) if manual \
        else "invoked by #%s as implementer" % by
    return {"id": "e1", "kind": "child", "ts": ts_, "text": "%s: do the thing" % detail}


def _memo(text="report", ts_=2000.0, from_sid="child-sid", from_task=None, acks=()):
    return {"id": "m1", "ts": ts_, "from_sid": from_sid, "from_task": from_task,
            "text": text, "acks": list(acks)}


def _resolver(tasks):
    by_id = {t["id"]: t for t in tasks}
    return by_id.get


def _repoint(tmp):
    os.environ["TASK_STATION_HOME"] = tmp
    ts.DATA = tmp
    ts.STORE = os.path.join(tmp, "store")
    ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
    ts.LINKS_DIR = os.path.join(ts.STORE, "links")
    store.reset_cache()


class _Args:
    def __init__(self, **kw):
        defaults = dict(session=None, task=None, as_json=False, all=False, run=False,
                        dim=None, threshold=None, note=None, park=None, why=None,
                        no_decision=False, no_memo=False, handoff=None, from_ref=None,
                        ask=None, role=None, model=None, permission_mode=None,
                        effort=None, cwd=None, print_command=True, force=False,
                        dry_run=False, verb=None, step=None, cmd=None, expect=None,
                        depth=None, build_wait=None, untick=False)
        defaults.update(kw)
        self.__dict__.update(defaults)


class _CliCase(unittest.TestCase):
    """A real store in a temp dir, for the tests that must exercise a CLI seam."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="turn-cli-")
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

    def _dims(self, **over):
        dims = dict.fromkeys(loop.DIMENSION_KEYS, "A")
        dims.update(over)
        return ["%s=%s" % (k, v) for k, v in dims.items()]


# ======================================================================= (1) =====
# THE COMPOSED TURN — scan -> invoke -> mechanical gate -> grade -> release, plus the
# seven findings the loop must not act against.

class DrivenTurn(_CliCase):

    # -- finding 1: a silent exit is neither a failure nor an unknown -------------

    def test_a_finished_child_that_said_nothing_is_a_silent_exit(self):
        """It worked, it is gone, and it left no report. Calling that "failed" retries
        work that may be complete; calling it "unknown" stalls the loop. It is its own
        state because it has its own action: gate it WITH the missing-report finding."""
        child = _t("c1", 11, events=[_launch(), {"kind": "save", "ts": 1500.0,
                                                 "text": "checkpoint"}])
        self.assertEqual(turn.child_state(child, live=()), turn.SILENT_EXIT)

    def test_a_child_that_left_a_report_memo_is_reported_not_silent(self):
        child = _t("c1", 11, sessions=["child-sid"],
                   events=[_launch()], memos=[_memo(ts_=1500.0)])
        self.assertEqual(turn.child_state(child, live=()), turn.REPORTED)

    def test_the_parents_own_memo_is_not_the_childs_report(self):
        """A rejection the parent wrote is on the same ledger. Counting it as the
        hand-back would mark every rejected child as having reported."""
        child = _t("c1", 11, sessions=["child-sid"], events=[_launch()],
                   memos=[_memo(text="GATE REJECTED", ts_=1500.0,
                                from_sid="orch-sid", from_task="o1")])
        self.assertEqual(turn.child_state(child, live=()), turn.SILENT_EXIT)

    def test_a_live_child_is_running_whatever_its_conditions_say(self):
        child = _t("c1", 11, events=[_launch()], conditions={1: "unmet"})
        self.assertEqual(turn.child_state(child, live={11}), turn.RUNNING)

    def test_a_silent_exit_is_gated_rather_than_retried_blindly(self):
        child = _t("c1", 11, events=[_launch(), {"kind": "save", "ts": 1500.0,
                                                 "text": "checkpoint"}])
        g = turn.gate(child)
        self.assertEqual(g["state"], turn.SILENT_EXIT)
        self.assertIn("no-report", [f["code"] for f in g["findings"]])

    # -- finding 2: the hand-back rail is a memo ---------------------------------

    def test_a_missing_report_memo_is_a_gate_finding_naming_the_rail(self):
        child = _t("c1", 11, events=[_launch(), {"kind": "save", "ts": 1500.0,
                                                 "text": "x"}])
        finding = [f for f in turn.gate(child)["findings"] if f["code"] == "no-report"]
        self.assertEqual(len(finding), 1)
        self.assertIn("memo", finding[0]["line"].lower())

    # -- finding 3: tree, not ancestry ------------------------------------------

    def test_the_landed_probe_compares_trees_and_never_ancestry(self):
        """`merge-base --is-ancestor` reports EVERY squash-merged branch as unmerged, and
        the failure direction makes a driven turn re-open landed work."""
        probe = turn.landed_probe("driven-turn", "origin/main")
        self.assertIn("git diff", probe)
        self.assertIn("driven-turn", probe)
        self.assertIn("origin/main", probe)
        self.assertNotIn("merge-base", probe)
        self.assertNotIn("is-ancestor", probe)

    def test_an_empty_tree_diff_is_landed_and_a_real_one_is_not(self):
        self.assertTrue(turn.landed(""))
        self.assertTrue(turn.landed("   \n"))
        self.assertFalse(turn.landed("diff --git a/lib/board/turn.py b/lib/board/turn.py"))

    def test_red_conditions_on_an_unlanded_branch_read_as_pre_merge(self):
        """Finding 1's cause: the conditions run against the MAIN checkout, so a child's
        own work cannot turn them green until it merges. The gate must say PRE-MERGE and
        name the probe, not report the child as failed."""
        child = _t("c1", 11, sessions=["child-sid"], events=[_launch()],
                   memos=[_memo(ts_=1500.0)], conditions={1: "unmet"})
        codes = [f["code"] for f in turn.gate(child, landed=False)["findings"]]
        self.assertIn("pre-merge", codes)
        self.assertNotIn("conditions-unmet", codes)

    def test_red_conditions_on_a_landed_branch_are_a_real_failure(self):
        child = _t("c1", 11, sessions=["child-sid"], events=[_launch()],
                   memos=[_memo(ts_=1500.0)], conditions={1: "unmet"})
        codes = [f["code"] for f in turn.gate(child, landed=True)["findings"]]
        self.assertIn("conditions-unmet", codes)
        self.assertNotIn("pre-merge", codes)

    # -- finding 4: the four ways a gate lies -----------------------------------

    def test_a_suite_that_ran_zero_tests_is_not_green(self):
        """`unittest discover -k <missing>` prints "Ran 0 tests" then "OK" and exits 0.
        An assertion on OK alone is satisfied by the absence of the test it was written
        to protect."""
        ok, why = turn.suite_green("Ran 0 tests in 0.000s\n\nOK\n")
        self.assertFalse(ok)
        self.assertIn("0", why)

    def test_a_positive_count_with_ok_is_green(self):
        ok, _why = turn.suite_green("Ran 5013 tests in 61.2s\n\nOK\n")
        self.assertTrue(ok)

    def test_a_positive_count_that_failed_is_not_green(self):
        ok, why = turn.suite_green("Ran 12 tests in 1.0s\n\nFAILED (failures=1)\n")
        self.assertFalse(ok)
        self.assertIn("FAILED", why)

    def test_no_count_at_all_is_not_green_because_uncountable_is_never_zero(self):
        ok, why = turn.suite_green("ModuleNotFoundError: No module named 'turn'\n")
        self.assertFalse(ok)
        self.assertTrue(why)

    def test_a_tail_swallowed_shape_is_named_by_the_lint(self):
        codes = [f["code"] for f in turn.condition_lint(
            "python3 -m unittest discover 2>&1 | tail -3", ["OK"])]
        self.assertIn("tail-swallow", codes)

    def test_a_bare_count_expect_is_named_by_the_lint(self):
        """"5013" is a substring of "15013" and of "5013 errors". A count must be pinned
        with the words around it."""
        codes = [f["code"] for f in turn.condition_lint(
            "python3 -m unittest discover -t . -s tests 2>&1", ["5013"])]
        self.assertIn("bare-count", codes)

    def test_an_absence_assertion_is_named_by_the_lint(self):
        for expect in (["no failures"], ["0 errors"], ["not found"], ["none"]):
            codes = [f["code"] for f in turn.condition_lint("some-check", expect)]
            self.assertIn("absence-assertion", codes, expect)

    def test_a_path_test_guarding_a_test_run_is_named_by_the_lint(self):
        """LIVE CASE, 2026-08-21. Step 5 of #531 was registered as
        `test -f skills/judge/SKILL.md && python3 -m unittest …`. The skill was later
        renamed judge -> grade, the path test failed, `&&` short-circuited, the tests never
        ran, and the command printed NOTHING — so the transcript said neither which half
        failed nor why. A closed, fully-graded, released track read as 4 of 5 conditions
        met, and unattended the loop would have parked finished work."""
        codes = [f["code"] for f in turn.condition_lint(
            "test -f skills/judge/SKILL.md && python3 -m unittest tests.test_loop",
            ["OK"])]
        self.assertIn("path-test-and", codes)

    def test_every_bracket_spelling_of_the_same_guard_is_named(self):
        for cmd in ("[ -f skills/x/SKILL.md ] && python3 -m unittest tests.test_loop",
                    "[[ -d lib/board ]] && python3 -m unittest tests.test_loop",
                    "test -e VERSION && make check",
                    "test ! -f stale.lock && python3 -m unittest tests.test_loop"):
            codes = [f["code"] for f in turn.condition_lint(cmd, ["OK"])]
            self.assertIn("path-test-and", codes, cmd)

    def test_the_diagnosis_names_the_empty_output_not_just_the_shape(self):
        found = [f for f in turn.condition_lint(
            "test -f a && python3 -m unittest tests.test_loop", ["OK"])
            if f["code"] == "path-test-and"]
        self.assertEqual(len(found), 1)
        self.assertIn("empty", found[0]["line"].lower())

    def test_a_cd_before_the_command_is_NOT_a_path_test(self):
        """`cd <dir> && <cmd>` is the ordinary way to run a condition against a checkout,
        and `cd` FAILS LOUDLY when the directory is gone. Flagging it would make the lint
        something people switch off."""
        codes = [f["code"] for f in turn.condition_lint(
            "cd ~/Workspace-Other/task-station && python3 -m unittest tests.test_turn",
            ["OK", "Ran"])]
        self.assertNotIn("path-test-and", codes)

    def test_a_string_test_is_NOT_flagged(self):
        """The check is about a PATH that a rename can move. `-z`/`-n` test a variable
        the same command just set, and that is not the failure mode observed."""
        codes = [f["code"] for f in turn.condition_lint(
            'test -z "$SKIP" && python3 -m unittest tests.test_loop', ["OK"])]
        self.assertNotIn("path-test-and", codes)

    def test_a_path_test_joined_by_semicolon_is_NOT_flagged(self):
        """`;` does not short-circuit: the command runs and prints its output whatever the
        test said. The defect is the AND, not the test."""
        codes = [f["code"] for f in turn.condition_lint(
            "test -f a ; python3 -m unittest tests.test_loop", ["OK"])]
        self.assertNotIn("path-test-and", codes)

    def test_the_repaired_shape_passes_the_lint_clean(self):
        """The conditions on this very task were repaired on 2026-08-19 to remove a
        `tail -3` and a bare count. The lint must not flag what replaced them, or the
        next repair will be to switch the lint off."""
        self.assertEqual(turn.condition_lint(
            "cd ~/Workspace-Other/task-station && python3 -m unittest "
            "tests.test_turn.DrivenTurn 2>&1 | rg '^(OK|FAILED|Ran [0-9]+ tests)'",
            ["OK", "Ran"]), [])

    def test_a_false_green_on_unstarted_work_is_refused(self):
        """Nothing has been invoked, so there is nothing to grade. A gate that scores
        this is the cheapest possible lie."""
        child = _t("c1", 11)
        g = turn.gate(child)
        self.assertEqual(g["state"], turn.UNSTARTED)
        self.assertIn("unstarted", [f["code"] for f in g["findings"]])
        self.assertFalse(g["gradeable"])

    def test_a_stale_installed_plugin_is_named_as_a_false_red_source(self):
        """B14's half that matters to the gate: a suite run against a stale INSTALLED
        plugin reports red for a reason having nothing to do with the work."""
        f = turn.stale_install("3.14.0", "3.12.0")
        self.assertIsNotNone(f)
        self.assertEqual(f["code"], "stale-install")
        self.assertIn("3.12.0", f["line"])
        self.assertIsNone(turn.stale_install("3.14.0", "3.14.0"))

    def test_the_installed_version_is_picked_numerically_not_alphabetically(self):
        """Sixteen versions sit in a real plugin cache and `sorted()` puts 3.9.0 after
        3.12.0. A probe whose job is catching a stale install must not report the wrong
        version as the installed one."""
        self.assertEqual(max(["3.12.0", "3.9.0", "3.13.0"], key=turn._version_key),
                         "3.13.0")
        self.assertLess(turn._version_key("3.9.0"), turn._version_key("3.12.0"))

    # -- B7 / B9 / B13: the gate-machinery items A4 mechanises -------------------

    def test_a_condition_that_dies_on_shell_syntax_is_refused_before_it_is_stored(self):
        """B7. The P7A truncation shipped a registered command that could not run; the
        check is a PARSE, never an execution — registering a condition must not have
        side effects."""
        self.assertIsNotNone(turn.shell_syntax_error("echo 'unterminated"))
        self.assertIsNone(turn.shell_syntax_error("python3 -m unittest x 2>&1 | rg OK"))

    def test_exit_add_refuses_a_lying_shape_and_records_nothing(self):
        t = self._task("child", "do the thing")
        out, code = self._out(ts.cmd_exit_add, _Args(
            task=str(t["seq"]), step=1, cmd="pytest -q 2>&1 | tail -3", expect=["5013"]))
        self.assertEqual(code, 2)
        self.assertIn("tail-swallow", out)
        self.assertIn("bare-count", out)
        self.assertIsNone(ts.load_task(t["id"])["steps"][0].get("exit"))

    def test_force_stores_a_flagged_condition_and_says_it_did(self):
        t = self._task("child", "do the thing")
        out, code = self._out(ts.cmd_exit_add, _Args(
            task=str(t["seq"]), step=1, cmd="pytest -q 2>&1 | tail -3", expect=["OK"],
            force=True))
        self.assertIsNone(code)
        self.assertIn("tail-swallow", out)
        self.assertIsNotNone(ts.load_task(t["id"])["steps"][0].get("exit"))

    def test_a_gate_number_with_no_measuring_command_is_a_finding(self):
        """B9. "Phase 4 went 58 -> 81" is a number that rots silently; the same number
        with the command that produced it says so the next time anybody looks."""
        steps = [{"text": "suite is 5013 green", "done": False},
                 {"text": "the docs read well", "done": False}]
        found = turn.number_without_command(steps)
        self.assertEqual([f["step"] for f in found], [1])
        self.assertIn("5013", found[0]["line"])

    def test_a_number_that_carries_its_command_is_not_a_finding(self):
        steps = [{"text": "suite is 5013 green", "done": False,
                  "exit": {"cmd": "python3 -m unittest discover", "expect": ["Ran 5013"]}}]
        self.assertEqual(turn.number_without_command(steps), [])

    def test_task_refs_years_and_versions_are_not_gate_numbers(self):
        steps = [{"text": "see task #444 and 2026-08-19, ships in 3.14.0", "done": False}]
        self.assertEqual(turn.number_without_command(steps), [])

    def test_pending_ack_debt_is_a_turn_input_not_background_noise(self):
        """B13. Twenty-two memos sat unacked while the loop ran; the loop never knew."""
        child = _t("c1", 11, sessions=["child-sid"], events=[_launch()],
                   memos=[_memo(ts_=1500.0),
                          _memo(text="main moved", ts_=1600.0, from_sid="orch-sid",
                                from_task="o1")])
        codes = [f["code"] for f in turn.gate(child, landed=True)["findings"]]
        self.assertIn("pending-acks", codes)

    def test_an_acked_memo_is_not_debt(self):
        child = _t("c1", 11, sessions=["child-sid"], events=[_launch()],
                   memos=[_memo(ts_=1500.0, acks=[{"session": "child-sid"}])],
                   conditions={1: "met"})
        codes = [f["code"] for f in turn.gate(child, landed=True)["findings"]]
        self.assertNotIn("pending-acks", codes)

    # -- finding 5: spawn intent is not liveness --------------------------------

    def test_an_invoked_child_that_never_came_up_is_relaunched_not_gated(self):
        """A failed window-open still records the invoke and still mints a session. The
        child never took a turn, so there is nothing to grade and nothing running."""
        child = _t("c1", 11, events=[_launch()])
        self.assertEqual(turn.child_state(child, live=()), turn.SPAWN_FAILED)

    def test_a_manual_launch_is_its_own_state_and_is_never_counted_as_running(self):
        child = _t("c1", 11, events=[_launch(manual=True)])
        self.assertEqual(turn.child_state(child, live=()), turn.MANUAL)

    def test_the_turn_relaunches_a_spawn_that_did_not_come_up(self):
        orch = _t("o1", 1)
        child = _t("c1", 11, parent="o1", events=[_launch()])
        p = turn.plan(orch, [child], live=())
        self.assertEqual([(a["action"], a["seq"]) for a in p["actions"]],
                         [(turn.RELAUNCH, 11)])
        self.assertIn("invoke", p["actions"][0]["command"])

    # -- finding 6: the concurrency budget --------------------------------------

    def test_the_turn_invokes_one_child_per_pass_even_when_three_are_ready(self):
        """Two children in flight is two version bumps and a rebase; three is a
        three-way conflict. The budget is spent one child at a time."""
        orch = _t("o1", 1)
        kids = [_t("c%d" % n, 10 + n, parent="o1") for n in (1, 2, 3)]
        p = turn.plan(orch, kids, live=())
        invokes = [a for a in p["actions"] if a["action"] == turn.INVOKE]
        self.assertEqual(len(invokes), 1)
        self.assertEqual(invokes[0]["seq"], 11)

    def test_a_full_children_budget_invokes_nothing_and_says_why(self):
        orch = _t("o1", 1)
        kids = [_t("c%d" % n, 10 + n, parent="o1", events=[_launch()])
                for n in (1, 2, 3)]
        kids.append(_t("c4", 14, parent="o1"))          # unblocked, unstarted, refused
        p = turn.plan(orch, kids, live={11, 12, 13}, cap=3)
        self.assertEqual([a for a in p["actions"] if a["action"] == turn.INVOKE], [])
        self.assertEqual(p["halt"], turn.HALT_BUDGET)
        self.assertIn("loop_children_max", " ".join(turn.lines(p)))

    def test_the_budget_counts_liveness_not_records(self):
        """A crashed child holds a slot forever if the cap counts records."""
        orch = _t("o1", 1)
        kids = [_t("c%d" % n, 10 + n, parent="o1", events=[_launch()])
                for n in (1, 2, 3)]
        p = turn.plan(orch, kids, live=(), cap=3)
        self.assertEqual(p["budget"]["running"], [])

    # -- finding 7: the blocking rail is reserved for a stand-down --------------

    def test_a_routine_notice_no_longer_holds_a_turn_hostage(self):
        task = {"id": "t1", "seq": 1, "orders": []}
        order, err = channel.order_queue(task, channel.ORDER_MEMO, "your child closed",
                                         "sid-1", routine=True)
        self.assertIsNone(err)
        self.assertEqual(channel.deliverable(task, "sid-1"), [])
        self.assertIn(order, channel.notices(task, "sid-1"))
        self.assertIn(order, channel.orders_for(task, "sid-1"))

    def test_a_memo_somebody_wrote_still_blocks_a_running_child(self):
        """The channel exists because nobody types into an invoked child again. Narrowing
        the rail must not take that away — only the bookkeeping rides the ledger."""
        task = {"id": "t1", "seq": 1, "orders": []}
        order, err = channel.order_queue(task, channel.ORDER_MEMO, "main moved, rebase",
                                         "sid-1")
        self.assertIsNone(err)
        self.assertEqual(channel.deliverable(task, "sid-1"), [order])

    def test_a_child_closing_is_routine_by_construction(self):
        """Finding 7's actual case: the notice the loop mints about another task's
        lifecycle. It must be routine where it is WRITTEN, not classified by a reader."""
        parent = self._task("parent")
        child = self._task("child")
        child = ts.load_task(child["id"])
        child["related"] = [{"kind": "parent", "id": parent["id"],
                             "seq": parent["seq"]}]
        ts.save_task(child)
        ts.report_to_parent(ts.load_task(child["id"]), "closed")
        memos = ts.load_task(parent["id"])["memos"]
        self.assertEqual(len(memos), 1)
        self.assertTrue(memos[0].get(channel.ROUTINE_FIELD))

    def test_a_stand_down_still_blocks_because_it_is_not_routine(self):
        task = {"id": "t1", "seq": 1, "orders": []}
        order, err = channel.order_queue(task, channel.ORDER_STAND_DOWN, "wrap up",
                                         "sid-1")
        self.assertIsNone(err)
        self.assertEqual(channel.deliverable(task, "sid-1"), [order])

    def test_a_moved_exit_condition_still_blocks_because_done_is_computed_from_it(self):
        task = {"id": "t1", "seq": 1, "orders": []}
        order, err = channel.order_queue(task, channel.ORDER_SPEC, "step 2 moved",
                                         "sid-1")
        self.assertIsNone(err)
        self.assertEqual(channel.deliverable(task, "sid-1"), [order])

    def test_a_stand_down_blocks_even_when_the_loop_minted_it(self):
        """Two kinds hold a turn whoever wrote them: stop, and the target moved. Neither
        is bookkeeping, so routine authorship does not excuse them."""
        task = {"id": "t1", "seq": 1, "orders": []}
        order, err = channel.order_queue(task, channel.ORDER_STAND_DOWN, "wrap up",
                                         "sid-1", routine=True)
        self.assertIsNone(err)
        self.assertEqual(channel.deliverable(task, "sid-1"), [order])
        self.assertEqual(set(channel.ALWAYS_BLOCKING),
                         {channel.ORDER_STAND_DOWN, channel.ORDER_SPEC})

    # -- the composition itself -------------------------------------------------

    def test_the_agenda_is_gate_before_invoke_so_a_slot_frees_first(self):
        """Grading what came back can release a wave and free a child slot; invoking
        first spends the slot the gate was about to return."""
        orch = _t("o1", 1)
        back = _t("c1", 11, parent="o1", sessions=["child-sid"], events=[_launch()],
                  memos=[_memo(ts_=1500.0)], conditions={1: "met"})
        fresh = _t("c2", 12, parent="o1")
        p = turn.plan(orch, [back, fresh], live=(), cap=3)
        order = [a["action"] for a in p["actions"]]
        self.assertIn(turn.GATE, order)
        self.assertIn(turn.INVOKE, order)
        self.assertLess(order.index(turn.GATE), order.index(turn.INVOKE))

    def test_every_action_carries_the_command_that_performs_it(self):
        """"No human between the steps" is exactly this: each step names the command a
        driver runs, so nothing in the turn needs a person to translate it."""
        orch = _t("o1", 1)
        back = _t("c1", 11, parent="o1", sessions=["child-sid"], events=[_launch()],
                  memos=[_memo(ts_=1500.0)], conditions={1: "met"})
        fresh = _t("c2", 12, parent="o1")
        p = turn.plan(orch, [back, fresh], live=(), cap=3)
        self.assertTrue(p["actions"])
        for a in p["actions"]:
            self.assertTrue(a["command"], a)
            self.assertTrue(a["why"], a)
            self.assertIn("task-station", a["command"])

    def test_the_five_steps_are_the_action_vocabulary(self):
        for verb in (turn.INVOKE, turn.GATE, turn.GRADE, turn.RELEASE, turn.PARK):
            self.assertIn(verb, turn.ACTIONS)

    def test_a_gated_child_gets_the_mechanical_command_then_the_grade(self):
        orch = _t("o1", 1)
        back = _t("c1", 11, parent="o1", sessions=["child-sid"], events=[_launch()],
                  memos=[_memo(ts_=1500.0)], conditions={1: "met"})
        p = turn.plan(orch, [back], live=())
        acts = {a["action"]: a for a in p["actions"]}
        self.assertIn(turn.GATE, acts)
        self.assertIn(turn.GRADE, acts)
        self.assertIn("exit-tick", acts[turn.GATE]["command"])
        self.assertIn("grade --task 11", acts[turn.GRADE]["command"])
        self.assertIn("--dim G1=", acts[turn.GRADE]["command"])

    def test_an_accepted_child_is_released_by_closing_it(self):
        """Release is what unblocks the next wave: a settled predecessor releases its
        dependents, and closing the child is what settles it."""
        orch = _t("o1", 1)
        done = _t("c1", 11, parent="o1", sessions=["child-sid"], events=[_launch()],
                  memos=[_memo(ts_=1500.0)], conditions={1: "met"},
                  grades=[{"ts": 3000.0, "dims": dict.fromkeys(loop.DIMENSION_KEYS, "A"),
                           "threshold": "A-", "accepted": True, "verdict": "ACCEPTED"}])
        p = turn.plan(orch, [done], live=())
        release = [a for a in p["actions"] if a["action"] == turn.RELEASE]
        self.assertEqual(len(release), 1)
        self.assertIn("done --task 11", release[0]["command"])

    def test_a_running_child_is_waited_on_not_re_invoked(self):
        orch = _t("o1", 1)
        child = _t("c1", 11, parent="o1", events=[_launch()])
        p = turn.plan(orch, [child], live={11})
        self.assertEqual([a["action"] for a in p["actions"]], [turn.WAIT])
        self.assertEqual(p["halt"], turn.HALT_WORKING)

    def test_a_complete_wave_halts_clean_with_nothing_to_do(self):
        orch = _t("o1", 1)
        child = _t("c1", 11, parent="o1", status="closed", events=[_launch()])
        p = turn.plan(orch, [child], live=())
        self.assertEqual(p["actions"], [])
        self.assertEqual(p["halt"], turn.HALT_COMPLETE)

    def test_an_orchestrator_with_no_children_says_the_plan_is_unbuilt(self):
        p = turn.plan(_t("o1", 1), [], live=())
        self.assertEqual(p["halt"], turn.HALT_EMPTY)

    def test_the_turn_carries_the_scan_it_ran_so_the_two_cannot_disagree(self):
        """The turn RUNS the scan rather than being handed a summary of one — the
        composition is the point, and two computations of the wave would drift."""
        orch = _t("o1", 1)
        child = _t("c1", 11, parent="o1")
        p = turn.plan(orch, [child], live=())
        self.assertEqual(p["scan"].get("totals", {}).get("total"), 1)
        self.assertEqual(p["scan"]["stop"], loop.READY)

    def test_a_blocked_dependent_is_never_offered_for_invoke(self):
        orch = _t("o1", 1)
        first = _t("c1", 11, parent="o1")
        second = _t("c2", 12, parent="o1", deps=["c1"])
        p = turn.plan(orch, [first, second], live=())
        invokes = [a["seq"] for a in p["actions"] if a["action"] == turn.INVOKE]
        self.assertEqual(invokes, [11])

    # -- the CLI seam -----------------------------------------------------------

    def test_the_turn_command_prints_the_next_action_and_its_command(self):
        orch = self._task("orchestrator")
        child = self._task("child")
        child = ts.load_task(child["id"])
        child["related"] = [{"kind": "parent", "id": orch["id"], "seq": orch["seq"]}]
        ts.save_task(child)
        out, code = self._out(ts.cmd_turn, _Args(task=str(orch["seq"])))
        self.assertIsNone(code)
        self.assertIn("invoke", out)
        self.assertIn("task-station invoke --task %s" % child["seq"], out)

    def test_the_turn_command_json_and_text_render_the_same_computation(self):
        import json as _json
        orch = self._task("orchestrator")
        child = self._task("child")
        child = ts.load_task(child["id"])
        child["related"] = [{"kind": "parent", "id": orch["id"], "seq": orch["seq"]}]
        ts.save_task(child)
        text, _ = self._out(ts.cmd_turn, _Args(task=str(orch["seq"])))
        raw, _ = self._out(ts.cmd_turn, _Args(task=str(orch["seq"]), as_json=True))
        self.assertTrue(raw.strip().startswith("{"), raw[:200])
        doc = _json.loads(raw)
        self.assertEqual(doc["halt"], None)
        self.assertEqual([a["action"] for a in doc["actions"]], [turn.INVOKE])
        self.assertIn(doc["actions"][0]["command"], text)

    def test_the_turn_command_writes_nothing_and_runs_no_shell(self):
        """A planner cheap enough to run constantly, for the same reason `scan` is: no
        model, and no shell either."""
        orch = self._task("orchestrator")
        before = ts.load_task(orch["id"])["updated_ts"]
        self._out(ts.cmd_turn, _Args(task=str(orch["seq"])))
        self.assertEqual(ts.load_task(orch["id"])["updated_ts"], before)

    # -- step 4: the backlog items owe an answer -------------------------------

    def test_the_loop_gate_spec_answers_for_every_backlog_item(self):
        """B5/B7/B8/B9/B13/B14 are task 444's numbering. Each must be MECHANISED or
        DEFERRED — and a deferral with no reason is how an item disappears."""
        with open(LOOP_GATE_SPEC, encoding="utf-8") as f:
            spec = f.read()
        for item in BACKLOG:
            self.assertIn(item, spec, item)
            block = spec.split("\n%s " % item, 1)
            self.assertEqual(len(block), 2, "%s needs its own entry" % item)
            head = block[1].split("\n\n", 1)[0]
            self.assertTrue("MECHANISED" in head or "DEFERRED" in head,
                            "%s: %r" % (item, head[:120]))
            if "DEFERRED" in head:
                self.assertIn("because", head.lower(), item)

    def test_the_spec_names_all_seven_findings_it_was_written_against(self):
        with open(LOOP_GATE_SPEC, encoding="utf-8") as f:
            spec = f.read().lower()
        for phrase in ("silent exit", "memo", "squash", "positive count",
                       "liveness", "loop_children_max", "stand-down"):
            self.assertIn(phrase, spec, phrase)


# ======================================================================= (2) =====
# THE REJECTION MEMO — a rejection that goes where the child can read it.

class RejectionMemo(_CliCase):

    def test_a_rejection_lands_on_the_child_as_a_memo(self):
        t = self._task("child")
        out, code = self._out(ts.cmd_grade, _Args(task=str(t["seq"]),
                                                  dim=self._dims(G4="C")))
        self.assertEqual(code, 1)
        memos = ts.load_task(t["id"]).get("memos") or []
        self.assertEqual(len(memos), 1)
        self.assertIn("REJECTED", memos[0]["text"])
        self.assertIn("memo", out.lower())

    def test_the_memo_names_the_failed_dimension_and_its_grade(self):
        t = self._task("child")
        self._out(ts.cmd_grade, _Args(task=str(t["seq"]), dim=self._dims(G4="C")))
        memos = ts.load_task(t["id"]).get("memos") or []
        self.assertEqual(len(memos), 1)
        text = memos[0]["text"]
        self.assertIn("G4", text)
        self.assertIn("C", text)
        self.assertIn(loop.DIMENSION_TITLES["G4"], text)

    def test_the_memo_names_an_ungraded_dimension_too(self):
        """Two ways to not pass, and they call for different work: a low grade is the
        child's to fix, an ungraded dimension is the judge's."""
        t = self._task("child")
        self._out(ts.cmd_grade, _Args(task=str(t["seq"]), dim=["G1=A"]))
        memos = ts.load_task(t["id"]).get("memos") or []
        self.assertEqual(len(memos), 1)
        text = memos[0]["text"]
        self.assertIn("ungraded", text.lower())
        self.assertIn("G6", text)

    def test_an_acceptance_sends_no_memo(self):
        t = self._task("child")
        self._out(ts.cmd_grade, _Args(task=str(t["seq"]), dim=self._dims()))
        self.assertEqual(ts.load_task(t["id"]).get("memos") or [], [])

    def test_a_park_says_it_does_not_come_back_rather_than_asking_for_a_retry(self):
        t = self._task("child")
        out, code = self._out(ts.cmd_grade, _Args(
            task=str(t["seq"]), park="human-gate", why="Ryan must rule on the schema"))
        self.assertEqual(code, 4)
        memos = ts.load_task(t["id"]).get("memos") or []
        self.assertEqual(len(memos), 1)
        text = memos[0]["text"]
        self.assertIn("PARKED", text)
        self.assertIn("human-gate", text)
        self.assertNotIn("retry", text.lower())
        self.assertIn("Ryan must rule", text)

    def test_no_memo_opts_out_for_a_grader_that_is_only_recording(self):
        t = self._task("child")
        self._out(ts.cmd_grade, _Args(task=str(t["seq"]), dim=self._dims(G4="C"),
                                       no_memo=True))
        self.assertEqual(ts.load_task(t["id"]).get("memos") or [], [])

    def test_the_memo_is_recorded_as_an_event_so_the_rail_is_auditable(self):
        t = self._task("child")
        self._out(ts.cmd_grade, _Args(task=str(t["seq"]), dim=self._dims(G4="C")))
        kinds = [e["kind"] for e in ts.load_task(t["id"]).get("events") or []]
        self.assertIn("memo", kinds)

    def test_the_rejection_text_is_composed_from_the_verdict_alone(self):
        v = loop.verdict({"G1": "A", "G2": "A", "G3": "A", "G4": "C", "G5": "A",
                          "G6": "A"}, "A-")
        text = turn.rejection_memo(v, ref=11, note="the suite was never run")
        self.assertIn("G4", text)
        self.assertIn("the suite was never run", text)
        self.assertIn("#11", text)

    def test_the_report_contract_names_the_rail_the_gate_reads(self):
        """The contract asked for a report and named no channel, so the compliant
        behaviour and the useless one were identical — three children wrote good reports
        into windows the parent could not see."""
        prompt = ts._child_prompt("do the thing", "implementer",
                                  "what changed file by file", ref=11)
        self.assertIn("memo", prompt.lower())
        self.assertIn("task-station memo send --task 11", prompt)

    def test_the_rail_is_named_even_when_the_role_carries_no_contract(self):
        prompt = ts._child_prompt("do the thing", None, None, ref=11)
        self.assertIn("task-station memo send --task 11", prompt)

    def test_a_prompt_with_no_task_ref_is_unchanged(self):
        self.assertEqual(ts._child_prompt("do the thing", None, None), "do the thing")


# ======================================================================= (3) =====
# BOUNDED RETRIES — the loop stops asking.

class BoundedRetries(_CliCase):

    def _graded(self, n, accepted=False, park=None):
        entry = {"ts": 1000.0, "dims": {"G1": "C"}, "threshold": "A-",
                 "accepted": accepted, "verdict": "REJECTED"}
        if park:
            entry["park"] = park
        return [dict(entry) for _ in range(n)]

    def test_a_rejection_with_retries_left_asks_for_a_retry(self):
        child = _t("c1", 11, grades=self._graded(1))
        v = loop.verdict({"G1": "C"}, "A-")
        d = turn.retry_decision(child, v, retry_max=2)
        self.assertEqual(d["do"], turn.RETRY)
        self.assertEqual(d["left"], 1)

    def test_the_last_retry_spent_parks_instead_of_retrying(self):
        child = _t("c1", 11, grades=self._graded(2))
        v = loop.verdict({"G1": "C"}, "A-")
        d = turn.retry_decision(child, v, retry_max=2)
        self.assertEqual(d["do"], turn.PARK)
        self.assertEqual(d["reason"], "retries-exhausted")

    def test_a_human_gate_is_never_retried_however_much_budget_is_left(self):
        """The blocker taxonomy's one hard rule. Iterating cannot resolve a decision
        that is not the loop's to make, so the loop must stop asking."""
        child = _t("c1", 11, grades=[])
        v = loop.verdict({"G1": "C"}, "A-")
        d = turn.retry_decision(child, v, retry_max=5, park="human-gate")
        self.assertEqual(d["do"], turn.PARK)
        self.assertEqual(d["reason"], "human-gate")
        self.assertEqual(d["left"], 5)

    def test_an_acceptance_is_neither_a_retry_nor_a_park(self):
        child = _t("c1", 11)
        v = loop.verdict(dict.fromkeys(loop.DIMENSION_KEYS, "A"), "A-")
        self.assertEqual(turn.retry_decision(child, v, retry_max=2)["do"], turn.RELEASE)

    def test_an_already_parked_child_is_never_offered_a_retry_again(self):
        child = _t("c1", 11, grades=self._graded(1) + self._graded(1, park="human-gate"))
        v = loop.verdict({"G1": "C"}, "A-")
        d = turn.retry_decision(child, v, retry_max=5)
        self.assertEqual(d["do"], turn.PARK)
        self.assertEqual(d["reason"], "human-gate")

    def test_a_park_does_not_burn_a_retry_the_child_never_got(self):
        child = _t("c1", 11, grades=self._graded(1) + self._graded(1, park="human-gate"))
        self.assertEqual(loop.retries_left(child, 2), 1)

    def test_the_turn_halts_at_a_parked_child_rather_than_looping_on_it(self):
        orch = _t("o1", 1)
        child = _t("c1", 11, parent="o1", events=[_launch()],
                   grades=self._graded(1, park="human-gate"))
        p = turn.plan(orch, [child], live=())
        self.assertEqual(p["halt"], turn.HALT_PARKED)
        self.assertEqual([a["action"] for a in p["actions"]], [])
        self.assertIn("human-gate", " ".join(turn.lines(p)))

    def test_the_park_action_carries_the_command_that_records_it(self):
        orch = _t("o1", 1)
        child = _t("c1", 11, parent="o1", sessions=["child-sid"], events=[_launch()],
                   memos=[_memo(ts_=1500.0)], conditions={1: "met"},
                   grades=self._graded(2))
        p = turn.plan(orch, [child], live=(), retry_max=2)
        park = [a for a in p["actions"] if a["action"] == turn.PARK]
        self.assertEqual(len(park), 1)
        self.assertIn("grade --task 11 --park retries-exhausted", park[0]["command"])
        self.assertIn("--why", park[0]["command"])

    def test_a_child_with_retries_left_is_asked_to_iterate_in_band(self):
        orch = _t("o1", 1)
        child = _t("c1", 11, parent="o1", sessions=["child-sid"], events=[_launch()],
                   memos=[_memo(ts_=1500.0)], conditions={1: "met"},
                   grades=self._graded(1))
        p = turn.plan(orch, [child], live=(), retry_max=2)
        self.assertIn(turn.GRADE, [a["action"] for a in p["actions"]])
        self.assertNotIn(turn.PARK, [a["action"] for a in p["actions"]])

    def test_the_retry_budget_default_comes_from_config_not_from_a_literal(self):
        import config as _config
        child = _t("c1", 11, grades=self._graded(_config.loop_retry_max()))
        v = loop.verdict({"G1": "C"}, "A-")
        self.assertEqual(turn.retry_decision(child, v)["do"], turn.PARK)

    def test_grade_exit_three_is_the_drivers_park_branch(self):
        t = self._task("child")
        for _ in range(2):
            self._out(ts.cmd_grade, _Args(task=str(t["seq"]), dim=self._dims(G4="C")))
        _out, code = self._out(ts.cmd_grade, _Args(task=str(t["seq"]),
                                                   dim=self._dims(G4="C")))
        self.assertEqual(code, 3)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# THE PICKUP RAIL — the loop could hand work DOWN and had no way to notice it come back.
#
# Found by running the loop for real, 2026-08-21. A child finished, shipped a release,
# opened a PR and filed its report as a memo on its own task — exactly what the contract
# asked for — and the report sat unread for SEVEN HOURS. Nothing was broken and nothing was
# lost. The parent simply never looks where the child was told to write.
#
# THREE CAUSES, and only the second was the loop's fault:
#   1. The report landed on the CHILD, which is what the brief asked for — but the
#      awaiting-your-ack rail fires only for the task the READING session is attached to,
#      so a parent attached to the orchestrator is never told a memo arrived on a child.
#      The rail was durable and invisible in exactly one direction.
#   2. `child_state` READ LIVENESS AND NOT REPORTS. The child never exited, so its session
#      sat idle in the worktree and `turn` printed WAIT — "a live session is attached, the
#      loop is working, not stuck". True sentence, wrong conclusion: the work had been done
#      for hours. An idle-but-alive child that has already reported was indistinguishable
#      from one still thinking.
#   3. EVEN A GATE RUN WOULD NOT HAVE RELEASED IT, and that part was correct. The child's
#      conditions read origin/main, so they stayed red until a human merged the PR. The
#      true state was DONE PENDING MERGE, and the vocabulary had no word for it.
#
# Track A's rule 4 said a memo "lands where the gate looks". That was asserted and never
# tested — the gate looked at child STATE, not child MEMOS — which is the same class of
# defect as a fix agreed and never built.
# ---------------------------------------------------------------------------

class ThePickupRail(unittest.TestCase):

    def _reported_and_live(self, **kw):
        """The live shape: invoked, filed an UNACKED report, session still attached."""
        return _t("c1", 11, parent="orch", events=[_launch()],
                  memos=[_memo(from_sid="child-sid", acks=())],
                  sessions=("child-sid",), **kw)

    # -- (a) an unacked report outranks liveness -----------------------------------

    def test_a_live_child_that_has_reported_reads_REPORTED_not_RUNNING(self):
        child = self._reported_and_live(conditions={1: "unmet"})
        self.assertEqual(turn.child_state(child, live={11}), turn.REPORTED)

    def test_a_live_child_that_has_NOT_reported_still_reads_RUNNING(self):
        """The negative control that keeps WAIT meaningful: a child genuinely still
        working must not be gated out from under itself."""
        child = _t("c1", 11, parent="orch", events=[_launch()], sessions=("child-sid",))
        self.assertEqual(turn.child_state(child, live={11}), turn.RUNNING)

    def test_an_ACKED_report_returns_a_live_child_to_RUNNING(self):
        """Once the parent has engaged the report, a still-live child is working again —
        otherwise a graded-and-retried child would be gated forever on its old report."""
        child = _t("c1", 11, parent="orch", events=[_launch()],
                   memos=[_memo(acks=[{"sid": "parent-sid", "ts": 2100.0}])],
                   sessions=("child-sid",))
        self.assertEqual(turn.child_state(child, live={11}), turn.RUNNING)

    def test_a_parked_child_is_never_dragged_back_by_its_report(self):
        """A park beats the trail whatever the trail says — that rule predates this one and
        outranks it. Probed with the child NOT live, so the answer is about the park and the
        report rather than about liveness (which has always outranked a park: a parked child
        whose session is running really is running)."""
        child = self._reported_and_live()
        child["grades"] = [{"ts": 2050.0, "park": "human-gate", "accepted": False}]
        self.assertEqual(turn.child_state(child, live=()), turn.PARKED)

    def test_the_turn_GATES_the_idle_reporter_instead_of_waiting_on_it(self):
        orch = _t("orch", 10, title="orchestrator")
        child = self._reported_and_live(conditions={1: "unmet"})
        p = turn.plan(orch, [child], live={11})
        acts = [a["action"] for a in p["actions"]]
        self.assertIn(turn.GATE, acts)
        self.assertNotIn(turn.WAIT, acts)

    # -- (b) done pending merge ---------------------------------------------------

    def test_a_merge_gated_condition_is_declarable(self):
        child = _t("c1", 11, steps=[{"text": "s", "done": False,
                                     "exit": {"cmd": "c", "expect": ["OK"],
                                              "merge_gated": True}}])
        probe = getattr(exits, "merge_gated", None)
        self.assertIsNotNone(probe, "exits.merge_gated does not exist")
        self.assertTrue(probe(child["steps"][0]))

    def test_an_unmet_merge_gated_condition_reads_DONE_PENDING_MERGE(self):
        child = _t("c1", 11, parent="orch", events=[_launch()],
                   memos=[_memo()], sessions=("child-sid",),
                   steps=[{"text": "s", "done": False,
                           "exit": {"cmd": "c", "expect": ["OK"], "merge_gated": True,
                                    "last": {"ts": 1000.0, "ok": False, "status": "ran",
                                             "missing": ["OK"], "got": ""}}}])
        self.assertTrue(hasattr(turn, "DONE_PENDING_MERGE"),
                        "turn has no DONE_PENDING_MERGE state")
        self.assertEqual(turn.child_state(child, live=()), turn.DONE_PENDING_MERGE)

    def test_the_gate_names_it_merge_gated_rather_than_a_red(self):
        child = _t("c1", 11, parent="orch", events=[_launch()], memos=[_memo()],
                   sessions=("child-sid",),
                   steps=[{"text": "s", "done": False,
                           "exit": {"cmd": "c", "expect": ["OK"], "merge_gated": True,
                                    "last": {"ts": 1000.0, "ok": False, "status": "ran",
                                             "missing": ["OK"], "got": ""}}}])
        codes = [f["code"] for f in turn.gate(child)["findings"]]
        self.assertIn("merge-gated", codes)
        self.assertNotIn("conditions-unmet", codes)
        self.assertNotIn("pre-merge", codes)

    def test_a_merge_gated_child_is_still_GRADEABLE_but_never_clean(self):
        """Gradeable, because the work is finished and the report is there to judge. Never
        clean, because closing it would settle a task whose work has not landed."""
        child = _t("c1", 11, parent="orch", events=[_launch()], memos=[_memo()],
                   sessions=("child-sid",),
                   steps=[{"text": "s", "done": False,
                           "exit": {"cmd": "c", "expect": ["OK"], "merge_gated": True,
                                    "last": {"ts": 1000.0, "ok": False, "status": "ran",
                                             "missing": ["OK"], "got": ""}}}])
        g = turn.gate(child)
        self.assertTrue(g["gradeable"])
        self.assertFalse(g["clean"])

    def test_an_ordinary_unmet_condition_is_UNCHANGED(self):
        """The negative control: only a condition that DECLARED itself merge-gated gets
        the softer reading. Everything else still reads pre-merge or red."""
        child = _t("c1", 11, parent="orch", events=[_launch()], memos=[_memo()],
                   sessions=("child-sid",), conditions={1: "unmet"})
        codes = [f["code"] for f in turn.gate(child)["findings"]]
        self.assertIn("pre-merge", codes)
        self.assertNotIn("merge-gated", codes)

    def test_a_mix_of_merge_gated_and_ordinary_unmet_is_NOT_done_pending_merge(self):
        """One condition that a merge cannot fix means the work is not finished."""
        child = _t("c1", 11, parent="orch", events=[_launch()], memos=[_memo()],
                   sessions=("child-sid",), conditions={1: "unmet"},
                   steps=[{"text": "s", "done": False,
                           "exit": {"cmd": "c", "expect": ["OK"], "merge_gated": True,
                                    "last": {"ts": 1000.0, "ok": False, "status": "ran",
                                             "missing": ["OK"], "got": ""}}}])
        self.assertEqual(turn.child_state(child, live=()), turn.REPORTED)

    def test_the_turn_says_DONE_PENDING_MERGE_in_words(self):
        orch = _t("orch", 10)
        child = _t("c1", 11, parent="orch", events=[_launch()], memos=[_memo()],
                   sessions=("child-sid",),
                   steps=[{"text": "s", "done": False,
                           "exit": {"cmd": "c", "expect": ["OK"], "merge_gated": True,
                                    "last": {"ts": 1000.0, "ok": False, "status": "ran",
                                             "missing": ["OK"], "got": ""}}}])
        text = "\n".join(turn.lines(turn.plan(orch, [child], live=())))
        self.assertIn("DONE PENDING MERGE", text)

    # -- (d) reaching an idle child is the harness's job --------------------------

    def test_the_turn_names_SendMessage_as_the_way_to_reach_a_live_child(self):
        """`channel` can stand a child down but cannot hand it work, and an order is only
        read at a turn boundary an idle session never reaches. What actually works is the
        harness's SendMessage — adopt, don't build (decision 382), now confirmed twice."""
        orch = _t("orch", 10)
        child = self._reported_and_live(conditions={1: "unmet"})
        p = turn.plan(orch, [child], live={11})
        gates = [a for a in p["actions"] if a["action"] == turn.GATE]
        self.assertTrue(gates, "the live reporter was not gated at all")
        self.assertIn("SendMessage", gates[0].get("reach") or "")
        self.assertIn("SendMessage", "\n".join(turn.lines(p)))

    def test_a_child_that_is_NOT_live_gets_no_reach_line(self):
        orch = _t("orch", 10)
        child = _t("c1", 11, parent="orch", events=[_launch()], memos=[_memo()],
                   sessions=("child-sid",), conditions={1: "unmet"})
        p = turn.plan(orch, [child], live=())
        gates = [a for a in p["actions"] if a["action"] == turn.GATE]
        self.assertTrue(gates, "an offline reporter was not gated")
        self.assertFalse(gates[0].get("reach"))


class AChildsReportSurfacesToTheParent(unittest.TestCase):
    """(c) The rail was durable and invisible in exactly ONE direction. Track A's rule 4
    said a memo "lands where the gate looks" — but the awaiting-your-ack nag fires only for
    the task the READING session is attached to, so a parent sitting on the orchestrator was
    never told a memo had arrived on a child. The rule described an intention; nothing
    implemented the path."""

    def _brief(self, orch, kids):
        """Asserted rather than called blind, so a missing surface fails as an ASSERTION
        and not as an AttributeError — the distinction the red-before-green rule turns on."""
        fn = getattr(turn, "child_reports_brief", None)
        self.assertIsNotNone(fn, "turn.child_reports_brief does not exist")
        return fn(orch, kids)

    def _child(self, seq, acks=(), text="report", parent="orch"):
        return _t("c%s" % seq, seq, parent=parent, events=[_launch()],
                  sessions=("child-%s" % seq,),
                  memos=[_memo(text=text, from_sid="child-%s" % seq, acks=list(acks))])

    def test_an_unacked_child_report_is_surfaced_on_the_parent(self):
        orch = _t("orch", 10, title="the plan")
        block = self._brief(orch, [self._child(11)])
        self.assertIsNotNone(block)
        self.assertIn("#11", block)

    def test_an_ACKED_child_report_is_not_surfaced(self):
        orch = _t("orch", 10)
        child = self._child(11, acks=[{"sid": "parent-sid", "ts": 2100.0}])
        self.assertIsNone(self._brief(orch, [child]))

    def test_a_parent_with_no_children_gets_nothing(self):
        self.assertIsNone(self._brief(_t("orch", 10), []))

    def test_a_child_of_some_other_parent_is_not_surfaced(self):
        orch = _t("orch", 10)
        stranger = self._child(11, parent="someone-else")
        self.assertIsNone(self._brief(orch, [stranger]))

    def test_the_block_names_the_command_that_reads_the_report(self):
        """A notice that says a report exists and not how to read it is half a rail."""
        orch = _t("orch", 10)
        block = self._brief(orch, [self._child(11)])
        self.assertIn("memo show", block)

    def test_several_children_are_bounded_and_the_overflow_is_counted(self):
        orch = _t("orch", 10)
        kids = [self._child(seq) for seq in range(11, 20)]
        block = self._brief(orch, kids)
        self.assertLessEqual(len(block.splitlines()), 3 + turn.CHILD_REPORT_MAX)
        self.assertIn("more", block)

    def test_a_long_report_is_truncated_not_dropped(self):
        orch = _t("orch", 10)
        block = self._brief(orch, [self._child(11, text="x" * 4000)])
        self.assertIn("#11", block)
        self.assertTrue(all(len(l) <= turn.CHILD_REPORT_LINE_MAX + 40
                            for l in block.splitlines()), block)
