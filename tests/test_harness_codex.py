"""Phase 6 (#463): real CodexAdapter — a detached `codex exec --json` fallback with
NO Agent View (caps False). codex is NOT installed on the authoring machine, so the
NDJSON event/field names are UNVERIFIED and pinned here by SHAPE via a fake `codex`
PATH shim; freeze them from a live `codex exec --json` capture before relying on the
live path. Covers the event→result mapping and a full `cmd_run --harness codex`
round-trip (register + roster + ledger + add-cost unpriced), asserting NO `claude`
call is made."""
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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib", "delegate"))
import delegate as dg  # noqa: E402
import harness         # noqa: E402

# Fake `codex`: `codex exec …` emits a tiny NDJSON stream (session id, final text,
# token usage). SHAPE-pinned, names UNVERIFIED.
CODEX_SHIM = r'''#!/usr/bin/env bash
case "$1" in
  exec)
    echo '{"type":"session.created","session_id":"codex-sid-1"}'
    echo '{"type":"item.completed","text":"codex did the thing"}'
    echo '{"type":"token_count","usage":{"input_tokens":50,"output_tokens":20}}'
    ;;
  *) exit 64;;
esac
'''


class CodexResultMappingTest(unittest.TestCase):
    def test_maps_events_to_result_blob(self):
        events = [
            {"type": "session.created", "session_id": "codex-sid-1"},
            {"type": "item.completed", "text": "final answer"},
            {"type": "token_count", "usage": {"input_tokens": 100,
                                              "output_tokens": 40,
                                              "cached_input_tokens": 7}},
        ]
        blob = harness.CodexAdapter().result_from_events(events)
        self.assertEqual(blob["session_id"], "codex-sid-1")
        self.assertEqual(blob["result"], "final answer")
        self.assertIsNone(blob["total_cost_usd"])          # unpriced — no codex rates
        self.assertEqual(blob["usage"]["input_tokens"], 100)
        self.assertEqual(blob["usage"]["output_tokens"], 40)
        self.assertEqual(blob["usage"]["cache_read_input_tokens"], 7)

    def test_no_usable_events_is_none(self):
        self.assertIsNone(harness.CodexAdapter().result_from_events([]))
        self.assertIsNone(harness.CodexAdapter().result_from_events(
            [{"type": "noise"}]))

    def test_parse_result_reads_the_blob(self):
        # The mapped blob round-trips through the EXISTING _parse_result unchanged.
        blob = harness.CodexAdapter().result_from_events(
            [{"session_id": "s"}, {"text": "hi"},
             {"usage": {"input_tokens": 3, "output_tokens": 2}}])
        rt, sid, cost, model, usage = dg._parse_result(json.dumps(blob))
        self.assertEqual(rt, "hi")
        self.assertEqual(sid, "s")
        self.assertIsNone(cost)                            # unpriced
        self.assertEqual(usage, {"in": 3, "out": 2, "cache_read": 0,
                                 "cache_creation": 0})


class CodexCmdRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-codex-")
        bindir = os.path.join(self.tmp, "bin"); os.makedirs(bindir)
        with open(os.path.join(bindir, "codex"), "w") as f:
            f.write(CODEX_SHIM)
        os.chmod(os.path.join(bindir, "codex"), 0o755)
        self._path = os.environ.get("PATH", "")
        os.environ["PATH"] = bindir + os.pathsep + self._path
        self._hub = os.environ.get("CLAUDE_CODE_SESSION_ID")
        os.environ["CLAUDE_CODE_SESSION_ID"] = "hub-c"
        self._reg = (dg.REG_DIR, dg.REG)
        dg.REG_DIR = self.tmp
        dg.REG = os.path.join(self.tmp, "workers.json")
        self.repo = _init_repo()
        self._saved = {"resolve": dg._resolve_dir_from_args,
                       "attached": dg._attached_seq,
                       "notify": dg.notify_event, "run": dg.subprocess.run}
        dg._resolve_dir_from_args = lambda a: self.repo
        dg._attached_seq = lambda: None
        dg.notify_event = lambda *a, **k: None
        self.calls = []
        real = self._saved["run"]

        def rec(cmd, *a, **k):
            if isinstance(cmd, list) and cmd[:1] == ["python3"]:
                self.calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if isinstance(cmd, list) and cmd[:1] == ["claude"]:
                raise AssertionError("codex path must NOT call claude: %r" % cmd)
            return real(cmd, *a, **k)

        dg.subprocess.run = rec

    def tearDown(self):
        os.environ["PATH"] = self._path
        if self._hub is None:
            os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        else:
            os.environ["CLAUDE_CODE_SESSION_ID"] = self._hub
        dg.REG_DIR, dg.REG = self._reg
        dg._resolve_dir_from_args = self._saved["resolve"]
        dg._attached_seq = self._saved["attached"]
        dg.notify_event = self._saved["notify"]
        dg.subprocess.run = self._saved["run"]
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.repo, ignore_errors=True)

    def _args(self, **kw):
        d = dict(seq="5", label=None, worktree=None, branch=None, base=None,
                 fresh=False, solo=True, task="do it", timeout=None,
                 model="gpt-5-codex", repo=self.repo, project=None, harness="codex")
        d.update(kw)
        return types.SimpleNamespace(**d)

    def _flag(self, cmd, name):
        return cmd[cmd.index(name) + 1] if name in cmd else None

    def _by_sub(self, sub):
        return [c for c in self.calls if sub in c]

    def test_codex_round_trips_without_claude(self):
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            dg.cmd_run(self._args())
        # registry entry marked codex (no bg), tracking the codex-reported sid
        entry = next(iter(dg.load_reg().values()))
        self.assertEqual(entry["harness"], "codex")
        self.assertNotIn("bg", entry)                # legacy detached path, not --bg
        self.assertEqual(entry["session_id"], "codex-sid-1")
        # ledger spawn + finish, rostered, add-cost fired (unpriced → usd 0)
        actions = [self._flag(c, "--action") for c in self._by_sub("add-ledger")]
        self.assertIn("spawn", actions)
        self.assertIn("finish", actions)
        regs = self._by_sub("register-worker-session")
        self.assertTrue(any(self._flag(c, "--status") == "ok" for c in regs))
        cost = self._by_sub("add-cost")
        self.assertTrue(cost)
        self.assertEqual(self._flag(cost[0], "--usd"), "0")   # unpriced (no codex rates)
        self.assertEqual(self._flag(cost[0], "--session"), "codex-sid-1")


def _init_repo():
    d = tempfile.mkdtemp()
    subprocess.run(["git", "init", "-q", d], check=True)
    subprocess.run(["git", "-C", d, "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", d, "config", "user.name", "t"], check=True)
    with open(os.path.join(d, "s.txt"), "w") as f:
        f.write("s\n")
    subprocess.run(["git", "-C", d, "add", "-A"], check=True)
    subprocess.run(["git", "-C", d, "commit", "-qm", "seed"], check=True)
    return d


if __name__ == "__main__":
    unittest.main()
