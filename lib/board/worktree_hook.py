# worktree_hook.py
"""The WorktreeCreate provisioner: CREATE the worktree, then set it up.

WHAT THE EVENT IS, AND WHY IT IS OPT-IN. `WorktreeCreate` fires when Claude Code
would create a worktree (`--worktree`, an agent's `isolation: "worktree"`, a
background session) — and the hook REPLACES the creation: it must create the
worktree itself and print the ABSOLUTE PATH as the first stdout line, and ANY
non-zero exit fails the creation. That makes a bug here able to break every worktree
creation on the machine, including Claude's own subagent isolation, which is exactly
why this ships as an opt-in installer (`config --worktree-hook on`) and never in the
plugin manifest.

WHY WE WANT IT ANYWAY. A freshly-created worktree is a NEW directory, so it inherits
none of the main checkout's local setup. Two consequences bite immediately and both
are silent:

  * `.claude/settings.local.json` is gitignored, so a delegated worker in the new
    worktree hits "tool not granted" on the very grants the main checkout already
    made, and a headless worker cannot prompt — it just fails;
  * the path has no trust entry in `~/.claude.json`, so opening a session there
    stops on the trust dialog, which a background session cannot answer.

Both are one-line fixes IF something does them at creation time. That is this
module's whole job.

THE CONTRACT, AND WHERE THE LINE IS. Exactly ONE failure is allowed to fail the
harness operation: the `git worktree add` itself. Every provisioning step is
best-effort — it may not change the exit code, may not write to stdout, and may not
raise. A worktree that exists but is missing its settings copy is a worktree; a
worktree that was never created is a broken session.
"""
import json
import os
import re
import shutil
import subprocess
import uuid

import hook_health

# Git is local, but a repo with a slow filesystem (or a hook holding a lock) must not
# hang a worktree creation forever. Generous, because failing the create is worse
# than waiting.
GIT_TIMEOUT = 120
# The worker tool-grant file copied from the main checkout (see module docstring).
LOCAL_SETTINGS = os.path.join(".claude", "settings.local.json")


def _run(cmd, cwd=None):
    """Run a git command → `(rc, stdout, stderr)`. Never raises: a missing `git`, a
    timeout, or an OS error all read as a non-zero rc with the reason in stderr.
    Injected by the tests so no test shells out to a real repo."""
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           timeout=GIT_TIMEOUT)
        return p.returncode, p.stdout or "", p.stderr or ""
    except Exception as e:                              # missing git / timeout / OSError
        return 1, "", str(e)


def repo_root(cwd, run=_run):
    """The git root of `cwd`, or None when it isn't a repo. In a worktree this is
    the WORKTREE's root, not the main checkout — `main_checkout` finds that."""
    rc, out, _ = run(["git", "-C", cwd, "rev-parse", "--show-toplevel"])
    if rc != 0:
        return None
    line = (out or "").strip().splitlines()
    return line[0].strip() if line else None


def main_checkout(repo, run=_run):
    """The MAIN checkout of `repo`'s repository — the first entry of
    `git worktree list --porcelain`, exactly as lib/delegate/worktree-up.sh reads it.

    It matters which one we read: the settings file we copy lives in the main
    checkout, and a session that is ALREADY inside a worktree would otherwise copy
    from its own (probably empty) `.claude/`."""
    rc, out, _ = run(["git", "-C", repo, "worktree", "list", "--porcelain"])
    if rc != 0:
        return None
    for line in (out or "").splitlines():
        if line.startswith("worktree "):
            return line[len("worktree "):].strip()
    return None


def _ref_exists(repo, ref, run=_run):
    rc, _, _ = run(["git", "-C", repo, "show-ref", "--verify", "--quiet", ref])
    return rc == 0


def safe_name(raw):
    """A branch/worktree name reduced to one directory-safe segment: `feature/x` →
    `feature-x`. Anything outside `[A-Za-z0-9._-]` becomes `-`, so a name from the
    payload can never escape the parent dir or invent a path separator."""
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", str(raw or "").strip().strip("/"))
    name = name.strip("-.") or ""
    return name


def _unique(parent, name):
    """`<parent>/<name>`, suffixed `-2`, `-3`… until it is free. `git worktree add`
    refuses an existing directory, and failing the creation over a name collision
    would be a worse answer than picking the next name."""
    path = os.path.join(parent, name)
    n = 1
    while os.path.exists(path):
        n += 1
        path = os.path.join(parent, "%s-%d" % (name, n))
    return path


def create(payload, run=_run):
    """Create the worktree the payload asks for.

    Returns `{"path", "repo", "main", "branch", "error"}` — `error` set (and `path`
    None) ONLY when the creation genuinely failed, which is the one case the caller
    is allowed to fail the harness operation on.

    Payload reading is deliberately TOLERANT (`parent_dir`/`parentDir`, `branch`,
    `name`, `cwd`, `base_ref`): the event's field names are the harness's, not ours,
    and an unrecognised extra field must never be the thing that breaks creation.

    Branch resolution mirrors `git worktree add`'s own, minus the network — **no
    fetch, ever** (this module makes no network calls):
      * a LOCAL branch of that name    → check it out into the new worktree;
      * only `origin/<branch>` locally → create a tracking branch from it;
      * neither                        → create the branch from `base_ref`, else HEAD.
    HEAD (not `origin/<default>`) is the fallback base on purpose: branching from a
    remote ref we have not fetched would silently base the work on a stale snapshot,
    and fetching is a network call this hook must not make."""
    cwd = payload.get("cwd") or os.getcwd()
    repo = repo_root(cwd, run=run)
    if not repo:
        return {"path": None, "repo": None, "main": None, "branch": None,
                "error": "not a git repository: %s" % cwd}
    main = main_checkout(repo, run=run) or repo
    parent = (payload.get("parent_dir") or payload.get("parentDir")
              or os.path.join(main, ".claude", "worktrees"))
    parent = os.path.abspath(os.path.expanduser(str(parent)))
    branch = str(payload.get("branch") or "").strip()
    # A generated name when the payload names neither a worktree nor a branch —
    # mirroring the harness's own "random name" behaviour as closely as we can.
    name = (safe_name(payload.get("name")) or safe_name(branch)
            or "wt-%s" % uuid.uuid4().hex[:8])
    path = _unique(parent, name)
    try:
        os.makedirs(parent, exist_ok=True)
    except OSError:
        pass                                    # git creates it too; let git report
    base = str(payload.get("base_ref") or payload.get("base") or "").strip() or "HEAD"
    if branch and _ref_exists(repo, "refs/heads/%s" % branch, run=run):
        cmd = ["git", "-C", repo, "worktree", "add", path, branch]
    elif branch and _ref_exists(repo, "refs/remotes/origin/%s" % branch, run=run):
        cmd = ["git", "-C", repo, "worktree", "add", "--track", "-b", branch,
               path, "origin/%s" % branch]
    else:
        cmd = ["git", "-C", repo, "worktree", "add", "-b", branch or name, path, base]
    rc, _, err = run(cmd)
    if rc != 0:
        last = [ln for ln in (err or "").splitlines() if ln.strip()]
        return {"path": None, "repo": repo, "main": main, "branch": branch or name,
                "error": (last[-1].strip() if last else "git worktree add failed")}
    return {"path": path, "repo": repo, "main": main, "branch": branch or name,
            "error": None}


def claude_json_path(path=None):
    """Claude Code's own `~/.claude.json` (the file holding per-project trust), or
    the injected `path`. Honours `CLAUDE_CONFIG_DIR` the way every other reader in
    this plugin does, so a relocated config dir is tracked rather than guessed."""
    if path:
        return path
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    if cfg:
        return os.path.join(os.path.expanduser(cfg), ".claude.json")
    return os.path.expanduser("~/.claude.json")


def add_trust_entry(worktree, path=None):
    """Mark `worktree` as a trusted project in `~/.claude.json` — True when this
    call added it.

    Read-modify-write with an ATOMIC replace, and every unhappy path is a silent
    False: a missing file is NOT created (its absence means Claude Code has not
    written one, and inventing it would be us guessing at another app's schema), a
    non-object file is left alone, and an entry that is already trusted is not
    rewritten. We only ever ADD one key to one entry — nothing else in that file is
    ours to touch."""
    p = claude_json_path(path)
    if not os.path.exists(p):
        return False
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return False
    projects = data.get("projects")
    if not isinstance(projects, dict):
        projects = {}
        data["projects"] = projects
    entry = projects.get(worktree)
    if not isinstance(entry, dict):
        entry = {}
    if entry.get("hasTrustDialogAccepted") is True and worktree in projects:
        return False                              # already trusted → no write at all
    entry["hasTrustDialogAccepted"] = True
    projects[worktree] = entry
    tmp = "%s.ts-tmp.%d" % (p, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, p)
    return True


def provision(worktree, main, claude_json=None):
    """Everything the new worktree needs that git does not give it. Returns the
    human notes for the hook-health record.

    Each step is caught SEPARATELY: one failing step must not skip the others, and
    NO step may raise — the worktree already exists by the time this runs, and the
    caller's exit code is already decided."""
    notes = []
    # (a) the worker tool-grant file. Gitignored, so a new worktree has none, and a
    # headless worker that hits a missing grant cannot prompt for it.
    try:
        src = os.path.join(main or "", LOCAL_SETTINGS)
        dst = os.path.join(worktree, LOCAL_SETTINGS)
        if main and os.path.isfile(src) and not os.path.exists(dst):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
            notes.append("copied .claude/settings.local.json")
    except Exception as e:
        notes.append("settings.local.json copy failed (%s)" % e.__class__.__name__)
    # (b) the trust entry, so a session opened here never stops on the trust dialog.
    try:
        if add_trust_entry(worktree, path=claude_json):
            notes.append("added the trust entry")
    except Exception as e:
        notes.append("trust entry failed (%s)" % e.__class__.__name__)
    return notes


def handle(payload, out, err, run=_run, claude_json=None):
    """The whole hook, as an exit code. `out`/`err` are injected streams so the test
    reads exactly what the harness would.

    STDOUT CARRIES ONE THING: the worktree's absolute path, on the first line. It is
    written and flushed BEFORE provisioning starts, so no provisioning step can
    reorder or pollute it."""
    result = create(payload, run=run)
    if result.get("error") or not result.get("path"):
        # The ONE sanctioned failure — the creation itself. Non-zero here fails the
        # harness's worktree creation, which is correct: there is no worktree.
        err.write("[task-station] worktree-create failed: %s\n"
                  % (result.get("error") or "no path"))
        return 1
    path = result["path"]
    out.write(path + "\n")
    try:
        out.flush()
    except Exception:
        pass
    notes = []
    try:
        notes = provision(path, result.get("main"), claude_json=claude_json)
    except Exception as e:                        # belt-and-braces: provision() already
        notes = ["provisioning failed (%s)" % e.__class__.__name__]   # catches per step
    try:
        # Code 0 = INFORMATIONAL (see hook_health.record): this is a report of work
        # done, not a failure, so it must not feed the session-start failure nag.
        hook_health.record("worktree-create", 0, "%s — %s"
                           % (path, "; ".join(notes) if notes else "nothing to provision"))
    except Exception:
        pass                                      # an unloggable success is still a success
    return 0
