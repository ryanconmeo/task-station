"""Task 1: artifacts-root config accessor + brief output-path derivation.

`config.artifacts_root()` must DERIVE from the data_dir seam (never a hardcoded
~/ path); precedence env > config key > derived default (<data_dir>/artifacts).
Plus the deterministic slug / project-slug / task-slug helpers and
brief_output_path(task) = <root>/<project>/<seq>-<slug>/brief.html. Stdlib-only."""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)
import config  # noqa: E402
import paths   # noqa: E402


class ArtifactsRootTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        os.environ.pop("TASK_STATION_ARTIFACTS_ROOT", None)

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        os.environ.pop("TASK_STATION_ARTIFACTS_ROOT", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_derives_from_data_dir(self):
        root = config.artifacts_root()
        self.assertEqual(root, os.path.join(paths.data_dir(), "artifacts"))
        # Derives from the (temp) data home — proves it is NOT a hardcoded ~/ path.
        self.assertTrue(root.startswith(self.tmp))

    def test_env_wins(self):
        os.environ["TASK_STATION_ARTIFACTS_ROOT"] = "/tmp/xyz-artifacts"
        self.assertEqual(config.artifacts_root(), "/tmp/xyz-artifacts")

    def test_env_expands_user(self):
        os.environ["TASK_STATION_ARTIFACTS_ROOT"] = "~/env-arts"
        self.assertEqual(config.artifacts_root(), os.path.expanduser("~/env-arts"))

    def test_config_key_overrides_default(self):
        config.set("artifacts_root", "~/custom-arts")
        self.assertEqual(config.artifacts_root(), os.path.expanduser("~/custom-arts"))

    def test_env_beats_config(self):
        config.set("artifacts_root", "/from/config")
        os.environ["TASK_STATION_ARTIFACTS_ROOT"] = "/from/env"
        self.assertEqual(config.artifacts_root(), "/from/env")


class BriefPathHelpersTest(unittest.TestCase):
    """`_project_slug` reads the ACTIVE taxonomy, so these tests must pin it.

    Unlike `config` (which re-reads config.json on every get), `categories` merges
    the user's overrides over the shipped taxonomy ONCE at import time and caches
    the result. This module imports task-station/config — and transitively
    categories — before setUp can redirect TASK_STATION_HOME, so without an
    explicit reload the assertions below would read the DEVELOPER'S real
    ~/.task-station/config.json: green in and green out on a clean machine, but a
    hard failure on any machine whose config renames a slot. Rebuild the taxonomy
    against the temp home instead, so every assertion is against SHIPPED defaults.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        os.environ.pop("TASK_STATION_ARTIFACTS_ROOT", None)
        self._reload_categories()

    def tearDown(self):
        # Reload while the temp home is still active AND config-free, so the module
        # is left on shipped defaults for whatever runs next in this process.
        # (Popping the env var first would rebind it to the developer's real config.)
        cfg = os.path.join(self.tmp, "config.json")
        if os.path.exists(cfg):
            os.remove(cfg)
        self._reload_categories()
        os.environ.pop("TASK_STATION_HOME", None)
        os.environ.pop("TASK_STATION_ARTIFACTS_ROOT", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def _reload_categories():
        import categories
        importlib.reload(categories)

    def test_slug_normalizes(self):
        self.assertEqual(ts._slug("LEGACY Key: Case-Sensitivity!"), "legacy-key-case-sensitivity")

    def test_slug_collapses_and_strips(self):
        self.assertEqual(ts._slug("  Hello   World  "), "hello-world")
        self.assertEqual(ts._slug("--Edge--"), "edge")

    def test_slug_empty(self):
        self.assertEqual(ts._slug(""), "")
        self.assertEqual(ts._slug(None), "")

    def test_project_slug_is_the_active_category_tag(self):
        # The folder is the task's resolved category [TAG], slugified — nothing about
        # it is hardcoded, so it tracks the taxonomy (dev pack: green FEATURE,
        # silver TOOLING, brown DATA, blue INFRA, purple RESEARCH).
        self.assertEqual(ts._project_slug({"color": "green"}), "feature")
        self.assertEqual(ts._project_slug({"color": "silver"}), "tooling")
        self.assertEqual(ts._project_slug({"color": "brown"}), "data")
        self.assertEqual(ts._project_slug({"color": "blue"}), "infra")
        self.assertEqual(ts._project_slug({"color": "purple"}), "research")

    def test_project_slug_accepts_any_category_alias(self):
        # a [TAG] / label / emoji names the same category as its key does
        self.assertEqual(ts._project_slug({"color": "FEATURE"}), "feature")
        self.assertEqual(ts._project_slug({"color": "TOOLING"}), "tooling")
        self.assertEqual(ts._project_slug({"color": "feature work"}), "feature")   # label
        self.assertEqual(ts._project_slug({"color": "🟢"}), "feature")             # emoji dot

    def test_project_slug_follows_a_user_override(self):
        # A per-slot override in config.json's `categories` key must reach the folder:
        # the tag the user chose is the folder they get, from THEIR config — never
        # from a map in our source.
        # (tearDown drops the config and restores the shipped taxonomy.)
        import categories
        cfg_path = os.path.join(self.tmp, "config.json")
        with open(cfg_path, "w") as f:
            json.dump({"categories": {"green": {"tag": "PROJECT", "label": "project work"}}}, f)
        importlib.reload(categories)
        self.assertEqual(categories.CATEGORIES["green"]["tag"], "PROJECT")
        self.assertEqual(ts._project_slug({"color": "green"}), "project")
        # the override is slugified like any other tag (spaces/punctuation → "-")
        with open(cfg_path, "w") as f:
            json.dump({"categories": {"green": {"tag": "My Project!", "label": "x"}}}, f)
        importlib.reload(categories)
        self.assertEqual(ts._project_slug({"color": "green"}), "my-project")

    def test_project_slug_unknown_color_falls_back_to_slug(self):
        # "teal" names no category in the dev pack ⇒ the colour itself is the folder
        self.assertEqual(ts._project_slug({"color": "teal"}), "teal")

    def test_project_slug_no_color_is_general(self):
        self.assertEqual(ts._project_slug({}), "general")

    def test_task_slug(self):
        t = {"seq": 42, "title": "LEGACY Key: Case-Sensitivity!", "id": "abcd1234ef"}
        self.assertEqual(ts._task_slug(t), "42-legacy-key-case-sensitivity")

    def test_task_slug_no_seq_uses_id_prefix(self):
        self.assertEqual(ts._task_slug({"title": "Hi", "id": "abcd1234ef99"}), "abcd1234-hi")

    def test_brief_output_path(self):
        task = {"seq": 7, "title": "Collation Strategy", "color": "green", "id": "deadbeef00"}
        self.assertEqual(
            ts.brief_output_path(task),
            os.path.join(self.tmp, "artifacts", "feature", "7-collation-strategy", "brief.html"))


if __name__ == "__main__":
    unittest.main()
