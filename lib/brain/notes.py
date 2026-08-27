"""brain-station — the single write path for vault notes.

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 2) from the brain source tree's
``scripts/note_io.py`` @ 0.14.0. That file did two jobs; chunk 1 split the
org-agnostic frontmatter SYNTAX half out to ``lib/core/frontmatter.py`` and left
everything that knows what a *note* is — which is this module.

Every mutation of a vault note MUST go through :func:`write_note`. Nothing else
in the brain plane is permitted to ``path.write_text`` a note. (A ``raw/`` drop
is not a note: it is an untrusted, un-schema'd capture that the heal pass reads
and deletes — ``brain.distill`` writes those directly by design, as do the
ingest INDEX.md line and the heal pass's ``reports/health/`` + LOG.md output.
The guarantee covers schema'd notes.) Centralising
writes here buys five guarantees the scattered writers could not:

  (a) slug validation + path containment — a slug is ``^[a-z0-9][a-z0-9-]{1,80}$``
      AND the resolved file must stay under ``<vault>/<folder>`` (notes or raw).
      This kills the path-traversal that the old ``VAULT / "notes" / f"{slug}.md"``
      concatenation allowed (``slug='../../etc/x'``).
  (b) a YAML-safe frontmatter emitter paired with a strict quote-aware
      mini-parser — round-trippable, so the class of "unquoted ': ' in a
      description broke the note" bugs cannot recur. That pair now lives in
      ``core.frontmatter``; this module owns the note KEY ORDER it is emitted in
      (see :data:`_FM_ORDER` and the two wrappers below).
  (c) explicit update modes — ``create`` (fail if exists), ``append`` (dated
      bullet under ``## Updates`` + frontmatter merge; the DEFAULT for updates),
      ``merge`` (frontmatter merge + named-section replace) and ``replace``
      (explicit destructive opt-in). The old silent full-body replace is gone.
  (d) trust integrity — an ``actor='agent'`` write can never raise ``verified:``
      or ``verified-by:`` on a note a human verified.
  (e) git-commit-at-write — each successful write commits in the vault repo as
      ``note: <op> · <slug> · <source>``; a commit that fails is a loud error.
  (f) the knowledge stamp — ``area:``/``plane:`` have a canonical slot in the
      frontmatter order and a single derivation (:func:`knowledge_stamp`) that
      REFUSES rather than writing a node the org schema would reject.
  (g) the two share switches — ``publish:`` and ``promote:``, both defaulting
      OFF and read by exactly one function (:func:`switch`). A note with neither
      field stays in the private vault; ``publish: true`` puts it in the owner's
      shared mirror, ``promote: true`` makes it a candidate for the org brain.
      The two are INDEPENDENT — neither implies the other — and a fresh note is
      written with NEITHER field, because a clean note is a private note and the
      point is that there is no field to remember.

KEY ORDER IS THE ONE SILENT CONTRACT. ``core.frontmatter`` takes the order as a
parameter because order is a schema decision and core owns no schema. So this
module never calls the core emitters directly: :func:`dump_frontmatter` and
:func:`render_note` below are order-bound wrappers that pass
:data:`_FM_ORDER` on every emit. A bare ``core.frontmatter.render_note(fm, body)``
would emit insertion order and silently reshuffle the frontmatter of every note
written — the wrappers exist so that mistake is not reachable from here.

The rest of the ``core.frontmatter`` surface (``parse_note``, ``emit_scalar``,
``parse_scalar``, ``emit_value``, ``parse_value``) is re-exported below, so this
module remains the one import a note reader/writer needs.

Layer rule: brain may import core and its own siblings, never board. Stdlib +
``core.frontmatter`` (+ a function-local ``naming``) only.

Pure stdlib, Python 3.9+.
"""
import datetime
import re
import subprocess
from pathlib import Path

import core.frontmatter as _frontmatter

SLUG_RX = re.compile(r"^[a-z0-9][a-z0-9-]{1,80}$")

# Folders write_note is allowed to target — the vault content tree plus raw/, the
# health report subdir, and references/ (reference-record stubs). notes/ is the
# curated default; raw/ is untrusted drops (auto-distill); reports/health holds
# deterministic-tool reports. Every target is containment-checked, so the
# traversal fix holds for all of them.
WRITABLE_FOLDERS = ("notes", "projects", "reports", "reports/health", "plans",
                    "raw", "references")

# Canonical frontmatter key order; unknown keys are emitted after, in order seen.
# The knowledge fields (tags/contributors/provenance) and the reference-record
# fields (org_node/org_rev/tasks/fetched) slot in ahead of any legacy tail.
# ``area``/``plane`` sit directly after ``description`` to match the org schema's
# order — every knowledge node carries both (see :func:`knowledge_stamp`), and a
# reader scanning the top of a file should see WHAT it is about before HOW it is
# typed. ``converged-with``/``distinct-from`` record a graded merge-target
# decision so the next session does not re-litigate it.
_FM_ORDER = ["name", "description", "area", "plane", "type", "publish", "promote",
             "verified", "verified-by", "source", "tags", "contributors", "provenance",
             "converged-with", "distinct-from",
             "org_node", "org_rev", "tasks", "fetched", "org_brain"]

# Folders whose files ARE knowledge nodes, so they carry area: + plane:. reports/
# plans/ raw/ references/ hold dated artifacts, undigested capture and org
# pointers — not standing claims — and are stamped with neither.
KNOWLEDGE_FOLDERS = ("notes", "projects")
KNOWLEDGE_PLANE = "knowledge"

# Frontmatter keys whose value is a LIST OF STRINGS (flat, overlapping) — tags
# (departments AND teams; never nested), provenance (task handles), and the
# reference-record task-handle list.
_STR_LIST_FIELDS = ("tags", "provenance", "tasks")
# ...and the one key whose value is a LIST OF RECORDS: contributor stamps
# ``{alias, ts, extent}`` (extent ∈ created|major|minor). See :func:`_validate_fields`.
_RECORD_LIST_FIELD = "contributors"
_EXTENTS = ("created", "major", "minor")


class NoteIOError(Exception):
    """Any refusal or failure from the write path (validation, trust, commit)."""


# --------------------------------------------------------------------------- #
# frontmatter syntax — re-exported from core, with the note key order bound on
# --------------------------------------------------------------------------- #
emit_scalar = _frontmatter.emit_scalar
parse_scalar = _frontmatter.parse_scalar
emit_value = _frontmatter.emit_value
parse_value = _frontmatter.parse_value
parse_note = _frontmatter.parse_note


def dump_frontmatter(fm):
    """Render a note's frontmatter dict to the ``---\\n...\\n---\\n`` block, in the
    canonical note key order (:data:`_FM_ORDER`)."""
    return _frontmatter.dump_frontmatter(fm, order=_FM_ORDER)


def render_note(fm, body):
    """Frontmatter block + a blank line + ``body``, in the canonical note key
    order (:data:`_FM_ORDER`)."""
    return _frontmatter.render_note(fm, body, order=_FM_ORDER)


# --------------------------------------------------------------------------- #
# the two share switches — publish: / promote:
# --------------------------------------------------------------------------- #
SWITCHES = ("publish", "promote")
_TRUE = "true"


def switch(fm, key):
    """Read one of the two share switches (:data:`SWITCHES`) off a frontmatter
    dict. THE one reader — publish, promote and tier-lint all come here.

    Both switches default OFF: an ABSENT field is False, and only a literal
    ``true`` (any case, quoted or not) is True. Anything else — ``yes``, ``1``,
    ``maybe``, a typo — is False. Never guess and never warn-and-enable: for a
    field that decides who can read a note, the safe answer to "I don't
    recognise this value" is "do not share it"."""
    v = fm.get(key)
    if isinstance(v, bool):
        return v
    return str("" if v is None else v).strip().strip('"').strip("'").lower() == _TRUE


def _apply_switches(fm, publish, promote):
    """Merge the two switches into ``fm``, ONLY where the caller said something.

    ``None`` (the default) leaves the field exactly as it was. A true value
    writes the literal ``true``. A FALSE value REMOVES the field rather than
    recording ``publish: false`` — absent already means off, and "no field to
    remember" is the whole point of the design."""
    for key, val in (("publish", publish), ("promote", promote)):
        if val is None:
            continue
        if val:
            fm[key] = _TRUE
        else:
            fm.pop(key, None)
    return fm


# --------------------------------------------------------------------------- #
# slug / path safety
# --------------------------------------------------------------------------- #
def validate_slug(slug):
    """Return ``slug`` if it matches ``^[a-z0-9][a-z0-9-]{1,80}$``, else raise.

    The regex alone forbids ``/``, ``.`` and ``..`` so no slug can express a
    path segment — but :func:`resolve_note_path` re-checks containment anyway.
    """
    if not isinstance(slug, str) or not SLUG_RX.match(slug):
        raise NoteIOError(
            f"invalid slug {slug!r}: must match ^[a-z0-9][a-z0-9-]{{1,80}}$ "
            "(lowercase, digits, hyphens; no slashes or dots)"
        )
    return slug


def resolve_note_path(vault, slug, folder="notes"):
    """Resolve ``<vault>/<folder>/<slug>.md`` and refuse anything that escapes
    ``<vault>/<folder>``. ``folder`` must be one of :data:`WRITABLE_FOLDERS`."""
    validate_slug(slug)
    if folder not in WRITABLE_FOLDERS:
        raise NoteIOError(f"refusing to write to folder {folder!r}; allowed: {WRITABLE_FOLDERS}")
    root = (Path(vault) / folder).resolve()
    path = (root / f"{slug}.md").resolve()
    if root not in path.parents:  # belt-and-suspenders behind validate_slug
        raise NoteIOError(f"path traversal blocked: {slug!r} resolves outside {root}")
    return path


# --------------------------------------------------------------------------- #
# body-section helpers (for append / merge)
# --------------------------------------------------------------------------- #
_SECTION_RX = r"^##[ \t]+"


def _append_updates_bullet(body, day, text):
    """Append a dated bullet under a ``## Updates`` section, creating the section
    at the end if absent. Never touches existing body content."""
    text = (text or "").strip()
    first, *rest = text.split("\n") if text else [""]
    bullet = f"- {day}: {first}"
    if rest:
        bullet += "\n" + "\n".join("  " + r for r in rest)
    body = body.rstrip("\n")
    if re.search(_SECTION_RX + r"Updates\b", body, re.M):
        return body + "\n" + bullet + "\n"
    sep = "\n\n" if body else ""
    return body + sep + "## Updates\n\n" + bullet + "\n"


def _replace_section(body, section, content):
    """Replace the ``## <section>`` block with ``content`` (heading kept),
    appending the section if it is absent. Other sections are untouched."""
    content = (content or "").strip()
    block = f"## {section}\n\n{content}\n" if content else f"## {section}\n"
    pat = re.compile(rf"(?m)^##[ \t]+{re.escape(section)}[ \t]*$.*?(?=^##[ \t]|\Z)", re.S)
    if pat.search(body):
        return pat.sub(block.rstrip("\n") + "\n", body, count=1)
    body = body.rstrip("\n")
    sep = "\n\n" if body else ""
    return body + sep + block


# --------------------------------------------------------------------------- #
# knowledge-field validation (typed, YAML-safe)
# --------------------------------------------------------------------------- #
def _validate_fields(fm):
    """Type-check the structured knowledge/reference fields present in ``fm``.

    Raises :class:`NoteIOError` on a shape violation so a malformed contributor
    record or a non-list ``tags`` can never be written. Absent fields are fine
    (the schema is additive — a legacy note carries none of them)."""
    for key in _STR_LIST_FIELDS:
        if key in fm:
            v = fm[key]
            if not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
                raise NoteIOError(f"{key!r} must be a list of strings, got {v!r}")
    if _RECORD_LIST_FIELD in fm:
        v = fm[_RECORD_LIST_FIELD]
        if not isinstance(v, list):
            raise NoteIOError(f"'contributors' must be a list, got {v!r}")
        for rec in v:
            if not isinstance(rec, dict):
                raise NoteIOError(f"contributor must be a mapping, got {rec!r}")
            if not isinstance(rec.get("alias"), str) or not rec["alias"]:
                raise NoteIOError(f"contributor needs a non-empty 'alias': {rec!r}")
            if not isinstance(rec.get("ts"), str) or not rec["ts"]:
                raise NoteIOError(f"contributor needs a 'ts': {rec!r}")
            if rec.get("extent") not in _EXTENTS:
                raise NoteIOError(f"contributor 'extent' must be one of {_EXTENTS}: {rec!r}")
    return fm


def contributor(alias, ts, extent="minor"):
    """Build one validated contributor stamp ``{alias, ts, extent}``."""
    rec = {"alias": alias, "ts": ts, "extent": extent}
    _validate_fields({"contributors": [rec]})
    return rec


def merge_contributor(contributors, alias, ts, extent="minor"):
    """Return ``contributors`` with ``alias``'s stamp folded in, cumulatively.

    A first appearance is appended (extent preserved — ``created`` on a CREATE);
    a returning contributor keeps their ORIGINAL ``created`` extent if they had
    it (creation is a one-time fact) but has ``ts`` refreshed and the extent
    raised to ``major`` when this pass is major. This is the promote/heal
    contributor semantic, kept next to the schema it stamps."""
    out = [dict(c) for c in (contributors or [])]
    for c in out:
        if c.get("alias") == alias:
            c["ts"] = ts
            if c.get("extent") != "created" and extent == "major":
                c["extent"] = "major"
            _validate_fields({"contributors": out})
            return out
    out.append(contributor(alias, ts, extent))
    return out


# --------------------------------------------------------------------------- #
# area: / plane: — the stamp every knowledge node must carry
# --------------------------------------------------------------------------- #
def knowledge_stamp(slug, folder="notes", *, contract=None, area=None, plane=None):
    """The ``{"area": …, "plane": …}`` a knowledge node is created with.

    ``area`` is DERIVED from the slug's domain (``naming.area_for``) rather than
    asked for, because the domain already determines it — asking twice is how the
    two drift apart. ``plane`` is ``knowledge`` for the node folders.

    A slug whose domain is unregistered has NO area, and an unstamped node is
    exactly what the org schema forbids, so this raises rather than writing one.
    That is the same ERROR-severity condition ``naming.slug_findings`` reports,
    and the message carries the same helpful fix — the nearest registered domain.
    Note this refuses for ``projects/`` too, which the slug-shape gate does not
    cover: ``domainRequiredIn`` lists the folders whose NAMES must carry a domain,
    while this is about the FIELD every knowledge node must carry.

    Returns ``{}`` for a non-knowledge folder (nothing to stamp), honouring an
    explicit ``area``/``plane`` there if the caller insists on one.
    """
    if folder not in KNOWLEDGE_FOLDERS:
        return {k: v for k, v in (("area", area), ("plane", plane)) if v is not None}

    from . import naming  # local import: notes stays importable without the contract

    contract = contract if contract is not None else naming.load_contract()
    resolved = area or naming.area_for(slug, contract)
    if not resolved:
        # reuse the contract's own refusal text so the CLI and the linter agree
        f = next((x for x in naming.slug_findings(slug, "notes", contract)
                  if x["check"] == "unregistered-domain"), None)
        detail = f["detail"] if f else "%r has no registered domain" % slug
        fix = ("; " + f["fix"]) if (f and f.get("fix")) else ""
        raise NoteIOError(
            "cannot derive area: for %r — %s%s. Every knowledge node carries "
            "area: and plane:; refusing to write an unstamped node."
            % (slug, detail, fix))
    return {"area": resolved, "plane": plane or KNOWLEDGE_PLANE}


# --------------------------------------------------------------------------- #
# trust integrity
# --------------------------------------------------------------------------- #
def _is_human_verified(fm):
    vb = (fm.get("verified-by") or "").strip().lower()
    return vb not in ("", "agent")


# --------------------------------------------------------------------------- #
# git commit at write
# --------------------------------------------------------------------------- #
def _git_commit(vault, path, op, slug, source):
    """Commit exactly ``path`` in the vault repo. Raises :class:`NoteIOError`
    on any git failure. No-op (returns None) when the vault is not a git repo —
    fresh vaults before ``git init`` and test fixtures simply have nothing to
    commit; a *present* repo that refuses the commit is the loud case."""
    if not (Path(vault) / ".git").exists():
        return None
    try:
        rel = str(Path(path).resolve().relative_to(Path(vault).resolve()))
    except ValueError:
        return None  # path is outside this repo — nothing to commit here
    msg = f"note: {op} · {slug} · {source}"
    add = subprocess.run(["git", "-C", str(vault), "add", "--", rel],
                         capture_output=True, text=True)
    if add.returncode != 0:
        raise NoteIOError(f"git add failed for {rel}: {add.stderr.strip()}")
    commit = subprocess.run(["git", "-C", str(vault), "commit", "-m", msg, "--", rel],
                            capture_output=True, text=True)
    if commit.returncode != 0:
        out = (commit.stderr or "") + (commit.stdout or "")
        if "nothing to commit" in out or "nothing added to commit" in out:
            return None  # byte-identical rewrite — a no-op, not a failure
        raise NoteIOError(
            f"git commit failed for {rel}: {out.strip()}"
        )
    return msg


# --------------------------------------------------------------------------- #
# the write path
# --------------------------------------------------------------------------- #
def _apply_stamp(fm, area, plane):
    """Set ``area:``/``plane:`` when supplied. Absent ⇒ leave whatever is there,
    so a legacy caller never strips an existing stamp."""
    if area is not None:
        fm["area"] = area
    if plane is not None:
        fm["plane"] = plane
    return fm


def _apply_fields(fm, *, name=None, tags=None, contributors=None,
                  provenance=None, extra=None):
    """Merge the additive knowledge/reference fields into ``fm`` (only when
    supplied), then type-check the whole result. ``extra`` carries
    reference-record keys (org_node/org_rev/tasks/fetched) and any other explicit
    frontmatter."""
    if name is not None:
        fm["name"] = name
    # Do NOT coerce with list()/dict() — that would silently turn a mis-typed
    # scalar (e.g. tags="a,b") into a char/pair list. Copy only genuine
    # collections; hand anything else straight to _validate_fields to reject.
    if tags is not None:
        fm["tags"] = list(tags) if isinstance(tags, (list, tuple)) else tags
    if contributors is not None:
        fm["contributors"] = ([dict(c) if isinstance(c, dict) else c for c in contributors]
                              if isinstance(contributors, (list, tuple)) else contributors)
    if provenance is not None:
        fm["provenance"] = list(provenance) if isinstance(provenance, (list, tuple)) else provenance
    if extra:
        for k, v in extra.items():
            fm[k] = v
    _validate_fields(fm)
    return fm


def write_note(vault, slug, *, mode="create", body="", description=None,
               type=None, publish=None, promote=None, source="manual", folder="notes",
               actor="agent", section=None, verified_by=None,
               commit=True, today=None, name=None, tags=None,
               contributors=None, provenance=None, extra=None,
               area=None, plane=None):
    """Create or update a vault note through the single sanctioned path.

    Parameters
    ----------
    mode : "create" | "append" | "merge" | "replace"
        create  — fail if the note exists.
        append  — merge frontmatter, add a dated bullet under ``## Updates``
                  (``body`` is the update text). DEFAULT for updates.
        merge   — merge frontmatter, replace the ``## <section>`` block with
                  ``body`` (``section`` required).
        replace — merge frontmatter, replace the whole body with ``body``.
                  Destructive; explicit opt-in only.
    actor : "agent" | "human"
        An ``agent`` write may not raise ``verified:``/``verified-by:`` on a
        human-verified note (see (d)).
    publish, promote : bool, optional
        The two independent share switches (see (g)). ``None`` — the default —
        writes nothing and leaves an existing value alone, so a new note gets
        NEITHER field and stays private. ``True`` writes the literal ``true``;
        ``False`` removes the field (absent already means off).
    area, plane : str, optional
        Stamped verbatim when given (both create and update). They are NOT
        derived here — :func:`knowledge_stamp` derives them and refuses when it
        cannot, and the deliberate-creation path (the search CLI's ``new``)
        calls it. A writer that passes neither leaves an existing note's stamp
        untouched, so this stays additive for every legacy caller.

    Returns the written ``Path``.
    """
    path = resolve_note_path(vault, slug, folder)
    day = today or datetime.date.today().isoformat()
    exists = path.exists()

    if mode == "create" and exists:
        raise NoteIOError(f"exists: {path} (update-don't-duplicate; use mode='append')")

    if not exists:
        # append/merge/replace on a missing note degrade to a fresh create so the
        # caller never has to special-case "does it exist yet".
        # NEITHER switch is written here on purpose: a clean note is a private
        # note, and the caller has to say `publish=True` to change that.
        fm = {
            "name": slug,
            "description": description if description is not None else "",
            "type": type or "reference",
            "verified": day,
            "source": source,
        }
        _apply_switches(fm, publish, promote)
        if actor == "human":
            fm["verified-by"] = verified_by or "human"
        elif verified_by:
            fm["verified-by"] = verified_by
        _apply_stamp(fm, area, plane)
        _apply_fields(fm, name=name, tags=tags, contributors=contributors,
                      provenance=provenance, extra=extra)
        text = render_note(fm, body if mode != "append" else "")
        if mode == "append" and (body or "").strip():
            _, b = parse_note(text)
            text = render_note(fm, _append_updates_bullet(b, day, body))
        op = "create"
    else:
        old_text = path.read_text(errors="ignore")
        fm, old_body = parse_note(old_text)
        human = _is_human_verified(fm)

        # --- frontmatter merge (type/switches/source/description honoured) ----
        if description is not None:
            fm["description"] = description
        if type is not None:
            fm["type"] = type
        _apply_switches(fm, publish, promote)
        if source is not None:
            fm["source"] = source
        _apply_stamp(fm, area, plane)
        fm.setdefault("name", slug)

        # --- trust integrity: agent cannot raise trust on a human note --------
        if actor == "agent" and human:
            pass  # leave verified: / verified-by: exactly as the human left them
        else:
            fm["verified"] = day
            if verified_by is not None:
                fm["verified-by"] = verified_by
            elif actor == "human":
                fm["verified-by"] = "human"

        # --- body mutation per mode -------------------------------------------
        if mode == "append":
            new_body = _append_updates_bullet(old_body, day, body)
        elif mode == "merge":
            if not section:
                raise NoteIOError("mode='merge' requires a section name")
            new_body = _replace_section(old_body, section, body)
        elif mode == "replace":
            new_body = body or ""
        else:
            raise NoteIOError(f"unknown mode {mode!r}")
        _apply_fields(fm, name=name, tags=tags, contributors=contributors,
                      provenance=provenance, extra=extra)
        text = render_note(fm, new_body)
        op = mode

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if commit:
        _git_commit(vault, path, op, slug, source)
    return path


def write_note_fm(repo, slug, fm, body="", *, folder="notes", source="promote",
                  op="create", commit=True):
    """Write a note with an EXPLICIT frontmatter dict — no auto publish/promote/
    source/verified injection; the caller owns the schema. Same slug + containment
    safety, YAML-safe emit, structured-field validation, and git-commit-at-write
    as :func:`write_note`.

    Used by the promote pipeline to land an org brain-schema node in the org clone
    (``name/description/type/verified`` only, personal keys stripped) and by the
    reference wire to stamp reference-record stubs whose schema is not the note
    schema. It is a sanctioned sibling of :func:`write_note` living in the ONE
    write module — not a parallel writer scattered across the codebase."""
    path = resolve_note_path(repo, slug, folder)
    fm = dict(fm)
    fm.setdefault("name", slug)
    _validate_fields(fm)
    text = render_note(fm, body)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if commit:
        _git_commit(repo, path, op, slug, source)
    return path


def write_memory_note(memory_dir, slug, *, description="", body="", mtype="reference",
                      source=None, vault=None, commit=True, overwrite=False):
    """Write a harness-shaped memory note (``metadata: type:`` block) into the
    memory store. Same safety as :func:`write_note` — validated slug, containment
    under ``memory_dir``, and an optional git commit (only when ``vault`` is given
    and the file lands inside that repo). Used by the tier-lint note→memory refile.

    The memory frontmatter schema differs from a vault note's, so this is a
    sibling writer rather than a ``write_note`` mode — but it lives in the same
    single-write-path module.
    """
    validate_slug(slug)
    root = Path(memory_dir).resolve()
    path = (root / f"{slug}.md").resolve()
    if root not in path.parents:
        raise NoteIOError(f"path traversal blocked: {slug!r} resolves outside {root}")
    if path.exists() and not overwrite:
        raise NoteIOError(f"exists: {path} (pass overwrite=True to replace)")
    fm_lines = ["---", f"name: {slug}", f"description: {emit_scalar(description)}",
                "metadata:", f"  type: {mtype}"]
    if source:
        fm_lines.append(f"  source: {emit_scalar(source)}")
    fm_lines.append("---")
    b = (body or "").rstrip("\n")
    text = "\n".join(fm_lines) + "\n\n" + (b + "\n" if b else "")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if commit and vault is not None:
        _git_commit(vault, path, "create", slug, source or "memory")
    return path
