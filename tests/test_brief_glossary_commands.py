"""Task 7: Claude adapters for /glossary and /brief — static file checks
(mirrors test_save_history_commands.py). The engine is host-agnostic; these thin
adapters wire it to Claude Code. Stdlib-only, no LLM."""
import os
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_COMMANDS = os.path.join(_REPO_ROOT, "commands")
_SKILLS = os.path.join(_REPO_ROOT, "skills")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class GlossaryCommandFileTest(unittest.TestCase):
    def setUp(self):
        self.text = _read(os.path.join(_COMMANDS, "glossary.md"))

    def test_frontmatter_fields(self):
        self.assertIn("description:", self.text)
        self.assertIn("argument-hint:", self.text)
        self.assertIn("allowed-tools: Bash", self.text)
        self.assertIn("disable-model-invocation: true", self.text)

    def test_bang_line_runs_engine_glossary_with_arguments(self):
        self.assertIn("glossary $ARGUMENTS", self.text)
        self.assertIn("task-station.py", self.text)
        self.assertIn("--session", self.text)

    def test_body_mentions_glossary_add_capture(self):
        self.assertIn("glossary add", self.text)
        self.assertIn("verbatim", self.text)


class BriefCommandFileTest(unittest.TestCase):
    def setUp(self):
        self.text = _read(os.path.join(_COMMANDS, "brief.md"))

    def test_frontmatter_fields(self):
        self.assertIn("description:", self.text)
        self.assertIn("argument-hint:", self.text)
        # /brief is a model-driven skill flow — Bash, Read, Write, Edit; NOT disabled.
        self.assertIn("allowed-tools: Bash, Read, Write, Edit", self.text)
        self.assertNotIn("disable-model-invocation: true", self.text)

    def test_points_at_the_brief_skill(self):
        self.assertIn("brief` skill", self.text)
        self.assertIn("brief render", self.text)


class BriefSkillFileTest(unittest.TestCase):
    def setUp(self):
        self.text = _read(os.path.join(_SKILLS, "brief", "SKILL.md"))

    def test_frontmatter_name_and_description(self):
        self.assertIn("name: brief", self.text)
        self.assertIn("description:", self.text)

    def test_documents_flow_and_render(self):
        for phrase in ("Gather", "brief-spec", "brief render", "Brief-spec schema"):
            self.assertIn(phrase, self.text)

    def test_never_write_html_or_css(self):
        self.assertIn("never write html or css", self.text.lower())

    def test_publish_note_is_host_side_artifact(self):
        self.assertIn("--publish", self.text)
        self.assertIn("Artifact", self.text)

    def test_glossary_auto_documented(self):
        self.assertIn('"glossary": "auto"', self.text)


if __name__ == "__main__":
    unittest.main()
