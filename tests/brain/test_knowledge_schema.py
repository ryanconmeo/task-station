"""The deterministic knowledge-node schema (brain.notes + brain.naming).

PROVENANCE: ported from the source's ``tests/test_knowledge_schema.py`` @ 0.14.0
(18 cases, 1:1). Rewrites: modules ``note_io`` -> ``brain.notes`` and ``naming``
-> ``brain.naming``; the twelve fixture lines carrying an org product word are on
``ledger`` here (slug, display name, tag and description text all moved together,
so every similarity band the find-target cases exercise lands where it did — the
arithmetic is in the chunk-2 handoff). Assertions are unchanged.

Covers: the additive frontmatter fields (tags/contributors/provenance) round-
tripping through the writer; typed validation of those fields; the warn-tier
naming rules (good vs bad slugs); and find_target's four resolution outcomes
(exact-name, normalized, similarity, none).
"""
import unittest

from tests.brain.base import BrainTestCase

import brain.naming as naming
import brain.notes as notes


class SchemaRoundTripTest(BrainTestCase):
    def setUp(self):
        super().setUp()
        self.vault = self.make_vault(self.home / "vault")

    def test_knowledge_fields_round_trip(self):
        tags = ["engineering", "ledger-team", "finance"]
        contributors = [
            {"alias": "ryan", "ts": "2026-07-20", "extent": "created"},
            {"alias": "sam", "ts": "2026-07-21", "extent": "minor"},
        ]
        provenance = ["task-station:42", "task-station:57"]
        notes.write_note(self.vault, "ledger-balance-node", description="d",
                         body="the fact", tags=tags, contributors=contributors,
                         provenance=provenance, commit=False)
        fm, body = notes.parse_note((self.vault / "notes/ledger-balance-node.md").read_text())
        self.assertEqual(fm["tags"], tags)
        self.assertEqual(fm["contributors"], contributors)
        self.assertEqual(fm["provenance"], provenance)
        self.assertIn("the fact", body)

    def test_empty_list_round_trips(self):
        notes.write_note(self.vault, "empty-tags-node", description="d",
                         tags=[], commit=False)
        fm, _ = notes.parse_note((self.vault / "notes/empty-tags-node.md").read_text())
        self.assertEqual(fm["tags"], [])

    def test_legacy_note_stays_valid(self):
        # a note with none of the new fields parses and re-emits unchanged shape
        p = self.vault / "notes/legacy.md"
        p.write_text("---\nname: legacy\ndescription: d\ntype: reference\n"
                     "verified: 2020-01-01\nsource: manual\n---\n\nx\n")
        fm, _ = notes.parse_note(p.read_text())
        self.assertNotIn("tags", fm)
        self.assertEqual(fm["name"], "legacy")

    def test_description_with_leading_bracket_is_scalar(self):
        # '[draft] ...' opens with '[' but is not JSON — must stay a plain string
        notes.write_note(self.vault, "bracket-desc", description="[draft] a plan",
                         commit=False)
        fm, _ = notes.parse_note((self.vault / "notes/bracket-desc.md").read_text())
        self.assertEqual(fm["description"], "[draft] a plan")

    def test_bad_tags_type_rejected(self):
        with self.assertRaises(notes.NoteIOError):
            notes.write_note(self.vault, "bad-tags", description="d",
                             tags="not-a-list", commit=False)

    def test_bad_contributor_extent_rejected(self):
        with self.assertRaises(notes.NoteIOError):
            notes.write_note(self.vault, "bad-contrib", description="d",
                             contributors=[{"alias": "x", "ts": "2026-07-20",
                                            "extent": "huge"}], commit=False)

    def test_contributor_missing_alias_rejected(self):
        with self.assertRaises(notes.NoteIOError):
            notes.write_note(self.vault, "bad-contrib2", description="d",
                             contributors=[{"ts": "2026-07-20", "extent": "minor"}],
                             commit=False)

    def test_merge_contributor_cumulative(self):
        c = notes.merge_contributor([], "ryan", "2026-07-20", "created")
        c = notes.merge_contributor(c, "sam", "2026-07-21", "minor")
        c = notes.merge_contributor(c, "ryan", "2026-07-22", "major")  # returning
        aliases = [x["alias"] for x in c]
        self.assertEqual(aliases, ["ryan", "sam"])          # no duplicate
        ryan = next(x for x in c if x["alias"] == "ryan")
        self.assertEqual(ryan["extent"], "created")          # creation is a one-time fact
        self.assertEqual(ryan["ts"], "2026-07-22")           # ts refreshed


class NamingRulesTest(BrainTestCase):
    """The rules themselves, read through ``slug_findings`` — the severity-carrying
    API. (``slug_warnings`` is back-compat surface with no callers left; its own
    coverage lives in ``test_naming.BackCompatTest``.)"""

    def checks(self, slug, folder="notes"):
        return {f["check"] for f in naming.slug_findings(slug, folder)}

    def test_good_slugs_clean(self):
        """Updated for the composed-slug contract: the first segment must now be a
        registered domain. The previous fixtures led with words that were never
        domains at all, which is the drift the contract exists to stop."""
        for good in ("finance-balance-sheet", "data-timeout-gotcha",
                     "repo-push-auth", "task-station-worker-crash"):
            self.assertEqual(naming.slug_findings(good), [], good)

    def test_generic_token_warns(self):
        self.assertIn("generic-token", self.checks("misc-stuff"))

    def test_date_in_slug_warns_outside_reports(self):
        self.assertIn("date-in-slug", self.checks("standup-2026-07-14"))

    def test_date_in_reports_is_fine(self):
        self.assertEqual(naming.slug_findings("2026-07-14-lint", "reports"), [])

    def test_a_bare_word_is_rejected_for_having_no_domain(self):
        """Replaces test_single_segment_warns. That check was dead code — 1 node of
        126 tripped it — and the real failure is stronger: a bare word has no
        registered domain, which is the one ERROR-severity finding."""
        self.assertIn("unregistered-domain", self.checks("widget"))
        self.assertTrue(naming.has_error(naming.slug_findings("widget")))

    def test_normalize(self):
        self.assertEqual(naming.normalize("Ledger Balance Sheet!"), "ledger-balance-sheet")
        self.assertEqual(naming.normalize("  a__b  c "), "a-b-c")


class FindTargetTest(BrainTestCase):
    def setUp(self):
        super().setUp()
        self.vault = self.make_vault(self.home / "vault")
        self.notes = self.vault / "notes"
        self._node("ledger-balance-sheet", "Ledger Balance Sheet",
                   "Rounding rules for the ledger balance sheet report")
        self._node("git-ado-push-auth", "git ADO push auth",
                   "Minting a bearer token to push to Azure DevOps")

    def _node(self, slug, name, desc):
        (self.notes / f"{slug}.md").write_text(
            f"---\nname: {name}\ndescription: {desc}\n---\n\nbody\n")

    def _dirs(self):
        return [self.notes, self.vault / "projects"]

    def test_exact_name_hit(self):
        hit = naming.find_target("ledger-balance-sheet", self._dirs())
        self.assertEqual(hit["slug"], "ledger-balance-sheet")
        self.assertEqual(hit["reason"], "exact-name")

    def test_normalized_hit(self):
        hit = naming.find_target("Ledger Balance Sheet", self._dirs())
        self.assertEqual(hit["slug"], "ledger-balance-sheet")
        self.assertEqual(hit["reason"], "normalized")

    def test_similarity_hit(self):
        hit = naming.find_target("ledger balance sheet rounding report", self._dirs())
        self.assertIsNotNone(hit)
        self.assertEqual(hit["slug"], "ledger-balance-sheet")
        self.assertEqual(hit["reason"], "similarity")

    def test_none_when_unrelated(self):
        self.assertIsNone(naming.find_target("quantum-chromodynamics-primer", self._dirs()))


if __name__ == "__main__":
    unittest.main()
