# Slash-command arguments reach a shell, and the harness does not escape them.
#
# A command file's `!` block is a SHELL COMMAND that Claude Code runs before the
# prompt is handed to the model. `$ARGUMENTS` is spliced into that command as
# PLAIN TEXT — no quoting, no escaping — and only then is the result executed.
# Claude Code says so itself: its Gemini-command importer refuses to translate a
# prompt whose shell block contains an argument placeholder, because "Gemini
# shell-escapes `{{args}}` inside `!{…}`, Claude Code's `$ARGUMENTS` substitution
# doesn't, so importing would let typed arguments inject shell commands."
#
# So whatever the user types is shell source. Two things follow, and both were
# true of every task-station command before this file existed:
#
#   1. `/repos don't` was a SYNTAX ERROR. The apostrophe opened a quote nothing
#      closed, the shell refused the whole line, and the user got an eval message
#      about an unmatched quote instead of their repo index.
#   2. ``/todo `whoami` `` and `/todo $(whoami)` EXECUTED. Command substitution
#      fires inside double quotes, so quoting the interpolation was never enough.
#
# The fix is a quoted heredoc (`<<'TS_ARGV_END'`), the one shell construct that
# expands nothing at all: the typed text becomes the value of a variable, and a
# variable's value is never re-scanned for backticks, `$(`, quotes or `;`. It is
# the same reasoning that put `--flag @PATH` / `--flag -` in board.prose_input —
# a shell word is not a string, it is a string the shell has already rewritten.
#
# These tests do not trust that reasoning. They REPLAY it: they reproduce Claude
# Code's own substitution and shell-block extraction (the regexes below are
# transcribed from the shipped binary), then actually run the resulting command
# under `bash`, with `python3` replaced by a shim that records its argv. A test
# passes only when the hostile text arrives at argv INTACT and the payload it
# tried to execute did not run.

import os
import pathlib
import re
import subprocess
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
COMMANDS = REPO / "commands"

# ---------------------------------------------------------------------------
# Claude Code's own behaviour, transcribed.
# ---------------------------------------------------------------------------

# `!`cmd`` inline markers must be preceded by start-of-input or whitespace, and
# their body may not contain a backtick — which is exactly why a backtick in the
# arguments used to truncate the command mid-flight.
_INLINE = re.compile(r"(?:\A|(?<=\s))!`([^`]+)`")
# The fenced form. Its body may contain backticks; only a ``` closes it.
_FENCED = re.compile(r"```!\s*\n?([\s\S]*?)\n?```")
# Ordinary markdown code spans are blanked before inline markers are scanned, so
# prose backticks cannot be mistaken for shell. A span preceded by `!` or by
# another backtick is left alone.
_SPAN = re.compile(r"`[^`\n]+`")


def _blank_code_spans(body: str) -> str:
    def repl(m: "re.Match[str]") -> str:
        prev = body[m.start() - 1] if m.start() else ""
        if prev in ("!", "`"):
            return m.group(0)
        return "`" + " " * (len(m.group(0)) - 2) + "`"

    return _SPAN.sub(repl, body)


def substitute(body: str, arguments: str) -> str:
    """What the harness does to `$ARGUMENTS`: a plain textual splice."""
    return body.replace("$ARGUMENTS", arguments)


def shell_blocks(body: str) -> list:
    """Every shell command the harness would run for this body, in its order."""
    found = [m.group(1) for m in _FENCED.finditer(body)]
    if "!`" in body:
        found += [m.group(1) for m in _INLINE.finditer(_blank_code_spans(body))]
    return [c.strip() for c in found if c and c.strip()]


# ---------------------------------------------------------------------------
# The hostile arguments. Each is a thing a person plausibly types or pastes —
# a possessive, a quoted phrase, an error message with a backtick in it.
# ---------------------------------------------------------------------------

PAYLOAD_DIR_ENV = "TS_TEST_PAYLOAD_DIR"

_T = "touch $" + PAYLOAD_DIR_ENV + "/PWNED"

HOSTILE = {
    "apostrophe": "don't",
    "double_quote": 'say "hi" now',
    # The rest each try to run `touch <payload dir>/PWNED`. If that file ever
    # appears, the typed argument executed. Note the payloads reference the
    # directory through an environment variable: it must survive to argv
    # UNEXPANDED, because a shell that expanded it is a shell that read the
    # text as source rather than as data.
    "backtick": "`" + _T + "`",
    "dollar_paren": "$(" + _T + ")",
    "semicolon": "x; " + _T,
    "all_of_it": "don't \"stop\" `" + _T + "` $(" + _T + ") ; " + _T,
}

# `#!/usr/bin/env bash`, not `/bin/sh`: argv entries are recorded NUL-separated so
# that a payload containing spaces, newlines or quotes stays one entry, and `\\0` in
# a format string is only dependable in bash — on Linux `/bin/sh` is often dash.
SHIM = """#!/usr/bin/env bash
: > "$TS_TEST_ARGV"
for a in "$@"; do printf '%s\\0' "$a" >> "$TS_TEST_ARGV"; done
echo "FAKE-TASK-STATION-OUTPUT"
exit "${TS_TEST_RC:-0}"
"""


class _Sandbox:
    """A PATH with a fake `python3` that records argv instead of running."""

    def __init__(self, tmp: pathlib.Path):
        self.tmp = tmp
        self.bin = tmp / "bin"
        self.bin.mkdir(parents=True, exist_ok=True)
        shim = self.bin / "python3"
        shim.write_text(SHIM)
        shim.chmod(0o755)
        self.argv_file = tmp / "argv"
        self.payloads = tmp / "payloads"
        self.payloads.mkdir(exist_ok=True)

    def run(self, command: str, rc: str = "0"):
        env = dict(os.environ)
        env["PATH"] = f"{self.bin}:{env['PATH']}"
        env["CLAUDE_PLUGIN_ROOT"] = str(self.tmp / "plugin")
        env["CLAUDE_SESSION_ID"] = "sess-test"
        env["CLAUDE_CODE_SESSION_ID"] = "sess-test"
        env["TS_TEST_ARGV"] = str(self.argv_file)
        env["TS_TEST_RC"] = rc
        env[PAYLOAD_DIR_ENV] = str(self.payloads)
        if self.argv_file.exists():
            self.argv_file.unlink()
        proc = subprocess.run(
            ["bash", "-c", command], env=env, capture_output=True, text=True
        )
        return proc

    def argv(self) -> list:
        if not self.argv_file.exists():
            return []
        raw = self.argv_file.read_bytes()
        return [p.decode() for p in raw.split(b"\0")[:-1]]

    def executed_payload(self) -> bool:
        return any(self.payloads.iterdir())


def _sandbox(case) -> _Sandbox:
    import tempfile

    d = tempfile.mkdtemp(prefix="ts-argquote-")
    case.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
    return _Sandbox(pathlib.Path(d))


# Every command file that splices `$ARGUMENTS` into its shell block. The audit
# that produced this list is the point: a file that grows a shell block and an
# argument placeholder without landing here is the same bug in a new file, so
# `test_the_audit_list_is_complete` re-derives it from the tree on every run.
ARG_BEARING = [
    "config.md",
    "done.md",
    "glossary.md",
    "heal.md",
    "history.md",
    "prompts.md",
    "repos.md",
    "todo.md",
    "unpin.md",
]
# Shell blocks that take no arguments at all — listed so "it has no `$ARGUMENTS`"
# stays a checked claim rather than an assumption.
ARGLESS = ["pin.md", "save.md"]


class CommandArgQuotingTests(unittest.TestCase):
    # -- the audit ---------------------------------------------------------

    def test_the_audit_list_is_complete(self):
        """Every shell-block command file is accounted for, in one list or the other."""
        with_blocks = sorted(
            p.name
            for p in COMMANDS.glob("*.md")
            if shell_blocks(p.read_text())
        )
        self.assertEqual(sorted(ARG_BEARING + ARGLESS), with_blocks)

    def test_skills_carry_no_shell_blocks(self):
        """A SKILL.md that grows a `!` block inherits this whole problem."""
        offenders = [
            str(p.relative_to(REPO))
            for p in (REPO / "skills").rglob("*.md")
            if shell_blocks(p.read_text())
        ]
        self.assertEqual([], offenders)

    def test_argless_blocks_really_are_argless(self):
        for name in ARGLESS:
            with self.subTest(name):
                self.assertNotIn("$ARGUMENTS", (COMMANDS / name).read_text())

    def test_no_block_contains_a_bang_or_a_backtick(self):
        """
        Two characters would re-open the hole from inside the block itself.
        A backtick ends the fenced body early once arguments are spliced in; a
        `!` next to a user-supplied backtick assembles a brand-new inline marker
        out of text the block never consented to run.
        """
        for name in ARG_BEARING + ARGLESS:
            with self.subTest(name):
                for block in shell_blocks((COMMANDS / name).read_text()):
                    self.assertNotIn("`", block)
                    self.assertNotIn("!", block)

    def test_every_arg_bearing_block_uses_a_quoted_heredoc(self):
        for name in ARG_BEARING:
            with self.subTest(name):
                block = shell_blocks((COMMANDS / name).read_text())[0]
                self.assertIn("<<'TS_ARGV_END'", block)
                # NOT `TS_ARGV="$(cat <<'TS_ARGV_END' … )"`. macOS still ships
                # bash 3.2, whose `$( )` parser tracks quotes naively and dies
                # on an unbalanced `'` inside a heredoc nested in a command
                # substitution — reintroducing the apostrophe bug on the exact
                # platform this plugin is mostly used on. Read it directly.
                self.assertNotIn("$(cat <<", block)
                # `$ARGUMENTS` appears once, on its own line, inside the heredoc.
                self.assertIn("\n$ARGUMENTS\nTS_ARGV_END\n", block)
                self.assertEqual(1, block.count("$ARGUMENTS"))

    # -- the security half -------------------------------------------------

    def test_hostile_arguments_reach_argv_intact_and_execute_nothing(self):
        for name in ARG_BEARING:
            for label, payload in HOSTILE.items():
                with self.subTest(command=name, payload=label):
                    box = _sandbox(self)
                    body = substitute((COMMANDS / name).read_text(), payload)
                    blocks = shell_blocks(body)
                    # The arguments must not have manufactured a second command.
                    self.assertEqual(1, len(blocks), f"{name}: {blocks}")
                    proc = box.run(blocks[0])
                    self.assertEqual(0, proc.returncode, proc.stderr)
                    self.assertFalse(
                        box.executed_payload(),
                        f"{name}/{label}: the argument EXECUTED",
                    )
                    argv = box.argv()
                    self.assertTrue(argv, f"{name}/{label}: the CLI never ran")
                    joined = " ".join(argv)
                    # The exact text the user typed survived to argv, character
                    # for character — no substitution, no stripping, no
                    # environment expansion.
                    for fragment in payload.split():
                        self.assertIn(fragment, joined,
                                      f"{name}/{label}: '{fragment}' lost")

    def test_an_apostrophe_no_longer_stops_the_command(self):
        """The original report: `/repos don't` refused to run at all."""
        for name in ARG_BEARING:
            with self.subTest(name):
                box = _sandbox(self)
                body = substitute((COMMANDS / name).read_text(), "don't")
                proc = box.run(shell_blocks(body)[0])
                self.assertEqual(0, proc.returncode, proc.stderr)
                self.assertNotIn("unexpected EOF", proc.stderr)
                self.assertIn("FAKE-TASK-STATION-OUTPUT", proc.stdout)

    # -- the ergonomic half: arguments still do their job -------------------

    def test_flags_still_split_into_separate_argv_words(self):
        """`/repos --refresh --dry-run` must stay two flags, not one string."""
        box = _sandbox(self)
        body = substitute((COMMANDS / "repos.md").read_text(), "--refresh --dry-run")
        box.run(shell_blocks(body)[0])
        argv = box.argv()
        self.assertIn("--refresh", argv)
        self.assertIn("--dry-run", argv)

    def test_a_glob_is_not_expanded_against_the_cwd(self):
        """Word splitting is wanted; filename expansion is not."""
        box = _sandbox(self)
        body = substitute((COMMANDS / "repos.md").read_text(), "*")
        box.run(shell_blocks(body)[0])
        self.assertIn("*", box.argv())

    def test_single_string_commands_keep_the_whole_phrase(self):
        """`/todo memo 5 it's fine` is one `--arg`, spaces and all."""
        box = _sandbox(self)
        body = substitute((COMMANDS / "todo.md").read_text(), "memo 5 it's fine")
        box.run(shell_blocks(body)[0])
        self.assertIn("memo 5 it's fine", box.argv())

    def test_empty_arguments_still_render(self):
        for name in ARG_BEARING:
            with self.subTest(name):
                box = _sandbox(self)
                body = substitute((COMMANDS / name).read_text(), "")
                proc = box.run(shell_blocks(body)[0])
                self.assertEqual(0, proc.returncode, proc.stderr)
                self.assertIn("FAKE-TASK-STATION-OUTPUT", proc.stdout)

    def test_history_and_prompts_still_append_their_route_word(self):
        for name, word in (("history.md", "history"), ("prompts.md", "prompts")):
            with self.subTest(name):
                box = _sandbox(self)
                box.run(shell_blocks(substitute((COMMANDS / name).read_text(), "12"))[0])
                self.assertIn(f"12 {word}", box.argv())
                box2 = _sandbox(self)
                box2.run(shell_blocks(substitute((COMMANDS / name).read_text(), ""))[0])
                self.assertIn(word, box2.argv())

    def test_done_and_unpin_still_branch_on_an_argument(self):
        for name in ("done.md", "unpin.md"):
            with self.subTest(name):
                box = _sandbox(self)
                box.run(shell_blocks(substitute((COMMANDS / name).read_text(), "13"))[0])
                self.assertIn("--task", box.argv())
                box2 = _sandbox(self)
                box2.run(shell_blocks(substitute((COMMANDS / name).read_text(), ""))[0])
                self.assertIn("--session", box2.argv())

    # -- the honest failure ------------------------------------------------

    def test_a_failure_says_the_skill_was_not_invoked(self):
        for name in ARG_BEARING + ARGLESS:
            with self.subTest(name):
                box = _sandbox(self)
                body = substitute((COMMANDS / name).read_text(), "")
                proc = box.run(shell_blocks(body)[0], rc="1")
                self.assertIn("THE SKILL WAS NOT INVOKED", proc.stdout)
                self.assertIn("nothing was changed", proc.stdout)

    def test_a_success_never_says_it(self):
        for name in ARG_BEARING + ARGLESS:
            with self.subTest(name):
                box = _sandbox(self)
                body = substitute((COMMANDS / name).read_text(), "")
                proc = box.run(shell_blocks(body)[0])
                self.assertNotIn("THE SKILL WAS NOT INVOKED", proc.stdout)

    def test_each_body_tells_the_model_to_report_a_failure_honestly(self):
        """
        A block that dies of a SHELL SYNTAX error never reaches its own banner —
        nothing in it runs. The only thing left standing is the prose, so every
        command file has to tell the reader what an unusable block means.
        """
        for name in ARG_BEARING + ARGLESS:
            with self.subTest(name):
                self.assertIn("DID NOT RUN", (COMMANDS / name).read_text())

    # -- the detector itself -----------------------------------------------

    def test_simulator_detects_the_vulnerable_form(self):
        """
        Red-proof. The shapes below are what these files held before the fix.
        If this replay ever stops catching them, every test above is vacuous.
        """
        box = _sandbox(self)
        old_quoted = '!`python3 "$CLAUDE_PLUGIN_ROOT/x.py" render --arg "$ARGUMENTS"`'
        old_bare = '!`python3 "$CLAUDE_PLUGIN_ROOT/x.py" repos $ARGUMENTS`'

        # 1. The quoted form EXECUTED a dollar-paren argument.
        body = substitute(old_quoted, HOSTILE["dollar_paren"])
        box.run(shell_blocks(body)[0])
        self.assertTrue(box.executed_payload(),
                        "the replay no longer reproduces command substitution")

        # 2. The bare form was a SYNTAX ERROR on an apostrophe.
        box2 = _sandbox(self)
        proc = box2.run(shell_blocks(substitute(old_bare, "don't"))[0])
        self.assertNotEqual(0, proc.returncode)
        self.assertEqual([], box2.argv(), "the CLI should never have started")

        # 3. A backtick TRUNCATED the inline marker mid-command: the shell got
        #    a command that stops dead inside an unclosed quote.
        blocks = shell_blocks(substitute(old_quoted, "a`b"))
        self.assertTrue(blocks[0].endswith('--arg "a'),
                        f"expected a truncated command, got: {blocks[0]!r}")
        box3 = _sandbox(self)
        self.assertNotEqual(0, box3.run(blocks[0]).returncode)


if __name__ == "__main__":
    unittest.main()
