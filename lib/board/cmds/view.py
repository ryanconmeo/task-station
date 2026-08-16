"""View command seam: the typed-edge jump-window family, the detail/history formatters, /todo render, the tint/title surfaces, and prompt-context + guidance."""
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
import os
import sys

import decisions as _dec
import loop as _loop
import save as _save
import steps as _steps

from board import nudges as _nudges

g, set_g = _shared.g, _shared.set_g

__all__ = [
    "_children_recap", "CHILDREN_RECAP_MAX",
    "_format_detail", "_replaced_suffix", "_format_history",
    "_open_jump_window", "_format_detail_session",
    "_hub_ordinals", "_hub_sid_for_ordinal", "_ordinal_resume",
    "_jump_ordinal", "_jump_one",
    "_parse_session_flag", "_is_session_jump_prompt",
    "_parse_list_arg", "_print_list_footer",
    "cmd_render",
    "cmd_prompt_tint", "cmd_session_tint", "cmd_prompt_title",
    "_auto_track_provisional", "_fold_candidate_lines",
    "cmd_prompt_context", "cmd_guidance",
]


# How many child rows the recap prints before folding the rest into a count. A parent
# with forty children is telling you something, but not in a resume digest.
CHILDREN_RECAP_MAX = 12


def _children_recap(task):
    """The computed plan for a task that HAS children — waves over `depends-on`, what is
    ready, and what is holding the rest. `[]` for a leaf, so a task with no children
    renders exactly as it always did.

    WHY THIS IS IN THE RECAP AND NOT A COMMAND YOU RUN. An orchestrator's digest used to
    describe its goal and its own checklist and say NOTHING about its children's state —
    so opening it told you what the plan WAS and never what it currently IS. You had to
    know to ask. That is the same failure the whole exit-condition design exists to
    remove, one surface up: a record that has the answer and does not volunteer it is a
    record somebody has to remember to interrogate, and the thing nobody remembers is
    exactly the thing that goes stale.

    It costs one store scan and NO model call — the same `scan` computation, rendered
    tighter. A closed task is skipped: its plan is history, not a next step."""
    if is_closed(task):
        return []
    try:
        every = all_tasks()
        tree = _loop.descendants(task, every)
        if not tree:
            return []
        by_id = {t.get("id"): t for t in every}
        depths = {t.get("id"): d for t, d in tree}
        live = set()
        try:
            import live_sessions
            live = {r.get("task_seq") for r in live_sessions.running()
                    if r.get("task_seq") is not None}
        except Exception:
            live = set()
        report = _loop.scan([t for t, _d in tree], by_id.get,
                            is_settled=_loop.settled_fn(every), depths=depths, live=live)
    except Exception:              # a recap must never be the reason a resume fails
        return []
    totals = report["totals"]
    out = ["", "Children (%d%s) — the computed plan, STOP: %s:"
           % (totals["total"],
              ", %d settled" % totals["settled"] if totals["settled"] else "",
              report["stop"].upper())]
    shown = 0
    for wave, rows in report["waves"].items():
        for r in rows:
            if shown >= CHILDREN_RECAP_MAX:
                break
            depth = r.get("tree_depth") or 1
            if r.get("orchestrator"):
                tail = "orchestrator"
            elif r.get("running"):
                tail = "RUNNING"
            elif r["settled"]:
                tail = "settled"
            elif r["parked"]:
                tail = "PARKED: %s" % r["parked"]
            elif r["ready"]:
                tail = "READY"
            elif r["blocked_by"]:
                tail = "blocked by %s" % ", ".join("#%s" % b["seq"]
                                                   for b in r["blocked_by"])
            else:
                tail = "in a cycle"
            out.append("  w%-2d %s#%-5s %-38s %s"
                       % (wave, "· " * (depth - 1), r["seq"],
                          (r["title"] or "")[:38 - 2 * (depth - 1)], tail))
            shown += 1
    if totals["total"] > shown:
        out.append("  (+%d more — full report: `task-station scan --task %s`)"
                   % (totals["total"] - shown, task.get("seq", task["id"][:8])))
    if report.get("running"):
        out.append("  RUNNING NOW: %s" % ", ".join("#%s" % s for s in report["running"]))
    if report["stop"] == _loop.READY and report["ready"]:
        out.append("  READY NOW: %s" % ", ".join("#%s" % s for s in report["ready"]))
    unreg = report["exits"]["unregistered"]
    if unreg:
        out.append("  %d of %d register NO exit condition, so they cannot report "
                   "themselves done." % (len(unreg), totals["total"]))
    return out


def _format_detail(task, session, attached=True):
    out = []
    cur = task_status(task)
    # Header carries the glyph for board tasks (○ open / ● active); closed has none.
    glyph = (STATUS_GLYPH[cur] + " ") if cur in STATUS_GLYPH else ""
    out.append("Task [%s]  —  %s%s" % (task["id"][:8], glyph, status_display(cur).upper()))
    out.append("Title:   %s" % task["title"])
    if cats:
        out.append(cats.summary(task.get("color")))
    eff = task.get("effort")
    if eff in EFFORT_GAUGE:
        out.append("Effort:  %s %s (%s)" % (EFFORT_GAUGE[eff], eff, EFFORT_WORD[eff]))
    out.append("Created: %s (%s)" % (rel_time(task.get("created_ts")), _local_iso(task.get("created_at", ""))))
    out.append("Updated: %s" % rel_time(task.get("updated_ts")))
    # Live = sessions still attached right now (link resolves back to this task);
    # total = every session that ever touched it (append-only, never pruned).
    out.append("Live sessions: %d  (of %d ever attached)"
               % (live_session_count(task), len(task.get("sessions", []))))
    # Session TREE — hubs (main vs side-quest) with their spawned workers nested.
    # Absent on a task with no recorded sessions (bare tasks render as before).
    sess_lines = _session_block_lines(task)
    if sess_lines:
        out.append("")
        out.extend(sess_lines)
    # Worker provenance — append-only hub<->worker interaction ledger tail (#463).
    # Absent when the task has no ledger (bare tasks render exactly as before).
    led = task.get("ledger") or []
    if led:
        out.append("")
        out.append("  workers (provenance, last %d):" % min(len(led), 8))
        lmeta = task.get("session_meta") or {}
        for e in led[-8:]:
            who = ("%s-%s" % (task.get("seq"), e["actor_ordinal"])
                   if e.get("actor_ordinal") is not None
                   else (e.get("actor") or "?")[:8])
            wname = ((lmeta.get(e.get("worker")) or {}).get("name")
                     or (e.get("worker") or "")[:8])
            out.append("    %s  %s %s → %s%s" % (
                rel_time(e.get("ts")), who, e.get("action"), wname,
                (" — " + e["detail"]) if e.get("detail") else ""))
    # Time/cost stats — active time (idle-gap-capped spans) + accumulated worker
    # cost. Omitted entirely for a brand-new task with neither recorded yet.
    stats = task_stats_line(task)
    if stats:
        out.append("Stats:   %s" % stats)
    # DIGEST (digest-first): goal → state → steps → decisions → artifacts. The
    # deterministic structured briefing that makes a resume load where the work
    # STANDS — never via an LLM. Supersedes the 1.15 "Briefing:" block; the full
    # Summary moves to the very end (below).
    goal = (task.get("goal") or "").strip()
    state = (task.get("state") or "").strip()
    steps = task.get("steps") or []
    decisions = task.get("decisions") or []
    prs = merged_prs(task)
    stories = merged_stories(task)
    files = task.get("files") or []
    projects = task.get("projects") or []
    if goal:
        out.append("")
        out.append("Goal:    %s" % goal)
    if state:
        out.append("")
        out.append("State (next):")
        out.append("  %s" % state)
    # The computed plan, for a task that has children — deliberately ABOVE the checklist,
    # because for an orchestrator the children ARE the outstanding work and its own steps
    # are the part it already finished or handed away.
    out.extend(_children_recap(task))
    # The ACTIVE checklist only. A SUPERSEDED step has left it — it is not outstanding
    # work — and it counts in neither side of the n/m. Numbers stay the ORIGINAL
    # indices (so `--step-done 4` keeps meaning step 4 after step 2 was retired), which
    # is why the list can show gaps; renumbering would silently repoint every command a
    # reader had in hand. The retired steps stay in `/todo <n> history`, marked.
    if steps:
        active = _steps.live(steps)
        done, total = step_progress(task)
        retired = len(steps) - len(active)
        out.append("")
        if active:
            out.append("Steps (%d/%d done):" % (done, total))
            for i, s in active:
                mark = "✓" if _steps.is_done(s) else "☐"
                out.append("  %s %2d. %s" % (mark, i, _steps.text(s)))
        else:
            out.append("Steps:   (none active)")
        if retired:
            out.append("  (%d superseded step(s) — off the checklist and out of the "
                       "count; full text in `/todo %s history`)"
                       % (retired, task.get("seq", task["id"][:8])))
    # EVERY still-current decision, spine first then narrative — no age limit, no count
    # limit, and no "+N earlier" pointer, because nothing is folded away to point at.
    # REPLACED decisions (superseded / split / merged) are omitted ENTIRELY: they are
    # not "old", they are no longer true, and showing them is the failure this exists to
    # fix. They survive only in `history`. Pinning sorts a decision into the leading
    # spine block (marked ★); it no longer decides whether it appears at all.
    shown_decisions = _dec.digest_order(decisions)
    if shown_decisions:
        out.append("")
        out.append("Decisions:")
        for _i, d in shown_decisions:
            out.append("  • %s%s" % (DECISION_PIN_MARK if _dec.is_pinned(d) else "",
                                     _dec.text(d)))
    # Memos: correspondence handed to this task — anything still awaiting THIS viewer's
    # ack is flagged first (ack it before acting so two sessions don't double-implement),
    # then the last few already-handled. Absent on a task with no memos feed.
    out.extend(_memo_detail_lines(task, session))
    # Relation edges (spawned-from / --relate + derived reverse edges); absent when
    # the task has none in either direction. Full scan is fine at detail cadence.
    related_line = _related_line(task)
    if files or prs or stories or projects or related_line:
        out.append("")
        out.append("Artifacts:")
        if files:
            out.append("  Files (most recent last):")
            for p in files[-8:]:
                d = os.path.dirname(p) or "."
                out.append("    %s  —  %s" % (os.path.basename(p), d))
        if prs:
            out.append("  PRs:")
            for p in prs:
                line = p["url"]
                if p.get("desc"):
                    line += "  —  " + p["desc"]
                out.append("    %s" % line)
        if stories:
            out.append("  Stories:")
            for s in stories:
                line = s["url"]
                if s.get("desc"):
                    line += "  —  " + s["desc"]
                out.append("    %s" % line)
        if projects:
            out.append("  Repos:   %s" % ", ".join(projects))
        if related_line:
            out.append("  " + related_line)
    # Recent activity (the per-prompt `log`) — capped to a lean tail for the
    # default resume path. The dated milestone trail (`history`, via --log) is
    # NOT rendered here at all; both the full activity + the milestone log live
    # behind `/todo <n> history`.
    log = task.get("log", [])
    if log:
        out.append("")
        out.append("Recent activity (most recent last):")
        recent = log[-ACTIVITY_TAIL:]
        if len(log) > len(recent):
            out.append("  … older activity — /todo %s history"
                       % task.get("seq", task["id"][:8]))
        for e in recent:
            when = rel_time(_iso_to_ts(e.get("ts", "")))
            out.append("  • [%s] %s" % (when, e.get("note", "")))
    out.append("")
    if attached:
        out.append("This session is now ATTACHED to this task (id %s). Continue the work "
                   "described above; the user's next message resumes it. To close it, use /done."
                   % task["id"])
    else:
        # Read-only digest (e.g. `search --detail`): rendered WITHOUT attaching, so
        # don't claim the session took the task. Point at the open/history commands.
        out.append("(Read-only digest — this did NOT attach the task. Open it with "
                   "/todo %s, or /todo %s history for the full trail.)"
                   % (task.get("seq", task["id"][:8]), task.get("seq", task["id"][:8])))
    # Live process state (running Claude sessions) annotates the resume line below
    # with a ● busy/idle · age marker — computed once, guarded. The in-project
    # workers this task delegated into are now shown nested under their spawning hub
    # in the Sessions tree above, so they are NOT re-listed here (no duplication).
    live = _live_session_index()
    rt = _resume_target(task, session)
    resume = rt["command"] if rt else None
    if resume:
        out.append("")
        out.append("Resume the working session that holds this task's context "
                   "(cd + resume, one command):")
        hub_note = _live_note(live.get(rt.get("session"))) if rt else ""
        out.append("    Hub%s:  %s%s" % (
            " (pinned)" if task.get("pinned_session") else "", resume, hub_note))
    # Summary LAST — the stable description, after the at-a-glance digest + resume.
    out.append("")
    out.append("Summary:")
    out.append(task.get("summary") or "  (no summary recorded)")
    adv = ultracode_advisory(task)
    if adv:
        out.append("")
        out.append(adv)
    return "\n".join(out)


def _replaced_suffix(decisions):
    """The ` — N superseded` / ` — N replaced (1 superseded · 2 split)` tail on the
    history view's Decisions header, or "" when every decision is current.

    Worded by the kinds ACTUALLY present, so a task whose only reconcile was
    supersession still reads exactly as it did before split/merge existed, and a mixed
    task names each verb rather than hiding all three behind one word."""
    kinds = {}
    for d in decisions or []:
        rep = _dec.replacement(d)
        if rep is not None:
            kinds[rep[0]] = kinds.get(rep[0], 0) + 1
    total = sum(kinds.values())
    if not total:
        return ""
    words = {_dec.REPLACED_SUPERSEDED: "superseded",
             _dec.REPLACED_SPLIT: "split",
             _dec.REPLACED_MERGED: "merged"}
    order = [k for k in (_dec.REPLACED_SUPERSEDED, _dec.REPLACED_SPLIT,
                         _dec.REPLACED_MERGED) if kinds.get(k)]
    if len(order) == 1:
        return " — %d %s" % (total, words[order[0]])
    return " — %d replaced (%s)" % (
        total, " · ".join("%d %s" % (kinds[k], words[k]) for k in order))


def _format_history(task):
    """The on-demand `/todo <n> history` time-machine: the COMPLETE trail for one
    task — every decision, every dated milestone (the `history` field, written via
    `update --log`), and the full activity log — clearly sectioned under a brief
    goal/state header. READ-ONLY: it renders, never attaches/reopens/mutates.

    What this shows and the default digest does NOT is the RETIRED decisions —
    superseded, split, merged — plus the dated milestone log and the full activity
    log. It is no longer the only uncapped view of the decisions themselves: the
    digest stopped truncating by age, so the difference between the two surfaces is
    now exactly "is this still true", which is the difference that means something."""
    out = []
    cur = task_status(task)
    glyph = (STATUS_GLYPH[cur] + " ") if cur in STATUS_GLYPH else ""
    seq = task.get("seq", task["id"][:8])
    out.append("History — Task #%s [%s]  —  %s%s"
               % (seq, task["id"][:8], glyph, status_display(cur).upper()))
    out.append("Title:   %s" % task["title"])
    goal = (task.get("goal") or "").strip()
    if goal:
        out.append("Goal:    %s" % goal)
    state = (task.get("state") or "").strip()
    if state:
        out.append("State:   %s" % state)
    # The COMPLETE checklist — every step ever added, including the SUPERSEDED ones the
    # active checklist drops, each marked with what replaced it (or that nothing did).
    # This is the only surface that shows a retired step, and it is what makes the
    # supersede verb honest: nothing is deleted, so the record of what was once planned
    # survives, and `update --step-restore <n>` puts any of them back.
    all_steps = task.get("steps") or []
    if all_steps:
        _active, retired = _steps.counts(all_steps)
        out.append("")
        out.append("Steps (%d, oldest first%s):"
                   % (len(all_steps),
                      (" — %d superseded" % retired) if retired else ""))
        for i, s in enumerate(all_steps, 1):
            label = _steps.replacement_label(s)
            if label is not None:
                out.append("  %2d. %s%s  — %s"
                           % (i, DECISION_DEAD_MARK, _steps.text(s), label))
            else:
                out.append("  %s %2d. %s"
                           % ("✓" if _steps.is_done(s) else "☐", i, _steps.text(s)))
    # Full decisions log (append-only, uncapped) — the complete why-trail. Unlike the
    # digest this shows REPLACED decisions too, clearly marked and naming exactly what
    # replaced them — superseded by a refutation, SPLIT into atomic parts, or MERGED
    # into a summary. History's job is to stay complete: NO reconcile verb ever deletes
    # a decision, so the record of a wrong turn (and what corrected it) is never lost,
    # and every mark is reversible via `update --restore-decision <n>`. NUMBERED because
    # these 1-based indices are exactly what `--supersedes` / `--pin-decision` take.
    decisions = task.get("decisions") or []
    out.append("")
    out.append("Decisions (%d, oldest first%s):"
               % (len(decisions), _replaced_suffix(decisions)))
    for i, d in enumerate(decisions, 1):
        label = _dec.replacement_label(d)
        if label is not None:
            out.append("  %2d. %s%s  — %s"
                       % (i, DECISION_DEAD_MARK, _dec.text(d), label))
        else:
            out.append("  %2d. %s%s"
                       % (i, DECISION_PIN_MARK if _dec.is_pinned(d) else "", _dec.text(d)))
    if not decisions:
        out.append("  (none recorded)")
    # PRESERVED SUMMARIES — every text a `--summary` replaced, oldest first, NUMBERED
    # because those 1-based positions are exactly what `--restore-summary <n>` takes.
    # Rendered ONLY here: the current summary is what a resume loads, and its ancestors
    # must never cost the digest anything. Data-gated, so a task that has never had a
    # summary replaced renders exactly as it did before this section existed.
    versions = _save.summary_versions(task)
    if versions:
        out.append("")
        out.append("Summary versions (%d preserved, oldest first — `update --task %s "
                   "--restore-summary <n>` brings one back):" % (len(versions), seq))
        for i, v in enumerate(versions, 1):
            out.append("  %2d. [%s] %s"
                       % (i, rel_time(v.get("ts")), v.get("text") or ""))
    # Dated milestone log (`--log` → history) — uncapped. Rendered ONLY here.
    history = task.get("history") or []
    out.append("")
    out.append("Log (%d dated milestone(s), oldest first):" % len(history))
    for e in history:
        when = rel_time(_iso_to_ts(e.get("ts", "")))
        out.append("  • [%s] %s" % (when, e.get("text", "")))
    if not history:
        out.append("  (none recorded)")
    # Full memo ledger — every memo + its complete ack roster, uncapped. Rendered ONLY
    # here and in the default detail's leaner "Memos:" section.
    memos = task.get("memos") or []
    out.append("")
    out.append("Memos (%d, oldest first):" % len(memos))
    for m in memos:
        out.append(_memo_line(m))
    if not memos:
        out.append("  (none recorded)")
    # Full activity log — every entry, uncapped.
    activity = task.get("log") or []
    out.append("")
    out.append("Activity (%d entr%s, oldest first):"
               % (len(activity), "y" if len(activity) == 1 else "ies"))
    for e in activity:
        when = rel_time(_iso_to_ts(e.get("ts", "")))
        out.append("  • [%s] %s" % (when, e.get("note", "")))
    if not activity:
        out.append("  (none recorded)")
    # Full worker-provenance ledger — the complete append-only hub<->worker
    # interaction trail (#463), oldest-first. Data-gated: absent on tasks with no
    # ledger, so a pre-roster task's history renders exactly as before.
    ledger = task.get("ledger") or []
    if ledger:
        hmeta = task.get("session_meta") or {}
        out.append("")
        out.append("Workers (%d interaction(s), oldest first):" % len(ledger))
        for e in ledger:
            who = ("%s-%s" % (task.get("seq"), e["actor_ordinal"])
                   if e.get("actor_ordinal") is not None
                   else (e.get("actor") or "?")[:8])
            wname = ((hmeta.get(e.get("worker")) or {}).get("name")
                     or (e.get("worker") or "")[:8])
            out.append("  • [%s] %s %s → %s%s" % (
                rel_time(e.get("ts")), who, e.get("action"), wname,
                (" — " + e["detail"]) if e.get("detail") else ""))
    out.append("")
    out.append("(Read-only history view — this did NOT attach or reopen the task. "
               "Resume with /todo %s.)" % seq)
    return "\n".join(out)


def _open_jump_window(cmd):
    """Open a NEW Terminal.app window running `cmd` (the resume one-liner) and
    bring it to the front, via open-session-window.sh. The current window — the
    one /todo was typed in — is left untouched.

    Best-effort and macOS/Terminal.app-only: any failure (not darwin, osascript
    missing, AppleScript error, script absent) returns False so the caller falls
    back to just printing the command for the user to run by hand. Never raises."""
    if sys.platform != "darwin":
        return False
    script = os.path.join(g("BASE"), "open-session-window.sh")
    if not os.path.exists(script):
        return False
    try:
        r = g("subprocess").run(["bash", script, cmd],
                                capture_output=True, text=True, timeout=15)
        return r.returncode == 0
    except Exception:
        return False


def _format_detail_session(task, session, resume=None, opened=False):
    """Compact `/todo <n> -s` view: skip the recap and jump straight into the
    task's main connected working session.

    When `opened` is True we've ALREADY launched a fresh Terminal window running
    `resume` (the current window is left as-is), so we just confirm it. When it's
    False — no recorded session yet, or the auto-open failed — we print the
    one-liner for the user to run by hand. `resume` is the precomputed resume
    command (recomputed here if not supplied)."""
    out = []
    out.append("[SESSION-JUMP] Task [%s] — %s — %s"
               % (task["id"][:8], status_display(task_status(task)).upper(), task["title"]))
    out.append("")
    if resume is None:
        resume = resume_command(task, session)
    fresh = bool(resume) and "--session-id " in resume
    verb = "starting a fresh session for" if fresh else "resuming"
    # The target is a LIVE `--bg` session when the resume one-liner is the bare
    # `claude agents` attach: a `--resume` would be refused, so we ATTACH the exact
    # live session (never a fork copy).
    bg_live = bool(resume) and resume.strip() == "claude agents"
    if bg_live:
        verb = "attaching the live background session for"
    if resume and opened:
        out.append("Opened a NEW Terminal window %s this task's working session "
                   "(this window is left as-is). Command now running there:" % verb)
        out.append("    %s" % resume)
        if bg_live:
            out.append("  (that session is a live background agent — the jump window "
                       "ATTACHES it via `claude agents`; to branch a copy instead: "
                       "`claude --resume <sid> --fork-session`)")
        out.append("")
        out.append("[JUMP-WINDOW-OPENED] The jump window is already running the "
                   "command. Reply with EXACTLY this one line and nothing else (no "
                   "preamble, recap, or extra words); do not run the command yourself:")
        out.append("    ↪ " + task_oneline(task))
    elif resume:
        # Auto-opening the jump window is macOS/Terminal.app-only (_open_jump_window
        # is darwin-gated). Off macOS we degrade to a clear one-liner the user runs
        # by hand — never an error.
        if sys.platform != "darwin":
            out.append("(Opening a jump window is macOS-only — run this in a new terminal:)")
        label = ("Start a fresh session for this task (cd + new session, one command):"
                 if fresh else
                 ("Attach the live background session (`claude agents` → pick this task's "
                  "worker):" if bg_live else
                  "Resume the main connected session (cd + resume, one command):"))
        out.append(label)
        out.append("    %s" % resume)
    else:
        out.append("No recorded working session to resume yet — start one in the "
                   "task's directory, or run `/todo %s` for the full detail."
                   % task.get("seq", task["id"][:8]))
    return "\n".join(out)


def _hub_ordinals(task):
    """Sorted list of the hub ordinals recorded on `task` (backfilling pre-roster
    entries first, so an old task answers too). Used to tell the user which
    `<seq>-<n>` handles actually exist."""
    if ensure_ordinals(task):
        save_task(task)
    return sorted(m["ordinal"] for m in (task.get("session_meta") or {}).values()
                  if m.get("role") == "hub" and m.get("ordinal") is not None)


def _hub_sid_for_ordinal(task, n):
    """The hub session id carrying ordinal `n` on `task`, or None when no hub does.

    Backfills ordinals first so a pre-roster task resolves. Compares `is not None`
    and `==` rather than truthiness — ordinal 0 is a real session."""
    if ensure_ordinals(task):
        save_task(task)
    for sid, m in (task.get("session_meta") or {}).items():
        if m.get("role") == "hub" and m.get("ordinal") is not None and m["ordinal"] == n:
            return sid
    return None


def _ordinal_resume(task, sid):
    """The resume one-liner for the EXACT hub session `sid`, or None when that one
    session can't be resumed (no findable transcript, an empty transcript, or a
    deliberately-skipped session).

    This is the single-target counterpart of `_resume_target`'s heuristic pick: the
    caller has NAMED the session by ordinal, so there is no candidate ranking and no
    fallback to a different session — an unresumable target returns None and the
    caller degrades to the fresh-start form, exactly like `-s` does."""
    m = (task.get("session_meta") or {}).get(sid) or {}
    if get_link(sid) != SKIP_SENTINEL:
        path = g("_find_session_path")(sid)
        if path and g("_session_msgcount")(path) >= 1:
            cwd = _session_cwd(path) or m.get("cwd")
            if cwd:
                return bg_aware_resume(sid, cwd)
    # A session pre-bound but not yet born (`pin --new`) is still a legitimate
    # target: the window that opens BECOMES it. Nothing is minted here.
    if m.get("preborn"):
        return "cd %s && claude --session-id %s" % (m.get("cwd") or os.getcwd(), sid)
    return None


def _jump_ordinal(task, n, session):
    """Jump into the ONE hub session named by `<seq>-<n>` — the ordinal-targeted
    twin of `_jump_one`'s heuristic jump.

    Same contract as `_jump_one`: open a fresh window, leave the INVOKING window
    completely untouched (no attach, no re-tint), and return the `[SESSION-JUMP]`
    block. An unknown ordinal returns a line naming the ordinals that DO exist —
    never a bare "no task matching", which would wrongly suggest the task is gone.
    A recorded-but-unresumable session says so and falls back to the same fresh
    `--session-id` start `-s` uses. Never raises."""
    seq = task.get("seq")
    sid = _hub_sid_for_ordinal(task, n)
    if sid is None:
        known = _hub_ordinals(task)
        if known:
            return ("Task %s has no session -%s. Its sessions are: %s."
                    % (seq, n, ", ".join("%s-%s" % (seq, k) for k in known)))
        return ("Task %s has no numbered hub sessions yet — `/todo %s` opens one."
                % (seq, seq))
    # Log the resumed touch WITHOUT passing the invoking session: linking it would
    # repaint the current window to the jumped task (the v1.9.1 re-tint bug). Only
    # the TARGET session carries this task's colour. Mirrors `_jump_one` exactly.
    touch(task, note="resumed", reopen=True)
    save_task(task)
    # Naming your OWN session by ordinal can't resume it — resuming the very
    # conversation you jumped from is the tainting bug `-s` guards against — so it
    # takes the same fresh-start degrade, just with an accurate reason.
    self_target = bool(session) and sid == session
    resume = None if self_target else _ordinal_resume(task, sid)
    dead = not _is_resumable(resume)
    if dead:
        _sid, resume = fresh_resume_command(task)
    opened = g("_open_jump_window")(resume) if resume else False
    out = _format_detail_session(task, session, resume=resume, opened=opened)
    if dead:
        why = ("is the session you are typing in" if self_target
               else "has no live session to resume (its transcript is gone)")
        out = ("Session %s-%s %s — starting a fresh session for this task instead.\n\n"
               % (seq, n, why)) + out
    return out


def _jump_one(ref, session):
    """Attach `session` to the task named by `ref`, open a fresh jump window for
    it, and return its `[SESSION-JUMP]` block. Used per-ref so `/todo <n,n…> -s`
    can jump into several tasks at once (one window + one block per task).

    A `<seq>-<ordinal>` ref names ONE specific hub session and is routed to
    `_jump_ordinal`; every other ref keeps the heuristic "main working session"
    pick unchanged.

    Returns a no-match line (never raises) so a bad ref in a comma list is
    reported without aborting the others."""
    hit = _parse_ordinal_ref(ref)
    if hit is not None:
        return _jump_ordinal(hit[0], hit[1], session)
    task = resolve_ref(ref)
    if not task:
        return "No task matching '%s'." % ref
    # A `-s` jump opens the task in a NEW window and must leave the INVOKING window
    # completely untouched — no attach, no re-tint. So we log the 'resumed' touch +
    # reopen but DON'T pass (or link) the invoking session: linking it would make
    # cmd_prompt_tint's attached-task fallback repaint the current window to the
    # jumped task (the v1.9.1 bug). Only the TARGET session carries this task's
    # colour — the resumed recorded session (already linked) or the fresh session
    # minted below (fresh_resume_command links THAT sid to the task).
    touch(task, note="resumed", reopen=True)
    save_task(task)
    resume = resume_command(task, session)
    # No concrete session to resume (no recorded one, or the only candidate was
    # THIS session) → mint + pre-bind a fresh one so the jump window auto-attaches
    # to a clean session instead of tainting into the current conversation.
    if not _is_resumable(resume):
        _sid, resume = fresh_resume_command(task)
    opened = g("_open_jump_window")(resume) if resume else False
    return _format_detail_session(task, session, resume=resume, opened=opened)


def _parse_session_flag(arg):
    """Pull a `-s` / `--session` token out of a /todo arg (e.g. `1 -s` or `-s 1`).

    `-s` means "jump straight into the task's connected working session" — emit
    the resume one-liner and skip the recap. Returns (clean_arg, session) where
    clean_arg has the flag removed so it still resolves to the task number/id.
    The flag may sit on either side of the number; only a bare `-s`/`--session`
    token counts, never a substring of an id."""
    toks = (arg or "").split()
    session = False
    kept = []
    for t in toks:
        if t in ("-s", "--session"):
            session = True
        else:
            kept.append(t)
    return " ".join(kept), session


def _is_session_jump_prompt(prompt):
    """True when `prompt` is a `/todo <n> -s` (or `--session`) session-jump.

    The jump opens the task in a NEW window and deliberately leaves the invoking
    session unattached, so cmd_prompt_tint must NOT fall back to repainting the
    current window to the jumped task's colour (the v1.9.1 re-tint bug). Matches
    only a bare `-s`/`--session` token on a todo command — never a substring of
    an id or an arbitrary non-todo prompt that happens to contain `-s`."""
    if not prompt or not cats or not hasattr(cats, "command_name"):
        return False
    name = cats.command_name(prompt)
    if not name or name.split(":")[-1].lower() != "todo":
        return False
    return any(t in ("-s", "--session") for t in prompt.split())


def _parse_list_arg(arg):
    """Recognize the listing keywords `closed [N]` and `all`.

    Returns the closed-task limit to pass to _format_list (None = show every
    closed task) when `arg` is a listing request, or False when it isn't (so
    the caller falls through to treating `arg` as a task ref). `closed` with no
    count uses DEFAULT_CLOSED_LIST; `closed N` uses N; `all` shows everything.
    """
    toks = arg.lower().split()
    if not toks:
        return False
    if toks[0] == "all":
        return None
    if toks[0] in ("closed", "recent"):
        if len(toks) > 1 and toks[1].isdigit():
            return max(1, int(toks[1]))
        return DEFAULT_CLOSED_LIST
    return False


def _print_list_footer():
    """Opt-in (default off) update nudge, list view only. Silent when off/up-to-date."""
    import update_check
    line = update_check.nudge_line()
    if line:
        print(line)


def cmd_render(a):
    # --format md makes the LIST branches emit GitHub-flavored Markdown tables the
    # skill prints verbatim (no hand-transcription). Detail and session-jump
    # branches are unaffected — they stay ASCII for this PR.
    md = getattr(a, "format", None) == "md"
    _fmt_list = _format_list_md if md else _format_list
    # Reserved leading keywords — save · pin · done · config — route the existing
    # actions through /todo. Checked BEFORE the -s/numeric/ref parsing so they
    # trigger ONLY on the exact leading token (a task never takes a free-text
    # title, so there's no collision with the numeric/board/closed/all path).
    raw = (a.arg or "").strip()
    toks = raw.split()
    if toks and toks[0].lower() in g("_TODO_SUBCMDS"):
        kw = toks[0].lower()
        rest = raw[len(toks[0]):].strip()   # everything after the leading keyword
        g("_TODO_SUBCMDS")[kw](a, rest)
        return
    # /todo <n> history (also `history <n>`) — the on-demand FULL trace: the
    # complete decisions log + the dated milestone log + the full activity log.
    # READ-ONLY: unlike `/todo <n>`, it does NOT attach, reopen, or mutate the
    # task, and it is the only view that renders the milestone log — so that
    # ever-growing trail stays OFF the default resume path. Checked before the
    # -s/numeric/ref parsing (two tokens, one of them the literal `history`).
    if len(toks) == 1 and toks[0].lower() == "history":
        # Bare `history` (no number, e.g. /task-station:history with no args) —
        # the CURRENT session's attached task's full trace. Same read-only
        # rendering as `<n> history` below, resolved via get_link instead of a ref.
        task = _session_task(a.session)
        if not task:
            print("No task attached — /todo <n> history for a specific task.")
            return
        print(_format_history(task))
        return
    if len(toks) == 2 and any(t.lower() == "history" for t in toks):
        ref = toks[1] if toks[0].lower() == "history" else toks[0]
        task = resolve_ref(ref)
        if not task:
            print("No task matching '%s'.\n\n%s" % (ref, _fmt_list()))
            return
        print(_format_history(task))
        return
    # /todo <n> prompts (also `prompts <n>`) — the WS6 read-only prompt trail: the
    # chronological, session-attributed list of the exact user prompts that drove
    # this task. READ-ONLY like `history` — it does NOT attach, reopen, or mutate.
    if len(toks) == 1 and toks[0].lower() == "prompts":
        task = _session_task(a.session)
        if not task:
            print("No task attached — /todo <n> prompts for a specific task.")
            return
        print(_format_prompts_view(task))
        return
    if len(toks) == 2 and any(t.lower() == "prompts" for t in toks):
        ref = toks[1] if toks[0].lower() == "prompts" else toks[0]
        task = resolve_ref(ref)
        if not task:
            print("No task matching '%s'.\n\n%s" % (ref, _fmt_list()))
            return
        print(_format_prompts_view(task))
        return
    arg, jump = _parse_session_flag(raw)
    if not arg:
        print(_fmt_list())
        _print_list_footer()
        return
    closed_limit = _parse_list_arg(arg)
    if closed_limit is not False:
        print(_fmt_list(closed_limit=closed_limit))
        _print_list_footer()
        return
    # `<seq>-<ordinal>` names ONE specific hub session and IMPLIES a jump, so
    # `/todo 4-0` == `/todo 4 -s` aimed at session -0 — with or without an explicit
    # `-s`. Gated on the ref actually resolving to a task, so a `<seq>` matching
    # nothing still falls through to the ordinary no-match + listing below. Only a
    # single bare `<seq>-<n>` token matches here; comma lists and the plain
    # `<seq> -s` multi-jump are left entirely to the `-s` branch.
    if _parse_ordinal_ref(arg) is not None:
        print(_jump_one(arg, a.session))
        return
    if jump:
        # -s: jump straight into the task's working session in a FRESH window
        # (leaving this one untouched). The ref before -s may be a comma list
        # (`/todo 1,2,5 -s`): attach + open one window and emit one
        # [SESSION-JUMP] block PER task. A single number is just a list of one.
        # Opening happens here so it's immediate and deterministic; each block
        # falls back to printing its one-liner if its window can't open.
        refs = [r.strip() for r in arg.split(",") if r.strip()]
        if not refs:
            print("No task matching '%s'.\n\n%s" % (arg, _fmt_list()))
            return
        print("\n\n".join(_jump_one(r, a.session) for r in refs))
        return
    if arg.lower() in ("board", "board open", "board --open"):
        # /todo board → render the visual HTML board and open it (default).
        out = write_board()
        opened = _open_path(out)
        print("[BOARD] Your visual task board:\n  %s" % out)
        print("  Opened in your browser." if opened
              else "  Open it with:  open \"%s\"" % out)
        return
    task = resolve_ref(arg)
    if not task:
        print("No task matching '%s'.\n\n%s" % (arg, _fmt_list()))
        return
    touch(task, session=a.session, note="resumed", reopen=True)
    # Viewing the detail counts as "seen" — the render already surfaces recent
    # activity, so advance the delta high-water mark to avoid re-injecting the
    # same news on this session's next prompt/session-start.
    mark_seen(task, a.session)
    save_task(task)
    set_link(a.session, task["id"])
    clear_count(a.session)
    print(_format_detail(task, a.session))


def cmd_prompt_tint(a):
    """Emit the full-palette tint escape for the skill a prompt invokes (or
    nothing), for the detected terminal (zero-setup OSC; see
    categories.tint_escape). The UserPromptSubmit hook calls this and writes
    whatever it prints to the originating TTY — so a skill like /review tints the
    terminal the instant it's run, before Claude responds. Silent when tinting is
    off, categories are off, the prompt isn't a skill, or the skill has no mapping."""
    import config
    if not config.tint_enabled():
        return
    if not cats or not hasattr(cats, "color_for_prompt") or not cats.TINT_TERMINAL:
        return
    prompt = a.prompt if getattr(a, "prompt", None) is not None else os.environ.get("TASK_STATION_PROMPT", "")
    color = cats.color_for_prompt(prompt)
    if not color:
        # The prompt invokes no skill → fall back to the ATTACHED task's category
        # colour (like cmd_session_tint), so a plain `/todo <n>` — or any non-skill
        # prompt — repaints the CURRENT window to the active task's theme tint
        # instead of leaving it on whatever the last skill painted.
        # EXCEPT a `/todo <n> -s` session-jump: that opens the task in a NEW window
        # and must leave the invoking window's tint alone — never repaint it to the
        # jumped task (v1.9.1; belt-and-suspenders to _jump_one not linking here).
        if _is_session_jump_prompt(prompt):
            return
        session = getattr(a, "session", None)
        task_id = get_link(session) if session else None
        if task_id and task_id != SKIP_SENTINEL:
            task = load_task(task_id)
            if task and task.get("color"):
                color = task.get("color")
    if not color:
        return
    import config, term
    esc = cats.tint_escape(color, term.detect())
    if esc:
        sys.stdout.write(esc)


def cmd_session_tint(a):
    """Emit the full-palette tint escape for the ATTACHED task's category, so the
    terminal tints on attach/resume (not only on the first prompt). Mirrors
    prompt-tint but resolves the colour from the session's task instead of the
    prompt. Silent when tinting is off, the session is unattached/skipped, or the
    task carries no colour; the SessionStart hook writes the bytes to the TTY."""
    import config
    if not config.tint_enabled():
        return
    if not cats or not getattr(cats, "TINT_TERMINAL", False):
        return
    task_id = get_link(a.session)
    if not task_id or task_id == SKIP_SENTINEL:
        return
    task = load_task(task_id)
    if not task or not task.get("color"):
        return
    import config, term
    esc = cats.tint_escape(task.get("color"), term.detect())
    if esc:
        sys.stdout.write(esc)


def cmd_prompt_title(a):
    """Emit an OSC title escape that labels the terminal tab/window `#<seq>: <title>`
    for an attached session — the on-attach surface, run by UserPromptSubmit every
    prompt. Pure stdout (like prompt-tint); the hook delivers the bytes to the real
    terminal. Emits NOTHING when the title feature is off (config / `TASK_STATION_TITLE=off`)
    or the session is unattached/skipped, so the user's own title is left untouched."""
    import config
    if not config.title_enabled():
        return
    task_id = get_link(a.session)
    if not task_id or task_id == SKIP_SENTINEL:
        return
    task = load_task(task_id)
    if not task:
        return
    ensure_seqs()
    # OSC 0 sets both tab and window title (Terminal.app + iTerm2); \033]0; … \007.
    sys.stdout.write("\033]0;#%s: %s\007" % (task.get("seq", "?"), task["title"]))


def _auto_track_provisional(a, prompt):
    """guaranteed-tracking: deterministically track a fresh, unattached session.

    Fold-don't-fork: if a similar OPEN task already exists, attach to it and fold
    the prompt in as a note (no sibling). Otherwise create a PROVISIONAL open task
    (auto-categorised), attach this session, and print a short directive telling
    the model how to refine it (`update`) or drop it (`skip`, which GCs it)."""
    seed = seed_title(prompt)
    note = (prompt or "").strip()

    dup = similar_open_task(seed)
    if dup:
        # F9 identity guard: a flavor-similar OPEN task is NOT a fold target when
        # the prompt names a PR/work-item the task doesn't carry (both keyed,
        # disjoint) — it's a different work item, so drop to the create path below
        # (bias create on mismatch, mirroring the interactive attach soft-guard).
        _pk = extract_identity_keys(prompt)
        if _pk and task_identity_keys(dup) and not (_pk & task_identity_keys(dup)):
            dup = None
    if dup:
        # Same code path cmd_attach uses to fold a cross-session prompt in: a
        # marker note via touch(), then the prompt itself via add_log() (so the
        # prompt is logged exactly once, not duplicated by touch's own add_log).
        touch(dup, session=a.session, note="auto-tracked (folded in)", reopen=True)
        if note:
            add_log(dup, note)
        save_task(dup)
        set_link(a.session, dup["id"])
        clear_count(a.session)
        auto_enable_category(dup.get("color"))
        _emit_tint_to_origin(dup.get("color"))   # tint NOW on auto-fold
        g("_emit_title_to_origin")(dup)          # relabel the window NOW on auto-fold
        print("[task-station] Auto-tracked: folded into open task [%s] %s — this "
              "session is now attached and your prompt was noted. No sibling task "
              "was created." % (dup["id"][:8], dup["title"]))
        return

    color = None
    if cats:
        color = (cats.color_for_prompt(prompt) if hasattr(cats, "color_for_prompt") else None) or cats.DEFAULT
    task = new_task(seed, "", color=color, status=STATUS_OPEN)
    task["provisional"] = True
    create_with_seq(task)              # atomically mint the stable number + persist
    touch(task, session=a.session, note="auto-tracked (provisional)")
    save_task(task)
    set_link(a.session, task["id"])
    clear_count(a.session)
    auto_enable_category(task.get("color"))
    _emit_tint_to_origin(task.get("color"))   # tint NOW on provisional auto-create
    g("_emit_title_to_origin")(task)          # label the window NOW on provisional auto-create

    tid = task["id"][:8]
    label = task.get("seq", tid)
    print("\n".join([
        "[task-station] Auto-tracked as task [%s] %s (provisional) — this session is "
        "now attached." % (tid, task["title"]),
        "If this is real work, refine it (clears the provisional flag):",
        "  update: task-station update --task %s --title '<short title>' "
        "--color <color> --summary '<1-3 sentences>'" % label,
        "If it is genuinely throwaway/meta, drop it (removes the provisional task):",
        "  skip:   task-station skip --session %s" % a.session,
        "  " + _cli_fallback(),
    ]))


def _fold_candidate_lines(prompt, opens, header):
    """Render the open-task candidate block for the fold-in / attach nudge with F9
    identity-keyed filtering.

    When the incoming `prompt` carries ≥1 identity key, candidates are limited to
    open tasks that share a key — flavor-only matches (same process words, different
    PR/work-item) are excluded, which is the whole point: attach on IDENTITY, not
    flavor. When the prompt carries a key that NO open task matches, the block is a
    single create-bias line instead of a candidate list. Keyless prompts list every
    open task exactly as before. Each candidate line renders that task's OWN keys
    (when it has any) so a mismatch is glanceable even on a keyless prompt. Keyless
    prompt + keyless tasks ⇒ byte-identical to the pre-F9 block. Returns a list of
    lines (empty when there is nothing to show)."""
    pkeys = extract_identity_keys(prompt)
    if pkeys:
        cands = [t for t in opens if task_identity_keys(t) & pkeys]
        if not cands:
            return ["No open task carries %s — this is a NEW work item; create a "
                    "task, don't fold into a flavor-only match."
                    % render_identity_keys(pkeys)]
    else:
        cands = list(opens)
    if not cands:
        return []
    lines = [header]
    for t in cands[:8]:
        ks = task_identity_keys(t)
        suffix = (" → keys: " + render_identity_keys(ks)) if ks else ""
        lines.append("  - #%s [%s] %s (%s)%s"
                     % (t.get("seq") or "?", t["id"][:8], t["title"],
                        rel_time(t.get("updated_ts")), suffix))
    return lines


def cmd_prompt_context(a):
    """UserPromptSubmit: bump if attached; otherwise nudge Claude to attach/create.

    An EXPLICIT 'create/attach a task' request in the prompt always prints a hard
    directive FIRST — even in a skipped or already-attached session — steering to
    task-station and away from the built-in/native TaskCreate session-todo tool."""
    intent = (cats.task_intent(os.environ.get("TASK_STATION_PROMPT", ""))
              if (cats and hasattr(cats, "task_intent")) else None)
    task_id = get_link(a.session)
    skipped = task_id == SKIP_SENTINEL
    task = load_task(task_id) if (task_id and not skipped) else None

    _ref_block = _resolve_prompt_task_refs(os.environ.get("TASK_STATION_PROMPT", ""))
    if _ref_block:
        print(_ref_block)

    if intent:
        verb = "attach" if intent == "attach" else "create"
        dlines = [
            "⚡ EXPLICIT TASK INTENT — the user's message explicitly asks to %s a task." % verb,
            "Track it on task-station NOW — the cross-session board, bound to THIS session for",
            "one-command resume. The native task tools are per-session/siloed (no board across",
            "sessions, no link back to the session that holds the context) — not what's wanted here.",
        ]
        if intent == "create":
            dlines.append("  create: task-station create --session %s --color <color> --effort <xs|s|m|l|xl> --title '<short title>' --summary '<1-3 sentences>'"
                          % a.session)
            if task:
                dlines.append("You are already attached to [%s] %s; if the user wants a SEPARATE task, "
                              "create with --force; if they mean this same task, you are already tracking "
                              "it — just say so." % (task["id"][:8], task["title"]))
        else:  # attach
            dlines.append("  attach: task-station attach --session %s --task <task-id> [--color <color>]"
                          % a.session)
            opens = [t for t in sorted_tasks() if is_on_board(t)]
            dlines.extend(_fold_candidate_lines(
                os.environ.get("TASK_STATION_PROMPT", ""), opens,
                "Open tasks you can attach to:"))
        print("\n".join(dlines))
        # The directive IS the message. Keep an attached task's activity fresh as
        # usual, but don't also dump the standard nudge after a hard directive.
        if task:
            touch(task, session=a.session, note=os.environ.get("TASK_STATION_PROMPT", ""), reopen=True)
            save_task(task)
        return

    if skipped:
        return  # session intentionally untracked: stay silent

    if task:
        was_closed = task.get("status") == "closed"
        # Compute the delta BEFORE touch — touch rewrites this session's
        # session_meta entry (resetting its high-water mark), so the "since I last
        # looked" comparison must read the pre-touch watermark.
        delta = delta_brief(task, a.session)
        # Memo arrivals are ack-gated (not seen_ts-gated), so they re-surface every
        # turn until this session acks — computed here alongside delta, but NOT cleared
        # by touch/mark_seen (only an explicit `memo ack` clears one).
        pending = memo_pending_brief(task, a.session)
        touch(task, session=a.session, note=os.environ.get("TASK_STATION_PROMPT", ""), reopen=True)
        save_task(task)
        if was_closed:
            print("[task-station] Reopened task [%s] %s — this session is working on it again."
                  % (task["id"][:8], task["title"]))
        # MODEL steering, ONLY on an ultracode turn (the harness is already
        # orchestrating): on a fan-out-worthy task with the hints feature on and an
        # ultracode signal in THIS prompt, steer breadth to think-phases and keep
        # repo writes on the delegation path. Default mode prints NOTHING here (the
        # human advisory lives on the lower-frequency detail/SessionStart surfaces),
        # so the per-prompt cost stays zero unless ultracode is in play.
        import config
        if (config.ultracode_hints_enabled() and fanout_worthy(task)
                and ultracode_signal(os.environ.get("TASK_STATION_PROMPT", ""))):
            print(ultracode_steering())
        # Delta-injection + memo pending are the only things the attached+open path
        # may emit. When another session/worker/child advanced the feed since this
        # session last looked, surface ONE bounded delta block and advance the
        # watermark so it never repeats. No news → stay silent.
        if delta:
            print(delta)
            mark_seen(task, a.session)
            save_task(task)
        # Pending memos re-surface until acked — printed AFTER the delta, and NOT
        # watermark-cleared (mark_seen doesn't touch them; only `memo ack` does).
        if pending:
            print(pending)
        # THE TWO RECORD ADVISORIES, in the same rail as the memo nag above and gated the
        # same way it is: ONE line each, at most once per (task, session), silent on a
        # healthy task. They are here rather than only at SessionStart because a long
        # working session is the thing that MAKES its own task heal-due and its checkpoint
        # stale — by the time either is true the session-start rail has long since run, and
        # the next surface that would say so is the `save` gate, which this session reaches
        # only if it already decided to checkpoint. See lib/board/nudges.py.
        #
        # HEAL FIRST, SAVE SECOND, which is the order the save gate itself argues for: a
        # summary written from an unreconciled decision set bakes the drift into the first
        # field anyone reads, so if both fire, the reconcile is the one to do first.
        #
        # Each wrapped separately so one failing advisory cannot silence the other, and
        # both fail open twice over (here and inside) — a prompt that failed to submit is
        # strictly worse than an unreported stale digest.
        try:
            heal_nudge = _nudges.heal_line(task, a.session)
        except Exception:
            heal_nudge = None
        if heal_nudge:
            print(heal_nudge)
        try:
            save_nudge = _nudges.save_line(task, a.session)
        except Exception:
            save_nudge = None
        if save_nudge:
            print(save_nudge)
        # Glossary auto-injection: when the attached task carries a canonical
        # vocabulary, append it so every session reuses the same terms. Gated —
        # emits nothing when the glossary is empty, so the per-prompt cost is zero
        # until terms exist. Claude's UserPromptSubmit wiring; other hosts emit the
        # same block via the `glossary-context` adapter hook.
        gc = glossary_context(task)
        if gc:
            print(gc)
        return  # attached & open: nothing else to emit

    # Not attached: count the miss, surface open tasks, and nudge Claude.
    n = bump_count(a.session)

    # Guaranteed-tracking (opt-in, default OFF): on the FIRST miss of a fresh,
    # unattached, non-skipped, no-explicit-intent session, the hook itself
    # deterministically creates+attaches a provisional task (fold-don't-fork) and
    # returns — no nudge. Default OFF → behaviour is exactly the firmer nudge below.
    import config
    if config.guaranteed_tracking_enabled() and n == 1:
        _auto_track_provisional(a, os.environ.get("TASK_STATION_PROMPT", ""))
        return

    # Intermediate misses (1 < n < NUDGE_ESCALATE_AFTER): a SINGLE compact line.
    # The full block — open-task list, attach/create syntax, colour legend, tint,
    # guidance pointer — was already shown at n == 1, so reprinting it every
    # message just burns tokens. Only n == 1 gets the full block (below).
    if 1 < n < NUDGE_ESCALATE_AFTER:
        line = ("[task-station] Still untracked (msg %d). Track the topic as an OPEN task "
                "(○) — or fold it into a task above with `attach --note` — else skip." % n)
        # Category auto-detection is a compiled-regex + dict lookup — effectively
        # free — so it keeps running on EVERY prompt, even the collapsed nudge. If
        # this prompt maps to a category, carry just that one hint (no legend) so a
        # later attach can still auto-categorize.
        if cats and hasattr(cats, "color_for_prompt"):
            skill_color = cats.color_for_prompt(os.environ.get("TASK_STATION_PROMPT", ""))
            if skill_color:
                line += (" This prompt maps to category '%s' (%s) — use --color %s on attach."
                         % (skill_color, cats.label(skill_color), skill_color))
        print(line)
        return

    opens = [t for t in sorted_tasks() if is_on_board(t)]
    lines = ["[task-station] This session is not attached to a tracked task yet."]
    lines.extend(_fold_candidate_lines(
        os.environ.get("TASK_STATION_PROMPT", ""), opens,
        "Open tasks that may match what the user wants:"))
    lines.append("")

    if n >= NUDGE_ESCALATE_AFTER:
        lines.append("⚠ %d messages in and still untracked. Attach/create NOW if this "
                     "is real work, else skip:" % n)
        lines.append("      task-station skip --session %s" % a.session)
        lines.append("Attach/create syntax + colours: task-station guidance")
        print("\n".join(lines))
        return

    # n == 1 only: full education block (open-task list + templates + legend).
    # Compact form: full rules/examples live in `task-station.py guidance` (and the
    # SessionStart injection points there) — keep the per-prompt cost minimal.
    lines.append("Track this topic NOW as a NEW task (○) — every topic gets tracked, "
                 "plain questions and Q&A included; it shows on the board immediately and "
                 "AUTO-PROMOTES to active (●) when you act on it (edit a file, delegate, "
                 "multi-step). FIRST scan the tasks above: if this prompt continues one of "
                 "them, FOLD INTO IT — `attach --session %s --task <id> --note '<this "
                 "prompt>'` — don't create a sibling. FOLD ON IDENTITY, NOT FLAVOR: when "
                 "this prompt names a PR or work-item (e.g. PR 1115, Projectname-3166), fold ONLY "
                 "into a task carrying that SAME key — a shared topic with a DIFFERENT "
                 "PR/story is a different work item, so create a new task. Only a genuinely "
                 "new topic creates a task." % a.session)
    if cats:
        skill_color = (cats.color_for_prompt(os.environ.get("TASK_STATION_PROMPT", ""))
                       if hasattr(cats, "color_for_prompt") else None)
        if skill_color:
            lines.append("This prompt's skill maps to category '%s' (%s); terminal already tinted — "
                         "use --color %s."
                         % (skill_color, cats.label(skill_color), skill_color))
        lines.append("  attach: task-station attach --session %s --task <task-id> [--color <color>]" % a.session)
        lines.append("  create: task-station create --session %s --color <color> --effort <xs|s|m|l|xl> --title '<short title>' --summary '<1-3 sentences>'"
                     % a.session)
        legend = cats.compact_legend() if hasattr(cats, "compact_legend") else ""
        if legend:
            lines.append("Colors: " + legend)
        lines.append("Tracking happens ONLY by RUNNING the create/attach command above — then "
                     "relay its result line (the '📋 Created task [..] <title>' / '📋 Attached to "
                     "task [..] <title>' the tool prints) to the user verbatim. Do NOT write your "
                     "own 'Tracking' line: a self-authored line WITHOUT running the command leaves "
                     "the session untracked on the board while telling the user otherwise. "
                     "The terminal tints to the category automatically. "
                     "Full rules: task-station guidance")
    else:
        lines.append("  attach: task-station attach --session %s --task <task-id>" % a.session)
        lines.append("  create: task-station create --session %s --effort <xs|s|m|l|xl> --title '<short title>' --summary '<1-3 sentences>'"
                     % a.session)
        lines.append("Tracking happens ONLY by RUNNING the create/attach command above — then "
                     "relay its result line (the '📋 Created task [..] <title>' / '📋 Attached to "
                     "task [..] <title>' the tool prints) to the user verbatim. Do NOT write your "
                     "own 'Tracking' line: a self-authored line WITHOUT running the command leaves "
                     "the session untracked on the board while telling the user otherwise. "
                     "Full rules: task-station guidance")
    print("\n".join(lines))


def cmd_guidance(a):
    """Full attach/create how-to, fetched on demand (kept out of the per-prompt
    injection for token economy — `prompt-context` points here)."""
    lines = ["[task-station] Every topic gets tracked from the first prompt — TRACK, don't stay silent:",
             "  - STATUS: a topic you merely raise starts NEW (○) — track it now, even a plain question.",
             "    It shows on the board immediately and AUTO-PROMOTES to ACTIVE (●) when work starts",
             "    (you edit a file in this session, delegate --worktree, or run a multi-step process).",
             "    /done then closes it. Per-task state: new (○) → active (●) → closed (✕)  "
             "(stored value stays `open`).",
             "  - FOLD, DON'T FORK: before creating, scan the board (new + active). If this prompt",
             "    continues an existing task, ATTACH to it and append the prompt as a note — no sibling.",
             "    FOLD ON IDENTITY: when the prompt names a PR/work-item (PR 1115, #1115, Projectname-3166,",
             "    AB#3166), fold ONLY into a task carrying that SAME key — a shared topic with a",
             "    DIFFERENT PR/story is a different work item ⇒ create. `attach` soft-blocks a",
             "    key-mismatched fold (re-run with --force-key to override).",
             "  - write a one-line title good enough to recognise the topic later.",
             'TRACK examples:  "how does X work?" (new), "add dark mode", "fix the auth bug"',
             "FOLD example:    a follow-up question about a task on the board → attach --note, not a new task",
             "SKIP only genuinely throwaway/meta chatter: task-station skip --session <session-id>"]
    if cats:
        lines.extend(cats.picker_lines())
        lines.append("  • Matches a task on the board → attach (FOLD IN; --note appends this prompt to its log; "
                     "--color sets/recategorizes — a key, emoji, or [TAG]):")
        lines.append("      task-station attach --session <session-id> --task <task-id> [--note '<prompt>'] [--color <color>]")
        lines.append("  • Otherwise → create with its colour and an effort estimate "
                     "(xs/s/m/l/xl — your read of the task's complexity & scope). New tasks "
                     "start as new (○); add --active to start active (●) when work has already begun:")
        lines.append("      task-station create --session <session-id> --color <color> --effort <xs|s|m|l|xl> --title '<short title>' --summary '<1-3 sentence summary>' [--active]")
        if cats.TINT_TERMINAL:
            lines.append("The terminal is tinted to the task's category automatically "
                         "(full palette via terminal escapes) — nothing to run by hand.")
    else:
        lines.append("  • attach: task-station attach --session <session-id> --task <task-id>")
        lines.append("  • create: task-station create --session <session-id> --effort <xs|s|m|l|xl> --title '<short title>' --summary '<1-3 sentence summary>'")
    lines.append("Always track via task-station (attach/create above) — it lands on your "
                 "cross-session board, bound to this session for one-command resume. The native "
                 "task tools are per-session/siloed (no global board, no session-resume link).")
    lines.append("NATIVE TASKS INTEROP (read-only): Claude Code's own in-session Tasks are for "
                 "in-session orchestration; task-station is the DURABLE cross-session console. See "
                 "recent native lists with `native` (`/todo native`); when a native item is worth "
                 "tracking durably, `adopt --native <list-prefix>:<id>` promotes it to a station "
                 "task. task-station NEVER writes the native store.")
    lines.append("The confirmation is the tool's OWN result line — run create/attach, then surface "
                 "that line (it names the task #, which is the proof it's recorded). NEVER fabricate "
                 "a '📋 Tracking' line yourself: printing one without having run the command desyncs "
                 "the board from what's actually stored.")
    lines.append("DIGEST (this is how a task stays resumable): as you work, keep the structured "
                 "digest current — `update --state '<next step / where it stands>'` (refresh when "
                 "you pause or finish), tick the checklist with `--step-done N` (add new ones with "
                 "`--step-add`), record choices as you make them with `--decision '<what & why>'`, "
                 "and log dated milestones/findings with `--log '<dated milestone/finding>'` (the "
                 "append-only HISTORY trail — it does NOT load on a normal resume; read it back via "
                 "`/todo <n> history`). `--goal '<what done looks like>'` anchors it; `--pr <url>` "
                 "pins the PR and `--story <url>` pins the story/work-item. So a resume "
                 "loads a briefing, not just a transcript.")
    lines.append("CONTENT HYGIENE: `summary` is the CURRENT-SNAPSHOT description — rewrite it to the "
                 "present truth (`--summary` REPLACES it wholesale) and keep it lean, NOT a running "
                 "log. The WHY/WHEN trail lives in `--decision` (choices + rationale) and `--log` "
                 "(dated milestones/findings) — both off the normal resume path, read back via "
                 "`/todo <n> history`. (`--append-summary` still exists but don't use it for "
                 "progress notes.) A replaced summary is NOT destroyed: it is preserved append-only "
                 "and `--restore-summary [n]` brings it back, so a thin save cannot silently lose a "
                 "good one.")
    lines.append("CHECKPOINT / RESUME: `/todo save` reports the GAP — which named slots are empty or "
                 "stale, what has landed since the last checkpoint, what the digest costs — instead "
                 "of echoing the digest you already have (`/todo save --verbose` dumps it, "
                 "`/todo save --check` re-runs the gap report alone as the mechanical cold-read "
                 "check). It captures only, no pin, and it does NOT stamp: "
                 "`last_full_save_ts` is written by the `update` that carries a `--summary` AND a "
                 "`--state`, because that pair IS the checkpoint. `/todo <n>` gives the lean recap; "
                 "`/todo <n> history` shows the full decisions + log + activity trail (and every "
                 "preserved summary version); `/todo <n> -s` resumes the original session's "
                 "transcript.")
    lines.append("Command forms: use `/task-station:<name>` (todo/save/history/pin/done/config/repos) "
                 "unless you have enabled the short bare aliases with `config --bare-cmds on` (then "
                 "`/todo`, `/save`, … work directly).")
    if _auto_checkpoint_enabled():
        lines.append("AUTO-CHECKPOINT is ON: on a compaction the harness summary is stashed to "
                     "`/todo <n> history` for free — but that is a backup, not the digest. Keep the "
                     "STRUCTURED digest current (refresh `--state`, tick `--step-done`, add a "
                     "`--decision`) so a resume stays accurate; a stale digest triggers a Stop nudge "
                     "until you refresh it.")

    # COMMANDS — compact full reference (model-facing source of truth). Use these
    # exact forms instead of reinventing a command. Preferred invocation is the
    # short `task-station <command> …` shim (the plugin's bin/ is on the Bash tool
    # PATH while enabled); the absolute python3 form is the parenthetical fallback
    # for shells without bin/ on PATH.
    lines.append("")
    lines.append("Commands  (invoke as: task-station <command> …, or "
                 "python3 %s/task-station.py <command> … if the shim isn't on PATH)" % g("BASE"))
    lines.append("Lifecycle: new ○ → active ● → closed ✕  (stored value stays `open`).  "
                 "<task> = seq number or id-prefix; "
                 "<session> = session uuid.")
    lines.extend([
        "  create  --session <s> --color <c> --effort <xs|s|m|l|xl> --title '…' --summary '…' "
        "[--goal '…'] [--step '…' …] [--active] [--no-attach|--attach] [--force]   — track a new "
        "task (attaches the session; --goal/--step seed the digest)",
        "  attach  --session <s> --task <ref> [--color <c>] [--note '…'] [--force-key]   — link "
        "session to a task (reopens if closed). FOLD-DON'T-FORK: prefer attach --note over a new "
        "create when it continues an existing task. Soft-blocks a key-mismatched fold (prompt "
        "names a PR/work-item the task doesn't carry) — --force-key overrides",
        "  detach  --session <s> [--task <ref>]   — unlink the session from its task",
        "  update  --task <ref> [--title|--summary|--append-summary|--restore-summary [N]|--goal|"
        "--state|--step-add|"
        "--step-done N|--step-undone N|--step-supersede N|--step-restore N|--decision|"
        "--supersedes N|--pin|--pin-decision N|"
        "--unpin-decision N|--restore-decision N|--log|--pr|--pr-desc|--story|--story-desc|--color|"
        "--effort]   — amend a task / keep its digest current (--goal what-done-looks-like · "
        "--state next-step · a --summary AND a --state in ONE call STAMPS a full checkpoint "
        "(the pair IS the checkpoint — no flag declares it) · --summary REPLACES wholesale but "
        "PRESERVES what it overwrote, and --restore-summary [N] brings any version back · "
        "--step-* checklist · "
        "--step-supersede N retires a STALE step: it leaves the checklist and BOTH sides of "
        "the n/m count, keeps its text in history marked with what replaced it (a --step-add "
        "in the same call), and --step-restore N undoes it — there is no step EDIT, because "
        "rewriting a step in place mutates the record · --decision append-only · "
        "--supersedes N marks decision N REPLACED by this one (gone from the digest, kept "
        "in history) · --pin sorts a decision FIRST in the digest (ordering, not "
        "visibility — every current decision renders) · "
        "--restore-decision N UNDOES a supersede/split/merge mark (every reconcile is "
        "reversible; nothing is ever deleted) · --log dated history "
        "(off the resume path; see /todo <n> history) · --pr stored PR url · "
        "--story stored story/work-item url · --orchestrator on|off flags the task "
        "ORCHESTRATOR-ONLY, so `delegate run --seq <it>` REFUSES and names the child that "
        "should own the work)",
        "  heal  [--task <ref>] [--scan [--probe-links]] [--apply [--verbose]] [--all] "
        "[--mark-healed [--note '…']] [--goal-reviewed] [--candidates] [--dismissals] "
        "[--apply --dismiss '<check>:<ref>' --why '…' | --undismiss '<check>:<ref>'] "
        "[--dispose-acks <id8,…|all> --decision '…'|--memory <slug>|--noop '<reason>']   — "
        "RECONCILE the append-only "
        "decision log into current state (the counterpart to `save`'s capture). PREFER the "
        "`heal` SKILL, which drives the whole sequence — scan, read the dry run ONCE, "
        "propose a plan, confirm, execute, stamp — so no flag below needs typing by hand. "
        "--scan is "
        "the deterministic zero-token pass and never modifies the task (and never stamps a "
        "heal); bare `heal` is a "
        "DRY RUN that prints the plan and changes nothing; --apply performs the mechanical "
        "plan after backing the task blob up, prints ONLY what it did (--verbose for the "
        "full block), STAMPS the heal when it performed at least one operation, and is "
        "REFUSED when it would perform none rather than recording a heal that never "
        "happened. --mark-healed records the judgement-only pass where nothing needed "
        "changing (--note says why). --dispose-acks retro-fills the dispositions of acks "
        "recorded before they were required — visibly retroactive, and it never overwrites "
        "one the acking session chose. --goal-reviewed records that the GOAL LINE was "
        "re-read and is still true (the only thing that resets the goal-review count; "
        "--mark-healed deliberately does not). --candidates is the CHEAP merge-only read: "
        "the goal, the pins and each candidate group's members in full, and nothing else. "
        "--dismiss adjudicates ONE false-positive finding away with a MANDATORY --why — it "
        "leaves the findings, the count and the due calculus, it covers that finding's exact "
        "text so an edit makes it re-report, --dismissals lists every ruling and --undismiss "
        "retires one; nothing is ever deleted. --probe-links opts into one HTTP HEAD per "
        "stored link (only a 404/410 is dead; everything else stays UNKNOWN). Three decision "
        "verbs: --supersedes for what is "
        "WRONG, `heal --split N --into n1,n2` for what is COMPOUND, "
        "`heal --merge n1,n2 --into N` "
        "for what is TRUE BUT NO LONGER LOAD-BEARING; `update --step-supersede N` is the "
        "same idea for a stale STEP. No verb ever deletes a decision",
        "  exit-add  --task <ref> --step N --cmd '<shell>' --expect '<substr>' [--expect …]   — "
        "give a checklist STEP the command that settles it, so DONE is COMPUTED not asserted. "
        "AT LEAST ONE --expect is required: a condition asserting nothing passes forever "
        "whatever the command printed. Upserts by step; `exit-rm --step N` drops one",
        "  exit-show  [--task <ref>]   — what each step's condition is and how it last went. "
        "Reads only; runs NOTHING",
        "  exit-tick  [--task <ref>] [--step N] [--dry-run] [--untick] [--timeout S]   — RUN "
        "the conditions and TICK the steps that passed. EXITS 1 when anything is not met, so "
        "it can gate a release rather than only inform. A condition that did NOT run "
        "(timeout/launch error) is UNKNOWN, refutes nothing and moves no tick either way; a "
        "FAILING condition on already-ticked work is reported as a REGRESSION and left alone "
        "unless --untick",
        "  scan  [--task <ref>] [--all] [--run] [--json]   — the ZERO-TOKEN loop driver: waves "
        "over depends-on, each node's exit-condition rollup, and the stopping condition "
        "(ready|working|complete|blocked|empty — WORKING means children are already "
        "running and nothing else is startable, which is the loop functioning rather "
        "than a stall). A node with a LIVE session reads RUNNING and is excluded from "
        "`ready`, because `ready` answers what to START. Calls no model and — without --run — no shell. A "
        "predecessor releases its dependents when CLOSED or when every exit condition it "
        "registered is MET; a task registering NONE is never settled, so an empty checklist "
        "cannot release work by having checked nothing. Cycles are reported, never traversed",
        "  invoke  --task <child> [--from <orch>] --ask '<the request>' [--role scout|"
        "implementer|reviewer|grader] [--model M] [--permission-mode P] [--cwd D] "
        "[--print-command] [--dry-run]   — spawn a child session ALREADY ATTACHED to its own "
        "task, so its SessionStart injects that task's digest and the ask carries the "
        "REQUEST ONLY. There is no brief to get wrong; an ask long enough to be context is "
        "warned about. A ROLE MAY RESTRICT AND MAY NEVER REPLACE: it names a permission mode "
        "only when that mode NARROWS what the child may do (silence inherits your default), "
        "and its bare model alias reclaims the parent's [1m] window when both name the same "
        "family — an explicit --model/--permission-mode always wins. A fresh git worktree is "
        "pre-trusted (and its own .mcp.json servers pre-approved) ONLY by inheritance from a "
        "main checkout you already trust; anything else is refused with the reason and the "
        "invoke still runs. --print-command is a REAL launch you finish by hand: the session "
        "is pre-attached and the trail records a MANUAL LAUNCH. --dry-run is the one that "
        "costs NOTHING — it prints the command it would run and writes nothing at all",
        "  grade  --task <child> --dim G1=A --dim G2=A- … [--threshold G] [--note '…'] "
        "[--park human-gate|blocked-external|retries-exhausted --why '…'] [--no-decision] "
        "[--json]   — one pass of the graded acceptance gate. PREFER the `grade` SKILL, which "
        "runs the mechanical gate FIRST and supplies the judgment this command cannot. "
        "Acceptance is PER-DIMENSION (default A-), never an average, and an ungraded "
        "dimension is not a pass. Exit codes: 0 accepted · 1 rejected with retries left · 3 "
        "retry budget spent · 4 parked · 2 bad command. A PARKED child is NEVER retried",
        "  decompose  --task <ref> --into '<title>' [--into …] [--chain] [--add]   — split "
        "a task into CHILD tasks and flag it orchestrator-only, in one move instead of "
        "four. A task holds WORK or holds CHILDREN, never both, and this is the verb that "
        "turns the first into the second. --chain also makes each child depend-on the "
        "previous, so the scan releases them one wave at a time. A task that already has "
        "children is REFUSED without --add: decomposing twice by accident is quiet, and "
        "surfaces only as duplicated work in the scan. It is PULL — a child decomposes "
        "ITSELF and the parent's scan sees the new generation on its next read",
        "  orchestrator-check  --task <ref>   — silent + exit 0 when delegating from this task "
        "is allowed; the refusal + exit 3 when it is flagged orchestrator-only "
        "(`update --orchestrator on`). delegate run consults it before spawning anything",
        "  status  --task <ref> [new|active]   — show/set status (new = stored open; close via done)",
        "  pin     --task <ref> [--session <s>] [--new]   (or just --session <s> to pin THIS session "
        "to its attached task)   ·   unpin --task <ref>   — pin/unpin a resume target",
        "  done    --task <ref>   (or --session <s>)   ·   skip --session <s>   — close a task · mark session untracked",
        "  whoami  --session <s>   ·   render --session <s> [--arg <ref>] [--format ascii|md]   ·   "
        "bump --session <s>   — current task · the /todo board · touch activity",
        "  search  <terms> [--open|--closed|--all] [--detail <ref>]   — ranked cross-task search "
        "(tier-1 hit list over every task's text; --detail prints one task's read-only digest). "
        "Also /todo search <terms>",
        "  board   [--open]   — write a self-contained HTML board of all tasks to <data_dir>/board.html",
        "  native   ·   adopt --native <list-prefix>:<id>   — list Claude Code's in-session native "
        "tasks (read-only) · adopt one as a durable station task",
        "  config   ·   repos   — settings board · repo index",
    ])
    lines.append("Maintenance (rarely needed — prefer done/close):")
    lines.append("  delete  --task <ref>   — HARD-delete a task (hidden from --help; lifecycle is "
                 "normally close-not-delete)")

    print("\n".join(lines))
