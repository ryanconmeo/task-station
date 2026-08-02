"""lib/brains.py — the Interbrain "brains & sharing" config (brains.json).

Ported out of the retired preview engine's test module (task #444): these cover
`brains.py` and its `task-station brains` CLI, neither of which went anywhere — only the
preview shell did. The feed-side resolution (brain/shares landing on a task's
view-model) is covered in tests/test_feeds.py; the retirement record is
docs/specs/BOARD-RETIREMENT.md.

brains.json is ADDITIVE: these ops must NEVER touch tasks.db.
"""
import hashlib
import importlib.util
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "lib")
TOOLS = os.path.join(ROOT, "tools")
for p in (LIB, TOOLS):
    if p not in sys.path:
        sys.path.insert(0, p)

import store  # noqa: E402

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


class _NS:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="brains-tests-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.DELEGATE_REGISTRY = os.path.join(self.tmp, "workers.json")
        store.reset_cache()

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        store.reset_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self, title, color="green", effort="m"):
        t = ts.new_task(title, "summary for " + title, color=color, effort=effort)
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _db_hash(self):
        with open(os.path.join(ts.STORE, "tasks.db"), "rb") as f:
            return hashlib.sha1(f.read()).hexdigest()


class BrainsModuleTest(_Base):
    def test_defaults_and_ops(self):
        import brains
        cfg = brains.load(self.tmp)
        self.assertIn("main", cfg["brains"])
        self.assertTrue(brains.add(cfg, "work"))
        self.assertFalse(brains.add(cfg, "work"))            # dup no-op
        self.assertTrue(brains.rename(cfg, "work", "research"))
        self.assertIn("research", cfg["brains"])
        self.assertNotIn("work", cfg["brains"])
        self.assertTrue(brains.share(cfg, "research", "kosei"))
        self.assertFalse(brains.share(cfg, "research", "kosei"))   # dup rule
        self.assertTrue(brains.unshare(cfg, "research", "kosei"))
        self.assertTrue(brains.archive(cfg, "research"))
        self.assertTrue(cfg["brains"]["research"]["archived"])

    def test_assign_and_resolve(self):
        import brains
        cfg = brains.load(self.tmp)
        brains.add(cfg, "work")
        brains.assign(cfg, "uuidX", "work")
        self.assertEqual(brains.brain_for(cfg, "uuidX"), "work")
        self.assertEqual(brains.brain_for(cfg, "unknown"), "main")
        brains.share(cfg, "work", "jpark")
        self.assertEqual(brains.shares_for(cfg, "uuidX"), ["jpark"])

    def test_tag_scoped_resolution(self):
        import brains
        cfg = brains.load(self.tmp)
        brains.add(cfg, "work")
        brains.assign(cfg, "u", "work")
        brains.share(cfg, "work", "org", tag="INFRA")
        self.assertEqual(brains.shares_for(cfg, "u", "INFRA"), ["org"])   # tag match
        self.assertEqual(brains.shares_for(cfg, "u", "FEATURE"), [])      # tag mismatch

    def test_rename_reassigns_and_persists(self):
        import brains
        cfg = brains.load(self.tmp)
        brains.add(cfg, "work")
        brains.assign(cfg, "u", "work")
        brains.rename(cfg, "work", "research")
        self.assertEqual(brains.brain_for(cfg, "u"), "research")
        brains.save(cfg, self.tmp)
        self.assertIn("research", brains.load(self.tmp)["brains"])


class BrainsCLITest(_Base):
    def test_cli_add_share_assign_persist(self):
        import brains
        t = self._seed("Some task")
        ts.cmd_brains(_NS(action="add", args=["work"], with_=None, tag=None))
        ts.cmd_brains(_NS(action="share", args=["work"], with_="jpark", tag=None))
        ts.cmd_brains(_NS(action="assign", args=[str(t["seq"]), "work"],
                          with_=None, tag=None))
        cfg = brains.load(self.tmp)
        self.assertIn("work", cfg["brains"])
        self.assertEqual(cfg["brains"]["work"]["shares"], [{"with": "jpark", "tag": None}])
        self.assertEqual(cfg["assign"].get(t["id"]), "work")

    def test_brains_cli_never_touches_store(self):
        self._seed("x")                                      # create the db
        before = self._db_hash()
        ts.cmd_brains(_NS(action="add", args=["work"], with_=None, tag=None))
        ts.cmd_brains(_NS(action="share", args=["work"], with_="org", tag="INFRA"))
        self.assertEqual(before, self._db_hash(), "brains ops must not touch tasks.db")
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "brains.json")))


if __name__ == "__main__":
    unittest.main()
