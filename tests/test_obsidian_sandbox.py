"""Fix A — `task-station config --obsidian-sandbox on` widens Claude Code's Bash
sandbox write allowlist so an Obsidian vault under a protected root (~/Documents)
gets INSTANT inline exports, not just the end-of-turn hook auto-flush.

The installer adds the configured vault path to `sandbox.filesystem.allowWrite`
in the user's `~/.claude/settings.json`, read-merge-write ATOMICALLY:
  * creates the sandbox/filesystem/allowWrite structure only as needed,
  * is a no-op if the path is already present,
  * NEVER disturbs other keys (incl. sandbox.enabled) or values,
  * records exactly what it added in setup-manifest.json so removal is precise
    and drops only the empty structures it created.

Isolated via a tmp CLAUDE_CONFIG_DIR (settings.json) + TASK_STATION_HOME (manifest).
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

import setup  # noqa: E402


class ObsidianSandboxAllowWrite(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="obs-sandbox-")
        self._env = {k: os.environ.get(k) for k in ("CLAUDE_CONFIG_DIR", "TASK_STATION_HOME")}
        os.environ["CLAUDE_CONFIG_DIR"] = self.tmp
        os.environ["TASK_STATION_HOME"] = self.tmp
        self.settings = setup.settings_path()
        self.vault = "~/Documents/Obsidian Vault"

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _read(self):
        with open(self.settings) as f:
            return json.load(f)

    # --- add ---------------------------------------------------------------
    def test_install_adds_path_and_records_manifest(self):
        setup.install_sandbox_allowwrite(self.vault)
        data = self._read()
        self.assertIn(self.vault, data["sandbox"]["filesystem"]["allowWrite"])
        rec = setup._manifest().get("sandbox_allowwrite")
        self.assertEqual(rec["entry"], self.vault)
        # we created the whole chain from scratch
        self.assertEqual(rec["created"], ["sandbox", "filesystem", "allowWrite"])

    def test_install_is_idempotent(self):
        setup.install_sandbox_allowwrite(self.vault)
        setup.install_sandbox_allowwrite(self.vault)
        aw = self._read()["sandbox"]["filesystem"]["allowWrite"]
        self.assertEqual(aw.count(self.vault), 1)

    # --- no-clobber of other keys / values / sandbox.enabled ---------------
    def test_install_preserves_other_keys_and_enabled(self):
        preexisting = {
            "model": "claude-opus-4-8",
            "statusLine": {"type": "command", "command": "echo hi"},
            "sandbox": {"enabled": True, "filesystem": {"allowWrite": ["~/pre-existing"]}},
        }
        with open(self.settings, "w") as f:
            json.dump(preexisting, f)
        setup.install_sandbox_allowwrite(self.vault)
        data = self._read()
        self.assertEqual(data["model"], "claude-opus-4-8")
        self.assertEqual(data["statusLine"]["command"], "echo hi")
        self.assertIs(data["sandbox"]["enabled"], True)         # untouched
        aw = data["sandbox"]["filesystem"]["allowWrite"]
        self.assertIn("~/pre-existing", aw)                     # user's entry survives
        self.assertIn(self.vault, aw)
        # nothing was "created" (structure already existed)
        self.assertEqual(setup._manifest()["sandbox_allowwrite"]["created"], [])

    # --- remove round-trip -------------------------------------------------
    def test_remove_round_trips_to_original(self):
        # from-scratch install then remove → file has no sandbox trace, manifest clean
        setup.install_sandbox_allowwrite(self.vault)
        setup.remove_sandbox_allowwrite()
        data = self._read()
        self.assertNotIn("sandbox", data)                       # empty chain we created is gone
        self.assertNotIn("sandbox_allowwrite", setup._manifest())

    def test_remove_leaves_enabled_and_other_paths(self):
        preexisting = {"sandbox": {"enabled": True, "filesystem": {"allowWrite": ["~/keep"]}}}
        with open(self.settings, "w") as f:
            json.dump(preexisting, f)
        setup.install_sandbox_allowwrite(self.vault)
        setup.remove_sandbox_allowwrite()
        data = self._read()
        # our entry gone; user's structure + enabled fully intact
        self.assertIs(data["sandbox"]["enabled"], True)
        self.assertEqual(data["sandbox"]["filesystem"]["allowWrite"], ["~/keep"])
        self.assertNotIn("sandbox_allowwrite", setup._manifest())

    def test_remove_when_never_installed_is_noop(self):
        msg = setup.remove_sandbox_allowwrite()
        self.assertIsInstance(msg, str)
        self.assertFalse(os.path.exists(self.settings))         # nothing written

    def test_status_reflects_install(self):
        self.assertFalse(setup.sandbox_allowwrite_status())
        setup.install_sandbox_allowwrite(self.vault)
        self.assertTrue(setup.sandbox_allowwrite_status())
        setup.remove_sandbox_allowwrite()
        self.assertFalse(setup.sandbox_allowwrite_status())


if __name__ == "__main__":
    unittest.main()
