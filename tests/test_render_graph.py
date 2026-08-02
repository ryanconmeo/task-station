"""Step 1 — the server-side clustered relations graph.

Covers the render-layer augmentation that sits ON TOP of the contract-pinned
`build_board_graph` (unchanged, see test_universal_graph.py):

  * `parse_pr_number`   — ADO/GitHub PR url → number (else trimmed url).
  * `shared_signal_groups` — group tasks by shared pr/story/repo VALUE, >=2 = a hub,
    exactly one = a singleton; stories keyed by story_ref id.
  * `build_render_graph` — typed STRING ids, category + signal HUB nodes, redundant
    touches-same collapse, degree, determinism, gated knowledge.
  * `render_board._minigraph` — the clustered 2D SVG + embedded mg-data JSON block.

Per-test temp-home isolation (the idiom shared with test_universal_graph.py); never
touches live data.
"""
import importlib.util
import os
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

_PR = "https://github.com/o/r/pull/1073"
_PR2 = "https://github.com/o/r/pull/99"


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-rgraph-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        for k in ("TASK_STATION_KNOWLEDGE_GRAPH", "TASK_STATION_OBSIDIAN_PROMPTS"):
            os.environ.pop(k, None)
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.DELEGATE_REGISTRY = os.path.join(self.tmp, "workers.json")
        # render_board resolves a task's [TAG] through `categories`, which caches the
        # config merge at IMPORT time — rebuild it against the empty temp home so
        # test_category_hub_label_is_short_form asserts the SHIPPED tag rather than
        # whatever this developer renamed the slot to in ~/.task-station/config.json.
        self._reload_categories()
        store.reset_cache()

    def tearDown(self):
        self._reload_categories()   # leave shipped defaults (temp home still active)
        os.environ.pop("TASK_STATION_HOME", None)
        store.reset_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def _reload_categories():
        import categories
        importlib.reload(categories)

    def _seed(self, title, **fields):
        t = ts.new_task(title, fields.pop("summary", "s " + title),
                        color=fields.pop("color", "green"),
                        effort=fields.pop("effort", "m"))
        for k, v in fields.items():
            t[k] = v
        ts.save_task(t)
        return ts.load_task(t["id"])

    def _seqs(self):
        ts.ensure_seqs()
        return {t["title"]: t.get("seq") for t in ts.all_tasks()}


# ------------------------------------------------------------ pr-number parser ---
class ParsePrNumberTest(unittest.TestCase):
    def test_github_pull(self):
        self.assertEqual(ts.parse_pr_number("https://github.com/o/r/pull/1073"), "1073")

    def test_ado_pullrequest(self):
        self.assertEqual(
            ts.parse_pr_number("https://dev.azure.com/Org/Proj/_git/repo/pullrequest/42"),
            "42")

    def test_trailing_number_fallback(self):
        self.assertEqual(ts.parse_pr_number("https://x/pr/7"), "7")

    def test_unparseable_returns_trimmed_url(self):
        self.assertEqual(ts.parse_pr_number("  not-a-pr  "), "not-a-pr")

    def test_empty(self):
        self.assertEqual(ts.parse_pr_number(""), "")


# ---------------------------------------------------------- shared_signal_groups ---
class SharedSignalGroupsTest(_Base):
    def test_ge2_group_and_singleton_split(self):
        self._seed("A", prs=[{"url": _PR}])
        self._seed("B", prs=[{"url": _PR}])
        self._seed("C", prs=[{"url": _PR2}])            # unique → singleton
        seqs = self._seqs()
        groups, labels, singletons = ts.shared_signal_groups(ts.all_tasks())
        self.assertEqual(groups.get(("pr", _PR)), sorted([seqs["A"], seqs["B"]]))
        self.assertEqual(labels[("pr", _PR)], "PR 1073")
        self.assertNotIn(("pr", _PR2), groups)          # shared by 1 → no group
        self.assertEqual(singletons.get(seqs["C"]), ["PR 99"])

    def test_repo_group_label(self):
        self._seed("A", projects=["projectname"])
        self._seed("B", projects=["projectname"])
        seqs = self._seqs()
        groups, labels, _ = ts.shared_signal_groups(ts.all_tasks())
        self.assertEqual(groups.get(("repo", "projectname")), sorted([seqs["A"], seqs["B"]]))
        self.assertEqual(labels[("repo", "projectname")], "repo projectname")

    def test_story_keyed_by_ref_id_across_url_forms(self):
        # bare id + full ADO url both resolve to story id 555 → one group.
        self._seed("A", stories=[{"url": "555"}])
        self._seed("B", stories=[{"url": "https://dev.azure.com/O/P/_workitems/edit/555"}])
        seqs = self._seqs()
        groups, labels, _ = ts.shared_signal_groups(ts.all_tasks())
        self.assertEqual(groups.get(("story", "555")), sorted([seqs["A"], seqs["B"]]))
        self.assertEqual(labels[("story", "555")], "story 555")

    def test_file_signal_never_grouped(self):
        self._seed("A", files=["/r/x.py"])
        self._seed("B", files=["/r/x.py"])
        self._seqs()
        groups, _, singletons = ts.shared_signal_groups(ts.all_tasks())
        self.assertEqual([k for k in groups if k[0] == "file"], [])
        self.assertEqual([v for s in singletons.values() for v in s
                          if v.startswith("file")], [])


# ------------------------------------------------------------ build_render_graph ---
class BuildRenderGraphTest(_Base):
    def _graph(self, knowledge=False):
        return ts.build_render_graph(ts.all_tasks(), knowledge=knowledge)

    def test_solo_board_equals_base_empty(self):
        self._seed("Lonely")
        self._seqs()
        g = self._graph()
        self.assertEqual(g, {"nodes": [], "edges": []})
        self.assertEqual(g, ts.build_board_graph(ts.all_tasks()))

    def test_signal_hub_ge2_rule_and_singleton(self):
        self._seed("A", prs=[{"url": _PR}])
        self._seed("B", prs=[{"url": _PR}])
        self._seed("C", prs=[{"url": _PR2}])
        seqs = self._seqs()
        g = self._graph()
        sig = [n for n in g["nodes"] if n["id"] == "sig:pr:%s" % _PR]
        self.assertEqual(len(sig), 1)
        self.assertEqual(sig[0]["type"], "signal")
        self.assertEqual(sig[0]["kind"], "pr")
        self.assertEqual(sig[0]["label"], "PR 1073")
        spokes = [e for e in g["edges"]
                  if e["kind"] == "pr" and e["b"] == "sig:pr:%s" % _PR]
        self.assertEqual(sorted(e["a"] for e in spokes),
                         ["t:%d" % seqs["A"], "t:%d" % seqs["B"]])
        # C shares nothing → not in the graph, but is a singleton.
        self.assertEqual([n for n in g["nodes"] if n["id"] == "t:%d" % seqs["C"]], [])
        self.assertEqual(g["singletons"].get(seqs["C"]), ["PR 99"])

    def test_category_hub_when_ge1_present(self):
        # A2: threshold is >=1 — every category present among the edge-graph's tasks gets a
        # hub (green has A+B, red has D alone → BOTH earn a hub now). The relation-free/solo
        # gate still yields no panel (see test_solo_board_equals_base_empty).
        self._seed("A", prs=[{"url": _PR}], color="green")
        self._seed("B", prs=[{"url": _PR}], color="green")
        self._seed("D", prs=[{"url": _PR}], color="red")
        self._seqs()
        g = self._graph()
        ids = {n["id"] for n in g["nodes"]}
        self.assertIn("cat:green", ids)
        self.assertIn("cat:red", ids)          # >=1 present task now earns a hub (was >=2)
        cat = [n for n in g["nodes"] if n["id"] == "cat:red"][0]
        self.assertEqual(cat["type"], "hub")
        self.assertEqual(cat["key"], "red")

    def test_all_node_ids_are_strings_with_types(self):
        self._seed("A", prs=[{"url": _PR}])
        self._seed("B", prs=[{"url": _PR}])
        self._seqs()
        g = self._graph()
        for n in g["nodes"]:
            self.assertIsInstance(n["id"], str)
            self.assertIn(n["type"], ("task", "hub", "signal"))
            self.assertTrue(n["id"].startswith(("t:", "cat:", "sig:")))
            self.assertIn("deg", n)
        for e in g["edges"]:
            self.assertIsInstance(e["a"], str)
            self.assertIsInstance(e["b"], str)

    def test_deterministic_build(self):
        self._seed("A", prs=[{"url": _PR}], projects=["projectname"])
        self._seed("B", prs=[{"url": _PR}], projects=["projectname"])
        self._seed("C", prs=[{"url": _PR}])
        self._seqs()
        self.assertEqual(self._graph(), self._graph())

    def test_touches_same_collapsed_but_file_only_kept(self):
        # A,B share a PR (→ hub, touches-same dropped). C,D share ONLY a file (kept).
        self._seed("A", prs=[{"url": _PR}])
        self._seed("B", prs=[{"url": _PR}])
        self._seed("C", files=["/r/only.py"])
        self._seed("D", files=["/r/only.py"])
        seqs = self._seqs()
        g = self._graph()
        ta, tb = "t:%d" % seqs["A"], "t:%d" % seqs["B"]
        tc, td = "t:%d" % seqs["C"], "t:%d" % seqs["D"]

        def _ts_between(x, y):
            return [e for e in g["edges"] if e["kind"] == "touches-same"
                    and {e["a"], e["b"]} == {x, y}]
        self.assertEqual(_ts_between(ta, tb), [])       # redundant w/ PR hub → dropped
        self.assertEqual(len(_ts_between(tc, td)), 1)   # file-only share → direct edge

    def test_knowledge_edges_gated(self):
        self._seed("A", prs=[{"url": _PR}], decisions=["per [[shared]]"])
        self._seed("B", prs=[{"url": _PR}], decisions=["see [[shared]]"])
        self._seqs()
        off = self._graph(knowledge=False)
        on = self._graph(knowledge=True)
        self.assertEqual([e for e in off["edges"] if e["kind"] == "related-knowledge"], [])
        kk = [e for e in on["edges"] if e["kind"] == "related-knowledge"]
        self.assertEqual(len(kk), 1)
        self.assertTrue(kk[0]["a"].startswith("t:") and kk[0]["b"].startswith("t:"))


# ------------------------------------------------------------------- minigraph ---
class MinigraphTest(_Base):
    def _graph(self):
        # 3 green tasks sharing one PR → cat hub + pr signal hub; A's title carries a
        # </script> breakout attempt to prove the mg-data escaping.
        self._seed("x</script><b>", prs=[{"url": _PR}])
        self._seed("B", prs=[{"url": _PR}])
        self._seed("C", prs=[{"url": _PR}])
        self._seqs()
        return ts.build_render_graph(ts.all_tasks())

    def test_empty_and_none_return_nothing(self):
        self.assertEqual(render_board._minigraph({"nodes": [], "edges": []}), [])
        self.assertEqual(render_board._minigraph(None), [])

    def test_clustered_markup_present(self):
        html = "".join(render_board._minigraph(self._graph()))
        self.assertIn('class="minigraph"', html)
        self.assertIn("<svg", html)
        self.assertIn("Task Graph", html)
        self.assertIn('class="mg-hub"', html)          # category hub group
        self.assertIn("<rect", html)                   # cat hub rounded rect
        self.assertIn("mg-sig-pr", html)               # pr signal-hub shape class

    def test_mg_data_block_present_and_script_safe(self):
        html = "".join(render_board._minigraph(self._graph()))
        self.assertIn('id="mg-data"', html)
        blob = html.split('id="mg-data">', 1)[1].split("</script>", 1)[0]
        self.assertNotIn("</script>", blob)            # no breakout in the JSON payload
        self.assertNotIn("<", blob)                    # every < escaped to \\u003c

    def test_deterministic_render(self):
        g = self._graph()
        self.assertEqual(render_board._minigraph(g), render_board._minigraph(g))

    def test_knowledge_legend_gated(self):
        html = "".join(render_board._minigraph(self._graph()))
        self.assertNotIn("shared knowledge", html)     # no co-citation edges here


# ---------------------------------------------- step 2: client enhancement layer ---
class BoardEnhancementTest(_Base):
    """The full board (write_board) must carry the live-canvas panel + the SEPARATE,
    guarded enhancement <script>, while keeping the static SVG as the no-JS fallback.
    Canvas interaction itself is a documented no-unit-test carve-out."""
    def _board_html(self):
        out = ts.write_board()
        with open(out, encoding="utf-8") as f:
            return f.read()

    def _seed_pr_pair(self, **kw):
        self._seed("A", prs=[{"url": _PR}], **kw)
        self._seed("B", prs=[{"url": _PR}], **kw)

    def test_canvas_controls_and_static_svg_all_present(self):
        self._seed_pr_pair()
        html = self._board_html()
        self.assertIn('class="mgcanvas"', html)        # live canvas
        self.assertIn('class="mg2d"', html)            # 2D toggle
        self.assertIn('class="mg3d"', html)            # 3D toggle
        self.assertIn('class="mgsvg"', html)           # static SVG fallback kept
        self.assertIn("<svg", html)
        self.assertIn('id="mg-data"', html)            # data the client reads
        self.assertIn("ts-board-graph", html)          # 2D/3D persistence key

    def test_enhancement_is_a_separate_guarded_script(self):
        self._seed_pr_pair()
        html = self._board_html()
        # wrapped so a parse failure can't break the page
        self.assertIn("try{(function(){", html)
        self.assertIn("}catch(e){}", html)
        # it lives in its OWN <script>, distinct from the behavior/theme script
        i = html.index("try{(function(){")
        seg = html[html.rfind("<script", 0, i):html.index("</script>", i)]
        self.assertIn("mgcanvas", seg)                 # this IS the graph script
        self.assertNotIn("ts-board-theme", seg)        # NOT merged with the theme toggle
        self.assertNotIn("board.rev.js", seg)          # NOT merged with the behavior script

    def test_no_external_asset_needles_with_enhancement(self):
        self._seed_pr_pair()
        html = self._board_html()
        for needle in ('src="http', "src='http", 'src="//', "src='//",
                       "<link ", "@import", "url(http", "//fonts."):
            self.assertNotIn(needle, html)

    def test_category_hub_label_is_short_form(self):
        # green → tag FEATURE. The node face + mg-data label are the compact 'dot TAG'
        # form; the full taxonomy description stays only in the SVG node <title>.
        self._seed_pr_pair(color="green")
        html = self._board_html()
        blob = html.split('id="mg-data">', 1)[1].split("</script>", 1)[0]
        self.assertIn("FEATURE", blob)                 # short tag in the mg-data label
        self.assertNotIn("[FEATURE]", blob)            # not the bracketed full form
        self.assertNotIn("feature work", blob)         # nor the full description
        self.assertIn("[FEATURE]", html)               # full form retained in the SVG title

    def test_solo_board_ships_no_enhancement_script(self):
        self._seed("Solo")
        html = self._board_html()
        self.assertNotIn('class="minigraph"', html)    # no panel at all
        self.assertNotIn("try{(function(){", html)     # and no inert enhancement JS

    # ---- step 3: naming/defaults + filter panel + tally + View-in-graph -----------
    def test_step3_panel_naming_and_defaults(self):
        self._seed_pr_pair()
        html = self._board_html()
        self.assertIn("Task Graph", html)                     # A1 rename
        self.assertNotIn("Task relations", html)
        self.assertIn('data-key="minigraph" open', html)      # A3 open by default
        self.assertIn('class="mg3d" aria-pressed="true"', html)   # A4 default 3D
        self.assertIn('class="mg2d" aria-pressed="false"', html)

    def test_step3_reset_only_no_resettle(self):
        self._seed_pr_pair()
        html = self._board_html()
        self.assertIn("mgreset", html)                        # B6: Reset kept
        self.assertNotIn("mgreheat", html)                    # B6: Re-settle removed
        self.assertNotIn("Re-settle", html)

    def test_step3_filter_panel_present(self):
        self._seed_pr_pair()
        html = self._board_html()
        self.assertIn('class="mgfilters"', html)              # C2 filter panel container

    def test_step3_view_in_graph_button_present(self):
        self._seed_pr_pair()
        html = self._board_html()
        self.assertIn('class="mgviewbtn"', html)              # D3 button
        self.assertIn("data-graph-seq=", html)                # keyed to the task seq
        self.assertIn("View in graph", html)
        # centerOnSeq is exposed by the enhancement script for the button to call
        self.assertIn("centerOnSeq", html)

    # ---- step 4 ---------------------------------------------------------------------
    def test_step4_filter_glyphs_and_planar_2d(self):
        self._seed_pr_pair()
        html = self._board_html()
        # G2: the filter swatches are built as actual node-shape inline-SVG glyphs.
        self.assertIn("glyphSVG", html)
        self.assertIn("rotate(45)", html)                     # PR diamond glyph
        self.assertIn("<polygon", html)                       # repo hexagon glyph
        # G1: the 2D layout flattens z (planar) rather than re-seeding it; z-reseed is 3D-only.
        self.assertIn("flattenZ", html)

    def test_step5_tally_removed_counts_in_filters(self):
        # §5: the separate Graph tally box is gone; every filter group/row shows a count
        # (mkRow appends a class="ct" count; the Category-hubs row now carries catHubN).
        self._seed_pr_pair()
        html = self._board_html()
        self.assertNotIn("Graph tally", html)                 # tally box removed
        self.assertNotIn('class="mgtally"', html)
        self.assertNotIn("updateTally", html)
        self.assertIn("catHubN", html)                        # Category-hubs row count wired in
        self.assertIn('ct.className="ct"', html)              # per-row count element (JS)

    def test_step4_view_in_graph_is_in_overview_not_sessions(self):
        self._seed_pr_pair()
        html = self._board_html()
        # B-VG: the button lives in the Overview (before the Sessions section), not buried
        # in Sessions. Its hidden wrapper is un-hidden by the enhancement for graph nodes.
        self.assertIn('class="mgviewwrap"', html)
        self.assertLess(html.index('class="mgviewbtn"'),
                        html.index('data-key="sec-sessions'))

    # ---- step 6: pan / momentum / hard-reset / dynamic-zoom / per-signal filters / perf --
    def test_step6_2d_pan_and_projection_offsets(self):
        self._seed_pr_pair()
        html = self._board_html()
        self.assertIn("sx:cx+px*zoom+panX", html)              # pan applied in 2D projection
        self.assertIn("(mx-cx-panX)/zoom", html)               # and inverted in unproject
        self.assertIn('dragMode="pan"', html)                  # empty 2D drag = pan

    def test_step6_rotate_momentum_and_no_freeze_hold(self):
        self._seed_pr_pair()
        html = self._board_html()
        self.assertIn("yawVel", html)
        self.assertIn("yawVel*=0.94;pitchVel*=0.94", html)     # momentum decay
        # the old hard 3-second rotate freeze is gone (replaced by momentum → resume).
        self.assertNotIn("performance.now()>rotateHoldUntil", html)

    def test_step6_hard_reset_settles_and_fits(self):
        self._seed_pr_pair()
        html = self._board_html()
        self.assertIn("function hardReset(", html)
        self.assertIn("function settle(", html)                # synchronous settle to rest
        self.assertIn("function fitView(", html)               # re-fit after reset
        self.assertIn("if(resetBtn)resetBtn.addEventListener(\"click\",hardReset)", html)

    def test_step6_dynamic_zoom_caps_from_graph_size(self):
        self._seed_pr_pair()
        html = self._board_html()
        self.assertIn("ZMIN=Math.max(0.05,base*0.5)", html)    # zoom-out floor scales to size
        self.assertIn("ZMAX=Math.max(2.6,base*6)", html)
        self.assertIn("Math.max(ZMIN,Math.min(ZMAX,zoom*", html)  # wheel uses the dynamic caps

    def test_solo_nodes_and_new_filter_groups(self):
        # every board task NOT drawn in the graph rides along in mg-data as a static
        # solo node (default-off "unlinked tasks" filter), and the rail gains a task
        # lifecycle status group; solo nodes never join the physics.
        self._seed_pr_pair()
        self._seed("relation-free loner")                       # third task, no relations
        html = self._board_html()
        self.assertIn('"solo":[', html)                         # solo pool embedded
        self.assertIn("unlinked tasks", html)                   # the default-off filter row
        self.assertIn("soloRow.set(false)", html)               # default OFF (and on Reset)
        self.assertIn('mkGroup("Tasks · status")', html)        # lifecycle status group
        self.assertIn("filt.status[n.status]===false", html)    # status drives visibility
        self.assertIn("if(nodes[i].solo||nodes[j].solo)continue", html)  # no forces on solo

    def test_hover_info_panel_never_reflows_the_canvas(self):
        # the node-details panel is FIXED-height + scrolls internally, and the canvas
        # buffer follows any layout resize via a ResizeObserver — hover must never
        # resize/jump the graph (the rail's height drives the canvas height).
        self._seed_pr_pair()
        html = self._board_html()
        self.assertIn("height:185px", html)                     # .mginfo fixed height
        self.assertIn("overflow-y:auto", html)
        self.assertIn("new ResizeObserver", html)               # buffer tracks layout resizes

    def test_step6_per_signal_filters_and_toggle_all(self):
        self._seed_pr_pair()
        html = self._board_html()
        self.assertIn("filt.sig[s.id]=true", html)             # one filter per signal NODE
        self.assertIn('all.className="mgallbtn"', html)        # per-group show/hide-all toggle (JS)
        self.assertIn("filt.sig[n.id]!==false", html)          # visibility keyed per node

    def test_step6_graph_defaults_reset_on_manual_load(self):
        self._seed_pr_pair()
        html = self._board_html()
        # graph view (mode/rotate) only RESTORES on an auto-refresh; manual/fresh = defaults.
        self.assertIn("if(isAuto){try{var sv=JSON.parse(localStorage.getItem(GKEY)", html)
        self.assertIn("window.__TS_ISAUTO", html)              # behavior script exposes isAuto

    def test_step6_performance_mode_in_graph(self):
        self._seed_pr_pair()
        html = self._board_html()
        self.assertIn('var perfLow=root.getAttribute("data-perf")==="low"', html)
        self.assertIn('attributeFilter:["data-theme","data-perf"]', html)  # reacts live
        self.assertIn("function kick(){if(perfLow)", html)     # on-demand render in low mode

    # ---- step 7: recenter-fit / smart toggle-all / reset-filters / labels -----------
    def test_step7_fit_recenters_centroid(self):
        self._seed_pr_pair()
        html = self._board_html()
        # fitView recenters the layout's centroid to the origin (fixes 3D vertical bias +
        # 2D off-centre scatter) and fits BOTH axes so every node is framed.
        self.assertIn("nodes[i].x-=mx", html)
        self.assertIn("Math.min((Wc||600)/spanX,(Hc||520)/spanY)", html)

    def test_step7_smart_toggle_all_and_reset_filters(self):
        self._seed_pr_pair()
        html = self._board_html()
        self.assertIn('all.textContent=allOn()?"hide all":"show all"', html)  # follows state
        self.assertIn("function resetFilters(", html)          # reset turns all filters on
        self.assertIn("resetFilters();", html)                 # …and hardReset calls it

    def test_step7_labels_and_hint(self):
        self._seed_pr_pair()
        html = self._board_html()
        self.assertIn("performance: high", html)               # spelled out (not "perf:")
        self.assertNotIn("perf: high", html)
        self.assertIn("click empty space to toggle rotate", html)
        self.assertNotIn("fling to spin", html)

    def test_step7_canvas_matches_rail_height(self):
        self._seed_pr_pair()
        html = self._board_html()
        self.assertIn("align-items:stretch", html)             # stage stretches both columns
        self.assertIn("height:100%;min-height:520px", html)    # canvas fills the rail height


if __name__ == "__main__":
    unittest.main()
