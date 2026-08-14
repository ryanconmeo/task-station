"""The heal rename: ``/brain-heal`` is the only name, and the retired one is gone.

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 5b) from the brain source tree's
``tests/test_rename.py`` @ 0.14.0 — the TWO surviving claims only.

The source's third case, ``test_reconcile_stub_delegates``, asserted that a
compatibility-alias skill (``skills/brain-reconcile/SKILL.md``) still shipped so
old muscle memory would not 404. That alias is NOT ported (hub ruling: it exists
for a userbase this repo does not have), so the case DIES with it — recorded in
the chunk-5b handoff as a deviation rather than dropped quietly. Its inverse is
here instead: the alias must stay absent, so nobody "restores" it by reflex.

The stale-reference sweep is re-derived: the source scanned three ``scripts/*.py``
files, all three of which are now package modules under a different name.
"""
import unittest

from tests.brain.base import LIB

REPO = LIB.parent

# The retired command name, in the two spellings a shipped file could carry: the
# slash-command a user types, and the skill directory it would live in.
RETIRED_COMMAND = "/brain-reconcile"
RETIRED_SKILL_DIR = "brain-reconcile"

# Everything this repo SHIPS that a user or a session reads as instructions.
SHIPPED_PROSE = (
    "skills/brain/SKILL.md",
    "skills/brain-heal/SKILL.md",
    "skills/brain-init/SKILL.md",
    "skills/brain-promote/SKILL.md",
    "skills/brain-save/SKILL.md",
    "skills/ado/SKILL.md",
    "system-instructions.md",
    "templates/org-brain/routing-spec.md",
    "templates/org-brain/team-rules.md",
    "docs/brain-naming.md",
)

# The modules whose user-facing nag / notification text names the command. These
# are the source's three files, under their ported names.
NAGGING_MODULES = ("brain/heal_gate.py", "brain/heal_lint.py", "brain/distill.py")


class RenameTest(unittest.TestCase):
    def test_brain_heal_skill_exists(self):
        skill = REPO / "skills/brain-heal/SKILL.md"
        self.assertTrue(skill.exists())
        text = skill.read_text()
        self.assertIn("name: brain-heal", text)
        self.assertIn("tier-lint", text.lower())

    def test_no_stale_brain_reconcile_command_refs(self):
        # the user-facing nag/notification must point at /brain-heal now
        for rel in NAGGING_MODULES:
            path = LIB / rel
            self.assertTrue(path.exists(), f"{rel} missing")
            self.assertNotIn(RETIRED_COMMAND, path.read_text(),
                             f"{rel} still references {RETIRED_COMMAND}")

    def test_no_shipped_prose_references_the_retired_command(self):
        """ADDED — the source only checked the three nagging modules, because the
        skills were the thing being renamed. Here the skills, the SessionStart
        routing document and the org-brain templates all ship, and every one of
        them could name a command that no longer exists."""
        for rel in SHIPPED_PROSE:
            path = REPO / rel
            self.assertTrue(path.exists(), f"{rel} missing")
            self.assertNotIn(RETIRED_COMMAND, path.read_text(),
                             f"{rel} still references {RETIRED_COMMAND}")

    def test_the_compat_alias_skill_does_not_ship(self):
        """ADDED — the inverse of the source case this port dropped. The alias was
        a migration aid for the standalone plugin's existing users; shipping it
        here would advertise a command this repo never had."""
        self.assertFalse((REPO / "skills" / RETIRED_SKILL_DIR).exists(),
                         "the retired alias skill is back — it was dropped deliberately")


if __name__ == "__main__":
    unittest.main()
