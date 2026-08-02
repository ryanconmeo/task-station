"""SessionStart orphan sweep — reap task-station `--bg` workers whose SPAWNING HUB
SESSION IS GONE.

A `--bg` worker outliving its hub is normal (that is what makes it adoptable), but a
hub that CRASHED leaves its workers running forever. This sweep runs once per
SessionStart and stops exactly those.

The six safety rules, one test each:
  1. only names matching `_is_ts_worker_name` — never a foreign agent
  2. never the sweeping session's own workers (`current_sid` is passed through)
  3. DEAD MEANS THE PID IS GONE, not `status: idle`. `busy|idle` describes the model
     turn, not existence — a hub sitting idle waiting for the user is ALIVE and its
     workers must survive. This is the most likely way to get the feature wrong.
  4. absence of evidence is not death — an unreadable sessions dir or a failing
     agents call reaps NOTHING
  5. a grace period (a module-level constant) spares very new workers, whose hub's
     session file may not be written yet
  6. every reap is logged

No real `claude`, no real processes: a fake agents adapter, a captured kill, a temp
sessions dir, and a monkeypatched `pid_alive`.
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)
sys.path.insert(0, os.path.join(LIB, "delegate"))

import delegate as dg          # noqa: E402
import live_sessions           # noqa: E402
import store as store_mod      # noqa: E402

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


class FakeAdapter:
    """Scripted `claude agents --json` index."""
    def __init__(self, index):
        self.index = dict(index)

    def agents_index(self, cwd=None):
        return dict(self.index)


class BadAdapter:
    """An agents call that fails — 'unknown', never 'dead'."""
    def agents_index(self, cwd=None):
        raise RuntimeError("claude agents unavailable")


WNAME = "task-station-7-3-projectname"      # a canonical new-format worker name


class OrphanSweepTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-orphan-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.PROJECTS_ROOT = os.path.join(self.tmp, "projects")
        store_mod.reset_cache()

        # Where Claude Code drops one <PID>.json per RUNNING process.
        self.sessdir = os.path.join(self.tmp, "sessions")
        os.makedirs(self.sessdir, exist_ok=True)
        os.environ["TASK_STATION_SESSIONS_DIR"] = self.sessdir

        # The ClaudeCode.app supervisor's own session store (removed before a kill).
        self.storedir = os.path.join(self.tmp, "bgstore")
        os.makedirs(self.storedir, exist_ok=True)

        self.saved = (dg.REG_DIR, dg.REG, dg.SESSIONS_DIR, dg._kill_pid_group,
                      live_sessions.pid_alive)
        dg.REG_DIR = self.tmp
        dg.REG = os.path.join(self.tmp, "workers.json")
        dg.SESSIONS_DIR = self.storedir
        self.killed = []
        dg._kill_pid_group = lambda pid, **k: self.killed.append(pid)
        self.alive = set()
        live_sessions.pid_alive = lambda pid: _as_int(pid) in self.alive
        os.environ["TASK_STATION_REAP_WORKERS_ON_DONE"] = "on"

    def tearDown(self):
        (dg.REG_DIR, dg.REG, dg.SESSIONS_DIR, dg._kill_pid_group,
         live_sessions.pid_alive) = self.saved
        for k in ("TASK_STATION_HOME", "TASK_STATION_SESSIONS_DIR",
                  "TASK_STATION_REAP_WORKERS_ON_DONE"):
            os.environ.pop(k, None)
        store_mod.reset_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- fixtures --------------------------------------------------------------

    def _hub_file(self, sid, pid, status="idle", alive=True):
        """Write a `<PID>.json` for a hub session. `status` is the MODEL-TURN state
        (busy|idle) — deliberately independent of `alive`, which is the pid."""
        with open(os.path.join(self.sessdir, "%d.json" % pid), "w") as f:
            json.dump({"pid": pid, "sessionId": sid, "cwd": "/work",
                       "kind": "hub", "entrypoint": "cli", "status": status,
                       "startedAt": 1000, "updatedAt": 2000}, f)
        if alive:
            self.alive.add(pid)

    def _task_with_worker(self, worker_sid="wk-orphan", name=WNAME):
        t = ts.new_task("Has a worker", "summary")
        ts.save_task(t)
        ts.ensure_seqs()
        t = ts.load_task(t["id"])
        ts.register_worker_session(t, worker_sid, name=name, status="running")
        ts.save_task(t)
        return ts.load_task(t["id"])

    def _reg(self, seq, worker_sid="wk-orphan", spawner="dead-hub", name=WNAME,
             age=10_000):
        """One registry entry. `age` = seconds ago it was spawned (well past grace
        by default)."""
        entry = {"seq": seq, "session_id": worker_sid, "project": "projectname",
                 "label": None, "dir": "/work", "spawner": spawner,
                 "started_ts": ts._now() - age}
        if name is not None:
            entry["name"] = name
        with open(dg.REG, "w") as f:
            json.dump({"%s:projectname" % seq: entry}, f)

    def _agents(self, worker_sid="wk-orphan", name=WNAME, status="idle", pid=111):
        return FakeAdapter({worker_sid: {"sessionId": worker_sid, "pid": pid,
                                         "status": status, "name": name}})

    # -- the one case that SHOULD reap ----------------------------------------

    def test_reaps_a_worker_whose_hub_is_gone(self):
        t = self._task_with_worker()
        self._reg(t["seq"], spawner="dead-hub")
        self._hub_file("other-live-hub", 1000)        # someone IS alive → evidence exists
        reaped = ts.sweep_orphan_workers(current_sid="me", adapter=self._agents())
        self.assertEqual(reaped, [(t["seq"], "wk-orphan")])
        self.assertEqual(self.killed, [111])

    # -- rule 1: only task-station workers ------------------------------------

    def test_rule1_never_touches_a_foreign_agent(self):
        """A registry entry whose recorded name isn't task-station-shaped is not even
        a candidate — some other tool's agent is never ours to kill."""
        t = self._task_with_worker(name="some-other-tools-agent")
        self._reg(t["seq"], name="some-other-tools-agent")
        self._hub_file("other-live-hub", 1000)
        reaped = ts.sweep_orphan_workers(
            current_sid="me", adapter=self._agents(name="some-other-tools-agent"))
        self.assertEqual(reaped, [])
        self.assertEqual(self.killed, [])

    def test_rule1_entry_with_no_name_is_left_alone(self):
        t = self._task_with_worker()
        self._reg(t["seq"], name=None)                # nothing to identify it by
        self._hub_file("other-live-hub", 1000)
        self.assertEqual(
            ts.sweep_orphan_workers(current_sid="me", adapter=self._agents()), [])

    # -- rule 2: never our own workers ----------------------------------------

    def test_rule2_never_reaps_the_sweeping_sessions_own_workers(self):
        """The sweeping session is alive BY DEFINITION — and this early in
        SessionStart its own `<PID>.json` may not be written yet, so its absence from
        the live set must never be read as death."""
        t = self._task_with_worker()
        self._reg(t["seq"], spawner="me")             # I spawned this worker
        self._hub_file("other-live-hub", 1000)        # note: NO file for "me" yet
        reaped = ts.sweep_orphan_workers(current_sid="me", adapter=self._agents())
        self.assertEqual(reaped, [])
        self.assertEqual(self.killed, [])

    def test_rule2_never_reaps_the_current_session_itself(self):
        t = self._task_with_worker(worker_sid="me")
        self._reg(t["seq"], worker_sid="me", spawner="dead-hub")
        self._hub_file("other-live-hub", 1000)
        reaped = ts.sweep_orphan_workers(
            current_sid="me", adapter=self._agents(worker_sid="me"))
        self.assertEqual(reaped, [])

    # -- rule 3: dead means the PID is gone, NOT status: idle -----------------

    def test_rule3_idle_but_alive_hub_keeps_its_workers(self):
        """THE headline safety case. A hub sitting `idle` waiting for the user to type
        is ALIVE. Reading `idle` as dead would eat the workers of every hub anyone
        left open."""
        t = self._task_with_worker()
        self._reg(t["seq"], spawner="idle-hub")
        self._hub_file("idle-hub", 1000, status="idle", alive=True)
        reaped = ts.sweep_orphan_workers(current_sid="me", adapter=self._agents())
        self.assertEqual(reaped, [])
        self.assertEqual(self.killed, [])

    def test_rule3_busy_hub_keeps_its_workers(self):
        t = self._task_with_worker()
        self._reg(t["seq"], spawner="busy-hub")
        self._hub_file("busy-hub", 1000, status="busy", alive=True)
        self.assertEqual(
            ts.sweep_orphan_workers(current_sid="me", adapter=self._agents()), [])

    def test_rule3_an_idle_file_with_a_dead_pid_is_dead(self):
        """The converse: a crashed hub often leaves a STALE `idle` file behind. The
        pid decides, so this one IS reaped."""
        t = self._task_with_worker()
        self._reg(t["seq"], spawner="crashed-hub")
        self._hub_file("crashed-hub", 2000, status="idle", alive=False)  # stale file
        self._hub_file("other-live-hub", 1000)                          # evidence
        reaped = ts.sweep_orphan_workers(current_sid="me", adapter=self._agents())
        self.assertEqual(reaped, [(t["seq"], "wk-orphan")])

    # -- rule 4: absence of evidence is not death -----------------------------

    def test_rule4_unreadable_sessions_dir_reaps_nothing(self):
        t = self._task_with_worker()
        self._reg(t["seq"], spawner="dead-hub")
        os.environ["TASK_STATION_SESSIONS_DIR"] = os.path.join(self.tmp, "nope")
        reaped = ts.sweep_orphan_workers(current_sid="me", adapter=self._agents())
        self.assertEqual(reaped, [])
        self.assertEqual(self.killed, [])

    def test_rule4_empty_live_set_reaps_nothing(self):
        """An empty/mis-resolved sessions dir is evidence-free, not a dead world."""
        t = self._task_with_worker()
        self._reg(t["seq"], spawner="dead-hub")
        reaped = ts.sweep_orphan_workers(current_sid="me", adapter=self._agents())
        self.assertEqual(reaped, [])
        self.assertEqual(self.killed, [])

    def test_rule4_failing_agents_call_reaps_nothing(self):
        t = self._task_with_worker()
        self._reg(t["seq"], spawner="dead-hub")
        self._hub_file("other-live-hub", 1000)
        reaped = ts.sweep_orphan_workers(current_sid="me", adapter=BadAdapter())
        self.assertEqual(reaped, [])
        self.assertEqual(self.killed, [])

    def test_rule4_worker_without_a_recorded_spawner_is_left_alone(self):
        """No recorded provenance → we cannot know whose hub it was → unknown."""
        t = self._task_with_worker()
        self._reg(t["seq"], spawner=None)
        self._hub_file("other-live-hub", 1000)
        self.assertEqual(
            ts.sweep_orphan_workers(current_sid="me", adapter=self._agents()), [])

    # -- rule 5: grace period -------------------------------------------------

    def test_rule5_a_very_new_worker_is_spared(self):
        """A worker spawned seconds ago is skipped: its hub's session file may not be
        written yet, so the hub would look dead when it is merely young."""
        t = self._task_with_worker()
        self._reg(t["seq"], spawner="brand-new-hub", age=0)
        self._hub_file("other-live-hub", 1000)
        reaped = ts.sweep_orphan_workers(current_sid="me", adapter=self._agents())
        self.assertEqual(reaped, [])
        self.assertEqual(self.killed, [])

    def test_rule5_grace_is_a_module_level_constant(self):
        self.assertIsInstance(ts.ORPHAN_SWEEP_GRACE_SECS, int)
        self.assertGreater(ts.ORPHAN_SWEEP_GRACE_SECS, 0)

    def test_rule5_just_past_grace_is_reapable(self):
        t = self._task_with_worker()
        self._reg(t["seq"], spawner="dead-hub",
                  age=ts.ORPHAN_SWEEP_GRACE_SECS + 5)
        self._hub_file("other-live-hub", 1000)
        self.assertEqual(
            ts.sweep_orphan_workers(current_sid="me", adapter=self._agents()),
            [(t["seq"], "wk-orphan")])

    def test_rule5_an_entry_with_no_age_is_left_alone(self):
        t = self._task_with_worker()
        with open(dg.REG, "w") as f:
            json.dump({"7:projectname": {"seq": t["seq"], "session_id": "wk-orphan",
                                         "spawner": "dead-hub", "name": WNAME}}, f)
        self._hub_file("other-live-hub", 1000)
        self.assertEqual(
            ts.sweep_orphan_workers(current_sid="me", adapter=self._agents()), [])

    # -- rule 6: log every reap ----------------------------------------------

    def test_rule6_every_reap_is_logged(self):
        t = self._task_with_worker()
        self._reg(t["seq"], spawner="dead-hub")
        self._hub_file("other-live-hub", 1000)
        ts.sweep_orphan_workers(current_sid="me", adapter=self._agents())
        t2 = ts.load_task(t["id"])
        stops = [e for e in t2.get("ledger", []) if e.get("action") == "stop"]
        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[0]["worker"], "wk-orphan")
        self.assertEqual(stops[0]["actor"], "me")
        self.assertIn("orphan", (stops[0].get("detail") or "").lower())
        # …and the roster records that it is no longer running.
        self.assertEqual(t2["session_meta"]["wk-orphan"]["status"], "stopped")

    # -- never break SessionStart --------------------------------------------

    def test_sweep_never_raises_when_the_registry_is_corrupt(self):
        self._task_with_worker()
        with open(dg.REG, "w") as f:
            f.write("{not json")
        self._hub_file("other-live-hub", 1000)
        self.assertEqual(
            ts.sweep_orphan_workers(current_sid="me", adapter=self._agents()), [])

    def test_sweep_never_raises_when_the_delegate_module_is_missing(self):
        orig = ts._delegate_module
        try:
            ts._delegate_module = lambda: None
            self._hub_file("other-live-hub", 1000)
            self.assertEqual(ts.sweep_orphan_workers(current_sid="me"), [])
        finally:
            ts._delegate_module = orig

    def test_cmd_sweep_orphans_exits_quietly_with_nothing_to_do(self):
        """The hook path: no candidates, no output on stdout (which would corrupt the
        SessionStart JSON), no exception."""
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_sweep_orphans(_SweepArgs(session="me"))
        self.assertEqual(buf.getvalue(), "")


class _SweepArgs:
    def __init__(self, session=None):
        self.session = session


def _as_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return -1


if __name__ == "__main__":
    unittest.main()
