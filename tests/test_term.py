import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import term

class Detect(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("CLAUDE_TODO_TERM","TERM_PROGRAM","LC_TERMINAL")}
        for k in self._env: os.environ.pop(k, None)
    def tearDown(self):
        for k,v in self._env.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k]=v
    def test_override_wins(self):
        os.environ["CLAUDE_TODO_TERM"]="none"; os.environ["TERM_PROGRAM"]="iTerm.app"
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

if __name__=="__main__": unittest.main()
