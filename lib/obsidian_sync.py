# obsidian_sync.py
"""Opt-in, ONE-WAY export of Task Station tasks into an Obsidian vault.

Task Station stays the source of truth; this module only ever WRITES derived
Markdown notes (plus one optional daily-note append) — it never reads a note back
into the store. Everything the plugin owns lives under a single, clearly-namespaced
folder — `<vault>/task-station/` (the exact folder name, after the plugin) —
so a user can delete that one folder to remove every trace.

Design rules (mirroring the engine's defensive style):

  * Stdlib only. No `yaml` dependency — frontmatter is emitted by a tiny, safe
    scalar/list formatter here (double-quoted scalars, block lists).
  * Graceful degradation: if the configured vault path doesn't exist we write a
    one-line stderr note and RETURN — an export failure must never crash the
    mutation that triggered it (the engine also wraps every call in try/except).
  * Atomic writes: temp file in the SAME dir + os.replace, so a reader (Obsidian,
    a sync client) never sees a half-written note.
  * Stable filenames: the note file is `<seq>-<slug>.md`, but the chosen filename
    is remembered in a sidecar index (`.task-station-index.json`) keyed by task id,
    so RENAMING a task's title keeps its original note file instead of orphaning it.
  * The managed `Task Board.base` view is written ONCE and never overwritten, so a
    user's edits to it survive a resync.

The public surface used by the engine: `export_task`, `append_daily_note`,
`plugin_dir`, `resolve_vault`, `status_report`.
"""
import errno
import json
import os
import re
import sys
from datetime import datetime

import decisions as _dec

# The exact, plugin-namespaced folder every managed file lives under: a single
# top-level segment inside the vault (was `Claude/task-station` before 1.67).
PLUGIN_FOLDER = "task-station"

# Sidecar map (task id -> {"file","seq"}) so a note file is STABLE across a title
# change. Dotfile so Obsidian ignores it; lives beside the notes it indexes.
_INDEX_NAME = ".task-station-index.json"

# The managed Bases view filename. Written once; never clobbered (see ensure_base).
BASE_NAME = "Task Board.base"


# --------------------------------------------------------------- path helpers ---

def plugin_dir(vault):
    """The single folder every managed file lives under: <vault>/task-station."""
    return os.path.join(vault, PLUGIN_FOLDER)


def owner_dir(base, owner=None):
    """`base/<owner>` when an owner handle is set (shared-vault scoping), else `base`
    unchanged — so an unset owner is BYTE-IDENTICAL to today (no extra path segment).
    Applied to both the vault plugin dir and a generic export dir so each owner's
    notes + sidecar index live in their own subtree (zero cross-owner collisions)."""
    return os.path.join(base, owner) if owner else base


# The macOS "protected" roots whose contents are gated behind TCC (Full Disk /
# Documents / Desktop / Downloads access). A sandboxed Claude Code session can't
# os.replace into these — the export's atomic write raises EPERM — even though an
# unsandboxed shell can. Kept as home-relative tails; expanded per call.
_PROTECTED_TAILS = (
    ("Documents",),
    ("Desktop",),
    ("Downloads",),
    ("Library", "Mobile Documents"),   # iCloud Drive (incl. iCloud-synced vaults)
)


def is_protected_vault_path(path):
    """True when `path` lives under a macOS TCC-protected root (~/Documents,
    ~/Desktop, ~/Downloads, ~/Library/Mobile Documents), root included.

    Sandboxed in-session exports into these are DENIED (EPERM) while an unsandboxed
    shell can write there — so this frames the config-time warning and the
    permission hint. Path-only and best-effort: never touches the filesystem beyond
    resolving symlinks, and a bad/empty path just reads as not-protected. The
    trailing-separator guard means a sibling like `~/Documents-archive` does NOT
    match `~/Documents`."""
    if not path:
        return False
    try:
        p = os.path.realpath(os.path.expanduser(path))
    except Exception:
        return False
    home = os.path.expanduser("~")
    for tail in _PROTECTED_TAILS:
        root = os.path.realpath(os.path.join(home, *tail))
        if p == root or p.startswith(root + os.sep):
            return True
    return False


def resolve_vault(vault=None):
    """The absolute vault path to export into, or "" when export is off.

    An explicit `vault` arg wins (tests pass one directly); otherwise read the
    opt-in `obsidian_vault` config. Empty/unset ⇒ "" ⇒ export disabled. Never
    raises — a missing config module just reads as off."""
    if vault:
        return os.path.expanduser(vault)
    try:
        import config
        return config.obsidian_vault() or ""
    except Exception:
        return ""


def _vault_ready(vault):
    """True when `vault` is a set, existing directory. A configured-but-absent
    vault (external drive unmounted, path typo) logs one stderr line and reads as
    not-ready so the caller skips silently rather than crashing."""
    if not vault:
        return False
    if not os.path.isdir(vault):
        sys.stderr.write(
            "task-station: Obsidian vault not found — skipping export: %s\n" % vault)
        return False
    return True


# ----------------------------------------------------------------- formatting ---

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(title):
    """A filesystem- and wikilink-safe slug from a title: lowercased, non-alnum
    runs collapsed to single hyphens, trimmed. Empty/space-only titles fall back
    to "task" so a filename is always well-formed."""
    s = _SLUG_STRIP.sub("-", (title or "").strip().lower()).strip("-")
    return s or "task"


# Characters that would break an Obsidian `[[wikilink]]` / note title reference.
_WIKILINK_UNSAFE = re.compile(r"[\[\]#^|\\]")


def wikilink_safe(title):
    """A title cleaned of the characters that break a `[[wikilink]]` reference
    (brackets, #, ^, |, backslash), whitespace-collapsed. Stored in frontmatter
    so the title can be used as a link alias without corrupting the link."""
    return _WIKILINK_UNSAFE.sub("", (title or "")).strip() or "Untitled task"


def _local_date(ts):
    """Epoch seconds → local `YYYY-MM-DD` (ISO 8601 date), or "" when unset."""
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except Exception:
        return ""


def _history_date(iso_s):
    """An ISO timestamp stored by the engine (`_iso`, UTC) → a local `YYYY-MM-DD`
    for the History log. Returns the input unchanged if it can't be parsed, so an
    old/foreign value still renders."""
    if not iso_s:
        return ""
    try:
        dt = datetime.fromisoformat(iso_s)
        if dt.tzinfo is not None:
            dt = dt.astimezone()
        return dt.strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return iso_s


def _urls(items):
    """Extract non-empty url strings from a stored prs/stories list (a mix of
    `{"url","desc"}` dicts and legacy bare strings), first-seen order, deduped."""
    out, seen = [], set()
    for it in (items or []):
        u = (it.get("url") if isinstance(it, dict) else str(it)) or ""
        u = u.strip()
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


# Story identity — a stored story entry ({url,desc} or a legacy bare string) is a
# work-item reference; the "story id" it groups by is parsed from the url. An ADO
# work-item url (`…/_workitems/edit/<n>`) yields its numeric id; any other http(s)
# url yields its last path segment; a bare token is its own id. The id is the group
# key + the `stories/<id>.md` hub name; the url (when http(s)) is the ADO link.
_ADO_WORKITEM = re.compile(r"/_workitems/edit/(\d+)", re.IGNORECASE)


def story_ref(entry):
    """`(story_id, ado_url)` for ONE stored story entry — a `{"url","desc"}` dict or a
    legacy bare url string. `story_id` is the ADO work-item number when the url is an
    ADO work-item link, else the last path segment of an http(s) url, else the bare
    token itself; `ado_url` is the http(s) url when the entry carries one, else None (a
    plain id carries no link). `(None, None)` for a blank/urlless entry."""
    url = (entry.get("url") if isinstance(entry, dict) else str(entry or "")).strip()
    if not url:
        return (None, None)
    if url.lower().startswith(("http://", "https://")):
        m = _ADO_WORKITEM.search(url)
        sid = m.group(1) if m else (url.rstrip("/").rsplit("/", 1)[-1] or url)
        return (sid, url)
    return (url, None)


def story_slug(story_id):
    """A filesystem- and wikilink-safe slug for a story id → the `stories/<slug>.md`
    hub name (and the `[[stories/<slug>]]` link target). A numeric ADO id passes
    through unchanged; other tokens keep alnum/dot/dash/underscore, other runs collapse
    to a single hyphen. Empty ⇒ "story"."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", str(story_id or "").strip()).strip("-")
    return s or "story"


def _category_label(task):
    """The task's human category label ("bug work"), via the categories module —
    the same label the board shows. Falls back to the raw colour key, then "", so
    a missing categories module never breaks the export."""
    color = task.get("color")
    if not color:
        return ""
    try:
        import categories
        return categories.label(color) or color
    except Exception:
        return color


def _q(value):
    """A YAML double-quoted scalar — safe for any string (colons, #, leading
    special chars). Newlines are folded to spaces so a scalar stays one line."""
    s = "" if value is None else str(value)
    s = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", "")
    return '"%s"' % s


def _yaml_list(key, items):
    """A flat YAML block list, or `key: []` when empty. Values are quoted scalars
    so a url with a colon or a repo name with a space stays valid."""
    items = [i for i in (items or []) if i]
    if not items:
        return "%s: []" % key
    return "\n".join(["%s:" % key] + ["  - %s" % _q(i) for i in items])


def _yaml_field(key, value):
    """Frontmatter lines for one GENERIC key whose value shape isn't fixed (the
    forward-compatible contract fields like `glossary` / `brief_path`): a list →
    a flat block list, a mapping → a nested block of quoted scalars, anything else
    → a single quoted scalar. Returns a list of lines."""
    if isinstance(value, (list, tuple)):
        return [_yaml_list(key, list(value))]
    if isinstance(value, dict):
        if not value:
            return ["%s: {}" % key]
        lines = ["%s:" % key]
        for k, v in value.items():
            lines.append("  %s: %s" % (k, _q(v)))
        return lines
    return ["%s: %s" % (key, _q(value))]


def _time_spent_min(task):
    """Total active MINUTES on a task — the sum of its recorded activity spans
    (each already idle-gap-capped by the engine). 0 when no spans (older tasks
    predating span tracking). Kept self-contained here (obsidian_sync must not
    import the engine — the engine imports it) and stored as a flat integer so
    the frontmatter key stays Bases/Dataview-queryable."""
    total = 0.0
    for span in (task.get("spans") or []):
        try:
            start, end = float(span[0]), float(span[1])
        except (TypeError, ValueError, IndexError):
            continue
        if end > start:
            total += end - start
    return int(total // 60)


def _model_family(model):
    """Best-effort display family for a model id ("fable", "opus", …), via
    lib/pricing.py; falls back to the raw id so an unknown model still renders."""
    try:
        import pricing
        return pricing.model_family(model) or model
    except Exception:
        return model


def _prompt_ts(ts):
    """A prompt row's epoch-seconds ts → local `YYYY-MM-DD HH:MM`, or "" when unset."""
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _usage_lines(usage):
    """The body lines for the `## Usage` block from a `usage.task_usage()` dict, or
    the `_(none)_` placeholder when there is no tracked usage. Compact, markdown
    bullets: model mix %, token totals, derived + reported $, and the phase mix."""
    models = (usage or {}).get("models") or {}
    if not models:
        return ["_(no usage tracked yet)_"]
    ranked = sorted(models.items(), key=lambda kv: -(kv[1].get("pct") or 0))
    mix = []
    for m, d in ranked:
        pct = round((d.get("pct") or 0) * 100)
        mix.append("%s %d%%%s" % (_model_family(m), pct,
                                  " ($n/a)" if d.get("cost_usd") is None else ""))
    lines = ["- Models: %s" % " · ".join(mix)]
    lines.append("- Tokens: in %d · out %d · cache-read %d"
                 % (usage.get("total_in") or 0, usage.get("total_out") or 0,
                    sum((d.get("cache_read") or 0) for d in models.values())))
    tc = usage.get("total_cost_usd") or 0.0
    cost = "- Cost: $%.2f derived" % tc
    rep = usage.get("reported_cost_usd") or 0.0
    if rep > 0:
        cost += " · $%.2f reported" % rep
    if usage.get("any_unpriced"):
        cost += " (excludes unknown model(s))"
    lines.append(cost)
    phases = usage.get("phases") or {}
    pmix = ["%s %d%%" % (name.capitalize(), round((d.get("pct") or 0) * 100))
            for name, d in sorted(phases.items(), key=lambda kv: -(kv[1].get("pct") or 0))
            if round((d.get("pct") or 0) * 100) > 0]
    if pmix:
        lines.append("- Phases: %s" % " · ".join(pmix))
    return lines


def _prompt_session(p):
    """The per-prompt session attribution string, mirroring the MCP get_prompts view
    (`_prompt_session_tag`): `<role> <sid>` (or `<role>:<label> <sid>`), the short
    8-char sid. Empty when the row carries no session info (a bare `{ts,kind,text}`
    row from a caller that didn't enrich it) — so those render byte-identically to
    before. Kept self-contained (obsidian_sync must not import the engine)."""
    role = p.get("role") or ""
    sid = p.get("sid") or (p.get("session_id") or "")[:8]
    if not role and not sid:
        return ""
    if p.get("label"):
        role = "%s:%s" % (role or "unknown", p["label"])
    return "%s %s" % (role or "unknown", sid or "—")


def _prompt_lines(prompts):
    """The body lines for the `## Prompts` block from a list of prompt rows
    (`{ts, kind, text}`, optionally enriched with `role`/`label`/`sid`), in
    chronological order — the full timestamped trail. `_(none)_` when empty. Slash
    commands / compaction summaries are tagged; the per-prompt session attribution
    (matching the MCP get_prompts view) is carried when the row has it; newlines are
    folded so each prompt stays one line."""
    out = []
    for p in (prompts or []):
        text = re.sub(r"\s+", " ", (p.get("text") or "").strip())
        if not text:
            continue
        kind = p.get("kind") or "prompt"
        tag = "" if kind == "prompt" else "[%s] " % kind
        # `<when> · <session>` — each part optional; when both are absent the line
        # is byte-identical to a caller passing a bare {ts,kind,text} row.
        meta = " · ".join(x for x in (_prompt_ts(p.get("ts")), _prompt_session(p)) if x)
        out.append("- %s — %s%s" % (meta, tag, text) if meta else "- %s%s" % (tag, text))
    return out or ["_(none)_"]


# ------------------------------------------------------------------ rendering ---

def render_note(task, usage=None, prompts=None, include_usage=True,
                include_history=True, related=None, owner=None):
    """The full Markdown note (flat YAML frontmatter + body) for one task.

    Frontmatter: managed-by, seq, status, category (label), effort, repos (list),
    story (list), pr (list), created/updated/done (ISO local dates), title
    (wikilink-safe), plus the derived-usage keys models (list of model ids),
    cost-usd (derived $ scalar), time-spent (active minutes) — all flat scalars/
    lists so Bases/Dataview can query them. Body: ## Goal, ## State, ## Summary,
    ## Decisions, ## History, ## Usage, ## Prompts.

    `usage` is a `usage.task_usage()` dict (or None) — it feeds the Usage block
    and the three usage frontmatter keys. `include_usage` renders the `## Usage`
    section (always on for the vault; the export command's `--include` can drop it).
    `## Prompts` is rendered ONLY when `prompts` is not None (a list) — the caller
    gates it on the opt-in `--obsidian-prompts` config / `--include prompts`.
    `include_history` renders `## History` (export's `--include` can drop it).

    `related` (default None) is a list of already-resolved edges for the `## Related`
    section. Each pair is either a 3-tuple `(stem, title, kind)` — a task↔task edge
    rendered `[[stem|title]] — kind`, the stem a sidecar-index note filename so the
    link RESOLVES (fixing the old dangling `[[title]]`) — or a 2-tuple `(target, kind)`
    → `[[target]] — kind` (a cited knowledge note, whose slug is its own note name).
    Universal task edges (lineage + `touches-same`) are always supplied by the caller;
    co-citation note edges only when the knowledge-graph gate is on. None / empty ⇒ the
    section is omitted (byte-identical to a note with no relations).

    `owner` (default None) stamps an `owner: <handle>` frontmatter key for a shared
    vault; unset ⇒ no key (byte-identical to today's note)."""
    usage = usage or {}
    umodels = usage.get("models") or {}
    ranked_models = [m for m, _ in sorted(umodels.items(),
                                          key=lambda kv: -(kv[1].get("pct") or 0))]
    cost_usd = usage.get("total_cost_usd")
    seq = task.get("seq")
    status = task.get("status") or "open"
    created = _local_date(task.get("created_ts"))
    updated = _local_date(task.get("updated_ts"))
    # `closed` is the REAL close date, stamped once by the close path (closed_ts) and
    # never disturbed by a later update — so it is stable across a post-close resync.
    # (Replaces the old `done` proxy, which used the last-updated ts while closed.)
    closed = _local_date(task.get("closed_ts"))

    fm = ["---", "managed-by: task-station", "schema-version: 2"]
    fm.append("uuid: %s" % _q(task.get("uuid") or task.get("id") or ""))
    fm.append("seq: %s" % (seq if seq is not None else '""'))
    fm.append("status: %s" % _q(status))
    fm.append("category: %s" % _q(_category_label(task)))
    fm.append("effort: %s" % _q(task.get("effort") or ""))
    fm.append(_yaml_list("repos", task.get("projects")))
    fm.append(_yaml_list("story", _urls(task.get("stories"))))
    fm.append(_yaml_list("pr", _urls(task.get("prs"))))
    fm.append("created: %s" % (created or '""'))
    fm.append("updated: %s" % (updated or '""'))
    fm.append("closed: %s" % (closed or '""'))
    fm.append("title: %s" % _q(wikilink_safe(task.get("title"))))
    # Derived-usage frontmatter — flat scalars/lists (Bases/Dataview-queryable).
    # `models` lists the model ids used; `cost-usd` is the derived $ (empty when
    # untracked); `time-spent` is active minutes (always an integer, 0 when none).
    fm.append(_yaml_list("models", ranked_models))
    fm.append("cost-usd: %s" % ('""' if cost_usd is None else round(cost_usd, 6)))
    fm.append("time-spent: %d" % _time_spent_min(task))
    if owner:
        fm.append("owner: %s" % _q(owner))
    # First-class contract fields carried GENERICALLY when present (they may not
    # exist on any task yet): a list renders as a YAML block list, a mapping as a
    # nested block, anything else as a quoted scalar.
    for key in ("glossary", "brief_path"):
        val = task.get(key)
        if val is None:
            continue
        fm.extend(_yaml_field(key, val))
    fm.append("---")

    body = ["", "# %s" % (wikilink_safe(task.get("title")))]

    def section(heading, text):
        body.append("")
        body.append("## %s" % heading)
        body.append(text.strip() if (text or "").strip() else "_(none)_")

    section("Goal", task.get("goal"))
    section("State", task.get("state"))
    section("Summary", task.get("summary"))

    body.append("")
    body.append("## Decisions")
    # Still-current decisions only — an exported note is a present-tense view, and a
    # superseded decision leaving for the vault is exactly the resurfacing this guards.
    decisions = [t for t in (s.strip() for s in _dec.live_texts(task.get("decisions"))) if t]
    if decisions:
        body.extend("- %s" % d for d in decisions)
    else:
        body.append("_(none)_")

    if include_history:
        body.append("")
        body.append("## History")
        history = task.get("history") or []
        if history:
            # Append-only order preserved (stored order == chronological).
            for e in history:
                when = _history_date(e.get("ts", ""))
                txt = (e.get("text") or "").strip()
                if txt:
                    body.append("- %s — %s" % (when, txt) if when else "- %s" % txt)
        else:
            body.append("_(none)_")

    # ## Usage — the derived model/token/$/phase snapshot. Always present for the
    # vault (compact block sourced from usage.task_usage); the export command's
    # --include can drop it.
    if include_usage:
        body.append("")
        body.append("## Usage")
        body.extend(_usage_lines(usage))

    # ## Prompts — the full timestamped trail. Rendered ONLY when a list is passed
    # (the caller gates it on the opt-in --obsidian-prompts config / --include prompts);
    # a vault may sync to third-party services, so prompt export stays off by default.
    if prompts is not None:
        body.append("")
        body.append("## Prompts")
        body.extend(_prompt_lines(prompts))

    # ## Related — resolvable wikilinks. The caller passes already-resolved edges:
    # universal task↔task edges (lineage + `touches-same`) always, co-citation
    # `[[note]]` edges only when the knowledge-graph gate is on. Each pair is either
    # a 3-tuple `(stem, title, kind)` — rendered `[[stem|title]] — kind`, the stem a
    # sidecar-index filename so the link resolves (no dangling `[[title]]`) — or a
    # 2-tuple `(target, kind)` → `[[target]] — kind` (a cited note, whose slug IS its
    # note name). Omitted when `related` is empty, keeping the default note
    # byte-identical to today's.
    rel = [pair for pair in (related or []) if pair and pair[0]]
    if rel:
        body.append("")
        body.append("## Related")
        for pair in rel:
            if len(pair) >= 3:
                stem, title, kind = pair[0], pair[1], pair[2]
                alias = wikilink_safe(str(title)) if title else ""
                stem_s = str(stem)
                link = ("- [[%s|%s]]" % (stem_s, alias)
                        if alias and alias != stem_s else "- [[%s]]" % stem_s)
            else:
                target, kind = pair[0], pair[1]
                link = "- [[%s]]" % wikilink_safe(str(target))
            body.append("%s — %s" % (link, kind) if kind else link)

    return "\n".join(fm) + "\n" + "\n".join(body) + "\n"


# ------------------------------------------------------------- atomic + index ---

def _atomic_write(path, text):
    """Write `text` to `path` atomically: temp file in the SAME dir + os.replace,
    so a reader never sees a partial note. The pid-suffixed temp avoids collisions
    between concurrent sessions."""
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _index_path(pdir):
    return os.path.join(pdir, _INDEX_NAME)


def _load_index(pdir):
    try:
        with open(_index_path(pdir), encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_index(pdir, idx):
    try:
        _atomic_write(_index_path(pdir), json.dumps(idx, indent=2, ensure_ascii=False))
    except Exception:
        pass   # the index is an optimisation; losing it just recomputes a filename


def note_filename(task, pdir):
    """The STABLE note filename for `task` under `pdir`, plus the (possibly
    updated) index to persist.

    First export records `<seq>-<slug>.md` in the sidecar index keyed by task id;
    later exports REUSE that filename even if the title (hence slug) changed, so a
    rename never orphans the note. Returns `(filename, index_dict)`."""
    idx = _load_index(pdir)
    tid = task.get("id") or ""
    ent = idx.get(tid)
    if isinstance(ent, dict) and ent.get("file"):
        return ent["file"], idx
    seq = task.get("seq")
    stem = str(seq) if seq is not None else (tid[:8] or "task")
    fname = "%s-%s.md" % (stem, slugify(task.get("title")))
    if tid:
        idx[tid] = {"file": fname, "seq": seq}
    return fname, idx


def migrate_to_owner(vault, owner):
    """Relocate managed notes from the FLAT plugin dir (<vault>/task-station) into the
    owner subtree (<vault>/task-station/<owner>) when an owner is newly set, using the
    flat sidecar index to move exactly our own notes (never user files), merging the
    index into the owner's, and pruning the emptied flat index so no orphan is left.
    Idempotent (nothing to move ⇒ 0) and best-effort — a move failure skips that note
    but never raises. Returns the count of notes relocated."""
    if not owner:
        return 0
    src = plugin_dir(vault)
    dst = owner_dir(src, owner)
    if os.path.abspath(src) == os.path.abspath(dst):
        return 0
    src_idx = _load_index(src)
    if not src_idx:
        return 0
    try:
        os.makedirs(dst, exist_ok=True)
    except OSError:
        return 0
    dst_idx = _load_index(dst)
    moved = 0
    for tid, ent in list(src_idx.items()):
        fname = ent.get("file") if isinstance(ent, dict) else None
        if not fname:
            continue
        srcp = os.path.join(src, fname)
        if os.path.exists(srcp):
            try:
                os.replace(srcp, os.path.join(dst, fname))
                moved += 1
            except OSError:
                continue
        dst_idx[tid] = ent
    _save_index(dst, dst_idx)
    # The flat notes are relocated — drop the flat sidecar so the old location holds
    # no managed index (user files + the .base view are left untouched).
    try:
        os.remove(_index_path(src))
    except OSError:
        pass
    return moved


def remove_task_note(task_id, pdir):
    """Best-effort removal of a task's managed note + its sidecar-index entry under
    `pdir` (used when a task is HARD-deleted). Returns the removed filename, or None
    when there was nothing to remove / on any error — never raises, so a delete never
    fails because a vault/export dir is gone."""
    try:
        idx = _load_index(pdir)
        ent = idx.get(task_id)
        fname = ent.get("file") if isinstance(ent, dict) else None
        removed = None
        if fname:
            try:
                os.remove(os.path.join(pdir, fname))
                removed = fname
            except OSError:
                pass   # already gone / unwritable — still drop the index entry
        if task_id in idx:
            del idx[task_id]
            _save_index(pdir, idx)
        return removed
    except Exception:
        return None


def note_stem(task, pdir=None):
    """The note filename STEM (no `.md`) for `task` — the wikilink target used to
    build resolvable `[[stem|title]]` Related links. With a plugin dir it is the
    index-stable name (a prior export's sidecar keeps it stable across a rename);
    without one it is the deterministic `<seq|id8>-<slug>` default. Reads only (never
    writes the index), so it is safe to call while resolving a note's relations."""
    if pdir:
        fname, _ = note_filename(task, pdir)
    else:
        seq = (task or {}).get("seq")
        stem = str(seq) if seq is not None else (((task or {}).get("id") or "")[:8] or "task")
        fname = "%s-%s.md" % (stem, slugify((task or {}).get("title")))
    return fname[:-3] if fname.endswith(".md") else fname


# ---------------------------------------------------------------- Bases view ---

def _base_content():
    """A minimal, well-commented Obsidian Bases view over the managed task notes:
    a table of OPEN tasks with seq/title/category/effort/updated columns. Bases'
    exact schema is still evolving, so this ships a conservative form plus a
    pointer to the equivalent Dataview snippet in the docs. Written ONCE and never
    overwritten, so any user edits survive a resync."""
    return (
        "# Task Station — managed Obsidian Bases view (written once; edit freely,\n"
        "# a resync will NOT overwrite this file). Filters to open, task-station-\n"
        "# managed notes and tables the key columns. If your Obsidian build's Bases\n"
        "# schema differs, adjust below — an equivalent Dataview query is in the\n"
        "# plugin README's \"Obsidian export\" section.\n"
        "filters:\n"
        "  and:\n"
        "    - 'managed-by == \"task-station\"'\n"
        "    - 'status == \"open\"'\n"
        "views:\n"
        "  - type: table\n"
        "    name: Open tasks\n"
        "    order:\n"
        "      - seq\n"
        "      - title\n"
        "      - category\n"
        "      - effort\n"
        "      - updated\n"
    )


def ensure_base(pdir):
    """Write the managed `Task Board.base` view IF it doesn't already exist. Never
    overwrites — an existing file is treated as user-owned (edited), so their
    columns/filters survive. Best-effort: a write failure is a silent no-op."""
    path = os.path.join(pdir, BASE_NAME)
    if os.path.exists(path):
        return False
    try:
        _atomic_write(path, _base_content())
        return True
    except Exception:
        return False


# --------------------------------------------------------------- public API ---

def export_task(task, vault=None, usage=None, prompts=None, related=None, owner=None):
    """Export ONE task's note into the vault. Returns the note filename (e.g.
    `12-fix-login.md`) on success, or None when export is off / the vault is
    missing / a write fails. Ensures the plugin folder and the managed Bases view
    exist. Best-effort throughout — the caller also guards, but this degrades
    gracefully on its own so it's safe to call directly (tests do).

    `usage` (a usage.task_usage() dict) feeds the note's ## Usage block + usage
    frontmatter; `prompts` (a list) is passed through only when the engine's opt-in
    --obsidian-prompts is on, rendering the ## Prompts trail. Both default to None,
    so a bare export_task(task, vault) still writes a valid note (Usage shows the
    placeholder, Prompts is omitted).

    `owner` (default None) nests the note + its sidecar index under
    <plugin>/<owner>/ and stamps the `owner` frontmatter key — for a shared vault.
    Unset ⇒ the flat plugin dir, BYTE-IDENTICAL to today."""
    vault = resolve_vault(vault)
    if not _vault_ready(vault):
        return None
    pdir = owner_dir(plugin_dir(vault), owner)
    try:
        os.makedirs(pdir, exist_ok=True)
    except OSError as e:
        # A PERMISSION denial (sandbox/TCC on a protected root) must SURFACE to the
        # caller — the engine tracks the task as pending-resync and warns — rather
        # than silently degrading to None like a benign, transient makedirs miss.
        if isinstance(e, PermissionError) or e.errno in (errno.EPERM, errno.EACCES):
            raise
        sys.stderr.write("task-station: cannot create %s — skipping export (%s)\n" % (pdir, e))
        return None
    fname, idx = note_filename(task, pdir)
    _save_index(pdir, idx)
    _atomic_write(os.path.join(pdir, fname),
                  render_note(task, usage=usage, prompts=prompts, related=related,
                              owner=owner))
    ensure_base(pdir)
    return fname


def _insert_under_heading(text, heading, line):
    """Return `text` with `line` inserted at the end of the `heading` section, or
    None when `line` is ALREADY present (idempotent per event).

    If the heading is absent it's appended (with the line) at the end of the file.
    If present, the line is inserted after the section's existing content (before
    the next heading of the same-or-higher level, else EOF) so entries stay in
    append (chronological) order."""
    if line in text:
        return None
    lines = text.splitlines()
    # Locate the heading line exactly (ignoring trailing whitespace).
    h_idx = None
    for i, ln in enumerate(lines):
        if ln.strip() == heading.strip():
            h_idx = i
            break
    if h_idx is None:
        # Append the heading + line. Keep one blank line of separation from prior
        # content so the new section reads cleanly.
        out = list(lines)
        if out and out[-1].strip() != "":
            out.append("")
        out.append(heading)
        out.append(line)
        return "\n".join(out) + "\n"
    # Find the end of this heading's section: the next markdown heading (# … ),
    # else end of file.
    end = len(lines)
    for j in range(h_idx + 1, len(lines)):
        if re.match(r"^#{1,6}\s", lines[j]):
            end = j
            break
    # Insert before any trailing blank lines that pad the section end, so the log
    # entries stay contiguous under the heading.
    insert_at = end
    while insert_at - 1 > h_idx and lines[insert_at - 1].strip() == "":
        insert_at -= 1
    out = lines[:insert_at] + [line] + lines[insert_at:]
    return "\n".join(out) + "\n"


def append_daily_note(vault, link_target, event, title, heading, when=None, owner=None):
    """Append a one-line entry to today's daily note under `heading`:

        - HH:MM · [[<link_target>]] — <event>: <title>

    `link_target` is the note filename WITHOUT the `.md` extension (a wikilink
    target). `event` is "closed" or "checkpoint". Creates the daily-note file and
    the heading if absent. Idempotent per event: an identical line already present
    (same minute/target/event) is not duplicated. `when` is injectable for tests;
    defaults to now. Best-effort — vault missing / write failure logs to stderr
    and returns without raising.

    `owner` (default None) prefixes the line with `<owner> ·` and path-qualifies the
    wikilink to that owner's subfolder (`[[<owner>/<link_target>]]`) so, in a SHARED
    daily note, two owners never collide (distinct lines) and each link resolves
    unambiguously. Unset ⇒ BYTE-IDENTICAL to today's line."""
    if not _vault_ready(vault):
        return None
    when = when or datetime.now()
    day = when.strftime("%Y-%m-%d")
    hhmm = when.strftime("%H:%M")
    if owner:
        line = "- %s · %s · [[%s/%s]] — %s: %s" % (
            hhmm, owner, owner, link_target, event, title)
    else:
        line = "- %s · [[%s]] — %s: %s" % (hhmm, link_target, event, title)
    path = os.path.join(vault, day + ".md")
    try:
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            text = ""
        new = _insert_under_heading(text, heading, line)
        if new is None:
            return line   # already recorded — idempotent no-op
        _atomic_write(path, new)
        return line
    except Exception as e:
        sys.stderr.write("task-station: daily-note append failed (%s)\n" % e)
        return None


def status_report(vault=None):
    """A human-readable multi-line status of the Obsidian export, for
    `task-station obsidian --status`. Reports whether export is on, the vault +
    plugin folder, how many notes exist, and the daily-note setting."""
    raw = resolve_vault(vault)
    if not raw:
        return ("Obsidian export: OFF\n"
                "  Enable by setting a vault path:\n"
                '    /task-station:config --obsidian-vault "~/Documents/Obsidian Vault"')
    lines = ["Obsidian export: ON", "  Vault:  %s" % raw]
    pdir = plugin_dir(raw)
    lines.append("  Folder: %s" % pdir)
    if not os.path.isdir(raw):
        lines.append("  ⚠ Vault path does not exist — exports are skipped until it does.")
    else:
        n = 0
        if os.path.isdir(pdir):
            n = sum(1 for f in os.listdir(pdir)
                    if f.endswith(".md") and not f.startswith("."))
        lines.append("  Notes:  %d" % n)
        lines.append("  Board:  %s%s" % (
            BASE_NAME, "" if os.path.exists(os.path.join(pdir, BASE_NAME)) else " (not yet written)"))
    try:
        import config
        if config.obsidian_daily_note_enabled():
            lines.append("  Daily note: on  (heading: %s)" % config.obsidian_daily_heading())
        else:
            lines.append("  Daily note: off")
    except Exception:
        pass
    return "\n".join(lines)
