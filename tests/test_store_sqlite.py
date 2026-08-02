"""SQLite storage backend: round-trip fidelity, sorted_tasks ordering, link /
counter / marker semantics, fresh-install (empty DB, no migration), and the
sqlite3-required guard.

These exercise lib/store.py both directly (SqliteBackend) and through
task-station.py's public primitives, under per-test temp-home isolation."""
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


# ------------------------------------------------------------ sqlite required ----

class SqliteRequiredTest(unittest.TestCase):
    """sqlite3 is a hard, stdlib requirement. With it monkeypatched away (as if the
    guarded import had failed), get_backend raises a clear RuntimeError instead of
    silently degrading to a file store."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store_dir = os.path.join(self.tmp, "store")
        self._real_sqlite3 = store.sqlite3
        store.sqlite3 = None                 # simulate the guarded import failing
        store.reset_cache()

    def tearDown(self):
        store.sqlite3 = self._real_sqlite3
        store.reset_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_sqlite3_raises_runtime_error(self):
        with self.assertRaises(RuntimeError) as cm:
            store.get_backend(self.store_dir)
        self.assertIn("sqlite3", str(cm.exception))


# --------------------------------------------------- legacy-column back-compat ---

class LegacyColumnTest(unittest.TestCase):
    """A DB that still carries the dropped `pinned` column keeps reading + writing
    after the column is gone from the code."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store_dir = os.path.join(self.tmp, "store")
        os.makedirs(self.store_dir)
        store.reset_cache()

    def tearDown(self):
        store.reset_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # backward-compat: a DB that still carries the dropped `pinned` column
    # keeps reading + writing after the column is gone from the code.
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


# ------------------------------------------------------ seq: unique allocation ---

def _mk(tid, created_ts, seq=None):
    t = {"id": tid, "title": tid, "status": "open",
         "created_ts": created_ts, "updated_ts": created_ts, "sessions": [], "log": []}
    if seq is not None:
        t["seq"] = seq
    return t


class SeqAllocation(unittest.TestCase):
    """create_with_seq mints a UNIQUE seq transactionally; the UNIQUE(seq) index
    is the hard backstop and rejects duplicate saves."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store_dir = os.path.join(self.tmp, "store")
        os.makedirs(self.store_dir)
        store.reset_cache()

    def tearDown(self):
        store.reset_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unique_index_present_and_old_index_gone(self):
        b = store.SqliteBackend(self.store_dir)
        conn = b._connect()
        self.assertIsNotNone(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
            (b._SEQ_UNIQUE_INDEX,)).fetchone())
        self.assertIsNone(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_tasks_seq'").fetchone())

    def test_create_with_seq_assigns_increasing_distinct(self):
        b = store.SqliteBackend(self.store_dir)
        seqs = []
        for i in range(5):
            t = _mk("t%d" % i, float(i))
            b.create_with_seq(t)
            seqs.append(t["seq"])
        self.assertEqual(seqs, [1, 2, 3, 4, 5])
        self.assertEqual(len(set(seqs)), 5)

    def test_duplicate_seq_save_is_rejected(self):
        b = store.SqliteBackend(self.store_dir)
        a = _mk("a", 1.0); b.create_with_seq(a)
        with self.assertRaises(store.sqlite3.IntegrityError):
            b.save_task(_mk("b", 2.0, seq=a["seq"]))   # same seq, different id

    def test_two_connection_writers_distinct_seqs(self):
        # Two backends = two connections on the same DB. BEGIN IMMEDIATE serialises
        # them, so alternating creates still yield distinct, contiguous seqs.
        b1 = store.SqliteBackend(self.store_dir)
        b2 = store.SqliteBackend(self.store_dir)
        got = []
        for i in range(6):
            t = _mk("w%d" % i, float(i))
            (b1 if i % 2 == 0 else b2).create_with_seq(t)
            got.append(t["seq"])
        b1.close(); b2.close()
        self.assertEqual(sorted(got), [1, 2, 3, 4, 5, 6])
        self.assertEqual(len(set(got)), 6)

    def test_concurrent_thread_writers_no_duplicate(self):
        import threading
        # Warm the DB (run the migration → unique index) once, so the two worker
        # threads don't race the one-time migration; each then opens its OWN
        # connection in-thread (sqlite objects are thread-affine).
        warm = store.SqliteBackend(self.store_dir); warm.ensure(); warm.close()
        results = {}
        barrier = threading.Barrier(2)

        def worker(key, base):
            bk = store.SqliteBackend(self.store_dir)
            got = []
            barrier.wait()
            for i in range(15):
                t = _mk("%s-%d" % (key, i), base + i)
                bk.create_with_seq(t)
                got.append(t["seq"])
            bk.close()
            results[key] = got

        t1 = threading.Thread(target=worker, args=("A", 0.0))
        t2 = threading.Thread(target=worker, args=("B", 1000.0))
        t1.start(); t2.start(); t1.join(); t2.join()
        allseqs = results["A"] + results["B"]
        self.assertEqual(len(allseqs), 30)
        self.assertEqual(len(set(allseqs)), 30)   # zero duplicate seqs under contention

    def test_integrity_error_retries_then_succeeds(self):
        b = store.SqliteBackend(self.store_dir)
        b.create_with_seq(_mk("t0", 0.0))          # seq 1
        real = b._write_row
        calls = {"n": 0}

        def flaky(conn, task):
            calls["n"] += 1
            if calls["n"] == 1:                    # first attempt "loses" the race
                raise store.sqlite3.IntegrityError("UNIQUE constraint failed: tasks.seq")
            return real(conn, task)

        b._write_row = flaky
        t1 = _mk("t1", 1.0)
        b.create_with_seq(t1)
        b._write_row = real
        self.assertEqual(calls["n"], 2)            # retried exactly once
        self.assertEqual(t1["seq"], 2)
        self.assertEqual({t["seq"] for t in b.all_tasks()}, {1, 2})

    def test_integrity_error_gives_up_after_three(self):
        b = store.SqliteBackend(self.store_dir)
        calls = {"n": 0}

        def always(conn, task):
            calls["n"] += 1
            raise store.sqlite3.IntegrityError("UNIQUE constraint failed: tasks.seq")

        b._write_row = always
        with self.assertRaises(store.sqlite3.IntegrityError):
            b.create_with_seq(_mk("z", 1.0))
        self.assertEqual(calls["n"], 3)            # 3 attempts, then re-raise


class SeqDedupMigration(unittest.TestCase):
    """The additive migration dedupes legacy duplicate seqs (earliest-created keeps
    its seq; the rest get MAX+1 ascending by created_ts) then swaps the non-unique
    index for a UNIQUE one. Deterministic + idempotent."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store_dir = os.path.join(self.tmp, "store")
        os.makedirs(self.store_dir)
        store.reset_cache()

    def tearDown(self):
        store.reset_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed_legacy_dupes(self):
        """A legacy tasks.db with the OLD non-unique idx_tasks_seq and duplicate
        seqs: group seq=2 (b,c,d) and group seq=5 (e,f), plus a unique seq=1 (a)."""
        db = os.path.join(self.store_dir, "tasks.db")
        conn = store.sqlite3.connect(db)
        conn.executescript(
            "CREATE TABLE tasks (id TEXT PRIMARY KEY, seq INTEGER, title TEXT, summary TEXT,"
            " status TEXT, color TEXT, effort TEXT, created_ts REAL, updated_ts REAL,"
            " sessions TEXT, session_meta TEXT, log TEXT, data TEXT NOT NULL);"
            "CREATE INDEX idx_tasks_seq ON tasks(seq);")
        rows = [("a", 1, 10.0), ("b", 2, 20.0), ("c", 2, 30.0),
                ("d", 2, 40.0), ("e", 5, 15.0), ("f", 5, 25.0)]
        for tid, seq, cts in rows:
            data = json.dumps({"id": tid, "seq": seq, "title": tid,
                               "status": "open", "created_ts": cts})
            conn.execute("INSERT INTO tasks (id, seq, created_ts, data) VALUES (?,?,?,?)",
                         (tid, seq, cts, data))
        conn.commit()
        conn.close()

    def test_dedupes_deterministically_and_enforces_unique(self):
        self._seed_legacy_dupes()
        store.reset_cache()
        b = store.SqliteBackend(self.store_dir)
        conn = b._connect()   # triggers the migration
        # index swap
        self.assertIsNotNone(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
            (b._SEQ_UNIQUE_INDEX,)).fetchone())
        self.assertIsNone(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_tasks_seq'").fetchone())
        by = {t["id"]: t["seq"] for t in b.all_tasks()}
        # every seq now unique
        self.assertEqual(len(set(by.values())), len(by))
        # earliest-created in each dup group keeps its seq
        self.assertEqual(by["a"], 1)
        self.assertEqual(by["b"], 2)
        self.assertEqual(by["e"], 5)
        # losers renumbered from MAX(5)+1, groups by seq value, within by created_ts:
        # seq-2 losers c(30)->6, d(40)->7 ; seq-5 loser f(25)->8
        self.assertEqual(by["c"], 6)
        self.assertEqual(by["d"], 7)
        self.assertEqual(by["f"], 8)
        # the reassignment is written to BOTH the seq column and the data blob
        col = {r["id"]: r["seq"] for r in conn.execute("SELECT id, seq FROM tasks")}
        self.assertEqual(col, by)

    def test_migration_idempotent_double_run(self):
        self._seed_legacy_dupes()
        store.reset_cache()
        b1 = store.SqliteBackend(self.store_dir); b1.ensure()
        first = {t["id"]: t["seq"] for t in b1.all_tasks()}
        b1.close()
        store.reset_cache()
        b2 = store.SqliteBackend(self.store_dir); b2.ensure()
        second = {t["id"]: t["seq"] for t in b2.all_tasks()}
        b2.close()
        self.assertEqual(first, second)   # second open is a clean no-op

    def test_new_creates_after_migration_stay_unique(self):
        self._seed_legacy_dupes()
        store.reset_cache()
        b = store.SqliteBackend(self.store_dir)
        b.ensure()
        t = _mk("g", 99.0)
        b.create_with_seq(t)
        self.assertEqual(t["seq"], 9)     # MAX after dedup was 8
        seqs = [x["seq"] for x in b.all_tasks()]
        self.assertEqual(len(seqs), len(set(seqs)))


# ------------------------------------------------------ rev / optimistic lock ---

class RevMigration(unittest.TestCase):
    """The rev column migrates cleanly onto a pre-rev fixture DB."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store_dir = os.path.join(self.tmp, "store")
        os.makedirs(self.store_dir)
        store.reset_cache()

    def tearDown(self):
        store.reset_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rev_added_to_pre_rev_db_defaults_zero(self):
        db = os.path.join(self.store_dir, "tasks.db")
        conn = store.sqlite3.connect(db)
        conn.executescript(
            "CREATE TABLE tasks (id TEXT PRIMARY KEY, seq INTEGER, title TEXT, summary TEXT,"
            " status TEXT, color TEXT, effort TEXT, created_ts REAL, updated_ts REAL,"
            " sessions TEXT, session_meta TEXT, log TEXT, data TEXT NOT NULL);")
        conn.execute("INSERT INTO tasks (id, seq, data) VALUES (?,?,?)",
                     ("old", 1, json.dumps({"id": "old", "seq": 1, "title": "Old",
                                            "status": "open"})))
        conn.commit()
        conn.close()
        store.reset_cache()
        b = store.SqliteBackend(self.store_dir)
        t = b.load_task("old")
        self.assertEqual(t["_rev"], 0)             # migrated existing row → rev 0
        t["title"] = "New"
        b.save_task(t, expected_rev=0)             # versioned save works post-migration
        self.assertEqual(b.load_task("old")["_rev"], 1)
        b.close()


class RevAndMutate(unittest.TestCase):
    """The rev column + save_task(expected_rev) + store.mutate optimistic loop."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store_dir = os.path.join(self.tmp, "store")
        os.makedirs(self.store_dir)
        store.reset_cache()
        self.b = store.SqliteBackend(self.store_dir)

    def tearDown(self):
        self.b.close()
        store.reset_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_new_task_starts_at_rev_zero(self):
        self.b.create_with_seq(_mk("t", 0.0))
        self.assertEqual(self.b.load_task("t")["_rev"], 0)

    def test_unversioned_save_bumps_rev_last_writer_wins(self):
        self.b.create_with_seq(_mk("t", 0.0))
        t = self.b.load_task("t"); t["title"] = "X"
        self.b.save_task(t)                       # unversioned — no expected_rev
        got = self.b.load_task("t")
        self.assertEqual(got["_rev"], 1)
        self.assertEqual(got["title"], "X")

    def test_rev_never_persisted_in_data_blob(self):
        self.b.create_with_seq(_mk("t", 0.0))
        t = self.b.load_task("t")                 # carries _rev
        self.b.save_task(t)                        # save a dict that includes _rev
        conn = self.b._connect()
        raw = conn.execute("SELECT data FROM tasks WHERE id='t'").fetchone()["data"]
        self.assertNotIn("_rev", raw)

    def test_versioned_save_conflict_raises(self):
        self.b.create_with_seq(_mk("t", 0.0))
        t1 = self.b.load_task("t")                # rev 0
        t2 = self.b.load_task("t")                # rev 0 (stale copy)
        t1["title"] = "A"
        self.b.save_task(t1, expected_rev=t1["_rev"])    # ok -> rev 1
        t2["title"] = "B"
        with self.assertRaises(store.RevConflict):
            self.b.save_task(t2, expected_rev=t2["_rev"])  # stale rev 0 -> conflict
        self.assertEqual(self.b.load_task("t")["title"], "A")   # loser did not write

    def test_mutate_missing_returns_none(self):
        self.assertIsNone(self.b.mutate("nope", lambda t: t))

    def test_mutate_persists_and_bumps(self):
        self.b.create_with_seq(_mk("t", 0.0))
        out = self.b.mutate("t", lambda t: t.setdefault("events", []).append({"x": 1}))
        self.assertEqual(out["_rev"], 1)
        self.assertEqual(len(self.b.load_task("t")["events"]), 1)

    def test_mutate_retries_on_conflict_no_lost_update(self):
        # Prove two interleaved appenders BOTH survive: the mutator's first pass
        # races an unversioned writer that commits an event in between, forcing a
        # RevConflict; mutate reloads the fresh state (with the other event) and
        # re-runs, so neither entry is lost.
        self.b.create_with_seq(_mk("t", 0.0))
        real_load = self.b.load_task
        state = {"raced": False}

        def mutator(task):
            if not state["raced"]:
                state["raced"] = True
                other = real_load("t")
                other.setdefault("events", []).append({"who": "other"})
                self.b.save_task(other)          # concurrent writer bumps rev
            task.setdefault("events", []).append({"who": "me"})

        self.b.mutate("t", mutator)
        whos = sorted(e["who"] for e in self.b.load_task("t")["events"])
        self.assertEqual(whos, ["me", "other"])  # zero lost updates

    def test_mutate_gives_up_after_retries(self):
        self.b.create_with_seq(_mk("t", 0.0))

        def always_conflict(task):
            # Bump rev out from under mutate on every pass so it never converges.
            other = store.SqliteBackend(self.store_dir)
            o = other.load_task("t"); o["n"] = o.get("n", 0) + 1
            other.save_task(o); other.close()
            task["touched"] = True

        with self.assertRaises(store.RevConflict):
            self.b.mutate("t", always_conflict, retries=2)


if __name__ == "__main__":
    unittest.main()
