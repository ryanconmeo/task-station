#!/usr/bin/env python3
"""delegate — spawn / resume an in-project Claude worker.

The "hub" session is usually launched from ~ and therefore lacks every
directory-scoped thing: a project's ./CLAUDE.md, its .claude/settings.json
permissions + env, its hooks, its project-scoped .mcp.json servers, and its
project-local skills. Those load ONLY in a `claude` process whose cwd is inside
the repo. This helper spawns such a process (`claude -p`) so the work runs with
the project's full machinery, keeps ONE persistent worker per project (resuming
it across turns), and relays the worker's result back to the hub.

The decision of *when* to delegate lives in ~/.claude/CLAUDE.md (always in
context); this script is the *how*. Workers run with --permission-mode
acceptEdits and inherit each repo's allowlist (a tool the repo hasn't allowlisted
fails rather than prompting, since a headless child can't ask a human).

Usage:
  delegate.py run  --project <name> --task "<instructions>" [--seq N] [--label L] [--fresh] [--timeout S]
  delegate.py list
  delegate.py dir  --project <name>      # just resolve & print the repo path

Lives inside the claude-todo repo (~/.claude/todo/delegate); the registry sits
beside this script and it links back to the tracker via the sibling todo.py.
"""
import argparse
import json
import os
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
PARENTS = [os.path.join(HOME, "Workspace"), os.path.join(HOME, "Workspace-Other")]
REG_DIR = os.path.dirname(os.path.abspath(__file__))           # ~/.claude/todo/delegate
REG = os.path.join(REG_DIR, "workers.json")
TODO_PY = os.path.join(os.path.dirname(REG_DIR), "todo.py")     # sibling tracker


def _now():
    return int(time.time())


def load_reg():
    try:
        with open(REG) as f:
            return json.load(f)
    except Exception:
        return {}


def save_reg(d):
    os.makedirs(REG_DIR, exist_ok=True)
    tmp = REG + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=2)
    os.replace(tmp, REG)


def _candidates():
    out = []
    for parent in PARENTS:
        if not os.path.isdir(parent):
            continue
        for name in sorted(os.listdir(parent)):
            full = os.path.join(parent, name)
            if os.path.isdir(full):
                out.append((name, full))
    return out


def resolve_dir(project):
    """Map a project name (or path) to a repo dir under ~/Workspace[-Other]."""
    p = project.strip()
    # Allow passing an explicit path.
    if os.path.isdir(os.path.expanduser(p)):
        return os.path.abspath(os.path.expanduser(p))
    cand = _candidates()
    exact = [f for (n, f) in cand if n.lower() == p.lower()]
    if exact:
        return exact[0]
    subs = [(n, f) for (n, f) in cand if p.lower() in n.lower()]
    if len(subs) == 1:
        return subs[0][1]
    if not subs:
        raise SystemExit(
            "delegate: no project under %s matching %r.\n  available: %s"
            % (PARENTS, project, ", ".join(n for n, _ in cand))
        )
    raise SystemExit(
        "delegate: %r is ambiguous — matches: %s. Be more specific."
        % (project, ", ".join(n for n, _ in subs))
    )


def run_worker(dirpath, task, session_id=None, timeout=None, name=None):
    cmd = ["claude", "-p", task,
           "--output-format", "json",
           "--permission-mode", "acceptEdits"]
    if session_id:
        cmd += ["--resume", session_id]
    elif name:
        cmd += ["-n", name]
    # Workers are headless children: silence the /todo hooks so each worker turn
    # doesn't get nudged to track its own task (that's the hub's job).
    env = dict(os.environ, CLAUDE_TODO_SUPPRESS="1")
    return subprocess.run(cmd, cwd=dirpath, capture_output=True,
                          text=True, timeout=timeout, env=env)


def cmd_run(a):
    dirpath = resolve_dir(a.project)
    project = os.path.basename(dirpath)
    seq, label = a.seq, a.label
    if seq:
        key = "%s:%s" % (seq, project)
        name = "todo-%s-%s" % (seq, project)
        if label:
            key += ":%s" % label
            name += "-%s" % label
    else:
        key, name = project, None
    reg = load_reg()
    entry = reg.get(key, {})
    sid = None if a.fresh else entry.get("session_id")

    proc = run_worker(dirpath, a.task, session_id=sid, timeout=a.timeout, name=name)
    # If resuming a stale/expired session failed, transparently start fresh.
    if sid and proc.returncode != 0:
        sys.stderr.write("[delegate] resume of %s failed; starting a fresh worker.\n" % sid)
        sid = None
        proc = run_worker(dirpath, a.task, session_id=None, timeout=a.timeout, name=name)

    out = (proc.stdout or "").strip()
    if proc.returncode != 0 and not out:
        raise SystemExit("delegate: worker failed (exit %d).\n%s"
                         % (proc.returncode, (proc.stderr or "").strip()))

    result_text, new_sid, cost = out, None, None
    try:
        obj = json.loads(out)
        if isinstance(obj, dict):
            result_text = obj.get("result", out)
            new_sid = obj.get("session_id")
            cost = obj.get("total_cost_usd")
    except Exception:
        pass

    final_sid = new_sid or sid
    if final_sid:
        reg[key] = {"project": project, "seq": seq, "label": label, "dir": dirpath,
                    "session_id": final_sid, "ts": _now()}
        save_reg(reg)

    # Link the repo to the /todo task so its detail view lists this worker.
    if seq:
        try:
            subprocess.run(["python3", TODO_PY, "add-project", "--task", str(seq),
                            "--project", project],
                           capture_output=True, text=True, timeout=20)
        except Exception:
            pass

    print(result_text)
    foot = "— worker '%s'  dir: %s" % (key, dirpath)
    if final_sid:
        foot += "  session: %s  (resume: cd %s && claude --resume %s)" % (final_sid, dirpath, final_sid)
    if cost is not None:
        foot += "  cost: $%.4f" % cost
    print("\n" + foot, file=sys.stderr)


def cmd_list(a):
    reg = load_reg()
    if not reg:
        print("delegate: no workers on record.")
        return
    for key, e in sorted(reg.items(), key=lambda kv: -kv[1].get("ts", 0)):
        age = _now() - e.get("ts", 0)
        print("%-28s %s\n    session %s  (%ds ago)\n    resume: cd %s && claude --resume %s"
              % (key, e.get("dir", "?"), e.get("session_id", "?"), age,
                 e.get("dir", "?"), e.get("session_id", "?")))


def cmd_dir(a):
    print(resolve_dir(a.project))


def main():
    ap = argparse.ArgumentParser(prog="delegate")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="spawn/resume a worker and relay its result")
    r.add_argument("--project", required=True)
    r.add_argument("--task", required=True, help="self-contained instructions for the worker")
    r.add_argument("--seq", default=None,
                   help="/todo task number to link this worker to (persistent per-(task,repo) worker + naming)")
    r.add_argument("--label", default=None,
                   help="discriminator for a SECOND concurrent worker in the same (task,repo)")
    r.add_argument("--fresh", action="store_true", help="ignore any saved worker session; start new")
    r.add_argument("--timeout", type=int, default=None, help="seconds before giving up on the worker")
    r.set_defaults(func=cmd_run)

    l = sub.add_parser("list", help="list known workers")
    l.set_defaults(func=cmd_list)

    d = sub.add_parser("dir", help="resolve a project name to its repo dir")
    d.add_argument("--project", required=True)
    d.set_defaults(func=cmd_dir)

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
