"""brain-station — artifact ingest (glossary + brief_path -> vault note).

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 3) from the brain source tree's
``scripts/ingest_artifacts.py`` @ 0.14.0. Behaviour is unchanged; the module was
renamed (``ingest_artifacts`` -> ``brain.ingest``), the imports are relative
siblings, and the CLI's program name / message prefix follow the new name.

A task-station consumer step (wired into ``/brain-heal`` after tier-lint). It
scans recent CLOSED tasks — via the episodic adapter (``brain.episodic``), the
ONE episodic access point — for the two durable artifacts a finished task leaves
behind:

  * a **brief_path** — a rendered deliverable (the task one-pager). We record a
    LINK to it as a file path; the artifact itself is NEVER copied or wikified
    into the vault (rendered deliverables live in the repo/artifact tier, not
    the wiki — see the routing spec).
  * a non-empty **glossary** — canonical vocabulary IS knowledge, so the terms
    are written into the note as a definition list.

For each qualifying task, exactly ONE vault note ``notes/task-<seq>-<slug>.md``
is created/updated through the single write path (``brain.notes``), with
``type: reference`` and ``source: task-station:<seq>``. Idempotent: a re-run
whose artifact link + glossary already match the note is a no-op; a genuine
change appends a dated update bullet (via ``mode='append'``) — it never
duplicates the note. A new note also gets an INDEX.md line.

Layer rule: brain may import core and its own siblings, never board. Stdlib +
``brain.config`` / ``brain.notes`` / ``brain.episodic`` only.

Pure stdlib, Python 3.9+.
"""
import argparse
import datetime
import re
import sys
from pathlib import Path

from . import config
from . import episodic
from . import notes


def _slugify(text, limit=60):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:limit].rstrip("-")


def note_slug(rec):
    """``task-<seq>-<title-slug>`` (falls back to a uuid stub when seq is absent),
    always a valid note slug."""
    ident = rec.get("seq")
    if ident in (None, ""):
        ident = str(rec.get("uuid") or "x")[:8]
    title_slug = _slugify(rec.get("title") or "")
    slug = f"task-{ident}" + (f"-{title_slug}" if title_slug else "")
    slug = _slugify(slug, limit=81)
    return slug or f"task-{ident}"


def _clean_glossary(rec):
    return [g for g in (rec.get("glossary") or []) if isinstance(g, dict) and g.get("name")]


def _glossary_inner(glossary):
    """The body of the ``## Glossary`` section: a markdown definition list."""
    lines = []
    for g in glossary:
        name = str(g.get("name") or "").strip()
        meta = [str(x) for x in (g.get("layer"), g.get("state")) if x]
        suffix = f" ({', '.join(meta)})" if meta else ""
        lines.append(f"{name}{suffix}")
        lines.append(f": {str(g.get('def') or '').strip()}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _artifact_inner(brief_path):
    """The body of the ``## Artifact`` section: a LINK, never a copy."""
    name = Path(brief_path).name or brief_path
    return (f"[{name}]({brief_path})\n\n"
            "> Link only — the rendered artifact is never copied into the vault.")


def build_body(rec):
    """The full note body for a task: a status/outcome line, the artifact link
    (if any), and the glossary definition list (if any)."""
    seq = rec.get("seq")
    title = rec.get("title") or f"task {seq}"
    status = rec.get("status") or "closed"
    outcome = f"**{title}** — status: {status}"
    if rec.get("closed"):
        outcome += f" · closed {rec['closed']}"
    parts = [outcome]
    if rec.get("brief_path"):
        parts.append("## Artifact\n\n" + _artifact_inner(rec["brief_path"]))
    glossary = _clean_glossary(rec)
    if glossary:
        parts.append("## Glossary\n\n" + _glossary_inner(glossary))
    return "\n\n".join(parts).rstrip() + "\n"


def _is_current(text, rec):
    """True when the existing note already reflects this task's artifact link and
    every glossary term — i.e. re-ingest would be a pure no-op."""
    brief = rec.get("brief_path")
    if brief and brief not in text:
        return False
    for g in _clean_glossary(rec):
        if str(g["name"]) not in text:
            return False
    return True


def _description(rec):
    seq = rec.get("seq")
    title = rec.get("title") or f"task {seq}"
    return f"task-station #{seq}: {title} ({rec.get('status') or 'closed'})"


def _add_index_line(vault, slug, desc):
    """Add a ``- [[slug]] — desc`` line to INDEX.md when the slug is new (vault
    INDEX convention; mirrors the tier-lint)."""
    idx = Path(vault) / "INDEX.md"
    line = f"- [[{slug}]] — {desc}"
    if not idx.exists():
        idx.write_text("# INDEX\n\n" + line + "\n")
        return
    text = idx.read_text(errors="ignore")
    if f"[[{slug}]]" in text:
        return
    sep = "" if text.endswith("\n") else "\n"
    idx.write_text(text + sep + line + "\n")


def run(cfg, days=30, commit=True, today=None):
    """Ingest artifacts from recent CLOSED tasks. Returns a summary dict."""
    day = today or datetime.date.today().isoformat()
    tasks = episodic.recent_tasks(days, cfg=cfg)
    if tasks is None:
        return {"status": "unavailable", "created": [], "updated": [], "skipped": 0}

    vault = cfg["vault"]
    created, updated, skipped = [], [], 0
    for rec in tasks:
        if not episodic.is_closed(rec):
            skipped += 1
            continue
        glossary = _clean_glossary(rec)
        if not rec.get("brief_path") and not glossary:
            skipped += 1
            continue

        slug = note_slug(rec)
        desc = _description(rec)
        seq = rec.get("seq")
        source = f"task-station:{seq if seq not in (None, '') else rec.get('uuid')}"
        path = notes.resolve_note_path(vault, slug, "notes")
        if path.exists():
            if _is_current(path.read_text(errors="ignore"), rec):
                skipped += 1
                continue
            # Artifact/glossary changed since last ingest. Refresh the sections
            # IN PLACE (mode='merge') rather than appending — this updates the one
            # note (never a duplicate) and is idempotent: a subsequent unchanged
            # run then reads as current and no-ops.
            if rec.get("brief_path"):
                notes.write_note(
                    vault, slug, mode="merge", section="Artifact",
                    body=_artifact_inner(rec["brief_path"]), folder="notes",
                    description=desc, source=source, actor="agent",
                    commit=commit, today=day)
            if glossary:
                notes.write_note(
                    vault, slug, mode="merge", section="Glossary",
                    body=_glossary_inner(glossary), folder="notes",
                    source=source, actor="agent", commit=commit, today=day)
            updated.append(slug)
        else:
            notes.write_note(
                vault, slug, mode="create", folder="notes",
                body=build_body(rec), description=desc, type="reference",
                source=source, actor="agent", commit=commit, today=day)
            _add_index_line(vault, slug, desc)
            created.append(slug)
    return {"status": "ok", "created": created, "updated": updated, "skipped": skipped}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="brain-ingest", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=30,
                    help="how far back to scan recent closed tasks (default 30)")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    config.require_valid()  # writes vault notes; refuse a broken config
    cfg = config.load()
    if not cfg["vault"].exists():
        if not a.quiet:
            print(f"brain-ingest: vault missing at {cfg['vault']} — run /brain-init")
        return 0
    result = run(cfg, days=a.days)
    if not a.quiet:
        if result["status"] == "unavailable":
            print("brain-ingest: episodic layer unavailable — no Tasktrail stream or "
                  "exported mirror (this is fine without task-station)")
        else:
            print(f"brain-ingest: {len(result['created'])} created, "
                  f"{len(result['updated'])} updated, {result['skipped']} skipped")
            for s in result["created"]:
                print(f"  + notes/{s}.md")
            for s in result["updated"]:
                print(f"  ~ notes/{s}.md")
    return 0


if __name__ == "__main__":  # pragma: no cover — Phase 5 owns the entry point
    sys.exit(main())
