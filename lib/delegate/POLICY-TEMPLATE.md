# In-project worker delegation — policy template

`delegate.py` is the *how* (spawn/resume an in-project worker). This file is a
template for the *when* — the decision logic that makes Claude reach for it
automatically. **Copy a customized version into your global `~/.claude/CLAUDE.md`**
(which is loaded into every session); replace the placeholders with your own paths
and conventions. Without a policy like this, the bundled `delegating-work` skill
already handles common cases conservatively — this template is for teams that want
stricter or more automatic behavior on top of it.

---

## Workspaces & in-project delegation

I usually launch `claude` from outside my repos and let you figure out which project
to work in. That means this "hub" session has my global config, plugin skills, and
machine-level MCP — but NOT any project's `./CLAUDE.md`, hooks, project-scoped
`.mcp.json`, project-local skills, or permissions/env. Those load only in a `claude`
process whose cwd is inside the repo.

My repos live under:
- `<your workspace dirs>/*`   ← replace with your actual paths
  (set `TASK_STATION_WORKSPACE_DIRS` to a `:`-separated list of these parent dirs
   to use `--project <name>` shorthand instead of `--repo /absolute/path`)

### When to delegate to an in-project worker

When a request targets a specific project **and** needs that project's machinery,
spawn an in-project worker instead of doing it from the hub. Triggers (any one):
- editing files in the repo (so its format/lint hooks fire on the change);
- running its build / test / lint (needs project permissions + env);
- using a project-scoped MCP server or a project-local skill/command;
- work where the repo's own `CLAUDE.md` conventions materially matter.

**Handle it in the hub (no worker)** for: questions / explanations / plans /
research, cross-repo *read-only* reasoning, or anything about `~/.claude` itself.

Cross-repo **write** work → one worker per affected repo, coordinated from the hub.
If the target project is ambiguous, infer it from the active `/todo` task, otherwise
ask.

### Worktree policy for write work (optional, but recommended)

Write work NEVER runs on a repo's main checkout — it runs in a git worktree.
Before delegating any work that mutates a repo:

1. Decide on a worktree slug (e.g. a ticket id + short description, or `fix-<PR#>`).
2. Pass `--worktree <slug>` to `delegate.py`. The worker runs in
   `<repo>-worktrees/<slug>/`, created off the repo's default branch
   (override with `--base <your-base-branch>`, e.g. `--base origin/develop`).
3. Omit `--worktree` only for read-only delegations.

### How (announce, then proceed — don't wait for approval)

Emit one line first — e.g. `→ delegating this to a <project> worker` — then run:

```bash
python3 "$HOME/.claude/task-station-engine/delegate/delegate.py" run \
  --repo /absolute/path/to/repo \
  --task "<self-contained instructions>"
```

Or, if `TASK_STATION_WORKSPACE_DIRS` is configured:

```bash
python3 "$HOME/.claude/task-station-engine/delegate/delegate.py" run \
  --project <repo-name> \
  --task "<self-contained instructions>"
```

- Workers run `--permission-mode acceptEdits` and **inherit each repo's allowlist**.
  An un-allowlisted tool *fails* (a headless worker can't prompt a human) — widen
  that repo's `.claude/settings.json` allowlist rather than retrying blindly.
- `--task` must be **self-contained** (the worker doesn't see your conversation):
  state the goal, the files/area, and acceptance criteria.
- For write work, pass `--worktree <slug>` (per the policy above).
- For a tracked `/todo` task, also pass `--seq <n>` (its number) — the task records
  which repos it touched, gets a **persistent worker per (task, repo)**, and
  `/todo`'s detail lists each repo's worker for direct resume. For write work
  (`--worktree`), `--seq` is *auto-inherited* from the calling session's attached
  task — you usually don't need to pass it manually; use `--solo` to opt out.
  Add `--label <slug>` for a second concurrent worker in the same repo.
- `delegate.py list` shows known workers; add `--fresh` to force a new one.
- Workers resume automatically on later turns (same `--repo`/`--project` + `--seq`
  re-attaches). For long worker tasks, pass a larger `--timeout` or run in background.

### Additional policy (customize for your team)

Add any team-specific gates here, for example:
- Require a linked story/ticket before creating a worktree.
- Require a pull request to exist before merging write work.
- Always use `--base origin/<your-default-branch>` for new branches.
- Approve or reject specific repos for automatic delegation.

The `delegating-work` skill (bundled with the plugin) handles the generic case.
This block in your `CLAUDE.md` is where you layer your specific conventions on top.
