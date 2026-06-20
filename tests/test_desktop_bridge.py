"""`config --desktop-bridge on|off` self-installer (setup.install_desktop_bridge /
remove_desktop_bridge). Proves it MERGES a `task-station` entry into Claude
Desktop's `claude_desktop_config.json` without clobbering other servers, is
idempotent, backs the file up, and removes only our entry on `off`."""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import setup as station_setup


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

    # -- on: creates + merges, points at the stable engine path -------------
    def test_on_creates_and_merges_entry(self):
        msg = station_setup.install_desktop_bridge(self.cfg)
        self.assertTrue(os.path.exists(self.cfg))
        data = self._read()
        entry = data["mcpServers"]["task-station"]
        self.assertEqual(entry["command"], "python3")
        self.assertEqual(len(entry["args"]), 1)
        path = entry["args"][0]
        self.assertTrue(os.path.isabs(path))
        self.assertTrue(path.endswith("mcp_server.py"))
        self.assertIn("task-station-engine", path)   # the stable symlink path
        self.assertIn("restart", msg.lower())         # "restart Claude Desktop"

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
        self.assertTrue(path.endswith("mcp_server.py"))

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


if __name__ == "__main__":
    unittest.main()
