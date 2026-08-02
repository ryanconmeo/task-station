"""ship.sh (the release helper) must stay valid + public-repo safe.

task-station ships from a PUBLIC GitHub repo, so the helper carries no personal
paths, private-org references, or embedded credentials — it derives the repo root
from git and defers auth to the user's own git credential helper. These tests lock
that in (a leaked home path or org handle would ship to every user/fork)."""
import os
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHIP = os.path.join(ROOT, "scripts", "ship.sh")


class ShipScriptTest(unittest.TestCase):
    def test_present_and_executable(self):
        self.assertTrue(os.path.isfile(SHIP), "scripts/ship.sh must exist")
        self.assertTrue(os.access(SHIP, os.X_OK), "scripts/ship.sh must be executable")

    def test_bash_syntax_ok(self):
        r = subprocess.run(["bash", "-n", SHIP], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_public_repo_safe(self):
        with open(SHIP, encoding="utf-8") as f:
            body = f.read()
        # personal paths / usernames / private-org references / obvious secret tokens
        # must never be hardcoded — the script must work from any clone or fork.
        # A private-org reference reaches a shell script as a host+org URL, so the
        # bare `dev.azure.com` needle catches that whole class without this public
        # repo having to name anyone's employer; `companyname` is the placeholder a
        # fork swaps for its own org handle.
        for needle in ("/Users/", "/home/", "Workspace-Other", "ryannguyen",
                       "companyname", "dev.azure.com", "ryanconmeo",
                       "ghp_", "github_pat_"):
            self.assertNotIn(needle, body,
                             "ship.sh must not hardcode %r (public repo)" % needle)

    def test_help_exits_clean(self):
        r = subprocess.run(["bash", SHIP, "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("release helper", r.stdout)

    def test_unknown_arg_rejected(self):
        r = subprocess.run(["bash", SHIP, "--nope"], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
