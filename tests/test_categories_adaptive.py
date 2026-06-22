# tests/test_categories_adaptive.py
"""Adaptive-categories redesign (task #87): new shipped defaults, slot-determined
emoji, seeded-but-removable enabled set with GENERAL permanent, and presets."""
import os, sys, json, tempfile, shutil, importlib, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_config(self, obj):
        with open(os.path.join(self.tmp, "config.json"), "w") as f:
            json.dump(obj, f)

    def _reload(self):
        import categories
        importlib.reload(categories)   # re-run module-load default + override merge
        return categories


class NewDefaults(_Base):
    """A. The redesigned shipped defaults."""
    def test_yellow_is_fix(self):
        c = self._reload()
        self.assertEqual(c.CATEGORIES["yellow"]["tag"], "FIX")
        self.assertEqual(c.CATEGORIES["yellow"]["dot"], "🟡")

    def test_white_is_design_palette(self):
        # DESIGN occupies the white slot; colour now comes from the active theme.
        c = self._reload()
        self.assertEqual(c.CATEGORIES["white"]["tag"], "DESIGN")
        self.assertEqual(c.CATEGORIES["white"]["dot"], "🎨")
        self.assertEqual(c.CATEGORIES["white"]["label"], "design")
        self.assertIn("white", c.THEMES["sands"]["dark"])    # palette per variant
        self.assertIn("white", c.THEMES["sands"]["light"])

    def test_pink_is_personal_heart(self):
        c = self._reload()
        self.assertEqual(c.CATEGORIES["pink"]["tag"], "PERSONAL")
        self.assertEqual(c.CATEGORIES["pink"]["dot"], "🩷")
        self.assertEqual(c.CATEGORIES["pink"]["label"], "personal projects")

    def test_silver_is_tooling_disco(self):
        # TOOLING occupies the silver slot; colour now comes from the active theme.
        c = self._reload()
        self.assertEqual(c.CATEGORIES["silver"]["tag"], "TOOLING")
        self.assertEqual(c.CATEGORIES["silver"]["dot"], "🪩")
        self.assertEqual(c.CATEGORIES["silver"]["label"], "dev/AI tooling, config, env")
        self.assertIn("silver", c.THEMES["sands"]["dark"])
        self.assertIn("silver", c.THEMES["sands"]["light"])

    def test_resolve_new_tags(self):
        c = self._reload()
        self.assertEqual(c.resolve("TOOLING"), "silver")
        self.assertEqual(c.resolve("PERSONAL"), "pink")
        self.assertEqual(c.resolve("DESIGN"), "white")
        self.assertEqual(c.resolve("FIX"), "yellow")

    def test_palette_follows_swapped_slots(self):
        # The palette is supplied by the ACTIVE theme per SLOT KEY: white carries
        # DESIGN, silver carries TOOLING. tint_escape emits the slot's bg (OSC 11)
        # whether addressed by key or by the category's [TAG]/label.
        self._write_config({"tint_theme": "dark"})   # force the dark variant
        c = self._reload()
        white_bg = "\033]11;%s\007" % c.THEMES["sands"]["dark"]["white"]["bg"]
        silver_bg = "\033]11;%s\007" % c.THEMES["sands"]["dark"]["silver"]["bg"]
        self.assertIn(white_bg, c.tint_escape("white", "auto", "iterm"))
        self.assertIn(silver_bg, c.tint_escape("silver", "auto", "iterm"))
        self.assertIn(white_bg, c.tint_escape("DESIGN", "auto", "iterm"))
        self.assertIn(silver_bg, c.tint_escape("TOOLING", "auto", "iterm"))


class SkillColorRedirect(_Base):
    """A'. Claude-tooling skills tint TOOLING, which now lives on the silver slot."""
    def test_tooling_skill_tints_silver(self):
        c = self._reload()
        self.assertEqual(c.color_for_prompt("/update-config"), "silver")
        self.assertEqual(c.color_for_prompt("/keybindings-help"), "silver")

    def test_review_skill_still_orange(self):
        c = self._reload()
        self.assertEqual(c.color_for_prompt("/review"), "orange")


class SlotDeterminesEmoji(_Base):
    """B. The dot is a property of the slot; overrides need only {tag,label}."""
    def test_override_without_dot_inherits_slot_emoji(self):
        self._write_config({"categories": {"green": {"tag": "VOLT", "label": "volt work"}}})
        c = self._reload()
        self.assertEqual(c.CATEGORIES["green"]["tag"], "VOLT")
        self.assertEqual(c.CATEGORIES["green"]["dot"], "🟢")           # inherited slot dot

    def test_override_palette_comes_from_active_theme(self):
        # An override that sets only {tag,label} leaves colour to the active theme,
        # resolved by SLOT KEY — so tint_escape still emits green's full theme palette.
        self._write_config({"categories": {"green": {"tag": "VOLT", "label": "volt work"}},
                            "tint_theme": "dark"})
        c = self._reload()
        self.assertEqual(c.CATEGORIES["green"]["tag"], "VOLT")
        p = c.THEMES["sands"]["dark"]["green"]
        self.assertEqual(len(p["ansi"]), 16)
        out = c.tint_escape("green", "auto", "iterm")
        self.assertIn("\033]10;%s\007" % p["fg"], out)                          # fg from theme
        self.assertIn("\033]1337;SetColors=bold=%s\007" % p["bold"].lstrip("#"), out)

    def test_explicit_dot_still_honored(self):
        self._write_config({"categories": {"green": {"dot": "⚡", "tag": "VOLT", "label": "volt"}}})
        c = self._reload()
        self.assertEqual(c.CATEGORIES["green"]["dot"], "⚡")

    def test_user_volt_and_migration_overrides_win(self):
        self._write_config({"categories": {
            "green": {"tag": "VOLT", "label": "volt work"},
            "brown": {"tag": "MIGRATION", "label": "data migration"},
        }})
        c = self._reload()
        self.assertEqual(c.CATEGORIES["green"]["tag"], "VOLT")
        self.assertEqual(c.CATEGORIES["brown"]["tag"], "MIGRATION")
        self.assertEqual(c.CATEGORIES["brown"]["dot"], "🟤")           # slot dot retained
        self.assertEqual(c.resolve("VOLT"), "green")
        self.assertEqual(c.resolve("MIGRATION"), "brown")

    def test_brand_new_key_without_dot_falls_back_to_general(self):
        self._write_config({"categories": {"teal": {"tag": "TEAL", "label": "ops"}}})
        c = self._reload()
        self.assertIn("teal", c.CATEGORIES)
        self.assertEqual(c.CATEGORIES["teal"]["dot"], c.CATEGORIES[c.DEFAULT]["dot"])


class EnabledSet(_Base):
    """C. Lean, growable enabled set; GENERAL permanent."""
    def test_unconfigured_defaults_to_core(self):
        c = self._reload()
        self.assertEqual(set(c.enabled_keys()), set(c.CORE))
        self.assertEqual(set(c.CORE), {"red", "green", "black"})   # BUG · FEATURE · GENERAL
        # CORE is a strict subset — the full taxonomy is NOT all enabled by default.
        self.assertNotEqual(set(c.enabled_keys()), set(c.all_keys()))

    def test_general_always_enabled_even_if_omitted(self):
        self._write_config({"enabled_categories": ["red", "green"]})
        c = self._reload()
        self.assertIn("black", c.enabled_keys())

    def test_enabled_keys_in_canonical_order(self):
        self._write_config({"enabled_categories": ["green", "red"]})
        c = self._reload()
        # canonical order is red before green regardless of config order
        self.assertEqual(c.enabled_keys(), ["red", "green", "black"])

    def test_is_enabled(self):
        self._write_config({"enabled_categories": ["red"]})
        c = self._reload()
        self.assertTrue(c.is_enabled("red"))
        self.assertTrue(c.is_enabled("BUG"))
        self.assertTrue(c.is_enabled("black"))     # permanent
        self.assertFalse(c.is_enabled("green"))


class ConfigCommands(_Base):
    """D. config command surface: enable/disable toggles from the lean default."""
    def _reload_config(self):
        import config
        importlib.reload(config)
        return config

    def test_disable_general_refused(self):
        cfg = self._reload_config()
        c = self._reload()
        cfg.toggle_category("black", False)        # should refuse (permanent)
        self.assertIn("black", c.enabled_keys())
        cfg.toggle_category("GENERAL", False)      # via tag, also refused
        self.assertIn("black", c.enabled_keys())

    def test_disable_then_enable_noncore(self):
        cfg = self._reload_config()
        c = self._reload()
        cfg.toggle_category("green", False)         # green (FEATURE) is in CORE
        self.assertNotIn("green", c.enabled_keys())
        self.assertIn("black", c.enabled_keys())    # untouched + permanent
        cfg.toggle_category("FEATURE", True)        # re-enable via tag
        self.assertIn("green", c.enabled_keys())

    def test_disable_materializes_from_core_default(self):
        cfg = self._reload_config()
        c = self._reload()
        # unconfigured (CORE) → disabling one CORE slot materializes CORE-minus-one
        cfg.toggle_category("green", False)
        ek = c.enabled_keys()
        self.assertNotIn("green", ek)
        self.assertEqual(set(ek), {"red", "black"})

    def test_enable_grows_a_disabled_slot(self):
        cfg = self._reload_config()
        c = self._reload()
        self.assertNotIn("blue", c.enabled_keys())  # INFRA off by default
        cfg.toggle_category("INFRA", True)           # enable via tag
        self.assertIn("blue", c.enabled_keys())


class AutoEnableCategories(_Base):
    """F. auto_categories: assigning a disabled slot grows the board (default on);
    off freezes it. The enabled set is DISPLAY only — assignment may pick any slot."""
    def _reload_config(self):
        import config
        importlib.reload(config)
        return config

    def test_assign_disabled_category_auto_enables_when_on(self):
        cfg = self._reload_config()
        c = self._reload()
        self.assertTrue(cfg.auto_categories_enabled())   # default on
        self.assertNotIn("blue", c.enabled_keys())       # INFRA not on the lean board
        notice = c.auto_enable("INFRA")                  # assign a disabled slot
        self.assertIsNotNone(notice)
        self.assertIn("INFRA", notice)
        self.assertIn("blue", c.enabled_keys())          # board grew
        # persisted, canonical order, GENERAL still in.
        self.assertEqual(cfg.enabled_categories(), ["red", "green", "blue", "black"])

    def test_already_enabled_is_noop(self):
        c = self._reload()
        self.assertIsNone(c.auto_enable("red"))          # red (BUG) is in CORE
        self.assertIsNone(c.auto_enable("nonsense"))     # unknown → no-op

    def test_auto_off_does_not_auto_enable(self):
        self._write_config({"auto_categories": False})
        cfg = self._reload_config()
        c = self._reload()
        self.assertFalse(cfg.auto_categories_enabled())
        before = list(c.enabled_keys())
        self.assertIsNone(c.auto_enable("INFRA"))
        self.assertEqual(c.enabled_keys(), before)
        self.assertNotIn("blue", c.enabled_keys())

    def test_env_escape_disables_auto(self):
        os.environ["TASK_STATION_AUTO_CATEGORIES"] = "off"
        try:
            cfg = self._reload_config()
            c = self._reload()
            self.assertFalse(cfg.auto_categories_enabled())
            self.assertIsNone(c.auto_enable("INFRA"))
            self.assertNotIn("blue", c.enabled_keys())
        finally:
            os.environ.pop("TASK_STATION_AUTO_CATEGORIES", None)

    def test_toggle_and_getter(self):
        cfg = self._reload_config()
        self.assertTrue(cfg.auto_categories_enabled())   # default on
        cfg.set("auto_categories", False)
        self.assertFalse(cfg.auto_categories_enabled())  # what --auto-categories-get prints
        cfg.set("auto_categories", True)
        self.assertTrue(cfg.auto_categories_enabled())


class LegendRespectsEnabled(_Base):
    """E. legend scope. legend() is always enabled-scoped (the board); the picker /
    compact_legend present the FULL taxonomy when auto_categories is on."""
    def test_legend_limited_to_enabled(self):
        self._write_config({"enabled_categories": ["red", "black"]})
        c = self._reload()
        leg = c.legend()
        self.assertIn("BUG", leg)
        self.assertIn("GENERAL", leg)
        self.assertNotIn("FEATURE", leg)
        self.assertNotIn("INFRA", leg)

    def test_compact_legend_enabled_only_when_auto_off(self):
        self._write_config({"enabled_categories": ["red", "black"], "auto_categories": False})
        c = self._reload()
        comp = c.compact_legend()
        self.assertIn("red=", comp)
        self.assertNotIn("green=", comp)

    def test_compact_legend_full_taxonomy_when_auto_on(self):
        # auto on (default): the categoriser sees ALL slots even if disabled.
        self._write_config({"enabled_categories": ["red", "black"]})
        c = self._reload()
        comp = c.compact_legend()
        self.assertIn("green=", comp)        # not enabled, still shown
        self.assertIn("blue=", comp)
        picker = "\n".join(c.picker_lines())
        self.assertIn("INFRA", picker)       # full taxonomy in the picker guidance

    def test_full_taxonomy_legend_shows_all_assigned_categories(self):
        c = self._reload()
        leg = c.legend(c._all_items())       # full-taxonomy legend (categoriser view)
        self.assertIn("PERSONAL", leg)
        self.assertIn("DESIGN", leg)
        self.assertIn("DOCS", leg)           # gold is a real category (no longer reserved/hidden)
        self.assertNotIn("reserved", leg)    # the "reserved" concept is gone


if __name__ == "__main__":
    unittest.main()
