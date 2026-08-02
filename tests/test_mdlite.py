"""Light-markdown renderer (1.17.0) for the board's expanded summary: a small,
pure-stdlib subset (headings · bullets · paragraphs · bold · code · links · hr).
SAFETY: the text is html-escaped FIRST, so raw HTML in a summary is rendered inert,
never as a live tag — the board stays self-contained / no-injection."""
import os
import sys
import unittest

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
sys.path.insert(0, TOOLS)
import mdlite


class MdLiteTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(mdlite.render(""), "")
        self.assertEqual(mdlite.render(None), "")

    def test_headings(self):
        self.assertIn("<h2>Heading two</h2>", mdlite.render("## Heading two"))
        self.assertIn("<h3>Heading three</h3>", mdlite.render("### Heading three"))
        self.assertIn("<h1>Top</h1>", mdlite.render("# Top"))

    def test_bullets_grouped_into_ul(self):
        out = mdlite.render("- first\n- second\n* third")
        self.assertIn("<ul>", out)
        self.assertEqual(out.count("<li>"), 3)
        self.assertIn("<li>first</li>", out)
        self.assertIn("<li>third</li>", out)
        self.assertEqual(out.count("<ul>"), 1)   # one contiguous list

    def test_paragraphs(self):
        out = mdlite.render("line one\nstill one\n\nsecond para")
        self.assertEqual(out.count("<p>"), 2)
        self.assertIn("line one still one", out)

    def test_bold(self):
        self.assertIn("<strong>bold</strong>", mdlite.render("a **bold** word"))

    def test_code_span(self):
        self.assertIn("<code>x = 1</code>", mdlite.render("run `x = 1` now"))

    def test_explicit_link(self):
        out = mdlite.render("see [the docs](https://example.com/p)")
        self.assertIn('<a href="https://example.com/p">the docs</a>', out)

    def test_bare_url(self):
        out = mdlite.render("visit https://example.com/x for more")
        self.assertIn('<a href="https://example.com/x">https://example.com/x</a>', out)

    def test_hr(self):
        self.assertIn("<hr>", mdlite.render("above\n\n---\n\nbelow"))

    def test_raw_html_is_escaped(self):
        # The headline safety guarantee: a literal <script> never becomes a tag.
        out = mdlite.render("danger <script>alert(1)</script> here")
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)

    def test_no_double_wrap_link(self):
        # A URL already inside an explicit link must not be re-linkified.
        out = mdlite.render("[x](https://a.b/c)")
        self.assertEqual(out.count("<a "), 1)

    def test_bold_inside_code_is_literal(self):
        out = mdlite.render("`**not bold**`")
        self.assertIn("<code>**not bold**</code>", out)
        self.assertNotIn("<strong>", out)

    def test_unknown_syntax_passes_through(self):
        out = mdlite.render("just plain text with no markup")
        self.assertIn("just plain text with no markup", out)


if __name__ == "__main__":
    unittest.main()
