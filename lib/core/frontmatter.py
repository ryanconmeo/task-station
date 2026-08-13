"""core.frontmatter — the generic ``---`` frontmatter block: emit, parse, round-trip.

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 1) from the brain source tree's
``scripts/note_io.py`` @ 0.14.0. That file is the brain's single note-write path;
this module is its ORG-AGNOSTIC, NOTE-SEMANTICS-FREE half — the frontmatter
syntax only. Everything that knows what a *note* is (slug + path containment,
the writable-folder list, the canonical note key order, the knowledge stamp, the
typed knowledge fields, the update modes, trust integrity, git-commit-at-write)
stays behind and lands in ``lib/brain/notes.py`` in Phase 4 chunk 2.

What lives here, and why it is core: a stdlib YAML-safe frontmatter EMITTER
paired with a strict quote-aware MINI-PARSER. The pair is a matched inverse, so
the class of bug where an unquoted ``': '`` in a description silently broke the
file cannot recur. Structured values (lists and dicts) are emitted as single-line
JSON — a strict subset of YAML flow syntax, so the block stays round-trippable
through a line-based parser (``json.dumps`` never emits a newline).

KEY ORDER IS NOT OURS. The source baked its note schema's key order into
``dump_frontmatter``; here it is the caller's ``order`` argument, because order is
a schema decision and this module owns only the syntax. Passing nothing emits
insertion order.

Layer rule: core is the bottom layer — this module imports the stdlib and nothing
else (no lib/brain, no lib/board).

Pure stdlib, Python 3.9+.
"""
import json

# Characters that force a scalar to be double-quoted (YAML would misread them raw).
_QUOTE_TRIGGERS = set(":#\"'{}[]")


def _needs_quote(value):
    if value == "":
        return True
    if value != value.strip():  # leading/trailing whitespace
        return True
    return any(c in _QUOTE_TRIGGERS for c in value)


def emit_scalar(value):
    """Emit a frontmatter scalar, double-quoting (and escaping ``\\`` and ``"``)
    only when the raw form would be ambiguous YAML."""
    s = value if isinstance(value, str) else str(value)
    if _needs_quote(s):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def parse_scalar(raw):
    """Inverse of :func:`emit_scalar`. A ``"``-wrapped value is unescaped;
    anything else is returned stripped and verbatim."""
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        inner = raw[1:-1]
        out, i = [], 0
        while i < len(inner):
            c = inner[i]
            if c == "\\" and i + 1 < len(inner):
                out.append(inner[i + 1])
                i += 2
            else:
                out.append(c)
                i += 1
        return "".join(out)
    return raw


def emit_value(value):
    """Emit any frontmatter value. Lists and dicts (the structured fields — tags,
    contributors, provenance, tasks) are emitted as single-line JSON, a strict
    subset of YAML flow syntax, so the block stays round-trippable through the
    line-based :func:`parse_note` (json.dumps never emits a newline). Scalars fall
    through to :func:`emit_scalar`."""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return emit_scalar(value)


def _split_flow_items(inner):
    """Split the inside of a ``[...]`` on top-level commas (respecting nested
    ``[]``/``{}`` and double-quoted strings) — the lenient fallback for a
    hand-authored flow list that is not strict JSON (e.g. ``[foo, bar]``)."""
    items, depth, i, start, inq = [], 0, 0, 0, False
    while i < len(inner):
        c = inner[i]
        if inq:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                inq = False
        elif c == '"':
            inq = True
        elif c in "[{":
            depth += 1
        elif c in "]}":
            depth -= 1
        elif c == "," and depth == 0:
            items.append(inner[start:i])
            start = i + 1
        i += 1
    tail = inner[start:]
    if tail.strip() or items:
        items.append(tail)
    return items


def parse_value(raw):
    """Inverse of :func:`emit_value`. A value that opens with ``[`` or ``{`` is
    parsed as JSON first (the canonical machine-emitted form); a bracketed value
    that is not strict JSON degrades to a lenient string list. Everything else is
    a scalar (so a description like ``[draft] ...`` stays a plain string)."""
    r = raw.strip()
    if r[:1] in "[{":
        try:
            return json.loads(r)
        except ValueError:
            if r[:1] == "[" and r[-1:] == "]":
                return [parse_scalar(x) for x in _split_flow_items(r[1:-1])]
    return parse_scalar(raw)


def dump_frontmatter(fm, order=None):
    """Render a frontmatter dict to the ``---\\n...\\n---\\n`` block.

    ``order`` is the caller's canonical key order: keys listed in it are emitted
    first, in that order, and every remaining key follows in insertion order. It
    is a PARAMETER rather than a module constant (the source hard-coded its note
    schema's order here) because key order is a schema decision owned by the
    writer — ``lib/brain/notes.py`` passes its note order, and a caller with no
    schema gets insertion order.
    """
    order = order or ()
    keys = [k for k in order if k in fm] + [k for k in fm if k not in order]
    lines = ["---"]
    for k in keys:
        lines.append(f"{k}: {emit_value(fm[k])}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def parse_note(text):
    """Split a frontmatter document into ``(frontmatter_dict, body)``.

    Flat ``key: value`` frontmatter only (quote-aware). A line that does not
    start a new key (indented, or no colon) is ignored for keying. Returns
    ``({}, text)`` when there is no leading ``---`` fence."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text
    fm = {}
    i = 1
    while i < len(lines) and lines[i].strip() != "---":
        line = lines[i]
        if ":" in line and not line[:1].isspace():
            k, v = line.split(":", 1)
            fm[k.strip()] = parse_value(v)
        i += 1
    body = "\n".join(lines[i + 1:]) if i < len(lines) else ""
    return fm, body.lstrip("\n")


def render_note(fm, body, order=None):
    """Frontmatter block + a blank line + ``body`` (one trailing newline, or no
    body block at all when ``body`` is empty). ``order`` is passed through to
    :func:`dump_frontmatter`."""
    body = (body or "").rstrip("\n")
    return dump_frontmatter(fm, order) + "\n" + (body + "\n" if body else "")
