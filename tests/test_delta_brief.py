"""WS5 — delta-injection context routing.

`delta_brief`/`mark_seen` and their two injection hooks (SessionStart,
UserPromptSubmit) + the detail-render mark-seen. Events feed authored by a
sibling workstream is NOT in this tree, so these tests build `task["events"]`
dicts by hand against the New Data Shapes contract.

Isolation mirrors tests/test_store_sqlite.py: TASK_STATION_HOME is pinned to a
tmp dir BEFORE importing the hyphenated module, and each store-touching suite
repoints the frozen path globals + resets the store cache in setUp.
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

# Freeze the store under a throwaway dir BEFORE importing the module.
_TMP_HOME = tempfile.mkdtemp(prefix="ts-delta-")
os.environ["TASK_STATION_HOME"] = _TMP_HOME

import store  # noqa: E402  (normal import — store.py has no hyphen)

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
        self.session = None
        self.source = ""
        self.arg = ""
        for k, v in kw.items():
            setattr(self, k, v)


def _ev(ts_val, kind, sid, text):
    return {"ts": ts_val, "kind": kind, "sid": sid, "text": text}


# ------------------------------------------------------- pure-helper units ----

class DeltaBriefUnitTest(unittest.TestCase):
    def test_delta_excludes_own_and_seen_events(self):
        t = ts.new_task("t", "s")
        t["events"] = [
            _ev(100.0, "log", "sid-a", "AAA-old"),
            _ev(300.0, "log", "sid-a", "AAA-new"),
            _ev(300.0, "worker", "sid-b", "BBB-new"),
        ]
        # sid-a watermark sits between the old and new events; sid-b has only an
        # attach ts (also between old and new) with no explicit seen_ts.
        t["session_meta"] = {"sid-a": {"ts": 100.0, "seen_ts": 200.0},
                             "sid-b": {"ts": 200.0}}

        a = ts.delta_brief(t, "sid-a")
        self.assertIsNotNone(a)
        self.assertIn("BBB-new", a)          # other session, newer than seen
        self.assertNotIn("AAA-new", a)       # own event excluded
        self.assertNotIn("AAA-old", a)       # older than seen

        b = ts.delta_brief(t, "sid-b")
        self.assertIsNotNone(b)
        self.assertIn("AAA-new", b)          # other session, newer than attach ts
        self.assertNotIn("AAA-old", b)       # older than attach ts
        self.assertNotIn("BBB-new", b)       # own event excluded

    def test_delta_caps_items_and_chars(self):
        t = ts.new_task("t", "s")
        t["seq"] = 366
        t["events"] = [_ev(1000.0 + i, "log", "other", "u%02d" % i) for i in range(20)]
        brief = ts.delta_brief(t, "me")      # no session_meta → seen defaults to 0
        self.assertIsNotNone(brief)
        self.assertEqual(brief.count("  • "), ts.DELTA_MAX_ITEMS)   # 6 rendered
        self.assertIn("(+14 earlier)", brief)                        # 20 - 6
        self.assertLessEqual(len(brief), ts.DELTA_MAX_CHARS)
        self.assertIn("20 update(s)", brief)
        # Newest-last: the last bullet is the newest event (u19), not u13.
        self.assertIn("u19", brief)
        self.assertNotIn("u05", brief)       # trimmed as one of the 14 earlier

    def test_delta_caps_chars_with_long_text(self):
        # Long events blow the per-line budget; fewer than max_items render but the
        # block still respects DELTA_MAX_CHARS and reports the rest as earlier.
        t = ts.new_task("t", "s")
        t["events"] = [_ev(1000.0 + i, "worker", "other", "x" * 160) for i in range(6)]
        brief = ts.delta_brief(t, "me")
        self.assertIsNotNone(brief)
        self.assertLessEqual(len(brief), ts.DELTA_MAX_CHARS)
        self.assertIn("earlier)", brief)

    def test_delta_none_when_quiet(self):
        self.assertIsNone(ts.delta_brief(ts.new_task("bare", ""), "me"))
        own = ts.new_task("t", "")
        own["events"] = [_ev(100.0, "log", "me", "mine")]
        self.assertIsNone(ts.delta_brief(own, "me"))       # all own → None
        seen = ts.new_task("t", "")
        seen["events"] = [_ev(100.0, "log", "other", "old")]
        seen["session_meta"] = {"me": {"seen_ts": 500.0}}
        self.assertIsNone(ts.delta_brief(seen, "me"))      # all older than seen → None

    def test_delta_excludes_memo_but_carries_memo_ack(self):
        # `memo` arrivals belong to memo_pending_brief; `memo-ack` still flows so a
        # session hears when a peer acknowledged.
        t = ts.new_task("t", "s")
        t["events"] = [
            _ev(100.0, "memo", "other", "memo abc from x: some fact"),
            _ev(200.0, "memo-ack", "other", "memo abc acked by yyyyyyyy"),
        ]
        brief = ts.delta_brief(t, "me")
        self.assertIsNotNone(brief)
        self.assertNotIn("some fact", brief)             # memo arrival excluded
        self.assertIn("acked by", brief)                 # memo-ack carried

    def test_delta_none_when_only_memo_events(self):
        t = ts.new_task("t", "s")
        t["events"] = [_ev(100.0, "memo", "other", "memo abc from x: fact")]
        self.assertIsNone(ts.delta_brief(t, "me"))       # nothing but a memo arrival

    def test_delta_treats_missing_sid_as_other(self):
        t = ts.new_task("t", "")
        t["events"] = [_ev(100.0, "log", None, "unattributed edit")]
        brief = ts.delta_brief(t, "me")
        self.assertIsNotNone(brief)
        self.assertIn("unattributed edit", brief)

    def test_mark_seen_tolerates_unknown_session(self):
        t = ts.new_task("t", "")
        ts.mark_seen(t, "ghost")
        self.assertIn("ghost", t["session_meta"])
        self.assertIsInstance(t["session_meta"]["ghost"]["seen_ts"], float)
        # None session is a no-op, never a KeyError.
        ts.mark_seen(t, None)
        self.assertNotIn(None, t.get("session_meta", {}))

    def test_mark_seen_preserves_existing_meta_fields(self):
        t = ts.new_task("t", "")
        t["session_meta"] = {"s1": {"cwd": "/x", "ts": 1.0, "role": "hub"}}
        ts.mark_seen(t, "s1")
        self.assertEqual(t["session_meta"]["s1"]["cwd"], "/x")
        self.assertIn("seen_ts", t["session_meta"]["s1"])


# --------------------------------------------------------- hook wiring --------

class DeltaHookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _repoint(self.tmp)

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        os.environ.pop("TASK_STATION_PROMPT", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _attached_task_with_news(self, sid):
        t = ts.new_task("Session tree work", "the summary")
        t["events"] = [_ev(ts._now() - 600, "worker", "other",
                            "worker finished: claude-todo:ws1 — authored the feed"),
                       _ev(ts._now() - 60, "child", "other-2",
                           "child #365 closed: checkpoint best practices")]
        ts.save_task(t)
        ts.set_link(sid, t["id"])
        return t

    def _capture(self, fn, args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            fn(args)
        return buf.getvalue()

    def test_session_start_injects_and_marks_seen(self):
        sid = "sess-start-1"
        self._attached_task_with_news(sid)
        out = self._capture(ts.cmd_session_start, _Args(session=sid, source="startup"))
        self.assertIn("While you were away", out)
        self.assertIn("worker finished", out)
        # Second invocation (watermark advanced + saved) prints the banner but no delta.
        out2 = self._capture(ts.cmd_session_start, _Args(session=sid, source="startup"))
        self.assertIn("attached to task", out2)
        self.assertNotIn("While you were away", out2)

    def test_prompt_context_injects_delta_once(self):
        sid = "sess-prompt-1"
        os.environ["TASK_STATION_PROMPT"] = "keep going on the design pass"
        self._attached_task_with_news(sid)
        out = self._capture(ts.cmd_prompt_context, _Args(session=sid))
        self.assertIn("While you were away", out)
        # Re-prompt: watermark advanced → the attached+open path stays silent.
        out2 = self._capture(ts.cmd_prompt_context, _Args(session=sid))
        self.assertEqual(out2, "")

    def test_prompt_context_stays_silent_without_delta(self):
        sid = "sess-prompt-quiet"
        os.environ["TASK_STATION_PROMPT"] = "keep going"
        t = ts.new_task("Quiet task", "no news")     # no events feed
        ts.save_task(t)
        ts.set_link(sid, t["id"])
        out = self._capture(ts.cmd_prompt_context, _Args(session=sid))
        self.assertEqual(out, "")

    def test_prompt_context_skips_own_session_events(self):
        # Events all authored by THIS session → nothing to tell it about.
        sid = "sess-self"
        os.environ["TASK_STATION_PROMPT"] = "carry on"
        t = ts.new_task("Solo task", "")
        t["events"] = [_ev(ts._now() - 30, "log", sid, "my own note")]
        ts.save_task(t)
        ts.set_link(sid, t["id"])
        out = self._capture(ts.cmd_prompt_context, _Args(session=sid))
        self.assertEqual(out, "")

    def test_render_detail_marks_seen(self):
        sid = "sess-render"
        t = self._attached_task_with_news("someone-else")   # not yet linked to sid
        ref = t["id"][:8]
        out = self._capture(ts.cmd_render, _Args(session=sid, arg=ref))
        self.assertIn("Task [", out)                          # detail rendered
        reloaded = ts.load_task(t["id"])
        self.assertIn("seen_ts", reloaded["session_meta"][sid])
        # A prompt turn right after viewing the detail sees no delta.
        os.environ["TASK_STATION_PROMPT"] = "next"
        out2 = self._capture(ts.cmd_prompt_context, _Args(session=sid))
        self.assertNotIn("While you were away", out2)

    # -- memo delivery through the hooks --------------------------------------
    def test_prompt_context_injects_pending_memo(self):
        sid = "sess-memo-p"
        os.environ["TASK_STATION_PROMPT"] = "keep going"
        t = self._attached_task_with_news(sid)          # sid is attached, own the feed
        ts.memo_send(t, "a fact from a peer session", from_sid="peer")
        ts.save_task(t)
        out = self._capture(ts.cmd_prompt_context, _Args(session=sid))
        self.assertIn("awaiting YOUR ack", out)
        self.assertIn("a fact from a peer session", out)
        # Re-prompt: the delta is watermark-cleared, but the UNACKED memo re-surfaces.
        out2 = self._capture(ts.cmd_prompt_context, _Args(session=sid))
        self.assertIn("awaiting YOUR ack", out2)
        # After acking, the pending block is gone.
        t2 = ts.load_task(t["id"])
        ts.memo_ack(t2, t2["memos"][-1], sid)
        ts.save_task(t2)
        out3 = self._capture(ts.cmd_prompt_context, _Args(session=sid))
        self.assertNotIn("awaiting YOUR ack", out3)

    def test_session_start_injects_pending_memo(self):
        sid = "sess-memo-s"
        t = self._attached_task_with_news(sid)
        ts.memo_send(t, "startup memo fact", from_sid="peer")
        ts.save_task(t)
        out = self._capture(ts.cmd_session_start, _Args(session=sid, source="startup"))
        self.assertIn("awaiting YOUR ack", out)
        self.assertIn("startup memo fact", out)

    def test_detail_render_does_not_clear_pending(self):
        # Viewing the detail marks the delta watermark seen but must NOT ack a memo.
        sid = "sess-memo-d"
        t = self._attached_task_with_news(sid)
        ts.memo_send(t, "detail-safe memo", from_sid="peer")
        ts.save_task(t)
        self._capture(ts.cmd_render, _Args(session=sid, arg=t["id"][:8]))
        os.environ["TASK_STATION_PROMPT"] = "next"
        out = self._capture(ts.cmd_prompt_context, _Args(session=sid))
        self.assertIn("awaiting YOUR ack", out)         # still pending after viewing


if __name__ == "__main__":
    unittest.main()
