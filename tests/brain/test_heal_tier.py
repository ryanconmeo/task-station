"""Tier-lint (``brain.heal_tier``) — classification, report shape, lossless --apply.

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 4a) from the brain source tree's
``tests/test_tier_lint.py`` @ 0.14.0. All 8 source cases port; 6 are ADDED
(``TombstoneTest`` 3, ``TeamSuggestTest`` 3 — see their class docstrings).

GENERICIZED FIXTURES. The source's company-knowledge fixtures named its own org's
product, and that product name was ALSO a cue word in ``COMPANY`` — so both the
fixture and the lexicon changed, and the CUE SCORES (what the classification
bands are computed from) had to be re-checked. Company-cue counts, source → here:

    COMPANY_BODY   {<product>, devops, sql} = 3  ->  {database, devops, sql} = 3
    company-fact   description + body union = 3  ->                          = 3
    company-arch   {<product>, materialized, rls, azure, sql} = 5
                                                 ->  {materialized, rls, azure, sql} = 4

Only the last one moves, and it cannot change an outcome: that fixture is already
filed as a note, so its branch is ``cand == current`` (aligned) for any company
score >= 1. Note that ``pipelines`` never scored in either version — the cue is
``pipeline`` and ``_count`` matches on a word boundary.
"""
from tests.brain.base import BrainTestCase

import brain.heal_tier as heal_tier

DATE = "2026-07-14"

COMPANY_BODY = ("The ledger database runs build pipelines on the managed DevOps pool "
                "to reach private-endpoint SQL. MARKER-COMPANY-BODY")
PERSONAL_BODY = ("When I review PRs I like to start from the diff, and remind me "
                 "to check the tests tab. MARKER-PERSONAL-BODY")


class ClassifyTest(BrainTestCase):
    def test_company_knowledge_in_memory_suggests_note(self):
        f = heal_tier.classify(COMPANY_BODY, "memory")
        self.assertEqual(f["suggested"], "note")
        self.assertEqual(f["confidence"], "high")
        self.assertTrue(f["applied_eligible"])

    def test_personal_howto_in_notes_suggests_memory(self):
        f = heal_tier.classify(PERSONAL_BODY, "note")
        self.assertEqual(f["suggested"], "memory")
        self.assertEqual(f["confidence"], "high")
        self.assertTrue(f["applied_eligible"])

    def test_safety_mechanizable_imperative_suggests_hook(self):
        f = heal_tier.classify(
            "Never commit a secret; block any commit whose diff matches an sk-ant- or ghp_ token.",
            "note")
        self.assertEqual(f["suggested"], "hook")
        self.assertFalse(f["applied_eligible"])   # hooks are human-only

    def test_plain_imperative_suggests_rule(self):
        f = heal_tier.classify("Always branch off origin/dev; never push to main directly.", "note")
        self.assertEqual(f["suggested"], "rule")
        self.assertFalse(f["applied_eligible"])

    def test_aligned_company_note_stays(self):
        f = heal_tier.classify("Ledger global search is a materialized RLS table on Azure SQL.", "note")
        self.assertEqual(f["suggested"], "note")
        self.assertFalse(f["applied_eligible"])


class TierLintFixture(BrainTestCase):
    """Vault + memory scaffolding. ZERO test methods, so it contributes no cases
    (the suite's established ``CliFixture`` / ``RefFixtureMixin`` shape)."""

    def setUp(self):
        super().setUp()
        self.vault = self.make_vault(self.home / "vault")
        self.memory = self.vault / "memory"
        self.memory.mkdir(parents=True, exist_ok=True)
        self.cfg = {"vault": self.vault, "memory": self.memory}

    def _mem(self, slug, desc, body, mtype="reference"):
        (self.memory / f"{slug}.md").write_text(
            f"---\nname: {slug}\ndescription: {desc}\nmetadata:\n  type: {mtype}\n---\n\n{body}\n")

    def _note(self, slug, desc, body, scope="personal"):
        (self.vault / "notes" / f"{slug}.md").write_text(
            f"---\nname: {slug}\ndescription: {desc}\ntype: reference\nscope: {scope}\n"
            f"verified: 2026-01-01\nsource: manual\n---\n\n{body}\n")


class CorpusTest(TierLintFixture):
    def setUp(self):
        super().setUp()
        self._seed()

    def _seed(self):
        self._mem("company-fact", "ledger database reached through the managed DevOps pool",
                  COMPANY_BODY)
        self._mem("how-i-work", "how I like to review", "I prefer the summary tab first; my usual review flow is top-down.")
        self._mem("ambiguous", "a stray note", "The kitchen is on the second floor.")
        self._note("personal-pref", "how I review PRs", PERSONAL_BODY)
        self._note("company-arch", "Ledger search architecture",
                   "Ledger global search is a materialized RLS table on Azure SQL.")
        self._note("secret-hook", "no secrets in commits",
                   "Never commit a secret; block any commit whose diff matches an sk-ant- or ghp_ token.")
        self._note("rule-cand", "branching rule", "Always branch off origin/dev; never push to main directly.")
        (self.memory / "MEMORY.md").write_text(
            "# MEMORY\n\n"
            "- [Company fact](company-fact.md) — hook\n"
            "- [How I work](how-i-work.md) — feedback\n"
            "- [Ambiguous](ambiguous.md) — reference\n")

    def _slugs(self, findings, pred):
        return {f["slug"] for f in findings if pred(f)}

    def test_report_shape(self):
        result = heal_tier.run(self.cfg, apply=False, today=DATE)
        report = (self.vault / "reports/health" / f"tier-lint-{DATE}.md").read_text()
        for header in ("# Tier-lint", "## Re-filings", "## Suggestions", "## Aligned"):
            self.assertIn(header, report)
        self.assertIn("company-fact", report)
        self.assertIn("personal-pref", report)
        self.assertIn("secret-hook", report)
        # high-confidence re-filings identified
        elig = self._slugs(result["findings"], lambda f: f["applied_eligible"])
        self.assertEqual(elig, {"company-fact", "personal-pref"})

    def test_apply_moves_content_losslessly(self):
        heal_tier.run(self.cfg, apply=True, today=DATE)

        # memory -> note: content re-filed to notes/, origin tombstoned, indexes updated
        note = (self.vault / "notes/company-fact.md").read_text()
        self.assertIn("MARKER-COMPANY-BODY", note)                  # content preserved
        mem_tomb = (self.memory / "company-fact.md").read_text()
        self.assertIn("MOVED", mem_tomb)
        self.assertIn("notes/company-fact.md", mem_tomb)
        memory_md = (self.memory / "MEMORY.md").read_text()
        self.assertRegex(memory_md, r"company-fact.*MOVED")
        self.assertIn("[[company-fact]]", (self.vault / "INDEX.md").read_text())

        # note -> memory: content re-filed to memory/, origin tombstoned
        memnote = (self.memory / "personal-pref.md").read_text()
        self.assertIn("MARKER-PERSONAL-BODY", memnote)
        self.assertIn("metadata:", memnote)                         # harness memory schema
        note_tomb = (self.vault / "notes/personal-pref.md").read_text()
        self.assertIn("MOVED", note_tomb)
        self.assertIn("memory/personal-pref.md", note_tomb)
        self.assertIn("(personal-pref.md)", (self.memory / "MEMORY.md").read_text())

    def test_low_confidence_and_suggestions_untouched_by_apply(self):
        before = {p: (self.memory / p).read_text() for p in ("how-i-work.md", "ambiguous.md")}
        before_notes = {p: (self.vault / "notes" / p).read_text()
                        for p in ("secret-hook.md", "rule-cand.md", "company-arch.md")}
        heal_tier.run(self.cfg, apply=True, today=DATE)
        for p, txt in before.items():
            self.assertEqual((self.memory / p).read_text(), txt, f"{p} was modified")
        for p, txt in before_notes.items():
            self.assertEqual((self.vault / "notes" / p).read_text(), txt, f"{p} was modified")
        # no spurious re-file destinations created
        self.assertFalse((self.vault / "notes/how-i-work.md").exists())
        self.assertFalse((self.memory / "secret-hook.md").exists())
        self.assertFalse((self.memory / "rule-cand.md").exists())


class TombstoneTest(TierLintFixture):
    """ADDED. ``--apply`` runs on every ``/brain-heal``, so the SECOND pass over an
    already-re-filed item is the common case, not the edge one — and the only
    thing that stops it re-filing again is ``_read_item``'s tombstone sniff
    (body mentions "tier-lint" AND "moved"). If that stopped matching, the
    re-file would call ``write_note(mode="create")`` on a path that now exists,
    which RAISES — aborting the whole tier-lint step of the heal, not just this
    item. Nothing in the source suite runs ``--apply`` twice.
    """

    def setUp(self):
        super().setUp()
        self._mem("company-fact", "ledger database reached through the managed DevOps pool",
                  COMPANY_BODY)
        self._note("personal-pref", "how I review PRs", PERSONAL_BODY)

    def test_a_second_apply_pass_re_files_nothing_and_does_not_raise(self):
        first = heal_tier.run(self.cfg, apply=True, today=DATE)
        self.assertEqual(sorted(first["applied"]),
                         ["memory→note: company-fact", "note→memory: personal-pref"])
        second = heal_tier.run(self.cfg, apply=True, today=DATE)
        self.assertEqual(second["applied"], [])

    def test_the_tombstoned_originals_are_skipped_by_the_scan(self):
        heal_tier.run(self.cfg, apply=True, today=DATE)
        findings = heal_tier.scan(self.cfg)
        # each slug survives exactly once — at its DESTINATION, not at both ends
        self.assertEqual(sorted(f["slug"] for f in findings),
                         ["company-fact", "personal-pref"])
        by_slug = {f["slug"]: f for f in findings}
        self.assertEqual(by_slug["company-fact"]["current"], "note")
        self.assertEqual(by_slug["personal-pref"]["current"], "memory")

    def test_the_content_survives_two_passes(self):
        heal_tier.run(self.cfg, apply=True, today=DATE)
        heal_tier.run(self.cfg, apply=True, today=DATE)
        self.assertIn("MARKER-COMPANY-BODY", (self.vault / "notes/company-fact.md").read_text())
        self.assertIn("MARKER-PERSONAL-BODY", (self.memory / "personal-pref.md").read_text())


class TeamSuggestTest(TierLintFixture):
    """ADDED. ``team_suggest`` is the ``scope: team`` promotion candidacy flag —
    the one output of this module that feeds the promote pipeline (chunk 4b), and
    the source suite never asserts it. It is deliberately suggestion-only: a
    ``scope: team`` tag is never applied, so a regression here is silent.
    """

    def test_a_team_flavoured_note_is_flagged_as_a_promotion_candidate(self):
        self._note("shared-convention", "how the team names branches",
                   "The team keeps to one standard branch naming convention here.")
        f = next(f for f in heal_tier.scan(self.cfg) if f["slug"] == "shared-convention")
        self.assertTrue(f["team_suggest"])
        self.assertEqual(f["suggested"], f["current"])   # a tag, never a re-file
        report = heal_tier.render_report([f], DATE)
        self.assertIn("consider `scope: team`", report)
        self.assertIn("never auto-pushed", report)

    def test_a_note_already_scoped_team_is_not_re_suggested(self):
        self._note("shared-convention", "how the team names branches",
                   "The team keeps to one standard branch naming convention here.",
                   scope="team")
        f = next(f for f in heal_tier.scan(self.cfg) if f["slug"] == "shared-convention")
        self.assertFalse(f["team_suggest"])
        self.assertIn("- shared-convention", heal_tier.render_report([f], DATE))  # listed as aligned

    def test_a_team_flavoured_memory_is_never_flagged(self):
        """The tag lives on vault notes; memory has no scope field to carry it."""
        self._mem("shared-convention", "how the team names branches",
                  "The team keeps to one standard branch naming convention here.")
        f = next(f for f in heal_tier.scan(self.cfg) if f["slug"] == "shared-convention")
        self.assertFalse(f["team_suggest"])


if __name__ == "__main__":
    import unittest
    unittest.main()
