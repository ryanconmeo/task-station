#!/usr/bin/env python3
"""claude-todo — persistent, cross-session task tracking for Claude Code.

Engine for the /todo and /done commands and the SessionStart / UserPromptSubmit
hooks. Tasks live as one JSON file per task under store/tasks/. A session is
"attached" to at most one task via a link file under store/links/<session_id>.

Subcommands:
  create  --session ID --title T --summary S   create a task, attach the session
  attach  --session ID --task REF              attach session to an existing task
  bump    --session ID                          touch the attached task's activity
  done    --session ID                          close the attached task
  render  --session ID --arg STR                /todo entrypoint (list | detail+attach)
  prompt-context --session ID                   UserPromptSubmit hook context
  session-start  --session ID --source SRC      SessionStart hook context

REF is a 1-based index from the most recent `render` listing, or a task id /
id-prefix. All writes are atomic (temp file + os.replace).
"""

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(BASE, "store")
TASKS_DIR = os.path.join(STORE, "tasks")
LINKS_DIR = os.path.join(STORE, "links")

LOG_KEEP = 25          # max activity-log entries kept per task
NUDGE_PROMPT_MAX = 120  # chars of the prompt stored in the activity log


# ---------------------------------------------------------------- storage ----

def _ensure_dirs():
    os.makedirs(TASKS_DIR, exist_ok=True)
    os.makedirs(LINKS_DIR, exist_ok=True)


def _now():
    return time.time()


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def _task_path(task_id):
    return os.path.join(TASKS_DIR, task_id + ".json")


def _atomic_write(path, text):
    tmp = path + ".tmp." + str(os.getpid())
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def load_task(task_id):
    try:
        with open(_task_path(task_id)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_task(task):
    _ensure_dirs()
    task["updated_at"] = _iso(task["updated_ts"])
    _atomic_write(_task_path(task["id"]), json.dumps(task, indent=2))


def all_tasks():
    _ensure_dirs()
    out = []
    for name in os.listdir(TASKS_DIR):
        if name.endswith(".json") and not name.endswith(".tmp"):
            t = load_task(name[:-5])
            if t:
                out.append(t)
    return out


def sorted_tasks():
    """Open before closed; within each, most recent activity first."""
    return sorted(
        all_tasks(),
        key=lambda t: (0 if t.get("status") == "open" else 1, -t.get("updated_ts", 0)),
    )


# ------------------------------------------------------------------ links ----

def _link_path(session):
    return os.path.join(LINKS_DIR, session)


def get_link(session):
    _ensure_dirs()
    try:
        with open(_link_path(session)) as f:
            task_id = f.read().strip()
    except OSError:
        return None
    return task_id or None


def set_link(session, task_id):
    _ensure_dirs()
    _atomic_write(_link_path(session), task_id)


def clear_link(session):
    try:
        os.remove(_link_path(session))
    except OSError:
        pass


# -------------------------------------------------------------- utilities ----

def rel_time(ts):
    if not ts:
        return "—"
    d = max(0, int(_now() - ts))
    if d < 60:
        return "just now"
    if d < 3600:
        return "%dm ago" % (d // 60)
    if d < 86400:
        return "%dh ago" % (d // 3600)
    if d < 7 * 86400:
        return "%dd ago" % (d // 86400)
    return datetime.fromtimestamp(ts).strftime("%b %-d")


def resolve_ref(ref):
    """Resolve a /todo argument to a task dict: 1-based index or id/prefix."""
    ref = (ref or "").strip()
    if not ref:
        return None
    listing = sorted_tasks()
    if ref.isdigit():
        i = int(ref) - 1
        return listing[i] if 0 <= i < len(listing) else None
    for t in listing:
        if t["id"] == ref or t["id"].startswith(ref):
            return t
    return None


def add_log(task, note):
    note = (note or "").strip()
    if not note:
        return
    task.setdefault("log", []).append({"ts": _iso(_now()), "note": note[:NUDGE_PROMPT_MAX]})
    task["log"] = task["log"][-LOG_KEEP:]


def touch(task, session=None, note=None, reopen=False):
    task["updated_ts"] = _now()
    if reopen and task.get("status") == "closed":
        task["status"] = "open"
    if session and session not in task.get("sessions", []):
        task.setdefault("sessions", []).append(session)
    add_log(task, note)


def new_task(title, summary):
    ts = _now()
    return {
        "id": str(uuid.uuid4()),
        "title": title.strip() or "Untitled task",
        "summary": summary.strip(),
        "status": "open",
        "created_ts": ts,
        "created_at": _iso(ts),
        "updated_ts": ts,
        "updated_at": _iso(ts),
        "sessions": [],
        "log": [],
    }


# ------------------------------------------------------------- subcommands ----

def cmd_create(a):
    task = new_task(a.title, a.summary)
    touch(task, session=a.session, note="created")
    save_task(task)
    set_link(a.session, task["id"])
    print("Created and attached to task [%s] %s" % (task["id"][:8], task["title"]))


def cmd_attach(a):
    task = resolve_ref(a.task)
    if not task:
        print("No task matching '%s'." % a.task)
        return
    reopened = task.get("status") == "closed"
    touch(task, session=a.session, note="attached", reopen=True)
    save_task(task)
    set_link(a.session, task["id"])
    print("Attached to task [%s] %s%s"
          % (task["id"][:8], task["title"], " (reopened)" if reopened else ""))


def cmd_bump(a):
    task_id = get_link(a.session)
    if not task_id:
        return
    task = load_task(task_id)
    if not task:
        return
    touch(task, session=a.session, note=os.environ.get("TODO_PROMPT", ""), reopen=True)
    save_task(task)


def cmd_done(a):
    task_id = get_link(a.session)
    task = load_task(task_id) if task_id else None
    if not task:
        print("No task is attached to this session. Nothing to close.")
        return
    task["status"] = "closed"
    touch(task, session=a.session, note="closed")
    save_task(task)
    print("Closed task [%s] %s. Send a message on it later to reopen."
          % (task["id"][:8], task["title"]))


def _format_list():
    listing = sorted_tasks()
    if not listing:
        return ("No tasks yet. One will be tracked automatically once the work "
                "in a session becomes clear, or say so explicitly.")
    lines = []
    attached_note = "  •  /todo <n> = open detail & resume   ·   /done = close current task"
    idx = 0
    last_status = None
    for t in listing:
        idx += 1
        if t["status"] != last_status:
            lines.append("")
            lines.append("OPEN" if t["status"] == "open" else "CLOSED")
            last_status = t["status"]
        lines.append("%3d  %-40.40s  %s" % (idx, t["title"], rel_time(t.get("updated_ts"))))
    return ("Tasks (open first, then by recent activity):" + attached_note + "\n"
            + "\n".join(lines))


def _format_detail(task, session):
    out = []
    out.append("Task [%s]  —  %s" % (task["id"][:8], task["status"].upper()))
    out.append("Title:   %s" % task["title"])
    out.append("Created: %s (%s)" % (rel_time(task.get("created_ts")), task.get("created_at", "")))
    out.append("Updated: %s" % rel_time(task.get("updated_ts")))
    out.append("Sessions attached: %d" % len(task.get("sessions", [])))
    out.append("")
    out.append("Summary:")
    out.append(task.get("summary") or "  (no summary recorded)")
    log = task.get("log", [])
    if log:
        out.append("")
        out.append("Recent activity:")
        for e in log[-8:]:
            out.append("  • %s" % e.get("note", ""))
    out.append("")
    out.append("This session is now ATTACHED to this task (id %s). Continue the work "
               "described above; the user's next message resumes it. To close it, use /done."
               % task["id"])
    return "\n".join(out)


def cmd_render(a):
    arg = (a.arg or "").strip()
    if not arg:
        print(_format_list())
        return
    task = resolve_ref(arg)
    if not task:
        print("No task matching '%s'.\n\n%s" % (arg, _format_list()))
        return
    touch(task, session=a.session, note="resumed", reopen=True)
    save_task(task)
    set_link(a.session, task["id"])
    print(_format_detail(task, a.session))


def cmd_prompt_context(a):
    """UserPromptSubmit: bump if attached; otherwise nudge Claude to attach/create."""
    task_id = get_link(a.session)
    task = load_task(task_id) if task_id else None
    if task:
        was_closed = task.get("status") == "closed"
        touch(task, session=a.session, note=os.environ.get("TODO_PROMPT", ""), reopen=True)
        save_task(task)
        if was_closed:
            print("[todo] Reopened task [%s] %s — this session is working on it again."
                  % (task["id"][:8], task["title"]))
        return  # attached & open: stay silent to avoid clutter

    # Not attached: surface open tasks and tell Claude how to attach/create.
    opens = [t for t in sorted_tasks() if t["status"] == "open"]
    lines = ["[todo] This session is not attached to a tracked task yet."]
    if opens:
        lines.append("Open tasks that may match what the user wants:")
        for t in opens[:8]:
            lines.append("  - [%s] %s (%s)" % (t["id"][:8], t["title"], rel_time(t.get("updated_ts"))))
    lines.append("")
    lines.append("Once you clearly understand the user's task (skip trivial questions):")
    lines.append("  • If it matches an open task above, attach to it:")
    lines.append("      python3 %s/todo.py attach --session %s --task <task-id>" % (BASE, a.session))
    lines.append("  • Otherwise create a new one:")
    lines.append("      python3 %s/todo.py create --session %s --title '<short title>' --summary '<1-3 sentence summary>'"
                 % (BASE, a.session))
    lines.append("Do this silently as a side action; do not announce it unless asked.")
    print("\n".join(lines))


def cmd_session_start(a):
    task_id = get_link(a.session)
    task = load_task(task_id) if task_id else None
    if task:
        print("[todo] This session is attached to task [%s] %s (%s). Continue it; /done to close."
              % (task["id"][:8], task["title"], task["status"]))
        return
    opens = [t for t in sorted_tasks() if t["status"] == "open"]
    if not opens:
        return
    lines = ["[todo] You have %d open task(s). If the user's request matches one, attach to it "
             "(see the per-message [todo] guidance); otherwise a new task will be tracked once "
             "the work is clear:" % len(opens)]
    for t in opens[:8]:
        lines.append("  - [%s] %s (%s)" % (t["id"][:8], t["title"], rel_time(t.get("updated_ts"))))
    print("\n".join(lines))


# ------------------------------------------------------------------- main ----

def main():
    p = argparse.ArgumentParser(prog="todo")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("create"); sp.add_argument("--session", required=True)
    sp.add_argument("--title", required=True); sp.add_argument("--summary", default="")
    sp.set_defaults(fn=cmd_create)

    sp = sub.add_parser("attach"); sp.add_argument("--session", required=True)
    sp.add_argument("--task", required=True); sp.set_defaults(fn=cmd_attach)

    sp = sub.add_parser("bump"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_bump)

    sp = sub.add_parser("done"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_done)

    sp = sub.add_parser("render"); sp.add_argument("--session", required=True)
    sp.add_argument("--arg", default=""); sp.set_defaults(fn=cmd_render)

    sp = sub.add_parser("prompt-context"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_prompt_context)

    sp = sub.add_parser("session-start"); sp.add_argument("--session", required=True)
    sp.add_argument("--source", default=""); sp.set_defaults(fn=cmd_session_start)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
