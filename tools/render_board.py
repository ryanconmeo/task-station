#!/usr/bin/env python3
"""Render a (mostly) self-contained HTML board of all task-station tasks.

Mirrors the terminal `/todo` board: TWO sections (Open, then Closed), each a GRID
with the same columns — status · # · Task · Category · Effort · Activity — and
every row EXPANDABLE via native <details>/<summary>. A row with steps carries a
compact progress bar + N/M in its Task cell. The expanded row leads with the FULL
(untruncated) title, then the structured digest (goal · next/standing · steps
checklist with rollup · decisions · repos · PRs · files), then the Open/Resume
actions block, a de-emphasised Workers subsection, and LAST the full task summary.

SELF-CONTAINMENT (board is a LOCAL file opened in a browser): inline `<script>`
and inline `<style>` ARE allowed (theme toggle + hover-scroll need them), but NO
EXTERNAL assets — no `src="http"`, no `<link >`, no `@import`, no `url(http`, no
remote fonts. All injected text (summary/goal/decisions) is HTML-ESCAPED (mdlite)
first, so it stays inert even with JS present. NO server, NO deps beyond the
stdlib + the optional `categories` module (for per-category palettes per VARIANT),
NO LLM, no network. Every value comes from the view-models task-station.py hands
in, so this module is import-safe and unit-testable on plain dicts."""
import html
import os
import sys

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
    },
    "light": {
        "page": "#f3efe7", "panel": "#fbf8f2", "panel2": "#ece7db", "code": "#fffdf8",
        "open": "#e3dccb",
        "ink": "#2b2823", "dim": "#6c665c", "line": "#dcd5c8", "accent": "#7a4fb0",
        "so": "#1d6fa5", "sob": "rgba(29,111,165,.12)",
        "sa": "#3c7a16", "sab": "rgba(60,122,22,.13)",
        "sc": "#6c665c", "scb": "rgba(108,102,92,.13)",
    },
}

# The order page palette vars are emitted (a per-variant block carries all of them).
_PAGE_KEYS = ("page", "panel", "panel2", "code", "open", "ink", "dim", "line",
              "accent", "so", "sob", "sa", "sab", "sc", "scb")

# The grid column template, shared by the header row and every <summary> so the
# columns line up across the (separate) grid containers — alignment across grids
# needs fixed/fr tracks, never `auto`. status · # · Task · Category · Effort · Activity.
_COLS = "94px 52px minmax(0,1fr) 168px 132px 96px"
_COLS_NARROW = "78px 42px minmax(0,1fr)"   # task only on a narrow viewport

_STATUS_GLYPH = {"open": "○", "active": "●", "closed": "✕"}

# Open/resume commands must never wrap — they scroll within their own box instead
# of widening the page (req 8). Applied inline so the style sits ON the element.
_CMD_STYLE = "white-space:nowrap;overflow-x:auto"

# localStorage key for the persisted light/dark choice (survives the meta-refresh).
_THEME_KEY = "ts-board-theme"


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
  body{background:var(--page);color:var(--ink);font-family:var(--sans);
    -webkit-font-smoothing:antialiased;line-height:1.5;padding:40px 28px 60px}
  .wrap{max-width:1180px;margin:0 auto}
  .hdr{display:flex;justify-content:space-between;align-items:flex-start;gap:16px}
  .kicker{font-family:var(--mono);font-size:12px;letter-spacing:.06em;color:var(--dim)}
  h1{font-size:28px;font-weight:650;letter-spacing:-.02em;margin:6px 0 6px}
  .lede{color:var(--dim);font-size:14px;max-width:80ch}
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
  /* the left accent stripe is the category's BACKGROUND colour (req 7), via the
     per-variant --cat-bg variable; the inline fallback is the resolved-variant bg. */
  details.row{border-bottom:1px solid var(--line);border-left:4px solid var(--accent)}
  details.row:last-child{border-bottom:none}
  details.row.closed{opacity:.62}
  summary.rowsum{cursor:pointer;list-style:none}
  summary.rowsum::-webkit-details-marker{display:none}
  summary.rowsum:hover{background:var(--panel2)}
  /* an EXPANDED row gets a distinct background so its bounds are obvious (req 6) —
     works in both variants since --open is defined per variant. */
  details.row[open]{background:var(--open)}
  details.row[open]>summary.rowsum{background:var(--open);border-bottom:1px solid var(--line)}
  .c-task{font-weight:600;font-size:14.5px;letter-spacing:-.01em;
    display:flex;align-items:center;gap:8px;min-width:0}
  .c-task .disc{flex:none;color:var(--dim);font-size:11px;transition:transform .12s}
  details.row[open] .c-task .disc{transform:rotate(90deg)}
  /* the collapsed title: ellipsis until hover, then JS auto-scrolls it (req 2).
     overflow:hidden keeps the scrollbar hidden; flex:1+min-width:0 fixes its width
     so the auto-scroll never shifts layout. */
  .c-task .ttl{flex:1;min-width:0;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
  /* compact per-row progress (mini bar + N/M) folded into the Task cell — no new
     column, so the grid template is untouched. Only rendered when steps exist. */
  .c-task .prog{flex:none;display:inline-flex;align-items:center;gap:5px;
    font-family:var(--mono);font-size:10.5px;color:var(--dim)}
  .c-task .pbar{display:inline-block;width:34px;height:5px;border-radius:99px;
    background:var(--line);overflow:hidden}
  .c-task .pbar>span{display:block;height:100%;background:var(--accent)}
  .c-seq{font-family:var(--mono);font-size:12px;color:var(--dim)}
  .c-act{font-family:var(--mono);font-size:11.5px;color:var(--dim)}
  .c-eff{font-family:var(--mono);font-size:12px;color:var(--ink)}
  .c-eff .gauge{letter-spacing:1px;margin-right:5px}

  /* status: a LABELED, clearly non-interactive pill — never a bare glyph */
  .pill{display:inline-flex;align-items:center;gap:5px;cursor:default;font-family:var(--mono);
    font-weight:650;font-size:10.5px;letter-spacing:.04em;border-radius:99px;padding:2px 9px;
    border:1px solid currentColor;white-space:nowrap}
  .pill.open{color:var(--so);background:var(--sob)}
  .pill.active{color:var(--sa);background:var(--sab)}
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
  /* the full summary comes LAST and is rendered as light markdown; cap its height
     so a huge blob scrolls inside its own box rather than dominating the card. */
  .summary{font-size:14px;color:var(--ink);background:var(--panel2);border-left:3px solid var(--accent);
    border-radius:6px;padding:10px 12px;overflow-wrap:anywhere;max-height:16em;overflow-y:auto}
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
  .action{display:grid;gap:5px}
  .action .lab{display:flex;flex-wrap:wrap;gap:6px 10px;align-items:baseline}
  .action .lab .name{font-family:var(--mono);font-size:11.5px;font-weight:650;color:var(--accent);
    letter-spacing:.04em;text-transform:uppercase}
  .action .lab .when{font-family:var(--mono);font-size:11px;color:var(--dim)}
  .action .sub{font-size:11.5px;color:var(--dim)}
  .cmd{display:block;font-family:var(--mono);font-size:12px;line-height:1.5;color:var(--ink);
    background:var(--code);border:1px solid var(--line);border-radius:6px;padding:8px 10px}
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
  .brief a{color:var(--accent);overflow-wrap:anywhere}
  .brief .files{font-family:var(--mono);font-size:11.5px;color:var(--ink);display:grid;gap:2px}
  .brief .files .d{color:var(--dim)}
  /* each PR on ITS OWN LINE, the link then its description when present (req 5). */
  .brief .prs{display:grid;gap:3px;min-width:0}
  .brief .prs .pr{font-family:var(--mono);font-size:11.5px;overflow-wrap:anywhere}
  .brief .prs .pr .d{color:var(--dim)}
  .brief ul.steps{margin:0;padding-left:2px;list-style:none;display:grid;gap:3px;min-width:0}
  .brief ul.steps li{font-family:var(--mono);font-size:12px;color:var(--ink);overflow-wrap:anywhere}
  .brief ul.steps li.done{color:var(--dim);text-decoration:line-through}
  .brief ul.decisions{margin:0;padding-left:18px;list-style:disc;min-width:0;display:grid;gap:2px}
  .brief ul.decisions li{color:var(--ink);overflow-wrap:anywhere}

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

  .snapshot{margin-top:30px;padding-top:14px;border-top:1px solid var(--line);
    font-family:var(--mono);font-size:11.5px;color:var(--dim)}
  .snapshot code{background:var(--panel2);border:1px solid var(--line);border-radius:5px;padding:1px 6px}
  .empty{color:var(--dim);font-style:italic;padding:22px 0}

  @media (max-width:720px){
    :root{--cols:__NARROW__}
    .c-cat,.c-eff,.c-act,.head .c-cat,.head .c-eff,.head .c-act{display:none}
    .panels{grid-template-columns:1fr}
  }
""".replace("__NARROW__", _COLS_NARROW)
    return root + themes + (category_css + "\n" if category_css else "") + body


def _e(s):
    return html.escape(str(s if s is not None else ""), quote=True)


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
    return ('<span class="prog" title="%d of %d steps done">'
            '<span class="pbar"><span style="width:%d%%"></span></span>%d/%d</span>'
            % (done, total, pct, done, total))


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
    """Per-category colour variables for BOTH variants, so the row's left stripe (its
    --cat-bg, req 7) and its tag re-tint when the toggle flips data-theme without a
    reload. Emits `html[data-theme="<v>"] .cat-<color>{--cat-bg:..;--cat-accent:..}`
    for every category present, in each variant — colours come from theme_palette."""
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
            if not pal:
                continue
            decls = []
            bg = _cat_bg(pal)
            if bg:
                decls.append("--cat-bg:%s" % bg)
            accent = _cat_accent(pal, None)
            if accent:
                decls.append("--cat-accent:%s" % accent)
            if decls:
                rules.append('html[data-theme="%s"] .%s{%s}' % (variant, cls, ";".join(decls)))
    return "\n".join(rules)


def _status_cell(t):
    st = t.get("status_label") or t.get("status") or "open"
    glyph = _STATUS_GLYPH.get(st, "")
    cls = st if st in ("open", "active", "closed") else "open"
    return ('<span class="c-status"><span class="pill %s">%s %s</span></span>'
            % (_e(cls), _e(glyph), _e(st)))


def _tag_cell(t, accent_fb, bg_fb):
    tag = t.get("tag")
    if not tag:
        return '<span class="c-cat"></span>'
    # colours via the per-variant --cat-* variables (set on the row); the inline
    # fallbacks are the resolved-variant hexes so it still tints without/before JS.
    style = "color:var(--cat-accent,%s)" % _e(accent_fb)
    if bg_fb:
        style += ";background:var(--cat-bg,%s)" % _e(bg_fb)
    else:
        style += ";background:var(--cat-bg,transparent)"
    return '<span class="c-cat"><span class="tag" style="%s">%s</span></span>' % (style, _e(tag))


def _effort_cell(t):
    gauge = t.get("effort_gauge") or ""
    eff = (t.get("effort") or "").upper()
    word = t.get("effort_label") or ""
    if not eff:
        return '<span class="c-eff"></span>'
    label = "%s %s" % (eff, word) if word else eff
    return ('<span class="c-eff"><span class="gauge">%s</span>%s</span>'
            % (_e(gauge), _e(label)))


def _resume_detail(t):
    """One cohesive Open/Resume ACTIONS block — the simple `/todo <seq>` open command
    (attaches the task to THIS session: the recap) ABOVE the resume one-liner (jumps
    back into the original working session). Pinned is folded INTO the resume label
    (req 3) — no separate pinned banner. A de-emphasised Workers subsection follows."""
    open_cmd = t.get("open_command")
    rm = t.get("resume_main")
    workers = t.get("workers") or []
    out = []
    actions = []
    if open_cmd:
        actions.append(
            '<div class="action"><div class="lab"><span class="name">Open the task</span></div>'
            '<div class="sub">attaches/opens it in the current session — the recap</div>'
            '<code class="cmd" style="%s">%s</code></div>'
            % (_CMD_STYLE, _e(open_cmd)))
    if rm and rm.get("command"):
        pinned = bool(rm.get("pinned"))
        name = "Resume the session (pinned \U0001F4CC)" if pinned else "Resume the session"
        when = rm.get("activity") or ""
        whenhtml = ('<span class="when">last activity %s</span>' % _e(when)) if when else ""
        actions.append(
            '<div class="action"><div class="lab"><span class="name">%s</span>%s</div>'
            '<div class="sub">jumps back into the original working session</div>'
            '<code class="cmd" style="%s">%s</code></div>'
            % (_e(name), whenhtml, _CMD_STYLE, _e(rm["command"])))
    if actions:
        out.append('<div class="actions">%s</div>' % "".join(actions))
    if workers:
        rows = []
        for w in workers:
            cmd = w.get("command")
            note = w.get("note")
            if cmd:
                suffix = ('  <span class="note">(%s)</span>' % _e(note)) if note else ""
                rows.append('<div class="worker"><span class="wlabel">%s</span>'
                            '<code class="cmd" style="%s">%s</code>%s</div>'
                            % (_e(w.get("label")), _CMD_STYLE, _e(cmd), suffix))
            else:
                rows.append('<div class="worker"><span class="wlabel">%s</span>'
                            '<span class="note">%s</span></div>'
                            % (_e(w.get("label")), _e(note or "no worker recorded yet")))
        out.append('<details class="workers"><summary>Workers (%d) — in-project, '
                   'you usually don’t resume these ▸</summary>%s</details>'
                   % (len(workers), "".join(rows)))
    return out


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
    link = '<a href="%s">%s</a>' % (_e(url), _e(url))
    if desc:
        link += ' <span class="d">— %s</span>' % _e(desc)
    return '<div class="pr">%s</div>' % link


def _brief_detail(t):
    # Digest-first: goal → next/standing → steps checklist (with rollup) →
    # decisions → repos → stored PRs → files. goal/state/decisions render through
    # mdlite (escaped first); steps/files/prs are structured.
    goal = t.get("goal")
    state = t.get("state")
    steps = t.get("steps") or []
    decisions = t.get("decisions") or []
    repos, prs, files = t.get("repos"), t.get("prs"), t.get("files")
    if not (goal or state or steps or decisions or repos or prs or files):
        return ""
    rows = ['<div class="brief">']
    if goal:
        rows.append('<div class="row"><span class="k">goal</span>'
                    '<span class="v">%s</span></div>' % _rich(goal))
    if state:
        rows.append('<div class="row"><span class="k">next / standing</span>'
                    '<span class="v">%s</span></div>' % _rich(state))
    if steps:
        done = sum(1 for s in steps if s.get("done"))
        items = "".join(
            '<li class="%s">%s %s</li>'
            % ("done" if s.get("done") else "todo",
               "✓" if s.get("done") else "☐", _e(s.get("text", "")))
            for s in steps)
        rows.append('<div class="row"><span class="k">steps %d/%d</span>'
                    '<ul class="steps">%s</ul></div>' % (done, len(steps), items))
    if decisions:
        items = "".join('<li>%s</li>' % _rich(d) for d in decisions)
        rows.append('<div class="row"><span class="k">decisions</span>'
                    '<ul class="decisions">%s</ul></div>' % items)
    if repos:
        rows.append('<div class="row"><span class="k">repos</span>'
                    '<span class="v">%s</span></div>' % _e(", ".join(repos)))
    if prs:
        items = "".join(_pr_line(p) for p in prs)
        rows.append('<div class="row"><span class="k">prs</span>'
                    '<div class="prs">%s</div></div>' % items)
    if files:
        items = "".join('<div><span>%s</span> <span class="d">— %s</span></div>'
                        % (_e(b), _e(d)) for b, d in files)
        rows.append('<div class="row"><span class="k">files</span>'
                    '<div class="files">%s</div></div>' % items)
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


def _row(t, theme, variant):
    pal = _palette_for(t.get("color"), theme, variant)
    accent_fb = _cat_accent(pal, "var(--accent)")
    bg_fb = _cat_bg(pal)
    # the left stripe is the category BG (req 7): the per-variant --cat-bg variable,
    # with the resolved-variant bg hex as the inline (no-JS) fallback.
    stripe = "var(--cat-bg,%s)" % (bg_fb if bg_fb else "var(--accent)")
    catcls = (" " + _cat_class(t.get("color"))) if t.get("color") else ""
    closed = " closed" if t.get("status") == "closed" else ""
    seq = t.get("seq")
    seqcell = ('#%s' % _e(seq)) if seq is not None else ""

    # Lead with the FULL untruncated title (req 1), then the at-a-glance DIGEST,
    # then the Open/Resume actions, the Workers subsection, and the Summary LAST.
    detail = ['<div class="detail">']
    full = t.get("full_title") or t.get("title") or ""
    if full:
        detail.append('<div class="fulltitle">%s</div>' % _e(full))
    brief = _brief_detail(t)
    if brief:
        detail.append(brief)
    detail.extend(_resume_detail(t))
    detail.append('<div><div class="k">Summary</div><div class="summary">%s</div></div>'
                  % _summary_html(t.get("summary")))
    detail.append('</div>')

    return (
        '<details class="row%s%s" style="border-left-color:%s">'
        '<summary class="rowsum">%s'
        '<span class="c-seq">%s</span>'
        '<span class="c-task"><span class="disc">▸</span>'
        '<span class="ttl">%s</span>%s</span>'
        '%s%s'
        '<span class="c-act">%s</span></summary>'
        '%s</details>'
        % (closed, catcls, stripe, _status_cell(t), seqcell, _e(t.get("title")),
           _progress_chip(t),
           _tag_cell(t, accent_fb, bg_fb), _effort_cell(t), _e(t.get("activity") or ""),
           "".join(detail))
    )


def _section(title, tasks, theme, variant):
    out = ['<div class="sec"><h2>%s</h2><span class="count">%d</span></div>'
           % (_e(title), len(tasks))]
    if not tasks:
        out.append('<div class="empty">No %s tasks.</div>' % _e(title.lower()))
        return out
    out.append('<div class="board">')
    out.append('<div class="head"><span class="c-status">status</span>'
               '<span class="c-seq">#</span><span class="c-task">task</span>'
               '<span class="c-cat">category</span><span class="c-eff">effort</span>'
               '<span class="c-act">activity</span></div>')
    out.extend(_row(t, theme, variant) for t in tasks)
    out.append('</div>')
    return out


def _help_panel(commands, config_rows, variant_label):
    """The bottom help: the /todo commands (reused from _COMMANDS_HELP) + the current
    config (config.board_rows()), each a compact read-only table."""
    if not (commands or config_rows):
        return []
    out = ['<div class="help">',
           '<div class="sec"><h2>Help</h2><span class="count">commands &amp; config</span></div>',
           '<div class="panels">']
    if commands:
        rows = "".join(
            '<tr><td class="key">%s</td><td class="val">%s</td></tr>' % (_e(c), _e(d))
            for c, d in commands)
        out.append('<div class="panel"><h3>Commands</h3><table class="kv">%s</table></div>' % rows)
    if config_rows:
        trs = []
        for r in config_rows:
            flag = r[0]
            value = r[1] if len(r) > 1 else ""
            if flag == "--reset":
                continue
            if flag == "--tint-theme" and variant_label:
                value = "%s → %s" % (value, variant_label)
            trs.append('<tr><td class="key">%s</td><td class="val mono">%s</td></tr>'
                       % (_e(flag.lstrip("-")), _e(value)))
        out.append('<div class="panel"><h3>Current config</h3><table class="kv">%s</table></div>'
                   % "".join(trs))
    out.append('</div></div>')
    return out


def _theme_init_script(default_variant):
    """A TINY inline script run in <head> BEFORE paint: set documentElement's
    data-theme from localStorage (the persisted choice survives the meta-refresh,
    req 4), falling back to the config's resolved variant. No external asset."""
    d = default_variant if default_variant in _PAGE else "dark"
    return ('<script>(function(){try{var s=localStorage.getItem(%r);'
            'document.documentElement.setAttribute("data-theme",'
            '(s==="dark"||s==="light")?s:%r);}catch(e){'
            'document.documentElement.setAttribute("data-theme",%r);}})();</script>'
            % (_THEME_KEY, d, d))


def _behavior_script():
    """The end-of-body inline script: (1) the light/dark TOGGLE — flips data-theme,
    persists to localStorage so the auto-refresh can't reset it (req 4); (2) the
    hover AUTO-SCROLL — when a collapsed title overflows, scroll it left→right on
    hover and reset on leave (req 2). Smooth, no layout shift, no external asset."""
    return (
        "<script>(function(){"
        # --- theme toggle ---
        "var b=document.getElementById('theme-toggle');"
        "function lab(){if(b)b.textContent=(document.documentElement.getAttribute('data-theme')==='light')?'\\u25D0 light':'\\u25D1 dark';}"
        "lab();"
        "if(b){b.addEventListener('click',function(){"
        "var cur=document.documentElement.getAttribute('data-theme')==='light'?'light':'dark';"
        "var nx=cur==='light'?'dark':'light';"
        "document.documentElement.setAttribute('data-theme',nx);"
        "try{localStorage.setItem('" + _THEME_KEY + "',nx);}catch(e){}lab();});}"
        # --- hover auto-scroll of overflowing collapsed titles ---
        "function anim(el,to){var start=el.scrollLeft,d=to-start;"
        "if(Math.abs(d)<1){el.scrollLeft=to;return;}"
        "var dur=Math.min(2400,Math.max(450,Math.abs(d)*7)),t0=null;"
        "if(el._raf)cancelAnimationFrame(el._raf);"
        "function step(ts){if(t0===null)t0=ts;var p=Math.min(1,(ts-t0)/dur);"
        "var e=p<0.5?2*p*p:1-Math.pow(-2*p+2,2)/2;"
        "el.scrollLeft=start+d*e;if(p<1){el._raf=requestAnimationFrame(step);}}"
        "el._raf=requestAnimationFrame(step);}"
        "var ts=document.querySelectorAll('.c-task .ttl');"
        "for(var i=0;i<ts.length;i++){(function(el){"
        "el.addEventListener('mouseenter',function(){var m=el.scrollWidth-el.clientWidth;if(m>2)anim(el,m);});"
        "el.addEventListener('mouseleave',function(){anim(el,0);});"
        "})(ts[i]);}"
        "})();</script>"
    )


def render_html(tasks, *, theme=None, variant=None, variant_label=None, generated="",
                commands=None, config_rows=None, board_autorefresh=False):
    """(Mostly) self-contained HTML board for the task view-models `tasks` (each a
    dict from task-station.py's _board_view_model). Open (not-closed) tasks first,
    then closed. theme/variant default to the active theme; theme picks per-category
    accent colours, variant sets the page's DEFAULT light/dark chrome (the toggle can
    override it). `commands` (the _COMMANDS_HELP list) and `config_rows`
    (config.board_rows()) drive the bottom help panel.

    Inline `<script>`/`<style>` ARE used (theme toggle + hover-scroll); there are
    NEVER any EXTERNAL assets (no src/link/@import/url(http)/remote fonts) — the
    board stays a single local file. When `board_autorefresh` is True (opt-in,
    default off) a `<meta http-equiv="refresh" content="5">` is added so an open
    tab reloads every 5s; the toggle's localStorage choice survives the reload."""
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
    # Opt-in only: a meta-refresh so an open tab reloads and picks up the Stop hook's
    # quiet regen. The toggle persists its choice to localStorage, so the reload
    # never resets the theme (req 4).
    if board_autorefresh:
        out.append('<meta http-equiv="refresh" content="5">')
    out += [
        "<title>task-station — board</title>",
        # set the theme attribute BEFORE the stylesheet paints (no flash).
        _theme_init_script(default_variant),
        "<style>%s</style>" % _css(default_variant, _category_css(tasks, theme)),
        '</head><body><div class="wrap">',
        '<div class="hdr"><div>'
        '<div class="kicker">task-station · /todo board</div>'
        "<h1>/todo board</h1></div>"
        '<button id="theme-toggle" class="toggle" type="button" '
        'aria-label="Toggle light/dark theme">theme</button></div>',
        '<p class="lede">%d task(s) — the same Open / Closed grid as the terminal '
        "<code>/todo</code> board; expand any row for its full title, summary, "
        "open/resume commands and briefing.</p>" % len(tasks),
    ]
    if not tasks:
        out.append('<div class="empty">No tasks yet.</div>')
    else:
        out.extend(_section("Open", open_tasks, theme, variant))
        out.extend(_section("Closed", closed_tasks, theme, variant))
    out.extend(_help_panel(commands, config_rows, variant_label))
    snap = "generated %s · " % _e(generated) if generated else ""
    if board_autorefresh:
        note = ('%sauto-refreshing every 5s · <code>config --board-autorefresh off</code> '
                "to stop." % snap)
    else:
        note = ("%sthis board is a static snapshot — re-run <code>/todo board</code> "
                "to refresh." % snap)
    out.append('<div class="snapshot">%s</div>' % note)
    out.append(_behavior_script())
    out.append("</div></body></html>")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    # Standalone rendering needs the store; that lives in task-station.py. Use the
    # CLI entrypoint instead so view-models are built consistently.
    sys.stderr.write("Run `python3 lib/task-station.py board` to render the board.\n")
    sys.exit(2)
