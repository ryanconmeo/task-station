---
description: Task Station doctor + installers (delegation policy, terminal tint profiles).
argument-hint: "[--policy on|off | --tint-profiles | --workspace-dirs a:b]"
allowed-tools: Bash
disable-model-invocation: true
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/lib/todo.py" setup $ARGUMENTS`

The block above is the live output of `task-station setup` — either the status report (no arguments) or the result of an installer flag. Present it to the user verbatim; do not editorialize.

- No arguments → a doctor report: what's configured vs missing (tint mode + detected terminal, tint-profiles, workspace dirs, whether the delegation policy is installed), each line ending in the exact command to fix it.
- `--policy on` / `--policy off` → adds / removes a **100%-reversible** delegation-policy block in your global `CLAUDE.md` (sentinel-fenced, hash-verified, backed up). Off restores the file byte-for-byte; refuses if the block was hand-edited.
- `--tint-profiles` → richer per-category tint: on iTerm2 nothing to do (zero-setup already); on Terminal.app it appends zsh aliases and prints the manual steps to create the matching colour profiles.
- `--workspace-dirs <a:b>` → same as `/task-station:config --workspace-dirs` (offered here too for the guided flow).

Unlike `/task-station:config` (which edits values the plugin owns), `setup` performs and reverses changes to your wider environment.
