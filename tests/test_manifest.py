"""Guardrail: plugin.json and marketplace.json must stay in sync.

Catches version drift (marketplace.json drifted to 1.9.1 while plugin.json
was at 1.48.0) and validates required plugin.json fields are non-empty.
"""
import json
import os
import re
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PLUGIN_JSON = os.path.join(_REPO_ROOT, ".claude-plugin", "plugin.json")
_MARKETPLACE_JSON = os.path.join(_REPO_ROOT, ".claude-plugin", "marketplace.json")
_README = os.path.join(_REPO_ROOT, "README.md")
_CHANGELOG = os.path.join(_REPO_ROOT, "CHANGELOG.md")


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class TestManifestConsistency(unittest.TestCase):
    def setUp(self):
        self.plugin = _load(_PLUGIN_JSON)
        self.marketplace = _load(_MARKETPLACE_JSON)
        self.mp_plugin = self.marketplace["plugins"][0]

    def test_versions_match(self):
        self.assertEqual(
            self.mp_plugin["version"],
            self.plugin["version"],
            "marketplace.json plugins[0].version must equal plugin.json version",
        )

    def test_names_match(self):
        self.assertEqual(
            self.mp_plugin["name"],
            self.plugin["name"],
            "marketplace.json plugins[0].name must equal plugin.json name",
        )

    def test_plugin_json_required_fields(self):
        for field in ("description", "homepage", "license"):
            self.assertTrue(
                self.plugin.get(field),
                "plugin.json '%s' must be non-empty" % field,
            )

    def test_both_files_valid_json(self):
        # Loading in setUp already validates JSON; these just confirm the keys exist.
        self.assertIn("version", self.plugin)
        self.assertIn("plugins", self.marketplace)

    def test_readme_badge_matches_version(self):
        """README shields.io version badge must equal plugin.json version
        (it silently drifted to 1.59.0 while the plugin was 1.81.0)."""
        m = re.search(r"version-(\d+\.\d+\.\d+)-blue", _read(_README))
        self.assertIsNotNone(m, "README.md has no shields version-X.Y.Z-blue badge")
        self.assertEqual(
            m.group(1), self.plugin["version"],
            "README version badge must equal plugin.json version — "
            "run scripts/release.py to sync all four files",
        )

    def test_changelog_has_current_version_entry(self):
        """CHANGELOG.md must have a '## [<current version>]' heading."""
        self.assertIn(
            "## [%s]" % self.plugin["version"], _read(_CHANGELOG),
            "CHANGELOG.md has no '## [%s]' entry for the current version"
            % self.plugin["version"],
        )
