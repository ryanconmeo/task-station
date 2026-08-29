# private brain — vault schema & maintainer rules

This vault is your personal second brain, an **LLM-maintained wiki**: Claude maintains it, you (and Claude) read it. If you are a Claude session working here, these are your rules. Companion system: **org brain**, a shared org brain (a PR-gated markdown repo) — see the org brain section below.

> **Command form.** Every `python3 -m brain.<module>` line below runs with task-station's `lib/` directory on `PYTHONPATH`, i.e. `PYTHONPATH=<lib> python3 -m brain.search …`. `<lib>` is the `lib/` dir of your task-station install. Written out in full the first time, abbreviated after.

## Layout

Four folders, one question each. Everything else is a file at the root.

- `INDEX.md` — the catalog, one line per note. **The only file you pre-load.** Update it whenever you add/rename a note.
- `LOG.md` — append-only chronicle. Every ingest/report/heal/promotion appends one line (`python3 -m brain.search log <op> <message>`).
- `notes/` — atomic durable facts. One fact per note. **Hubs live here too**: a hub is a note carrying `type: hub`, a map of content for one area. Hubs are the graph's connective tissue — every note should be reachable from one.
- `docs/` — dated long-form (`YYYY-MM-DD-<slug>.md`): written-back syntheses (deep answers, research, briefings) and implementation plans. A plan declares itself with `type: plan`, which exempts its forward references from the broken-link lint.
- `inbox/` — dated, **untrusted** dumps (`YYYY-MM-DD-<slug>.md`). Fast capture; distill into `notes/` later, then note the distillation in LOG.
- `mirror/` — machine-written output, never hand-edited. `mirror/health/` is lint output.

Two optional symlinks sit beside them, and neither is part of this vault:

- `org-brain/` → a local clone of the org brain repo, once it exists. **Read-only** — contribute via `/brain-promote` → PR.
- `task-station/` → the board's exported task mirror, if you keep one.

**Your agent memory is NOT in here.** It lives beside the brains at `~/knowledge/memory`, inside no brain, because memory is about the *person* and has to survive a second brain. Wikilink into it freely with `[[slug]]`; write it only through normal memory conventions.

## Note schema (org brain-compatible + local extensions)

```
---
name: <kebab-slug>            # must equal filename
description: <one line — used by INDEX and relevance scans>
type: how-to | gotcha | state | architecture | reference | report | decision | hub | plan
publish: true                 # OPTIONAL, default off ⇒ mirror to your shared brain (colleagues read it)
promote: true                 # OPTIONAL, default off ⇒ org brain candidate (a lead reviews the PR)
verified: YYYY-MM-DD          # last confirmed true
source: <session | manual | inbox/<file>>
org-brain: <note slug or repo URL once promoted; omit until then>
---
<body: the fact. Wikilink related notes with [[slug]]. Convert relative dates to absolute.>
```

**Sharing is two switches, both OFF by default, and they are independent.** A note with
neither field stays in this vault and nobody else can read it — that is the default, and
there is no field to remember. `publish: true` mirrors it to your shared brain, where
colleagues read it with no review step. `promote: true` makes it a candidate for the org
brain, where a lead reviews the PR. `promote: true` without `publish: true` is legal and
means exactly what it says: straight to the company wiki, no personal-mirror stop.

## Read rules (every session)

- Load `INDEX.md`; open only what's relevant. Search: `PYTHONPATH=<lib> python3 -m brain.search search <terms>`.
- Cite notes as `[[slug]]` in answers. Trust order: `notes/` (verified facts) > `docs/` (dated syntheses) > `inbox/` (untrusted).
- A `state`-type note beats any dashboard/wiki page whose date is older than the note's `verified:`.

## Write rules

- **Autonomous write-back is model-initiated — no explicit user ask required.** Learning or correcting a durable fact is itself the trigger: write the note then and there, unprompted. This is a **distinct trigger** from user-invoked capture (`/brain-save`); the "explicit brain-directed ask" guard on brain-save/`brain_save` only keeps a casual bare "remember this" in Claude's native memory — it does **not** veto this policy.
- **Mechanism (single write path): the `brain_save` MCP tool** — for both autonomous write-back and user-invoked capture. It creates/updates the note, bumps `verified:`, and appends LOG; the caller then finishes INDEX + hub link + commit. Fall back to `python3 -m brain.search new` only when the brain MCP is unavailable.
- **One fact per note; update-don't-duplicate.** Correcting a note: fix in place, bump `verified:`, and if the old claim mattered, add a dated correction line ("Corrected YYYY-MM-DD: was X, actually Y"). Provenance is git.
- New note: prefer the `brain_save` MCP tool (or `python3 -m brain.search new <slug> --description '…' --type <t> [--publish] [--promote]`), then fill/verify the body, add it to INDEX (right section), link it from at least one hub (a `type: hub` note) or related note — **no orphans**.
- Substantial answers (multi-source syntheses worth re-reading) get filed to `docs/YYYY-MM-DD-<slug>.md` and linked from INDEX.
- Append LOG for every operation. Commit in logical units.
- **Never**: secrets/connection strings/tokens (reference the secret's *location*, never its value); person-directed criticism; edits to a linked `org-brain/` clone (contribute by PR).

## Shared brain (federation) boundary

- **Publishing is opt-IN.** A note in `notes/` reaches your org-visible **shared brain** ONLY if its frontmatter says `publish: true`. Nothing leaves this vault unless a note explicitly says so. `inbox/`, `docs/`, `mirror/` never publish at all, and your memory is not even in this vault. Company knowledge is worth publishing so it outlives you — but that is a decision you make per note, by adding the switch.
- **The mirror is generated, not hand-edited** — `python3 -m brain.search publish` (and a `/brain-heal` step) copies marked notes byte-exact, regenerates the mirror `INDEX.md`, and commits (never pushes). A **publish-lint** refuses to mirror any note containing a local home path, a UUID-shaped session id, or a secret — fix the source (scrub it, or drop the `publish: true`); the note is skipped, never silently rewritten.
- **Un-publishing is deliberate.** Remove `publish: true` and the next run does NOT delete the mirror copy — it reports the note as `WITHDRAWN-BUT-KEPT` and leaves it in place. `python3 -m brain.search publish --withdraw` is what actually removes it. (A note whose source file is gone, or that the publish-lint now blocks, IS deleted straight away — a leak has to come out.)
- **Peers are read-only.** Teammates' shared brains clone lazily under `~/knowledge/brains/peers/` (`python3 -m brain.search peers add <alias>`), are never auto-pulled, and are searched only with `--peers`. Never edit a peer clone.

## org brain (org brain) boundary

- private brain is a superset. Team-relevant facts get `promote: true` — the promotion pipeline. This is independent of `publish:`; a fact can go to the org brain without ever entering your shared mirror.
- `/brain-promote <slug>`: strips personal context (local paths, session IDs, first person), drops `publish/promote/source/org-brain` keys, writes to the org-brain clone on a branch (you open the PR). Until org brain exists, promotions queue in `notes/_org-brain-queue.md`.
- org brain notes are **linked, never copied**. Contradiction between a org brain note and a local note = reconcile item: newest verified fact wins.

## Self-healing

- `python3 -m brain.heal_lint`: broken wikilinks, orphans, INDEX drift, stale `verified:` (>90d), secrets scan. Report → `mirror/health/`.
- `/brain-heal` (LLM pass): runs tier-lint (re-files knowledge to the right tier), fixes what lint found, reconciles old-vs-new facts, commits.
