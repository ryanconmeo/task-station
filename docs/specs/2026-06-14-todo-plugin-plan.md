# claude-todo Plugin Repackage — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repackage the `claude-todo` clone-and-merge module into a native Claude Code plugin (`.claude-plugin` manifest + marketplace, declarative hooks, auto-discovered commands) while relocating all mutable state to a stable, update-surviving data dir, with seamless migration from the legacy install.

**Architecture:** Introduce a shared `paths.py` data-dir resolver (`$CLAUDE_TODO_HOME` → `${CLAUDE_CONFIG_DIR:-~/.claude}/todo-data` → XDG fallback) used by `todo.py`, `categories.py`, and `delegate.py`; route `store/`, `workers.json`, and `pending-briefs/` through it while keeping `__file__` strictly for code self-location. Then restructure the repo into the plugin layout (`lib/`, `hooks/hooks.json`, `.claude-plugin/`) and repath hooks/commands to `${CLAUDE_PLUGIN_ROOT}`. Tests use stdlib `unittest` (project is stdlib-only, Python 3.9); install/hook wiring is verified by a manual checklist.

**Tech Stack:** Python 3.9 stdlib only (`json`, `shutil`, `unittest`, `tempfile`), Bash hooks, JSON manifests. No new dependencies.

**Spec:** `docs/specs/2026-06-14-todo-plugin-design.md`

---

## File Structure

- **Create `paths.py`** — single source of truth for the data dir. One function, `data_dir()`. Imported by the other three modules.
- **Modify `todo.py`** — replace `BASE`-derived state paths (`todo.py:34-38`) with `paths.data_dir()`; keep `BASE` only for self-invocation strings; add `_maybe_migrate()` + a `migrate` subcommand.
- **Modify `categories.py`** — keep shipped taxonomy as defaults; merge user overrides from `<data_dir>/categories.json`; gate tinting to macOS.
- **Modify `delegate/delegate.py`** — route `workers.json` (`delegate.py:46`) through `paths.data_dir()`.
- **Create `tests/test_paths.py`, `tests/test_migrate.py`, `tests/test_categories_overrides.py`** — stdlib unittest.
- **Restructure** — move `todo.py`, `categories.py`, `paths.py`, `delegate/`, `close-session-window.sh`, `open-session-window.sh` into `lib/`; move the four `on_*.sh` into `hooks/`; create `hooks/hooks.json`, `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`; repath hooks + `commands/*.md` to `${CLAUDE_PLUGIN_ROOT}`.
- **Modify `README.md`, `CATEGORIES.md`** — `/plugin`-first install; legacy uninstall step.

State at runtime lives in `<data_dir>/{store/,workers.json,pending-briefs/,categories.json,.migrated}`.

---

## Task 1: Shared data-dir resolver (`paths.py`)

**Files:**
- Create: `paths.py`
- Test: `tests/test_paths.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_paths.py
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import paths

class DataDirResolution(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("CLAUDE_TODO_HOME", "CLAUDE_CONFIG_DIR", "XDG_STATE_HOME")}
        for k in self._saved:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v

    def test_explicit_override_wins(self):
        os.environ["CLAUDE_TODO_HOME"] = "/tmp/td-home"
        os.environ["CLAUDE_CONFIG_DIR"] = "/tmp/cfg"
        self.assertEqual(paths.data_dir(), "/tmp/td-home")

    def test_config_dir_then_todo_data(self):
        os.environ["CLAUDE_CONFIG_DIR"] = "/tmp/cfg"
        self.assertEqual(paths.data_dir(), "/tmp/cfg/todo-data")

    def test_xdg_only_when_config_dir_unset(self):
        os.environ["XDG_STATE_HOME"] = "/tmp/xdg"
        self.assertEqual(paths.data_dir(), "/tmp/xdg/claude-todo")

    def test_default(self):
        self.assertEqual(paths.data_dir(),
                         os.path.expanduser("~/.claude/todo-data"))

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/.claude/todo-worktrees/plugin-rework && python3 -m unittest tests.test_paths -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'paths'`

- [ ] **Step 3: Write minimal implementation**

```python
# paths.py
"""Resolve the stable, version-independent home for claude-todo's mutable state.

Must NOT live inside the plugin dir: a plugin installs to a versioned cache that
is replaced on every `/plugin update`, which would destroy task history. Anchored
to CLAUDE_CONFIG_DIR (Claude Code's own relocation primitive) so it tracks a moved
~/.claude, with an explicit override and an XDG courtesy fallback.
"""
import os


def data_dir():
    override = os.environ.get("CLAUDE_TODO_HOME")
    if override:
        return os.path.expanduser(override)
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    if cfg:
        return os.path.join(os.path.expanduser(cfg), "todo-data")
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return os.path.join(xdg, "claude-todo")
    return os.path.expanduser("~/.claude/todo-data")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_paths -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add paths.py tests/test_paths.py
git commit -m "feat: add shared data-dir resolver (paths.py)"
```

---

## Task 2: Route state paths through the resolver (`todo.py`, `delegate.py`)

**Files:**
- Modify: `todo.py:33-38`
- Modify: `delegate/delegate.py:44-47`

- [ ] **Step 1: Repoint state globals in `todo.py`**

Replace lines 33-38:

```python
BASE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(BASE, "store")
TASKS_DIR = os.path.join(STORE, "tasks")
LINKS_DIR = os.path.join(STORE, "links")
DELEGATE_REGISTRY = os.path.join(BASE, "delegate", "workers.json")
PROJECTS_ROOT = os.path.expanduser("~/.claude/projects")
```

with:

```python
import paths

BASE = os.path.dirname(os.path.abspath(__file__))  # code location only (self-invocation)
DATA = paths.data_dir()                             # mutable state — survives /plugin update
STORE = os.path.join(DATA, "store")
TASKS_DIR = os.path.join(STORE, "tasks")
LINKS_DIR = os.path.join(STORE, "links")
PENDING_BRIEFS = os.path.join(DATA, "pending-briefs")
DELEGATE_REGISTRY = os.path.join(DATA, "workers.json")
PROJECTS_ROOT = os.path.join(
    os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")), "projects")
```

(`.edited`/`.blocked` markers are built from `_link_path()` under `LINKS_DIR` — `todo.py:327` — so they follow `STORE` automatically. `BASE`-built self-invocation strings at `todo.py:816` stay as-is: they invoke the engine, not state.)

- [ ] **Step 2: Repoint `workers.json` in `delegate/delegate.py`**

Replace lines 45-46:

```python
REG_DIR = os.path.dirname(os.path.abspath(__file__))           # ~/.claude/todo/delegate
REG = os.path.join(REG_DIR, "workers.json")
```

with:

```python
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import paths
REG_DIR = paths.data_dir()                                     # state dir (survives update)
REG = os.path.join(REG_DIR, "workers.json")
```

(Keep `TODO_PY` at `delegate.py:47` — it resolves the sibling engine via `__file__` and is correct after the restructure. Confirm `import sys` is present near the top of `delegate.py`; add it if missing.)

- [ ] **Step 3: Verify the engine still runs and creates state in the new dir**

Run:
```bash
rm -rf /tmp/td && CLAUDE_TODO_HOME=/tmp/td \
  python3 todo.py create --session smoke --color black --effort s \
  --title "smoke" --summary "smoke test" && ls -R /tmp/td
```
Expected: prints "Created and attached…"; `/tmp/td/store/tasks/<uuid>.json` and `/tmp/td/store/links/smoke` exist; no `store/` created under the repo.

- [ ] **Step 4: Commit**

```bash
git add todo.py delegate/delegate.py
git commit -m "feat: route store/registry/projects through the data-dir resolver"
```

---

## Task 3: Auto-migration + explicit `migrate` command

**Files:**
- Modify: `todo.py` (add `_migrate()` near the path globals; add `_maybe_migrate()` call at the top of `main()`; register a `migrate` subparser by `todo.py:1437`)
- Test: `tests/test_migrate.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_migrate.py
import os, sys, json, tempfile, shutil, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import todo

class Migrate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.legacy = os.path.join(self.tmp, "legacy")
        self.data = os.path.join(self.tmp, "data")
        os.makedirs(os.path.join(self.legacy, "store", "tasks"))
        os.makedirs(os.path.join(self.legacy, "store", "links"))
        with open(os.path.join(self.legacy, "store", "tasks", "x.json"), "w") as f:
            json.dump({"id": "x"}, f)
        os.makedirs(os.path.join(self.legacy, "delegate"))
        with open(os.path.join(self.legacy, "delegate", "workers.json"), "w") as f:
            json.dump({"w": 1}, f)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_copies_legacy_store_and_registry(self):
        moved = todo._migrate(os.path.join(self.legacy, "store"), self.data)
        self.assertTrue(moved)
        self.assertTrue(os.path.isfile(
            os.path.join(self.data, "store", "tasks", "x.json")))
        self.assertTrue(os.path.isfile(os.path.join(self.data, "workers.json")))
        self.assertTrue(os.path.isfile(os.path.join(self.data, ".migrated")))
        # legacy left intact (copy, not move)
        self.assertTrue(os.path.isfile(
            os.path.join(self.legacy, "store", "tasks", "x.json")))

    def test_idempotent(self):
        self.assertTrue(todo._migrate(os.path.join(self.legacy, "store"), self.data))
        self.assertFalse(todo._migrate(os.path.join(self.legacy, "store"), self.data))

    def test_skips_when_data_store_exists(self):
        os.makedirs(os.path.join(self.data, "store"))
        self.assertFalse(todo._migrate(os.path.join(self.legacy, "store"), self.data))

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_migrate -v`
Expected: FAIL — `AttributeError: module 'todo' has no attribute '_migrate'`

- [ ] **Step 3: Write the implementation**

Add near the path globals in `todo.py` (after Task 2's block), and ensure `import shutil` is present:

```python
LEGACY_STORE = os.path.expanduser("~/.claude/todo/store")

def _migrate(src_store, data, marker_name=".migrated"):
    """One-time idempotent COPY of legacy state into the data dir. Returns True if it ran.
    Copy (not move) leaves the old clone intact as a backup."""
    marker = os.path.join(data, marker_name)
    dst_store = os.path.join(data, "store")
    if os.path.exists(marker) or os.path.isdir(dst_store):
        return False
    if not os.path.isdir(src_store):
        return False
    os.makedirs(data, exist_ok=True)
    shutil.copytree(src_store, dst_store)
    legacy_base = os.path.dirname(src_store)
    reg = os.path.join(legacy_base, "delegate", "workers.json")
    if os.path.isfile(reg):
        shutil.copy2(reg, os.path.join(data, "workers.json"))
    pb = os.path.join(legacy_base, "pending-briefs")
    if os.path.isdir(pb):
        shutil.copytree(pb, os.path.join(data, "pending-briefs"))
    with open(marker, "w") as f:
        f.write("migrated from %s\n" % src_store)
    return True

def _maybe_migrate():
    try:
        _migrate(LEGACY_STORE, DATA)
    except Exception:
        pass  # never let a migration hiccup block the tracker
```

Add a `migrate` subparser next to the others (by `todo.py:1437`):

```python
sp = sub.add_parser("migrate"); sp.add_argument("--from", dest="src", default=LEGACY_STORE)
```

Add a handler in the command dispatch (follow the existing `if a.cmd == ...` pattern):

```python
if a.cmd == "migrate":
    ran = _migrate(a.src, DATA)
    print("Migrated from %s" % a.src if ran else "Nothing to migrate (already done or no source).")
    return
```

Call `_maybe_migrate()` as the first line inside `main()` (before subcommand dispatch).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_migrate -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add todo.py tests/test_migrate.py
git commit -m "feat: auto-migrate legacy state on first run + explicit migrate command"
```

---

## Task 4: Externalize category config (`categories.py`)

**Files:**
- Modify: `categories.py` (after the `CATEGORIES`/`SKILL_COLORS` definitions and `_TAG_WIDTH` at `categories.py:45`)
- Test: `tests/test_categories_overrides.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_categories_overrides.py
import os, sys, json, tempfile, importlib, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

class Overrides(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["CLAUDE_TODO_HOME"] = self.tmp
        with open(os.path.join(self.tmp, "categories.json"), "w") as f:
            json.dump({"tint_terminal": False,
                       "categories": {"teal": {"dot": "🟦", "tag": "TEAL", "label": "ops"}}},
                      f)

    def tearDown(self):
        os.environ.pop("CLAUDE_TODO_HOME", None)

    def test_user_override_merges_over_defaults(self):
        import categories
        importlib.reload(categories)            # re-run module-load merge
        self.assertIn("teal", categories.CATEGORIES)
        self.assertIn("red", categories.CATEGORIES)     # defaults still present
        self.assertFalse(categories.TINT_TERMINAL)

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_categories_overrides -v`
Expected: FAIL — `AssertionError: 'teal' not found in {...}`

- [ ] **Step 3: Write the implementation**

After `_TAG_WIDTH = …` (`categories.py:45`), insert:

```python
import json as _json
import sys as _sys
import paths as _paths

def _apply_overrides():
    """Merge user overrides from <data_dir>/categories.json over the shipped defaults,
    so customizations survive `/plugin update`. Absent/invalid file → defaults unchanged."""
    global TINT_TERMINAL, SKILL_COLORS, _TAG_WIDTH
    cfg = os.path.join(_paths.data_dir(), "categories.json")
    if not os.path.isfile(cfg):
        return
    try:
        with open(cfg) as f:
            data = _json.load(f)
    except Exception:
        return
    if isinstance(data.get("categories"), dict):
        CATEGORIES.update(data["categories"])
    if "tint_terminal" in data:
        TINT_TERMINAL = bool(data["tint_terminal"])
    if isinstance(data.get("skill_colors"), list):
        SKILL_COLORS = [tuple(x) for x in data["skill_colors"]] + SKILL_COLORS
    _TAG_WIDTH = max(len(m["tag"]) for m in CATEGORIES.values()) + 2

_apply_overrides()
```

(`import os` already exists at the top of `categories.py`; if not, add it.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_categories_overrides -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add categories.py tests/test_categories_overrides.py
git commit -m "feat: merge user category overrides from data-dir categories.json"
```

---

## Task 5: Gate macOS-only behaviour

**Files:**
- Modify: `categories.py` (the `tint_cmd`/tint helper at `categories.py:161`)
- Modify: `open-session-window.sh`, `close-session-window.sh` (top of each)

- [ ] **Step 1: Gate tint suggestions to macOS in `categories.py`**

The tint helper is `tint_command(color)` at `categories.py:158`. Change its guard so it also requires macOS:

```python
def tint_command(color):
    """The shell command that tints the terminal to `color`, or None when
    terminal tinting is disabled or the platform isn't macOS."""
    if not (TINT_TERMINAL and _sys.platform == "darwin"):
        return None
    return "zsh -ic '%s'" % normalize(color)
```

(`_sys` was imported in Task 4.)

- [ ] **Step 2: Guard the window scripts**

Add as the first executable line (after the shebang) of **both** `open-session-window.sh` and `close-session-window.sh`:

```bash
[[ "$OSTYPE" == darwin* ]] || exit 0
```

- [ ] **Step 3: Verify off-mac degradation**

Run:
```bash
python3 -c "import sys; sys.platform='linux'; import categories; print(categories.tint_cmd('green'))"
```
Expected: prints `None` (no tint command emitted when not macOS).

- [ ] **Step 4: Commit**

```bash
git add categories.py open-session-window.sh close-session-window.sh
git commit -m "feat: gate Terminal.app tint + window control to macOS"
```

---

## Task 6: Restructure into the native plugin layout

**Files:**
- Move into `lib/`: `todo.py`, `categories.py`, `paths.py`, `delegate/`, `open-session-window.sh`, `close-session-window.sh`
- Move into `hooks/`: `on_session_start.sh`, `on_user_prompt.sh`, `on_post_tool.sh`, `on_stop.sh`
- Create: `hooks/hooks.json`, `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`
- Modify: the four `hooks/on_*.sh`, `commands/todo.md:8`, `commands/done.md`

- [ ] **Step 1: Move code + hooks with git**

```bash
mkdir -p lib hooks .claude-plugin
git mv todo.py categories.py paths.py lib/
git mv delegate lib/delegate
git mv open-session-window.sh close-session-window.sh lib/
git mv on_session_start.sh on_user_prompt.sh on_post_tool.sh on_stop.sh hooks/
```

(Sibling imports survive: `todo.py`, `categories.py`, `paths.py` are all in `lib/`; `delegate.py`'s `sys.path` insert from Task 2 points at `lib/`. `tests/*` already insert the repo root and import the modules from `lib/` — update those inserts in Step 4.)

- [ ] **Step 2: Create `hooks/hooks.json`**

```json
{
  "hooks": {
    "SessionStart": [
      { "matcher": "", "hooks": [
        { "type": "command", "command": "bash \"${CLAUDE_PLUGIN_ROOT}/hooks/on_session_start.sh\"" } ] }
    ],
    "UserPromptSubmit": [
      { "matcher": "", "hooks": [
        { "type": "command", "command": "bash \"${CLAUDE_PLUGIN_ROOT}/hooks/on_user_prompt.sh\"" } ] }
    ],
    "PostToolUse": [
      { "matcher": "Write|Edit|NotebookEdit", "hooks": [
        { "type": "command", "command": "bash \"${CLAUDE_PLUGIN_ROOT}/hooks/on_post_tool.sh\"" } ] }
    ],
    "Stop": [
      { "matcher": "", "hooks": [
        { "type": "command", "command": "bash \"${CLAUDE_PLUGIN_ROOT}/hooks/on_stop.sh\"" } ] }
    ]
  }
}
```

- [ ] **Step 3: Repath the hook scripts**

In all four `hooks/on_*.sh`: replace every `"$HOME/.claude/todo/todo.py"` with `"${CLAUDE_PLUGIN_ROOT}/lib/todo.py"`. In `hooks/on_session_start.sh`, **delete the command-copy block (old lines 14-21)** and its loop — the plugin auto-discovers `commands/`, so the self-heal is obsolete. Leave the `python3 … session-start` / `session-title` calls (now pointing at `${CLAUDE_PLUGIN_ROOT}/lib/todo.py`).

- [ ] **Step 4: Fix test sys.path inserts**

In each `tests/test_*.py`, change the import bootstrap from the repo root to `lib/`:

```python
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
```

- [ ] **Step 5: Repath the slash commands**

In `commands/todo.md:8` replace `python3 "$HOME/.claude/todo/todo.py" render` with `python3 "${CLAUDE_PLUGIN_ROOT}/lib/todo.py" render`. Apply the same `$HOME/.claude/todo/todo.py` → `${CLAUDE_PLUGIN_ROOT}/lib/todo.py` swap anywhere in `commands/done.md`.

- [ ] **Step 6: Create `.claude-plugin/plugin.json`**

```json
{
  "name": "claude-todo",
  "description": "Persistent cross-session task tracking (/todo, /done) with auto-attach, an optional enforcement gate, category colours, and in-project worker delegation.",
  "version": "1.0.0",
  "author": { "name": "Ryan Nguyen", "url": "https://github.com/ryanconmeo" },
  "homepage": "https://github.com/ryanconmeo/claude-todo",
  "repository": "https://github.com/ryanconmeo/claude-todo",
  "license": "MIT",
  "keywords": ["todo", "task-tracking", "hooks", "productivity", "delegation"]
}
```

- [ ] **Step 7: Create `.claude-plugin/marketplace.json`**

```json
{
  "name": "ryanconmeo",
  "owner": { "name": "Ryan Nguyen", "url": "https://github.com/ryanconmeo" },
  "plugins": [
    {
      "name": "claude-todo",
      "source": "./",
      "description": "Persistent cross-session task tracking with auto-attach, enforcement gate, category colours, and in-project worker delegation.",
      "version": "1.0.0",
      "license": "MIT"
    }
  ]
}
```

- [ ] **Step 8: Re-run the whole test suite from the new layout**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS (all tests from Tasks 1, 3, 4).

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "refactor: restructure into native plugin layout (lib/, hooks/, .claude-plugin/)"
```

---

## Task 7: Docs — `/plugin`-first install + legacy uninstall

**Files:**
- Modify: `README.md`, `CATEGORIES.md`
- Create: `LICENSE` (MIT)

- [ ] **Step 1: Rewrite the install section of `README.md`**

Lead with the plugin flow; keep clone as fallback:

```markdown
## Install

```bash
/plugin marketplace add ryanconmeo/claude-todo
/plugin install claude-todo
```

That wires the `/todo` + `/done` commands and all four hooks automatically — no
`settings.json` edit, no command copy. Task data lives in
`${CLAUDE_CONFIG_DIR:-~/.claude}/todo-data/` (override with `$CLAUDE_TODO_HOME`)
and **survives `/plugin update`**.

### Upgrading from the legacy clone
Your existing tasks migrate automatically on first run (copied, not moved, so the
old `~/.claude/todo/` stays as a backup). Then remove the old wiring:
- delete the four `~/.claude/todo/*.sh` hook entries from `~/.claude/settings.json`
- delete `~/.claude/commands/{todo,done}.md` (the plugin ships its own)
- optionally `rm -rf ~/.claude/todo` once you've confirmed the migration

To re-run or inspect migration manually: `claude-todo migrate --from <path>` (or
`python3 "$CLAUDE_PLUGIN_ROOT/lib/todo.py" migrate`).
```

Update the **Uninstall** and **Files** sections to the `lib/`, `hooks/`, `.claude-plugin/` layout and the data-dir location.

- [ ] **Step 2: Update `CATEGORIES.md`**

Document the new override path: ship defaults in `lib/categories.py`; users customize via `<data_dir>/categories.json` (`{"categories": {...}, "tint_terminal": false, "skill_colors": [["regex","color"]]}`) instead of editing the shipped file. Note tinting is macOS-only.

- [ ] **Step 3: Add `LICENSE`**

Create a standard MIT `LICENSE` with copyright `2026 Ryan Nguyen`.

- [ ] **Step 4: Commit**

```bash
git add README.md CATEGORIES.md LICENSE
git commit -m "docs: /plugin-first install, legacy migration/uninstall, MIT license"
```

---

## Task 8: End-to-end verification (manual checklist)

**Files:** none (verification only)

- [ ] **Step 1: Clean-room smoke test of the engine**

Run:
```bash
rm -rf /tmp/td-e2e && CLAUDE_TODO_HOME=/tmp/td-e2e \
  python3 lib/todo.py session-start --session e2e --source startup
```
Expected: prints the open-tasks context block without error; `/tmp/td-e2e/` is created.

- [ ] **Step 2: Migration dry-run against a copy of the real legacy store**

Run:
```bash
rm -rf /tmp/td-mig && cp -R ~/.claude/todo/store /tmp/legacy-store 2>/dev/null
CLAUDE_TODO_HOME=/tmp/td-mig python3 lib/todo.py migrate --from /tmp/legacy-store
CLAUDE_TODO_HOME=/tmp/td-mig python3 lib/todo.py render --session e2e --arg ""
```
Expected: "Migrated from …"; the render lists the 6 real open tasks. The original `~/.claude/todo/store` is untouched.

- [ ] **Step 3: Install the plugin locally and confirm wiring**

In a scratch Claude Code session:
```
/plugin marketplace add ~/.claude/todo-worktrees/plugin-rework
/plugin install claude-todo
```
Restart, then verify: SessionStart injects the todo context; `/todo` renders the (auto-migrated) list; `/todo <n>` attaches + recaps; `/done` closes; editing a file in an untracked session triggers the gate. No `settings.json` edit was performed.

- [ ] **Step 4: Simulate `/plugin update` and confirm state survives**

Bump `version` to `1.0.1` in both manifests, reinstall/update the plugin, and re-run `/todo`.
Expected: all tasks still present (they live in `todo-data/`, not the replaced cache dir).

- [ ] **Step 5: Record results**

Append a short "Verification — 2026-06-14" note to the spec capturing pass/fail for Steps 1-4 and any deviations, so the PR reviewer sees evidence.

- [ ] **Step 6: Commit**

```bash
git add docs/specs/2026-06-14-todo-plugin-design.md
git commit -m "docs: record end-to-end verification results"
```

---

## Self-Review

- **Spec coverage:** Goals 1-2 (install/hooks) → Tasks 6-7; Goal 3 (no hardcoded paths) → Tasks 1-2, 6; Goal 4 (migration) → Task 3; Goal 5 (category config survives) → Task 4; Goal 6 (macOS gating) → Task 5; Goal 7 (retain commands/params) → Task 6 Step 5 (repath only, no behavioural change) + Task 8 Step 3. All success criteria map to Task 8 steps.
- **Placeholder scan:** none — every code/JSON step shows full content; the one spec-deferred item (config format) is resolved to JSON here.
- **Type/name consistency:** `paths.data_dir()` (Task 1) used identically in Tasks 2-4; `_migrate(src_store, data)` signature matches its test and CLI handler (Task 3); `tint_command(color)` matches the verified name at `categories.py:158` (Task 5); `lib/`-relative `sys.path` insert applied consistently (Tasks 1-4 use repo-root insert pre-move, Task 6 Step 4 switches them to `lib/`).
- **Verified against source:** `tint_command` (`categories.py:158`), state globals (`todo.py:33-38`), `workers.json` path (`delegate.py:46`), and `${CLAUDE_PLUGIN_ROOT}` availability in command bodies were all confirmed against the actual code/installed plugins before writing — no unverified assumptions remain.
