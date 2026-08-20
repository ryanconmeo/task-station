"""Board I/O seam: the Obsidian export hook, native Tasks interop, and the F6 capture / write_board family."""
from board import _shared
from board._shared import *          # constants + utilities (explicit __all__)
from board.state import *
from board.model import *
from board.memos import *
from board.sessions import *
from board.render import *
from board.graph import *
import hashlib
import json
import os
import re
import sys
from datetime import datetime

import loop as _loop
import paths

g, set_g = _shared.g, _shared.set_g

__all__ = [
    "_obsidian_vault", "_obsidian_perm_marker", "_obsidian_persistent_help",
    "_warn_obsidian_persistent_once", "_clear_obsidian_perm_marker",
    "_obsidian_note_data", "_related_pairs", "_knowledge_gate_on",
    "_export_related_fn", "_category_hubs_on", "_subgroups_on",
    "_story_groups_on",
    "_SUBGROUP_MAP", "_STORY_GROUP_IDS",
    "_compute_subgroup_token_map", "_compute_story_group_ids",
    "_begin_subgroups", "_end_subgroups", "_subgroup_token_for",
    "_category_hub_pair", "_with_category_hub", "_story_hub_pairs",
    "_with_story_hubs", "_sync_obsidian_category_hubs",
    "_sync_obsidian_story_hubs", "_obsidian_related_links",
    "_obsidian_sync", "_obsidian_event",
    "_native_lists", "_format_native", "_native_footer_line",
    "cmd_native", "cmd_adopt",
    "_format_list", "_md_escape", "_md_effort", "_md_task_row",
    "_format_list_md",
    "_semver_tuple", "_semver_gt", "_existing_board_version",
    "_autolink_task_signals",
    "_augment_graph_foreign", "write_board", "maybe_refresh_board",
    "_task_cat_tag", "_brain_signals", "_brain_task_views",
    "auto_attach_brain",
]


# ------------------------------------------------------- Obsidian export hook ---
# Opt-in, one-way mirror of a task into an Obsidian vault (lib/obsidian_sync.py).
# Guarded end-to-end: OFF unless a vault is configured, and EVERY call is wrapped
# so an export failure (missing vault, permission error, a bug) degrades to a
# stderr warning and NEVER breaks the engine mutation that triggered it.

def _obsidian_vault():
    """The configured vault path, or None when export is off. Cheap to call on the
    hot mutation path — a bare config read."""
    try:
        import config
        return config.obsidian_vault() or None
    except Exception:
        return None


def _obsidian_perm_marker():
    """Path to the one-shot marker that dedupes the permission hint across the many
    separate CLI processes of one episode. Lives in the mutable data dir (resolved
    fresh so test isolation via TASK_STATION_HOME is honoured)."""
    return os.path.join(paths.data_dir(), ".obsidian-perm-warned")


def _obsidian_persistent_help(vault):
    """The actionable multi-line remedy text for a PERSISTENT export failure — one a
    hot-path mutation AND the unsandboxed end-of-turn hook flush both failed to write.
    Frames the cause (protected folder or gone path) and the four fixes. Shared by the
    deduped hot-path warning and the manual `obsidian --flush` summary."""
    try:
        import obsidian_sync
        protected = obsidian_sync.is_protected_vault_path(vault)
    except Exception:
        protected = False
    where = " under a macOS-protected folder" if protected else ""
    return (
        "task-station: Obsidian export keeps FAILING even from an unsandboxed "
        "auto-flush%s: %s\n"
        "  Tasks remain pending-resync — the vault is drifting stale. Fix any one of:\n"
        "    (a) grant Claude Code / your terminal Full Disk (or Documents) access "
        "in System Settings > Privacy & Security;\n"
        "    (b) point the vault OUTSIDE a protected folder: "
        "/task-station:config --obsidian-vault \"~/ObsidianVault\";\n"
        "    (c) enable instant inline exports: "
        "task-station config --obsidian-sandbox on;\n"
        "    (d) or run: task-station obsidian --flush (or --sync-all) from an "
        "unsandboxed shell.\n" % (where, vault))


def _warn_obsidian_persistent_once(vault):
    """Print ONE actionable hint per episode for a persistent failure, then a marker
    file dedupes every later mutation (each is its own process, so an in-memory flag
    wouldn't hold). Cleared on the next successful export so a future breakage
    re-warns. Best-effort — a marker read/write hiccup must never break a mutation."""
    marker = _obsidian_perm_marker()
    try:
        if os.path.exists(marker):
            return
    except Exception:
        pass
    sys.stderr.write(_obsidian_persistent_help(vault))
    try:
        with open(marker, "w") as f:
            f.write("obsidian export persistently failing — see stderr hint\n")
    except Exception:
        pass


def _clear_obsidian_perm_marker():
    """Drop the one-shot marker once an export succeeds, so a LATER permission
    breakage warns afresh. Best-effort no-op when absent/unwritable."""
    try:
        os.remove(_obsidian_perm_marker())
    except OSError:
        pass


def _obsidian_note_data(task):
    """(usage_dict_or_None, prompts_list_or_None) for a task's vault note — the
    derived Usage block + (opt-in) Prompts trail. Usage is read from the persisted
    ledger (cheap, no transcript scan); the prompt trail is passed through ONLY when
    the opt-in --obsidian-prompts config is on (a synced vault may leave the
    machine). Fully guarded — any failure degrades to (None, None) so the note
    still writes and the mutation never breaks."""
    try:
        import config as _cfg, export as _export
        include = {"usage"}
        if _cfg.obsidian_prompts_enabled():
            include.add("prompts")
        _usage_engine()   # point usage's path globals at the frozen engine paths
        return _export.note_context(_backend(), task, include)
    except Exception:
        return None, None


def _related_pairs(task, scan, knowledge, stem_of):
    """The render_note `related` list for one task — shared by the generic export and
    the Obsidian mirror so the two paths carry the SAME graph (one renderer, byte
    parity). UNIVERSAL task↔task edges (stored lineage + derived semantic
    `touches-same`) are ALWAYS included as resolvable `(stem, title, kind)` triples;
    cited-note co-citation `(slug, "knowledge")` pairs are added ONLY when `knowledge`
    (the knowledge-graph gate) is on — so a gate-off render leaks zero knowledge.

    `stem_of(task_id)` maps a related task to its note STEM in the target dir (None ⇒
    that task has no note there, so the edge is dropped — every emitted stem
    resolves). Pure over `scan`; deterministic order (out lineage, reverse lineage,
    touches-same strongest-first, then sorted cited notes)."""
    title_by_id = {t.get("id"): t.get("title") for t in scan}
    pairs, seen = [], set()

    def _add_task(other_id, kind):
        if not other_id or other_id in seen:
            return
        stem = stem_of(other_id)
        if not stem:
            return
        seen.add(other_id)
        pairs.append((stem, title_by_id.get(other_id) or "", kind))

    edges = related_edges(task, scan, semantic=True)
    for e in edges.get("out", []):
        _add_task(e.get("id"), e.get("kind") or "related")
    for e in edges.get("in", []):
        _add_task(e.get("id"), "spawned" if e.get("kind") == "spawned-from" else "related")
    for e in edges.get("semantic", []):
        _add_task(e.get("id"), "touches same")
    if knowledge:
        for slug in sorted(_task_cited_notes(task)):
            if slug not in seen:
                seen.add(slug)
                pairs.append((slug, "knowledge"))
    return pairs


def _knowledge_gate_on():
    """Whether the second-brain co-citation tier is enabled (knowledge-graph flag).
    Guarded — a config hiccup reads as off."""
    try:
        import config as _cfg
        return bool(_cfg.knowledge_graph_enabled())
    except Exception:
        return False


def _export_related_fn(task, scan, stem_of):
    """The `related_fn` the generic export passes to export.export_tasks: universal
    task edges always, co-citation only when the knowledge-graph gate is on, with
    stems resolved against the export dir (via `stem_of`). Thin wrapper over
    _related_pairs so the export and the vault mirror share one graph. The category-hub
    link is appended, then the orthogonal grouped-story links (each when its toggle is on)."""
    pairs = _related_pairs(task, scan, _knowledge_gate_on(), stem_of)
    return _with_story_hubs(_with_category_hub(pairs, task, _owner()), task, _owner())


def _category_hubs_on():
    """Whether the category-hubs tier is enabled (default ON). Guarded — a config
    hiccup reads as off (no link, no hub pages), never crashing an export."""
    try:
        import config as _cfg
        return bool(_cfg.obsidian_category_hubs_enabled())
    except Exception:
        return False


def _subgroups_on():
    """Whether emergent sub-groups are active — the subgroups toggle (default ON) AND
    the category-hubs toggle (nesting: a within-category sub-hub is meaningless without
    the category hub). Guarded — a config hiccup reads as off."""
    try:
        import config as _cfg
        return _category_hubs_on() and bool(_cfg.obsidian_subgroups_enabled())
    except Exception:
        return False


def _story_groups_on():
    """Whether story hubs are active — the story-groups toggle (default ON) AND the
    category-hubs toggle (nesting, mirroring sub-groups). Guarded — a config hiccup
    reads as off. Stories are an ORTHOGONAL axis to categories, but the toggle nests
    under category hubs so one switch turns the whole clustering layer off."""
    try:
        import config as _cfg
        return _category_hubs_on() and bool(_cfg.obsidian_story_groups_enabled())
    except Exception:
        return False


# The full-board sub-group token map ({task_id: token}) for the CURRENT sync/export
# operation. Set by _begin_subgroups() around a multi-task loop so the per-note link
# resolves in O(1) and is consistent with the pages; None ⇒ resolve lazily per task.
_SUBGROUP_MAP = None

# The full-board GROUPED story ids (a set) for the CURRENT sync/export operation —
# story ids referenced by >= 1 tasks, so the per-note [[stories/<id>]] link is added
# only for stories that actually get a hub. Set by _begin_subgroups(); None ⇒ lazy.
_STORY_GROUP_IDS = None


def _compute_subgroup_token_map(tasks):
    """`{task_id: token}` for every task in `tasks` that belongs to an emergent
    sub-group in its category, over the whole given task set. Groups tasks by their
    resolved category key (matching the disk hubs), tokenises title+slug via
    categories.detect_subgroups, and inverts the result. Empty/guarded — subgroups are
    a best-effort refinement and must never break an export."""
    try:
        import categories, obsidian_sync
        by_cat = {}
        for t in tasks:
            key = categories.normalize((t or {}).get("color") or "")
            by_cat.setdefault(key, []).append(
                {"id": t.get("id"), "title": t.get("title") or "",
                 "slug": obsidian_sync.slugify(t.get("title"))})
        groups = categories.detect_subgroups(by_cat)
        return {m["id"]: token
                for toks in groups.values()
                for token, members in toks.items()
                for m in members}
    except Exception:
        return {}


def _compute_story_group_ids(tasks):
    """The set of story ids referenced by >= 1 of `tasks` — the ids that get a story
    hub, keyed identically to the emitted `stories/<id>.md` pages (via
    obsidian_sync.story_ref over each task's STRUCTURED `stories` field, never title
    tokens). A task counts once per distinct id. Guarded — story groups are a
    best-effort refinement and must never break an export."""
    try:
        import obsidian_sync
        counts = {}
        for t in tasks:
            ids = set()
            for ent in merged_stories(t):
                sid, _ = obsidian_sync.story_ref(ent)
                if sid:
                    ids.add(sid)
            for sid in ids:
                counts[sid] = counts.get(sid, 0) + 1
        return {sid for sid, c in counts.items() if c >= 1}
    except Exception:
        return set()


def _begin_subgroups():
    """Compute the full-board sub-group map AND the grouped-story-id set ONCE for a
    multi-task sync/export, so the per-note link resolution below is O(1) and
    byte-consistent with the emitted pages. A no-op (empty) when the respective toggle
    is off. Paired with _end_subgroups(). (Named for sub-groups historically; it now
    also preps the orthogonal story axis, which brackets the same operations.)"""
    global _SUBGROUP_MAP, _STORY_GROUP_IDS
    tasks = all_tasks()
    _SUBGROUP_MAP = _compute_subgroup_token_map(tasks) if _subgroups_on() else {}
    _STORY_GROUP_IDS = _compute_story_group_ids(tasks) if _story_groups_on() else set()


def _end_subgroups():
    global _SUBGROUP_MAP, _STORY_GROUP_IDS
    _SUBGROUP_MAP = None
    _STORY_GROUP_IDS = None


def _subgroup_token_for(task):
    """The emergent sub-group token this task belongs to within its category, or None.
    Reads the active per-operation map when set (a full sync/export), else computes it
    lazily over the full board (a single-task mirror). None when subgroups are off."""
    if not _subgroups_on():
        return None
    tid = (task or {}).get("id")
    m = _SUBGROUP_MAP if _SUBGROUP_MAP is not None else _compute_subgroup_token_map(all_tasks())
    return m.get(tid)


def _category_hub_pair(task, owner=None):
    """The `## Related` pair linking a task note to its MOST-SPECIFIC hub:
    `(target, LABEL, None)` → `[[<target>|<LABEL>]]` (a 3-tuple with no ' — kind' suffix).
    `target` is `categories/<slug>` (the category hub) — or, when the task belongs to an
    emergent sub-group (WS11), the nested `categories/<slug>/<token>` sub-hub, with LABEL
    the uppercased token instead of the category [TAG]. An `<owner>/` prefix scopes it to
    a shared vault so each owner resolves to their OWN subtree. `slug`/`TAG` come from the
    task's category (uncategorised ⇒ the default, e.g. `general`/`GENERAL`). None when the
    category-hubs toggle is off — a gate-off render carries no category link. Guarded —
    any failure ⇒ None."""
    if not _category_hubs_on():
        return None
    try:
        import categories
        meta = categories.hub_meta((task or {}).get("color") or "")
        base = "categories/%s" % meta["slug"]
        label = meta["tag"]
        token = _subgroup_token_for(task)
        if token:
            base = "%s/%s" % (base, token)
            label = token.upper()
        if owner:
            base = "%s/%s" % (owner, base)
        return (base, label, None)
    except Exception:
        return None


def _with_category_hub(pairs, task, owner=None):
    """Append the category-hub `## Related` pair to an already-built related list (when
    the toggle is on). Shared by the vault mirror and the generic export so both carry
    the SAME category link. Returns the (possibly extended) list."""
    pair = _category_hub_pair(task, owner)
    return (list(pairs) + [pair]) if pair else pairs


def _story_hub_pairs(task, owner=None):
    """The `## Related` pairs linking a task note to each GROUPED story hub it references:
    `(target, "Story <id>", None)` → `[[<target>|Story <id>]]`, `target` = `stories/<slug>`
    (owner-prefixed for a shared vault). One per distinct grouped story id (referenced by
    >= 1 tasks); [] when the story-groups toggle is off or the task cites no grouped story.
    Cross-category — added IN ADDITION to the category pair. The grouped-id set comes from
    the active per-operation map when set (a full sync/export), else is computed lazily.
    Guarded — any failure ⇒ []."""
    if not _story_groups_on():
        return []
    try:
        import obsidian_sync
        grouped = (_STORY_GROUP_IDS if _STORY_GROUP_IDS is not None
                   else _compute_story_group_ids(all_tasks()))
        out, seen = [], set()
        for ent in merged_stories(task):
            sid, _ = obsidian_sync.story_ref(ent)
            if not sid or sid in seen or sid not in grouped:
                continue
            seen.add(sid)
            base = "stories/%s" % obsidian_sync.story_slug(sid)
            if owner:
                base = "%s/%s" % (owner, base)
            out.append((base, "Story %s" % sid, None))
        return out
    except Exception:
        return []


def _with_story_hubs(pairs, task, owner=None):
    """Append the grouped-story `## Related` pairs to an already-built related list.
    Shared by the vault mirror and the generic export so both carry the SAME story links
    (orthogonal to, and after, the category link). Returns the (possibly extended) list."""
    extra = _story_hub_pairs(task, owner)
    return (list(pairs) + extra) if extra else pairs


def _sync_obsidian_category_hubs(vault, owner):
    """Regenerate (toggle on) or prune (toggle off) the vault's category-hub pages
    under `<plugin>/<owner>/categories/`, from the sidecar index — like index.md.
    Best-effort + fully guarded: it must never break the mutation/export that triggered
    it, nor loop back into an export."""
    try:
        import export as _export, obsidian_sync
        pdir = obsidian_sync.owner_dir(obsidian_sync.plugin_dir(vault), owner)
        if os.path.isdir(pdir):
            _export.sync_category_hubs(pdir, enabled=_category_hubs_on(),
                                       subgroups=_subgroups_on())
    except Exception:
        pass


def _sync_obsidian_story_hubs(vault, owner):
    """Regenerate (toggle on) or prune (toggle off) the vault's story-hub pages under
    `<plugin>/<owner>/stories/`, from the on-disk notes — like the category hubs. The
    orthogonal, cross-category axis. Best-effort + fully guarded: it must never break
    the mutation/export that triggered it, nor loop back into an export."""
    try:
        import export as _export, obsidian_sync
        pdir = obsidian_sync.owner_dir(obsidian_sync.plugin_dir(vault), owner)
        if os.path.isdir(pdir):
            _export.sync_story_hubs(pdir, enabled=_story_groups_on())
    except Exception:
        pass


def _obsidian_related_links(task):
    """The render_note `related` list for a task's VAULT note — universal task↔task
    edges (lineage + semantic `touches-same`) ALWAYS, cited-note knowledge edges only
    when the knowledge-graph gate is on. Task stems resolve against the vault's
    sidecar index (via obsidian_sync.note_stem) so `## Related` links never dangle.
    Fully guarded — any failure degrades to []."""
    try:
        import obsidian_sync
        vault = _obsidian_vault()
        pdir = obsidian_sync.owner_dir(obsidian_sync.plugin_dir(vault), _owner()) if vault else None
        scan = all_tasks()
        by_id = {t.get("id"): t for t in scan}
        def stem_of(tid):
            other = by_id.get(tid)
            return obsidian_sync.note_stem(other, pdir) if other else None
        pairs = _related_pairs(task, scan, _knowledge_gate_on(), stem_of)
        return _with_story_hubs(_with_category_hub(pairs, task, _owner()), task, _owner())
    except Exception:
        return []


def _obsidian_sync(task):
    """Mirror a single task's note into the vault (create/update/done/save). No-op
    when export is off. Returns the note filename on success (used by the daily-note
    path), else None.

    Sandbox-safety net (mirrors the digest_dirty design): while a vault IS
    configured, a FAILED export marks the task obsidian_dirty and persists it, so
    the silent vault-drift becomes a recorded, re-syncable state; a SUCCESSFUL
    export clears that flag (and the persistent-failure signal).

    The mid-turn failure is SILENT on purpose: most mutations run in a SANDBOXED
    session that can't write a protected-root vault, but the unsandboxed Stop /
    SessionStart hook auto-flush (Fix B) heals it seconds later — so nagging every
    turn would be noise. The loud, actionable hint fires ONLY when a task is STILL
    dirty AFTER a hook flush also failed (obsidian_flush_failed) — a genuine
    persistent failure (vault gone, or even the unsandboxed hook is denied). The
    bookkeeping is fully guarded — it must never break the mutation that triggered
    the export, nor loop back into export (it writes via a direct save_task, which
    does NOT re-trigger the hook)."""
    vault = _obsidian_vault()
    if not vault:
        return None   # export OFF — never mark dirty (nothing to resync into)
    fname = None
    try:
        import obsidian_sync
        usage, prompts = _obsidian_note_data(task)
        related = _obsidian_related_links(task)
        fname = obsidian_sync.export_task(task, vault, usage=usage, prompts=prompts,
                                          related=related, owner=_owner())
    except Exception:
        # SILENT on purpose — no stderr line. A sandboxed hot-path denial is the common
        # case and self-heals via the unsandboxed hook flush; the dirty flag (surfaced by
        # `obsidian --status`) is the trace, and the loud remedy hint is reserved for a
        # confirmed PERSISTENT failure (below). Per-mutation noise would defeat Fix B.
        pass
    try:
        if fname:
            changed = clear_obsidian_dirty(task)
            if task.pop("obsidian_flush_failed", None) is not None:
                changed = True
            if changed:
                save_task(task)
            _clear_obsidian_perm_marker()   # things wrote again — re-arm the one-shot hint
            # The note wrote cleanly, so its category hub can be refreshed in place
            # (regenerated from the sidecar; toggle-off prunes it). Guarded no-op if the
            # toggle is off and there's nothing to prune.
            _sync_obsidian_category_hubs(vault, _owner())
            _sync_obsidian_story_hubs(vault, _owner())
        else:
            if mark_obsidian_dirty(task):
                save_task(task)
            # Persistent: a prior unsandboxed hook flush ALSO couldn't write this task.
            if task.get("obsidian_flush_failed"):
                _warn_obsidian_persistent_once(vault)
    except Exception:
        pass   # dirty/hint bookkeeping must never break the mutation
    return fname


def _obsidian_event(task, event):
    """Export the task AND (when the daily-note setting is on) append a dated line
    to the vault's daily note. `event` is "closed" or "checkpoint". Fully guarded."""
    fname = _obsidian_sync(task)
    if not fname:
        return
    try:
        import config, obsidian_sync
        if config.obsidian_daily_note_enabled():
            link = fname[:-3] if fname.endswith(".md") else fname
            obsidian_sync.append_daily_note(
                config.obsidian_vault(), link, event, task.get("title", ""),
                config.obsidian_daily_heading(), owner=_owner())
    except Exception as e:
        sys.stderr.write("task-station: Obsidian daily-note append failed: %s\n" % e)


# ------------------------------------------------- native Tasks interop ----
# READ-ONLY bridge to Claude Code's in-session native Tasks (see native_tasks.py).
# Native Tasks = ephemeral, per-session orchestration; task-station = the durable
# cross-session console. We surface native lists so nothing is invisible, and let
# `adopt` promote a native item worth tracking durably — but NEVER write the native
# store. Every helper degrades to "nothing" if native_tasks or the store is absent.


def _native_lists():
    """Best-effort recent native task lists — [] when the module/store is
    unavailable. Guarded so a missing native_tasks.py or unreadable ~/.claude/tasks
    never breaks a board render."""
    try:
        import native_tasks
        return native_tasks.list_native_lists()
    except Exception:
        return []


def _format_native():
    """Compact read-only listing of Claude Code's recent native task lists: one
    section per list (short uuid + relative mtime), each item as `<glyph> <id>
    <subject>`. Says so plainly when there's nothing recent. Read-only — the native
    store is never written."""
    try:
        import native_tasks
    except Exception:
        return "Native Tasks interop unavailable (native_tasks.py missing)."
    lists = _native_lists()
    if not lists:
        return ("No recent native task lists. Claude Code 2.1+ writes in-session "
                "tasks under ~/.claude/tasks; nothing recent to show.")
    out = ["Native Tasks (Claude Code in-session; READ-ONLY) — adopt one to track it "
           "durably:  /todo adopt <list>:<id>"]
    for L in lists:
        out.append("")
        out.append("%s  (%s)" % (L["short"], rel_time(L["mtime"])))
        for it in L["items"]:
            glyph = native_tasks.STATUS_GLYPH.get(it["status"], "○")
            out.append("  %s %s %s" % (glyph, it["id"], it["subject"]))
    return "\n".join(out)


def _native_footer_line():
    """One-line NATIVE summary for the terminal board footer, or None. Shown ONLY
    when at least one recent native list has OPEN items — a pointer, not the full
    dump (the board stays lean; `/todo native` shows the detail)."""
    lists = [L for L in _native_lists() if L["open_count"] > 0]
    if not lists:
        return None
    n_open = sum(L["open_count"] for L in lists)
    return ("NATIVE: %d open item(s) in %d Claude Code native list(s) — /todo native "
            "to view · /todo adopt <list>:<id> to track durably"
            % (n_open, len(lists)))


def cmd_native(a):
    """`task-station native` (and `/todo native`) — read-only listing of Claude
    Code's recent in-session native task lists."""
    print(_format_native())


def cmd_adopt(a):
    """`task-station adopt --native <list-prefix>:<id>` — promote a Claude Code
    native task into a durable station task. Creates it from the native item's
    subject/description (colour black/GENERAL, effort S), records provenance in the
    summary + activity log, and prints the created-task line. NEVER writes the
    native store (read-only interop)."""
    ref = (getattr(a, "native", None) or "").strip()
    if not ref:
        print("usage: adopt --native <list-prefix>:<id>")
        return
    try:
        import native_tasks
    except Exception:
        print("Native Tasks interop unavailable (native_tasks.py missing).")
        return
    L, item = native_tasks.find_native_item(ref)
    if not item:
        print("No native task matching '%s'. Run `native` to list recent native "
              "tasks, then adopt with <list-prefix>:<id>." % ref)
        return
    prov = "adopted from native task %s:%s" % (L["short"], item["id"])
    title = item["subject"] or "Adopted native task"
    desc = item["description"]
    summary = ("%s\n\n(%s)" % (desc, prov)) if desc else "(%s)" % prov
    task = new_task(title, summary, color="black", effort="s")
    create_with_seq(task)              # atomically mint the stable number + persist
    touch(task, note=prov)
    save_task(task)
    _stream_emit("task.created", task, _stream_created_data(task), getattr(a, "session", None))
    print("📋 Adopted native task %s:%s → task [%s] #%s %s"
          % (L["short"], item["id"], task["id"][:8], task["seq"], task["title"]))
    for line in cat_lines(task.get("color")):
        print(line)
    auto_enable_category(task.get("color"))


def _format_list(closed_limit=MAX_CLOSED_IN_LIST):
    # closed_limit caps how many closed tasks are shown (most recent first).
    # None means "show every closed task" (`/todo all`); an int shows that many
    # (`/todo closed` / `/todo closed N`). The default keeps the bare `/todo`
    # list short.
    ensure_seqs()                      # guarantee every task has its stable number
    listing = sorted_tasks()
    if not listing:
        return ("No tasks yet. One will be tracked automatically once the work "
                "in a session becomes clear, or say so explicitly.")
    lines = []
    closed_total = sum(1 for t in listing if is_closed(t))
    capped = closed_limit is not None and closed_total > closed_limit
    if capped:
        shown = 0
        trimmed = []
        for t in listing:
            if is_closed(t):
                shown += 1
                if shown > closed_limit:
                    continue
            trimmed.append(t)
        listing = trimmed
    # Two sections: the board (open + active, glyph-distinguished) then closed.
    last_section = None
    for t in listing:
        section = "CLOSED" if is_closed(t) else "OPEN"
        if section != last_section:
            lines.append("")
            lines.append(section)
            last_section = section
        tag = cat_tag(t.get("color"), pad=True)
        eff = effort_cell(t.get("effort"))
        marker = _live_marker(t)
        g = status_glyph(t)            # leading lifecycle glyph, before the number
        # Compact progress rollup folded INTO the 40-wide Task cell (no new column,
        # so the grid + the /todo verbatim contract are untouched): "Title  ✓2/5",
        # only when the task has steps. Truncated with the title at 40 chars.
        d, m = step_progress(t)
        title_cell = ("%s  ✓%d/%d" % (t["title"], d, m)) if m else t["title"]
        if tag:
            lines.append("%s %3d  %-40.40s  %s  %s  %s%s"
                         % (g, t["seq"], title_cell, tag, eff, rel_time(t.get("updated_ts")), marker))
        else:
            lines.append("%s %3d  %-40.40s  %s  %s%s"
                         % (g, t["seq"], title_cell, eff, rel_time(t.get("updated_ts")), marker))
    if capped:
        lines.append("     … %d older closed task(s) hidden  ·  show more with /todo closed N "
                     "or /todo all  ·  reachable by number: /todo <n> or /done <n>"
                     % (closed_total - closed_limit))
    lines.append("")
    lines.append(status_legend())
    lines.append(effort_legend())
    if cats:
        lines.append(cats.legend())
    # A one-line NATIVE pointer — only when Claude Code's in-session native tasks
    # have open items — so the durable board stays aware of them without dumping
    # the whole list (see `/todo native`). Kept lean and conditional.
    nf = _native_footer_line()
    if nf:
        lines.append("")
        lines.append(nf)
    lines.append(commands_footer())
    return ("Tasks (not-closed first, then by recent activity):\n"
            + "\n".join(lines))


def _md_escape(text):
    """Escape the characters that would break a GitHub table cell."""
    return (text or "").replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def _md_effort(effort):
    """`▰▱` gauge + size for a Markdown cell — same content as the ASCII column
    (reuses effort_cell) but without its fixed-width padding."""
    return effort_cell(effort).rstrip()


def _md_task_row(task):
    """One GitHub-table row: `|  | # | Task | Category | Effort | Activity |`.
    The leading STATUS column holds the lifecycle glyph (`●` active / `○` open /
    `✕` closed); the `#` cell holds the bare seq number only. The Task cell carries
    the ` ⧉N` live-session marker (when >1), mirroring the ASCII list; the Category
    cell keeps the `<emoji> [TAG]` intact."""
    st = task_status(task)
    status_cell = STATUS_GLYPH_CLOSED if st == STATUS_CLOSED else STATUS_GLYPH.get(st, "")
    # Compact progress rollup appended to the Task cell (no new column), only when
    # the task has steps — mirrors the ASCII list's "✓N/M".
    d, m = step_progress(task)
    progress = ("  ✓%d/%d" % (d, m)) if m else ""
    return "| %s | %s | %s | %s | %s | %s |" % (
        status_cell,
        task.get("seq", ""),
        _md_escape(task["title"]) + _live_marker(task) + progress,
        cat_tag(task.get("color")),
        _md_effort(task.get("effort")),
        rel_time(task.get("updated_ts")),
    )


def _format_list_md(closed_limit=MAX_CLOSED_IN_LIST):
    """Markdown form of the /todo list — what the skill now prints VERBATIM (no
    hand-transcription). Two GitHub tables, Open first then Closed, preserving the
    tracker's ordering; columns are a centered STATUS glyph · # (stable seq,
    right-aligned) · Task · Category (`<emoji> [TAG]`) · Effort (`▰▱` bar + size)
    · Activity (relative time). Honors the same closed-limit logic as the ASCII
    list (default MAX_CLOSED_IN_LIST, `all`, or N) and repeats the hidden-older
    note after the Closed table, then the Commands footer mini-table."""
    ensure_seqs()
    listing = sorted_tasks()
    if not listing:
        return ("No tasks yet. One will be tracked automatically once the work "
                "in a session becomes clear, or say so explicitly.")
    board_tasks = [t for t in listing if is_on_board(t)]   # open + active
    closed_tasks = [t for t in listing if is_closed(t)]
    closed_total = len(closed_tasks)
    capped = closed_limit is not None and closed_total > closed_limit
    shown_closed = closed_tasks[:closed_limit] if capped else closed_tasks

    out = []
    if board_tasks:
        out.append("### Open")
        out.append(_MD_HEADER)
        out.extend(_md_task_row(t) for t in board_tasks)
    if shown_closed:
        if out:
            out.append("")
        out.append("### Closed")
        out.append(_MD_HEADER)
        out.extend(_md_task_row(t) for t in shown_closed)
    if capped:
        out.append("")
        out.append("… %d older closed task(s) hidden — show more with `/todo closed N` "
                   "or `/todo all`." % (closed_total - closed_limit))
    out.append("")
    out.append("_%s new · %s in progress · %s closed_" % (
        STATUS_GLYPH[STATUS_OPEN], STATUS_GLYPH[STATUS_ACTIVE], STATUS_GLYPH_CLOSED))
    out.append(commands_footer_md())
    return "\n".join(out)


def _semver_tuple(s):
    m = re.match(r"^\s*(\d+)\.(\d+)\.(\d+)\s*$", str(s or ""))
    return tuple(int(x) for x in m.groups()) if m else None


def _semver_gt(a, b):
    """True iff version `a` is STRICTLY newer than `b` (both X.Y.Z). False when either
    is missing/unparseable — we never block a write on absent version info."""
    ta, tb = _semver_tuple(a), _semver_tuple(b)
    return bool(ta and tb and ta > tb)


def _existing_board_version(path):
    """The plugin version stamped into an existing board.html (its
    `<meta name="ts-board-version">`), or None if absent/unreadable. Only the head is
    read — the stamp lives in <head>."""
    try:
        with open(path, encoding="utf-8") as f:
            head = f.read(4096)
    except Exception:
        return None
    m = _BOARD_VER_RE.search(head)
    return m.group(1) if m else None


def _autolink_task_signals(task, session=None):
    """F6.2 cross-person auto-link: for every PR/story signal on `task`, scan mounted peer
    feeds for a task carrying the SAME signal id and auto-create the correspondence link +
    mint a memo. Idempotent — add_link dedups, so an already-linked pair mints nothing.
    Signal ids are the SAME normalized forms the feeds carry (feeds._pr_signal_id /
    story_ref), so a captured PR pairs with the peer reviewing it. Returns links created."""
    try:
        import feeds as _feeds
    except Exception:
        return 0
    my_sigs = {}                                   # canonical id → "PR"|"story"
    for p in merged_prs(task):
        sid = _feeds._pr_signal_id(p.get("url"))
        if sid:
            my_sigs.setdefault(sid, "PR")
    for s in _story_refs(task):
        if s.get("id"):
            my_sigs.setdefault(s["id"], "story")
    if not my_sigs:
        return 0
    made = 0
    my = "%s-%s" % (_owner() or "me", task.get("seq", task["id"][:8]))
    for feed in _all_peer_feeds():
        alias = feed.get("alias") or feed.get("owner")
        for ftask in feed.get("tasks") or []:
            fsig = ftask.get("signals") or {}
            fids = set(fsig.get("prs") or []) | set(fsig.get("stories") or [])
            shared = [s for s in my_sigs if s in fids]
            if not shared:
                continue
            u8 = (ftask.get("uuid8") or "")[:8]
            handle = ftask.get("handle") or ("%s-%s" % (alias, u8))
            if add_link(task, alias, u8, handle, kind="signal"):
                sig = shared[0]
                # Two-way wording: my side links to the peer over the shared signal (the
                # peer independently captures + links back on sync — the reciprocal side).
                memo_send(task, "%s linked: shared %s %s ↔ %s"
                          % (my, my_sigs[sig], sig, handle), from_sid=None,
                          routine=True)
                add_event(task, "autolink",
                          "auto-linked ↔ %s (shared %s %s)" % (handle, my_sigs[sig], sig),
                          session)
                made += 1
    return made


def _augment_graph_foreign(graph, raw, foreign_vms, owner_alias, brain_by_seq):
    """Return a NEW render-graph dict with owner/brain stamped on every LOCAL task node
    (owner=`owner_alias`) plus FOREIGN task nodes and dashed cross-brain shared-signal
    edges (kind `xbrain`) for peer tasks that share a PR/story id with a local task. The
    local endpoint node is created if the base graph didn't already carry it (a task that
    shares a signal ONLY across brains). Input is not mutated; foreign nodes appear only
    when they connect (mirrors the graph's connected-only rule). Pure; guarded by caller."""
    import copy
    g = {"nodes": [copy.deepcopy(n) for n in (graph or {}).get("nodes") or []],
         "edges": [copy.deepcopy(e) for e in (graph or {}).get("edges") or []],
         "singletons": dict((graph or {}).get("singletons") or {})}
    by_id = {n["id"]: n for n in g["nodes"]}
    # stamp owner/brain on the existing local task nodes.
    for n in g["nodes"]:
        if n.get("type") == "task":
            n["owner"] = owner_alias
            n["brain"] = brain_by_seq.get(n.get("seq"), "main")
    # local signal index — feed-normalized ids (feeds._pr_signal_id) so they match the
    # foreign feeds' signal ids; stories keyed by their story_ref id.
    import feeds as _feeds
    local_prs, local_stories, local_meta = {}, {}, {}
    for t in raw:
        seq = t.get("seq")
        if seq is None:
            continue
        prs = set(_feeds._pr_signal_id(p.get("url")) for p in merged_prs(t))
        prs.discard("")
        stories = set(s.get("id") for s in _story_refs(t) if s.get("id"))
        local_prs[seq] = prs
        local_stories[seq] = stories
        cur = task_status(t)
        local_meta[seq] = {
            "title": t.get("title", ""), "color": t.get("color"), "status": cur,
            "glyph": STATUS_GLYPH_CLOSED if cur == STATUS_CLOSED
            else STATUS_GLYPH.get(cur, "○")}

    def _ensure_local(seq):
        nid = "t:%d" % seq
        if nid in by_id:
            return nid
        m = local_meta.get(seq) or {}
        node = {"id": nid, "type": "task", "seq": seq, "title": m.get("title", ""),
                "color": m.get("color"), "status": m.get("status"),
                "glyph": m.get("glyph"), "deg": 0,
                "owner": owner_alias, "brain": brain_by_seq.get(seq, "main")}
        g["nodes"].append(node)
        by_id[nid] = node
        return nid

    for fvm in foreign_vms:
        f_prs = set(fvm.get("signal_prs") or [])
        f_stories = set(fvm.get("signal_stories") or [])
        if not f_prs and not f_stories:
            continue
        matches = []                                    # (local_seq, [shared labels])
        for seq in local_meta:
            shared = ["PR %s" % parse_pr_number(pid)
                      for pid in sorted(f_prs & local_prs.get(seq, set()))]
            shared += ["story %s" % sid
                       for sid in sorted(f_stories & local_stories.get(seq, set()))]
            if shared:
                matches.append((seq, shared))
        if not matches:
            continue
        fid = "f:%s:%s" % (fvm.get("owner"), fvm.get("uuid8") or fvm.get("handle"))
        if fid not in by_id:
            fnode = {"id": fid, "type": "task", "seq": None, "foreign": True,
                     "title": fvm.get("title", ""), "color": fvm.get("color"),
                     "status": fvm.get("status"), "glyph": "◇",
                     "owner": fvm.get("owner"), "brain": fvm.get("brain"),
                     "owner_color": fvm.get("owner_color"),
                     "handle": fvm.get("handle"), "label": fvm.get("handle"), "deg": 0}
            g["nodes"].append(fnode)
            by_id[fid] = fnode
        for seq, labels in matches:
            lid = _ensure_local(seq)
            g["edges"].append({"a": fid, "b": lid, "kind": "xbrain", "dir": "none",
                               "weight": 1, "via": labels})

    # F5: explicit correspondence links (`task-station link` / fork auto-link) draw a
    # dashed cross-brain PAIR edge between the local task and the peer it points at — even
    # with NO shared signal. The foreign endpoint node is created on demand. Dedup: never
    # add a second edge between an already-connected pair (shared-signal edge wins).
    fvm_by_key = {}
    for fvm in foreign_vms:
        fvm_by_key[(fvm.get("owner"), (fvm.get("uuid8") or "")[:8])] = fvm

    def _edge_exists(x, y):
        return any((e["a"] == x and e["b"] == y) or (e["a"] == y and e["b"] == x)
                   for e in g["edges"])

    for t in raw:
        seq = t.get("seq")
        if seq is None:
            continue
        for lk in (t.get("links") or []):
            fvm = fvm_by_key.get((lk.get("alias"), (lk.get("uuid8") or "")[:8]))
            if not fvm:
                continue
            fid = "f:%s:%s" % (fvm.get("owner"), fvm.get("uuid8") or fvm.get("handle"))
            if fid not in by_id:
                fnode = {"id": fid, "type": "task", "seq": None, "foreign": True,
                         "title": fvm.get("title", ""), "color": fvm.get("color"),
                         "status": fvm.get("status"), "glyph": "◇",
                         "owner": fvm.get("owner"), "brain": fvm.get("brain"),
                         "owner_color": fvm.get("owner_color"),
                         "handle": fvm.get("handle"), "label": fvm.get("handle"), "deg": 0}
                g["nodes"].append(fnode)
                by_id[fid] = fnode
            lid = _ensure_local(seq)
            if not _edge_exists(fid, lid):
                g["edges"].append({"a": fid, "b": lid, "kind": "xbrain", "dir": "none",
                                   "weight": 1, "via": ["linked ↔ %s" % (fvm.get("handle") or "")]})
    # recompute degree over the augmented edge set.
    deg = {}
    for e in g["edges"]:
        deg[e["a"]] = deg.get(e["a"], 0) + 1
        deg[e["b"]] = deg.get(e["b"], 0) + 1
    for n in g["nodes"]:
        n["deg"] = deg.get(n["id"], 0)
    return g


def write_board(guard_downgrade=False):
    """Render the self-contained HTML board of every task (open + closed) to
    <data_dir>/board.html and return its path. THE board — there is one renderer
    (`tools/render_board.py`), no engine choice. Inline CSS, no server, no external
    assets, no LLM. Shared by `board` and `/todo board`.

    Also exports this machine's feed to <data_dir>/feeds/self.js via `lib/feeds.py` (see
    the export call below), so the feed root is always current for the demo seeder and
    the future sync transport. Federation (peer/org feeds → read-only foreign rows) is
    part of THIS render, gated by `config --interbrain`.

    `guard_downgrade` (the passive/auto path — maybe_refresh_board): refuse to overwrite
    an existing board that a NEWER plugin version rendered, so a stale older-version
    session's turn-end refresh can't clobber a current render back to old markup. The
    explicit `board` command leaves it False (an intentional render always writes)."""
    ensure_seqs()
    raw = sorted_tasks()
    # WS4: build the reverse relation-edge index ONCE (O(total edges)) so each card's
    # incoming ("spawned #N" / "related ←") edges are an O(1) lookup rather than an
    # N² per-task scan. Maps a target task id → [{seq, id, kind, status}] for every
    # task whose `related` list points at it. The SOURCE task's `id` rides along so
    # `canonical_relations` can dedup a reciprocal pair on the task itself rather
    # than on its machine-local seq.
    rev_map = {}
    for t in raw:
        st = task_status(t)
        for r in (t.get("related") or []):
            tgt = r.get("id")
            if tgt:
                rev_map.setdefault(tgt, []).append(
                    {"seq": t.get("seq"), "id": t.get("id"),
                     "kind": r.get("kind"), "status": st})
    # The scan's own answer to "is this predecessor out of the way" — CLOSED or every
    # exit condition met, recursively over the subtree (an orchestrator with an unbuilt
    # child is NOT settled even once its own checklist is green). Built ONCE here, same
    # reason as rev_map: a `depends-on` chip that reused only `status` would rebuild the
    # "closed dependency still blocks" bug in a narrower form (see `_board_related`).
    is_settled = _loop.settled_fn(raw)
    tasks_by_id = {t.get("id"): t for t in raw if t.get("id")}
    # WS-D: the knowledge (co-citation) tier is SECOND-BRAIN-GATED — active only when
    # the opt-in flag is on AND a consumer (an Obsidian vault) is configured. Off by
    # default, so the graph below carries only the UNIVERSAL edges (lineage + semantic).
    knowledge_on = False
    try:
        import config as _cfg_kg
        knowledge_on = bool(_cfg_kg.knowledge_graph_enabled()) and bool(_cfg_kg.obsidian_vault())
    except Exception:
        knowledge_on = False
    # WS5: real process liveness (running Claude pids). Fetched HERE — before the
    # view-models — so each hub/worker card gets its true 3-tier state (running vs merely
    # resumable), not just the transcript-derived flag that survives a crash. Reused for
    # the top live-strip below. Guarded: a sessions-dir hiccup must never block the board.
    try:
        import live_sessions
        live = live_sessions.running()
    except Exception:
        live = []
    live_sids = {r["session_id"] for r in live if r.get("session_id")}
    # The set of task seqs that have a RUNNING session right now (the same signal the
    # breathing dot used). A task with a live session DISPLAYS as `active` (green); an
    # in-progress task with none displays as `paused` (yellow). Derived once here.
    live_seqs = {r.get("task_seq") for r in live if r.get("task_seq") is not None}
    vms = [_board_view_model(t, live_sids=live_sids, rev_map=rev_map,
                             knowledge=knowledge_on, live_seqs=live_seqs,
                             is_settled=is_settled, tasks_by_id=tasks_by_id)
           for t in raw]
    # F1/F2: Interbrain gate. When OFF (the default) nothing below runs — no foreign VMs,
    # no owner/handle/brain stamping, no focus strip — so the render is byte-parity with the
    # pre-F1 board (the parity law; the one enumerated exception is the help-panel hint,
    # BOARD-BEHAVIOR.md B14). When ON, self VMs gain their display-only handle + owner +
    # brain, and peer/org feeds are rendered server-side as foreign rows.
    interbrain_on = False
    try:
        interbrain_on = _interbrain_on(paths.data_dir())
    except Exception:
        interbrain_on = False
    owner_alias = "me"
    brain_by_seq = {}
    foreign_vms = []
    org_lbl = "Org brain"
    if interbrain_on:
        try:
            import config as _cfg_own
            owner_alias = _cfg_own.owner() or "me"
            org_lbl = _cfg_own.org_label()
        except Exception:
            owner_alias = "me"
        _bmod = _bcfg = None
        try:
            import brains as _bmod
            _bcfg = _bmod.load(paths.data_dir())
        except Exception:
            _bmod = _bcfg = None
        for _t, _vm in zip(raw, vms):
            _seq = _vm.get("seq")
            _vm["_ib"] = True
            _vm["foreign"] = False
            _vm["owner"] = owner_alias
            _vm["handle"] = ("%s-%s" % (owner_alias, _seq)) if _seq is not None else None
            _brain = "main"
            if _bmod is not None and _bcfg is not None:
                try:
                    _brain = _bmod.brain_for(_bcfg, _t.get("id") or "")
                except Exception:
                    _brain = "main"
            _vm["brain"] = _brain
            if _seq is not None:
                brain_by_seq[_seq] = _brain
        try:
            foreign_vms = foreign_view_models(paths.data_dir())
        except Exception:
            foreign_vms = []
    vms = vms + foreign_vms
    # WS-D: the board-level relation graph for the mini-graph panel. build_render_graph
    # augments build_board_graph (lineage, plus gated co-citation) with category +
    # signal HUB nodes and string ids for the clustered SVG. Empty
    # {nodes:[], edges:[]} on a relation-free store, so the renderer omits the panel.
    # …and the KNOWLEDGE PLANE's corpus, resolved once per render. `board_notes` checks
    # the (separate) knowledge-plane gate itself and returns [] when it is off, so this
    # needs no second gate and a user with no vault gets the byte-identical graph they
    # get today. It never raises: an unreadable or unmounted vault degrades to [].
    try:
        notes = board_notes()
    except Exception:
        notes = []
    try:
        graph = build_render_graph(raw, knowledge=knowledge_on, notes=notes)
    except Exception:
        graph = {"nodes": [], "edges": []}
    # F1: fold foreign nodes + dashed cross-brain shared-signal edges into the graph, and
    # stamp owner/brain on every task node (so the focus filter works for self brains too,
    # not only when peers exist). Only when Interbrain is ON — off leaves the graph
    # byte-identical to pre-F1.
    if interbrain_on:
        try:
            graph = _augment_graph_foreign(graph, raw, foreign_vms,
                                           owner_alias, brain_by_seq)
        except Exception:
            pass
    # A STABLE revision hash over the RAW task records (NOT the view-models, whose
    # relative-time strings drift): it changes iff a task is created/modified — touch()
    # bumps a task's stored timestamp on any mutation — and NOT merely from time
    # passing. The board loads a sibling board.rev.js <script> and reloads only when
    # this changes (file:// browsers block local fetch but DO load local scripts).
    rev = hashlib.sha1(
        json.dumps(raw, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]
    # realpath derefs the ~/.claude/task-station-engine symlink to the real lib/
    # so tools/ (a sibling of lib/) resolves; BASE keeps the stable symlink path
    # for the resume one-liners, so we must NOT use it here.
    # double dirname: this file lives in lib/board/, one level deeper than the engine did
    real_lib = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    tools = os.path.join(os.path.dirname(real_lib), "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import render_board
    theme = variant = variant_label = None
    config_rows = []
    board_autorefresh = False
    bare = False
    try:
        import config
        theme = config.active_theme()
        config_rows = config.board_rows()
        board_autorefresh = config.board_autorefresh_enabled()
        bare = config.bare_commands()
    except Exception:
        pass
    if cats and hasattr(cats, "resolve_variant"):
        try:
            variant = cats.resolve_variant()
        except Exception:
            variant = None
    if variant:
        try:
            variant_label = config._variant_label(variant, theme)
        except Exception:
            variant_label = None
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    # B-TS: also pass the absolute epoch so the board rewrites these to the VIEWER's LOCAL
    # time client-side (the formatted strings above are just the no-JS fallback).
    generated_ts = _now()
    # The footer shows the task-station VERSION + when this installed version was
    # written. The plugin root is CLAUDE_PLUGIN_ROOT when set, else real_lib's parent
    # (real_lib is <ver>/lib, so its parent is <ver>); plugin.json lives under
    # .claude-plugin/. version = its "version" string; updated = its mtime (the install
    # time of this version). Both guarded → "" so the board still renders if absent.
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(real_lib)
    pj = os.path.join(plugin_root, ".claude-plugin", "plugin.json")
    version = ""
    repo_url = ""
    try:
        with open(pj, encoding="utf-8") as f:
            pj_data = json.load(f)
        version = pj_data.get("version") or ""
        # The footer links to the repo: prefer "homepage", fall back to "repository".
        repo_url = pj_data.get("homepage") or pj_data.get("repository") or ""
    except Exception:
        version = ""
        repo_url = ""
    updated_ts = None
    try:
        updated_ts = os.path.getmtime(pj)
        updated = datetime.fromtimestamp(updated_ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        updated = ""
        updated_ts = None
    # WS5: the live-session strip (running Claude processes) reuses `live` fetched
    # above (before the view-models), so the strip and the per-card 3-tier state agree.
    html_doc = render_board.render_html(
        vms, theme=theme, variant=variant, variant_label=variant_label,
        generated=generated, commands=_COMMANDS_HELP, config_rows=config_rows,
        board_autorefresh=board_autorefresh, bare=bare, rev=rev,
        version=version, updated=updated, repo_url=repo_url, live_sessions=live,
        graph=graph, generated_ts=generated_ts, updated_ts=updated_ts,
        interbrain=interbrain_on, org_label=org_lbl)
    out = os.path.join(paths.data_dir(), "board.html")
    # Refuse to DOWNGRADE (passive path only): if the on-disk board was stamped by a
    # newer plugin version than the one rendering now, leave it — a stale older-version
    # session must not overwrite a current render. Missing/unparseable stamp → not a
    # downgrade, so we proceed (upgrading an old, unstamped board is fine).
    if guard_downgrade and _semver_gt(_existing_board_version(out), version):
        return out
    os.makedirs(paths.data_dir(), exist_ok=True)
    # Export this machine's feed alongside the board (<data_dir>/feeds/self.js, plus the
    # archive shard when there is one). Written AFTER the downgrade guard so a refused
    # passive refresh touches nothing at all. Read-only over the store and fully guarded:
    # a feed hiccup must never cost the user their board.
    try:
        import feeds as _feeds
        # Resolve the CALLING generation's engine namespace, not this module's:
        # g('__name__') is the facade's own __name__ — '__main__' when run as the CLI
        # (sys.modules['__main__'] IS the engine module, as before the split), or the
        # unregistered importlib spec name in tests, in which case hand feeds a
        # namespace view of the calling facade's globals (_shared._G). Using this
        # module's __name__ here would grab the NEWEST board.boardio generation, whose
        # facade may be a different engine copy with a different store.
        _self_mod = sys.modules.get(g("__name__"))
        if _self_mod is None:
            import types as _types
            _self_mod = _types.SimpleNamespace(**_shared._G)
        _feeds.export_self_feed(_self_mod, paths.data_dir())
    except Exception:
        pass
    tmp = out + ".tmp." + str(os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(html_doc)
    os.replace(tmp, out)
    # Write the revision hash to a sibling board.rev.js (same dir, board.html basename
    # swapped for board.rev.js) so the change-driven poll can detect a real data change.
    # It is a JS sidecar — `window.__TSREV="<rev>";` — loaded via a dynamically-created
    # <script>, NOT fetch: file:// pages (Safari AND Chrome) BLOCK fetch-ing local files
    # but DO load local <script> resources, so this works with no server. A dynamically-
    # loaded subresource also does NOT trigger the top-level loading bar. Written
    # unconditionally on every regen, incl. the Stop-hook --refresh-if-live path: an
    # unchanged data set yields the same hash, so the page won't reload.
    revpath = os.path.join(os.path.dirname(out), "board.rev.js")
    rtmp = revpath + ".tmp." + str(os.getpid())
    with open(rtmp, "w", encoding="utf-8") as f:
        f.write("window.__TSREV=%s;" % json.dumps(rev))
    os.replace(rtmp, revpath)
    return out


def maybe_refresh_board():
    """Best-effort, silent board regen — only when autorefresh is on AND board.html
    already exists (the user has opened it). Same gate as `board --refresh-if-live`.
    Never creates the file for a non-board user, never prints, never raises.

    Called synchronously at every status-transition (#399) so the persisted board
    never shows stale status between a mutation and the turn-end Stop hook."""
    try:
        import config
        if not config.board_autorefresh_enabled():
            return
        if not os.path.exists(os.path.join(paths.data_dir(), "board.html")):
            return
        # passive refresh: never let a stale older-version session downgrade a newer board.
        write_board(guard_downgrade=True)
    except Exception:
        pass


def _task_cat_tag(task):
    """The bare category TAG for a task ('FEATURE'…) or '' — for auto-attach scoring."""
    try:
        import categories
        meta = categories.hub_meta(task.get("color")) if task.get("color") else None
        return (meta or {}).get("tag") or ""
    except Exception:
        return ""


def _brain_signals(task, session=None):
    """The auto-attach signal bundle for a task (see brains.score_brains). Reads only the
    task blob + the session's recorded cwd + an optional skill hint env — never the LLM."""
    cwd = ""
    if session:
        try:
            cwd = (task.get("session_meta", {}).get(session) or {}).get("cwd") or ""
        except Exception:
            cwd = ""
    if not cwd:
        try:
            cwd = os.getcwd()
        except Exception:
            cwd = ""
    text = " ".join(x for x in (task.get("title") or "", task.get("summary") or "",
                                task.get("goal") or "") if x)
    return {"repos": list(task.get("projects") or []), "cwd": cwd, "text": text,
            "category": _task_cat_tag(task),
            "skill": os.environ.get("TASK_STATION_SKILL_HINT", "")}


def _brain_task_views():
    """Light per-task views for brains.derive (read-only tally; never writes the store):
    `{uuid, status, title, category, signals:{prs,stories}, updated_ts}` for every task."""
    try:
        import feeds as _feeds
    except Exception:
        _feeds = None
    out = []
    for t in sorted_tasks():
        prs = [(_feeds._pr_signal_id(p.get("url")) if _feeds else p.get("url"))
               for p in merged_prs(t)]
        out.append({"uuid": t.get("id") or "", "status": task_status(t),
                    "title": t.get("title") or "", "category": _task_cat_tag(t),
                    "signals": {"prs": [p for p in prs if p],
                                "stories": [s.get("id") for s in _story_refs(t) if s.get("id")]},
                    "updated_ts": t.get("updated_ts") or 0})
    return out


def auto_attach_brain(task, session=None):
    """AUTO-attach `task` to its best-scoring brain (F4) — the user never names one. Scores
    every brain from the task's signals and, when a scored brain clears the threshold,
    writes the winner to brains.json ONLY if the task is still on 'main' and not pinned
    (auto_assign's rule). Fail-open: any hiccup leaves the task on 'main'. Returns the
    resolved brain name ('main' when nothing scored or on error)."""
    try:
        import brains as _brains
        dd = paths.data_dir()
        cfg = _brains.load(dd)
        tid = task.get("id") or ""
        if _brains.is_pinned(cfg, tid) or _brains.brain_for(cfg, tid) != _brains.DEFAULT_BRAIN:
            return _brains.brain_for(cfg, tid)
        winner = _brains.score_brains(cfg, _brain_signals(task, session)).get("winner")
        if winner and winner != _brains.DEFAULT_BRAIN and _brains.auto_assign(cfg, tid, winner):
            _brains.save(cfg, dd)
            return winner
        return _brains.brain_for(cfg, tid)
    except Exception:
        return "main"
