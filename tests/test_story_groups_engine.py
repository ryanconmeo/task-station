# tests/test_story_groups_engine.py
"""WS13 story hubs — end-to-end through the engine: the generic `export --dir`
command and the Obsidian vault `--sync-all`, plus the per-note [[stories/<id>]] link
(orthogonal to the category link), the toggle + nesting, owner scoping, and dissolve.
Mirrors test_category_hubs_engine.py's in-process harness."""
import importlib, importlib.util, io, os, shutil, sys, tempfile, unittest
from contextlib import redirect_stdout

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

import store        # noqa: E402
import config       # noqa: E402

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)

_URL = "https://dev.azure.com/Org/Proj/_workitems/edit/1234"


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
                  "TASK_STATION_OBSIDIAN_STORY_GROUPS", "TASK_STATION_KNOWLEDGE_GRAPH"):
            os.environ.pop(v, None)
        self.tmp = tempfile.mkdtemp(prefix="story-eng-")
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
        importlib.reload(categories)
        store.reset_cache()

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)
        import categories
        importlib.reload(categories)

    def _seed(self, title, color, stories=None, status="open"):
        t = ts.new_task(title, "summary", color=color)
        if stories is not None:
            t["stories"] = stories
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

    def _story_hubs(self, base):
        d = os.path.join(base, "stories")
        return sorted(os.listdir(d)) if os.path.isdir(d) else []


class GenericExport(_Base):
    def test_grouped_story_gets_hub_and_orthogonal_link(self):
        a = self._seed("Wire the API", "green", [{"url": _URL, "desc": "d"}])
        b = self._seed("Fix the parser", "red", [{"url": "1234"}])
        self._export(all=True, include="usage")
        # both notes gain the story link IN ADDITION to their (different) category link
        anote = self._note(self.out, "%s-wire-the-api" % a["seq"])
        self.assertIn("[[stories/1234|Story 1234]]", anote)
        self.assertIn("[[categories/feature|FEATURE]]", anote)
        bnote = self._note(self.out, "%s-fix-the-parser" % b["seq"])
        self.assertIn("[[stories/1234|Story 1234]]", bnote)
        self.assertIn("[[categories/bug|BUG]]", bnote)
        # the hub exists and carries the ADO url (from task a's full url)
        self.assertEqual(self._story_hubs(self.out), ["1234.md"])
        hub = self._read(os.path.join(self.out, "stories", "1234.md"))
        self.assertIn(_URL, hub)

    def test_single_reference_gets_link_and_hub(self):
        a = self._seed("Lonely", "green", [{"url": _URL}])
        self._export(all=True, include="usage")
        note = self._note(self.out, "%s-lonely" % a["seq"])
        self.assertIn("[[stories/1234|Story 1234]]", note)
        self.assertEqual(self._story_hubs(self.out), ["1234.md"])

    def test_toggle_off_no_link_no_hubs(self):
        config.set("obsidian_story_groups", False)
        self._seed("Wire the API", "green", [{"url": _URL}])
        self._seed("Fix the parser", "red", [{"url": _URL}])
        self._export(all=True, include="usage")
        note = self._note(self.out, "1-wire-the-api")
        self.assertNotIn("[[stories/", note)
        self.assertEqual(self._story_hubs(self.out), [])

    def test_toggle_off_prunes_existing_hubs(self):
        self._seed("Wire the API", "green", [{"url": _URL}])
        self._seed("Fix the parser", "red", [{"url": _URL}])
        self._export(all=True, include="usage")
        self.assertEqual(self._story_hubs(self.out), ["1234.md"])
        config.set("obsidian_story_groups", False)
        self._export(all=True, include="usage")
        self.assertEqual(self._story_hubs(self.out), [])

    def test_category_hubs_off_disables_story_groups(self):
        # story groups are NESTED inside category hubs — hubs off ⇒ no story hub/link
        config.set("obsidian_category_hubs", False)
        self._seed("Wire the API", "green", [{"url": _URL}])
        self._seed("Fix the parser", "red", [{"url": _URL}])
        self._export(all=True, include="usage")
        note = self._note(self.out, "1-wire-the-api")
        self.assertNotIn("[[stories/", note)
        self.assertEqual(self._story_hubs(self.out), [])

    def test_survives_drop_to_one_reference(self):
        a = self._seed("Wire the API", "green", [{"url": _URL}])
        b = self._seed("Fix the parser", "red", [{"url": _URL}])
        self._export(all=True, include="usage")
        self.assertEqual(self._story_hubs(self.out), ["1234.md"])
        ts.delete_task(b["id"])
        store.reset_cache()
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_export(_ExpArgs(dir=self.out, prune=True))
        # note b gone → one reference left → hub survives (threshold is >= 1)
        self._export(all=True, include="usage")
        self.assertEqual(self._story_hubs(self.out), ["1234.md"])

    def test_dissolve_on_zero_references(self):
        a = self._seed("Wire the API", "green", [{"url": _URL}])
        self._export(all=True, include="usage")
        self.assertEqual(self._story_hubs(self.out), ["1234.md"])
        ts.delete_task(a["id"])
        store.reset_cache()
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_export(_ExpArgs(dir=self.out, prune=True))
        # note a gone → no references left → hub dissolves
        self._export(all=True, include="usage")
        self.assertEqual(self._story_hubs(self.out), [])


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

    def test_sync_all_writes_story_hub_and_link(self):
        a = self._seed("Wire the API", "green", [{"url": _URL}])
        self._seed("Fix the parser", "red", [{"url": "1234"}])
        self._sync_all()
        pdir = self._plugin()
        note = self._note(pdir, "%s-wire-the-api" % a["seq"])
        self.assertIn("[[stories/1234|Story 1234]]", note)
        self.assertEqual(self._story_hubs(pdir), ["1234.md"])

    def test_owner_scoped_story_link(self):
        config.set("owner", "alice")
        a = self._seed("Wire the API", "green", [{"url": _URL}])
        self._seed("Fix the parser", "red", [{"url": "1234"}])
        self._sync_all()
        adir = self._plugin("alice")
        note = self._note(adir, "%s-wire-the-api" % a["seq"])
        self.assertIn("[[alice/stories/1234|Story 1234]]", note)
        self.assertEqual(self._story_hubs(adir), ["1234.md"])


if __name__ == "__main__":
    unittest.main()
