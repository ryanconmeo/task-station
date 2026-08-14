"""brain.peers — the peers layer (lazy, read-only teammate private brains).

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 4c) from the brain source tree's
``tests/test_peers.py`` @ 0.14.0. 15 of its 16 cases ported 1:1 then; the 16th
(``SearchIntegrationTest::test_injection_never_includes_peers``) drove
``context-inject.py`` and was DEFERRED by name until that module landed. Chunk 5a
ported it (bottom of this file) — as plain ``import brain.hooks.inject``, with no
``spec_from_file_location``: the trick died with the rename. It sits in its own
class rather than back inside ``SearchIntegrationTest``, because that class is
skipped without ripgrep and this guard must never be silently skipped. Two cases
are ADDED (below).

Rewrites: modules ``pb_config`` -> ``brain.config``, ``peers`` -> ``brain.peers``,
``brain`` -> ``brain.search``; env names come from chunk 1's
``TASK_STATION_BRAIN_*`` namespace (asserted against ``base.ENV_KEYS``, so a
renamed config key fails loudly rather than silently reading the developer's real
vault). ``SearchIntegrationTest`` passes an explicit ``cfg`` to ``default_roots``
instead of ``importlib.reload``-ing the module to rebind its module-level
``_CFG`` — same coverage, no reload, and it exercises the ``cfg=`` parameter that
exists for exactly this.

Covers: registry parse + missing/malformed registry + peers_extra; lazy clone
from a local fixture 'remote' repo; ff-only sync; remove; and search integration
— peer tier inclusion, tier priority (own > org_brain > peers), and the
peer:<alias>/<slug> label.
"""
import ast
import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

from tests.brain.base import BrainTestCase, ENV_KEYS

import brain.config as bconfig
import brain.hooks.inject as inject
import brain.peers as peers
import brain.search as search


def _git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True)


class PeersBase(BrainTestCase):
    def setUp(self):
        super().setUp()
        self.org_brain = self.home / "org_brain"
        self.org_brain.mkdir(parents=True, exist_ok=True)
        self.peers_dir = self.home / "peers"
        # base.setUp cleared these and restores them on cleanup; named against
        # ENV_KEYS so a renamed config key fails as "not in ENV_KEYS".
        self.assertIn("TASK_STATION_BRAIN_ORG_BRAIN_CLONE", ENV_KEYS)
        self.assertIn("TASK_STATION_BRAIN_PEERS_DIR", ENV_KEYS)
        os.environ["TASK_STATION_BRAIN_ORG_BRAIN_CLONE"] = str(self.org_brain)
        os.environ["TASK_STATION_BRAIN_PEERS_DIR"] = str(self.peers_dir)

    def write_registry(self, people):
        (self.org_brain / "registry.json").write_text(json.dumps({"people": people}))

    def make_remote(self, name="teammate-remote", note_slug="peer-note",
                    note_body="a peer fact about widgets"):
        """A local git repo that stands in for a teammate's published mirror."""
        remote = self.home / name
        (remote / "notes").mkdir(parents=True, exist_ok=True)
        (remote / "notes" / f"{note_slug}.md").write_text(
            f"---\nname: {note_slug}\ndescription: peer desc\nverified: 2026-01-01\n---\n\n{note_body}\n")
        _git("-C", str(remote), "init")
        _git("-C", str(remote), "config", "user.email", "t@e.com")
        _git("-C", str(remote), "config", "user.name", "T")
        _git("-C", str(remote), "add", "-A")
        _git("-C", str(remote), "commit", "-m", "seed")
        return remote


class RegistryTest(PeersBase):
    def test_parse_registry(self):
        self.write_registry([{"alias": "ada", "name": "Ada L", "shared": "https://x/ada"}])
        people = peers.load_registry(bconfig.load())
        self.assertEqual(len(people), 1)
        self.assertEqual(people[0]["alias"], "ada")
        self.assertEqual(people[0]["name"], "Ada L")

    def test_registry_reads_the_new_shared_key(self):
        # The org-brain registry's per-person key is `shared`.
        # Both must resolve: the rename ships in a separate repo's PR, so for a
        # while the live registry carries the old key and a fresh one the new.
        self.write_registry([{"alias": "ada", "name": "Ada L", "shared": "https://x/ada"}])
        people = peers.load_registry(bconfig.load())
        self.assertEqual(len(people), 1)
        self.assertEqual(people[0]["alias"], "ada")
        self.assertEqual(people[0]["shared"], "https://x/ada")

    def test_missing_registry_is_graceful(self):
        # no registry.json at all
        self.assertEqual(peers.load_registry(bconfig.load()), [])

    def test_malformed_registry_is_graceful_and_logged(self):
        (self.org_brain / "registry.json").write_text("{ not json ")
        self.assertEqual(peers.load_registry(bconfig.load()), [])
        log = bconfig.state_dir() / "error.log"
        self.assertTrue(log.exists() and "peers:registry" in log.read_text())

    def test_peers_extra_merges(self):
        self.write_registry([{"alias": "ada", "name": "Ada", "shared": "u1"}])
        self.write_primary_config({
            "org_brain_clone": str(self.org_brain),
            "peers_dir": str(self.peers_dir),
            "peers_extra": [{"alias": "bob", "shared": "u2"}],
        })
        aliases = {p["alias"] for p in peers.load_registry(bconfig.load())}
        self.assertEqual(aliases, {"ada", "bob"})


@unittest.skipIf(shutil.which("git") is None, "git not installed")
class CloneSyncTest(PeersBase):
    def test_lazy_clone_from_local_remote(self):
        remote = self.make_remote()
        self.write_registry([{"alias": "mate", "name": "Mate", "shared": str(remote)}])
        cfg = bconfig.load()
        self.assertFalse(peers.is_cloned(cfg, "mate"))
        res = peers.add(cfg, "mate")
        self.assertEqual(res["status"], "cloned")
        self.assertTrue(peers.is_cloned(cfg, "mate"))
        self.assertTrue((self.peers_dir / "mate" / "notes" / "peer-note.md").exists())

    def test_add_unknown_alias(self):
        self.write_registry([])
        res = peers.add(bconfig.load(), "ghost")
        self.assertEqual(res["status"], "unknown")

    def test_add_twice_is_exists(self):
        remote = self.make_remote()
        self.write_registry([{"alias": "mate", "name": "M", "shared": str(remote)}])
        cfg = bconfig.load()
        peers.add(cfg, "mate")
        self.assertEqual(peers.add(cfg, "mate")["status"], "exists")

    def test_sync_ff_only(self):
        remote = self.make_remote()
        self.write_registry([{"alias": "mate", "name": "M", "shared": str(remote)}])
        cfg = bconfig.load()
        peers.add(cfg, "mate")
        # advance the remote, then sync should ff-only pull it
        (remote / "notes" / "peer-note.md").write_text(
            "---\nname: peer-note\ndescription: peer desc\nverified: 2026-02-01\n---\n\nupdated fact\n")
        _git("-C", str(remote), "commit", "-am", "update")
        res = peers.sync(cfg, "mate")
        self.assertEqual(res, [{"alias": "mate", "status": "pulled"}])
        self.assertIn("updated fact", (self.peers_dir / "mate" / "notes" / "peer-note.md").read_text())

    def test_remove(self):
        remote = self.make_remote()
        self.write_registry([{"alias": "mate", "name": "M", "shared": str(remote)}])
        cfg = bconfig.load()
        peers.add(cfg, "mate")
        self.assertEqual(peers.remove(cfg, "mate")["status"], "removed")
        self.assertFalse((self.peers_dir / "mate").exists())


@unittest.skipIf(shutil.which("git") is None, "git not installed")
class ReadOnlyCrossTest(PeersBase):
    """ADDED — the cross-brain half of the federation contract, stated as tests.

    A peer tier is READ-ONLY (a sync pulls; it never pushes back) and NEVER
    auto-cloned (a sync of an alias that was never added does nothing and creates
    nothing). The source covered the happy path of both verbs but asserted
    neither prohibition, and both are the reason peers are safe to enable.
    """

    def test_sync_never_advances_the_peer_remote(self):
        remote = self.make_remote()
        self.write_registry([{"alias": "mate", "name": "M", "shared": str(remote)}])
        cfg = bconfig.load()
        peers.add(cfg, "mate")
        before = _git("-C", str(remote), "rev-parse", "HEAD").stdout.strip()
        # local edit + commit in the clone, then sync: a push would move the remote
        (self.peers_dir / "mate" / "notes" / "peer-note.md").write_text("local edit\n")
        _git("-C", str(self.peers_dir / "mate"), "config", "user.email", "t@e.com")
        _git("-C", str(self.peers_dir / "mate"), "config", "user.name", "T")
        _git("-C", str(self.peers_dir / "mate"), "commit", "-am", "local")
        peers.sync(cfg, "mate")
        after = _git("-C", str(remote), "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(before, after)

    def test_sync_of_an_uncloned_alias_never_clones(self):
        remote = self.make_remote()
        self.write_registry([{"alias": "mate", "name": "M", "shared": str(remote)}])
        cfg = bconfig.load()
        res = peers.sync(cfg, "mate")                       # never added
        self.assertEqual(res, [{"alias": "mate", "status": "not-cloned"}])
        self.assertFalse((self.peers_dir / "mate").exists())


class PeerRootsAndLabelTest(PeersBase):
    def _make_clone(self, alias, slug="n", body="content"):
        d = self.peers_dir / alias / "notes"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{slug}.md").write_text(
            f"---\nname: {slug}\ndescription: d\nverified: 2026-01-01\n---\n\n{body}\n")
        return d / f"{slug}.md"

    def test_peer_roots_lists_notes_dirs(self):
        self._make_clone("ada")
        self._make_clone("bob")
        roots = peers.peer_roots(bconfig.load())
        self.assertEqual(sorted(r.name for r in roots), ["notes", "notes"])
        self.assertTrue(any("ada" in str(r) for r in roots))

    def test_peer_label(self):
        f = self._make_clone("ada", slug="widgets")
        cfg = bconfig.load()
        self.assertEqual(peers.peer_label(f, cfg), "peer:ada/widgets")
        self.assertIsNone(peers.peer_label(self.home / "vault/notes/x.md", cfg))


@unittest.skipIf(shutil.which("rg") is None, "ripgrep not installed")
class SearchIntegrationTest(PeersBase):
    def setUp(self):
        super().setUp()
        self.vault = self.make_vault(self.home / "vault")
        self.assertIn("TASK_STATION_BRAIN_VAULT", ENV_KEYS)
        os.environ["TASK_STATION_BRAIN_VAULT"] = str(self.vault)

    def _cfg(self):
        """This test's config, read fresh. The source reloaded ``brain`` here to
        rebind its module-level ``_CFG``; ``default_roots`` takes an explicit
        ``cfg`` for exactly this, so nothing is reloaded."""
        return bconfig.load()

    def _peer_note(self, alias, slug, body):
        d = self.peers_dir / alias / "notes"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{slug}.md").write_text(
            f"---\nname: {slug}\ndescription: d\nverified: 2026-01-01\n---\n\n{body}\n")

    def _own_note(self, slug, body):
        (self.vault / "notes" / f"{slug}.md").write_text(
            f"---\nname: {slug}\ndescription: d\nverified: 2026-01-01\n---\n\n{body}\n")

    def test_peers_excluded_by_default(self):
        self._peer_note("ada", "ponglobble", "the ponglobble token lives in a peer note")
        roots = search.default_roots(include_peers=False, cfg=self._cfg())
        hits = search.search_hits(["ponglobble"], roots)
        self.assertEqual(hits, [])

    def test_peers_included_with_flag(self):
        self._peer_note("ada", "ponglobble", "the ponglobble token lives in a peer note")
        roots = search.default_roots(include_peers=True, cfg=self._cfg())
        hits = search.search_hits(["ponglobble"], roots)
        self.assertEqual(len(hits), 1)
        self.assertTrue(hits[0][0].endswith("ada/notes/ponglobble.md"))

    def test_own_note_beats_peer_on_dupe(self):
        # same slug in own vault and a peer -> own wins (lower tier), peer collapsed
        self._own_note("shared", "the sharedterm is mine")
        self._peer_note("ada", "shared", "the sharedterm is a peer copy")
        roots = search.default_roots(include_peers=True, cfg=self._cfg())
        hits = search.search_hits(["sharedterm"], roots)
        self.assertEqual(len(hits), 1)
        self.assertTrue(hits[0][0].endswith("vault/notes/shared.md"))


# --------------------------------------------------------------------------- #
# APPENDED in chunk 5a — the guard chunk 4c deferred by name.
# --------------------------------------------------------------------------- #
class InjectionNeverIncludesPeersTest(PeersBase):
    """A teammate's notes must never reach someone's context unasked.

    The peers tier is explicit opt-in — CLI ``--peers``, MCP ``peers: true`` — and
    the injection hook has no way to pass it: :func:`brain.hooks.inject._inject_roots`
    builds its own curated list (notes/projects/reports) and never calls
    ``search.default_roots``, whose ``include_peers`` parameter is the only door.
    Both halves are asserted, because "it doesn't today" is not a guarantee.

    Needs no ripgrep: this is about which ROOTS are assembled, not what is found
    in them — which is also why it is not inside the rg-skipped class above.
    """

    def setUp(self):
        super().setUp()
        self.vault = self.make_vault(self.home / "vault")
        self.assertIn("TASK_STATION_BRAIN_VAULT", ENV_KEYS)
        os.environ["TASK_STATION_BRAIN_VAULT"] = str(self.vault)

    def _peer_note(self, alias, slug, body):
        d = self.peers_dir / alias / "notes"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{slug}.md").write_text(
            f"---\nname: {slug}\ndescription: d\nverified: 2026-01-01\n---\n\n{body}\n")

    def test_injection_never_includes_peers(self):
        self._peer_note("ada", "peeronly", "peeronly content")
        roots = inject._inject_roots(bconfig.load())
        self.assertTrue(roots, "the fixture vault should have injectable roots")
        for _t, p in roots:
            self.assertNotIn(str(self.peers_dir), str(p))

    def test_the_hook_never_reaches_the_only_door_to_the_peers_tier(self):
        # read the CODE, not the prose: the module's docstring names both of
        # these while explaining why it must not call them.
        tree = ast.parse(Path(inject.__file__).read_text())
        passed = {kw.arg for n in ast.walk(tree) if isinstance(n, ast.Call)
                  for kw in n.keywords}
        self.assertNotIn("include_peers", passed)
        called = {n.func.attr for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        self.assertNotIn("default_roots", called)


if __name__ == "__main__":
    unittest.main()
