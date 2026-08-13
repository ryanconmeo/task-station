"""brain.references — reference records (the task↔knowledge wire).

PROVENANCE: ported from the source's ``tests/test_references.py`` @ 0.14.0. Eleven
of its 14 cases are here 1:1; the three in ``SearchHintTest`` call
``brain.reference_hints`` — the search CLI, which lands in chunk 3 — so that class
is DEFERRED and named in the chunk-2 handoff (the source's module-level
``import brain`` went with it). Rewrites: module ``note_io`` -> ``brain.notes``,
``references`` -> ``brain.references``; the ``org_label`` fixture and the five
fixture-slug lines carrying an org word are neutral here.

Covers: ref add stub shape + one-line body; idempotent re-fetch (task appended,
org_rev/fetched bumped); ref list in both directions (--task / --node); dirty
detection against a fixture org-brain clone whose HEAD moved; and refresh.
"""
import subprocess
import unittest

from tests.brain.base import BrainTestCase

import brain.notes as notes
import brain.references as references


class RefFixtureMixin(BrainTestCase):
    def setUp(self):
        super().setUp()
        self.vault = self.make_vault(self.home / "vault")
        self.clone = self.home / "org_brain"
        self.cfg = {"vault": self.vault, "org_brain_clone": self.clone,
                    "org_label": "org brain"}

    def _git(self, *args):
        return subprocess.run(["git", "-C", str(self.clone), *args],
                              capture_output=True, text=True)

    def _init_clone(self, nodes):
        (self.clone / "notes").mkdir(parents=True, exist_ok=True)
        for slug, body in nodes.items():
            (self.clone / "notes" / f"{slug}.md").write_text(
                f"---\nname: {slug}\ndescription: d\n---\n\n{body}\n")
        self._git("init")
        self._git("config", "user.email", "t@e.com")
        self._git("config", "user.name", "T")
        self._git("add", "-A")
        self._git("commit", "-m", "init")

    def _head(self):
        return self._git("rev-parse", "HEAD").stdout.strip()


class RefAddTest(RefFixtureMixin):
    def test_add_creates_stub_shape(self):
        path = references.ref_add(self.cfg, "ledger-balance-sheet", "task-station:42",
                                  today="2026-07-24", org_rev="abc123")
        fm, body = notes.parse_note(path.read_text())
        self.assertEqual(path, self.vault / "references/ledger-balance-sheet.md")
        self.assertEqual(fm["name"], "ref-ledger-balance-sheet")
        self.assertEqual(fm["type"], "reference-record")
        self.assertEqual(fm["org_node"], "ledger-balance-sheet")
        self.assertEqual(fm["org_rev"], "abc123")
        self.assertEqual(fm["tasks"], ["task-station:42"])
        self.assertEqual(fm["fetched"], "2026-07-24")
        # body is ONE pointer line — never a copy of the org node
        self.assertIn("[[ledger-balance-sheet]]", body)
        self.assertEqual(len([l for l in body.splitlines() if l.strip()]), 1)

    def test_add_uses_clone_head_when_no_rev_given(self):
        self._init_clone({"node-a": "content"})
        references.ref_add(self.cfg, "node-a", "task-1", today="2026-07-24")
        fm, _ = notes.parse_note((self.vault / "references/node-a.md").read_text())
        self.assertEqual(fm["org_rev"], self._head())

    def test_add_idempotent_refetch(self):
        references.ref_add(self.cfg, "node-a", "task-1", today="2026-07-20", org_rev="rev1")
        references.ref_add(self.cfg, "node-a", "task-2", today="2026-07-24", org_rev="rev2")
        fm, _ = notes.parse_note((self.vault / "references/node-a.md").read_text())
        self.assertEqual(fm["tasks"], ["task-1", "task-2"])   # appended, not duplicated
        self.assertEqual(fm["org_rev"], "rev2")                # bumped
        self.assertEqual(fm["fetched"], "2026-07-24")
        # a repeat of the same task does not double-add
        references.ref_add(self.cfg, "node-a", "task-2", today="2026-07-25", org_rev="rev3")
        fm2, _ = notes.parse_note((self.vault / "references/node-a.md").read_text())
        self.assertEqual(fm2["tasks"], ["task-1", "task-2"])


class RefListTest(RefFixtureMixin):
    def setUp(self):
        super().setUp()
        references.ref_add(self.cfg, "node-a", "task-1", today="2026-07-24", org_rev="r")
        references.ref_add(self.cfg, "node-a", "task-2", today="2026-07-24", org_rev="r")
        references.ref_add(self.cfg, "node-b", "task-1", today="2026-07-24", org_rev="r")

    def test_list_all(self):
        rows = references.ref_list(self.cfg)
        self.assertEqual({r["org_node"] for r in rows}, {"node-a", "node-b"})

    def test_list_by_task(self):  # wire direction: task -> nodes
        rows = references.ref_list(self.cfg, task="task-2")
        self.assertEqual([r["org_node"] for r in rows], ["node-a"])
        rows = references.ref_list(self.cfg, task="task-1")
        self.assertEqual({r["org_node"] for r in rows}, {"node-a", "node-b"})

    def test_list_by_node(self):  # wire direction: node -> tasks
        rows = references.ref_list(self.cfg, node="node-a")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tasks"], ["task-1", "task-2"])


class DirtyDetectionTest(RefFixtureMixin):
    def test_dirty_when_org_node_moved(self):
        self._init_clone({"node-a": "v1", "node-b": "stable"})
        references.ref_add(self.cfg, "node-a", "t", today="2026-07-24")
        references.ref_add(self.cfg, "node-b", "t", today="2026-07-24")
        # advance the clone: change node-a only
        (self.clone / "notes/node-a.md").write_text(
            "---\nname: node-a\ndescription: d\n---\n\nv2 CHANGED\n")
        self._git("add", "-A")
        self._git("commit", "-m", "update node-a")
        dirty = {r["org_node"] for r in references.ref_list(self.cfg, dirty=True)}
        self.assertEqual(dirty, {"node-a"})                    # node-b untouched, not dirty
        self.assertTrue(references.is_dirty(self.cfg, "node-a",
                        notes.parse_note((self.vault / "references/node-a.md").read_text())[0]["org_rev"]))
        self.assertFalse(references.is_dirty(self.cfg, "node-b",
                         notes.parse_note((self.vault / "references/node-b.md").read_text())[0]["org_rev"]))

    def test_no_clone_never_dirty(self):
        references.ref_add(self.cfg, "node-a", "t", today="2026-07-24", org_rev="x")
        self.assertEqual(references.ref_list(self.cfg, dirty=True), [])


class StubShapeTest(RefFixtureMixin):
    """ADDED — ``stub_frontmatter``'s ``provenance`` argument is the collapse-to-
    reference shape: when the promote pipeline replaces a promoted node with its
    reference stub, the stub keeps the node's provenance. That pipeline lands in
    chunk 4, and the source's own cases never passed ``provenance`` (``ref add``
    does not), so the field shipped untested. The SHAPE is implemented here, so it
    is asserted here; the collapse ACTION is named as deferred in the handoff.
    """

    def test_provenance_is_carried_only_when_given(self):
        plain = references.stub_frontmatter("node-a", "rev", ["task-1"], "2026-07-24")
        self.assertNotIn("provenance", plain)
        collapsed = references.stub_frontmatter("node-a", "rev", ["task-1"], "2026-07-24",
                                                provenance=["task-station:42"])
        self.assertEqual(collapsed["provenance"], ["task-station:42"])

    def test_refresh_preserves_a_provenance_list_the_collapse_path_wrote(self):
        fm = references.stub_frontmatter("node-a", "old", ["task-1"], "2026-07-20",
                                         provenance=["task-station:42"])
        notes.write_note_fm(self.vault, "node-a", fm,
                            references.stub_body(self.cfg, "node-a"),
                            folder="references", source="ref", commit=False)
        references.ref_refresh(self.cfg, "node-a", today="2026-07-24", commit=False)
        got, _ = notes.parse_note((self.vault / "references/node-a.md").read_text())
        self.assertEqual(got["provenance"], ["task-station:42"])
        self.assertEqual(got["tasks"], ["task-1"])
        self.assertEqual(got["fetched"], "2026-07-24")


class RefRefreshTest(RefFixtureMixin):
    def test_refresh_clears_dirty_and_bumps_rev(self):
        self._init_clone({"node-a": "v1"})
        references.ref_add(self.cfg, "node-a", "task-1", today="2026-07-20")
        # advance the clone -> node-a goes dirty
        (self.clone / "notes/node-a.md").write_text(
            "---\nname: node-a\ndescription: d\n---\n\nv2\n")
        self._git("add", "-A"); self._git("commit", "-m", "update")
        self.assertEqual({r["org_node"] for r in references.ref_list(self.cfg, dirty=True)},
                         {"node-a"})
        references.ref_refresh(self.cfg, "node-a", today="2026-07-24")
        # no longer dirty; org_rev == current clone HEAD; fetched bumped
        self.assertEqual(references.ref_list(self.cfg, dirty=True), [])
        fm, _ = notes.parse_note((self.vault / "references/node-a.md").read_text())
        self.assertEqual(fm["org_rev"], self._head())
        self.assertEqual(fm["fetched"], "2026-07-24")

    def test_refresh_preserves_tasks_and_provenance(self):
        self._init_clone({"node-a": "v1"})
        references.ref_add(self.cfg, "node-a", "task-1", today="2026-07-20")
        references.ref_add(self.cfg, "node-a", "task-2", today="2026-07-20")
        references.ref_refresh(self.cfg, "node-a", today="2026-07-24")
        fm, _ = notes.parse_note((self.vault / "references/node-a.md").read_text())
        self.assertEqual(fm["tasks"], ["task-1", "task-2"])

    def test_refresh_missing_stub_raises(self):
        with self.assertRaises(notes.NoteIOError):
            references.ref_refresh(self.cfg, "no-such-node", today="2026-07-24")


if __name__ == "__main__":
    unittest.main()
