"""Phase 3.2/3.3 (#463): run_worker_bg poll loop + wall-clock watchdog, and
_classify_exit_bg (status-only — there is NO transcript `result` record under
--bg, per the spike). Uses a scripted fake adapter; no real `claude`."""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "lib", "delegate"
))
import delegate as dg  # noqa: E402


class FakeAdapter:
    """Scripted agents-status sequence. Once one state remains it sticks (a
    never-ending 'busy' for the watchdog test)."""
    def __init__(self, states):
        self.states = list(states)

    def spawn_worker(self, brief, worktree, **kw):
        return "sid-bg-1"

    def worker_status(self, wid):
        s = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return {"state": s, "pid": 999, "raw": {"status": s}}


class RunWorkerBgTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-bglife-")
        self._kill = dg._kill_pid_group

    def tearDown(self):
        dg._kill_pid_group = self._kill
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, ad, **kw):
        with redirect_stderr(io.StringIO()):
            return dg.run_worker_bg(ad, self.tmp, "brief", **kw)

    def test_poll_until_idle_is_ok(self):
        ad = FakeAdapter(["busy", "busy", "idle"])
        sid, state, to = self._run(ad, name="wk", key=None, poll_secs=0.01)
        self.assertEqual((sid, state, to), ("sid-bg-1", "idle", False))

    def test_poll_until_gone(self):
        ad = FakeAdapter(["busy", "gone"])
        sid, state, to = self._run(ad, name="wk", key=None, poll_secs=0.01)
        self.assertEqual((sid, state, to), ("sid-bg-1", "gone", False))

    def test_watchdog_times_out_and_kills(self):
        ad = FakeAdapter(["busy"])
        killed = []
        dg._kill_pid_group = lambda pid, **k: killed.append(pid)
        sid, state, to = self._run(ad, name="wk", timeout=0.05, poll_secs=0.01)
        self.assertTrue(to)
        self.assertEqual(killed, [999])

    def test_heartbeat_written_to_registry(self):
        # Pre-seed reg[key]; the poll loop must write pid/phase heartbeats.
        saved = (dg.REG_DIR, dg.REG)
        dg.REG_DIR = self.tmp
        dg.REG = os.path.join(self.tmp, "workers.json")
        try:
            with open(dg.REG, "w") as f:
                json.dump({"k1": {"project": "p", "seq": 1}}, f)
            ad = FakeAdapter(["busy", "idle"])
            self._run(ad, name="wk", key="k1", poll_secs=0.01)
            entry = dg.load_reg()["k1"]
            self.assertEqual(entry["pid"], 999)
            self.assertIn("agent status", entry["phase"])
        finally:
            dg.REG_DIR, dg.REG = saved


class ClassifyExitBgTest(unittest.TestCase):
    def test_idle_is_ok(self):
        self.assertEqual(dg._classify_exit_bg("idle", False), ("ok", False))

    def test_gone_before_idle_is_crash(self):
        self.assertEqual(dg._classify_exit_bg("gone", False), ("crash", True))

    def test_timeout_wins_over_state(self):
        self.assertEqual(dg._classify_exit_bg("idle", True), ("timeout", True))
        self.assertEqual(dg._classify_exit_bg("busy", True), ("timeout", True))

    def test_stalled_keeps_its_label(self):
        self.assertEqual(dg._classify_exit_bg("stalled", False), ("stalled", True))
        self.assertEqual(dg._classify_exit_bg("needs-input", False),
                         ("stalled", True))

    def test_unknown_error_status_is_crash(self):
        self.assertEqual(dg._classify_exit_bg("error", False), ("crash", True))


if __name__ == "__main__":
    unittest.main()
