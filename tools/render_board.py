#!/usr/bin/env python3
"""Render a self-contained HTML board of all task-station tasks.

Mirrors the terminal `/todo` board: TWO sections (Open, then Closed), each a GRID
with the same columns — status · # · Task · Category · Effort · Activity — and
every row EXPANDABLE via native <details>/<summary> (NO JS). A row with steps carries a
compact progress bar + N/M in its Task cell. The expanded row leads with the structured
digest (goal · next/standing · steps checklist with rollup · decisions · repos · PRs ·
files), then the hub/pinned resume one-liner (with its last-activity time) and a
de-emphasised Workers subsection, and LAST the full task summary.

Constraints (same as tools/render_palettes.py): ONE static HTML file, inline CSS,
NO server, NO external assets / http(s) references, NO deps beyond the stdlib + the
optional `categories` module (for the ACTIVE theme's per-category palette), NO LLM,
no network. Every value comes from the view-models task-station.py hands in, so this
module is import-safe and unit-testable on plain dicts."""
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

# Page chrome per RESOLVED variant — a light variant gives a light page, a dark
# variant a dark page (req 6). Warm neutrals that complement the shipped Sands
# palette. Status colours are variant-aware too so the open/active/closed pills
# stay legible on either page. Per-CATEGORY colour still comes from theme_palette.
_PAGE = {
    "dark": {
        "page": "#0d0e11", "panel": "#16181d", "panel2": "#1b1e24", "code": "#0b0c0f",
        "ink": "#e8e6e0", "dim": "#8b8f99", "line": "#262a31", "accent": "#d7b5fb",
        "so": "#5bc8f5", "sob": "rgba(91,200,245,.14)",
        "sa": "#b6e85a", "sab": "rgba(182,232,90,.16)",
        "sc": "#9aa0ab", "scb": "rgba(154,160,171,.14)",
    },
    "light": {
        "page": "#f3efe7", "panel": "#fbf8f2", "panel2": "#ece7db", "code": "#fffdf8",
        "ink": "#2b2823", "dim": "#6c665c", "line": "#dcd5c8", "accent": "#7a4fb0",
        "so": "#1d6fa5", "sob": "rgba(29,111,165,.12)",
        "sa": "#3c7a16", "sab": "rgba(60,122,22,.13)",
        "sc": "#6c665c", "scb": "rgba(108,102,92,.13)",
    },
}

# The grid column template, shared by the header row and every <summary> so the
# columns line up across the (separate) grid containers — alignment across grids
# needs fixed/fr tracks, never `auto`. status · # · Task · Category · Effort · Activity.
_COLS = "94px 52px minmax(0,1fr) 168px 132px 96px"
_COLS_NARROW = "78px 42px minmax(0,1fr)"   # task only on a narrow viewport

_STATUS_GLYPH = {"open": "○", "active": "●", "closed": "✕"}

# resume / worker commands must never wrap — they scroll within their own box
# instead of widening the page (req 5). Applied inline so the style sits ON the
# element, not just in a stylesheet rule.
_CMD_STYLE = "white-space:nowrap;overflow-x:auto"


def _css(pg):
    # The :root block is %-formatted from the page palette (no literal % inside it);
    # the body below is a PLAIN string (it contains literal `100%`), with the narrow
    # column template concatenated in — so no %-formatting touches the CSS body.
    root = """
  :root{
    --page:%(page)s; --panel:%(panel)s; --panel2:%(panel2)s; --code:%(code)s;
    --ink:%(ink)s; --dim:%(dim)s; --line:%(line)s; --accent:%(accent)s;
    --so:%(so)s; --sob:%(sob)s; --sa:%(sa)s; --sab:%(sab)s; --sc:%(sc)s; --scb:%(scb)s;
    --cols:%(cols)s;
    --mono:ui-monospace,"SF Mono",Menlo,"Cascadia Code",Consolas,monospace;
    --sans:"Inter",system-ui,-apple-system,"Segoe UI",sans-serif;
  }
""" % dict(pg, cols=_COLS)
    body = """
  *{box-sizing:border-box;margin:0;padding:0}
  html,body{max-width:100%;overflow-x:hidden}
  body{background:var(--page);color:var(--ink);font-family:var(--sans);
    -webkit-font-smoothing:antialiased;line-height:1.5;padding:40px 28px 60px}
  .wrap{max-width:1180px;margin:0 auto}
  .kicker{font-family:var(--mono);font-size:12px;letter-spacing:.06em;color:var(--dim)}
  h1{font-size:28px;font-weight:650;letter-spacing:-.02em;margin:6px 0 6px}
  .lede{color:var(--dim);font-size:14px;max-width:80ch}
  .sec{display:flex;align-items:baseline;gap:12px;margin:32px 0 10px;padding-bottom:8px;
    border-bottom:1px solid var(--line)}
  .sec h2{font-size:19px;font-weight:650;letter-spacing:-.01em}
  .sec .count{font-family:var(--mono);font-size:12.5px;color:var(--dim)}

  .board{border:1px solid var(--line);border-radius:12px;overflow:hidden;background:var(--panel)}
  .head,summary.rowsum{display:grid;grid-template-columns:var(--cols);align-items:center;
    gap:0 14px;padding:9px 14px}
  .head{font-family:var(--mono);font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;
    color:var(--dim);background:var(--panel2);border-bottom:1px solid var(--line)}
  details.row{border-bottom:1px solid var(--line);border-left:4px solid var(--accent)}
  details.row:last-child{border-bottom:none}
  details.row.closed{opacity:.62}
  summary.rowsum{cursor:pointer;list-style:none}
  summary.rowsum::-webkit-details-marker{display:none}
  summary.rowsum:hover{background:var(--panel2)}
  details.row[open]>summary.rowsum{background:var(--panel2);border-bottom:1px solid var(--line)}
  .c-task{font-weight:600;font-size:14.5px;letter-spacing:-.01em;overflow:hidden;
    text-overflow:ellipsis;white-space:nowrap;display:flex;align-items:center;gap:8px}
  .c-task .disc{color:var(--dim);font-size:11px;transition:transform .12s}
  details.row[open] .c-task .disc{transform:rotate(90deg)}
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

  /* status: a LABELED, clearly non-interactive pill — never a bare glyph (req 2) */
  .pill{display:inline-flex;align-items:center;gap:5px;cursor:default;font-family:var(--mono);
    font-weight:650;font-size:10.5px;letter-spacing:.04em;border-radius:99px;padding:2px 9px;
    border:1px solid currentColor;white-space:nowrap}
  .pill.open{color:var(--so);background:var(--sob)}
  .pill.active{color:var(--sa);background:var(--sab)}
  .pill.closed{color:var(--sc);background:var(--scb)}
  .tag{display:inline-flex;align-items:center;font-family:var(--mono);font-weight:650;
    font-size:11px;letter-spacing:.03em;border:1px solid currentColor;border-radius:99px;
    padding:1px 9px;white-space:nowrap}

  .detail{padding:14px 16px 16px;display:grid;gap:13px;background:var(--panel)}
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
  .pinned{display:inline-flex;align-items:center;gap:7px;font-family:var(--mono);font-size:11.5px;
    color:var(--accent);background:var(--panel2);border:1px solid var(--accent);border-radius:7px;
    padding:6px 10px;width:max-content;max-width:100%}
  .resume{border:1px solid var(--accent);border-radius:8px;background:var(--panel2);padding:11px 12px}
  .resume .lab{display:flex;flex-wrap:wrap;gap:6px 10px;align-items:baseline;margin-bottom:7px}
  .resume .lab .name{font-family:var(--mono);font-size:11.5px;font-weight:650;color:var(--accent);
    letter-spacing:.04em;text-transform:uppercase}
  .resume .lab .when{font-family:var(--mono);font-size:11px;color:var(--dim)}
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
    return root + body


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


def _accent_for(pal, fallback):
    """The category's accent hex (its terminal `bold` colour in this variant)."""
    if isinstance(pal, dict):
        return pal.get("bold") or pal.get("fg") or fallback
    return fallback


def _status_cell(t):
    st = t.get("status_label") or t.get("status") or "open"
    glyph = _STATUS_GLYPH.get(st, "")
    cls = st if st in ("open", "active", "closed") else "open"
    return ('<span class="c-status"><span class="pill %s">%s %s</span></span>'
            % (_e(cls), _e(glyph), _e(st)))


def _tag_cell(t, accent, pal):
    tag = t.get("tag")
    if not tag:
        return '<span class="c-cat"></span>'
    bg = pal.get("bg") if isinstance(pal, dict) else None
    style = "color:%s" % _e(accent)
    if bg:
        style += ";background:%s" % _e(bg)
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
    """The hub/pinned resume block (prominent) + a de-emphasised Workers subsection."""
    rm = t.get("resume_main")
    workers = t.get("workers") or []
    out = []
    if t.get("pinned"):
        out.append('<div class="pinned">\U0001F4CC Pinned — this task resumes its '
                   'pinned session</div>')
    if rm and rm.get("command"):
        when = rm.get("activity") or ""
        whenhtml = ('<span class="when">last activity %s</span>' % _e(when)) if when else ""
        out.append(
            '<div class="resume"><div class="lab"><span class="name">%s</span>%s</div>'
            '<code class="cmd" style="%s">%s</code></div>'
            % (_e(rm.get("label") or "Resume (hub)"), whenhtml, _CMD_STYLE, _e(rm["command"])))
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
        links = " · ".join('<a href="%s">%s</a>' % (_e(u), _e(u)) for u in prs)
        rows.append('<div class="row"><span class="k">prs</span>'
                    '<span class="v">%s</span></div>' % links)
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
    accent = _accent_for(pal, "var(--accent)")
    closed = " closed" if t.get("status") == "closed" else ""
    seq = t.get("seq")
    seqcell = ('#%s' % _e(seq)) if seq is not None else ""

    # Lead with the at-a-glance DIGEST (briefing: next/standing · files · PRs · repos),
    # THEN resume (hub/pinned + workers), THEN the full Summary LAST — the eye hits the
    # scannable digest before the wall of text.
    detail = ['<div class="detail">']
    brief = _brief_detail(t)
    if brief:
        detail.append(brief)
    detail.extend(_resume_detail(t))
    detail.append('<div><div class="k">Summary</div><div class="summary">%s</div></div>'
                  % _summary_html(t.get("summary")))
    detail.append('</div>')

    return (
        '<details class="row%s" style="border-left-color:%s">'
        '<summary class="rowsum">%s'
        '<span class="c-seq">%s</span>'
        '<span class="c-task"><span class="disc">▸</span>%s%s</span>'
        '%s%s'
        '<span class="c-act">%s</span></summary>'
        '%s</details>'
        % (closed, _e(accent), _status_cell(t), seqcell, _e(t.get("title")),
           _progress_chip(t),
           _tag_cell(t, accent, pal), _effort_cell(t), _e(t.get("activity") or ""),
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


def render_html(tasks, *, theme=None, variant=None, variant_label=None, generated="",
                commands=None, config_rows=None, board_autorefresh=False):
    """Self-contained HTML board for the task view-models `tasks` (each a dict from
    task-station.py's _board_view_model). Open (not-closed) tasks first, then closed.
    theme/variant default to the active theme; they pick per-category accent colours
    AND the page's light/dark chrome. `commands` (the _COMMANDS_HELP list) and
    `config_rows` (config.board_rows()) drive the bottom help panel.

    When `board_autorefresh` is True (opt-in, default off) the ONLY non-static element
    on the page is added: a `<meta http-equiv="refresh" content="5">` tag so an open
    tab reloads every 5s and the Stop hook's quiet regen shows current state. There is
    never any JavaScript or external asset — the board stays fully self-contained."""
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
    pg = _PAGE.get(variant if variant in _PAGE else "dark", _PAGE["dark"])
    open_tasks = [t for t in tasks if t.get("status") != "closed"]
    closed_tasks = [t for t in tasks if t.get("status") == "closed"]

    out = [
        "<!doctype html>", '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
    ]
    # The sole non-static element, only when the user opted in: a meta-refresh so an
    # open tab reloads and picks up the Stop hook's quiet regen. No JS, no network.
    if board_autorefresh:
        out.append('<meta http-equiv="refresh" content="5">')
    out += [
        "<title>task-station — board</title>",
        "<style>%s</style>" % _css(pg),
        '</head><body><div class="wrap">',
        '<div class="kicker">task-station · board</div>',
        "<h1>task board</h1>",
        '<p class="lede">%d task(s) — the same Open / Closed grid as the terminal '
        "<code>/todo</code> board; expand any row for its summary, one-command resume "
        "and briefing.</p>" % len(tasks),
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
    out.append("</div></body></html>")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    # Standalone rendering needs the store; that lives in task-station.py. Use the
    # CLI entrypoint instead so view-models are built consistently.
    sys.stderr.write("Run `python3 lib/task-station.py board` to render the board.\n")
    sys.exit(2)
