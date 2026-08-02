#!/usr/bin/env python3
"""Light Markdown → HTML, pure stdlib, for the visual board's expanded summary.

The summary is stored as one large (often markdown, often very long) text blob. To
make it SCANNABLE without an LLM we render a safe subset of Markdown to HTML so the
board can format + contain it. Deliberately small and robust: anything we don't
recognise passes straight through as escaped text.

SAFETY FIRST: the raw text is `html.escape`d BEFORE any markup is applied, so a
literal `<script>` (or any HTML) in a summary can never become a live tag — the
board stays self-contained / no-injection. Only the small, known set of inline +
block constructs below is then turned into HTML; href values are escaped text, never
arbitrary attributes.

Supported:
  - block:  `#`/`##`/`###` headings · `-`/`*` bullet lists (grouped into <ul>) ·
            blank-line-separated paragraphs · `---` (3+ dashes) → <hr>
  - inline: **bold** · `code` · [text](url) and bare http(s) URLs → <a> links
No external markdown dependency."""
import html
import re

# Inline patterns. Applied to ALREADY-ESCAPED text (html.escape leaves `* [ ] ( ) ` `
# and the http scheme intact, so these still match). Code spans and links are stashed
# as placeholders first so bold / bare-URL passes never reach inside them.
_CODE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_BARE = re.compile(r"https?://[^\s<]+")
_HEADING = re.compile(r"^(#{1,3})\s+(.*)$")
_BULLET = re.compile(r"^[-*]\s+(.*)$")
_HR = re.compile(r"^-{3,}$")


def _inline(text):
    """Render inline markup on a single (already html-escaped) string."""
    stash = []

    def keep(markup):
        stash.append(markup)
        return "\x00%d\x00" % (len(stash) - 1)

    # code spans first — their contents are literal, immune to bold / link / url.
    text = _CODE.sub(lambda m: keep("<code>%s</code>" % m.group(1)), text)
    # explicit [text](url) links — stash so the bare-URL pass can't double-wrap them.
    text = _LINK.sub(lambda m: keep('<a href="%s">%s</a>' % (m.group(2), m.group(1))), text)
    # bold
    text = _BOLD.sub(lambda m: "<strong>%s</strong>" % m.group(1), text)
    # bare http(s) URLs (trailing sentence punctuation left outside the link)
    def _bare(m):
        url = m.group(0)
        trail = ""
        while url and url[-1] in ").,;:!?":
            trail = url[-1] + trail
            url = url[:-1]
        return '<a href="%s">%s</a>%s' % (url, url, trail)
    text = _BARE.sub(_bare, text)
    # restore stashed placeholders
    for i, markup in enumerate(stash):
        text = text.replace("\x00%d\x00" % i, markup)
    return text


def render(text):
    """Render the supported Markdown subset of `text` to a safe HTML fragment.

    Returns "" for empty input. Output is a sequence of block elements
    (<h1..3>/<ul>/<p>/<hr>); callers wrap it in their own scroll container."""
    if not text:
        return ""
    escaped = html.escape(str(text), quote=True)
    out = []
    para = []
    items = []

    def flush_para():
        if para:
            out.append("<p>%s</p>" % _inline(" ".join(para)))
            del para[:]

    def flush_list():
        if items:
            out.append("<ul>%s</ul>" % "".join("<li>%s</li>" % _inline(it) for it in items))
            del items[:]

    for raw in escaped.split("\n"):
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            flush_para(); flush_list()
            continue
        if _HR.match(stripped):
            flush_para(); flush_list()
            out.append("<hr>")
            continue
        m = _HEADING.match(stripped)
        if m:
            flush_para(); flush_list()
            level = len(m.group(1))
            out.append("<h%d>%s</h%d>" % (level, _inline(m.group(2).strip()), level))
            continue
        m = _BULLET.match(stripped)
        if m:
            flush_para()
            items.append(m.group(1).strip())
            continue
        # plain line → part of the current paragraph
        flush_list()
        para.append(stripped)

    flush_para(); flush_list()
    return "".join(out)
