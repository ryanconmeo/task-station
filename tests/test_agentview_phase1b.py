"""Phase 1b (#463) display surfaces: hub-ordinal + worker-ledger rendering across
hud, board, brief-provenance derivation, and the history view. Every surface is
DATA-GATED — it appears only when ordinals/ledger are present — so this locks in
both the rendered-when-present behavior and the absent-when-not behavior."""
import importlib.util
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "lib")
TOOLS = os.path.join(ROOT, "tools")
for p in (LIB, TOOLS):
    if p not in sys.path:
        sys.path.insert(0, p)

import hud            # noqa: E402
import render_board   # noqa: E402


def _load_engine():
    spec = importlib.util.spec_from_file_location(
        "ts_engine_p1b", os.path.join(LIB, "task-station.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class HudOrdinalTest(unittest.TestCase):
    def test_segment_renders_ordinal_when_given(self):
        seg = hud._task_segment({"seq": 9, "title": "T"}, ordinal=2)
        self.assertIn("#9-2", seg)

    def test_segment_bare_seq_when_no_ordinal(self):
        seg = hud._task_segment({"seq": 9, "title": "T"})
        self.assertIn("#9", seg)
        self.assertNotIn("#9-", seg)

    def test_header_threads_ordinal_from_hub_session_meta(self):
        task = {"seq": 9, "title": "T",
                "session_meta": {"s1": {"role": "hub", "ordinal": 3}}}
        payload = {"model": {"display_name": "Opus"}, "session_id": "s1"}
        self.assertIn("#9-3", hud._header(payload, task))

    def test_header_bare_seq_for_worker_session(self):
        task = {"seq": 9, "title": "T",
                "session_meta": {"w1": {"role": "worker", "name": "wk"}}}
        payload = {"model": {"display_name": "Opus"}, "session_id": "w1"}
        out = hud._header(payload, task)
        self.assertIn("#9", out)
        self.assertNotIn("#9-", out)


class BoardHubCardOrdinalTest(unittest.TestCase):
    def test_card_renders_ordinal_span(self):
        card = {"sid8": "a1b2c3d4", "ordinal": 2, "role": "hub", "agg": {}}
        html = render_board._hub_card(card, [0.01, 0.05], 9)
        self.assertIn('class="hcord"', html)
        self.assertIn("#9-2", html)

    def test_card_no_ordinal_span_when_absent(self):
        card = {"sid8": "a1b2c3d4", "ordinal": None, "role": "hub", "agg": {}}
        html = render_board._hub_card(card, [0.01, 0.05], 9)
        self.assertNotIn("hcord", html)
        self.assertIn("a1b2c3d4", html)


class EngineProvenanceDerivationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-p1b-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        self.ts = _load_engine()

    def _task(self):
        t = self.ts.new_task("t", ""); t["seq"] = 9
        self.ts.touch(t, session="hub-A")                       # hub ordinal 0
        self.ts.register_worker_session(t, "wk-1", name="task-station-9-projectname",
                                        model="sonnet", status="ok")
        self.ts.add_ledger(t, "spawn", worker_sid="wk-1", actor_sid="hub-A",
                           detail="projectname:fix-99")
        return t

    def test_sessions_rows_hub_first_then_worker(self):
        rows = self.ts._brief_provenance_sessions(self._task())
        self.assertEqual(rows[0]["kind"], "hub")
        self.assertEqual(rows[0]["ordinal"], "9-0")
        wk = [r for r in rows if r["kind"] == "worker"][0]
        self.assertEqual(wk["name"], "task-station-9-projectname")
        self.assertEqual(wk["model"], "sonnet")
        self.assertEqual(wk["status"], "ok")
        self.assertEqual(wk["ordinal"], "")                      # workers never numbered

    def test_ledger_tail_resolves_actor_and_worker(self):
        tail = self.ts._brief_provenance_ledger(self._task())
        self.assertEqual(len(tail), 1)
        self.assertEqual(tail[0]["actor"], "9-0")
        self.assertEqual(tail[0]["action"], "spawn")
        self.assertEqual(tail[0]["worker"], "task-station-9-projectname")
        self.assertEqual(tail[0]["detail"], "projectname:fix-99")

    def test_empty_when_no_data(self):
        bare = self.ts.new_task("b", "")
        self.assertEqual(self.ts._brief_provenance_sessions(bare), [])
        self.assertEqual(self.ts._brief_provenance_ledger(bare), [])

    def test_history_view_includes_ledger_tail(self):
        out = self.ts._format_history(self._task())
        self.assertIn("Workers (1 interaction(s)", out)
        self.assertIn("9-0 spawn", out)
        self.assertIn("task-station-9-projectname", out)

    def test_history_view_omits_workers_when_no_ledger(self):
        bare = self.ts.new_task("b", ""); bare["seq"] = 5
        self.assertNotIn("Workers (", self.ts._format_history(bare))


if __name__ == "__main__":
    unittest.main()
