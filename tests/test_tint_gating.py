# tests/test_tint_gating.py
import os, sys, unittest
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import categories

class TintGating(unittest.TestCase):
    def setUp(self):
        self._plat = categories._sys.platform
        self._tint = categories.TINT_TERMINAL

    def tearDown(self):
        categories._sys.platform = self._plat
        categories.TINT_TERMINAL = self._tint

    def test_macos_with_tint_returns_command(self):
        categories._sys.platform = "darwin"
        categories.TINT_TERMINAL = True
        self.assertEqual(categories.tint_command("green"), "zsh -ic 'green'")

    def test_non_macos_returns_none(self):
        categories._sys.platform = "linux"
        categories.TINT_TERMINAL = True
        self.assertIsNone(categories.tint_command("green"))

    def test_tint_disabled_returns_none(self):
        categories._sys.platform = "darwin"
        categories.TINT_TERMINAL = False
        self.assertIsNone(categories.tint_command("green"))

if __name__ == "__main__":
    unittest.main()
