"""WS1 — per-task event feed, relations, `--relate`, and automatic spawn-edge
capture.

Covers the New Data Shapes contract:
  * `add_event` — bounded, session-attributed feed; text truncated; cap enforced.
  * the wired mutators (`add_log`/`append_decision`/`append_history`/`record_run`/
    `set_status`, `update --summary`) each emit a matching-kind event.
  * `append_related` — idempotent edge list stored on the child / relate target.
  * `cmd_create` records a `spawned-from` edge (the 363→365 silent-spawn case) and
    the parent's feed hears about it.
  * `update --relate` posts to both feeds and is idempotent.
  * events + related round-trip through the SQLite data column.

Per-test temp-home isolation (copies the `_repoint` idiom from
`tests/test_store_sqlite.py`) plus a synthetic transcript for the substantive-
session check. Never touches live data.
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

import store  # noqa: E402  (normal import — store.py has no hyphen)

# task-station.py has a hyphen, so load it by path (see tests/test_board.py:20-22).
_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


def _repoint(tmp):
    """Point task-station.py's import-frozen path globals at a fresh tmp store."""
    os.environ["TASK_STATION_HOME"] = tmp
    ts.DATA = tmp
    ts.STORE = os.path.join(tmp, "store")
    ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
    ts.LINKS_DIR = os.path.join(ts.STORE, "links")
    store.reset_cache()


class _Args:
    """A stand-in argparse.Namespace covering every attribute cmd_create / cmd_update
    read (all defaulting to the parser's default) so a test only sets what it means."""
    def __init__(self, **kw):
        defaults = dict(
            # create (create callers MUST pass summary=<str> — None.strip() would crash)
            session=None, title=None, summary=None, color=None, effort=None,
            force=False, no_attach=False, attach=False, active=False, goal=None, step=None,
            # update
            task=None, append_summary=None, state=None, step_add=None, step_done=None,
            step_undone=None, decision=None, log=None, relate=None, pr=None, pr_desc=None,
            story=None, story_desc=None,
        )
        defaults.update(kw)
        self.__dict__.update(defaults)


class EventsRelationsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _repoint(self.tmp)
        self.proj = os.path.join(self.tmp, "projects")
        os.makedirs(self.proj, exist_ok=True)
        ts.PROJECTS_ROOT = self.proj

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers ---------------------------------------------------------------
    def _seed(self, title):
        """Create + persist a task and give it a stable seq; return the reloaded blob."""
        t = ts.new_task(title, "summary")
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _fake_transcript(self, sid, msgs=3):
        """Write a real .jsonl under a PROJECTS_ROOT bucket with `msgs` user lines so
        _find_session_path finds it and _session_msgcount clears SUBSTANCE_FLOOR."""
        bucket = os.path.join(self.proj, "-work-bucket")
        os.makedirs(bucket, exist_ok=True)
        path = os.path.join(bucket, sid + ".jsonl")
        with open(path, "w") as f:
            for i in range(msgs):
                f.write('{"message": {"role": "user", "content": "real prompt %d"}}\n' % i)
        return path

    def _out(self, fn, args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            fn(args)
        return buf.getvalue()

    # -- add_event -------------------------------------------------------------
    def test_add_event_caps_and_truncates(self):
        t = ts.new_task("t", "s")
        for _ in range(105):
            ts.add_event(t, "log", "x" * 500, session="sid-a")
        self.assertEqual(len(t["events"]), ts.EVENTS_KEEP)              # 100, most-recent kept
        self.assertEqual(len(t["events"][-1]["text"]), ts.EVENT_TEXT_MAX)  # 160
        self.assertEqual(t["events"][-1]["sid"], "sid-a")
        self.assertEqual(t["events"][-1]["kind"], "log")
        self.assertIsInstance(t["events"][-1]["ts"], float)

    def test_add_event_absent_by_default(self):
        # A brand-new task has no feed until something appends — back-compat.
        self.assertNotIn("events", ts.new_task("bare", ""))

    # -- Foundation B: stable event ids ---------------------------------------
    def test_add_event_returns_event_with_stable_hex_id(self):
        t = ts.new_task("t", "s")
        ev = ts.add_event(t, "log", "hello", session="sid-a")
        # add_event now returns the appended dict (was None).
        self.assertIsNotNone(ev)
        self.assertIs(ev, t["events"][-1])
        self.assertIn("id", ev)
        self.assertEqual(len(ev["id"]), 32)                 # uuid4().hex
        int(ev["id"], 16)                                   # 32-char hex, parses

    def test_add_event_ids_are_unique(self):
        t = ts.new_task("t", "s")
        ids = {ts.add_event(t, "log", "x", session="s")["id"] for _ in range(20)}
        self.assertEqual(len(ids), 20)

    def test_legacy_event_without_id_or_sid_renders(self):
        # A hand-built pre-Foundation-B event (no `id`, no `sid`) must still render
        # through delta_brief and _format_history exactly as before — readers use .get.
        t = ts.new_task("legacy", "s")
        t["seq"] = 7
        t["events"] = [{"ts": 100.0, "kind": "log", "text": "legacy unattributed edit"}]
        brief = ts.delta_brief(t, "me")                     # missing sid counts as "other"
        self.assertIsNotNone(brief)
        self.assertIn("legacy unattributed edit", brief)
        # _format_history must not crash on the id-less event.
        hist = ts._format_history(t)
        self.assertIn("History — Task #7", hist)

    # -- wired mutators --------------------------------------------------------
    def test_wired_mutators_emit_events(self):
        t = ts.new_task("a", "s")

        ts.add_log(t, "did a thing", session="sid-x")
        self.assertEqual(t["events"][-1]["kind"], "log")
        self.assertEqual(t["events"][-1]["sid"], "sid-x")
        self.assertEqual(t["events"][-1]["text"], "did a thing")

        ts.append_decision(t, "chose X")
        self.assertEqual(t["events"][-1]["kind"], "decision")
        self.assertEqual(t["events"][-1]["text"], "chose X")

        ts.append_history(t, "shipped Y")
        self.assertEqual(t["events"][-1]["kind"], "milestone")

        ts.record_run(t, session_id="wk-1", model="claude-opus-4-8", cost_usd=0.5)
        self.assertEqual(t["events"][-1]["kind"], "run")
        self.assertEqual(t["events"][-1]["sid"], "wk-1")

        # new_task starts open; promoting to active emits a status event last.
        self.assertTrue(ts.set_status(t, ts.STATUS_ACTIVE))
        self.assertEqual(t["events"][-1]["kind"], "status")
        self.assertIn("active", t["events"][-1]["text"])

    def test_touch_threads_session_into_log_event(self):
        t = ts.new_task("a", "s")
        ts.touch(t, session="sid-hub", note="attached")
        log_evs = [e for e in t["events"] if e["kind"] == "log"]
        self.assertTrue(log_evs)
        self.assertEqual(log_evs[-1]["sid"], "sid-hub")

    # -- append_related --------------------------------------------------------
    def test_append_related_idempotent(self):
        a, b = ts.new_task("a", ""), ts.new_task("b", "")
        a["seq"], b["seq"] = 1, 2
        self.assertTrue(ts.append_related(a, b, "spawned-from"))
        self.assertFalse(ts.append_related(a, b, "spawned-from"))   # dup id+kind → no-op
        self.assertEqual(len(a["related"]), 1)
        self.assertEqual(a["related"][0]["seq"], 2)
        self.assertEqual(a["related"][0]["id"], b["id"])
        self.assertEqual(a["related"][0]["kind"], "spawned-from")
        # A DIFFERENT kind to the same target is a distinct edge.
        self.assertTrue(ts.append_related(a, b, "related"))
        self.assertEqual(len(a["related"]), 2)

    # -- spawn-edge capture ----------------------------------------------------
    def test_create_from_attached_session_records_spawn_edge(self):
        parent = self._seed("Parent conversation topic")
        sid = "hub-session-uuid"
        self._fake_transcript(sid, msgs=4)          # ≥ SUBSTANCE_FLOOR user msgs
        ts.set_link(sid, parent["id"])
        self.assertTrue(ts._is_substantive_tracked(sid))   # guard: precondition holds

        out = self._out(ts.cmd_create,
                        _Args(session=sid, title="Distinct spun-off widget", summary="s"))
        self.assertIn("spawned-from #%s" % parent["seq"], out)

        new = [x for x in ts.all_tasks() if x["id"] != parent["id"]][0]
        rel = new.get("related") or []
        self.assertEqual(len(rel), 1)
        self.assertEqual(rel[0]["kind"], "spawned-from")
        self.assertEqual(rel[0]["seq"], parent["seq"])
        self.assertEqual(rel[0]["id"], parent["id"])

        parent2 = ts.load_task(parent["id"])
        self.assertEqual(parent2["events"][-1]["kind"], "child")
        self.assertTrue(parent2["events"][-1]["text"].startswith("spawned #%s" % new["seq"]))

    # -- update --relate -------------------------------------------------------
    def test_update_relate_flag(self):
        t1 = self._seed("First task alpha")
        t2 = self._seed("Second task beta")

        out = self._out(ts.cmd_update,
                        _Args(task=str(t1["seq"]), relate=[str(t2["seq"])], session="sid-a"))
        self.assertIn("relate", out)

        t1r = ts.load_task(t1["id"])
        t2r = ts.load_task(t2["id"])
        self.assertTrue(any(r["kind"] == "related" and r["seq"] == t2["seq"]
                            for r in (t1r.get("related") or [])))
        self.assertEqual(t2r["events"][-1]["kind"], "child")
        self.assertIn("#%s" % t1["seq"], t2r["events"][-1]["text"])

        # Second run is idempotent: nothing to change, no new event on the other side.
        t2_events_before = len(t2r["events"])
        out2 = self._out(ts.cmd_update,
                         _Args(task=str(t1["seq"]), relate=[str(t2["seq"])], session="sid-a"))
        self.assertIn("nothing to change", out2)
        self.assertEqual(len(ts.load_task(t2["id"])["events"]), t2_events_before)

    def test_update_relate_rejects_self(self):
        t1 = self._seed("Solo task")
        out = self._out(ts.cmd_update, _Args(task=str(t1["seq"]), relate=[str(t1["seq"])]))
        self.assertIn("can't relate to itself", out)
        self.assertFalse(ts.load_task(t1["id"]).get("related"))

    def test_update_summary_emits_summary_event(self):
        t = self._seed("Task to rescope")
        self._out(ts.cmd_update, _Args(task=str(t["seq"]), summary="new scope", session="s1"))
        evs = ts.load_task(t["id"])["events"]
        self.assertTrue(any(e["kind"] == "summary" for e in evs))

    # -- add-event CLI ---------------------------------------------------------
    def test_cmd_add_event_quiet_bookkeeping(self):
        t = self._seed("Worker target")
        out = self._out(ts.cmd_add_event,
                        _Args(task=str(t["seq"]), kind="worker",
                              text="worker finished: claude-todo:ws1", session="wk-sid"))
        self.assertEqual(out, "")                      # silent, like add-cost
        r = ts.load_task(t["id"])
        self.assertEqual(r["events"][-1]["kind"], "worker")
        self.assertEqual(r["events"][-1]["sid"], "wk-sid")
        # No attach / no session recorded (quiet bookkeeping, no touch).
        self.assertEqual(ts.get_link("wk-sid"), None)

    # -- round trip ------------------------------------------------------------
    def test_events_round_trip_sqlite(self):
        t = ts.new_task("rt", "s")
        t["seq"] = 5
        ts.add_event(t, "worker", "worker finished", session="wk")
        ts.append_related(t, {"id": "other-task-id", "seq": 9}, "related")
        ts.save_task(t)
        r = ts.load_task(t["id"])
        self.assertEqual(r["events"], t["events"])
        self.assertEqual(r["related"], t["related"])


class ConcurrentMutationTest(unittest.TestCase):
    """The optimistic-lock conversion of the delegate entrypoints: two interleaved
    writers appending events/costs to one task lose zero entries (a plain
    load→save would clobber one)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _repoint(self.tmp)

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self):
        t = ts.new_task("race", "s")
        ts.create_with_seq(t)
        return t["id"]

    def _race_once(self, inject):
        """Wrap the backend's save_task so the FIRST versioned save triggers `inject`
        (a concurrent unversioned writer) beforehand — forcing exactly one RevConflict
        so the entrypoint's store.mutate must reload + retry."""
        b = ts._backend()
        real = b.save_task
        state = {"raced": False}

        def racing(task, expected_rev=None):
            if expected_rev is not None and not state["raced"]:
                state["raced"] = True
                inject(b)                      # concurrent session commits first
            return real(task, expected_rev=expected_rev)
        b.save_task = racing
        return b, real, state

    def test_add_event_no_lost_entry_under_conflict(self):
        tid = self._seed()

        def inject(b):
            other = b.load_task(tid)
            ts.add_event(other, "other", "from other session", "sB")
            b.save_task(other)                 # unversioned bump → our expected_rev stale
        b, real, state = self._race_once(inject)
        try:
            a = _Args(task=tid); a.kind = "mine"; a.text = "from me"; a.session = "sA"
            ts.cmd_add_event(a)
        finally:
            b.save_task = real
        self.assertTrue(state["raced"])
        kinds = sorted(e["kind"] for e in ts.load_task(tid)["events"])
        self.assertEqual(kinds, ["mine", "other"])   # both survived; neither clobbered

    def test_add_cost_accumulates_under_conflict(self):
        tid = self._seed()

        def inject(b):
            other = b.load_task(tid)
            ts.add_cost(other, 1.00)
            b.save_task(other)
        b, real, state = self._race_once(inject)
        try:
            a = _Args(task=tid)
            a.usd = 2.00
            a.model = a.session = a.seq_label = a.usage_json = None
            ts.cmd_add_cost(a)
        finally:
            b.save_task = real
        self.assertTrue(state["raced"])
        total, runs = ts.task_cost(ts.load_task(tid))
        self.assertAlmostEqual(total, 3.00, places=6)   # 1.00 + 2.00, neither lost


if __name__ == "__main__":
    unittest.main()
