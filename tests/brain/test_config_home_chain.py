"""brain.config — the home-resolution chain and its defaults.

Order: env -> ``~/.claude/brain-station.json`` (a full config OR a
``{"config": <path>}`` pointer) -> ``~/brains/config.json`` -> defaults. Content
defaults live under ``~/brains/`` with lowercase ``brain``/``org-brain``.

PROVENANCE: ported from the source's ``tests/test_home_chain.py`` @ 0.14.0 (13
cases). Two rewrites, both in the chunk-1 handoff:
  * ``test_pointer_to_home_config_tilde`` pointed at the pre-0.12 home path, which
    no longer existed — so it was passing for the WRONG reason (broken pointer →
    degrade to the home-config layer → same vault). It now points at the real home
    config, which is what it claims to test.
  * ``test_no_legacy_documents_default`` guarded a retired org-branded legacy
    path that has no counterpart here; dropped (net -1 case).
"""
import io
import os
import unittest
from contextlib import redirect_stderr

from tests.brain.base import BrainTestCase

import brain.config as bconfig


class NewDefaultsTest(BrainTestCase):
    def test_default_vault_is_home_brain(self):
        self.assertEqual(bconfig.load()["vault"], self.home / "brains/brain")

    def test_default_memory_is_brain_memory(self):
        # no native-memory dirs discoverable in the temp home -> vault/memory
        self.assertEqual(bconfig.load()["memory"], self.home / "brains/brain/memory")

    def test_default_org_brain_is_home_org_brain(self):
        self.assertEqual(bconfig.load()["org_brain_clone"], self.home / "brains/org-brain")


class HomeConfigLayerTest(BrainTestCase):
    def test_home_config_used_when_no_primary(self):
        self.write_home_config({"vault": str(self.home / "hc-vault")})
        self.assertEqual(bconfig.load()["vault"], self.home / "hc-vault")

    def test_primary_beats_home_config(self):
        self.write_home_config({"vault": str(self.home / "hc-vault")})
        self.write_primary_config({"vault": str(self.home / "primary-vault")})
        self.assertEqual(bconfig.load()["vault"], self.home / "primary-vault")

    def test_env_beats_primary_and_home(self):
        self.write_home_config({"vault": str(self.home / "hc-vault")})
        self.write_primary_config({"vault": str(self.home / "primary-vault")})
        os.environ["TASK_STATION_BRAIN_VAULT"] = str(self.home / "env-vault")
        self.assertEqual(bconfig.load()["vault"], self.home / "env-vault")

    def test_keys_fill_across_layers(self):
        # vault from primary, org brain from the home config, memory from default
        self.write_home_config({"org_brain_clone": str(self.home / "hc-org-brain")})
        self.write_primary_config({"vault": str(self.home / "p-vault")})
        cfg = bconfig.load()
        self.assertEqual(cfg["vault"], self.home / "p-vault")
        self.assertEqual(cfg["org_brain_clone"], self.home / "hc-org-brain")


class PointerTest(BrainTestCase):
    def test_pointer_followed(self):
        target = self.home / "elsewhere/real-config.json"
        target.parent.mkdir(parents=True)
        target.write_text('{"vault": "%s"}' % (self.home / "pointed-vault"))
        self.write_primary_config({"config": str(target)})
        self.assertEqual(bconfig.load()["vault"], self.home / "pointed-vault")

    def test_pointer_to_home_config_tilde(self):
        # a tilde pointer at the home config resolves through $HOME, and NOT via
        # the degrade-to-home-layer path: assert no warning is emitted.
        self.write_home_config({"vault": str(self.home / "hc-vault")})
        self.write_primary_config({"config": "~/brains/config.json"})
        buf = io.StringIO()
        with redirect_stderr(buf):
            cfg = bconfig.load()
        self.assertEqual(cfg["vault"], self.home / "hc-vault")
        self.assertEqual(buf.getvalue(), "")

    def test_broken_pointer_read_warns_and_degrades(self):
        self.write_primary_config({"config": str(self.home / "does-not-exist.json")})
        buf = io.StringIO()
        with redirect_stderr(buf):
            cfg = bconfig.load()
        self.assertEqual(cfg["vault"], self.home / "brains/brain")
        self.assertIn("pointer", buf.getvalue().lower())

    def test_broken_pointer_write_raises(self):
        self.write_primary_config({"config": str(self.home / "does-not-exist.json")})
        with self.assertRaises(bconfig.ConfigError) as ctx:
            bconfig.require_valid()
        self.assertIn("pointer", str(ctx.exception).lower())

    def test_pointer_to_malformed_target_raises_on_write(self):
        target = self.home / "elsewhere/bad.json"
        target.parent.mkdir(parents=True)
        target.write_text("{ not json ")
        self.write_primary_config({"config": str(target)})
        with self.assertRaises(bconfig.ConfigError):
            bconfig.require_valid()


if __name__ == "__main__":
    unittest.main()
