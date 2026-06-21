"""The Desktop bridge (MCP server) over the SHARED store.

These tests target the plain-stdlib LOGIC functions in lib/mcp_server.py
(`_list_tasks`, `_create_task`, `_get_task`, `_set_status`, `_add_note`) — the
same functions the hand-rolled MCP server dispatches — AND drive the server
itself over stdio with real JSON-RPC traffic. They prove that a Desktop-side
create lands in the very store the CLI reads, round-trips through the CLI's own
render path, and walks the open → active → closed lifecycle. NONE of this needs
the `mcp` SDK: the server is hand-rolled in stdlib only (json + sys), so the
module imports, runs, and serves a full protocol round-trip with `mcp` absent.
"""
import importlib
import importlib.util
import io
import json
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

    # -- verbatim-render instruction prefix on the TOOL result --------------
    def test_list_tasks_tool_result_leads_with_verbatim_instruction(self):
        self.mcp._create_task("Board task one", "x", effort="s")
        out = self.mcp._tool_list_tasks({})
        # Leads with the verbatim-render instruction line.
        self.assertTrue(out.startswith(self.mcp.VERBATIM_INSTRUCTION))
        self.assertIn("EXACTLY as written below", out)
        self.assertIn("render the tables verbatim", out)
        # Then the Markdown board: section header + exact table header row.
        self.assertIn("### Open", out)
        self.assertIn("| # | Task |", out)
        # The board body (minus the prefix) is byte-equal to the CLI render.
        board = out[len(self.mcp.VERBATIM_INSTRUCTION):].lstrip("\n")
        self.assertEqual(board, self.ts._format_list_md())
        # The raw helper stays byte-equal to the CLI (no prefix leaks in).
        self.assertEqual(self.mcp._list_tasks(), self.ts._format_list_md())

    # -- every tool carries a crisp, non-empty description ------------------
    def test_every_tool_has_a_description(self):
        for t in self.mcp.TOOLS:
            self.assertTrue(t["description"].strip(),
                            "tool %r needs a description" % t["name"])

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


class McpProtocolTest(unittest.TestCase):
    """Drive the hand-rolled stdio JSON-RPC server end-to-end: feed newline-
    delimited requests on a fake stdin, read responses off a fake stdout."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="task-station-mcp-proto-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        self.mcp = _load_mcp_server()
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

    def _drive(self, requests):
        """Pipe `requests` (list of JSON-RPC dicts) through the server's stdio
        loop; return the parsed response objects (one per line of stdout)."""
        stdin = io.StringIO("\n".join(json.dumps(r) for r in requests) + "\n")
        stdout = io.StringIO()
        self.mcp.serve(stdin, stdout)
        return [json.loads(ln) for ln in stdout.getvalue().splitlines() if ln.strip()]

    def _one(self, request):
        out = self._drive([request])
        self.assertEqual(len(out), 1)
        return out[0]

    # -- initialize ---------------------------------------------------------
    def test_initialize_advertises_capabilities(self):
        resp = self._one({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                          "params": {"protocolVersion": "2024-11-05"}})
        self.assertEqual(resp["jsonrpc"], "2.0")
        self.assertEqual(resp["id"], 1)
        result = resp["result"]
        self.assertIn("protocolVersion", result)
        self.assertEqual(result["serverInfo"]["name"], "task-station")
        self.assertTrue(result["serverInfo"]["version"])     # plugin version
        caps = result["capabilities"]
        self.assertIn("tools", caps)
        self.assertIn("prompts", caps)
        self.assertIn("resources", caps)

    # -- initialize does NOT carry an `instructions` field ------------------
    def test_initialize_has_no_instructions(self):
        # Desktop silently drops MCP `instructions`, so the 1.6.3 field was inert
        # and was removed in 1.6.4. The rest of the result must stay intact.
        resp = self._one({"jsonrpc": "2.0", "id": 16, "method": "initialize",
                          "params": {"protocolVersion": "2024-11-05"}})
        result = resp["result"]
        self.assertNotIn("instructions", result)
        # The regression invariants still hold.
        self.assertIn("protocolVersion", result)
        self.assertEqual(result["serverInfo"]["name"], "task-station")
        self.assertTrue(result["serverInfo"]["version"])
        for k in ("tools", "prompts", "resources"):
            self.assertIn(k, result["capabilities"])

    # -- ping + initialized notification ------------------------------------
    def test_ping_returns_empty(self):
        resp = self._one({"jsonrpc": "2.0", "id": 7, "method": "ping"})
        self.assertEqual(resp["result"], {})

    def test_initialized_notification_no_response(self):
        # A notification has no `id` → the server must stay silent.
        out = self._drive([{"jsonrpc": "2.0", "method": "notifications/initialized"}])
        self.assertEqual(out, [])

    # -- tools/list ---------------------------------------------------------
    def test_tools_list_has_the_tools(self):
        resp = self._one({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {t["name"] for t in resp["result"]["tools"]}
        self.assertEqual(
            names,
            {"list_tasks", "create_task", "get_task", "set_status", "add_note"})
        # Each tool carries a JSON-Schema inputSchema.
        for t in resp["result"]["tools"]:
            self.assertEqual(t["inputSchema"]["type"], "object")

    # -- tools/call create_task → store → CLI-visible -----------------------
    def test_tools_call_create_task_lands_in_store(self):
        resp = self._one({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "create_task",
                       "arguments": {"title": "RPC-born task",
                                     "summary": "made over JSON-RPC",
                                     "source": "https://claude.ai/chat/zzz"}}})
        result = resp["result"]
        self.assertFalse(result.get("isError"))
        text = result["content"][0]["text"]
        self.assertIn("RPC-born task", text)
        # The task is genuinely in the shared store, visible to the CLI render.
        cli_md = self.ts._format_list_md()
        self.assertIn("RPC-born task", cli_md)
        ids = [x["id"] for x in self.ts.all_tasks()]
        self.assertTrue(ids)

    def test_tools_call_unknown_tool_is_error_result(self):
        resp = self._one({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                          "params": {"name": "no_such_tool", "arguments": {}}})
        # A bad tool name is a tool-execution error (isError), not a protocol error.
        self.assertTrue(resp["result"]["isError"])

    # -- prompts ------------------------------------------------------------
    def test_prompts_list_and_get_todo(self):
        self.mcp._create_task("Prompt board task", "x")
        lst = self._one({"jsonrpc": "2.0", "id": 5, "method": "prompts/list"})
        names = {p["name"] for p in lst["result"]["prompts"]}
        self.assertIn("todo", names)
        got = self._one({"jsonrpc": "2.0", "id": 6, "method": "prompts/get",
                         "params": {"name": "todo"}})
        msgs = got["result"]["messages"]
        self.assertIn("Prompt board task", msgs[0]["content"]["text"])

    # -- todo prompt is discoverable: non-empty description (+ title) -------
    def test_prompts_list_todo_has_description(self):
        lst = self._one({"jsonrpc": "2.0", "id": 12, "method": "prompts/list"})
        todo = next(p for p in lst["result"]["prompts"] if p["name"] == "todo")
        self.assertTrue(todo.get("description", "").strip())   # discoverable
        self.assertTrue(todo.get("title", "").strip())         # newer-spec title

    # -- todo prompt CONTENT leads with the verbatim instruction + board ----
    def test_prompts_get_todo_content_has_instruction_and_board(self):
        self.mcp._create_task("Prompt verbatim task", "x")
        got = self._one({"jsonrpc": "2.0", "id": 13, "method": "prompts/get",
                         "params": {"name": "todo"}})
        text = got["result"]["messages"][0]["content"]["text"]
        self.assertTrue(text.startswith(self.mcp.VERBATIM_INSTRUCTION))
        self.assertIn("### Open", text)
        self.assertIn("Prompt verbatim task", text)

    # -- tools/call list_tasks result leads with the instruction over RPC ---
    def test_tools_call_list_tasks_leads_with_instruction(self):
        self.mcp._create_task("Proto board task", "x")
        resp = self._one({"jsonrpc": "2.0", "id": 14, "method": "tools/call",
                          "params": {"name": "list_tasks", "arguments": {}}})
        text = resp["result"]["content"][0]["text"]
        self.assertTrue(text.startswith(self.mcp.VERBATIM_INSTRUCTION))
        self.assertIn("### Open", text)
        self.assertIn("| # | Task |", text)

    # -- tools/list descriptions are all non-empty --------------------------
    def test_tools_list_descriptions_non_empty(self):
        resp = self._one({"jsonrpc": "2.0", "id": 15, "method": "tools/list"})
        for t in resp["result"]["tools"]:
            self.assertTrue(t["description"].strip())

    # -- resources ----------------------------------------------------------
    def test_resources_list_and_read_detail(self):
        t = self.mcp._create_task("Resource task", "detail body here")
        uri = "task://%d" % t["seq"]
        lst = self._one({"jsonrpc": "2.0", "id": 8, "method": "resources/list"})
        uris = {r["uri"] for r in lst["result"]["resources"]}
        self.assertIn(uri, uris)
        rd = self._one({"jsonrpc": "2.0", "id": 9, "method": "resources/read",
                        "params": {"uri": uri}})
        contents = rd["result"]["contents"]
        self.assertEqual(contents[0]["uri"], uri)
        self.assertIn("Resource task", contents[0]["text"])
        self.assertIn("detail body here", contents[0]["text"])

    # -- unknown method → -32601 --------------------------------------------
    def test_unknown_method_is_method_not_found(self):
        resp = self._one({"jsonrpc": "2.0", "id": 10, "method": "no/such/method"})
        self.assertEqual(resp["error"]["code"], -32601)

    # -- malformed line never crashes the loop ------------------------------
    def test_malformed_line_does_not_crash(self):
        stdin = io.StringIO("this is not json\n" +
                            json.dumps({"jsonrpc": "2.0", "id": 11, "method": "ping"}) + "\n")
        stdout = io.StringIO()
        self.mcp.serve(stdin, stdout)
        out = [json.loads(ln) for ln in stdout.getvalue().splitlines() if ln.strip()]
        # The valid ping after the garbage line still gets answered.
        pings = [o for o in out if o.get("id") == 11]
        self.assertEqual(pings[0]["result"], {})

    # -- full round-trip works with NO mcp module present -------------------
    def test_protocol_round_trip_without_mcp(self):
        saved = sys.modules.get("mcp")
        sys.modules["mcp"] = None      # poison `import mcp`
        try:
            out = self._drive([
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            ])
            self.assertEqual(out[0]["result"]["serverInfo"]["name"], "task-station")
            self.assertTrue(out[1]["result"]["tools"])
            # The server never imported the SDK.
            self.assertIsNone(sys.modules["mcp"])
        finally:
            if saved is not None:
                sys.modules["mcp"] = saved
            else:
                sys.modules.pop("mcp", None)


if __name__ == "__main__":
    unittest.main()
