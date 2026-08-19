"""THE CHILD CONTROL CHANNEL — reaching a running child, not merely observing one.

A parent could already SEE a child (the scan's RUNNING column) and could already leave
it a note (`memo`). Neither of those REACHES it. A memo lands in the record and is
surfaced by the UserPromptSubmit rail — which fires when somebody TYPES. An invoked
child is handed exactly one prompt and then works; nobody types again. So every memo
sent after that prompt waited for a turn the child was never going to take, and the
parent had no way to tell it anything at all until it stopped.

Five properties, one per class, each the thing that was missing:

  1. LiveDelivery — a memo to a task with a live session is DELIVERED. The transport is
     the Stop hook, because the end of a turn is the one moment a running session
     reaches on its own, with no human in the loop. The child cannot finish the turn
     without reading it.

  2. StandDown — the parent can stand a child down AND GET BACK WHAT IT WROTE. A
     stand-down that merely stops a child loses everything the child had not yet
     written down, so settling one REQUIRES a report, and the report goes back to
     whoever ordered it.

  3. SpecMovedReaches — DONE is computed from exit conditions, so editing them mid-
     flight moves the goalposts under a working child. The change reaches it.

  4. LivenessWithoutSessionStart — liveness comes from the harness's own per-process
     record (a live pid, and the control socket the harness opened for that session),
     joined to the task through the roster the LAUNCH wrote. Not the link store, which
     a hook writes: a child that has been launched but whose hooks have not run is
     exactly the child a parent most needs to reach.

  5. NoPermissionLaundering — a session that was DENIED an action may not ask a peer to
     perform it. Refused at the channel, from a durable denial record, so it does not
     depend on each session choosing to be honest about it.

`StandDown` also carries the PRECEDENCE ruling against 3.8.0's relay: a pending
stand-down silences the relay nudge for that turn. A stand-down is an order from the
parent; a relay is a self-assessment about context. If both fire and the relay proceeds,
the child spawns a successor to continue work the parent just cancelled — a child
disobeying a stop by proxy, and burning a fresh full-window session to do it.

The fixture fakes two things and nothing else: which pids are alive, and the harness's
sessions directory. Both are real files/real process state in production, and both are
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

_TMP_HOME = tempfile.mkdtemp(prefix="ts-channel-")
os.environ["TASK_STATION_HOME"] = _TMP_HOME

import channel                                                          # noqa: E402
import live_sessions                                                    # noqa: E402
import store                                                            # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)

# The seam that holds the Stop gate, grabbed AFTER the facade exec so it is the same
# generation `ts.cmd_stop_gate` was bound from (the facade purges and re-imports its seams
# per copy). Spying on its `all_tasks` is how the cost tests below prove the gate does not
# read the whole store on every turn.
import board.cmds.sub as _sub                                           # noqa: E402


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _ChannelBase(unittest.TestCase):
    """An isolated store, a fake harness sessions dir, a fake socket dir, and pid
    liveness under the test's control."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="control-channel-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        self.sessions = os.path.join(self.tmp, "sessions")
        self.socks = os.path.join(self.tmp, "socks")
        os.makedirs(self.sessions, exist_ok=True)
        os.makedirs(self.socks, exist_ok=True)
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

    def _launch(self, task, sid, pid, cwd="/tmp/wt", socket=True, alive=True,
                status="busy", link=False):
        """What a LAUNCH leaves behind: the task's own roster entry (this is what
        `invoke` pre-binds) plus the harness's per-process record.

        `link=False` on purpose — the sid→task link is written by an attach, i.e. by a
        hook. A launched child that has not reached its hooks yet has no link, and that
        is the case the channel has to survive."""
        meta = task.setdefault("session_meta", {})
        meta[sid] = {"cwd": cwd, "ts": 1_700_000_000.0, "role": "hub",
                     "spawned_at": 1_700_000_000.0, "preborn": True}
        if sid not in task.setdefault("sessions", []):
            task["sessions"].append(sid)
        ts.save_task(task)
        rec = {"pid": pid, "sessionId": sid, "cwd": cwd, "kind": "hub",
               "entrypoint": "cli", "status": status, "startedAt": 1_700_000_000_000,
               "updatedAt": 1_700_000_100_000, "peerProtocol": 1}
        if socket:
            sock = os.path.join(self.socks, "%d.sock" % pid)
            with open(sock, "w", encoding="utf-8") as f:
                f.write("")
            rec["messagingSocketPath"] = sock
        with open(os.path.join(self.sessions, "%d.json" % pid), "w",
                  encoding="utf-8") as f:
            json.dump(rec, f)
        if alive:
            self.alive.add(pid)
        if link:
            ts.set_link(sid, task["id"])
        return ts.load_task(task["id"])

    def _gate(self, sid):
        """Run the Stop gate and return its emitted document (or None)."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_stop_gate(_Args(session=sid))
        out = buf.getvalue().strip()
        if not out:
            return None
        return json.loads(out)

    def _memos(self, task_id):
        return [m.get("text", "") for m in (ts.load_task(task_id).get("memos") or [])]

    def _pressure(self, sid, used=700_000, window=1_000_000):
        """Put `sid` genuinely over the relay/checkpoint trigger: a real transcript
        carrying a `usage` block, and a hud snapshot naming the window. No engine name is
        patched — the nudge measures what the files say, exactly as it does in production."""
        import hud
        self._proj = os.path.join(self.tmp, "projects")
        ts.PROJECTS_ROOT = self._proj
        bucket = os.path.join(self._proj, "-fake-bucket")
        os.makedirs(bucket, exist_ok=True)
        line = {"type": "assistant", "message": {"role": "assistant",
                "model": "claude-opus-4-8", "usage": {"input_tokens": used}}}
        with open(os.path.join(bucket, sid + ".jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")
        hud._write_snap(sid, {"context_window_size": window})
        import config as _cfg
        _cfg.set("auto_checkpoint", True)

    def _nudge(self, sid):
        """Run the Stop nudge and return its emitted document (or None)."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_stop_nudge(_Args(session=sid))
        out = buf.getvalue().strip()
        return json.loads(out) if out else None

    def _scan_spy(self):
        """Count whole-store scans inside the Stop gate. Returns the call list."""
        calls = []
        real = _sub.all_tasks

        def spy(*a, **kw):
            calls.append(1)
            return real(*a, **kw)

        _sub.all_tasks = spy
        self.addCleanup(lambda: setattr(_sub, "all_tasks", real))
        return calls


# ---------------------------------------------------------------- (1) delivery ----

class LiveDelivery(_ChannelBase):
    """A memo to a task with a live session is DELIVERED — it does not wait for a turn
    the child may never take."""

    def test_a_memo_to_a_live_session_queues_an_order_for_it(self):
        parent, child = self._pair()
        child = self._launch(child, "child-sid", 4101)
        ts.memo_send(child, "the API moved to /v2", from_sid="parent-sid")
        ts.save_task(child)
        child = ts.load_task(child["id"])
        pending = channel.orders_for(child, "child-sid")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["kind"], "memo")
        self.assertIn("the API moved to /v2", pending[0]["text"])

    def test_the_child_cannot_end_its_turn_without_reading_it(self):
        parent, child = self._pair()
        child = self._launch(child, "child-sid", 4102, link=True)
        ts.memo_send(child, "stop rebasing, main moved", from_sid="parent-sid")
        ts.save_task(child)
        doc = self._gate("child-sid")
        self.assertIsNotNone(doc, "the Stop gate said nothing — the memo was not delivered")
        self.assertEqual(doc.get("decision"), "block")
        self.assertIn("stop rebasing, main moved", doc["reason"])
        self.assertIn("channel settle", doc["reason"])

    def test_a_task_with_no_live_session_queues_nothing(self):
        parent, child = self._pair()
        # A recorded session whose process is GONE: the memo behaves exactly as before.
        child = self._launch(child, "child-sid", 4103, alive=False)
        ts.memo_send(child, "nobody is home", from_sid="parent-sid")
        ts.save_task(child)
        child = ts.load_task(child["id"])
        self.assertEqual(channel.orders_for(child, "child-sid"), [])
        self.assertEqual(len(child.get("memos") or []), 1)   # the memo itself is intact

    def test_the_sender_is_never_ordered_to_read_its_own_memo(self):
        parent, child = self._pair()
        child = self._launch(child, "author-sid", 4104)
        ts.memo_send(child, "note to self", from_sid="author-sid")
        ts.save_task(child)
        child = ts.load_task(child["id"])
        self.assertEqual(channel.orders_for(child, "author-sid"), [])

    def test_delivery_is_not_an_ack(self):
        """Reading it at the Stop gate marks the ORDER delivered. The memo stays pending
        for this session's ack — the ledger's claim is still the ledger's to make."""
        parent, child = self._pair()
        child = self._launch(child, "child-sid", 4105, link=True)
        ts.memo_send(child, "integrate this", from_sid="parent-sid")
        ts.save_task(child)
        self._gate("child-sid")
        child = ts.load_task(child["id"])
        order = channel.orders_for(child, "child-sid")[0]
        self.assertTrue(order.get("delivered_ts"))
        self.assertEqual(len(ts.memo_pending(child, "child-sid")), 1)

    def test_an_unsettled_order_cannot_wedge_the_session(self):
        """The gate gives up after ORDER_MAX_BLOCKS. The order stays pending and
        visible; what it stops doing is holding the turn hostage."""
        parent, child = self._pair()
        child = self._launch(child, "child-sid", 4106, link=True)
        ts.memo_send(child, "read me", from_sid="parent-sid")
        ts.save_task(child)
        for _ in range(channel.ORDER_MAX_BLOCKS):
            doc = self._gate("child-sid")
            self.assertEqual((doc or {}).get("decision"), "block")
        doc = self._gate("child-sid")
        self.assertNotEqual((doc or {}).get("decision"), "block")
        child = ts.load_task(child["id"])
        self.assertEqual(len(channel.orders_for(child, "child-sid")), 1)

    def test_an_unused_channel_is_invisible_to_the_stop_gate(self):
        """The gate runs on EVERY turn end of EVERY session. A machine that has never used
        the channel must pay one absent-file stat for it — never a walk of the store."""
        calls = self._scan_spy()
        self.assertFalse(channel.index_active())
        self.assertIsNone(self._gate("a-stranger"))
        self.assertEqual(calls, [])

    def test_an_unlinked_child_is_found_by_the_index_not_by_a_scan(self):
        parent, child = self._pair()
        child = self._launch(child, "child-sid", 4108)          # no link
        ts.memo_send(child, "reach me", from_sid="parent-sid")
        ts.save_task(child)
        self.assertIn(child["id"], channel.indexed_tasks("child-sid"))
        calls = self._scan_spy()
        doc = self._gate("child-sid")
        self.assertEqual((doc or {}).get("decision"), "block")
        self.assertEqual(calls, [])

    def test_a_settled_order_stops_blocking(self):
        parent, child = self._pair()
        child = self._launch(child, "child-sid", 4107, link=True)
        ts.memo_send(child, "one thing", from_sid="parent-sid")
        ts.save_task(child)
        self.assertEqual(self._gate("child-sid")["decision"], "block")
        child = ts.load_task(child["id"])
        order = channel.orders_for(child, "child-sid")[0]
        with redirect_stdout(io.StringIO()):
            ts.cmd_channel(_Args(sub="settle", task=str(child["seq"]),
                                 id=order["id"][:8], session="child-sid",
                                 report=None, why=None, as_json=False, action=None,
                                 by=None))
        self.assertIsNone(self._gate("child-sid"))


# -------------------------------------------------------------- (2) stand-down ----

class StandDown(_ChannelBase):
    """The parent can stand a child down and get back what it wrote."""

    def _stand_down(self, child, why="the spec moved", session="parent-sid"):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_channel(_Args(sub="stand-down", task=str(child["seq"]),
                                 session=session, why=why, id=None, report=None,
                                 as_json=False, action=None, by=None))
        return buf.getvalue()

    def test_it_reaches_every_live_session_on_the_child(self):
        parent, child = self._pair()
        child = self._launch(child, "child-a", 4201)
        child = self._launch(child, "child-b", 4202)
        child = self._launch(child, "child-dead", 4203, alive=False)
        ts.set_link("parent-sid", parent["id"])
        self._stand_down(child)
        child = ts.load_task(child["id"])
        self.assertEqual(len(channel.orders_for(child, "child-a")), 1)
        self.assertEqual(len(channel.orders_for(child, "child-b")), 1)
        self.assertEqual(channel.orders_for(child, "child-dead"), [])
        self.assertEqual(channel.orders_for(child, "child-a")[0]["kind"], "stand-down")

    def test_the_child_is_told_at_its_next_turn_boundary(self):
        parent, child = self._pair()
        child = self._launch(child, "child-a", 4204, link=True)
        ts.set_link("parent-sid", parent["id"])
        self._stand_down(child, why="main moved under you")
        doc = self._gate("child-a")
        self.assertEqual((doc or {}).get("decision"), "block")
        self.assertIn("STAND-DOWN", doc["reason"])
        self.assertIn("main moved under you", doc["reason"])

    def test_settling_it_without_a_report_is_refused(self):
        """A stand-down whose settle needs nothing back is a stand-down that loses
        whatever the child had not written down yet."""
        parent, child = self._pair()
        child = self._launch(child, "child-a", 4205, link=True)
        ts.set_link("parent-sid", parent["id"])
        self._stand_down(child)
        child = ts.load_task(child["id"])
        order = channel.orders_for(child, "child-a")[0]
        buf = io.StringIO()
        with self.assertRaises(SystemExit) as cm:      # loud: a driver must be able to branch
            with redirect_stdout(buf):
                ts.cmd_channel(_Args(sub="settle", task=str(child["seq"]),
                                     id=order["id"][:8], session="child-a", report=None,
                                     why=None, as_json=False, action=None, by=None))
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("--report", buf.getvalue())
        child = ts.load_task(child["id"])
        self.assertEqual(len(channel.orders_for(child, "child-a")), 1)   # still pending

    def test_the_report_goes_back_to_whoever_ordered_it(self):
        parent, child = self._pair()
        child = self._launch(child, "child-a", 4206, link=True)
        ts.set_link("parent-sid", parent["id"])
        self._stand_down(child)
        child = ts.load_task(child["id"])
        order = channel.orders_for(child, "child-a")[0]
        with redirect_stdout(io.StringIO()):
            ts.cmd_channel(_Args(sub="settle", task=str(child["seq"]),
                                 id=order["id"][:8], session="child-a",
                                 report="wrote lib/x.py + two tests; rebase unfinished",
                                 why=None, as_json=False, action=None, by=None))
        got = " ".join(self._memos(parent["id"]))
        self.assertIn("wrote lib/x.py + two tests", got)
        self.assertIn("rebase unfinished", got)
        child = ts.load_task(child["id"])
        self.assertEqual(channel.orders_for(child, "child-a"), [])        # settled
        settled = [o for o in channel.orders(child) if o["id"] == order["id"]][0]
        self.assertEqual(settled["report"],
                         "wrote lib/x.py + two tests; rebase unfinished")

    def test_standing_down_a_child_nobody_is_running_says_so(self):
        parent, child = self._pair()
        ts.set_link("parent-sid", parent["id"])
        out = self._stand_down(child)
        self.assertIn("no live session", out)
        self.assertEqual(channel.orders(ts.load_task(child["id"])), [])

    # -- precedence against 3.8.0's relay ------------------------------------------

    def test_the_relay_nudge_fires_without_a_stand_down(self):
        """The control: this session IS over the trigger, so the nudge must speak. Without
        this, the suppression test below would pass on a nudge that never fired at all."""
        parent, child = self._pair()
        child = self._launch(child, "child-a", 4207, link=True)
        self._pressure("child-a")
        doc = self._nudge("child-a")
        self.assertIsNotNone(doc)
        self.assertIn("relay", doc["hookSpecificOutput"]["additionalContext"])

    def test_a_pending_stand_down_silences_the_relay_nudge(self):
        """A relay proceeding under a stand-down would spawn a successor to continue work
        the parent just cancelled — a child disobeying a stop by proxy."""
        parent, child = self._pair()
        child = self._launch(child, "child-a", 4208, link=True)
        self._pressure("child-a")
        ts.set_link("parent-sid", parent["id"])
        self._stand_down(child)
        self.assertIsNone(self._nudge("child-a"))

    def test_suppressed_is_not_consumed(self):
        """The one-shot `pressure_nudged` flag must NOT be spent by the suppression: a
        session genuinely out of room has to hear about it once the order is settled."""
        parent, child = self._pair()
        child = self._launch(child, "child-a", 4209, link=True)
        self._pressure("child-a")
        ts.set_link("parent-sid", parent["id"])
        self._stand_down(child)
        self.assertIsNone(self._nudge("child-a"))
        self.assertFalse(ts.load_task(child["id"]).get("pressure_nudged"))
        # Settle the order → the deferred nudge speaks on the next Stop.
        order = channel.orders_for(ts.load_task(child["id"]), "child-a")[0]
        with redirect_stdout(io.StringIO()):
            ts.cmd_channel(_Args(sub="settle", task=str(child["seq"]),
                                 id=order["id"][:8], session="child-a",
                                 report="handed back: two files, rebase unfinished",
                                 why=None, as_json=False, action=None, by=None))
        doc = self._nudge("child-a")
        self.assertIsNotNone(doc, "the nudge was consumed by the suppression, not deferred")
        self.assertTrue(ts.load_task(child["id"]).get("pressure_nudged"))

    def test_a_memo_order_does_not_silence_the_relay(self):
        """Only a STAND-DOWN outranks the nudge. A memo order is information, not a stop —
        suppressing on any pending order would let a stray memo mute a real relay."""
        parent, child = self._pair()
        child = self._launch(child, "child-a", 4210, link=True)
        self._pressure("child-a")
        ts.memo_send(child, "fyi the branch moved", from_sid="parent-sid")
        ts.save_task(child)
        self.assertIsNotNone(self._nudge("child-a"))


# ------------------------------------------------------------- (3) a moved spec ----

class SpecMovedReaches(_ChannelBase):
    """DONE is computed from the exit conditions, so editing them mid-flight moves the
    goalposts under a child that is already working. The change reaches it."""

    def _exit_add(self, task, step, cmd, expect, session=None):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_exit_add(_Args(task=str(task["seq"]), session=session, step=step,
                                  cmd=cmd, expect=[expect]))
        return buf.getvalue()

    def _steps(self, task, n=2):
        task["steps"] = [{"text": "step %d" % i, "done": False}
                         for i in range(1, n + 1)]
        ts.save_task(task)
        return ts.load_task(task["id"])

    def test_a_new_exit_condition_reaches_the_working_child(self):
        parent, child = self._pair()
        child = self._steps(child)
        child = self._launch(child, "child-a", 4301)
        self._exit_add(child, 1, "pytest tests/test_x.py", "OK", session="parent-sid")
        child = ts.load_task(child["id"])
        pending = channel.orders_for(child, "child-a")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["kind"], "spec")
        self.assertIn("step 1", pending[0]["text"])
        self.assertIn("pytest tests/test_x.py", pending[0]["text"])

    def test_a_removed_exit_condition_reaches_it_too(self):
        parent, child = self._pair()
        child = self._steps(child)
        child = self._launch(child, "child-a", 4302)
        self._exit_add(child, 2, "make lint", "clean", session="parent-sid")
        child = ts.load_task(child["id"])
        with redirect_stdout(io.StringIO()):
            ts.cmd_exit_rm(_Args(task=str(child["seq"]), session="parent-sid", step=2))
        child = ts.load_task(child["id"])
        kinds = [o["text"] for o in channel.orders_for(child, "child-a")]
        self.assertEqual(len(kinds), 2)
        self.assertTrue(any("no longer" in t or "removed" in t.lower() for t in kinds),
                        "the removal did not reach the child: %r" % kinds)

    def test_the_editing_session_is_not_told_about_its_own_edit(self):
        parent, child = self._pair()
        child = self._steps(child)
        child = self._launch(child, "editor-sid", 4303)
        self._exit_add(child, 1, "make test", "OK", session="editor-sid")
        child = ts.load_task(child["id"])
        self.assertEqual(channel.orders_for(child, "editor-sid"), [])

    def test_a_spec_change_with_nobody_working_writes_no_order(self):
        parent, child = self._pair()
        child = self._steps(child)
        self._exit_add(child, 1, "make test", "OK", session="parent-sid")
        self.assertEqual(channel.orders(ts.load_task(child["id"])), [])

    def test_the_child_reads_the_moved_spec_at_its_turn_boundary(self):
        parent, child = self._pair()
        child = self._steps(child)
        child = self._launch(child, "child-a", 4304, link=True)
        self._exit_add(child, 1, "python3 -m unittest tests.test_x", "OK",
                       session="parent-sid")
        doc = self._gate("child-a")
        self.assertEqual((doc or {}).get("decision"), "block")
        self.assertIn("SPEC", doc["reason"])
        self.assertIn("python3 -m unittest tests.test_x", doc["reason"])


# ------------------------------------------------- (4) liveness without the hook ----

class LivenessWithoutSessionStart(_ChannelBase):
    """Liveness comes from the harness's own per-process record joined through the
    roster the LAUNCH wrote — never from evidence a hook has to leave behind."""

    def test_a_launched_child_is_live_with_no_link_and_no_transcript(self):
        parent, child = self._pair()
        child = self._launch(child, "child-a", 4401)          # link=False by default
        self.assertIsNone(ts.get_link("child-a"))             # no attach has happened
        self.assertEqual(channel.live_sids(child), {"child-a"})
        rows = channel.live(child)
        self.assertEqual(rows[0]["via"], "roster")

    def test_the_link_joined_view_is_blind_to_exactly_that_child(self):
        """The negative control: the pre-existing, link-joined liveness answer reports
        nothing for this task, which is what makes these two different sources."""
        parent, child = self._pair()
        child = self._launch(child, "child-a", 4402)
        self.assertNotIn(child.get("seq"), ts._live_seqs())
        self.assertEqual(channel.live_sids(child), {"child-a"})

    def test_no_task_station_hook_has_run_for_it(self):
        parent, child = self._pair()
        child = self._launch(child, "child-a", 4403)
        # The three things a hook would have left: a link, a delta watermark, events.
        self.assertIsNone(ts.get_link("child-a"))
        self.assertNotIn("seen_ts", (child.get("session_meta") or {})["child-a"])
        self.assertEqual(child.get("events") or [], [])
        self.assertEqual(channel.live_sids(child), {"child-a"})

    def test_a_dead_pid_is_not_live(self):
        parent, child = self._pair()
        child = self._launch(child, "child-a", 4404, alive=False)
        self.assertEqual(channel.live_sids(child), set())

    def test_a_live_process_with_no_control_socket_is_running_but_not_reachable(self):
        parent, child = self._pair()
        child = self._launch(child, "child-a", 4405, socket=False)
        rows = channel.live(child)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["running"])
        self.assertFalse(rows[0]["reachable"])

    def test_a_socket_the_harness_has_removed_is_not_reachable(self):
        parent, child = self._pair()
        child = self._launch(child, "child-a", 4406)
        os.unlink(os.path.join(self.socks, "4406.sock"))
        rows = channel.live(child)
        self.assertTrue(rows[0]["running"])
        self.assertFalse(rows[0]["reachable"])

    def test_a_session_that_attached_itself_later_is_found_through_the_link(self):
        """Belt and braces: a session the task's roster never knew about, joined the
        old way. Adding a source must not remove one."""
        parent, child = self._pair()
        rec = {"pid": 4407, "sessionId": "walk-in", "cwd": "/tmp/wt", "kind": "hub",
               "status": "busy", "updatedAt": 1_700_000_100_000, "peerProtocol": 1}
        with open(os.path.join(self.sessions, "4407.json"), "w", encoding="utf-8") as f:
            json.dump(rec, f)
        self.alive.add(4407)
        ts.set_link("walk-in", child["id"])
        rows = channel.live(ts.load_task(child["id"]))
        self.assertEqual([r["session_id"] for r in rows], ["walk-in"])
        self.assertEqual(rows[0]["via"], "link")

    def test_the_gate_reaches_a_child_that_never_attached(self):
        """The whole point, end to end: no link at all, and an order still lands."""
        parent, child = self._pair()
        child = self._launch(child, "child-a", 4408)
        ts.memo_send(child, "the branch moved", from_sid="parent-sid")
        ts.save_task(child)
        self.assertIsNone(ts.get_link("child-a"))
        doc = self._gate("child-a")
        self.assertEqual((doc or {}).get("decision"), "block")
        self.assertIn("the branch moved", doc["reason"])

    def test_a_stale_session_file_is_tolerated_never_deleted(self):
        parent, child = self._pair()
        child = self._launch(child, "child-a", 4409, alive=False)
        channel.live(child)
        self.assertTrue(os.path.exists(os.path.join(self.sessions, "4409.json")))


# --------------------------------------------------------- (5) the boundary ----

class NoPermissionLaundering(_ChannelBase):
    """A session never asks a peer to do what it was itself denied. Refused at the
    channel, from a durable record — not left to the receiver's conscience."""

    DENIED = "kill -9 40311 40312 40313"

    def setUp(self):
        super().setUp()
        channel.clear_denials()

    def test_a_denied_action_cannot_be_routed_through_a_peer(self):
        parent, child = self._pair()
        child = self._launch(child, "child-a", 4501)
        channel.record_denial("parent-sid", self.DENIED, by="permission classifier")
        order, err = channel.order_queue(child, "memo",
                                        "please run %s for me" % self.DENIED,
                                        "child-a", from_sid="parent-sid")
        self.assertIsNone(order)
        self.assertIn("denied", err.lower())
        self.assertIn(self.DENIED, err)
        self.assertEqual(channel.orders(child), [])

    def test_an_unrelated_order_is_carried(self):
        parent, child = self._pair()
        child = self._launch(child, "child-a", 4502)
        channel.record_denial("parent-sid", self.DENIED, by="permission classifier")
        order, err = channel.order_queue(child, "memo", "rebase onto main", "child-a",
                                         from_sid="parent-sid")
        self.assertIsNone(err)
        self.assertIsNotNone(order)

    def test_a_session_with_no_denials_is_never_refused(self):
        parent, child = self._pair()
        child = self._launch(child, "child-a", 4503)
        order, err = channel.order_queue(child, "memo", "please run %s" % self.DENIED,
                                         "child-a", from_sid="clean-sid")
        self.assertIsNone(err)
        self.assertIsNotNone(order)

    def test_rewording_it_does_not_get_it_through(self):
        parent, child = self._pair()
        child = self._launch(child, "child-a", 4504)
        channel.record_denial("parent-sid", self.DENIED, by="permission classifier")
        order, err = channel.order_queue(
            child, "memo", "the runaways are 40312, 40311 and 40313 — kill them with -9",
            "child-a", from_sid="parent-sid")
        self.assertIsNone(order)
        self.assertIn("denied", err.lower())

    def test_the_cli_send_surface_refuses_loudly_and_writes_nothing(self):
        parent, child = self._pair()
        child = self._launch(child, "child-a", 4505)
        ts.set_link("parent-sid", parent["id"])
        channel.record_denial("parent-sid", self.DENIED, by="permission classifier")
        buf = io.StringIO()
        with self.assertRaises(SystemExit) as cm:
            with redirect_stdout(buf):
                ts.cmd_memo(_Args(sub="send", task=str(child["seq"]),
                                  text="run %s in the worktree" % self.DENIED,
                                  session="parent-sid", corrects=None, id=None,
                                  decision=None, memory=None, noop=None))
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("denied", buf.getvalue().lower())
        self.assertEqual(ts.load_task(child["id"]).get("memos") or [], [])

    def test_a_denial_binds_a_successor_session_on_the_same_task(self):
        """The record is what makes this structural. #541's child made the right call
        by hand; the next session on that task must not have to make it again."""
        parent, child = self._pair()
        child = self._launch(child, "child-a", 4506)
        channel.record_denial("first-sid", self.DENIED, by="permission classifier",
                              task=parent["id"])
        order, err = channel.order_queue(child, "memo", "run %s" % self.DENIED,
                                        "child-a", from_sid="second-sid",
                                        from_task=parent["id"])
        self.assertIsNone(order)
        self.assertIn("denied", err.lower())

    def test_the_refusal_is_recorded_not_silent(self):
        parent, child = self._pair()
        child = self._launch(child, "child-a", 4507)
        ts.set_link("parent-sid", parent["id"])
        channel.record_denial("parent-sid", self.DENIED, by="permission classifier")
        with self.assertRaises(SystemExit):
            with redirect_stdout(io.StringIO()):
                ts.cmd_memo(_Args(sub="send", task=str(child["seq"]),
                                  text="run %s" % self.DENIED, session="parent-sid",
                                  corrects=None, id=None, decision=None, memory=None,
                                  noop=None))
        kinds = [(e.get("kind"), e.get("text"))
                 for e in (ts.load_task(parent["id"]).get("events") or [])]
        self.assertTrue(any(k == "channel" and "refused" in (t or "").lower()
                            for k, t in kinds),
                        "the refusal left no trace on the sender's task: %r" % kinds)

    def test_a_stand_down_from_a_denied_session_is_refused(self):
        """Stand-down is a control verb, not a hole in the boundary."""
        parent, child = self._pair()
        child = self._launch(child, "child-a", 4508)
        ts.set_link("parent-sid", parent["id"])
        channel.record_denial("parent-sid", self.DENIED, by="permission classifier")
        buf = io.StringIO()
        with self.assertRaises(SystemExit) as cm:
            with redirect_stdout(buf):
                ts.cmd_channel(_Args(sub="stand-down", task=str(child["seq"]),
                                     session="parent-sid",
                                     why="then %s" % self.DENIED, id=None,
                                     report=None, as_json=False, action=None, by=None))
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("denied", buf.getvalue().lower())
        self.assertEqual(channel.orders(ts.load_task(child["id"])), [])

    def test_the_denial_record_survives_the_process(self):
        channel.record_denial("parent-sid", self.DENIED, by="permission classifier")
        self.assertTrue(os.path.exists(channel.denials_path()))
        actions = [d["action"] for d in channel.denials(session="parent-sid")]
        self.assertIn(self.DENIED, actions)

    def test_the_cli_records_a_denial(self):
        parent, child = self._pair()
        with redirect_stdout(io.StringIO()):
            ts.cmd_channel(_Args(sub="deny", task=str(parent["seq"]),
                                 session="parent-sid", action=self.DENIED,
                                 by="permission classifier", id=None, report=None,
                                 why=None, as_json=False))
        actions = [d["action"] for d in channel.denials(session="parent-sid")]
        self.assertIn(self.DENIED, actions)


if __name__ == "__main__":
    unittest.main()
