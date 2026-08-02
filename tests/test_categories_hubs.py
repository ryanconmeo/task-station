# tests/test_categories_hubs.py
"""Category-hub taxonomy helpers (WS10): hub_slug / hub_meta — the join between a
task note's `[[categories/<slug>]]` link and its generated hub page. Slug is the
[TAG] lowercased (black→general, red→bug), so the default/uncategorised slug is
`general` (matching the shipped GENERAL tag), and every slug is a resolvable,
filesystem-safe token."""
import importlib, os, shutil, sys, tempfile, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

import categories  # noqa: E402


class _Clean(unittest.TestCase):
    """Reload categories under an empty temp HOME so the shipped taxonomy (not the
    developer's own config.json overrides) is under test."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cat-hubs-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        importlib.reload(categories)

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)
        importlib.reload(categories)


class HubSlug(_Clean):
    def test_slug_is_tag_lowercased(self):
        self.assertEqual(categories.hub_slug("red"), "bug")
        self.assertEqual(categories.hub_slug("green"), "feature")
        self.assertEqual(categories.hub_slug("black"), "general")

    def test_slug_resolves_aliases(self):
        # any of key / emoji / [TAG] / label maps to the same slug
        self.assertEqual(categories.hub_slug("🔴"), "bug")
        self.assertEqual(categories.hub_slug("[BUG]"), "bug")
        self.assertEqual(categories.hub_slug("bug"), "bug")

    def test_unknown_or_empty_falls_back_to_default(self):
        # DEFAULT is black → GENERAL → general
        self.assertEqual(categories.hub_slug(""), "general")
        self.assertEqual(categories.hub_slug(None), "general")
        self.assertEqual(categories.hub_slug("not-a-color"), "general")

    def test_slug_is_filesystem_safe(self):
        for key in categories.all_keys():
            s = categories.hub_slug(key)
            self.assertTrue(s)
            self.assertRegex(s, r"^[a-z0-9-]+$")


class HubMeta(_Clean):
    def test_meta_carries_key_slug_label_tag_description(self):
        m = categories.hub_meta("black")
        self.assertEqual(m["key"], "black")
        self.assertEqual(m["slug"], "general")
        self.assertEqual(m["tag"], "GENERAL")
        self.assertEqual(m["label"], "general")
        self.assertEqual(m["dot"], "⚫")
        self.assertTrue(m["description"])   # the "when to use" guide sentence

    def test_meta_default_for_unknown(self):
        m = categories.hub_meta("")
        self.assertEqual(m["key"], "black")
        self.assertEqual(m["slug"], "general")


if __name__ == "__main__":
    unittest.main()
