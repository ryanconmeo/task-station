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

Worktree policy: write work NEVER runs on a repo's main checkout. Pass
--worktree <name> and the worker runs in <repo>-worktrees/<name>, resolving it
or creating it on the fly (off origin/dev by default) via worktree-up.sh. The
naming convention is the ADO story id + slug (e.g. Volt-2704-balance-sheet) or
fix-<PR#> for PR-fix branches; --branch overrides the branch (default = the
worktree name) and --base overrides the new-branch base (default origin/dev,
never main unless asked). Omit --worktree only for read-only delegations.

Usage:
  delegate.py run  --project <name> --task "<instructions>" [--worktree NAME] [--branch BR] [--base REF] [--seq N] [--solo] [--label L] [--fresh] [--timeout S]
    For write work (--worktree) with no --seq, the calling session's attached /todo
    seq is inherited automatically (use --solo to opt out for ad-hoc work).
  delegate.py list
  delegate.py dir  --project <name> [--worktree NAME]   # resolve & print the repo (or worktree) path

Lives inside the claude-todo repo (~/.claude/todo/delegate); the registry sits
beside this script and it links back to the tracker via the sibling todo.py.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import uuid

HOME = os.path.expanduser("~")
PARENTS = [os.path.join(HOME, "Workspace"), os.path.join(HOME, "Workspace-Other")]

# delegate.py lives one dir deeper than paths.py, so add the plugin root to sys.path before importing it
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import paths

REG_DIR = paths.data_dir()                                     # data dir (e.g. ~/.claude/todo-data) — survives /plugin update
REG = os.path.join(REG_DIR, "workers.json")
TODO_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "todo.py")     # plugin root → sibling todo.py (delegate.py is one dir deeper)


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
            # A '<repo>-worktrees' dir holds worktrees, it is NOT a project —
            # never let --project resolve to one (that would run on a worktree
            # tree as if it were the repo).
            if name.endswith("-worktrees"):
                continue
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


def worktrees_parent(repo_root):
    """The sibling '<repo>-worktrees' dir, e.g. ~/Workspace/Volt -> Volt-worktrees."""
    return repo_root.rstrip("/") + "-worktrees"


def worktree_path(repo_root, name):
    """The would-be path for worktree <name> (no side effects)."""
    return os.path.join(worktrees_parent(repo_root), name)


def resolve_worktree(repo_root, name, branch=None, base="origin/dev"):
    """Find or create <repo>-worktrees/<name>; return its path.

    A missing worktree is built with worktree-up.sh (which also bootstraps
    .env.local + deps so it can build/run). Falls back to a bare
    `git worktree add` for non-pnpm repos where that script aborts.
    """
    wt = worktree_path(repo_root, name)
    if os.path.isdir(wt):
        return wt
    branch = branch or name
    os.makedirs(worktrees_parent(repo_root), exist_ok=True)
    script = os.path.join(HOME, ".claude", "scripts", "worktree-up.sh")
    if os.path.exists(script):
        proc = subprocess.run(["bash", script, wt, branch, base],
                              cwd=repo_root, capture_output=True, text=True)
        if os.path.isdir(wt):
            return wt
        sys.stderr.write("[delegate] worktree-up.sh did not produce %s; "
                         "falling back to bare git worktree add.\n%s\n"
                         % (wt, (proc.stderr or "").strip()))
    # Bare fallback: reuse an existing local/remote branch, else cut a new one.
    subprocess.run(["git", "fetch", "origin", "--quiet"],
                   cwd=repo_root, capture_output=True)
    add = subprocess.run(["git", "worktree", "add", wt, branch],
                         cwd=repo_root, capture_output=True, text=True)
    if not os.path.isdir(wt):
        add = subprocess.run(["git", "worktree", "add", wt, "-b", branch, base],
                             cwd=repo_root, capture_output=True, text=True)
    if not os.path.isdir(wt):
        raise SystemExit("delegate: could not create worktree %s:\n%s"
                         % (wt, (add.stderr or "").strip()))
    return wt


def run_worker(dirpath, task, session_id=None, resume=False, timeout=None, name=None):
    """Launch a headless worker in `dirpath`.

    resume=True  -> `--resume <id>`: continue an existing session (id unchanged).
    resume=False -> `--session-id <id>`: create a session with a KNOWN id. The
                    caller pre-registers that id BEFORE launching, so even if this
                    process or the worker is killed mid-run the session is on disk
                    under a resumable id (no lost conversation link).
    """
    cmd = ["claude", "-p", task,
           "--output-format", "json",
           "--permission-mode", "acceptEdits"]
    if session_id and resume:
        cmd += ["--resume", session_id]
    elif session_id:
        cmd += ["--session-id", session_id]
        if name:
            cmd += ["-n", name]
    elif name:
        cmd += ["-n", name]
    # Workers are headless children: silence the /todo hooks so each worker turn
    # doesn't get nudged to track its own task (that's the hub's job).
    env = dict(os.environ, CLAUDE_TODO_SUPPRESS="1")
    return subprocess.run(cmd, cwd=dirpath, capture_output=True,
                          text=True, timeout=timeout, env=env)


def _attached_seq():
    """The /todo task seq the CALLING (hub) session is attached to, or None.
    Read from CLAUDE_CODE_SESSION_ID (set in the worker's parent env) via
    `todo.py whoami --porcelain`. Lets write work inherit the right seq so the
    worktree binding is deterministic without the hub remembering to pass it."""
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not sid:
        return None
    try:
        out = subprocess.run(["python3", TODO_PY, "whoami", "--porcelain",
                              "--session", sid], capture_output=True, text=True, timeout=20)
        return (out.stdout or "").strip() or None
    except Exception:
        return None


def _save_entry(reg, key, project, seq, label, dirpath, sid):
    reg[key] = {"project": project, "seq": seq, "label": label,
                "dir": dirpath, "session_id": sid, "ts": _now()}
    save_reg(reg)


def _parse_result(out):
    """Pull (result_text, session_id, cost) from a `claude -p --output-format json`
    blob, tolerating leading control chars / trailing lines some shells inject."""
    result_text, sid, cost = out, None, None
    brace = out.find("{")
    if brace != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(out[brace:])
            if isinstance(obj, dict):
                result_text = obj.get("result", out)
                sid = obj.get("session_id")
                cost = obj.get("total_cost_usd")
        except Exception:
            pass
    return result_text, sid, cost


def cmd_run(a):
    repo_root = resolve_dir(a.project)
    project = os.path.basename(repo_root)          # key/name stay the repo's
    # Auto-inherit the seq from the calling session's attached task for WRITE work
    # (--worktree) when none was given. This makes the worktree binding deterministic
    # without the hub having to remember --seq. --solo opts out for genuine ad-hoc
    # work unrelated to the current task.
    if a.worktree and not a.seq and not a.solo:
        inherited = _attached_seq()
        if inherited:
            a.seq = inherited
            sys.stderr.write("[delegate] inheriting --seq %s from the attached session "
                             "(pass --solo for ad-hoc work unrelated to that task).\n"
                             % inherited)
    seq, label = a.seq, a.label
    # Identity of the persistent worker. For a TRACKED task the seq IS the
    # identity (one task = one worktree per policy; --label splits a second
    # concurrent worktree in the same repo), so the worktree is NOT in the key —
    # that lets a resume find the session even if --worktree is omitted, while
    # the saved dir below still pins it to the right tree. For UN-tracked ad-hoc
    # work there's no seq to disambiguate, so the worktree joins the key to keep
    # two trees in one repo from sharing a slot.
    if seq:
        key = "%s:%s" % (seq, project)
        name = "todo-%s-%s" % (seq, project)
    else:
        key = "%s@%s" % (project, a.worktree) if a.worktree else project
        name = ("wk-%s-%s" % (project, a.worktree)) if a.worktree else None
    if label:
        key += ":%s" % label
        name = (name or project) + "-%s" % label

    reg = load_reg()
    entry = reg.get(key, {})
    sid = None if a.fresh else entry.get("session_id")
    saved_dir = entry.get("dir")

    # Decide the worker's cwd. On RESUME the worktree it was created in is the
    # source of truth — we never silently relocate a resumed session, and never
    # auto-recreate a removed worktree under the guise of a resume.
    if sid and saved_dir:
        if a.worktree and os.path.basename(saved_dir.rstrip("/")) != a.worktree:
            raise SystemExit(
                "delegate: worker %r is pinned to %s but --worktree=%s requests a "
                "different tree.\n  Refusing to resume it elsewhere. Use --fresh for a "
                "new worker, or drop --worktree to resume in place."
                % (key, saved_dir, a.worktree))
        if not os.path.isdir(saved_dir):
            raise SystemExit(
                "delegate: worker %r was created in %s, which no longer exists "
                "(worktree removed?).\n  Not recreating it silently — use --fresh to "
                "start a new worker." % (key, saved_dir))
        dirpath = saved_dir
    else:
        # New worker: resolve-or-create its worktree now (off origin/dev).
        dirpath = (resolve_worktree(repo_root, a.worktree, branch=a.branch,
                                    base=a.base) if a.worktree else repo_root)

    # Launch. A resume reattaches to the existing id; a brand-new worker gets a
    # UUID we choose AND PRE-REGISTER before launching, so a mid-run kill (timeout
    # or SIGKILL) still leaves the session on disk under a known, resumable id —
    # the next same-key delegate call reattaches to it instead of losing the chat.
    try:
        if sid:
            proc = run_worker(dirpath, a.task, session_id=sid, resume=True,
                              timeout=a.timeout, name=name)
            if proc.returncode != 0:
                sys.stderr.write("[delegate] resume of %s failed; starting a fresh "
                                 "pre-registered worker.\n" % sid)
                sid = None
        if not sid:
            sid = str(uuid.uuid4())
            _save_entry(reg, key, project, seq, label, dirpath, sid)   # pre-register
            proc = run_worker(dirpath, a.task, session_id=sid, resume=False,
                              timeout=a.timeout, name=name)
    except subprocess.TimeoutExpired:
        # Child killed at the deadline, but its session persisted under `sid`,
        # which is already in the registry. Resume with the same --seq/--project.
        raise SystemExit(
            "delegate: worker timed out after %ss — session %s saved in %s.\n"
            "  Re-run the same delegate command (same --seq/--project) to resume it."
            % (a.timeout, sid, dirpath))

    out = (proc.stdout or "").strip()
    result_text, echoed_sid, cost = _parse_result(out)
    if proc.returncode != 0 and not out:
        raise SystemExit(
            "delegate: worker failed (exit %d) — session %s saved in %s, resume "
            "with the same --seq/--project.\n%s"
            % (proc.returncode, sid, dirpath, (proc.stderr or "").strip()))

    # `claude` echoes the session id; honor it if it ever differs from ours
    # (e.g. a forked session) so the registry tracks the real on-disk session.
    if echoed_sid and echoed_sid != sid:
        sys.stderr.write("[delegate] worker reported session %s (expected %s); "
                         "tracking the reported one.\n" % (echoed_sid, sid))
        sid = echoed_sid
    final_sid = sid
    _save_entry(reg, key, project, seq, label, dirpath, sid)   # refresh ts + sid

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
    root = resolve_dir(a.project)
    if getattr(a, "worktree", None):
        print(worktree_path(root, a.worktree))   # path only; no create
    else:
        print(root)


def main():
    ap = argparse.ArgumentParser(prog="delegate")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="spawn/resume a worker and relay its result")
    r.add_argument("--project", required=True)
    r.add_argument("--task", required=True, help="self-contained instructions for the worker")
    r.add_argument("--worktree", default=None,
                   help="worktree dir name under <repo>-worktrees/; resolve-or-create and run there. "
                        "Required for write work (use the story id+slug, e.g. Volt-2704-foo, or fix-<PR#>).")
    r.add_argument("--branch", default=None,
                   help="branch for the worktree (default: same as --worktree name)")
    r.add_argument("--base", default="origin/dev",
                   help="base ref for a NEW branch (default: origin/dev — never main unless asked)")
    r.add_argument("--seq", default=None,
                   help="/todo task number to link this worker to (persistent per-(task,repo) worker + naming)")
    r.add_argument("--label", default=None,
                   help="discriminator for a SECOND concurrent worker in the same (task,repo)")
    r.add_argument("--solo", action="store_true",
                   help="ad-hoc: do NOT auto-inherit --seq from the calling session's attached task")
    r.add_argument("--fresh", action="store_true", help="ignore any saved worker session; start new")
    r.add_argument("--timeout", type=int, default=None, help="seconds before giving up on the worker")
    r.set_defaults(func=cmd_run)

    l = sub.add_parser("list", help="list known workers")
    l.set_defaults(func=cmd_list)

    d = sub.add_parser("dir", help="resolve a project name to its repo dir")
    d.add_argument("--project", required=True)
    d.add_argument("--worktree", default=None,
                   help="print the <repo>-worktrees/<name> path instead (does not create it)")
    d.set_defaults(func=cmd_dir)

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
