---
description: View or change Task Station settings (workspace dirs, categories).
argument-hint: "[--workspace-dirs a:b | --categories edit]"
allowed-tools: Bash
disable-model-invocation: true
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/lib/todo.py" config $ARGUMENTS`

The block above is the live output of `task-station config` — either the settings board (no arguments) or the result of a `--flag` change. Present it to the user verbatim in a code block; do not editorialize.

- No arguments → the board of settable values (`--workspace-dirs`, `--categories`) plus the read-only data dir and current tint mode / detected terminal.
- `--workspace-dirs <a:b>` → sets the colon-separated repo roots used by delegate's `--project` shorthand (writes `config.json`).
- `--categories edit` → prints the path to `config.json`; open that file to customize category tags/labels and skill→colour auto-tint rules.

These are stored values the plugin owns. For actions that touch your wider environment (CLAUDE.md policy, Terminal tint profiles), use `/task-station:setup` instead.
