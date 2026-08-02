"""Foundation A — reliable event attribution.

Threads `session` end-to-end so authored events (decision / milestone / status /
the scope-updated log) carry a `sid`. Also verifies the new `register=False` mode
of `touch` — a cross-task scope update attributes its log event WITHOUT registering
the acting session as a worker on the target task.

Isolation copies the `_repoint` idiom from tests/test_store_sqlite.py: pin
TASK_STATION_HOME to a tmp dir before importing the hyphenated module, repoint the
frozen path globals + reset the store cache in setUp. Never touches live data.
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

_TMP_HOME = tempfile.mkdtemp(prefix="ts-attrib-")
os.environ["TASK_STATION_HOME"] = _TMP_HOME

import store  # noqa: E402
import config  # noqa: E402

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


def _repoint(tmp):
    os.environ["TASK_STATION_HOME"] = tmp
    ts.DATA = tmp
    ts.STORE = os.path.join(tmp, "store")
    ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
    ts.LINKS_DIR = os.path.join(ts.STORE, "links")
    store.reset_cache()


class _Args:
    def __init__(self, **kw):
        defaults = dict(
            task=None, title=None, summary=None, append_summary=None, state=None,
            goal=None, step_add=None, step_done=None, step_undone=None,
            decision=None, log=None, relate=None, pr=None, pr_desc=None,
            story=None, story_desc=None, color=None, effort=None, session=None,
            value=None, trigger="auto",
        )
        defaults.update(kw)
        self.__dict__.update(defaults)


class AttributionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _repoint(self.tmp)
        self._stdin = sys.stdin

    def tearDown(self):
        sys.stdin = self._stdin
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self, title="A task", status="open"):
        t = ts.new_task(title, "summary")
        if status != "open":
            t["status"] = status
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _events_by_kind(self, task, kind):
        return [e for e in (task.get("events") or []) if e.get("kind") == kind]

    # (a) _update_one threads the session into decision/log + scope-updated events.
    def test_update_one_attributes_authored_events(self):
        t = self._seed("Rescope me")
        with redirect_stdout(io.StringIO()):
            ts._update_one(str(t["seq"]),
                           _Args(task=str(t["seq"]), decision=["chose X"],
                                 log=["shipped Y"], state="mid-flight", session="S"))
        r = ts.load_task(t["id"])
        dec = self._events_by_kind(r, "decision")
        mile = self._events_by_kind(r, "milestone")
        self.assertTrue(dec and dec[-1]["sid"] == "S")
        self.assertTrue(mile and mile[-1]["sid"] == "S")
        scope_logs = [e for e in self._events_by_kind(r, "log")
                      if (e.get("text") or "").startswith("scope updated")]
        self.assertTrue(scope_logs and scope_logs[-1]["sid"] == "S")

    # (b) A scope update on a task S is NOT attached to must not register S.
    def test_update_one_does_not_register_acting_session(self):
        t = self._seed("Not attached to S")
        self.assertNotIn("S", t.get("sessions", []))
        with redirect_stdout(io.StringIO()):
            ts._update_one(str(t["seq"]),
                           _Args(task=str(t["seq"]), decision=["a choice"], session="S"))
        r = ts.load_task(t["id"])
        self.assertNotIn("S", r.get("sessions", []))            # register=False
        self.assertNotIn("S", r.get("session_meta", {}))
        # …yet the scope-updated log event is still attributed to S.
        scope_logs = [e for e in self._events_by_kind(r, "log")
                      if (e.get("text") or "").startswith("scope updated")]
        self.assertTrue(scope_logs and scope_logs[-1]["sid"] == "S")

    # (c) cmd_post_compact attributes its history/milestone event to a.session.
    def test_post_compact_attributes_history_event(self):
        config.set("auto_checkpoint", True)
        try:
            t = self._seed("Compaction target")
            ts.set_link("cmp-sid", t["id"])
            sys.stdin = io.StringIO("some compaction summary text")
            with redirect_stdout(io.StringIO()):
                ts.cmd_post_compact(_Args(session="cmp-sid", trigger="auto"))
            r = ts.load_task(t["id"])
            mile = self._events_by_kind(r, "milestone")
            self.assertTrue(mile and mile[-1]["sid"] == "cmp-sid")
        finally:
            config.set("auto_checkpoint", False)

    # (d) `status --task <n> active --session S` → status event carries S.
    def test_status_cmd_attributes_status_event(self):
        t = self._seed("Promote me", status="open")
        with redirect_stdout(io.StringIO()):
            ts.cmd_status(_Args(task=str(t["seq"]), value="active", session="S"))
        r = ts.load_task(t["id"])
        st = self._events_by_kind(r, "status")
        self.assertTrue(st and st[-1]["sid"] == "S")
        self.assertIn("active", st[-1]["text"])

    # set_status / promote_active forward the session directly, too.
    def test_set_status_forwards_session(self):
        t = ts.new_task("t", "s")
        self.assertTrue(ts.set_status(t, ts.STATUS_ACTIVE, session="sid-z"))
        st = self._events_by_kind(t, "status")
        self.assertEqual(st[-1]["sid"], "sid-z")

    def test_promote_active_forwards_session(self):
        t = ts.new_task("t", "s")
        self.assertTrue(ts.promote_active(t, session="sid-p"))
        st = self._events_by_kind(t, "status")
        self.assertEqual(st[-1]["sid"], "sid-p")


if __name__ == "__main__":
    unittest.main()
