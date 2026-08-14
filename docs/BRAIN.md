# brain-station — the knowledge plane

The board records **what happened**; the brain records **what is true**. Task Station
3.0.0 ships both planes in one plugin: `lib/board/` is the episodic plane (tasks
accumulate — lifecycle, sessions, decisions, cost), `lib/brain/` is the knowledge plane
(notes converge — one fact per note, updated in place, with a dated `verified:` field
instead of a status). Everything below is opt-in, stdlib-only, and silent until a brain
is configured: the board never notices if you skip this page.

## Quick start

```text
/brain-init
```

One run, idempotent and reversible. It preflights the tooling, creates the vault from the
bundled scaffold (`lib/brain/vault-scaffold/` — schema rules in its `CLAUDE.md`, an
`INDEX.md` catalog, a `notes/ plans/ projects/ raw/ reports/` tree), and writes the
runtime config. It can also migrate-then-link your agent-memory directory into the vault
(`memory/` becomes a real directory inside the vault; the harness path symlinks into it),
so memory notes and knowledge notes share one graph. Then:

```text
/brain what do we know about <topic>?   # search with [[slug]] citations
/brain-save                             # distill this conversation into atomic notes
/brain-heal                             # reconcile: tier-lint, lint fixes, episode ingest
/brain-promote <note>                   # personal note → org-brain PR
/ado 1234                               # read an Azure DevOps work-item tree, zero tokens
```

Plain phrases route without memorizing commands — `system-instructions.md` at the plugin
root maps "search the brain for X", "save this to the brain", "heal the brain", and
work-item asks to the right skill. A bare "remember this" (no mention of the brain or the
vault) deliberately stays with Claude's native memory.

## Configuration

Resolution order, per key: **environment override → config file → default.**

The pointer file `~/.claude/brain-station.json` holds one line —
`{"config": "<path>"}` — naming the real config (default `~/brains/config.json`).
Defaults put the vault at `~/brains/brain` and the org-brain clone at
`~/brains/org-brain`; mutable state (throttles, error log) lives in the station's data
home, the same one the board uses (`$TASK_STATION_HOME`, default
`~/.claude/task-station-data`).

| config key | env override | what it is |
|---|---|---|
| `vault` | `TASK_STATION_BRAIN_VAULT` | the personal vault (the **private brain**) |
| `memory` | `TASK_STATION_BRAIN_MEMORY` | agent-memory dir (default: `<vault>/memory`) |
| `org_brain_clone` | `TASK_STATION_BRAIN_ORG_BRAIN_CLONE` | local clone of the org tier (read-only; contribute via promote) |
| `publish_mirror` | `TASK_STATION_BRAIN_PUBLISH_MIRROR` | your published-subset repo, if you share one |
| `peers_dir` | `TASK_STATION_BRAIN_PEERS_DIR` | where subscribed peers' published subsets live |
| `tasks_db` | `TASK_STATION_BRAIN_TASKS_DB` | the board's store — the brain's episodic source |
| `episodic_stream` | `TASK_STATION_BRAIN_EPISODIC_STREAM` | exported episodic notes dir, if you keep one |
| `state_dir` | `TASK_STATION_BRAIN_STATE` | mutable state home override |
| `inject_context` | `TASK_STATION_BRAIN_INJECT_CONTEXT` | on/off: per-prompt context injection |
| `inject_keywords` | `TASK_STATION_BRAIN_INJECT_KEYWORDS` | extra keywords that trigger injection |
| `auto_distill` | `TASK_STATION_BRAIN_AUTO_DISTILL` | on/off: Stop-hook capture of durable facts |
| `knowledge_memos` | `TASK_STATION_BRAIN_KNOWLEDGE_MEMOS` | org-update memos onto board tasks (unset = auto-on when the board's store exists) |
| `org_label` | `TASK_STATION_BRAIN_ORG_LABEL` | display name of your org's shared wiki |
| `alias` / `owner` / `task` | `TASK_STATION_BRAIN_ALIAS` / `…_OWNER` / `…_TASK` | contributor stamps on promoted notes |
| `forge_kind` / `forge_org` / `forge_project` / `forge_repo` / `forge_target_branch` | `TASK_STATION_BRAIN_FORGE_*` | where promote pushes its PR (GitHub or Azure DevOps); **absent ⇒ promote queues locally instead of pushing** |
| `ado_org` | `TASK_STATION_BRAIN_ADO_ORG` | Azure DevOps org for the `/ado` reader |

Org values never live in code. An **org profile** — a small JSON of `org_label`,
`labels`, `keywords`, and the `forge` block, typically kept in a private repo your org
controls (template: `templates/org-brain/`) — is applied with:

```sh
PYTHONPATH=<plugin>/lib python3 -m brain.init_home --profile org.json
```

A partial profile is fine; only the keys present are set.

## Hooks — how the two planes share events

Both planes want SessionStart, UserPromptSubmit, and Stop, but the harness runs
same-event hooks in parallel and two JSON docs on one stdout don't merge. So the manifest
registers **one command per shared event** — `lib/hookmux.py` — which runs the children
**in order** (board first: tint and task-attach resolve before context injection), fans
the same stdin to each, concatenates `additionalContext`, resolves other keys
first-writer-wins, and **always exits 0**: a failing child leaves a stderr breadcrumb and
never breaks the session.

| event | children, in order |
|---|---|
| `SessionStart` | board `on_session_start.sh` → `brain.hooks.inject --session-start` (orientation context) → `brain.hooks.gate --session-start` (heal-due nag, daily dirty-gated) |
| `UserPromptSubmit` | board `on_user_prompt.sh` → `brain.hooks.inject --prompt` (relevant notes, throttled once per topic per session) |
| `Stop` | board `on_stop.sh` → `brain.hooks.distill` (opt-in auto-capture) |
| `PreToolUse(Bash)` | `brain/hooks/guard.py`, registered **directly** (brain-only event, no mux): denies a Bash command that would write a secret into the transcript — an opaque token as a literal flag value, or a secret-reading command whose output is neither suppressed nor captured. It **fails open**: any parse or logic error allows the command. |

Brain hook failures are recorded in the brain's own error log (in the data home), not in
the board's `hook-health.log` — two planes, two ledgers.

## MCP

The plugin's `.mcp.json` registers the one server (`lib/mcp_server.py`) for Claude Code;
`task-station config --desktop-bridge on` wires the same server into Claude Desktop. The
board's eleven tools are always there; when a brain is configured, five more mount
lazily: `brain_search`, `brain_status`, `brain_save`, `brain_log`, `brain_recent_tasks`.
Any brain-side failure leaves the board serving alone.

## The command line

Every brain module runs as `python3 -m brain.<module>` with the plugin's `lib/` on
`PYTHONPATH` (the hooks and `.mcp.json` set this up for their own children; set it
yourself for ad-hoc shells). The ones you'd reach for by hand:

```sh
python3 -m brain.search <terms>        # ranked search: vault + memory + org clone + peers
python3 -m brain.ado_tree <id>         # work-item + parent Feature + children + PRs (--json)
python3 -m brain.init_home             # (re-)scaffold; --profile applies an org profile
python3 -m brain.heal_lint             # lint: broken links, orphans, INDEX drift, stale verified:
python3 -m brain.ingest                # pull recent board episodes into the vault
python3 -m brain.orgpull               # freshen the org-brain clone (throttled)
python3 -m brain.subscribe <cmd>       # manage peer subscriptions; check for org updates
python3 -m brain.publish               # sync the published subset to your mirror
```

## Federation, in one paragraph

Your vault is the superset. A note marked `scope: team` is a **promotion candidate** —
`/brain-promote` strips personal context, converts it to the org schema, and lands it as
a PR a lead approves; nothing is ever auto-pushed. The org-brain clone joins search
read-only, and org updates can arrive as memos on your board tasks
(`knowledge_memos`). Peers' published subsets are subscribable and searchable, and a
peer's copy of a fact never outranks your own note. The only brain→board code path is
memo delivery (`brain/subscribe.py` → the board's memo API); the board never imports the
brain.

## Naming

Note naming — slug shape, type inference, the merge-target scorer — is owned by one
module, `lib/brain/naming.py`, with its contract data in
`lib/brain/data/naming-contract.json`. The full spec is
[docs/brain-naming.md](brain-naming.md).

## Data & privacy

Same rules as the board: everything is local plain Markdown in directories you chose; no
telemetry. The only network operations are the ones you configure explicitly — the
promote PR push (absent forge config, promote queues locally), the org-pull fetch of
your org's clone, and publish to your own mirror.
