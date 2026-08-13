"""brain.naming — the naming contract: shape, severity, grading, meaning guard.

PROVENANCE: ported from the source's ``tests/test_naming.py`` @ 0.14.0 (34 cases,
1:1). Rewrites: module ``naming`` -> ``brain.naming``; four fixture slugs/values
that carried an org product word were genericized onto ``acme`` / ``data`` /
``ledger`` (every assertion's shape is unchanged — the replacements were chosen so
the domain resolution, subject length and similarity bands they exercise all land
in the same place; the arithmetic is written out in the chunk-2 handoff).
ADDED (marked): one case pinning the shipped contract data file next to the
module, because its ``__file__`` anchor moved from ``scripts/`` -> repo root to
``lib/brain/`` -> package child.

Everything here is offline and pure: the contract is JSON on disk, the checks are
functions over strings, and the find-target tests build a tiny corpus in a temp
dir. No network, no vault, no config.

The cases that matter are the ones drawn from real evidence, marked REGRESSION —
each encodes a bug that shipped or a claim that turned out false.
"""
import json
import unittest
from pathlib import Path

from tests.brain.base import BrainTestCase

import brain.naming as naming


class ContractDataFileTest(BrainTestCase):
    """ADDED — the shipped generic half must resolve from the module's new home.

    The source anchored it one level up (``scripts/`` -> repo root -> ``data/``);
    here the data dir is a child of the package (``lib/brain/data/``). Get the
    depth wrong and ``_read_json`` swallows the OSError, ``load_contract`` returns
    an EMPTY contract, and every check silently stops firing — so this is asserted
    directly rather than only through a downstream expectation.
    """

    def test_the_contract_data_file_ships_next_to_the_module(self):
        self.assertTrue(naming._DATA.exists(), "%s does not exist" % naming._DATA)
        self.assertEqual(naming._DATA.name, "naming-contract.json")

    def test_the_loaded_contract_is_not_empty(self):
        c = naming.load_contract()
        for key in ("shape", "areas", "severity", "claimShape", "findTarget"):
            self.assertIn(key, c, "contract lost %r — is _DATA resolving?" % key)


class ContractLoadTest(BrainTestCase):
    def test_generic_areas_double_as_domains_so_a_fresh_install_works(self):
        c = naming.load_contract()
        for a in ("finance", "repo", "cloud", "ai", "task-station", "product"):
            self.assertIn(a, c["_registry"], "%s should be usable with no org clone" % a)

    def test_ai_replaced_harness_and_forge_became_repo(self):
        c = naming.load_contract()
        self.assertIn("ai", c["_registry"])
        self.assertIn("repo", c["_registry"])
        self.assertNotIn("harness", c["_registry"])
        self.assertNotIn("forge", c["_registry"])

    def test_industrial_pack_is_opt_in(self):
        with_pack = naming.load_contract(packs=("industrial",))["_registry"]
        without = naming.load_contract(packs=())["_registry"]
        self.assertIn("production", with_pack)
        self.assertNotIn("production", without)

    def test_org_registry_is_read_from_the_org_brain_clone(self):
        clone = Path(self.home) / "orgbrain"
        (clone / "schemas").mkdir(parents=True)
        (clone / "schemas" / "node-types.json").write_text(json.dumps(
            {"domains": {"registry": {"acme": "product", "widgets": "inventory"}}}))
        c = naming.load_contract(org_brain_clone=str(clone))
        self.assertEqual(c["_registry"]["acme"], "product")
        self.assertEqual(c["_registry"]["widgets"], "inventory")

    def test_an_unreadable_org_clone_degrades_to_generic_rather_than_failing(self):
        c = naming.load_contract(org_brain_clone=str(Path(self.home) / "nope"))
        self.assertIn("finance", c["_registry"])

    def test_the_shipped_registry_is_an_allowlist_with_no_room_for_an_org_name(self):
        """REGRESSION, and this test itself is the lesson.

        brain-station's fingerprint guard WAS the leak: it spelled an org
        denylist out in a public repo, exempted itself from its own check, and so
        published a labelled inventory of exactly what it was hiding. The first
        version of THIS test made the same mistake, and the extractor sweep
        caught it.

        So the check is inverted: assert every shipped domain is drawn from the
        known GENERIC vocabulary. An allowlist cannot leak, needs no exemption,
        and fails closed on anything unexpected — including an org name.
        """
        c = naming.load_contract()
        a = c["areas"]
        expected = set(a["business"]) | set(a["technical"]) | set(a["toolchain"])
        for pack in a.get("packs", {}).values():
            expected |= set(pack)
        self.assertTrue(expected, "the shipped vocabulary should not be empty")
        self.assertEqual(set(c["_registry"]) - expected, set(),
                         "a shipped domain outside the generic vocabulary — an org "
                         "word has leaked into brain/data/naming-contract.json")


class DomainResolutionTest(BrainTestCase):
    def setUp(self):
        super().setUp()
        self.c = naming.load_contract()

    def test_two_word_domain_wins_over_its_first_word(self):
        """REGRESSION: `task` is not a domain; `task-station` is. Shortest-prefix
        matching would have invented a `task` domain."""
        self.assertEqual(naming.resolve_domain("task-station-worker-crash", self.c),
                         ("task-station", 2))

    def test_single_word_domain(self):
        self.assertEqual(naming.resolve_domain("finance-ap-invoice-approval", self.c),
                         ("finance", 1))

    def test_unregistered_domain_resolves_to_nothing(self):
        self.assertEqual(naming.resolve_domain("hammerspoon-dollar-expansion", self.c),
                         (None, 0))

    def test_area_rolls_up(self):
        clone = Path(self.home) / "ob"
        (clone / "schemas").mkdir(parents=True)
        (clone / "schemas" / "node-types.json").write_text(json.dumps(
            {"domains": {"registry": {"acme": "product"}}}))
        c = naming.load_contract(org_brain_clone=str(clone))
        self.assertEqual(naming.area_for("acme-widget-tolerance", c), "product")

    def test_compose_is_the_inverse_of_reading(self):
        self.assertEqual(naming.compose("finance", "invoice approval", subdomain="ap"),
                         "finance-ap-invoice-approval")
        self.assertEqual(naming.compose("ai", "MCP Server Scope"), "ai-mcp-server-scope")


class SeverityTest(BrainTestCase):
    def setUp(self):
        super().setUp()
        self.c = naming.load_contract()

    def checks(self, slug, folder="notes"):
        return {f["check"]: f for f in naming.slug_findings(slug, folder, self.c)}

    def test_only_the_domain_check_blocks(self):
        """A refusal makes an author drop the fact or fake a name. Evidence: the
        pinned-decision cap refused and produced a crowding-out workaround."""
        f = self.checks("nonsense-word-here")
        self.assertEqual(f["unregistered-domain"]["severity"], "error")
        f2 = self.checks("finance-ap-a-very-long-mechanism-explaining-subject")
        self.assertNotIn("unregistered-domain", f2)
        self.assertEqual(f2["subject-too-long"]["severity"], "warn")
        self.assertFalse(naming.has_error(naming.slug_findings(
            "finance-ap-a-very-long-mechanism-explaining-subject", "notes", self.c)))

    def test_the_refusal_names_the_nearest_registered_domain(self):
        f = self.checks("financ-ap-invoice")
        self.assertIn("finance", f["unregistered-domain"]["fix"])

    def test_a_conforming_slug_is_silent(self):
        self.assertEqual(naming.slug_findings("acme-store-flip", "notes",
                                              self._with_org()), [])

    def _with_org(self):
        clone = Path(self.home) / "ob2"
        (clone / "schemas").mkdir(parents=True, exist_ok=True)
        (clone / "schemas" / "node-types.json").write_text(json.dumps(
            {"domains": {"registry": {"acme": "product"}}}))
        return naming.load_contract(org_brain_clone=str(clone))


class ClaimShapeTest(BrainTestCase):
    def setUp(self):
        super().setUp()
        self.c = naming.load_contract()

    def test_leading_imperative(self):
        r = naming.claim_shape("never-apply-flyway-to-dev-directly", self.c)
        self.assertIn("never", r)
        self.assertIn("type: rule", r, "the fix should point at the rule type")

    def test_embedded_copula(self):
        self.assertIn("is", naming.claim_shape("plugin-update-is-version-gated", self.c))

    def test_negation(self):
        self.assertIn("not", naming.claim_shape("structure-by-domain-not-goal", self.c))

    def test_a_subject_shaped_name_is_clean(self):
        self.assertIsNone(naming.claim_shape("acme-store-flip", self.c))

    def test_a_leading_word_that_only_looks_imperative_in_position_two(self):
        """`use` mid-slug is a noun-ish token, not the verdict shape."""
        self.assertIsNone(naming.claim_shape("cloud-sql-contained-users", self.c))


class DateRuleTest(BrainTestCase):
    def setUp(self):
        super().setUp()
        self.c = naming.load_contract()

    def test_year_month_counts_as_a_date(self):
        """REGRESSION: the old regex demanded YYYY-MM-DD, so
        `verification-incidents-2026-07` passed a check that reported ZERO
        violations. The measurement was wrong, not the corpus."""
        f = {x["check"] for x in naming.slug_findings("ai-verification-incidents-2026-07",
                                                      "notes", self.c)}
        self.assertIn("date-in-slug", f)

    def test_full_date_counts(self):
        f = {x["check"] for x in naming.slug_findings("ai-incident-2026-07-14", "notes", self.c)}
        self.assertIn("date-in-slug", f)

    def test_plans_and_raw_are_dated_by_design(self):
        """REGRESSION: only reports/ was exempt, but plans/ held 14 dated files
        and raw/ is dated by convention — the rule flagged correct names."""
        for folder in ("plans", "raw", "reports"):
            f = {x["check"] for x in naming.slug_findings("ai-incidents-2026-07",
                                                          folder, self.c)}
            self.assertNotIn("date-in-slug", f, "%s/ is legitimately dated" % folder)


class FindTargetGradingTest(BrainTestCase):
    def setUp(self):
        super().setUp()
        self.c = naming.load_contract()
        self.dir = Path(self.home) / "notes"
        self.dir.mkdir(parents=True)

    def node(self, slug, description):
        (self.dir / (slug + ".md")).write_text(
            "---\nname: %s\ndescription: \"%s\"\n---\n\nbody\n" % (slug, description))

    def test_exact_slug_is_an_update_because_the_slug_is_the_identity(self):
        self.node("ai-mcp-server-scope", "MCP servers are neutral infra")
        r = naming.find_target("ai-mcp-server-scope", [self.dir], self.c)
        self.assertEqual(r["action"], "update")
        self.assertEqual(r["reason"], "exact-name")

    def test_high_description_similarity_is_an_update(self):
        self.node("ai-mcp-server-scope",
                  "MCP DB servers are neutral infra plus central per-DB configs")
        r = naming.find_target(
            "MCP DB servers are neutral infra plus central per-DB configs",
            [self.dir], self.c)
        self.assertEqual(r["action"], "update")

    def test_nothing_similar_returns_none(self):
        """None is the contract every caller relies on — the promote pipeline does
        `if target:` and the search CLI does `if not hit:`. An always-truthy return
        makes promote reconcile into the nearest node every single time."""
        self.node("finance-ap-invoice-approval", "Invoices over 50k need two signatures")
        self.assertIsNone(naming.find_target(
            "How the metals surcharge is applied at invoice time", [self.dir], self.c))

    def test_the_lookalike_pair_must_not_merge(self):
        """REGRESSION, negative fixture #1. These two scored 0.595 on slug text and
        are DIFFERENT FACTS. Grading reads the description, where they score far
        apart, so slug text can never drive a merge."""
        self.node("data-store-reload-owner",
                  "The store-rebuild reload leg is ADF, not an ADO pipeline")
        r = naming.find_target(
            "The ADF reload needs four pre-existing dbo framework tables a fresh "
            "store lacks; pre-create them CI_AS-pinned",
            [self.dir], self.c)
        if r is not None:
            self.assertNotEqual(r["action"], "update",
                                "two different facts must never auto-merge")
            self.assertLess(r["descScore"], 0.9)

    def test_an_empty_corpus_returns_none_rather_than_crashing(self):
        self.assertIsNone(naming.find_target("anything", [self.dir], self.c))

    def test_a_missing_directory_is_not_an_error(self):
        self.assertIsNone(naming.find_target("anything", [Path(self.home) / "gone"], self.c))

    def test_a_middling_candidate_asks_rather_than_deciding(self):
        """The grey band exists so the CHOICE gets recorded, not guessed."""
        self.node("cloud-sql-contained-users",
                  "A fresh Azure SQL DB has zero contained users until you replicate "
                  "the source principals")
        r = naming.find_target("cloud-sql-contained-user", [self.dir], self.c)
        self.assertIsNotNone(r)
        self.assertIn(r["action"], ("choose", "update"))


class MeaningGuardTest(BrainTestCase):
    def setUp(self):
        super().setUp()
        self.c = naming.load_contract()
        self.dir = Path(self.home) / "notes"
        self.dir.mkdir(parents=True)

    def test_dropping_a_rare_word_is_flagged(self):
        """REGRESSION: a rename draft turned `zydeco-labeled-claude-plugins`
        into `ai-plugin-labelling`, deleting the only identifying word."""
        for s in ("zydeco-labeled-claude-plugins", "ai-plugin-version-gate",
                  "ai-mcp-server-scope"):
            (self.dir / (s + ".md")).write_text("---\nname: %s\n---\n" % s)
        freq = naming.word_frequency([self.dir])
        lost = naming.dropped_rare_words("zydeco-labeled-claude-plugins",
                                         "ai-plugin-labelling", freq, self.c)
        self.assertIn("zydeco", lost)

    def test_dropping_a_common_word_is_fine(self):
        for i in range(5):
            (self.dir / ("ai-plugin-thing%d.md" % i)).write_text("---\n---\n")
        freq = naming.word_frequency([self.dir])
        self.assertEqual(
            naming.dropped_rare_words("ai-plugin-thing0", "ai-thing0", freq, self.c), [])

    def test_word_frequency_counts_nodes_not_occurrences(self):
        (self.dir / "ai-ai-ai.md").write_text("---\n---\n")
        self.assertEqual(naming.word_frequency([self.dir])["ai"], 1)


class BackCompatTest(BrainTestCase):
    def test_slug_warnings_still_returns_strings(self):
        """The lint pass and the promote pipeline called this; it must keep working."""
        w = naming.slug_warnings("misc-stuff-untitled")
        self.assertTrue(all(isinstance(x, str) for x in w))
        self.assertTrue(any("generic-token" in x for x in w))

    def test_normalize_is_unchanged(self):
        self.assertEqual(naming.normalize("  Ledger Key: Case-Sensitivity!  "),
                         "ledger-key-case-sensitivity")


if __name__ == "__main__":
    unittest.main()
