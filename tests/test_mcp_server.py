"""The Desktop bridge (MCP server) over the SHARED store.

These tests target the plain-stdlib LOGIC functions in lib/mcp_server.py
(`_list_tasks`, `_create_task`, `_get_task`, `_set_status`, `_add_note`) — the
same functions the FastMCP tools wrap. They prove that a Desktop-side create
lands in the very store the CLI reads, round-trips through the CLI's own render
path, and walks the open → active → closed lifecycle. NONE of this needs the
optional `mcp` SDK: the module imports (and the logic runs) with `mcp` absent —
the FastMCP wrapper is lazily imported only inside `main()`.
"""
import importlib
import importlib.util
import os
import shutil
import sys
import tempfile
import unittest

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

import store  # the shared store module the engine + bridge both use


def _load_mcp_server():
    """Import lib/mcp_server.py fresh (it has a clean module name — no hyphen)."""
    sys.modules.pop("mcp_server", None)
    return importlib.import_module("mcp_server")


class McpServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="task-station-mcp-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        self.mcp = _load_mcp_server()
        # Repoint the engine the bridge drives at this test's throwaway store,
        # exactly as the other suites repoint task-station.py's path globals.
        self.ts = self.mcp._engine()
        self.ts.DATA = self.tmp
        self.ts.STORE = os.path.join(self.tmp, "store")
        self.ts.TASKS_DIR = os.path.join(self.ts.STORE, "tasks")
        self.ts.LINKS_DIR = os.path.join(self.ts.STORE, "links")
        store.reset_cache()

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- create -------------------------------------------------------------
    def test_create_lands_as_open_with_source(self):
        t = self.mcp._create_task(
            "Wire up the deploy pipeline",
            "Set up CI for the new service.",
            category="devops",
            effort="m",
            source="https://claude.ai/chat/abc-123",
        )
        self.assertEqual(self.ts.task_status(t), "open")        # open(◦), not active
        self.assertTrue(t.get("seq"))
        self.assertEqual(t.get("effort"), "M")
        self.assertEqual(t.get("source"), "https://claude.ai/chat/abc-123")
        # Persisted to the store, not just returned in memory.
        stored = self.ts.load_task(t["id"])
        self.assertEqual(stored["source"], "https://claude.ai/chat/abc-123")
        self.assertEqual(self.ts.task_status(stored), "open")

    # -- round-trip: Desktop create -> CLI-visible --------------------------
    def test_desktop_create_visible_to_cli(self):
        t = self.mcp._create_task("Desktop-born task", "Created from Claude Desktop.")
        # The CLI's OWN render path (render --format md) now shows it — proving
        # both sides read/write one shared store.
        cli_md = self.ts._format_list_md()
        self.assertIn("Desktop-born task", cli_md)
        self.assertIn("◦ %d" % t["seq"], cli_md)
        # And it is a genuine row in all_tasks(), the listing the CLI builds on.
        ids = [x["id"] for x in self.ts.all_tasks()]
        self.assertIn(t["id"], ids)

    # -- get_task detail ----------------------------------------------------
    def test_get_task_detail_includes_source(self):
        t = self.mcp._create_task(
            "Inspectable task", "A summary worth surfacing.",
            category="devops", effort="l",
            source="desktop://conversation/xyz",
        )
        self.mcp._add_note(str(t["seq"]), "looked into the logs")
        detail = self.mcp._get_task(str(t["seq"]))
        self.assertIn("Inspectable task", detail)
        self.assertIn("A summary worth surfacing.", detail)
        self.assertIn("desktop://conversation/xyz", detail)   # source surfaced
        self.assertIn("looked into the logs", detail)          # activity log
        self.assertIn("◦", detail)                             # open glyph
        # Unknown ref → None (caller renders a not-found line).
        self.assertIsNone(self.mcp._get_task("99999"))

    # -- lifecycle ----------------------------------------------------------
    def test_set_status_open_active_closed(self):
        t = self.mcp._create_task("Lifecycle task", "walk the states")
        ref = str(t["seq"])
        self.assertEqual(self.ts.task_status(t), "open")

        a = self.mcp._set_status(ref, "active")
        self.assertEqual(self.ts.task_status(a), "active")
        self.assertEqual(self.ts.task_status(self.ts.load_task(t["id"])), "active")

        c = self.mcp._set_status(ref, "closed")
        self.assertEqual(self.ts.task_status(c), "closed")
        self.assertTrue(self.ts.is_closed(self.ts.load_task(t["id"])))

        # An out-of-range status is refused, not silently mislabelled.
        with self.assertRaises(ValueError):
            self.mcp._set_status(ref, "bogus")

    # -- add_note -----------------------------------------------------------
    def test_add_note_appends_to_log(self):
        t = self.mcp._create_task("Note task", "needs a note")
        before = len(self.ts.load_task(t["id"]).get("log", []))
        self.mcp._add_note(str(t["seq"]), "first observation")
        log = self.ts.load_task(t["id"]).get("log", [])
        self.assertEqual(len(log), before + 1)
        self.assertEqual(log[-1]["note"], "first observation")
        self.assertIn("ts", log[-1])                            # timestamped

    # -- list md matches the CLI render -------------------------------------
    def test_list_tasks_md_matches_cli_render(self):
        self.mcp._create_task("Board task one", "x", effort="s")
        self.mcp._create_task("Board task two", "y", effort="xl")
        bridge_md = self.mcp._list_tasks()                      # default board
        cli_md = self.ts._format_list_md()                     # CLI --format md
        self.assertEqual(bridge_md, cli_md)

    # -- the module imports & runs with NO mcp SDK present -------------------
    def test_import_and_logic_work_without_mcp(self):
        # Poison `import mcp` so a top-level dependency would explode on import.
        saved = sys.modules.get("mcp")
        sys.modules["mcp"] = None
        try:
            mod = _load_mcp_server()
            mod._engine().STORE = self.ts.STORE                # share this store
            mod._engine().DATA = self.ts.DATA
            store.reset_cache()
            t = mod._create_task("No-SDK task", "made without mcp installed")
            self.assertEqual(mod._engine().task_status(t), "open")
            self.assertIn("No-SDK task", mod._list_tasks())
        finally:
            if saved is not None:
                sys.modules["mcp"] = saved
            else:
                sys.modules.pop("mcp", None)


if __name__ == "__main__":
    unittest.main()
