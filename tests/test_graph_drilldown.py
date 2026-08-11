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

    def test_breadcrumb_ships_with_the_galaxy_graph(self):
        # F4a: the breadcrumb element is minted by the enhancement JS (only when the graph
        # carries galaxy grouping), so what the PAGE has to ship is its style rule and the
        # code that builds it. Asserted on the real render, not on the JS constant.
        self._seed_interbrain_graph()
        html = self._render("on")
        self.assertIn(".mgcrumb{", html)                    # the style rule ships
        self.assertIn("function drawCrumb(", html)          # …and the builder
        self.assertIn('data-crumb="0"', html)               # depth 0 is a real segment

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

    def test_off_render_emits_no_breadcrumb_element(self):
        # F4a: the breadcrumb is minted by the enhancement JS and only when hasGalaxy, so
        # it must never appear in the served markup — in EITHER render. This guards
        # against a later move of it into the server-side panel without the gate.
        self._seed("Local ledger work", prs=[_PR])
        self._seed("Local ledger tests", prs=[_PR])
        self.assertNotIn('class="mgcrumb"', self._render("off"))


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
        # The same rule one step earlier: a class CLS can PRODUCE also needs a row, or
        # that edge draws with no way to turn it off. `xbrain` was the one grandfathered
        # exception and has its row now, so this is an EXACT check — there is no
        # exception set left to hide the next one in.
        html = self._pr_pair_html()
        m = re.search(r"var CLS=(\{.*?\});", html)
        self.assertIsNotNone(m, "CLS map not found in the emitted page")
        classes = set(re.findall(r':"([A-Za-z0-9_-]+)"', m.group(1)))
        self.assertNotIn("touch", classes)                 # the touches-same mapping is gone
        unfiltered = classes - {r[0] for r in _ek_rows(html)}
        self.assertEqual(unfiltered, set(),
                         "edge classes with no filter row: %s" % sorted(unfiltered))

    def test_the_cross_brain_edge_has_a_filter_row(self):
        rows = _ek_rows(self._pr_pair_html())
        self.assertIn("xbrain", [r[0] for r in rows])


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


# ================================================ Stage 4a: the enum→path refactor ===
# The graph could zoom through exactly three hardcoded levels — `galaxyLevel()` returned
# one of "interbrain"/"person"/"brain" and five functions branched on that string. The
# last of them, `drawGalaxyBlobs`, early-returned unless the level was one of the first
# two, which is precisely why bubbles could never nest. Nesting makes depth VARIABLE, so
# the enum becomes a PATH: those three levels are paths of length 0, 1 and 2, and
# DEPTH IS THE LENGTH.
#
# This stage changes no behaviour, with ONE mandated exception recorded in the report
# (deleting the blob early-return means a blob now draws at the deepest level, where none
# drew before — that deletion IS the unlock the brief asks for).
#
# The four helpers are tested through their PURE PYTHON TWINS, the same carve-out
# WellRingTest and BlobPathTest above already use: the live versions run inside the
# <canvas> enhancement, so the RULES are mirrored in render_board and asserted here. The
# JS side gets structural assertions only for the things a twin cannot show — that a
# guard was deleted, that a field stayed singular, that a name survived.

_TASK_A = {"type": "task", "id": "t:1", "owner": "ann", "brain": "main"}
_TASK_A2 = {"type": "task", "id": "t:2", "owner": "ann", "brain": "side"}
_TASK_B = {"type": "task", "id": "t:3", "owner": "bob", "brain": "main"}
_TASK_BARE = {"type": "task", "id": "t:4"}                  # no owner, no brain


class ContainmentPathTest(unittest.TestCase):
    """F4a.1 — galaxyPath / nodePath / isDescendant / containerKeyAt, via the twins."""

    def test_galaxy_path_for_the_three_focus_states(self):
        self.assertEqual(render_board._galaxy_path(None), [])
        self.assertEqual(render_board._galaxy_path({"kind": "owner", "owner": "ann"}),
                         ["ann"])
        self.assertEqual(render_board._galaxy_path(
            {"kind": "brain", "owner": "ann", "brain": "side"}), ["ann", "side"])
        # …and their depths are 0 / 1 / 2 — the old enum, now just a length.
        self.assertEqual([len(render_board._galaxy_path(f)) for f in (
            None, {"kind": "owner", "owner": "ann"},
            {"kind": "brain", "owner": "ann", "brain": "side"})], [0, 1, 2])

    def test_a_non_container_focus_reads_as_the_root(self):
        # `org` is a focus KIND but not a container in the tree, so it is the root —
        # mirroring the old galaxyLevel() fall-through to "interbrain".
        self.assertEqual(render_board._galaxy_path({"kind": "org"}), [])
        self.assertEqual(render_board._galaxy_path({}), [])

    def test_an_empty_path_matches_every_node(self):
        # This is what makes the unfocused case fall out instead of being special-cased.
        for n in (_TASK_A, _TASK_A2, _TASK_B, _TASK_BARE):
            self.assertTrue(render_board._is_descendant(n, []), n["id"])

    def test_is_descendant_walks_the_prefix(self):
        self.assertTrue(render_board._is_descendant(_TASK_A, ["ann"]))
        self.assertTrue(render_board._is_descendant(_TASK_A, ["ann", "main"]))
        self.assertFalse(render_board._is_descendant(_TASK_A, ["ann", "side"]))
        self.assertFalse(render_board._is_descendant(_TASK_A, ["bob"]))
        # a path DEEPER than the node's own containment can never match
        self.assertFalse(render_board._is_descendant(_TASK_A, ["ann", "main", "x"]))

    def test_container_key_at_zero_is_the_owner_and_at_one_adds_the_brain(self):
        self.assertEqual(render_board._container_key_at(_TASK_A, 0), "ann")
        self.assertEqual(render_board._container_key_at(_TASK_A, 1),
                         "ann" + render_board.PATH_SEP + "main")

    def test_depth_zero_is_a_real_depth_not_a_missing_one(self):
        # The bug class this project has been bitten by twice — hub ordinal `444-0` is a
        # real ordinal, station numbering starts at 0 — and both needed their own test.
        # Depth 0 must yield the OUTERMOST container and must never read as "no depth".
        self.assertEqual(render_board._container_key_at(_TASK_A, 0), "ann")
        self.assertTrue(bool(render_board._container_key_at(_TASK_A, 0)))
        self.assertNotEqual(render_board._container_key_at(_TASK_A, 0),
                            render_board._container_key_at(_TASK_A, 1))
        # `depth` is positional with NO default, so it cannot be silently skipped into
        # meaning something else — falsy-testing it would collapse 0 into "unset".
        with self.assertRaises(TypeError):
            render_board._container_key_at(_TASK_A)

    def test_a_node_with_no_owner_yields_an_empty_path_and_crashes_nothing(self):
        self.assertEqual(render_board._node_path(_TASK_BARE), [])
        self.assertEqual(render_board._container_key_at(_TASK_BARE, 0), "")
        self.assertEqual(render_board._container_key_at(_TASK_BARE, 5), "")
        self.assertTrue(render_board._is_descendant(_TASK_BARE, []))
        self.assertFalse(render_board._is_descendant(_TASK_BARE, ["ann"]))
        self.assertEqual(render_board._node_path(None), [])
        self.assertEqual(render_board._node_path({"type": "hub", "key": "green"}), [])

    def test_a_depth_past_the_node_clamps_to_its_deepest_container(self):
        # The property assignWells and galaxyKey rely on: past a node's own depth the key
        # stops changing, so "brain level" and anything below it share one well.
        deepest = render_board._container_key_at(_TASK_A, 1)
        self.assertEqual(render_board._container_key_at(_TASK_A, 2), deepest)
        self.assertEqual(render_board._container_key_at(_TASK_A, 9), deepest)

    # -- the three original levels, expressed through the path -------------------
    def test_level_one_interbrain_groups_a_person_s_brains_together(self):
        self.assertEqual(render_board._container_key_at(_TASK_A, 0),
                         render_board._container_key_at(_TASK_A2, 0))     # ann/* together
        self.assertNotEqual(render_board._container_key_at(_TASK_A, 0),
                            render_board._container_key_at(_TASK_B, 0))   # bob apart

    def test_level_two_person_splits_a_person_s_brains(self):
        self.assertNotEqual(render_board._container_key_at(_TASK_A, 1),
                            render_board._container_key_at(_TASK_A2, 1))
        self.assertEqual(render_board._container_key_at(_TASK_A, 1),
                         render_board._container_key_at(
                             {"type": "task", "id": "t:9", "owner": "ann",
                              "brain": "main"}, 1))                       # same brain

    def test_level_three_brain_is_below_the_deepest_container(self):
        # At depth 2 the key has stopped changing, which is exactly why levelMembers
        # switches to identity (`n===pv`) there — the hover outline hugs the one hovered
        # task rather than ballooning to its whole brain.
        self.assertGreaterEqual(2, len(render_board._node_path(_TASK_A)))
        self.assertEqual(render_board._container_key_at(_TASK_A, 2),
                         render_board._container_key_at(_TASK_A, 1))


class PathRefactorStructureTest(unittest.TestCase):
    """F4a.2 — the JS side: the level enum is gone from the consumers, the blob guard is
    gone, `n._well` is still singular, and the breadcrumb is wired."""

    def setUp(self):
        self.js = render_board._MG_ENHANCE_JS

    def _body(self, name, until):
        """The source of one JS function, from its declaration to the next named one."""
        i = self.js.index("function %s(" % name)
        return self.js[i:self.js.index(until, i)]

    def test_the_four_helpers_exist(self):
        for fn in ("function galaxyPath(", "function nodePath(",
                   "function isDescendant(", "function containerKeyAt("):
            self.assertIn(fn, self.js, fn)

    def test_draw_galaxy_blobs_has_no_level_guard(self):
        # THE unlock: the early return refused to run past level two, so bubbles could
        # never nest. It is gone, and grouping is by container key at whatever depth.
        body = self._body("drawGalaxyBlobs", "function drawHoverOutline")
        self.assertNotIn("interbrain", body)
        self.assertNotIn("person", body)
        self.assertIn("containerKeyAt(n,depth)", body)

    def test_no_consumer_branches_on_a_level_string(self):
        # The five consumers go through the path helpers. The three NAMES survive only in
        # galaxyLevel itself (the canvas hint prints one) and in the nav verbs that mint
        # the focus KINDS the strip shares — neither is a grouping branch.
        for name, until in (("assignWells", "function levelMembers"),
                            ("levelMembers", "function galaxyColor"),
                            ("galaxyKey", "function crossGalaxy"),
                            ("crossGalaxy", "function jsHull")):
            body = self._body(name, until)
            for lit in ('"interbrain"', '"person"', '"brain"'):
                self.assertNotIn(lit, body, "%s still branches on %s" % (name, lit))

    def test_well_is_still_singular(self):
        # n._well means "my well at the CURRENT depth", recomputed per depth — never a
        # list. That is what keeps the physics cost FLAT at any nesting depth, and why
        # only the focused frame ever needs live simulation.
        self.assertIn("n._well=wellsByKey[containerKeyAt(n,depth)]", self.js)
        self.assertNotIn("n._wells", self.js)
        self.assertNotIn("_well.push", self.js)
        self.assertNotIn("n._well=[", self.js)

    def test_depth_is_never_falsy_tested_in_the_js(self):
        for name, until in (("containerKeyAt", "function galaxyLevel"),
                            ("levelMembers", "function galaxyColor")):
            body = self._body(name, until)
            self.assertIn("depth==null", body, name)        # explicit, not truthiness
            self.assertNotIn("!depth", body, name)

    def test_galaxy_level_is_kept_and_derived_from_the_path(self):
        # A pre-existing test pins `galaxyLevel`, and the canvas hint still prints the
        # level NAME — so it stays, now DERIVED from the path rather than authoritative.
        self.assertIn("function galaxyLevel(", self.js)
        self.assertIn("galaxyPath(f).length", self._body("galaxyLevel", "function assignWells"))

    def test_the_strip_sync_uses_depth_not_a_rank_table(self):
        # {interbrain:0,person:1,brain:2} would read `undefined` for any level past the
        # third, which is exactly what variable depth introduces.
        self.assertNotIn("rank={interbrain", self.js)
        self.assertIn("old=galaxyPath().length", self.js)

    def test_breadcrumb_is_present_and_clickable(self):
        # Variable depth forces one genuinely new control: once "up" can be more than two
        # steps, blank-click-up has to say where up goes.
        self.assertIn("function drawCrumb(", self.js)
        self.assertIn("function focusAtDepth(", self.js)
        self.assertIn("mgcrumbseg", self.js)
        self.assertIn("data-crumb", self.js)
        self.assertIn("drawCrumb();", self.js)              # wired into the level change

    def test_the_breadcrumb_is_absent_from_the_off_render(self):
        # Built in JS and only when hasGalaxy, so the Interbrain-off page emits no
        # breadcrumb element at all — the parity law holds without a special case.
        self.assertIn('if(hasGalaxy){try{var cwrap=panel.querySelector(".mgcanvaswrap")',
                      self.js)


# ================================================ Stage 4b: the concentric layout ===
# Four rings, outermost in — rim (category MAGNETS) · middle (tasks + parent BUBBLES) ·
# story band (MAGNETS) · core (repo BUBBLES holding their PRs) — and two placement axes
# with one job each: ANGLE from category (loose) or parent (grouped), RADIUS from how
# entangled a task is in shared work.
#
# THE RULE THAT KEEPS BUBBLES INTACT, and the one most of these tests exist to pin: a
# task inside a group is positioned by its group ALONE — no category pull, no core pull.
# The live programme has 8 children spanning 5 categories, so a category-driven angle
# would tear that bubble across five sectors.
#
# `_concentric_layout` is pure, so the whole design is testable without a browser — the
# same twin idiom WellRingTest and the F4a path tests use. What a twin cannot answer is
# whether it LOOKS right; that is the owner's call on the real render.


def _lt(tid, seq, cat, parent=None, artifacts=0):
    return {"id": tid, "seq": seq, "cat": cat, "parent": parent, "artifacts": artifacts}


def _polar(pt, cx=0.0, cy=0.0):
    """(radius, angle) of a laid-out point about the layout centre."""
    dx, dy = pt[0] - cx, pt[1] - cy
    return math.hypot(dx, dy), math.atan2(dy, dx)


def _adist(a, b):
    """Absolute angular distance, wrapped to [0, π]."""
    d = abs(a - b) % (2 * math.pi)
    return min(d, 2 * math.pi - d)


class ConcentricLayoutTest(unittest.TestCase):
    """F4b — the four rings and the two axes, through the pure layout function."""

    def test_every_task_gets_a_position_even_with_no_edge_at_all(self):
        # Category membership — not an edge — is what entitles a task to a position.
        tasks = [_lt("t:%d" % i, i, "green" if i % 2 else "red") for i in range(1, 13)]
        lay = render_board._concentric_layout(tasks)
        self.assertEqual(set(lay["task"]), {t["id"] for t in tasks})

    def test_a_loose_task_takes_its_angle_from_its_category(self):
        tasks = [_lt("t:1", 1, "green"), _lt("t:2", 2, "green"), _lt("t:3", 3, "red")]
        lay = render_board._concentric_layout(tasks)
        a1 = _polar(lay["task"]["t:1"])[1]
        a2 = _polar(lay["task"]["t:2"])[1]
        a3 = _polar(lay["task"]["t:3"])[1]
        # two tasks of one category sit inside one sector…
        self.assertLessEqual(_adist(a1, a2), render_board.CAT_SPAN + 1e-9)
        # …and a different category is further away than that sector is wide, so a
        # sector never bleeds into its neighbour.
        self.assertGreater(_adist(a1, a3), render_board.CAT_SPAN)
        # each sits on its own category's magnet bearing
        self.assertLessEqual(_adist(a1, _polar(lay["cat"]["green"])[1]),
                             render_board.CAT_SPAN)

    def test_category_angles_are_stable_across_renders(self):
        # Assigned over SORTED keys, so the rim never wobbles between renders.
        tasks = [_lt("t:1", 1, "green"), _lt("t:2", 2, "red"), _lt("t:3", 3, "blue")]
        first = render_board._concentric_layout(tasks)
        second = render_board._concentric_layout(list(reversed(tasks)))
        self.assertEqual(first["cat"], second["cat"])
        self.assertEqual(first["task"], second["task"])

    def test_a_parented_task_is_inside_its_parent_bubble_and_ignores_its_category(self):
        # The bubble-integrity rule: angle comes from the PARENT, not the category.
        tasks = [_lt("t:1", 1, "green"),                       # the parent
                 _lt("t:2", 2, "red", parent="t:1"),
                 _lt("t:3", 3, "blue", parent="t:1"),
                 _lt("t:9", 9, "red")]                         # a loose red comparator
        lay = render_board._concentric_layout(tasks)
        px, py = lay["task"]["t:1"]
        for kid in ("t:2", "t:3"):
            kx, ky = lay["task"][kid]
            self.assertLessEqual(math.hypot(kx - px, ky - py),
                                 render_board.R_CHILD + 1e-6)   # inside the bubble
        # …and the red child is nowhere near the red magnet's bearing, unlike the loose
        # red task, which is: that difference IS the rule.
        red = _polar(lay["cat"]["red"])[1]
        self.assertLessEqual(_adist(_polar(lay["task"]["t:9"])[1], red),
                             render_board.CAT_SPAN)
        self.assertGreater(_adist(_polar(lay["task"]["t:2"])[1], red),
                           render_board.CAT_SPAN)
        # the bubble is a real group with no node of its own beyond the parent
        bub = [b for b in lay["bubbles"] if b["kind"] == "parent"]
        self.assertEqual(len(bub), 1)
        self.assertEqual(bub[0]["members"], ["t:1", "t:2", "t:3"])

    def test_a_parented_task_feels_no_core_pull(self):
        # Adding shared artifacts must not move a grouped task: inside a group there is
        # no core pull, or the bubble would be stretched toward the centre.
        base = [_lt("t:1", 1, "green"), _lt("t:2", 2, "red", parent="t:1")]
        with_prs = [_lt("t:1", 1, "green"),
                    _lt("t:2", 2, "red", parent="t:1", artifacts=9)]
        self.assertEqual(render_board._concentric_layout(base)["task"],
                         render_board._concentric_layout(with_prs)["task"])

    def test_a_parent_is_not_magnetised_either(self):
        # Same reason: a magnetised root would drag its whole subtree to the rim.
        tasks = [_lt("t:1", 1, "green"), _lt("t:2", 2, "green", parent="t:1"),
                 _lt("t:9", 9, "green")]
        lay = render_board._concentric_layout(tasks)
        r_root = _polar(lay["task"]["t:1"])[0]
        self.assertAlmostEqual(r_root, render_board.R_GROUP, places=6)
        self.assertNotAlmostEqual(r_root, _polar(lay["task"]["t:9"])[0], places=3)

    def test_more_entangled_sits_further_in(self):
        tasks = [_lt("t:1", 1, "green", artifacts=0),
                 _lt("t:2", 2, "green", artifacts=1),
                 _lt("t:3", 3, "green", artifacts=4)]
        lay = render_board._concentric_layout(tasks)
        r = [_polar(lay["task"]["t:%d" % i])[0] for i in (1, 2, 3)]
        self.assertGreater(r[0], r[1])
        self.assertGreater(r[1], r[2])

    def test_zero_artifacts_sits_on_the_rim(self):
        lay = render_board._concentric_layout([_lt("t:1", 1, "green", artifacts=0)])
        self.assertAlmostEqual(_polar(lay["task"]["t:1"])[0], render_board.R_TASK_MAX,
                               places=6)
        # …just inside the magnets, so a task never lands on top of one.
        self.assertLess(render_board.R_TASK_MAX, render_board.R_RIM)

    def test_the_radius_scale_is_clamped(self):
        # A task with twelve PRs must NOT collapse into the centre — log-scaled, then
        # clamped at R_TASK_MIN, which is still well outside the story band and core.
        r12 = render_board._entangle_radius(12)
        self.assertAlmostEqual(r12, render_board.R_TASK_MIN, places=6)
        self.assertGreater(r12, render_board.R_STORY)
        self.assertGreater(render_board.R_STORY, render_board.R_CORE)
        self.assertEqual(render_board._entangle_radius(999),
                         render_board._entangle_radius(12))

    def test_a_nested_parent_produces_a_bubble_inside_a_bubble(self):
        # What 4a's path unlocked: nesting is free because each bubble is hulled over
        # its own subtree.
        tasks = [_lt("t:1", 1, "green"), _lt("t:2", 2, "green", parent="t:1"),
                 _lt("t:3", 3, "green", parent="t:2")]
        lay = render_board._concentric_layout(tasks)
        kinds = {b["key"]: b["members"] for b in lay["bubbles"]
                 if b["kind"] == "parent"}
        self.assertEqual(kinds["t:1"], ["t:1", "t:2", "t:3"])    # outer
        self.assertEqual(kinds["t:2"], ["t:2", "t:3"])           # …and the inner one
        # the inner cluster is tighter than the outer one — it has to fit inside it
        p, c, g = (lay["task"]["t:1"], lay["task"]["t:2"], lay["task"]["t:3"])
        self.assertLess(math.hypot(g[0] - c[0], g[1] - c[1]),
                        math.hypot(c[0] - p[0], c[1] - p[1]))

    def test_a_repo_bubble_contains_its_prs_and_a_story_is_a_bare_magnet(self):
        lay = render_board._concentric_layout(
            [_lt("t:1", 1, "green")], stories=["sig:story:S1"],
            repos={"sig:repo:one": ["sig:pr:a", "sig:pr:b"]})
        bub = [b for b in lay["bubbles"] if b["kind"] == "repo"]
        self.assertEqual(len(bub), 1)
        self.assertEqual(bub[0]["members"],
                         ["sig:pr:a", "sig:pr:b", "sig:repo:one"])
        for p in ("sig:pr:a", "sig:pr:b"):
            self.assertIn(p, lay["pr"])
        # a story is a NODE on the band — never a hull, and never inside a repo bubble
        self.assertIn("sig:story:S1", lay["story"])
        self.assertNotIn("sig:story:S1",
                         [m for b in lay["bubbles"] for m in b["members"]])
        self.assertAlmostEqual(_polar(lay["story"]["sig:story:S1"])[0],
                               render_board.R_STORY, places=6)

    def test_a_story_spanning_two_repos_is_in_neither_bubble(self):
        # 37 of 70 real stories span more than one repo, which is exactly why a story is
        # a magnet: a story bubble would have to cut through repo boundaries.
        lay = render_board._concentric_layout(
            [_lt("t:1", 1, "green", artifacts=2), _lt("t:2", 2, "red", artifacts=2)],
            stories=["sig:story:wide"],
            repos={"sig:repo:one": ["sig:pr:a"], "sig:repo:two": ["sig:pr:b"]})
        members = [m for b in lay["bubbles"] if b["kind"] == "repo"
                   for m in b["members"]]
        self.assertNotIn("sig:story:wide", members)
        self.assertEqual(len([b for b in lay["bubbles"] if b["kind"] == "repo"]), 2)
        # both repos are in the core, the story out on its own band
        for k in ("sig:repo:one", "sig:repo:two"):
            self.assertAlmostEqual(_polar(lay["repo"][k])[0], render_board.R_CORE,
                                   places=6)

    def test_the_ring_order_is_outermost_in(self):
        self.assertGreater(render_board.R_RIM, render_board.R_TASK_MAX)
        self.assertGreater(render_board.R_TASK_MAX, render_board.R_GROUP)
        self.assertGreater(render_board.R_TASK_MIN, render_board.R_STORY)
        self.assertGreater(render_board.R_STORY, render_board.R_CORE)

    def test_a_parent_cycle_does_not_hang_the_layout(self):
        # Stage 3 warns on a parent cycle but ALWAYS stores it, so the layout has to be
        # cycle-safe by construction rather than by hoping the data is a tree.
        tasks = [_lt("t:1", 1, "green", parent="t:2"),
                 _lt("t:2", 2, "green", parent="t:1")]
        lay = render_board._concentric_layout(tasks)
        self.assertEqual(set(lay["task"]), {"t:1", "t:2"})

    def test_a_self_parent_is_treated_as_loose(self):
        lay = render_board._concentric_layout([_lt("t:1", 1, "green", parent="t:1")])
        self.assertEqual(lay["bubbles"], [])
        self.assertAlmostEqual(_polar(lay["task"]["t:1"])[0], render_board.R_TASK_MAX,
                               places=6)


class ConcentricRenderTest(_BoardBase):
    """F4b — the layout reaching the real render: no cap, no Unlinked group, bubbles."""

    def test_sixty_tasks_all_draw(self):
        # The 40-cap is gone. One task pair shares a PR so the panel has an edge (the
        # relation-free gate is unchanged); the other 58 have nothing at all and are
        # promoted onto the rim.
        self._seed("Shared work one", prs=[_PR])
        self._seed("Shared work two", prs=[_PR])
        for i in range(58):
            self._seed("Loose task %02d" % i)
        html = self._render("off")
        drawn = [n for n in self._mgdata(html)["nodes"] if n.get("type") == "task"]
        self.assertEqual(len(drawn), 60)
        self.assertNotIn("· showing", html)                # nothing is being withheld

    def test_no_unlinked_filter_group_is_emitted(self):
        self._seed("Shared work one", prs=[_PR])
        self._seed("Shared work two", prs=[_PR])
        self._seed("A relation-free task")
        html = self._render("off")
        self.assertNotIn('mkGroup("Unlinked")', html)
        self.assertNotIn("unlinked tasks", html)
        self.assertNotIn("soloRow", html)

    def test_the_canvas_holds_nodes_at_their_layout_slot(self):
        # F4c INVERTED this: the slot is a PIN by default, and the spring back to it is
        # only for the nodes that still simulate — a focused frame's members.
        self.assertIn("var s=slotFor(n);n._lt=s;", render_board._MG_ENHANCE_JS)
        self.assertIn("else if(n._lt){n.vx+=(n._lt.x-n.x)*LAYK", render_board._MG_ENHANCE_JS)
        self.assertIn("var LAYK=", render_board._MG_ENHANCE_JS)
        # the galaxy well still WINS when present, so 4a's Interbrain grouping is intact
        self.assertLess(render_board._MG_ENHANCE_JS.index("if(n._well){"),
                        render_board._MG_ENHANCE_JS.index("else if(n._lt){"))

    def test_a_parent_bubble_is_drawn_as_a_hull_path(self):
        parent = self._seed("Programme root", prs=[_PR])
        self._seed("Sibling sharing the pr", prs=[_PR])
        for i in range(3):
            kid = self._seed("Child %d" % i)
            kid = ts.load_task(kid["id"])
            ts.append_related(kid, parent, "parent")
            ts.save_task(kid)
        html = self._render("off")
        self.assertIn('class="mg-bubble mg-bubble-parent"', html)
        self.assertIn(".mg-bubble{", html)                  # …and it has a style rule


# ============================ Stage 4c: pinning, seating, labels, and the two fixes ===
class PinnedByDefaultTest(unittest.TestCase):
    """F4c.1 — the canvas pins by default and simulates only the focused subset plus
    whatever is dragged. 4b made the slot an ATTRACTOR, which left the naive O(n²) pair
    loop running over 433 nodes: 93,528 pairs per tick and 20.6M in the first settle.
    Pinning turns that into O(|sim| × n), and |sim| is 0 when nothing is focused."""

    def setUp(self):
        self.js = render_board._MG_ENHANCE_JS

    def _body(self, name, until):
        i = self.js.index("function %s(" % name)
        return self.js[i:self.js.index(until, i)]

    def test_tick_returns_immediately_when_nothing_simulates(self):
        body = self._body("tick", "// camera:")
        self.assertIn("if(!simList.length)return;", body)
        # the pair loop is over simList × nodes, NOT the old nodes × nodes triangle
        self.assertIn("for(var i=0;i<simList.length;i++)", body)
        self.assertNotIn("for(var j=i+1;j<nodes.length;j++)", body)

    def test_nothing_is_live_unless_focused_or_dragged(self):
        body = self._body("rebuildSim", "function tick(")
        self.assertIn("n===dragNode", body)                  # a dragged node simulates
        self.assertIn("p.length>0", body)                    # …and only a FOCUSED frame
        self.assertIn("isDescendant(n,p)", body)             # via 4a's containment test
        self.assertIn("n.pinned=", body)
        # the pin is EXACT — snapped to the slot with zero velocity, in all three axes,
        # because the slot is what defines a node's depth
        self.assertIn("n.x=n._lt.x;n.y=n._lt.y;n.z=n._lt.z||0;n.vx=0;n.vy=0;n.vz=0;", body)

    def test_settle_rebuilds_the_live_set_first(self):
        # …so a settle with nothing focused iterates 220 no-ops instead of 20.6M pairs.
        self.assertIn("function settle(iter){rebuildSim();", self.js)

    def test_only_live_nodes_integrate(self):
        body = self._body("tick", "// camera:")
        self.assertIn("simList.forEach(function(n){", body)  # integration is over simList
        self.assertIn("if(A.pinned&&B.pinned)return;", body)  # …and a pinned spring is inert

    def test_a_dragged_node_unpins_and_re_pins_where_it_was_dropped(self):
        # 4b's report flagged that pointerup cleared `fixed` — under pinning that would
        # leave the node drifting with nothing to settle it.
        self.assertIn('dragMode="node";dragNode=n;n.fixed=true;rebuildSim();', self.js)
        self.assertIn("dragNode._lt={x:dragNode.x,y:dragNode.y};dragNode.fixed=false;",
                      self.js)
        self.assertNotIn('if(dragMode==="node"&&dragNode){dragNode.fixed=false;reheat(0.7);}',
                         self.js)

    def test_rotation_is_a_camera_transform_not_a_layout_change(self):
        # auto-rotate advances yaw in step(); it must never reheat or re-settle.
        body = self._body("step", "function draw()")
        self.assertIn("else if(autoRotate){yaw+=0.0022;}", body)
        self.assertNotIn("reheat", body)
        self.assertNotIn("settle(", body)

    def test_focus_visibility_routes_through_isDescendant(self):
        # F4c.6: and it no longer compares `n.brain||""` against a focus minted with
        # `brain:"main"`, which used to hide the node you had just clicked.
        body = self._body("focusOkNode", "function nodeVisible")
        self.assertIn("return isDescendant(n,galaxyPath());", body)
        self.assertNotIn('n.brain||""', body)


class BrainlessFocusTest(unittest.TestCase):
    """F4c.6 — the node you click stays visible, through the Python twins of the same
    rules the canvas uses (`nodePath` normalises a missing brain to "main")."""

    def test_a_brainless_node_is_visible_when_focused_into(self):
        node = {"type": "task", "id": "t:1", "owner": "ann"}      # owner, no brain
        # navDescend mints exactly this focus for such a node
        path = render_board._galaxy_path({"kind": "brain", "owner": "ann",
                                          "brain": "main"})
        self.assertEqual(path, ["ann", "main"])
        self.assertTrue(render_board._is_descendant(node, path))
        # the old comparison was `n.brain || ""` against "main" — this is what it did
        self.assertNotEqual("", "main")

    def test_a_node_in_another_brain_is_still_hidden(self):
        node = {"type": "task", "id": "t:2", "owner": "ann", "brain": "side"}
        path = render_board._galaxy_path({"kind": "brain", "owner": "ann",
                                          "brain": "main"})
        self.assertFalse(render_board._is_descendant(node, path))


class SectorSeatingTest(unittest.TestCase):
    """F4c.2 — the rim was clumped because 297 of 379 tasks have zero shared artifacts
    and so shared ONE radius: 103 tasks in a 0.40 rad sector is 0.0039 rad each, closest
    pair 0.67 units. Angle cannot fix it (the sector gap caps widening at ~20%), so the
    band's second dimension does: theatre-seat rows."""

    def _one_category(self, n, artifacts=0):
        return [_lt("t:%d" % i, i, "orange", artifacts=artifacts)
                for i in range(1, n + 1)]

    def test_equal_entanglement_tasks_get_different_radii(self):
        lay = render_board._concentric_layout(self._one_category(40))
        radii = {round(_polar(p)[0], 3) for p in lay["task"].values()}
        self.assertGreater(len(radii), 1, "40 identical tasks must not share one circle")

    def test_a_hundred_task_category_is_not_clumped(self):
        lay = render_board._concentric_layout(self._one_category(100))
        pts = list(lay["task"].values())
        self.assertEqual(len(pts), 100)
        # no pair closer than the 6 units the clump measurement uses
        closest = min(math.hypot(a[0] - b[0], a[1] - b[1])
                      for i, a in enumerate(pts) for b in pts[i + 1:])
        self.assertGreater(closest, 6.0, "closest pair %.2f units" % closest)
        # …and no row is in clump territory (< 0.004 rad per task at one radius)
        rows = {}
        for p in pts:
            r, a = _polar(p)
            rows.setdefault(round(r, 3), []).append(a)
        for r, angs in rows.items():
            if len(angs) > 1:
                self.assertGreater(render_board.CAT_SPAN / float(len(angs)), 0.004,
                                   "row at r=%.1f holds %d" % (r, len(angs)))

    def test_seating_stays_outside_the_story_band(self):
        # The largest real category holds 103; that must not seat rows across the story
        # magnets. Rows step strictly inward and are never clamped, because clamping
        # would make two rows share a radius — the stacking this seating exists to stop.
        lay = render_board._concentric_layout(self._one_category(103))
        radii = [_polar(p)[0] for p in lay["task"].values()]
        self.assertLessEqual(max(radii), render_board.R_TASK_MAX + 1e-6)
        self.assertGreater(min(radii), render_board.R_STORY)

    def test_entanglement_still_decides_which_row_you_start_in(self):
        # The radial axis keeps its meaning: a more entangled task is further in, even
        # when a big zero-entanglement group spills inward past its own first row.
        tasks = self._one_category(30) + [_lt("t:900", 900, "orange", artifacts=5)]
        lay = render_board._concentric_layout(tasks)
        entangled = _polar(lay["task"]["t:900"])[0]
        for tid, p in lay["task"].items():
            if tid != "t:900":
                self.assertGreater(_polar(p)[0], entangled)

    def test_seating_is_deterministic(self):
        tasks = self._one_category(50)
        self.assertEqual(render_board._concentric_layout(tasks)["task"],
                         render_board._concentric_layout(list(reversed(tasks)))["task"])


class BoardRelationLabelTest(unittest.TestCase):
    """F4c.3/4 — the board card labelled every new kind as `related`, and repeated
    same-kind entries printed the word once per target."""

    def _line(self, frm=(), inn=()):
        return render_board._related_line({"from": list(frm), "in": list(inn)})

    def test_each_kind_and_its_inverse_reads_correctly(self):
        for kind, stored, derived in (
                ("depends-on", "depends on", "blocks"),
                ("parent", "parent", "children"),
                ("duplicates", "duplicates", "duplicates"),
                ("replaces", "replaces", "replaced by"),
                ("absorbed-by", "absorbed-by", "absorbed"),
                ("spawned-from", "from", "spawned")):
            out = self._line(frm=[{"seq": 7, "kind": kind}])
            self.assertTrue(out.startswith(stored + " "), "%s → %s" % (kind, out))
            inn = self._line(inn=[{"seq": 8, "kind": kind, "status": "open"}])
            self.assertTrue(inn.startswith(derived + " "), "%s ← %s" % (kind, inn))
        # …and nothing is labelled `related` any more just for being unrecognised-adjacent
        self.assertNotIn("related", self._line(frm=[{"seq": 7, "kind": "parent"}]))

    def test_an_unknown_kind_still_falls_back_to_related(self):
        self.assertIn("related", self._line(frm=[{"seq": 7, "kind": "invented-later"}]))

    def test_an_outgoing_spawned_from_keeps_its_qualifier(self):
        self.assertIn("(spawned-from)", self._line(frm=[{"seq": 7, "kind": "spawned-from"}]))

    def test_repeated_same_kind_entries_group_under_one_label(self):
        out = self._line(inn=[{"seq": 384, "kind": "parent", "status": "open"},
                              {"seq": 462, "kind": "parent", "status": "open"},
                              {"seq": 481, "kind": "parent", "status": "closed"}])
        self.assertEqual(out.count("children"), 1)          # the word once, not three times
        self.assertIn("#384", out)
        self.assertIn("#462", out)
        # the closed mark is PER TARGET and survives the grouping
        self.assertIn("#481</a> (closed)", out)
        self.assertNotIn("#384</a> (closed)", out)

    def test_a_run_of_one_renders_exactly_as_before(self):
        self.assertEqual(self._line(inn=[{"seq": 365, "kind": "spawned-from",
                                          "status": "open"}]),
                         render_board._rel_token({"seq": 365, "kind": "spawned-from",
                                                  "status": "open"}, True))

    def test_different_kinds_do_not_merge(self):
        out = self._line(inn=[{"seq": 1, "kind": "parent", "status": "open"},
                              {"seq": 2, "kind": "spawned-from", "status": "open"}])
        self.assertIn("children", out)
        self.assertIn("spawned", out)
        self.assertIn(" · ", out)                            # two separate runs


# ==================================================== Stage 4d: the spherical shells ===
# A shell has AREA, so a crowded category spreads over a patch of it rather than spilling
# inward — which is what lets radius carry entanglement and nothing else. One meaning per
# axis: longitude = category, latitude = spread within it, radius = entanglement.


def _r3(p):
    """Distance of a 3D slot from the layout centre."""
    return math.sqrt(p[0] * p[0] + p[1] * p[1] + p[2] * p[2])


def _d3(a, b):
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


class SphericalLayoutTest(unittest.TestCase):
    def _cat(self, n, cat="orange", artifacts=0, start=1):
        return [_lt("t:%d" % i, i, cat, artifacts=artifacts)
                for i in range(start, start + n)]

    def test_equal_entanglement_shares_a_radius_and_differs_in_angle(self):
        lay = render_board._concentric_layout(self._cat(40))
        sph = lay["sphere"]
        radii = {round(_r3(sph["t:%d" % i]), 6) for i in range(1, 41)}
        self.assertEqual(len(radii), 1, "one entanglement level is ONE shell")
        pts = [sph["t:%d" % i] for i in range(1, 41)]
        self.assertEqual(len({tuple(p) for p in pts}), 40)   # …separated by θ and φ

    def test_radius_is_a_function_of_entanglement_alone(self):
        # Across categories too: the shell a task sits on depends on nothing else.
        tasks = (self._cat(5, "orange", artifacts=2, start=1)
                 + self._cat(5, "green", artifacts=2, start=100)
                 + self._cat(30, "blue", artifacts=2, start=200))
        sph = render_board._concentric_layout(tasks)["sphere"]
        radii = {round(_r3(sph[t["id"]]), 6) for t in tasks}
        self.assertEqual(len(radii), 1)
        self.assertAlmostEqual(radii.pop(), render_board._entangle_radius(2), places=6)

    def test_a_large_category_does_not_move_anyone_off_their_shell(self):
        # The flaw this replaces: rows that spill inward make a zero-edge task sit
        # deeper than a well-connected one. On a shell there is nowhere to spill TO.
        small = render_board._concentric_layout(self._cat(3))["sphere"]
        big = render_board._concentric_layout(self._cat(120))["sphere"]
        r_small = _r3(small["t:1"])
        for i in range(1, 121):
            self.assertAlmostEqual(_r3(big["t:%d" % i]), r_small, places=6)

    def test_radius_still_falls_as_entanglement_rises(self):
        tasks = [_lt("t:1", 1, "orange", artifacts=0),
                 _lt("t:2", 2, "orange", artifacts=3),
                 _lt("t:3", 3, "orange", artifacts=6)]
        sph = render_board._concentric_layout(tasks)["sphere"]
        self.assertGreater(_r3(sph["t:1"]), _r3(sph["t:2"]))
        self.assertGreater(_r3(sph["t:2"]), _r3(sph["t:3"]))

    def test_depth_is_substantial(self):
        # "make it actually 3d": max|z| must be a real fraction of the layout radius.
        tasks = []
        for k, cat in enumerate(("orange", "green", "blue", "red")):
            tasks += self._cat(25, cat, start=1 + k * 100)
        sph = render_board._concentric_layout(tasks)["sphere"]
        zmax = max(abs(p[2]) for p in sph.values())
        self.assertGreater(zmax, render_board.R_TASK_MAX * 0.3)

    def test_no_crowding_in_three_space(self):
        tasks = []
        for k, cat in enumerate(("orange", "green", "blue")):
            tasks += self._cat(35, cat, start=1 + k * 100)
        pts = [render_board._concentric_layout(tasks)["sphere"][t["id"]] for t in tasks]
        closest = min(_d3(a, b)
                      for i, a in enumerate(pts) for b in pts[i + 1:])
        self.assertGreater(closest, 6.0, "closest 3D pair %.2f units" % closest)

    def test_neighbouring_shells_do_not_share_a_slot(self):
        # The log radius scale saturates, so two adjacent high entanglement levels sit
        # close together; a half-step of latitude keeps them off the same bearing.
        tasks = [_lt("t:1", 1, "orange", artifacts=5), _lt("t:2", 2, "orange", artifacts=6)]
        sph = render_board._concentric_layout(tasks)["sphere"]
        self.assertGreater(_d3(sph["t:1"], sph["t:2"]), 6.0)

    def test_a_parents_children_stay_a_compact_neighbourhood(self):
        # drawGalaxyBlobs hulls PROJECTED positions, so children scattered across the
        # shell would produce a hull that swallows half the graph.
        tasks = [_lt("t:1", 1, "green")] + [
            _lt("t:%d" % i, i, "orange", parent="t:1") for i in range(2, 10)]
        sph = render_board._concentric_layout(tasks)["sphere"]
        root = sph["t:1"]
        for i in range(2, 10):
            self.assertLessEqual(_d3(sph["t:%d" % i], root),
                                 render_board.R_CHILD + 1e-6)
        # …and the cluster is far smaller than the shell it sits on
        self.assertLess(2 * render_board.R_CHILD, render_board.R_GROUP)

    def test_a_nested_child_cluster_is_tighter_than_its_parent_s(self):
        tasks = [_lt("t:1", 1, "green"), _lt("t:2", 2, "green", parent="t:1"),
                 _lt("t:3", 3, "green", parent="t:2")]
        sph = render_board._concentric_layout(tasks)["sphere"]
        self.assertLess(_d3(sph["t:3"], sph["t:2"]), _d3(sph["t:2"], sph["t:1"]))

    def test_magnets_and_the_core_sit_on_their_own_equator(self):
        lay = render_board._concentric_layout(
            [_lt("t:1", 1, "green")], stories=["sig:story:S1"],
            repos={"sig:repo:one": ["sig:pr:a"]})
        sph = lay["sphere"]
        self.assertAlmostEqual(_r3(sph["cat:green"]), render_board.R_RIM, places=6)
        self.assertAlmostEqual(_r3(sph["sig:story:S1"]), render_board.R_STORY, places=6)
        self.assertAlmostEqual(_r3(sph["sig:repo:one"]), render_board.R_CORE, places=6)
        self.assertAlmostEqual(sph["cat:green"][1], 0.0, places=6)      # on the equator
        self.assertIn("sig:pr:a", sph)                                  # PR rides its repo
        self.assertLess(_d3(sph["sig:pr:a"], sph["sig:repo:one"]), 18.0)

    def test_the_shell_placement_is_deterministic(self):
        tasks = self._cat(60) + self._cat(20, "green", start=500)
        a = render_board._concentric_layout(tasks)["sphere"]
        b = render_board._concentric_layout(list(reversed(tasks)))["sphere"]
        self.assertEqual(a, b)

    def test_the_planar_placement_still_separates_tasks(self):
        # 2D keeps the row seating, which is what separates a crowded category on a
        # flat surface; the shell would collapse two tasks differing only in latitude.
        lay = render_board._concentric_layout(self._cat(100))
        pts = list(lay["task"].values())
        closest = min(math.hypot(a[0] - b[0], a[1] - b[1])
                      for i, a in enumerate(pts) for b in pts[i + 1:])
        self.assertGreater(closest, 6.0)


class ShellWiringTest(unittest.TestCase):
    """The shell has to reach the canvas, and the pin has to hold it."""

    def setUp(self):
        self.js = render_board._MG_ENHANCE_JS

    def test_the_slot_is_chosen_per_view_mode(self):
        self.assertIn('if(mode==="3d"&&n.lt3)return {x:(n.lt3[0]-SW/2)*SCL,'
                      'y:(n.lt3[1]-SH/2)*SCL,z:n.lt3[2]*SCL};', self.js)
        self.assertIn("var s=slotFor(n);n._lt=s;n.x=s.x;n.y=s.y;n.z=s.z;", self.js)

    def test_the_pin_holds_all_three_axes(self):
        self.assertIn("n.x=n._lt.x;n.y=n._lt.y;n.z=n._lt.z||0;", self.js)

    def test_no_z_flattening_spring_was_reintroduced(self):
        # The slot defines z. A spring pulling z toward 0 would drag a simulating node
        # off its shell — the layout spring has to target the slot's z, not the plane.
        self.assertNotIn("n.vz+=(-n.z)*LAYK", self.js)
        self.assertIn("n.vz+=((n._lt.z||0)-n.z)*LAYK", self.js)

    def test_a_random_z_spread_never_overwrites_a_slot(self):
        self.assertIn("nodes.forEach(function(n){if(n._lt)return;n.z=n.solo?0:", self.js)

    def test_the_mode_switch_reseeds_from_the_right_slot(self):
        self.assertIn("seedXY();", self.js)
        self.assertIn('if(m==="3d")seedZ();else flattenZ();', self.js)

    def test_nothing_simulates_when_nothing_is_focused(self):
        # 4c's guarantee is untouched by adding depth.
        self.assertIn("if(!simList.length)return;", self.js)
        self.assertIn("function settle(iter){rebuildSim();", self.js)


if __name__ == "__main__":
    unittest.main()
