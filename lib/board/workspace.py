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

It is also where the SPAWN RESOLVER lives — the one function that answers model,
context window and permission mode for every path that starts a session, plus the env
hygiene that keeps a child from inheriting the parent's identity. Those belong beside
the trust gates because they answer the same question from the other side: the gates
settle what a new workspace may do, and the resolver settles what the session opening
into it is actually given.

This module is a STANDALONE seam, not a split-engine one: it owns its imports and reads
no patchable facade globals, so `tests/test_patch_surface.py` does not scan it. What it
borrows from other seams it binds at import time — `_same_model_family` is a pure
predicate over two strings, and `role_spec` is a plain lookup function — so neither
binding can miss a patch that mattered. `role_spec` stopped being a lookup in a *literal*
table in 3.12.0 (the table is config now, re-read on every call), which does not change
that: the import binds the FUNCTION, and the function reads the store when it is asked,
so a station's override lands in the next spawn rather than the next process.
"""
import json
import os
import subprocess

import pricing
from board.loop import role_spec
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


# ------------------------------------------------------------ the spawn resolver ----
#
# ONE function answers model, context window and permission mode for EVERY path that
# starts a session. There used to be two answers: `invoke` grew the rule above in 3.7.0,
# and `delegate` kept a hardcoded `acceptEdits` and a bare `sonnet` from before that rule
# existed. Two copies of one rule is not a tidiness complaint — the copies had already
# drifted, and the mode delegate was handing its workers is the one its OWN `--bg` design
# had ruled out as unsafe unattended. So the rule lives here once and both paths ask it.

SPAWN_WINDOW = "window"   # an interactive session in a new terminal window
SPAWN_BG = "bg"           # an unattended background worker with nobody at the keyboard

# THE UNATTENDED MODES, and why the role table does not get a vote on them. `dontAsk`
# fails CLOSED: a tool outside the granted allowlist is DENIED rather than queued behind
# a prompt, so a worker with nobody watching finishes or stops instead of parking
# forever. `acceptEdits` — which the implementer role names — auto-approves edits and
# then hangs on the first non-edit prompt; `plan` — which scout and reviewer name — ends
# at ExitPlanMode, which is also a prompt. Both are correct for a human-facing window and
# wrong here, so a bg spawn takes neither.
BG_DEFAULT_MODE = "dontAsk"
# The one widening, and it is doubly gated: the human has to have turned it on once
# (config.delegate_bypass_permissions, which carries the disclaimer) AND the target has
# to be inside a `-worktrees/` sandbox. Either gate alone is not enough.
BG_BYPASS_MODE = "bypassPermissions"


def under_worktrees(path):
    """True when `path` is inside a `<repo>-worktrees/` sandbox tree (any path segment
    ending in '-worktrees'). The worktree half of the bypass gate: a mode that can write
    anything is tolerable only where the whole tree is disposable.

    AN EMPTY PATH IS FALSE, not "the current directory". `os.path.abspath("")` resolves
    to the process cwd, so the permissive reading would satisfy this gate from the hub's
    own location whenever the hub is itself running inside a worktree — granting
    `bypassPermissions` to a spawn whose directory nobody named. A gate with no input
    fails closed."""
    if not path:
        return False
    parts = os.path.abspath(path).split(os.sep)
    return any(p.endswith("-worktrees") for p in parts)


def resolve_spawn(kind, role=None, model=None, permission_mode=None,
                  parent_selection=None, cwd=None, bypass_allowed=False, effort=None):
    """What a `kind` spawn should actually run: `{kind, role, model, window,
    permission_mode, effort, deny_tools, report, notes}`.

    `model` and `permission_mode` are the EXPLICIT overrides, and an explicit value
    always wins — a human passing the flag has made the decision the role was only
    guessing at. Everything else is derived:

    * MODEL — the role's alias, then `inherited_model` to reclaim the parent's `[1m]`
      window when the two name the same family. `None` means emit no `--model` at all,
      which inherits the account default; the resolver never invents one.
    * WINDOW — the context window of the model actually chosen, reported so a caller can
      state it rather than imply it. `None` when the model is inherited, because a
      window nobody resolved is not a number this can honestly report.
    * PERMISSION MODE — for a window spawn, the role's mode only when it RESTRICTS, else
      None so the human's configured default inherits. For a bg spawn the role table
      does not apply at all (see BG_DEFAULT_MODE): the answer is the bg policy, and a
      role mode that was discarded is named in `notes` rather than dropped silently.

    * EFFORT — the role's own reasoning level (a scout is cheap on purpose), overridable
      like the other two. `None` emits no `--effort` and inherits the account default.
    * DENY_TOOLS — the role's TOOL GRANT, always as a deny list. An allow list would
      REPLACE the human's tool set and drop the MCP servers they configured; a deny list
      narrows it, which is the `restricts` rule applied to the other flag a role sets.
    * REPORT — the role's report contract, for the caller to put in the child's PROMPT.
      It rides in the answer because the role table should be read in exactly one place;
      it is not a flag, so the caller applies it rather than this function.

    THE LAST THREE ARE ROLE-DERIVED, SO THEY LIVE HERE, but they are not all consumed by
    both spawners — see the `deny_tools`/`report` note in `delegate._build_worker_cmd`
    for what the bg path deliberately does not emit yet, and why that is a stated limit
    rather than a silent drop.

    `notes` exists so the override can be REPORTED. A design that quietly throws away a
    role's stated mode is indistinguishable from a bug the first time somebody wonders
    why their scout was not in plan mode."""
    spec = role_spec(role) if role else None
    notes = []

    chosen_model = inherited_model(model or (spec or {}).get("model"),
                                   parent_selection) or None

    role_mode = (spec or {}).get("permission_mode")
    if permission_mode:
        chosen_mode = permission_mode
    elif kind == SPAWN_BG:
        chosen_mode = (BG_BYPASS_MODE if (bypass_allowed and under_worktrees(cwd))
                       else BG_DEFAULT_MODE)
        if role_mode and role_mode != chosen_mode:
            notes.append("role %s asks for %s, which stops an unattended worker at a "
                         "prompt nobody is there to answer — a %s spawn runs %s instead"
                         % (role, role_mode, kind, chosen_mode))
    else:
        chosen_mode = role_mode if restricts(role_mode) else None
        if role_mode and not chosen_mode:
            notes.append("role %s asks for %s, which REPLACES the human's default rather "
                         "than narrowing it — the flag is omitted so the default inherits"
                         % (role, role_mode))

    deny = [str(t).strip() for t in ((spec or {}).get("deny_tools") or [])
            if str(t or "").strip()]
    return {"kind": kind, "role": role, "model": chosen_model,
            "window": pricing.context_window_for(chosen_model) if chosen_model else None,
            "permission_mode": chosen_mode,
            "effort": effort or (spec or {}).get("effort") or None,
            "deny_tools": deny,
            "report": str((spec or {}).get("report") or "").strip() or None,
            "notes": notes}


# ------------------------------------------------------------------ env hygiene ----
#
# MEASURED 2026-08-18 (task 549). A window opened by the Apple Event inherits the parent
# session's whole CLAUDE_* set. `CLAUDE_CODE_CHILD_SESSION` turns transcript saving OFF,
# and the parent's session id and messaging socket come along with it, so the child
# answers to the PARENT's identity: it never appears in `sessions --task`, never appears
# in ListAgents, and the memo ledger is the only channel left to it. The trigger is
# conditional — it fires only when Terminal.app is COLD and the Apple Event is what
# launches it, which inherits the launching process's environment — which is why it
# stayed latent for anyone whose daily driver is iTerm.
#
# THE DETAIL THAT DECIDES THE FIX: `env=` on `subprocess.Popen` sets the environment of
# the `osascript` PROCESS, and Terminal.app is not that process — it receives an Apple
# Event. So for that transport the unset must live INSIDE the do-script string, which is
# what `scrubbed_command` is for. `delegate`'s worker is a direct child, where `env=`
# does reach the process, so that path scrubs the mapping instead. One list, one rule,
# two transports.
#
# FORCE_SESSION_PERSISTENCE=1 treats the symptom: the transcript comes back and the
# stale ids stay, so the child still answers to the wrong session.

# CLOSED LIST — the session's IDENTITY and TRANSPORT, which is the damage that was
# actually measured. Everything else observed in a live session was left, deliberately,
# and is written down here so the next reader can tell "classified and excluded" from
# "never looked":
#
#   CLAUDE_CONFIG_DIR            the human's own config choice. Unsetting it would
#                                silently repoint the child at a different store — a
#                                worse bug than the one being fixed.
#   CLAUDE_TTY, CLAUDE_WIN_THEME exported by the user's shell rc, so the new window's
#                                own shell sets them correctly for itself.
#   CLAUDE_PID, CLAUDE_EFFORT    harness-set (neither the shell rc nor this repo writes
#                                them) but NOT session identity or transport, so neither
#                                is part of the measured failure. Unsetting CLAUDE_EFFORT
#                                in particular would silently re-rate the child's
#                                reasoning — the same unasked-for downgrade the model
#                                rule above exists to refuse. Left until something
#                                measures a harm, on this file's own standing rule that
#                                an unclassified name is not a finding.
LEAKED_SESSION_ENV = (
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_EXECPATH",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
)


def scrub_prefix():
    """The shell prefix that clears the inherited set — `unset A B C; `.

    Plain `unset`, which succeeds on a name that was never set, so this is a no-op on a
    clean environment rather than an error on one."""
    return "unset %s; " % " ".join(LEAKED_SESSION_ENV)


def scrubbed_command(cmd):
    """`cmd` with the parent's session env unset FIRST, for a command that will be run
    by something this process does not spawn directly — the Apple Event path, where an
    `env=` mapping would land on the wrong process entirely.

    An empty command is returned untouched: prefixing nothing would hand the window
    opener a line that only unsets things."""
    if not cmd:
        return cmd
    return scrub_prefix() + cmd


def scrubbed_env(env=None):
    """A COPY of `env` (default `os.environ`) with the inherited set removed, for a
    direct child where `env=` genuinely reaches the process. Never mutates its
    argument — the caller's own environment is not this function's to edit."""
    src = os.environ if env is None else env
    return {k: v for k, v in src.items() if k not in LEAKED_SESSION_ENV}


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
