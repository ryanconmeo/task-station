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

def enabled_categories():
    """The configured active-category key list, or None when unconfigured
    (categories.enabled_keys() then defaults to the full set)."""
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
    """`9/12 (preset web)`-style summary of the active category set, or
    `12/12 (full — default)` when unconfigured."""
    cats = _categories_module()
    if cats is None:
        return "n/a"
    enabled = cats.enabled_keys()
    total = len(cats.all_keys())
    raw = enabled_categories()
    name = "full — default" if raw is None else "custom"
    if raw is not None:
        for pname in cats.PRESETS:
            if cats.preset_keys(pname) == enabled:   # both canonical order
                name = "preset %s" % pname
                break
    return "%d/%d (%s)" % (len(enabled), total, name)

def render_board():
    import term
    ws = ":".join(get("workspace_dirs") or []) or "(unset — use --repo)"
    cats = get("categories"); n_cat = len(cats) if isinstance(cats, dict) else 0
    lines = [
        "task-station config       store: %s" % _path(),
        "                          set: task-station config --<flag> <value>   ·   reset: <flag> default",
        "",
        "  --workspace-dirs  %-34s repo roots for delegate --project" % ws,
        "  --categories      %-34s enabled set + presets/toggles  (--categories edit · preset <name> · --enable/--disable <key>)"
        % _enabled_summary(),
        "  category overrides %-33s custom tags/labels + skill auto-tint  (--categories edit)"
        % ("%d override(s)" % n_cat if n_cat else "defaults"),
        "  --bare-cmds        %-33s install bare /todo + /done (else /task-station:todo)  on · off"
        % ("on" if bare_commands() else "off"),
        "  --update-check     %-33s opt-in /todo footer when a newer version ships  on · off"
        % ("on" if update_check_enabled() else "off"),
        "  --tint-theme      %-34s tint palette; auto follows OS appearance  auto · dark · light"
        % tint_theme(),
        "  --title           %-34s auto terminal title '#<seq>: <title>' on attach  on · off"
        % ("on" if title_enabled() else "off"),
        "",
        "  read-only",
        "  --data-dir        %-34s (set via $TASK_STATION_HOME)" % paths.data_dir(),
        "  tint mode: %s · terminal: %s" % (tint_mode(), term.detect()),
    ]
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
    lines.append("Presets  (config --categories preset <name>):")
    for name in cats.PRESETS:
        lines.append("  %-8s %s" % (name, " ".join(cats.preset_keys(name))))
    lines.append("")
    lines.append("Toggle individual slots: config --enable <key> · config --disable <key>")
    lines.append("(⚫ GENERAL is permanent — always on, cannot be disabled.)")
    return "\n".join(lines)


def cmd_categories(arg):
    """Handle `config --categories [...]`:
      (no arg)        → show the enabled set + available presets
      edit            → print the config.json path (legacy behaviour)
      preset <name>   → set enabled_categories to that preset (GENERAL forced in)
    """
    if arg == ["edit"]:
        print(_path()); return
    cats = _categories_module()
    if cats is None:
        print("categories plugin not available (lib/categories.py missing)"); return
    if arg and arg[0] == "preset":
        if len(arg) < 2:
            print("usage: config --categories preset <%s>" % "|".join(cats.PRESETS)); return
        name = arg[1]
        keys = cats.preset_keys(name)
        if keys is None:
            print("Unknown preset '%s'. Available: %s" % (name, ", ".join(cats.PRESETS))); return
        set_enabled_categories(keys)
        print("enabled_categories = preset '%s' → %s" % (name, " ".join(keys))); return
    if arg:
        print("usage: config --categories [edit | preset <name>]"); return
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
    if getattr(a, "categories", None) is not None:
        return cmd_categories(a.categories);
    if getattr(a, "enable", None) is not None:
        return toggle_category(a.enable, True)
    if getattr(a, "disable", None) is not None:
        return toggle_category(a.disable, False)
    import setup
    if getattr(a, "policy", None) is not None:
        print(setup.set_policy(a.policy == "on")); return
    if getattr(a, "tint_profiles", False):
        print(setup.install_tint_profiles()); return
    # No flags: the unified settings + doctor/status view.
    print(render_board())
    print("")
    print(setup.status())
