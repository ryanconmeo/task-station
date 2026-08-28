"""Vault lint (``brain.heal_lint``) — the deterministic, zero-token health check.

PROVENANCE: the ported module comes from the brain source tree's
``scripts/lint.py`` @ 0.14.0, which had **no dedicated test file**. Its only
source coverage was ``tests/test_naming_write_path.py::LintSeverityTest`` (3
cases, ported below verbatim in shape but driven in-process instead of through a
subprocess) and ``tests/test_publish.py``'s reuse of ``lint.SECRET_RX``, which is
chunk 4b's.

Every other case here is ADDED, one pair per CHECK CLASS the module implements:
each check must FIRE on a minimal fixture vault and stay QUIET on a clean one.
That pairing is the point — lint's failure mode is silence. A check that stopped
firing looks exactly like a healthy vault, and the whole feature is "run this and
believe the report".

The check classes, and where each is asserted:

  broken wikilinks   BrokenLinkTest      (incl. the three exemptions: code
                                          fences, plans/, memory → warn tier)
  memory namespace   MemoryNamespaceTest (#578 — memory resolves whether it
                                          sits inside or outside the vault)
  orphan notes       OrphanTest
  INDEX drift        IndexDriftTest      (both directions)
  frontmatter        FrontmatterTest     (missing name, name≠filename, bad date)
  stale verified:    StaleTest           (incl. notes/-only scoping)
  naming severity    LintSeverityTest    (PORTED — error counted, warn is not)
  secrets            SecretTest          (all four alternations of SECRET_RX)
  MEMORY.md rot      MemoryRotTest       (both directions)
  memory type        MemoryTypeTest      (feedback|user only; MEMORY.md and
                                          tombstones exempt)
  reference-dirty    ReferenceDirtyTest  (real git clone; warn tier)
  self-exemption     ReportsHealthTest   (lint never lints its own report)
  report + exit      RunTest

Assertions are per-BUCKET, never on the total: one fixture note trips several
checks at once (an unindexed note is also an orphan), so a total-based assertion
would pass for the wrong reason.

DEFERRED, named rather than faked: nothing. Every check class the module
implements is exercised here.
"""
import datetime
import subprocess
import unittest
from pathlib import Path

from tests.brain.base import BrainTestCase, LIB
from tests.brain.test_layers import STDLIB_OK, top_level_imports

import brain.heal_lint as heal_lint
import brain.references as references

DATE = "2026-07-14"


class HealLintFixture(BrainTestCase):
    """Vault + memory scaffolding. ZERO test methods, so it contributes no cases
    (the suite's established ``CliFixture`` / ``RefFixtureMixin`` shape)."""

    def setUp(self):
        super().setUp()
        self.vault = self.make_vault(self.home / "vault")
        self.memory = self.home / "memory"
        self.memory.mkdir(parents=True, exist_ok=True)
        # org_brain_clone points at a path that does not exist: load_contract
        # degrades to the shipped generic contract, which is what a machine
        # without the org tier really has.
        self.cfg = {"vault": self.vault, "memory": self.memory,
                    "org_brain_clone": self.home / "org_brain"}

    # --- fixtures -----------------------------------------------------------
    def note(self, slug, body="body text", folder="notes", index=True, **fm):
        """A vault note. Indexed by default so INDEX-drift noise does not leak
        into the bucket under test."""
        fields = {"name": slug, "description": "d", "type": "reference",
                  "scope": "personal", "source": "manual"}
        fields.update(fm)
        head = "\n".join(f"{k}: {v}" for k, v in fields.items() if v is not None)
        p = self.vault / folder / f"{slug}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"---\n{head}\n---\n\n{body}\n")
        if index and folder == "notes":
            self.add_index(slug)
        return p

    def add_index(self, *slugs):
        idx = self.vault / "INDEX.md"
        text = idx.read_text()
        idx.write_text(text + "".join(f"- [[{s}]] — d\n" for s in slugs))

    def mem(self, name, body="a memory", index=True, type="feedback"):
        (self.memory / f"{name}.md").write_text(
            f"---\nname: {name}\ndescription: d\nmetadata:\n  type: {type}\n---\n\n{body}\n")
        if index:
            self.mem_index(f"- [{name}]({name}.md) — d")
        return self.memory / f"{name}.md"

    def mem_index(self, *lines):
        idx = self.memory / "MEMORY.md"
        prev = idx.read_text() if idx.exists() else "# MEMORY\n\n"
        idx.write_text(prev + "".join(l + "\n" for l in lines))

    # --- drivers ------------------------------------------------------------
    def scan(self, **kw):
        kw.setdefault("today", DATE)
        return heal_lint.scan(self.cfg, **kw)

    def issues(self, bucket, **kw):
        return self.scan(**kw)[0][bucket]

    def info(self, bucket, **kw):
        return self.scan(**kw)[1][bucket]

    def exit_code(self, result):
        """What ``main()`` would exit with — ``sys.exit(1 if total else 0)``."""
        return 1 if result["total"] else 0


class BrokenLinkTest(HealLintFixture):
    def test_a_dangling_wikilink_is_an_issue(self):
        self.note("data-ledger-import", "see [[nowhere]] for the rest")
        found = self.issues("broken-links")
        self.assertEqual(len(found), 1)
        self.assertIn("[[nowhere]]", found[0])
        self.assertIn("data-ledger-import.md", found[0])

    def test_a_resolvable_wikilink_is_quiet(self):
        self.note("data-ledger-import", "see [[data-ledger-export]]")
        self.note("data-ledger-export", "the other half")
        self.assertEqual(self.issues("broken-links"), [])

    def test_a_link_inside_code_is_an_example_not_a_link(self):
        """Docs quote link syntax; a lint that read those would flag its own
        examples — including the vault CLAUDE.md that teaches the syntax."""
        self.note("data-ledger-import",
                  "inline `[[inline-example]]` and a block:\n\n```\n[[block-example]]\n```\n")
        self.assertEqual(self.issues("broken-links"), [])

    def test_a_plan_may_name_an_artifact_that_does_not_exist_yet(self):
        self.note("2026-08-02-naming-spec", "will produce [[not-written-yet]]",
                  folder="plans", index=False)
        self.assertEqual(self.issues("broken-links"), [])

    def test_a_dangling_link_in_memory_is_informational_not_counted(self):
        """A memory note's ``[[link]]`` is a to-write marker, not a defect — the
        heal skill treats these as work items, so counting them would make the
        exit code permanently non-zero."""
        self.mem("how-i-work", "still owe a note on [[nowhere]]")
        issues, info = self.scan()
        self.assertEqual(issues["broken-links"], [])
        self.assertEqual(len(info["memory-dangling"]), 1)
        self.assertIn("[[nowhere]]", info["memory-dangling"][0])


class MemoryNamespaceTest(HealLintFixture):
    """A [[wikilink]] between two REAL memory entries must resolve regardless
    of where the memory store lives relative to the vault (#578) — the
    resolution namespace has to look inside the resolved ``cfg["memory"]``
    path, not the vault-relative ``vault/"memory"`` guess."""

    def test_memory_to_memory_links_resolve_when_memory_is_outside_the_vault(self):
        """``HealLintFixture``'s default layout: memory is a sibling of the
        vault, never nested inside it — the shape this machine actually runs."""
        self.mem("how-i-work", "see [[who-ryan-is]] for background")
        self.mem("who-ryan-is", "the other half")
        issues, info = self.scan()
        self.assertEqual(issues["broken-links"], [])
        self.assertEqual(info["memory-dangling"], [])

    def test_memory_to_memory_links_resolve_when_memory_is_inside_the_vault(self):
        """The shipped default (memory nested under the vault) must keep
        working — a fix that only special-cased the outside path would pass
        the sibling test above while silently breaking this one instead."""
        self.memory = self.vault / "memory"
        self.memory.mkdir(parents=True, exist_ok=True)
        self.cfg["memory"] = self.memory
        self.mem("how-i-work", "see [[who-ryan-is]] for background")
        self.mem("who-ryan-is", "the other half")
        issues, info = self.scan()
        self.assertEqual(issues["broken-links"], [])
        self.assertEqual(info["memory-dangling"], [])


    def test_the_vault_relative_fallback_still_finds_memory_when_none_is_passed(self):
        """The ONLY branch the two tests above never reach. Both of them set
        ``cfg["memory"]``, so ``memory`` is never None and the
        ``else vault / "memory"`` fallback is dead in their runs — I deleted that
        fallback outright and the whole 43-test suite still passed, which is what
        exposed the hole. This calls the function directly with no memory
        argument, which is the only way to exercise it."""
        (self.vault / "memory").mkdir(parents=True, exist_ok=True)
        (self.vault / "memory" / "how-i-work.md").write_text("body\n")
        names = heal_lint.all_note_basenames(self.vault)
        self.assertIn("how-i-work", names)


class OrphanTest(HealLintFixture):
    def test_a_note_nobody_links_and_that_links_nobody_is_an_orphan(self):
        self.note("data-ledger-import", "no links at all")
        self.assertEqual(self.issues("orphans"), ["notes/data-ledger-import.md"])

    def test_a_linked_pair_is_not_orphaned_in_either_direction(self):
        self.note("data-ledger-import", "see [[data-ledger-export]]")
        self.note("data-ledger-export", "no links")
        # the source has outbound links; the target has an inbound one
        self.assertEqual(self.issues("orphans"), [])


class IndexDriftTest(HealLintFixture):
    def test_a_note_missing_from_the_index_is_drift(self):
        self.note("data-ledger-import", index=False)
        self.assertEqual(self.issues("index-drift"),
                         ["notes/data-ledger-import.md not in INDEX.md"])

    def test_an_index_entry_for_a_missing_note_is_drift(self):
        self.add_index("data-ledger-gone")
        found = self.issues("index-drift")
        self.assertEqual(found, ["INDEX.md lists dead [[data-ledger-gone]]"])

    def test_an_indexed_note_is_quiet_in_both_directions(self):
        self.note("data-ledger-import")
        self.assertEqual(self.issues("index-drift"), [])


class FrontmatterTest(HealLintFixture):
    def test_a_note_without_a_name_is_flagged(self):
        self.note("data-ledger-import", name=None)
        self.assertEqual(self.issues("frontmatter"),
                         ["notes/data-ledger-import.md: missing frontmatter/name"])

    def test_a_name_that_disagrees_with_the_filename_is_flagged(self):
        self.note("data-ledger-import", name="data-ledger-export")
        found = self.issues("frontmatter")
        self.assertEqual(len(found), 1)
        self.assertIn("!= filename", found[0])

    def test_an_unparseable_verified_date_is_a_frontmatter_issue(self):
        """It lands in frontmatter, NOT in stale — an unreadable date is a broken
        field, and calling it stale would tell the reader to re-verify a note
        whose real problem is that nothing can read its stamp."""
        self.note("data-ledger-import", verified="soon")
        issues = self.scan()[0]
        self.assertEqual(issues["stale"], [])
        self.assertIn("bad verified date 'soon'", issues["frontmatter"][0])


class StaleTest(HealLintFixture):
    def test_a_note_verified_past_the_window_is_stale(self):
        self.note("data-ledger-import", verified="2026-01-01")
        found = self.issues("stale", stale_days=90)
        self.assertEqual(len(found), 1)
        self.assertIn("verified 2026-01-01", found[0])
        expected = (datetime.date.fromisoformat(DATE)
                    - datetime.date.fromisoformat("2026-01-01")).days
        self.assertIn(f"({expected}d)", found[0])

    def test_a_recently_verified_note_is_quiet(self):
        self.note("data-ledger-import", verified="2026-07-01")
        self.assertEqual(self.issues("stale", stale_days=90), [])

    def test_staleness_is_scoped_to_notes(self):
        """``projects/`` nodes track live work and are re-verified by the work
        itself; only ``notes/`` carries the standing-claim freshness contract."""
        self.note("data-ledger-import", folder="projects", verified="2020-01-01",
                  index=False)
        self.assertEqual(self.issues("stale", stale_days=90), [])


class SecretTest(HealLintFixture):
    SHAPES = [
        'password = "hunter2hunter2hunter2"',
        "ghp_0123456789abcdefghijABCDEFGHIJ",
        "sk-ant-0123456789abcdef",
        "-----BEGIN RSA PRIVATE KEY-----",
    ]

    def test_every_alternation_of_the_pattern_fires(self):
        for i, shape in enumerate(self.SHAPES):
            self.note(f"data-secret-case{i}", f"the value is {shape} here")
        found = self.issues("secrets")
        self.assertEqual(len(found), len(self.SHAPES), found)
        for i, shape in enumerate(self.SHAPES):
            # named per shape so a failure says WHICH alternation stopped matching
            self.assertTrue(any(f"data-secret-case{i}.md" in x for x in found),
                            f"no finding for {shape!r}")

    def test_a_secret_inside_a_code_fence_is_an_example(self):
        """The vault's own docs show what a leaked token looks like; flagging
        those trains the reader to ignore the section."""
        self.note("data-secret-doc", "an example:\n\n```\nghp_0123456789abcdefghijABCDEFGHIJ\n```\n")
        self.assertEqual(self.issues("secrets"), [])

    def test_ordinary_prose_is_quiet(self):
        self.note("data-ledger-import", "the password policy is documented elsewhere")
        self.assertEqual(self.issues("secrets"), [])


class MemoryRotTest(HealLintFixture):
    def test_an_index_line_pointing_at_a_missing_file_is_rot(self):
        self.mem_index("- [gone](gone.md) — d")
        found = self.issues("memory-rot")
        self.assertEqual(found, ["MEMORY.md links missing file gone.md"])

    def test_a_memory_file_missing_from_the_index_is_rot(self):
        self.mem("how-i-work", index=False)
        self.mem_index()  # an index exists, but does not list it
        self.assertEqual(self.issues("memory-rot"),
                         ["memory/how-i-work.md not in MEMORY.md index"])

    def test_a_consistent_memory_store_is_quiet(self):
        self.mem("how-i-work")
        self.assertEqual(self.issues("memory-rot"), [])


class MemoryTypeTest(HealLintFixture):
    """memory/ holds ONLY how-to-work-with-Ryan facts — harness types
    ``feedback`` and ``user``. Anything else (a system fact, a per-repo rule,
    a fact about someone else) drifted in and belongs elsewhere."""

    def test_a_reference_typed_memory_is_flagged(self):
        self.mem("how-i-work", type="reference")
        found = self.issues("memory-type")
        self.assertEqual(len(found), 1)
        self.assertIn("how-i-work", found[0])
        self.assertIn("notes/", found[0])

    def test_a_project_typed_memory_is_flagged(self):
        self.mem("release-freeze", type="project")
        found = self.issues("memory-type")
        self.assertEqual(len(found), 1)
        self.assertIn("release-freeze", found[0])
        self.assertIn("notes/", found[0])

    def test_feedback_and_user_typed_memory_are_both_quiet(self):
        self.mem("how-i-work", type="feedback")
        self.mem("who-ryan-is", type="user")
        self.assertEqual(self.issues("memory-type"), [])

    def test_memory_md_itself_is_never_flagged(self):
        self.mem_index("- [how-i-work](how-i-work.md) — d")
        self.assertEqual(self.issues("memory-type"), [])

    def test_a_tombstone_is_quiet_regardless_of_its_stale_type(self):
        """tier-lint leaves a tombstone behind when it re-files an entry — its
        stale ``type:`` is already handled and must not double-flag."""
        (self.memory / "how-i-work.md").write_text(
            "---\nname: how-i-work\ndescription: d\nmetadata:\n  type: reference\n---\n\n"
            "<!-- MOVED to notes/how-i-work.md by tier-lint 2026-07-14 -->\n")
        self.mem_index("- [how-i-work](how-i-work.md) — d")
        self.assertEqual(self.issues("memory-type"), [])

    def test_a_lone_misfiled_memory_is_counted_toward_the_exit_code(self):
        self.mem("how-i-work", type="reference")
        out = heal_lint.run(self.cfg, today=DATE)
        self.assertEqual(out["total"], 1, out["report"])
        self.assertEqual(self.exit_code(out), 1)


class ReportsHealthTest(HealLintFixture):
    def test_lint_never_lints_its_own_report(self):
        """The report quotes every finding VERBATIM — including the broken links
        and the secret snippets. Linting it would make each run manufacture the
        next run's findings, and the count would climb forever."""
        out = self.vault / "reports/health" / f"{DATE}-lint.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("# Lint\n\n- notes/x.md: [[nowhere]]\n"
                       "- ghp_0123456789abcdefghijABCDEFGHIJ\n")
        issues = self.scan()[0]
        self.assertEqual(issues["broken-links"], [])
        self.assertEqual(issues["secrets"], [])


class ReferenceDirtyTest(HealLintFixture):
    """The one check that reads git. Warn-tier: a moved org node is news, not a
    defect in this vault, and the memo feed (chunk 4c) is what acts on it."""

    def setUp(self):
        super().setUp()
        self.clone = self.home / "org_brain"
        self.cfg["org_brain_clone"] = self.clone
        (self.clone / "notes").mkdir(parents=True)
        self._node("v1")
        self._git("init")
        self._git("config", "user.email", "t@e.com")
        self._git("config", "user.name", "T")
        self._git("add", "-A")
        self._git("commit", "-m", "c1")
        self.rev1 = self._head()

    def _git(self, *args):
        return subprocess.run(["git", "-C", str(self.clone), *args],
                              capture_output=True, text=True)

    def _head(self):
        return self._git("rev-parse", "HEAD").stdout.strip()

    def _node(self, body):
        (self.clone / "notes" / "data-org-node.md").write_text(
            f"---\nname: data-org-node\ndescription: d\n---\n\n{body}\n")

    def _advance(self):
        self._node("v2")
        self._git("add", "-A")
        self._git("commit", "-m", "c2")

    def test_a_reference_whose_org_node_advanced_is_reported_informationally(self):
        references.ref_add(self.cfg, "data-org-node", "task-station:42",
                           today=DATE, org_rev=self.rev1, commit=False)
        self._advance()
        issues, info = self.scan()
        self.assertEqual(len(info["reference-dirty"]), 1)
        self.assertIn("data-org-node", info["reference-dirty"][0])
        self.assertIn("re-fetch", info["reference-dirty"][0])
        # warn tier: it never reaches the counted buckets
        self.assertEqual(sum(len(v) for k, v in issues.items()), 0, issues)

    def test_a_current_reference_is_quiet(self):
        references.ref_add(self.cfg, "data-org-node", "task-station:42",
                           today=DATE, org_rev=self._head(), commit=False)
        self.assertEqual(self.info("reference-dirty"), [])


class LintSeverityTest(HealLintFixture):
    """PORTED from ``tests/test_naming_write_path.py`` @ 0.14.0 (chunk 2 deferred
    these three by name). Lint splits naming findings by severity instead of
    flattening them: an unregistered domain is an error and counts toward the
    exit code; a shape warning stays informational, because a refusal makes an
    author drop the fact rather than write a better name.

    The source drove ``lint.py`` as a SUBPROCESS and asserted on stdout +
    returncode; these call ``run()`` in-process and assert on the same report
    text, with ``exit_code()`` standing in for the process exit status. The
    source's ``--stale-days 99999`` is kept as the ``stale_days`` argument.
    """

    def run_lint(self):
        return heal_lint.run(self.cfg, stale_days=99999, today=DATE)

    def test_an_unregistered_domain_is_counted_as_an_issue(self):
        self.note("hammerspoon-dollar-expansion", index=False)
        out = self.run_lint()
        self.assertEqual(self.exit_code(out), 1)
        self.assertIn("## naming", out["report"])
        self.assertIn("unregistered-domain", out["report"])

    def test_a_warn_finding_stays_informational_and_is_not_counted(self):
        self.note("ai-plugin-update-is-version-gated", index=False)
        out = self.run_lint()
        # informational section, and NOT part of the counted naming issues
        self.assertIn("claim-shaped", out["report"])
        self.assertIn("informational, not counted", out["report"])
        counted = out["report"].split("informational, not counted")[0]
        self.assertNotIn("claim-shaped", counted)

    def test_the_error_line_carries_its_fix_hint(self):
        self.note("financ-ap-invoice-approval", index=False)
        out = self.run_lint()
        self.assertIn("closest registered domain", out["report"])


class RunTest(HealLintFixture):
    def test_a_dirty_vault_writes_a_report_and_a_log_line(self):
        self.note("data-ledger-import", "see [[nowhere]]")
        out = heal_lint.run(self.cfg, today=DATE)
        self.assertEqual(self.exit_code(out), 1)
        self.assertEqual(out["report_path"], self.vault / "reports/health" / f"{DATE}-lint.md")
        self.assertTrue(out["report_path"].exists())
        self.assertEqual(out["report_path"].read_text(), out["report"])
        log = (self.vault / "LOG.md").read_text()
        self.assertIn(f"{DATE}", log)
        self.assertIn(f"lint · {out['total']} issues", log)

    def test_a_clean_vault_writes_nothing(self):
        out = heal_lint.run(self.cfg, today=DATE)
        self.assertEqual(out["total"], 0)
        self.assertEqual(self.exit_code(out), 0)
        self.assertIsNone(out["report_path"])
        self.assertIn("clean", out["report"])
        self.assertFalse((self.vault / "reports/health").exists())
        self.assertEqual((self.vault / "LOG.md").read_text(), "# LOG\n")

    def test_the_warn_tier_never_moves_the_total(self):
        """A vault whose ONLY findings are informational still exits 0 — that is
        what makes the daily gate cheap; if warnings counted, every vault would
        be permanently 'dirty'."""
        self.mem("how-i-work", "still owe a note on [[nowhere]]")
        out = heal_lint.run(self.cfg, today=DATE)
        self.assertEqual(out["total"], 0)
        self.assertTrue(any(out["info"].values()))
        self.assertIsNone(out["report_path"])
        # a clean run still SHOWS the informational findings
        self.assertIn("memory-dangling", out["report"])


class ImportPurityTest(unittest.TestCase):
    """The source resolved its config at IMPORT time (``_CFG = pb_config.load()``
    with ``VAULT``/``MEMORY`` module globals). That froze the vault for every
    importer — and ``publish`` imports this module for nothing but
    :data:`SECRET_RX`. Every path now arrives as an argument; this pins it.
    """

    def test_no_module_level_path_state(self):
        leaked = [n for n in dir(heal_lint)
                  if isinstance(getattr(heal_lint, n), Path)]
        self.assertEqual(leaked, [], f"import-time path state is back: {leaked}")

    def test_the_retired_globals_are_gone(self):
        for name in ("_CFG", "VAULT", "MEMORY"):
            self.assertFalse(hasattr(heal_lint, name), name)


class HealLayeringTest(unittest.TestCase):
    """ADDED — the layer rule for the five modules this chunk adds, read with
    ``ast`` (chunk 1's reader, which also sees function-local imports). Each
    chunk keeps its own copy so its claims stay reviewable on their own.
    Relative sibling imports (``from . import config``) are invisible to the
    reader by design — they cannot cross a layer.
    """

    FILES = ("brain/heal_tier.py", "brain/heal_lint.py", "brain/heal_gate.py",
             "brain/orgpull.py", "brain/publish_setup.py")
    # ``platform`` is new to the brain plane with heal_lint's macOS notification.
    # Kept LOCAL to this chunk rather than widened into chunk 1's STDLIB_OK, so
    # chunk 1's guard still says exactly what chunk 1 reviewed.
    OK = STDLIB_OK | {"platform"}

    def test_no_module_reaches_the_board(self):
        for rel in self.FILES:
            self.assertNotIn("board", top_level_imports(LIB / rel), rel)

    def test_each_module_reaches_only_core_and_stdlib(self):
        for rel in self.FILES:
            path = LIB / rel
            self.assertTrue(path.exists(), f"{rel} missing")
            extra = top_level_imports(path) - self.OK - {"core"}
            self.assertEqual(extra, set(), f"{rel} reaches outside core+stdlib: {sorted(extra)}")


if __name__ == "__main__":
    unittest.main()
