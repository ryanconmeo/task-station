"""Single JSON config store under the data dir, plus the `task-station config` board."""
import json, os
import paths

# Default repo roots scanned for the hub repo index (`task-station repos`) when no
# workspace dirs are configured. Centralized here so both delegate's `--project`
# shorthand and the repo index share one source of truth.
DEFAULT_WORKSPACE_DIRS = ["~/Workspace", "~/Workspace-Other"]

def _path():
    return os.path.join(paths.data_dir(), "config.json")

def _load():
    try:
        with open(_path()) as f:
            return json.load(f)
    except Exception:
        return {}

def _save(d):
    data = paths.data_dir()
    os.makedirs(data, exist_ok=True)
    tmp = _path() + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)
    os.replace(tmp, _path())

def get(key, default=None):
    return _load().get(key, default)

def set(key, value):
    d = _load(); d[key] = value; _save(d)

def unset(key):
    d = _load()
    if key in d:
        del d[key]; _save(d)

def workspace_dirs():
    raw = get("workspace_dirs")
    if raw is None:
        env = os.environ.get("TASK_STATION_WORKSPACE_DIRS", "")
        raw = [p for p in env.split(os.pathsep) if p] if env else []
    return [os.path.expanduser(p) for p in raw]

def repo_roots():
    """Roots to scan for the hub repo index. Precedence: an explicit `repo_roots`
    config list (set via `repos --set-roots` during first-run onboarding) > the
    configured workspace dirs (`--workspace-dirs` / `TASK_STATION_WORKSPACE_DIRS`) >
    DEFAULT_WORKSPACE_DIRS. Unlike delegate's `--project` resolution — which
    deliberately errors when nothing is configured — the repo index has a sensible
    default so the hub can route tasks out of the box."""
    explicit = get("repo_roots")
    if explicit:
        return [os.path.expanduser(p) for p in explicit]
    dirs = workspace_dirs()
    if not dirs:
        dirs = [os.path.expanduser(p) for p in DEFAULT_WORKSPACE_DIRS]
    return dirs

def set_repo_roots(paths_list):
    """Persist the repo-index discovery roots (stored un-expanded). Written by
    `repos --set-roots` once the user confirms the first-run onboarding proposal."""
    set("repo_roots", [p for p in paths_list if p])
    return get("repo_roots")

def repo_roots_configured():
    """True once the user has explicitly chosen discovery roots (either `repo_roots`
    or the legacy `workspace_dirs`). When False AND no manifest exists yet, `repos`
    drives one-time onboarding instead of silently scanning the defaults."""
    return bool(get("repo_roots") or get("workspace_dirs"))

def repo_enrich_enabled():
    """Global kill-switch for repo enrichment egress. Default ON — but this only
    *permits* the per-repo `enrich` manifest flag to take effect; enrichment is OFF
    for every repo by default, so a normal `repos --refresh` still sends nothing.
    `TASK_STATION_REPO_ENRICH=off` or `repo_enrich:false` hard-disables ALL egress
    regardless of per-repo flags (so does `repos --refresh --no-llm` per-call).
    Enrichment always degrades to a deterministic summary regardless."""
    if os.environ.get("TASK_STATION_REPO_ENRICH") == "off":
        return False
    return bool(get("repo_enrich", True))

def bare_commands():
    """True only if the user opted in (config flag or env). Default off."""
    if os.environ.get("TASK_STATION_BARE_CMDS") == "on":
        return True
    return bool(get("bare_commands", False))

def update_check_enabled():
    """True only if the user opted in (config flag). Default off — no network."""
    return bool(get("update_check", False))

def title_enabled():
    """True unless explicitly disabled — default ON. Mirrors TASK_STATION_TINT's
    env escape: `TASK_STATION_TITLE=off` (or `config --title off`) suppresses the
    auto terminal title."""
    if os.environ.get("TASK_STATION_TITLE") == "off":
        return False
    return bool(get("title", True))

def auto_categories_enabled():
    """True unless explicitly disabled — default ON. Mirrors TASK_STATION_TITLE's
    env escape: `TASK_STATION_AUTO_CATEGORIES=off` (or `config --auto-categories off`)
    freezes the enabled set — assigning a task to a disabled slot no longer
    auto-enables it (today's restrict-to-enabled behaviour)."""
    if os.environ.get("TASK_STATION_AUTO_CATEGORIES") == "off":
        return False
    return bool(get("auto_categories", True))

def enabled_categories():
    """The configured active-category key list, or None when unconfigured
    (categories.enabled_keys() then defaults to CORE — the lean default)."""
    raw = get("enabled_categories")
    return raw if isinstance(raw, list) else None

def set_enabled_categories(keys):
    set("enabled_categories", list(keys))

def _categories_module():
    try:
        import categories as _c
        return _c
    except Exception:
        return None

def tint_mode():
    return get("tint_mode", "auto")

def tint_theme():
    """Configured palette: "auto" (follow OS appearance), "dark", or "light"."""
    val = get("tint_theme", "auto")
    return val if val in ("auto", "dark", "light") else "auto"

def _enabled_summary():
    """`3/12 (default: CORE)`-style summary of the active category set, or
    `N/12 (custom)` once the user has configured it."""
    cats = _categories_module()
    if cats is None:
        return "n/a"
    enabled = cats.enabled_keys()
    total = len(cats.all_keys())
    name = "default: CORE" if enabled_categories() is None else "custom"
    return "%d/%d (%s)" % (len(enabled), total, name)

def _desktop_bridge_summary():
    """`installed` / `off` for the no-arg config board (lazy import — setup imports
    config, so keep this out of module scope)."""
    try:
        import setup
        installed, _ = setup.desktop_bridge_status()
        return "installed" if installed else "off"
    except Exception:
        return "off"

def render_board():
    """The unified, width-aware `task-station config` board (no-arg view).

    Short-valued settings render as a 4-column aligned grid (SETTING / VALUE /
    OPTIONS / WHAT IT DOES); the first three columns are sized to their widest cell
    per render, and the description column takes the remaining terminal width,
    wrapping with a hanging indent under WHAT IT DOES so long text never breaks the
    grid. Long PATH-valued settings print as their own full-width two-line blocks
    below the grid. The status facts that used to live in a separate
    setup.status() block are folded into a compact `status` section at the bottom —
    one board, nothing duplicated."""
    import textwrap
    import term
    width = term.width()
    indent = "  "
    gutter = "  "

    cats = get("categories"); n_cat = len(cats) if isinstance(cats, dict) else 0
    lines = []

    # --- top header: store path (+ set/reset hint); store breaks to its own line
    #     rather than overflow the terminal width.
    store_line = "task-station config       store: %s" % _path()
    if len(store_line) <= width:
        lines.append(store_line)
    else:
        lines.append("task-station config")
        lines.append(indent + "store: %s" % _path())
    lines.append(indent + "set: task-station config --<flag> <value>   ·   reset: <flag> default")
    lines.append("")

    # --- toggle grid (short values only) ----------------------------------------
    header = ("SETTING", "VALUE", "OPTIONS", "WHAT IT DOES")
    rows = [
        ("--categories", _enabled_summary(), "edit·toggle",
         "enabled set (CORE default) + toggles"),
        ("--auto-categories", "on" if auto_categories_enabled() else "off", "on · off",
         "grow the board automatically as new categories are assigned"),
        ("category overrides", "%d override(s)" % n_cat if n_cat else "defaults", "edit",
         "custom tags/labels + skill auto-tint"),
        ("--bare-cmds", "on" if bare_commands() else "off", "on · off",
         "install bare /todo + /done (else /task-station:todo)"),
        ("--update-check", "on" if update_check_enabled() else "off", "on · off",
         "opt-in /todo footer when a newer version ships"),
        ("--tint-theme", tint_theme(), "auto · dark · light",
         "tint palette; auto follows OS appearance"),
        ("--title", "on" if title_enabled() else "off", "on · off",
         "auto terminal title '#<seq>: <title>' on attach"),
        ("--desktop-bridge", _desktop_bridge_summary(), "on · off",
         "wire the dependency-free MCP server into Claude Desktop"),
    ]
    w_set = max(len(header[0]), *(len(r[0]) for r in rows))
    w_val = max(len(header[1]), *(len(r[1]) for r in rows))
    w_opt = max(len(header[2]), *(len(r[2]) for r in rows))
    fixed = len(indent) + w_set + len(gutter) + w_val + len(gutter) + w_opt + len(gutter)
    w_desc = max(24, width - fixed)

    def _grid_row(setting, value, options, desc):
        prefix = (indent + setting.ljust(w_set) + gutter
                  + value.ljust(w_val) + gutter + options.ljust(w_opt) + gutter)
        wrapped = textwrap.wrap(desc, w_desc) or [""]
        out = [prefix + wrapped[0]]
        hang = " " * len(prefix)         # aligns continuations under WHAT IT DOES
        out += [hang + cont for cont in wrapped[1:]]
        return out

    lines += _grid_row(*header)
    for r in rows:
        lines += _grid_row(*r)

    # --- long PATH-valued settings: own full-width two-line blocks (never gridded)
    ws = ":".join(get("workspace_dirs") or []) or "(unset — use --repo)"
    lines.append("")
    lines.append(indent + "--workspace-dirs  (repo roots for delegate --project)")
    lines.append(indent + "    " + ws)
    lines.append(indent + "--data-dir        (read-only · $TASK_STATION_HOME)")
    lines.append(indent + "    " + paths.data_dir())

    # --- status: facts folded in from setup.status() (reusing its helpers) -------
    import setup
    t = term.detect()
    has_policy = ("policy" in setup._manifest())
    installed, _server = setup.desktop_bridge_status()
    st = [
        ("tint", "escape (full palette) · terminal %s%s" % (
            t, "" if t != "none" else "  (no supported terminal → no-op)")),
        ("policy", "installed in CLAUDE.md — remove: --policy off" if has_policy
         else "off — install: --policy on"),
        ("desktop-bridge", "installed — remove: --desktop-bridge off" if installed
         else "off — install: --desktop-bridge on"),
    ]
    w_label = max(len(s[0]) for s in st)
    lines.append("")
    lines.append(indent + "status")
    for label, val in st:
        lines.append(indent + "  " + label.ljust(w_label) + "  " + val)

    return "\n".join(lines)

def _categories_status(cats):
    enabled = cats.enabled_keys()
    lines = ["Enabled categories (%d/%d):" % (len(enabled), len(cats.all_keys()))]
    for k in enabled:
        m = cats.CATEGORIES[k]
        perm = "   (permanent)" if k == cats.PERMANENT else ""
        lines.append("  %-7s %s %-11s %s%s" % (k, m["dot"], "[%s]" % m["tag"], m["label"], perm))
    disabled = [k for k in cats.all_keys() if k not in enabled]
    if disabled:
        lines.append("  off: " + ", ".join(disabled))
    lines.append("")
    lines.append("The board starts lean at CORE (BUG · FEATURE · GENERAL) and grows on its own:")
    lines.append("assigning a task to a new category auto-enables that slot. Freeze the set")
    lines.append("with: config --auto-categories off  (currently %s)."
                 % ("on" if auto_categories_enabled() else "off"))
    lines.append("")
    lines.append("Toggle individual slots: config --enable <key> · config --disable <key>")
    lines.append("(⚫ GENERAL is permanent — always on, cannot be disabled.)")
    return "\n".join(lines)


def cmd_categories(arg):
    """Handle `config --categories [...]`:
      (no arg)  → show the enabled set + how to toggle slots
      edit      → print the config.json path (legacy behaviour)
    """
    if arg == ["edit"]:
        print(_path()); return
    cats = _categories_module()
    if cats is None:
        print("categories plugin not available (lib/categories.py missing)"); return
    if arg:
        print("usage: config --categories [edit]"); return
    print(_categories_status(cats))


def toggle_category(color, on):
    """Enable/disable a single slot. Refuses to disable ⚫ GENERAL (permanent).
    Materializes the current effective set first, so toggling from the
    unconfigured (full) default behaves intuitively."""
    cats = _categories_module()
    if cats is None:
        print("categories plugin not available (lib/categories.py missing)"); return
    key = cats.resolve(color)
    if key is None:
        print("Unknown category '%s'. Use a key, emoji, or [TAG]." % color); return
    m = cats.CATEGORIES[key]
    if not on and key == cats.PERMANENT:
        print("Refusing to disable %s [%s] — GENERAL is permanent." % (m["dot"], m["tag"])); return
    cur = list(cats.enabled_keys())
    if on and key not in cur:
        cur.append(key)
    elif not on:
        cur = [k for k in cur if k != key]
    if cats.PERMANENT not in cur:
        cur.append(cats.PERMANENT)
    keys = [k for k in cats.all_keys() if k in cur]
    set_enabled_categories(keys)
    print("%s %s [%s] — enabled set now: %s"
          % ("enabled" if on else "disabled", m["dot"], m["tag"], " ".join(keys)))


def cmd_config(a):
    if getattr(a, "workspace_dirs_get", False):
        print(":".join(get("workspace_dirs") or "")); return
    if a.workspace_dirs is not None:
        set("workspace_dirs", [p for p in a.workspace_dirs.split(os.pathsep) if p])
        print("workspace_dirs = %s" % ":".join(get("workspace_dirs"))); return
    if getattr(a, "bare_cmds", None) is not None:
        set("bare_commands", a.bare_cmds == "on")
        print("bare_commands = %s" % ("on" if get("bare_commands") else "off")); return
    if getattr(a, "bare_cmds_get", False):
        print("on" if bare_commands() else "off"); return
    if getattr(a, "update_check", None) is not None:
        set("update_check", a.update_check == "on")
        print("update_check = %s" % ("on" if get("update_check") else "off")); return
    if getattr(a, "update_check_get", False):
        print("on" if update_check_enabled() else "off"); return
    if getattr(a, "tint_theme", None) is not None:
        set("tint_theme", a.tint_theme)
        print("tint_theme = %s" % tint_theme()); return
    if getattr(a, "tint_theme_get", False):
        print(tint_theme()); return
    if getattr(a, "title", None) is not None:
        set("title", a.title == "on")
        print("title = %s" % ("on" if get("title") else "off")); return
    if getattr(a, "title_get", False):
        print("on" if title_enabled() else "off"); return
    if getattr(a, "auto_categories", None) is not None:
        set("auto_categories", a.auto_categories == "on")
        print("auto_categories = %s" % ("on" if get("auto_categories") else "off")); return
    if getattr(a, "auto_categories_get", False):
        print("on" if auto_categories_enabled() else "off"); return
    if getattr(a, "categories", None) is not None:
        return cmd_categories(a.categories);
    if getattr(a, "enable", None) is not None:
        return toggle_category(a.enable, True)
    if getattr(a, "disable", None) is not None:
        return toggle_category(a.disable, False)
    import setup
    if getattr(a, "policy", None) is not None:
        print(setup.set_policy(a.policy == "on")); return
    if getattr(a, "desktop_bridge", None) is not None:
        print(setup.install_desktop_bridge() if a.desktop_bridge == "on"
              else setup.remove_desktop_bridge()); return
    # No flags: the single unified settings + status board. The status facts are
    # folded into render_board() now, so we no longer print setup.status() here
    # (setup.status() is unchanged and still used by the install flow).
    print(render_board())
