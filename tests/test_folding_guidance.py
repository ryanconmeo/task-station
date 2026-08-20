"""Folding guidance must not re-home a standing-down child onto its parent task.

THE MECHANISM (from the #543 session). Closing a task DETACHES its session, so the
child's very next prompt arrives unattached and hits the fold-don't-fork rule: fold
into an existing task rather than create a sibling, under FOLD ON IDENTITY, NOT
FLAVOR — when the prompt names a PR or work item, fold only into a task carrying that
SAME key. The parent carries the child's PR as a key, so the PARENT is the identity
match, and the hook routes a standing-down child straight onto its parent's ledger.
That is how #543's session came to write four acks to #531's log.

THE FIX. `skip` already exists and is already the right answer for a session that is
standing down — no new mechanism, just a preference for the one that's there. So the
parent of a task this session JUST CLOSED is dropped from the fold candidates, and
when that leaves nothing to fold into, the guidance prefers SKIP over both attaching
and forking.

These tests pin both halves, plus the precision of the exclusion: a non-parent match
still folds, another session is unaffected, and an OPEN child excludes nothing.
"""
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

import store  # noqa: E402

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        os.environ.pop("TASK_STATION_GATE", None)
        os.environ.pop("TASK_STATION_GUARANTEED_TRACKING", None)
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.PROJECTS_ROOT = os.path.join(self.tmp, "projects")
        store.reset_cache()

    def tearDown(self):
        store.reset_cache()
        for var in ("TASK_STATION_HOME", "TASK_STATION_PROMPT",
                    "TASK_STATION_GUARANTEED_TRACKING"):
            os.environ.pop(var, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- fixtures ------------------------------------------------------------
    def _seed(self, title, summary=""):
        t = ts.new_task(title, summary)
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _close_child_of(self, parent, session, title="Implement the folding guard (PR 1115)"):
        """A child task with a stored `parent` edge, worked and then CLOSED by
        `session` — exactly the state cmd_done leaves behind (closed task, session
        recorded on it, session's link cleared)."""
        child = self._seed(title)
        child.setdefault("related", []).append({"kind": "parent", "id": parent["id"]})
        ts.touch(child, session=session, note="worked")
        child["status"] = ts.STATUS_CLOSED
        ts.stamp_closed(child)
        ts.save_task(child)
        ts.clear_link(session)          # cmd_done detaches on close
        ts.clear_count(session)
        return ts.load_task(child["id"])

    def _nudge(self, session, prompt=""):
        os.environ["TASK_STATION_PROMPT"] = prompt
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_prompt_context(_Args(session=session))
        return buf.getvalue()

    def _offered(self, out, task):
        """True iff `task` is rendered as a fold/attach CANDIDATE ROW — the exact
        `  - #<seq> [<id8>] <title>` offer shape `_fold_candidate_lines` emits."""
        return ("  - #%s [%s]" % (task.get("seq"), task["id"][:8])) in out


class ParentIsNotAFoldTarget(_Base):
    """Exit condition 2 — a session on a just-closed child, prompted again, is not
    offered its parent as a fold target."""

    def test_parent_of_just_closed_task_is_not_a_fold_candidate(self):
        parent = self._seed("Orchestrate the release wave (PR 1115)")
        self._close_child_of(parent, "s-child")
        out = self._nudge("s-child", "acknowledged — the gate on PR 1115 passed")
        self.assertFalse(self._offered(out, parent),
                         "parent was offered as a fold candidate:\n%s" % out)

    def test_a_non_parent_identity_match_still_folds(self):
        """Precision: the exclusion removes the PARENT, not every keyed task. A
        sibling carrying the same PR is still a legitimate fold target."""
        parent = self._seed("Orchestrate the release wave (PR 1115)")
        sibling = self._seed("Chase the flaky check on PR 1115")
        self._close_child_of(parent, "s-child")
        out = self._nudge("s-child", "one more thing about PR 1115")
        self.assertTrue(self._offered(out, sibling),
                        "the non-parent same-key task stopped being offered:\n%s" % out)
        self.assertFalse(self._offered(out, parent),
                         "parent was offered as a fold candidate:\n%s" % out)

    def test_another_session_is_still_offered_the_parent(self):
        """Scoped to the session that stood down — a session that never worked the
        child sees the board unchanged."""
        parent = self._seed("Orchestrate the release wave (PR 1115)")
        self._close_child_of(parent, "s-child")
        out = self._nudge("s-other", "picking up PR 1115")
        self.assertTrue(self._offered(out, parent),
                        "an unrelated session lost the parent as a candidate:\n%s" % out)

    def test_open_child_excludes_nothing(self):
        """The trigger is a CLOSED task. A session whose child is still open has not
        stood down, so its parent stays a normal candidate."""
        parent = self._seed("Orchestrate the release wave (PR 1115)")
        child = self._seed("Implement the folding guard (PR 1115)")
        child.setdefault("related", []).append({"kind": "parent", "id": parent["id"]})
        ts.touch(child, session="s-child", note="worked")
        ts.save_task(child)
        ts.clear_link("s-child")
        out = self._nudge("s-child", "still working on PR 1115")
        self.assertTrue(self._offered(out, parent),
                        "an OPEN child suppressed its parent:\n%s" % out)


class ClosedChildPrefersSkip(_Base):
    """Exit condition 1 — the folding guidance prefers SKIP when the only identity
    match is the PARENT of the task this session just closed."""

    def test_only_match_is_the_parent_so_skip_is_preferred(self):
        parent = self._seed("Orchestrate the release wave (PR 1115)")
        self._seed("Add dark mode")          # keyless distractor, not a match
        self._close_child_of(parent, "s-child")
        out = self._nudge("s-child", "acknowledged — the gate on PR 1115 passed")
        self.assertIn("task-station skip --session s-child", out)
        self.assertFalse(self._offered(out, parent), out)

    def test_skip_is_preferred_over_forking_a_sibling(self):
        """Not just 'don't attach' — the standing-down turn must not be steered into
        creating a task either. SKIP is the answer, so the create template is not
        the headline."""
        parent = self._seed("Orchestrate the release wave (PR 1115)")
        self._close_child_of(parent, "s-child")
        out = self._nudge("s-child", "acknowledged — the gate on PR 1115 passed")
        self.assertIn("skip", out.lower())
        self.assertNotIn("Track this topic NOW as a NEW task", out)

    def test_it_keeps_preferring_skip_on_later_turns(self):
        """#543's session wrote FOUR acks. The guidance has to hold past the first
        miss, where the ordinary nudge collapses to a one-liner."""
        parent = self._seed("Orchestrate the release wave (PR 1115)")
        self._close_child_of(parent, "s-child")
        outs = [self._nudge("s-child", "ack %d on PR 1115" % i) for i in range(1, 5)]
        for i, out in enumerate(outs, 1):
            self.assertIn("task-station skip --session s-child", out,
                          "turn %d stopped preferring skip:\n%s" % (i, out))
            self.assertFalse(self._offered(out, parent),
                             "turn %d offered the parent:\n%s" % (i, out))

    def test_a_genuinely_new_topic_is_unaffected(self):
        """Fail-open: a keyed prompt that matches nothing on the board still gets the
        ordinary create-bias, not the stand-down block."""
        parent = self._seed("Orchestrate the release wave (PR 1115)")
        self._close_child_of(parent, "s-child")
        out = self._nudge("s-child", "start on PR 9999")
        self.assertIn("No open task carries PR 9999", out)


class AutoTrackDoesNotRehome(_Base):
    """Guaranteed-tracking (opt-in) folds AUTOMATICALLY — so it is the one path that
    can re-home a standing-down child with no model in the loop. It must not."""

    def test_auto_track_does_not_fold_a_standing_down_child_into_its_parent(self):
        os.environ["TASK_STATION_GUARANTEED_TRACKING"] = "on"
        parent = self._seed("Orchestrate the release wave (PR 1115)")
        self._close_child_of(parent, "s-child")
        self._nudge("s-child", "Orchestrate the release wave (PR 1115) — ack")
        self.assertNotEqual(ts.get_link("s-child"), parent["id"],
                            "auto-track attached the standing-down child to its parent")


if __name__ == "__main__":
    unittest.main()
