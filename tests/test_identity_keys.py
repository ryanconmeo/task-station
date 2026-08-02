"""F9 identity-keyed fold-in — the shared key-extraction module.

extract_identity_keys(text) mines strong identity keys (PR #, work-item #) from
free text or a url, typed so a PR number never joins a story number. These tests
pin the recognized surface forms, the typing, multi-key, keyless, render, and the
task-payload extractor."""
import importlib.util
import os
import sys
import unittest

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


class ExtractPRForms(unittest.TestCase):
    def test_pr_word_forms(self):
        for s in ["PR 1115", "PR-1115", "PR#1115", "pr 1115", "pr/1115",
                  "review PR 1115 please", "/my-review-pr-auto PR-1115"]:
            self.assertEqual(ts.extract_identity_keys(s), {"pr:1115"}, s)

    def test_pull_and_pullrequest(self):
        self.assertEqual(ts.extract_identity_keys("pull/1115"), {"pr:1115"})
        self.assertEqual(ts.extract_identity_keys("pullrequest/1115"), {"pr:1115"})

    def test_pull_urls(self):
        self.assertEqual(
            ts.extract_identity_keys("https://github.com/org/repo/pull/1115"), {"pr:1115"})
        self.assertEqual(
            ts.extract_identity_keys(
                "https://dev.azure.com/org/proj/_git/repo/pullrequest/1115"), {"pr:1115"})

    def test_bare_hash_is_pr(self):
        self.assertEqual(ts.extract_identity_keys("#1115"), {"pr:1115"})
        self.assertEqual(ts.extract_identity_keys("look at #1115 today"), {"pr:1115"})


class ExtractWorkItemForms(unittest.TestCase):
    def test_ab_hash(self):
        self.assertEqual(ts.extract_identity_keys("AB#3166"), {"wi:3166"})
        # AB# is NOT also read as a bare-# PR.
        self.assertNotIn("pr:3166", ts.extract_identity_keys("AB#3166"))

    def test_project_prefixed(self):
        self.assertEqual(ts.extract_identity_keys("Projectname-3166"), {"wi:3166"})
        self.assertEqual(ts.extract_identity_keys("OtherProj-3166 balance sheet"), {"wi:3166"})

    def test_story_workitem_words(self):
        for s in ["story 3166", "story #3166", "workitem 3166", "work item 3166"]:
            self.assertEqual(ts.extract_identity_keys(s), {"wi:3166"}, s)

    def test_workitems_url(self):
        self.assertEqual(
            ts.extract_identity_keys(
                "https://dev.azure.com/org/proj/_workitems/edit/3166"), {"wi:3166"})

    def test_lowercase_prefix_not_a_key(self):
        # "utf-8" / "sha-256" must not read as work items (lowercase prefix).
        self.assertEqual(ts.extract_identity_keys("utf-8 encoding"), set())
        self.assertEqual(ts.extract_identity_keys("sha-256 digest"), set())


class ExtractTypingAndEdges(unittest.TestCase):
    def test_pr_and_story_do_not_cross_join(self):
        self.assertEqual(ts.extract_identity_keys("PR 1115"), {"pr:1115"})
        self.assertEqual(ts.extract_identity_keys("story 1115"), {"wi:1115"})
        self.assertFalse(
            ts.extract_identity_keys("PR 1115") & ts.extract_identity_keys("story 1115"))

    def test_pr_hyphen_not_a_work_item(self):
        # "PR-1115" is a PR, never wi:1115.
        self.assertEqual(ts.extract_identity_keys("PR-1115"), {"pr:1115"})

    def test_multi_key(self):
        self.assertEqual(
            ts.extract_identity_keys("Revise PR 1111 for Projectname-3166"),
            {"pr:1111", "wi:3166"})

    def test_keyless(self):
        for s in ["", None, "add dark mode", "fix the auth bug", "run 3.5 fold-in"]:
            self.assertEqual(ts.extract_identity_keys(s), set(), repr(s))


class RenderKeys(unittest.TestCase):
    def test_render_prs_first_then_stories_sorted(self):
        self.assertEqual(
            ts.render_identity_keys({"wi:3166", "pr:1115", "pr:200"}),
            "PR 200, PR 1115, story 3166")

    def test_render_single(self):
        self.assertEqual(ts.render_identity_keys({"pr:1111"}), "PR 1111")

    def test_render_empty(self):
        self.assertEqual(ts.render_identity_keys(set()), "")


class TaskPayloadKeys(unittest.TestCase):
    def test_keys_from_title(self):
        self.assertEqual(
            ts.task_identity_keys({"title": "Revise PR 1111"}), {"pr:1111"})

    def test_keys_from_summary_and_stories(self):
        t = {"title": "Balance sheet",
             "summary": "tracked under story 3166",
             "stories": ["https://dev.azure.com/org/proj/_workitems/edit/9000"]}
        self.assertEqual(ts.task_identity_keys(t), {"wi:3166", "wi:9000"})

    def test_keys_from_stored_pr_url(self):
        t = {"title": "x",
             "prs": [{"url": "https://github.com/o/r/pull/1115", "desc": ""}]}
        self.assertEqual(ts.task_identity_keys(t), {"pr:1115"})

    def test_keyless_task(self):
        self.assertEqual(ts.task_identity_keys({"title": "add dark mode"}), set())
        self.assertEqual(ts.task_identity_keys(None), set())


if __name__ == "__main__":
    unittest.main()
