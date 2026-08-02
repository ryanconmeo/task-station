# export.py
"""Generic, vault-independent task export — the `task-station export --dir` core.

This is the open-source **episodic-memory** export contract: it writes the SAME
per-task Markdown notes obsidian_sync produces (sharing render_note) plus an
`index.md` (a wikilinked list) into ANY directory, with NO vault config required.
Everything is plain markdown + Obsidian-compatible `[[wikilinks]]` + flat YAML
frontmatter, so a downstream second brain (any user's, pointed anywhere) can
ingest a self-sufficient snapshot. Direction is one-way, pull: task-station
exports; a consumer reads.

Design mirrors obsidian_sync: stdlib only, atomic writes, best-effort per task
(one task's render failure never aborts the rest). Nothing enterprise-specific —
the command is a generic export any user could point at any folder.

The public surface: `parse_include`, `note_context`, `filter_since`,
`export_tasks`.
"""
import os
import sys
from datetime import datetime

import obsidian_sync

# The optional sections `--include` can toggle. `usage` and `history` are on by
# default; `prompts` is OFF by default — prompt export is opt-in (a snapshot may
# leave the machine), so it renders only when explicitly requested.
_SECTIONS = ("usage", "prompts", "history")
_DEFAULT_INCLUDE = frozenset(("usage", "history"))


def parse_include(spec):
    """A comma-separated `--include` string → a set of known section names.
    Empty/None ⇒ the default set (usage + history; prompts stays opt-in). Unknown
    tokens are ignored so a typo degrades rather than crashes."""
    if not spec:
        return set(_DEFAULT_INCLUDE)
    want = {t.strip().lower() for t in str(spec).split(",") if t.strip()}
    return {s for s in _SECTIONS if s in want}


def note_context(store, task, include):
    """(usage_dict_or_None, prompts_list_or_None) for rendering one task's note,
    given the resolved `include` set. Usage is read from the persisted ledger (via
    usage.task_usage — cheap, no transcript IO) only when 'usage' is included;
    prompts are read straight from the prompts table only when 'prompts' is
    included (WS6-independent — the table is the contract). Best-effort: any error
    degrades to None so a note still renders."""
    usage = None
    if "usage" in include:
        try:
            import usage as _usage
            usage = _usage.task_usage(store, task)
        except Exception:
            usage = None
    prompts = None
    if "prompts" in include:
        try:
            # usage.task_prompts enriches each row with role/label/sid — the SAME
            # session attribution the MCP get_prompts view shows — so the exported
            # ## Prompts lines carry session ids. Degrades to [] on any error.
            import usage as _usage
            prompts = _usage.task_prompts(store, task)
        except Exception:
            prompts = []
    return usage, prompts


def _since_ts(since):
    """An ISO date/datetime `--since` string → epoch seconds, or None when unset/
    unparseable (⇒ no filtering). A bare date (YYYY-MM-DD) is local midnight."""
    if not since:
        return None
    s = str(since).strip()
    try:
        return datetime.fromisoformat(s).timestamp()
    except (TypeError, ValueError):
        pass
    try:
        return datetime.strptime(s, "%Y-%m-%d").timestamp()
    except (TypeError, ValueError):
        return None


def filter_since(tasks, since):
    """Keep tasks last updated at/after `since` (an ISO date/datetime). An
    unparseable/empty `since` ⇒ every task passes; a task with no `updated_ts` is
    kept (it can't be excluded by a date it doesn't carry)."""
    cutoff = _since_ts(since)
    if cutoff is None:
        return list(tasks)
    out = []
    for t in tasks:
        ts = t.get("updated_ts")
        if ts is None or float(ts) >= cutoff:
            out.append(t)
    return out


def _index_markdown(rows):
    """The wikilinked `index.md` body for a list of index `rows` (each a dict
    `{stem, seq, title, status}`). Flat frontmatter marks it as the managed index;
    the body is a `[[stem|#seq · title]] — status` bullet per note, seq-ordered."""
    rows = sorted(rows, key=lambda r: (r.get("seq") if r.get("seq") is not None else 1 << 30))
    fm = ["---", "managed-by: task-station", "kind: index",
          "count: %d" % len(rows), "---", "", "# Task Station export", ""]
    body = []
    for r in rows:
        stem = r["stem"]
        seq = r.get("seq")
        title = r.get("title") or ""
        label = ("#%s · %s" % (seq, title)) if seq is not None else title
        status = r.get("status") or "open"
        body.append("- [[%s|%s]] — %s" % (stem, label, status))
    if not body:
        body = ["_(no tasks exported)_"]
    return "\n".join(fm + body) + "\n"


def _unquote(s):
    """Reverse obsidian_sync._q for a frontmatter scalar: strip the wrapping double
    quotes and un-escape `\\"`/`\\\\`. A bare (unquoted) value is returned as-is."""
    s = (s or "").strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return s


def _parse_note_meta(path):
    """(seq, title, status, category) parsed from a managed note's flat YAML
    frontmatter, or all-None when the file is unreadable / not a managed note. Only the
    first frontmatter block (between the first pair of `---` lines) is scanned.
    `category` is the human LABEL the note stores (an empty string for an uncategorised
    task); a hub groups notes by resolving that label back to a category."""
    seq = title = status = category = None
    try:
        with open(path, encoding="utf-8") as f:
            in_fm = False
            for line in f:
                s = line.rstrip("\n")
                if s == "---":
                    if in_fm:
                        break
                    in_fm = True
                    continue
                if not in_fm:
                    continue
                if s.startswith("seq: "):
                    raw = s[5:].strip()
                    try:
                        seq = int(raw)
                    except ValueError:
                        seq = None
                elif s.startswith("title: "):
                    title = _unquote(s[7:])
                elif s.startswith("status: "):
                    status = _unquote(s[8:])
                elif s.startswith("category: "):
                    category = _unquote(s[10:])
    except OSError:
        return (None, None, None, None)
    return (seq, title, status, category)


def _dir_index_rows(out_dir):
    """The full listing of notes CURRENTLY present in `out_dir`, derived from its
    sidecar index: one `{stem, seq, title, status}` row per managed note whose file
    still exists (seq/title/status parsed from that note's frontmatter). A sidecar
    entry whose file is gone is skipped. This is the merge base so a partial
    (`--since`/`--status`) export never shrinks index.md to just its delta."""
    idx = obsidian_sync._load_index(out_dir)
    rows = []
    for ent in idx.values():
        fname = ent.get("file") if isinstance(ent, dict) else None
        if not fname:
            continue
        path = os.path.join(out_dir, fname)
        if not os.path.exists(path):
            continue
        seq, title, status, category = _parse_note_meta(path)
        stem = fname[:-3] if fname.endswith(".md") else fname
        rows.append({"stem": stem, "seq": seq if seq is not None else ent.get("seq"),
                     "title": title or "", "status": status or "open",
                     "category": category or ""})
    return rows


def rebuild_index(out_dir):
    """Rewrite `out_dir/index.md` from its sidecar index alone — the full listing of
    notes currently present. Used to merge a partial export into the complete listing
    and to refresh the index after a delete/prune removes entries."""
    obsidian_sync._atomic_write(os.path.join(out_dir, "index.md"),
                                _index_markdown(_dir_index_rows(out_dir)))


# ------------------------------------------------------------- category hubs ---
# One markdown "hub" page per category, under <out_dir>/categories/, so exported
# task notes stop being orphan nodes in a graph view — each note links to its hub
# and each hub lists its notes, clustering the graph by category. Machine-managed
# and fully regenerated from the sidecar index each sync (exactly like index.md);
# consumer-agnostic (task-station's OWN taxonomy via the categories module).

CATEGORIES_SUBDIR = "categories"


def _ordered_rows(rows):
    """Rows sorted for a hub/sub-hub listing: open+active first, then closed, each
    group seq-ordered (a missing seq sorts last)."""
    return sorted(rows, key=lambda r: (1 if (r.get("status") == "closed") else 0,
                                       r.get("seq") if r.get("seq") is not None else 1 << 30))


def _row_bullet(r):
    """A `- [[stem|title]]` (or bare `- [[stem]]`) bullet for one note row."""
    import obsidian_sync
    stem = r["stem"]
    alias = obsidian_sync.wikilink_safe(r.get("title") or "") or stem
    return "- [[%s|%s]]" % (stem, alias) if alias != stem else "- [[%s]]" % stem


def _category_hub_markdown(meta, rows, groups=None):
    """The full markdown for one category-hub page: flat YAML frontmatter (managed-by,
    kind, the category key/label/description from the taxonomy, count of ALL tasks in
    the category), a one-line intro, and a `[[stem|title]]` bullet per note — open+active
    first, then closed, each group seq-ordered. `meta` is a categories.hub_meta() dict;
    `rows` are the `{stem, seq, title, status}` dicts for the notes in this category.

    `groups` (WS11, default None) is `{token: [member_row, ...]}` — the emergent
    sub-groups within this category. When present, grouped members are LIFTED out of
    the hub's own list (they live under their sub-hub, keeping the graph a tree) and a
    `### Groups` section links each sub-hub with its member count."""
    groups = groups or {}
    grouped = {r["stem"] for members in groups.values() for r in members}
    ordered = _ordered_rows([r for r in rows if r["stem"] not in grouped])
    fm = ["---", "managed-by: task-station", "kind: category-hub",
          "category: %s" % meta["key"],
          "label: %s" % obsidian_sync._q(meta["label"]),
          "description: %s" % obsidian_sync._q(meta.get("description") or ""),
          "count: %d" % len(rows), "---", ""]
    heading = "# %s [%s] — %s" % (meta["dot"], meta["tag"], meta["label"])
    intro = meta.get("description") or ("All task-station notes in the %s category."
                                        % meta["label"])
    body = [heading, "", intro, ""]
    body.extend(_row_bullet(r) for r in ordered)
    if not ordered and not groups:
        body.append("_(no tasks in this category)_")
    if groups:
        body.append("")
        body.append("### Groups")
        for token in sorted(groups):
            n = len(groups[token])
            body.append("- [[categories/%s/%s|%s]] — %d task%s"
                        % (meta["slug"], token, token.title(), n, "" if n == 1 else "s"))
    return "\n".join(fm + body) + "\n"


def _subhub_markdown(meta, token, member_rows):
    """The full markdown for one sub-hub page under `categories/<cat-slug>/<token>.md`
    (WS11): flat YAML frontmatter (managed-by, kind `category-subhub`, the parent
    category key + slug, the token, member count), a title-cased heading, a
    `[[stem|title]]` bullet per member (open+active first, then closed, seq-ordered),
    and an up-link `[[categories/<cat-slug>|<TAG>]]` so the task -> group -> category
    chain stays connected in a graph view."""
    ordered = _ordered_rows(member_rows)
    fm = ["---", "managed-by: task-station", "kind: category-subhub",
          "category: %s" % meta["key"],
          "parent: %s" % meta["slug"],
          "token: %s" % token,
          "count: %d" % len(ordered), "---", ""]
    body = ["# %s" % token.title(), ""]
    body.extend(_row_bullet(r) for r in ordered)
    body.append("")
    body.append("[[categories/%s|%s]]" % (meta["slug"], meta["tag"]))
    return "\n".join(fm + body) + "\n"


def _managed_kind(path):
    """The `kind:` of one of OUR managed pages (frontmatter `managed-by: task-station`
    + a `kind:` value) — e.g. "category-hub" or "category-subhub" — or None when the
    file isn't ours (no leading frontmatter / not managed) or is unreadable. Lets a
    prune tell our pages apart from a user's own file living in the categories/ tree."""
    try:
        with open(path, encoding="utf-8") as f:
            managed = False
            kind = None
            in_fm = False
            for i, line in enumerate(f):
                s = line.rstrip("\n")
                if s == "---":
                    if in_fm:
                        break
                    in_fm = True
                    continue
                if not in_fm:
                    return None         # no leading frontmatter ⇒ not ours
                if s == "managed-by: task-station":
                    managed = True
                elif s.startswith("kind: "):
                    kind = s[6:].strip()
                if i > 20:
                    break
            return kind if managed else None
    except OSError:
        return None


def _is_managed_hub(path):
    """True when `path` is one of OUR category-hub pages (`kind: category-hub`), so a
    prune never removes a user's own file in the categories/ dir."""
    return _managed_kind(path) == "category-hub"


def _prune_subhub_dir(subdir, slug, keep_subs, removed):
    """Remove OUR sub-hub pages (`kind: category-subhub`) under one category's nested
    `categories/<slug>/` dir that are no longer wanted (`keep_subs` holds the
    `<slug>/<token>` rel-keys to keep); leave any user file untouched, and drop the
    dir only once it is empty. Appends removed `<slug>/<name>` to `removed`."""
    for name in os.listdir(subdir):
        if not name.endswith(".md"):
            continue
        if ("%s/%s" % (slug, name[:-3])) in keep_subs:
            continue
        path = os.path.join(subdir, name)
        if _managed_kind(path) != "category-subhub":
            continue
        try:
            os.remove(path)
            removed.append("%s/%s" % (slug, name))
        except OSError:
            pass
    try:
        if not os.listdir(subdir):
            os.rmdir(subdir)
    except OSError:
        pass


def _category_rows(out_dir):
    """Group the notes CURRENTLY present in `out_dir` (via its sidecar index) by
    category. Returns an ordered list of `(meta, rows)` in canonical taxonomy order,
    one entry per category that has >=1 present note. Each note's stored `category`
    label is resolved back through the taxonomy (an unknown/empty label ⇒ the default
    category), so uncategorised notes cluster under the default hub."""
    try:
        import categories
    except Exception:
        return []
    by_key, meta_by_key = {}, {}
    for row in _dir_index_rows(out_dir):
        meta = categories.hub_meta(row.get("category") or "")
        by_key.setdefault(meta["key"], []).append(row)
        meta_by_key[meta["key"]] = meta
    # Canonical taxonomy order (categories.all_keys), then any unknown key seen.
    order = [k for k in categories.all_keys() if k in by_key]
    order += [k for k in by_key if k not in set(order)]
    return [(meta_by_key[k], by_key[k]) for k in order]


def _detect_category_subgroups(cat_rows):
    """Run categories.detect_subgroups over the category rows, keyed by category key.
    `cat_rows` is `_category_rows`' `[(meta, rows)]`; each row is augmented with a
    tokenisable `slug` (from its title) in place. Returns `{cat_key: {token: [row]}}`
    (empty on any failure — subgroups are a best-effort refinement)."""
    by_key = {}
    for meta, rows in cat_rows:
        for r in rows:
            r.setdefault("slug", obsidian_sync.slugify(r.get("title") or ""))
        by_key[meta["key"]] = rows
    try:
        import categories
        return categories.detect_subgroups(by_key)
    except Exception:
        return {}


def sync_category_hubs(out_dir, enabled=True, subgroups=False):
    """(Re)generate the category-hub pages under `out_dir/categories/` from the sidecar
    index — one hub per category with >=1 present note, FULLY regenerated each call
    (like index.md). When `subgroups` is on, emergent sub-groups within a category are
    also emitted as nested sub-hub pages at `categories/<cat-slug>/<token>.md`, their
    members lifted out of the parent hub's list into a `### Groups` section.

    A category that drops to zero notes has its hub removed; a sub-group that falls
    below the detection threshold has its sub-hub pruned (members fall back into the
    parent hub); `subgroups` off prunes ALL sub-hubs but keeps the category hubs; when
    `enabled` is False EVERY managed hub AND sub-hub is removed (the toggle-off prune).
    Only files we manage (`kind: category-hub` / `category-subhub`) are ever removed, so
    a user's own file in the tree survives, and an emptied dir is dropped. Returns
    (written, removed) sorted filename lists. Best-effort — a per-file failure never
    aborts the rest."""
    cat_dir = os.path.join(out_dir, CATEGORIES_SUBDIR)
    want_hubs = {}   # slug -> markdown (top-level category hub)
    want_subs = {}   # (slug, token) -> markdown (nested sub-hub)
    if enabled:
        cat_rows = _category_rows(out_dir)
        groups_by_key = _detect_category_subgroups(cat_rows) if subgroups else {}
        for meta, rows in cat_rows:
            groups = groups_by_key.get(meta["key"]) or {}
            want_hubs[meta["slug"]] = _category_hub_markdown(meta, rows, groups)
            for token, members in groups.items():
                want_subs[(meta["slug"], token)] = _subhub_markdown(meta, token, members)
    written, removed = [], []
    if want_hubs or want_subs:
        try:
            os.makedirs(cat_dir, exist_ok=True)
        except OSError:
            return ([], [])
    for slug, text in sorted(want_hubs.items()):
        try:
            obsidian_sync._atomic_write(os.path.join(cat_dir, slug + ".md"), text)
            written.append(slug + ".md")
        except OSError:
            pass
    for (slug, token), text in sorted(want_subs.items()):
        try:
            os.makedirs(os.path.join(cat_dir, slug), exist_ok=True)
            obsidian_sync._atomic_write(os.path.join(cat_dir, slug, token + ".md"), text)
            written.append("%s/%s.md" % (slug, token))
        except OSError:
            pass
    # Remove managed pages no longer wanted (or all of them when disabled). Nested
    # sub-hub dirs are pruned first (they may empty a dir we then remove).
    if os.path.isdir(cat_dir):
        keep_hubs = set(want_hubs)
        keep_subs = {"%s/%s" % (s, t) for (s, t) in want_subs}
        for name in os.listdir(cat_dir):
            full = os.path.join(cat_dir, name)
            if os.path.isdir(full):
                _prune_subhub_dir(full, name, keep_subs, removed)
                continue
            if not name.endswith(".md") or name[:-3] in keep_hubs:
                continue
            if not _is_managed_hub(full):
                continue
            try:
                os.remove(full)
                removed.append(name)
            except OSError:
                pass
        # Drop the dir if we left it empty (best-effort; a user file keeps it).
        try:
            if not os.listdir(cat_dir):
                os.rmdir(cat_dir)
        except OSError:
            pass
    return (sorted(written), sorted(removed))


# -------------------------------------------------------------- story hubs ---
# One markdown "hub" page per STORY (work item) referenced by >= 1 tasks, under
# <out_dir>/stories/. Orthogonal to the category axis: a story clusters tasks
# ACROSS categories, keyed by the structured `stories` field (never title tokens).
# Fully regenerated from the on-disk notes each sync (like categories/), reading the
# `story:` frontmatter list each note carries; a story dropping below 1 reference
# has its hub pruned; only `kind: story-hub` pages are ever removed.

STORIES_SUBDIR = "stories"


def _note_story_urls(path):
    """The `story:` frontmatter list of a managed note → its url strings (first
    frontmatter block only), [] when the key is absent/empty or the file is
    unreadable. Handles both the block form (`story:` then `  - "url"` lines) and the
    empty `story: []` form."""
    urls = []
    try:
        with open(path, encoding="utf-8") as f:
            in_fm = in_list = False
            for line in f:
                s = line.rstrip("\n")
                if s == "---":
                    if in_fm:
                        break
                    in_fm = True
                    continue
                if not in_fm:
                    continue
                if in_list:
                    st = s.lstrip()
                    if s[:1] in (" ", "\t") and st.startswith("- "):
                        urls.append(_unquote(st[2:]))
                        continue
                    in_list = False   # list ended — fall through to key checks
                if s.startswith("story:"):
                    rest = s[len("story:"):].strip()
                    if not rest:
                        in_list = True      # block list follows
                    elif rest != "[]":
                        urls.append(_unquote(rest))   # inline scalar (defensive)
    except OSError:
        return []
    return urls


def _story_rows(out_dir):
    """`{story_id: {"rows": [member_row], "ado_url": url_or_None}}` for the stories the
    notes CURRENTLY present in `out_dir` reference (via the sidecar index + each note's
    `story:` frontmatter), keyed by the derived story id. Only ids with >= 1 distinct
    member notes are returned — a story group needs at least one task. `ado_url` is the
    first full url seen for the id (a member may reference it as a bare id)."""
    import obsidian_sync
    groups = {}
    for row in _dir_index_rows(out_dir):
        path = os.path.join(out_dir, row["stem"] + ".md")
        seen = set()
        for url in _note_story_urls(path):
            sid, ado = obsidian_sync.story_ref(url)
            if not sid or sid in seen:
                continue
            seen.add(sid)
            g = groups.setdefault(sid, {"rows": [], "ado_url": None})
            g["rows"].append(row)
            if ado and not g["ado_url"]:
                g["ado_url"] = ado
    return {sid: g for sid, g in groups.items() if len(g["rows"]) >= 1}


def _story_hub_markdown(story_id, ado_url, rows):
    """The full markdown for one story-hub page: flat YAML frontmatter (managed-by,
    kind `story-hub`, the story id, the ADO url when known, member count), a `# Story
    <id>` heading, a `[[stem|title]]` bullet per member (open+active first, then closed,
    seq-ordered), and the ADO link line when a member carried a full url."""
    import obsidian_sync
    ordered = _ordered_rows(rows)
    fm = ["---", "managed-by: task-station", "kind: story-hub",
          "story: %s" % obsidian_sync._q(story_id),
          "ado-url: %s" % (obsidian_sync._q(ado_url) if ado_url else '""'),
          "count: %d" % len(ordered), "---", ""]
    body = ["# Story %s" % story_id, ""]
    body.extend(_row_bullet(r) for r in ordered)
    if ado_url:
        body.append("")
        body.append("[Story %s](%s)" % (story_id, ado_url))
    return "\n".join(fm + body) + "\n"


def _is_managed_story_hub(path):
    """True when `path` is one of OUR story-hub pages (`kind: story-hub`), so a prune
    never removes a user's own file living in the stories/ dir."""
    return _managed_kind(path) == "story-hub"


def sync_story_hubs(out_dir, enabled=True):
    """(Re)generate the story-hub pages under `out_dir/stories/` from the on-disk notes
    — one hub per story referenced by >= 1 present notes, FULLY regenerated each call
    (like index.md / the category hubs). A story dropping below 1 reference has its hub
    pruned; `enabled=False` prunes EVERY managed story hub (the toggle-off path). Only
    `kind: story-hub` files are ever removed, so a user's own file in the tree survives,
    and an emptied dir is dropped. Returns (written, removed) sorted filename lists.
    Best-effort — a per-file failure never aborts the rest."""
    import obsidian_sync
    story_dir = os.path.join(out_dir, STORIES_SUBDIR)
    want = {}   # slug -> markdown
    if enabled:
        for sid, g in _story_rows(out_dir).items():
            want[obsidian_sync.story_slug(sid)] = _story_hub_markdown(sid, g["ado_url"], g["rows"])
    written, removed = [], []
    if want:
        try:
            os.makedirs(story_dir, exist_ok=True)
        except OSError:
            return ([], [])
    for slug, text in sorted(want.items()):
        try:
            obsidian_sync._atomic_write(os.path.join(story_dir, slug + ".md"), text)
            written.append(slug + ".md")
        except OSError:
            pass
    if os.path.isdir(story_dir):
        keep = set(want)
        for name in os.listdir(story_dir):
            if not name.endswith(".md") or name[:-3] in keep:
                continue
            full = os.path.join(story_dir, name)
            if not _is_managed_story_hub(full):
                continue
            try:
                os.remove(full)
                removed.append(name)
            except OSError:
                pass
        try:
            if not os.listdir(story_dir):
                os.rmdir(story_dir)
        except OSError:
            pass
    return (sorted(written), sorted(removed))


def prune_dir(out_dir, live_ids):
    """Reconcile `out_dir` against the set of live task ids: remove every managed note
    whose task id is NOT in `live_ids` (deleted or redacted), drop its sidecar entry,
    and rebuild index.md. Returns the sorted list of removed filenames — [] (and NO
    index.md rewrite) on an already-clean dir."""
    idx = obsidian_sync._load_index(out_dir)
    removed, changed = [], False
    for tid in list(idx.keys()):
        if tid in live_ids:
            continue
        ent = idx.get(tid)
        fname = ent.get("file") if isinstance(ent, dict) else None
        if fname:
            try:
                os.remove(os.path.join(out_dir, fname))
                removed.append(fname)
            except OSError:
                pass
        del idx[tid]
        changed = True
    if changed:
        obsidian_sync._save_index(out_dir, idx)
        rebuild_index(out_dir)
    return sorted(removed)


def export_tasks(out_dir, tasks, store, include=None, since=None, related_fn=None,
                 owner=None, category_hubs=False, subgroups=False, story_groups=False):
    """Write a per-task note for each of `tasks` into `out_dir`, plus an `index.md`
    wikilinking them. Returns the list of written `(task, filename)` pairs.

    `owner` (default None) scopes the whole export under `out_dir/<owner>/` (its own
    sidecar index + index.md) and stamps the `owner` frontmatter key — so several
    people can export into ONE shared directory. Unset ⇒ the flat `out_dir`,
    BYTE-IDENTICAL to today.

    `include` is a resolved section set (see parse_include) or None ⇒ the default
    (usage + history). `since` filters to tasks updated at/after that ISO date.
    Notes reuse render_note, so they're byte-identical to the vault's; filenames
    are the stable `<seq>-<slug>.md` (obsidian_sync's sidecar index keeps them
    stable across a title change).

    `related_fn(task, tasks, stem_of)` (optional) returns the render_note `related`
    list for a task — the engine passes one that carries the universal task graph
    (lineage + touches-same) plus gated co-citation. It is called AFTER every note's
    filename is resolved (pass 1), so `stem_of(task_id)` maps a related task to its
    note stem IN THIS export dir (None for a task not in this run) — every emitted
    `## Related` wikilink therefore resolves to a file the export actually wrote.

    `category_hubs` (default False) maintains the `categories/` hub pages under
    `out_dir` after writing notes — one page per non-empty category, fully regenerated
    like index.md; False prunes any managed hubs (the toggle-off path). `subgroups`
    (default False) additionally emits nested sub-hub pages for emergent within-category
    clusters (only meaningful when `category_hubs` is on). `story_groups` (default False)
    maintains the ORTHOGONAL `stories/` hub pages — one per story id referenced by >= 1
    tasks, cross-category by nature; False prunes any managed story hubs. The per-note
    `[[categories/<slug>]]` (or most-specific sub-hub) and `[[stories/<id>]]` links are
    supplied separately by `related_fn`.

    Creating the directory or writing a note may raise (e.g. a sandbox EPERM on a
    path outside the allowed roots) — the caller surfaces that; a per-task RENDER
    error is caught so one bad task can't abort the snapshot."""
    include = set(_DEFAULT_INCLUDE) if include is None else set(include)
    tasks = filter_since(tasks, since)
    out_dir = obsidian_sync.owner_dir(out_dir, owner)   # owner-scoped subtree (or flat)
    os.makedirs(out_dir, exist_ok=True)   # may raise (sandbox/permission) — surfaces
    # Pass 1: resolve + persist every note's stable filename FIRST, so related edges
    # can link by a stem that will exist in this dir.
    fnames = {}
    for task in tasks:
        try:
            fname, idx = obsidian_sync.note_filename(task, out_dir)
            obsidian_sync._save_index(out_dir, idx)
            fnames[task.get("id")] = fname
        except Exception as e:
            sys.stderr.write("task-station export: task %s failed (naming): %s\n"
                             % (task.get("seq", task.get("id", "?")), e))

    def _stem_of(tid):
        f = fnames.get(tid)
        if not f:
            return None
        return f[:-3] if f.endswith(".md") else f

    # Pass 2: render + write each note (with its resolved related graph).
    entries = []
    for task in tasks:
        tid = task.get("id")
        fname = fnames.get(tid)
        if not fname:
            continue
        try:
            usage, prompts = note_context(store, task, include)
            related = related_fn(task, tasks, _stem_of) if related_fn else None
            note = obsidian_sync.render_note(
                task, usage=usage, prompts=prompts,
                include_usage=("usage" in include),
                include_history=("history" in include),
                related=related, owner=owner)
            obsidian_sync._atomic_write(os.path.join(out_dir, fname), note)
            entries.append((task, fname))
        except Exception as e:
            sys.stderr.write("task-station export: task %s failed: %s\n"
                             % (task.get("seq", task.get("id", "?")), e))
    # index.md is rebuilt from the FULL sidecar index (this run's notes are already in
    # it), so a partial --since/--status export MERGES into the complete listing
    # instead of overwriting it down to just this run's delta.
    rebuild_index(out_dir)
    # Category hubs are regenerated from the (now-current) sidecar index, exactly like
    # index.md. `category_hubs=False` (the default) prunes any managed hubs — so the
    # toggle-off path leaves no orphan category pages behind.
    sync_category_hubs(out_dir, enabled=category_hubs, subgroups=subgroups)
    # Story hubs are an ORTHOGONAL (cross-category) axis, regenerated from the same
    # on-disk notes; `story_groups=False` (the default) prunes any managed story hubs.
    sync_story_hubs(out_dir, enabled=story_groups)
    return entries
