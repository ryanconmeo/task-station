import os, sys, tempfile, shutil, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import categories
import config

# OSC helpers (BEL-terminated) mirroring tint_escape's emission.
def _osc(n, body): return "\033]%s;%s\007" % (n, body)


class Tint(unittest.TestCase):
    """Full-palette escape tint sourced from the ACTIVE theme (default `default`) in
    the resolved VARIANT. bg uses OSC 11 for both iTerm and Terminal.app, with an
    iTerm-only SetColors=bold extra. dot/tag/label still come from CATEGORIES."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(); os.environ["TASK_STATION_HOME"] = self.tmp
        # tint_escape DCS-wraps under tmux; clear $TMUX so these raw-escape assertions
        # hold regardless of whether the suite itself runs inside tmux.
        self._tmux = os.environ.pop("TMUX", None)
        config.set("tint_theme", "dark")    # force the dark variant for determinism

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        if self._tmux is not None: os.environ["TMUX"] = self._tmux
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _pal(self, variant, key):
        return categories.THEMES["sands"][variant][key]

    def test_full_palette_iterm_dark_variant(self):
        out = categories.tint_escape("silver", "iterm")
        p = self._pal("dark", "silver")
        self.assertIn(_osc(11, p["bg"]), out)                   # bg via OSC 11
        self.assertIn(_osc(10, p["fg"]), out)                   # fg via OSC 10
        self.assertIn(_osc(12, p["cursor"]), out)               # cursor via OSC 12
        self.assertIn(_osc("4", "1;%s" % p["ansi"][1]), out)    # an ANSI slot via OSC 4
        self.assertIn(_osc(17, p["sel"]), out)                  # selection via OSC 17
        self.assertIn("\033]1337;SetColors=bold=%s\007" % p["bold"].lstrip("#"), out)  # iTerm bold

    def test_full_palette_terminal_has_bg_fg_ansi_no_bold(self):
        out = categories.tint_escape("silver", "terminal")
        p = self._pal("dark", "silver")
        self.assertIn(_osc(11, p["bg"]), out)
        self.assertIn(_osc(10, p["fg"]), out)
        self.assertIn(_osc("4", "1;%s" % p["ansi"][1]), out)
        self.assertNotIn("SetColors=bold=", out)                # bold is iTerm-only

    def test_all_sixteen_ansi_slots_emitted(self):
        out = categories.tint_escape("silver", "terminal")
        p = self._pal("dark", "silver")
        for n in range(16):
            self.assertIn(_osc("4", "%d;%s" % (n, p["ansi"][n])), out)

    def test_tint_theme_switches_variant(self):
        # dark variant → Dusk silver bg.
        config.set("tint_theme", "dark")
        self.assertIn(_osc(11, self._pal("dark", "silver")["bg"]),
                      categories.tint_escape("silver", "iterm"))
        # light variant → Sands silver bg, and the dark bg is gone.
        config.set("tint_theme", "light")
        out = categories.tint_escape("silver", "iterm")
        self.assertIn(_osc(11, self._pal("light", "silver")["bg"]), out)
        self.assertNotIn(_osc(11, self._pal("dark", "silver")["bg"]), out)

    def test_auto_follows_resolved_variant(self):
        # With tint_theme=auto, the emitted bg matches whatever variant resolves.
        config.set("tint_theme", "auto")
        variant = categories.resolve_variant()
        self.assertIn(variant, ("dark", "light"))
        out = categories.tint_escape("silver", "iterm")
        self.assertIn(_osc(11, self._pal(variant, "silver")["bg"]), out)

    def test_bg_only_palette_emits_only_bg(self):
        # A theme variant palette that defines ONLY a bg must emit JUST the bg.
        config.set("themes", {"mini": {"dark": {"green": {"bg": "#123456"}}}})
        config.set("theme", "mini"); config.set("tint_theme", "dark")
        out = categories.tint_escape("green", "iterm")
        self.assertEqual(out, _osc(11, "#123456"))

    def test_missing_category_falls_back_to_default(self):
        # 'red' isn't in the custom theme's dark variant → fall back to default dark.
        config.set("themes", {"mini": {"dark": {"green": {"bg": "#123456"}}}})
        config.set("theme", "mini"); config.set("tint_theme", "dark")
        out = categories.tint_escape("red", "iterm")
        self.assertIn(_osc(11, self._pal("dark", "red")["bg"]), out)

    def test_none_term_is_empty(self):
        self.assertEqual(categories.tint_escape("green", "none"), "")

    def test_unknown_color_is_empty(self):
        self.assertEqual(categories.tint_escape("notacolor", "iterm"), "")

    def test_generic_osc_term_emits_bg_fg_ansi_no_bold(self):
        # A non-iTerm xterm-compatible terminal (term.detect() -> "osc") gets the
        # standard OSC palette but NOT the iTerm-only SetColors=bold extra.
        out = categories.tint_escape("silver", "osc")
        p = self._pal("dark", "silver")
        self.assertIn(_osc(11, p["bg"]), out)
        self.assertIn(_osc(10, p["fg"]), out)
        self.assertIn(_osc("4", "1;%s" % p["ansi"][1]), out)
        self.assertNotIn("SetColors=bold=", out)

    def test_tmux_wraps_the_escape(self):
        # Under tmux the whole sequence is DCS-wrapped (ESC P tmux ; … ESC \) with
        # every inner ESC doubled, so it reaches the real terminal through the pane.
        os.environ["TMUX"] = "/tmp/tmux-1000/default,123,0"
        try:
            out = categories.tint_escape("silver", "iterm")
        finally:
            os.environ.pop("TMUX", None)
        p = self._pal("dark", "silver")
        self.assertTrue(out.startswith("\033Ptmux;"))
        self.assertTrue(out.endswith("\033\\"))
        self.assertIn("\033\033]11;%s\007" % p["bg"], out)   # inner ESC doubled

    def test_tmux_none_term_still_empty(self):
        # A no-op tint stays "" even under tmux (nothing to wrap).
        os.environ["TMUX"] = "/tmp/tmux-1000/default,123,0"
        try:
            self.assertEqual(categories.tint_escape("green", "none"), "")
        finally:
            os.environ.pop("TMUX", None)


if __name__ == "__main__":
    unittest.main()
