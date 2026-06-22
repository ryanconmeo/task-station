---
description: View or change Task Station settings, plus one-time setup (workspace dirs, categories, tint, title, bare commands, delegation policy, Desktop bridge).
argument-hint: "[--workspace-dirs a:b | --categories [edit] | --enable/--disable <key> | --tint-theme auto|dark|light | --title on|off | --bare-cmds on|off | --update-check on|off | --desktop-bridge on|off | --policy on|off]"
allowed-tools: Bash
disable-model-invocation: true
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" config $ARGUMENTS`

The block above is the live output of `task-station config` — either the unified settings + status board (no arguments) or the result of a `--flag` change. Present it to the user verbatim in a code block; do not editorialize.

- No arguments → the unified board: the settable values below, the read-only data dir, the tint (full-palette escape) + detected terminal, and a status report of what's still unconfigured (workspace dirs, whether the delegation policy is installed, the Desktop bridge).
- `--workspace-dirs <a:b>` → sets the colon-separated repo roots used by delegate's `--project` shorthand (writes `config.json`).
- `--categories [edit]` → no arg shows the enabled set (which **starts lean at CORE — BUG · FEATURE · GENERAL — and grows automatically** as tasks are categorised); `edit` prints the `config.json` path so you can customize category tags/labels, palettes, and skill→colour auto-tint rules.
- `--enable <key>` / `--disable <key>` → toggle a single category slot on/off (accepts a key, emoji, or `[TAG]`). Disabling `⚫ GENERAL` is refused — it's permanent.
- `--tint-theme auto|dark|light` → choose the tint palette appearance. `auto` follows the OS appearance; the Sands palettes ship theme-independent, so this mainly affects any custom `hex_light` overrides.
- `--title on|off` → toggle the auto terminal tab/window title `#<seq>: <title>` for attached sessions (default on; also via `TASK_STATION_TITLE=off`).
- `--bare-cmds on|off` → install or remove the bare `/todo` + `/done` aliases (the namespaced `/task-station:todo` + `/task-station:done` always work regardless).
- `--update-check on|off` → opt in (default off) to a one-line `/todo` list footer when a newer Task Station version is published. When on, it makes at most one `git ls-remote` version check to GitHub per day (cached locally); offline or any failure is silent, and no task data is ever sent.
- `--desktop-bridge on|off` → wire the dependency-free MCP server into Claude Desktop (on) / remove it (off). Safely merges one entry into the Desktop config (backed up first); restart Desktop to apply.
- `--policy on|off` → adds / removes a **100%-reversible** delegation-policy block in your global `CLAUDE.md` (sentinel-fenced, hash-verified, backed up). Off restores the file byte-for-byte; refuses if the block was hand-edited.
- `--data-dir` *(read-only)* → shown on the board; the data directory holding `tasks.db` + `config.json` (set via `$TASK_STATION_HOME`, outside the plugin cache so updates never touch it).

Category tinting is **zero-setup**: each category ships a full **Sands** palette (background, foreground, bold, cursor, 16 ANSI colors) that the hooks apply to your terminal via standard escape sequences — iTerm2 and Terminal.app both honor it, with no profiles or shell aliases to install. (The old `--tint-profiles` / profile-switching mechanism was removed in 1.7.0.)

The settable values are stored by the plugin; `--policy` and `--desktop-bridge` perform and reverse changes to your wider environment (your `CLAUDE.md`, Claude Desktop config).
