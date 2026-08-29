"""brain-station lint — deterministic vault health check. No LLM, no network.

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 4a) from the brain source tree's
``scripts/lint.py`` @ 0.14.0.

Checks: broken wikilinks, orphan notes, INDEX drift, frontmatter validity,
stale ``verified:`` dates, secret-looking strings, MEMORY.md index rot, memory
entries typed outside the feedback|user contract, and (informational)
reference records whose org node has moved on.

Exit 0 = clean, 1 = issues (report written to ``mirror/health/``). Paths
resolve at runtime via ``brain.config`` — nothing baked in at install time, and
nothing resolved at IMPORT time either: the source held the vault in module
globals filled by a ``pb_config.load()`` at import, which froze the config for
every importer (``publish`` imports this module only for :data:`SECRET_RX`).
Every path now arrives as the ``cfg`` argument.

:data:`SECRET_RX` is the one shared constant here — the promote/publish pipeline
reuses this exact pattern set so there is a single source of truth for "what
looks like a secret". Keep the name.

Layer rule: brain may import core and its own siblings, never board. Stdlib +
the sibling ``config`` / ``naming`` / ``references`` modules only. Python 3.9+.
"""
import argparse
import datetime
import platform
import re
import subprocess
import sys
from pathlib import Path

from . import config
from . import naming
from . import notes as _notes
from . import references

#: What the linter walks. Sourced from the one folder vocabulary (``brain.notes``)
#: rather than re-listed, so a vault's shape is defined in exactly one place.
#: inbox/ (and the pre-fold raw/) are untrusted capture and are NOT linted.
CONTENT_DIRS = _notes.CONTENT_FOLDERS
CAPTURE_DIRS = (_notes.INBOX_DIR, "raw")
#: Lint output quotes the issues it found verbatim, so linting it would report
#: every quoted problem a second time. Both spellings — a pre-fold vault keeps
#: its reports where it put them.
HEALTH_DIRS = (tuple(_notes.HEALTH_DIR.split("/")),
               tuple(_notes.LEGACY_HEALTH_DIR.split("/")))
LINK_RX = re.compile(r"\[\[([^\]\|#]+)")
CODE_RX = re.compile(r"```.*?```|`[^`\n]*`", re.S)
SECRET_RX = re.compile(
    r"((?:password|api.?key|access.?token|secret)\s*[=:]\s*['\"]?[A-Za-z0-9+/_-]{12,}"
    r"|ghp_[A-Za-z0-9]{20,}|sk-ant-[A-Za-z0-9-]{10,}|BEGIN [A-Z ]*PRIVATE KEY)",
    re.I,
)
DEFAULT_STALE_DAYS = 90


def all_note_basenames(vault, memory=None):
    names = {}
    for d in (*CONTENT_DIRS, *CAPTURE_DIRS, "task-station", "org-brain"):
        p = vault / d
        if p.exists():
            for f in p.rglob("*.md"):
                names[f.stem.lower()] = f
    # cfg["memory"] commonly resolves OUTSIDE the vault (#578) — fall back to the
    # legacy vault/memory nesting only when no memory path was given at all.
    mem_dir = memory if memory is not None else vault / "memory"
    if mem_dir.exists():
        for f in mem_dir.rglob("*.md"):
            names[f.stem.lower()] = f
    for f in (vault / "CLAUDE.md", vault / "INDEX.md", vault / "LOG.md"):
        if f.exists():
            names[f.stem.lower()] = f
    return names


def content_files(vault):
    for d in CONTENT_DIRS:
        p = vault / d
        if p.exists():
            yield from sorted(p.rglob("*.md"))


def frontmatter(path):
    text = path.read_text(errors="ignore")
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    fm = {}
    for line in parts[1].splitlines():
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip().strip('"')
    return fm, text


TYPE_RX = re.compile(r"(?m)^\s*type:[ \t]*(\S+)")


def memory_type(path):
    """A memory entry's ``type``, or ``None`` if missing/unreadable.

    ``frontmatter()`` only keeps column-0 keys, so it cannot see the harness
    shape's ``metadata:\\n  type: <t>`` — this matches ``type:`` at any
    indent instead, which also covers a vault-note-shaped file (top-level
    ``type: <t>``) living in memory/. Frontmatter block only, never the body.
    """
    text = path.read_text(errors="ignore")
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    m = TYPE_RX.search(parts[1])
    if not m:
        return None
    return m.group(1).strip().strip("\"'").lower()


def notify(total):
    """Best-effort desktop notification; never crashes, macOS-only."""
    if platform.system() != "Darwin":
        return
    try:
        subprocess.run([
            "osascript", "-e",
            f'display notification "{total} issues — run /brain-heal" with title "brain-station lint"',
        ], timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


def scan(cfg, stale_days=DEFAULT_STALE_DAYS, today=None):
    """Walk the vault + memory store and return ``(issues, info)``.

    ``issues`` is the COUNTED set (it drives the exit code); ``info`` is the
    warn tier — reported so a human can see it, never counted, because a
    refusal there would make an author drop the fact rather than fix the name.
    """
    vault = Path(cfg["vault"])
    memory = Path(cfg["memory"]) if cfg.get("memory") else None
    day = datetime.date.fromisoformat(today) if today else datetime.date.today()

    names = all_note_basenames(vault, memory=memory)
    contract = naming.load_contract(
        str(cfg["org_brain_clone"]) if cfg.get("org_brain_clone") else None)
    issues = {
        "broken-links": [], "orphans": [], "index-drift": [],
        "frontmatter": [], "naming": [], "stale": [], "secrets": [], "memory-rot": [],
        "memory-type": [],
    }
    # Naming findings are split by SEVERITY: an unregistered domain (or a reserved
    # stem / illegal character) is an error and counts toward the exit code; the
    # shape warnings stay informational, because a refusal makes an author drop
    # the fact rather than write a better name.
    info = {"memory-dangling": [], "naming": [], "reference-dirty": []}  # warn-tier: reported, never counted
    inbound = set()
    outbound = {}

    lintable = list(content_files(vault)) + (
        sorted(memory.glob("*.md")) if memory and memory.exists() else [])
    for f in lintable:
        rel = None
        try:
            rel = f.relative_to(vault)
        except ValueError:
            pass
        if rel and rel.parts[:2] in HEALTH_DIRS:
            continue  # lint output quotes issues verbatim; never lint it
        fm, text = frontmatter(f)
        prose = CODE_RX.sub("", text)  # links/secrets inside code blocks are examples
        links = [t.strip().lower() for t in LINK_RX.findall(prose)]
        links = [t[:-3] if t.endswith(".md") else t for t in links if "<" not in t and "{" not in t]
        outbound[f] = links
        in_memory = bool(memory) and memory in f.parents
        # A plan names artifacts that do not exist yet, by design, so its dangling
        # links are not findings. Post-fold a plan lives in docs/ alongside
        # reports (whose broken links ARE findings), so it declares itself with
        # ``type: plan``; the pre-fold plans/ folder still counts on its own.
        is_plan = (bool(rel) and rel.parts[0] == "plans") or fm.get("type") == "plan"
        for t in links:
            if t in names:
                inbound.add(t)
            elif in_memory:
                info["memory-dangling"].append(f"{f.name}: [[{t}]]")
            elif not is_plan:
                issues["broken-links"].append(f"{f.parent.name}/{f.name}: [[{t}]]")
        if rel and rel.parts[0] in _notes.KNOWLEDGE_FOLDERS and not f.stem.startswith("_"):
            if not fm.get("name"):
                issues["frontmatter"].append(f"{rel}: missing frontmatter/name")
            elif fm["name"] != f.stem:
                issues["frontmatter"].append(f"{rel}: name '{fm['name']}' != filename")
            for nf in naming.slug_findings(f.stem, rel.parts[0], contract):
                line = f"{rel}: {nf['check']}: {nf['detail']}"
                if nf.get("fix"):
                    line += f" — {nf['fix']}"
                bucket = issues if nf["severity"] == "error" else info
                bucket["naming"].append(line)
        for m in SECRET_RX.finditer(prose):
            issues["secrets"].append(f"{f}: …{m.group(0)[:40]}…")
        v = fm.get("verified") or fm.get("updated")
        if v and rel and rel.parts[0] == "notes":
            try:
                age = (day - datetime.date.fromisoformat(v[:10])).days
                if age > stale_days:
                    issues["stale"].append(f"{rel}: verified {v} ({age}d)")
            except ValueError:
                issues["frontmatter"].append(f"{rel}: bad verified date '{v}'")

    # orphans: notes/ files nobody links and that link nobody
    index_path = vault / "INDEX.md"
    index_text = index_path.read_text(errors="ignore").lower() if index_path.exists() else ""
    notes_dir = vault / "notes"
    for f in (notes_dir.rglob("*.md") if notes_dir.exists() else []):
        if f.stem.startswith("_"):
            continue
        if f.stem.lower() not in inbound and not outbound.get(f):
            issues["orphans"].append(str(f.relative_to(vault)))
        if f"[[{f.stem.lower()}]]" not in index_text:
            issues["index-drift"].append(f"notes/{f.name} not in INDEX.md")
    for t in LINK_RX.findall(index_text):
        if t.strip().lower() not in names:
            issues["index-drift"].append(f"INDEX.md lists dead [[{t.strip()}]]")

    # MEMORY.md rot: index lines pointing at missing files, and unindexed memory files
    mem_index = memory / "MEMORY.md" if memory else None
    if mem_index and mem_index.exists():
        listed = set(re.findall(r"\]\(([^)]+\.md)\)", mem_index.read_text(errors="ignore")))
        actual = {f.name for f in memory.glob("*.md")} - {"MEMORY.md"}
        for miss in sorted(listed - actual):
            issues["memory-rot"].append(f"MEMORY.md links missing file {miss}")
        for un in sorted(actual - listed):
            issues["memory-rot"].append(f"memory/{un} not in MEMORY.md index")

    # Memory typed outside the feedback|user contract: memory/ holds only
    # how-to-work-with-Ryan facts. A tombstone (tier-lint's re-file leftover)
    # is already handled — skip it rather than double-flag.
    if memory and memory.exists():
        for f in sorted(memory.glob("*.md")):
            if f.name == "MEMORY.md" or f.stem.startswith("_"):
                continue
            if "<!-- MOVED to " in f.read_text(errors="ignore"):
                continue
            t = memory_type(f) or "missing"
            if t not in ("feedback", "user"):
                issues["memory-type"].append(
                    f"memory/{f.name}: type '{t}' — memory holds only how-to-work-with-Ryan "
                    "facts (feedback|user); re-file it (a fact about a system -> a vault note "
                    "in notes/, a rule about one repo -> that repo's CLAUDE.md, a fact about "
                    "another person -> a vault note)")

    # Reference records whose org node moved on since fetch (org_rev behind the
    # org-brain clone HEAD for that file). Warn-tier: the memo feed consumes it.
    for r in references.ref_list(cfg, dirty=True):
        info["reference-dirty"].append(
            f"references/{r['org_node']}.md: org node advanced since {r['org_rev'][:8]} "
            f"(fetched {r['fetched']}) — re-fetch to refresh")

    return issues, info


def render(issues, info, today):
    """The report text. Identical shape to the source's inline builder: counted
    sections first, then the informational ones, and a one-line 'clean' header
    when nothing is counted."""
    total = sum(len(v) for v in issues.values())
    lines = [f"# Lint {today} — {total} issue(s)", ""]
    for k, v in issues.items():
        if v:
            lines.append(f"## {k} ({len(v)})")
            lines += [f"- {i}" for i in v] + [""]
    for k, v in info.items():
        if v:
            lines.append(f"## {k} ({len(v)}) — informational, not counted")
            lines += [f"- {i}" for i in v] + [""]
    return "\n".join(lines) if total else f"# Lint {today} — clean\n" + "\n".join(
        lines[2:] if any(info.values()) else []
    )


def run(cfg, stale_days=DEFAULT_STALE_DAYS, today=None, notify_on=False):
    """Scan, render, and — when anything is COUNTED — write the report to
    ``mirror/health/<date>-lint.md`` and append a LOG.md line.

    Returns ``{issues, info, total, report, report_path}``; ``report_path`` is
    ``None`` on a clean pass (a clean run writes nothing, as in the source)."""
    day = today or datetime.date.today().isoformat()
    issues, info = scan(cfg, stale_days=stale_days, today=day)
    total = sum(len(v) for v in issues.values())
    report = render(issues, info, day)
    out = None
    if total:
        vault = Path(cfg["vault"])
        out = vault / _notes.HEALTH_DIR / f"{day}-lint.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report)
        with open(vault / "LOG.md", "a") as fh:
            fh.write(f"- {day} {datetime.datetime.now():%H:%M} · lint · "
                     f"{total} issues → {_notes.HEALTH_DIR}/{out.name}\n")
        if notify_on:
            notify(total)
    return {"issues": issues, "info": info, "total": total, "report": report,
            "report_path": out}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="brain-lint")
    ap.add_argument("--stale-days", type=int, default=DEFAULT_STALE_DAYS)
    ap.add_argument("--notify", action="store_true", help="desktop notification when issues found (best-effort)")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    cfg = config.load()
    if not cfg["vault"].exists():
        if not a.quiet:
            print(f"brain-lint: vault missing at {cfg['vault']} — run /brain-init")
        sys.exit(0)

    result = run(cfg, stale_days=a.stale_days, notify_on=a.notify)
    if not a.quiet:
        print(result["report"])
    sys.exit(1 if result["total"] else 0)


if __name__ == "__main__":  # pragma: no cover — Phase 5 owns the entry point
    main()
