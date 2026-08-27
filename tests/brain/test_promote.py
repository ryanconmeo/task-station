"""brain.promote — the promote / upload-reconcile pipeline + collapse-to-reference.

Runs against a FIXTURE org-brain clone (a local git repo — no network). The forge
group (token/push/PR) is exercised via dry-run and via monkeypatched stubs;
nothing here reaches az or a remote.

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 4b) from the brain source tree's
``tests/test_promote.py`` @ 0.14.0. Ten of its eleven cases are here 1:1. The
eleventh, ``SkillHygieneTest``, reads ``skills/brain-promote/SKILL.md`` — a brain
SKILL asset that did not ship in this repo when chunk 4b landed, so it was
DEFERRED BY NAME rather than faked. **Chunk 5b shipped the skills and APPENDED it
below**, widened to every brain skill and with its needle list genericized; see
that class's own docstring.

Rewrites: module ``note_io`` -> ``brain.notes``, ``promote`` -> ``brain.promote``,
``forge`` -> ``core.forge``; the org product word in every fixture slug and in the
clone's domain registry is neutral here, and ``org_label`` is a generic display
string. The chunk-4b handoff carries the full table (and the arithmetic for the
one fixture whose naming findings depend on word counts).

ADDED here (no source counterpart): the collapse-to-reference ACTION
(``CollapseToReferenceTest``), the clone-INDEX best-effort writer, the queue
shape, the strip rules as pure functions, the local PR stamp, and the layer rule.
"""
import json
import re
import subprocess
import unittest
from unittest import mock

from tests.brain.base import BrainTestCase, LIB
from tests.brain.test_layers import STDLIB_OK, top_level_imports

import core.forge as forge
import brain.notes as notes
import brain.promote as promote


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def _init_repo(repo, branch="main"):
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "checkout", "-b", branch)
    _git(repo, "config", "user.email", "t@e.com")
    _git(repo, "config", "user.name", "T")


class PromoteFixture(BrainTestCase):
    """Vault + org-brain clone fixture. ZERO test methods — contributes no cases."""

    def setUp(self):
        super().setUp()
        self.vault = self.make_vault(self.home / "vault")
        self.clone = self.home / "org_brain"
        (self.clone / "notes").mkdir(parents=True, exist_ok=True)
        (self.clone / "INDEX.md").write_text("# INDEX\n")
        # The org brain owns the domain registry, and promote lands notes in THIS
        # clone — so the fixture carries one. `ledger` is an ORG word mapping to a
        # generic area, which is exactly the shape a real registry has (the
        # shipped contract must never carry an org word itself, and `ledger` is
        # absent from it — that absence is what makes this fixture meaningful).
        (self.clone / "schemas").mkdir(parents=True, exist_ok=True)
        (self.clone / "schemas" / "node-types.json").write_text(
            json.dumps({"domains": {"registry": {"ledger": "finance"}}}))
        _init_repo(self.clone)
        _git(self.clone, "add", "-A")
        _git(self.clone, "commit", "-m", "init")
        self.cfg = {"vault": self.vault, "org_brain_clone": self.clone,
                    "org_label": "org brain", "alias": "ryan",
                    "forge_target_branch": "main"}

    def _private(self, slug, *, promote="true", publish=None, type="reference",
                 desc="d", body="b", provenance=None, folder="notes"):
        """``promote`` defaults to ``"true"`` so the suites below that are about
        something else (reconcile, strip, finalize) get a promotable note.
        :class:`GateTest` is where the default-OFF property is asserted."""
        fm = {"name": slug, "description": desc, "type": type,
              "verified": "2026-01-01", "source": "manual"}
        if publish is not None:
            fm["publish"] = publish
        if promote is not None:
            fm["promote"] = promote
        if provenance:
            fm["provenance"] = provenance
        (self.vault / folder / f"{slug}.md").write_text(notes.render_note(fm, body))

    def _clone_node(self, slug, *, contributors=None, body="old body", desc="d"):
        fm = {"name": slug, "description": desc, "type": "reference",
              "verified": "2026-01-01"}
        if contributors is not None:
            fm["contributors"] = contributors
        (self.clone / "notes" / f"{slug}.md").write_text(notes.render_note(fm, body))
        _git(self.clone, "add", "-A")
        _git(self.clone, "commit", "-m", f"seed {slug}")


class GateTest(PromoteFixture):
    def test_promote_true_passes_the_gate(self):
        """Brief test 3 (the pass half)."""
        self._private("ledger-marked-note")
        res = promote.promote(self.cfg, "ledger-marked-note", dry_run=True)
        self.assertEqual(res["status"], "created")

    def test_a_note_without_the_switch_is_refused_by_name(self):
        """Brief test 3 (the refusal half). The message has to name the field the
        author must add, or they cannot act on it."""
        self._private("ai-unmarked-note", promote=None)
        res = promote.promote(self.cfg, "ai-unmarked-note", dry_run=True)
        self.assertEqual(res["status"], "gated")
        self.assertIn("promote: true", res["message"])
        self.assertIn("--unmarked", res["message"])

    def test_promote_without_publish_is_a_valid_promote(self):
        """Brief test 4 (the promote half). The two switches are independent: a
        note can go straight to the org brain with no shared-mirror stop, and
        promote must never require, imply, or add `publish:`."""
        self._private("ledger-org-only", promote="true", publish=None)
        res = promote.promote(self.cfg, "ledger-org-only", dry_run=True,
                              today="2026-07-24")
        self.assertEqual(res["status"], "created")
        source = (self.vault / "notes/ledger-org-only.md").read_text()
        self.assertNotIn("publish:", source)     # the source is untouched
        _git(self.clone, "checkout", "promote-ledger-org-only")
        fm, _ = notes.parse_note((self.clone / "notes/ledger-org-only.md").read_text())
        self.assertNotIn("publish", fm)          # and neither switch is carried over
        self.assertNotIn("promote", fm)

    def test_a_value_that_is_not_true_does_not_pass_the_gate(self):
        for raw in ("false", "yes", "1", "maybe"):
            with self.subTest(value=raw):
                self._private("ai-odd-note", promote=raw)
                res = promote.promote(self.cfg, "ai-odd-note", dry_run=True)
                self.assertEqual(res["status"], "gated")

    def test_true_is_read_in_any_case(self):
        # Asserted as "not gated" rather than "created": once the first case has
        # landed in the clone, a later near-named slug legitimately RECONCILES
        # instead — which is the merge-target machinery, not the gate.
        for i, raw in enumerate(("true", "True", "TRUE", '"true"')):
            with self.subTest(value=raw):
                slug = f"ledger-cased-note-{i}"
                self._private(slug, promote=raw)
                res = promote.promote(self.cfg, slug, dry_run=True)
                self.assertNotEqual(res["status"], "gated")
                self.assertIn(res["status"], ("created", "reconciled"))

    def test_unmarked_opt_in_allowed(self):
        self._private("ai-unmarked-note", promote=None)
        res = promote.promote(self.cfg, "ai-unmarked-note", allow_unmarked=True, dry_run=True)
        self.assertEqual(res["status"], "created")

    def test_an_unregistered_domain_gates_the_promote(self):
        """Severity, not advice: the slug landing in the org brain must resolve to
        a registered domain, and the refusal names the fix."""
        self._private("hammerspoon-dollar-expansion")
        res = promote.promote(self.cfg, "hammerspoon-dollar-expansion", dry_run=True)
        self.assertEqual(res["status"], "name-gated")
        self.assertTrue(any("unregistered-domain" in e for e in res["errors"]))
        self.assertFalse((self.clone / "notes/hammerspoon-dollar-expansion.md").exists())

    def test_a_warn_level_finding_rides_along_instead_of_gating(self):
        self._private("ledger-a-very-long-mechanism-explaining-subject")
        res = promote.promote(self.cfg, "ledger-a-very-long-mechanism-explaining-subject",
                              dry_run=True)
        self.assertEqual(res["status"], "created")
        self.assertTrue(any("subject-too-long" in w for w in res["warnings"]))


class CreateTest(PromoteFixture):
    def test_create_when_no_target(self):
        self._private("ledger-new-subject", desc="a fresh org fact", body="the body")
        res = promote.promote(self.cfg, "ledger-new-subject", dry_run=True, today="2026-07-24")
        self.assertEqual(res["status"], "created")
        self.assertEqual(res["action"], "create")
        self.assertEqual(res["branch"], "promote-ledger-new-subject")
        # the note landed on the promote branch in the clone
        _git(self.clone, "checkout", "promote-ledger-new-subject")
        fm, body = notes.parse_note((self.clone / "notes/ledger-new-subject.md").read_text())
        self.assertEqual(fm["type"], "reference")
        self.assertNotIn("publish", fm)        # personal keys stripped —
        self.assertNotIn("promote", fm)        # the org copy carries no switch
        self.assertNotIn("source", fm)
        self.assertEqual(fm["verified"], "2026-07-24")
        self.assertEqual(fm["contributors"], [{"alias": "ryan", "ts": "2026-07-24",
                                               "extent": "created"}])


class ReconcileTest(PromoteFixture):
    def test_reconcile_merges_contributors_and_replaces_body(self):
        self._clone_node("ledger-balance-sheet",
                         contributors=[{"alias": "sam", "ts": "2026-01-01", "extent": "created"}],
                         body="OLD org body")
        self._private("ledger-balance-sheet", desc="d", body="NEW private body")
        res = promote.promote(self.cfg, "ledger-balance-sheet", extent="major",
                              dry_run=True, today="2026-07-24")
        self.assertEqual(res["status"], "reconciled")
        self.assertEqual(res["action"], "reconcile")
        _git(self.clone, "checkout", "promote-ledger-balance-sheet")
        fm, body = notes.parse_note((self.clone / "notes/ledger-balance-sheet.md").read_text())
        aliases = {c["alias"] for c in fm["contributors"]}
        self.assertEqual(aliases, {"sam", "ryan"})     # cumulative
        sam = next(c for c in fm["contributors"] if c["alias"] == "sam")
        self.assertEqual(sam["extent"], "created")      # original creation preserved
        self.assertIn("NEW private body", body)         # body replaced
        self.assertNotIn("OLD org body", body)
        self.assertEqual(fm["verified"], "2026-07-24")  # bumped


class StripTest(PromoteFixture):
    def test_strip_rules_applied(self):
        self._private("ledger-strip-me", type="decision",
                      body="path /Users/ryan/secret/x and session "
                           "3fa85f64-5717-4562-b3fc-2c963f66afa6 here")
        promote.promote(self.cfg, "ledger-strip-me", dry_run=True, today="2026-07-24")
        _git(self.clone, "checkout", "promote-ledger-strip-me")
        fm, body = notes.parse_note((self.clone / "notes/ledger-strip-me.md").read_text())
        self.assertEqual(fm["type"], "reference")       # local-only type remapped
        self.assertNotIn("/Users/", body)               # home path redacted
        self.assertIn("<local-path>", body)
        self.assertNotIn("3fa85f64", body)              # session id redacted
        self.assertIn("<session-id>", body)


class QueueFallbackTest(BrainTestCase):
    def test_queue_when_no_clone(self):
        vault = self.make_vault(self.home / "vault")
        cfg = {"vault": vault, "org_brain_clone": None, "alias": "ryan",
               "org_label": "org brain"}
        # no clone ⇒ no org registry, so the slug must lead with a GENERIC area
        fm = {"name": "ai-q-note", "description": "d", "type": "reference",
              "promote": "true", "verified": "2026-01-01"}
        (vault / "notes/ai-q-note.md").write_text(notes.render_note(fm, "queued body"))
        res = promote.promote(cfg, "ai-q-note", dry_run=True, today="2026-07-24")
        self.assertEqual(res["status"], "queued")
        queued = (vault / "notes/_org_brain-queue.md").read_text()
        self.assertIn("ai-q-note", queued)
        self.assertIn("queued body", queued)


class FinalizeTest(PromoteFixture):
    def test_finalize_collapses_to_reference_with_history_intact(self):
        # vault must be a git repo so history survives the collapse
        _init_repo(self.vault)
        self._private("ledger-done-node", provenance=["task-station:42", "task-station:57"])
        _git(self.vault, "add", "-A")
        _git(self.vault, "commit", "-m", "seed note")
        # move the clone HEAD so a merged sha exists
        _git(self.clone, "commit", "--allow-empty", "-m", "merged")
        merged = _git(self.clone, "rev-parse", "HEAD").stdout.strip()

        res = promote.finalize(self.cfg, "ledger-done-node", today="2026-07-24")
        self.assertEqual(res["status"], "finalized")
        fm, body = notes.parse_note((self.vault / "notes/ledger-done-node.md").read_text())
        self.assertEqual(fm["type"], "reference-record")
        self.assertEqual(fm["org_node"], "ledger-done-node")
        self.assertEqual(fm["org_rev"], merged)
        self.assertEqual(fm["provenance"], ["task-station:42", "task-station:57"])
        self.assertIn("[[ledger-done-node]]", body)     # one-line pointer
        # full private history retained (seed + collapse = >=2 commits for the file)
        log = _git(self.vault, "log", "--oneline", "--", "notes/ledger-done-node.md").stdout
        self.assertGreaterEqual(len(log.strip().splitlines()), 2)


class ForgeOpsTest(PromoteFixture):
    def test_push_and_pr_via_forge_adapter(self):
        # no forge_kind set -> defaults to the ado adapter
        self._private("ledger-forge-node", body="b")
        cfg = dict(self.cfg, forge_org="https://example.org", forge_project="Proj",
                   forge_repo="org_brain")
        calls = {}
        with mock.patch.object(forge.ado, "push_branch",
                               side_effect=lambda clone, branch, c: calls.setdefault("push", branch)), \
             mock.patch.object(forge.ado, "open_pr",
                               return_value="https://example.org/pr/1"):
            res = promote.promote(cfg, "ledger-forge-node", today="2026-07-24")
        self.assertEqual(res["pr_url"], "https://example.org/pr/1")
        self.assertEqual(calls["push"], "promote-ledger-forge-node")


# --------------------------------------------------------------------------- #
# ADDED — the collapse-to-reference ACTION.
#
# Chunk 2 ported the stub SHAPE (references.stub_frontmatter, incl. its
# `provenance=` argument) and pinned it with `StubShapeTest`. The ACTION — a
# merged private node being REPLACED, in place, by that stub — lives here, in
# `promote.finalize`, and the source's single FinalizeTest asserts only the happy
# path on a note that HAS provenance. Everything below is a distinct way for the
# collapse to go silently wrong: a body that was appended rather than replaced
# still reads fine, an untagged INDEX line still resolves, and a stub written with
# an empty `provenance: []` still parses.
# --------------------------------------------------------------------------- #
class CollapseToReferenceTest(PromoteFixture):
    def _finalize(self, slug, **kw):
        kw.setdefault("today", "2026-07-24")
        return promote.finalize(self.cfg, slug, **kw)

    def test_the_private_body_is_replaced_not_appended(self):
        """Collapse means REPLACE. An append would leave the full private prose in
        a note whose frontmatter now claims to be a pointer — the exact leak the
        stub exists to prevent, and invisible to a test that only checks the
        pointer is present."""
        self._private("ledger-collapse-me", body="the whole long private explanation")
        self._finalize("ledger-collapse-me", org_rev="abc12345def")
        fm, body = notes.parse_note((self.vault / "notes/ledger-collapse-me.md").read_text())
        self.assertNotIn("the whole long private explanation", body)
        lines = [ln for ln in body.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1)
        self.assertIn("[[ledger-collapse-me]]", lines[0])
        self.assertEqual(fm["type"], "reference-record")

    def test_provenance_becomes_both_provenance_and_tasks(self):
        """The private node's task handles are carried into BOTH fields: promote
        passes the provenance list as the stub's ``tasks`` positional AND as its
        ``provenance=`` keyword. A stub that lost ``tasks`` would drop the node out
        of ``ref list --task`` while still looking correct on screen."""
        self._private("ledger-two-tasks", provenance=["task-station:42", "task-station:57"])
        self._finalize("ledger-two-tasks", org_rev="abc12345def")
        fm, _ = notes.parse_note((self.vault / "notes/ledger-two-tasks.md").read_text())
        self.assertEqual(fm["provenance"], ["task-station:42", "task-station:57"])
        self.assertEqual(fm["tasks"], ["task-station:42", "task-station:57"])

    def test_a_node_without_provenance_gets_no_provenance_key(self):
        """`provenance=prov or None` — an empty list must not be written as an
        empty key. `tasks:` still appears (it is unconditional in the stub)."""
        self._private("ledger-no-prov")
        self._finalize("ledger-no-prov", org_rev="abc12345def")
        fm, _ = notes.parse_note((self.vault / "notes/ledger-no-prov.md").read_text())
        self.assertNotIn("provenance", fm)
        self.assertEqual(fm["tasks"], [])

    def test_the_vault_index_line_is_tagged_once(self):
        (self.vault / "INDEX.md").write_text(
            "# INDEX\n\n- [[ledger-indexed]] — d\n- [[ledger-other]] — d\n")
        self._private("ledger-indexed")
        self._finalize("ledger-indexed", org_rev="abc12345def")
        text = (self.vault / "INDEX.md").read_text()
        self.assertIn("- [[ledger-indexed]] — d  (→ org reference)", text)
        self.assertNotIn("[[ledger-other]] — d  (→", text)   # only the collapsed line
        # a second finalize must not double-tag the same line
        self._finalize("ledger-indexed", org_rev="abc12345def")
        self.assertEqual((self.vault / "INDEX.md").read_text().count("(→ org reference)"), 1)

    def test_explicit_org_node_and_rev_win_and_the_file_stays_put(self):
        """`--org-node` points the stub at a differently-named org node; the FILE
        keeps the private slug, so the vault's own wikilinks still resolve."""
        self._private("ledger-local-name")
        res = self._finalize("ledger-local-name", org_node="ledger-org-name",
                             org_rev="deadbeefcafe")
        self.assertEqual(res["org_node"], "ledger-org-name")
        self.assertEqual(res["org_rev"], "deadbeefcafe")
        path = self.vault / "notes/ledger-local-name.md"
        self.assertTrue(path.exists())
        fm, body = notes.parse_note(path.read_text())
        self.assertEqual(fm["name"], "ref-ledger-org-name")
        self.assertEqual(fm["org_node"], "ledger-org-name")
        self.assertEqual(fm["org_rev"], "deadbeefcafe")
        self.assertIn("[[ledger-org-name]]", body)

    def test_a_projects_node_collapses_in_place(self):
        """`folder = src_path.parent.name` — a projects/ node must not be
        resurrected under notes/, which would leave two files for one fact."""
        self._private("ledger-project-node", folder="projects")
        self._finalize("ledger-project-node", org_rev="abc12345def")
        self.assertTrue((self.vault / "projects/ledger-project-node.md").exists())
        self.assertFalse((self.vault / "notes/ledger-project-node.md").exists())

    def test_the_collapse_is_logged(self):
        self._private("ledger-logged")
        self._finalize("ledger-logged", org_rev="abc12345def")
        log = (self.vault / "LOG.md").read_text()
        self.assertIn("promote", log)
        self.assertIn("ledger-logged finalized -> reference to ledger-logged", log)

    def test_finalize_of_a_missing_slug_raises(self):
        with self.assertRaises(promote.PromoteError):
            self._finalize("ledger-not-here")


# --------------------------------------------------------------------------- #
# ADDED — the two best-effort index writers and the queue, none of which the
# source exercises. All three fail SILENTLY by design (an absent INDEX is a
# no-op, a duplicate line is skipped), so a regression looks like a clean run.
# --------------------------------------------------------------------------- #
class CloneIndexTest(PromoteFixture):
    def test_a_new_node_is_appended_to_the_clone_index(self):
        self._private("ledger-new-subject", desc="a fresh org fact")
        promote.promote(self.cfg, "ledger-new-subject", dry_run=True, today="2026-07-24")
        text = (self.clone / "INDEX.md").read_text()
        self.assertIn("- [[ledger-new-subject]] — a fresh org fact", text)

    def test_an_already_listed_node_is_not_duplicated(self):
        (self.clone / "INDEX.md").write_text("# INDEX\n\n- [[ledger-known]] — old text\n")
        promote._update_clone_index(self.clone, "ledger-known", "new text")
        text = (self.clone / "INDEX.md").read_text()
        self.assertEqual(text.count("[[ledger-known]]"), 1)
        self.assertIn("old text", text)        # the existing line is left alone
        self.assertNotIn("new text", text)

    def test_a_clone_without_an_index_is_a_no_op(self):
        bare = self.home / "no-index-clone"
        bare.mkdir()
        self.assertIsNone(promote._update_clone_index(bare, "ledger-x", "d"))
        self.assertFalse((bare / "INDEX.md").exists())


class QueueShapeTest(BrainTestCase):
    def setUp(self):
        super().setUp()
        self.vault = self.make_vault(self.home / "vault")
        self.cfg = {"vault": self.vault, "org_brain_clone": None, "alias": "ryan",
                    "org_label": "org brain"}

    def _private(self, slug, body="b"):
        fm = {"name": slug, "description": "d", "type": "decision", "promote": "true",
              "publish": "true", "verified": "2026-01-01", "source": "manual"}
        (self.vault / "notes" / f"{slug}.md").write_text(notes.render_note(fm, body))

    def test_the_queue_header_is_written_once(self):
        self._private("ai-q-note")
        promote.promote(self.cfg, "ai-q-note", dry_run=True, today="2026-07-24")
        promote.promote(self.cfg, "ai-q-note", dry_run=True, today="2026-07-25")
        text = (self.vault / "notes/_org_brain-queue.md").read_text()
        self.assertEqual(text.count("# org brain promote queue"), 1)
        self.assertEqual(text.count("## Queued"), 2)

    def test_the_queued_block_holds_the_stripped_org_schema_note(self):
        """The queue is what a human will paste into the org brain by hand, so it
        must already be stripped — queuing the PRIVATE note would move the leak
        rather than block it."""
        self._private("ai-q-strip", body="see /Users/ryan/secret/x for detail")
        promote.promote(self.cfg, "ai-q-strip", dry_run=True, today="2026-07-24")
        text = (self.vault / "notes/_org_brain-queue.md").read_text()
        self.assertIn("<local-path>", text)
        self.assertNotIn("/Users/", text)
        self.assertNotIn("publish:", text)        # personal keys never queued
        self.assertNotIn("promote:", text)        # (neither share switch survives)
        self.assertNotIn("source:", text)
        self.assertIn("contributors:", text)      # org schema present
        self.assertIn("type: reference", text)    # local-only type remapped


# --------------------------------------------------------------------------- #
# ADDED — the strip rules as pure functions. `promote()` exercises one path
# through them; these pin each rule so a regression names itself.
# --------------------------------------------------------------------------- #
class StripRulesTest(unittest.TestCase):
    def test_org_types_survive_and_local_only_types_remap(self):
        for t in promote.ORG_BRAIN_TYPES:
            self.assertEqual(promote.remap_type(t), t, t)
        for t in ("decision", "hub", "rule", "reference-record", ""):
            self.assertEqual(promote.remap_type(t), "reference", t)

    def test_personal_keys_are_dropped_by_not_being_copied(self):
        fm = {"name": "n", "description": "d", "type": "gotcha", "scope": "team",
              "source": "manual", "verified": "2026-01-01", "verified-by": "human",
              "org_brain": "https://example.org/pr/1", "provenance": ["task-station:1"],
              "org_node": "x", "tags": ["t"]}
        out = promote.strip_frontmatter(fm, verified="2026-07-24", contributors=[])
        self.assertEqual(set(out), {"name", "description", "type", "verified",
                                    "tags", "contributors"})
        self.assertEqual(out["verified"], "2026-07-24")     # bumped, not copied
        self.assertEqual(out["type"], "gotcha")

    def test_tags_ride_along_only_as_a_non_empty_list(self):
        base = {"name": "n", "description": "d", "type": "reference"}
        self.assertEqual(
            promote.strip_frontmatter(dict(base, tags=["a", "b"]), verified="d",
                                      contributors=[])["tags"], ["a", "b"])
        for bad in ([], "a-string", None):
            out = promote.strip_frontmatter(dict(base, tags=bad), verified="d",
                                            contributors=[])
            self.assertNotIn("tags", out, repr(bad))

    def test_both_home_path_flavours_and_session_ids_redact(self):
        out = promote.strip_body(
            "mac /Users/ada/x/y linux /home/ada/x/y "
            "session 3fa85f64-5717-4562-b3fc-2c963f66afa6 "
            "short 3fa85f64 stays")
        self.assertNotIn("/Users/", out)
        self.assertNotIn("/home/", out)
        self.assertEqual(out.count("<local-path>"), 2)
        self.assertIn("<session-id>", out)
        self.assertIn("short 3fa85f64 stays", out)   # a bare hex run is not a UUID

    def test_strip_body_tolerates_an_empty_body(self):
        self.assertEqual(promote.strip_body(None), "")
        self.assertEqual(promote.strip_body(""), "")


# --------------------------------------------------------------------------- #
# ADDED — the write-back onto the LOCAL note after a PR opens. The source's
# ForgeOpsTest asserts the PR url comes back; nothing asserts the private note
# records it, which is the half a human later reads to find the PR.
# --------------------------------------------------------------------------- #
class LocalStampTest(PromoteFixture):
    FORGE = {"forge_org": "https://example.org", "forge_project": "Proj",
             "forge_repo": "org_brain"}

    def test_a_successful_pr_stamps_the_private_note(self):
        self._private("ledger-stamped", body="b")
        cfg = dict(self.cfg, **self.FORGE)
        with mock.patch.object(forge.ado, "push_branch", return_value=True), \
             mock.patch.object(forge.ado, "open_pr", return_value="https://example.org/pr/7"):
            promote.promote(cfg, "ledger-stamped", today="2026-07-24")
        fm, body = notes.parse_note((self.vault / "notes/ledger-stamped.md").read_text())
        self.assertEqual(fm["org_brain"], "https://example.org/pr/7")
        self.assertIn("## org brain", body)
        self.assertIn("Promoted: https://example.org/pr/7", body)

    def test_a_dry_run_never_stamps_even_with_a_configured_forge(self):
        self._private("ledger-unstamped", body="b")
        cfg = dict(self.cfg, **self.FORGE)
        res = promote.promote(cfg, "ledger-unstamped", dry_run=True, today="2026-07-24")
        self.assertIsNone(res["pr_url"])
        fm, body = notes.parse_note((self.vault / "notes/ledger-unstamped.md").read_text())
        self.assertNotIn("org_brain", fm)
        self.assertNotIn("## org brain", body)


class PromotePublishLayeringTest(unittest.TestCase):
    """ADDED — the layer rule for the two modules this chunk adds, read with
    ``ast`` (chunk 1's reader, which also sees function-local imports). Each chunk
    keeps its own copy so its claims stay reviewable on their own. Relative
    sibling imports (``from . import config``) are invisible to the reader by
    design — they cannot cross a layer.

    Unlike chunk 4a's HealLayeringTest this needs NO local widening of
    ``STDLIB_OK``: both modules stay inside chunk 1's set, and ``core`` is here
    for the FIRST brain-plane use of ``core.forge``.
    """

    FILES = ("brain/promote.py", "brain/publish.py")

    def test_no_module_reaches_the_board(self):
        for rel in self.FILES:
            self.assertNotIn("board", top_level_imports(LIB / rel), rel)

    def test_each_module_reaches_only_core_and_stdlib(self):
        for rel in self.FILES:
            path = LIB / rel
            self.assertTrue(path.exists(), f"{rel} missing")
            extra = top_level_imports(path) - STDLIB_OK - {"core"}
            self.assertEqual(extra, set(), f"{rel} reaches outside core+stdlib: {sorted(extra)}")

    def test_only_promote_reaches_core(self):
        """publish is sibling+stdlib only; promote is the brain plane's first
        core.forge consumer. Stated as a test so a stray core import in publish is
        a decision, not a drift."""
        self.assertIn("core", top_level_imports(LIB / "brain/promote.py"))
        self.assertNotIn("core", top_level_imports(LIB / "brain/publish.py"))


class SkillHygieneTest(unittest.TestCase):
    """APPENDED by chunk 5b — the case chunk 4b deferred (D49), widened.

    The source asserted ONE thing about ONE file: that ``brain-promote``'s skill
    no longer carried the token/push/PR mechanics after they moved into
    ``promote.py``. The claim generalises — a skill is prose a model follows, so
    anything operational it spells out is a copy that drifts from the code — so
    it now reads every brain skill this repo ships.

    THE NEEDLE LIST IS GENERICIZED. Two of the source's eight needles were one
    organisation's words; they are replaced by what these skills genuinely must
    not contain: a module invoked by its retired ``scripts/<file>.py`` path, and a
    module name that no longer exists. **The org-term half stays a ripgrep**, as
    in every prior chunk (5a §6.2): writing those words here as string literals
    would plant in the tree the exact spellings the scrub sweep exists to keep
    out. Old spellings are described, never written.

    Plain ``TestCase``: this reads shipped files and needs no temp home.
    """

    SKILLS = ("brain", "brain-heal", "brain-init", "brain-promote", "brain-save", "ado")

    # Forge mechanics + one developer's local clone path. Six of the source's
    # eight needles, unchanged: these belong in brain.promote, never in prose.
    FORGE_MECHANICS = ("az account", "az repos", "http.extraheader", "499b84ac",
                       "dev.azure.com", "Workspace/org_brain")

    # The two REPLACEMENT needles' first half: every module the 3.0.0 port
    # renamed, in the spelling a stale skill would still carry. ``scripts/``
    # catches the path form on its own; the rest catch a bare filename.
    RETIRED_PATHS = ("scripts/", "brain.py", "note_io", "reconcile-gate.py",
                     "tier_lint.py", "lint.py", "ingest_artifacts.py",
                     "auto-distill.py", "org-brain-pull.py", "subscriptions.py",
                     "mcp_server.py", "init_home.py", "publish_setup.py",
                     "promote.py", "publish.py", "ado_tree.py", "ado_resolve.py")

    _MODULE_RX = re.compile(r"\bbrain\.([a-z_][a-z0-9_]*)")
    # A skill the SessionStart routing document routes to, e.g. ``brain-save``.
    _ROUTED_RX = re.compile(r"\bbrain-[a-z]+(?:-[a-z]+)*\b")

    def _skill(self, name):
        path = LIB.parent / "skills" / name / "SKILL.md"
        self.assertTrue(path.exists(), f"skills/{name}/SKILL.md missing")
        return path

    def test_skills_carry_no_forge_mechanics_or_local_clone_paths(self):
        for name in self.SKILLS:
            text = self._skill(name).read_text()
            for bad in self.FORGE_MECHANICS:
                self.assertNotIn(bad, text,
                                 f"skills/{name} still carries forbidden literal {bad!r}")

    def test_no_skill_invokes_a_module_by_its_retired_path(self):
        """The 3.0.0 port turned every ``scripts/<file>.py`` invocation into
        ``python3 -m brain.<module>``. A skill that missed the rewrite reads
        perfectly and runs nothing."""
        for name in self.SKILLS:
            text = self._skill(name).read_text()
            for bad in self.RETIRED_PATHS:
                self.assertNotIn(bad, text,
                                 f"skills/{name} still invokes {bad!r} — rewrite it to the -m form")

    def test_every_brain_module_a_skill_names_exists(self):
        named = set()
        for name in self.SKILLS:
            text = self._skill(name).read_text()
            for mod in self._MODULE_RX.findall(text):
                named.add(mod)
                self.assertTrue((LIB / "brain" / f"{mod}.py").exists(),
                                f"skills/{name} names brain.{mod}, which does not exist")
        # a rewrite that deleted the invocations entirely would pass vacuously
        self.assertIn("search", named)

    def test_every_skill_frontmatter_names_its_own_directory(self):
        for name in self.SKILLS:
            head = self._skill(name).read_text().split("---", 2)[1]
            self.assertIn(f"name: {name}\n", head,
                          f"skills/{name} frontmatter does not declare `name: {name}`")
            self.assertIn("description:", head, f"skills/{name} has no description")

    def test_the_routing_document_ships_and_routes_only_to_skills_that_exist(self):
        """The SessionStart hook injects ``$CLAUDE_PLUGIN_ROOT/system-instructions.md``
        verbatim (5a's ``inject._routing_text``), and injects NOTHING when it is
        missing — silently, by design. So the only thing that can notice the
        document is absent, or that it routes a user at a skill this repo does not
        ship, is a test. Both halves are here."""
        doc = LIB.parent / "system-instructions.md"
        self.assertTrue(doc.exists(),
                        "system-instructions.md must sit at the plugin root — it is what "
                        "inject._routing_text() reads, and without it SessionStart ships "
                        "the orientation line with no routing rules")
        text = doc.read_text()
        self.assertTrue(text.strip(), "the routing document is empty")
        routed = {n for n in self._ROUTED_RX.findall(text) if n != "brain-station"}
        self.assertTrue(routed, "the routing document names no skill at all")
        for name in sorted(routed):
            self.assertIn(name, self.SKILLS,
                          f"the routing document routes to /{name}, which does not ship")


if __name__ == "__main__":
    unittest.main()
