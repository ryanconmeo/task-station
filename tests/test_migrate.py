# tests/test_migrate.py
import os, sys, json, tempfile, shutil, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import todo

class Migrate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.legacy = os.path.join(self.tmp, "legacy")
        self.data = os.path.join(self.tmp, "data")
        os.makedirs(os.path.join(self.legacy, "store", "tasks"))
        os.makedirs(os.path.join(self.legacy, "store", "links"))
        with open(os.path.join(self.legacy, "store", "tasks", "x.json"), "w") as f:
            json.dump({"id": "x"}, f)
        os.makedirs(os.path.join(self.legacy, "delegate"))
        with open(os.path.join(self.legacy, "delegate", "workers.json"), "w") as f:
            json.dump({"w": 1}, f)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_copies_legacy_store_and_registry(self):
        moved = todo._migrate(os.path.join(self.legacy, "store"), self.data)
        self.assertTrue(moved)
        self.assertTrue(os.path.isfile(
            os.path.join(self.data, "store", "tasks", "x.json")))
        self.assertTrue(os.path.isfile(os.path.join(self.data, "workers.json")))
        self.assertTrue(os.path.isfile(os.path.join(self.data, ".migrated")))
        # legacy left intact (copy, not move)
        self.assertTrue(os.path.isfile(
            os.path.join(self.legacy, "store", "tasks", "x.json")))

    def test_idempotent(self):
        self.assertTrue(todo._migrate(os.path.join(self.legacy, "store"), self.data))
        self.assertFalse(todo._migrate(os.path.join(self.legacy, "store"), self.data))

    def test_skips_when_data_store_exists(self):
        os.makedirs(os.path.join(self.data, "store"))
        self.assertFalse(todo._migrate(os.path.join(self.legacy, "store"), self.data))

if __name__ == "__main__":
    unittest.main()
