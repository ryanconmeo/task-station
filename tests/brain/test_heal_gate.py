"""The episodic-aware heal gate (``brain.heal_gate``) — due/not-due matrix + stamp reset.

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 4a) from the brain source tree's
``tests/test_reconcile_gate.py`` @ 0.14.0. All 10 source cases port unchanged in
shape. Two mechanical differences:

  * the module is imported normally (``import brain.heal_gate``) — the source had
    to load ``reconcile-gate.py`` through ``importlib.util.spec_from_file_location``
    because a hyphenated filename is not importable. The rename retires that trick.
  * the completion stamp is ``.last-heal`` (the module completed the source's
    half-finished reconcile → heal rename), so ``_stamp`` writes that name. The
    reason substrings the cases assert on ("no previous", "clean", "<24h") are
    unchanged.

NOTE the inheritance: ``StreamDirtinessTest`` subclasses ``HealGateTest``, so it
RE-RUNS all 9 of its methods under a second class name. That is verbatim from the
source (it is how the source ran), and it means 10 defined cases COLLECT as 19.
"""
import json
import os
import subprocess

from tests.brain.base import BrainTestCase

import brain.config as bconfig
import brain.heal_gate as gate

T = 1_000_000.0          # fixed epoch base
DAY = 24 * 3600


class HealGateTest(BrainTestCase):
    def setUp(self):
        super().setUp()
        self.vault = self.make_vault(self.home / "vault")
        self.tasks_db = self.home / "tasks.db"
        self.cfg = {"vault": self.vault, "tasks_db": self.tasks_db}

    def _stamp(self, ts, head=None):
        p = bconfig.state_dir() / gate.STAMP_NAME
        p.write_text(json.dumps({"head": head, "ts": ts}))
        return p

    def _touch(self, path, mtime):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
        os.utime(path, (mtime, mtime))

    # --- due / not-due matrix ----------------------------------------------
    def test_no_stamp_is_due(self):
        res = gate.compute(self.cfg, now=T)
        self.assertTrue(res["due"])
        self.assertIn("no previous", res["reasons"][0])

    def test_clean_is_not_due(self):
        self._stamp(T)
        res = gate.compute(self.cfg, now=T + 2 * DAY)  # stale but nothing changed
        self.assertFalse(res["due"])
        self.assertIn("clean", res["reasons"][0])

    def test_tasks_db_only_dirty_and_stale_is_due(self):
        self._stamp(T)
        self._touch(self.tasks_db, T + 100)            # only the tasks_db moved
        res = gate.compute(self.cfg, now=T + 2 * DAY)
        self.assertTrue(res["due"])
        self.assertTrue(any("tasks_db" in r for r in res["reasons"]))

    def test_dirty_but_within_24h_is_not_due(self):
        self._stamp(T)
        self._touch(self.tasks_db, T + 100)
        res = gate.compute(self.cfg, now=T + 3600)     # dirty, but <24h since last pass
        self.assertFalse(res["due"])
        self.assertIn("<24h", res["reasons"][0])

    def test_raw_capture_dirty_is_due(self):
        self._stamp(T)
        self._touch(self.vault / "raw" / "2026-07-14-auto-x.md", T + 500)
        res = gate.compute(self.cfg, now=T + 2 * DAY)
        self.assertTrue(res["due"])
        self.assertTrue(any("raw" in r for r in res["reasons"]))

    def test_missing_sources_are_graceful(self):
        self._stamp(T)                                 # no tasks_db, empty raw, no reports/health
        res = gate.compute(self.cfg, now=T + 2 * DAY)
        self.assertFalse(res["due"])                   # nothing dirty, no crash

    def test_head_move_is_dirty(self):
        subprocess.run(["git", "-C", str(self.vault), "init"], capture_output=True)
        subprocess.run(["git", "-C", str(self.vault), "config", "user.email", "t@e.com"], capture_output=True)
        subprocess.run(["git", "-C", str(self.vault), "config", "user.name", "T"], capture_output=True)
        (self.vault / "f.txt").write_text("x")
        subprocess.run(["git", "-C", str(self.vault), "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", str(self.vault), "commit", "-m", "init"], capture_output=True)
        self._stamp(T, head="0" * 40)                  # stamp recorded a different HEAD
        res = gate.compute(self.cfg, now=T + 2 * DAY)
        self.assertTrue(res["due"])
        self.assertTrue(any("HEAD" in r for r in res["reasons"]))

    # --- stamp reset --------------------------------------------------------
    def test_mark_done_resets_dueness(self):
        self._touch(self.tasks_db, T + 100)
        self.assertTrue(gate.compute(self.cfg, now=T + 2 * DAY)["due"])   # dirty (no stamp)
        gate.mark_done(self.cfg, now=T + 200)                            # completion stamp
        after = gate.compute(self.cfg, now=T + 200 + 2 * DAY)
        self.assertFalse(after["due"])                                   # tasks_db older than stamp -> clean

    def test_session_start_nag_only_when_due(self):
        # not due -> no output; due -> a one-line SessionStart nag
        self._stamp(T)
        env_clean = gate.compute(self.cfg, now=T + 2 * DAY)
        self.assertFalse(env_clean["due"])
        self.assertEqual(gate._nag_line({"reasons": ["new raw/ captures"]}).count("\n"), 0)
        self.assertIn("/brain-heal", gate._nag_line({"reasons": ["x"]}))


class StreamDirtinessTest(HealGateTest):
    def test_stream_mtime_alone_makes_dirty_and_due(self):
        """New Tasktrail stream activity must make a heal due (episodic-aware gate)."""
        self._stamp(T)
        shard = self.home / "stream" / "events" / "2026-07.jsonl"
        self._touch(shard, T + DAY)
        self.cfg["episodic_stream"] = self.home / "stream"
        res = gate.compute(self.cfg, now=T + 2 * DAY)
        self.assertTrue(res["due"])
        self.assertIn("episodic stream changed", res["reasons"])


if __name__ == "__main__":
    import unittest
    unittest.main()
