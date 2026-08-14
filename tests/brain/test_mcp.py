"""brain.mcp_tools — the brain MCP tool layer, driven as a real stdio subprocess.

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 5a) from the brain source tree's
``tests/test_mcp.py`` @ 0.14.0. All 7 source cases port; one is REWRITTEN and the
rest change only in how the process is started.

  * The server is spawned as ``python3 -m brain.mcp_tools`` with ``lib/`` on
    ``PYTHONPATH`` (the source ran ``scripts/mcp_server.py`` by path, which a
    package module with relative imports cannot do), and the child carries
    ``PINNED_ENV`` so its ``data_dir()`` cannot escape this test's temp home (D62).
  * ``test_serverinfo_version_from_manifest`` no longer reads the repo's own
    manifest. The port resolves the version through ``$CLAUDE_PLUGIN_ROOT``, so
    the case now writes a manifest with a known version into the temp home and
    asserts the server reports THAT — a hermetic check of the same claim, plus an
    ADDED case for the unset/absent fallback the source could not reach.

ADDED beyond the source: an in-process class over :func:`brain.mcp_tools.handle`
(the message shapes a subprocess makes expensive to enumerate — notifications,
an unknown tool name, the tool-name wire contract Phase 5 mounts) and the
chunk-5a layering guard.
"""
import ast
import json
import os
import subprocess
import sys
import unittest

from tests.brain.base import BrainTestCase, LIB, PINNED_ENV
from tests.brain.test_layers import STDLIB_OK, top_level_imports

import brain.config as bconfig
import brain.mcp_tools as mcp

MANIFEST_VERSION = "9.9.9-test"


class ServerProcessMixin(BrainTestCase):
    """A real ``-m brain.mcp_tools`` child, talking newline-delimited JSON-RPC."""

    #: written into the child's CLAUDE_PLUGIN_ROOT; None = leave the var unset
    plugin_version = MANIFEST_VERSION

    def setUp(self):
        super().setUp()
        self.vault = self.make_vault(self.home / "vault")
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["PYTHONPATH"] = str(LIB)
        for k, rel in PINNED_ENV.items():
            env[k] = str(self.home / rel)
        env["TASK_STATION_BRAIN_VAULT"] = str(self.vault)
        env.pop("CLAUDE_PLUGIN_ROOT", None)
        if self.plugin_version is not None:
            root = self.home / "plugin-root"
            (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
            (root / ".claude-plugin/plugin.json").write_text(
                json.dumps({"name": "task-station", "version": self.plugin_version}))
            env["CLAUDE_PLUGIN_ROOT"] = str(root)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "brain.mcp_tools"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=env,
        )
        self.addCleanup(self._shutdown)
        self._id = 0

    def _shutdown(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    def _notify(self, method, params=None):
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method,
                                          "params": params or {}}) + "\n")
        self.proc.stdin.flush()

    def _rpc(self, method, params=None):
        self._id += 1
        rid = self._id
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": rid,
                                          "method": method, "params": params or {}}) + "\n")
        self.proc.stdin.flush()
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise AssertionError(
                    f"server closed stdout awaiting id {rid}; stderr:\n{self.proc.stderr.read()}")
            msg = json.loads(line)
            if msg.get("id") == rid:
                return msg

    def _initialize(self, proto="2025-06-18"):
        r = self._rpc("initialize", {"protocolVersion": proto, "capabilities": {}})
        self._notify("notifications/initialized")
        return r["result"]


class MCPServerTest(ServerProcessMixin):
    def test_initialize_negotiates_supported_protocol(self):
        result = self._initialize("2025-06-18")
        self.assertEqual(result["protocolVersion"], "2025-06-18")

    def test_initialize_rejects_unknown_protocol(self):
        result = self._initialize("1999-01-01")  # unsupported -> newest supported
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        self.assertNotEqual(result["protocolVersion"], "1999-01-01")

    def test_serverinfo_version_from_manifest(self):
        result = self._initialize()
        self.assertEqual(result["serverInfo"]["version"], MANIFEST_VERSION)
        self.assertEqual(result["serverInfo"]["name"], "brain")

    def test_tools_list(self):
        self._initialize()
        result = self._rpc("tools/list")["result"]
        names = {t["name"] for t in result["tools"]}
        self.assertIn("brain_save", names)
        self.assertIn("brain_search", names)

    def test_brain_save_create_then_update_round_trip(self):
        # The slug carries a REGISTERED domain (`repo`). The source's fixture slug
        # did not need one; this repo's write path derives `area:` from the slug's
        # domain and REFUSES an unstamped node (chunk 2's knowledge stamp), so an
        # arbitrary two-word slug is now an isError result, not a note. Same
        # round-trip, a slug the contract accepts.
        self._initialize()
        r1 = self._rpc("tools/call", {"name": "brain_save", "arguments": {
            "slug": "repo-my-fact", "description": "first", "body": "ORIGINAL-BODY-MARKER"}})
        self.assertNotIn("isError", r1["result"])
        self.assertIn("created", r1["result"]["content"][0]["text"])

        r2 = self._rpc("tools/call", {"name": "brain_save", "arguments": {
            "slug": "repo-my-fact", "description": "second", "body": "an update fact"}})
        self.assertIn("updated", r2["result"]["content"][0]["text"])

        text = (self.vault / "notes/repo-my-fact.md").read_text()
        self.assertIn("ORIGINAL-BODY-MARKER", text)   # create body preserved on update
        self.assertIn("## Updates", text)
        self.assertIn("an update fact", text)

    def test_traversal_slug_is_error(self):
        self._initialize()
        r = self._rpc("tools/call", {"name": "brain_save", "arguments": {
            "slug": "../../etc/evil", "description": "x", "body": "x"}})
        self.assertTrue(r["result"].get("isError"))
        self.assertFalse((self.home / "etc/evil.md").exists())

    def test_unknown_method_is_minus_32601(self):
        self._initialize()
        r = self._rpc("nonexistent/method")
        self.assertEqual(r["error"]["code"], -32601)

    def test_the_child_wrote_into_this_tests_home(self):
        """The temp-home pin actually held: a save landed in the fixture vault and
        the LOG line went with it. Without PINNED_ENV in the child env the write
        still 'succeeds' — into the developer's real state dir."""
        self._initialize()
        self._rpc("tools/call", {"name": "brain_save", "arguments": {
            "slug": "repo-pinned-home", "description": "d", "body": "b"}})
        self.assertTrue((self.vault / "notes/repo-pinned-home.md").exists())
        self.assertIn("repo-pinned-home", (self.vault / "LOG.md").read_text())


class ServerVersionFallbackTest(ServerProcessMixin):
    """ADDED — no plugin root in the environment ⇒ the documented '0.0.0', never a
    crash and never a stale literal. The source read its own repo manifest through
    ``__file__`` math and so had no unset case to test."""

    plugin_version = None

    def test_version_falls_back_when_no_plugin_root(self):
        result = self._initialize()
        self.assertEqual(result["serverInfo"]["version"], "0.0.0")


class HandleShapeTest(BrainTestCase):
    """ADDED — in-process message shapes. These are the paths a subprocess makes
    expensive to enumerate, and three of them are contracts Phase 5 inherits when
    it mounts these tools on the board's bridge."""

    def test_notifications_get_no_reply(self):
        self.assertIsNone(mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_unknown_method_without_an_id_is_silent(self):
        # a notification the protocol says gets no reply, even when unrecognized
        self.assertIsNone(mcp.handle({"jsonrpc": "2.0", "method": "nope/nope"}))

    def test_ping_is_an_empty_result(self):
        self.assertEqual(mcp.handle({"jsonrpc": "2.0", "id": 3, "method": "ping"}),
                         {"jsonrpc": "2.0", "id": 3, "result": {}})

    def test_unknown_tool_is_minus_32602(self):
        r = mcp.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                        "params": {"name": "brain_nope", "arguments": {}}})
        self.assertEqual(r["error"]["code"], -32602)
        self.assertIn("brain_nope", r["error"]["message"])

    def test_a_tool_that_raises_is_a_result_not_a_protocol_error(self):
        r = mcp.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                        "params": {"name": "brain_save", "arguments": {}}})  # missing keys
        self.assertNotIn("error", r)
        self.assertTrue(r["result"]["isError"])
        self.assertIn("error:", r["result"]["content"][0]["text"])

    def test_tool_names_and_handlers_agree(self):
        """The wire contract Phase 5 mounts. Names are byte-identical to the
        source's; a rename belongs to Phase 5, not to this port."""
        self.assertEqual([t["name"] for t in mcp.TOOLS],
                         ["brain_search", "brain_status", "brain_save",
                          "brain_log", "brain_recent_tasks"])
        self.assertEqual(set(mcp.HANDLERS), {t["name"] for t in mcp.TOOLS})
        for t in mcp.TOOLS:
            self.assertEqual(set(t), {"name", "description", "inputSchema"})
            self.assertEqual(t["inputSchema"]["type"], "object")

    def test_protocol_negotiation_never_echoes_an_unknown_version(self):
        self.assertEqual(mcp._negotiate_protocol("2024-11-05"), "2024-11-05")
        self.assertEqual(mcp._negotiate_protocol("1999-01-01"), mcp.SUPPORTED_PROTOCOLS[0])
        self.assertEqual(mcp._negotiate_protocol(None), mcp.SUPPORTED_PROTOCOLS[0])


class ImportTimeConfigTest(BrainTestCase):
    """ADDED — the import-time config load is LOAD-BEARING (a hub ruling, not an
    accident). It lives in ``brain.search`` and the tools read it from there; if
    someone makes it lazy, ``search._CFG`` stops existing and this fails, which is
    the intended alarm rather than a silently different config per call."""

    def test_the_tools_read_the_config_search_resolved_at_import(self):
        # `search.config` IS `brain.config` — the ruling's rewrite of
        # `brain.pb_config.require_valid()` reaches the one config module, not a copy.
        self.assertIs(mcp.search.config, bconfig)
        self.assertIsInstance(mcp.search._CFG, dict)
        self.assertIn("vault", mcp.search._CFG)


class McpHooksLayeringTest(unittest.TestCase):
    """ADDED — the layer rule for the nine modules chunk 5a adds, read with
    ``ast`` (chunk 1's reader, which also sees function-local imports). Each chunk
    keeps its own copy so its claims stay reviewable on their own. Relative
    imports (``from . import config``, ``from .. import search``) are invisible to
    the reader by design — they cannot cross a layer.

    FOUR names are new to the brain plane with this chunk, so they are widened
    LOCALLY (chunk 4a's ``HealLayeringTest.OK = STDLIB_OK | {"platform"}``
    precedent) rather than added to chunk 1's list — chunk 1's guard should keep
    saying exactly what chunk 1 reviewed:

      ``shlex``      — mcp_tools, so a quoted MCP query is ONE search term
      ``base64``     — ado_tree, the PAT -> HTTP Basic header
      ``html``       — ado_tree, unescaping ADO's HTML description fields
      ``__future__`` — the ado pair's ``annotations`` import, which is what lets
                       both files use ``X | None`` annotations on Python 3.9
    """

    FILES = ("brain/mcp_tools.py", "brain/init_home.py", "brain/ado_tree.py",
             "brain/ado_resolve.py", "brain/hooks/__init__.py",
             "brain/hooks/inject.py", "brain/hooks/guard.py",
             "brain/hooks/gate.py", "brain/hooks/distill.py")
    OK = STDLIB_OK | {"shlex", "base64", "html", "__future__"}

    def test_no_module_reaches_the_board(self):
        for rel in self.FILES:
            self.assertNotIn("board", top_level_imports(LIB / rel), rel)

    def test_each_module_reaches_only_core_and_stdlib(self):
        for rel in self.FILES:
            path = LIB / rel
            self.assertTrue(path.exists(), f"{rel} missing")
            extra = top_level_imports(path) - self.OK - {"core"}
            self.assertEqual(extra, set(), f"{rel} reaches outside core+stdlib: {sorted(extra)}")

    def test_the_local_widening_is_exactly_what_this_chunk_needs(self):
        """A widening nobody re-checks becomes a hole. Every name added above must
        still be used by one of these files, and no more than that."""
        used = set()
        for rel in self.FILES:
            used |= top_level_imports(LIB / rel)
        self.assertEqual(self.OK - STDLIB_OK, (self.OK - STDLIB_OK) & used,
                         "a widened name is no longer used — drop it")

    def test_only_mcp_tools_reaches_core(self):
        """``core.jsonrpc`` is this chunk's one core edge, and it is deliberate:
        the transport is shared with the board's bridge. Everything else is
        stdlib + siblings."""
        reaching = sorted(rel for rel in self.FILES
                          if "core" in top_level_imports(LIB / rel))
        self.assertEqual(reaching, ["brain/mcp_tools.py"])

    def test_the_guard_hook_has_no_imports_at_all_beyond_stdlib(self):
        """guard.py stays runnable BY PATH (no relative imports), because that is
        how a PreToolUse hook is cheapest to wire. A relative import here would
        take that away silently."""
        tree = ast.parse((LIB / "brain/hooks/guard.py").read_text())
        relative = [n for n in ast.walk(tree)
                    if isinstance(n, ast.ImportFrom) and n.level]
        self.assertEqual(relative, [], "guard.py grew a relative import")
        self.assertEqual(top_level_imports(LIB / "brain/hooks/guard.py") - STDLIB_OK, set())


if __name__ == "__main__":
    unittest.main()
