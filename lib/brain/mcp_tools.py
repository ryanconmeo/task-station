"""brain.mcp_tools — the brain plane's MCP tool layer (Claude's query interface
to the private brain vault).

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 5a) from the brain source tree's
``scripts/mcp_server.py`` @ 0.14.0 (module rename ``mcp_server`` ->
``brain.mcp_tools``, per the plan's port map). The five tool NAMES, their
JSON-Schema input contracts and their result strings are unchanged — Phase 5
MOUNTED them, unrenamed, on the board's MCP bridge (``lib/mcp_server.py``'s
``_brain()`` / ``_brain_tools_call()``: it borrows ``TOOLS`` and ``HANDLERS``
directly, so this module's own ``handle``/``serve`` stay the standalone path).
Two tool DESCRIPTIONS lost an org word (the scrub gate) and ``brain_save``'s
gained a sentence about the slug's domain gate in Phase 5 (the shipped behaviour
was refusing writes the description never mentioned); no wire key, enum value or
default has moved.

What else changed, and only this:

  * **Transport is** :mod:`core.jsonrpc`. Phase 1 extracted the stdio JSON-RPC
    loop out of the board's server for exactly this adoption, and the source kept
    its transport signatures (``handle``-shaped dispatch, one JSON object per
    line) so the swap is mechanical. The hand-rolled ``respond()`` writer is
    gone; :func:`handle` now RETURNS the response dict (or ``None`` where the
    protocol says answer nothing) and the transport writes it.
  * ``brain`` -> the sibling :mod:`brain.search`; ``brain._CFG`` ->
    ``search._CFG``; ``brain.pb_config.require_valid()`` ->
    ``search.config.require_valid()`` (``search.config`` IS :mod:`brain.config` —
    the same module object, reached through the module that already resolved it).
  * ``note_io`` -> the sibling :mod:`brain.notes`.
  * :func:`_server_version` reads ``$CLAUDE_PLUGIN_ROOT/.claude-plugin/plugin.json``
    rather than walking ``__file__`` up to a repo root — the brain plane keeps a
    fixed, tiny budget of ``__file__`` anchors (``naming`` and ``init_home``,
    each pointing at a data file that ships beside it; ``orgpull``'s went away in
    Phase 5 when its spawn stopped needing a path) and this is not one of them.
    Same fallback contract: ``"0.0.0"``, logged.

THE IMPORT-TIME CONFIG LOAD IS LOAD-BEARING, and deliberately kept. Importing
:mod:`brain.search` resolves the config once, at import, and every tool then
reads the same ``search._CFG``. An MCP server is long-lived, so a config edit
made after the server starts is NOT picked up until it restarts — that is source
behaviour and the reason it is written down here rather than "fixed" in a port.

THE ENTRY POINT IS ``-m``: ``python3 -m brain.mcp_tools`` with ``lib/`` on
``PYTHONPATH`` (a package module with relative imports cannot be run by path), so
the ``__main__`` guard below is LIVE — ``tests/brain/test_mcp.py`` drives the real
process through it. Phase 5 owns whatever wrapper ships.

Layer rule: brain may import core and its own siblings, never board. Stdlib +
``core.jsonrpc`` + siblings only.
"""
import json
import os
import shlex
import sys
from pathlib import Path

from core import jsonrpc

from . import search
from . import notes
from . import errorlog
from . import peers

# protocolVersion is negotiated (see _negotiate_protocol); serverInfo.version is
# read from the plugin manifest at startup, never hard-coded. Newest first — the
# fallback when a client asks for something we do not support is SUPPORTED[0].
SUPPORTED_PROTOCOLS = ["2025-06-18", "2025-03-26", "2024-11-05"]

TOOLS = [
    {
        "name": "brain_search",
        "description": "Ranked zero-token search across the user's private brain vault (notes, hubs, reports, plans, raw, agent memory, org-brain clone if linked). Returns paths + descriptions; read the top hits for detail.",
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string", "description": "search terms (space-separated)"},
            "episodic": {"type": "boolean", "description": "include task-station episodic mirror"},
            "peers": {"type": "boolean", "description": "also search cloned teammates' shared brains (read-only; default false)"},
            "limit": {"type": "integer", "default": 12}},
            "required": ["query"]},
    },
    {
        "name": "brain_status",
        "description": "Vault health at a glance: configured vault path, per-directory note counts, org brain link state, recent LOG lines.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "brain_save",
        "description": "Save an atomic fact into the vault (the private brain). A new slug is created with full frontmatter; an existing slug is UPDATED by appending a dated bullet under ## Updates and merging frontmatter (its body is preserved, never clobbered). SLUG SHAPE IS ENFORCED: `<domain>-<subject>`, where the domain is one the naming contract registers (the shipped generic areas — `repo`, `data`, `cloud`, `ai`, `finance`, `people`, `task-station`, `brain-station`, … — plus any the org brain adds); an UNREGISTERED domain is refused, not guessed, so this call returns an error instead of a note. Before saving, run `python3 -m brain.search find-target \"<the fact's title>\"` to see whether an existing note should absorb the fact. Two sanctioned triggers: (1) explicit brain-directed capture ('save this to the brain/private brain'), and (2) the vault's autonomous hub write-back policy — model-initiated whenever a durable fact is learned or corrected, no user ask needed. Only exclusion: a bare 'remember this' with no brain/vault mention routes to Claude's own memory, not here.",
        "inputSchema": {"type": "object", "properties": {
            "slug": {"type": "string", "description": "kebab-case note name"},
            "description": {"type": "string", "description": "one-line summary"},
            "body": {"type": "string", "description": "the fact: absolute dates, [[wikilinks]], no secrets"},
            "type": {"type": "string", "enum": ["how-to", "gotcha", "state", "architecture", "reference", "decision"], "default": "reference"},
            "scope": {"type": "string", "enum": ["personal", "team", "private"], "default": "personal", "description": "personal (default; publishes to your shared brain) | team (org brain promotion candidate) | private (opt out of publishing)"},
            "mode": {"type": "string", "enum": ["create", "append", "merge", "replace"], "description": "Write mode. Omit to auto-pick (create if new, append if it exists). 'replace' is DESTRUCTIVE — it overwrites the whole body; use only when the caller truly means to discard the existing note."}},
            "required": ["slug", "description", "body"]},
    },
    {
        "name": "brain_log",
        "description": "Append one line to the vault's LOG.md chronicle.",
        "inputSchema": {"type": "object", "properties": {
            "op": {"type": "string"}, "message": {"type": "string"}},
            "required": ["op", "message"]},
    },
    {
        "name": "brain_recent_tasks",
        "description": "Recent task-station activity (read-only episodic layer). Degrades gracefully when task-station is not installed.",
        "inputSchema": {"type": "object", "properties": {
            "days": {"type": "integer", "default": 14}}},
    },
]


def t_search(args):
    query = args.get("query") or ""
    try:
        terms = shlex.split(query)  # respects "quoted phrases" as single terms
    except ValueError:
        terms = query.split()
    episodic = args.get("episodic", False)
    include_peers = args.get("peers", False)
    hits = search.search_hits(terms, search.default_roots(episodic, include_peers), args.get("limit", 12))
    if not hits:
        return "no hits"
    out = "\n".join(
        f"{s}  {peers.peer_label(f, search._CFG) or f}" + (f"\n      {d}" if d else "")
        for f, s, d in hits)
    hints = search.reference_hints(hits)  # mirror the CLI's K2 reference nudge
    if hints:
        out += "\n" + "\n".join(hints)
    return out


def t_status(args):
    return search.status_text()


def t_save(args):
    cfg = search.config.require_valid()  # never write to a silently-defaulted vault
    vault = cfg["vault"]
    slug, desc, body = args["slug"], args["description"], args["body"]
    # resolve_note_path validates the slug AND blocks traversal before any write —
    # this is the single write path (notes), so mcp cannot escape the vault.
    exists = notes.resolve_note_path(vault, slug, "notes").exists()
    mode = args.get("mode") or ("append" if exists else "create")
    path = notes.write_note(
        vault, slug, mode=mode, body=body, description=desc,
        type=args.get("type", "reference"), scope=args.get("scope", "personal"),
        source="mcp", folder="notes", actor="agent",
    )
    action = {"create": "created", "append": "updated (appended under ## Updates)",
              "merge": "updated (section merged)", "replace": "REPLACED (destructive)"}.get(mode, mode)
    search.append_log("note", f"{slug} ({action} via brain MCP)")
    return (f"{action}: {path}\nREMINDER for the caller: add/update the INDEX.md line and a hub link "
            f"(no orphans). The note was committed to the vault git repo automatically. "
            f"scope: team notes are org brain promotion candidates.")


def t_log(args):
    search.append_log(args["op"], args["message"])
    return "logged"


def t_recent(args):
    rows = search.recent_tasks(args.get("days", 14))
    if rows is None:
        return "task-station not installed — episodic layer unavailable (this is fine)"
    if not rows:
        return "no recent tasks"
    return "\n".join(
        f"#{r.get('seq')} [{r.get('status')}] {r.get('title')}"
        + (f"\n    {str(r['summary'])[:300]}" if r.get("summary") else "")
        for r in rows)


HANDLERS = {"brain_search": t_search, "brain_status": t_status, "brain_save": t_save,
            "brain_log": t_log, "brain_recent_tasks": t_recent}


def _server_version():
    """serverInfo.version = the plugin manifest version, resolved through
    ``$CLAUDE_PLUGIN_ROOT`` (the one env var the harness always sets for a
    plugin). Falls back to '0.0.0' (logged) when the manifest is unset or
    unreadable. The source anchored this to ``__file__`` walked up one dir; the
    package layout has no such fixed distance to the plugin root, and adding
    another ``__file__`` anchor to the brain plane to guess one would be worse
    than reading the variable that already holds the answer."""
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if not root:
        return "0.0.0"
    manifest = Path(root) / ".claude-plugin" / "plugin.json"
    try:
        return json.loads(manifest.read_text()).get("version") or "0.0.0"
    except Exception as e:
        errorlog.record("mcp:server-version", e)
        return "0.0.0"


def _negotiate_protocol(client_proto):
    """Never echo the client's protocolVersion blindly. If the client asks for
    one we support, use it; otherwise answer with our newest supported version
    and let the client decide whether it can proceed."""
    if client_proto in SUPPORTED_PROTOCOLS:
        return client_proto
    return SUPPORTED_PROTOCOLS[0]


def handle(msg):
    """Process one parsed JSON-RPC message; return a response dict, or ``None``
    where the protocol says answer nothing (``notifications/initialized``, and an
    unknown method arriving without an id).

    The dispatch chain, the codes and the shape of every result are the source's,
    one for one — only the writing moved out to :mod:`core.jsonrpc`.
    """
    method, rid = msg.get("method"), msg.get("id")
    if method == "initialize":
        client_proto = (msg.get("params") or {}).get("protocolVersion")
        return jsonrpc.result(rid, {
            "protocolVersion": _negotiate_protocol(client_proto),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "brain", "version": _server_version()},
        })
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return jsonrpc.result(rid, {"tools": TOOLS})
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        fn = HANDLERS.get(name)
        if not fn:
            return jsonrpc.error(rid, -32602, f"unknown tool {name}")
        try:
            text = fn(args)
        except Exception as e:  # tool errors are results, not protocol errors
            errorlog.record(f"mcp:tool:{name}", e)
            return jsonrpc.result(rid, {"content": [{"type": "text", "text": f"error: {e}"}],
                                        "isError": True})
        return jsonrpc.result(rid, {"content": [{"type": "text", "text": text}]})
    if method == "ping":
        return jsonrpc.result(rid, {})
    if rid is not None:
        return jsonrpc.error(rid, -32601, f"method not found: {method}")
    return None


def serve(stdin=None, stdout=None):
    """The stdio transport: newline-delimited JSON-RPC in, one-object-per-line
    responses out. Shares the board bridge's loop verbatim (core.jsonrpc)."""
    return jsonrpc.serve(handle, stdin, stdout)


def main():
    serve(sys.stdin, sys.stdout)


if __name__ == "__main__":
    main()
