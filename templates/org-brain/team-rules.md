---
name: team-rules
description: "Org team-rules — the FEW, sharp imperatives that steer every session. @import'd into each dev's ~/.claude/CLAUDE.md."
type: reference
verified: 2026-08-13
---

# Team rules

The **CLAUDE.md RULE tier**: imperative, org-wide, and deliberately short. Each rule is one
line + a why. Add a rule only when it is worth interrupting every session; safety-critical +
mechanizable rules should graduate to a **hook** instead (see [[routing-spec]]). Promote changes
here via `/brain-promote` (a lead approves the org-brain PR) — never edit the clone directly.

> This file is `@import`'d by `/brain-init` into `~/.claude/CLAUDE.md`; the SessionStart hook
> keeps the clone fresh with a throttled `git pull`. The lines below are examples — replace with
> your org's real set.

- **Never commit a secret** (API key, token, password, private key) — leaks are irreversible; a secret-guard hook is the mechanized backstop.
- **Branch write-work off your repo's integration branch, never its release branch** — branching off a release-only branch corrupts the release line.
- **Every change needs a work item and a PR** — no orphan commits; traceability is an audit requirement.
- **`promote: true` notes are promotion candidates, not pushed automatically** — org knowledge lands only through a human-approved org-brain PR.
- **Publish company knowledge to your shared brain — add `publish: true` to the note, because nothing publishes unless it says so — so it outlives you; org-wide facts still promote to the org brain** — shared knowledge that stays on one laptop is lost when that person moves on.
- **Prefer the latest Claude models for new AI work** — capability and cost both favor current models over pinned old ones.
