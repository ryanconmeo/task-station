# repo_index.py
"""Hub-side, on-demand repo index for routing fuzzy tasks to the right repo(s).

A `claude` session launched from the hub (outside any repo) can't auto-load
anything inside the workspace dirs. This module gives it a deterministic,
regenerable index of the repos under the configured roots so it can rank
candidates at delegation time. The index lives next to the task store
(`<data_dir>/repos.md` + `repos.json`) — NOT in tasks.db (repos aren't tasks),
and NOT as per-repo committed files.

Split of responsibility:
  - Discovery is DETERMINISTIC (no LLM): name/path/remote/ado_project/stack/status
    are all derived from the filesystem + a couple of cheap `git` calls.
  - Prose (summary/keywords/domain) is hand-authored in `<data_dir>/repos.overrides.json`,
    keyed by repo name. Overrides WIN and SURVIVE regeneration — discovery never
    writes them.

== Scaling (schema baked in now, machinery deferred on purpose) ==
The fields here are chosen so the following are ADDITIVE later without a schema
change — they are intentionally NOT built yet, but nothing precludes them:
  1. Two-stage retrieval at 100+ repos: `match()` already returns a ranked list,
     so it doubles as a stage-1 pre-filter — take the top-K and only read those K
     cards' prose (`summary`/`keywords`/`domain`) into context, keeping tokens
     bounded. Those fields exist for exactly this.
  2. Incremental refresh at scale: a per-repo fingerprint (remote + sorted
     top-level entries + sha1 of README/manifests) cached in
     `<data_dir>/.repos-cache.json`, re-deriving only changed repos, plus a
     ~10-minute debounce on `--refresh`. `_fingerprint()` below is the seam; see
     its TODO. A full rescan is fine at the current scale, so the cache is not
     wired in yet.
"""
import hashlib
import os
import re
import subprocess
import time

import paths

# A repo whose last commit is older than this is flagged `stale`. Overridable via
# env for testing / tuning; the default matches the brief.
REPO_STALE_MONTHS = int(os.environ.get("REPO_STALE_MONTHS", "6"))
_MONTH_SECONDS = 30 * 86400


def _git(repo, *args):
    """Run `git -C <repo> <args>` and return stripped stdout, or None on any
    failure (no remote, not a git repo, git missing, timeout)."""
    try:
        out = subprocess.run(
            ["git", "-C", repo, *args],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    s = out.stdout.strip()
    return s or None


def parse_ado_project(remote):
    """Map a remote URL to a routing key, or None.

    Azure DevOps `dev.azure.com/<org>/<project>/_git/<repo>` -> `<project>`.
    GitHub `github.com[:/]<owner>/<repo>` -> `<owner>/<repo>`.
    Anything else (local path, unknown host, empty) -> None.
    """
    if not remote:
        return None
    m = re.search(r"dev\.azure\.com/[^/]+/([^/]+)/_git/", remote)
    if m:
        return m.group(1)
    # Legacy ADO host: <org>.visualstudio.com/<project>/_git/<repo>
    m = re.search(r"\.visualstudio\.com/([^/]+)/_git/", remote)
    if m:
        return m.group(1)
    m = re.search(r"github\.com[:/]([^/]+)/([^/\s]+?)(?:\.git)?/?$", remote)
    if m:
        return "%s/%s" % (m.group(1), m.group(2))
    return None


def _detect_stack(repo):
    """Detect the repo's stack(s) by manifest presence at the top level.
    Returns a list (a repo can be polyglot); empty if nothing recognised."""
    try:
        names = os.listdir(repo)
    except OSError:
        return []
    s = set(names)
    stack = []
    if any(n.endswith(".csproj") or n.endswith(".sln") for n in names):
        stack.append("dotnet")
    if "package.json" in s:
        stack.append("node")
    if "pyproject.toml" in s or "setup.py" in s:
        stack.append("python")
    if "go.mod" in s:
        stack.append("go")
    if "Cargo.toml" in s:
        stack.append("rust")
    if "pom.xml" in s or "build.gradle" in s:
        stack.append("jvm")
    return stack


def _status_from_ct(ct, now=None):
    """Map a last-commit unix timestamp to a status. None ct -> 'unknown'."""
    if ct is None:
        return "unknown"
    if now is None:
        now = time.time()
    age = now - ct
    return "active" if age <= REPO_STALE_MONTHS * _MONTH_SECONDS else "stale"


def _commit_ct(repo):
    """Unix timestamp of the repo's last commit, or None if it has none."""
    out = _git(repo, "log", "-1", "--format=%ct")
    if not out:
        return None
    try:
        return int(out.split()[0])
    except (ValueError, IndexError):
        return None


def _derive(path):
    """Derive the deterministic fields for a single repo at `path`."""
    remote = _git(path, "remote", "get-url", "origin")
    return {
        "name": os.path.basename(os.path.normpath(path)),
        "path": os.path.abspath(path),
        "remote": remote,
        "ado_project": parse_ado_project(remote),
        "stack": _detect_stack(path),
        "status": _status_from_ct(_commit_ct(path)),
    }


def discover(roots):
    """For each root, find immediate child dirs that contain a `.git` entry and
    derive each one's deterministic fields. Missing/unreadable roots are skipped."""
    repos = []
    for root in roots:
        root = os.path.expanduser(root)
        try:
            entries = sorted(os.listdir(root))
        except OSError:
            continue
        for name in entries:
            path = os.path.join(root, name)
            if not os.path.isdir(path):
                continue
            # `.git` is a dir for a normal clone, a file for a worktree/submodule.
            if not os.path.exists(os.path.join(path, ".git")):
                continue
            repos.append(_derive(path))
    return repos


def _load_overrides(data_dir):
    """Load hand-authored overrides keyed by repo name; {} if absent/invalid."""
    import json
    p = os.path.join(data_dir, "repos.overrides.json")
    try:
        with open(p) as f:
            data = json.load(f)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def merge_overrides(repos, overrides):
    """Apply hand-authored overrides onto discovered repos, in place.

    Always ensures `summary`/`keywords`/`domain` exist (defaults), then lets the
    overrides win for any of summary/keywords/domain/status. Discovery never
    writes overrides, so they survive every regeneration."""
    for r in repos:
        r.setdefault("summary", "")
        r.setdefault("keywords", [])
        r.setdefault("domain", [])
        ov = overrides.get(r["name"]) or {}
        if "summary" in ov:
            r["summary"] = ov["summary"]
        if "keywords" in ov:
            r["keywords"] = list(ov["keywords"])
        if "domain" in ov:
            r["domain"] = list(ov["domain"])
        if ov.get("status"):
            r["status"] = ov["status"]
    return repos


def _tokens(s):
    return set(re.findall(r"[a-z0-9]+", (s or "").lower()))


def score(query, repo):
    """Cheap relevance score: case-insensitive token overlap of the query against
    the repo's name/keywords/domain/stack/ado_project/path. A name-token hit is
    weighted extra so an obvious term lands its repo first."""
    qtok = query if isinstance(query, (set, frozenset)) else _tokens(query)
    if not qtok:
        return 0
    name_tok = _tokens(repo.get("name", ""))
    hay = _tokens(" ".join([
        repo.get("name", ""),
        " ".join(repo.get("keywords", []) or []),
        " ".join(repo.get("domain", []) or []),
        " ".join(repo.get("stack", []) or []),
        repo.get("ado_project") or "",
        (repo.get("path") or "").replace("/", " ").replace("-", " ").replace("_", " "),
    ]))
    return len(qtok & hay) + 2 * len(qtok & name_tok)


def match(query, repos):
    """Return all repos ranked best-first by `score()`. At small scale this is
    the whole ranked list; at 100+ repos it is the stage-1 pre-filter — take the
    top-K and only read those cards' prose into context."""
    qtok = _tokens(query)
    return sorted(repos, key=lambda r: (-score(qtok, r), r.get("name", "").lower()))


def render_md(repos, query=None):
    """Render a compact human/agent-readable index: one short block per repo."""
    lines = ["# Repo index", ""]
    note = ("Generated by `task-station repos --refresh`. Deterministic discovery "
            "+ hand-authored overrides (`repos.overrides.json`).")
    if query:
        lines.append("_%s %d repo(s) matching `%s`._" % (note, len(repos), query))
    else:
        lines.append("_%s %d repo(s)._" % (note, len(repos)))
    lines.append("")
    for r in repos:
        lines.append("## %s" % r.get("name", "?"))
        lines.append("- path: %s" % r.get("path", ""))
        lines.append("- ado_project: %s" % (r.get("ado_project") or "—"))
        lines.append("- stack: %s" % (", ".join(r.get("stack") or []) or "—"))
        lines.append("- status: %s" % r.get("status", "unknown"))
        if r.get("summary"):
            lines.append("- summary: %s" % r["summary"])
        if r.get("keywords"):
            lines.append("- keywords: %s" % ", ".join(r["keywords"]))
        if r.get("domain"):
            lines.append("- domain: %s" % ", ".join(r["domain"]))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _write(path, text):
    """Atomic write (tmp + replace), mirroring config.py's store writes."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def build_index(roots, data_dir=None):
    """Discover repos under `roots`, merge overrides, and write the index
    (`repos.md` + `repos.json`) into `data_dir`. Returns the structured list."""
    import json
    if data_dir is None:
        data_dir = paths.data_dir()
    repos = discover(roots)
    repos = merge_overrides(repos, _load_overrides(data_dir))
    repos.sort(key=lambda r: r["name"].lower())
    os.makedirs(data_dir, exist_ok=True)
    _write(os.path.join(data_dir, "repos.json"),
           json.dumps(repos, indent=2, ensure_ascii=False) + "\n")
    _write(os.path.join(data_dir, "repos.md"), render_md(repos))
    return repos


def _fingerprint(repo_path):
    """SEAM — per-repo identity for the deferred incremental-refresh cache.

    Combines the remote, the sorted top-level entries, and a sha1 of the README +
    manifests so a future `--refresh` can re-derive ONLY repos whose fingerprint
    changed (cached in `<data_dir>/.repos-cache.json`), plus a ~10-minute debounce.
    This is intentionally NOT called yet — a full rescan is fine at the current
    scale. TODO(scale): wire into build_index() behind a cache + debounce before
    the index grows past ~100 repos. See the module docstring's Scaling note.
    """
    try:
        entries = sorted(os.listdir(repo_path))
    except OSError:
        entries = []
    h = hashlib.sha1()
    h.update((_git(repo_path, "remote", "get-url", "origin") or "").encode())
    h.update("\0".join(entries).encode())
    for fn in ("README.md", "README", "package.json", "pyproject.toml", "go.mod"):
        fp = os.path.join(repo_path, fn)
        try:
            with open(fp, "rb") as f:
                h.update(f.read())
        except OSError:
            pass
    return h.hexdigest()
