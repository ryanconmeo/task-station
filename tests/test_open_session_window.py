"""lib/open-session-window.sh — the window gets a FILE to run, not a line to type.

THE DEFECT, measured 2026-08-27. An `invoke` whose command line was ~1045 characters
was typed into a fresh iTerm2 window and cut off mid-word. The session was minted, the
trail said "invoked", the opener said "opened a new window running it", and nothing
ran — the window sat at a prompt.

WHERE THE CUT IS. Not osascript: a 20 000-character argv through the same
`on run argv` path arrives intact. It is the TTY LINE DISCIPLINE. `write text` and
`do script` TYPE the string into the session. A shell already at its line editor reads
any length; a shell that is NOT — a window one millisecond old, still sourcing its rc
files — leaves the tty in canonical mode, where the kernel caps a line at 1024 BYTES
and discards the rest with no error. Reproduced exactly: a 1189-character line typed
into a busy session arrived as its first 1024 bytes.

WHY THESE TESTS ASSERT ON WHAT IS RECEIVED, NEVER ON WHAT IS SENT. The whole defect is
that the sending side looked perfect. Every length assertion below therefore runs the
launch script the way the new window would and compares the bytes that came OUT, and
the runner-line test pins the only property that actually fixes it: the line the window
receives is short no matter how long the command is.

No test here opens a window. The opener's `--dry-run` writes the launch script and
prints what the window would receive; `$TASK_STATION_TERMINAL` pins the host branch.
"""
import os
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPENER = os.path.join(ROOT, "lib", "open-session-window.sh")

# The payload that a naive quoting scheme mangles: both quote characters, a backtick,
# a `$(…)`, an embedded newline, and an em dash (3 bytes in UTF-8, which is why the
# original 1024-BYTE cut read as "~930 chars"). Padded past 4000 characters, because
# 4000 is comfortably over the 1024-byte tty cap that started this.
NASTY = """it's a "quoted" `backtick` $(echo sub) $HOME
second line — em dash — done"""
LONG_PAYLOAD = NASTY + "x" * (4000 - len(NASTY))


def run_opener(*args, env=None):
    e = dict(os.environ)
    e.setdefault("TASK_STATION_TERMINAL", "iterm2")
    e.update(env or {})
    return subprocess.run(["bash", OPENER, *args], capture_output=True, text=True,
                          timeout=30, env=e)


def dry_run(cmd, env=None):
    """`(runner_line, script_path)` — exactly what the window would receive, and the
    file it would source. Fails the calling test loudly rather than returning junk."""
    r = run_opener("--dry-run", cmd, env=env)
    assert r.returncode == 0, "opener --dry-run failed: %s%s" % (r.stdout, r.stderr)
    lines = r.stdout.splitlines()
    assert len(lines) == 2, "expected runner + path, got %r" % (r.stdout,)
    return lines[0], lines[1]


class TheWindowIsHandedAFileNotTheCommand(unittest.TestCase):
    def test_the_line_the_window_receives_does_not_grow_with_the_command(self):
        """THE FIX, stated as the one measurable property. A 4000-character command
        and a 12-character one produce the SAME-LENGTH line into the window, and it is
        nowhere near the 1024-byte cap that silently ate the original invoke."""
        short_runner, short_path = dry_run("echo hi")
        long_runner, long_path = dry_run(LONG_PAYLOAD)
        self.addCleanup(_unlink, short_path)
        self.addCleanup(_unlink, long_path)
        self.assertEqual(len(short_runner), len(long_runner))
        self.assertLess(len(long_runner.encode("utf-8")), 1024)

    def test_the_runner_sources_the_script(self):
        runner, path = dry_run("echo hi")
        self.addCleanup(_unlink, path)
        self.assertTrue(runner.startswith("source "), runner)
        self.assertIn(os.path.basename(path), runner)

    def test_the_script_is_private_to_this_user(self):
        _, path = dry_run("echo hi")
        self.addCleanup(_unlink, path)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_the_temp_name_is_not_predictable(self):
        """BSD `mktemp` leaves non-trailing X's alone, so a `…-XXXX.sh` template
        yields that literal name for every caller — the file carries a session id and
        a whole prompt, so two runs must not collide on a guessable path."""
        _, a = dry_run("echo hi")
        _, b = dry_run("echo hi")
        self.addCleanup(_unlink, a)
        self.addCleanup(_unlink, b)
        self.assertNotEqual(a, b)
        self.assertNotIn("XXXXXXXXXX", os.path.basename(a))


class A4000CharacterCommandSurvivesByteExact(unittest.TestCase):
    def test_the_bytes_the_new_session_runs_are_the_bytes_that_went_in(self):
        """Asserted on the RECEIVED value: the launch script is executed the way the
        window would source it, and the command it ran prints its own argument back."""
        out_path = os.path.join(tempfile.mkdtemp(), "received")
        cmd = "printf '%%s' %s > %s" % (_shquote(LONG_PAYLOAD), _shquote(out_path))
        runner, path = dry_run(cmd)
        self.addCleanup(_unlink, path)
        subprocess.run(["bash", "-c", runner], check=True, timeout=30)
        with open(out_path, encoding="utf-8") as fh:
            received = fh.read()
        self.assertEqual(len(received), 4000)
        self.assertEqual(received, LONG_PAYLOAD)

    def test_the_script_holds_the_command_verbatim(self):
        _, path = dry_run(LONG_PAYLOAD)
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
        self.addCleanup(_unlink, path)
        self.assertTrue(body.endswith(LONG_PAYLOAD + "\n"))

    def test_the_script_deletes_itself_when_run(self):
        """A command line carrying a session id must not linger. Unlinking a file the
        shell already opened is safe on POSIX, so the payload still runs."""
        out_path = os.path.join(tempfile.mkdtemp(), "ran")
        runner, path = dry_run("printf ran > %s" % _shquote(out_path))
        self.assertTrue(os.path.exists(path))
        subprocess.run(["bash", "-c", runner], check=True, timeout=30)
        with open(out_path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "ran")
        self.assertFalse(os.path.exists(path), "launch script was left on disk")


class EveryFailureIsLoud(unittest.TestCase):
    """A window that opened but was never given its command is the same class of lie
    as a window in the wrong app: no error, and "success" is what gets reported."""

    def test_an_undrivable_host_opens_nothing_and_says_which(self):
        r = run_opener("echo hi", env={"TASK_STATION_TERMINAL": "warp"})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("cannot open a window in Warp", r.stderr)
        self.assertIn("echo hi", r.stderr)

    def test_an_undrivable_host_leaves_no_launch_script_behind(self):
        """The host is asked BEFORE anything is written, so a refusal is also clean."""
        with tempfile.TemporaryDirectory() as d:
            r = run_opener("echo hi", env={"TASK_STATION_TERMINAL": "warp",
                                           "TASK_STATION_HOME": d, "TMPDIR": d})
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(_launch_files(d), [])

    def test_a_script_that_cannot_be_written_refuses_on_every_driven_host(self):
        """Positive test per branch: iTerm2 and Terminal.app are typed, the rest are
        argv — none of them may open a window it cannot hand the command to.

        This runs the REAL path, not `--dry-run`, and still opens nothing on any of the
        six: the launch script is written before the host is driven, so its failure
        exits before a window can exist. `/dev/null/nope` is unwritable in a way
        `makedirs` cannot paper over — an earlier draft of this test used a missing
        directory under $TMPDIR, `launch_dir()` created it, the write SUCCEEDED, and the
        iTerm2 and Terminal.app subtests each opened a real window on the machine
        running the suite."""
        nowhere = "/dev/null/nope"
        for host, name in (("iterm2", "iTerm2"), ("apple_terminal", "Terminal.app"),
                           ("kitty", "kitty"), ("wezterm", "WezTerm"),
                           ("ghostty", "Ghostty"), ("alacritty", "Alacritty")):
            with self.subTest(host=host):
                r = run_opener("echo hi", env={"TASK_STATION_TERMINAL": host,
                                               "TASK_STATION_HOME": nowhere,
                                               "TMPDIR": nowhere})
                self.assertEqual(r.returncode, 4, r.stderr)
                self.assertIn("could not write the launch", r.stderr)
                self.assertIn(name, r.stderr)
                self.assertIn("NOTHING was opened", r.stderr)


class TheResolverIsStillTheOnlyOneAskingWhichTerminal(unittest.TestCase):
    """#558 was closed on this and PR 20 shipped it: the opener must not grow a second
    copy of the host table. `--host` still answers from `core.termhost`."""

    def test_host_reports_the_override(self):
        r = run_opener("--host", env={"TASK_STATION_TERMINAL": "ghostty"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Ghostty", r.stdout)


def _shquote(s):
    import shlex
    return shlex.quote(s)


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def _launch_files(root):
    found = []
    for base, _dirs, files in os.walk(root):
        found += [os.path.join(base, f) for f in files if f.startswith("open-window-")]
    return found


if __name__ == "__main__":
    unittest.main()
