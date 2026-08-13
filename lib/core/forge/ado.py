"""Azure DevOps forge adapter — the ADO mechanics, lifted verbatim from the
brain source tree's ``scripts/forge/ado.py`` @ 0.14.0 (3.0.0 Phase 4 chunk 1).

Push uses a bearer token minted non-interactively via ``az`` and injected through
``http.extraheader`` (the ADO non-interactive mechanic); the PR is opened with
``az repos pr create``. Org/project/repo come from config, never literals.
``_ADO_RESOURCE_ID`` is the PUBLIC Azure DevOps app id (documented as
not-a-secret), platform-generic, not tied to any organization.
"""
import subprocess

# imported lazily to avoid a circular import at package load
def _err():
    from . import ForgeError
    return ForgeError


_ADO_RESOURCE_ID = "499b84ac-1321-427f-aa17-267ca6975798"


def configured(cfg):
    """True iff org/project/repo are all set — the precondition for push + PR."""
    return bool(cfg.get("forge_org") and cfg.get("forge_project") and cfg.get("forge_repo"))


def _token():
    """Mint a bearer token for the forge (az, non-interactive). Returns the token
    or ``None`` when az is absent / not logged in — the caller degrades to a
    local-only commit rather than failing."""
    try:
        r = subprocess.run(
            ["az", "account", "get-access-token", "--resource", _ADO_RESOURCE_ID,
             "--query", "accessToken", "-o", "tsv"],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() or None if r.returncode == 0 else None


def push_argv(clone, branch, token=None):
    """The exact ``git push`` argv (with the bearer header injected when present)."""
    args = ["git", "-C", str(clone)]
    if token:
        args += ["-c", f"http.extraheader=AUTHORIZATION: bearer {token}"]
    args += ["push", "-u", "origin", branch]
    return args


def push_branch(clone, branch, cfg):
    """Push ``branch`` to the clone's ``origin``. A bearer token is injected when
    one can be minted; without it a plain push is attempted (works for a local
    remote). Raises ForgeError on push failure."""
    r = subprocess.run(push_argv(clone, branch, _token()), capture_output=True, text=True)
    if r.returncode != 0:
        raise _err()(f"ado push failed: {(r.stderr or r.stdout).strip()}")
    return True


def pr_create_argv(cfg, branch, target, title):
    """The exact ``az repos pr create`` argv. Org/project/repo from config."""
    return ["az", "repos", "pr", "create",
            "--organization", str(cfg["forge_org"]),
            "--project", str(cfg["forge_project"]),
            "--repository", str(cfg["forge_repo"]),
            "--source-branch", branch, "--target-branch", target,
            "--title", title, "--output", "tsv", "--query", "repository.webUrl"]


def open_pr(cfg, branch, title):
    """Open a PR ``branch`` -> target branch; return the PR URL (or ``None``)."""
    target = cfg.get("forge_target_branch") or "main"
    r = subprocess.run(pr_create_argv(cfg, branch, target, title),
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise _err()(f"ado PR create failed: {(r.stderr or r.stdout).strip()}")
    return r.stdout.strip() or None


def repo_create_argv(cfg, name):
    """The exact ``az repos create`` argv (parity with the github adapter)."""
    return ["az", "repos", "create", "--name", str(name),
            "--organization", str(cfg["forge_org"]),
            "--project", str(cfg["forge_project"]),
            "--output", "tsv", "--query", "webUrl"]


def create_repo(cfg, name):
    """Create a repo on the forge; return its URL (or ``None``)."""
    r = subprocess.run(repo_create_argv(cfg, name), capture_output=True, text=True)
    if r.returncode != 0:
        raise _err()(f"ado repo create failed: {(r.stderr or r.stdout).strip()}")
    return r.stdout.strip() or None
