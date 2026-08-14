# private brain — vault schema & maintainer rules

This vault is your personal second brain, an **LLM-maintained wiki**: Claude maintains it, you (and Claude) read it. If you are a Claude session working here, these are your rules. Companion system: **org brain**, a shared org brain (a PR-gated markdown repo) — see the org brain section below.

> **Command form.** Every `python3 -m brain.<module>` line below runs with task-station's `lib/` directory on `PYTHONPATH`, i.e. `PYTHONPATH=<lib> python3 -m brain.search …`. `<lib>` is the `lib/` dir of your task-station install. Written out in full the first time, abbreviated after.

## Layout

- `INDEX.md` — the catalog, one line per note. **The only file you pre-load.** Update it whenever you add/rename a note.
- `LOG.md` — append-only chronicle. Every ingest/report/heal/promotion appends one line (`python3 -m brain.search log <op> <message>`).
- `notes/` — atomic durable facts. One fact per note.
- `projects/` — hub pages (maps of content) per active area. These are the graph's connective tissue; every note should be reachable from a hub.
- `reports/` — written-back syntheses (deep answers, research, briefings). `reports/health/` is lint output.
- `plans/` — implementation plans.
- `raw/` — dated, untrusted dumps (`YYYY-MM-DD-<slug>.md`). Fast capture; distill into `notes/` later, then note the distillation in LOG.
- `memory/` (optional) → symlink to an agent-memory store, if you use one. Managed by the harness; wikilink into it freely, write it only through normal memory conventions.
- `org-brain/` (optional) → symlink to a local clone of the org brain repo, once it exists. **Read-only** — contribute via `/brain-promote` → PR.

## Note schema (org brain-compatible + local extensions)

```
---
name: <kebab-slug>            # must equal filename
description: <one line — used by INDEX and relevance scans>
type: how-to | gotcha | state | architecture | reference | report | decision | hub
scope: personal | team | private   # team ⇒ org brain promotion candidate; private ⇒ never published (opt-out)
verified: YYYY-MM-DD          # last confirmed true
source: <session | manual | raw/<file>>
org-brain: <note slug or repo URL once promoted; omit until then>
---
<body: the fact. Wikilink related notes with [[slug]]. Convert relative dates to absolute.>
```

## Read rules (every session)

- Load `INDEX.md`; open only what's relevant. Search: `PYTHONPATH=<lib> python3 -m brain.search search <terms>`.
- Cite notes as `[[slug]]` in answers. Trust order: `notes/` (verified facts) > `reports/` (dated syntheses) > `raw/` (untrusted).
- A `state`-type note beats any dashboard/wiki page whose date is older than the note's `verified:`.

## Write rules

- **Autonomous write-back is model-initiated — no explicit user ask required.** Learning or correcting a durable fact is itself the trigger: write the note then and there, unprompted. This is a **distinct trigger** from user-invoked capture (`/brain-save`); the "explicit brain-directed ask" guard on brain-save/`brain_save` only keeps a casual bare "remember this" in Claude's native memory — it does **not** veto this policy.
- **Mechanism (single write path): the `brain_save` MCP tool** — for both autonomous write-back and user-invoked capture. It creates/updates the note, bumps `verified:`, and appends LOG; the caller then finishes INDEX + hub link + commit. Fall back to `python3 -m brain.search new` only when the brain MCP is unavailable.
- **One fact per note; update-don't-duplicate.** Correcting a note: fix in place, bump `verified:`, and if the old claim mattered, add a dated correction line ("Corrected YYYY-MM-DD: was X, actually Y"). Provenance is git.
- New note: prefer the `brain_save` MCP tool (or `python3 -m brain.search new <slug> --description '…' --type <t> [--scope team]`), then fill/verify the body, add it to INDEX (right section), link it from at least one hub or related note — **no orphans**.
- Substantial answers (multi-source syntheses worth re-reading) get filed to `reports/YYYY-MM-DD-<slug>.md` and linked from INDEX.
- Append LOG for every operation. Commit in logical units.
- **Never**: secrets/connection strings/tokens (reference the secret's *location*, never its value); person-directed criticism; edits to a linked `org-brain/` clone (contribute by PR).

## Shared brain (federation) boundary

- **Publishing is opt-OUT.** Every note in `notes/` is mirrored to your org-visible **shared brain** UNLESS its frontmatter says `scope: private`. `memory/`, `raw/`, `plans/`, `reports/` NEVER publish. Default to publishing company knowledge so it outlives you; reserve `scope: private` for genuinely personal notes.
- **The mirror is generated, not hand-edited** — `python3 -m brain.search publish` (and a `/brain-heal` step) copies eligible notes byte-exact, regenerates the mirror `INDEX.md`, and commits (never pushes). A **publish-lint** refuses to mirror any note containing a local home path, a UUID-shaped session id, or a secret — fix the source (scrub it or mark `scope: private`); the note is skipped, never silently rewritten.
- **Peers are read-only.** Teammates' shared brains clone lazily under `~/brains/peers/` (`python3 -m brain.search peers add <alias>`), are never auto-pulled, and are searched only with `--peers`. Never edit a peer clone.

## org brain (org brain) boundary

- private brain is a superset. Team-relevant facts get `scope: team` — the promotion pipeline.
- `/brain-promote <slug>`: strips personal context (local paths, session IDs, first person), drops `scope/source/org-brain` keys, writes to the org-brain clone on a branch (you open the PR). Until org brain exists, promotions queue in `notes/_org-brain-queue.md`.
- org brain notes are **linked, never copied**. Contradiction between a org brain note and a local note = reconcile item: newest verified fact wins.

## Self-healing

- `python3 -m brain.heal_lint`: broken wikilinks, orphans, INDEX drift, stale `verified:` (>90d), secrets scan. Report → `reports/health/`.
- `/brain-heal` (LLM pass): runs tier-lint (re-files knowledge to the right tier), fixes what lint found, reconciles old-vs-new facts, commits.
