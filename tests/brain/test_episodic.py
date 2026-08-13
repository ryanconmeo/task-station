"""The episodic adapter (``brain.episodic``) — the ONE episodic access point over
the Tasktrail contract.

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 3) from the brain source tree's
``tests/test_episodic.py`` @ 0.14.0. All 20 source cases port; the env-var names
are chunk 1's ``TASK_STATION_BRAIN_*`` namespace, and ``NoSqliteTest`` scans
``lib/brain/`` (this repo's brain plane) where the source scanned its
``scripts/`` dir. Three cases are ADDED (see their class docstrings).

Covers: stream fold (created/updated/status/checkpoint), tombstones, unknown
event kinds, unknown MAJOR schema-version (warn once), generation-bump re-sync,
manifest filename alias, the markdown-mirror fallback, absent-both graceful
unavailability, stream<->mirror equivalence, episodic_roots, and the config
derivation of episodic_stream. Also guards that no brain module imports sqlite3
— the brain reaches board DATA through config-resolved paths only, never by
opening the board's store itself.
"""
import json
import os
import unittest

from tests.brain.base import BrainTestCase, LIB
from tests.brain.test_layers import STDLIB_OK, top_level_imports

import brain.config as bconfig
import brain.episodic as episodic
import brain.errorlog as errorlog


class EpisodicBase(BrainTestCase):
    def setUp(self):
        super().setUp()
        episodic._WARNED.clear()
        self.vault = self.make_vault(self.home / "vault")
        os.environ["TASK_STATION_BRAIN_VAULT"] = str(self.vault)

    # --- stream fixture helpers --------------------------------------------
    def make_stream(self, manifest=None, events=(), shard="2026-07", name="tasktrail.json"):
        stream = self.home / "task-station-data" / "stream"
        (stream / "events").mkdir(parents=True, exist_ok=True)
        if manifest is not None:
            (stream / name).write_text(json.dumps(manifest))
        if events:
            lines = "\n".join(json.dumps(e) for e in events) + "\n"
            (stream / "events" / f"{shard}.jsonl").write_text(lines)
        os.environ["TASK_STATION_BRAIN_EPISODIC_STREAM"] = str(stream)
        return stream

    def ev(self, n, kind, uuid, seq, ts, data=None, v=1):
        return {"v": v, "ts": ts, "n": n, "event": kind,
                "task": {"uuid": uuid, "seq": seq},
                "actor": {"session": "s1"}, "data": data or {}}

    def error_log(self):
        p = errorlog.error_log_path()
        return p.read_text() if p.exists() else ""

    # --- mirror fixture helper ---------------------------------------------
    def make_mirror_note(self, slug, fm, body=""):
        d = self.vault / "task-station"
        d.mkdir(parents=True, exist_ok=True)
        lines = ["---"] + [f"{k}: {v}" for k, v in fm.items()] + ["---", "", body]
        (d / f"{slug}.md").write_text("\n".join(lines) + "\n")


class StreamFoldTest(EpisodicBase):
    def _standard_stream(self, generation=1, spec="1.0"):
        events = [
            self.ev(1, "task.created", "u-42", 42, "2026-07-10T09:00:00",
                    {"title": "Ship the widget", "status": "open"}),
            self.ev(2, "task.updated", "u-42", 42, "2026-07-11T09:00:00",
                    {"summary": "wiring the consumer"}),
            self.ev(3, "task.checkpoint", "u-42", 42, "2026-07-12T09:00:00",
                    {"brief_path": "/repo/briefs/task-42.html",
                     "glossary": [{"name": "widget", "layer": "domain",
                                   "state": "canonical", "def": "a reusable unit"}]}),
            self.ev(4, "task.status", "u-42", 42, "2026-07-13T09:00:00",
                    {"status": "closed", "closed": "2026-07-13"}),
        ]
        return self.make_stream({"spec_version": spec, "producer": "task-station/1.84.0",
                                 "generation": generation}, events)

    def test_fold_reflects_latest_state(self):
        self._standard_stream()
        rec = episodic.task_detail(42)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["title"], "Ship the widget")
        self.assertEqual(rec["status"], "closed")          # status delta applied
        self.assertEqual(rec["summary"], "wiring the consumer")  # updated delta preserved
        self.assertEqual(rec["brief_path"], "/repo/briefs/task-42.html")
        self.assertEqual(rec["glossary"][0]["name"], "widget")
        self.assertTrue(episodic.is_closed(rec))

    def test_recent_tasks_returns_records(self):
        self._standard_stream()
        rows = episodic.recent_tasks(days=3650)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["seq"], 42)

    def test_task_detail_by_uuid(self):
        self._standard_stream()
        self.assertEqual(episodic.task_detail("u-42")["seq"], 42)

    def test_status_via_to_field(self):
        self.make_stream({"spec_version": "1.0", "generation": 1}, [
            self.ev(1, "task.created", "u-1", 1, "2026-07-10T09:00:00", {"title": "t"}),
            self.ev(2, "task.status", "u-1", 1, "2026-07-11T09:00:00", {"from": "open", "to": "done"}),
        ])
        self.assertEqual(episodic.task_detail(1)["status"], "done")

    def test_tombstone_honored(self):
        self.make_stream({"spec_version": "1.0", "generation": 1}, [
            self.ev(1, "task.created", "u-9", 9, "2026-07-10T09:00:00", {"title": "doomed"}),
            self.ev(2, "task.deleted", "u-9", 9, "2026-07-11T09:00:00"),
        ])
        self.assertIsNone(episodic.task_detail(9))
        self.assertEqual(episodic.recent_tasks(days=3650), [])

    def test_unknown_event_kind_tolerated(self):
        self.make_stream({"spec_version": "1.0", "generation": 1}, [
            self.ev(1, "task.created", "u-5", 5, "2026-07-10T09:00:00", {"title": "ok"}),
            self.ev(2, "task.frobnicate", "u-5", 5, "2026-07-11T09:00:00", {"noise": True}),
        ])
        rec = episodic.task_detail(5)
        self.assertEqual(rec["title"], "ok")  # unknown kind neither crashed nor clobbered

    def test_unknown_major_version_warns_once(self):
        self._standard_stream(spec="2.0")
        episodic.recent_tasks(days=3650)
        episodic.recent_tasks(days=3650)  # second call must not re-warn
        log = self.error_log()
        self.assertEqual(log.lower().count("spec_version"), 1)
        self.assertIn("episodic:schema", log)

    def test_generation_bump_forces_resync(self):
        stream = self._standard_stream(generation=1)
        episodic.recent_tasks(days=3650)
        state = json.loads((bconfig.state_dir() / "episodic-cursor.json").read_text())
        self.assertEqual(state["generation"], 1)

        # producer rebuilds the ledger -> generation bumps
        (stream / "tasktrail.json").write_text(json.dumps(
            {"spec_version": "1.0", "generation": 2}))
        episodic.recent_tasks(days=3650)
        state = json.loads((bconfig.state_dir() / "episodic-cursor.json").read_text())
        self.assertEqual(state["generation"], 2)
        self.assertIn("resync", self.error_log())

    def test_manifest_filename_alias(self):
        self.make_stream({"spec_version": "1.0", "generation": 1}, [
            self.ev(1, "task.created", "u-7", 7, "2026-07-10T09:00:00", {"title": "aliased"}),
        ], name="taskstream.json")
        self.assertEqual(episodic.task_detail(7)["title"], "aliased")


class CursorStateTest(EpisodicBase):
    """ADDED — what the persisted cursor actually holds.

    ``_save_state`` writes ``{generation, cursor}`` on every fold. The source's
    suite asserts the generation half (the re-sync trigger) and never the cursor,
    so the highest-``n`` claim was unpinned. Note for the hub: in 0.14.0 the
    cursor is WRITE-ONLY — ``_load_state`` reads ``generation`` and nothing reads
    ``cursor`` — so this test pins the persisted shape, not a consumed value.
    """

    def test_cursor_records_the_highest_event_n(self):
        self.make_stream({"spec_version": "1.0", "generation": 1}, [
            self.ev(1, "task.created", "u-3", 3, "2026-07-10T09:00:00", {"title": "a"}),
            self.ev(7, "task.updated", "u-3", 3, "2026-07-11T09:00:00", {"summary": "b"}),
            self.ev(4, "task.updated", "u-3", 3, "2026-07-12T09:00:00", {"summary": "c"}),
        ])
        episodic.recent_tasks(days=3650)
        state = json.loads((bconfig.state_dir() / "episodic-cursor.json").read_text())
        self.assertEqual(state["cursor"], 7)  # max, not last-seen


class MirrorFallbackTest(EpisodicBase):
    def test_mirror_backend_reads_frontmatter(self):
        self.make_mirror_note("task-42", {
            "managed-by": "task-station", "seq": 42, "uuid": "u-42",
            "status": "closed", "updated": "2026-07-13", "title": "Ship the widget",
            "schema-version": 1, "closed": "2026-07-13",
            "brief_path": "/repo/briefs/task-42.html",
        }, body="## Glossary\n\nwidget\n: a reusable unit\n")
        rec = episodic.task_detail(42)
        self.assertEqual(rec["title"], "Ship the widget")
        self.assertEqual(rec["status"], "closed")
        self.assertEqual(rec["brief_path"], "/repo/briefs/task-42.html")
        self.assertTrue(episodic.is_closed(rec))
        self.assertEqual(rec["glossary"][0]["name"], "widget")

    def test_non_task_markdown_ignored(self):
        self.make_mirror_note("random", {"title": "not a task"})  # no seq / managed-by
        self.assertEqual(episodic.recent_tasks(days=3650), [])

    def test_episodic_roots_returns_mirror(self):
        self.assertEqual(episodic.episodic_roots(), [])  # nothing yet
        self.make_mirror_note("task-1", {"managed-by": "task-station", "seq": 1,
                                          "status": "open", "updated": "2026-07-13",
                                          "title": "x"})
        self.assertEqual(episodic.episodic_roots(), [self.vault / "task-station"])


class AbsentTest(EpisodicBase):
    def test_absent_both_is_unavailable(self):
        # no stream env, no mirror dir
        os.environ.pop("TASK_STATION_BRAIN_EPISODIC_STREAM", None)
        self.assertIsNone(episodic.recent_tasks(days=3650))
        self.assertIsNone(episodic.task_detail(1))
        self.assertEqual(episodic.episodic_roots(), [])

    def test_empty_stream_dir_falls_through_to_mirror(self):
        # stream dir exists but holds no ledger -> not a usable stream backend;
        # with a mirror present the mirror answers instead.
        stream = self.home / "empty-stream"
        stream.mkdir(parents=True, exist_ok=True)
        os.environ["TASK_STATION_BRAIN_EPISODIC_STREAM"] = str(stream)
        self.make_mirror_note("task-3", {"managed-by": "task-station", "seq": 3,
                                          "status": "open", "updated": "2026-07-13",
                                          "title": "from mirror"})
        self.assertEqual(episodic.task_detail(3)["title"], "from mirror")


class EquivalenceTest(EpisodicBase):
    def _core(self, rec):
        return (str(rec["seq"]), rec["status"], rec["title"], bool(rec["closed"]))

    def test_stream_and_mirror_agree_on_same_task(self):
        # fold from the stream
        self.make_stream({"spec_version": "1.0", "generation": 1}, [
            self.ev(1, "task.created", "u-42", 42, "2026-07-10T09:00:00",
                    {"title": "Ship the widget", "status": "open"}),
            self.ev(2, "task.status", "u-42", 42, "2026-07-13T09:00:00",
                    {"status": "closed", "closed": "2026-07-13"}),
        ])
        from_stream = episodic.task_detail(42)

        # now fold the SAME task from the mirror (drop the stream)
        os.environ.pop("TASK_STATION_BRAIN_EPISODIC_STREAM", None)
        self.make_mirror_note("task-42", {
            "managed-by": "task-station", "seq": 42, "uuid": "u-42",
            "status": "closed", "updated": "2026-07-13", "title": "Ship the widget",
            "schema-version": 1, "closed": "2026-07-13"})
        from_mirror = episodic.task_detail(42)

        self.assertEqual(self._core(from_stream), self._core(from_mirror))


class ConfigDerivationTest(BrainTestCase):
    def test_episodic_stream_derives_from_tasks_db(self):
        cfg = bconfig.load()
        # default tasks_db is <data home>/store/tasks.db; the stream is the
        # sibling of the store under the same data root.
        self.assertEqual(cfg["episodic_stream"], self.data_home() / "stream")

    def test_episodic_stream_follows_configured_tasks_db(self):
        os.environ["TASK_STATION_BRAIN_TASKS_DB"] = str(self.home / ".task-station/store/tasks.db")
        self.assertEqual(bconfig.load()["episodic_stream"],
                         self.home / ".task-station/stream")

    def test_episodic_stream_env_override(self):
        os.environ["TASK_STATION_BRAIN_EPISODIC_STREAM"] = str(self.home / "custom/stream")
        self.assertEqual(bconfig.load()["episodic_stream"], self.home / "custom/stream")


class NoSqliteTest(BrainTestCase):
    """Re-derived for this repo: the source scanned its ``scripts/`` dir; the
    brain plane is now ``lib/brain/``. The claim is the same one — the brain
    reads the board's episodic layer through the published Tasktrail contract,
    never by opening the board's sqlite store itself (the direct SQL was deleted
    upstream and must not come back through the port)."""

    def test_no_sqlite3_in_brain_modules(self):
        hits = []
        for f in sorted((LIB / "brain").rglob("*.py")):
            for i, line in enumerate(f.read_text(errors="ignore").splitlines(), 1):
                if "sqlite3" in line:
                    hits.append(f"{f.relative_to(LIB)}:{i}: {line.strip()}")
        self.assertEqual(hits, [], "sqlite3 must not appear in the brain plane:\n"
                         + "\n".join(hits))


class ContentPipelineLayeringTest(unittest.TestCase):
    """ADDED — the layer rule for the four modules this chunk adds, read with
    ``ast`` (chunk 1's reader, which also sees function-local imports). Chunk 2
    put the same guard beside its own modules; this one lives here so each
    chunk's claims stay reviewable on their own. Relative sibling imports
    (``from . import config``) are invisible to the reader by design — they
    cannot cross a layer.
    """

    FILES = ("brain/errorlog.py", "brain/episodic.py", "brain/ingest.py",
             "brain/distill.py")

    def test_no_module_reaches_the_board(self):
        for rel in self.FILES:
            self.assertNotIn("board", top_level_imports(LIB / rel), rel)

    def test_each_module_reaches_only_core_and_stdlib(self):
        for rel in self.FILES:
            path = LIB / rel
            self.assertTrue(path.exists(), f"{rel} missing")
            extra = top_level_imports(path) - STDLIB_OK - {"core"}
            self.assertEqual(extra, set(), f"{rel} reaches outside core+stdlib: {sorted(extra)}")


class BootstrapMergeTest(EpisodicBase):
    def test_mirror_state_plus_stream_deltas_merge(self):
        """Per the Tasktrail bootstrap contract: notes carry full state, stream
        events fold deltas on top — a thin event history (no title in any event)
        must not erase the title the mirror knows."""
        self.make_mirror_note("7-some-task", {
            "managed-by": "task-station", "seq": 7,
            "status": '"open"', "title": '"Some Task"',
            "updated": '"2026-07-10T00:00:00+00:00"',
        })
        self.make_stream(
            manifest={"spec_version": "1.0", "producer": "t", "generation": 1},
            events=[self.ev(1, "task.status", "u-7", 7, "2026-07-14T00:00:00+00:00",
                            {"status": "closed"})],
        )
        recs = episodic.recent_tasks(days=3650)
        rec = next(r for r in recs if str(r.get("seq")) == "7")
        self.assertEqual(rec["title"], "Some Task")   # from the mirror bootstrap
        self.assertEqual(rec["status"], "closed")      # from the stream delta


if __name__ == "__main__":
    unittest.main()
