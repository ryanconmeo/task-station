"""brain.config — runtime configuration resolution for the brain plane.

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 1) from the brain source tree's
``scripts/pb_config.py`` @ 0.14.0, GENERICIZED AT SOURCE. The source file was the
port's top scrub offender (32 org-term hits) — every one of them was a NAME, not
a value: the env-var prefix, the config filename, the warning prefix and prose.
The value defaults were already org-agnostic by 0.14.0 (that is what the source's
0.11-0.14 genericization arc did), and they stay that way here: the real
org/project/repo/owner arrive from the user's config file (an OrgProfile writes
it) at runtime, NEVER from a literal in this file.

Order of precedence, per key:
  1. environment variable (see ENV below — one for every key), namespace
     ``TASK_STATION_BRAIN_*``
  2. ``~/.claude/brain-station.json``  (a full config, OR a one-line pointer
     ``{"config": "<path>"}`` that is followed to the real config)
  3. ``~/brains/config.json``          (the home config, if present)
  4. built-in defaults (content under ``~/brains/``; mutable state under the
     task-station data home)

Robustness contract:
  * A malformed config file is not fatal to *read* paths: :func:`load` prints ONE
    stderr warning and degrades to defaults.
  * *Write* paths must call :func:`require_valid` first — it RAISES on a broken
    config instead of silently redirecting writes to the default vault.

TWO DIFFERENT ANCHORS, deliberately. The config chain above is anchored to
``Path.home()`` exactly as the source had it: it is the brain's OWN file, at a
stable place a human edits. The two MUTABLE-STATE defaults (``tasks_db``,
``state_dir``) instead resolve through :func:`core.paths.data_dir`, because they
must find what the BOARD wrote — and the board's store follows
``TASK_STATION_HOME`` / ``CLAUDE_CONFIG_DIR``. Hard-coding ``~/.claude/...`` for
those (as the standalone source had to) would miss a relocated store, which is
the whole reason the two planes now share one data home.

No install-time patching: every caller resolves at runtime, so one install serves
every machine. Nothing here is computed at import time (all paths read
``Path.home()`` / ``data_dir()`` lazily) so an overridden ``$HOME`` is honoured.

Layer rule: brain may import core, never board. Stdlib + ``core.paths`` only.
"""
import json
import os
import sys
from pathlib import Path

import core.paths as _paths

# --- env var name for every config key ------------------------------------
ENV = {
    "vault": "TASK_STATION_BRAIN_VAULT",
    "memory": "TASK_STATION_BRAIN_MEMORY",
    "org_brain_clone": "TASK_STATION_BRAIN_ORG_BRAIN_CLONE",
    "tasks_db": "TASK_STATION_BRAIN_TASKS_DB",
    "episodic_stream": "TASK_STATION_BRAIN_EPISODIC_STREAM",
    "state_dir": "TASK_STATION_BRAIN_STATE",
    "inject_context": "TASK_STATION_BRAIN_INJECT_CONTEXT",
    "auto_distill": "TASK_STATION_BRAIN_AUTO_DISTILL",
    "inject_keywords": "TASK_STATION_BRAIN_INJECT_KEYWORDS",
    "publish_mirror": "TASK_STATION_BRAIN_PUBLISH_MIRROR",
    "peers_dir": "TASK_STATION_BRAIN_PEERS_DIR",
    # Knowledge plane. org_label is the display name for the shared org wiki (a
    # real org supplies its own); alias/owner stamp contributor records; the
    # forge_* keys drive the promote push/PR — org-agnostic, NEVER literals in
    # code (absent → promote queues instead of pushing).
    "org_label": "TASK_STATION_BRAIN_ORG_LABEL",
    "alias": "TASK_STATION_BRAIN_ALIAS",
    "owner": "TASK_STATION_BRAIN_OWNER",
    "task": "TASK_STATION_BRAIN_TASK",
    "forge_kind": "TASK_STATION_BRAIN_FORGE_KIND",
    "forge_org": "TASK_STATION_BRAIN_FORGE_ORG",
    "forge_project": "TASK_STATION_BRAIN_FORGE_PROJECT",
    "forge_repo": "TASK_STATION_BRAIN_FORGE_REPO",
    "forge_target_branch": "TASK_STATION_BRAIN_FORGE_TARGET_BRANCH",
    "ado_org": "TASK_STATION_BRAIN_ADO_ORG",
    # Subscription memos. task_station_cli = the published task-station CLI
    # (stable engine path by default). knowledge_memos gates memo delivery —
    # tri-state: unset ⇒ auto-on iff task-station is detected.
    "task_station_cli": "TASK_STATION_BRAIN_TASK_STATION_CLI",
    "knowledge_memos": "TASK_STATION_BRAIN_KNOWLEDGE_MEMOS",
}
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}

_warned = set()  # dedup: emit each distinct config warning at most once per process


class ConfigError(Exception):
    """Raised by ``require_valid()`` when a present config source is malformed."""


# --- path anchors (lazy: read $HOME every call) ----------------------------
def _primary_config_path():
    return Path.home() / ".claude/brain-station.json"


def _home_root():
    return Path.home() / "brains"


def _home_config_path():
    return _home_root() / "config.json"


def DEFAULT_VAULT():
    return _home_root() / "brain"


def DEFAULT_MEMORY(vault):
    """Memory lives inside the vault (init migrate-then-links the native dir here)."""
    return vault / "memory"


def DEFAULT_ORG_BRAIN():
    return _home_root() / "org-brain"


def _data_home():
    """The task-station data home — the ONE place both planes keep mutable state.
    Resolves TASK_STATION_HOME > CLAUDE_CONFIG_DIR/task-station-data >
    XDG_STATE_HOME/task-station > ~/.claude/task-station-data (core.paths)."""
    return Path(_paths.data_dir())


def DEFAULT_TASKS_DB():
    """The board's sqlite store. Anchored to the data home rather than a literal
    ``~/.claude/...`` so a relocated store is still found (the standalone source
    could not do this — it had no core.paths to read)."""
    return _data_home() / "store/tasks.db"


def DEFAULT_TASK_STATION_CLI():
    """The stable published task-station CLI entry (memo delivery bridge). The
    ``task-station-engine`` symlink is re-pointed at the active lib/ every
    session, so this path survives version bumps."""
    return Path.home() / ".claude/task-station-engine/task-station.py"


def DEFAULT_EPISODIC_STREAM(tasks_db):
    """The event-stream dir. task-station keeps its ledger in a ``stream/`` dir
    that is a *sibling* of the sqlite store — i.e. under the same data home.
    Derive it from the resolved tasks_db (``.../<root>/store/tasks.db`` ->
    ``.../<root>/stream``) so a machine that only configured ``tasks_db`` gets the
    stream for free; ``None`` if tasks_db is unset."""
    if tasks_db is None:
        return None
    return Path(tasks_db).parent.parent / "stream"


def DEFAULT_STATE():
    """Brain-plane state (gate stamps, throttles). Under the shared data home for
    the same reason as the store — one env var relocates both planes."""
    return _data_home() / "brain-state"


def DEFAULT_PUBLISH_MIRROR():
    """The shared-brain mirror repo — org-readable, owner-writable."""
    return _home_root() / "shared-brain"


def DEFAULT_PEERS_DIR():
    """Where teammates' shared brains are cloned lazily, one dir per alias."""
    return _home_root() / "peers"


# --- helpers ---------------------------------------------------------------
def _warn_once(msg):
    if msg not in _warned:
        _warned.add(msg)
        sys.stderr.write(f"brain-station: config warning: {msg}; degrading to defaults.\n")


def _read_json(path):
    """Return (data, error). ``error`` is a message string when the file exists
    but could not be read/parsed; both are None when the file is simply absent."""
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None, None
    except OSError as e:
        return None, f"cannot read {path}: {e}"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return None, f"malformed JSON in {path}: {e}"


def _load_primary():
    """Read ~/.claude/brain-station.json, following a {"config": "<path>"}
    pointer to the real config. Returns (cfg_dict_or_None, problem_or_None)."""
    data, err = _read_json(_primary_config_path())
    if err or data is None:
        return None, err
    if isinstance(data, dict) and isinstance(data.get("config"), str):
        target = Path(os.path.expanduser(data["config"]))
        tdata, terr = _read_json(target)
        if terr:
            return None, f"broken config pointer {_primary_config_path()} -> {target}: {terr}"
        if tdata is None:
            return None, f"broken config pointer {_primary_config_path()} -> {target}: no such file"
        return (tdata if isinstance(tdata, dict) else {}), None
    return (data if isinstance(data, dict) else {}), None


def _resolve():
    """Merge the JSON config sources into one dict, primary winning over the
    home config. Returns (cfg, problem). ``problem`` is the first source error."""
    primary, perr = _load_primary()
    home, herr = _read_json(_home_config_path())
    cfg = {}
    if isinstance(home, dict):
        cfg.update(home)
    if isinstance(primary, dict):
        cfg.update(primary)  # ~/.claude config wins over ~/brains/config.json
    return cfg, (perr or herr)


def _env(key):
    """Non-empty env value for ``key``, else None."""
    v = os.environ.get(ENV[key])
    return v if v else None


def _resolve_org_brain_clone(cfg):
    """The org-brain clone path.

      env  TASK_STATION_BRAIN_ORG_BRAIN_CLONE
      json org_brain_clone
      else DEFAULT_ORG_BRAIN()

    The retired org-tier aliases were dropped in the source's 0.14.0, along with
    the extractor whose rename table was the reason they survived.
    """
    v = _env("org_brain_clone")
    if v is None and cfg.get("org_brain_clone") is not None:
        v = cfg["org_brain_clone"]
    if v is None:
        return DEFAULT_ORG_BRAIN()
    return Path(os.path.expanduser(str(v)))


def _parse_bool(v, default):
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    s = str(v).strip().lower()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    return default


def _pick(cfg, key, default):
    """env -> json -> default, for a raw (non-typed) value."""
    ev = _env(key)
    if ev is not None:
        return ev
    if cfg.get(key) is not None:
        return cfg[key]
    return default


def _pick_path(cfg, key, default):
    v = _pick(cfg, key, default)
    return Path(os.path.expanduser(str(v))) if v is not None else None


def _resolve_tristate(cfg, key):
    """A boolean config key that must distinguish *unset* from an explicit False:
    returns True/False when set (env or JSON, parsed), else None. Used by the
    ``knowledge_memos`` auto-gate — None means "decide by detection"."""
    ev = _env(key)
    if ev is not None:
        return _parse_bool(ev, None)
    v = cfg.get(key)
    if v is None:
        return None
    return _parse_bool(v, None)


def discover_native_memory(cwd=None):
    """Locate the Claude native-memory dir (``~/.claude/projects/<enc>/memory``
    holding MEMORY.md), never alphabetical-first:

      1. the entry whose encoded path == ``cwd`` (default: os.getcwd())
      2. else the memory dir with the newest-mtime MEMORY.md
      3. else None
    """
    root = Path.home() / ".claude/projects"
    if not root.exists():
        return None
    if cwd is None:
        try:
            cwd = os.getcwd()
        except OSError:
            cwd = str(Path.home())
    exact = root / str(cwd).replace(os.sep, "-") / "memory"
    if (exact / "MEMORY.md").exists():
        return exact
    candidates = []
    for mem in root.glob("*/memory"):
        md = mem / "MEMORY.md"
        if md.exists():
            try:
                candidates.append((md.stat().st_mtime, mem))
            except OSError:
                pass
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0], reverse=True)
    return candidates[0][1]


def _resolve_memory(cfg, vault):
    ev = _env("memory")
    if ev is not None:
        return Path(os.path.expanduser(ev))
    if cfg.get("memory") is not None:
        return Path(os.path.expanduser(str(cfg["memory"])))
    disc = discover_native_memory()
    if disc is not None:
        return disc
    return DEFAULT_MEMORY(vault)


def _resolve_keywords(cfg):
    ev = _env("inject_keywords")
    if ev is not None:
        return [k.strip() for k in ev.split(",") if k.strip()]
    kw = cfg.get("inject_keywords", [])
    if isinstance(kw, list):
        return [k for k in kw if isinstance(k, str)]
    return []


def _resolve_peers_extra(cfg):
    """Out-of-registry peers declared in config: a list of
    ``{alias, name, shared}`` dicts. Malformed entries are dropped, not fatal.
    """
    extra = cfg.get("peers_extra", [])
    if not isinstance(extra, list):
        return []
    out = []
    for e in extra:
        if isinstance(e, dict) and e.get("alias") and e.get("shared"):
            out.append({
                "alias": str(e["alias"]),
                "name": str(e.get("name") or e["alias"]),
                "shared": str(e["shared"]),
            })
    return out


def load(_warn=True):
    cfg, problem = _resolve()
    if problem and _warn:
        _warn_once(problem)

    vault = _pick_path(cfg, "vault", DEFAULT_VAULT())
    tasks_db = _pick_path(cfg, "tasks_db", DEFAULT_TASKS_DB())
    return {
        "vault": vault,
        "memory": _resolve_memory(cfg, vault),
        "tasks_db": tasks_db,
        "episodic_stream": _pick_path(cfg, "episodic_stream", DEFAULT_EPISODIC_STREAM(tasks_db)),
        "state_dir": _pick_path(cfg, "state_dir", DEFAULT_STATE()),
        "org_brain_clone": _resolve_org_brain_clone(cfg),
        "inject_context": _parse_bool(_pick(cfg, "inject_context", True), True),
        "auto_distill": _parse_bool(_pick(cfg, "auto_distill", True), True),
        "inject_keywords": _resolve_keywords(cfg),
        "publish_mirror": _pick_path(cfg, "publish_mirror", DEFAULT_PUBLISH_MIRROR()),
        "peers_dir": _pick_path(cfg, "peers_dir", DEFAULT_PEERS_DIR()),
        "peers_extra": _resolve_peers_extra(cfg),
        # knowledge plane: string values, org-agnostic, defaults are safe generics
        # — the real org/project/repo live in the user's config, never in code.
        "org_label": _pick(cfg, "org_label", "org"),
        # Tier labels {private, org} from an OrgProfile (brain-init --profile). A
        # plain dict; defaults empty so prose falls back to the built-in generics.
        "labels": cfg.get("labels") if isinstance(cfg.get("labels"), dict) else {},
        "alias": _pick(cfg, "alias", None),
        "owner": _pick(cfg, "owner", None),
        "task": _pick(cfg, "task", None),
        # forge_kind selects the git-forge adapter (ado | github) in core.forge.
        # "ado" is the source's default and is a VENDOR name, not an org one; a
        # profile overrides it.
        "forge_kind": (_pick(cfg, "forge_kind", "ado") or "ado").strip().lower(),
        "forge_org": _pick(cfg, "forge_org", None),
        "forge_project": _pick(cfg, "forge_project", None),
        "forge_repo": _pick(cfg, "forge_repo", None),
        "forge_target_branch": _pick(cfg, "forge_target_branch", "main"),
        # ADO org url for the ado work-item helper; a plain string (not a path).
        # Default None so the helper's own env / built-in fallbacks stay
        # authoritative when nothing is configured here.
        "ado_org": _pick(cfg, "ado_org", None),
        # Subscription memos: the published task-station CLI + the delivery gate
        # (tri-state; None ⇒ the subscriber auto-decides by detection).
        "task_station_cli": _pick_path(cfg, "task_station_cli", DEFAULT_TASK_STATION_CLI()),
        "knowledge_memos": _resolve_tristate(cfg, "knowledge_memos"),
    }


def require_valid():
    """Assert config integrity before any WRITE. Raises ``ConfigError`` when a
    present config source is malformed (which would otherwise silently redirect
    the write to the default vault). Absent config is fine — that's the honest
    defaults case. Returns the loaded config on success."""
    _, problem = _resolve()
    if problem:
        raise ConfigError(
            f"{problem}. Refusing to write — a broken config would silently "
            f"redirect writes to the default vault. Fix {_primary_config_path()} "
            f"(or run /brain-init)."
        )
    return load(_warn=False)


def state_dir():
    d = load(_warn=False)["state_dir"]
    d.mkdir(parents=True, exist_ok=True)
    return d


def publish_mirror_configured():
    """True iff a publish mirror was *explicitly* set (env or a config-file key).

    The built-in default location (:func:`DEFAULT_PUBLISH_MIRROR`) does NOT
    count — so the ``/brain-heal`` auto-step can be gated on real opt-in and the
    brain never auto-publishes to a mirror the owner never set up. An explicit
    ``publish`` command still uses the default when nothing is configured."""
    if _env("publish_mirror"):
        return True
    cfg, _ = _resolve()
    return cfg.get("publish_mirror") is not None
