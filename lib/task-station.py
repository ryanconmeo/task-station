#!/usr/bin/env python3
"""Task Station — persistent, cross-session task tracking for Claude Code.

Engine for the /todo and /done commands and the SessionStart / UserPromptSubmit
hooks. Tasks live as one JSON file per task under store/tasks/. A session is
"attached" to at most one task via a link file under store/links/<session_id>.

Subcommands:
  create  --session ID --title T --summary S   create a task, attach the session
  attach  --session ID --task REF              attach session to an existing task
  bump    --session ID                          touch the attached task's activity
  skip    --session ID                          mark session intentionally untracked (silences nudge)
  done    --session ID                          close the attached task
  render  --session ID --arg STR                /todo entrypoint (list | detail+attach)
  prompt-context --session ID                   UserPromptSubmit hook context
  session-start  --session ID --source SRC      SessionStart hook context
  guidance                                      full attach/create how-to (on demand)

REF is a 1-based index from the most recent `render` listing, or a task id /
id-prefix. All writes are atomic (temp file + os.replace).
"""

import argparse
import atexit
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

import checker as _checker
import config_change as _config_change
import decisions as _dec
import heal as _heal
import hook_health
import knowledge as _knowledge
import paths
import save as _save
import steps as _steps
import store
import worktree_hook as _worktree_hook

BASE = os.path.dirname(os.path.abspath(__file__))  # code location only (self-invocation)
DATA = paths.data_dir()                             # mutable state — survives /plugin update
STORE = os.path.join(DATA, "store")
TASKS_DIR = os.path.join(STORE, "tasks")
LINKS_DIR = os.path.join(STORE, "links")
PENDING_BRIEFS = os.path.join(DATA, "pending-briefs")
DELEGATE_REGISTRY = os.path.join(DATA, "workers.json")
_LIVE_BG_INDEX = None   # per-process snapshot of `claude agents --json` (bg-aware resume); None = not yet queried
PROJECTS_ROOT = os.path.join(
    os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")), "projects")

# ----------------------------------------------------------------- facade ----
# 3.0.0 splits the engine into lib/board/*. THIS file stays the FACADE: the config
# block above, every seam not yet split, and a star-import of each split module in
# layer order — so every historical `ts.<name>` still resolves here. 77 test files
# load this module as a fresh copy by literal path and patch `ts.<name>` directly;
# the split modules read those names back through `_shared.g("NAME")`.
#
# The purge is what keeps those copies independent: each new copy drops the previous
# copy's `board.*` modules from sys.modules so it imports its OWN generation, and
# that generation binds to THIS module's globals.
for _m in [m for m in list(sys.modules) if m == "board" or m.startswith("board.")]:
    del sys.modules[_m]

import board._shared as _bshared                    # noqa: E402
_bshared.bind(globals())

from board._shared import *      # noqa: F401,F403,E402
from board.state import *        # noqa: F401,F403,E402
from board.model import *        # noqa: F401,F403,E402
from board.memos import *        # noqa: F401,F403,E402
from board.sessions import *     # noqa: F401,F403,E402
from board.render import *       # noqa: F401,F403,E402
from board.graph import *        # noqa: F401,F403,E402
from board.boardio import *      # noqa: F401,F403,E402
from board.cmds.maintain import *  # noqa: F401,F403,E402
from board.cmds.manage import *    # noqa: F401,F403,E402


# ---------------------------------------------------- transcript-derived caches ----
# A session transcript is APPEND-ONLY, and everything we derive from one — the user
# message count, the prompt→reply map — is a pure function of its bytes. So
# (st_mtime_ns, st_size) is a COMPLETE cache key: any change to the file changes one
# of them, which means a cache hit can never be stale. There is no invalidation
# window to reason about, and no need to guess whether a transcript is "done".
#
# This matters because the board asks the same questions over and over. Rendering
# 375 tasks over 458 transcripts called _session_msgcount 4072 times (one file was
# re-parsed 120 times) and _prompt_replies 571 times, each one re-reading a whole
# transcript: 2.37M json.loads for ~460 files' worth of information, and a Stop hook
# that blocked turn end for ~22s.
#
# Two layers:
#   • in-process — collapses the repeats WITHIN one render (4072 parses → 458).
#   • on-disk    — <data_dir>/cache/msgcounts.json, so a transcript that has not
#     changed is not re-parsed on the NEXT turn either. Counts ONLY: reply text is
#     prompt content and is never persisted, and the in-process layer already covers
#     the single render that needs it.
#
# Every layer is fail-open. This code runs inside the Stop hook, where an exception
# would block the user's turn, so a missing, malformed, or foreign cache file is
# ignored and the value simply recomputed. A cache is never a correctness dependency.

REPLIES_CACHE_MAX = 256              # reply maps held in memory at once (bounds a big render)
MSGCOUNT_MEM_MAX = 8192              # in-memory counts kept — a growing transcript mints a
                                     # new key per append, and the MCP server is long-lived

_MSGCOUNT_DISK = None    # {"file", "entries": {path: [mtime_ns, size, count, used]}, "dirty"}


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
    path = _find_session_path(session)
    return bool(path) and _session_msgcount(path) >= SUBSTANCE_FLOOR


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
    _emit_title_to_origin(task)                # label the window NOW, not next prompt


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
    _emit_title_to_origin(task)                # relabel the window NOW on attach


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
            mutate(link, _apply)
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
    mutate(task["id"], _apply)


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
        script = os.path.join(BASE, "close-session-window.sh")
        if not os.path.exists(script):
            return
        subprocess.Popen(["bash", script, "--detach", "--after", "1"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
        mutate(task["id"], lambda t: t.__setitem__("redacted", True))
    except Exception:
        pass
    print("Redacted task #%s [%s]: stubbed %d payload(s); manifest generation now %d."
          % (task.get("seq"), task["id"][:8], stubbed, gen))


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
    script = os.path.join(BASE, "open-session-window.sh")
    if not os.path.exists(script):
        return False
    try:
        r = subprocess.run(["bash", script, cmd],
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
        path = _find_session_path(sid)
        if path and _session_msgcount(path) >= 1:
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
    opened = _open_jump_window(resume) if resume else False
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
    opened = _open_jump_window(resume) if resume else False
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
    updated = mutate(task_id, mutator)
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


# ---------------------------------------------------------------- brief ---------

def _brief_provenance_sessions(task):
    """Roster rows for the brief's Sessions table (#463), derived from session_meta:
    hubs first (by ordinal) then workers (newest spawned first). [] when the task has
    no sessions — so the brief's provenance section stays absent (data-gated)."""
    meta = task.get("session_meta") or {}
    if not meta:
        return []
    ensure_ordinals(task)
    def _key(kv):
        _sid, m = kv
        if m.get("role") == "hub":
            o = m.get("ordinal")
            return (0, o if o is not None else float("inf"))
        return (1, -(m.get("spawned_at") or m.get("ts") or 0))
    rows = []
    for sid, m in sorted(meta.items(), key=_key):
        role = m.get("role") or "unknown"
        if role == "hub":
            rows.append({"ordinal": ordinal_label(task, sid) or "",
                         "kind": "hub", "name": sid[:8], "model": m.get("model") or "",
                         "status": m.get("status") or "",
                         "spawned": rel_time(m.get("spawned_at") or m.get("ts"))})
        else:
            rows.append({"ordinal": "", "kind": role,
                         "name": m.get("name") or sid[:8], "model": m.get("model") or "",
                         "status": m.get("status") or "",
                         "spawned": rel_time(m.get("spawned_at") or m.get("ts"))})
    return rows


def _brief_provenance_ledger(task, limit=5):
    """The last `limit` hub<->worker interactions for the brief (#463), oldest→newest.
    [] when the task has no ledger (data-gated)."""
    led = task.get("ledger") or []
    if not led:
        return []
    meta = task.get("session_meta") or {}
    out = []
    for e in led[-limit:]:
        actor = ("%s-%s" % (task.get("seq"), e["actor_ordinal"])
                 if e.get("actor_ordinal") is not None
                 else (e.get("actor") or "?")[:8])
        worker = ((meta.get(e.get("worker")) or {}).get("name")
                  or (e.get("worker") or "")[:8])
        out.append({"when": rel_time(e.get("ts")), "actor": actor,
                    "action": e.get("action"), "worker": worker,
                    "detail": e.get("detail")})
    return out


def _brief_persist_path(task, out, session):
    """Persist task['brief_path'] = out through mutate(), then emit task.updated and
    sync Obsidian. Shared by the `render` and `path` actions so a brief is findable
    the same way however it was produced — the contract-v2 note frontmatter carries
    brief_path automatically once it is on the record."""
    def _apply(t):
        t["brief_path"] = out
    updated = mutate(task["id"], _apply)
    if updated is not None:
        _stream_emit("task.updated", updated,
                     _stream_updated_data(updated, ["brief_path"]), session)
        _obsidian_sync(updated)


def cmd_brief(a):
    """`task-station brief [render|path] [--task|--session] [--spec FILE]`.

    **path** — resolve the task, create the artifact dir, persist and print
    brief_output_path(task). Reads no spec. This is the model-authored flow: the
    `/brief` skill asks for the path, then writes its own HTML there.

    **render** (default, retained for back-compat) — read a brief-spec (JSON) from
    --spec FILE or stdin, lazy-import lib/brief (pure stdlib, host-agnostic), render
    it against the task's glossary into the frozen house-style template, write to
    brief_output_path (makedirs), persist task['brief_path'] and print the path."""
    task = _resolve_glossary_task(a)
    if not task:
        print("brief: no task — attach a session or pass --task <ref>.")
        return

    session = getattr(a, "session", None)

    if (getattr(a, "action", None) or "").strip().lower() == "path":
        out = brief_output_path(task)
        try:
            os.makedirs(os.path.dirname(out), exist_ok=True)
        except OSError as e:
            print("brief: cannot create %s: %s" % (os.path.dirname(out), e))
            return
        _brief_persist_path(task, out, session)
        print(out)
        return

    src = getattr(a, "spec", None)
    try:
        if src:
            with open(os.path.expanduser(src), encoding="utf-8") as f:
                raw = f.read()
        else:
            raw = sys.stdin.read()
    except OSError as e:
        print("brief: cannot read spec: %s" % e)
        return
    raw = (raw or "").strip()
    if not raw:
        print("brief: empty spec — pass --spec FILE or pipe the brief-spec JSON on stdin.")
        return
    try:
        spec = json.loads(raw)
    except ValueError as e:
        print("brief: spec is not valid JSON: %s" % e)
        return

    import brief as _brief   # lazy: keep the renderer off the hot engine paths
    glossary = _normalize_glossary(task.get("glossary"))
    # Inject the task's session roster + worker ledger tail (#463) unless the spec
    # already supplies them. Data-gated in the renderer: empty lists → no section.
    if isinstance(spec, dict):
        spec.setdefault("sessions", _brief_provenance_sessions(task))
        spec.setdefault("ledger", _brief_provenance_ledger(task))
    html = _brief.render_brief(spec, glossary)

    out = brief_output_path(task)
    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write(html)
    except OSError as e:
        print("brief: cannot write %s: %s" % (out, e))
        return

    _brief_persist_path(task, out, session)
    print(out)


def cmd_recap(a):
    """`task-station recap [--week YYYY-Www] [--open] [--json] [--no-scan]
    [--auto-if-due]` — build the strictly-private weekly usage recap: a local,
    self-contained HTML one-pager under <data_dir>/recaps/<week>.html summarizing what
    you did, what it cost, and concrete guidance to use LLMs more effectively.

    Reads only the persisted ledger + task store (no transcript IO of its own); by
    default it first runs an incremental scan-all so the week's numbers are current
    (--no-scan skips it). --auto-if-due is the hook entry point: it self-gates on the
    `recap` config toggle + a once-per-week stamp and is a silent, fail-open no-op
    otherwise. Output is machine-local and added to NO sync boundary."""
    import config
    import recap as _recap
    store = _backend()

    if getattr(a, "auto_if_due", False):
        # Hook path: strictly gated + silent. auto_generate_if_due swallows all errors.
        path = _recap.auto_generate_if_due(store)
        if path and not getattr(a, "quiet", False):
            print(path)
        return

    if not config.usage_tracking_enabled():
        print("recap: usage tracking is off (config --usage-tracking off) — no data "
              "to summarize.")
        return

    if not getattr(a, "no_scan", False):
        try:
            _usage_engine().scan_all(store)      # freshen the ledger; best-effort
        except Exception:
            pass                                  # stale numbers beat a crashed recap

    try:
        result = _recap.generate(store, week=getattr(a, "week", None))
    except ValueError as e:
        print("recap: %s" % e)
        return

    if getattr(a, "as_json", False):
        print(json.dumps(result["aggregates"], ensure_ascii=False, indent=2))
        return

    print(result["path"])
    if getattr(a, "open", False):
        _open_path(result["path"])


def _save_flags(rest):
    """The two flags `/todo save` takes, read WITHOUT argparse: `(verbose, check)`.

    Deliberately a token scan rather than a parser. `/save` has always ignored trailing
    free text — `/todo SAVE please checkpoint this` is a documented, tested shape — and
    an argparse spec would turn that into a usage error. `rest` also arrives as a list
    from a couple of internal callers, so both shapes are accepted."""
    raw = rest if isinstance(rest, str) else " ".join(rest or [])
    toks = raw.split()
    return ("--verbose" in toks), ("--check" in toks)


def _save_check_block(task, seq, session):
    """`/todo save --check` — the MECHANICAL cold-read verification, and NOTHING else.

    The gap report alone: no capture checklist, no command templates, no digest. It is
    what step 6 of the save flow runs after a write to prove the gaps actually closed,
    and it is READ-ONLY — it stamps nothing, clears no flag and writes no session
    record. That is the same contract `heal --scan` keeps, and for the same reason: a
    verification pass that mutates what it verifies cannot be trusted about either."""
    report = _save.gap_report(task, digest_chars=len(_format_detail(task, session)))
    out = ["[SAVE] Task #%s [%s] — session %s"
           % (seq, task["id"][:8], (session or "")[:8]),
           "COLD-READ CHECK — the gap report, re-run. This is READ-ONLY: nothing was "
           "changed, no checkpoint was stamped."]
    out.extend(_save.gap_lines(report))
    out.append("")
    out.append("VERDICT: %s"
               % ("PASS — every named slot carries something and the state leads with "
                  "`NEXT:`" if _save.is_clean(report)
                  else "FAIL — patch each line above with another `update --task %s …`, "
                       "then re-run `/todo save --check`" % seq))
    return "\n".join(out)


def _todo_save(a, rest):
    """/todo save — checkpoint the CURRENT task's context into its digest so a
    FRESH session can resume with zero context loss. Prints a model-facing [SAVE]
    block: a GAP REPORT of what the digest is MISSING + the named-slot capture
    checklist + the exact `update` templates + the mechanical cold-read check.
    Records THIS session (with its cwd) as a transcript backstop for `/todo <n> -s`.
    Mints NO session and performs NO pin — capture only.

    IT DOES NOT DUMP THE DIGEST, and it does NOT STAMP. Both are deliberate reversals:

      * The caller has been working this task all along, so it already HAS the digest.
        Measured on one real task, echoing it back cost 71,516 characters of which
        71,271 — 99.7% — were the dump. What the caller does not have is the list of
        what is missing, which is what `save.gap_report` computes. `--verbose` still
        prints the full digest; `--check` prints the gap report alone, which is the
        mechanical cold-read re-check after a write.
      * `last_full_save_ts` claims a full checkpoint was CAPTURED. Emitting a prompt
        captures nothing, so this records only that a save was STARTED; the stamp
        belongs to the `update` that writes a summary AND a state (see
        `save.is_checkpoint_write`)."""
    task = _session_task(a.session)
    if not task:
        print(_NO_TASK_ATTACHED)
        return
    verbose, check_only = _save_flags(rest)
    seq = task.get("seq", task["id"][:8])
    if check_only:
        # `--check` is READ-ONLY, and that is its whole contract — the same one
        # `heal --scan` keeps. It runs BEFORE any of the writes below precisely because
        # a verification pass that mutates the thing it is verifying is the bug this
        # release exists to fix, one surface over: it would print "nothing was changed"
        # having just changed something.
        print(_save_check_block(task, seq, a.session))
        return
    # A save has been STARTED — that, and only that, is what emitting the block proves.
    # The two staleness flags still clear here, and they are a different kind of claim:
    # they gate NUDGES ("your digest looks stale", "run /todo save NOW"), and the nudge
    # has been delivered and acted on the moment this block is read. The checkpoint
    # STAMP is a claim about captured content, so it waits for the write.
    _save.mark_save_started(task)
    clear_digest_dirty(task)
    clear_pressure_nudged(task)
    save_task(task)
    # Notifications that a save HAPPENED (note + daily-note entry + feed event) — the
    # record that a checkpoint was CAPTURED is `last_full_save_ts`, and that one waits
    # for the write. These fire here because the export and the feed track activity,
    # not the stamp.
    _obsidian_event(task, "checkpoint")
    _stream_emit("task.checkpoint", task, _stream_digest(task), a.session)
    # Transcript backstop: make sure THIS session is a findable resume candidate so a
    # later fresh session can `/todo <n> -s` back into the FULL transcript if the digest
    # ever misses a detail. Record its cwd in session_meta (authoritative from the
    # transcript when available; os.getcwd() fallback) WITHOUT pinning. Only writes when
    # the entry is missing or lacks a cwd, so an existing record is left intact.
    if a.session:
        meta = task.setdefault("session_meta", {})
        entry = meta.get(a.session)
        if not entry or not entry.get("cwd"):
            path = _find_session_path(a.session)
            cwd = (_session_cwd(path) if path else None) or os.getcwd()
            meta[a.session] = {"cwd": cwd, "ts": _now(), "role": "hub"}
            save_task(task)
    # The digest is rendered ONCE and, by default, only MEASURED — its length is the
    # "what a fresh session loads" number in the gap report. Rendering it costs the same
    # as it always did; what changed is that the 71,271 characters no longer go to the
    # caller, who has been working this task and already has them.
    detail = _format_detail(task, a.session)
    report = _save.gap_report(task, digest_chars=len(detail))
    out = []
    out.append("[SAVE] Task #%s [%s] — session %s"
               % (seq, task["id"][:8], (a.session or "")[:8]))
    out.append("Checkpoint this task so a FRESH session — with NO memory of this "
               "conversation — can resume with ZERO context loss. AMEND what the GAP "
               "REPORT names via the `update` command; do NOT rewrite slots that are "
               "already accurate — a save is an amendment, not a rewrite.")
    # THE SAVE GATE. `--summary` REPLACES the summary wholesale, so writing one from a
    # decision set that still contains refuted entries bakes the drift into the very
    # first field anyone reads. One line, and it does NOT block and does NOT run the
    # heal — this is a warning at a decision point, not a gate. Fail-open.
    try:
        gate = _heal.gate_line(task)
    except Exception:
        gate = None
    if gate:
        out.append("[task-station] %s The summary you are about to REPLACE would be "
                   "written from a decision set that has not been reconciled." % gate)
    out.append("")
    # THE GAP REPORT REPLACED THE DIGEST DUMP. See `_todo_save`'s docstring for the
    # measurement; the short version is that echoing the digest back to the session that
    # wrote it was 99.7% of this block's cost and told it nothing it did not know.
    out.append("GAP REPORT — what the digest is MISSING. The digest itself is NOT "
               "reprinted: you have been working this task, so you already have it "
               "(`/todo save --verbose` dumps it if you genuinely do not).")
    out.extend(_save.gap_lines(report))
    if verbose:
        out.append("")
        out.append("CURRENT DIGEST (--verbose)")
        out.append(detail)
    out.append("")
    out.append("CAPTURE CHECKLIST — the reference for each slot. Write ONLY the ones the "
               "GAP REPORT named; leave an accurate slot alone. Fill EVERY slot you do "
               "write with SPECIFICS (exact paths, names, values, commands — never vague "
               "summaries):")
    out.append("  1. GOAL (--goal): the objective — what \"done\" concretely looks like.")
    out.append("  2. NEXT ACTION (--state): the state line MUST LEAD with "
               "`NEXT: <the concrete first move the resumed session should make>`, then "
               "the current standing. Specific enough to act on immediately.")
    out.append("  3. STEPS (--step-add / --step-done N): the FULL plan as a checklist, "
               "marking done vs not-done accurately — INCLUDE the not-yet-started steps.")
    out.append("     • A step gone STALE (the plan moved on, or it names something "
               "retired) is retired with `--step-supersede <n>` — add the corrected step "
               "in the same call and it is recorded as the replacement. Do NOT tick it "
               "done (nobody did it) and do NOT add a warning step about it. It leaves "
               "the checklist and both sides of the n/m count, stays in `/todo %s "
               "history`, and `--step-restore <n>` undoes it." % seq)
    out.append("  4. DECISIONS + WHY (--decision, one per): every material choice AND its "
               "rationale — INCLUDING approaches TRIED and REJECTED and why, so the resume "
               "never re-explores dead ends.")
    out.append("     • REPLACING an earlier call? Add the new decision with "
               "`--supersedes <n>` (the number from `/todo %s history`, repeatable). The "
               "old one then vanishes from this digest instead of sitting here "
               "contradicting the new one — a refuted decision left visible is worse "
               "than no decision." % seq)
    out.append("     • Nothing is hidden by age: EVERY still-current decision renders in "
               "this digest, however old and however many. A decision leaves the digest "
               "only by ceasing to be true (`--supersedes`, or `heal`'s split/merge).")
    out.append("     • ARCHITECTURE SPINE — a rule the rest of the work must obey? Add it "
               "with `--pin` (or `--pin-decision <n>`). A pin is READING ORDER, not "
               "visibility: pinned decisions sort FIRST (marked ★), then everything else "
               "oldest-first. No limit; keep the pinned set to the spine so leading with "
               "it still means something.")
    out.append("     • ONE decision per --decision, atomic. Past %d chars you get an "
               "advisory suggesting `heal --split` — it is a SUGGESTION and the write "
               "always succeeds in full, so never drop a fact or fake two entries out of "
               "one to get under it." % _dec.LONG_DECISION_CHARS)
    out.append("  5. CONTEXT SNAPSHOT (--summary, REPLACE): rewrite `summary` to the CURRENT "
               "truth — a lean structured snapshot (this REPLACES the summary wholesale; keep "
               "it the present state, NOT a running log — do NOT dump the history into it, the "
               "why-trail lives in decisions + --log, read it back via `/todo <n> history`). It "
               "must EXPLICITLY cover —")
    out.append("       • Files / modules touched or relevant, with PATHS, and how they "
               "fit together.")
    out.append("       • The repo / branch / worktree / environment + any auth / config / "
               "tooling quirks.")
    out.append("       • Commands to build / test / run / reproduce.")
    out.append("       • Constraints & gotchas — \"watch out for X\", \"never do Y\".")
    out.append("       • Open questions / blockers awaiting a decision.")
    out.append("       • The user's most recent intent (what they last asked for), in "
               "their words.")
    out.append("     • The summary you replace is NOT destroyed: it is kept, append-only, "
               "and `update --task %s --restore-summary` puts the previous one back "
               "(`--restore-summary <n>` for an older version; `/todo %s history` lists "
               "them). A thin save can no longer silently lose a good summary." % (seq, seq))
    out.append("  5b. LOG (--log): one dated line for a milestone/finding worth keeping in "
               "history (does not load on normal resume). Exactly ONE per save.")
    out.append("  6. LINKS (--pr / --story): PRs and work-items.")
    out.append("")
    out.append("Command templates (seq %s filled in — one call or several):" % seq)
    out.append("    task-station update --task %s \\" % seq)
    out.append("      --goal '<what done looks like>' \\")
    out.append("      --state 'NEXT: <concrete first move> — <current standing>' \\")
    out.append("      --step-add '<not-yet-started step>' --step-done <N> \\")
    out.append("      --decision '<decision + why — incl. what was tried & rejected>' \\")
    out.append("      --log '<vX.Y.Z shipped: what — or a finding worth keeping in history>' \\")
    out.append("      --pr '<url>' --story '<url>' \\")
    out.append("      --summary '<CURRENT snapshot: files+paths · branch/env · commands · "
               "gotchas · open questions · user's latest intent>'")
    out.append("    (--summary REPLACES the summary wholesale — use it for the current "
               "snapshot; --append-summary only adds. History goes to --decision / --log, "
               "not into --summary.)")
    out.append("    " + _cli_fallback())
    out.append("")
    # THE STAMP BELONGS TO THE WRITE, and the block has to say so — otherwise the next
    # reader assumes running `/save` was the checkpoint, which is precisely the belief
    # that let an empty summary sit under a `last full save just now`.
    out.append("THIS BLOCK DID NOT STAMP A CHECKPOINT. `last_full_save_ts` means \"a full "
               "checkpoint was CAPTURED\", and printing a prompt captures nothing — all "
               "this recorded is that a save was STARTED. The stamp lands on the `update` "
               "that writes a `--summary` AND a `--state` together, because that pair IS "
               "the checkpoint; no flag declares it, so no one can claim one without "
               "writing it.")
    out.append("")
    out.append("COLD-READ CHECK — after the write, and MECHANICAL, not a feeling: every "
               "named slot must be non-empty and `state` must begin with `NEXT:`. The "
               "stamping `update` reports any that still fail; `/todo save --check` "
               "re-runs the same check on demand. Then the judgement half: re-read the "
               "digest as if you have NO memory of this conversation and PATCH anything "
               "ambiguous or assumed with another `update`.")
    out.append("")
    out.append("Not pinned — /todo save only captures. DO NOT pin a session or open / "
               "resume anything. If a detail is ever missing, the trail is recoverable: "
               "`/todo %s history` (the decisions + log record) or `/todo %s -s` "
               "(this session's full transcript)." % (seq, seq))
    print("\n".join(out))


def _todo_pin(a, rest):
    """/todo pin — pin THIS session as the attached task's resume target (same as
    the standalone /pin). No --new: pins the current session."""
    task = _session_task(a.session)
    if not task:
        print(_NO_TASK_ATTACHED)
        return
    print(_pin_one(str(task.get("seq") or task["id"]), a))


def _todo_unpin(a, rest):
    """/todo unpin [n,…] — drop the pinned resume session. With a numeric list,
    unpins those task(s); bare, unpins THIS session's attached task (inverse of
    /todo pin). Reuses cmd_unpin."""
    ns = argparse.Namespace(session=a.session, task=(rest or None))
    cmd_unpin(ns)


def _todo_done(a, rest):
    """/todo done [n,…] — close the current session's attached task, or the
    task(s) named by number. Reuses cmd_done (does NOT close the terminal window —
    you're mid-session; that's intended)."""
    ns = argparse.Namespace(session=a.session, task=(rest or None))
    cmd_done(ns)


def _todo_config(a, rest):
    """/todo config [flags] — route to the config console. Everything after the
    keyword is tokenized (shlex) and parsed by the same argparse spec cmd_config
    uses, then dispatched in-process so config prints verbatim."""
    import shlex
    import config
    parser = argparse.ArgumentParser(prog="/todo config", add_help=False)
    _add_config_args(parser)
    try:
        ns = parser.parse_args(shlex.split(rest))
    except SystemExit:
        return   # argparse already reported the bad flag/usage
    config.cmd_config(ns)


def _todo_search(a, rest):
    """/todo search [<--open|--closed|--all>] <terms> — the search surface on the
    /todo command (mirrors the standalone `search` subcommand's tier-1 output).
    Defined here (above _TODO_SUBCMDS) because that dict literal references it at
    module-load; the _search_core/_format_search it calls resolve at runtime."""
    rest = (rest or "").strip()
    want = "all"
    m = re.match(r"^--(open|closed|all)\b\s*(.*)$", rest)
    if m:
        want, rest = m.group(1), m.group(2).strip()
    if not rest:
        print("search: give one or more terms, e.g. /todo search auth token")
        return
    detail = _numeric_ref_detail(rest, a.session)
    if detail is not None:
        print(detail)
        return
    print(_format_search(rest, _search_core(rest, want), want))


# Reserved /todo leading keywords → handler(a, rest). Checked before the numeric/
# ref parsing; each triggers only on the exact leading token (case-insensitive).
def _todo_native(a, rest):
    """/todo native — read-only listing of Claude Code's recent native task lists."""
    print(_format_native())


def _todo_adopt(a, rest):
    """/todo adopt <list-prefix>:<id> — promote a native task into a durable station
    task (read-only on the native side)."""
    cmd_adopt(argparse.Namespace(native=(rest or "").strip() or None))


def _memo_ns(**kw):
    """An argparse.Namespace for cmd_memo with every optional field defaulted, so the
    slash surface never trips a getattr on a flag it doesn't spell."""
    ns = dict(sub=None, task=None, id=None, text=None, session=None,
              decision=None, memory=None, noop=None, corrects=None)
    ns.update(kw)
    return argparse.Namespace(**ns)


def _todo_memo(a, rest):
    """/todo memo — hand a fact/decision to a task's session(s). Grammar:
        /todo memo <n> <text…>        send to task n FROM this session
        /todo memo ack <id8> <TEXT>   ack, promoting the memo to a decision
        /todo memo ack <id8> memory:<slug>   ack, folded into that memory note
        /todo memo ack <id8> noop:<reason>   ack, no durable change needed
        /todo memo show [<n>] [<id8>] list the attached/nth task's memos, or one full body
    An ack MUST carry one of the three dispositions — a bare `/todo memo ack <id8>` is
    an error naming all three. Routes to cmd_memo so the CLI + slash surfaces share one
    code path."""
    toks = (rest or "").split()
    if not toks:
        cmd_memo(_memo_ns(sub="show", session=a.session))
        return
    head = toks[0].lower()
    if head == "ack":
        mid = toks[1] if len(toks) > 1 else None
        # Everything after the id8 is the disposition. `memory:` / `noop:` select the two
        # non-decision dispositions; anything else is the promote-to-decision text.
        text = rest.split(None, 2)[2].strip() if len(toks) > 2 else ""
        kw = {}
        low = text.lower()
        if low.startswith("memory:"):
            kw["memory"] = text[len("memory:"):].strip()
        elif low.startswith("noop:"):
            kw["noop"] = text[len("noop:"):].strip()
        elif text:
            kw["decision"] = text
        cmd_memo(_memo_ns(sub="ack", id=mid, session=a.session, **kw))
        return
    if head == "show":
        rest_toks = toks[1:]
        task_ref = None
        mid = None
        for tk in rest_toks:
            if tk.isdigit() and task_ref is None:
                task_ref = tk
            else:
                mid = tk
        cmd_memo(_memo_ns(sub="show", task=task_ref, id=mid, session=a.session))
        return
    # Default: send — first token is the target task number, the remainder is the body.
    ref = toks[0]
    body = rest[len(toks[0]):].strip()
    cmd_memo(_memo_ns(sub="send", task=ref, text=body, session=a.session))


def _todo_glossary(a, rest):
    """/todo glossary [flags] — route to the glossary console. Everything after the
    keyword is tokenized (shlex) and parsed by the SAME argparse spec cmd_glossary
    uses; this session is injected as --session so task resolution matches /glossary."""
    import shlex
    parser = argparse.ArgumentParser(prog="/todo glossary", add_help=False)
    _add_glossary_args(parser)
    try:
        ns = parser.parse_args(shlex.split(rest or ""))
    except SystemExit:
        return
    ns.session = a.session      # the /todo dispatch owns the session, not `rest`
    cmd_glossary(ns)


def _todo_brief(a, rest):
    """/todo brief [flags] — route to the brief renderer (parity with /brief).
    Tokenized + parsed by the SAME spec cmd_brief uses; this session is injected."""
    import shlex
    parser = argparse.ArgumentParser(prog="/todo brief", add_help=False)
    _add_brief_args(parser)
    try:
        ns = parser.parse_args(shlex.split(rest or ""))
    except SystemExit:
        return
    ns.session = a.session
    cmd_brief(ns)


def _todo_heal(a, rest):
    """/todo heal [--scan|--apply|--all|<n>] — the reconcile pass on the current task
    (or the one named). Everything after the keyword is tokenized and parsed by the same
    spec `cmd_heal` uses, so `/todo heal --scan` and `task-station heal --scan` behave
    identically. A bare `/todo heal` is a DRY RUN and changes nothing."""
    import shlex
    parser = argparse.ArgumentParser(prog="/todo heal", add_help=False)
    parser.add_argument("ref", nargs="?", default=None)
    parser.add_argument("--task", default=None)
    parser.add_argument("--scan", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--all", dest="all", action="store_true")
    parser.add_argument("--split", type=int, default=None)
    parser.add_argument("--merge", default=None)
    parser.add_argument("--into", default=None)
    parser.add_argument("--mark-healed", dest="mark_healed", action="store_true")
    parser.add_argument("--note", default=None)
    parser.add_argument("--dispose-acks", dest="dispose_acks", default=None)
    parser.add_argument("--decision", nargs="?", const=True, default=None)
    parser.add_argument("--memory", default=None)
    parser.add_argument("--noop", default=None)
    # The ledger, the cheap candidate view and the two opt-ins. Mirrored here rather than
    # shared with the subparser because `/todo heal` parses a REST STRING and the top-level
    # `heal` parses argv — but every flag must exist in both, or `/todo heal --candidates`
    # dies on `unrecognized arguments` while `heal --candidates` works, which is the one
    # class of divergence a user actually hits.
    parser.add_argument("--dismiss", action="append", default=None)
    parser.add_argument("--undismiss", action="append", default=None)
    parser.add_argument("--why", default=None)
    parser.add_argument("--dismissals", action="store_true")
    parser.add_argument("--candidates", action="store_true")
    parser.add_argument("--goal-reviewed", dest="goal_reviewed", action="store_true")
    parser.add_argument("--probe-links", dest="probe_links", action="store_true")
    try:
        ns = parser.parse_args(shlex.split(rest or ""))
    except SystemExit:
        return                       # argparse already reported the bad flag/usage
    ns.session = a.session
    # A bare leading number is the task ref (`/todo heal 12`), matching how the other
    # /todo subcommands take one. The FOLD ITSELF belongs to `cmd_heal`
    # (`_heal_positional_ref`), which the top-level `heal` subparser reaches too — one
    # place deciding what a positional means, and one place refusing the combinations it
    # cannot mean. A second precedence rule here is how two surfaces start disagreeing
    # about which task a command was aimed at.
    cmd_heal(ns)


_TODO_SUBCMDS = {
    "save": _todo_save,
    "heal": _todo_heal,
    "pin": _todo_pin,
    "unpin": _todo_unpin,
    "done": _todo_done,
    "config": _todo_config,
    "native": _todo_native,
    "adopt": _todo_adopt,
    "search": _todo_search,
    "memo": _todo_memo,
    "glossary": _todo_glossary,
    "brief": _todo_brief,
}


def _search_core(query, want="all", limit_shown=SEARCH_HITS_SHOWN):
    """Run the ranked search, load each hit, apply the status filter, and keep the
    top `limit_shown`. Returns [(task, snippet), …] in rank order. `want` is
    all|open|closed (open = the board: open + active)."""
    rows = []
    for h in search_tasks(query, limit=SEARCH_SCAN_LIMIT):
        task = load_task(h["id"])
        if not task:
            continue
        if want == "open" and is_closed(task):
            continue
        if want == "closed" and not is_closed(task):
            continue
        rows.append((task, h.get("snippet") or ""))
        if len(rows) >= limit_shown:
            break
    return rows


def _numeric_ref_detail(query, session):
    """A bare all-digit search query (e.g. `search 362`) is a lookup by the task's
    display number (#seq), not a text search — resolve it to that task's read-only
    digest so a numeric lookup never falsely reports "no match". Returns the
    formatted detail string, or None to fall through to text search (query isn't a
    lone number, or no task carries that number — e.g. a PR/story number)."""
    q = (query or "").strip()
    if not q.isdigit():
        return None
    task = resolve_ref(q)
    if not task:
        return None
    return _format_detail(task, session, attached=False)


def cmd_search(a):
    """`task-station search <terms>` — ranked cross-task search (tier 1), or
    `--detail <seq>` to print one task's full read-only digest."""
    if getattr(a, "detail", None):
        task = resolve_ref(a.detail) or load_task(a.detail)
        if not task:
            print("No task matching '%s'." % a.detail)
            return
        print(_format_detail(task, a.session, attached=False))
        return
    query = " ".join(a.terms).strip()
    if not query:
        print("search: give one or more terms, e.g. task-station search auth token")
        return
    detail = _numeric_ref_detail(query, a.session)
    if detail is not None:
        print(detail)
        return
    want = "open" if getattr(a, "open", False) else \
           "closed" if getattr(a, "closed", False) else "all"
    print(_format_search(query, _search_core(query, want), want))


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
    if toks and toks[0].lower() in _TODO_SUBCMDS:
        kw = toks[0].lower()
        rest = raw[len(toks[0]):].strip()   # everything after the leading keyword
        _TODO_SUBCMDS[kw](a, rest)
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


def _memo_target(a):
    """Resolve the task for a `memo ack`/`show`: an explicit --task (any seq/id-prefix)
    else the acting session's attached task. Returns the task dict or None."""
    ref = getattr(a, "task", None)
    if ref:
        return resolve_ref(ref) or load_task(ref)
    tid = get_link(getattr(a, "session", None))
    if tid and tid != SKIP_SENTINEL:
        return load_task(tid)
    return None


def cmd_memo(a):
    """`task-station memo send|ack|show` — hand a fact/decision to a task's working
    session(s). `send --task <ref>` posts onto any task (attached or not, cross-task via
    resolve_ref); `ack`/`show` default --task to the acting session's attached task.
    A bad ref / unknown id prints ONE error line and returns (exit 0, like add-event),
    so a best-effort caller never aborts."""
    sub = getattr(a, "sub", None)
    if sub == "send":
        ref = getattr(a, "task", None)
        task = (resolve_ref(ref) or load_task(ref)) if ref else None
        if not task:
            print("memo: no task matching %r" % ref)
            return
        text = getattr(a, "text", "") or ""
        if not text.strip():
            print("memo: --text is required (the memo body)")
            return
        corrects = [c for c in (getattr(a, "corrects", None) or []) if str(c).strip()]
        memo = memo_send(task, text, from_sid=getattr(a, "session", None),
                         corrects=corrects)
        task["updated_ts"] = _now()
        save_task(task)
        print("memo %s → task #%s (%s)"
              % (memo["id"][:8], task.get("seq", task["id"][:8]), task["title"]))
        if corrects:
            print("  corrects: %s — the ack will require a disposition that engages it."
                  % ", ".join(corrects))
        else:
            # M4 backstop: `--corrects` only helps when the sender remembers it. Warn on
            # correction-shaped language; never block — the sender may have good reason.
            hits = correction_language(text)
            if hits:
                print("  ⚠ this reads like a correction (matched: %s) but declares no "
                      "--corrects target." % ", ".join(hits))
                print("    Add `--corrects <memory-slug|decision:N|memo-id8>` so the ack "
                      "has to engage what it replaces.")
        return

    task = _memo_target(a)
    if not task:
        print("memo: no task — pass --task <ref>, or attach this session first.")
        return

    if sub == "ack":
        memo, err = _memo_by_prefix(task, getattr(a, "id", None))
        if err:
            print(err)
            return
        sid = getattr(a, "session", None)
        if not sid:
            print("memo ack: --session <your-session-id> is required.")
            return
        dec = getattr(a, "decision", None)
        # M1: an ack must carry EXACTLY ONE disposition. A bare ack is refused — it was
        # the shape that let a correction be acknowledged and never integrated.
        disp, err = memo_ack_disposition(decision=dec,
                                         memory=getattr(a, "memory", None),
                                         noop=getattr(a, "noop", None))
        if err:
            print(err)
            corrects = memo_corrections(memo)
            if corrects:
                print("memo %s CORRECTS %s — it cannot be acked without saying what you "
                      "did about that." % (memo["id"][:8], ", ".join(corrects)))
            return
        decisions_before = len(task.get("decisions") or [])
        result = memo_ack(task, memo, sid,
                          promote=(disp["kind"] == "decision"),
                          decision_text=(disp["value"] if disp["kind"] == "decision" else None),
                          disposition=disp)
        task["updated_ts"] = _now()
        save_task(task)
        if result == "already":
            print("memo %s already acked by %s." % (memo["id"][:8], sid[:8]))
            return
        tail = {"decision": " → promoted to a decision",
                "memory": " → folded into memory note '%s'" % disp["value"],
                "noop": " → no durable change (%s)" % disp["value"]}[disp["kind"]]
        print("memo %s acked by %s%s." % (memo["id"][:8], sid[:8], tail))
        # A promoted memo body is UNCAPPED, so this is the other way a very long
        # decision gets written. Same advisory, same rule: it is already stored in
        # full, and this only suggests splitting it. Gated on the log actually having
        # GROWN, so a promote that no-oped on blank text can't warn about the entry
        # that was already there.
        n = len(task.get("decisions") or [])
        if n > decisions_before:
            warn = _dec.length_warning(task["decisions"][n - 1], n)
            if warn:
                print("  ⚠ %s" % warn)
        # M4: the memo read like a correction but declared no target — remind the acker
        # to go update the durable stores, which is the step that actually gets missed.
        if not memo_corrections(memo) and correction_language(memo.get("text")):
            print("  ⚠ REMINDER: this memo reads like a correction (matched: %s)."
                  % ", ".join(correction_language(memo.get("text"))))
            print("    An ack is a receipt, not an integration — update the durable store "
                  "it contradicts (agent memory / a task decision) now.")
        return

    if sub == "show":
        mid = getattr(a, "id", None)
        if not (task.get("memos") or []):
            print("(no memos)")
            return
        if mid:
            memo, err = _memo_by_prefix(task, mid)
            if err:
                print(err)
                return
            print(_format_memo_full(task, memo))
        else:
            print(_format_memo_list(task, getattr(a, "session", None)))
        return

    print("memo: use `send`, `ack`, or `show`.")


def cmd_sessions(a):
    """`task-station sessions [--task <ref>] [--json]` — every ACTUALLY-running
    Claude Code session (hub + delegated workers), each with its task, busy/idle
    state, and a one-command resume. Dead/crashed sessions never appear. `--task`
    filters to one task's live sessions; `--json` emits the raw row list."""
    import live_sessions
    rows = live_sessions.running()
    if getattr(a, "task", None):
        t = resolve_ref(a.task) or load_task(a.task)
        rows = [r for r in rows if t and r.get("task_seq") == t.get("seq")]
    if getattr(a, "as_json", False):
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    if not rows:
        print("No live Claude sessions." if not getattr(a, "task", None)
              else "No live Claude sessions for that task.")
        return
    for r in rows:
        print(_format_session_row(r))


def cmd_status(a):
    """Show or set a task's per-task state between the board states (○ new /
    ● active). `status --task <ref>` with no value reports the current state;
    `status --task <ref> new|active` sets it (idempotent — `new` is the input
    alias for the stored `open`). Closing goes through /done, not here — a closed
    task is reported but not settable from here."""
    task = resolve_ref(a.task) or load_task(a.task)
    if not task:
        print("No task matching '%s'." % a.task)
        return
    value = getattr(a, "value", None)
    cur = task_status(task)
    if not value:
        glyph = STATUS_GLYPH.get(cur, "")
        print("Task [%s] %s — status: %s %s"
              % (task["id"][:8], task["title"], glyph, status_display(cur)))
        return
    value = normalize_status_input(value)   # `new` → stored `open`
    if value not in STATUS_SETTABLE:
        if value == STATUS_CLOSED:
            print("status: close a task with /done (or `done --task %s`), not `status`."
                  % task.get("seq", task["id"][:8]))
        else:
            print("status: unknown status '%s' — use 'new' or 'active'." % value)
        return
    if is_closed(task):
        print("Task [%s] %s is closed — reopen it via /todo %s first."
              % (task["id"][:8], task["title"], task.get("seq", task["id"][:8])))
        return
    if set_status(task, value, note="status set to %s (manual)" % value,
                  session=getattr(a, "session", None)):
        save_task(task)
        maybe_refresh_board()   # open⇄active flip must show on the board NOW
        print("Task [%s] %s → %s %s"
              % (task["id"][:8], task["title"], STATUS_GLYPH[value], status_display(value)))
    else:
        print("Task [%s] %s already %s %s."
              % (task["id"][:8], task["title"], STATUS_GLYPH[value], status_display(value)))


def cmd_session_title(a):
    """Print the window/title-bar label for an attached session (or nothing).

    The SessionStart hook puts this in hookSpecificOutput.sessionTitle so the
    terminal reads `#<seq>: <title>` — the closest we get to auto-labelling
    the hub (the resume-NAME can't be set programmatically on a running session)."""
    task_id = get_link(a.session)
    if not task_id or task_id == SKIP_SENTINEL:
        return
    task = load_task(task_id)
    if not task:
        return
    ensure_seqs()
    print("#%s: %s" % (task.get("seq", "?"), task["title"]))


def cmd_whoami(a):
    """Map any session id → its task. The backstop that identifies a session
    regardless of whether it was ever named."""
    task_id = get_link(a.session)
    porcelain = getattr(a, "porcelain", False)
    if task_id == SKIP_SENTINEL:
        if not porcelain:
            print("session %s: intentionally untracked (skipped)" % a.session[:8])
        return
    statusline = getattr(a, "statusline", False)
    if statusline:
        # HUD-INDEPENDENT capture: this provider runs whenever the statusline is on.
        # Persist the harness context-window size from the piped payload so the Stop
        # nudge sizes % against the REAL window even when the cost HUD is off. When the
        # HUD is on, its own provider (hud.observe) already captured it — skip here to
        # avoid a redundant read-modify-write race on the shared snapshot.
        try:
            import config as _cfg_cap
            if not _cfg_cap.hud_enabled():
                _cw = _read_statusline_stdin().get("context_window") or {}
                persist_harness_context_window(
                    a.session,
                    _cw.get("context_window_size") if isinstance(_cw, dict) else None)
        except Exception:
            pass
    task = load_task(task_id) if task_id else None
    if not task:
        if not porcelain and not statusline:
            print("session %s: not attached to any task" % a.session[:8])
        return
    ensure_seqs()
    if porcelain:
        # Machine-readable: '<seq>\t<seq>-<n>\t<kind>' (tab-separated). Field 1
        # stays the bare seq so pre-463 `cut -f1`-style consumers keep working;
        # delegate._attached_seq splits on whitespace and takes field 1.
        if ensure_ordinals(task):
            save_task(task)
        m = (task.get("session_meta") or {}).get(a.session) or {}
        kind = m.get("role") or "unknown"
        print("%s\t%s\t%s" % (task.get("seq", ""),
                              ordinal_label(task, a.session) or "", kind))
        return
    if statusline:
        # When the cost HUD is on it owns the whole bar and renders the task inline
        # in its own header (model badge + segment). Emit NOTHING here so the task
        # line never renders twice; the host skips an empty provider. Toggling the
        # HUD off restores this segment on the next render.
        try:
            import config as _cfg
            if _cfg.hud_enabled():
                return
        except Exception:
            pass
        # A ready-to-display, ANSI-colored segment for a status bar —
        # '#<seq>  <dot> [TAG]  <title>'. Self-contained: knows nothing about who
        # renders it. Honors --width (>0) by truncating the title so the whole
        # segment fits that many columns; --width 0 means no limit.
        print(statusline_segment(task, getattr(a, "width", 0),
                                  ordinal=hub_ordinal(task, a.session)))
        return
    print("session %s → task-station %s · %s (%s)"
          % (a.session[:8], task.get("seq", "?"), task["title"],
             status_display(task_status(task))))


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
        _emit_title_to_origin(dup)               # relabel the window NOW on auto-fold
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
    _emit_title_to_origin(task)               # label the window NOW on provisional auto-create

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
                 "python3 %s/task-station.py <command> … if the shim isn't on PATH)" % BASE)
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
        "--story stored story/work-item url)",
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


# ------------------------------------------------------------------- main ----

def main(argv=None):
    """`argv=None` reads sys.argv, exactly as before. The explicit-list form exists so
    a caller already holding this module can run a subcommand through the REAL parser
    and dispatch without paying another interpreter start-up — lib/stop_steps.py runs
    the Stop hook's seven best-effort steps that way."""
    p = argparse.ArgumentParser(prog="task-station")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("create"); sp.add_argument("--session", default=None)
    sp.add_argument("--title", required=True); sp.add_argument("--summary", default="")
    sp.add_argument("--color", default=None); sp.add_argument("--effort", default=None)
    sp.add_argument("--goal", default=None,
                    help="one line: what 'done' looks like (digest)")
    sp.add_argument("--step", action="append", default=None,
                    help="seed a checklist step (repeatable)")
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--no-attach", dest="no_attach", action="store_true",
                    help="create unattached (empty sessions) — /todo <n> -s fresh-starts")
    sp.add_argument("--attach", action="store_true",
                    help="force-bind --session even if it's a substantive tracked session")
    sp.add_argument("--active", action="store_true",
                    help="start the task active (●) instead of the default new (○)")
    sp.set_defaults(fn=cmd_create)

    sp = sub.add_parser("attach"); sp.add_argument("--session", required=True)
    sp.add_argument("--task", required=True); sp.add_argument("--color", default=None)
    sp.add_argument("--note", default=None,
                    help="append this text to the task's activity log (fold a prompt in)")
    sp.add_argument("--force-key", dest="force_key", action="store_true",
                    help="confirm an attach whose prompt/--note identity keys "
                         "(PR/work-item #) don't match the target task's (F9 soft-guard)")
    sp.set_defaults(fn=cmd_attach)

    sp = sub.add_parser("detach"); sp.add_argument("--session", required=True)
    sp.add_argument("--task", default=None,
                    help="task to detach from (default: the session's linked task)")
    sp.set_defaults(fn=cmd_detach)

    sp = sub.add_parser("bump"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_bump)

    sp = sub.add_parser("skip"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_skip)

    sp = sub.add_parser("done"); sp.add_argument("--session", default=None)
    sp.add_argument("--task", default=None)   # close any task by seq/id from anywhere
    sp.set_defaults(fn=cmd_done)

    # HARD-delete a task. Hidden (help=SUPPRESS) — not in --help's command list,
    # the config board, or the README; lifecycle is close-not-delete (use `done`).
    # Discoverable only via `guidance`'s maintenance line. See cmd_delete.
    sp = sub.add_parser("delete", help=argparse.SUPPRESS)
    sp.add_argument("--task", required=True)
    sp.set_defaults(fn=cmd_delete)

    sp = sub.add_parser("mark-edited"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_mark_edited)   # PostToolUse(Write|Edit|NotebookEdit) one-shot reminder

    sp = sub.add_parser("touch-file"); sp.add_argument("--session", required=True)
    sp.add_argument("--file", dest="file", required=True)
    sp.set_defaults(fn=cmd_touch_file)    # PostToolUse: append an edited path to the task's briefing

    sp = sub.add_parser("board")
    sp.add_argument("--open", dest="open", action="store_true",
                    help="best-effort: open the written board.html in a browser (macOS)")
    sp.add_argument("--refresh-if-live", dest="refresh_if_live", action="store_true",
                    help="Stop-hook path: silently regen board.html only when auto-refresh is on AND the file exists")
    sp.set_defaults(fn=cmd_board)         # /todo board — ONE board; no engine choice

    sp = sub.add_parser("brains")         # Interbrain brains & sharing config (brains.json)
    sp.add_argument("action", nargs="?", default="show",
                    help="list | add | edit | rename | archive | share | unshare | "
                         "assign | suggest | show")
    sp.add_argument("args", nargs="*", help="positional args for the action")
    sp.add_argument("--with", dest="with_", default=None, help="audience for share/unshare")
    sp.add_argument("--tag", default=None, help="optional category tag scope for a share rule")
    sp.add_argument("--task", default=None, help="task ref for `suggest` (the scoring audit)")
    # Definable-brain fields (add/edit) — the auto-attach signals. List fields accept a
    # comma/space-separated value and REPLACE (an empty string clears them).
    sp.add_argument("--description", default=None, help="one-line brain description")
    sp.add_argument("--purpose", default=None, help="what the brain is for")
    sp.add_argument("--keywords", default=None, help="auto-attach keywords (comma/space list)")
    sp.add_argument("--repos", default=None, help="auto-attach repos (comma/space list)")
    sp.add_argument("--category-affinity", dest="category_affinity", default=None,
                    help="auto-attach category tags (comma/space list)")
    sp.set_defaults(fn=cmd_brains)

    sp = sub.add_parser("hook-health",
                        help="failures the (deliberately non-fatal) hooks recorded")
    sp.add_argument("--clear", action="store_true",
                    help="empty the log and re-arm the SessionStart nag")
    sp.set_defaults(fn=cmd_hook_health)

    # claims — bind a plan document to a task and register the commands that settle what
    # it asserts, so the plan checks itself. See cmd_claims and lib/checker.py.
    sp = sub.add_parser("claims",
                        help="bind a document to a task and register/run the commands "
                             "that verify what it claims")
    # The action is a bare positional with no argparse `choices`, matching `brains`:
    # cmd_claims validates it itself so an unknown word gets a sentence saying what the
    # two actions are, rather than argparse's usage dump and exit code 2.
    sp.add_argument("action", nargs="?", default="show",
                    help="show (default: the bound doc, the claims, the last result — "
                         "runs nothing) | verify (RUN the registered commands; exits "
                         "non-zero if any claim fails, so it can gate a step)")
    sp.add_argument("--task", default=None,
                    help="task by seq/id (default: the attached task)")
    sp.add_argument("--session", default=None)
    sp.add_argument("--bind", default=None, metavar="PATH",
                    help="set/replace the document these claims are about. ABSOLUTE "
                         "path — a relative one would name a different file from every "
                         "directory. The pointer check stats it every session start; it "
                         "never opens it.")
    sp.add_argument("--unbind", action="store_true",
                    help="forget the bound document, KEEPING the registered claims "
                         "(a renamed or split plan is the common case)")
    sp.add_argument("--register", action="append", default=None,
                    metavar="'ID|CMD|EXPECTED[|EXPECTED…]'",
                    help="register one claim: an id, the shell command that settles it, "
                         "and every substring that must appear in its combined "
                         "stdout+stderr. Repeatable, and UPSERTS by id — re-registering "
                         "C1 rewrites C1 and leaves the rest alone. A literal pipe "
                         "inside the command is written `\\|`. At least one expected "
                         "substring is required: a claim asserting nothing would pass "
                         "forever.")
    sp.add_argument("--replace", action="store_true",
                    help="with --register: this invocation's claims REPLACE the whole "
                         "list, instead of upserting into it")
    sp.add_argument("--remove", action="append", default=None, metavar="ID",
                    help="drop a registered claim by id (repeatable)")
    sp.add_argument("--id", default=None, metavar="ID",
                    help="with verify: run just this one claim")
    sp.add_argument("--timeout", type=int, default=None, metavar="SECONDS",
                    help="per-claim timeout for this run (default: the configured "
                         "checker_claim_timeout, 600s)")
    sp.set_defaults(fn=cmd_claims)

    # heal — the RECONCILE pass: turn the append-only decision log into current state.
    # Per-task by default; a DRY RUN by default. See cmd_heal and lib/heal.py.
    sp = sub.add_parser("heal",
                        help="reconcile a task's append-only decision log into current "
                             "state (dry run by default)")
    sp.add_argument("--session", default=None)
    sp.add_argument("ref", nargs="?", default=None, metavar="TASK",
                    help="the task to reconcile, named POSITIONALLY: `heal --scan 12` is "
                         "exactly `heal --scan --task 12`, resolved by the same lookup. "
                         "It exists because /heal passes $ARGUMENTS straight through, so "
                         "a bare `/heal 12` arrives here as a positional. REFUSED "
                         "alongside --all (they name different scopes) or alongside a "
                         "--task naming a DIFFERENT task; the same ref in both places is "
                         "accepted.")
    sp.add_argument("--task", default=None,
                    help="task to reconcile by seq/id (default: the attached task). May "
                         "also be given positionally — `heal --scan 12`.")
    sp.add_argument("--scan", action="store_true",
                    help="layer 1 ONLY: the deterministic scan. Zero tokens, and it "
                         "never modifies the task.")
    sp.add_argument("--apply", action="store_true",
                    help="perform the mechanical plan. Backs the task blob up first and "
                         "REFUSES if that backup cannot be written. Without this, heal "
                         "is a dry run and changes nothing. Prints ONLY what it did — "
                         "not the scan, the decision list or the judgment list, which "
                         "the dry run already showed you. An --apply that performs at "
                         "least one operation STAMPS the heal; one that performs NONE is "
                         "refused rather than stamping a reconcile that never happened "
                         "(use --mark-healed for a judgement-only pass).")
    sp.add_argument("--verbose", action="store_true",
                    help="with --apply: print the FULL block (scan, current decisions, "
                         "judgment list) as well as what was applied. Off by default "
                         "because that block is ~94%% decision text and the caller has "
                         "just read it in the dry run.")
    sp.add_argument("--mark-healed", dest="mark_healed", action="store_true",
                    help="record a JUDGEMENT-ONLY heal: the log was read and nothing "
                         "needed changing. Performs no operation, backs the blob up "
                         "first, and is the only way to say so — without it the record "
                         "still reads `last heal never` and every session opens on a "
                         "false alarm.")
    sp.add_argument("--note", default=None, metavar="WHY",
                    help="with --mark-healed: one line saying what was checked and why "
                         "nothing changed (stored on the task, shown by the scan)")
    sp.add_argument("--dispose-acks", dest="dispose_acks", default=None,
                    metavar="ID8,…|all",
                    help="retro-fill the disposition of acks recorded before one was "
                         "required (needs --apply to write). Takes memo id8s or `all` — "
                         "`all` is legitimate here, since those acking sessions no "
                         "longer exist. Pass exactly ONE of --decision/--memory/--noop. "
                         "Every retro-fill is MARKED retro with who filled it and when, "
                         "the original ack's session/timestamp are never rewritten, and "
                         "a disposition the acker chose is never overwritten.")
    sp.add_argument("--decision", nargs="?", const=True, default=None,
                    help="with --dispose-acks: the memo became a decision (optional TEXT "
                         "says which). Records the disposition only — a heal never mints "
                         "a decision dated to a session that no longer exists.")
    sp.add_argument("--memory", default=None, metavar="SLUG",
                    help="with --dispose-acks: it was folded into that agent-memory note")
    sp.add_argument("--noop", default=None, metavar="REASON",
                    help="with --dispose-acks: no durable change was needed — the reason "
                         "is MANDATORY and is recorded on the ledger")
    sp.add_argument("--all", dest="all", action="store_true",
                    help="sweep every open/active task instead of one — warns about its "
                         "scope before doing anything")
    sp.add_argument("--split", type=int, default=None, metavar="N",
                    help="mark decision N as SPLIT into the decisions named by --into "
                         "(add those first with `update --decision`)")
    sp.add_argument("--merge", default=None, metavar="N1,N2,…",
                    help="mark these decisions as MERGED into the one named by --into "
                         "(add that summary first with `update --decision`)")
    sp.add_argument("--into", default=None, metavar="N1,N2,…",
                    help="the decision(s) a --split became, or the ONE that a --merge "
                         "was absorbed into")
    sp.add_argument("--dismiss", action="append", default=None, metavar="CHECK:REF",
                    help="adjudicate ONE finding away (repeatable; needs --apply and "
                         "--why). It leaves the findings, the issue count and the due "
                         "calculus. The ruling covers that finding's EXACT text, so editing "
                         "the entry it names makes the finding re-report — a dismissal "
                         "adjudicates one state, never a category. Nothing is deleted.")
    sp.add_argument("--undismiss", action="append", default=None, metavar="CHECK:REF",
                    help="retire a dismissal and restore full reporting (repeatable; needs "
                         "--apply). The ledger entry stays, marked retired.")
    sp.add_argument("--why", default=None, metavar="REASON",
                    help="with --dismiss: MANDATORY. Why that finding is not a defect. A "
                         "dismissal with no reason is indistinguishable later from a "
                         "finding somebody buried, so one without this is refused.")
    sp.add_argument("--dismissals", action="store_true",
                    help="list the adjudication ledger — every dismissal with its why, its "
                         "date, and whether it is still silencing anything (an ACTIVE ruling "
                         "whose text has since changed reads EXPIRED). Read-only.")
    sp.add_argument("--candidates", action="store_true",
                    help="the CHEAP merge-only dry run: the goal line, the pinned "
                         "decisions, and each candidate group's members IN FULL — and "
                         "nothing else. The full dry run is ~94%% decision corpus; this is "
                         "the same reading with the corpus removed. Read-only.")
    sp.add_argument("--goal-reviewed", dest="goal_reviewed", action="store_true",
                    help="record that the GOAL LINE was re-read and is still true, resetting "
                         "the goal-review count WITHOUT rewriting it. The only thing that "
                         "resets it: --mark-healed deliberately does not, because a stamp "
                         "saying the record was read is not one saying this line was ruled "
                         "on. May be combined with --mark-healed.")
    sp.add_argument("--probe-links", dest="probe_links", action="store_true",
                    help="opt in to ONE unauthenticated HTTP HEAD per stored PR/story link. "
                         "Off by default (a session start must cost no network). Only an "
                         "explicit 404/410 counts as dead; every other answer, including any "
                         "error, stays UNKNOWN and is never reported.")
    sp.set_defaults(fn=cmd_heal)

    sp = sub.add_parser("stop-gate"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_stop_gate)     # Stop hook: block ending an untracked edit session

    sp = sub.add_parser("post-compact"); sp.add_argument("--session", required=True)
    sp.add_argument("--trigger", default="")
    sp.set_defaults(fn=cmd_post_compact)  # PostCompact hook: stash the compaction summary to history (stdin)

    sp = sub.add_parser("stop-nudge"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_stop_nudge)    # Stop hook: non-blocking staleness nudge (opt-in auto-checkpoint)

    sp = sub.add_parser("render"); sp.add_argument("--session", required=True)
    sp.add_argument("--arg", default="")
    sp.add_argument("--format", choices=["ascii", "md"], default="ascii",
                    help="list output format: ascii (default) or md (GitHub tables, printed verbatim)")
    sp.set_defaults(fn=cmd_render)

    sp = sub.add_parser("add-project"); sp.add_argument("--task", required=True)
    sp.add_argument("--project", required=True); sp.set_defaults(fn=cmd_add_project)

    # search — ranked cross-task FTS search (tier-1 hit list) + --detail digest.
    sp = sub.add_parser("search")
    sp.add_argument("terms", nargs="*", help="terms to search task text for")
    sp.add_argument("--session", default=None)
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--open", dest="open", action="store_true",
                   help="only open + active tasks")
    g.add_argument("--closed", dest="closed", action="store_true",
                   help="only closed tasks")
    g.add_argument("--all", dest="all", action="store_true",
                   help="all tasks (the default)")
    sp.add_argument("--detail", default=None,
                    help="print one task's full digest (read-only) by seq/id instead of searching")
    sp.set_defaults(fn=cmd_search)

    # add-cost — accumulate a delegate run's worker cost onto a task (called by
    # delegate.py so per-run cost lands on the linked /todo task, not just workers.json).
    sp = sub.add_parser("add-cost"); sp.add_argument("--task", required=True)
    sp.add_argument("--usd", required=True, help="this run's total_cost_usd")
    # Optional per-run detail — when any is given, a record is appended to task['runs'].
    sp.add_argument("--model", default=None, help="model id this run used (e.g. claude-opus-4-8)")
    sp.add_argument("--session", default=None, help="worker session id for this run")
    sp.add_argument("--seq-label", dest="seq_label", default=None,
                    help="concurrent-worker label discriminator for this run")
    sp.add_argument("--usage-json", dest="usage_json", default=None,
                    help='JSON token usage {in,out,cache_read,cache_creation}')
    sp.add_argument("--category", default="real", choices=["real", "wasted"],
                    help="real (successful run, default) | wasted (crashed/timed-out spend)")
    sp.set_defaults(fn=cmd_add_cost)

    # add-event — append one entry to a task's bounded event feed (delta-brief source).
    # Quiet bookkeeping called by delegate.py so worker/child milestones land on the
    # linked /todo task (no attach, no activity-log entry, like add-cost).
    sp = sub.add_parser("add-event"); sp.add_argument("--task", required=True)
    sp.add_argument("--kind", required=True,
                    help="event kind: log|decision|milestone|summary|status|run|worker|child")
    sp.add_argument("--text", default="",
                    help="event text (truncated to %d chars)" % EVENT_TEXT_MAX)
    sp.add_argument("--session", default=None, help="session id to attribute the event to")
    sp.set_defaults(fn=cmd_add_event)

    # add-ledger — append a hub<->worker interaction to a task's provenance ledger
    # (unbounded append-only; delegate.py posts spawn/resume/stop/adopt/finish/crash).
    sp = sub.add_parser("add-ledger", help="append a hub<->worker interaction to a task's provenance ledger")
    sp.add_argument("--task", required=True)
    sp.add_argument("--action", required=True,
                    choices=["spawn", "resume", "iterate", "modify", "stop",
                             "adopt", "finish", "crash", "timeout", "stalled"])
    sp.add_argument("--worker", default=None, help="worker session uuid")
    sp.add_argument("--session", default=None, help="acting HUB session uuid")
    sp.add_argument("--detail", default=None)
    sp.set_defaults(fn=cmd_add_ledger)

    # register-worker-session — roster a worker session on a task record (name/model/
    # harness/status). Quiet bookkeeping; delegate.py posts it on spawn + terminal.
    sp = sub.add_parser("register-worker-session",
                        help="roster a worker session on a task record (#463)")
    sp.add_argument("--task", required=True)
    sp.add_argument("--session", required=True, help="worker session uuid")
    sp.add_argument("--name", default=None, help="worker display slug")
    sp.add_argument("--model", default=None)
    sp.add_argument("--harness", default="claude")
    sp.add_argument("--status", default="running")
    sp.set_defaults(fn=cmd_register_worker)

    # memo — hand a fact/decision to a task's working session(s). One subcommand
    # (send|ack|show); a shared, visible ack ledger lets multiple sessions on one task
    # coordinate without double-implementing. --task accepts any seq/id-prefix.
    sp = sub.add_parser("memo")
    sp.add_argument("sub", choices=["send", "ack", "show"], help="memo action")
    sp.add_argument("--task", default=None,
                    help="target task (seq or id-prefix); ack/show default to the "
                         "session's attached task")
    sp.add_argument("--text", default="", help="memo body (send)")
    sp.add_argument("--id", default=None, help="memo id-prefix (ack/show)")
    sp.add_argument("--session", default=None,
                    help="acting session id (signs a send; REQUIRED to ack)")
    sp.add_argument("--corrects", action="append", default=None, metavar="TARGET",
                    help="on send: declare what this memo REPLACES (repeatable) — a "
                         "memory-note slug, `decision:<n>` on the target task, or another "
                         "memo's id8. A memo that declares corrections cannot be acked "
                         "without a disposition that engages them.")
    # An ack must carry EXACTLY ONE disposition — a bare ack is an error. An ack is a
    # receipt; treating it as an integration is how a correction never lands.
    sp.add_argument("--decision", nargs="?", const=True, default=None,
                    help="ack disposition: promote the memo to a decision (optional TEXT "
                         "overrides the memo body)")
    sp.add_argument("--memory", default=None, metavar="SLUG",
                    help="ack disposition: record that it was folded into that "
                         "agent-memory note")
    sp.add_argument("--noop", default=None, metavar="REASON",
                    help="ack disposition: no durable change needed — the reason is "
                         "MANDATORY and is recorded on the ledger")
    sp.set_defaults(fn=cmd_memo)

    # F5 correspondence: link a peer pair · fork a peer node into my own task ·
    # subscribe to a peer's feed (mints memos when it advances).
    sp = sub.add_parser("link")
    sp.add_argument("--task", required=True, help="my task (seq or id-prefix)")
    sp.add_argument("--peer", required=True, help="peer task ref <alias>-<n|uuid8>")
    sp.add_argument("--session", default=None)
    sp.set_defaults(fn=cmd_link)

    sp = sub.add_parser("fork")
    sp.add_argument("--from", dest="from_ref", required=True,
                    help="peer task ref to fork <alias>-<n|uuid8>")
    sp.add_argument("--title", default=None, help="title for my forked task (default: peer's)")
    sp.add_argument("--session", default=None)
    sp.set_defaults(fn=cmd_fork)

    sp = sub.add_parser("subscribe")
    sp.add_argument("--task", required=True, help="my task (seq or id-prefix)")
    sp.add_argument("--peer", required=True, help="peer task ref <alias>-<n|uuid8>")
    sp.add_argument("--on", dest="on", default="checkpoint,decision,trail",
                    help="event kinds to watch (comma list: checkpoint,decision,trail)")
    sp.add_argument("--session", default=None)
    sp.set_defaults(fn=cmd_subscribe)

    sp = sub.add_parser("subscriptions")
    sp.add_argument("sub", nargs="?", default="check",
                    help="check (diff peer feeds, mint memos) | list")
    sp.add_argument("--throttle", action="store_true",
                    help="hook path: self-throttle + stay silent (skip if run recently)")
    sp.add_argument("--session", default=None)
    sp.set_defaults(fn=cmd_subscriptions)

    # F6 PostToolUse artifact capture — scans a tool RESULT (stdin) for PR/work-item URLs.
    sp = sub.add_parser("capture-artifacts")
    sp.add_argument("--session", required=True)
    sp.add_argument("--text", default=None,
                    help="text to scan (default: read the tool result from stdin)")
    sp.set_defaults(fn=cmd_capture_artifacts)

    sp = sub.add_parser("status"); sp.add_argument("--task", required=True)
    sp.add_argument("value", nargs="?", default=None,
                    help="new|active to set (new = the stored open); omit to report the "
                         "current status (close via /done)")
    sp.add_argument("--session", default=None, help="session id to attribute the transition to")
    sp.set_defaults(fn=cmd_status)

    sp = sub.add_parser("session-title"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_session_title)

    sp = sub.add_parser("whoami"); sp.add_argument("--session", required=True)
    sp.add_argument("--porcelain", action="store_true",
                    help="print only the attached task's seq (empty if none) for scripts")
    sp.add_argument("--statusline", action="store_true",
                    help="print a colored '#seq <dot> [TAG] title' status-bar segment (empty if no task)")
    sp.add_argument("--width", type=int, default=0,
                    help="with --statusline, truncate the title so the segment fits N columns (0 = no limit)")
    sp.set_defaults(fn=cmd_whoami)

    sp = sub.add_parser("update"); sp.add_argument("--task", required=True)
    sp.add_argument("--title", default=None); sp.add_argument("--summary", default=None)
    sp.add_argument("--append-summary", dest="append_summary", default=None)
    sp.add_argument("--restore-summary", dest="restore_summary", nargs="?", const="",
                    default=None, metavar="N",
                    help="bring back a PRESERVED previous summary — bare restores the "
                         "most recent, `<n>` an older one (1-based, as numbered by "
                         "`/todo <n> history`). `--summary` replaces wholesale, so the "
                         "text it overwrites is kept append-only; this is the inverse "
                         "that makes the replace safe. The restore is itself reversible: "
                         "the text it replaces is preserved too, and nothing is deleted.")
    sp.add_argument("--state", default=None,
                    help="set the briefing's 'where it stands / next step' line "
                         "(model-curated; '' clears it)")
    sp.add_argument("--goal", default=None,
                    help="one line: what 'done' looks like ('' clears it)")
    sp.add_argument("--step-add", dest="step_add", action="append", default=None,
                    help="append a checklist step (repeatable)")
    sp.add_argument("--step-done", dest="step_done", action="append", type=int, default=None,
                    metavar="N", help="tick step N (1-based; repeatable)")
    sp.add_argument("--step-undone", dest="step_undone", action="append", type=int, default=None,
                    metavar="N", help="untick step N (1-based; repeatable)")
    sp.add_argument("--step-supersede", dest="step_supersede", action="append", type=int,
                    default=None, metavar="N",
                    help="retire STALE step N from the checklist (1-based; repeatable). "
                         "The checklist's one reconcile verb, shaped like --supersedes: "
                         "non-destructive, so the step keeps its text in `/todo <n> "
                         "history` marked with what replaced it, and it counts in NEITHER "
                         "side of the n/m progress number. A --step-add in the same "
                         "update is recorded as the replacement. There is deliberately no "
                         "--step-edit: supersede the stale step and add a corrected one.")
    sp.add_argument("--step-restore", dest="step_restore", action="append", type=int,
                    default=None, metavar="N",
                    help="UNDO --step-supersede on step N (1-based; repeatable) — it "
                         "returns to the active checklist with its text and tick intact")
    sp.add_argument("--decision", action="append", default=None,
                    help="append a decision note (repeatable, append-only). Every "
                         "still-current decision renders in the digest — there is no age "
                         "or count limit. Past %d chars you get an advisory suggesting "
                         "`heal --split`; it never refuses, the entry is stored in full."
                         % _dec.LONG_DECISION_CHARS)
    sp.add_argument("--supersedes", action="append", type=int, default=None, metavar="N",
                    help="mark decision N (1-based, as numbered by `/todo <n> history`) as "
                         "REPLACED by the --decision in this same update; repeatable, so one "
                         "decision may replace several. A superseded decision vanishes from "
                         "the default digest and every other present-tense surface, and "
                         "survives only in `history`, marked with its replacement.")
    sp.add_argument("--pin", action="store_true", default=False,
                    help="pin the --decision in this same update. A pin is READING ORDER, "
                         "not visibility: every still-current decision renders in the "
                         "digest anyway, and pinned ones sort FIRST (marked ★) as the "
                         "architecture spine, ahead of everything else oldest-first. No "
                         "limit on how many are pinned.")
    sp.add_argument("--pin-decision", dest="pin_decision", action="append", type=int,
                    default=None, metavar="N",
                    help="pin EXISTING decision N (1-based; repeatable) — sorts it into "
                         "the digest's leading spine block")
    sp.add_argument("--unpin-decision", dest="unpin_decision", action="append", type=int,
                    default=None, metavar="N",
                    help="unpin existing decision N (1-based; repeatable) — it returns to "
                         "the oldest-first narrative block; it does NOT stop rendering")
    sp.add_argument("--restore-decision", dest="restore_decision", action="append",
                    type=int, default=None, metavar="N",
                    help="UNDO the reconcile mark on decision N (1-based; repeatable): "
                         "clears a supersede, split or merge and returns it to the "
                         "default digest. The inverse of all three verbs — nothing was "
                         "ever deleted, so any heal is reversible.")
    sp.add_argument("--log", action="append", default=None,
                    help="append a dated milestone/finding to the task's history "
                         "(repeatable, append-only). Off the default resume path — "
                         "surfaced only by `/todo <n> history`.")
    sp.add_argument("--pr", action="append", default=None,
                    help="store a PR URL on the task (repeatable, upsert by url)")
    sp.add_argument("--pr-desc", dest="pr_desc", default=None,
                    help="description for the --pr url in this update "
                         "(or the most-recent stored pr when no --pr is given)")
    sp.add_argument("--story", action="append", default=None,
                    help="store a story/work-item URL on the task (repeatable, upsert by url)")
    sp.add_argument("--story-desc", dest="story_desc", default=None,
                    help="description for the --story url in this update "
                         "(or the most-recent stored story when no --story is given)")
    sp.add_argument("--color", default=None); sp.add_argument("--effort", default=None)
    sp.add_argument("--trail-visibility", dest="trail_visibility", default=None,
                    choices=["private", "checkpoints", "full"],
                    help="F5: how much of this task's trail its feed exports — private "
                         "(default, trails never leave), checkpoints (digest only), full "
                         "(include the prompt/response trail)")
    sp.add_argument("--relate", action="append", default=None,
                    help="record a relation edge to another task by seq/id (repeatable, "
                         "idempotent). The related task's event feed hears about it too.")
    # The TYPED edge flags. One rule covers all of them: the SUBORDINATE side stores
    # the edge — the dependent, the child, the absorbed task — so every one of these
    # writes on the task being updated, and the reverse direction is derived.
    sp.add_argument("--depends-on", dest="depends_on", action="append", default=None,
                    metavar="TASK",
                    help="THIS task depends on TASK — TASK must land first (repeatable, "
                         "idempotent). Stored on the dependent, which is this task. "
                         "There is no --blocks: that is this edge read backwards, and "
                         "reverse edges are always derived, never stored. Local tasks "
                         "only. A cycle warns and still stores.")
    sp.add_argument("--parent", default=None, metavar="TASK",
                    help="TASK is THIS task's parent — at most ONE, because a task under "
                         "two parents double-counts in every roll-up. Writing a second "
                         "one REPLACES the first and says which it replaced. Stored on "
                         "the child, which is this task. Local tasks only.")
    sp.add_argument("--absorbed-by", dest="absorbed_by", default=None, metavar="TASK",
                    help="THIS task's work became part of TASK, so THIS task CLOSES. "
                         "Absorbing inherits work, so it prints a reconcile handoff for "
                         "TASK — steps are never merged automatically, and children are "
                         "never moved. (Compare --replaces, which closes the OTHER task.)")
    sp.add_argument("--replaces", action="append", default=None, metavar="TASK",
                    help="THIS task replaces TASK, so TASK CLOSES — its approach was "
                         "dropped, not absorbed, so nothing is inherited and no reconcile "
                         "is needed (repeatable). Note the direction: --replaces closes "
                         "the OTHER task, --absorbed-by closes THIS one. Spelled "
                         "`replaces`, not `supersedes`, because --supersedes already "
                         "retires a DECISION and both are valid in one command.")
    sp.add_argument("--duplicates", action="append", default=None, metavar="TASK",
                    help="THIS task and TASK are the same work (repeatable). Symmetric — "
                         "either side may declare it, it is stored once, and the reverse "
                         "reads the same. Closes nothing and decides nothing: it makes "
                         "duplication a warning instead of something someone must notice.")
    sp.add_argument("--unrelate", action="append", default=None, metavar="TASK",
                    help="remove EVERY edge this task stores to TASK, whatever the kind "
                         "(repeatable). An edge states present structure, not a "
                         "historical belief, so it is corrected rather than superseded. "
                         "Removing nothing is reported, not an error. Only touches this "
                         "task's own edges — a derived reverse edge belongs to the task "
                         "that stored it.")
    sp.add_argument("--session", default=None,
                    help="session id to attribute --relate / --summary events to (optional)")
    sp.set_defaults(fn=cmd_update)

    sp = sub.add_parser("pin"); sp.add_argument("--task", required=False, default=None)
    sp.add_argument("--session", default=None)
    sp.add_argument("--new", action="store_true",
                    help="pin a freshly-minted unborn session (claude --session-id <uuid>)")
    sp.set_defaults(fn=cmd_pin)

    sp = sub.add_parser("unpin"); sp.add_argument("--task", required=False, default=None)
    sp.add_argument("--session", default=None)
    sp.set_defaults(fn=cmd_unpin)

    sp = sub.add_parser("prompt-tint"); sp.add_argument("--session", default=None)
    sp.add_argument("--prompt", default=None); sp.set_defaults(fn=cmd_prompt_tint)

    sp = sub.add_parser("session-tint"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_session_tint)

    sp = sub.add_parser("prompt-title"); sp.add_argument("--session", default=None)
    sp.add_argument("--prompt", default=None); sp.set_defaults(fn=cmd_prompt_title)

    sp = sub.add_parser("prompt-context"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_prompt_context)

    sp = sub.add_parser("native")
    sp.set_defaults(fn=cmd_native)        # read-only listing of Claude Code's native task lists

    sp = sub.add_parser("adopt")
    sp.add_argument("--native", required=True,
                    help="native task ref <list-prefix>:<id> to promote into a durable station task")
    sp.set_defaults(fn=cmd_adopt)

    sp = sub.add_parser("guidance")
    sp.set_defaults(fn=cmd_guidance)

    sp = sub.add_parser("session-start"); sp.add_argument("--session", required=True)
    sp.add_argument("--source", default=""); sp.set_defaults(fn=cmd_session_start)

    # sweep-orphans — stop background workers whose spawning hub session is gone.
    # Called from the SessionStart hook; logs each reap to stderr, always exits 0.
    sp = sub.add_parser("sweep-orphans",
                        help="reap task-station workers whose spawning hub is gone")
    sp.add_argument("--session", default=None)
    sp.set_defaults(fn=cmd_sweep_orphans)

    # session-end — the SessionEnd hook's exact pass (roster row + feed + reap this
    # session's own workers). Idempotent, always exits 0; the SessionStart sweep above
    # stays as the crash backstop.
    sp = sub.add_parser("session-end",
                        help="record a clean session end and stop the workers it spawned")
    sp.add_argument("--session", required=True)
    sp.add_argument("--reason", default="other",
                    help="why the session ended (clear|resume|logout|prompt_input_exit|"
                         "bypass_permissions_disabled|other); an unknown value is kept verbatim")
    sp.set_defaults(fn=cmd_session_end)

    # config-change — the ConfigChange hook's path validator. Exit 2 (BLOCK) only in
    # enforce mode; the hook-health record is written first either way.
    sp = sub.add_parser("config-change",
                        help="report config-declared paths that no longer resolve")
    sp.add_argument("--session", default=None)
    sp.add_argument("--source", default="",
                    help="user_settings | project_settings | local_settings")
    sp.add_argument("--file", dest="file", default=None,
                    help="the config file that changed")
    sp.set_defaults(fn=cmd_config_change)

    # file-changed — the FileChanged hook. Acts ONLY on files inside the data dir
    # (the manifest matcher is basename-level); re-arms the checker gate.
    sp = sub.add_parser("file-changed",
                        help="re-arm the checker gate when a station config file changes")
    sp.add_argument("--session", default=None)
    sp.add_argument("--file", dest="file", default=None)
    sp.add_argument("--change", dest="change", default="",
                    help="modified | created | deleted")
    sp.set_defaults(fn=cmd_file_changed)

    # worktree-create — the OPT-IN WorktreeCreate hook (installed into the user's own
    # settings.json by `config --worktree-hook on`, never in the plugin manifest).
    # Payload on stdin; the worktree's absolute path is the first stdout line.
    sp = sub.add_parser("worktree-create",
                        help="create + provision a worktree for the WorktreeCreate hook")
    sp.set_defaults(fn=cmd_worktree_create)

    sp = sub.add_parser("repos")
    sp.add_argument("terms", nargs="*",
                    help="terms to rank repos by; omit (or 'show') to print the index. "
                         "Also: include/exclude/enrich <name>, config")
    sp.add_argument("--refresh", action="store_true", help="rescan roots + rewrite the index")
    sp.add_argument("--json", action="store_true", help="emit the structured list for the skill")
    sp.add_argument("--quiet", action="store_true", help="with --refresh, print only a one-line summary")
    sp.add_argument("--no-llm", dest="no_llm", action="store_true",
                    help="with --refresh, skip model enrichment — deterministic summary/keywords only")
    sp.add_argument("--dry-run", dest="dry_run", action="store_true",
                    help="with --refresh, report which enrich:true repos WOULD be sent — send nothing")
    sp.add_argument("--re-summarize", dest="re_summarize", action="store_true",
                    help="with --refresh, regenerate summaries even when one already exists")
    sp.add_argument("--detect-roots", dest="detect_roots", action="store_true",
                    help="propose candidate discovery roots for first-run setup")
    sp.add_argument("--set-roots", dest="set_roots", default=None,
                    help="persist a comma-separated list of discovery roots")
    sp.set_defaults(fn=cmd_repos)

    sp = sub.add_parser("obsidian")
    sp.add_argument("--sync-all", dest="sync_all", action="store_true",
                    help="(re)export every task into the configured Obsidian vault")
    sp.add_argument("--flush", dest="flush", action="store_true",
                    help="re-export ONLY the pending-resync (previously-failed) tasks "
                         "and clear their flags — cheaper than --sync-all; run from an "
                         "unsandboxed shell to drain a sandboxed-export backlog")
    sp.add_argument("--quiet", dest="quiet", action="store_true",
                    help="with --flush: suppress happy-path output (used by the hooks)")
    sp.add_argument("--status", dest="status", action="store_true",
                    help="report the Obsidian export status (default when no flag given)")
    sp.set_defaults(fn=cmd_obsidian)

    sp = sub.add_parser("usage")
    sp.add_argument("mode", nargs="?", default=None,
                    choices=["scan-all", "import-costbar"],
                    help="scan-all: ledger every transcript · import-costbar: one-time costbar cache import")
    sp.add_argument("--task", default=None)
    sp.add_argument("--refresh", action="store_true")
    sp.add_argument("--flush", action="store_true")
    sp.add_argument("--quiet", action="store_true")
    sp.add_argument("--path", default=None,
                    help="with import-costbar: path to session_totals.json (default: ~/.claude/cache/)")
    sp.add_argument("--json", dest="as_json", action="store_true")
    sp.set_defaults(fn=cmd_usage)         # WS1 usage ledger: per-task model mix + derived $

    sp = sub.add_parser("export")         # WS8 generic episodic export → any directory
    sp.add_argument("--dir", default=None, help="destination directory (created if absent)")
    sp.add_argument("--task", default=None, help="export one task (seq/id)")
    sp.add_argument("--all", dest="all", action="store_true", help="export every task (default)")
    sp.add_argument("--status", default=None, choices=["open", "active", "closed", "new"],
                    help="export only tasks in this status")
    sp.add_argument("--include", default=None,
                    help="sections to render: usage,prompts,history (default usage,history)")
    sp.add_argument("--since", default=None, help="only tasks updated at/after this ISO date")
    sp.add_argument("--prune", dest="prune", action="store_true",
                    help="reconcile --dir against live tasks: remove notes whose task "
                         "no longer exists (or was redacted) + update index.md")
    sp.set_defaults(fn=cmd_export)

    sp = sub.add_parser("sessions")       # WS5 live-session viewer: running Claude processes
    sp.add_argument("--task", default=None,
                    help="filter to one task's live sessions (seq/id)")
    sp.add_argument("--json", dest="as_json", action="store_true")
    sp.set_defaults(fn=cmd_sessions)

    sp = sub.add_parser("prompts")        # WS6 tasks-by-prompt view: the exact prompt trail
    sp.add_argument("--task", default=None)
    sp.add_argument("--json", dest="as_json", action="store_true")
    sp.add_argument("--md", dest="as_md", action="store_true",
                    help="the shareable Markdown artifact (full text + timestamps)")
    sp.add_argument("--all", action="store_true",
                    help="the complete RAW trail (every kind: commands, compaction rows, "
                         "wrappers) with no replies; default is human prompts + Claude's reply")
    sp.set_defaults(fn=cmd_prompts)

    sp = sub.add_parser("config")
    _add_config_args(sp)
    sp.set_defaults(fn=lambda a: __import__("config").cmd_config(a))

    sp = sub.add_parser("glossary")       # WS3 per-task canonical vocabulary
    _add_glossary_args(sp)
    sp.set_defaults(fn=cmd_glossary)

    sp = sub.add_parser("brief")          # WS3 deterministic house-style brief
    _add_brief_args(sp)
    sp.set_defaults(fn=cmd_brief)

    sp = sub.add_parser("recap")          # task 444: private weekly usage recap
    sp.add_argument("--week", default=None, metavar="YYYY-Www",
                    help="the ISO week to summarize (default: the current week)")
    sp.add_argument("--open", dest="open", action="store_true",
                    help="open the rendered recap in your browser (macOS)")
    sp.add_argument("--json", dest="as_json", action="store_true",
                    help="print the privacy-safe aggregate stats instead of the path")
    sp.add_argument("--no-scan", dest="no_scan", action="store_true",
                    help="skip the pre-render ledger scan (use the stored numbers as-is)")
    sp.add_argument("--auto-if-due", dest="auto_if_due", action="store_true",
                    help=argparse.SUPPRESS)   # hook entry point: gated + silent
    sp.add_argument("--quiet", dest="quiet", action="store_true", help=argparse.SUPPRESS)
    sp.set_defaults(fn=cmd_recap)

    sp = sub.add_parser("glossary-context")   # WS3 adapter hook: inject the block
    sp.add_argument("--task", default=None)
    sp.add_argument("--session", default=None)
    sp.set_defaults(fn=cmd_glossary_context)

    sp = sub.add_parser("stream")         # A-2 durable JSONL event ledger
    sp.add_argument("--since", default=None,
                    help="read events after this cursor (0-based global index)")
    sp.add_argument("--tail", nargs="?", type=int, const=20, default=None,
                    metavar="N", help="the last N events (default 20)")
    sp.add_argument("--json", action="store_true", help="emit raw JSONL envelopes")
    sp.add_argument("--backfill", action="store_true",
                    help="emit a task.snapshot per still-unstreamed task (idempotent)")
    sp.add_argument("--verify", action="store_true",
                    help="check per-task n continuity + shard order")
    sp.set_defaults(fn=cmd_stream)

    sp = sub.add_parser("redact",          # right-to-be-forgotten
                        help="scrub a task's payloads from the stream ledger")
    sp.add_argument("--task", required=True, help="task to redact (seq/id)")
    sp.add_argument("--session", default=None)
    sp.set_defaults(fn=cmd_redact)

    a = p.parse_args(argv)
    a.fn(a)


def _add_glossary_args(sp):
    """Attach the glossary command's args to a parser/subparser. Shared by main()'s
    `glossary` subcommand AND the `/todo glossary` dispatch, so the two stay identical."""
    sp.add_argument("action", nargs="?", default="list",
                    help="list | add | edit | rm | <task#> (list another task)")
    sp.add_argument("args", nargs="*",
                    help='positional term fields: add "<name>" <layer> <state> "<def>"')
    sp.add_argument("--task", default=None, help="target task (seq/id); else the session's task")
    sp.add_argument("--session", default=None)
    sp.add_argument("--layer", default=None)
    sp.add_argument("--state", default=None)
    sp.add_argument("--def", dest="definition", default=None)
    sp.add_argument("--rename", default=None, help="edit: set a new canonical name")


def _add_brief_args(sp):
    """Attach the brief command's args to a parser/subparser. Shared by main()'s
    `brief` subcommand AND the `/todo brief` dispatch, so the two stay identical."""
    sp.add_argument("action", nargs="?", default="render",
                    help="render | path — render (default) templates a brief-spec JSON; "
                         "path creates + records the output path for a model-authored brief")
    sp.add_argument("--task", default=None, help="target task (seq/id); else the session's task")
    sp.add_argument("--session", default=None)
    sp.add_argument("--spec", default=None, help="brief-spec JSON file (default: read stdin)")


def _add_config_args(sp):
    """Attach the config command's flags to a parser/subparser. Shared by main()'s
    `config` subcommand AND the `/todo config` dispatch (which parses the tokens
    after the keyword with the SAME spec), so the two stay identical."""
    sp.add_argument("--workspace-dirs", dest="workspace_dirs", default=None)
    sp.add_argument("--workspace-dirs-get", dest="workspace_dirs_get", action="store_true")
    sp.add_argument("--artifacts-root", dest="artifacts_root", nargs="?", const="", default=None,
                    help="root dir for rendered /brief artifacts (default: <data_dir>/artifacts; "
                         "TASK_STATION_ARTIFACTS_ROOT env wins; no value clears the override)")
    sp.add_argument("--artifacts-root-get", dest="artifacts_root_get", action="store_true")
    sp.add_argument("--category-pack", dest="category_pack", nargs="*", default=None,
                    help="(no arg / 'list') list packs + active · <name> select the active pack "
                         "(dev · finance · hr · exec · general + org packs; per-slot overrides still win)")
    sp.add_argument("--category-pack-get", dest="category_pack_get", action="store_true")
    sp.add_argument("--categories", dest="categories", nargs="*", default=None,
                    help="(no arg) show enabled set + toggles · 'edit' print config path")
    sp.add_argument("--enable", dest="enable", default=None,
                    help="enable a category slot (key, emoji, or [TAG])")
    sp.add_argument("--disable", dest="disable", default=None,
                    help="disable a category slot (refuses ⚫ GENERAL — permanent)")
    sp.add_argument("--auto-categories", dest="auto_categories", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="auto-enable a category slot the first time a task is assigned to it (default on)")
    sp.add_argument("--auto-categories-get", dest="auto_categories_get", action="store_true")
    sp.add_argument("--bare-cmds", dest="bare_cmds", nargs="?", choices=["on","off"], const="on", default=None)
    sp.add_argument("--bare-cmds-get", dest="bare_cmds_get", action="store_true")
    sp.add_argument("--update-check", dest="update_check", nargs="?", choices=["on","off"], const="on", default=None)
    sp.add_argument("--update-check-get", dest="update_check_get", action="store_true")
    sp.add_argument("--stream", dest="stream", nargs="?", choices=["on","off"], const="on", default=None,
                    help="the durable JSONL event ledger (internal, local-only; default on)")
    sp.add_argument("--stream-get", dest="stream_get", action="store_true")
    sp.add_argument("--stream-dir", dest="stream_dir", default=None,
                    help="external tee directory for the stream ('' clears it; default off)")
    sp.add_argument("--stream-dir-get", dest="stream_dir_get", action="store_true")
    sp.add_argument("--board-autorefresh", dest="board_autorefresh", nargs="?", choices=["on","off"], const="on", default=None,
                    help="open /todo board tab stays live via meta-refresh + Stop-hook regen (no network); default off")
    sp.add_argument("--board-autorefresh-get", dest="board_autorefresh_get", action="store_true")
    sp.add_argument("--done-closes-window", dest="done_closes_window", nargs="?", choices=["on","off"], const="on", default=None,
                    help="auto-close the terminal window ~1s after a no-arg /done closes this session's task; opt-in, default off (window stays open)")
    sp.add_argument("--done-closes-window-get", dest="done_closes_window_get", action="store_true")
    sp.add_argument("--board-browser", dest="board_browser", nargs="?", const="", default=None,
                    help='browser app the board opens in (macOS: open -a "<App>", e.g. "Google '
                         'Chrome"); no value clears it (back to the system default browser)')
    sp.add_argument("--board-browser-get", dest="board_browser_get", action="store_true")
    sp.add_argument("--interbrain", dest="interbrain", nargs="?", choices=["on", "off", "auto"],
                    const="on", default=None,
                    help="board Interbrain federation: on · off · auto (default auto → on when >1 brain/peers)")
    sp.add_argument("--interbrain-get", dest="interbrain_get", action="store_true")
    sp.add_argument("--knowledge-plane", dest="knowledge_plane", nargs="?",
                    choices=["on", "off", "auto"], const="on", default=None,
                    help="board knowledge plane: on · off · auto — the vault's notes as a "
                         "second plane above the task plane, read-only (default auto → on "
                         "when a configured vault holds at least one note)")
    sp.add_argument("--knowledge-plane-get", dest="knowledge_plane_get", action="store_true")
    sp.add_argument("--org-label", dest="org_label", nargs="?", const="", default=None,
                    help='display label for the org brain (default "Org brain"; e.g. "Company Brain"); no value clears it')
    sp.add_argument("--org-label-get", dest="org_label_get", action="store_true")
    # RETIRED (#444): there is one board now, so there is nothing to select. Still PARSED —
    # and answered with a one-line notice — so muscle memory and old scripts get an
    # explanation instead of an argparse error. Hidden from --help.
    sp.add_argument("--board-engine", dest="board_engine", nargs="?", const="",
                    default=None, help=argparse.SUPPRESS)
    sp.add_argument("--board-engine-get", dest="board_engine_get", action="store_true",
                    help=argparse.SUPPRESS)
    sp.add_argument("--theme", dest="theme", nargs="*", default=None,
                    help="(no arg) list themes + active · <name> select · save <name> · edit · preview")
    sp.add_argument("--tint-theme", dest="tint_theme", nargs="?", choices=["auto","dark","light"], const="auto", default=None,
                    help="appearance variant: auto follows the OS (dark=Dark Sands, light=Light Sands), or force dark/light")
    sp.add_argument("--tint-theme-get", dest="tint_theme_get", action="store_true")
    sp.add_argument("--tint", dest="tint", nargs="?", choices=["on","off"], const="on", default=None,
                    help="full-palette terminal tint via escape codes (default on; TASK_STATION_TINT env overrides)")
    sp.add_argument("--tint-get", dest="tint_get", action="store_true")
    sp.add_argument("--reset", dest="reset", nargs="?", const="ask", default=None,
                    help="reset ALL config settings to factory defaults — asks to confirm (tasks unaffected)")
    sp.add_argument("--title", dest="title", nargs="?", choices=["on","off"], const="on", default=None)
    sp.add_argument("--title-get", dest="title_get", action="store_true")
    sp.add_argument("--strict-delegation", dest="strict_delegation", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="install (on) / remove (off) a managed delegation-rules block in CLAUDE.md")
    # Hidden back-compat alias for the former flag name; same dest.
    sp.add_argument("--policy", dest="strict_delegation", nargs="?",
                    choices=["on", "off"], const="on", default=None, help=argparse.SUPPRESS)
    sp.add_argument("--guaranteed-tracking", dest="guaranteed_tracking", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="hook-side deterministic create+attach of a provisional task on a fresh session (default off)")
    sp.add_argument("--guaranteed-tracking-get", dest="guaranteed_tracking_get", action="store_true")
    sp.add_argument("--auto-checkpoint", dest="auto_checkpoint", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="opt-in automatic checkpointing: a PostCompact hook stashes the "
                         "compaction summary into the task history (free), plus a "
                         "post-compaction + a staleness nudge keep the digest fresh (default off)")
    sp.add_argument("--auto-checkpoint-get", dest="auto_checkpoint_get", action="store_true")
    sp.add_argument("--checkpoint-at", dest="checkpoint_at", nargs="?", const="off", default=None,
                    metavar="TOKENS",
                    help="LEGACY/fallback proactive threshold (estimated tokens): with "
                         "--auto-checkpoint on, prompt a full /todo save before auto-compaction "
                         "once the transcript-size ESTIMATE grows past this (default off; prefer "
                         "--checkpoint-pct; 0/off disables it, PostCompact stash still runs)")
    sp.add_argument("--checkpoint-at-get", dest="checkpoint_at_get", action="store_true")
    sp.add_argument("--checkpoint-pct", dest="checkpoint_pct", nargs="?", const="off", default=None,
                    metavar="PCT",
                    help="proactive context-pressure threshold as a %% of --context-window, "
                         "MEASURED from the transcript's real usage block: with --auto-checkpoint "
                         "on, prompt a full /todo save before auto-compaction once measured context "
                         "reaches this %% (default 65; 1-95; 0/off disables)")
    sp.add_argument("--checkpoint-pct-get", dest="checkpoint_pct_get", action="store_true")
    sp.add_argument("--context-window", dest="context_window", nargs="?", default=None,
                    metavar="TOKENS",
                    help="the model's context-window size, the denominator --checkpoint-pct "
                         "measures against (default 200000; raise for a larger window)")
    sp.add_argument("--context-window-get", dest="context_window_get", action="store_true")
    sp.add_argument("--checkpoint-milestone-edits", dest="checkpoint_milestone_edits",
                    nargs="?", const="off", default=None, metavar="COUNT",
                    help="with --auto-checkpoint on, fire the light staleness nudge only after "
                         "this many meaningful events (edits / promotions) since the last digest "
                         "refresh (default 5; 0/off = nudge on any staleness)")
    sp.add_argument("--checkpoint-milestone-edits-get", dest="checkpoint_milestone_edits_get",
                    action="store_true")
    sp.add_argument("--desktop-bridge", dest="desktop_bridge", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="wire the dependency-free MCP server into Claude Desktop (on) / remove it (off)")
    sp.add_argument("--statusline", dest="statusline", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="install (on) / remove (off) the opt-in self-sufficient status bar; "
                         "non-destructive, never clobbers an existing statusLine (default off)")
    sp.add_argument("--statusline-get", dest="statusline_get", action="store_true")
    sp.add_argument("--hud", dest="hud", nargs="?", choices=["on", "off"],
                    const="on", default=None,
                    help="install (on) / remove (off) the opt-in cost HUD (turn/session/"
                         "limit/week/total/task $ rows) on the status-bar host; "
                         "non-destructive, priced by the shared usage ledger (default off)")
    sp.add_argument("--hud-get", dest="hud_get", action="store_true")
    sp.add_argument("--hud-rows", dest="hud_rows", nargs="?", const="", default=None,
                    metavar="ROWS",
                    help="comma-separated cost-HUD rows to show, in order (subset of "
                         "turn,session,limits,week,total,task; default all)")
    sp.add_argument("--hud-rows-get", dest="hud_rows_get", action="store_true")
    sp.add_argument("--hud-eco", dest="hud_eco", nargs="?", choices=["on", "off"],
                    const="on", default=None,
                    help="append the eco-footprint column to the cost HUD (default off)")
    sp.add_argument("--hud-eco-get", dest="hud_eco_get", action="store_true")
    sp.add_argument("--worktree-hook", dest="worktree_hook", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="install (on) / remove (off) the opt-in WorktreeCreate "
                         "provisioner in your settings.json: new worktrees get the main "
                         "checkout's .claude/settings.local.json + a trust entry. The hook "
                         "REPLACES worktree creation while installed (default off)")
    sp.add_argument("--worktree-hook-get", dest="worktree_hook_get", action="store_true")
    sp.add_argument("--config-change-enforce", dest="config_change_enforce", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="BLOCK a settings save declaring a path that no longer exists "
                         "(default off — warn via hook-health only; a block is "
                         "transcript-silent). TASK_STATION_CONFIG_ENFORCE overrides")
    sp.add_argument("--config-change-enforce-get", dest="config_change_enforce_get",
                    action="store_true")
    sp.add_argument("--ultracode-hints", dest="ultracode_hints", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="suggest ultracode multi-agent breadth on fan-out-worthy tasks "
                         "(L/XL, or RESEARCH/REVIEW/DATA at M+) for read/think phases "
                         "only — never repo writes (default on)")
    sp.add_argument("--ultracode-hints-get", dest="ultracode_hints_get", action="store_true")
    sp.add_argument("--notify", dest="notify", nargs="?", choices=["on", "off"], const="on", default=None,
                    help="macOS banner when a delegated worker run finishes/fails (default off; "
                         "TASK_STATION_NOTIFY env overrides)")
    sp.add_argument("--notify-get", dest="notify_get", action="store_true")
    sp.add_argument("--delegate-bypass-permissions", dest="delegate_bypass_permissions",
                    nargs="?", choices=["on", "off"], const="on", default=None,
                    help="spawn --bg workers in a worktree with bypassPermissions so they "
                         "never block (default on, enforced worktree-only; "
                         "TASK_STATION_DELEGATE_BYPASS env overrides)")
    sp.add_argument("--delegate-bypass-permissions-get",
                    dest="delegate_bypass_permissions_get", action="store_true")
    sp.add_argument("--reap-workers-on-done", dest="reap_workers_on_done",
                    nargs="?", choices=["on", "off"], const="on", default=None,
                    help="stop this task's live --bg workers when it closes so they don't "
                         "linger/respawn in Agent View (default on; airtight — only a "
                         "registered, role==worker, task-station-named, idle worker is "
                         "reaped; TASK_STATION_REAP_WORKERS_ON_DONE env overrides)")
    sp.add_argument("--reap-workers-on-done-get",
                    dest="reap_workers_on_done_get", action="store_true")
    sp.add_argument("--notify-webhook", dest="notify_webhook", nargs="?", const="", default=None,
                    metavar="URL",
                    help="POST worker finished/failed events to this URL (Slack/Teams/ntfy-style "
                         "JSON receiver); no value clears it (TASK_STATION_NOTIFY_WEBHOOK overrides)")
    sp.add_argument("--notify-webhook-get", dest="notify_webhook_get", action="store_true")
    sp.add_argument("--obsidian-vault", dest="obsidian_vault", nargs="?", const="", default=None,
                    metavar="PATH",
                    help="export tasks (one-way) into this Obsidian vault; files land under "
                         "<vault>/task-station/. No value turns export OFF "
                         '(e.g. --obsidian-vault "~/Documents/Obsidian Vault")')
    sp.add_argument("--obsidian-vault-get", dest="obsidian_vault_get", action="store_true")
    sp.add_argument("--obsidian-sandbox", dest="obsidian_sandbox", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="add (on) / remove (off) the configured vault in the Claude Code "
                         "sandbox write-allowlist (sandbox.filesystem.allowWrite in your "
                         "settings.json) so in-session exports into a protected folder "
                         "(~/Documents, iCloud) write instantly; does NOT force sandbox on")
    sp.add_argument("--obsidian-sandbox-get", dest="obsidian_sandbox_get", action="store_true")
    sp.add_argument("--obsidian-daily-note", dest="obsidian_daily_note", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="append a line to the vault daily note on task close + /todo save (default off)")
    sp.add_argument("--obsidian-daily-note-get", dest="obsidian_daily_note_get", action="store_true")
    sp.add_argument("--obsidian-daily-heading", dest="obsidian_daily_heading", nargs="?",
                    const="", default=None, metavar="HEADING",
                    help='daily-note heading the entries go under (default "## Claude sessions"); '
                         "no value restores the default")
    sp.add_argument("--obsidian-daily-heading-get", dest="obsidian_daily_heading_get", action="store_true")
    sp.add_argument("--obsidian-prompts", dest="obsidian_prompts", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="write the full prompt trail (## Prompts) into exported vault notes "
                         "(default off — prompt export is opt-in; TASK_STATION_OBSIDIAN_PROMPTS overrides)")
    sp.add_argument("--obsidian-prompts-get", dest="obsidian_prompts_get", action="store_true")
    sp.add_argument("--obsidian-category-hubs", dest="obsidian_category_hubs", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="cluster the export/vault graph by category (default ON): a "
                         "[[categories/<slug>]] link in each note + a hub page per category "
                         "under <target>/categories/. Off drops the link and prunes the hub "
                         "pages on the next sync. TASK_STATION_OBSIDIAN_CATEGORY_HUBS overrides")
    sp.add_argument("--obsidian-category-hubs-get", dest="obsidian_category_hubs_get", action="store_true")
    sp.add_argument("--obsidian-subgroups", dest="obsidian_subgroups", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="emergent sub-groups within a category (default ON, nested inside "
                         "--obsidian-category-hubs): distinctive recurring title tokens auto-cluster "
                         "into nested categories/<cat-slug>/<token>.md sub-hub pages, and member notes "
                         "link the sub-hub instead of the bare category. Off prunes the sub-hubs and "
                         "reverts members on the next sync. TASK_STATION_OBSIDIAN_SUBGROUPS overrides")
    sp.add_argument("--obsidian-subgroups-get", dest="obsidian_subgroups_get", action="store_true")
    sp.add_argument("--obsidian-story-groups", dest="obsidian_story_groups", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="story hubs (default ON, nested inside --obsidian-category-hubs): tasks that "
                         "share a story id (from the structured `stories` field, referenced by >= 1 "
                         "tasks) get a cross-category stories/<id>.md hub + a [[stories/<id>]] link in "
                         "each member note, IN ADDITION to the category link. Off prunes the hubs and "
                         "drops the link. TASK_STATION_OBSIDIAN_STORY_GROUPS overrides")
    sp.add_argument("--obsidian-story-groups-get", dest="obsidian_story_groups_get", action="store_true")
    sp.add_argument("--knowledge-graph", dest="knowledge_graph", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="second-brain tier (default off): task<->note co-citation edges in the "
                         "board mini-graph + 'Related knowledge' panel, and ## Related wikilink "
                         "emission into the Obsidian mirror. Inert without an --obsidian-vault; "
                         "TASK_STATION_KNOWLEDGE_GRAPH overrides")
    sp.add_argument("--knowledge-graph-get", dest="knowledge_graph_get", action="store_true")
    sp.add_argument("--owner", dest="owner", nargs="?", const="", default=None,
                    metavar="HANDLE",
                    help="owner handle for a SHARED vault: notes nest under <target>/<owner>/ "
                         "and carry the handle (frontmatter/manifest/daily lines); no value "
                         "clears it (single-owner). Run `obsidian --sync-all` after to "
                         "relocate existing notes. TASK_STATION_OWNER overrides")
    sp.add_argument("--owner-get", dest="owner_get", action="store_true")
    sp.add_argument("--usage-tracking", dest="usage_tracking", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="track per-task model usage + derived $ from your local transcripts "
                         "(default on; reads only local files; TASK_STATION_USAGE_TRACKING overrides)")
    sp.add_argument("--usage-tracking-get", dest="usage_tracking_get", action="store_true")
    sp.add_argument("--usage-prompts", dest="usage_prompts", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="capture prompt text into the usage ledger (same-machine; default on)")
    sp.add_argument("--usage-prompts-get", dest="usage_prompts_get", action="store_true")
    sp.add_argument("--board-prompts", dest="board_prompts", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="show the captured prompt trail on the visual board "
                         "(local-only; default on)")
    sp.add_argument("--board-prompts-get", dest="board_prompts_get", action="store_true")
    sp.add_argument("--usage-billing-mode", dest="usage_billing_mode", nargs="?",
                    choices=["api", "subscription"], const="api", default=None,
                    help="frame the derived $ as metered (api) or flat-rate API-equivalent value "
                         "(subscription); default api")
    sp.add_argument("--usage-billing-mode-get", dest="usage_billing_mode_get", action="store_true")
    sp.add_argument("--recap", dest="recap", nargs="?", choices=["on", "off"],
                    const="on", default=None,
                    help="auto-generate the private weekly usage recap under <data_dir>/recaps/ "
                         "(local-only, never synced; default off; TASK_STATION_RECAP overrides)")
    sp.add_argument("--recap-get", dest="recap_get", action="store_true")
    sp.add_argument("--recap-curator-cmd", dest="recap_curator_cmd", nargs="?", const="", default=None,
                    help="command that turns recap AGGREGATE stats (JSON on stdin; never prompt "
                         "text) into 3 tailored tips; no value clears it (default off)")
    sp.add_argument("--recap-curator-cmd-get", dest="recap_curator_cmd_get", action="store_true")
    sp.add_argument("--editor-scheme", dest="editor_scheme", nargs="?", const="", default=None,
                    help="URI scheme the board uses to open file paths, e.g. cursor/vscode/zed "
                         "→ <scheme>://file/<abs>, or `file` → file://<abs>; no value AUTO-DETECTS "
                         "from your editor ($VISUAL/$EDITOR, then installed editor apps, else file)")
    sp.add_argument("--editor-scheme-get", dest="editor_scheme_get", action="store_true")


if __name__ == "__main__":
    main()
