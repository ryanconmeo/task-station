# tests/test_worker_ledger.py  (same _load_engine + setUp pattern as test_session_roster.py)
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

class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-ledger-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        self.ts = _load_engine()

    def test_append_resolves_actor_ordinal(self):
        t = self.ts.new_task("t", ""); t["seq"] = 12
        self.ts.touch(t, session="hub-A")          # ordinal 0
        e = self.ts.add_ledger(t, "spawn", worker_sid="wk-1",
                               actor_sid="hub-A", detail="projectname:fix-99")
        self.assertEqual(e["action"], "spawn")
        self.assertEqual(e["worker"], "wk-1")
        self.assertEqual(e["actor"], "hub-A")
        self.assertEqual(e["actor_ordinal"], 0)
        self.assertEqual(t["ledger"][-1], e)

    def test_unknown_actor_records_none_ordinal(self):
        t = self.ts.new_task("t", ""); t["seq"] = 12
        e = self.ts.add_ledger(t, "adopt", worker_sid="wk-2", actor_sid="ghost")
        self.assertIsNone(e["actor_ordinal"])

    def test_ledger_grows_unbounded(self):
        # RESOLVED override (design brief): the ledger is UNBOUNDED append-only —
        # no trim. Complete provenance is the point. Assert it grows without cap.
        t = self.ts.new_task("t", "")
        n = 450
        for i in range(n):
            self.ts.add_ledger(t, "iterate", worker_sid="wk", detail=str(i))
        self.assertEqual(len(t["ledger"]), n)               # nothing trimmed
        self.assertEqual(t["ledger"][0]["detail"], "0")     # oldest survives
        self.assertEqual(t["ledger"][-1]["detail"], str(n - 1))
        self.assertFalse(hasattr(self.ts, "LEDGER_KEEP"))   # no cap constant

if __name__ == "__main__":
    unittest.main()
