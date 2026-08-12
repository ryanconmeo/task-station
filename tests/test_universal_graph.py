"""WS-D — the universal task-relation graph + the second-brain-gated knowledge tier.

Covers the shipping contract from the runbook's §6.6 universal-vs-gated table:

  UNIVERSAL (ships to everyone, no vault, default config):
    * `semantic_edges` — derived `touches-same` edges from a shared PR / story / file /
      repo, weighted (pr 3 > story/file 2 > repo 1), with `via` = the shared signals.
    * `build_board_graph` — STORED lineage only (the derived `touches-same` tier was
      deliberately removed from the graph — see
      test_lineage_universal_and_no_touches_same_edges — while `semantic_edges` itself
      stays, for the export and the signal hubs); empty on a relation-free store.
    * the board mini-graph SVG panel — rendered when there are edges, omitted otherwise,
      and never referencing an external asset.

  SECOND-BRAIN-GATED (default OFF; requires the flag AND a configured vault):
    * co-citation `related-knowledge` edges (two tasks citing the same `[[note]]`).
    * the board's per-card "Related knowledge" panel.
    * the `## Related` wikilink emission into the Obsidian mirror.

  DEGRADE: a user with no vault + default config sees exactly today's behaviour for the
  gated tier (no knowledge edges, no panel, no `## Related`) while still getting the
  universal graph — proven directly against the worktree's own render code path.

Per-test temp-home isolation (the `_repoint` idiom shared with test_board.py /
test_events_relations.py); never touches live data.
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
import config  # noqa: E402
import obsidian_sync  # noqa: E402
import render_board  # noqa: E402

# task-station.py has a hyphen — load it by path (see tests/test_board.py).
_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


class _GraphBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-wsD-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        # Ensure the gated flag + vault start clean regardless of ambient env.
        for k in ("TASK_STATION_KNOWLEDGE_GRAPH", "TASK_STATION_OBSIDIAN_PROMPTS"):
            os.environ.pop(k, None)
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.DELEGATE_REGISTRY = os.path.join(self.tmp, "workers.json")
        store.reset_cache()

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        os.environ.pop("TASK_STATION_KNOWLEDGE_GRAPH", None)
        store.reset_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self, title, **fields):
        t = ts.new_task(title, fields.pop("summary", "summary for " + title),
                        color=fields.pop("color", "green"),
                        effort=fields.pop("effort", "m"))
        for k, v in fields.items():
            t[k] = v
        ts.save_task(t)
        return ts.load_task(t["id"])

    def _seqs(self):
        ts.ensure_seqs()
        return {t["title"]: t.get("seq") for t in ts.all_tasks()}


# --------------------------------------------------------------- semantic edges ---
class SemanticEdgesTest(_GraphBase):
    def test_shared_pr_creates_touches_same(self):
        a = self._seed("A", prs=[{"url": "https://x/pr/1", "desc": ""}])
        b = self._seed("B", prs=[{"url": "https://x/pr/1", "desc": ""}])
        scan = ts.all_tasks()
        edges = ts.semantic_edges(a, scan)
        self.assertEqual([e["id"] for e in edges], [b["id"]])
        self.assertEqual(edges[0]["kind"], "touches-same")
        self.assertEqual(edges[0]["via"], ["pr"])
        self.assertEqual(edges[0]["weight"], 3)     # pr weight

    def test_no_signal_no_edges(self):
        a = self._seed("A")     # bare — no pr/story/file/repo
        self._seed("B")
        self.assertEqual(ts.semantic_edges(a, ts.all_tasks()), [])

    def test_weight_sums_and_ranks(self):
        # A shares a repo with B (weak, 1) and a PR+file with C (3+2=5).
        a = self._seed("A", projects=["projectname"], prs=[{"url": "p1"}],
                       files=["/r/x.py"])
        self._seed("B", projects=["projectname"])
        self._seed("C", prs=[{"url": "p1"}], files=["/r/x.py"])
        scan = ts.all_tasks()
        edges = ts.semantic_edges(a, scan)
        titles = {e["id"]: e for e in edges}
        by_title = {t["title"]: t["id"] for t in scan}
        self.assertEqual(titles[by_title["C"]]["weight"], 5)
        self.assertEqual(sorted(titles[by_title["C"]]["via"]), ["file", "pr"])
        self.assertEqual(titles[by_title["B"]]["weight"], 1)
        # Strongest first.
        self.assertEqual(edges[0]["id"], by_title["C"])

    def test_related_edges_semantic_optin_default_off(self):
        a = self._seed("A", prs=[{"url": "p1"}])
        self._seed("B", prs=[{"url": "p1"}])
        scan = ts.all_tasks()
        # Default call is byte-identical to the pre-WS-D shape (no `semantic` key).
        self.assertNotIn("semantic", ts.related_edges(a, scan))
        withsem = ts.related_edges(a, scan, semantic=True)
        self.assertIn("semantic", withsem)
        self.assertEqual(len(withsem["semantic"]), 1)


# --------------------------------------------------------------- board graph ---
class BuildBoardGraphTest(_GraphBase):
    def test_empty_on_relation_free_store(self):
        self._seed("Lonely")
        self._seqs()
        g = ts.build_board_graph(ts.all_tasks())
        self.assertEqual(g, {"nodes": [], "edges": []})

    def test_lineage_universal_and_no_touches_same_edges(self):
        # The graph carries STORED lineage only. A shared PR/story/file/repo no longer
        # draws a task<->task edge — two tasks touching one file is not a relationship
        # worth drawing, and the kind was 97% of every edge in the real graph. Shared
        # PRs/repos/stories still reach the RENDER graph as signal hubs, which is a
        # different tier (see test_render_graph.py).
        parent = self._seed("Parent")
        child = self._seed("Child", prs=[{"url": "p9"}])
        self._seed("Sibling", prs=[{"url": "p9"}])
        child["related"] = [{"id": parent["id"], "seq": parent.get("seq"),
                             "kind": "spawned-from", "ts": ts._now()}]
        ts.save_task(child)
        seqs = self._seqs()                           # seqs are assigned HERE
        g = ts.build_board_graph(ts.all_tasks())      # knowledge default off
        kinds = sorted({e["kind"] for e in g["edges"]})
        self.assertIn("spawned-from", kinds)
        self.assertNotIn("touches-same", kinds)       # deleted from the graph by design
        self.assertNotIn("related-knowledge", kinds)  # gated off
        # …and the derivation itself still works — this is a GRAPH change, not a
        # semantic_edges change (the export still consumes it).
        self.assertEqual(len(ts.semantic_edges(ts.load_task(child["id"]),
                                               ts.all_tasks())), 1)
        # Only edge-touching nodes appear: the lineage pair. The sibling shared a PR
        # with the child and nothing else, so it is no longer in the base graph.
        self.assertEqual(len(g["nodes"]), 2)
        self.assertNotIn(seqs["Sibling"], [n["seq"] for n in g["nodes"]])
        # A spawned-from edge is directed a->b.
        sp = [e for e in g["edges"] if e["kind"] == "spawned-from"][0]
        self.assertEqual(sp["dir"], "a->b")

    def test_knowledge_gated_by_param(self):
        # Two tasks cite the same [[note]] in their decisions.
        a = self._seed("A", decisions=["Per [[shared-note]] we chose X"])
        b = self._seed("B", decisions=["Also see [[shared-note]]"])
        self._seqs()
        off = ts.build_board_graph(ts.all_tasks(), knowledge=False)
        self.assertEqual(off, {"nodes": [], "edges": []})   # no co-citation, nothing
        on = ts.build_board_graph(ts.all_tasks(), knowledge=True)
        kk = [e for e in on["edges"] if e["kind"] == "related-knowledge"]
        self.assertEqual(len(kk), 1)
        self.assertEqual(kk[0]["via"], ["shared-note"])
        self.assertEqual(len(on["nodes"]), 2)

    def test_cited_notes_extraction(self):
        a = self._seed("A", summary="see [[note-one]] and [[note-two|alias]]",
                       decisions=["ref [[note-three#head]]"])
        self.assertEqual(ts._task_cited_notes(a),
                         {"note-one", "note-two", "note-three"})


# --------------------------------------------------------- board rendering ---
_EXTERNAL_NEEDLES = ('src="http', "src='http", 'src="//', "src='//",
                     "<link ", "@import", "url(http", "//fonts.")


class BoardRenderTest(_GraphBase):
    def _board_html(self):
        out = ts.write_board()
        with open(out, encoding="utf-8") as f:
            return f.read()

    def test_minigraph_present_with_edges_no_external_assets(self):
        self._seed("A", prs=[{"url": "p1"}])
        self._seed("B", prs=[{"url": "p1"}])
        html = self._board_html()
        self.assertIn('class="minigraph"', html)
        self.assertIn("<svg", html)
        self.assertIn("Task Graph", html)
        for needle in _EXTERNAL_NEEDLES:
            self.assertNotIn(needle, html)

    def test_minigraph_absent_when_no_relations(self):
        self._seed("Solo")
        html = self._board_html()
        self.assertNotIn('class="minigraph"', html)

    def test_public_user_no_vault_default_config_degrades(self):
        # No vault, default config (knowledge flag off): the UNIVERSAL graph still
        # renders from semantic edges, but NO knowledge tier leaks in.
        self.assertFalse(config.knowledge_graph_enabled())
        self.assertEqual(config.obsidian_vault(), "")
        self._seed("A", prs=[{"url": "p1"}],
                   decisions=["cites [[secret-note]]"])
        self._seed("B", prs=[{"url": "p1"}],
                   decisions=["cites [[secret-note]]"])
        html = self._board_html()
        self.assertIn('class="minigraph"', html)           # universal graph shows
        self.assertNotIn("shared knowledge", html)         # no co-citation legend
        # no co-citation EDGE drawn (the `.k-knowledge` CSS rule is always defined in
        # the stylesheet — only the drawn `<line class="mg-edge k-knowledge">` is gated).
        self.assertNotIn('mg-edge k-knowledge', html)
        # the per-card "Related knowledge" panel row is never emitted (the [[wikilink]]
        # still appears as ORDINARY decision text — that is unchanged behaviour).
        self.assertNotIn('<span class="k">knowledge</span>', html)

    def test_minigraph_shows_knowledge_when_enabled(self):
        os.environ["TASK_STATION_KNOWLEDGE_GRAPH"] = "on"
        config.set("obsidian_vault", self.tmp)   # configured consumer
        try:
            self._seed("A", decisions=["cites [[joint-note]]"])
            self._seed("B", decisions=["cites [[joint-note]]"])
            html = self._board_html()
            self.assertIn('class="minigraph"', html)
            self.assertIn("shared knowledge", html)        # co-citation legend
        finally:
            os.environ.pop("TASK_STATION_KNOWLEDGE_GRAPH", None)

    def test_related_knowledge_panel_gated_in_view_model(self):
        t = self._seed("K", decisions=["per [[a-note]]"])
        off = ts._board_view_model(t, knowledge=False)
        self.assertIsNone(off["knowledge"])
        self.assertNotIn('<span class="k">knowledge</span>',
                         render_board._brief_detail(off))
        on = ts._board_view_model(t, knowledge=True)
        self.assertEqual(on["knowledge"], ["a-note"])
        self.assertIn("[[a-note]]", render_board._brief_detail(on))

    def test_minigraph_helper_empty_graph_returns_nothing(self):
        self.assertEqual(render_board._minigraph({"nodes": [], "edges": []}), [])
        self.assertEqual(render_board._minigraph(None), [])


# ------------------------------------------------------- config gate + emission ---
class GateTest(_GraphBase):
    def test_knowledge_flag_default_off_and_env_override(self):
        self.assertFalse(config.knowledge_graph_enabled())
        os.environ["TASK_STATION_KNOWLEDGE_GRAPH"] = "on"
        try:
            self.assertTrue(config.knowledge_graph_enabled())
        finally:
            os.environ.pop("TASK_STATION_KNOWLEDGE_GRAPH", None)
        config.set("knowledge_graph", True)
        self.assertTrue(config.knowledge_graph_enabled())

    def test_render_note_related_default_omits_section(self):
        t = self._seed("N")
        note = obsidian_sync.render_note(t)
        self.assertNotIn("## Related", note)

    def test_render_note_emits_related_when_passed(self):
        t = self._seed("N")
        note = obsidian_sync.render_note(
            t, related=[("Other Task", "touches same"), ("a-note", "knowledge")])
        self.assertIn("## Related", note)
        self.assertIn("[[Other Task]] — touches same", note)
        self.assertIn("[[a-note]] — knowledge", note)

    def test_obsidian_related_links_gated_off_by_default(self):
        # A-3: the UNIVERSAL task graph (touches-same) now ships in ## Related even
        # with the gate off; only the co-citation knowledge tier stays gated. So the
        # touches-same sibling appears but the cited [[some-note]] leaks ZERO.
        a = self._seed("A", prs=[{"url": "p1"}], decisions=["[[some-note]]"])
        b = self._seed("B", prs=[{"url": "p1"}])
        self._seqs()
        a = ts.load_task(a["id"])
        pairs = ts._obsidian_related_links(a)
        # each task edge is a resolvable (stem, title, kind) triple
        b_stem = obsidian_sync.note_stem(ts.load_task(b["id"]))
        trip = [p for p in pairs if len(p) == 3]
        self.assertIn((b_stem, "B", "touches same"), trip)
        # NO knowledge leak: the cited note is nowhere in the pairs
        self.assertNotIn("some-note", [p[0] for p in pairs])
        self.assertNotIn("knowledge", [p[-1] for p in pairs])

    def test_obsidian_related_links_when_enabled(self):
        os.environ["TASK_STATION_KNOWLEDGE_GRAPH"] = "on"
        try:
            a = self._seed("A", prs=[{"url": "p1"}], decisions=["see [[some-note]]"])
            b = self._seed("B", prs=[{"url": "p1"}])
            self._seqs()
            a = ts.load_task(a["id"])
            pairs = ts._obsidian_related_links(a)
            # task edge → (stem, title, kind); the stem resolves to B's note file
            b_stem = obsidian_sync.note_stem(ts.load_task(b["id"]))
            self.assertIn((b_stem, "B", "touches same"), pairs)
            # cited knowledge note → 2-tuple (slug, "knowledge"), gate on
            self.assertIn(("some-note", "knowledge"), pairs)
        finally:
            os.environ.pop("TASK_STATION_KNOWLEDGE_GRAPH", None)


if __name__ == "__main__":
    unittest.main()
