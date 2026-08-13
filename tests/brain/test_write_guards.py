"""Write paths refuse to run against a broken config rather than silently
redirecting to the default vault. Exercised end-to-end through the CLI so the
actual wiring (not just ``require_valid`` in isolation) is covered.

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 4c) from the brain source tree's
``tests/test_write_guards.py`` @ 0.14.0. All 4 source cases port 1:1. Two
mechanical differences, both forced:

  * the CLI is spawned as ``python3 -m brain.search`` with ``lib/`` on
    ``PYTHONPATH`` — a package module with relative imports cannot be run by
    path, which is how the source invoked ``scripts/brain.py``;
  * the default-vault location the refusal must NOT write to is read from
    ``brain.config.DEFAULT_VAULT()`` rather than spelled out, so the case keeps
    asserting the right thing if that default ever moves.

These four genuinely need the subprocess: three assert an EXIT CODE and the
fourth asserts that a refusal wrote nothing — and the CLI resolves its config at
IMPORT time (module-level ``_CFG``), so an in-process run would either measure a
config frozen before the fixture existed or need a module reload.
"""
import os
import subprocess
import sys
import unittest

from tests.brain.base import BrainTestCase, LIB, PINNED_ENV

import brain.config as bconfig


class WriteGuardTest(BrainTestCase):
    def _run(self, *args):
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["PYTHONPATH"] = str(LIB)
        for k, rel in PINNED_ENV.items():
            env[k] = str(self.home / rel)
        for k in ("TASK_STATION_BRAIN_VAULT", "TASK_STATION_BRAIN_MEMORY"):
            env.pop(k, None)
        return subprocess.run(
            [sys.executable, "-m", "brain.search", *args],
            capture_output=True, text=True, env=env,
        )

    def test_new_refuses_on_malformed_config(self):
        self.write_primary_config("{ not json ")
        r = self._run("new", "some-slug", "--description", "x")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("config", (r.stderr + r.stdout).lower())
        # and it must NOT have created a note in the default vault
        self.assertFalse((bconfig.DEFAULT_VAULT() / "notes/some-slug.md").exists())

    def test_log_refuses_on_malformed_config(self):
        self.write_primary_config("{ not json ")
        r = self._run("log", "note", "hello")
        self.assertNotEqual(r.returncode, 0)

    def test_new_succeeds_with_valid_config(self):
        vault = self.home / "vault"
        self.make_vault(vault)
        self.write_primary_config({"vault": str(vault)})
        # the slug leads with a registered domain — `some-slug` no longer can
        r = self._run("new", "ai-some-slug", "--description", "x")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((vault / "notes/ai-some-slug.md").exists())

    def test_the_config_guard_fires_before_the_naming_gate(self):
        """A broken config must be the reported failure even when the slug is also
        wrong — otherwise the naming error hides the reason nothing was written."""
        self.write_primary_config("{ not json ")
        r = self._run("new", "ai-fine-slug", "--description", "x")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("config", (r.stderr + r.stdout).lower())


if __name__ == "__main__":
    unittest.main()
