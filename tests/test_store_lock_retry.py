"""SQLite write-lock contention retry in lib/store.py (RETRY-LOCK): a writer that
can't get the lock within busy_timeout must back off and retry within a bounded
wall-clock budget — degrade to slower, not crash the process or hang forever."""
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

import store  # noqa: E402  (normal import — store.py has no hyphen)


def _task(id_, title="t"):
    return {
        "id": id_, "seq": 1, "title": title, "summary": "", "status": "open",
        "color": None, "effort": None, "created_ts": 1.0, "updated_ts": 1.0,
        "sessions": [], "session_meta": {}, "log": [],
    }


# A driver run in a subprocess: opens the SAME db file, takes the same BEGIN
# IMMEDIATE write lock save_task needs, holds it for sys.argv[2] seconds, then
# rolls back. Prints "locked" the instant it holds the lock so the parent's own
# write attempt can't start (and thus race) before contention is real.
_HOLD_DRIVER = """
import sqlite3, sys, time
conn = sqlite3.connect(sys.argv[1], timeout=5.0)
conn.isolation_level = None
conn.execute("BEGIN IMMEDIATE")
conn.execute("CREATE TABLE IF NOT EXISTS _lockhold(x)")
print("locked", flush=True)
time.sleep(float(sys.argv[2]))
conn.execute("ROLLBACK")
"""


def _hold_lock(db_path, seconds):
    proc = subprocess.Popen(
        [sys.executable, "-c", _HOLD_DRIVER, db_path, str(seconds)],
        stdout=subprocess.PIPE, text=True,
    )
    proc.stdout.readline()   # blocks until the child confirms it holds the lock
    return proc


class RetrySucceedsPastBusyTimeout(unittest.TestCase):
    """(a) A real writer holds the lock for 6s — longer than the 5s busy_timeout
    save_task's connection is opened with. save_task must still succeed (it would
    have raised OperationalError before this change) and must take longer than
    busy_timeout, proving it actually retried rather than winning on attempt 1."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.backend = store.SqliteBackend(self.tmp)
        self.backend.ensure()

    def tearDown(self):
        self.backend.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_write_survives_a_lock_held_longer_than_busy_timeout(self):
        holder = _hold_lock(self.backend.db_path, 6.0)
        try:
            start = time.monotonic()
            self.backend.save_task(_task("x1"))
            elapsed = time.monotonic() - start
        finally:
            self.assertEqual(holder.wait(timeout=10), 0)
        self.assertGreater(elapsed, 5.0)
        self.assertIsNotNone(self.backend.load_task("x1"))


class BudgetIsEnforced(unittest.TestCase):
    """(b) A write that keeps failing with "database is locked" for longer than
    LOCK_RETRY_BUDGET_S must raise sqlite3.OperationalError rather than hang or
    retry forever. The budget is monkeypatched down (and the failure mocked,
    rather than held via a real multi-second lock) so this stays fast."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.backend = store.SqliteBackend(self.tmp)
        self.backend.ensure()
        self._orig_budget = store.LOCK_RETRY_BUDGET_S
        store.LOCK_RETRY_BUDGET_S = 0.3

    def tearDown(self):
        store.LOCK_RETRY_BUDGET_S = self._orig_budget
        self.backend.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_write_raises_once_the_budget_elapses(self):
        def _always_locked(conn, task):
            raise sqlite3.OperationalError("database is locked")
        self.backend._write_row = _always_locked

        start = time.monotonic()
        with self.assertRaises(sqlite3.OperationalError):
            self.backend.save_task(_task("x2"))
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 5.0)   # bounded by the budget, not a hang


class NonLockErrorsAreNotRetried(unittest.TestCase):
    """(c) A real (non-contention) OperationalError — e.g. a schema/SQL bug — must
    propagate on the first attempt, with no retry delay."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.backend = store.SqliteBackend(self.tmp)
        self.backend.ensure()

    def tearDown(self):
        self.backend.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_non_lock_operational_error_propagates_immediately(self):
        calls = []

        def _boom(conn, task):
            calls.append(1)
            raise sqlite3.OperationalError("no such column: bogus")
        self.backend._write_row = _boom

        start = time.monotonic()
        with self.assertRaises(sqlite3.OperationalError):
            self.backend.save_task(_task("x3"))
        elapsed = time.monotonic() - start
        self.assertEqual(len(calls), 1)     # no retry attempted
        self.assertLess(elapsed, 0.05)


if __name__ == "__main__":
    unittest.main()
