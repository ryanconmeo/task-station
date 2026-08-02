# tests/test_category_subgroups_export.py
"""WS11 sub-hub pages — the export.sync_category_hubs subgroups path. Within a
category, distinctive recurring tokens cluster into sub-hub pages at
<dir>/categories/<cat-slug>/<token>.md; the parent hub gains a ### Groups section
and drops the grouped members from its own list (they live under the sub-hub). A
group below 3 members dissolves; two syncs are byte-identical; user files survive."""
import importlib, os, shutil, sys, tempfile, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

import categories       # noqa: E402
import obsidian_sync    # noqa: E402
import export           # noqa: E402


def _task(tid, seq, title, color, status="open"):
    return {"id": tid, "uuid": "uuid-" + tid, "seq": seq, "title": title,
            "color": color, "status": status}


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cat-subg-exp-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        importlib.reload(categories)
        self.dir = os.path.join(self.tmp, "brain")
        os.makedirs(self.dir)

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)
        importlib.reload(categories)

    def _write(self, task):
        fname, idx = obsidian_sync.note_filename(task, self.dir)
        obsidian_sync._save_index(self.dir, idx)
        obsidian_sync._atomic_write(os.path.join(self.dir, fname),
                                    obsidian_sync.render_note(task))
        return fname

    def _cat_dir(self):
        return os.path.join(self.dir, "categories")

    def _read(self, *parts):
        with open(os.path.join(self._cat_dir(), *parts), encoding="utf-8") as f:
            return f.read()

    def _seed_hammerspoon(self):
        # 4 hammerspoon personal tasks + 2 unrelated personal tasks.
        self._write(_task("a", 1, "Hammerspoon window tiling", "pink"))
        self._write(_task("b", 2, "Hammerspoon reload config", "pink"))
        self._write(_task("c", 3, "Hammerspoon spoon install", "pink"))
        self._write(_task("d", 4, "Hammerspoon menubar clock", "pink"))
        self._write(_task("e", 5, "Buy groceries weekly", "pink"))
        self._write(_task("f", 6, "Plan summer vacation", "pink"))


class SubHubGenerate(_Base):
    def test_one_subhub_with_four_members(self):
        self._seed_hammerspoon()
        export.sync_category_hubs(self.dir, subgroups=True)
        # sub-hub lives nested under the category slug
        sub = self._read("personal", "hammerspoon.md")
        self.assertIn("managed-by: task-station", sub)
        self.assertIn("kind: category-subhub", sub)
        self.assertIn("category: pink", sub)
        self.assertIn("parent: personal", sub)
        self.assertIn("token: hammerspoon", sub)
        self.assertIn("count: 4", sub)
        # title-cased heading + all four members + the up-link to the category
        self.assertIn("# Hammerspoon", sub)
        for seq, slug in [(1, "hammerspoon-window-tiling"), (2, "hammerspoon-reload-config"),
                          (3, "hammerspoon-spoon-install"), (4, "hammerspoon-menubar-clock")]:
            self.assertIn("[[%d-%s|" % (seq, slug), sub)
        self.assertIn("[[categories/personal|PERSONAL]]", sub)

    def test_parent_hub_has_groups_section_and_drops_members(self):
        self._seed_hammerspoon()
        export.sync_category_hubs(self.dir, subgroups=True)
        hub = self._read("personal.md")
        # ### Groups links the sub-hub with a member count
        self.assertIn("### Groups", hub)
        self.assertIn("[[categories/personal/hammerspoon|", hub)
        self.assertIn("4", hub)   # member count shown
        # grouped members are NOT in the parent hub's own list; non-members are
        self.assertNotIn("Hammerspoon window tiling", hub)
        self.assertIn("Buy groceries weekly", hub)
        self.assertIn("Plan summer vacation", hub)

    def test_dissolves_below_threshold(self):
        self._seed_hammerspoon()
        export.sync_category_hubs(self.dir, subgroups=True)
        self.assertTrue(os.path.exists(os.path.join(self._cat_dir(), "personal", "hammerspoon.md")))
        # drop two hammerspoon notes -> only 2 remain -> group dissolves
        obsidian_sync.remove_task_note("c", self.dir)
        obsidian_sync.remove_task_note("d", self.dir)
        export.sync_category_hubs(self.dir, subgroups=True)
        self.assertFalse(os.path.isdir(os.path.join(self._cat_dir(), "personal")))
        # members fall back into the parent hub's own list
        hub = self._read("personal.md")
        self.assertNotIn("### Groups", hub)
        self.assertIn("Hammerspoon window tiling", hub)

    def test_two_syncs_byte_identical(self):
        self._seed_hammerspoon()
        export.sync_category_hubs(self.dir, subgroups=True)
        first_sub = self._read("personal", "hammerspoon.md")
        first_hub = self._read("personal.md")
        export.sync_category_hubs(self.dir, subgroups=True)
        self.assertEqual(self._read("personal", "hammerspoon.md"), first_sub)
        self.assertEqual(self._read("personal.md"), first_hub)


class SubgroupsToggle(_Base):
    def test_off_prunes_subhubs_but_keeps_category_hub(self):
        self._seed_hammerspoon()
        export.sync_category_hubs(self.dir, subgroups=True)
        self.assertTrue(os.path.isdir(os.path.join(self._cat_dir(), "personal")))
        # subgroups off (hubs still on) -> sub-hub pruned, category hub stays
        export.sync_category_hubs(self.dir, subgroups=False)
        self.assertFalse(os.path.isdir(os.path.join(self._cat_dir(), "personal")))
        self.assertTrue(os.path.exists(os.path.join(self._cat_dir(), "personal.md")))
        hub = self._read("personal.md")
        self.assertNotIn("### Groups", hub)
        self.assertIn("Hammerspoon window tiling", hub)   # members back in the list

    def test_hubs_off_prunes_everything(self):
        self._seed_hammerspoon()
        export.sync_category_hubs(self.dir, subgroups=True)
        export.sync_category_hubs(self.dir, enabled=False, subgroups=True)
        self.assertFalse(os.path.isdir(self._cat_dir()))

    def test_user_file_in_subgroup_dir_survives(self):
        self._seed_hammerspoon()
        export.sync_category_hubs(self.dir, subgroups=True)
        subdir = os.path.join(self._cat_dir(), "personal")
        user = os.path.join(subdir, "my-notes.md")
        with open(user, "w") as f:
            f.write("# my own file, not managed\n")
        # dissolve the group; the user's file keeps its dir alive
        obsidian_sync.remove_task_note("c", self.dir)
        obsidian_sync.remove_task_note("d", self.dir)
        export.sync_category_hubs(self.dir, subgroups=True)
        self.assertTrue(os.path.exists(user))
        self.assertFalse(os.path.exists(os.path.join(subdir, "hammerspoon.md")))


if __name__ == "__main__":
    unittest.main()
