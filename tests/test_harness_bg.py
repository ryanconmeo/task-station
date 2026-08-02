"""Phase 3.1 (#463): ClaudeAdapter --bg launch-id capture + `claude agents --json`
status, exercised with a fake `claude` PATH shim (no real --bg in tests). The shim
is the reusable test double for the whole bg phase."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "lib", "delegate"
))
import harness  # noqa: E402

FULL_SID = "0a623186-77be-4846-9e4e-222485b92871"

# Realistic shim (spike-verified): --bg prints a SHORT 8-hex id wrapped in ANSI
# color and registers the FULL uuid in `agents --json`, of which the short id is
# the prefix. The adapter must ANSI-strip, capture the short id, and canonicalize
# it to the full sessionId.
SHIM = r'''#!/usr/bin/env bash
# fake `claude` for --bg tests. State dir: $FAKE_CLAUDE_DIR
case "$1" in
  --bg)
    printf 'backgrounded \302\267 \033[36m0a623186\033[39m \302\267 wk-test\n'
    cat > "$FAKE_CLAUDE_DIR/agents.json" <<EOF
[{"sessionId":"0a623186-77be-4846-9e4e-222485b92871","status":"busy","pid":4242,"name":"wk-test","cwd":"$PWD","kind":"background","startedAt":"2026-07-22T00:00:00Z"}]
EOF
    ;;
  agents) cat "$FAKE_CLAUDE_DIR/agents.json" 2>/dev/null || echo "[]";;
  *) exit 64;;
esac
'''

# --bg refusing bypassPermissions until the machine accepts the disclaimer once.
SHIM_DISCLAIMER = r'''#!/usr/bin/env bash
case "$1" in
  --bg) echo "--bg with bypassPermissions requires accepting the disclaimer first. Run \`claude --dangerously-skip-permissions\` once interactively." >&2; exit 1;;
  agents) echo "[]";;
  *) exit 64;;
esac
'''


class BgSpawnTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-bg-")
        bindir = os.path.join(self.tmp, "bin"); os.makedirs(bindir)
        shim = os.path.join(bindir, "claude")
        with open(shim, "w") as f:
            f.write(SHIM)
        os.chmod(shim, 0o755)
        self._path = os.environ.get("PATH", "")
        os.environ["PATH"] = bindir + os.pathsep + self._path
        os.environ["FAKE_CLAUDE_DIR"] = self.tmp

    def tearDown(self):
        os.environ["PATH"] = self._path
        os.environ.pop("FAKE_CLAUDE_DIR", None)

    def test_spawn_canonicalizes_short_ansi_id_to_full(self):
        # real print = ANSI-wrapped SHORT id; spawn must strip ANSI, capture it,
        # and canonicalize to the FULL sessionId the agents list keys on.
        sid = harness.ClaudeAdapter().spawn_worker("brief", self.tmp, name="wk-test")
        self.assertEqual(sid, FULL_SID)

    def test_worker_status_resolves_short_prefix(self):
        with open(os.path.join(self.tmp, "agents.json"), "w") as f:
            json.dump([{"sessionId": FULL_SID, "status": "busy", "pid": 4242,
                        "name": "wk-test", "cwd": self.tmp, "kind": "background",
                        "startedAt": "2026-07-22T00:00:00Z"}], f)
        # a stored SHORT id still resolves via unique-prefix fallback
        st = harness.ClaudeAdapter().worker_status("0a623186")
        self.assertEqual(st["state"], "busy")
        self.assertEqual(st["pid"], 4242)

    def test_bypass_disclaimer_raises_actionable(self):
        bindir = os.path.join(self.tmp, "bin")
        with open(os.path.join(bindir, "claude"), "w") as f:
            f.write(SHIM_DISCLAIMER)
        os.chmod(os.path.join(bindir, "claude"), 0o755)
        with self.assertRaises(SystemExit) as cm:
            harness.ClaudeAdapter().spawn_worker(
                "brief", self.tmp, name="wk", permission_mode="bypassPermissions")
        self.assertIn("dangerously-skip-permissions", str(cm.exception))

    def test_spawn_raises_when_no_id_printed(self):
        # A shim that prints nothing on --bg → SystemExit (nothing to track).
        bindir = os.path.join(self.tmp, "bin")
        with open(os.path.join(bindir, "claude"), "w") as f:
            f.write("#!/usr/bin/env bash\nexit 0\n")
        os.chmod(os.path.join(bindir, "claude"), 0o755)
        with self.assertRaises(SystemExit):
            harness.ClaudeAdapter().spawn_worker("brief", self.tmp, name="wk")

    def test_agents_index_and_status(self):
        with open(os.path.join(self.tmp, "agents.json"), "w") as f:
            json.dump([{"sessionId": "s1", "status": "busy", "pid": 4242,
                        "name": "wk-test", "cwd": self.tmp, "kind": "background",
                        "startedAt": "2026-07-22T00:00:00Z"}], f)
        ad = harness.ClaudeAdapter()
        idx = ad.agents_index()
        self.assertIn("s1", idx)
        st = ad.worker_status("s1")
        self.assertEqual(st["state"], "busy")
        self.assertEqual(st["pid"], 4242)
        self.assertEqual(ad.worker_status("nope")["state"], "gone")

    def test_agents_index_empty_on_bad_json(self):
        with open(os.path.join(self.tmp, "agents.json"), "w") as f:
            f.write("not json")
        self.assertEqual(harness.ClaudeAdapter().agents_index(), {})


if __name__ == "__main__":
    unittest.main()
