"""Sandbox/permission safety net for the Obsidian export (task-station.py side).

The vault often lives under a macOS-protected root (~/Documents, iCloud Mobile
Documents). Inside a sandboxed Claude Code session the atomic write is DENIED
(os.replace → EPERM), so the export hook must, mirroring the digest_dirty design:

  * track the failed task as pending-resync (obsidian_dirty) and persist it,
  * clear that flag on a LATER successful export,
  * NEVER mark dirty when export is simply OFF (no vault),
  * surface ONE actionable, deduped permission hint (not per-mutation spam),
  * and let `obsidian --flush` re-export ONLY the dirty tasks + clear their flags.

These exercise that engine-level behaviour under per-test temp-home isolation
(the same repoint dance the store suites use), loading the hyphenated CLI module
by path.
"""
import argparse
import errno
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

import config          # noqa: E402
import obsidian_sync   # noqa: E402
import store           # noqa: E402

# task-station.py has a hyphen, so it can't be a normal import — load it by path.
_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


def _repoint(tmp):
    """Point task-station.py's import-frozen path globals at a fresh tmp store."""
    os.environ["TASK_STATION_HOME"] = tmp
    ts.DATA = tmp
    ts.STORE = os.path.join(tmp, "store")
    ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
    ts.LINKS_DIR = os.path.join(ts.STORE, "links")
    store.reset_cache()


def _ns(**kw):
    return argparse.Namespace(**kw)


def _raise(exc):
    """A callable that raises `exc` — a stand-in for a sandbox-denied export."""
    def _f(*a, **k):
        raise exc
    return _f


class ObsidianDirty(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="obs-dirty-")
        _repoint(self.tmp)
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)
        config.set("obsidian_vault", self.vault)

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mk(self, title="A task"):
        t = ts.new_task(title, "the summary")
        ts.save_task(t)
        ts.ensure_seqs()                 # every note filename needs a stable seq
        return ts.load_task(t["id"])

    # --- dirty set on EPERM, cleared on a later success --------------------
    def test_dirty_set_on_eperm_and_cleared_on_success(self):
        t = self._mk()
        boom = OSError(errno.EPERM, "Operation not permitted")
        orig = obsidian_sync.export_task
        obsidian_sync.export_task = _raise(boom)
        try:
            with redirect_stderr(io.StringIO()):
                ts._obsidian_sync(t)
        finally:
            obsidian_sync.export_task = orig
        self.assertTrue(ts.obsidian_dirty(ts.load_task(t["id"])))
        # a later real (successful) export clears the flag
        with redirect_stderr(io.StringIO()):
            ts._obsidian_sync(ts.load_task(t["id"]))
        self.assertFalse(ts.obsidian_dirty(ts.load_task(t["id"])))

    def test_export_off_never_marks_dirty(self):
        config.set("obsidian_vault", None)   # OFF
        t = self._mk()
        ts._obsidian_sync(t)
        self.assertFalse(ts.obsidian_dirty(ts.load_task(t["id"])))

    def test_missing_field_reads_not_dirty(self):
        t = self._mk()
        self.assertNotIn("obsidian_dirty", t)
        self.assertFalse(ts.obsidian_dirty(t))

    # --- --flush re-exports ONLY the dirty tasks and clears them -----------
    def test_flush_reexports_only_dirty_tasks(self):
        self._mk("clean one")                    # never exported, never dirty
        dirty = self._mk("dirty one")
        ts.mark_obsidian_dirty(dirty); ts.save_task(dirty)
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(io.StringIO()):
            ts.cmd_obsidian(_ns(flush=True))
        out = buf.getvalue()
        self.assertIn("Flushed 1", out)
        # the dirty task is now clean, and only IT got a note on disk (the clean
        # task was never dirty, so --flush must leave it untouched)
        self.assertFalse(ts.obsidian_dirty(ts.load_task(dirty["id"])))
        pdir = obsidian_sync.plugin_dir(self.vault)
        notes = [f for f in os.listdir(pdir) if f.endswith(".md")]
        self.assertEqual(len(notes), 1)
        self.assertIn("dirty-one", notes[0])

    def test_flush_with_nothing_dirty(self):
        self._mk()
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(io.StringIO()):
            ts.cmd_obsidian(_ns(flush=True))
        self.assertIn("Nothing to flush", buf.getvalue())

    def test_flush_reports_still_failing(self):
        d1 = self._mk("one"); ts.mark_obsidian_dirty(d1); ts.save_task(d1)
        orig = obsidian_sync.export_task
        obsidian_sync.export_task = _raise(OSError(errno.EPERM, "nope"))
        try:
            buf = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(io.StringIO()):
                ts.cmd_obsidian(_ns(flush=True))
        finally:
            obsidian_sync.export_task = orig
        out = buf.getvalue()
        self.assertIn("still failing", out)
        # still dirty — a failed flush must not clear the flag
        self.assertTrue(ts.obsidian_dirty(ts.load_task(d1["id"])))

    # --- --status reports the pending-resync count ------------------------
    def test_status_shows_dirty_count(self):
        t = self._mk()
        ts.mark_obsidian_dirty(t); ts.save_task(t)
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_obsidian(_ns(status=True))
        out = buf.getvalue()
        self.assertIn("Obsidian export: ON", out)
        self.assertRegex(out, r"[Pp]ending resync: 1")

    def test_status_no_dirty_line_when_clean(self):
        self._mk()
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_obsidian(_ns(status=True))
        self.assertNotIn("Pending resync", buf.getvalue())

    # --- --sync-all clears the dirty flag on every exported task ----------
    def test_sync_all_clears_dirty(self):
        t = self._mk()
        ts.mark_obsidian_dirty(t); ts.save_task(t)
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(io.StringIO()):
            ts.cmd_obsidian(_ns(sync_all=True))
        self.assertFalse(ts.obsidian_dirty(ts.load_task(t["id"])))

    # --- hook auto-flush (Fix B): heals a sandbox-denied export unsandboxed --
    def test_hook_flush_heals_dirty_task_silently(self):
        # Simulate the mid-turn (sandboxed) hot-path failure: dirty, silent.
        t = self._mk()
        orig = obsidian_sync.export_task
        obsidian_sync.export_task = _raise(OSError(errno.EPERM, "denied"))
        try:
            with redirect_stderr(io.StringIO()):
                ts._obsidian_sync(t)
        finally:
            obsidian_sync.export_task = orig
        self.assertTrue(ts.obsidian_dirty(ts.load_task(t["id"])))
        # The Stop/SessionStart hook runs the SAME code path UNSANDBOXED — the export
        # now succeeds — via `obsidian --flush --quiet`, healing the task with zero noise.
        buf, err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            ts.cmd_obsidian(_ns(flush=True, quiet=True))
        self.assertFalse(ts.obsidian_dirty(ts.load_task(t["id"])))
        self.assertEqual(buf.getvalue(), "")   # quiet on success — hooks must stay silent
        self.assertEqual(err.getvalue(), "")

    def test_quiet_flush_noop_when_clean(self):
        self._mk()
        buf, err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            ts.cmd_obsidian(_ns(flush=True, quiet=True))
        self.assertEqual(buf.getvalue(), "")
        self.assertEqual(err.getvalue(), "")

    def test_quiet_flush_noop_when_export_off(self):
        config.set("obsidian_vault", None)
        t = self._mk()
        ts.mark_obsidian_dirty(t); ts.save_task(t)   # a stale flag from when it was on
        buf, err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            ts.cmd_obsidian(_ns(flush=True, quiet=True))
        self.assertEqual(buf.getvalue(), "")   # off ⇒ silent no-op, never spawns work

    # --- hint reconciliation (Fix B): silent first, loud only if persistent ---
    def test_hotpath_failure_is_silent_first_time(self):
        # A first sandboxed hot-path denial marks dirty but must NOT print the hint —
        # the end-of-turn hook flush will heal it seconds later, so nagging is noise.
        t = self._mk()
        orig = obsidian_sync.export_task
        obsidian_sync.export_task = _raise(OSError(errno.EPERM, "Operation not permitted"))
        try:
            err = io.StringIO()
            with redirect_stderr(err):
                ts._obsidian_sync(t)
        finally:
            obsidian_sync.export_task = orig
        self.assertTrue(ts.obsidian_dirty(ts.load_task(t["id"])))
        self.assertEqual(err.getvalue(), "")   # fully silent — Fix B heals it; no noise

    def test_hint_fires_only_after_persistent_flush_failure(self):
        t = self._mk()
        boom = OSError(errno.EPERM, "Operation not permitted")
        orig = obsidian_sync.export_task
        obsidian_sync.export_task = _raise(boom)
        try:
            # 1) hot-path failure → silent, dirty
            with redirect_stderr(io.StringIO()):
                ts._obsidian_sync(ts.load_task(t["id"]))
            # 2) a hook flush that ALSO can't write → arms the persistent signal, silent
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                ts.cmd_obsidian(_ns(flush=True, quiet=True))
            self.assertTrue(ts.load_task(t["id"]).get("obsidian_flush_failed"))
            # 3) NOW a hot-path failure surfaces the loud hint (once)...
            err_a = io.StringIO()
            with redirect_stderr(err_a):
                ts._obsidian_sync(ts.load_task(t["id"]))
            # 4) ...and a further failure is deduped
            err_b = io.StringIO()
            with redirect_stderr(err_b):
                ts._obsidian_sync(ts.load_task(t["id"]))
        finally:
            obsidian_sync.export_task = orig
        self.assertIn("Full Disk", err_a.getvalue())
        self.assertNotIn("Full Disk", err_b.getvalue())
        # a later SUCCESS clears both flags and re-arms the one-shot hint
        with redirect_stderr(io.StringIO()):
            ts._obsidian_sync(ts.load_task(t["id"]))
        healed = ts.load_task(t["id"])
        self.assertFalse(ts.obsidian_dirty(healed))
        self.assertFalse(healed.get("obsidian_flush_failed"))


if __name__ == "__main__":
    unittest.main()
