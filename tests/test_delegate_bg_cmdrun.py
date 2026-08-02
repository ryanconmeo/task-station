"""Phase 3.3/3.4 (#463): cmd_run's --bg lifecycle end-to-end with a fake `claude`
PATH shim (spawns → prints id + writes an `idle` agents row) and a recorder over
delegate.subprocess.run that captures the python3 write-backs (add-project / status
active / add-event / add-ledger spawn+finish / register-worker-session). Asserts the
registry is written post-launch with bg=True + the printed id, and the ledger/roster
posts fire with the acting hub session."""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "lib", "delegate"
))
import delegate as dg  # noqa: E402

BG_ID = "11111111-2222-3333-4444-555555555555"

# Fake `claude`: on --bg print the backgrounded line AND write an `idle` agents row
# (so the first poll sees a completed turn → ok); on `agents` serve that file.
SHIM = r'''#!/usr/bin/env bash
case "$1" in
  --bg)
    echo "backgrounded · 11111111-2222-3333-4444-555555555555 · wk-test"
    echo '[{"sessionId":"11111111-2222-3333-4444-555555555555","status":"idle","pid":4242,"name":"wk-test","cwd":"'"$FAKE_CLAUDE_DIR"'","kind":"background"}]' > "$FAKE_CLAUDE_DIR/agents.json"
    ;;
  agents) cat "$FAKE_CLAUDE_DIR/agents.json" 2>/dev/null || echo "[]";;
  *) exit 64;;
esac
'''


def _init_repo():
    d = tempfile.mkdtemp()
    subprocess.run(["git", "init", "-q", d], check=True)
    subprocess.run(["git", "-C", d, "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", d, "config", "user.name", "t"], check=True)
    with open(os.path.join(d, "seed.txt"), "w") as f:
        f.write("seed\n")
    subprocess.run(["git", "-C", d, "add", "-A"], check=True)
    subprocess.run(["git", "-C", d, "commit", "-qm", "seed"], check=True)
    return d


class BgCmdRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-bgrun-")
        # fake claude on PATH
        bindir = os.path.join(self.tmp, "bin"); os.makedirs(bindir)
        shim = os.path.join(bindir, "claude")
        with open(shim, "w") as f:
            f.write(SHIM)
        os.chmod(shim, 0o755)
        self._path = os.environ.get("PATH", "")
        os.environ["PATH"] = bindir + os.pathsep + self._path
        os.environ["FAKE_CLAUDE_DIR"] = self.tmp
        self._hub = os.environ.get("CLAUDE_CODE_SESSION_ID")
        os.environ["CLAUDE_CODE_SESSION_ID"] = "hub-abc"
        # A worker transcript so _transcript_usage_summary sources a reported figure.
        self._proj_root = dg.PROJECTS_ROOT
        dg.PROJECTS_ROOT = os.path.join(self.tmp, "projects")
        bucket = os.path.join(dg.PROJECTS_ROOT, "-work-repo"); os.makedirs(bucket)
        with open(os.path.join(bucket, BG_ID + ".jsonl"), "w") as f:
            f.write(json.dumps({"type": "assistant", "timestamp": "2026-07-22T00:00:00Z",
                                "message": {"model": "claude-sonnet-5",
                                            "usage": {"input_tokens": 100,
                                                      "output_tokens": 50}}}) + "\n")
        # registry redirect
        self._reg = (dg.REG_DIR, dg.REG)
        dg.REG_DIR = self.tmp
        dg.REG = os.path.join(self.tmp, "workers.json")
        # repo + worktree
        self.repo = _init_repo()
        self.wt = _init_repo()
        # monkeypatches
        self._saved = {
            "resolve": dg._resolve_dir_from_args,
            "wt": dg.resolve_worktree,
            "attached": dg._attached_seq,
            "notify": dg.notify_event,
            "sleep": dg.time.sleep,
            "run": dg.subprocess.run,
        }
        dg._resolve_dir_from_args = lambda a: self.repo
        dg.resolve_worktree = lambda *a, **k: self.wt
        dg._attached_seq = lambda: None
        dg.notify_event = lambda *a, **k: None
        dg.time.sleep = lambda *a, **k: None
        self.calls = []
        real_run = self._saved["run"]

        def rec(cmd, *a, **k):
            if isinstance(cmd, list) and cmd[:1] == ["python3"]:
                self.calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            return real_run(cmd, *a, **k)   # claude shim + git go through

        dg.subprocess.run = rec

    def tearDown(self):
        os.environ["PATH"] = self._path
        os.environ.pop("FAKE_CLAUDE_DIR", None)
        if self._hub is None:
            os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        else:
            os.environ["CLAUDE_CODE_SESSION_ID"] = self._hub
        dg.REG_DIR, dg.REG = self._reg
        dg._resolve_dir_from_args = self._saved["resolve"]
        dg.resolve_worktree = self._saved["wt"]
        dg._attached_seq = self._saved["attached"]
        dg.notify_event = self._saved["notify"]
        dg.time.sleep = self._saved["sleep"]
        dg.subprocess.run = self._saved["run"]
        dg.PROJECTS_ROOT = self._proj_root
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.repo, ignore_errors=True)
        shutil.rmtree(self.wt, ignore_errors=True)

    def _args(self, **kw):
        d = dict(seq="9", label=None, worktree="9-fix", branch=None, base=None,
                 fresh=False, solo=False, task="do it", timeout=None,
                 model="sonnet", repo=self.repo, project=None, harness="claude")
        d.update(kw)
        return types.SimpleNamespace(**d)

    def _flag(self, cmd, name):
        return cmd[cmd.index(name) + 1] if name in cmd else None

    def _by_sub(self, sub):
        return [c for c in self.calls if sub in c]

    def _run(self, **kw):
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            dg.cmd_run(self._args(**kw))

    def test_registers_bg_worker_with_launch_id(self):
        self._run()
        reg = dg.load_reg()
        entry = next(iter(reg.values()))
        self.assertEqual(entry["session_id"], BG_ID)
        self.assertTrue(entry["bg"])
        self.assertEqual(entry["harness"], "claude")
        self.assertEqual(entry["agent_state"], "idle")

    def test_posts_spawn_and_finish_ledger(self):
        self._run()
        ledgers = self._by_sub("add-ledger")
        actions = [self._flag(c, "--action") for c in ledgers]
        self.assertIn("spawn", actions)
        self.assertIn("finish", actions)
        for c in ledgers:                       # every ledger post names the hub + worker
            self.assertEqual(self._flag(c, "--session"), "hub-abc")
            self.assertEqual(self._flag(c, "--worker"), BG_ID)

    def test_rosters_worker_running_then_ok(self):
        self._run()
        regs = self._by_sub("register-worker-session")
        statuses = [self._flag(c, "--status") for c in regs]
        self.assertIn("running", statuses)
        self.assertIn("ok", statuses)
        self.assertTrue(all(self._flag(c, "--session") == BG_ID for c in regs))

    def test_keeps_add_project_and_status_active_and_event(self):
        self._run()
        self.assertTrue(self._by_sub("add-project"))
        active = [c for c in self.calls if "status" in c and "active" in c]
        self.assertTrue(active)
        self.assertTrue(self._by_sub("add-event"))

    def test_ok_posts_real_add_cost_from_transcript(self):
        self._run()
        costs = self._by_sub("add-cost")
        self.assertTrue(costs)
        c = costs[0]
        self.assertEqual(self._flag(c, "--category"), "real")
        self.assertEqual(self._flag(c, "--session"), BG_ID)
        self.assertEqual(self._flag(c, "--model"), "claude-sonnet-5")
        self.assertIsNotNone(self._flag(c, "--usage-json"))

    def test_crash_when_worker_goes_gone(self):
        # A shim that prints the id but never writes an agents row → the poll sees
        # 'gone' → crash → SystemExit + a crash ledger post (no `idle` reached).
        shim = os.path.join(self.tmp, "bin", "claude")
        with open(shim, "w") as f:
            f.write("#!/usr/bin/env bash\n"
                    "case \"$1\" in\n"
                    "  --bg) echo 'backgrounded · %s · wk-test';;\n"
                    "  agents) echo '[]';;\n"
                    "  *) exit 64;;\n"
                    "esac\n" % BG_ID)
        os.chmod(shim, 0o755)
        with self.assertRaises(SystemExit):
            self._run()
        actions = [self._flag(c, "--action") for c in self._by_sub("add-ledger")]
        self.assertIn("crash", actions)
        self.assertNotIn("finish", actions)
        # crashed tokens are captured in the WASTED bucket, not skipped (#4).
        costs = self._by_sub("add-cost")
        self.assertTrue(costs)
        self.assertEqual(self._flag(costs[0], "--category"), "wasted")


if __name__ == "__main__":
    unittest.main()
