"""brain.hooks.guard — the secret-guard PreToolUse(Bash) hook.

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 5a) from the brain source tree's
``tests/test_guard.py`` @ 0.14.0. 7 of its 8 cases port 1:1, driven exactly as
the source drove them — the guard as a SUBPROCESS with a PreToolUse hook-JSON
payload on stdin, which is how Claude Code invokes it. The 8th
(``test_hooks_json_registers_guard_on_bash``) is **DEFERRED**: it asserts the
source plugin's ``hooks/hooks.json`` wiring, and this repo's ``hooks/hooks.json``
has no PreToolUse block yet — hook REGISTRATION is Phase 5's, and a test written
now would either fail or assert markup nobody ships. Named in the chunk-5a
handoff rather than faked.

By-path (not ``-m``) is deliberate here: guard.py imports nothing but the stdlib,
so it stays runnable as a plain file, which is the cheapest thing for Phase 5 to
wire. ``McpHooksLayeringTest`` pins that property from the other side.

ADDED: a behavioural matrix over :func:`brain.hooks.guard.detect` — the real
match logic, both directions (what must block, what must pass), including the
context gate that keeps ordinary high-entropy data out of the deny path, and the
claim that a deny reason never echoes the secret it matched.
"""
import json
import subprocess
import sys
import unittest

from tests.brain.base import LIB

import brain.hooks.guard as guard

GUARD = LIB / "brain/hooks/guard.py"

# A syntactically real JWT (header.payload.signature), not a live credential.
JWT = (
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
    "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
)


class GuardHookTest(unittest.TestCase):
    # --- helpers -----------------------------------------------------------
    def _run(self, stdin_text):
        """Invoke the guard with raw stdin; return (returncode, stdout)."""
        r = subprocess.run(
            [sys.executable, str(GUARD)],
            input=stdin_text, capture_output=True, text=True,
        )
        return r.returncode, r.stdout

    def _run_bash(self, command):
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
        return self._run(payload)

    def assertAllows(self, command):
        code, out = self._run_bash(command)
        self.assertEqual(code, 0, f"exit {code} for {command!r}")
        self.assertEqual(out.strip(), "", f"unexpected output for {command!r}: {out!r}")

    def assertDenies(self, command):
        code, out = self._run_bash(command)
        self.assertEqual(code, 0, f"guard must always exit 0 (got {code})")
        data = json.loads(out)  # deny output must be valid JSON
        hso = data["hookSpecificOutput"]
        self.assertEqual(hso["hookEventName"], "PreToolUse")
        self.assertEqual(hso["permissionDecision"], "deny")
        self.assertTrue(hso["permissionDecisionReason"].strip())
        return data

    # --- (A) secret literals -----------------------------------------------
    def test_jwt_literal_denies(self):
        self.assertDenies(f'echo "auth: Bearer {JWT}"')

    # --- (B) secret-reading commands ---------------------------------------
    def test_get_access_token_uncaptured_denies(self):
        self.assertDenies("az account get-access-token --query accessToken -o tsv | cat")

    def test_get_access_token_captured_in_var_allows(self):
        self.assertAllows('TOK=$(az account get-access-token --query accessToken -o tsv)')

    def test_output_none_allows(self):
        self.assertAllows("az account get-access-token -o none")

    # --- fail-open & benign -------------------------------------------------
    def test_malformed_json_allows_fail_open(self):
        code, out = self._run("{ not valid json ")
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "")

    def test_benign_command_allows(self):
        self.assertAllows("git status && ls -la /tmp")

    def test_deny_output_is_valid_hook_json(self):
        data = self.assertDenies(f'curl -H "Authorization: Bearer {JWT}" https://x')
        # round-trips and carries the canonical deny shape
        self.assertIn("permissionDecision", data["hookSpecificOutput"])

    # --- ADDED: the reason must not become the leak -------------------------
    def test_deny_reason_never_echoes_the_secret(self):
        data = self.assertDenies(f'echo "auth: Bearer {JWT}"')
        reason = data["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertNotIn(JWT, reason)
        self.assertNotIn(JWT.split(".")[1], reason)   # not even the payload segment

    # --- ADDED: the guard only speaks for Bash ------------------------------
    def test_a_non_bash_tool_is_never_inspected(self):
        payload = json.dumps({"tool_name": "Write",
                              "tool_input": {"command": f"echo {JWT}"}})
        code, out = self._run(payload)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "")

    def test_an_empty_command_allows(self):
        code, out = self._run(json.dumps({"tool_name": "Bash", "tool_input": {"command": "   "}}))
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "")


class DetectMatrixTest(unittest.TestCase):
    """ADDED — the match logic itself, in process, both directions. The source
    exercised four commands end to end; these pin WHY each side of the line falls
    where it does, which is what makes a later regex edit reviewable."""

    def assertBlocked(self, cmd):
        hit = guard.detect(cmd)
        self.assertIsNotNone(hit, f"should have been blocked: {cmd!r}")
        return hit

    def assertPassed(self, cmd):
        self.assertIsNone(guard.detect(cmd), f"should have passed: {cmd!r}")

    # (A) literal values behind a secret-bearing flag
    def test_literal_token_flag_value_blocks(self):
        self.assertBlocked("swa deploy --deployment-token abcdef0123456789abcdef")

    def test_shell_variable_flag_value_passes(self):
        self.assertPassed('swa deploy --deployment-token "$TOK"')

    def test_file_ref_flag_value_passes(self):
        self.assertPassed("cmd --token @secrets.txt")

    def test_path_or_url_flag_value_passes(self):
        self.assertPassed("cmd --value /etc/hosts")
        self.assertPassed("cmd --value https://example.invalid/whatever/long/path")

    def test_short_flag_value_passes(self):
        self.assertPassed("cmd --token abc123")      # under the 16-char floor

    # the entropy blob + its context gate
    def test_blob_next_to_a_secret_keyword_blocks(self):
        blob = "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0"   # 40 chars, 3 classes
        self.assertBlocked(f"curl -H 'X-Api-Key: {blob}' https://example.invalid")

    def test_blob_with_no_secret_keyword_nearby_passes(self):
        blob = "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0"
        self.assertPassed(f"git cat-file -p {blob}")

    def test_low_entropy_run_passes(self):
        self.assertPassed("echo token " + "a" * 60)   # one char class, not a secret

    def test_a_long_path_is_not_a_blob(self):
        # '/' is excluded from the blob alphabet precisely so paths break up
        self.assertPassed("ls -la /very/long/directory/name/that/keeps/going/token/here")

    # (B) secret reads, contained or not
    def test_keyvault_secret_show_uncontained_blocks(self):
        self.assertBlocked("az keyvault secret show --vault-name v -n n")

    def test_keyvault_secret_show_with_output_none_passes(self):
        self.assertPassed("az keyvault secret show --vault-name v -n n --output none")

    def test_secrets_list_uncontained_blocks(self):
        self.assertBlocked("az webapp config appsettings secrets list -o tsv")

    def test_backtick_capture_passes(self):
        self.assertPassed("TOK=`az account get-access-token -o tsv`")

    def test_reason_strings_are_stable(self):
        """The four reasons are user-visible and are what a blocked user reads;
        they name the detection, never the match."""
        self.assertEqual(self.assertBlocked(f"echo {JWT}"), "JWT literal in command")
        self.assertEqual(self.assertBlocked("cmd --password hunter2hunter2hunter2"),
                         "opaque token passed as a literal flag value")
        self.assertEqual(self.assertBlocked("az account get-access-token -o tsv"),
                         "secret-reading command without --output none / variable capture")


if __name__ == "__main__":
    unittest.main()
