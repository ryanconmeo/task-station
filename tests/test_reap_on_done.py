"""Task #464/#465 — reap still-LIVE --bg workers when a task is closed, AIRTIGHTLY.

Two layers:
  * delegate.reap_task_workers — the airtight safety predicate (registry+seq ∩
    roster role==worker ∩ task-station name ∩ not-busy ∩ not-current), session-store
    file removal BEFORE the kill, the config kill-switch, and its best-effort guards.
  * task-station cmd_done — both close paths (--session and --task) invoke the reap
    (passing the roster + closing session) and record a `stop` ledger entry per reaped
    worker; a failing reap never breaks the close.

No real `claude`, no real processes: a scripted fake adapter, a captured kill, and a
temp session-store dir.
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

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)
sys.path.insert(0, os.path.join(LIB, "delegate"))

import store  # noqa: E402
import delegate as dg  # noqa: E402

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


class FakeAdapter:
    """Scripted `claude agents --json` index."""
    def __init__(self, index):
        self.index = dict(index)

    def agents_index(self, cwd=None):
        return dict(self.index)


def _wname(seq=7, project="proj"):
    """A canonical task-station worker display name (`task-station-<seq>-<project>`)."""
    return "task-station-%s-%s" % (seq, project)


# ---------------------------------------------------------------- delegate ----

class ReapTaskWorkersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-reap-")
        self.sessdir = tempfile.mkdtemp(prefix="ts-sess-")
        self.saved = (dg.REG_DIR, dg.REG, dg._kill_pid_group, dg.SESSIONS_DIR)
        dg.REG_DIR = self.tmp
        dg.REG = os.path.join(self.tmp, "workers.json")
        dg.SESSIONS_DIR = self.sessdir
        self.killed = []
        dg._kill_pid_group = lambda pid, **k: self.killed.append(pid)
        # Reap is gated on config (default on) — pin ON so the identity predicate,
        # not the kill-switch, is what each test exercises. The off-test overrides.
        os.environ["TASK_STATION_REAP_WORKERS_ON_DONE"] = "on"

    def tearDown(self):
        dg.REG_DIR, dg.REG, dg._kill_pid_group, dg.SESSIONS_DIR = self.saved
        os.environ.pop("TASK_STATION_REAP_WORKERS_ON_DONE", None)
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.sessdir, ignore_errors=True)

    def _reg(self, d):
        with open(dg.REG, "w") as f:
            json.dump(d, f)

    def _seed_session_file(self, sid, fn=None, extra=None):
        """Write a fake ClaudeCode.app session-store file keyed by `sessionId` (the
        filename is deliberately NOT the sid, matching the real store)."""
        fn = fn or ("store-" + sid + ".json")
        path = os.path.join(self.sessdir, fn)
        blob = {"sessionId": sid, "pid": 999}
        if extra:
            blob.update(extra)
        with open(path, "w") as f:
            json.dump(blob, f)
        return path

    def _worker(self, sid, name=None):
        """A role==worker roster entry (task.session_meta shape)."""
        return {sid: {"role": "worker", "name": name}}

    # --- the one case that SHOULD reap ---------------------------------------

    def test_reaps_genuine_idle_worker(self):
        # registry+seq  ∩  roster role==worker  ∩  task-station name  ∩  idle  →  reaped.
        self._reg({"7:proj": {"seq": 7, "session_id": "w1"}})
        sf = self._seed_session_file("w1")
        ad = FakeAdapter({"w1": {"sessionId": "w1", "pid": 111, "status": "idle",
                                 "name": _wname()}})
        roster = self._worker("w1", name=_wname())
        self.assertEqual(dg.reap_task_workers(7, adapter=ad, roster=roster), ["w1"])
        self.assertEqual(self.killed, [111])            # process group killed
        self.assertFalse(os.path.exists(sf))            # …AND session file removed first

    # --- the four cases that MUST NOT reap (safety) --------------------------

    def test_does_not_reap_hub_role(self):
        # Even a session in the registry with matching seq is spared if the roster
        # marks it role==hub (a real user session that mis-attached to the task).
        self._reg({"7:proj": {"seq": 7, "session_id": "hubx"}})
        sf = self._seed_session_file("hubx")
        ad = FakeAdapter({"hubx": {"sessionId": "hubx", "pid": 5, "status": "idle",
                                   "name": _wname()}})
        roster = {"hubx": {"role": "hub", "name": "7-1"}}
        self.assertEqual(dg.reap_task_workers(7, adapter=ad, roster=roster), [])
        self.assertEqual(self.killed, [])
        self.assertTrue(os.path.exists(sf))             # file untouched

    def test_does_not_reap_roster_only_session(self):
        # A session that merely ATTACHED (role==worker in the roster) but is NOT in
        # workers.json is excluded — no reap on roster-attachment alone.
        self._reg({})
        sf = self._seed_session_file("stray")
        ad = FakeAdapter({"stray": {"sessionId": "stray", "pid": 8, "status": "idle",
                                    "name": _wname()}})
        roster = self._worker("stray", name=_wname())
        self.assertEqual(dg.reap_task_workers(7, adapter=ad, roster=roster), [])
        self.assertEqual(self.killed, [])
        self.assertTrue(os.path.exists(sf))

    def test_does_not_reap_busy_worker(self):
        # A worker mid-turn (busy) is left alone even though it otherwise qualifies.
        self._reg({"7:proj": {"seq": 7, "session_id": "w1"}})
        sf = self._seed_session_file("w1")
        ad = FakeAdapter({"w1": {"sessionId": "w1", "pid": 111, "status": "busy",
                                 "name": _wname()}})
        roster = self._worker("w1", name=_wname())
        self.assertEqual(dg.reap_task_workers(7, adapter=ad, roster=roster), [])
        self.assertEqual(self.killed, [])
        self.assertTrue(os.path.exists(sf))

    def test_does_not_reap_current_session(self):
        # The closing/current session is never reaped.
        self._reg({"7:proj": {"seq": 7, "session_id": "w1"}})
        sf = self._seed_session_file("w1")
        ad = FakeAdapter({"w1": {"sessionId": "w1", "pid": 111, "status": "idle",
                                 "name": _wname()}})
        roster = self._worker("w1", name=_wname())
        self.assertEqual(
            dg.reap_task_workers(7, adapter=ad, roster=roster, current_sid="w1"), [])
        self.assertEqual(self.killed, [])
        self.assertTrue(os.path.exists(sf))

    def test_does_not_reap_non_ts_named(self):
        # A registry+roster worker whose live name isn't task-station-shaped is spared.
        self._reg({"7:proj": {"seq": 7, "session_id": "w1"}})
        ad = FakeAdapter({"w1": {"sessionId": "w1", "pid": 111, "status": "idle",
                                 "name": "some-random-agent"}})
        roster = self._worker("w1", name="some-random-agent")
        self.assertEqual(dg.reap_task_workers(7, adapter=ad, roster=roster), [])
        self.assertEqual(self.killed, [])

    # --- config kill-switch --------------------------------------------------

    def test_noop_when_disabled(self):
        os.environ["TASK_STATION_REAP_WORKERS_ON_DONE"] = "off"
        self._reg({"7:proj": {"seq": 7, "session_id": "w1"}})
        sf = self._seed_session_file("w1")
        ad = FakeAdapter({"w1": {"sessionId": "w1", "pid": 111, "status": "idle",
                                 "name": _wname()}})
        roster = self._worker("w1", name=_wname())
        self.assertEqual(dg.reap_task_workers(7, adapter=ad, roster=roster), [])
        self.assertEqual(self.killed, [])
        self.assertTrue(os.path.exists(sf))

    # --- version-tolerance of the session store ------------------------------

    def test_broken_session_store_never_raises(self):
        # Point the store at a non-directory → os.listdir raises inside the helper,
        # which must swallow it; the kill still proceeds and the reap succeeds.
        self._reg({"7:proj": {"seq": 7, "session_id": "w1"}})
        broken = os.path.join(self.tmp, "not-a-dir")
        with open(broken, "w") as f:
            f.write("x")
        dg.SESSIONS_DIR = broken
        ad = FakeAdapter({"w1": {"sessionId": "w1", "pid": 111, "status": "idle",
                                 "name": _wname()}})
        roster = self._worker("w1", name=_wname())
        self.assertEqual(dg.reap_task_workers(7, adapter=ad, roster=roster), ["w1"])
        self.assertEqual(self.killed, [111])

    def test_odd_schema_store_files_skipped(self):
        # A non-JSON file and a JSON file with no sessionId are skipped without raising;
        # the correct matching file is still removed.
        self._reg({"7:proj": {"seq": 7, "session_id": "w1"}})
        with open(os.path.join(self.sessdir, "garbage.json"), "w") as f:
            f.write("}{not json")
        with open(os.path.join(self.sessdir, "no-sid.json"), "w") as f:
            json.dump({"foo": "bar"}, f)
        with open(os.path.join(self.sessdir, "readme.txt"), "w") as f:
            f.write("ignore me")
        sf = self._seed_session_file("w1")
        ad = FakeAdapter({"w1": {"sessionId": "w1", "pid": 111, "status": "idle",
                                 "name": _wname()}})
        roster = self._worker("w1", name=_wname())
        self.assertEqual(dg.reap_task_workers(7, adapter=ad, roster=roster), ["w1"])
        self.assertFalse(os.path.exists(sf))

    def test_missing_session_store_still_kills(self):
        # No session file on disk at all → removal is a no-op, kill still happens.
        self._reg({"7:proj": {"seq": 7, "session_id": "w1"}})
        ad = FakeAdapter({"w1": {"sessionId": "w1", "pid": 111, "status": "idle",
                                 "name": _wname()}})
        roster = self._worker("w1", name=_wname())
        self.assertEqual(dg.reap_task_workers(7, adapter=ad, roster=roster), ["w1"])
        self.assertEqual(self.killed, [111])

    # --- existing best-effort / identity guards ------------------------------

    def test_only_matching_seq_reaped(self):
        self._reg({"7:proj": {"seq": 7, "session_id": "w7"},
                   "8:proj": {"seq": 8, "session_id": "w8"}})
        ad = FakeAdapter({"w7": {"sessionId": "w7", "pid": 1, "status": "idle", "name": _wname()},
                          "w8": {"sessionId": "w8", "pid": 2, "status": "idle", "name": _wname(8)}})
        roster = {"w7": {"role": "worker", "name": _wname()},
                  "w8": {"role": "worker", "name": _wname(8)}}
        self.assertEqual(dg.reap_task_workers(7, adapter=ad, roster=roster), ["w7"])
        self.assertEqual(self.killed, [1])

    def test_skips_zombie_no_pid(self):
        self._reg({"7:proj": {"seq": 7, "session_id": "w1"}})
        ad = FakeAdapter({"w1": {"sessionId": "w1", "pid": None, "status": "idle", "name": _wname()}})
        roster = self._worker("w1", name=_wname())
        self.assertEqual(dg.reap_task_workers(7, adapter=ad, roster=roster), [])
        self.assertEqual(self.killed, [])

    def test_skips_gone_worker(self):
        self._reg({"7:proj": {"seq": 7, "session_id": "w1"}})
        ad = FakeAdapter({})            # not listed → Claude Code already pruned it
        roster = self._worker("w1", name=_wname())
        self.assertEqual(dg.reap_task_workers(7, adapter=ad, roster=roster), [])
        self.assertEqual(self.killed, [])

    def test_prefix_match_short_stored_id(self):
        self._reg({"7:proj": {"seq": 7, "session_id": "abc123"}})
        sf = self._seed_session_file("abc123-full-uuid")
        ad = FakeAdapter({"abc123-full-uuid": {"sessionId": "abc123-full-uuid", "pid": 9,
                                               "status": "idle", "name": _wname()}})
        roster = self._worker("abc123", name=_wname())
        self.assertEqual(dg.reap_task_workers(7, adapter=ad, roster=roster), ["abc123"])
        self.assertEqual(self.killed, [9])
        self.assertFalse(os.path.exists(sf))            # matched by full sessionId

    def test_kill_failure_swallowed_and_not_counted(self):
        self._reg({"7:proj": {"seq": 7, "session_id": "w7"}})
        ad = FakeAdapter({"w7": {"sessionId": "w7", "pid": 1, "status": "idle", "name": _wname()}})
        roster = self._worker("w7", name=_wname())

        def boom(pid, **k):
            raise RuntimeError("nope")
        dg._kill_pid_group = boom
        self.assertEqual(dg.reap_task_workers(7, adapter=ad, roster=roster), [])  # no raise, not counted

    def test_adapter_agents_index_failure_noop(self):
        self._reg({"7:proj": {"seq": 7, "session_id": "w7"}})

        class BadAd:
            def agents_index(self, cwd=None):
                raise RuntimeError("no claude")
        roster = self._worker("w7", name=_wname())
        self.assertEqual(dg.reap_task_workers(7, adapter=BadAd(), roster=roster), [])
        self.assertEqual(self.killed, [])

    def test_no_candidates_noop(self):
        self._reg({})
        self.assertEqual(dg.reap_task_workers(7, adapter=FakeAdapter({}), roster={}), [])


# --------------------------------------------------------------- cmd_done ----

def _repoint(tmp):
    os.environ["TASK_STATION_HOME"] = tmp
    ts.DATA = tmp
    ts.STORE = os.path.join(tmp, "store")
    ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
    ts.LINKS_DIR = os.path.join(ts.STORE, "links")
    ts.PROJECTS_ROOT = os.path.join(tmp, "projects")
    store.reset_cache()


class _Args:
    def __init__(self, task=None, session=None):
        self.task = task
        self.session = session


class ReapOnDoneIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _repoint(self.tmp)
        self.calls = []
        outer = self

        class StubDG:
            @staticmethod
            def reap_task_workers(seq, roster=None, current_sid=None):
                outer.calls.append((seq, dict(roster or {}), current_sid))
                return ["wsid-live"]
        self._orig = ts._delegate_module
        ts._delegate_module = lambda: StubDG

    def tearDown(self):
        ts._delegate_module = self._orig
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed_with_worker(self, session=None):
        t = ts.new_task("Reapable", "close me")
        ts.save_task(t)
        ts.ensure_seqs()
        t = ts.load_task(t["id"])
        if session:
            ts.touch(t, session=session, note="hub")     # a real hub session
        ts.register_worker_session(t, "wsid-live", name="wk-1", status="running")
        ts.save_task(t)
        return ts.load_task(t["id"])

    def _done(self, args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_done(args)
        return buf.getvalue()

    def test_session_close_reaps_and_ledgers_stop(self):
        sess = "hub-sess"
        t = self._seed_with_worker(session=sess)
        ts.set_link(sess, t["id"])
        self._done(_Args(session=sess))
        t2 = ts.load_task(t["id"])
        self.assertEqual(t2["status"], "closed")
        # reap called with this task's seq, the roster (worker present), and the
        # closing hub as the current session.
        self.assertEqual(len(self.calls), 1)
        seq, roster, current_sid = self.calls[0]
        self.assertEqual(seq, t["seq"])
        self.assertEqual((roster.get("wsid-live") or {}).get("role"), "worker")
        self.assertEqual(current_sid, sess)
        # a `stop` ledger entry naming the reaped worker + the closing hub as actor.
        stops = [e for e in t2.get("ledger", []) if e.get("action") == "stop"]
        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[0]["worker"], "wsid-live")
        self.assertEqual(stops[0]["actor"], sess)
        # roster flipped to stopped.
        self.assertEqual(t2["session_meta"]["wsid-live"]["status"], "stopped")

    def test_close_by_task_ref_also_reaps(self):
        t = self._seed_with_worker()
        self._done(_Args(task=str(t["seq"])))
        t2 = ts.load_task(t["id"])
        self.assertEqual(t2["status"], "closed")
        self.assertEqual(len(self.calls), 1)
        stops = [e for e in t2.get("ledger", []) if e.get("action") == "stop"]
        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[0]["worker"], "wsid-live")

    def test_reap_failure_never_breaks_close(self):
        class BoomDG:
            @staticmethod
            def reap_task_workers(seq, roster=None, current_sid=None):
                raise RuntimeError("delegate blew up")
        ts._delegate_module = lambda: BoomDG
        t = self._seed_with_worker()
        self._done(_Args(task=str(t["seq"])))
        self.assertEqual(ts.load_task(t["id"])["status"], "closed")   # still closed

    def test_missing_delegate_module_noop_close(self):
        ts._delegate_module = lambda: None
        t = self._seed_with_worker()
        self._done(_Args(task=str(t["seq"])))
        t2 = ts.load_task(t["id"])
        self.assertEqual(t2["status"], "closed")
        self.assertEqual([e for e in t2.get("ledger", []) if e.get("action") == "stop"], [])


if __name__ == "__main__":
    unittest.main()
