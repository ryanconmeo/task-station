"""Step 78 stage 5a — the knowledge plane's DATA layer (the two-plane view).

Two literal stacked planes in ONE graph: the lower TASK plane (the sphere layout that
already renders) and the upper KNOWLEDGE plane — one global corpus of the whole vault,
built every time rather than a per-task tree, because the knowledge layer is a plane in
its own right and not a derived view of tasks.

What is covered here, and why in this style. These are BEHAVIOURAL tests: they build a
real corpus in a real temp dir, build the real graph, and assert what the DATA says.
None of them assert markup — the regressions this task has had were invisible to markup
assertions, which pass happily while the thing they describe is wrong.

  * THE GAP — every edge between the planes is one of the three kinds that may cross it,
    with a live edge inside each plane so the assertion cannot pass on an empty graph.
  * THE GATE — on / off / auto-with-a-vault / auto-without-one, and the env escape.
  * THE PARITY LAW — auto with no vault yields a graph byte-identical to the gate hard
    off, and to the call that predates the plane. This is the test that protects every
    user who has no second brain.
  * THE CORPUS — a note with no `area` is `unfiled` (a first-class sector, not an
    error); a note with no frontmatter at all is still a note; a `[[task:N]]` mention is
    a task reference and never a note; a link out of the corpus is dropped rather than
    phantom-noded.
  * PLACEMENT — deterministic, and radius means connectedness.

Every fixture vault is built inside the test's own tmpdir. Nothing here reads a real
vault, and no path to one is hardcoded.
"""
import importlib.util
import json
import math
import os
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

# The fixture corpus. Slugs, areas and types are invented — the shape is what matters:
# two notes that link to each other, a third linking one way into them, an orphan, a
# note with no area at all, and a note with no frontmatter at all.
_LEDGER = "ledger-rounding-gotcha"
_QUEUE = "queue-retry-design"
_WARMUP = "cache-warmup-how-to"
_GLOSSARY = "orphan-glossary"
_SCRATCH = "unfiled-scratch"
_BARE = "bare-jotting"


class _PlaneBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-kplane-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        self._saved = {k: os.environ.get(k)
                       for k in (_PLANE_ENV, "TASK_STATION_KNOWLEDGE_GRAPH")}
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

    # ---- fixture vault ----------------------------------------------------------
    def _write_note(self, slug, body="", subdir="notes", **fm):
        """One note file. Keyword args become frontmatter lines; pass none for a file
        with no frontmatter block at all."""
        d = os.path.join(self.tmp, "vault", subdir)
        os.makedirs(d, exist_ok=True)
        head = ""
        if fm:
            lines = ["---"] + ["%s: %s" % (k.rstrip("_"), v)
                               for k, v in sorted(fm.items())] + ["---", ""]
            head = "\n".join(lines)
        with open(os.path.join(d, slug + ".md"), "w", encoding="utf-8") as f:
            f.write(head + body)
        return os.path.join(d, slug + ".md")

    def _configure_vault(self):
        """Point config at the fixture vault (the ONLY way the vault path is ever
        resolved — never guessed from a home dir)."""
        vault = os.path.join(self.tmp, "vault")
        os.makedirs(vault, exist_ok=True)
        config.set("obsidian_vault", vault)
        return vault

    def _corpus(self, source_seq=None):
        """The fixture corpus. `source_seq` stamps the `source: task-station:<seq>`
        frontmatter that produces a `distilled-from` edge."""
        self._write_note(_LEDGER, "Rounds down. See [[%s]].\n" % _QUEUE,
                         type="gotcha", area="platform", plane="knowledge",
                         description="why the ledger rounds down")
        extra = {"source": "task-station:%d" % source_seq} if source_seq else {}
        self._write_note(_QUEUE, "# Retry design\n\nBack to [[%s]].\n" % _LEDGER,
                         type="architecture", area="platform", plane="knowledge",
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
                        color=fields.pop("color", "green"),
                        effort=fields.pop("effort", "m"))
        for k, v in fields.items():
            t[k] = v
        ts.save_task(t)
        return ts.load_task(t["id"])

    def _lineage(self, child, parent):
        """A stored `spawned-from` edge — the task plane's own relationship, so the gap
        assertion has a live within-plane edge to find."""
        child["related"] = [{"id": parent["id"], "seq": parent.get("seq"),
                             "kind": "spawned-from", "ts": ts._now()}]
        ts.save_task(child)

    def _seqs(self):
        ts.ensure_seqs()
        return {t["title"]: t.get("seq") for t in ts.all_tasks()}

    def _graph(self, notes=None):
        return ts.build_render_graph(ts.all_tasks(), notes=notes)


# ===================================================== 1. THE GAP ASSERTION ===
class TheGapTest(_PlaneBase):
    """The named requirement of the two-plane view: exactly three kinds cross between
    the planes, and nothing else does."""

    def _two_plane_graph(self):
        parent = self._seed("Ledger work")
        child = self._seed("Ledger follow-up", goal="per [[%s]] we retry" % _QUEUE)
        seqs = self._seqs()
        self._lineage(ts.load_task(child["id"]), ts.load_task(parent["id"]))
        self._corpus(source_seq=seqs["Ledger work"])
        notes = knowledge.vault_notes(os.path.join(self.tmp, "vault"))
        return self._graph(notes=notes), seqs, notes

    def test_only_the_three_kinds_cross_between_the_planes(self):
        g, _seqs, _notes = self._two_plane_graph()
        plane = {n["id"]: n.get("plane") for n in g["nodes"]}
        self.assertTrue(all(plane.values()), "every node names its plane")
        crossing = [e for e in g["edges"] if plane[e["a"]] != plane[e["b"]]]
        self.assertTrue(crossing, "the fixture must actually cross the gap")
        for e in crossing:
            self.assertIn(e["kind"], knowledge.CROSS_PLANE_KINDS)

    def test_each_plane_carries_an_edge_of_its_own(self):
        # …so the assertion above cannot pass by the graph being empty on one side.
        g, _seqs, _notes = self._two_plane_graph()
        plane = {n["id"]: n.get("plane") for n in g["nodes"]}
        within = {}
        for e in g["edges"]:
            if plane[e["a"]] == plane[e["b"]]:
                within.setdefault(plane[e["a"]], []).append(e["kind"])
        self.assertIn("spawned-from", within.get("task", []))
        self.assertIn(knowledge.NOTE_EDGE_KIND, within.get("knowledge", []))

    def test_cites_points_up_and_distilled_from_points_down(self):
        # The asymmetry is deliberate and kept: a task points UP at what it read, a note
        # points DOWN at what produced it. They are not inverses.
        g, seqs, _notes = self._two_plane_graph()
        cites = [e for e in g["edges"] if e["kind"] == "cites"]
        distilled = [e for e in g["edges"] if e["kind"] == "distilled-from"]
        self.assertEqual([(e["a"], e["b"]) for e in cites],
                         [("t:%d" % seqs["Ledger follow-up"], "n:%s" % _QUEUE)])
        self.assertEqual([(e["a"], e["b"]) for e in distilled],
                         [("n:%s" % _QUEUE, "t:%d" % seqs["Ledger work"])])
        for e in cites + distilled:
            self.assertEqual(e["dir"], "a->b")      # source first, in both directions

    def test_every_note_is_a_node_orphan_included(self):
        g, _seqs, notes = self._two_plane_graph()
        note_ids = {n["id"] for n in g["nodes"] if n["type"] == "note"}
        self.assertEqual(note_ids, {"n:%s" % n["slug"] for n in notes})
        orphan = [n for n in g["nodes"] if n["id"] == "n:%s" % _GLOSSARY][0]
        self.assertEqual(orphan["deg"], 0)          # drawn anyway: the corpus is whole


# ============================================================== 2. THE GATE ===
class GateTest(_PlaneBase):
    def test_default_is_auto(self):
        self.assertEqual(config.knowledge_plane_mode(), "auto")

    def test_on_and_off_are_explicit(self):
        config.set("knowledge_plane", "on")
        self.assertEqual(config.knowledge_plane_mode(), "on")
        self.assertTrue(ts._knowledge_plane_on())       # on needs no vault
        config.set("knowledge_plane", "off")
        self.assertFalse(ts._knowledge_plane_on())

    def test_off_wins_even_with_a_full_vault(self):
        self._corpus()
        config.set("knowledge_plane", "off")
        self.assertFalse(ts._knowledge_plane_on())
        self.assertEqual(ts.board_notes(), [])

    def test_auto_with_a_vault_that_has_notes_resolves_on(self):
        self._corpus()
        self.assertEqual(config.knowledge_plane_mode(), "auto")
        self.assertTrue(ts._knowledge_plane_on())
        self.assertTrue(ts.board_notes())

    def test_auto_without_a_vault_resolves_off(self):
        self.assertEqual(config.obsidian_vault(), "")
        self.assertFalse(ts._knowledge_plane_on())
        self.assertEqual(ts.board_notes(), [])

    def test_auto_with_an_empty_vault_resolves_off(self):
        # A configured vault holding no note is the same as none: nothing to draw.
        self._configure_vault()
        self.assertFalse(ts._knowledge_plane_on())

    def test_env_escape_wins_over_the_persisted_flag(self):
        config.set("knowledge_plane", "off")
        os.environ[_PLANE_ENV] = "on"
        self.assertEqual(config.knowledge_plane_mode(), "on")
        self.assertTrue(ts._knowledge_plane_on())
        os.environ[_PLANE_ENV] = "off"
        config.set("knowledge_plane", "on")
        self.assertEqual(config.knowledge_plane_mode(), "off")
        self.assertFalse(ts._knowledge_plane_on())

    def test_garbage_falls_back_to_auto(self):
        os.environ[_PLANE_ENV] = "sideways"
        self.assertEqual(config.knowledge_plane_mode(), "auto")
        os.environ.pop(_PLANE_ENV)
        config.set("knowledge_plane", "yes-please")
        self.assertEqual(config.knowledge_plane_mode(), "auto")

    def test_the_vault_write_switch_is_untouched(self):
        # The plane is READ-ONLY and board-only. The flag that gates the vault WRITE path
        # keeps its own default, so turning the plane on cannot change what is written
        # into anybody's vault.
        self.assertFalse(config.knowledge_graph_enabled())
        config.set("knowledge_plane", "on")
        self.assertFalse(config.knowledge_graph_enabled())

    def test_the_flag_is_on_the_config_board_and_round_trips(self):
        rows = {r[0]: r for r in config.board_rows()}
        self.assertIn("--knowledge-plane", rows)
        self.assertEqual(rows["--knowledge-plane"][1], "auto")
        self.assertEqual(rows["--knowledge-plane"][2], "on · off · auto")
        config.set("knowledge_plane", "on")
        rows = {r[0]: r for r in config.board_rows()}
        self.assertEqual(rows["--knowledge-plane"][1], "on")
        self.assertIn("knowledge_plane", config.RESET_KEYS)


# ========================================================= 3. THE PARITY LAW ===
class ParityTest(_PlaneBase):
    """auto + no vault ⇒ no plane ⇒ the graph is byte-identical to the gate hard off.
    Every public user with no second brain lands here."""

    def _blob(self):
        return json.dumps(ts.build_render_graph(ts.all_tasks(), notes=ts.board_notes()),
                          sort_keys=True)

    def test_auto_without_a_vault_is_byte_identical_to_hard_off(self):
        parent = self._seed("Ledger work")
        child = self._seed("Ledger follow-up")
        self._seqs()
        self._lineage(ts.load_task(child["id"]), ts.load_task(parent["id"]))
        os.environ[_PLANE_ENV] = "auto"
        auto = self._blob()
        os.environ[_PLANE_ENV] = "off"
        off = self._blob()
        self.assertEqual(auto, off)
        # …and to the call that predates the plane entirely: no `plane` key, no note
        # node, nothing added to a node dict that used to be emitted without it.
        self.assertEqual(auto, json.dumps(ts.build_render_graph(ts.all_tasks()),
                                          sort_keys=True))
        self.assertNotIn('"plane"', auto)

    def test_a_relation_free_store_with_no_vault_still_yields_no_panel(self):
        self._seed("Lonely")
        self._seqs()
        os.environ[_PLANE_ENV] = "auto"
        self.assertEqual(ts.build_render_graph(ts.all_tasks(), notes=ts.board_notes()),
                         {"nodes": [], "edges": []})


# ============================================================= 4. THE CORPUS ===
class CorpusTest(_PlaneBase):
    def _notes(self):
        self._corpus()
        return knowledge.vault_notes(os.path.join(self.tmp, "vault"))

    def test_a_note_with_no_area_lands_in_unfiled(self):
        notes = self._notes()
        scratch = [n for n in notes if n["slug"] == _SCRATCH][0]
        self.assertEqual(scratch["area"], "")       # the corpus reports what the file says
        lay = render_board._knowledge_layout(notes, knowledge.note_degree(notes))
        self.assertIn(render_board.NOTE_UNFILED, lay["sectors"])
        self.assertIn((render_board.NOTE_UNFILED, "reference"), lay["wedges"])
        self.assertIn(_SCRATCH, lay["note"])        # …and it is really placed

    def test_a_note_with_no_frontmatter_is_still_a_note(self):
        notes = self._notes()
        bare = [n for n in notes if n["slug"] == _BARE]
        self.assertEqual(len(bare), 1)
        bare = bare[0]
        self.assertEqual(bare["title"], _BARE)      # slug is the fallback title
        self.assertEqual((bare["type"], bare["area"], bare["description"]), ("", "", ""))
        g = self._graph(notes=notes)
        self.assertIn("n:%s" % _BARE, {n["id"] for n in g["nodes"]})

    def test_frontmatter_fields_are_read_as_written(self):
        notes = {n["slug"]: n for n in self._notes()}
        self.assertEqual(notes[_LEDGER]["type"], "gotcha")
        self.assertEqual(notes[_LEDGER]["area"], "platform")
        self.assertEqual(notes[_LEDGER]["plane"], "knowledge")
        self.assertEqual(notes[_LEDGER]["description"], "why the ledger rounds down")
        # a body heading titles a note whose frontmatter carries no title
        self.assertEqual(notes[_QUEUE]["title"], "Retry design")

    def test_the_frontmatter_subset_reads_scalars_and_skips_structure(self):
        # Measured reality: frontmatter is uneven, and a stdlib-only reader must degrade
        # rather than guess. A nested block's contents and a list item are skipped, a
        # quoted scalar is unwrapped, and the fields it never saw come back empty.
        fm, body = knowledge.parse_frontmatter(
            '---\n'
            'type: gotcha\n'
            'description: "rounds: down"\n'
            '# a comment\n'
            'tags:\n'
            '  - one\n'
            '  - two\n'
            'area: platform\n'
            '---\n'
            'body text\n')
        self.assertEqual(fm["type"], "gotcha")
        self.assertEqual(fm["description"], "rounds: down")   # quotes unwrapped
        self.assertEqual(fm["area"], "platform")              # read past the block
        self.assertEqual(fm["tags"], "")                      # structure not guessed at
        self.assertEqual(body, "body text\n")

    def test_an_unterminated_fence_is_body_not_frontmatter(self):
        fm, body = knowledge.parse_frontmatter("---\ntype: gotcha\nno closing fence\n")
        self.assertEqual(fm, {})
        self.assertEqual(body, "---\ntype: gotcha\nno closing fence\n")

    def test_the_task_mirror_folder_is_not_part_of_the_corpus(self):
        # The writer exports task notes into one namespaced folder at the vault root.
        # Reading them back would put TASKS on the knowledge plane, which is the layer
        # confusion this plane exists to prevent.
        self._corpus()
        self._write_note("7-ledger-work", "a mirrored task note\n",
                         subdir=knowledge.MIRROR_FOLDER, type="task")
        slugs = {n["slug"] for n in
                 knowledge.vault_notes(os.path.join(self.tmp, "vault"))}
        self.assertNotIn("7-ledger-work", slugs)
        self.assertIn(_LEDGER, slugs)

    def test_the_whole_vault_is_read_not_one_folder(self):
        self._corpus()
        self._write_note("deep-decision-log", "elsewhere in the vault\n",
                         subdir=os.path.join("archive", "2026"), type="decision")
        slugs = {n["slug"] for n in
                 knowledge.vault_notes(os.path.join(self.tmp, "vault"))}
        self.assertIn("deep-decision-log", slugs)

    def test_an_absent_vault_is_an_empty_corpus_not_an_error(self):
        self.assertEqual(knowledge.vault_notes(""), [])
        self.assertEqual(knowledge.vault_notes(os.path.join(self.tmp, "nope")), [])

    def test_a_mutual_link_is_one_edge_a_one_way_link_is_directed(self):
        notes = self._notes()
        edges = {(e["a"], e["b"]): e for e in knowledge.note_edges(notes)}
        self.assertEqual(edges[(_LEDGER, _QUEUE)]["dir"], "none")     # mutual → one tie
        self.assertEqual(edges[(_WARMUP, _QUEUE)]["dir"], "a->b")     # one-way
        self.assertEqual(len(edges), 2)

    def test_references_is_supported_and_empty(self):
        # A declared kind with no instances: collapse-to-reference has never run, so the
        # honest answer is []. The kind is still in the crossing set, so the day a record
        # appears nothing downstream has to change.
        notes = self._notes()
        tasks = ts.all_tasks()
        self.assertIn("references", knowledge.CROSS_PLANE_KINDS)
        self.assertEqual(knowledge.reference_edges(tasks, notes), [])
        self.assertEqual([e for e in knowledge.cross_plane_edges(tasks, notes)
                          if e["kind"] == "references"], [])

    def test_a_source_naming_a_task_this_store_lacks_is_dropped(self):
        self._seed("Ledger work")
        self._seqs()
        self._corpus(source_seq=99999)
        notes = knowledge.vault_notes(os.path.join(self.tmp, "vault"))
        self.assertEqual([e for e in knowledge.cross_plane_edges(ts.all_tasks(), notes)
                          if e["kind"] == "distilled-from"], [])


# ================================================= 5. THE task: DISAMBIGUATION ===
class TaskMentionTest(_PlaneBase):
    """`[[task:502]]` is a reference to a TASK, never a knowledge node. The corpus has
    none today, which is exactly why this is built now: the failure it prevents is a task
    reference drawing a phantom note that no file backs."""

    def test_the_predicate_reads_a_mention(self):
        self.assertEqual(knowledge.task_mention("task:502"), 502)
        self.assertTrue(knowledge.is_task_mention(" task:7 "))
        self.assertIsNone(knowledge.task_mention(_QUEUE))
        self.assertIsNone(knowledge.task_mention("task:abc"))
        self.assertIsNone(knowledge.task_mention("tasks:12"))

    def test_a_mention_is_not_a_link_target_but_a_slug_is(self):
        self.assertEqual(knowledge.link_targets("see [[task:502]] and [[%s]]" % _QUEUE),
                         [_QUEUE])

    def test_a_mention_produces_no_knowledge_node_and_a_slug_does(self):
        parent = self._seed("Ledger work")
        child = self._seed("Ledger follow-up",
                           goal="blocked by [[task:502]]; per [[%s]] we retry" % _QUEUE)
        seqs = self._seqs()
        self._lineage(ts.load_task(child["id"]), ts.load_task(parent["id"]))
        self._corpus()
        notes = knowledge.vault_notes(os.path.join(self.tmp, "vault"))
        g = self._graph(notes=notes)
        ids = {n["id"] for n in g["nodes"]}
        self.assertNotIn("n:task:502", ids)                 # no phantom
        self.assertNotIn("task:502", json.dumps(g))         # …not anywhere
        self.assertIn("n:%s" % _QUEUE, ids)                 # the real slug did resolve
        self.assertEqual([e["b"] for e in g["edges"] if e["kind"] == "cites"],
                         ["n:%s" % _QUEUE])
        self.assertEqual(seqs["Ledger follow-up"],
                         int([e["a"] for e in g["edges"]
                              if e["kind"] == "cites"][0].split(":")[1]))

    def test_a_mention_inside_a_note_links_to_nothing(self):
        self._configure_vault()
        self._write_note("release-runbook", "step one: [[task:88]]\n", type="how-to")
        notes = knowledge.vault_notes(os.path.join(self.tmp, "vault"))
        self.assertEqual(notes[0]["links"], [])
        self.assertEqual(knowledge.note_edges(notes), [])


# ============================================== 6. LINKS OUT OF THE CORPUS ===
class DroppedLinkTest(_PlaneBase):
    def test_a_link_to_a_target_outside_the_corpus_is_dropped(self):
        parent = self._seed("Ledger work")
        child = self._seed("Ledger follow-up", goal="see [[nowhere-note]]")
        self._seqs()
        self._lineage(ts.load_task(child["id"]), ts.load_task(parent["id"]))
        self._configure_vault()
        self._write_note(_QUEUE, "points out at [[nowhere-note]] too\n",
                         type="architecture", area="platform")
        notes = knowledge.vault_notes(os.path.join(self.tmp, "vault"))
        # the note records the raw target — dropping happens where an EDGE would be made
        self.assertEqual(notes[0]["links"], ["nowhere-note"])
        self.assertEqual(knowledge.note_edges(notes), [])
        self.assertEqual(knowledge.cross_plane_edges(ts.all_tasks(), notes), [])
        g = self._graph(notes=notes)
        self.assertNotIn("n:nowhere-note", {n["id"] for n in g["nodes"]})
        self.assertNotIn("nowhere-note", json.dumps(g))

    def test_a_self_link_is_not_an_edge(self):
        self._configure_vault()
        self._write_note(_QUEUE, "see [[%s]]\n" % _QUEUE, type="architecture")
        notes = knowledge.vault_notes(os.path.join(self.tmp, "vault"))
        self.assertEqual(knowledge.note_edges(notes), [])


# ============================================================ 7. PLACEMENT ===
class PlacementTest(_PlaneBase):
    def _notes(self):
        self._corpus()
        notes = knowledge.vault_notes(os.path.join(self.tmp, "vault"))
        return notes, knowledge.note_degree(notes)

    def test_placement_is_deterministic(self):
        notes, deg = self._notes()
        a = render_board._knowledge_layout(notes, deg)
        b = render_board._knowledge_layout(notes, deg)
        self.assertEqual(a, b)
        # …and independent of the order the corpus arrives in, since every key is sorted
        c = render_board._knowledge_layout(list(reversed(notes)), deg)
        self.assertEqual(a["note"], c["note"])

    def test_a_rebuilt_corpus_gives_the_same_slots(self):
        # The whole path, twice: read the vault again and re-place it.
        notes, deg = self._notes()
        again = knowledge.vault_notes(os.path.join(self.tmp, "vault"))
        self.assertEqual(render_board._knowledge_layout(notes, deg)["note"],
                         render_board._knowledge_layout(
                             again, knowledge.note_degree(again))["note"])

    def test_radius_is_degree_hub_inside_orphan(self):
        notes, degree = self._notes()
        lay = render_board._knowledge_layout(notes, degree)
        deg = lay["degree"]
        self.assertEqual(deg[_QUEUE], 2)            # linked from two notes
        self.assertEqual(deg[_GLOSSARY], 0)         # an orphan
        self.assertLess(render_board._note_radius(deg[_QUEUE]),
                        render_board._note_radius(deg[_GLOSSARY]))
        self.assertEqual(render_board._note_radius(0), render_board.R_NOTE_RIM)
        # …and a corpus far past saturation still stops at the hub radius.
        self.assertEqual(render_board._note_radius(500), render_board.R_NOTE_HUB)

    def test_sector_is_area_and_wedge_is_type(self):
        notes, deg = self._notes()
        lay = render_board._knowledge_layout(notes, deg)
        self.assertEqual(set(lay["sectors"]),
                         {"platform", "tooling", render_board.NOTE_UNFILED})
        # every wedge belongs to a sector that exists, and same-type notes share one
        for sector, ntype in lay["wedges"]:
            self.assertIn(sector, lay["sectors"])
        self.assertIn(("platform", "gotcha"), lay["wedges"])
        self.assertIn(("platform", "architecture"), lay["wedges"])
        # a sector's wedges partition its angular share and never leave it
        for sector, base in lay["sectors"].items():
            for (s, _t), (centre, sub) in lay["wedges"].items():
                if s != sector:
                    continue
                self.assertLessEqual(abs(centre - base) + sub / 2.0,
                                     lay["span"] / 2.0 + 1e-9)

    def test_notes_of_one_type_sit_together(self):
        # Two notes of the same type in the same sector land in the same wedge, so they
        # are neighbours in bearing rather than scattered around the sector.
        self._configure_vault()
        for i in range(4):
            self._write_note("gotcha-note-%d" % i, "one of a family\n",
                             type="gotcha", area="platform")
        self._write_note("lone-reference", "different type\n",
                         type="reference", area="platform")
        notes = knowledge.vault_notes(os.path.join(self.tmp, "vault"))
        lay = render_board._knowledge_layout(notes, knowledge.note_degree(notes))
        g_centre, g_span = lay["wedges"][("platform", "gotcha")]
        r_centre, _ = lay["wedges"][("platform", "reference")]
        self.assertNotAlmostEqual(g_centre, r_centre)
        self.assertGreater(g_span, 0.0)
        # the gotcha family is the bigger type, so it gets the bigger arc
        self.assertGreater(g_span, lay["wedges"][("platform", "reference")][1])

    def test_degree_is_undirected_and_covers_every_note(self):
        notes, deg = self._notes()
        self.assertEqual(set(deg), {n["slug"] for n in notes})   # orphans included
        # _QUEUE links out once and is linked from twice; the tie is counted once either
        # way, so it is 2 — being linked FROM is what makes a note a hub.
        self.assertEqual(deg[_QUEUE], 2)
        self.assertEqual(deg[_WARMUP], 1)
        self.assertEqual(deg[_BARE], 0)

    def test_with_no_degree_given_every_note_sits_on_the_rim(self):
        notes, _deg = self._notes()
        lay = render_board._knowledge_layout(notes)
        self.assertEqual(set(lay["degree"].values()), {0})
        for slug, (x, y) in lay["note"].items():
            self.assertAlmostEqual(math.hypot(x, y), render_board.R_NOTE_RIM, places=6,
                                   msg="%s should be on the rim" % slug)

    def test_an_empty_corpus_places_nothing_without_raising(self):
        lay = render_board._knowledge_layout([], {})
        self.assertEqual(lay["note"], {})
        self.assertEqual(lay["sectors"], {})


if __name__ == "__main__":
    unittest.main()
