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

import channel as _channel
import checker as _checker
import config as _config
import decisions as _dec
import exits as _exits
import gating as _gating
import loop as _loop
import steps as _steps
import treeref as _treeref
import turn as _turn
import succession as _succ
from board import workspace as _workspace

g, set_g = _shared.g, _shared.set_g

__all__ = [
    "_loop_target", "_exit_step_arg", "_exit_show_lines", "_scan_population",
    "_scan_lines", "_child_prompt", "_invoke_command", "ASK_CONTEXT_HINT",
    "DRY_RUN_SID", "MANUAL_LAUNCH", "_record_launch",
    "cmd_exit_add", "cmd_exit_rm", "cmd_exit_show", "cmd_exit_tick",
    "cmd_scan", "cmd_invoke", "cmd_grade", "cmd_orchestrator_check",
    "cmd_decompose", "cmd_relay", "HUB_CWD", "_successor_cwd", "_await_registration",
    "_live_seqs", "_announce_spec_change", "_channel_report_back", "cmd_channel",
    "cmd_pickup", "_pickup_report_text", "_subscribe_lines",
    "cmd_turn",
]


# ------------------------------------------------------------------ resolution ----

def _live_seqs():
    """The task seqs with a Claude session RUNNING right now, or an empty set.

    Same derivation the HTML board already uses (`write_board`'s `live_seqs`) — process
    liveness, not a transcript flag that survives a crash. Fail-open: a sessions-dir
    hiccup must degrade the scan to "nothing reported running", never break it, because
    a planner that refuses to answer is worse than one that answers without this column.
    """
    seqs = _live_seqs_or_none()
    return set() if seqs is None else seqs


def _live_seqs_or_none():
    """The same derivation, but `None` when liveness CANNOT BE DETERMINED — which is a
    different fact from "nothing is running" and the two must not share a value.

    Fail-open is right for the scan's display column and WRONG for the concurrency cap:
    an empty set tells the budget that no children are live, so a sessions-dir hiccup
    silently raises the cap to unlimited and the loop spawns into a machine it cannot
    see. The display keeps degrading gracefully; the budget refuses instead."""
    try:
        import live_sessions
        return {r.get("task_seq") for r in live_sessions.running()
                if r.get("task_seq") is not None}
    except Exception:
        return None


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
    # THE MERGE-GATED HEADER, and it is the reason this surface was changed. Until 3.45.0
    # a declared condition and an undeclared one printed byte-identically here, so on a
    # night when nobody can merge — when EVERY condition on EVERY child is red for that one
    # reason — the reader had no way to tell red-because-unmerged from red-because-broken.
    # `gating.header_notes` prints nothing at all when nothing is declared, so the line
    # only ever appears where it carries information.
    out += ["  %s" % line for line in _gating.header_notes(_exits.merge_gate(task))]
    for item in items:
        mark = _STATE_MARK.get(item["state"], "  ? ")
        tick = "✓" if item["done"] else " "
        out.append("   %s %s step %-3d %s" % (mark, tick, item["n"], item["cmd"]))
        note = _gating.step_note(item["merge_gated"])
        if note:
            out.append("            %s" % note)
        if item.get("decl"):
            out.append("            tree: %s" % _treeref.long_label(item["decl"]))
        out.append("            expects: %s" % " · ".join(item["expect"]))
        # WHICH TREE THE LAST VERDICT WAS PRODUCED AGAINST, printed only when the run
        # recorded one. A legacy result gets no line at all rather than an invented
        # default — saying where an unrecorded run happened is the exact failure this
        # surface exists to end.
        prov = _treeref.provenance_line(item.get("tree"))
        if prov:
            out.append("            %s" % prov)
        last = item["last"] or {}
        if last.get("status") == "ran":
            note = _checker.exit_note("ran", last.get("code"), last.get("missing") or [])
            if note:
                out.append("            %s" % note)
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


def _build_slot(a, verb, label):
    """`(token, refused)` — hold the machine's build slot for the run about to happen.

    THE SUITE IS THE BUILD. `exit-tick` and `scan --run` are the two verbs in this
    codebase that execute somebody's test command, and `loop_builds_max` (default 1) is
    how many of those may run ON THIS MACHINE at once. The cap is not timidity: this
    machine OOMs on concurrent builds, and this repo's load-dependent flakes made a
    parallel suite run a source of FALSE RED — a gate that goes red for a reason having
    nothing to do with the work is worse than no gate at all.

    It WAITS before it refuses (`--build-wait`, default the exit-command timeout),
    because a contended slot usually frees when the other suite finishes and a slower
    loop beats a red nobody caused. When it does refuse, NOTHING RAN — which is why the
    caller exits 3 rather than 1: an unmet condition was refuted, this one was never
    asked."""
    wait = getattr(a, "build_wait", None)
    wait = _config.exit_command_timeout() if wait is None else wait
    token = _loop.acquire_build_slot(label, wait=wait)
    if token:
        return token, False
    print("%s: did NOT run — the machine-wide build slot is taken and "
          "loop_builds_max is %d." % (verb, _config.loop_builds_max()))
    for h in _loop.build_slot_holders():
        print("  held by pid %s since %s: %s"
              % (h.get("pid"), rel_time(int(h.get("started_ts") or 0)),
                 h.get("label") or "(unlabelled)"))
    print("  nothing was proved either way, so no tick moved. Retry when it frees, or "
          "raise loop_builds_max if this machine really can take two.")
    return None, True


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
    # THE SELF-CHECK, BEFORE ANYTHING IS STORED (B7). A registered condition that cannot
    # run, or that can be satisfied by something other than the work, is worse than no
    # condition: it reports on the board as a computed gate. Both checks are STATIC — the
    # shell is asked to PARSE the command, never to run it, because registering a
    # condition must not have side effects.
    cmd_raw = getattr(a, "cmd", None)
    expect_raw = getattr(a, "expect", None)
    problems = []
    syntax = _turn.shell_syntax_error(cmd_raw)
    if syntax:
        problems.append({"code": "shell-syntax", "line":
                         "the shell cannot parse it: %s. A command stored broken looks "
                         "registered and can never run." % syntax})
    problems += _turn.condition_lint(cmd_raw, expect_raw)
    forced = bool(getattr(a, "force", False))
    if problems:
        print("Exit condition — task #%s step %s: %d problem(s) found before storing it"
              % (task.get("seq") or task["id"][:8], n, len(problems)))
        for pr in problems:
            print("  %-18s %s" % (pr["code"], pr["line"]))
        if not forced:
            print("  nothing was stored. Fix the shape, or pass --force to register it "
                  "anyway and have that recorded.")
            sys.exit(2)
        print("  --force: registering it anyway.")
    decl, derr = _treeref.parse(getattr(a, "repo", None), getattr(a, "ref", None))
    if derr:
        # NOT FORCEABLE, unlike the shape lint above. A declaration that does not resolve
        # has nothing for merge-gated to be computed FROM, and the moment one can be
        # stored anyway the author is asserting merge-gatedness again by the back door.
        print("exit-add: %s" % derr)
        sys.exit(2)
    ok, err = _exits.set_condition(steps_list, n, cmd_raw, expect_raw,
                                   merge_gated=bool(getattr(a, "merge_gated", False)),
                                   decl=decl)
    if not ok:
        print(err)
        sys.exit(2)
    task["updated_ts"] = _now()
    save_task(task)
    ref = task.get("seq") or task["id"][:8]
    print("Exit condition — task #%s step %d" % (ref, n))
    if decl:
        print("  tree:    %s" % _treeref.label(decl))
        print("           resolved to %s — the runner checks that out and runs the "
              "command THERE, so the directory it inherits cannot decide the answer."
              % decl[_treeref.RESOLVED_KEY])
        if _treeref.merge_gated(decl):
            print("  MERGE-GATED, COMPUTED — that ref is a remote-tracking ref, so this "
                  "condition cannot be green until the work lands there. Nobody typed "
                  "it: it follows from the ref, and the loop can say DONE PENDING MERGE "
                  "instead of calling a finished child unfinished.")
    elif getattr(a, "merge_gated", False):
        print("  MERGE-GATED — it reads the merge target, so it stays red until this "
              "work lands there. That is recorded, not inferred: the loop can now say "
              "DONE PENDING MERGE instead of calling a finished child unfinished.")
    print("  cmd:     %s" % a.cmd.strip())
    print("  expects: %s" % " · ".join(str(e).strip() for e in a.expect if str(e).strip()))
    print("  nothing has been run — `exit-tick --task %s --step %d` settles it"
          % (ref, n))
    _announce_spec_change(
        task, "exit condition for step %d is now: %s (expects %s). Re-read the checklist "
              "before you call anything done." % (n, a.cmd.strip(),
                                                 " · ".join(str(e).strip()
                                                            for e in a.expect
                                                            if str(e).strip())),
        getattr(a, "session", None))


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
    _announce_spec_change(
        task, "step %d no longer carries an exit condition — it is no longer computed, "
              "and nothing will tick it for you." % n,
        getattr(a, "session", None))


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
        # NOT ZERO. This function's own docstring says "Not proven met must never exit 0,
        # because the exit code is what lets this gate a release step" — and a task that
        # could not even be RESOLVED has proven nothing at all. Exiting 0 here made an
        # unresolvable --task indistinguishable from every condition passing, which is
        # the failure direction a release gate must never have.
        print(err)
        sys.exit(2)
    only = getattr(a, "step", None)
    was_satisfied = _exits.satisfied(task)
    ref = task.get("seq") or task["id"][:8]
    token, refused = _build_slot(a, "exit-tick", "exit-tick #%s" % ref)
    if refused:
        sys.exit(3)
    try:
        results = _exits.evaluate(task, only=[only] if only else None,
                                  timeout=getattr(a, "timeout", None) or None)
    finally:
        _loop.release_build_slot(token)
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
        else:
            note = _checker.exit_note(r["status"], r.get("code"), r["missing"])
            if note:
                print("         %s" % note)
        prov = _treeref.provenance_line(r.get("tree"))
        if prov:
            print("         %s" % prov)
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
    # THE TRANSITION, not the state: only the run that CROSSES into fully-satisfied
    # reports upward. A memo on every green tick would train the reader to skip the rail.
    if not dry and not was_satisfied and _exits.satisfied(task):
        pseq = report_to_parent(task, "every exit condition is now MET — ready for the "
                                      "gate (`/grade`)", getattr(a, "session", None))
        if pseq:
            print("  told #%s: this child reports ready for the gate." % pseq)
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
    if report.get("running"):
        out.append("  RUNNING NOW: %s" % ", ".join("#%s" % s for s in report["running"]))
    if report["stop"] == _loop.READY:
        out.append("  READY NOW: %s" % ", ".join("#%s" % s for s in report["ready"]))
        first = report["ready"][0]
        out.append("  next: task-station invoke --task %s --from %s --ask '<the request>'"
                   % (first, (parent.get("seq") if parent else "<orchestrator>")))
    elif report["stop"] == _loop.COMPLETE:
        out.append("  COMPLETE — every node is closed or has every exit condition met.")
    elif report["stop"] == _loop.WORKING:
        out.append("  WORKING — %s already running and nothing else is startable. "
                   "Nothing to do but wait; this is the loop functioning, not a stall."
                   % (", ".join("#%s" % s for s in report["running"]) or "children"))
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
    elif r.get("running"):
        tail = "  RUNNING"
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
    # An UNGRADED SESSION HANDOFF is appended rather than replacing the tail, because it is
    # orthogonal to why the node is or is not startable — a settled child can still owe
    # the gate a handoff verdict, and a tail that showed one instead of the other would
    # hide whichever it dropped. Only the ungraded count is shown: a handoff already graded
    # is history, and history belongs in the trail.
    if r.get("handoffs_ungraded"):
        tail += "  +%d ungraded handoff(s)" % r["handoffs_ungraded"]
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
        sys.exit(2)        # a population that could not be resolved is not "nothing to do"
    ran = bool(getattr(a, "run", False))
    if ran:
        # ONE slot for the whole sweep, not one per node: a sweep is a build. Locking
        # `exit-tick` and leaving this open would make the cap true only of the verb
        # somebody happened to remember.
        token, refused = _build_slot(a, "scan", "scan --run")
        if refused:
            sys.exit(3)
        try:
            for t in nodes:
                results = _exits.evaluate(t)
                if results:
                    _exits.apply_results(t, results)
                    t["updated_ts"] = _now()
                    save_task(t)
        finally:
            _loop.release_build_slot(token)
    every = all_tasks()
    by_id = {t.get("id"): t for t in every}
    nodes = [by_id.get(t.get("id"), t) for t in nodes]
    # The DEEP settled rule — a parent whose children are unbuilt is not settled, however
    # green its own checklist is. Built over the WHOLE store, not just the scanned nodes,
    # so a child sitting outside the scanned subtree still counts.
    report = _loop.scan(nodes, by_id.get, is_settled=_loop.settled_fn(every),
                        depths=depths, live=_live_seqs())
    if getattr(a, "as_json", False):
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
        return
    print("\n".join(_scan_lines(report, parent, ran)))


# ------------------------------------------------------------------ the invoke ----
#
# B10 — CHILD AS ATTACHED SESSION. The child is spawned ALREADY LINKED to its own task,
# so it reads its own context OUT OF THE RECORD, in one command, instead of out of a
# brief somebody wrote. That is what kills the lossy-brief boundary BY CONSTRUCTION
# rather than by discipline: there is no brief to get wrong, and the ask carries the
# REQUEST only.
#
# ATTACHMENT IS A POINTER, NOT A DELIVERY — #583, and this comment was one of the seven
# places that said otherwise. Nothing hands a child its digest: SessionStart prints the
# task's title and status, and that is the whole of it. So "the ask carries the request
# only" is only safe BECAUSE the launch prompt names the read (`_child_prompt`); without
# that line the same sentence describes a child with no context at all, which is correct
# reasoning from a false premise and is exactly how this defect survived seven sites.

# Past this many characters an ask has almost certainly stopped being a request and
# started being context. A WARNING, never a refusal: a legitimate request can be long,
# and refusing one would be this command inventing a rule nobody agreed to. The warning
# names the reason, which is the part that changes behaviour.
ASK_CONTEXT_HINT = 800

# The session id a `--dry-run` prints. A PREVIEW COSTS NOTHING, so it cannot mint a real
# one: a minted id is registered against the child and, for a preview nobody launches,
# becomes a phantom session that will never exist. An all-zero uuid is obviously a
# placeholder to a reader and is never linked to anything.
DRY_RUN_SID = "00000000-0000-0000-0000-000000000000"

# The trail marker for a launch handed to a human rather than opened by the loop. The
# parent's RUNNING column (3.6.0) exists to stop a double-invoke, and it cannot do that
# on a log where a preview and a launch wrote the identical line.
#
# DEFINED IN `turn`, READ HERE. The turn RECONCILES this trail against liveness (finding
# 5: a failed window-open still writes the event and still mints a session), so the writer
# and the reader must be the same string — two copies of it drift, and what drifts is
# whether a child gets re-launched or silently waited on forever.
MANUAL_LAUNCH = _turn.MANUAL_MARK

# The placeholder a report contract writes where the CHILD'S OWN task ref belongs, so a
# contract that names a command can name a runnable one. `loop.CLAIMS_CONTRACT` uses it
# for `claims --task <n>`. Substituted here and nowhere else: the role table is written
# once and read by every spawner, and a contract that resolved its own ref would need to
# know something only the invoke knows. When no ref is known the token is LEFT STANDING —
# a visible placeholder beats a wrong number, and beats a sentence that quietly drops the
# flag it was telling you to pass.
CHILD_REF_TOKEN = "<n>"


def _child_prompt(ask, role, report, ref=None):
    """The ask, plus the role's REPORT CONTRACT, plus THE RAIL THE REPORT TRAVELS ON.

    The contract travels in the prompt because a contract the child is never told about
    is decoration — and it is APPENDED, never substituted: the request is the one thing
    the orchestrator has to say that the child's own record cannot tell it. What gets
    RECORDED on the trail stays the bare ask (`_record_launch`), so a boilerplate
    sentence can never push the actual request out of the event text.

    THE RAIL IS NAMED BECAUSE NAMING NOTHING MADE THE TWO BEHAVIOURS IDENTICAL. The
    contract asked for a report and said nothing about where to put it, so a child that
    printed a perfect report to its own terminal was fully compliant and completely
    useless — the parent cannot see that window, and the session ends. Three children out
    of seven did exactly that on 2026-08-19. A memo is durable, survives the window
    closing, and lands on the record the gate already loads, so the rail is a memo and the
    prompt says so with the command. Added whenever a `ref` is known, contract or not: a
    role with no contract still has to hand something back.

    Takes the contract STRING, not the role spec: the role table is read once, by
    `workspace.resolve_spawn`, and this function only formats what it answered.

    `<n>` IN THE CONTRACT BECOMES THE CHILD'S TASK REF, so a contract that tells the
    child to run `claims --task <n>` hands it a command it can paste rather than one more
    thing to resolve on the way to doing what was asked. Only when a ref is known.

    THE READ IS NAMED FOR THE SAME REASON THE RAIL IS — #583. Attaching a child to a task
    delivers nothing: SessionStart prints that task's title and status, and every surface
    around this one used to claim the digest arrived with it. So a child was told its
    context had been handed over, told the ask deliberately withheld that context, and
    given no command to fetch it — three true-sounding sentences that together left it
    with nothing. It rides on the same `ref is not None` condition as the memo rail
    because both are the same bargain: the record is where the work comes from and where
    it goes back, and a child that is told neither is a brief boundary with extra steps."""
    report = str(report or "").strip()
    if report and ref is not None:
        report = report.replace(CHILD_REF_TOKEN, str(ref))
    out = ask
    if report:
        out = "%s\n\nREPORT BACK — the %s contract: %s" % (ask, role or "role", report)
    if ref is None:
        return out
    return ("%s\n\nREAD YOUR OWN RECORD FIRST — nothing was loaded into this session at "
            "start: `task-station search --detail %s` returns the goal, the open "
            "checklist and every live decision, and re-deriving any of it is waste.\n\n"
            "HAND IT BACK AS A MEMO ON YOUR OWN TASK — `task-station memo send "
            "--task %s --text '<the report>'`. That is where the gate reads it; a report "
            "in this window dies with the session." % (out, ref, ref))


def _invoke_command(base, role, model, permission_mode, ask, effort=None, ref=None):
    """Assemble the child's launch command from the pre-bound `cd … && claude
    --session-id <sid>` base.

    EVERY ROLE-DERIVED ANSWER COMES BACK FROM `workspace.resolve_spawn`, which
    `delegate` also asks — the rule about what a child is given lives in exactly one
    place, because when it lived in two they drifted, and the copy that drifted was
    handing unattended workers a mode that hangs them. This function's remaining job is
    assembling a shell line out of that answer.

    What the resolver decides, in short: a role may RESTRICT and may never REPLACE, so
    the permission mode is emitted only when it narrows what the child may do and is
    otherwise omitted to inherit the human's default; the model keeps the role's alias
    but reclaims the parent's `[1m]` window when the two name the same family; the tool
    grant is a DENY list, never an allow list, so it narrows the human's tool set instead
    of replacing it and dropping their MCP servers; and the effort is the role's own,
    emitted because the table carries it and the CLI takes it — a field the config board
    shows while nothing applies it is a lie told on every render. Any of them can be
    overridden explicitly, and an explicit value always wins.

    Read with `.get()`, not `[]`: `test_neither_path_answers_for_itself` stubs the
    resolver with a partial answer on purpose, and a spawner that only works against a
    complete dict is a spawner nobody can stub.

    The prompt is `shlex.quote`d rather than wrapped in quotes by hand — an ask
    containing an apostrophe is the common case, not an edge one, and a hand-quoted one
    would truncate at it."""
    r = _workspace.resolve_spawn(_workspace.SPAWN_WINDOW, role=role, model=model,
                                 permission_mode=permission_mode, effort=effort,
                                 parent_selection=g("claude_code_model_selection")())
    parts = [base]
    if r.get("model"):
        parts.append("--model %s" % shlex.quote(r["model"]))
    if r.get("effort"):
        parts.append("--effort %s" % shlex.quote(r["effort"]))
    if r.get("permission_mode"):
        parts.append("--permission-mode %s" % shlex.quote(r["permission_mode"]))
    if r.get("deny_tools"):
        parts.append("--disallowed-tools %s" % shlex.quote(",".join(r["deny_tools"])))
    parts.append(shlex.quote(_child_prompt(ask, role, r.get("report"), ref=ref)))
    return " ".join(parts)


# THE OTHER HALF OF THE PICKUP RAIL, AND THE ENGINE CANNOT PERFORM IT.
#
# The pickup gate delivers a hand-back at the parent's NEXT TURN END. That bounds the
# delay by the parent's own cadence rather than by when a human next asks, which is the
# whole fix — but if the parent has nothing to do it may not take a turn for a while, and
# the fastest honest edge is the harness's own: `SendMessage(notify_when_idle: true)` is a
# one-shot, opt-in subscription that fires exactly once when a named session next goes
# idle or exits. No polling, and a pure subscription (no `message`) costs the target
# session nothing. Proven on this machine on 2026-08-27, waiting on a peer before a heal.
#
# ONLY THE MODEL CAN CALL IT. It is a harness tool, not a shell command, so no engine
# function can emit it — which is exactly why this is a printed instruction rather than a
# feature. Adopt, do not rebuild: the engine spawns and records, the harness notifies.
#
# AND THE NOTICE IS A PROMPT TO LOOK, NOT PROOF OF ANYTHING. `idle` means the session
# finished a turn, and a child that pauses mid-work is idle too — so a subscription alone
# would report a thinking child as finished, which is the same lie liveness always told,
# arriving faster. The two halves compose in one direction only: the harness says WHEN to
# look, and the record — the pickup, the report memo, the exit conditions — says WHETHER
# anything actually landed. The wording below says so, because a reader who takes the
# notice as the answer has re-created the bug.

def _subscribe_lines(child, sid):
    """The two lines that tell the spawning MODEL to subscribe to this child's idle edge.

    Printed with every real launch, addressed to whoever is reading the invoke output —
    which is a model with the tool, not a shell."""
    ref = child.get("seq") or (child.get("id") or "")[:8]
    return [
        "  wake yourself when it stops (harness rail — do this now, in one tool call):",
        "    SendMessage(to: \"<its name from ListAgents>\", notify_when_idle: true)  "
        "— omit `message`: a pure subscription costs it nothing, fires ONCE, and needs "
        "no polling.",
        "    Its window is titled #%s. The notice means LOOK, not done — a child that "
        "pauses mid-turn is idle too. `task-station turn --task %s` says whether anything "
        "landed, and a real hand-back also files a pickup your own Stop gate will not let "
        "you past." % (ref, ref),
    ]


def _record_launch(child, orch, role, ask, manual, session=None, forced=None):
    """Write the invoke's trail entries — AFTER the launch decision, never before it.

    Both halves of this used to run before the code knew whether a window had opened, so
    a preview and a real launch wrote the same line and one child read as two invokes.
    The kind of launch is therefore part of the record: only a window that actually
    opened is an `invoked #…`, and everything else — `--print-command`, or a window
    opener that failed and fell back to printing — is a MANUAL LAUNCH, because in both
    cases the thing that happens next is a human running the line by hand."""
    child = load_task(child["id"]) or child
    who = orch.get("seq") if orch else "?"
    as_role = " as %s" % role if role else ""
    if manual:
        detail = "%s — handed to a human by #%s%s" % (MANUAL_LAUNCH, who, as_role)
    else:
        detail = "invoked by #%s%s" % (who, as_role)
    add_event(child, "child", "%s: %s" % (detail, ask[:160]), session=session)
    child["updated_ts"] = _now()
    save_task(child)
    if not orch:
        return
    orch = load_task(orch["id"]) or orch
    head = "%s #%s" % (MANUAL_LAUNCH, child.get("seq")) if manual \
        else "invoked #%s" % child.get("seq")
    # A budget override is recorded on the ORCHESTRATOR, which is where the budget is:
    # a deliberate override is sometimes right, an invisible one never is.
    add_event(orch, "child", "%s (%s)%s — %s"
              % (head, role or "no role", (" %s" % forced) if forced else "",
                 ask[:160]), session=session)
    orch["updated_ts"] = _now()
    save_task(orch)


def cmd_invoke(a):
    """`task-station invoke --task CHILD --from ORCH --ask '<the request>'`

    Spawn a session that is ALREADY ATTACHED to the child task, carrying the request and
    nothing else. The child's context comes from its own record via the hooks; the
    orchestrator writes no brief, which is the point — every brief is a lossy copy of a
    digest that already exists.

    `--role` picks the child's model from the role table (scout / implementer / reviewer
    / grader) and its permission mode only when that mode RESTRICTS; `--model` and
    `--permission-mode` override it. With neither, the child inherits the harness
    defaults.

    `--dry-run` prints the command it WOULD run and writes nothing at all — no session,
    no event, no trust file. `--print-command` is not that: it is a real launch the
    human completes by hand, so it pre-attaches the session and records itself as a
    MANUAL LAUNCH."""
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
              % (role, ", ".join(sorted(_loop.roles()))))
        sys.exit(2)
    warnings = []
    # THE CHILDREN CAP, ENFORCED HERE — before a session is minted, an event written or
    # a window opened, so a refusal leaves nothing behind that looks invoked. Exit 3
    # rather than 2, deliberately: 2 means "you asked wrong" and asking again will not
    # help, 3 means the budget is full and this is worth retrying when a child finishes.
    # LIVENESS THAT COULD NOT BE READ IS NOT "NOTHING IS RUNNING". `_live_seqs()` fails
    # open to an empty set, which is right for the scan's display column and catastrophic
    # here: it tells the budget no children are live, so a sessions-dir hiccup silently
    # lifts the cap and this spawns into a machine it cannot see. Refuse instead, with the
    # same retryable exit 3 the full-budget path uses — the condition is transient.
    live = _live_seqs_or_none()
    if live is None and not getattr(a, "force", False):
        print("invoke: cannot determine which child sessions are RUNNING, so the "
              "children cap cannot be enforced.\n"
              "Refusing rather than spawning blind — an unreadable sessions directory "
              "looks identical to an idle machine, and guessing wrong launches over the "
              "cap.\n"
              "Retry in a moment, or pass --force to launch anyway and have that recorded.")
        sys.exit(3)
    budget = _loop.children_budget(orch, all_tasks(), live or set())
    if budget["over"]:
        lines = ["invoke: #%s already has %d child session(s) RUNNING (%s) and "
                 "loop_children_max is %d."
                 % (orch.get("seq") if orch else "?", len(budget["running"]),
                    ", ".join("#%s" % s for s in budget["running"]), budget["max"]),
                 "The cap is machine-scoped, not per-task — two orchestrators share one "
                 "machine, and a per-task cap would let them sum to a load neither "
                 "asked for.",
                 "Wait for one to finish (`task-station scan --task %s` names them), "
                 "raise it (`loop_children_max` in config.json, or "
                 "TASK_STATION_LOOP_CHILDREN_MAX), or pass --force to launch over it "
                 "and have that recorded."
                 % (orch.get("seq") if orch else "<orch>")]
        if not getattr(a, "force", False):
            print("\n".join(lines))
            sys.exit(3)
        warnings.append("launched OVER the cap: %d child session(s) already running and "
                        "loop_children_max is %d." % (len(budget["running"]),
                                                      budget["max"]))
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
    model = getattr(a, "model", None)
    permission_mode = getattr(a, "permission_mode", None)
    effort = getattr(a, "effort", None)
    cwd = getattr(a, "cwd", None)
    header = "Invoke — #%s %s%s" % (child.get("seq"), child.get("title"),
                                    ("  ← #%s" % orch.get("seq")) if orch else "")
    # WHERE THE CHILD STARTS, resolved ONCE and used by every path below — the preview,
    # the guard, the roster entry and the command. It used to be derived three times
    # (once in the preview, once inside `fresh_resume_command`, once in the `--cwd`
    # override), which is how a roster entry could record one directory while the window
    # opened in another.
    explicit = bool(cwd)
    where = os.path.expanduser(cwd) if explicit \
        else _fresh_session_cwd(child.get("session_meta"))
    # The workspace verdict is reached BEFORE anything is written, so the preview and
    # the real run report the same finding — one of them just stops here.
    verdict = _workspace.assess(cwd)

    # A DEFAULT IS A GUESS, AND A GUESS GETS CHECKED. An explicit `--cwd` is a human
    # naming a directory and is left alone (the trust guard still reports on it, and
    # `test_a_refusal_does_not_stop_the_invoke` pins that a refusal there does not block
    # the launch). A DEFAULT is this command's own inference, and inferring a directory a
    # child cannot start in is how #570 happened: the child dies at zero turns, its dead
    # entry becomes the next default, and the retry dies identically. Refusing costs one
    # flag; not refusing costs a run of phantom child failures. Exit 3, not 2 — the ask
    # was fine, the inference was not, and naming a --cwd makes the same command work.
    if not explicit:
        spawn = _workspace.spawnable(where)
        if not spawn["ok"]:
            print(header)
            for line in _workspace.spawn_refusal_lines(
                    spawn, inherited=(where != os.getcwd())):
                print(line)
            sys.exit(3)

    if getattr(a, "dry_run", False):
        # A PREVIEW MUST COST NOTHING. No session minted, no event on either task, no
        # trust file touched, no window. `--print-command` is a REAL launch path (the
        # human runs the printed line, so it legitimately pre-attaches a session); this
        # is the path for merely LOOKING, which previously did not exist.
        base = "cd %s && claude --session-id %s" % (shlex.quote(where), DRY_RUN_SID)
        cmd = _invoke_command(base, role, model, permission_mode, ask, effort,
                              ref=child.get("seq"))
        print(header)
        print("  DRY RUN — nothing was written: no session minted, no event recorded, "
              "no window opened. That session id is a placeholder, not a real one.")
        for w in warnings:
            print("  note: %s" % w)
        for line in _workspace.lines(verdict, dry=True):
            print(line)
        print("  it would run:")
        print("    %s" % cmd)
        return

    sid, base = fresh_resume_command(child, preborn=True, cwd=where)
    base = "cd %s && claude --session-id %s" % (shlex.quote(where), sid)
    done = _workspace.apply(verdict)
    cmd = _invoke_command(base, role, model, permission_mode, ask, effort,
                          ref=child.get("seq"))
    print(header)
    print("  session %s is pre-attached, which is a POINTER and not a delivery: nothing "
          "is loaded into it, so the ask carries the request only and its launch prompt "
          "tells it to run `task-station search --detail %s` first."
          % (sid[:8], child.get("seq") or child["id"][:8]))
    for w in warnings:
        print("  note: %s" % w)
    for line in _workspace.lines(verdict, done):
        print(line)
    # A hand-off to a human is a MANUAL LAUNCH — including the fallback below, which is
    # one in every respect that matters. The trail is written from what actually
    # happened, so the events come after this decision rather than before it.
    manual = True
    if getattr(a, "print_command", False):
        print("  run it yourself:")
        print("    %s" % cmd)
    elif g("_open_jump_window")(cmd):
        # THE OPENER RETURNING IS NOT A SESSION COMING UP. It reports that a command was
        # ISSUED; a terminal that refused, a `claude` that died at startup, or a trust
        # dialog nobody answered all return the same True. An orchestrator that believes a
        # child launched then waits on an idle rail for a session that never existed, and
        # the rail cannot tell that apart from one that has simply not fired yet. So the
        # claim is made only once `_await_registration` — the check `task-station
        # sessions` performs — says the session is running. FAIL-CLOSED: cannot-tell
        # prints the command, because "a window opened" is the sentence that was the
        # defect. Same helper, same wording shape as `relay --spawn` (#603), because two
        # spawners telling the truth differently is a third bug.
        if _await_registration(sid):
            manual = False
            print("  opened a new window and CONFIRMED session %s is running (this one "
                  "is untouched):" % sid[:8])
            print("    %s" % cmd)
        else:
            waited = int(_registration_timeout())
            print("  PREPARED ONLY — the window opener returned, but no session %s "
                  "registered %s, so this command cannot say a window opened. Run it "
                  "yourself (or check `task-station sessions` if you believe one came "
                  "up late):"
                  % (sid[:8], ("within %ds" % waited) if waited > 0 else "at all"))
            print("    %s" % cmd)
    else:
        print("  could not open a window (macOS/Terminal only) — run it yourself:")
        print("    %s" % cmd)
    for line in _subscribe_lines(child, sid):
        print(line)
    forced = ("FORCED over the cap (loop_children_max=%d, %d running)"
              % (budget["max"], len(budget["running"]))) if budget["over"] else None
    _record_launch(child, orch, role, ask, manual, session=getattr(a, "session", None),
                   forced=forced)


# ------------------------------------------------------------------- the relay ----
#
# THE ONE LINE THAT SEPARATES THIS FROM `invoke`: the successor is pre-attached to the
# SAME task. Everything else is the same substrate on purpose — `fresh_resume_command`'s
# pre-bound session id, the workspace trust pass, the window opener, and the MANUAL LAUNCH
# distinction — because a second spawner would be a second set of the bugs 3.7.0 just
# finished fixing in the first one.
#
# NO ORCHESTRATOR REFUSAL HERE, deliberately. `invoke` and `delegate` refuse to run work
# from an orchestrator-only task because the WORK belongs to a child. A relay moves no
# work anywhere: an orchestrator's own planning session fills up like any other, and
# telling it to hand its planning to a child would be the guard firing on the one case it
# was never about.


def _predecessor_label(task, session):
    """The OUTGOING session's own ordinal — `444-32` — or its bare id when no roster can
    name it, or None when the caller supplied no session at all.

    NONE, NOT `"?"`, and that is a live defect rather than tidying. `relay` runs without
    `--session` whenever the caller has no session id to pass, and the prompt built from
    the old sentinel opened `you are session 444-34, succeeding ?` — session 444-34's own
    launch prompt on 2026-09-03. It reads as a broken interpolation, which is a worse
    thing to hand a successor than silence: a name it cannot resolve invites it to go
    looking for one. `continuation_prompt` already omits the clause on a falsy
    predecessor, so answering None deletes the sentence instead of filling it with a
    question mark, and the surfaces that must name SOMEBODY say so in words.

    RESOLVED ON THE SESSION'S OWN TASK, NOT ON THE TASK BEING HANDED OFF, and that is the
    whole of #599's second defect. `ordinal_label` is per-task by construction: it reads
    `task["session_meta"][sid]["ordinal"]` and answers None for a session that task has
    never seen. A relay usually hands one task to itself, so the two tasks are the same
    one and the lookup worked — but `relay --task <other>` hands a NEW task to a session
    that is rostered on the OLD one, and there the successor's ordinal resolved (`600-0`,
    minted on the target) while the predecessor's did not. On 2026-08-31 the prompt read
    `you are session 600-0, succeeding 29c54f8c` — one sentence naming the same kind of
    thing two different ways, the second of them unreadable. The predecessor was `444-32`,
    and `get_link` knew it: that session's link pointed at #444 the whole time.

    THE UUID FALLBACK STAYS, and stays LAST. A session with no link, or one rostered as a
    worker (workers carry a descriptive name and never an ordinal, by design), genuinely
    has no ordinal — and a made-up one would be worse than an unreadable true one."""
    if not session:
        return None
    label = ordinal_label(task, session)
    if label:
        return label
    try:
        owner_id = get_link(session)
        owner = load_task(owner_id) if owner_id and owner_id != task.get("id") else None
    except Exception:                                   # noqa: BLE001
        owner = None
    if owner:
        label = ordinal_label(owner, session)
        if label:
            return label
    return session[:8]


# WHERE THE HUB IS. A directory, not a role: the session that coordinates a programme
# runs from the home directory, and every repo worktree it invokes children into is
# somewhere below it. There is no config key for this because there is nothing to tune —
# `~` is where a shell starts, and a station that wanted something else would be naming
# a second hub nobody else's tooling knows about.
HUB_CWD = "~"


def _successor_cwd(task, cwd):
    """Where the successor's window opens, resolved ONCE for the mint, the roster entry
    and the command.

    AN ORCHESTRATOR'S SUCCESSOR STARTS AT THE HUB. The default is otherwise inherited
    from wherever the predecessor last ran, which is right for a leaf task — that IS
    where the work lives — and wrong for an orchestrator, which holds no work by
    construction (`loop.is_orchestrator`, and the refusal that guards `delegate`). On
    #503 the inherited directory was a BRANCH WORKTREE belonging to one of its children,
    so the successor woke inside a repo it had no business editing, one `git commit`
    away from writing a coordinator's notes onto a child's branch.

    An explicit `--cwd` always wins: a human naming a directory is not a guess, and this
    function only replaces the guess."""
    if cwd:
        return os.path.expanduser(cwd)
    if _loop.is_orchestrator(task):
        return os.path.expanduser(HUB_CWD)
    return _fresh_session_cwd(task.get("session_meta"))


def _registration_timeout():
    """How long `--spawn` waits for the successor to register. Read through the same
    module that performs the check, so the wait and the check cannot drift."""
    try:
        import live_sessions
        return live_sessions.registration_timeout()
    except Exception:                                   # noqa: BLE001
        return 0.0


def _await_registration(sid):
    """True when session `sid` actually came up — the check `task-station sessions`
    performs, asked here so the claim printed above it is a report rather than a hope.

    FAIL-CLOSED, unlike `_live_seqs`. A scan that cannot read process state is better
    off printing the plan without a liveness column; a spawner that cannot read it must
    NOT print "a window opened", because that sentence is the whole defect. Cannot-tell
    lands in the PREPARED branch, which prints the command a human can run."""
    try:
        import live_sessions
        return bool(live_sessions.await_registration(sid))
    except Exception:                                   # noqa: BLE001
        return False


def cmd_relay(a):
    """`task-station relay [--task REF] [--spawn]`

    Bare, this is the REPORT: where this session stands, what the policy says to do, and
    what still blocks a handoff. It writes nothing at all — no session minted, no event,
    no field touched. `invoke` needed a `--dry-run` flag to offer that; here the preview
    is the DEFAULT and the flag is what opens a window, which is the right way round for a
    verb whose whole job is to end the session that typed it.

    `--spawn` performs the handoff: mint a session pre-attached to THIS task, launch it
    with the generated continuation prompt, CONFIRM the successor actually registered,
    and only then record the handoff on the ledger the gate grades. It REFUSES a verdict
    of keep-going or unknown, and refuses a record that cannot carry a handoff, naming
    the gaps. `--force` overrides both — sometimes the right call at 95% — and is
    recorded as forced with its blockers, because a forced handoff that nobody could see
    afterwards makes G1 ungradeable.

    WHAT IT REPORTS IS WHAT HAPPENED. The window opener returning 0 means the command
    was ISSUED; it is `task-station sessions`' own check — a live process carrying that
    session id — that says a window opened. Unconfirmed, this says it PREPARED the
    command, prints it, and writes NO handoff: a ledger entry naming a session that
    never existed cannot be told apart later from a real one.

    EXIT CODES: 0 done (or reported) · 2 the command was wrong · 3 refused, with the
    reason printed."""
    task, err = _loop_target(a, "relay")
    if err:
        print(err)
        sys.exit(2)
    session = getattr(a, "session", None)
    spawn = bool(getattr(a, "spawn", False))
    # The report is a READ and never refuses; only a spawn needs a live task to hand to.
    if spawn and is_closed(task):
        print("relay: task #%s is closed. There is nothing to hand off — reopen it first "
              "(`/todo %s`)." % (task.get("seq"), task.get("seq")))
        sys.exit(2)
    rep = _succ.report(task, measure_context_tokens(session),
                       effective_context_window(session), session=session)
    ref = task.get("seq") or task["id"][:8]
    if getattr(a, "as_json", False) and not spawn:
        print(json.dumps(rep, indent=2, sort_keys=True, default=str))
        return
    print("Relay — #%s %s" % (ref, task.get("title")))
    for line in _succ.report_lines(rep):
        print(line)
    if not spawn:
        print("  nothing was written — this is the report. `relay --spawn` hands off.")
        return

    force = bool(getattr(a, "force", False))
    if rep["verdict"] not in (_succ.RELAY,) and not force:
        print("  REFUSED: the verdict is %s, not %s. %s"
              % (rep["verdict"], _succ.RELAY,
                 "Pass --force if you have a reason the numbers cannot see."))
        sys.exit(3)
    if not rep["ready"] and not force:
        print("  REFUSED: the record cannot carry a handoff yet — the successor would "
              "lose the items above. Close them (`/todo save`), or --force and accept a "
              "degraded handoff that says so in its own prompt.")
        sys.exit(3)
    # `forced` is narrower than the FLAG: passing --force on a relay that needed nothing
    # overridden is not a forced handoff, and recording it as one would put a finding in
    # front of a grader that has nothing behind it.
    blockers = list(rep["blockers"])
    forced = force and (rep["verdict"] != _succ.RELAY or bool(blockers))

    predecessor = _predecessor_label(task, session)
    cwd = getattr(a, "cwd", None)
    where = _successor_cwd(task, cwd)
    # Mint FIRST: the successor's own ordinal is assigned here, and the prompt names it.
    # THE MINT CARRIES THE DIRECTORY rather than deriving a second one. The roster entry
    # this writes is what the NEXT spawn reads as its default, so an entry recording one
    # directory while the window opens in another re-seeds the exact propagation the
    # `--cwd` override was added to escape — the same defect `invoke` resolved by
    # computing `where` once and handing it to every path.
    sid, base = fresh_resume_command(task, preborn=True, cwd=where)
    base = "cd %s && claude --session-id %s" % (shlex.quote(where), sid)
    task = load_task(task["id"]) or task
    # THE ORDINAL IS KEPT, NOT JUST COLLAPSED. It was already resolved here to name the
    # successor in the prompt; the handoff's FILENAME is built from the same value, so it
    # is held separately rather than folded into the `or sid[:8]` fallback. One lookup,
    # two uses — a second resolution could disagree with the first.
    ordinal = ordinal_label(task, sid)
    successor = ordinal or sid[:8]
    prompt = _succ.continuation_prompt(task, rep=rep,
                                       blockers=blockers if force else None,
                                       predecessor=predecessor, successor=successor)
    # THE HANDOFF IS A FILE AND THE LAUNCH ARGUMENT IS A POINTER TO IT.
    #
    # Every cap `succession` used to carry existed because this command put the whole
    # handoff in an argv string, where a length limit bites: it was reported cut
    # mid-sentence four times and every fix re-cut the same string. A successor told to
    # READ A FILE has no budget to overrun and nothing to truncate.
    #
    # A WRITE THAT FAILS REFUSES, and does not fall back to the argv it just replaced.
    # With the caps gone that fallback would push a whole handoff — 27,891 characters,
    # measured on #444 — through argv and let the kernel decide where it ends. Refusing
    # costs one command to retry; a successor spawned on a truncated prompt looks like
    # it worked. Nothing is launched and no handoff is recorded on this path.
    #
    # THE FILE IS NAMED AFTER THE SUCCESSOR, which is why the mint happens first and why
    # both the successor's session id and its ROSTER NUMBER are passed here. Two relays on
    # one task used to write one path — `<seq>-CONTINUATION.md`, opened `"w"` — so the
    # second silently replaced a handoff the first successor had not read yet. It reads
    # minutes later, in its own process, after its own SessionStart, so no ordering here
    # could have closed that window. The STABLE per-task name survives as a pointer, and
    # it moves further down: only once a session is confirmed.
    #
    # THE NUMBER IS HOW THE SUCCESSOR IS SPELLED, not a second identity. `444-36` keys the
    # file on the same session `444-be0202bd` did, in the notation every other surface on
    # this board already prints — so a human can read it, sort it, and match it against
    # the roster. Ordinals are per task and never reused, so per-successor uniqueness is
    # untouched. `handoff_name` falls back to the session-blind form when there is no
    # number to spell, and `form` is what lets the next lines SAY which one was written.
    try:
        handoff_path, form = _succ.write_handoff(task, prompt, sid, label=ordinal)
    except OSError as e:
        print("  REFUSED: the handoff could not be written (%s). The successor is "
              "launched with a POINTER to that file, so there is nothing to hand it — "
              "and the old fallback of passing the whole handoff as a command-line "
              "argument is what this replaced. Session %s was minted and NOT launched; "
              "fix the path and re-run `relay --spawn`."
              % (e, sid[:8]))
        sys.exit(3)
    # A NAME THAT LOST ITS NUMBER IS REPORTED, never emitted quietly. `ordinal_label`
    # returns None for a worker or an unrostered session, and the fallback name is still
    # unique and still per-successor — but it is not a number a human can match against
    # the roster, so the human is told that is what they got. This is the rule
    # `session_title_label`'s own docstring already sets for this codebase, applied
    # rather than reinvented.
    if form != _succ.ORDINAL_FORM:
        print("  NAMED BY SESSION ID, not by roster number: no ordinal could be resolved "
              "for session %s on this task (workers have none by design), so the handoff "
              "is `%s` rather than `<seq>-<n>-CONTINUATION.md`. Still one file per "
              "successor — just not a name that sorts or matches the roster."
              % (sid[:8], os.path.basename(handoff_path)))
    # THE ARGUMENT STAYS SHORT BY CONSTRUCTION. It is not a summary of the handoff —
    # a second copy of the record is the thing this module exists not to make — it is
    # the one instruction that reaches the file, so it cannot itself go stale or be cut.
    #
    # AND THE PATH IT NAMES IS NAMED AFTER THE READER, which the sentence says out loud
    # because that is what makes it checkable — and it is now checkable BY EYE: the
    # successor is told it is `444-36` and handed `444-36-CONTINUATION.md`, one string
    # matching in two places in one sentence, where an eight-hex-character id was a thing
    # a reader had to trust rather than read.
    named = ("named %s, after the roster number you were just given"
             if form == _succ.ORDINAL_FORM
             else "named %s, after your own session id") % os.path.basename(handoff_path)
    pointer = ("RELAY on task #%s — you are session %s. Read %s FIRST, in full: it is "
               "your handoff, %s and written by your predecessor, and nothing else is "
               "loaded for you. Then read the record it points at."
               % (ref, successor, handoff_path, named))
    # THE WORKSPACE WRITES COME AFTER THE HANDOFF, because they are writes. Granting a
    # directory trust for a successor this command then refuses to launch would leave a
    # permission behind for a session that never existed.
    verdict = _workspace.assess(cwd)
    done = _workspace.apply(verdict)
    # THE SUCCESSOR RUNS THE SAME MODEL, full selection string and all. A relay continues
    # one piece of work, so there is no role to consult and nothing to choose — and the
    # `[1m]` marker has to survive, because handing a successor a 200k window to finish
    # work started in a 1M one is the same unasked-for downgrade `invoke` refuses to make.
    model = getattr(a, "model", None) or g("claude_code_model_selection")()
    parts = [base]
    if model:
        parts.append("--model %s" % shlex.quote(model))
    parts.append(shlex.quote(pointer))
    cmd = " ".join(parts)
    # WHAT THIS LINE SAYS IS WHAT THE SUCCESSOR ACTUALLY GETS, which is now one hop
    # longer than it was: the launch argument names the FILE, and the file names the
    # record. #583 was this line claiming a delivery nothing performed, so it names the
    # path it wrote rather than describing the shape of the handoff.
    print("  session %s (%s) is pre-attached to THIS task — no child, no new record. "
          "Nothing is loaded into it: the launch argument points it at %s — the whole "
          "handoff, written just now — and that file points at `task-station search "
          "--detail %s`, the same record you have been working from."
          % (sid[:8], successor, handoff_path, ref))
    for line in _workspace.lines(verdict, done):
        print(line)
    # PREPARED VERSUS HANDED OFF, and the difference is a measurement rather than a
    # hope. `_open_jump_window` returning True says the opener exited 0 — the command
    # was ISSUED. Whether a session came up is a separate question, and
    # `_await_registration` asks the one `task-station sessions` answers.
    manual = True
    confirmed = False
    if getattr(a, "print_command", False):
        print("  PREPARED — nothing has been handed off yet. Run it yourself:")
        print("    %s" % cmd)
    elif g("_open_jump_window")(cmd):
        confirmed = _await_registration(sid)
        if confirmed:
            manual = False
            print("  opened the successor's window and CONFIRMED session %s is running "
                  "(this one is untouched):" % sid[:8])
            print("    %s" % cmd)
        else:
            # The wait is named only when there WAS one. A zero here means the check
            # itself could not run, and "registered within 0s" would read as a
            # measurement of the successor rather than of this command's own blindness.
            waited = int(_registration_timeout())
            print("  PREPARED ONLY — the window opener returned, but no session %s "
                  "registered %s, so this command cannot say a window opened. Run it "
                  "yourself (or check `task-station sessions` if you believe one came "
                  "up late):"
                  % (sid[:8], ("within %ds" % waited) if waited > 0 else "at all"))
            print("    %s" % cmd)
    else:
        print("  could not open a window (macOS/Terminal only) — run it yourself:")
        print("    %s" % cmd)
    # A HANDOFF IS A CLAIM ABOUT A SESSION, so it is written only once that session
    # exists. Recorded on intent, it named a successor that had never run — and a later
    # reader cannot tell that entry from a real one, which is strictly worse than an
    # absent entry: the ledger is what the gate grades, and a phantom handoff is a
    # handoff nobody can grade honestly.
    #
    # RELOADED FIRST because the wait above is real time, during which the successor has
    # attached and stamped its own roster entry. Saving the copy minted before the poll
    # would drop that.
    entry = index1 = None
    linked = link_note = moved_aside = None
    if confirmed:
        task = load_task(task["id"]) or task
        entry, index1 = _succ.record_handoff(task, session, sid, rep, forced=forced,
                                             blockers=blockers)
        # THE STABLE POINTER MOVES ONLY HERE, FOR THE SAME REASON THE LEDGER ENTRY DOES.
        # `<seq>-CONTINUATION.md` is the path pinned decision 444:658 tells a cold
        # session to open by hand, so it has to keep resolving — and what it resolves to
        # is a CLAIM ABOUT A SESSION. Moved before this branch it would name a successor
        # that never ran on every `--print-command` and every failed opener, which is
        # the phantom the sentence above refuses for the ledger.
        #
        # A POINTER THAT CANNOT BE MADE IS A NAMED SKIP, never a second copy and never a
        # failed relay. The per-successor file is already written and the successor's
        # launch argument already names it, so the whole cost of a missing pointer is
        # one `ls` for a human; copying the handoff there instead would put two files on
        # disk with one origin, which is the staleness the per-successor name removed.
        try:
            linked, moved_aside = _succ.link_handoff(task, handoff_path)
        except OSError as e:
            link_note = str(e)
    # THE TRAIL HAS TO NAME BOTH SIDES, so where the prompt drops an unresolvable
    # predecessor the durable event says in words that there was not one to name.
    from_who = predecessor or "an unidentified session"
    head = "relay %s → %s" % (from_who, successor) if confirmed \
        else "%s — relay %s → %s PREPARED, no successor confirmed" \
             % (MANUAL_LAUNCH, from_who, successor)
    # THE EVENT SAYS WHAT WAS MEASURED, and says so only when something was. This line is
    # the durable one — the report scrolls away and the history entry does not — so an
    # invented `~0% of a 1000k window` here outlives every other copy of the same lie.
    occ = ("occupancy unknown (not measured), %dk window" % (rep["window"] // 1000)
           if rep["used_pct"] is None
           else "~%d%% of a %dk window" % (rep["used_pct"], rep["window"] // 1000))
    add_event(task, "child", "%s (session %s) — %s%s"
              % (head, sid[:8], occ,
                 ", FORCED past %d gap(s)" % len(blockers) if forced else ""),
              session=session)
    task["updated_ts"] = _now()
    save_task(task)
    if index1 is not None:
        print("  handoff #%d recorded — the parent grades it like any other child work "
              "(`grade --task %s --handoff %d --dim G1=… …`)." % (index1, ref, index1))
    else:
        print("  NO handoff was recorded — a handoff is a claim about a session, and "
              "none has registered. The session id above is minted and linked, so the "
              "command still works when a human runs it; what is withheld is a ledger "
              "entry claiming a handoff that has not happened.")
    # WHAT THE STABLE NAME NOW POINTS AT, and where it does not, said out loud. A skip
    # that nobody printed would leave a human typing the path from 444:658 at a name
    # that resolves to an older handoff — or to nothing — with no way to learn why.
    if linked:
        print("  %s now points at it — that is the stable name a human types, and it "
              "resolves to the newest handoff rather than storing one." % linked)
    # A FILE MOVED UNDER A HUMAN IS SAID OUT LOUD. Before 3.62.0 the stable name held a
    # real handoff, and every task that has relayed still has one there; it is the only
    # copy of that handoff, so it is renamed rather than replaced. Printing where it went
    # is what makes the rename findable without asking.
    if moved_aside:
        print("  the handoff that was at that name — written before the pointer existed, "
              "and the only copy of itself — is now %s. Nothing was overwritten."
              % moved_aside)
    elif link_note:
        print("  the stable name %s was NOT updated (%s) — SKIPPED, not copied. The "
              "successor is pointed at %s, which is written; a human reading by hand "
              "wants the newest file in that directory."
              % (_succ.stable_handoff_path(task), link_note, handoff_path))
    if forced:
        print("  FORCED, and the record says so — the grader sees the gaps you overrode.")


# ------------------------------------------------------------------- the grade ----

def cmd_grade(a):
    """`task-station grade --task CHILD --dim G1=A --dim G2=A- … [--park REASON --why W]`

    Record one pass of the graded acceptance gate on a child task. The ENGINE does the
    arithmetic and the recording; the JUDGMENT — what grade each dimension earns — is
    the skill's, and no flag here can supply it.

    ACCEPTANCE IS PER-DIMENSION, not an average. A rubric averaged
    into one number lets a failed gate-integrity dimension hide behind five strong ones,
    which is the exact failure six separate dimensions exist to prevent. An UNGRADED
    dimension is not a pass either — it is work the grader has not done.

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
    # `--handoff N` grades a SESSION HANDOFF rather than the task's work. Same rubric,
    # same threshold, same verb — the link is only so a task that relayed three times can
    # say which verdict judged which handoff. Validated BEFORE anything is recorded: a
    # grading filed against a handoff that does not exist would be a verdict about
    # nothing, and it would still burn an attempt.
    handoff = getattr(a, "handoff", None)
    if handoff is not None:
        ledger = _succ.handoffs(task)
        if not ledger:
            print("grade --handoff %s: task #%s has recorded no session handoff. "
                  "A handoff is written by `relay --spawn`; there is nothing here to "
                  "grade." % (handoff, task.get("seq")))
            sys.exit(2)
        if not 1 <= int(handoff) <= len(ledger):
            print("grade --handoff %s: task #%s has %d handoff(s), numbered 1-%d."
                  % (handoff, task.get("seq"), len(ledger), len(ledger)))
            sys.exit(2)
    note = getattr(a, "note", None) or getattr(a, "why", None)
    entry, v = _loop.record(task, dims, threshold, note=note,
                            session=getattr(a, "session", None), park=park,
                            handoff=handoff)
    retry_max = _config.loop_retry_max()
    left = _loop.retries_left(task, retry_max)
    ref = task.get("seq") or task["id"][:8]
    line = ("PARKED (%s) — %s" % (park, note)) if park else _loop.verdict_line(v)
    if not getattr(a, "no_decision", False):
        # DECLARED, NOT INFERRED, and inference-free by construction: a gate grade is
        # dimension scores against a threshold taken at one moment, which is a
        # MEASUREMENT. The writer knows what it is writing, so nothing here is a guess —
        # this is the whole reason the machine writers could declare before any human did.
        append_decision(task, "Gate %s: %s%s"
                        % (ref, line,
                           (" — %s" % note) if note and not park else ""),
                        session=getattr(a, "session", None),
                        kind=_dec.KIND_MEASUREMENT)
    # THE VERDICT GOES BACK DOWN THE RAIL, not just into the ledger. A rejection recorded
    # on the task and nowhere else is a rejection the child never reads: the child is a
    # session nobody types into again, and by gate time it has usually stopped. A memo is
    # durable, it survives the window closing, and it is on the record the child's own
    # SessionStart reads — so a retry starts from the verdict instead of from nothing.
    # ROUTINE=FALSE deliberately: this is a judgement aimed at that session, not
    # bookkeeping, so it may hold a running child's turn end.
    memo = None
    if not getattr(a, "no_memo", False):
        text = (_turn.park_memo(park, note, ref=ref) if park
                else (None if v["accepted"]
                      else _turn.rejection_memo(v, ref=ref, note=note)))
        if text:
            memo = memo_send(task, text, from_sid=getattr(a, "session", None))
    task["updated_ts"] = _now()
    save_task(task)
    if getattr(a, "as_json", False):
        print(json.dumps({"task": ref, "entry": entry, "verdict": v,
                          "attempts": _loop.attempts(task),
                          "memo": (memo or {}).get("id"),
                          "retries_left": left}, indent=2, sort_keys=True,
                         default=str))
    else:
        print("Gate — task #%s %s%s" % (ref, task.get("title"),
                                        ("  · handoff #%s" % handoff)
                                        if handoff is not None else ""))
        # The handoff's mechanical evidence is printed WITH the verdict, so the record of
        # what was graded and the grade itself are one artefact. Otherwise "G1=A" on a
        # forced handoff is a claim with nothing beside it. `evidence`, not `line` — the
        # verdict line is already bound above and shadowing it here printed the last
        # evidence row where the verdict belonged.
        if handoff is not None:
            for evidence in _succ.handoff_evidence_lines(task, handoff):
                print(evidence)
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
        if memo:
            print("  sent as memo %s on task #%s — the child reads the verdict off its "
                  "own record, not off a window that has closed."
                  % ((memo.get("id") or "")[:8], ref))
    if park:
        sys.exit(4)
    if v["accepted"]:
        return
    sys.exit(1 if left else 3)


# ---------------------------------------------------- the orchestrator guard ----

def cmd_turn(a):
    """`task-station turn [--task ORCH] [--ask '<request>'] [--json]`

    ONE PASS OF THE LOOP, as an ordered agenda: scan -> invoke -> mechanical gate ->
    grade -> release, with the command that performs each step. This is the composition
    A4 exists for — every piece of it already shipped, and what was missing was the
    parent running them in order without a person deciding what comes next.

    ZERO-TOKEN AND ZERO-WRITE, exactly like `scan`, and for the same reason: a driver
    that has to be afraid of its own planner is a driver nobody leaves running. It reads
    the stored condition results rather than re-running them (`exit-tick` is one of the
    steps it EMITS), it touches nothing, and it calls no model. The judgement — what
    grade each dimension earns, what to ask a child for — is the skill's, and the
    commands it prints leave exactly those blanks.

    Exit codes so a driver can branch without parsing prose: 0 there is work to do · 3
    the turn halted (`halt` says which of the six reasons) · 2 the command was wrong."""
    task, err = _loop_target(a, "turn")
    if err:
        print(err)
        sys.exit(2)
    every = all_tasks()
    tree = _loop.descendants(task, every)
    cap = getattr(a, "depth", None)
    if cap:
        tree = [(t, d) for t, d in tree if d <= int(cap)]
    children = [t for t, _d in tree]
    p = _turn.plan(task, children, live=_live_seqs(),
                   is_settled=_loop.settled_fn(every),
                   resolve={t.get("id"): t for t in every}.get,
                   ask=getattr(a, "ask", None))
    # The stale-install probe is a MACHINE fact, so it is read here rather than in the
    # pure planner: a gate run against a plugin cache older than the tree under test
    # reports red about work that is correct (finding 4's FALSE RED).
    stale = _turn.stale_install(_turn.repo_version(), _turn.installed_version())
    if stale:
        p["stale_install"] = stale
    if getattr(a, "as_json", False):
        print(json.dumps(p, indent=2, sort_keys=True, default=str))
    else:
        print("\n".join(_turn.lines(p)))
        if stale:
            print("  note [%s] %s" % (stale["dim"], stale["line"]))
    if p["halt"]:
        sys.exit(3)




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


# ---------------------------------------------------------------- decompose ----
#
# THE RULE THIS EXISTS TO MAKE CHEAP: a task holds WORK or holds CHILDREN, never both.
# When a child finds its work is bigger than one session, it splits itself — and until
# now that took four commands typed in the right order (create ×N, --parent each, chain
# --depends-on, --orchestrator on). Four commands is enough friction that the honest move
# loses to carrying on, and carrying on is how a flat list of steps drifts.
#
# It is PULL, not push: nothing travels down the tree telling a child to decompose. The
# child runs this on itself, and the parent's scan sees the grandchildren on its next
# read because the scan walks the subtree.

def cmd_decompose(a):
    """`task-station decompose --task <n> --into 'Title' --into 'Title' [--chain]`

    Split a task into children in one move: create each, parent it here, optionally chain
    them with `depends-on` in the order given, and flag this task orchestrator-only so
    `delegate run` from it is refused.

    REFUSES A TASK THAT ALREADY HAS CHILDREN unless `--add` is passed. Decomposing twice
    by accident produces a second generation nobody intended, and the failure is quiet —
    the scan simply starts reporting work that duplicates work."""
    task, err = _loop_target(a, "decompose")
    if err:
        print(err)
        sys.exit(2)
    titles = [str(t).strip() for t in (getattr(a, "into", None) or []) if str(t).strip()]
    if not titles:
        print("decompose needs at least one --into '<child title>'. A task with no "
              "children to hand its work to has nothing to decompose INTO.")
        sys.exit(2)
    every = all_tasks()
    existing = _loop.children(task, every)
    if existing and not getattr(a, "add", False):
        print("decompose: task #%s already has %d child task(s): %s.\n"
              "  Pass --add to append to them, or name a different task. Decomposing "
              "twice by accident is quiet — the scan just starts reporting duplicated "
              "work." % (task.get("seq"), len(existing),
                         ", ".join("#%s" % c.get("seq") for c in existing)))
        sys.exit(2)
    made = []
    for title in titles:
        child = new_task(title, "", color=task.get("color"), effort=task.get("effort"))
        create_with_seq(child)
        child = load_task(child["id"])
        append_related(child, task, "parent")
        if getattr(a, "chain", False) and made:
            append_related(child, made[-1], "depends-on")
        child["updated_ts"] = _now()
        save_task(child)
        made.append(child)
    parent = load_task(task["id"]) or task
    parent[_loop.ORCHESTRATOR_FIELD] = True
    add_event(parent, "child", "decomposed into %s"
              % ", ".join("#%s" % c.get("seq") for c in made),
              session=getattr(a, "session", None))
    parent["updated_ts"] = _now()
    save_task(parent)
    ref = parent.get("seq") or parent["id"][:8]
    print("Decomposed #%s into %d child task(s)%s:"
          % (ref, len(made), " (chained)" if getattr(a, "chain", False) else ""))
    for c in made:
        print("  #%-5s %s" % (c.get("seq"), c.get("title")))
    print("  #%s is now ORCHESTRATOR-ONLY — it plans and grades; `delegate run --seq %s` "
          "will refuse and name a child." % (ref, ref))
    print("  Each child still needs its own goal and exit conditions — a child that "
          "registers none can never report itself done.")
    print("  next: task-station scan --task %s" % ref)


# ------------------------------------------------------ the child control channel ----
#
# A5 — REACHING A RUNNING CHILD. `invoke` starts one and `scan` watches one; neither of
# them can say a single word to one that is already going. The mechanism, the reachability
# derivation and the permission boundary all live in lib/board/channel.py, which explains
# at length why each is shaped the way it is. This is the surface: four verbs and the
# refusal that guards them.

def _announce_spec_change(task, text, session=None):
    """Push a moved exit condition to every session RUNNING on `task`, and record that it
    was pushed. Best-effort: a channel failure must never turn a successful `exit-add`
    into an error, so nothing here can raise and nothing here prints on the empty path.

    WHY EXIT-ADD IS A CONTROL EVENT AT ALL. DONE on this board is COMPUTED from the exit
    conditions, so editing them while a child works moves the target under it — and the
    child has no way to notice, because it read the checklist once at session start. The
    silence is on both sides: the parent thinks it retargeted the work, the child finishes
    something that no longer counts."""
    try:
        task = load_task(task["id"]) or task
        queued, err = _channel.announce_spec(
            task, text, from_sid=session, from_task=(get_link(session) if session else None))
        if err:
            print("  %s" % err)
            return []
        if not queued:
            return []
        add_event(task, "channel", "spec change pushed to %d live session(s): %s"
                  % (len(queued), text[:100]), session)
        task["updated_ts"] = _now()
        save_task(task)
        print("  reached %d live session(s) on this task — each must settle the change "
              "before its turn ends." % len(queued))
        return queued
    except Exception:                                   # noqa: BLE001
        return []


def _channel_report_back(order, task, report, session=None):
    """Hand a settled order's report back to WHOEVER ORDERED IT, as a memo on their task.

    This is the half of a stand-down that makes it not a kill: the parent gets the child's
    own account of where it got to. The target is the ordering task when the order
    recorded one, else the child's parent — an order with neither is settled with its
    report stored on the order itself and nothing else, which is reported rather than
    silently dropped. Returns the seq it reached, or None."""
    if not str(report or "").strip():
        return None
    target_id = order.get("from_task") or _loop.parent_id(task)
    target = load_task(target_id) if target_id else None
    if not target:
        return None
    memo_send(target, "CHILD #%s stood down — %s" % (task.get("seq"), report),
              from_sid=session, routine=True)
    target["updated_ts"] = _now()
    save_task(target)
    return target.get("seq")


def _channel_refuse(reason, task, session=None):
    """Print a channel refusal, leave a trace on the REQUESTING session's task, and exit
    2. The trace is the point: a boundary that refuses silently is indistinguishable from
    one nobody tested, and the next reader of that task needs to know an escalation was
    attempted here."""
    print(reason)
    try:
        sender = load_task(get_link(session)) if session else None
        if sender:
            add_event(sender, "channel", "refused an order — %s" % reason[:120], session)
            sender["updated_ts"] = _now()
            save_task(sender)
    except Exception:                                   # noqa: BLE001
        pass
    sys.exit(2)


def cmd_channel(a):
    """`task-station channel reach|orders|stand-down|settle|deny`

    The control channel: what a parent can say to a child that is already running, and
    the one thing it may never say.

      reach       what the channel can see on this task right now, and by which source
      orders      the queue — pending, delivered, settled
      stand-down  wrap up and hand back what you wrote (settling one REQUIRES a report)
      settle      the receiving session's answer, and where its report went
      deny        record that this session was REFUSED an action, so the channel will
                  never carry it to a peer

    `deny` is the verb that makes the boundary durable. The harness's permission
    classifier refuses the SESSION, not task-station, so a refusal it hands down is
    invisible here until somebody records it — and once recorded it binds this session
    AND every later session on the same task."""
    sub = getattr(a, "sub", None)
    session = getattr(a, "session", None)

    if sub == "deny":
        action = str(getattr(a, "action", "") or "").strip()
        if not session or not action:
            print("channel deny: --session <yours> and --action '<what was refused>' are "
                  "both required — a denial with no action names nothing to refuse.")
            sys.exit(2)
        task = None
        ref = getattr(a, "task", None)
        if ref:
            task = resolve_ref(ref) or load_task(ref)
            if not task:
                print("channel deny: no task matching %r." % ref)
                sys.exit(2)
        else:
            task = _session_task(session)
        entry = _channel.record_denial(session, action,
                                       by=getattr(a, "by", None),
                                       task=(task or {}).get("id"))
        if not entry:
            print("channel deny: nothing recorded.")
            return
        print("Recorded: this session was denied %r%s."
              % (entry["action"], (" by %s" % entry["by"]) if entry.get("by") else ""))
        if task:
            print("  Bound to task #%s as well as to this session, so a successor session "
                  "on it inherits the refusal." % (task.get("seq") or task["id"][:8]))
        print("  The channel will now refuse to carry any order that performs it.")
        return

    task, err = _loop_target(a, "channel")
    if err:
        print(err)
        sys.exit(2)
    ref = task.get("seq") or task["id"][:8]

    if sub == "reach":
        rows = _channel.live(task)
        print("Control channel — task #%s %s" % (ref, task.get("title")))
        if not rows:
            print("  no live session on this task — nothing to reach. A memo still lands "
                  "on the record and is read whenever somebody next opens it.")
            return
        for r in rows:
            print("  %s  pid %-7s %-8s via %-6s %s"
                  % ((r["session_id"] or "?")[:8], r["pid"], r.get("status") or "live",
                     r["via"], "reachable" if r["reachable"] else "no control socket"))
        pend = sum(len(_channel.orders_for(task, r["session_id"])) for r in rows)
        print("  %d order(s) pending across them." % pend)
        return

    if sub == "orders":
        rows = _channel.orders(task)
        if getattr(a, "as_json", False):
            print(json.dumps(rows, indent=2, sort_keys=True, default=str))
            return
        if not rows:
            print("(no orders on task #%s)" % ref)
            return
        print("Orders — task #%s %s" % (ref, task.get("title")))
        for o in rows:
            state = ("settled %s" % rel_time(o["settled_ts"])) if o.get("settled_ts") \
                else ("delivered, unsettled" if o.get("delivered_ts") else "queued")
            print("  %s → %s  [%s]" % (o["id"][:8], (o.get("to_sid") or "?")[:8], state))
            print("      %s" % o.get("text"))
            if o.get("report"):
                print("      report: %s" % o["report"])
        return

    if sub == "stand-down":
        why = getattr(a, "why", None)
        from_task = get_link(session) if session else None
        queued, err = _channel.stand_down(task, why=why, from_sid=session,
                                          from_task=from_task)
        if err:
            _channel_refuse(err, task, session)
        if not queued:
            print("channel stand-down: task #%s has no live session — there is nothing "
                  "running to stand down. Send a memo instead; it is read whenever the "
                  "task is next opened." % ref)
            return
        add_event(task, "channel", "stood down %d live session(s)%s"
                  % (len(queued), (" — %s" % why) if why else ""), session)
        task["updated_ts"] = _now()
        save_task(task)
        print("Stood down %d live session(s) on #%s." % (len(queued), ref))
        for o in queued:
            print("  %s → %s" % (o["id"][:8], (o["to_sid"] or "?")[:8]))
        print("  Each cannot end its turn until it settles, and settling REQUIRES a "
              "report — that report is what comes back to you.")
        return

    if sub == "settle":
        if not session:
            print("channel settle: --session <your-session-id> is required — an order is "
                  "addressed to a session, and the ledger records which one answered.")
            sys.exit(2)
        order, err = _channel.order_by_prefix(task, getattr(a, "id", None))
        if err:
            print(err)
            sys.exit(2)
        report = getattr(a, "report", None)
        status, err = _channel.order_settle(order, session, report=report)
        if err:
            print(err)
            sys.exit(2)
        if status == "already":
            print("order %s was already settled by %s."
                  % (order["id"][:8], (order.get("settled_by") or "?")[:8]))
            return
        add_event(task, "channel", "order %s settled by %s"
                  % (order["id"][:8], (session or "?")[:8]), session)
        task["updated_ts"] = _now()
        save_task(task)
        print("order %s settled." % order["id"][:8])
        reached = _channel_report_back(order, task, report, session)
        if report and reached:
            print("  your report went back to #%s as a memo." % reached)
        elif report:
            print("  nobody was recorded as having ordered it, so the report is stored on "
                  "the order and went nowhere else.")
        return

    print("channel: use `reach`, `orders`, `stand-down`, `settle`, or `deny`.")


# ----------------------------------------------------------------- the pickup rail ----
#
# The surface for the other direction of the channel: what a PARENT does about a child
# that handed work back. Two verbs, because there are exactly two things to do with a
# pending pickup — read it, and be finished with it. The record and the gate are in
# lib/board/channel.py and lib/board/cmds/sub.py respectively; this is the view.

def _pickup_report_text(row, limit=400):
    """The child's own report, as text, or None — resolved HERE rather than stamped on
    the row at file time.

    `turn.report_memo` is the definition of "the child's hand-back", including the sender
    discrimination that keeps a PARENT's rejection memo from being read as the child's
    report. A second copy of that rule would answer differently the first time either was
    tuned, and lib/board/channel.py cannot ask for it — it never loads a task. So the
    seam asks, where a task load is already happening."""
    try:
        child = load_task(row.get("child_id")) if row.get("child_id") else None
        if not child:
            return None
        launch = _turn.last_launch(child)
        memo = _turn.report_memo(child, after=(launch["ts"] if launch else None))
        if not memo:
            return None
        text = " ".join(str(memo.get("text") or "").split())
        return text[:limit - 1] + "…" if len(text) > limit else text
    except Exception:                                   # noqa: BLE001
        return None


def cmd_pickup(a):
    """`task-station pickup list|take [--task <ref>] [--id ID8] [--all]`

    A pickup is the durable record that a CHILD handed work back to this task, and the
    thing the parent's Stop gate refuses to end a turn on until it is taken.

      list   what is waiting, each with the command that reads the child's own report
      take   this one has been dealt with — stop holding the turn for it

    TAKING ONE IS NOT GRADING IT. `take` retires the notice and nothing else; the work
    still has to be gated and graded, and `turn --task <ref>` is the command that does
    both. A pickup also retires ITSELF when its child closes or parks, so the ordinary
    loop never has to run this at all — it exists for the child that reported and is
    still open, which is precisely the case that used to be invisible."""
    sub = getattr(a, "sub", None)
    session = getattr(a, "session", None)
    task, err = _loop_target(a, "pickup")
    if err:
        print(err)
        sys.exit(2)
    ref = task.get("seq") or task["id"][:8]

    if sub == "list":
        rows = _channel.pickups(task) if getattr(a, "all", False) \
            else _channel.pickups_pending(task)
        if getattr(a, "as_json", False):
            print(json.dumps(rows, indent=2, sort_keys=True, default=str))
            return
        if not rows:
            print("(no pickup waiting on task #%s — no child of it has handed work back "
                  "that nobody has taken)" % ref)
            return
        print("Pickups — task #%s %s" % (ref, task.get("title")))
        for p in rows:
            state = ("taken %s (%s)" % (rel_time(p["taken_ts"]), p.get("how") or "?")) \
                if p.get("taken_ts") else \
                ("delivered, waiting" if p.get("delivered_ts") else "waiting")
            print("  %s → child #%s  [%s]"
                  % (p["id"][:8], p.get("child_seq"), state))
            print("      %s" % (p.get("headline") or ""))
            report = _pickup_report_text(p)
            if report:
                print("      report: %s" % report)
            print("      read: %s" % _channel.pickup_read_command(p))
            if not p.get("taken_ts"):
                print("      take: %s" % _channel.pickup_command(task, p))
        pend = len([p for p in rows if not p.get("taken_ts")])
        if pend:
            print("  %d waiting. Gate and grade what came back: task-station turn "
                  "--task %s" % (pend, ref))
        return

    if sub == "take":
        rows = _channel.pickups_pending(task)
        if getattr(a, "all", False):
            targets = rows
        else:
            row, err = _channel.pickup_by_prefix(task, getattr(a, "id", None))
            if err:
                print(err)
                sys.exit(2)
            targets = [row]
        if not targets:
            print("(nothing waiting on task #%s)" % ref)
            return
        took = []
        for row in targets:
            status, err = _channel.pickup_take(task, row, sid=session,
                                               how=_channel.PICKUP_TAKEN)
            if err:
                print(err)
                sys.exit(2)
            if status == "already":
                print("pickup %s was already taken by %s."
                      % (row["id"][:8], (row.get("taken_by") or "?")[:8]))
                continue
            took.append(row)
        if not took:
            return
        add_event(task, "child", "picked up %d child hand-back(s): %s"
                  % (len(took), ", ".join("#%s" % r.get("child_seq") for r in took)),
                  session)
        task["updated_ts"] = _now()
        save_task(task)
        for row in took:
            print("pickup %s taken — child #%s." % (row["id"][:8], row.get("child_seq")))
        left = len(_channel.pickups_pending(task))
        print("  %d still waiting. Taking one does NOT grade it — `task-station turn "
              "--task %s` runs the mechanical gate and emits the grade command." % (left, ref))
        return

    print("pickup: use `list` or `take`.")
