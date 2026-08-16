"""WORKSPACE TRUST AND THE FIRST-RUN GATES — what `invoke` settles before a child
window opens, and the guard that decides whether it may.

A worktree created moments ago is a stranger to Claude Code. Opening a session in it
stops on the trust dialog, and clearing only that one stops again on the project-scoped
MCP approval for the workspace's own `.mcp.json`. Neither is a decision anybody made;
both are first-run paperwork, and a loop cannot type. So the gates are enumerated FROM
THE WORKSPACE ITSELF rather than patched one at a time — a third gate should surface
here as a missing case, not as another stalled child.

THE GUARD MATTERS MORE THAN THE FIX. Pre-seeding trust for any `--cwd` would turn a
safety prompt into a no-op for arbitrary directories: a security regression wearing a
convenience costume. So trust is only ever INHERITED. A linked git worktree may be
pre-trusted when its OWN main checkout is already a trusted project on this machine, and
nothing else may be, ever. Every refusal prints its reason and the invoke continues —
the human answers one dialog, which is what a safety prompt is for.

This module is a STANDALONE seam, not a split-engine one: it owns its imports and reads
no patchable facade globals, so `tests/test_patch_surface.py` does not scan it. The one
thing it borrows from another seam — `_same_model_family` — is a pure predicate over two
strings, so binding it at import time can never miss a patch that mattered.
"""
import json
import os
import subprocess

import pricing
from board.sessions import _same_model_family

# The one permission mode that genuinely NARROWS what a child may do. Everything else
# in Claude Code's vocabulary — acceptEdits, default, bypassPermissions — either widens
# or merely restates the harness baseline, and a role emitting one of those REPLACES the
# human's configured default instead of restricting it. Closed list, never a guess: a
# mode nobody has classified is not a restriction.
RESTRICTING_MODES = frozenset({"plan"})

# `.mcp.json` is the workspace's own declaration of the servers a session there will be
# asked to approve; `.claude/settings.local.json` is the repo-local, git-ignored file
# that records the answer. Seeding the second from the first is exactly what the main
# checkout already does by hand.
MCP_MANIFEST = ".mcp.json"
LOCAL_SETTINGS = os.path.join(".claude", "settings.local.json")

_GIT_TIMEOUT = 15


# ------------------------------------------------------------ the role's limits ----

def restricts(mode):
    """True only when `mode` NARROWS the child's autonomy relative to the human's
    configured default.

    This is the whole of the "a role may restrict but never replace" rule. `invoke`
    emits `--permission-mode` only when this returns True, so a role that wants
    acceptEdits gets silence instead — and silence inherits, which is the point."""
    return bool(mode) and mode in RESTRICTING_MODES


def inherited_model(chosen, selection):
    """The model `invoke` should actually emit for a child the ROLE gave `chosen`, given
    the parent's own `selection` string.

    The role table names models by bare alias (`opus`) so a role follows the current
    generation instead of freezing one release. But a bare alias is also a 200k context
    window, and a parent running `opus[1m]` that spawns a child on `opus` has silently
    handed it one fifth of the context — an unasked-for downgrade, which is the same
    thing `restricts` refuses to do to the permission mode.

    So the `[1m]` marker is inherited, and ONLY the marker: the child gets the parent's
    full selection string when it names the same family and a strictly larger window.
    A different family is never inflated (a `sonnet` scout under an `opus[1m]` parent
    stays a 200k sonnet), because a window is a property of the model actually chosen and
    borrowing one across families would be inventing a variant that may not exist.

    The family test is `_same_model_family`, which the checkpoint-pressure math already
    uses for the identical question and which is conservative in the identical way: an
    id with no recognizable family matches NOTHING, including another unrecognizable
    one. No evidence of a match is a refusal to inherit, never a coin flip."""
    if not chosen or not selection:
        return chosen
    if not _same_model_family(chosen, selection):
        return chosen
    if pricing.context_window_for(selection) <= pricing.context_window_for(chosen):
        return chosen
    return selection


# ------------------------------------------------------------ Claude Code's config ----

def claude_json_path():
    """Claude Code's own `~/.claude.json` — the file whose `projects` map records which
    directories the human has trusted. Honours `CLAUDE_CONFIG_DIR` so a test (or a
    sandboxed run) can redirect it and never read or write the real one."""
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    if cfg and cfg.strip():
        return os.path.join(os.path.expanduser(cfg.strip()), ".claude.json")
    return os.path.expanduser(os.path.join("~", ".claude.json"))


def _read_json(path, default=None):
    """A best-effort read: a missing or malformed file is `default`, never an exception.
    A crash here would take down an invoke over a config file this plugin does not own."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return default
    return doc if doc is not None else default


def _write_json(path, doc):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
        fh.write("\n")


def trusted_projects():
    """The set of directories `~/.claude.json` records as TRUSTED — `hasTrustDialogAccepted`
    actually true, not merely present. A project the human has seen but never accepted
    cannot hand out a grant it does not itself hold."""
    doc = _read_json(claude_json_path(), {}) or {}
    projects = doc.get("projects")
    if not isinstance(projects, dict):
        return set()
    return {p for p, entry in projects.items()
            if isinstance(entry, dict) and entry.get("hasTrustDialogAccepted") is True}


# ------------------------------------------------------------------- git facts ----

def _git(cwd, *args):
    """A git call that never raises. None on any failure — a machine without git simply
    cannot answer the guard's question, and the guard's answer to an unanswerable
    question is no."""
    try:
        r = subprocess.run(["git", "-C", cwd] + list(args), capture_output=True,
                           text=True, timeout=_GIT_TIMEOUT)
    except Exception:
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def _git_dirs(cwd):
    """`(git_dir, git_common_dir)` as absolute real paths, or None when `cwd` is not in
    a git repository at all.

    These two differ ONLY in a linked worktree: its `git_dir` is
    `<main>/.git/worktrees/<name>` while its `git_common_dir` stays `<main>/.git`. In a
    main checkout they are the same path, which is exactly how the guard tells a
    worktree from the repository it was cut from."""
    out = _git(cwd, "rev-parse", "--git-dir", "--git-common-dir")
    if not out:
        return None
    parts = out.splitlines()
    if len(parts) != 2:
        return None
    resolved = []
    for p in parts:
        p = p.strip()
        if not p:
            return None
        if not os.path.isabs(p):
            p = os.path.join(cwd, p)
        resolved.append(os.path.realpath(p))
    return tuple(resolved)


def _main_checkout(git_common_dir):
    """The working directory of the repository that owns `git_common_dir` — its parent,
    since the common dir is `<main>/.git`. None for anything else (a bare repo, say),
    because a repository with no working tree is not a project anybody trusted."""
    if os.path.basename(git_common_dir) != ".git":
        return None
    return os.path.dirname(git_common_dir)


def declared_mcp_servers(path):
    """The server names the workspace's own `.mcp.json` declares, sorted. Read from the
    WORKSPACE, never copied from the main checkout — a copied list goes stale the moment
    the two repositories differ, and a stale list re-prompts for exactly the server it
    was supposed to clear."""
    doc = _read_json(os.path.join(path, MCP_MANIFEST), {}) or {}
    servers = doc.get("mcpServers")
    if not isinstance(servers, dict):
        return []
    return sorted(servers)


# -------------------------------------------------------------------- the guard ----

def assess(cwd):
    """Decide what `invoke` may pre-seed for `cwd`, WITHOUT writing anything.

    Returns a verdict dict — `ok` plus the facts behind it — so the caller can print the
    same finding whether or not it is about to act on it. That split is what makes
    `--dry-run` honest: a preview reports the verdict it would act on and then does not
    act on it, rather than describing a decision made somewhere else.

    `cwd=None` is not a refusal, it is silence: the child inherits the launching
    session's directory and there is no new workspace to vouch for."""
    verdict = {"cwd": None, "ok": False, "reason": "", "main": None,
               "trust_needed": False, "servers": [], "settings": None}
    if not cwd:
        return verdict
    path = os.path.realpath(os.path.expanduser(cwd))
    verdict["cwd"] = path

    dirs = _git_dirs(path)
    if not dirs:
        verdict["reason"] = ("%s is not a git repository — trust is only ever inherited, "
                             "and a plain directory has nothing to inherit it from" % path)
        return verdict
    git_dir, common_dir = dirs
    if git_dir == common_dir:
        verdict["reason"] = ("%s is a main checkout, not a linked worktree — it has no "
                             "trusted parent to inherit from, so its dialog is the "
                             "human's to answer once" % path)
        return verdict
    main = _main_checkout(common_dir)
    if not main:
        verdict["reason"] = ("%s belongs to a repository with no working tree, which is "
                             "not a project this machine could have trusted" % path)
        return verdict
    verdict["main"] = main

    cfg = claude_json_path()
    if not os.path.exists(cfg):
        verdict["reason"] = ("Claude Code has not written %s yet — this plugin will not "
                             "invent another application's config file" % cfg)
        return verdict
    trusted = trusted_projects()
    if main not in trusted and os.path.realpath(main) not in trusted:
        verdict["reason"] = ("its main checkout %s is not a trusted project on this "
                             "machine — known is not trusted, and inheriting from "
                             "nothing is not inheriting" % main)
        return verdict

    verdict["ok"] = True
    verdict["trust_needed"] = path not in trusted
    verdict["servers"] = declared_mcp_servers(path)
    verdict["settings"] = os.path.join(path, LOCAL_SETTINGS)
    return verdict


def apply(verdict):
    """Perform the writes `verdict` authorises, and return the human-readable list of
    what was actually done. A refused or empty verdict writes NOTHING and returns []."""
    if not verdict.get("ok"):
        return []
    done = []
    if verdict.get("trust_needed"):
        _grant_trust(verdict["cwd"])
        done.append("pre-trusted %s (inherited from %s)"
                    % (verdict["cwd"], verdict["main"]))
    enabled = _approve_servers(verdict.get("settings"), verdict.get("servers") or [])
    if enabled:
        done.append("pre-approved MCP servers in %s: %s"
                    % (verdict["settings"], ", ".join(enabled)))
    return done


def _grant_trust(path):
    """Set `projects[path].hasTrustDialogAccepted`, preserving every other key the file
    holds. Claude Code owns this file and stores far more in it than trust; a rewrite
    that dropped a project's history would be this plugin corrupting another
    application's state to save one keystroke."""
    cfg = claude_json_path()
    doc = _read_json(cfg, {}) or {}
    projects = doc.get("projects")
    if not isinstance(projects, dict):
        projects = {}
    entry = projects.get(path)
    if not isinstance(entry, dict):
        entry = {}
    entry["hasTrustDialogAccepted"] = True
    projects[path] = entry
    doc["projects"] = projects
    _write_json(cfg, doc)


def _approve_servers(settings_path, servers):
    """Record `servers` as enabled in the workspace's repo-local settings, and return the
    names now enabled (or [] when there was nothing to do).

    A server the human EXPLICITLY disabled is never re-enabled — a convenience that
    reverses a stated decision is not a convenience. A workspace declaring no servers
    gets no file at all: writing an empty one would leave a puzzling artifact in a
    repository this plugin was only passing through."""
    if not settings_path or not servers:
        return []
    current = _read_json(settings_path, {}) or {}
    if not isinstance(current, dict):
        current = {}
    disabled = current.get("disabledMcpjsonServers")
    disabled = set(disabled) if isinstance(disabled, list) else set()
    already = current.get("enabledMcpjsonServers")
    already = set(already) if isinstance(already, list) else set()
    enabled = sorted((already | set(servers)) - disabled)
    if not enabled or enabled == sorted(already):
        return []
    current["enabledMcpjsonServers"] = enabled
    _write_json(settings_path, current)
    return enabled


# -------------------------------------------------------------------- reporting ----

def lines(verdict, done=None, dry=False):
    """The `workspace:` lines `invoke` prints.

    A refusal is never silent — the reason is the part that changes what the human does
    next. Otherwise the two callers differ in WHO supplies the facts, which is the whole
    difference between a preview and a run: `dry=True` describes the verdict in the
    conditional, while a real run reports `done` — the list `apply` came back with, so
    the output can never claim a write that did not happen."""
    if not verdict.get("cwd"):
        return []
    if not verdict.get("ok"):
        return ["  workspace: %s pre-trust %s — %s"
                % ("would not" if dry else "did not", verdict["cwd"], verdict["reason"])]
    if not dry:
        return ["  workspace: %s" % d for d in (done or [])] or \
            ["  workspace: %s is already trusted and declares no new MCP servers — "
             "nothing to pre-seed" % verdict["cwd"]]
    out = []
    if verdict.get("trust_needed"):
        out.append("  workspace: would pre-trust %s (inherited from %s)"
                   % (verdict["cwd"], verdict["main"]))
    else:
        out.append("  workspace: %s is already trusted (inherited from %s)"
                   % (verdict["cwd"], verdict["main"]))
    if verdict.get("servers"):
        out.append("  workspace: would pre-approve MCP servers in %s: %s"
                   % (verdict["settings"], ", ".join(verdict["servers"])))
    return out
