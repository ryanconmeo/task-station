"""brain.init_home — the reversible migrate-then-link home setup.

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 5a) from the brain source tree's
``tests/test_init_home.py`` @ 0.14.0 (13 cases) plus
``tests/test_team_rules.py::ClaudeMdImportTest`` (5 cases), which drives the same
module and belongs with it. All 18 port. What changed:

  * the primary config file is named ONCE for the whole suite, in
    ``tests/brain/base.PRIMARY_CONFIG_REL`` — the source spelled its own
    org-branded filename in six places;
  * the profile-less config no longer carries an ``ado_org`` default or a keyword
    list, because the port removed both org literals. Two ADDED cases pin the
    absence, so a future "helpful default" cannot creep back in unnoticed;
  * ``test_onboarding_checklist_reflects_state``'s marketplace token is this
    repo's (the brain plane ships inside task-station).

ADDED beyond the source: the config-contract cases. ``init_home`` WRITES the
files ``brain.config`` READS, and the source had no test that the two agree — a
drift there produces a "successful" init whose config the loader never finds.
"""
import json
import os
import unittest
from pathlib import Path

from tests.brain.base import BrainTestCase, PRIMARY_CONFIG_REL

import brain.config as bconfig
import brain.init_home as init_home


class FreshInitTest(BrainTestCase):
    def test_creates_home_dirs_config_and_pointer(self):
        init_home.run()
        root = self.home / "brains"
        self.assertTrue((root / "brain").is_dir())
        self.assertTrue((root / "org-brain").is_dir())
        # scaffold landed
        self.assertTrue((root / "brain/INDEX.md").exists())
        # no stray .gitkeep survived
        self.assertEqual(list((root / "brain").rglob(".gitkeep")), [])
        # home config written with lowercase home-relative paths
        cfg = json.loads((root / "config.json").read_text())
        self.assertEqual(cfg["vault"], "~/brains/brain")
        # primary rewritten as a pointer
        primary = json.loads((self.home / PRIMARY_CONFIG_REL).read_text())
        self.assertEqual(primary, {"config": "~/brains/config.json"})

    def test_resolution_chain_end_to_end(self):
        init_home.run()
        self.assertEqual(bconfig.load()["vault"], self.home / "brains/brain")
        self.assertEqual(bconfig.load()["org_brain_clone"], self.home / "brains/org-brain")

    def test_prelinks_native_memory(self):
        init_home.run()
        native = self.home / ".claude/projects" / str(self.home).replace(os.sep, "-") / "memory"
        self.assertTrue(native.is_symlink())
        self.assertEqual(os.path.realpath(native),
                         os.path.realpath(self.home / "brains/brain/memory"))

    def test_the_whole_scaffold_lands(self):
        """ADDED — the four documents a fresh vault must have. The source asserted
        only INDEX.md; the scaffold is now a shipped asset inside the package and
        a missing file would surface as a confusing empty vault."""
        init_home.run()
        vault = self.home / "brains/brain"
        for name in ("INDEX.md", "LOG.md", "CLAUDE.md", "team-rules.md", ".gitignore"):
            self.assertTrue((vault / name).exists(), name)
        for d in ("notes", "projects", "plans", "raw", "reports/health"):
            self.assertTrue((vault / d).is_dir(), d)


class ScaffoldSourceTest(BrainTestCase):
    """ADDED — where the scaffold comes from. It is the THIRD (and last)
    sanctioned ``__file__`` anchor in the brain plane; the env fallback exists for
    a layout where the package is not the file next to it."""

    def test_scaffold_dir_is_the_packaged_one(self):
        d = init_home._scaffold_dir()
        self.assertTrue(d.is_dir())
        self.assertEqual(d.name, "vault-scaffold")
        self.assertEqual(d.parent.name, "brain")
        self.assertTrue((d / "CLAUDE.md").exists())

    def test_env_fallback_only_applies_when_the_local_copy_is_absent(self):
        # CLAUDE_PLUGIN_ROOT is not a brain config key, so base.setUp neither
        # clears nor restores it (D24) — back it up here rather than leaking a
        # pop into the rest of the run.
        prev = os.environ.get("CLAUDE_PLUGIN_ROOT")
        self.addCleanup(lambda: os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
                        if prev is None else os.environ.__setitem__("CLAUDE_PLUGIN_ROOT", prev))
        os.environ["CLAUDE_PLUGIN_ROOT"] = str(self.home / "elsewhere")
        # the packaged copy exists, so the env var must NOT win
        self.assertEqual(init_home._scaffold_dir().parent.name, "brain")


class ConfigContractTest(BrainTestCase):
    """ADDED — init writes what config reads. Every literal below has a
    counterpart in ``brain.config``; if either side moves, this fails instead of
    an init that silently produces a config nothing loads."""

    def test_pointer_target_is_configs_home_config_path(self):
        target = Path(os.path.expanduser(init_home.POINTER["config"]))
        self.assertEqual(target, bconfig._home_config_path())

    def test_default_paths_match_configs_defaults(self):
        cfg = init_home._build_config(None)
        self.assertEqual(Path(os.path.expanduser(cfg["vault"])), bconfig.DEFAULT_VAULT())
        self.assertEqual(Path(os.path.expanduser(cfg["org_brain_clone"])),
                         bconfig.DEFAULT_ORG_BRAIN())
        self.assertEqual(Path(os.path.expanduser(cfg["memory"])),
                         bconfig.DEFAULT_MEMORY(bconfig.DEFAULT_VAULT()))

    def test_init_writes_the_file_config_reads(self):
        init_home.run()
        self.assertTrue(bconfig._primary_config_path().exists())
        self.assertEqual(bconfig._primary_config_path(), self.home / PRIMARY_CONFIG_REL)


class ProfileTest(BrainTestCase):
    def _run_with_profile(self, profile):
        init_home.run(profile=profile)
        return json.loads((self.home / "brains/config.json").read_text())

    def test_github_profile_wires_labels_forge_keywords(self):
        cfg = self._run_with_profile({
            "org_label": "Acme Brain",
            "labels": {"private": "Personal", "org": "Org"},
            "keywords": ["proj-a", "proj-b"],
            "forge": {"kind": "github", "owner": "acme-org", "repo": "org-brain",
                      "target_branch": "main"},
        })
        self.assertEqual(cfg["org_label"], "Acme Brain")
        self.assertEqual(cfg["labels"], {"private": "Personal", "org": "Org"})
        self.assertEqual(cfg["inject_keywords"], ["proj-a", "proj-b"])
        self.assertEqual(cfg["forge_kind"], "github")
        self.assertEqual(cfg["forge_org"], "acme-org")   # owner -> forge_org
        self.assertEqual(cfg["forge_repo"], "org-brain")
        self.assertNotIn("forge_project", cfg)            # github has no project
        # end-to-end through the config loader
        loaded = bconfig.load()
        self.assertEqual(loaded["forge_kind"], "github")
        self.assertEqual(loaded["labels"], {"private": "Personal", "org": "Org"})
        self.assertEqual(loaded["inject_keywords"], ["proj-a", "proj-b"])

    def test_ado_profile_sets_ado_org(self):
        cfg = self._run_with_profile({
            "forge": {"kind": "ado", "org": "https://example.com/your-org",
                      "project": "Proj", "repo": "wiki"},
        })
        self.assertEqual(cfg["forge_kind"], "ado")
        self.assertEqual(cfg["forge_org"], "https://example.com/your-org")
        self.assertEqual(cfg["forge_project"], "Proj")
        self.assertEqual(cfg["ado_org"], "https://example.com/your-org")

    def test_no_profile_leaves_defaults_unchanged(self):
        with_profile = json.dumps(init_home._build_config(None), sort_keys=True)
        init_home.run()  # no profile
        on_disk = json.dumps(
            {k: v for k, v in json.loads(
                (self.home / "brains/config.json").read_text()).items()},
            sort_keys=True)
        self.assertEqual(on_disk, with_profile)
        # forge_* keys are NOT written without a profile
        cfg = json.loads((self.home / "brains/config.json").read_text())
        self.assertNotIn("forge_kind", cfg)
        self.assertNotIn("labels", cfg)

    def test_no_profile_writes_no_org_identity(self):
        """ADDED — the port's rule, made mechanical: org identity arrives at
        runtime from an OrgProfile, never from a literal in this repo. The source
        shipped one org's ADO url and one org's product names as DEFAULTS."""
        init_home.run()
        cfg = json.loads((self.home / "brains/config.json").read_text())
        self.assertNotIn("ado_org", cfg)
        self.assertEqual(cfg["inject_keywords"], [])
        self.assertEqual(init_home.ORG_KEYWORDS, [])

    def test_an_empty_keyword_list_means_injection_is_off_until_configured(self):
        """ADDED — the consequence of the line above, stated where a reader will
        find it: the injection hook treats an empty list as "disabled", so a fresh
        install injects nothing until the user or a profile sets keywords."""
        init_home.run()
        self.assertEqual(bconfig.load()["inject_keywords"], [])

    def test_broken_profile_is_a_clean_error(self):
        """ADDED — ``_load_profile``'s three failure modes, none of which the
        source covered, all of which a user hits by typo."""
        with self.assertRaises(ValueError):
            init_home._load_profile(self.home / "no-such-profile.json")
        bad = self.home / "bad.json"
        bad.write_text("{ not json")
        with self.assertRaises(ValueError):
            init_home._load_profile(bad)
        notdict = self.home / "list.json"
        notdict.write_text("[1, 2]")
        with self.assertRaises(ValueError):
            init_home._load_profile(notdict)


class RerunTest(BrainTestCase):
    def test_rerun_is_noop(self):
        init_home.run()
        cfg1 = (self.home / "brains/config.json").read_text()
        lines, conflicts = init_home.run()
        cfg2 = (self.home / "brains/config.json").read_text()
        self.assertEqual(cfg1, cfg2)
        self.assertEqual(conflicts, [])
        # second run must not create a .bak (primary is already a pointer)
        self.assertFalse((self.home / (PRIMARY_CONFIG_REL + ".bak")).exists())
        self.assertTrue(any("already" in ln.lower() for ln in lines))


class MigrateThenLinkTest(BrainTestCase):
    def test_moves_contents_and_links(self):
        native = self.make_native_memory(str(self.home), memory_md="- fact A\n")
        (native / "note.md").write_text("a native note\n")
        init_home.run()
        vm = self.home / "brains/brain/memory"
        self.assertTrue((vm / "note.md").exists())
        self.assertEqual((vm / "note.md").read_text(), "a native note\n")
        self.assertTrue(native.is_symlink())
        self.assertEqual(os.path.realpath(native), os.path.realpath(vm))

    def test_memory_md_line_union_merge(self):
        # pre-populate the vault memory so scaffolding is skipped and a merge happens
        vm = self.home / "brains/brain/memory"
        vm.mkdir(parents=True)
        (vm / "MEMORY.md").write_text("- fact A\n- fact B\n")
        self.make_native_memory(str(self.home), memory_md="- fact B\n- fact C\n")
        init_home.run()
        merged = (vm / "MEMORY.md").read_text().splitlines()
        self.assertEqual(merged, ["- fact A", "- fact B", "- fact C"])

    def test_refuse_clobber_on_conflict(self):
        vm = self.home / "brains/brain/memory"
        vm.mkdir(parents=True)
        (vm / "note.md").write_text("vault content\n")
        native = self.make_native_memory(str(self.home))
        (native / "note.md").write_text("DIFFERENT native content\n")
        lines, conflicts = init_home.run()
        # target content preserved, native left intact, NOT symlinked
        self.assertEqual((vm / "note.md").read_text(), "vault content\n")
        self.assertFalse(native.is_symlink())
        self.assertTrue((native / "note.md").exists())
        self.assertTrue(conflicts)

    def test_undo_leaves_vault_intact(self):
        native = self.make_native_memory(str(self.home), memory_md="- fact A\n")
        init_home.run()
        vm = self.home / "brains/brain/memory"
        self.assertTrue(native.is_symlink())
        # undo == remove only the link
        native.unlink()
        self.assertFalse(native.exists())
        self.assertTrue((vm / "MEMORY.md").exists())
        self.assertEqual((vm / "MEMORY.md").read_text(), "- fact A\n")


class BackupTest(BrainTestCase):
    def test_existing_full_config_backed_up(self):
        old = {"vault": str(self.home / "old-vault"), "inject_context": False}
        self.write_primary_config(old)
        init_home.run()
        bak = self.home / (PRIMARY_CONFIG_REL + ".bak")
        self.assertTrue(bak.exists())
        self.assertEqual(json.loads(bak.read_text()), old)
        primary = json.loads((self.home / PRIMARY_CONFIG_REL).read_text())
        self.assertIn("config", primary)
        # soft preference carried into the new config
        newcfg = json.loads((self.home / "brains/config.json").read_text())
        self.assertIs(newcfg["inject_context"], False)


class DryRunTest(BrainTestCase):
    def test_dry_run_makes_no_changes(self):
        lines, conflicts = init_home.run(dry_run=True)
        self.assertTrue(lines)
        self.assertFalse((self.home / "brains").exists())
        self.assertFalse((self.home / PRIMARY_CONFIG_REL).exists())


class ClaudeMdImportTest(BrainTestCase):
    """PORTED from the source's ``tests/test_team_rules.py`` — it drives
    ``init_home``, so it lands here rather than in a module of its own (its
    sibling ``AutoPullTest`` went to ``test_orgpull.py`` for the same reason)."""

    def _claude_md(self):
        return self.home / ".claude/CLAUDE.md"

    def test_adds_import_block_once_idempotently(self):
        init_home.run()
        text = self._claude_md().read_text()
        self.assertIn(init_home.TR_MARKER_START, text)
        self.assertIn("@~/brains/org-brain/team-rules.md", text)
        self.assertEqual(text.count(init_home.TR_MARKER_START), 1)
        init_home.run()  # rerun must not duplicate
        self.assertEqual(self._claude_md().read_text().count(init_home.TR_MARKER_START), 1)

    def test_preserves_existing_claude_md(self):
        self._claude_md().write_text("# My own rules\n\n- do the thing\n")
        init_home.run()
        text = self._claude_md().read_text()
        self.assertIn("# My own rules", text)              # existing content kept
        self.assertIn(init_home.TR_MARKER_START, text)     # block appended

    def test_no_claude_md_opt_out(self):
        init_home.run(no_claude_md=True)
        cm = self._claude_md()
        self.assertNotIn(init_home.TR_MARKER_START, cm.read_text() if cm.exists() else "")

    def test_dry_run_writes_nothing(self):
        init_home.run(dry_run=True)
        self.assertFalse(self._claude_md().exists())

    def test_onboarding_checklist_reflects_state(self):
        init_home.run()
        items = init_home._onboarding_checklist(self.home, no_claude_md=False)
        joined = "\n".join(items)
        for token in ("vault scaffolded", "memory linked", "keyword", "team-rules", "task-station"):
            self.assertIn(token, joined)
        self.assertTrue(any(i.startswith("✓ vault scaffolded") for i in items))
        self.assertTrue(any("✓" in i and "team-rules" in i for i in items))

    def test_the_import_target_is_the_configured_org_brain_clone(self):
        """ADDED — the @import path and the org-brain clone default are two
        spellings of one location; if they diverge the import silently points at
        a directory nothing ever pulls."""
        self.assertEqual(Path(os.path.expanduser(init_home.TEAM_RULES_IMPORT)),
                         bconfig.DEFAULT_ORG_BRAIN() / "team-rules.md")


if __name__ == "__main__":
    unittest.main()
