"""The naming contract wired into the WRITE path (brain.notes + brain.naming).

PROVENANCE: ported from the source's ``tests/test_naming_write_path.py`` @ 0.14.0.
That file has 34 cases in six classes. Chunk 2 ported the 15 that exercise
``brain.notes`` + ``brain.naming`` directly; chunk 4a took ``LintSeverityTest``
(3) with the lint pass; chunk 4c APPENDED the remaining 16 — ``CliFixture`` and
its two subclasses — with ``brain.search``, the CLI they drive.

Rewrites: modules ``note_io`` -> ``brain.notes``, ``naming`` -> ``brain.naming``;
``ShimIsUnusedTest`` scans ``lib/`` (this repo's shipped code) where the source
scanned its ``scripts/``. The source ran ``scripts/brain.py`` as a subprocess BY
PATH; a package module with relative imports cannot be run that way, so
``CliFixture`` spawns ``python3 -m brain.search`` with ``lib/`` on PYTHONPATH and
the temp home's pins carried into the child (see the class docstring).

Three things are covered here, and each exists because the contract was DATA with
nothing acting on it:

  * ``area:``/``plane:`` — the org schema requires both on every knowledge node
    and nothing created them, so every node written since the schema landed was
    silently non-conforming.
  * the graded merge-target lookup — mandatory before a create, with three
    outcomes (update / choose / create) rather than an advisory print.
  * severity — ``slug_findings`` carries error-vs-warn, and the callers act on the
    difference instead of flattening it into a warning.
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from tests.brain.base import BrainTestCase, LIB, PINNED_ENV

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


# --------------------------------------------------------------------------- #
# APPENDED in chunk 4c with brain.search — the CLI half of the write path.
# The wiring (argparse -> gate -> lookup -> write) is the thing under test, not
# the functions in isolation, which is why these run the CLI as a subprocess.
# --------------------------------------------------------------------------- #
class CliFixture(BrainTestCase):
    """``brain.search`` driven as a subprocess against a scratch vault.

    ZERO test methods — contributes no cases of its own.

    The source ran ``[sys.executable, str(BRAIN), …]`` on ``scripts/brain.py``.
    Here the CLI is a PACKAGE module whose imports are relative, so it cannot be
    run by path at all; the sanctioned form is ``-m brain.search`` with ``lib/``
    on ``PYTHONPATH``. The temp home's pins (``TASK_STATION_HOME`` /
    ``CLAUDE_CONFIG_DIR``, base.PINNED_ENV) are carried into the child DELIBERATELY:
    without them the child's ``core.paths.data_dir()`` walks out of the temp home
    and the brain's state dir lands in the developer's real ``~/.claude``.
    """

    def setUp(self):
        super().setUp()
        self.vault = self.make_vault(self.home / "vault")

    def run_brain(self, *args, org_clone=None):
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["PYTHONPATH"] = str(LIB)
        for k, rel in PINNED_ENV.items():
            env[k] = str(self.home / rel)
        env["TASK_STATION_BRAIN_VAULT"] = str(self.vault)
        env.pop("TASK_STATION_BRAIN_ORG_BRAIN_CLONE", None)
        if org_clone:
            env["TASK_STATION_BRAIN_ORG_BRAIN_CLONE"] = str(org_clone)
        return subprocess.run([sys.executable, "-m", "brain.search", *args],
                              capture_output=True, text=True, env=env)

    def node(self, slug, description, folder="notes"):
        p = self.vault / folder / f"{slug}.md"
        p.write_text('---\nname: %s\ndescription: "%s"\n---\n\nbody\n'
                     % (slug, description))
        return p

    def fm(self, slug, folder="notes"):
        return notes.parse_note((self.vault / folder / f"{slug}.md").read_text())[0]


class NewStampsTest(CliFixture):
    def test_a_created_note_carries_area_and_plane(self):
        r = self.run_brain("new", "ai-mcp-server-scope", "--description", "neutral infra")
        self.assertEqual(r.returncode, 0, r.stderr)
        fm = self.fm("ai-mcp-server-scope")
        self.assertEqual(fm["area"], "ai")
        self.assertEqual(fm["plane"], "knowledge")

    def test_a_projects_node_carries_them_too(self):
        r = self.run_brain("new", "task-station-hub", "--folder", "projects",
                           "--description", "the toolchain hub")
        self.assertEqual(r.returncode, 0, r.stderr)
        fm = self.fm("task-station-hub", "projects")
        self.assertEqual((fm["area"], fm["plane"]), ("task-station", "knowledge"))

    def test_a_reports_artifact_is_stamped_with_neither(self):
        r = self.run_brain("new", "2026-08-03-lint-run", "--folder", "reports",
                           "--description", "a dated artifact")
        self.assertEqual(r.returncode, 0, r.stderr)
        fm = self.fm("2026-08-03-lint-run", "reports")
        self.assertNotIn("area", fm)
        self.assertNotIn("plane", fm)

    def test_the_override_flags_win(self):
        r = self.run_brain("new", "ai-mcp-server-scope", "--description", "x",
                           "--area", "risk", "--plane", "control")
        self.assertEqual(r.returncode, 0, r.stderr)
        fm = self.fm("ai-mcp-server-scope")
        self.assertEqual((fm["area"], fm["plane"]), ("risk", "control"))

    def test_an_unregistered_domain_refuses_and_writes_nothing(self):
        r = self.run_brain("new", "hammerspoon-dollar-expansion", "--description", "x")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("unregistered-domain", r.stderr)
        self.assertFalse((self.vault / "notes/hammerspoon-dollar-expansion.md").exists())

    def test_the_refusal_names_the_nearest_registered_domain(self):
        r = self.run_brain("new", "financ-ap-invoice-approval", "--description", "x")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("finance", r.stderr)

    def test_a_warn_finding_prints_its_severity_and_still_writes(self):
        r = self.run_brain("new", "ai-never-trust-a-green-suite",
                           "--description", "an unrelated subject entirely")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("naming warn", r.stderr)
        self.assertIn("claim-shaped", r.stderr)
        self.assertTrue((self.vault / "notes/ai-never-trust-a-green-suite.md").exists())

    def test_an_org_domain_is_read_from_the_clone_and_never_from_this_repo(self):
        clone = self.home / "orgbrain"
        (clone / "schemas").mkdir(parents=True)
        (clone / "schemas" / "node-types.json").write_text(json.dumps(
            {"domains": {"registry": {"acme": "product"}}}))
        ok = self.run_brain("new", "acme-widget-tolerance", "--description", "x",
                            org_clone=clone)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(self.fm("acme-widget-tolerance")["area"], "product")
        refused = self.run_brain("new", "acme-widget-cost", "--description", "y")
        self.assertNotEqual(refused.returncode, 0)


class GradedCreateTest(CliFixture):
    """The lookup is mandatory; the ACTION is graded into three outcomes."""

    def test_same_fact_refuses_and_names_the_target(self):
        desc = "MCP DB servers are neutral infra plus central per-DB configs"
        self.node("ai-mcp-server-scope", desc)
        r = self.run_brain("new", "ai-mcp-neutrality", "--description", desc)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("SAME FACT", r.stdout)
        self.assertIn("ai-mcp-server-scope", r.stdout)
        self.assertIn("update it in place", r.stderr)
        self.assertFalse((self.vault / "notes/ai-mcp-neutrality.md").exists())

    def test_an_exact_slug_hit_is_the_same_node_because_the_slug_is_the_identity(self):
        self.node("ai-mcp-server-scope", "some description")
        r = self.run_brain("new", "ai-mcp-server-scope", "--description", "unrelated text")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("SAME FACT", r.stdout)

    def test_a_middling_candidate_requires_an_explicit_choice(self):
        self.node("cloud-sql-contained-users",
                  "A fresh Azure SQL DB has zero contained users until you replicate "
                  "the source principals")
        r = self.run_brain("new", "cloud-sql-contained-user",
                           "--description", "Restoring a DB copy leaves logins orphaned")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("CANDIDATE", r.stdout)
        self.assertIn("--new", r.stderr)
        self.assertIn("--update", r.stderr)
        self.assertFalse((self.vault / "notes/cloud-sql-contained-user.md").exists())

    def test_new_records_the_choice_as_distinct_from(self):
        self.node("cloud-sql-contained-users", "zero contained users on a fresh DB")
        r = self.run_brain("new", "cloud-sql-contained-user", "--new",
                           "--description", "Restoring a DB copy leaves logins orphaned")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.fm("cloud-sql-contained-user")["distinct-from"],
                         "cloud-sql-contained-users")

    def test_update_converges_onto_the_target_and_forks_nothing(self):
        self.node("cloud-sql-contained-users", "zero contained users on a fresh DB")
        r = self.run_brain("new", "cloud-sql-contained-user",
                           "--update", "cloud-sql-contained-users",
                           "--description", "Restoring a DB copy leaves logins orphaned")
        self.assertEqual(r.returncode, 0, r.stderr)
        fm, body = notes.parse_note(
            (self.vault / "notes/cloud-sql-contained-users.md").read_text())
        self.assertEqual(fm["converged-with"], "cloud-sql-contained-user")
        self.assertIn("Restoring a DB copy", body)          # the fact is not lost
        self.assertIn("## Updates", body)
        self.assertFalse((self.vault / "notes/cloud-sql-contained-user.md").exists())

    def test_a_missing_update_target_is_an_error_not_a_silent_create(self):
        self.node("cloud-sql-contained-users", "zero contained users on a fresh DB")
        r = self.run_brain("new", "cloud-sql-contained-user", "--update", "cloud-sql-gone",
                           "--description", "Restoring a DB copy leaves logins orphaned")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not found", r.stderr)

    def test_nothing_similar_creates_normally(self):
        self.node("finance-ap-invoice-approval", "Invoices over 50k need two signatures")
        r = self.run_brain("new", "ai-mcp-server-scope",
                           "--description", "MCP servers are neutral infrastructure")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.vault / "notes/ai-mcp-server-scope.md").exists())

    def test_slug_text_alone_never_auto_merges(self):
        """REGRESSION: this pair scores 0.667 on slug text and they are different
        facts. A close slug may ASK (``choose``); only the description may merge.

        The source's fixture named a real product and a real store; both are
        genericized here, and the slug pair was chosen to hold the score EXACTLY
        (the arithmetic is in the chunk-4c handoff) so the case still sits in the
        same band it was written for.
        """
        self.node("ledger-batch2-reload-owner",
                  "The batch2 store-rebuild reload leg is a data-flow job, not a pipeline")
        r = self.run_brain("new", "data-batch2-reload-tables", "--new",
                           "--description", "The data-flow reload needs four pre-existing "
                                            "framework tables a fresh store lacks")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.vault / "notes/data-batch2-reload-tables.md").exists())


if __name__ == "__main__":
    unittest.main()
