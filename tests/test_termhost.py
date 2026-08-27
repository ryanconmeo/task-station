"""core.termhost — which terminal am I in, and may I open a window in it?

THE INCIDENT, 2026-08-26. Asked to open a new terminal, a session ran

    osascript -e 'tell application "Terminal" to do script ...'

while it was itself running inside iTerm. A stray Terminal.app window opened
somewhere the session could not see, the session reported success, and a human had
to go and close it. Two independent signals were available and neither was read:
the environment (`TERM_PROGRAM=iTerm.app`, `LC_TERMINAL=iTerm2`,
`ITERM_SESSION_ID`, `LC_TERMINAL=iTerm2`) and a process ancestry ending in
`/Applications/iTerm.app/Contents/MacOS/iTerm2`.

WHAT THESE PIN, in the order they matter:

  1. The env is read, and read in a defined ORDER — an override, then the marker
     that survives ssh/tmux, then the common one, then per-terminal ones.
  2. When the env says nothing, the ANCESTRY is walked. This is not a nicety: a
     detached re-exec, a `sudo`, or a login shell that resets the environment all
     leave the parent chain as the only remaining evidence.
  3. An unrecognised host NEVER resolves to Terminal.app. That silent default is
     the bug — a window in the wrong app raises no error, so "success" is what the
     session reports and the human finds the window later.
  4. Every answer carries `how`, the signal it believed. A wrong guess that says
     why it guessed is catchable; a silent one is not.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))

from core import termhost


ITERM_ANCESTRY = ["/Users/x/.local/bin/claude",
                  "/bin/zsh",
                  "/Applications/iTerm.app/Contents/MacOS/iTerm2",
                  "/sbin/launchd"]


class TheEnvironmentIsReadInOrder(unittest.TestCase):
    def test_the_explicit_override_wins_over_everything(self):
        h = termhost.resolve(env={"TASK_STATION_TERMINAL": "ghostty",
                                  "LC_TERMINAL": "iTerm2",
                                  "TERM_PROGRAM": "Apple_Terminal"}, ancestry=[])
        self.assertEqual(h["id"], "ghostty")
        self.assertIn("TASK_STATION_TERMINAL", h["how"])

    def test_lc_terminal_is_asked_before_term_program(self):
        """LC_TERMINAL is the only marker that survives ssh and tmux, which is why
        it goes first — under tmux TERM_PROGRAM is routinely the wrong answer."""
        h = termhost.resolve(env={"LC_TERMINAL": "iTerm2",
                                  "TERM_PROGRAM": "tmux"}, ancestry=[])
        self.assertEqual(h["id"], "iterm2")
        self.assertEqual(h["how"], "LC_TERMINAL=iTerm2")

    def test_the_session_that_caused_this_resolves_to_iterm(self):
        """The incident's actual environment, verbatim."""
        h = termhost.resolve(env={"TERM_PROGRAM": "iTerm.app",
                                  "TERM_PROGRAM_VERSION": "3.6.11",
                                  "ITERM_SESSION_ID": "w2t0p0:XXXX",
                                  "LC_TERMINAL": "iTerm2"}, ancestry=[])
        self.assertEqual(h["id"], "iterm2")
        self.assertNotEqual(h["id"], "apple_terminal")

    def test_each_named_terminal_is_recognised_from_term_program(self):
        for value, expect in (("iTerm.app", "iterm2"),
                              ("Apple_Terminal", "apple_terminal"),
                              ("WezTerm", "wezterm"),
                              ("ghostty", "ghostty"),
                              ("vscode", "vscode"),
                              ("Hyper", "hyper"),
                              ("WarpTerminal", "warp"),
                              ("Tabby", "tabby"),
                              ("rio", "rio"),
                              ("alacritty", "alacritty"),
                              ("kitty", "kitty")):
            h = termhost.resolve(env={"TERM_PROGRAM": value}, ancestry=[])
            self.assertEqual(h["id"], expect, "TERM_PROGRAM=%s" % value)

    def test_term_program_matching_is_case_insensitive(self):
        """Terminals are not consistent about their own casing between versions."""
        self.assertEqual(termhost.resolve(env={"TERM_PROGRAM": "ITERM.APP"},
                                          ancestry=[])["id"], "iterm2")

    def test_terminals_that_only_set_their_own_marker_are_found(self):
        """kitty, Alacritty, WezTerm, Windows Terminal and Konsole all leave
        TERM_PROGRAM unset in common configurations."""
        for var, expect in (("KITTY_WINDOW_ID", "kitty"),
                            ("ALACRITTY_SOCKET", "alacritty"),
                            ("WEZTERM_PANE", "wezterm"),
                            ("GHOSTTY_RESOURCES_DIR", "ghostty"),
                            ("WT_SESSION", "windows_terminal"),
                            ("KONSOLE_VERSION", "konsole"),
                            ("ITERM_SESSION_ID", "iterm2")):
            h = termhost.resolve(env={var: "1"}, ancestry=[])
            self.assertEqual(h["id"], expect, var)

    def test_every_answer_says_which_signal_it_believed(self):
        """A wrong guess that says why is catchable. A silent one is not."""
        for env in ({"LC_TERMINAL": "iTerm2"}, {"TERM_PROGRAM": "WezTerm"},
                    {"KITTY_WINDOW_ID": "3"}, {}):
            h = termhost.resolve(env=env, ancestry=[])
            self.assertTrue(h["how"].strip(), "empty `how` for %r" % env)


class TheAncestryIsTheSecondSignal(unittest.TestCase):
    """Env can be scrubbed — a detached re-exec, a sudo, a login shell that resets
    it. The parent chain cannot be, and in the incident it ended in iTerm2."""

    def test_a_scrubbed_env_still_finds_the_host(self):
        h = termhost.resolve(env={}, ancestry=ITERM_ANCESTRY)
        self.assertEqual(h["id"], "iterm2")
        self.assertIn("ancestry", h["how"])

    def test_the_env_is_believed_over_the_ancestry(self):
        """Deliberate: a session inside `screen` inside iTerm is hosted by what its
        env says, and the ancestry is the fallback for when nothing says anything."""
        h = termhost.resolve(env={"TERM_PROGRAM": "WezTerm"}, ancestry=ITERM_ANCESTRY)
        self.assertEqual(h["id"], "wezterm")

    def test_bundle_names_are_matched_for_each_macos_terminal(self):
        for bundle, expect in (("iTerm.app", "iterm2"),
                               ("Terminal.app", "apple_terminal"),
                               ("WezTerm.app", "wezterm"),
                               ("Ghostty.app", "ghostty"),
                               ("Alacritty.app", "alacritty")):
            h = termhost.resolve(
                env={}, ancestry=["/bin/zsh",
                                  "/Applications/%s/Contents/MacOS/x" % bundle])
            self.assertEqual(h["id"], expect, bundle)

    def test_a_bare_unix_binary_name_is_matched_too(self):
        """On Linux `ps -o comm=` prints the name, not a bundle path."""
        h = termhost.resolve(env={}, ancestry=["/bin/bash", "/usr/bin/kitty"])
        self.assertEqual(h["id"], "kitty")

    def test_an_ancestry_of_nothing_recognisable_is_not_an_answer(self):
        h = termhost.resolve(env={}, ancestry=["/bin/zsh", "/sbin/launchd"])
        self.assertEqual(h["id"], "unknown")


class UnknownIsNeverSilentlyTerminalApp(unittest.TestCase):
    """The bug, stated as a test. A window opened in the wrong app raises no error,
    so no error reads as success and a human finds the stray window later."""

    def test_nothing_identified_resolves_to_unknown(self):
        h = termhost.resolve(env={}, ancestry=[])
        self.assertEqual(h["id"], "unknown")
        self.assertIn("nothing identified", h["how"])

    def test_an_unknown_host_gets_no_spawn_plan(self):
        plan = termhost.spawn_plan("echo hi", env={}, ancestry=[])
        self.assertIsNone(plan["mechanism"])
        self.assertIsNone(plan["argv"])

    def test_the_refusal_hands_back_the_command(self):
        plan = termhost.spawn_plan("claude --resume abc", env={}, ancestry=[])
        self.assertIn("claude --resume abc", plan["reason"])

    def test_a_terminal_we_cannot_drive_refuses_rather_than_substituting(self):
        for tp in ("Hyper", "WarpTerminal", "vscode", "Tabby"):
            plan = termhost.spawn_plan("echo hi", env={"TERM_PROGRAM": tp},
                                       ancestry=[])
            self.assertIsNone(plan["mechanism"], tp)
            self.assertNotIn("Terminal.app", plan.get("argv") or [])

    def test_an_unrecognised_term_program_is_still_named(self):
        """A terminal this module has never heard of is a name the reader can teach
        it — better than "unknown" with no clue what to add."""
        h = termhost.resolve(env={"TERM_PROGRAM": "SomeNewTerm"}, ancestry=[])
        self.assertIn("SomeNewTerm", h["how"])
        self.assertIn("not a terminal this knows", h["how"])


class TheSpawnPlanMatchesTheHost(unittest.TestCase):
    def test_the_two_applescript_hosts(self):
        for tp, expect in (("iTerm.app", "iterm2"), ("Apple_Terminal", "apple_terminal")):
            plan = termhost.spawn_plan("echo hi", env={"TERM_PROGRAM": tp}, ancestry=[])
            self.assertEqual(plan["mechanism"], "applescript")
            self.assertEqual(plan["host"], expect)

    def test_cli_driven_terminals_get_an_argv_carrying_the_command(self):
        for tp in ("WezTerm", "ghostty", "kitty", "alacritty"):
            plan = termhost.spawn_plan("claude --resume abc",
                                       env={"TERM_PROGRAM": tp}, ancestry=[])
            self.assertEqual(plan["mechanism"], "argv", tp)
            self.assertIn("claude --resume abc", plan["argv"], tp)

    def test_describe_names_the_app_and_the_signal(self):
        plan = termhost.spawn_plan("echo hi", env={"LC_TERMINAL": "iTerm2"},
                                   ancestry=[])
        line = termhost.describe(plan)
        self.assertIn("iTerm2", line)
        self.assertIn("LC_TERMINAL=iTerm2", line)

    def test_describe_says_CANNOT_when_it_cannot(self):
        self.assertIn("cannot", termhost.describe(
            termhost.spawn_plan("echo hi", env={}, ancestry=[])))


class OneResolverTwoConsumers(unittest.TestCase):
    """open-session-window.sh and close-session-window.sh are bash and this is
    Python. They used to carry their own copies of the iTerm2 test, and the copy in
    close-session-window.sh never learned the ancestry fallback."""

    def test_the_shell_report_is_evaluable_assignments(self):
        out = termhost.shell_report(env={"LC_TERMINAL": "iTerm2"}, ancestry=[])
        self.assertIn("TS_TERM_ID=iterm2", out)
        self.assertIn("TS_TERM_NAME=", out)
        self.assertIn("TS_TERM_HOW=", out)

    def test_a_how_containing_spaces_is_quoted_for_the_shell(self):
        out = termhost.shell_report(env={}, ancestry=[])
        line = [l for l in out.splitlines() if l.startswith("TS_TERM_HOW=")][0]
        self.assertTrue(line.endswith("'") or " " not in line,
                        "unquoted value would break `eval`: %r" % line)


class TheScriptsUseTheResolver(unittest.TestCase):
    """A grep guard: the detection must not creep back into the shell files. It
    lived in both of them, and two copies of one rule is how they drifted."""

    LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "lib")

    def _read(self, name):
        with open(os.path.join(self.LIB, name), encoding="utf-8") as fh:
            return fh.read()

    def test_neither_script_tests_lc_terminal_itself(self):
        for name in ("open-session-window.sh", "close-session-window.sh"):
            body = self._read(name)
            self.assertNotIn('"${LC_TERMINAL:-}" = "iTerm2"', body, name)
            self.assertIn("core.termhost", body, name)

    def test_the_opener_refuses_rather_than_defaulting(self):
        body = self._read("open-session-window.sh")
        self.assertIn("exit 3", body)
        self.assertIn("Run this yourself", body)


class TheOpenerAddressesTheRightApp(unittest.TestCase):
    """END TO END, without opening a window: `osascript` is replaced on $PATH by a
    stub that records what it was asked to do. This is the assertion the incident
    needed — not "the resolver said iTerm2" but "the Apple Event went to iTerm".

    A window that opens in the wrong app produces NO error, so a test that only
    checks the exit code would have passed on the broken version."""

    SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "lib", "open-session-window.sh")

    def _run(self, env_overrides):
        import shutil
        import subprocess
        import tempfile
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        stub = os.path.join(tmp, "osascript")
        with open(stub, "w") as fh:
            fh.write('#!/bin/sh\ncat > "$OSASCRIPT_LOG.script"\necho opened\n')
        os.chmod(stub, 0o755)
        env = dict(os.environ)
        for k in ("LC_TERMINAL", "ITERM_SESSION_ID", "TERM_PROGRAM",
                  "TASK_STATION_TERMINAL", "KITTY_WINDOW_ID", "WEZTERM_PANE"):
            env.pop(k, None)
        env.update(env_overrides)
        env["PATH"] = tmp + os.pathsep + env["PATH"]
        env["OSASCRIPT_LOG"] = os.path.join(tmp, "spawn")
        r = subprocess.run(["bash", self.SCRIPT, "claude --resume abc123"],
                           capture_output=True, text=True, env=env, timeout=30)
        sent = ""
        path = os.path.join(tmp, "spawn.script")
        if os.path.exists(path):
            with open(path, encoding="utf-8", errors="replace") as fh:
                sent = fh.read()
        return r, sent

    def test_an_iterm_session_addresses_iterm_and_never_terminal(self):
        r, sent = self._run({"LC_TERMINAL": "iTerm2", "TERM_PROGRAM": "iTerm.app"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('tell application "iTerm"', sent)
        self.assertNotIn('tell application "Terminal"', sent)
        self.assertIn("iTerm2", r.stderr)          # it SAYS which one it chose

    def test_an_apple_terminal_session_addresses_terminal(self):
        r, sent = self._run({"TERM_PROGRAM": "Apple_Terminal"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('tell application "Terminal"', sent)

    def test_an_undrivable_host_opens_nothing_at_all(self):
        r, sent = self._run({"TASK_STATION_TERMINAL": "warp"})
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(sent, "")                 # no Apple Event was sent anywhere
        self.assertIn("claude --resume abc123", r.stderr)   # handed back instead

    def test_no_command_is_a_refusal_not_an_empty_window(self):
        import subprocess
        r = subprocess.run(["bash", self.SCRIPT], capture_output=True, text=True,
                           timeout=30)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
