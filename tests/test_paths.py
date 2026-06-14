# tests/test_paths.py
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import paths

class DataDirResolution(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("CLAUDE_TODO_HOME", "CLAUDE_CONFIG_DIR", "XDG_STATE_HOME")}
        for k in self._saved:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v

    def test_explicit_override_wins(self):
        os.environ["CLAUDE_TODO_HOME"] = "/tmp/td-home"
        os.environ["CLAUDE_CONFIG_DIR"] = "/tmp/cfg"
        self.assertEqual(paths.data_dir(), "/tmp/td-home")

    def test_config_dir_then_todo_data(self):
        os.environ["CLAUDE_CONFIG_DIR"] = "/tmp/cfg"
        self.assertEqual(paths.data_dir(), "/tmp/cfg/todo-data")

    def test_xdg_only_when_config_dir_unset(self):
        os.environ["XDG_STATE_HOME"] = "/tmp/xdg"
        self.assertEqual(paths.data_dir(), "/tmp/xdg/claude-todo")

    def test_xdg_expanduser(self):
        os.environ["XDG_STATE_HOME"] = "~/xdgstate"
        self.assertEqual(paths.data_dir(),
                         os.path.join(os.path.expanduser("~/xdgstate"), "claude-todo"))

    def test_default(self):
        self.assertEqual(paths.data_dir(),
                         os.path.expanduser("~/.claude/todo-data"))

if __name__ == "__main__":
    unittest.main()
