# In-project worker delegation — policy template

`delegate.py` is the *how* (spawn/resume an in-project worker). This file is a
template for the *when* — the decision logic that makes Claude reach for it
automatically. **Copy a customized version into your global `~/.claude/CLAUDE.md`**
(which is loaded into every session); replace the placeholder workspace paths and
project names with your own. Without a policy like this, `delegate.py` still works
when you invoke it by hand, but Claude won't know *when* to use it on its own.

---

## Workspaces & in-project delegation

I usually launch `claude` from `~` and let you figure out which project to work in.
That means this "hub" session has my global config, plugin skills, and any
machine-level MCP — but NOT any project's `./CLAUDE.md`, hooks, project-scoped
`.mcp.json`, project-local skills, or permissions/env. Those load only in a
`claude` process whose cwd is inside the repo.

My repos live under:
- `~/<work-root>/*`     — `<project-a>`, `<project-b>`, …   ← replace with yours
- `~/<personal-root>/*` — `<project-c>`, …

### When to delegate to an in-project worker
When a request targets a specific project **and** needs that project's machinery,
spawn an in-project worker instead of doing it from the hub. Triggers (any one):
- editing files in the repo (so its format/lint hooks fire on the change);
- running its build / test / lint (needs project permissions + env);
- using a project-scoped MCP server or a project-local skill/command;
- work where the repo's own `CLAUDE.md` conventions materially matter.

**Handle it in the hub (no worker)** for: questions / explanations / plans /
research, cross-repo *read-only* reasoning, or anything about `~/.claude` itself.

Cross-workspace **write** work → one worker per affected repo, coordinated from the
hub. If the target project is ambiguous, infer it from the active `/todo` task,
otherwise ask.

> **Set `DELEGATE` to the installed delegate path before copying this in.** When
> Claude runs the command below in a normal session, `${CLAUDE_PLUGIN_ROOT}` is **not**
> set (the harness only exports it inside this plugin's own hooks/commands), and the
> install path is version-specific. So resolve it once and hard-code (or alias) it.
> Find it with:
> ```bash
> ls ~/.claude/plugins/cache/*/claude-todo/*/lib/delegate/delegate.py
> ```
> Then replace `<DELEGATE>` below with that absolute path (re-point it after a
> `/plugin update` bumps the version, or wrap it in a shell alias / `$DELEGATE` env var
> so the policy text never changes).

### How (announce, then proceed — don't wait for approval)
Emit one line first — e.g. `→ delegating this to a <project> worker` — then run:
```bash
python3 <DELEGATE> run --project <name> --task "<self-contained instructions>"
```
- Workers run `--permission-mode acceptEdits` and **inherit each repo's allowlist**.
  An un-allowlisted tool *fails* (a headless worker can't prompt a human) — widen
  that repo's `.claude/settings.json` allowlist rather than retrying blindly.
- `--task` must be **self-contained** (the worker doesn't see your conversation):
  state the goal, the files/area, and acceptance criteria.
- If a `/todo` task is active, also pass `--seq <n>` (its number) — the task records
  which repos it touched, gets a **persistent worker per (task, repo)** named
  `todo-<seq>-<project>`, and `/todo`'s detail lists each repo's worker for direct
  resume. Add `--label <slug>` for a second concurrent worker in the same repo.
- `delegate.py list` shows known workers; add `--fresh` to force a new one. For long
  worker tasks, give the call a larger timeout or run it in the background.
