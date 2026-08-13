"""Artifact ingest (``brain.ingest``) — glossary + brief_path -> one vault note.

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 3) from the brain source tree's
``tests/test_ingest_artifacts.py`` @ 0.14.0. All 10 source cases port 1:1 with
the env-var names moved to chunk 1's ``TASK_STATION_BRAIN_*`` namespace and the
module renamed (``ingest_artifacts`` -> ``brain.ingest``). Three cases ADDED for
``note_slug`` (see ``NoteSlugTest``).

Covers: a closed task with brief + glossary produces a note with the artifact
LINK (not a copy) + the glossary terms; re-run is idempotent (updates, never a
duplicate note); a genuine change refreshes the note in place; tasks lacking
both artifacts, and open tasks, are skipped; unavailable episodic layer no-ops.
"""
import json
import os
import unittest

from tests.brain.base import BrainTestCase

import brain.config as bconfig
import brain.episodic as episodic
import brain.ingest as ingest
import brain.notes as notes


class IngestBase(BrainTestCase):
    def setUp(self):
        super().setUp()
        episodic._WARNED.clear()
        self.vault = self.make_vault(self.home / "vault")
        os.environ["TASK_STATION_BRAIN_VAULT"] = str(self.vault)

    def make_stream(self, events, generation=1):
        stream = self.home / "task-station-data" / "stream"
        (stream / "events").mkdir(parents=True, exist_ok=True)
        (stream / "tasktrail.json").write_text(json.dumps(
            {"spec_version": "1.0", "producer": "task-station/1.84.0", "generation": generation}))
        (stream / "events" / "2026-07.jsonl").write_text(
            "\n".join(json.dumps(e) for e in events) + "\n")
        os.environ["TASK_STATION_BRAIN_EPISODIC_STREAM"] = str(stream)
        return stream

    def ev(self, n, kind, seq, ts, data=None):
        return {"v": 1, "ts": ts, "n": n, "event": kind,
                "task": {"uuid": f"u-{seq}", "seq": seq},
                "actor": {"session": "s1"}, "data": data or {}}

    def closed_task_events(self, seq=42, title="Ship the widget",
                           brief="/repo/briefs/task-42.html", glossary=None):
        if glossary is None:
            glossary = [{"name": "widget", "layer": "domain", "state": "canonical",
                         "def": "a reusable UI unit"}]
        return [
            self.ev(1, "task.created", seq, "2026-07-10T09:00:00", {"title": title, "status": "open"}),
            self.ev(2, "task.checkpoint", seq, "2026-07-12T09:00:00",
                    {"brief_path": brief, "glossary": glossary}),
            self.ev(3, "task.status", seq, "2026-07-13T09:00:00",
                    {"status": "closed", "closed": "2026-07-13"}),
        ]

    def run_ingest(self):
        return ingest.run(bconfig.load(), days=3650)

    def note_files(self):
        return sorted((self.vault / "notes").glob("task-*.md"))


class CreateTest(IngestBase):
    def test_creates_one_note_with_link_and_terms(self):
        self.make_stream(self.closed_task_events())
        result = self.run_ingest()
        self.assertEqual(result["created"], ["task-42-ship-the-widget"])

        found = self.note_files()
        self.assertEqual(len(found), 1)
        text = found[0].read_text()
        # the brief is a LINK (path present), never copied/wikified
        self.assertIn("/repo/briefs/task-42.html", text)
        self.assertIn("never copied", text.lower())
        # glossary term is present as canonical vocabulary
        self.assertIn("widget", text)
        self.assertIn("a reusable UI unit", text)
        # frontmatter contract: reference type, task-station provenance
        fm, _ = notes.parse_note(text)
        self.assertEqual(fm["type"], "reference")
        self.assertEqual(fm["source"], "task-station:42")

    def test_index_line_added_for_new_note(self):
        self.make_stream(self.closed_task_events())
        self.run_ingest()
        idx = (self.vault / "INDEX.md").read_text()
        self.assertIn("[[task-42-ship-the-widget]]", idx)

    def test_glossary_only_task_ingested(self):
        events = self.closed_task_events(seq=7, title="Glossary only", brief=None)
        # drop brief from the checkpoint
        events[1]["data"].pop("brief_path")
        self.make_stream(events)
        result = self.run_ingest()
        self.assertEqual(result["created"], ["task-7-glossary-only"])

    def test_brief_only_task_ingested(self):
        self.make_stream(self.closed_task_events(seq=8, title="Brief only", glossary=[]))
        result = self.run_ingest()
        self.assertEqual(result["created"], ["task-8-brief-only"])


class IdempotencyTest(IngestBase):
    def test_rerun_updates_not_duplicates(self):
        self.make_stream(self.closed_task_events())
        first = self.run_ingest()
        self.assertEqual(len(first["created"]), 1)

        second = self.run_ingest()
        # no second note, and nothing re-created; the unchanged re-run is a no-op
        self.assertEqual(len(self.note_files()), 1)
        self.assertEqual(second["created"], [])
        self.assertGreaterEqual(second["skipped"], 1)

    def test_change_refreshes_note_in_place(self):
        self.make_stream(self.closed_task_events())
        self.run_ingest()

        # producer adds a glossary term -> the note should be refreshed, not duplicated
        events = self.closed_task_events()
        events[1]["data"]["glossary"].append(
            {"name": "gadget", "def": "a second canonical term"})
        self.make_stream(events, generation=1)
        result = self.run_ingest()

        self.assertEqual(len(self.note_files()), 1)
        self.assertEqual(result["updated"], ["task-42-ship-the-widget"])
        text = self.note_files()[0].read_text()
        self.assertIn("gadget", text)
        self.assertIn("widget", text)  # original term retained

        # a THIRD, unchanged run is now a clean no-op (true idempotency)
        again = self.run_ingest()
        self.assertEqual(again["updated"], [])
        self.assertEqual(len(self.note_files()), 1)


class SkipTest(IngestBase):
    def test_closed_task_without_artifacts_skipped(self):
        self.make_stream([
            self.ev(1, "task.created", 5, "2026-07-10T09:00:00", {"title": "bare"}),
            self.ev(2, "task.status", 5, "2026-07-13T09:00:00", {"status": "closed"}),
        ])
        result = self.run_ingest()
        self.assertEqual(result["created"], [])
        self.assertEqual(self.note_files(), [])
        self.assertGreaterEqual(result["skipped"], 1)

    def test_open_task_with_artifacts_skipped(self):
        # has brief+glossary but never closed -> not ingested (CLOSED only)
        self.make_stream([
            self.ev(1, "task.created", 6, "2026-07-10T09:00:00", {"title": "still open"}),
            self.ev(2, "task.checkpoint", 6, "2026-07-12T09:00:00",
                    {"brief_path": "/x/y.html",
                     "glossary": [{"name": "term", "def": "d"}]}),
        ])
        result = self.run_ingest()
        self.assertEqual(result["created"], [])
        self.assertEqual(self.note_files(), [])

    def test_unavailable_layer_is_graceful(self):
        os.environ.pop("TASK_STATION_BRAIN_EPISODIC_STREAM", None)
        result = ingest.run(bconfig.load(), days=3650)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["created"], [])


class MirrorSourceTest(IngestBase):
    def test_ingest_from_markdown_mirror(self):
        # no stream — ingest must work off the exported mirror too
        os.environ.pop("TASK_STATION_BRAIN_EPISODIC_STREAM", None)
        d = self.vault / "task-station"
        d.mkdir(parents=True, exist_ok=True)
        (d / "task-99.md").write_text(
            "---\nmanaged-by: task-station\nseq: 99\nuuid: u-99\nstatus: closed\n"
            "updated: 2026-07-13\ntitle: Mirror task\nclosed: 2026-07-13\n"
            "brief_path: /repo/briefs/task-99.html\n---\n\n"
            "## Glossary\n\nthingy\n: a mirror-sourced term\n")
        result = self.run_ingest()
        self.assertEqual(result["created"], ["task-99-mirror-task"])
        text = self.note_files()[0].read_text()
        self.assertIn("/repo/briefs/task-99.html", text)
        self.assertIn("thingy", text)


class NoteSlugTest(unittest.TestCase):
    """ADDED — ``note_slug`` must always return a slug the write path ACCEPTS.

    Every slug goes straight into ``notes.resolve_note_path``, which raises
    ``NoteIOError`` on anything outside ``^[a-z0-9][a-z0-9-]{1,80}$``. A refusal
    there aborts the whole heal step, so the three inputs that can bend the slug
    — a missing seq, an over-long title, a title with nothing sluggable in it —
    are pinned here. The source suite only ever fed it well-behaved titles.
    """

    def _valid(self, slug):
        self.assertIsNotNone(notes.SLUG_RX.match(slug), f"not a legal slug: {slug!r}")
        return slug

    def test_missing_seq_falls_back_to_a_uuid_stub(self):
        slug = ingest.note_slug({"uuid": "abcdef0123456789", "title": "No seq here"})
        self.assertEqual(self._valid(slug), "task-abcdef01-no-seq-here")  # uuid[:8]

    def test_a_very_long_title_stays_within_the_slug_limit(self):
        slug = ingest.note_slug({"seq": 42, "title": "word " * 80})
        self._valid(slug)
        self.assertTrue(slug.startswith("task-42-word"))
        self.assertLessEqual(len(slug), 81)

    def test_an_unsluggable_title_degrades_to_the_bare_task_slug(self):
        self.assertEqual(self._valid(ingest.note_slug({"seq": 5, "title": "!!! ??? ***"})),
                         "task-5")


if __name__ == "__main__":
    unittest.main()
