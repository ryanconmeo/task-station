---
name: brain
description: Query the brain — search the personal vault (private brain) + agent memory + task history + org-brain clone, answer with note citations, file substantial answers back as reports. Use when asked "check the brain", "search the brain for X", "what do we know about X", or for briefings on any project/setup topic.
---

# /brain — query the brain

**Input:** `$ARGUMENTS` = a question or topic.

> **Command form.** Every `python3 -m brain.<module>` line below runs with task-station's
> `lib/` directory on `PYTHONPATH`:
> `PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/lib" python3 -m brain.search …`.
> Written out in full the first time, abbreviated after.

Resolve the vault first — `PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/lib" python3 -m brain.search status`
prints the configured vault path (rules: that vault's `CLAUDE.md`). Never assume a path. Do not
bulk-read folders — search, then open only the hits.

1. **Search** with 2–3 term variants: `python3 -m brain.search search <terms>` (separate runs for synonyms; zero tokens). Add `--episodic` when the question is about past work/decisions ("when did we…", "why did we…"). Add `--snippets` for context. Also check the relevant hub — a `notes/` file carrying `type: hub` (start from `INDEX.md` if unsure which).
2. **Read the top hits only** (notes are atomic — cheap). Trust order: `notes/` + `memory/` > `docs/` > `task-station/` > `inbox/`. A `state` note beats any older-dated dashboard/wiki claim.
3. **Answer with citations**: cite notes as `[[slug]]` and include the work-item URLs the notes carry. Say when the brain has nothing — don't pad.
4. **Write back** (interactive sessions only): if the answer synthesized 3+ sources or is worth re-reading, file it to `docs/YYYY-MM-DD-<slug>.md` (schema per the vault CLAUDE.md), add an INDEX line, link it from the relevant hub, then `python3 -m brain.search log report <slug>` and commit the vault (`git -C "<vault>" add -A && git commit -m "brain: report <slug>"` — quote the path).
5. If the search exposed a wrong/stale note, fix it in place (bump `verified:`, dated correction line) as part of the same pass.
