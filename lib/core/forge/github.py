"""GitHub forge adapter — mirrors the ADO surface via the ``gh`` CLI. Lifted
verbatim from the brain source tree's ``scripts/forge/github.py`` @ 0.14.0
(3.0.0 Phase 4 chunk 1).

Mapping onto the generic config keys: ``forge_org`` is the repo **owner**,
``forge_repo`` is the repo **name** (``forge_project`` is unused on GitHub). Repo
create and PR create go through ``gh``; the branch push is plain ``git`` (``gh``'s
credential helper supplies auth), matching how a GitHub checkout normally pushes.
``gh pr create`` prints the new PR URL to stdout — that is the returned url.
"""
import subprocess

def _err():
    from . import ForgeError
    return ForgeError


def _slug(cfg):
    """owner/repo for gh --repo."""
    return f"{cfg['forge_org']}/{cfg['forge_repo']}"


def configured(cfg):
    """True iff owner (forge_org) and repo (forge_repo) are both set."""
    return bool(cfg.get("forge_org") and cfg.get("forge_repo"))


def push_argv(clone, branch):
    """The exact ``git push`` argv (gh's credential helper handles auth)."""
    return ["git", "-C", str(clone), "push", "-u", "origin", branch]


def push_branch(clone, branch, cfg):
    """Push ``branch`` to ``origin``. Raises ForgeError on failure."""
    r = subprocess.run(push_argv(clone, branch), capture_output=True, text=True)
    if r.returncode != 0:
        raise _err()(f"github push failed: {(r.stderr or r.stdout).strip()}")
    return True


def pr_create_argv(cfg, branch, target, title):
    """The exact ``gh pr create`` argv."""
    return ["gh", "pr", "create", "--repo", _slug(cfg),
            "--head", branch, "--base", target,
            "--title", title, "--body", title]


def open_pr(cfg, branch, title):
    """Open a PR ``branch`` -> target branch; return the PR URL gh prints."""
    target = cfg.get("forge_target_branch") or "main"
    r = subprocess.run(pr_create_argv(cfg, branch, target, title),
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise _err()(f"github PR create failed: {(r.stderr or r.stdout).strip()}")
    return r.stdout.strip() or None


def repo_create_argv(cfg, name, *, private=True):
    """The exact ``gh repo create`` argv (private by default)."""
    args = ["gh", "repo", "create", f"{cfg['forge_org']}/{name}",
            "--private" if private else "--public"]
    return args


def create_repo(cfg, name, *, private=True):
    """Create a repo on GitHub via gh; return its URL (or ``None``)."""
    r = subprocess.run(repo_create_argv(cfg, name, private=private),
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise _err()(f"github repo create failed: {(r.stderr or r.stdout).strip()}")
    return r.stdout.strip() or None
