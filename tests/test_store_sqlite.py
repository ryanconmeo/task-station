"""SQLite storage backend: round-trip fidelity, sorted_tasks ordering, link /
counter / marker semantics, JSON->SQLite migration (backup + stale-link GC +
idempotency), and the sqlite3-unavailable JSON fallback.

These exercise lib/store.py both directly (SqliteBackend / JsonBackend) and
through task-station.py's public primitives, under per-test temp-home isolation."""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

import store  # noqa: E402  (normal import — store.py has no hyphen)

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


# --------------------------------------------------------------- round-trip ---

class SqliteRoundTripTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _repoint(self.tmp)

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_sqlite_is_the_active_backend(self):
        self.assertIsInstance(ts._backend(), store.SqliteBackend)
        t = ts.new_task("Hello", "world")
        ts.save_task(t)
        self.assertTrue(os.path.exists(os.path.join(ts.STORE, "tasks.db")))
        # No per-task JSON file is written under the SQLite backend.
        self.assertFalse(os.path.isdir(os.path.join(ts.STORE, "tasks")))

    def test_round_trip_preserves_nested_and_unknown_fields(self):
        t = ts.new_task("Build thing", "the summary")
        t["seq"] = 7
        t["color"] = "blue"
        t["effort"] = "M"
        t["pinned"] = True
        t["sessions"] = ["s1", "s2"]
        t["session_meta"] = {"s1": {"cwd": "/x", "role": "hub"}}
        t["log"] = [{"event": "created", "ts": 1.0}, {"event": "noted"}]
        t["a_future_field"] = {"nested": [1, 2, 3]}  # data column must not drop it
        ts.save_task(t)

        got = ts.load_task(t["id"])
        self.assertEqual(got["title"], "Build thing")
        self.assertEqual(got["seq"], 7)
        self.assertEqual(got["sessions"], ["s1", "s2"])
        self.assertEqual(got["session_meta"], {"s1": {"cwd": "/x", "role": "hub"}})
        self.assertEqual(got["log"][0]["event"], "created")
        self.assertTrue(got["pinned"])
        self.assertEqual(got["a_future_field"], {"nested": [1, 2, 3]})

    def test_save_updates_in_place_no_duplicate(self):
        t = ts.new_task("Once", "s")
        ts.save_task(t)
        t["title"] = "Twice"
        ts.save_task(t)
        all_t = ts.all_tasks()
        self.assertEqual(len(all_t), 1)
        self.assertEqual(all_t[0]["title"], "Twice")

    def test_load_missing_returns_none(self):
        self.assertIsNone(ts.load_task("does-not-exist"))


# --------------------------------------------------------------- ordering -----

class SortedTasksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _repoint(self.tmp)

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mk(self, title, status, updated_ts):
        t = ts.new_task(title, "s")
        t["status"] = status
        t["updated_ts"] = updated_ts
        ts.save_task(t)
        return t

    def test_open_before_closed_then_recent_first(self):
        self._mk("old-open", "open", 100.0)
        self._mk("new-open", "open", 300.0)
        self._mk("newest-closed", "closed", 400.0)
        self._mk("old-closed", "closed", 50.0)
        order = [t["title"] for t in ts.sorted_tasks()]
        self.assertEqual(order, ["new-open", "old-open", "newest-closed", "old-closed"])

    def test_max_seq(self):
        a = self._mk("a", "open", 1.0); a["seq"] = 3; ts.save_task(a)
        b = self._mk("b", "open", 2.0); b["seq"] = 9; ts.save_task(b)
        self.assertEqual(ts._max_seq(), 9)


# ----------------------------------------------------------------- links ------

class LinksAndCountersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _repoint(self.tmp)

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_set_get_clear(self):
        self.assertIsNone(ts.get_link("s1"))
        ts.set_link("s1", "task-A")
        self.assertEqual(ts.get_link("s1"), "task-A")
        ts.set_link("s1", "task-B")          # re-attach
        self.assertEqual(ts.get_link("s1"), "task-B")
        ts.clear_link("s1")
        self.assertIsNone(ts.get_link("s1"))

    def test_clear_link_preserves_miss_counter(self):
        ts.set_link("s1", "task-A")
        ts.bump_count("s1")
        ts.bump_count("s1")
        ts.clear_link("s1")
        self.assertIsNone(ts.get_link("s1"))
        self.assertEqual(ts.get_count("s1"), 2)   # counter survives clearing the link

    def test_counter_bump_and_clear(self):
        self.assertEqual(ts.get_count("s2"), 0)
        self.assertEqual(ts.bump_count("s2"), 1)
        self.assertEqual(ts.bump_count("s2"), 2)
        ts.clear_count("s2")
        self.assertEqual(ts.get_count("s2"), 0)

    def test_edit_and_blocked_markers(self):
        self.assertFalse(ts.has_edited("s3"))
        self.assertTrue(ts.mark_edited("s3"))     # first call -> True
        self.assertFalse(ts.mark_edited("s3"))    # one-shot
        self.assertTrue(ts.has_edited("s3"))
        self.assertEqual(ts.bump_blocked("s3"), 1)
        self.assertEqual(ts.bump_blocked("s3"), 2)
        self.assertEqual(ts.get_blocked("s3"), 2)
        ts.clear_edit_markers("s3")
        self.assertFalse(ts.has_edited("s3"))
        self.assertEqual(ts.get_blocked("s3"), 0)

    def test_live_session_count_live_vs_stale(self):
        a = ts.new_task("A", "s"); ts.save_task(a)
        b = ts.new_task("B", "s"); ts.save_task(b)
        a["sessions"] = ["s1", "s2"]; ts.save_task(a)
        ts.set_link("s1", a["id"])
        ts.set_link("s2", a["id"])
        self.assertEqual(ts.live_session_count(ts.load_task(a["id"])), 2)
        ts.set_link("s2", b["id"])                # s2 re-attaches elsewhere
        self.assertEqual(ts.live_session_count(ts.load_task(a["id"])), 1)
        ts.clear_link("s1")                       # s1 detaches
        reloaded = ts.load_task(a["id"])
        self.assertEqual(ts.live_session_count(reloaded), 0)
        self.assertEqual(len(reloaded["sessions"]), 2)   # append-only, unchanged


# --------------------------------------------------------------- migration ----

class MigrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store_dir = os.path.join(self.tmp, "store")
        self.tasks_dir = os.path.join(self.store_dir, "tasks")
        self.links_dir = os.path.join(self.store_dir, "links")
        os.makedirs(self.tasks_dir)
        os.makedirs(self.links_dir)
        os.environ.pop(store.MIGRATE_OPT_IN_ENV, None)

    def tearDown(self):
        os.environ.pop(store.MIGRATE_OPT_IN_ENV, None)
        store.reset_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_task(self, tid, **kw):
        t = {"id": tid, "title": tid, "summary": "", "status": "open",
             "created_ts": 10.0, "updated_ts": 20.0, "sessions": [], "log": []}
        t.update(kw)
        with open(os.path.join(self.tasks_dir, tid + ".json"), "w") as f:
            json.dump(t, f)
        return t

    def _write_file(self, name, content):
        with open(os.path.join(self.links_dir, name), "w") as f:
            f.write(content)

    def test_explicit_migrate_moves_json_into_backup(self):
        self._write_task("alpha", updated_ts=20.0)
        self._write_task("beta", status="closed", updated_ts=99.0)
        b = store.SqliteBackend(self.store_dir)
        res = b.migrate()

        self.assertEqual(res["tasks"], 2)
        # Stamp derives from the newest updated_ts (99), not wall-clock.
        self.assertTrue(res["backup"].endswith("json-backup-99"))
        # ATOMIC SWAP: the JSON dirs are MOVED into the backup, gone from store/.
        self.assertFalse(os.path.exists(self.tasks_dir))
        self.assertFalse(os.path.exists(self.links_dir))
        self.assertTrue(os.path.exists(os.path.join(res["backup"], "tasks", "alpha.json")))
        self.assertTrue(os.path.isdir(os.path.join(res["backup"], "links")))
        # DB is now the unambiguous sole store.
        self.assertEqual(b.load_task("alpha")["title"], "alpha")
        self.assertEqual(b.load_task("beta")["status"], "closed")
        self.assertEqual(len(b.all_tasks()), 2)

    def test_migrate_stale_link_gc_keeps_valid_and_skip(self):
        self._write_task("alpha")
        self._write_file("sess-live", "alpha")          # valid -> kept
        self._write_file("sess-stale", "ghost-task")    # target gone -> GC'd
        self._write_file("sess-skip", ts.SKIP_SENTINEL)  # skip marker -> kept
        self._write_file("sess-live.n", "3")            # counter on the live link
        b = store.SqliteBackend(self.store_dir)
        res = b.migrate()

        self.assertEqual(res["links_stale"], 1)
        self.assertEqual(b.get_link("sess-live"), "alpha")
        self.assertEqual(b.get_count("sess-live"), 3)
        self.assertIsNone(b.get_link("sess-stale"))      # pointer GC'd
        self.assertEqual(b.get_link("sess-skip"), ts.SKIP_SENTINEL)

    def test_migrate_is_idempotent_after_swap(self):
        self._write_task("alpha")
        self._write_task("beta")
        self._write_file("sess-live", "alpha")
        b = store.SqliteBackend(self.store_dir)
        res1 = b.migrate()
        self.assertEqual(res1["tasks"], 2)
        self.assertFalse(os.path.exists(self.tasks_dir))   # JSON moved away
        res2 = b.migrate()                                 # re-run: nothing left
        self.assertEqual(res2["tasks"], 0)
        self.assertIsNone(res2["backup"])
        self.assertEqual(len(b.all_tasks()), 2)            # no duplicates
        self.assertEqual(b.get_link("sess-live"), "alpha")

    def test_no_auto_migration_without_opt_in(self):
        # The opt-in flag is unset (popped in setUp) — a bare invocation must NOT
        # migrate as a side effect, must leave the JSON untouched, and must warn.
        self._write_task("alpha")
        self._write_file("sess-live", "alpha")
        err = io.StringIO()
        b = store.SqliteBackend(self.store_dir)
        with contextlib.redirect_stderr(err):
            tasks = b.all_tasks()
        self.assertEqual(tasks, [])                                       # not migrated
        self.assertTrue(os.path.exists(os.path.join(self.tasks_dir, "alpha.json")))
        self.assertIn("WARNING", err.getvalue())                         # divergence guard fires

    def test_auto_migration_with_opt_in(self):
        os.environ[store.MIGRATE_OPT_IN_ENV] = "1"
        self._write_task("alpha")
        self._write_file("sess-live", "alpha")
        # First DB access auto-migrates because the opt-in flag is set.
        b = store.SqliteBackend(self.store_dir)
        tasks = b.all_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(b.get_link("sess-live"), "alpha")
        self.assertFalse(os.path.exists(self.tasks_dir))   # JSON swapped away

    def test_divergence_warning_when_db_and_json_coexist(self):
        # Clean migration first: DB holds 'alpha', JSON swapped away.
        self._write_task("alpha")
        store.SqliteBackend(self.store_dir).migrate()
        # A newer/leftover JSON store reappears beside the DB.
        os.makedirs(self.tasks_dir, exist_ok=True)
        self._write_task("beta")
        err = io.StringIO()
        b = store.SqliteBackend(self.store_dir)
        with contextlib.redirect_stderr(err):
            ids = {t["id"] for t in b.all_tasks()}
        self.assertIn("WARNING", err.getvalue())
        self.assertIn(self.tasks_dir, err.getvalue())      # names the ignored store
        self.assertEqual(ids, {"alpha"})                   # DB used; JSON 'beta' ignored

    def test_migrate_empty_store_is_noop(self):
        b = store.SqliteBackend(self.store_dir)
        res = b.migrate()
        self.assertEqual(res["tasks"], 0)
        self.assertIsNone(res["backup"])


# ---------------------------------------------------------------- fallback ----

class JsonFallbackTest(unittest.TestCase):
    """With sqlite3 monkeypatched away, the primitives fall back to the JSON
    file store and still behave identically."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._real_sqlite3 = store.sqlite3
        store.sqlite3 = None                 # simulate the guarded import failing
        _repoint(self.tmp)

    def tearDown(self):
        store.sqlite3 = self._real_sqlite3
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_backend_is_json(self):
        self.assertIsInstance(ts._backend(), store.JsonBackend)

    def test_round_trip_uses_json_files(self):
        t = ts.new_task("Filed", "s")
        ts.save_task(t)
        # A per-task JSON file is written; no SQLite DB exists.
        self.assertTrue(os.path.exists(os.path.join(ts.STORE, "tasks", t["id"] + ".json")))
        self.assertFalse(os.path.exists(os.path.join(ts.STORE, "tasks.db")))
        self.assertEqual(ts.load_task(t["id"])["title"], "Filed")

    def test_links_and_live_count_under_json(self):
        a = ts.new_task("A", "s"); ts.save_task(a)
        a["sessions"] = ["s1", "s2"]; ts.save_task(a)
        ts.set_link("s1", a["id"])
        self.assertEqual(ts.get_link("s1"), a["id"])
        self.assertEqual(ts.live_session_count(ts.load_task(a["id"])), 1)
        ts.clear_link("s1")
        self.assertIsNone(ts.get_link("s1"))

    def test_counters_and_markers_under_json(self):
        self.assertEqual(ts.bump_count("s9"), 1)
        self.assertEqual(ts.bump_count("s9"), 2)
        self.assertTrue(ts.mark_edited("s9"))
        self.assertFalse(ts.mark_edited("s9"))
        self.assertEqual(ts.bump_blocked("s9"), 1)
        ts.clear_edit_markers("s9")
        self.assertFalse(ts.has_edited("s9"))


if __name__ == "__main__":
    unittest.main()
