"""Behavioral gate for `/todo board` (task #444) — the cheap automated asserts backing
docs/specs/BOARD-BEHAVIOR.md.

Two families:
  * B1–B8  — observable board behaviors that 2.0.0's preview-engine default regressed
             (rows on first load, closed 5-cap + show-more, title marquee hook, session
             resume block, refuse-downgrade, rev sidecar).
  * F1/F2  — Interbrain rendered SERVER-SIDE through the board pipeline: interbrain-off
             parity, foreign VM mapping, foreign rows memo-only, graph foreign owner +
             dashed cross-brain edge, and the focus strip + mg-data owner/brain.

Verification is BEHAVIORAL / served-content, never markup-grep of an incidental class.
"""
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)
sys.path.insert(0, os.path.join(os.path.dirname(LIB), "tools"))

_spec = importlib.util.spec_from_file_location(
    "task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)
import render_board  # noqa: E402


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _feed_js(alias, feed):
    """Canonical feed serialization (matches feeds._feed_js) — the form the board parses
    server-side. Kept hand-written HERE on purpose: it pins the wire form independently of
    lib/feeds.py, so a change to the writer can't silently move the goalposts for the
    reader. tests/test_feeds.py asserts the writer/parser round-trip."""
    return ("window.__TSFEED_%s = %s;\n"
            "(window.__TSFEEDS = window.__TSFEEDS || []).push(window.__TSFEED_%s);\n"
            % (alias, json.dumps(feed), alias))


def _peer_task(uuid8="7f3a2b10", handle="jpark-201", title="Balance-sheet API rollup",
               status="active", prs=None, stories=None, brain="main", org=True):
    return {
        "uuid8": uuid8, "handle": handle, "title": title, "status": status, "live": False,
        "category": {"key": "green", "tag": "FEATURE", "dot": "🟢",
                     "hex": "#3f9e2f", "hex_dark": "#6fe05a"},
        "effort": "m", "brain": brain, "shares": (["org"] if org else []),
        "tokens": 100, "tokens_estimated": False, "cost_usd": 1.0, "models": ["sonnet"],
        "updated_ts": 1752810000, "relations": [],
        "signals": {"prs": list(prs or []), "stories": list(stories or [])},
        "digest": {"goal": "serve period rollups", "state": "wire cache invalidation",
                   "steps_done": 4, "steps_total": 6,
                   "decisions_tail": ["cache rollups per fiscal period"]},
        "participants": ["jpark"], "owner": "jpark",
        "shared_org": org,
    }


def _peer_feed(tasks, alias="jpark"):
    return {"schema": 3, "kind": "peer", "alias": alias, "owner": alias, "label": alias,
            "editable": False, "color": "#4f8fe6", "color_dark": "#4f8fe6",
            "tasks": tasks, "memos": []}


def _normalize(html):
    """Strip the per-render version/timestamp noise so two renders of the same data
    compare equal: the kicker `refreshed …`, every server timestamp (data-ts), and the
    footer's `updated …`."""
    html = re.sub(r'refreshed [^<]*', 'refreshed', html)
    html = re.sub(r'data-ts="[0-9.]+"', 'data-ts=""', html)
    html = re.sub(r'updated [0-9:\- ]+', 'updated', html)
    return html


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="board-behavior-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.DELEGATE_REGISTRY = os.path.join(self.tmp, "workers.json")
        ts.store.reset_cache()
        self._ib_saved = os.environ.get("TASK_STATION_INTERBRAIN")

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        if self._ib_saved is None:
            os.environ.pop("TASK_STATION_INTERBRAIN", None)
        else:
            os.environ["TASK_STATION_INTERBRAIN"] = self._ib_saved
        ts.store.reset_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self, title, color="green", effort="m", closed=False, prs=None):
        t = ts.new_task(title, "summary for " + title, color=color, effort=effort)
        if prs:
            t["prs"] = [{"url": u, "desc": ""} for u in prs]
        ts.save_task(t)
        ts.ensure_seqs()
        t = ts.load_task(t["id"])
        if closed:
            t["status"] = "closed"
        ts.save_task(t)
        return ts.load_task(t["id"])

    def _write_feed(self, feed, alias="jpark", sub="peers"):
        d = os.path.join(self.tmp, "feeds", sub)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, alias + ".js"), "w", encoding="utf-8") as f:
            f.write(_feed_js(alias, feed))

    def _render(self, interbrain=None):
        if interbrain is not None:
            os.environ["TASK_STATION_INTERBRAIN"] = interbrain
        out = ts.write_board()
        with open(out, encoding="utf-8") as f:
            return f.read()

    def _mgdata(self, html):
        m = re.search(r'<script type="application/json" id="mg-data">(.*?)</script>',
                      html, re.S)
        self.assertIsNotNone(m, "mg-data block missing")
        return json.loads(m.group(1).replace("\\u003c", "<").replace("\\u0026", "&"))


# ---- B1–B8: classic behavioral gate -----------------------------------------------

class ClassicBehaviorTest(_Base):
    def test_first_load_rows_present(self):
        """B1: rows are SERVER-RENDERED — present with no JS run (the first-load guarantee
        the v2 default broke)."""
        self._seed("Alpha task one")
        self._seed("Beta task two")
        html = self._render(interbrain="off")
        self.assertIn('<details class="row', html)
        self.assertIn("Alpha task one", html)
        self.assertIn("Beta task two", html)
        self.assertIn('data-key="row:', html)

    def test_closed_cap_and_show_more(self):
        """B2: closed list shows 5 newest inline + a 'see more (N more)' expander."""
        for i in range(7):
            self._seed("Closed %d" % i, closed=True)
        html = self._render(interbrain="off")
        self.assertIn('id="closed-extra"', html)
        self.assertIn('data-more="2"', html)          # 7 closed − 5 inline
        self.assertIn("see more (2 more)", html)

    def test_title_marquee_hook_present(self):
        """B3: title cells carry the .ttl marquee hook + the hover-scroll behavior."""
        self._seed("A very long task title that would overflow its cell on hover")
        html = self._render(interbrain="off")
        self.assertIn('<span class="ttl">', html)
        self.assertIn("stopScroll", html)             # the hover-slide behavior script

    def test_session_resume_block_present(self):
        """B4: a task with a resume target renders a `cd … && claude --resume` block."""
        vm = {"seq": 5, "title": "t", "resume_main": {
            "command": "cd /repo && claude --resume abc123",
            "activity": "2h ago", "label": "Resume (hub)", "fresh": False}}
        html = render_board._resume_action(vm)
        self.assertIn("claude --resume", html)
        self.assertIn("cd /repo", html)

    def test_refuse_downgrade(self):
        """B7: a passive refresh must not overwrite a board stamped by a NEWER version."""
        self._seed("x")
        out = ts.write_board()
        with open(out, "w", encoding="utf-8") as f:      # forge a far-future stamp
            f.write('<meta name="ts-board-version" content="99.9.9"> sentinel-body')
        ts.write_board(guard_downgrade=True)             # passive path
        with open(out, encoding="utf-8") as f:
            self.assertIn("sentinel-body", f.read())     # left untouched

    def test_rev_sidecar_written(self):
        """B8: board.rev.js carries window.__TSREV matching the board's embedded rev."""
        self._seed("x")
        out = ts.write_board()
        with open(out, encoding="utf-8") as f:
            html = f.read()
        m = re.search(r'BOARD_REV="([0-9a-f]+)"', html)
        self.assertIsNotNone(m)
        with open(os.path.join(self.tmp, "board.rev.js"), encoding="utf-8") as f:
            rev = f.read()
        self.assertIn(m.group(1), rev)
        self.assertIn("window.__TSREV", rev)


# ---- F1: interbrain-off parity -----------------------------------------------------

class InterbrainOffParityTest(_Base):
    def test_off_with_feeds_equals_no_feeds(self):
        """B10: with interbrain OFF, a peer feed present renders byte-identical (modulo the
        version/timestamp stamp) to no feed present — the parity law: feeds never leak."""
        self._seed("Local one")
        self._seed("Local two", color="blue")
        no_feed = _normalize(self._render(interbrain="off"))
        self._write_feed(_peer_feed([_peer_task()]))
        with_feed = _normalize(self._render(interbrain="off"))
        self.assertEqual(no_feed, with_feed)

    def test_off_has_no_foreign_or_strip(self):
        self._seed("Local one")
        self._write_feed(_peer_feed([_peer_task()]))
        html = self._render(interbrain="off")
        self.assertNotIn('id="focus-strip"', html)   # the strip ELEMENT (the .focusstrip CSS class always ships)
        self.assertNotIn("read-only · foreign brain", html)
        self.assertNotIn("jpark-201", html)
        self.assertNotIn("data-owner", html)

    _HINT = "Interbrain federation is off"

    def test_off_shows_federation_hint(self):
        """B14: the ONE enumerated parity exception — an off board must SAY it is off and
        how to turn federation on. Silence was the bug (#444)."""
        self._seed("Local one")
        html = self._render(interbrain="off")
        self.assertIn(self._HINT, html)
        self.assertIn("--interbrain on", html)

    def test_on_hides_federation_hint(self):
        """The hint is state-driven, not decorative: gone when federation is on."""
        self._seed("Local one")
        self._write_feed(_peer_feed([_peer_task()]))
        self.assertNotIn(self._HINT, self._render(interbrain="on"))

    def test_hint_does_not_depend_on_why_federation_is_off(self):
        """The hint must be identical with and without peer feeds present — that is what
        keeps B10's parity compare (test_off_with_feeds_equals_no_feeds) honest."""
        self._seed("Local one")
        bare = self._render(interbrain="off")
        self._write_feed(_peer_feed([_peer_task()]))
        with_feed = self._render(interbrain="off")
        self.assertEqual(bare.count(self._HINT), 1)
        self.assertEqual(with_feed.count(self._HINT), 1)


# ---- F1: foreign VM mapping --------------------------------------------------------

class ForeignMappingTest(_Base):
    def test_feed_to_vm(self):
        feed = _peer_feed([_peer_task()])
        vm = ts._foreign_view_model(feed, feed["tasks"][0])
        self.assertTrue(vm["foreign"])
        self.assertFalse(vm["editable"])
        self.assertEqual(vm["owner"], "jpark")
        self.assertEqual(vm["owner_color"], "#4f8fe6")
        self.assertEqual(vm["handle"], "jpark-201")
        self.assertEqual(vm["brain"], "main")
        self.assertEqual(vm["color"], "green")
        self.assertEqual(vm["tag"], "FEATURE")
        self.assertEqual(vm["seq"], None)
        self.assertEqual(vm["status_display"], "paused")   # foreign active → paused, never live
        self.assertIsNone(vm["resume_main"])
        self.assertIsNone(vm["open_command"])
        self.assertEqual(vm["goal"], "serve period rollups")

    def test_closed_foreign_status(self):
        feed = _peer_feed([_peer_task(status="closed")])
        vm = ts._foreign_view_model(feed, feed["tasks"][0])
        self.assertEqual(vm["status"], "closed")
        self.assertEqual(vm["status_display"], "closed")

    def test_view_models_gated_off(self):
        self._write_feed(_peer_feed([_peer_task()]))
        os.environ["TASK_STATION_INTERBRAIN"] = "off"
        self.assertEqual(ts.foreign_view_models(self.tmp), [])
        os.environ["TASK_STATION_INTERBRAIN"] = "on"
        vms = ts.foreign_view_models(self.tmp)
        self.assertEqual(len(vms), 1)
        self.assertTrue(vms[0]["foreign"])

    def test_iife_fixture_skipped_not_crashed(self):
        """A LEGACY IIFE-wrapped feed (payload behind a JS variable) is not server-parseable
        — it must be skipped, not fatal. The shipped fixtures are canonical as of #444, so
        this guards the tolerance branch with a synthetic file, not a real one."""
        d = os.path.join(self.tmp, "feeds", "demo")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "iife.js"), "w", encoding="utf-8") as f:
            f.write("(function(){var feed={tasks:[]};window.__TSFEED_x=feed;})();\n")
        os.environ["TASK_STATION_INTERBRAIN"] = "on"
        self.assertEqual(ts.foreign_view_models(self.tmp), [])   # skipped, no crash


# ---- F1: foreign rows render read-only, memo-only ----------------------------------

class ForeignRowTest(_Base):
    def test_foreign_row_memo_only(self):
        self._seed("Local one")
        self._write_feed(_peer_feed([_peer_task()]))
        html = self._render(interbrain="on")
        self.assertIn("jpark-201", html)                       # handle chip
        self.assertIn("read-only · foreign brain", html)       # tooltip
        self.assertIn('class="ochip"', html)                   # owner chip
        self.assertIn("🔒", html)                              # lock marker
        self.assertIn("Memo", html)                            # the one enabled affordance
        # slice out the foreign row (start → next row / graph / body end) and confirm it
        # carries NO resume section (foreign has no sessions/prompts/resume).
        i = html.index('<details class="row foreign')
        ends = [x for x in (html.find('<details class="row', i + 20),
                            html.find('<div class="minigraph', i),
                            html.find('</body>', i)) if x != -1]
        frag = html[i:min(ends)]
        self.assertIn("Memo", frag)
        self.assertIn("🔒", frag)
        self.assertNotIn("claude --resume", frag)
        self.assertNotIn("Resume (hub)", frag)

    def test_self_handle_chip_when_on(self):
        self._seed("Local one")
        self._write_feed(_peer_feed([_peer_task()]))
        html = self._render(interbrain="on")
        self.assertIn('class="hchip"', html)                   # self handle chip
        self.assertIn("data-owner=", html)
        self.assertIn("data-brain=", html)


# ---- B11: the SHIPPED demo feeds federate end to end -------------------------------

class DemoFeedFederationTest(_Base):
    """The end-to-end path a user actually takes: `python3 tools/seed_demo.py`, interbrain
    on, write the board. Every synthetic peer test above builds its feed in-process from
    `_peer_feed`, so all of them passed while the SHIPPED fixtures rendered nothing at all —
    they were IIFE-wrapped and skipped server-side. These tests close that hole by driving
    the real committed fixtures through the real seeder.
    """
    def _seed_demo(self, org_label="Org brain"):
        import seed_demo
        return seed_demo.seed(self.tmp, None, org_label=org_label)

    def test_seeded_demo_yields_foreign_vms_for_every_demo_brain(self):
        """Behavior at the view-model layer: all four demo brains arrive, read-only."""
        self._seed_demo()
        os.environ["TASK_STATION_INTERBRAIN"] = "on"
        vms = ts.foreign_view_models(self.tmp)
        self.assertTrue(vms, "seeded demo feeds produced NO foreign view-models")
        self.assertEqual({vm["owner"] for vm in vms}, {"jpark", "kosei", "rnguyen", "org"})
        for vm in vms:
            self.assertTrue(vm["foreign"], vm["handle"])
            self.assertFalse(vm["editable"], vm["handle"])
            self.assertIsNone(vm["resume_main"], vm["handle"])   # never machine-local
            self.assertIsNone(vm["open_command"], vm["handle"])
            self.assertFalse(vm["live"], vm["handle"])           # liveness never federates

    def test_seeded_demo_renders_peer_rows_on_the_board(self):
        """B11 through the shipped fixtures: a user who seeds the demo feeds and turns
        federation on SEES peer work, with the owner chip and the read-only lock."""
        self._seed("Local one")
        self._seed_demo()
        html = self._render(interbrain="on")
        for handle in ("jpark-201", "kosei-140", "rnguyen-9001"):
            self.assertIn(handle, html, "%s missing from the rendered board" % handle)
        self.assertIn('class="ochip"', html)                  # owner chip
        self.assertIn("🔒", html)                             # read-only lock
        self.assertIn("read-only · foreign brain", html)
        # …and the local task is still there: peers augment, never replace.
        self.assertIn("Local one", html)

    def test_seeded_demo_renders_the_org_brain(self):
        """org.js IS the org brain: its rows render AND its org-shared tasks raise the org
        chip in the focus strip, labelled from `--org-label`."""
        self._seed("Local one")
        self._seed_demo(org_label="Company Brain")
        html = self._render(interbrain="on")
        self.assertIn("org-12", html)                         # an org row
        self.assertIn('data-focus-kind="org"', html)          # the org chip exists
        self.assertIn("Company Brain", html)                  # …labelled from config
        vms = ts.foreign_view_models(self.tmp)
        org = [vm for vm in vms if vm["owner"] == "org"]
        self.assertTrue(org)
        for vm in org:
            self.assertTrue(vm["shared_org"], vm["handle"])

    def test_seeded_demo_cross_links_to_a_real_task_in_the_graph(self):
        """The whole point of the sentinels, end to end: seed a local task carrying a PR,
        export the self feed, let the seeder rewrite `__XREF_PR_1__` to that REAL signal id,
        and the demo peer then shares a signal with the user's own task — a foreign node
        plus a dashed cross-brain edge. Also pins that foreign nodes carry a category
        colour, which comes from `category.key`."""
        import feeds
        self._seed("Local ledger work", prs=["https://github.com/o/r/pull/7"])
        ts.write_board()                                   # exports feeds/self.js
        self_feed = feeds.read_self_feed(self.tmp)
        self.assertIsNotNone(self_feed, "board write must export the self feed")
        import seed_demo
        seed_demo.seed(self.tmp, self_feed)                # binds sentinels to "o/r#7"
        data = self._mgdata(self._render(interbrain="on"))
        foreign = [n for n in data["nodes"]
                   if n.get("type") == "task" and n.get("foreign")]
        self.assertTrue(foreign, "no foreign graph nodes from the seeded demo feeds")
        for n in foreign:
            self.assertTrue(n.get("owner"), n.get("handle"))
            self.assertTrue(n.get("color"), "%s has no category colour" % n.get("handle"))
        xb = [e for e in data["edges"] if e.get("kind") == "xbrain"]
        self.assertTrue(xb, "the rewritten sentinel produced no cross-brain edge")

    def test_demo_feeds_flip_interbrain_auto_on(self):
        """`auto` resolves ON once peer feeds exist — so seeding is enough, and the user
        does not also have to know to set `--interbrain on`."""
        os.environ.pop("TASK_STATION_INTERBRAIN", None)
        self.assertFalse(ts._interbrain_on(self.tmp))          # single brain, no feeds
        self._seed_demo()
        self.assertTrue(ts._interbrain_on(self.tmp))

    def test_interbrain_off_still_hides_the_seeded_demo(self):
        """The parity law outranks the demo feeds: off means off, however many feeds sit
        on disk."""
        self._seed("Local one")
        self._seed_demo()
        html = self._render(interbrain="off")
        for handle in ("jpark-201", "kosei-140", "rnguyen-9001", "org-12"):
            self.assertNotIn(handle, html)
        self.assertNotIn("read-only · foreign brain", html)


# ---- F1: graph foreign nodes + dashed cross-brain edges ----------------------------

class GraphForeignTest(_Base):
    def test_foreign_owner_node_and_xbrain_edge(self):
        self._seed("Local ledger work", prs=["https://github.com/o/r/pull/7"])
        self._write_feed(_peer_feed([_peer_task(prs=["o/r#7"])]))
        html = self._render(interbrain="on")
        data = self._mgdata(html)
        tasks = [n for n in data["nodes"] if n.get("type") == "task"]
        # every task node carries an owner (focus filter data); a foreign node exists.
        self.assertTrue(all("owner" in n for n in tasks))
        foreign = [n for n in tasks if n.get("foreign")]
        self.assertEqual(len(foreign), 1)
        self.assertEqual(foreign[0]["owner"], "jpark")
        self.assertIn("owner_color", foreign[0])
        # a dashed cross-brain edge links the foreign node to the local task.
        xb = [e for e in data["edges"] if e.get("kind") == "xbrain"]
        self.assertEqual(len(xb), 1)


# ---- F2: focus strip + mg-data owner/brain -----------------------------------------

class FocusStripTest(_Base):
    def test_strip_and_mgdata_fields(self):
        self._seed("Local ledger work", prs=["https://github.com/o/r/pull/7"])
        self._write_feed(_peer_feed([_peer_task(prs=["o/r#7"])]))
        html = self._render(interbrain="on")
        self.assertIn('id="focus-strip"', html)
        self.assertIn('data-focus-kind="all"', html)           # Everything
        self.assertIn(">Everything<", html)
        self.assertIn('data-focus-owner="jpark"', html)        # a peer chip
        self.assertIn('ts-board-focus', html)                  # the focus JS + localStorage
        data = self._mgdata(html)
        tasks = [n for n in data["nodes"] if n.get("type") == "task"]
        self.assertTrue(any(n.get("brain") for n in tasks))
        self.assertTrue(any("owner" in n for n in tasks))


if __name__ == "__main__":
    unittest.main()
