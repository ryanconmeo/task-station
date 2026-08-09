"""F3 — hierarchical graph drill-down (galaxy gravity wells, hover-outline/click-zoom
nav, smooth crash-proof boundary blobs), built ON the existing _MG_ENHANCE_JS.

Two kinds of coverage, because the interaction itself runs on a <canvas> (the documented
no-unit-test carve-out shared with test_render_graph.py):

  * PURE GEOMETRY — the tested reference twins of the canvas JS: `_galaxy_well_ring`
    (deterministic distinct wells per level) and `_blob_path`/`_convex_hull` (blob path
    generation for 1/2/3/N node groups, degenerate → circle, NEVER an exception — the
    2.0.0 blob-crash class).
  * STRUCTURAL — the F3 code is present + wired in the enhancement, the blob toggle rides
    the controls only when the graph carries galaxy grouping, and — the standing law —
    with Interbrain OFF the graph is byte-parity with pre-F3 (no wells, no blobs, no
    toggle, no owner/brain in mg-data).
"""
import importlib.util
import json
import math
import os
import re
import shutil
import sys
import tempfile
import unittest

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
sys.path.insert(0, LIB)
sys.path.insert(0, TOOLS)

import store  # noqa: E402
import render_board  # noqa: E402

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)

_PR = "https://github.com/o/r/pull/7"


# =========================================================================== geometry ===
class WellRingTest(unittest.TestCase):
    """F3.1 — gravity wells laid on a deterministic ring; distinct per level."""

    def test_distinct_and_deterministic(self):
        keys = ["ann\x00main", "bob\x00main", "cass\x00main"]
        a = render_board._galaxy_well_ring(keys, radius=100.0)
        b = render_board._galaxy_well_ring(list(reversed(keys)), radius=100.0)
        self.assertEqual(a, b)                              # sorted → order-independent
        pts = list(a.values())
        self.assertEqual(len(set(pts)), len(pts))           # every well is distinct
        for x, y in pts:                                    # …and on the ring
            self.assertAlmostEqual(math.hypot(x, y), 100.0, places=2)

    def test_two_people_two_brains_distinct_wells_and_person_centroids(self):
        # fixture: 2 people × 2 brains → 4 brain wells (person level) all distinct, and
        # 2 person wells (interbrain level = centroid of a person's brain wells) distinct.
        keys = ["ann\x00main", "ann\x00side", "bob\x00main", "bob\x00side"]
        wells = render_board._galaxy_well_ring(keys, radius=100.0)
        self.assertEqual(len(set(wells.values())), 4)       # F3.1 brain level: 4 galaxies
        people = {}
        for k, xy in wells.items():
            people.setdefault(k.split("\x00")[0], []).append(xy)
        centroids = {o: (sum(p[0] for p in v) / len(v), sum(p[1] for p in v) / len(v))
                     for o, v in people.items()}
        self.assertEqual(len(centroids), 2)                 # ann + bob
        (cx1, cy1), (cx2, cy2) = list(centroids.values())
        self.assertGreater(math.hypot(cx1 - cx2, cy1 - cy2), 1.0)   # F3.1 person level: apart


class BlobPathTest(unittest.TestCase):
    """F3.3 — blob path generation degrades cleanly for every group size."""

    def test_empty_group(self):
        r = render_board._blob_path([])
        self.assertEqual(r["kind"], "empty")
        self.assertEqual(r["d"], "")

    def test_one_and_two_points_are_circles(self):
        for pts in ([(10.0, 10.0)], [(0.0, 0.0), (20.0, 0.0)]):
            r = render_board._blob_path(pts)
            self.assertEqual(r["kind"], "circle")           # <3 → circle, NO hull math
            self.assertTrue(r["d"].startswith("M") and r["d"].endswith("Z"))

    def test_collinear_points_fall_back_to_circle(self):
        # three collinear points have no area → the crash class; must degrade to a circle.
        r = render_board._blob_path([(0.0, 0.0), (10.0, 10.0), (20.0, 20.0)])
        self.assertEqual(r["kind"], "circle")

    def test_three_plus_points_smooth_hull(self):
        r = render_board._blob_path([(0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0)])
        self.assertEqual(r["kind"], "hull")
        self.assertTrue(r["d"].startswith("M"))
        self.assertIn("C", r["d"])                          # cubic-Bézier smoothing
        self.assertTrue(r["d"].endswith("Z"))               # closed

    def test_never_raises_on_degenerate_or_duplicate(self):
        for pts in ([], [(1.0, 1.0)], [(1.0, 1.0), (1.0, 1.0)],
                    [(1.0, 1.0)] * 5, [(0.0, 0.0), (0.0, 0.0), (0.0, 1.0)]):
            render_board._blob_path(pts)                    # simply must not throw

    def test_convex_hull_drops_collinear(self):
        h = render_board._convex_hull([(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)])
        self.assertLess(len(h), 3)                          # collinear → <3 → circle upstream
        h2 = render_board._convex_hull([(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)])
        self.assertGreaterEqual(len(h2), 3)

    def test_deterministic(self):
        pts = [(3.0, 1.0), (9.0, 2.0), (5.0, 8.0), (1.0, 5.0)]
        self.assertEqual(render_board._blob_path(pts), render_board._blob_path(pts))


# ========================================================================= JS structure ===
class EnhancementStructureTest(unittest.TestCase):
    """The F3 engine is present + wired in the existing _MG_ENHANCE_JS (never a rewrite)."""

    def setUp(self):
        self.js = render_board._MG_ENHANCE_JS

    def test_existing_graph_behaviour_survives(self):
        # the enhancement is EXTENDED, not replaced: the run-1/pre-F3 vocabulary stays.
        for needle in ("fitView", "flattenZ", "seedZ", "yawVel", "hardReset",
                       "centerOnSeq", "perfLow", "autoRotate", "ts-board-graph"):
            self.assertIn(needle, self.js)

    def test_gravity_wells(self):
        self.assertIn("hasGalaxy", self.js)
        self.assertIn("WELLK", self.js)
        self.assertIn("brainWells", self.js)
        self.assertIn("personWells", self.js)
        self.assertIn("assignWells", self.js)
        self.assertIn("n._well", self.js)                   # per-node well attraction in tick

    def test_nav_levels_and_transitions(self):
        self.assertIn("galaxyLevel", self.js)
        self.assertIn("navDescend", self.js)
        self.assertIn("navAscend", self.js)
        self.assertIn('kind:"owner"', self.js)              # interbrain → person
        self.assertIn('kind:"brain"', self.js)              # person → brain
        self.assertIn("graphSetFocus", self.js)

    def test_hover_outline_and_reduced_motion(self):
        self.assertIn("drawHoverOutline", self.js)
        self.assertIn("levelMembers", self.js)
        self.assertIn("reduce||perfLow", self.js)           # static outline, no pulse

    def test_blobs_degrade_cleanly(self):
        self.assertIn("drawGalaxyBlobs", self.js)
        self.assertIn("drawBlob", self.js)
        self.assertIn("jsHull", self.js)
        self.assertIn("h.length<3", self.js)                # degenerate group → circle
        self.assertIn("bezierCurveTo", self.js)             # smoothed hull otherwise
        self.assertIn("blobsOn", self.js)                   # toggle-gated
        self.assertIn("pref.blobs!==false", self.js)        # default ON

    def test_animated_zoom_reuses_fitview_no_new_fov(self):
        self.assertIn("zoomTarget", self.js)
        self.assertIn("frameLevel", self.js)
        # camera is the WORKING fit-view/zoom-cap; no bespoke FOV/focal was introduced.
        self.assertIn("fitView();", self.js)
        self.assertNotIn("newFOV", self.js)

    def test_curved_cross_galaxy_edges(self):
        # F3.4: cross-galaxy edges bow (quadratic) so they span the gap; within-galaxy edges
        # stay straight (lineTo), and OFF keeps every edge straight (crossGalaxy → false).
        self.assertIn("crossGalaxy", self.js)
        self.assertIn("quadraticCurveTo", self.js)
        self.assertIn("ctx.lineTo(B.sx,B.sy)", self.js)     # within-galaxy / OFF path kept

    def test_strip_sync(self):
        self.assertIn("ts-focus-change", self.js)
        self.assertIn("navApplying", self.js)               # anti-bounce guard


# ============================================================ board render: on/off parity ===
def _feed_js(alias, feed):
    return ("window.__TSFEED_%s = %s;\n"
            "(window.__TSFEEDS = window.__TSFEEDS || []).push(window.__TSFEED_%s);\n"
            % (alias, json.dumps(feed), alias))


def _peer_task(uuid8, handle, title, prs, brain="main"):
    return {"uuid8": uuid8, "handle": handle, "title": title, "status": "active",
            "live": False,
            "category": {"key": "green", "tag": "FEATURE", "dot": "🟢",
                         "hex": "#3f9e2f", "hex_dark": "#6fe05a"},
            "effort": "m", "brain": brain, "shares": ["org"], "tokens": 100,
            "tokens_estimated": False, "cost_usd": 1.0, "models": ["sonnet"],
            "updated_ts": 1752810000, "relations": [],
            "signals": {"prs": list(prs), "stories": []},
            "digest": {"goal": "g", "state": "s", "steps_done": 1, "steps_total": 2,
                       "decisions_tail": []},
            "participants": [handle.split("-")[0]], "owner": handle.split("-")[0],
            "shared_org": True}


def _peer_feed(alias, tasks, color="#4f8fe6"):
    return {"schema": 3, "kind": "peer", "alias": alias, "owner": alias, "label": alias,
            "editable": False, "color": color, "color_dark": color, "tasks": tasks,
            "memos": []}


class _BoardBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-f3-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        for k in ("TASK_STATION_KNOWLEDGE_GRAPH", "TASK_STATION_OBSIDIAN_PROMPTS"):
            os.environ.pop(k, None)
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.DELEGATE_REGISTRY = os.path.join(self.tmp, "workers.json")
        store.reset_cache()
        self._ib = os.environ.get("TASK_STATION_INTERBRAIN")

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        if self._ib is None:
            os.environ.pop("TASK_STATION_INTERBRAIN", None)
        else:
            os.environ["TASK_STATION_INTERBRAIN"] = self._ib
        store.reset_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self, title, prs=None, **fields):
        t = ts.new_task(title, "s " + title, color=fields.pop("color", "green"),
                        effort="m")
        if prs:
            t["prs"] = [{"url": u, "desc": ""} for u in prs]
        for k, v in fields.items():          # projects / files / … set verbatim
            t[k] = v
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _write_feed(self, alias, feed):
        d = os.path.join(self.tmp, "feeds", "peers")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, alias + ".js"), "w", encoding="utf-8") as f:
            f.write(_feed_js(alias, feed))

    def _render(self, interbrain):
        os.environ["TASK_STATION_INTERBRAIN"] = interbrain
        out = ts.write_board()
        with open(out, encoding="utf-8") as f:
            return f.read()

    def _mgdata(self, html):
        m = re.search(r'<script type="application/json" id="mg-data">(.*?)</script>',
                      html, re.S)
        self.assertIsNotNone(m, "mg-data block missing")
        return json.loads(m.group(1).replace("\\u003c", "<").replace("\\u0026", "&"))


class InterbrainOnGalaxyTest(_BoardBase):
    def _seed_interbrain_graph(self):
        # two local tasks share the PR (→ a real signal hub, so the base graph has edges),
        # plus two peers each sharing that PR → 3 person galaxies (me + 2 peers).
        self._seed("Local ledger work", prs=[_PR])
        self._seed("Local ledger tests", prs=[_PR])
        self._write_feed("jpark", _peer_feed(
            "jpark", [_peer_task("aa11", "jpark-1", "peer A", ["o/r#7"])]))
        self._write_feed("alee", _peer_feed(
            "alee", [_peer_task("bb22", "alee-1", "peer B", ["o/r#7"])], color="#c76fe0"))

    def test_blob_toggle_present_and_default_on(self):
        self._seed_interbrain_graph()
        html = self._render("on")
        self.assertIn('class="mgbtn mgblob"', html)         # F3.3 blob toggle in controls
        self.assertIn('aria-pressed="true"', html)          # default ON
        self.assertIn("◍ Blobs", html)

    def test_mgdata_carries_owner_and_brain(self):
        self._seed_interbrain_graph()
        data = self._mgdata(self._render("on"))
        tasks = [n for n in data["nodes"] if n.get("type") == "task"]
        self.assertTrue(all("owner" in n for n in tasks))   # galaxy grouping data present
        owners = {n["owner"] for n in tasks}
        self.assertIn("jpark", owners)
        self.assertIn("alee", owners)                       # multiple person galaxies

    def test_blobs_on_and_off_both_render(self):
        # blobs default-on and the toggle-off path both live in the same guarded enhancement
        # (the whole script is try-caught); the board builds cleanly with the fixture and
        # ships BOTH the blob-draw code and the (default-on) toggle. Degenerate-group safety
        # is proven by BlobPathTest above (1/2/3/N, no exception).
        self._seed_interbrain_graph()
        html = self._render("on")
        self.assertIn("drawGalaxyBlobs", html)
        self.assertIn('class="mgbtn mgblob"', html)


class InterbrainOffParityTest(_BoardBase):
    def test_off_graph_unchanged_no_galaxy_footprint(self):
        # the standing law: Interbrain OFF ⇒ the graph is behaviour-identical to classic —
        # no blob toggle, no focus strip, and mg-data carries NO owner/brain (so hasGalaxy
        # is false client-side and every F3 path is inert).
        self._seed("Local ledger work", prs=[_PR])
        self._seed("Local ledger tests", prs=[_PR])   # 2 locals share a PR → a graph panel
        self._write_feed("jpark", _peer_feed(
            "jpark", [_peer_task("aa11", "jpark-1", "peer A", ["o/r#7"])]))
        html = self._render("off")
        self.assertNotIn('class="mgbtn mgblob"', html)      # no blob toggle
        self.assertNotIn('id="focus-strip"', html)          # no strip → hasGalaxy false
        data = self._mgdata(html)
        for n in data["nodes"]:
            self.assertNotIn("owner", n)                    # no galaxy grouping leaks
            self.assertNotIn("brain", n)
        self.assertNotIn("foreign", json.dumps(data))       # peer feed never leaks


# ============================================ Stage 2: edge registry + graph filters ===
# Three rulings about the board's dependency graph, all of them PRE-conditions on the
# later banded-layout rework because they land in the same filter panel and edge registry:
#
#   (a) the `touch` / "Same file" edge kind is gone from the GRAPH (the export keeps it),
#   (b) signal hubs filter by KIND — three rows, not one per hub,
#   (c) a category hub follows its category instead of carrying its own switch.
#
# (b) and (c) are JS inside a Python string, so some assertions must read the emitted
# source. Where that is unavoidable they assert the OLD control is gone AND the NEW
# expression is present — never merely that some string appears somewhere — and the
# substantive claims (how many hubs the fixture really has, whether a hub's key resolves
# against the category filter) are checked against the emitted mg-data instead.

_PR_B = "https://github.com/o/r/pull/8"


def _ek_rows(html):
    """The `EK` edge-kind registry parsed out of the emitted JS as real data —
    [[class, label], …]. Structural, so a renamed label cannot fake a match."""
    m = re.search(r"var EK=(\[\[.*?\]\]),g4=null;", html)
    assert m, "EK registry not found in the emitted page"
    return json.loads(m.group(1))


class EdgeRegistryTest(_BoardBase):
    """(a) — the registry lost `touch`, and every class an edge can land in still has
    a filter row."""

    def _pr_pair_html(self):
        self._seed("Ledger work", prs=[_PR])
        self._seed("Ledger tests", prs=[_PR])
        return self._render("off")

    def test_ek_has_no_touch_row(self):
        rows = _ek_rows(self._pr_pair_html())
        self.assertNotIn("touch", [r[0] for r in rows])
        self.assertNotIn("Same file", [r[1] for r in rows])
        self.assertIn("lineage", [r[0] for r in rows])     # …the rest is intact

    def test_unknown_edge_kind_fallback_names_a_class_ek_still_lists(self):
        # THE TRAP, and the highest-value guard here. `CLS[e.kind]||"<fallback>"` decides
        # the class of any edge kind the renderer does not know, and four typed kinds
        # (depends-on / parent / absorbed-by / related) are heading for this registry.
        # If the fallback ever names a class EK has no row for, that edge draws with NO
        # filter and can never be turned off. Fails if EITHER side of the pair moves.
        html = self._pr_pair_html()
        m = re.search(r'var cls=CLS\[e\.kind\]\|\|"([A-Za-z0-9_-]+)"', html)
        self.assertIsNotNone(m, "edge-class fallback expression not found")
        self.assertIn(m.group(1), [r[0] for r in _ek_rows(html)])

    def test_every_mapped_edge_class_has_a_filter_row(self):
        # The same rule one step earlier: a class CLS can PRODUCE also needs a row.
        # `xbrain` (the cross-brain edge) is the one pre-existing exception — a subset
        # check, so it may gain a row later without breaking, while a NEW unfiltered
        # class fails here.
        html = self._pr_pair_html()
        m = re.search(r"var CLS=(\{.*?\});", html)
        self.assertIsNotNone(m, "CLS map not found in the emitted page")
        classes = set(re.findall(r':"([A-Za-z0-9_-]+)"', m.group(1)))
        self.assertNotIn("touch", classes)                 # the touches-same mapping is gone
        unfiltered = classes - {r[0] for r in _ek_rows(html)}
        self.assertTrue(unfiltered <= {"xbrain"},
                        "edge classes with no filter row: %s" % sorted(unfiltered))


class TouchesSameRemovalTest(_BoardBase):
    """(a) — the graph stopped drawing it; the export did not."""

    def test_graph_draws_nothing_for_a_shared_file(self):
        self._seed("Ledger work", files=["/r/ledger.py"])
        self._seed("Ledger tests", files=["/r/ledger.py"])
        g = ts.build_board_graph(ts.all_tasks())
        self.assertEqual([e for e in g["edges"] if e["kind"] == "touches-same"], [])
        self.assertEqual(g, {"nodes": [], "edges": []})    # a file share is not a relation

    def test_shared_pr_still_reaches_the_graph_as_a_hub(self):
        # The other half of the ruling: dropping the direct edge must NOT drop the
        # signal tier. A shared PR still draws — once, as a hub with spokes.
        self._seed("Ledger work", prs=[_PR])
        self._seed("Ledger tests", prs=[_PR])
        g = ts.build_render_graph(ts.all_tasks())
        self.assertEqual([e for e in g["edges"] if e["kind"] == "touches-same"], [])
        self.assertIn("sig:pr:%s" % _PR, [n["id"] for n in g["nodes"]])
        self.assertEqual(len([e for e in g["edges"] if e["kind"] == "pr"]), 2)

    def test_export_still_emits_its_touches_same_pair(self):
        # `_related_pairs` feeds the vault/markdown export and is deliberately unchanged
        # — this is the graph dropping a consumer, not the signal being deleted.
        a = self._seed("Ledger work", prs=[_PR])
        self._seed("Ledger tests", prs=[_PR])
        pairs = ts._related_pairs(ts.load_task(a["id"]), ts.all_tasks(), False,
                                  lambda tid: "stem-" + str(tid)[:8])
        self.assertIn("touches same", [p[-1] for p in pairs])


class SignalHubKindFilterTest(_BoardBase):
    """(b) — one row per signal KIND, not one per hub."""

    def _multi_hub_html(self):
        """Two PR hubs + two repo hubs = FOUR hub nodes across TWO kinds. A per-hub
        implementation would emit four rows against this fixture; the kind rule emits
        two, and can never emit more than three."""
        self._seed("Ledger work", prs=[_PR], projects=["projectname"])
        self._seed("Ledger tests", prs=[_PR], projects=["projectname"])
        self._seed("Report work", prs=[_PR_B], projects=["OtherProj"])
        self._seed("Report tests", prs=[_PR_B], projects=["OtherProj"])
        return self._render("off")

    def test_rows_are_bounded_by_kind_not_by_hub_count(self):
        html = self._multi_hub_html()
        hubs = [n for n in self._mgdata(html)["nodes"] if n.get("type") == "signal"]
        self.assertGreaterEqual(len(hubs), 4)              # the fixture really has 4+ hubs
        self.assertLessEqual(len({h["kind"] for h in hubs}), 3)   # …across <=3 kinds
        self.assertIn('var kkeys=["story","repo","pr"].filter', html)   # order: story,repo,pr
        self.assertIn("filt.sig[k]=true", html)            # one row per KIND key
        self.assertNotIn("filt.sig[s.id]=true", html)      # the per-hub loop is gone

    def test_node_visibility_keys_on_signal_kind(self):
        html = self._multi_hub_html()
        self.assertIn("return filt.sig[n.kind]!==false;", html)
        self.assertNotIn("filt.sig[n.id]", html)


class CategoryHubFollowsCategoryTest(_BoardBase):
    """(c) — the standalone `filt.cathub` switch is gone; a hub follows its category."""

    def _html(self):
        self._seed("Ledger work", prs=[_PR])
        self._seed("Ledger tests", prs=[_PR])
        return self._render("off")

    def test_no_cathub_control_survives_anywhere(self):
        html = self._html()
        self.assertNotIn("cathub", html)                   # state, glyph, row and reset
        self.assertNotIn("catHubN", html)                  # …and no dead locals
        self.assertNotIn("hasCatHub", html)
        self.assertNotIn('mkGroup("Category hubs")', html)

    def test_category_hub_visibility_reads_from_filt_cat(self):
        html = self._html()
        self.assertIn('if(n.type==="hub")return filt.cat[n.key]!==false;', html)
        # The substantive half: the key it reads must be one `filt.cat` actually carries.
        # filt.cat is keyed by the task's stored `color`; a hub's `key` is that colour
        # NORMALISED. If those two ever diverged every lookup would be undefined and the
        # category toggle would silently stop hiding its hub.
        data = self._mgdata(html)
        hubs = [n for n in data["nodes"] if n.get("type") == "hub"]
        cats = {n.get("color") for n in data["nodes"] if n.get("type") == "task"}
        self.assertTrue(hubs, "fixture must produce a category hub")
        for h in hubs:
            self.assertIn(h.get("key"), cats)


if __name__ == "__main__":
    unittest.main()
