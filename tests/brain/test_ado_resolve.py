"""brain.ado_resolve — network-free unit tests for the number -> ADO link(s) resolver.

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 5a) from the brain source tree's
``tests/test_ado_resolve.py`` @ 0.14.0. All 5 source cases port 1:1 (the PR
fixture's repo/project names were an org product name and are genericized).

ADDED: the no-org path. This module's whole contract is "exactly one line of JSON
on stdout, never a traceback", and the port removed the built-in organization
default that made "no org" unreachable — so the contract now has a case the
source could not have written.

Monkeypatches ``ado_resolve._try_get`` / ``resolve_auth`` so nothing hits the network.
"""
import contextlib
import io
import json
import os
import sys
import unittest

from tests.brain.base import BrainTestCase  # noqa: F401 — also puts lib/ on sys.path

import brain.ado_resolve as ado_resolve

ORG = "https://dev.azure.com/o"


def _fake_workitem():
    return {
        "fields": {
            "System.Title": "Ship the thing",
            "System.State": "Active",
            "System.WorkItemType": "User Story",
        }
    }


def _fake_pr():
    return {
        "pullRequestId": 555,
        "title": "Add the feature",
        "status": "active",
        "repository": {"name": "atlas-web", "project": {"name": "Atlas"}},
    }


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self._orig_try_get = ado_resolve._try_get
        self.addCleanup(self._restore)

    def _restore(self):
        ado_resolve._try_get = self._orig_try_get

    def _patch_try_get(self, wi=None, pr=None):
        def fake(url, auth):
            if "/wit/workitems/" in url:
                return wi
            if "/git/pullrequests/" in url:
                return pr
            raise AssertionError(f"unexpected url: {url}")
        ado_resolve._try_get = fake

    def test_workitem_only(self):
        self._patch_try_get(wi=_fake_workitem(), pr=None)
        result = ado_resolve.resolve(ORG, 100, auth=None)
        self.assertEqual(result["query"], 100)
        self.assertIsNone(result["error"])
        self.assertEqual(len(result["matches"]), 1)
        m = result["matches"][0]
        self.assertEqual(m["kind"], "workitem")
        self.assertEqual(m["title"], "Ship the thing")
        self.assertEqual(m["state"], "Active")
        self.assertEqual(m["type"], "User Story")
        self.assertEqual(m["url"], f"{ORG}/_workitems/edit/100")

    def test_pr_only(self):
        self._patch_try_get(wi=None, pr=_fake_pr())
        result = ado_resolve.resolve(ORG, 555, auth=None)
        self.assertIsNone(result["error"])
        self.assertEqual(len(result["matches"]), 1)
        m = result["matches"][0]
        self.assertEqual(m["kind"], "pr")
        self.assertEqual(m["title"], "Add the feature")
        self.assertEqual(m["state"], "active")
        self.assertEqual(m["url"], f"{ORG}/Atlas/_git/atlas-web/pullrequest/555")

    def test_both_found(self):
        self._patch_try_get(wi=_fake_workitem(), pr=_fake_pr())
        result = ado_resolve.resolve(ORG, 100, auth=None)
        self.assertEqual(len(result["matches"]), 2)
        kinds = {m["kind"] for m in result["matches"]}
        self.assertEqual(kinds, {"workitem", "pr"})

    def test_neither_found(self):
        self._patch_try_get(wi=None, pr=None)
        result = ado_resolve.resolve(ORG, 999999, auth=None)
        self.assertEqual(result["matches"], [])
        self.assertIsNone(result["error"])


def _run_main(argv):
    """Run ``main()`` with a fixed argv, capturing stdout. The popup contract is
    that this NEVER raises and always prints exactly one JSON line."""
    argv_backup = sys.argv
    sys.argv = argv
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            ado_resolve.main()
    finally:
        sys.argv = argv_backup
    out = buf.getvalue().strip().splitlines()
    assert len(out) == 1, f"expected exactly one line of JSON, got {out!r}"
    return json.loads(out[0])


class MainAuthFailureTest(unittest.TestCase):
    def test_auth_failure_yields_error_and_empty_matches(self):
        orig_resolve_auth = ado_resolve.resolve_auth

        def boom(allow_login=False):
            raise SystemExit("no credential")

        ado_resolve.resolve_auth = boom
        self.addCleanup(setattr, ado_resolve, "resolve_auth", orig_resolve_auth)
        out = _run_main(["ado-resolve", "42", "--org", ORG])

        self.assertEqual(out["query"], 42)
        self.assertEqual(out["matches"], [])
        self.assertEqual(out["error"], "no ADO credential")
        self.assertIn("az login", out["hint"])


class MainNoOrgTest(BrainTestCase):
    """ADDED — no ``--org``, no config, no ``$ADO_ORG``. The source had a built-in
    org here; the port has none, so this path is new and must still honour the
    one-line-of-JSON contract rather than building URLs out of ``None``."""

    def setUp(self):
        super().setUp()
        self._ado_org = os.environ.pop("ADO_ORG", None)
        if self._ado_org is not None:
            self.addCleanup(os.environ.__setitem__, "ADO_ORG", self._ado_org)

    def test_no_org_is_one_json_line_and_never_a_network_call(self):
        def never(*a, **k):
            raise AssertionError("auth was resolved despite there being no org")

        orig = ado_resolve.resolve_auth
        ado_resolve.resolve_auth = never
        self.addCleanup(setattr, ado_resolve, "resolve_auth", orig)
        out = _run_main(["ado-resolve", "42"])

        self.assertEqual(out["query"], 42)
        self.assertEqual(out["matches"], [])
        self.assertEqual(out["error"], "no ADO organization")
        self.assertIn("ADO_ORG", out["hint"])


if __name__ == "__main__":
    unittest.main()
