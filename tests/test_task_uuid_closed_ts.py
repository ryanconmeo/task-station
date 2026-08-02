"""A-1: stable in-band task uuid + a real closed_ts.

The task `id` has always been a uuid4 (new_task) — this surfaces it as an explicit,
immutable `uuid` field (uuid == id) that survives every mutation / resync / export
round-trip and equals the obsidian sidecar-index key. `closed_ts` records the real
moment a task entered a closed status (stamped by every close path, cleared on
reopen, and NOT disturbed by later updates), with a one-time backfill of already-
closed tasks to their current updated_ts. Both are threaded through load/save and
surfaced by the MCP get_task detail.
"""
import importlib
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

import store  # noqa: E402
import obsidian_sync  # noqa: E402

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


# ----------------------------------------------------------------- uuid (engine)

class UuidThroughEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        os.environ.pop("TASK_STATION_GATE", None)
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        store.reset_cache()

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self, title, **kw):
        t = ts.new_task(title, "summary for " + title, **kw)
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def test_new_task_uuid_equals_id(self):
        t = ts.new_task("Fresh", "raised")
        self.assertEqual(t["uuid"], t["id"])
        self.assertTrue(t["uuid"])                       # non-empty

    def test_uuid_survives_save_load_roundtrip(self):
        t = ts.new_task("Persisted", "")
        uid = t["uuid"]
        ts.save_task(t)
        self.assertEqual(ts.load_task(t["id"])["uuid"], uid)

    def test_uuid_immutable_across_mutation(self):
        t = self._seed("Mutating")
        uid = t["uuid"]
        # a store.mutate cycle (add a note) must never touch uuid
        ts.mutate(t["id"], lambda x: ts.add_log(x, "did a thing"))
        self.assertEqual(ts.load_task(t["id"])["uuid"], uid)

    def test_uuid_immutable_across_close_and_reopen(self):
        t = self._seed("Round the lifecycle", status="active")
        uid = t["uuid"]
        ts.set_link("life-sess", t["id"])
        with redirect_stdout(io.StringIO()):
            ts.cmd_done(_Args(session="life-sess", task=None))
        self.assertEqual(ts.load_task(t["id"])["uuid"], uid)
        with redirect_stdout(io.StringIO()):
            ts.cmd_attach(_Args(session="reopen-sess", task=str(t["seq"]),
                                color=None, note=None))
        self.assertEqual(ts.load_task(t["id"])["uuid"], uid)

    def test_uuid_equals_obsidian_sidecar_index_key(self):
        t = self._seed("Exported task")
        vault = tempfile.mkdtemp(prefix="uuid-vault-")
        try:
            os.environ["TASK_STATION_OBSIDIAN"] = "1"
            fname = obsidian_sync.export_task(t, vault)
            self.assertIsNotNone(fname)                  # export actually wrote a note
            pdir = obsidian_sync.plugin_dir(vault)
            with open(os.path.join(pdir, obsidian_sync._INDEX_NAME), encoding="utf-8") as f:
                idx = json.load(f)
            # the sidecar index is keyed by task id; uuid must equal that key
            self.assertIn(t["uuid"], idx)
            self.assertEqual(t["uuid"], t["id"])
        finally:
            os.environ.pop("TASK_STATION_OBSIDIAN", None)
            shutil.rmtree(vault, ignore_errors=True)


# ------------------------------------------------------------ closed_ts (engine)

class ClosedTsThroughEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        os.environ.pop("TASK_STATION_GATE", None)
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        store.reset_cache()

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self, title, **kw):
        t = ts.new_task(title, "summary for " + title, **kw)
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def test_new_and_open_tasks_have_no_closed_ts(self):
        t = ts.new_task("Still open", "")
        self.assertIsNone(t.get("closed_ts"))
        ts.save_task(t)
        self.assertIsNone(ts.load_task(t["id"]).get("closed_ts"))

    def test_done_session_stamps_closed_ts(self):
        t = self._seed("Close via /done", status="active")
        ts.set_link("d-sess", t["id"])
        with redirect_stdout(io.StringIO()):
            ts.cmd_done(_Args(session="d-sess", task=None))
        closed = ts.load_task(t["id"])
        self.assertEqual(closed["status"], "closed")
        self.assertIsInstance(closed.get("closed_ts"), float)
        self.assertGreater(closed["closed_ts"], 0)

    def test_close_by_ref_stamps_closed_ts(self):
        t = self._seed("Close by ref")
        with redirect_stdout(io.StringIO()):
            ts.cmd_done(_Args(session=None, task=str(t["seq"])))
        self.assertIsInstance(ts.load_task(t["id"]).get("closed_ts"), float)

    def test_post_close_update_does_not_change_closed_ts(self):
        t = self._seed("Post-close update")
        with redirect_stdout(io.StringIO()):
            ts.cmd_done(_Args(session=None, task=str(t["seq"])))
        closed = ts.load_task(t["id"])
        stamp = closed["closed_ts"]
        # an ordinary later update (still closed) must leave closed_ts alone
        ts.mutate(t["id"], lambda x: ts.touch(x, note="a later edit"))
        after = ts.load_task(t["id"])
        self.assertEqual(after["closed_ts"], stamp)
        self.assertGreaterEqual(after["updated_ts"], stamp)

    def test_reopen_clears_closed_ts(self):
        t = self._seed("Reopen clears", status="active")
        with redirect_stdout(io.StringIO()):
            ts.cmd_done(_Args(session=None, task=str(t["seq"])))
        self.assertIsNotNone(ts.load_task(t["id"]).get("closed_ts"))
        with redirect_stdout(io.StringIO()):
            ts.cmd_attach(_Args(session="reopen", task=str(t["seq"]),
                                color=None, note=None))
        reopened = ts.load_task(t["id"])
        self.assertEqual(reopened["status"], "open")
        self.assertIsNone(reopened.get("closed_ts"))


# --------------------------------------------------------------- MCP get_task

class McpExposesUuidAndClosedTs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="uuid-mcp-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        sys.modules.pop("mcp_server", None)
        self.mcp = importlib.import_module("mcp_server")
        self.ts = self.mcp._engine()
        self.ts.DATA = self.tmp
        self.ts.STORE = os.path.join(self.tmp, "store")
        self.ts.TASKS_DIR = os.path.join(self.ts.STORE, "tasks")
        self.ts.LINKS_DIR = os.path.join(self.ts.STORE, "links")
        store.reset_cache()

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_get_task_detail_shows_uuid(self):
        t = self.mcp._create_task("Bridge task", "via desktop")
        detail = self.mcp._get_task(str(t["seq"]))
        self.assertIn(t["uuid"], detail)

    def test_set_status_close_stamps_and_reopen_clears_closed_ts(self):
        t = self.mcp._create_task("Lifecycle", "")
        ref = str(t["seq"])
        closed = self.mcp._set_status(ref, "closed")
        self.assertIsInstance(closed.get("closed_ts"), float)
        detail = self.mcp._get_task(ref)
        self.assertIn("Closed", detail)                  # closed_ts surfaced
        reopened = self.mcp._set_status(ref, "open")
        self.assertIsNone(reopened.get("closed_ts"))


# ------------------------------------------------------- store migration (backfill)

class TaskMetaMigration(unittest.TestCase):
    """The additive backfill stamps uuid=id on every legacy row and
    closed_ts=updated_ts on already-closed rows. Idempotent + defensive."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store_dir = os.path.join(self.tmp, "store")
        os.makedirs(self.store_dir)
        store.reset_cache()

    def tearDown(self):
        store.reset_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed_legacy(self):
        """A pre-A1 tasks.db: rows with NO uuid and NO closed_ts, one open + one
        closed (updated_ts 4242.0)."""
        db = os.path.join(self.store_dir, "tasks.db")
        conn = store.sqlite3.connect(db)
        conn.executescript(
            "CREATE TABLE tasks (id TEXT PRIMARY KEY, seq INTEGER, title TEXT, summary TEXT,"
            " status TEXT, color TEXT, effort TEXT, created_ts REAL, updated_ts REAL,"
            " sessions TEXT, session_meta TEXT, log TEXT, data TEXT NOT NULL);")
        rows = [("open-id", 1, "open", 1000.0), ("closed-id", 2, "closed", 4242.0)]
        for tid, seq, status, uts in rows:
            data = json.dumps({"id": tid, "seq": seq, "title": tid,
                               "status": status, "updated_ts": uts})
            conn.execute(
                "INSERT INTO tasks (id, seq, status, updated_ts, data) VALUES (?,?,?,?,?)",
                (tid, seq, status, uts, data))
        conn.commit()
        conn.close()

    def test_backfills_uuid_for_every_row(self):
        self._seed_legacy()
        store.reset_cache()
        b = store.SqliteBackend(self.store_dir)
        b._connect()   # runs the migration
        for tid in ("open-id", "closed-id"):
            self.assertEqual(b.load_task(tid)["uuid"], tid)

    def test_backfills_closed_ts_for_closed_rows_only(self):
        self._seed_legacy()
        store.reset_cache()
        b = store.SqliteBackend(self.store_dir)
        b._connect()
        self.assertEqual(b.load_task("closed-id")["closed_ts"], 4242.0)
        self.assertIsNone(b.load_task("open-id").get("closed_ts"))

    def test_migration_idempotent_double_run(self):
        self._seed_legacy()
        store.reset_cache()
        b1 = store.SqliteBackend(self.store_dir); b1.ensure()
        first = {t["id"]: (t.get("uuid"), t.get("closed_ts")) for t in b1.all_tasks()}
        b1.close()
        store.reset_cache()
        b2 = store.SqliteBackend(self.store_dir); b2.ensure()
        second = {t["id"]: (t.get("uuid"), t.get("closed_ts")) for t in b2.all_tasks()}
        b2.close()
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
