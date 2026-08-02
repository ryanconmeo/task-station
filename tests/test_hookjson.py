"""lib/hookjson.py — the jq-free stdin JSON field extractor the hooks use in place
of `jq -r '.path // default'`. Driven as a subprocess (same style as
test_statusline's host-script tests) so we exercise the real CLI contract the
hooks depend on: dotted paths, `// default` fallback, and a SILENT no-op (empty
output, exit 0) on absent / malformed / non-string input."""
import os
import subprocess
import sys
import unittest

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
SCRIPT = os.path.join(LIB, "hookjson.py")


def _run(payload, *args):
    """Feed `payload` on stdin, return (stdout, returncode)."""
    p = subprocess.run([sys.executable, SCRIPT, *args],
                       input=payload, capture_output=True, text=True)
    return p.stdout, p.returncode


class HookJson(unittest.TestCase):
    def test_simple_field(self):
        out, rc = _run('{"session_id": "abc123"}', "session_id")
        self.assertEqual(out, "abc123")
        self.assertEqual(rc, 0)

    def test_no_trailing_newline(self):
        # jq -r adds a newline but $(...) strips it; a PIPED value (compact_summary)
        # must pass through verbatim, so we add none.
        out, _ = _run('{"session_id": "abc"}', "session_id")
        self.assertEqual(out, "abc")

    def test_missing_field_uses_default(self):
        out, rc = _run('{"other": 1}', "session_id", "unknown")
        self.assertEqual(out, "unknown")
        self.assertEqual(rc, 0)

    def test_missing_field_no_default_is_empty(self):
        out, rc = _run('{"other": 1}', "session_id")
        self.assertEqual(out, "")
        self.assertEqual(rc, 0)

    def test_null_value_uses_default(self):
        # jq's `// default` also fires on null; a null value must fall to the default.
        out, _ = _run('{"session_id": null}', "session_id", "unknown")
        self.assertEqual(out, "unknown")

    def test_non_string_value_uses_default(self):
        out, _ = _run('{"n": 5}', "n", "def")
        self.assertEqual(out, "def")

    def test_nested_path(self):
        out, _ = _run('{"tool_input": {"file_path": "/tmp/x.py"}}', "tool_input.file_path")
        self.assertEqual(out, "/tmp/x.py")

    def test_nested_missing_uses_default(self):
        out, _ = _run('{"tool_input": {}}', "tool_input.file_path")
        self.assertEqual(out, "")

    def test_nested_parent_not_object_uses_default(self):
        out, _ = _run('{"tool_input": "oops"}', "tool_input.file_path", "d")
        self.assertEqual(out, "d")

    def test_malformed_json_is_silent_default(self):
        out, rc = _run("not json at all", "session_id", "unknown")
        self.assertEqual(out, "unknown")
        self.assertEqual(rc, 0)          # never raises, always exit 0

    def test_empty_stdin_is_silent_default(self):
        out, rc = _run("", "session_id", "unknown")
        self.assertEqual(out, "unknown")
        self.assertEqual(rc, 0)

    def test_multiline_value_preserved(self):
        # The compaction summary is piped verbatim into post-compact; newlines survive.
        payload = '{"compact_summary": "line one\\nline two"}'
        out, _ = _run(payload, "compact_summary")
        self.assertEqual(out, "line one\nline two")


if __name__ == "__main__":
    unittest.main()
