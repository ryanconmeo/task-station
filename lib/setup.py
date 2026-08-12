"""task-station config: doctor + consented installers. This module owns the
100%-reversible CLAUDE.md managed-block engine. Kept as an internal module
(imported by config.py); its flags are surfaced under `task-station config`."""
import hashlib, json, os, shutil
import paths
import term, config

# Stable marker ID present in BOTH the historical (`--policy`) and current
# (`--strict-delegation`) BEGIN parentheticals. Detection matches on this prefix so a
# block installed by an older version is still found for replace/remove — never orphaned.
BEGIN_MARK = "<!-- BEGIN task-station:delegation-policy"
BEGIN = "%s (managed — task-station config --strict-delegation) -->" % BEGIN_MARK
END = "<!-- END task-station:delegation-policy -->"


def _find_span(body):
    """Return [start, end) of the managed block — tolerant of the old (`--policy`) and
    current (`--strict-delegation`) BEGIN text — or None when no block is present."""
    i = body.find(BEGIN_MARK)
    if i == -1:
        return None
    j = body.find(END, i)
    if j == -1:
        return None
    return i, j + len(END)


def _manifest_path():
    return os.path.join(paths.data_dir(), "setup-manifest.json")


def _manifest():
    try:
        with open(_manifest_path()) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_manifest(m):
    os.makedirs(paths.data_dir(), exist_ok=True)
    tmp = _manifest_path() + ".tmp"
    with open(tmp, "w") as f:
        json.dump(m, f, indent=2)
    os.replace(tmp, _manifest_path())


def _block(text):
    return "%s\n%s\n%s" % (BEGIN, text, END)


def _apply_block(md_path, text):
    """Add-or-replace, fenced, idempotent.
    Records exact inserted substring + hash of the block; backs up."""
    os.makedirs(os.path.dirname(os.path.abspath(md_path)), exist_ok=True)
    body = open(md_path).read() if os.path.exists(md_path) else ""
    # Always write a backup before any modification.
    with open(md_path + ".bak", "w") as f:
        f.write(body)

    block = _block(text)

    span = _find_span(body)
    if span:
        # Replace in-place: the old inserted substring was recorded in the manifest.
        # Re-slice around sentinels so we can swap old block for new one (the span
        # finder tolerates a pre-1.14.4 `--policy` BEGIN marker too).
        start, end = span
        old_block = body[start:end]
        new_body = body[:start] + block + body[end:]
        # The inserted span is the same slice (no separator change on replace).
        # Determine what extra characters existed before/after the block to store
        # the exact replaced span.  For simplicity on replace we record the new
        # block directly; the separator characters are already in the file and
        # don't change on an idempotent replace.
        # We track the full span that was originally inserted so removal still
        # works: reuse the recorded `inserted` from the manifest if it exists,
        # replacing only the block portion.
        recorded = _manifest().get("policy", {})
        old_inserted = recorded.get("inserted", "")
        if old_inserted and old_block in old_inserted:
            new_inserted = old_inserted.replace(old_block, block, 1)
        else:
            new_inserted = block
        with open(md_path, "w") as f:
            f.write(new_body)
    else:
        # Append with one separator newline.
        if body == "" or body.endswith("\n\n"):
            sep = ""
        elif body.endswith("\n"):
            sep = "\n"
        else:
            sep = "\n\n"
        inserted = sep + block + "\n"
        new_body = body + inserted
        new_inserted = inserted
        with open(md_path, "w") as f:
            f.write(new_body)

    m = _manifest()
    m["policy"] = {
        "block": block,
        "hash": hashlib.sha256(block.encode()).hexdigest(),
        "inserted": new_inserted,
    }
    _save_manifest(m)


def _remove_block(md_path):
    """Remove exactly the managed block; restore prior content. Returns False (no-op)
    if the block was hand-edited (hash mismatch) so we never delete the user's edits."""
    if not os.path.exists(md_path):
        return False
    body = open(md_path).read()
    span = _find_span(body)
    if not span:
        return False

    # Extract current block from file (tolerant of an old `--policy` BEGIN marker).
    start, end = span
    current_block = body[start:end]

    # Hash-check: refuse if block was hand-edited.
    recorded = _manifest().get("policy", {})
    if recorded.get("hash") and hashlib.sha256(current_block.encode()).hexdigest() != recorded["hash"]:
        return False  # edited → refuse; caller warns

    # Use the exact recorded inserted substring for a guaranteed byte-identical restore.
    inserted = recorded.get("inserted", "")
    if inserted and inserted in body:
        new_body = body.replace(inserted, "", 1)
    else:
        # Fallback: strip block + surrounding newlines manually (less precise).
        pre = body[:start]
        post = body[end:]
        new_body = pre.rstrip("\n") + ("\n" if pre.rstrip("\n") else "") + post.lstrip("\n")
        if not pre.strip():
            new_body = post.lstrip("\n")

    with open(md_path, "w") as f:
        f.write(new_body)

    m = _manifest()
    m.pop("policy", None)
    _save_manifest(m)
    return True


# --------------------------------------------------------- doctor + installers ----

def _policy_text():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "policy-block.md")
    return open(p).read().strip() if os.path.exists(p) else "(policy text missing)"


def _claude_md():
    return os.path.join(os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")), "CLAUDE.md")


def status():
    t = term.detect()
    ws = config.workspace_dirs()
    has_policy = ("policy" in _manifest())
    lines = ["task-station config — status", ""]
    lines.append("  tint        full-palette escape · terminal %s%s" % (
        t, "" if t != "none" else "  (no supported terminal detected → no-op)"))
    lines.append("  workspace   %s" % (":".join(ws) if ws else "unset — task-station config --workspace-dirs <dirs>"))
    lines.append("  strict-delegation %s" % ("installed in CLAUDE.md — remove: task-station config --strict-delegation off"
                 if has_policy else "not installed — task-station config --strict-delegation on"))
    installed, server_path = desktop_bridge_status()
    lines.append("  desktop-bridge %s" % (
        "installed → %s  (restart Desktop after changes)" % server_path if installed
        else "not installed — task-station config --desktop-bridge on"))
    return "\n".join(lines)


def set_policy(on):
    md = _claude_md()
    if on:
        _apply_block(md, _policy_text())
        return "Added strict-delegation rules to %s (reverse: task-station config --strict-delegation off)." % md
    ok = _remove_block(md)
    return ("Removed strict-delegation rules from %s." % md) if ok else \
           ("Left %s unchanged — the managed block was hand-edited; remove it manually." % md)


# --------------------------------------------------- Desktop bridge (MCP) ----
#
# Self-installer for the dependency-free stdlib MCP server: merge a `task-station`
# entry into Claude Desktop's `claude_desktop_config.json` so Desktop and the CLI
# share one task store. Mirrors the consented-installer shape of the other flags.

BRIDGE_SERVER = "task-station"          # the mcpServers key we own
BRIDGE_BACKUP_SUFFIX = ".bak-desktop-bridge"
BRIDGE_LAUNCHER_NAME = "mcp-launcher.py"   # generated under the (stable) data dir


def desktop_config_path():
    """Claude Desktop's MCP config. Honors `TASK_STATION_DESKTOP_CONFIG` (set by
    tests / safe manual checks to target a temp file) before falling back to the
    real macOS path. The dir/file may not exist yet."""
    override = os.environ.get("TASK_STATION_DESKTOP_CONFIG")
    if override:
        return os.path.expanduser(override)
    return os.path.expanduser(
        "~/Library/Application Support/Claude/claude_desktop_config.json")


def launcher_path():
    """Abs path to the STABLE self-resolving launcher
    (`<data_dir>/mcp-launcher.py`). The data dir is version-independent, so this
    path survives `/plugin update` and the volatile engine symlink alike."""
    return os.path.join(paths.data_dir(), BRIDGE_LAUNCHER_NAME)


def _launcher_source():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_launcher.py")


def write_launcher():
    """(Re)generate the launcher at the stable data-dir path and return it. A plain
    copy of the bundled stdlib `mcp_launcher.py` — self-contained, so the generated
    file resolves the installed `mcp_server.py` at run time independent of which
    plugin version wrote it. Idempotent (overwrites)."""
    dest = launcher_path()
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copyfile(_launcher_source(), dest)
    return dest


def _read_desktop_config(path):
    """Parse the Desktop config, or {} when missing/empty/invalid (never raises)."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            body = f.read().strip()
        return json.loads(body) if body else {}
    except Exception:
        return {}


def _write_desktop_config(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def _backup_desktop_config(path):
    """Snapshot the current file (if any) before we modify it. Idempotent."""
    if os.path.exists(path):
        shutil.copyfile(path, path + BRIDGE_BACKUP_SUFFIX)


def install_desktop_bridge(path=None):
    """Create/locate the Desktop config, back it up, and MERGE our `task-station`
    server entry without clobbering other servers. Points Desktop at the STABLE
    self-resolving launcher (regenerated here) — not the volatile engine symlink.
    Idempotent."""
    path = path or desktop_config_path()
    server_path = write_launcher()
    _backup_desktop_config(path)
    data = _read_desktop_config(path)
    if not isinstance(data.get("mcpServers"), dict):
        data["mcpServers"] = {}
    data["mcpServers"][BRIDGE_SERVER] = {
        "command": "python3",
        "args": [server_path],
    }
    _write_desktop_config(path, data)
    return ("Wired the task-station MCP bridge into %s\n"
            "  command: python3 %s\n"
            "Restart Claude Desktop to apply." % (path, server_path))


def remove_desktop_bridge(path=None):
    """Remove ONLY our `task-station` server entry (leave any others). No-op when
    nothing is installed. The generated launcher file is left in place — it is
    inert without the config entry and lets a later `on` re-wire instantly; it is
    harmless to delete by hand."""
    path = path or desktop_config_path()
    if not os.path.exists(path):
        return "No Claude Desktop config at %s — nothing to remove." % path
    data = _read_desktop_config(path)
    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or BRIDGE_SERVER not in servers:
        return "task-station bridge not present in %s — nothing to remove." % path
    _backup_desktop_config(path)
    del servers[BRIDGE_SERVER]
    _write_desktop_config(path, data)
    return ("Removed the task-station MCP bridge from %s.\n"
            "Restart Claude Desktop to apply." % path)


def desktop_bridge_status(path=None):
    """(installed?, launcher_path) — for the no-arg config view."""
    path = path or desktop_config_path()
    data = _read_desktop_config(path)
    servers = data.get("mcpServers")
    installed = isinstance(servers, dict) and BRIDGE_SERVER in servers
    return installed, launcher_path()


# ----------------------------------------------- Status-line host + provider ----
#
# Opt-in (`config --statusline on`), DEFAULT OFF, non-destructive + reversible —
# same consented-installer shape as the Desktop bridge above. Implements the
# composition convention in docs/STATUSLINE.md: task-station is BOTH a conformant
# PROVIDER (a drop-in in statusline.d/) and — when nothing else owns the bar — a
# self-sufficient HOST (the embedded compose routine in lib/statusline-host.sh).

STATUSLINE_HOST_MARKER = "# claude-statusline-host:task-station"
# Managed-marker comment carried near the top of our provider drop-in so
# unregister only ever removes a file we wrote (never a hand-rolled provider).
PROVIDER_MANAGED_MARKER = "# task-station-managed statusline provider"
PROVIDER_NAME = "50-task-station.sh"
SETTINGS_BACKUP_SUFFIX = ".bak-statusline"

# The cost-HUD (WS7) rides the SAME compose host as `--statusline` but ships its own
# provider drop-in (lexically after the task segment). Managed-marker gated so
# unregister only removes a file we wrote.
HUD_PROVIDER_MANAGED_MARKER = "# task-station-managed cost-hud provider"
HUD_PROVIDER_NAME = "60-cost-hud.sh"


def _config_dir():
    return os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude"))


def settings_path():
    """Claude Code's `settings.json` under `${CLAUDE_CONFIG_DIR:-~/.claude}`
    (env-honoring, like _claude_md()). The file may not exist yet."""
    return os.path.join(_config_dir(), "settings.json")


def statusline_d_dir():
    return os.path.join(_config_dir(), "statusline.d")


def provider_path():
    return os.path.join(statusline_d_dir(), PROVIDER_NAME)


def host_path():
    """Stable, version-independent path to the host compose script, via the
    `task-station-engine` symlink (refreshed every SessionStart → survives
    `/plugin update`). Mirrors how the launcher resolves a stable path."""
    return os.path.join(_config_dir(), "task-station-engine", "statusline-host.sh")


def register_provider():
    """(Re)write the executable PROVIDER drop-in at statusline.d/50-task-station.sh:
    reads the statusLine JSON on stdin, pulls `session_id`, and emits task-station's
    segment honoring CLAUDE_STATUSLINE_WIDTH. Routed through the stable engine path.
    Idempotent (overwrites). Returns the path."""
    d = statusline_d_dir()
    os.makedirs(d, exist_ok=True)
    engine = os.path.join(_config_dir(), "task-station-engine", "task-station.py")
    body = (
        "#!/usr/bin/env bash\n"
        "%s (config --statusline). Regenerated on install; do not edit.\n"
        "payload=$(cat)\n"
        "sid=$(printf '%%s' \"$payload\" | python3 -c 'import sys,json; print(json.load(sys.stdin).get(\"session_id\",\"\"))' 2>/dev/null)\n"
        "[ -n \"$sid\" ] || exit 0\n"
        "printf '%%s' \"$payload\" | python3 \"%s\" whoami --session \"$sid\" --statusline --width \"${CLAUDE_STATUSLINE_WIDTH:-0}\"\n"
        % (PROVIDER_MANAGED_MARKER, engine)
    )
    p = provider_path()
    with open(p, "w") as f:
        f.write(body)
    os.chmod(p, 0o755)
    return p


def unregister_provider():
    """Remove ONLY our managed provider drop-in (verified via its marker). Leaves a
    hand-rolled file of the same name untouched. No-op when absent."""
    p = provider_path()
    if not os.path.exists(p):
        return False
    try:
        with open(p) as f:
            head = f.read(512)
    except Exception:
        return False
    if PROVIDER_MANAGED_MARKER not in head:
        return False  # not ours — never delete a foreign provider
    os.remove(p)
    return True


def _read_settings(path):
    """Parse settings.json, or {} when missing/empty/invalid (never raises)."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            body = f.read().strip()
        return json.loads(body) if body else {}
    except Exception:
        return {}


def _write_settings(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def _backup_settings(path):
    if os.path.exists(path):
        shutil.copyfile(path, path + SETTINGS_BACKUP_SUFFIX)


def install_statusline(path=None):
    """Opt-in install. The PROVIDER is ALWAYS (re)registered into statusline.d/.
    Then, per the non-destructive rule (docs/STATUSLINE.md), inspect
    settings["statusLine"].command:
      - unset/empty   → install ourselves as host (write the marked compose command).
      - bears OUR marker → leave settings untouched (idempotent); provider ensured.
      - foreign command  → DO NOT modify settings.json; provider registered either way.
    settings.json is backed up before any modification. Reversible via
    remove_statusline()."""
    path = path or settings_path()
    register_provider()                      # provider first, always
    data = _read_settings(path)
    sl = data.get("statusLine")
    cmd = sl.get("command", "") if isinstance(sl, dict) else ""

    if not cmd:
        _backup_settings(path)
        data["statusLine"] = {
            "type": "command",
            "command": "bash %s  %s" % (host_path(), STATUSLINE_HOST_MARKER),
        }
        _write_settings(path, data)
        return ("Installed the task-station status-bar host + segment provider.\n"
                "  statusLine.command → bash %s\n"
                "  provider           → %s\n"
                "Reverse: task-station config --statusline off." % (host_path(), provider_path()))

    if STATUSLINE_HOST_MARKER in cmd:
        return ("task-station already owns the status bar — settings.json left "
                "unchanged. Segment provider ensured at %s." % provider_path())

    # Foreign / unknown statusLine.command — never clobber it.
    return ("Your statusLine is owned by another command — left untouched. "
            "Registered a segment provider at %s; if your bar composes "
            "statusline.d/ it will appear automatically, otherwise add it to your bar."
            % provider_path())


def remove_statusline(path=None):
    """Reversible removal: drop our provider drop-in, and clear statusLine ONLY when
    its command bears OUR host marker (never a foreign statusLine). settings.json is
    backed up before any modification."""
    removed_provider = unregister_provider()
    path = path or settings_path()
    data = _read_settings(path)
    sl = data.get("statusLine")
    cmd = sl.get("command", "") if isinstance(sl, dict) else ""
    if STATUSLINE_HOST_MARKER in cmd and not _managed_providers_present():
        _backup_settings(path)
        del data["statusLine"]
        _write_settings(path, data)
        return ("Removed the task-station status-bar host from %s and its segment "
                "provider." % path)
    note = "Removed the task-station segment provider." if removed_provider \
        else "task-station status bar not installed — nothing to remove."
    if STATUSLINE_HOST_MARKER in cmd and _managed_providers_present():
        note += " Left the shared status-bar host in place (the cost HUD still uses it)."
    elif cmd:
        note += " Left the existing statusLine.command (owned by another command) untouched."
    return note


def statusline_status(path=None):
    """'installed (host)' / 'provider-only' / 'off' — for the config board + status."""
    path = path or settings_path()
    data = _read_settings(path)
    sl = data.get("statusLine")
    cmd = sl.get("command", "") if isinstance(sl, dict) else ""
    if STATUSLINE_HOST_MARKER in cmd:
        return "installed (host)"
    if os.path.exists(provider_path()):
        return "provider-only"
    return "off"


# ------------------------------------------------------- Cost HUD (WS7) ------
#
# `config --hud on` installs the cost-HUD segment provider (60-cost-hud.sh) and —
# per the non-destructive rule — installs the compose host as statusLine.command
# only when nothing else owns the bar. The host is SHARED with `--statusline`, so
# neither `off` clears the host while the other's provider is still present.

def hud_provider_path():
    return os.path.join(statusline_d_dir(), HUD_PROVIDER_NAME)


def register_hud_provider():
    """(Re)write the executable cost-HUD provider drop-in. It pipes the status-line
    JSON on stdin straight to lib/hud.py (routed via the stable engine path), which
    extracts the session id itself and emits the HUD rows. Idempotent."""
    d = statusline_d_dir()
    os.makedirs(d, exist_ok=True)
    engine = os.path.join(_config_dir(), "task-station-engine", "hud.py")
    body = (
        "#!/usr/bin/env bash\n"
        "%s (config --hud). Regenerated on install; do not edit.\n"
        "exec python3 \"%s\" --width \"${CLAUDE_STATUSLINE_WIDTH:-0}\"\n"
        % (HUD_PROVIDER_MANAGED_MARKER, engine)
    )
    p = hud_provider_path()
    with open(p, "w") as f:
        f.write(body)
    os.chmod(p, 0o755)
    return p


def unregister_hud_provider():
    """Remove ONLY our managed cost-HUD drop-in (verified via its marker). No-op when
    absent or when the file isn't ours."""
    p = hud_provider_path()
    if not os.path.exists(p):
        return False
    try:
        with open(p) as f:
            head = f.read(512)
    except Exception:
        return False
    if HUD_PROVIDER_MANAGED_MARKER not in head:
        return False
    os.remove(p)
    return True


def _managed_providers_present():
    """True while EITHER task-station provider drop-in (the task segment or the cost
    HUD) is still installed — the host is only cleared once both are gone."""
    return os.path.exists(provider_path()) or os.path.exists(hud_provider_path())


def _install_host(path):
    """Install task-station's compose host as statusLine.command when the bar is
    unowned; leave a marked (ours) or foreign command untouched. Backs up
    settings.json before any modification. Returns 'host' | 'already' | 'foreign'."""
    data = _read_settings(path)
    sl = data.get("statusLine")
    cmd = sl.get("command", "") if isinstance(sl, dict) else ""
    if not cmd:
        _backup_settings(path)
        data["statusLine"] = {
            "type": "command",
            "command": "bash %s  %s" % (host_path(), STATUSLINE_HOST_MARKER),
        }
        _write_settings(path, data)
        return "host"
    if STATUSLINE_HOST_MARKER in cmd:
        return "already"
    return "foreign"


def install_hud(path=None):
    """Opt-in install: register the cost-HUD provider, then install the compose host
    when nothing else owns the bar (non-destructive — never clobbers a foreign
    statusLine). Reversible via remove_hud()."""
    path = path or settings_path()
    register_hud_provider()
    state = _install_host(path)
    if state == "host":
        return ("Installed the task-station cost-HUD host + segment provider.\n"
                "  statusLine.command → bash %s\n"
                "  provider           → %s\n"
                "Reverse: task-station config --hud off." % (host_path(), hud_provider_path()))
    if state == "already":
        return ("task-station already owns the status bar — settings.json left "
                "unchanged. Cost-HUD provider ensured at %s." % hud_provider_path())
    return ("Your statusLine is owned by another command — left untouched. "
            "Registered a cost-HUD segment provider at %s; if your bar composes "
            "statusline.d/ it will appear automatically, otherwise add it to your bar."
            % hud_provider_path())


def remove_hud(path=None):
    """Reversible removal: drop the cost-HUD provider, and clear statusLine ONLY when
    its command bears our host marker AND no other task-station provider remains."""
    removed = unregister_hud_provider()
    path = path or settings_path()
    data = _read_settings(path)
    sl = data.get("statusLine")
    cmd = sl.get("command", "") if isinstance(sl, dict) else ""
    if STATUSLINE_HOST_MARKER in cmd and not _managed_providers_present():
        _backup_settings(path)
        del data["statusLine"]
        _write_settings(path, data)
        return ("Removed the task-station cost-HUD host from %s and its segment "
                "provider." % path)
    note = "Removed the task-station cost-HUD segment provider." if removed \
        else "task-station cost HUD not installed — nothing to remove."
    if STATUSLINE_HOST_MARKER in cmd and _managed_providers_present():
        note += " Left the shared status-bar host in place (the task segment still uses it)."
    elif cmd:
        note += " Left the existing statusLine.command (owned by another command) untouched."
    return note


def hud_status(path=None):
    """'installed (host)' / 'provider-only' / 'off' — for the config board. 'host' iff
    OUR host owns the bar AND our HUD provider is present; 'provider-only' if the HUD
    provider exists but a foreign command owns the bar; else 'off'."""
    if not os.path.exists(hud_provider_path()):
        return "off"
    path = path or settings_path()
    data = _read_settings(path)
    sl = data.get("statusLine")
    cmd = sl.get("command", "") if isinstance(sl, dict) else ""
    if STATUSLINE_HOST_MARKER in cmd:
        return "installed (host)"
    return "provider-only"


# ------------------------------------ WorktreeCreate provisioner (opt-in) ----
#
# `config --worktree-hook on` writes ONE `WorktreeCreate` entry into the user's own
# settings.json. Same consented-installer discipline as the statusline/sandbox pair
# above — back up first, atomic read-merge-write, create only the structure we need,
# record exactly what we created so the reverse is precise, and NEVER touch a foreign
# entry — but with two rules the others don't need:
#
#   * A PLUGIN CANNOT SHIP THIS. The hook REPLACES worktree creation, so a bug in it
#     breaks every worktree Claude Code makes on the machine, including its own
#     subagent isolation. Shipping it in hooks/hooks.json would install it for
#     everyone who installs the plugin; it belongs behind a deliberate opt-in.
#   * A FOREIGN WorktreeCreate ENTRY IS A REFUSAL, not something to sit beside. Two
#     hooks that both create the worktree and both print a path is not a composition,
#     it is a race — so if someone else already owns the event, we install nothing and
#     say so.
#
# The command points at the STABLE engine path (`<config>/task-station-engine/../
# hooks/on_worktree_create.sh`), not the versioned plugin-cache dir, so a
# `/plugin update` doesn't leave settings.json pointing at a directory that no longer
# exists. `task-station-engine` is a symlink to the active `lib/`, so `..` from it is
# the plugin root — which is why the `..` must survive into the written string and
# must not be normalised away.

WORKTREE_HOOK_MARKER = "# task-station-managed:worktree-create"
WORKTREE_HOOK_MANIFEST_KEY = "worktree_hook"
SETTINGS_BACKUP_WORKTREE = ".bak-worktree-hook"


def worktree_hook_script():
    """The stable path to `hooks/on_worktree_create.sh`, via the `task-station-engine`
    symlink (refreshed every SessionStart → survives `/plugin update`). The literal
    `..` is deliberate and must NOT be normalised: the symlink points at `lib/`, so
    the parent of the symlink target is the plugin root."""
    return os.path.join(_config_dir(), "task-station-engine", "..",
                        "hooks", "on_worktree_create.sh")


def worktree_hook_command():
    return 'bash "%s"  %s' % (worktree_hook_script(), WORKTREE_HOOK_MARKER)


def _worktree_entries(data):
    """The `hooks.WorktreeCreate` list in a settings dict, or []."""
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return []
    entries = hooks.get("WorktreeCreate")
    return entries if isinstance(entries, list) else []


def _is_ours(entry):
    """True when a WorktreeCreate entry carries OUR marker — the only entries this
    installer is ever allowed to touch."""
    if not isinstance(entry, dict):
        return False
    for h in (entry.get("hooks") or []):
        if isinstance(h, dict) and WORKTREE_HOOK_MARKER in str(h.get("command") or ""):
            return True
    return False


def install_worktree_hook(path=None):
    """Opt-in install of the WorktreeCreate provisioner. Idempotent (a second `on` is
    a no-op), refuses to sit beside a foreign entry, and backs settings.json up before
    any modification. Returns the human message — including the reverse command, which
    for this hook is also the emergency stop."""
    path = path or settings_path()
    data = _read_settings(path)
    entries = _worktree_entries(data)
    if any(_is_ours(e) for e in entries):
        return ("task-station already provisions new worktrees — %s left unchanged.\n"
                "  entry → %s\n"
                "Reverse: task-station config --worktree-hook off." % (path, worktree_hook_command()))
    if entries:
        return ("Another WorktreeCreate hook already owns worktree creation in %s — "
                "left untouched, nothing installed. A WorktreeCreate hook REPLACES the "
                "creation, so two of them would race to create and to print a path. "
                "Remove the other entry first if you want task-station to provision."
                % path)
    _backup_settings_worktree(path)
    created = []
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
        data["hooks"] = hooks
        created.append("hooks")
    hooks["WorktreeCreate"] = [
        {"hooks": [{"type": "command", "command": worktree_hook_command()}]}]
    created.append("WorktreeCreate")
    _write_settings(path, data)
    m = _manifest()
    m[WORKTREE_HOOK_MANIFEST_KEY] = {"created": created}
    _save_manifest(m)
    return ("Installed the task-station worktree provisioner into %s\n"
            "  WorktreeCreate → %s\n"
            "New worktrees now get the main checkout's .claude/settings.local.json "
            "and a trust entry in ~/.claude.json.\n"
            "This hook CREATES the worktree itself — if the plugin is ever removed "
            "while it is installed, worktree creation fails until you run the reverse.\n"
            "Reverse: task-station config --worktree-hook off." % (path, worktree_hook_command()))


def remove_worktree_hook(path=None):
    """Reversible removal: drop ONLY our marked entry, then tear down only the empty
    scaffolding WE created. A foreign WorktreeCreate entry, and every other key in
    settings.json, is left exactly as it was."""
    path = path or settings_path()
    if not os.path.exists(path):
        m = _manifest()
        if m.pop(WORKTREE_HOOK_MANIFEST_KEY, None) is not None:
            _save_manifest(m)
            return "settings.json is gone — cleared the worktree-hook manifest entry."
        return "task-station worktree provisioner not installed — nothing to remove."
    data = _read_settings(path)
    entries = _worktree_entries(data)
    ours = [e for e in entries if _is_ours(e)]
    if not ours:
        m = _manifest()
        if m.pop(WORKTREE_HOOK_MANIFEST_KEY, None) is not None:
            _save_manifest(m)
        return "task-station worktree provisioner not installed — nothing to remove."
    _backup_settings_worktree(path)
    created = (_manifest().get(WORKTREE_HOOK_MANIFEST_KEY) or {}).get("created", [])
    kept = [e for e in entries if not _is_ours(e)]
    hooks = data.get("hooks")
    if kept:
        hooks["WorktreeCreate"] = kept
    elif "WorktreeCreate" in created:
        del hooks["WorktreeCreate"]
    else:
        hooks["WorktreeCreate"] = []      # the list predates us — leave it, emptied
    if "hooks" in created and data.get("hooks") == {}:
        del data["hooks"]
    _write_settings(path, data)
    m = _manifest()
    m.pop(WORKTREE_HOOK_MANIFEST_KEY, None)
    _save_manifest(m)
    return ("Removed the task-station worktree provisioner from %s. Claude Code "
            "creates worktrees natively again." % path)


def worktree_hook_status(path=None):
    """'installed' / 'off' — read from settings.json itself, never from the flag. The
    config flag records intent; this reports what is actually wired."""
    path = path or settings_path()
    data = _read_settings(path)
    return "installed" if any(_is_ours(e) for e in _worktree_entries(data)) else "off"


def _backup_settings_worktree(path):
    if os.path.exists(path):
        shutil.copyfile(path, path + SETTINGS_BACKUP_WORKTREE)


# ------------------------------------- Obsidian sandbox allowWrite (Fix A) ----
#
# Opt-in widening of Claude Code's BASH-tool sandbox so an Obsidian vault under a
# macOS-protected root (~/Documents, iCloud, …) gets INSTANT inline exports, not
# only the end-of-turn unsandboxed hook auto-flush (Fix B). The sandbox blocks
# writes outside the session cwd + $TMPDIR; the documented widening key is
# `sandbox.filesystem.allowWrite` (an array of ~/ or absolute path prefixes, merged
# across settings scopes). A plugin CANNOT ship sandbox config — only the user's own
# settings.json is honoured — so we (with consent) edit ~/.claude/settings.json.
#
# Discipline (same as the Desktop-bridge / statusline installers): atomic
# read-merge-write, back up first, create structure only as needed, no-op if the
# path is already present, and NEVER touch other keys/values — crucially NOT
# `sandbox.enabled` (we only add a path; if the user hasn't opted into the sandbox
# we don't force it on). The manifest records the exact entry AND which nested keys
# we created, so removal strips precisely our entry and only the empty scaffolding
# we introduced.

SANDBOX_MANIFEST_KEY = "sandbox_allowwrite"
SETTINGS_BACKUP_SANDBOX = ".bak-obsidian-sandbox"


def _backup_settings_sandbox(path):
    if os.path.exists(path):
        shutil.copyfile(path, path + SETTINGS_BACKUP_SANDBOX)


def install_sandbox_allowwrite(vault_entry, path=None):
    """Add `vault_entry` (a ~/ or absolute path — stored verbatim so `~/` survives) to
    `sandbox.filesystem.allowWrite` in settings.json. Idempotent; never disturbs other
    keys or `sandbox.enabled`. Records the entry + the nested keys we had to create so
    the reverse is precise. Returns a human message."""
    path = path or settings_path()
    data = _read_settings(path)
    # Walk/create the nested structure, tracking exactly which levels WE introduced.
    created = []
    sb = data.get("sandbox")
    if not isinstance(sb, dict):
        sb = {}; data["sandbox"] = sb; created.append("sandbox")
    fs = sb.get("filesystem")
    if not isinstance(fs, dict):
        fs = {}; sb["filesystem"] = fs; created.append("filesystem")
    aw = fs.get("allowWrite")
    if not isinstance(aw, list):
        aw = []; fs["allowWrite"] = aw; created.append("allowWrite")
    if vault_entry in aw:
        # Already allowed — record it as ours ONLY if we're not stepping on a
        # user-authored entry: leave the manifest untouched so `off` never strips a
        # path the user added themselves.
        return ("Vault already in sandbox.filesystem.allowWrite (%s) — no change." % path)
    _backup_settings_sandbox(path)
    aw.append(vault_entry)
    _write_settings(path, data)
    m = _manifest()
    m[SANDBOX_MANIFEST_KEY] = {"entry": vault_entry, "created": created}
    _save_manifest(m)
    return ("Added the vault to sandbox.filesystem.allowWrite in %s\n"
            "  %s\n"
            "In-session (sandboxed) exports into it now write instantly. "
            "Reverse: task-station config --obsidian-sandbox off." % (path, vault_entry))


def remove_sandbox_allowwrite(path=None):
    """Reverse install_sandbox_allowwrite precisely: drop ONLY our recorded entry, then
    delete only the empty nested structures WE created (so a user's `sandbox.enabled`
    or other allowWrite paths are never disturbed). No-op when we never installed."""
    path = path or settings_path()
    rec = _manifest().get(SANDBOX_MANIFEST_KEY)
    if not rec:
        return "Obsidian sandbox allowlist not installed — nothing to remove."
    entry = rec.get("entry")
    created = rec.get("created", [])
    if not os.path.exists(path):
        m = _manifest(); m.pop(SANDBOX_MANIFEST_KEY, None); _save_manifest(m)
        return "settings.json is gone — cleared the sandbox-allowlist manifest entry."
    _backup_settings_sandbox(path)
    data = _read_settings(path)
    sb = data.get("sandbox") if isinstance(data.get("sandbox"), dict) else None
    fs = sb.get("filesystem") if sb and isinstance(sb.get("filesystem"), dict) else None
    aw = fs.get("allowWrite") if fs and isinstance(fs.get("allowWrite"), list) else None
    if aw is not None and entry in aw:
        aw.remove(entry)
    # Tear down only the empty scaffolding we created, innermost-first, each guarded on
    # BOTH "we created it" AND "it's now empty" so we never delete user content.
    if "allowWrite" in created and fs is not None and fs.get("allowWrite") == []:
        del fs["allowWrite"]
    if "filesystem" in created and sb is not None and sb.get("filesystem") == {}:
        del sb["filesystem"]
    if "sandbox" in created and data.get("sandbox") == {}:
        del data["sandbox"]
    _write_settings(path, data)
    m = _manifest(); m.pop(SANDBOX_MANIFEST_KEY, None); _save_manifest(m)
    return "Removed the vault from sandbox.filesystem.allowWrite in %s." % path


def sandbox_allowwrite_status(path=None):
    """True when WE have an allowWrite entry installed AND it's still present in
    settings.json — for the config board + protected-path warning."""
    rec = _manifest().get(SANDBOX_MANIFEST_KEY)
    if not rec:
        return False
    path = path or settings_path()
    data = _read_settings(path)
    sb = data.get("sandbox") if isinstance(data.get("sandbox"), dict) else {}
    fs = sb.get("filesystem") if isinstance(sb.get("filesystem"), dict) else {}
    aw = fs.get("allowWrite") if isinstance(fs.get("allowWrite"), list) else []
    return rec.get("entry") in aw


def cmd_setup(a):
    if a.policy is not None:
        print(set_policy(a.policy == "on")); return
    if a.workspace_dirs is not None:
        config.set("workspace_dirs", [p for p in a.workspace_dirs.split(os.pathsep) if p])
        print("workspace_dirs = %s" % ":".join(config.workspace_dirs())); return
    print(status())
