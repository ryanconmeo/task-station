import os, sys, io, types, importlib, tempfile, shutil, unittest
from contextlib import redirect_stdout
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import categories
import config


class _Base(unittest.TestCase):
    """Each test runs against a clean tmp config and reloads `categories` so cross-
    module state (other suites reload it with overrides and don't restore it) can't
    leak into the THEMES/CATEGORIES structure assertions."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); os.environ["TASK_STATION_HOME"] = self.tmp
        importlib.reload(categories)

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)


class ThemesRegistry(_Base):
    """The baked THEMES registry: one `sands` theme, two variants, 16-ANSI each."""

    def test_one_shipped_theme_two_variants(self):
        self.assertEqual(set(categories.THEMES), {"sands"})
        self.assertEqual(categories.DEFAULT_THEME, "sands")
        self.assertEqual(set(categories.THEMES["sands"]), {"dark", "light"})
        self.assertEqual(tuple(categories.VARIANTS), ("dark", "light"))

    def test_variant_labels_and_display(self):
        self.assertEqual(categories.theme_display("sands"), "Sands")
        self.assertEqual(categories.variant_label("sands", "dark"), "Dark Sands")
        self.assertEqual(categories.variant_label("sands", "light"), "Light Sands")
        self.assertEqual(categories.variant_label("ocean", "dark"), "Dark Ocean")

    def test_each_variant_has_twelve_categories_full_palette(self):
        cat_keys = set(categories.CATEGORIES)
        self.assertEqual(len(cat_keys), 12)
        for variant in ("dark", "light"):
            pals = categories.THEMES["sands"][variant]
            self.assertEqual(set(pals), cat_keys, "variant %s missing categories" % variant)
            for ckey, p in pals.items():
                for field in ("bg", "fg", "bold", "cursor", "sel"):
                    self.assertTrue(p.get(field), "%s/%s missing %s" % (variant, ckey, field))
                self.assertEqual(len(p["ansi"]), 16, "%s/%s ansi != 16" % (variant, ckey))

    def test_dark_and_light_palettes(self):
        # dark = the muted reds, light = the vibrant reds (same hexes as before).
        self.assertEqual(categories.THEMES["sands"]["dark"]["red"]["bg"], "#2c1518")
        self.assertEqual(categories.THEMES["sands"]["light"]["red"]["bg"], "#80232a")

    def test_categories_carry_no_colour(self):
        for meta in categories.CATEGORIES.values():
            self.assertEqual(set(meta), {"dot", "tag", "label"})


class EffectiveThemes(_Base):
    """effective_themes() deep-merges config.json `themes` over shipped THEMES,
    variant-nested (theme → variant → category → field)."""

    def test_no_overrides_returns_shipped(self):
        eff = categories.effective_themes()
        self.assertEqual(eff["sands"]["dark"]["red"]["bg"],
                         categories.THEMES["sands"]["dark"]["red"]["bg"])

    def test_field_override_merges_and_keeps_siblings(self):
        config.set("themes", {"sands": {"dark": {"red": {"bg": "#abcdef"}}}})
        eff = categories.effective_themes()
        self.assertEqual(eff["sands"]["dark"]["red"]["bg"], "#abcdef")            # overridden
        self.assertEqual(eff["sands"]["dark"]["red"]["fg"],
                         categories.THEMES["sands"]["dark"]["red"]["fg"])         # sibling kept
        self.assertEqual(len(eff["sands"]["dark"]["red"]["ansi"]), 16)           # ansi kept
        self.assertEqual(eff["sands"]["light"]["red"]["bg"],
                         categories.THEMES["sands"]["light"]["red"]["bg"])        # other variant untouched

    def test_brand_new_named_theme_appears(self):
        config.set("themes", {"ocean": {"dark": {"green": {"bg": "#001122", "fg": "#ffffff"}}}})
        eff = categories.effective_themes()
        self.assertIn("ocean", eff)
        self.assertEqual(eff["ocean"]["dark"]["green"]["bg"], "#001122")
        self.assertIn("ocean", categories.available_themes())

    def test_shipped_themes_not_mutated_by_override(self):
        before = categories.THEMES["sands"]["dark"]["red"]["bg"]
        config.set("themes", {"sands": {"dark": {"red": {"bg": "#000000"}}}})
        categories.effective_themes()   # must deep-copy, not mutate THEMES
        self.assertEqual(categories.THEMES["sands"]["dark"]["red"]["bg"], before)

    def test_available_themes_sands_first(self):
        config.set("themes", {"aqua": {"dark": {"red": {"bg": "#111111"}}}})
        self.assertEqual(categories.available_themes(), ["sands", "aqua"])


class AppearanceVariant(_Base):
    """resolve_variant(): forced dark/light, or auto via the OS appearance."""

    def test_forced_dark_without_os_call(self):
        config.set("tint_theme", "dark")
        import subprocess
        orig = subprocess.run
        subprocess.run = lambda *a, **k: self.fail("no OS probe for a forced variant")
        try:
            self.assertEqual(categories.resolve_variant(), "dark")
        finally:
            subprocess.run = orig

    def test_forced_light_without_os_call(self):
        config.set("tint_theme", "light")
        import subprocess
        orig = subprocess.run
        subprocess.run = lambda *a, **k: self.fail("no OS probe for a forced variant")
        try:
            self.assertEqual(categories.resolve_variant(), "light")
        finally:
            subprocess.run = orig

    def _with_macos(self, stdout):
        """Run resolve_variant pretending to be macOS with a mocked AppleInterfaceStyle."""
        import subprocess
        orig_sys, orig_run = categories._sys, subprocess.run
        categories._sys = types.SimpleNamespace(platform="darwin")
        subprocess.run = lambda *a, **k: types.SimpleNamespace(stdout=stdout)
        try:
            return categories.resolve_variant()
        finally:
            categories._sys, subprocess.run = orig_sys, orig_run

    def test_auto_dark_when_os_dark(self):
        config.set("tint_theme", "auto")
        self.assertEqual(self._with_macos("Dark\n"), "dark")

    def test_auto_light_when_os_light(self):
        config.set("tint_theme", "auto")
        self.assertEqual(self._with_macos(""), "light")   # key absent => light mode

    def test_auto_returns_valid_variant(self):
        config.set("tint_theme", "auto")
        self.assertIn(categories.resolve_variant(), ("dark", "light"))


class TintVariant(_Base):
    """tint_escape switches variant with --tint-theme; default auto follows the OS."""

    def _osc11(self, variant, key):
        return "\033]11;%s\007" % categories.THEMES["sands"][variant][key]["bg"]

    def test_dark_vs_light_differ(self):
        config.set("tint_theme", "dark")
        dark = categories.tint_escape("silver", "auto", "iterm")
        config.set("tint_theme", "light")
        light = categories.tint_escape("silver", "auto", "iterm")
        self.assertNotEqual(dark, light)
        self.assertIn(self._osc11("dark", "silver"), dark)
        self.assertIn(self._osc11("light", "silver"), light)

    def test_default_setting_is_auto(self):
        self.assertEqual(config.tint_theme(), "auto")


class ActiveTheme(_Base):
    def test_default_is_sands(self):
        self.assertEqual(config.active_theme(), "sands")

    def test_valid_selection(self):
        config.set("themes", {"ocean": {"dark": {"red": {"bg": "#001122"}}}})
        config.set("theme", "ocean")
        self.assertEqual(config.active_theme(), "ocean")

    def test_unknown_falls_back_to_sands(self):
        config.set("theme", "nonsense")
        self.assertEqual(config.active_theme(), "sands")


class ThemeCommands(_Base):
    def _run(self, arg):
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_theme(arg)
        return buf.getvalue()

    def test_select_persists(self):
        config.set("themes", {"ocean": {"dark": {"red": {"bg": "#001122"}}}})
        out = self._run(["ocean"])
        self.assertIn("theme = ocean", out)
        self.assertEqual(config.get("theme"), "ocean")

    def test_select_unknown_refused(self):
        out = self._run(["bogus"])
        self.assertIn("Unknown theme", out)
        self.assertIsNone(config.get("theme"))

    def test_list_marks_active_and_shows_labels(self):
        config.set("tint_theme", "dark")
        out = self._run([])                       # bare --theme → list
        self.assertIn("* sands", out)             # active marked
        self.assertIn("Dark Sands", out)          # variant labels per theme line
        self.assertIn("Light Sands", out)
        self.assertIn("--tint-theme dark → Dark Sands", out)   # appearance line

    def test_save_reserved_name_refused(self):
        for name in ("save", "edit", "preview", "list", "show", "default"):
            out = self._run(["save", name])
            self.assertIn("reserved", out)
        self.assertIsNone(config.get("themes"))  # nothing written

    def test_save_invalid_name_refused(self):
        out = self._run(["save", "Bad Name"])    # space + uppercase
        self.assertIn("invalid name", out)
        self.assertIsNone(config.get("themes"))

    def test_save_captures_both_variants(self):
        config.set("tint_theme", "light")        # appearance must NOT affect what's saved
        out = self._run(["save", "my-theme"])
        self.assertIn("saved theme 'my-theme'", out)
        self.assertIn("both variants", out)
        themes = config.get("themes")
        self.assertIn("my-theme", themes)
        # BOTH variants captured, each full (12 categories)
        self.assertEqual(set(themes["my-theme"]), {"dark", "light"})
        self.assertEqual(set(themes["my-theme"]["dark"]), set(categories.CATEGORIES))
        self.assertEqual(set(themes["my-theme"]["light"]), set(categories.CATEGORIES))
        self.assertEqual(themes["my-theme"]["dark"]["red"]["bg"],
                         categories.THEMES["sands"]["dark"]["red"]["bg"])
        self.assertEqual(themes["my-theme"]["light"]["red"]["bg"],
                         categories.THEMES["sands"]["light"]["red"]["bg"])
        self.assertIn("my-theme", categories.available_themes())

    def test_partial_user_theme_falls_back_to_sands(self):
        # A theme that defines only one variant falls back to sands for the other.
        config.set("themes", {"mt": {"dark": {"green": {"bg": "#111111"}}}})
        self.assertEqual(categories.theme_palette("mt", "green", "dark")["bg"], "#111111")
        self.assertEqual(categories.theme_palette("mt", "red", "light")["bg"],
                         categories.THEMES["sands"]["light"]["red"]["bg"])

    def test_edit_prints_config_path(self):
        out = self._run(["edit"]).strip()
        self.assertTrue(out.endswith("config.json"))


class RenderPalettes(_Base):
    def _import(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
        import render_palettes
        importlib.reload(render_palettes)   # pick up the freshly-reloaded categories
        return render_palettes

    def test_render_html_contains_both_variant_labels(self):
        html = self._import().render_html()
        self.assertIn("<html", html)
        self.assertIn("Sands — Dark Sands", html)     # section headers
        self.assertIn("Sands — Light Sands", html)
        self.assertIn(categories.THEMES["sands"]["dark"]["red"]["bg"], html)   # a real dark bg
        self.assertIn(categories.THEMES["sands"]["light"]["red"]["bg"], html)  # a real light bg
        self.assertIn("[BUG]", html)

    def test_render_includes_user_theme(self):
        config.set("themes", {"ocean": {"dark": {"green": {"bg": "#001122", "fg": "#ffffff",
                                                           "bold": "#88ccff", "cursor": "#88ccff",
                                                           "sel": "#003344", "ansi": ["#000000"] * 16}}}})
        html = self._import().render_html()
        self.assertIn("Ocean — Dark Ocean", html)
        self.assertIn("#001122", html)


if __name__ == "__main__":
    unittest.main()
