"""WS2 delegate: spawner capture + worker/child event write-back.

Three isolated concerns, mirroring the import + temp-home isolation of
tests/test_delegate_capture.py so it runs under both unittest (hub) and pytest (CI):

  * delegate._save_entry now records `spawner` (the hub CLAUDE_CODE_SESSION_ID) on
    the workers.json entry, preserving an existing one across a refresh when the
    env var is missing.
  * delegate.cmd_run fires WS1's `add-event` CLI (best-effort subprocess, like the
    add-cost write-back) so the /todo task feed hears a worker finished/failed.
  * task-station's close path (`_close_one` / `cmd_done`) mirrors a `spawned-from`
    child's closure to each parent's event feed via `add_event`.
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import types
import unittest

# delegate.py lives in lib/delegate/; import it directly (it inserts lib/ itself).
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "lib", "delegate"
))
import delegate as _delegate_mod  # noqa: E402

# task-station.py has a hyphen → load via importlib spec (test_delegate_capture pattern).
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)
import store  # noqa: E402
_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)

ENV = "CLAUDE_CODE_SESSION_ID"


# --------------------------------------------------------------- _save_entry ---

class SaveEntrySpawnerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._saved = (_delegate_mod.REG_DIR, _delegate_mod.REG)
        _delegate_mod.REG_DIR = self._tmp
        _delegate_mod.REG = os.path.join(self._tmp, "workers.json")
        self._env = os.environ.get(ENV)
        os.environ.pop(ENV, None)

    def tearDown(self):
        _delegate_mod.REG_DIR, _delegate_mod.REG = self._saved
        if self._env is None:
            os.environ.pop(ENV, None)
        else:
            os.environ[ENV] = self._env
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_records_spawner_from_env(self):
        os.environ[ENV] = "hub-sid"
        reg = {}
        _delegate_mod._save_entry(reg, "42:Projectname", "Projectname", 42, None, "/tmp/wt", "sid-1")
        self.assertEqual(reg["42:Projectname"]["spawner"], "hub-sid")
        # round-trips through workers.json
        self.assertEqual(_delegate_mod.load_reg()["42:Projectname"]["spawner"], "hub-sid")

    def test_spawner_absent_without_env(self):
        self.assertNotIn(ENV, os.environ)
        reg = {}
        _delegate_mod._save_entry(reg, "k", "Projectname", 42, None, "/tmp/wt", "sid-1")
        self.assertNotIn("spawner", reg["k"])   # key absent, not None/empty

    def test_spawner_preserved_on_refresh_without_env(self):
        # Pre-register with the env set (launch), then refresh without it (a resume
        # in a hub that no longer exports the var) — the spawner must survive.
        os.environ[ENV] = "hub-sid"
        reg = {}
        _delegate_mod._save_entry(reg, "k", "Projectname", 42, None, "/tmp/wt", "sid-1")
        os.environ.pop(ENV, None)
        _delegate_mod._save_entry(reg, "k", "Projectname", 42, None, "/tmp/wt", "sid-1", model="opus")
        self.assertEqual(reg["k"]["spawner"], "hub-sid")


# ----------------------------------------------------------- cmd_run add-event ---

class CmdRunWorkerEventTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._repo = tempfile.mkdtemp()
        self._saved_reg = (_delegate_mod.REG_DIR, _delegate_mod.REG)
        _delegate_mod.REG_DIR = self._tmp
        _delegate_mod.REG = os.path.join(self._tmp, "workers.json")
        # Capture every post-run write-back subprocess call.
        self.calls = []
        self._saved = {
            "run": _delegate_mod.subprocess.run,
            "resolve": _delegate_mod._resolve_dir_from_args,
            "worker": _delegate_mod.run_worker,
            "notify": _delegate_mod.notify_event,
        }

        def _fake_run(cmd, **kw):
            self.calls.append(cmd)
            return _delegate_mod.subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        _delegate_mod.subprocess.run = _fake_run
        _delegate_mod._resolve_dir_from_args = lambda a: self._repo
        _delegate_mod.notify_event = lambda *a, **k: None

    def tearDown(self):
        _delegate_mod.subprocess.run = self._saved["run"]
        _delegate_mod._resolve_dir_from_args = self._saved["resolve"]
        _delegate_mod.run_worker = self._saved["worker"]
        _delegate_mod.notify_event = self._saved["notify"]
        _delegate_mod.REG_DIR, _delegate_mod.REG = self._saved_reg
        shutil.rmtree(self._tmp, ignore_errors=True)
        shutil.rmtree(self._repo, ignore_errors=True)

    def _args(self, **kw):
        # harness="codex" pins the NON-bg (legacy `-p` streaming) cmd_run path, which
        # these tests exercise via a monkeypatched run_worker. The claude harness now
        # spawns via run_worker_bg (see tests/test_delegate_bg_cmdrun.py). (#463)
        d = dict(seq="42", label=None, worktree=None, branch=None, base=None,
                 fresh=False, solo=False, task="do it", timeout=None,
                 model="sonnet", repo=self._repo, project=None, harness="codex")
        d.update(kw)
        return types.SimpleNamespace(**d)

    def _find_add_event(self):
        for c in self.calls:
            if "add-event" in c:
                return c
        return None

    def _flag(self, cmd, name):
        return cmd[cmd.index(name) + 1]

    def test_finished_run_posts_worker_event(self):
        result = "WS1 events feed authored " + ("x" * 300)   # long → snippet truncated
        blob = json.dumps({"result": result, "session_id": "worker-sid",
                           "total_cost_usd": 0.05, "model": "claude-opus-4-8",
                           "usage": {"input_tokens": 1, "output_tokens": 2}})
        _delegate_mod.run_worker = lambda *a, **k: (0, blob, "", False)

        _delegate_mod.cmd_run(self._args())

        ev = self._find_add_event()
        self.assertIsNotNone(ev, "expected an add-event write-back call: %r" % self.calls)
        self.assertEqual(ev[:3], ["python3", _delegate_mod.TASK_STATION_PY, "add-event"])
        self.assertEqual(self._flag(ev, "--task"), "42")
        self.assertEqual(self._flag(ev, "--kind"), "worker")
        self.assertEqual(self._flag(ev, "--session"), "worker-sid")
        text = self._flag(ev, "--text")
        project = os.path.basename(self._repo)
        self.assertTrue(text.startswith("worker finished: %s" % project),
                        "unexpected event text: %r" % text)
        self.assertLessEqual(len(text), 200)
        self.assertNotIn("\n", text)

    def test_finished_run_posts_event_even_without_cost(self):
        # An older CLI reports neither cost nor model nor usage — the worker event
        # still fires (a finish is always news), unlike the add-cost write-back.
        blob = json.dumps({"result": "ok", "session_id": "worker-sid"})
        _delegate_mod.run_worker = lambda *a, **k: (0, blob, "", False)

        _delegate_mod.cmd_run(self._args())

        ev = self._find_add_event()
        self.assertIsNotNone(ev)
        self.assertTrue(self._flag(ev, "--text").startswith("worker finished:"))

    def test_no_seq_posts_no_event(self):
        # Ad-hoc read-only run (no task) → nothing to attribute to → no add-event.
        blob = json.dumps({"result": "ok", "session_id": "worker-sid"})
        _delegate_mod.run_worker = lambda *a, **k: (0, blob, "", False)

        _delegate_mod.cmd_run(self._args(seq=None, solo=True))
        self.assertIsNone(self._find_add_event())

    def test_failed_run_posts_failure_event(self):
        # Non-zero exit with no stdout → cmd_run raises SystemExit, but a "failed"
        # worker event is fired first.
        _delegate_mod.run_worker = lambda *a, **k: (1, None, "boom", False)

        with self.assertRaises(SystemExit):
            _delegate_mod.cmd_run(self._args())

        ev = self._find_add_event()
        self.assertIsNotNone(ev)
        self.assertTrue(self._flag(ev, "--text").startswith("worker failed:"))


# ------------------------------------------------------- child-close mirror ---

def _repoint(tmp):
    os.environ["TASK_STATION_HOME"] = tmp
    ts.DATA = tmp
    ts.STORE = os.path.join(tmp, "store")
    ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
    ts.LINKS_DIR = os.path.join(ts.STORE, "links")
    ts.DELEGATE_REGISTRY = os.path.join(tmp, "workers.json")
    store.reset_cache()


class ChildCloseMirrorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _repoint(self.tmp)

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _parent_and_child(self, child_seq=365, parent_seq=363):
        parent = ts.new_task("checkpoint best practices", "")
        parent["seq"] = parent_seq
        ts.save_task(parent)
        child = ts.new_task("child work", "")
        child["seq"] = child_seq
        child["related"] = [{"id": parent["id"], "seq": parent_seq,
                             "kind": "spawned-from", "ts": ts._now()}]
        ts.save_task(child)
        return parent, child

    def test_close_by_ref_mirrors_child_close_to_parent(self):
        parent, child = self._parent_and_child()
        ts._close_one(child["id"], "sid-a")
        got = ts.load_task(parent["id"])
        ev = got["events"][-1]
        self.assertEqual(ev["kind"], "child")
        self.assertEqual(ev["text"], "child #365 closed: child work")
        self.assertEqual(ev["sid"], "sid-a")

    def test_done_session_path_mirrors_child_close(self):
        parent, child = self._parent_and_child(child_seq=366)
        ts.set_link("sess-x", child["id"])
        ts.cmd_done(types.SimpleNamespace(task=None, session="sess-x"))
        got = ts.load_task(parent["id"])
        self.assertEqual(got["events"][-1]["text"], "child #366 closed: child work")
        self.assertTrue(ts.is_closed(ts.load_task(child["id"])))

    def test_bare_close_touches_no_parent(self):
        # A task with no related edges closes without mirroring a child event to anyone.
        lone = ts.new_task("lone", "")
        lone["seq"] = 400
        ts.save_task(lone)
        ts._close_one(lone["id"], "sid-a")
        got = ts.load_task(lone["id"])
        self.assertTrue(ts.is_closed(got))
        self.assertNotIn("child", [ev["kind"] for ev in got.get("events", [])])

    def test_non_spawned_related_edge_is_ignored(self):
        # A plain "related" edge (not spawned-from) must NOT trigger a child-close event.
        parent, child = self._parent_and_child(child_seq=370)
        child["related"][0]["kind"] = "related"
        ts.save_task(child)
        ts._close_one(child["id"], "sid-a")
        got = ts.load_task(parent["id"])
        self.assertNotIn("events", got)


if __name__ == "__main__":
    unittest.main()
