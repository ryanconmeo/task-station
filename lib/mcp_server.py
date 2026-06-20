#!/usr/bin/env python3
# mcp_server.py
"""Task Station's Desktop bridge — an MCP server over the SHARED task store.

Claude Desktop (or any MCP client) can create / read / update tasks in the very
same local store the Claude Code CLI uses: one `tasks.db`, two front doors. This
is the Desktop ↔ Code bridge.

Design — the `mcp` SDK is an OPTIONAL, server-only dependency:

  * The tool LOGIC is plain-stdlib functions (`_list_tasks`, `_create_task`,
    `_get_task`, `_set_status`, `_add_note`) that drive the existing engine
    (`task-station.py`, which itself sits on `paths.py` + `store.py`). They
    return plain strings / dicts and need NOTHING beyond the stdlib — so the
    test suite and the core plugin stay stdlib-only.

  * The `mcp` import (FastMCP) lives behind a lazy import inside `main()` /
    `__main__`. Importing THIS module never imports `mcp`; you only need
    `pip install mcp` to actually *run* the server.

The engine reads `TASK_STATION_HOME` / `CLAUDE_CONFIG_DIR` exactly as the CLI
does, so the bridge writes where the CLI reads with zero extra config. WAL is
already on, so concurrent Desktop + CLI access is safe.
"""
import importlib.util
import os
import sys

_LIB = os.path.dirname(os.path.abspath(__file__))
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

# The engine module file is `task-station.py` — a hyphen, so not import-able by
# name. Load it once via importlib and reuse its public primitives so the bridge
# never forks store/render/lifecycle logic.
_ENGINE = None


def _engine():
    """The task-station engine module (lazy, cached). Reusing it means the bridge
    shares the CLI's store paths, seq numbering, lifecycle rules, and `--format
    md` render verbatim."""
    global _ENGINE
    if _ENGINE is None:
        spec = importlib.util.spec_from_file_location(
            "task_station", os.path.join(_LIB, "task-station.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _ENGINE = mod
    return _ENGINE


def _resolve(ref):
    """A task by seq/id ref, or None — mirrors the CLI's resolution order."""
    ts = _engine()
    return ts.resolve_ref(ref) or ts.load_task(str(ref))


# --------------------------------------------------------------- tool logic ---

def _list_tasks(status="all-open"):
    """The rendered Markdown board — byte-for-byte the CLI's `render --format md`.

    `status`:
      * "all-open" (default) / "open" / "active" — the board (open + active) with
        a capped Closed section, exactly like the CLI's default `--format md`.
      * "all" / "closed" — include EVERY closed task (no cap), for a full history.
    """
    ts = _engine()
    closed_limit = None if status in ("all", "closed") else ts.MAX_CLOSED_IN_LIST
    return ts._format_list_md(closed_limit=closed_limit)


def _create_task(title, summary="", category=None, effort=None, source=None):
    """Create an OPEN (◦) task in the shared store and return the stored dict.

    `category`/`effort` honor the same taxonomy/sizes as the CLI (unknown values
    are dropped by the engine, never guessed). `source` records the originating
    Desktop conversation ref/URL on the task so `get_task` can surface it — the
    Desktop ↔ Code provenance link.
    """
    ts = _engine()
    task = ts.new_task(title or "", summary or "",
                       color=category, effort=effort, status=ts.STATUS_OPEN)
    ts.ensure_seqs()                 # number any pre-seq tasks before we pick ours
    task["seq"] = ts._max_seq() + 1  # stable number, never reused
    if source:
        task["source"] = str(source)
    ts.touch(task, note="created (Desktop bridge)")
    ts.save_task(task)
    return ts.load_task(task["id"])


def _create_confirmation(task):
    """One-line confirmation for the create_task tool (seq + glyph + title)."""
    ts = _engine()
    glyph = ts.STATUS_GLYPH.get(ts.task_status(task), ts.STATUS_GLYPH[ts.STATUS_OPEN])
    line = "Created task #%d %s [%s] %s" % (
        task.get("seq"), glyph, task["id"][:8], task.get("title", ""))
    if task.get("source"):
        line += "\nSource: %s" % task["source"]
    return line


def _get_task(ref):
    """Full task detail as Markdown — title, summary, status glyph, category,
    effort, refs, the `source` conversation link, and the activity log. Returns
    None when no task matches `ref` (caller renders the not-found line)."""
    task = _resolve(ref)
    if not task:
        return None
    return _task_detail_md(task)


def _task_detail_md(task):
    """Render one task as a self-contained Markdown detail view (used by both
    `get_task` and the `task://<seq>` resource)."""
    ts = _engine()
    status = ts.task_status(task)
    glyph = ts.STATUS_GLYPH.get(status, "")
    head = ("%s " % glyph if glyph else "") + status.upper()
    out = ["# %s — task #%s [%s]" % (task.get("title", "Untitled"),
                                     task.get("seq", "?"), task["id"][:8]),
           "",
           "- **Status:** %s" % head]
    color = task.get("color")
    if color:
        tag = ts.cat_tag(color)
        out.append("- **Category:** %s" % (tag or color))
    eff = task.get("effort")
    if eff in ts.EFFORT_GAUGE:
        out.append("- **Effort:** %s %s (%s)"
                   % (ts.EFFORT_GAUGE[eff], eff, ts.EFFORT_WORD[eff]))
    if task.get("source"):
        out.append("- **Source:** %s" % task["source"])
    out.append("- **Created:** %s" % task.get("created_at", ""))
    out.append("- **Updated:** %s" % ts.rel_time(task.get("updated_ts")))
    out.append("")
    out.append("## Summary")
    out.append(task.get("summary") or "_(no summary recorded)_")
    log = task.get("log", [])
    if log:
        out.append("")
        out.append("## Activity (most recent last)")
        for e in log[-12:]:
            when = ts.rel_time(ts._iso_to_ts(e.get("ts", "")))
            out.append("- [%s] %s" % (when, e.get("note", "")))
    return "\n".join(out)


def _set_status(ref, status):
    """Move a task along the lifecycle: open ⇄ active, or closed. Returns the
    updated task dict, or None if no task matches `ref`. Raises ValueError on an
    out-of-range status so a typo never mislabels a task."""
    ts = _engine()
    if status not in (ts.STATUS_OPEN, ts.STATUS_ACTIVE, ts.STATUS_CLOSED):
        raise ValueError(
            "status must be one of open/active/closed, got %r" % (status,))
    task = _resolve(ref)
    if not task:
        return None
    if status == ts.STATUS_CLOSED:
        # Closing mirrors the CLI: set closed, log it, detach any linked sessions
        # so none can silently reopen the task.
        if not ts.is_closed(task):
            task["status"] = ts.STATUS_CLOSED
            ts.touch(task, note="closed (Desktop bridge)")
            ts.save_task(task)
            for sess in list(task.get("sessions", [])):
                if ts.get_link(sess) == task["id"]:
                    ts.clear_link(sess)
                    ts.clear_count(sess)
                    ts.clear_edit_markers(sess)
    else:
        # open ⇄ active via the engine's idempotent transition (logs the change).
        ts.set_status(task, status)
        task["updated_ts"] = ts._now()
        ts.save_task(task)
    return ts.load_task(task["id"])


def _add_note(ref, text):
    """Append a timestamped note to the task's activity log. Returns the updated
    task dict, or None if no task matches `ref`."""
    ts = _engine()
    task = _resolve(ref)
    if not task:
        return None
    ts.add_log(task, text)
    task["updated_ts"] = ts._now()
    ts.save_task(task)
    return ts.load_task(task["id"])


# ------------------------------------------------------- FastMCP server (opt) ---

def main():
    """Run the MCP server over stdio. Imports the optional `mcp` SDK lazily — so
    this is the ONLY place that needs `pip install mcp`."""
    from typing import Optional

    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        sys.stderr.write(
            "task-station MCP bridge needs the `mcp` SDK. Install it with:\n"
            "    pip install mcp\n")
        raise

    server = FastMCP("task-station")

    @server.tool()
    def list_tasks(status: str = "all-open") -> str:
        """The task board as Markdown (the Desktop analog of /todo). `status`:
        all-open (default, open+active) | open | active | closed | all."""
        return _list_tasks(status)

    @server.tool()
    def create_task(title: str, summary: str = "",
                    category: Optional[str] = None, effort: Optional[str] = None,
                    source: Optional[str] = None) -> str:
        """Create an open(◦) task. `category` = a category key/emoji/[TAG];
        `effort` = xs/s/m/l/xl; `source` = the originating Desktop conversation
        ref/URL (stored on the task, surfaced in get_task)."""
        return _create_confirmation(
            _create_task(title, summary, category, effort, source))

    @server.tool()
    def get_task(ref: str) -> str:
        """Full detail (status, category, effort, source, activity log) for a
        task by its number or id."""
        detail = _get_task(ref)
        return detail if detail is not None else "No task matching %r." % ref

    @server.tool()
    def set_status(ref: str, status: str) -> str:
        """Move a task to open / active / closed."""
        task = _set_status(ref, status)
        if task is None:
            return "No task matching %r." % ref
        return "Task #%s → %s." % (task.get("seq"), _engine().task_status(task))

    @server.tool()
    def add_note(ref: str, text: str) -> str:
        """Append a timestamped note to a task's activity log."""
        task = _add_note(ref, text)
        if task is None:
            return "No task matching %r." % ref
        return "Noted on task #%s." % task.get("seq")

    @server.prompt()
    def todo() -> str:
        """The current task board, rendered as Markdown (the Desktop /todo)."""
        return _list_tasks("all-open")

    @server.resource("task://{seq}")
    def task_resource(seq: str) -> str:
        """A single task's full detail — attach one to a Desktop conversation."""
        detail = _get_task(seq)
        return detail if detail is not None else "No task #%s." % seq

    server.run()


if __name__ == "__main__":
    main()
