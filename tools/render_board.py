#!/usr/bin/env python3
"""Render a (mostly) self-contained HTML board of all task-station tasks.

Mirrors the terminal `/todo` board: TWO sections (Open, then Closed), each a GRID
with the same columns — status · # · Task · Category · Effort · Activity — and
every row EXPANDABLE via native <details>/<summary>. A row with steps carries a
compact progress bar + N/M in its Task cell. The expanded row leads with the FULL
(untruncated) title, then the structured digest (goal · next · steps
checklist with rollup · decisions · repos · PRs · stories · files), then the Open/Resume
actions block, a de-emphasised Workers subsection, the full task summary, and — LAST,
collapsed — the on-demand HISTORY trail (the `--log` dated milestones).

SELF-CONTAINMENT (board is a LOCAL file opened in a browser): inline `<script>`
and inline `<style>` ARE allowed (theme toggle + hover-scroll need them), but NO
EXTERNAL assets — no `src="http"`, no `<link >`, no `@import`, no `url(http`, no
remote fonts. All injected text (summary/goal/decisions/history) is HTML-ESCAPED (mdlite)
first, so it stays inert even with JS present. NO server, NO deps beyond the
stdlib + the optional `categories` module (for per-category palettes per VARIANT),
NO LLM, no network. Every value comes from the view-models task-station.py hands
in, so this module is import-safe and unit-testable on plain dicts."""
import colorsys
import html
import json
import math
import os
import re
import sys
from datetime import datetime, timezone

# categories is optional (same guard as task-station.py): without it the board
# still renders, just without per-category colour.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
try:
    import categories as _cats
except Exception:
    _cats = None

# mdlite (sibling in tools/) renders the summary's light-markdown subset. Optional:
# without it the summary still shows, just html-escaped + unformatted. Either way the
# text is escaped first, so the board stays self-contained.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import mdlite as _md
except Exception:
    _md = None

# Page chrome per VARIANT. BOTH palettes are embedded in the page (req 4) so the
# in-page light/dark toggle can switch chrome without a reload; `--open` is the
# distinct background for an EXPANDED row (req 6). Per-CATEGORY colour comes from
# theme_palette and is emitted as per-variant CSS variables (see _category_css).
_PAGE = {
    "dark": {
        "page": "#0d0e11", "panel": "#16181d", "panel2": "#1b1e24", "code": "#0b0c0f",
        "open": "#23272f",
        "ink": "#e8e6e0", "dim": "#8b8f99", "line": "#262a31", "accent": "#d7b5fb",
        "so": "#5bc8f5", "sob": "rgba(91,200,245,.14)",
        "sa": "#b6e85a", "sab": "rgba(182,232,90,.16)",
        "sc": "#9aa0ab", "scb": "rgba(154,160,171,.14)",
        # `paused` status hue (yellow/amber) — in-progress with no live session.
        "sp": "#e6c15a", "spb": "rgba(230,193,90,.15)",
        # WS4 model-mix segment colours (one per family + unknown), light-on-dark.
        "mxf": "#c3a6f5", "mxo": "#7aa2f7", "mxs": "#9ece6a", "mxh": "#e0af68",
        "mxu": "#8b8f99",
        # WS6 collapsible-section header hues — muted, mutually distinct, and distinct
        # from --accent (purple). Rendered as coloured text + a 2px left border (never
        # a filled background). One per section: overview/cost/prompts/sessions/history.
        "sec_ov": "#7aa2f7", "sec_cost": "#9ece6a", "sec_pr": "#e0af68",
        "sec_sess": "#5cc2c2", "sec_hist": "#c98a9a",
        # relations-graph signal-hub / spoke colours (pr/repo/story), light-on-dark.
        "mg-pr": "#e0764f", "mg-repo": "#8bb562", "mg-story": "#5aa6dd",
    },
    "light": {
        "page": "#f3efe7", "panel": "#fbf8f2", "panel2": "#ece7db", "code": "#fffdf8",
        "open": "#e3dccb",
        "ink": "#2b2823", "dim": "#6c665c", "line": "#dcd5c8", "accent": "#7a4fb0",
        "so": "#1d6fa5", "sob": "rgba(29,111,165,.12)",
        "sa": "#3c7a16", "sab": "rgba(60,122,22,.13)",
        "sc": "#6c665c", "scb": "rgba(108,102,92,.13)",
        # `paused` status hue (amber), darkened to read on the cream page.
        "sp": "#a5741a", "spb": "rgba(165,116,26,.13)",
        # WS4 model-mix segment colours, darker so they read on the cream page.
        "mxf": "#7a4fb0", "mxo": "#2d5fb0", "mxs": "#3c7a16", "mxh": "#a5741a",
        "mxu": "#6c665c",
        # WS6 collapsible-section header hues, darker so they read on the cream page.
        "sec_ov": "#2d5fb0", "sec_cost": "#3c7a16", "sec_pr": "#a5741a",
        "sec_sess": "#0f7a7a", "sec_hist": "#9a5a68",
        # relations-graph signal-hub / spoke colours (pr/repo/story), darker for the page.
        "mg-pr": "#c05a3c", "mg-repo": "#6c8f4a", "mg-story": "#1d6fa5",
    },
}

# The order page palette vars are emitted (a per-variant block carries all of them).
_PAGE_KEYS = ("page", "panel", "panel2", "code", "open", "ink", "dim", "line",
              "accent", "so", "sob", "sa", "sab", "sc", "scb", "sp", "spb",
              "mxf", "mxo", "mxs", "mxh", "mxu",
              "sec_ov", "sec_cost", "sec_pr", "sec_sess", "sec_hist",
              "mg-pr", "mg-repo", "mg-story")

# The grid column template, shared by the header row and every <summary> so the
# columns line up across the (separate) grid containers — alignment across grids
# needs fixed/fr tracks, never `auto`. status · # · Task · Category · Effort · Activity.
# The separate leading `live` activity-dot column is GONE — its meaning is folded into
# the status pill (a running session → the `live` state). Six tracks now: status · # ·
# Task · Category · Effort · Activity. status widened 94→100px to fit the widest word
# ("paused"). Cost and Story live in each task's Overview digest; Steps is not a column.
_COLS = "100px 52px minmax(0,1fr) 120px 118px 96px"
_COLS_NARROW = "84px 42px minmax(0,1fr)"   # status · # · task on a narrow viewport

# The board's 4-state DISPLAY status (folds the live-session signal into the stored
# status). Keyed by `status_display` from the view-model: new (untouched) · paused
# (in-progress, no running session) · live (a session is RUNNING now) · closed. The
# green breathing state is called `live` — "active" is reserved for the stored task
# lifecycle (open/active/closed), so the word never means two things. The stored
# status and every stored-status contract are unchanged — this is display-only.
_STATUS_GLYPH = {"new": "○", "paused": "◐", "live": "●", "closed": "✕"}
_STATUS_LABEL = {"new": "new", "paused": "paused", "live": "live", "closed": "closed"}

# Open/resume commands must never wrap — they scroll within their own box instead
# of widening the page (req 8). Applied inline so the style sits ON the element.
_CMD_STYLE = "white-space:nowrap;overflow-x:auto"

# localStorage key for the persisted theme MODE (auto | light | dark; survives the
# change-driven reload). Default when unset is 'auto' (follow the OS appearance).
_THEME_KEY = "ts-board-theme"

# localStorage key for the set of EXPANDED <details> data-keys — so EVERY persistable
# row (task rows, config rows, category rows, worker sub-details) survives the opt-in
# change-driven reload (same persistence idea as the theme toggle).
_OPEN_KEY = "ts-board-open"

# sessionStorage key for the page scroll position (per-tab) — restored after the opt-in
# change-driven reload so the full reload no longer jumps the page to the top (1.30.0).
_SCROLL_KEY = "ts-board-scroll"


def _palette_decls(pg):
    return "".join("--%s:%s;" % (k, pg[k]) for k in _PAGE_KEYS if k in pg)


def _css(default_variant, category_css):
    """The full stylesheet: a `:root` carrying the RESOLVED-variant chrome (so the
    first paint matches the config before JS runs — no flash) plus the layout
    tokens, then BOTH variant palettes under `html[data-theme="dark|light"]` (the
    toggle flips the attribute), the per-category colour variables, and the body."""
    defpg = _PAGE.get(default_variant if default_variant in _PAGE else "dark", _PAGE["dark"])
    root = (":root{" + _palette_decls(defpg) +
            "--cols:" + _COLS + ";"
            '--mono:ui-monospace,"SF Mono",Menlo,"Cascadia Code",Consolas,monospace;'
            '--sans:"Inter",system-ui,-apple-system,"Segoe UI",sans-serif;}\n')
    themes = ('html[data-theme="dark"]{%s}\n'
              'html[data-theme="light"]{%s}\n'
              % (_palette_decls(_PAGE["dark"]), _palette_decls(_PAGE["light"])))
    body = """
  *{box-sizing:border-box;margin:0;padding:0}
  html,body{max-width:100%;overflow-x:hidden}
  /* the root paints the themed page colour too (not just <body>) so any frame painted
     before <body> lays out shows the variant background, not white (1.30.0 flash-fix;
     the head init also sets this inline for the very first frame). */
  html{background:var(--page)}
  body{background:var(--page);color:var(--ink);font-family:var(--sans);
    -webkit-font-smoothing:antialiased;line-height:1.5;padding:40px 28px 60px}
  .wrap{max-width:1180px;margin:0 auto}
  /* align-items:center so the theme toggle sits in line with the big "/todo board"
     <h1>, vertically centered against the header (not floating above its baseline). */
  .hdr{display:flex;justify-content:space-between;align-items:center;gap:16px}
  /* the kicker carries "claude code • task-station" on the LEFT (.kleft, words sitting
     together with a small gap + a dimmed "•" separator) and the "refreshed <ts>" on the
     RIGHT (.kgen), space-between across the page width. */
  .kicker{display:flex;justify-content:space-between;align-items:center;gap:8px;
    font-family:var(--mono);font-size:12px;letter-spacing:.06em;color:var(--dim)}
  .kleft{display:flex;gap:8px;align-items:center}
  .kgen{color:var(--dim)}
  /* the kicker's right group — "refreshed <ts>" label sitting next to the light
     text Refresh button (1.37.0: moved up here from beside the theme toggle). */
  .kright{display:flex;align-items:center;gap:10px}
  /* a light, kicker-appropriate text button — blends into the small dim kicker
     line just right of "refreshed <ts>" (NOT the boxy .toggle chrome). */
  .krefresh{font-family:var(--mono);font-size:11px;color:var(--dim);background:none;
    border:none;padding:0;cursor:pointer;letter-spacing:.04em}
  .krefresh:hover{color:var(--ink)}
  .ksep{opacity:.5}
  h1{font-size:28px;font-weight:650;letter-spacing:-.02em;margin:6px 0 6px}
  .lede{color:var(--dim);font-size:14px;max-width:80ch}
  .hdrbtns{display:flex;gap:8px;align-items:center;flex:none}
  .toggle{flex:none;font-family:var(--mono);font-size:11.5px;color:var(--dim);
    background:var(--panel2);border:1px solid var(--line);border-radius:7px;
    padding:6px 11px;cursor:pointer;white-space:nowrap}
  .toggle:hover{color:var(--ink);border-color:var(--accent)}
  .sec{display:flex;align-items:baseline;gap:12px;margin:32px 0 10px;padding-bottom:8px;
    border-bottom:1px solid var(--line)}
  .sec h2{font-size:19px;font-weight:650;letter-spacing:-.01em}
  .sec .count{font-family:var(--mono);font-size:12.5px;color:var(--dim)}

  .board{border:1px solid var(--line);border-radius:12px;overflow:hidden;background:var(--panel)}
  .head,summary.rowsum{display:grid;grid-template-columns:var(--cols);align-items:center;
    gap:0 14px;padding:9px 14px}
  .head{font-family:var(--mono);font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;
    color:var(--dim);background:var(--panel2);border-bottom:1px solid var(--line)}
  /* B18: uniform header cells. The header row reuses the per-column data classes
     (.c-status/.c-act/.c-eff/.c-seq/.c-task), whose own font-size/weight/colour would
     otherwise bleed in and make each column heading look different. Force one style
     across every header cell (specificity beats the single-class column rules). */
  .head>span{font-family:var(--mono);font-size:10.5px;font-weight:600;letter-spacing:.1em;
    text-transform:uppercase;color:var(--dim);display:block}
  /* the left accent stripe is the category's BACKGROUND colour (req 7), via the
     per-variant --cat-bg variable; the inline fallback is the resolved-variant bg. */
  details.row{border-bottom:1px solid var(--line);border-left:4px solid var(--accent)}
  details.row:last-child{border-bottom:none}
  details.row.closed{opacity:.62}
  summary.rowsum{cursor:pointer;list-style:none}
  summary.rowsum::-webkit-details-marker{display:none}
  summary.rowsum:hover{background:var(--panel2)}
  /* an EXPANDED row is clearly set apart from its neighbours: the --open background
     PLUS a top+bottom accent boundary frame the WHOLE card (req 5). Works in both
     variants since --open/--accent are defined per variant. */
  details.row[open]{background:var(--open);border-top:1px solid var(--accent);border-bottom:1px solid var(--accent)}
  /* the open summary shares the detail's --open background (NO panel2, NO 2px accent
     divider) so the header and its content read as ONE card — distinct but NOT
     separated by a line (req 5). The header stays distinguishable via the left accent
     stripe, the disclosure triangle, and the column layout. */
  details.row[open]>summary.rowsum{background:var(--open)}
  /* a quick, smooth opening animation for collapsible content (rows + inner sections +
     the graph panel): on open, the content slides down + fades in over ~160ms. Native
     <details> can't animate the CLOSE, so only the open is animated. Off under
     prefers-reduced-motion and performance=low. */
  @keyframes tsopen{from{opacity:0;transform:translateY(-5px)}to{opacity:1;transform:none}}
  details.row[open]>.detail,details.sec[open]>.secbody,
  details.minigraph[open]>.mgwrap,details.seemore[open]>*:not(summary),
  details.catrow[open]>.catdetail{animation:tsopen .16s ease-out}
  @media (prefers-reduced-motion: reduce){
    details.row[open]>.detail,details.sec[open]>.secbody,
    details.minigraph[open]>.mgwrap,details.seemore[open]>*:not(summary),
    details.catrow[open]>.catdetail{animation:none}
  }
  html[data-perf="low"] details[open]>*{animation:none!important}
  .c-task{font-weight:600;font-size:14.5px;letter-spacing:-.01em;
    display:flex;align-items:center;gap:8px;min-width:0}
  .c-task .disc{flex:none;color:var(--dim);font-size:11px;transition:transform .12s}
  details.row[open] .c-task .disc{transform:rotate(90deg)}
  /* the collapsed title: ellipsis until hover, then JS auto-scrolls it (req 2).
     overflow:hidden keeps the scrollbar hidden; flex:1+min-width:0 fixes its width
     so the auto-scroll never shifts layout. */
  /* B1: hover marquee via text-indent (robust — no scrollLeft/overflow-x quirks). The
     title clips with an ellipsis at rest; on hover JS sets a linear text-indent
     transition to slide the full title left, then snaps back instantly on leave. */
  .c-task .ttl{flex:1;min-width:0;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;
    transition:text-indent 0s linear}
  /* an EXPANDED row's task title shows in the accent colour (reverts on collapse). */
  details.row[open] .c-task .ttl{color:var(--accent)}
  /* while scrolling, drop the ellipsis so the full title reads as it slides. */
  .c-task .ttl.scrolling{text-overflow:clip}
  /* WS4: a muted `↳ from #N` relation marker on the collapsed row. flex:none so it
     never disturbs the title's hover auto-scroll layout. */
  .c-task .relfrom{flex:none;font-family:var(--mono);font-size:10.5px;color:var(--dim)}
  /* clickable related-task references (#N). In the collapsed-row chip they stay subtle
     (inherit the dim colour, dotted underline); in the expanded digest they take the
     accent colour like other links. */
  .rellink{color:var(--accent);text-decoration:none;cursor:pointer}
  .rellink:hover{text-decoration:underline}
  .relfrom .rellink{color:inherit;text-decoration:underline;text-decoration-style:dotted}
  .relfrom .rellink:hover{color:var(--accent)}
  /* the # cell uses --ink (not the faded --dim) + a touch heavier so the task
     NUMBER reads clearly — modestly more pronounced, not loud (req 4). */
  .c-seq{font-family:var(--mono);font-size:12px;color:var(--ink);font-weight:600}
  .c-act{font-family:var(--mono);font-size:11.5px;color:var(--dim)}
  .c-eff{font-family:var(--mono);font-size:12px;color:var(--ink)}
  .c-eff .gauge{letter-spacing:1px;margin-right:5px}
  /* status: a LABELED, clearly non-interactive pill — never a bare glyph */
  .pill{display:inline-flex;align-items:center;gap:5px;cursor:default;font-family:var(--mono);
    font-weight:650;font-size:10.5px;letter-spacing:.04em;border-radius:99px;padding:2px 9px;
    border:1px solid currentColor;white-space:nowrap}
  .pill.new{color:var(--so);background:var(--sob)}
  .pill.paused{color:var(--sp);background:var(--spb)}
  .pill.live{color:var(--sa);background:var(--sab)}
  .pill.closed{color:var(--sc);background:var(--scb)}
  /* category tag colours come from the per-variant --cat-* variables (inline
     fallback = resolved-variant hex), so the tag re-tints with the theme toggle. */
  .tag{display:inline-flex;align-items:center;font-family:var(--mono);font-weight:650;
    font-size:11px;letter-spacing:.03em;border:1px solid currentColor;border-radius:99px;
    padding:1px 9px;white-space:nowrap}

  .detail{padding:14px 16px 16px;display:grid;gap:13px;background:var(--open)}
  /* the FULL, untruncated title leads the expanded detail (req 1) — wrap is fine. */
  .fulltitle{font-size:16px;font-weight:650;letter-spacing:-.01em;line-height:1.3;
    overflow-wrap:anywhere}
  .k{font-family:var(--mono);font-size:10.5px;letter-spacing:.09em;text-transform:uppercase;
    color:var(--dim);margin-bottom:4px}
  /* Decisions/Summary are collapsible overview sub-sections (default collapsed): the
     <summary class="k"> stays a clickable dim label with a caret affordance, and the
     body gets a little breathing room once expanded. */
  .ovsec>summary.k{cursor:pointer;list-style:revert;margin-bottom:0}
  .ovsec[open]>summary.k{margin-bottom:4px}
  /* a collapsible digest field (steps/PRs/files when there are many) — the summary is a
     dim clickable label with a caret; the list gets breathing room once expanded. */
  .briefsec>summary.k{cursor:pointer;list-style:none;margin-bottom:0}
  .briefsec>summary.k::-webkit-details-marker{display:none}
  .briefsec>summary.k:hover{color:var(--ink)}
  .briefsec[open]>summary.k{margin-bottom:5px}
  /* the full summary comes LAST and is rendered as light markdown; cap its height
     so a huge blob scrolls inside its own box rather than dominating the card. */
  /* overscroll-behavior:contain keeps the wheel INSIDE this box — reaching its top/
     bottom does not chain the scroll to the whole page. */
  .summary{font-size:14px;color:var(--ink);background:var(--panel2);border-left:3px solid var(--accent);
    border-radius:6px;padding:10px 12px;overflow-wrap:anywhere;max-height:16em;overflow-y:auto;
    overscroll-behavior:contain}
  .summary>*+*{margin-top:7px}
  .summary h1,.summary h2,.summary h3{font-weight:650;letter-spacing:-.01em;line-height:1.3}
  .summary h1{font-size:16px}.summary h2{font-size:15px}.summary h3{font-size:13.5px}
  .summary ul{margin:0;padding-left:20px}
  .summary li{margin:2px 0}
  .summary a{color:var(--accent);overflow-wrap:anywhere}
  .summary code{font-family:var(--mono);font-size:12px;background:var(--code);
    border:1px solid var(--line);border-radius:5px;padding:1px 5px}
  .summary hr{border:none;border-top:1px solid var(--line);margin:9px 0}
  /* one cohesive Open/Resume actions area (req 3, 8): the two commands sit side by
     side, each clearly labeled so the open (recap) and resume (jump-back) actions
     are obviously different. */
  .actions{display:grid;gap:11px;border:1px solid var(--accent);border-radius:8px;
    background:var(--panel2);padding:12px 13px}
  /* a compact action sizes to its content (short commands like `/todo <seq>`) instead of
     spanning the whole card width. */
  .actions.compact{display:inline-grid;width:auto;max-width:100%;justify-self:start}
  .actions.compact .cmd{min-width:0;width:auto}
  .action{display:grid;gap:5px}
  .action .lab{display:flex;flex-wrap:wrap;gap:6px 10px;align-items:baseline}
  .action .lab .name{font-family:var(--mono);font-size:11.5px;font-weight:650;color:var(--accent);
    letter-spacing:.04em;text-transform:uppercase}
  .action .lab .when{font-family:var(--mono);font-size:11px;color:var(--dim)}
  .action .sub{font-size:11.5px;color:var(--dim)}
  /* overscroll-behavior-x:contain keeps a horizontal wheel/trackpad scroll inside the
     command box (it scrolls via the inline overflow-x:auto) — no page chaining. */
  .cmd{display:block;font-family:var(--mono);font-size:12px;line-height:1.5;color:var(--ink);
    background:var(--code);border:1px solid var(--line);border-radius:6px;padding:8px 10px;
    overscroll-behavior-x:contain}
  /* a small "copy" button beside the Open/Resume commands (req D). The .cmd keeps its
     nowrap + overflow-x:auto so it scrolls; min-width:0 inside .cmdwrap lets it shrink
     so the flex button isn't pushed off. The behavior script wires the click. */
  .cmdwrap{display:flex;gap:8px;align-items:center}
  .cmdwrap .cmd{min-width:0}
  .copybtn{flex:none;font-family:var(--mono);font-size:10.5px;color:var(--dim);
    background:var(--panel2);border:1px solid var(--line);border-radius:6px;
    padding:3px 8px;cursor:pointer}
  .copybtn:hover{color:var(--ink);border-color:var(--accent)}
  .copybtn.copied{color:var(--accent);border-color:var(--accent)}
  details.workers{border:1px solid var(--line);border-radius:8px;background:var(--panel2);opacity:.86}
  details.workers>summary{cursor:pointer;list-style:none;font-family:var(--mono);font-size:10.5px;
    letter-spacing:.07em;text-transform:uppercase;color:var(--dim);padding:9px 11px}
  details.workers>summary::-webkit-details-marker{display:none}
  details.workers[open]>summary{color:var(--ink);border-bottom:1px solid var(--line)}
  .worker{padding:9px 11px;display:grid;gap:5px}
  .worker+.worker{border-top:1px solid var(--line)}
  .worker .wlabel{font-family:var(--mono);font-size:11px;color:var(--dim)}
  .worker .note{font-family:var(--mono);font-size:11px;color:var(--dim);font-style:italic}

  .brief{display:grid;gap:8px;font-size:13px}
  .brief .row{display:flex;gap:9px;flex-wrap:wrap;align-items:baseline}
  .brief .v{min-width:0;overflow-wrap:anywhere;color:var(--ink)}
  /* D1: the Overview cost row's stacked model-mix bar sits just above the derived $. */
  .brief .ccostbar{display:block;margin:0 0 3px}
  .brief .ccostbar .mixbar{width:100%;max-width:120px;height:6px}
  .brief a{color:var(--accent);overflow-wrap:anywhere}
  .brief .files{font-family:var(--mono);font-size:11.5px;color:var(--ink);display:grid;gap:2px}
  .brief .files .d{color:var(--dim)}
  /* each PR on ITS OWN LINE, the link then its description when present (req 5). */
  .brief .prs{display:grid;gap:3px;min-width:0}
  .brief .prs .pr{font-family:var(--mono);font-size:11.5px;overflow-wrap:anywhere}
  .brief .prs .pr .d{color:var(--dim)}
  /* the Stories block mirrors the PRs block exactly — each story on its own line,
     the linked url then its description when present. */
  .brief .stories{display:grid;gap:3px;min-width:0}
  .brief .stories .pr{font-family:var(--mono);font-size:11.5px;overflow-wrap:anywhere}
  .brief .stories .pr .d{color:var(--dim)}
  .brief ul.steps{margin:0;padding-left:2px;list-style:none;display:grid;gap:3px;min-width:0}
  .brief ul.steps li{font-family:var(--mono);font-size:12px;color:var(--ink);overflow-wrap:anywhere}
  .brief ul.steps li.done{color:var(--dim);text-decoration:line-through}
  .brief ul.decisions{margin:0;padding-left:18px;list-style:disc;min-width:0;display:grid;gap:2px}
  .brief ul.decisions li{color:var(--ink);overflow-wrap:anywhere}
  /* on-demand HISTORY trail (1.53.0): the --log dated milestones, COLLAPSED at the
     bottom of the expansion — secondary to the current snapshot, mirroring the
     terminal's `/todo <n> history`. data-key="hist:<seq>" persists it across the
     change-driven refresh via the generic details[data-key] handler. */
  /* WS6: history is now its OWN collapsible section (sec-history); its body reuses the
     .hbody/.hentry/.hts styling (formerly scoped to details.cathistory). */
  .hbody{display:grid;gap:3px}
  .hentry{font-family:var(--mono);font-size:11.5px;color:var(--ink);overflow-wrap:anywhere}
  .hts{color:var(--dim)}

  /* ---- WS4 usage / cost / work-mix panels -------------------------------- */
  /* The derived $ now lives in each task's Overview digest (the `cost` row); the full
     stacked mix bar lives only in the expanded Usage & Cost panel. */
  /* a stacked model-mix bar: coloured <span> segments summing to 100%. Reused by the
     collapsed-row chip and (with a legend beside it) the expanded Usage panel + each
     per-session row. Segment colours come from the per-variant --mx* vars (toggle-aware). */
  .mixbar{display:inline-flex;height:9px;border-radius:99px;overflow:visible;
    background:var(--line);vertical-align:middle;min-width:34px}
  .mseg{display:inline-block;height:100%;min-width:2px}
  /* end segments inherit the bar radius so the rounded ends survive overflow:visible
     (which we need so the B4 hover tooltip isn't clipped by the bar). */
  .mixbar>.mseg:first-child{border-radius:99px 0 0 99px}
  .mixbar>.mseg:last-child{border-radius:0 99px 99px 0}
  .mixbar>.mseg:only-child{border-radius:99px}
  .mixleg{display:inline-flex;flex-wrap:wrap;gap:9px;font-family:var(--mono);
    font-size:10.5px;color:var(--dim);vertical-align:middle}
  .mleg{display:inline-flex;align-items:center;gap:4px}
  .mdot{width:9px;height:9px;border-radius:2px;display:inline-block}
  .mseg.mx-fable,.mdot.mx-fable{background:var(--mxf)}
  .mseg.mx-opus,.mdot.mx-opus{background:var(--mxo)}
  .mseg.mx-sonnet,.mdot.mx-sonnet{background:var(--mxs)}
  .mseg.mx-haiku,.mdot.mx-haiku{background:var(--mxh)}
  .mseg.mx-unknown,.mdot.mx-unknown{background:var(--mxu)}
  /* the expanded Usage & Cost block. */
  .usage{display:grid;gap:8px}
  .usage>.k,.workmix>.k{font-family:var(--mono);font-size:11px;letter-spacing:.05em;
    text-transform:uppercase;color:var(--dim)}
  .usage .mixrow{display:flex;flex-wrap:wrap;align-items:center;gap:10px;max-width:640px}
  .usage .mixrow .mixbar{width:200px;max-width:60%}
  .usage .utot{font-family:var(--mono);font-size:12px;color:var(--ink)}
  table.usess{width:100%;border-collapse:collapse;font-size:11.5px;margin-top:2px}
  table.usess th{text-align:left;font-family:var(--mono);font-size:9.5px;
    text-transform:uppercase;letter-spacing:.08em;color:var(--dim);
    padding:3px 10px 4px 0;border-bottom:1px solid var(--line)}
  table.usess td{padding:5px 10px 5px 0;border-bottom:1px solid var(--line);
    vertical-align:middle;color:var(--ink)}
  table.usess tr:last-child td{border-bottom:none}
  table.usess .ssid{font-family:var(--mono);color:var(--dim);white-space:nowrap}
  table.usess .srole{font-family:var(--mono);white-space:nowrap}
  table.usess .smix .mixbar{width:72px}
  table.usess .stok{font-family:var(--mono);color:var(--dim);white-space:nowrap}
  table.usess .scost{font-family:var(--mono);white-space:nowrap}
  table.usess .scost .rep{color:var(--dim)}
  /* the work-mix (phase) stacked bar + its legend. */
  .workmix{display:grid;gap:7px}
  .pbar-mix{display:flex;height:12px;border-radius:6px;overflow:visible;
    background:var(--line);max-width:420px}
  .pseg{display:inline-block;height:100%;min-width:2px}
  .pbar-mix>.pseg:first-child{border-radius:6px 0 0 6px}
  .pbar-mix>.pseg:last-child{border-radius:0 6px 6px 0}
  .pbar-mix>.pseg:only-child{border-radius:6px}
  .plegend{display:flex;flex-wrap:wrap;gap:10px;font-family:var(--mono);
    font-size:11px;color:var(--dim)}
  .pleg{display:inline-flex;align-items:center;gap:5px}
  .pdot{width:9px;height:9px;border-radius:2px;display:inline-block}
  .pseg.pc0,.pdot.pc0{background:#7aa2f7}
  .pseg.pc1,.pdot.pc1{background:#9ece6a}
  .pseg.pc2,.pdot.pc2{background:#e0af68}
  .pseg.pc3,.pdot.pc3{background:#bb9af7}
  .pseg.pc4,.pdot.pc4{background:#f7768e}
  .pseg.pc5,.pdot.pc5{background:#73daca}
  /* B4: hover feedback on the usage & work-mix bar segments (CSS only, no JS). A
     hovered segment brightens + gets a thin outline; its siblings desaturate; AND a
     styled tooltip (data-tip = "label · share · tokens · $") floats above it — genuinely
     useful detail, not just a colour change. */
  .mseg,.pseg{position:relative}
  .mseg:hover,.pseg:hover{filter:brightness(1.25);outline:1px solid var(--line)}
  .mixbar:hover .mseg:not(:hover),
  .pbar-mix:hover .pseg:not(:hover){filter:saturate(.6)}
  .mseg[data-tip]:hover::after,.pseg[data-tip]:hover::after{
    content:attr(data-tip);position:absolute;left:50%;bottom:calc(100% + 6px);
    transform:translateX(-50%);white-space:nowrap;z-index:9;pointer-events:none;
    font-family:var(--mono);font-size:10.5px;line-height:1.3;color:var(--ink);
    background:var(--panel);border:1px solid var(--line);border-radius:6px;
    padding:4px 8px;box-shadow:0 3px 10px rgba(0,0,0,.35)}
  /* the collapsed derivation + prompts sub-details (secondary, dim summaries). */
  details.deriv,details.prompts{border-top:1px solid var(--line);padding-top:8px}
  details.deriv>summary,details.prompts>summary{cursor:pointer;list-style:none;
    font-family:var(--mono);font-size:11px;letter-spacing:.05em;color:var(--dim)}
  details.deriv>summary::-webkit-details-marker,
  details.prompts>summary::-webkit-details-marker{display:none}
  details.deriv>summary:hover,details.prompts>summary:hover{color:var(--ink)}
  .deriv .dbody{margin-top:7px;display:grid;gap:8px}
  .deriv .dnote{font-size:11.5px;color:var(--dim);line-height:1.5;max-width:82ch}
  table.drates{border-collapse:collapse;font-family:var(--mono);font-size:11px}
  table.drates th{text-align:right;color:var(--dim);font-weight:600;
    padding:2px 12px 3px 0;text-transform:uppercase;letter-spacing:.06em;font-size:9.5px}
  table.drates th:first-child,table.drates td:first-child{text-align:left}
  table.drates td{text-align:right;padding:2px 12px 2px 0;color:var(--ink)}
  .deriv .drnote{font-size:10.5px;color:var(--dim)}
  .prompts .pbody{margin-top:7px;display:grid;gap:9px}
  .pmt{display:grid;gap:2px}
  .pmt .pk{font-family:var(--mono);font-size:9.5px;font-weight:700;text-transform:uppercase;
    letter-spacing:.05em;border:1px solid var(--line);border-radius:99px;padding:1px 7px;
    color:var(--dim)}
  .pmt .pk.command{color:var(--accent);border-color:var(--accent)}
  .pmt .psid{font-family:var(--mono);font-size:10px;color:var(--dim);margin-left:6px}
  .pmt .pts{font-family:var(--mono);font-size:10px;color:var(--dim);margin-left:6px}
  .pmt .ptx{font-size:12px;color:var(--ink);overflow-wrap:anywhere;line-height:1.45}
  /* Claude's last-bullet reply paired under the prompt (`↳ …`): dimmer + indented so it
     reads as the response to the primary human prompt, not another prompt. */
  .pmt .preply{font-size:11.5px;color:var(--dim);overflow-wrap:anywhere;line-height:1.4;
    white-space:pre-line;    /* the reply is the full tail from Claude's last bullet — keep its line breaks */
    padding-left:10px;border-left:2px solid var(--line);margin-top:1px}
  /* WS6: hub prompts (self-authored) read as PRIMARY ink; worker prompts are dimmed +
     a touch smaller and grouped behind a collapsed "worker prompts (N)" toggle so the
     hub's own prompts stay front and centre. */
  .pmt.p-hub .ptx{color:var(--ink)}
  .pmt.p-worker{opacity:.72}
  .pmt.p-worker .ptx{font-size:11px;color:var(--dim)}
  details.wprompts{margin-top:2px}
  details.wprompts>summary{cursor:pointer;list-style:none;font-family:var(--mono);
    font-size:10.5px;letter-spacing:.05em;color:var(--dim)}
  details.wprompts>summary::-webkit-details-marker{display:none}
  details.wprompts>summary:hover{color:var(--ink)}
  details.wprompts .wpbody{margin-top:6px;display:grid;gap:9px}
  /* B8: a Claude/slash/hook-GENERATED prompt (command / compaction summary / a `/save`
     or `<command-…>` managed line) reads as secondary — dimmed, mono, a dashed border —
     so a genuine human-typed prompt stands out as the primary ink. */
  .pmt.gen{opacity:.66}
  .pmt.gen .ptx{font-family:var(--mono);font-size:11px;color:var(--dim);
    border-left:2px dashed var(--line);padding-left:8px}
  .pmt.gen .pk{border-style:dashed}
  .pmt.human .pk{color:var(--sa);border-color:var(--sa)}

  /* ---- board B9: cost/token colour scheme (mirrors the HUD costbar) ---------- */
  /* token counts are the HUD's muted blue; $ figures take the stdev μ/σ bands
     (green ≤μ / amber ≤μ+σ / orange >μ+σ). Fixed hexes on purpose — these are the
     shared costbar palette, identical in light + dark. */
  .tok{color:#5a87af}
  .cost-lo{color:#b4dc6e}
  .cost-mid{color:#f0be50}
  .cost-hi{color:#e67850}
  .mono{font-family:var(--mono)}

  /* ---- board B10–B14: per-hub cards in the Sessions section ------------------ */
  .hubcard{border:1px solid var(--line);border-radius:9px;background:var(--panel2);
    padding:10px 12px;display:grid;gap:9px}
  .hubcard+.hubcard{margin-top:9px}
  /* pinned hub → accent left border (as before); MAIN hub (the /todo resume target,
     floated first) → a distinct success-hued border + faint tint so the two highlights
     stay visually separable even when a hub is BOTH. */
  .hubcard.pinned{border-left:3px solid var(--accent)}
  .hubcard.main{border-left:3px solid var(--sa);
    box-shadow:inset 2px 0 0 var(--sa),0 0 0 1px var(--sa)}
  .hubcard.main.pinned{box-shadow:inset 2px 0 0 var(--accent),0 0 0 1px var(--sa)}
  .hubhead{display:grid;gap:4px}
  .hubhead .hcline{display:flex;flex-wrap:wrap;gap:4px 9px;align-items:baseline;
    font-family:var(--mono);font-size:11px;color:var(--dim)}
  .hubhead .hcord{color:var(--sa);font-weight:700}
  .hubhead .hcsid{color:var(--ink);font-weight:650}
  .hubhead .hcone{color:var(--ink);font-size:12.5px;overflow-wrap:anywhere}
  .hubhead .hclive{color:var(--sa)}
  /* 3-tier session-liveness badges (running / resumable / attached) — the three
     meanings of "live" made visually distinct instead of one green dot. */
  .sbadge{font-family:var(--mono);font-size:10px;font-weight:650;letter-spacing:.02em}
  .sb-run{color:var(--sa)}
  .sb-res{color:var(--so)}
  .sb-att{color:var(--dim)}
  .hbadge{font-family:var(--mono);font-size:9px;font-weight:700;letter-spacing:.06em;
    text-transform:uppercase;border-radius:99px;padding:1px 7px}
  .hbadge.b-main{color:var(--sa);border:1px solid var(--sa)}
  .hbadge.b-pin{color:var(--accent);border:1px solid var(--accent)}
  /* the per-hub expandable sub-sections (prompts, cost & work-mix, workers). */
  details.hubsec{border-top:1px solid var(--line);padding-top:7px}
  details.hubsec>summary{cursor:pointer;list-style:none;font-family:var(--mono);
    font-size:10.5px;letter-spacing:.05em;color:var(--dim)}
  details.hubsec>summary::-webkit-details-marker{display:none}
  details.hubsec>summary:hover{color:var(--ink)}
  .hubsec .hsbody{margin-top:8px;display:grid;gap:10px}
  /* the aggregate cost line + the per-worker/own drill (reuses .usess table style). */
  .hubcost .cagg{font-family:var(--mono);font-size:12px;display:flex;flex-wrap:wrap;
    gap:4px 10px;align-items:baseline}
  /* B5: the "other" work-mix drill-down — the top tool/command contributors. */
  .otherdrill{font-family:var(--mono);font-size:10.5px;color:var(--dim);
    display:flex;flex-wrap:wrap;gap:4px 10px;margin-top:2px}
  .otherdrill .odname{color:var(--ink)}
  /* a per-worker sub-card inside the nested workers list. */
  .wcard{display:grid;gap:6px;padding:7px 9px;border:1px solid var(--line);
    border-radius:7px;background:var(--panel)}
  .wcard+.wcard{margin-top:7px}
  .wcard .wchead{display:flex;flex-wrap:wrap;gap:4px 9px;align-items:baseline;
    font-family:var(--mono);font-size:11px;color:var(--dim)}
  .wcard .wclabel{color:var(--ink);font-weight:650}

  /* ---- WS6 collapsible expanded-row SECTIONS -------------------------------- */
  /* the expanded row's content is grouped into 5 collapsible <details class="sec sec-*">
     with coloured, mutually-distinct headers. `details.sec` RESETS the top-level
     `.sec` (flex section-heading) styles it would otherwise inherit — here it is a plain
     block group. Header colour = coloured text + a 2px left border (subtle, never a
     filled background); each section's hue comes from a per-variant --sec_* var. */
  details.sec{display:block;margin:0;padding:0;border:none}
  details.sec+details.sec{margin-top:6px}
  summary.sech{cursor:pointer;list-style:none;font-family:var(--mono);font-size:10.5px;
    letter-spacing:.08em;text-transform:uppercase;padding:5px 0 5px 9px;
    border-left:2px solid transparent}
  summary.sech::-webkit-details-marker{display:none}
  summary.sech:hover{filter:brightness(1.15)}
  summary.sech-overview{color:var(--sec_ov);border-left-color:var(--sec_ov)}
  summary.sech-cost{color:var(--sec_cost);border-left-color:var(--sec_cost)}
  summary.sech-prompts{color:var(--sec_pr);border-left-color:var(--sec_pr)}
  summary.sech-sessions{color:var(--sec_sess);border-left-color:var(--sec_sess)}
  summary.sech-history{color:var(--sec_hist);border-left-color:var(--sec_hist)}
  .secbody{padding:9px 0 6px 9px;display:grid;gap:13px}
  /* WS6: the pinned hub one-liner rows in the merged Sessions section. */
  .hubsess{display:grid;gap:3px;font-size:12.5px;padding:7px 9px;
    border:1px solid var(--line);border-radius:7px;background:var(--panel2)}
  .hubsess+.hubsess{margin-top:6px}
  .hubsess.pinned{border-left:3px solid var(--accent)}
  .hubsess .hsline{display:flex;flex-wrap:wrap;gap:4px 9px;align-items:baseline;
    font-family:var(--mono);font-size:11px;color:var(--dim)}
  .hubsess .hssid{color:var(--ink);font-weight:650}
  .hubsess .hsone{color:var(--ink);overflow-wrap:anywhere}
  .hubsess .hslive{color:var(--sa)}
  /* WS6: a longlist scroll box shared by every long-accumulation control (Summary,
     Decisions, History, full-prompts). Summary keeps its accent left border; the others
     get a dimmer 2px border so they read as siblings, not clones of Summary. */
  .longlist{max-height:16em;overflow-y:auto;overscroll-behavior:contain}
  ul.decisions.longlist,.hbody.longlist,.wpbody.longlist,.pbody.longlist{
    border-left:2px solid var(--line);border-radius:6px;padding:8px 10px 8px 22px;
    background:var(--panel2)}
  .hbody.longlist,.wpbody.longlist,.pbody.longlist{padding-left:12px}
  /* B3: normalise EVERY scrollable accumulation box (Summary + all .longlist boxes) —
     the same contained overscroll and one thin, theme-coloured scrollbar so they read
     as one system instead of each scrolling differently. (.summary already shares the
     16em cap.) */
  .summary,.longlist{overscroll-behavior:contain;scrollbar-width:thin;
    scrollbar-color:var(--line) transparent}
  .summary::-webkit-scrollbar,.longlist::-webkit-scrollbar{width:8px}
  .summary::-webkit-scrollbar-thumb,.longlist::-webkit-scrollbar-thumb{
    background:var(--line);border-radius:99px}
  .summary::-webkit-scrollbar-track,.longlist::-webkit-scrollbar-track{background:transparent}
  /* WS6: clickable file link (editor scheme) + its copy-path button. */
  .brief .files a.fopen{color:var(--accent);text-decoration:none}
  .brief .files a.fopen:hover{text-decoration:underline}
  .brief .files .fcopy{margin-left:6px}
  /* E2/L1: Live sessions as a collapsible <details> (collapsed by default; count in the
     summary) holding a FIXED stacked list — one row per session, every field labeled with a
     dim-mono caption. Nothing displaces siblings. */
  .livestrip{margin:.4rem 0 .9rem;border:1px solid var(--line);border-radius:10px;
    background:var(--panel);overflow:hidden}
  .lshead{font:600 10.5px var(--mono);letter-spacing:.08em;text-transform:uppercase;
    color:var(--dim);background:var(--panel2);padding:6px 12px;cursor:pointer;
    display:flex;align-items:center;gap:8px;list-style:none}
  .lshead::-webkit-details-marker{display:none}
  .lshead::before{content:"▸";color:var(--dim);font-size:.8em}
  .livestrip[open] .lshead::before{content:"▾"}
  .livestrip[open] .lshead{border-bottom:1px solid var(--line)}
  .liverow{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 12px;padding:7px 12px;
    border-bottom:1px solid var(--line);font-size:12px;min-width:0}
  .liverow:last-child{border-bottom:0}
  /* §3: ONE leading activity dot per live row — role hue, static-dim when idle, breathing
     when active. (The _state_badge('running') + separate activity field are gone.) */
  .liverow .ldot{flex:none;font-size:9px;line-height:1;align-self:center;opacity:.5}
  .liverow.r-hub .ldot{color:var(--accent)}
  .liverow.r-worker .ldot{color:var(--so)}
  .liverow.r-other .ldot{color:var(--dim)}
  /* an ACTIVE session dot is green — the SAME green as the `active` task status, so the
     Live-sessions strip and the status pills read as one system (role stays in the text). */
  .liverow .ldot.active{opacity:1;color:var(--sa)}
  .liverow .ltask{flex:none;font-family:var(--mono);font-weight:600}
  .liverow .ltask-none{flex:none;font-family:var(--mono);color:var(--dim)}
  /* §2: the task link is a subtle mono CHIP/badge, not a blue underlined hyperlink. */
  .livechip{font-family:var(--mono);color:var(--ink);background:var(--panel2);
    border:1px solid var(--line);border-radius:6px;padding:1px 6px;text-decoration:none;
    transition:border-color .12s}
  .livechip:hover{border-color:var(--accent);text-decoration:none}
  .lfields{display:flex;flex-wrap:wrap;gap:4px 12px;min-width:0;flex:1}
  .lf{display:inline-flex;align-items:baseline;gap:4px;min-width:0;font-family:var(--mono)}
  .lf .cap{font-size:9.5px;letter-spacing:.04em;text-transform:uppercase;color:var(--dim);flex:none}
  .lf .val{color:var(--ink);overflow-wrap:anywhere;min-width:0}
  .lf-wide{flex-basis:100%}
  .lresume{flex-basis:100%;min-width:0}
  /* the breathing "activity" pulse. Live-strip dots breathe when working. A LIVE task
     (a session running now) breathes the WHOLE row green — a soft green glow around the
     entire card (summary + any expanded detail), plus a green left stripe overriding the
     category colour so the live state is unmistakable. The glow pulses; the stripe is
     steady. OFF under prefers-reduced-motion or performance=low (the steady green stripe
     still conveys the state). */
  @keyframes mgbreathe{0%,100%{opacity:.4}50%{opacity:1}}
  @keyframes rowbreathe{0%,100%{box-shadow:0 0 0 1px transparent}
    50%{box-shadow:0 0 0 1px var(--sa),0 0 16px -4px var(--sa)}}
  .liverow .ldot.active{animation:mgbreathe 2.4s ease-in-out infinite}
  details.row.stat-live{border-left-color:var(--sa)!important;
    animation:rowbreathe 2.8s ease-in-out infinite}
  @media (prefers-reduced-motion: reduce){
    .liverow .ldot.active{animation:none}
    details.row.stat-live{animation:none}
  }
  html[data-perf="low"] .liverow .ldot.active,
  html[data-perf="low"] details.row.stat-live{animation:none}
  details.row.hl{background:var(--panel2)}
  details.row.hl>summary.rowsum{background:var(--panel2)}

  .help{margin-top:34px}
  .panels{display:grid;grid-template-columns:1fr 1fr;gap:18px;align-items:start}
  .panel{border:1px solid var(--line);border-radius:11px;background:var(--panel);
    padding:14px 16px;min-width:0}
  .panel h3{font-size:14px;font-weight:650;margin-bottom:11px}
  table.kv{width:100%;border-collapse:collapse;font-size:12.5px}
  table.kv td{padding:4px 0;vertical-align:top;border-top:1px solid var(--line)}
  table.kv tr:first-child td{border-top:none}
  table.kv td.key{font-family:var(--mono);color:var(--dim);white-space:nowrap;padding-right:14px}
  table.kv td.val{color:var(--ink);overflow-wrap:anywhere}
  table.kv td.val.mono{font-family:var(--mono);font-size:12px}
  /* config panel col3 (kept for back-compat) + the Commands panel footer line. */
  .kv .cdefault{color:var(--dim)}
  /* a single compact, dim footer line under a help panel (how-to-set / bare-state). */
  .helpnote{color:var(--dim);font-size:11px;margin-top:8px}
  /* Configs panel (1.26.0): EXPANDABLE rows. A non-expanding header labels two
     columns (flag · options); each setting is a <details class="crow"> whose summary
     shows the flag + ONE options cell (every choice listed, the CURRENT value bold),
     with the description / default / per-flag usage moved into the
     expanded .cdetail. The header + each summary share the SAME .cflag column width so
     the options column lines up across rows (longest flag ~ "--guaranteed-tracking"). */
  .cfg-head{display:flex;gap:12px;font-size:11px;font-weight:600;color:var(--dim);
    padding-bottom:6px;border-bottom:1px solid var(--line);margin-bottom:4px}
  .cfg-head>span:first-child,.crowsum .cflag{flex:none;min-width:185px}
  details.crow{border-bottom:1px solid var(--line)}
  summary.crowsum{display:flex;gap:12px;align-items:baseline;cursor:pointer;
    padding:5px 0;list-style:none;font-size:12px;flex-wrap:wrap}
  summary.crowsum::-webkit-details-marker{display:none}
  .crowsum .cflag{font-family:var(--mono);color:var(--ink)}
  /* E1: the options cell must wrap/break inside the panel — long choice strings used to
     overflow the box. min-width:0 lets the flex item shrink; overflow-wrap breaks it. */
  .crowsum .copts{font-family:var(--mono);color:var(--ink);flex:1;min-width:0;
    overflow-wrap:anywhere}
  .crowsum .copts strong{color:var(--accent);text-decoration:underline}
  .cdetail{padding:2px 0 8px 0;color:var(--dim);font-size:11.5px;display:grid;gap:3px}
  /* the config expansion's code (the usage block + the Default value code) stands out
     from the dim description lines in the accent colour. */
  .cdetail code{color:var(--accent)}
  .cdim{color:var(--dim)}

  /* top-of-board search + filters (req 8) */
  .filters{display:flex;flex-wrap:wrap;gap:10px;margin:22px 0 4px}
  .filters .fsearch{flex:1;min-width:180px;font-family:var(--sans);font-size:13px;
    color:var(--ink);background:var(--panel);border:1px solid var(--line);
    border-radius:8px;padding:9px 12px}
  .filters .fsel{font-family:var(--mono);font-size:12px;color:var(--ink);
    background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:9px 10px;cursor:pointer}
  .filters .fsearch:focus,.filters .fsel:focus{outline:none;border-color:var(--accent)}
  /* reset filters button (req F) */
  .filters .freset{flex:none;font-family:var(--mono);font-size:12px;color:var(--dim);
    background:var(--panel2);border:1px solid var(--line);border-radius:8px;
    padding:9px 14px;cursor:pointer;white-space:nowrap}
  .filters .freset:hover{color:var(--ink);border-color:var(--accent)}
  /* closed "see more" expander (req 7) */
  details.seemore>summary{cursor:pointer;list-style:none;font-family:var(--mono);
    font-size:11px;letter-spacing:.05em;color:var(--dim);padding:9px 14px;background:var(--panel2)}
  details.seemore>summary::-webkit-details-marker{display:none}
  details.seemore>summary:hover{color:var(--ink)}
  /* bottom Categories panel: a vertical LIST; each row is an EXPANDABLE <details>
     whose SUMMARY is an INLINE FLOW reading left-to-right — [colored pill = the TAG
     only] · [CUSTOM marker, only if overridden] · description (the label, plain
     uncoloured text) · per-state counts (sitting right after the description with a
     small gap, NOT floated to the page edge — req H). The pill is sized to its OWN
     content (NOT stretched to a uniform width); the description is NEVER truncated;
     there is no swatch, and non-overridden rows simply omit the marker. */
  .cats{margin-top:30px}
  .catlist{display:flex;flex-direction:column;gap:8px;font-size:13px}
  .catitem{display:flex;align-items:center;gap:8px}
  /* the colored pill wraps ONLY the [TAG]; content-sized (inline-flex, no stretch),
     filled with the highlight via --cat-stripe + readable text via --cat-fg. */
  .catitem .cchip{display:inline-flex;align-items:center;border-radius:99px;padding:3px 11px}
  .catitem .ctag{font-family:var(--mono);font-size:11.5px;font-weight:700;letter-spacing:.02em}
  /* the description (label) sits to the RIGHT of the pill (and right of CUSTOM) as
     plain, UNCOLOURED text — never truncated. */
  .catitem .clabel{min-width:0;color:var(--ink);opacity:.94}
  /* the pill + CUSTOM + description group occupies a FIXED width (set inline per render
     = widest tag+label + 4ch) so every row's counts begin at the SAME x — aligned just
     past the longest description, NOT floated to the page edge (req 2). */
  .catitem .catleft{flex:none;display:flex;gap:8px;align-items:center;white-space:nowrap}
  /* the per-state counts follow the fixed-width .catleft (no margin-left float — req 2),
     nowrap; each count word tinted by its STATUS colour (new=--so, active=--sa,
     closed=--sc); the " · " separators inherit the dim ccount colour. */
  .catitem .ccount{font-family:var(--mono);font-size:12px;font-weight:650;
    white-space:nowrap;color:var(--dim)}
  .catitem .ccount .cn{color:var(--so)}
  .catitem .ccount .cp{color:var(--sp)}
  .catitem .ccount .ca{color:var(--sa)}
  .catitem .ccount .cc{color:var(--sc)}
  /* the CUSTOM marker — a small outlined pill, content-sized + inline; ONLY rendered
     for an overridden category, directly right of the pill and BEFORE the description. */
  .catitem .cmark{font-family:var(--mono);font-size:9.5px;font-weight:700;
    letter-spacing:.06em;text-transform:uppercase;color:var(--dim);border:1px solid var(--dim);
    border-radius:99px;padding:1px 7px}
  /* each category row is now an EXPANDABLE <details> (req I): the summary keeps the
     inline catitem flow (pill · CUSTOM · description · counts), the expanded
     .catdetail shows the "when to use" guidance + the auto-assignment note. The
     existing .catitem inner-span rules apply because the summary also carries the
     .catitem class. These rows persist across the refresh via the generic
     details[data-key] handler (data-key="cat:<color>"). */
  details.catrow{border-bottom:1px solid var(--line)}
  summary.catsum{display:flex;align-items:center;gap:8px;cursor:pointer;list-style:none;padding:6px 0}
  summary.catsum::-webkit-details-marker{display:none}
  .catdetail{padding:2px 0 8px;color:var(--dim);font-size:11.5px;display:grid;gap:3px}

  .snapshot{margin-top:30px;padding-top:14px;border-top:1px solid var(--line);
    font-family:var(--mono);font-size:11.5px;color:var(--dim)}
  .snapshot code{background:var(--panel2);border:1px solid var(--line);border-radius:5px;padding:1px 6px}
  .snapshot .repo{margin-top:4px}
  .snapshot a{color:var(--accent)}
  .empty{color:var(--dim);font-style:italic;padding:22px 0}

  /* WS-D task-relations mini-graph — a small, collapsible SVG panel. No external
     assets: one inline <svg>, colours from the theme vars + per-node category hex. */
  .minigraph{margin:14px 0 4px;border:1px solid var(--line);border-radius:10px;
    background:var(--panel);overflow:hidden}
  .minigraph>summary{cursor:pointer;list-style:none;padding:10px 14px;
    font-weight:600;color:var(--fg);display:flex;align-items:center;gap:8px}
  .minigraph>summary::-webkit-details-marker{display:none}
  .minigraph>summary::before{content:"▸";color:var(--dim);font-size:.8em}
  .minigraph[open]>summary::before{content:"▾"}
  .minigraph .mgcount{color:var(--dim);font-weight:400;font-size:.85em}
  .mgwrap{padding:6px 14px 14px}
  .mgsvg{width:100%;height:auto;display:block;max-width:640px;margin:0 auto}
  .mg-edge{stroke:var(--dim);stroke-width:1.4;fill:none;opacity:.55}
  .mg-edge.k-related{stroke:var(--accent);opacity:.5}
  .mg-edge.k-touch{stroke:var(--accent);stroke-dasharray:4 3;opacity:.7}
  .mg-edge.k-knowledge{stroke:var(--so,#6aa);stroke-dasharray:1 4;stroke-width:1.8;opacity:.85}
  .mg-node circle{stroke:var(--bg);stroke-width:1.5}
  .mg-node.closed circle{opacity:.4}
  .mg-node text{font:600 10px ui-monospace,SFMono-Regular,Menlo,monospace;
    fill:var(--fg);text-anchor:middle;dominant-baseline:central;pointer-events:none}
  .mg-node.closed text{fill:var(--dim)}
  .mg-node:hover circle{stroke:var(--accent);stroke-width:2.5}
  .mglegend{display:flex;flex-wrap:wrap;gap:14px;justify-content:center;
    margin-top:8px;color:var(--dim);font-size:.8em}
  .mglegend span{display:inline-flex;align-items:center;gap:5px}
  .mglegend i{width:18px;height:0;border-top-width:2px;border-top-style:solid;display:inline-block}
  .mgi-lineage{border-top-color:var(--accent);border-top-style:solid}
  .mgi-touch{border-top-color:var(--accent);border-top-style:dashed}
  .mgi-knowledge{border-top-color:var(--so,#6aa);border-top-style:dotted}
  /* clustered graph: category + signal hubs and their spoke edges. Per-kind signal
     colours are theme vars with an inline hex fallback (no external asset). */
  .mg-edge.k-lineage{stroke:var(--accent);opacity:.65}
  .mg-edge.k-membership{stroke:var(--dim);stroke-dasharray:2 4;opacity:.4}
  .mg-edge.k-pr{stroke:var(--mg-pr,#c79bef);stroke-dasharray:5 3;opacity:.6}
  .mg-edge.k-repo{stroke:var(--mg-repo,#7aa6ec);stroke-dasharray:5 3;opacity:.6}
  .mg-edge.k-story{stroke:var(--mg-story,#e6c178);stroke-dasharray:5 3;opacity:.6}
  .mg-hub rect{stroke:var(--bg);stroke-width:1.5}
  .mg-hub text{font:600 10px ui-monospace,SFMono-Regular,Menlo,monospace;
    text-anchor:middle;dominant-baseline:central;pointer-events:none}
  .mg-hub:hover rect{stroke:var(--accent);stroke-width:2.5}
  .mg-sig{stroke:var(--bg);stroke-width:1.4}
  .mg-sig-pr{fill:var(--mg-pr,#c79bef)}
  .mg-sig-repo{fill:var(--mg-repo,#7aa6ec)}
  .mg-sig-story{fill:var(--mg-story,#e6c178)}
  .mg-signode text{font:600 9px ui-monospace,SFMono-Regular,Menlo,monospace;
    fill:var(--dim);text-anchor:middle;dominant-baseline:central;pointer-events:none}
  .mg-signode:hover .mg-sig{stroke:var(--accent);stroke-width:2.5}
  .mglegend i.mgi-cat{width:14px;height:10px;border:0;border-radius:3px;
    background:var(--accent)}
  .mglegend i.mgi-sig{width:11px;height:11px;border:0;background:var(--dim);
    transform:rotate(45deg)}
  /* step 2: the live canvas graph. Hidden until the enhancement script adds .mg-live to
     the panel (no-JS keeps the static SVG + legend). No external assets. */
  .mgcontrols,.mgstage{display:none}
  .minigraph.mg-live .mgsvg,.minigraph.mg-live .mglegend{display:none}
  .minigraph.mg-live .mgcontrols{display:flex;flex-wrap:wrap;gap:8px;align-items:center;
    padding:2px 0 10px}
  /* align-items:stretch so the canvas column grows to the filter rail's height (the
     canvas is as tall as the filters beside it, not a fixed short box). */
  .minigraph.mg-live .mgstage{display:grid;grid-template-columns:1fr 250px;gap:14px;
    align-items:stretch}
  @media (max-width:820px){.minigraph.mg-live .mgstage{grid-template-columns:1fr}}
  .mgseg{display:inline-flex;border:1px solid var(--line);border-radius:9px;overflow:hidden}
  .mgseg button{font:600 12px var(--sans);background:var(--panel);color:var(--dim);border:0;
    padding:5px 13px;cursor:pointer}
  .mgseg button[aria-pressed="true"]{background:var(--accent);color:var(--page)}
  .mgbtn{font:500 12px var(--sans);color:var(--ink);background:var(--panel);
    border:1px solid var(--line);border-radius:8px;padding:5px 10px;cursor:pointer}
  .mgbtn:hover{border-color:var(--accent)}
  .mgbtn[aria-pressed="true"]{border-color:var(--accent);color:var(--accent)}
  /* D3: the per-task "View in graph" button — a SMALL clean chip (hidden until the
     enhancement enables it), sized to its text, not a full-width boxed action. */
  .mgviewwrap{margin:0 0 2px}
  .mgviewbtn{font:600 11px var(--mono);letter-spacing:.02em;color:var(--accent);
    background:none;border:1px solid var(--line);border-radius:7px;padding:3px 9px;
    cursor:pointer;line-height:1.3}
  .mgviewbtn:hover{border-color:var(--accent);background:var(--panel2)}
  .mgviewwrap[hidden]{display:none}
  .mgsearch{flex:1;min-width:150px;display:flex;align-items:center;gap:7px;
    background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:5px 10px}
  .mgsearch:focus-within{border-color:var(--accent)}
  .mgsearch input{flex:1;border:0;background:none;color:var(--ink);font:400 13px var(--sans);
    outline:none;min-width:0}
  .mgsearch .mag{color:var(--dim)}
  .mgsearch .mgn{font:500 11px var(--mono);color:var(--dim)}
  .mgcanvaswrap{position:relative;border:1px solid var(--line);border-radius:12px;
    overflow:hidden;background:var(--panel);min-height:520px}
  /* fill the (stretched) wrap so the canvas matches the filter rail's height; the min
     keeps a usable size when the rail is short. resize() reads the rendered height. */
  .mgcanvas{display:block;width:100%;height:100%;min-height:520px;cursor:grab;touch-action:none}
  .mgcanvas.grabbing{cursor:grabbing}
  .mghint{position:absolute;left:10px;top:9px;font:500 11px var(--mono);color:var(--dim);
    background:var(--panel2);padding:3px 7px;border-radius:6px;pointer-events:none;opacity:.92}
  .mgrail{border:1px solid var(--line);border-radius:12px;background:var(--panel);padding:12px}
  /* C2: grouped multi-select filter panel (replaces the flat legend). */
  .mgfilters{display:flex;flex-direction:column;gap:9px}
  .mgfgroup{display:flex;flex-direction:column;gap:2px}
  /* the group header is a row: the title on the left + a small "hide/show all" toggle on
     the right that flips every filter in the group at once. */
  .mgfgroup>.h{display:flex;align-items:center;justify-content:space-between;gap:8px;
    font:600 10px var(--mono);letter-spacing:.06em;text-transform:uppercase;
    color:var(--dim);margin-bottom:2px}
  .mgfgroup>.h .ht{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .mgallbtn{flex:none;font:600 9px var(--mono);letter-spacing:.03em;text-transform:uppercase;
    color:var(--dim);background:none;border:1px solid var(--line);border-radius:5px;
    padding:1px 6px;cursor:pointer}
  .mgallbtn:hover{color:var(--ink);border-color:var(--accent)}
  .mgf{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--ink);
    padding:3px 5px;border-radius:6px;cursor:pointer;border:0;background:none;width:100%;
    text-align:left}
  .mgf:hover{background:var(--panel2)}
  .mgf:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
  /* G2: the swatch is an inline-SVG node-shape glyph (task ● / cat-hub rect / PR ◆ /
     repo ⬡ / story ▢ / edge dashed line), coloured by kind. */
  .mgf .gly{flex:none;width:18px;height:14px;display:inline-flex;align-items:center;
    justify-content:center}
  .mgf .gly svg{display:block}
  .mgf.off{opacity:.42}.mgf.off .nm{text-decoration:line-through}
  .mgf .nm{flex:1;overflow-wrap:anywhere}.mgf .ct{font:500 11px var(--mono);color:var(--dim)}
  .mgf .box{flex:none;width:12px;height:12px;border:1.5px solid var(--dim);border-radius:3px;
    display:inline-block;position:relative}
  .mgf:not(.off) .box{background:var(--accent);border-color:var(--accent)}
  /* FIXED height + internal scroll: renderInfo fires on every hover, and a growing
     info panel used to grow the rail → the height-matched canvas resized under the
     cursor (jumpy hover). The panel now never changes the rail's layout. */
  .mginfo{border-top:1px solid var(--line);margin-top:10px;padding-top:11px;height:185px;
    overflow-y:auto;font-size:12.5px}
  .mginfo .empty{color:var(--dim);font-style:italic;font-size:12px}
  .mginfo .title{display:flex;align-items:center;gap:7px;font:600 13.5px var(--sans)}
  .mginfo .chip{flex:none;width:11px;height:11px;border-radius:50%}
  .mginfo .cat{font:600 10px var(--mono);letter-spacing:.05em;text-transform:uppercase;
    color:var(--dim);margin:3px 0 9px}
  .mginfo .rel{margin:0 0 8px}
  .mginfo .rel .k{font:600 10px var(--mono);letter-spacing:.04em;text-transform:uppercase;
    color:var(--dim);display:block;margin-bottom:2px}
  .mginfo a{color:var(--so);text-decoration:none;font-family:var(--mono);font-size:12px}
  .mginfo a:hover{text-decoration:underline}
  .mginfo .open{margin-left:auto;font:600 10px var(--mono);color:var(--so)}
  .mgprobe{position:absolute;left:-9999px;top:0;width:0;height:0;overflow:hidden}

  /* F1/F2 — Interbrain: owner chip, handle chip, foreign rows, focus strip.
     All selectors are inert when Interbrain is off (the markup that uses them isn't
     emitted), so the off render stays behaviorally identical to classic. */
  .mg-edge.k-xbrain{stroke:var(--dim);stroke-dasharray:6 4;opacity:.7}
  .mg-node.foreign circle{stroke-dasharray:2 2}
  .hchip{display:inline-block;margin-left:6px;padding:0 5px;border:1px solid var(--line);
    border-radius:5px;font:600 10px var(--mono);color:var(--dim);vertical-align:middle}
  .ochip{display:inline-flex;align-items:center;gap:4px;margin-right:6px;padding:1px 7px;
    border:1px solid var(--oc,var(--line));border-radius:9px;font:600 11px var(--mono);
    color:var(--ink);vertical-align:middle}
  .ochip .odot{width:8px;height:8px;border-radius:50%;background:var(--oc,var(--dim));flex:none}
  .ochip.inline{margin:0}
  .bchip{display:inline-block;margin-left:6px;padding:0 6px;border:1px solid var(--line);
    border-radius:9px;font:600 10px var(--mono);color:var(--dim);background:var(--panel);
    vertical-align:middle;opacity:.9}
  .fxcount{margin-left:6px;font:600 10px var(--mono);color:var(--dim);opacity:.8}
  .lchip{display:inline-block;margin-right:6px;padding:0 7px;border:1px dashed var(--line);
    border-radius:9px;font:600 10px var(--mono);color:var(--dim);vertical-align:middle}
  .rolock{margin-right:5px;font-size:12px;opacity:.85}
  details.row.foreign{opacity:.94}
  .foreign-notice{margin:6px 0;font-size:12px;color:var(--dim)}
  .fdigest{margin:6px 0;font-size:12.5px;line-height:1.5}
  .fdigest .k{font:600 10px var(--mono);letter-spacing:.04em;text-transform:uppercase;
    color:var(--dim);margin-right:6px}
  .fsignals{margin-top:6px;display:flex;flex-wrap:wrap;gap:6px}
  .fsig{font:600 10px var(--mono);padding:1px 6px;border-radius:5px;
    border:1px solid var(--line);color:var(--dim)}
  .foreign-act .action.disabled{opacity:.5}
  .fcmd{margin:4px 0 0;padding:5px 8px;background:var(--panel);border:1px solid var(--line);
    border-radius:6px;font:600 11px var(--mono);color:var(--ink);overflow-x:auto;white-space:pre}
  .focusstrip{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:10px 0 2px}
  .focusstrip .fxlabel{font:600 10px var(--mono);letter-spacing:.06em;
    text-transform:uppercase;color:var(--dim)}
  .fxchip{font:600 12px var(--mono);color:var(--ink);background:var(--panel);
    border:1px solid var(--line);border-radius:14px;padding:3px 11px;cursor:pointer}
  .fxchip:hover{border-color:var(--accent)}
  .fxchip.active,.fxmenu.active>.fxchip{background:var(--accent);color:var(--page);
    border-color:var(--accent)}
  .fxmenu{position:relative;display:inline-block}
  .fxmenu>summary{list-style:none;cursor:pointer}
  .fxmenu>summary::-webkit-details-marker{display:none}
  .fxlist{position:absolute;z-index:20;top:calc(100% + 4px);left:0;min-width:150px;
    background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:5px;
    display:flex;flex-direction:column;gap:2px;box-shadow:0 6px 20px rgba(0,0,0,.28)}
  .fxopt{text-align:left;font-family:var(--mono);font-size:12px;color:var(--ink);
    background:transparent;border:0;border-radius:6px;padding:5px 9px;cursor:pointer}
  .fxopt:hover,.fxopt.active{background:var(--accent);color:var(--page)}

  @media (max-width:720px){
    :root{--cols:__NARROW__}
    .c-cat,.c-eff,.c-act,
    .head .c-cat,.head .c-eff,.head .c-act{display:none}
    .panels{grid-template-columns:1fr}
  }
""".replace("__NARROW__", _COLS_NARROW)
    return root + themes + (category_css + "\n" if category_css else "") + body


def _hex_to_rgb(color):
    """`#rgb` / `#rrggbb` → (r,g,b) ints 0-255, or None when unparseable."""
    h = str(color or "").strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return None
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def _rgb_to_hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(c)))) for c in rgb)


def brighten(color):
    """Push a `#hex` colour into a VIVID mid-lightness / high-saturation version of
    the SAME hue and return `#rrggbb` (req 1). The raw category background hexes are
    too dark to tell apart at a glance — raising lightness into a mid range and
    boosting saturation makes each category's HUE read distinctly in BOTH the dark
    and light variants. The hue is preserved (derived from the category's own colour,
    not one generic accent). Returns the input unchanged when it isn't parseable hex
    (pure stdlib: colorsys uses H,L,S order)."""
    rgb = _hex_to_rgb(color)
    if rgb is None:
        return color
    r, g, b = (c / 255.0 for c in rgb)
    hue, light, sat = colorsys.rgb_to_hls(r, g, b)
    light = min(0.70, max(light, 0.60))     # raise into a vivid mid range
    sat = min(1.0, max(sat, 0.68))          # boost saturation so the hue reads
    r, g, b = colorsys.hls_to_rgb(hue, light, sat)
    return _rgb_to_hex((r * 255, g * 255, b * 255))


# Curated per-category HIGHLIGHT palette, one entry per VARIANT (req B). This
# REPLACES the uniform brighten() for the category colour: each category gets a
# hand-tuned highlight that is clearly DISTINCT from its neighbours AND TRUE TO ITS
# NAME. Used by the row's left stripe, the categories-list row background, and the
# category tag (so the three always agree). Notes on the deliberate choices:
#   • blue vs silver — blue is a saturated sky-blue, silver a desaturated grey, so
#     they no longer look alike.
#   • white = DESIGN — white in dark mode; the nearest VISIBLE light shade in light
#     mode (pure white would vanish on the light page), keeping the white intent.
#   • black = GENERAL — black in light mode; a visible near-black/dark-grey in dark
#     mode (pure black would vanish on the dark page).
#   • brown = DATA — a clearly BROWN highlight (not washed-out / purple).
#   • gold = DOCS — a clearly GOLD/amber highlight, kept apart from yellow.
# red/orange/yellow/green/purple/pink stay vivid and mutually distinct.
_CAT_HIGHLIGHT = {
    "dark": {
        "red":    "#ff5d5d", "orange": "#ff9b3d", "yellow": "#ffe14d",
        "green":  "#6fe05a", "blue":   "#3fa9ff", "purple": "#b072ff",
        "black":  "#3a3d44", "pink":   "#ff6ec7", "white":  "#ffffff",
        "silver": "#aeb7c4", "gold":   "#e0a92e", "brown":  "#9a6233",
    },
    "light": {
        "red":    "#d23440", "orange": "#dd7414", "yellow": "#c2a200",
        "green":  "#3f9e2f", "blue":   "#1f7fd6", "purple": "#8a3fd0",
        "black":  "#000000", "pink":   "#cf3a96", "white":  "#cfd3da",
        "silver": "#7f8a9c", "gold":   "#b8860b", "brown":  "#7a4a22",
    },
}


def category_highlight(color, variant):
    """The curated highlight hex for category `color` in `variant` (req B), or None
    when the colour isn't a known category (caller falls back to brighten())."""
    table = _CAT_HIGHLIGHT.get(variant if variant in _CAT_HIGHLIGHT else "dark", {})
    return table.get(str(color or ""))


def _readable_fg(hexcolor):
    """Black-ish or near-white text, whichever reads better on `hexcolor` — used to
    fill the categories-list rows + the tag with the category highlight (req D) and
    keep their text legible. Defaults to light ink when `hexcolor` isn't parseable."""
    rgb = _hex_to_rgb(hexcolor)
    if rgb is None:
        return "#f5f3ee"
    lum = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]   # perceived luminance
    return "#15140f" if lum > 150 else "#f5f3ee"


def _highlight_fb(color, theme, variant):
    """The inline (no-JS) highlight hex for `color` in `variant`: the curated value
    (req B), else brighten() of the theme bg, else the page accent var."""
    hi = category_highlight(color, variant)
    if hi:
        return hi
    bg = _cat_bg(_palette_for(color, theme, variant))
    return brighten(bg) if bg else "var(--accent)"


def _e(s):
    return html.escape(str(s if s is not None else ""), quote=True)


def _local_ts(text, ts):
    """B-TS: wrap a server-formatted timestamp so the behavior script rewrites it to the
    VIEWER's LOCAL wall-clock time via `new Date(ts*1000).toLocaleString()`. `text` is the
    no-JS fallback (already escaped here); `ts` is epoch SECONDS (absolute, from the UTC
    `_now()` / file mtime). When `ts` is None (e.g. a caller that only has a preformatted
    string), returns the plain escaped text — no wrapper, unchanged behaviour."""
    if ts is None:
        return _e(text)
    return ('<span class="lts" data-ts="%d">%s</span>' % (int(ts), _e(text)))


def _live_strip(live):
    """The ACTUALLY-running Claude sessions (rows from live_sessions.running(), passed
    through by task-station.py) as a compact FIXED vertical LIST — one row per session.

    A stacked list, one row per session: a leading "activity" dot (role-hued; breathes when
    the session is busy/active, static-dim when idle) + a mono task chip, then the fields
    (pid · session · role · label · cwd · resume) inline with dim-mono captions — nothing
    expands, so nothing displaces. A task-linked row keeps its `<a class="livechip"
    href="#task-<seq>">` jump (the behavior script opens that row). Self-contained (board CSS
    classes, theme-aware); returns [] when nothing runs."""
    rows = [r for r in (live or []) if r]
    if not rows:
        return []

    def _field(cap, val, extra=""):
        return ('<span class="lf%s"><span class="cap">%s</span>'
                '<span class="val">%s</span></span>' % (extra, cap, val))

    items = []
    for r in rows:
        seq = r.get("task_seq")
        pid = _e(r.get("pid"))
        role = (r.get("role") or "").strip() or "—"
        # B19: colour each row's dot by role — hub / worker / other get distinct hues.
        _rr = role.lower()
        rcls = "r-hub" if "hub" in _rr else ("r-worker" if "worker" in _rr else "r-other")
        # §3: ONE leading "activity" dot — BREATHES when the session is actively working
        # (status busy/running/active), STATIC-dim when idle. Vocabulary is working/idle
        # (the running sub-state; running/resumable/linked stay reserved for the
        # session-card STATE badges, and "live"/"active" for the task pill/lifecycle).
        active = (r.get("status") or "").strip().lower() in ("busy", "running", "active")
        dotcls = "ldot active" if active else "ldot"
        dottip = ("working — busy right now" if active else "idle — running, waiting")
        sid = str(r.get("session_id") or "")
        if seq is not None:
            taskcell = ('<a class="livechip %s" href="#task-%s">task #%s</a>'
                        % (rcls, _e(seq), _e(seq)))
        else:
            taskcell = '<span class="ltask-none">no task</span>'
        fields = [_field("pid", pid)]
        if sid:
            fields.append(_field("session", _e(sid[:8])))
        fields.append(_field("role", _e(role)))
        if r.get("label"):
            fields.append(_field("label", _e(r.get("label"))))
        if r.get("cwd"):
            fields.append(_field("cwd", _e(r.get("cwd")), " lf-wide"))
        rc = r.get("resume_command")
        resume = ""
        if rc:
            resume = ('<div class="cmdwrap lresume"><code class="cmd" style="%s">%s</code>'
                      '<button class="copybtn" type="button" aria-label="Copy command">'
                      'copy</button></div>' % (_CMD_STYLE, _e(rc)))
        items.append(
            '<div class="liverow %s"><span class="%s" title="%s">●</span>'
            '<span class="ltask">%s</span><div class="lfields">%s</div>%s</div>'
            % (rcls, dotcls, _e(dottip), taskcell, "".join(fields), resume))
    # L1: collapsible, collapsed by default, with the count in the summary. data-key="live"
    # lets it persist across the change-driven refresh (collapsed on a fresh load).
    return ['<details class="livestrip" data-key="live"><summary class="lshead">'
            'Live sessions (%d)</summary><div class="livebody">%s</div></details>'
            % (len(rows), "".join(items))]


def _rich(text):
    """Light-markdown (mdlite) render of a SHORT digest string (goal/state/a single
    decision) — html-escaped first, so it stays self-contained. A single-paragraph
    result is unwrapped to inline content (these live inside a span / li); falls
    back to escaped plain text when mdlite is unavailable or yields nothing."""
    text = (text or "").strip()
    if not text:
        return ""
    if _md is not None:
        try:
            rendered = _md.render(text)
        except Exception:
            rendered = ""
        if rendered:
            if (rendered.startswith("<p>") and rendered.endswith("</p>")
                    and rendered.count("<p>") == 1):
                return rendered[3:-4]
            return rendered
    return _e(text)


def _progress_chip(t):
    """Compact mini-bar + `N/M` for the Task cell — only when the task has steps."""
    prog = list(t.get("progress") or [])
    done = prog[0] if len(prog) > 0 else 0
    total = prog[1] if len(prog) > 1 else 0
    if not total:
        return ""
    pct = int(round(100 * done / total))
    # B17: colour by completion — done=green, ≥50%=amber, else orange (costbar bands).
    band = "p-done" if pct >= 100 else ("p-mid" if pct >= 50 else "p-lo")
    return ('<span class="prog %s" title="%d of %d steps done">'
            '<span class="pbar"><span style="width:%d%%"></span></span>%d/%d</span>'
            % (band, done, total, pct, done, total))


def _palette_for(color, theme, variant):
    if not color or _cats is None or not hasattr(_cats, "theme_palette"):
        return None
    try:
        pal = _cats.theme_palette(theme, color, variant)
    except Exception:
        pal = None
    return pal if isinstance(pal, dict) else None


def _cat_class(color):
    """A safe CSS class name for a category colour (alnum/dash only)."""
    return "cat-" + "".join(c if (c.isalnum() or c == "-") else "-" for c in str(color or ""))


def _cat_bg(pal):
    return pal.get("bg") if isinstance(pal, dict) else None


def _cat_accent(pal, fallback):
    """The category's accent hex (its terminal `bold` colour in this variant)."""
    if isinstance(pal, dict):
        return pal.get("bold") or pal.get("fg") or fallback
    return fallback


def _category_css(tasks, theme):
    """Per-category colour variables for BOTH variants, so the row's left stripe, its
    tag, and the categories-list rows re-tint when the toggle flips data-theme without
    a reload. Emits, per category present and per variant:
      --cat-bg      the raw theme bg (kept for back-compat / subtle fills)
      --cat-stripe  the CURATED highlight (req B) — left stripe, tag, cat-list row bg
      --cat-fg      readable text colour to lay over that highlight (req D)
      --cat-accent  the category's terminal `bold` accent."""
    seen, colors = set(), []
    for t in tasks:
        c = t.get("color")
        if c and c not in seen:
            seen.add(c)
            colors.append(c)
    rules = []
    for c in colors:
        cls = _cat_class(c)
        for variant in ("dark", "light"):
            pal = _palette_for(c, theme, variant)
            decls = []
            bg = _cat_bg(pal) if pal else None
            if bg:
                decls.append("--cat-bg:%s" % bg)
            # the CURATED highlight (req B): distinct + true-to-name per category &
            # variant — falls back to brighten(bg) only for an unknown colour.
            hi = category_highlight(c, variant) or (brighten(bg) if bg else None)
            if hi:
                decls.append("--cat-stripe:%s" % hi)
                decls.append("--cat-fg:%s" % _readable_fg(hi))
            accent = _cat_accent(pal, None) if pal else None
            if accent:
                decls.append("--cat-accent:%s" % accent)
            if decls:
                rules.append('html[data-theme="%s"] .%s{%s}' % (variant, cls, ";".join(decls)))
    return "\n".join(rules)


def _status_display(t):
    """The task's 4-state board display status (new / paused / live / closed). Prefers
    the view-model's precomputed `status_display`; falls back to deriving it from the
    stored status + the `live` flag so an older/partial view-model still renders (an
    older vm's `active` display value reads as `live`)."""
    sd = t.get("status_display")
    if sd == "active":                    # pre-rename vm → the same green state
        return "live"
    if sd in ("new", "paused", "live", "closed"):
        return sd
    st = t.get("status") or "open"
    if st == "closed":
        return "closed"
    if t.get("live"):
        return "live"
    return "paused" if st == "active" else "new"


def _status_cell(t):
    cls = _status_display(t)
    glyph = _STATUS_GLYPH.get(cls, "")
    disp = _STATUS_LABEL.get(cls, cls)
    return ('<span class="c-status"><span class="pill %s">%s %s</span></span>'
            % (_e(cls), _e(glyph), _e(disp)))


def _brain_chip(t):
    """F4: the subtle brain chip that rides AFTER the category tag — the brain a task
    lives in. Emitted ONLY when Interbrain is on (self rows carry the `_ib` stamp; foreign
    rows are inherently interbrain), so the off render is byte-parity (the chip HTML simply
    isn't produced). Non-'main' brains only — 'main' is the implicit default, no chip."""
    if not (t.get("_ib") or t.get("foreign")):
        return ""
    b = (t.get("brain") or "").strip()
    if not b or b == "main":
        return ""
    return '<span class="bchip" title="brain · %s">%s</span>' % (_e(b), _e(b))


def _tag_cell(t, hi_fb, fg_fb):
    tag = t.get("tag")
    bchip = _brain_chip(t)
    if not tag:
        return '<span class="c-cat">%s</span>' % bchip
    # the tag is filled with the category HIGHLIGHT (req B/D) with readable text on
    # top, so it agrees with the left stripe + the categories-list rows. Colours come
    # from the per-variant --cat-* vars (set on the row); the inline hexes are the
    # resolved-variant no-JS fallback.
    style = ("color:var(--cat-fg,%s);background:var(--cat-stripe,%s);"
             "border-color:var(--cat-stripe,%s)"
             % (_e(fg_fb or "var(--ink)"), _e(hi_fb or "transparent"),
                _e(hi_fb or "currentColor")))
    return ('<span class="c-cat"><span class="tag" style="%s">%s</span>%s</span>'
            % (style, _e(tag), bchip))


def _effort_cell(t, theme, variant):
    """The effort cell (gauge + "EFF word"), COLOUR-CODED by tier: xl=red, l=orange,
    m=yellow, s=green, xs=white. The colour reuses the per-variant category HIGHLIGHT
    palette (via _highlight_fb, the same fallback the row stripe uses), so each tier is
    vivid AND visible in BOTH variants (the category machinery already solves "white in
    light mode"). Unknown/empty effort stays uncoloured (early return), as before."""
    gauge = t.get("effort_gauge") or ""
    raw = (t.get("effort") or "").lower()
    eff = raw.upper()
    word = t.get("effort_label") or ""
    if not eff:
        return '<span class="c-eff"></span>'
    label = "%s %s" % (eff, word) if word else eff
    color_key = {"xl": "red", "l": "orange", "m": "yellow",
                 "s": "green", "xs": "white"}.get(raw)
    if color_key:
        hi = _highlight_fb(color_key, theme, variant)
        return ('<span class="c-eff" style="color:%s"><span class="gauge">%s</span>%s</span>'
                % (_e(hi), _e(gauge), _e(label)))
    return ('<span class="c-eff"><span class="gauge">%s</span>%s</span>'
            % (_e(gauge), _e(label)))


def _sec_group(slug, title, body, open_default=False, key=None):
    """Wrap expanded-row content in a collapsible WS6 SECTION with a coloured header.
    `slug` (overview/cost/prompts/sessions/history) picks the header hue. Returns "" when
    `body` is empty, so an absent section is simply omitted and a bare task stays
    byte-stable. Overview passes open_default=True and NO key so it stays open regardless
    of the JS restore; the other sections carry a data-key so their open/closed state
    persists (and defaults collapsed on a fresh load) via the generic details[data-key]
    handler."""
    if not body:
        return ""
    # data-defopen marks a section whose SERVER default is open (the Overview) so the
    # behavior script can restore a row's children to their defaults on collapse.
    op = ' open data-defopen="1"' if open_default else ""
    dk = (' data-key="%s"' % _e(key)) if key is not None else ""
    return ('<details class="sec sec-%s"%s%s>'
            '<summary class="sech sech-%s">%s</summary>'
            '<div class="secbody">%s</div></details>'
            % (slug, op, dk, slug, _e(title), body))


def _story_cell(refs):
    """The STORY column cell body for a task's pre-derived story refs — a list of
    `{"id","url"}` (task-station.py builds these from the structured `stories` field via
    obsidian_sync.story_ref). Each id is a link to its ADO url when the entry carries one,
    plain text otherwise; ids are comma-joined, capped at 3 with a `+N` overflow. Empty
    string for a storyless task, so the cell renders blank."""
    seen, out = set(), []
    for r in (refs or []):
        sid = (r.get("id") if isinstance(r, dict) else None) or ""
        if not sid or sid in seen:
            continue
        seen.add(sid)
        out.append((sid, (r.get("url") or "").strip()))
    if not out:
        return ""
    shown, extra = out[:3], len(out) - 3
    parts = []
    for sid, url in shown:
        if url:
            parts.append('<a href="%s" target="_blank" rel="noopener noreferrer">%s</a>'
                         % (_e(url), _e(sid)))
        else:
            parts.append(_e(sid))
    txt = ", ".join(parts)
    if extra > 0:
        txt += ' <span class="d">+%d</span>' % extra
    return txt


def _pr_line(pr):
    """One stored/derived PR `{url,desc}` → a single line: the linked url (or `#<n>`)
    then its description when present. Plain-string entries are tolerated too."""
    if isinstance(pr, dict):
        url = (pr.get("url") or "").strip()
        desc = (pr.get("desc") or "").strip()
    else:
        url, desc = (str(pr).strip() if pr else ""), ""
    if not url:
        return ""
    # target=_blank so external PR/story URLs open in a new tab — lets the user's
    # default-browser router (e.g. Safari + BrowserFairy/basicurlrouter) handle them
    # instead of navigating away from the self-contained board page.
    link = '<a href="%s" target="_blank" rel="noopener noreferrer">%s</a>' % (_e(url), _e(url))
    if desc:
        link += ' <span class="d">— %s</span>' % _e(desc)
    return '<div class="pr">%s</div>' % link


def _file_line(f, scheme):
    """One files entry → the basename as an editor-scheme link that OPENS the file
    (default `vscode://file/<abspath>`), the dir as dim text, and a copy-path button.
    Tolerates BOTH the legacy 2-tuple `(base, dir)` and the WS7 3-tuple
    `(base, dir, abspath)`; when no abspath is supplied it is reconstructed from dir+base.
    The link is a hyperlink the user CLICKS (an app URL scheme), NEVER a loaded asset —
    no src/link/@import — so the board stays self-contained. dir/`file://` links are inert
    in a `file://` page, so the copy-path button is the reliable folder-reveal fallback."""
    try:
        base = f[0]
        d = f[1] if len(f) > 1 else ""
        abspath = f[2] if len(f) > 2 else ""
    except (TypeError, IndexError):
        base, d, abspath = str(f), "", ""
    if not abspath:
        abspath = os.path.join(d, base) if (d and d != ".") else base
    p = abspath if str(abspath).startswith("/") else "/" + str(abspath)
    sch = scheme or "vscode"
    # `file` → a plain file:// link (browser/OS handles it) rather than the editor
    # `<scheme>://file/<path>` form. Any other scheme is an editor's open-file scheme.
    href = ("file://%s" % p) if sch == "file" else ("%s://file%s" % (sch, p))
    return ('<div><a class="fopen" href="%s">%s</a> <span class="d">— %s</span> '
            '<button class="copybtn fcopy" type="button" data-copy="%s" '
            'aria-label="Copy path">copy path</button></div>'
            % (_e(href), _e(base), _e(d), _e(abspath)))


def _local_iso(s):
    """An ISO timestamp stamped in UTC (naive treated as UTC), converted to the
    system local timezone for display. Returns the input unchanged if unparseable."""
    if not s:
        return s
    try:
        dt = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return s
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().isoformat(timespec="seconds")


def _history_detail(t):
    """The on-demand HISTORY trail (`update --log` dated milestones, entries {ts,text}).
    WS6: this is now the LAST of the five collapsible expanded-row SECTIONS
    (<details class="sec sec-history">) — the secondary trail, kept after the
    current-snapshot digest + usage + prompts + sessions, mirroring `/todo <n> history`.
    Its body scrolls inside a shared .longlist box. Returns "" when the task has no
    history. data-key="hist:<seq>" persists the open/closed state across the change-driven
    refresh via the generic details[data-key] handler (collapsed by default). Every ts +
    text is HTML-escaped (_e); ts is converted from its stored UTC to local first."""
    hist = t.get("history") or []
    if not hist:
        return ""
    seq = t.get("seq")
    if seq is None:
        seq = t.get("id") or (t.get("title") or "").lower()
    entries = "".join(
        '<div class="hentry"><span class="hts">%s</span> %s</div>'
        % (_e(_local_iso(e.get("ts", ""))), _e(e.get("text", "")))
        for e in hist)
    return ('<details class="sec sec-history" data-key="hist:%s">'
            '<summary class="sech sech-history">history (%d) ▸</summary>'
            '<div class="secbody"><div class="hbody longlist">%s</div></div></details>'
            % (_e(seq), len(hist), entries))


def _sessions_line(st):
    """WS4: the compact session-tree summary for the brief detail, e.g.
    `2 hubs · 1 running · 1 resumable · 1 worker`. Uses the canonical session-state
    vocabulary (running = a process is alive now; resumable = a saved transcript,
    nothing running); zero clauses are omitted. Falls back to the legacy
    `(N live)` clause when an older view-model only carries `live_hubs`. '' when
    the task has no hubs and no workers (row omitted)."""
    hubs = st.get("hubs") or 0
    workers = st.get("workers") or 0
    if not hubs and not workers:
        return ""
    parts = []
    if hubs:
        parts.append("%d hub%s" % (hubs, "" if hubs == 1 else "s"))
        if "running" in st or "resumable" in st:
            if st.get("running"):
                parts.append("%d running" % st["running"])
            if st.get("resumable"):
                parts.append("%d resumable" % st["resumable"])
        elif st.get("live_hubs"):
            parts[-1] += " (%d live)" % st["live_hubs"]
    if workers:
        parts.append("%d worker%s" % (workers, "" if workers == 1 else "s"))
    return " · ".join(parts)


def _rel_link(seq):
    """A clickable `#N` relation reference — opens task N's row (the behavior script
    wires `a.rellink` → openTaskRow + scroll; stopPropagation when it sits in a summary
    so it doesn't also toggle the row it's inside)."""
    return '<a class="rellink" href="#task-%s">#%s</a>' % (_e(seq), _e(seq))


def _rel_token(e, incoming):
    """One relation edge rendered as HTML with a CLICKABLE `#N`. Outgoing (stored on
    this task): spawned-from → `from #N`, else `related #N`. Incoming (derived — another
    task points at this one): spawned-from → `spawned #N`, else `related #N`; a closed
    counterpart gets a ` (closed)` suffix."""
    link = _rel_link(e.get("seq"))
    kind = e.get("kind")
    if not incoming:
        verb = "from" if kind == "spawned-from" else "related"
        return "%s %s" % (verb, link)
    closed = " (closed)" if e.get("status") == "closed" else ""
    verb = "spawned" if kind == "spawned-from" else "related"
    return "%s %s%s" % (verb, link, closed)


def _related_line(rel):
    """WS4: the combined relation line (HTML, clickable `#N`) for the brief detail, e.g.
    `from #363 · spawned #365 (closed) · related #341`. '' when no edges."""
    frm = rel.get("from") or []
    inn = rel.get("in") or []
    if not frm and not inn:
        return ""
    toks = [_rel_token(e, False) for e in frm if e.get("seq") is not None]
    toks += [_rel_token(e, True) for e in inn if e.get("seq") is not None]
    return " · ".join(toks)


def _row_related_chip(t):
    """A muted `↳ from #N` marker on the COLLAPSED row when this task carries outgoing
    relation edges (spawned-from / related). Each `#N` is a clickable link that opens the
    counterpart's row. '' when there are none."""
    frm = (t.get("related") or {}).get("from") or []
    links = ", ".join(_rel_link(e.get("seq")) for e in frm if e.get("seq") is not None)
    if not links:
        return ""
    return '<span class="relfrom" title="relations">↳ from %s</span>' % links


def _brief_field(row_label, summary_label, body_html, collapse):
    """A digest field. When `collapse` is True (a lot of items — steps/PRs/files that
    would otherwise dominate the Overview), render it as a COLLAPSED <details> whose
    summary carries the count; otherwise the normal inline labeled row. No data-key, so
    it always defaults collapsed (reset-to-default) and never persists an expansion."""
    if collapse:
        return ('<details class="briefsec"><summary class="k">%s &#9656;</summary>'
                '%s</details>' % (summary_label, body_html))
    return '<div class="row"><span class="k">%s</span>%s</div>' % (row_label, body_html)


def _brief_detail(t):
    # Digest-first: goal → next → steps checklist (with rollup) →
    # decisions → repos → stories → PRs → files. goal/state/decisions render
    # through mdlite (escaped first); steps/files/prs/stories are structured.
    goal = t.get("goal")
    state = t.get("state")
    stats = (t.get("stats") or "").strip()
    steps = t.get("steps") or []
    repos, prs, files = t.get("repos"), t.get("prs"), t.get("files")
    stories = t.get("stories")
    # the session-tree counts are no longer a digest row — they ride on the Sessions
    # section header (see _row). related edges stay a digest row.
    related_line = _related_line(t.get("related") or {})
    # WS-D second-brain-gated: the cited [[notes]] — None (panel omitted) unless the
    # knowledge tier is on AND the task cites notes, so bare/public tasks are unchanged.
    knowledge = t.get("knowledge") or []
    # Cost moved from the collapsed row's own column into the digest; "" when the task has
    # no cost signal (_cost_text never returns "$n/a"), so a cost-only task still renders.
    cost_txt = _cost_text(t)
    # F5 correspondence: peer links + fork provenance (a task's pair partners).
    links = t.get("links") or []
    forked_from = t.get("forked_from") or None
    # decisions render OUTSIDE the brief now — grouped directly above Summary (see the
    # overview assembly), so they are NOT part of this card's emptiness guard.
    if not (goal or state or stats or steps or repos or prs or stories
            or files or related_line or knowledge or cost_txt or links or forked_from):
        return ""
    rows = ['<div class="brief">']
    if goal:
        rows.append('<div class="row"><span class="k">goal</span>'
                    '<span class="v">%s</span></div>' % _rich(goal))
    if state:
        rows.append('<div class="row"><span class="k">next</span>'
                    '<span class="v">%s</span></div>' % _rich(state))
    if stats:
        # Compact time/cost line — plain text (no markdown), so escape only.
        rows.append('<div class="row"><span class="k">stats</span>'
                    '<span class="v">%s</span></div>' % _e(stats))
    if cost_txt:
        # D1: the cost row carries the interactive stacked model-mix bar (same hover
        # data-tip segments as the old collapsed-row column) ABOVE the derived $ text —
        # restored here after the column was dropped. Bar omitted when there's no mix, so
        # a cost-only task still shows just the text.
        cost_mix = (t.get("usage") or {}).get("mix") or []
        bar = ('<span class="ccostbar">%s</span>' % _mix_bar(cost_mix)) if cost_mix else ""
        rows.append('<div class="row"><span class="k">cost</span>'
                    '<span class="v">%s%s</span></div>' % (bar, _e(cost_txt)))
    if steps:
        done = sum(1 for s in steps if s.get("done"))
        items = "".join(
            '<li class="%s">%s %s</li>'
            % ("done" if s.get("done") else "todo",
               "✓" if s.get("done") else "☐", _e(s.get("text", "")))
            for s in steps)
        lbl = "steps %d/%d" % (done, len(steps))
        rows.append(_brief_field(lbl, lbl, '<ul class="steps">%s</ul>' % items,
                                 len(steps) > 6))
    if repos:
        rows.append('<div class="row"><span class="k">repos</span>'
                    '<span class="v">%s</span></div>' % _e(", ".join(repos)))
    if stories:
        # Stories render ABOVE PRs (the work item frames the PRs that implement it).
        # Mirrors the PRs block: each story on its own line, linked url + optional desc.
        items = "".join(_pr_line(s) for s in stories)
        rows.append('<div class="row"><span class="k">stories</span>'
                    '<div class="stories">%s</div></div>' % items)
    if prs:
        items = "".join(_pr_line(p) for p in prs)
        rows.append(_brief_field("prs", "prs (%d)" % len(prs),
                                 '<div class="prs">%s</div>' % items, len(prs) > 4))
    if files:
        # vm `files` is a (base, dir, abspath) 3-tuple (WS7); each basename is an
        # editor-scheme link that opens it in the configured editor (default
        # vscode://file/<abspath>), with the dir as dim text + a copy-path button
        # (WS6) — _file_line tolerates a legacy 2-tuple too.
        scheme = t.get("editor_scheme") or "vscode"
        items = "".join(_file_line(f, scheme) for f in files)
        rows.append(_brief_field("files", "files (%d)" % len(files),
                                 '<div class="files">%s</div>' % items, len(files) > 6))
    # WS4: relation edges — a labeled row in the repos pattern (session-tree counts moved
    # onto the Sessions section header). Emitted only when there is content.
    if related_line:
        # related_line is already HTML (clickable #N links) — do NOT re-escape it.
        rows.append('<div class="row"><span class="k">related</span>'
                    '<span class="v">%s</span></div>' % related_line)
    # WS-D second-brain-gated: the "Related knowledge" panel — the vault notes this
    # task cites (as [[wikilink]] text). Only present when the knowledge tier is on.
    if knowledge:
        toks = " · ".join("[[%s]]" % _e(n) for n in knowledge)
        rows.append('<div class="row"><span class="k">knowledge</span>'
                    '<span class="v">%s</span></div>' % toks)
    # F5: fork provenance ("forked from <peer> @rev") — renders above the peer links.
    if forked_from:
        ff = forked_from
        rev = (ff.get("at_rev") or "")[:8]
        label = "%s%s" % (_e(ff.get("handle") or ff.get("alias") or "peer"),
                          (" @%s" % _e(rev)) if rev else "")
        title = _e(ff.get("title") or "")
        rows.append('<div class="row"><span class="k">forked from</span>'
                    '<span class="v"><span class="lchip" title="%s">%s</span></span></div>'
                    % (title, label))
    # F5: correspondence pairs (task-station link) — one chip per linked peer task.
    if links:
        chips = "".join(
            '<span class="lchip" title="%s%s">%s</span>'
            % (_e(l.get("alias") or ""),
               (" · " + _e(l.get("kind"))) if l.get("kind") and l.get("kind") != "link" else "",
               _e(l.get("handle") or ("%s-%s" % (l.get("alias") or "", (l.get("uuid8") or "")[:8]))))
            for l in links)
        rows.append('<div class="row"><span class="k">linked</span>'
                    '<span class="v">%s</span></div>' % chips)
    rows.append('</div>')
    return "".join(rows)


def _summary_html(summary):
    """The full summary, rendered as light markdown (mdlite) when available, else
    html-escaped plain text. Either path escapes first — no raw HTML survives."""
    text = (summary or "").strip()
    if not text:
        return "(no summary recorded)"
    if _md is not None:
        try:
            rendered = _md.render(text)
        except Exception:
            rendered = ""
        if rendered:
            return rendered
    return _e(text)


# --------------------------------------------------------- WS4 usage panels ----

_MIX_FAMILIES = ("fable", "opus", "sonnet", "haiku")


def _mix_class(family, unpriced=False):
    """The CSS segment/dot class for a model family — a known family maps to its
    coloured class, an unknown/unpriced one to the neutral 'unknown' colour."""
    if unpriced:
        return "mx-unknown"
    f = (family or "").lower()
    return ("mx-" + f) if f in _MIX_FAMILIES else "mx-unknown"


def _mix_bar(segs):
    """A single stacked model-mix bar — one coloured <span> segment per family, width
    = its share (0..1). '' when there are no segments. Each segment carries a
    'family NN%' title so the mix is readable on hover even without the legend."""
    if not segs:
        return ""
    parts = []
    for s in segs:
        pct = max(0.0, min(1.0, s.get("pct") or 0.0))
        fam = s.get("family") or "?"
        na = " — unpriced (unknown model)" if s.get("unpriced") else ""
        tip = "%s · %d%%%s" % (fam, round(pct * 100), na)   # B4: styled hover tooltip
        parts.append('<span class="mseg %s" style="width:%s%%" title="%s %d%%%s" '
                     'data-tip="%s"></span>'
                     % (_mix_class(fam, s.get("unpriced")), round(pct * 100, 2),
                        _e(fam), round(pct * 100), na, _e(tip)))
    return '<span class="mixbar">%s</span>' % "".join(parts)


def _mix_legend(segs):
    """The 'family NN%' legend that sits beside the expanded mix bar (colour dot +
    label per family; unpriced families are flagged 'unpriced', never $n/a). '' when
    empty."""
    if not segs:
        return ""
    parts = []
    for s in segs:
        na = " unpriced" if s.get("unpriced") else ""
        parts.append('<span class="mleg"><span class="mdot %s"></span>%s %d%%%s</span>'
                     % (_mix_class(s.get("family"), s.get("unpriced")),
                        _e(s.get("family") or "?"), round((s.get("pct") or 0.0) * 100), na))
    return '<span class="mixleg">%s</span>' % "".join(parts)


def _human_tokens(n):
    """Compact human token count: 1234567 → '1.2M', 34567 → '34.6k', 812 → '812'."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "0"
    if n >= 1_000_000:
        return "%.1fM" % (n / 1_000_000.0)
    if n >= 1_000:
        return "%.1fk" % (n / 1_000.0)
    return str(n)


def _ts_local(ts):
    """Epoch-seconds → a compact local-time 'YYYY-MM-DD HH:MM' (prompts store ts as
    epoch seconds). '' when absent/unparseable."""
    if ts is None:
        return ""
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _clip(s, n):
    """Collapse whitespace and clip to `n` chars with an ellipsis (for the prompt
    preview lines)."""
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


# WS6 removed `_mix_chip` — the collapsed row no longer folds a model-mix bar + $ into
# the Task cell. The derived cost is now a `cost` row in each task's Overview digest (via
# `_cost_text`, which never yields "$n/a"); the full stacked mix bar lives only in the
# expanded Usage & Cost panel.


def _cost_text(t):
    """The never-"n/a" cost string, rendered as the `cost` row in each task's Overview
    digest (it used to be a dedicated grid column). Prefers WS7's precomputed
    `cost_cell` / `stats_cost` (which already kill $n/a), then falls back to the usage
    ledger: fully-priced derived total → "$X.XX"; else delegate-reported → "$X.XX";
    else a priced-subtotal floor → "≥$X.XX"; else "". A task with NO cost signal shows
    nothing (not $0.00), so bare rows stay clean. NEVER returns "$n/a"."""
    cc = t.get("cost_cell")
    if cc:
        return cc
    sc = t.get("stats_cost")
    if isinstance(sc, dict) and sc.get("text"):
        return sc["text"]
    u = t.get("usage") or {}
    if not u:
        return ""
    tc = u.get("total_cost_usd") or 0.0
    if not u.get("any_unpriced") and tc > 0:
        return "$%.2f" % tc
    rep = u.get("reported_cost_usd") or 0.0
    if rep > 0:
        return "$%.2f" % rep
    if tc > 0:
        return "≥$%.2f" % tc          # priced-subtotal floor (some models unpriced)
    return ""


def _fmt_usd(v):
    """`$` amount text (no colour): 4 decimals for a sub-cent value, 2 otherwise —
    mirrors hud.fmt_cost so tiny per-session costs stay visible."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        v = 0.0
    return "%.4f" % v if 0 < v < 0.01 else "%.2f" % v


def _cost_band(usd, thresholds):
    """The HUD stdev band CLASS for a $ figure (board B9): `cost-lo` (≤μ, green),
    `cost-mid` (≤μ+σ, amber), `cost-hi` (>μ+σ, orange). An unknown ($ None) figure is
    neutral (`cost-mid`). `thresholds` = [lo, hi] = [μ, μ+σ] from the view-model."""
    lo, hi = 0.01, 0.05
    if isinstance(thresholds, (list, tuple)) and len(thresholds) >= 2:
        lo, hi = thresholds[0], thresholds[1]
    if usd is None:
        return "cost-mid"
    try:
        v = float(usd)
    except (TypeError, ValueError):
        return "cost-mid"
    if v > hi:
        return "cost-hi"
    if v > lo:
        return "cost-mid"
    return "cost-lo"


def _money(usd, thresholds, floor=False):
    """A `$` figure coloured by the stdev bands (B9). A None figure with `floor` renders
    a dim `$—`; a numeric one renders `$X.XX` (or `≥$X.XX` when floor) in its band hue."""
    if usd is None:
        return '<span class="mono cost-mid">$&mdash;</span>' if floor else ""
    pre = "&ge;$" if floor else "$"
    return ('<span class="mono %s">%s%s</span>'
            % (_cost_band(usd, thresholds), pre, _fmt_usd(usd)))


def _tok(n):
    """A token count in the HUD's muted blue (B9)."""
    return '<span class="mono tok">%s</span>' % _e(_human_tokens(n))


def _session_role_label(s):
    """`hub` / `worker:label` / role for a per-session row."""
    role = s.get("role") or "unknown"
    label = s.get("label")
    if role == "worker" and label:
        return "worker:%s" % label
    return role


def _prompt_session_tag(p):
    """`hub` / `worker:<label>` / `<role>` + the short session id — the per-prompt
    attribution shown in the prompt trail. '' when no attribution is present."""
    role = p.get("role")
    if not role:
        return ""
    if p.get("label"):
        role = "%s:%s" % (role, p["label"])
    sid = p.get("sid")
    return "%s %s" % (role, sid) if sid else role


def _prompt_item(p):
    """One prompt row for a hub's prompt breakdown (board B6/B7/B8): FULL untruncated
    text, styled by TWO axes — hub-vs-worker (`p-hub`/`p-worker`) and human-vs-generated
    (`human`/`gen`). A slash/command/compaction/managed line reads as `gen` (secondary);
    a genuine human-typed prompt reads as `human` (primary)."""
    role_cls = "p-worker" if p.get("role") == "worker" else "p-hub"
    axis = "human" if p.get("human", True) else "gen"
    kind = p.get("kind") or "prompt"
    tag = _prompt_session_tag(p)
    tag_html = ('<span class="psid">%s</span>' % _e(tag)) if tag else ""
    # Claude's last-bullet reply, paired under the human prompt as `↳ …` (mirrors the
    # markdown/CLI prompt views). Omitted when there is no reply, exactly as those do.
    reply = p.get("reply") or ""
    reply_html = ('<div class="preply">↳ %s</div>' % _e(reply)) if reply else ""
    return ('<div class="pmt %s %s"><span class="pk %s">%s</span>%s'
            '<span class="pts">%s</span><div class="ptx">%s</div>%s</div>'
            % (role_cls, axis, _e(kind), _e(kind), tag_html,
               _e(_ts_local(p.get("ts"))), _e(p.get("text") or ""), reply_html))


# Work-mix colours are keyed by PHASE LABEL, not bar position, so "planning" (etc.) is
# the same colour in every bar on the page regardless of each session's share order.
# The map covers the canonical phases.PHASES; an unknown label degrades to a stable
# byte-sum bucket (never a shifting index).
_PHASE_CLASS = {"planning": "pc0", "research": "pc1", "implementation": "pc2",
                "verification": "pc3", "delivery": "pc4", "other": "pc5"}


def _phase_class(label):
    l = (label or "?").lower()
    return _PHASE_CLASS.get(l, "pc%d" % (sum(bytearray(l.encode("utf-8"))) % 6))


def _phase_tip(p):
    """B4 hover tooltip text for a work-mix segment: `label · share%` plus its output
    tokens + derived $ when the phase blob carries them (both are optional — degrades
    to just label · share when absent)."""
    bits = ["%s · %d%%" % (p.get("label") or "?", round((p.get("pct") or 0.0) * 100))]
    out = p.get("out")
    if out:
        bits.append(("%.0fk tok" % (out / 1000)) if out >= 1000 else "%d tok" % int(out))
    cost = p.get("cost_usd")
    if cost:
        bits.append(("$%.2f" % cost) if cost >= 0.01 else ("$%.4f" % cost))
    return " · ".join(bits)


def _workmix_block(phases, title="work mix"):
    """A stacked work-mix (phase) bar + legend for a hub or worker (board B14), plus the
    B5 'other' drill-down — the top tool/command names that fell into `other`. '' when
    there is no phase data. Defends against the `__v` version stamp leaking as a bar."""
    phases = [p for p in (phases or []) if p.get("label") != "__v"]
    if not phases:
        return ""
    bar = "".join(
        '<span class="pseg %s" style="width:%s%%" title="%s %d%%" data-tip="%s"></span>'
        % (_phase_class(p.get("label")),
           round(max(0.0, min(1.0, p.get("pct") or 0.0)) * 100, 2),
           _e(p.get("label") or "?"), round((p.get("pct") or 0.0) * 100),
           _e(_phase_tip(p)))                       # B4: label · share · tokens · $
        for p in phases)
    legend = "".join(
        '<span class="pleg"><span class="pdot %s"></span>%s %d%%</span>'
        % (_phase_class(p.get("label")), _e(p.get("label") or "?"),
           round((p.get("pct") or 0.0) * 100))
        for p in phases)
    drill = ""
    for p in phases:
        if p.get("label") == "other" and p.get("names"):
            items = "".join(
                '<span class="odn"><span class="odname">%s</span> &times;%d</span>'
                % (_e(n.get("name")), n.get("count") or 0) for n in p["names"])
            drill = '<div class="otherdrill">other &rarr; %s</div>' % items
    return ('<div class="workmix"><div class="k">%s</div>'
            '<span class="pbar-mix">%s</span><div class="plegend">%s</div>%s</div>'
            % (_e(title), bar, legend, drill))


def _cost_agg_line(summ, reported, thresholds, floorable=True):
    """The one-line token + $ summary for a usage summary dict (a hub aggregate, a hub's
    own session, or a worker): `in N · out N · $X.XX derived · $Y.YY reported`, tokens in
    muted blue + $ in its stdev band. '' when the summary is empty."""
    if not summ:
        return ""
    bits = ["in %s" % _tok(summ.get("in") or 0), "out %s" % _tok(summ.get("out") or 0)]
    cost = summ.get("cost_usd")
    floor = bool(summ.get("any_unpriced")) and floorable
    money = _money(cost, thresholds, floor=floor)
    if money:
        bits.append("%s derived" % money)
    if isinstance(reported, (int, float)) and reported > 0:
        bits.append("%s reported" % _money(reported, thresholds))
    return '<div class="cagg">%s</div>' % " &middot; ".join(bits)


def _hub_cost_section(card, thresholds, seq_key):
    """A hub's 'cost & work-mix' expandable (board B14): the hub-plus-workers AGGREGATE
    cost line + model-mix bar + work-mix bar (with the B5 other drill-down), and — when
    the hub has workers — a distinct 'this hub alone' line so the split is legible.
    Per-worker cost + work-mix live in the nested workers section. '' when no usage."""
    agg = card.get("agg") or {}
    own = card.get("own") or {}
    if not (agg.get("mix") or agg.get("in") or agg.get("out")):
        return ""
    workers = card.get("workers") or []
    body = []
    if workers:
        body.append('<div class="mono" style="color:var(--dim);font-size:10.5px">'
                    'this hub alone</div>')
        body.append(_cost_agg_line(own, 0, thresholds))
        body.append('<div class="mono" style="color:var(--dim);font-size:10.5px">'
                    'hub + %d worker%s</div>' % (len(workers),
                                                 "" if len(workers) == 1 else "s"))
    body.append(_cost_agg_line(agg, card.get("reported"), thresholds))
    body.append('<div class="mixrow">%s%s</div>'
                % (_mix_bar(agg.get("mix") or []), _mix_legend(agg.get("mix") or [])))
    wm = _workmix_block(agg.get("phases") or [])
    if wm:
        body.append(wm)
    return ('<details class="hubsec hubcost" data-key="hc:%s">'
            '<summary>cost &amp; work-mix &#9656;</summary>'
            '<div class="hsbody">%s</div></details>'
            % (_e(seq_key), "".join(b for b in body if b)))


def _card_state(card):
    """The session's 3-tier liveness state, back-compat: prefer the explicit `state`
    field; fall back to running/'' from the legacy boolean `live` so older view-models
    still render a badge."""
    st = card.get("state")
    if st:
        return st
    return "running" if card.get("live") else ""


# The canonical per-task session-card state vocabulary + hover descriptions (used by the
# hub/session badges via _state_badge). These three words (running/resumable/linked) are
# a DIFFERENT axis from the live strip's working/idle activity dot — kept distinct on
# purpose so "running" isn't overloaded. Each entry: (css class, glyph, word, title
# description). "attached" is the pre-rename alias of "linked" (older view-models).
_STATE_META = {
    "running":   ("sb-run", "&#9679;", "running",
                  "a Claude process is alive now"),
    "resumable": ("sb-res", "&#9681;", "resumable",
                  "saved — reopen with the resume command"),
    "linked":    ("sb-att", "&#9675;", "linked",
                  "recorded on this task — nothing to resume"),
}
_STATE_META["attached"] = _STATE_META["linked"]


def _state_badge(state):
    """The 3-tier per-task session-card badge: RUNNING = a live process (pid alive);
    RESUMABLE = a saved transcript, nothing running; LINKED = recorded on this task,
    nothing to resume. '' for an unknown/empty state. Carries a `title=` describing the
    state. This is a DIFFERENT axis from the live strip's working/idle activity dot
    (kept distinct)."""
    m = _STATE_META.get(state)
    if not m:
        return ""
    cls, glyph, word, desc = m
    return ('<span class="sbadge %s" title="%s">%s %s</span>'
            % (cls, _e(desc), glyph, word))


def _worker_subcard(w, thresholds):
    """One worker's sub-card inside a hub's nested workers list (board B12 + the B14
    per-worker cost + work-mix drill): header (sid/model/age/state) + its own cost line,
    model-mix bar, work-mix bar, and a resume command."""
    head = ['<span class="wclabel">worker:%s</span>' % _e(w.get("label") or "worker")]
    if w.get("sid8"):
        head.append(_e(w.get("sid8")))
    if w.get("model"):
        head.append(_e(w.get("model")))
    if w.get("age"):
        head.append(_e(w.get("age")))
    badge = _state_badge(_card_state(w))
    if badge:
        head.append(badge)
    body = ['<div class="wchead">%s</div>' % " &middot; ".join(head)]
    cl = _cost_agg_line(w, w.get("reported"), thresholds)
    if cl:
        body.append(cl)
    mix = w.get("mix") or []
    if mix:
        body.append('<div class="mixrow">%s%s</div>' % (_mix_bar(mix), _mix_legend(mix)))
    wm = _workmix_block(w.get("phases") or [])
    if wm:
        body.append(wm)
    if w.get("resume_command"):
        body.append('<div class="cmdwrap"><code class="cmd" style="%s">%s</code>'
                    '<button class="copybtn" type="button" aria-label="Copy command">'
                    'copy</button></div>' % (_CMD_STYLE, _e(w.get("resume_command"))))
    return '<div class="wcard">%s</div>' % "".join(body)


def _hub_workers_section(card, thresholds, seq_key):
    """The nested 'worker sessions (N)' expandable under a hub (board B12): one
    sub-card per worker with its cost + work-mix drill. '' when the hub has no workers."""
    workers = card.get("workers") or []
    if not workers:
        return ""
    cards = "".join(_worker_subcard(w, thresholds) for w in workers)
    return ('<details class="workers" data-key="hw:%s">'
            '<summary>worker sessions (%d) &#9656;</summary>%s</details>'
            % (_e(seq_key), len(workers), cards))


def _hub_prompts_section(card, seq_key):
    """A hub's prompt breakdown (board B6/B7/B13): its OWN prompts + all its workers'
    prompts, chronological, FULL text, human-vs-generated styled. '' when none."""
    prompts = card.get("prompts") or []
    if not prompts:
        return ""
    body = "".join(_prompt_item(p) for p in prompts)
    return ('<details class="hubsec prompts" data-key="hp:%s">'
            '<summary>prompts (%d) &#9656;</summary>'
            '<div class="pbody longlist">%s</div></details>'
            % (_e(seq_key), len(prompts), body))


def _hub_card(card, thresholds, seq):
    """One per-hub card (board B10–B14): a header (sid/role/msgs/age/$/live, with MAIN +
    pinned badges), the hub's prompt breakdown, its cost & work-mix, and its nested
    worker sessions. The main hub floats first + gets a distinct highlight; the pinned
    hub keeps the accent highlight."""
    sid8 = card.get("sid8") or ""
    seq_key = "%s:%s" % (seq if seq is not None else "x", sid8 or card.get("role") or "u")
    cls = "hubcard"
    if card.get("main"):
        cls += " main"
    if card.get("pinned"):
        cls += " pinned"
    badges = []
    if card.get("main"):
        badges.append('<span class="hbadge b-main">main</span>')
    if card.get("pinned"):
        badges.append('<span class="hbadge b-pin">\U0001F4CC pinned</span>')
    line = list(badges)
    # Hub session ordinal (#463): '#<seq>-<n>' beside the sid8. Data-gated — cards
    # without an ordinal (pre-roster / worker) render exactly as before.
    ordn = card.get("ordinal")
    if ordn is not None and seq is not None:
        line.append('<span class="hcord">#%s-%s</span>' % (_e(seq), _e(ordn)))
    if sid8:
        line.append('<span class="hcsid">%s</span>' % _e(sid8))
    role = card.get("role") or "hub"
    line.append(_e(role))
    if card.get("msgs"):
        line.append("%s msgs" % _e(card.get("msgs")))
    if card.get("age"):
        line.append(_e(card.get("age")))
    agg = card.get("agg") or {}
    money = _money(agg.get("cost_usd"), thresholds, floor=bool(agg.get("any_unpriced")))
    if money:
        line.append(money)
    badge = _state_badge(_card_state(card))
    if badge:
        line.append(badge)
    head = ['<div class="hcline">%s</div>' % " &middot; ".join(line)]
    if card.get("oneliner"):
        head.append('<div class="hcone">%s</div>' % _e(card.get("oneliner")))
    if card.get("resume_command"):
        head.append('<div class="cmdwrap"><code class="cmd" style="%s">%s</code>'
                    '<button class="copybtn" type="button" aria-label="Copy command">'
                    'copy</button></div>' % (_CMD_STYLE, _e(card.get("resume_command"))))
    parts = ['<div class="hubhead">%s</div>' % "".join(head)]
    parts.append(_hub_prompts_section(card, seq_key))
    parts.append(_hub_cost_section(card, thresholds, seq_key))
    parts.append(_hub_workers_section(card, thresholds, seq_key))
    return '<div class="%s">%s</div>' % (cls, "".join(p for p in parts if p))


def _hubs_panel(t):
    """All per-hub cards for a task (board B10–B14), main hub first (the view-model
    already sorts main → pinned → newest). '' when the task has no hubs."""
    hubs = t.get("hubs") or []
    if not hubs:
        return ""
    thresholds = t.get("cost_thresholds") or [0.01, 0.05]
    seq = t.get("seq")
    return "".join(_hub_card(h, thresholds, seq) for h in hubs)


def _open_action(t):
    """The single 'Open the task' ACTION (board B10 dropped the redundant resume-hub
    block now that every hub shows as its own card). '' when there is no open command."""
    open_cmd = t.get("open_command")
    if not open_cmd:
        return ""
    # `.compact` sizes the action to its content (the `/todo <seq>` command is short) so
    # it's a small piece, not a full-width band across the card.
    return ('<div class="actions compact"><div class="action">'
            '<div class="lab"><span class="name">Open the task</span></div>'
            '<div class="sub">attaches/opens it in the current session &mdash; the recap</div>'
            '<div class="cmdwrap"><code class="cmd" style="%s">%s</code>'
            '<button class="copybtn" type="button" aria-label="Copy command">copy</button>'
            '</div></div></div>' % (_CMD_STYLE, _e(open_cmd)))


def _resume_action(t):
    """The full `cd … && claude --resume …` for the hub session that holds this task's
    context. Rendered ONLY when the task has no per-hub cards (small tasks whose hub was
    never recorded in session_meta) — otherwise the per-hub cards already carry a resume
    line and this would duplicate them. Fixes small tasks (e.g. /todo 364) showing only
    the `/todo <seq>` recap and no real session resume: the view-model computes
    `resume_main` regardless, but nothing consumed it. '' when there is no resume target."""
    rm = t.get("resume_main")
    if not rm or not rm.get("command"):
        return ""
    label = rm.get("label") or "Resume"
    sub = ("no live session found &mdash; starts fresh; re-attach with the open command"
           if rm.get("fresh") else
           "jumps back into the working session that holds this task&rsquo;s context")
    act = (' <span class="d">%s</span>' % _e(rm.get("activity"))) if rm.get("activity") else ""
    return ('<div class="actions"><div class="action">'
            '<div class="lab"><span class="name">%s</span>%s</div>'
            '<div class="sub">%s</div>'
            '<div class="cmdwrap"><code class="cmd" style="%s">%s</code>'
            '<button class="copybtn" type="button" aria-label="Copy command">copy</button>'
            '</div></div></div>' % (_e(label), act, sub, _CMD_STYLE, _e(rm["command"])))


def _view_in_graph(t):
    """D3: a 'View in graph' button that opens the Task Graph, scrolls to it, and centers
    the graph on this task (via the enhancement script's centerOnSeq, keyed by
    data-graph-seq). Rendered `hidden`; the enhancement un-hides it only when this task is
    actually a graph node — so no-JS boards and relation-less tasks show nothing. '' when
    the task has no seq to address."""
    seq = t.get("seq")
    if seq is None:
        return ""
    # a small, clean button — no boxed .actions section, no explanatory sub-line, and no
    # ↗ glyph (which reads as "open in a new tab/window"). Hidden until the enhancement
    # confirms this task is a graph node.
    return ('<div class="mgviewwrap" hidden>'
            '<button type="button" class="mgviewbtn" data-graph-seq="%s">'
            'View in graph</button></div>' % _e(seq))


def _owner_chip(t):
    """The owner dot + alias chip that precedes a FOREIGN task's title (colour is never the
    sole channel — the alias text rides alongside). '' for a self row."""
    owner = t.get("owner") or "peer"
    oc = t.get("owner_color") or "#7f8a9c"
    return ('<span class="ochip" style="--oc:%s" title="%s · read-only foreign brain">'
            '<span class="odot"></span>%s</span>'
            % (_e(oc), _e(owner), _e(owner)))


def _handle_chip(t):
    """The display-only mono handle chip (`<owner>-<seq>` / feed handle) shown beside the
    seq. Emitted only when the view-model carries a handle (Interbrain on). No store data."""
    h = t.get("handle")
    return ('<span class="hchip" title="Interbrain handle">%s</span>' % _e(h)) if h else ""


def _foreign_overview(t):
    """The compact overview body for a foreign (peer/org) task — full title + read-only
    notice + digest (goal/state/steps/decisions) + shared signals. No summary/sessions/
    prompts/resume (that data doesn't exist for a foreign brain)."""
    ov = []
    full = t.get("full_title") or t.get("title") or ""
    if full:
        ov.append('<div class="fulltitle">%s</div>' % _e(full))
    ov.append('<div class="foreign-notice">🔒 read-only · foreign brain '
              '(<span class="ochip inline" style="--oc:%s"><span class="odot"></span>%s'
              '</span>)</div>'
              % (_e(t.get("owner_color") or "#7f8a9c"), _e(t.get("owner") or "peer")))
    goal = (t.get("goal") or "").strip()
    state = (t.get("state") or "").strip()
    prog = t.get("progress") or [0, 0]
    dig = []
    if goal:
        dig.append('<div class="fgoal"><span class="k">goal</span> %s</div>' % _rich(goal))
    if state:
        dig.append('<div class="fstate"><span class="k">next</span> %s</div>' % _rich(state))
    try:
        done, total = int(prog[0]), int(prog[1])
    except Exception:
        done, total = 0, 0
    if total:
        dig.append('<div class="fsteps"><span class="k">steps</span> %d/%d</div>'
                   % (done, total))
    if dig:
        ov.append('<div class="fdigest">%s</div>' % "".join(dig))
    decisions = t.get("decisions") or []
    if decisions:
        items = "".join('<li>%s</li>' % _rich(d) for d in decisions)
        ov.append('<details class="ovsec ovsec-decisions">'
                  '<summary class="k">Decisions</summary>'
                  '<ul class="decisions longlist">%s</ul></details>' % items)
    sigs = []
    for p in (t.get("prs") or []):
        sigs.append('<span class="fsig fsig-pr">%s</span>' % _e(p.get("label") or ""))
    for s in (t.get("stories") or []):
        sigs.append('<span class="fsig fsig-story">story %s</span>' % _e(s.get("id") or ""))
    if sigs:
        ov.append('<div class="fsignals">%s</div>' % "".join(sigs))
    return "".join(ov)


def _foreign_actions(t):
    """Foreign rows are read-only (a file:// board can't write), so the correspondence
    affordances (F5.5: Link · Fork · Subscribe · Memo) are shown as the exact terminal
    commands to run — the same copy-the-CLI pattern the manager panel uses. Only rendered
    when Interbrain is on (foreign VMs exist only then), so the off render is unaffected."""
    handle = t.get("handle") or ""
    h = _e(handle)
    acts = [
        ("Link", "task-station link --task &lt;n&gt; --peer %s" % h,
         "record a correspondence pair with your task"),
        ("Fork", "task-station fork --from %s" % h,
         "copy this node's digest into your own task (+ provenance)"),
        ("Subscribe", "task-station subscribe --task &lt;n&gt; --peer %s "
         "--on checkpoint,decision,trail" % h,
         "mint a memo when this task's feed advances"),
        ("Memo", "task-station memo send --task &lt;n&gt; --text &quot;…&quot;",
         "hand a note to your task about this one (cross-brain)"),
    ]
    rows = ['<div class="actions compact foreign-act">']
    for name, cmd, sub in acts:
        rows.append('<div class="action"><div class="lab"><span class="name">%s</span></div>'
                    '<div class="sub">%s</div>'
                    '<pre class="fcmd">%s</pre></div>' % (name, sub, cmd))
    rows.append('<div class="action disabled" title="read-only · foreign brain">'
                '<div class="lab"><span class="name">Open · Resume</span></div>'
                '<div class="sub">disabled — read-only · foreign brain</div></div>')
    rows.append('</div>')
    return "".join(rows)


def _foreign_row(t, theme, variant):
    """Render ONE foreign (peer/org) task as a read-only board row through the classic row
    chrome: owner dot + alias chip before the title, a 🔒 marker, memo-only actions, and no
    sessions/prompts/resume. Only ever called when Interbrain is on (foreign VMs exist only
    then), so it never affects the interbrain-off parity render."""
    color = t.get("color")
    hi_fb = _highlight_fb(color, theme, variant)
    fg_fb = _readable_fg(hi_fb)
    stripe = "var(--cat-stripe,%s)" % hi_fb
    catcls = (" " + _cat_class(color)) if color else ""
    closed = " closed" if t.get("status") == "closed" else ""
    sdisp = _status_display(t)
    statcls = " stat-%s" % sdisp
    okey = t.get("id") or (t.get("handle") or (t.get("title") or "").lower())
    blob = " ".join(str(t.get(k) or "") for k in
                    ("title", "goal", "state", "owner", "handle", "brain", "tag")).lower()
    dattrs = (' data-key="row:%s" data-title="%s" data-cat="%s" data-status="%s"'
              ' data-sess="none" data-search="%s" data-owner="%s" data-brain="%s"'
              ' data-foreign="1"'
              % (_e(okey), _e((t.get("title") or "").lower()), _e(color or ""),
                 _e(sdisp), _e(blob), _e(t.get("owner") or ""), _e(t.get("brain") or "")))
    detail = ('<div class="detail">%s%s</div>'
              % (_sec_group("overview", "overview", _foreign_overview(t),
                            open_default=True),
                 _sec_group("sessions", "actions", _foreign_actions(t),
                            key="sec-foreign:%s" % okey)))
    return (
        '<details class="row foreign%s%s%s" style="border-left-color:%s"%s>'
        '<summary class="rowsum">'
        '%s'
        '<span class="c-seq">%s</span>'
        '<span class="c-task"><span class="disc">▸</span>'
        '%s<span class="rolock" title="read-only · foreign brain">🔒</span>'
        '<span class="ttl">%s</span></span>'
        '%s%s'
        '<span class="c-act">%s</span></summary>'
        '%s</details>'
        % (closed, statcls, catcls, stripe, dattrs, _status_cell(t),
           _handle_chip(t), _owner_chip(t), _e(t.get("title")),
           _tag_cell(t, hi_fb, fg_fb), _effort_cell(t, theme, variant),
           _e(t.get("activity") or ""), detail))


def _row(t, theme, variant):
    if t.get("foreign"):
        return _foreign_row(t, theme, variant)
    color = t.get("color")
    # the left stripe + the tag are the CURATED category highlight (req B): the
    # per-variant --cat-stripe variable, with the resolved-variant highlight as the
    # inline (no-JS) fallback — distinct + true-to-name across both variants.
    hi_fb = _highlight_fb(color, theme, variant)
    fg_fb = _readable_fg(hi_fb)
    stripe = "var(--cat-stripe,%s)" % hi_fb
    catcls = (" " + _cat_class(color)) if color else ""
    closed = " closed" if t.get("status") == "closed" else ""
    # the 4-state display status drives the row class (stat-live breathes green), and
    # the data-status filter attribute so the status dropdown filters by the same value.
    sdisp = _status_display(t)
    statcls = " stat-%s" % sdisp
    # data-sess: the row's best SESSION state (running > resumable > none) for the
    # session-state dropdown — from the session-tree counts (ledger-independent), with
    # the raw live flag as the running fallback for older/partial view-models.
    _st = t.get("session_tree") or {}
    if (_st.get("running") or 0) > 0 or t.get("live"):
        sess = "running"
    elif (_st.get("resumable") or 0) > 0:
        sess = "resumable"
    else:
        sess = "none"
    seq = t.get("seq")
    seqcell = ('#%s' % _e(seq)) if seq is not None else ""
    # F1: the display-only Interbrain handle chip (rnguyen-<seq>) rides next to the seq —
    # only when Interbrain is on (the `_ib` stamp), so the off render is byte-parity.
    if t.get("_ib"):
        seqcell = seqcell + _handle_chip(t)
    # data-* attributes drive the inline search/filter JS (req 8): the title, the
    # category KEY, the status word, and a lowercased blob of all the searchable text.
    rel = t.get("related") or {}
    # WS4: fold the related-task seq numbers into the search blob so a relation edge
    # (e.g. "from #363") is findable by typing the counterpart's number.
    rel_seqs = " ".join(str(e.get("seq"))
                        for e in ((rel.get("from") or []) + (rel.get("in") or []))
                        if e.get("seq") is not None)
    blob = (" ".join(str(t.get(k) or "") for k in ("title", "goal", "state", "summary"))
            + " " + rel_seqs).lower()
    # data-key is a STABLE, NAMESPACED per-row id so the open/closed state persists
    # across the 5s auto-refresh via the GENERIC details[data-key] handler: "row:<seq>"
    # (falling back to the task id or lowercased title when no seq).
    okey = t.get("seq")
    if okey is None:
        okey = t.get("id") or (t.get("title") or "").lower()
    dattrs = (' data-key="row:%s" data-title="%s" data-cat="%s" data-status="%s"'
              ' data-sess="%s" data-search="%s"'
              % (_e(okey), _e((t.get("title") or "").lower()), _e(t.get("color") or ""),
                 _e(sdisp), _e(sess), _e(blob)))
    # F2: owner/brain focus attributes — only when Interbrain is on (parity when off).
    if t.get("_ib"):
        dattrs += (' data-owner="%s" data-brain="%s"'
                   % (_e(t.get("owner") or ""), _e(t.get("brain") or "")))

    # WS6: the expanded detail is grouped into FIVE collapsible sections, each with a
    # coloured header (overview/cost/prompts/sessions/history). Overview is open by
    # default; the rest persist their open/closed state (collapsed on a fresh load) via
    # the generic details[data-key] handler. Every section returns "" when it has no
    # content, so a bare task shows only Overview — no empty panels.
    detail = ['<div class="detail">']
    seq_for_key = t.get("seq")
    if seq_for_key is None:
        seq_for_key = t.get("id") or (t.get("full_title") or t.get("title") or "").lower()

    # ---- Overview: full title (req 1) + at-a-glance digest + full summary (LAST) ----
    ov = []
    full = t.get("full_title") or t.get("title") or ""
    if full:
        ov.append('<div class="fulltitle">%s</div>' % _e(full))
    # B-VG: the "View in graph" action sits prominently at the TOP of the Overview (not
    # buried in Sessions). Rendered hidden; the enhancement un-hides it for graph-node tasks.
    ov.append(_view_in_graph(t))
    brief = _brief_detail(t)
    if brief:
        ov.append(brief)
    # Decisions render directly ABOVE Summary (per Ryan) — the two long-accumulation
    # .longlist boxes sit together, decisions first, so the reasoning trail precedes the
    # prose snapshot. Both are wrapped in their OWN <details> (no `open`) so they default
    # COLLAPSED per-section — the overview opens to the digest, and the long accumulation
    # boxes stay folded until the reader expands them. No data-key (so they never restore
    # to open), keeping them collapsed on every load.
    decisions = t.get("decisions") or []
    if decisions:
        items = "".join('<li>%s</li>' % _rich(d) for d in decisions)
        ov.append('<details class="ovsec ovsec-decisions">'
                  '<summary class="k">Decisions</summary>'
                  '<ul class="decisions longlist">%s</ul></details>' % items)
    # Summary keeps its own .summary class (a superset of .longlist — same scroll cap +
    # its distinctive accent left border), so the existing class="summary" contract holds.
    ov.append('<details class="ovsec ovsec-summary">'
              '<summary class="k">Summary</summary>'
              '<div class="summary">%s</div></details>'
              % _summary_html(t.get("summary")))
    detail.append(_sec_group("overview", "overview", "".join(ov), open_default=True))

    # ---- Sessions: board B10–B14 restructure. The standalone Usage & Cost and Prompts
    # sections are GONE — their content is now baked PER HUB. The Sessions section holds
    # the 'Open the task' action (the redundant resume-hub block was dropped, B10) then
    # one card per hub: each with its own prompts (B6/B7/B8/B13), cost + work-mix
    # (B9/B14), and nested worker sessions (B12). The main hub floats first + is
    # highlighted (B11). ----
    hubs_html = _hubs_panel(t)
    # When there are no per-hub cards (small tasks with no recorded hub session), fall
    # back to the top-level resume_main so the row still shows a real session resume,
    # not just the `/todo <seq>` recap. Hub cards already carry their own resume line.
    sess_body = (_open_action(t)
                 + (_resume_action(t) if not hubs_html else "") + hubs_html)
    # the session-tree counts (N hubs (M live) · K workers) ride on the Sessions header
    # itself, not a separate digest row.
    sess_counts = _sessions_line(t.get("session_tree") or {})
    sess_title = "sessions · %s" % sess_counts if sess_counts else "sessions"
    detail.append(_sec_group("sessions", sess_title, sess_body,
                             key="sec-sessions:%s" % seq_for_key))

    # ---- History: its own sec-history section (collapsed by default) ----
    hist = _history_detail(t)
    if hist:
        detail.append(hist)
    detail.append('</div>')

    # a stable anchor id so the WS5 'Live now' strip can link straight to the row
    # (#task-<seq>); only emitted when the task has a seq to address it by.
    idattr = (' id="task-%s"' % _e(seq)) if seq is not None else ""

    return (
        '<details class="row%s%s%s"%s style="border-left-color:%s"%s>'
        '<summary class="rowsum">'
        '%s'
        '<span class="c-seq">%s</span>'
        '<span class="c-task"><span class="disc">▸</span>'
        '<span class="ttl">%s</span>%s</span>'
        '%s%s'
        '<span class="c-act">%s</span></summary>'
        '%s</details>'
        % (closed, statcls, catcls, idattr, stripe, dattrs, _status_cell(t), seqcell,
           _e(t.get("title")),
           _row_related_chip(t),
           _tag_cell(t, hi_fb, fg_fb), _effort_cell(t, theme, variant),
           _e(t.get("activity") or ""),
           "".join(detail))
    )


def _section(title, tasks, theme, variant, see_more_after=None):
    out = ['<div class="sec"><h2>%s</h2><span class="count">%d</span></div>'
           % (_e(title), len(tasks))]
    if not tasks:
        out.append('<div class="empty">No %s tasks.</div>' % _e(title.lower()))
        return out
    out.append('<div class="board">')
    # Six columns: status · # · task · category · effort · activity. The live-session
    # signal is folded into the status pill (active = a session is running now), so there
    # is no separate live column. Cost + story live in each task's Overview digest.
    out.append('<div class="head">'
               '<span class="c-status">status</span>'
               '<span class="c-seq">#</span><span class="c-task">task</span>'
               '<span class="c-cat">category</span><span class="c-eff">effort</span>'
               '<span class="c-act">activity</span></div>')
    # req 7: show the first `see_more_after` rows, fold the rest into a native
    # <details> whose summary reads "see more (N more)" (no JS needed). The filter
    # JS force-opens this when a search/filter is active so matches inside it show.
    if see_more_after is not None and len(tasks) > see_more_after:
        out.extend(_row(t, theme, variant) for t in tasks[:see_more_after])
        rest = tasks[see_more_after:]
        # `data-more` is the collapsed count; the filter JS force-opens this when a
        # search/filter is active (so matching closed rows inside it show) and rewrites
        # the summary to the matching count, restoring "see more (N more)" when cleared.
        out.append('<details class="seemore" id="closed-extra" data-more="%d">'
                   '<summary id="closed-extra-sum">see more (%d more) ▸</summary>%s</details>'
                   % (len(rest), len(rest), "".join(_row(t, theme, variant) for t in rest)))
    else:
        out.extend(_row(t, theme, variant) for t in tasks)
    out.append('</div>')
    return out


def _collect_categories(tasks):
    """Per-category PER-STATE counts across ALL tasks, with the RESOLVED meta
    (tag/label) — which already reflects the user's config overrides because the
    view-model's `tag`/`label` come from the same `categories` source the terminal
    uses (req D). Returns dicts {color,tag,label,count,new,active,closed,overridden}
    sorted by count desc, ties broken by first-seen (canonical) order. The stored
    `open` is counted as `new` (the relabelled per-task state). Tasks with no
    category are skipped."""
    order, meta = [], {}
    for t in tasks:
        c = t.get("color")
        if not c:
            continue
        if c not in meta:
            order.append(c)
            meta[c] = {"color": c, "tag": t.get("tag") or "",
                       "label": t.get("label") or "", "count": 0,
                       "new": 0, "paused": 0, "live": 0, "closed": 0,
                       "overridden": bool(t.get("overridden"))}
        m = meta[c]
        m["count"] += 1
        # tally by the SAME 4-state display status the pills show, so the panel agrees.
        m[_status_display(t)] += 1
    rows = [meta[c] for c in order]
    rows.sort(key=lambda r: (-r["count"], order.index(r["color"])))
    return rows


def _categories_panel(cat_rows, theme=None, variant=None):
    """The bottom Categories panel: a vertical LIST, one row per category that has ≥1
    task. Each row is now an EXPANDABLE <details class="catrow" data-key="cat:<color>">
    (req I) whose SUMMARY is the inline flow reading left-to-right:
      [colored pill = the TAG only]  [CUSTOM marker, only if overridden]  description  counts
    The pill·CUSTOM·description group sits in a FIXED-width .catleft (widest tag+label +
    4ch) so the PER-STATE counts that follow begin at the SAME x on every row — aligned
    just past the longest description, NOT floated to the page edge (req 2). The pill
    (.cchip) wraps ONLY the [TAG] and is sized to its OWN content; the description
    (.clabel) renders to the RIGHT of the pill as plain UNCOLOURED text, never truncated.
    The CUSTOM marker (.cmark) renders ONLY when the user customized the tag/label. The
    expanded .catdetail shows the category's "when to use" guide sentence
    (cats.category_guide) and — ONLY when overridden — a "Default: <dot> [TAG] label"
    line with the SHIPPED default for the slot (req 3). The counts "N new · N paused · N live · N closed"
    are each tinted by their STATUS colour (new=--so, live=--sa, closed=--sc). Pill colours
    come from the per-variant --cat-* vars (toggle-reactive); the inline hexes are the
    resolved-variant no-JS fallback. These rows persist across the refresh via the
    generic details[data-key] handler. Sorted by count desc."""
    if not cat_rows:
        return []
    # a FIXED left column so every row's counts begin at the SAME x (req 2): reserve
    # (widest tag + 1 + widest label, across the SHOWN rows) + 4 chars for the
    # pill+CUSTOM+description group. Because every .catleft has the identical width, the
    # counts line up exactly — just past the longest description, not at the page edge.
    L = max(len(r.get("tag") or "") + 1 + len(r.get("label") or "") for r in cat_rows)
    leftw = L + 4
    items = []
    for r in cat_rows:
        cls = _cat_class(r["color"])
        hi_fb = _highlight_fb(r["color"], theme, variant)
        fg_fb = _readable_fg(hi_fb)
        # the colored pill wraps ONLY the tag, content-sized.
        chip = ('<span class="cchip" style="background:var(--cat-stripe,%s);'
                'color:var(--cat-fg,%s)"><span class="ctag">%s</span></span>'
                % (_e(hi_fb), _e(fg_fb), _e(r["tag"])))
        # CUSTOM marker ONLY when overridden — right of the pill, before the description.
        marker = '<span class="cmark">CUSTOM</span>' if r.get("overridden") else ""
        # the description (label) as plain uncoloured text, to the right of pill + marker.
        label = '<span class="clabel">%s</span>' % _e(r["label"])
        # the pill+CUSTOM+description group in a FIXED-width .catleft so the counts that
        # follow align across every row (req 2).
        left = ('<span class="catleft" style="width:%dch">%s%s%s</span>'
                % (leftw, chip, marker, label))
        # per-state counts after the fixed-width group, each word tinted by its STATUS
        # colour; the separators inherit the dim ccount colour.
        counts = ('<span class="ccount"><span class="cn">%d new</span> · '
                  '<span class="cp">%d paused</span> · '
                  '<span class="ca">%d live</span> · '
                  '<span class="cc">%d closed</span></span>'
                  % (r.get("new", 0), r.get("paused", 0),
                     r.get("live", 0), r.get("closed", 0)))
        # the expanded body (req 3): the "when to use" guide sentence, plus — ONLY when
        # the row is overridden — a "Default: <dot> [TAG] label" line showing the SHIPPED
        # default for this slot (so the user sees what it was before their override). The
        # per-row auto-assignment note and the "Customized (CUSTOM)" line are gone; the
        # one section-level helpnote under <h2> explains auto-assignment once for all.
        guide = ""
        if _cats is not None and hasattr(_cats, "category_guide"):
            try:
                guide = _cats.category_guide(r["color"]) or ""
            except Exception:
                guide = ""
        detail = []
        if guide:
            detail.append("<div>%s</div>" % _e(guide))
        if r.get("overridden") and _cats is not None and hasattr(_cats, "default_tag_label"):
            try:
                d = _cats.default_tag_label(r["color"])
            except Exception:
                d = None
            if d:
                detail.append("<div>Default: %s [%s] %s</div>"
                              % (_e(d.get("dot") or ""), _e(d.get("tag") or ""),
                                 _e(d.get("label") or "")))
        # the row is an expandable <details>; its summary keeps the inline catitem flow
        # (fixed-width pill·CUSTOM·description group · counts) and ALSO carries the
        # .catitem class so the existing inner-span rules apply. data-key persists it.
        items.append('<details class="catrow %s" data-key="cat:%s">'
                     '<summary class="catitem catsum %s">%s%s</summary>'
                     '<div class="catdetail">%s</div></details>'
                     % (cls, _e(r["color"]), cls, left, counts, "".join(detail)))
    # the section note sits to the RIGHT of the header (like Help's "commands & config"),
    # reusing the dim .count styling — no standalone helpnote line under the heading.
    return ['<div class="cats">',
            '<div class="sec"><h2>Categories</h2><span class="count">%d</span>'
            '<span class="count">auto assigned by best fit</span></div>'
            % len(cat_rows),
            '<div class="catlist">%s</div>' % "".join(items),
            '</div>']


def _focus_strip(tasks, org_label=""):
    """The compact top-bar focus strip (Interbrain only): `Everything · my brains ▾ · one
    dropdown per peer alias · <org_label>`. EXACTLY one brain OR person is focused at a
    time, or none (= Everything, the default). Chips carry the `data-focus-*` the behavior
    script reads; focus is persisted to localStorage and filters BOTH table and graph.
    Light-touch: a single chip row, no vertical rail, no horizontal real-estate loss."""
    self_alias = "me"
    self_brains = []
    peer_order, peers = [], {}
    org_present = False
    # F4: derived per-brain counts (open · active) for the dropdown labels, tallied from
    # the same VMs the strip iterates — no extra store read.
    counts = {}

    def _tally(owner, brain, t):
        c = counts.setdefault((owner, brain), [0, 0])
        st = (t.get("status") or "").lower()
        if st != "closed":
            c[0] += 1
        if st == "active":
            c[1] += 1

    for t in tasks:
        if t.get("foreign"):
            o = t.get("owner") or "peer"
            if o not in peers:
                peers[o] = []
                peer_order.append(o)
            b = t.get("brain") or "main"
            if b not in peers[o]:
                peers[o].append(b)
            _tally(o, b, t)
            if t.get("shared_org"):
                org_present = True
        elif t.get("_ib"):
            self_alias = t.get("owner") or self_alias
            b = t.get("brain") or "main"
            if b not in self_brains:
                self_brains.append(b)
            _tally(self_alias, b, t)
            if t.get("_org"):
                org_present = True

    def _cnt(owner, brain):
        c = counts.get((owner, brain))
        if not c:
            return ""
        return '<span class="fxcount" title="%d open · %d active">%d</span>' % (c[0], c[1], c[0])

    chips = ['<button type="button" class="fxchip fx-all active" data-focus-kind="all">'
             'Everything</button>']
    if self_brains:
        opts = ['<button type="button" class="fxopt" data-focus-kind="owner" '
                'data-focus-owner="%s">all my brains</button>' % _e(self_alias)]
        for b in self_brains:
            opts.append('<button type="button" class="fxopt" data-focus-kind="brain" '
                        'data-focus-owner="%s" data-focus-brain="%s">%s%s</button>'
                        % (_e(self_alias), _e(b), _e(b), _cnt(self_alias, b)))
        chips.append('<details class="fxmenu"><summary class="fxchip">my brains ▾'
                     '</summary><div class="fxlist">%s</div></details>' % "".join(opts))
    for o in peer_order:
        opts = ['<button type="button" class="fxopt" data-focus-kind="owner" '
                'data-focus-owner="%s">all of %s</button>' % (_e(o), _e(o))]
        for b in peers[o]:
            opts.append('<button type="button" class="fxopt" data-focus-kind="brain" '
                        'data-focus-owner="%s" data-focus-brain="%s">%s%s</button>'
                        % (_e(o), _e(b), _e(b), _cnt(o, b)))
        chips.append('<details class="fxmenu"><summary class="fxchip">%s ▾</summary>'
                     '<div class="fxlist">%s</div></details>' % (_e(o), "".join(opts)))
    if org_present:
        chips.append('<button type="button" class="fxchip fx-org" data-focus-kind="org">'
                     '%s</button>' % _e(org_label or "Org brain"))
    return ('<div class="focusstrip" id="focus-strip" role="group" aria-label="Focus brain">'
            '<span class="fxlabel">focus</span>%s</div>' % "".join(chips))


def _filters(cat_rows):
    """The top-of-board search + category + status filter controls (req 8). All
    inline; the end-of-body script reads them and toggles row display via the rows'
    data-title/data-cat/data-status/data-search attributes."""
    opts = ['<option value="">all categories</option>']
    for r in cat_rows:
        label = (r["tag"] + " " + r["label"]).strip() or r["color"]
        opts.append('<option value="%s">%s</option>' % (_e(r["color"]), _e(label)))
    # the status filter VALUES are the 4-state DISPLAY status (new/paused/live/closed),
    # matching each row's data-status so the dropdown filters by exactly what the pill
    # shows; the session dropdown filters by each row's data-sess (best session state:
    # running > resumable > none).
    return (
        '<div class="filters">'
        '<input id="board-search" class="fsearch" type="search" '
        'placeholder="search tasks…" aria-label="Search tasks">'
        '<select id="filter-cat" class="fsel" aria-label="Filter by category">%s</select>'
        '<select id="filter-status" class="fsel" aria-label="Filter by status">'
        '<option value="">all statuses</option><option value="new">new</option>'
        '<option value="paused">paused</option><option value="live">live</option>'
        '<option value="closed">closed</option>'
        '</select>'
        '<select id="filter-sess" class="fsel" aria-label="Filter by session state">'
        '<option value="">all sessions</option><option value="running">running</option>'
        '<option value="resumable">resumable</option><option value="none">no session</option>'
        '</select>'
        # req F: a reset that clears the search box + both filters and restores the
        # default view (incl. re-collapsing the closed see-more) — handled in the JS.
        '<button id="filter-reset" class="freset" type="button" '
        'aria-label="Reset filters">reset</button>'
        '</div>' % "".join(opts))


def _command_label(label, bare):
    """The Commands-panel label for a command. When `bare` is False, rewrite the
    LEADING bare token (/todo, /done, /pin) to its /task-station: form, preserving any
    trailing args (e.g. "/todo <n>" → "/task-station:todo <n>"); /task-station:config
    (and any already-namespaced token) is untouched. When True, return the label as-is."""
    if bare:
        return label
    for tok in ("/todo", "/done", "/pin"):
        if label == tok or label.startswith(tok + " "):
            return "/task-station:" + tok[1:] + label[len(tok):]
    return label


def _opt_cell(value, options):
    """The single OPTIONS column for one config row's summary (1.26.0).

    options is None (path rows: --workspace-dirs, --data-dir): there is nothing to
    choose, so just show the current value escaped, plain — NO asterisk, NO bold.

    options present, tokens = the "·"-split choices:
      • value IS one of the tokens (enum/toggle): join the tokens with " · " and render
        the matching one as <strong>TOKEN</strong> (bold marks the current value), the
        rest plain — e.g. `<strong>on</strong> · off`.
      • value is NOT a token (a reported STATE/action whose current isn't a literal
        option — e.g. statusline "provider-only", categories "3/12 (CORE)"): mark the
        current state bold, then show the settable options dimmed —
        `<strong>VALUE</strong> <span class="cdim">· OPTIONS</span>`.

    The match is on the RAW value, so the caller must pass the value BEFORE the
    --tint-theme "value → variant" display concat (so raw "auto" still matches)."""
    value = "" if value is None else str(value)
    if not options:
        return _e(value)
    tokens = [t.strip() for t in str(options).split("·")]
    if value in tokens:
        # the CURRENT token is bold+underlined (accent via .copts strong); every
        # NON-current token is grayed with .cdim (req 4).
        return " · ".join(
            ("<strong>%s</strong>" % _e(t)) if t == value
            else ('<span class="cdim">%s</span>' % _e(t))
            for t in tokens)
    return ('<strong>%s</strong> <span class="cdim">· %s</span>'
            % (_e(value), _e(" · ".join(tokens))))


def _help_panel(commands, config_rows, variant_label, bare=False, interbrain=True):
    """The bottom help: the /todo commands (reused from _COMMANDS_HELP) as a compact
    read-only table + the current config (config.board_rows()) as expandable rows.

    Commands panel: when `bare` is False each command label's LEADING bare token is
    rewritten to the /task-station: form (preserving trailing args); when True the
    labels show as-is. Two dim helptext lines under the panel reflect the bare-cmds
    state, the second always stating that the /task-station: prefix still works.

    Current config panel (1.26.0): a small list of EXPANDABLE rows. A non-expanding
    header labels two columns — "flag" and "options" (with a dim "· bold + underline =
    current" hint) — sharing the SAME column width as the rows so they line up. Each setting is
    a <details class="crow"> (the --reset row is skipped) whose <summary class="crowsum">
    carries the flag and ONE "options" cell built by _opt_cell: every choice is listed,
    and the CURRENT value is bold — for an enum/toggle the matching token
    is `<strong>on</strong>`, while a REPORTED state/action whose current isn't a
    literal option (e.g. statusline "provider-only", categories "3/12 (CORE)") shows
    `<strong>VALUE</strong>` then the settable options dimmed. The options cell is
    computed from the RAW value, BEFORE the `--tint-theme` "value → variant" display
    concat, so an enum like "auto" still matches its token. The expanded body
    (.cdetail) carries, each on its own dim line: a bare `<code>` usage block first
    (the row's explicit `set_with` 6th element, else a derived
    "/task-station:config --flag <hint>" — no "Set with:" label), then a
    "Default: <code>X</code>" line (no trailing period) directly below it when one was
    parsed, then the description with the trailing "(default: X)" stripped, then each
    `extra_lines` entry (the 5th tuple element — accurate usage / state nuance), and
    (for --tint-theme only) a "Resolving to: <variant>" line when a variant_label is
    set. Each row is a
    <details data-key="cfg:<flag>"> so its open state survives the auto-refresh via the
    generic details[data-key] handler. The old bottom legend is gone; path rows
    (options=None) render their value plain and use a `<path>` usage hint."""
    if not (commands or config_rows):
        return []
    out = ['<div class="help">',
           '<div class="sec"><h2>Help</h2><span class="count">commands &amp; config</span></div>',
           '<div class="panels">']
    if commands:
        rows = "".join(
            '<tr><td class="key">%s</td><td class="val">%s</td></tr>'
            % (_e(_command_label(c, bare)), _e(d))
            for c, d in commands)
        # helptext reflecting whether the bare aliases are on — TWO lines (each its
        # own .helpnote div) so the /task-station: prefix statement always stands
        # on its own line, not folded into the first sentence via a parenthetical.
        if bare:
            note = ('<div class="helpnote">bare-cmds is on — <code>/todo</code>, '
                    '<code>/done</code>, <code>/save</code>, <code>/pin</code>, '
                    '<code>/history</code>, <code>/repos</code> work directly.</div>'
                    '<div class="helpnote">The <code>/task-station:</code> prefix also '
                    'always works.</div>')
        else:
            note = ('<div class="helpnote">bare-cmds is off — use the '
                    '<code>/task-station:</code> prefix (shown).</div>'
                    '<div class="helpnote">Enable the short <code>/todo</code>, '
                    '<code>/done</code>, <code>/save</code>, <code>/pin</code>, '
                    '<code>/history</code>, <code>/repos</code> aliases with '
                    '<code>/task-station:config --bare-cmds on</code>.</div>')
        # #444: make the SILENT off-state visible. `--interbrain auto` resolves to off on a
        # single brain with no peer feeds, and the board used to say nothing at all — so the
        # shipped federation work was undiscoverable. One dim line, here in the help panel,
        # keeps the blast radius off the task sections (the interbrain-off parity render).
        # It does not depend on WHY federation is off, so both off renders stay identical.
        if not interbrain:
            note += ('<div class="helpnote">Interbrain federation is off — peer and org '
                     'brains are not shown. Turn it on with '
                     '<code>/todo config --interbrain on</code>.</div>')
        out.append('<div class="panel"><h3>Commands</h3><table class="kv">%s</table>'
                   '%s</div>' % (rows, note))
    if config_rows:
        # a non-expanding header labels the two columns; it shares the .cflag width with
        # each row's summary (.crowsum) so the options column aligns.
        head = '<div class="cfg-head"><span>flag</span><span>options</span></div>'
        crows = []
        for r in config_rows:
            flag = r[0]
            value = r[1] if len(r) > 1 else ""
            options = r[2] if len(r) > 2 else None
            desc = r[3] if len(r) > 3 else ""
            extra_lines = r[4] if len(r) > 4 else None
            set_with = r[5] if len(r) > 5 else None
            if flag == "--reset":
                continue
            # the OPTIONS cell from the RAW value, BEFORE the --tint-theme "value →
            # variant" concat (so raw "auto" still matches its token); the resolved
            # variant is mentioned in the expanded body instead, never the summary.
            opts_cell = _opt_cell(value, options)
            # the expanded body, each entry on its OWN line: (1) the usage <code> block —
            # ALWAYS FIRST; (2) a "Default: <code>X</code>" line when one was parsed,
            # directly below the usage block; (3) the description (trailing "(default: X)"
            # stripped); (4) each extra_lines entry; (5) (--tint-theme only) the resolved
            # variant.
            m = re.search(r"\(default:\s*(.*?)\)\s*$", desc or "")
            desc_body = re.sub(r"\s*\(default:.*\)\s*$", "", desc or "")
            body = []
            # 1. the usage <code> block — derived when set_with is None (tokens joined by
            # " | ", or a "<value>" placeholder for option-less rows). Kept identical to
            # config._derive_set_with so the terminal and HTML boards never drift.
            if set_with:
                cmd = set_with
            elif options:
                cmd = "/task-station:config %s <%s>" % (
                    flag, " | ".join(t.strip() for t in str(options).split("·")))
            else:
                cmd = "/task-station:config %s <value>" % flag
            body.append("<div><code>%s</code></div>" % _e(cmd))
            # 2. the explicit Default line, directly below the usage block.
            if m:
                body.append("<div>Default: <code>%s</code></div>" % _e(m.group(1)))
            # 3. the description.
            if desc_body:
                body.append("<div>%s</div>" % _e(desc_body))
            # 4. each extra_lines entry on its own line (escaped).
            if extra_lines:
                for el in extra_lines:
                    body.append("<div>%s</div>" % _e(el))
            # 5. (--tint-theme only) the resolved variant.
            if flag == "--tint-theme" and variant_label:
                body.append("<div>Resolving to: %s</div>" % _e(variant_label))
            # the namespaced data-key="cfg:<flag>" persists this row's open state across
            # the refresh via the generic details[data-key] handler (req B).
            crows.append(
                '<details class="crow" data-key="cfg:%s"><summary class="crowsum">'
                '<span class="cflag">%s</span><span class="copts">%s</span></summary>'
                '<div class="cdetail">%s</div></details>'
                % (_e(flag.lstrip("-")), _e(flag.lstrip("-")), opts_cell, "".join(body)))
        out.append('<div class="panel"><h3>Configs</h3>%s%s</div>'
                   % (head, "".join(crows)))
    out.append('</div></div>')
    return out


def _theme_init_script(default_variant):
    """A TINY inline script run in <head> BEFORE paint: set documentElement's
    data-theme from the persisted MODE (auto | light | dark) in localStorage, defaulting
    to 'auto' when nothing is stored. 'auto' RESOLVES the variant live from the OS via
    matchMedia('(prefers-color-scheme: dark)'); 'light'/'dark' use the stored variant.
    No external asset.

    It also makes the change-driven reload flicker-free:
      * `history.scrollRestoration="manual"` so the browser doesn't fight our own
        end-of-body scroll restore (see _behavior_script / _SCROLL_KEY); and
      * paints the RESOLVED-variant PAGE background straight onto documentElement so the
        very first frame is the themed colour, not white — killing the repaint flash.
        BOTH page hexes are embedded; the resolved variant (incl. auto's OS pick) chooses
        which so the first frame is correct even before the stylesheet applies.
    Both are best-effort and try/caught so a failure never blocks the theme set."""
    d = default_variant if default_variant in _PAGE else "dark"
    dark_pg = _PAGE["dark"]["page"]
    light_pg = _PAGE["light"]["page"]
    fallback_pg = _PAGE.get(d, _PAGE["dark"])["page"]
    return ('<script>(function(){'
            'try{history.scrollRestoration="manual";}catch(e){}'
            # performance mode (persisted, global pref): set data-perf before paint so the
            # animation-disabling CSS applies on the very first frame. Default 'high'.
            'try{var pf=localStorage.getItem("ts-board-perf");'
            'document.documentElement.setAttribute("data-perf",pf==="low"?"low":"high");}catch(e){}'
            'try{var s=localStorage.getItem(%r);'
            'var mode=(s==="dark"||s==="light"||s==="auto")?s:"auto";'
            'var v=mode==="auto"?((window.matchMedia&&'
            'window.matchMedia("(prefers-color-scheme: dark)").matches)?"dark":"light"):mode;'
            'document.documentElement.setAttribute("data-theme",v);'
            'document.documentElement.style.backgroundColor=(v==="light")?%r:%r;'
            '}catch(e){document.documentElement.setAttribute("data-theme",%r);'
            'try{document.documentElement.style.backgroundColor=%r;}catch(e2){}}'
            '})();</script>'
            % (_THEME_KEY, light_pg, dark_pg, d, fallback_pg))


def _behavior_script(autorefresh=False, rev="", interbrain=False):
    """The end-of-body inline script: (1) the THREE-way theme TOGGLE (auto → light →
    dark → auto) — 'auto' follows the OS via matchMedia and updates live; the chosen
    MODE persists to localStorage so a reload can't reset it; (2) the COPY buttons —
    each .copybtn copies its sibling .cmd to the clipboard (async API with an
    execCommand fallback for file:// pages) (req D); (3) the GENERIC <details>
    PERSISTENCE — EVERY <details data-key> (task rows, config rows, category rows,
    worker sub-details) mirrors its open state to localStorage; (4) the hover SCROLL —
    when a title overflows, scroll the FULL title left at a LINEAR, constant speed and
    SNAP back instantly on mouse-out (req 2); (5) the SEARCH + category + status
    FILTERS — toggle row display from the rows' data-* attributes (req 8); (6) the
    SCROLL-position persistence; (7) the explicit Refresh button (#board-refresh) — sets
    the `ts-auto` flag then reloads (so it restores open rows / scroll / filters), always
    present regardless of autorefresh. All inline, no external asset, no layout shift.

    AUTO vs MANUAL reload: the open-state, search/filters, and scroll restore ONLY when
    the load is a change-driven AUTO reload (this script tagged it via the `ts-auto`
    sessionStorage flag before reloading). A MANUAL browser reload or a freshly-opened
    `/todo board` carries no flag, so it starts CLEAN — all rows collapsed (the stored
    open set is also cleared), at the top of the page, with empty search/filters.

    CHANGE-DRIVEN reload: when `autorefresh` is True the script POLLS the sibling
    `board.rev.js` file on a GENTLE ~10s cadence by appending a fresh <script> that sets
    `window.__TSREV`, and reloads ONLY when that value differs from the embedded
    BOARD_REV (a real data change) — setting `ts-auto` first so the next load restores
    state. A <script> is used, NOT fetch: file:// pages (Safari AND Chrome) BLOCK local
    fetch but DO load local scripts, and a dynamically-loaded subresource does NOT
    trigger the top-level loading bar — so the steady state shows NO periodic reload /
    address-bar bar, and the page reloads ONLY on a real change. To keep even the
    occasional check unobtrusive the poll is GENTLE (1.36.0): a tick does NOTHING (no
    <script> load → no loading bar) while the tab is hidden, the window is unfocused, or
    the user interacted within the last ~2500ms (passive mousemove/scroll/keydown/
    touchstart/wheel listeners stamp `lastAct`); it never polls immediately on load
    (the page is already current as of this render). Cache-busting uses a `?t=`+counter
    query so each poll re-reads; if a browser 404s the query form (onerror) we fall back
    ONCE to a no-query src, and only if THAT also errors do we stop polling
    (clearInterval) and degrade silently to a static snapshot — NEVER a blind reload.
    REFRESH-ON-RETURN (1.40.0): the change-check is factored into a no-guard `loadRev()`
    that both poll() and the return-from-inactivity signals call. The FIRST input after an
    idle gap (> IDLE_MS), a window `focus`, or the tab becoming `visible` again each fire
    an immediate loadRev() — so coming back to the board instantly reloads if (and only if)
    a task changed while you were away, on top of the gentle idle poll.
    When False there is no poll and no timer, so every load is a clean "manual" one."""
    px_per_sec = 55          # constant title-scroll velocity (req 2: linear, no easing)
    rev_js = _e(rev or "")
    # F2 focus strip (Interbrain only — absent when off, so the off render is byte-parity
    # with pre-F1 classic). `focusOk(r)` is spliced into the existing row filter so focus
    # COMPOSES with search/category/status; a focus change dispatches `ts-focus-change`
    # which the graph enhancement listens for.
    _focus_and = "&&focusOk(r)" if interbrain else ""
    _focus_active = "||focus" if interbrain else ""
    _focus_defs = (
        "var FKEY='ts-board-focus';"
        "function rdFocus(){try{var v=JSON.parse(localStorage.getItem(FKEY)||'null');"
        "return (v&&v.kind)?v:null;}catch(e){return null;}}"
        "function wrFocus(f){try{if(f)localStorage.setItem(FKEY,JSON.stringify(f));"
        "else localStorage.removeItem(FKEY);}catch(e){}}"
        "var focus=rdFocus();"
        "function focusOk(r){if(!focus)return true;"
        "var o=r.getAttribute('data-owner')||'',b=r.getAttribute('data-brain')||'';"
        "if(focus.kind==='owner')return o===focus.owner;"
        "if(focus.kind==='brain')return o===focus.owner&&b===focus.brain;"
        "if(focus.kind==='org')return r.getAttribute('data-org')==='1';"
        "return true;}"
    ) if interbrain else ""
    _focus_wire = (
        "var fstrip=document.getElementById('focus-strip');"
        "function paintFocus(){if(!fstrip)return;"
        "var chips=fstrip.querySelectorAll('[data-focus-kind]');"
        "for(var fi=0;fi<chips.length;fi++){var c=chips[fi],"
        "k=c.getAttribute('data-focus-kind'),on=false;"
        "if(!focus){on=(k==='all');}else if(k!=='all'){on=(k===focus.kind"
        "&&(c.getAttribute('data-focus-owner')||'')===(focus.owner||'')"
        "&&(c.getAttribute('data-focus-brain')||'')===(focus.brain||''));}"
        "c.classList.toggle('active',on);}"
        "var men=fstrip.querySelectorAll('.fxmenu');"
        "for(var mi=0;mi<men.length;mi++){men[mi].classList.toggle('active',"
        "!!men[mi].querySelector('[data-focus-kind].active'));}}"
        "var _fxDispatch=false;"
        "function setFocus(f){focus=f;wrFocus(f);paintFocus();apply();"
        "_fxDispatch=true;try{window.dispatchEvent(new CustomEvent('ts-focus-change',{detail:f}));}catch(e){}"
        "_fxDispatch=false;}"
        # F3 graph→strip sync: when the GRAPH navigates it dispatches ts-focus-change; mirror
        # that back into the strip (repaint chips + re-filter the table). _fxDispatch guards
        # the strip's OWN dispatch from re-entering.
        "try{window.addEventListener('ts-focus-change',function(ev){if(_fxDispatch)return;"
        "var f=(ev&&ev.detail)||null;focus=(f&&f.kind)?f:null;wrFocus(focus);paintFocus();apply();});}catch(e){}"
        "if(fstrip){fstrip.addEventListener('click',function(ev){"
        "var b=ev.target&&ev.target.closest?ev.target.closest('[data-focus-kind]'):null;"
        "if(!b)return;var k=b.getAttribute('data-focus-kind');"
        "if(k==='all'){setFocus(null);}else{setFocus({kind:k,"
        "owner:b.getAttribute('data-focus-owner')||'',"
        "brain:b.getAttribute('data-focus-brain')||''});}"
        "var men=b.closest?b.closest('.fxmenu'):null;if(men)men.open=false;});paintFocus();}"
    ) if interbrain else ""
    return (
        "<script>(function(){"
        # --- AUTO vs MANUAL reload detection (req A): read+CLEAR the one-shot `ts-auto`
        # flag this script set just before the 5s auto-reload. isAuto gates every state
        # restore below; a manual reload / fresh open has no flag → clean slate. ---
        "var isAuto=false;try{isAuto=sessionStorage.getItem('ts-auto')==='1';"
        "sessionStorage.removeItem('ts-auto');}catch(e){}"
        # B-TS: rewrite every server-stamped timestamp (.lts[data-ts]) to the VIEWER's LOCAL
        # wall-clock time. data-ts is epoch SECONDS (absolute), so this is tz-correct.
        "try{var lts=document.querySelectorAll('.lts[data-ts]');"
        "for(var qi=0;qi<lts.length;qi++){var le=lts[qi],lv=+le.getAttribute('data-ts');"
        "if(lv){try{le.textContent=new Date(lv*1000).toLocaleString();}catch(x){}}}}catch(e){}"
        # the embedded revision of THIS render — the poll compares board.rev.js against it.
        'var BOARD_REV="' + rev_js + '";'
        + (
            # CHANGE-DRIVEN reload (autorefresh ON): on a GENTLE cadence (~10s) append a
            # fresh <script> whose src is the sibling board.rev.js (NOT fetch — file://
            # Safari/Chrome block local fetch but DO load local scripts; a dynamic
            # subresource doesn't trip the loading bar). On load it sets window.__TSREV; we
            # reload ONLY when that differs from BOARD_REV (a real data change), tagging the
            # reload so the NEXT load restores state. Cache-bust via ?t=counter so each poll
            # re-reads. If the query form errors (a browser that 404s the query as a path),
            # fall back ONCE to the no-query src; if THAT also errors, clearInterval and stop
            # — degrade silently to a static snapshot, NEVER a blind reload.
            #
            # GENTLE poll (1.36.0): a tick does NOTHING — no <script> load, so no loading
            # bar — while the tab is hidden, the window is unfocused, or the user interacted
            # within the last ~2500ms. Passive activity listeners stamp `lastAct`; the gate
            # in poll() skips this tick (waits for the next) when any hold condition is true.
            # We also DON'T poll immediately on load (the page is already current as of this
            # render). Net: the bar appears only occasionally, when the board is idle+focused,
            # and a reload still happens ONLY on a real change.
            # lastAct starts at NOW (not 0) so the first activity right after load isn't
            # mistaken for a return-from-idle. IDLE_MS is the inactivity gap that counts as
            # "was away". loadRev() is the NO-GUARD change-check: append the board.rev.js
            # <script>, compare window.__TSREV to BOARD_REV on load, reload (tagged ts-auto)
            # only on a real change; degrade via onPollError on repeated error.
            "var rc=0,triedPlain=false,pollId,lastAct=Date.now(),IDLE_MS=10000;"
            "function onPollError(){if(!triedPlain){triedPlain=true;}"
            "else if(pollId){clearInterval(pollId);pollId=null;}}"
            "function loadRev(){"
            "var s=document.createElement('script');"
            "s.src=triedPlain?'board.rev.js':('board.rev.js?t='+(rc++));"
            "s.onload=function(){var v=window.__TSREV;"
            "if(s.parentNode)s.parentNode.removeChild(s);"
            "if(typeof v==='string'&&v!==BOARD_REV){"
            "try{sessionStorage.setItem('ts-auto','1');}catch(e){}location.reload();}};"
            "s.onerror=function(){if(s.parentNode)s.parentNode.removeChild(s);onPollError();};"
            "(document.head||document.documentElement).appendChild(s);}"
            # bump() is the RETURN-FROM-IDLE detector: while continuously active `was` stays
            # small (no extra checks); the FIRST input after an idle gap (>IDLE_MS) triggers
            # an immediate loadRev(). Passive activity listeners call bump().
            "function bump(){var was=Date.now()-lastAct;lastAct=Date.now();"
            "if(was>IDLE_MS&&!document.hidden){loadRev();}}"
            "var acts=['mousemove','scroll','keydown','touchstart','wheel'];"
            "for(var ai=0;ai<acts.length;ai++){"
            "try{window.addEventListener(acts[ai],bump,{passive:true});}"
            "catch(e){window.addEventListener(acts[ai],bump);}}"
            # gentle idle poll (fallback): same hold gate as before, then loadRev().
            "function poll(){"
            "if(document.hidden||(document.hasFocus&&!document.hasFocus())"
            "||(Date.now()-lastAct<2500))return;"
            "loadRev();}"
            # return-to-tab/window signals: refocus or tab-visible → check + reload-if-changed.
            "try{document.addEventListener('visibilitychange',function(){"
            "if(document.visibilityState==='visible'){lastAct=Date.now();loadRev();}});}catch(e){}"
            "try{window.addEventListener('focus',function(){"
            "lastAct=Date.now();loadRev();});}catch(e){}"
            "pollId=setInterval(poll,10000);"
            if autorefresh else ""
        ) +
        # --- THREE-way theme toggle: auto -> light -> dark -> auto. 'auto' resolves the
        # variant LIVE from the OS via matchMedia; the chosen MODE (not the resolved
        # variant) persists to localStorage. The button LABEL shows which mode is active
        # ("◑ auto" / "○ light" / "● dark"). A matchMedia 'change' listener
        # re-applies on OS appearance change WHILE the mode is 'auto' (live). ---
        "var b=document.getElementById('theme-toggle');"
        "var MM=window.matchMedia?window.matchMedia('(prefers-color-scheme: dark)'):null;"
        "function readMode(){try{var s=localStorage.getItem('" + _THEME_KEY + "');"
        "return (s==='dark'||s==='light'||s==='auto')?s:'auto';}catch(e){return 'auto';}}"
        "function variantFor(m){return m==='auto'?((MM&&MM.matches)?'dark':'light'):m;}"
        "function modeLabel(m){return m==='auto'?'\\u25D1 auto':(m==='light'?'\\u25CB light':'\\u25CF dark');}"
        "function applyMode(m){var v=variantFor(m);"
        "document.documentElement.setAttribute('data-theme',v);"
        "if(b)b.textContent=modeLabel(m);}"
        "applyMode(readMode());"
        "if(b){b.addEventListener('click',function(){"
        "var cur=readMode();var nx=cur==='auto'?'light':(cur==='light'?'dark':'auto');"
        "try{localStorage.setItem('" + _THEME_KEY + "',nx);}catch(e){}applyMode(nx);});}"
        "if(MM){var onMM=function(){if(readMode()==='auto')applyMode('auto');};"
        "try{MM.addEventListener('change',onMM);}catch(e){try{MM.addListener(onMM);}catch(e2){}}}"
        # --- PERFORMANCE toggle (high ⇄ low): a persisted GLOBAL pref (survives refresh,
        # like the theme). 'low' sets data-perf=low → the CSS disables the breathing +
        # section-open animations, and the graph enhancement (watching data-perf) freezes
        # its physics/auto-rotate and renders on demand. Default high. ---
        "var pf=document.getElementById('perf-toggle');"
        "function readPerf(){try{return localStorage.getItem('ts-board-perf')==='low'?'low':'high';}catch(e){return 'high';}}"
        "function applyPerf(m){document.documentElement.setAttribute('data-perf',m);"
        "if(pf)pf.textContent=m==='low'?'\\u26A1 performance: low':'\\u26A1 performance: high';}"
        "applyPerf(readPerf());"
        "if(pf){pf.addEventListener('click',function(){var nx=readPerf()==='low'?'high':'low';"
        "try{localStorage.setItem('ts-board-perf',nx);}catch(e){}applyPerf(nx);});}"
        # --- the explicit Refresh button (1.36.0): force a full reload on demand
        # (handy when the browser's own reload doesn't pick up the change, or just for
        # convenience). It sets the one-shot `ts-auto` flag first so the reload takes the
        # isAuto path that restores open rows / scroll / filters (same as an auto-reload),
        # then location.reload(). Present + wired regardless of the autorefresh setting. ---
        "var rb=document.getElementById('board-refresh');"
        "if(rb){rb.addEventListener('click',function(){"
        "try{sessionStorage.setItem('ts-auto','1');}catch(e){}location.reload();});}"
        # --- copy buttons (req D): copy the sibling .cmd to the clipboard. Prefer the
        # async clipboard API; fall back to a hidden textarea + execCommand('copy') for
        # file:// pages where the async API is often unavailable. Flash "copied" ~1.2s. ---
        "function copyText(txt){try{if(navigator.clipboard&&navigator.clipboard.writeText){"
        "navigator.clipboard.writeText(txt);return;}}catch(e){}"
        "try{var ta=document.createElement('textarea');ta.value=txt;"
        "ta.style.position='fixed';ta.style.left='-9999px';document.body.appendChild(ta);"
        "ta.focus();ta.select();document.execCommand('copy');document.body.removeChild(ta);}catch(e){}}"
        "var cbs=document.querySelectorAll('.copybtn');"
        "for(var ci=0;ci<cbs.length;ci++){(function(btn){"
        "btn.addEventListener('click',function(){"
        # WS6: a copy button either carries an explicit data-copy (file paths / any text)
        # or copies its sibling code.cmd (Open/Resume/worker commands, as before).
        "var txt=btn.getAttribute('data-copy');"
        "if(txt===null){var code=btn.parentNode.querySelector('code.cmd');"
        "if(!code)return;txt=code.textContent||'';}"
        "copyText(txt||'');"
        "var prev=btn.textContent;btn.textContent='copied';btn.classList.add('copied');"
        "setTimeout(function(){btn.textContent=prev;btn.classList.remove('copied');},1200);});"
        "})(cbs[ci]);}"
        # --- GENERIC persist-across-refresh for EVERY persistable <details> (req B):
        # any <details data-key> — task rows (row:<seq>), config rows (cfg:<flag>),
        # category rows (cat:<color>), worker sub-details (wk:<seq>) — mirrors its open
        # state to localStorage. On load: re-open the saved keys. On every toggle:
        # re-read the set and add/remove this key by d.open, write back. The closed
        # see-more (#closed-extra) carries NO data-key, so it is left to the filter JS.
        # All localStorage access is try/caught; a no-op when zero matches. ---
        "var OK='" + _OPEN_KEY + "';"
        "function rdOpen(){try{var v=JSON.parse(localStorage.getItem(OK)||'[]');"
        "return Array.isArray(v)?v:[];}catch(e){return [];}}"
        "function wrOpen(a){try{localStorage.setItem(OK,JSON.stringify(a));}catch(e){}}"
        # restore the saved open set ONLY on the auto-refresh (req A); on a manual / fresh
        # load leave everything collapsed AND clear the stored set so nothing reopens. The
        # per-row 'toggle' listener is wired ALWAYS (set d.open BEFORE adding it so the
        # restore itself doesn't fire it) so new expansions are saved for the next refresh.
        # expose isAuto to the SEPARATE graph enhancement script (appended later) so it
        # can reset the graph view (mode/rotate/zoom) to defaults on a manual/fresh load.
        "try{window.__TS_ISAUTO=isAuto;}catch(e){}"
        "var oset={};"
        "if(isAuto){var oarr=rdOpen();for(var oi=0;oi<oarr.length;oi++)oset[oarr[oi]]=true;}"
        "else{wrOpen([]);}"
        "var ds=document.querySelectorAll('details[data-key]');"
        # Task ROWS are NOT an accordion — any number may be open at once. The Task Graph
        # (data-key='minigraph') defaults OPEN on every load; every other keyed <details>
        # restores its saved state ONLY on an auto-refresh and defaults collapsed on a
        # manual/fresh load (reset-to-default).
        "function defOpen(k){if(k==='minigraph')return true;return isAuto?!!oset[k]:false;}"
        "for(var di=0;di<ds.length;di++){(function(d){"
        "var k=d.getAttribute('data-key');if(k===null)return;"
        "var isRow=!!(d.classList&&d.classList.contains('row'));"
        "d.open=defOpen(k);"
        "d.addEventListener('toggle',function(){var a=rdOpen(),ix=a.indexOf(k);"
        "if(d.open){if(ix<0)a.push(k);}else{if(ix>=0)a.splice(ix,1);}wrOpen(a);"
        # collapsing a task row RESETS its child sections to their server defaults (an
        # element with data-defopen reopens, everything else closes) and prunes the closed
        # keys from the stored set — so reopening the row shows the default layout, never a
        # stale expansion.
        "if(isRow&&!d.open){var kids=d.querySelectorAll('details'),st=rdOpen();"
        "for(var ki=0;ki<kids.length;ki++){var kd=kids[ki];"
        "kd.open=kd.hasAttribute('data-defopen');"
        "var kk=kd.getAttribute('data-key');if(kk){var kx=st.indexOf(kk);"
        "if(!kd.open&&kx>=0)st.splice(kx,1);else if(kd.open&&kx<0)st.push(kk);}}"
        "wrOpen(st);}});"
        "})(ds[di]);}"
        # --- WS6 live-strip: a task-linked chip (<a class="livechip" href="#task-N">)
        # jumps to AND opens that task's row. Runs AFTER the details-persistence loop
        # above so its d.open=false (fresh load) doesn't re-close the row we just opened.
        # Also handles a direct #task-N in the URL (initial load + hashchange). ---
        "function openTaskRow(hash){if(!hash||hash.indexOf('#task-')!==0)return;"
        "var row=document.getElementById(hash.slice(1));"
        "if(row&&(row.tagName||'').toLowerCase()==='details')row.open=true;}"
        "var lcs=document.querySelectorAll('a.livechip');"
        "for(var li=0;li<lcs.length;li++){(function(a){"
        "a.addEventListener('click',function(){openTaskRow(a.getAttribute('href'));});"
        # B19: hovering a chip highlights its linked task row (add/remove .hl).
        "function hl(on){var r=document.getElementById((a.getAttribute('href')||'').slice(1));"
        "if(r)r.classList.toggle('hl',on);}"
        "a.addEventListener('mouseenter',function(){hl(true);});"
        "a.addEventListener('mouseleave',function(){hl(false);});"
        "})(lcs[li]);}"
        # clickable RELATED-task links (a.rellink, in both the collapsed-row chip and the
        # expanded `related` digest row): open the counterpart's row + scroll to it. When
        # the link sits inside a <summary> (the collapsed-row chip), stop the event so it
        # doesn't ALSO toggle the row the link is inside.
        "var rls=document.querySelectorAll('a.rellink');"
        "for(var rli=0;rli<rls.length;rli++){(function(a){"
        "a.addEventListener('click',function(ev){ev.preventDefault();"
        "if(a.closest&&a.closest('summary'))ev.stopPropagation();"
        "var h=a.getAttribute('href')||'';openTaskRow(h);"
        "var tgt=document.getElementById(h.slice(1));"
        "if(tgt&&tgt.scrollIntoView)tgt.scrollIntoView({block:'nearest'});});"
        "})(rls[rli]);}"
        # (the live-row breathing is now server-baked: the row carries .stat-live when
        # a session is running, driven by the SAME live set — no client detection needed.)
        "window.addEventListener('hashchange',function(){openTaskRow(location.hash);});"
        "openTaskRow(location.hash);"
        # --- hover scroll: LINEAR constant speed out, INSTANT snap-back on leave ---
        # one fixed px/sec rAF (no easing, no velocity ramp); on mouseleave we cancel
        # and reset scrollLeft to 0 immediately — no animated return (req 2).
        "var SPEED=" + str(px_per_sec) + "/1000;"   # px per ms
        # add 'scrolling' (switches to clip + overflow-x:auto) BEFORE measuring: under
        # text-overflow:ellipsis Safari reports scrollWidth==clientWidth (no overflow), so
        # we must measure with the clip active. Bail (removing the class) only if there is
        # truly no overflow.
        # WS6 marquee fix: with the progress/cost chips removed from the Task cell the
        # title regains its width, so re-measure AFTER a rAF tick (the .scrolling clip
        # layout must settle first — Safari otherwise reports no overflow). add('scrolling')
        # stays BEFORE the measure; the no-overflow bail removes the class and returns.
        # text-indent marquee: measure the overflow, slide the title left by exactly that
        # much over a LINEAR transition at SPEED px/ms; snap back instantly on leave.
        "function startScroll(el){var d=el.scrollWidth-el.clientWidth;if(d<=2)return;"
        "el.classList.add('scrolling');"
        "el.style.transitionDuration=(d/(SPEED*1000))+'s';"   # seconds = px / (px per s)
        "void el.offsetWidth;"                                # force reflow so it animates
        "el.style.textIndent=(-d)+'px';}"
        "function stopScroll(el){el.style.transitionDuration='0s';el.style.textIndent='0px';"
        "el.classList.remove('scrolling');}"                  # snap back instantly
        # WS6: bind on the WHOLE row summary (not just .c-task) so hovering ANYWHERE in the
        # row scrolls the title; fall back to el.parentNode where closest() is unavailable.
        "var ts=document.querySelectorAll('.c-task .ttl');"
        "for(var i=0;i<ts.length;i++){(function(el){"
        "var host=(el.closest&&el.closest('summary.rowsum'))||el.parentNode;"
        "host.addEventListener('mouseenter',function(){startScroll(el);});"
        "host.addEventListener('mouseleave',function(){stopScroll(el);});"
        "})(ts[i]);}"
        # --- search + category + status filters (req 8) ---
        "var q=document.getElementById('board-search'),"
        "fc=document.getElementById('filter-cat'),fs=document.getElementById('filter-status'),"
        "fx=document.getElementById('filter-sess'),"
        "rst=document.getElementById('filter-reset'),"
        "extra=document.getElementById('closed-extra'),"
        "esum=document.getElementById('closed-extra-sum'),"
        "rows=document.querySelectorAll('details.row');"
        + _focus_defs +
        "function apply(){var s=(q&&q.value||'').trim().toLowerCase(),"
        "c=fc&&fc.value||'',st=fs&&fs.value||'',sx=fx&&fx.value||'',active=!!(s||c||st||sx"
        + _focus_active + ");"
        # persist the live search + filters so the 5s auto-refresh can restore them (req B);
        # the raw search box value is stored so the box shows exactly what was typed.
        "try{sessionStorage.setItem('ts-board-filters',"
        "JSON.stringify({s:(q&&q.value||''),c:c,st:st,sx:sx}));}catch(e){}"
        "for(var i=0;i<rows.length;i++){var r=rows[i],"
        "ok=(!s||(r.getAttribute('data-search')||'').indexOf(s)>=0)"
        "&&(!c||r.getAttribute('data-cat')===c)"
        "&&(!st||r.getAttribute('data-status')===st)"
        "&&(!sx||(r.getAttribute('data-sess')||'none')===sx)"
        + _focus_and + ";"
        "r.style.display=ok?'':'none';}"
        # the closed "see more" reacts to the filter (req E): filtering runs over EVERY
        # closed row (incl. those inside it), so when a filter is active we force it
        # OPEN and rewrite its summary to the count of matching rows inside it; with no
        # filter it re-collapses and restores "see more (N more)".
        "if(extra){var em=extra.querySelectorAll('details.row'),sh=0;"
        "for(var j=0;j<em.length;j++){if(em[j].style.display!=='none')sh++;}"
        "extra.open=active;"
        "if(esum)esum.textContent=active?(sh+' matching closed \\u25BE')"
        ":('see more ('+(extra.getAttribute('data-more')||'0')+' more) \\u25B8');}}"
        "if(q)q.addEventListener('input',apply);"
        "if(fc)fc.addEventListener('change',apply);"
        "if(fs)fs.addEventListener('change',apply);"
        "if(fx)fx.addEventListener('change',apply);"
        # reset returns to a fully-CLEAN view (req C): clear the search + both filters,
        # COLLAPSE every open details[data-key], clear the stored open set + the saved
        # filters, then re-apply (which re-collapses the closed see-more).
        "if(rst)rst.addEventListener('click',function(){"
        "if(q)q.value='';if(fc)fc.value='';if(fs)fs.value='';if(fx)fx.value='';"
        "for(var ri=0;ri<ds.length;ri++){ds[ri].open=false;}wrOpen([]);"
        "try{sessionStorage.removeItem('ts-board-filters');}catch(e){}apply();});"
        + _focus_wire +
        # restore the saved search + filters ONLY on the auto-refresh (req B), matching the
        # open-state rule (req A); on a manual / fresh load clear them and leave the
        # controls empty. apply() runs LAST here (before scroll restore) so the rows +
        # closed see-more reflect the restored (or empty) filter.
        "try{if(isAuto){var fr=JSON.parse(sessionStorage.getItem('ts-board-filters')||'null');"
        "if(fr){if(q)q.value=fr.s||'';if(fc)fc.value=fr.c||'';if(fs)fs.value=fr.st||'';"
        "if(fx)fx.value=fr.sx||'';}}"
        "else{sessionStorage.removeItem('ts-board-filters');}}catch(e){}apply();"
        # --- SCROLL-POSITION persistence across the opt-in change-driven reload (1.30.0):
        # the reload is a FULL reload, so without this the page snaps to the top.
        # We SAVE window.scrollY continuously — a THROTTLED scroll listener (one write per
        # animation frame via a guard) plus beforeunload + pagehide + when the tab is hidden
        # — to sessionStorage, then RESTORE it here, LAST: this runs AFTER the details open-state
        # restore above (re-opening a <details> changes layout height, so the scroll must
        # be applied once the final height is known) and BEFORE the first paint (this is an
        # end-of-body script), so the page shows already positioned — no visible jump.
        # All access is try/caught and a no-op when the key is absent. ---
        "var SK='" + _SCROLL_KEY + "';"
        "function curY(){return window.scrollY||"
        "(document.scrollingElement||document.documentElement).scrollTop||0;}"
        "function saveScroll(){try{sessionStorage.setItem(SK,String(curY()));}catch(e){}}"
        "var sPend=false;"
        "window.addEventListener('scroll',function(){if(sPend)return;sPend=true;"
        "requestAnimationFrame(function(){sPend=false;saveScroll();});},{passive:true});"
        "window.addEventListener('beforeunload',saveScroll);"
        # Safari fires 'pagehide' more reliably than 'beforeunload' on a reload, so save
        # there too; visibilitychange covers tab-switch / background.
        "window.addEventListener('pagehide',saveScroll);"
        "document.addEventListener('visibilitychange',function(){"
        "if(document.visibilityState==='hidden')saveScroll();});"
        # restore LAST — after the open-state + filter restore changed row visibility /
        # height — and ONLY on the auto-refresh (req A); a manual / fresh load stays at top.
        # The first scrollTo can clamp before the page reaches full height (late layout /
        # image settling), so we RE-APPLY the target across a few frames + timeouts. A
        # `lock` is dropped the moment the user genuinely scrolls (wheel/touch/key), so we
        # never fight a real scroll — we only force the restored position until then.
        "try{if(isAuto){var want=parseInt(sessionStorage.getItem(SK),10),lock=true;"
        "function reapply(){if(lock&&!isNaN(want))window.scrollTo(0,want);}"
        "['wheel','touchmove','keydown'].forEach(function(ev){"
        "window.addEventListener(ev,function(){lock=false;},{passive:true,once:true});});"
        "reapply();requestAnimationFrame(reapply);"
        "setTimeout(reapply,60);setTimeout(reapply,200);}}catch(e){}"
        "})();</script>"
    )


_MG_KIND_CLASS = {"spawned-from": "k-lineage", "related": "k-related",
                  "touches-same": "k-touch", "related-knowledge": "k-knowledge",
                  "membership": "k-membership", "pr": "k-pr", "repo": "k-repo",
                  "story": "k-story", "xbrain": "k-xbrain"}
_MG_KIND_WORD = {"spawned-from": "spawned-from", "related": "related",
                 "touches-same": "touches same", "related-knowledge": "shared knowledge",
                 "xbrain": "cross-brain"}


def _short_hub_label(full):
    """The compact `dot TAG` form of a category-hub label — "🔵 [INFRA] CI/CD, …" →
    "🔵 INFRA" — for the node face + the mg-data `label`. The full taxonomy description
    stays only in the SVG node's `<title>` tooltip. Falls back to the input unchanged."""
    m = re.match(r"\s*(\S+)\s+\[([^\]]+)\]", full or "")
    return ("%s %s" % (m.group(1), m.group(2))) if m else (full or "")


# ---------------------------------------------------------------------------
# F3 galaxy geometry — the PURE, TESTED reference twins of the canvas JS below.
# The live board draws blobs/wells on the <canvas> in _MG_ENHANCE_JS (positions
# are physics-driven, so they can only be computed client-side); these Python
# functions mirror the SAME formulas so the crash-proofing + determinism are
# unit-testable. The 2.0.0 blob crash was hull math on <3 points — every entry
# point here refuses to run hull math on degenerate input (falls back to a
# circle), and the JS twin (drawBlob/jsHull) does the byte-identical thing.
# ---------------------------------------------------------------------------
def _galaxy_well_ring(keys, radius=1.0):
    """Deterministic angular slots for gravity wells: sorted keys → stable angles
    on a ring (angle = -π/2 + 2πi/N), so galaxy layout is identical across renders.
    Returns {key: (x, y)}. The JS gravity uses the IDENTICAL formula, which is why
    distinct keys always land on distinct, reproducible slots (F3.1)."""
    ks = sorted(keys)
    n = len(ks)
    out = {}
    for i, k in enumerate(ks):
        a = -math.pi / 2 + (2 * math.pi * i / n if n else 0.0)
        out[k] = (round(math.cos(a) * radius, 4), round(math.sin(a) * radius, 4))
    return out


def _convex_hull(pts):
    """Monotone-chain convex hull of (x, y) points, CCW. Collinear vertices are
    dropped (the `<= 0` cross test), so a set of collinear points collapses to its
    two endpoints and the caller's `len(hull) < 3` guard sends it to the circle
    fallback — hull SMOOTHING is never fed a degenerate (zero-area) polygon."""
    uniq = sorted(set((round(float(x), 4), round(float(y), 4)) for x, y in pts))
    if len(uniq) < 3:
        return uniq

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in uniq:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(uniq):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _circle_path(cx, cy, r):
    """A closed 4-segment cubic-Bézier circle (the degenerate-group blob) — the
    ONLY blob path used when a group has <3 non-collinear points."""
    k = 0.5522847498307936 * r
    return (("M%.2f %.2f"
             "C%.2f %.2f %.2f %.2f %.2f %.2f"
             "C%.2f %.2f %.2f %.2f %.2f %.2f"
             "C%.2f %.2f %.2f %.2f %.2f %.2f"
             "C%.2f %.2f %.2f %.2f %.2f %.2fZ")
            % (cx + r, cy,
               cx + r, cy + k, cx + k, cy + r, cx, cy + r,
               cx - k, cy + r, cx - r, cy + k, cx - r, cy,
               cx - r, cy - k, cx - k, cy - r, cx, cy - r,
               cx + k, cy - r, cx + r, cy - k, cx + r, cy))


def _smooth_closed_path(pts):
    """Catmull-Rom → cubic-Bézier closed spline through `pts` (a proper hull, ≥3
    points). Returns "" for <3 (never reached from _blob_path, which guards)."""
    m = len(pts)
    if m < 3:
        return ""
    d = ["M%.2f %.2f" % (pts[0][0], pts[0][1])]
    for i in range(m):
        p0, p1, p2, p3 = pts[(i - 1) % m], pts[i], pts[(i + 1) % m], pts[(i + 2) % m]
        c1x, c1y = p1[0] + (p2[0] - p0[0]) / 6.0, p1[1] + (p2[1] - p0[1]) / 6.0
        c2x, c2y = p2[0] - (p3[0] - p1[0]) / 6.0, p2[1] - (p3[1] - p1[1]) / 6.0
        d.append("C%.2f %.2f %.2f %.2f %.2f %.2f" % (c1x, c1y, c2x, c2y, p2[0], p2[1]))
    d.append("Z")
    return "".join(d)


def _blob_path(pts, pad=18.0):
    """A boundary-blob SVG path for a node group, degrading CLEANLY (F3.3):
      * empty group           → {"kind": "empty",  "d": ""}
      * <3 non-collinear pts   → {"kind": "circle", "d": <enclosing circle>}
      * ≥3 pts                 → {"kind": "hull",   "d": <expanded smooth hull>}
    NEVER raises on degenerate input — the whole point of the 2.0.0 regression."""
    pts = [(float(x), float(y)) for x, y in (pts or [])]
    if not pts:
        return {"kind": "empty", "d": ""}
    hull = _convex_hull(pts)
    if len(hull) < 3:
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        r = max([math.hypot(p[0] - cx, p[1] - cy) for p in pts] + [0.0]) + pad + 6.0
        return {"kind": "circle", "d": _circle_path(cx, cy, r)}
    hx = sum(p[0] for p in hull) / len(hull)
    hy = sum(p[1] for p in hull) / len(hull)
    exp = []
    for x, y in hull:
        dx, dy = x - hx, y - hy
        L = math.hypot(dx, dy) or 1.0
        exp.append((x + dx / L * pad, y + dy / L * pad))
    return {"kind": "hull", "d": _smooth_closed_path(exp)}


def _minigraph(graph, theme=None, variant=None, solo_pool=None):
    """Step 1: a small, collapsible, clustered 2D SVG of the task-relation graph. The
    graph (from `build_render_graph`) carries typed STRING ids: task nodes (`t:<seq>`),
    category HUB nodes (`cat:<key>`), and signal HUB nodes (`sig:<kind>:<value>` for a
    shared PR/repo/story). Layout is deterministic and closed-form (NO physics/JS):
    category hubs sit on a ring, each hub's member tasks pack on a short outward arc, and
    signal hubs sit at the centroid of their members. Edges are styled by kind (lineage,
    membership, per-signal, gated shared-knowledge). Task circles are filled
    with the category accent (resolved-variant hex); category hubs are rounded rects sized
    to their label; signal hubs are shaped by kind (pr=diamond, repo=hexagon, story=rect).
    Native SVG `<title>` names the REAL shared signal; the whole graph is also embedded as
    a deterministic `<script type="application/json" id="mg-data">` block for step 2. NO
    script logic and NO external asset.

    Returns [] when the graph has no edges/nodes, so a relation-free board renders exactly
    as before — the panel simply isn't emitted. The single-positional / empty / None call
    (`_minigraph(None)` / `_minigraph({"nodes":[],"edges":[]})`) still yields []. Caps the
    drawn TASK nodes at 40 (highest-degree first, seq tie-break), then prunes signal hubs
    with <2 surviving members and category hubs with <1.

    `solo_pool` (optional): every board task as {seq,title,color,status} — the ones NOT
    drawn above (relation-free, capped-out, or pruned) are embedded in mg-data under
    `"solo"`, seeded on concentric rings OUTSIDE the layout, for the canvas's default-off
    "unlinked tasks" filter. They never join the static SVG and never create a panel on
    their own (panel emission still requires ≥1 edge)."""
    nodes = list((graph or {}).get("nodes") or [])
    edges = list((graph or {}).get("edges") or [])
    if not edges or not nodes:
        return []
    by_id = {n["id"]: n for n in nodes}

    # ---- cap TASK nodes only; then prune hubs left short of members -----------
    task_nodes = [n for n in nodes if n.get("type") == "task"]
    total_tasks = len(task_nodes)
    CAP = 40
    if total_tasks > CAP:
        task_nodes = sorted(
            task_nodes,
            key=lambda n: (-(n.get("deg") or 0),
                           n.get("seq") if n.get("seq") is not None else 1 << 30))[:CAP]
    kept_task_ids = {n["id"] for n in task_nodes}
    hub_members = {}                                   # hub id -> set(kept task id)
    for e in edges:
        tgt = by_id.get(e["b"])
        if e["a"] in kept_task_ids and tgt and tgt.get("type") in ("hub", "signal"):
            hub_members.setdefault(e["b"], set()).add(e["a"])
    kept_ids = set(kept_task_ids)
    for n in nodes:
        if n.get("type") == "hub" and len(hub_members.get(n["id"], ())) >= 1:
            kept_ids.add(n["id"])
        elif n.get("type") == "signal" and len(hub_members.get(n["id"], ())) >= 2:
            kept_ids.add(n["id"])
    draw_edges = [e for e in edges if e["a"] in kept_ids and e["b"] in kept_ids]
    if not draw_edges:
        return []
    draw_nodes = [n for n in nodes if n["id"] in kept_ids]

    # ---- deterministic closed-form layout -------------------------------------
    W, H = 720, 520
    CX, CY = W / 2.0, H / 2.0
    cat_hubs = sorted((n for n in draw_nodes if n.get("type") == "hub"),
                      key=lambda n: n["id"])
    sig_hubs = sorted((n for n in draw_nodes if n.get("type") == "signal"),
                      key=lambda n: n["id"])
    hub_pos, hub_ang, task_pos = {}, {}, {}
    C = len(cat_hubs)
    Rc = 178.0
    for i, h in enumerate(cat_hubs):
        ang = -math.pi / 2 + (2 * math.pi * i / C if C else 0)
        hub_ang[h["id"]] = ang
        hub_pos[h["id"]] = (CX + Rc * math.cos(ang), CY + Rc * math.sin(ang))
    r_task = 60.0
    for h in cat_hubs:
        members = sorted((t for t in hub_members.get(h["id"], ()) if t in kept_task_ids),
                         key=lambda tid: by_id[tid].get("seq") or 0)
        m = len(members)
        base = hub_ang[h["id"]]
        span = min(math.pi * 0.9, 0.42 * m)
        hx, hy = hub_pos[h["id"]]
        for j, tid_ in enumerate(members):
            off = 0.0 if m <= 1 else (j / (m - 1) - 0.5) * span
            a = base + off
            task_pos[tid_] = (hx + r_task * math.cos(a), hy + r_task * math.sin(a))
    free = sorted((n for n in draw_nodes
                   if n.get("type") == "task" and n["id"] not in task_pos),
                  key=lambda n: n.get("seq") or 0)
    F = len(free)
    Ri = 82.0
    for k, n in enumerate(free):
        a = -math.pi / 2 + (2 * math.pi * k / F if F else 0)
        # a lone free task sits dead-centre; otherwise on a small inner ring
        task_pos[n["id"]] = ((CX, CY) if F == 1 else
                             (CX + Ri * math.cos(a), CY + Ri * math.sin(a)))
    sig_pos = {}
    for s in sig_hubs:
        pts = [task_pos[t] for t in hub_members.get(s["id"], ()) if t in task_pos]
        if pts:
            sig_pos[s["id"]] = (sum(p[0] for p in pts) / len(pts),
                                sum(p[1] for p in pts) / len(pts))
        else:
            sig_pos[s["id"]] = (CX, CY)
    pos = {}
    pos.update(task_pos)
    pos.update(hub_pos)
    pos.update(sig_pos)

    def _lbl(nid):
        n = by_id.get(nid) or {}
        if n.get("type") == "task":
            if n.get("foreign"):
                return n.get("handle") or n.get("owner") or nid
            return "#%s" % n.get("seq")
        return n.get("label") or nid

    # ---- SVG: edges under nodes -----------------------------------------------
    svg = ['<svg class="mgsvg" viewBox="0 0 %d %d" role="img" '
           'aria-label="Task Graph">' % (W, H)]
    for e in draw_edges:
        if e["a"] not in pos or e["b"] not in pos:
            continue
        x1, y1 = pos[e["a"]]
        x2, y2 = pos[e["b"]]
        cls = _MG_KIND_CLASS.get(e["kind"], "k-related")
        kind = e["kind"]
        if kind in ("pr", "repo", "story"):
            tip = "%s — shares %s" % (_lbl(e["a"]), _lbl(e["b"]))
        elif kind == "membership":
            tip = "%s — %s" % (_lbl(e["a"]), _lbl(e["b"]))
        else:
            via = [v for v in (e.get("via") or []) if v != "lineage"]
            via_txt = (" (" + ", ".join(via) + ")") if via else ""
            tip = "%s — %s · %s%s" % (_lbl(e["a"]), _lbl(e["b"]),
                                      _MG_KIND_WORD.get(kind, kind), via_txt)
        svg.append('<line class="mg-edge %s" x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f">'
                   '<title>%s</title></line>' % (cls, x1, y1, x2, y2, _e(tip)))

    # ---- SVG: category hubs (rounded rect sized to label) ---------------------
    data_nodes = []
    for h in cat_hubs:
        x, y = hub_pos[h["id"]]
        full = h.get("label") or h.get("key") or ""     # full taxonomy line → SVG title
        short = _short_hub_label(full)                   # "🔵 INFRA" → node face + mg-data
        w = max(46.0, len(short) * 6.4 + 16.0)
        rh = 22.0
        hi = _highlight_fb(h.get("key"), theme, variant)
        fg = _readable_fg(hi)
        svg.append('<g class="mg-hub"><title>%s</title>'
                   '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="7" '
                   'style="fill:%s"></rect>'
                   '<text x="%.1f" y="%.1f" style="fill:%s">%s</text></g>'
                   % (_e(full), x - w / 2, y - rh / 2, w, rh, _e(hi),
                      x, y, _e(fg), _e(short)))
        data_nodes.append({"id": h["id"], "type": "hub", "key": h.get("key"),
                           "label": short, "deg": h.get("deg", 0),
                           "x": round(x, 1), "y": round(y, 1)})

    # ---- SVG: signal hubs (shape by kind) -------------------------------------
    for s in sig_hubs:
        x, y = sig_pos[s["id"]]
        kind = s.get("kind") or "pr"
        label = s.get("label") or ""
        rs = 12.0
        if kind == "repo":
            pts = " ".join("%.1f,%.1f" % (x + rs * math.cos(math.pi / 3 * i),
                                          y + rs * math.sin(math.pi / 3 * i))
                           for i in range(6))
            shape = '<polygon class="mg-sig mg-sig-repo" points="%s"></polygon>' % pts
        elif kind == "story":
            w = max(30.0, len(label) * 5.6 + 12.0)
            shape = ('<rect class="mg-sig mg-sig-story" x="%.1f" y="%.1f" '
                     'width="%.1f" height="18" rx="5"></rect>'
                     % (x - w / 2, y - 9, w))
        else:                                          # pr → diamond
            pts = "%.1f,%.1f %.1f,%.1f %.1f,%.1f %.1f,%.1f" % (
                x, y - rs, x + rs, y, x, y + rs, x - rs, y)
            shape = '<polygon class="mg-sig mg-sig-pr" points="%s"></polygon>' % pts
        ty = y if kind == "story" else y + rs + 8
        svg.append('<g class="mg-signode"><title>%s</title>%s'
                   '<text x="%.1f" y="%.1f">%s</text></g>'
                   % (_e(label), shape, x, ty, _e(label)))
        data_nodes.append({"id": s["id"], "type": "signal", "kind": kind,
                           "label": label, "deg": s.get("deg", 0),
                           "x": round(x, 1), "y": round(y, 1)})

    # ---- SVG: task nodes on top ------------------------------------------------
    for n in sorted((nn for nn in draw_nodes if nn.get("type") == "task"),
                    key=lambda nn: nn.get("seq") or 0):
        x, y = pos[n["id"]]
        foreign = bool(n.get("foreign"))
        hi = _highlight_fb(n.get("color"), theme, variant)
        # F1: foreign nodes are filled with the OWNER colour (never the sole channel — the
        # face text is the owner tag + the tooltip carries the handle), not the category.
        fill = (n.get("owner_color") or hi) if foreign else hi
        fg = _readable_fg(fill)
        closed = " closed" if n.get("status") == "closed" else ""
        fcls = " foreign" if foreign else ""
        r = 8.0 + 1.3 * min(n.get("deg") or 0, 6)
        if foreign:
            face = (n.get("owner") or "")[:3].upper() or "◇"
            tip = "%s · %s" % (n.get("handle") or n.get("owner") or "peer",
                               n.get("title") or "")
        else:
            face = "#%s" % n.get("seq")
            tip = "#%s %s" % (n.get("seq"), n.get("title") or "")
        svg.append('<g class="mg-node%s%s"><title>%s</title>'
                   '<circle cx="%.1f" cy="%.1f" r="%.1f" style="fill:%s"></circle>'
                   '<text x="%.1f" y="%.1f" style="fill:%s">%s</text></g>'
                   % (closed, fcls, _e(tip), x, y, r, _e(fill), x, y, _e(fg),
                      _e(face)))
        d = {"id": n["id"], "type": "task", "seq": n.get("seq"),
             "title": n.get("title", ""), "color": n.get("color"),
             "status": n.get("status"), "deg": n.get("deg", 0),
             "x": round(x, 1), "y": round(y, 1)}
        # F1/F2: owner/brain (present only when Interbrain augmented the graph — so the
        # off render emits the identical mg-data JSON as pre-F1) + foreign face data.
        if n.get("owner"):
            d["owner"] = n.get("owner")
        if n.get("brain"):
            d["brain"] = n.get("brain")
        if foreign:
            d["foreign"] = True
            d["owner_color"] = n.get("owner_color")
            d["handle"] = n.get("handle")
        data_nodes.append(d)
    svg.append('</svg>')

    # ---- solo (undrawn) tasks for the canvas's default-off "unlinked" filter ----
    # Concentric rings outside the layout (Rc=178 + task arc ≈ 240 max), deterministic
    # order (seq asc). Ring capacity grows with circumference so dense stores stay tidy.
    solo_data = []
    drawn_seqs = {n.get("seq") for n in draw_nodes if n.get("type") == "task"}
    pool = [p for p in (solo_pool or [])
            if p.get("seq") is not None and p.get("seq") not in drawn_seqs]
    pool.sort(key=lambda p: p["seq"])
    ring_r, k = 310.0, 0
    for p in pool:
        cap = max(8, int((2 * math.pi * ring_r) / 30))
        if k >= cap:
            ring_r += 36.0
            k = 0
            cap = max(8, int((2 * math.pi * ring_r) / 30))
        a = -math.pi / 2 + 2 * math.pi * k / cap
        solo_data.append({"id": "t:%s" % p["seq"], "seq": p["seq"],
                          "title": p.get("title", ""), "color": p.get("color"),
                          "status": p.get("status"),
                          "x": round(CX + ring_r * math.cos(a), 1),
                          "y": round(CY + ring_r * math.sin(a), 1)})
        k += 1

    # ---- embedded JSON for step 2 (deterministic; </script>-safe) -------------
    data = {"nodes": data_nodes,
            "edges": [{"a": e["a"], "b": e["b"], "kind": e["kind"],
                       "dir": e.get("dir", "none"), "weight": e.get("weight", 1),
                       "via": list(e.get("via") or [])} for e in draw_edges]}
    if solo_data:
        data["solo"] = solo_data
    blob = json.dumps(data, sort_keys=True, separators=(",", ":"))
    blob = blob.replace("<", "\\u003c").replace("&", "\\u0026")
    mgdata = '<script type="application/json" id="mg-data">%s</script>' % blob

    # ---- legend: plain-language labels + node-shape key -----------------------
    legend = ['<div class="mglegend">'
              '<span><i class="mgi-lineage"></i>Lineage</span>'
              '<span><i class="mgi-touch"></i>Shares a PR/repo/story</span>'
              '<span><i class="mgi-cat"></i>Category</span>']
    if any(e["kind"] == "related-knowledge" for e in draw_edges):
        legend.append('<span><i class="mgi-knowledge"></i>shared knowledge</span>')
    legend.append('<span><i class="mgi-sig"></i>Signal hub</span></div>')

    # ---- live-canvas controls + stage (hidden until the enhancement adds .mg-live) ----
    # Defaults are 3D (A4); the enhancement re-reads the persisted ts-board-graph pref.
    # Re-settle is gone (B6) — Reset (↻) resets the camera AND reheats the sim.
    # F3: the boundary-blob toggle (default ON) is emitted ONLY when the graph carries
    # galaxy grouping (owner/brain stamped by Interbrain augmentation). With Interbrain
    # OFF no node has owner/brain, so the button is absent and the controls markup stays
    # byte-identical to pre-F3 — the parity law. The client (_MG_ENHANCE_JS) wires .mgblob.
    has_galaxy = any((n.get("owner") or n.get("brain"))
                     for n in draw_nodes if n.get("type") == "task")
    blob_btn = ('<button type="button" class="mgbtn mgblob" aria-pressed="true">'
                '◍ Blobs</button>') if has_galaxy else ""
    controls = (
        '<div class="mgcontrols">'
        '<span class="mgseg" role="group" aria-label="graph view mode">'
        '<button type="button" class="mg2d" aria-pressed="false">2D</button>'
        '<button type="button" class="mg3d" aria-pressed="true">3D</button></span>'
        '<label class="mgsearch"><span class="mag" aria-hidden="true">⌕</span>'
        '<input class="mgq" type="text" autocomplete="off" '
        'placeholder="Search #seq, title, category, PR/repo/story, status…">'
        '<span class="mgn" aria-live="polite"></span></label>'
        '<button type="button" class="mgbtn mgrotate" aria-pressed="true">'
        '⟳ Auto-rotate</button>'
        '%s'
        '<button type="button" class="mgbtn mgreset">↻ Reset</button>'
        '</div>') % blob_btn
    stage = (
        '<div class="mgstage">'
        '<div class="mgcanvaswrap">'
        '<canvas class="mgcanvas" aria-hidden="true"></canvas>'
        '<div class="mghint" aria-hidden="true"></div></div>'
        '<aside class="mgrail"><div class="mgfilters"></div>'
        '<div class="mginfo"></div></aside>'
        '</div>')
    n_tasks = sum(1 for n in draw_nodes if n.get("type") == "task")
    shown = ""
    if n_tasks < total_tasks:
        shown = (' <span class="mgcount">· showing %d of %d</span>'
                 % (n_tasks, total_tasks))
    return ['<details class="minigraph" data-key="minigraph" open><summary>Task Graph'
            '<span class="mgcount">%d task(s) · %d edge(s)%s</span></summary>'
            '<div class="mgwrap">%s%s%s%s%s</div></details>'
            % (n_tasks, len(draw_edges), shown, "".join(svg), mgdata,
               controls, stage, "".join(legend))]


# The client interaction layer (step 2): a SEPARATE, fully try-caught <script> appended
# LAST in render_html (never inside _behavior_script — the theme toggle / copy / scroll
# must never be endangered). It reads the #mg-data JSON emitted by _minigraph and draws a
# live 2D/3D canvas graph on top of the static SVG (which stays as the no-JS + a11y view).
# Any failure leaves the static SVG + the rest of the board fully working. Colors are
# resolved theme-correctly via getComputedStyle probes (no hardcoded category hex); the
# 2D/3D + auto-rotate prefs persist under localStorage 'ts-board-graph'. Kept free of the
# external-asset needle strings (src="http / <link / @import / url(http / //fonts.).
_MG_ENHANCE_JS = """try{(function(){
  var panel=document.querySelector(".minigraph");
  if(!panel)return;
  var dataEl=panel.querySelector("#mg-data"),canvas=panel.querySelector(".mgcanvas");
  if(!dataEl||!canvas)return;
  var G;try{G=JSON.parse(dataEl.textContent||"{}");}catch(e){return;}
  var rawNodes=(G&&G.nodes)||[],rawEdges=(G&&G.edges)||[],rawSolo=(G&&G.solo)||[];
  if(!rawNodes.length||!rawEdges.length)return;
  var ctx=canvas.getContext&&canvas.getContext("2d");if(!ctx)return;
  var root=document.documentElement;
  // performance mode: 'low' disables continuous animation (physics/auto-rotate/momentum)
  // and renders only on demand. Read live from the board's data-perf attribute.
  var perfLow=root.getAttribute("data-perf")==="low";

  var probeBox=document.createElement("div");probeBox.className="mgprobe";document.body.appendChild(probeBox);
  var catCache={},varCache={};
  function catClass(k){return "cat-"+String(k||"").replace(/[^A-Za-z0-9-]+/g,"-");}
  function rootVar(name){if(name in varCache)return varCache[name];var v=getComputedStyle(root).getPropertyValue(name).trim();varCache[name]=v;return v;}
  function catColor(k){if(k in catCache)return catCache[k];var el=document.createElement("span");el.className=catClass(k);probeBox.appendChild(el);var cs=getComputedStyle(el);var c=(cs.getPropertyValue("--cat-stripe")||"").trim()||(cs.getPropertyValue("--cat-accent")||"").trim()||rootVar("--accent")||"#8a7fb0";catCache[k]=c;return c;}
  var EDGEVAR={membership:"--dim",lineage:"--accent",touch:"--accent",knowledge:"--so",pr:"--mg-pr",repo:"--mg-repo",story:"--mg-story",xbrain:"--dim"};
  var SIGVAR={pr:"--mg-pr",repo:"--mg-repo",story:"--mg-story"};
  function edgeColor(cls){return rootVar(EDGEVAR[cls]||"--dim")||"#888";}
  function sigColor(kind){return rootVar(SIGVAR[kind]||"--accent")||"#888";}

  var CLS={"spawned-from":"lineage","related":"lineage","related-knowledge":"knowledge","membership":"membership","pr":"pr","repo":"repo","story":"story","xbrain":"xbrain"};
  function esc(s){return String(s==null?"":s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");}
  function lbl(n){return n.type!=="task"?(n.label||n.id):(n.foreign?(n.handle||n.owner||n.id):("#"+n.seq));}
  var nodes=[],byId={},catLabels={};
  rawNodes.forEach(function(n){
    var o={id:n.id,type:n.type,deg:n.deg||0,label:n.label||"",sx0:n.x||0,sy0:n.y||0};
    if(n.type==="task"){o.seq=n.seq;o.title=n.title||"";o.cat=n.color;o.status=n.status||"open";o.closed=(n.status==="closed");
      if(n.owner)o.owner=n.owner;if(n.brain)o.brain=n.brain;
      if(n.foreign){o.foreign=true;o.owner_color=n.owner_color||"";o.handle=n.handle||"";}}
    else if(n.type==="hub"){o.key=n.key;catLabels[n.key]=n.label||n.key;}
    else{o.kind=n.kind;}
    o.r=(n.type==="task")?(6+Math.min(o.deg,6)*1.7):(n.type==="hub")?12:6;
    nodes.push(o);byId[o.id]=o;
  });
  // UNDRAWN (unlinked/capped) tasks ride along as STATIC solo nodes: pre-seeded on
  // outer rings, fixed, and excluded from the physics — zero sim cost. Hidden until
  // the default-off "unlinked tasks" filter turns them on.
  rawSolo.forEach(function(n){
    if(byId["t:"+n.seq])return;
    var o={id:"t:"+n.seq,type:"task",solo:true,fixed:true,deg:0,label:"",sx0:n.x||0,sy0:n.y||0,
      seq:n.seq,title:n.title||"",cat:n.color,status:n.status||"open",closed:(n.status==="closed"),r:6};
    nodes.push(o);byId[o.id]=o;
  });
  var edges=[];
  // The fallback class for an unrecognised kind MUST be one that EK still lists, or that
  // edge draws with no filter row and can never be turned off. "lineage" is the generic
  // task<->task class and always has a row; it is also where the coming typed kinds
  // (depends-on / parent / absorbed-by) land until they earn rows of their own.
  rawEdges.forEach(function(e){
    if(!byId[e.a]||!byId[e.b])return;
    var cls=CLS[e.kind]||"lineage",A=byId[e.a],B=byId[e.b],tip;
    if(cls==="membership")tip=lbl(A)+" — in "+B.label;
    else if(cls==="pr"||cls==="repo"||cls==="story")tip=lbl(A)+" — shares "+B.label;
    else tip=lbl(A)+" — "+lbl(B);
    edges.push({a:e.a,b:e.b,cls:cls,kind:e.kind,tip:tip});
  });
  var adj={};nodes.forEach(function(n){adj[n.id]=[];});
  edges.forEach(function(e){adj[e.a].push(e.b);adj[e.b].push(e.a);});

  function mulberry32(a){return function(){a|=0;a=a+0x6D2B79F5|0;var t=Math.imul(a^a>>>15,1|a);t=t+Math.imul(t^t>>>7,61|t)^t;return((t^t>>>14)>>>0)/4294967296;};}
  var SW=720,SH=520,SCL=0.78;
  function seedXY(){var rnd=mulberry32(20260715);nodes.forEach(function(n){n.x=(n.sx0-SW/2)*SCL;n.y=(n.sy0-SH/2)*SCL;n.vx=0;n.vy=0;n.vz=0;});}
  // B7: a deterministic z spread — 3D ONLY. Switching to 3D / Reset-in-3D re-seeds z + reheats
  // so the forces pull nodes into volume. G1: in 2D z must stay 0 (a leftover z spread made
  // the 2D Reset settle messily with overlaps), so 2D uses flattenZ instead.
  function seedZ(){var rz=mulberry32(990911);nodes.forEach(function(n){n.z=n.solo?0:(rz()-0.5)*240;n.vz=0;});}
  function flattenZ(){nodes.forEach(function(n){n.z=0;n.vz=0;});}
  seedXY();seedZ();
  // SPREAD scales the whole layout with node count/complexity so a dense graph breathes
  // out instead of overlapping (rest lengths, repulsion, hub ring all grow ~sqrt(N)).
  // Solo (static) nodes don't join the sim, so they must not inflate it.
  var simN=0;nodes.forEach(function(n){if(!n.solo)simN++;});
  var SPREAD=Math.max(1,Math.sqrt(simN/10));
  var WELLK=0.032;                                 // F3: per-node galaxy-well attraction
  var hubNodes=nodes.filter(function(n){return n.type==="hub";});
  hubNodes.forEach(function(h,i){var a=2*Math.PI*i/Math.max(1,hubNodes.length),tilt=(i%2?1:-1)*70*SPREAD;h.ax=Math.cos(a)*150*SPREAD;h.ay=tilt;h.az=Math.sin(a)*150*SPREAD;});

  var GKEY="ts-board-graph",pref={mode:"3d",autoRotate:true,blobs:true};
  // reset-to-default on a manual/fresh load: only RESTORE the saved view (mode/rotate) on
  // an auto-refresh; a manual/browser refresh starts at the defaults (3D + auto-rotate).
  var isAuto=false;try{isAuto=!!window.__TS_ISAUTO;}catch(e){}
  if(isAuto){try{var sv=JSON.parse(localStorage.getItem(GKEY)||"{}");if(sv&&typeof sv==="object"){if(sv.mode==="2d"||sv.mode==="3d")pref.mode=sv.mode;if(typeof sv.autoRotate==="boolean")pref.autoRotate=sv.autoRotate;if(typeof sv.blobs==="boolean")pref.blobs=sv.blobs;}}catch(e){}}
  function savePref(){try{localStorage.setItem(GKEY,JSON.stringify(pref));}catch(e){}}
  var reduce=window.matchMedia&&window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var mode=pref.mode,autoRotate=reduce?false:pref.autoRotate;
  if(mode!=="3d")flattenZ();                                     // G1: start 2D strictly planar

  function tick(alpha){
    for(var i=0;i<nodes.length;i++)for(var j=i+1;j<nodes.length;j++){
      if(nodes[i].solo||nodes[j].solo)continue;      // solo nodes are static décor — no forces
      var A=nodes[i],B=nodes[j],dx=A.x-B.x,dy=A.y-B.y,dz=A.z-B.z,d2=dx*dx+dy*dy+dz*dz+0.1,d=Math.sqrt(d2);
      var f=90000*SPREAD*SPREAD/d2/d,ux=dx/d,uy=dy/d,uz=dz/d;
      A.vx+=ux*f;A.vy+=uy*f;A.vz+=uz*f;B.vx-=ux*f;B.vy-=uy*f;B.vz-=uz*f;
    }
    edges.forEach(function(e){
      var A=byId[e.a],B=byId[e.b],dx=B.x-A.x,dy=B.y-A.y,dz=B.z-A.z,d=Math.sqrt(dx*dx+dy*dy+dz*dz)+0.01;
      var rest=((e.cls==="membership")?58:(e.cls==="lineage")?86:78)*SPREAD,f=(d-rest)*0.04,ux=dx/d,uy=dy/d,uz=dz/d;
      A.vx+=ux*f;A.vy+=uy*f;A.vz+=uz*f;B.vx-=ux*f;B.vy-=uy*f;B.vz-=uz*f;
    });
    nodes.forEach(function(n){
      if(n.solo)return;                              // static — never simulated
      if(n.type==="hub"){n.vx+=(n.ax-n.x)*0.02;n.vy+=(n.ay-n.y)*0.02;if(mode==="3d")n.vz+=(n.az-n.z)*0.02;}
      // stronger centering gravity keeps weakly-connected nodes from drifting far out
      // (which stretched the bbox + made the fit tiny/scattered) → a compact, balanced
      // equilibrium where every node sits reasonably close to the pack.
      // F3: a task assigned to a galaxy well is pulled to it (per-focus-level gravity);
      // everything else (hubs, and every node when Interbrain is OFF → no wells) keeps the
      // original origin-centering, so the OFF layout is physics-identical — the parity law.
      if(n._well){var wx=(mode==="2d")?n._well.bx:n._well.ax,wy=(mode==="2d")?n._well.by:0,wz=(mode==="2d")?0:n._well.az;
        n.vx+=(wx-n.x)*WELLK;n.vy+=(wy-n.y)*WELLK;n.vz+=(wz-n.z)*WELLK;}
      else{var grav=0.009/SPREAD;n.vx+=(-n.x)*grav;n.vy+=(-n.y)*grav;n.vz+=(-n.z)*grav;}
      if(mode==="2d")n.vz+=(-n.z)*0.22;               // G1: strong flatten keeps 2D planar
      if(!n.fixed){n.x+=n.vx*0.5*alpha;n.y+=n.vy*0.5*alpha;n.z+=n.vz*0.5*alpha;}
      n.vx*=0.9;n.vy*=0.9;n.vz*=0.9;
    });
  }

  // camera: yaw/pitch (3D orbit), yawVel/pitchVel (post-drag momentum), zoom (with
  // dynamic ZMIN/ZMAX set by fitView to the graph size), and panX/panY (screen-space pan,
  // used by 2D drag-to-pan; harmless in 3D). rotateHoldUntil is gone — momentum replaces it.
  var yaw=0.5,pitch=-0.35,focal=560,zoom=1,zoomTarget=1,yawVel=0,pitchVel=0,panX=0,panY=0;
  var ZMIN=0.08,ZMAX=3;
  var pivot={x:0,y:0,z:0},pivotTarget={x:0,y:0,z:0};
  function project(n,cx,cy){
    var px=n.x-pivot.x,py=n.y-pivot.y,pzc=n.z-pivot.z;
    if(mode==="2d")return {sx:cx+px*zoom+panX,sy:cy+py*zoom+panY,scale:zoom,depth:0};
    var cosY=Math.cos(yaw),sinY=Math.sin(yaw),cosX=Math.cos(pitch),sinX=Math.sin(pitch);
    var x1=px*cosY-pzc*sinY,z1=px*sinY+pzc*cosY,y1=py;
    var y2=y1*cosX-z1*sinX,z2=y1*sinX+z1*cosX;
    var pz=focal/(focal+z2)*zoom;
    return {sx:cx+x1*pz+panX,sy:cy+y2*pz+panY,scale:pz,depth:z2};
  }
  function unproject2d(mx,my,cx,cy){return {x:(mx-cx-panX)/zoom+pivot.x,y:(my-cy-panY)/zoom+pivot.y};}
  function settle(iter){for(var i=0;i<(iter||220);i++)tick(1);alpha=0;}
  // FIT the whole graph into view + scale the zoom caps to its size. First RECENTER the
  // layout's centroid to the origin — the settled layout drifts off-centre (often more
  // mass above centre in 3D), which otherwise clips nodes out of the initial fit. Then
  // fit to the ACTUAL half-extent in x AND y separately (not an origin radius), so the
  // whole bounding box is framed regardless of shape. Zoom caps scale with graph size:
  // dense graphs zoom out more, small ones zoom in more.
  function fitView(){
    // fit over the VISIBLE population: hidden solo nodes must not stretch the frame
    // (their outer ring would force a huge zoom-out while invisible).
    // F3: with galaxies active, frame the VISIBLE (focus-filtered) population so each nav
    // level starts framed on its own cluster; OFF keeps the original all-non-solo frame.
    var fitN=nodes.filter(function(n){return hasGalaxy?nodeVisible(n):(!n.solo||filt.solo);});
    var N=fitN.length;if(!N)return;
    var mx=0,my=0,mz=0,i;
    for(i=0;i<N;i++){mx+=fitN[i].x;my+=fitN[i].y;mz+=fitN[i].z;}
    mx/=N;my/=N;mz/=N;
    // shift EVERY node (solo included) by the same delta so relative geometry holds.
    for(i=0;i<nodes.length;i++){nodes[i].x-=mx;nodes[i].y-=my;nodes[i].z-=mz;}
    // half-extent per axis (3D uses the in-depth radius for x/z since orbit mixes them)
    var hx=1,hy=1;
    for(i=0;i<N;i++){var n=fitN[i];
      var ex=(mode==="2d")?Math.abs(n.x):Math.sqrt(n.x*n.x+n.z*n.z);
      if(ex>hx)hx=ex;if(Math.abs(n.y)>hy)hy=Math.abs(n.y);}
    var spanX=hx*2+64,spanY=hy*2+64;
    var base=Math.min((Wc||600)/spanX,(Hc||520)/spanY);   // fit BOTH axes → all nodes in view
    ZMIN=Math.max(0.05,base*0.5);ZMAX=Math.max(2.6,base*6);   // zoom-out cap tightened (was 0.04/0.35)
    zoom=Math.max(ZMIN,Math.min(ZMAX,base));zoomTarget=zoom;panX=0;panY=0;
  }

  var DPR=Math.max(1,window.devicePixelRatio||1),Wc=0,Hc=0;
  function resize(){var r=canvas.getBoundingClientRect();Wc=r.width||600;Hc=r.height||520;canvas.width=Math.max(1,Math.round(Wc*DPR));canvas.height=Math.max(1,Math.round(Hc*DPR));ctx.setTransform(DPR,0,0,DPR,0,0);}
  window.addEventListener("resize",function(){resize();draw();});
  // Keep the drawing buffer synced to ANY layout-driven canvas resize (filter rows
  // toggling, rail reflow) — not just window resizes — so the render never smears or
  // jumps against a stale buffer size.
  if(window.ResizeObserver){try{new ResizeObserver(function(){resize();draw();}).observe(canvas);}catch(e){}}

  function hexPath(r){ctx.beginPath();for(var i=0;i<6;i++){var a=Math.PI/6+i*Math.PI/3;ctx[i?"lineTo":"moveTo"](Math.cos(a)*r,Math.sin(a)*r);}ctx.closePath();}
  function rrCtr(w,h,r){var x=-w/2,y=-h/2;ctx.beginPath();ctx.moveTo(x+r,y);ctx.arcTo(x+w,y,x+w,y+h,r);ctx.arcTo(x+w,y+h,x,y+h,r);ctx.arcTo(x,y+h,x,y,r);ctx.arcTo(x,y,x+w,y,r);ctx.closePath();}
  function rr(x,y,w,h,r){ctx.beginPath();ctx.moveTo(x+r,y);ctx.arcTo(x+w,y,x+w,y+h,r);ctx.arcTo(x+w,y+h,x,y+h,r);ctx.arcTo(x,y+h,x,y,r);ctx.arcTo(x,y,x+w,y,r);ctx.closePath();}
  function drawSignal(p,kind,r,fill){
    ctx.fillStyle=fill;ctx.strokeStyle=rootVar("--page")||"#111";ctx.lineWidth=1.4*p.scale;
    ctx.save();ctx.translate(p.sx,p.sy);
    if(kind==="pr"){ctx.rotate(Math.PI/4);ctx.fillRect(-r,-r,r*2,r*2);ctx.strokeRect(-r,-r,r*2,r*2);}
    else if(kind==="repo"){hexPath(r);ctx.fill();ctx.stroke();}
    else{rrCtr(r*2.1,r*1.5,4*p.scale);ctx.fill();ctx.stroke();}
    ctx.restore();
  }

  var EDGEW={membership:1,lineage:2.1,pr:2.4,repo:1.6,story:1.8,touch:1.6,knowledge:1.8};
  // ---- filter state (C2): all-on by default, EXCEPT solo (unlinked tasks) which is
  // default-OFF; status is the task LIFECYCLE (open/active/closed), all-on. ----
  var filt={cat:{},sig:{},edge:{},status:{},solo:false};
  var hover=null,selected=null,screenPos={},query="",terms=[];
  // F2: the shared focus (one brain / person / org, or none) drives BOTH the table and
  // the graph. Read from the same localStorage key the focus strip writes; a focused
  // task node keeps its category/signal/cat hubs but hides tasks of other owners/brains.
  // gated on the strip's presence so a stale focus key can NEVER filter the graph when
  // Interbrain is off (the strip is absent then) — keeps the off render behavior-neutral.
  var gfocus=null;try{if(document.getElementById("focus-strip")){var _gf=JSON.parse(localStorage.getItem("ts-board-focus")||"null");gfocus=(_gf&&_gf.kind)?_gf:null;}}catch(e){}
  function focusOkNode(n){
    if(!gfocus)return true;
    if(n.type!=="task")return true;              // hubs/signals stay; edges prune via endpoints
    var o=n.owner||"",b=n.brain||"";
    if(gfocus.kind==="owner")return o===gfocus.owner;
    if(gfocus.kind==="brain")return o===gfocus.owner&&b===gfocus.brain;
    if(gfocus.kind==="org")return !!n.foreign;   // graph org bucket ≈ shared foreign nodes
    return true;
  }
  function nodeVisible(n){
    if(n.type==="task"){
      if(n.solo&&!filt.solo)return false;
      if(!focusOkNode(n))return false;
      if(filt.status[n.status]===false)return false;
      return filt.cat[n.cat]!==false;
    }
    // a category hub is a RENDERING of its category, not a separate thing to switch:
    // hiding the category hides its hub. filt.cat only carries categories present among
    // the drawn tasks, so a hub whose category has no visible task reads `undefined`,
    // and undefined!==false stays TRUE — fail-visible, deliberately, not fail-hidden.
    if(n.type==="hub")return filt.cat[n.key]!==false;
    return filt.sig[n.kind]!==false;             // per signal KIND (all pr / repo / story hubs together)
  }
  function edgeVisible(e){return filt.edge[e.cls]!==false&&nodeVisible(byId[e.a])&&nodeVisible(byId[e.b]);}
  // C1: robust multi-term (space AND) match across seq / title / category label /
  // signal label / status.
  function matches(n){
    if(!terms.length)return true;
    var hay;
    if(n.type==="task")hay=("#"+n.seq+" "+n.seq+" "+(n.title||"")+" "+(catLabels[n.cat]||n.cat||"")+" "+(n.status||"")).toLowerCase();
    else hay=((n.label||"")+" "+(n.kind||"")+" "+(n.type||"")).toLowerCase();
    for(var i=0;i<terms.length;i++){if(hay.indexOf(terms[i])<0)return false;}
    return true;
  }
  function keepSet(n){if(!n)return null;var s={};s[n.id]=1;(adj[n.id]||[]).forEach(function(id){s[id]=1;});return s;}
  function nodeOn(n,foc,kp){if(!nodeVisible(n))return false;if(query&&!matches(n))return false;if(foc&&kp&&!kp[n.id])return false;return true;}

  // ---- F3: hierarchical galaxy nav — gravity wells, hover outline, boundary blobs -------
  // EVERYTHING here is inert unless the graph carries owner/brain grouping (Interbrain ON)
  // AND the focus strip exists — so with Interbrain OFF hasGalaxy is false, no well is
  // assigned, no blob/outline is drawn, and the graph behaves EXACTLY as before (parity).
  var hasGalaxy=false;try{hasGalaxy=!!document.getElementById("focus-strip")&&nodes.some(function(n){return n.type==="task"&&(n.owner||n.brain);});}catch(e){}
  var navApplying=false,blobPhase=0,blobsOn=(pref.blobs!==false),brainWells={},personWells={};
  function buildWells(){
    brainWells={};personWells={};if(!hasGalaxy)return;
    var keys=[],seen={},pbrains={};
    nodes.forEach(function(n){if(n.type!=="task"||n.solo)return;var o=n.owner||"",b=n.brain||"main",k=o+"\\u0000"+b;
      if(!seen[k]){seen[k]=1;keys.push(k);}(pbrains[o]=pbrains[o]||{})[k]=1;});
    keys.sort();var K=keys.length,RW=(120+34*K)*SPREAD;
    // deterministic ring — angle=-π/2+2πi/K over SORTED keys (mirrors _galaxy_well_ring),
    // radius grows with #galaxies so clusters read as distinct galaxies with clear gaps.
    keys.forEach(function(k,i){var a=-Math.PI/2+(K?2*Math.PI*i/K:0);
      brainWells[k]={ax:Math.cos(a)*RW,az:Math.sin(a)*RW,bx:Math.cos(a)*RW,by:Math.sin(a)*RW};});
    // a person's well = centroid of their brains' wells (F3.1).
    Object.keys(pbrains).forEach(function(o){var ks=Object.keys(pbrains[o]),sx=0,sz=0,bx=0,by=0;
      ks.forEach(function(k){var w=brainWells[k];sx+=w.ax;sz+=w.az;bx+=w.bx;by+=w.by;});
      var m=ks.length||1;personWells[o]={ax:sx/m,az:sz/m,bx:bx/m,by:by/m};});
  }
  function galaxyLevel(f){if(f===undefined)f=gfocus;if(!f)return "interbrain";
    if(f.kind==="owner")return "person";if(f.kind==="brain")return "brain";return "interbrain";}
  // assign each task its gravity well for the CURRENT level: interbrain → the PERSON well
  // (a person's brains merge into one galaxy), person/brain → the BRAIN well (brains split
  // into their own galaxies). Every galaxy stays parked at a stable ring slot regardless of
  // level; only the camera, focus filter, and blobs change — hidden galaxies never pile up.
  function assignWells(){buildWells();var lv=galaxyLevel();
    nodes.forEach(function(n){
      if(!hasGalaxy||n.type!=="task"||n.solo){n._well=null;return;}
      var o=n.owner||"",b=n.brain||"main";
      n._well=(lv==="interbrain")?(personWells[o]||null):(brainWells[o+"\\u0000"+b]||null);});}
  function levelMembers(pv,lv){var pts=[];
    nodes.forEach(function(n){if(n.type!=="task"||n.solo||!nodeVisible(n)||!n._p)return;var take;
      if(lv==="interbrain")take=((n.owner||"")===(pv.owner||""));
      else if(lv==="person")take=((n.owner||"")===(pv.owner||"")&&(n.brain||"main")===(pv.brain||"main"));
      else take=(n===pv);
      if(take)pts.push([n._p.sx,n._p.sy]);});return pts;}
  function galaxyColor(n){return (n.foreign&&n.owner_color)?n.owner_color:(rootVar("--accent")||"#c99b5a");}
  // F3.4: an edge whose endpoints live in DIFFERENT galaxies (at the current level) is
  // drawn curved (quadratic, bowed) so it visibly SPANS the gap; within-galaxy edges stay
  // straight. Inert when Interbrain is OFF (crossGalaxy → false → straight, as before).
  function galaxyKey(n){if(!n||n.type!=="task")return null;
    return (galaxyLevel()==="interbrain")?(n.owner||""):((n.owner||"")+"\\u0000"+(n.brain||"main"));}
  function crossGalaxy(a,b){if(!hasGalaxy)return false;var ka=galaxyKey(a),kb=galaxyKey(b);
    return ka!=null&&kb!=null&&ka!==kb;}
  // monotone-chain hull — twin of _convex_hull: collinear dropped via <=0, so a degenerate
  // group collapses to <3 and the caller falls back to a circle (NEVER hull math on <3).
  function jsHull(ps){var u=[],seen={},i;
    for(i=0;i<ps.length;i++){var kk=ps[i][0].toFixed(2)+","+ps[i][1].toFixed(2);if(!seen[kk]){seen[kk]=1;u.push([ps[i][0],ps[i][1]]);}}
    u.sort(function(a,b){return a[0]-b[0]||a[1]-b[1];});if(u.length<3)return u;
    function cr(o,a,b){return (a[0]-o[0])*(b[1]-o[1])-(a[1]-o[1])*(b[0]-o[0]);}
    var lo=[],up=[];
    for(i=0;i<u.length;i++){while(lo.length>=2&&cr(lo[lo.length-2],lo[lo.length-1],u[i])<=0)lo.pop();lo.push(u[i]);}
    for(i=u.length-1;i>=0;i--){while(up.length>=2&&cr(up[up.length-2],up[up.length-1],u[i])<=0)up.pop();up.push(u[i]);}
    return lo.slice(0,-1).concat(up.slice(0,-1));}
  // draw a smoothed boundary blob (Catmull-Rom→Bézier closed); <3 pts → a CIRCLE. Per-frame
  // try/caught so a single bad frame can NEVER kill the animation loop (defence in depth).
  function drawBlob(ps,color,fillA,strokeA,lw,pad){try{if(!ps.length)return;var h=jsHull(ps),i;
    ctx.save();ctx.beginPath();
    if(h.length<3){var cx=0,cy=0;for(i=0;i<ps.length;i++){cx+=ps[i][0];cy+=ps[i][1];}cx/=ps.length;cy/=ps.length;
      var r=0;for(i=0;i<ps.length;i++){var dd=Math.hypot(ps[i][0]-cx,ps[i][1]-cy);if(dd>r)r=dd;}
      ctx.arc(cx,cy,r+pad+6,0,7);}
    else{var hx=0,hy=0;for(i=0;i<h.length;i++){hx+=h[i][0];hy+=h[i][1];}hx/=h.length;hy/=h.length;
      var e=[];for(i=0;i<h.length;i++){var dx=h[i][0]-hx,dy=h[i][1]-hy,L=Math.hypot(dx,dy)||1;e.push([h[i][0]+dx/L*pad,h[i][1]+dy/L*pad]);}
      var m=e.length;ctx.moveTo(e[0][0],e[0][1]);
      for(i=0;i<m;i++){var p0=e[(i-1+m)%m],p1=e[i],p2=e[(i+1)%m],p3=e[(i+2)%m];
        ctx.bezierCurveTo(p1[0]+(p2[0]-p0[0])/6,p1[1]+(p2[1]-p0[1])/6,p2[0]-(p3[0]-p1[0])/6,p2[1]-(p3[1]-p1[1])/6,p2[0],p2[1]);}}
    ctx.closePath();ctx.lineJoin="round";
    if(fillA){ctx.globalAlpha=fillA;ctx.fillStyle=color;ctx.fill();}
    if(strokeA){ctx.globalAlpha=strokeA;ctx.strokeStyle=color;ctx.lineWidth=lw||2;ctx.stroke();}
    ctx.restore();ctx.globalAlpha=1;}catch(e){try{ctx.restore();ctx.globalAlpha=1;}catch(e2){}}}
  // persistent per-group boundary blobs at the current level (F3.3): one per person's
  // system (interbrain) / one per brain (person). None at brain/node level.
  function drawGalaxyBlobs(){if(!hasGalaxy||!blobsOn)return;var lv=galaxyLevel();if(lv!=="interbrain"&&lv!=="person")return;
    var g={};nodes.forEach(function(n){if(n.type!=="task"||n.solo||!nodeVisible(n)||!n._p)return;
      var key=(lv==="interbrain")?(n.owner||""):((n.owner||"")+"\\u0000"+(n.brain||"main"));
      if(!g[key])g[key]={pts:[],color:galaxyColor(n)};g[key].pts.push([n._p.sx,n._p.sy]);});
    Object.keys(g).sort().forEach(function(k){drawBlob(g[k].pts,g[k].color,0.09,0.5,2,20);});}
  // hover affordance: a soft rounded (spherical) outline around the hovered entity's
  // members; reduced-motion = static (no pulse). Drawn ON TOP so it reads as clickable.
  function drawHoverOutline(){if(!hasGalaxy||!hover||hover.type!=="task")return;
    var lv=galaxyLevel(),pts=levelMembers(hover,lv);if(!pts.length)return;
    var cx=0,cy=0,i;for(i=0;i<pts.length;i++){cx+=pts[i][0];cy+=pts[i][1];}cx/=pts.length;cy/=pts.length;
    var r=0;for(i=0;i<pts.length;i++){var d=Math.hypot(pts[i][0]-cx,pts[i][1]-cy);if(d>r)r=d;}
    var pr=(reduce||perfLow)?0:Math.sin(blobPhase*0.11)*3,color=galaxyColor(hover);
    try{ctx.save();ctx.beginPath();ctx.arc(cx,cy,r+22+pr,0,7);
      ctx.globalAlpha=0.08;ctx.fillStyle=color;ctx.fill();
      ctx.globalAlpha=0.85;ctx.strokeStyle=color;ctx.lineWidth=2.5;ctx.setLineDash([]);ctx.stroke();
      ctx.restore();ctx.globalAlpha=1;}catch(e){try{ctx.restore();ctx.globalAlpha=1;}catch(e2){}}}
  // reframe on a level change: reuse the WORKING fit-view/zoom-cap camera (no new FOV — 3D
  // starts framed, never engulfed), then animate the zoom in/out for the descend/ascend.
  function frameLevel(dir){selected=null;renderInfo(null);yawVel=0;pitchVel=0;
    assignWells();settle(200);fitView();
    // fitView leaves zoom==zoomTarget (framed). Reduced-motion / perf snap; otherwise start
    // offset so step() eases the zoom in (descend) or out (ascend) toward the framed target.
    if(!reduce&&!perfLow){
      if(dir==="down")zoom=Math.max(ZMIN,zoomTarget*0.62);
      else if(dir==="up")zoom=Math.min(ZMAX,zoomTarget*1.6);}
    updateHint();draw();kick();}
  // set the shared focus FROM the graph → persist it + notify the strip (graph→strip sync).
  function graphSetFocus(f,dir){gfocus=(f&&f.kind)?f:null;
    try{if(gfocus)localStorage.setItem("ts-board-focus",JSON.stringify(gfocus));else localStorage.removeItem("ts-board-focus");}catch(e){}
    navApplying=true;try{window.dispatchEvent(new CustomEvent("ts-focus-change",{detail:gfocus}));}catch(e){}navApplying=false;
    frameLevel(dir);}
  // descend one level by clicking an entity (F3.2): interbrain→person, person→brain.
  // Returns true when it consumed the click; brain/node level returns false → node select.
  function navDescend(n){if(n.type!=="task")return false;var lv=galaxyLevel();
    if(lv==="interbrain"){if(n.owner!=null){graphSetFocus({kind:"owner",owner:n.owner,brain:""},"down");return true;}return false;}
    if(lv==="person"){graphSetFocus({kind:"brain",owner:n.owner||"",brain:n.brain||"main"},"down");return true;}
    return false;}
  // ascend one level on empty-canvas click: node→brain→person→interbrain; top = reframe.
  function navAscend(){if(selected){selected=null;renderInfo(null);reheat(0.4);return;}
    var lv=galaxyLevel();
    if(lv==="brain")graphSetFocus({kind:"owner",owner:gfocus.owner,brain:""},"up");
    else if(lv==="person")graphSetFocus(null,"up");
    else if(gfocus&&gfocus.kind==="org")graphSetFocus(null,"up");
    else frameLevel("up");}

  function render(){
    ctx.clearRect(0,0,Wc,Hc);
    var cx=Wc/2,cy=Hc/2,foc=(hover||selected),kp=keepSet(foc),mono=rootVar("--mono")||"monospace";
    nodes.forEach(function(n){var p=project(n,cx,cy);n._p=p;screenPos[n.id]=p;});
    drawGalaxyBlobs();                                          // F3: boundary blobs UNDER edges/nodes
    var eord=edges.slice().sort(function(a,b){return (byId[b.a]._p.depth+byId[b.b]._p.depth)-(byId[a.a]._p.depth+byId[a.b]._p.depth);});
    eord.forEach(function(e){
      if(!edgeVisible(e))return;
      var A=byId[e.a]._p,B=byId[e.b]._p,on;
      if(foc)on=(e.a===foc.id||e.b===foc.id);
      else if(query)on=(matches(byId[e.a])&&matches(byId[e.b]));
      else on=true;
      var fog=Math.max(.12,Math.min(1,(A.scale+B.scale)/2));
      ctx.globalAlpha=(on?0.82:0.05)*fog;
      ctx.strokeStyle=edgeColor(e.cls);ctx.lineWidth=(EDGEW[e.cls]||1.4)*((A.scale+B.scale)/2);
      ctx.setLineDash(e.cls==="repo"?[5,4]:e.cls==="story"?[2,4]:e.cls==="knowledge"?[1,4]:e.cls==="xbrain"?[6,4]:[]);
      ctx.beginPath();ctx.moveTo(A.sx,A.sy);
      if(crossGalaxy(byId[e.a],byId[e.b])){var mx=(A.sx+B.sx)/2,my=(A.sy+B.sy)/2,dx=B.sx-A.sx,dy=B.sy-A.sy,L=Math.hypot(dx,dy)||1,bow=Math.min(60,L*0.22);
        ctx.quadraticCurveTo(mx-dy/L*bow,my+dx/L*bow,B.sx,B.sy);}
      else ctx.lineTo(B.sx,B.sy);
      ctx.stroke();
    });
    ctx.setLineDash([]);
    var nord=nodes.slice().sort(function(a,b){return b._p.depth-a._p.depth;});
    nord.forEach(function(n){
      if(!nodeVisible(n))return;                                   // C2: filtered out → hidden
      var p=n._p,on=nodeOn(n,foc,kp),fog=Math.max(.16,Math.min(1,p.scale)),r=n.r*p.scale;
      ctx.globalAlpha=(on?1:0.1)*(n.closed?0.55:1);
      if(n.type==="task"){
        var fill=(n.foreign&&n.owner_color)?n.owner_color:catColor(n.cat);
        ctx.fillStyle=fill;ctx.beginPath();ctx.arc(p.sx,p.sy,r,0,7);ctx.fill();
        ctx.strokeStyle=rootVar("--page")||"#111";ctx.lineWidth=1.5*p.scale;ctx.stroke();
        if(r>=6.5){                                                // B9: fit the label to the ball
          var t=n.foreign?((n.owner||"").slice(0,3).toUpperCase()||"◇"):("#"+n.seq),fs=Math.min(r*1.15,13);
          ctx.font="600 "+fs.toFixed(1)+"px "+mono;
          var tw=ctx.measureText(t).width,maxw=r*1.72;
          if(tw>maxw){fs=Math.max(5,fs*maxw/tw);ctx.font="600 "+fs.toFixed(1)+"px "+mono;}
          if(fs>=5){
            // ONE label colour per theme (--ink), fill only — no stroke/halo (step 5 §1).
            ctx.globalAlpha=Math.min(1,(on?1:0.1)+0.15);ctx.textAlign="center";ctx.textBaseline="middle";
            ctx.fillStyle=rootVar("--ink")||"#eee";ctx.fillText(t,p.sx,p.sy);
          }
        }
      }else if(n.type==="hub"){
        var fs2=Math.max(9,10.5*p.scale);ctx.font="700 "+fs2.toFixed(0)+"px "+mono;
        var lw=ctx.measureText(n.label).width,padx=11*p.scale,w=lw+padx*2,h=fs2+11*p.scale;
        ctx.fillStyle=rootVar("--panel")||"#222";ctx.strokeStyle=catColor(n.key);ctx.lineWidth=2.2*p.scale;
        rr(p.sx-w/2,p.sy-h/2,w,h,6*p.scale);ctx.fill();ctx.stroke();
        ctx.globalAlpha=Math.min(1,fog+0.25)*(on?1:0.35);ctx.textAlign="center";ctx.textBaseline="middle";
        ctx.fillStyle=rootVar("--ink")||"#eee";ctx.fillText(n.label,p.sx,p.sy);
      }else{
        drawSignal(p,n.kind,r,sigColor(n.kind));
        if(on){ctx.globalAlpha=Math.min(1,fog+0.25);ctx.textAlign="center";ctx.textBaseline="middle";ctx.fillStyle=sigColor(n.kind);ctx.font="600 "+Math.max(8,9.5*p.scale).toFixed(0)+"px "+mono;ctx.fillText(n.label,p.sx,p.sy+r+10*p.scale);}
      }
    });
    drawHoverOutline();                                        // F3: hover affordance ON TOP
    ctx.globalAlpha=1;
  }

  var alpha=0,running=false;
  function moving(){
    if(dragging)return true;
    if(alpha>0.004&&!perfLow)return true;
    if(!perfLow&&(Math.abs(yawVel)>0.0004||Math.abs(pitchVel)>0.0004))return true;
    if(mode==="3d"&&autoRotate&&!perfLow)return true;
    if(Math.abs(zoomTarget-zoom)>0.002)return true;         // F3: animated level-zoom
    if(hasGalaxy&&hover&&!reduce&&!perfLow)return true;     // F3: pulse the hover outline
    return Math.abs(pivotTarget.x-pivot.x)>0.4||Math.abs(pivotTarget.y-pivot.y)>0.4
        ||Math.abs(pivotTarget.z-pivot.z)>0.4;
  }
  function step(){
    if(alpha>0.004&&!perfLow){tick(alpha);alpha*=0.992;}
    pivotTarget.x=selected?selected.x:0;pivotTarget.y=selected?selected.y:0;pivotTarget.z=selected?selected.z:0;
    var e=perfLow?1:0.12;
    pivot.x+=(pivotTarget.x-pivot.x)*e;pivot.y+=(pivotTarget.y-pivot.y)*e;pivot.z+=(pivotTarget.z-pivot.z)*e;
    if(mode==="3d"&&!dragging&&!perfLow){
      // momentum spin: after a fling the last drag velocity carries + decays; once it dies
      // down auto-rotate resumes (if enabled) — no hard 3s freeze.
      if(Math.abs(yawVel)>0.0004||Math.abs(pitchVel)>0.0004){
        yaw+=yawVel;pitch=Math.max(-1.3,Math.min(1.3,pitch+pitchVel));
        yawVel*=0.94;pitchVel*=0.94;
        if(Math.abs(yawVel)<=0.0004)yawVel=0;if(Math.abs(pitchVel)<=0.0004)pitchVel=0;
      }else if(autoRotate){yaw+=0.0022;}
    }
    // F3: ease the camera zoom toward its target (animated descend/ascend) + advance the
    // hover-outline pulse clock. perfLow snaps (no continuous animation).
    if(Math.abs(zoomTarget-zoom)>0.002){zoom+=(zoomTarget-zoom)*(perfLow?1:0.18);}else{zoom=zoomTarget;}
    blobPhase++;
  }
  function draw(){step();render();}                       // one on-demand frame
  function loop(){step();render();if(moving()){requestAnimationFrame(loop);}else{running=false;}}
  function kick(){if(perfLow){draw();return;}if(!running){running=true;requestAnimationFrame(loop);}}
  function reheat(a){if(!perfLow)alpha=Math.max(alpha,a||0.7);kick();}

  var dragging=false,dragMode=null,dragNode=null,moved=false,lx=0,ly=0;
  function hitTest(mx,my){var best=null,bd=1e9;nodes.forEach(function(n){if(!nodeVisible(n))return;var p=screenPos[n.id];if(!p)return;var d=Math.hypot(p.sx-mx,p.sy-my),hitR=n.r*p.scale+7;if(d<hitR&&d<bd){bd=d;best=n;}});return best;}
  function localXY(ev){var r=canvas.getBoundingClientRect();return {mx:ev.clientX-r.left,my:ev.clientY-r.top};}
  canvas.addEventListener("pointerdown",function(ev){
    dragging=true;moved=false;lx=ev.clientX;ly=ev.clientY;yawVel=0;pitchVel=0;try{canvas.setPointerCapture(ev.pointerId);}catch(e){}canvas.classList.add("grabbing");
    var l=localXY(ev),n=hitTest(l.mx,l.my);
    // 2D: drag a node to move it, OR drag empty space to PAN the view. 3D: orbit.
    if(mode==="2d"&&n){dragMode="node";dragNode=n;n.fixed=true;}
    else if(mode==="2d"){dragMode="pan";dragNode=null;}
    else{dragMode="orbit";dragNode=null;}
    kick();
  });
  canvas.addEventListener("pointermove",function(ev){
    if(dragging){
      var dx=ev.clientX-lx,dy=ev.clientY-ly;lx=ev.clientX;ly=ev.clientY;if(Math.abs(dx)+Math.abs(dy)>2)moved=true;
      if(dragMode==="node"&&dragNode){var l=localXY(ev),w=unproject2d(l.mx,l.my,Wc/2,Hc/2);dragNode.x=w.x;dragNode.y=w.y;reheat(0.5);}
      else if(dragMode==="pan"){panX+=dx;panY+=dy;draw();}
      else if(dragMode==="orbit"&&mode==="3d"){yaw+=dx*0.01;pitch=Math.max(-1.3,Math.min(1.3,pitch+dy*0.01));yawVel=dx*0.01;pitchVel=dy*0.01;draw();}
      return;
    }
    var l=localXY(ev),n=hitTest(l.mx,l.my);
    if(n!==hover){hover=n;renderInfo(hover||selected);canvas.style.cursor=n?"pointer":"grab";kick();}
  });
  canvas.addEventListener("pointerup",function(ev){
    dragging=false;canvas.classList.remove("grabbing");
    if(dragMode==="node"&&dragNode){dragNode.fixed=false;reheat(0.7);}
    if(!moved){
      yawVel=0;pitchVel=0;                      // a click must not fling
      var l=localXY(ev),n=hitTest(l.mx,l.my);
      if(n){                                    // node click selects/recenters ONLY — never opens a row
        // F3: clicking an entity DESCENDS a level (interbrain→person→brain); at brain/node
        // level (or Interbrain OFF) it falls through to the classic select/recenter toggle.
        if(hasGalaxy&&navDescend(n)){}
        else if(selected!==n){selected=n;renderInfo(n);}else{selected=null;renderInfo(null);}
      }else if(hasGalaxy){navAscend();}         // F3: empty-canvas click ASCENDS a level
      else if(selected){selected=null;renderInfo(null);}
      else{setAutoRotate(!autoRotate);}         // empty-canvas click toggles auto-rotate
    }
    dragMode=null;dragNode=null;kick();          // orbit release keeps yawVel → momentum spin
  });
  canvas.addEventListener("wheel",function(ev){ev.preventDefault();zoom=Math.max(ZMIN,Math.min(ZMAX,zoom*(ev.deltaY>0?0.92:1.08)));zoomTarget=zoom;draw();},{passive:false});

  var infoEl=panel.querySelector(".mginfo"),filtersEl=panel.querySelector(".mgfilters");
  function taskA(t){return t&&t.type==="task"?('<a href="#task-'+t.seq+'">#'+t.seq+"</a>"):(t?esc(t.label):"");}
  function relword(k){return ({"spawned-from":"spawned from","related":"related","related-knowledge":"co-cited note","touches-same":"touches same"})[k]||k;}
  function renderInfo(n){
    if(!infoEl)return;
    if(!n){infoEl.innerHTML='<div class="empty">Hover or tap a node to see its relations.</div>';return;}
    var h="";
    if(n.type==="hub"){
      var mem=edges.filter(function(e){return e.cls==="membership"&&e.b===n.id;}).map(function(e){return byId[e.a];});
      h='<div class="title">'+esc(n.label)+'</div><div class="cat">category hub · '+mem.length+' tasks</div><div class="rel"><span class="k">members</span>'+mem.map(taskA).join(" ")+"</div>";
    }else if(n.type==="signal"){
      var tb=edges.filter(function(e){return e.b===n.id&&byId[e.a].type==="task";}).map(function(e){return byId[e.a];});
      h='<div class="title">'+esc(n.label)+'</div><div class="cat">shared '+esc(n.kind)+' · '+tb.length+' tasks</div><div class="rel"><span class="k">touched by</span>'+tb.map(taskA).join(" ")+"</div>";
    }else{
      var ch=byId["cat:"+n.cat];
      h='<div class="title"><span class="chip" style="background:'+catColor(n.cat)+'"></span><a href="#task-'+n.seq+'">#'+n.seq+'</a><a class="open" href="#task-'+n.seq+'">↗ open row</a></div>';
      h+='<div style="font-size:13px;margin-bottom:2px">'+esc(n.title)+"</div>";
      h+='<div class="cat">'+esc(ch?ch.label:n.cat)+(n.closed?" · closed":"")+"</div>";
      edges.forEach(function(e){
        if(e.cls!=="lineage"&&e.cls!=="knowledge")return;
        if(e.a===n.id)h+='<div class="rel"><span class="k">'+esc(relword(e.kind))+'</span>'+taskA(byId[e.b])+"</div>";
        else if(e.b===n.id)h+='<div class="rel"><span class="k">'+esc(relword(e.kind))+' ←</span>'+taskA(byId[e.a])+"</div>";
      });
      nodes.forEach(function(s){
        if(s.type!=="signal")return;
        if(!edges.some(function(e){return e.a===n.id&&e.b===s.id;}))return;
        var others=edges.filter(function(e){return e.b===s.id&&e.a!==n.id&&byId[e.a].type==="task";}).map(function(e){return byId[e.a];});
        h+='<div class="rel"><span class="k">shares '+esc(s.label)+'</span>'+others.map(taskA).join(" ")+"</div>";
      });
    }
    infoEl.innerHTML=h;
  }

  // ---- C2/G2: grouped multi-select filter panel — swatches are the ACTUAL node-shape
  // glyphs (inline SVG) so the legend reads at a glance; edges keep a colored dashed line.
  var swatches=[];
  function glyphSVG(g,color,dash){
    var inner;
    if(g==="circle")inner='<circle cx="0" cy="0" r="5" fill="'+color+'"></circle>';
    else if(g==="diamond")inner='<rect x="-4.2" y="-4.2" width="8.4" height="8.4" transform="rotate(45)" fill="'+color+'"></rect>';
    else if(g==="hexagon"){var pts="";for(var i=0;i<6;i++){var a=Math.PI/6+i*Math.PI/3;pts+=(i?" ":"")+(Math.cos(a)*6).toFixed(1)+","+(Math.sin(a)*6).toFixed(1);}inner='<polygon points="'+pts+'" fill="'+color+'"></polygon>';}
    else if(g==="rounded")inner='<rect x="-6" y="-4.5" width="12" height="9" rx="3.5" fill="'+color+'"></rect>';
    else inner='<line x1="-8" y1="0" x2="8" y2="0" stroke="'+color+'" stroke-width="3" stroke-linecap="round"'+(dash?(' stroke-dasharray="'+dash+'"'):"")+'></line>';
    return '<svg width="18" height="14" viewBox="-9 -7 18 14" aria-hidden="true">'+inner+'</svg>';
  }
  // a group is a header (title + a smart "hide/show all" toggle) plus its rows. mkRow
  // returns {btn,set,get} so the group toggle-all can drive each row AND read their state.
  // The toggle-all LABEL follows the actual row states: if every row is on → "hide all"
  // (clicking hides them), otherwise → "show all" (clicking shows them). It refreshes on
  // every row change so it never lies. Every toggle re-renders (kick).
  var groups=[];
  function mkGroup(title){
    var g=document.createElement("div");g.className="mgfgroup";
    var hh=document.createElement("div");hh.className="h";
    var lab=document.createElement("span");lab.className="ht";lab.textContent=title;hh.appendChild(lab);
    var all=document.createElement("button");all.type="button";all.className="mgallbtn";
    hh.appendChild(all);g.appendChild(hh);
    var rows=[];
    function allOn(){for(var i=0;i<rows.length;i++)if(!rows[i].get())return false;return rows.length>0;}
    function refresh(){all.textContent=allOn()?"hide all":"show all";}
    all.addEventListener("click",function(){var v=!allOn();      // all on → hide; else → show all
      for(var j=0;j<rows.length;j++)rows[j].set(v);refresh();kick();});
    var gobj={group:g,rows:rows,allBtn:all,refresh:refresh};
    groups.push(gobj);return gobj;
  }
  function mkRow(label,count,glyph,colorFn,onToggle,dash){
    var b=document.createElement("button");b.type="button";b.className="mgf";
    var box=document.createElement("span");box.className="box";b.appendChild(box);
    var sw=document.createElement("span");sw.className="gly";sw.innerHTML=glyphSVG(glyph,colorFn(),dash||"");b.appendChild(sw);
    var nm=document.createElement("span");nm.className="nm";nm.textContent=label;b.appendChild(nm);
    if(count!==""){var ct=document.createElement("span");ct.className="ct";ct.textContent=count;b.appendChild(ct);}
    function set(v){b.classList.toggle("off",!v);onToggle(v);}
    swatches.push({sw:sw,glyph:glyph,colorFn:colorFn,dash:dash||""});
    return {btn:b,set:set,get:function(){return !b.classList.contains("off");}};
  }
  // wire the row's click here (after its group exists) so it also refreshes the group's
  // smart toggle-all label to follow the new state.
  function pushRow(gobj,row){
    row.btn.addEventListener("click",function(){row.set(!row.get());gobj.refresh();kick();});
    gobj.group.appendChild(row.btn);gobj.rows.push(row);
  }
  var catsPresent={},edgeKinds={},statusPresent={},soloCount=0;
  var sigNodes=nodes.filter(function(n){return n.type==="signal";}).sort(function(a,b){
    return (a.kind+a.label).localeCompare(b.kind+b.label);});
  nodes.forEach(function(n){if(n.type==="task"&&!n.solo&&n.cat!=null)catsPresent[n.cat]=(catsPresent[n.cat]||0)+1;});
  nodes.forEach(function(n){if(n.type==="task"){statusPresent[n.status]=(statusPresent[n.status]||0)+1;if(n.solo)soloCount++;}});
  edges.forEach(function(e){edgeKinds[e.cls]=(edgeKinds[e.cls]||0)+1;});
  var sigKinds={};sigNodes.forEach(function(s){sigKinds[s.kind]=(sigKinds[s.kind]||0)+1;});
  var SIGGLY={pr:"diamond",repo:"hexagon",story:"rounded"},EDGEDASH={repo:"5,4",story:"2,4",knowledge:"1,4"};
  var soloRow=null,soloGroup=null;
  if(filtersEl){
    var ckeys=Object.keys(catsPresent).sort();
    if(ckeys.length){var g1=mkGroup("Tasks · category");ckeys.forEach(function(k){filt.cat[k]=true;pushRow(g1,mkRow(catLabels[k]||k,catsPresent[k],"circle",function(){return catColor(k);},function(v){filt.cat[k]=v;}));});filtersEl.appendChild(g1.group);}
    // task LIFECYCLE status filter (open/active/closed — matches the task glyphs).
    var STATVAR={open:"--so",active:"--sa",closed:"--sc"},STATGLY={open:"○",active:"●",closed:"✕"};
    var skeys=["open","active","closed"].filter(function(k){return statusPresent[k];});
    Object.keys(statusPresent).forEach(function(k){if(skeys.indexOf(k)<0)skeys.push(k);});
    if(skeys.length){var g1s=mkGroup("Tasks · status");skeys.forEach(function(k){filt.status[k]=true;
      pushRow(g1s,mkRow((STATGLY[k]||"")+" "+k,statusPresent[k],"circle",function(){return rootVar(STATVAR[k]||"--dim")||"#888";},function(v){filt.status[k]=v;}));});filtersEl.appendChild(g1s.group);}
    // unlinked (undrawn) tasks — default OFF; toggling refits the view around the
    // shown population (the outer solo ring changes the frame).
    if(soloCount){soloGroup=mkGroup("Unlinked");
      soloRow=mkRow("unlinked tasks",soloCount,"circle",function(){return rootVar("--dim")||"#888";},function(v){filt.solo=v;fitView();});
      pushRow(soloGroup,soloRow);soloRow.set(false);soloGroup.refresh();filtersEl.appendChild(soloGroup.group);}
    // one filter row per signal KIND (story / repo / pr), each showing+hiding ALL hubs of
    // that kind — their spokes prune for free via edgeVisible's endpoint check. Per-hub
    // rows grew without bound as repos and PRs accumulated, until the panel was longer
    // than the graph it filtered. The count is how many HUBS the row collapses, not their
    // summed membership: a kind row is about what it folds together.
    // Ordered story→repo→pr, then any unforeseen kind appended (mirrors the status rows) —
    // a signal kind with no row would be an unfilterable hub, the same landmine as an edge
    // kind with no row.
    var kkeys=["story","repo","pr"].filter(function(k){return sigKinds[k];});
    Object.keys(sigKinds).forEach(function(k){if(kkeys.indexOf(k)<0)kkeys.push(k);});
    if(kkeys.length){var g2=mkGroup("Signal hubs");kkeys.forEach(function(k){filt.sig[k]=true;
      pushRow(g2,mkRow(k,sigKinds[k],SIGGLY[k]||"diamond",function(){return sigColor(k);},function(v){filt.sig[k]=v;}));});filtersEl.appendChild(g2.group);}
    // No "Category hubs" row: a hub follows its category (see nodeVisible) — toggling a
    // category off obviously hides the hub that renders it.
    var EK=[["lineage","Lineage"],["membership","Membership"],["pr","Shares PR"],["repo","Shares repo"],["story","Shares story"],["knowledge","Co-cited note"]],g4=null;
    EK.forEach(function(it){var c=it[0];if(!edgeKinds[c])return;if(!g4)g4=mkGroup("Edges");filt.edge[c]=true;pushRow(g4,mkRow(it[1],edgeKinds[c],"line",function(){return edgeColor(c);},function(v){filt.edge[c]=v;},EDGEDASH[c]||""));});
    if(g4)filtersEl.appendChild(g4.group);
  }
  // initialise each group's smart toggle-all label to the current (all-on) state.
  groups.forEach(function(g){g.refresh();});
  // restore every filter to its DEFAULT (all-on; unlinked back OFF) + resync the
  // toggle-all labels + the swatch colours (used by Reset).
  function resetFilters(){
    Object.keys(filt.cat).forEach(function(k){filt.cat[k]=true;});
    Object.keys(filt.sig).forEach(function(k){filt.sig[k]=true;});
    Object.keys(filt.edge).forEach(function(k){filt.edge[k]=true;});
    Object.keys(filt.status).forEach(function(k){filt.status[k]=true;});
    if(filtersEl){var offs=filtersEl.querySelectorAll(".mgf.off");
      Array.prototype.forEach.call(offs,function(b){b.classList.remove("off");});}
    if(soloRow)soloRow.set(false);                 // unlinked tasks default OFF
    groups.forEach(function(g){g.refresh();});
  }

  var q2d=panel.querySelector(".mg2d"),q3d=panel.querySelector(".mg3d"),rotBtn=panel.querySelector(".mgrotate"),hintEl=panel.querySelector(".mghint");
  // B5: the on-canvas indicator reflects the auto-rotate STATE.
  function updateHint(){if(!hintEl)return;var t;
    if(mode==="2d")t="2D · drag to pan · drag a node to move · scroll to zoom · click a node to focus";
    else t="3D · drag to orbit · scroll to zoom · click empty space to toggle rotate · rotate: "+(autoRotate?"on":"off");
    if(hasGalaxy)t+=" · click to zoom in · empty to go up ("+galaxyLevel()+")";
    hintEl.textContent=t;}
  function setAutoRotate(v){if(perfLow)v=false;autoRotate=v;pref.autoRotate=v;savePref();if(rotBtn){rotBtn.setAttribute("aria-pressed",v);rotBtn.textContent=v?"⟳ Auto-rotate":"⦻ Rotate off";}updateHint();kick();}
  // switching mode keeps the x/y layout, re-seeds z (3D) or flattens it (2D), then settles
  // synchronously and FITS so the view is framed + not over-zoomed.
  function setMode(m){mode=m;pref.mode=m;savePref();
    if(q2d)q2d.setAttribute("aria-pressed",m==="2d");if(q3d)q3d.setAttribute("aria-pressed",m==="3d");
    if(rotBtn)rotBtn.style.display=(m==="3d")?"":"none";
    selected=null;renderInfo(null);yawVel=0;pitchVel=0;pivot.x=0;pivot.y=0;pivot.z=0;
    if(m==="3d"){yaw=0.5;pitch=-0.35;seedZ();}else{flattenZ();}
    assignWells();settle(200);fitView();updateHint();draw();kick();
  }
  if(q2d)q2d.addEventListener("click",function(){setMode("2d");});
  if(q3d)q3d.addEventListener("click",function(){setMode("3d");});
  if(rotBtn)rotBtn.addEventListener("click",function(){setAutoRotate(!autoRotate);});
  // F3: boundary-blob toggle (default on; button present only when the graph has galaxy
  // grouping). OFF renders the graph identically minus the blobs.
  var blobBtn=panel.querySelector(".mgblob");
  function setBlobs(v){blobsOn=v;pref.blobs=v;savePref();if(blobBtn)blobBtn.setAttribute("aria-pressed",v);draw();kick();}
  if(blobBtn){blobBtn.setAttribute("aria-pressed",blobsOn);blobBtn.addEventListener("click",function(){setBlobs(!blobsOn);});}
  // Reset = a HARD, immediate reset to equilibrium (a total refresh): re-seed the layout,
  // settle it to rest synchronously, re-fit + re-centre the camera, clear selection/pan,
  // AND reset the filters + search back to all-on — NOT a reheat that eases the current
  // tangled positions back.
  function hardReset(){selected=null;renderInfo(null);yawVel=0;pitchVel=0;panX=0;panY=0;
    pivot.x=0;pivot.y=0;pivot.z=0;pivotTarget.x=0;pivotTarget.y=0;pivotTarget.z=0;
    resetFilters();
    if(searchEl)searchEl.value="";query="";terms=[];if(searchN)searchN.textContent="";
    seedXY();if(mode==="3d"){yaw=0.5;pitch=-0.35;seedZ();}else{flattenZ();}
    assignWells();settle(300);fitView();draw();kick();}
  var resetBtn=panel.querySelector(".mgreset");
  if(resetBtn)resetBtn.addEventListener("click",hardReset);
  var searchEl=panel.querySelector(".mgq"),searchN=panel.querySelector(".mgn");
  if(searchEl)searchEl.addEventListener("input",function(){query=(searchEl.value||"").trim();terms=query?query.toLowerCase().split(" ").filter(Boolean):[];var c=query?nodes.filter(function(n){return nodeVisible(n)&&matches(n);}).length:0;if(searchN)searchN.textContent=query?(c+" match"+(c===1?"":"es")):"";kick();});

  // F2: react to a focus change from the top-bar strip — re-read the shared focus, re-fit
  // the view around the now-visible population, and redraw. Guarded; a no-op with no strip.
  // F3: strip→graph sync. A focus change from the top-bar strip re-derives the level,
  // rebuilds wells, and reframes (direction from the depth delta). navApplying guards our
  // OWN dispatch (graph→strip, in graphSetFocus) from bouncing back. No-op with no strip.
  try{window.addEventListener("ts-focus-change",function(ev){
    if(navApplying)return;
    var f=(ev&&ev.detail)||null,rank={interbrain:0,person:1,brain:2},old=rank[galaxyLevel()];
    gfocus=(f&&f.kind)?f:null;var nw=rank[galaxyLevel()];
    frameLevel(nw>old?"down":(nw<old?"up":null));});}catch(e){}

  // ---- D3: centerOnSeq exposed for the board's "View in graph" button ----
  function centerOnSeq(seq){var n=byId["t:"+seq];if(!n)return false;
    // "View in graph" on an unlinked task auto-enables the solo filter so the
    // target is actually visible.
    if(n.solo&&!filt.solo&&soloRow){soloRow.set(true);if(soloGroup)soloGroup.refresh();}
    selected=n;renderInfo(n);if(perfLow){pivot.x=n.x;pivot.y=n.y;pivot.z=n.z;}kick();return true;}
  try{window.__mgCenterOnSeq=centerOnSeq;}catch(e){}
  if(document.querySelectorAll){var vbs=document.querySelectorAll(".mgviewbtn[data-graph-seq]");Array.prototype.forEach.call(vbs,function(b){if(byId["t:"+b.getAttribute("data-graph-seq")]){var w=b.closest?b.closest(".mgviewwrap"):null;if(w)w.hidden=false;else b.hidden=false;}});}
  document.addEventListener("click",function(ev){var b=(ev.target&&ev.target.closest)?ev.target.closest(".mgviewbtn[data-graph-seq]"):null;if(!b)return;var seq=b.getAttribute("data-graph-seq");if(!byId["t:"+seq])return;panel.open=true;try{panel.scrollIntoView({block:"start"});}catch(e){}resize();centerOnSeq(seq);});

  if(window.MutationObserver){
    var mo=new MutationObserver(function(){catCache={};varCache={};swatches.forEach(function(s){s.sw.innerHTML=glyphSVG(s.glyph,s.colorFn(),s.dash);});
      // react to a live performance-mode toggle: entering low freezes animation, leaving
      // it restarts the loop. Either way, redraw with the fresh theme colours.
      var pl=root.getAttribute("data-perf")==="low";
      if(pl!==perfLow){perfLow=pl;if(perfLow)autoRotate=false;kick();}else{draw();}});
    mo.observe(root,{attributes:true,attributeFilter:["data-theme","data-perf"]});
  }

  panel.classList.add("mg-live");
  resize();
  if(reduce||perfLow)autoRotate=false;
  if(rotBtn)rotBtn.setAttribute("aria-pressed",autoRotate);
  // setMode settles + fits + draws + starts the loop (idle-stops itself when at rest).
  setMode(mode);renderInfo(null);
  panel.addEventListener("toggle",function(){if(panel.open){resize();fitView();draw();kick();}});
})();}catch(e){}"""


def _graph_enhance_script():
    """The step-2 client interaction layer as a standalone `<script>` (see _MG_ENHANCE_JS).
    Appended LAST in render_html, guarded so any failure is inert."""
    return "<script>%s</script>" % _MG_ENHANCE_JS


def render_html(tasks, *, theme=None, variant=None, variant_label=None, generated="",
                commands=None, config_rows=None, board_autorefresh=False, bare=False,
                rev="", version="", updated="", repo_url="", live_sessions=None,
                graph=None, generated_ts=None, updated_ts=None,
                interbrain=False, org_label="Org brain"):
    """(Mostly) self-contained HTML board for the task view-models `tasks` (each a
    dict from task-station.py's _board_view_model). Open (not-closed) tasks first,
    then closed. theme/variant default to the active theme; theme picks per-category
    accent colours, variant sets the page's DEFAULT light/dark chrome (the toggle can
    override it). `commands` (the _COMMANDS_HELP list) and `config_rows`
    (config.board_rows()) drive the bottom help panel; `bare`
    (config.bare_commands()) controls whether the Commands panel shows the bare
    /todo,/done,/pin labels or the /task-station: prefixed forms, with helptext
    reflecting the state. `generated` is shown top-right in the kicker (not the footer). `version` and
    `updated` (the task-station version + when this installed version was written) lead
    the footer snapshot note as a dim prefix when present, for both refresh states.
    `repo_url` (the plugin's GitHub homepage/repository) renders on its OWN line below
    the version/updated line as a clickable link — a hyperlink, NOT a loaded asset.


    Inline `<script>`/`<style>` ARE used (theme toggle + hover-scroll); there are
    NEVER any EXTERNAL assets (no src/link/@import/url(http)/remote fonts) — the
    board stays a single local file. When `board_autorefresh` is True (opt-in,
    default off) the behavior script POLLS the sibling `board.rev.js` <script> sidecar
    every ~2s and reloads ONLY when its `window.__TSREV` differs from the embedded `rev`
    (a real data change), tagging the reload so it restores open rows / scroll / filters.
    A <script> sidecar (not fetch) is used because file:// browsers (Safari/Chrome)
    block local fetch but load local scripts, and the dynamic subresource doesn't trip
    the address-bar loading bar — so the steady state never reloads. A manual reload
    starts clean. The toggle's choice survives the reload."""
    tasks = list(tasks or [])
    if theme is None and _cats is not None:
        try:
            import config as _config
            theme = _config.active_theme()
        except Exception:
            theme = getattr(_cats, "DEFAULT_THEME", "sands")
    if variant is None and _cats is not None and hasattr(_cats, "resolve_variant"):
        try:
            variant = _cats.resolve_variant()
        except Exception:
            variant = "dark"
    default_variant = variant if variant in _PAGE else "dark"
    open_tasks = [t for t in tasks if t.get("status") != "closed"]
    closed_tasks = [t for t in tasks if t.get("status") == "closed"]

    out = [
        "<!doctype html>", '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
    ]
    # Opt-in only: a CHANGE-DRIVEN reload (no <meta http-equiv="refresh">) — the behavior
    # script polls the sibling board.rev.js <script> sidecar and reloads only when the data
    # actually changed (the embedded rev differs), picking up the Stop hook's quiet regen.
    # It tags the
    # reload via the `ts-auto` sessionStorage flag so the NEXT load restores open rows /
    # scroll / filters (req A/B); a manual reload carries no flag and starts clean. The
    # theme toggle persists its choice to localStorage, surviving the reload.
    cat_rows = _collect_categories(tasks)
    out += [
        "<title>task-station — board</title>",
        # Stamp the RENDERING plugin version so the Stop-hook regen can refuse to
        # DOWNGRADE an already-written board (a stale, older-version session must not
        # clobber a newer render — see task-station.py write_board(guard_downgrade)).
        ('<meta name="ts-board-version" content="%s">' % _e(version)) if version else "",
        # set the theme attribute BEFORE the stylesheet paints (no flash).
        _theme_init_script(default_variant),
        "<style>%s</style>" % _css(default_variant, _category_css(tasks, theme)),
        '</head><body><div class="wrap">',
        # A small kicker — "claude code • task-station" on the LEFT (.kleft, sitting
        # together with a dimmed "•" separator) and the "refreshed <ts>" on the RIGHT
        # (.kgen, only when a timestamp is given — the value is the write-board time, i.e.
        # when the board was last refreshed), space-between across the page. The command
        # name lives in the <h1> ("/todo board"); <title> stays "task-station — board".
        # The kicker's right group (.kright) holds the "refreshed <ts>" label (only when
        # a timestamp is given) and, to its RIGHT, the always-present light text Refresh
        # button (1.37.0: moved up here from the header — the .hdr right side is now JUST
        # the theme toggle again). The theme toggle sits in its header corner below.
        '<div class="kicker"><span class="kleft"><span>claude code</span>'
        '<span class="ksep">•</span><span>task-station</span></span>'
        '<span class="kright">%s'
        '<button id="board-refresh" class="krefresh" type="button" '
        'aria-label="Refresh the board">↻ refresh</button></span></div>'
        % (('<span class="kgen">refreshed %s</span>' % _local_ts(generated, generated_ts))
           if generated else "") +
        '<div class="hdr"><div>'
        "<h1>/todo board</h1></div>"
        '<div class="hdrbtns">'
        '<button id="perf-toggle" class="toggle" type="button" '
        'aria-label="Toggle performance mode (disable animations on slow machines)">performance</button>'
        '<button id="theme-toggle" class="toggle" type="button" '
        'aria-label="Toggle light/dark theme">theme</button></div></div>',
        '<p class="lede">%d task(s) — expand any row for its full title, summary, '
        "open/resume commands, and briefing.</p>" % len(tasks),
    ]
    # F2: the compact focus strip (Interbrain only — absent when off, so the off render is
    # byte-parity with pre-F1 classic). Sits in the header row, no vertical rail.
    if interbrain:
        out.append(_focus_strip(tasks, org_label))
    # WS5: a strip of the ACTUALLY-running Claude sessions (process state), above
    # the task sections. Empty list → nothing rendered.
    out.extend(_live_strip(live_sessions))
    if not tasks:
        out.append('<div class="empty">No tasks yet.</div>')
    else:
        out.append(_filters(cat_rows))
        out.extend(_section("Open", open_tasks, theme, variant))
        out.extend(_section("Closed", closed_tasks, theme, variant, see_more_after=5))
        # WS-D: the task-relations mini-graph. Emitted only when the graph has edges
        # (relation-free / bare stores show nothing — unchanged board). Every board
        # task rides along as the solo pool so the canvas can offer the default-off
        # "unlinked tasks" filter (tasks with no drawn relations).
        solo_pool = [{"seq": t.get("seq"), "title": t.get("title", ""),
                      "color": t.get("color"), "status": t.get("status")}
                     for t in tasks if t.get("seq") is not None]
        out.extend(_minigraph(graph, theme, default_variant, solo_pool=solo_pool))
        out.extend(_categories_panel(cat_rows, theme, default_variant))
    out.extend(_help_panel(commands, config_rows, variant_label, bare,
                           interbrain=interbrain))
    # The generated timestamp now lives in the top kicker (.kgen), so the bottom note
    # no longer carries a "generated …" prefix. Build the snapshot from non-empty parts
    # joined by " · " so there is never a dangling separator. The autorefresh-ON note was
    # REMOVED — the Refresh button + "refreshed" timestamp in the kicker cover it; when
    # autorefresh is on the footer is just version/updated. version/updated are our own
    # strings but kept _e-escaped for consistency; the static-snapshot note carries
    # intended <code> markup, so it is appended already-built and NOT escaped here.
    parts = []
    if version:
        parts.append("task-station v%s" % _e(version))
    if updated:
        parts.append("updated %s" % _local_ts(updated, updated_ts))
    if not board_autorefresh:
        # `/todo board` IS bare-aware: bare → /todo board, else /task-station:todo board.
        refresh_cmd = "/todo board" if bare else "/task-station:todo board"
        parts.append("this board is a static snapshot — re-run <code>%s</code> "
                     "to refresh." % refresh_cmd)
    # The footer is now two stacked lines: the version/updated (+ static note) line first,
    # then — on its OWN line below — the GitHub repo link (when a repo_url is given). line1
    # already carries intended <code> markup so it is NOT re-escaped; the repo link's href
    # is the full url and its visible text is the url with the scheme stripped, both _e-
    # escaped. The link is a hyperlink the user CLICKS, not a loaded asset (no src/link/
    # @import/font) — the board stays self-contained.
    line1 = " · ".join(parts)
    rows = []
    if line1:
        rows.append('<div>%s</div>' % line1)
    if repo_url:
        rows.append('<div class="repo"><a href="%s" target="_blank" rel="noopener noreferrer">%s</a></div>'
                    % (_e(repo_url), _e(repo_url.replace("https://", "").replace("http://", ""))))
    out.append('<div class="snapshot">%s</div>' % "".join(rows))
    out.append(_behavior_script(autorefresh=board_autorefresh, rev=rev,
                                interbrain=interbrain))
    # Step 2: the relations-graph client interaction layer — a SEPARATE, fully try-caught
    # <script> appended LAST (never inside _behavior_script). Only when the graph has edges
    # (so a relation-free board ships no inert enhancement JS).
    if isinstance(graph, dict) and graph.get("edges"):
        out.append(_graph_enhance_script())
    out.append("</div></body></html>")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    # Standalone rendering needs the store; that lives in task-station.py. Use the
    # CLI entrypoint instead so view-models are built consistently.
    sys.stderr.write("Run `python3 lib/task-station.py board` to render the board.\n")
    sys.exit(2)
