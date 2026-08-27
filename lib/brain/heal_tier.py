"""brain-station — tier-lint: the consistency machinery.

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 4a) from the brain source tree's
``scripts/tier_lint.py`` @ 0.14.0, GENERICIZED AT SOURCE (the ``COMPANY`` cue
lexicon shipped five org product names; see its comment).

Classifies each stored item against the routing model and reports where it is
mis-filed. The routing model (kind × audience → destination):

    imperative + safety-critical + mechanizable  -> HOOK   (strongest; human graduates it)
    imperative (else)                            -> CLAUDE.md RULE (team → org brain team-rules)
    declarative + company knowledge              -> vault note
    declarative + personal how-to-work           -> memory (the vault's memory/)
    team-relevant                                -> promote: true (promotion candidate)

Enforcement spectrum: HOOK > CLAUDE.md RULE > MEMORY. Rules stay FEW + sharp;
memory holds the long tail.

Scans (a) the memory store (``<memory>/*.md`` + ``MEMORY.md`` index) and
(b) the vault ``notes/``. Emits a findings report to
``reports/health/tier-lint-<date>.md`` (via ``brain.notes`` — reports/health is a
writable folder). ``--apply`` executes ONLY high-confidence ``memory→note`` and
``note→memory`` re-filings (lossless: create at destination via ``brain.notes``,
source preserved, a tombstone line at the origin, the MEMORY.md index line
moved). It NEVER deletes content, NEVER auto-promotes to org brain, and NEVER
creates hooks or rules — those are suggestions for a human to act on.

THE MEMORY TIER IS GOVERNED BY DECLARATION, NOT BY CUES. An item may only be
re-filed INTO memory when it already declares ``type: feedback`` or ``type:
user`` (:data:`MEMORY_TYPES`) — the same contract ``heal_lint``'s ``memory-type``
check enforces. Cues can suggest the move; only the declaration authorises it.

Layer rule: brain may import core and its own siblings, never board. Stdlib +
the sibling ``config`` / ``notes`` modules only. Python 3.9+.
"""
import argparse
import datetime
import re
import sys
from pathlib import Path

from . import config
from . import heal_lint
from . import notes

# --- heuristic cue lexicons (lowercase; single words match on word-boundary,
#     multi-word phrases match as substrings) ---------------------------------
IMPERATIVE = ["never", "always", "must", "must not", "do not", "don't", "ensure",
              "avoid", "shall", "required", "require", "make sure", "should never",
              "should always"]
SAFETY = ["secret", "secrets", "password", "token", "tokens", "credential",
          "credentials", "api key", "api-key", "private key", "force push",
          "force-push", "rm -rf", "destructive", "production", "prod", "leak", "security"]
MECH_WORDS = ["regex", "pattern", "grep", "glob", "command", "hook", "block",
              "reject", "match", "commit", "diff", "scan", "exit 1"]
MECH_TOKENS = ["sk-ant-", "ghp_", "`", "^", ">>", "&&", "\\b"]
# The source shipped five of ITS org's product names at the head of this list.
# They are gone: a cue lexicon is the one place org vocabulary would otherwise
# be baked into shipped code. What remains is platform/architecture vocabulary,
# which is what actually generalises. An org that wants its own product names
# scored is asking for tunable data, not a code edit — see the handoff.
COMPANY = ["org_brain", "azure", "sql", "pipeline",
           "devops", "repo", "database", "server", "deployment", "entra", "rls",
           "materialized", "gl", "lz", "adf", "synapse"]
PERSONAL = ["i prefer", "i like", "i usually", "when i", "my ", "remind me", "ask me",
            "remember to", "i work", "i want", "let me", "for me", "i tend", "i always",
            "i never", "i review", "i start"]
TEAM = ["team", "everyone", "org", "org-wide", "org wide", "all devs", "we should",
        "standard", "everybody", "the team", "our team"]

# The memory tier's membership rule, not a cue: memory/ holds ONLY facts about
# how to work with its owner, declared `feedback` or `user`. Same tuple the
# heal_lint `memory-type` check enforces — these two lints must not disagree.
MEMORY_TYPES = ("feedback", "user")


def _count(cues, text):
    n = 0
    for c in cues:
        if " " in c:
            if c in text:
                n += 1
        elif re.search(rf"\b{re.escape(c)}\b", text):
            n += 1
    return n


def _mech(text):
    n = _count(MECH_WORDS, text)
    n += sum(1 for tok in MECH_TOKENS if tok in text)
    return n


def classify(text, current, declared=None):
    """Return a finding dict for one item's text, given its ``current`` tier
    ('memory' or 'note') and its ``declared`` frontmatter type.

    ``declared`` is the item's own declaration of what kind of fact it is, and it
    GOVERNS the memory tier — see the guard at the end of this function."""
    t = (text or "").lower()
    imp, saf, mech = _count(IMPERATIVE, t), _count(SAFETY, t), _mech(t)
    comp, pers, team = _count(COMPANY, t), _count(PERSONAL, t), _count(TEAM, t)

    suggested, kind, conf, applied = current, "aligned — no change", "n/a", False

    if imp >= 1 and saf >= 1 and mech >= 1:
        suggested = "hook"
        kind = "graduate to HOOK (safety-critical + mechanizable imperative) — human action"
        conf = "high" if mech >= 2 else "medium"
    elif imp >= 1:
        suggested = "rule"
        kind = "CLAUDE.md RULE (imperative)" + (" — team → org brain team-rules" if team else "")
        conf = "medium"
    else:
        if pers > comp and pers >= 1:
            cand = "memory"
        elif comp > pers and comp >= 1:
            cand = "note"
        else:
            cand = current  # no clear signal — leave it
        if cand != current and cand in ("memory", "note"):
            suggested = cand
            dominant = pers if cand == "memory" else comp
            other = comp if cand == "memory" else pers
            if dominant >= 2 and other == 0:
                conf, applied = "high", True
            elif dominant >= 1 and other == 0:
                conf = "medium"
            else:
                conf = "low"
            kind = ("personal how-to-work → memory" if cand == "memory"
                    else "company knowledge → vault note")

    # --- the memory contract governs the destination; cues only inform it -----
    # memory/ holds ONLY how-to-work-with-its-owner facts, declared `feedback`
    # or `user`. Cue counting cannot establish that. First-person VOICE ("my ",
    # "for me") is not the claim "this is a fact about the owner", and on
    # 2026-08-21 conflating the two moved a survey of three THIRD-PARTY plugins
    # into memory and tombstoned the note it came from: personal 2 / company 0,
    # scored `high`, auto-applied. The move was also self-refuting — the memory
    # it minted carried `type: reference` (write_memory_note's default), which
    # is precisely the type heal_lint's `memory-type` check refuses.
    declared_ok = (declared or "").strip().lower() in MEMORY_TYPES
    if suggested == "memory" and current == "note" and not declared_ok:
        # Keep the signal; remove the machine's authority to act on it. A human
        # re-types the note first, and that re-type IS the assertion tier-lint
        # cannot make for itself.
        conf, applied = "low", False
        kind = (f"cues read personal, but the note declares `type: "
                f"{declared or 'missing'}` — re-type it to feedback|user first; "
                f"tier-lint will not mint a memory outside the contract")
    elif current == "memory" and suggested == "memory" and not declared_ok:
        # The same rule read from inside memory/: a declaration outside the
        # contract is a finding, not an 'aligned'. heal_lint owns the counted
        # bucket, so this reports and never auto-moves.
        suggested = "note"
        conf, applied = "medium", False
        kind = (f"memory declares `type: {declared or 'missing'}` — memory/ holds "
                f"only feedback|user; re-file to a vault note, or re-type it if it "
                f"really is a fact about working with its owner")

    return {"scores": {"imperative": imp, "safety": saf, "mechanizable": mech,
                       "company": comp, "personal": pers, "team": team},
            "current": current, "suggested": suggested, "kind": kind,
            "declared": declared, "confidence": conf, "applied_eligible": applied,
            "team": team}


# --------------------------------------------------------------------------- #
# reading stored items (tolerant of both note and harness-memory frontmatter)
# --------------------------------------------------------------------------- #
def _split_fm(text):
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return "", text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1:])
    return "", text


def _read_item(path):
    text = path.read_text(errors="ignore")
    fm, body = _split_fm(text)
    desc = ""
    m = re.search(r"(?mi)^description:[ \t]*(.*)$", fm)
    if m:
        desc = notes.parse_scalar(m.group(1).strip())
    # The `promote:` switch, read through brain.notes — the ONE reader, so a
    # tier-lint suggestion can never disagree with what promote.py will do.
    raw_promote = ""
    m = re.search(r"(?mi)^\s*promote:[ \t]*(.*)$", fm)
    if m:
        raw_promote = m.group(1).strip()
    src = ""
    m = re.search(r"(?mi)^\s*source:[ \t]*(.*)$", fm)
    if m:
        src = notes.parse_scalar(m.group(1).strip())
    is_tombstone = "tier-lint" in body.lower() and "moved" in body.lower()
    # The declared type, read through heal_lint.memory_type — the ONE reader, so
    # tier-lint's view of an item's type can never disagree with the check that
    # enforces it. It handles both shapes (harness `metadata:\n  type:` and a
    # top-level note `type:`), frontmatter only.
    declared = heal_lint.memory_type(path)
    return {"description": desc, "body": body, "type": declared,
            "promote": notes.switch({"promote": raw_promote}, "promote"),
            "source": src, "tombstone": is_tombstone}


def _iter_paths(memory_dir, notes_dir):
    if memory_dir and Path(memory_dir).exists():
        for f in sorted(Path(memory_dir).glob("*.md")):
            if f.name == "MEMORY.md" or f.stem.startswith("_"):
                continue
            yield "memory", f
    if notes_dir and Path(notes_dir).exists():
        for f in sorted(Path(notes_dir).rglob("*.md")):
            if f.stem.startswith("_"):
                continue
            yield "note", f


def scan(cfg):
    memory_dir = cfg.get("memory")
    notes_dir = cfg["vault"] / "notes"
    findings = []
    for tier, path in _iter_paths(memory_dir, notes_dir):
        item = _read_item(path)
        if item["tombstone"]:
            continue  # already re-filed
        text = f"{item['description']}\n{item['body']}"
        f = classify(text, tier, declared=item["type"])
        f.update({"slug": path.stem, "path": str(path),
                  "description": item["description"], "body": item["body"],
                  "promote": item["promote"], "source": item["source"]})
        f["team_suggest"] = bool(f["team"] and tier == "note" and not f["promote"])
        findings.append(f)
    return findings


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #
def _cue_str(f):
    return ", ".join(f"{k}:{v}" for k, v in f["scores"].items() if v)


def render_report(findings, date):
    refiles = [f for f in findings if f["applied_eligible"]]
    suggestions = [f for f in findings
                   if f["suggested"] in ("hook", "rule") or
                   (f["suggested"] in ("memory", "note") and f["suggested"] != f["current"]
                    and not f["applied_eligible"])]
    team_tags = [f for f in findings if f["team_suggest"]]
    aligned = [f for f in findings if f["suggested"] == f["current"] and not f["team_suggest"]]

    n_mem = sum(1 for f in findings if f["current"] == "memory")
    lines = [
        f"# Tier-lint {date}", "",
        f"Scanned {len(findings)} item(s) — {n_mem} memory, {len(findings) - n_mem} notes. "
        f"{len(refiles)} high-confidence re-filing(s), {len(suggestions)} suggestion(s) for review.",
        "",
        "## Re-filings (high confidence — `brain-tier-lint --apply` executes these)", "",
    ]
    if refiles:
        for f in refiles:
            lines.append(f"- **{f['slug']}** — `{f['current']}` → `{f['suggested']}` "
                         f"(high). {f['kind']}. cues: {_cue_str(f)}. source: {f['source'] or '—'}")
    else:
        lines.append("- none")
    lines += ["", "## Suggestions (human action — tier-lint never auto-creates hooks/rules, "
                  "never auto-promotes to org brain)", ""]
    if suggestions or team_tags:
        for f in suggestions:
            lines.append(f"- **{f['slug']}** (`{f['current']}`, {f['confidence']}) → "
                         f"{f['kind']}. cues: {_cue_str(f)}")
        for f in team_tags:
            lines.append(f"- **{f['slug']}** (`note`) → consider `promote: true` "
                         f"(promotion candidate; never auto-pushed). cues: {_cue_str(f)}")
    else:
        lines.append("- none")
    lines += ["", "## Aligned (correctly filed — no change)", ""]
    lines += [f"- {f['slug']} (`{f['current']}`)" for f in aligned] or ["- none"]
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# --apply: high-confidence memory↔note re-filings (lossless)
# --------------------------------------------------------------------------- #
def _memory_tombstone(slug, desc, dest_rel, date):
    return ("---\n"
            f"name: {slug}\n"
            f"description: {notes.emit_scalar(desc)}\n"
            "metadata:\n  type: reference\n---\n\n"
            f"<!-- MOVED to {dest_rel} by tier-lint {date}: this was company knowledge, "
            f"re-filed to the vault note tier. See [[{slug}]]. -->\n")


def _note_tombstone(slug, desc, dest_rel, date):
    return notes.render_note(
        {"name": slug, "description": desc, "type": "reference",
         "verified": date, "source": "tier-lint"},
        f"<!-- MOVED to {dest_rel} by tier-lint {date}: this was personal how-to-work, "
        f"re-filed to the memory tier. -->")


def _memory_index_lines(memory_dir):
    idx = Path(memory_dir) / "MEMORY.md"
    return idx, (idx.read_text(errors="ignore") if idx.exists() else None)


def _mark_memory_index_moved(memory_dir, slug, date):
    idx, text = _memory_index_lines(memory_dir)
    if text is None:
        return
    out, changed = [], False
    for line in text.split("\n"):
        if f"({slug}.md)" in line and "MOVED" not in line:
            out.append(f"- {slug}: MOVED → notes/ (tier-lint {date}) — see [[{slug}]]")
            changed = True
        else:
            out.append(line)
    if changed:
        idx.write_text("\n".join(out))


def _add_memory_index_line(memory_dir, slug, desc, date):
    idx, text = _memory_index_lines(memory_dir)
    line = f"- [{slug}]({slug}.md) — {desc}"
    if text is None:
        Path(idx).write_text("# MEMORY\n\n" + line + "\n")
        return
    if f"({slug}.md)" in text:
        return
    sep = "" if text.endswith("\n") else "\n"
    idx.write_text(text + sep + line + "\n")


def _add_index_line(vault, slug, desc):
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


def apply_moves(findings, cfg, date, commit=True):
    vault = cfg["vault"]
    memory_dir = Path(cfg["memory"]) if cfg.get("memory") else None
    applied = []
    for f in findings:
        if not f["applied_eligible"]:
            continue
        slug, desc, body = f["slug"], f["description"], f["body"]
        if f["current"] == "memory" and f["suggested"] == "note":
            notes.write_note(vault, slug, mode="create", folder="notes",
                             description=desc, body=body,
                             source=f["source"] or f"memory/{slug}.md",
                             actor="agent", commit=commit)
            (memory_dir / f"{slug}.md").write_text(_memory_tombstone(slug, desc, f"notes/{slug}.md", date))
            _mark_memory_index_moved(memory_dir, slug, date)
            _add_index_line(vault, slug, desc)
            applied.append(f"memory→note: {slug}")
        elif f["current"] == "note" and f["suggested"] == "memory":
            # mtype comes from the note's own declaration, never the
            # write_memory_note default of "reference" — minting a memory typed
            # `reference` is the contract violation this direction used to
            # commit on every single move. classify() has already refused any
            # note whose declaration is outside MEMORY_TYPES, so this is legal.
            notes.write_memory_note(memory_dir, slug, description=desc, body=body,
                                    mtype=(f.get("declared") or "feedback"),
                                    source=f["source"] or f"notes/{slug}.md",
                                    vault=vault, commit=commit, overwrite=True)
            (Path(vault) / "notes" / f"{slug}.md").write_text(_note_tombstone(slug, desc, f"memory/{slug}.md", date))
            _add_memory_index_line(memory_dir, slug, desc, date)
            applied.append(f"note→memory: {slug}")
    return applied


# --------------------------------------------------------------------------- #
def run(cfg, apply=False, today=None, commit=True):
    date = today or datetime.date.today().isoformat()
    findings = scan(cfg)
    body = render_report(findings, date)
    slug = f"tier-lint-{date}"
    exists = notes.resolve_note_path(cfg["vault"], slug, "reports/health").exists()
    report_path = notes.write_note(
        cfg["vault"], slug, folder="reports/health",
        mode="replace" if exists else "create",
        description=f"Tier-lint findings {date}", body=body,
        type="reference", source="tier-lint", actor="agent", commit=commit,
    )
    applied = apply_moves(findings, cfg, date, commit=commit) if apply else []
    return {"findings": findings, "report_path": report_path, "applied": applied,
            "date": date}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="brain-tier-lint", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="execute ONLY high-confidence memory↔note re-filings (lossless)")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    if a.apply:
        config.require_valid()  # writes re-file content; refuse a broken config
    cfg = config.load()
    if not cfg["vault"].exists():
        if not a.quiet:
            print(f"brain-tier-lint: vault missing at {cfg['vault']} — run /brain-init")
        return 0
    result = run(cfg, apply=a.apply)
    if not a.quiet:
        n = len(result["findings"])
        refiles = sum(1 for f in result["findings"] if f["applied_eligible"])
        print(f"tier-lint: {n} item(s) scanned, {refiles} high-confidence re-filing(s); "
              f"report → {result['report_path']}")
        if a.apply:
            print("applied: " + (", ".join(result["applied"]) or "none"))
        else:
            print("review the report, then re-run with --apply to execute the re-filings")
    return 0


if __name__ == "__main__":  # pragma: no cover — Phase 5 owns the entry point
    sys.exit(main())
