# tests/test_categories_overrides.py
import os, sys, json, tempfile, importlib, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

class Overrides(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        with open(os.path.join(self.tmp, "config.json"), "w") as f:
            json.dump({"tint_terminal": False,
                       "categories": {"teal": {"dot": "🟦", "tag": "TEAL", "label": "ops"}}},
                      f)

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)

    def test_user_override_merges_over_defaults(self):
        import categories
        importlib.reload(categories)            # re-run module-load merge
        self.assertIn("teal", categories.CATEGORIES)
        self.assertIn("red", categories.CATEGORIES)     # defaults still present
        self.assertFalse(categories.TINT_TERMINAL)

    def _write(self, obj):
        with open(os.path.join(self.tmp, "config.json"), "w") as f:
            json.dump(obj, f)

    def test_malformed_entry_is_skipped(self):
        import categories
        self._write({"categories": {"teal": "not-a-dict", "navy": {"dot": "X"}}})
        importlib.reload(categories)            # must not raise
        self.assertNotIn("teal", categories.CATEGORIES)   # bad entries skipped
        self.assertNotIn("navy", categories.CATEGORIES)
        self.assertIn("red", categories.CATEGORIES)        # defaults intact

    def test_broken_json_leaves_defaults(self):
        with open(os.path.join(self.tmp, "config.json"), "w") as f:
            f.write("{ this is not json")
        import categories
        importlib.reload(categories)            # must not raise
        self.assertIn("red", categories.CATEGORIES)

    def test_malformed_skill_colors_skipped_good_entries_apply(self):
        # A malformed skill_colors entry (not a 2-element [pattern, color] of
        # strings) must NOT poison SKILL_COLORS — previously a 1- or 3-element
        # entry slipped through and raised ValueError when color_for_prompt
        # unpacked it (outside the import-time guard). Bad entries are skipped;
        # well-formed ones still apply.
        import categories
        self._write({"skill_colors": [
            ["only-one"],                 # malformed: len 1
            ["myspecialcmd", "purple"],   # GOOD
            ["a", "b", "c"],              # malformed: len 3
            "not-a-list",                 # malformed: not a list
            [123, "blue"],                # malformed: non-str pattern
        ]})
        importlib.reload(categories)            # must not raise at import
        # The unpack site must not raise, and the good entry must win.
        self.assertEqual(categories.color_for_prompt("/myspecialcmd"), "purple")
        self.assertIsNone(categories.color_for_prompt("/unmapped-xyz-123"))

if __name__ == "__main__":
    unittest.main()
