import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import term

_DETECT_ENV = ("TASK_STATION_TERM", "TERM_PROGRAM", "LC_TERMINAL", "WT_SESSION",
               "KITTY_WINDOW_ID", "ALACRITTY_WINDOW_ID", "ALACRITTY_SOCKET", "TERM")


class Detect(unittest.TestCase):
    def setUp(self):
        # Clear EVERY signal detect() reads so an ambient $TERM (xterm-256color in
        # most CI) can't leak the OSC fallback into a test expecting "none".
        self._env = {k: os.environ.get(k) for k in _DETECT_ENV}
        for k in self._env: os.environ.pop(k, None)
    def tearDown(self):
        for k,v in self._env.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k]=v
    def test_override_wins(self):
        os.environ["TASK_STATION_TERM"]="none"; os.environ["TERM_PROGRAM"]="iTerm.app"
        self.assertEqual(term.detect(), "none")
    def test_override_osc(self):
        os.environ["TASK_STATION_TERM"]="osc"
        self.assertEqual(term.detect(), "osc")
    def test_override_unrecognised_is_none(self):
        os.environ["TASK_STATION_TERM"]="bogus"
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
    def test_wezterm_is_osc(self):
        os.environ["TERM_PROGRAM"]="WezTerm"
        self.assertEqual(term.detect(), "osc")
    def test_vscode_is_osc(self):
        os.environ["TERM_PROGRAM"]="vscode"
        self.assertEqual(term.detect(), "osc")
    def test_ghostty_is_osc(self):
        os.environ["TERM_PROGRAM"]="ghostty"
        self.assertEqual(term.detect(), "osc")
    def test_windows_terminal_is_osc(self):
        os.environ["WT_SESSION"]="abc-123"
        self.assertEqual(term.detect(), "osc")
    def test_kitty_is_osc(self):
        os.environ["KITTY_WINDOW_ID"]="1"
        self.assertEqual(term.detect(), "osc")
    def test_alacritty_by_window_id_is_osc(self):
        os.environ["ALACRITTY_WINDOW_ID"]="1"
        self.assertEqual(term.detect(), "osc")
    def test_alacritty_by_socket_is_osc(self):
        os.environ["ALACRITTY_SOCKET"]="/tmp/a.sock"
        self.assertEqual(term.detect(), "osc")
    def test_xterm_term_fallback_is_osc(self):
        os.environ["TERM"]="xterm-256color"
        self.assertEqual(term.detect(), "osc")
    def test_256color_term_fallback_is_osc(self):
        os.environ["TERM"]="screen-256color"
        self.assertEqual(term.detect(), "osc")
    def test_no_signal_is_none(self):
        os.environ["TERM"]="dumb"
        self.assertEqual(term.detect(), "none")
    def test_empty_env_is_none(self):
        self.assertEqual(term.detect(), "none")


class TmuxWrap(unittest.TestCase):
    def setUp(self):
        self._tmux = os.environ.get("TMUX")
        os.environ.pop("TMUX", None)
    def tearDown(self):
        if self._tmux is None: os.environ.pop("TMUX", None)
        else: os.environ["TMUX"] = self._tmux
    def test_noop_without_tmux(self):
        self.assertEqual(term.tmux_wrap("\033]11;#123456\007"), "\033]11;#123456\007")
    def test_empty_stays_empty(self):
        os.environ["TMUX"] = "/tmp/tmux-1000/default,123,0"
        self.assertEqual(term.tmux_wrap(""), "")
    def test_wraps_and_doubles_esc_under_tmux(self):
        os.environ["TMUX"] = "/tmp/tmux-1000/default,123,0"
        out = term.tmux_wrap("\033]11;#123456\007")
        # ESC P tmux ; <body, each ESC doubled> ESC backslash
        self.assertTrue(out.startswith("\033Ptmux;"))
        self.assertTrue(out.endswith("\033\\"))
        self.assertIn("\033\033]11;#123456\007", out)

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
