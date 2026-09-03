"""The rail writes the disposition (3.57.0) — the memo substrate stops leaking.

THE LEAK WAS SPECIFIED, NOT NEGLECTED. Every closed child posts a routine memo on its
parent AND files a pickup pointing at it. The pickup rail works — on this programme's own
record all seven pickups show `delivered_ts` and `taken_ts` within ~20 seconds, so the
parent genuinely was forced to look. But a taken pickup wrote `task["pickups"]` and left
the memo it pointed at unacked forever: two ledgers describing one event, neither able to
close the other. #444 has closed 21 children and carries 93 memos, 40 of them still nagging.

Two facts made it permanent. `pickup_file` has always had a `memo_id` slot and nothing
ever filled it. And `memo_settled` required a durable disposition or a three-session
quorum — so a fact written by a verb ("this child was graded") could never settle a memo,
because quorum is a test for judgement and this is not judgement.
"""
import os
import sys
import tempfile
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(_REPO_ROOT, "lib")
sys.path.insert(0, LIB)
sys.path.insert(0, os.path.join(LIB, "board"))
os.environ.setdefault("TASK_STATION_HOME", tempfile.mkdtemp(prefix="ts-rail-"))

import channel as ch        # noqa: E402
import memos as m           # noqa: E402


def _parent():
    return {"id": "p", "seq": 1, "title": "parent", "status": "open",
            "decisions": [], "steps": [], "memos": [], "pickups": []}


def _child():
    return {"id": "c", "seq": 2, "title": "child", "status": "open",
            "decisions": [], "steps": []}


class TestThePickupCarriesTheMemoId(unittest.TestCase):

    def test_a_filed_pickup_records_the_memo_it_points_at(self):
        p, c = _parent(), _child()
        memo = m.memo_send(p, "CHILD #2 — CLOSED", routine=True)
        row, created = ch.pickup_file(p, c, "CHILD #2 — CLOSED", memo_id=memo["id"])
        self.assertTrue(created)
        self.assertEqual(row["memo_id"], memo["id"])


class TestTakingAPickupDisposesItsMemo(unittest.TestCase):

    def setUp(self):
        self.p, self.c = _parent(), _child()
        self.memo = m.memo_send(self.p, "CHILD #2 — CLOSED", routine=True)
        self.row, _ = ch.pickup_file(self.p, self.c, "CHILD #2 — CLOSED",
                                     memo_id=self.memo["id"])

    def test_before_the_take_the_memo_is_unsettled(self):
        self.assertFalse(m.memo_settled(self.memo))

    def test_taking_settles_it(self):
        ch.pickup_take(self.p, self.row, sid="s2", how=ch.PICKUP_TAKEN)
        self.assertTrue(m.memo_settled(self.memo))

    def test_the_ledger_says_WHAT_was_done_and_by_whom(self):
        ch.pickup_take(self.p, self.row, sid="s2", how=ch.PICKUP_GRADED)
        ack = self.memo["acks"][-1]
        self.assertEqual(ack["disposition"]["kind"], "graded")
        self.assertEqual(ack["disposition"]["by"], "pickup")

    def test_grading_and_parking_settle_it_too(self):
        for how in (ch.PICKUP_GRADED, ch.PICKUP_PARKED):
            p, c = _parent(), _child()
            memo = m.memo_send(p, "CHILD #2", routine=True)
            row, _ = ch.pickup_file(p, c, "CHILD #2", memo_id=memo["id"])
            ch.pickup_take(p, row, sid=None, how=how)
            self.assertTrue(m.memo_settled(memo), how)

    def test_a_missing_memo_never_breaks_the_take(self):
        # The pickup is the load-bearing half; the ack is bookkeeping.
        p, c = _parent(), _child()
        row, _ = ch.pickup_file(p, c, "CHILD #2", memo_id="nosuchmemo")
        status, err = ch.pickup_take(p, row, sid="s2", how=ch.PICKUP_TAKEN)
        self.assertEqual((status, err), ("taken", None))


class TestLimbC(unittest.TestCase):
    """A fact written by a verb settles alone; a judgement still needs quorum."""

    def test_a_verb_disposition_settles_without_quorum(self):
        memo = {"id": "m", "acks": [{"sid": "rail",
                                     "disposition": {"kind": "graded", "by": "pickup"}}]}
        self.assertTrue(m.memo_settled(memo))

    def test_a_single_noop_still_does_not_settle(self):
        # This is why 51 of 93 memos were immortal: one hub's bulk noop sweep satisfied
        # neither limb, and noop is a judgement, so quorum remains the right test.
        memo = {"id": "m", "acks": [{"sid": "s1", "disposition": {"kind": "noop"}}]}
        self.assertFalse(m.memo_settled(memo))

    def test_a_durable_disposition_still_settles_alone(self):
        memo = {"id": "m", "acks": [{"sid": "s1", "disposition": {"kind": "decision"}}]}
        self.assertTrue(m.memo_settled(memo))


if __name__ == "__main__":
    unittest.main()


class TestRoutineNoticesSettleOnOneDisposition(unittest.TestCase):
    """Limb (d), 3.59.0. Quorum is a test for JUDGEMENT ABOUT A DISPUTED CLAIM, and a
    machine-minted "child #591 closed" is not a claim anyone disputes.

    Measured on #444 before the change: 51 memos were noop-only and stuck, and EXACTLY 40
    of them were routine — which was the nag count. The other 11 are correspondence and
    keep needing quorum, because disagreeing with those is the entire point of the test.
    """

    def _routine(self, **kw):
        memo = {"id": "m", "routine": True, "acks": []}
        memo.update(kw)
        return memo

    def test_one_noop_settles_a_routine_notice(self):
        memo = self._routine(acks=[{"sid": "s1", "disposition": {"kind": "noop"}}])
        self.assertTrue(m.memo_settled(memo))

    def test_one_noop_does_NOT_settle_correspondence(self):
        memo = {"id": "m", "acks": [{"sid": "s1", "disposition": {"kind": "noop"}}]}
        self.assertFalse(m.memo_settled(memo))

    def test_an_unacked_routine_notice_is_still_unsettled(self):
        # Limb (d) settles on a DISPOSITION, not on being routine. A notice nobody has
        # touched has had nothing done with it.
        self.assertFalse(m.memo_settled(self._routine()))

    def test_a_bare_ack_with_no_disposition_does_not_settle_it_either(self):
        memo = self._routine(acks=[{"sid": "s1"}])
        self.assertFalse(m.memo_settled(memo))
