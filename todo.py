#!/usr/bin/env python3
"""claude-todo — persistent, cross-session task tracking for Claude Code.

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

REF is a 1-based index from the most recent `render` listing, or a task id /
id-prefix. All writes are atomic (temp file + os.replace).
"""

import argparse
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(BASE, "store")
TASKS_DIR = os.path.join(STORE, "tasks")
LINKS_DIR = os.path.join(STORE, "links")
DELEGATE_REGISTRY = os.path.join(BASE, "delegate", "workers.json")
PROJECTS_ROOT = os.path.expanduser("~/.claude/projects")

LOG_KEEP = 25          # max activity-log entries kept per task
NUDGE_PROMPT_MAX = 120  # chars of the prompt stored in the activity log
NUDGE_ESCALATE_AFTER = 4   # unattached prompts before the nudge escalates
SKIP_SENTINEL = "__skip__"  # link value marking a session intentionally untracked

# Categories / colours are an OPTIONAL plugin: all of that logic lives in
# categories.py. If it's absent (or fails to import), `cats` is None and the
# tracker runs plain and colourless — no tags, no --color, no tint hints. See
# categories.py and CATEGORIES.md.
try:
    import categories as cats
except Exception:
    cats = None


def cat_color(color):
    """Normalised colour to store on a task, or None when categories are off."""
    return cats.normalize(color) if cats else None


def cat_tag(color, pad=False):
    """`<emoji> [TAG]` for the list, or "" when categories are off."""
    return cats.tag(color, pad=pad) if cats else ""


def cat_lines(color):
    """Category summary + (optional) tint-command lines, or [] when off."""
    if not cats:
        return []
    out = [cats.summary(color)]
    cmd = cats.tint_command(color)
    if cmd:
        out.append("Tint this terminal to match — run:  " + cmd)
    return out


# ---------------------------------------------------------------- storage ----

def _ensure_dirs():
    os.makedirs(TASKS_DIR, exist_ok=True)
    os.makedirs(LINKS_DIR, exist_ok=True)


def _now():
    return time.time()


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def _iso_to_ts(s):
    """Epoch seconds for an ISO string written by _iso, or None if unparseable."""
    try:
        return datetime.fromisoformat(s).timestamp()
    except (TypeError, ValueError):
        return None


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


def _max_seq(tasks=None):
    tasks = tasks if tasks is not None else all_tasks()
    return max((t.get("seq") or 0 for t in tasks), default=0)


def ensure_seqs():
    """Backfill stable per-task sequence numbers, assigned in creation order.

    Every task gets a permanent `seq` the first time it's seen — the number a
    user sees in `/todo` and types as `/todo <n>`. Unlike the old render-time
    index, a task keeps its number even as others are added, closed, or reorder
    by recent activity. Idempotent: tasks that already have a seq keep it.
    """
    tasks = all_tasks()
    missing = [t for t in tasks if not t.get("seq")]
    if not missing:
        return
    n = _max_seq(tasks)
    for t in sorted(missing, key=lambda t: t.get("created_ts", 0)):
        n += 1
        t["seq"] = n
        save_task(t)


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


def _count_path(session):
    return _link_path(session) + ".n"


def get_count(session):
    """How many prompts this session has gone without attaching to a task."""
    try:
        with open(_count_path(session)) as f:
            return int(f.read().strip() or 0)
    except (OSError, ValueError):
        return 0


def bump_count(session):
    n = get_count(session) + 1
    _atomic_write(_count_path(session), str(n))
    return n


def clear_count(session):
    try:
        os.remove(_count_path(session))
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
    """Resolve a /todo argument to a task dict.

    An all-digit ref is matched against tasks' stable `seq` numbers (the numbers
    shown in the listing). Anything else — or a digit string matching no seq —
    is matched against task ids by exact match or prefix, so a longer all-digit
    id prefix that happens to contain no hex letters (e.g. "03471986") still
    resolves correctly.
    """
    ref = (ref or "").strip()
    if not ref:
        return None
    ensure_seqs()
    listing = sorted_tasks()
    if ref.isdigit():
        i = int(ref)
        for t in listing:
            if t.get("seq") == i:
                return t
        # No task with that number: fall through and treat as an id prefix.
    for t in listing:
        if t["id"] == ref or t["id"].startswith(ref):
            return t
    return None


_DEDUP_STOPWORDS = {
    "the", "and", "for", "with", "from", "into", "all", "new", "add", "fix",
    "update", "make", "use", "via", "per", "out", "off", "this", "that",
}


def _norm_tokens(s):
    toks = re.findall(r"[a-z0-9]+", (s or "").lower())
    return {t for t in toks if len(t) > 2 and t not in _DEDUP_STOPWORDS}


def similar_open_task(title):
    """Return the most similar OPEN task if its title strongly overlaps `title`.

    Scores the larger of Jaccard overlap and containment (share of the new
    title's tokens already covered by an existing one). Containment only counts
    when at least two tokens are shared, so a one-word title can't match
    everything. Containment handles noise like a trailing "(Volt-1234)" tag and
    singular/plural drift that would otherwise sink a pure-Jaccard score.
    """
    want = _norm_tokens(title)
    if not want:
        return None
    best, best_score = None, 0.0
    for t in sorted_tasks():
        if t.get("status") != "open":
            continue
        have = _norm_tokens(t.get("title", ""))
        if not have:
            continue
        inter = len(want & have)
        jaccard = inter / len(want | have)
        containment = inter / len(want) if inter >= 2 else 0.0
        score = max(jaccard, containment)
        if score > best_score:
            best, best_score = t, score
    return best if best_score >= 0.6 else None


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
    if session:
        if session not in task.get("sessions", []):
            task.setdefault("sessions", []).append(session)
        # Record where this session is running so /todo can later hand back a
        # `cd … && claude --resume …` one-liner that reopens it in the right dir.
        task.setdefault("session_meta", {})[session] = {"cwd": os.getcwd(), "ts": _now(), "role": "hub"}
    add_log(task, note)


def _project_dir_for(cwd):
    """The session-transcript bucket Claude Code uses for a given launch cwd."""
    return os.path.join(PROJECTS_ROOT, cwd.replace("/", "-"))


def _session_msgcount(path):
    """Count of non-empty, non-system user messages in a transcript (0 if unreadable).

    Used to tell a real working session from an empty/stray one — size alone lies
    (a freshly-spawned empty session can still be several KB of system init)."""
    n = 0
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                msg = o.get("message")
                if not isinstance(msg, dict) or msg.get("role") != "user":
                    continue
                c = msg.get("content")
                if isinstance(c, str):
                    t = c
                elif isinstance(c, list):
                    t = " ".join(b.get("text", "") for b in c
                                 if isinstance(b, dict) and b.get("type") == "text")
                else:
                    t = ""
                t = t.strip()
                if t and not t.startswith("<"):
                    n += 1
    except OSError:
        return 0
    return n


def _find_session_path(sid):
    """Locate a session's transcript across ALL project buckets.

    A session's bucket is its LAUNCH cwd, which can differ from whatever cwd /todo
    happened to record (e.g. you launched from ~ but cd'd into a worktree before the
    task was touched). So we search every bucket by session id rather than trusting
    the recorded cwd. Returns the `.jsonl` path, or None."""
    try:
        buckets = os.listdir(PROJECTS_ROOT)
    except OSError:
        return None
    for b in buckets:
        p = os.path.join(PROJECTS_ROOT, b, sid + ".jsonl")
        if os.path.exists(p):
            return p
    return None


def _session_cwd(path):
    """The cwd a session was launched in, read from the transcript itself —
    authoritative (Claude Code records it on every entry), and decode-free (we never
    have to reverse the lossy bucket-name encoding). None if unreadable/absent."""
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    cwd = json.loads(line).get("cwd")
                except ValueError:
                    continue
                if cwd:
                    return cwd
    except OSError:
        return None
    return None


def _load_delegate_registry():
    try:
        with open(DELEGATE_REGISTRY) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def worker_lines(task):
    """Resume lines for the in-project workers this task has delegated into.

    Worker session-ids are read LIVE from the delegate registry (keyed
    <seq>:<project>[:<label>]) so they reflect delegate's own self-healing rather
    than a stale snapshot. Lists the default worker per repo plus any labelled
    concurrent ones. Returns [] when the task has no recorded projects."""
    projects = task.get("projects") or []
    if not projects:
        return []
    reg = _load_delegate_registry()
    seq = task.get("seq")
    out = []
    for p in projects:
        base = "%s:%s" % (seq, p)
        keys = sorted(k for k in reg if k == base or k.startswith(base + ":"))
        if not keys:
            out.append("    %-22s (no worker recorded yet)" % p)
            continue
        for k in keys:
            e = reg.get(k, {})
            d, sid = e.get("dir"), e.get("session_id")
            disp = p if k == base else "%s [%s]" % (p, e.get("label") or k.split(":", 2)[-1])
            if d and sid:
                out.append("    %-22s cd %s && claude --resume %s" % (disp, d, sid))
            elif d:
                out.append("    %-22s cd %s && claude   (no active worker yet)" % (disp, d))
            else:
                out.append("    %-22s (no worker recorded yet)" % disp)
    return out


def resume_command(task, current_session=None):
    """`cd <dir> && claude …` for the HUB session that holds this task's context.

    GUARANTEE: only ever resumes one of THIS task's own recorded sessions — never
    another task's. (Critical: every hub shares the home bucket, so a whole-bucket
    fallback or `claude --continue` could resume an unrelated task. We never do that.)

    SELF-CORRECTING cwd: the resume directory comes from the *transcript itself* (its
    recorded launch cwd), located by searching every bucket for the session id — NOT
    from whatever cwd /todo happened to capture. So a session is still found and
    resumed correctly even if it was recorded against the wrong directory (e.g. you
    launched from ~ but cd'd into a worktree before the task was touched). Prefers the
    most recent SUBSTANTIVE session (so merely opening `/todo <n>` to look — a 1-2
    message session — never displaces the real working session); if none of the
    task's sessions have a findable live transcript, starts fresh. Returns None only
    when there are no recorded sessions."""
    meta = task.get("session_meta") or {}
    if not meta:
        return None
    # An explicit pin wins (PK-style): always resume that exact session, with the cwd
    # self-corrected from its transcript. Falls through to the heuristic only if the
    # pinned session has no findable live transcript (so a stale pin can't strand you).
    pin = task.get("pinned_session")
    if pin:
        path = _find_session_path(pin)
        if path and _session_msgcount(path) >= 1:
            cwd = _session_cwd(path) or (meta.get(pin) or {}).get("cwd")
            if cwd:
                return "cd %s && claude --resume %s" % (cwd, pin)
    hubs = [(sid, m) for sid, m in meta.items() if m.get("role") == "hub"]
    pool = hubs or list(meta.items())
    # For each of THIS task's sessions, find its transcript ANYWHERE and read the
    # cwd from the transcript — independent of the (possibly wrong) recorded cwd.
    live = []
    for sid, m in pool:
        path = _find_session_path(sid)
        if not path:
            continue
        msgs = _session_msgcount(path)
        if msgs >= 1:
            cwd = _session_cwd(path) or m.get("cwd")
            if cwd:
                live.append((sid, cwd, os.path.getmtime(path), msgs))
    if live:
        # Prefer SUBSTANTIVE sessions: a session that merely ran `/todo <n>` to look
        # has 1-2 messages and must not displace the real working session. Among
        # sessions past a small substance floor, take the most recent; only if none
        # clear the floor do we fall back to the most recent of any.
        SUBSTANCE_FLOOR = 3
        cands = [x for x in live if x[3] >= SUBSTANCE_FLOOR] or live
        cands = [x for x in cands if x[0] != current_session] or cands
        cands.sort(key=lambda x: x[2], reverse=True)   # newest transcript first
        sid, cwd, _, _ = cands[0]
        return "cd %s && claude --resume %s" % (cwd, sid)
    # No findable live transcript for any recorded session → fresh start
    # (NEVER --continue, which in the shared home bucket could resume a different task).
    pool.sort(key=lambda kv: kv[1].get("ts", 0), reverse=True)
    for sid, m in pool:
        if m.get("cwd"):
            return ("cd %s && claude   # no live session found — starting fresh; "
                    "re-attach with /todo %s" % (m["cwd"], task.get("seq", "")))
    return None


def new_task(title, summary, color=None):
    ts = _now()
    t = {
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
    c = cat_color(color)
    if c is not None:
        t["color"] = c
    return t


# ------------------------------------------------------------- subcommands ----

def cmd_create(a):
    if not getattr(a, "force", False):
        dup = similar_open_task(a.title)
        if dup:
            print("Not created — likely a duplicate of open task [%s] %s.\n"
                  "Attach instead:  python3 %s/todo.py attach --session %s --task %s\n"
                  "Or re-run create with --force to make a separate task."
                  % (dup["id"][:8], dup["title"], BASE, a.session, dup["id"][:8]))
            return
    requested = getattr(a, "color", None)
    if cats and requested and not cats.is_known(requested):
        print("⚠ --color '%s' is not a known category; defaulting to %s. "
              "Recategorize later with: attach --color <key|emoji|[TAG]>."
              % (requested, cats.DEFAULT))
    task = new_task(a.title, a.summary, requested)
    ensure_seqs()                      # number any pre-seq tasks before we pick ours
    task["seq"] = _max_seq() + 1       # stable number, never reused even after /done
    touch(task, session=a.session, note="created")
    save_task(task)
    set_link(a.session, task["id"])
    clear_count(a.session)
    print("Created and attached to task [%s] %s" % (task["id"][:8], task["title"]))
    for line in cat_lines(task.get("color")):
        print(line)


def cmd_attach(a):
    task = resolve_ref(a.task)
    if not task:
        print("No task matching '%s'." % a.task)
        return
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
                  "emoji, or [TAG] — e.g. brown, 🟤, or MIGRATION. (Keeping %s.)"
                  % (requested, task.get("color") or cats.DEFAULT))
            if not task.get("color"):
                task["color"] = cats.DEFAULT
        elif not task.get("color"):
            task["color"] = cats.DEFAULT
    touch(task, session=a.session, note="attached", reopen=True)
    save_task(task)
    set_link(a.session, task["id"])
    clear_count(a.session)
    print("Attached to task [%s] %s%s"
          % (task["id"][:8], task["title"], " (reopened)" if reopened else ""))
    for line in cat_lines(task.get("color")):
        print(line)


def cmd_bump(a):
    task_id = get_link(a.session)
    if not task_id:
        return
    task = load_task(task_id)
    if not task:
        return
    touch(task, session=a.session, note=os.environ.get("TODO_PROMPT", ""), reopen=True)
    save_task(task)


def cmd_skip(a):
    set_link(a.session, SKIP_SENTINEL)
    clear_count(a.session)
    print("This session is marked untracked — the [todo] nudge will stay silent. "
          "Attaching to or creating a task later resumes tracking.")


def cmd_done(a):
    task_id = get_link(a.session)
    task = load_task(task_id) if task_id else None
    if not task:
        print("No task is attached to this session. Nothing to close.")
        return
    task["status"] = "closed"
    touch(task, session=a.session, note="closed")
    save_task(task)
    clear_link(a.session)   # detach so a later message can't silently reopen it
    clear_count(a.session)
    print("Closed task [%s] %s and detached this session. Reopen later with /todo."
          % (task["id"][:8], task["title"]))


def _format_list():
    ensure_seqs()                      # guarantee every task has its stable number
    listing = sorted_tasks()
    if not listing:
        return ("No tasks yet. One will be tracked automatically once the work "
                "in a session becomes clear, or say so explicitly.")
    lines = []
    attached_note = "  •  /todo <n> = open detail & resume   ·   /done = close current task"
    last_status = None
    for t in listing:
        if t["status"] != last_status:
            lines.append("")
            lines.append("OPEN" if t["status"] == "open" else "CLOSED")
            last_status = t["status"]
        tag = cat_tag(t.get("color"), pad=True)
        if tag:
            lines.append("%3d  %-40.40s  %s  %s"
                         % (t["seq"], t["title"], tag, rel_time(t.get("updated_ts"))))
        else:
            lines.append("%3d  %-40.40s  %s"
                         % (t["seq"], t["title"], rel_time(t.get("updated_ts"))))
    if cats:
        lines.append("")
        lines.append(cats.legend())
    return ("Tasks (open first, then by recent activity):" + attached_note + "\n"
            + "\n".join(lines))


def _format_detail(task, session):
    out = []
    out.append("Task [%s]  —  %s" % (task["id"][:8], task["status"].upper()))
    out.append("Title:   %s" % task["title"])
    if cats:
        out.append(cats.summary(task.get("color")))
    out.append("Created: %s (%s)" % (rel_time(task.get("created_ts")), task.get("created_at", "")))
    out.append("Updated: %s" % rel_time(task.get("updated_ts")))
    out.append("Sessions attached: %d" % len(task.get("sessions", [])))
    out.append("")
    out.append("Summary:")
    out.append(task.get("summary") or "  (no summary recorded)")
    log = task.get("log", [])
    if log:
        out.append("")
        out.append("Recent activity (most recent last):")
        for e in log[-12:]:
            when = rel_time(_iso_to_ts(e.get("ts", "")))
            out.append("  • [%s] %s" % (when, e.get("note", "")))
    out.append("")
    out.append("This session is now ATTACHED to this task (id %s). Continue the work "
               "described above; the user's next message resumes it. To close it, use /done."
               % task["id"])
    if cats:
        cmd = cats.tint_command(task.get("color"))
        if cmd:
            out.append("Tint this terminal to match the category — run:  " + cmd)
    resume = resume_command(task, session)
    workers = worker_lines(task)
    if resume or workers:
        out.append("")
        out.append("Resume the working session that holds this task's context "
                   "(cd + resume, one command):")
        if resume:
            out.append("    Hub%s:  %s" % (" (pinned)" if task.get("pinned_session") else "", resume))
        if workers:
            out.append("  In-project workers this task has delegated into "
                       "(drop into one directly to debug a repo):")
            out.extend(workers)
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
    clear_count(a.session)
    print(_format_detail(task, a.session))


def cmd_add_project(a):
    """Record that a task has delegated work into a repo (project). Idempotent.

    Called by delegate.py when a worker is spawned with --seq, so /todo can
    list the task's in-project workers in its detail view. No session attach, no
    activity-log entry — keeps the link bookkeeping quiet."""
    task = resolve_ref(a.task) or load_task(a.task)
    if not task:
        sys.stderr.write("add-project: no task matching %r\n" % a.task)
        return
    projs = task.setdefault("projects", [])
    if a.project not in projs:
        projs.append(a.project)
        task["updated_ts"] = _now()
        save_task(task)


def cmd_session_title(a):
    """Print the window/title-bar label for an attached session (or nothing).

    The SessionStart hook puts this in hookSpecificOutput.sessionTitle so the
    terminal reads `todo-<seq> · <title>` — the closest we get to auto-labelling
    the hub (the resume-NAME can't be set programmatically on a running session)."""
    task_id = get_link(a.session)
    if not task_id or task_id == SKIP_SENTINEL:
        return
    task = load_task(task_id)
    if not task:
        return
    ensure_seqs()
    print("todo-%s · %s" % (task.get("seq", "?"), task["title"]))


def cmd_whoami(a):
    """Map any session id → its task. The backstop that identifies a session
    regardless of whether it was ever named."""
    task_id = get_link(a.session)
    if task_id == SKIP_SENTINEL:
        print("session %s: intentionally untracked (skipped)" % a.session[:8])
        return
    task = load_task(task_id) if task_id else None
    if not task:
        print("session %s: not attached to any task" % a.session[:8])
        return
    ensure_seqs()
    print("session %s → todo %s · %s (%s)"
          % (a.session[:8], task.get("seq", "?"), task["title"], task["status"]))


def cmd_update(a):
    """Amend a task's title / summary / scope / colour after creation.

    Fills the gap that `summary` was otherwise frozen at create — keeps the task
    description current as scope drifts."""
    task = resolve_ref(a.task) or load_task(a.task)
    if not task:
        sys.stderr.write("update: no task matching %r\n" % a.task)
        return
    changed = []
    if a.title is not None:
        task["title"] = a.title.strip(); changed.append("title")
    if a.summary is not None:
        task["summary"] = a.summary.strip(); changed.append("summary")
    if a.append_summary:
        base = (task.get("summary") or "").rstrip()
        add = a.append_summary.strip()
        task["summary"] = (base + "\n" + add) if base else add
        changed.append("summary+")
    if a.color is not None and cats:
        task["color"] = cat_color(a.color); changed.append("color")
    if not changed:
        print("update: nothing to change (pass --title/--summary/--append-summary/--color)")
        return
    touch(task, note="scope updated: " + ", ".join(changed))
    save_task(task)
    print("updated task %s: %s" % (task.get("seq", task["id"][:8]), ", ".join(changed)))


def cmd_pin(a):
    """Pin a specific session as the task's canonical resume target (PK-style).

    `/todo` then always resumes THIS session, overriding the most-recent-substantive
    heuristic — the cwd is still read live from the transcript, so the pin survives
    directory changes. A pin with no findable live transcript is ignored (falls back
    to the heuristic) so it can't strand you."""
    task = resolve_ref(a.task) or load_task(a.task)
    if not task:
        sys.stderr.write("pin: no task matching %r\n" % a.task)
        return
    task["pinned_session"] = a.session
    meta = task.setdefault("session_meta", {})
    if a.session not in meta:
        path = _find_session_path(a.session)
        meta[a.session] = {"cwd": (_session_cwd(path) if path else None) or os.getcwd(),
                           "ts": _now(), "role": "hub"}
    touch(task, note="pinned resume session %s" % a.session[:8])
    save_task(task)
    label = task.get("seq", task["id"][:8])
    if _find_session_path(a.session):
        print("Pinned task %s → session %s\n  resume: %s"
              % (label, a.session[:8], resume_command(task)))
    else:
        print("Pinned task %s → session %s — note: no transcript found for that id yet; "
              "/todo falls back to the heuristic until it appears." % (label, a.session[:8]))


def cmd_unpin(a):
    """Drop a task's pinned resume session — revert to most-recent-substantive."""
    task = resolve_ref(a.task) or load_task(a.task)
    if not task:
        sys.stderr.write("unpin: no task matching %r\n" % a.task)
        return
    if task.pop("pinned_session", None):
        touch(task, note="unpinned resume session")
        save_task(task)
        print("Unpinned task %s — resume reverts to most-recent-substantive."
              % task.get("seq", task["id"][:8]))
    else:
        print("Task %s was not pinned." % task.get("seq", task["id"][:8]))


def cmd_prompt_color(a):
    """Print the category colour a skill-invocation prompt maps to (or nothing).

    The UserPromptSubmit hook calls this FIRST and, if it prints a colour, runs
    `zsh -ic '<colour>'` straight away — so a skill like /volt:review-pr-auto
    tints the terminal the instant it's run, before Claude responds. Silent
    (prints nothing) when categories are off, the prompt isn't a skill, or the
    skill has no mapping."""
    if not cats or not hasattr(cats, "color_for_prompt"):
        return
    prompt = a.prompt if getattr(a, "prompt", None) is not None else os.environ.get("TODO_PROMPT", "")
    color = cats.color_for_prompt(prompt)
    if color:
        print(color)


def cmd_prompt_context(a):
    """UserPromptSubmit: bump if attached; otherwise nudge Claude to attach/create."""
    task_id = get_link(a.session)
    if task_id == SKIP_SENTINEL:
        return  # session intentionally untracked: stay silent

    task = load_task(task_id) if task_id else None
    if task:
        was_closed = task.get("status") == "closed"
        touch(task, session=a.session, note=os.environ.get("TODO_PROMPT", ""), reopen=True)
        save_task(task)
        if was_closed:
            print("[todo] Reopened task [%s] %s — this session is working on it again."
                  % (task["id"][:8], task["title"]))
        return  # attached & open: stay silent to avoid clutter

    # Not attached: count the miss, surface open tasks, and nudge Claude.
    n = bump_count(a.session)
    opens = [t for t in sorted_tasks() if t["status"] == "open"]
    lines = ["[todo] This session is not attached to a tracked task yet."]
    if opens:
        lines.append("Open tasks that may match what the user wants:")
        for t in opens[:8]:
            lines.append("  - [%s] %s (%s)" % (t["id"][:8], t["title"], rel_time(t.get("updated_ts"))))
    lines.append("")

    if n >= NUDGE_ESCALATE_AFTER:
        lines.append("⚠ %d messages in and still untracked. If this session is doing real "
                     "work, attach or create a task NOW. If it is just Q&A, silence this with:" % n)
        lines.append("      python3 %s/todo.py skip --session %s" % (BASE, a.session))
        lines.append("")

    lines.append("Track this session (attach or create) the moment ALL of these hold:")
    lines.append("  - it is a concrete task, not a question / explanation / discussion")
    lines.append("  - acting on it will edit files, run a multi-step process, or take more than ~2-3 tool calls")
    lines.append("  - you understand it well enough to write a one-line title")
    lines.append('TRACK examples:  "duplicate the review skills", "add dark mode", "fix the auth bug"')
    lines.append('SKIP examples:   "what does this do?", "when is X true?", "reword this", a one-line typo fix')
    lines.append("If you have already started editing files and still are not attached — attach now.")
    if cats:
        skill_color = (cats.color_for_prompt(os.environ.get("TODO_PROMPT", ""))
                       if hasattr(cats, "color_for_prompt") else None)
        if skill_color:
            lines.append("This prompt invokes a skill mapped to category '%s' (%s) — the terminal "
                         "has ALREADY been tinted to it by the hook. Prefer --color %s when you "
                         "create/attach this task, and DON'T re-run the tint alias."
                         % (skill_color, cats.label(skill_color), skill_color))
        lines.extend(cats.picker_lines())
        lines.append("  • Matches an open task above → attach (pass --color to set OR recategorize it; accepts a key, emoji, or [TAG]):")
        lines.append("      python3 %s/todo.py attach --session %s --task <task-id> [--color <color>]" % (BASE, a.session))
        lines.append("  • Otherwise → create with its colour:")
        lines.append("      python3 %s/todo.py create --session %s --color <color> --title '<short title>' --summary '<1-3 sentence summary>'"
                     % (BASE, a.session))
        if cats.TINT_TERMINAL and not skill_color:
            lines.append("The command prints the category and a `zsh -ic '<color>'` line — RUN that "
                         "alias to tint this terminal to the task's colour.")
    else:
        lines.append("  • Matches an open task above → attach:")
        lines.append("      python3 %s/todo.py attach --session %s --task <task-id>" % (BASE, a.session))
        lines.append("  • Otherwise → create:")
        lines.append("      python3 %s/todo.py create --session %s --title '<short title>' --summary '<1-3 sentence summary>'"
                     % (BASE, a.session))
    lines.append("Do this as a side action, but DO tell the user in one short line when you "
                 "create or attach a task — e.g. \"📋 Tracking this as a new task: <title>\" or "
                 "\"📋 Attached to existing task: <title>\".")
    print("\n".join(lines))


def cmd_session_start(a):
    task_id = get_link(a.session)
    if task_id == SKIP_SENTINEL:
        return  # session intentionally untracked: stay silent
    task = load_task(task_id) if task_id else None
    if task:
        msg = ["[todo] This session is attached to task [%s] %s (%s). Continue it; /done to close."
               % (task["id"][:8], task["title"], task["status"])]
        msg.extend(cat_lines(task.get("color")))
        print("\n".join(msg))
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
    sp.add_argument("--color", default=None)
    sp.add_argument("--force", action="store_true"); sp.set_defaults(fn=cmd_create)

    sp = sub.add_parser("attach"); sp.add_argument("--session", required=True)
    sp.add_argument("--task", required=True); sp.add_argument("--color", default=None)
    sp.set_defaults(fn=cmd_attach)

    sp = sub.add_parser("bump"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_bump)

    sp = sub.add_parser("skip"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_skip)

    sp = sub.add_parser("done"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_done)

    sp = sub.add_parser("render"); sp.add_argument("--session", required=True)
    sp.add_argument("--arg", default=""); sp.set_defaults(fn=cmd_render)

    sp = sub.add_parser("add-project"); sp.add_argument("--task", required=True)
    sp.add_argument("--project", required=True); sp.set_defaults(fn=cmd_add_project)

    sp = sub.add_parser("session-title"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_session_title)

    sp = sub.add_parser("whoami"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_whoami)

    sp = sub.add_parser("update"); sp.add_argument("--task", required=True)
    sp.add_argument("--title", default=None); sp.add_argument("--summary", default=None)
    sp.add_argument("--append-summary", dest="append_summary", default=None)
    sp.add_argument("--color", default=None); sp.set_defaults(fn=cmd_update)

    sp = sub.add_parser("pin"); sp.add_argument("--task", required=True)
    sp.add_argument("--session", required=True); sp.set_defaults(fn=cmd_pin)

    sp = sub.add_parser("unpin"); sp.add_argument("--task", required=True)
    sp.set_defaults(fn=cmd_unpin)

    sp = sub.add_parser("prompt-color"); sp.add_argument("--session", default=None)
    sp.add_argument("--prompt", default=None); sp.set_defaults(fn=cmd_prompt_color)

    sp = sub.add_parser("prompt-context"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_prompt_context)

    sp = sub.add_parser("session-start"); sp.add_argument("--session", required=True)
    sp.add_argument("--source", default=""); sp.set_defaults(fn=cmd_session_start)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
