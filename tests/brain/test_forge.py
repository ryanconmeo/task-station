"""core.forge adapters — selection + command-shape (argv) checks.

PROVENANCE: ported 1:1 from the source's ``tests/test_forge.py`` @ 0.14.0 (15
cases, same classes, same assertions); only the import paths change and one
fixture repo name was neutralised (``vault-ryan`` -> ``vault-owner``).

Everything here is offline: adapter selection is a dict lookup, and the argv
builders are pure functions, so the github/ado command shapes are asserted
without ever touching a network or the az/gh CLIs."""
import unittest

from tests.brain.base import BrainTestCase

import core.forge as forge
from core.forge import ado, github


class AdapterSelectionTest(BrainTestCase):
    def test_default_is_ado(self):
        self.assertIs(forge.get_adapter({}), ado)

    def test_explicit_ado(self):
        self.assertIs(forge.get_adapter({"forge_kind": "ado"}), ado)

    def test_github(self):
        self.assertIs(forge.get_adapter({"forge_kind": "github"}), github)

    def test_case_and_whitespace_insensitive(self):
        self.assertIs(forge.get_adapter({"forge_kind": "  GitHub "}), github)

    def test_unknown_kind_raises(self):
        with self.assertRaises(forge.ForgeError):
            forge.get_adapter({"forge_kind": "gitlab"})


class ConfiguredTest(BrainTestCase):
    def test_ado_needs_org_project_repo(self):
        self.assertFalse(ado.configured({"forge_org": "o", "forge_repo": "r"}))
        self.assertTrue(ado.configured({"forge_org": "o", "forge_project": "p", "forge_repo": "r"}))

    def test_github_needs_owner_and_repo(self):
        self.assertFalse(github.configured({"forge_org": "acme-org"}))
        self.assertTrue(github.configured({"forge_org": "acme-org", "forge_repo": "org-brain"}))


class GithubArgvTest(BrainTestCase):
    def setUp(self):
        super().setUp()
        self.cfg = {"forge_org": "acme-org", "forge_repo": "org-brain",
                    "forge_target_branch": "main"}

    def test_pr_create_argv(self):
        argv = github.pr_create_argv(self.cfg, "promote-x", "main", "promote: x")
        self.assertEqual(argv, ["gh", "pr", "create", "--repo", "acme-org/org-brain",
                                "--head", "promote-x", "--base", "main",
                                "--title", "promote: x", "--body", "promote: x"])

    def test_repo_create_argv_private_by_default(self):
        self.assertEqual(github.repo_create_argv(self.cfg, "vault-owner"),
                         ["gh", "repo", "create", "acme-org/vault-owner", "--private"])

    def test_repo_create_argv_public(self):
        self.assertEqual(github.repo_create_argv(self.cfg, "vault-owner", private=False),
                         ["gh", "repo", "create", "acme-org/vault-owner", "--public"])

    def test_push_argv(self):
        self.assertEqual(github.push_argv("/tmp/clone", "promote-x"),
                         ["git", "-C", "/tmp/clone", "push", "-u", "origin", "promote-x"])


class AdoArgvTest(BrainTestCase):
    def setUp(self):
        super().setUp()
        self.cfg = {"forge_org": "https://example.com/your-org", "forge_project": "Proj",
                    "forge_repo": "wiki", "forge_target_branch": "main"}

    def test_pr_create_argv(self):
        argv = ado.pr_create_argv(self.cfg, "promote-x", "main", "promote: x")
        self.assertEqual(argv[:4], ["az", "repos", "pr", "create"])
        self.assertIn("--organization", argv)
        self.assertIn("https://example.com/your-org", argv)
        self.assertIn("--source-branch", argv)
        self.assertEqual(argv[argv.index("--source-branch") + 1], "promote-x")

    def test_push_argv_injects_bearer_when_token(self):
        argv = ado.push_argv("/tmp/clone", "br", token="TOK")
        self.assertIn("-c", argv)
        self.assertIn("http.extraheader=AUTHORIZATION: bearer TOK", argv)
        self.assertEqual(argv[-4:], ["push", "-u", "origin", "br"])

    def test_push_argv_plain_without_token(self):
        argv = ado.push_argv("/tmp/clone", "br")
        self.assertNotIn("-c", argv)
        self.assertEqual(argv, ["git", "-C", "/tmp/clone", "push", "-u", "origin", "br"])

    def test_repo_create_argv(self):
        argv = ado.repo_create_argv(self.cfg, "wiki2")
        self.assertEqual(argv[:3], ["az", "repos", "create"])
        self.assertIn("--name", argv)
        self.assertEqual(argv[argv.index("--name") + 1], "wiki2")


if __name__ == "__main__":
    unittest.main()
