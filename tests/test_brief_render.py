"""Task 4: the deterministic brief renderer (lib/brief.py).

Pure stdlib; imports NOTHING from task-station. The model supplies a structured
brief-spec (JSON) — NEVER raw HTML — and the core templates the frozen house style.
Covers: HTML escaping, longest-first term wrapping, title/banner/glossary sections,
the ADO tree (verb pills + hrefs), and optional sections being absent when omitted."""
import importlib.util
import os
import sys
import unittest

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)
import brief  # noqa: E402


class FrozenTemplateContractTest(unittest.TestCase):
    """The template's <style> is the frozen house-style contract: source lines
    7-47 (the :root…a{} CSS) copied VERBATIM, plus appended .ado tree styles."""

    def setUp(self):
        fix = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
        with open(os.path.join(fix, "collation-decision-brief-source.html"),
                  encoding="utf-8") as f:
            self.source = f.read().split("\n")
        with open(os.path.join(LIB, "brief_template.html"), encoding="utf-8") as f:
            self.template = f.read()

    def test_source_css_copied_verbatim(self):
        # source lines 8-46 (1-indexed) are the CSS rules between <style>/</style>
        css = "\n".join(self.source[7:46])
        self.assertIn(css, self.template)

    def test_appended_ado_styles_present(self):
        for sel in (".ado li", ".verb.add", ".verb.change", ".verb.remove", ".does"):
            self.assertIn(sel, self.template)

    def test_single_style_block_and_placeholders(self):
        self.assertEqual(self.template.count("<style>"), 1)
        self.assertEqual(self.template.count("</style>"), 1)
        self.assertIn("{TITLE}", self.template)
        self.assertIn('<div class="wrap">{BODY}</div>', self.template)


class HighlightTest(unittest.TestCase):
    def test_escapes_html(self):
        self.assertEqual(brief.highlight_terms("a < b & c > d", []), "a &lt; b &amp; c &gt; d")

    def test_escapes_quotes(self):
        self.assertIn("&quot;", brief.highlight_terms('say "hi"', []))

    def test_wraps_a_term(self):
        out = brief.highlight_terms("use the Store now", ["Store"])
        self.assertEqual(out, 'use the <span class="term">Store</span> now')

    def test_longest_first_wins(self):
        out = brief.highlight_terms("the Binary Store here", ["Store", "Binary Store"])
        self.assertEqual(out, 'the <span class="term">Binary Store</span> here')

    def test_term_with_special_chars(self):
        out = brief.highlight_terms("we build a Binary-Default (BIN2) Store today",
                                    ["Binary-Default (BIN2) Store"])
        self.assertIn('<span class="term">Binary-Default (BIN2) Store</span>', out)

    def test_no_names_is_just_escaped(self):
        self.assertEqual(brief.highlight_terms("plain & text", None), "plain &amp; text")

    def test_does_not_wrap_absent_term(self):
        self.assertEqual(brief.highlight_terms("nothing here", ["Absent"]), "nothing here")


class RenderBriefTest(unittest.TestCase):
    def _min_spec(self, **kw):
        spec = {
            "title": "LEGACY Key Collation — Strategy",
            "decision": {"label": "Decision", "body": "Rebuild + Centralize the Store."},
            "glossary": "auto",
            "provenance": "Shipped fix: PR 931.",
        }
        spec.update(kw)
        return spec

    _GLOSS = [
        {"name": "Store", "layer": "db", "state": "target", "def": "the data store"},
        {"name": "Collation Gate", "layer": "CI", "state": "shipped", "def": "a build check"},
    ]

    def test_title_in_title_and_h1(self):
        html = brief.render_brief(self._min_spec(), self._GLOSS)
        self.assertIn("<title>LEGACY Key Collation — Strategy</title>", html)
        self.assertIn("<h1>LEGACY Key Collation — Strategy</h1>", html)

    def test_title_is_escaped(self):
        html = brief.render_brief(self._min_spec(title="A < B & C"), self._GLOSS)
        self.assertIn("<title>A &lt; B &amp; C</title>", html)

    def test_subtitle_optional(self):
        html = brief.render_brief(self._min_spec(subtitle="Projectname · 2026"), self._GLOSS)
        self.assertIn('<p class="sub">Projectname · 2026</p>', html)
        html2 = brief.render_brief(self._min_spec(), self._GLOSS)
        self.assertNotIn('class="sub"', html2)

    def test_banner_rendered_with_highlighted_term(self):
        html = brief.render_brief(self._min_spec(), self._GLOSS)
        self.assertIn('<div class="banner">', html)
        self.assertIn('<div class="k">Decision</div>', html)
        self.assertIn('<span class="term">Store</span>', html)

    def test_glossary_auto_pulls_passed_glossary(self):
        html = brief.render_brief(self._min_spec(), self._GLOSS)
        self.assertIn("<h2>The vocabulary</h2>", html)
        self.assertIn('<span class="name">Store</span>', html)
        self.assertIn('<span class="where">db · target</span>', html)
        self.assertIn('<div class="d">the data store</div>', html)
        self.assertIn('<span class="where">CI · shipped</span>', html)

    def test_glossary_inline_list_used_when_not_auto(self):
        inline = [{"name": "OnlyTerm", "layer": "app", "state": "today", "def": "d"}]
        html = brief.render_brief(self._min_spec(glossary=inline), self._GLOSS)
        self.assertIn('<span class="name">OnlyTerm</span>', html)
        self.assertNotIn(">Store<", html)   # the passed auto-glossary is ignored

    def test_provenance_footer(self):
        html = brief.render_brief(self._min_spec(), self._GLOSS)
        self.assertIn('<div class="foot">', html)
        self.assertIn("Shipped fix: PR 931.", html)

    def test_transition_section(self):
        spec = self._min_spec(transition={
            "today": {"label": "Today", "name": "Patch-and-Guard",
                      "lines": ["Store — Mixed-Collation", "fragile"]},
            "goal": {"label": "Goal", "name": "Rebuild + Centralize",
                     "lines": ["Store — Binary-Default"]},
        })
        html = brief.render_brief(spec, self._GLOSS)
        self.assertIn("<h2>Where we are → where we're going</h2>", html)
        self.assertIn("Patch-and-Guard", html)
        self.assertIn("Rebuild + Centralize", html)
        # 'Store' is a glossary term → highlighted mechanically inside the line
        self.assertIn('<span class="term">Store</span> — Mixed-Collation', html)

    def test_one_rule_section(self):
        spec = self._min_spec(one_rule="Build the layer before flipping the Store.")
        html = brief.render_brief(spec, self._GLOSS)
        self.assertIn("<h2>The one rule</h2>", html)
        self.assertIn('<div class="rule">', html)
        self.assertIn('<span class="term">Store</span>', html)

    def test_plan_section_with_done_and_numbered(self):
        spec = self._min_spec(plan=[
            {"state": "done", "title": "Bridge", "body": "keep it live"},
            {"state": "1", "title": "Build the layer", "body": "the big step"},
        ])
        html = brief.render_brief(spec, self._GLOSS)
        self.assertIn("<h2>The plan</h2>", html)
        self.assertIn('<div class="phase done">', html)
        self.assertIn('<div class="num">✓</div>', html)
        self.assertIn('<div class="num">1</div>', html)
        self.assertIn("Build the layer", html)

    def test_ado_tree_verbs_and_hrefs(self):
        spec = self._min_spec(ado_tree=[
            {"type": "Feature", "id": 3049, "url": "https://dev.azure.com/companyname/Projectname/_workitems/edit/3049",
             "title": "Collation", "verb": "change", "does": "reshape the Store", "state": "active",
             "children": [
                 {"type": "Story", "id": 3133, "url": "https://example/story/3133",
                  "title": "Gate", "verb": "add", "does": "block bypass", "children": [
                      {"type": "PR", "id": 931, "url": "https://example/pr/931",
                       "title": "Shipped fix", "verb": "remove", "does": "drop inline collation"}]}]},
        ])
        html = brief.render_brief(spec, self._GLOSS)
        self.assertIn("<h2>ADO structure</h2>", html)
        self.assertIn('<ul class="ado">', html)
        self.assertIn('<span class="verb change">change</span>', html)
        self.assertIn('<span class="verb add">add</span>', html)
        self.assertIn('<span class="verb remove">remove</span>', html)
        self.assertIn('<a href="https://dev.azure.com/companyname/Projectname/_workitems/edit/3049">Collation</a>', html)
        self.assertIn('<a href="https://example/pr/931">Shipped fix</a>', html)
        # the "does" one-liner is highlighted in glossary terms
        self.assertIn('reshape the <span class="term">Store</span>', html)

    def test_diagram_svg_passthrough(self):
        spec = self._min_spec(diagrams=[
            {"type": "svg", "title": "Two choices → four combinations",
             "svg": '<svg viewBox="0 0 10 10"><rect/></svg>', "caption": "safe path"}])
        html = brief.render_brief(spec, self._GLOSS)
        self.assertIn("<h2>Two choices → four combinations</h2>", html)
        self.assertIn('<svg viewBox="0 0 10 10"><rect/></svg>', html)   # raw, not escaped
        self.assertIn("<figcaption>safe path</figcaption>", html)

    def test_diagram_matrix_parametrized(self):
        spec = self._min_spec(diagrams=[{
            "type": "matrix", "title": "Two knobs", "x_label": "SEARCH", "y_label": "STORAGE",
            "x": ["Inline", "Centralized"], "y": ["Mixed", "Binary"],
            "quadrants": [
                {"pos": "tl", "title": "Patch-and-Guard", "note": "today", "kind": "today"},
                {"pos": "br", "title": "Rebuild + Centralize", "note": "chosen", "kind": "goal"},
            ]}])
        html = brief.render_brief(spec, self._GLOSS)
        self.assertIn("<h2>Two knobs</h2>", html)
        self.assertIn("<svg", html)
        self.assertIn("Patch-and-Guard", html)
        self.assertIn("Rebuild + Centralize", html)
        self.assertIn("SEARCH", html)
        self.assertIn("STORAGE", html)

    def test_diagram_architecture_parametrized(self):
        spec = self._min_spec(diagrams=[{
            "type": "architecture", "title": "How it's built",
            "today": {"label": "TODAY", "box": "Mixed-Collation Store"},
            "goal": {"label": "GOAL", "box": "Binary-Default Store"}}])
        html = brief.render_brief(spec, self._GLOSS)
        self.assertIn("<h2>How it's built</h2>", html)
        self.assertIn("<svg", html)
        self.assertIn("Mixed-Collation Store", html)
        self.assertIn("Binary-Default Store", html)

    def test_unknown_diagram_type_omitted(self):
        spec = self._min_spec(diagrams=[{"type": "bogus", "title": "X"}])
        html = brief.render_brief(spec, self._GLOSS)
        self.assertNotIn("<figure>", html)

    def test_optional_sections_absent_when_omitted(self):
        html = brief.render_brief(self._min_spec(), self._GLOSS)
        self.assertNotIn("Where we are", html)              # no transition
        self.assertNotIn("<h2>The one rule</h2>", html)      # no one_rule
        self.assertNotIn("<h2>The plan</h2>", html)          # no plan
        self.assertNotIn("<h2>ADO structure</h2>", html)     # no ado_tree ('ADO' is in the CSS comment)
        self.assertNotIn("<figure>", html)                   # no diagrams
        # but the required sections are present
        self.assertIn('class="banner"', html)
        self.assertIn("The vocabulary", html)
        self.assertIn('class="foot"', html)

    def test_no_raw_html_from_model_body(self):
        # a model body containing markup is escaped, never injected verbatim
        spec = self._min_spec(decision={"label": "Decision", "body": "<script>x</script>"})
        html = brief.render_brief(spec, self._GLOSS)
        self.assertNotIn("<script>x</script>", html)
        self.assertIn("&lt;script&gt;", html)


class ProvenanceSectionTest(unittest.TestCase):
    """The #463 session-roster table + worker-ledger tail — data-gated: rendered
    only when the spec carries `sessions` / `ledger`."""

    _SESSIONS = [
        {"ordinal": "9-0", "kind": "hub", "name": "a1b2c3d4", "model": "opus",
         "status": "", "spawned": "2h ago"},
        {"ordinal": "", "kind": "worker", "name": "task-station-9-projectname",
         "model": "sonnet", "status": "ok", "spawned": "1h ago"},
    ]
    _LEDGER = [
        {"when": "1h ago", "actor": "9-0", "action": "spawn",
         "worker": "task-station-9-projectname", "detail": "projectname:fix-99"},
    ]

    def test_roster_renders_rows(self):
        html = brief._roster(self._SESSIONS)
        self.assertIn("<h2>Sessions</h2>", html)
        self.assertIn('<table class="roster">', html)
        self.assertIn("9-0", html)
        self.assertIn("task-station-9-projectname", html)
        self.assertIn("sonnet", html)

    def test_ledger_renders_tail(self):
        html = brief._ledger(self._LEDGER)
        self.assertIn("<h2>Recent worker activity</h2>", html)
        self.assertIn("spawn", html)
        self.assertIn("projectname:fix-99", html)

    def test_absent_when_no_data(self):
        # data-gated: no sessions/ledger → empty string, so a spec without them
        # renders exactly as before.
        self.assertEqual(brief._roster(None), "")
        self.assertEqual(brief._roster([]), "")
        self.assertEqual(brief._ledger(None), "")
        self.assertEqual(brief._ledger([]), "")

    def test_render_brief_includes_provenance_when_present(self):
        spec = {"title": "T", "decision": {"label": "D", "body": "b"},
                "glossary": [], "provenance": "p",
                "sessions": self._SESSIONS, "ledger": self._LEDGER}
        html = brief.render_brief(spec)
        self.assertIn('<table class="roster">', html)
        self.assertIn('<ul class="ledger">', html)

    def test_render_brief_omits_provenance_when_absent(self):
        spec = {"title": "T", "decision": {"label": "D", "body": "b"},
                "glossary": [], "provenance": "p"}
        html = brief.render_brief(spec)
        self.assertNotIn('class="roster"', html)
        self.assertNotIn('class="ledger"', html)

    def test_roster_escapes_html(self):
        html = brief._roster([{"ordinal": "", "kind": "worker",
                               "name": "<script>x</script>", "model": "", "status": "",
                               "spawned": ""}])
        self.assertNotIn("<script>x</script>", html)
        self.assertIn("&lt;script&gt;", html)


if __name__ == "__main__":
    unittest.main()
