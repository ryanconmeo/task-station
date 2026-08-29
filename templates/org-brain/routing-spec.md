---
name: routing-spec
description: "The brain-station routing model: where each kind of knowledge lives, how it is enforced, and how it is captured and promoted."
type: architecture
verified: 2026-08-13
---

# Routing spec — where knowledge goes

Every durable thing you learn has exactly one right home. Route by **kind × audience**;
enforce by the **enforcement spectrum**; capture and promote by the **flows** below.

## The classification function

Given an item, classify it and file it:

| Kind / audience | Destination | Why |
|---|---|---|
| imperative **and** safety-critical **and** mechanizable | **HOOK** | cannot-miss, deterministic (e.g. a pre-commit secret-guard) |
| imperative (anything else) | **CLAUDE.md RULE** | steers every session; team-scoped → **org-brain team-rules** |
| declarative + company knowledge | **private-brain note** | searchable Q&A knowledge; **private until you say otherwise** (see below) |
| declarative + personal how-to-work | **memory** (`brain/memory/`) | the long tail of "how I work" |
| worth sharing with colleagues | switch **`publish: true`** | the opt-IN — mirrored to your shared brain, no review step |
| team-relevant (any of the above) | switch **`promote: true`** | promotion candidate — **never auto-pushed to the org brain** |
| rendered deliverable | **artifact** (the board's artifacts dir) | never wikified |
| code | **repos** | code lives in git, not the wiki |

Decision cues (what the tier-lint keys on):
- **imperative** — "never / always / must / don't / ensure / avoid".
- **safety-critical** — secrets, credentials, force-push, production, destructive ops.
- **mechanizable** — expressible as a command / regex / pattern the machine can check.
- **company knowledge** — your organisation's own domain nouns (its products, platforms and systems) plus infrastructure nouns (SQL, pipelines, storage…).
- **personal how-to-work** — first-person workflow ("when I review…", "remind me…").
- **team-relevant** — "the team", "everyone", "org-wide", "standard".

## The enforcement spectrum

**HOOK > CLAUDE.md RULE > MEMORY.** Strength buys reliability at the cost of rigidity, so
spend it sparingly:

- **Hooks** are deterministic and unmissable — reserve them for safety-critical, mechanizable rules.
- **Rules** stay **FEW and sharp**. Every rule interrupts every session; a rule earns its place by being imperative and worth that cost.
- **Memory** holds the long tail — soft, plentiful, low-stakes preferences and how-tos.

## Capture flow

1. **Autonomous write-back** — a session that *learns or corrects a durable fact* writes it back **unprompted**, via the sanctioned `brain_save` path. No user ask is needed for this.
2. **Phrasing guard (routing only)** — a bare "remember this" with **no** mention of the brain / private brain / the vault routes to the harness's **native memory**, not the wiki. This guard classifies *user phrasing*; it does **not** veto the autonomous write-back above.
3. **Tier-lint** — `/brain-heal` runs `brain.heal_tier`, which flags mis-filed items and re-files high-confidence `memory↔note` moves automatically (lossless). HOOK/RULE graduations and `promote: true` switches are **suggestions for a human**.
4. **Gated promotion** — `promote: true` marks a note as an org candidate. Promotion to the org brain is **always** a human-approved PR (`/brain-promote`), **never** an automatic push. Leads merge with one click.

## Shared brain — the three visibility tiers

A note has a **home** (above) and a **visibility**. Three tiers, widening:

1. **Private vault** — your full private brain on your machine. Everything lives here.
2. **Shared brain** — a per-person mirror repo (org-readable, owner-writable) holding the notes you publish. Publishing is **opt-IN**: a note in `notes/` publishes ONLY if its frontmatter says `publish: true`, so nothing leaves the private vault by accident. `inbox/`, `docs/`, `mirror/` never publish at all, and your agent memory does not live in the vault. A blocking **publish-lint** skips (never silently rewrites) any note carrying a local home path, a UUID-shaped session id, or a secret. Company knowledge is worth the switch — it outlives you there. Dropping the switch does not delete the mirror copy: the run reports it as withdrawn and keeps it until you pass `--withdraw`.
3. **Org brain** — the one curated, PR-gated org wiki. `promote: true` notes are promotion candidates; a lead approves the PR. The two switches are independent — `promote: true` alone sends a note to the org brain without publishing it to your shared mirror.

**Peers** are other people's shared brains, cloned **lazily on demand**
(`python3 -m brain.search peers add <alias>`) and **read-only** — never auto-pulled, and
never included in context injection. Search opts in with `--peers`; peer hits rank below
your own notes, memory, and the org brain.

Setup of a shared-brain repo + its ACL (allow-only, inheritance off — deny-wins rationale
in `python3 -m brain.publish_setup --help`) is scripted in `brain.publish_setup`.

## HOOK tier — graduating a rule to a deterministic guard

When a rule is both **safety-critical and mechanizable**, it should graduate from prose to a
hook so it cannot be missed. This is a **human action** — tier-lint suggests it, never installs it.

Worked example — a secret-guard (spec, not an installed hook):

- **Rule (prose):** "Never commit a secret."
- **Mechanized:** a `PreToolUse`/pre-commit hook that scans the staged diff and blocks on a match:
  - patterns: `sk-ant-[A-Za-z0-9-]{10,}`, `ghp_[A-Za-z0-9]{20,}`, `BEGIN [A-Z ]*PRIVATE KEY`, `(password|api[-_]?key|secret)\s*[=:]\s*\S{12,}`
  - action: exit non-zero with the offending pattern name; the commit/tool call is refused.
- **Why a hook, not a rule:** a secret leak is irreversible and the check is a pure pattern match — exactly the "safety-critical + mechanizable" corner that deserves the strongest tier.

The toolchain already ships this pattern set in `brain.heal_lint` (secret scan); a graduated
hook would run it at commit time rather than at lint time.
