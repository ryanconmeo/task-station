"""Task #464 part 2 — bg-aware resume one-liners.

A bare `claude --resume <sid>` is REFUSED by Claude Code when <sid> is a live
`--bg` background agent. bg_aware_resume() ATTACHES the exact live session (`claude
agents`) for such sessions — never a `--fork-session` copy — and keeps the bare
`--resume` otherwise; every resume call site routes through it. The live snapshot is
stubbed via ts._LIVE_BG_INDEX so nothing shells out.
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

import store  # noqa: E402

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


def _row(sid, pid=None, kind="background"):
    return {"sessionId": sid, "pid": pid, "status": "idle", "kind": kind}


class HelperTest(unittest.TestCase):
    def tearDown(self):
        ts._LIVE_BG_INDEX = None

    def test_not_live_is_bare_resume(self):
        ts._LIVE_BG_INDEX = {}
        self.assertEqual(ts.bg_aware_resume("sid1", "/d"), "cd /d && claude --resume sid1")

    def test_live_bg_attaches_primary(self):
        # Live bg → ATTACH the exact session; NO fork, NO cd (attach is global).
        ts._LIVE_BG_INDEX = {"sid1": _row("sid1", pid=9)}
        self.assertEqual(ts.bg_aware_resume("sid1", "/d"), "claude agents")

    def test_live_bg_never_forks(self):
        ts._LIVE_BG_INDEX = {"sid1": _row("sid1", pid=9)}
        self.assertNotIn("--fork-session", ts.bg_aware_resume("sid1", "/d"))

    def test_live_but_no_pid_is_bare(self):
        ts._LIVE_BG_INDEX = {"sid1": _row("sid1", pid=None)}
        self.assertEqual(ts.bg_aware_resume("sid1", "/d"), "cd /d && claude --resume sid1")

    def test_no_cwd_omits_cd(self):
        ts._LIVE_BG_INDEX = {}
        self.assertEqual(ts.bg_aware_resume("sid1"), "claude --resume sid1")

    def test_falsy_sid_is_none(self):
        ts._LIVE_BG_INDEX = {}
        self.assertIsNone(ts.bg_aware_resume(None, "/d"))

    def test_prefix_match_short_id_live(self):
        ts._LIVE_BG_INDEX = {"sid1-full-uuid": _row("sid1-full-uuid", pid=9)}
        self.assertEqual(ts.bg_aware_resume("sid1", "/d"), "claude agents")

    def test_hint_attach_primary_fork_as_aside(self):
        ts._LIVE_BG_INDEX = {"sid1": _row("sid1", pid=9)}
        h = ts.bg_resume_hint("sid1", "/d")
        self.assertIn("claude agents", h)                    # attach = the primary path
        self.assertIn("--fork-session", h)                   # fork only mentioned as an aside

    def test_hint_none_when_not_live(self):
        ts._LIVE_BG_INDEX = {}
        self.assertIsNone(ts.bg_resume_hint("sid1", "/d"))

    def test_interactive_row_with_pid_is_bare_resume(self):
        # Interactive hub sessions carry a pid too — kind is what distinguishes them.
        ts._LIVE_BG_INDEX = {"sid1": _row("sid1", pid=9, kind="interactive")}
        self.assertEqual(ts.bg_aware_resume("sid1", "/d"), "cd /d && claude --resume sid1")
        self.assertFalse(ts._is_live_bg("sid1"))

    def test_background_row_with_pid_still_attaches(self):
        ts._LIVE_BG_INDEX = {"sid1": _row("sid1", pid=9, kind="background")}
        self.assertEqual(ts.bg_aware_resume("sid1", "/d"), "claude agents")
        self.assertTrue(ts._is_live_bg("sid1"))

    def test_index_snapshot_is_empty_on_no_delegate(self):
        # _delegate_module → None means no `claude` query; degrade to {} (bare resume).
        # Clear the suppression env so we exercise the real delegate branch, not the gate.
        ts._LIVE_BG_INDEX = None
        orig = ts._delegate_module
        had = os.environ.pop("TASK_STATION_NO_AGENT_QUERY", None)
        ts._delegate_module = lambda: None
        try:
            self.assertEqual(ts._live_bg_index(), {})
            self.assertFalse(ts._is_live_bg("anything"))
        finally:
            ts._delegate_module = orig
            ts._LIVE_BG_INDEX = None
            if had is not None:
                os.environ["TASK_STATION_NO_AGENT_QUERY"] = had


class CallSiteTest(unittest.TestCase):
    """A representative call site (worker_targets) must reflect the fork when its
    worker sid is a live bg agent — proving the site routes through the helper."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.PROJECTS_ROOT = os.path.join(self.tmp, "projects")
        self.reg = os.path.join(self.tmp, "workers.json")
        ts.DELEGATE_REGISTRY = self.reg
        store.reset_cache()

    def tearDown(self):
        ts._LIVE_BG_INDEX = None
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self):
        t = ts.new_task("Worky", "x")
        ts.save_task(t)
        ts.ensure_seqs()
        t = ts.load_task(t["id"])
        t["projects"] = ["proj"]
        ts.save_task(t)
        with open(self.reg, "w") as f:
            json.dump({"%s:proj" % t["seq"]:
                       {"seq": t["seq"], "project": "proj", "dir": "/d",
                        "session_id": "wsid"}}, f)
        return ts.load_task(t["id"])

    def test_worker_targets_attaches_live_bg(self):
        t = self._seed()
        ts._LIVE_BG_INDEX = {"wsid": _row("wsid", pid=7)}
        cmd = ts.worker_targets(t)[0]["command"]
        self.assertEqual(cmd, "claude agents")

    def test_worker_targets_bare_when_not_live(self):
        t = self._seed()
        ts._LIVE_BG_INDEX = {}
        cmd = ts.worker_targets(t)[0]["command"]
        self.assertEqual(cmd, "cd /d && claude --resume wsid")


if __name__ == "__main__":
    unittest.main()
