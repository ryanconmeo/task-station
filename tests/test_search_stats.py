"""Cross-task FTS search + per-task time/cost stats.

Covers the store's FTS5 index (sync on write, ranking, delete, migration/backfill)
and its transparent LIKE fallback, plus the engine's search rendering, the
idle-gap-capped time-span heuristic, and worker-cost accumulation. Exercised both
directly against lib/store.py and through task-station.py's public primitives,
under per-test temp-home isolation (mirrors tests/test_store_sqlite.py)."""
import importlib.util
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

import store  # noqa: E402  (normal import — store.py has no hyphen)

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


def _repoint(tmp):
    os.environ["TASK_STATION_HOME"] = tmp
    ts.DATA = tmp
    ts.STORE = os.path.join(tmp, "store")
    ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
    ts.LINKS_DIR = os.path.join(ts.STORE, "links")
    store.reset_cache()


def _mk(title, summary="", **kw):
    t = ts.new_task(title, summary, color=kw.pop("color", None), effort=kw.pop("effort", None))
    t.update(kw)
    return t


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # FTS support is process-wide + cached; restore it around each test so a
        # fallback test can't leak into the next.
        self._fts_saved = store._fts5_supported
        _repoint(self.tmp)

    def tearDown(self):
        store._fts5_supported = self._fts_saved
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)


# ------------------------------------------------------------------- search ---

class SearchTest(_Base):
    def _seed(self):
        ts.save_task(_mk("Fix the auth bug", "authentication token refresh fails",
                         decisions=["use JWT rotation"]))
        ts.save_task(_mk("Add dark mode", "theme toggle for the board"))
        t3 = _mk("Search feature", "FTS5 index over task text")
        t3["status"] = ts.STATUS_CLOSED
        ts.save_task(t3)

    def _ids(self, query, **kw):
        return {h["id"] for h in ts.search_tasks(query, **kw)}

    def test_finds_by_title(self):
        self._seed()
        hits = ts.search_tasks("dark")
        self.assertEqual(len(hits), 1)
        self.assertEqual(ts.load_task(hits[0]["id"])["title"], "Add dark mode")

    def test_finds_by_summary_and_decisions(self):
        self._seed()
        # summary term
        self.assertTrue(self._ids("token"))
        # decision term (proves decisions are indexed)
        jwt = ts.search_tasks("jwt")
        self.assertEqual(len(jwt), 1)
        self.assertEqual(ts.load_task(jwt[0]["id"])["title"], "Fix the auth bug")

    def test_prefix_match(self):
        self._seed()
        # 'auth' should prefix-match 'authentication' (FTS) or substring (LIKE).
        self.assertTrue(self._ids("auth"))

    def test_no_match_is_empty(self):
        self._seed()
        self.assertEqual(ts.search_tasks("zzznomatch"), [])

    def test_snippet_present(self):
        self._seed()
        hit = ts.search_tasks("token")[0]
        self.assertIn("token", hit["snippet"].lower())

    def test_sync_on_update(self):
        t = _mk("Alpha task", "original body about widgets")
        ts.save_task(t)
        self.assertTrue(ts.search_tasks("widgets"))
        t["summary"] = "rewritten body about gadgets"
        ts.save_task(t)
        # New text is indexed; the old term no longer matches.
        self.assertTrue(ts.search_tasks("gadgets"))
        self.assertFalse(ts.search_tasks("widgets"))

    def test_removed_on_delete(self):
        t = _mk("Ephemeral", "temporary content marker uniqueterm")
        ts.save_task(t)
        self.assertTrue(ts.search_tasks("uniqueterm"))
        ts.delete_task(t["id"])
        self.assertFalse(ts.search_tasks("uniqueterm"))

    def test_status_filters(self):
        self._seed()
        # "index"/"text" is on the CLOSED task; "board" on an OPEN one.
        rows_all = ts._search_core("task", "all")
        rows_open = ts._search_core("task", "open")
        rows_closed = ts._search_core("task", "closed")
        self.assertTrue(all(not ts.is_closed(t) for t, _ in rows_open))
        self.assertTrue(all(ts.is_closed(t) for t, _ in rows_closed))
        self.assertGreaterEqual(len(rows_all), len(rows_open))

    def test_tier1_render(self):
        self._seed()
        rows = ts._search_core("token", "all")
        out = ts._format_search("token", rows, "all")
        self.assertIn("Fix the auth bug", out)
        self.assertIn("#", out)                       # seq marker
        self.assertIn("--detail", out)                # the guidance note
        self.assertIn("history", out)

    def test_tier1_empty_render(self):
        self._seed()
        out = ts._format_search("zzznone", ts._search_core("zzznone", "all"), "all")
        self.assertIn("No", out)
        self.assertIn("zzznone", out)


class NumericRefLookupTest(_Base):
    """`search <n>` / `/todo search <n>` resolve a bare display number to that
    task's digest (regression for the "numeric lookup falsely reports no match")."""

    def test_numeric_query_resolves_to_task_digest(self):
        t = _mk("Balance sheet report", "quarterly rollup")
        ts.save_task(t)
        ts.ensure_seqs()
        seq = ts.load_task(t["id"])["seq"]
        out = ts._numeric_ref_detail(str(seq), None)
        self.assertIsNotNone(out)
        self.assertIn("Balance sheet report", out)

    def test_unknown_number_falls_through(self):
        # No task carries seq 99999 → None so the caller runs a text search
        # (lets a PR/story number still match by text).
        ts.save_task(_mk("Some task", "body"))
        self.assertIsNone(ts._numeric_ref_detail("99999", None))

    def test_non_numeric_falls_through(self):
        ts.save_task(_mk("Some task", "body"))
        self.assertIsNone(ts._numeric_ref_detail("token", None))


class LikeFallbackTest(_Base):
    def setUp(self):
        super().setUp()
        # Force the sqlite3-less / no-FTS5 path even where FTS5 is present.
        store._fts5_supported = False
        store.reset_cache()

    def test_like_search_ranks_by_hits(self):
        # Two hits of 'report' should rank above one hit.
        ts.save_task(_mk("Weekly report", "report report everywhere"))
        ts.save_task(_mk("Misc", "a single report mention"))
        hits = ts.search_tasks("report")
        self.assertEqual(len(hits), 2)
        self.assertEqual(ts.load_task(hits[0]["id"])["title"], "Weekly report")

    def test_like_and_semantics(self):
        ts.save_task(_mk("Cache invalidation", "redis keys and ttl"))
        ts.save_task(_mk("Redis setup", "install redis server"))
        # Both terms must appear → only the first task qualifies.
        hits = ts.search_tasks("redis ttl")
        self.assertEqual(len(hits), 1)
        self.assertEqual(ts.load_task(hits[0]["id"])["title"], "Cache invalidation")

    def test_like_snippet(self):
        ts.save_task(_mk("X", "lots of leading text before the needle marker and trailing text after"))
        hit = ts.search_tasks("needle")[0]
        self.assertIn("needle", hit["snippet"])


class MigrationBackfillTest(_Base):
    def test_backfill_indexes_preexisting_rows(self):
        if not store._fts5_available():
            self.skipTest("host sqlite3 lacks FTS5")
        # Populate, then simulate a pre-FTS DB: drop the index + reset the schema
        # version so the next open triggers the one-time backfill.
        ts.save_task(_mk("Legacy alpha", "content about migrations backfilltoken"))
        ts.save_task(_mk("Legacy beta", "another entry"))
        db = os.path.join(ts.STORE, "tasks.db")
        store.reset_cache()
        conn = sqlite3.connect(db)
        conn.execute("DROP TABLE IF EXISTS tasks_fts")
        conn.execute("PRAGMA user_version=0")
        conn.commit()
        conn.close()
        store.reset_cache()
        # Reopening rebuilds + backfills the index from the existing task rows.
        self.assertTrue(ts.search_tasks("backfilltoken"))


# -------------------------------------------------------------------- stats ---

class TimeSpanTest(_Base):
    def test_extends_within_gap(self):
        t = _mk("T")
        base = 1_000_000.0
        ts.record_activity_span(t, base)
        ts.record_activity_span(t, base + 600)        # +10min → same span
        ts.record_activity_span(t, base + 1200)       # +20min → same span
        self.assertEqual(len(t["spans"]), 1)
        self.assertAlmostEqual(ts.time_in_task(t), 1200.0)

    def test_new_span_after_gap_excludes_gap(self):
        t = _mk("T")
        base = 2_000_000.0
        ts.record_activity_span(t, base)
        ts.record_activity_span(t, base + 300)         # 5min of work
        ts.record_activity_span(t, base + 300 + 4000)  # >30min idle → new span starts
        ts.record_activity_span(t, base + 300 + 4000 + 120)  # +2min work
        self.assertEqual(len(t["spans"]), 2)
        # Total = 300 + 120; the 4000s idle gap is NOT counted.
        self.assertAlmostEqual(ts.time_in_task(t), 420.0)

    def test_out_of_order_ignored(self):
        t = _mk("T")
        ts.record_activity_span(t, 5000.0)
        ts.record_activity_span(t, 4000.0)             # earlier → ignored
        self.assertEqual(len(t["spans"]), 1)
        self.assertAlmostEqual(ts.time_in_task(t), 0.0)

    def test_spans_capped(self):
        t = _mk("T")
        # Every event past the gap cap → its own span; cap enforced.
        for i in range(ts.SPANS_KEEP + 25):
            ts.record_activity_span(t, i * (ts.IDLE_GAP_CAP + 100))
        self.assertLessEqual(len(t["spans"]), ts.SPANS_KEEP)

    def test_touch_records_span(self):
        t = _mk("T")
        ts.touch(t, note="work")
        self.assertTrue(t.get("spans"))

    def test_stats_line_time(self):
        t = _mk("T")
        t["sessions"] = ["s1", "s2"]
        t["spans"] = [[3_000_000.0, 3_000_000.0 + 5400]]   # one 1h30m span
        line = ts.task_stats_line(t)
        self.assertIn("~1h 30m", line)
        self.assertIn("2 sessions", line)

    def test_stats_line_empty_for_new_task(self):
        self.assertEqual(ts.task_stats_line(_mk("brand new")), "")


class CostTest(_Base):
    def test_accumulates(self):
        t = _mk("T")
        self.assertTrue(ts.add_cost(t, 0.25))
        self.assertTrue(ts.add_cost(t, 1.50))
        total, runs = ts.task_cost(t)
        self.assertAlmostEqual(total, 1.75)
        self.assertEqual(runs, 2)

    def test_ignores_nonpositive_and_garbage(self):
        t = _mk("T")
        self.assertFalse(ts.add_cost(t, 0))
        self.assertFalse(ts.add_cost(t, -3))
        self.assertFalse(ts.add_cost(t, "notanumber"))
        self.assertEqual(ts.task_cost(t), (0.0, 0))

    def test_stats_line_cost(self):
        t = _mk("T")
        ts.add_cost(t, 2.5)
        self.assertIn("workers $2.50", ts.task_stats_line(t))

    def test_cmd_add_cost_persists(self):
        t = _mk("Delegated work")
        t["seq"] = 42
        ts.save_task(t)

        class A:
            task = "42"
            usd = "0.99"
        ts.cmd_add_cost(A())
        got = ts.load_task(t["id"])
        total, runs = ts.task_cost(got)
        self.assertAlmostEqual(total, 0.99)
        self.assertEqual(runs, 1)


if __name__ == "__main__":
    unittest.main()
