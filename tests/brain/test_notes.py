"""brain.notes — the single write path for vault notes.

PROVENANCE: ported from the source's ``tests/test_note_io.py`` @ 0.14.0. Chunk 1
took the one note-semantics-free case out of ``FrontmatterRoundTripTest`` (the
hostile-scalar round trip) into ``tests/brain/test_frontmatter.py``; everything
here drives a real note file. Rewrites: module ``note_io`` -> ``brain.notes``, and
the fixture slug ``<org>-2704-balance`` -> ``story-2704-balance`` (same shape, no
org word). Assertions are unchanged.

Covers: slug/traversal validation, hostile-description YAML round-trips
(colons/quotes/emoji), each non-destructive mode preserving existing body bytes,
the agent-cannot-raise-trust rule, and one git commit per write against a temp
git fixture.

ADDED (each marked): the ``_FM_ORDER`` proof — a fixed input written through the
real writer, asserted byte-for-byte on disk — plus a layer guard for the three
modules this chunk adds. The key ORDER is this port's one silent contract:
``core.frontmatter`` takes the order as a parameter, so a bare call there emits
insertion order and reshuffles every note it writes, with nothing failing loudly.
"""
import subprocess
import unittest

from tests.brain.base import BrainTestCase, LIB
from tests.brain.test_layers import STDLIB_OK, top_level_imports

import brain.notes as notes


class SlugAndTraversalTest(BrainTestCase):
    def setUp(self):
        super().setUp()
        self.vault = self.make_vault(self.home / "vault")

    def test_valid_slugs_accepted(self):
        for good in ("abc", "a1", "story-2704-balance", "x" * 81):
            self.assertEqual(notes.validate_slug(good), good)

    def test_invalid_slugs_rejected(self):
        for bad in ("A-upper", "has space", "-leading", "with.dot", "a",
                    "", "x" * 82, "foo/bar", "../etc", "café"):
            with self.assertRaises(notes.NoteIOError):
                notes.validate_slug(bad)

    def test_traversal_slug_blocked_at_resolve(self):
        for evil in ("../../etc/passwd", "..", "foo/../../bar", "/abs/path"):
            with self.assertRaises(notes.NoteIOError):
                notes.resolve_note_path(self.vault, evil, "notes")

    def test_traversal_slug_blocked_at_write(self):
        with self.assertRaises(notes.NoteIOError):
            notes.write_note(self.vault, "../../escape", body="x", commit=False)
        # nothing written outside the vault
        self.assertFalse((self.home / "escape.md").exists())

    def test_unknown_folder_rejected(self):
        with self.assertRaises(notes.NoteIOError):
            notes.resolve_note_path(self.vault, "ok-slug", "org_brain")


class NoteRoundTripTest(BrainTestCase):
    """The two write-path cases of the source's ``FrontmatterRoundTripTest`` (its
    third, the pure scalar round trip, is in ``test_frontmatter.py``)."""

    def test_note_round_trip_hostile_description(self):
        vault = self.make_vault(self.home / "vault")
        desc = 'Dev SQL: use AllowAllWindowsAzureIps — "per-IP" rules only 🧠 {off-VPN}'
        notes.write_note(vault, "hostile-desc", description=desc,
                         body="body with [[wikilink]]", commit=False)
        text = (vault / "notes/hostile-desc.md").read_text()
        fm, body = notes.parse_note(text)
        self.assertEqual(fm["description"], desc)
        self.assertEqual(fm["name"], "hostile-desc")
        self.assertIn("[[wikilink]]", body)

    def test_emitted_frontmatter_is_yaml_safe(self):
        # a raw ': ' in a value must be quoted, never bare
        vault = self.make_vault(self.home / "vault")
        notes.write_note(vault, "colon-desc", description="a: b", commit=False)
        line = next(l for l in (vault / "notes/colon-desc.md").read_text().splitlines()
                    if l.startswith("description:"))
        self.assertEqual(line, 'description: "a: b"')


class FrontmatterKeyOrderTest(BrainTestCase):
    """ADDED — the chunk-1 handoff's silent contract, made loud.

    ``core.frontmatter`` owns the frontmatter SYNTAX and takes the key order as a
    parameter; ``brain.notes`` owns the note SCHEMA, so it must pass
    ``order=_FM_ORDER`` on every emit. Nothing raises if it doesn't — the notes
    just come out in insertion order. So this asserts the finished bytes for one
    fixed input, which pins the order and the emitter shape at once.
    """

    # Every ordered slot the note schema has a value for, plus one unknown key
    # (which must land in the tail, after everything _FM_ORDER names).
    EXPECTED = "\n".join([
        "---",
        "name: story-2704-balance",
        'description: "a: b"',
        "area: finance",
        "plane: knowledge",
        "type: reference",
        "publish: true",
        "promote: true",
        "verified: 2026-08-13",
        "source: manual",
        'tags: ["billing", "ledger"]',
        'contributors: [{"alias": "a", "ts": "2026-08-13", "extent": "created"}]',
        'provenance: ["task-station:42"]',
        "org_node: x",
        "org_rev: deadbeef",
        'tasks: ["task-station:42"]',
        "fetched: 2026-08-13",
        "zz-unknown: tail",
        "---",
        "",
        "the body",
    ]) + "\n"

    def _write(self):
        vault = self.make_vault(self.home / "vault")
        path = notes.write_note(
            vault, "story-2704-balance", description="a: b", body="the body",
            type="reference", publish=True, promote=True, source="manual",
            area="finance", plane="knowledge",
            tags=["billing", "ledger"],
            contributors=[{"alias": "a", "ts": "2026-08-13", "extent": "created"}],
            provenance=["task-station:42"],
            extra={"org_node": "x", "org_rev": "deadbeef",
                   "tasks": ["task-station:42"], "fetched": "2026-08-13",
                   "zz-unknown": "tail"},
            today="2026-08-13", commit=False)
        return path

    def test_written_note_is_byte_identical_for_a_fixed_input(self):
        self.assertEqual(self._write().read_text(), self.EXPECTED)

    def test_the_key_order_itself(self):
        # the same claim as above, isolated so a failure names the drift directly
        keys = [l.split(":", 1)[0] for l in self._write().read_text().splitlines()
                if l and l != "---" and not l[:1].isspace() and ":" in l]
        self.assertEqual(keys, [
            "name", "description", "area", "plane", "type", "publish", "promote",
            "verified", "source", "tags", "contributors", "provenance",
            "org_node", "org_rev", "tasks", "fetched", "zz-unknown"])

    def test_module_emitters_bind_the_note_key_order(self):
        """The tripwire itself: ``notes.dump_frontmatter`` / ``notes.render_note``
        must not be bare aliases of the core functions, or insertion order wins."""
        shuffled = {"source": "manual", "name": "n", "description": "d"}
        self.assertEqual(notes.dump_frontmatter(shuffled).splitlines()[1:4],
                         ["name: n", "description: d", "source: manual"])
        self.assertEqual(notes.render_note(shuffled, "b").splitlines()[1:4],
                         ["name: n", "description: d", "source: manual"])


class UpdateModeTest(BrainTestCase):
    def setUp(self):
        super().setUp()
        self.vault = self.make_vault(self.home / "vault")
        self.body = "ORIGINAL-MARKER line one\n\n## Notes\n\nkeep-this-content\n"
        notes.write_note(self.vault, "note", description="d", body=self.body, commit=False)
        self.path = self.vault / "notes/note.md"

    def test_create_fails_if_exists(self):
        with self.assertRaises(notes.NoteIOError):
            notes.write_note(self.vault, "note", description="d", body="x",
                             mode="create", commit=False)

    def test_append_preserves_body_bytes_and_adds_bullet(self):
        notes.write_note(self.vault, "note", body="a fresh fact", mode="append",
                         commit=False, today="2026-07-14")
        text = self.path.read_text()
        self.assertIn("ORIGINAL-MARKER line one", text)   # byte-diff: nothing lost
        self.assertIn("keep-this-content", text)
        self.assertIn("## Updates", text)
        self.assertIn("- 2026-07-14: a fresh fact", text)

    def test_merge_replaces_named_section_only(self):
        # add a new section, original body preserved
        notes.write_note(self.vault, "note", body="beta text", mode="merge",
                         section="Beta", commit=False)
        text = self.path.read_text()
        self.assertIn("keep-this-content", text)          # untouched section
        self.assertIn("## Beta", text)
        self.assertIn("beta text", text)
        # now replace the Notes section — its old content goes, others stay
        notes.write_note(self.vault, "note", body="new notes body", mode="merge",
                         section="Notes", commit=False)
        text2 = self.path.read_text()
        self.assertNotIn("keep-this-content", text2)
        self.assertIn("new notes body", text2)
        self.assertIn("beta text", text2)                 # Beta survived

    def test_replace_is_destructive(self):
        notes.write_note(self.vault, "note", body="totally new", mode="replace",
                         commit=False)
        text = self.path.read_text()
        self.assertNotIn("ORIGINAL-MARKER", text)
        self.assertIn("totally new", text)

    def test_update_honours_type_switches_source(self):
        notes.write_note(self.vault, "note", body="u", mode="append",
                         type="decision", publish=True, promote=True,
                         source="task-station:42", commit=False)
        fm, _ = notes.parse_note(self.path.read_text())
        self.assertEqual(fm["type"], "decision")
        self.assertEqual(fm["publish"], "true")
        self.assertEqual(fm["promote"], "true")
        self.assertEqual(fm["source"], "task-station:42")


class TrustIntegrityTest(BrainTestCase):
    def setUp(self):
        super().setUp()
        self.vault = self.make_vault(self.home / "vault")
        self.path = self.vault / "notes/human.md"
        self.path.write_text(
            "---\nname: human\ndescription: a human fact\ntype: reference\n"
            "verified: 2020-01-01\nverified-by: human\nsource: manual\n---\n\n"
            "the fact\n"
        )

    def test_agent_cannot_raise_trust(self):
        notes.write_note(self.vault, "human", body="agent addendum", mode="append",
                         actor="agent", commit=False, today="2026-07-14")
        fm, _ = notes.parse_note(self.path.read_text())
        self.assertEqual(fm["verified"], "2020-01-01")    # NOT bumped
        self.assertEqual(fm["verified-by"], "human")      # NOT downgraded

    def test_human_may_bump_trust(self):
        notes.write_note(self.vault, "human", body="human addendum", mode="append",
                         actor="human", commit=False, today="2026-07-14")
        fm, _ = notes.parse_note(self.path.read_text())
        self.assertEqual(fm["verified"], "2026-07-14")
        self.assertEqual(fm["verified-by"], "human")

    def test_agent_bumps_trust_on_non_human_note(self):
        # a note with no verified-by is not human-verified; agent may bump verified
        p = self.vault / "notes/agent.md"
        p.write_text("---\nname: agent\ndescription: d\ntype: reference\n"
                     "verified: 2020-01-01\nsource: manual\n---\n\nx\n")
        notes.write_note(self.vault, "agent", body="more", mode="append",
                         actor="agent", commit=False, today="2026-07-14")
        fm, _ = notes.parse_note(p.read_text())
        self.assertEqual(fm["verified"], "2026-07-14")


class GitCommitAtWriteTest(BrainTestCase):
    def _git(self, *args):
        return subprocess.run(["git", "-C", str(self.vault), *args],
                              capture_output=True, text=True)

    def setUp(self):
        super().setUp()
        self.vault = self.make_vault(self.home / "vault")
        self._git("init")
        self._git("config", "user.email", "t@example.com")
        self._git("config", "user.name", "Test")

    def _count(self):
        r = self._git("rev-list", "--count", "HEAD")
        return int(r.stdout.strip()) if r.returncode == 0 else 0

    def test_one_commit_per_write(self):
        self.assertEqual(self._count(), 0)
        notes.write_note(self.vault, "first", description="d", body="b", source="manual")
        self.assertEqual(self._count(), 1)
        notes.write_note(self.vault, "second", description="d", body="b", source="mcp")
        self.assertEqual(self._count(), 2)
        last = self._git("log", "-1", "--pretty=%s").stdout.strip()
        self.assertEqual(last, "note: create · second · mcp")

    def test_commit_only_touches_the_note(self):
        # an unrelated dirty file must not be swept into the note commit
        (self.vault / "unrelated.txt").write_text("dirty\n")
        notes.write_note(self.vault, "solo", description="d", body="b")
        status = self._git("status", "--porcelain").stdout
        self.assertIn("unrelated.txt", status)  # still uncommitted

    def test_non_git_vault_is_noop(self):
        plain = self.make_vault(self.home / "plain")  # no git init
        # must not raise even though commit=True (default)
        notes.write_note(plain, "ok", description="d", body="b")
        self.assertTrue((plain / "notes/ok.md").exists())


class NoopCommitTest(GitCommitAtWriteTest):
    def test_identical_rewrite_is_noop_not_failure(self):
        """A byte-identical rewrite yields git 'nothing to commit' — must be a
        silent no-op, not a NoteIOError (the tier-lint --apply crash case)."""
        notes.write_note(self.vault, "noop-case", description="d", body="same body",
                         source="manual")
        before = self._count()
        notes.write_note(self.vault, "noop-case", mode="replace", description="d",
                         body="same body", source="manual")
        self.assertEqual(self._count(), before)  # no new commit, no exception


class WriteMemoryNoteTest(BrainTestCase):
    """ADDED — the third writer in this module had no coverage in the source's
    ``test_note_io.py``; the tests that drive it live in the tier-lint suite
    (chunk 4). It is a WRITE PATH with its own containment check and its own
    frontmatter shape (the harness ``metadata: type:`` block, not the note
    schema), so it gets a behavioural test here rather than waiting for a caller.
    """

    def setUp(self):
        super().setUp()
        self.memory = self.home / "memory"
        self.memory.mkdir(parents=True)

    def test_memory_note_shape_is_the_harness_metadata_block(self):
        path = notes.write_memory_note(self.memory, "mem-note", description="d",
                                       body="the fact", mtype="reference",
                                       source="tier-lint", commit=False)
        self.assertEqual(path.read_text(), "\n".join([
            "---",
            "name: mem-note",
            "description: d",
            "metadata:",
            "  type: reference",
            "  source: tier-lint",
            "---",
            "",
            "the fact",
        ]) + "\n")

    def test_traversal_is_blocked_outside_the_memory_dir(self):
        with self.assertRaises(notes.NoteIOError):
            notes.write_memory_note(self.memory, "../escape", body="x", commit=False)
        self.assertFalse((self.home / "escape.md").exists())

    def test_existing_note_needs_overwrite(self):
        notes.write_memory_note(self.memory, "mem-note", body="first", commit=False)
        with self.assertRaises(notes.NoteIOError):
            notes.write_memory_note(self.memory, "mem-note", body="second", commit=False)
        notes.write_memory_note(self.memory, "mem-note", body="second",
                                overwrite=True, commit=False)
        self.assertIn("second", (self.memory / "mem-note.md").read_text())


# --------------------------------------------------------------------------- #
# ADDED — the two share switches. `notes.switch` is the ONE reader for the whole
# brain plane (publish, promote and tier-lint all call it), so its truthiness
# rule is pinned here rather than re-derived in each caller's suite.
# --------------------------------------------------------------------------- #
class SwitchReaderTest(unittest.TestCase):
    def test_absent_is_off(self):
        for key in notes.SWITCHES:
            with self.subTest(key=key):
                self.assertFalse(notes.switch({}, key))
                self.assertFalse(notes.switch({"name": "n"}, key))

    def test_only_a_literal_true_is_on(self):
        for raw in ("true", "True", "TRUE", " true ", '"true"', "'true'", True):
            with self.subTest(value=raw):
                self.assertTrue(notes.switch({"publish": raw}, "publish"))

    def test_everything_else_is_off(self):
        for raw in ("false", "False", "yes", "y", "1", 1, "on", "maybe", "tru",
                    "", None, [], {}, "team", "personal", "private"):
            with self.subTest(value=raw):
                self.assertFalse(notes.switch({"publish": raw}, "publish"))

    def test_the_two_switches_are_read_independently(self):
        fm = {"promote": "true"}
        self.assertFalse(notes.switch(fm, "publish"))
        self.assertTrue(notes.switch(fm, "promote"))


class SwitchWritePathTest(BrainTestCase):
    def setUp(self):
        super().setUp()
        self.vault = self.make_vault(self.home / "vault")

    def _fm(self, slug):
        fm, _ = notes.parse_note((self.vault / f"notes/{slug}.md").read_text())
        return fm

    def test_a_new_note_is_written_with_neither_switch(self):
        """Rule 2: no `publish: false` line, no `promote: false` line. A clean
        note is a private note, and there is no field to remember."""
        notes.write_note(self.vault, "fresh", description="d", body="b", commit=False)
        text = (self.vault / "notes/fresh.md").read_text()
        self.assertIn("name: fresh", text)        # it really did write a note
        self.assertNotIn("publish", text)
        self.assertNotIn("promote", text)

    def test_explicit_false_writes_no_field_either(self):
        notes.write_note(self.vault, "off", description="d", body="b",
                         publish=False, promote=False, commit=False)
        self.assertNotIn("publish", (self.vault / "notes/off.md").read_text())

    def test_true_writes_the_lowercase_literal(self):
        """Emitted as `true`, not python's `True` — a note is read by humans and
        by the mini-parser, and both should see the documented spelling."""
        notes.write_note(self.vault, "on", description="d", body="b",
                         publish=True, commit=False)
        self.assertIn("publish: true", (self.vault / "notes/on.md").read_text())
        self.assertTrue(notes.switch(self._fm("on"), "publish"))

    def test_an_update_that_says_nothing_leaves_the_switches_alone(self):
        notes.write_note(self.vault, "keep", description="d", body="b",
                         publish=True, commit=False)
        notes.write_note(self.vault, "keep", body="an addendum", mode="append",
                         commit=False)
        self.assertTrue(notes.switch(self._fm("keep"), "publish"))

    def test_an_update_can_turn_a_switch_off_by_removing_the_field(self):
        notes.write_note(self.vault, "flip", description="d", body="b",
                         publish=True, promote=True, commit=False)
        notes.write_note(self.vault, "flip", body="u", mode="append",
                         publish=False, commit=False)
        text = (self.vault / "notes/flip.md").read_text()
        self.assertNotIn("publish", text)         # removed, not set to false
        self.assertIn("promote: true", text)      # the other one is untouched


class BrainLayeringTest(unittest.TestCase):
    """ADDED — the layer rule for the three modules this chunk adds, read with
    ``ast`` (borrowing chunk 1's reader, which also sees function-local imports).
    ``tests/brain/test_layers.py`` guards core; this guards these files, and is
    left here rather than bolted onto that file so chunk 1's claims stay as they
    were reviewed. Relative sibling imports (``from . import naming``) are invisible
    to the reader by design — they cannot cross a layer.
    """

    FILES = ("brain/notes.py", "brain/naming.py", "brain/references.py")

    def test_no_module_reaches_the_board(self):
        for rel in self.FILES:
            self.assertNotIn("board", top_level_imports(LIB / rel), rel)

    def test_each_module_reaches_only_core_and_stdlib(self):
        for rel in self.FILES:
            path = LIB / rel
            self.assertTrue(path.exists(), f"{rel} missing")
            extra = top_level_imports(path) - STDLIB_OK - {"core"}
            self.assertEqual(extra, set(), f"{rel} reaches outside core+stdlib: {sorted(extra)}")


if __name__ == "__main__":
    unittest.main()
