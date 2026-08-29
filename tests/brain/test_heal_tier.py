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

import brain.heal_lint as heal_lint
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
        """PORTED CASE, AMENDED (#571): the cue reading is unchanged, but the
        move into memory is now authorised by the note's DECLARED type, so the
        case has to declare one. Without a declaration the same text is a
        suggestion only — the next test."""
        f = heal_tier.classify(PERSONAL_BODY, "note", declared="feedback")
        self.assertEqual(f["suggested"], "memory")
        self.assertEqual(f["confidence"], "high")
        self.assertTrue(f["applied_eligible"])

    def test_an_undeclared_note_is_suggested_into_memory_but_never_applied(self):
        f = heal_tier.classify(PERSONAL_BODY, "note")
        self.assertEqual(f["suggested"], "memory")
        self.assertFalse(f["applied_eligible"])
        self.assertIn("type: missing", f["kind"])

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

    def _note(self, slug, desc, body, promote=None, ntype="reference"):
        fm = [f"name: {slug}", f"description: {desc}", f"type: {ntype}"]
        if promote is not None:
            fm.append(f"promote: {promote}")
        fm += ["verified: 2026-01-01", "source: manual"]
        (self.vault / "notes" / f"{slug}.md").write_text(
            "---\n" + "\n".join(fm) + f"\n---\n\n{body}\n")


class CorpusTest(TierLintFixture):
    def setUp(self):
        super().setUp()
        self._seed()

    def _seed(self):
        self._mem("company-fact", "ledger database reached through the managed DevOps pool",
                  COMPANY_BODY)
        self._mem("how-i-work", "how I like to review",
                  "I prefer the summary tab first; my usual review flow is top-down.",
                  mtype="feedback")
        self._mem("ambiguous", "a stray note", "The kitchen is on the second floor.")
        self._note("personal-pref", "how I review PRs", PERSONAL_BODY, ntype="feedback")
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
        report = (self.vault / "mirror/health" / f"tier-lint-{DATE}.md").read_text()
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
        self._note("personal-pref", "how I review PRs", PERSONAL_BODY, ntype="feedback")

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
    """ADDED. ``team_suggest`` is the ``promote: true`` promotion candidacy flag —
    the one output of this module that feeds the promote pipeline (chunk 4b), and
    the source suite never asserts it. It is deliberately suggestion-only: the
    switch is never written for you, so a regression here is silent.
    """

    def test_a_team_flavoured_note_is_flagged_as_a_promotion_candidate(self):
        self._note("shared-convention", "how the team names branches",
                   "The team keeps to one standard branch naming convention here.")
        f = next(f for f in heal_tier.scan(self.cfg) if f["slug"] == "shared-convention")
        self.assertTrue(f["team_suggest"])
        self.assertEqual(f["suggested"], f["current"])   # a switch, never a re-file
        report = heal_tier.render_report([f], DATE)
        self.assertIn("consider `promote: true`", report)
        self.assertIn("never auto-pushed", report)

    def test_a_note_already_marked_promote_is_not_re_suggested(self):
        self._note("shared-convention", "how the team names branches",
                   "The team keeps to one standard branch naming convention here.",
                   promote="true")
        f = next(f for f in heal_tier.scan(self.cfg) if f["slug"] == "shared-convention")
        self.assertFalse(f["team_suggest"])
        self.assertIn("- shared-convention", heal_tier.render_report([f], DATE))  # listed as aligned

    def test_a_promote_value_that_is_not_true_still_gets_suggested(self):
        """Tier-lint reads the switch through the same one reader promote.py
        uses, so a value promote.py would refuse must not silence the nudge."""
        for raw in ("false", "yes", "maybe"):
            with self.subTest(value=raw):
                self._note("shared-convention", "how the team names branches",
                           "The team keeps to one standard branch naming convention here.",
                           promote=raw)
                f = next(f for f in heal_tier.scan(self.cfg)
                         if f["slug"] == "shared-convention")
                self.assertTrue(f["team_suggest"])

    def test_a_team_flavoured_memory_is_never_flagged(self):
        """The switch lives on vault notes; memory carries no promote field."""
        self._mem("shared-convention", "how the team names branches",
                  "The team keeps to one standard branch naming convention here.")
        f = next(f for f in heal_tier.scan(self.cfg) if f["slug"] == "shared-convention")
        self.assertFalse(f["team_suggest"])

    def test_a_tombstoned_note_is_written_with_neither_switch(self):
        """A tier-lint tombstone is a generated note, and a generated note is a
        private note — it must not arrive pre-marked for anyone else to read."""
        text = heal_tier._note_tombstone("moved-note", "d", "memory/moved-note.md", DATE)
        self.assertIn("name: moved-note", text)          # it did write a note
        self.assertNotIn("publish:", text)
        self.assertNotIn("promote:", text)


SURVEY_BODY = (
    "Reviewed three unrelated third-party plugins that share one brand. The first "
    "is an output formatter; rated 3/10 for me, because my global guide already "
    "encodes the good 70% of it. The third is a reasoning scaffold; also 3/10 for "
    "me, since my harness ships the same patterns first-class. MARKER-SURVEY-BODY")


class MemoryContractTest(TierLintFixture):
    """ADDED (task-station #571). memory/ holds ONLY how-to-work-with-its-owner
    facts, declared ``feedback`` or ``user`` — the contract heal_lint's
    ``memory-type`` check enforces since 3.23.0. Tier-lint did not know it, and
    the two lints therefore disagreed in the most damaging possible way: on
    2026-08-21 tier-lint scored :data:`SURVEY_BODY`-shaped content personal 2 /
    company 0, called it ``high``, moved a survey of three THIRD-PARTY plugins
    into memory/ and left a tombstone behind — then wrote it with
    ``write_memory_note``'s default ``type: reference``, i.e. it minted the exact
    artifact the other lint refuses. Every case here fails on that classifier.
    """

    def test_a_declared_reference_note_is_never_auto_filed_into_memory(self):
        f = heal_tier.classify(SURVEY_BODY, "note", declared="reference")
        self.assertEqual(f["scores"]["personal"], 2)     # the cues are unchanged...
        self.assertEqual(f["scores"]["company"], 0)
        self.assertFalse(f["applied_eligible"])          # ...the declaration governs
        self.assertEqual(f["confidence"], "low")
        self.assertIn("re-type it to feedback|user", f["kind"])

    def test_the_old_cue_only_reading_is_what_made_it_high_confidence(self):
        """Same text, declaration withheld from the classifier would be the old
        behaviour — so pin that the ONLY thing standing between this survey and
        memory/ is the declared type, not a lexicon tweak."""
        f = heal_tier.classify(SURVEY_BODY, "note", declared="feedback")
        self.assertEqual(f["suggested"], "memory")
        self.assertTrue(f["applied_eligible"])

    def test_apply_leaves_a_declared_reference_note_exactly_where_it_is(self):
        self._note("plugin-survey", "three plugins sharing one brand", SURVEY_BODY)
        before = (self.vault / "notes/plugin-survey.md").read_text()
        result = heal_tier.run(self.cfg, apply=True, today=DATE)
        self.assertEqual(result["applied"], [])
        self.assertFalse((self.memory / "plugin-survey.md").exists(),
                         "a reference survey was minted as a memory")
        self.assertEqual((self.vault / "notes/plugin-survey.md").read_text(), before,
                         "the note was tombstoned by a move that must not happen")

    def test_a_note_that_does_move_lands_with_a_legal_memory_type(self):
        """The move is authorised by the declaration, so the minted memory must
        CARRY that declaration — not write_memory_note's `reference` default,
        which would be an instant heal_lint memory-type finding."""
        self._note("how-to-brief-me", "how I want briefs written", PERSONAL_BODY,
                   ntype="feedback")
        heal_tier.run(self.cfg, apply=True, today=DATE)
        minted = (self.memory / "how-to-brief-me.md").read_text()
        self.assertIn("MARKER-PERSONAL-BODY", minted)
        self.assertEqual(heal_lint.memory_type(self.memory / "how-to-brief-me.md"),
                         "feedback")

    def test_a_memory_declared_outside_the_contract_is_a_finding_not_aligned(self):
        self._mem("brain-plane-state", "where the brain plane ships",
                  "My notes on where this ships and how I reach it.", mtype="project")
        f = next(f for f in heal_tier.scan(self.cfg) if f["slug"] == "brain-plane-state")
        self.assertEqual(f["suggested"], "note")
        self.assertFalse(f["applied_eligible"])          # heal_lint owns the count
        self.assertIn("only feedback|user", f["kind"])

    def test_the_two_lints_read_the_declared_type_through_one_reader(self):
        """A tier-lint suggestion can never disagree with the check that
        enforces it — the same argument the module already makes for `promote`."""
        self._mem("harness-shaped", "d", "b", mtype="user")
        path = self.memory / "harness-shaped.md"
        self.assertEqual(heal_tier._read_item(path)["type"],
                         heal_lint.memory_type(path))


if __name__ == "__main__":
    import unittest
    unittest.main()
