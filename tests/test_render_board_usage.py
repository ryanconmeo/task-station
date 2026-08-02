"""HTML board usage surfaces. Board B10–B14 restructured the expanded panel to be
PER-HUB: the standalone Usage & Cost and Prompts sections are gone; instead each hub
session renders its own card baking in its prompts (all, untruncated, human-vs-generated
styled), its cost + work-mix (HUD-coloured), and its nested worker sessions (with a
per-worker cost + work-mix drill). Cost is no longer a collapsed-row column — the derived
$ is a `cost` row in each task's Overview digest. Everything is pure inline CSS/HTML — NO
external assets — and every
panel degrades gracefully to absent when its data is missing."""
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
sys.path.insert(0, LIB)
sys.path.insert(0, TOOLS)

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)

import render_board  # noqa: E402


# Inline <script>/<style> are allowed; these needles flag EXTERNAL assets only.
_EXTERNAL_NEEDLES = ('src="http', "src='http", 'src="//', "src='//",
                     "<link ", "@import", "url(http", "//fonts.")


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _vm(**kw):
    """A minimal board view-model dict (every renderer field is `.get`-guarded, so a
    partial dict suffices) merged with the usage fields under test."""
    base = {
        "seq": kw.pop("seq", 1),
        "title": kw.pop("title", "A task"),
        "full_title": kw.pop("title2", "A task"),
        "status": "open",
        "status_label": "open",
        "usage": None,
        "sessions": [],
        "phases": [],
        "prompts_preview": [],
        "hubs": [],
        "cost_thresholds": [0.01, 0.05],
    }
    base.update(kw)
    return base


_MIX_FO = [{"family": "fable", "pct": 0.8, "unpriced": False},
           {"family": "opus", "pct": 0.2, "unpriced": False}]

_USAGE_FULL = {
    "mix": _MIX_FO,
    "total_in": 1800, "total_out": 2400,
    "total_cost_usd": 0.15, "reported_cost_usd": 0.03,
    "any_unpriced": False,
    "derived_note": "Derived from your local transcript token counts × published rates.",
    "rates": [{"family": "fable", "model": "claude-fable-5", "in": 10.0, "out": 50.0,
               "w5m": 12.5, "w1h": 20.0, "read": 1.0}],
}

_PHASES = [{"label": "implementation", "pct": 0.6}, {"label": "verification", "pct": 0.4}]
_PHASES_OTHER = [{"label": "implementation", "pct": 0.7},
                 {"label": "other", "pct": 0.3,
                  "names": [{"name": "$ curl", "count": 3}, {"name": "Skill", "count": 1}]}]


def _wk(**kw):
    """A worker sub-card fixture for a hub's nested workers list."""
    base = {
        "sid8": "wk012345", "session_id": "wk012345aaaa", "label": "ws4",
        "model": "claude-opus-4-8", "live": True, "age": "1h ago",
        "resume_command": "cd /w/repo && claude --resume wk012345aaaa",
        "in": 800, "out": 400, "cache_read": 100, "cost_usd": 0.03,
        "any_unpriced": False, "reported": 0.031,
        "mix": [{"family": "opus", "pct": 1.0, "unpriced": False}],
        "phases": [{"label": "research", "pct": 1.0}],
        "prompts": [], "prompt_count": 0,
    }
    base.update(kw)
    return base


def _hub(**kw):
    """A per-hub card fixture (the board VM `hubs` entry shape)."""
    base = {
        "sid8": "hub12345", "session_id": "hub12345aaaa",
        "pinned": False, "main": False, "live": False, "role": "hub",
        "msgs": 5, "age": "2h ago", "oneliner": "build the board",
        "resume_command": "cd /w && claude --resume hub12345aaaa",
        "own": {"in": 1000, "out": 2000, "cache_read": 500, "cost_usd": 0.12,
                "any_unpriced": False, "mix": _MIX_FO, "phases": _PHASES, "msgs": 5},
        "agg": {"in": 1800, "out": 2400, "cache_read": 600, "cost_usd": 0.15,
                "any_unpriced": False, "mix": _MIX_FO, "phases": _PHASES},
        "reported": 0.03, "prompts": [], "prompt_count": 0, "workers": [],
    }
    base.update(kw)
    return base


def _full_vm(**kw):
    """A view-model exercising the expanded panel: usage (→ Overview cost row), a hub
    card with prompts + workers, an open action, decisions, history, a files entry."""
    base = _vm()
    base.update({
        "usage": dict(_USAGE_FULL),
        "hubs": [_hub(main=True, pinned=True, workers=[_wk()], prompts=[
            {"ts": 1751600000, "kind": "prompt", "text": "hub authored this",
             "role": "hub", "human": True},
            {"ts": 1751600100, "kind": "command", "text": "todo board",
             "role": "hub", "human": False},
            {"ts": 1751600200, "kind": "prompt", "text": "worker ran that",
             "role": "worker", "label": "ws4", "human": True}])],
        "open_command": "/todo 1",
        "history": [{"ts": "2026-07-02T10:00:00", "text": "milestone one"}],
        "files": [("render_board.py", "/repo/tools", "/repo/tools/render_board.py")],
        "decisions": ["went with per-hub cards"],
    })
    base.update(kw)
    return base


class CostInOverviewTest(unittest.TestCase):
    def test_cost_shows_in_overview_digest_with_mix_bar(self):
        # D1: cost is a `cost` row in the Overview digest (not a grid column). When the
        # task has a usage mix, the row restores the interactive stacked mix bar ABOVE the
        # derived $ text (same hover data-tip segments as the old column).
        html = render_board.render_html([_vm(usage=dict(_USAGE_FULL))])
        self.assertNotIn('class="mix"', html)              # the old folded chip is gone
        self.assertNotIn('class="c-cost"', html)           # cost is no longer a column
        self.assertIn('<span class="k">cost</span>', html)  # the Overview cost row
        self.assertIn("$0.15", html)                       # derived $ text
        self.assertIn('class="ccostbar"', html)            # the restored mix bar wrapper
        self.assertIn('class="mixbar"', html)              # the stacked bar itself
        self.assertIn("mseg mx-fable", html)               # its coloured segments

    def test_unpriced_never_shows_na_and_uses_unknown_colour(self):
        # The stacked mix bar now lives only in the per-hub Sessions cards: an unpriced
        # family renders with the neutral 'unknown' colour, and cost never shows "$n/a".
        mix = [{"family": "opus", "pct": 0.5, "unpriced": False},
               {"family": "mystery-x", "pct": 0.5, "unpriced": True}]
        hub = _hub(agg={"in": 0, "out": 0, "cache_read": 0, "cost_usd": 0.0,
                        "any_unpriced": True, "mix": mix, "phases": _PHASES})
        html = render_board.render_html([_vm(hubs=[hub])])
        self.assertIn("mseg mx-unknown", html)
        self.assertNotIn("$n/a", html)

    def test_no_cost_bar_when_no_usage(self):
        html = render_board.render_html([_vm(usage=None)])
        self.assertNotIn('class="ccostbar"', html)
        self.assertNotIn('class="mseg', html)
        self.assertNotIn('class="mixbar"', html)

    def test_family_colour_vars_emitted_both_variants(self):
        html = render_board.render_html([_vm(usage=dict(_USAGE_FULL))])
        self.assertIn("--mxf:", html)
        self.assertIn("--mxo:", html)
        self.assertIn(".mseg.mx-fable", html)

    def test_b4_segments_carry_styled_hover_tooltip(self):
        # B4: bar segments carry a data-tip (label · share …) and a styled ::after
        # tooltip replaces the old colour-only hover; bars are non-clipping so it shows.
        # The stacked mix bar now lives only in the per-hub Sessions cards (the collapsed
        # row's cost column + its bar were removed), so drive it via a hub with a mix.
        html = render_board.render_html([_vm(hubs=[_hub()])])
        self.assertIn('data-tip="fable', html)          # model-mix segment tip
        self.assertIn("[data-tip]:hover::after", html)  # the styled tooltip rule
        self.assertIn("overflow:visible", html)         # bar no longer clips the tooltip
        self.assertIn(".mixbar>.mseg:first-child", html)  # rounded ends preserved


class PerHubCardTest(unittest.TestCase):
    def test_hub_card_header_badges_and_cost(self):
        html = render_board.render_html([_vm(hubs=[_hub(main=True, pinned=True, live=True)])])
        self.assertIn('class="hubcard main pinned"', html)
        self.assertIn('class="hbadge b-main"', html)       # MAIN badge
        self.assertIn('class="hbadge b-pin"', html)        # pinned badge
        self.assertIn("\U0001F4CC", html)                  # 📌
        self.assertIn("hub12345", html)                    # short sid
        self.assertIn("build the board", html)             # one-liner
        self.assertIn('class="sbadge sb-run"', html)       # running badge (live=True → running)
        self.assertIn("running", html)
        # aggregate $ coloured by the stdev band scheme (0.15 > hi=0.05 → cost-hi).
        self.assertIn("cost-hi", html)

    def test_main_hub_floats_first_and_highlight_distinct_from_pinned(self):
        # VM already sorts main first; the renderer keeps order. main != pinned here.
        hubs = [_hub(sid8="pinnedhub", pinned=True, oneliner="the pinned one"),
                _hub(sid8="mainhub00", main=True, oneliner="the main one")]
        # simulate the VM sort (main → pinned → rest)
        hubs.sort(key=lambda c: (0 if c["main"] else 1, 0 if c["pinned"] else 1))
        html = render_board.render_html([_vm(hubs=hubs)])
        self.assertLess(html.index("the main one"), html.index("the pinned one"))
        self.assertIn('class="hubcard main"', html)        # main highlight
        self.assertIn('class="hubcard pinned"', html)      # pinned highlight (distinct)

    def test_no_standalone_usage_or_prompts_sections(self):
        html = render_board.render_html([_full_vm()])
        for gone in ("Usage &amp; Cost", "Recent prompts", "All prompts",
                     "How these numbers are derived", "Resume the hub session"):
            self.assertNotIn(gone, html)

    def test_open_action_kept_resume_hub_block_dropped(self):
        # B10: the redundant "Resume the hub session" action is gone; "Open the task" stays.
        html = render_board.render_html([_full_vm()])
        self.assertIn("Open the task", html)
        self.assertNotIn("Resume the hub session", html)

    def test_resume_action_shown_when_no_hubs(self):
        # Item 6 (/todo 364): a small task with NO per-hub cards still surfaces the full
        # `cd … && claude --resume …` from resume_main — previously only /todo <seq> showed.
        rm = {"command": "cd /repo && claude --resume abc123zzz", "activity": "3h ago",
              "pinned": False, "fresh": False, "label": "Resume (hub)"}
        html = render_board.render_html([_vm(resume_main=rm)])   # hubs=[] by default
        self.assertIn("--resume abc123zzz", html)
        self.assertIn("Resume (hub)", html)

    def test_resume_action_absent_when_hubs_present(self):
        # Hub cards carry their own resume line, so the fallback must NOT duplicate it.
        rm = {"command": "cd /repo && claude --resume dupNOTshown", "activity": "3h ago",
              "pinned": False, "fresh": False, "label": "Resume (hub)"}
        html = render_board.render_html([_vm(resume_main=rm, hubs=[_hub()])])
        self.assertNotIn("dupNOTshown", html)

    def test_pr_link_opens_new_tab(self):
        # Item 4: external PR/story URLs open in a new tab (Safari/basicurlrouter).
        out = render_board._pr_line({"url": "https://x/pr/643", "desc": "fix"})
        self.assertIn('target="_blank"', out)
        self.assertIn('rel="noopener noreferrer"', out)

    def test_three_tier_state_badges(self):
        # Q12: running / resumable / linked render as distinct badges, not one "live";
        # the pre-rename "attached" state still renders the linked badge (back-compat).
        for state, cls, word in (("running", "sb-run", "running"),
                                  ("resumable", "sb-res", "resumable"),
                                  ("linked", "sb-att", "linked"),
                                  ("attached", "sb-att", "linked")):
            html = render_board.render_html([_vm(hubs=[_hub(state=state, live=False)])])
            self.assertIn('class="sbadge %s"' % cls, html)
            self.assertIn(word, html)
        # The old always-green "live" badge is gone from hub cards.
        html = render_board.render_html([_vm(hubs=[_hub(state="resumable", live=False)])])
        self.assertNotIn('class="hclive"', html)

    def test_state_beats_legacy_live_flag(self):
        # An explicit resumable state must NOT be overridden by a stale live=True.
        html = render_board.render_html([_vm(hubs=[_hub(state="resumable", live=True)])])
        self.assertIn("resumable", html)

    def test_hub_prompts_all_untruncated(self):
        # B6/B7: ALL prompts (not last-5), FULL text (no ellipsis clip).
        long_text = "please " + ("x" * 800)
        prompts = [{"ts": i, "kind": "prompt", "text": "p%d %s" % (i, long_text),
                    "role": "hub", "human": True} for i in range(9)]
        html = render_board.render_html([_vm(hubs=[_hub(prompts=prompts, prompt_count=9)])])
        self.assertIn("prompts (9)", html)                 # count, not "recent"
        self.assertIn("x" * 800, html)                     # full text, not clipped
        self.assertIn("p8 please", html)                   # the 9th (last) prompt shows

    def test_human_vs_generated_prompt_styling(self):
        prompts = [
            {"ts": 1, "kind": "prompt", "text": "a real human prompt",
             "role": "hub", "human": True},
            {"ts": 2, "kind": "command", "text": "save", "role": "hub", "human": False},
        ]
        html = render_board.render_html([_vm(hubs=[_hub(prompts=prompts, prompt_count=2)])])
        self.assertIn("pmt p-hub human", html)             # human axis
        self.assertIn("pmt p-hub gen", html)               # generated axis
        self.assertIn(".pmt.gen", html)                    # styling rule present
        self.assertIn(".pmt.human .pk", html)

    def test_prompt_text_escaped(self):
        html = render_board.render_html([_vm(hubs=[_hub(prompts=[
            {"ts": 1, "kind": "prompt", "text": "danger <script>x</script>",
             "role": "hub", "human": True}], prompt_count=1)])])
        self.assertNotIn("<script>x</script>", html)
        self.assertIn("&lt;script&gt;x&lt;/script&gt;", html)

    def test_hub_cost_and_workmix_section(self):
        html = render_board.render_html([_vm(hubs=[_hub()])])
        self.assertIn("cost &amp; work-mix", html)
        self.assertIn('class="mono tok"', html)            # token counts in the cost line
        self.assertIn('class="pbar-mix"', html)            # work-mix bar
        self.assertIn("implementation 60%", html)
        self.assertIn("verification 40%", html)

    def test_sessions_line_uses_running_resumable_vocabulary(self):
        # the header uses the canonical session states; zero clauses drop out.
        line = render_board._sessions_line(
            {"hubs": 2, "workers": 1, "running": 1, "resumable": 1, "live_hubs": 2})
        self.assertEqual(line, "2 hubs · 1 running · 1 resumable · 1 worker")
        self.assertEqual(render_board._sessions_line(
            {"hubs": 1, "workers": 0, "running": 0, "resumable": 1, "live_hubs": 1}),
            "1 hub · 1 resumable")
        # legacy view-model (no new keys) keeps the old "(N live)" clause.
        self.assertEqual(render_board._sessions_line(
            {"hubs": 2, "workers": 0, "live_hubs": 2}), "2 hubs (2 live)")

    def test_row_carries_data_sess_for_session_filter(self):
        html = render_board.render_html([_vm(session_tree={
            "hubs": 1, "workers": 0, "running": 1, "resumable": 0})])
        self.assertIn('data-sess="running"', html)
        html = render_board.render_html([_vm(session_tree={
            "hubs": 1, "workers": 0, "running": 0, "resumable": 1})])
        self.assertIn('data-sess="resumable"', html)
        html = render_board.render_html([_vm()])
        self.assertIn('data-sess="none"', html)

    def test_workmix_colors_keyed_by_label_not_position(self):
        # "planning" (etc.) is the SAME colour in every bar regardless of share order.
        self.assertEqual(render_board._phase_class("planning"), "pc0")
        self.assertEqual(render_board._phase_class("research"), "pc1")
        self.assertEqual(render_board._phase_class("implementation"), "pc2")
        self.assertEqual(render_board._phase_class("verification"), "pc3")
        self.assertEqual(render_board._phase_class("delivery"), "pc4")
        self.assertEqual(render_board._phase_class("other"), "pc5")
        # an unknown label gets a STABLE bucket (same class every call), never an index.
        self.assertEqual(render_board._phase_class("mystery"),
                         render_board._phase_class("mystery"))
        # a bar led by verification still colours verification as pc3 (not first=pc0).
        blk = render_board._workmix_block([{"label": "verification", "pct": 0.7},
                                           {"label": "planning", "pct": 0.3}])
        self.assertIn('class="pseg pc3" style="width:70', blk)
        self.assertIn('class="pdot pc0"', blk)

    def test_other_workmix_drilldown(self):
        # B5: the "other" slice shows its top tool/command contributors.
        h = _hub(agg={"in": 100, "out": 100, "cache_read": 0, "cost_usd": 0.05,
                      "any_unpriced": False, "mix": _MIX_FO, "phases": _PHASES_OTHER})
        html = render_board.render_html([_vm(hubs=[h])])
        self.assertIn('class="otherdrill"', html)
        self.assertIn("$ curl", html)
        self.assertIn("&times;3", html)
        self.assertIn("Skill", html)

    def test_nested_workers_expandable_with_drill(self):
        # B12 + B14: worker sessions nested, each with its own cost + work-mix + resume.
        html = render_board.render_html([_vm(hubs=[_hub(workers=[_wk()])])])
        self.assertIn('<details class="workers"', html)
        self.assertIn("worker sessions (1)", html)
        self.assertIn('class="wcard"', html)
        self.assertIn("worker:ws4", html)
        self.assertIn("claude-opus-4-8", html)             # worker model
        self.assertIn("cd /w/repo &amp;&amp; claude --resume wk012345aaaa", html)
        self.assertIn("research 100%", html)               # per-worker work-mix

    def test_cost_colour_scheme_rules_present(self):
        html = render_board.render_html([_vm(hubs=[_hub()])])
        # HUD-matched hexes: muted-blue tokens + green/amber/orange cost bands.
        self.assertIn(".tok{color:#5a87af}", html)
        self.assertIn(".cost-lo{color:#b4dc6e}", html)
        self.assertIn(".cost-mid{color:#f0be50}", html)
        self.assertIn(".cost-hi{color:#e67850}", html)

    def test_bare_task_has_no_hub_cards(self):
        html = render_board.render_html([_vm(seq=9, title="Bare task")])
        self.assertNotIn('class="hubcard', html)
        self.assertIn("Bare task", html)

    def test_hubs_self_contained(self):
        html = render_board.render_html([_full_vm()])
        for needle in _EXTERNAL_NEEDLES:
            self.assertNotIn(needle, html)


class BoardSectionsTest(unittest.TestCase):
    """The expanded-row sections: overview + sessions + history remain (the old cost +
    prompts sections were folded into the per-hub cards, B13/B14)."""

    def test_sections_with_colored_headers(self):
        html = render_board.render_html([_full_vm()])
        self.assertIn('<details class="sec sec-overview" open', html)
        for slug in ("sec-sessions", "sec-history"):
            self.assertIn('class="sec %s"' % slug, html)
        # the removed cost/prompts sections no longer render as their own <details>.
        self.assertNotIn('class="sec sec-cost"', html)
        self.assertNotIn('class="sec sec-prompts"', html)
        for var in ("--sec_ov:", "--sec_sess:", "--sec_hist:"):
            self.assertIn(var, html)

    def test_section_open_state_persists_via_data_key(self):
        html = render_board.render_html([_full_vm(seq=5)])
        self.assertIn('<details class="sec sec-overview" open', html)
        self.assertIn('data-key="sec-sessions:5"', html)
        self.assertIn('data-key="hist:5"', html)

    def test_longlist_scroll_boxes(self):
        html = render_board.render_html([_full_vm()])
        self.assertIn(".longlist{max-height:16em;overflow-y:auto;overscroll-behavior:contain}",
                      html)
        self.assertIn('class="decisions longlist"', html)
        self.assertIn('class="hbody longlist"', html)
        self.assertIn("went with per-hub cards", html)
        self.assertIn('class="pbody longlist"', html)      # hub prompts scroll box

    def test_six_column_grid_no_live_column(self):
        # Steps/cost/story are not columns (moved into the Overview digest). The separate
        # live column is GONE (folded into the status pill) → 6 tracks: status # task
        # category effort activity.
        self.assertEqual(len(render_board._COLS.split()), 6)
        html = render_board.render_html([_full_vm()])
        self.assertNotIn('<span class="c-steps">steps</span>', html)
        self.assertNotIn('<span class="c-cost">cost</span>', html)
        self.assertNotIn('<span class="c-story">story</span>', html)
        self.assertNotIn('<span class="c-live"', html)                       # no live column
        self.assertNotIn('class="livedot"', html)                            # no per-row dot

    def test_bar_segment_hover_rules(self):
        html = render_board.render_html([_full_vm()])
        self.assertIn(".mseg:hover,.pseg:hover{filter:brightness(1.25)", html)
        self.assertIn(".pbar-mix:hover .pseg:not(:hover)", html)

    def test_files_are_editor_scheme_links_with_copy(self):
        html = render_board.render_html([_full_vm()])
        self.assertIn('<a class="fopen" href="vscode://file/repo/tools/render_board.py">', html)
        self.assertIn("render_board.py</a>", html)
        self.assertIn('class="copybtn fcopy"', html)

    def test_files_legacy_two_tuple_tolerated(self):
        html = render_board.render_html([_full_vm(files=[("x.py", "/repo/lib")])])
        self.assertIn('href="vscode://file/repo/lib/x.py"', html)


class LiveStripTest(unittest.TestCase):
    def test_live_strip_present_when_sessions_running(self):
        live = [{"pid": 500, "task_seq": 3, "task_title": "Running task",
                 "role": "hub", "status": "busy",
                 "resume_command": "cd /w && claude --resume abc"}]
        html = render_board.render_html([_vm(seq=3, title="Running task")],
                                        live_sessions=live)
        self.assertIn("livechip", html)
        self.assertIn("task #3", html)
        # §3: busy → the single activity dot BREATHES (active), with an active tooltip.
        self.assertIn('class="ldot active"', html)
        self.assertIn("working — busy right now", html)

    def test_live_strip_absent_by_default(self):
        html = render_board.render_html([_vm(seq=3)])
        self.assertNotIn('class="livechip', html)

    def test_live_chip_task_linked_is_anchor_that_opens_row(self):
        live = [{"pid": 500, "task_seq": 3, "role": "hub", "status": "busy",
                 "resume_command": "cd /w && claude --resume abc"}]
        html = render_board.render_html([_vm(seq=3)], live_sessions=live)
        self.assertIn('<a class="livechip r-hub" href="#task-3"', html)
        self.assertIn("openTaskRow", html)
        self.assertIn("classList.toggle('hl'", html)

    def test_live_chip_task_less_is_inspectable(self):
        # A task-less session is a fixed .liverow in the stacked list — its fields
        # (pid/session/role/label/cwd + resume) are shown inline with a dim caption, so
        # nothing expands/reflows and the pid is identifiable in place.
        live = [{"pid": 90161, "task_seq": None, "role": "worker", "status": "idle",
                 "session_id": "abcdef012345", "cwd": "/w/repo", "label": "ws6",
                 "resume_command": "claude --resume abcdef01"}]
        html = render_board.render_html([_vm()], live_sessions=live)
        self.assertIn('class="liverow', html)          # the fixed list row (no inline <details>)
        self.assertNotIn('livechip-x', html)           # the old reflowing chip is gone
        self.assertIn("no task", html)                 # task-less marker
        self.assertIn("90161", html)                   # pid
        self.assertIn("abcdef01", html)                # session (8-char) + resume
        self.assertIn("/w/repo", html)                 # cwd shown inline
        self.assertIn("ws6", html)                     # label shown inline

    def test_live_strip_collapsible_with_count(self):
        # L1: the strip is a <details> collapsed by default with the count in the summary.
        live = [{"pid": 1, "task_seq": 3, "role": "hub", "status": "busy"},
                {"pid": 2, "task_seq": None, "role": "worker", "status": "idle"}]
        html = render_board.render_html([_vm(seq=3)], live_sessions=live)
        self.assertIn('<details class="livestrip"', html)
        self.assertIn("Live sessions (2)", html)        # count in the summary
        self.assertNotIn('<details class="livestrip" open', html)  # collapsed by default

    def test_live_row_single_activity_dot(self):
        # §3: the three overlapping indicators are consolidated to ONE activity dot. No
        # _state_badge in the strip, no separate activity text field. Idle → static dim dot
        # with an idle tooltip; the strip uses active/idle vocabulary (not running).
        live = [{"pid": 1, "task_seq": 3, "role": "hub", "status": "idle"}]
        html = render_board.render_html([_vm(seq=3)], live_sessions=live)
        self.assertNotIn('class="sbadge', html)                 # no running state badge here
        self.assertNotIn('class="cap">activity', html)          # no separate activity field
        self.assertIn("idle — running, waiting", html)          # idle tooltip on the dot


class Step4BoardTest(unittest.TestCase):
    def test_breathing_keyframes_gated_by_reduced_motion(self):
        # the live-strip dots breathe (mgbreathe); an ACTIVE task breathes the WHOLE row
        # green (rowbreathe). Both are OFF under prefers-reduced-motion and performance=low.
        html = render_board.render_html([_vm()])
        self.assertIn("@keyframes mgbreathe", html)
        self.assertIn("@keyframes rowbreathe", html)                   # whole-row live breathing
        self.assertIn("details.row.stat-live", html)                   # the breathing element
        self.assertIn("prefers-reduced-motion", html)
        self.assertIn('html[data-perf="low"] details.row.stat-live{animation:none}', html)

    def test_timestamps_localized_client_side(self):
        # B-TS: a server-stamped ts is wrapped in .lts[data-ts] (epoch seconds) and a tiny
        # inline script rewrites it to the viewer's LOCAL time via toLocaleString().
        html = render_board.render_html([_vm()], generated="2026-07-15 09:00",
                                        generated_ts=1752570000,
                                        updated="2026-07-15 08:00", updated_ts=1752566400)
        self.assertIn('class="lts" data-ts="1752570000"', html)
        self.assertIn('class="lts" data-ts="1752566400"', html)
        self.assertIn("toLocaleString()", html)
        self.assertIn("2026-07-15 09:00", html)         # server-formatted no-JS fallback kept

    def test_timestamp_plain_when_no_epoch(self):
        # No epoch → plain string (unchanged), so the string-only callers/tests are intact.
        html = render_board.render_html([_vm()], generated="GEN_TS")
        self.assertIn('<span class="kgen">refreshed GEN_TS</span>', html)
        self.assertNotIn('data-ts=', html)


class ViewModelUsageTest(unittest.TestCase):
    """_board_view_model wires the persisted ledger into the render fields (usage +
    the per-hub cards). Seeds the SQLite usage ledger directly (no transcripts needed)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
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

    def _seed_task(self, title="Ledger task"):
        t = ts.new_task(title, "summary for " + title, color="green", effort="m")
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _model(self, **kw):
        m = {"in": 0, "out": 0, "cache_read": 0, "cache_w5m": 0, "cache_w1h": 0,
             "web": 0, "cost_usd": 0.0, "msgs": 1}
        m.update(kw)
        return m

    def _seed_ledger(self, t, hub_sid="hubsession01", wk_sid="workersession02"):
        store = ts._backend()
        store.upsert_session_usage({
            "session_id": hub_sid, "task_id": t["id"], "role": "hub", "label": None,
            "entrypoint": "cli", "first_ts": 100, "last_ts": 200,
            "models": {"claude-fable-5": self._model(**{
                "in": 1000, "out": 2000, "cache_read": 500, "cost_usd": 0.12, "msgs": 3})},
            "sidechain": {}, "phases": {"__v": 3,
                "implementation": {"out": 3, "msgs": 3, "cost_usd": 0.3},
                "verification": {"out": 1, "msgs": 1, "cost_usd": 0.1}},
        })
        store.upsert_session_usage({
            "session_id": wk_sid, "task_id": t["id"], "role": "worker", "label": "ws4",
            "entrypoint": "sdk-cli", "first_ts": 300, "last_ts": 400,
            "models": {"claude-opus-4-8": self._model(**{
                "in": 800, "out": 400, "cache_read": 100, "cost_usd": 0.03, "msgs": 2})},
            "sidechain": {}, "phases": {},
        })
        store.upsert_prompt({"uuid": "u1", "session_id": hub_sid, "task_id": t["id"],
                             "ts": 150, "kind": "prompt", "text": "build the board"})
        t["runs"] = [{"session_id": wk_sid, "cost_usd": 0.031, "model": "claude-opus-4-8"}]
        t["cost"] = {"total_usd": 0.031, "runs": 1}
        ts.save_task(t)
        return ts.load_task(t["id"])

    def test_view_model_carries_usage_from_ledger(self):
        t = self._seed_ledger(self._seed_task())
        vm = ts._board_view_model(ts.load_task(t["id"]), live_sids={"workersession02"})
        u = vm["usage"]
        self.assertIsNotNone(u)
        self.assertEqual(u["mix"][0]["family"], "fable")
        self.assertAlmostEqual(u["total_cost_usd"], 0.15, places=4)

    def test_view_model_hubs_from_session_meta_with_nested_worker(self):
        import json
        t = self._seed_ledger(self._seed_task())
        t = ts.load_task(t["id"])
        hub, wk = "hubsession01", "workersession02"
        t["session_meta"] = {hub: {"cwd": "/w", "ts": 1000, "role": "hub"}}
        t["pinned_session"] = hub
        t["projects"] = ["repo"]
        ts.save_task(t)
        json.dump({"%s:repo" % t["seq"]: {
            "seq": t["seq"], "project": "repo", "label": "ws4", "dir": "/w/repo",
            "model": "claude-opus-4-8", "session_id": wk, "ts": 300, "spawner": hub}},
            open(ts.DELEGATE_REGISTRY, "w"))
        vm = ts._board_view_model(ts.load_task(t["id"]), live_sids={wk})
        hubs = vm["hubs"]
        self.assertEqual(len(hubs), 1)
        h = hubs[0]
        self.assertTrue(h["main"])                          # pinned → main
        self.assertTrue(h["pinned"])
        self.assertEqual(h["sid8"], hub[:8])
        self.assertEqual(h["oneliner"], "build the board")
        self.assertAlmostEqual(h["agg"]["cost_usd"], 0.15, places=4)  # hub + worker
        self.assertAlmostEqual(h["own"]["cost_usd"], 0.12, places=4)  # hub alone
        self.assertEqual(len(h["workers"]), 1)
        w = h["workers"][0]
        self.assertEqual(w["label"], "ws4")
        self.assertTrue(w["live"])
        self.assertEqual(w["state"], "running")             # worker pid is in live_sids
        self.assertEqual(h["state"], "linked")              # hub not running, no live transcript
        self.assertAlmostEqual(w["cost_usd"], 0.03, places=4)
        self.assertEqual(w["resume_command"], "cd /w/repo && claude --resume %s" % wk)
        # the hub prompt is folded into the hub's own prompt trail.
        self.assertTrue(any(p["text"] == "build the board" for p in h["prompts"]))

    def test_view_model_unattributed_pseudo_hub_when_no_session_meta(self):
        # ledger sessions with no recorded hub still surface (nothing lost).
        t = self._seed_ledger(self._seed_task())
        vm = ts._board_view_model(ts.load_task(t["id"]))
        self.assertTrue(vm["hubs"])
        self.assertEqual(vm["hubs"][0]["role"], "unattributed")

    def test_view_model_cost_thresholds_present(self):
        t = self._seed_ledger(self._seed_task())
        vm = ts._board_view_model(ts.load_task(t["id"]))
        self.assertEqual(len(vm["cost_thresholds"]), 2)
        # <3 priced sessions → fixed fallback bands.
        self.assertEqual(vm["cost_thresholds"], [0.01, 0.05])

    def test_view_model_absent_when_no_ledger(self):
        t = self._seed_task("No ledger task")
        vm = ts._board_view_model(ts.load_task(t["id"]))
        self.assertIsNone(vm["usage"])
        self.assertEqual(vm["hubs"], [])

    def test_view_model_stats_cost_and_grid_cells(self):
        t = self._seed_ledger(self._seed_task())
        t = ts.load_task(t["id"])
        t["steps"] = [{"text": "a", "done": True}, {"text": "b", "done": False},
                      {"text": "c", "done": False}]
        ts.save_task(t)
        vm = ts._board_view_model(ts.load_task(t["id"]))
        self.assertEqual(vm["stats_cost"]["text"], "$0.15")
        self.assertEqual(vm["cost_cell"], "$0.15")
        self.assertEqual(vm["steps_cell"], "1/3")

    def test_write_board_renders_per_hub_end_to_end(self):
        import json
        t = self._seed_ledger(self._seed_task("End to end task"))
        t = ts.load_task(t["id"])
        hub, wk = "hubsession01", "workersession02"
        t["session_meta"] = {hub: {"cwd": "/w", "ts": 1000, "role": "hub"}}
        t["pinned_session"] = hub
        t["projects"] = ["repo"]
        ts.save_task(t)
        json.dump({"%s:repo" % t["seq"]: {
            "seq": t["seq"], "project": "repo", "label": "ws4", "dir": "/w/repo",
            "model": "claude-opus-4-8", "session_id": wk, "ts": 300, "spawner": hub}},
            open(ts.DELEGATE_REGISTRY, "w"))
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_board(_Args(open=False))
        path = buf.getvalue().strip().splitlines()[-1]
        with open(path, encoding="utf-8") as f:
            html = f.read()
        self.assertIn('class="hubcard', html)
        self.assertIn("mseg mx-fable", html)
        self.assertIn("worker sessions (1)", html)
        self.assertIn("worker:ws4", html)
        self.assertIn("cost &amp; work-mix", html)
        self.assertNotIn("Usage &amp; Cost", html)
        for needle in _EXTERNAL_NEEDLES:
            self.assertNotIn(needle, html)


if __name__ == "__main__":
    unittest.main()
