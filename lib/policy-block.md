## Workspaces & in-project delegation

I usually launch `claude` from outside my repos. That means this "hub" session has
my global config, plugin skills, and machine-level MCP — but NOT any project's
`./CLAUDE.md`, hooks, project-scoped `.mcp.json`, project-local skills, or
permissions/env. Those load only in a `claude` process whose cwd is inside the repo.

My repos live under:
- `<your workspace dirs>/*`
  (set `CLAUDE_TODO_WORKSPACE_DIRS` to a `:`-separated list, or `todo config --workspace-dirs`)

### When to delegate to an in-project worker

When a request targets a specific project **and** needs that project's machinery,
spawn an in-project worker instead of acting from the hub. Triggers (any one):
- editing files in the repo (so its format/lint hooks fire on the change);
- running its build / test / lint (needs project permissions + env);
- using a project-scoped MCP server or a project-local skill/command;
- work where the repo's own `CLAUDE.md` conventions materially matter.

**Handle in the hub (no worker):** questions / explanations / plans / research,
cross-repo read-only reasoning, or anything about `~/.claude` itself.

Cross-repo **write** work → one worker per affected repo, coordinated from the hub.

### Worktree isolation for write work

Write work NEVER runs on a repo's main checkout — it runs in a git worktree.
Before delegating any mutation:

1. Choose a worktree slug (ticket id + short description, or `fix-<n>`).
2. Pass `--worktree <slug>` to `delegate.py`. The worker runs in
   `<repo>-worktrees/<slug>/`, created off `<your-base-branch>`.
3. Omit `--worktree` only for read-only delegations.

### Self-contained briefs

`--task` must be **self-contained** — the worker sees no conversation history.
State the goal, the files/area, and acceptance criteria explicitly.

### --seq linking

For a tracked `/todo` task, pass `--seq <n>` (or let it auto-inherit) — the task
records which repos it touched and `/todo`'s detail view lists each repo's worker
for direct resume. Use `--label <slug>` for a second concurrent worker in the same
repo.

### How (announce, then proceed — don't wait for approval)

Emit one line first — e.g. `→ delegating to a <project> worker` — then run
`delegate.py run --repo /path/to/repo --task "..."` (or `--project <name>` if
`CLAUDE_TODO_WORKSPACE_DIRS` is set).

### Additional policy (customize for your team)

- Require a linked story/ticket before creating a worktree.
- Require a pull request before merging write work.
- Always use `--base origin/<your-base-branch>` for new branches.
