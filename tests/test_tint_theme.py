import os, sys, io, importlib, tempfile, shutil, unittest
from contextlib import redirect_stdout
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import categories
import config


class _Base(unittest.TestCase):
    """Each test runs against a clean tmp config, and reloads `categories` so cross-
    module state (other suites reload it with overrides and don't restore it) can't
    leak into the THEMES/CATEGORIES structure assertions."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); os.environ["TASK_STATION_HOME"] = self.tmp
        importlib.reload(categories)

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)


class ThemesRegistry(_Base):
    """The baked THEMES registry: two themes, the full taxonomy, 16-ANSI each."""

    def test_two_shipped_themes(self):
        self.assertEqual(set(categories.THEMES), {"dusk", "sands"})
        self.assertEqual(categories.DEFAULT_THEME, "dusk")

    def test_each_theme_has_twelve_categories_full_palette(self):
        cat_keys = set(categories.CATEGORIES)
        self.assertEqual(len(cat_keys), 12)
        for tname, pals in categories.THEMES.items():
            self.assertEqual(set(pals), cat_keys, "theme %s missing categories" % tname)
            for ckey, p in pals.items():
                for field in ("bg", "fg", "bold", "cursor", "sel"):
                    self.assertTrue(p.get(field), "%s/%s missing %s" % (tname, ckey, field))
                self.assertEqual(len(p["ansi"]), 16, "%s/%s ansi != 16" % (tname, ckey))

    def test_categories_carry_no_colour(self):
        # The taxonomy is colour-free now; colour lives only in THEMES.
        for meta in categories.CATEGORIES.values():
            self.assertEqual(set(meta), {"dot", "tag", "label"})


class EffectiveThemes(_Base):
    """effective_themes() deep-merges config.json `themes` over shipped THEMES."""

    def test_no_overrides_returns_shipped(self):
        eff = categories.effective_themes()
        self.assertEqual(eff["dusk"]["red"]["bg"], categories.THEMES["dusk"]["red"]["bg"])

    def test_field_override_merges_and_keeps_siblings(self):
        config.set("themes", {"dusk": {"red": {"bg": "#abcdef"}}})
        eff = categories.effective_themes()
        self.assertEqual(eff["dusk"]["red"]["bg"], "#abcdef")            # overridden
        self.assertEqual(eff["dusk"]["red"]["fg"],
                         categories.THEMES["dusk"]["red"]["fg"])         # sibling field kept
        self.assertEqual(len(eff["dusk"]["red"]["ansi"]), 16)           # ansi kept

    def test_brand_new_named_theme_appears(self):
        config.set("themes", {"ocean": {"green": {"bg": "#001122", "fg": "#ffffff"}}})
        eff = categories.effective_themes()
        self.assertIn("ocean", eff)
        self.assertEqual(eff["ocean"]["green"]["bg"], "#001122")
        self.assertIn("ocean", categories.available_themes())

    def test_shipped_themes_not_mutated_by_override(self):
        before = categories.THEMES["dusk"]["red"]["bg"]
        config.set("themes", {"dusk": {"red": {"bg": "#000000"}}})
        categories.effective_themes()   # must deep-copy, not mutate THEMES
        self.assertEqual(categories.THEMES["dusk"]["red"]["bg"], before)

    def test_available_themes_shipped_first(self):
        config.set("themes", {"aqua": {"red": {"bg": "#111111"}}})
        self.assertEqual(categories.available_themes(), ["dusk", "sands", "aqua"])


class ActiveTheme(_Base):
    def test_default_is_dusk(self):
        self.assertEqual(config.active_theme(), "dusk")

    def test_valid_selection(self):
        config.set("theme", "sands")
        self.assertEqual(config.active_theme(), "sands")

    def test_unknown_falls_back_to_dusk(self):
        config.set("theme", "nonsense")
        self.assertEqual(config.active_theme(), "dusk")

    def test_user_theme_selectable(self):
        config.set("themes", {"ocean": {"red": {"bg": "#001122"}}})
        config.set("theme", "ocean")
        self.assertEqual(config.active_theme(), "ocean")


class ThemeCommands(_Base):
    def _run(self, arg):
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_theme(arg)
        return buf.getvalue()

    def test_select_persists(self):
        out = self._run(["sands"])
        self.assertIn("theme = sands", out)
        self.assertEqual(config.get("theme"), "sands")

    def test_select_unknown_refused(self):
        out = self._run(["bogus"])
        self.assertIn("Unknown theme", out)
        self.assertIsNone(config.get("theme"))

    def test_list_marks_active(self):
        config.set("theme", "sands")
        out = self._run([])                      # bare --theme → list
        self.assertIn("dusk", out)
        self.assertIn("sands", out)
        self.assertIn("* sands", out)            # active marked

    def test_save_reserved_name_refused(self):
        for name in ("save", "edit", "preview", "list", "show", "default"):
            out = self._run(["save", name])
            self.assertIn("reserved", out)
        self.assertIsNone(config.get("themes"))  # nothing written

    def test_save_invalid_name_refused(self):
        out = self._run(["save", "Bad Name"])    # space + uppercase
        self.assertIn("invalid name", out)
        self.assertIsNone(config.get("themes"))

    def test_save_valid_writes_full_palette(self):
        config.set("theme", "sands")             # snapshot the effective active theme
        out = self._run(["save", "my-theme"])
        self.assertIn("saved theme 'my-theme'", out)
        themes = config.get("themes")
        self.assertIn("my-theme", themes)
        self.assertEqual(set(themes["my-theme"]), set(categories.CATEGORIES))
        self.assertEqual(themes["my-theme"]["red"]["bg"],
                         categories.THEMES["sands"]["red"]["bg"])
        self.assertIn("my-theme", categories.available_themes())

    def test_edit_prints_config_path(self):
        out = self._run(["edit"]).strip()
        self.assertTrue(out.endswith("config.json"))


class RenderPalettes(_Base):
    def _import(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
        import render_palettes
        importlib.reload(render_palettes)   # pick up the freshly-reloaded categories
        return render_palettes

    def test_render_html_contains_themes_and_palette(self):
        html = self._import().render_html()
        self.assertIn("<html", html)
        self.assertIn("dusk", html)
        self.assertIn("sands", html)
        self.assertIn(categories.THEMES["dusk"]["red"]["bg"], html)   # a real bg hex rendered
        self.assertIn("[BUG]", html)                                  # a category tag rendered

    def test_render_includes_user_theme(self):
        config.set("themes", {"ocean": {"green": {"bg": "#001122", "fg": "#ffffff",
                                                   "bold": "#88ccff", "cursor": "#88ccff",
                                                   "sel": "#003344", "ansi": ["#000000"] * 16}}})
        html = self._import().render_html()
        self.assertIn("ocean", html)
        self.assertIn("#001122", html)


if __name__ == "__main__":
    unittest.main()
