import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import term

class Detect(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("TASK_STATION_TERM","TERM_PROGRAM","LC_TERMINAL")}
        for k in self._env: os.environ.pop(k, None)
    def tearDown(self):
        for k,v in self._env.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k]=v
    def test_override_wins(self):
        os.environ["TASK_STATION_TERM"]="none"; os.environ["TERM_PROGRAM"]="iTerm.app"
        self.assertEqual(term.detect(), "none")
    def test_iterm_by_lc_terminal(self):
        os.environ["LC_TERMINAL"]="iTerm2"
        self.assertEqual(term.detect(), "iterm")
    def test_iterm_by_term_program(self):
        os.environ["TERM_PROGRAM"]="iTerm.app"
        self.assertEqual(term.detect(), "iterm")
    def test_apple_terminal(self):
        os.environ["TERM_PROGRAM"]="Apple_Terminal"
        self.assertEqual(term.detect(), "terminal")
    def test_unknown_is_none(self):
        os.environ["TERM_PROGRAM"]="vscode"
        self.assertEqual(term.detect(), "none")

class Width(unittest.TestCase):
    def setUp(self):
        self._cols = os.environ.get("COLUMNS")
    def tearDown(self):
        if self._cols is None: os.environ.pop("COLUMNS", None)
        else: os.environ["COLUMNS"] = self._cols
    def test_honors_columns_env(self):
        os.environ["COLUMNS"] = "123"
        self.assertEqual(term.width(), 123)
    def test_clamps_to_minimum_60(self):
        os.environ["COLUMNS"] = "40"
        self.assertEqual(term.width(), 60)
    def test_wide_columns_passthrough(self):
        os.environ["COLUMNS"] = "200"
        self.assertEqual(term.width(), 200)

if __name__=="__main__": unittest.main()
