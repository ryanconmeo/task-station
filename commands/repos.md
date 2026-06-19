---
description: Hub repo index — list discovered repos, /repos <term> ranks them for routing a fuzzy task, /repos --refresh rescans.
argument-hint: "[term(s) to match · 'show' to print the index · --refresh [--force] to rescan · --json]"
allowed-tools: Bash
disable-model-invocation: true
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" repos $ARGUMENTS`

The block above is the live output of the hub **repo index** — a deterministic, regenerable map of the repos under your configured workspace roots (`task-station config --workspace-dirs`, defaulting to `~/Workspace` + `~/Workspace-Other`). It is built **on demand**, lives next to the task store at `<data_dir>/repos.{md,json}`, and is **not** part of `/todo` or `tasks.db` — repos aren't tasks.

Use it to route a fuzzy task to the right repo(s) **before** delegating in-project work, since a hub session launched from `~` can't auto-load anything inside a repo.

- **No argument** (or `show`) → prints `repos.md`: one short block per repo (name, path, ado_project, stack, status, plus any hand-authored summary/keywords/domain). Rebuilt automatically if the index doesn't exist yet. **Print it verbatim.**
- **`<term...>`** → ranks repos by token overlap of the term against name/keywords/domain/stack/ado_project/path and prints only the matches, best first. Use the task's own words (e.g. `/repos billing invoices`) to find the target repo.
- **`--refresh [--force]`** → rescans the roots and rewrites the index. Run this when repos have been added/removed/moved. Add **`--quiet`** for a one-line summary instead of the full board (used by the delegation routing step).
- **`--json`** → emits the structured list (for tooling / the routing step to consume).

Each repo's deterministic fields (name, path, remote, ado_project, stack, status) come from the filesystem + a couple of cheap `git` calls. The prose fields (`summary`, `keywords`, `domain`) are hand-authored in `<data_dir>/repos.overrides.json`, keyed by repo name — those **win** and **survive** every regeneration; discovery never overwrites them. Edit that file to make fuzzy matching smarter for repos whose purpose isn't obvious from the name.
