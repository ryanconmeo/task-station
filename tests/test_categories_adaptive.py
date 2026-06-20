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
        # DESIGN now occupies the white slot → White Sands profile, white hex kept.
        c = self._reload()
        self.assertEqual(c.CATEGORIES["white"]["tag"], "DESIGN")
        self.assertEqual(c.CATEGORIES["white"]["dot"], "🎨")
        self.assertEqual(c.CATEGORIES["white"]["label"], "design")
        self.assertEqual(c.CATEGORIES["white"]["hex"], "#202024")        # hex unchanged
        self.assertEqual(c.CATEGORIES["white"]["hex_light"], "#f2f2f5")  # hex unchanged

    def test_pink_is_personal_heart(self):
        c = self._reload()
        self.assertEqual(c.CATEGORIES["pink"]["tag"], "PERSONAL")
        self.assertEqual(c.CATEGORIES["pink"]["dot"], "🩷")
        self.assertEqual(c.CATEGORIES["pink"]["label"], "personal projects")

    def test_silver_is_ai_config_disco(self):
        # AI CONFIG now occupies the silver slot → Silver Sands profile, silver hex kept.
        c = self._reload()
        self.assertEqual(c.CATEGORIES["silver"]["tag"], "AI CONFIG")
        self.assertEqual(c.CATEGORIES["silver"]["dot"], "🪩")
        self.assertEqual(c.CATEGORIES["silver"]["label"], "AI tooling & config")
        self.assertEqual(c.CATEGORIES["silver"]["hex"], "#303033")        # hex unchanged
        self.assertEqual(c.CATEGORIES["silver"]["hex_light"], "#eeeef0")  # hex unchanged

    def test_resolve_new_tags(self):
        c = self._reload()
        self.assertEqual(c.resolve("AI CONFIG"), "silver")
        self.assertEqual(c.resolve("PERSONAL"), "pink")
        self.assertEqual(c.resolve("DESIGN"), "white")
        self.assertEqual(c.resolve("FIX"), "yellow")

    def test_tint_command_follows_swapped_slots(self):
        # tint_command logic is unchanged: it emits `zsh -ic '<key>'`. Since the
        # categories moved slots, white→White Sands now carries DESIGN and
        # silver→Silver Sands now carries AI CONFIG.
        c = self._reload()
        c._sys.platform = "darwin"; c.TINT_TERMINAL = True   # pin the tint gate
        self.assertEqual(c.tint_command("white"), "zsh -ic 'white'")    # DESIGN / White Sands
        self.assertEqual(c.tint_command("silver"), "zsh -ic 'silver'")  # AI CONFIG / Silver Sands
        # resolving by the category's tag lands on the swapped key, then the alias
        self.assertEqual(c.tint_command("DESIGN"), "zsh -ic 'white'")
        self.assertEqual(c.tint_command("AI CONFIG"), "zsh -ic 'silver'")


class SkillColorRedirect(_Base):
    """A'. Claude-tooling skills tint AI CONFIG, which now lives on the silver slot."""
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
        self.assertEqual(c.CATEGORIES["green"]["hex"], "#233a2b")      # slot hex kept

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
    """C. Seeded-but-removable enabled set; GENERAL permanent."""
    def test_unconfigured_defaults_to_full_set(self):
        c = self._reload()
        self.assertEqual(set(c.enabled_keys()), set(c.all_keys()))

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


class Presets(_Base):
    """D. Presets, with the universal core in every one."""
    CORE = {"red", "silver", "pink", "black"}   # AI CONFIG now on the silver slot

    def test_minimal_is_core_only(self):
        c = self._reload()
        self.assertEqual(set(c.preset_keys("minimal")), self.CORE)

    def test_full_is_all_twelve(self):
        c = self._reload()
        self.assertEqual(set(c.preset_keys("full")), set(c.all_keys()))
        self.assertEqual(len(c.preset_keys("full")), 12)

    def test_every_preset_contains_core(self):
        c = self._reload()
        for name in c.PRESETS:
            self.assertTrue(self.CORE <= set(c.preset_keys(name)),
                            "%s missing core" % name)

    def test_every_preset_contains_general(self):
        c = self._reload()
        for name in c.PRESETS:
            self.assertIn("black", c.preset_keys(name))

    def test_web_preset_contents(self):
        c = self._reload()
        self.assertEqual(set(c.preset_keys("web")),
                         self.CORE | {"green", "white", "blue", "orange", "yellow"})
        # web still contains both AI CONFIG (silver, via core) and DESIGN (white)
        self.assertIn("silver", c.preset_keys("web"))
        self.assertIn("white", c.preset_keys("web"))

    def test_data_preset_contents(self):
        c = self._reload()
        self.assertEqual(set(c.preset_keys("data")),
                         self.CORE | {"brown", "green", "blue", "orange"})

    def test_ops_preset_contents(self):
        c = self._reload()
        self.assertEqual(set(c.preset_keys("ops")),
                         self.CORE | {"blue", "brown", "orange", "yellow", "purple"})

    def test_unknown_preset_is_none(self):
        c = self._reload()
        self.assertIsNone(c.preset_keys("nope"))


class ConfigCommands(_Base):
    """D. config command surface: preset apply + enable/disable toggles."""
    def _args(self, **kw):
        kw.setdefault("categories", None)
        kw.setdefault("enable", None)
        kw.setdefault("disable", None)
        return type("A", (), kw)()

    def _reload_config(self):
        import config
        importlib.reload(config)
        return config

    def test_preset_apply_persists_enabled_set(self):
        cfg = self._reload_config()
        cfg.cmd_categories(["preset", "minimal"])
        c = self._reload()
        self.assertEqual(set(c.enabled_keys()), {"red", "silver", "pink", "black"})

    def test_disable_general_refused(self):
        cfg = self._reload_config()
        c = self._reload()
        cfg.cmd_categories(["preset", "full"])
        cfg.toggle_category("black", False)        # should refuse
        self.assertIn("black", c.enabled_keys())
        cfg.toggle_category("GENERAL", False)      # via tag, also refused
        self.assertIn("black", c.enabled_keys())

    def test_disable_then_enable_noncore(self):
        cfg = self._reload_config()
        c = self._reload()
        cfg.cmd_categories(["preset", "full"])
        cfg.toggle_category("green", False)
        self.assertNotIn("green", c.enabled_keys())
        self.assertIn("black", c.enabled_keys())   # untouched + permanent
        cfg.toggle_category("FEATURE", True)        # re-enable via tag
        self.assertIn("green", c.enabled_keys())

    def test_enable_materializes_from_full_default(self):
        cfg = self._reload_config()
        c = self._reload()
        # unconfigured (full) → disabling one slot materializes full-minus-one
        cfg.toggle_category("purple", False)
        ek = c.enabled_keys()
        self.assertNotIn("purple", ek)
        self.assertEqual(len(ek), 11)


class LegendRespectsEnabled(_Base):
    """E. legend / compact_legend / picker only show enabled categories."""
    def test_legend_limited_to_enabled(self):
        self._write_config({"enabled_categories": ["red", "black"]})
        c = self._reload()
        leg = c.legend()
        self.assertIn("BUG", leg)
        self.assertIn("GENERAL", leg)
        self.assertNotIn("FEATURE", leg)
        self.assertNotIn("DEVOPS", leg)

    def test_compact_legend_limited_to_enabled(self):
        self._write_config({"enabled_categories": ["red", "black"]})
        c = self._reload()
        comp = c.compact_legend()
        self.assertIn("red=", comp)
        self.assertNotIn("green=", comp)

    def test_full_legend_shows_assigned_not_reserved(self):
        c = self._reload()
        leg = c.legend()
        self.assertIn("PERSONAL", leg)
        self.assertIn("DESIGN", leg)
        self.assertNotIn("reserved", leg)   # gold is reserved


if __name__ == "__main__":
    unittest.main()
