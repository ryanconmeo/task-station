"""brain-station — reference records: the task ↔ knowledge wire.

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 2) from the brain source tree's
``scripts/references.py`` @ 0.14.0.

When a task consumes org knowledge, we do NOT copy the org node into the private
vault. Instead we stamp a tiny **reference record** at ``references/<org-slug>.md``
whose body is a single line pointing back at the canonical org node, and whose
frontmatter records which tasks touched it and at what org revision:

    name: ref-<org-slug>   type: reference-record
    org_node: <org-slug>   org_rev: <org-brain clone HEAD sha at fetch>
    tasks: [<task handles>] fetched: <date>

This keeps a single source of truth (the org node) while making the wire
auditable in both directions (``ref list --task`` / ``--node``) and letting the
health lint flag a stub whose org node has moved on since it was fetched
(``org_rev`` behind the clone HEAD for that file ⇒ **dirty**).

All writes go through ``notes.write_note_fm`` (the single write path); git and
similarity are the only external deps, as elsewhere.

Layer rule: brain may import core and its own siblings, never board. Stdlib +
the sibling ``notes`` module only. Pure stdlib.
"""
import datetime
import subprocess
from pathlib import Path

from . import notes

REF_FOLDER = "references"
REF_TYPE = "reference-record"


# --------------------------------------------------------------------------- #
# org-brain-clone git helpers (local only — no network)
# --------------------------------------------------------------------------- #
def _clone(cfg):
    """The org-brain clone Path iff it is a git repo, else ``None``.

    Reads the canonical ``org_brain_clone`` key. This module used to read a
    retired alias ONLY, which meant a config migrated to the canonical key lost
    its org tier silently — no error, just reference records that stopped being
    stamped. ``brain.config`` always resolved both; this file bypassed it.
    """
    d = cfg.get("org_brain_clone")
    if d and (Path(d) / ".git").exists():
        return Path(d)
    return None


def org_brain_head(cfg):
    """Current HEAD sha of the org-brain clone, or ``None`` when it is absent."""
    clone = _clone(cfg)
    if not clone:
        return None
    try:
        r = subprocess.run(["git", "-C", str(clone), "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() or None if r.returncode == 0 else None


def is_dirty(cfg, org_node, org_rev):
    """True iff the org node's file has changed in the org-brain clone SINCE
    ``org_rev`` — i.e. ``org_rev`` is behind the clone HEAD for that file.

    Deterministic and local: ``git log <org_rev>..HEAD -- notes/<slug>.md``. When
    the clone is absent, ``org_rev`` is empty, or ``org_rev`` is not a known
    commit (can't compare), we do NOT flag — dirtiness must be provable, not
    guessed."""
    clone = _clone(cfg)
    if not clone or not org_rev:
        return False
    rel = f"notes/{org_node}.md"
    try:
        r = subprocess.run(
            ["git", "-C", str(clone), "log", "--oneline", f"{org_rev}..HEAD", "--", rel],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    if r.returncode != 0:
        return False  # unknown/invalid rev — unprovable, so not flagged
    return bool(r.stdout.strip())


# --------------------------------------------------------------------------- #
# stub shape (shared with the promote pipeline's collapse-to-reference)
# --------------------------------------------------------------------------- #
def stub_frontmatter(org_node, org_rev, tasks, fetched, *, provenance=None):
    """The reference-record frontmatter dict. ``provenance`` is carried only by
    the collapse path (a promoted node keeps its provenance); a plain ``ref add``
    omits it."""
    fm = {
        "name": f"ref-{org_node}",
        "type": REF_TYPE,
        "org_node": org_node,
        "org_rev": org_rev or "",
        "tasks": list(tasks),
        "fetched": fetched,
    }
    if provenance:
        fm["provenance"] = list(provenance)
    return fm


def stub_body(cfg, org_node):
    """The one-line body — a pointer at the canonical org node, never a copy."""
    label = cfg.get("org_label") or "org"
    return f"→ [[{org_node}]] — canonical {label} node (do not copy its content here)."


# --------------------------------------------------------------------------- #
# ref add / list
# --------------------------------------------------------------------------- #
def _ref_path(cfg, org_node):
    return Path(cfg["vault"]) / REF_FOLDER / f"{org_node}.md"


def ref_add(cfg, org_node, task, *, today=None, org_rev=None, commit=True):
    """Create or update the reference stub for ``org_node``, recording ``task``.

    Idempotent: a re-fetch appends the task handle (deduped, order-preserved) and
    bumps ``org_rev``/``fetched`` to now. Returns the written Path."""
    notes.validate_slug(org_node)
    day = today or datetime.date.today().isoformat()
    if org_rev is None:
        org_rev = org_brain_head(cfg) or ""

    path = _ref_path(cfg, org_node)
    tasks, provenance = [], None
    if path.exists():
        old_fm, _ = notes.parse_note(path.read_text(errors="ignore"))
        prev = old_fm.get("tasks")
        tasks = list(prev) if isinstance(prev, list) else []
        prov = old_fm.get("provenance")
        provenance = list(prov) if isinstance(prov, list) else None
    if task and task not in tasks:
        tasks.append(task)

    fm = stub_frontmatter(org_node, org_rev, tasks, day, provenance=provenance)
    return notes.write_note_fm(
        cfg["vault"], org_node, fm, stub_body(cfg, org_node),
        folder=REF_FOLDER, source="ref", op="update" if path.exists() else "create",
        commit=commit)


def ref_refresh(cfg, org_node, *, today=None, commit=True):
    """Re-fetch a reference stub: bump its ``org_rev`` to the clone's current HEAD
    and ``fetched`` to now, leaving ``tasks``/``provenance`` intact. This is what
    a task owner runs after a subscription memo tells them their reference is
    behind — it clears the dirty flag (``org_rev`` no longer trails HEAD for the
    node file). Raises ``NoteIOError`` if there is no stub to refresh."""
    notes.validate_slug(org_node)
    path = _ref_path(cfg, org_node)
    if not path.exists():
        raise notes.NoteIOError(f"no reference stub for {org_node!r} (nothing to refresh)")
    old_fm, _ = notes.parse_note(path.read_text(errors="ignore"))
    prev = old_fm.get("tasks")
    tasks = list(prev) if isinstance(prev, list) else []
    prov = old_fm.get("provenance")
    provenance = list(prov) if isinstance(prov, list) else None

    day = today or datetime.date.today().isoformat()
    org_rev = org_brain_head(cfg) or ""
    fm = stub_frontmatter(org_node, org_rev, tasks, day, provenance=provenance)
    return notes.write_note_fm(
        cfg["vault"], org_node, fm, stub_body(cfg, org_node),
        folder=REF_FOLDER, source="ref", op="update", commit=commit)


def _all_refs(cfg):
    d = Path(cfg["vault"]) / REF_FOLDER
    if not d.exists():
        return
    for f in sorted(d.glob("*.md")):
        fm, _ = notes.parse_note(f.read_text(errors="ignore"))
        if fm.get("type") != REF_TYPE:
            continue
        yield f, fm


def ref_list(cfg, *, task=None, node=None, dirty=None):
    """List reference records, filtered by either direction of the wire.

    ``task`` → refs whose ``tasks`` include that handle; ``node`` → the ref for
    that org node; ``dirty`` → only refs whose org node moved on since fetch.
    Returns ``[{org_node, path, tasks, org_rev, fetched, dirty}]``."""
    out = []
    for f, fm in _all_refs(cfg):
        org_node = fm.get("org_node") or f.stem
        tasks = fm.get("tasks") if isinstance(fm.get("tasks"), list) else []
        if node is not None and org_node != node:
            continue
        if task is not None and task not in tasks:
            continue
        d = is_dirty(cfg, org_node, fm.get("org_rev") or "")
        if dirty and not d:
            continue
        out.append({"org_node": org_node, "path": str(f), "tasks": tasks,
                    "org_rev": fm.get("org_rev") or "", "fetched": fm.get("fetched") or "",
                    "dirty": d})
    return out
