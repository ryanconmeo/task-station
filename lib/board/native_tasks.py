#!/usr/bin/env python3
"""Read-only interop with Claude Code's NATIVE Tasks store.

Claude Code 2.1+ persists in-session tasks as one JSON file per item under
~/.claude/tasks/<list-uuid>/<n>.json (verified item shape: id, subject,
description, status ∈ {completed,pending,in_progress}, plus blocks/blockedBy
we don't render). Task Station NEVER writes there — this module only READS, so
native in-session orchestration and the durable cross-session board coexist.

Everything degrades gracefully: a missing root, a malformed dir, or an unreadable/
non-JSON file yields nothing rather than raising, so interop can never break the
tracker. We also code defensively against missing/extra keys — the schema is
trusted-but-verified, since we can't touch the real store from a sandbox.
"""
import json
import os
import time

RECENT_DAYS = 14
RECENT_SECS = RECENT_DAYS * 86400

# Native status → the tracker's single-width glyph vocabulary (○ new · ◐ in
# progress · ✓ done). Unknown/missing status falls back to ○ at the call site.
STATUS_GLYPH = {"completed": "✓", "in_progress": "◐", "pending": "○"}


def _default_root():
    """~/.claude/tasks, honouring CLAUDE_CONFIG_DIR the same way task-station.py
    resolves PROJECTS_ROOT — so a relocated Claude Code config home (and the test
    harness, which pins CLAUDE_CONFIG_DIR) points at the right store. Resolved at
    call time, not import, so a per-test env override takes effect."""
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    base = os.path.expanduser(base) if base else os.path.expanduser("~/.claude")
    return os.path.join(base, "tasks")


def _root(root=None):
    """The native-tasks dir to read. Precedence: an explicit `root` arg >
    TASK_STATION_NATIVE_TASKS_DIR (the test/override hook) > ~/.claude/tasks
    (CLAUDE_CONFIG_DIR-aware)."""
    if root is not None:
        return root
    return os.environ.get("TASK_STATION_NATIVE_TASKS_DIR") or _default_root()


def _is_open(item):
    """True for a not-completed native item (pending or in_progress). A missing/
    unknown status reads as open — we'd rather over-surface than hide live work."""
    return (item or {}).get("status") != "completed"


def _item_sort_key(item):
    """Stable display order: numeric ids ascending first (1,2,10 not 1,10,2),
    then any non-numeric ids lexically."""
    i = (item or {}).get("id") or ""
    return (0, int(i), "") if i.isdigit() else (1, 0, i)


def _load_item(path):
    """One native task item from its JSON file, defensively normalized to just the
    keys we render. Returns None on any read/parse error or a non-dict payload, so
    the caller silently skips it — a single malformed file never aborts a listing."""
    try:
        with open(path) as f:
            obj = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    return {
        "id": str(obj.get("id") or "").strip(),
        "subject": (obj.get("subject") or "").strip(),
        "description": (obj.get("description") or "").strip(),
        "status": obj.get("status") or "pending",
    }


def _list_items(list_dir):
    """Every parseable `<n>.json` item in one list dir, in display order. Tolerates
    an unreadable dir (→ []) and skips malformed files silently."""
    try:
        names = os.listdir(list_dir)
    except OSError:
        return []
    items = []
    for name in names:
        if not name.endswith(".json"):
            continue
        item = _load_item(os.path.join(list_dir, name))
        if item is not None:
            items.append(item)
    items.sort(key=_item_sort_key)
    return items


def list_native_lists(root=None):
    """Recent native task lists, newest dir first. Each is a dict:
    {uuid, short, mtime, items:[{id,subject,description,status}], open_count}.

    'Recent' = the list dir's mtime is within RECENT_DAYS *or* it still holds any
    non-completed item — so a stale-but-unfinished list stays visible while an
    ancient all-done one drops off. Empty lists (no parseable items) are omitted.
    Never raises: an unreadable/missing root yields []."""
    base = _root(root)
    try:
        entries = os.listdir(base)
    except OSError:
        return []
    now = time.time()
    lists = []
    for name in entries:
        d = os.path.join(base, name)
        if not os.path.isdir(d):
            continue
        try:
            mtime = os.path.getmtime(d)
        except OSError:
            continue
        items = _list_items(d)
        if not items:
            continue
        open_count = sum(1 for it in items if _is_open(it))
        recent = (now - mtime) <= RECENT_SECS or open_count > 0
        if not recent:
            continue
        lists.append({
            "uuid": name,
            "short": name[:8],
            "mtime": mtime,
            "items": items,
            "open_count": open_count,
        })
    lists.sort(key=lambda L: L["mtime"], reverse=True)
    return lists


def find_native_item(ref, root=None):
    """Resolve a '<list-prefix>:<id>' ref to (list_dict, item_dict), or (None, None).

    The list-prefix matches a recent list's uuid by exact match or prefix; the id
    matches an item's id within it. Only recent lists are searched (same set the
    `native` listing shows), so an adopt ref always names something the user just
    saw. First (newest) matching list wins on an ambiguous prefix."""
    if not ref or ":" not in ref:
        return None, None
    prefix, _, item_id = ref.partition(":")
    prefix, item_id = prefix.strip(), item_id.strip()
    if not prefix or not item_id:
        return None, None
    for L in list_native_lists(root):
        if L["uuid"] == prefix or L["uuid"].startswith(prefix):
            for it in L["items"]:
                if it["id"] == item_id:
                    return L, it
    return None, None
