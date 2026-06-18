---
description: View or change Task Station settings, plus one-time setup (workspace dirs, categories, bare commands, delegation policy, terminal tint profiles).
argument-hint: "[--workspace-dirs a:b | --categories edit | --bare-cmds on|off | --update-check on|off | --policy on|off | --tint-profiles]"
allowed-tools: Bash
disable-model-invocation: true
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" config $ARGUMENTS`

The block above is the live output of `task-station config` — either the unified settings + status board (no arguments) or the result of a `--flag` change. Present it to the user verbatim in a code block; do not editorialize.

- No arguments → the board of settable values (`--workspace-dirs`, `--categories`, `--bare-cmds`) plus the read-only data dir, current tint mode / detected terminal, and a status report of what's still unconfigured (tint profiles, workspace dirs, whether the delegation policy is installed).
- `--workspace-dirs <a:b>` → sets the colon-separated repo roots used by delegate's `--project` shorthand (writes `config.json`).
- `--categories edit` → prints the path to `config.json`; open that file to customize category tags/labels and skill→colour auto-tint rules.
- `--bare-cmds on|off` → install or remove the bare `/todo` + `/done` aliases (the namespaced `/task-station:todo` + `/task-station:done` always work regardless).
- `--update-check on|off` → opt in (default off) to a one-line `/todo` list footer when a newer Task Station version is published. When on, it makes at most one `git ls-remote` version check to GitHub per day (cached locally); offline or any failure is silent, and no task data is ever sent.
- `--policy on|off` → adds / removes a **100%-reversible** delegation-policy block in your global `CLAUDE.md` (sentinel-fenced, hash-verified, backed up). Off restores the file byte-for-byte; refuses if the block was hand-edited.
- `--tint-profiles` → richer per-category tint: on iTerm2 nothing to do (zero-setup already); on Terminal.app it appends zsh aliases and prints the manual steps to create the matching colour profiles.

The first group are stored values the plugin owns; `--policy` and `--tint-profiles` perform and reverse changes to your wider environment (your `CLAUDE.md`, Terminal tint profiles).
