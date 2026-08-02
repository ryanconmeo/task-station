# tests/test_category_hubs_export.py
"""WS10 category-hub pages — the export.sync_category_hubs core that maintains
<dir>/categories/<slug>.md from the sidecar index, one hub per non-empty category,
fully regenerated each call (like index.md). Notes are written with obsidian_sync
(the real sidecar + render_note), so the hubs read live-shaped data."""
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
        self.tmp = tempfile.mkdtemp(prefix="cat-hub-exp-")
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

    def _hub(self, slug):
        with open(os.path.join(self._cat_dir(), slug + ".md"), encoding="utf-8") as f:
            return f.read()

    def _hubs(self):
        d = self._cat_dir()
        return sorted(f for f in os.listdir(d)) if os.path.isdir(d) else []


class Generate(_Base):
    def test_one_hub_per_category_with_its_tasks(self):
        self._write(_task("a", 1, "Fix login", "red"))
        self._write(_task("b", 2, "Add export", "green"))
        self._write(_task("c", 3, "Another bug", "red"))
        written, removed = export.sync_category_hubs(self.dir)
        self.assertEqual(self._hubs(), ["bug.md", "feature.md"])
        bug = self._hub("bug")
        # frontmatter: managed-by + kind + category key + label + description
        self.assertIn("managed-by: task-station", bug)
        self.assertIn("kind: category-hub", bug)
        self.assertIn("category: red", bug)
        self.assertIn("label: ", bug)
        # both bug tasks listed with resolvable stems, feature task NOT in bug hub
        self.assertIn("[[1-fix-login|Fix login]]", bug)
        self.assertIn("[[3-another-bug|Another bug]]", bug)
        self.assertNotIn("Add export", bug)
        feat = self._hub("feature")
        self.assertIn("[[2-add-export|Add export]]", feat)

    def test_uncategorised_lands_in_general_hub(self):
        self._write(_task("a", 1, "No colour", None))
        export.sync_category_hubs(self.dir)
        self.assertEqual(self._hubs(), ["general.md"])
        self.assertIn("[[1-no-colour|No colour]]", self._hub("general"))

    def test_open_active_before_closed_then_by_seq(self):
        self._write(_task("a", 3, "Closed one", "red", status="closed"))
        self._write(_task("b", 1, "Open one", "red", status="open"))
        self._write(_task("c", 2, "Active one", "red", status="active"))
        export.sync_category_hubs(self.dir)
        bug = self._hub("bug")
        i_open = bug.index("Open one")
        i_active = bug.index("Active one")
        i_closed = bug.index("Closed one")
        self.assertLess(i_open, i_closed)
        self.assertLess(i_active, i_closed)
        self.assertLess(i_open, i_active)   # within open+active, seq order

    def test_regenerated_fully_each_call_prunes_empty_category(self):
        self._write(_task("a", 1, "Fix login", "red"))
        self._write(_task("b", 2, "Add export", "green"))
        export.sync_category_hubs(self.dir)
        self.assertEqual(self._hubs(), ["bug.md", "feature.md"])
        # recolour b to red and drop it from feature: rewrite the note + remove old
        # entry by regenerating from a dir where feature now has no notes.
        obsidian_sync.remove_task_note("b", self.dir)
        export.sync_category_hubs(self.dir)
        self.assertEqual(self._hubs(), ["bug.md"])   # feature hub pruned (zero tasks)


class ToggleOff(_Base):
    def test_disabled_removes_all_managed_hubs(self):
        self._write(_task("a", 1, "Fix login", "red"))
        export.sync_category_hubs(self.dir)
        self.assertEqual(self._hubs(), ["bug.md"])
        written, removed = export.sync_category_hubs(self.dir, enabled=False)
        self.assertIn("bug.md", removed)
        self.assertEqual(self._hubs(), [])   # dir emptied / gone

    def test_user_file_in_categories_dir_survives_prune(self):
        self._write(_task("a", 1, "Fix login", "red"))
        export.sync_category_hubs(self.dir)
        user = os.path.join(self._cat_dir(), "my-notes.md")
        with open(user, "w") as f:
            f.write("# my own file, not managed\n")
        export.sync_category_hubs(self.dir, enabled=False)
        self.assertTrue(os.path.exists(user))   # unmanaged file untouched


class ExportTasksIntegration(_Base):
    def test_export_tasks_maintains_hubs_only_when_enabled(self):
        tasks = [_task("a", 1, "Fix login", "red"),
                 _task("b", 2, "Add export", "green")]
        # default OFF ⇒ no categories/ dir (byte-parity for direct callers)
        export.export_tasks(self.dir, tasks, store=None)
        self.assertFalse(os.path.isdir(self._cat_dir()))
        # enabled ⇒ hubs generated
        export.export_tasks(self.dir, tasks, store=None, category_hubs=True)
        self.assertEqual(self._hubs(), ["bug.md", "feature.md"])
        # toggling back off prunes them
        export.export_tasks(self.dir, tasks, store=None, category_hubs=False)
        self.assertEqual(self._hubs(), [])


if __name__ == "__main__":
    unittest.main()
