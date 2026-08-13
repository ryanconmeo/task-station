"""brain.search — search mechanics (``search_hits``: fixed-string, phrase, dedup,
ranking).

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 4c) from the brain source tree's
``tests/test_search.py`` @ 0.14.0. All 6 source cases port 1:1 (module ``brain``
-> ``brain.search``; the metachar fixture's org word is genericized — see the
chunk-4c handoff's arithmetic). Two cases are ADDED for the ``-m`` entry point,
which is new surface: a package module with relative imports cannot be run by
path, so ``python3 -m brain.search`` IS the CLI and its runnability is a contract
the CLI-driving suites (``test_write_guards``, ``test_naming_write_path``) all
sit on.

Requires ripgrep (a hard runtime dep); those cases are skipped if rg is absent.
"""
import os
import shutil
import subprocess
import sys
import unittest

from tests.brain.base import BrainTestCase, LIB, PINNED_ENV

import brain.search as search


@unittest.skipIf(shutil.which("rg") is None, "ripgrep not installed")
class SearchHitsTest(BrainTestCase):
    def setUp(self):
        super().setUp()
        self.vault = self.make_vault(self.home / "vault")

    def _write(self, folder, slug, description="", body="", verified="2026-01-01", name=None):
        d = self.vault / folder
        d.mkdir(parents=True, exist_ok=True)
        name = name or slug
        (d / f"{slug}.md").write_text(
            f"---\nname: {name}\ndescription: {description}\nverified: {verified}\n---\n\n{body}\n"
        )
        return d / f"{slug}.md"

    def _roots(self):
        return [(search.TIER_NOTES, self.vault / "notes"),
                (search.TIER_MEMORY, self.vault / "memory"),
                (search.TIER_ORG_BRAIN, self.vault / "org_brain"),
                (search.TIER_TASKS, self.vault / "task-station")]

    def test_metachar_query_returns_hits(self):
        # '(ledger' is an invalid regex; as a fixed string it must still match.
        self._write("notes", "paren-note", body="working on the (ledger migration today")
        hits = search.search_hits(["(ledger"], self._roots())
        self.assertEqual([h[0] for h in hits], [str(self.vault / "notes/paren-note.md")])

    def test_phrase_adjacency(self):
        self._write("notes", "adjacent", body="the global search bar ships next")
        self._write("notes", "scattered", body="search across the global corpus")
        hits = search.search_hits(["global search"], self._roots())
        paths = [h[0] for h in hits]
        self.assertIn(str(self.vault / "notes/adjacent.md"), paths)
        self.assertNotIn(str(self.vault / "notes/scattered.md"), paths)

    def test_dedup_keeps_highest_tier(self):
        self._write("notes", "dup", body="the widget lives here")     # tier 0
        self._write("memory", "dup", body="the widget lives here")    # tier 1
        hits = search.search_hits(["widget"], self._roots())
        self.assertEqual(len(hits), 1)                                 # dupe collapsed
        self.assertEqual(hits[0][0], str(self.vault / "notes/dup.md"))  # highest tier kept

    def test_dedup_does_not_consume_budget(self):
        self._write("notes", "dup", body="alpha term here")
        self._write("memory", "dup", body="alpha term here")
        self._write("notes", "other", body="alpha term here too")
        hits = search.search_hits(["alpha"], self._roots(), limit=2)
        # two DISTINCT notes returned, not the dupe eating a slot twice
        self.assertEqual(len(hits), 2)
        self.assertEqual({h[0] for h in hits},
                         {str(self.vault / "notes/dup.md"), str(self.vault / "notes/other.md")})

    def test_ranking_title_beats_body(self):
        self._write("notes", "widget-guide", body="unrelated prose")   # term in title -> 3
        self._write("notes", "misc", body="a stray widget mention")     # term in body -> 1
        hits = search.search_hits(["widget"], self._roots())
        self.assertEqual(hits[0][0], str(self.vault / "notes/widget-guide.md"))

    def test_ranking_verified_date_tiebreak(self):
        self._write("notes", "older", body="ranking token", verified="2020-01-01")
        self._write("notes", "newer", body="ranking token", verified="2026-06-01")
        hits = search.search_hits(["ranking token"], self._roots())
        self.assertEqual(hits[0][0], str(self.vault / "notes/newer.md"))


class ModuleEntryPointTest(BrainTestCase):
    """ADDED — ``python3 -m brain.search`` is the CLI.

    The source ran ``scripts/brain.py`` as a subprocess by PATH. That cannot work
    for a package module whose imports are relative, so every CLI-driving case in
    this suite spawns ``[sys.executable, "-m", "brain.search", …]`` with ``lib/``
    on PYTHONPATH. If that invocation ever stops working, the failure should name
    the entry point rather than surfacing as a dozen confusing CLI failures
    elsewhere — which is what these two cases are for.
    """

    def _run(self, *args):
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["PYTHONPATH"] = str(LIB)
        for k, rel in PINNED_ENV.items():
            env[k] = str(self.home / rel)
        return subprocess.run([sys.executable, "-m", "brain.search", *args],
                              capture_output=True, text=True, env=env)

    def test_the_module_runs_and_prints_help(self):
        r = self._run("--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("brain", r.stdout)
        # every subcommand the chunk ships is reachable from the front door
        for cmd in ("search", "new", "ref", "subscriptions", "peers", "publish",
                    "status", "log", "find-target", "recent-tasks"):
            self.assertIn(cmd, r.stdout)

    def test_status_runs_against_the_temp_home(self):
        vault = self.make_vault(self.home / "vault")
        self.write_primary_config({"vault": str(vault)})
        r = self._run("status")
        self.assertEqual(r.returncode, 0, r.stderr)
        # the child resolved THIS test's config, not the developer's real vault
        self.assertIn(str(vault), r.stdout)


if __name__ == "__main__":
    unittest.main()
