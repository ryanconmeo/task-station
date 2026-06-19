import os, sys, tempfile, shutil, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import categories


class HexFor(unittest.TestCase):
    def test_light_returns_light_hex(self):
        self.assertEqual(categories.hex_for("red", "light"),
                         categories.CATEGORIES["red"]["hex_light"])

    def test_dark_returns_dark_hex(self):
        self.assertEqual(categories.hex_for("red", "dark"),
                         categories.CATEGORIES["red"]["hex"])

    def test_falls_back_to_hex_when_no_hex_light(self):
        # A slot/override that defines only `hex` must still tint under light.
        try:
            categories.CATEGORIES["__probe__"] = {
                "dot": "⬜", "tag": "PROBE", "label": "probe", "hex": "#123456",
            }
            categories._ALIASES = categories._build_aliases()
            self.assertEqual(categories.hex_for("__probe__", "light"), "#123456")
            self.assertEqual(categories.hex_for("__probe__", "dark"), "#123456")
        finally:
            categories.CATEGORIES.pop("__probe__", None)
            categories._ALIASES = categories._build_aliases()

    def test_unknown_color_is_none(self):
        self.assertIsNone(categories.hex_for("notacolor", "light"))


class ResolveTheme(unittest.TestCase):
    """resolve_theme must honor a forced setting WITHOUT touching the OS."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(); os.environ["TASK_STATION_HOME"] = self.tmp

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_forced_dark_returns_dark_without_detection(self):
        import config
        config.set("tint_theme", "dark")
        import subprocess
        orig = subprocess.run
        subprocess.run = lambda *a, **k: self.fail("defaults must not run for a forced theme")
        try:
            self.assertEqual(categories.resolve_theme(), "dark")
        finally:
            subprocess.run = orig

    def test_forced_light_returns_light_without_detection(self):
        import config
        config.set("tint_theme", "light")
        import subprocess
        orig = subprocess.run
        subprocess.run = lambda *a, **k: self.fail("defaults must not run for a forced theme")
        try:
            self.assertEqual(categories.resolve_theme(), "light")
        finally:
            subprocess.run = orig

    def test_auto_returns_a_valid_theme(self):
        import config
        config.set("tint_theme", "auto")
        self.assertIn(categories.resolve_theme(), ("dark", "light"))


if __name__ == "__main__":
    unittest.main()
