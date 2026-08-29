---
name: brain-init
description: First-run scaffold for the brain — preflight the tooling, create the knowledge container at ~/knowledge (brains under brains/, agent memory beside them), scaffold the private brain from the bundled template, write the runtime config, migrate-then-link agent memory, and print the one manual settings step. Safe, idempotent, reversible. Use on install, or on "set up the brain / initialize my private brain".
---

# /brain-init — set up your personal brain home

Everything you know lands in one **knowledge container**, `~/knowledge`:

```text
~/knowledge/
  config.json                     the runtime config
  memory/                         your agent memory — beside the brains, inside NONE
  brains/
    <org-slug>/private            the work vault (the private brain)
    <org-slug>/shared             the org-readable mirror
    <org-slug>/org                the org-brain clone
    personal/<username>-brain     the brain that outlives the employer
    peers/                        teammates' shared brains, read-only
```

`<org-slug>` comes from the `org_slug` config key (default `org`; an org profile sets the
real one). **Memory sits outside every brain on purpose** — it is about the *person*, so
it has to survive a second personal brain; a brain-local memory dir was considered and
rejected. Setup is driven by `brain.init_home` — idempotent, safe, and reversible, and an
install that names its own paths keeps them (there is no migration step).

> **Command form.** Every `python3 -m brain.<module>` line below runs with task-station's
> `lib/` directory on `PYTHONPATH`:
> `PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/lib" python3 -m brain.init_home …`.
> Written out in full the first time, abbreviated after.

1. **Preflight** (report anything missing, with the install command):
   - `git --version` · `python3 --version` · `rg --version` (missing on macOS → offer `brew install ripgrep`; Windows → `winget install BurntSushi.ripgrep.MSVC`).
2. **Check for an existing vault**: `python3 -m brain.search status`. If the configured vault already exists with an `INDEX.md`, report "already initialized" — init is still safe to run (it's a no-op on an initialized home), but never overwrite existing notes.
3. **Preview the plan**: `PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/lib" python3 -m brain.init_home --dry-run`. Show the user exactly what will be created / migrated / linked.
4. **Apply**: `python3 -m brain.init_home`. This:
   - creates the container above — the vault is scaffolded from the bundled `lib/brain/vault-scaffold/` if empty (`notes/ docs/ inbox/ mirror/`); `shared/`, `org/`, the personal brain and `peers/` are left empty until you clone or publish into them;
   - writes `~/knowledge/config.json` and rewrites `~/.claude/brain-station.json` as a one-line pointer `{"config": "~/knowledge/config.json"}` (backing up any existing full config to `brain-station.json.bak`);
   - **migrate-then-links agent memory**: moves the hub's Claude native-memory dir to `~/knowledge/memory` (merging `MEMORY.md` line-unions on collision) and replaces the emptied native dir with a symlink → `~/knowledge/memory`, so future memory writes flow there. If a file conflicts, init refuses to clobber it and leaves the native dir intact — resolve the conflict and re-run.
   - On **Windows** init prints the `mklink /J` junction command instead of creating the symlink (run it in an elevated shell).
   - **Sets NO injection keywords.** A fresh install ships an EMPTY `inject_keywords` list, and an empty list is how the prompt hook spells "injection off" — so until keywords exist, the brain injects nothing on your prompts. Give init a `--profile <org.json>` to seed them, or add `inject_keywords` to `~/knowledge/config.json` by hand afterwards. Say this out loud to the user; it is the difference between "installed" and "working".
5. **Initialize the vault git repo** (new scaffold only): init prints the exact `cd <vault> && git init && git add -A && git commit -m "brain: vault scaffold"` line for the path it actually created. Until you run it, vault writes are unversioned.
6. **Verify**: `python3 -m brain.search status` shows the vault under `~/knowledge/brains/<org-slug>/private`; a first `search` on an empty vault politely returns "no hits" — suggest the user saves their first fact with "save this to the brain: …".
7. **Print the ONE manual step** (a plugin cannot edit permission settings): add the vault path (and `~/knowledge/memory`) to `sandbox.filesystem.allowWrite` in `~/.claude/settings.json` so sandboxed shells can write them.
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
9. **Federation** (optional, print it): to publish to an org-visible **shared brain**, set `publish_mirror` in `~/knowledge/config.json` (or env `TASK_STATION_BRAIN_PUBLISH_MIRROR`) to your mirror clone — until it is *explicitly* set, the `/brain-heal` publish step stays a no-op (nothing is auto-published). Create the mirror repo + its allow-only ACL with `python3 -m brain.publish_setup --org <org> --project <project> --repo <alias>-brain-shared --owner <upn>` (prints the recipe; `--execute` runs it with a token you supply). Then `python3 -m brain.search publish` mirrors the notes you have marked `publish: true` — and only those; publishing is opt-in, so an unmarked note never leaves the vault.

## Reversibility / undo

The memory setup is a plain **symlink** (native dir → `~/knowledge/memory`). To undo:

```
rm ~/.claude/projects/<enc>/memory     # removes ONLY the link, never the target
```

⚠️ **Never** `rm -rf <link>/` with a trailing slash — that recurses *into* the
store and deletes your memory. Your memory lives at `~/knowledge/memory`, and your
vault at `~/knowledge/brains/<org-slug>/private`.
The pre-init `~/.claude/brain-station.json` is preserved at `.json.bak`. Per-project
memory dirs other than the hub are left untouched (consolidation is opt-in later).
`python3 -m brain.init_home --help` restates all of this.
