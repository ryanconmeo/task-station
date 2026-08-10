# Contributing to Task Station

Thanks for helping improve Task Station! This is a Claude Code plugin with a
strict simplicity constraint — please read the ground rules before opening a PR.

## Ground rule: stdlib-only, system python

Task Station has **zero third-party dependencies**. Everything runs on the
**system `python3` (3.9+)** with no `pip install` step — that's a hard design
constraint, not an accident:

- No `requirements.txt`, no virtualenv, no build step.
- Use only the Python standard library. If you think you need a dependency,
  open an issue first to discuss — the answer is usually "we can do it with
  stdlib."
- Generated code must also be stdlib-only and import-free at runtime (see
  `lib/stack_map.py`).
- Shell hooks are POSIX-ish `bash` and must stay `shellcheck --severity=warning`
  clean. They depend only on `python3` (the sole hard requirement) — **not** `jq`:
  hooks parse their JSON stdin via `lib/hookjson.py` (`jq -r '.path // default'`
  equivalent). Keep it that way; don't reintroduce `jq` or other CLI dependencies.

## Running the tests

The suite is plain `unittest`:

```bash
python3 -m unittest discover -t . -s tests -v
```

If you happen to have `pytest` installed, `pytest.ini` points it at the same
tests (no plugins required):

```bash
python3 -m pytest tests -q
```

**State isolation:** `tests/conftest.py` pins `TASK_STATION_HOME` to a throwaway
temp dir before any test imports the engine, so the suite never touches your real
`~/.claude/task-station-data`. Individual tests that need per-test isolation
additionally repoint the path globals in `setUp`. When you write tests, set
`TASK_STATION_HOME` (and, for the Desktop bridge, `TASK_STATION_DESKTOP_CONFIG`)
to tmp paths — never write to the real config dir.

CI runs the same `unittest` command on `ubuntu-latest` + `macos-latest` across
Python 3.11 and 3.12 (`.github/workflows/ci.yml`).

## Mutating a task: use `store.mutate` (mandatory)

Task rows carry an optimistic-lock `rev` column. Several Claude sessions, workers,
hooks, and the Desktop bridge can all mutate the **same** task at once, so the old
`load_task → mutate the dict → save_task` pattern silently loses updates: whoever
saves last overwrites the other's appended event / cost / decision.

**Any new code that changes an existing task MUST go through `store.mutate` (or the
`task-station.py` `mutate()` wrapper):**

```python
mutate(task_id, lambda t: add_event(t, "worker", "done", session))
```

`store.mutate(task_id, mutator_fn, retries=5)` loads the task, runs your
`mutator_fn(task)`, and saves it guarded by the loaded `rev`; if a concurrent
writer committed first it **reloads the fresh task and re-runs your mutator**, so
both writers' changes survive.

### Two retry layers, for two different failures

`mutate`'s loop handles the **rev race** only — it catches `RevConflict` and nothing else.
Contention for SQLite's **write lock** is a separate failure with its own layer underneath:

| failure | what it means | handled by |
|---|---|---|
| `RevConflict` | another writer committed first; your `rev` is stale | `mutate`'s reload-and-re-run loop |
| `OperationalError("database is locked")` | the write lock was held past `busy_timeout` (5s) | `_retry_locked` on the `SqliteBackend` write methods |

`_retry_locked` backs off with jitter and retries until `LOCK_RETRY_BUDGET_S` (10s) of
wall-clock has elapsed, then re-raises the original error. It is bounded by **time, not attempt
count**, because each attempt can itself block for the full `busy_timeout` — and it only retries
contention (`"locked"`/`"busy"` in the message); any other `OperationalError` propagates on the
first attempt, so a schema or SQL bug never gets retried into a timeout.

Two things follow for new code:

- **Don't add a lock retry around `mutate`.** It belongs under it, where it already is; wrapping
  `mutate` would let a lock wait consume one of its five `RevConflict` attempts.
- **A new write method on `SqliteBackend` needs the `@_retry_locked` decorator.** Without it that
  path crashes the process under contention rather than waiting — which is what happened before
  2.23.0, and it surfaced as an intermittently failing test rather than as an obvious bug.

Rules:
- **`mutator_fn` must be PURE** — it may only transform the task dict it is handed.
  No I/O, no `save_task`, no reading other mutable state, no cross-task writes:
  a conflict re-runs it, so any side effect would fire more than once. Do
  cross-task or external work (e.g. posting a reciprocal event to *another* task,
  an Obsidian sync) **after** `mutate` returns — each cross-task write its own
  `mutate` call.
- Plain `save_task(task)` (no `expected_rev`) still works for create paths and
  genuinely single-writer cases; it is last-writer-wins but still bumps `rev`.
- `load_task` attaches the row's version as `task["_rev"]`; it is stripped from the
  persisted blob and must never be rendered/exported (use `store.strip_rev` if you
  dump a task dict whole).

## Regenerating `lib/stack_map.py`

`lib/stack_map.py` is a **generated file — do not edit it by hand.** It's
distilled from the curated `STACKS` table in `tools/gen_stack_map.py` (the top
~40 common language/tooling stacks) — the single source of truth. To add or
change a stack, edit that table, then regenerate:

```bash
python3 tools/gen_stack_map.py
```

The generator is self-contained (no external input, no network) and emits pure
stdlib dict literals, so it reproduces `lib/stack_map.py` byte-for-byte on every
run. Commit the regenerated module.

## Branch & PR flow

1. Fork (or branch off `main`).
2. Make focused changes; keep unrelated refactors out of the PR.
3. Add/extend tests for any behaviour change and run the full suite (above).
4. Add a `CHANGELOG.md` entry under the next version; bump
   `.claude-plugin/plugin.json` **and** `.claude-plugin/marketplace.json`
   versions together when releasing.
5. Open a PR against `main`. The PR template's checklist covers the essentials
   (stdlib-only, tests green, state isolation, stack-map regenerated if touched,
   docs/changelog updated).

## Reporting bugs & security issues

- Bugs / features: use the GitHub issue templates.
- Security vulnerabilities: **do not** open a public issue — see
  [SECURITY.md](SECURITY.md) for private reporting.

By contributing you agree your contributions are licensed under the repository's
[MIT License](LICENSE).
