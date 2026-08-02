"""A-4 — export lifecycle fixes.

  * a partial (`--since`/`--status`/`--task`) export MERGES into the full index.md
    derived from the sidecar index — it never rewrites it down to the run's delta;
  * `delete` removes the task's vault note + any recorded generic-export notes and
    their sidecar entries (best-effort), and always emits the task.deleted tombstone;
  * `export --prune --dir <d>` removes exactly the notes whose task no longer exists
    (or was redacted), updates index.md, and is a no-op on a clean dir.

Temp-home isolation, driving the real CLI entrypoints in-process.
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

import store          # noqa: E402
import config         # noqa: E402
import stream         # noqa: E402
import obsidian_sync  # noqa: E402

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


class _Args:
    def __init__(self, **kw):
        d = dict(dir=None, task=None, all=False, status=None, include=None, since=None,
                 prune=False, sync_all=False, flush=False, quiet=False, session=None)
        d.update(kw)
        self.__dict__.update(d)


class _Base(unittest.TestCase):
    def setUp(self):
        os.environ["TASK_STATION_HOME"] = tempfile.mkdtemp(prefix="export-life-")
        self.tmp = os.environ["TASK_STATION_HOME"]
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.PROJECTS_ROOT = os.path.join(self.tmp, "projects")
        ts.DELEGATE_REGISTRY = os.path.join(self.tmp, "workers.json")
        self.out = os.path.join(self.tmp, "brain")
        store.reset_cache()

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self, title, *, updated_ts=None, status="open"):
        t = ts.new_task(title, "summary of " + title)
        if status != "open":
            t["status"] = status
        if updated_ts is not None:
            t["updated_ts"] = updated_ts
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _export(self, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_export(_Args(dir=self.out, **kw))
        return buf.getvalue()

    def _index(self):
        with open(os.path.join(self.out, "index.md"), encoding="utf-8") as f:
            return f.read()

    def _notes(self):
        return sorted(f for f in os.listdir(self.out)
                      if f.endswith(".md") and f != "index.md")


class PartialExportMergesIndex(_Base):
    def test_since_run_does_not_shrink_index(self):
        a = self._seed("Old task", updated_ts=1_600_000_000.0)   # 2020
        self._export(all=True, include="usage")
        self.assertIn("%s-old-task" % a["seq"], self._index())
        # a later --since run touches only the NEW task; index.md must still list BOTH
        b = self._seed("New task", updated_ts=1_800_000_000.0)   # 2027
        self._export(since="2026-01-01", include="usage")
        idx = self._index()
        self.assertIn("%s-old-task" % a["seq"], idx)   # previously exported — retained
        self.assertIn("%s-new-task" % b["seq"], idx)   # this run's delta — merged in
        self.assertIn("count: 2", idx)
        self.assertEqual(len(self._notes()), 2)

    def test_single_task_run_keeps_others(self):
        a = self._seed("First")
        b = self._seed("Second")
        self._export(all=True, include="usage")
        # re-export ONLY the second; the first's index line survives
        self._export(task=str(b["seq"]), include="usage")
        idx = self._index()
        self.assertIn("%s-first" % a["seq"], idx)
        self.assertIn("%s-second" % b["seq"], idx)


class DeletePurgesNotes(_Base):
    def test_delete_removes_vault_and_export_notes_and_tombstones(self):
        vault = os.path.join(self.tmp, "vault")
        os.makedirs(vault)
        config.set("obsidian_vault", vault)
        try:
            t = self._seed("Doomed task")
            # mirror to the vault + a recorded generic export
            buf = io.StringIO()
            with redirect_stdout(buf):
                ts.cmd_obsidian(_Args(sync_all=True))
            self._export(all=True, include="usage")
            pdir = obsidian_sync.plugin_dir(vault)
            stem = "%s-doomed-task" % t["seq"]
            self.assertTrue(os.path.exists(os.path.join(pdir, stem + ".md")))
            self.assertTrue(os.path.exists(os.path.join(self.out, stem + ".md")))

            buf = io.StringIO()
            with redirect_stdout(buf):
                ts.cmd_delete(_Args(task=str(t["seq"])))
            self.assertIn("Deleted task", buf.getvalue())
            # vault note + its sidecar entry gone
            self.assertFalse(os.path.exists(os.path.join(pdir, stem + ".md")))
            self.assertNotIn(t["id"], obsidian_sync._load_index(pdir))
            # generic-export note + entry gone, index.md refreshed
            self.assertFalse(os.path.exists(os.path.join(self.out, stem + ".md")))
            self.assertNotIn(t["id"], obsidian_sync._load_index(self.out))
            self.assertNotIn(stem, self._index())
            # tombstone always emitted
            deleted = [e for e in stream.read_events()
                       if e.get("event") == "task.deleted"
                       and (e.get("task") or {}).get("uuid") == t["uuid"]]
            self.assertEqual(len(deleted), 1)
        finally:
            config.unset("obsidian_vault")

    def test_delete_survives_missing_vault(self):
        # a configured-but-gone vault must not abort the delete / tombstone
        config.set("obsidian_vault", os.path.join(self.tmp, "gone-vault"))
        try:
            t = self._seed("Still deletable")
            buf = io.StringIO()
            with redirect_stdout(buf):
                ts.cmd_delete(_Args(task=str(t["seq"])))
            self.assertIn("Deleted task", buf.getvalue())
            self.assertIsNone(ts.load_task(t["id"]))
        finally:
            config.unset("obsidian_vault")


class PruneReconciles(_Base):
    def test_prune_removes_only_orphans(self):
        a = self._seed("Alpha")
        b = self._seed("Beta")
        c = self._seed("Gamma")
        self._export(all=True, include="usage")
        self.assertEqual(len(self._notes()), 3)
        # orphan C's note by removing the task row directly (no note-purge path)
        ts.delete_task(c["id"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_export(_Args(dir=self.out, prune=True))
        self.assertIn("Pruned 1 orphan note", buf.getvalue())
        notes = self._notes()
        self.assertIn("%s-alpha.md" % a["seq"], notes)
        self.assertIn("%s-beta.md" % b["seq"], notes)
        self.assertNotIn("%s-gamma.md" % c["seq"], notes)
        idx = self._index()
        self.assertNotIn("%s-gamma" % c["seq"], idx)
        self.assertIn("count: 2", idx)

    def test_prune_removes_redacted(self):
        a = self._seed("Keeper")
        b = self._seed("Secret")
        self._export(all=True, include="usage")
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_redact(_Args(task=str(b["seq"])))
        self.assertTrue(ts.load_task(b["id"]).get("redacted"))
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_export(_Args(dir=self.out, prune=True))
        self.assertIn("Pruned 1 orphan note", buf.getvalue())
        self.assertNotIn("%s-secret.md" % b["seq"], self._notes())
        self.assertIn("%s-keeper.md" % a["seq"], self._notes())

    def test_prune_noop_on_clean_dir(self):
        self._seed("Alpha")
        self._export(all=True, include="usage")
        before = self._index()
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_export(_Args(dir=self.out, prune=True))
        self.assertIn("already in sync", buf.getvalue())
        self.assertEqual(before, self._index())   # untouched


if __name__ == "__main__":
    unittest.main()
