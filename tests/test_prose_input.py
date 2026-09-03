"""Long prose must reach the record intact, whatever the shell does to it.

THE BUG THESE TESTS PIN DOWN. A prose-bearing flag used to have exactly one input
path: a shell word. Backticks inside a double-quoted argument run as command
substitution, so `--decision "the `turn` command found it"` arrives as
`the  command found it` — the word and its backticks gone before argv exists —
and the write then reports SUCCESS. There is nothing for a downstream check to
catch: the corrupted value is a shorter sentence that parses fine.

So every prose flag now also accepts `-` (stdin) and `@PATH` (a file), neither of
which a shell can rewrite. What is tested here is BOTH halves of that: that the
two new paths carry a hostile payload through byte-exact, and that the old
plain-string path did not move an inch — including the two values that now look
like syntax (a bare `-`, and anything starting with `@`).
"""
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)

from board import prose_input as pi   # noqa: E402


# The payload every round-trip test uses: one string carrying every construct a
# shell rewrites, plus the newline that makes it impossible to pass as one word.
HOSTILE = (
    "the `turn` command found it\n"
    "$(whoami) said \"it's 100% $HOME\" — 'single' and \"double\" quotes\n"
    "a trailing backslash \\ and a bare @ and a lone - in the middle"
)


class _FakeStdin(io.StringIO):
    """A pipe: not a tty, readable once."""
    def isatty(self):
        return False


class _FakeTty:
    """A terminal: isatty() is True, and read() must never be called — if the
    resolver ever reaches for it, a real run would block on a terminal that will
    never send EOF, so this fails the test instead of hanging the suite."""
    def __init__(self, case):
        self._case = case

    def isatty(self):
        return True

    def read(self, *a):
        self._case.fail("read stdin from a tty — a real run would have hung here")


class _ProseResolverTest(unittest.TestCase):
    """The resolver itself, where byte-exactness is actually observable.

    `append_decision` strips its argument before storing, so a store-level test
    cannot distinguish "the resolver preserved the bytes" from "the store trimmed
    them". These assert the seam that this change owns.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="prose-")
        self._stdin = sys.stdin

    def tearDown(self):
        sys.stdin = self._stdin
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _file(self, text, name="p.txt"):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def _refusal(self, fn):
        """Run fn expecting the house refusal — printed line, exit 2."""
        buf = io.StringIO()
        with self.assertRaises(SystemExit) as cm, redirect_stdout(buf):
            fn()
        self.assertEqual(cm.exception.code, 2, buf.getvalue())
        return buf.getvalue()

    # ---- branch 1: the plain string, unchanged --------------------------------
    def test_plain_string_is_verbatim(self):
        for value in (
            "an ordinary decision",
            "contains an @ in someone@example.com",
            "-x is a value that begins with a dash",
            "--not-a-flag either",
            "trailing whitespace kept   ",
            HOSTILE,                       # already intact in argv → untouched
        ):
            self.assertEqual(pi.resolve_prose_value(value, "decision"), value)

    def test_non_strings_pass_through(self):
        # `heal --decision` / `memo --decision` are nargs="?" const=True: a bare
        # flag means "yes" and is not prose at all.
        self.assertIs(pi.resolve_prose_value(True, "decision"), True)
        self.assertIsNone(pi.resolve_prose_value(None, "decision"))

    # ---- branch 2: stdin via `-` ---------------------------------------------
    def test_stdin_dash_reads_the_payload_byte_exact(self):
        sys.stdin = _FakeStdin(HOSTILE)
        self.assertEqual(pi.resolve_prose_value("-", "decision"), HOSTILE)

    def test_stdin_drops_exactly_one_trailing_newline(self):
        sys.stdin = _FakeStdin(HOSTILE + "\n")
        self.assertEqual(pi.resolve_prose_value("-", "decision"), HOSTILE)
        sys.stdin = _FakeStdin(HOSTILE + "\n\n")
        self.assertEqual(pi.resolve_prose_value("-", "decision"), HOSTILE + "\n")

    def test_stdin_preserves_interior_newlines(self):
        sys.stdin = _FakeStdin("a\n\nb\nc\n")
        self.assertEqual(pi.resolve_prose_value("-", "decision"), "a\n\nb\nc")

    # ---- branch 3: the file form --------------------------------------------
    def test_file_form_reads_the_payload_byte_exact(self):
        path = self._file(HOSTILE)
        self.assertEqual(pi.resolve_prose_value("@" + path, "decision"), HOSTILE)

    def test_file_form_drops_exactly_one_trailing_newline(self):
        path = self._file(HOSTILE + "\n")
        self.assertEqual(pi.resolve_prose_value("@" + path, "decision"), HOSTILE)

    def test_file_form_expands_tilde(self):
        # ~ is the shell's job normally; @PATH bypasses the shell, so it is ours.
        # $HOME is repointed at the tmpdir rather than writing into the real one.
        self._file("tilde payload", "t.txt")
        home = os.environ.get("HOME")
        os.environ["HOME"] = self.tmp
        try:
            self.assertEqual(
                pi.resolve_prose_value("@~/t.txt", "decision"), "tilde payload")
        finally:
            if home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = home

    # ---- the ambiguous values, ruled on explicitly ---------------------------
    def test_at_at_escapes_a_literal_leading_at(self):
        self.assertEqual(
            pi.resolve_prose_value("@@claude mentioned it", "decision"),
            "@claude mentioned it")
        self.assertEqual(pi.resolve_prose_value("@@", "decision"), "@")

    def test_a_literal_lone_dash_is_reachable_through_stdin(self):
        # `-` on the command line now MEANS stdin, so this is the way to store the
        # one-character value `-`. It has to stay reachable: before this change
        # `--decision -` silently stored the dash, and something may depend on it.
        sys.stdin = _FakeStdin("-")
        self.assertEqual(pi.resolve_prose_value("-", "decision"), "-")

    def test_a_literal_lone_dash_is_reachable_through_a_file(self):
        self.assertEqual(
            pi.resolve_prose_value("@" + self._file("-"), "decision"), "-")

    # ---- the no-hang guarantee ----------------------------------------------
    def test_tty_stdin_with_no_dash_never_reads(self):
        # _FakeTty.read() fails the test, so reaching this far at all is the
        # assertion: no value other than `-` may touch stdin.
        sys.stdin = _FakeTty(self)
        for value in ("a plain decision", "-x", "", "a - in the middle"):
            self.assertEqual(pi.resolve_prose_value(value, "decision"), value)
        self.assertEqual(pi.resolve_prose_value("@@literal", "decision"), "@literal")

    def test_tty_stdin_with_a_dash_refuses_instead_of_hanging(self):
        sys.stdin = _FakeTty(self)
        out = self._refusal(lambda: pi.resolve_prose_value("-", "decision"))
        self.assertIn("interactive terminal", out)

    # ---- every failure is LOUD (the bug survived by being quiet) -------------
    def test_missing_file_refuses_rather_than_storing_the_literal(self):
        out = self._refusal(lambda: pi.resolve_prose_value(
            "@" + os.path.join(self.tmp, "nope.txt"), "decision"))
        self.assertIn("no such file", out)

    def test_directory_path_refuses(self):
        out = self._refusal(lambda: pi.resolve_prose_value("@" + self.tmp, "decision"))
        self.assertIn("directory", out)

    def test_bare_at_with_no_path_refuses(self):
        out = self._refusal(lambda: pi.resolve_prose_value("@", "decision"))
        self.assertIn("no path", out)

    def test_empty_input_refuses(self):
        sys.stdin = _FakeStdin("")
        out = self._refusal(lambda: pi.resolve_prose_value("-", "decision"))
        self.assertIn("0 bytes", out)

    def test_two_flags_taking_stdin_refuses(self):
        sys.stdin = _FakeStdin("only one reader\n")
        state = {}
        self.assertEqual(pi.resolve_prose_value("-", "state", state), "only one reader")
        out = self._refusal(lambda: pi.resolve_prose_value("-", "goal", state))
        self.assertIn("ONE stdin", out)

    # ---- the namespace walk -------------------------------------------------
    def test_repeatable_flag_resolves_element_wise(self):
        path = self._file("from the file")
        a = _Args(cmd="update", decision=["plain", "@" + path, "@@at-literal"])
        pi.resolve_prose_args(a)
        self.assertEqual(a.decision, ["plain", "from the file", "@at-literal"])

    def test_unlisted_subcommand_is_untouched(self):
        a = _Args(cmd="done", decision="-", note="@nope")
        pi.resolve_prose_args(a)
        self.assertEqual(a.decision, "-")
        self.assertEqual(a.note, "@nope")

    def test_missing_dest_is_not_fatal(self):
        a = _Args(cmd="update")          # no prose attributes at all
        pi.resolve_prose_args(a)         # must not raise


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _ProseTableTest(unittest.TestCase):
    """The table is the single source of truth for BOTH behaviour and help text.
    If it names a flag that does not exist, or a flag it names loses its
    annotation, these fail — which is the only thing keeping the documented
    convention and the implemented one from drifting."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="prose-cli-")
        os.environ["TASK_STATION_HOME"] = self.tmp

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _subparsers(self):
        """The REAL parser tree main() builds, captured at parse time."""
        import argparse
        grabbed = {}
        real = argparse.ArgumentParser.parse_args

        def _capture(self, argv=None, namespace=None):
            if self._subparsers:
                for act in self._subparsers._group_actions:
                    grabbed["sub"] = act
            raise SystemExit(0)

        argparse.ArgumentParser.parse_args = _capture
        try:
            ts.main([])
        except SystemExit:
            pass
        finally:
            argparse.ArgumentParser.parse_args = real
        self.assertIn("sub", grabbed, "could not capture the subparsers action")
        return grabbed["sub"]

    def test_the_required_flags_are_all_covered(self):
        """The set the task named, each on every subcommand that defines it."""
        required = {
            ("update", "decision"), ("update", "summary"),
            ("update", "append_summary"), ("update", "state"), ("update", "goal"),
            ("create", "summary"), ("create", "goal"),
            ("attach", "note"), ("grade", "note"), ("heal", "note"),
            ("turn", "ask"), ("invoke", "ask"),
            ("grade", "why"), ("heal", "why"), ("channel", "why"),
            ("memo", "text"),
            # found by sweeping every free-text option in the tree, not by the
            # original list: `--log` is the README's own pair to `--decision`, and a
            # title is a sentence like any other.
            ("update", "log"), ("update", "title"), ("create", "title"),
            ("update", "pr_desc"), ("update", "story_desc"),
            ("decompose", "into"),
        }
        covered = {(c, d) for c, ds in pi.PROSE_FLAGS.items() for d in ds}
        self.assertEqual(set(), required - covered)

    def _find_parser(self, sub, name):
        """The parser for `name`, wherever it is filed. A subcommand may sit at the top
        level or under the `hook` group (3.63.0 moved the 22 plumbing verbs there), and
        the prose contract is about the FLAG, not about which group holds it."""
        parser = sub.choices.get(name)
        if parser is not None:
            return parser
        group = sub.choices.get("hook")
        for act in getattr(group, "_subparsers", None)._actions if group else []:
            inner = getattr(act, "choices", None)
            if inner and name in inner:
                return inner[name]
        return None

    def test_every_table_entry_is_a_real_flag_and_is_annotated(self):
        sub = self._subparsers()
        annotated = 0
        for name, dests in pi.PROSE_FLAGS.items():
            parser = self._find_parser(sub, name)
            self.assertIsNotNone(parser, "PROSE_FLAGS names subcommand %r, which "
                                         "the parser does not define" % name)
            for dest in dests:
                acts = [x for x in parser._actions
                        if x.dest == dest and x.option_strings]
                self.assertTrue(acts, "PROSE_FLAGS names %s --%s, which the parser "
                                      "does not define" % (name, dest))
                for act in acts:
                    self.assertIn(pi.HELP_SUFFIX, act.help or "",
                                  "%s --%s is resolved but its help never says so"
                                  % (name, dest))
                    annotated += 1
        self.assertGreaterEqual(annotated, 31)     # a positive count, asserted

    def test_no_second_file_spelling_exists(self):
        """ONE file spelling. A `--<flag>-file` twin would be a second thing to
        keep correct and a second thing for a caller to guess wrong."""
        sub = self._subparsers()
        for name, dests in pi.PROSE_FLAGS.items():
            parser = self._find_parser(sub, name)
            self.assertIsNotNone(parser, name)
            opts = {o for act in parser._actions for o in act.option_strings}
            for dest in dests:
                twin = "--%s-file" % dest.replace("_", "-")
                self.assertNotIn(twin, opts, "%s defines both @PATH and %s"
                                             % (name, twin))


class _ProseEndToEndTest(unittest.TestCase):
    """Through `main(argv)` and into the store — proof the resolver is actually
    wired into the dispatch path, not just importable."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="prose-e2e-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.PROJECTS_ROOT = os.path.join(self.tmp, "projects")
        self._stdin = sys.stdin
        t = ts.new_task("prose target", "summary")
        ts.save_task(t)
        ts.ensure_seqs()
        self.task = ts.load_task(t["id"])
        self.ref = str(self.task["seq"])

    def tearDown(self):
        sys.stdin = self._stdin
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.main(argv)
        return buf.getvalue()

    def _decisions(self):
        return ts.load_task(self.task["id"]).get("decisions") or []

    def test_stdin_payload_lands_in_the_store_with_every_construct_intact(self):
        sys.stdin = _FakeStdin(HOSTILE + "\n")
        self._run(["update", "--task", self.ref, "--decision", "-"])
        stored = self._decisions()
        self.assertEqual(1, len(stored))
        self.assertEqual(HOSTILE, stored[0])
        for fragment in ("`turn`", "$(whoami)", "100% $HOME",
                         "'single'", '"double"', "\n"):
            self.assertIn(fragment, stored[0])

    def test_file_payload_lands_in_the_store(self):
        path = os.path.join(self.tmp, "d.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(HOSTILE + "\n")
        self._run(["update", "--task", self.ref, "--decision", "@" + path])
        self.assertEqual([HOSTILE], self._decisions())

    def test_plain_string_call_site_is_unchanged(self):
        value = "a plain decision with an @ in a@b.com"
        self._run(["update", "--task", self.ref, "--decision", value])
        self.assertEqual([value], self._decisions())

    def test_state_and_goal_take_stdin_too(self):
        sys.stdin = _FakeStdin("NEXT: run `exit-tick` and quote $(its output)\n")
        self._run(["update", "--task", self.ref, "--state", "-"])
        self.assertEqual("NEXT: run `exit-tick` and quote $(its output)",
                         ts.load_task(self.task["id"]).get("state"))

    def test_log_takes_stdin(self):
        # `--log` is repeatable and append-only, the same shape as `--decision`.
        sys.stdin = _FakeStdin("3.21.0 shipped: `-` and `@PATH` on every prose flag\n")
        self._run(["update", "--task", self.ref, "--log", "-"])
        entries = ts.load_task(self.task["id"]).get("history") or []
        self.assertEqual(1, len(entries))
        self.assertIn("`-` and `@PATH`", entries[0]["text"])

    def test_title_takes_a_file(self):
        path = os.path.join(self.tmp, "t.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("a title with `backticks` and $(a subshell) in it\n")
        self._run(["update", "--task", self.ref, "--title", "@" + path])
        self.assertEqual("a title with `backticks` and $(a subshell) in it",
                         ts.load_task(self.task["id"])["title"])

    def test_empty_string_still_clears_a_field(self):
        self._run(["update", "--task", self.ref, "--goal", "a goal"])
        self.assertEqual("a goal", ts.load_task(self.task["id"]).get("goal"))
        self._run(["update", "--task", self.ref, "--goal", ""])
        self.assertFalse(ts.load_task(self.task["id"]).get("goal"))

    def test_a_missing_file_writes_nothing(self):
        buf = io.StringIO()
        with self.assertRaises(SystemExit) as cm, redirect_stdout(buf):
            ts.main(["update", "--task", self.ref, "--decision",
                     "@" + os.path.join(self.tmp, "absent.txt")])
        self.assertEqual(2, cm.exception.code)
        self.assertEqual([], self._decisions())

    def test_tty_stdin_with_no_dash_does_not_hang(self):
        sys.stdin = _FakeTty(self)
        self._run(["update", "--task", self.ref, "--decision", "typed by hand"])
        self.assertEqual(["typed by hand"], self._decisions())


if __name__ == "__main__":
    unittest.main()
