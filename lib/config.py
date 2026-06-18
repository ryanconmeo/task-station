"""Single JSON config store under the data dir, plus the `task-station config` board."""
import json, os
import paths

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

def bare_commands():
    """True only if the user opted in (config flag or env). Default off."""
    if os.environ.get("TASK_STATION_BARE_CMDS") == "on":
        return True
    return bool(get("bare_commands", False))

def tint_mode():
    return get("tint_mode", "auto")

def render_board():
    import term
    ws = ":".join(get("workspace_dirs") or []) or "(unset — use --repo)"
    cats = get("categories"); n_cat = len(cats) if isinstance(cats, dict) else 0
    lines = [
        "task-station config       store: %s" % _path(),
        "                          set: task-station config --<flag> <value>   ·   reset: <flag> default",
        "",
        "  --workspace-dirs  %-34s repo roots for delegate --project" % ws,
        "  --categories      %-34s custom tags/labels + skill auto-tint  (task-station config --categories edit)"
        % ("%d override(s)" % n_cat if n_cat else "defaults"),
        "  --bare-cmds        %-33s install bare /todo + /done (else /task-station:todo)  on · off"
        % ("on" if bare_commands() else "off"),
        "",
        "  read-only",
        "  --data-dir        %-34s (set via $TASK_STATION_HOME)" % paths.data_dir(),
        "  tint mode: %s · terminal: %s" % (tint_mode(), term.detect()),
    ]
    return "\n".join(lines)

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
    if a.categories == "edit":
        print(_path()); return
    import setup
    if getattr(a, "policy", None) is not None:
        print(setup.set_policy(a.policy == "on")); return
    if getattr(a, "tint_profiles", False):
        print(setup.install_tint_profiles()); return
    # No flags: the unified settings + doctor/status view.
    print(render_board())
    print("")
    print(setup.status())
