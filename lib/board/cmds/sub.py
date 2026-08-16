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
import json
import os
import sys

import checker as _checker
import config_change as _config_change
import decisions as _dec
import heal as _heal
import hook_health
import paths
import steps as _steps
import worktree_hook as _worktree_hook

g, set_g = _shared.g, _shared.set_g

__all__ = [
    "_is_substantive_tracked", "cmd_create", "cmd_attach", "cmd_bump", "cmd_skip",
    "cmd_detach", "_open_tasks_brief", "cmd_mark_edited", "cmd_touch_file",
    "cmd_stop_gate", "cmd_post_compact", "cmd_stop_nudge",
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
    g("_emit_title_to_origin")(task)           # label the window NOW, not next prompt


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
    g("_emit_title_to_origin")(task)           # relabel the window NOW on attach


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


def cmd_stop_gate(a):
    """Stop hook: refuse to end the turn if this session edited files but never
    tracked a task. Self-healing — clears its markers the moment a task is
    attached or the session is skipped — and capped at STOP_GATE_MAX_BLOCKS so a
    non-complying loop can't wedge the session."""
    if os.environ.get("TASK_STATION_GATE") == "off":
        return
    if not has_edited(a.session):
        return                              # no untracked edits → nothing to enforce
    link = get_link(a.session)
    if link:                                # real task attached, or skipped
        clear_edit_markers(a.session)
        return
    if get_blocked(a.session) >= STOP_GATE_MAX_BLOCKS:
        clear_edit_markers(a.session)       # gave it two tries — don't wedge the session
        return
    bump_blocked(a.session)
    reason = (
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
    print(json.dumps({"decision": "block", "reason": reason}))


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
    2. LIGHT staleness nudge — only when the pressure trigger did NOT fire and the digest
       is stale. Activity-gated by checkpoint_milestone_edits: it holds until N meaningful
       events (edits / promotions) have accrued since the last refresh (default 5), so a
       couple of small edits no longer nudge; 0/off restores nudge-on-any-staleness.

    Never emits both in one Stop. Prints nothing unless auto-checkpoint is ON and a task
    is attached — so it never fires on today's default setup. Deliberately NOT a block
    (no decision:block) — avoids the Stop gate's block cap / hard interrupts. Best-effort:
    the Stop hook emits whatever this prints."""
    if not _auto_checkpoint_enabled():
        return
    task = _session_task(a.session)
    if not task:
        return
    try:
        import config
        pct = config.checkpoint_pct()
        # Size the window to the model actually in use (Opus-1M → 1M, Haiku/Sonnet →
        # 200k) unless the user has explicitly set context_window. A fixed 200k
        # denominator on a 1M model reads ~5x over-full and fires this nudge almost
        # every Stop — the "saves too often / percentages look reversed" bug.
        window = effective_context_window(a.session)
        thresh_abs = config.checkpoint_at()
        milestone = config.checkpoint_milestone_edits()
    except Exception:
        pct, window, thresh_abs, milestone = 0, 200000, 0, 0
    # 1. Proactive context-pressure trigger (takes precedence over the staleness nudge).
    #    checkpoint_pct (measured) is the default path; checkpoint_at (estimated) is the
    #    absolute back-compat fallback. Either crossing fires the same nudge.
    measured = measure_context_tokens(a.session) if pct > 0 else 0
    pct_hit = pct > 0 and window > 0 and measured >= (pct * window) // 100
    est = estimate_session_tokens(a.session) if thresh_abs > 0 else 0
    abs_hit = thresh_abs > 0 and est >= thresh_abs
    if (pct_hit or abs_hit) and not task.get("pressure_nudged"):
        seq = task.get("seq", task["id"][:8])
        # Prefer the real measurement in the copy (percent + tokens); fall back to the
        # byte-size estimate's token count when only the absolute trigger fired.
        if measured > 0:
            pct_now = round(measured * 100 / window) if window else 0
            left = max(0, 100 - pct_now)
            # Report BOTH used and remaining so the figure can't be misread against
            # Claude's native "% left" indicator. The nudge fires as the window FILLS
            # (used ≥ checkpoint_pct), i.e. precisely when little context is left.
            amount = "~%d%% used · ~%d%% left (~%dk/%dk tokens)" % (
                pct_now, left, measured // 1000, window // 1000)
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
                "auto-summary. Then continue, or open a fresh session and `/todo %s` to "
                "resume from the digest." % (who, amount, seq, seq))
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "Stop", "additionalContext": line}}))
        return
    # 2. Light staleness nudge — only when pressure did not fire.
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
    pseq = report_to_parent(task, "CLOSED — ready for the gate (`/grade`)", session)
    if pseq:
        line += "\n  told #%s: this child is closed." % pseq
    return (line + "\n" + gate) if gate else line


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
    pseq = report_to_parent(task, "CLOSED — ready for the gate (`/grade`)", a.session)
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
                memo_send(t, text, from_sid=None)
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
