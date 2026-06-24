"""task-station config: doctor + consented installers. This module owns the
100%-reversible CLAUDE.md managed-block engine. Kept as an internal module
(imported by config.py); its flags are surfaced under `task-station config`."""
import hashlib, json, os, shutil
import paths
import term, config

BEGIN = "<!-- BEGIN task-station:delegation-policy (managed — task-station config --policy) -->"
END = "<!-- END task-station:delegation-policy -->"


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

    if BEGIN in body and END in body:
        # Replace in-place: the old inserted substring was recorded in the manifest.
        # Re-slice around sentinels so we can swap old block for new one.
        start = body.index(BEGIN)
        end = body.index(END) + len(END)
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
    if BEGIN not in body or END not in body:
        return False

    # Extract current block from file.
    start = body.index(BEGIN)
    end = body.index(END) + len(END)
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
        "sid=$(python3 -c 'import sys,json; print(json.load(sys.stdin).get(\"session_id\",\"\"))' 2>/dev/null)\n"
        "[ -n \"$sid\" ] || exit 0\n"
        "exec python3 \"%s\" whoami --session \"$sid\" --statusline --width \"${CLAUDE_STATUSLINE_WIDTH:-0}\"\n"
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
    if STATUSLINE_HOST_MARKER in cmd:
        _backup_settings(path)
        del data["statusLine"]
        _write_settings(path, data)
        return ("Removed the task-station status-bar host from %s and its segment "
                "provider." % path)
    note = "Removed the task-station segment provider." if removed_provider \
        else "task-station status bar not installed — nothing to remove."
    if cmd:
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


def cmd_setup(a):
    if a.policy is not None:
        print(set_policy(a.policy == "on")); return
    if a.workspace_dirs is not None:
        config.set("workspace_dirs", [p for p in a.workspace_dirs.split(os.pathsep) if p])
        print("workspace_dirs = %s" % ":".join(config.workspace_dirs())); return
    print(status())
