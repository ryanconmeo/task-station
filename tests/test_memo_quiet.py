"""Memo-ack quieting — the awaiting-YOUR-ack nag stops re-listing SETTLED memos.

WHAT WENT WRONG. `memo_pending` is ack-gated PER SESSION, and that is right for the
ledger: a memo this session never acked is, factually, unacked by this session, forever.
It is wrong for the per-prompt NAG. On a long-lived task memos arrive faster than any one
session acks them, so a session that opens on day nine inherits every memo it personally
never saw — a real session opened to 22 of them, every single one already dispositioned by
peer hub sessions. A block that lists two dozen items nobody needs to act on is not a
signal; worse, it buries the one memo that DOES need this session.

THE RULE UNDER TEST. A memo is SETTLED once either

  (a) ANY session acked it with a DECISION or MEMORY disposition — a durable store took
      the fact, and durable stores are shared; or
  (b) >= `memo_quiet_after` DISTINCT sessions dispositioned it at all, noop included
      (default 3). One noop is one session's judgement; three independent ones are the
      room's.

Retro-filled dispositions count in both limbs. A BARE ack (no disposition) counts toward
neither — it records only that a session saw the memo, which is the exact shape the
disposition requirement exists to stop treating as an integration.

WHAT SETTLED DOES **NOT** MEAN, and most of this file is proof of it. A settled memo
leaves ONE surface: the awaiting-your-ack prompt nag. It stays in `memo show`, in the
detail view's "Memos:" section, in every count those render, and it stays ackable. This is
a quieter nag, never a shorter record — so the nag says so out loud when it hides
anything, and names the surface that lists them all.

Isolation copies the `_repoint` idiom from tests/test_memo.py. The engine patch surface is
deliberately DATA / STORE / TASKS_DIR / LINKS_DIR only (all already routed) — see
tests/test_patch_surface.py, which fails if this file patches anything else on `ts`.
"""
import argparse
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

_TMP_HOME = tempfile.mkdtemp(prefix="ts-memo-quiet-")
os.environ["TASK_STATION_HOME"] = _TMP_HOME

import board.memos as memos       # noqa: E402  (the predicate, unit-tested directly)
import config as ts_config        # noqa: E402
import heal                       # noqa: E402  (retro_disposition)
import store                      # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)

_ENV_KEYS = ("TASK_STATION_MEMO_QUIET", "TASK_STATION_MEMO_QUIET_AFTER")
_CFG_KEYS = ("memo_quiet", "memo_quiet_after")


class _Args:
    def __init__(self, **kw):
        defaults = dict(task=None, text=None, id=None, session=None, sub=None,
                        decision=None, memory=None, noop=None, corrects=None)
        defaults.update(kw)
        self.__dict__.update(defaults)


class _CfgArgs:
    """cmd_config reads every flag through getattr-with-default, so a call only needs
    the flags it is exercising (plus workspace_dirs, which the board render reads)."""
    def __init__(self, **kw):
        self.__dict__.update(dict(workspace_dirs=None, **kw))


def _repoint(tmp):
    os.environ["TASK_STATION_HOME"] = tmp
    ts.DATA = tmp
    ts.STORE = os.path.join(tmp, "store")
    ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
    ts.LINKS_DIR = os.path.join(ts.STORE, "links")
    store.reset_cache()


def _ack(memo, sid, kind, value="why", retro=False):
    """Append a dispositioned ack straight onto the ledger — the stored shape, without a
    task or an event, so the predicate can be tested as the pure function it is."""
    disp = (heal.retro_disposition(kind, value, sid="healer") if retro
            else {"kind": kind, "value": value})
    memo.setdefault("acks", []).append({"sid": sid, "ts": 1.0, "disposition": disp})
    return memo


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="memo-quiet-")
        _repoint(self.tmp)

    def tearDown(self):
        store.reset_cache()
        for k in _ENV_KEYS:
            os.environ.pop(k, None)
        for k in _CFG_KEYS:
            ts_config.unset(k)
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- fixtures ---------------------------------------------------------------

    def _seed(self, title="A task"):
        t = ts.new_task(title, "summary")
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _memo(self, task, text="a durable fact", **kw):
        m = ts.memo_send(task, text, from_sid="sender", **kw)
        ts.save_task(task)
        return m

    def _dispose(self, task, memo, sid, kind, value="because", retro=False):
        """A peer session's ack, recorded through the real mutator so the ledger, the
        event and the disposition all land exactly as production writes them."""
        disp = (heal.retro_disposition(kind, value, sid="healer") if retro
                else {"kind": kind, "value": value})
        ts.memo_ack(task, memo, sid, promote=(kind == "decision"),
                    decision_text=(value if kind == "decision" else None),
                    disposition=disp)
        ts.save_task(task)

    def _out(self, fn, args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            fn(args)
        return buf.getvalue()


# ---------------------------------------------------------------------------
# (a) the predicate — pure, reads the ledger exactly as stored
# ---------------------------------------------------------------------------

class TestSettledPredicate(unittest.TestCase):
    def _memo(self):
        return {"id": "a" * 32, "ts": 1.0, "from_sid": "sender", "text": "x", "acks": []}

    def test_a_fresh_memo_is_not_settled(self):
        self.assertFalse(ts.memo_settled(self._memo()))

    def test_a_single_decision_ack_settles_it(self):
        self.assertTrue(ts.memo_settled(_ack(self._memo(), "alice", "decision")))

    def test_a_single_memory_ack_settles_it(self):
        self.assertTrue(ts.memo_settled(_ack(self._memo(), "alice", "memory", "a-note")))

    def test_three_distinct_noops_settle_it_and_two_do_not(self):
        m = _ack(_ack(self._memo(), "alice", "noop"), "bob", "noop")
        self.assertFalse(ts.memo_settled(m))            # two sessions — not the room
        _ack(m, "carol", "noop")
        self.assertTrue(ts.memo_settled(m))

    def test_the_same_session_twice_is_still_one_session(self):
        """The quorum counts DISTINCT sessions. `memo_ack` is idempotent per sid, but a
        duplicate reaching the ledger by any other path must not inflate the count."""
        m = self._memo()
        for _ in range(4):
            _ack(m, "alice", "noop")
        self.assertFalse(ts.memo_settled(m))

    def test_a_bare_ack_settles_nothing(self):
        """An ack with no disposition says a session SAW it and nothing about what it did
        — the exact shape the disposition requirement was added to stop counting as an
        integration. `heal --dispose-acks` is how one becomes a settling ack."""
        m = self._memo()
        for sid in ("alice", "bob", "carol", "dave"):
            m["acks"].append({"sid": sid, "ts": 1.0})
        self.assertFalse(ts.memo_settled(m))

    def test_retro_dispositions_count_in_both_limbs(self):
        """A retro disposition is a later reconcile pass's guess, which is why every
        ledger surface tags it "(retro)". The question here is only whether ANOTHER
        session still needs nagging, and the memo stays fully visible either way."""
        self.assertTrue(ts.memo_settled(
            _ack(self._memo(), "alice", "memory", "a-note", retro=True)))
        m = self._memo()
        for sid in ("alice", "bob", "carol"):
            _ack(m, sid, "noop", retro=True)
        self.assertTrue(ts.memo_settled(m))

    def test_the_quorum_is_the_after_argument(self):
        m = _ack(_ack(self._memo(), "alice", "noop"), "bob", "noop")
        self.assertTrue(ts.memo_settled(m, after=2))
        self.assertFalse(ts.memo_settled(m, after=4))

    def test_a_non_positive_or_unparseable_quorum_falls_back(self):
        """A zero would settle every memo on sight — that is the off switch, not a
        threshold, and the positive-only contract refuses it everywhere else."""
        m = _ack(_ack(self._memo(), "alice", "noop"), "bob", "noop")
        for bad in (0, -5, "nonsense", None):
            self.assertFalse(ts.memo_settled(m, after=bad))
        self.assertTrue(ts.memo_settled(_ack(m, "carol", "noop"), after=0))

    def test_a_malformed_ledger_does_not_raise(self):
        self.assertFalse(ts.memo_settled({}))
        self.assertFalse(ts.memo_settled({"acks": None}))
        self.assertFalse(ts.memo_settled({"acks": [{"ts": 1.0}]}))          # no sid
        self.assertFalse(ts.memo_settled({"acks": [{"sid": "a", "disposition": {}}]}))

    def test_the_shipped_quorum_is_three(self):
        self.assertEqual(memos.MEMO_QUIET_AFTER, 3)
        self.assertEqual(ts.MEMO_QUIET_AFTER, 3)


# ---------------------------------------------------------------------------
# (b) the nag — the ONE surface that quiets
# ---------------------------------------------------------------------------

class TestTheNag(_Base):
    def test_an_unsettled_memo_still_nags(self):
        t = self._seed()
        m = self._memo(t)
        nag = ts.memo_pending_brief(ts.load_task(t["id"]), "viewer")
        self.assertIn("awaiting YOUR ack", nag)
        self.assertIn(m["id"][:8], nag)

    def test_a_peers_decision_ack_quiets_it_for_a_fresh_session(self):
        t = self._seed()
        m = self._memo(t)
        self._dispose(t, m, "alice", "decision", "we do it this way")
        self.assertIsNone(ts.memo_pending_brief(ts.load_task(t["id"]), "viewer"))

    def test_a_peers_memory_ack_quiets_it_for_a_fresh_session(self):
        t = self._seed()
        m = self._memo(t)
        self._dispose(t, m, "alice", "memory", "some-note-slug")
        self.assertIsNone(ts.memo_pending_brief(ts.load_task(t["id"]), "viewer"))

    def test_three_distinct_noops_quiet_it_and_two_do_not(self):
        t = self._seed()
        m = self._memo(t)
        self._dispose(t, m, "alice", "noop", "already true")
        self._dispose(t, m, "bob", "noop", "already true")
        self.assertIsNotNone(ts.memo_pending_brief(ts.load_task(t["id"]), "viewer"))
        self._dispose(t, m, "carol", "noop", "already true")
        self.assertIsNone(ts.memo_pending_brief(ts.load_task(t["id"]), "viewer"))

    def test_the_dispositioning_sessions_never_saw_it_anyway(self):
        """Quieting changes nothing for the sessions that acked — an ack has always
        removed a memo from that session's own pending list. This is only about the
        sessions that did NOT act."""
        t = self._seed()
        m = self._memo(t)
        self._dispose(t, m, "alice", "noop", "n/a")
        loaded = ts.load_task(t["id"])
        self.assertIsNone(ts.memo_pending_brief(loaded, "alice"))
        self.assertEqual(ts.memo_pending(loaded, "alice"), [])
        self.assertIsNotNone(ts.memo_pending_brief(loaded, "viewer"))    # not yet settled

    def test_the_sender_is_still_excluded(self):
        """The sender's own exclusion predates this and is untouched by it — settled or
        not, you are never nagged about your own memo."""
        t = self._seed()
        m = self._memo(t)
        self.assertIsNone(ts.memo_pending_brief(ts.load_task(t["id"]), "sender"))
        self._dispose(t, m, "alice", "decision", "yes")
        self.assertIsNone(ts.memo_pending_brief(ts.load_task(t["id"]), "sender"))

    def test_a_declared_correction_quiets_by_the_same_rule(self):
        t = self._seed()
        m = self._memo(t, "the fix", corrects=["some-note-name"])
        self.assertIn("CORRECTS: some-note-name",
                      ts.memo_pending_brief(ts.load_task(t["id"]), "viewer"))
        self._dispose(t, m, "alice", "memory", "some-note-name")
        self.assertIsNone(ts.memo_pending_brief(ts.load_task(t["id"]), "viewer"))

    def test_a_memo_that_reads_as_a_correction_quiets_by_the_same_rule(self):
        t = self._seed()
        m = self._memo(t, "the earlier guidance is withdrawn")
        self.assertIn("reads as a correction",
                      ts.memo_pending_brief(ts.load_task(t["id"]), "viewer"))
        for sid in ("alice", "bob", "carol"):
            self._dispose(t, m, sid, "noop", "the note already says so")
        self.assertIsNone(ts.memo_pending_brief(ts.load_task(t["id"]), "viewer"))

    def test_the_quieted_clause_renders_only_when_something_was_quieted(self):
        t = self._seed()
        loud = self._memo(t, "still needs you")
        nag = ts.memo_pending_brief(ts.load_task(t["id"]), "viewer")
        self.assertNotIn("quieted", nag)                       # nothing hidden → no clause
        settled = self._memo(t, "already handled")
        self._dispose(t, settled, "alice", "decision", "handled")
        nag = ts.memo_pending_brief(ts.load_task(t["id"]), "viewer")
        self.assertIn("1 memo(s) awaiting YOUR ack", nag)      # the SHOWN count
        self.assertIn("(1 settled memo(s) quieted — memo show lists all)", nag)
        self.assertIn(loud["id"][:8], nag)
        self.assertNotIn(settled["id"][:8], nag)

    def test_the_clause_counts_every_memo_it_hid(self):
        t = self._seed()
        for i in range(4):
            m = self._memo(t, "handled %d" % i)
            self._dispose(t, m, "alice", "memory", "note-%d" % i)
        self._memo(t, "still needs you")
        nag = ts.memo_pending_brief(ts.load_task(t["id"]), "viewer")
        self.assertIn("(4 settled memo(s) quieted", nag)

    def test_a_fully_settled_task_says_nothing_at_all(self):
        """The whole point: silence, not a block reporting how quiet it is."""
        t = self._seed()
        for i in range(3):
            m = self._memo(t, "handled %d" % i)
            self._dispose(t, m, "alice", "decision", "handled %d" % i)
        self.assertIsNone(ts.memo_pending_brief(ts.load_task(t["id"]), "viewer"))

    def test_the_nag_does_not_mutate_the_task(self):
        t = self._seed()
        m = self._memo(t)
        self._dispose(t, m, "alice", "decision", "handled")
        loaded = ts.load_task(t["id"])
        before = json.dumps(loaded, sort_keys=True, default=str)
        ts.memo_pending_brief(loaded, "viewer")
        self.assertEqual(before, json.dumps(loaded, sort_keys=True, default=str))


# ---------------------------------------------------------------------------
# (c) everything else keeps seeing every pending memo
# ---------------------------------------------------------------------------

class TestNothingElseQuiets(_Base):
    def _settled_task(self):
        t = self._seed()
        m = self._memo(t, "a settled fact")
        self._dispose(t, m, "alice", "decision", "handled")
        return ts.load_task(t["id"]), m

    def test_memo_pending_is_unquieted_by_default(self):
        t, m = self._settled_task()
        self.assertEqual([x["id"] for x in ts.memo_pending(t, "viewer")], [m["id"]])
        self.assertEqual(ts.memo_pending(t, "viewer", quiet=True), [])

    def test_memo_show_still_lists_it(self):
        t, m = self._settled_task()
        out = self._out(ts.cmd_memo, _Args(sub="show", task=str(t["seq"]),
                                           session="viewer"))
        self.assertIn(m["id"][:8], out)
        self.assertIn("Memos on task #%s" % t["seq"], out)
        self.assertIn("(1)", out)                       # the roster count is unchanged
        self.assertIn("⚑ unacked by you", out)          # and it is still flagged as mine

    def test_memo_show_id_still_opens_it(self):
        t, m = self._settled_task()
        out = self._out(ts.cmd_memo, _Args(sub="show", task=str(t["seq"]),
                                           id=m["id"][:8], session="viewer"))
        self.assertIn("a settled fact", out)
        self.assertIn("alice", out)                     # the ack ledger, inline

    def test_the_detail_section_still_flags_it(self):
        t, m = self._settled_task()
        lines = "\n".join(ts._memo_detail_lines(t, "viewer"))
        self.assertIn(m["id"][:8], lines)
        self.assertIn("⚑ unacked by you", lines)

    def test_a_settled_memo_is_still_ackable(self):
        t, m = self._settled_task()
        out = self._out(ts.cmd_memo, _Args(sub="ack", task=str(t["seq"]), id=m["id"][:8],
                                           session="viewer", noop="peer already did it"))
        self.assertIn("acked by viewer", out)
        acks = ts.load_task(t["id"])["memos"][-1]["acks"]
        self.assertEqual([a["sid"] for a in acks], ["alice", "viewer"])

    def test_the_trim_still_treats_it_as_pending_for_a_session_that_has_not_acked(self):
        """`_trim_memos` drops only memos pending for NOBODY. Settled is a nag verdict,
        not an ack — a settled-but-unacked memo must still survive the soft trim."""
        t = self._seed()
        t["sessions"] = ["viewer"]
        for i in range(ts.MEMOS_KEEP + 10):
            m = ts.memo_send(t, "settled %d" % i, from_sid="sender")
            ts.memo_ack(t, m, "alice", disposition={"kind": "decision", "value": "x"})
        self.assertEqual(len(t["memos"]), ts.MEMOS_KEEP + 10)


# ---------------------------------------------------------------------------
# (d) the config surface
# ---------------------------------------------------------------------------

class TestConfig(_Base):
    def _settled(self):
        t = self._seed()
        m = self._memo(t, "a settled fact")
        self._dispose(t, m, "alice", "decision", "handled")
        return ts.load_task(t["id"])

    def test_it_is_on_by_default(self):
        self.assertTrue(ts_config.memo_quiet_enabled())
        self.assertEqual(ts_config.memo_quiet_after(), 3)

    def test_off_restores_the_old_behaviour(self):
        t = self._settled()
        ts_config.set("memo_quiet", False)
        nag = ts.memo_pending_brief(t, "viewer")
        self.assertIn("awaiting YOUR ack", nag)
        self.assertNotIn("quieted", nag)                # nothing was hidden to report

    def test_the_env_escape_wins_over_config(self):
        t = self._settled()
        ts_config.set("memo_quiet", True)
        os.environ["TASK_STATION_MEMO_QUIET"] = "off"
        self.assertIsNotNone(ts.memo_pending_brief(t, "viewer"))
        os.environ["TASK_STATION_MEMO_QUIET"] = "on"
        self.assertIsNone(ts.memo_pending_brief(t, "viewer"))

    def test_config_retunes_the_quorum_end_to_end(self):
        t = self._seed()
        m = self._memo(t)
        self._dispose(t, m, "alice", "noop", "n/a")
        self._dispose(t, m, "bob", "noop", "n/a")
        loaded = ts.load_task(t["id"])
        self.assertIsNotNone(ts.memo_pending_brief(loaded, "viewer"))    # 2 < 3
        ts_config.set("memo_quiet_after", 2)
        self.assertIsNone(ts.memo_pending_brief(loaded, "viewer"))

    def test_the_env_escape_retunes_the_quorum_end_to_end(self):
        t = self._seed()
        m = self._memo(t)
        for sid in ("alice", "bob", "carol"):
            self._dispose(t, m, sid, "noop", "n/a")
        loaded = ts.load_task(t["id"])
        os.environ["TASK_STATION_MEMO_QUIET_AFTER"] = "4"
        self.assertIsNotNone(ts.memo_pending_brief(loaded, "viewer"))    # 3 < 4 now
        ts_config.set("memo_quiet_after", 9)
        os.environ["TASK_STATION_MEMO_QUIET_AFTER"] = "2"
        self.assertIsNone(ts.memo_pending_brief(loaded, "viewer"))       # env beats config

    def test_a_zero_or_negative_quorum_is_refused_back_to_the_default(self):
        for bad in (0, -5, "nonsense", ""):
            ts_config.set("memo_quiet_after", bad)
            self.assertEqual(ts_config.memo_quiet_after(), 3)

    def test_a_raising_config_keeps_the_shipped_defaults(self):
        """The usual prompt-rail fail-open direction is "speak"; here speaking IS the
        failure being fixed, so a broken config falls back to the documented defaults
        (quiet, quorum 3) rather than to the 22-item block. Nothing is hidden from the
        record either way, so this direction costs nothing."""
        t = self._settled()
        real = ts_config.memo_quiet_enabled
        ts_config.memo_quiet_enabled = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            self.assertIsNone(ts.memo_pending_brief(t, "viewer"))
        finally:
            ts_config.memo_quiet_enabled = real

    def test_the_keys_are_cleared_by_a_config_reset(self):
        for key in _CFG_KEYS:
            self.assertIn(key, ts_config.RESET_KEYS)

    def test_the_flags_appear_on_the_config_board(self):
        flags = [r[0] for r in ts_config.board_rows()]
        self.assertIn("--memo-quiet", flags)
        self.assertIn("--memo-quiet-after", flags)

    def test_the_board_rows_report_the_current_values(self):
        ts_config.set("memo_quiet", False)
        ts_config.set("memo_quiet_after", 5)
        rows = {r[0]: r for r in ts_config.board_rows()}
        self.assertEqual(rows["--memo-quiet"][1], "off")
        self.assertEqual(rows["--memo-quiet-after"][1], "5")

    def test_the_cli_sets_and_gets_the_toggle(self):
        out = self._out(ts_config.cmd_config, _CfgArgs(memo_quiet="off"))
        self.assertIn("memo_quiet = off", out)
        self.assertFalse(ts_config.memo_quiet_enabled())
        self.assertEqual(self._out(ts_config.cmd_config,
                                   _CfgArgs(memo_quiet_get=True)).strip(), "off")
        self._out(ts_config.cmd_config, _CfgArgs(memo_quiet="on"))
        self.assertTrue(ts_config.memo_quiet_enabled())

    def test_the_cli_sets_and_gets_the_quorum(self):
        out = self._out(ts_config.cmd_config, _CfgArgs(memo_quiet_after="4"))
        self.assertIn("memo_quiet_after = 4", out)
        self.assertEqual(ts_config.memo_quiet_after(), 4)
        self.assertEqual(self._out(ts_config.cmd_config,
                                   _CfgArgs(memo_quiet_after_get=True)).strip(), "4")

    def test_the_cli_refuses_a_junk_quorum_without_storing_it(self):
        out = self._out(ts_config.cmd_config, _CfgArgs(memo_quiet_after="lots"))
        self.assertIn("expected a positive session count", out)
        self.assertEqual(ts_config.memo_quiet_after(), 3)

    def test_the_parser_accepts_both_flags_and_their_get_forms(self):
        p = argparse.ArgumentParser()
        ts._add_config_args(p)
        a = p.parse_args(["--memo-quiet", "off", "--memo-quiet-after", "5"])
        self.assertEqual((a.memo_quiet, a.memo_quiet_after), ("off", "5"))
        b = p.parse_args(["--memo-quiet-get", "--memo-quiet-after-get"])
        self.assertTrue(b.memo_quiet_get and b.memo_quiet_after_get)
        self.assertEqual(p.parse_args(["--memo-quiet"]).memo_quiet, "on")   # bare → on


if __name__ == "__main__":
    unittest.main()
