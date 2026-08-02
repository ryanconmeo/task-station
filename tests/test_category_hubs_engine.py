# tests/test_category_hubs_engine.py
"""WS10 category hubs — end-to-end through the engine: the generic `export --dir`
command and the Obsidian vault `--sync-all`, plus the per-note category link, the
toggle, owner scoping, and prune. Mirrors the in-process harness the other CLI
tests use (temp-home isolation, _Args + redirect_stdout)."""
import importlib, importlib.util, io, json, os, shutil, sys, tempfile, unittest
from contextlib import redirect_stdout

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

import store        # noqa: E402
import config       # noqa: E402

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


class _ExpArgs:
    def __init__(self, **kw):
        d = dict(dir=None, task=None, all=False, status=None, include=None,
                 since=None, prune=False)
        d.update(kw)
        self.__dict__.update(d)


class _ObsArgs:
    def __init__(self, **kw):
        d = dict(status=False, sync_all=False, flush=False, quiet=False)
        d.update(kw)
        self.__dict__.update(d)


class _Base(unittest.TestCase):
    def setUp(self):
        for v in ("TASK_STATION_OWNER", "TASK_STATION_OBSIDIAN_CATEGORY_HUBS",
                  "TASK_STATION_KNOWLEDGE_GRAPH"):
            os.environ.pop(v, None)
        self.tmp = tempfile.mkdtemp(prefix="cat-hub-eng-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.PROJECTS_ROOT = os.path.join(self.tmp, "projects")
        ts.DELEGATE_REGISTRY = os.path.join(self.tmp, "workers.json")
        self.out = os.path.join(self.tmp, "brain")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)
        import categories
        importlib.reload(categories)   # shipped taxonomy, not the dev's config overrides
        store.reset_cache()

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)
        import categories
        importlib.reload(categories)

    def _seed(self, title, color, status="open"):
        t = ts.new_task(title, "summary", color=color)
        if status != "open":
            t["status"] = status
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _export(self, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_export(_ExpArgs(dir=self.out, **kw))
        return buf.getvalue()

    def _read(self, path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def _note(self, base, stem):
        return self._read(os.path.join(base, stem + ".md"))

    def _hubs(self, base):
        d = os.path.join(base, "categories")
        return sorted(os.listdir(d)) if os.path.isdir(d) else []


class GenericExport(_Base):
    def test_note_carries_category_link_and_hub_exists(self):
        a = self._seed("Fix login", "red")
        self._export(all=True, include="usage")
        note = self._note(self.out, "%s-fix-login" % a["seq"])
        self.assertIn("## Related", note)
        self.assertIn("[[categories/bug|BUG]]", note)
        self.assertEqual(self._hubs(self.out), ["bug.md"])
        hub = self._read(os.path.join(self.out, "categories", "bug.md"))
        self.assertIn("[[%s-fix-login|Fix login]]" % a["seq"], hub)

    def test_uncategorised_links_general(self):
        a = self._seed("No colour", None)
        self._export(all=True, include="usage")
        note = self._note(self.out, "%s-no-colour" % a["seq"])
        self.assertIn("[[categories/general|GENERAL]]", note)
        self.assertEqual(self._hubs(self.out), ["general.md"])

    def test_toggle_off_no_link_no_hubs(self):
        config.set("obsidian_category_hubs", False)
        a = self._seed("Fix login", "red")
        self._export(all=True, include="usage")
        note = self._note(self.out, "%s-fix-login" % a["seq"])
        self.assertNotIn("[[categories/", note)
        self.assertEqual(self._hubs(self.out), [])

    def test_toggle_off_prunes_existing_hubs(self):
        a = self._seed("Fix login", "red")
        self._export(all=True, include="usage")
        self.assertEqual(self._hubs(self.out), ["bug.md"])
        config.set("obsidian_category_hubs", False)
        self._export(all=True, include="usage")
        self.assertEqual(self._hubs(self.out), [])

    def test_prune_removes_emptied_category_hub(self):
        a = self._seed("Fix login", "red")
        b = self._seed("Add export", "green")
        self._export(all=True, include="usage")
        self.assertEqual(self._hubs(self.out), ["bug.md", "feature.md"])
        # hard-delete the feature task's store row, then prune the export dir
        ts.delete_task(b["id"])
        store.reset_cache()
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_export(_ExpArgs(dir=self.out, prune=True))
        self.assertEqual(self._hubs(self.out), ["bug.md"])   # feature hub pruned


class VaultSyncAll(_Base):
    def _sync_all(self):
        config.set("obsidian_vault", self.vault)
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_obsidian(_ObsArgs(sync_all=True))
        return buf.getvalue()

    def _plugin(self, owner=None):
        base = os.path.join(self.vault, "task-station")
        return os.path.join(base, owner) if owner else base

    def test_sync_all_writes_hubs_and_links(self):
        a = self._seed("Fix login", "red")
        self._sync_all()
        pdir = self._plugin()
        note = self._note(pdir, "%s-fix-login" % a["seq"])
        self.assertIn("[[categories/bug|BUG]]", note)
        self.assertEqual(self._hubs(pdir), ["bug.md"])

    def test_two_owners_keep_hubs_under_each_subtree(self):
        # Two owners = two machines/configs sharing ONE vault. Model that by syncing
        # alice's store, then swapping the store contents for bob's before his sync.
        config.set("owner", "alice")
        a = self._seed("Alice bug", "red")
        self._sync_all()
        ts.delete_task(a["id"])   # alice's machine gone
        store.reset_cache()
        config.set("owner", "bob")
        b = self._seed("Bob feature", "green")
        self._sync_all()
        alice_dir, bob_dir = self._plugin("alice"), self._plugin("bob")
        self.assertEqual(self._hubs(alice_dir), ["bug.md"])
        self.assertEqual(self._hubs(bob_dir), ["feature.md"])
        # each owner's note links its OWN owner-scoped hub
        anote = self._note(alice_dir, "%s-alice-bug" % a["seq"])
        self.assertIn("[[alice/categories/bug|BUG]]", anote)
        bnote = self._note(bob_dir, "%s-bob-feature" % b["seq"])
        self.assertIn("[[bob/categories/feature|FEATURE]]", bnote)


if __name__ == "__main__":
    unittest.main()
