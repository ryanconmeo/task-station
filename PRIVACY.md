# Privacy

Task Station is **local-first** and has **no telemetry**. Out of the box it makes
**no network calls** and sends your task data nowhere. Everything below is the
honest, complete picture of what is stored and what — only if you opt in — can
ever leave your machine.

## What's stored, and where

All data lives **locally** under `${CLAUDE_CONFIG_DIR:-~/.claude}/task-station-data/`:

- `store/tasks.db` — a single indexed SQLite database of your tasks (WAL mode, so
  the CLI and the Desktop bridge can share it safely).
- `config.json` — your settings.
- `workers.json`, `pending-briefs/` — delegate runtime state.
- `repos.{md,json}` + `.repos-cache.json` — the optional hub repo index and its
  cache (only if you use `/repos`).

Nothing here is uploaded. Your task data is yours and stays on disk.

## Network egress — there are exactly two opt-in paths

By default there is **no egress**. Only these two features can make network
calls, and both are **off until you turn them on**:

### 1. Update check (opt-in, off by default)

Enable with `task-station config --update-check on`. When on, Task Station makes
**at most one `git ls-remote` to GitHub per day** (cached locally) to see whether
a newer version is published. It sends **no task data** — just a version probe.
Offline or any failure is silent. Disable any time with
`config --update-check off`.

### 2. Repo enrichment (opt-in per repo, off by default)

The hub repo index (`/repos`) is **deterministic and fully local by default** —
`summary` + `keywords` are computed on-device. Model enrichment (the **only**
egress path in `/repos`) runs **only** for a repo whose manifest `enrich` flag you
explicitly flip on with `/repos enrich <name>`. Even then it is tightly bounded:

- **Fingerprint-gated.** Each repo has a `fingerprint = sha1(remote + sorted
  top-level entries + …)`; an enriched repo is re-sent only when it's new or
  structurally changed — not on ordinary commits.
- **Name-only, bounded input.** The prompt is a bounded README excerpt + a
  top-level file/dir **name** sketch. Arbitrary file **contents are never read**,
  and a **secret denylist** guard keeps sensitive names out of what's sent.
- **Transparent.** Refresh logs what it's doing
  (`enriching (sending README+tree NAMES): …`), and `/repos --refresh --dry-run`
  reports what *would* be sent **without sending anything**.
- **Kill-switches.**
  - `--no-llm` — force the deterministic path even for `enrich:true` repos.
  - `--dry-run` — report, never send.
  - `TASK_STATION_REPO_ENRICH=off` (or `repo_enrich:false` in config) —
    **hard-disable all enrichment egress globally**, regardless of per-repo flags.

> Honest nuance: *listing ≠ sending, but indexing ≠ fully private.* An
> `enrich:false` repo never has its content sent; the local index still records
> repo metadata on your own disk.

## Delegate workers

The delegate feature spawns local `claude -p` workers in your own repos. That's
**your own Claude usage** — no third party is involved, and Task Station adds no
egress of its own.

## Claude Desktop bridge

`task-station config --desktop-bridge on` wires a **local stdio MCP server** into
Claude Desktop's config and points it at a stable launcher. Desktop talks to it
over local stdio and it reads/writes the **same local `tasks.db`** the CLI uses —
so Desktop and the CLI share one task store on your machine. No network, no third
party. Remove it any time with `config --desktop-bridge off`.

> Testing/CI tip: set `TASK_STATION_DESKTOP_CONFIG=/tmp/desktop.json` to redirect
> the bridge installer at a throwaway file.

## Changes to your wider environment

For completeness, the only writes outside the data dir are explicit and
reversible: the bare-command aliases (`config --bare-cmds on`), the
delegation-policy block in your global `CLAUDE.md` (`config --policy on`, 100%
reversible and hash-verified), the engine symlink + status-line segment under
`~/.claude/`, and the Desktop bridge entry above. None of these transmit data.
