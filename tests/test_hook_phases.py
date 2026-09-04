"""3.64.0 perf: which hook steps the turn WAITS for, and which it does not.

MEASURED on 3.63.0 against a real session, per command (tools/hooklat.py, which
reads the harness's own transcript records rather than a stopwatch):

    hookmux session-start   p50 18.6s over 273 runs, max 78.2s
      on_session_start.sh                  23.0s, of which
        hook sweep-orphans                 20.7s
        usage --flush                       1.1s
    hookmux stop            12.9s
      on_stop.sh                           12.9s, of which
        board --refresh-if-live            11.3s
        usage --flush                       1.1s

None of those steps prints anything a hook contract reads, and the user waited for
all of them. So they detach. What these tests pin down is the part that is easy to
lose while making something fast:

  • the split is by the STDOUT CONTRACT and nothing else — a step whose output the
    harness reads runs inline, every other step detaches, and no step is in both
  • the detached phase still runs every step it was given, in order, through the
    same isolation and the same hook-health labels — detaching moved WHO WAITS, it
    did not drop the work
  • the default phase actually detaches, rather than quietly running inline
  • a spawn that FAILS is recorded and reported False, so a hook whose housekeeping
    never started cannot look like one whose housekeeping ran

Stdlib-only unittest, no LLM, never touches the real store.
"""
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(_REPO_ROOT, "lib")
HOOKS = os.path.join(_REPO_ROOT, "hooks")
sys.path.insert(0, LIB)

import hook_steps  # noqa: E402


class PhaseSplitTest(unittest.TestCase):
    def test_foreground_is_exactly_the_passthrough_steps(self):
        for event in hook_steps.EVENTS:
            fg = [s[0] for s in hook_steps.steps_for(event, "foreground")]
            self.assertEqual(fg, [s[0] for s in hook_steps.steps_for(event, "all")
                                  if s[0] in hook_steps.PASSTHROUGH], event)

    def test_a_step_is_never_in_both_phases(self):
        for event in hook_steps.EVENTS:
            fg = {s[0] for s in hook_steps.steps_for(event, "foreground")}
            bg = {s[0] for s in hook_steps.steps_for(event, "background")}
            self.assertEqual(fg & bg, set(), event)

    def test_the_two_phases_together_are_the_whole_table(self):
        for event in hook_steps.EVENTS:
            both = (list(hook_steps.steps_for(event, "foreground"))
                    + list(hook_steps.steps_for(event, "background")))
            self.assertCountEqual(both, list(hook_steps.steps_for(event, "all")), event)

    def test_stop_waits_only_for_the_nudge(self):
        self.assertEqual([s[0] for s in hook_steps.steps_for("stop", "foreground")],
                         ["stop-nudge"])

    def test_the_measured_offenders_are_in_the_background(self):
        """The named cost of each event, by label, must be off the critical path."""
        bg_stop = {s[0] for s in hook_steps.steps_for("stop", "background")}
        self.assertIn("board-refresh", bg_stop)
        bg_start = {s[0] for s in hook_steps.steps_for("session-start", "background")}
        self.assertIn("sweep-orphans", bg_start)

    def test_session_start_waits_for_nothing(self):
        """The shell keeps every SessionStart step whose stdout it reads; the ones that
        moved into this table are silent by definition, so none of them is foreground."""
        self.assertEqual(hook_steps.steps_for("session-start", "foreground"), ())

    def test_an_unknown_event_is_no_steps_rather_than_a_crash(self):
        self.assertEqual(hook_steps.steps_for("no-such-event", "all"), ())


class _Recorder:
    """A fake engine that records the argv of every step that reaches it."""

    def __init__(self):
        self.seen = []

    def main(self, argv):
        self.seen.append(list(argv))
        return 0


class SchedulingTest(unittest.TestCase):
    def setUp(self):
        self.rec = _Recorder()
        real = hook_steps._engine
        hook_steps._engine = lambda: self.rec
        self.addCleanup(setattr, hook_steps, "_engine", real)
        self.detached = []
        real_detach = hook_steps.detach
        hook_steps.detach = lambda event, session: (
            self.detached.append((event, session)) or True)
        self.addCleanup(setattr, hook_steps, "detach", real_detach)

    def _main(self, *args):
        with redirect_stdout(io.StringIO()):
            return hook_steps.main(list(args))

    def test_the_default_phase_runs_foreground_and_detaches_the_rest(self):
        self._main("--event", "stop", "--session", "sid1")
        self.assertEqual([a[:2] for a in self.rec.seen], [["hook", "stop-nudge"]])
        self.assertEqual(self.detached, [("stop", "sid1")])

    def test_the_background_phase_runs_the_rest_and_detaches_nothing(self):
        self._main("--event", "stop", "--phase", "background", "--session", "sid1")
        labels = [a[0] for a in self.rec.seen]
        self.assertEqual(labels, ["board", "obsidian", "usage", "subscriptions",
                                  "recap"])
        self.assertEqual(self.detached, [])

    def test_background_keeps_the_declared_order(self):
        self._main("--event", "session-start", "--phase", "background",
                   "--session", "sid1")
        self.assertEqual([a[0] for a in self.rec.seen],
                         ["obsidian", "usage", "hook", "hook"])

    def test_session_start_default_detaches_everything(self):
        self._main("--event", "session-start", "--session", "sid9")
        self.assertEqual(self.rec.seen, [])
        self.assertEqual(self.detached, [("session-start", "sid9")])

    def test_the_session_id_reaches_the_detached_phase_verbatim(self):
        self._main("--event", "session-start", "--phase", "background",
                   "--session", "100%-not-a-format-string")
        self.assertIn(["hook", "sweep-orphans", "--session",
                       "100%-not-a-format-string"], self.rec.seen)


class DetachTest(unittest.TestCase):
    def setUp(self):
        """A failed detach RECORDS to <data_dir>/logs/hook-health.log, so this case
        gets a throwaway data dir — a test that proves the recording works must not
        prove it in the user's own log."""
        tmp = tempfile.mkdtemp(prefix="ts-detach-")
        self.addCleanup(shutil.rmtree, tmp, True)
        prior = os.environ.get("TASK_STATION_HOME")
        os.environ["TASK_STATION_HOME"] = tmp
        self.addCleanup(lambda: os.environ.__setitem__("TASK_STATION_HOME", prior)
                        if prior is not None
                        else os.environ.pop("TASK_STATION_HOME", None))
        self.tmp = tmp

    def test_a_failed_spawn_is_reported_false_and_never_raises(self):
        import subprocess
        real = subprocess.Popen

        def _boom(*a, **k):
            raise OSError("no fork for you")

        subprocess.Popen = _boom
        self.addCleanup(setattr, subprocess, "Popen", real)
        self.assertFalse(hook_steps.detach("stop", "sid1"))
        with open(os.path.join(self.tmp, "logs", "hook-health.log"),
                  encoding="utf-8") as fh:
            self.assertIn("stop-detach", fh.read())

    def test_a_real_spawn_leaves_the_hook_and_says_so(self):
        """The child must be started in a session of its OWN — that is the whole
        mechanism, so it is asserted on the call, not on the docstring."""
        import subprocess
        real = subprocess.Popen
        seen = {}

        def _spy(argv, **kw):
            seen["argv"], seen["kw"] = list(argv), kw
            return real([sys.executable, "-c", "pass"], **kw)

        subprocess.Popen = _spy
        self.addCleanup(setattr, subprocess, "Popen", real)
        self.assertTrue(hook_steps.detach("session-start", "sid1"))
        self.assertTrue(seen["kw"].get("start_new_session"))
        self.assertIn("--phase", seen["argv"])
        self.assertEqual(seen["argv"][seen["argv"].index("--phase") + 1],
                         "background")
        self.assertIn("hook_steps.py", " ".join(seen["argv"]))


class HookWiringTest(unittest.TestCase):
    """The shells must call the runner the new way, and must not have kept a copy of
    a step they handed over."""

    def _read(self, name):
        with open(os.path.join(HOOKS, name), encoding="utf-8") as fh:
            return fh.read()

    def test_session_start_hands_its_silent_steps_to_the_runner(self):
        body = self._read("on_session_start.sh")
        self.assertIn('hook_steps.py" --event session-start', body)
        for gone in ("obsidian --flush", "usage --flush", "sweep-orphans --session"):
            self.assertNotIn(gone, body, gone)

    def test_stop_still_calls_the_runner_for_its_event(self):
        self.assertIn('hook_steps.py" --event stop', self._read("on_stop.sh"))

    def test_the_session_start_launcher_is_recorded_if_it_fails(self):
        body = self._read("on_session_start.sh")
        self.assertIn("ts_run session-start-steps ", body)


if __name__ == "__main__":
    unittest.main()
