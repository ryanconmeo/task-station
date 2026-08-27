---
name: brain-init
description: First-run scaffold for the brain — preflight the tooling, create the personal vault (private brain) from the bundled scaffold, write the runtime config, migrate-then-link agent memory into the vault, and print the one manual settings step. Safe, idempotent, reversible. Use on install, or on "set up the brain / initialize my private brain".
---

# /brain-init — set up your personal brain home

The brain's data home is `~/brains/` with lowercase `brain/` (your personal vault,
`memory/` inside it) and `org-brain/` (the org clone). Setup is driven by
`brain.init_home` — idempotent, safe, and reversible.

> **Command form.** Every `python3 -m brain.<module>` line below runs with task-station's
> `lib/` directory on `PYTHONPATH`:
> `PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/lib" python3 -m brain.init_home …`.
> Written out in full the first time, abbreviated after.

1. **Preflight** (report anything missing, with the install command):
   - `git --version` · `python3 --version` · `rg --version` (missing on macOS → offer `brew install ripgrep`; Windows → `winget install BurntSushi.ripgrep.MSVC`).
2. **Check for an existing vault**: `python3 -m brain.search status`. If the configured vault already exists with an `INDEX.md`, report "already initialized" — init is still safe to run (it's a no-op on an initialized home), but never overwrite existing notes.
3. **Preview the plan**: `PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/lib" python3 -m brain.init_home --dry-run`. Show the user exactly what will be created / migrated / linked.
4. **Apply**: `python3 -m brain.init_home`. This:
   - creates `~/brains/{brain,org-brain}` (`brain/` scaffolded from the bundled `lib/brain/vault-scaffold/` if empty; `org-brain/` left empty until you clone the org brain into it);
   - writes `~/brains/config.json` and rewrites `~/.claude/brain-station.json` as a one-line pointer `{"config": "~/brains/config.json"}` (backing up any existing full config to `brain-station.json.bak`);
   - **migrate-then-links agent memory**: moves the hub's Claude native-memory dir into `<vault>/memory` (merging `MEMORY.md` line-unions on collision) and replaces the emptied native dir with a symlink → `<vault>/memory`, so future memory writes flow into the vault. If a file conflicts, init refuses to clobber it and leaves the native dir intact — resolve the conflict and re-run.
   - On **Windows** init prints the `mklink /J` junction command instead of creating the symlink (run it in an elevated shell).
   - **Sets NO injection keywords.** A fresh install ships an EMPTY `inject_keywords` list, and an empty list is how the prompt hook spells "injection off" — so until keywords exist, the brain injects nothing on your prompts. Give init a `--profile <org.json>` to seed them, or add `inject_keywords` to `~/brains/config.json` by hand afterwards. Say this out loud to the user; it is the difference between "installed" and "working".
5. **Initialize the vault git repo** (new scaffold only): `cd ~/brains/brain && git init && git add -A && git commit -m "brain: vault scaffold"` (init prints this reminder). Until you run it, vault writes are unversioned.
6. **Verify**: `python3 -m brain.search status` shows the vault under `~/brains/brain`; a first `search` on an empty vault politely returns "no hits" — suggest the user saves their first fact with "save this to the brain: …".
7. **Print the ONE manual step** (a plugin cannot edit permission settings): add `~/brains/brain` to `sandbox.filesystem.allowWrite` in `~/.claude/settings.json` so sandboxed shells can write it.
8. **Claude Desktop parity** (optional, print it): add to `claude_desktop_config.json` → `mcpServers`:
   ```json
   {
     "private-brain": {
       "command": "python3",
       "args": ["-m", "brain.mcp_tools"],
       "env": { "PYTHONPATH": "<plugin-root>/lib" }
     }
   }
   ```
   where `<plugin-root>` is this plugin's installed path.
9. **Federation** (optional, print it): to publish to an org-visible **shared brain**, set `publish_mirror` in `~/brains/config.json` (or env `TASK_STATION_BRAIN_PUBLISH_MIRROR`) to your mirror clone — until it is *explicitly* set, the `/brain-heal` publish step stays a no-op (nothing is auto-published). Create the mirror repo + its allow-only ACL with `python3 -m brain.publish_setup --org <org> --project <project> --repo <alias>-brain-shared --owner <upn>` (prints the recipe; `--execute` runs it with a token you supply). Then `python3 -m brain.search publish` mirrors the notes you have marked `publish: true` — and only those; publishing is opt-in, so an unmarked note never leaves the vault.

## Reversibility / undo

The memory setup is a plain **symlink** (native dir → `<vault>/memory`). To undo:

```
rm ~/.claude/projects/<enc>/memory     # removes ONLY the link, never the target
```

⚠️ **Never** `rm -rf <link>/` with a trailing slash — that recurses *into* the
vault and deletes your notes. Your vault always lives at `~/brains/brain`.
The pre-init `~/.claude/brain-station.json` is preserved at `.json.bak`. Per-project
memory dirs other than the hub are left untouched (consolidation is opt-in later).
`python3 -m brain.init_home --help` restates all of this.
