# tests/test_categories_overrides.py
import os, sys, json, tempfile, importlib, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

class Overrides(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["CLAUDE_TODO_HOME"] = self.tmp
        with open(os.path.join(self.tmp, "categories.json"), "w") as f:
            json.dump({"tint_terminal": False,
                       "categories": {"teal": {"dot": "🟦", "tag": "TEAL", "label": "ops"}}},
                      f)

    def tearDown(self):
        os.environ.pop("CLAUDE_TODO_HOME", None)

    def test_user_override_merges_over_defaults(self):
        import categories
        importlib.reload(categories)            # re-run module-load merge
        self.assertIn("teal", categories.CATEGORIES)
        self.assertIn("red", categories.CATEGORIES)     # defaults still present
        self.assertFalse(categories.TINT_TERMINAL)

    def _write(self, obj):
        with open(os.path.join(self.tmp, "categories.json"), "w") as f:
            json.dump(obj, f)

    def test_malformed_entry_is_skipped(self):
        import categories
        self._write({"categories": {"teal": "not-a-dict", "navy": {"dot": "X"}}})
        importlib.reload(categories)            # must not raise
        self.assertNotIn("teal", categories.CATEGORIES)   # bad entries skipped
        self.assertNotIn("navy", categories.CATEGORIES)
        self.assertIn("red", categories.CATEGORIES)        # defaults intact

    def test_broken_json_leaves_defaults(self):
        with open(os.path.join(self.tmp, "categories.json"), "w") as f:
            f.write("{ this is not json")
        import categories
        importlib.reload(categories)            # must not raise
        self.assertIn("red", categories.CATEGORIES)

if __name__ == "__main__":
    unittest.main()
