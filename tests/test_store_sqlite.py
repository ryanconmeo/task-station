"""SQLite storage backend: round-trip fidelity, sorted_tasks ordering, link /
counter / marker semantics, fresh-install (empty DB, no migration), and the
sqlite3-unavailable JSON fallback.

These exercise lib/store.py both directly (SqliteBackend / JsonBackend) and
through task-station.py's public primitives, under per-test temp-home isolation."""
import importlib.util
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

    def test_not_closed_before_closed_then_recent_first(self):
        # open + active share the "not-closed" group, ordered by recent activity;
        # closed comes after (also recent-first).
        self._mk("old-open", "open", 100.0)
        self._mk("mid-active", "active", 200.0)
        self._mk("new-open", "open", 300.0)
        self._mk("newest-closed", "closed", 400.0)
        self._mk("old-closed", "closed", 50.0)
        order = [t["title"] for t in ts.sorted_tasks()]
        self.assertEqual(
            order,
            ["new-open", "mid-active", "old-open", "newest-closed", "old-closed"])

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


# -------------------------------------------------------------- fresh store ---

class FreshStoreTest(unittest.TestCase):
    """A fresh install (no tasks.db) gets a new empty SQLite store. There is no
    migration of any prior data, ever."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store_dir = os.path.join(self.tmp, "store")
        os.makedirs(self.store_dir)
        store.reset_cache()

    def tearDown(self):
        store.reset_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fresh_store_selects_sqlite_and_starts_empty(self):
        db_path = os.path.join(self.store_dir, "tasks.db")
        b = store.get_backend(self.store_dir)
        self.assertIsInstance(b, store.SqliteBackend)
        self.assertFalse(os.path.exists(db_path))   # selecting alone creates nothing
        # First access materialises a fresh, EMPTY DB — no migration of prior data.
        self.assertEqual(b.all_tasks(), [])
        self.assertTrue(os.path.exists(db_path))
        # No legacy JSON store is ever read or created.
        self.assertFalse(os.path.isdir(os.path.join(self.store_dir, "tasks")))

    def test_existing_db_is_reused(self):
        b1 = store.get_backend(self.store_dir)
        b1.save_task({"id": "keep", "title": "keep", "status": "open",
                      "created_ts": 1.0, "updated_ts": 1.0, "sessions": [], "log": []})
        store.reset_cache()
        b2 = store.get_backend(self.store_dir)
        self.assertIsInstance(b2, store.SqliteBackend)
        self.assertEqual({t["id"] for t in b2.all_tasks()}, {"keep"})


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


# -------------------------------------------------------- JSON→SQLite migrate ---

class MigrationTest(unittest.TestCase):
    """One-time, idempotent, non-destructive import of a legacy file-per-task JSON
    store into a fresh SQLite DB on first init."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store_dir = os.path.join(self.tmp, "store")
        os.makedirs(self.store_dir)
        store.reset_cache()

    def tearDown(self):
        store.reset_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed_legacy(self):
        """Write a populated legacy JSON store directly via JsonBackend."""
        j = store.JsonBackend(self.store_dir)
        j.save_task({"id": "t1", "seq": 1, "title": "Legacy one", "status": "open",
                     "created_ts": 1.0, "updated_ts": 1.0, "sessions": ["s1"],
                     "log": [{"note": "hi"}], "pinned_session": "s1"})
        j.save_task({"id": "t2", "seq": 2, "title": "Legacy two", "status": "closed",
                     "created_ts": 2.0, "updated_ts": 2.0, "sessions": [], "log": []})
        j.set_link("s1", "t1")
        j.bump_count("s1"); j.bump_count("s1")     # n = 2
        j.mark_edited("s1")                          # edited = 1
        j.bump_blocked("s1")                         # blocked = 1
        return j

    # (a) populated legacy store → migrated; tasks + links round-trip; pin survives.
    def test_legacy_json_migrated_on_first_init(self):
        self._seed_legacy()
        b = store.SqliteBackend(self.store_dir)
        tasks = {t["id"]: t for t in b.all_tasks()}
        self.assertEqual(set(tasks), {"t1", "t2"})
        self.assertEqual(tasks["t1"]["title"], "Legacy one")
        self.assertEqual(tasks["t1"]["status"], "open")
        self.assertEqual(tasks["t2"]["status"], "closed")
        # pinned_session (the real pin feature) rides in the data blob and survives.
        self.assertEqual(tasks["t1"]["pinned_session"], "s1")
        # links + miss counter + edit/blocked markers all round-trip.
        self.assertEqual(b.get_link("s1"), "t1")
        self.assertEqual(b.get_count("s1"), 2)
        self.assertTrue(b.has_edited("s1"))
        self.assertEqual(b.get_blocked("s1"), 1)
        # NON-DESTRUCTIVE: the legacy JSON files are left in place.
        self.assertTrue(os.path.exists(os.path.join(self.store_dir, "tasks", "t1.json")))
        self.assertTrue(os.path.exists(os.path.join(self.store_dir, "links", "s1")))

    # (b) idempotent — re-init adds no duplicates, never re-imports.
    def test_migration_idempotent(self):
        self._seed_legacy()
        b1 = store.SqliteBackend(self.store_dir)
        self.assertEqual(len(b1.all_tasks()), 2)
        store.reset_cache()
        b2 = store.SqliteBackend(self.store_dir)
        self.assertEqual(len(b2.all_tasks()), 2)        # no duplicates on 2nd init
        # A native add then re-init keeps 3 — the 2 legacy rows are never re-imported.
        b2.save_task({"id": "t3", "title": "native", "status": "open",
                      "created_ts": 3.0, "updated_ts": 3.0, "sessions": [], "log": []})
        store.reset_cache()
        b3 = store.SqliteBackend(self.store_dir)
        self.assertEqual(len(b3.all_tasks()), 3)

    # (c) no legacy store → clean no-op (no crash, no tasks/ dir created).
    def test_no_legacy_store_is_clean_noop(self):
        b = store.SqliteBackend(self.store_dir)
        self.assertEqual(b.all_tasks(), [])
        self.assertFalse(os.path.isdir(os.path.join(self.store_dir, "tasks")))

    # (d) backward-compat: a DB that still carries the dropped `pinned` column
    #     keeps reading + writing after the column is gone from the code.
    def test_legacy_pinned_column_still_reads_and_writes(self):
        db = os.path.join(self.store_dir, "tasks.db")
        conn = store.sqlite3.connect(db)
        conn.executescript(
            "CREATE TABLE tasks (id TEXT PRIMARY KEY, seq INTEGER, title TEXT, "
            "summary TEXT, status TEXT, color TEXT, effort TEXT, created_ts REAL, "
            "updated_ts REAL, pinned INTEGER, sessions TEXT, session_meta TEXT, "
            "log TEXT, data TEXT NOT NULL);"
            "CREATE TABLE links (session TEXT PRIMARY KEY, task_id TEXT, "
            "n INTEGER NOT NULL DEFAULT 0, edited INTEGER NOT NULL DEFAULT 0, "
            "blocked INTEGER NOT NULL DEFAULT 0);")
        conn.execute(
            "INSERT INTO tasks (id, seq, title, status, pinned, data) VALUES (?,?,?,?,?,?)",
            ("old", 1, "Old row", "open", 1,
             json.dumps({"id": "old", "title": "Old row", "status": "open"})))
        conn.commit(); conn.close()
        store.reset_cache()
        b = store.SqliteBackend(self.store_dir)
        # reads the legacy row (which has a `pinned` column the code no longer names)
        self.assertIn("old", {t["id"] for t in b.all_tasks()})
        # writes a brand-new row though save_task no longer lists `pinned`
        b.save_task({"id": "new", "title": "New row", "status": "open",
                     "created_ts": 1.0, "updated_ts": 1.0, "sessions": [], "log": []})
        self.assertEqual(b.load_task("new")["title"], "New row")
        # upsert path also works on the legacy row
        old = b.load_task("old"); old["title"] = "Updated"; b.save_task(old)
        self.assertEqual(b.load_task("old")["title"], "Updated")


if __name__ == "__main__":
    unittest.main()
