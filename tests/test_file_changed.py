"""FileChanged — the pointer/drift re-arm (`task-station file-changed`).

A station config file changed on disk (an external editor counts). The checker's
pointer and drift nags are SELF-CAPPING: each stays silent until the state it
fingerprinted changes. When the config they were evaluated against changes underneath
them, that cap is now guarding a stale answer — so the gate file is dropped and both
nags re-evaluate at the next session start.

The two properties that make this safe:

  1. THE MATCHER IS BASENAME-LEVEL. Every project's `config.json` reaches this hook,
     so the FULL-PATH test (inside `paths.data_dir()`) is what makes a file ours. A
     miss here would clear a gate on every unrelated repo's config edit.
  2. THE RECORD IS INFORMATIONAL. It is written with exit code 0, and `hook_health.nag`
     ignores code 0 — otherwise a routine config edit would announce itself at the next
     session start as a "hook failure", which is the cry-wolf failure that whole
     subsystem exists to avoid.
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

import checker         # noqa: E402
import hook_health     # noqa: E402
import store as store_mod   # noqa: E402

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FileChangedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-filechanged-")
        self._env = os.environ.get("TASK_STATION_HOME")
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.PROJECTS_ROOT = os.path.join(self.tmp, "projects")
        store_mod.reset_cache()
        self.elsewhere = tempfile.mkdtemp(prefix="ts-otherproject-")

    def tearDown(self):
        if self._env is None:
            os.environ.pop("TASK_STATION_HOME", None)
        else:
            os.environ["TASK_STATION_HOME"] = self._env
        store_mod.reset_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.elsewhere, ignore_errors=True)

    # -- fixtures --------------------------------------------------------------

    def _task(self, session="s1"):
        t = ts.new_task("A tracked task", "summary")
        ts.save_task(t)
        ts.ensure_seqs()
        ts.set_link(session, t["id"])
        return ts.load_task(t["id"])

    def _gate(self, task_id):
        checker.write_gate(task_id, {"pointer_sig": "abc", "drift_sig": "def"})
        return checker.gate_path(task_id)

    def _file(self, name="config.json", where=None):
        path = os.path.join(where or self.tmp, name)
        with open(path, "w") as f:
            f.write("{}\n")
        return path

    def _run(self, path, session="s1", change="modified"):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_file_changed(_Args(session=session, file=path, change=change))
        return buf.getvalue()

    # -- the path filter -------------------------------------------------------

    def test_another_projects_config_json_is_ignored(self):
        """The matcher fires on the BASENAME, so this is the common case, not an
        edge one: a config.json in any repo the user edits."""
        task = self._task()
        gate = self._gate(task["id"])
        self._run(self._file(where=self.elsewhere))
        self.assertTrue(os.path.exists(gate))          # untouched
        self.assertEqual(hook_health.entries(), [])

    def test_a_data_dir_config_json_re_arms_the_gate(self):
        task = self._task()
        gate = self._gate(task["id"])
        self._run(self._file())
        self.assertFalse(os.path.exists(gate))         # both nags re-armed

    def test_every_watched_basename_is_handled(self):
        for name in ts.STATION_WATCHED_FILES:
            task = self._task(session="s-%s" % name)
            gate = self._gate(task["id"])
            self._run(self._file(name=name), session="s-%s" % name)
            self.assertFalse(os.path.exists(gate), name)

    def test_a_relative_or_empty_path_is_a_noop(self):
        self._run("")
        self._run(None)
        self.assertEqual(hook_health.entries(), [])

    # -- the record ------------------------------------------------------------

    def test_records_informationally_with_exit_code_zero(self):
        task = self._task()
        self._gate(task["id"])
        self._run(self._file(), change="created")
        recs = hook_health.entries()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["label"], "file-changed")
        self.assertEqual(recs[0]["code"], 0)           # informational, not a failure
        self.assertIn("config.json", recs[0]["detail"])
        self.assertIn("created", recs[0]["detail"])
        self.assertIn(task["id"][:8], recs[0]["detail"])

    def test_the_informational_record_never_nags(self):
        """THE point of code 0: a config edit must not surface at the next session
        start as 'N hook failure(s)'."""
        self._task()
        self._run(self._file())
        self.assertTrue(hook_health.entries())         # it IS in the log…
        self.assertIsNone(hook_health.nag())           # …and it is NOT an alarm
        self.assertIn("file-changed", "\n".join(hook_health.summary()))

    def test_a_real_failure_still_nags_alongside_it(self):
        self._task()
        self._run(self._file())
        hook_health.record("sweep-orphans", 2, "boom")
        self.assertIn("sweep-orphans", hook_health.nag() or "")

    def test_a_data_dir_file_we_do_not_read_records_nothing(self):
        """Inside our dir but not one of ours: the gate still re-arms (harmless), and
        we say nothing, because a record naming a file the station never reads would
        be noise."""
        task = self._task()
        gate = self._gate(task["id"])
        self._run(self._file(name="scratch.json"))
        self.assertFalse(os.path.exists(gate))
        self.assertEqual(hook_health.entries(), [])

    # -- the session link ------------------------------------------------------

    def test_an_unattached_session_still_records(self):
        self._run(self._file(), session="nobody")
        recs = hook_health.entries()
        self.assertEqual(len(recs), 1)
        self.assertNotIn("re-armed", recs[0]["detail"])

    def test_a_skipped_session_clears_no_gate(self):
        task = self._task(session="s1")
        gate = self._gate(task["id"])
        ts.set_link("skipme", ts.SKIP_SENTINEL)
        self._run(self._file(), session="skipme")
        self.assertTrue(os.path.exists(gate))

    def test_prints_nothing(self):
        """FileChanged cannot inject context — the re-armed gate IS the mechanism."""
        self._task()
        self.assertEqual(self._run(self._file()), "")


if __name__ == "__main__":
    unittest.main()
