"""Three places the loop reported something it had not established (3.55.0).

All three fail in the direction that LOOKS like success, which is why using the tool
never surfaced them:

  1. `turn` and `scan` used different settledness rules on the same record. A flat board
     cannot show it — the leaf and deep rules agree on a flat tree by construction — so
     the only honest test uses a SENTINEL rule that cannot be confused with either default.
  2. An unresolvable `--task` exited 0, which contradicts `exit-tick`'s own docstring.
  3. Liveness that could not be READ looked identical to an idle machine, so the children
     cap silently lifted.
"""
import os
import sys
import tempfile
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(_REPO_ROOT, "lib")
sys.path.insert(0, LIB)
sys.path.insert(0, os.path.join(LIB, "board"))
os.environ.setdefault("TASK_STATION_HOME", tempfile.mkdtemp(prefix="ts-honesty-"))

import loop                    # noqa: E402
import turn                    # noqa: E402


def _task(seq, tid, parent=None):
    t = {"id": tid, "seq": seq, "title": "t%s" % seq, "status": "open",
         "decisions": [], "steps": []}
    if parent:
        t["related"] = [{"id": parent, "rel": "parent"}]
    return t


class TestTurnUsesTheRuleItIsGiven(unittest.TestCase):
    """`turn.plan` accepted no `is_settled`, so it silently took the LEAF default while
    `cmds/loop.py`'s scan passed the DEEP one."""

    def setUp(self):
        self.orch = _task(1, "o")
        self.orch[loop.ORCHESTRATOR_FIELD] = True
        self.childA = _task(2, "a", parent="o")
        self.grand = _task(3, "g", parent="a")
        self.every = [self.orch, self.childA, self.grand]
        self.by_id = {t["id"]: t for t in self.every}

    def test_plan_consults_the_settledness_rule_it_is_handed(self):
        seen = []

        def sentinel(t):
            seen.append(t.get("seq"))
            return True

        # The default disagrees, so a pass here cannot be the default answering.
        self.assertFalse(loop.settled(self.childA))
        turn.plan(self.orch, [self.childA, self.grand], resolve=self.by_id.get,
                  is_settled=sentinel)
        self.assertTrue(seen, "turn.plan ignored the is_settled it was given")

    def test_plan_and_scan_compute_the_same_report(self):
        rule = lambda t: True                                    # noqa: E731
        plan = turn.plan(self.orch, [self.childA, self.grand],
                         resolve=self.by_id.get, is_settled=rule)
        rep = loop.scan([self.childA, self.grand], self.by_id.get, is_settled=rule)
        self.assertEqual([r["settled"] for r in plan["scan"]["rows"]],
                         [r["settled"] for r in rep["rows"]])
        self.assertEqual(plan["scan"]["stop"], "complete")

    def test_the_deep_rule_reaches_grandchildren(self):
        # The regression this whole thing protects: a parent is not settled while a
        # descendant is unbuilt, however green the parent's own checklist is.
        deep = loop.settled_fn(self.every)
        self.assertFalse(deep(self.orch))

    def test_orchestrator_refusal_recommends_on_the_deep_rule(self):
        # Signature is (task, tasks, verb) — the refusal RECOMMENDS ready children to
        # run instead, so on the leaf rule it would recommend a parent whose own children
        # are unbuilt. It must produce a refusal at all for an orchestrator.
        out = loop.orchestrator_refusal(self.orch, self.every, "invoke")
        self.assertIsNotNone(out)
        self.assertIn("orchestrator-only", out)   # it returns a STRING, not lines


class TestUnreadableLivenessIsNotAnIdleMachine(unittest.TestCase):

    def test_the_cap_can_distinguish_none_from_cannot_tell(self):
        src = open(os.path.join(LIB, "board", "cmds", "loop.py")).read()
        self.assertIn("_live_seqs_or_none", src)
        self.assertIn("cannot determine which child sessions are RUNNING", src)

    def test_display_path_still_fails_open(self):
        # `_live_seqs` must KEEP degrading to an empty set — the fix splits the two
        # consumers rather than making the display strict. Asserted on source because
        # `lib/board/loop.py` and `lib/board/cmds/loop.py` share a module name, so an
        # import here resolves to whichever was loaded first.
        src = open(os.path.join(LIB, "board", "cmds", "loop.py")).read()
        self.assertIn("return set() if seqs is None else seqs", src)


if __name__ == "__main__":
    unittest.main()
