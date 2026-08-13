"""The naming contract wired into the WRITE path (brain.notes + brain.naming).

PROVENANCE: ported from the source's ``tests/test_naming_write_path.py`` @ 0.14.0.
That file has 34 cases in six classes; 15 of them exercise the two modules this
chunk owns and are ported below 1:1. The other 19 drive the search CLI
(``brain.py new``) and the lint pass as SUBPROCESSES — those modules land in
chunks 3 and 4, so their classes are DEFERRED and named in the chunk-2 handoff
rather than faked here.

Rewrites: modules ``note_io`` -> ``brain.notes``, ``naming`` -> ``brain.naming``;
``ShimIsUnusedTest`` scans ``lib/`` (this repo's shipped code) where the source
scanned its ``scripts/``. No fixture needed genericizing — all six of the source
file's org-term hits sit in the deferred CLI classes.

Two things are covered here, and each exists because the contract was DATA with
nothing acting on it:

  * ``area:``/``plane:`` — the org schema requires both on every knowledge node
    and nothing created them, so every node written since the schema landed was
    silently non-conforming.
  * severity — ``slug_findings`` carries error-vs-warn, and the callers act on the
    difference instead of flattening it into a warning.
"""
import json
import unittest
from pathlib import Path

from tests.brain.base import BrainTestCase, LIB

import brain.naming as naming
import brain.notes as notes


class KnowledgeStampTest(BrainTestCase):
    """``notes.knowledge_stamp`` — the single derivation of area:/plane:."""

    def setUp(self):
        super().setUp()
        self.c = naming.load_contract()

    def test_area_is_derived_from_the_domain_not_asked_for(self):
        self.assertEqual(notes.knowledge_stamp("finance-ap-invoice-approval",
                                               contract=self.c),
                         {"area": "finance", "plane": "knowledge"})

    def test_a_two_word_domain_resolves_before_its_first_word(self):
        """REGRESSION guard: `task` is not a domain, `task-station` is."""
        self.assertEqual(notes.knowledge_stamp("task-station-worker-crash",
                                               contract=self.c)["area"],
                         "task-station")

    def test_projects_are_knowledge_nodes_too(self):
        self.assertEqual(notes.knowledge_stamp("repo-pr-isdraft", "projects",
                                               contract=self.c),
                         {"area": "repo", "plane": "knowledge"})

    def test_an_unregistered_domain_refuses_rather_than_writing_unstamped(self):
        with self.assertRaises(notes.NoteIOError) as e:
            notes.knowledge_stamp("hammerspoon-dollar-expansion", contract=self.c)
        self.assertIn("unstamped", str(e.exception))

    def test_the_refusal_names_the_nearest_registered_domain(self):
        with self.assertRaises(notes.NoteIOError) as e:
            notes.knowledge_stamp("financ-ap-invoice", contract=self.c)
        self.assertIn("finance", str(e.exception))

    def test_projects_refuse_too_even_though_the_slug_gate_skips_them(self):
        """``domainRequiredIn`` covers which NAMES must carry a domain; the stamp
        is about the FIELD, which every knowledge node carries. projects/ is not
        in domainRequiredIn, so this is a genuinely separate gate."""
        self.assertNotIn("projects", self.c["domainRequiredIn"])
        self.assertEqual(naming.slug_findings("hammerspoon-thing", "projects", self.c), [])
        with self.assertRaises(notes.NoteIOError):
            notes.knowledge_stamp("hammerspoon-thing", "projects", contract=self.c)

    def test_a_dated_artifact_folder_is_stamped_with_neither(self):
        for folder in ("reports", "plans", "raw", "references"):
            self.assertEqual(notes.knowledge_stamp("2026-08-03-lint", folder,
                                                   contract=self.c), {},
                             "%s/ holds artifacts, not knowledge nodes" % folder)

    def test_an_explicit_area_overrides_the_derivation(self):
        s = notes.knowledge_stamp("finance-ap-invoice", contract=self.c,
                                  area="risk", plane="control")
        self.assertEqual(s, {"area": "risk", "plane": "control"})

    def test_an_explicit_area_rescues_an_unregistered_domain(self):
        """The override is the escape hatch, so a refusal never costs the fact."""
        self.assertEqual(notes.knowledge_stamp("hammerspoon-thing", contract=self.c,
                                               area="it"),
                         {"area": "it", "plane": "knowledge"})

    def test_an_org_domain_resolves_through_the_org_brain_clone(self):
        clone = Path(self.home) / "orgbrain"
        (clone / "schemas").mkdir(parents=True)
        (clone / "schemas" / "node-types.json").write_text(json.dumps(
            {"domains": {"registry": {"acme": "product"}}}))
        c = naming.load_contract(org_brain_clone=str(clone))
        self.assertEqual(notes.knowledge_stamp("acme-widget-tolerance", contract=c),
                         {"area": "product", "plane": "knowledge"})
        # ...and the same slug is refused without it — the org half never ships
        with self.assertRaises(notes.NoteIOError):
            notes.knowledge_stamp("acme-widget-tolerance", contract=self.c)


class FrontmatterOrderTest(BrainTestCase):
    def setUp(self):
        super().setUp()
        self.vault = self.make_vault(self.home / "vault")

    def test_area_and_plane_sit_between_description_and_type(self):
        notes.write_note(self.vault, "finance-ap-invoice-approval", description="d",
                         area="finance", plane="knowledge", commit=False)
        keys = [l.split(":", 1)[0] for l in
                (self.vault / "notes/finance-ap-invoice-approval.md").read_text()
                .splitlines()[1:] if l.strip() and l.strip() != "---"]
        self.assertEqual(keys[:5], ["name", "description", "area", "plane", "type"])

    def test_they_round_trip_through_the_parser(self):
        notes.write_note(self.vault, "repo-pr-isdraft", description="d",
                         area="repo", plane="knowledge", commit=False)
        fm, _ = notes.parse_note((self.vault / "notes/repo-pr-isdraft.md").read_text())
        self.assertEqual((fm["area"], fm["plane"]), ("repo", "knowledge"))

    def test_a_writer_that_passes_neither_never_strips_an_existing_stamp(self):
        notes.write_note(self.vault, "repo-pr-isdraft", description="d",
                         area="repo", plane="knowledge", commit=False)
        notes.write_note(self.vault, "repo-pr-isdraft", mode="append",
                         body="an update", commit=False)
        fm, _ = notes.parse_note((self.vault / "notes/repo-pr-isdraft.md").read_text())
        self.assertEqual(fm["area"], "repo")


class ShimIsUnusedTest(BrainTestCase):
    """``slug_warnings`` stays as back-compat surface, but this repo's own code
    must be off it — a shim with live callers is not a shim, it is the API.

    The source walked its ``scripts/`` tree; the equivalent here is ``lib/``, the
    whole shipped code surface of both planes. It stays valid as later chunks land:
    the source's own lint pass and promote pipeline moved to ``slug_findings``
    before 0.14.0, which is why the claim held there.
    """

    def test_no_shipped_module_calls_the_shim(self):
        callers = []
        for f in sorted(LIB.rglob("*.py")):
            if f.name == "naming.py":
                continue
            for i, line in enumerate(f.read_text(errors="ignore").splitlines(), 1):
                if "slug_warnings" in line:
                    callers.append("%s:%d" % (f.relative_to(LIB), i))
        self.assertEqual(callers, [], "these still call the back-compat shim")

    def test_the_shim_itself_is_still_there(self):
        self.assertTrue(callable(naming.slug_warnings))
        self.assertTrue(all(isinstance(x, str)
                            for x in naming.slug_warnings("misc-stuff-untitled")))


if __name__ == "__main__":
    unittest.main()
