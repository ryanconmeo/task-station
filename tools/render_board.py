#!/usr/bin/env python3
"""Render a self-contained HTML board of all Task Station tasks.

Mirrors tools/render_palettes.py: ONE static HTML file, inline CSS, NO server, NO
external assets, NO deps beyond the stdlib + the optional `categories` module
(for the ACTIVE theme's per-category palette). NO LLM, no network — every value
comes from the task view-models task-station.py hands in.

`task-station.py board` builds the view-models and calls render_html(); this
module only turns them into HTML, so it's import-safe and unit-testable on plain
dicts. Each task is a colour card carrying status glyph · #seq · title · [TAG]
category (category colour) · effort gauge · last activity, plus the briefing
(state/next-step · repos · PR links · recent files) and the resume one-liner."""
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

_CSS = """
  :root{
    --page:#0d0e11; --panel:#16181d; --panel2:#1b1e24; --ink:#e8e6e0;
    --dim:#8b8f99; --line:#262a31; --accent:#d7b5fb;
    --mono:ui-monospace,"SF Mono",Menlo,"Cascadia Code",Consolas,monospace;
    --sans:"Inter",system-ui,-apple-system,"Segoe UI",sans-serif;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  html,body{max-width:100%;overflow-x:hidden}
  body{background:var(--page);color:var(--ink);font-family:var(--sans);
    -webkit-font-smoothing:antialiased;line-height:1.5;padding:40px 28px 80px}
  .wrap{max-width:1180px;margin:0 auto}
  .kicker{font-family:var(--mono);font-size:12px;letter-spacing:.22em;text-transform:uppercase;color:var(--dim)}
  h1{font-size:28px;font-weight:650;letter-spacing:-.02em;margin:6px 0 6px}
  .lede{color:var(--dim);font-size:14px;max-width:74ch}
  .sec{display:flex;align-items:baseline;gap:12px;margin:34px 0 14px;padding-bottom:8px;border-bottom:1px solid var(--line)}
  .sec h2{font-size:19px;font-weight:650;letter-spacing:-.01em}
  .sec .count{font-family:var(--mono);font-size:12.5px;color:var(--dim)}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:18px;align-items:start}
  .card{border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:13px;
    overflow:hidden;background:var(--panel);min-width:0}
  .card.closed{opacity:.62}
  .card .head{display:flex;align-items:center;gap:9px;padding:12px 14px 9px}
  .card .glyph{font-size:15px;line-height:1;color:var(--accent)}
  .card .seq{font-family:var(--mono);font-size:12px;color:var(--dim)}
  .card .title{font-weight:600;font-size:15px;letter-spacing:-.01em;
    overflow-wrap:anywhere;flex:1;min-width:0}
  .card .sub{display:flex;flex-wrap:wrap;align-items:center;gap:6px 12px;
    padding:0 14px 11px;font-family:var(--mono);font-size:11.5px;color:var(--dim)}
  .tag{font-family:var(--mono);font-weight:650;font-size:11px;letter-spacing:.04em;
    border:1px solid currentColor;border-radius:99px;padding:1px 8px}
  .eff{letter-spacing:1px}
  .brief{border-top:1px solid var(--line);background:var(--panel2);padding:11px 14px;
    font-size:12.5px;display:grid;gap:8px}
  .brief .state{color:var(--ink);background:rgba(255,255,255,.04);
    border-left:3px solid var(--accent);border-radius:5px;padding:7px 9px;overflow-wrap:anywhere}
  .brief .row{display:flex;gap:8px;flex-wrap:wrap;align-items:baseline}
  .brief .k{font-family:var(--mono);font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;
    color:var(--dim);flex:0 0 auto}
  .brief .v{min-width:0;overflow-wrap:anywhere;color:var(--ink)}
  .brief a{color:var(--accent);overflow-wrap:anywhere}
  .brief .files{font-family:var(--mono);font-size:11px;color:var(--ink);display:grid;gap:2px}
  .brief .files .d{color:var(--dim)}
  details{border-top:1px solid var(--line)}
  summary{cursor:pointer;list-style:none;padding:9px 14px;font-family:var(--mono);
    font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--dim)}
  summary::-webkit-details-marker{display:none}
  details[open] summary{color:var(--accent)}
  pre{font-family:var(--mono);font-size:11.5px;line-height:1.55;background:#0b0c0f;
    color:var(--ink);padding:10px 12px;margin:0 14px 12px;border-radius:7px;
    border:1px solid var(--line);overflow-x:auto;max-width:calc(100% - 28px);
    white-space:pre-wrap;overflow-wrap:anywhere}
  .empty{color:var(--dim);font-style:italic;padding:24px 0}
"""


def _e(s):
    return html.escape(str(s if s is not None else ""), quote=True)


def _accent_for(color, theme, variant):
    """The category's accent hex for the card's left border / tag, from the active
    theme palette. Falls back to a neutral accent when categories/palette absent."""
    if not color or _cats is None or not hasattr(_cats, "theme_palette"):
        return "#d7b5fb"
    try:
        pal = _cats.theme_palette(theme, color, variant)
    except Exception:
        pal = None
    if isinstance(pal, dict):
        return pal.get("bold") or pal.get("fg") or "#d7b5fb"
    return "#d7b5fb"


def _brief(t):
    """The briefing sub-block (state · repos · PRs · recent files), or '' when the
    task carries no briefing at all."""
    state, repos, prs, files = t.get("state"), t.get("repos"), t.get("prs"), t.get("files")
    if not (state or repos or prs or files):
        return ""
    rows = ['<div class="brief">']
    if state:
        rows.append('<div class="state">%s</div>' % _e(state))
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


def _resume_block(t):
    """The collapsible resume one-liner(s) — the hub resume command plus any
    in-project worker lines — wrapped so long commands never widen the page."""
    resume, workers = t.get("resume"), t.get("workers") or []
    if not resume and not workers:
        return ""
    body = []
    if resume:
        body.append(_e(resume))
    for w in workers:
        body.append(_e(w))
    return ('<details><summary>resume ▸</summary>'
            '<pre>%s</pre></details>' % "\n".join(body))


def _card(t, theme, variant):
    accent = _accent_for(t.get("color"), theme, variant)
    closed = " closed" if t.get("status") == "closed" else ""
    seq = t.get("seq")
    seqcell = ('<span class="seq">#%s</span>' % _e(seq)) if seq is not None else ""
    tag = t.get("tag")
    tagcell = ('<span class="tag" style="color:%s">%s</span>' % (_e(accent), _e(tag))) if tag else ""
    eff = t.get("effort_gauge") or ""
    effcell = ('<span class="eff">%s</span> %s' % (_e(eff), _e((t.get("effort") or "").upper()))) if eff else ""
    return (
        '<div class="card%s" style="border-left-color:%s">'
        '<div class="head"><span class="glyph">%s</span>%s'
        '<span class="title">%s</span></div>'
        '<div class="sub">%s%s<span>%s</span></div>'
        '%s%s</div>'
        % (closed, _e(accent), _e(t.get("glyph") or ""), seqcell, _e(t.get("title")),
           tagcell, effcell, _e(t.get("activity") or ""),
           _brief(t), _resume_block(t))
    )


def _section(title, tasks, theme, variant):
    out = ['<div class="sec"><h2>%s</h2><span class="count">%d</span></div>'
           % (_e(title), len(tasks))]
    if not tasks:
        out.append('<div class="empty">No %s tasks.</div>' % _e(title.lower()))
        return out
    out.append('<div class="grid">')
    out.extend(_card(t, theme, variant) for t in tasks)
    out.append('</div>')
    return out


def render_html(tasks, *, theme=None, variant=None, generated=""):
    """Self-contained HTML board for the task view-models `tasks` (each a dict from
    task-station.py's _board_view_model). Open (not-closed) tasks first, then
    closed. theme/variant default to the active theme; both are only used to pick
    per-category accent colours."""
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
    open_tasks = [t for t in tasks if t.get("status") != "closed"]
    closed_tasks = [t for t in tasks if t.get("status") == "closed"]

    out = [
        "<!doctype html>", '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>Task Station — board</title>",
        "<style>%s</style>" % _CSS,
        '</head><body><div class="wrap">',
        '<div class="kicker">Task Station · board</div>',
        "<h1>Task board</h1>",
        '<p class="lede">%d task(s) — every topic, its briefing (next step · repos · '
        "PRs · recent files) and one-command resume.%s</p>"
        % (len(tasks), (" Generated %s." % _e(generated)) if generated else ""),
    ]
    if not tasks:
        out.append('<div class="empty">No tasks yet.</div>')
    else:
        out.extend(_section("Open", open_tasks, theme, variant))
        out.extend(_section("Closed", closed_tasks, theme, variant))
    out.append("</div></body></html>")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    # Standalone rendering needs the store; that lives in task-station.py. Use the
    # CLI entrypoint instead so view-models are built consistently.
    sys.stderr.write("Run `python3 lib/task-station.py board` to render the board.\n")
    sys.exit(2)
