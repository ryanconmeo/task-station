"""Per-task context briefing (1.15.0): deterministic `files` capture (touch-file),
the model-curated `state` (update --state), DERIVED PR-link extraction, and the
Briefing block in the task detail. No LLM, stdlib-only; extra keys ride along in
the serialized task blob (no schema migration)."""
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class BriefingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.store.reset_cache()

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        ts.store.reset_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _task(self, title="A task", color="green", effort="m"):
        t = ts.new_task(title, "summary for " + title, color=color, effort=effort)
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    # -- files capture: append + dedup + cap -----------------------------------

    def test_append_edited_file_appends_and_dedups(self):
        t = self._task()
        self.assertTrue(ts.append_edited_file(t, "/repo/a.py"))
        self.assertTrue(ts.append_edited_file(t, "/repo/b.py"))
        # re-touching a.py moves it to most-recent-last, no duplicate
        self.assertTrue(ts.append_edited_file(t, "/repo/a.py"))
        self.assertEqual(t["files"], ["/repo/b.py", "/repo/a.py"])
        # re-touching the already-most-recent file is a no-op
        self.assertFalse(ts.append_edited_file(t, "/repo/a.py"))
        # blank path → no-op
        self.assertFalse(ts.append_edited_file(t, "  "))

    def test_append_edited_file_caps(self):
        t = self._task()
        for i in range(ts.FILES_KEEP + 8):
            ts.append_edited_file(t, "/repo/f%d.py" % i)
        self.assertEqual(len(t["files"]), ts.FILES_KEEP)
        # the most recent survive, oldest dropped, order preserved
        self.assertEqual(t["files"][-1], "/repo/f%d.py" % (ts.FILES_KEEP + 7))
        self.assertNotIn("/repo/f0.py", t["files"])

    def test_touch_file_command_appends_to_attached_task_only(self):
        t = self._task()
        sess = "sess-A"
        ts.set_link(sess, t["id"])
        ts.cmd_touch_file(_Args(session=sess, file="/repo/x.py"))
        ts.cmd_touch_file(_Args(session=sess, file="/repo/y.py"))
        reloaded = ts.load_task(t["id"])
        self.assertEqual(reloaded["files"], ["/repo/x.py", "/repo/y.py"])

    def test_touch_file_noop_without_attached_task(self):
        # unlinked session: silent no-op, no crash, nothing persisted
        ts.cmd_touch_file(_Args(session="ghost", file="/repo/z.py"))
        self.assertEqual(ts.all_tasks(), [])

    def test_touch_file_noop_on_skipped_session(self):
        t = self._task()
        sess = "skipme"
        ts.set_link(sess, ts.SKIP_SENTINEL)
        ts.cmd_touch_file(_Args(session=sess, file="/repo/z.py"))
        self.assertNotIn("files", ts.load_task(t["id"]))

    # -- state: update --state persists + renders ------------------------------

    def test_update_state_persists(self):
        t = self._task()
        out = ts._update_one(str(t["seq"]), _Args(
            task=str(t["seq"]), title=None, summary=None, append_summary=None,
            state="next: wire the hook", color=None, effort=None))
        self.assertIn("state", out)
        self.assertEqual(ts.load_task(t["id"]).get("state"), "next: wire the hook")

    def test_update_state_blank_clears(self):
        t = self._task()
        t["state"] = "old standing"
        ts.save_task(t)
        ts._update_one(str(t["seq"]), _Args(
            task=str(t["seq"]), title=None, summary=None, append_summary=None,
            state="", color=None, effort=None))
        self.assertEqual(ts.load_task(t["id"]).get("state"), "")

    # -- PR-URL extraction: github + ADO, dedup, ignores non-PR ----------------

    def test_extract_prs_github_and_ado_dedup(self):
        gh = "https://github.com/octo/repo/pull/42"
        ado = "https://dev.azure.com/IWGDevops/Proj/_git/Repo/pullrequest/123"
        t = self._task()
        t["log"] = [
            {"ts": "t1", "note": "opened " + gh},
            {"ts": "t2", "note": "see also " + gh + " and " + ado},
        ]
        prs = ts.extract_prs(t)
        self.assertEqual(prs, [gh, ado])   # first-seen order, deduped

    def test_extract_prs_ignores_non_pr_urls(self):
        t = self._task()
        t["log"] = [
            {"ts": "t1", "note": "repo https://github.com/octo/repo and issue "
                                 "https://github.com/octo/repo/issues/9"},
        ]
        t["summary"] = "docs at https://example.com/page"
        self.assertEqual(ts.extract_prs(t), [])

    def test_extract_prs_scans_state_and_summary(self):
        t = self._task()
        t["state"] = "waiting on review: https://github.com/o/r/pull/7"
        self.assertEqual(ts.extract_prs(t), ["https://github.com/o/r/pull/7"])

    def test_extract_prs_trailing_punctuation(self):
        t = self._task()
        t["log"] = [{"ts": "t1", "note": "(merged https://github.com/o/r/pull/7)."}]
        self.assertEqual(ts.extract_prs(t), ["https://github.com/o/r/pull/7"])

    # -- detail renders the Briefing block (present + cleanly absent) -----------

    def test_detail_shows_briefing_when_present(self):
        t = self._task()
        t["state"] = "next: open the PR"
        t["files"] = ["/repo/lib/store.py", "/repo/lib/cli.py"]
        t["projects"] = ["task-station"]
        t["log"] = [{"ts": "t1", "note": "pushed https://github.com/o/r/pull/9"}]
        ts.save_task(t)
        detail = ts._format_detail(ts.load_task(t["id"]), "sess")
        self.assertIn("Briefing:", detail)
        self.assertIn("next: open the PR", detail)
        self.assertIn("task-station", detail)
        self.assertIn("https://github.com/o/r/pull/9", detail)
        self.assertIn("store.py", detail)
        # the briefing sits above the recent-activity log
        self.assertLess(detail.index("Briefing:"), detail.index("Recent activity"))

    def test_detail_omits_briefing_when_empty(self):
        t = self._task()
        detail = ts._format_detail(t, "sess")
        self.assertNotIn("Briefing:", detail)


if __name__ == "__main__":
    unittest.main()
