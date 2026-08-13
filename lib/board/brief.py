"""Deterministic brief renderer — the house-style HTML one-pager.

PURE STDLIB. Imports NOTHING from task-station: the model produces a structured
brief-spec (JSON), NEVER raw HTML, and this core templates the frozen house style
(lib/brief_template.html). → identical output under any host, no style drift,
unit-testable, trivially regenerable. Glossary term highlighting is applied
mechanically (the model never hand-tags terms).

Brief-spec shape (all sections optional except title + decision + glossary +
provenance):
    {
      "title": str, "subtitle": str,
      "decision":   {"label": str, "body": str},
      "transition": {"today": SIDE, "goal": SIDE},   SIDE={"label","name","lines":[str]}
      "diagrams":   [ {"type":"svg"|"matrix"|"architecture", "title","caption","intro", …} ],
      "glossary":   "auto" | [ {"name","layer","state","def"} ],
      "one_rule":   str,
      "plan":       [ {"state":"done"|"1"|…, "title": str, "body": str} ],
      "ado_tree":   [ NODE ],   NODE={"type","id","url","title","verb":"add|change|remove",
                                       "does","state","children":[NODE]}
      "provenance": str,
    }
"""
import html
import os
import re

# lib/board/ is one level deeper than lib/
_TEMPLATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brief_template.html")

# The one house-style verb → pill-class map (unknown verbs render class-less).
_VERBS = {"add", "change", "remove"}


def _template():
    with open(_TEMPLATE_PATH, encoding="utf-8") as f:
        return f.read()


def _esc(s):
    """HTML-escape a value (quotes included), coercing None to ''."""
    return html.escape("" if s is None else str(s), quote=True)


def highlight_terms(text, names):
    """HTML-escape `text`, then wrap every glossary `name` occurrence in a
    <span class="term">…</span>. Longest-first alternation so a longer term wins over
    a shorter substring; the ESCAPED name is matched against the ESCAPED text so a
    term with markup-significant chars (e.g. '(BIN2)') still matches. Deterministic."""
    esc = _esc(text)
    names = [n for n in (names or []) if n and str(n).strip()]
    if not names:
        return esc
    ordered = sorted(set(names), key=len, reverse=True)
    alt = "|".join(re.escape(_esc(n)) for n in ordered)
    return re.sub(alt, lambda m: '<span class="term">%s</span>' % m.group(0), esc)


def _h2(title):
    # Heading text: escape markup but leave quotes/apostrophes literal (text content,
    # not an attribute) so "where we're going" reads cleanly.
    return "<h2>%s</h2>" % html.escape("" if title is None else str(title), quote=False)


# --------------------------------------------------------------- sections --------

def _banner(decision, names):
    """The lead decision banner (required section)."""
    if not decision:
        return ""
    label = _esc(decision.get("label") or "Decision")
    body = highlight_terms(decision.get("body") or "", names)
    return ('<div class="banner">\n'
            '  <div class="k">%s</div>\n'
            '  <p>%s</p>\n'
            '</div>') % (label, body)


def _transition_side(side, names, good=False):
    """One card of the before→after transition."""
    if not side:
        return ""
    accent = "var(--good)" if good else "var(--accent)"
    bg = "var(--good-bg)" if good else "#fff"
    border = "#bfe4cd" if good else "var(--line)"
    label = _esc(side.get("label") or ("Goal" if good else "Today"))
    marker = "◎" if good else "●"
    name = _esc(side.get("name") or "")
    lines = "<br>\n      ".join(highlight_terms(l, names) for l in (side.get("lines") or []))
    return (
        '  <div style="flex:1;min-width:250px;background:%s;border:1px solid %s;'
        'border-radius:12px;padding:16px 18px;">\n'
        '    <div style="font-size:12px;text-transform:uppercase;letter-spacing:.06em;'
        'font-weight:700;color:%s;margin-bottom:8px;">%s %s</div>\n'
        '    <div style="font-weight:700;color:%s;margin-bottom:8px;">%s</div>\n'
        '    <div style="font-size:14px;color:var(--muted);line-height:1.8;">\n      %s\n    </div>\n'
        '  </div>') % (bg, border, accent if good else "var(--muted)", marker, label,
                       accent, name, lines)


def _transition(transition, names):
    """The 'where we are → where we're going' two-card section (optional)."""
    if not transition:
        return ""
    heading = transition.get("heading") or "Where we are → where we're going"
    today = _transition_side(transition.get("today"), names, good=False)
    goal = _transition_side(transition.get("goal"), names, good=True)
    arrow = ('  <div style="flex:0 0 auto;display:flex;align-items:center;'
             'justify-content:center;font-size:26px;color:var(--muted);padding:0 4px;">→</div>')
    inner = "\n".join(p for p in (today, arrow, goal) if p)
    return ('%s\n<div style="display:flex;flex-wrap:wrap;gap:12px;align-items:stretch;margin:14px 0;">\n'
            '%s\n</div>') % (_h2(heading), inner)


def _glossary(terms):
    """The vocabulary section from resolved glossary terms (required section)."""
    terms = [t for t in (terms or []) if isinstance(t, dict) and (t.get("name") or "").strip()]
    if not terms:
        return ""
    rows = []
    for t in terms:
        where = " · ".join([p for p in ((t.get("layer") or "").strip(),
                                        (t.get("state") or "").strip()) if p])
        where_html = ('<span class="where">%s</span>' % _esc(where)) if where else ""
        d = (t.get("def") or "").strip()
        d_html = ('<div class="d">%s</div>' % _esc(d)) if d else ""
        rows.append('  <div class="g"><span class="name">%s</span>%s%s</div>'
                    % (_esc(t.get("name")), where_html, d_html))
    return '%s\n<div class="glossary">\n%s\n</div>' % (_h2("The vocabulary"), "\n".join(rows))


def _one_rule(one_rule, names):
    """The single load-bearing rule (optional)."""
    if not one_rule:
        return ""
    return ('%s\n<div class="rule">\n  <p style="margin:0">%s</p>\n</div>'
            % (_h2("The one rule"), highlight_terms(one_rule, names)))


def _plan(plan, names):
    """The numbered plan with done/pending phase badges (optional)."""
    if not plan:
        return ""
    rows = []
    for step in plan:
        state = str(step.get("state") or "").strip()
        done = state.lower() in ("done", "✓", "shipped")
        num = "✓" if done else (state or "•")
        cls = "phase done" if done else "phase"
        title = _esc(step.get("title") or "")
        title_html = ('<span class="t">%s</span> ' % title) if title else ""
        body = highlight_terms(step.get("body") or "", names)
        rows.append('<div class="%s"><div class="num">%s</div>'
                    '<div class="b">%s%s</div></div>' % (cls, _esc(num), title_html, body))
    return "%s\n%s" % (_h2("The plan"), "\n".join(rows))


def _verb_pill(verb):
    v = (verb or "").strip().lower()
    if not v:
        return ""
    cls = ("verb %s" % v) if v in _VERBS else "verb"
    return '<span class="%s">%s</span>' % (cls, _esc(v))


def _ado_nodes(nodes, names):
    """Recursively render the Feature→Story→PR tree as nested <li> items."""
    items = []
    for n in (nodes or []):
        if not isinstance(n, dict):
            continue
        pill = _verb_pill(n.get("verb"))
        kind = ('<span class="kind">%s</span>' % _esc(n.get("type"))) if n.get("type") else ""
        title = _esc(n.get("title") or n.get("id") or "")
        url = (n.get("url") or "").strip()
        link = ('<a href="%s">%s</a>' % (_esc(url), title)) if url else title
        node = '  <div class="node">%s</div>' % " ".join(
            x for x in (pill, kind, link) if x)
        does = n.get("does")
        does_html = ('\n  <div class="does">%s</div>' % highlight_terms(does, names)) if does else ""
        children = n.get("children") or []
        child_html = ("\n  <ul>\n%s\n  </ul>" % _ado_nodes(children, names)) if children else ""
        items.append("<li>\n%s%s%s\n</li>" % (node, does_html, child_html))
    return "\n".join(items)


def _ado_tree(ado_tree, names):
    """The ADO structure section: a nested hyperlinked verb-tagged tree (optional)."""
    if not ado_tree:
        return ""
    return '%s\n<ul class="ado">\n%s\n</ul>' % (_h2("ADO structure"), _ado_nodes(ado_tree, names))


def _matrix_svg(d):
    """A parametrized 2×2 strategy matrix reproducing the source's hand-drawn look.
    d: {x_label, y_label, x:[c0,c1], y:[r0,r1],
        quadrants:[{pos:"tl|tr|bl|br", title, note, kind:"today|goal|avoid|neutral"}]}"""
    x = d.get("x") or ["", ""]
    y = d.get("y") or ["", ""]
    style = {"today": ("#eef3fc", "#2c5cc5"), "goal": ("#edf7f0", "#1f8a4c"),
             "avoid": ("#fbeeec", "#c0392b"), "neutral": ("#fff", "#c9d4e8")}
    boxes = {"tl": (150, 70), "tr": (370, 70), "bl": (150, 230), "br": (370, 230)}
    parts = [
        '<svg viewBox="0 0 600 430" role="img" aria-label="%s" '
        'style="width:100%%;height:auto;min-width:460px;font-family:inherit">' % _esc(d.get("title") or "matrix"),
        '<text x="26" y="215" transform="rotate(-90 26 215)" text-anchor="middle" '
        'font-size="11" font-weight="700" fill="#66707e">%s</text>' % _esc(d.get("y_label") or ""),
        '<text x="360" y="418" text-anchor="middle" font-size="11" font-weight="700" '
        'fill="#66707e">%s</text>' % _esc(d.get("x_label") or ""),
        '<text x="250" y="400" text-anchor="middle" font-size="12" fill="#66707e">%s</text>' % _esc(x[0]),
        '<text x="470" y="400" text-anchor="middle" font-size="12" fill="#66707e">%s</text>' % _esc(x[1] if len(x) > 1 else ""),
        '<text x="132" y="150" text-anchor="end" font-size="12" fill="#66707e">%s</text>' % _esc(y[0]),
        '<text x="132" y="300" text-anchor="end" font-size="12" fill="#66707e">%s</text>' % _esc(y[1] if len(y) > 1 else ""),
    ]
    for q in (d.get("quadrants") or []):
        bx, by = boxes.get(q.get("pos"), (150, 70))
        bg, stroke = style.get(q.get("kind"), style["neutral"])
        sw = "3" if q.get("kind") == "goal" else "1.5"
        parts.append('<rect x="%d" y="%d" width="200" height="130" rx="12" fill="%s" '
                     'stroke="%s" stroke-width="%s"/>' % (bx, by, bg, stroke, sw))
        parts.append('<text x="%d" y="%d" text-anchor="middle" font-size="15" font-weight="700" '
                     'fill="%s">%s</text>' % (bx + 100, by + 70, stroke, _esc(q.get("title") or "")))
        if q.get("note"):
            parts.append('<text x="%d" y="%d" text-anchor="middle" font-size="11" '
                         'fill="#66707e">%s</text>' % (bx + 100, by + 92, _esc(q.get("note"))))
    parts.append("</svg>")
    return "\n".join(parts)


def _architecture_svg(d):
    """A parametrized before→after architecture sketch. d: {today:{label,box:str},
    goal:{label,box:str}} — each side a single labelled stack (kept deliberately
    simple; richer diagrams use type:'svg' passthrough)."""
    def side(x, s, accent):
        return ('<text x="%d" y="26" text-anchor="middle" font-size="12" font-weight="700" '
                'fill="%s">%s</text>\n'
                '<rect x="%d" y="40" width="310" height="150" rx="12" fill="#fff" '
                'stroke="%s" stroke-width="1.5"/>\n'
                '<text x="%d" y="120" text-anchor="middle" font-size="13" font-weight="700" '
                'fill="%s">%s</text>' % (x + 155, accent, _esc((s or {}).get("label") or ""),
                                         x, accent, x + 155, accent, _esc((s or {}).get("box") or "")))
    return ('<svg viewBox="0 0 720 210" role="img" aria-label="%s" '
            'style="width:100%%;height:auto;min-width:600px;font-family:inherit">\n'
            '%s\n%s\n</svg>' % (_esc(d.get("title") or "architecture"),
                                side(30, d.get("today"), "#66707e"),
                                side(390, d.get("goal"), "#1f8a4c")))


def _one_diagram(d):
    """A single <figure> (optional h2 title, optional intro <p>, scrollable svg,
    optional figcaption). type: svg (raw passthrough) | matrix | architecture."""
    if not isinstance(d, dict):
        return ""
    dtype = (d.get("type") or "svg").lower()
    if dtype == "svg":
        svg = d.get("svg") or ""          # trusted raw passthrough (documented exception)
    elif dtype == "matrix":
        svg = _matrix_svg(d)
    elif dtype == "architecture":
        svg = _architecture_svg(d)
    else:
        svg = ""
    if not svg:
        return ""
    head = (_h2(d.get("title")) + "\n") if d.get("title") else ""
    intro = ('<p style="font-size:14px;color:var(--muted);margin-bottom:4px">%s</p>\n'
             % _esc(d.get("intro"))) if d.get("intro") else ""
    cap = ("\n  <figcaption>%s</figcaption>" % _esc(d.get("caption"))) if d.get("caption") else ""
    return '%s%s<figure>\n  <div class="scroll">\n  %s\n  </div>%s\n</figure>' % (head, intro, svg, cap)


def _diagrams(diagrams):
    """Zero or more diagram figures (optional section)."""
    if not diagrams:
        return ""
    return "\n".join(f for f in (_one_diagram(d) for d in diagrams) if f)


def _roster(sessions):
    """Session roster table — ordinal | kind | name/sid8 | model | status | spawned
    (task #463). Optional/data-gated: '' when no sessions are supplied, so a spec
    without provenance rows renders exactly as before."""
    rows = [s for s in (sessions or []) if isinstance(s, dict)]
    if not rows:
        return ""
    trs = []
    for s in rows:
        trs.append(
            '    <tr><td class="ord">%s</td><td class="kind">%s</td>'
            '<td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
                _esc(s.get("ordinal") or "—"), _esc(s.get("kind") or "—"),
                _esc(s.get("name") or "—"), _esc(s.get("model") or "—"),
                _esc(s.get("status") or "—"), _esc(s.get("spawned") or "—")))
    return ('%s\n<table class="roster">\n'
            '  <thead><tr><th>#</th><th>kind</th><th>name</th><th>model</th>'
            '<th>status</th><th>spawned</th></tr></thead>\n'
            '  <tbody>\n%s\n  </tbody>\n</table>'
            % (_h2("Sessions"), "\n".join(trs)))


def _ledger(ledger):
    """Recent hub↔worker interaction tail (task #463). Optional/data-gated: '' when
    no ledger entries are supplied."""
    rows = [e for e in (ledger or []) if isinstance(e, dict)]
    if not rows:
        return ""
    lis = []
    for e in rows:
        detail = (" — %s" % _esc(e.get("detail"))) if e.get("detail") else ""
        lis.append('  <li><span class="act">%s</span> %s → %s%s '
                   '<span class="when">%s</span></li>'
                   % (_esc(e.get("actor") or "?"), _esc(e.get("action") or "?"),
                      _esc(e.get("worker") or "?"), detail, _esc(e.get("when") or "")))
    return '%s\n<ul class="ledger">\n%s\n</ul>' % (
        _h2("Recent worker activity"), "\n".join(lis))


def _provenance(provenance, names):
    """The provenance footer (required section)."""
    if not provenance:
        return ""
    return '<div class="foot">\n  %s\n</div>' % highlight_terms(provenance, names)


# --------------------------------------------------------------- assembly --------

def _resolve_glossary(spec, glossary):
    """The glossary terms to render: spec['glossary']=='auto' pulls the passed
    `glossary`; a spec-inline list is used verbatim; anything else → no terms."""
    g = spec.get("glossary")
    if g == "auto":
        return list(glossary or [])
    if isinstance(g, list):
        return g
    return []


def render_brief(spec, glossary=None):
    """Render a brief-spec dict to the frozen house-style HTML. `spec` is the
    model-supplied structured intermediate — NEVER raw HTML. `glossary` is the task's
    resolved term list, pulled in when spec['glossary']=='auto'. Returns the full
    HTML document as a string."""
    spec = spec or {}
    terms = _resolve_glossary(spec, glossary)
    names = [t.get("name") for t in terms if isinstance(t, dict) and t.get("name")]

    title = spec.get("title") or ""
    parts = ["<h1>%s</h1>" % _esc(title)]
    if spec.get("subtitle"):
        parts.append('<p class="sub">%s</p>' % _esc(spec.get("subtitle")))
    parts.append(_banner(spec.get("decision"), names))
    parts.append(_transition(spec.get("transition"), names))
    parts.append(_glossary(terms))
    parts.append(_diagrams(spec.get("diagrams")))
    parts.append(_one_rule(spec.get("one_rule"), names))
    parts.append(_plan(spec.get("plan"), names))
    parts.append(_ado_tree(spec.get("ado_tree"), names))
    parts.append(_roster(spec.get("sessions")))         # #463 provenance (data-gated)
    parts.append(_ledger(spec.get("ledger")))           # #463 worker interaction tail
    parts.append(_provenance(spec.get("provenance"), names))

    body = "\n\n".join(p for p in parts if p)
    return _template().replace("{TITLE}", _esc(title)).replace("{BODY}", body)
