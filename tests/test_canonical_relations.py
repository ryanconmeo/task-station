"""Stage 1 — canonical relation resolution: ONE relationship per counterpart.

A task↔task relationship is a single thing that the store can record up to three
times — this task's `related` list, the other task's mirror entry, and (because
`append_related` dedups on id+KIND) a second entry on the same side under a second
kind. Every consumer that walked the out edges and then the in edges printed the
same counterpart twice, or three times for a mixed-label pair.

`canonical_relations` is the one resolver the four consumers route through. The key
that makes it work is THE OTHER TASK'S id ALONE: keying on `(other, kind)` would
leave the mixed-label duplicate standing, because those two records differ precisely
in their kind.

These are BEHAVIOURAL tests — every one builds a fixture, runs the real code path,
and asserts on what a reader would see. Nothing here asserts markup presence, and
nothing pins a live-store count: the fixtures are synthetic, so the suite does not
drift when the real store does.

Per-test temp-home isolation (the `_repoint` idiom shared with
tests/test_events_relations.py); never touches live data.
"""
import importlib.util
import os
import shutil
import sys
import tempfile
import unittest

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


class _RelBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-canon-")
        _repoint(self.tmp)

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- fixture helpers -------------------------------------------------------
    def _seed(self, title):
        """Create + persist a task and give it a stable seq; return the reloaded
        blob. (The `_seed` idiom from tests/test_events_relations.py.)"""
        t = ts.new_task(title, "summary")
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _relate(self, task, other, kind):
        """Record `task` --kind--> `other` through the REAL write helper
        (`append_related`, which dedups on id+kind) and persist."""
        ts.append_related(task, other, kind)
        ts.save_task(task)

    def _scan(self, *tasks):
        """Reload the given tasks so each carries its persisted `related` list —
        the task-blob list every consumer takes as its `tasks` argument."""
        return [ts.load_task(t["id"]) for t in tasks]

    def _mixed_pair(self):
        """The live 438/382 shape, with the helpers' own ids (never the real seqs):
        the child recorded the SAME target twice under two kinds, and the origin
        recorded the mirror. THREE stored records, ONE relationship."""
        origin, child = self._seed("Origin task"), self._seed("Child task")
        self._relate(child, origin, "spawned-from")
        self._relate(child, origin, "related")
        self._relate(origin, child, "related")
        # Precondition: the store really does hold the duplicate this fixes.
        self.assertEqual(len(ts.load_task(child["id"])["related"]), 2)
        self.assertEqual(len(ts.load_task(origin["id"])["related"]), 1)
        return origin, child


# ================================================================== the resolver ===
class CanonicalRelationsTest(_RelBase):
    def test_reciprocal_pair_is_one_relationship(self):
        # A stores →B and B stores →A. That is ONE undirected relationship; the
        # out/in split used to present it as two.
        a, b = self._seed("Alpha task"), self._seed("Beta task")
        self._relate(a, b, "related")
        self._relate(b, a, "related")
        scan = self._scan(a, b)

        arels = ts.canonical_relations(scan[0], tasks=scan)
        self.assertEqual(len(arels), 1)
        self.assertEqual(arels[0]["id"], b["id"])
        self.assertEqual(arels[0]["seq"], b["seq"])
        self.assertEqual(arels[0]["kind"], "related")
        # Both ends agree it is one thing (each sees the counterpart exactly once).
        self.assertEqual([r["id"] for r in ts.canonical_relations(scan[1], tasks=scan)],
                         [a["id"]])

    def test_mixed_label_pair_resolves_once_to_the_stronger_kind(self):
        # The defect that forces the key to be the other task ALONE: these two
        # records differ in kind, so an (other, kind) key would keep both.
        origin, child = self._mixed_pair()
        scan = self._scan(origin, child)

        rels = ts.canonical_relations(scan[1], tasks=scan)
        self.assertEqual(len(rels), 1)
        self.assertEqual(rels[0]["id"], origin["id"])
        self.assertEqual(rels[0]["kind"], "spawned-from")   # outranks `related`
        self.assertEqual(rels[0]["dir"], "out")             # …and the child asserted it

        # The origin end collapses its three sightings of the child to one too.
        orels = ts.canonical_relations(scan[0], tasks=scan)
        self.assertEqual(len(orels), 1)
        self.assertEqual(orels[0]["id"], child["id"])
        self.assertEqual(orels[0]["kind"], "spawned-from")
        self.assertEqual(orels[0]["dir"], "in")             # derived from the child

    def test_every_adjacent_kind_pair_resolves_to_the_lower_rank(self):
        # Walk the whole precedence table, strongest → weakest, and prove each
        # neighbour beats the one below it. Recording the WEAKER kind first proves
        # the winner is decided by rank, not by insertion order.
        order = [k for k, _ in sorted(ts._REL_KIND_RANK.items(), key=lambda kv: kv[1])]
        self.assertGreaterEqual(len(order), 2)
        for stronger, weaker in zip(order, order[1:]):
            a, b = self._seed("Source " + stronger), self._seed("Target " + weaker)
            self._relate(a, b, weaker)
            self._relate(a, b, stronger)
            scan = self._scan(a, b)
            rels = ts.canonical_relations(scan[0], tasks=scan)
            msg = "%s should outrank %s" % (stronger, weaker)
            self.assertEqual(len(rels), 1, msg)
            self.assertEqual(rels[0]["kind"], stronger, msg)

    def test_equal_kind_resolves_to_the_side_that_stored_it(self):
        # The direction tie-break: on an equal kind the asserting side wins, so each
        # end of a reciprocal pair reports its own edge as `out`.
        a, b = self._seed("Asserting task"), self._seed("Mirror task")
        self._relate(a, b, "related")
        self._relate(b, a, "related")
        for task in self._scan(a, b):
            rels = ts.canonical_relations(task, tasks=self._scan(a, b))
            self.assertEqual(len(rels), 1)
            self.assertEqual(rels[0]["dir"], "out")

    def test_entry_without_id_survives_on_a_seq_fallback(self):
        # A legacy entry carrying only a seq must still be shown — dedup falls back
        # to a ("seq", n) key rather than collapsing every id-less entry into one.
        a, b = self._seed("Task with a full edge"), self._seed("Edge target")
        a = ts.load_task(a["id"])
        a["related"] = [{"id": b["id"], "seq": b["seq"], "kind": "related"},
                        {"seq": 90001, "kind": "related"},
                        {"seq": 90002, "kind": "related"}]
        ts.save_task(a)
        scan = self._scan(a, b)

        rels = ts.canonical_relations(scan[0], tasks=scan)
        self.assertEqual(len(rels), 3)                      # nothing silently dropped
        by_seq = {r["seq"]: r for r in rels}
        self.assertEqual(sorted(by_seq), sorted([b["seq"], 90001, 90002]))
        self.assertIsNone(by_seq[90001]["id"])
        self.assertEqual(by_seq[b["seq"]]["id"], b["id"])

    def test_self_edge_is_dropped(self):
        a = self._seed("Self-referring task")
        a = ts.load_task(a["id"])
        a["related"] = [{"id": a["id"], "seq": a["seq"], "kind": "related"}]
        ts.save_task(a)
        scan = self._scan(a)
        self.assertEqual(ts.canonical_relations(scan[0], tasks=scan), [])

    def test_future_kind_resolves_and_outranks_related(self):
        # `depends-on` has no writer yet but is already in the table, so adding one
        # later needs no edit here.
        a, b = self._seed("Dependent task"), self._seed("Dependency task")
        self._relate(a, b, "depends-on")
        self._relate(b, a, "related")
        scan = self._scan(a, b)
        rels = ts.canonical_relations(scan[0], tasks=scan)
        self.assertEqual(len(rels), 1)
        self.assertEqual(rels[0]["kind"], "depends-on")

        # A kind NOBODY has defined must not crash either: it sorts last and is
        # still shown, rather than being dropped or raising.
        c = self._seed("Target of an unknown kind")
        a2 = ts.load_task(a["id"])
        a2["related"].append({"id": c["id"], "seq": c["seq"], "kind": "invented-later"})
        ts.save_task(a2)
        scan = self._scan(a, b, c)
        rels = ts.canonical_relations(scan[0], tasks=scan)
        self.assertEqual([r["kind"] for r in rels], ["depends-on", "invented-later"])
        self.assertEqual(ts._rel_kind_rank("invented-later"), ts._REL_KIND_RANK_UNKNOWN)


# ============================================================== the `Related:` line ===
class RelatedLineTest(_RelBase):
    def test_related_line_prints_each_counterpart_once(self):
        # Whole-line equality, not a substring: three stored records used to print
        # `#382` three times, so "is the counterpart named once" is the assertion.
        origin, child = self._mixed_pair()

        self.assertEqual(ts._related_line(ts.load_task(child["id"])),
                         "Related: from #%s (spawned-from)" % origin["seq"])
        # The origin end names the child once too, as the derived reverse edge.
        self.assertEqual(ts._related_line(ts.load_task(origin["id"])),
                         "Related: spawned #%s" % child["seq"])

    def test_reciprocal_related_pair_prints_one_token(self):
        a, b = self._seed("Alpha task"), self._seed("Beta task")
        self._relate(a, b, "related")
        self._relate(b, a, "related")
        line = ts._related_line(ts.load_task(a["id"]))
        self.assertEqual(line, "Related: related #%s" % b["seq"])

    def test_closed_counterpart_keeps_its_mark(self):
        # The existing vocabulary is untouched: a closed target still gets its
        # trailing closed marker. Taken from the module's own constant rather than
        # retyped, so the glyph can never drift between source and test.
        origin, child = self._seed("Origin task"), self._seed("Child task")
        child = ts.load_task(child["id"])
        child["status"] = ts.STATUS_CLOSED
        ts.save_task(child)
        self._relate(ts.load_task(child["id"]), origin, "spawned-from")
        self.assertEqual(ts._related_line(ts.load_task(origin["id"])),
                         "Related: spawned #%s %s" % (child["seq"],
                                                      ts.STATUS_GLYPH_CLOSED))


# ==================================================================== the graph ===
class GraphLineageTest(_RelBase):
    def _lineage(self, graph):
        return [e for e in graph["edges"] if e.get("via") == ["lineage"]]

    def test_graph_collapses_a_mixed_pair_to_one_directed_edge(self):
        origin, child = self._mixed_pair()
        g = ts.build_board_graph(ts.all_tasks())

        lin = self._lineage(g)
        self.assertEqual(len(lin), 1)                       # was two: one per kind
        self.assertEqual(lin[0]["kind"], "spawned-from")
        self.assertEqual(lin[0]["dir"], "a->b")             # the arrow survives
        self.assertEqual((lin[0]["a"], lin[0]["b"]), (child["seq"], origin["seq"]))
        self.assertEqual(sorted(n["seq"] for n in g["nodes"]),
                         sorted([child["seq"], origin["seq"]]))

    def test_graph_collapses_a_reciprocal_related_pair_to_one_undirected_edge(self):
        a, b = self._seed("Alpha task"), self._seed("Beta task")
        self._relate(a, b, "related")
        self._relate(b, a, "related")
        lin = self._lineage(ts.build_board_graph(ts.all_tasks()))
        self.assertEqual(len(lin), 1)
        self.assertEqual(lin[0]["kind"], "related")
        self.assertEqual(lin[0]["dir"], "none")             # undirected stays undirected

    def test_graph_follows_the_id_when_the_stored_seq_disagrees(self):
        # A poisoned entry: its `id` names the origin, its stored `seq` names an
        # unrelated decoy. Only the id is machine-portable — a seq is local to one
        # store — so the edge must land on the origin. This inconsistency cannot
        # occur in the real store yet; it is built deliberately as the regression
        # guard for the seq-poisoning class.
        origin = self._seed("Origin task")
        decoy = self._seed("Decoy task that owns the stale number")
        child = self._seed("Child task")
        self.assertNotEqual(origin["seq"], decoy["seq"])    # guard: a real disagreement
        child = ts.load_task(child["id"])
        child["related"] = [{"id": origin["id"], "seq": decoy["seq"],
                             "kind": "spawned-from"}]
        ts.save_task(child)

        lin = self._lineage(ts.build_board_graph(ts.all_tasks()))
        self.assertEqual(len(lin), 1)
        self.assertEqual((lin[0]["a"], lin[0]["b"]), (child["seq"], origin["seq"]))
        self.assertNotIn(decoy["seq"], (lin[0]["a"], lin[0]["b"]))

    def test_graph_still_uses_a_stored_seq_when_there_is_no_id(self):
        # The one case a stored seq is all there is: a legacy entry with no id.
        origin, child = self._seed("Origin task"), self._seed("Child task")
        child = ts.load_task(child["id"])
        child["related"] = [{"seq": origin["seq"], "kind": "spawned-from"}]
        ts.save_task(child)
        lin = self._lineage(ts.build_board_graph(ts.all_tasks()))
        self.assertEqual(len(lin), 1)
        self.assertEqual((lin[0]["a"], lin[0]["b"]), (child["seq"], origin["seq"]))


# ============================================================== the board card ===
class BoardRelatedTest(_RelBase):
    def _rev_map(self, scan):
        """The reverse index write_board builds once per render."""
        rev_map = {}
        for t in scan:
            st = ts.task_status(t)
            for r in (t.get("related") or []):
                tgt = r.get("id")
                if tgt:
                    rev_map.setdefault(tgt, []).append(
                        {"seq": t.get("seq"), "id": t.get("id"),
                         "kind": r.get("kind"), "status": st})
        return rev_map

    def test_board_related_returns_one_entry_per_counterpart_with_id(self):
        a, b = self._seed("Alpha task"), self._seed("Beta task")
        self._relate(a, b, "related")
        self._relate(b, a, "related")
        scan = self._scan(a, b)

        rel = ts._board_related(scan[0], tasks=scan)
        rows = (rel["from"] or []) + (rel["in"] or [])
        self.assertEqual(len(rows), 1)                      # not once in EACH list
        self.assertEqual(rel["from"], [{"seq": b["seq"], "kind": "related",
                                        "id": b["id"]}])
        self.assertEqual(rel["in"], [])

        # The board's O(1) rev_map fast path agrees with the scanning path.
        self.assertEqual(ts._board_related(scan[0], rev_map=self._rev_map(scan)), rel)

    def test_board_related_carries_id_on_the_derived_side(self):
        origin, child = self._mixed_pair()
        scan = self._scan(origin, child)
        rel = ts._board_related(scan[0], tasks=scan)
        self.assertEqual(rel["from"], [])                   # the child's claim wins
        self.assertEqual(len(rel["in"]), 1)
        self.assertEqual(rel["in"][0]["id"], child["id"])
        self.assertEqual(rel["in"][0]["seq"], child["seq"])
        self.assertEqual(rel["in"][0]["kind"], "spawned-from")
        self.assertEqual(rel["in"][0]["status"], ts.task_status(scan[1]))

    def test_board_related_keeps_every_pre_existing_key(self):
        # Purely additive: `id` joined the dicts, nothing was renamed or removed.
        origin, child = self._seed("Origin task"), self._seed("Child task")
        self._relate(child, origin, "spawned-from")
        scan = self._scan(origin, child)
        self.assertEqual(set(ts._board_related(scan[1], tasks=scan)["from"][0]),
                         {"seq", "kind", "id"})
        self.assertEqual(set(ts._board_related(scan[0], tasks=scan)["in"][0]),
                         {"seq", "kind", "status", "id"})


if __name__ == "__main__":
    unittest.main()
