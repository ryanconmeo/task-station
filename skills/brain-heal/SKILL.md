---
name: brain-heal
description: Self-healing pass over the brain (personal private brain) — tier-lint (re-file memory↔notes to the right tier), fix lint findings, ingest recent episodes and auto-distilled captures, reconcile stale/contradictory facts, sync with the org brain. Default cadence is daily dirty-gated (clean days skip); also run when the lint notification nags or on demand.
---

# /brain-heal — heal the brain

> **Command form.** Every `python3 -m brain.<module>` line below runs with task-station's
> `lib/` directory on `PYTHONPATH`:
> `PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/lib" python3 -m brain.search …`.
> Written out in full the first time, abbreviated after.

Resolve the vault via `PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/lib" python3 -m brain.search status`
(rules: the vault's `CLAUDE.md`). This is the LLM tier of the maintenance loop;
`brain.heal_lint` and `brain.heal_tier` are the deterministic tiers.

0. **Gate** (unless the user explicitly forced a pass): `python3 -m brain.heal_gate` — parse the JSON. If `due` is false, report the `reasons` (e.g. "clean since last heal" or "dirty but <24h gate") and stop. If `due` is true, note the `reasons` — they tell you what changed (HEAD moved, new raw/ captures, newer lint report, task mirror or tasks_db changed) so you can target the pass. Due-ness requires BOTH >24h since the last completed heal AND a dirty signal; clean days and sub-24h windows cost $0 — that is the cost model.
1. **Tier-lint** (the consistency machinery): `python3 -m brain.heal_tier` writes a findings report to `reports/health/tier-lint-<date>.md` — each item's current tier, suggested tier, confidence, and a mechanical move plan against the routing model (imperative+safety-critical+mechanizable → HOOK; imperative → CLAUDE.md RULE; declarative company knowledge → private-brain note; declarative personal how-to-work → memory; team-relevant → `promote: true` promotion candidate). Review it, then optionally `python3 -m brain.heal_tier --apply` to execute ONLY the high-confidence `memory→note` / `note→memory` re-filings (lossless move via `brain.notes`, tombstone at origin, MEMORY.md index moved). HOOK/RULE candidates, `promote: true` switches, and org-brain promotions are **suggestions for you to act on by hand** — tier-lint never creates hooks/rules, never auto-switches, and never auto-pushes to the org brain.
2. **Artifact ingest** (board consumer, after tier-lint): `python3 -m brain.ingest` — scans recent CLOSED tasks (via the episodic adapter) carrying a `brief_path` and/or a non-empty glossary, and creates/updates ONE `notes/task-<seq>-<slug>.md` per task via `brain.notes` (`type: reference`, `source: task-station:<seq>`): a status/outcome line, a **link** to the brief (never copied/wikified — rendered deliverables stay in the artifact tier), and the glossary terms as a definition list. Idempotent — a re-run with no artifact/glossary change is a no-op, a real change refreshes the note's sections in place (never a duplicate note); a new note also gets an INDEX line. Degrades silently when the episodic layer is unavailable (no stream/mirror).
2b. **Publish** (shared-brain mirror, after artifact ingest — gated): `python3 -m brain.publish --if-configured`. When a `publish_mirror` is configured, this mirrors the marked `notes/` (opt-IN: only notes whose frontmatter says `publish: true`; `memory/`/`raw/`/`plans/`/`reports/` never publish) into the mirror — byte-exact, idempotent, one git commit, never pushed. A blocking **publish-lint** SKIPS (never rewrites) any note containing a local home path, a UUID-shaped session id, or a secret; the run continues and its summary lists blocked notes — **surface those to the user so they can fix the source note** (scrub the leak, or drop the `publish: true`). The summary can also report `WITHDRAWN-BUT-KEPT` notes — mirror copies whose source no longer says `publish: true`. They are deliberately NOT deleted; **surface them too**, and let the user choose between re-adding the switch and re-running with `--withdraw`. With no mirror configured, `--if-configured` is a clean no-op.
3. **Mechanical lint**: run `python3 -m brain.heal_lint`. Fix every finding: create/repair broken `[[links]]`, add missing INDEX lines, remove dead ones, fix frontmatter, resolve `memory-rot` (recreate a lost memory file from its MEMORY.md index line, or index/delete an unindexed one — judge which side is right), resolve `memory-type` (a memory typed anything but feedback|user gets re-filed — a system fact to a vault note, a repo rule to that repo's CLAUDE.md, a fact about another person to a note — and the memory entry is deleted, with its MEMORY.md index line, once the destination exists). `memory-dangling` items are to-write markers: write the note if you can source the content, otherwise leave.

   **Naming findings come in two severities — treat them differently.**
   - `## naming` (counted, above the informational block) holds **errors**: an
     unregistered domain, a reserved stem, illegal characters. Each carries its
     fix. These block: rename the node, or add the domain to the org brain's
     `schemas/node-types.json` by PR when the word is genuinely one of your
     organisation's. **Renaming moves five things together** — the file, `name:`,
     every inbound `[[wikilink]]`, the `INDEX.md` line, and hub links — so
     re-run `brain.heal_lint` afterwards to find what broke.
   - `## naming` under *informational, not counted* holds **warnings**:
     claim-shaped names, subjects over 3 words, generic tokens, dates in a note
     slug. Fix them as you touch the notes; don't churn the vault for them alone.
   - Never auto-apply a rename that drops a word appearing in **2 or fewer** node
     slugs (`naming.dropped_rare_words`) — those are the identifying words.
     Propose it instead.

3b. **Naming conformance** (the half `brain.heal_lint` cannot see): walk `notes/` and
   `projects/` and check the shape itself — every knowledge node's slug should be
   `<domain>[-<subdomain>]-<subject>` and every node should carry `area:` (derived
   from its domain) and `plane: knowledge`. Nodes written before that stamp
   existed have neither; add them, deriving `area` from the domain rather than
   inventing one. Also read the `converged-with:` / `distinct-from:` records the
   graded merge-target lookup writes — they are *decisions already made*:
   `distinct-from:` means a near-duplicate pair was deliberately kept apart, so
   do not "merge the duplicates"; `converged-with:` names a slug someone was
   talked out of, so an inbound `[[link]]` using that name should be repointed at
   the surviving node, not resurrected as a new one.
4. **Episodic ingest**: `python3 -m brain.search recent-tasks --days 14` (degrades gracefully with no board — skip this half if so), plus distill any `raw/*-auto-*.md` files the Stop-hook auto-distill dropped since the last pass. Extract durable facts (decisions, gotchas, state changes) into new/updated `notes/` (`source: task-station:<seq>` or `source: raw/<file>`), link from hubs. Skip pure execution logs; delete distilled raw captures. (Artifact notes from step 2 are the structured half; this step is the free-form distillation half.)
5. **Staleness / old-vs-new reconciliation**: for each `stale` finding and each hub claim, verify against reality (code, the board, the work-item tracker, git). **Newest verified fact wins**: update in place, bump `verified:`, add a dated correction line when the old claim mattered. Update hub "state" sections — they are the where-are-we orientation surface.
6. **Org-brain sync** (only if the vault's `org-brain/` clone exists): the SessionStart auto-pull keeps it fresh, but pull again if in doubt (`git -C <org-brain-clone> pull`); diff org-brain claims (incl. `org-brain/team-rules.md`) against local notes on shared topics; queue corrections in the right direction (local edit, or `/brain-promote` a correction). If the org brain isn't linked yet, check whether [[_org-brain-queue]] has entries worth nagging about.
6b. **Subscription memos** (after the org-brain pull): `python3 -m brain.subscribe check` — walks the reference stubs (`references/*.md`), and for each whose org node has moved on since it was fetched (`org_rev` behind the clone), memos every referencing task **through the board's own memo API in this repo** (config-gated by `knowledge_memos`, graceful-absent, once per node-rev per task). No CLI is spawned: the brain calls the board directly, and when the board's store is not on disk the report comes back with `board_absent: true` and nothing is sent. Idempotent — a re-run with nothing newly changed sends nothing. Surface the printed dirty-reference + delivered-memo tally in the close-out report, and surface `board_absent` when it is set (it means the memos were skipped, not that nothing was dirty). (`--no-deliver` reports without sending.)
7. **Close out**: update the Health section of the vault's brain hub note, `python3 -m brain.search log heal <summary>`, commit the vault, re-run `python3 -m brain.heal_lint` and confirm clean (memory-dangling excepted), then `python3 -m brain.heal_gate --mark-done` (writes the completion stamp the gate reads). Report changes in a short list — include the dirty-reference + delivered-memo counts from step 6b.

Cost note: a clean pass is ~51k tokens; prefer a cheaper model for routine passes.
