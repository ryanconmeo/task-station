#!/usr/bin/env python3
# hookmux.py
"""One hook command, several hook programs — the two planes' hook mux.

Claude Code runs the commands a plugin's ``hooks/hooks.json`` registers for an
event. Three events are now claimed by BOTH planes: the board's shell hooks
(``hooks/on_*.sh``) and the brain plane's python hooks
(``python3 -m brain.hooks.*``). Rather than registering two entries per event and
hoping the harness merges their output, ``hooks.json`` registers ONE command —
this mux — and the mux runs the children itself:

    python3 "${CLAUDE_PLUGIN_ROOT}/lib/hookmux.py" <session-start|user-prompt|stop>

WHAT IT GUARANTEES

  * **One stdin, fanned out.** The hook payload is read ONCE and the same bytes
    are handed to every child, so each child parses the event for itself exactly
    as it does when it is registered alone.
  * **One output document.** Children speak the harness's hook JSON; the mux
    merges their documents into at most one (see :func:`_absorb`) and prints
    nothing at all when there is nothing to say — the board scripts' own
    convention.
  * **Order is board first, brain after** (:data:`CHILDREN`), and
    ``additionalContext`` is concatenated in that order, so a session opens with
    the board's orientation above the brain's.
  * **A child can never break the session.** A child that fails to start, exits
    non-zero, or prints garbage leaves a one-line breadcrumb on stderr and the
    others still run. The mux itself ALWAYS exits 0.
  * **Child stderr is not swallowed.** Children inherit this process's stderr, so
    the board's hook-health logging keeps seeing everything it saw before.
  * **No mux-imposed timeouts.** The harness's own hook timeout governs; every
    brain child is bounded by design (3.0.0 decision).

WHERE ``lib/`` COMES FROM. The brain plane is a package under ``lib/``, so its
modules run as ``python3 -m brain.hooks.<mod>`` and need ``lib/`` importable. The
mux puts it there: every child's ``PYTHONPATH`` is this file's own directory,
PREPENDED to whatever the session already had. That is the one packaging answer —
a brain child, and anything it spawns in turn, just inherits ``os.environ``.

This is a lib/ ROOT script (same class as ``mcp_server.py`` / ``hookjson.py``): it
is invoked by literal path and resolves its own directory the way those do. It
imports NO board and NO brain module — it only spawns them.

Stdlib only, python3.9+.
"""
import json
import os
import subprocess
import sys

_LIB = os.path.dirname(os.path.abspath(__file__))

# Child kinds. SCRIPT is a board hook, run the way hooks.json ran it before this
# mux existed (`bash <plugin-root>/hooks/on_*.sh`); MODULE is a brain hook, run
# through the `-m` entry point every brain hook module ships.
SCRIPT, MODULE = "script", "module"

# The event's spelling in the emitted document. Taken from what the children
# already emit (hooks/on_session_start.sh's inner dict, brain.hooks.inject._emit).
EVENT_NAMES = {
    "session-start": "SessionStart",
    "user-prompt": "UserPromptSubmit",
    "stop": "Stop",
}

# Who runs, in order, per event. BOARD FIRST — the board's context is the frame
# the brain's orientation hangs on, and first-writer-wins (below) means the
# board's scalar keys (e.g. sessionTitle) win a collision.
CHILDREN = {
    "session-start": (
        (SCRIPT, "hooks/on_session_start.sh"),
        (MODULE, "brain.hooks.inject", "--session-start"),
        (MODULE, "brain.hooks.gate", "--session-start"),
    ),
    "user-prompt": (
        (SCRIPT, "hooks/on_user_prompt.sh"),
        (MODULE, "brain.hooks.inject", "--prompt"),
    ),
    "stop": (
        (SCRIPT, "hooks/on_stop.sh"),
        (MODULE, "brain.hooks.distill"),
    ),
}


def _plugin_root():
    """The plugin root: ``$CLAUDE_PLUGIN_ROOT`` (which the harness sets for every
    hook it runs), else the parent of this script's own directory — so the mux is
    still runnable by hand from a checkout."""
    return os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(_LIB)


def _label(child):
    """The child's name in a breadcrumb (its script path or its module)."""
    return child[1]


def _argv(child):
    """The command line for one child."""
    if child[0] == SCRIPT:
        return ["bash", os.path.join(_plugin_root(), child[1])]
    return [sys.executable, "-m", child[1], *child[2:]]


def _child_env():
    """The session's environment with ``lib/`` PREPENDED to PYTHONPATH (an
    existing value is preserved after it, never replaced)."""
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = _LIB + (os.pathsep + existing if existing else "")
    return env


def _breadcrumb(label, what):
    """One line on stderr. A hook's stderr is diagnostics, never session output."""
    sys.stderr.write("hookmux: %s: %s\n" % (label, what))


def _read_stdin():
    """The hook payload, once, as bytes (binary: it is fanned out verbatim)."""
    try:
        return sys.stdin.buffer.read()
    except (AttributeError, ValueError, OSError):
        try:
            return (sys.stdin.read() or "").encode("utf-8", "replace")
        except Exception:
            return b""


def _spawn(child, payload, env):
    """Run one child on the shared payload; return its stdout as text.

    Never raises. stderr is INHERITED (the board's hook-health logging must keep
    seeing child failures), and a non-zero exit is a breadcrumb — the child's
    stdout is still used, because a child that printed its document and then fell
    over should not lose the document.
    """
    label = _label(child)
    try:
        proc = subprocess.run(_argv(child), input=payload,
                              stdout=subprocess.PIPE, env=env)
    except Exception as e:                       # missing bash/python, OSError, …
        _breadcrumb(label, "did not run (%s: %s)" % (type(e).__name__, e))
        return ""
    if proc.returncode != 0:
        _breadcrumb(label, "exit %s" % proc.returncode)
    return (proc.stdout or b"").decode("utf-8", "replace")


def _child_items(text):
    """One child's stdout, split into ``("doc", dict)`` / ``("text", str)`` items
    IN OUTPUT ORDER.

    A child may print one JSON document (the common case), several — the board's
    Stop hook prints the stop-gate decision and, separately, the stop-nudge
    context — or plain text (the board's UserPromptSubmit context). Only a JSON
    OBJECT counts as a document; anything else is text, so a stray number or a
    quoted line can never be mistaken for hook JSON.
    """
    if not text.strip():
        return []
    try:
        whole = json.loads(text)
    except ValueError:
        whole = None
    if isinstance(whole, dict):
        return [("doc", whole)]
    items, buf = [], []
    for line in text.splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            if buf:
                items.append(("text", "\n".join(buf)))
                buf = []
            items.append(("doc", obj))
        else:
            buf.append(line)
    if buf:
        items.append(("text", "\n".join(buf)))
    return [(kind, value) for kind, value in items
            if kind == "doc" or value.strip()]


def _absorb(doc, merged, inner, contexts):
    """Fold one child's document into the merged one.

    ``additionalContext`` accumulates (joined with a blank line, in child order);
    every other key is FIRST-WRITER-WINS, so the earlier child — the board — keeps
    its answer and a later child can only add what nobody said yet.
    ``hookEventName`` is the mux's to write, so a child's copy is dropped here.
    """
    for key, value in doc.items():
        if key == "hookSpecificOutput":
            if not isinstance(value, dict):
                continue                         # garbage shape — ignore, not fatal
            for ikey, ivalue in value.items():
                if ikey == "hookEventName":
                    continue
                if ikey == "additionalContext":
                    if isinstance(ivalue, str) and ivalue.strip():
                        contexts.append(ivalue.strip())
                elif ikey not in inner:
                    inner[ikey] = ivalue
        elif key not in merged:
            merged[key] = value


def run(event, payload, children=None, stdout=None):
    """Run `event`'s children on `payload` (bytes) and write at most one document.

    Returns the merged document (or ``None`` when there was nothing to say), which
    is what the tests read; the process contract is what the harness reads.
    """
    stdout = sys.stdout if stdout is None else stdout
    name = EVENT_NAMES[event]
    table = CHILDREN[event] if children is None else children
    env = _child_env()
    merged, inner, contexts = {}, {}, []
    for child in table:
        try:
            for kind, value in _child_items(_spawn(child, payload, env)):
                if kind == "doc":
                    _absorb(value, merged, inner, contexts)
                elif event == "stop":
                    # Stop's output contract is a decision document, not context —
                    # loose text there is diagnostics, so it goes to stderr.
                    _breadcrumb(_label(child), value.strip())
                else:
                    contexts.append(value.strip())
        except Exception as e:                   # a broken child is never fatal
            _breadcrumb(_label(child), "unreadable output (%s: %s)"
                        % (type(e).__name__, e))
    if contexts:
        inner["additionalContext"] = "\n\n".join(contexts)
    if inner:
        hook_output = {"hookEventName": name}    # the event's spelling, first key
        hook_output.update(inner)
        merged["hookSpecificOutput"] = hook_output
    if not merged:
        return None                              # nothing to say: say nothing
    stdout.write(json.dumps(merged) + "\n")
    stdout.flush()
    return merged


def main(argv=None):
    """``hookmux.py <event>``. Always returns 0 — a hook must never break the
    session, so even an unusable invocation is a breadcrumb, not a failure."""
    args = list(sys.argv[1:] if argv is None else argv)
    event = args[0] if args else ""
    if event not in CHILDREN:
        _breadcrumb("hookmux", "unknown event %r (expected: %s)"
                    % (event, ", ".join(sorted(CHILDREN))))
        return 0
    try:
        run(event, _read_stdin())
    except Exception as e:
        _breadcrumb("hookmux", "%s: %s" % (type(e).__name__, e))
    return 0


if __name__ == "__main__":
    sys.exit(main())
