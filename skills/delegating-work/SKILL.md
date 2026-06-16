---
name: delegating-work
description: Delegate work that targets a specific repo and needs that repo's own CLAUDE.md, hooks, MCP servers, project-local skills, or build/test environment — spawns a background `claude` worker inside the repo. Do NOT load for general edits, Q&A, or research; only for work that genuinely requires a repo's project-scoped machinery.
---

# Delegating in-project work

A "hub" session launched from outside a repo does *not* load that repo's machinery:
its `./CLAUDE.md`, hooks, project-scoped `.mcp.json`, project-local skills, or
permissions/env — those load **only** in a `claude` process whose cwd is inside the
repo. `delegate` spawns exactly that process, keeps one persistent worker per
(task, repo), resumes it across turns, and relays the result back.

## When to delegate

Delegate when the work targets a specific repo **and** needs at least one of:
- editing files (so the repo's format/lint hooks fire on the changes);
- running its build / test / lint (needs project permissions + env);
- using a project-scoped MCP server or project-local skill/command;
- work where the repo's own `CLAUDE.md` conventions materially matter.

**Do NOT delegate** for: questions, explanations, plans, research, cross-repo
read-only reasoning, or anything about `~/.claude` itself. Handle those directly.

## How

Announce in one line first — e.g. `→ delegating this to a <repo> worker` — then run
(do not wait for approval):

```bash
python3 "$HOME/.claude/todo-engine/delegate/delegate.py" run \
  --repo /absolute/path/to/repo \
  --task "<self-contained instructions>"
```

**`--repo`** takes an absolute path and requires no extra setup — zero config.

**`--task` must be self-contained.** The worker has no access to this conversation:
include the goal, the files/area, and acceptance criteria.

**For write work, add `--worktree <slug>`** — the worker runs in an isolated git
worktree (`<repo>-worktrees/<slug>/`), created off the repo's auto-detected default
branch. Use a descriptive slug (e.g. a story id + short description).
Override the branch name with `--branch <name>` and the base ref with `--base <ref>`.

**`--seq <n>`** links the worker to the active `/todo` task (pass the task's number).
For write work (`--worktree`), `--seq` is *auto-inherited* from the calling session's
attached task — you usually don't need to pass it manually. Use `--solo` to opt out
for ad-hoc work unrelated to the current task.

Relay the worker's result back and continue orchestrating from here.

## Optional shorthand: `--project`

If `CLAUDE_TODO_WORKSPACE_DIRS` is set to a `:`-separated list of directories that
contain your repos, you can pass `--project <name>` instead of `--repo`:

```bash
export CLAUDE_TODO_WORKSPACE_DIRS="$HOME/Workspace:$HOME/Projects"
python3 "$HOME/.claude/todo-engine/delegate/delegate.py" run \
  --project my-repo \
  --task "<instructions>"
```

Without this env var, `--project` errors and tells you to use `--repo` instead.

## Resume and persistent workers

One worker per (task, repo) — the same invocation **resumes** the session on the next
turn automatically. The worker's session is pre-registered before launch, so even a
mid-run timeout leaves it resumable. Key flags:

- `delegate.py list` — show all known workers and their resume commands.
- `--fresh` — ignore the saved session and start a new one.
- `--label <slug>` — add a second concurrent worker for the same (task, repo).

Workers run with `--permission-mode acceptEdits` and inherit each repo's tool
allowlist. A tool the repo hasn't allowlisted will *fail* rather than prompt
(headless workers can't ask a human) — widen the repo's `.claude/settings.json`
allowlist rather than retrying blindly.

## Layering stricter policy

This skill is generic. Teams with stronger conventions — mandatory worktrees for all
write work, story/PR gates, a fixed workspace dir — should add those rules to their
own `~/.claude/CLAUDE.md`. See `lib/delegate/POLICY-TEMPLATE.md` for a ready-to-adapt
template.
