"""brain-station — the promote / upload-reconcile pipeline.

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 4b) from the brain source tree's
``scripts/promote.py`` @ 0.14.0.

This module CODIFIES what used to live only as prose in the promote skill's
instructions — the #1 doc↔code drift source. A promote takes a personal knowledge
node and lands it in the org brain (the org-brain clone) as a PR, reconciling into
the existing org node when one already covers the subject instead of forking a
sibling.

Flow (``promote``):
  (a) gate — the note must be ``scope: team`` (or the caller explicitly opts a
      non-team note in with ``--non-team``);
  (b) merge-target detection — ``naming.find_target`` against the org-brain clone's
      ``notes/``: a hit ⇒ RECONCILE (body replaced, ``contributors[]`` kept
      cumulative, ``verified`` bumped — git IS the history); no hit ⇒ CREATE;
  (c) strip personal context — org brain core schema only (scope/source/org_brain/
      provenance dropped, local-only types remapped), home paths + session ids
      redacted, and the naming contract applied BY SEVERITY: warnings ride along
      on the result, an error (unregistered domain) gates the promote;
  (d) write to a branch in the clone, commit, and — via the selected forge
      ADAPTER only (``core.forge``; ado or github) — push + open the PR
      (org/owner/project/repo from config, never literals);
  (e) print the PR URL. When the clone is absent the fully-prepared note is
      QUEUED to ``notes/_org_brain-queue.md`` instead.

Collapse-to-reference (``promote --finalize <slug>``, after the PR merges): the
private node's body is replaced by a reference stub (org_rev = merged sha,
provenance/tasks carried over); the full private history stays in vault git.

All note writes go through ``brain.notes`` (single write path). Pure stdlib; the
only external calls are ``git`` and — inside the forge adapter — ``az`` / ``gh``.

Layer rule: brain may import core and its own siblings, never board. Stdlib +
``core.forge`` + the sibling ``config`` / ``naming`` / ``notes`` / ``references``
modules only. Python 3.9+.
"""
import argparse
import datetime
import re
import subprocess
import sys
from pathlib import Path

import core.forge as forge

from . import config
from . import naming
from . import notes
from . import references

# org brain core schema types; a local-only type (decision/hub/…) remaps to reference.
ORG_BRAIN_TYPES = ("how-to", "gotcha", "state", "architecture", "reference", "report")

# Redaction patterns (built via char classes so this file carries no home-path
# literal of its own — a shipped module must name none). Personal-context
# stripping is leak REMOVAL — deterministic; prose polish (first person, relative
# dates) stays a human/LLM pass before promote is run.
_HOME_ABS_RX = re.compile(r"/(?:Users|home)/[^\s)]+")
_UUID_RX = re.compile(r"\b[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\b")


class PromoteError(Exception):
    """A refusal or failure in the promote pipeline."""


# --------------------------------------------------------------------------- #
# strip personal context → org brain core schema
# --------------------------------------------------------------------------- #
def remap_type(t):
    return t if t in ORG_BRAIN_TYPES else "reference"


def strip_frontmatter(fm, *, verified, contributors, name=None):
    """Build the org brain-schema frontmatter from a private node's ``fm``.

    Keeps name/description/type/verified/tags and the cumulative contributor
    record; DROPS scope/source/org_brain/verified-by/provenance/org_* by simply not
    copying them. ``type`` is remapped into the org brain vocabulary."""
    out = {
        "name": name or fm.get("name"),
        "description": fm.get("description", "") or "",
        "type": remap_type((fm.get("type") or "reference").strip()),
        "verified": verified,
    }
    tags = fm.get("tags")
    if isinstance(tags, list) and tags:
        out["tags"] = tags
    out["contributors"] = contributors
    return out


def strip_body(body):
    """Redact the mechanizable leaks: home-directory absolute paths and
    session-id UUIDs. (Paths become a placeholder rather than a fabricated URL —
    the guarantee is "no leak", not "guessed link".)"""
    body = _HOME_ABS_RX.sub("<local-path>", body or "")
    body = _UUID_RX.sub("<session-id>", body)
    return body


# --------------------------------------------------------------------------- #
# config helpers
# --------------------------------------------------------------------------- #
def _alias(cfg):
    return cfg.get("alias") or cfg.get("owner") or "owner"


def _clone(cfg):
    d = cfg.get("org_brain_clone")
    return Path(d) if d and (Path(d) / ".git").exists() else None


# --------------------------------------------------------------------------- #
# private-note loading
# --------------------------------------------------------------------------- #
def _load_private(cfg, slug):
    for folder in ("notes", "projects"):
        p = Path(cfg["vault"]) / folder / f"{slug}.md"
        if p.exists():
            fm, body = notes.parse_note(p.read_text(errors="ignore"))
            return p, fm, body
    raise PromoteError(f"note {slug!r} not found under notes/ or projects/")


def _queue(cfg, slug, fm, body, day, action):
    """Append the fully-prepared org brain note to notes/_org_brain-queue.md (kept when
    the clone is absent). This is a queue log — appended directly, like LOG.md."""
    q = Path(cfg["vault"]) / "notes" / "_org_brain-queue.md"
    q.parent.mkdir(parents=True, exist_ok=True)
    prepared = notes.render_note(fm, body)
    header = "" if q.exists() else "# org brain promote queue\n\n"
    with open(q, "a") as fh:
        fh.write(f"{header}## Queued {day} · {action} · {slug}\n\n```md\n{prepared}```\n\n")
    return q


# --------------------------------------------------------------------------- #
# promote (CREATE / RECONCILE)
# --------------------------------------------------------------------------- #
def promote(cfg, slug, *, extent="minor", allow_non_team=False, dry_run=False,
            today=None):
    """Promote the private node ``slug`` into the org brain. Returns a result
    dict: ``{status, action, slug, branch, pr_url, clone, warnings, message}``.

    ``status`` ∈ gated | name-gated | queued | created | reconciled. ``pr_url`` is None when
    the forge is unconfigured or ``dry_run`` (the branch/commit are still made in
    the clone, ready for a human to push)."""
    day = today or datetime.date.today().isoformat()
    src_path, fm, body = _load_private(cfg, slug)

    # (a) gate
    scope = (fm.get("scope") or "personal").strip().lower()
    if scope != "team" and not allow_non_team:
        return {"status": "gated", "slug": slug, "pr_url": None,
                "message": f"{slug} is scope:{scope} — promote needs scope:team "
                           f"or an explicit --non-team opt-in"}

    alias = _alias(cfg)
    clone = _clone(cfg)
    body_stripped = strip_body(body)

    # (b) merge-target detection (only meaningful with a clone to search)
    target = None
    if clone:
        # The reconcile signal is the node identity: deterministic naming means the
        # same subject resolves to the same (or a near) slug. Probe with the
        # name/slug, not name+body (which would dilute the slug similarity).
        target = naming.find_target(fm.get("name") or slug, [clone / "notes"])
    action = "reconcile" if target else "create"
    target_slug = target["slug"] if target else slug

    # Naming findings on the slug about to land in the ORG brain, split by
    # severity. A warn rides along on the result (the PR reviewer sees it); an
    # ERROR — an unregistered domain, a reserved stem, illegal characters — gates
    # the promote exactly as scope: does. Gating here is not the "refusal makes
    # authors fake a name" case: the fact already has a private node, nothing is
    # lost, and the fix is a one-line PR to the registry in this very clone.
    contract = naming.load_contract(str(clone) if clone else None)
    findings = naming.slug_findings(target_slug, "notes", contract)
    warnings = ["%s: %s" % (f["check"], f["detail"])
                for f in findings if f["severity"] != "error"]
    if naming.has_error(findings):
        errors = ["%s: %s%s" % (f["check"], f["detail"],
                                (" — " + f["fix"]) if f.get("fix") else "")
                  for f in findings if f["severity"] == "error"]
        return {"status": "name-gated", "action": action, "slug": target_slug,
                "branch": None, "pr_url": None, "clone": str(clone) if clone else None,
                "warnings": warnings, "errors": errors,
                "message": f"{target_slug} cannot land in the org brain under that "
                           f"name — register the domain (schemas/node-types.json) "
                           f"or rename the node first"}

    # (c) build org brain frontmatter (contributors merged per action)
    if action == "reconcile":
        tgt_path = clone / "notes" / f"{target_slug}.md"
        tfm, _ = notes.parse_note(tgt_path.read_text(errors="ignore"))
        prev = tfm.get("contributors") if isinstance(tfm.get("contributors"), list) else []
        contributors = notes.merge_contributor(prev, alias, day, extent)
        name = tfm.get("name") or target_slug
    else:
        contributors = notes.merge_contributor([], alias, day, "created")
        name = fm.get("name") or slug
    org_brain_fm = strip_frontmatter(fm, verified=day, contributors=contributors, name=name)

    # queue fallback — no clone to land into
    if not clone:
        q = _queue(cfg, slug, org_brain_fm, body_stripped, day, action)
        _log(cfg, "promote", f"{slug} queued ({action}) — no org_brain clone")
        return {"status": "queued", "action": action, "slug": target_slug,
                "branch": None, "pr_url": None, "clone": None,
                "warnings": warnings, "message": f"queued to {q}"}

    # (d) write to a branch in the clone (single write path), then forge push/PR
    #     through the selected adapter (ado | github) — never a literal forge.
    adapter = forge.get_adapter(cfg)
    branch = f"promote-{target_slug}"
    base = cfg.get("forge_target_branch") or "main"
    forge.start_branch(clone, branch, base)
    notes.write_note_fm(
        clone, target_slug, org_brain_fm, body_stripped, folder="notes",
        source="promote", op=("replace" if action == "reconcile" else "create"))
    _update_clone_index(clone, target_slug, org_brain_fm.get("description", ""))

    pr_url = None
    if not dry_run and adapter.configured(cfg):
        adapter.push_branch(clone, branch, cfg)
        pr_url = adapter.open_pr(cfg, branch, f"promote: {target_slug}")
        if pr_url:
            _stamp_local_org_brain(cfg, src_path, slug, pr_url, day)

    _log(cfg, "promote", f"{slug} -> {target_slug} ({action}, branch {branch})")
    status = "reconciled" if action == "reconcile" else "created"
    return {"status": status, "action": action, "slug": target_slug, "branch": branch,
            "pr_url": pr_url, "clone": str(clone), "warnings": warnings,
            "message": ("dry-run — branch/commit ready, not pushed" if dry_run else
                        "PR opened" if pr_url else
                        "committed to branch; forge not configured — push manually")}


def _update_clone_index(clone, slug, description):
    """Best-effort: append an INDEX line in the clone for a node that isn't listed
    yet, and commit it. Never fatal — the org wiki owns its own index conventions."""
    idx = clone / "INDEX.md"
    if not idx.exists():
        return
    text = idx.read_text(errors="ignore")
    if f"[[{slug}]]" in text:
        return
    line = f"- [[{slug}]]" + (f" — {description}" if description else "")
    idx.write_text(text.rstrip("\n") + "\n" + line + "\n")
    subprocess.run(["git", "-C", str(clone), "add", "--", "INDEX.md"],
                   capture_output=True, text=True)
    subprocess.run(["git", "-C", str(clone), "commit", "-m", f"promote: index {slug}", "--", "INDEX.md"],
                   capture_output=True, text=True)


def _stamp_local_org_brain(cfg, src_path, slug, pr_url, day):
    """Record the PR URL on the LOCAL private note (the promote skill's
    record-the-PR step) via ``brain.notes``."""
    folder = src_path.parent.name
    notes.write_note(cfg["vault"], slug, mode="merge", section="org brain",
                     body=f"Promoted: {pr_url}", folder=folder, actor="agent",
                     extra={"org_brain": pr_url}, today=day)


# --------------------------------------------------------------------------- #
# collapse-to-reference (--finalize)
# --------------------------------------------------------------------------- #
def finalize(cfg, slug, *, org_node=None, org_rev=None, today=None):
    """Collapse a merged private node into a reference stub, in place.

    The body becomes a one-line pointer at the org node; frontmatter becomes the
    reference-record schema (org_node/org_rev/tasks/fetched) with provenance/tasks
    carried over from the private node. The full private history remains in vault
    git (this is a ``replace``, a new commit — nothing is destroyed)."""
    day = today or datetime.date.today().isoformat()
    src_path, fm, _ = _load_private(cfg, slug)
    org_node = org_node or slug
    if org_rev is None:
        org_rev = references.org_brain_head(cfg) or ""
    prov = fm.get("provenance") if isinstance(fm.get("provenance"), list) else []
    stub_fm = references.stub_frontmatter(org_node, org_rev, prov, day, provenance=prov or None)
    body = references.stub_body(cfg, org_node)
    folder = src_path.parent.name
    path = notes.write_note_fm(cfg["vault"], slug, stub_fm, body, folder=folder,
                               source="finalize", op="replace")
    _update_vault_index(cfg, slug, org_node)
    _log(cfg, "promote", f"{slug} finalized -> reference to {org_node} @ {org_rev[:8]}")
    return {"status": "finalized", "slug": slug, "org_node": org_node,
            "org_rev": org_rev, "path": str(path)}


def _update_vault_index(cfg, slug, org_node):
    """Best-effort: mark the vault INDEX line for ``slug`` as pointing at the org
    reference (the wikilink is unchanged, so it still resolves)."""
    idx = Path(cfg["vault"]) / "INDEX.md"
    if not idx.exists():
        return
    lines = idx.read_text(errors="ignore").splitlines()
    tag = "(→ org reference)"
    changed = False
    for i, ln in enumerate(lines):
        if f"[[{slug}]]" in ln and tag not in ln:
            lines[i] = ln.rstrip() + f"  {tag}"
            changed = True
    if changed:
        idx.write_text("\n".join(lines) + "\n")


# --------------------------------------------------------------------------- #
def _log(cfg, op, message):
    log = Path(cfg["vault"]) / "LOG.md"
    if not log.parent.exists():
        return
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(log, "a") as fh:
        fh.write(f"- {stamp} · {op} · {message}\n")


def _print(res):
    if res["status"] == "gated":
        print(f"brain-promote: GATED — {res['message']}")
        return
    if res["status"] == "name-gated":
        print(f"brain-promote: NAME-GATED — {res['message']}")
        for e in res.get("errors", []):
            print(f"  naming error: {e}")
        for w in res.get("warnings", []):
            print(f"  naming warning: {w}")
        return
    if res["status"] == "queued":
        print(f"brain-promote: queued ({res['action']}) — {res['message']}")
        return
    if res["status"] == "finalized":
        print(f"brain-promote: finalized {res['slug']} -> reference to {res['org_node']} "
              f"@ {res['org_rev'][:8]}")
        return
    print(f"brain-promote: {res['status']} — {res['slug']} on branch {res['branch']}")
    for w in res.get("warnings", []):
        print(f"  naming warning: {w}")
    if res.get("pr_url"):
        print(f"  PR: {res['pr_url']}")
    else:
        print(f"  {res['message']}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="brain-promote", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug", help="vault node slug to promote / finalize")
    ap.add_argument("--extent", choices=["minor", "major"], default="minor",
                    help="RECONCILE contributor extent (default minor)")
    ap.add_argument("--non-team", action="store_true",
                    help="opt a non-scope:team note into promotion (explicit gate override)")
    ap.add_argument("--dry-run", action="store_true",
                    help="build the branch/commit in the clone but do not push or open a PR")
    ap.add_argument("--finalize", action="store_true",
                    help="collapse a merged node into a reference stub (run after the PR merges)")
    ap.add_argument("--org-node", help="finalize: the org node slug (default: same slug)")
    a = ap.parse_args(argv)

    cfg = config.require_valid()  # a write path — refuse a silently-defaulted vault
    try:
        if a.finalize:
            res = finalize(cfg, a.slug, org_node=a.org_node)
        else:
            res = promote(cfg, a.slug, extent=a.extent, allow_non_team=a.non_team,
                          dry_run=a.dry_run)
    except (PromoteError, forge.ForgeError, notes.NoteIOError) as e:
        sys.exit(f"brain-promote: {e}")
    _print(res)
    return 0


if __name__ == "__main__":  # pragma: no cover — Phase 5 owns the entry point
    sys.exit(main())
