import os, sys, json, tempfile, shutil, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import config

class Config(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); os.environ["TASK_STATION_HOME"] = self.tmp
    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None); shutil.rmtree(self.tmp, ignore_errors=True)

    def test_set_get_roundtrip(self):
        config.set("tint_mode", "profile")
        self.assertEqual(config.get("tint_mode"), "profile")
        with open(os.path.join(self.tmp, "config.json")) as f:
            self.assertEqual(json.load(f)["tint_mode"], "profile")

    def test_get_default_when_absent(self):
        self.assertEqual(config.get("tint_mode", "auto"), "auto")

    def test_workspace_dirs_parsed(self):
        config.set("workspace_dirs", ["~/a", "~/b"])
        self.assertEqual(config.workspace_dirs(),
                         [os.path.expanduser("~/a"), os.path.expanduser("~/b")])

    def test_unset_restores_default(self):
        config.set("tint_mode", "profile"); config.unset("tint_mode")
        self.assertEqual(config.get("tint_mode", "auto"), "auto")

if __name__=="__main__": unittest.main()
