"""Subcommand seam: the create/attach/done lifecycle commands, the hook entry points (mark-edited, stop-gate, post-compact, stop-nudge, ConfigChange/FileChanged/WorktreeCreate), the stream surface, the glossary commands, and the F5 correspondence commands."""
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
from board.cmds.view import *
from board import nudges as _nudges
import json
import os
import sys

import channel as _channel
import checker as _checker
import loop as _loop
import config_change as _config_change
import decisions as _dec
import ownership as _own
import heal as _heal
import save as _save
import hook_health
import paths
import steps as _steps
import store as _store
import succession as _succ
import timing as _timing
import worktree_hook as _worktree_hook

g, set_g = _shared.g, _shared.set_g

__all__ = [
    "_worth_checkpointing",
    "_is_substantive_tracked", "cmd_create", "cmd_attach", "cmd_bump", "cmd_skip",
    "cmd_detach", "_open_tasks_brief", "cmd_mark_edited", "cmd_touch_file",
    "cmd_terminal",
    "cmd_stop_gate", "cmd_post_compact", "cmd_stop_nudge",
    "_boundary_facts", "_boundary_schedule", "_boundary_signature", "_record_shape",
    "_boundary_maintain", "_checkpoint_mark_text", "cmd_timing", "cmd_window",
    "_channel_task_for", "_channel_block", "_stop_gate_edit_reason",
    "_pickup_block", "_pickup_retire",
    "_stand_down_pending",
    "cmd_config_change", "cmd_file_changed", "cmd_worktree_create",
    "_done_gate_line", "_close_one", "_maybe_close_session_window",
    "cmd_done", "cmd_delete",
    "_stream_human", "cmd_stream", "cmd_redact",
    "_resolve_glossary_task", "_glossary_mutate", "cmd_glossary",
    "cmd_glossary_context",
    "cmd_link", "cmd_fork",
    "_subscriptions_check", "cmd_subscribe", "cmd_subscriptions",
]


# ------------------------------------------------------------- subcommands ----

def _is_substantive_tracked(session):
    """True when `session` is itself a real, tracked working conversation — linked
    to a live task (not unlinked, not skipped) AND past the substance floor. Used
    by `create` to avoid binding a busy parent conversation as a NEW task's resume
    target (the spun-off-task tainting bug)."""
    if not session:
        return False
    link = get_link(session)
    if not link or link == SKIP_SENTINEL:
        return False
    path = g("_find_session_path")(session)
    return bool(path) and g("_session_msgcount")(path) >= SUBSTANCE_FLOOR


def cmd_create(a):
    if not getattr(a, "force", False):
        dup = similar_open_task(a.title)
        if dup:
            attach_hint = ("attach --session %s --task %s" % (a.session, dup["id"][:8])
                           if getattr(a, "session", None)
                           else "attach --session <session-id> --task %s" % dup["id"][:8])
            print("Not created — likely a duplicate of open task [%s] %s.\n"
                  "Attach instead:  task-station %s\n"
                  "Or re-run create with --force to make a separate task."
                  % (dup["id"][:8], dup["title"], attach_hint))
            return
    requested = getattr(a, "color", None)
    if cats and requested and not cats.is_known(requested):
        print("⚠ --color '%s' is not a known category; defaulting to %s. "
              "Recategorize later with: attach --color <key|emoji|[TAG]>."
              % (requested, cats.DEFAULT))
    if getattr(a, "effort", None) and not normalize_effort(a.effort):
        print("⚠ --effort '%s' is not a known size; leaving it unset. "
              "Use xs/s/m/l/xl (or 1–5)." % a.effort)
    status = STATUS_ACTIVE if getattr(a, "active", False) else STATUS_DEFAULT
    task = new_task(a.title, a.summary, requested, getattr(a, "effort", None), status=status)
    # Structured-digest seeds: a one-line `goal` and an initial `--step` checklist
    # (repeatable). Stored straight on the task blob (no schema migration).
    goal = getattr(a, "goal", None)
    if goal:
        task["goal"] = goal.strip()
        # Baseline it here too, not only on `update --goal`. A goal written at creation
        # has an EXACTLY knowable baseline — this moment, zero decisions — and leaving it
        # unstamped would make heal's goal review say "cannot be counted" for the whole
        # life of a task whose goal was never rewritten. Uncountable is the honest answer
        # when nobody recorded the baseline; it is the wrong answer when we are standing
        # at the baseline.
        _heal.stamp_goal_touched(task)
    for s in (getattr(a, "step", None) or []):
        append_step(task, s)
    create_with_seq(task)              # atomically mint the stable number + persist
    # F4 auto-attach: score the new task against every brain and (silently) file it in
    # the winning brain. The user never names one; 'main' when nothing scores. Fail-open
    # — no brains config / a single-brain user just stays on 'main' (zero-impact law).
    auto_attach_brain(task, getattr(a, "session", None))

    session = getattr(a, "session", None)
    no_attach = getattr(a, "no_attach", False)
    # #6: creating from a SUBSTANTIVE tracked conversation defaults to no-attach so
    # the busy parent session isn't silently made the new task's resume target.
    # `--attach` forces the old bind-this-session behaviour; `--no-attach` is explicit.
    substantive = (not no_attach and not getattr(a, "attach", False)
                   and _is_substantive_tracked(session))
    spawn_parent = None
    if substantive:
        no_attach = True
        # The creating conversation is itself a real, tracked task — this new task
        # was spun off from it (the 363→365 silent-spawn case). Record a spawned-from
        # edge on the child and let the parent's event feed hear about the spin-off.
        spawn_parent = load_task(get_link(session))
        if spawn_parent:
            append_related(task, spawn_parent, "spawned-from")
            add_event(spawn_parent, "child",
                      "spawned #%s: %s" % (task["seq"], task["title"]), session)
            save_task(spawn_parent)

    if no_attach or not session:
        # Unattached create: empty sessions[]/session_meta, no session→task link.
        # `/todo <n> -s` then has no recorded session and fresh-starts a clean one.
        touch(task, note="created (no-attach)")
        save_task(task)
        _obsidian_sync(task)
        _stream_emit("task.created", task, _stream_created_data(task), session)
        if substantive:
            print("⚠ Created from a substantive tracked session — NOT binding this "
                  "conversation as the new task's resume target (use --attach to "
                  "override). /todo %s -s starts a fresh session." % task["seq"])
            if spawn_parent:
                print("   ↳ spawned-from #%s" % spawn_parent.get("seq"))
        else:
            print("📋 Created task [%s] %s (unattached). /todo %s -s starts a fresh "
                  "session." % (task["id"][:8], task["title"], task["seq"]))
        for line in cat_lines(task.get("color")):
            print(line)
        auto_enable_category(task.get("color"))
        return

    touch(task, session=session, note="created")
    save_task(task)
    _obsidian_sync(task)
    _stream_emit("task.created", task, _stream_created_data(task), session)
    set_link(session, task["id"])
    clear_count(session)
    print("📋 Created and attached to task [%s] %s" % (task["id"][:8], task["title"]))
    for line in cat_lines(task.get("color")):
        print(line)
    auto_enable_category(task.get("color"))
    _emit_tint_to_origin(task.get("color"))   # tint NOW, not on the next prompt
    g("_emit_title_to_origin")(task, session)  # label the window NOW, not next prompt


def cmd_attach(a):
    task = resolve_ref(a.task)
    if not task:
        print("No task matching '%s'." % a.task)
        return
    # F9 identity soft-guard: if the attaching prompt/--note names a PR/work-item
    # and the target task carries DIFFERENT identity keys (both sides keyed, empty
    # intersection), this is almost certainly a fold-into-the-wrong-task — warn and
    # EXIT NONZERO without attaching unless --force-key confirms. Keyless on either
    # side ⇒ proceeds exactly as before (zero behavior change for keyless flows).
    if not getattr(a, "force_key", False):
        pkeys = extract_identity_keys(getattr(a, "note", None) or "")
        tkeys = task_identity_keys(task)
        if pkeys and tkeys and not (pkeys & tkeys):
            print("⚠ key mismatch: prompt has %s, task #%s carries %s — attach "
                  "anyway? re-run with --force-key to confirm."
                  % (render_identity_keys(pkeys),
                     task.get("seq") or task["id"][:8], render_identity_keys(tkeys)))
            sys.exit(1)
    reopened = task.get("status") == "closed"
    # When categories are on: a recognized --color (re)categorizes the task —
    # this is how a task auto-tracked as the default 'general' gets corrected to
    # its real topic later. An unrecognized --color is REFUSED, not silently
    # mapped to the default, so a typo / stray emoji can't quietly mislabel the
    # task. With no --color we only backfill the default on a task that has none.
    if cats:
        requested = getattr(a, "color", None)
        if requested and cats.is_known(requested):
            task["color"] = cats.normalize(requested)
        elif requested:
            print("⚠ Ignoring --color '%s': not a known category. Use a key, "
                  "emoji, or [TAG] — e.g. brown, 🟤, or DATA. (Keeping %s.)"
                  % (requested, task.get("color") or cats.DEFAULT))
            if not task.get("color"):
                task["color"] = cats.DEFAULT
        elif not task.get("color"):
            task["color"] = cats.DEFAULT
    touch(task, session=a.session, note="attached", reopen=True)
    # --note folds a cross-session prompt into this task's activity log instead of
    # spawning a sibling task ("fold don't fork" — see commands/todo.md §grouping).
    note = getattr(a, "note", None)
    if note and note.strip():
        add_log(task, note.strip())
        clear_provisional(task)   # a folded-in note is genuine engagement
    save_task(task)
    # F4 auto-attach re-score: attach-with-edit is the promote-to-active moment — the
    # category/summary may have just been corrected. Re-score, but auto_assign only moves
    # a task still on 'main' (never yanks it out of a scored/pinned brain silently).
    auto_attach_brain(task, a.session)
    set_link(a.session, task["id"])
    clear_count(a.session)
    if reopened:
        maybe_refresh_board()   # a reopened task flips closed → open on the board
    print("📋 Attached to task [%s] %s%s%s"
          % (task["id"][:8], task["title"], " (reopened)" if reopened else "",
             " (note appended)" if note and note.strip() else ""))
    for line in cat_lines(task.get("color")):
        print(line)
    auto_enable_category(task.get("color"))
    _emit_tint_to_origin(task.get("color"))   # tint NOW on attach/recategorize
    g("_emit_title_to_origin")(task, getattr(a, "session", None))   # relabel the window NOW on attach


def cmd_bump(a):
    task_id = get_link(a.session)
    if not task_id:
        return
    task = load_task(task_id)
    if not task:
        return
    touch(task, session=a.session, note=os.environ.get("TASK_STATION_PROMPT", ""), reopen=True)
    save_task(task)


def cmd_skip(a):
    # GC: if this session is attached to a still-PROVISIONAL auto-task (created by
    # guaranteed-tracking and never engaged), skipping means it was throwaway —
    # delete it so the board carries no litter.
    gc_note = ""
    link = get_link(a.session)
    if link and link != SKIP_SENTINEL:
        task = load_task(link)
        if task and task.get("provisional"):
            delete_task(task["id"])
            gc_note = (" Removed the untouched provisional task [%s] %s."
                       % (task["id"][:8], task["title"]))
    set_link(a.session, SKIP_SENTINEL)
    clear_count(a.session)
    clear_edit_markers(a.session)   # skip is a deliberate opt-out — stop the gate nagging
    print("This session is marked untracked — the [task-station] nudge will stay silent. "
          "Attaching to or creating a task later resumes tracking.%s" % gc_note)


def cmd_detach(a):
    """Remove a session from a task's resume candidates.

    Drops `<session>` from the task's `sessions[]` and `session_meta`, clears
    `pinned_session` if it pointed at this session, and clears the session→task
    link if it still points here. `--task` selects the task; without it, the
    session's currently-linked task is used. Idempotent — a missing reference just
    reports "nothing to detach"."""
    session = a.session
    task = (resolve_ref(a.task) or load_task(a.task)) if getattr(a, "task", None) else None
    if not task:
        link = get_link(session)
        if link and link != SKIP_SENTINEL:
            task = load_task(link)
    if not task:
        print("detach: no task for session %s — pass --task <id-or-number>." % session[:8])
        return
    label = task.get("seq", task["id"][:8])
    cleared = []
    if session in task.get("sessions", []):
        task["sessions"].remove(session)
        cleared.append("sessions[]")
    meta = task.get("session_meta") or {}
    if session in meta:
        del meta[session]
        cleared.append("session_meta")
    if task.get("pinned_session") == session:
        task.pop("pinned_session", None)
        cleared.append("pin")
    if not cleared:
        print("Session %s was not attached to task %s — nothing to detach."
              % (session[:8], label))
        return
    touch(task, note="detached session %s" % session[:8])
    save_task(task)
    if get_link(session) == task["id"]:
        clear_link(session)
        clear_count(session)
        cleared.append("link")
    print("Detached session %s from task %s (cleared: %s)."
          % (session[:8], label, ", ".join(cleared)))


def _open_tasks_brief(limit=8):
    """A compact 'tasks on the board you might attach to' list for hook reasons."""
    rows = [t for t in sorted_tasks() if is_on_board(t)][:limit]
    return "\n".join("  - #%s [%s] %s" % (t.get("seq") or "?", t["id"][:8], t["title"]) for t in rows)


def cmd_mark_edited(a):
    """PostToolUse(Write|Edit|NotebookEdit): if this session edited a file but is
    NOT tracking a task, emit a one-shot reminder. Silent when already tracked,
    skipped, or already reminded — so it costs ~one injection per session, max."""
    if os.environ.get("TASK_STATION_GATE") == "off":
        return
    link = get_link(a.session)
    if link == SKIP_SENTINEL:      # session deliberately untracked — stay silent
        return
    if link:                       # attached to a real task — editing means work
        # has started, so promote an open task to active (idempotent), then
        # we're done (tracked sessions get no nudge). Editing is genuine
        # engagement, so an auto-tracked task is no longer provisional.
        if load_task(link):
            # Concurrent-safe: parallel edit hooks on the same task each land their
            # digest tally + promotion without clobbering the others.
            def _apply(task):
                promote_active(task)
                if task.get("provisional"):
                    clear_provisional(task)
                # A real file edit is substantive work → the digest is now stale
                # (marks even an already-active task, where promote_active is a no-op)
                # and counts as one event toward the milestone staleness nudge.
                mark_digest_dirty(task)
                bump_digest_events(task)   # always advances the milestone tally
            g("mutate")(link, _apply)
        return
    if not mark_edited(a.session):  # one-shot: the reminder already fired
        return
    msg = (
        "[task-station] You just edited a file and this session is NOT tracking a task. "
        "This is exactly the work that should be tracked. Attach to an existing "
        "task or create one NOW (or `skip` if this is genuinely throwaway) — the "
        "Stop gate will otherwise refuse to end the turn until you do.\n"
        "Create:  task-station create --session %s --color <color> "
        "--effort <xs|s|m|l|xl> --title '<short title>' --summary '<1-3 sentences>'\n"
        "Attach:  task-station attach --session %s --task <id-or-number>\n"
        "%s\n"
        "Open tasks:\n%s"
        % (a.session, a.session, _cli_fallback(), _open_tasks_brief() or "  (none)")
    )
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse", "additionalContext": msg}}))


def cmd_touch_file(a):
    """PostToolUse(Write|Edit|NotebookEdit): append the edited file path to the
    attached task's `files` briefing list (deduped, capped, most-recent-last).

    Cheap + best-effort: a silent no-op when the session has no attached task (or
    is skipped) or no path was passed. No log entry, no status change, no reminder
    — that's mark-edited's job; this only enriches the briefing."""
    path = getattr(a, "file", None)
    if not path:
        return
    link = get_link(a.session)
    if not link or link == SKIP_SENTINEL:
        return
    task = load_task(link)
    if not task:
        return
    if append_edited_file(task, path):
        save_task(task)


def _channel_task_for(session, linked=None):
    """The task whose control channel might hold orders for `session`.

    THREE STEPS, CHEAPEST FIRST, because this runs on EVERY turn end of EVERY session.

    1. THE LINK. For a child that has attached — the overwhelming case at Stop — this is
       one link read and we are done. `linked` is that answer already loaded: the Stop
       gate now has two limbs that both want the session's own task, and resolving it
       twice would double the cost of the commonest turn end there is. Passing None means
       "there is no linked task", which is exactly what step 1 would have concluded.
    2. THE ADDRESSEE INDEX. A session with no link is exactly the one the channel exists
       to reach, and the index says which tasks have ever addressed it. On a machine that
       has never used the channel the index file does not exist, so this whole path costs
       one stat.
    3. THE FULL SCAN, gated on the index being non-empty. The index is a cache and may
       have been evicted; the scan is the correctness backstop, and it can only cost
       anything on a machine actually running the channel.

    Returns None when nothing claims the session."""
    if linked is not None:
        return linked
    link = get_link(session)
    if link and link != SKIP_SENTINEL:
        return load_task(link)
    if not session or not _channel.index_active():
        return None
    for tid in reversed(_channel.indexed_tasks(session)):
        t = load_task(tid)
        if t and _channel.orders_for(t, session):
            return t
    for t in all_tasks():
        if session in _channel.roster(t) and _channel.orders_for(t, session):
            return t
    return None


def _channel_block(session, linked=None):
    """The Stop-hook `reason` for a session with control-channel orders waiting, or None.

    Marks the orders delivered and persists BEFORE returning the text, so the block count
    advances even if the harness never shows the reason — an order that could re-block
    forever because nobody recorded the attempt is the wedge the cap exists to prevent.
    Fail-open: a channel that raises must never stop a turn from ending."""
    try:
        task = _channel_task_for(session, linked=linked)
        if not task:
            return None
        # ROUTINE NOTICES ARE SETTLED HERE AND NEVER BLOCK. The gate fires at every turn
        # end, so an order that holds a turn costs a round trip each time — and for a
        # memo, whose fact is already durable on the task's own ledger and already
        # surfaced on the next prompt, that is a cost with nothing on the other side.
        # Settling rather than leaving it pending is what stops the queue growing a
        # backlog nobody can ever clear: the notice was delivered, to the ledger.
        quiet = _channel.notices(task, session)
        pending = _channel.deliverable(task, session)
        if quiet:
            _channel.mark_delivered(task, quiet)
            for order in quiet:
                _channel.order_settle(order, session)
        if not pending:
            if quiet:
                task["updated_ts"] = _now()
                save_task(task)
            return None
        _channel.mark_delivered(task, pending)
        task["updated_ts"] = _now()
        save_task(task)
        return _channel.block_reason(task, pending, session)
    except Exception:                                   # noqa: BLE001
        return None


def _pickup_retire(task):
    """Retire the pickups on `task` whose child the parent has already ENGAGED. Mutates;
    does NOT save. Returns the rows it retired.

    THE HALF OF THE RAIL THAT LOADS A TASK, which is why it is here and not in
    lib/board/channel.py. A rail that keeps demanding something already done is the
    cry-wolf failure this codebase has paid for repeatedly, so a pickup has to be able to
    stand down without anybody typing.

    IT IS NOT "THE CHILD IS CLOSED", and that is the trap. `done` is the verb a FINISHED
    CHILD RUNS — the commonest hand-back headline this rail carries is literally "CLOSED —
    ready for the gate" — so retiring on closure would file the notice and cancel it before
    the parent ever saw it, restoring the exact stall while looking like a fix. Engagement
    is what counts, and there are two shapes of it the record can prove:

      * A GRADE ENTRY NEWER THAN THE HAND-BACK. The parent ran the gate; the work has been
        judged. Newer, specifically: a child rejected, sent back and reporting again has a
        grade on its ledger and is waiting all over again, and `pickup_file` moves the row's
        `ts` forward for exactly that reason.
      * A PARK. The loop has stopped asking about this child, permanently. That rule
        predates this rail and outranks it.

    Costs nothing on the ordinary turn: `pickups_pending` is empty for every task with no
    child mid-hand-back, and no child is loaded until it is not."""
    retired = []
    for row in _channel.pickups_pending(task):
        child = load_task(row.get("child_id")) if row.get("child_id") else None
        if not child:
            continue                    # a child we cannot read is not a child we may retire
        how = None
        if _loop.parked(child):
            how = _channel.PICKUP_PARKED
        elif any(float(gr.get("ts") or 0) >= float(row.get("ts") or 0)
                 for gr in _loop.grades(child)):
            how = _channel.PICKUP_GRADED
        if not how:
            continue
        status, err = _channel.pickup_take(task, row, sid=None, how=how)
        if status == "taken" and not err:
            retired.append(row)
    return retired


def _pickup_block(session, task=None):
    """The Stop-hook `reason` for a PARENT whose children have handed work back, or None.

    THE KEYSTONE OF THE LOOP, and the smallest thing that could possibly be it. A child
    finishing already wrote a memo and already minted a lifecycle notice, and both of
    those surface only when a human types (`memo_pending_brief` on UserPromptSubmit,
    `child_reports_brief` on SessionStart). An orchestrator driving a loop is typed into
    by nobody, so its only move was to poll `sessions --task <child>` — which answers
    whether a process is up, and says "busy" about a child that finished an hour ago and
    left its window open. That is how #532 sat for about an hour and #536 for seven, with
    nothing broken either time.

    So the fact rides the one transport that reaches a running session with no human in
    it: the turn boundary. Same rail the control channel already uses to reach a child,
    pointed the other way.

    THE COST ON AN ORDINARY TURN IS ONE FIELD LOOKUP on a task the gate has already
    loaded. There is no scan and no index here — unlike an order, a pickup is filed on the
    parent's own task, and the session that must read it is by definition attached to that
    task. A session with no pending pickups leaves before anything else is touched.
    `task` is the gate's already-resolved answer; passing nothing makes this resolve the
    link itself, so it is callable on its own.

    Marks delivered and persists BEFORE returning the text, so the block count advances
    even if the harness never shows the reason — the same anti-wedge rule the order gate
    obeys, for the same reason. Fail-open: a rail that raises must never stop a turn from
    ending."""
    try:
        if task is None:
            link = get_link(session)
            if not link or link == SKIP_SENTINEL:
                return None
            task = load_task(link)
        if not task or not _channel.pickups_pending(task):
            return None
        dirty = bool(_pickup_retire(task))
        rows = _channel.pickups_blocking(task)
        if rows:
            _channel.pickup_mark_delivered(task, rows)
            dirty = True
        if dirty:
            task["updated_ts"] = _now()
            save_task(task)
        return _channel.pickup_reason(task, rows) if rows else None
    except Exception:                                   # noqa: BLE001
        return None


def _stand_down_pending(task, session):
    """True when a STAND-DOWN order is waiting for `session` on `task`.

    Gates the relay nudge — see the precedence note in `cmd_stop_nudge`. Fail-CLOSED on
    an exception, unusually for this file: every other channel path fails open because
    the cost of being wrong is an undelivered message, but here the cost of being wrong is
    a child spawning a successor to continue work its parent just cancelled. Staying
    silent for one turn is the cheap error."""
    try:
        return any(o.get("kind") == _channel.ORDER_STAND_DOWN
                   for o in _channel.orders_for(task, session))
    except Exception:                                   # noqa: BLE001
        return True


def cmd_terminal(a):
    """`task-station terminal [--open CMD] [--json]` — which terminal is hosting
    this session, and the only sanctioned way to open a new window in it.

    WHY IT IS A COMMAND. 2026-08-26: asked to open a new terminal, a session ran
    `osascript -e 'tell application "Terminal" to do script …'` while it was itself
    running inside iTerm. A stray Terminal.app window opened somewhere the session
    could not see, the session reported success, and a human had to close it. Both
    signals were in that session's own environment ($TERM_PROGRAM, $LC_TERMINAL,
    $ITERM_SESSION_ID) and its process ancestry, and neither was read.

    A rule saying "check $TERM_PROGRAM first" cannot fail; this can. So there is a
    command, it prints WHICH terminal it chose and WHICH SIGNAL it believed, and on
    a terminal it cannot drive it REFUSES and hands back the command — because a
    window in the wrong app produces no error, and no error reads as success."""
    from core import termhost
    if not getattr(a, "open_cmd", None):
        h = termhost.resolve()
        if getattr(a, "as_json", False):
            print(json.dumps(h))
        else:
            print("%s  (%s)" % (h["name"], h["how"]))
            print("Open a window here with: task-station terminal --open '<command>'")
        return
    cmd = a.open_cmd
    plan = termhost.spawn_plan(cmd)
    if not plan["mechanism"]:
        print(termhost.describe(plan))
        print(plan["reason"])
        return
    script = os.path.join(g("BASE"), "open-session-window.sh")
    if not os.path.exists(script):
        print("open-session-window.sh is missing from %s — run this yourself:\n  %s"
              % (g("BASE"), cmd))
        return
    try:
        # `g("subprocess")` and not a bare read: the suite patches the FACADE, so a
        # module-global here would see the unpatched value (tests/test_patch_surface).
        r = g("subprocess").run(["bash", script, cmd], capture_output=True,
                                text=True, timeout=20)
    except Exception as exc:                       # never raise out of a spawn
        print("could not open a window (%s) — run this yourself:\n  %s" % (exc, cmd))
        return
    # The script's own stderr already names the host and the signal; relaying it is
    # what makes the choice visible to whoever asked.
    for line in (r.stderr or "").splitlines():
        print(line)
    if r.returncode != 0:
        print("No window was opened. Nothing was opened anywhere else either.")


def cmd_stop_gate(a):
    """Stop hook: the turn-end gate. Three independent reasons to refuse, in this order.

    1. CONTROL-CHANNEL ORDERS. A parent reached this session while it was running; the
       end of a turn is the moment that delivery lands (see lib/board/channel.py). This
       runs FIRST because it carries somebody's words ADDRESSED TO THIS SESSION — a
       tracking nag can wait a turn; a stand-down cannot.
    2. PICKUPS — a child of this session's task handed work back and nobody has taken it.
       The same rail as (1) pointed the other way: a child reaching UP. It ranks below an
       order because an order is an instruction and a pickup is a fact, and above the
       edit nag because a pickup is somebody else's finished work waiting on this session
       while a nag is this session's own housekeeping.
    3. UNTRACKED EDITS. This session edited files but never tracked a task. Self-healing
       — clears its markers the moment a task is attached or the session is skipped — and
       capped at STOP_GATE_MAX_BLOCKS so a non-complying loop can't wedge the session.

    ALL live reasons ride ONE block document: the harness reads a single JSON object from
    this hook, and dropping one of several live reasons to fit that shape would silently
    lose whichever lost the coin toss."""
    if os.environ.get("TASK_STATION_GATE") == "off":
        return
    # RESOLVED ONCE FOR BOTH LIMBS. (1) and (2) both want the session's own task, and this
    # is the hottest path in the plugin — every turn end of every session. Two independent
    # resolutions would have doubled the cost of a turn where nothing at all is waiting.
    link = get_link(a.session)
    linked = load_task(link) if link and link != SKIP_SENTINEL else None
    reasons = []
    ch = _channel_block(a.session, linked=linked)
    if ch:
        reasons.append(ch)
    pick = _pickup_block(a.session, task=linked)
    if pick:
        reasons.append(pick)
    edit_reason = _stop_gate_edit_reason(a)
    if edit_reason:
        reasons.append(edit_reason)
    if reasons:
        print(json.dumps({"decision": "block", "reason": "\n\n".join(reasons)}))


def _stop_gate_edit_reason(a):
    """The untracked-edits half of the gate: its reason line, or None when it has nothing
    to enforce. Split out so the two halves can share one block document without either
    one deciding for the other whether to speak."""
    if not has_edited(a.session):
        return None                         # no untracked edits → nothing to enforce
    link = get_link(a.session)
    if link:                                # real task attached, or skipped
        clear_edit_markers(a.session)
        return None
    if get_blocked(a.session) >= STOP_GATE_MAX_BLOCKS:
        clear_edit_markers(a.session)       # gave it two tries — don't wedge the session
        return None
    bump_blocked(a.session)
    return (
        "This session edited files but is not tracking a /todo task. Before you "
        "finish, attach to an existing task or create one — or mark the session "
        "skipped if this edit is genuinely throwaway. Pick exactly one:\n"
        "  Create:  task-station create --session %s --color <color> "
        "--effort <xs|s|m|l|xl> --title '<short title>' --summary '<1-3 sentences>'\n"
        "  Attach:  task-station attach --session %s --task <id-or-number>\n"
        "  Skip:    task-station skip --session %s\n"
        "%s\n"
        "Open tasks:\n%s"
        % (a.session, a.session, a.session, _cli_fallback(),
           _open_tasks_brief() or "  (none)")
    )


def cmd_post_compact(a):
    """PostCompact hook (opt-in auto-checkpoint): durably stash the harness's
    compaction summary into the attached task's history — ZERO model tokens,
    retrievable via `/todo <n> history`.

    Reads the compaction summary from stdin (the hook pipes it), trims + single-
    space-collapses it to ~1200 chars, and appends a dated `history` entry. Does
    NOT touch summary/state — this is a durable backup record, not the structured
    digest. Best-effort and silent: a no-op when auto-checkpoint is off, no task is
    attached, or the session is skipped."""
    if not _auto_checkpoint_enabled():
        return
    task = _session_task(a.session)
    if not task:
        return                                  # unattached / skipped → nothing to stash
    summary = ""
    try:
        if not sys.stdin.isatty():
            summary = sys.stdin.read()
    except Exception:
        summary = ""
    summary = " ".join((summary or "").split())  # collapse newlines/runs of whitespace
    if len(summary) > 1200:
        summary = summary[:1200].rstrip() + "…"
    trigger = (getattr(a, "trigger", "") or "").strip() or "unknown"
    text = ("context compacted (%s): %s" % (trigger, summary)) if summary \
           else ("context compacted (%s)" % trigger)

    def _apply(t):                                # stdin already consumed above, so the
        if append_history(t, text, session=a.session):   # mutator stays pure/retryable
            touch(t, session=a.session,
                  note="compaction summary stashed to history (%s)" % trigger)
    g("mutate")(task["id"], _apply)


def _worth_checkpointing(task):
    """Is there anything for a checkpoint to CAPTURE? Gates the pressure nudge.

    WHY THE NUDGE NEEDED THIS. The trigger is a context percentage, and a context
    percentage only ever goes UP. The one-shot `pressure_nudged` flag is cleared the
    moment the `/todo save` block is read — correctly, because the nudge WAS delivered —
    but reading a block does not lower the percentage. So a session that crosses the
    threshold and keeps working re-armed and re-fired on EVERY subsequent Stop, telling
    the user to checkpoint a task whose checkpoint was seconds old. Observed three times
    in a row on one task with `+0 decisions, +0 steps, +0 log entries` since the stamp.

    A nag that fires when there is nothing to do is the cry-wolf failure this codebase
    has paid for four times over in `heal`, and it costs the same thing: the next real
    one gets skipped.

    So the fire side asks what the clear side cannot: has anything accrued? Never
    checkpointed at all → always worth it (that is the case the nudge exists for).
    Otherwise it takes the SAME accrual numbers the save block's own gap report prints,
    so the nudge and the report can never disagree about whether there is work to
    capture. Fail-open: an unreadable stamp nudges rather than staying silent, because a
    missed checkpoint costs more than a redundant line."""
    try:
        if not float(task.get("last_full_save_ts") or 0):
            return True
        since = _save.since_checkpoint(task)
        counts = (since.get("decisions"), since.get("steps"), since.get("log"))
        if all(c is None for c in counts):
            return True          # no baseline recorded — cannot tell, so speak up
        return any((c or 0) > 0 for c in counts) or bool(digest_stale(task))
    except Exception:
        return True


def cmd_stop_nudge(a):
    """Stop hook (opt-in auto-checkpoint): print at most ONE non-blocking Stop
    additionalContext line, with precedence:

    1. PROACTIVE context-pressure nudge — asks the model to run a FULL structured
       `/todo save` NOW, from full context, BEFORE the harness auto-compacts. It fires
       when EITHER trigger crosses:
         • checkpoint_pct (the DEFAULT): the MEASURED context (measure_context_tokens,
           read from the transcript's real usage block) reaches checkpoint_pct% of
           context_window — the accurate, window-relative signal.
         • checkpoint_at (LEGACY/fallback): the transcript-size token ESTIMATE grows
           past an explicitly-set absolute threshold — the back-compat path, used when a
           real measurement isn't available.
       Fires ONCE per pressure episode: `pressure_nudged` is set when emitted and held
       until a `/todo save` clears it, so an ignored nudge is NOT re-spammed every turn.
    0. A PENDING STAND-DOWN SILENCES BOTH OF THEM, and that ordering is a ruling rather
       than a tidy-up. A stand-down is an ORDER FROM THE PARENT — external authority the
       child does not get to weigh against its own housekeeping — while a relay is a SELF
       assessment about context. If both fire and the relay proceeds, the child spawns a
       successor to carry on work the parent has just cancelled: not a confusing pair of
       messages but a child disobeying a stop BY PROXY, and burning a fresh full-window
       session to do it. So: stand-down pending → silent, no exceptions. The reverse needs
       no rule; a relay with no stand-down pending proceeds as normal.

       SUPPRESSED IS NOT CONSUMED. The check returns BEFORE `pressure_nudged` is set, so
       the nudge is merely deferred — it fires on the next Stop once the order is settled.
       A suppression that silently spent the one-shot flag would mean a session that was
       genuinely out of room never heard about it.

       Only the pressure limb carries the disobedience risk; the staleness limb is
       suppressed for a smaller reason — the gate is already BLOCKING this turn with
       explicit instructions, and a nudge printed alongside a block is noise on top of an
       order.

    2. LIGHT staleness nudge — only when the pressure trigger did NOT fire and the digest
       is stale. Activity-gated by checkpoint_milestone_edits: it holds until N meaningful
       events (edits / promotions) have accrued since the last refresh (default 5), so a
       couple of small edits no longer nudge; 0/off restores nudge-on-any-staleness.

    1.5 THE WORK BOUNDARY (NEW in 3.44.0, `--boundary-maintenance`, default off). Between the
       two nudges sits the scheduler: when this turn is ending with NOTHING IN FLIGHT — no
       undelivered order, no unclaimed pickup, no half-done merge in the working tree — the
       AUTO maintenance class runs and REPORTS what it did, and a due handoff is named here
       rather than mid-thought. It sits BELOW the pressure nudge because pressure is the one
       signal with a deadline, and ABOVE the staleness nudge because a pass that has already
       reconciled the record makes that nudge's advice stale. See `_boundary_maintain`.

    Never emits more than ONE line in one Stop, and that is a contract with the harness, not
    a style choice: this hook's stdout is a single JSON object. Prints nothing unless a task
    is attached AND at least one of `--auto-checkpoint` / `--boundary-maintenance` is on — so
    it never fires on today's default setup. Deliberately NOT a block (no decision:block) —
    avoids the Stop gate's block cap / hard interrupts. Best-effort: the Stop hook emits
    whatever this prints."""
    checkpointing = _auto_checkpoint_enabled()
    if not checkpointing and not _config_boundary_enabled():
        return
    task = _session_task(a.session)
    if not task:
        return
    if _stand_down_pending(task, a.session):
        return                      # limb 0 above — the parent's order outranks every limb
    if not checkpointing:
        line = _boundary_maintain(a, task)
        if line:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "Stop", "additionalContext": line}}))
        return
    try:
        import config
        pct = config.checkpoint_pct()
        # Size the window to the model actually in use (Opus-1M → 1M, Haiku/Sonnet →
        # 200k) unless the user has explicitly set context_window. A fixed 200k
        # denominator on a 1M model reads ~5x over-full and fires this nudge almost
        # every Stop — the "saves too often / percentages look reversed" bug.
        #
        # THE RESOLUTION IS KEPT, NOT JUST THE NUMBER. This nudge is the exact surface that
        # fired at ~13% of the real budget for months, and the reason nobody caught it is
        # that its copy quoted a percentage without ever saying what it was a percentage OF.
        # When a stored override disagrees with what the session detects, the line now says
        # so — the cheapest possible place to notice, because it is the place the wrong
        # number is being spent.
        _wres = window_resolution(a.session)
        window = _wres["window"]
        thresh_abs = config.checkpoint_at()
        milestone = config.checkpoint_milestone_edits()
    except Exception:
        pct, window, thresh_abs, milestone = 0, 200000, 0, 0
        _wres = {"source": "default", "diverges": False, "override": None,
                 "detected": None, "window": 200000}
    # 1. Proactive context-pressure trigger (takes precedence over the staleness nudge).
    #    checkpoint_pct (measured) is the default path; checkpoint_at (estimated) is the
    #    absolute back-compat fallback. Either crossing fires the same nudge.
    measured = measure_context_tokens(a.session) if pct > 0 else 0
    pct_hit = pct > 0 and window > 0 and measured >= (pct * window) // 100
    est = estimate_session_tokens(a.session) if thresh_abs > 0 else 0
    abs_hit = thresh_abs > 0 and est >= thresh_abs
    if (pct_hit or abs_hit) and not task.get("pressure_nudged") \
            and _worth_checkpointing(task):
        seq = task.get("seq", task["id"][:8])
        # Prefer the real measurement in the copy (percent + tokens); fall back to the
        # byte-size estimate's token count when only the absolute trigger fired.
        if measured > 0:
            pct_now = round(measured * 100 / window) if window else 0
            left = max(0, 100 - pct_now)
            # Report BOTH used and remaining so the figure can't be misread against
            # Claude's native "% left" indicator. The nudge fires as the window FILLS
            # (used ≥ checkpoint_pct), i.e. precisely when little context is left.
            amount = "~%d%% used · ~%d%% left (~%dk/%dk tokens, window source: %s)" % (
                pct_now, left, measured // 1000, window // 1000, _wres["source"])
            if _wres.get("diverges"):
                amount += (" — WARNING: that window is a stored OVERRIDE of %s tokens while "
                           "this session detects %s. Every percentage above is measured "
                           "against the override, so this nudge may be firing at the wrong "
                           "time; `task-station window` explains and "
                           "`config --context-window auto` drops the override"
                           % ("{:,}".format(_wres["override"] or 0),
                              "{:,}".format(_wres["detected"] or 0)))
            note = "proactive checkpoint nudge (~%d%% used, ~%dk tokens)" % (pct_now, measured // 1000)
        else:
            amount = "large (~%dk tokens)" % (est // 1000)
            note = "proactive checkpoint nudge (~%dk tokens)" % (est // 1000)
        if mark_pressure_nudged(task):
            touch(task, note=note)
            save_task(task)
        # Name the acting hub by its ordinal when it resolves (#463); data-gated so a
        # non-rostered session keeps the original "This session's" phrasing.
        olabel = ordinal_label(task, a.session)
        who = ("Hub session %s's" % olabel) if olabel else "This session's"
        line = ("[task-station] %s context is %s and nearing auto-compaction. "
                "Run `/todo save` NOW to capture a STRUCTURED checkpoint of task %s from "
                "full context — it is a better, task-shaped compaction than the generic "
                "auto-summary. Then continue, or hand off: `task-station relay --task %s` "
                "says whether a relay is due and what a handoff would still lose, and "
                "`--spawn` opens the successor on this same task."
                % (who, amount, seq, seq))
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "Stop", "additionalContext": line}}))
        return
    # 1.5 The work boundary — only when pressure did not fire. Ranked above the staleness
    #     nudge because this limb may have just reconciled the record, which would make that
    #     nudge's advice describe a state that no longer exists.
    line = _boundary_maintain(a, task)
    if line:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "Stop", "additionalContext": line}}))
        return
    # 2. Light staleness nudge — only when neither pressure nor the boundary fired.
    if not digest_stale(task):
        return
    # Milestone gate: hold the nudge until N meaningful events have accrued since the
    # last refresh (0/off = fire on any staleness, the pre-1.61 behaviour).
    if milestone > 0 and digest_events(task) < milestone:
        return
    line = ("[task-station] The attached task's digest looks stale (work happened "
            "since the last refresh). Before finishing, refresh `--state` in one line "
            "(or tick a `--step-done` / add a `--decision`) so a resume stays current.")
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "Stop", "additionalContext": line}}))


# ---- ConfigChange / FileChanged: the two watcher hooks ------------------------

def cmd_config_change(a):
    """`task-station config-change --session <sid> --source <s> --file <path>` — the
    ConfigChange hook entry point.

    WARN BY DEFAULT: write one hook-health record naming the unresolvable paths and
    exit 0, so the config change lands and the next session start names the problem.
    ENFORCE (`config_change_enforce`, env `TASK_STATION_CONFIG_ENFORCE`) exits 2,
    which BLOCKS the change — and the record is written FIRST either way, because a
    block surfaces no transcript message at all and that record is the only trace the
    user ever gets.

    NEVER blocks on our own inability to read the file: an unparseable config is
    Claude Code's error to report, and refusing the save would trap the user's fix
    inside the file they are fixing (see lib/config_change.py)."""
    path = (getattr(a, "file", None) or "").strip()
    if not path:
        return
    try:
        findings = _config_change.unresolvable(path)
    except Exception:
        return                            # fail-open: our bug is never their block
    if not findings:
        return
    source = (getattr(a, "source", None) or "").strip() or "settings"
    detail = "%s [%s]" % (_config_change.detail(path, findings), source)
    enforce = False
    try:
        import config as _cfg
        enforce = _cfg.config_change_enforce()
    except Exception:
        enforce = False
    try:
        hook_health.record("config-change", 2 if enforce else 1, detail)
    except Exception:
        pass
    if enforce:
        sys.stderr.write("[task-station] config change blocked — %s\n" % detail)
        sys.exit(2)


def cmd_file_changed(a):
    """`task-station file-changed --session <sid> --file <path> --change <t>` — the
    FileChanged hook entry point.

    THE ACTION IS A RE-ARM, NOT A MESSAGE. FileChanged cannot inject context, so
    there is nothing to tell the model here. What it CAN do is invalidate the
    checker's self-cap: the pointer/drift nags stay silent until the state they
    fingerprinted changes, and the station config they were evaluated against just
    changed underneath them. Clearing the gate re-arms both, so the NEXT session start
    re-evaluates against the new config instead of trusting a stale fingerprint.

    FILTERS ON THE FULL PATH FIRST. The matcher can only name basenames, so every
    project's `config.json` reaches this hook; only a file inside `paths.data_dir()`
    is ours. Everything else returns in microseconds having done nothing."""
    path = (getattr(a, "file", None) or "").strip()
    if not path:
        return
    try:
        data = os.path.realpath(paths.data_dir())
        real = os.path.realpath(os.path.expanduser(path))
    except Exception:
        return
    if real != data and not real.startswith(data + os.sep):
        return                            # another project's config.json — not ours
    cleared = None
    try:
        session = getattr(a, "session", None)
        link = get_link(session) if session else None
        if link and link != SKIP_SENTINEL and _checker.clear_gate(link):
            cleared = link
    except Exception:
        cleared = None
    base = os.path.basename(real)
    if base not in STATION_WATCHED_FILES:
        return                            # inside our dir but nothing we read → silent
    change = (getattr(a, "change", None) or "").strip() or "modified"
    try:
        # Code 0 = INFORMATIONAL (hook_health.record): a config edit is not a hook
        # failure and must never be counted by the session-start failure nag.
        hook_health.record("file-changed", 0, "%s %s%s" % (
            base, change,
            (" — re-armed the checker gate for task %s" % cleared[:8]) if cleared else ""))
    except Exception:
        pass


def cmd_worktree_create(a):
    """`task-station worktree-create` — the WorktreeCreate hook entry point (payload
    on stdin). CREATES the worktree, prints its absolute path as the first stdout
    line, then provisions it best-effort. See lib/worktree_hook.py for the contract:
    the ONLY non-zero exit is a genuine creation failure, which SHOULD fail the
    harness operation because there is no worktree."""
    payload = {}
    try:
        if not sys.stdin.isatty():
            raw = sys.stdin.read()
            payload = json.loads(raw) if (raw or "").strip() else {}
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    try:
        rc = _worktree_hook.handle(payload, sys.stdout, sys.stderr)
    except Exception as e:
        sys.stderr.write("[task-station] worktree-create failed: %s: %s\n"
                         % (e.__class__.__name__, e))
        rc = 1
    if rc:
        sys.exit(rc)


def _done_gate_line(task):
    """THE DONE GATE — one warning line, or None.

    Closing a task whose record contradicts itself is the LAST chance to fix it: the
    digest outlives the session, and a closed task is what someone reads a year later
    to find out what was decided. So it WARNS and never blocks — refusing a close would
    trap work for a bookkeeping reason. Fail-open."""
    try:
        gate = _heal.gate_line(task)
    except Exception:
        return None
    if not gate:
        return None
    return ("  ⚠ %s Closing leaves that record as the permanent one — reconcile now if "
            "it still matters." % gate)


def _close_one(ref, session):
    """Close a single task by seq/id ref and return one human result line.

    Detaches every session linked to the task so none can silently reopen it.
    Returns a no-match / already-closed / closed line — never raises — so a
    caller closing a comma list can keep going past a bad ref."""
    task = resolve_ref(ref) or load_task(ref)
    if not task:
        return "No task matching '%s'." % ref
    if is_closed(task):
        return "Task [%s] %s is already closed." % (task["id"][:8], task["title"])
    if task.get("provisional"):
        # Untouched auto-tracked task: closing it leaves no closed-task litter —
        # GC it instead. Detach every linked session first.
        tid, ttl = task["id"][:8], task["title"]
        for sess in list(task.get("sessions", [])):
            if get_link(sess) == task["id"]:
                clear_link(sess); clear_count(sess); clear_edit_markers(sess)
        delete_task(task["id"])
        return "Discarded provisional task [%s] %s (auto-tracked, never engaged)." % (tid, ttl)
    # Read the heal gate BEFORE closing — the "days on an active task" limb stops
    # applying the moment the status flips, and this is the last chance to warn.
    gate = _done_gate_line(task)
    task["status"] = STATUS_CLOSED              # close from open OR active
    stamp_closed(task)                          # real moment it entered closed
    touch(task, session=session, note="closed (by id)")
    _reap_task_workers(task, session)           # stop still-live --bg workers (#464)
    save_task(task)
    _mirror_child_close(task, session)          # tell any spawned-from parent's feed
    _obsidian_event(task, "closed")
    _stream_emit("task.status", task,
                 {"status": task.get("status"), "closed_ts": task.get("closed_ts")}, session)
    # Detach EVERY session linked to this task so none can silently reopen it.
    for sess in list(task.get("sessions", [])):
        if get_link(sess) == task["id"]:
            clear_link(sess)
            clear_count(sess)
            clear_edit_markers(sess)   # closing is a deliberate wrap-up — don't let the gate block
    line = "Closed task [%s] %s. Reopen later with /todo." % (task["id"][:8], task["title"])
    rehome, rehome_note = _rehome_on_close(task, session)
    if rehome:
        line += "\n" + "\n".join(rehome)
    pseq = report_to_parent(task, _close_headline(rehome_note), session)
    if pseq:
        line += "\n  told #%s: this child is closed." % pseq
    return (line + "\n" + gate) if gate else line


def _close_headline(rehome_note=""):
    """The headline the close files on its parent. Carries the un-moved rulings when
    there are any, so the names land on a record instead of a terminal."""
    base = "CLOSED — ready for the gate (`/grade`)"
    return (base + "\n\nDECISION OWNERSHIP — " + rehome_note) if rehome_note else base


def _rehome_on_close(task, session=None):
    """Run the ownership half of closing `task` and return its report lines.

    THE INVERSE OF COLLAPSE-TO-REFERENCE, and the step the design itself flags as most
    likely to be forgotten: without it a ruling goes COLD the instant its child closes,
    which is strictly worse than the problem ownership exists to fix. Today a misplaced
    ruling is merely in the wrong place; after a bad close it is nowhere.

    `ownership.close_plan` splits the question into the part that is mechanical and the
    part that is not, and this refuses to pretend the second is the first — see that
    module's own comment for why the undecidable remainder is NAMED rather than moved.

    Returns `(display_lines, durable_note)`. Called AFTER the status flip and before the
    result line, and fully guarded: an ownership problem must never be the reason a close
    fails. A close that half-happened
    is worse than one that reported nothing, and the record is recoverable either way
    (the rulings are exactly where they were)."""
    try:
        plan = _own.close_plan(task, load_task)
        if not (plan["released"] or plan["rehomed"] or plan["reported"]):
            return [], ""
        touched, lines = _own.apply_close_plan(task, plan)
        for other in touched:
            other["updated_ts"] = _now()
            save_task(other)
        if plan["released"] or plan["rehomed"]:
            task["updated_ts"] = _now()
            save_task(task)
            for text in lines:
                add_event(task, "decision", text, session)
        out = []
        if lines:
            out.append("  DECISION OWNERSHIP on close:")
            out.extend("    • %s" % line for line in lines)
        out.extend(_own.close_report_lines(task, plan))
        # The SECOND return value is the durable half. Printing the un-moved names to a
        # terminal nobody is necessarily watching is dropping them with extra steps, so
        # the caller rides them out on the memo + pickup the close already files on the
        # parent — the one rail the parent's Stop gate will not let it past.
        return out, _own.close_report_note(task, plan)
    except Exception:
        return [], ""


def _maybe_close_session_window(session):
    """Best-effort auto-close of THIS session's terminal window after a no-arg
    /done, gated on the opt-in `done_closes_window` config (default OFF). No-op
    unless the user opted in — we cannot tell a human-typed /done from a model
    Skill-tool /done, so the destructive close is opt-in, not intent-detected.
    When enabled, spawns close-session-window.sh detached (it resolves the tty
    synchronously, then closes ~1s later from a process that outlives this shell).
    Swallows every failure — never raises, never blocks the close. The `--task`
    path never calls this."""
    try:
        import config
        if not config.done_closes_window_enabled():
            return
        script = os.path.join(g("BASE"), "close-session-window.sh")
        if not os.path.exists(script):
            return
        g("subprocess").Popen(["bash", script, "--detach", "--after", "1"],
                              stdout=g("subprocess").DEVNULL, stderr=g("subprocess").DEVNULL)
    except Exception:
        pass


def cmd_done(a):
    # Two modes:
    #   --task REF  → close any task by seq/id from anywhere (no session needed).
    #   --session   → close the task attached to this session (the /done path).
    ref = getattr(a, "task", None)
    if ref:
        # --task accepts a comma-separated list (e.g. "1,2,5"): close each ref,
        # print one result line per task, and tolerate a mix of valid/invalid —
        # a bad ref is reported but doesn't abort the rest. A single number is
        # just a list of one.
        refs = _split_refs(ref)
        if not refs:
            print("No task matching '%s'.\n\n%s" % (ref, _format_list()))
            return
        for r in refs:
            print(_close_one(r, a.session or None))
        maybe_refresh_board()   # once after the batch — the board's lifecycle changed
        return

    if not a.session:
        print("Pass --task <id-or-number> to close a specific task, "
              "or --session <id> to close the session's attached task.")
        return
    task_id = get_link(a.session)
    task = load_task(task_id) if task_id else None
    if not task:
        print("No task is attached to this session. Nothing to close.")
        return
    if task.get("provisional"):
        # Untouched auto-tracked task: GC instead of leaving a closed-task husk.
        tid, ttl = task["id"][:8], task["title"]
        delete_task(task["id"])
        clear_link(a.session)
        clear_count(a.session)
        clear_edit_markers(a.session)
        maybe_refresh_board()   # a discarded task leaves the board too
        _maybe_close_session_window(a.session)   # opt-in; no-op unless enabled
        print("Discarded provisional task [%s] %s (auto-tracked, never engaged) and "
              "detached this session." % (tid, ttl))
        return
    # The heal gate, read BEFORE the status flips (see _done_gate_line).
    gate = _done_gate_line(task)
    task["status"] = STATUS_CLOSED          # close from open OR active
    stamp_closed(task)                      # real moment it entered closed
    touch(task, session=a.session, note="closed")
    _reap_task_workers(task, a.session)     # stop still-live --bg workers (#464)
    save_task(task)
    _mirror_child_close(task, a.session)    # tell any spawned-from parent's feed
    _obsidian_event(task, "closed")
    _stream_emit("task.status", task,
                 {"status": task.get("status"), "closed_ts": task.get("closed_ts")}, a.session)
    clear_link(a.session)   # detach so a later message can't silently reopen it
    clear_count(a.session)
    clear_edit_markers(a.session)   # deliberate wrap-up — don't let the Stop gate block
    maybe_refresh_board()   # board must show this closed NOW, not wait for the Stop hook
    _maybe_close_session_window(a.session)   # opt-in; no-op unless enabled
    print("Closed task [%s] %s and detached this session. Reopen later with /todo."
          % (task["id"][:8], task["title"]))
    rehome, rehome_note = _rehome_on_close(task, a.session)
    for line in rehome:
        print(line)
    pseq = report_to_parent(task, _close_headline(rehome_note), a.session)
    if pseq:
        print("  told #%s: this child is closed." % pseq)
    if gate:
        print(gate)


def cmd_delete(a):
    """HARD-delete a single task and detach any session linked to it.

    Maintenance escape hatch only — the lifecycle is close-not-delete (`done`
    closes the task and keeps its record; this removes the record entirely).
    Hidden from `--help`, the config board, and the README; documented only in
    `guidance` so the model can still reach for it. Removes EXACTLY the one
    resolved task — never the store."""
    task = resolve_ref(a.task) or load_task(a.task)
    if not task:
        print("No task matching '%s'." % a.task)
        return
    tid, seq, title = task["id"], task.get("seq"), task["title"]
    # Detach every session linked to this task so none is left pointing at a ghost
    # (mirrors the provisional-discard path in _close_one).
    for sess in list(task.get("sessions", [])):
        if get_link(sess) == tid:
            clear_link(sess)
            clear_count(sess)
            clear_edit_markers(sess)
    delete_task(tid)
    # Purge the task's exported notes so a hard-delete doesn't leave orphans behind:
    # the vault mirror note + any generic-export notes recorded in locatable sidecar
    # indexes, dropping each sidecar entry and refreshing that dir's index.md. Fully
    # best-effort — a gone/unwritable vault must never abort the delete, and the
    # tombstone is emitted regardless.
    try:
        import obsidian_sync, export as _export
        owner = _owner()
        v = _obsidian_vault()
        if v:
            obsidian_sync.remove_task_note(
                tid, obsidian_sync.owner_dir(obsidian_sync.plugin_dir(v), owner))
        for d in _export_dirs():
            scoped = obsidian_sync.owner_dir(d, owner)   # only this owner's subtree
            try:
                if obsidian_sync.remove_task_note(tid, scoped):
                    _export.rebuild_index(scoped)   # drop the note's line from the listing
            except Exception:
                pass
    except Exception:
        pass
    # Tombstone AFTER the row is gone — persist=False (nothing to persist the counter
    # onto), the in-hand dict carries the final n.
    _stream_emit("task.deleted", task, {}, getattr(a, "session", None), persist=False)
    print("Deleted task [%s] #%s %s." % (tid[:8], seq, title))


def _stream_human(e):
    """One compact human line for a stream event (the non-JSON `stream` view)."""
    t = e.get("task") or {}
    data = e.get("data") or {}
    preview = ""
    if e.get("event") == "task.updated":
        preview = ",".join(data.get("changed") or [])
    elif e.get("event") == "task.event":
        preview = "%s: %s" % (data.get("kind", ""), data.get("text", ""))
    elif e.get("event") == "task.status":
        preview = data.get("status", "")
    elif e.get("event") == "task.relation":
        preview = "→ #%s" % (data.get("other") or {}).get("seq")
    elif data.get("redacted"):
        preview = "(redacted)"
    return "n%-4s %s  %-16s #%-4s %s" % (
        e.get("n"), e.get("ts"), e.get("event"), t.get("seq"), preview)


def cmd_stream(a):
    """Read / maintain the durable Tasktrail ledger.

      --since <cursor> | --tail [N]   read events (cursor = 0-based global index)
      --json                          emit raw JSONL envelopes (else a human line)
      --backfill                      emit a task.snapshot per still-unstreamed task
      --verify                        check per-task n continuity + shard order

    Read paths do NOT swallow errors — a corrupt/unreadable ledger is reported."""
    import stream
    if getattr(a, "backfill", False):
        try:
            present = {(e.get("task") or {}).get("uuid") for e in stream.read_events()}
        except Exception as ex:
            sys.stderr.write("stream --backfill: cannot read existing stream: %s\n" % ex)
            return
        made = 0
        for t in sorted(all_tasks(), key=lambda t: t.get("seq") or 0):
            u = t.get("uuid") or t.get("id")
            if u in present:
                continue     # already represented — idempotent
            if _stream_emit("task.snapshot", t, _stream_digest(t), None) is not None:
                made += 1
        print("Backfilled %d task snapshot(s)." % made)
        return
    if getattr(a, "verify", False):
        res = stream.verify()
        if res["ok"]:
            print("stream verify OK — %d event(s) across %d task(s); "
                  "continuity + shard order intact." % (res["events"], res["tasks"]))
        else:
            print("stream verify FAILED (%d issue(s)):" % len(res["issues"]))
            for i in res["issues"]:
                print("  - %s" % i)
        return
    events = list(stream.read_events())
    tail = getattr(a, "tail", None)
    if tail is not None:
        k = tail if isinstance(tail, int) and tail > 0 else 20
        events = events[-k:]
    else:
        since = getattr(a, "since", None)
        if since is not None:
            try:
                cur = int(since)
            except (TypeError, ValueError):
                cur = 0
            events = events[cur:]
    as_json = getattr(a, "json", False)
    for e in events:
        print(json.dumps(e, ensure_ascii=False, sort_keys=True) if as_json
              else _stream_human(e))


def cmd_redact(a):
    """Right-to-be-forgotten: rewrite EVERY shard replacing task N's event payloads
    with a stub, bump the manifest generation, and append a task.redacted marker.
    Also rewrites the external tee when configured. Read/maintenance path — surfaces
    errors rather than swallowing them."""
    import stream, config
    task = resolve_ref(a.task) or load_task(a.task)
    if not task:
        print("No task matching '%s'." % a.task)
        return
    u = task.get("uuid") or task["id"]
    stubbed = stream.stub_task(u)
    tee = config.stream_dir()
    if tee:
        stream.stub_task(u, base=tee)
    gen = stream.bump_generation()
    if tee:
        stream.bump_generation(base=tee)
    # The task still exists (redact ≠ delete) — persist the marker's counter normally.
    _stream_emit("task.redacted", task, {"generation": gen}, getattr(a, "session", None))
    # Mark the task redacted so `export --prune` reconciles away its exported notes
    # (their content is being forgotten) even in dirs offline at redact time. Guarded.
    try:
        g("mutate")(task["id"], lambda t: t.__setitem__("redacted", True))
    except Exception:
        pass
    print("Redacted task #%s [%s]: stubbed %d payload(s); manifest generation now %d."
          % (task.get("seq"), task["id"][:8], stubbed, gen))


def _resolve_glossary_task(a):
    """The task a glossary command targets: --task <ref> wins, else the session's
    attached task."""
    ref = getattr(a, "task", None)
    if ref:
        return resolve_ref(ref) or load_task(ref)
    return _session_task(getattr(a, "session", None))


def _glossary_mutate(task_id, mutator, session, cap):
    """Run `mutator(task)` under mutate(), setting cap['changed'], and on a real
    change emit task.updated + sync Obsidian. Shared by add/edit/rm."""
    updated = g("mutate")(task_id, mutator)
    if updated is not None and cap.get("changed"):
        _stream_emit("task.updated", updated,
                     _stream_updated_data(updated, ["glossary"]), session)
        _obsidian_sync(updated)
    return updated


def cmd_glossary(a):
    """`task-station glossary [list|add|edit|rm] …` — the per-task canonical
    vocabulary. Resolution: --task <ref> > the --session attached task. A bare
    non-keyword first token (`glossary <task#>`) lists that task's terms.

    Grammar:
      glossary [list]                          list the resolved task's terms
      glossary <task#>                         list another task's terms
      glossary add "<name>" <layer> <state> "<def>"
      glossary edit "<name>" [--layer|--state|--def|--rename …]
      glossary rm "<name>"
    """
    action = (getattr(a, "action", None) or "list").lower()
    args = list(getattr(a, "args", None) or [])
    session = getattr(a, "session", None)
    KEYWORDS = {"list", "add", "edit", "rm"}

    # `glossary <ref>` — list another task's terms (bare non-keyword first token).
    if action not in KEYWORDS:
        task = resolve_ref(a.action) or load_task(a.action)
        if not task:
            print("glossary: unknown action or task %r — use list/add/edit/rm, "
                  "or a task number." % a.action)
            return
        print(_format_glossary(task))
        return

    task = _resolve_glossary_task(a)
    if not task:
        print("glossary: no task — attach a session or pass --task <ref>.")
        return
    tid = task["id"]
    seq = task.get("seq", tid[:8])

    if action == "list":
        print(_format_glossary(task))
        return

    name = args[0] if args else None
    if not name:
        print('glossary %s: give a term name, e.g. glossary %s "<name>" …' % (action, action))
        return

    if action == "add":
        layer = a.layer if a.layer is not None else (args[1] if len(args) > 1 else "")
        state = a.state if a.state is not None else (args[2] if len(args) > 2 else "")
        definition = a.definition if a.definition is not None else (args[3] if len(args) > 3 else "")
        cap = {}

        def _apply(t):
            cap["changed"] = add_glossary_term(t, name, layer, state, definition)
            if cap["changed"]:
                touch(t, session=session, note="glossary +%s" % name, register=False)
        updated = _glossary_mutate(tid, _apply, session, cap)
        print(_format_glossary(updated or task))
        return

    if action == "edit":
        cap = {}

        def _apply(t):
            r = edit_glossary_term(t, name, layer=a.layer, state=a.state,
                                   definition=a.definition, rename=a.rename)
            cap["result"] = r
            cap["changed"] = bool(r)
            if r:
                touch(t, session=session, note="glossary ~%s" % name, register=False)
        try:
            updated = _glossary_mutate(tid, _apply, session, cap)
        except ValueError as e:
            print("glossary edit: %s" % e)
            return
        if cap.get("result") is None:
            print("glossary edit: no term named %r on task #%s." % (name, seq))
            return
        print(_format_glossary(updated or task))
        return

    if action == "rm":
        cap = {}

        def _apply(t):
            cap["changed"] = remove_glossary_term(t, name)
            if cap["changed"]:
                touch(t, session=session, note="glossary -%s" % name, register=False)
        updated = _glossary_mutate(tid, _apply, session, cap)
        if not cap.get("changed"):
            print("glossary rm: no term named %r on task #%s." % (name, seq))
            return
        print("Removed '%s' from task #%s glossary." % (name, seq))
        return


def cmd_glossary_context(a):
    """`task-station glossary-context [--task|--session]` — print the injectable
    glossary block for a task (the adapter hook non-Claude hosts emit through their
    own prompt channel). Silent when the task has no terms."""
    task = _resolve_glossary_task(a)
    if not task:
        return
    block = glossary_context(task)
    if block:
        print(block)


# ============================ F5 — correspondence ============================
# Collaboration WITHOUT shared writes: link (record a peer pair), fork (copy a peer node's
# digest into my own task + provenance), subscribe (mint memos when the peer feed advances),
# and per-node trail_visibility (what my feed exports). All read CANONICAL peer feeds
# (feeds/{peers,demo}/*.js — the `window.__TSFEED_<alias> = {json};` form real sync
# produces + tests seed + the demo fixtures use as of #444). Correspondence targets that
# canonical form, so it is sync-ready; any legacy non-canonical file is skipped, not fatal.

def cmd_link(a):
    """`task-station link --task <ref> --peer <alias>-<n|uuid8>` — record a correspondence
    pair between my task and a peer feed task. One-way storage (peer feeds are read-only);
    the pair renders on the detail, the board row, and as a dashed graph edge."""
    task = resolve_ref(getattr(a, "task", None))
    if not task:
        print("link: no task matching %r" % getattr(a, "task", None))
        return
    feed, ftask = _resolve_peer_ref(getattr(a, "peer", None))
    if not ftask:
        print("link: no peer task matching %r (looked in canonical feeds/{peers,demo}/*.js)"
              % getattr(a, "peer", None))
        return
    alias = feed.get("alias") or feed.get("owner")
    u8 = (ftask.get("uuid8") or "")[:8]
    handle = ftask.get("handle") or ("%s-%s" % (alias, u8))
    label = task.get("seq", task["id"][:8])
    if add_link(task, alias, u8, handle):
        add_event(task, "link", "linked ↔ %s: %s" % (handle, ftask.get("title") or ""),
                  getattr(a, "session", None))
        task["updated_ts"] = _now()
        save_task(task)
        _obsidian_sync(task)
        print("linked #%s ↔ %s (%s)" % (label, handle, ftask.get("title") or ""))
    else:
        print("link: #%s is already linked to %s — no change" % (label, handle))


def cmd_fork(a):
    """`task-station fork --from <alias>-<n|uuid8> [--title ...]` — create MY task from a
    peer feed node: copy its digest (goal/state/decisions + any summary/glossary/steps the
    feed carries), record `forked_from` provenance (alias, uuid8, at_rev), auto-link the
    pair, and auto-attach to a brain (F4). The peer feed is never mutated."""
    feed, ftask = _resolve_peer_ref(getattr(a, "from_ref", None))
    if not ftask:
        print("fork: no peer task matching %r (looked in canonical feeds/{peers,demo}/*.js)"
              % getattr(a, "from_ref", None))
        return
    alias = feed.get("alias") or feed.get("owner")
    u8 = (ftask.get("uuid8") or "")[:8]
    handle = ftask.get("handle") or ("%s-%s" % (alias, u8))
    digest = ftask.get("digest") or {}
    title = (getattr(a, "title", None) or ftask.get("title") or "Forked task").strip()
    summary = (ftask.get("summary") or digest.get("goal") or "").strip()
    # map the peer category key to a local category (unknown → default/none).
    cat = ftask.get("category")
    color = cat.get("key") if isinstance(cat, dict) else (cat or None)
    task = new_task(title, summary, color=color, effort=ftask.get("effort"))
    # DIGEST DOWNLOAD — copy every digest field the feed carries.
    if digest.get("goal"):
        task["goal"] = digest["goal"].strip()
    if digest.get("state"):
        task["state"] = digest["state"].strip()
    # The feed already stripped superseded decisions; coerce to plain text so a peer
    # writing the rich shape can never land a dict in my own decisions log.
    dec = [t for t in (_dec.text(d) for d in (digest.get("decisions_tail") or [])) if t]
    if dec:
        task["decisions"] = list(dec)
    if isinstance(ftask.get("steps"), list) and ftask["steps"]:
        # ACTIVE steps only, coerced to the plain shape: a step the peer retired is not
        # work I am inheriting, and my checklist starts from what is still to do.
        task["steps"] = [{"text": _steps.text(s), "done": _steps.is_done(s)}
                         for _i, s in _steps.live(ftask["steps"])]
    if isinstance(ftask.get("glossary"), list) and ftask["glossary"]:
        task["glossary"] = list(ftask["glossary"])
    # PROVENANCE — where this fork came from + the peer feed rev at fork time.
    task["forked_from"] = {"alias": alias, "uuid8": u8, "handle": handle,
                           "title": ftask.get("title") or "", "at_rev": _feed_rev(feed),
                           "ts": _now()}
    add_link(task, alias, u8, handle, kind="fork")
    create_with_seq(task)              # mint seq + persist (forked_from/links ride along)
    brain = auto_attach_brain(task, getattr(a, "session", None))
    add_event(task, "fork", "forked from %s (%s) @rev %s"
              % (handle, ftask.get("title") or "", task["forked_from"]["at_rev"]),
              getattr(a, "session", None))
    touch(task, note="forked from %s" % handle)
    save_task(task)
    _obsidian_sync(task)
    print("📋 Forked %s → task #%s [%s] %s (brain: %s). /todo %s -s starts a session."
          % (handle, task.get("seq"), task["id"][:8], task["title"], brain, task.get("seq")))
    for line in cat_lines(task.get("color")):
        print(line)


# -- F5.3 subscriptions: mint memos when a subscribed peer feed advances --------

def _subscriptions_check(session=None):
    """Diff every subscribed link's peer feed rev vs its last-seen rev; when it advanced,
    mint ONE memo onto my task (idempotent per rev — last_rev is bumped immediately) and
    persist. Returns the number of memos minted. Fail-open: a bad feed is skipped."""
    feeds = {}
    try:
        for feed in _all_peer_feeds():
            feeds[feed.get("alias") or feed.get("owner")] = feed
    except Exception:
        return 0
    minted = 0
    for t in sorted_tasks():
        changed = False
        for l in (t.get("links") or []):
            s = l.get("subscribe")
            if not s:
                continue
            feed = feeds.get(l.get("alias"))
            if not feed:
                continue
            rev = _feed_rev(feed)
            if not rev or rev == s.get("last_rev"):
                continue                       # unchanged → idempotent no-op
            ftask = _feed_task(feed, l.get("uuid8"))
            text = _subscription_memo_text(l, feed, ftask)
            if text:
                memo_send(t, text, from_sid=None, routine=True)
                minted += 1
            s["last_rev"] = rev
            changed = True
        if changed:
            t["updated_ts"] = _now()
            save_task(t)
            _obsidian_sync(t)
    return minted


def cmd_subscribe(a):
    """`task-station subscribe --task <ref> --peer <alias>-<ref> --on checkpoint,decision,
    trail` — watch a peer feed task; a later `subscriptions check` mints a memo when it
    advances. Ensures the link exists, then stores the subscription ON that link with a
    baseline rev (so only FUTURE changes mint)."""
    task = resolve_ref(getattr(a, "task", None))
    if not task:
        print("subscribe: no task matching %r" % getattr(a, "task", None))
        return
    feed, ftask = _resolve_peer_ref(getattr(a, "peer", None))
    if not ftask:
        print("subscribe: no peer task matching %r" % getattr(a, "peer", None))
        return
    alias = feed.get("alias") or feed.get("owner")
    u8 = (ftask.get("uuid8") or "")[:8]
    handle = ftask.get("handle") or ("%s-%s" % (alias, u8))
    on = [k.strip() for k in (getattr(a, "on", "") or "").split(",") if k.strip()]
    add_link(task, alias, u8, handle)          # ensure a link to hang the subscription on
    for l in task.get("links") or []:
        if l.get("alias") == alias and (l.get("uuid8") or "")[:8] == u8:
            l["subscribe"] = {"on": on, "last_rev": _feed_rev(feed)}
            break
    task["updated_ts"] = _now()
    save_task(task)
    _obsidian_sync(task)
    print("subscribed #%s → %s on %s (baseline rev %s)"
          % (task.get("seq", task["id"][:8]), handle, ",".join(on) or "—", _feed_rev(feed)))


def cmd_subscriptions(a):
    """`task-station subscriptions <check|list>`. `check` diffs subscribed peer feeds and
    mints memos (the on_stop-hook path passes --throttle so it self-throttles + stays
    silent); `list` prints every active subscription. Fail-open."""
    sub = getattr(a, "sub", None) or "check"
    if sub == "list":
        any_ = False
        for t in sorted_tasks():
            for l in (t.get("links") or []):
                s = l.get("subscribe")
                if s:
                    any_ = True
                    print("#%s → %s on %s (last_rev %s)"
                          % (t.get("seq", t["id"][:8]), l.get("handle"),
                             ",".join(s.get("on") or []) or "—", s.get("last_rev")))
        if not any_:
            print("(no subscriptions)")
        return
    if getattr(a, "throttle", False) and _subs_throttled():
        return                                 # cheap silent no-op on the hook path
    minted = _subscriptions_check(getattr(a, "session", None))
    if not getattr(a, "throttle", False):
        print("subscriptions check: minted %d memo(s)" % minted)


# ---- THE WORK-BOUNDARY SCHEDULER: steps 2-4 at the seam -----------------------
#
# `lib/board/timing.py` holds the whole policy and cannot reach a task, a transcript or a
# filesystem. THIS is the half that gathers the facts and performs the writes, and the split
# is the reason the policy is testable against hand-built dicts while the gathering is
# testable against a real store.

def _boundary_facts(task, session):
    """The four in-flight inputs `timing.boundary` needs, gathered from what already exists.

    NOTHING NEW MEASURES ANYTHING HERE. Orders and pickups are fields on the task the Stop
    gate has already loaded; the git operation is a handful of `os.path.exists` calls under
    one `.git`; the untracked-edit marker is the same one the gate reads. The whole point of
    the feature is that both halves of "is this a boundary" already shipped."""
    try:
        orders = len(_channel.orders_for(task, session) or [])
    except Exception:                                   # noqa: BLE001
        orders = 0
    try:
        pickups = len(_channel.pickups_pending(task) or [])
    except Exception:                                   # noqa: BLE001
        pickups = 0
    git_op = _timing.git_operation_in_progress(os.getcwd())
    try:
        edits = bool(has_edited(session) and not get_link(session))
    except Exception:                                   # noqa: BLE001
        edits = False
    return {"orders": orders, "pickups": pickups, "git_op": git_op,
            "untracked_edits": edits}


def _record_shape(task):
    """A cheap fingerprint of everything a mechanical finding could come from — the decision,
    step and memo counts, plus the last-heal stamp.

    THIS IS THE SCAN'S COST GATE, and it is the only honest one available. `heal.cheap_limbs`
    cannot serve as it: the limb that would have mattered here — "the scan found N issue(s)" —
    is precisely the one a cheap read cannot evaluate, so gating on the cheap limbs would let
    an oversized entry sit unreconciled until twenty-five unrelated decisions had accrued. A
    turn that wrote nothing to the record cannot have created a finding, so THAT is what the
    scan is gated on: the record moved."""
    return _nudges.signature([
        "d:%d" % len(task.get("decisions") or []),
        "s:%d" % len(task.get("steps") or []),
        "m:%d" % len(task.get("memos") or []),
        "h:%s" % (task.get("last_heal_ts") or ""),
    ])


def _boundary_schedule(task, session, scan_ops=True):
    """`(schedule, extras)` — the whole scheduler verdict for this task/session right now.

    `extras` carries what the CALLER needs and the schedule dict deliberately does not: the
    window resolution (so a report can say which source won), the succession report, and the
    mechanical ops actually planned. Kept out of the schedule because `timing.schedule` is
    pure and must stay constructible without any of them.

    `scan_ops=False` skips the corpus scan and reports zero mechanical operations — the
    boundary pass passes False on a turn where the record did not move (see `_record_shape`).
    `timing` always passes True, because a report that quietly skipped a check would be the
    thing this codebase calls a false green."""
    facts = _boundary_facts(task, session)
    bstate = _timing.boundary(orders=facts["orders"], pickups=facts["pickups"],
                              git_op=facts["git_op"],
                              untracked_edits=facts["untracked_edits"])
    ops, limbs = [], []
    if scan_ops:
        try:
            result = _heal.scan(task)
            ops = _heal.plan(task, result=result)
            # `due`'s limbs, in `due`'s order and `due`'s wording — the scan limb first, then
            # the four blob limbs. Read off the result rather than recomputed, so the
            # scheduler and `heal` can never disagree about what is owed.
            _due, reasons = _heal.due(task, result=result)
            limbs = [("findings" if i == 0 and result.get("findings") else "limb", text)
                     for i, text in enumerate(reasons)]
        except Exception:                               # noqa: BLE001
            ops, limbs = [], []
    else:
        try:
            limbs = _heal.cheap_limbs(task)
        except Exception:                               # noqa: BLE001
            limbs = []
    try:
        gap = _save.since_checkpoint(task)
    except Exception:                                   # noqa: BLE001
        gap = {}
    res = window_resolution(session)
    measured = measure_context_tokens(session)
    rep = _succ.report(task, measured, res["window"], session=session)
    hand = _timing.handoff_due(rep["verdict"], rep["ready"], rep["blockers"],
                               boundary_safe=bstate["safe"],
                               relay=_succ.RELAY, compact=_succ.COMPACT)
    sched = _timing.schedule(bstate, heal_limbs=limbs, checkpoint_gap=gap, handoff=hand,
                             auto_ops=len(_heal.auto_ops(ops)))
    return sched, {"window": res, "relay": rep, "ops": ops, "facts": facts}


def _boundary_signature(sched):
    """The state fingerprint the boundary pass throttles on — the heal LIMBS (not their
    counts), the handoff state, and whether the auto class has anything to do.

    LIMBS RATHER THAN COUNTS, for the reason `nudges._signature` spells out: every reason this
    scheduler reports carries a number that moves on its own, so hashing the worded reason
    would re-arm the throttle on every single turn and a pass built to act once would act
    forever."""
    parts = [l for l, _t in (sched.get("heal_limbs") or [])]
    parts.append("handoff:%s" % sched.get("handoff"))
    parts.append("auto:%s" % ("yes" if sched.get("auto_fires") else "no"))
    return _nudges.signature(parts)


def _checkpoint_mark_text(sched, extras):
    """The LIGHT CHECKPOINT's one history entry — facts only, and it says so.

    It records the occupancy, the window AND WHICH SOURCE SUPPLIED IT, and what has accrued
    since the last full checkpoint. It does NOT stamp a checkpoint and never will: this
    codebase already ruled that `last_full_save_ts` means one thing, that a stamp is a claim
    that work was captured, and that only work may make it. A machine cannot author the prose
    a digest is, so the most it may honestly write is the arithmetic — which is the half that
    is expensive to reconstruct an hour later, and free right now."""
    res = extras["window"]
    rep = extras["relay"]
    # AN UNMEASURED OCCUPANCY IS NOT 0%, and this entry is written into the record where
    # it long outlives the turn that wrote it. `used_pct` is None exactly when no usage
    # block could be read; `or 0` rendered that as a session that had burned nothing.
    occ = ("occupancy unknown — not measured, against a %s-token window"
           % "{:,}".format(res.get("window") or 0)
           if rep.get("used_pct") is None
           else "~%d%% of a %s-token window used"
                % (rep["used_pct"], "{:,}".format(res.get("window") or 0)))
    return ("boundary mark: %s (window source: %s) · %d record "
            "change(s) since the last full checkpoint. Facts only — no checkpoint was "
            "stamped and the digest was not touched."
            % (occ, res.get("source"), sched.get("checkpoint_accrued") or 0))


def _boundary_maintain(a, task):
    """The AUTO class at a work boundary: run it, then say what it did. Returns ONE line for
    the Stop hook's additionalContext, or None.

    THE ORDER OF THE GUARDS IS THE POLICY:

      1. OFF unless asked. `boundary_maintenance` is default-off — see the note in `config`.
      2. NOT AT A BOUNDARY, NOTHING HAPPENS. Not a quieter version of the pass: nothing. A
         maintenance line printed mid-flight is the furniture this feature exists to remove.
      3. ONCE PER STATE. The same watermark the prompt nudges use, so a session that sits at a
         boundary for six turns acts once and is silent five times.
      4. MERGES ARE EXCLUDED BY CLASS. `heal.auto_ops` filters on the VERB, and
         `timing.may_run_unattended` is asserted here as well — two independent readings of
         the same rule, because this is the one place where getting it wrong writes a false
         consolidation into somebody's record.

    Every write is preceded by the same pre-heal backup `heal --apply` takes, and the report
    names it. Fails open and silent: an exception here must never cost a turn."""
    try:
        if not _config_boundary_enabled():
            return None
        tid = task.get("id")
        # CHEAP FIRST, ALWAYS. The boundary is four blob reads and a handful of stats; a turn
        # that is not at a boundary leaves here having touched no corpus at all.
        facts = _boundary_facts(task, a.session)
        if not _timing.boundary(orders=facts["orders"], pickups=facts["pickups"],
                                git_op=facts["git_op"],
                                untracked_edits=facts["untracked_edits"])["safe"]:
            return None
        # THE SCAN IS PAID FOR ONLY WHEN THE RECORD MOVED. Recorded whether or not this pass
        # goes on to act, because the statement being stored is "this shape has been scanned",
        # not "this shape was acted on" — conflating the two would re-scan the same corpus at
        # every turn end for as long as nothing was owed.
        shape = _record_shape(task)
        scan_ops = not _nudges.acted(tid, _nudges.SHAPE_KEY, a.session, shape)
        sched, extras = _boundary_schedule(task, a.session, scan_ops=scan_ops)
        if scan_ops:
            _nudges.record_acted(tid, _nudges.SHAPE_KEY, a.session, shape)
        if not sched["safe"]:
            return None
        sig = _boundary_signature(sched)
        if _nudges.acted(tid, _nudges.BOUNDARY_KEY, a.session, sig):
            return None
        auto = _heal.auto_ops(extras["ops"])
        held = _heal.held_ops(extras["ops"])
        hand_state = sched.get("handoff")
        if not auto and hand_state not in (_timing.HANDOFF_PROMPT, _timing.HANDOFF_BLOCKED,
                                           _timing.HANDOFF_MISSED):
            return None                       # nothing owed at this boundary — stay silent
        _nudges.record_acted(tid, _nudges.BOUNDARY_KEY, a.session, sig)
        seq = task.get("seq") or str(task.get("id") or "")[:8]
        done, backup = [], None
        # THE SECOND READING OF THE SAME RULE, and it is a refusal rather than an `assert`,
        # because an assert vanishes under `python -O` and this is the one guard whose failure
        # writes a false consolidation into somebody's record. `heal.auto_ops` filtered on the
        # verb; this asks `timing.classify` independently. If the two ever disagree, nothing
        # runs.
        if auto and not (_timing.may_run_unattended(_timing.ACTION_HEAL_MECHANICAL)
                         and all(o.get("verb") in _heal.AUTO_VERBS for o in auto)):
            return ("[task-station] Work boundary on #%s — the AUTO maintenance class REFUSED "
                    "to run: the plan filter and the action classifier disagree about what may "
                    "run unattended, and nothing runs while they do. Nothing was changed. Run "
                    "`/todo heal` by hand." % seq)
        if auto:
            backup = _heal.backup(task, strip=_store.strip_rev)
            if backup:
                session = a.session

                def _append(text, _t=task, _s=session):
                    if not append_decision(_t, text, _s):
                        return None
                    return len(_t.get("decisions") or [])

                lines, applied, _skipped = _heal.apply(task, auto, append=_append)
                if applied:
                    _heal.stamp_healed(task)
                    done.extend(lines)
                append_history(task, _checkpoint_mark_text(sched, extras),
                               session=a.session)
                task["updated_ts"] = _now()
                save_task(task)
                _heal.clear_gate(tid)
        parts = ["[task-station] Work boundary on #%s — nothing in flight, so the AUTO "
                 "maintenance class ran. THIS IS A REPORT, NOT A REQUEST." % seq]
        if done:
            parts.append("Applied %d mechanical operation(s): %s."
                         % (len(done), "; ".join(done)))
            undo = _heal.undo_lines(extras["ops"])
            if undo:
                parts.append("Each one is reversible: %s" % " ".join(undo))
            if backup:
                parts.append("Pre-heal blob: %s." % backup)
        elif auto:
            parts.append("Refused to write: the pre-heal backup could not be taken, so "
                         "nothing was changed.")
        if held:
            parts.append("%d operation(s) were HELD, not skipped: %s. A merge is never "
                         "automatic — its summary has to be true of all its members at once "
                         "and no verb unsays it when it is not. Run `/todo heal` to decide "
                         "them by hand."
                         % (len(held), ", ".join(sorted(set(o.get("verb") or "?"
                                                            for o in held)))))
        if hand_state == _timing.HANDOFF_PROMPT:
            parts.append("HANDOFF IS DUE and this is the cheap moment to take it: %s "
                         "`task-station relay --task %s` reports it; `relay --spawn` "
                         "performs it. Never automatic — it ends this session."
                         % (sched.get("handoff_why"), seq))
        elif hand_state == _timing.HANDOFF_BLOCKED:
            parts.append("HANDOFF WITHHELD: %s" % sched.get("handoff_why"))
        elif hand_state == _timing.HANDOFF_MISSED:
            parts.append("HANDOFF: %s" % sched.get("handoff_why"))
        return " ".join(parts)
    except Exception:                                   # noqa: BLE001
        return None


def _config_boundary_enabled():
    try:
        import config
        return config.boundary_maintenance_enabled()
    except Exception:                                   # noqa: BLE001
        return False


def cmd_timing(a):
    """`task-station timing [--task REF] [--json]` — the scheduler's whole verdict, and it
    WRITES NOTHING.

    Three questions in one report: is this a work boundary (and if not, what is in flight),
    what does the record say is owed, and what would the AUTO class do about it. The point of
    a read-only surface is that the scheduler can be inspected BEFORE `--boundary-maintenance`
    is turned on — a feature whose first observable behaviour is an unattended write to your
    record is a feature nobody switches on."""
    task, err = _timing_target(a)
    if err:
        print(err)
        sys.exit(2)
    sched, extras = _boundary_schedule(task, getattr(a, "session", None))
    if getattr(a, "as_json", False):
        out = dict(sched)
        out["window"] = extras["window"]
        out["relay"] = extras["relay"]
        out["held_ops"] = [o.get("verb") for o in _heal.held_ops(extras["ops"])]
        print(json.dumps(out, indent=2, sort_keys=True, default=str))
        return
    seq = task.get("seq") or str(task.get("id") or "")[:8]
    print("Timing — #%s %s" % (seq, task.get("title")))
    for line in window_lines(extras["window"]):
        print(line)
    for line in _timing.schedule_lines(sched):
        print(line)
    held = _heal.held_ops(extras["ops"])
    if held:
        print("  %-13s %d op(s) a machine may NEVER run unattended: %s"
              % ("never auto", len(held),
                 ", ".join(sorted(set(o.get("verb") or "?" for o in held)))))
    print("  %-13s %s" % ("writes", "none — this is the report. "
                          "`config --boundary-maintenance on` lets the AUTO class act."
                          if not _config_boundary_enabled() else
                          "none from this command; the AUTO class is ENABLED and acts at "
                          "the next safe boundary."))


def _timing_target(a):
    """The task `timing` reports on, as `(task, error_line)` — `--task <ref>` by seq/id, else
    the session's attached task. The same resolution every task-scoped command uses."""
    ref = getattr(a, "task", None)
    if ref:
        task = resolve_ref(ref) or load_task(ref)
        if not task:
            return None, "timing: no task matching '%s'." % ref
        return task, None
    task = _session_task(getattr(a, "session", None))
    if not task:
        return None, ("timing: no task attached — name one with `timing --task <n>`.")
    return task, None


def cmd_window(a):
    """`task-station window [--session SID] [--json]` — the context window this session is
    actually measured against, AND WHICH SOURCE SUPPLIED IT.

    THIS COMMAND EXISTS BECAUSE A NUMBER WITH NO PROVENANCE WAS WRONG BY 5x FOR MONTHS AND
    NOTHING COULD HAVE SAID SO. `--context-window` was 200,000 on a machine running a
    1,000,000-token model, so the checkpoint nudge fired at ~13% of the real budget on every
    session in silence. The arithmetic was never wrong; the denominator was, and a bare
    integer cannot be questioned. So this prints the window, the source that won, and — when
    a stored override disagrees with what the session detects — says so in those words."""
    res = window_resolution(getattr(a, "session", None))
    if getattr(a, "as_json", False):
        print(json.dumps(res, indent=2, sort_keys=True, default=str))
        return
    for line in window_lines(res):
        print(line)
