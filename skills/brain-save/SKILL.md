---
name: brain-save
description: Fast capture into the brain (personal private brain) — distill the current conversation's durable takeaways (or the given text) into atomic notes, or drop a dump into inbox/. Use on "save this to the brain", "brain-save", "add that to my private brain". Guard (routing only) — a bare "remember this" with no brain/private brain mention belongs to Claude's native memory, NOT this skill; this guard does NOT override the vault's autonomous write-back policy (vault CLAUDE.md), under which a session that learns a durable fact writes it back unprompted.
---

# /brain-save — capture into the brain

**Input:** `$ARGUMENTS` = what to save; if empty, extract the current conversation's durable takeaways (facts that outlive this session — decisions, gotchas, state changes, how-tos).

> **Command form.** Every `python3 -m brain.<module>` line below runs with task-station's
> `lib/` directory on `PYTHONPATH`:
> `PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/lib" python3 -m brain.search …`.
> Written out in full the first time, abbreviated after.

Resolve the vault via `PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/lib" python3 -m brain.search status`
(rules: the vault's `CLAUDE.md`). Pick the path per item:

**Atomic fact → note.**

*Compose the slug; never invent one from a title.* The shape is
`<domain>[-<subdomain>]-<subject>` — `domain` comes from a **closed registry**
(the generic areas in `lib/brain/data/naming-contract.json` plus your org's own
words from the org brain's `schemas/node-types.json`), `subdomain` is optional and
free, and `subject` is at most **3 words naming the thing, never the verdict**. A
name that states a claim is unstable by construction: correct the claim and the
name lies — the imperative belongs in `type: rule`. Examples:
`finance-ap-invoice-approval`, `cloud-sql-contained-users`, `ai-mcp-server-scope`.
An unregistered domain is an **error**: `brain.search new` refuses and names the
closest registered domain. Fix the slug, or add the domain to the org registry by
PR — don't bend the name to pass.

1. **Look for the merge target FIRST — this is mandatory, not advisory.** No node
   is created without a result in hand:
   `python3 -m brain.search find-target '<the one-line description>'`
   Grading reads the **description**, never the slug text alone (two real notes
   scored 0.595 on slug similarity and were entirely different facts). Three
   outcomes:
   - **≥ 0.90 — the same fact.** Do NOT create. Update that node in place: bump
     `verified:`, add a dated correction line if the claim changed.
     `brain.search new` enforces this — it prints the target and exits without writing.
   - **0.60 – 0.90 — judgement call.** `brain.search new` stops and asks. Decide, then
     re-run with `--new` (a distinct fact, recorded as `distinct-from:`) or
     `--update <slug>` (the same fact — it appends to that node and records
     `converged-with:`). Either way the decision is *recorded*, so the next
     session doesn't re-litigate it.
   - **below 0.60 — genuinely new.** Create.
2. `python3 -m brain.search new <slug> --description '<one line>' --type <rule|how-to|gotcha|architecture|state|reference> [--publish] [--promote] [--source task-station:<seq>]`
   `area:` and `plane:` are stamped for you — `area` is derived from the slug's
   domain and `plane: knowledge` is set for `notes/` (hubs included). Only pass
   `--area`/`--plane` to override a derivation you know is wrong.
3. Fill the body: the fact, absolute dates, `[[related-note]]` links, work-item URLs (never local paths in anything promotable).
4. Add an INDEX.md line (right section) and a link from the relevant hub — a `notes/` file carrying `type: hub` — no orphans.
5. Decide the two sharing switches — both default OFF, and a note with neither stays private. `publish: true` if a colleague should be able to read it in your shared brain. `promote: true` if the whole org should have it → an org brain promotion candidate ([[_org-brain-queue]]). They are independent; set either, both, or neither.

**Dump/transcript/finding pile → inbox/.** Write `inbox/YYYY-MM-DD-<slug>.md` verbatim with a 2-line provenance header (where it came from, date). Untrusted until distilled.

**Always finish:** `python3 -m brain.search log <note|ingest> <slug or summary>`, then commit the vault (`git -C "<vault>" add -A && git commit -m "brain: <what>"` — quote the path).

Never save secrets (reference the secret's location, not its value). Sandboxed workers: don't write the vault — report the facts in your final message instead.
