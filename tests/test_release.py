"""Tests for scripts/release.py — the single-source version bumper.

All tests run against a synthesized TEMP fixture repo; the real repo files are
never mutated.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import release as _release  # noqa: E402


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _fixture(root, version="1.0.0", date="2026-01-01"):
    """Synthesize the 4 version-bearing files in `root`, all at `version`."""
    plugin = {
        "name": "task-station",
        "description": "desc",
        "version": version,
        "author": {"name": "Ryan Nguyen", "url": "https://x"},
        "homepage": "https://h",
        "license": "MIT",
        "keywords": ["a", "b"],
    }
    _write(os.path.join(root, ".claude-plugin", "plugin.json"),
           json.dumps(plugin, indent=2) + "\n")
    marketplace = {
        "name": "ryanconmeo",
        "plugins": [{"name": "task-station", "source": "./",
                     "version": version, "license": "MIT"}],
    }
    _write(os.path.join(root, ".claude-plugin", "marketplace.json"),
           json.dumps(marketplace, indent=2) + "\n")
    _write(os.path.join(root, "README.md"),
           '<p>\n  <img alt="version" '
           'src="https://img.shields.io/badge/version-%s-blue">\n</p>\n' % version)
    _write(os.path.join(root, "CHANGELOG.md"),
           "# Changelog\n\nIntro paragraph about semver.\n\n"
           "## [%s] — %s\n\n### Fixed\n- something\n" % (version, date))


class SemverTest(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(_release.parse_semver("1.2.3"), (1, 2, 3))

    def test_invalid_raises(self):
        for bad in ("1.2", "x.y.z", "1.2.3.4", "1.2.-1", "v1.2.3", "1.2.3-rc"):
            with self.assertRaises(ValueError):
                _release.parse_semver(bad)


class SetVersionTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        _fixture(self.root)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def _plugin(self):
        with open(os.path.join(self.root, ".claude-plugin", "plugin.json")) as f:
            return json.load(f)

    def _marketplace(self):
        with open(os.path.join(self.root, ".claude-plugin", "marketplace.json")) as f:
            return json.load(f)

    def _readme(self):
        with open(os.path.join(self.root, "README.md")) as f:
            return f.read()

    def _changelog(self):
        with open(os.path.join(self.root, "CHANGELOG.md")) as f:
            return f.read()

    def test_set_rewrites_all_four(self):
        _release.set_version(self.root, "2.3.4", "2026-02-02")
        self.assertEqual(self._plugin()["version"], "2.3.4")
        self.assertEqual(self._marketplace()["plugins"][0]["version"], "2.3.4")
        self.assertIn("version-2.3.4-blue", self._readme())
        self.assertNotIn("version-1.0.0-blue", self._readme())
        self.assertIn("## [2.3.4] — 2026-02-02", self._changelog())

    def test_changelog_entry_has_placeholder_sections(self):
        _release.set_version(self.root, "2.3.4", "2026-02-02")
        cl = self._changelog()
        head = cl.index("## [2.3.4]")
        # the new entry precedes the old one and carries scaffold section headers
        self.assertLess(head, cl.index("## [1.0.0]"))
        entry = cl[head:cl.index("## [1.0.0]")]
        self.assertIn("### ", entry)

    def test_changelog_idempotent_on_rerun(self):
        _release.set_version(self.root, "2.3.4", "2026-02-02")
        _release.set_version(self.root, "2.3.4", "2026-02-02")
        self.assertEqual(self._changelog().count("## [2.3.4]"), 1)

    def test_json_stays_valid_and_two_space_indent(self):
        _release.set_version(self.root, "2.3.4", "2026-02-02")
        self._plugin()          # json.load → raises if invalid
        self._marketplace()
        with open(os.path.join(self.root, ".claude-plugin", "plugin.json")) as f:
            text = f.read()
        self.assertIn('\n  "version": "2.3.4"', text)   # 2-space indent preserved
        self.assertTrue(text.endswith("\n"))            # trailing newline

    def test_json_key_order_preserved(self):
        # targeted edit must not reorder keys (version stays between description
        # and author, keywords stays last).
        _release.set_version(self.root, "2.3.4", "2026-02-02")
        with open(os.path.join(self.root, ".claude-plugin", "plugin.json")) as f:
            text = f.read()
        self.assertLess(text.index('"description"'), text.index('"version"'))
        self.assertLess(text.index('"version"'), text.index('"author"'))

    def test_bad_semver_raises(self):
        with self.assertRaises(ValueError):
            _release.set_version(self.root, "1.2", "2026-02-02")

    def test_default_date_is_today(self):
        import datetime
        _release.set_version(self.root, "2.3.4")
        self.assertIn("## [2.3.4] — %s" % datetime.date.today().isoformat(),
                      self._changelog())


class BumpVersionTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        _fixture(self.root, version="1.4.9")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def _ver(self):
        with open(os.path.join(self.root, ".claude-plugin", "plugin.json")) as f:
            return json.load(f)["version"]

    def test_patch(self):
        _release.bump_version(self.root, "patch", "2026-02-02")
        self.assertEqual(self._ver(), "1.4.10")

    def test_minor(self):
        _release.bump_version(self.root, "minor", "2026-02-02")
        self.assertEqual(self._ver(), "1.5.0")

    def test_major(self):
        _release.bump_version(self.root, "major", "2026-02-02")
        self.assertEqual(self._ver(), "2.0.0")


class ValidateTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        _fixture(self.root, version="1.0.0")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def test_consistent_fixture_validates(self):
        self.assertEqual(_release.validate(self.root), "1.0.0")

    def test_readme_drift_detected(self):
        p = os.path.join(self.root, "README.md")
        with open(p, "w") as f:
            f.write("version-9.9.9-blue\n")
        with self.assertRaises(Exception):
            _release.validate(self.root)

    def test_marketplace_drift_detected(self):
        p = os.path.join(self.root, ".claude-plugin", "marketplace.json")
        d = json.load(open(p))
        d["plugins"][0]["version"] = "0.0.1"
        json.dump(d, open(p, "w"), indent=2)
        with self.assertRaises(Exception):
            _release.validate(self.root)


class MainCliTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        _fixture(self.root, version="1.0.0")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def _ver(self):
        with open(os.path.join(self.root, ".claude-plugin", "plugin.json")) as f:
            return json.load(f)["version"]

    def test_main_set(self):
        rc = _release.main(["--set", "3.0.0", "--date", "2026-03-03", "--root", self.root])
        self.assertEqual(rc, 0)
        self.assertEqual(self._ver(), "3.0.0")

    def test_main_bump(self):
        _release.main(["--bump", "minor", "--date", "2026-03-03", "--root", self.root])
        self.assertEqual(self._ver(), "1.1.0")

    def test_main_bad_semver_nonzero(self):
        rc = _release.main(["--set", "nope", "--root", self.root])
        self.assertNotEqual(rc, 0)

    def test_main_requires_set_or_bump(self):
        with self.assertRaises(SystemExit):
            _release.main(["--root", self.root])


if __name__ == "__main__":
    unittest.main()
