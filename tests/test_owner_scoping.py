"""A-5 — owner stamp + owner-scoped layout for shared vaults.

  * config.owner() get/set + env override; unset ⇒ single-owner (byte-identical);
  * export_task / generic export nest notes under <target>/<owner>/ + stamp `owner`;
  * daily-note lines carry the owner prefix + a path-qualified link (no collisions);
  * the stream manifest + event actor carry the owner;
  * `obsidian --sync-all` relocates existing flat notes into the owner subtree with
    no orphans left behind;
  * a simulated two-owner shared vault has zero filename / daily-note collisions.
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

import store          # noqa: E402
import config         # noqa: E402
import stream         # noqa: E402
import obsidian_sync  # noqa: E402

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


def _task(**over):
    t = {"id": "abcdef1234", "uuid": "abcdef1234", "seq": 12, "title": "Fix login",
         "status": "open", "color": "red", "effort": "m",
         "created_ts": 1_700_000_000.0, "updated_ts": 1_700_100_000.0}
    t.update(over)
    return t


class ConfigOwner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        os.environ.pop("TASK_STATION_OWNER", None)

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        os.environ.pop("TASK_STATION_OWNER", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_unset(self):
        self.assertEqual(config.owner(), "")

    def test_set_and_env_override(self):
        config.set("owner", "alice")
        self.assertEqual(config.owner(), "alice")
        os.environ["TASK_STATION_OWNER"] = "bob"
        self.assertEqual(config.owner(), "bob")   # env wins


class RenderAndExportScoping(unittest.TestCase):
    def setUp(self):
        self.vault = tempfile.mkdtemp(prefix="owner-vault-")

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)

    def test_owner_unset_byte_identical(self):
        a = obsidian_sync.render_note(_task())
        b = obsidian_sync.render_note(_task(), owner=None)
        self.assertEqual(a, b)
        self.assertNotIn("owner:", a)

    def test_owner_frontmatter_and_subfolder(self):
        fname = obsidian_sync.export_task(_task(), self.vault, owner="alice")
        pdir = obsidian_sync.plugin_dir(self.vault)
        # note lives under the owner subfolder, not the flat plugin dir
        self.assertFalse(os.path.exists(os.path.join(pdir, fname)))
        self.assertTrue(os.path.exists(os.path.join(pdir, "alice", fname)))
        with open(os.path.join(pdir, "alice", fname), encoding="utf-8") as f:
            text = f.read()
        self.assertIn('owner: "alice"', text)

    def test_daily_note_owner_prefix_and_no_collision(self):
        when = datetime(2026, 7, 3, 14, 30)
        heading = "## Claude sessions"
        # two owners, SAME minute/stem/event/title → distinct lines, both recorded
        obsidian_sync.append_daily_note(self.vault, "12-fix", "closed", "Fix", heading,
                                        when=when, owner="alice")
        obsidian_sync.append_daily_note(self.vault, "12-fix", "closed", "Fix", heading,
                                        when=when, owner="bob")
        with open(os.path.join(self.vault, "2026-07-03.md"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("- 14:30 · alice · [[alice/12-fix]] — closed: Fix", text)
        self.assertIn("- 14:30 · bob · [[bob/12-fix]] — closed: Fix", text)
        # idempotent per owner (same line again ⇒ no dup)
        obsidian_sync.append_daily_note(self.vault, "12-fix", "closed", "Fix", heading,
                                        when=when, owner="alice")
        with open(os.path.join(self.vault, "2026-07-03.md"), encoding="utf-8") as f:
            self.assertEqual(f.read().count("alice · [[alice/12-fix]]"), 1)


class _EngineBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="owner-eng-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        for k in ("TASK_STATION_OWNER", "TASK_STATION_STREAM", "TASK_STATION_STREAM_DIR"):
            os.environ.pop(k, None)
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.PROJECTS_ROOT = os.path.join(self.tmp, "projects")
        ts.DELEGATE_REGISTRY = os.path.join(self.tmp, "workers.json")
        store.reset_cache()

    def tearDown(self):
        store.reset_cache()
        for k in ("TASK_STATION_HOME", "TASK_STATION_OWNER"):
            os.environ.pop(k, None)
        config.unset("owner")
        config.unset("obsidian_vault")
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self, title):
        t = ts.new_task(title, "summary of " + title)
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    class Args:
        def __init__(self, **kw):
            d = dict(sync_all=False, flush=False, quiet=False, title=None, summary="",
                     color=None, effort=None, goal=None, step=None, session=None,
                     no_attach=True, attach=False, active=False, force=True)
            d.update(kw)
            self.__dict__.update(d)


class StreamOwner(_EngineBase):
    def test_manifest_and_actor_carry_owner(self):
        config.set("owner", "alice")
        with redirect_stdout(io.StringIO()):
            ts.cmd_create(self.Args(title="streamed"))
        with open(stream.manifest_path(), encoding="utf-8") as f:
            m = json.loads(f.read())
        self.assertEqual(m.get("owner"), "alice")
        created = [e for e in stream.read_events() if e["event"] == "task.created"]
        self.assertTrue(created)
        self.assertEqual(created[0]["actor"].get("owner"), "alice")

    def test_owner_unset_actor_has_no_owner_key(self):
        with redirect_stdout(io.StringIO()):
            ts.cmd_create(self.Args(title="plain"))
        created = [e for e in stream.read_events() if e["event"] == "task.created"]
        self.assertEqual(created[0]["actor"], {"session": None})   # byte-identical


class SyncAllMigration(_EngineBase):
    def test_migration_relocates_and_leaves_no_orphans(self):
        vault = os.path.join(self.tmp, "vault")
        os.makedirs(vault)
        config.set("obsidian_vault", vault)
        a = self._seed("Alpha")
        b = self._seed("Beta")
        pdir = obsidian_sync.plugin_dir(vault)
        # first sync WITHOUT an owner → flat notes
        with redirect_stdout(io.StringIO()):
            ts.cmd_obsidian(self.Args(sync_all=True))
        flat = sorted(f for f in os.listdir(pdir) if f.endswith(".md"))
        self.assertEqual(len(flat), 2)
        # now set an owner and re-sync → notes relocate under the owner subtree
        config.set("owner", "alice")
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_obsidian(self.Args(sync_all=True))
        self.assertIn("relocated 2 existing note", buf.getvalue())
        odir = os.path.join(pdir, "alice")
        owned = sorted(f for f in os.listdir(odir) if f.endswith(".md"))
        self.assertEqual(len(owned), 2)
        # NO orphans: the flat location holds no managed notes + no sidecar index
        self.assertEqual([f for f in os.listdir(pdir) if f.endswith(".md")], [])
        self.assertFalse(os.path.exists(os.path.join(pdir, obsidian_sync._INDEX_NAME)))
        # each task has EXACTLY one note (relocated, not duplicated)
        for t in (a, b):
            stem = "%s-%s" % (t["seq"], obsidian_sync.slugify(t["title"]))
            self.assertTrue(os.path.exists(os.path.join(odir, stem + ".md")))


class TwoOwnerSharedVault(_EngineBase):
    def test_zero_filename_and_daily_collisions(self):
        # a single shared vault; two owners each export a task that lands on the SAME
        # seq/slug filename — they must not collide (own subtrees).
        vault = os.path.join(self.tmp, "shared")
        os.makedirs(vault)
        config.set("obsidian_vault", vault)
        self._seed("Same Title")   # seq 1 for both owners' stores would collide flat
        pdir = obsidian_sync.plugin_dir(vault)
        for handle in ("alice", "bob"):
            config.set("owner", handle)
            with redirect_stdout(io.StringIO()):
                ts.cmd_obsidian(self.Args(sync_all=True))
        # each owner's note lives in its own subtree — same filename, no clobber
        self.assertTrue(os.path.exists(os.path.join(pdir, "alice", "1-same-title.md")))
        self.assertTrue(os.path.exists(os.path.join(pdir, "bob", "1-same-title.md")))
        with open(os.path.join(pdir, "alice", "1-same-title.md"), encoding="utf-8") as f:
            self.assertIn('owner: "alice"', f.read())
        with open(os.path.join(pdir, "bob", "1-same-title.md"), encoding="utf-8") as f:
            self.assertIn('owner: "bob"', f.read())


if __name__ == "__main__":
    unittest.main()
