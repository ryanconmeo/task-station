"""Memo correspondence — data ops (Phase 3), pending brief + delivery (Phase 4),
CLI + /todo + render (Phase 5).

A memo hands a fact/decision to a task's working session(s): `memo_send` posts a
record onto a target task (attached or not) + a capped preview event; each session
explicitly acks on a shared, visible ledger so no two sessions double-implement.

Isolation copies the `_repoint` idiom from tests/test_store_sqlite.py.
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

_TMP_HOME = tempfile.mkdtemp(prefix="ts-memo-")
os.environ["TASK_STATION_HOME"] = _TMP_HOME

import store  # noqa: E402

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
        defaults = dict(task=None, text=None, id=None, session=None,
                        decision=None, sub=None)
        defaults.update(kw)
        self.__dict__.update(defaults)


class _MemoBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _repoint(self.tmp)

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self, title="A task"):
        t = ts.new_task(title, "summary")
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _out(self, fn, args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            fn(args)
        return buf.getvalue()


# ---------------------------------------------------------- Phase 3: data ops
class MemoCoreTest(_MemoBase):
    def test_send_creates_record_with_shared_event_id(self):
        t = self._seed()
        memo = ts.memo_send(t, "Use WAL mode because it avoids writer starvation",
                            from_sid="sender-sid")
        self.assertIn("memos", t)
        self.assertEqual(t["memos"][-1], memo)
        self.assertEqual(len(memo["id"]), 32)
        # A companion 'memo' event rides the feed and SHARES the memo id.
        mev = [e for e in t["events"] if e.get("kind") == "memo"][-1]
        self.assertEqual(mev["id"], memo["id"])
        self.assertEqual(mev["sid"], "sender-sid")
        self.assertIn(memo["id"][:8], mev["text"])
        self.assertLessEqual(len(mev["text"]), ts.EVENT_TEXT_MAX)
        # Ledger starts empty; from_sid recorded.
        self.assertEqual(memo["acks"], [])
        self.assertEqual(memo["from_sid"], "sender-sid")

    def test_body_uncapped_and_round_trips(self):
        t = self._seed()
        t["seq"] = 3
        big = "x" * 50000
        memo = ts.memo_send(t, big, from_sid=None)
        self.assertEqual(memo["text"], big)                        # body arrives whole — no cap
        self.assertIsNone(memo["from_sid"])                        # anonymous/Desktop
        ts.save_task(t)
        r = ts.load_task(t["id"])
        self.assertEqual(r["memos"], t["memos"])                   # dict round-trips SQLite

    def test_cross_task_send_records_from_task(self):
        home = self._seed("Sender home task")
        target = self._seed("Target task")
        ts.set_link("sender-sid", home["id"])
        memo = ts.memo_send(target, "cross-task fact", from_sid="sender-sid")
        self.assertEqual(memo["from_task"], home["id"])

    def test_ack_appends_idempotent_and_posts_event(self):
        t = self._seed()
        memo = ts.memo_send(t, "a fact", from_sid="sender")
        self.assertEqual(ts.memo_ack(t, memo, "acker"), "acked")
        self.assertEqual([a["sid"] for a in memo["acks"]], ["acker"])
        self.assertIsInstance(memo["acks"][0]["ts"], float)
        ack_ev = [e for e in t["events"] if e.get("kind") == "memo-ack"][-1]
        self.assertEqual(ack_ev["sid"], "acker")
        self.assertIn(memo["id"][:8], ack_ev["text"])
        # Duplicate ack → 'already', no second ledger entry, no second event.
        n_events = len(t["events"])
        self.assertEqual(ts.memo_ack(t, memo, "acker"), "already")
        self.assertEqual(len(memo["acks"]), 1)
        self.assertEqual(len(t["events"]), n_events)

    def test_ack_decision_promotion_attributes_to_acker(self):
        t = self._seed()
        memo = ts.memo_send(t, "the memo body", from_sid="sender")
        ts.memo_ack(t, memo, "acker", promote=True)               # no text → promote body
        self.assertIn("the memo body", t.get("decisions", []))
        dec_ev = [e for e in t["events"] if e.get("kind") == "decision"][-1]
        self.assertEqual(dec_ev["sid"], "acker")
        # Explicit text overrides the body.
        memo2 = ts.memo_send(t, "another body", from_sid="sender")
        ts.memo_ack(t, memo2, "acker", promote=True, decision_text="curated wording")
        self.assertIn("curated wording", t.get("decisions", []))

    def test_trim_keeps_unacked_and_drops_oldest_acked(self):
        t = self._seed()
        t["sessions"] = ["viewer"]
        # 60 memos from a different session, all acked by the only registered session.
        for i in range(60):
            m = ts.memo_send(t, "acked %d" % i, from_sid="sender")
            ts.memo_ack(t, m, "viewer")
        self.assertLessEqual(len(t["memos"]), ts.MEMOS_KEEP)       # fully-acked trimmed to 50
        # Now flood with unacked memos — none may be trimmed below the hard cap.
        for i in range(60):
            ts.memo_send(t, "unacked %d" % i, from_sid="sender")
        pending = ts.memo_pending(t, "viewer")
        self.assertEqual(len(pending), 60)                         # every unacked survives

    def test_hard_cap_drops_oldest_regardless(self):
        t = self._seed()
        t["sessions"] = ["viewer"]
        for i in range(ts.MEMOS_HARD_CAP + 25):
            ts.memo_send(t, "unacked %d" % i, from_sid="sender")   # all pending for viewer
        self.assertLessEqual(len(t["memos"]), ts.MEMOS_HARD_CAP)

    def test_no_registered_sessions_never_soft_trims(self):
        # No session has attached yet → nobody could have acked → the soft cap
        # (MEMOS_KEEP) must not delete anything, since "fully acked" is undefined.
        t = self._seed()
        t["sessions"] = []
        for i in range(ts.MEMOS_KEEP + 25):
            ts.memo_send(t, "unacked %d" % i, from_sid="sender")
        self.assertEqual(len(t["memos"]), ts.MEMOS_KEEP + 25)      # soft cap no-ops
        # The hard cap still bounds a pathological unattended pile-up.
        for i in range(ts.MEMOS_HARD_CAP + 25):
            ts.memo_send(t, "more %d" % i, from_sid="sender")
        self.assertLessEqual(len(t["memos"]), ts.MEMOS_HARD_CAP)

    def test_memo_by_prefix_resolution(self):
        t = self._seed()
        m1 = ts.memo_send(t, "first", from_sid="s")
        found, err = ts._memo_by_prefix(t, m1["id"][:8])
        self.assertIsNone(err)
        self.assertIs(found, m1)
        _, err2 = ts._memo_by_prefix(t, "zzzzzzzz")
        self.assertIn("no memo", err2)


# ------------------------------------------- Phase 4: pending brief + delivery
class MemoPendingBriefTest(_MemoBase):
    def test_none_on_no_or_legacy_task(self):
        self.assertIsNone(ts.memo_pending_brief(ts.new_task("bare", ""), "me"))
        # A task with memos but none pending for me (I sent them) → None.
        t = self._seed()
        ts.memo_send(t, "mine", from_sid="me")
        self.assertIsNone(ts.memo_pending_brief(t, "me"))

    def test_shows_unacked_with_peer_ledger(self):
        # Two sessions on one task: alice acks, bob still sees it pending WITH alice
        # in the ledger — the visible signal that prevents double-implementing.
        t = self._seed()
        t["seq"] = 42
        memo = ts.memo_send(t, "Use WAL mode because it avoids writer starvation",
                            from_sid="sender")
        ts.memo_ack(t, memo, "alice")
        self.assertIsNone(ts.memo_pending_brief(t, "alice"))       # alice acked → gone
        brief = ts.memo_pending_brief(t, "bob")
        self.assertIsNotNone(brief)
        self.assertIn("awaiting YOUR ack", brief)
        self.assertIn(memo["id"][:8], brief)
        self.assertIn("acked by: %s" % "alice"[:8], brief)         # peer ledger inline
        self.assertIn("/todo memo ack <id8>", brief)                # actionable footer

    def test_resurfaces_after_mark_seen_until_ack(self):
        # Ack-gated persistence: mark_seen (the delta watermark) must NOT clear a memo.
        t = self._seed()
        ts.memo_send(t, "important fact", from_sid="sender")
        self.assertIsNotNone(ts.memo_pending_brief(t, "me"))
        ts.mark_seen(t, "me")                                      # advance delta watermark
        self.assertIsNotNone(ts.memo_pending_brief(t, "me"))       # still pending
        memo = t["memos"][-1]
        ts.memo_ack(t, memo, "me")
        self.assertIsNone(ts.memo_pending_brief(t, "me"))          # only ack clears it

    def test_caps_at_pending_max_with_more_line(self):
        t = self._seed()
        for i in range(5):
            ts.memo_send(t, "fact %d" % i, from_sid="sender")
        brief = ts.memo_pending_brief(t, "me")
        self.assertEqual(brief.count("  • "), ts.MEMO_PENDING_MAX)
        self.assertIn("(+2 more pending)", brief)                  # 5 - 3
        # Newest-last: the newest (fact 4) shows, the oldest (fact 0) is folded away.
        self.assertIn("fact 4", brief)
        self.assertNotIn("fact 0", brief)

    def test_long_body_truncates_but_ack_ledger_survives(self):
        # A long pasted body must never chop off the ack ledger — that's the
        # anti-double-implement signal, and it's the primary real-world use case.
        t = self._seed()
        big = "word " * 200                                        # far exceeds MEMO_LINE_MAX
        memo = ts.memo_send(t, big, from_sid="sender")
        ts.memo_ack(t, memo, "alice")
        ts.memo_ack(t, memo, "bob")
        brief = ts.memo_pending_brief(t, "carol")
        self.assertIn("acked by: %s, %s" % ("alice"[:8], "bob"[:8]), brief)
        self.assertIn("…", brief)                                   # body itself was truncated


# --------------------------------------------------- Phase 5: CLI + rendering
class MemoCliTest(_MemoBase):
    def test_send_happy_prints_confirmation(self):
        t = self._seed("Target task")
        out = self._out(ts.cmd_memo, _Args(sub="send", task=str(t["seq"]),
                                           text="a durable fact", session="me"))
        r = ts.load_task(t["id"])
        memo = r["memos"][-1]
        self.assertIn("memo %s" % memo["id"][:8], out)
        self.assertIn("task #%s" % t["seq"], out)
        self.assertIn("Target task", out)

    def test_send_bad_ref_one_error_line_no_crash(self):
        out = self._out(ts.cmd_memo, _Args(sub="send", task="9999", text="x", session="me"))
        self.assertIn("no task matching", out)

    def test_ack_defaults_to_attached_task(self):
        # M1: an ack now carries a disposition (here --noop with its mandatory reason);
        # the point of THIS test is that --task defaults to the attached task, and that
        # a repeat ack is idempotent.
        t = self._seed()
        ts.memo_send(t, "fact", from_sid="sender")
        ts.save_task(t)
        ts.set_link("acker", t["id"])                              # attached → default --task
        memo = ts.load_task(t["id"])["memos"][-1]
        out = self._out(ts.cmd_memo, _Args(sub="ack", task=None, id=memo["id"][:8],
                                           session="acker", noop="already knew this"))
        self.assertIn("acked by", out)
        r = ts.load_task(t["id"])
        self.assertEqual([a["sid"] for a in r["memos"][-1]["acks"]], ["acker"])
        # Idempotent: a second ack reports "already".
        out2 = self._out(ts.cmd_memo, _Args(sub="ack", task=None, id=memo["id"][:8],
                                            session="acker", noop="already knew this"))
        self.assertIn("already", out2)

    def test_ack_ambiguous_prefix_errors_with_candidates(self):
        t = self._seed()
        # Force two memos whose ids share a leading char so a 1-char prefix is ambiguous.
        m1 = ts.memo_send(t, "one", from_sid="s")
        m2 = ts.memo_send(t, "two", from_sid="s")
        m2["id"] = m1["id"][0] + m2["id"][1:]                      # same first char
        ts.save_task(t)
        out = self._out(ts.cmd_memo, _Args(sub="ack", task=str(t["seq"]),
                                           id=m1["id"][0], session="acker"))
        self.assertIn("ambiguous", out)

    def test_ack_decision_promotion_via_cli(self):
        t = self._seed()
        ts.memo_send(t, "promote me", from_sid="sender")
        ts.save_task(t)
        memo = ts.load_task(t["id"])["memos"][-1]
        self._out(ts.cmd_memo, _Args(sub="ack", task=str(t["seq"]), id=memo["id"][:8],
                                     session="acker", decision=True))
        self.assertIn("promote me", ts.load_task(t["id"]).get("decisions", []))

    def test_show_list_and_full_body(self):
        t = self._seed()
        memo = ts.memo_send(t, "the full body text here", from_sid="sender")
        ts.save_task(t)
        listing = self._out(ts.cmd_memo, _Args(sub="show", task=str(t["seq"]), id=None))
        self.assertIn(memo["id"][:8], listing)
        full = self._out(ts.cmd_memo, _Args(sub="show", task=str(t["seq"]), id=memo["id"][:8]))
        self.assertIn("the full body text here", full)

    def test_show_no_memos(self):
        t = self._seed()
        out = self._out(ts.cmd_memo, _Args(sub="show", task=str(t["seq"]), id=None))
        self.assertIn("(no memos)", out)


class MemoRenderTest(_MemoBase):
    def test_detail_memos_section_flags_unacked_for_viewer(self):
        t = self._seed("Detail task")
        ts.memo_send(t, "unacked fact", from_sid="sender")
        m2 = ts.memo_send(t, "acked fact", from_sid="sender")
        ts.memo_ack(t, m2, "viewer")
        ts.save_task(t)
        detail = ts._format_detail(t, "viewer", attached=False)
        self.assertIn("Memos:", detail)
        self.assertIn("unacked fact", detail)
        self.assertIn("⚑ unacked by you", detail)

    def test_history_shows_full_memo_ledger(self):
        t = self._seed("History task")
        t["seq"] = 9
        memo = ts.memo_send(t, "historic memo", from_sid="sender")
        ts.memo_ack(t, memo, "alice")
        ts.memo_ack(t, memo, "bob")
        hist = ts._format_history(t)
        self.assertIn("Memos", hist)
        self.assertIn("historic memo", hist)
        self.assertIn("alice"[:8], hist)
        self.assertIn("bob"[:8], hist)


if __name__ == "__main__":
    unittest.main()
