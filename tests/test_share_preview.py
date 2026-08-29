"""WIDENING IS THE ONE THING YOU CANNOT TAKE BACK.

Un-sharing deletes the file. It does not un-read what somebody already read. So the
moment that needs a human is the moment visibility GROWS — a task published for the
first time, an audience gaining a member, a trail level going up — and a share run
that would do any of those is HELD, prints exactly what would become visible, and
writes nothing at all.

NARROWING NEVER ASKS. Taking something back is always safe, and a transport that
stopped to confirm a retraction would be training people to click through the prompt
that matters.

`HoldTest` asserts the hold WROTE NOTHING rather than that it printed a warning — a
gate that announces itself and then proceeds is the failure this class exists to
catch, and one of the red probes was exactly that.

The baseline for "wider than before" is THE PUBLISHED STATE ITSELF, not a separate
ledger of what was acknowledged, so the record of what you agreed to share cannot
drift from what is actually shared: they are the same bytes.
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(_REPO_ROOT, "lib")
sys.path.insert(0, LIB)

_TMP_HOME = tempfile.mkdtemp(prefix="ts-preview-")
os.environ["TASK_STATION_HOME"] = _TMP_HOME

import sync                                                             # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)

CLI = os.path.join(LIB, "task-station.py")

# NOT the bare word "HELD": it is a substring of "WITHHELD", which appears in every
# share report, so asserting on it passes on a run that was never held at all. That is
# the "a bare count substring-matching a bigger number" failure in its other costume,
# and it cost two red tests here before it was caught.
HOLD_MARK = "this run is HELD, not failed"


def _count(root, needle):
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


class WidenedTest(unittest.TestCase):
    """The predicate, in isolation. Everything else rests on it being right."""

    def p(self, audience, visibility="private"):
        return {"audience": list(audience), "visibility": visibility}

    def test_a_first_publication_is_a_widening(self):
        self.assertTrue(sync._widened(None, self.p(["org"])))

    def test_gaining_an_audience_member_is_a_widening(self):
        self.assertTrue(sync._widened(self.p(["org"]), self.p(["org", "jpark"])))

    def test_raising_the_trail_level_is_a_widening(self):
        self.assertTrue(sync._widened(self.p(["org"], "private"),
                                      self.p(["org"], "checkpoints")))
        self.assertTrue(sync._widened(self.p(["org"], "checkpoints"),
                                      self.p(["org"], "full")))

    def test_LOSING_an_audience_member_is_not(self):
        self.assertFalse(sync._widened(self.p(["org", "jpark"]), self.p(["org"])))

    def test_LOWERING_the_trail_level_is_not(self):
        self.assertFalse(sync._widened(self.p(["org"], "full"),
                                       self.p(["org"], "private")))

    def test_republishing_the_same_thing_is_not(self):
        self.assertFalse(sync._widened(self.p(["org"]), self.p(["org"])))

    def test_swapping_one_audience_for_another_IS_a_widening(self):
        """`jpark` has not seen it before, and that is what matters — not whether the
        audience got bigger."""
        self.assertTrue(sync._widened(self.p(["org"]), self.p(["jpark"])))


class _Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-preview-store-")
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

    def seed(self, share_it=True):
        self.cli("sync", "--init")
        self.cli("sync", "--init-share")
        self.cli("create", "--title", "WIDENPROBE", "--no-attach",
                 "--goal", "GOALTEXTPUBLISHED")
        self.cli("create", "--title", "STAYSPRIVATE", "--no-attach")
        if share_it:
            self.cli("brains", "add", "teamwork")
            self.cli("brains", "assign", "1", "teamwork")
            self.cli("brains", "share", "teamwork", "--with", "org")


class HoldTest(_Store):
    def test_a_first_publication_is_HELD_and_writes_NOTHING(self):
        self.seed()
        out = self.cli("sync")
        self.assertIn("SHARING PREVIEW", out)
        self.assertIn(HOLD_MARK, out)
        self.assertEqual(_count(self.share, "WIDENPROBE"), 0)

    def test_the_hold_names_the_task_its_audience_and_what_is_published(self):
        """"Exactly what becomes visible" has to mean the fields and the values, not a
        count — a preview that says "1 task" is a number to click past."""
        self.seed()
        out = self.cli("sync")
        self.assertIn("WIDENPROBE", out)
        self.assertIn("to: org", out)
        self.assertIn("GOALTEXTPUBLISHED", out)
        self.assertIn("trail: private", out)
        self.assertIn("fields:", out)

    def test_the_hold_says_how_many_stay_private(self):
        self.seed()
        self.assertIn("1 task(s) stay private", self.cli("sync"))

    def test_confirming_publishes_exactly_what_was_previewed(self):
        self.seed()
        self.cli("sync")
        out = self.cli("sync", "--confirm-share")
        self.assertGreaterEqual(_count(self.share, "WIDENPROBE"), 1)
        self.assertEqual(_count(self.share, "STAYSPRIVATE"), 0)
        self.assertIn("1 task(s) shared", out)

    def test_a_SECOND_sync_after_confirming_is_not_held_again(self):
        """The baseline is the published state, so an accepted share stays accepted
        and unattended syncs keep running."""
        self.seed()
        self.cli("sync", "--confirm-share")
        out = self.cli("sync")
        self.assertNotIn(HOLD_MARK, out)
        self.assertIn("1 task(s) shared", out)

    def test_widening_an_ALREADY_shared_task_is_held_again(self):
        self.seed()
        self.cli("sync", "--confirm-share")
        self.cli("brains", "share", "teamwork", "--with", "jpark")
        out = self.cli("sync")
        self.assertIn(HOLD_MARK, out)
        self.assertIn("jpark", out)

    def test_NARROWING_is_never_held(self):
        """Un-sharing takes effect immediately — a prompt on a retraction teaches
        people to click through the one that matters."""
        self.seed()
        self.cli("sync", "--confirm-share")
        self.cli("brains", "unshare", "teamwork", "--with", "org")
        out = self.cli("sync")
        self.assertNotIn(HOLD_MARK, out)
        self.assertEqual(_count(self.share, "WIDENPROBE"), 0)

    def test_the_BACKUP_destination_is_never_held_by_a_share_decision(self):
        """Durability does not wait on a visibility question."""
        self.seed()
        self.cli("sync")
        self.assertGreaterEqual(_count(self.backup, "WIDENPROBE"), 1)
        self.assertGreaterEqual(_count(self.backup, "STAYSPRIVATE"), 1)


class PreviewCommandTest(_Store):
    def test_preview_writes_nothing_even_when_confirmed_would(self):
        self.seed()
        out = self.cli("sync", "--preview")
        self.assertIn("SHARING PREVIEW", out)
        self.assertIn("WIDENPROBE", out)
        self.assertEqual(_count(self.share, "WIDENPROBE"), 0)
        self.assertEqual(_count(self.backup, "WIDENPROBE"), 0)

    def test_preview_with_nothing_shared_says_so_plainly(self):
        self.seed(share_it=False)
        out = self.cli("sync", "--preview")
        self.assertIn("Nothing is shared", out)
        self.assertIn("2 task(s) withheld", out)

    def test_preview_without_a_share_exchange_explains_rather_than_erroring(self):
        self.cli("sync", "--init")
        env_out = self.cli("sync", "--preview", "--dir", self.backup)
        self.assertIn("no share exchange", env_out.lower())


class PlanTest(_Store):
    def test_the_plan_lists_the_fields_that_would_be_published(self):
        self.seed()
        self.cli("sync", "--confirm-share")
        out = self.cli("sync", "--preview")
        for field in ("handle", "title", "digest"):
            self.assertIn(field, out)

    def test_a_retraction_shows_up_as_WOULD_BE_WITHDRAWN(self):
        self.seed()
        self.cli("sync", "--confirm-share")
        self.cli("brains", "unshare", "teamwork", "--with", "org")
        out = self.cli("sync", "--preview")
        self.assertIn("WOULD BE WITHDRAWN", out)
        self.assertIn("WIDENPROBE", out)


if __name__ == "__main__":
    unittest.main()
