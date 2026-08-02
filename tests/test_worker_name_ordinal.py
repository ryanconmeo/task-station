"""Worker display names carry the SPAWNING hub's ordinal.

`task-station-<seq>-<ordinal>-<project>[-<label>]` — the ordinal is the roster
number (`<seq>-<n>`) of the hub session that launched the worker, resolved at spawn
time. It records PROVENANCE: a different hub resuming the worker later must not
rename it (the resuming actor is recorded separately as the ledger `actor_ordinal`).

The load-bearing invariant: `_is_ts_worker_name()` gates ALL reap logic on the name
starting with `task-station-` / `wk-`. The ordinal is inserted AFTER the seq so that
prefix is untouched — any format that moved it would make the reaper blind to every
worker it ever spawned. That is asserted explicitly below.

No real `claude`, no real processes: `whoami --porcelain` is stubbed and the
registry is a temp dir.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)
sys.path.insert(0, os.path.join(LIB, "delegate"))

import delegate as dg  # noqa: E402


class FakeAdapter:
    """Scripted `claude agents --json` index."""
    def __init__(self, index):
        self.index = dict(index)

    def agents_index(self, cwd=None):
        return dict(self.index)


class WorkerNameOrdinalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-wname-")
        self.saved_ord = dg._spawner_ordinal
        # Default: an ordinal IS resolvable, so the happy path is the new format.
        dg._spawner_ordinal = lambda seq: 3

    def tearDown(self):
        dg._spawner_ordinal = self.saved_ord
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- the new format --------------------------------------------------------

    def test_name_carries_the_spawning_hub_ordinal(self):
        self.assertEqual(
            dg._worker_name(444, "projectname", None, None),
            "task-station-444-3-projectname")

    def test_label_suffix_follows_the_ordinal(self):
        self.assertEqual(
            dg._worker_name(444, "projectname", "packs", None),
            "task-station-444-3-projectname-packs")

    # -- the reaper must still recognise it (CRITICAL) --------------------------

    def test_is_ts_worker_name_still_true_for_the_new_format(self):
        for nm in ("task-station-444-3-projectname",
                   "task-station-444-3-projectname-packs",
                   "task-station-444-0-projectname",       # ordinal 0
                   "task-station-444-projectname",         # no-ordinal fallback
                   "wk-projectname-mytree"):               # unchanged no-seq form
            self.assertTrue(dg._is_ts_worker_name(nm), nm)

    def test_reap_still_matches_a_worker_spawned_under_the_new_format(self):
        """End-to-end: the identity predicate accepts a new-format name, so a worker
        spawned today is still reapable tomorrow."""
        saved = (dg.REG_DIR, dg.REG, dg._kill_pid_group, dg.SESSIONS_DIR)
        sessdir = tempfile.mkdtemp(prefix="ts-sess-")
        killed = []
        try:
            dg.REG_DIR = self.tmp
            dg.REG = os.path.join(self.tmp, "workers.json")
            dg.SESSIONS_DIR = sessdir
            dg._kill_pid_group = lambda pid, **k: killed.append(pid)
            os.environ["TASK_STATION_REAP_WORKERS_ON_DONE"] = "on"
            nm = "task-station-7-3-projectname"
            with open(dg.REG, "w") as f:
                json.dump({"7:projectname": {"seq": 7, "session_id": "w1"}}, f)
            ad = FakeAdapter({"w1": {"sessionId": "w1", "pid": 111,
                                     "status": "idle", "name": nm}})
            roster = {"w1": {"role": "worker", "name": nm}}
            self.assertEqual(dg.reap_task_workers(7, adapter=ad, roster=roster), ["w1"])
            self.assertEqual(killed, [111])
        finally:
            os.environ.pop("TASK_STATION_REAP_WORKERS_ON_DONE", None)
            dg.REG_DIR, dg.REG, dg._kill_pid_group, dg.SESSIONS_DIR = saved
            shutil.rmtree(sessdir, ignore_errors=True)

    # -- fallback when no ordinal is resolvable --------------------------------

    def test_falls_back_to_todays_format_without_an_ordinal(self):
        dg._spawner_ordinal = lambda seq: None
        nm = dg._worker_name(444, "projectname", None, None)
        self.assertEqual(nm, "task-station-444-projectname")
        self.assertNotIn("--", nm)              # no empty segment
        self.assertFalse(nm.endswith("-"))      # no dangling separator

    def test_fallback_still_takes_the_label_suffix(self):
        dg._spawner_ordinal = lambda seq: None
        self.assertEqual(dg._worker_name(444, "projectname", "packs", None),
                         "task-station-444-projectname-packs")

    def test_ordinal_zero_is_a_real_segment(self):
        """Ordinal 0 is a real hub (the task's creator) — it must not read as absent."""
        dg._spawner_ordinal = lambda seq: 0
        self.assertEqual(dg._worker_name(444, "projectname", None, None),
                         "task-station-444-0-projectname")

    # -- provenance is fixed at spawn time ------------------------------------

    def test_resume_reuses_the_recorded_name(self):
        """A DIFFERENT hub resuming the worker must not rename it."""
        dg._spawner_ordinal = lambda seq: 9          # the resuming hub is 444-9…
        entry = {"name": "task-station-444-3-projectname"}   # …but -3 spawned it
        self.assertEqual(
            dg._worker_name(444, "projectname", None, None, entry=entry, resuming=True),
            "task-station-444-3-projectname")

    def test_fresh_run_ignores_a_recorded_name(self):
        """`--fresh` is a NEW worker (resuming=False) → the name is rebuilt."""
        entry = {"name": "task-station-444-8-projectname"}
        self.assertEqual(
            dg._worker_name(444, "projectname", None, None, entry=entry, resuming=False),
            "task-station-444-3-projectname")

    def test_resume_with_no_recorded_name_builds_one(self):
        """A pre-ordinal registry entry has no recorded name → build the current form
        rather than returning None."""
        self.assertEqual(
            dg._worker_name(444, "projectname", None, None, entry={}, resuming=True),
            "task-station-444-3-projectname")

    # -- the no-seq worktree form is untouched --------------------------------

    def test_no_seq_worktree_form_is_unchanged(self):
        self.assertEqual(dg._worker_name(None, "projectname", None, "mytree"),
                         "wk-projectname-mytree")

    def test_no_seq_no_worktree_is_still_nameless(self):
        self.assertIsNone(dg._worker_name(None, "projectname", None, None))

    def test_no_seq_no_worktree_with_label_keeps_the_project_fallback(self):
        self.assertEqual(dg._worker_name(None, "projectname", "packs", None),
                         "projectname-packs")

    def test_seq_is_preferred_over_the_wk_form(self):
        """A seq is available → the `task-station-` form wins even with a worktree."""
        self.assertEqual(dg._worker_name(444, "projectname", None, "mytree"),
                         "task-station-444-3-projectname")

    # -- resolving the spawning hub's ordinal ---------------------------------

    def _ordinal_from(self, porcelain, seq=444, sid="hub-sid"):
        """Run the REAL `_spawner_ordinal` against a scripted `whoami --porcelain`."""
        saved = dg._whoami_porcelain
        try:
            os.environ["CLAUDE_CODE_SESSION_ID"] = sid
            dg._whoami_porcelain = lambda s: porcelain
            dg._spawner_ordinal = self.saved_ord
            return dg._spawner_ordinal(seq)
        finally:
            dg._whoami_porcelain = saved
            os.environ.pop("CLAUDE_CODE_SESSION_ID", None)

    def test_spawner_ordinal_reads_the_porcelain_label(self):
        self.assertEqual(self._ordinal_from("444\t444-3\thub\n"), 3)

    def test_spawner_ordinal_accepts_zero(self):
        self.assertEqual(self._ordinal_from("444\t444-0\thub\n"), 0)

    def test_spawner_ordinal_ignores_a_label_for_a_different_task(self):
        """An explicit `--seq` can differ from the caller's ATTACHED task; borrowing
        that task's ordinal would misrecord provenance, so it is refused."""
        self.assertIsNone(self._ordinal_from("999\t999-3\thub\n"))

    def test_spawner_ordinal_none_for_a_worker_session(self):
        """A worker has no ordinal → porcelain field 2 is empty → no ordinal."""
        self.assertIsNone(self._ordinal_from("444\t\tworker\n"))

    def test_spawner_ordinal_none_when_unattached(self):
        """`--porcelain` prints nothing when the session has no task."""
        self.assertIsNone(self._ordinal_from(""))

    def test_spawner_ordinal_none_without_a_session_env(self):
        dg._spawner_ordinal = self.saved_ord
        os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        self.assertIsNone(dg._spawner_ordinal(444))

    def test_whoami_porcelain_swallows_a_failing_call(self):
        saved_run = dg.subprocess.run

        def _boom(*a, **k):
            raise OSError("no python3")

        try:
            dg.subprocess.run = _boom
            self.assertEqual(dg._whoami_porcelain("hub-sid"), "")
        finally:
            dg.subprocess.run = saved_run

    # -- the name persists on the registry entry ------------------------------

    def test_registry_entry_records_the_name_and_carries_it_forward(self):
        saved = (dg.REG_DIR, dg.REG)
        try:
            dg.REG_DIR = self.tmp
            dg.REG = os.path.join(self.tmp, "workers.json")
            reg = {}
            nm = "task-station-444-3-projectname"
            dg._save_entry(reg, "444:projectname", "projectname", 444, None,
                           "/tmp/wt", "sid-1", name=nm)
            self.assertEqual(dg.load_reg()["444:projectname"]["name"], nm)
            # A later refresh that does NOT pass a name must preserve the recorded
            # one — the entry is rebuilt from scratch on every write.
            dg._save_entry(reg, "444:projectname", "projectname", 444, None,
                           "/tmp/wt", "sid-1")
            self.assertEqual(dg.load_reg()["444:projectname"]["name"], nm)
        finally:
            dg.REG_DIR, dg.REG = saved


if __name__ == "__main__":
    unittest.main()
