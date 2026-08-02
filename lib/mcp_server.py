#!/usr/bin/env python3
# mcp_server.py
"""Task Station's Desktop bridge — an MCP server over the SHARED task store.

Claude Desktop (or any MCP client) can create / read / update tasks in the very
same local store the Claude Code CLI uses: one `tasks.db`, two front doors. This
is the Desktop ↔ Code bridge.

Design — DEPENDENCY-FREE, stdlib only:

  * The tool LOGIC is plain-stdlib functions (`_list_tasks`, `_create_task`,
    `_get_task`, `_set_status`, `_add_note`) that drive the existing engine
    (`task-station.py`, which itself sits on `paths.py` + `store.py`). They
    return plain strings / dicts and need NOTHING beyond the stdlib.

  * The MCP protocol itself is hand-rolled: a minimal stdio JSON-RPC 2.0 server
    (`serve`/`handle`/`dispatch`) built on `json` + `sys` ONLY — no `mcp` SDK,
    no `pip install`, runs on the system `python3` (3.9+). Newline-delimited
    JSON on stdin/stdout; stderr for logs.

The engine reads `TASK_STATION_HOME` / `CLAUDE_CONFIG_DIR` exactly as the CLI
does, so the bridge writes where the CLI reads with zero extra config. WAL is
already on, so concurrent Desktop + CLI access is safe.

Episodic-memory query surface (WS8): alongside the one-way `task-station export`
snapshot, these read tools are the LIVE query surface an external second brain (or
any MCP client) uses to pull task-station's episodic layer on demand —
`search_tasks` (ranked full-text find), `get_task` (full detail for one task), and
`get_prompts` (a task's captured prompt trail, when prompt capture is enabled).
They complement the pull-based markdown export: export for a durable snapshot,
these for interactive, up-to-the-moment queries over the same shared store.
"""
import importlib.util
import json
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

# Claude Desktop Chat tends to paraphrase a Markdown board into prose unless it
# is told to render it verbatim (the CLI `/todo` skill says "print verbatim" for
# the same reason). So the bridge prepends this one-line instruction to the
# board it hands Chat — for both the `list_tasks` tool result and the `todo`
# prompt — while the board BODY stays byte-equal to the CLI `render --format md`.
VERBATIM_INSTRUCTION = (
    "Display this task board to the user EXACTLY as written below — it is "
    "preformatted Markdown; render the tables verbatim, do not summarize, "
    "reword, or re-rank."
)


def _board_with_instruction(status="all-open"):
    """The verbatim-render instruction line, a blank line, then the board —
    byte-equal to the CLI `render --format md` (only the prefix is added). This
    is what Chat sees from the `list_tasks` tool and the `todo` prompt."""
    return VERBATIM_INSTRUCTION + "\n\n" + _list_tasks(status)


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


def _search_tasks(query, status="all"):
    """Ranked cross-task search as Markdown — the Desktop analog of `task-station
    search` (tier 1). Reuses the engine's ranking + tier-1 renderer so the bridge
    never forks search logic. `status`: all (default) | open | closed."""
    ts = _engine()
    want = status if status in ("open", "closed", "all") else "all"
    q = (query or "").strip()
    return ts._format_search(q, ts._search_core(q, want), want)


def _create_task(title, summary="", category=None, effort=None, source=None):
    """Create an OPEN (○) task in the shared store and return the stored dict.

    `category`/`effort` honor the same taxonomy/sizes as the CLI (unknown values
    are dropped by the engine, never guessed). `source` records the originating
    Desktop conversation ref/URL on the task so `get_task` can surface it — the
    Desktop ↔ Code provenance link.
    """
    ts = _engine()
    task = ts.new_task(title or "", summary or "",
                       color=category, effort=effort, status=ts.STATUS_OPEN)
    if source:
        task["source"] = str(source)
    ts.create_with_seq(task)         # atomically mint the stable number + persist
    ts.touch(task, note="created (Desktop bridge)")
    ts.save_task(task)
    # Grow the board if this lands on a not-yet-enabled slot (auto_categories on).
    # Persist silently — this channel is JSON-RPC, so no stdout notice here.
    if task.get("color") and ts.cats and hasattr(ts.cats, "auto_enable"):
        ts.cats.auto_enable(task.get("color"))
    result = ts.load_task(task["id"])
    ts._stream_emit("task.created", result, ts._stream_created_data(result), None)
    return result


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
           "- **Status:** %s" % head,
           "- **UUID:** %s" % task.get("uuid", task.get("id", ""))]
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
    out.append("- **Created:** %s" % ts._local_iso(task.get("created_at", "")))
    out.append("- **Updated:** %s" % ts.rel_time(task.get("updated_ts")))
    if task.get("closed_ts"):
        out.append("- **Closed:** %s" % ts.rel_time(task.get("closed_ts")))
    stats = ts.task_stats_line(task)
    if stats:
        out.append("- **Stats:** %s" % stats)
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


def _get_prompts(ref, include_compact=False):
    """The task's prompt trail as Markdown — the Desktop analog of `task-station
    prompts --md`: the chronological, session-attributed list of the exact user
    prompts (and slash commands) that drove the task. Reuses the engine's
    usage.task_prompts + _format_prompts_md so the bridge never forks the view.
    `include_compact` adds compaction-summary rows (omitted by default). Returns
    None when no task matches `ref`."""
    ts = _engine()
    task = _resolve(ref)
    if not task:
        return None
    prompts = ts._usage_engine().task_prompts(
        ts._backend(), task, include_compact=include_compact)
    return ts._format_prompts_md(task, prompts, include_compact)


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
            def _close(t):
                t["status"] = ts.STATUS_CLOSED
                ts.stamp_closed(t)          # real moment it entered closed
                ts.touch(t, note="closed (Desktop bridge)")
            updated = ts.mutate(task["id"], _close) or task
            # Link-table detachment is a side effect, so it runs AFTER the atomic
            # task write (never inside the retryable mutator).
            for sess in list(updated.get("sessions", [])):
                if ts.get_link(sess) == task["id"]:
                    ts.clear_link(sess)
                    ts.clear_count(sess)
                    ts.clear_edit_markers(sess)
    else:
        # open ⇄ active via the engine's idempotent transition (logs the change).
        def _move(t):
            ts.set_status(t, status)
            t["updated_ts"] = ts._now()
        ts.mutate(task["id"], _move)
    updated = ts.load_task(task["id"])
    if updated is not None:
        ts._stream_emit("task.status", updated,
                        {"status": updated.get("status"),
                         "closed_ts": updated.get("closed_ts")}, None)
    return updated


def _list_sessions(task=None):
    """Every ACTUALLY-running Claude Code session as Markdown — the Desktop analog
    of `task-station sessions`. Reuses lib/live_sessions.running() (process-state,
    pid-liveness-filtered) so the bridge never forks the viewer. `task` (seq/id)
    filters to one task's live sessions. Returns a friendly line when nothing runs."""
    import live_sessions
    ts = _engine()
    rows = live_sessions.running()
    if task:
        t = _resolve(task)
        rows = [r for r in rows if t and r.get("task_seq") == t.get("seq")]
    if not rows:
        return "_No live Claude sessions%s._" % (" for that task" if task else "")
    out = ["# Live Claude sessions", ""]
    for r in rows:
        seq = r.get("task_seq")
        task_col = ("task #%s" % seq) if seq is not None else "no task"
        title = (" — %s" % r["task_title"]) if r.get("task_title") else ""
        out.append("- ● **%s** · %s%s · %s · %s · %s"
                   % (r.get("pid"), task_col, title, r.get("role") or "—",
                      r.get("status") or "—", ts.rel_time(r.get("updated_ts"))))
        if r.get("resume_command"):
            out.append("  - `%s`" % r["resume_command"])
    return "\n".join(out)


def _add_note(ref, text):
    """Append a timestamped note to the task's activity log. Returns the updated
    task dict, or None if no task matches `ref`."""
    ts = _engine()
    task = _resolve(ref)
    if not task:
        return None

    def _apply(t):
        ts.add_log(t, text)
        t["updated_ts"] = ts._now()
    updated = ts.mutate(task["id"], _apply)
    if updated is not None:
        ts._stream_emit("task.event", updated,
                        {"kind": "note", "text": (text or "")[:ts.EVENT_TEXT_MAX]}, None)
    return updated


def _send_memo(ref, text, sender="desktop"):
    """Post a memo (a fact/decision handed to a task's working session(s)) onto the
    task named by `ref`. `sender` is a FREE-TEXT signature — Desktop/MCP callers have
    no Claude Code session id, so it fills the sid slot (default "desktop"). Returns
    the confirmation line, or None when no task matches `ref`."""
    ts = _engine()
    task = _resolve(ref)
    if not task:
        return None
    memo = ts.memo_send(task, text or "", from_sid=(sender or "desktop"))
    task["updated_ts"] = ts._now()
    ts.save_task(task)
    return "memo %s → task #%s (%s)" % (
        memo["id"][:8], task.get("seq", task["id"][:8]), task.get("title", ""))


def _list_memos(ref):
    """The memo roster for one task as text (the engine's `memo show` list). Returns
    None when no task matches `ref`."""
    ts = _engine()
    task = _resolve(ref)
    if not task:
        return None
    return ts._format_memo_list(task)


def _ack_memo(ref, memo_id, by):
    """Acknowledge a memo on the shared ledger, signed with the FREE-TEXT `by` (a
    Desktop signature — required, since Desktop has no session id). Idempotent. Returns
    the result line (or an id-resolution error line), or None when `ref` matches no task."""
    ts = _engine()
    task = _resolve(ref)
    if not task:
        return None
    memo, err = ts._memo_by_prefix(task, memo_id)
    if err:
        return err
    result = ts.memo_ack(task, memo, by)
    task["updated_ts"] = ts._now()
    ts.save_task(task)
    verb = "already acked" if result == "already" else "acked"
    return "memo %s %s by %s." % (memo["id"][:8], verb, (by or "?")[:8])


# ---------------------------------------------- tool / prompt / resource wiring ---
#
# The five logic fns above are the WHOLE behaviour. Everything below is a
# hand-rolled MCP stdio JSON-RPC 2.0 server (stdlib `json` + `sys` ONLY — no
# `mcp` SDK, runs on the system `python3` 3.9+) that advertises them and
# dispatches calls. The tool handlers return the same plain strings the FastMCP
# wrappers used to; only the transport changed.

PROTOCOL_VERSION = "2024-11-05"


# Desktop runs the plugin's COMMANDS but not its HOOKS, and it silently drops the
# MCP `initialize` `instructions` field — so there's no transport-level lever to
# make Desktop auto-track. Tracking in Desktop is on-demand (the `/todo` command
# or "track this" invoking the conversational tools); see the README's Desktop
# matrix. (The one proactive lever is the user's own Desktop Custom Instructions.)


def _server_version():
    """The plugin's version string for `serverInfo` (best-effort; never raises)."""
    root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(_LIB)
    try:
        import json as _json
        with open(os.path.join(root, ".claude-plugin", "plugin.json")) as f:
            return _json.load(f).get("version", "0")
    except Exception:
        return "0"


# Each tool: its JSON-Schema input contract + a handler that returns the text the
# client sees. Handlers reuse the stdlib logic fns verbatim — no forked logic.

def _tool_list_tasks(args):
    # Prepend the verbatim-render instruction so Chat shows the table, not prose.
    return _board_with_instruction(args.get("status", "all-open"))


def _tool_create_task(args):
    return _create_confirmation(_create_task(
        args.get("title", ""), args.get("summary", ""),
        args.get("category"), args.get("effort"), args.get("source")))


def _tool_search_tasks(args):
    return _search_tasks(args.get("query", ""), args.get("status", "all"))


def _tool_get_task(args):
    ref = args.get("ref")
    detail = _get_task(ref)
    return detail if detail is not None else "No task matching %r." % ref


def _tool_get_prompts(args):
    ref = args.get("ref")
    md = _get_prompts(ref, bool(args.get("include_compact")))
    return md if md is not None else "No task matching %r." % ref


def _tool_set_status(args):
    ref = args.get("ref")
    task = _set_status(ref, args.get("status"))
    if task is None:
        return "No task matching %r." % ref
    return "Task #%s → %s." % (task.get("seq"), _engine().task_status(task))


def _tool_add_note(args):
    ref = args.get("ref")
    task = _add_note(ref, args.get("text"))
    if task is None:
        return "No task matching %r." % ref
    return "Noted on task #%s." % task.get("seq")


def _tool_list_sessions(args):
    return _list_sessions(args.get("task"))


def _tool_send_memo(args):
    ref = args.get("ref")
    line = _send_memo(ref, args.get("text", ""), args.get("from") or "desktop")
    return line if line is not None else "No task matching %r." % ref


def _tool_list_memos(args):
    ref = args.get("ref")
    md = _list_memos(ref)
    return md if md is not None else "No task matching %r." % ref


def _tool_ack_memo(args):
    ref = args.get("ref")
    line = _ack_memo(ref, args.get("memo_id"), args.get("by"))
    return line if line is not None else "No task matching %r." % ref


TOOLS = [
    {
        "name": "list_tasks",
        "description": ("Show the user's task board as Markdown (the Desktop "
                        "analog of /todo). `status`: all-open (default, "
                        "open+active) | open | active | closed | all."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "default": "all-open",
                           "description": "all-open | open | active | closed | all"},
            },
        },
        "handler": _tool_list_tasks,
    },
    {
        "name": "create_task",
        "description": ("Create / track a new open(○) task. `category` = a "
                        "category key/emoji/[TAG]; `effort` = xs/s/m/l/xl; "
                        "`source` = the originating Desktop conversation ref/URL "
                        "(stored on the task, surfaced in get_task)."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "category": {"type": "string"},
                "effort": {"type": "string"},
                "source": {"type": "string"},
            },
            "required": ["title"],
        },
        "handler": _tool_create_task,
    },
    {
        "name": "search_tasks",
        "description": ("Ranked full-text search across all tasks (the Desktop "
                        "analog of `task-station search`). Returns a tier-1 hit "
                        "list — #seq, status dot, title, and a match-context "
                        "snippet each. `status`: all (default) | open | closed."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "terms to search task text for"},
                "status": {"type": "string", "default": "all",
                           "description": "all | open | closed"},
            },
            "required": ["query"],
        },
        "handler": _tool_search_tasks,
    },
    {
        "name": "get_task",
        "description": ("Show full detail (status, category, effort, source, "
                        "activity log) for one task by its number or id."),
        "inputSchema": {
            "type": "object",
            "properties": {"ref": {"type": "string"}},
            "required": ["ref"],
        },
        "handler": _tool_get_task,
    },
    {
        "name": "get_prompts",
        "description": ("Show the exact user prompts (and slash commands) that drove "
                        "a task — a chronological, timestamped, session-attributed "
                        "trail (hub + each delegated worker), oldest first, as "
                        "Markdown. The shareable 'here's exactly what I prompted' "
                        "artifact. `include_compact` adds compaction-summary rows "
                        "(omitted by default)."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string",
                        "description": "task seq/id whose prompt trail to show"},
                "include_compact": {"type": "boolean", "default": False,
                                    "description": "include compaction-summary rows"},
            },
            "required": ["ref"],
        },
        "handler": _tool_get_prompts,
    },
    {
        "name": "set_status",
        "description": "Move a task to open / active / closed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string"},
                "status": {"type": "string", "enum": ["open", "active", "closed"]},
            },
            "required": ["ref", "status"],
        },
        "handler": _tool_set_status,
    },
    {
        "name": "add_note",
        "description": "Append a timestamped note to a task's activity log.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["ref", "text"],
        },
        "handler": _tool_add_note,
    },
    {
        "name": "list_sessions",
        "description": ("Show every ACTUALLY-running Claude Code session (hub + "
                        "delegated workers) — its task, busy/idle state, and a "
                        "one-command resume. Dead/crashed sessions never appear. "
                        "`task` (optional seq/id) filters to one task's live "
                        "sessions."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string",
                         "description": "optional task seq/id to filter by"},
            },
        },
        "handler": _tool_list_sessions,
    },
    {
        "name": "send_memo",
        "description": ("Hand a fact/decision to a task's working session(s) — post a "
                        "memo onto the task (attached or not). Every Claude Code session "
                        "on that task passively sees it awaiting ack on its next turn, and "
                        "acks on a shared ledger so no two sessions double-implement. "
                        "`from` is a free-text signature for who's sending (default "
                        "'desktop')."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "target task seq/id"},
                "text": {"type": "string", "description": "the memo body"},
                "from": {"type": "string", "default": "desktop",
                         "description": "free-text sender signature"},
            },
            "required": ["ref", "text"],
        },
        "handler": _tool_send_memo,
    },
    {
        "name": "list_memos",
        "description": ("List the memos on one task — each with its id, sender, preview, "
                        "and the shared ack ledger (who has acknowledged it)."),
        "inputSchema": {
            "type": "object",
            "properties": {"ref": {"type": "string", "description": "task seq/id"}},
            "required": ["ref"],
        },
        "handler": _tool_list_memos,
    },
    {
        "name": "ack_memo",
        "description": ("Acknowledge a memo on a task's shared ledger, signed with `by` "
                        "(a free-text signature — Desktop has no session id). Idempotent: "
                        "a repeat ack reports it was already acknowledged."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "task seq/id"},
                "memo_id": {"type": "string", "description": "memo id or id-prefix"},
                "by": {"type": "string", "description": "free-text signer of the ack"},
            },
            "required": ["ref", "memo_id", "by"],
        },
        "handler": _tool_ack_memo,
    },
]
_TOOLS_BY_NAME = {t["name"]: t for t in TOOLS}


# ------------------------------------------------------------ JSON-RPC plumbing ---

class _RpcError(Exception):
    """A JSON-RPC error to surface as an `error` member (code + message)."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _result(mid, payload):
    return {"jsonrpc": "2.0", "id": mid, "result": payload}


def _error(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _text_content(text):
    """An MCP `content` list holding one text block."""
    return [{"type": "text", "text": text}]


def _handle_tools_call(params):
    name = params.get("name")
    args = params.get("arguments") or {}
    tool = _TOOLS_BY_NAME.get(name)
    if tool is None:
        # A bad tool name is a tool-execution error (reported in the result with
        # isError) rather than a transport-level JSON-RPC error.
        return {"content": _text_content("Unknown tool: %s" % name), "isError": True}
    try:
        text = tool["handler"](args)
    except Exception as e:                       # bad args, ValueError, etc.
        return {"content": _text_content("%s: %s" % (type(e).__name__, e)),
                "isError": True}
    return {"content": _text_content(text)}


# The `todo` prompt's human-facing labels for Desktop's prompt/attachment picker.
TODO_PROMPT_TITLE = "Task Station: todo"
TODO_PROMPT_DESCRIPTION = "Show your task-station board (open · active · closed)"


def _handle_prompts_get(params):
    name = params.get("name")
    if name != "todo":
        raise _RpcError(-32602, "Unknown prompt: %s" % name)
    return {
        "description": TODO_PROMPT_DESCRIPTION,
        "messages": [{"role": "user",
                      "content": {"type": "text",
                                  "text": _board_with_instruction("all-open")}}],
    }


def _handle_resources_read(params):
    uri = params.get("uri", "")
    if not uri.startswith("task://"):
        raise _RpcError(-32602, "Unknown resource: %s" % uri)
    seq = uri[len("task://"):]
    detail = _get_task(seq)
    text = detail if detail is not None else "No task #%s." % seq
    return {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": text}]}


def _resource_list():
    ts = _engine()
    out = []
    for task in ts.all_tasks():
        seq = task.get("seq")
        if seq is None:
            continue
        out.append({
            "uri": "task://%s" % seq,
            "name": "#%s %s" % (seq, task.get("title", "Untitled")),
            "description": task.get("summary", "") or "",
            "mimeType": "text/markdown",
        })
    return out


def dispatch(method, params):
    """Map an MCP method to its result payload. Raises `_RpcError` for protocol
    errors (e.g. unknown method → -32601)."""
    if method == "initialize":
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "serverInfo": {"name": "task-station", "version": _server_version()},
            "capabilities": {"tools": {}, "prompts": {}, "resources": {}},
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [{k: t[k] for k in ("name", "description", "inputSchema")}
                          for t in TOOLS]}
    if method == "tools/call":
        return _handle_tools_call(params)
    if method == "prompts/list":
        # title + description so the prompt is recognizable in Desktop's prompt /
        # attachment picker (the MCP spec carries `description`; newer clients
        # also surface an optional `title`).
        return {"prompts": [{"name": "todo",
                             "title": TODO_PROMPT_TITLE,
                             "description": TODO_PROMPT_DESCRIPTION}]}
    if method == "prompts/get":
        return _handle_prompts_get(params)
    if method == "resources/list":
        return {"resources": _resource_list()}
    if method == "resources/read":
        return _handle_resources_read(params)
    raise _RpcError(-32601, "Method not found: %s" % method)


def handle(msg):
    """Process one parsed JSON-RPC message; return a response dict, or None for
    notifications (no `id`) which the protocol says get no reply."""
    mid = msg.get("id")
    method = msg.get("method")
    is_notification = "id" not in msg
    if method is None:
        return None if is_notification else _error(mid, -32600, "Invalid Request: no method")
    # Notifications (incl. notifications/initialized) are fire-and-forget.
    if is_notification:
        return None
    params = msg.get("params") or {}
    try:
        return _result(mid, dispatch(method, params))
    except _RpcError as e:
        return _error(mid, e.code, e.message)
    except Exception as e:                       # never crash the loop
        sys.stderr.write("task-station MCP: error handling %r: %s\n" % (method, e))
        return _error(mid, -32603, "Internal error: %s" % e)


def serve(stdin=None, stdout=None):
    """The stdio transport: read newline-delimited JSON-RPC from `stdin`, write
    one-object-per-line responses to `stdout`, flushing after each. A malformed
    line is answered with a parse error but never crashes the loop."""
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception as e:
            _write(stdout, _error(None, -32700, "Parse error: %s" % e))
            continue
        try:
            resp = handle(msg)
        except Exception as e:                   # belt-and-suspenders
            sys.stderr.write("task-station MCP: unhandled: %s\n" % e)
            resp = _error(msg.get("id") if isinstance(msg, dict) else None,
                          -32603, "Internal error: %s" % e)
        if resp is not None:
            _write(stdout, resp)


def _write(stdout, obj):
    """One JSON object per line, no embedded newlines, flushed immediately."""
    stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    stdout.flush()


def main():
    """Run the dependency-free MCP server over stdio (system `python3`, no SDK)."""
    serve(sys.stdin, sys.stdout)


if __name__ == "__main__":
    main()
