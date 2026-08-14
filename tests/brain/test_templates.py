"""The shipped org-brain templates parse clean and round-trip through brain.notes.

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 5b) from the brain source tree's
``tests/test_templates.py`` @ 0.14.0 (TemplateLintTest 4 + RegistrySeedTest 1, all
five 1:1). Rewrites: ``note_io`` -> ``brain.notes``; the template root is this
repo's ``templates/org-brain`` (repo root, beside ``skills/`` and ``docs/``), not
the source plugin's; the source's ``from tests.base import BrainTestCase``
sys.path trick becomes ``from tests.brain.base import LIB``, which does the same
job (importing it puts ``lib/`` on the path).

These three files are what a NEW org brain is seeded from — they land in someone
else's repo, so every command they name has to be the real entry point. The
ADDED sixth case is the guard for exactly that.
"""
import json
import re
import unittest

from tests.brain.base import LIB

import brain.notes as notes

REPO = LIB.parent
TEMPLATES = REPO / "templates/org-brain"

# ``brain.<module>`` as written in prose or in a runnable line. A trailing
# attribute (``brain.notes.knowledge_stamp``) still names the module first.
_MODULE_RX = re.compile(r"\bbrain\.([a-z_][a-z0-9_]*)")


class TemplateLintTest(unittest.TestCase):
    def _check(self, slug, allowed_types):
        path = TEMPLATES / f"{slug}.md"
        self.assertTrue(path.exists(), f"{path} missing")
        fm, body = notes.parse_note(path.read_text())
        self.assertEqual(fm.get("name"), slug)                       # name matches filename
        self.assertTrue(fm.get("description"))                       # non-empty description
        self.assertIn(fm.get("type"), allowed_types)                 # org-brain schema type
        self.assertRegex(fm.get("verified", ""), r"^\d{4}-\d{2}-\d{2}$")
        self.assertTrue(body.strip())                                # has content
        # round-trip: re-emit the frontmatter and re-parse -> identical (YAML-safe)
        reparsed, _ = notes.parse_note(notes.dump_frontmatter(fm) + "\nx\n")
        self.assertEqual(reparsed, fm)
        return fm, body

    def test_routing_spec_lint_clean(self):
        _, body = self._check("routing-spec", {"architecture", "reference"})
        self.assertIn("HOOK", body)
        self.assertIn("enforcement spectrum", body.lower())

    def test_team_rules_lint_clean(self):
        _, body = self._check("team-rules", {"reference", "architecture"})
        # starter rules present as one-line bullets
        self.assertGreaterEqual(len([l for l in body.splitlines() if l.startswith("- **")]), 3)

    def test_routing_spec_documents_shared_brain(self):
        _, body = self._check("routing-spec", {"architecture", "reference"})
        low = body.lower()
        self.assertIn("shared brain", low)
        self.assertIn("scope: private", body)      # the opt-out is documented
        self.assertIn("peers", low)                # peers are read-only

    def test_team_rules_has_publish_rule(self):
        _, body = self._check("team-rules", {"reference", "architecture"})
        self.assertTrue(any("shared brain" in l.lower() and l.startswith("- **")
                            for l in body.splitlines()),
                        "team-rules must carry the publish-to-shared-brain rule")


class RegistrySeedTest(unittest.TestCase):
    def test_registry_seed_parses(self):
        path = TEMPLATES / "registry.json"
        self.assertTrue(path.exists(), f"{path} missing")
        data = json.loads(path.read_text())      # must be valid JSON (no real // comments)
        self.assertIn("people", data)
        self.assertIsInstance(data["people"], list)
        for p in data["people"]:
            self.assertIn("alias", p)
            self.assertIn("name", p)
            self.assertIn("shared", p)
        self.assertIn("_comment", data)          # a commented example, per spec


class TemplateEntryPointTest(unittest.TestCase):
    """ADDED (no source counterpart). The templates seed a DIFFERENT repo, so a
    command they name is copied out of this repo's reach before anyone runs it.
    The 3.0.0 port moved every one of them from a ``scripts/<file>.py`` path to a
    ``brain.<module>`` module, and nothing but this test notices when one is
    missed or misspelled."""

    FILES = ("routing-spec.md", "team-rules.md", "registry.json")

    def _text(self, name):
        return (TEMPLATES / name).read_text()

    def test_no_template_invokes_a_module_by_path(self):
        for name in self.FILES:
            self.assertNotIn("scripts/", self._text(name),
                             f"{name} still invokes a module by its old scripts/ path")

    def test_every_named_brain_module_exists(self):
        seen = set()
        for name in self.FILES:
            for mod in _MODULE_RX.findall(self._text(name)):
                seen.add(mod)
                self.assertTrue((LIB / "brain" / f"{mod}.py").exists(),
                                f"{name} names brain.{mod}, which does not exist")
        # a template that names no module at all would pass the loop vacuously
        self.assertIn("search", seen)


if __name__ == "__main__":
    unittest.main()
