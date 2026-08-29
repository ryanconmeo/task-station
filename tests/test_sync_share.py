"""PRIVATE BY DEFAULT, ENFORCED ON WRITE.

The claim is not "private tasks are hidden from the share exchange". It is that THEY
ARE NOT IN IT — no file, nothing to hide, nothing a reader bug can expose. So the
tests here read the WRITTEN TREE rather than the flags that were supposed to govern
it: `_grep_tree` walks the exchange on disk and counts hits, which is the only way to
tell "we filtered it" apart from "we meant to".

BOTH DIRECTIONS, ALWAYS. A share filter that withheld everything would pass a
one-sided test perfectly while being completely broken, and a backup that filtered
anything would destroy the durability guarantee while looking careful. So every
privacy test asserts the private task is ABSENT from the share exchange AND PRESENT in
the backup one, in the same run.

THE ALLOW-LIST IS THE POINT OF `ProjectionTest`. A deny-list leaks every field added
after it was written; these tests pin that the share view carries the named identity
fields and NOT the owner's cost, narrative, paths or sessions — so adding a field to
the view-model cannot quietly widen what is published.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(_REPO_ROOT, "lib")
sys.path.insert(0, LIB)

_TMP_HOME = tempfile.mkdtemp(prefix="ts-share-")
os.environ["TASK_STATION_HOME"] = _TMP_HOME

import brains                                                           # noqa: E402
import station                                                          # noqa: E402
import sync                                                             # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)

CLI = os.path.join(LIB, "task-station.py")


def _grep_tree(root, needle):
    """How many files under `root` contain `needle`. Reads the artifact, not the
    intention — a filter is only proved by what is on disk."""
    hits = 0
    for base, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d != ".git"]
        for n in names:
            try:
                with open(os.path.join(base, n), encoding="utf-8", errors="ignore") as f:
                    if needle in f.read():
                        hits += 1
            except OSError:
                pass
    return hits


class DestinationTest(unittest.TestCase):
    """Backup and share are two destinations and must never collapse into one."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-share-dest-")
        self.b = os.path.join(self.tmp, "backup")
        self.s = os.path.join(self.tmp, "share")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_an_exchange_declares_its_own_kind(self):
        sync.init_root(self.b, kind=sync.KIND_BACKUP)
        sync.init_root(self.s, kind=sync.KIND_SHARE)
        self.assertEqual(sync.exchange_kind(self.b), sync.KIND_BACKUP)
        self.assertEqual(sync.exchange_kind(self.s), sync.KIND_SHARE)

    def test_an_exchange_predating_kinds_reads_as_BACKUP(self):
        """The safe default: it means the unfiltered records already in it stay put,
        rather than a share filter being applied to a repo that never had one."""
        os.makedirs(self.b)
        self.assertEqual(sync.exchange_kind(self.b), sync.KIND_BACKUP)

    def test_a_kind_is_never_rewritten_by_a_second_init(self):
        sync.init_root(self.s, kind=sync.KIND_SHARE)
        info = sync.init_root(self.s, kind=sync.KIND_BACKUP)
        self.assertEqual(info["kind"], sync.KIND_SHARE)
        self.assertEqual(sync.exchange_kind(self.s), sync.KIND_SHARE)

    def test_treating_a_share_exchange_as_backup_is_REFUSED(self):
        """The one command that would publish every private task at once."""
        sync.init_root(self.s, kind=sync.KIND_SHARE)
        with self.assertRaises(sync.DestinationMismatch):
            sync.require_kind(self.s, sync.KIND_BACKUP)

    def test_treating_a_backup_exchange_as_share_is_REFUSED(self):
        sync.init_root(self.b, kind=sync.KIND_BACKUP)
        with self.assertRaises(sync.DestinationMismatch):
            sync.require_kind(self.b, sync.KIND_SHARE)

    def test_the_two_destinations_may_not_be_the_same_directory(self):
        with self.assertRaises(sync.DestinationMismatch):
            sync.destinations(backup=self.b, share=self.b)

    def test_backup_comes_first_so_durability_precedes_visibility(self):
        got = sync.destinations(backup=self.b, share=self.s)
        self.assertEqual([d["kind"] for d in got],
                         [sync.KIND_BACKUP, sync.KIND_SHARE])

    def test_nothing_configured_means_no_destinations_at_all(self):
        self.assertEqual(sync.destinations(backup=None, share=None), [])


class AudienceTest(unittest.TestCase):
    """Who may see a task — and the answer for a fresh install is nobody."""

    def setUp(self):
        self.cfg = brains._default()
        self.task = {"id": "u1", "uuid": "u1", "title": "t"}

    def test_a_fresh_install_shares_nothing(self):
        self.assertEqual(sync.share_audience(self.task, self.cfg), [])

    def test_a_rule_on_the_tasks_brain_grants_an_audience(self):
        brains.add(self.cfg, "team")
        brains.assign(self.cfg, "u1", "team")
        brains.share(self.cfg, "team", "org")
        self.assertEqual(sync.share_audience(self.task, self.cfg), ["org"])

    def test_a_rule_on_a_DIFFERENT_brain_grants_nothing(self):
        brains.add(self.cfg, "team")
        brains.share(self.cfg, "team", "org")
        self.assertEqual(sync.share_audience(self.task, self.cfg), [])

    def test_removing_the_rule_removes_the_audience(self):
        brains.add(self.cfg, "team")
        brains.assign(self.cfg, "u1", "team")
        brains.share(self.cfg, "team", "org")
        brains.unshare(self.cfg, "team", "org")
        self.assertEqual(sync.share_audience(self.task, self.cfg), [])

    def test_another_owners_task_is_never_shared_onward(self):
        """Receiving somebody's task does not make you entitled to republish it."""
        brains.add(self.cfg, "team")
        brains.assign(self.cfg, "u1", "team")
        brains.share(self.cfg, "team", "org")
        foreign = dict(self.task, origin_owner="somebody-else")
        self.assertEqual(sync.share_audience(foreign, self.cfg), [])


class _Store(unittest.TestCase):
    """A real store plus two real exchanges, driven through the real CLI."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-share-store-")
        self.home = os.path.join(self.tmp, "home")
        self.backup = os.path.join(self.tmp, "backup")
        self.share = os.path.join(self.tmp, "share")
        os.makedirs(self.home)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cli(self, *args):
        env = dict(os.environ)
        env.update({"TASK_STATION_HOME": self.home, "CLAUDE_CONFIG_DIR": self.home,
                    "XDG_STATE_HOME": self.home, "TASK_STATION_SELF_ALIAS": "kosei",
                    "TASK_STATION_STATION": "0",
                    "TASK_STATION_SYNC_DIR": self.backup,
                    "TASK_STATION_SHARE_DIR": self.share})
        r = subprocess.run([sys.executable, CLI] + list(args), capture_output=True,
                           text=True, env=env, timeout=180)
        return (r.stdout or "") + (r.stderr or "")

    def seed(self):
        self.cli("sync", "--init")
        self.cli("sync", "--init-share")
        self.cli("create", "--title", "PRIVATEPROBE", "--no-attach",
                 "--goal", "nobody sees this")
        self.cli("create", "--title", "SHAREDPROBE", "--no-attach",
                 "--goal", "the team sees this")
        self.cli("update", "--task", "1", "--state", "PRIVATESTATE",
                 "--decision", "PRIVATEDECISION")
        self.cli("update", "--task", "2", "--state", "SHAREDSTATE",
                 "--decision", "SHAREDDECISION")

    def sync(self):
        """A share run that ACCEPTS the widening it is about to perform. Publishing is
        held pending confirmation since 3.33.0 (the sharing preview), so a test that
        expects something to become visible has to say so — which is the point."""
        return self.cli("sync", "--confirm-share")

    def open_sharing(self):
        self.cli("brains", "add", "teamwork")
        self.cli("brains", "assign", "2", "teamwork")
        self.cli("brains", "share", "teamwork", "--with", "org")


class WriteSideFilterTest(_Store):
    def test_with_no_rules_the_share_exchange_holds_NO_task_at_all(self):
        self.seed()
        self.sync()
        self.assertEqual(_grep_tree(self.share, "PRIVATEPROBE"), 0)
        self.assertEqual(_grep_tree(self.share, "SHAREDPROBE"), 0)
        # …and both are in the backup, because backup is never filtered.
        self.assertGreaterEqual(_grep_tree(self.backup, "PRIVATEPROBE"), 1)
        self.assertGreaterEqual(_grep_tree(self.backup, "SHAREDPROBE"), 1)

    def test_a_private_task_is_ABSENT_from_share_and_PRESENT_in_backup(self):
        """Both directions in one run. Either half alone can pass on broken code."""
        self.seed()
        self.open_sharing()
        self.sync()
        self.assertEqual(_grep_tree(self.share, "PRIVATEPROBE"), 0)
        self.assertGreaterEqual(_grep_tree(self.backup, "PRIVATEPROBE"), 1)
        self.assertGreaterEqual(_grep_tree(self.share, "SHAREDPROBE"), 1)

    def test_a_private_tasks_NARRATIVE_never_reaches_the_share_tree(self):
        self.seed()
        self.open_sharing()
        self.sync()
        for withheld in ("PRIVATESTATE", "PRIVATEDECISION", "nobody sees this"):
            self.assertEqual(_grep_tree(self.share, withheld), 0, withheld)

    def test_even_a_SHARED_tasks_trail_is_withheld_at_default_visibility(self):
        """Sharing a task is not sharing its trail — `trail_visibility` defaults to
        private, so the state and the decisions stay behind."""
        self.seed()
        self.open_sharing()
        self.sync()
        self.assertGreaterEqual(_grep_tree(self.share, "SHAREDPROBE"), 1)
        self.assertEqual(_grep_tree(self.share, "SHAREDSTATE"), 0)
        self.assertEqual(_grep_tree(self.share, "SHAREDDECISION"), 0)

    def test_the_report_states_how_many_were_WITHHELD(self):
        self.seed()
        self.open_sharing()
        out = self.sync()
        self.assertIn("WITHHELD", out)
        self.assertIn("1 task(s) shared", out)

    def test_unsharing_RETRACTS_the_file_rather_than_leaving_it(self):
        """"I removed the rule" has to be true of the repository, not just the config."""
        self.seed()
        self.open_sharing()
        self.sync()
        self.assertGreaterEqual(_grep_tree(self.share, "SHAREDPROBE"), 1)
        self.cli("brains", "unshare", "teamwork", "--with", "org")
        out = self.sync()
        self.assertEqual(_grep_tree(self.share, "SHAREDPROBE"), 0)
        self.assertIn("retracted", out)

    def test_re_sharing_clears_the_retraction_so_peers_are_not_told_two_things(self):
        self.seed()
        self.open_sharing()
        self.sync()
        self.cli("brains", "unshare", "teamwork", "--with", "org")
        self.sync()
        self.cli("brains", "share", "teamwork", "--with", "org")
        self.sync()
        part = os.path.join(self.share, "owners", "kosei", "station-0", "tasks")
        names = sorted(os.listdir(part))
        self.assertTrue(any(n.endswith(".json") for n in names), names)
        self.assertFalse(any(n.endswith(".tombstone") for n in names), names)

    def test_a_share_exchange_is_never_imported_into_the_store(self):
        self.seed()
        self.open_sharing()
        out = self.sync()
        self.assertIn("EXPORT ONLY", out)

    def test_pointing_a_run_at_the_share_directory_stays_filtered(self):
        self.seed()
        out = self.cli("sync", "--dir", self.share)
        # `--dir` takes the exchange's OWN declared kind, so the run stays filtered.
        self.assertIn("Share —", out)
        self.assertEqual(_grep_tree(self.share, "PRIVATEPROBE"), 0)


class ProjectionTest(_Store):
    """What a shared file actually contains — the allow-list, pinned."""

    def _shared_file(self):
        part = os.path.join(self.share, "owners", "kosei", "station-0", "tasks")
        names = [n for n in sorted(os.listdir(part)) if n.endswith(".json")]
        self.assertEqual(len(names), 1, names)
        with open(os.path.join(part, names[0]), encoding="utf-8") as f:
            return json.load(f)

    def test_the_shared_view_carries_identity_and_the_goal(self):
        self.seed()
        self.open_sharing()
        self.sync()
        payload = self._shared_file()
        self.assertEqual(payload["kind"], sync.KIND_SHARE)
        self.assertEqual(payload["audience"], ["org"])
        task = payload["task"]
        self.assertEqual(task["title"], "SHAREDPROBE")
        self.assertTrue(task["handle"].startswith("kosei-"))
        self.assertEqual(task["digest"]["goal"], "the team sees this")

    def test_the_owners_COST_AND_SESSIONS_are_not_in_it(self):
        """Not stripped afterwards — never built. A field absent from the allow-list
        cannot be published by a stripper that forgets it."""
        self.seed()
        self.open_sharing()
        self.sync()
        task = self._shared_file()["task"]
        for field in ("cost_usd", "tokens", "models", "sessions", "session_tree",
                      "usage", "work_mix", "prompts", "resume", "files", "repos",
                      "summary", "history", "glossary", "open_command"):
            self.assertNotIn(field, task, field)

    def test_liveness_is_never_federated(self):
        self.seed()
        self.open_sharing()
        self.sync()
        self.assertIs(self._shared_file()["task"]["live"], False)

    def test_a_field_added_to_the_view_model_does_not_widen_the_share(self):
        """The allow-list direction, asserted directly: the shared view's keys are a
        SUBSET of the names the allow-list permits."""
        self.seed()
        self.open_sharing()
        self.sync()
        allowed = set(sync.SHARE_IDENTITY) | {"live", "digest"} | \
            set(sync.SHARE_CHECKPOINT_EXTRA) | set(sync.SHARE_FULL_EXTRA)
        self.assertEqual(set(self._shared_file()["task"]) - allowed, set())


if __name__ == "__main__":
    unittest.main()
