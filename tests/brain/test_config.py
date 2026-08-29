"""brain.config — per-key env overrides, precedence, boolean parsing,
malformed-JSON read-vs-write behaviour, and memory discovery.

PROVENANCE: ported from the source's ``tests/test_pb_config.py`` @ 0.14.0 (29
cases). Rewrites applied, all recorded in the chunk-1 handoff:
  * module ``pb_config`` -> ``brain.config``; the source's org-branded env prefix
    -> ``TASK_STATION_BRAIN_``; primary config filename genericized (both old
    spellings are in the handoff, not here — this file must stay scrub-clean).
  * ``test_default_tasks_db_and_state_under_dot_claude`` became
    ``…_under_data_home``: those two defaults now resolve through
    ``core.paths.data_dir()`` instead of a literal ``~/.claude/…``. The tasks_db
    expectation is byte-for-byte the source's; state_dir moved from
    ``~/.claude/<brand>-state`` to ``<data home>/brain-state``.
  * keyword fixtures genericized (they were org product names).
ADDED (marked): the data-home relocation behaviour, the config-filename lock, and
two namespace/fixture-drift guards.
"""
import io
import os
import unittest
from contextlib import redirect_stderr

from tests.brain import base
from tests.brain.base import BrainTestCase

import brain.config as bconfig


class DefaultsTest(BrainTestCase):
    def test_default_vault(self):
        # Assert via the module function so a later default flip touches one place.
        self.assertEqual(bconfig.load()["vault"], bconfig.DEFAULT_VAULT())

    def test_default_tasks_db_and_state_under_data_home(self):
        cfg = bconfig.load()
        self.assertEqual(cfg["tasks_db"], self.data_home() / "store/tasks.db")
        self.assertEqual(cfg["state_dir"], self.data_home() / "brain-state")

    def test_data_home_relocation_moves_store_state_and_stream(self):
        """ADDED — the point of resolving through core.paths: one env var moves
        both planes' state. The standalone source hard-coded ~/.claude and could
        not follow a relocated store."""
        elsewhere = self.home / "relocated-data"
        os.environ["TASK_STATION_HOME"] = str(elsewhere)
        cfg = bconfig.load()
        self.assertEqual(cfg["tasks_db"], elsewhere / "store/tasks.db")
        self.assertEqual(cfg["state_dir"], elsewhere / "brain-state")
        self.assertEqual(cfg["episodic_stream"], elsewhere / "stream")


class ConfigFilePathTest(BrainTestCase):
    """ADDED — lock the genericized config filenames (the chunk-1 naming call)."""

    def test_primary_config_path(self):
        self.assertEqual(bconfig._primary_config_path(),
                         self.home / ".claude/brain-station.json")

    def test_home_config_path(self):
        self.assertEqual(bconfig._home_config_path(), self.home / "knowledge/config.json")

    def test_fixture_writes_where_the_module_reads(self):
        written = self.write_primary_config({"vault": str(self.home / "v")})
        self.assertEqual(written, bconfig._primary_config_path())
        written_home = self.write_home_config({"vault": str(self.home / "hv")})
        self.assertEqual(written_home, bconfig._home_config_path())


class EnvNamespaceTest(BrainTestCase):
    """ADDED — scrub + isolation guards: every env name is in the task-station
    brain namespace, and the fixture clears exactly the set the module reads (the
    source's base.py had drifted to a stale key plus a duplicate)."""

    def test_every_env_name_is_namespaced(self):
        for key, env in bconfig.ENV.items():
            self.assertTrue(env.startswith("TASK_STATION_BRAIN_"), f"{key} -> {env}")

    def test_fixture_clears_exactly_the_module_env_keys(self):
        self.assertEqual(sorted(base.ENV_KEYS), sorted(bconfig.ENV.values()))


class PrecedenceTest(BrainTestCase):
    def test_env_beats_json_beats_default(self):
        env_path = self.home / "from-env"
        json_path = self.home / "from-json"

        # default (nothing set)
        self.assertEqual(bconfig.load()["vault"], bconfig.DEFAULT_VAULT())

        # json beats default
        self.write_primary_config({"vault": str(json_path)})
        self.assertEqual(bconfig.load()["vault"], json_path)

        # env beats json
        os.environ["TASK_STATION_BRAIN_VAULT"] = str(env_path)
        self.assertEqual(bconfig.load()["vault"], env_path)

    def test_every_path_key_has_env_override(self):
        os.environ["TASK_STATION_BRAIN_VAULT"] = str(self.home / "v")
        os.environ["TASK_STATION_BRAIN_MEMORY"] = str(self.home / "m")
        os.environ["TASK_STATION_BRAIN_ORG_BRAIN_CLONE"] = str(self.home / "d")
        os.environ["TASK_STATION_BRAIN_TASKS_DB"] = str(self.home / "t.db")
        os.environ["TASK_STATION_BRAIN_STATE"] = str(self.home / "s")
        cfg = bconfig.load()
        self.assertEqual(cfg["vault"], self.home / "v")
        self.assertEqual(cfg["memory"], self.home / "m")
        self.assertEqual(cfg["org_brain_clone"], self.home / "d")
        self.assertEqual(cfg["tasks_db"], self.home / "t.db")
        self.assertEqual(cfg["state_dir"], self.home / "s")


class OrgBrainCloneTest(BrainTestCase):
    """org_brain_clone is the only name. The retired org-tier aliases were
    dropped in the source's 0.14.0 with the extractor that kept them alive."""

    def test_default_when_unset(self):
        self.assertEqual(bconfig.load()["org_brain_clone"], bconfig.DEFAULT_ORG_BRAIN())

    def test_json_key(self):
        self.write_primary_config({"org_brain_clone": str(self.home / "org-clone")})
        self.assertEqual(bconfig.load()["org_brain_clone"], self.home / "org-clone")

    def test_env_key(self):
        os.environ["TASK_STATION_BRAIN_ORG_BRAIN_CLONE"] = str(self.home / "e-org-clone")
        self.assertEqual(bconfig.load()["org_brain_clone"], self.home / "e-org-clone")

    def test_env_beats_json(self):
        self.write_primary_config({"org_brain_clone": str(self.home / "org-clone")})
        os.environ["TASK_STATION_BRAIN_ORG_BRAIN_CLONE"] = str(self.home / "e-org-clone")
        self.assertEqual(bconfig.load()["org_brain_clone"], self.home / "e-org-clone")

    def test_retired_key_is_ignored(self):
        # The old name must NOT resolve any more - a config still carrying it
        # falls through to the default rather than silently half-working.
        self.write_primary_config({"legacy_org_clone": str(self.home / "retired")})
        self.assertEqual(bconfig.load()["org_brain_clone"], bconfig.DEFAULT_ORG_BRAIN())


class ForgeKindTest(BrainTestCase):
    def test_default_ado(self):
        self.assertEqual(bconfig.load()["forge_kind"], "ado")

    def test_json_override(self):
        self.write_primary_config({"forge_kind": "GitHub"})
        self.assertEqual(bconfig.load()["forge_kind"], "github")

    def test_env_override(self):
        os.environ["TASK_STATION_BRAIN_FORGE_KIND"] = "github"
        self.assertEqual(bconfig.load()["forge_kind"], "github")

    def test_tilde_paths_expand(self):
        self.write_primary_config({"vault": "~/tilde-vault"})
        self.assertEqual(bconfig.load()["vault"], self.home / "tilde-vault")


class BooleanParsingTest(BrainTestCase):
    def test_defaults_true(self):
        cfg = bconfig.load()
        self.assertIs(cfg["inject_context"], True)
        self.assertIs(cfg["auto_distill"], True)

    def test_json_booleans(self):
        self.write_primary_config({"inject_context": False, "auto_distill": False})
        cfg = bconfig.load()
        self.assertIs(cfg["inject_context"], False)
        self.assertIs(cfg["auto_distill"], False)

    def test_env_boolean_strings(self):
        for raw, expected in [("0", False), ("false", False), ("FALSE", False),
                              ("1", True), ("true", True), ("True", True)]:
            os.environ["TASK_STATION_BRAIN_INJECT_CONTEXT"] = raw
            self.assertIs(bconfig.load()["inject_context"], expected, raw)

    def test_env_overrides_json_boolean(self):
        self.write_primary_config({"inject_context": True})
        os.environ["TASK_STATION_BRAIN_INJECT_CONTEXT"] = "0"
        self.assertIs(bconfig.load()["inject_context"], False)


class InjectKeywordsTest(BrainTestCase):
    def test_default_empty(self):
        self.assertEqual(bconfig.load()["inject_keywords"], [])

    def test_json_list(self):
        self.write_primary_config({"inject_keywords": ["ledger", "billing"]})
        self.assertEqual(bconfig.load()["inject_keywords"], ["ledger", "billing"])

    def test_env_comma_list_overrides(self):
        self.write_primary_config({"inject_keywords": ["ignored"]})
        os.environ["TASK_STATION_BRAIN_INJECT_KEYWORDS"] = "ledger, billing ,  atlas"
        self.assertEqual(bconfig.load()["inject_keywords"], ["ledger", "billing", "atlas"])


class MalformedJsonTest(BrainTestCase):
    def test_read_degrades_to_defaults_with_one_warning(self):
        self.write_primary_config("{ this is not json ")
        buf = io.StringIO()
        with redirect_stderr(buf):
            cfg = bconfig.load()
            bconfig.load()  # second call: warning must NOT repeat
        self.assertEqual(cfg["vault"], bconfig.DEFAULT_VAULT())
        self.assertEqual(buf.getvalue().lower().count("config"), 1)

    def test_write_path_raises(self):
        self.write_primary_config("{ this is not json ")
        with self.assertRaises(bconfig.ConfigError):
            bconfig.require_valid()

    def test_require_valid_ok_when_config_valid(self):
        self.write_primary_config({"vault": str(self.make_vault(self.home / "vault"))})
        bconfig.require_valid()  # must not raise

    def test_require_valid_ok_when_config_absent(self):
        bconfig.require_valid()  # no config file at all -> defaults are legit


class MemoryDiscoveryTest(BrainTestCase):
    def test_cwd_match_wins(self):
        cwd_a = "/Users/someone/project-a"
        cwd_b = "/Users/someone/project-b"
        self.make_native_memory(cwd_a, mtime=1000)
        self.make_native_memory(cwd_b, mtime=9000)  # newer, but not our cwd
        got = bconfig.discover_native_memory(cwd=cwd_a)
        self.assertEqual(got, self.home / ".claude/projects/-Users-someone-project-a/memory")

    def test_newest_mtime_when_no_cwd_match(self):
        self.make_native_memory("/Users/someone/aaa-old", mtime=1000)
        newest = self.make_native_memory("/Users/someone/zzz-new", mtime=9000)
        got = bconfig.discover_native_memory(cwd="/nowhere/unmatched")
        self.assertEqual(got, newest)

    def test_not_alphabetical_first(self):
        # 'aaa' sorts first alphabetically but is older -> must NOT be chosen.
        self.make_native_memory("/Users/someone/aaa", mtime=1000)
        newest = self.make_native_memory("/Users/someone/mmm", mtime=5000)
        self.make_native_memory("/Users/someone/bbb", mtime=3000)
        got = bconfig.discover_native_memory(cwd="/nowhere")
        self.assertEqual(got, newest)

    def test_none_when_no_memory_dirs(self):
        self.assertIsNone(bconfig.discover_native_memory(cwd="/nowhere"))

    def test_env_memory_overrides_discovery(self):
        self.make_native_memory("/Users/someone/proj", mtime=1000)
        os.environ["TASK_STATION_BRAIN_MEMORY"] = str(self.home / "explicit-mem")
        self.assertEqual(bconfig.load()["memory"], self.home / "explicit-mem")


if __name__ == "__main__":
    unittest.main()
