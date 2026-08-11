"""knowledge.py — the ONE owner of vault READING (step 78, the two-plane view).

`lib/obsidian_sync.py` is the WRITER: task → note, one way, and it never reads a note
back. Nothing in this repo read a vault at all before this module, so this is the single
place that does — the way `lib/feeds.py` is the single place that knows the feed format.
Everything here is a PURE function over a path or a list of records: no config read (the
caller resolves the vault path), no network, no writes, stdlib only.

THE CORPUS IS GLOBAL. `vault_notes` returns every note in the vault, every call. It is
not a per-task tree and not a filtered slice, because the knowledge plane is a plane in
its own right rather than a derived view of the task plane: task nodes ACCUMULATE and
are episodic, note nodes CONVERGE and are semantic. A per-task tree would make the
knowledge layer a function of the task layer, which is the distinction the two-plane
view exists to draw — and it is why the two planes also get different PLACEMENT (the
knowledge plane does not borrow the task plane's category rings).

FRONTMATTER IS OPTIONAL AND UNEVEN, measured against a real 103-note corpus rather than
assumed: `type` is on every note, `area` on about 60% of them, `plane` on the same 60%
and always carrying the same literal value. So `type` is safe to lean on structurally,
`area` must tolerate absence (a note without one is `unfiled`, a first-class sector
holding ~40% of that corpus, not an error case), and `plane` has no variance so it is
passed through without being branched on. Both `type` and `area` are OPEN sets: a vault
this code has never seen will have values these notes do not.

The frontmatter reader is a deliberately small YAML SUBSET — `key: value` scalar lines
between the opening and closing `---`. This repo is stdlib-only, so a real YAML parser
is not available, and the subset never tries to GUESS structure: a nested block's
indented lines and a `-` list item are skipped, and any other value is kept as its
literal text. That is why every field has a defined empty value, and why a file with no
frontmatter at all is still a note.

THREE KINDS CROSS THE GAP, and only three (`CROSS_PLANE_KINDS`):

    cites           task → note   a `[[slug]]` in the task's own human text
    distilled-from  note → task   the note's `source: task-station:<seq>` frontmatter
    references      note → task   the collapse-to-reference records (none exist yet)

The directions are not inverses, and the asymmetry is deliberate: `cites` is a task
pointing UP at what it read, `distilled-from` is a note pointing DOWN at what produced
it. Both can hold between the same pair, and both are then drawn.
"""
import os
import re

import decisions as _dec

# The one folder the WRITER owns, `<vault>/task-station`. Imported rather than spelled
# again so the reader can never drift from the writer's namespace — everything under it
# is a task MIRROR, and mirroring tasks back onto the knowledge plane is exactly the
# layer confusion this plane exists to prevent.
try:
    from obsidian_sync import PLUGIN_FOLDER as MIRROR_FOLDER
except Exception:                       # a stdlib-only sibling; belt and braces
    MIRROR_FOLDER = "task-station"

# A wikilink target: `[[slug]]`, `[[slug|alias]]`, `[[slug#heading]]` — the slug is
# everything before the first `|` or `#`. ONE definition for the whole codebase: the
# task side of a citation and the note side of a link resolve the same syntax through
# this regex, so they cannot drift into two spellings of it.
WIKILINK_RE = re.compile(r"\[\[\s*([^\]|#]+)")

# A wikilink whose target is `task:<n>` is a TASK MENTION, never a knowledge node. The
# failure it prevents: a task reference drawing a phantom note on the knowledge plane,
# one that no file backs and that nothing can open. The measured corpus has ZERO of
# them, which is precisely why the predicate is exported now rather than left implicit —
# the renderer, this module and any future writer resolve the ambiguity the same way
# from the start, instead of agreeing on it after the bug.
TASK_MENTION_RE = re.compile(r"^task:(\d+)$")

# A note's `source` frontmatter, the origin of the `distilled-from` edge: the note was
# written out of that task. The scheme is named so a source from somewhere else (a URL,
# another tool) simply does not match and draws nothing.
SOURCE_RE = re.compile(r"^task-station:(\d+)$")

# Every kind that may cross the gap between the two planes. The gap assertion reads this
# tuple, so adding a kind here is the deliberate act of widening what crosses.
CROSS_PLANE_KINDS = ("cites", "distilled-from", "references")

# The within-plane kind: one note links to another. Named for what a wikilink IS, and
# kept distinct from the task plane's kinds so no filter row has to mean two things.
NOTE_EDGE_KIND = "links-to"

_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$")


# ------------------------------------------------------------------ wikilinks ---

def task_mention(target):
    """The task seq a wikilink target names (`task:502` → 502), or None when the target
    is an ordinary note slug. Pure string work — it does not check that the task
    exists, which is the caller's job (see `cross_plane_edges`)."""
    m = TASK_MENTION_RE.match((target or "").strip())
    return int(m.group(1)) if m else None


def is_task_mention(target):
    """True when a wikilink target is a task reference rather than a note slug."""
    return task_mention(target) is not None


def link_targets(text):
    """Every NOTE slug `text` links to, in first-seen order, deduped.

    Task mentions are excluded here rather than at every call site: a `[[task:502]]` is
    a reference to a task, so it is not a note link at all and nothing downstream should
    have to remember that. Returns [] for empty/None text."""
    out = []
    for raw in WIKILINK_RE.findall(text or ""):
        slug = raw.strip()
        if not slug or is_task_mention(slug) or slug in out:
            continue
        out.append(slug)
    return out


def task_note_links(task):
    """The set of note slugs a task cites via `[[wikilink]]` in its own human text —
    title, goal, state, summary, live decisions, history.

    A SUPERSEDED decision is not a live citation, so decision text comes from
    `decisions.live_texts` rather than the raw list. Empty set on a task with no
    wikilinks (the common case), so this never fabricates a relationship.

    This is the single implementation of "what does this task cite", shared by the
    co-citation tier (`_task_cited_notes`) and the cross-plane `cites` edge, so the two
    can never disagree about what a link means."""
    texts = [task.get("title"), task.get("goal"), task.get("state"),
             task.get("summary")]
    texts += _dec.live_texts(task.get("decisions"))
    texts += [e.get("text", "") for e in (task.get("history") or [])
              if isinstance(e, dict)]
    out = set()
    for t in texts:
        out.update(link_targets(t))
    return out


# ---------------------------------------------------------------- frontmatter ---

def _scalar(raw):
    """A frontmatter value: trimmed, and unwrapped from ONE layer of matching quotes so
    `description: "a: b"` reads as it looks."""
    v = (raw or "").strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        v = v[1:-1].strip()
    return v


def parse_frontmatter(text):
    """Split a note into `(frontmatter dict, body)`.

    Frontmatter is the block between an opening `---` on the FIRST line and the next
    `---` line. Anything else means the file has none, which is legal — the note is
    still a note. Only `key: value` scalar lines are read (see the module docstring on
    the YAML subset): keys are lowercased, a duplicate keeps the first occurrence, and a
    blank line, an indented line (a nested block's contents), a `-` list item, a `#`
    comment or a line with no colon is skipped.

    An UNTERMINATED opening fence is not frontmatter: the whole file reads as body, so a
    note that merely begins with a horizontal rule is never silently truncated."""
    lines = (text or "").split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text or ""
    fm = {}
    for i in range(1, len(lines)):
        line = lines[i]
        if line.strip() == "---":
            return fm, "\n".join(lines[i + 1:])
        if (not line.strip() or line.lstrip() != line
                or line.lstrip()[0] in ("#", "-")):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        k = key.strip().lower()
        if not k or k in fm:
            continue
        fm[k] = _scalar(value)
    return {}, text or ""


def _first_heading(body):
    """The text of the body's first markdown heading, or "" — the display title of a
    note whose frontmatter carries none."""
    for line in (body or "").split("\n"):
        m = _HEADING_RE.match(line)
        if m:
            return m.group(1).strip()
    return ""


def note_record(path, text, slug=None):
    """One note file → the record every consumer of this module reads.

      slug         the wikilink target: the filename stem (see `vault_notes`)
      title        frontmatter `title`, else the body's first heading, else the slug
      description  the node's tooltip text; "" when the note has none
      type         the primary STRUCTURAL field — present on every note measured
      area         the sector, "" when absent (the caller maps that to `unfiled`)
      plane        carried through, never branched on: it has no variance
      source       frontmatter `source`, the origin of a `distilled-from` edge
      path         the file, so a renderer can offer to open it
      links[]      the note slugs this note links to, in document order

    `source` is not in the plane's visual vocabulary but IS the source of truth for one
    of the three cross-plane kinds, so it rides on the record: the alternative is
    `cross_plane_edges` re-reading every file, which would make a pure function do I/O
    and read the corpus twice."""
    fm, body = parse_frontmatter(text)
    s = slug or _slug_from_path(path)
    return {"slug": s,
            "title": fm.get("title") or _first_heading(body) or s,
            "description": fm.get("description", ""),
            "type": fm.get("type", ""),
            "area": fm.get("area", ""),
            "plane": fm.get("plane", ""),
            "source": fm.get("source", ""),
            "path": path,
            "links": link_targets(text)}


def _slug_from_path(path):
    """The wikilink slug for a note file: its filename without the `.md`."""
    name = os.path.basename(path or "")
    return name[:-3] if name.endswith(".md") else name


# --------------------------------------------------------------- the corpus ---

def vault_notes(vault):
    """Every note in `vault`, as records, sorted by slug — the whole global corpus.

    A missing, empty or unreadable path yields [] rather than raising. That is the
    NORMAL case, not an error: a user with no vault must land on exactly today's board,
    which is what lets the knowledge plane default to `auto` instead of off.

    Skipped: hidden directories (`.obsidian`, `.git`, …), which hold the vault's
    machinery rather than notes, and the task-station MIRROR folder at the vault root —
    `obsidian_sync` exports task notes into that one folder, and reading them back would
    put TASKS on the knowledge plane. Only the root-level folder is pruned, so a user's
    own deeper folder of the same name is still read.

    A note's slug is its FILENAME STEM, because that is how a `[[wikilink]]` resolves in
    Obsidian regardless of which folder the file sits in. Two files with the same stem
    are therefore one slug: the first in the sorted walk wins and the second is dropped,
    deterministically, rather than silently overwriting it."""
    root = os.path.expanduser(vault or "")
    if not root or not os.path.isdir(root):
        return []
    by_slug = {}
    for dirpath, dirnames, filenames in os.walk(root):
        prune = {d for d in dirnames if d.startswith(".")}
        if os.path.abspath(dirpath) == os.path.abspath(root):
            prune.add(MIRROR_FOLDER)
        dirnames[:] = sorted(d for d in dirnames if d not in prune)
        for name in sorted(filenames):
            if not name.endswith(".md") or name.startswith("."):
                continue
            slug = name[:-3]
            if slug in by_slug:
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                continue                # an unreadable file is not a fatal corpus
            by_slug[slug] = note_record(path, text, slug=slug)
    return [by_slug[s] for s in sorted(by_slug)]


def note_edges(notes):
    """The note↔note wikilink edges over a corpus — ONE edge per unordered pair.

    A link to a target that is not itself a note in the corpus is DROPPED: it points at
    a file outside the note tree, or at nothing at all, and minting a node for it would
    put a phantom on the plane that no file backs. A self-link is dropped for the same
    reason it is not a relationship.

    A pair collapses to one edge because two notes linking to each other are one tie,
    not two: `dir` is `a->b` when only `a` links to `b`, and `none` when the link is
    mutual. Sorted by `(a, b)`, so the same corpus always yields the same list."""
    slugs = {n.get("slug") for n in notes if n.get("slug")}
    pairs = {}                          # frozenset({a, b}) → set of directed (src, dst)
    for n in sorted(notes, key=lambda n: n.get("slug") or ""):
        src = n.get("slug")
        if not src:
            continue
        for dst in (n.get("links") or []):
            if dst == src or dst not in slugs:
                continue
            pairs.setdefault(frozenset((src, dst)), set()).add((src, dst))
    out = []
    for pair, directed in pairs.items():
        if len(directed) > 1:
            a, b = sorted(pair)
            direction = "none"
        else:
            (a, b), = directed
            direction = "a->b"
        out.append({"a": a, "b": b, "kind": NOTE_EDGE_KIND, "dir": direction,
                    "weight": 1, "via": []})
    out.sort(key=lambda e: (e["a"], e["b"]))
    return out


def note_degree(notes, edges=None):
    """`{slug: degree}` — how many DISTINCT notes in the corpus each note is tied to,
    counting a tie once in either direction. Every note in the corpus appears, orphans
    included with 0.

    Degree is undirected on purpose: a note twenty others link to is a hub even if it
    links out to nothing, and hub-ness is what the plane's radius means."""
    deg = {n["slug"]: 0 for n in notes if n.get("slug")}
    for e in (edges if edges is not None else note_edges(notes)):
        for slug in (e.get("a"), e.get("b")):
            if slug in deg:
                deg[slug] += 1
    return deg


# --------------------------------------------------------- across the gap ---

def cross_plane_edges(tasks, notes):
    """The three kinds that cross the gap between the task plane and the knowledge
    plane — and ONLY those three (`CROSS_PLANE_KINDS`).

    Returns a flat list of `{"kind", "seq", "slug", "dir"}`, sorted by
    `(kind, seq, slug)`. `seq` is a task seq and `slug` a note slug, so this module
    never has to know the graph's node-id space — the caller mints its own ids. `dir`
    reads relative to the plane pair: `task->note` for `cites`, `note->task` for the
    other two.

    BOTH ENDPOINTS MUST EXIST. A citation of a slug outside the corpus is dropped, and
    so is a note whose `source` names a task this store does not have. Dropping is the
    entire point: the alternative is a node the user can click and never open.

    THE CROSS-PLANE DATA IS THIN, and honestly so — on the measured corpus 8 of 382
    tasks cite a note and 6 notes name a source. That is the argument for improving the
    data, not for hiding the plane: nothing here synthesises an edge, and the task↔task
    co-citation tier is deliberately NOT used as a fallback to make the gap look busier
    than it is."""
    slugs = {n.get("slug") for n in notes if n.get("slug")}
    seqs = {t.get("seq") for t in tasks if t.get("seq") is not None}
    out = []
    for t in tasks:
        seq = t.get("seq")
        if seq is None:
            continue
        for slug in sorted(task_note_links(t)):
            if slug in slugs:
                out.append({"kind": "cites", "seq": seq, "slug": slug,
                            "dir": "task->note"})
    for n in notes:
        slug = n.get("slug")
        m = SOURCE_RE.match((n.get("source") or "").strip())
        if not slug or not m:
            continue
        seq = int(m.group(1))
        if seq in seqs:
            out.append({"kind": "distilled-from", "seq": seq, "slug": slug,
                        "dir": "note->task"})
    out.extend(reference_edges(tasks, notes))
    out.sort(key=lambda e: (e["kind"], e["seq"], e["slug"]))
    return out


def reference_edges(tasks, notes):
    """The `references` kind: a note pointing at the task record that a
    collapse-to-reference left behind.

    ALWAYS EMPTY TODAY, and correctly so — collapse-to-reference has never run, so no
    store carries one of those records and there is nothing to read. This is a SUPPORTED
    kind with no instances, not an unimplemented one: `references` is in
    `CROSS_PLANE_KINDS`, the gap assertion admits it, and a renderer keyed on the kind
    set already draws it the day one appears.

    Do NOT "fix" this by inventing a source field. When collapse-to-reference lands it
    fills this list with `{"kind": "references", "seq", "slug", "dir": "note->task"}`
    records and nothing else in the pipeline changes; the arguments are already the two
    sides such a record joins."""
    return []
