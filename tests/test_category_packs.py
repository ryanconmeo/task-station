# tests/test_category_packs.py
"""Category packs (task #444, F8): the 12 dev categories become the DEV pack — one
pack among many (finance/hr/exec/general seeded, GENERAL permanent). Covers the pack
registry shape, the default=dev byte-comparability LAW, pack switching (board +
guidance render the active pack), overrides-beat-pack, the org category_packs.json
merge, and the `packs list` output."""
import os, sys, json, tempfile, shutil, importlib, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

# The pristine shipped DEV taxonomy — the pre-pack defaults, frozen here so the LAW
# test fails loudly if the default pack ever drifts from byte-comparable.
DEV_CATEGORIES = {
    "red":    {"dot": "🔴", "tag": "BUG",      "label": "bug"},
    "orange": {"dot": "🟠", "tag": "REVIEW",   "label": "code review"},
    "yellow": {"dot": "🟡", "tag": "FIX",      "label": "fixing PR review feedback"},
    "green":  {"dot": "🟢", "tag": "FEATURE",  "label": "feature work"},
    "blue":   {"dot": "🔵", "tag": "INFRA",    "label": "CI/CD, pipelines, cloud, deploy"},
    "purple": {"dot": "🟣", "tag": "RESEARCH", "label": "spikes / investigation"},
    "black":  {"dot": "⚫", "tag": "GENERAL",  "label": "general"},
    "pink":   {"dot": "🩷", "tag": "PERSONAL", "label": "personal projects"},
    "white":  {"dot": "🎨", "tag": "DESIGN",   "label": "design"},
    "silver": {"dot": "🪩", "tag": "TOOLING",  "label": "dev/AI tooling, config, env"},
    "gold":   {"dot": "📖", "tag": "DOCS",     "label": "documentation, writing"},
    "brown":  {"dot": "🟤", "tag": "DATA",     "label": "databases, schemas, ETL, migrations"},
}
DEV_GUIDE = {
    "red":    "Fixing a defect / broken behaviour.",
    "orange": "Reviewing someone else's code or a PR.",
    "yellow": "Addressing PR review feedback on your own PR.",
    "green":  "Feature / product coding.",
    "blue":   "CI/CD, pipelines, cloud, deploys, DNS, environment setup.",
    "purple": "Spikes / investigation: research, prototypes, one-off exploration.",
    "black":  "General / catch-all when nothing else fits (the permanent default).",
    "pink":   "Personal projects / side work.",
    "white":  "UI/UX, theming, layout, visual design.",
    "silver": "Dev/AI tooling, config & env: skills, slash commands, hooks, memory, this task-station.",
    "gold":   "Documentation & writing: READMEs, guides, changelogs.",
    "brown":  "Data work: databases, schemas, queries, SQL, ETL, and data migrations.",
}


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

    def _write_org_packs(self, obj):
        with open(os.path.join(self.tmp, "category_packs.json"), "w") as f:
            json.dump(obj, f)

    def _reload(self):
        import categories
        importlib.reload(categories)   # re-run module-load pack build + override merge
        return categories


class RegistryShape(_Base):
    """A. The pack registry is well-formed."""
    def test_seeded_packs_present(self):
        c = self._reload()
        for name in ("dev", "finance", "hr", "exec", "general"):
            self.assertIn(name, c.PACKS)
        self.assertEqual(c.DEFAULT_PACK, "dev")
        self.assertEqual(c.available_packs()[0], "dev")     # default first

    def test_every_pack_valid_and_general_permanent(self):
        c = self._reload()
        for name, entry in c.PACKS.items():
            self.assertTrue(c._valid_pack(entry), "pack %s invalid" % name)
            # GENERAL (black) is permanent — present in EVERY pack, as GENERAL.
            self.assertIn(c.PERMANENT, entry["slots"], "pack %s lacks GENERAL" % name)
            self.assertEqual(entry["slots"][c.PERMANENT]["tag"], "GENERAL")
            # 5-8 slots each is the brief's target; dev is the full 12.
            n = len(entry["slots"])
            if name == "dev":
                self.assertEqual(n, 12)
            else:
                self.assertTrue(3 <= n <= 8, "pack %s has %d slots" % (name, n))

    def test_every_pack_slot_has_a_palette(self):
        # Reuse the SAME colour/palette machinery: every pack slot keys off a colour
        # slot that the shipped `sands` theme paints in both variants.
        c = self._reload()
        for name, entry in c.PACKS.items():
            for key in entry["slots"]:
                self.assertIn(key, c.THEMES["sands"]["dark"],
                              "pack %s slot %s has no dark palette" % (name, key))
                self.assertIn(key, c.THEMES["sands"]["light"],
                              "pack %s slot %s has no light palette" % (name, key))

    def test_general_pack_is_lean_tri_slot(self):
        c = self._reload()
        self.assertEqual(len(c.PACKS["general"]["slots"]), 3)


class DefaultIsDevByteComparable(_Base):
    """B. The LAW: unconfigured (default pack) ⇒ byte-comparable to the pre-pack dev
    taxonomy. Nothing about the categories layer changes for an existing user."""
    def test_unconfigured_categories_equal_shipped_dev(self):
        c = self._reload()
        self.assertEqual(c.active_pack(), "dev")
        self.assertEqual(c.CATEGORIES, DEV_CATEGORIES)
        self.assertEqual(list(c.CATEGORIES), list(DEV_CATEGORIES))   # order too
        self.assertEqual(c.CATEGORY_GUIDE, DEV_GUIDE)
        self.assertEqual(c.CORE, ("red", "green", "black"))
        self.assertEqual(c.DEFAULT, "black")
        self.assertEqual(c.PERMANENT, "black")
        self.assertEqual(c.SLOT_DOTS, {k: v["dot"] for k, v in DEV_CATEGORIES.items()})

    def test_explicit_dev_equals_unconfigured(self):
        c = self._reload()
        base_cats, base_leg, base_comp = dict(c.CATEGORIES), c.legend(), c.compact_legend()
        self._write_config({"category_pack": "dev"})
        c = self._reload()
        self.assertEqual(c.CATEGORIES, base_cats)
        self.assertEqual(c.legend(), base_leg)
        self.assertEqual(c.compact_legend(), base_comp)

    def test_default_render_helpers_unchanged(self):
        c = self._reload()
        self.assertEqual(c.tag("red"), "🔴 [BUG]")
        self.assertEqual(c.summary("green"), "Category: 🟢 [FEATURE] feature work (green)")
        self.assertIn("🔴 [BUG]", c.legend(c._all_items()))


class PackSwitch(_Base):
    """C. Switching pack retargets the SAME slots — board + guidance render the new
    pack's tags so self-categorisation works for the discipline."""
    def test_finance_pack_retags_slots(self):
        self._write_config({"category_pack": "finance"})
        c = self._reload()
        self.assertEqual(c.active_pack(), "finance")
        self.assertEqual(c.CATEGORIES["red"]["tag"], "CLOSE")
        self.assertEqual(c.CATEGORIES["green"]["tag"], "REPORTING")
        self.assertEqual(c.CATEGORIES["black"]["tag"], "GENERAL")   # still permanent
        # colour/dot unchanged — same palette machinery
        self.assertEqual(c.CATEGORIES["red"]["dot"], "🔴")
        self.assertNotIn("yellow", c.CATEGORIES)                    # not a finance slot

    def test_finance_tags_render_in_legend_and_guidance(self):
        self._write_config({"category_pack": "finance"})
        c = self._reload()
        leg = c.legend(c._all_items())
        self.assertIn("CLOSE", leg)
        self.assertIn("REPORTING", leg)
        self.assertNotIn("BUG", leg)                                # dev tag gone
        comp = c.compact_legend()
        self.assertIn("CLOSE", comp)
        picker = "\n".join(c.picker_lines())
        self.assertIn("pack: finance", picker)                      # names the active pack
        self.assertIn("CLOSE", picker)

    def test_switch_changes_core_default_enabled_set(self):
        # general pack seeds a different CORE; unconfigured enabled set follows it.
        self._write_config({"category_pack": "general"})
        c = self._reload()
        self.assertEqual(set(c.CORE), {"green", "pink", "black"})
        self.assertEqual(set(c.enabled_keys()), {"green", "pink", "black"})

    def test_resolve_new_pack_tags(self):
        self._write_config({"category_pack": "hr"})
        c = self._reload()
        self.assertEqual(c.resolve("RECRUITING"), "green")
        self.assertEqual(c.resolve("[onboarding]"), "blue")
        self.assertEqual(c.normalize("nonsense"), "black")

    def test_unknown_pack_falls_back_to_dev(self):
        self._write_config({"category_pack": "does-not-exist"})
        c = self._reload()
        self.assertEqual(c.active_pack(), "dev")
        self.assertEqual(c.CATEGORIES, DEV_CATEGORIES)


class OverridesBeatPack(_Base):
    """D. Per-slot config overrides still win over the pack's slot."""
    def test_override_wins_over_pack_slot(self):
        self._write_config({"category_pack": "finance",
                            "categories": {"red": {"tag": "URGENT", "label": "urgent close"}}})
        c = self._reload()
        self.assertEqual(c.CATEGORIES["red"]["tag"], "URGENT")
        self.assertEqual(c.CATEGORIES["red"]["label"], "urgent close")
        self.assertEqual(c.CATEGORIES["red"]["dot"], "🔴")          # dot inherited
        # a non-overridden finance slot is untouched
        self.assertEqual(c.CATEGORIES["green"]["tag"], "REPORTING")

    def test_default_tag_label_reflects_pack_default(self):
        self._write_config({"category_pack": "finance",
                            "categories": {"red": {"tag": "URGENT", "label": "urgent close"}}})
        c = self._reload()
        d = c.default_tag_label("red")
        self.assertEqual(d["tag"], "CLOSE")                         # the finance default
        self.assertEqual(d["label"], "period-end close")


class OrgPackMerge(_Base):
    """E. An org-supplied category_packs.json can add a pack or override slots — same
    merge discipline as the categories override map."""
    def test_org_file_adds_new_pack(self):
        self._write_org_packs({"legal": {
            "label": "Legal", "description": "Legal ops.",
            "core": ["red", "black"],
            "slots": {
                "red":   {"tag": "CONTRACTS", "label": "contracts"},
                "green": {"tag": "COMPLIANCE", "label": "compliance"},
                "black": {"tag": "GENERAL", "label": "general"},
            }}})
        self._write_config({"category_pack": "legal"})
        c = self._reload()
        self.assertIn("legal", c.effective_packs())
        self.assertIn("legal", c.available_packs())
        self.assertEqual(c.active_pack(), "legal")
        self.assertEqual(c.CATEGORIES["red"]["tag"], "CONTRACTS")

    def test_org_file_overrides_shipped_pack_slot(self):
        self._write_org_packs({"finance": {"slots": {
            "red": {"tag": "HARDCLOSE", "label": "hard close"}}}})
        self._write_config({"category_pack": "finance"})
        c = self._reload()
        self.assertEqual(c.CATEGORIES["red"]["tag"], "HARDCLOSE")
        self.assertEqual(c.CATEGORIES["green"]["tag"], "REPORTING")  # other slots intact

    def test_org_file_nested_under_packs_key(self):
        self._write_org_packs({"packs": {"ops": {
            "slots": {"blue": {"tag": "INCIDENT", "label": "incident"},
                      "black": {"tag": "GENERAL", "label": "general"}}}}})
        self._write_config({"category_pack": "ops"})
        c = self._reload()
        self.assertEqual(c.active_pack(), "ops")
        self.assertEqual(c.CATEGORIES["blue"]["tag"], "INCIDENT")

    def test_malformed_org_pack_is_ignored(self):
        # No GENERAL slot ⇒ invalid ⇒ dropped; shipped packs still work.
        self._write_org_packs({"broken": {"slots": {"red": {"tag": "X", "label": "y"}}}})
        c = self._reload()
        self.assertNotIn("broken", c.effective_packs())
        self.assertIn("finance", c.effective_packs())

    def test_broken_json_leaves_shipped_packs(self):
        with open(os.path.join(self.tmp, "category_packs.json"), "w") as f:
            f.write("{ not json")
        c = self._reload()                          # must not raise
        self.assertEqual(set(c.effective_packs()),
                         {"dev", "finance", "hr", "exec", "general"})


class PacksListOutput(_Base):
    """F. `config --category-pack list` enumerates packs, marking the active one."""
    def _capture(self, fn, *a):
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fn(*a)
        return buf.getvalue()

    def test_list_marks_active_and_lists_all(self):
        self._write_config({"category_pack": "finance"})
        self._reload()
        import config
        importlib.reload(config)
        out = self._capture(config.cmd_category_pack, [])
        self.assertIn("finance", out)
        self.assertIn("dev", out)
        self.assertIn("* finance", out)             # active marker
        self.assertIn("Finance", out)               # display label

    def test_select_valid_pack_persists(self):
        self._reload()
        import config
        importlib.reload(config)
        self._capture(config.cmd_category_pack, ["hr"])
        self.assertEqual(config.get("category_pack"), "hr")
        self.assertEqual(config.category_pack(), "hr")

    def test_select_unknown_pack_does_not_persist(self):
        self._reload()
        import config
        importlib.reload(config)
        out = self._capture(config.cmd_category_pack, ["nope"])
        self.assertIn("Unknown pack", out)
        self.assertIsNone(config.get("category_pack"))


if __name__ == "__main__":
    unittest.main()
