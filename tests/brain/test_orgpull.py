"""The org-brain auto-pull (``brain.orgpull``) — after the throttled pull, if the
clone HEAD moved, run the subscription check DETACHED and fail-open.

Gated on an actual HEAD move so a no-op pull costs nothing; the spawn is isolated
behind a patchable seam so the test never has to reach into a real detached
process.

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 4a) from the brain source tree's
``tests/test_org_brain_pull.py`` @ 0.14.0. All 5 source cases port. Two
mechanical differences:

  * the module imports normally (``import brain.orgpull``) — the source loaded
    ``org-brain-pull.py`` through ``importlib.util.spec_from_file_location``
    because a hyphenated filename is not importable;
  * the two env overrides in ``PullIntegrationTest`` use chunk 1's
    ``TASK_STATION_BRAIN_*`` namespace (read off ``base.ENV_KEYS``), not the
    source's retired prefix.

The source's ``AutoPullTest`` (throttle stamp + ``pull()`` failure breadcrumb)
lived in its ``tests/test_team_rules.py``; chunk 5a APPENDED it at the bottom of
this file, as chunk 4a's handoff asked. D33 kept the error-log labels and the
stamp filename at their source spellings, so all 5 cases land with no assertion
rewritten — the only change is that the stamp filename is READ from
``orgpull.STAMP_NAME`` instead of being re-spelled in the test.
"""
import os
import subprocess
import time

from tests.brain.base import BrainTestCase, ENV_KEYS

import brain.config as bconfig
import brain.orgpull as orgpull


class SpawnSeamMixin(BrainTestCase):
    """Swaps the detached-spawn seam. ZERO test methods — contributes no cases."""

    def setUp(self):
        super().setUp()
        self.calls = []
        self._orig = orgpull._spawn_check
        orgpull._spawn_check = lambda: self.calls.append(1)
        self.addCleanup(setattr, orgpull, "_spawn_check", self._orig)


class NotifyGateTest(SpawnSeamMixin):
    def test_spawns_when_head_moved(self):
        moved = orgpull.maybe_notify_subscriptions("a" * 40, "b" * 40)
        self.assertTrue(moved)
        self.assertEqual(self.calls, [1])

    def test_gated_when_head_unchanged(self):
        moved = orgpull.maybe_notify_subscriptions("a" * 40, "a" * 40)
        self.assertFalse(moved)
        self.assertEqual(self.calls, [])

    def test_gated_when_no_head(self):
        # pull failed / clone absent -> after is None -> never spawn
        self.assertFalse(orgpull.maybe_notify_subscriptions("a" * 40, None))
        self.assertEqual(self.calls, [])


class PullIntegrationTest(SpawnSeamMixin):
    """A real ff-only pull over a local bare remote: HEAD genuinely advances."""

    def setUp(self):
        super().setUp()
        self.remote = self.home / "remote.git"
        self._run(["git", "init", "--bare", str(self.remote)])
        self.work = self.home / "work"
        self._run(["git", "clone", str(self.remote), str(self.work)])
        self._gwork("config", "user.email", "t@e.com")
        self._gwork("config", "user.name", "T")
        (self.work / "f.md").write_text("v1")
        self._gwork("add", "-A"); self._gwork("commit", "-m", "c1")
        self._gwork("push", "origin", "HEAD:refs/heads/main")

        self.clone = self.home / "org_brain"
        self._run(["git", "clone", "-b", "main", str(self.remote), str(self.clone)])
        # base.setUp cleared these and restores them on cleanup. Named here so a
        # renamed config key fails as "not in ENV_KEYS" rather than as a
        # mysteriously un-spawned check (an unset override silently falls back to
        # the DEFAULT org-brain path, which does not exist under the temp home).
        self.assertIn("TASK_STATION_BRAIN_ORG_BRAIN_CLONE", ENV_KEYS)
        self.assertIn("TASK_STATION_BRAIN_STATE", ENV_KEYS)
        os.environ["TASK_STATION_BRAIN_ORG_BRAIN_CLONE"] = str(self.clone)
        os.environ["TASK_STATION_BRAIN_STATE"] = str(self.home / "state")

    def _main(self):
        try:
            orgpull.main()     # main() ends with sys.exit(0) (hook contract)
        except SystemExit:
            pass

    def _run(self, args):
        return subprocess.run(args, capture_output=True, text=True)

    def _gwork(self, *args):
        return subprocess.run(["git", "-C", str(self.work), *args], capture_output=True, text=True)

    def _advance_remote(self):
        (self.work / "f.md").write_text("v2")
        self._gwork("add", "-A"); self._gwork("commit", "-m", "c2")
        self._gwork("push", "origin", "HEAD:refs/heads/main")

    def test_main_spawns_after_head_moves(self):
        self._advance_remote()          # remote now ahead of the clone
        self._main()                    # due (no stamp) -> pull ff -> HEAD moves -> spawn
        self.assertEqual(self.calls, [1])

    def test_main_no_spawn_when_up_to_date(self):
        self._main()                    # clone already == remote -> HEAD unchanged -> no spawn
        self.assertEqual(self.calls, [])


# --------------------------------------------------------------------------- #
# APPENDED in chunk 5a — the throttle + breadcrumb half of org-pull coverage,
# ported from the source's ``tests/test_team_rules.py::AutoPullTest`` (5 cases).
# It tests `orgpull`, so it belongs here rather than in a second module that
# would drift from this one; its sibling `ClaudeMdImportTest` went to
# `test_init_home.py` for the same reason.
# --------------------------------------------------------------------------- #
class AutoPullTest(BrainTestCase):
    def test_is_due_when_no_stamp(self):
        self.assertTrue(orgpull.is_due())

    def test_throttled_after_touch(self):
        orgpull._touch_stamp()
        self.assertFalse(orgpull.is_due())                 # just pulled -> not due

    def test_due_again_after_24h(self):
        orgpull._touch_stamp()
        # the stamp filename is the module's constant, never re-spelled here (D33)
        stamp = bconfig.state_dir() / orgpull.STAMP_NAME
        old = time.time() - 25 * 3600
        os.utime(stamp, (old, old))
        self.assertTrue(orgpull.is_due())

    def test_no_org_brain_clone_is_graceful(self):
        cfg = {"org_brain_clone": self.home / "no-such-org_brain"}
        self.assertEqual(orgpull.pull(cfg), "no org_brain git clone")

    def test_pull_failure_silent_but_logged(self):
        org_brain = self.home / "org_brain"
        org_brain.mkdir()
        subprocess.run(["git", "-C", str(org_brain), "init"], capture_output=True)
        subprocess.run(["git", "-C", str(org_brain), "config", "user.email", "t@e.com"], capture_output=True)
        subprocess.run(["git", "-C", str(org_brain), "config", "user.name", "T"], capture_output=True)
        cfg = {"org_brain_clone": org_brain}
        status = orgpull.pull(cfg)                          # no remote -> git pull fails
        self.assertIn("logged", status)                    # returned quietly, no raise
        log = (bconfig.state_dir() / "error.log")
        self.assertTrue(log.exists())
        self.assertIn("org-brain-pull", log.read_text())


if __name__ == "__main__":
    import unittest
    unittest.main()
