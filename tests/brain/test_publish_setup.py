"""Shared-brain ACL/setup automation (``brain.publish_setup``) — dry-run, no network.

Asserts the exact ACL JSON shape (allow-only, inheritPermissions false, the Git
namespace + repoV2 token), the three printed steps, and that the deny-wins
rationale is in --help. Nothing here touches the network.

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 4a) from the brain source tree's
``tests/test_publish_setup.py`` @ 0.14.0. All 5 source cases port. The only
genericization is the CLI's ``--org``/``--project`` fixture values, which no
assertion reads (every assertion is on the ACL constants, the namespace GUID, or
the fixed step text).

The source imported ``tests.base`` purely for its ``sys.path`` side effect; here
``tests.brain.base`` does the same job for ``lib/``, and the classes stay plain
``unittest.TestCase`` — this module reads no config, so it needs no temp home.
"""
import contextlib
import io
import unittest

from tests.brain.base import BrainTestCase  # noqa: F401  (puts lib/ on sys.path)

import brain.publish_setup as ps


class AclShapeTest(unittest.TestCase):
    def test_acl_is_allow_only_no_denies(self):
        body = ps.acl_body("PID", "RID", "own", "contrib", "read")
        entry = body["value"][0]
        self.assertIs(entry["inheritPermissions"], False)          # inheritance OFF
        self.assertEqual(entry["token"], "repoV2/PID/RID")
        aces = entry["acesDictionary"]
        self.assertEqual(len(aces), 3)                             # owner + contributors + readers
        for a in aces.values():
            self.assertEqual(a["deny"], 0)                        # allow-only: no positive deny bit
            self.assertGreater(a["allow"], 0)
        self.assertEqual(aces["own"]["allow"], ps.OWNER_ALLOW)
        self.assertEqual(aces["contrib"]["allow"], ps.CONTRIBUTORS_ALLOW)
        self.assertEqual(aces["read"]["allow"], ps.READERS_ALLOW)

    def test_namespace_constant(self):
        self.assertEqual(ps.GIT_REPO_NAMESPACE, "2e9eb7ed-3c0a-47d4-87c1-0ffdd275fd87")


class DryRunOutputTest(unittest.TestCase):
    def _run(self, **kw):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = ps.main(["--org", "acme-devops", "--project", "Ledger",
                          "--repo", "ada-brain-shared", "--owner", "ada@x.com", *kw.get("extra", [])])
        return rc, buf.getvalue()

    def test_dry_run_prints_three_steps_and_acl(self):
        rc, out = self._run()
        self.assertEqual(rc, 0)
        self.assertIn("az repos create", out)                     # step 1
        self.assertIn("accesscontrollists", out.lower())          # step 2 endpoint
        self.assertIn(ps.GIT_REPO_NAMESPACE, out)
        self.assertIn("repoV2/", out)
        self.assertIn('"inheritPermissions": false', out)         # exact ACL JSON shape
        self.assertIn('"deny": 0', out)                           # allow-only
        self.assertIn(f'"allow": {ps.OWNER_ALLOW}', out)          # owner full control
        self.assertIn("--default-branch main", out)               # step 3

    def test_dry_run_never_executes(self):
        # no az/network invocation on the default path — DRY RUN banner present
        _, out = self._run()
        self.assertIn("DRY RUN", out)


class HelpTest(unittest.TestCase):
    def test_help_contains_deny_wins_rationale(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with self.assertRaises(SystemExit):
                ps.main(["--help"])
        text = buf.getvalue().lower()
        self.assertIn("deny-wins", text)
        self.assertIn("inheritance off", text)


if __name__ == "__main__":
    unittest.main()
