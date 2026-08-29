"""THE PICKUP RAIL — a parent learning a child finished, without being asked.

WHAT WAS MISSING, AND WHY IT WAS NOT OBVIOUS. Every piece already existed and every piece
worked. A child reaching a terminal state wrote its report as a memo on its own task, and
the lifecycle minted a notice memo on the parent. Both were durable. Both were correct.
And both surface ONLY WHEN A HUMAN TYPES — `memo_pending_brief` rides UserPromptSubmit,
`child_reports_brief` rides UserPromptSubmit and SessionStart — while an orchestrator
driving the loop starts no new session and is typed into by nobody.

So the parent's only remaining move was to poll `sessions --task <child>`. That command
answers a different question: is a PROCESS up. A child that finishes, files its report and
leaves its window open is a live process with nothing to do, and the harness's own word for
it is "busy". MEASURED TWICE, on the same board, in the same week: #532 sat about an hour,
#536 sat seven. Nothing was broken either time. There was no signal.

FIVE PROPERTIES, ONE PER CLASS.

  1. DurableRecord — the child's hand-back is a record on the PARENT's task, filed whether
     or not anything is running. Not an order: an order is addressed to a live session, and
     a child usually finishes while its parent is between sessions, so an order queued to
     nobody is a fact that was never recorded at all.

  2. TheGate — the parent's turn does not end while a hand-back is unclaimed. Same
     transport the control channel already uses to reach a running child (the Stop hook can
     refuse to let a turn pass), pointed the other way. Capped, so it can never wedge; and
     free on a turn with nothing waiting, which is nearly every turn.

  3. RetiresItself — a pickup the parent has ENGAGED stops nagging on its own, with the
     reason recorded. Engaged is graded or parked, and deliberately NOT "the child closed":
     `done` is the verb a finished child runs, so the commonest hand-back this rail carries
     is a closure, and retiring on that would cancel the notice before anyone read it.

  4. NoWaitForALandedChild — `turn` stops printing WAIT for a child whose work has
     demonstrably landed. An unacked report already outranked liveness; a GREEN CHECKLIST
     now does too, because a child can forget to file a report and cannot fake a set of
     exit conditions somebody wrote down before the work started.

  5. SessionsStopsSayingOnlyBusy — the surface that actually lied. `status` is the harness's
     word for "mid-turn"; the task itself knows whether the work is finished, and the
     listing now says so.

The fixture fakes two things and nothing else — which pids are alive, and the harness's
sessions directory. Both are real files and real process state in production, and both are
exactly what a test cannot have.
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

_TMP_HOME = tempfile.mkdtemp(prefix="ts-pickup-")
os.environ["TASK_STATION_HOME"] = _TMP_HOME

import channel                                                          # noqa: E402
import live_sessions                                                    # noqa: E402
import loop                                                             # noqa: E402
import store                                                            # noqa: E402
import turn                                                             # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)

# The seam that holds the Stop gate, grabbed AFTER the facade exec so it is the same
# generation `ts.cmd_stop_gate` was bound from. Spying on its `load_task` is how the cost
# test below proves an idle turn reads nothing.
import board.cmds.sub as _sub                                           # noqa: E402


class _Args:
    def __init__(self, **kw):
        defaults = dict(session=None, task=None, sub=None, id=None,
                        all=False, as_json=False)
        defaults.update(kw)
        self.__dict__.update(defaults)


# ------------------------------------------------------------------ the fixtures ----

def _blob(tid, seq, title="T", parent=None, conditions=None, steps=None,
          events=None, memos=None, grades=None, sessions=(), status="open"):
    """A hand-built task, the shape `test_turn` builds: the three feeds a turn reads
    (events = spawn intent, memos = the hand-back rail, grades) plus exit conditions.

    `conditions` is `{step_number: "met"|"unmet"|None}`; None leaves the condition
    registered and NEVER RUN, which is a different thing from failed and has to be."""
    related = [{"kind": "parent", "id": parent, "seq": None}] if parent else []
    step_list = list(steps or [])
    for n in sorted(conditions or {}):
        verdict = conditions[n]
        block = {"cmd": "check-%s" % n, "expect": ["OK"]}
        if verdict is not None:
            block["last"] = {"ts": 2000.0, "ok": verdict == "met", "status": "ran",
                             "missing": [], "got": ""}
        step_list.append({"text": "step %d" % n, "done": False, "exit": block})
    return {"id": tid, "seq": seq, "title": title, "status": status,
            "related": related, "steps": step_list, "events": list(events or []),
            "memos": list(memos or []), "grades": list(grades or []),
            "sessions": list(sessions)}


def _launch(ts_=1000.0):
    return {"id": "e1", "kind": "child", "ts": ts_,
            "text": "invoked by #1 as implementer: do the thing"}


def _report(text="the report", ts_=2500.0, from_sid="child-sid", acks=()):
    return {"id": "m1", "ts": ts_, "from_sid": from_sid, "from_task": None,
            "text": text, "acks": list(acks)}


class _PickupBase(unittest.TestCase):
    """An isolated store, a fake harness sessions dir, and pid liveness under the
    test's control."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pickup-rail-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        self.sessions = os.path.join(self.tmp, "sessions")
        os.makedirs(self.sessions, exist_ok=True)
        os.environ["TASK_STATION_SESSIONS_DIR"] = self.sessions
        store.reset_cache()
        self.alive = set()
        self._real_alive = live_sessions.pid_alive
        live_sessions.pid_alive = lambda pid: pid in self.alive
        channel.pid_alive = lambda pid: pid in self.alive

    def tearDown(self):
        live_sessions.pid_alive = self._real_alive
        channel.pid_alive = self._real_alive
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        os.environ.pop("TASK_STATION_SESSIONS_DIR", None)
        os.environ.pop("TASK_STATION_GATE", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- fixtures ---------------------------------------------------------------

    def _task(self, title):
        t = ts.new_task(title, "summary")
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _pair(self):
        """An orchestrator and a child whose stored `parent` edge names it."""
        parent = self._task("orchestrator")
        child = self._task("the child")
        child.setdefault("related", []).append(
            {"kind": "parent", "id": parent["id"], "seq": parent.get("seq")})
        ts.save_task(child)
        return ts.load_task(parent["id"]), ts.load_task(child["id"])

    def _gate(self, sid):
        """Run the Stop gate and return its emitted document (or None)."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_stop_gate(_Args(session=sid))
        out = buf.getvalue().strip()
        return json.loads(out) if out else None

    def _run(self, **kw):
        """Run the `pickup` CLI verb and return its stdout."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_pickup(_Args(**kw))
        return buf.getvalue()

    def _pickups(self, task_id):
        return channel.pickups(ts.load_task(task_id))


# --------------------------------------------------------------- (1) the record ----

class DurableRecord(_PickupBase):
    """A child handing work back is recorded ON THE PARENT'S TASK, whether or not
    anything is running to hear it."""

    def test_a_terminal_transition_files_a_pickup_on_the_parent(self):
        parent, child = self._pair()
        ts.report_to_parent(child, "CLOSED — ready for the gate")
        rows = self._pickups(parent["id"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["child_id"], child["id"])
        self.assertEqual(rows[0]["child_seq"], child["seq"])
        self.assertIn("CLOSED", rows[0]["headline"])

    def test_it_is_filed_with_nothing_running_at_all(self):
        """THE REASON IT IS NOT AN ORDER. An order is queued to the sessions that are up
        RIGHT NOW; a child usually finishes while its parent is between sessions, and an
        order queued to nobody is a fact that was never recorded."""
        parent, child = self._pair()
        self.assertEqual(channel.live_sids(ts.load_task(parent["id"])), set())
        ts.report_to_parent(child, "every exit condition is now MET")
        self.assertEqual(len(channel.pickups_pending(ts.load_task(parent["id"]))), 1)

    def test_the_memo_rail_is_untouched(self):
        """The pickup is additive. The routine notice memo still lands, still reads as
        routine, and still does not hold anybody's turn."""
        parent, child = self._pair()
        ts.report_to_parent(child, "closed")
        memos = ts.load_task(parent["id"])["memos"]
        self.assertEqual(len(memos), 1)
        self.assertTrue(memos[0].get(channel.ROUTINE_FIELD))

    def test_one_open_row_per_child_however_often_it_reports(self):
        """A child reports twice — `exit-tick` when its conditions go green, `done` when
        it closes. Two rows for one child is two nags for one thing to do."""
        parent, child = self._pair()
        ts.report_to_parent(child, "conditions MET")
        ts.report_to_parent(child, "conditions MET")
        self.assertEqual(len(self._pickups(parent["id"])), 1)

    def test_a_new_fact_re_arms_the_delivery_budget(self):
        """The cap exists so a notice cannot trap a session, not so a parent can miss a
        SECOND, different fact by having ignored the first."""
        parent = self._task("orch")
        child = {"id": "c1", "seq": 9, "title": "kid"}
        row, created = channel.pickup_file(parent, child, "conditions MET")
        self.assertTrue(created)
        row["blocks"] = channel.PICKUP_MAX_BLOCKS
        again, created = channel.pickup_file(parent, child, "conditions MET")
        self.assertFalse(created)
        self.assertEqual(again["blocks"], channel.PICKUP_MAX_BLOCKS)   # same fact
        moved, created = channel.pickup_file(parent, child, "CLOSED — ready for the gate")
        self.assertFalse(created)
        self.assertEqual(moved["blocks"], 0)                            # new fact
        self.assertEqual(len(channel.pickups(parent)), 1)

    def test_nothing_is_filed_without_a_headline_or_a_child(self):
        parent = self._task("orch")
        self.assertEqual(channel.pickup_file(parent, {"id": "c"}, "   "), (None, False))
        self.assertEqual(channel.pickup_file(parent, {"seq": 3}, "done"), (None, False))
        self.assertEqual(channel.pickups(parent), [])

    def test_a_closed_parent_is_not_nagged(self):
        parent, child = self._pair()
        ts.close_task_inplace(parent)
        ts.save_task(parent)
        ts.report_to_parent(ts.load_task(child["id"]), "closed")
        self.assertEqual(self._pickups(parent["id"]), [])


# ----------------------------------------------------------------- (2) the gate ----

class TheGate(_PickupBase):
    """The parent's turn does not end while a child's hand-back is unclaimed."""

    def _waiting(self):
        parent, child = self._pair()
        ts.report_to_parent(child, "CLOSED — ready for the gate")
        ts.set_link("parent-sid", parent["id"])
        return ts.load_task(parent["id"]), ts.load_task(child["id"])

    def test_the_turn_does_not_end_while_a_child_waits(self):
        parent, child = self._waiting()
        doc = self._gate("parent-sid")
        self.assertIsNotNone(doc, "the Stop gate emitted nothing at all")
        self.assertEqual(doc["decision"], "block")
        self.assertIn("HANDED WORK BACK", doc["reason"])
        self.assertIn("#%s" % child["seq"], doc["reason"])

    def test_the_reason_says_how_to_read_the_report_and_how_to_take_it(self):
        """A notice that says a report exists without saying how to read it is half a
        rail — and the report is on the CHILD's ledger, which nothing on the parent shows."""
        parent, child = self._waiting()
        reason = self._gate("parent-sid")["reason"]
        self.assertIn("memo list --task %s" % child["seq"], reason)
        self.assertIn("pickup take --task %s" % parent["seq"], reason)
        self.assertIn("turn --task %s" % parent["seq"], reason)

    def test_it_names_the_harness_rails_rather_than_rebuilding_them(self):
        """The programme-wide ruling: adopt what the harness already gives you. This rail
        carries the FACT; liveness and reach stay ListAgents and SendMessage."""
        self._waiting()
        reason = self._gate("parent-sid")["reason"]
        self.assertIn("ListAgents", reason)
        self.assertIn("SendMessage", reason)

    def test_delivery_is_stamped_once_and_the_block_count_advances(self):
        parent, _child = self._waiting()
        self._gate("parent-sid")
        first = channel.pickups(ts.load_task(parent["id"]))[0]
        self.assertEqual(first["blocks"], 1)
        stamped = first["delivered_ts"]
        self.assertIsNotNone(stamped)
        self._gate("parent-sid")
        second = channel.pickups(ts.load_task(parent["id"]))[0]
        self.assertEqual(second["blocks"], 2)
        self.assertEqual(second["delivered_ts"], stamped,
                         "the second block is the same delivery, insisted upon")

    def test_it_cannot_wedge_and_stays_visible_after_the_cap(self):
        """Past the cap the row stays PENDING and fully visible — it just stops holding
        the turn. The same anti-wedge rule the order gate has always had."""
        parent, _child = self._waiting()
        for _ in range(channel.PICKUP_MAX_BLOCKS):
            self.assertIsNotNone(self._gate("parent-sid"))
        self.assertIsNone(self._gate("parent-sid"))
        after = ts.load_task(parent["id"])
        self.assertEqual(len(channel.pickups_pending(after)), 1)
        self.assertEqual(channel.pickups_blocking(after), [])
        self.assertIn("waiting", self._run(sub="list", task=str(parent["seq"])))

    def test_an_idle_parent_pays_one_link_read_and_nothing_else(self):
        """This runs at EVERY turn end of EVERY session. A rail that cost a store scan
        would be a rail people turn off."""
        parent, _child = self._pair()
        ts.set_link("parent-sid", parent["id"])
        loads = []
        real = _sub.load_task

        def spy(tid, *a, **kw):
            loads.append(tid)
            return real(tid, *a, **kw)

        _sub.load_task = spy
        self.addCleanup(lambda: setattr(_sub, "load_task", real))
        self.assertIsNone(self._gate("parent-sid"))
        self.assertEqual(loads, [parent["id"]],
                         "an idle turn loaded something other than its own task")

    def test_a_session_on_no_task_is_untouched(self):
        self.assertIsNone(self._gate("stranger-sid"))

    def test_the_gate_switch_still_turns_it_off(self):
        self._waiting()
        os.environ["TASK_STATION_GATE"] = "off"
        self.assertIsNone(self._gate("parent-sid"))

    def test_a_pickup_and_an_order_ride_one_block_document(self):
        """The harness reads a single JSON object from this hook. Dropping one of two live
        reasons to fit that shape would silently lose whichever lost the coin toss."""
        parent, child = self._waiting()
        parent = ts.load_task(parent["id"])
        order, err = channel.order_queue(parent, channel.ORDER_STAND_DOWN,
                                         "wrap up now", "parent-sid")
        self.assertIsNone(err)
        ts.save_task(parent)
        reason = self._gate("parent-sid")["reason"]
        self.assertIn("STAND-DOWN", reason)
        self.assertIn("HANDED WORK BACK", reason)


# ------------------------------------------------------------- (3) it retires ----

class RetiresItself(_PickupBase):
    """A pickup for a child already dealt with must stop asking — and the ledger must
    say WHICH of the three ways it was dealt with."""

    def _waiting(self):
        parent, child = self._pair()
        ts.report_to_parent(child, "CLOSED — ready for the gate")
        ts.set_link("parent-sid", parent["id"])
        return ts.load_task(parent["id"]), ts.load_task(child["id"])

    def test_a_closed_child_does_NOT_retire_its_own_pickup(self):
        """THE TRAP, and the reason the end-to-end probe exists. `done` is the verb a
        FINISHED CHILD RUNS, so the commonest headline this rail carries is literally
        "CLOSED — ready for the gate". Retiring on closure would file the notice and
        cancel it before the parent saw it — the exact stall, restored, with a mechanism
        in place claiming otherwise. A closed child still has to be graded."""
        parent, child = self._waiting()
        child = ts.load_task(child["id"])
        ts.close_task_inplace(child)
        ts.save_task(child)
        self.assertIsNotNone(self._gate("parent-sid"),
                             "closing the child silently voided the pickup")
        self.assertEqual(len(channel.pickups_pending(ts.load_task(parent["id"]))), 1)

    def test_grading_the_child_retires_its_pickup(self):
        """Engagement is what retires it: the parent ran the gate and judged the work."""
        parent, child = self._waiting()
        child = ts.load_task(child["id"])
        loop.record(child, {"G1": "A"}, "A-")
        ts.save_task(child)
        self.assertIsNone(self._gate("parent-sid"))
        row = channel.pickups(ts.load_task(parent["id"]))[0]
        self.assertEqual(row["how"], channel.PICKUP_GRADED)
        self.assertIsNotNone(row["taken_ts"])

    def test_a_grade_that_predates_the_hand_back_retires_nothing(self):
        """A child rejected, sent back, and reporting AGAIN is waiting all over again —
        its ledger already carries the grade that rejected it."""
        parent, child = self._pair()
        child = ts.load_task(child["id"])
        loop.record(child, {"G1": "C"}, "A-", now=1000.0)
        ts.save_task(child)
        ts.report_to_parent(ts.load_task(child["id"]), "reworked — conditions MET")
        ts.set_link("parent-sid", parent["id"])
        self.assertIsNotNone(self._gate("parent-sid"))

    def test_a_parked_child_retires_its_own_pickup(self):
        parent, child = self._waiting()
        child = ts.load_task(child["id"])
        loop.record(child, {}, "A-", park="human-gate")
        ts.save_task(child)
        self.assertIsNone(self._gate("parent-sid"))
        self.assertEqual(channel.pickups(ts.load_task(parent["id"]))[0]["how"],
                         channel.PICKUP_PARKED)

    def test_an_open_child_is_never_retired_for_it(self):
        parent, _child = self._waiting()
        self.assertIsNotNone(self._gate("parent-sid"))
        self.assertEqual(len(channel.pickups_pending(ts.load_task(parent["id"]))), 1)

    def test_taking_one_records_who_took_it_and_frees_the_turn(self):
        parent, child = self._waiting()
        row = channel.pickups(ts.load_task(parent["id"]))[0]
        out = self._run(sub="take", task=str(parent["seq"]), id=row["id"][:8],
                        session="parent-sid")
        self.assertIn("taken", out)
        after = channel.pickups(ts.load_task(parent["id"]))[0]
        self.assertEqual(after["how"], channel.PICKUP_TAKEN)
        self.assertEqual(after["taken_by"], "parent-sid")
        self.assertIsNone(self._gate("parent-sid"))

    def test_taking_one_twice_is_reported_not_silently_repeated(self):
        parent, _child = self._waiting()
        row = channel.pickups(ts.load_task(parent["id"]))[0]
        self._run(sub="take", task=str(parent["seq"]), id=row["id"][:8],
                  session="parent-sid")
        out = self._run(sub="take", task=str(parent["seq"]), id=row["id"][:8],
                        session="other-sid")
        self.assertIn("already taken", out)

    def test_take_says_plainly_that_it_is_not_a_grade(self):
        """The one misreading that would cost real work: retiring the notice is not the
        same as gating and grading what came back."""
        parent, _child = self._waiting()
        row = channel.pickups(ts.load_task(parent["id"]))[0]
        out = self._run(sub="take", task=str(parent["seq"]), id=row["id"][:8],
                        session="parent-sid")
        self.assertIn("does NOT grade it", out)
        self.assertIn("turn --task %s" % parent["seq"], out)

    def test_list_shows_the_childs_own_report_text(self):
        parent, child = self._pair()
        child = ts.load_task(child["id"])
        child["events"] = [_launch()]
        child["sessions"] = ["child-sid"]
        child["memos"] = [_report("steps 3-5 landed; PR is open")]
        ts.save_task(child)
        ts.report_to_parent(ts.load_task(child["id"]), "conditions MET")
        out = self._run(sub="list", task=str(parent["seq"]))
        self.assertIn("steps 3-5 landed", out)

    def test_an_unknown_id_is_refused_rather_than_guessed(self):
        parent, _child = self._waiting()
        with self.assertRaises(SystemExit):
            self._run(sub="take", task=str(parent["seq"]), id="deadbeef",
                      session="parent-sid")


# ---------------------------------------------------- (4) turn stops saying WAIT ----

class NoWaitForALandedChild(unittest.TestCase):
    """`turn` is pure over task dicts, so this class needs no store at all."""

    def _plan(self, child, live=()):
        orch = _blob("o1", 1, "orch")
        return turn.plan(orch, [child], live=live, cap=3)

    def _actions(self, plan_, seq):
        return [a["action"] for a in plan_["actions"] if a["seq"] == seq]

    def test_a_live_child_with_a_green_checklist_is_not_waited_on(self):
        """THE #532 SHAPE. A live session, an idle model, and a plan that has demonstrably
        finished. Liveness cannot tell that apart from thinking; the checklist can."""
        child = _blob("c1", 11, parent="o1", events=[_launch()],
                      conditions={1: "met", 2: "met"})
        p = self._plan(child, live={11})
        self.assertEqual(p["states"][11], turn.REPORTED)
        self.assertNotIn(turn.WAIT, self._actions(p, 11))
        self.assertIn(turn.GATE, self._actions(p, 11))

    def test_a_live_child_with_nothing_computed_is_still_a_wait(self):
        """The false-positive direction is the expensive one. A registered-but-never-run
        condition refutes nothing and proves nothing."""
        child = _blob("c1", 11, parent="o1", events=[_launch()],
                      conditions={1: None})
        p = self._plan(child, live={11})
        self.assertEqual(p["states"][11], turn.RUNNING)
        self.assertIn(turn.WAIT, self._actions(p, 11))

    def test_a_live_child_with_a_failing_condition_is_still_a_wait(self):
        child = _blob("c1", 11, parent="o1", events=[_launch()],
                      conditions={1: "met", 2: "unmet"})
        p = self._plan(child, live={11})
        self.assertEqual(p["states"][11], turn.RUNNING)

    def test_a_green_verdict_that_predates_the_launch_proves_nothing(self):
        """Otherwise a task whose conditions were already green would read as finished the
        second it was invoked — which is the lie in the direction that costs work."""
        child = _blob("c1", 11, parent="o1", events=[_launch(ts_=9000.0)],
                      conditions={1: "met"})          # last ran at 2000.0
        self.assertFalse(turn.landed_work(child, after=9000.0))
        p = self._plan(child, live={11})
        self.assertEqual(p["states"][11], turn.RUNNING)

    def test_a_child_that_registers_nothing_can_never_be_landed(self):
        """`exits.satisfied`'s empty-registration rule, inherited whole: a plan that has
        checked nothing must never buy a green."""
        child = _blob("c1", 11, parent="o1", events=[_launch()])
        self.assertFalse(turn.landed_work(child, after=1000.0))

    def test_partial_instrumentation_does_not_buy_a_green(self):
        """One condition passed alongside a live step nothing can answer is one eighth of
        a plan claiming to be all of it."""
        child = _blob("c1", 11, parent="o1", events=[_launch()],
                      conditions={1: "met"},
                      steps=[{"text": "the uninstrumented half", "done": False}])
        self.assertFalse(turn.landed_work(child, after=1000.0))

    def test_a_parked_child_is_never_dragged_back_by_a_green_checklist(self):
        """That rule predates this one and outranks it."""
        child = _blob("c1", 11, parent="o1", events=[_launch()],
                      conditions={1: "met"},
                      grades=[{"ts": 3000.0, "park": "human-gate"}])
        p = self._plan(child, live=())
        self.assertEqual(p["states"][11], turn.PARKED)

    def test_the_gate_still_demands_a_report_from_a_landed_silent_child(self):
        """Proving the work is done is not the same as handing it back. A parent that
        cannot read what happened has not been handed anything."""
        child = _blob("c1", 11, parent="o1", events=[_launch()],
                      conditions={1: "met"})
        g = turn.gate(child, live={11})
        self.assertEqual(g["state"], turn.REPORTED)
        self.assertIn("no-report", [f["code"] for f in g["findings"]])

    def test_a_landed_child_that_did_report_gets_no_such_finding(self):
        child = _blob("c1", 11, parent="o1", events=[_launch()],
                      conditions={1: "met"}, sessions=["child-sid"],
                      memos=[_report()])
        g = turn.gate(child, live={11})
        self.assertNotIn("no-report", [f["code"] for f in g["findings"]])

    def test_a_silent_exit_keeps_its_finding_exactly_as_before(self):
        """The finding moved from being keyed on the STATE NAME to being keyed on the
        FACT. The pre-existing case must be untouched by that."""
        child = _blob("c1", 11, parent="o1", events=[_launch()],
                      memos=[{"id": "x", "ts": 1500.0, "from_sid": "child-sid",
                              "from_task": None, "text": "", "acks": []}])
        g = turn.gate(child, live=(), worked=True)
        self.assertEqual(g["state"], turn.SILENT_EXIT)
        self.assertIn("no-report", [f["code"] for f in g["findings"]])

    def test_the_wait_command_is_no_longer_a_poll(self):
        """`scan` every few minutes is how a parent read "busy" about a finished child.
        A real WAIT now means there is nothing yet, and the pickup rail will say when
        there is."""
        child = _blob("c1", 11, parent="o1", events=[_launch()], conditions={1: None})
        p = self._plan(child, live={11})
        wait = [a for a in p["actions"] if a["action"] == turn.WAIT][0]
        self.assertNotIn("scan", wait["command"])
        self.assertIn("pickup list", wait["command"])
        self.assertIn("neither reported nor turned its exit conditions green",
                      wait["why"])

    def test_an_unacked_report_still_outranks_liveness(self):
        """The half that already existed. Adding a source must never remove one."""
        child = _blob("c1", 11, parent="o1", events=[_launch()],
                      sessions=["child-sid"], memos=[_report()])
        p = self._plan(child, live={11})
        self.assertEqual(p["states"][11], turn.REPORTED)


# ------------------------------------------ (5) the surface that actually lied ----

class SessionsStopsSayingOnlyBusy(_PickupBase):
    """`status` is the harness's word for "the model is mid-turn". A child that finished
    and left its window open is a live process with nothing to do."""

    def _child_with(self, **kw):
        child = self._task("the child")
        child.update(kw)
        ts.save_task(child)
        return ts.load_task(child["id"])

    def test_a_finished_childs_row_says_handed_back(self):
        child = self._child_with(events=[_launch()], sessions=["child-sid"],
                                 memos=[_report()])
        row = {"pid": 4242, "task_id": child["id"], "task_seq": child["seq"],
               "role": "hub", "status": "busy", "updated_ts": 0, "cwd": "/tmp",
               "resume_command": "claude --resume x"}
        line = ts._format_session_row(row)
        self.assertIn("busy", line)
        self.assertIn("HANDED BACK", line)
        self.assertIn("report filed", line)

    def test_a_green_checklist_says_so_in_its_own_words(self):
        child = self._child_with(
            events=[_launch()],
            steps=[{"text": "step 1", "done": False,
                    "exit": {"cmd": "c", "expect": ["OK"],
                             "last": {"ts": 2000.0, "ok": True, "status": "ran",
                                      "missing": [], "got": ""}}}])
        row = {"pid": 1, "task_id": child["id"], "task_seq": child["seq"],
               "role": "hub", "status": "busy", "updated_ts": 0, "cwd": "/tmp",
               "resume_command": None}
        self.assertIn("exit conditions green", ts._format_session_row(row))

    def test_a_child_still_working_gets_no_such_suffix(self):
        child = self._child_with(events=[_launch()])
        row = {"pid": 1, "task_id": child["id"], "task_seq": child["seq"],
               "role": "hub", "status": "busy", "updated_ts": 0, "cwd": "/tmp",
               "resume_command": None}
        self.assertNotIn("HANDED BACK", ts._format_session_row(row))

    def test_a_row_with_no_task_is_unchanged(self):
        row = {"pid": 1, "task_id": None, "task_seq": None, "role": None,
               "status": "idle", "updated_ts": 0, "cwd": None, "resume_command": None}
        line = ts._format_session_row(row)
        self.assertIn("task —", line)
        self.assertNotIn("HANDED BACK", line)

    def test_the_live_rows_carry_the_task_id_the_question_needs(self):
        """The join already loaded the task; without its id every reader would have to
        re-resolve the seq against the whole store to ask it anything."""
        self.assertIn("task_id", live_sessions.running.__doc__)


# ---------------------------------------------------- (6) the cold-start contract ----

class ColdStartProbe(unittest.TestCase):
    """The hook that runs in production is `python3 lib/task-station.py stop-gate` in a
    FRESH interpreter, and its contract is a JSON document on stdout that the harness
    parses. Everything above exercises `cmd_stop_gate` in-process, with the facade's seams
    already imported — a rail that worked there and printed nothing from a cold start would
    pass every one of those tests and deliver nothing.

    That is not hypothetical here. The probe this wraps is what caught the retirement rule
    being wrong: `done` is the verb a finished child RUNS, so retiring a pickup on "the
    child is closed" cancelled the notice before anybody could read it — the exact stall
    this task exists to remove, restored, with a mechanism in place claiming otherwise. The
    in-process suite was green at the time."""

    def test_the_rail_works_from_a_cold_cli(self):
        import subprocess
        script = os.path.join(_REPO_ROOT, "tests", "e2e_pickup_rail.sh")
        env = dict(os.environ)
        env.pop("TASK_STATION_HOME", None)
        env.pop("TASK_STATION_SESSIONS_DIR", None)
        r = subprocess.run(["bash", script], capture_output=True, text=True,
                           timeout=180, env=env)
        self.assertIn("PICKUP-RAIL-OK", r.stdout,
                      "cold-start probe failed:\n%s\n%s" % (r.stdout, r.stderr))
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
