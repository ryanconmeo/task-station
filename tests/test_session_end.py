"""SessionEnd — the EXACT end-of-session pass (`task-station session-end`).

The SessionStart orphan sweep notices at the START of the NEXT session that a hub is
gone. This is the other half: a session ending CLEANLY knows its own id right now, so
it stamps its roster row with the reason, puts one line on the attached task's feed,
and stops the workers it spawned. The sweep stays as the crash backstop — SessionEnd
is not guaranteed to fire on a crash — so these tests also pin the properties that
make the two safe to run against each other.

What is nailed down here:
  1. the roster row gains `ended_ts` + `end_reason`, and ONE `session-end` event lands
  2. it is IDEMPOTENT — a second run stamps nothing and appends nothing
  3. `updated_ts` is NOT bumped (a session ending is not work on the task)
  4. an unattached / skipped session is a silent no-op
  5. only workers THIS session spawned are reaped — never another hub's, never a
     foreign agent, never the ending session itself
  6. no registry candidates → not one subprocess (the 1.5s shared budget)
  7. it never raises, whatever the store or the registry does

No real `claude`, no real processes: a fake agents adapter and a captured kill, the
same fixtures the orphan-sweep tests use.
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)
sys.path.insert(0, os.path.join(LIB, "delegate"))

import delegate as dg          # noqa: E402
import store as store_mod      # noqa: E402

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)

WNAME = "task-station-7-3-projectname"      # a canonical worker name


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeAdapter:
    """Scripted `claude agents --json` index."""
    def __init__(self, index):
        self.index = dict(index)

    def agents_index(self, cwd=None):
        return dict(self.index)


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-session-end-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.PROJECTS_ROOT = os.path.join(self.tmp, "projects")
        store_mod.reset_cache()
        self.saved = (dg.REG_DIR, dg.REG, dg.SESSIONS_DIR, dg._kill_pid_group)
        dg.REG_DIR = self.tmp
        dg.REG = os.path.join(self.tmp, "workers.json")
        dg.SESSIONS_DIR = os.path.join(self.tmp, "bgstore")
        os.makedirs(dg.SESSIONS_DIR, exist_ok=True)
        self.killed = []
        dg._kill_pid_group = lambda pid, **k: self.killed.append(pid)
        os.environ["TASK_STATION_REAP_WORKERS_ON_DONE"] = "on"

    def tearDown(self):
        (dg.REG_DIR, dg.REG, dg.SESSIONS_DIR, dg._kill_pid_group) = self.saved
        for k in ("TASK_STATION_HOME", "TASK_STATION_REAP_WORKERS_ON_DONE"):
            os.environ.pop(k, None)
        store_mod.reset_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- fixtures --------------------------------------------------------------

    def _task(self, session=None):
        t = ts.new_task("A tracked task", "summary")
        ts.save_task(t)
        ts.ensure_seqs()
        t = ts.load_task(t["id"])
        if session:
            ts.touch(t, session=session, note="attached")
            ts.save_task(t)
            ts.set_link(session, t["id"])
        return ts.load_task(t["id"])

    def _worker_on(self, task, worker_sid="wk-1", name=WNAME):
        ts.register_worker_session(task, worker_sid, name=name, status="running")
        ts.save_task(task)
        return ts.load_task(task["id"])

    def _reg(self, seq, worker_sid="wk-1", spawner="hub-1", name=WNAME):
        entry = {"seq": seq, "session_id": worker_sid, "project": "projectname",
                 "label": None, "dir": "/work", "spawner": spawner,
                 "started_ts": ts._now() - 10_000}
        if name is not None:
            entry["name"] = name
        with open(dg.REG, "w") as f:
            json.dump({"%s:projectname" % seq: entry}, f)

    def _agents(self, worker_sid="wk-1", name=WNAME, status="idle", pid=111):
        return FakeAdapter({worker_sid: {"sessionId": worker_sid, "pid": pid,
                                         "status": status, "name": name}})

    def _run(self, session, reason="clear"):
        """Run the CLI handler; return its STDOUT. stderr (where reap lines go) is
        swallowed so the suite's own output stays clean."""
        buf, errbuf = io.StringIO(), io.StringIO()
        with redirect_stdout(buf), redirect_stderr(errbuf):
            ts.cmd_session_end(_Args(session=session, reason=reason))
        self.stderr = errbuf.getvalue()
        return buf.getvalue()

    def _session_end_events(self, task_id):
        return [e for e in (ts.load_task(task_id).get("events") or [])
                if e["kind"] == "session-end"]


# ======================================================= the reason normaliser ===
class ReasonTest(unittest.TestCase):
    def test_documented_reasons_pass_through(self):
        for r in ("clear", "resume", "logout", "prompt_input_exit",
                  "bypass_permissions_disabled", "other"):
            self.assertEqual(ts._end_reason(r), r)

    def test_an_unknown_reason_is_kept_verbatim(self):
        """A newer build's new reason is the ONE fact worth recording — folding it
        into 'other' would destroy it."""
        self.assertEqual(ts._end_reason("window_closed"), "window_closed")

    def test_empty_becomes_other(self):
        self.assertEqual(ts._end_reason(""), "other")
        self.assertEqual(ts._end_reason(None), "other")

    def test_garbage_is_collapsed_and_capped(self):
        got = ts._end_reason("  a\n\n  b  " + "x" * 200)
        self.assertLessEqual(len(got), ts.SESSION_END_REASON_MAX)
        self.assertTrue(got.startswith("a b"))


# ============================================== the roster row + the feed line ===
class RosterAndFeedTest(_Base):
    def test_stamps_the_roster_row_and_appends_one_event(self):
        task = self._task(session="hub-1")
        self._run("hub-1", reason="logout")
        t = ts.load_task(task["id"])
        row = t["session_meta"]["hub-1"]
        self.assertEqual(row["end_reason"], "logout")
        self.assertGreater(row["ended_ts"], 0)
        ends = [e for e in t.get("events", []) if e["kind"] == "session-end"]
        self.assertEqual(len(ends), 1)
        self.assertIn("logout", ends[0]["text"])
        self.assertEqual(ends[0]["sid"], "hub-1")

    def test_is_idempotent(self):
        """Running twice — a resume that ends again, a double-fired hook — stamps
        once and appends once."""
        task = self._task(session="hub-1")
        self._run("hub-1", reason="clear")
        first = ts.load_task(task["id"])["session_meta"]["hub-1"]["ended_ts"]
        self._run("hub-1", reason="logout")
        t = ts.load_task(task["id"])
        self.assertEqual(t["session_meta"]["hub-1"]["ended_ts"], first)
        self.assertEqual(t["session_meta"]["hub-1"]["end_reason"], "clear")
        self.assertEqual(len([e for e in t["events"] if e["kind"] == "session-end"]), 1)

    def test_does_not_bump_updated_ts(self):
        """Ending a session is not work on the task: bumping updated_ts would let a
        /clear reorder the board and reset every staleness window."""
        task = self._task(session="hub-1")
        before = ts.load_task(task["id"])["updated_ts"]
        self._run("hub-1")
        self.assertEqual(ts.load_task(task["id"])["updated_ts"], before)

    def test_roster_row_keeps_its_existing_fields(self):
        task = self._task(session="hub-1")
        before = ts.load_task(task["id"])["session_meta"]["hub-1"]
        self._run("hub-1")
        after = ts.load_task(task["id"])["session_meta"]["hub-1"]
        self.assertEqual(after.get("role"), before.get("role"))
        self.assertEqual(after.get("ordinal"), before.get("ordinal"))

    def test_attached_session_with_no_roster_row_still_records(self):
        task = self._task()
        ts.set_link("ghost", task["id"])          # linked, never rostered
        self._run("ghost", reason="resume")
        row = ts.load_task(task["id"])["session_meta"]["ghost"]
        self.assertEqual(row["end_reason"], "resume")

    def test_unattached_session_is_a_silent_noop(self):
        self._task()                              # a task exists, nobody attached
        self.assertEqual(self._run("nobody"), "")

    def test_skipped_session_is_a_silent_noop(self):
        task = self._task()
        ts.set_link("skipme", ts.SKIP_SENTINEL)
        self._run("skipme")
        self.assertEqual(self._session_end_events(task["id"]), [])

    def test_no_session_id_does_nothing(self):
        self._run("unknown")                      # hookjson's default when absent
        self._run("")

    def test_a_broken_store_never_raises(self):
        task = self._task(session="hub-1")
        boom = ts.mutate
        ts.mutate = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("store down"))
        try:
            self._run("hub-1")                    # must not propagate
        finally:
            ts.mutate = boom
        self.assertEqual(self._session_end_events(task["id"]), [])


# ==================================================== reaping this session's own ==
class ReapOwnWorkersTest(_Base):
    def test_reaps_a_worker_this_session_spawned(self):
        task = self._task(session="hub-1")
        task = self._worker_on(task)
        self._reg(task["seq"], spawner="hub-1")
        reaped = ts.reap_own_workers("hub-1", adapter=self._agents(), reason="logout")
        self.assertEqual(reaped, [(task["seq"], "wk-1")])
        self.assertEqual(self.killed, [111])
        led = [e for e in ts.load_task(task["id"])["ledger"] if e["action"] == "stop"]
        self.assertEqual(len(led), 1)
        self.assertIn("session end", led[0]["detail"])
        self.assertIn("logout", led[0]["detail"])
        self.assertEqual(ts.load_task(task["id"])["session_meta"]["wk-1"]["status"],
                         "stopped")

    def test_never_another_hubs_worker(self):
        """The registry's `spawner` is the whole predicate: someone else's worker
        outliving THEIR hub is the orphan sweep's business, not ours."""
        task = self._task(session="hub-1")
        task = self._worker_on(task)
        self._reg(task["seq"], spawner="some-other-hub")
        self.assertEqual(ts.reap_own_workers("hub-1", adapter=self._agents()), [])
        self.assertEqual(self.killed, [])

    def test_never_a_foreign_agent(self):
        task = self._task(session="hub-1")
        task = self._worker_on(task, name="some-other-tools-agent")
        self._reg(task["seq"], spawner="hub-1", name="some-other-tools-agent")
        self.assertEqual(
            ts.reap_own_workers("hub-1",
                                adapter=self._agents(name="some-other-tools-agent")), [])
        self.assertEqual(self.killed, [])

    def test_entry_with_no_recorded_spawner_is_left_alone(self):
        task = self._task(session="hub-1")
        task = self._worker_on(task)
        self._reg(task["seq"], spawner=None)
        self.assertEqual(ts.reap_own_workers("hub-1", adapter=self._agents()), [])

    def test_never_the_ending_session_itself(self):
        task = self._task(session="hub-1")
        task = self._worker_on(task, worker_sid="hub-1")
        self._reg(task["seq"], worker_sid="hub-1", spawner="hub-1")
        self.assertEqual(
            ts.reap_own_workers("hub-1", adapter=self._agents(worker_sid="hub-1")), [])

    def test_a_busy_worker_is_left_running(self):
        """Inherited from reap_task_workers' predicate, and worth pinning here: the
        session ending does not make a worker mid-turn disposable."""
        task = self._task(session="hub-1")
        task = self._worker_on(task)
        self._reg(task["seq"], spawner="hub-1")
        self.assertEqual(
            ts.reap_own_workers("hub-1", adapter=self._agents(status="busy")), [])
        self.assertEqual(self.killed, [])

    def test_a_roster_hub_is_never_reaped(self):
        """role==worker in the roster is required — a hub session that happens to be
        registry-listed is not a worker."""
        task = self._task(session="hub-1")
        ts.touch(task, session="wk-1", note="a second hub")   # role == hub
        ts.save_task(task)
        self._reg(task["seq"], spawner="hub-1")
        self.assertEqual(ts.reap_own_workers("hub-1", adapter=self._agents()), [])

    def test_no_candidates_spends_no_subprocess(self):
        """The 1.5s shared SessionEnd budget: with nothing in the registry we must not
        even build an adapter, let alone shell out."""
        self._task(session="hub-1")
        calls = []

        class Boom:
            def agents_index(self, cwd=None):
                calls.append(1)
                raise AssertionError("must not be consulted")

        self.assertEqual(ts.reap_own_workers("hub-1", adapter=Boom()), [])
        self.assertEqual(calls, [])

    def test_no_agent_query_env_suppresses_the_probe(self):
        task = self._task(session="hub-1")
        task = self._worker_on(task)
        self._reg(task["seq"], spawner="hub-1")
        had = os.environ.get("TASK_STATION_NO_AGENT_QUERY")    # set by the test bootstrap
        os.environ["TASK_STATION_NO_AGENT_QUERY"] = "1"
        try:
            self.assertEqual(ts.reap_own_workers("hub-1"), [])  # no adapter → no probe
        finally:
            if had is None:
                os.environ.pop("TASK_STATION_NO_AGENT_QUERY", None)
            else:
                os.environ["TASK_STATION_NO_AGENT_QUERY"] = had
        self.assertEqual(self.killed, [])

    def test_no_session_reaps_nothing(self):
        self.assertEqual(ts.reap_own_workers(None), [])
        self.assertEqual(ts.reap_own_workers(""), [])


# ============================================================ the CLI, end to end ==
class CommandTest(_Base):
    def test_records_and_reaps_in_one_pass(self):
        task = self._task(session="hub-1")
        task = self._worker_on(task)
        self._reg(task["seq"], spawner="hub-1")
        real = ts.reap_own_workers
        ts.reap_own_workers = lambda s, adapter=None, reason="other": real(
            s, adapter=self._agents(), reason=reason)
        try:
            self._run("hub-1", reason="prompt_input_exit")
        finally:
            ts.reap_own_workers = real
        t = ts.load_task(task["id"])
        self.assertEqual(t["session_meta"]["hub-1"]["end_reason"], "prompt_input_exit")
        self.assertEqual(self.killed, [111])

    def test_stdout_stays_empty(self):
        """SessionEnd output is not read by anything — nothing this prints may look
        like hook output. Reaps go to stderr."""
        task = self._task(session="hub-1")
        self._worker_on(task)
        self.assertEqual(self._run("hub-1"), "")

    def test_a_failing_reap_never_fails_the_teardown(self):
        task = self._task(session="hub-1")
        real = ts.reap_own_workers
        ts.reap_own_workers = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope"))
        try:
            self._run("hub-1")
        finally:
            ts.reap_own_workers = real
        self.assertEqual(
            ts.load_task(task["id"])["session_meta"]["hub-1"]["end_reason"], "clear")


if __name__ == "__main__":
    unittest.main()
