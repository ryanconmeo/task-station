# live_sessions.py
"""Live Claude Code session viewer — the process-state layer behind the
`task-station sessions` command, the `/todo <n>` detail annotations, and the
board's live strip.

Claude Code drops one `~/.claude/sessions/<PID>.json` file per RUNNING process
(interactive hub AND `claude -p` delegated workers), shaped:

    {pid, sessionId, cwd, kind, entrypoint, name, status: busy|idle,
     startedAt, updatedAt}

This is genuine process state — distinct from task-station's own "live" notions
(`store.links` link-state, `workers.json` registry snapshot), which persist past
a crash. Joining the two is what turns "I only see python3 shell details" into a
row that names the task and hands back a one-command resume.

Reads ONLY the user's own local files; nothing leaves the machine. The sessions
dir is env-overridable (TASK_STATION_SESSIONS_DIR, else CLAUDE_CONFIG_DIR) so the
tests can point it at a fixture dir. Stale files left by a crash are TOLERATED —
a dead pid is skipped, never deleted.
"""
import json
import os

import paths
import store as store_mod


def sessions_dir():
    """The dir holding one `<PID>.json` per running Claude Code process.

    Override order: TASK_STATION_SESSIONS_DIR (explicit / tests) →
    `<CLAUDE_CONFIG_DIR or ~/.claude>/sessions` (tracks a moved ~/.claude exactly
    like the rest of the plugin). Resolved on every call so a test's env change
    takes effect without reimporting."""
    override = os.environ.get("TASK_STATION_SESSIONS_DIR")
    if override:
        return os.path.expanduser(override)
    base = os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")
    return os.path.join(os.path.expanduser(base), "sessions")


def pid_alive(pid):
    """True when a process with `pid` currently exists.

    `os.kill(pid, 0)` is the canonical liveness probe: it delivers no signal,
    only running the existence + permission check. ProcessLookupError → the pid is
    gone (crashed/exited); PermissionError → the process exists but is owned by
    another user (still counts as running). A non-int / non-positive / absent pid
    reads as dead. Tests monkeypatch this to fake alive/dead pids without spawning
    real processes."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _load_session_file(path):
    """Parse one `<PID>.json` into a dict, or None. Fully defensive — a malformed
    / half-written / non-object file is skipped (never raised on, never deleted)."""
    try:
        with open(path) as f:
            obj = json.load(f)
    except (OSError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _parse_ts(v):
    """Best-effort epoch seconds from a sessions-file timestamp, which may be a
    number (epoch) or an ISO-8601 string. None when absent/unparseable."""
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str) and v:
        s = v.strip().replace("Z", "+00:00")
        try:
            from datetime import datetime
            return datetime.fromisoformat(s).timestamp()
        except ValueError:
            return None
    return None


def _worker_index():
    """Map each recorded worker `session_id` → its registry entry
    ({label, seq, project, dir}) from `workers.json`. Empty on any read/parse
    failure — labels are a nicety, never a hard dependency."""
    reg_path = os.path.join(paths.data_dir(), "workers.json")
    try:
        with open(reg_path) as f:
            reg = json.load(f)
    except (OSError, ValueError):
        return {}
    out = {}
    if isinstance(reg, dict):
        for _key, e in reg.items():
            if not isinstance(e, dict):
                continue
            sid = e.get("session_id")
            if sid:
                out[sid] = {"label": e.get("label"), "seq": e.get("seq"),
                            "project": e.get("project"), "dir": e.get("dir")}
    return out


def _role(rec, winfo):
    """The session's role for display: an explicit `kind` (hub/worker) wins; else
    a registry hit means it's a delegated worker; else map the transcript
    `entrypoint` (sdk-cli → worker, cli → hub); else 'unknown'."""
    kind = rec.get("kind")
    if kind in ("hub", "worker"):
        return kind
    if winfo:
        return "worker"
    ep = rec.get("entrypoint")
    if ep == "sdk-cli":
        return "worker"
    if ep == "cli":
        return "hub"
    return kind or "unknown"


def running():
    """Every ACTUALLY-running Claude Code session as a list of row dicts:

        {session_id, pid, task_id, task_seq, task_title, role, label, status,
         updated_ts, cwd, resume_command}

    Enumerates the sessions dir, keeps only rows whose pid is alive (dead/stale
    files are skipped, NEVER deleted), joins each `sessionId` to a task via
    `store.links` (→ seq + title) and to `workers.json` for a worker label.

    `resume_command` is the `cd <cwd> && claude --resume <sid>` one-liner (the
    same shape `_resume_target` emits in task-station.py) so every live row is one
    copy-paste from being reopened — this is what replaces the raw "python3 shell
    details". None when the session has no recorded cwd. Rows are sorted
    most-recently-active first."""
    d = sessions_dir()
    try:
        names = os.listdir(d)
    except OSError:
        return []
    backend = store_mod.get_backend(os.path.join(paths.data_dir(), "store"))
    wmap = _worker_index()
    rows = []
    for name in names:
        if not name.endswith(".json"):
            continue
        rec = _load_session_file(os.path.join(d, name))
        if rec is None:
            continue                       # malformed → skip (tolerate, never delete)
        if not pid_alive(rec.get("pid")):
            continue                       # dead/crashed/stale → skip (never delete)
        sid = rec.get("sessionId")
        cwd = rec.get("cwd")
        # Join to a task through the link store (the link resolving back to a task
        # is what makes this row "task 360" instead of a bare pid).
        task_seq = task_title = None
        task_id = backend.get_link(sid) if sid else None
        if task_id:
            try:
                t = backend.load_task(task_id)
            except Exception:
                t = None
            if t:
                task_seq = t.get("seq")
                task_title = t.get("title")
        winfo = wmap.get(sid) or {}
        label = rec.get("name") or winfo.get("label")
        resume = ("cd %s && claude --resume %s" % (cwd, sid)) if (cwd and sid) else None
        rows.append({
            "session_id": sid,
            "pid": int(rec.get("pid")),
            # The id as well as the seq: a reader that needs to ask the task a QUESTION
            # (has this child handed its work back?) would otherwise have to re-resolve
            # the seq against the whole store to reach the dict this loop already loaded.
            "task_id": task_id,
            "task_seq": task_seq,
            "task_title": task_title,
            "role": _role(rec, winfo),
            "label": label,
            "status": rec.get("status"),
            "updated_ts": _parse_ts(rec.get("updatedAt")) or _parse_ts(rec.get("startedAt")),
            "cwd": cwd,
            "resume_command": resume,
        })
    rows.sort(key=lambda r: r.get("updated_ts") or 0, reverse=True)
    return rows
