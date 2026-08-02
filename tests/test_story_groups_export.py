# tests/test_story_groups_export.py
"""WS13 story hubs — a guaranteed, STRUCTURED grouping axis, orthogonal to the
token-based category sub-groups. A story group exists for every story id (parsed
from the task's structured `stories` field, NOT title tokens) referenced by >= 1
task; it is emitted as <dir>/stories/<id>.md carrying its members and the ADO url
when any member task stored a full url. Dissolves at zero references, toggle-off
prunes; user files survive; two syncs are byte-identical."""
import importlib
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

import obsidian_sync    # noqa: E402
import export           # noqa: E402


class StoryRefHelpers(unittest.TestCase):
    def test_ado_workitem_url_yields_numeric_id_and_link(self):
        sid, url = obsidian_sync.story_ref(
            {"url": "https://dev.azure.com/Org/Proj/_workitems/edit/1234", "desc": "x"})
        self.assertEqual(sid, "1234")
        self.assertEqual(url, "https://dev.azure.com/Org/Proj/_workitems/edit/1234")

    def test_generic_http_url_uses_last_path_segment_as_id(self):
        sid, url = obsidian_sync.story_ref({"url": "https://ado/story/42", "desc": ""})
        self.assertEqual(sid, "42")
        self.assertEqual(url, "https://ado/story/42")

    def test_plain_id_token_is_its_own_id_with_no_link(self):
        sid, url = obsidian_sync.story_ref("387")
        self.assertEqual(sid, "387")
        self.assertIsNone(url)

    def test_blank_entry_yields_none(self):
        self.assertEqual(obsidian_sync.story_ref({"url": "", "desc": ""}), (None, None))
        self.assertEqual(obsidian_sync.story_ref(""), (None, None))

    def test_slug_is_filesystem_safe(self):
        self.assertEqual(obsidian_sync.story_slug("1234"), "1234")
        self.assertEqual(obsidian_sync.story_slug("PROJ 12/beta"), "PROJ-12-beta")
        self.assertEqual(obsidian_sync.story_slug(""), "story")


def _task(tid, seq, title, color, stories, status="open"):
    return {"id": tid, "uuid": "uuid-" + tid, "seq": seq, "title": title,
            "color": color, "status": status, "stories": stories}


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="story-exp-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        importlib.reload(obsidian_sync)
        self.dir = os.path.join(self.tmp, "brain")
        os.makedirs(self.dir)

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, task):
        fname, idx = obsidian_sync.note_filename(task, self.dir)
        obsidian_sync._save_index(self.dir, idx)
        obsidian_sync._atomic_write(os.path.join(self.dir, fname),
                                    obsidian_sync.render_note(task))
        return fname

    def _story_dir(self):
        return os.path.join(self.dir, "stories")

    def _read(self, *parts):
        with open(os.path.join(self._story_dir(), *parts), encoding="utf-8") as f:
            return f.read()

    _URL = "https://dev.azure.com/Org/Proj/_workitems/edit/1234"

    def _seed_two_ref_story(self):
        # Two tasks reference story 1234 (one via full ADO url, one via a bare id),
        # in DIFFERENT categories (stories are cross-category), plus one unrelated task.
        self._write(_task("a", 1, "Wire the API", "green",
                          [{"url": self._URL, "desc": "the story"}]))
        self._write(_task("b", 2, "Fix the parser", "red", [{"url": "1234", "desc": ""}]))
        self._write(_task("c", 3, "Unrelated chore", "blue",
                          [{"url": "https://ado/story/99", "desc": ""}]))


class StoryHubGenerate(_Base):
    def test_hub_at_two_references(self):
        self._seed_two_ref_story()
        export.sync_story_hubs(self.dir, enabled=True)
        hub = self._read("1234.md")
        self.assertIn("managed-by: task-station", hub)
        self.assertIn("kind: story-hub", hub)
        self.assertIn("story: \"1234\"", hub)
        self.assertIn("count: 2", hub)
        self.assertIn("# Story 1234", hub)
        # both members, cross-category, as [[stem|title]] lines
        self.assertIn("[[1-wire-the-api|", hub)
        self.assertIn("[[2-fix-the-parser|", hub)

    def test_ado_url_carried_when_a_member_has_a_full_url(self):
        self._seed_two_ref_story()
        export.sync_story_hubs(self.dir, enabled=True)
        hub = self._read("1234.md")
        self.assertIn("ado-url: \"%s\"" % self._URL, hub)
        self.assertIn(self._URL, hub.split("---", 2)[-1])   # also linked in the body

    def test_single_reference_story_gets_a_hub(self):
        self._seed_two_ref_story()
        export.sync_story_hubs(self.dir, enabled=True)
        # story 99 is referenced by exactly one task -> still gets a hub (threshold is >= 1)
        hub = self._read("99.md")
        self.assertIn("count: 1", hub)
        self.assertIn("[[3-unrelated-chore|", hub)

    def test_open_active_ordered_before_closed(self):
        self._write(_task("a", 1, "Closed first seq", "green",
                          [{"url": "1234"}], status="closed"))
        self._write(_task("b", 2, "Open later seq", "green", [{"url": "1234"}]))
        export.sync_story_hubs(self.dir, enabled=True)
        hub = self._read("1234.md")
        self.assertLess(hub.index("open-later-seq"), hub.index("closed-first-seq"))

    def test_two_syncs_byte_identical(self):
        self._seed_two_ref_story()
        export.sync_story_hubs(self.dir, enabled=True)
        first = self._read("1234.md")
        export.sync_story_hubs(self.dir, enabled=True)
        self.assertEqual(self._read("1234.md"), first)


class StoryHubDissolveToggle(_Base):
    def test_survives_drop_to_one_reference(self):
        self._seed_two_ref_story()
        export.sync_story_hubs(self.dir, enabled=True)
        self.assertTrue(os.path.exists(os.path.join(self._story_dir(), "1234.md")))
        obsidian_sync.remove_task_note("b", self.dir)   # only one ref left
        export.sync_story_hubs(self.dir, enabled=True)
        self.assertTrue(os.path.exists(os.path.join(self._story_dir(), "1234.md")))

    def test_dissolves_at_zero_references(self):
        self._seed_two_ref_story()
        export.sync_story_hubs(self.dir, enabled=True)
        self.assertTrue(os.path.exists(os.path.join(self._story_dir(), "1234.md")))
        obsidian_sync.remove_task_note("a", self.dir)
        obsidian_sync.remove_task_note("b", self.dir)   # no refs to 1234 left
        export.sync_story_hubs(self.dir, enabled=True)
        self.assertFalse(os.path.exists(os.path.join(self._story_dir(), "1234.md")))

    def test_toggle_off_prunes_all_hubs(self):
        self._seed_two_ref_story()
        export.sync_story_hubs(self.dir, enabled=True)
        export.sync_story_hubs(self.dir, enabled=False)
        self.assertFalse(os.path.isdir(self._story_dir()))

    def test_user_file_in_stories_dir_survives(self):
        self._seed_two_ref_story()
        export.sync_story_hubs(self.dir, enabled=True)
        user = os.path.join(self._story_dir(), "my-notes.md")
        with open(user, "w") as f:
            f.write("# my own file, not managed\n")
        # dissolve the group; the user's file keeps the dir alive
        obsidian_sync.remove_task_note("a", self.dir)
        obsidian_sync.remove_task_note("b", self.dir)
        export.sync_story_hubs(self.dir, enabled=True)
        self.assertTrue(os.path.exists(user))
        self.assertFalse(os.path.exists(os.path.join(self._story_dir(), "1234.md")))


class ExportTasksWiring(_Base):
    def test_export_tasks_maintains_story_hubs_only_when_enabled(self):
        tasks = [_task("a", 1, "Wire the API", "green", [{"url": self._URL}]),
                 _task("b", 2, "Fix the parser", "red", [{"url": "1234"}])]
        export.export_tasks(self.dir, tasks, store=None)   # default off
        self.assertFalse(os.path.isdir(self._story_dir()))
        export.export_tasks(self.dir, tasks, store=None, story_groups=True)
        self.assertTrue(os.path.exists(os.path.join(self._story_dir(), "1234.md")))
        export.export_tasks(self.dir, tasks, store=None, story_groups=False)
        self.assertFalse(os.path.isdir(self._story_dir()))


if __name__ == "__main__":
    unittest.main()
