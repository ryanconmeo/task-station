# tests/test_session_roster.py
import importlib.util, os, sys, tempfile, unittest

def _load_engine():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    lib = os.path.join(root, "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    spec = importlib.util.spec_from_file_location(
        "ts_engine", os.path.join(root, "lib", "task-station.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

class OrdinalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-roster-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        self.ts = _load_engine()

    def test_creating_session_gets_zero(self):
        t = self.ts.new_task("t", "")
        t["seq"] = 9
        self.ts.touch(t, session="sid-A")
        self.assertEqual(self.ts.hub_ordinal(t, "sid-A"), 0)
        self.assertEqual(self.ts.ordinal_label(t, "sid-A"), "9-0")

    def test_ordinals_monotonic_never_reused(self):
        t = self.ts.new_task("t", ""); t["seq"] = 9
        self.ts.touch(t, session="sid-A")
        self.ts.touch(t, session="sid-B")
        self.assertEqual(self.ts.hub_ordinal(t, "sid-B"), 1)
        # detach B, attach C: C gets 2, not B's freed 1
        del t["session_meta"]["sid-B"]
        self.ts.touch(t, session="sid-C")
        self.assertEqual(self.ts.hub_ordinal(t, "sid-C"), 2)

    def test_lazy_backfill_oldest_is_zero(self):
        # pre-ordinal task shape: hubs with ts but no ordinal
        t = self.ts.new_task("t", ""); t["seq"] = 9
        t["sessions"] = ["old-1", "old-2"]
        t["session_meta"] = {"old-2": {"cwd": "/x", "ts": 200, "role": "hub"},
                             "old-1": {"cwd": "/x", "ts": 100, "role": "hub"}}
        changed = self.ts.ensure_ordinals(t)
        self.assertTrue(changed)
        self.assertEqual(self.ts.hub_ordinal(t, "old-1"), 0)   # oldest = creator = -0
        self.assertEqual(self.ts.hub_ordinal(t, "old-2"), 1)
        self.assertFalse(self.ts.ensure_ordinals(t))            # idempotent

    def test_worker_entries_never_get_ordinals(self):
        t = self.ts.new_task("t", ""); t["seq"] = 9
        self.ts.register_worker_session(t, "wk-sid", name="task-station-9-projectname",
                                        model="sonnet")
        self.assertIsNone(self.ts.hub_ordinal(t, "wk-sid"))
        m = t["session_meta"]["wk-sid"]
        self.assertEqual(m["role"], "worker")
        self.assertEqual(m["name"], "task-station-9-projectname")
        self.assertFalse(self.ts.ensure_ordinals(t))            # backfill skips workers

    def test_whoami_porcelain_seq_ordinal_kind(self):
        # create + attach through the real CLI surface
        t = self.ts.new_task("t", ""); t["seq"] = 9
        self.ts.create_with_seq(t)          # persists with a real seq
        self.ts.touch(t, session="sid-A"); self.ts.save_task(t)
        self.ts.set_link("sid-A", t["id"])
        import io, contextlib, types
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.ts.cmd_whoami(types.SimpleNamespace(
                session="sid-A", porcelain=True, statusline=False, width=0))
        seq, ordinal, kind = buf.getvalue().strip().split("\t")
        self.assertEqual(ordinal, "%s-0" % t["seq"])
        self.assertEqual(kind, "hub")

    def test_register_worker_session_cli(self):
        # delegate's roster write-through (#463): the register-worker-session CLI
        # rosters a worker on the task record with name/model/harness/status.
        import types
        t = self.ts.new_task("t", ""); t["seq"] = 9
        self.ts.create_with_seq(t)
        self.ts.cmd_register_worker(types.SimpleNamespace(
            task=str(t["seq"]), session="wk-9", name="task-station-9-projectname",
            model="sonnet", harness="claude", status="ok"))
        reloaded = self.ts.load_task(t["id"])
        m = reloaded["session_meta"]["wk-9"]
        self.assertEqual(m["role"], "worker")
        self.assertEqual(m["name"], "task-station-9-projectname")
        self.assertEqual(m["model"], "sonnet")
        self.assertEqual(m["status"], "ok")
        self.assertIsNone(self.ts.hub_ordinal(reloaded, "wk-9"))   # workers get no ordinal

if __name__ == "__main__":
    unittest.main()
