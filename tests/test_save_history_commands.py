"""1.57.0: `/task-station:save` and `/task-station:history` become first-class
namespaced commands (previously reachable only as `/todo save` / `/todo <n>
history` subcommands). Verifies the new command files' frontmatter + `!` lines,
and that the bare-alias install loop in on_session_start.sh now covers both.
Stdlib-only, no LLM — static file checks."""
import os
import re
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_COMMANDS = os.path.join(_REPO_ROOT, "commands")
_HOOK = os.path.join(_REPO_ROOT, "hooks", "on_session_start.sh")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class SaveCommandFileTest(unittest.TestCase):
    def setUp(self):
        self.text = _read(os.path.join(_COMMANDS, "save.md"))

    def test_frontmatter_fields(self):
        self.assertIn("description:", self.text)
        self.assertIn('argument-hint: ""', self.text)
        self.assertIn("allowed-tools: Bash", self.text)
        self.assertIn("disable-model-invocation: true", self.text)

    def test_bang_line_renders_save(self):
        self.assertIn('render --arg save --session', self.text)
        self.assertIn("task-station.py", self.text)

    def test_body_orchestrates_the_flow_rather_than_handing_over_a_checklist(self):
        """2.16.0 rewrote save.md the way heal's skill was rewritten: it PRESCRIBES the
        order — read the gap report, fill only what is missing, present truth, decisions,
        one log line, the mechanical cold-read check, confirm — instead of handing the
        model a checklist and leaving it to hand-assemble a multi-flag `update`."""
        for phrase in ("GAP REPORT", "AMENDMENT, not a rewrite", "PRESENT TRUTH",
                       "--supersedes", "--log", "COLD-READ CHECK", "NOT** pin"):
            self.assertIn(phrase, self.text)
        # the seven numbered steps, in order
        for n in range(1, 8):
            self.assertIn("### %d." % n, self.text)
        # it must not re-ask for the digest, and must name the escape hatch that does
        self.assertIn("/todo save --verbose", self.text)
        self.assertIn("/todo save --check", self.text)
        # the two guarantees the flow depends on
        self.assertIn("--restore-summary", self.text)
        self.assertIn("last_full_save_ts", self.text)
        self.assertIn("/todo <n> history", self.text)
        self.assertIn("/todo <n> -s", self.text)


class HistoryCommandFileTest(unittest.TestCase):
    def setUp(self):
        self.text = _read(os.path.join(_COMMANDS, "history.md"))

    def test_frontmatter_fields(self):
        self.assertIn("description:", self.text)
        self.assertIn("argument-hint:", self.text)
        self.assertIn("allowed-tools: Bash", self.text)
        self.assertIn("disable-model-invocation: true", self.text)

    def test_bang_line_forwards_arguments_and_appends_history(self):
        # The typed arguments reach the shell as the value of TS_ARGV, never as
        # shell source — see tests/test_command_arg_quoting.py. The route word
        # is still appended to whatever the user typed.
        self.assertIn("<<'TS_ARGV_END'", self.text)
        self.assertIn('${TS_ARGV:+$TS_ARGV }history', self.text)
        self.assertIn("task-station.py", self.text)

    def test_body_is_read_only(self):
        self.assertIn("READ-ONLY", self.text)
        self.assertIn("verbatim", self.text)


class BareAliasLoopTest(unittest.TestCase):
    def test_loop_includes_save_and_history(self):
        text = _read(_HOOK)
        m = re.search(r"for c in ([^;]+); do", text)
        self.assertIsNotNone(m, "expected a `for c in ...; do` bare-alias loop")
        # Strip shell quoting: `'done'` is quoted in the script to appease
        # shellcheck SC1010, but it is the same word to bash.
        members = [w.strip("'\"") for w in m.group(1).split()]
        for name in ("todo", "done", "repos", "pin", "save", "history"):
            self.assertIn(name, members)


if __name__ == "__main__":
    unittest.main()
