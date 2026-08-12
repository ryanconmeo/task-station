"""core.jsonrpc — the stdio JSON-RPC 2.0 transport shared by both planes.

Extracted from lib/mcp_server.py in 3.0.0 Phase 1 — the ONE genuine board/brain
merge in this phase (the brain adopts it in Phase 4). Newline-delimited JSON on
stdin/stdout, one object per line, flushed immediately; stderr for logs. Pure
stdlib — `json` + `sys` only, no `mcp` SDK.
"""
import json
import sys


def result(mid, payload):
    return {"jsonrpc": "2.0", "id": mid, "result": payload}


def error(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _write(stdout, obj):
    """One JSON object per line, no embedded newlines, flushed immediately."""
    stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    stdout.flush()


def serve(handle, stdin=None, stdout=None):
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
            _write(stdout, error(None, -32700, "Parse error: %s" % e))
            continue
        try:
            resp = handle(msg)
        except Exception as e:                   # belt-and-suspenders
            sys.stderr.write("task-station MCP: unhandled: %s\n" % e)
            resp = error(msg.get("id") if isinstance(msg, dict) else None,
                        -32603, "Internal error: %s" % e)
        if resp is not None:
            _write(stdout, resp)
