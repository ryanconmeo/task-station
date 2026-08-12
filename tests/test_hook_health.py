"""Hooks stay non-fatal, but stop being silently broken.

Every task-station hook call is deliberately masked so a hook can never fail or
slow a session. The cost was invisibility: 38 masked call sites, any one of which
could have been permanently broken forever with nobody the wiser (the orphan sweep
was only proven working by running it by hand with the mask removed).

`hooks/_ts_lib.sh::ts_run` keeps the non-fatal contract and adds a record: on a
non-zero exit it appends one line to <data_dir>/logs/hook-health.log naming the
label, the exit code and the last line of stderr — then still returns success.
`lib/hook_health.py` reads that log for the SessionStart nag, and bounds/clears it.

Stdlib-only, no LLM. The shell half is exercised by really running bash.
"""
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(_REPO_ROOT, "lib")
HOOKS = os.path.join(_REPO_ROOT, "hooks")
TS_LIB_SH = os.path.join(HOOKS, "_ts_lib.sh")
sys.path.insert(0, LIB)

import hook_health  # noqa: E402
import store  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)

HOOK_FILES = ("on_session_start.sh", "on_user_prompt.sh", "on_stop.sh",
              "on_post_compact.sh", "on_post_tool.sh")


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _HomeMixin:
    """Pin the data home (and hence the health log) at a throwaway dir."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hook-health-")
        self._saved = {k: os.environ.get(k) for k in
                       ("TASK_STATION_HOME", "CLAUDE_CONFIG_DIR", "XDG_STATE_HOME")}
        os.environ["TASK_STATION_HOME"] = self.tmp
        self.log = os.path.join(self.tmp, "logs", "hook-health.log")

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- log helpers ----------------------------------------------------------
    def _write_log(self, *lines):
        os.makedirs(os.path.dirname(self.log), exist_ok=True)
        with open(self.log, "a", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")

    def _record(self, label, code=1, detail="boom", age=0):
        self._write_log("%s\t%s\t%s\t%s" % (
            hook_health.iso(time.time() - age), label, code, detail))

    def _log_lines(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding="utf-8") as f:
            return [ln for ln in f.read().splitlines() if ln.strip()]

    # -- bash helper ----------------------------------------------------------
    def _bash(self, script, **env_extra):
        env = dict(os.environ)
        env["TASK_STATION_HOME"] = self.tmp
        env.update({k: str(v) for k, v in env_extra.items()})
        body = '. "%s"\n%s\n' % (TS_LIB_SH, script)
        return subprocess.run(["bash", "-c", body], capture_output=True,
                              text=True, env=env)


# =============================================================== the shell ====
class TsLibShellTest(_HomeMixin, unittest.TestCase):
    def test_library_is_valid_bash(self):
        r = subprocess.run(["bash", "-n", TS_LIB_SH], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_failing_command_still_returns_success(self):
        r = self._bash('ts_run boom false; echo "rc=$?"')
        self.assertIn("rc=0", r.stdout)

    def test_failure_is_recorded_with_label_and_exit_code(self):
        self._bash("ts_run sweep-orphans sh -c 'echo kaboom >&2; exit 3'")
        lines = self._log_lines()
        self.assertEqual(len(lines), 1)
        fields = lines[0].split("\t")
        self.assertEqual(len(fields), 4)
        self.assertEqual(fields[1], "sweep-orphans")
        self.assertEqual(fields[2], "3")
        self.assertIn("kaboom", fields[3])

    def test_succeeding_command_records_nothing(self):
        self._bash("ts_run fine true")
        self.assertEqual(self._log_lines(), [])

    def test_missing_command_does_not_kill_the_hook(self):
        r = self._bash('ts_run nope ts-definitely-no-such-command-xyz; echo "rc=$?"')
        self.assertIn("rc=0", r.stdout)
        lines = self._log_lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].split("\t")[1], "nope")
        self.assertEqual(lines[0].split("\t")[2], "127")

    def test_label_with_no_command_is_a_noop(self):
        r = self._bash('ts_run lonely; echo "rc=$?"')
        self.assertIn("rc=0", r.stdout)
        self.assertEqual(self._log_lines(), [])

    def test_stdout_is_discarded_exactly_as_before(self):
        r = self._bash("ts_run noisy sh -c 'echo LOUD'")
        self.assertNotIn("LOUD", r.stdout)

    def test_stderr_never_leaks_to_the_hooks_stderr(self):
        # A hook's stderr is user-visible noise; ts_run captures it for the log.
        r = self._bash("ts_run quiet sh -c 'echo SECRET >&2; exit 1'")
        self.assertNotIn("SECRET", r.stderr)
        self.assertIn("SECRET", self._log_lines()[0])

    def test_multiline_stderr_becomes_one_line_holding_the_last(self):
        self._bash("ts_run multi sh -c 'printf \"first\\nsecond\\nlast\\n\" >&2; exit 2'")
        lines = self._log_lines()
        self.assertEqual(len(lines), 1)
        self.assertIn("last", lines[0])
        self.assertNotIn("first", lines[0])

    def test_tabs_in_stderr_cannot_forge_extra_fields(self):
        self._bash("ts_run tabby sh -c 'printf \"a\\tb\\n\" >&2; exit 1'")
        self.assertEqual(len(self._log_lines()[0].split("\t")), 4)

    def test_ts_capture_passes_stdout_through(self):
        r = self._bash('v=$(ts_capture cap sh -c "echo VALUE"); echo "[$v]"')
        self.assertIn("[VALUE]", r.stdout)
        self.assertEqual(self._log_lines(), [])

    def test_ts_capture_logs_a_failure_and_yields_empty(self):
        r = self._bash('v=$(ts_capture cap sh -c "echo x >&2; exit 4"); echo "[$v]"')
        self.assertIn("[]", r.stdout)
        self.assertEqual(self._log_lines()[0].split("\t")[2], "4")

    def test_ts_capture_inherits_stdin(self):
        # on_post_compact pipes the compaction summary INTO the logged command.
        r = self._bash('printf "hi" | ts_capture cat cat; echo "|"')
        self.assertIn("hi|", r.stdout)

    def test_log_is_bounded_dropping_the_oldest(self):
        for i in range(8):
            self._bash("ts_run label%d false" % i, TS_HOOK_LOG_MAX=5)
        lines = self._log_lines()
        self.assertLessEqual(len(lines), 5)
        blob = "\n".join(lines)
        self.assertNotIn("label0", blob)      # oldest dropped…
        self.assertIn("label7", blob)         # …newest kept

    def test_log_line_is_parseable_by_the_reader(self):
        self._bash("ts_run usage-flush sh -c 'echo bad >&2; exit 9'")
        got = hook_health.entries()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["label"], "usage-flush")
        self.assertEqual(got[0]["code"], 9)
        self.assertIsNotNone(got[0]["ts"])


# ============================================================ the hooks ======
class HookConversionTest(unittest.TestCase):
    """One implementation, not 38 bespoke edits: every hook sources the helper and
    routes its maskable calls through it."""

    def _read(self, name):
        with open(os.path.join(HOOKS, name), encoding="utf-8") as f:
            return f.read()

    def test_every_hook_is_valid_bash(self):
        for name in HOOK_FILES:
            r = subprocess.run(["bash", "-n", os.path.join(HOOKS, name)],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, "%s: %s" % (name, r.stderr))

    def test_every_hook_sources_the_shared_helper(self):
        for name in HOOK_FILES:
            self.assertIn("_ts_lib.sh", self._read(name), name)

    def test_every_hook_has_a_fallback_stub(self):
        # A missing/corrupt lib must degrade to exactly the old behaviour, never
        # leave the hook calling an undefined function.
        for name in HOOK_FILES:
            self.assertIn("command -v ts_run", self._read(name), name)

    # Comments and the fallback-stub block are the only places `|| true` survives.
    _STUB_PREFIXES = ("#", "if ! command -v", "ts_run()", "ts_capture()", "fi")

    def test_no_call_site_is_masked_with_or_true_any_more(self):
        for name in HOOK_FILES:
            for line in self._read(name).splitlines():
                if line.lstrip().startswith(self._STUB_PREFIXES):
                    continue
                self.assertNotIn(">/dev/null 2>&1 || true", line,
                                 "%s still masks: %s" % (name, line))

    def test_known_labels_are_wired(self):
        expected = {
            "on_session_start.sh": ("sweep-orphans", "obsidian-flush", "usage-flush"),
            # on_stop.sh's seven best-effort steps moved INTO lib/stop_steps.py (2.21.0,
            # one interpreter instead of seven); the shell now has one masked call site.
            # Their labels are asserted below, at their new home.
            "on_stop.sh": ("stop-steps",),
            "on_user_prompt.sh": ("prompt-tint", "prompt-title", "hud-turn-start"),
            "on_post_compact.sh": ("auto-checkpoint-get", "post-compact"),
            "on_post_tool.sh": ("touch-file", "capture-artifacts"),
        }
        for name, labels in expected.items():
            text = self._read(name)
            for label in labels:
                self.assertTrue("ts_run %s " % label in text
                                or "ts_capture %s " % label in text,
                                "%s is missing the %s label" % (name, label))

    def test_stop_step_labels_survived_the_consolidation(self):
        """The seven Stop labels are the only handle a human gets on which step broke,
        so they must stay the SAME strings after the move out of the shell."""
        import stop_steps
        labels = [label for label, _target, _argv in stop_steps.STEPS]
        self.assertEqual(labels, ["stop-nudge", "board-refresh", "obsidian-flush",
                                  "usage-flush", "subscriptions-check", "recap-auto",
                                  "hud-turn-end"])


# ============================================================ the reader =====
class ParseTest(unittest.TestCase):
    def test_good_line(self):
        e = hook_health.parse_line("2026-07-30T01:02:03Z\tsweep-orphans\t2\tboom")
        self.assertEqual(e["label"], "sweep-orphans")
        self.assertEqual(e["code"], 2)
        self.assertEqual(e["detail"], "boom")
        self.assertIsNotNone(e["ts"])

    def test_missing_detail_is_allowed(self):
        e = hook_health.parse_line("2026-07-30T01:02:03Z\tusage-flush\t1\t")
        self.assertEqual(e["detail"], "")

    def test_garbage_lines_are_dropped_not_raised(self):
        for bad in ("", "   ", "only-one-field", "a\tb", "a\tb\tc",
                    "not-a-date\tlabel\tnope\tx"):
            self.assertIsNone(hook_health.parse_line(bad), bad)

    def test_non_numeric_code_is_dropped(self):
        self.assertIsNone(
            hook_health.parse_line("2026-07-30T01:02:03Z\tlabel\tboom\tx"))


class NagTest(_HomeMixin, unittest.TestCase):
    def test_no_log_no_nag(self):
        self.assertIsNone(hook_health.nag())

    def test_empty_log_no_nag(self):
        self._write_log("")
        self.assertIsNone(hook_health.nag())

    def test_nag_appears_for_a_recent_failure(self):
        self._record("sweep-orphans", code=2)
        n = hook_health.nag()
        self.assertIsNotNone(n)
        self.assertNotIn("\n", n)                     # strictly one line
        self.assertTrue(n.startswith("[task-station]"))
        self.assertIn("sweep-orphans", n)
        self.assertIn("2", n)
        self.assertIn("hook-health", n)               # points at the log / command

    def test_nag_counts_every_recent_failure(self):
        for i in range(4):
            self._record("label%d" % i)
        n = hook_health.nag()
        self.assertIn("4", n)

    def test_nag_stays_one_line_with_many_labels(self):
        for i in range(12):
            self._record("label%d" % i)
        n = hook_health.nag()
        self.assertNotIn("\n", n)
        self.assertLess(len(n), 400)

    # -- code 0 = INFORMATIONAL, and the nag is an ALARM ----------------------
    #
    # The FileChanged trigger and the WorktreeCreate provisioner record what they DID.
    # Those belong in the log — `hook-health` is the full record — but a line whose
    # whole sentence is "N hook failure(s)" must not count them, or a routine config
    # edit announces itself at the next session start as a broken hook.

    def test_an_informational_record_never_nags(self):
        self._record("file-changed", code=0, detail="config.json modified")
        self.assertIsNone(hook_health.nag())

    def test_informational_records_do_not_inflate_the_count(self):
        self._record("file-changed", code=0)
        self._record("sweep-orphans", code=2)
        n = hook_health.nag()
        self.assertIn("1 hook failure(s)", n)
        self.assertNotIn("file-changed", n)

    def test_an_informational_record_does_not_stamp_away_a_later_failure(self):
        """It must not advance the watermark either — a real failure landing after it
        still has to speak."""
        self._record("worktree-create", code=0)
        self.assertIsNone(hook_health.nag())
        self._record("obsidian-flush", code=1)
        self.assertIsNotNone(hook_health.nag())

    def test_informational_records_still_list_in_the_summary(self):
        self._record("file-changed", code=0, detail="config.json modified")
        self.assertIn("file-changed", "\n".join(hook_health.summary()))

    def test_nag_does_not_repeat_for_the_same_failures(self):
        self._record("sweep-orphans")
        self.assertIsNotNone(hook_health.nag())
        self.assertIsNone(hook_health.nag())
        self.assertIsNone(hook_health.nag())

    def test_a_new_failure_re_arms_the_nag(self):
        self._record("sweep-orphans", age=60)
        self.assertIsNotNone(hook_health.nag())
        self.assertIsNone(hook_health.nag())
        self._record("usage-flush")                   # newer than the stamp
        n = hook_health.nag()
        self.assertIsNotNone(n)
        self.assertIn("usage-flush", n)

    def test_old_failures_do_not_nag(self):
        self._record("ancient", age=hook_health.RECENT_WINDOW + 600)
        self.assertIsNone(hook_health.nag())

    def test_malformed_log_does_not_raise(self):
        self._write_log("garbage", "\t\t\t", "x")
        self.assertIsNone(hook_health.nag())


class ClearTest(_HomeMixin, unittest.TestCase):
    def test_clear_empties_the_log_and_silences_the_nag(self):
        self._record("sweep-orphans")
        self.assertEqual(hook_health.clear(), 1)
        self.assertEqual(self._log_lines(), [])
        self.assertIsNone(hook_health.nag())

    def test_clear_on_a_missing_log_is_zero_not_an_error(self):
        self.assertEqual(hook_health.clear(), 0)

    def test_a_failure_after_a_clear_nags_again(self):
        self._record("sweep-orphans")
        hook_health.nag()
        hook_health.clear()
        self._record("usage-flush")
        self.assertIsNotNone(hook_health.nag())


class SummaryTest(_HomeMixin, unittest.TestCase):
    def test_summary_of_an_empty_log(self):
        self.assertEqual(hook_health.summary(), [])

    def test_summary_is_newest_last_and_capped(self):
        for i in range(5):
            self._record("label%d" % i, code=i, age=(5 - i) * 10)
        rows = hook_health.summary(limit=3)
        self.assertEqual(len(rows), 3)
        self.assertIn("label4", rows[-1])
        self.assertNotIn("label0", "\n".join(rows))


class CliTest(_HomeMixin, unittest.TestCase):
    def _run(self, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_hook_health(_Args(**dict({"clear": False}, **kw)))
        return buf.getvalue()

    def test_reports_a_clean_log(self):
        self.assertIn("no hook failures", self._run().lower())

    def test_lists_recorded_failures(self):
        self._record("sweep-orphans", code=2, detail="traceback here")
        out = self._run()
        self.assertIn("sweep-orphans", out)
        self.assertIn("2", out)

    def test_clear_flag_empties_the_log(self):
        self._record("sweep-orphans")
        out = self._run(clear=True)
        self.assertIn("clear", out.lower())
        self.assertEqual(self._log_lines(), [])


class SessionStartNagTest(_HomeMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self._paths = (ts.DATA, ts.STORE, ts.TASKS_DIR, ts.LINKS_DIR, ts.PROJECTS_ROOT)
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.PROJECTS_ROOT = os.path.join(self.tmp, "projects")
        store.reset_cache()

    def tearDown(self):
        store.reset_cache()
        (ts.DATA, ts.STORE, ts.TASKS_DIR, ts.LINKS_DIR, ts.PROJECTS_ROOT) = self._paths
        super().tearDown()

    def _session_start(self, session="sess-1"):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_session_start(_Args(session=session, source="startup"))
        return buf.getvalue()

    def test_silent_when_nothing_to_say(self):
        self.assertEqual(self._session_start().strip(), "")

    def test_nag_shows_with_no_tasks_at_all(self):
        self._record("sweep-orphans", code=2)
        self.assertIn("sweep-orphans", self._session_start())

    def test_nag_shows_alongside_an_attached_task(self):
        with redirect_stdout(io.StringIO()):
            ts.cmd_create(_Args(session="sess-2", title="Projectname sweep",
                                summary="", color=None, effort=None, goal=None,
                                step=None, force=True, no_attach=False,
                                attach=True, active=False))
        self._record("obsidian-flush", code=1)
        out = self._session_start(session="sess-2")
        self.assertIn("attached to task", out)
        self.assertIn("obsidian-flush", out)

    def test_clean_log_leaves_the_attached_block_untouched(self):
        with redirect_stdout(io.StringIO()):
            ts.cmd_create(_Args(session="sess-3", title="Projectname sweep",
                                summary="", color=None, effort=None, goal=None,
                                step=None, force=True, no_attach=False,
                                attach=True, active=False))
        out = self._session_start(session="sess-3")
        self.assertIn("attached to task", out)
        self.assertNotIn("hook failure", out)

    def test_skipped_session_stays_silent_even_with_failures(self):
        with redirect_stdout(io.StringIO()):
            ts.cmd_skip(_Args(session="sess-4"))
        self._record("sweep-orphans")
        self.assertEqual(self._session_start(session="sess-4").strip(), "")


if __name__ == "__main__":
    unittest.main()
