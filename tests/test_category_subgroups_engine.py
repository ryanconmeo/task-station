# tests/test_category_subgroups_engine.py
"""WS11 emergent sub-groups — end-to-end through the engine (vault --sync-all +
generic export). Verifies the MOST-SPECIFIC per-note link (a sub-group member links
its sub-hub instead of the bare category; non-members keep the category link), the
nested sub-hub page, dissolution + toggle-off reversion, owner-scoped nesting, and
byte-level determinism across two syncs."""
import importlib, importlib.util, io, os, shutil, sys, tempfile, unittest
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
                  "TASK_STATION_OBSIDIAN_SUBGROUPS", "TASK_STATION_KNOWLEDGE_GRAPH"):
            os.environ.pop(v, None)
        self.tmp = tempfile.mkdtemp(prefix="cat-subg-eng-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.PROJECTS_ROOT = os.path.join(self.tmp, "projects")
        ts.DELEGATE_REGISTRY = os.path.join(self.tmp, "workers.json")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)
        import categories
        importlib.reload(categories)
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

    def _sync_all(self):
        config.set("obsidian_vault", self.vault)
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_obsidian(_ObsArgs(sync_all=True))
        return buf.getvalue()

    def _plugin(self, owner=None):
        import obsidian_sync
        base = os.path.join(self.vault, "task-station")
        return os.path.join(base, owner) if owner else base

    def _read(self, base, *parts):
        with open(os.path.join(base, *parts), encoding="utf-8") as f:
            return f.read()

    def _note(self, base, stem):
        return self._read(base, stem + ".md")

    def _seed_hammerspoon(self):
        return [
            self._seed("Hammerspoon window tiling", "pink"),
            self._seed("Hammerspoon reload config", "pink"),
            self._seed("Hammerspoon spoon install", "pink"),
            self._seed("Hammerspoon menubar clock", "pink"),
            self._seed("Buy groceries weekly", "pink"),
            self._seed("Plan summer vacation", "pink"),
        ]


class MostSpecificLink(_Base):
    def test_members_link_subhub_others_link_category(self):
        tasks = self._seed_hammerspoon()
        self._sync_all()
        pdir = self._plugin()
        # a member links the sub-hub, NOT the bare category
        m = self._note(pdir, "%s-hammerspoon-window-tiling" % tasks[0]["seq"])
        self.assertIn("[[categories/personal/hammerspoon|HAMMERSPOON]]", m)
        self.assertNotIn("[[categories/personal|PERSONAL]]", m)
        # a non-member keeps the plain category link
        o = self._note(pdir, "%s-buy-groceries-weekly" % tasks[4]["seq"])
        self.assertIn("[[categories/personal|PERSONAL]]", o)
        self.assertNotIn("hammerspoon", o)
        # the nested sub-hub page exists with all four members + up-link
        sub = self._read(pdir, "categories", "personal", "hammerspoon.md")
        self.assertIn("count: 4", sub)
        self.assertIn("[[categories/personal|PERSONAL]]", sub)
        # parent hub gained a Groups section
        self.assertIn("### Groups", self._read(pdir, "categories", "personal.md"))

    def test_generic_token_across_categories_stays_category(self):
        # 'update' is a stopword -> never groups; notes keep the category link.
        a = self._seed("Update the login page", "green")
        self._seed("Update the billing flow", "green")
        self._seed("Update the search index", "green")
        self._sync_all()
        pdir = self._plugin()
        note = self._note(pdir, "%s-update-the-login-page" % a["seq"])
        self.assertIn("[[categories/feature|FEATURE]]", note)
        self.assertFalse(os.path.isdir(os.path.join(pdir, "categories", "feature")))

    def test_dissolution_reverts_members_to_category(self):
        tasks = self._seed_hammerspoon()
        self._sync_all()
        pdir = self._plugin()
        self.assertTrue(os.path.exists(os.path.join(pdir, "categories", "personal", "hammerspoon.md")))
        # auto-maintenance: retitle two members so the token drops below 3 on the next
        # sync -> the group dissolves and its remaining members revert to the category.
        for t in (tasks[2], tasks[3]):
            cur = ts.load_task(t["id"])
            cur["title"] = "Unrelated errand number %s" % cur["seq"]
            ts.save_task(cur)
        store.reset_cache()
        self._sync_all()
        self.assertFalse(os.path.isdir(os.path.join(pdir, "categories", "personal")))
        m = self._note(pdir, "%s-hammerspoon-window-tiling" % tasks[0]["seq"])
        self.assertIn("[[categories/personal|PERSONAL]]", m)   # reverted to category

    def test_toggle_off_reverts_and_prunes(self):
        tasks = self._seed_hammerspoon()
        self._sync_all()
        pdir = self._plugin()
        config.set("obsidian_subgroups", False)
        self._sync_all()
        # sub-hubs pruned, category hub stays, members revert to the category link
        self.assertFalse(os.path.isdir(os.path.join(pdir, "categories", "personal")))
        self.assertTrue(os.path.exists(os.path.join(pdir, "categories", "personal.md")))
        m = self._note(pdir, "%s-hammerspoon-window-tiling" % tasks[0]["seq"])
        self.assertIn("[[categories/personal|PERSONAL]]", m)

    def test_deterministic_two_syncs_byte_identical(self):
        tasks = self._seed_hammerspoon()
        self._sync_all()
        pdir = self._plugin()
        member = "%s-hammerspoon-window-tiling.md" % tasks[0]["seq"]
        first_note = self._read(pdir, member)
        first_sub = self._read(pdir, "categories", "personal", "hammerspoon.md")
        first_hub = self._read(pdir, "categories", "personal.md")
        self._sync_all()
        self.assertEqual(self._read(pdir, member), first_note)
        self.assertEqual(self._read(pdir, "categories", "personal", "hammerspoon.md"), first_sub)
        self.assertEqual(self._read(pdir, "categories", "personal.md"), first_hub)


class OwnerScoped(_Base):
    def test_owner_nests_subhub_and_link(self):
        config.set("owner", "alice")
        tasks = self._seed_hammerspoon()
        self._sync_all()
        adir = self._plugin("alice")
        m = self._note(adir, "%s-hammerspoon-window-tiling" % tasks[0]["seq"])
        self.assertIn("[[alice/categories/personal/hammerspoon|HAMMERSPOON]]", m)
        self.assertTrue(os.path.exists(os.path.join(adir, "categories", "personal", "hammerspoon.md")))


if __name__ == "__main__":
    unittest.main()
