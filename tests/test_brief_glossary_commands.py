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
        # `$TS_ARGV`, not `$ARGUMENTS`: the typed text is captured by a quoted
        # heredoc first, so it splits into words without being re-read as shell.
        self.assertIn("<<'TS_ARGV_END'", self.text)
        self.assertIn("glossary $TS_ARGV", self.text)
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
    """The skill authors the HTML itself now, against a shipped stylesheet + diagram
    catalogue. These are static checks that the 14 hand-won rules are still in it —
    the rules ARE the deliverable, so silently losing one is a regression."""

    def setUp(self):
        self.text = _read(os.path.join(_SKILLS, "brief", "SKILL.md"))
        self.lower = self.text.lower()

    def test_frontmatter_name_and_description(self):
        self.assertIn("name: brief", self.text)
        self.assertIn("description:", self.text)

    def test_documents_the_authoring_flow(self):
        for phrase in ("Gather", "Derive the sections", "Self-check", "brief path"):
            self.assertIn(phrase, self.text)

    def test_authors_the_html_against_the_shipped_stylesheet(self):
        self.assertIn("assets/brief.css", self.text)
        self.assertIn("references/diagrams.md", self.text)
        # the old prohibition is gone — the skill writes the HTML now
        self.assertNotIn("never write html or css", self.lower)

    def test_not_one_template(self):
        self.assertIn("is not one template", self.lower)
        self.assertIn("no fixed section list", self.lower)

    def test_all_fourteen_rules_are_numbered_and_present(self):
        for n in range(1, 15):
            self.assertIn("## Rule %d" % n, self.text,
                          "rule %d heading missing from the skill" % n)

    def test_section_derivation_rule_carries_the_worked_example(self):
        # rule 1 — sections are the reader's questions, with the real example list
        self.assertIn("reader's questions", self.text)
        for section in ("Broken now", "seeds → test-data", "Fixture vs run",
                        "Who may write what", "Smoke vs e2e", "Limits", "Plan"):
            self.assertIn(section, self.text)

    def test_prose_budget_is_shape_not_word_count(self):
        # rule 3 — both failure ends are documented, and the lever is named
        for token in ("2,600", "1,540", "1,280"):
            self.assertIn(token, self.text)
        self.assertIn("word count is not the lever", self.lower)

    def test_persona_badges(self):
        self.assertIn('class="who dev"', self.text)
        self.assertIn('class="who qa"', self.text)

    def test_diagram_catalogue_lists_the_four_patterns(self):
        for pattern in ("promotion ladder", "lifetime timeline", "pipeline flow",
                        "breadth vs depth"):
            self.assertIn(pattern, self.lower)

    def test_collapse_rule_states_open_what_collapsed_how(self):
        self.assertIn("<details>", self.text)
        self.assertIn("Open = what and why", self.text)
        self.assertIn("Collapsed = how", self.text)

    def test_limits_section_is_mandatory(self):
        self.assertIn("`Limits` section is mandatory", self.text)

    def test_only_real_commands(self):
        self.assertIn("Grep the repo for the actual script names", self.text)

    def test_mandatory_slots_and_footer(self):
        for slot in ("thesis", "Limits", "Plan", "footer"):
            self.assertIn(slot, self.text)
        self.assertIn("ADO tree", self.text)

    def test_voice_do_not_list_is_explicit(self):
        self.assertIn("do not sound like ai", self.lower)
        for banned in ("Rhetorical setups", "Cadence tricks",
                       "Self-congratulating summaries", "Stacked em-dash asides",
                       "next steps"):
            self.assertIn(banned, self.text)

    def test_final_self_check_list(self):
        self.assertIn("Self-check", self.text)
        self.assertIn("before writing the file", self.lower)

    def test_glossary_terms_are_forced_vocabulary(self):
        self.assertIn("task-station glossary --task", self.text)
        self.assertIn("verbatim", self.text)

    def test_publish_note_is_host_side_artifact(self):
        self.assertIn("--publish", self.text)
        self.assertIn("Artifact", self.text)

    def test_render_spec_retained_for_back_compat(self):
        self.assertIn("brief render", self.text)
        self.assertIn("back-compat", self.lower)


class BriefAssetsTest(unittest.TestCase):
    """The stylesheet and diagram catalogue the skill authors against."""

    def setUp(self):
        self.css = _read(os.path.join(_SKILLS, "brief", "assets", "brief.css"))
        self.dgm = _read(os.path.join(_SKILLS, "brief", "references", "diagrams.md"))

    def test_css_defines_the_three_semantic_hues(self):
        for tok in ("--promote:", "--hold:", "--broken:"):
            self.assertIn(tok, self.css)

    def test_css_defines_the_three_type_roles(self):
        for tok in ("--sans:", "--mono:", "--serif:"):
            self.assertIn(tok, self.css)

    def test_css_themes_both_ways(self):
        # the media query gives the default, data-theme overrides it in BOTH directions
        self.assertIn("@media (prefers-color-scheme: dark)", self.css)
        self.assertIn(':root[data-theme="dark"]', self.css)
        self.assertIn(':root[data-theme="light"]', self.css)

    def test_css_carries_every_class_the_skill_uses(self):
        for sel in (".eyebrow", ".thesis", ".sub", "h2 .who", ".tw", ".db", ".rule",
                    ".lim", ".pill", "ol.plan", "figure", "svg.w700", "footer"):
            self.assertIn(sel, self.css)

    def test_css_collapses_the_plan_under_780px(self):
        self.assertIn("@media (max-width:780px)", self.css)

    def test_diagrams_document_the_four_patterns_with_svg(self):
        for pattern in ("promotion ladder", "lifetime timeline", "pipeline flow",
                        "breadth vs depth"):
            self.assertIn(pattern, self.dgm.lower())
        self.assertGreaterEqual(self.dgm.count("<svg"), 4)

    def test_diagrams_state_the_theming_and_a11y_mechanics(self):
        self.assertIn("var(--promote)", self.dgm)
        self.assertIn("currentColor", self.dgm)
        self.assertIn('role="img"', self.dgm)
        self.assertIn("aria-labelledby", self.dgm)
        self.assertIn("<figure>", self.dgm)
        self.assertIn("min-width", self.dgm)

    def test_diagrams_forbid_hardcoded_hex_inside_svg(self):
        self.assertIn("hardcoded hex", self.dgm.lower())

    def test_diagrams_say_when_not_to_draw(self):
        self.assertIn("When NOT to draw", self.dgm)
        self.assertIn("two-column table", self.dgm.lower())


if __name__ == "__main__":
    unittest.main()
