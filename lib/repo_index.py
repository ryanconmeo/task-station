# repo_index.py
"""Hub-side, on-demand repo index for routing fuzzy tasks to the right repo(s).

A `claude` session launched from the hub (outside any repo) can't auto-load
anything inside the workspace dirs. This module gives it a regenerable index of
the repos under the configured roots so it can rank candidates at delegation
time. The index lives next to the task store (`<data_dir>/repos.md` +
`repos.json`) — NOT in tasks.db (repos aren't tasks), and NOT as per-repo
committed files.

== Privacy / opt-in enrichment ==
The index ALWAYS builds deterministically and offline: discovery + content stack
detection + a README-derived summary read NOTHING off the machine. The model
(`_call_model`) is contacted ONLY for a repo whose manifest `enrich` flag is True
— and `enrich` defaults to False for every repo. So a normal `repos --refresh`
makes ZERO model calls. Egress is opt-in, per repo, and the prompt is bounded to
repo name + ado_project + stack + README top + a `git ls-files` NAME sketch (with
a secret-name denylist guard); arbitrary file CONTENTS are never read.

Cards are FULLY auto-filled — no manual overrides required:
  - Deterministic discovery (no model): name/path/remote/ado_project/status, plus
    `stack` detected by CONTENT (tracked-file extension histogram + config/tooling
    signals + root manifests), so SQL/Flyway and manifest-less repos still get a
    stack.
  - `summary`/`keywords`: deterministic README-derived by default; replaced by a
    best-effort, FINGERPRINT-GATED model call ONLY for `enrich:true` repos. The
    index ALWAYS builds deterministically; enrichment is a layer on top and never
    raises out of the build. A deterministic refresh PRESERVES an existing non-empty
    summary rather than clobbering it (use `--re-summarize` to force regeneration).
  - Hand-authored overrides (`<data_dir>/repos.overrides.json`, keyed by repo name)
    remain as an optional escape hatch and ALWAYS win.
Precedence for summary/keywords: override > model > deterministic-fallback.

== Include/exclude manifest (`<data_dir>/repos.config.json`) ==
An auto-maintained map keyed by repo name -> {index: bool=true, enrich: bool=false}.
`repos --refresh` reconciles it: newly-discovered repos are added with safe defaults
and vanished repos are pruned. Only `index:true` repos reach the index; only
`enrich:true` repos are eligible for model egress. A `.task-station-ignore` marker
file at a repo root excludes it from discovery entirely, regardless of the manifest.

== Fingerprint gating (cheap in steady state) ==
Each repo carries a `fingerprint` = sha1(remote + sorted top-level entries +
sha1(README) + sha1(each root manifest))[:12]. It moves only on identity/structure
change, NOT on ordinary commits. On `--refresh` the model is called ONLY for a repo
that is new or whose fingerprint changed AND has no override summary; everything
else is served from `<data_dir>/.repos-cache.json`. So steady-state refreshes make
zero model calls.

== Scaling (schema baked in now, machinery deferred on purpose) ==
  1. Two-stage retrieval at 100+ repos: `match()` already returns a ranked list,
     so it doubles as a stage-1 pre-filter — take the top-K and only read those K
     cards' prose (`summary`/`keywords`/`domain`) into context.
  2. The fingerprint cache above already avoids redundant model work; a future
     `--refresh` debounce (~10 min) is the only remaining piece and is additive.
"""
import hashlib
import json
import os
import re
import subprocess
import time
from collections import Counter

import paths
from stack_map import EXT_TO_STACK, FILENAME_TO_STACK

# A repo whose last commit is older than this is flagged `stale`. Overridable via
# env for testing / tuning; the default matches the brief.
REPO_STALE_MONTHS = int(os.environ.get("REPO_STALE_MONTHS", "6"))
_MONTH_SECONDS = 30 * 86400

# Extension/filename -> stack for the tracked-file histogram. Sourced from
# `stack_map`, which is GENERATED from GitHub Linguist's languages.yml
# (regenerate via `tools/gen_stack_map.py`) — comprehensive language coverage
# with the tool's ergonomic labels preserved (python/node/dotnet/sql/...).
_EXT_THRESHOLD = 3          # a histogram stack needs this many files (or be dominant)
_STACK_CAP = 6              # keep cards readable

# Root manifests are a high-confidence signal and feed both stack detection and
# the structural fingerprint.
_MANIFEST_NAMES = ("package.json", "pyproject.toml", "setup.py", "go.mod",
                   "Cargo.toml", "pom.xml", "build.gradle", "build.gradle.kts")

# Model-call knobs for enrichment (kept bounded + cheap).
_LLM_MODEL = os.environ.get("TASK_STATION_REPO_MODEL", "claude-haiku-4-5-20251001")
_LLM_TIMEOUT = int(os.environ.get("TASK_STATION_REPO_MODEL_TIMEOUT", "45"))
_README_NAMES = ("README.md", "README.MD", "Readme.md", "readme.md",
                 "README.rst", "README.txt", "README")

# A repo owner can self-exclude by dropping this marker file at the repo root; it
# travels with the repo and wins over the manifest (excluded from discovery/index).
_IGNORE_MARKER = ".task-station-ignore"

# The auto-maintained include/exclude manifest (the single include/exclude surface).
_MANIFEST_FILE = "repos.config.json"
_DEFAULT_ENTRY = {"index": True, "enrich": False}


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


def _git_ls_files(repo):
    """Tracked files (relative paths), or [] when not a git repo / empty / fails.
    Tracked-only keeps it fast and ignores node_modules/build output."""
    out = _git(repo, "ls-files")
    if not out:
        return []
    return [ln for ln in out.split("\n") if ln]


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


def _root_manifests(repo):
    """Root-level manifest filenames present (sorted), incl. *.csproj/*.sln."""
    try:
        names = os.listdir(repo)
    except OSError:
        return []
    out = [n for n in names if n in _MANIFEST_NAMES]
    out += [n for n in names if n.endswith(".csproj") or n.endswith(".sln")]
    return sorted(out)


def _manifest_stacks(repo):
    """High-confidence stacks implied by root manifests."""
    names = set(os.listdir(repo)) if os.path.isdir(repo) else set()
    stack = []
    if any(n.endswith(".csproj") or n.endswith(".sln") for n in names):
        stack.append("dotnet")
    if "package.json" in names:
        stack.append("node")
    if "pyproject.toml" in names or "setup.py" in names:
        stack.append("python")
    if "go.mod" in names:
        stack.append("go")
    if "Cargo.toml" in names:
        stack.append("rust")
    if "pom.xml" in names or "build.gradle" in names or "build.gradle.kts" in names:
        stack.append("jvm")
    return stack


def _ext_histogram_stacks(files):
    """Stacks implied by the dominant tracked files. Counts each file by its
    extension (Linguist-derived `EXT_TO_STACK`), falling back to its basename for
    extension-less files (`FILENAME_TO_STACK`: Dockerfile, Makefile, …). Includes
    any stack at/above the threshold; if none reach it, includes the single
    dominant one so tiny repos still get a stack."""
    counts = Counter()
    for f in files:
        st = EXT_TO_STACK.get(os.path.splitext(f)[1].lower())
        if not st:
            st = FILENAME_TO_STACK.get(os.path.basename(f))
        if st:
            counts[st] += 1
    if not counts:
        return []
    ranked = [s for s, _ in counts.most_common()]
    qualifying = [s for s in ranked if counts[s] >= _EXT_THRESHOLD]
    return qualifying or ranked[:1]


def _tooling_stacks(files):
    """Stacks implied by config/tooling signals in the tracked-file list."""
    out = []
    base = [os.path.basename(f) for f in files]
    if any(b == "Dockerfile" or b.startswith("Dockerfile") or b.endswith(".dockerfile")
           for b in base):
        out.append("docker")
    if any(f.startswith(".github/workflows/") or "/.github/workflows/" in f
           for f in files):
        out.append("github-actions")
    flyway_conf = any(re.match(r"flyway.*\.conf$", b) or b == "flyway.toml" for b in base)
    migration_sql = any(f.lower().endswith(".sql") and "migration" in f.lower()
                        for f in files)
    if flyway_conf or migration_sql:
        out.append("flyway")
    if any(f.endswith(".tf") for f in files):
        out.append("terraform")
    return out


def _detect_stack(repo):
    """Detect stack(s) by CONTENT: union of root-manifest signals (high
    confidence), the tracked-file extension histogram, and config/tooling
    signals. Deduped, manifest-first, capped to the few most relevant."""
    files = _git_ls_files(repo)
    ordered = []
    ordered += _manifest_stacks(repo)
    ordered += _ext_histogram_stacks(files)
    ordered += _tooling_stacks(files)
    seen = []
    for s in ordered:
        if s not in seen:
            seen.append(s)
    return seen[:_STACK_CAP]


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
    derive each one's deterministic fields. Missing/unreadable roots are skipped.
    A `.task-station-ignore` marker at a repo root excludes it entirely (repo-owner
    opt-out), as if it didn't exist."""
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
            # Repo-owner self-exclusion: the marker fully removes it from discovery.
            if os.path.exists(os.path.join(path, _IGNORE_MARKER)):
                continue
            repos.append(_derive(path))
    return repos


# ---------------------------------------------------------------------------
# Discovery-root detection (first-run onboarding helper)
# ---------------------------------------------------------------------------

def _count_git_children(path):
    """How many immediate child dirs of `path` look like git repos."""
    try:
        entries = os.listdir(path)
    except OSError:
        return 0
    n = 0
    for name in entries:
        child = os.path.join(path, name)
        if os.path.isdir(child) and os.path.exists(os.path.join(child, ".git")):
            n += 1
    return n


def detect_roots(home=None):
    """Propose candidate discovery roots for first-run onboarding: `~/Workspace`
    and `~/Workspace-Other` if they exist, plus any other top-level `~` directory
    that itself contains >=2 git repos. Returned in a stable order, de-duplicated."""
    if home is None:
        home = os.path.expanduser("~")
    roots = []
    for name in ("Workspace", "Workspace-Other"):
        p = os.path.join(home, name)
        if os.path.isdir(p) and p not in roots:
            roots.append(p)
    try:
        entries = sorted(os.listdir(home))
    except OSError:
        entries = []
    for name in entries:
        p = os.path.join(home, name)
        if p in roots or not os.path.isdir(p):
            continue
        if _count_git_children(p) >= 2:
            roots.append(p)
    return roots


# ---------------------------------------------------------------------------
# Include/exclude manifest (repos.config.json)
# ---------------------------------------------------------------------------

def _manifest_path(data_dir):
    return os.path.join(data_dir, _MANIFEST_FILE)


def load_manifest(data_dir):
    """Load the include/exclude manifest (name -> {index, enrich}); {} if absent."""
    try:
        with open(_manifest_path(data_dir)) as f:
            data = json.load(f)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_manifest(data_dir, manifest):
    """Atomically persist the manifest (sorted keys for stable diffs)."""
    os.makedirs(data_dir, exist_ok=True)
    _write(_manifest_path(data_dir),
           json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def _norm_entry(entry):
    """Coerce a (possibly partial/legacy) manifest entry to {index, enrich}."""
    e = dict(_DEFAULT_ENTRY)
    if isinstance(entry, dict):
        if "index" in entry:
            e["index"] = bool(entry["index"])
        if "enrich" in entry:
            e["enrich"] = bool(entry["enrich"])
    return e


def reconcile_manifest(manifest, discovered_names):
    """Return a manifest reconciled against the discovered repo names: every
    discovered repo present (existing flags preserved, new repos get index:true,
    enrich:false), and entries whose repo no longer exists pruned. Does not mutate
    the input."""
    out = {}
    for name in discovered_names:
        out[name] = _norm_entry(manifest.get(name))
    return out


def entry_for(manifest, name):
    """The normalized {index, enrich} entry for `name` (defaults if unknown)."""
    return _norm_entry(manifest.get(name))


def _load_overrides(data_dir):
    """Load hand-authored overrides keyed by repo name; {} if absent/invalid."""
    p = os.path.join(data_dir, "repos.overrides.json")
    try:
        with open(p) as f:
            data = json.load(f)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def merge_overrides(repos, overrides):
    """Apply hand-authored overrides onto repos, in place.

    Ensures `summary`/`keywords`/`domain` exist (without clobbering values already
    set by enrichment), then lets overrides win for any of summary/keywords/domain/
    status. Discovery never writes overrides, so they survive every regeneration."""
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
    note = ("Generated by `task-station repos --refresh`. Deterministic discovery + "
            "offline README summary; model enrichment is opt-in per repo "
            "(`repos enrich <name>`). Overrides (`repos.overrides.json`) win.")
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


# ---------------------------------------------------------------------------
# Fingerprint + enrichment cache
# ---------------------------------------------------------------------------

def _sha1_bytes(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha1(f.read()).hexdigest()
    except OSError:
        return ""


def _find_readme(repo_path):
    for name in _README_NAMES:
        fp = os.path.join(repo_path, name)
        if os.path.isfile(fp):
            return fp
    return None


def _fingerprint(repo_path):
    """Per-repo identity hash: sha1(remote + sorted top-level entries +
    sha1(README) + sha1(each root manifest))[:12]. Moves on identity/structure
    change (remote, top-level layout, README or manifest content) but NOT on
    ordinary commits to files deep in the tree — exactly the gate for enrichment."""
    h = hashlib.sha1()
    h.update((_git(repo_path, "remote", "get-url", "origin") or "").encode())
    try:
        entries = sorted(os.listdir(repo_path))
    except OSError:
        entries = []
    h.update("\n".join(entries).encode())
    rp = _find_readme(repo_path)
    h.update((_sha1_bytes(rp) if rp else "").encode())
    for m in _root_manifests(repo_path):
        h.update(_sha1_bytes(os.path.join(repo_path, m)).encode())
    return h.hexdigest()[:12]


def _cache_path(data_dir):
    return os.path.join(data_dir, ".repos-cache.json")


def _load_cache(data_dir):
    try:
        with open(_cache_path(data_dir)) as f:
            data = json.load(f)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_cache(data_dir, cache):
    _write(_cache_path(data_dir), json.dumps(cache, indent=2, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Deterministic fallback (always available)
# ---------------------------------------------------------------------------

def _read_readme(repo_path, max_lines=200):
    rp = _find_readme(repo_path)
    if not rp:
        return ""
    try:
        with open(rp, encoding="utf-8", errors="replace") as f:
            out = []
            for i, line in enumerate(f):
                if i >= max_lines:
                    break
                out.append(line.rstrip("\n"))
            return "\n".join(out)
    except OSError:
        return ""


def _first_paragraph(text):
    """First contiguous block of prose: skips leading headings, blanks, and
    image/badge lines; stops at the next blank line."""
    buf = []
    for raw in text.splitlines():
        s = raw.strip()
        if s.startswith("#") or s.startswith("!["):
            continue
        if not s:
            if buf:
                break
            continue
        buf.append(s)
    return " ".join(buf).strip()


_STOPWORDS = {"the", "and", "for", "with", "a", "an", "to", "of", "in", "is", "on",
              "this", "that", "it", "as", "by", "or", "be", "are"}


def _keyword_set(repo, text):
    kws = []
    for raw in text.splitlines():
        s = raw.strip()
        if s.startswith("#"):
            kws += list(_tokens(s.lstrip("#")))
    kws += [t for t in re.split(r"[-_./ ]+", repo.get("name", "").lower()) if t]
    kws += list(repo.get("stack") or [])
    if repo.get("ado_project"):
        kws += [t for t in re.split(r"[/ ]+", repo["ado_project"].lower()) if t]
    seen = []
    for k in kws:
        if len(k) < 2 or k in _STOPWORDS:
            continue
        if k not in seen:
            seen.append(k)
    return seen[:12]


def _deterministic_enrich(repo):
    """README-derived summary + keywords. Always succeeds (never raises)."""
    text = _read_readme(repo["path"])
    summary = _first_paragraph(text)
    if not summary:
        st = ", ".join(repo.get("stack") or [])
        summary = "%s%s" % (repo.get("name", "repo"), " (%s)" % st if st else "")
    return {"summary": summary[:240], "keywords": _keyword_set(repo, text),
            "source": "fallback"}


# ---------------------------------------------------------------------------
# Best-effort model enrichment
# ---------------------------------------------------------------------------

# Egress denylist: secret-bearing filenames that must NEVER enter the enrichment
# input — not even as names in the tree sketch. The enrichment only ever reads the
# README + file NAMES (never arbitrary contents), so contents of these can't leak;
# this guard additionally keeps their NAMES out of the prompt.
_SECRET_SUFFIXES = (".pem", ".key", ".pfx", ".p12", ".keystore", ".jks",
                    ".crt", ".cer", ".der", ".asc", ".gpg", ".kdbx")
_SECRET_EXACT = (".env", ".npmrc", ".netrc", ".pgpass", ".htpasswd",
                 "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519")


def _is_sensitive_name(path):
    """True if `path`'s basename looks secret-bearing (.env, *.pem, *.key,
    secrets*, credentials*, .npmrc, private keys, …). Conservative by design —
    the cost of dropping a benign name from the tree sketch is nil."""
    base = os.path.basename(path).lower()
    if base in _SECRET_EXACT:
        return True
    if base.startswith(".env") or base.startswith("env."):
        return True
    if base.startswith("secret") or base.startswith("credential"):
        return True
    if "secret" in base or "credential" in base or "password" in base:
        return True
    for suf in _SECRET_SUFFIXES:
        if base.endswith(suf):
            return True
    return False


def _tree_sketch(repo, limit=40):
    files = _git_ls_files(repo["path"])
    if not files:
        try:
            files = sorted(os.listdir(repo["path"]))
        except OSError:
            files = []
    # Egress guard: never let secret-bearing names into the prompt.
    files = [f for f in files if not _is_sensitive_name(f)]
    return "\n".join(files[:limit])


def _build_prompt(repo):
    """A bounded prompt: name + ado_project + stack + README head + tree sketch."""
    readme = _read_readme(repo["path"], max_lines=80)
    return "\n".join([
        "You are indexing a code repository for a task-routing system.",
        "Return ONLY a strict JSON object, no prose and no code fences:",
        '{"summary": "<one sentence, <=160 chars, what the repo is/does>", '
        '"keywords": ["lowercase", "domain", "terms"]}',
        "",
        "name: %s" % repo.get("name", ""),
        "ado_project: %s" % (repo.get("ado_project") or "-"),
        "stack: %s" % (", ".join(repo.get("stack") or []) or "-"),
        "",
        "README (first lines):",
        readme or "(no README)",
        "",
        "file tree (sample):",
        _tree_sketch(repo) or "(empty)",
    ])


def _call_model(prompt, model=None, timeout=None):
    """The single model-call seam (tests monkeypatch THIS). Shells out to the
    already-authenticated Claude CLI for a one-shot headless completion and
    returns its raw stdout. Raises on any failure so callers can degrade."""
    cmd = ["claude", "-p", prompt,
           "--model", model or _LLM_MODEL,
           "--output-format", "json"]
    out = subprocess.run(cmd, capture_output=True, text=True,
                         timeout=timeout or _LLM_TIMEOUT)
    if out.returncode != 0:
        raise RuntimeError("claude -p failed (%s): %s" % (out.returncode, (out.stderr or "")[:200]))
    return out.stdout


def _unwrap_cli_json(raw):
    """`claude -p --output-format json` wraps the model text in an envelope with a
    `result` field; return that inner text, or the raw string if not an envelope."""
    try:
        env = json.loads(raw)
    except Exception:
        return raw
    if isinstance(env, dict) and "result" in env:
        return env["result"]
    return raw


def _extract_json_object(text):
    if not text:
        return None
    try:
        o = json.loads(text)
        if isinstance(o, dict):
            return o
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            o = json.loads(m.group(0))
            if isinstance(o, dict):
                return o
        except Exception:
            pass
    return None


def _llm_enrich(repo):
    """Attempt a model summary/keywords. Returns a dict or None (caller degrades).
    May raise — `_enrich` swallows it."""
    raw = _call_model(_build_prompt(repo))
    obj = _extract_json_object(_unwrap_cli_json(raw))
    if not obj or not obj.get("summary"):
        return None
    summary = str(obj["summary"]).strip().splitlines()[0][:240]
    kws = obj.get("keywords") or []
    kws = [str(k).strip() for k in kws if str(k).strip()][:12]
    return {"summary": summary, "keywords": kws, "source": "llm"}


def _enrich(repo, use_llm=True):
    """Best-effort enrichment. Tries the model when use_llm; on ANY failure (CLI
    missing, timeout, bad JSON, empty result) falls back deterministically. Always
    returns {summary, keywords, source} and NEVER raises."""
    if use_llm:
        try:
            res = _llm_enrich(repo)
            if res:
                return res
        except Exception:
            pass
    return _deterministic_enrich(repo)


def build_index(roots, data_dir=None, use_llm=False, dry_run=False,
                re_summarize=False, egress=None):
    """Discover repos, reconcile the include/exclude manifest, fill stack by
    content, enrich summary/keywords for `enrich:true` repos only, merge overrides,
    and write the index (`repos.md` + `repos.json`, plus the `.repos-cache.json`
    enrichment cache and `repos.config.json` manifest). Returns the structured list
    (index:true repos only).

    Egress is OPT-IN: a model call happens ONLY for a repo whose manifest `enrich`
    flag is True AND `use_llm` is set AND it's new/changed (fingerprint-gated). Every
    other repo gets a deterministic README-derived summary — and an EXISTING non-empty
    summary is PRESERVED, not clobbered (unless `re_summarize`). `use_llm` defaults to
    False so library/read-path callers never spawn a model by accident; the CLI's
    `repos --refresh` passes it (subject to `--no-llm` / the global config gate).

    `dry_run` reports (via `egress`, a list the caller passes) which repos WOULD have
    content sent, without sending. On a real send, the sent repo names are appended to
    `egress` too. The build is always deterministic and crash-free regardless.
    """
    if data_dir is None:
        data_dir = paths.data_dir()
    discovered = discover(roots)
    discovered_names = [r["name"] for r in discovered]

    # The manifest is the single include/exclude surface: reconcile (add new repos
    # with safe defaults, prune vanished ones) and persist BEFORE filtering so the
    # user can flip flags on anything that was discovered.
    manifest = reconcile_manifest(load_manifest(data_dir), discovered_names)
    save_manifest(data_dir, manifest)

    # Only index:true repos reach the index (and are eligible for enrichment).
    repos = [r for r in discovered if entry_for(manifest, r["name"])["index"]]

    overrides = _load_overrides(data_dir)
    cache = _load_cache(data_dir)
    new_cache = {}

    for r in repos:
        fp = _fingerprint(r["path"])
        r["fingerprint"] = fp
        name = r["name"]
        enrich_flag = entry_for(manifest, name)["enrich"]
        ov = overrides.get(name) or {}
        if ov.get("summary"):
            # Override supplies the summary; skip enrichment entirely (it wins in
            # merge_overrides below). Record the fingerprint so a later removal of
            # the override re-triggers a fresh enrichment.
            new_cache[name] = {"fingerprint": fp}
            continue
        cached = cache.get(name) or {}
        eligible = enrich_flag and use_llm

        if eligible and not dry_run:
            # Opt-in egress path. Reuse a prior MODEL summary when nothing structural
            # changed -> zero model calls in steady state.
            if (cached.get("source") == "llm"
                    and cached.get("fingerprint") == fp
                    and cached.get("summary") is not None
                    and not re_summarize):
                r["summary"] = cached["summary"]
                r["keywords"] = list(cached.get("keywords") or [])
                new_cache[name] = cached
                continue
            if egress is not None:
                egress.append(name)
            enr = _enrich(r, use_llm=True)
            r["summary"] = enr["summary"]
            r["keywords"] = enr["keywords"]
            new_cache[name] = {"fingerprint": fp, "summary": enr["summary"],
                               "keywords": enr["keywords"], "source": enr["source"]}
            continue

        # Deterministic path (enrich off, --no-llm, or --dry-run): no egress.
        if dry_run and eligible and egress is not None:
            egress.append(name)
        if cached.get("summary") and not re_summarize:
            # Don't clobber an existing (model- or fallback-derived) summary on a
            # deterministic refresh; preserve it. Only fill repos that lack one.
            r["summary"] = cached["summary"]
            r["keywords"] = list(cached.get("keywords") or [])
            new_cache[name] = cached
            continue
        enr = _deterministic_enrich(r)
        r["summary"] = enr["summary"]
        r["keywords"] = enr["keywords"]
        new_cache[name] = {"fingerprint": fp, "summary": enr["summary"],
                           "keywords": enr["keywords"], "source": enr["source"]}

    repos = merge_overrides(repos, overrides)
    repos.sort(key=lambda r: r["name"].lower())
    os.makedirs(data_dir, exist_ok=True)
    _write(os.path.join(data_dir, "repos.json"),
           json.dumps(repos, indent=2, ensure_ascii=False) + "\n")
    _write(os.path.join(data_dir, "repos.md"), render_md(repos))
    _save_cache(data_dir, new_cache)
    return repos
