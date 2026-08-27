"""brain.ado_tree — network-free unit tests for the ADO work-item tree helper.

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 5a) from the brain source tree's
``tests/test_ado_tree.py`` @ 0.14.0. All 17 source cases port 1:1; the only
change is the fixture's project name (an org product name in the source).

ADDED: the org-resolution chain. The port DELETED the built-in organization URL
the source shipped as a last-resort default, so "nothing configured" is now a
reachable state with a defined answer — which is exactly the kind of thing that
regresses quietly if nobody pins it.

Covers relation parsing, HTML stripping, node shaping (compact vs --full),
markdown rendering, auth resolution and org resolution — all without touching the
network or a real `az` binary.
"""
import os
import unittest

from tests.brain.base import BrainTestCase  # noqa: F401 — also puts lib/ on sys.path

import brain.ado_tree as ado_tree


# --------------------------------------------------------------------------- fixtures
def _fake_item():
    """A work item with a parent (Hierarchy-Reverse), two children
    (Hierarchy-Forward), and one linked PR (ArtifactLink)."""
    return {
        "id": 100,
        "rev": 7,
        "fields": {
            "System.Id": 100,
            "System.WorkItemType": "User Story",
            "System.Title": "Ship the thing",
            "System.State": "Active",
            "System.AssignedTo": {"displayName": "Ada Lovelace"},
            "System.TeamProject": "Atlas",
            "System.Tags": "alpha; beta",
            "System.Description": "<div>Do <b>the</b> work</div><br>next line",
            "Microsoft.VSTS.Common.AcceptanceCriteria": "<p>It works</p>",
        },
        "relations": [
            {"rel": "System.LinkTypes.Hierarchy-Reverse",
             "url": "https://dev.azure.com/o/_apis/wit/workItems/42"},
            {"rel": "System.LinkTypes.Hierarchy-Forward",
             "url": "https://dev.azure.com/o/_apis/wit/workItems/101"},
            {"rel": "System.LinkTypes.Hierarchy-Forward",
             "url": "https://dev.azure.com/o/_apis/wit/workItems/102"},
            {"rel": "ArtifactLink",
             "url": "vstfs:///Git/PullRequestId/projGuid%2FrepoGuid%2F555",
             "attributes": {"name": "Pull Request"}},
        ],
        "_links": {"self": {"href": "https://example/100"}},
    }


ORG = "https://dev.azure.com/o"


# --------------------------------------------------------------------------- relations
class RelationTest(unittest.TestCase):
    def test_id_from_url(self):
        self.assertEqual(
            ado_tree._id_from_url("https://x/_apis/wit/workItems/42"), 42)
        self.assertIsNone(ado_tree._id_from_url("no id here"))

    def test_pr_from_artifact(self):
        url = "vstfs:///Git/PullRequestId/projGuid%2FrepoGuid%2F555"
        pr = ado_tree._pr_from_artifact(url, {"attributes": {"name": "My PR"}})
        self.assertEqual(pr, {"id": "555", "name": "My PR"})

    def test_pr_from_artifact_default_name(self):
        url = "vstfs:///Git/PullRequestId/a%2Fb%2F9"
        self.assertEqual(ado_tree._pr_from_artifact(url, {})["name"], "Pull Request")

    def test_parse_relations(self):
        rels = ado_tree.parse_relations(_fake_item())
        self.assertEqual(rels["parent"], 42)
        self.assertEqual(rels["children"], [101, 102])
        self.assertEqual(len(rels["prs"]), 1)
        self.assertEqual(rels["prs"][0]["id"], "555")

    def test_parse_relations_no_relations(self):
        rels = ado_tree.parse_relations({"fields": {}})
        self.assertEqual(rels, {"parent": None, "children": [], "prs": []})


# --------------------------------------------------------------------------- html
class StripHtmlTest(unittest.TestCase):
    def test_tags_removed_and_breaks_to_newlines(self):
        out = ado_tree._strip_html("<div>a</div><br>b<p>c</p>")
        self.assertNotIn("<", out)
        self.assertIn("a", out)
        self.assertIn("b", out)

    def test_entities_unescaped(self):
        self.assertEqual(ado_tree._strip_html("A &amp; B &lt;x&gt;"), "A & B <x>")

    def test_empty(self):
        self.assertEqual(ado_tree._strip_html(""), "")

    def test_truncation_at_limit(self):
        out = ado_tree._strip_html("x" * 100, limit=10)
        self.assertTrue(out.endswith("..."))
        self.assertLessEqual(len(out), 10 + len(" ..."))


# --------------------------------------------------------------------------- node_of
class NodeOfTest(unittest.TestCase):
    def test_compact_has_core_fields_no_full_bag(self):
        n = ado_tree.node_of(ORG, _fake_item(), want_desc=True)
        self.assertEqual(n["id"], 100)
        self.assertEqual(n["type"], "User Story")
        self.assertEqual(n["title"], "Ship the thing")
        self.assertEqual(n["state"], "Active")
        self.assertEqual(n["assignee"], "Ada Lovelace")
        self.assertEqual(n["project"], "Atlas")
        self.assertEqual(n["tags"], "alpha; beta")
        self.assertEqual(n["url"], f"{ORG}/_workitems/edit/100")
        # compact must NOT carry the full field bag / relations / rev
        self.assertNotIn("fields", n)
        self.assertNotIn("relations", n)
        self.assertNotIn("rev", n)

    def test_description_and_ac_present_only_when_want_desc(self):
        with_desc = ado_tree.node_of(ORG, _fake_item(), want_desc=True)
        self.assertIn("description", with_desc)
        self.assertIn("acceptance_criteria", with_desc)
        self.assertNotIn("<", with_desc["description"])  # html stripped

        without = ado_tree.node_of(ORG, _fake_item(), want_desc=False)
        self.assertNotIn("description", without)
        self.assertNotIn("acceptance_criteria", without)

    def test_full_includes_fields_rev_relations_links(self):
        n = ado_tree.node_of(ORG, _fake_item(), want_desc=False, full=True)
        self.assertIn("fields", n)
        self.assertEqual(n["rev"], 7)
        self.assertIn("relations", n)
        self.assertIn("_links", n)
        # the complete field bag is preserved verbatim
        self.assertEqual(n["fields"]["System.Title"], "Ship the thing")

    def test_person_string_passthrough(self):
        self.assertEqual(ado_tree._person("Plain Name"), "Plain Name")
        self.assertEqual(ado_tree._person(None), "")
        self.assertEqual(ado_tree._person({"displayName": "D"}), "D")


# --------------------------------------------------------------------------- render
class RenderTest(unittest.TestCase):
    def _tree(self):
        root = ado_tree.node_of(ORG, _fake_item(), want_desc=True)
        root["prs"] = [{"id": "555", "name": "My PR"}]
        child = ado_tree.node_of(ORG, {
            "id": 101,
            "fields": {"System.Id": 101, "System.WorkItemType": "Task",
                       "System.Title": "Sub task", "System.State": "Done"},
        }, want_desc=False)
        root["children"] = [child]
        parent = ado_tree.node_of(ORG, {
            "id": 42,
            "fields": {"System.Id": 42, "System.WorkItemType": "Feature",
                       "System.Title": "Big feature", "System.State": "Active"},
        }, want_desc=False)
        return {"root": root, "parent": parent}

    def test_render_md_shows_ids_titles_prs(self):
        out = ado_tree.render_md(self._tree(), ORG)
        self.assertIn("#100", out)
        self.assertIn("Ship the thing", out)
        self.assertIn("PR !555", out)
        self.assertIn("My PR", out)
        # parent line
        self.assertIn("parent Feature #42", out)
        self.assertIn("Big feature", out)
        # child + its Done state mark
        self.assertIn("#101", out)
        self.assertIn("Sub task", out)
        self.assertIn("✓", out)

    def test_render_md_no_parent(self):
        tree = self._tree()
        del tree["parent"]
        out = ado_tree.render_md(tree, ORG)
        self.assertNotIn("parent Feature", out)


# --------------------------------------------------------------------------- auth
class AuthTest(unittest.TestCase):
    def setUp(self):
        self._backup = {k: os.environ.get(k) for k in ("ADO_PAT", "AZURE_DEVOPS_EXT_PAT")}
        self.addCleanup(self._restore)
        for k in self._backup:
            os.environ.pop(k, None)

    def _restore(self):
        for k, v in self._backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_pat_env_yields_basic_header_without_az(self):
        os.environ["ADO_PAT"] = "supersecret"

        def _boom(*a, **k):  # az must never be invoked when a PAT is present
            raise AssertionError("az was called despite ADO_PAT being set")

        orig = ado_tree.subprocess.run
        ado_tree.subprocess.run = _boom
        try:
            auth = ado_tree.resolve_auth(allow_login=False)
        finally:
            ado_tree.subprocess.run = orig
        self.assertTrue(auth.header.startswith("Basic "))
        self.assertIn("PAT", auth.kind)

    def test_az_token_none_when_az_missing(self):
        def _missing(*a, **k):
            raise FileNotFoundError("az not found")

        orig = ado_tree.subprocess.run
        ado_tree.subprocess.run = _missing
        try:
            self.assertIsNone(ado_tree._az_token())
        finally:
            ado_tree.subprocess.run = orig


# --------------------------------------------------------------------------- org
class DefaultOrgTest(BrainTestCase):
    """ADDED — the resolution chain, and the deleted default.

    The source ended this chain with one organization's URL baked into the code:
    an unconfigured install pointed at a stranger's tenant. The port stops at
    "nothing", and every caller has to say so out loud."""

    def setUp(self):
        super().setUp()
        # ADO_ORG is not a brain config key, so base.setUp does not clear it (D24:
        # a non-config env var is cleared by the test that cares).
        self._ado_org = os.environ.pop("ADO_ORG", None)
        if self._ado_org is not None:
            self.addCleanup(os.environ.__setitem__, "ADO_ORG", self._ado_org)

    def test_config_wins(self):
        self.write_primary_config({"ado_org": "https://example.invalid/from-config"})
        os.environ["ADO_ORG"] = "https://example.invalid/from-env"
        self.addCleanup(os.environ.pop, "ADO_ORG", None)
        self.assertEqual(ado_tree.default_org(), "https://example.invalid/from-config")

    def test_env_is_the_fallback(self):
        os.environ["ADO_ORG"] = "https://example.invalid/from-env"
        self.addCleanup(os.environ.pop, "ADO_ORG", None)
        self.assertEqual(ado_tree.default_org(), "https://example.invalid/from-env")

    def test_nothing_configured_is_none_not_someone_elses_org(self):
        self.assertIsNone(ado_tree.default_org())

    def test_the_hint_tells_the_user_all_three_ways_in(self):
        for token in ("--org", "ado_org", "ADO_ORG"):
            self.assertIn(token, ado_tree.NO_ORG_HINT)


if __name__ == "__main__":
    unittest.main()


# ------------------------------------------------------- the 604-character lie
class TruncationDeclaresItself(unittest.TestCase):
    """MEASURED 2026-08-26 on Volt story 3614.

    `--json` returned a 604-character `acceptance_criteria` for 3614 — and for
    3607, 2966, 3202 and 3510, all exactly 604, because the old 600-char clip plus
    " ..." lands on the same length every time. 3614's field is 9,237 characters
    and 33 numbered criteria; the clip stopped inside criterion 4. A session read
    it, believed it had the story, and built a file-level checksum ledger that
    criteria 2, 23, 24 and 28 already specified better.

    The defect was never the truncation. It was that a truncated value was
    INDISTINGUISHABLE from a complete one. So the rule pinned here: when the text
    is clipped, the plain field name is ABSENT — a reader keying on
    `acceptance_criteria` gets the truth or nothing, never a confident fraction.
    """

    LONG = "<ol>" + "".join("<li>criterion %d body text here</li>" % i
                            for i in range(1, 34)) + "</ol>"

    def _item(self, ac):
        it = _fake_item()
        it["fields"]["Microsoft.VSTS.Common.AcceptanceCriteria"] = ac
        return it

    def test_clipped_field_never_uses_the_plain_name(self):
        n = ado_tree.node_of(ORG, self._item(self.LONG), want_desc=True)
        self.assertNotIn("acceptance_criteria", n)
        self.assertIn("acceptance_criteria_preview", n)
        self.assertTrue(n["acceptance_criteria_truncated"])

    def test_clip_carries_the_size_it_is_hiding(self):
        n = ado_tree.node_of(ORG, self._item(self.LONG), want_desc=True)
        full = ado_tree._plain(self.LONG)
        self.assertEqual(n["acceptance_criteria_chars"], len(full))
        self.assertEqual(n["acceptance_criteria_criteria"], 33)
        self.assertIn("acceptance_criteria", n["truncated"])
        self.assertIn("--no-clip", n["truncated_hint"])

    def test_no_clip_returns_the_whole_field_under_the_plain_name(self):
        n = ado_tree.node_of(ORG, self._item(self.LONG), want_desc=True, clip=None)
        self.assertNotIn("acceptance_criteria_preview", n)
        self.assertNotIn("truncated", n)
        self.assertEqual(n["acceptance_criteria_criteria"], 33)
        self.assertIn("criterion 33", n["acceptance_criteria"])

    def test_full_does_not_clip_either(self):
        """`--full` means "nothing dropped". Before this it still carried the
        604-char clip under the plain name while the truth sat in `fields` as raw
        HTML — the worst of both, and the shape the 3614 session actually hit."""
        n = ado_tree.node_of(ORG, self._item(self.LONG), want_desc=True, full=True)
        self.assertIn("criterion 33", n["acceptance_criteria"])
        self.assertNotIn("acceptance_criteria_truncated", n)

    def test_short_field_is_untouched(self):
        n = ado_tree.node_of(ORG, self._item("<p>It works</p>"), want_desc=True)
        self.assertEqual(n["acceptance_criteria"], "It works")
        self.assertNotIn("acceptance_criteria_truncated", n)

    def test_five_distinct_stories_never_share_a_length(self):
        """The tell that started the investigation: five different work items, five
        identical 604-char values. Distinct sources must now differ in what they
        report, because the reported number is the SOURCE's size, not the clip's."""
        sizes = set()
        for n_crit in (12, 20, 24, 33):
            ac = "<ol>" + "".join("<li>criterion %d body</li>" % i
                                  for i in range(1, n_crit + 1)) + "</ol>"
            node = ado_tree.node_of(ORG, self._item(ac), want_desc=True)
            sizes.add((node.get("acceptance_criteria_chars"),
                       node.get("acceptance_criteria_criteria")))
        self.assertEqual(len(sizes), 4)


class OrderedListNumbering(unittest.TestCase):
    """ADO's editor writes criteria as `<ol><li>`, so the numbering lives in the
    MARKUP. Stripping tags turned "criterion 23" into an anonymous line — which is
    why `count_criteria` saw zero on three of the five Volt stories."""

    def test_ol_items_are_numbered(self):
        self.assertEqual(ado_tree._plain("<ol><li>a</li><li>b</li></ol>"),
                         "1. a\n\n2. b")

    def test_ol_start_is_honoured(self):
        self.assertTrue(ado_tree._plain('<ol start="5"><li>x</li></ol>').startswith("5."))

    def test_ul_items_are_bulleted_not_numbered(self):
        self.assertEqual(ado_tree._plain("<ul><li>x</li></ul>"), "- x")

    def test_count_criteria_is_a_floor_not_a_guess(self):
        self.assertEqual(ado_tree.count_criteria("1. a\n2. b\n3. c"), 3)
        self.assertEqual(ado_tree.count_criteria("prose with no numbering"), 0)

    def test_malformed_markup_still_yields_the_text(self):
        self.assertIn("kept", ado_tree._plain("<div><b>kept</div></b><<>"))


class RenderDeclaresWhatItIsNotShowing(unittest.TestCase):
    def test_md_says_how_many_criteria_it_is_hiding(self):
        it = _fake_item()
        it["fields"]["Microsoft.VSTS.Common.AcceptanceCriteria"] = (
            TruncationDeclaresItself.LONG)
        tree = {"root": ado_tree.node_of(ORG, it, want_desc=True)}
        out = ado_tree.render_md(tree, ORG)
        self.assertIn("33 criteria", out)
        self.assertIn("--no-clip", out)
