import os, sys, tempfile, shutil, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import setup as station_setup

class PolicyBlock(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        self.md = os.path.join(self.tmp, "CLAUDE.md")
        with open(self.md, "w") as f: f.write("# my rules\n\nkeep this.\n")
        self.before = open(self.md).read()
    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None); shutil.rmtree(self.tmp, ignore_errors=True)

    def test_apply_then_remove_is_byte_identical(self):
        station_setup._apply_block(self.md, "POLICY TEXT")
        self.assertIn("POLICY TEXT", open(self.md).read())
        self.assertIn("BEGIN task-station:delegation-policy", open(self.md).read())
        station_setup._remove_block(self.md)
        self.assertEqual(open(self.md).read(), self.before)   # exact restore

    def test_apply_is_idempotent(self):
        station_setup._apply_block(self.md, "A")
        station_setup._apply_block(self.md, "B")          # replace, not duplicate
        body = open(self.md).read()
        self.assertEqual(body.count("BEGIN task-station:delegation-policy"), 1)
        self.assertIn("B", body); self.assertNotIn("A", body)

    def test_backup_written(self):
        station_setup._apply_block(self.md, "X")
        self.assertTrue(os.path.exists(self.md + ".bak"))

    def test_remove_detects_edited_block(self):
        station_setup._apply_block(self.md, "ORIG")
        b = open(self.md).read().replace("ORIG", "HAND EDIT")
        with open(self.md, "w") as f: f.write(b)
        self.assertFalse(station_setup._remove_block(self.md))   # refuses, returns False
        self.assertIn("HAND EDIT", open(self.md).read())      # left intact

if __name__=="__main__": unittest.main()
