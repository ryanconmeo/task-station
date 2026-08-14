# brain-station — natural-language routing

The user has a brain: a personal living wiki (the **private brain**) plus, when linked, the **org brain** — the shared, PR-gated org wiki. Route plain-language intent to the right skill — no command memorization required.

## Search phrases → the `brain` skill

| The user says (any close variant) | Action |
|---|---|
| "search the brain for X" | run the brain skill on X |
| "hey brain, …" / "ask the brain about X" | run the brain skill on X |
| "query the brain about X" / "what do we know about X" | run the brain skill on X |
| "check the brain" / "brain, what's the state of X" | run the brain skill on X |

**Guardrail:** search intent is required. Casual mentions of the word "brain" ("brainstorm", "my brain hurts", anatomy) never trigger. When a search returns nothing, say so plainly and offer to save the answer once found — never pad.

## Capture phrases → the `brain-save` skill

| The user says (any close variant) | Action |
|---|---|
| "save this to the brain" | run brain-save on the current takeaway |
| "add that to my private brain" / "save to the private brain" | run brain-save |
| "brain, save/remember this" | run brain-save |
| "remember this **in the vault/brain**" | run brain-save |

**Hard rule (routing only):** a bare "remember this" (no mention of the brain, the private brain, or the vault) is Claude's **native memory**, never the vault. Do not route it here. This rule classifies *user phrasing* only — it does **not** gate autonomous write-back: per the vault CLAUDE.md, a session that learns or corrects a durable fact writes it back **unprompted**, and the `brain_save` MCP tool is the sanctioned mechanism for both that autonomous path and explicit capture.

## Reading Azure DevOps work items

- To READ an Azure DevOps work item / its tree, run `python3 -m brain.ado_tree <id>` (zero tokens), not the ADO MCP. MCP is for writes, or when the reader errors.
- When the board's `/brief` needs an ADO tree, source it from `python3 -m brain.ado_tree <id> --json`. The board's own skills stay forge-agnostic — the brain plane owns the work-item reader, and the dependency only ever points that way.

## Other routes

- "promote X" / "share X with the team/org" (about a note) → `brain-promote`.
- "heal/reconcile the brain", the lint notification, or a due daily gate → `brain-heal`.
- "set up the brain" / first run → `brain-init`.
