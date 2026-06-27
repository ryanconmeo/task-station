"""Structured, STORED task digest (1.18.0): goal · state · steps (checklist with
stable indices) · decisions (append-only) · prs (stored + auto-merged). The digest
is written by the model via CLI flags and rides the task JSON blob (no schema
migration); render is digest-first on BOTH boards, with an N/M progress rollup.
No LLM, stdlib-only."""
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


def _update_args(seq, **kw):
    """An update _Args with every flag defaulted to None/absent, so a test only
    sets the one(s) it exercises (mirrors _update_one's getattr-with-default reads)."""
    base = dict(task=str(seq), title=None, summary=None, append_summary=None,
                state=None, goal=None, step_add=None, step_done=None,
                step_undone=None, decision=None, pr=None, pr_desc=None,
                color=None, effort=None)
    base.update(kw)
    return _Args(**base)


class DigestTest(unittest.TestCase):
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

    def _run_board(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_board(_Args(open=False))
        path = buf.getvalue().strip().splitlines()[-1]
        with open(path, encoding="utf-8") as f:
            return f.read()

    # -- helpers: step ops + progress math -------------------------------------

    def test_append_step_appends_and_skips_blank(self):
        t = self._task()
        self.assertTrue(ts.append_step(t, "first"))
        self.assertTrue(ts.append_step(t, "  second  "))
        self.assertFalse(ts.append_step(t, "   "))
        self.assertEqual([s["text"] for s in t["steps"]], ["first", "second"])
        self.assertEqual([s["done"] for s in t["steps"]], [False, False])

    def test_set_step_done_toggles_by_1_based_index(self):
        t = self._task()
        ts.append_step(t, "a"); ts.append_step(t, "b")
        self.assertTrue(ts.set_step_done(t, 2, True))
        self.assertEqual(t["steps"][1]["done"], True)
        self.assertTrue(ts.set_step_done(t, 2, False))
        self.assertEqual(t["steps"][1]["done"], False)

    def test_set_step_done_out_of_range_is_safe_noop(self):
        t = self._task()
        ts.append_step(t, "only")
        for bad in (0, -1, 2, 99, "x", None):
            self.assertFalse(ts.set_step_done(t, bad, True))
        self.assertEqual(t["steps"][0]["done"], False)   # untouched, no crash

    def test_step_progress_math(self):
        t = self._task()
        self.assertEqual(ts.step_progress(t), (0, 0))    # no steps
        ts.append_step(t, "a"); ts.append_step(t, "b"); ts.append_step(t, "c")
        self.assertEqual(ts.step_progress(t), (0, 3))
        ts.set_step_done(t, 1, True); ts.set_step_done(t, 3, True)
        self.assertEqual(ts.step_progress(t), (2, 3))    # partial
        ts.set_step_done(t, 2, True)
        self.assertEqual(ts.step_progress(t), (3, 3))    # all done

    # -- persistence: goal / decisions / pr via update -------------------------

    def test_goal_persists_and_clears(self):
        t = self._task()
        out = ts._update_one(str(t["seq"]), _update_args(t["seq"], goal="ship 1.18"))
        self.assertIn("goal", out)
        self.assertEqual(ts.load_task(t["id"]).get("goal"), "ship 1.18")
        ts._update_one(str(t["seq"]), _update_args(t["seq"], goal=""))
        self.assertEqual(ts.load_task(t["id"]).get("goal"), "")

    def test_decisions_append_only(self):
        t = self._task()
        ts._update_one(str(t["seq"]), _update_args(t["seq"], decision=["chose sqlite"]))
        ts._update_one(str(t["seq"]), _update_args(t["seq"], decision=["no migration"]))
        self.assertEqual(ts.load_task(t["id"]).get("decisions"),
                         ["chose sqlite", "no migration"])

    def test_create_seeds_goal_and_steps(self):
        # cmd_create wires --goal + repeatable --step into the stored task.
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_create(_Args(session=None, title="Seeded", summary="s", color="green",
                                 effort="m", goal="done means green CI", step=["plan", "code"],
                                 force=True, no_attach=True, attach=False, active=False))
        t = [x for x in ts.all_tasks() if x["title"] == "Seeded"][0]
        self.assertEqual(t.get("goal"), "done means green CI")
        self.assertEqual([s["text"] for s in t.get("steps", [])], ["plan", "code"])

    def test_update_steps_add_done_undone_and_out_of_range(self):
        t = self._task()
        ts._update_one(str(t["seq"]), _update_args(t["seq"], step_add=["one", "two"]))
        self.assertEqual([s["text"] for s in ts.load_task(t["id"])["steps"]], ["one", "two"])
        ts._update_one(str(t["seq"]), _update_args(t["seq"], step_done=[1]))
        self.assertEqual(ts.load_task(t["id"])["steps"][0]["done"], True)
        ts._update_one(str(t["seq"]), _update_args(t["seq"], step_undone=[1]))
        self.assertEqual(ts.load_task(t["id"])["steps"][0]["done"], False)
        # out-of-range index is warned + ignored, never raised
        out = ts._update_one(str(t["seq"]), _update_args(t["seq"], step_done=[99]))
        self.assertIn("ignoring --step-done 99", out)
        self.assertEqual(ts.step_progress(ts.load_task(t["id"])), (0, 2))

    def test_update_engagement_clears_provisional(self):
        t = self._task()
        t["provisional"] = True
        ts.save_task(t)
        ts._update_one(str(t["seq"]), _update_args(t["seq"], step_add=["x"]))
        self.assertFalse(ts.load_task(t["id"]).get("provisional"))

    # -- prs: stored + auto-merge with derived, deduped ------------------------

    def test_pr_stored_via_update(self):
        t = self._task()
        url = "https://github.com/o/r/pull/5"
        ts._update_one(str(t["seq"]), _update_args(t["seq"], pr=[url]))
        # stored as a {url,desc} entry (1.19), desc empty when none given
        self.assertEqual(ts.load_task(t["id"]).get("prs"), [{"url": url, "desc": ""}])
        # storing the same url again is a deduped no-op
        ts._update_one(str(t["seq"]), _update_args(t["seq"], pr=[url]))
        self.assertEqual(ts.load_task(t["id"]).get("prs"), [{"url": url, "desc": ""}])

    def test_pr_desc_upsert_by_url(self):
        # --pr --pr-desc stores the desc; --pr-desc alone updates the most-recent pr.
        t = self._task()
        url = "https://github.com/o/r/pull/5"
        ts._update_one(str(t["seq"]), _update_args(t["seq"], pr=[url], pr_desc="the fix"))
        self.assertEqual(ts.load_task(t["id"]).get("prs"), [{"url": url, "desc": "the fix"}])
        # re-running with a new desc on the same url UPSERTS (keys on url, not append)
        ts._update_one(str(t["seq"]), _update_args(t["seq"], pr=[url], pr_desc="revised"))
        self.assertEqual(ts.load_task(t["id"]).get("prs"), [{"url": url, "desc": "revised"}])
        # --pr-desc with no --pr applies to the most-recent stored pr
        ts._update_one(str(t["seq"]), _update_args(t["seq"], pr_desc="latest"))
        self.assertEqual(ts.load_task(t["id"]).get("prs"), [{"url": url, "desc": "latest"}])

    def test_pr_back_compat_string_loads(self):
        # A task whose stored prs are legacy bare strings normalizes to {url,desc}.
        t = self._task()
        legacy = "https://github.com/o/r/pull/2"
        t["prs"] = [legacy]
        self.assertEqual(ts.merged_prs(t), [{"url": legacy, "desc": ""}])
        # adding a desc upgrades the legacy entry in place (upsert by url)
        ts.add_pr(t, legacy, "now described")
        self.assertEqual(t["prs"], [{"url": legacy, "desc": "now described"}])

    def test_merged_prs_dedups_stored_and_derived(self):
        stored = "https://github.com/o/r/pull/5"
        derived = "https://github.com/o/r/pull/9"
        t = self._task()
        t["prs"] = [{"url": stored, "desc": "main fix"}]
        # the derived one appears in the log; `stored` also appears in the log but
        # must NOT duplicate the stored entry.
        t["log"] = [{"ts": "t1", "note": "see %s and %s" % (stored, derived)}]
        self.assertEqual(ts.merged_prs(t),
                         [{"url": stored, "desc": "main fix"},
                          {"url": derived, "desc": ""}])   # stored-first, deduped

    # -- terminal detail: digest-first -----------------------------------------

    def test_detail_is_digest_first_goal_before_summary(self):
        t = self._task()
        t["goal"] = "what done looks like"
        t["state"] = "next: wire the board"
        t["steps"] = [{"text": "a", "done": True}, {"text": "b", "done": False}]
        t["decisions"] = ["picked plan B"]
        ts.save_task(t)
        detail = ts._format_detail(ts.load_task(t["id"]), "sess")
        self.assertIn("Goal:", detail)
        self.assertIn("what done looks like", detail)
        self.assertIn("picked plan B", detail)
        # steps rollup present
        self.assertIn("Steps (1/2 done):", detail)
        # goal leads, full summary is LAST
        self.assertLess(detail.index("Goal:"), detail.index("Summary:"))
        self.assertLess(detail.index("Steps (1/2 done):"), detail.index("Summary:"))

    def test_detail_omits_digest_when_empty(self):
        t = self._task()
        detail = ts._format_detail(t, "sess")
        self.assertNotIn("Goal:", detail)
        self.assertNotIn("Steps (", detail)
        self.assertNotIn("Decisions:", detail)

    # -- progress rollup on the terminal lists ---------------------------------

    def test_progress_cell_on_terminal_ascii_list(self):
        t = self._task(title="Has steps")
        t["steps"] = [{"text": "a", "done": True}, {"text": "b", "done": False}]
        ts.save_task(t)
        self._task(title="No steps here")
        out = ts._format_list()
        self.assertIn("✓1/2", out)                      # rollup on the stepped row
        # the row without steps carries no rollup
        no_steps_line = [ln for ln in out.splitlines() if "No steps here" in ln][0]
        self.assertNotIn("✓", no_steps_line)

    def test_progress_cell_on_terminal_md_list(self):
        t = self._task(title="MD stepped")
        t["steps"] = [{"text": "a", "done": True}, {"text": "b", "done": True},
                      {"text": "c", "done": False}]
        ts.save_task(t)
        out = ts._format_list_md()
        self.assertIn("✓2/3", out)
        # still a valid 6-cell row (5 pipes' worth of separators preserved)
        row = [ln for ln in out.splitlines() if "MD stepped" in ln][0]
        self.assertEqual(row.count("|"), 7)

    # -- HTML board: progress + digest, escaping -------------------------------

    def test_board_progress_and_checklist_when_steps(self):
        t = self._task(title="Boarded steps")
        t["steps"] = [{"text": "alpha", "done": True}, {"text": "beta", "done": False}]
        t["decisions"] = ["went with X"]
        ts.save_task(t)
        html = self._run_board()
        self.assertIn('class="prog"', html)             # mini progress bar/chip
        self.assertIn("1/2", html)                      # rollup count
        self.assertIn('class="steps"', html)            # checklist in the expand
        self.assertIn("alpha", html)
        self.assertIn("went with X", html)              # decisions rendered

    def test_board_no_progress_chip_without_steps(self):
        self._task(title="Stepless")
        html = self._run_board()
        self.assertNotIn('class="prog"', html)

    def test_board_escapes_goal_and_decisions(self):
        t = self._task(title="Injection attempt")
        t["goal"] = "<script>alert(1)</script>"
        t["decisions"] = ["<img src=x onerror=alert(2)>"]
        ts.save_task(t)
        html = self._run_board()
        # raw HTML must be neutralised (escaped), never emitted live
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertNotIn("<img src=x onerror=alert(2)>", html)
        self.assertIn("&lt;script&gt;", html)

    # -- guidance mentions the new flags ---------------------------------------

    def test_guidance_mentions_new_flags(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_guidance(_Args())
        out = buf.getvalue()
        for flag in ("--goal", "--step-add", "--step-done", "--decision", "--pr"):
            self.assertIn(flag, out)


if __name__ == "__main__":
    unittest.main()
