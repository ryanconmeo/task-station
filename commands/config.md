---
description: View or change Task Station settings, plus one-time setup (workspace dirs, categories, bare commands, delegation policy).
argument-hint: "[--workspace-dirs a:b | --categories edit | --bare-cmds on|off | --update-check on|off | --tint-theme auto|dark|light | --policy on|off]"
allowed-tools: Bash
disable-model-invocation: true
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" config $ARGUMENTS`

The block above is the live output of `task-station config` — either the unified settings + status board (no arguments) or the result of a `--flag` change. Present it to the user verbatim in a code block; do not editorialize.

- No arguments → the board of settable values (`--workspace-dirs`, `--categories`, `--bare-cmds`) plus the read-only data dir, the tint mode (full-palette escape) / detected terminal, and a status report of what's still unconfigured (workspace dirs, whether the delegation policy is installed, the Desktop bridge).
- `--workspace-dirs <a:b>` → sets the colon-separated repo roots used by delegate's `--project` shorthand (writes `config.json`).
- `--categories edit` → prints the path to `config.json`; open that file to customize category tags/labels and skill→colour auto-tint rules.
- `--bare-cmds on|off` → install or remove the bare `/todo` + `/done` aliases (the namespaced `/task-station:todo` + `/task-station:done` always work regardless).
- `--update-check on|off` → opt in (default off) to a one-line `/todo` list footer when a newer Task Station version is published. When on, it makes at most one `git ls-remote` version check to GitHub per day (cached locally); offline or any failure is silent, and no task data is ever sent.
- `--tint-theme auto|dark|light` → choose the tint palette appearance. `auto` follows the OS appearance; the Sands palettes ship theme-independent, so this mainly affects any custom `hex_light` overrides.
- `--policy on|off` → adds / removes a **100%-reversible** delegation-policy block in your global `CLAUDE.md` (sentinel-fenced, hash-verified, backed up). Off restores the file byte-for-byte; refuses if the block was hand-edited.

Category tinting is **zero-setup**: each category ships a full **Sands** palette (background, foreground, bold, cursor, 16 ANSI colors) that the hooks apply to your terminal via standard escape sequences — iTerm2 and Terminal.app both honor it, with no profiles or shell aliases to install. (The old `--tint-profiles` / profile-switching mechanism was removed in 1.7.0.)

The settable values are stored by the plugin; `--policy` and `--desktop-bridge` perform and reverse changes to your wider environment (your `CLAUDE.md`, Claude Desktop config).
