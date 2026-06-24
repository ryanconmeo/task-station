"""Single JSON config store under the data dir, plus the `task-station config` board."""
import copy, json, os, re
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

def tint_enabled():
    """True unless explicitly disabled — default ON. The env escape
    `TASK_STATION_TINT` (on/off/1/0/true/false) WINS over config (so a one-off
    `TASK_STATION_TINT=on` re-enables a config `tint=off`, and vice-versa); else
    the persisted `tint` flag (default ON). Gates every terminal-tint emitter."""
    env = os.environ.get("TASK_STATION_TINT")
    if env is not None:
        return env.strip().lower() in ("on", "1", "true")
    return bool(get("tint", True))

def statusline_enabled():
    """False unless explicitly enabled — default OFF (opt-in; writes to the user's
    settings.json). The env escape `TASK_STATION_STATUSLINE` (on/off/1/0/true/false)
    WINS over config; else the persisted `statusline` flag (default off). Gates the
    SessionStart provider drop-in. Mirrors tint_enabled()/guaranteed_tracking_enabled()."""
    env = os.environ.get("TASK_STATION_STATUSLINE")
    if env is not None:
        return env.strip().lower() in ("on", "1", "true")
    return bool(get("statusline", False))

def auto_categories_enabled():
    """True unless explicitly disabled — default ON. Mirrors TASK_STATION_TITLE's
    env escape: `TASK_STATION_AUTO_CATEGORIES=off` (or `config --auto-categories off`)
    freezes the enabled set — assigning a task to a disabled slot no longer
    auto-enables it (today's restrict-to-enabled behaviour)."""
    if os.environ.get("TASK_STATION_AUTO_CATEGORIES") == "off":
        return False
    return bool(get("auto_categories", True))

def guaranteed_tracking_enabled():
    """False unless explicitly enabled — default OFF. When ON, the UserPromptSubmit
    hook deterministically creates+attaches a provisional task on a fresh, unattached,
    non-skipped session instead of merely nudging. Env override
    `TASK_STATION_GUARANTEED_TRACKING` (on/off/1/0/true/false) wins over config."""
    env = os.environ.get("TASK_STATION_GUARANTEED_TRACKING")
    if env is not None:
        return env.strip().lower() in ("on", "1", "true")
    return bool(get("guaranteed_tracking", False))

def ultracode_hints_enabled():
    """True unless explicitly disabled — default ON. The env escape
    `TASK_STATION_ULTRACODE_HINTS` (on/off/1/0/true/false) WINS over config; else
    the persisted `ultracode_hints` flag (default on). Gates EVERY ultracode
    fan-out hint — the human advisory (detail recap + SessionStart) and the
    model-facing steering on an ultracode turn. Mirrors tint_enabled()/
    statusline_enabled()."""
    env = os.environ.get("TASK_STATION_ULTRACODE_HINTS")
    if env is not None:
        return env.strip().lower() in ("on", "1", "true")
    return bool(get("ultracode_hints", True))


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
    """The appearance control `tint_theme` ("auto" | "dark" | "light"), default
    "auto". "auto" follows the OS appearance; "dark"/"light" force the variant. This
    picks which VARIANT of the active theme renders (dark → Dark Sands, light → Light
    Sands for the shipped `sands` theme)."""
    val = get("tint_theme", "auto")
    return val if val in ("auto", "dark", "light") else "auto"

def active_theme():
    """The active theme NAME: config `theme`, validated against the available themes
    (shipped `sands` + any user themes), falling back to 'sands' for an absent/unknown
    value. The active theme supplies every category's full palette in two variants —
    the appearance (tint_theme) picks which renders. See
    categories.effective_themes / resolve_variant / tint_escape."""
    cats = _categories_module()
    default = getattr(cats, "DEFAULT_THEME", "sands") if cats else "sands"
    name = get("theme", default)
    if cats is None:
        return default
    try:
        avail = cats.available_themes()
    except Exception:
        return default
    return name if name in avail else default

def resolved_variant():
    """The variant ('dark'/'light') that will actually render, given tint_theme +
    the OS appearance. Thin wrapper over categories.resolve_variant for the board."""
    cats = _categories_module()
    if cats is None:
        return "dark"
    try:
        return cats.resolve_variant()
    except Exception:
        return "dark"

def _variant_label(variant, theme=None):
    """'{Dark|Light} {ThemeDisplay}' for the active theme's variant, via
    categories.variant_label (falls back to a bare capitalised variant)."""
    cats = _categories_module()
    theme = theme or active_theme()
    if cats is not None and hasattr(cats, "variant_label"):
        try:
            return cats.variant_label(theme, variant)
        except Exception:
            pass
    return variant.capitalize()

def _enabled_summary():
    """`3/12 (CORE)`-style summary of the active category set, or `N/12 (custom)`
    once the user has configured it. The factory default is carried in the board
    description parens now, so this never embeds the word "default"."""
    cats = _categories_module()
    if cats is None:
        return "n/a"
    enabled = cats.enabled_keys()
    total = len(cats.all_keys())
    name = "CORE" if enabled_categories() is None else "custom"
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

def _statusline_summary():
    """`installed (host)` / `provider-only` / `off` for the no-arg config board
    (lazy import — setup imports config, so keep this out of module scope)."""
    try:
        import setup
        return setup.statusline_status()
    except Exception:
        return "off"

def render_board():
    """The unified, width-aware `task-station config` board (no-arg view).

    Every setting renders as a two-line STANZA: an aligned
    `<flag>  <current value>  <options>` line, then an indented description that
    ends with the factory default in parens — `(default: X)`. A blank line
    separates every stanza. The flag / value / options columns are sized to their
    widest cell per render (the path-valued rows are excluded from the value width
    so a long path never inflates the grid); on a narrow terminal the description
    wraps with a hanging indent under itself, never under a column. The former
    separate `status`, `--workspace-dirs`, and `--data-dir` blocks are folded into
    this single list — one board, nothing duplicated, no `* = default` markers."""
    import textwrap
    import term
    import setup
    width = term.width()
    indent = "  "
    gutter = "  "
    desc_indent = "      "   # 6 cols — the description hangs under the flag, not a column

    cats = get("categories"); n_cat = len(cats) if isinstance(cats, dict) else 0
    has_policy = ("policy" in setup._manifest())

    # (flag, value, options-or-None, description). options=None marks a value-only
    # row (the paths) that carries no OPTIONS cell. The VALUE column always shows
    # the CURRENT value; the factory default lives only in the description parens.
    rows = [
        ("--categories", _enabled_summary(), "edit · toggle",
         "enabled category set — starts lean at CORE (BUG · FEATURE · GENERAL), grows itself (default: CORE)"),
        ("--auto-categories", "on" if auto_categories_enabled() else "off", "on · off",
         "auto-enable a slot the first time a task is assigned to it (default: on)"),
        ("--category-overrides", "%d override(s)" % n_cat if n_cat else "none", "edit",
         "custom tags / labels / skill auto-tint, edit config.json (default: none)"),
        ("--bare-cmds", "on" if bare_commands() else "off", "on · off",
         "install bare /todo + /done aliases, else /task-station:todo (default: off)"),
        ("--update-check", "on" if update_check_enabled() else "off", "on · off",
         "/todo footer when a newer version ships, one git ls-remote/day (default: off)"),
        ("--theme", active_theme(), "sands · …",
         "active colour theme — full palette, dark + light variants (default: sands)"),
        ("--tint-theme", tint_theme(), "auto · dark · light",
         "appearance: which variant renders — auto follows the OS (default: auto)"),
        ("--tint", "on" if tint_enabled() else "off", "on · off",
         "full-palette terminal tint via escape codes; TASK_STATION_TINT overrides (default: on)"),
        ("--title", "on" if title_enabled() else "off", "on · off",
         "auto terminal title '#<seq>: <title>' on attach (default: on)"),
        ("--guaranteed-tracking", "on" if guaranteed_tracking_enabled() else "off", "on · off",
         "hook creates+attaches a provisional task on a fresh session; GC'd if untouched (default: off)"),
        ("--ultracode-hints", "on" if ultracode_hints_enabled() else "off", "on · off",
         "suggest multi-agent breadth (ultracode) on big / RESEARCH / REVIEW / DATA tasks — read/think phases only, never repo writes (default: on)"),
        ("--strict-delegation", "on" if has_policy else "off", "on · off",
         "write the delegation-rules block into CLAUDE.md, reversible (default: off)"),
        ("--desktop-bridge", _desktop_bridge_summary(), "on · off",
         "wire the dependency-free MCP server into Claude Desktop (default: off)"),
        ("--statusline", _statusline_summary(), "on · off",
         "opt-in standalone status bar (composes statusline.d/); installs into settings.json, reversible (default: off)"),
        ("--workspace-dirs", ":".join(get("workspace_dirs") or []) or "unset", None,
         "repo roots for delegate --project (default: unset)"),
        ("--data-dir", paths.data_dir(), None,
         "where tasks.db + config.json live (read-only · $TASK_STATION_HOME)"),
        ("--reset", "—", "(action)",
         "reset ALL settings above to factory defaults — asks to confirm (default: —)"),
    ]
    w_flag = max(len(r[0]) for r in rows)
    w_val = max(len(r[1]) for r in rows if r[2] is not None)
    wrap_w = max(24, width - len(desc_indent))

    lines = []
    # --- top header: store path (+ set/reset hint); store breaks to its own line
    #     rather than overflow the terminal width.
    store_line = "task-station config        store: %s" % _path()
    if len(store_line) <= width:
        lines.append(store_line)
    else:
        lines.append("task-station config")
        lines.append(indent + "store: %s" % _path())
    lines.append("set a flag: task-station config --<flag> <value>     ·     reset a flag: --<flag> default")

    for flag, value, options, desc in rows:
        lines.append("")
        if options is None:
            lines.append(indent + flag.ljust(w_flag) + gutter + value)
        else:
            lines.append(indent + flag.ljust(w_flag) + gutter
                         + value.ljust(w_val) + gutter + options)
        for seg in (textwrap.wrap(desc, wrap_w) or [""]):
            lines.append(desc_indent + seg)

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


# --- themes -------------------------------------------------------------------
# A THEME is a named full-palette set (per category: bg/fg/bold/cursor/sel + 16
# ANSI). `--theme` is verb-first: the first token is a VERB if in THEME_VERBS,
# else a theme NAME to select. RESERVED names can never be saved.
THEME_VERBS = {"save", "edit", "preview", "list"}
RESERVED_THEME_NAMES = {"save", "edit", "preview", "list", "show", "default"}
_THEME_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _list_themes():
    cats = _categories_module()
    if cats is None:
        print("categories plugin not available (lib/categories.py missing)"); return
    active = active_theme()
    shipped = getattr(cats, "THEMES", {}) or {}   # NB: module-level set() shadows builtin
    variant = resolved_variant()
    lines = ["Themes (* = active):"]
    for name in cats.available_themes():
        mark = "*" if name == active else " "
        kind = "shipped" if name in shipped else "user"
        variants = " · ".join(_variant_label(v, name) for v in getattr(cats, "VARIANTS", ("dark", "light")))
        lines.append("  %s %-12s (%s)   %s" % (mark, name, kind, variants))
    lines.append("")
    lines.append("Appearance: --tint-theme %s → %s" % (tint_theme(), _variant_label(variant)))
    lines.append("")
    lines.append("Select:  config --theme <name>")
    lines.append("Appearance:  config --tint-theme auto|dark|light")
    lines.append("Save current palette as a theme:  config --theme save <name>")
    lines.append("Edit user themes (config.json):   config --theme edit")
    lines.append("Render a preview gallery:         config --theme preview")
    print("\n".join(lines))


def _theme_save(name):
    """Snapshot BOTH variants (dark + light) of the active theme's currently-resolved
    palette into config.json themes[<name>] — a fully self-contained copy, independent
    of the current appearance. Each variant captures every category (resolving the
    active theme over the shipped fallback). Refuses reserved names and names not
    matching ^[a-z0-9][a-z0-9_-]*$."""
    cats = _categories_module()
    if cats is None:
        print("categories plugin not available (lib/categories.py missing)"); return
    if name in RESERVED_THEME_NAMES:
        print("Refusing to save theme '%s' — reserved name (one of: %s)."
              % (name, ", ".join(sorted(RESERVED_THEME_NAMES)))); return
    if not _THEME_NAME_RE.match(name):
        print("Refusing to save theme '%s' — invalid name. Use a lowercase letter or "
              "digit, then any of [a-z0-9_-] (e.g. 'my-theme')." % name); return
    active = active_theme()
    entry = {}
    for variant in getattr(cats, "VARIANTS", ("dark", "light")):
        pals = {}
        for key in cats.CATEGORIES:
            try:
                p = cats.theme_palette(active, key, variant)
            except Exception:
                p = None
            if isinstance(p, dict) and p:
                pals[key] = copy.deepcopy(p)
        if pals:
            entry[variant] = pals
    if not entry:
        print("No active theme palette to snapshot (active = '%s')." % active); return
    d = _load()
    themes = d.get("themes")
    if not isinstance(themes, dict):
        themes = {}
    themes[name] = entry
    d["themes"] = themes
    _save(d)
    labels = " + ".join(_variant_label(v, active) for v in entry)
    counts = ", ".join("%s: %d cats" % (v, len(entry[v])) for v in entry)
    print("saved theme '%s' — snapshot of '%s' (both variants: %s; %s) → %s"
          % (name, active, labels, counts, _path()))


def _theme_preview():
    """Render the gallery for effective_themes() to <data_dir>/themes-preview.html."""
    import sys as _sys
    out = os.path.join(paths.data_dir(), "themes-preview.html")
    try:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        tools = os.path.join(here, "tools")
        if tools not in _sys.path:
            _sys.path.insert(0, tools)
        import render_palettes
        html = render_palettes.render_html()
        os.makedirs(paths.data_dir(), exist_ok=True)
        tmp = out + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(html)
        os.replace(tmp, out)
        print(out)
    except Exception as e:
        print("preview failed: %s" % e)


def cmd_theme(arg):
    """Handle `config --theme [...]` (verb-first grammar):
      (no arg) / list   → list shipped + user themes, mark active
      <name>            → select that theme as active
      save <name>       → snapshot the effective active palette into config themes[<name>]
      edit              → print the config.json path (edit user themes there)
      preview           → render the gallery for effective_themes() to a HTML file
    """
    cats = _categories_module()
    if cats is None:
        print("categories plugin not available (lib/categories.py missing)"); return
    if not arg:
        return _list_themes()
    verb, rest = arg[0], arg[1:]
    if verb in THEME_VERBS:
        if verb == "list":
            return _list_themes()
        if verb == "edit":
            print(_path()); return
        if verb == "preview":
            return _theme_preview()
        if verb == "save":
            if len(rest) != 1:
                print("usage: config --theme save <name>"); return
            return _theme_save(rest[0])
    # not a verb → a theme NAME to select
    if len(arg) != 1:
        print("usage: config --theme [<name> | save <name> | edit | preview | list]"); return
    name = arg[0]
    avail = cats.available_themes()
    if name not in avail:
        print("Unknown theme '%s'. Available: %s" % (name, ", ".join(avail))); return
    set("theme", name)
    print("theme = %s" % active_theme())


# --- factory reset -----------------------------------------------------------
# The config.json keys the board manages. `--reset confirm` pops exactly these
# (so get()'s defaults take over). NOT touched: tasks.db (a separate file) and
# externally-installed integrations that live OUTSIDE config.json — the bare
# /todo,/done command files, the Claude Desktop bridge entry, and the CLAUDE.md
# delegation block. Those are reported with their off-commands, never silently
# removed, so the user removes them deliberately.
RESET_KEYS = [
    "enabled_categories", "auto_categories", "categories",
    "bare_commands", "update_check", "theme", "tint_theme",
    "tint", "title", "guaranteed_tracking", "statusline", "ultracode_hints",
    "workspace_dirs",
]


def reset_settings():
    """Pop every board-managed key from config.json, returning the count cleared.
    Other config (user themes, repo-index roots) and tasks.db are left intact."""
    d = _load()
    cleared = [k for k in RESET_KEYS if k in d]
    for k in cleared:
        del d[k]
    _save(d)
    return len(cleared)


def _commands_dir():
    """Where the SessionStart hook writes bare /todo,/done aliases (honours
    CLAUDE_CONFIG_DIR like the hook does)."""
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    base = os.path.expanduser(cfg) if cfg else os.path.expanduser("~/.claude")
    return os.path.join(base, "commands")


def bare_commands_installed():
    """True if any task-station-managed bare command file (/todo, /done, /repos)
    is present on disk. These are written by the hook OUTSIDE config.json, so a
    settings reset reports rather than deletes them."""
    cdir = _commands_dir()
    for name in ("todo", "done", "repos"):
        try:
            with open(os.path.join(cdir, "%s.md" % name)) as f:
                if "task-station-managed" in f.readline():
                    return True
        except Exception:
            continue
    return False


def cmd_reset(token):
    """`config --reset` factory reset. Bare (`token == "ask"`, or anything other
    than the confirm token) prints what it WILL do plus the confirm command and
    resets NOTHING. `--reset confirm` wipes the board-managed settings back to
    defaults, then reports which externally-installed integrations survive (with
    their off-commands). tasks.db is never touched — your tasks survive."""
    if token != "confirm":
        print("task-station config --reset resets ALL settings on the board above to")
        print("factory defaults (categories, theme, tint, title, workspace-dirs, …).")
        print("Your tasks are NOT affected — tasks.db is left untouched.")
        print("")
        print("To proceed, re-run:  task-station config --reset confirm")
        return
    n = reset_settings()
    print("Reset %d setting%s to defaults." % (n, "" if n == 1 else "s"))
    # Integrations that live OUTSIDE config.json can't (and shouldn't) be removed
    # by a settings reset — report what survives so the user removes it deliberately.
    import setup
    leftovers = []
    if bare_commands_installed():
        leftovers.append(("bare /todo + /done command files", "--bare-cmds off"))
    installed, _ = setup.desktop_bridge_status()
    if installed:
        leftovers.append(("Claude Desktop MCP bridge entry", "--desktop-bridge off"))
    if setup.statusline_status() != "off":
        leftovers.append(("status-bar host/provider in settings.json + statusline.d/",
                          "--statusline off"))
    if "policy" in setup._manifest():
        leftovers.append(("delegation-rules block in CLAUDE.md", "--strict-delegation off"))
    if leftovers:
        print("")
        print("Still installed outside config.json (remove deliberately):")
        for what, how in leftovers:
            print("  %s — task-station config %s" % (what, how))


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
    if getattr(a, "theme", None) is not None:
        return cmd_theme(a.theme)
    if getattr(a, "tint_theme", None) is not None:
        set("tint_theme", a.tint_theme)
        print("tint_theme = %s   (variant: %s)" % (tint_theme(), resolved_variant())); return
    if getattr(a, "tint_theme_get", False):
        print(tint_theme()); return
    if getattr(a, "tint", None) is not None:
        set("tint", a.tint == "on")
        print("tint = %s" % ("on" if get("tint") else "off")); return
    if getattr(a, "tint_get", False):
        print("on" if tint_enabled() else "off"); return
    if getattr(a, "reset", None) is not None:
        return cmd_reset(a.reset)
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
    if getattr(a, "guaranteed_tracking", None) is not None:
        set("guaranteed_tracking", a.guaranteed_tracking == "on")
        print("guaranteed_tracking = %s" % ("on" if get("guaranteed_tracking") else "off")); return
    if getattr(a, "guaranteed_tracking_get", False):
        print("on" if guaranteed_tracking_enabled() else "off"); return
    if getattr(a, "ultracode_hints", None) is not None:
        set("ultracode_hints", a.ultracode_hints == "on")
        print("ultracode_hints = %s" % ("on" if get("ultracode_hints") else "off")); return
    if getattr(a, "ultracode_hints_get", False):
        print("on" if ultracode_hints_enabled() else "off"); return
    if getattr(a, "categories", None) is not None:
        return cmd_categories(a.categories);
    if getattr(a, "enable", None) is not None:
        return toggle_category(a.enable, True)
    if getattr(a, "disable", None) is not None:
        return toggle_category(a.disable, False)
    import setup
    if getattr(a, "strict_delegation", None) is not None:
        print(setup.set_policy(a.strict_delegation == "on")); return
    if getattr(a, "desktop_bridge", None) is not None:
        print(setup.install_desktop_bridge() if a.desktop_bridge == "on"
              else setup.remove_desktop_bridge()); return
    if getattr(a, "statusline_get", False):
        print("on" if statusline_enabled() else "off"); return
    if getattr(a, "statusline", None) is not None:
        on = a.statusline == "on"
        set("statusline", on)
        print(setup.install_statusline() if on else setup.remove_statusline()); return
    # No flags: the single unified settings + status board. The status facts are
    # folded into render_board() now, so we no longer print setup.status() here
    # (setup.status() is unchanged and still used by the install flow).
    print(render_board())
