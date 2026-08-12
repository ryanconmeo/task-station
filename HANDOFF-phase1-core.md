# Phase 1 handoff — populate lib/core/

Branch `monorepo-3.0.0`, worktree `/Users/ryannguyen/Workspace-Other/claude-todo-worktrees/monorepo-3.0.0`
(confirmed via `.git` gitdir pointer — already isolated from the main `claude-todo` checkout, so no
`EnterWorktree` call was needed).

## A. Straight moves — created

- `lib/core/paths.py` — byte-for-byte copy of `lib/paths.py` (docstring, `data_dir()`).
- `lib/core/term.py` — byte-for-byte copy of `lib/term.py` (`width()`, `detect()`, `tmux_wrap()`).
- `lib/core/pricing.py` — byte-for-byte copy of `lib/pricing.py` (rate sheets, `rates_for`,
  `context_window_for`, `message_cost`, `model_family`, etc).

## B. Identity shims — rewritten

`lib/paths.py`, `lib/term.py`, `lib/pricing.py` each now read (module name adjusted per file):

```python
"""Moved to core.<name> in 3.0.0 Phase 1 — identity shim, same module object."""
import sys
import core.<name> as _mod
sys.modules[__name__] = _mod
```

`import paths` / `import term` / `import pricing` (the flat style every caller and test already
uses) still work unchanged: `lib/` is on `sys.path` wherever these are imported, and `core/` is a
subpackage of it, so `import core.paths` resolves fine from inside the shim. Monkeypatching (e.g.
`self.mcp._engine()` reaching into `pricing.SONNET5_INTRO_END`, or any test that pokes at
`paths.<attr>`) still lands on the one real module object, since `sys.modules[__name__] = _mod`
makes `paths` and `core.paths` the literal same object.

## C. `lib/core/fsutil.py` — new, atomic write

Created exactly as specified (`atomic_write(path, text)`: pid-suffixed temp file in the same dir +
`os.replace`).

- `lib/obsidian_sync.py`: added `from core.fsutil import atomic_write as _atomic_write  # moved to
  core.fsutil in 3.0.0 Phase 1` next to the other top imports (after `import decisions as _dec`);
  deleted the local `def _atomic_write`. Its old body was:
  ```python
  tmp = "%s.tmp.%d" % (path, os.getpid())
  with open(tmp, "w", encoding="utf-8") as f:
      f.write(text)
  os.replace(tmp, path)
  ```
  — identical to `core.fsutil.atomic_write`, so the alias is behavior-preserving.

- `lib/feeds.py`: same treatment. Its old body was spelled slightly differently
  (`tmp = path + ".tmp." + str(os.getpid())` vs. the `%`-format used in `obsidian_sync`/`fsutil`)
  but produces the identical string and behavior — confirmed **behaviorally identical**, as the
  task called for, and unified onto the one `core.fsutil.atomic_write`.

Both modules still expose `_atomic_write` as a module attribute (now bound to the imported
function, not a local `def`), so `obsidian_sync._atomic_write(...)` and `feeds._atomic_write(...)`
— both called directly by tests (`tests/test_story_groups_export.py`,
`tests/test_category_hubs_export.py`, `tests/test_category_subgroups_export.py`,
`tests/test_feeds.py`) — resolve exactly as before.

## D. `lib/core/jsonrpc.py` — new, stdio JSON-RPC transport

Extracted from `lib/mcp_server.py`:

- `result(mid, payload)`, `error(mid, code, message)` — renamed from `mcp_server`'s `_result`/
  `_error` (no leading underscore — this is the shared module's public surface now).
- `_write(stdout, obj)` — same name, same docstring ("One JSON object per line, no embedded
  newlines, flushed immediately."), same body.
- `serve(handle, stdin=None, stdout=None)` — the exact loop from `mcp_server.serve`, generalized to
  take the dispatch callback (`handle`) as its first argument since the transport is now shared.
  Stderr string kept byte-identical: `"task-station MCP: unhandled: %s\n"`. Parse-error path kept
  byte-identical too (`error(None, -32700, "Parse error: %s" % e)`).

`lib/mcp_server.py` rewiring:

- Added `import core.jsonrpc as _jsonrpc` right after the `_LIB` / `sys.path` bootstrap block (so
  it benefits from the same path setup).
- `_result`/`_error` now delegate: `return _jsonrpc.result(...)` / `return _jsonrpc.error(...)`.
- `_write` now delegates: `return _jsonrpc._write(stdout, obj)`.
- `serve(stdin=None, stdout=None)` kept its exact signature and docstring; body replaced with
  `return _jsonrpc.serve(handle, stdin, stdout)`.
- `handle`, `dispatch`, `main`, every `_tool_*` function, and all tool logic (`_list_tasks`,
  `_create_task`, etc.) — untouched.

Tests call `mcp_server.serve(stdin, stdout)` positionally (see
`tests/test_mcp_server.py::McpProtocolTest._drive` and `test_malformed_line_does_not_crash`) — the
signature is unchanged, so this passes through unmodified to `_jsonrpc.serve(handle, stdin,
stdout)`.

## E. `lib/hookjson.py` — untouched, per instructions (frozen at `lib/` root; executed as a script
by literal path from hooks/tests).

## Verification

Bash is permission-denied in this headless worktree session (`don't ask` mode blocked the shell
outright — same gating noted for this repo before: python3/pytest are gated in headless sessions).
Per the task's own fallback, this was **skipped** — the hub should run:

```
python3 -m unittest tests.test_paths tests.test_pricing tests.test_mcp_server tests.test_feeds tests.test_hookjson -v
```

I traced the changes by hand instead:
- `test_paths.py` / `test_pricing.py` do `sys.path.insert(0, .../lib); import paths` /
  `import pricing` — lands on the shim, which rebinds `sys.modules["paths"]` to `core.paths` before
  any attribute is touched, so `paths.data_dir()` / `pricing.rates_for()` etc. all resolve on the
  real (moved) module.
- `test_mcp_server.py` imports `mcp_server` fresh each test via `importlib.import_module`; the new
  `import core.jsonrpc as _jsonrpc` line resolves because `mcp_server.py` inserts `lib/` onto
  `sys.path` before that import, and `lib/core/` is an existing package (Phase 0 `__init__.py`).
  `serve()`'s new one-line body is a straight pass-through — same stdin/stdout consumption, same
  error shapes.
- `test_feeds.py` / other export tests call `feeds._atomic_write` / `obsidian_sync._atomic_write`
  directly as module attributes — verified both still exist as names bound in each module's
  namespace (via the `from core.fsutil import atomic_write as _atomic_write` alias import).

## Surprises / notes

- This worktree (`claude-todo-worktrees/monorepo-3.0.0`) was already a dedicated git worktree
  (gitdir → `.../claude-todo/.git/worktrees/monorepo-3.0.0`) distinct from the main checkout, so no
  new worktree was created for this session.
- `feeds.py`'s old `_atomic_write` used `path + ".tmp." + str(os.getpid())` instead of
  `"%s.tmp.%d" % (path, os.getpid())` — cosmetically different, same resulting string and
  semantics, so the unification to `core.fsutil.atomic_write` is safe (matches the task's own
  "behaviorally identical" framing).
- After delegating `serve`/`_write` to `core.jsonrpc`, `lib/mcp_server.py`'s top-level `import json`
  (line 34) is no longer used directly by those two functions — it's still reachable only via the
  inner shadowed `import json as _json` inside `_server_version()`. Left the top-level import in
  place since removing it wasn't part of the specified change set and the task said not to touch
  anything beyond the listed rewiring.
- No other files were touched.
