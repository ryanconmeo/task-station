"""Step 78 stage 5b — the two-plane view, DRAWN.

Stage 5a computed the knowledge plane's data. This stage puts it on the canvas as a
SECOND LITERAL PLANE stacked over the task sphere, with a CAMERA PAN between them: same
yaw, same pitch, same zoom, same scope — only the look-at point moves. Everything below
tests that claim rather than the markup that expresses it.

  * WORLD PLACEMENT — the corpus is flat, sits ABOVE the task layout (up is negative y),
    and the gap is measured off the task layout's own extent rather than pinned to a
    number, so a three-task board and a four-hundred-task board both read as two planes.
  * THE GAP — the named requirement: no drawn line whose endpoints are on different
    planes has any kind other than the three that may cross. Tested by feeding the
    renderer an ILLEGAL crossing and watching it disappear, not by reading a comment.
  * THE PAN — it is a pan: nothing in it writes yaw, pitch or zoom, it reuses the pivot
    tween that already exists, and reduced-motion / low-performance both snap.
  * 3D ONLY — in 2D the plane is not drawn and its control is hidden, the same call the
    auto-rotate button makes.
  * HIGHLIGHT, NEVER FILTER — the corpus is global and whole: every note in the vault is
    a node however few of them any task cites.
  * FLAT FRAME COST — notes carry a computed slot (so they are pinned, never simulated),
    and the plane the camera is not on draws low-poly with no text measurement.
  * THE PARITY LAW — with no vault the graph panel is byte-identical to the same board
    with the plane hard off, and carries no trace of the feature.

Fixture slugs, areas and types are invented; every vault is built inside the test's own
tmpdir and reaches the code only through `config.set("obsidian_vault", …)`.
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

import config  # noqa: E402
import knowledge  # noqa: E402
import render_board  # noqa: E402
import store  # noqa: E402

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)

_PLANE_ENV = "TASK_STATION_KNOWLEDGE_PLANE"

_LEDGER = "ledger-rounding-gotcha"
_QUEUE = "queue-retry-design"
_WARMUP = "cache-warmup-how-to"
_GLOSSARY = "orphan-glossary"
_SCRATCH = "unfiled-scratch"
_BARE = "bare-jotting"
_CORPUS_SIZE = 6

_MGDATA_RE = re.compile(r'<script type="application/json" id="mg-data">(.*?)</script>',
                        re.S)
_PANEL_RE = re.compile(r'<details class="minigraph".*?</details>', re.S)


def _blob(html):
    """The raw mg-data payload as it was emitted — for byte comparisons."""
    m = _MGDATA_RE.search(html)
    return m.group(1) if m else None


def _data(html):
    raw = _blob(html)
    assert raw is not None, "mg-data block missing"
    return json.loads(raw.replace("\\u003c", "<").replace("\\u0026", "&"))


def _planes(data):
    """{node id: plane} for the emitted graph, read from the blob itself."""
    return {n["id"]: n.get("plane") for n in data["nodes"]}


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-2plane-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        self._saved = {k: os.environ.get(k) for k in
                       (_PLANE_ENV, "TASK_STATION_KNOWLEDGE_GRAPH",
                        "TASK_STATION_INTERBRAIN")}
        for k in self._saved:
            os.environ.pop(k, None)
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.DELEGATE_REGISTRY = os.path.join(self.tmp, "workers.json")
        store.reset_cache()

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        store.reset_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- fixture vault ---------------------------------------------------------
    def _write_note(self, slug, body="", subdir="notes", **fm):
        d = os.path.join(self.tmp, "vault", subdir)
        os.makedirs(d, exist_ok=True)
        head = ""
        if fm:
            head = "\n".join(["---"] + ["%s: %s" % (k, v) for k, v in sorted(fm.items())]
                             + ["---", ""])
        with open(os.path.join(d, slug + ".md"), "w", encoding="utf-8") as f:
            f.write(head + body)

    def _configure_vault(self):
        vault = os.path.join(self.tmp, "vault")
        os.makedirs(vault, exist_ok=True)
        config.set("obsidian_vault", vault)
        return vault

    def _corpus(self, source_seq=None):
        """Six notes: a mutual pair, a one-way link into it, an orphan, a note with no
        area, and one with no frontmatter at all. Degrees therefore differ, which is what
        makes the radius assertion below mean something."""
        self._write_note(_LEDGER, "Rounds down. See [[%s]].\n" % _QUEUE,
                         type="gotcha", area="platform",
                         description="why the ledger rounds down")
        extra = {"source": "task-station:%d" % source_seq} if source_seq else {}
        self._write_note(_QUEUE, "# Retry design\n\nBack to [[%s]].\n" % _LEDGER,
                         type="architecture", area="platform",
                         description="how retries back off", **extra)
        self._write_note(_WARMUP, "Warm it. Depends on [[%s]].\n" % _QUEUE,
                         type="how-to", area="tooling", description="warm the cache")
        self._write_note(_GLOSSARY, "Terms, linked from nowhere.\n",
                         type="reference", area="tooling", description="terms")
        self._write_note(_SCRATCH, "No area at all.\n",
                         type="reference", description="a scratch note")
        self._write_note(_BARE, "Just prose, no frontmatter block.\n")
        return self._configure_vault()

    # ---- fixture store ---------------------------------------------------------
    def _seed(self, title, **fields):
        t = ts.new_task(title, fields.pop("summary", "s " + title),
                        color=fields.pop("color", "green"), effort="m")
        for k, v in fields.items():
            t[k] = v
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _lineage(self, child, parent):
        child["related"] = [{"id": parent["id"], "seq": parent.get("seq"),
                             "kind": "spawned-from", "ts": ts._now()}]
        ts.save_task(child)

    def _two_plane_board(self):
        """A store and a vault that produce BOTH planes and a live edge on each side:
        lineage inside the task plane, wikilinks inside the knowledge plane, a `cites`
        and a `distilled-from` across the gap."""
        parent = self._seed("Ledger work")
        child = self._seed("Ledger follow-up", goal="per [[%s]] we retry" % _QUEUE)
        self._lineage(ts.load_task(child["id"]), ts.load_task(parent["id"]))
        self._corpus(source_seq=parent.get("seq"))
        return self._render()

    def _render(self):
        out = ts.write_board()
        with open(out, encoding="utf-8") as f:
            return f.read()

    def _panel(self, html):
        m = _PANEL_RE.search(html)
        return m.group(0) if m else ""


# ============================================================ 1. WORLD PLACEMENT ===
class PlacementTest(_Base):
    """The corpus is a flat plane, above the task layout, separated by a gap sized off
    the task layout's own extent."""

    def test_the_gap_scales_with_the_task_layout_rather_than_being_a_number(self):
        near = {"t:1": (360.0, 260.0 + 40.0, 0.0)}      # everything close to the equator
        far = {"t:1": (360.0, 260.0 + 200.0, 0.0)}      # a full-size sphere
        self.assertLess(render_board._plane_offset(near, cy=260.0),
                        render_board._plane_offset(far, cy=260.0))
        # …and a degenerate layout still separates: the floor is the innermost shell.
        self.assertGreaterEqual(render_board._plane_offset({"t:1": (360.0, 260.0, 0.0)},
                                                           cy=260.0),
                                render_board.R_TASK_MIN)
        # an empty layout is not an error — it falls back to the rim radius
        self.assertGreater(render_board._plane_offset({}, cy=260.0), 0.0)

    def test_every_note_shares_one_y_and_spreads_over_x_and_z(self):
        data = _data(self._two_plane_board())
        notes = [n for n in data["nodes"] if n["type"] == "note"]
        self.assertEqual(len(notes), _CORPUS_SIZE)
        ys = {n["lt3"][1] for n in notes}
        self.assertEqual(len(ys), 1, "the knowledge plane must be FLAT: one y for all")
        self.assertGreater(len({n["lt3"][0] for n in notes}), 1)   # spread in x
        self.assertGreater(len({n["lt3"][2] for n in notes}), 1)   # …and in z

    def test_the_corpus_sits_above_the_task_layout_with_a_real_gap(self):
        data = _data(self._two_plane_board())
        note_y = [n["lt3"][1] for n in data["nodes"] if n["type"] == "note"][0]
        task_y = [n["lt3"][1] for n in data["nodes"]
                  if n["type"] in ("task", "hub") and n.get("lt3")]
        self.assertTrue(task_y, "fixture must draw the task plane too")
        # UP IS NEGATIVE y, so the whole corpus sits strictly above everything on the
        # task plane — the planes cannot interpenetrate.
        self.assertLess(note_y, min(task_y))
        half = max(abs(y - 260.0) for y in task_y)
        self.assertGreaterEqual(260.0 - note_y, 1.5 * half)

    def test_the_two_planes_share_the_same_x_z_extent(self):
        # The corpus is offset in y ALONE; it is not shrunk, pushed sideways, or given a
        # coordinate space of its own.
        data = _data(self._two_plane_board())
        notes = [n for n in data["nodes"] if n["type"] == "note"]
        for n in notes:
            self.assertLessEqual(abs(n["lt3"][0] - 360.0), render_board.R_NOTE_RIM + 1)
            self.assertLessEqual(abs(n["lt3"][2]), render_board.R_NOTE_RIM + 1)

    def test_a_better_linked_note_sits_closer_to_the_middle(self):
        # This is the RENDERER's half of the placement contract: it must hand the layout
        # the degree map built from the graph's own note links. Forget that and every
        # note lands on the rim — which is exactly what this asserts cannot happen.
        data = _data(self._two_plane_board())
        pos = {n["slug"]: (n["lt3"][0] - 360.0, n["lt3"][2])
               for n in data["nodes"] if n["type"] == "note"}
        r = {s: math.hypot(*p) for s, p in pos.items()}
        self.assertLess(r[_QUEUE], r[_GLOSSARY])       # 2 links vs none
        self.assertAlmostEqual(r[_GLOSSARY], render_board.R_NOTE_RIM, places=0)


# ==================================================================== 2. THE GAP ===
class TheGapTest(_Base):
    """Exactly three kinds may join the planes. Enforced where the lines are made, so an
    illegal crossing is DROPPED rather than merely discouraged."""

    def _hand_built(self):
        """A graph the renderer would never build itself, carrying one legal crossing and
        one illegal one between the SAME pair of planes."""
        return {"nodes": [
            {"id": "t:1", "type": "task", "seq": 1, "title": "A", "color": "green",
             "status": "new", "deg": 2, "plane": "task"},
            {"id": "t:2", "type": "task", "seq": 2, "title": "B", "color": "green",
             "status": "new", "deg": 2, "plane": "task"},
            {"id": "n:alpha-jotting", "type": "note", "slug": "alpha-jotting",
             "title": "Alpha", "kind": "gotcha", "area": "platform", "deg": 2,
             "plane": "knowledge"},
        ], "edges": [
            {"a": "t:1", "b": "t:2", "kind": "spawned-from", "dir": "a->b",
             "weight": 1, "via": []},
            {"a": "t:1", "b": "n:alpha-jotting", "kind": "cites", "dir": "a->b",
             "weight": 1, "via": []},
            {"a": "t:2", "b": "n:alpha-jotting", "kind": "spawned-from", "dir": "a->b",
             "weight": 1, "via": []},
        ]}

    def test_an_illegal_crossing_is_dropped_and_a_legal_one_survives(self):
        data = _data("".join(render_board._minigraph(self._hand_built())))
        pairs = {(e["a"], e["b"], e["kind"]) for e in data["edges"]}
        self.assertIn(("t:1", "n:alpha-jotting", "cites"), pairs)
        self.assertNotIn(("t:2", "n:alpha-jotting", "spawned-from"), pairs)
        self.assertIn(("t:1", "t:2", "spawned-from"), pairs)   # …inside a plane it stays

    def test_no_drawn_line_crosses_the_gap_with_any_other_kind(self):
        data = _data(self._two_plane_board())
        plane = _planes(data)
        self.assertTrue(all(plane.values()), "every drawn node names its plane")
        crossing = [e for e in data["edges"] if plane[e["a"]] != plane[e["b"]]]
        self.assertTrue(crossing, "the fixture must actually cross the gap")
        for e in crossing:
            self.assertIn(e["kind"], knowledge.CROSS_PLANE_KINDS)

    def test_each_plane_still_carries_an_edge_of_its_own(self):
        # …so the assertion above cannot pass on a graph that is empty on one side.
        data = _data(self._two_plane_board())
        plane = _planes(data)
        within = {}
        for e in data["edges"]:
            if plane[e["a"]] == plane[e["b"]]:
                within.setdefault(plane[e["a"]], set()).add(e["kind"])
        self.assertIn("spawned-from", within.get("task", set()))
        self.assertIn(knowledge.NOTE_EDGE_KIND, within.get("knowledge", set()))

    def test_both_crossing_directions_are_drawn(self):
        data = _data(self._two_plane_board())
        kinds = {e["kind"] for e in data["edges"]}
        self.assertIn("cites", kinds)                  # a task points UP at what it read
        self.assertIn("distilled-from", kinds)         # a note points DOWN at its source

    def test_the_static_svg_is_flat_and_task_only(self):
        # Two stacked planes seen from directly above are one plane, so the no-JS view
        # draws neither the corpus nor anything joining it: one <line>, the lineage edge.
        html = "".join(render_board._minigraph(self._hand_built()))
        svg = html[html.index("<svg"):html.index("</svg>")]
        self.assertNotIn("n:alpha-jotting", svg)
        self.assertNotIn("Alpha", svg)
        self.assertEqual(svg.count('<line class="mg-edge'), 1)

    def test_the_client_refuses_the_same_edge_again(self):
        # The server is one gate; the canvas is the other, because the requirement is
        # about what is DRAWN and the canvas draws from the blob independently.
        js = render_board._MG_ENHANCE_JS
        self.assertIn("var XPLANE=", js)
        self.assertIn("if(crossesGap(A,B)&&!XPLANE[e.kind])return;", js)
        for kind in knowledge.CROSS_PLANE_KINDS:
            self.assertIn('"%s":1' % kind, js)


# ==================================================================== 3. THE PAN ===
class PanTest(_Base):
    """Moving between the planes changes WHERE THE CAMERA LOOKS and nothing else."""

    def setUp(self):
        super().setUp()
        self.js = render_board._MG_ENHANCE_JS

    def _fn(self, name, arity="p"):
        m = re.search(r"function %s\(%s\)\{(.*?)\n  \}" % (name, arity), self.js, re.S)
        self.assertIsNotNone(m, "%s() not found in the enhancement" % name)
        return m.group(1)

    def test_the_pan_never_touches_yaw_pitch_or_zoom(self):
        # The distinction the whole feature rests on: a pan that also reframed the view
        # would read as a zoom or a drill-down, which is what this design rejected.
        body = self._fn("setPlane")
        for forbidden in ("yaw", "pitch", "zoom", "fitView", "settle"):
            self.assertNotIn(forbidden, body,
                             "the pan must not touch `%s`" % forbidden)

    def test_the_pan_moves_the_pivot_and_reuses_the_existing_tween(self):
        self.assertIn('var y=(planeFocus==="knowledge")?planeY:', self.js)
        self.assertIn("pivotTarget.y=aim.y;", self.js)
        # no second animation style: the pivot ease that was already here does the work.
        self.assertEqual(self.js.count("pivot.y+=(pivotTarget.y-pivot.y)*e"), 1)

    def test_reduced_motion_and_low_performance_both_snap(self):
        self.assertIn("var e=(perfLow||reduce)?1:0.12;", self.js)

    def test_the_plane_position_is_measured_not_remembered(self):
        # fitView shifts EVERY node to recentre the pair, so a remembered constant would
        # go stale; the return to the task plane goes through the pivot, never a reshift.
        self.assertIn("function measurePlanes(", self.js)
        self.assertIn("measurePlanes();", self.js)

    def test_selecting_a_task_rises_but_never_filters(self):
        self.assertIn("function planeOnSelect(", self.js)
        self.assertIn("planeOnSelect(n);", self.js)
        body = self._fn("planeOnSelect", "n")
        self.assertIn('setPlane("knowledge")', body)
        for forbidden in ("filt.", "nodeVisible", "hidden"):
            self.assertNotIn(forbidden, body,
                             "selection highlights the cited notes; it never filters")

    def test_the_control_ships_with_a_corpus(self):
        # …and its absence WITHOUT one is ParityTest's job, since that is the parity law.
        panel = self._panel(self._two_plane_board())
        self.assertIn('class="mgbtn mgplane"', panel)
        self.assertIn("Notes layer", panel)

    def test_the_control_is_hidden_in_2d(self):
        # exactly as auto-rotate hides itself — and the camera parks back on the task
        # layer, so it is never aimed at a plane that is not drawn.
        self.assertIn('if(planeBtn)planeBtn.style.display=(m==="3d")?"":"none";', self.js)
        self.assertIn('if(m!=="3d")planeFocus="task";', self.js)

    def test_arrow_keys_pan_and_the_focus_stop_is_not_the_aria_hidden_canvas(self):
        html = self._two_plane_board()
        self.assertIn('<canvas class="mgcanvas" aria-hidden="true">', html)
        panel = self._panel(html)
        self.assertNotIn("tabindex", panel)            # added by the client, not shipped
        self.assertIn('kwrap.setAttribute("tabindex","0")', self.js)
        self.assertIn('if(ev.key==="ArrowUp"){setPlane("knowledge");', self.js)
        self.assertIn('else if(ev.key==="ArrowDown"){setPlane("task");', self.js)


# ============================================== 4. HIGHLIGHT, NEVER FILTER ===
class GlobalCorpusTest(_Base):
    """The corpus is global and stays whole. Nothing about a selection reduces it."""

    def test_every_note_is_drawn_however_few_are_cited(self):
        data = _data(self._two_plane_board())
        slugs = {n["slug"] for n in data["nodes"] if n["type"] == "note"}
        self.assertEqual(len(slugs), _CORPUS_SIZE)
        cited = {e["b"][2:] for e in data["edges"] if e["kind"] == "cites"}
        self.assertEqual(len(cited), 1)                # …one of six, and six are drawn
        self.assertTrue(cited <= slugs)

    def test_note_visibility_reads_only_the_mode_and_its_own_switch(self):
        js = render_board._MG_ENHANCE_JS
        self.assertIn('if(n.type==="note")return planeDrawn(n)&&filt.note!==false;', js)
        # a note is DIMMED, not removed, when something else is selected: that is nodeOn,
        # which is applied to alpha rather than to visibility.
        self.assertIn("on=nodeOn(n,foc,kp)", js)

    def test_a_note_carries_no_category_colour(self):
        data = _data(self._two_plane_board())
        for n in data["nodes"]:
            if n["type"] == "note":
                self.assertNotIn("color", n)
        self.assertIn("function noteColor(", render_board._MG_ENHANCE_JS)

    def test_no_absolute_path_reaches_the_graph(self):
        # The note record carries one so an open action COULD be built; putting it in the
        # blob would write a home-directory fingerprint into a syncable artifact. Scoped
        # to the graph panel: the config help panel has always echoed the vault path the
        # user configured, which is a different thing and not this stage's to change.
        html = self._two_plane_board()
        for n in _data(html)["nodes"]:
            self.assertNotIn("path", n)
        self.assertNotIn(self.tmp, self._panel(html))


# =========================================================== 5. FLAT FRAME COST ===
class FrameCostTest(_Base):
    """A hundred more nodes must not cost a frame anything."""

    def test_every_note_arrives_with_a_computed_slot(self):
        # A node WITH a slot is pinned by seedXY/rebuildSim and never seeded or simulated;
        # one without would fall into the z-spread and then into the physics.
        data = _data(self._two_plane_board())
        for n in data["nodes"]:
            if n["type"] == "note":
                self.assertEqual(len(n.get("lt3") or []), 3)

    def test_a_note_can_never_join_the_simulation(self):
        js = render_board._MG_ENHANCE_JS
        # the live test is task-only, so a note is always pinned…
        self.assertIn('n.type==="task"&&isDescendant(n,p)', js)
        # …and the pair loop skips the other plane outright, keeping the cost flat.
        self.assertIn("nodePlane(B)!==nodePlane(A))continue;", js)

    def test_the_unfocused_plane_draws_low_poly(self):
        js = render_board._MG_ENHANCE_JS
        self.assertIn("var lowPlane=", js)
        self.assertIn("if(r>=6.5&&!low){", js)         # no label on the far plane
        self.assertIn("if(on&&!low){", js)             # …nor on its signal hubs
        # the hub's measureText is what sizes its plate, so the low path must not run it
        m = re.search(r"if\(low\)\{ctx\.fillStyle=rootVar\(\"--panel\"\).*?\}\n", js, re.S)
        self.assertIsNotNone(m, "low-poly hub path not found")
        self.assertNotIn("measureText", m.group(0))


# ==================================================== 6. THE PANEL GATE (§0.1) ===
class PanelGateTest(_Base):
    """A corpus is a plane in its own right: it entitles the panel on its own."""

    def test_a_store_with_no_task_relation_still_gets_its_corpus(self):
        self._seed("Ledger work")
        self._seed("Report work")
        self._corpus()
        html = self._render()
        self.assertIn('class="minigraph"', html)
        data = _data(html)
        self.assertEqual(len([n for n in data["nodes"] if n["type"] == "note"]),
                         _CORPUS_SIZE)
        self.assertTrue([n for n in data["nodes"] if n["type"] == "task"])
        self.assertEqual([e for e in data["edges"] if e["kind"] == "membership"], [])

    def test_a_relation_free_store_with_no_vault_still_has_no_panel(self):
        self._seed("Ledger work")
        self._seed("Report work")
        html = self._render()
        self.assertNotIn('class="minigraph"', html)
        self.assertNotIn("try{(function(){", html)     # …and no inert enhancement JS

    def test_the_corpus_is_counted_in_the_summary(self):
        html = self._two_plane_board()
        self.assertIn("note(s)", self._panel(html))


# ============================================================= 7. THE PARITY LAW ===
class ParityTest(_Base):
    """With no vault, the graph panel is byte-identical to the same board with the plane
    hard off — and carries no trace of the feature at all."""

    def _seed_pair(self):
        parent = self._seed("Ledger work")
        child = self._seed("Ledger follow-up")
        self._lineage(ts.load_task(child["id"]), ts.load_task(parent["id"]))

    def test_auto_without_a_vault_equals_the_gate_hard_off(self):
        self._seed_pair()
        auto = self._panel(self._render())             # auto + no vault → off
        os.environ[_PLANE_ENV] = "off"
        hard_off = self._panel(self._render())
        self.assertTrue(auto)
        self.assertEqual(auto, hard_off)

    def test_the_gate_off_with_a_full_vault_is_the_same_bytes_again(self):
        self._seed_pair()
        auto = self._panel(self._render())
        self._corpus()
        os.environ[_PLANE_ENV] = "off"
        self.assertEqual(auto, self._panel(self._render()))

    def test_the_off_render_carries_no_footprint_of_the_plane(self):
        # Scoped to the graph panel, which is what the parity law is about: the shared
        # enhancement script grows with every stage and stays INERT, exactly as the
        # Interbrain galaxy code does with federation off.
        self._seed_pair()
        self._corpus()
        os.environ[_PLANE_ENV] = "off"
        html = self._render()
        panel = self._panel(html)
        self.assertTrue(panel)
        self.assertNotIn("mgplane", panel)             # no control
        self.assertNotIn("Notes layer", panel)
        self.assertNotIn("note(s)", panel)             # …and no count in the summary
        blob = _blob(html)
        self.assertNotIn('"note"', blob)
        self.assertNotIn('"plane"', blob)
        for slug in (_LEDGER, _QUEUE, _GLOSSARY):
            self.assertNotIn(slug, panel)

    def test_turning_it_on_is_what_changes_the_blob(self):
        # the other half: parity is a property of the gate, not of the fixture.
        self._seed_pair()
        self._corpus()
        os.environ[_PLANE_ENV] = "off"
        off = _blob(self._render())
        os.environ[_PLANE_ENV] = "on"
        on = _blob(self._render())
        self.assertNotEqual(off, on)
        self.assertIn('"plane":"knowledge"', on)

    def test_the_two_plane_render_is_deterministic(self):
        self._two_plane_board()
        self.assertEqual(self._panel(self._render()), self._panel(self._render()))


# ========================================================= 8. THE EDGE REGISTRY ===
class EdgeRegistryTest(_Base):
    """Every class the renderer can put an edge in needs a filter row, or that edge draws
    with no way to turn it off. The exact check lives in test_graph_drilldown; this names
    the two classes this stage added."""

    def test_the_new_classes_are_registered_and_filterable(self):
        js = render_board._MG_ENHANCE_JS
        m = re.search(r"var EK=(\[\[.*?\]\]),g4=null;", js)
        self.assertIsNotNone(m)
        classes = [row[0] for row in json.loads(m.group(1))]
        self.assertIn("note", classes)
        self.assertIn("cross", classes)
        self.assertIn('"links-to":"note"', js)
        self.assertIn('"cites":"cross"', js)
        self.assertIn('"distilled-from":"cross"', js)
        self.assertIn('"references":"cross"', js)

    def test_a_cross_plane_line_is_the_quietest_on_the_canvas(self):
        # provenance, not dependency: thinner, dashed, and knocked back even when lit.
        js = render_board._MG_ENHANCE_JS
        self.assertIn('((e.cls==="cross")?0.6:1)', js)
        self.assertIn('e.cls==="cross"?[1,5]', js)
        m = re.search(r"var EDGEW=\{(.*?)\};", js)
        self.assertIsNotNone(m)
        widths = dict(re.findall(r"([a-z]+):([0-9.]+)", m.group(1)))
        self.assertLess(float(widths["cross"]), float(widths["lineage"]))


if __name__ == "__main__":
    unittest.main()
