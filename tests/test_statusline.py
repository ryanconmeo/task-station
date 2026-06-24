"""The opt-in status bar: `config --statusline on|off` (setup.install_statusline /
remove_statusline) + the composition convention. Proves the installer is
non-destructive (never clobbers an existing/foreign statusLine), reversible
(removes only what we own), backs settings.json up, and that the embedded host
script (lib/statusline-host.sh) composes statusline.d/ providers with errors
isolated. Mirrors test_desktop_bridge's temp-dir isolation."""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(REPO_ROOT, "lib")
sys.path.insert(0, LIB)
import setup as station_setup  # noqa: E402
import config  # noqa: E402

HOST_SCRIPT = os.path.join(LIB, "statusline-host.sh")
SAMPLE_JSON = '{"session_id":"abc123","cwd":"/tmp","model":"opus"}'


def _write_provider(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("#!/usr/bin/env bash\n" + body + "\n")
    os.chmod(path, 0o755)


class StatuslineEnabled(unittest.TestCase):
    """statusline_enabled(): default off, env override wins, persists via config."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="task-station-slcfg-")
        self._saved_home = os.environ.get("TASK_STATION_HOME")
        self._saved_env = os.environ.get("TASK_STATION_STATUSLINE")
        os.environ["TASK_STATION_HOME"] = self.tmp
        os.environ.pop("TASK_STATION_STATUSLINE", None)

    def tearDown(self):
        # Save+restore — never leave a harness-pinned var popped.
        if self._saved_home is None:
            os.environ.pop("TASK_STATION_HOME", None)
        else:
            os.environ["TASK_STATION_HOME"] = self._saved_home
        if self._saved_env is None:
            os.environ.pop("TASK_STATION_STATUSLINE", None)
        else:
            os.environ["TASK_STATION_STATUSLINE"] = self._saved_env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_off(self):
        self.assertFalse(config.statusline_enabled())

    def test_persists_via_config(self):
        config.set("statusline", True)
        self.assertTrue(config.statusline_enabled())

    def test_env_on_overrides_config_off(self):
        config.set("statusline", False)
        os.environ["TASK_STATION_STATUSLINE"] = "on"
        self.assertTrue(config.statusline_enabled())   # env wins over config

    def test_env_off_overrides_config_on(self):
        config.set("statusline", True)
        os.environ["TASK_STATION_STATUSLINE"] = "off"
        self.assertFalse(config.statusline_enabled())


class InstallRemove(unittest.TestCase):
    """install_statusline / remove_statusline against a temp CLAUDE_CONFIG_DIR."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="task-station-sl-")
        self._saved_cfg = os.environ.get("CLAUDE_CONFIG_DIR")
        self._saved_home = os.environ.get("TASK_STATION_HOME")
        os.environ["CLAUDE_CONFIG_DIR"] = self.tmp
        os.environ["TASK_STATION_HOME"] = self.tmp
        self.settings = os.path.join(self.tmp, "settings.json")

    def tearDown(self):
        # Save+restore — never leave a harness-pinned var popped.
        if self._saved_cfg is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self._saved_cfg
        if self._saved_home is None:
            os.environ.pop("TASK_STATION_HOME", None)
        else:
            os.environ["TASK_STATION_HOME"] = self._saved_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _read(self):
        with open(self.settings) as f:
            return json.load(f)

    def _write_settings(self, data):
        with open(self.settings, "w") as f:
            json.dump(data, f)

    # -- no existing statusLine → install as host, provider registered, backup --
    def test_install_no_existing_becomes_host(self):
        self._write_settings({"foo": "bar"})           # exists, no statusLine
        station_setup.install_statusline()
        data = self._read()
        self.assertIn("statusLine", data)
        self.assertIn(station_setup.STATUSLINE_HOST_MARKER, data["statusLine"]["command"])
        self.assertEqual(data["statusLine"]["type"], "command")
        self.assertEqual(data["foo"], "bar")            # sibling key preserved
        # provider drop-in exists + executable
        prov = station_setup.provider_path()
        self.assertTrue(os.path.exists(prov))
        self.assertTrue(os.access(prov, os.X_OK))
        # backup written
        self.assertTrue(os.path.exists(self.settings + station_setup.SETTINGS_BACKUP_SUFFIX))
        # status reports host
        self.assertEqual(station_setup.statusline_status(), "installed (host)")

    # -- foreign statusLine present → NOT clobbered, provider still registered --
    def test_install_foreign_not_clobbered(self):
        foreign = {"statusLine": {"type": "command", "command": "costbar --fancy"}}
        self._write_settings(foreign)
        msg = station_setup.install_statusline()
        data = self._read()
        # foreign command untouched
        self.assertEqual(data["statusLine"]["command"], "costbar --fancy")
        self.assertNotIn(station_setup.STATUSLINE_HOST_MARKER, data["statusLine"]["command"])
        # provider still registered
        self.assertTrue(os.path.exists(station_setup.provider_path()))
        # status: provider-only (not host)
        self.assertEqual(station_setup.statusline_status(), "provider-only")
        self.assertIn("another command", msg.lower())

    # -- our marker already present → idempotent, no duplicate / no error -------
    def test_install_idempotent_when_ours(self):
        self._write_settings({})
        station_setup.install_statusline()
        first = self._read()
        msg = station_setup.install_statusline()   # second run
        second = self._read()
        self.assertEqual(first, second)            # settings unchanged
        self.assertIn("already owns", msg.lower())

    # -- remove: drops provider + clears OUR statusLine -------------------------
    def test_remove_clears_ours(self):
        self._write_settings({"foo": "bar"})
        station_setup.install_statusline()
        self.assertTrue(os.path.exists(station_setup.provider_path()))
        station_setup.remove_statusline()
        data = self._read()
        self.assertNotIn("statusLine", data)            # ours cleared
        self.assertEqual(data["foo"], "bar")            # sibling preserved
        self.assertFalse(os.path.exists(station_setup.provider_path()))  # provider gone
        self.assertEqual(station_setup.statusline_status(), "off")

    # -- remove: leaves a FOREIGN statusLine intact -----------------------------
    def test_remove_leaves_foreign_intact(self):
        self._write_settings({"statusLine": {"type": "command", "command": "costbar"}})
        station_setup.install_statusline()              # registers provider only
        station_setup.remove_statusline()
        data = self._read()
        self.assertEqual(data["statusLine"]["command"], "costbar")  # untouched
        self.assertFalse(os.path.exists(station_setup.provider_path()))  # provider gone

    # -- unregister never deletes a foreign (non-managed) provider --------------
    def test_unregister_spares_foreign_provider(self):
        _write_provider(station_setup.provider_path(), "echo hand-rolled")  # no marker
        self.assertFalse(station_setup.unregister_provider())
        self.assertTrue(os.path.exists(station_setup.provider_path()))

    # -- cmd_config dispatches on/off + persists the flag -----------------------
    def test_cmd_config_dispatches(self):
        import types
        self._write_settings({})
        config.cmd_config(types.SimpleNamespace(workspace_dirs=None, statusline="on"))
        self.assertTrue(config.statusline_enabled())
        self.assertIn(station_setup.STATUSLINE_HOST_MARKER, self._read()["statusLine"]["command"])
        config.cmd_config(types.SimpleNamespace(workspace_dirs=None, statusline="off"))
        self.assertFalse(config.statusline_enabled())
        self.assertNotIn("statusLine", self._read())


class HostCompose(unittest.TestCase):
    """The embedded host script composes statusline.d/* with errors isolated."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="task-station-host-")
        self.d = os.path.join(self.tmp, "statusline.d")
        os.makedirs(self.d)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, sep=None):
        env = dict(os.environ)
        env["CLAUDE_CONFIG_DIR"] = self.tmp
        if sep is not None:
            env["CLAUDE_STATUSLINE_SEP"] = sep
        proc = subprocess.run(["bash", HOST_SCRIPT], input=SAMPLE_JSON,
                              capture_output=True, text=True, env=env, timeout=20)
        return proc

    def test_skips_empty_and_failing_providers(self):
        _write_provider(os.path.join(self.d, "10-alpha.sh"), "echo ALPHA")
        _write_provider(os.path.join(self.d, "20-empty.sh"), "exit 0")        # no output
        _write_provider(os.path.join(self.d, "30-fail.sh"), "echo nope; exit 1")  # non-zero
        proc = self._run()
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "ALPHA")   # only the good one

    def test_joins_in_lexical_order_with_separator(self):
        _write_provider(os.path.join(self.d, "40-beta.sh"), "echo BETA")
        _write_provider(os.path.join(self.d, "10-alpha.sh"), "echo ALPHA")
        proc = self._run(sep=" | ")
        self.assertEqual(proc.stdout.strip(), "ALPHA | BETA")  # 10 before 40

    def test_non_executable_provider_ignored(self):
        _write_provider(os.path.join(self.d, "10-alpha.sh"), "echo ALPHA")
        plain = os.path.join(self.d, "20-plain.sh")
        with open(plain, "w") as f:
            f.write("#!/usr/bin/env bash\necho PLAIN\n")
        os.chmod(plain, stat.S_IRUSR | stat.S_IWUSR)     # not executable
        proc = self._run()
        self.assertEqual(proc.stdout.strip(), "ALPHA")

    def test_no_providers_prints_nothing(self):
        proc = self._run()
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
