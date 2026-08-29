---
description: The current task's canonical vocabulary — list terms, or add/edit/rm one (name unique per task).
argument-hint: "[list | add \"<name>\" <layer> <state> \"<def>\" | edit \"<name>\" [--layer|--state|--def|--rename] | rm \"<name>\" | <task#>]"
allowed-tools: Bash
disable-model-invocation: true
---

```!
IFS= read -r -d '' TS_ARGV <<'TS_ARGV_END'
$ARGUMENTS
TS_ARGV_END
TS_ARGV="${TS_ARGV%$'\n'}"
TS_RC=0
TS_OUT="$( ( set -f; python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" glossary $TS_ARGV --session "${CLAUDE_SESSION_ID:-$CLAUDE_CODE_SESSION_ID}" ) 2>&1 )" || TS_RC=$?
[ -n "$TS_OUT" ] && printf '%s\n' "$TS_OUT"
[ "$TS_RC" -eq 0 ] || printf '%s\n' "[task-station] THE SKILL WAS NOT INVOKED. /glossary exited $TS_RC without producing the glossary; nothing was read and nothing was changed. Any text above this line is the failure, not the glossary."
:
```

> **If the block above is not the command's own output** — it is empty, it is a raw shell error, or it carries `THE SKILL WAS NOT INVOKED` — then `/glossary` **DID NOT RUN**. Say exactly that to the user in one line, show the failure verbatim, and stop. Do not reconstruct the output by hand, and do not describe anything as done.

The block above is the current task's **glossary** — a per-task canonical vocabulary: `{name, layer, state, def}` terms, name unique per task (case-insensitive). Each term is auto-injected into every attached session so plans, ADO items, and dialogue reuse the same words.

**Print the result verbatim** and do nothing else — the engine already handled the list/add/edit/rm.

- No argument (or `list`) lists the attached task's terms; `/glossary <task#>` lists another task's.
- `add` upserts by name (case-insensitive): `layer` is a short where-tag (`db`/`app`/`CI`/`infra`); `state` is `today`/`target`/`shipped`/`planned` (free text allowed); `def` is one plain sentence.
- `edit "<name>"` changes fields via `--layer`/`--state`/`--def`, and renames via `--rename "<new>"`.

**Proactive capture:** whenever you coin a canonical concept for this task and the user confirms it, add it yourself so it sticks for every session — `task-station glossary add "<name>" <layer> <state> "<def>"`.
