"""Loop command seam: exit conditions (`exit-add`/`exit-rm`/`exit-show`/`exit-tick`), the wave `scan`, the `invoke` verb that spawns a pre-attached child, and the graded `grade` gate."""
from board import _shared
from board._shared import *          # constants + utilities (explicit __all__)
from board.state import *
from board.model import *
from board.memos import *
from board.sessions import *
from board.render import *
from board.graph import *
from board.boardio import *
from board.cmds.maintain import *
from board.cmds.manage import *
from board.cmds.view import *          # _open_jump_window — the window opener `-s` uses
from board.cmds.sub import *
from board.cmds.surface import *
import json
import os
import shlex
import sys

import config as _config
import exits as _exits
import loop as _loop
import steps as _steps

g, set_g = _shared.g, _shared.set_g

__all__ = [
    "_loop_target", "_exit_step_arg", "_exit_show_lines", "_scan_population",
    "_scan_lines", "_invoke_command", "ASK_CONTEXT_HINT",
    "cmd_exit_add", "cmd_exit_rm", "cmd_exit_show", "cmd_exit_tick",
    "cmd_scan", "cmd_invoke", "cmd_grade", "cmd_orchestrator_check",
]


# ------------------------------------------------------------------ resolution ----

def _loop_target(a, flag):
    """The ONE task this invocation acts on, as `(task, error_line)` — `--task <ref>` by
    seq/id, else the session's attached task.

    The same resolution `claims` uses, deliberately: every task-scoped command in this
    codebase resolves its target the same way, and a reader who has learned one has
    learned all of them. `flag` names the command in the refusal so the line is
    actionable rather than generic."""
    ref = getattr(a, "task", None)
    if ref:
        task = resolve_ref(ref) or load_task(ref)
        if not task:
            return None, "No task matching '%s'.\n\n%s" % (ref, _format_list())
        return task, None
    task = _session_task(getattr(a, "session", None))
    if not task:
        return None, "No task attached — name one with `%s --task <n>`." % flag
    return task, None


def _exit_step_arg(a):
    """The `--step N` argument as an int, or `(None, error)`. Argparse already types it;
    this catches the missing case with a sentence instead of a stack trace."""
    n = getattr(a, "step", None)
    if n is None:
        return None, ("--step <n> is required — an exit condition settles ONE checklist "
                      "item, and the number is the one `/todo <n>` prints.")
    return int(n), None


# ------------------------------------------------------------- exit conditions ----
#
# THE ONE RULE THAT MAKES A PLAN DIFFERENT FROM A LIST: every item carries its own exit
# condition as a runnable command, so DONE is computed rather than asserted. The store
# side lives in lib/exits.py (which explains why, at length, from the migration's own
# evidence); this is the surface.
#
# `exit-tick` is the only verb here that RUNS anything, and it has to be typed. Bare
# `exit-show` reads.

_STATE_MARK = {_exits.MET: " ok ", _exits.UNMET: "FAIL", _exits.UNKNOWN: "  ? "}


def _exit_show_lines(task):
    """The read-only view: every step carrying a condition, its command, what it expects,
    and how it last went. Runs nothing."""
    out = []
    items = _exits.items(task)
    ref = task.get("seq") or task["id"][:8]
    if not items:
        total = len(_steps.live(task.get("steps") or []))
        out.append("  no exit conditions registered on %d step(s) — "
                   "`exit-add --task %s --step 1 --cmd '<command>' --expect '<substring>'`"
                   % (total, ref))
        return out
    counts = _exits.summary(task)
    out.append("  %d of %d live step(s) carry a condition — %d met · %d unmet · %d not run"
               % (counts["total"], len(_steps.live(task.get("steps") or [])),
                  counts[_exits.MET], counts[_exits.UNMET], counts[_exits.UNKNOWN]))
    for item in items:
        mark = _STATE_MARK.get(item["state"], "  ? ")
        tick = "✓" if item["done"] else " "
        out.append("   %s %s step %-3d %s" % (mark, tick, item["n"], item["cmd"]))
        out.append("            expects: %s" % " · ".join(item["expect"]))
        last = item["last"] or {}
        if last.get("status") == "ran" and last.get("missing"):
            out.append("            missing from the output: %s"
                       % " · ".join(last["missing"]))
        elif last.get("status") in ("timeout", "error"):
            out.append("            did NOT run (%s) — nothing was proved either way"
                       % last["status"])
    cover = _exits.coverage(task)
    if cover["uncovered_open"]:
        bare = [n for n, s in _steps.live(task.get("steps") or [])
                if not _exits.has_condition(s) and not _steps.is_done(s)]
        out.append("  %d live step(s) carry NO condition and are not ticked: %s"
                   % (cover["uncovered_open"],
                      ", ".join("step %d" % n for n in bare)))
        out.append("    → this task cannot report itself finished until each is "
                   "covered or ticked; partial instrumentation must not buy a green.")
    ran = _exits.last_run_ts(task)
    out.append("  last run: %s" % (rel_time(ran) if ran else
                                   "never — `exit-tick --task %s`" % ref))
    return out


def cmd_exit_add(a):
    """`task-station exit-add --task REF --step N --cmd '<shell>' --expect '<substr>'…`

    Attach the command that settles step N, and every substring that must appear in its
    combined stdout+stderr. UPSERTS: re-running it on the same step rewrites that
    step's condition and leaves the rest alone.

    THREE FIELDS, THREE FLAGS — not the single pipe-separated spec `claims --register`
    takes. That format needs `\\|` escaping precisely because one flag carries three
    fields, and a claim's command pipes constantly; here the shell command is alone in
    its own flag and needs no escaping at all."""
    task, err = _loop_target(a, "exit-add")
    if err:
        print(err)
        return
    n, err = _exit_step_arg(a)
    if err:
        print(err)
        sys.exit(2)
    steps_list = task.setdefault("steps", [])
    ok, err = _exits.set_condition(steps_list, n, getattr(a, "cmd", None),
                                   getattr(a, "expect", None))
    if not ok:
        print(err)
        sys.exit(2)
    task["updated_ts"] = _now()
    save_task(task)
    ref = task.get("seq") or task["id"][:8]
    print("Exit condition — task #%s step %d" % (ref, n))
    print("  cmd:     %s" % a.cmd.strip())
    print("  expects: %s" % " · ".join(str(e).strip() for e in a.expect if str(e).strip()))
    print("  nothing has been run — `exit-tick --task %s --step %d` settles it"
          % (ref, n))


def cmd_exit_rm(a):
    """`task-station exit-rm --task REF --step N` — drop a step's exit condition,
    leaving the step and its tick intact."""
    task, err = _loop_target(a, "exit-rm")
    if err:
        print(err)
        return
    n, err = _exit_step_arg(a)
    if err:
        print(err)
        sys.exit(2)
    ok, err = _exits.clear_condition(task.setdefault("steps", []), n)
    if not ok:
        print(err)
        sys.exit(2)
    task["updated_ts"] = _now()
    save_task(task)
    print("Removed the exit condition on task #%s step %d (the step is unchanged)."
          % (task.get("seq") or task["id"][:8], n))


def cmd_exit_show(a):
    """`task-station exit-show [--task REF]` — what is registered and how it last went.
    Reads only; runs nothing."""
    task, err = _loop_target(a, "exit-show")
    if err:
        print(err)
        return
    print("Exit conditions — task #%s %s"
          % (task.get("seq") or task["id"][:8], task.get("title")))
    print("\n".join(_exit_show_lines(task)))


def cmd_exit_tick(a):
    """`task-station exit-tick [--task REF] [--step N] [--dry-run] [--untick]`

    RUN the registered conditions and move the ticks they justify. This is the verb the
    whole design turns on: a step whose command passes is ticked BY THE MACHINE, so the
    thirteen-steps-silently-true failure cannot recur.

    EXIT CODE 1 WHEN ANYTHING IS NOT MET — including conditions that could not be run.
    "Not proven met" must never exit 0, because the exit code is what lets this gate a
    release step rather than merely inform a reader. The OUTPUT still distinguishes the
    two: an unmet condition was refuted, an unrun one was not."""
    task, err = _loop_target(a, "exit-tick")
    if err:
        print(err)
        return
    only = getattr(a, "step", None)
    results = _exits.evaluate(task, only=[only] if only else None,
                              timeout=getattr(a, "timeout", None) or None)
    ref = task.get("seq") or task["id"][:8]
    if not results:
        if only:
            print("exit-tick: task #%s step %s has no exit condition registered."
                  % (ref, only))
        else:
            print("exit-tick: task #%s registers no exit conditions — "
                  "`exit-add --task %s --step 1 --cmd '<command>' --expect '<substring>'`."
                  % (ref, ref))
        return
    dry = bool(getattr(a, "dry_run", False))
    moved = ({"ticked": [], "unticked": [], "regressed": [], "unknown": []} if dry
             else _exits.apply_results(task, results,
                                       untick=bool(getattr(a, "untick", False))))
    if not dry:
        task["updated_ts"] = _now()
        save_task(task)
    met = [r for r in results if r["ok"]]
    print("Exit conditions — task #%s %s: %d/%d met.%s"
          % (ref, task.get("title"), len(met), len(results),
             "  (--dry-run: nothing was ticked)" if dry else ""))
    for r in results:
        mark = " ok " if r["ok"] else ("  ? " if r["status"] != "ran" else "FAIL")
        print("  %s step %-3d %s" % (mark, r["n"], r["cmd"]))
        if r["status"] == "timeout":
            print("         timed out — nothing was proved either way, so this is NOT "
                  "a refutation and no tick moved")
        elif r["status"] == "error":
            print("         could not be run: %s" % r["got"])
        elif r["missing"]:
            print("         missing from the output: %s" % " · ".join(r["missing"]))
    if moved["ticked"]:
        print("  ticked: %s" % ", ".join("step %d" % n for n in moved["ticked"]))
    if moved["regressed"]:
        print("  REGRESSED — ticked, but the condition now fails: %s"
              % ", ".join("step %d" % n for n in moved["regressed"]))
        if not moved["unticked"]:
            print("           left ticked; `exit-tick --untick` is how you ask for the "
                  "record to follow the command")
    if moved["unticked"]:
        print("  unticked: %s" % ", ".join("step %d" % n for n in moved["unticked"]))
    done, total = _steps.progress(task.get("steps") or [])
    print("  checklist: %d/%d" % (done, total))
    if len(met) != len(results):
        sys.exit(1)


# -------------------------------------------------------------------- the scan ----

def _scan_population(a):
    """`(nodes, parent_task, depths)` — what this scan is planning over.

    `--task <ref>` plans that task's WHOLE SUBTREE, not just its child row: an
    orchestrator whose child has itself become an orchestrator would otherwise report
    that child as the startable unit, when the thing anybody can actually pick up is two
    levels down. A scan that stops at depth one is the silo Open tail again — correct,
    current, and not where the work is. `--depth N` caps it for a very deep tree.

    `--all` plans every open/active task on the board. Naming a task with no descendants
    falls back to planning the task ITSELF, so `scan --task <n>` on a leaf is a useful
    answer ("here is your own exit state") rather than an empty report that looks like a
    bug."""
    every = all_tasks()
    if getattr(a, "all", False):
        return [t for t in sorted_tasks() if not _loop.is_closed(t)], None, {}
    task, err = _loop_target(a, "scan")
    if err:
        return None, err, {}
    tree = _loop.descendants(task, every)
    cap = getattr(a, "depth", None)
    if cap:
        tree = [(t, d) for t, d in tree if d <= int(cap)]
    if not tree:
        return [task], task, {}
    return [t for t, _d in tree], task, {t.get("id"): d for t, d in tree}


def _scan_lines(report, parent, ran):
    """The human reading of a scan report. The JSON is the same object — one computation,
    two renderings, so a driver and a person can never be told different things."""
    out = []
    totals = report["totals"]
    head = "Scan — %s · %d node(s) · %d settled · STOP: %s" % (
        ("task #%s %s" % (parent.get("seq") or parent["id"][:8], parent.get("title")))
        if parent else "the open board",
        totals["total"], totals["settled"], report["stop"].upper())
    out.append(head)
    if ran:
        out.append("  (conditions were RE-RUN for this scan)")
    else:
        out.append("  (stored results — nothing was run; `scan --run` re-runs every "
                   "condition first)")
    for wave, rows in report["waves"].items():
        ready = len([r for r in rows if r["ready"]])
        out.append("")
        out.append("  wave %d — %d node(s), %d ready" % (wave, len(rows), ready))
        for r in rows:
            out.append("    %s" % _scan_row(r))
    orphans = [r for r in report["rows"] if not r["wave"]]
    if orphans:
        out.append("")
        out.append("  no wave (inside or downstream of a dependency cycle):")
        for r in orphans:
            out.append("    %s" % _scan_row(r))
    for cyc in report["cycles"]:
        out.append("  CYCLE: %s — nothing in it can ever start; break one edge with "
                   "`update --task <n> --unrelate <m>`" % " → ".join(c[:8] for c in cyc))
    out.append("")
    ex = report["exits"]
    out.append("  exit conditions: %d of %d node(s) register any" % (ex["registered"],
                                                                    totals["total"]))
    if ex["unregistered"]:
        out.append("    NO CONDITIONS (so they can never report themselves done): %s"
                   % ", ".join("#%s" % s for s in ex["unregistered"]))
    if report["stop"] == _loop.READY:
        out.append("  READY NOW: %s" % ", ".join("#%s" % s for s in report["ready"]))
        first = report["ready"][0]
        out.append("  next: task-station invoke --task %s --from %s --ask '<the request>'"
                   % (first, (parent.get("seq") if parent else "<orchestrator>")))
    elif report["stop"] == _loop.COMPLETE:
        out.append("  COMPLETE — every node is closed or has every exit condition met.")
    elif report["stop"] == _loop.BLOCKED:
        out.append("  BLOCKED — work remains and none of it is startable. The blockers "
                   "are named above; a park needs a human, not a retry.")
    else:
        out.append("  EMPTY — this task has no children, so there is no plan to compute "
                   "over. Give a child `update --task <child> --parent %s`."
                   % (parent.get("seq") if parent else "<n>"))
    return out


def _scan_row(r):
    """One node's line: status glyph, ref, title, exit rollup, and what holds it."""
    counts = r["exits"]
    cover = r.get("coverage") or {}
    if r["exit_state"] == _exits.NONE:
        ex = "exits: none registered"
    else:
        ex = "exits: %d/%d met" % (counts[_exits.MET], counts["total"])
        if counts[_exits.UNKNOWN]:
            ex += " (%d unrun)" % counts[_exits.UNKNOWN]
        if cover.get("uncovered_open"):
            ex += " +%d uncovered" % cover["uncovered_open"]
    tail = ""
    if r.get("orchestrator"):
        tail = "  orchestrator"
    elif r["parked"]:
        tail = "  PARKED: %s" % r["parked"]
    elif r["settled"]:
        tail = "  settled"
    elif r["ready"]:
        tail = "  READY"
    elif r["blocked_by"]:
        tail = "  blocked by %s" % ", ".join("#%s" % b["seq"] for b in r["blocked_by"])
    elif r["in_cycle"]:
        tail = "  in a cycle"
    if r["dangling"]:
        tail += "  (depends on %d task(s) that no longer exist)" % len(r["dangling"])
    # A node deeper than a direct child says WHOSE it is — the wave column groups by
    # readiness, not by family, so without this a grandchild reads as a sibling.
    depth = r.get("tree_depth") or 0
    label = ("· " * (depth - 1)) + ((r["title"] or "")[:42 - 2 * max(0, depth - 1)])
    return "%-6s %-42s %-28s%s" % ("#%s" % r["seq"], label, ex, tail)


def cmd_scan(a):
    """`task-station scan [--task REF] [--all] [--run] [--json]`

    THE ZERO-TOKEN HALF OF THE LOOP DRIVER. Computes waves over `depends-on`, rolls up
    each node's exit conditions, and prints the stopping condition. No model is called
    and — unless `--run` is given — no command either: it reads the results `exit-tick`
    already stored, so the scan itself costs a few file reads.

    That default is the design. A scan that re-ran every condition would be expensive
    enough that nobody would run it often, and a planner nobody runs is the silo Open
    tail all over again. `--run` is there for the moment a parent actually needs a fresh
    verdict — the gate before releasing a wave."""
    nodes, parent, depths = _scan_population(a)
    if nodes is None:
        print(parent)      # the resolution error line
        return
    ran = bool(getattr(a, "run", False))
    if ran:
        for t in nodes:
            results = _exits.evaluate(t)
            if results:
                _exits.apply_results(t, results)
                t["updated_ts"] = _now()
                save_task(t)
    every = all_tasks()
    by_id = {t.get("id"): t for t in every}
    nodes = [by_id.get(t.get("id"), t) for t in nodes]
    # The DEEP settled rule — a parent whose children are unbuilt is not settled, however
    # green its own checklist is. Built over the WHOLE store, not just the scanned nodes,
    # so a child sitting outside the scanned subtree still counts.
    report = _loop.scan(nodes, by_id.get, is_settled=_loop.settled_fn(every),
                        depths=depths)
    if getattr(a, "as_json", False):
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
        return
    print("\n".join(_scan_lines(report, parent, ran)))


# ------------------------------------------------------------------ the invoke ----
#
# B10 — CHILD AS ATTACHED SESSION. The child is spawned ALREADY LINKED to its own task,
# so the SessionStart hook injects that task's digest and the child reads its own
# context from the record instead of from a brief somebody wrote. That is what kills the
# lossy-brief boundary BY CONSTRUCTION rather than by discipline: there is no brief to
# get wrong, and the ask carries the REQUEST only.

# Past this many characters an ask has almost certainly stopped being a request and
# started being context. A WARNING, never a refusal: a legitimate request can be long,
# and refusing one would be this command inventing a rule nobody agreed to. The warning
# names the reason, which is the part that changes behaviour.
ASK_CONTEXT_HINT = 800


def _invoke_command(base, role, model, permission_mode, ask):
    """Assemble the child's launch command from the pre-bound `cd … && claude
    --session-id <sid>` base.

    The ask is `shlex.quote`d rather than wrapped in quotes by hand — an ask containing
    an apostrophe is the common case, not an edge one, and a hand-quoted one would
    truncate at it."""
    parts = [base]
    spec = _loop.role_spec(role) if role else None
    chosen_model = model or (spec or {}).get("model")
    chosen_mode = permission_mode or (spec or {}).get("permission_mode")
    if chosen_model:
        parts.append("--model %s" % shlex.quote(chosen_model))
    if chosen_mode:
        parts.append("--permission-mode %s" % shlex.quote(chosen_mode))
    parts.append(shlex.quote(ask))
    return " ".join(parts)


def cmd_invoke(a):
    """`task-station invoke --task CHILD --from ORCH --ask '<the request>'`

    Spawn a session that is ALREADY ATTACHED to the child task, carrying the request and
    nothing else. The child's context comes from its own record via the hooks; the
    orchestrator writes no brief, which is the point — every brief is a lossy copy of a
    digest that already exists.

    `--role` picks the child's model and permission mode from the role table (scout /
    implementer / reviewer / judge); `--model` and `--permission-mode` override it. With
    neither, the child inherits the harness defaults."""
    child, err = _loop_target(a, "invoke")
    if err:
        print(err)
        sys.exit(2)
    ask = str(getattr(a, "ask", "") or "").strip()
    if not ask:
        print("invoke needs --ask '<the request>' — the one thing the orchestrator has "
              "to say that the child's own task record cannot tell it.")
        sys.exit(2)
    orch = None
    from_ref = getattr(a, "from_ref", None)
    if from_ref:
        orch = resolve_ref(from_ref) or load_task(from_ref)
        if not orch:
            print("No task matching '%s' for --from." % from_ref)
            sys.exit(2)
    else:
        orch = _session_task(getattr(a, "session", None))
    if orch and orch.get("id") == child.get("id"):
        print("invoke: --task and --from name the same task. A task cannot invoke "
              "itself; name the CHILD that should own this work.")
        sys.exit(2)
    if is_closed(child):
        print("invoke: task #%s is closed. Reopen it first (`/todo %s`) — invoking a "
              "closed task would spawn a session with nothing to resume."
              % (child.get("seq"), child.get("seq")))
        sys.exit(2)
    role = getattr(a, "role", None)
    if role and not _loop.role_spec(role):
        print("invoke: %r is not a role. They are %s."
              % (role, ", ".join(sorted(_loop.ROLES))))
        sys.exit(2)
    warnings = []
    if orch and _loop.parent_id(child) != orch.get("id"):
        warnings.append("#%s is not a child of #%s — the roll-up will not count it. "
                        "`update --task %s --parent %s` fixes that."
                        % (child.get("seq"), orch.get("seq"),
                           child.get("seq"), orch.get("seq")))
    if len(ask) > ASK_CONTEXT_HINT:
        warnings.append("the ask is %d characters. That is usually context, not a "
                        "request — the child already gets its own task digest at "
                        "session start, so anything restating it is a lossy copy."
                        % len(ask))
    sid, base = fresh_resume_command(child, preborn=True)
    cwd = getattr(a, "cwd", None)
    if cwd:
        base = "cd %s && claude --session-id %s" % (shlex.quote(os.path.expanduser(cwd)),
                                                    sid)
    cmd = _invoke_command(base, role, getattr(a, "model", None),
                          getattr(a, "permission_mode", None), ask)
    child = load_task(child["id"]) or child
    detail = "invoked by #%s%s" % (orch.get("seq") if orch else "?",
                                   " as %s" % role if role else "")
    add_event(child, "child", "%s: %s" % (detail, ask[:160]),
              session=getattr(a, "session", None))
    child["updated_ts"] = _now()
    save_task(child)
    if orch:
        orch = load_task(orch["id"]) or orch
        add_event(orch, "child", "invoked #%s (%s) — %s"
                  % (child.get("seq"), role or "no role", ask[:160]),
                  session=getattr(a, "session", None))
        orch["updated_ts"] = _now()
        save_task(orch)
    print("Invoke — #%s %s%s"
          % (child.get("seq"), child.get("title"),
             ("  ← #%s" % orch.get("seq")) if orch else ""))
    print("  session %s is pre-attached: its SessionStart injects THIS task's digest, "
          "so the ask carries the request only." % sid[:8])
    for w in warnings:
        print("  note: %s" % w)
    if getattr(a, "print_command", False):
        print("  run it yourself:")
        print("    %s" % cmd)
        return
    if _open_jump_window(cmd):
        print("  opened a new window running it (this one is untouched):")
        print("    %s" % cmd)
        return
    print("  could not open a window (macOS/Terminal only) — run it yourself:")
    print("    %s" % cmd)


# ------------------------------------------------------------------- the grade ----

def cmd_grade(a):
    """`task-station grade --task CHILD --dim G1=A --dim G2=A- … [--park REASON --why W]`

    Record one pass of the graded acceptance gate on a child task. The ENGINE does the
    arithmetic and the recording; the JUDGMENT — what grade each dimension earns — is
    the skill's, and no flag here can supply it.

    ACCEPTANCE IS PER-DIMENSION, not an average. A rubric averaged
    into one number lets a failed gate-integrity dimension hide behind five strong ones,
    which is the exact failure six separate dimensions exist to prevent. An UNGRADED
    dimension is not a pass either — it is work the judge has not done.

    EXIT CODES, so a driver can branch without parsing prose: 0 accepted · 1 rejected
    with retries left · 3 rejected with the retry budget spent (park it) · 4 parked ·
    2 the command was wrong."""
    task, err = _loop_target(a, "grade")
    if err:
        print(err)
        sys.exit(2)
    threshold = getattr(a, "threshold", None) or _config.loop_accept_threshold()
    park = getattr(a, "park", None)
    if park and park not in _loop.PARK_REASONS:
        print("grade --park %r — the reasons are %s. A park is the loop saying THIS DOES "
              "NOT COME BACK TO ME, so it is a short closed list rather than free text."
              % (park, ", ".join(_loop.PARK_REASONS)))
        sys.exit(2)
    if park and not getattr(a, "why", None):
        print("grade --park needs --why: a park stops the retries, and one with no "
              "reason is indistinguishable later from work somebody quietly dropped.")
        sys.exit(2)
    dims, errors = _loop.parse_dimensions(getattr(a, "dim", None))
    for line in errors:
        print(line)
    if errors:
        sys.exit(2)
    if not dims and not park:
        print("grade needs at least one --dim G1=A-, or --park <reason> --why '<why>'. "
              "The dimensions are:")
        for key in _loop.DIMENSION_KEYS:
            print("  %s  %-28s %s" % (key, _loop.DIMENSION_TITLES[key],
                                      _loop.DIMENSION_QUESTIONS[key]))
        sys.exit(2)
    note = getattr(a, "note", None) or getattr(a, "why", None)
    entry, v = _loop.record(task, dims, threshold, note=note,
                            session=getattr(a, "session", None), park=park)
    retry_max = _config.loop_retry_max()
    left = _loop.retries_left(task, retry_max)
    ref = task.get("seq") or task["id"][:8]
    line = ("PARKED (%s) — %s" % (park, note)) if park else _loop.verdict_line(v)
    if not getattr(a, "no_decision", False):
        append_decision(task, "Gate %s: %s%s"
                        % (ref, line,
                           (" — %s" % note) if note and not park else ""),
                        session=getattr(a, "session", None))
    task["updated_ts"] = _now()
    save_task(task)
    if getattr(a, "as_json", False):
        print(json.dumps({"task": ref, "entry": entry, "verdict": v,
                          "attempts": _loop.attempts(task),
                          "retries_left": left}, indent=2, sort_keys=True,
                         default=str))
    else:
        print("Gate — task #%s %s" % (ref, task.get("title")))
        for key in _loop.DIMENSION_KEYS:
            grade = dims.get(key)
            mark = ("  " if grade is None else
                    ("ok" if _loop.meets(grade, threshold) else "NO"))
            print("  %s %s %-28s %s" % (mark, key, _loop.DIMENSION_TITLES[key],
                                        grade or "— not graded"))
        print("  %s" % line)
        print("  attempt %d — %d retr%s left before this must be parked"
              % (_loop.attempts(task), left, "y" if left == 1 else "ies"))
        if park:
            print("  parked children are NEVER retried; a human-gate park waits for a "
                  "person, not for the loop.")
    if park:
        sys.exit(4)
    if v["accepted"]:
        return
    sys.exit(1 if left else 3)


# ---------------------------------------------------- the orchestrator guard ----

def cmd_orchestrator_check(a):
    """`task-station orchestrator-check --task REF` — is delegating FROM this task
    allowed?

    Prints nothing and exits 0 when it is. When the task is flagged orchestrator-only it
    prints the refusal — which NAMES the ready child that should own the work — and
    exits 3. `delegate.py` calls this before it spawns anything; the distinct exit code
    is what lets it tell "refused" apart from "the check itself broke", and a broken
    check must never block a delegation.

    The check lives here rather than inside delegate because the answer depends on the
    wave computation, and there must be exactly one of those."""
    task, err = _loop_target(a, "orchestrator-check")
    if err:
        print(err)
        sys.exit(2)
    refusal = _loop.orchestrator_refusal(task, all_tasks(),
                                         verb=getattr(a, "verb", None) or "delegate run")
    if not refusal:
        return
    print(refusal)
    sys.exit(3)
