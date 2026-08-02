"""`config --desktop-bridge on|off` self-installer (setup.install_desktop_bridge /
remove_desktop_bridge). Proves it MERGES a `task-station` entry into Claude
Desktop's `claude_desktop_config.json` without clobbering other servers, is
idempotent, backs the file up, and removes only our entry on `off`."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(REPO_ROOT, "lib")
sys.path.insert(0, LIB)
import setup as station_setup


def _load_launcher():
    """Import the bundled launcher source (`lib/mcp_launcher.py`) under a clean
    module name so its resolution helpers can be exercised directly."""
    spec = importlib.util.spec_from_file_location(
        "mcp_launcher", os.path.join(LIB, "mcp_launcher.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").close()


class DesktopBridge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="task-station-bridge-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        # A nested path that does NOT exist yet — install must create the dirs.
        self.cfg = os.path.join(self.tmp, "Application Support", "Claude",
                                "claude_desktop_config.json")

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _read(self):
        with open(self.cfg) as f:
            return json.load(f)

    # -- on: creates + merges, points at the stable self-resolving launcher --
    def test_on_creates_and_merges_entry(self):
        msg = station_setup.install_desktop_bridge(self.cfg)
        self.assertTrue(os.path.exists(self.cfg))
        data = self._read()
        entry = data["mcpServers"]["task-station"]
        self.assertEqual(entry["command"], "python3")
        self.assertEqual(len(entry["args"]), 1)
        path = entry["args"][0]
        self.assertTrue(os.path.isabs(path))
        # args[0] is the STABLE launcher, NOT the volatile engine symlink.
        self.assertEqual(path, station_setup.launcher_path())
        self.assertTrue(path.endswith("mcp-launcher.py"))
        self.assertNotIn("task-station-engine", path)
        self.assertTrue(os.path.exists(path))          # launcher generated on `on`
        self.assertIn("restart", msg.lower())          # "restart Claude Desktop"

    # -- on: does not clobber a pre-existing other server -------------------
    def test_on_preserves_other_servers(self):
        os.makedirs(os.path.dirname(self.cfg))
        with open(self.cfg, "w") as f:
            json.dump({"mcpServers": {"other": {"command": "node", "args": ["x.js"]}},
                       "theme": "dark"}, f)
        station_setup.install_desktop_bridge(self.cfg)
        data = self._read()
        self.assertIn("other", data["mcpServers"])             # untouched
        self.assertEqual(data["mcpServers"]["other"]["command"], "node")
        self.assertIn("task-station", data["mcpServers"])      # added
        self.assertEqual(data["theme"], "dark")                # sibling key kept

    # -- on: idempotent — second run leaves exactly one entry ---------------
    def test_on_is_idempotent(self):
        station_setup.install_desktop_bridge(self.cfg)
        first = self._read()
        station_setup.install_desktop_bridge(self.cfg)
        second = self._read()
        self.assertEqual(first, second)
        self.assertEqual(list(second["mcpServers"].keys()), ["task-station"])

    # -- backup written -----------------------------------------------------
    def test_backup_written(self):
        os.makedirs(os.path.dirname(self.cfg))
        with open(self.cfg, "w") as f:
            json.dump({"mcpServers": {"other": {"command": "node"}}}, f)
        station_setup.install_desktop_bridge(self.cfg)
        bak = self.cfg + ".bak-desktop-bridge"
        self.assertTrue(os.path.exists(bak))
        with open(bak) as f:
            self.assertEqual(json.load(f), {"mcpServers": {"other": {"command": "node"}}})

    # -- off: removes only ours --------------------------------------------
    def test_off_removes_only_our_entry(self):
        os.makedirs(os.path.dirname(self.cfg))
        with open(self.cfg, "w") as f:
            json.dump({"mcpServers": {"other": {"command": "node"}}}, f)
        station_setup.install_desktop_bridge(self.cfg)
        station_setup.remove_desktop_bridge(self.cfg)
        data = self._read()
        self.assertNotIn("task-station", data["mcpServers"])   # ours gone
        self.assertIn("other", data["mcpServers"])             # other intact

    # -- off when nothing installed is a graceful no-op ---------------------
    def test_off_no_file_is_noop(self):
        msg = station_setup.remove_desktop_bridge(self.cfg)   # never created
        self.assertIsInstance(msg, str)

    # -- status reflects install state -------------------------------------
    def test_status_reports_installed(self):
        installed, _ = station_setup.desktop_bridge_status(self.cfg)
        self.assertFalse(installed)
        station_setup.install_desktop_bridge(self.cfg)
        installed, path = station_setup.desktop_bridge_status(self.cfg)
        self.assertTrue(installed)
        self.assertTrue(path.endswith("mcp-launcher.py"))

    # -- cmd_config dispatches on/off (patched so no real ~/Library write) --
    def test_cmd_config_dispatches_on_off(self):
        import types, config
        calls = []
        orig_on = station_setup.install_desktop_bridge
        orig_off = station_setup.remove_desktop_bridge
        station_setup.install_desktop_bridge = lambda *a: calls.append("on") or "on-msg"
        station_setup.remove_desktop_bridge = lambda *a: calls.append("off") or "off-msg"
        try:
            config.cmd_config(types.SimpleNamespace(workspace_dirs=None, desktop_bridge="on"))
            config.cmd_config(types.SimpleNamespace(workspace_dirs=None, desktop_bridge="off"))
        finally:
            station_setup.install_desktop_bridge = orig_on
            station_setup.remove_desktop_bridge = orig_off
        self.assertEqual(calls, ["on", "off"])


class LauncherResolution(unittest.TestCase):
    """The generated launcher resolves the INSTALLED mcp_server.py itself —
    installed_plugins.json first, highest-cache-version fallback second."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="task-station-launcher-")
        self.cfg_dir = os.path.join(self.tmp, "config")     # stands in for ~/.claude
        self.launcher = _load_launcher()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _installed_plugins(self, install_path):
        p = os.path.join(self.cfg_dir, "plugins", "installed_plugins.json")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            json.dump({"plugins": {"task-station@ryanconmeo":
                                   [{"installPath": install_path}]}}, f)

    # -- installed_plugins.json wins ---------------------------------------
    def test_resolves_via_installed_plugins(self):
        install_path = os.path.join(self.tmp, "install", "1.6.1")
        server = os.path.join(install_path, "lib", "mcp_server.py")
        _touch(server)
        self._installed_plugins(install_path)
        self.assertEqual(self.launcher.resolve_server(self.cfg_dir), server)

    # -- missing installed_plugins → highest cache version that has the file
    def test_falls_back_to_highest_cache_version(self):
        cache = os.path.join(self.cfg_dir, "plugins", "cache", "ryanconmeo",
                             "task-station")
        _touch(os.path.join(cache, "1.2.0", "lib", "mcp_server.py"))
        _touch(os.path.join(cache, "1.10.0", "lib", "mcp_server.py"))   # numerically highest
        os.makedirs(os.path.join(cache, "1.20.0", "lib"))               # newer dir, NO file
        # No installed_plugins.json at all → fallback path.
        resolved = self.launcher.resolve_server(self.cfg_dir)
        self.assertEqual(resolved,
                         os.path.join(cache, "1.10.0", "lib", "mcp_server.py"))
        # 1.10.0 must beat 1.2.0 — numeric compare, not lexical.
        self.assertIn(os.path.join("1.10.0", "lib"), resolved)

    # -- installed_plugins present but its target is gone → cache fallback --
    def test_installed_plugins_missing_file_falls_back(self):
        self._installed_plugins(os.path.join(self.tmp, "install", "gone"))  # no lib/mcp_server.py
        cache = os.path.join(self.cfg_dir, "plugins", "cache", "ryanconmeo",
                             "task-station")
        _touch(os.path.join(cache, "1.5.0", "lib", "mcp_server.py"))
        self.assertEqual(self.launcher.resolve_server(self.cfg_dir),
                         os.path.join(cache, "1.5.0", "lib", "mcp_server.py"))

    # -- nothing resolvable → a clear error, not a silent bad exec ----------
    def test_unresolvable_raises(self):
        with self.assertRaises(RuntimeError):
            self.launcher.resolve_server(self.cfg_dir)


class DesktopConfigOverride(unittest.TestCase):
    """`TASK_STATION_DESKTOP_CONFIG` redirects the CLI path (install/remove/status
    called with NO explicit path) to a temp file — the real Desktop config is
    never the target when the override is set."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="task-station-cfgenv-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        self.override = os.path.join(self.tmp, "Claude", "claude_desktop_config.json")
        os.environ["TASK_STATION_DESKTOP_CONFIG"] = self.override

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        os.environ.pop("TASK_STATION_DESKTOP_CONFIG", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_env_override_targets_temp_not_real(self):
        # The resolved path is the override, NOT ~/Library/Application Support.
        self.assertEqual(station_setup.desktop_config_path(), self.override)
        self.assertNotIn("Application Support", station_setup.desktop_config_path())
        # install with NO path arg (the CLI shape) writes to the override file.
        station_setup.install_desktop_bridge()
        self.assertTrue(os.path.exists(self.override))
        with open(self.override) as f:
            data = json.load(f)
        self.assertIn("task-station", data["mcpServers"])
        self.assertEqual(data["mcpServers"]["task-station"]["args"][0],
                         station_setup.launcher_path())

    def test_cmd_config_on_off_respects_override(self):
        import types, config
        config.cmd_config(types.SimpleNamespace(workspace_dirs=None, desktop_bridge="on"))
        self.assertTrue(os.path.exists(self.override))           # wrote to temp, not real
        with open(self.override) as f:
            self.assertIn("task-station", json.load(f)["mcpServers"])
        config.cmd_config(types.SimpleNamespace(workspace_dirs=None, desktop_bridge="off"))
        with open(self.override) as f:
            self.assertNotIn("task-station", json.load(f).get("mcpServers", {}))


class RoundTripThroughLauncher(unittest.TestCase):
    """End-to-end THROUGH the generated launcher: launcher → mcp_server →
    tools/call create_task → shared store → CLI-visible. Proves the stable
    launcher really becomes the version-resolved MCP server."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="task-station-rt-")
        self.store_home = os.path.join(self.tmp, "store-home")
        self.cfg_dir = os.path.join(self.tmp, "config")
        os.makedirs(self.store_home)
        # installed_plugins.json points at THIS repo (which has lib/mcp_server.py).
        p = os.path.join(self.cfg_dir, "plugins", "installed_plugins.json")
        os.makedirs(os.path.dirname(p))
        with open(p, "w") as f:
            json.dump({"plugins": {"task-station@ryanconmeo":
                                   [{"installPath": REPO_ROOT}]}}, f)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_create_task_through_launcher_is_cli_visible(self):
        # Generate the launcher at the stable data-dir path (data_dir = store_home).
        os.environ["TASK_STATION_HOME"] = self.store_home
        try:
            launcher = station_setup.write_launcher()
        finally:
            os.environ.pop("TASK_STATION_HOME", None)
        self.assertTrue(os.path.exists(launcher))

        env = dict(os.environ)
        env["CLAUDE_CONFIG_DIR"] = self.cfg_dir       # how the launcher resolves the server
        env["TASK_STATION_HOME"] = self.store_home    # how the resolved server finds the store
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "create_task",
                        "arguments": {"title": "Launcher round-trip task",
                                      "summary": "born through the stable launcher"}}},
        ]
        stdin = "\n".join(json.dumps(r) for r in requests) + "\n"
        proc = subprocess.run([sys.executable, launcher], input=stdin,
                              capture_output=True, text=True, env=env, timeout=30)
        out = [json.loads(ln) for ln in proc.stdout.splitlines() if ln.strip()]
        # initialize answered by the resolved real server.
        init = [o for o in out if o.get("id") == 1][0]
        self.assertEqual(init["result"]["serverInfo"]["name"], "task-station")
        # create_task succeeded.
        created = [o for o in out if o.get("id") == 2][0]
        self.assertFalse(created["result"].get("isError"), proc.stderr)
        self.assertIn("Launcher round-trip task", created["result"]["content"][0]["text"])

        # CLI-visible: load the engine, point it at the same store, confirm the row.
        spec = importlib.util.spec_from_file_location(
            "task_station_rt", os.path.join(LIB, "task-station.py"))
        ts = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ts)
        import store
        ts.DATA = self.store_home
        ts.STORE = os.path.join(self.store_home, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        store.reset_cache()
        titles = [t.get("title") for t in ts.all_tasks()]
        self.assertIn("Launcher round-trip task", titles)
        self.assertIn("Launcher round-trip task", ts._format_list_md())
        store.reset_cache()


if __name__ == "__main__":
    unittest.main()
