import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import categories

# OSC helpers (BEL-terminated) mirroring tint_escape's emission.
def _osc(n, body): return "\033]%s;%s\007" % (n, body)


class Tint(unittest.TestCase):
    """Full-palette escape tint. Profile mode is gone; bg uses OSC 11 for both
    iTerm and Terminal.app, with an iTerm-only SetColors=bold extra."""

    def test_full_palette_iterm_has_bg_fg_ansi_bold(self):
        out = categories.tint_escape("silver", "auto", "iterm")
        m = categories.CATEGORIES["silver"]
        self.assertIn(_osc(11, m["hex"]), out)        # bg via OSC 11
        self.assertIn(_osc(10, m["fg"]), out)         # fg via OSC 10
        self.assertIn(_osc(12, m["cursor"]), out)     # cursor via OSC 12
        self.assertIn(_osc("4", "1;%s" % m["ansi"][1]), out)   # an ANSI slot via OSC 4
        self.assertIn("\033]1337;SetColors=bold=%s\007" % m["bold"].lstrip("#"), out)  # iTerm bold

    def test_full_palette_terminal_has_bg_fg_ansi_no_bold(self):
        out = categories.tint_escape("silver", "auto", "terminal")
        m = categories.CATEGORIES["silver"]
        self.assertIn(_osc(11, m["hex"]), out)
        self.assertIn(_osc(10, m["fg"]), out)
        self.assertIn(_osc("4", "1;%s" % m["ansi"][1]), out)
        self.assertNotIn("SetColors=bold=", out)      # bold is iTerm-only

    def test_all_sixteen_ansi_slots_emitted(self):
        out = categories.tint_escape("silver", "auto", "terminal")
        m = categories.CATEGORIES["silver"]
        for n in range(16):
            self.assertIn(_osc("4", "%d;%s" % (n, m["ansi"][n])), out)

    def test_bg_only_category_emits_only_bg(self):
        # A minimal slot that defines only a bg must still emit JUST the bg
        # (back-compat for public minimal taxonomies).
        try:
            categories.CATEGORIES["__probe__"] = {
                "dot": "⬜", "tag": "PROBE", "label": "probe", "hex": "#123456",
            }
            categories._ALIASES = categories._build_aliases()
            out = categories.tint_escape("__probe__", "auto", "iterm")
            self.assertEqual(out, _osc(11, "#123456"))
        finally:
            categories.CATEGORIES.pop("__probe__", None)
            categories._ALIASES = categories._build_aliases()

    def test_none_term_is_empty(self):
        self.assertEqual(categories.tint_escape("green", "auto", "none"), "")

    def test_unknown_color_is_empty(self):
        self.assertEqual(categories.tint_escape("notacolor", "auto", "iterm"), "")


if __name__ == "__main__":
    unittest.main()
