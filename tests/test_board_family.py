"""THE BOARD'S FAMILY LAYOUT — children nest under their parent, and each relation
kind says what it actually is.

WHY THIS EXISTS, measured on one real board. The table sorted purely by activity, so an
orchestrator and its six tracks scattered: task 444 sat BETWEEN two of its own children,
with two unrelated tasks interleaved among the rest. The only signal that any of them
belonged together was a chip reading `↳ from #444` — and that chip was worse than
useless, because it printed the same four words for EVERY outgoing edge kind. One track
rendered `↳ from #533, #444` where #533 merely GATED it and #444 OWNED it. A third task
read `↳ from #535` and looked like a child when it was a dependent.

So two things are under test here, and they are separable:

  1. ORDER — a family renders as a block, and it sits WHERE ITS MOST RECENT MEMBER WOULD
     HAVE SAT. Recency still drives the board; it drives it a family at a time. The
     incoming list is already activity-sorted, so a member's index IS its recency and no
     date is ever parsed — which is what stops the two orderings from disagreeing.
  2. LABELS — `N children` · `parent #N` · `waits on #N`, each its own word. Anything the
     table does not know about keeps the old generic `from #N`, so a store written by a
     newer version still renders.

THE THREE REFUSALS, each one a way a layout pass takes a whole board down rather than
one row: never follow a parent edge into a cycle, never reach outside the section being
laid out, and never move a task that has no family.
"""
import importlib.util
import io
import os
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(_ROOT, "lib")
sys.path.insert(0, LIB)
sys.path.insert(0, os.path.join(_ROOT, "tools"))

import render_board as rb      # noqa: E402
import loop                    # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


def _task(tid, seq, status="open", related=(), conditions=None):
    """A hand-built task blob — pure, no store needed (mirrors test_loop.py's `_t`)."""
    steps = []
    for n in sorted((conditions or {})):
        verdict = conditions[n]
        block = {"cmd": "check-%s" % n, "expect": ["OK"]}
        if verdict is not None:
            block["last"] = {"ts": 1000.0, "ok": verdict == "met", "status": "ran",
                             "missing": [] if verdict == "met" else ["OK"], "got": ""}
        steps.append({"text": "step %d" % n, "done": False, "exit": block})
    return {"id": tid, "seq": seq, "title": "T", "status": status,
            "related": list(related), "steps": steps}


def _dep_vm(seq, deps):
    """A board-row stub carrying just the `related.from` entries `_row_related_chip`
    reads, each free to carry `settled`/`status` alongside the usual `seq`/`kind`/`id`."""
    return {"seq": seq, "title": "T", "status": "open",
            "related": {"from": list(deps), "in": []}}


def vm(seq, title="T", out=(), inn=()):
    """A board view-model stub carrying only what the layout reads: a seq and the
    relation edges. `out` is what this task STORES (the subordinate side holds the
    edge); `inn` is what points at it."""
    return {"seq": seq, "title": title, "status": "open",
            "related": {"from": [{"seq": s, "kind": k} for s, k in out],
                        "in": [{"seq": s, "kind": k, "status": "open"} for s, k in inn]}}


def laid(rows):
    """`[(seq, depth, last, flat)…]` — the layout, flattened for assertion."""
    return [(t["seq"], tr["depth"], tr["last"], tr["flat"])
            for t, tr in rb._family_layout(rows)]


def order(rows):
    return [seq for seq, _d, _l, _f in laid(rows)]


class LayoutTest(unittest.TestCase):
    def test_a_child_follows_its_parent_and_indents(self):
        rows = [vm(1, out=[(2, "parent")]), vm(2, inn=[(1, "parent")])]
        self.assertEqual(laid(rows), [(2, 0, True, 1), (1, 1, True, 0)])

    def test_the_family_sits_where_its_most_recent_member_would_have(self):
        """The rule that keeps the flat sort's whole point. A parent touched an hour ago
        whose child moved a minute ago rides to the top WITH that child, as one unit —
        rather than the child leaping up alone and the parent staying behind."""
        rows = [vm(9, "unrelated but recent"),
                vm(1, "child, very recent", out=[(2, "parent")]),
                vm(8, "unrelated, older"),
                vm(2, "parent, older", inn=[(1, "parent")])]
        # #1 is index 1, so the family outranks #8 (index 2) and trails #9 (index 0).
        self.assertEqual(order(rows), [9, 2, 1, 8])

    def test_children_are_ordered_by_their_own_recency(self):
        rows = [vm(10, inn=[(1, "parent"), (2, "parent")]),
                vm(2, out=[(10, "parent")]),
                vm(1, out=[(10, "parent")])]
        self.assertEqual(order(rows), [10, 2, 1])

    def test_the_last_child_is_marked_so_the_connector_can_close_the_run(self):
        rows = [vm(10, inn=[(1, "parent"), (2, "parent")]),
                vm(1, out=[(10, "parent")]),
                vm(2, out=[(10, "parent")])]
        marks = {seq: last for seq, _d, last, _f in laid(rows)}
        self.assertFalse(marks[1])
        self.assertTrue(marks[2])

    def test_a_grandchild_nests_two_deep(self):
        rows = [vm(10, inn=[(20, "parent")]),
                vm(20, out=[(10, "parent")], inn=[(30, "parent")]),
                vm(30, out=[(20, "parent")])]
        self.assertEqual(laid(rows), [(10, 0, True, 0), (20, 1, True, 1), (30, 2, True, 2)])

    def test_a_task_with_no_family_keeps_its_place(self):
        rows = [vm(10, inn=[(11, "parent")]), vm(11, out=[(10, "parent")]), vm(99)]
        self.assertEqual(order(rows), [10, 11, 99])

    def test_a_parent_outside_this_section_leaves_the_child_a_root(self):
        """A closed parent must not be dragged into the open table — that would be a
        bigger lie than the scattering this fixes. The child renders as a root and keeps
        its `parent #N` chip, which is exactly what the chip is for."""
        rows = [vm(11, out=[(10, "parent")]), vm(99)]
        self.assertEqual(laid(rows), [(11, 0, True, 0), (99, 0, True, 1)])
        self.assertIn("parent", rb._row_related_chip(rows[0]))

    def test_a_cycle_is_survived_rather_than_recursed_into(self):
        """A `parent` edge is single-valued and replaces, so a cycle should be
        impossible — but "should be impossible" is not a rendering guarantee, and an
        infinite recursion here takes the WHOLE board down, not one row."""
        rows = [vm(1, out=[(2, "parent")], inn=[(2, "parent")]),
                vm(2, out=[(1, "parent")], inn=[(1, "parent")])]
        got = order(rows)
        self.assertEqual(sorted(got), [1, 2])
        self.assertEqual(len(got), 2)

    def test_a_self_parent_edge_is_ignored(self):
        rows = [vm(1, out=[(1, "parent")], inn=[(1, "parent")])]
        self.assertEqual(laid(rows), [(1, 0, True, 0)])

    def test_a_depends_on_edge_never_nests(self):
        """The exact bug the old chip hid: a dependent looked like a child. It gates the
        wave, it does not belong to the parent."""
        rows = [vm(5, inn=[(6, "depends-on")]), vm(6, out=[(5, "depends-on")])]
        self.assertEqual(laid(rows), [(5, 0, True, 0), (6, 0, True, 1)])

    def test_every_row_is_emitted_exactly_once(self):
        rows = [vm(1, out=[(2, "parent")]), vm(2, inn=[(1, "parent")], out=[(3, "parent")]),
                vm(3, inn=[(2, "parent")]), vm(4), vm(5, out=[(2, "parent")])]
        got = order(rows)
        self.assertEqual(sorted(got), [1, 2, 3, 4, 5])


class ConnectorTest(unittest.TestCase):
    def _prefix(self, rows, seq):
        for t, tr in rb._family_layout(rows):
            if t["seq"] == seq:
                html = rb._tree_prefix(tr)
                m = re.search(r'>(.*?)</span>', html)
                return m.group(1) if m else ""
        raise AssertionError("no row %s" % seq)

    def test_depth_zero_draws_no_connector(self):
        self.assertEqual(self._prefix([vm(1)], 1), "")

    def test_a_middle_child_opens_the_run_and_the_last_closes_it(self):
        rows = [vm(10, inn=[(1, "parent"), (2, "parent")]),
                vm(1, out=[(10, "parent")]), vm(2, out=[(10, "parent")])]
        self.assertEqual(self._prefix(rows, 1), "├─ ")
        self.assertEqual(self._prefix(rows, 2), "└─ ")

    def test_a_grandchild_draws_a_bar_through_an_ancestor_that_has_more_siblings(self):
        """The connector column has to know, at every ancestor level, whether that
        ancestor's run is still open — otherwise a deep tree renders as ragged indent
        with no visible spine."""
        rows = [vm(10, inn=[(1, "parent"), (2, "parent")]),
                vm(1, out=[(10, "parent")], inn=[(3, "parent")]),
                vm(2, out=[(10, "parent")]),
                vm(3, out=[(1, "parent")])]
        self.assertEqual(self._prefix(rows, 3), "│  └─ ")

    def test_the_bar_becomes_blank_once_the_ancestor_run_has_closed(self):
        rows = [vm(10, inn=[(1, "parent"), (2, "parent")]),
                vm(1, out=[(10, "parent")]),
                vm(2, out=[(10, "parent")], inn=[(3, "parent")]),
                vm(3, out=[(2, "parent")])]
        self.assertEqual(self._prefix(rows, 3), "   └─ ")


class ChipTest(unittest.TestCase):
    def _text(self, t):
        return re.sub(r"<[^>]+>", "", rb._row_related_chip(t))

    def test_a_parent_reports_that_it_has_children(self):
        """The half that was entirely invisible: the old chip read only the OUTGOING
        side, so a parent with six children advertised nothing at all."""
        t = vm(444, inn=[(s, "parent") for s in (531, 532, 533, 534, 535, 536)])
        self.assertIn("6 children", self._text(t))

    def test_a_short_child_run_is_listed_rather_than_counted(self):
        t = vm(10, inn=[(1, "parent"), (2, "parent")])
        self.assertIn("children #1, #2", self._text(t))

    def test_a_long_child_run_collapses_but_keeps_every_seq_in_the_tooltip(self):
        t = vm(10, inn=[(n, "parent") for n in (1, 2, 3, 4, 5)])
        html = rb._row_related_chip(t)
        self.assertIn("5 children", self._text(t))
        self.assertIn("#1, #2, #3, #4, #5", html)

    def test_parent_and_depends_on_no_longer_read_identically(self):
        """The whole bug in one assertion. `↳ from #533, #444` used to be the entire
        rendering of two completely different relationships."""
        t = vm(532, out=[(533, "depends-on"), (444, "parent")])
        text = self._text(t)
        self.assertIn("parent #444", text)
        self.assertIn("waits on #533", text)
        self.assertNotIn("from #533", text)

    def test_an_unknown_kind_keeps_the_generic_marker(self):
        """A store written by a newer version must still render — mislabelled or
        crashing are both worse than generic."""
        t = vm(1, out=[(2, "spawned-from"), (3, "some-future-kind")])
        text = self._text(t)
        self.assertIn("from", text)
        self.assertIn("#2", text)
        self.assertIn("#3", text)

    def test_no_edges_renders_nothing(self):
        self.assertEqual(rb._row_related_chip(vm(1)), "")

    def test_the_parent_chip_is_rendered_even_while_nesting_hides_it(self):
        """One render serves both orderings: the chip is always emitted and CSS-hidden
        in family order, so the flat toggle reveals it with no re-render."""
        self.assertIn("relparent", rb._row_related_chip(vm(1, out=[(2, "parent")])))


class SettledRelatedTest(unittest.TestCase):
    """The HTML board's `_board_related` must agree with the scan about which
    depends-on edges still block — see loop.settled_fn. A CLOSED gate and a merely
    SETTLED one (open, but every exit condition met) both read `settled=True`; only
    `status` tells them apart."""

    def test_board_related_out_side_carries_settled(self):
        closed_gate = _task("p", 1, status="closed")
        open_but_met = _task("o", 2, conditions={1: "met"})
        dependent = _task("d", 3, related=[
            {"id": "p", "seq": 1, "kind": "depends-on"},
            {"id": "o", "seq": 2, "kind": "depends-on"},
        ])
        tasks = [closed_gate, open_but_met, dependent]
        is_settled = loop.settled_fn(tasks)

        rel = ts._board_related(dependent, tasks=tasks, is_settled=is_settled)
        by_seq = {e["seq"]: e for e in rel["from"]}
        self.assertTrue(by_seq[1]["settled"])
        self.assertEqual(by_seq[1]["status"], "closed")
        # settled but NOT closed — the whole point of carrying both keys separately.
        self.assertTrue(by_seq[2]["settled"])
        self.assertEqual(by_seq[2]["status"], "open")

    def test_orchestrator_blocker_with_unbuilt_children_not_settled(self):
        """The 3.4.0 rule, reused rather than re-derived: an orchestrator whose own
        checklist is green is NOT settled while it still has an unbuilt child."""
        orch = _task("p", 1, conditions={1: "met"})       # its OWN checklist is green
        unbuilt_kid = _task("k", 2, related=[{"id": "p", "seq": 1, "kind": "parent"}])
        dependent = _task("d", 3, related=[{"id": "p", "seq": 1, "kind": "depends-on"}])
        tasks = [orch, unbuilt_kid, dependent]
        is_settled = loop.settled_fn(tasks)

        self.assertTrue(loop.settled(orch))                # the LEAF rule alone says yes
        rel = ts._board_related(dependent, tasks=tasks, is_settled=is_settled)
        self.assertFalse(rel["from"][0]["settled"])         # …and the board must not agree


class WaitsChipSettledTest(unittest.TestCase):
    """The board's `waits on` chip must stop claiming a SETTLED dependency still
    blocks — the surface divergence this task closes."""

    def test_waits_chip_omits_settled_dependency(self):
        mixed = _dep_vm(9, [
            {"seq": 5, "kind": "depends-on", "id": "a", "settled": True, "status": "open"},
            {"seq": 6, "kind": "depends-on", "id": "b", "settled": False, "status": "open"},
        ])
        chip = rb._row_related_chip(mixed)
        waits = re.search(r'<span class="relchip relwaits"[^>]*>(.*?)</span>', chip)
        self.assertIsNotNone(waits)
        self.assertIn("#6", waits.group(1))
        self.assertNotIn("#5", waits.group(1))          # the settled one makes no claim

        all_settled = _dep_vm(9, [
            {"seq": 5, "kind": "depends-on", "id": "a", "settled": True, "status": "open"},
        ])
        chip2 = rb._row_related_chip(all_settled)
        self.assertNotIn("relwaits", chip2)              # nothing left to wait on at all
        self.assertNotIn("waits on", chip2)

    def test_waits_chip_marks_settled_gate_positively(self):
        t = _dep_vm(9, [
            {"seq": 5, "kind": "depends-on", "id": "a", "settled": True, "status": "open"},
            {"seq": 6, "kind": "depends-on", "id": "b", "settled": True, "status": "closed"},
        ])
        chip = rb._row_related_chip(t)
        self.assertNotIn("relwaits", chip)
        gate = re.search(r'<span class="relchip relgates"[^>]*>(.*?)</span>', chip)
        self.assertIsNotNone(gate)
        gate_text = re.sub(r"<[^>]+>", "", gate.group(1))     # visible text, tags stripped
        self.assertIn("#5", gate_text)
        self.assertIn("#6 ✕", gate_text)                      # closed keeps the CLI's ✕ mark
        self.assertIn('title="gates met: #5, #6 ✕"', chip)


class RenderedBoardTest(unittest.TestCase):
    """The end-to-end board: the layout reaches the real HTML, and the toggle that
    switches it back is wired."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="board-family-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.DELEGATE_REGISTRY = os.path.join(self.tmp, "workers.json")
        ts.store.reset_cache()

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        ts.store.reset_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _board(self):
        parent = ts.new_task("The orchestrator", "s")
        ts.save_task(parent)
        child = ts.new_task("The track", "s")
        ts.save_task(child)
        ts.ensure_seqs()
        parent, child = ts.load_task(parent["id"]), ts.load_task(child["id"])
        ts.append_related(child, parent, "parent")
        ts.save_task(child)
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_board(type("A", (), {"open": False, "refresh_if_live": False})())
        with open(os.path.join(self.tmp, "board.html")) as fh:
            return fh.read(), parent, child

    def test_the_child_row_carries_its_depth_and_both_orderings(self):
        html, _p, child = self._board()
        row = re.search(r'id="task-%s"[^>]*data-nest="(\d+)" data-flat="(\d+)" '
                        r'data-depth="(\d+)"' % child["seq"], html)
        self.assertIsNotNone(row, "the child row carries no ordering attributes")
        self.assertEqual(row.group(3), "1")

    def test_the_child_row_draws_a_connector(self):
        html, _p, child = self._board()
        seg = html[html.find('id="task-%s"' % child["seq"]):][:900]
        self.assertIn('class="treeline"', seg)

    def test_the_parent_row_advertises_its_children(self):
        html, parent, _c = self._board()
        seg = html[html.find('id="task-%s"' % parent["seq"]):][:900]
        self.assertIn("relkids", seg)

    def test_the_flat_toggle_is_present_and_persists(self):
        html, _p, _c = self._board()
        self.assertIn('id="nest-toggle"', html)
        self.assertIn("group families", html)
        self.assertIn(rb._NEST_KEY, html)

    def test_the_board_stays_self_contained(self):
        """The board's standing law: inline script/style only, never an external asset.
        A layout change must not quietly reach for a web font to draw its connectors."""
        html, _p, _c = self._board()
        for needle in ('src="http', "src='http", '<link ', '@import', 'url(http'):
            self.assertNotIn(needle, html)


if __name__ == "__main__":
    unittest.main()
