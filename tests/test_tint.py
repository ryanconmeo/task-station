import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import categories

class Tint(unittest.TestCase):
    def test_profile_mode_emits_alias(self):
        self.assertEqual(categories.tint_escape("green", "profile", "terminal"),
                         "zsh -ic 'green'")
    def test_auto_iterm_emits_setcolors(self):
        out = categories.tint_escape("green", "auto", "iterm")
        self.assertIn("\033]1337;SetColors=bg=", out)
    def test_auto_terminal_emits_osc11(self):
        out = categories.tint_escape("green", "auto", "terminal")
        self.assertTrue(out.startswith("\033]11;#"))
    def test_none_term_is_empty(self):
        self.assertEqual(categories.tint_escape("green", "auto", "none"), "")
    def test_unknown_color_is_empty(self):
        self.assertEqual(categories.tint_escape("notacolor", "auto", "iterm"), "")

if __name__=="__main__": unittest.main()
