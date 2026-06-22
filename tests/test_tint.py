import os, sys, tempfile, shutil, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import categories

# OSC helpers (BEL-terminated) mirroring tint_escape's emission.
def _osc(n, body): return "\033]%s;%s\007" % (n, body)


class Tint(unittest.TestCase):
    """Full-palette escape tint sourced from the ACTIVE theme (default `dusk`).
    bg uses OSC 11 for both iTerm and Terminal.app, with an iTerm-only
    SetColors=bold extra. dot/tag/label still come from CATEGORIES."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(); os.environ["TASK_STATION_HOME"] = self.tmp

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _pal(self, theme, key):
        return categories.THEMES[theme][key]

    def test_full_palette_iterm_has_bg_fg_cursor_ansi_sel_bold(self):
        out = categories.tint_escape("silver", "auto", "iterm")
        p = self._pal("dusk", "silver")
        self.assertIn(_osc(11, p["bg"]), out)                   # bg via OSC 11
        self.assertIn(_osc(10, p["fg"]), out)                   # fg via OSC 10
        self.assertIn(_osc(12, p["cursor"]), out)               # cursor via OSC 12
        self.assertIn(_osc("4", "1;%s" % p["ansi"][1]), out)    # an ANSI slot via OSC 4
        self.assertIn(_osc(17, p["sel"]), out)                  # selection via OSC 17
        self.assertIn("\033]1337;SetColors=bold=%s\007" % p["bold"].lstrip("#"), out)  # iTerm bold

    def test_full_palette_terminal_has_bg_fg_ansi_no_bold(self):
        out = categories.tint_escape("silver", "auto", "terminal")
        p = self._pal("dusk", "silver")
        self.assertIn(_osc(11, p["bg"]), out)
        self.assertIn(_osc(10, p["fg"]), out)
        self.assertIn(_osc("4", "1;%s" % p["ansi"][1]), out)
        self.assertNotIn("SetColors=bold=", out)                # bold is iTerm-only

    def test_all_sixteen_ansi_slots_emitted(self):
        out = categories.tint_escape("silver", "auto", "terminal")
        p = self._pal("dusk", "silver")
        for n in range(16):
            self.assertIn(_osc("4", "%d;%s" % (n, p["ansi"][n])), out)

    def test_active_theme_switches_palette(self):
        import config
        # dusk (default) → dusk red bg.
        self.assertIn(_osc(11, categories.THEMES["dusk"]["red"]["bg"]),
                      categories.tint_escape("red", "auto", "iterm"))
        # switch active theme → sands red bg, and the dusk bg is gone.
        config.set("theme", "sands")
        out = categories.tint_escape("red", "auto", "iterm")
        self.assertIn(_osc(11, categories.THEMES["sands"]["red"]["bg"]), out)
        self.assertNotIn(_osc(11, categories.THEMES["dusk"]["red"]["bg"]), out)

    def test_bg_only_palette_emits_only_bg(self):
        # A theme palette that defines ONLY a bg must emit JUST the bg.
        import config
        config.set("themes", {"mini": {"green": {"bg": "#123456"}}})
        config.set("theme", "mini")
        out = categories.tint_escape("green", "auto", "iterm")
        self.assertEqual(out, _osc(11, "#123456"))

    def test_category_absent_from_active_theme_is_empty(self):
        import config
        config.set("themes", {"mini": {"green": {"bg": "#123456"}}})
        config.set("theme", "mini")
        # 'red' has no palette in the active 'mini' theme → no-op.
        self.assertEqual(categories.tint_escape("red", "auto", "iterm"), "")

    def test_none_term_is_empty(self):
        self.assertEqual(categories.tint_escape("green", "auto", "none"), "")

    def test_unknown_color_is_empty(self):
        self.assertEqual(categories.tint_escape("notacolor", "auto", "iterm"), "")


if __name__ == "__main__":
    unittest.main()
