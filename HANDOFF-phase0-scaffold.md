# Phase 0 scaffold — handoff

Branch: `monorepo-3.0.0`, base `main` @ 92f1d44.

## Files created (exactly these four, nothing else touched)

### `lib/core/__init__.py`
```python
"""task-station 3.0.0 core package — genuinely shared plumbing between the board and the brain. Populated in Phase 1; empty scaffold from Phase 0."""
```

### `lib/board/__init__.py`
```python
"""task-station 3.0.0 board package — the task plane (engine split lands here in Phase 2, flat-module moves in Phase 3). Empty scaffold from Phase 0."""
```

### `lib/brain/__init__.py`
```python
"""task-station 3.0.0 brain package — the knowledge plane (ported in Phase 4). Empty scaffold from Phase 0."""
```

### `tests/brain/__init__.py`
```python
"""Brain-suite package for the 3.0.0 dual-suite layout — the brain tests port here in Phase 4 so one unittest discover run covers both planes."""
```

Each file is a bare module docstring, nothing else. No existing file was modified.

## Verification

Not run. Bash is permission-gated in this session ("don't ask mode" denies Bash outright), so I could not execute `python3 -m unittest discover -t . -s tests -p "test_store*.py"`. Confirmed via `Glob` before writing that none of `lib/core`, `lib/board`, `lib/brain`, or `tests/brain` existed yet, so this is a clean scaffold with no collisions. The hub should run the full suite as planned.

## Surprises / notes

- None of the four target packages or their parent directories previously existed — `Write` created the directories implicitly, no conflicts.
- `git` invocation also failed under the same Bash-denial (`don't ask mode`), so I could not independently confirm branch/clean status via git; I relied on the branch/base stated in the task prompt and the environment's git status snapshot (clean, branch `monorepo-3.0.0`).
- No other files were touched — `plugin.json`, hooks, and existing `lib/`/`tests/` modules are untouched. Nothing imports the four new packages, so the scaffold is inert as required.
- Not committing, per instructions — the hub finishes git.
