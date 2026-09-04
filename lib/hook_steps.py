#!/usr/bin/env python3
"""hook_steps.py — every best-effort step of a hook, in ONE interpreter, and OFF
the turn's critical path.

WHY THIS EXISTS. hooks/on_stop.sh ran seven separate python3 processes at every
turn end: stop-nudge, board --refresh-if-live, obsidian --flush, usage --flush,
subscriptions check, recap --auto-if-due, and hud turn-end. Each paid ~90ms of
interpreter start-up plus a fresh import of the engine — ~0.7s of the user's turn
spent starting Python — and each one rebuilt its in-process caches from nothing, so
the transcript parsing the board had just done was thrown away and redone.

WHY IT NOW COVERS TWO EVENTS, AND WHY IT DETACHES. Consolidating the processes
took the interpreter tax off, and left the real bill: MEASURED on 3.63.0 against a
real session, `board --refresh-if-live` cost 11.3s of a 12.5s Stop hook and
`hook sweep-orphans` cost 20.7s of a 23.0s SessionStart hook, both while the user
waited. Neither prints anything a hook contract reads. So the steps are split by
the only property that decides whether the turn must wait for them:

  * **FOREGROUND** — the step's stdout IS the hook's answer to the harness
    (:data:`PASSTHROUGH`). It runs inline, and the turn waits, because there is
    nothing else it could mean.
  * **BACKGROUND** — everything else. It runs in a DETACHED session of its own
    (:func:`detach`) and the hook returns without it. The work is not dropped and
    not weakened: the same steps, in the same order, through the same
    :func:`run_step`, with the same hook-health labels — a failure still lands in
    <data_dir>/logs/hook-health.log under the name it always had. What changes is
    only WHO waits.

Detaching means a background step's effect lands SHORTLY AFTER the hook, not
before it. That is a real consequence and it is the intended one: a board refresh,
a usage rescan, and an orphan sweep are all housekeeping whose reader is the NEXT
command, never the turn that triggered them.

WHAT DOES NOT MOVE HERE. `stop-gate` stays its own synchronous call AHEAD of this
one, and it is not a step in this file at all. The harness reads its stdout for the {"decision":"block"} contract, so it must
not be merged, reordered, or have anything else printed around it.

STDOUT IS A CONTRACT. Only `stop-nudge` may print (its additionalContext is read by
the harness — the shell used `ts_capture`, not `ts_run`, for exactly that one step).
Every other step's stdout is captured and dropped, as `ts_run` always did. That
capture is `redirect_stdout`, which swaps `sys.stdout` and so does NOT cover a child
process writing to fd 1 — every subprocess the engine spawns captures or DEVNULLs
its own output today (audited), and a new one that doesn't would leak into this
hook's stdout. Keep it that way.

FAILURE ISOLATION IS PRESERVED. Each step is wrapped: an exception or a sys.exit is
caught, recorded to <data_dir>/logs/hook-health.log under the SAME label `ts_run`
used, and the following steps still run. This process always exits 0. The one thing
consolidation genuinely gives up is protection from a hard interpreter crash
(segfault, os._exit), which would take the remaining steps with it — a normal
exception cannot.
"""
import importlib.util
import io
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout

LIB = os.path.dirname(os.path.abspath(__file__))
if LIB not in sys.path:
    sys.path.insert(0, LIB)

import hook_health                                                    # noqa: E402

# (label, target, argv) — labels are the SAME strings ts_run logged, because they are
# the only handle a human gets on which call site broke. Order matches the shell's.
# SESSION is a placeholder replaced by whole-token equality, never %-formatting or
# string interpolation: a session id is external input and must not be able to reach
# a format string. No real argument in these fixed templates can collide with it.
SESSION = "<session-id>"
STEPS = (
    ("stop-nudge", "engine", ["hook", "stop-nudge", "--session", SESSION]),
    ("board-refresh", "engine", ["board", "--refresh-if-live"]),
    ("obsidian-flush", "engine", ["obsidian", "--flush", "--quiet"]),
    ("usage-flush", "engine", ["usage", "--flush", "--quiet"]),
    ("subscriptions-check", "engine",
     ["subscriptions", "check", "--throttle", "--session", SESSION]),
    ("recap-auto", "engine", ["recap", "--auto-if-due", "--quiet"]),
    ("hud-turn-end", "hud", ["turn-end", "--session", SESSION]),
)
# SessionStart's silent steps, in the order hooks/on_session_start.sh ran them as
# separate `ts_run` python3 calls, plus `prune-cache`, which is new here because the
# activate flow is where it belongs (see board/plugincache.py). `hook session-start`,
# `session-tint` and `session-title` are NOT here: the shell reads their stdout to
# build the SessionStart document, so they are the foreground and stay in the shell.
SESSION_START_STEPS = (
    ("obsidian-flush", "engine", ["obsidian", "--flush", "--quiet"]),
    ("usage-flush", "engine", ["usage", "--flush", "--quiet"]),
    ("sweep-orphans", "engine", ["hook", "sweep-orphans", "--session", SESSION]),
    ("prune-cache", "engine", ["hook", "prune-cache"]),
)
# event -> the NAME of this module's table for it. By name, not by value: a caller
# (or a test) that replaces `STEPS` must be replacing the table that actually runs,
# and a dict captured at import time would quietly keep the original.
EVENTS = {"stop": "STEPS", "session-start": "SESSION_START_STEPS"}
DEFAULT_EVENT = "stop"
# The ONLY step whose stdout reaches the harness (see STDOUT IS A CONTRACT above),
# and therefore the only one the turn waits for. Everything else detaches.
PASSTHROUGH = ("stop-nudge",)


_ENGINE = None


def _engine():
    """The engine module, loaded once and reused — which is the whole point: six of
    the seven steps run against it, and they now share one import AND one set of
    warm transcript caches.

    task-station.py has a hyphen, so it can't be a normal import (same importlib
    load as lib/mcp_server.py). It IS registered in sys.modules afterwards, unlike
    there, so `sys.modules.get(__name__)` inside write_board resolves to a real
    module — exactly what happens when the CLI runs it as __main__ — and the feed
    export takes the same branch it takes today. Registered only AFTER a successful
    exec, so a failed import is never cached as a half-built module."""
    global _ENGINE
    if _ENGINE is None:
        spec = importlib.util.spec_from_file_location(
            "task_station", os.path.join(LIB, "task-station.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules.setdefault("task_station", mod)
        _ENGINE = mod
    return _ENGINE


def _detail(exc, err):
    """The hook-health detail field: the last non-blank stderr line if the step wrote
    one (what the shell logged), else the exception itself. `ValueError: no such task`
    is more use to a human than an empty column."""
    for line in reversed((err or "").splitlines()):
        if line.strip():
            return line.strip()
    if exc is None:
        return ""
    return "%s: %s" % (type(exc).__name__, exc)


def run_step(label, target, argv, out):
    """Run one step with its stdout/stderr captured. Returns (code, detail): code 0 on
    success, else the SystemExit code or 1 for an exception. Anything the step printed
    is appended to `out` only when the step is a passthrough one.

    Nothing propagates. A step that fails is a logged line, never a broken turn."""
    buf, errbuf = io.StringIO(), io.StringIO()
    code, exc = 0, None
    try:
        with redirect_stdout(buf), redirect_stderr(errbuf):
            if target == "hud":
                import hud
                hud.main(argv)
            else:
                _engine().main(argv)
    except SystemExit as e:                  # argparse/`sys.exit` inside a command
        try:
            code = int(e.code or 0)
        except (TypeError, ValueError):
            code = 1
    except BaseException as e:               # noqa: BLE001 — a hook swallows everything
        code, exc = 1, e
    if label in PASSTHROUGH:
        out.append(buf.getvalue())
    return code, _detail(exc, errbuf.getvalue())


def steps_for(event, phase):
    """The steps of `event` that belong to `phase` — "foreground", "background", or
    "all" — in their declared order.

    One table, split by :data:`PASSTHROUGH` alone, so a step can never be in both
    phases and adding a step to a table is all it takes to schedule it."""
    table = globals().get(EVENTS.get(event, ""), ())
    if phase == "all":
        return table
    fg = phase == "foreground"
    return tuple(s for s in table if (s[0] in PASSTHROUGH) is fg)


def run_phase(event, phase, session, out):
    """Run one phase's steps; a failure is logged and the rest still run."""
    for label, target, template in steps_for(event, phase):
        step_argv = [session if x == SESSION else x for x in template]
        code, detail = run_step(label, target, step_argv, out)
        if code != 0:
            try:
                hook_health.record(label, code, detail)
            except Exception:
                pass                         # the reporter must not outrank the hook


def detach(event, session):
    """Launch this file again, in a session of its OWN, to run `event`'s background
    steps; return whether the launch happened.

    ``start_new_session=True`` is the whole point — the child leaves the hook's
    process group, so it outlives the hook rather than being torn down with it. It
    is the same detachment `brain.hooks.inject._spawn_orgpull` has run in
    production since 3.0.0, which is why this does not invent a second mechanism.
    stdio is DEVNULL: a background step must never reach a hook's stdout, which is
    a contract, nor its stderr, which the harness stores.

    Fail-open — a spawn that cannot happen is recorded and returns False, never
    raises. A hook whose housekeeping could not start is still a working hook, and
    the caller is told, so nothing can mistake a failed spawn for a fired one."""
    try:
        subprocess.Popen(
            [sys.executable, os.path.abspath(__file__),
             "--event", event, "--phase", "background", "--session", session],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
            env=os.environ.copy())
        return True
    except Exception as e:                   # noqa: BLE001 — a hook swallows everything
        try:
            hook_health.record("%s-detach" % event, 1,
                               "%s: %s" % (type(e).__name__, e))
        except Exception:
            pass
        return False


def _opt(args, name, default):
    """`--name value` out of a plain argv, or `default`. argparse is not worth an
    import on a path that runs at every turn end."""
    if name in args:
        i = args.index(name)
        if i + 1 < len(args):
            return args[i + 1]
    return default


def main(argv=None):
    """Run one event's steps and always exit 0.

    Default (`--phase run`, i.e. no flag): the foreground steps inline, then the
    background ones DETACHED — the shape both hooks call. `--phase background` is
    what the detached child runs; `--phase all` runs everything inline and is the
    pre-3.64.0 behaviour, kept because a test that wants the effects without a
    race needs a way to ask for them."""
    args = list(sys.argv[1:] if argv is None else argv)
    session = _opt(args, "--session", "unknown")
    event = _opt(args, "--event", DEFAULT_EVENT)
    phase = _opt(args, "--phase", "run")
    out = []
    if phase in ("run", "foreground", "all"):
        run_phase(event, "all" if phase == "all" else "foreground", session, out)
    if phase == "run":
        detach(event, session)
    elif phase == "background":
        run_phase(event, "background", session, out)
    text = "".join(out)
    if text:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
