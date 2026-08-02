---
description: The current task's canonical vocabulary — list terms, or add/edit/rm one (name unique per task).
argument-hint: "[list | add \"<name>\" <layer> <state> \"<def>\" | edit \"<name>\" [--layer|--state|--def|--rename] | rm \"<name>\" | <task#>]"
allowed-tools: Bash
disable-model-invocation: true
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" glossary $ARGUMENTS --session "${CLAUDE_SESSION_ID:-$CLAUDE_CODE_SESSION_ID}"`

The block above is the current task's **glossary** — a per-task canonical vocabulary: `{name, layer, state, def}` terms, name unique per task (case-insensitive). Each term is auto-injected into every attached session so plans, ADO items, and dialogue reuse the same words.

**Print the result verbatim** and do nothing else — the engine already handled the list/add/edit/rm.

- No argument (or `list`) lists the attached task's terms; `/glossary <task#>` lists another task's.
- `add` upserts by name (case-insensitive): `layer` is a short where-tag (`db`/`app`/`CI`/`infra`); `state` is `today`/`target`/`shipped`/`planned` (free text allowed); `def` is one plain sentence.
- `edit "<name>"` changes fields via `--layer`/`--state`/`--def`, and renames via `--rename "<new>"`.

**Proactive capture:** whenever you coin a canonical concept for this task and the user confirms it, add it yourself so it sticks for every session — `task-station glossary add "<name>" <layer> <state> "<def>"`.
