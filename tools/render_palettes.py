#!/usr/bin/env python3
"""Render a self-contained HTML gallery of the Task Station THEMES.

Data-driven from `categories.effective_themes()` (shipped THEMES + any user
overrides / brand-new named themes) and the `categories.CATEGORIES` taxonomy
(dot/[TAG]/name). Each theme is a section of cards; each card is a real terminal
scene over the theme's background — comment · command (bold word) · prose ·
selected span · ERR/warn/ok · cursor — plus the 16-ANSI strip and the
bg/fg/bold/sel hexes.

  python3 tools/render_palettes.py            # HTML to stdout
  python3 tools/render_palettes.py --out x.html

`config --theme preview` imports this and calls render_html()."""
import html
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import categories  # noqa: E402

_CSS = """
  :root{
    --page:#0d0e11; --panel:#16181d; --ink:#e8e6e0; --dim:#8b8f99; --line:#262a31;
    --mono:ui-monospace,"SF Mono",Menlo,"Cascadia Code",Consolas,monospace;
    --sans:"Inter",system-ui,-apple-system,"Segoe UI",sans-serif;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--page);color:var(--ink);font-family:var(--sans);
    -webkit-font-smoothing:antialiased;line-height:1.5;padding:48px 32px 80px}
  .wrap{max-width:1180px;margin:0 auto}
  .kicker{font-family:var(--mono);font-size:12px;letter-spacing:.22em;text-transform:uppercase;color:var(--dim)}
  h1{font-size:30px;font-weight:650;letter-spacing:-.02em;margin:6px 0 10px}
  .lede{color:var(--dim);max-width:74ch;font-size:15px}
  .sec{display:flex;align-items:baseline;gap:12px;margin:40px 0 16px;padding-bottom:8px;border-bottom:1px solid var(--line)}
  .sec h2{font-size:21px;font-weight:650;letter-spacing:-.01em}
  .sec .tagline{font-family:var(--mono);font-size:12.5px;color:var(--dim)}
  .sec .badge{font-family:var(--mono);font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;
    background:#2a2330;color:#d7b5fb;border:1px solid #43354f;border-radius:99px;padding:2px 9px}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:20px}
  .card{border:1px solid var(--line);border-radius:14px;overflow:hidden;background:var(--panel)}
  .card .head{display:flex;align-items:center;gap:9px;padding:11px 14px;border-bottom:1px solid var(--line)}
  .card .dot{font-size:15px;line-height:1}
  .card .tag{font-family:var(--mono);font-weight:650;font-size:13px;letter-spacing:.04em}
  .card .nm{font-family:var(--mono);color:var(--dim);font-size:12px;margin-left:auto}
  .term{font-family:var(--mono);font-size:12.5px;line-height:1.62;padding:14px 15px 13px;white-space:pre-wrap;word-break:break-word}
  .term .b{font-weight:700}
  .term .selspan{border-radius:2px;padding:0 1px}
  .cursor{display:inline-block;width:7px;height:14px;vertical-align:-2px;border-radius:1px}
  .ansi{display:flex;gap:0;margin-top:11px;border-radius:5px;overflow:hidden;border:1px solid rgba(127,127,127,.18)}
  .ansi i{flex:1;height:16px}
  .meta{display:flex;flex-wrap:wrap;gap:4px 12px;padding:9px 15px 13px;font-family:var(--mono);font-size:10.5px;color:var(--dim)}
  .meta .sw{display:inline-block;width:9px;height:9px;border-radius:2px;vertical-align:-1px;margin-right:4px;border:1px solid rgba(255,255,255,.18)}
"""

# Per-variant section chrome (each theme renders both halves).
_VARIANT_CHROME = {
    "dark":  ("dark", "muted · legible cream prose · soft accents"),
    "light": ("light", "vibrant · saturated · bright accents"),
}


def _e(s):
    return html.escape(str(s), quote=True)


def _card(meta, pal):
    """One category card: header + terminal scene + 16-ANSI strip + hex meta."""
    bg, fg = pal.get("bg", "#000000"), pal.get("fg", "#e8e6e0")
    bold = pal.get("bold", fg)
    sel = pal.get("sel", "#444444")
    ansi = list(pal.get("ansi", [])) + ["#000000"] * 16
    c1, c2, c4, c5, c8 = ansi[1], ansi[2], ansi[4], ansi[5], ansi[8]
    dot, tag, nm = meta["dot"], meta["tag"], meta["_key"]
    term = (
        '<span style="color:%s"># %s — %s work</span>\n' % (_e(c8), _e(nm), _e(tag.lower()))
        + '<span style="color:%s">~/proj</span> <span style="color:%s">git</span> '
          'commit -m "<span class="b" style="color:%s">fix</span>: redirect loop"\n'
          % (_e(c2), _e(c4), _e(bold))
        + 'the quick brown fox — <span class="b" style="color:%s">bold emphasis</span> reads clearly\n'
          % _e(bold)
        + '<span class="selspan" style="background:%s;color:%s">a selected range of text</span> stands out\n'
          % (_e(sel), _e(fg))
        + '<span style="color:%s">ERR</span> <span style="color:%s">warn</span> '
          '<span style="color:%s">ok</span> ok ok<span class="cursor" style="background:%s"></span>'
          % (_e(c1), _e(c5), _e(c2), _e(bold))
    )
    strip = "".join('<i style="background:%s"></i>' % _e(h) for h in ansi[:16])
    return (
        '<div class="card">\n'
        '  <div class="head"><span class="dot">%s</span><span class="tag">[%s]</span>'
        '<span class="nm">%s</span></div>\n'
        '  <div class="term" style="background:%s;color:%s">%s</div>\n'
        '  <div class="ansi">%s</div>\n'
        '  <div class="meta">\n'
        '    <span><span class="sw" style="background:%s"></span>%s</span>\n'
        '    <span><span class="sw" style="background:%s"></span>fg %s</span>\n'
        '    <span><span class="sw" style="background:%s"></span>bold %s</span>\n'
        '    <span><span class="sw" style="background:%s"></span>sel %s</span>\n'
        '  </div>\n'
        '</div>'
        % (_e(dot), _e(tag), _e(nm),
           _e(bg), _e(fg), term, strip,
           _e(bg), _e(bg), _e(fg), _e(fg), _e(bold), _e(bold), _e(sel), _e(sel))
    )


def render_html(themes=None):
    """The full self-contained HTML gallery for `themes` (default: the effective
    theme registry). For EACH theme it renders BOTH variants (dark + light) — a
    theme that doesn't define a variant falls back to the shipped `default` (via
    categories.theme_palette). Categories render in canonical CATEGORIES order."""
    if themes is None:
        themes = categories.effective_themes()
    try:
        order = list(categories.available_themes())
    except Exception:
        order = list(themes)
    order = [t for t in order if t in themes] + [t for t in themes if t not in order]
    variants = getattr(categories, "VARIANTS", ("dark", "light"))
    vnames = getattr(categories, "VARIANT_NAMES", {})

    out = [
        "<!doctype html>", '<html lang="en"><head><meta charset="utf-8">',
        "<title>Task Station — themes</title>",
        "<style>%s</style>" % _CSS,
        '</head><body><div class="wrap">',
        '<div class="kicker">Task Station · themes</div>',
        "<h1>Themes</h1>",
        '<p class="lede">Each theme has two variants — <b>dark</b> and <b>light</b> — and '
        "the OS appearance picks which renders. Each card is a real terminal scene — "
        "comment · command (bold word) · prose · <em>selected</em> span · ERR/warn/ok · "
        "cursor — plus the 16-ANSI strip.</p>",
    ]
    for tname in order:
        for variant in variants:
            badge, tagline = _VARIANT_CHROME.get(variant, (variant, ""))
            vname = vnames.get(variant, "")
            label = "%s · %s%s" % (tname, variant, (" — %s" % vname) if vname else "")
            out.append('<div class="sec"><h2>%s</h2><span class="badge">%s</span>'
                       '<span class="tagline">%s</span></div>'
                       % (_e(label), _e(badge), _e(tagline)))
            out.append('<div class="grid">')
            for key in categories.CATEGORIES:
                pal = categories.theme_palette(tname, key, variant)
                if not pal:
                    continue
                meta = dict(categories.CATEGORIES[key]); meta["_key"] = key
                out.append(_card(meta, pal))
            out.append("</div>")
    out.append("</div></body></html>")
    return "\n".join(out) + "\n"


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description="Render the Task Station themes gallery to HTML.")
    p.add_argument("--out", default=None, help="write to this path (default: stdout)")
    a = p.parse_args(argv)
    html_doc = render_html()
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(html_doc)
        print(a.out)
    else:
        sys.stdout.write(html_doc)


if __name__ == "__main__":
    main()
