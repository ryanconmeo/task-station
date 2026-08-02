"""HarnessAdapter seam (#463). Phase 3: ClaudeAdapter.spawn_cmd is the `claude
--bg` background-worker command (no -p/stream-json/session-id); the legacy `-p`
base now lives in delegate._build_worker_cmd (see test_delegate.BuildWorkerCmdTest).
Covers the --bg argv shape, resume flag, capability flags, get_adapter routing, and
the CodexAdapter stub."""
import os
import sys
import unittest

# harness.py lives in lib/delegate/ (same bootstrap as tests/test_delegate.py).
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "lib", "delegate"
))
import harness  # noqa: E402


class SpawnCmdBgShapeTest(unittest.TestCase):
    def test_bg_cmd_shape(self):
        # Default is dontAsk (fail-closed, no dangerous-skip) + the author-only edit
        # toolset via --allowedTools (dontAsk won't auto-approve edits). --allowedTools
        # is terminated by the following --name so the positional brief isn't swallowed.
        cmd = harness.ClaudeAdapter().spawn_cmd("do it", name="wk-x", model="sonnet")
        self.assertEqual(cmd, ["claude", "--bg", "--permission-mode", "dontAsk",
                               "--allowedTools", "Read", "Grep", "Glob", "LS",
                               "Edit", "Write", "MultiEdit", "NotebookEdit", "TodoWrite",
                               "--name", "wk-x", "--model", "sonnet", "do it"])

    def test_dontask_grants_edit_tools_not_bash_or_network(self):
        cmd = harness.ClaudeAdapter().spawn_cmd("do it", name="wk-x")
        self.assertIn("dontAsk", cmd)
        self.assertIn("Edit", cmd); self.assertIn("Write", cmd)
        self.assertNotIn("Bash", cmd); self.assertNotIn("WebFetch", cmd)
        self.assertNotIn("bypassPermissions", cmd)

    def test_bg_cmd_has_no_print_or_stream_json(self):
        cmd = harness.ClaudeAdapter().spawn_cmd("b", name="n", model="sonnet")
        self.assertIn("--bg", cmd)
        for banned in ("-p", "--print", "--output-format", "--verbose",
                       "--session-id"):
            self.assertNotIn(banned, cmd)

    def test_permission_mode_override(self):
        cmd = harness.ClaudeAdapter().spawn_cmd("b", permission_mode="acceptEdits")
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "acceptEdits")

    def test_resume_uses_resume_flag_not_session_id(self):
        cmd = harness.ClaudeAdapter().spawn_cmd("t", session_id="sid-1", resume=True)
        self.assertIn("--resume", cmd)
        self.assertEqual(cmd[cmd.index("--resume") + 1], "sid-1")
        self.assertNotIn("--session-id", cmd)

    def test_session_id_without_resume_is_ignored(self):
        # --bg mints its own id, so a non-resume session_id must NOT be passed.
        cmd = harness.ClaudeAdapter().spawn_cmd("t", session_id="sid-1", resume=False)
        self.assertNotIn("sid-1", cmd)
        self.assertNotIn("--resume", cmd)

    def test_brief_is_last(self):
        cmd = harness.ClaudeAdapter().spawn_cmd("the brief", name="n")
        self.assertEqual(cmd[-1], "the brief")


class AdapterRegistryTest(unittest.TestCase):
    def test_unknown_harness_exits(self):
        with self.assertRaises(SystemExit):
            harness.get_adapter("gemini")

    def test_codex_spawn_cmd_is_codex_exec_json(self):
        # Phase 6: CodexAdapter is real — `codex exec --json` (UNVERIFIED flags),
        # NOT a stub that exits, and NOT a claude/--bg command.
        cmd = harness.CodexAdapter().spawn_cmd("do it", model="gpt-5-codex")
        self.assertEqual(cmd[:4], ["codex", "exec", "--json", "--skip-git-repo-check"])
        self.assertEqual(cmd[-1], "do it")
        self.assertIn("-m", cmd)
        for banned in ("--bg", "claude", "-p", "--session-id"):
            self.assertNotIn(banned, cmd)

    def test_claude_caps_true(self):
        a = harness.get_adapter("claude")
        self.assertTrue(a.supports_bg)
        self.assertTrue(a.supports_agent_view)

    def test_codex_caps_false(self):
        a = harness.get_adapter("codex")
        self.assertFalse(a.supports_bg)
        self.assertFalse(a.supports_agent_view)

    def test_base_caps_all_false(self):
        base = harness.HarnessAdapter()
        self.assertFalse(base.supports_bg)
        self.assertFalse(base.supports_agent_view)
        self.assertFalse(base.supports_named_sessions)

    def test_get_adapter_default_is_claude(self):
        self.assertEqual(harness.get_adapter(None).name, "claude")
        self.assertEqual(harness.get_adapter("codex").name, "codex")


if __name__ == "__main__":
    unittest.main()
