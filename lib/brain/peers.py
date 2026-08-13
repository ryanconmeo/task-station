"""brain-station — the peers layer (lazy, read-only teammate private brains).

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 4c) from the brain source tree's
``scripts/peers.py`` @ 0.14.0. Body behaviour is unchanged; the module's
``sys.path`` self-bootstrap is gone (it is a package module now), the config
import it never used is gone with it, and ``errorlog`` is the relative sibling.

The third federation tier. Each teammate publishes a shared brain mirror
(see :mod:`publish`); a **registry** in the org-brain clone lists them. Peers are
cloned LAZILY on explicit user action (``brain peers add <alias>``) and only
pulled on ``peers sync`` — NEVER auto-cloned or auto-pulled at session start.

Search can include cloned peers as a low-priority tier (``brain search
--peers`` / MCP ``peers: true``); context injection never does. Failures during
add/sync are recorded to the error log and returned as a status — they never
raise into the session.

Registry shape (``<org_brain_clone>/registry.json``)::

    {"people": [{"alias": "...", "name": "...", "shared": "<repo url>"}]}

Out-of-registry peers may be declared in config under ``peers_extra``. A missing
or malformed registry is tolerated (peers_extra still works).

Layer rule: brain may import core and its own siblings, never board. Stdlib +
the sibling ``errorlog`` module only. Pure stdlib, Python 3.9+.
"""
import json
import shutil
import subprocess
from pathlib import Path

from . import errorlog


def registry_path(cfg):
    org_brain = cfg.get("org_brain_clone")
    return (Path(org_brain) / "registry.json") if org_brain else None


def load_registry(cfg):
    """People from the org brain registry.json + config ``peers_extra`` (registry
    wins on alias collision). Tolerates a missing/malformed registry."""
    people, seen = [], set()
    rp = registry_path(cfg)
    if rp and rp.exists():
        try:
            data = json.loads(rp.read_text())
            for e in (data.get("people") or []):
                if isinstance(e, dict) and e.get("alias") and e.get("shared"):
                    alias = str(e["alias"])
                    people.append({"alias": alias, "name": str(e.get("name") or alias),
                                   "shared": str(e["shared"])})
                    seen.add(alias)
        except (json.JSONDecodeError, OSError, AttributeError) as ex:
            errorlog.record("peers:registry", ex)
    for e in cfg.get("peers_extra", []):
        if e.get("alias") and e["alias"] not in seen:
            people.append(e)
            seen.add(e["alias"])
    return people


def peers_dir(cfg):
    return Path(cfg["peers_dir"])


def clone_path(cfg, alias):
    return peers_dir(cfg) / alias


def is_cloned(cfg, alias):
    return (clone_path(cfg, alias) / ".git").exists()


def list_peers(cfg):
    """Registry+extra people annotated with clone state, plus any on-disk clones
    not in the registry (surfaced so they can be pruned)."""
    people = load_registry(cfg)
    known = {p["alias"] for p in people}
    rows = [{**p, "cloned": is_cloned(cfg, p["alias"]),
             "path": str(clone_path(cfg, p["alias"]))} for p in people]
    pd = peers_dir(cfg)
    if pd.exists():
        for d in sorted(pd.iterdir()):
            if d.is_dir() and (d / ".git").exists() and d.name not in known:
                rows.append({"alias": d.name, "name": d.name,
                             "shared": "(local clone; not in registry)",
                             "cloned": True, "path": str(d)})
    return rows


def add(cfg, alias):
    """Lazily clone a peer's shared brain (shallow) under ``<peers_dir>/<alias>``.
    Errors are logged and returned as status — never raised."""
    people = {p["alias"]: p for p in load_registry(cfg)}
    if alias not in people:
        return {"status": "unknown", "alias": alias,
                "message": "not in registry or peers_extra"}
    if is_cloned(cfg, alias):
        return {"status": "exists", "alias": alias,
                "message": "already cloned", "path": str(clone_path(cfg, alias))}
    dest = clone_path(cfg, alias)
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = people[alias]["shared"]
    try:
        r = subprocess.run(["git", "clone", "--depth", "1", url, str(dest)],
                           capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError) as ex:
        errorlog.record("peers:add", ex)
        return {"status": "error", "alias": alias, "message": "clone error (logged)"}
    if r.returncode != 0:
        errorlog.record("peers:add", f"clone {alias} from {url}: {(r.stderr or r.stdout).strip()}")
        return {"status": "error", "alias": alias, "message": "clone failed (logged)"}
    return {"status": "cloned", "alias": alias, "path": str(dest)}


def sync(cfg, alias=None):
    """``git pull --ff-only`` one clone (``alias``) or all clones (no alias).
    Failures are logged, never raised."""
    if alias:
        targets = [alias]
    else:
        pd = peers_dir(cfg)
        targets = sorted(d.name for d in pd.iterdir() if (d / ".git").exists()) if pd.exists() else []
    results = []
    for al in targets:
        dest = clone_path(cfg, al)
        if not (dest / ".git").exists():
            results.append({"alias": al, "status": "not-cloned"})
            continue
        try:
            r = subprocess.run(["git", "-C", str(dest), "pull", "--ff-only"],
                               capture_output=True, text=True)
        except (OSError, subprocess.SubprocessError) as ex:
            errorlog.record("peers:sync", ex)
            results.append({"alias": al, "status": "error"})
            continue
        if r.returncode != 0:
            errorlog.record("peers:sync", f"pull {al}: {(r.stderr or r.stdout).strip()}")
            results.append({"alias": al, "status": "error"})
        else:
            results.append({"alias": al, "status": "pulled"})
    return results


def remove(cfg, alias):
    dest = clone_path(cfg, alias)
    if not dest.exists():
        return {"status": "absent", "alias": alias}
    shutil.rmtree(dest, ignore_errors=True)
    return {"status": "removed", "alias": alias}


def peer_roots(cfg):
    """The peers search tier: every ``<peers_dir>/<alias>/notes`` that exists."""
    pd = peers_dir(cfg)
    roots = []
    if pd.exists():
        for d in sorted(pd.iterdir()):
            n = d / "notes"
            if n.is_dir():
                roots.append(n)
    return roots


def peer_label(path, cfg):
    """``peer:<alias>/<slug>`` for a hit under ``peers_dir``, else ``None``."""
    try:
        rel = Path(path).resolve().relative_to(peers_dir(cfg).resolve())
    except (ValueError, OSError):
        return None
    alias = rel.parts[0] if rel.parts else "?"
    return f"peer:{alias}/{Path(path).stem}"
