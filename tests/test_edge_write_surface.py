"""Stage 3 — the typed-edge WRITE surface.

The typed-edge model names four task↔task kinds plus a close transition, and until now
the CLI could write none of them: `update --relate` was hard-coded to `related`, plus
automatic `spawned-from`. This adds `--depends-on` / `--parent` / `--absorbed-by` /
`--replaces` / `--duplicates` / `--unrelate`.

THE UNIFYING RULE the whole surface rests on: the SUBORDINATE side stores the edge —
the dependent, the child, the absorbed task — so every flag is a single-task write and
every reverse direction is derived. The one exception is `--replaces`, which closes its
TARGET; that asymmetry with `--absorbed-by` (which closes the task being updated) is the
whole reason both verbs exist, and several tests below exist only to pin it.

Purely additive: `--relate` and every stored `related` edge are untouched, because live
edges still need their writer until a separate migration converts them.

Behavioural throughout — each test drives the real `cmd_update` and asserts on the stored
blob and the printed output. Per-test temp-home isolation (the `_repoint` idiom from
tests/test_events_relations.py, which is also where the `append_related` fixtures live).
Never touches live data.
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
    """A stand-in argparse.Namespace covering every attribute cmd_update reads (all
    defaulting to the parser's default) so a test only sets what it means. Mirrors
    tests/test_events_relations.py's, extended with the typed-edge flags."""
    def __init__(self, **kw):
        defaults = dict(
            session=None, title=None, summary=None, color=None, effort=None,
            task=None, append_summary=None, state=None, step_add=None, step_done=None,
            step_undone=None, decision=None, log=None, relate=None, pr=None, pr_desc=None,
            story=None, story_desc=None,
            # Stage 3: the typed-edge write surface. Repeatable ones default to None
            # (argparse `action="append"`); the two single-valued ones likewise.
            depends_on=None, parent=None, absorbed_by=None, replaces=None,
            duplicates=None, unrelate=None,
        )
        defaults.update(kw)
        self.__dict__.update(defaults)


class _EdgeBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-edges-")
        _repoint(self.tmp)

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- fixtures --------------------------------------------------------------
    def _seed(self, title):
        """Create + persist a task and give it a stable seq; return the reloaded blob."""
        t = ts.new_task(title, "summary")
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _update(self, task, **flags):
        """Run the REAL cmd_update on `task` with the given flags; return its stdout."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_update(_Args(task=str(task["seq"]), **flags))
        return buf.getvalue()

    def _kinds(self, task):
        """{other-seq: sorted kinds} for every edge STORED on `task`."""
        out = {}
        for r in (ts.load_task(task["id"]).get("related") or []):
            out.setdefault(r.get("seq"), []).append(r.get("kind"))
        return {k: sorted(v) for k, v in out.items()}

    def _status(self, task):
        return ts.task_status(ts.load_task(task["id"]))


# ================================================================ storing edges ===
class EdgeStorageTest(_EdgeBase):
    def test_each_flag_stores_its_kind_on_the_subordinate_task(self):
        # One rule, five flags: the subordinate side stores the edge. So every kind
        # here lands on the task being UPDATED, never on the target.
        for flag, kind in (("depends_on", "depends-on"), ("parent", "parent"),
                           ("replaces", "replaces"), ("duplicates", "duplicates"),
                           ("absorbed_by", "absorbed-by")):
            a = self._seed("Subject for " + kind)
            b = self._seed("Target for " + kind)
            self._update(a, **{flag: (b["seq"] if flag in ("parent", "absorbed_by")
                                      else [str(b["seq"])])})
            self.assertEqual(self._kinds(a), {b["seq"]: [kind]},
                             "%s must store %s on the updated task" % (flag, kind))
            self.assertEqual(ts.load_task(b["id"]).get("related") or [], [],
                             "%s must not write on the target" % flag)

    def test_depends_on_is_repeatable_and_idempotent(self):
        a = self._seed("Dependent task")
        b, c = self._seed("First blocker"), self._seed("Second blocker")
        self._update(a, depends_on=[str(b["seq"]), str(c["seq"])])
        self.assertEqual(self._kinds(a),
                         {b["seq"]: ["depends-on"], c["seq"]: ["depends-on"]})
        out = self._update(a, depends_on=[str(b["seq"])])       # re-run
        self.assertIn("nothing to change", out)
        self.assertEqual(len(ts.load_task(a["id"])["related"]), 2)   # no duplicate

    def test_relate_and_existing_related_edges_are_untouched(self):
        # Purely additive: --relate still writes `related`, and a typed write beside it
        # neither converts nor disturbs it.
        a, b = self._seed("Alpha task"), self._seed("Beta task")
        self._update(a, relate=[str(b["seq"])])
        self._update(a, depends_on=[str(b["seq"])])
        self.assertEqual(self._kinds(a), {b["seq"]: ["depends-on", "related"]})


# ===================================================================== --parent ===
class ParentCardinalityTest(_EdgeBase):
    def test_second_parent_replaces_the_first_and_names_it(self):
        # Never a silent swap: a task under two parents double-counts in every roll-up,
        # so the replacement is the behaviour AND the report.
        child = self._seed("Child task")
        first, second = self._seed("First parent"), self._seed("Second parent")
        self._update(child, parent=first["seq"])
        out = self._update(child, parent=second["seq"])
        self.assertIn("parent #%s" % second["seq"], out)
        self.assertIn("REPLACED #%s" % first["seq"], out)

    def test_only_one_parent_is_ever_stored(self):
        child = self._seed("Child task")
        first, second = self._seed("First parent"), self._seed("Second parent")
        self._update(child, parent=first["seq"])
        self._update(child, parent=second["seq"])
        stored = [r for r in ts.load_task(child["id"])["related"]
                  if r.get("kind") == "parent"]
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["seq"], second["seq"])

    def test_new_parent_with_an_authored_state_gets_the_derived_notice(self):
        # A task with children has a COMPUTED state, which authors cannot write — so the
        # write says now that the hand-written line will be replaced, not merged.
        child = self._seed("Child task")
        parent = self._seed("Parent task")
        parent = ts.load_task(parent["id"])
        parent["state"] = "NEXT: hand-written by a human"
        ts.save_task(parent)
        out = self._update(child, parent=parent["seq"])
        self.assertIn("#%s now has children" % parent["seq"], out)
        self.assertIn("DERIVED", out)
        self.assertIn("replaced, not merged", out)

    def test_parent_without_an_authored_state_stays_quiet(self):
        child, parent = self._seed("Child task"), self._seed("Parent task")
        self.assertNotIn("now has children", self._update(child, parent=parent["seq"]))


# ================================================== --absorbed-by vs --replaces ===
class AbsorbAndReplaceTest(_EdgeBase):
    def test_absorbed_by_stores_the_edge_and_closes_the_updated_task(self):
        gone, survivor = self._seed("Task going away"), self._seed("Surviving task")
        self._update(gone, absorbed_by=survivor["seq"])
        self.assertEqual(self._kinds(gone), {survivor["seq"]: ["absorbed-by"]})
        self.assertEqual(self._status(gone), ts.STATUS_CLOSED)
        self.assertEqual(self._status(survivor), ts.STATUS_OPEN)   # survivor untouched
        self.assertIsNotNone(ts.load_task(gone["id"]).get("closed_ts"))

    def test_absorbed_by_prints_the_reconcile_handoff_naming_the_survivor(self):
        # Required output, not decoration: absorbing INHERITS work, so a survivor whose
        # checklist is the blind union of two plans describes work nobody intends to do.
        gone, survivor = self._seed("Task going away"), self._seed("Surviving task")
        out = self._update(gone, absorbed_by=survivor["seq"])
        self.assertIn("task CLOSED", out)
        self.assertIn("RECONCILE NEEDED on #%s" % survivor["seq"], out)
        self.assertIn("task-station heal --task %s" % survivor["seq"], out)

    def test_absorbed_by_does_not_move_the_absorbed_task_s_children(self):
        gone, survivor = self._seed("Task going away"), self._seed("Surviving task")
        kid = self._seed("Child of the absorbed task")
        self._update(kid, parent=gone["seq"])
        out = self._update(gone, absorbed_by=survivor["seq"])
        # The child still points at the ABSORBED task — naming it is the handoff's job;
        # reparenting it is a decision, not a mechanical move.
        self.assertEqual(self._kinds(kid), {gone["seq"]: ["parent"]})
        self.assertEqual(ts.load_task(survivor["id"]).get("related") or [], [])
        self.assertIn("#%s" % kid["seq"], out)
        self.assertIn("NOT moved", out)

    def test_absorbed_by_may_point_at_an_already_closed_task(self):
        gone, survivor = self._seed("Task going away"), self._seed("Surviving task")
        survivor = ts.load_task(survivor["id"])
        survivor["status"] = ts.STATUS_CLOSED
        ts.save_task(survivor)
        self._update(gone, absorbed_by=survivor["seq"])          # legal, not an error
        self.assertEqual(self._kinds(gone), {survivor["seq"]: ["absorbed-by"]})
        self.assertEqual(self._status(gone), ts.STATUS_CLOSED)

    def test_replaces_closes_the_target_not_the_updated_task(self):
        # The direction flip: --absorbed-by closes THIS task, --replaces closes the OTHER.
        winner, loser = self._seed("Replacement task"), self._seed("Replaced task")
        out = self._update(winner, replaces=[str(loser["seq"])])
        self.assertEqual(self._status(loser), ts.STATUS_CLOSED)
        self.assertNotEqual(self._status(winner), ts.STATUS_CLOSED)
        self.assertEqual(self._kinds(winner), {loser["seq"]: ["replaces"]})
        self.assertEqual(ts.load_task(loser["id"]).get("related") or [], [])
        self.assertIn("#%s CLOSED" % loser["seq"], out)

    def test_replaces_prints_no_reconcile_notice(self):
        # The asymmetry that is the whole reason both verbs exist: replacing says the
        # approach was dropped, so nothing was inherited and nothing needs recalculating.
        winner, loser = self._seed("Replacement task"), self._seed("Replaced task")
        out = self._update(winner, replaces=[str(loser["seq"])])
        self.assertNotIn("RECONCILE", out.upper())
        self.assertNotIn("heal --task", out)


# ================================================================= --duplicates ===
class DuplicatesTest(_EdgeBase):
    def test_duplicates_is_stored_once_and_the_reverse_derives(self):
        a, b = self._seed("Alpha task"), self._seed("Beta task")
        self._update(a, duplicates=[str(b["seq"])])
        self.assertEqual(self._kinds(a), {b["seq"]: ["duplicates"]})
        self.assertEqual(ts.load_task(b["id"]).get("related") or [], [])   # stored once
        # …and the other end sees it, derived like every inverse.
        scan = [ts.load_task(a["id"]), ts.load_task(b["id"])]
        rels = ts.canonical_relations(scan[1], tasks=scan)
        self.assertEqual([(r["seq"], r["kind"], r["dir"]) for r in rels],
                         [(a["seq"], "duplicates", "in")])
        # Symmetric: it closes nothing and limits nothing.
        self.assertEqual(self._status(a), ts.STATUS_OPEN)
        self.assertEqual(self._status(b), ts.STATUS_OPEN)


# =================================================================== --unrelate ===
class UnrelateTest(_EdgeBase):
    def test_unrelate_removes_every_kind_for_that_ref_and_reports_them(self):
        # An edge states PRESENT STRUCTURE, not a historical belief, so unlike a
        # decision it is corrected rather than superseded.
        a, b, c = self._seed("Alpha"), self._seed("Beta"), self._seed("Gamma")
        self._update(a, depends_on=[str(b["seq"])], relate=[str(b["seq"])])
        self._update(a, duplicates=[str(c["seq"])])              # a bystander edge
        self.assertEqual(sorted(self._kinds(a)[b["seq"]]), ["depends-on", "related"])
        out = self._update(a, unrelate=[str(b["seq"])])
        self.assertIn("unrelated #%s" % b["seq"], out)
        self.assertIn("depends-on, related", out)
        self.assertEqual(self._kinds(a), {c["seq"]: ["duplicates"]})   # bystander kept

    def test_unrelate_on_a_non_edge_reports_plainly(self):
        a, b = self._seed("Alpha"), self._seed("Beta")
        out = self._update(a, unrelate=[str(b["seq"])])
        self.assertIn("no edge to #%s" % b["seq"], out)
        self.assertEqual(self._kinds(a), {})

    def test_unrelate_never_touches_the_other_task_s_own_edges(self):
        # It cannot reach the DERIVED reverse direction and must not try — that edge
        # belongs to the task that stored it.
        a, b = self._seed("Alpha"), self._seed("Beta")
        self._update(b, depends_on=[str(a["seq"])])              # stored on B
        self._update(a, unrelate=[str(b["seq"])])
        self.assertEqual(self._kinds(b), {a["seq"]: ["depends-on"]})


# ====================================================================== cycles ===
class CycleTest(_EdgeBase):
    def test_depends_on_cycle_warns_and_still_stores(self):
        # Precedent: the 600-char decision advisory always stores, because a refusal
        # makes the author drop a fact or fake two entries out of one. Refusing here
        # would not remove the dependency, only stop it being written down.
        a, b = self._seed("Alpha"), self._seed("Beta")
        self._update(b, depends_on=[str(a["seq"])])              # B → A
        out = self._update(a, depends_on=[str(b["seq"])])        # A → B closes the loop
        self.assertIn("depends-on cycle", out)
        self.assertIn("#%s → #%s → #%s" % (a["seq"], b["seq"], a["seq"]), out)
        self.assertIn("Stored anyway", out)
        self.assertEqual(self._kinds(a), {b["seq"]: ["depends-on"]})   # …and it IS stored

    def test_parent_cycle_stores(self):
        # A parent cycle is allowed to exist, which is exactly why every walker of the
        # parent chain must be cycle-safe by construction.
        a, b = self._seed("Alpha"), self._seed("Beta")
        self._update(b, parent=a["seq"])
        out = self._update(a, parent=b["seq"])
        self.assertIn("parent cycle", out)
        self.assertEqual(self._kinds(a), {b["seq"]: ["parent"]})

    def test_a_longer_chain_is_reported_in_full(self):
        a, b, c = self._seed("Alpha"), self._seed("Beta"), self._seed("Gamma")
        self._update(b, depends_on=[str(c["seq"])])              # B → C
        self._update(c, depends_on=[str(a["seq"])])              # C → A
        out = self._update(a, depends_on=[str(b["seq"])])        # A → B → C → A
        self.assertIn("#%s → #%s → #%s → #%s"
                      % (a["seq"], b["seq"], c["seq"], a["seq"]), out)

    def test_no_cycle_no_warning(self):
        a, b = self._seed("Alpha"), self._seed("Beta")
        self.assertNotIn("cycle", self._update(a, depends_on=[str(b["seq"])]))

    def test_the_cycle_walker_terminates_over_an_existing_cycle(self):
        # The `seen` set is load-bearing, not an optimisation: the store is allowed to
        # already contain a parent cycle, and a naive walk over one would not return.
        a, b = self._seed("Alpha"), self._seed("Beta")
        self._update(b, parent=a["seq"])
        self._update(a, parent=b["seq"])                         # the store now cycles
        c = self._seed("Gamma")
        self._update(c, parent=a["seq"])                         # must simply return


# ================================================================= self-edges ===
class SelfEdgeTest(_EdgeBase):
    def test_every_new_flag_refuses_a_self_edge(self):
        # Meaningless, not a judgement call — `_add_undirected` already drops a == b.
        for flag, cli in (("depends_on", "--depends-on"), ("parent", "--parent"),
                          ("absorbed_by", "--absorbed-by"), ("replaces", "--replaces"),
                          ("duplicates", "--duplicates")):
            t = self._seed("Solo task for " + cli)
            val = t["seq"] if flag in ("parent", "absorbed_by") else [str(t["seq"])]
            out = self._update(t, **{flag: val})
            self.assertIn("can't point %s at itself" % cli, out, cli)
            self.assertEqual(self._kinds(t), {}, cli)
            # …and --absorbed-by in particular must not have closed it on the way out.
            self.assertNotEqual(self._status(t), ts.STATUS_CLOSED, cli)


# ============================================================ ownership rule ===
class OwnershipRuleTest(_EdgeBase):
    def test_depends_on_and_parent_reject_a_foreign_looking_handle(self):
        # `related` may name a foreign task; these two may not, because both are
        # COMPUTED OVER and compute requires freshness.
        t = self._seed("Local task")
        for flag in ("depends_on", "parent"):
            val = "someone-7" if flag == "parent" else ["someone-7"]
            out = self._update(t, **{flag: val})
            self.assertIn("LOCAL tasks only", out, flag)
            self.assertEqual(self._kinds(t), {}, flag)

    def test_an_ordinary_bad_ref_still_reads_as_no_such_task(self):
        t = self._seed("Local task")
        self.assertIn("no such task", self._update(t, depends_on=["99999"]))

    def test_the_symmetric_kinds_do_not_apply_the_rule(self):
        # Only depends-on / parent are local-only; a foreign-looking ref on the others
        # falls through to the ordinary no-such-task path, not the ownership message.
        t = self._seed("Local task")
        out = self._update(t, duplicates=["someone-7"])
        self.assertIn("no such task", out)
        self.assertNotIn("LOCAL tasks only", out)


# =============================================== rank table + the Related: line ===
class RankAndRenderTest(_EdgeBase):
    def test_every_new_kind_is_ranked_and_outranks_the_vaguer_ones(self):
        for kind in ("depends-on", "parent", "absorbed-by", "replaces",
                     "duplicates", "spawned-from", "related"):
            self.assertIn(kind, ts._REL_KIND_RANK, kind)
        rank = ts._REL_KIND_RANK
        # `duplicates` and `replaces` sit ABOVE spawned-from: spawned-from is a
        # historical fact about origin, they are claims about what the pair IS now.
        self.assertLess(rank["replaces"], rank["spawned-from"])
        self.assertLess(rank["duplicates"], rank["spawned-from"])
        # …and a settled verdict outranks a merely-noticed collision.
        self.assertLess(rank["absorbed-by"], rank["replaces"])
        self.assertLess(rank["replaces"], rank["duplicates"])
        self.assertLess(rank["spawned-from"], rank["related"])   # `related` stays weakest

    def _line(self, subject, target, flag, val=None):
        """Store one typed edge and return both ends' `Related:` lines."""
        self._update(subject, **{flag: (val if val is not None else [str(target["seq"])])})
        return (ts._related_line(ts.load_task(subject["id"])),
                ts._related_line(ts.load_task(target["id"])))

    def test_related_line_renders_each_kind_and_its_derived_inverse(self):
        # depends-on and duplicates close nothing, so both ends read exactly.
        for flag, stored_fmt, derived_fmt in (
                ("depends_on", "depends on #%s", "blocks #%s"),
                ("duplicates", "duplicates #%s", "duplicates #%s")):
            s, t = self._seed("Subject " + flag), self._seed("Target " + flag)
            mine, theirs = self._line(s, t, flag)
            self.assertEqual(mine, "Related: " + stored_fmt % t["seq"], flag)
            self.assertEqual(theirs, "Related: " + derived_fmt % s["seq"], flag)

    def test_related_line_renders_replaces_and_replaced_by(self):
        # --replaces CLOSES its target, so the replacement's own line carries the
        # closed mark on the task it replaced — while the replaced task's view of the
        # (still open) replacement does not.
        s, t = self._seed("Replacement task"), self._seed("Replaced task")
        mine, theirs = self._line(s, t, "replaces")
        self.assertEqual(mine, "Related: replaces #%s %s"
                         % (t["seq"], ts.STATUS_GLYPH_CLOSED))
        self.assertEqual(theirs, "Related: replaced by #%s" % s["seq"])

    def test_related_line_renders_parent_and_children(self):
        child, parent = self._seed("Child task"), self._seed("Parent task")
        mine, theirs = self._line(child, parent, "parent", parent["seq"])
        self.assertEqual(mine, "Related: parent #%s" % parent["seq"])
        self.assertEqual(theirs, "Related: children #%s" % child["seq"])

    def test_related_line_renders_absorbed_by_and_absorbed(self):
        gone, survivor = self._seed("Absorbed task"), self._seed("Surviving task")
        mine, theirs = self._line(gone, survivor, "absorbed_by", survivor["seq"])
        self.assertEqual(mine, "Related: absorbed-by #%s" % survivor["seq"])
        # the absorbed task closed, so the survivor's view carries the closed mark
        self.assertEqual(theirs, "Related: absorbed #%s %s"
                         % (gone["seq"], ts.STATUS_GLYPH_CLOSED))

    def test_the_existing_wording_is_untouched(self):
        # Stage 3 is additive: `related` and `spawned-from` read exactly as before.
        a, b = self._seed("Alpha"), self._seed("Beta")
        self._update(a, relate=[str(b["seq"])])
        self.assertEqual(ts._related_line(ts.load_task(a["id"])),
                         "Related: related #%s" % b["seq"])
        self.assertEqual(ts._related_line(ts.load_task(b["id"])),
                         "Related: related #%s" % a["seq"])
        child, origin = self._seed("Child"), self._seed("Origin")
        child = ts.load_task(child["id"])
        ts.append_related(child, origin, "spawned-from")
        ts.save_task(child)
        self.assertEqual(ts._related_line(ts.load_task(child["id"])),
                         "Related: from #%s (spawned-from)" % origin["seq"])
        self.assertEqual(ts._related_line(ts.load_task(origin["id"])),
                         "Related: spawned #%s" % child["seq"])

    def test_an_unknown_future_kind_still_reads_as_related(self):
        a, b = self._seed("Alpha"), self._seed("Beta")
        a = ts.load_task(a["id"])
        a["related"] = [{"id": b["id"], "seq": b["seq"], "kind": "invented-later"}]
        ts.save_task(a)
        self.assertEqual(ts._related_line(ts.load_task(a["id"])),
                         "Related: related #%s" % b["seq"])


if __name__ == "__main__":
    unittest.main()
