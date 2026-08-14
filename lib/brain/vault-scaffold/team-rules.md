# Team rules (the CLAUDE.md RULE tier)

This file is the local landing spot for **team-scoped imperative rules** — the
"do / don't" instructions that must steer every session, not just answer a
question. In the tier model:

- **HOOK** — safety-critical + mechanizable imperatives (e.g. secret-guard). Deterministic, cannot-miss.
- **CLAUDE.md RULE** — imperatives that stay FEW and sharp. Team-scoped rules live in **org brain**.
- **MEMORY** — the long tail of personal how-to-work.

## Where the canonical org rules live

The org rules surface is the org-brain clone: **`~/brains/org-brain/team-rules.md`**.
`/brain-init` adds an `@import` of that file to your user-level `~/.claude/CLAUDE.md`
(inside a `<!-- brain-station:team-rules -->` marker block), and the SessionStart
hook keeps the clone fresh with a throttled `git pull`. Until org brain is linked the
import is inert — no error, just no team rules yet.

## How rules get here

`/brain-heal` runs the tier-lint (`brain.heal_tier`), which flags imperative
notes/memories that should graduate to a RULE (and safety-critical + mechanizable
ones that should become a HOOK). Those are **suggestions for you** — tier-lint never
writes rules, never installs hooks, and never auto-pushes to org brain. Promote a rule
to the org with `/brain-promote` (a org brain PR a lead approves). Keep the set small:
a rule earns its place by being imperative, sharp, and worth interrupting every session.
