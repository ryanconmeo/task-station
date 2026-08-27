"""Which terminal am I actually running in, and how do I open a window in it?

WHY THIS EXISTS. 2026-08-26: asked to open a new terminal, a session ran

    osascript -e 'tell application "Terminal" to do script ...'

while it was itself running inside **iTerm**. A stray Terminal.app window opened
somewhere the session could not see, the session reported success, and a human had
to go and close it. The signal was sitting in that session's own environment and
went unread — `TERM_PROGRAM=iTerm.app`, `LC_TERMINAL=iTerm2`, `ITERM_SESSION_ID`,
and a process ancestry ending in `/Applications/iTerm.app/Contents/MacOS/iTerm2`.
Two independent signals, neither consulted, a plausible assumption substituted for
both.

That is worth MECHANISING rather than documenting, because a window in the wrong
terminal is invisible to the process that opened it: there is no error, and
"success" is what gets reported. A rule in a document cannot fail; this can.

THE THREE THINGS THIS GUARANTEES.

  1. RESOLUTION IS ORDERED AND EXPLICIT. An explicit override, then the env markers
     each terminal sets, then the process ancestry, then — and only then — a
     platform default.
  2. IT ALWAYS SAYS WHICH ONE IT CHOSE AND HOW. `resolve()` returns `how`, a short
     string naming the signal it believed ("TERM_PROGRAM=iTerm.app",
     "ancestry: iTerm.app", "default: nothing identified the host"). A caller that
     prints it turns a silent wrong guess into a visible one.
  3. AN UNKNOWN HOST IS NOT SILENTLY TERMINAL.APP. When nothing identifies the
     terminal, or the terminal has no way to be driven, `spawn_argv` returns None
     and the caller prints the command for the human instead. Opening the wrong
     app is strictly worse than opening nothing: nothing is visible, and the wrong
     app is not.

Layer rule: `core` is the bottom layer — stdlib only, no `brain`, no `board`.
"""
import os
import shlex
import subprocess

# Every host this module can name, as `id -> (display name, macOS bundle name)`.
# The bundle name is what the process-ancestry walk matches and what AppleScript
# addresses; None means "no bundle / not macOS-driven".
HOSTS = {
    "iterm2": ("iTerm2", "iTerm.app"),
    "apple_terminal": ("Terminal.app", "Terminal.app"),
    "wezterm": ("WezTerm", "WezTerm.app"),
    "ghostty": ("Ghostty", "Ghostty.app"),
    "kitty": ("kitty", "kitty.app"),
    "alacritty": ("Alacritty", "Alacritty.app"),
    "vscode": ("VS Code integrated terminal", "Visual Studio Code.app"),
    "hyper": ("Hyper", "Hyper.app"),
    "warp": ("Warp", "Warp.app"),
    "tabby": ("Tabby", "Tabby.app"),
    "rio": ("Rio", "Rio.app"),
    "windows_terminal": ("Windows Terminal", None),
    "konsole": ("Konsole", None),
    "gnome_terminal": ("GNOME Terminal", None),
    "tmux": ("tmux (no host identified beyond it)", None),
    "unknown": ("unknown", None),
}

# `$TERM_PROGRAM` values, verbatim as each terminal sets them. Matched
# case-insensitively because they are not consistent about it between versions.
TERM_PROGRAM = {
    "iterm.app": "iterm2",
    "apple_terminal": "apple_terminal",
    "wezterm": "wezterm",
    "ghostty": "ghostty",
    "vscode": "vscode",
    "hyper": "hyper",
    "warpterminal": "warp",
    "tabby": "tabby",
    "rio": "rio",
    "alacritty": "alacritty",
    "kitty": "kitty",
}

# Terminals that announce themselves with their OWN variable instead of (or as well
# as) `$TERM_PROGRAM`. Checked in this order; first hit wins.
ENV_MARKERS = (
    ("ITERM_SESSION_ID", "iterm2"),
    ("WEZTERM_PANE", "wezterm"),
    ("WEZTERM_EXECUTABLE", "wezterm"),
    ("GHOSTTY_RESOURCES_DIR", "ghostty"),
    ("GHOSTTY_BIN_DIR", "ghostty"),
    ("KITTY_WINDOW_ID", "kitty"),
    ("ALACRITTY_WINDOW_ID", "alacritty"),
    ("ALACRITTY_SOCKET", "alacritty"),
    ("WT_SESSION", "windows_terminal"),
    ("KONSOLE_VERSION", "konsole"),
    ("GNOME_TERMINAL_SCREEN", "gnome_terminal"),
    ("VSCODE_INJECTION", "vscode"),
)

# macOS bundle name -> host id, for the ancestry walk. Keyed on the `.app` segment
# of the executable path, which is what `ps -o comm=` prints for a bundled app.
BUNDLES = {bundle: hid for hid, (_, bundle) in HOSTS.items() if bundle}

ENV_OVERRIDE = "TASK_STATION_TERMINAL"
ANCESTRY_LIMIT = 12


def _env(env):
    return os.environ if env is None else env


def resolve(env=None, ancestry=None, platform=None):
    """Which terminal is hosting this process.

    Returns `{"id", "name", "how"}`. `how` is the short reason — print it. A caller
    that says "opening in iTerm2 (TERM_PROGRAM=iTerm.app)" makes a wrong guess
    visible the moment it happens, which is the whole failure being repaired: the
    stray Terminal.app window produced no error and was reported as success.

    ORDER, and each step is there because the one before it can be absent:

      1. `$TASK_STATION_TERMINAL` — an explicit answer always wins.
      2. `$LC_TERMINAL` — iTerm2 sets it, and it is the only marker that survives
         `ssh` and `tmux`, so it is asked before the rest.
      3. `$TERM_PROGRAM` — set by most terminals, wiped by some multiplexers.
      4. per-terminal markers (`$KITTY_WINDOW_ID`, `$WEZTERM_PANE`, …) — several
         terminals leave `$TERM_PROGRAM` unset and announce themselves this way.
      5. the PROCESS ANCESTRY — env can be scrubbed (a detached re-exec, a `sudo`,
         a login shell that resets it); the parent chain cannot. On macOS this ends
         in `/Applications/iTerm.app/Contents/MacOS/iTerm2`, and that path was the
         second unread signal in the incident.
      6. a default, ANNOUNCED AS A DEFAULT. `how` says "nothing identified the
         host", so a caller can decline to act on it rather than guessing.
    """
    e = _env(env)
    override = (e.get(ENV_OVERRIDE) or "").strip().lower()
    if override:
        return _host(override if override in HOSTS else "unknown",
                     "%s=%s" % (ENV_OVERRIDE, override))
    if (e.get("LC_TERMINAL") or "").strip().lower() == "iterm2":
        return _host("iterm2", "LC_TERMINAL=iTerm2")
    tp = (e.get("TERM_PROGRAM") or "").strip()
    if tp.lower() in TERM_PROGRAM:
        return _host(TERM_PROGRAM[tp.lower()], "TERM_PROGRAM=%s" % tp)
    for var, hid in ENV_MARKERS:
        if e.get(var):
            return _host(hid, "$%s is set" % var)
    walked = ancestry_host(ancestry=ancestry, env=e)
    if walked:
        hid, proc = walked
        return _host(hid, "ancestry: %s" % proc)
    if tp:
        # A `$TERM_PROGRAM` this module has never heard of is still a name, and
        # naming it beats "unknown" — the reader learns which terminal to teach it.
        return _host("unknown", "TERM_PROGRAM=%s — not a terminal this knows" % tp)
    if e.get("TMUX"):
        return _host("tmux", "$TMUX is set and nothing named the host outside it")
    return _host("unknown", "nothing identified the host")


def _host(hid, how):
    name = HOSTS.get(hid, HOSTS["unknown"])[0]
    return {"id": hid, "name": name, "how": how}


def ancestry_host(ancestry=None, env=None, limit=ANCESTRY_LIMIT):
    """Walk the parent-process chain for a terminal we recognise, as `(id, proc)`.

    `ancestry` is injectable — the whole point of splitting it out is that the walk
    is otherwise untestable without spawning real processes. Failure is None, never
    an exception: a resolver that raises is worse than one that says "I don't
    know", because the caller's fallback is to print a command for a human, which
    is always safe."""
    for proc in (ancestry if ancestry is not None else _parent_commands(env)):
        for segment in str(proc).split("/"):
            if segment in BUNDLES:
                return BUNDLES[segment], segment
        base = os.path.basename(str(proc)).lower()
        for hid in ("wezterm", "ghostty", "kitty", "alacritty", "konsole", "tabby",
                    "rio", "hyper"):
            if base == hid or base.startswith(hid):
                return hid, os.path.basename(str(proc))
    return None


def _parent_commands(env=None, limit=ANCESTRY_LIMIT):
    """This process's ancestors' executables, nearest first. `ps` rather than any
    library: stdlib-only is the layer rule, and `ps -o comm=` prints the full
    bundle path on macOS, which is exactly the string that identifies the app."""
    out, pid = [], os.getppid()
    for _ in range(limit):
        if pid <= 1:
            break
        try:
            r = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)],
                               capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            break
        line = (r.stdout or "").strip()
        if not line:
            break
        parts = line.split(None, 1)
        if len(parts) != 2:
            break
        out.append(parts[1].strip())
        try:
            pid = int(parts[0])
        except ValueError:
            break
    return out


# ---------------------------------------------------------------------- spawning
#
# HOW EACH TERMINAL OPENS A WINDOW. Two mechanisms only:
#
#   applescript  the app is scripted through an Apple Event. `spawn_argv` returns
#                None and the caller uses the AppleScript in `open-session-window.sh`
#                — an Apple Event cannot be expressed as an argv.
#   argv         the terminal ships a CLI that opens a window and runs a command.
#
# A host in NEITHER column cannot be driven, and that is reported rather than
# papered over with Terminal.app. See `spawn_plan`.
APPLESCRIPT_HOSTS = ("iterm2", "apple_terminal")

ARGV_SPAWN = {
    "wezterm": lambda cmd: ["wezterm", "start", "--", "bash", "-lc", cmd],
    "ghostty": lambda cmd: ["open", "-na", "Ghostty", "--args",
                            "-e", "bash", "-lc", cmd],
    "kitty": lambda cmd: ["kitty", "--single-instance", "bash", "-lc", cmd],
    "alacritty": lambda cmd: ["alacritty", "-e", "bash", "-lc", cmd],
}


def spawn_plan(cmd, env=None, ancestry=None, host=None):
    """How to open a new window running `cmd` in THIS terminal.

    Returns `{"host", "how", "mechanism", "argv", "reason"}`. `mechanism` is
    `"applescript"`, `"argv"`, or `None` — and `None` is a real answer, not a
    failure to produce one. When it is None the caller must print the command for
    the human and say which terminal it could not drive.

    THE ONE THING THIS MUST NEVER DO is fall back to Terminal.app on a host it did
    not recognise. That fallback is the incident: a window opened in an app the
    session was not in, no error, success reported, a human left to find it."""
    h = host or resolve(env=env, ancestry=ancestry)
    plan = {"host": h["id"], "name": h["name"], "how": h["how"],
            "mechanism": None, "argv": None, "reason": ""}
    if h["id"] in APPLESCRIPT_HOSTS:
        plan["mechanism"] = "applescript"
        return plan
    builder = ARGV_SPAWN.get(h["id"])
    if builder:
        plan["mechanism"] = "argv"
        plan["argv"] = builder(cmd)
        return plan
    plan["reason"] = (
        "%s has no window-spawn this knows about (%s). Opening a window in a "
        "DIFFERENT terminal would be worse than opening none — a window you cannot "
        "see reports success. Run the command yourself:\n  %s"
        % (h["name"], h["how"], cmd))
    return plan


def describe(plan):
    """The one line a caller prints so the choice is visible. Naming the SIGNAL and
    not just the app is what makes a wrong guess catchable by the person reading."""
    if plan.get("mechanism"):
        return "opening a new window in %s (%s)" % (plan["name"], plan["how"])
    return "cannot open a window in %s (%s)" % (plan["name"], plan["how"])


def shell_report(env=None, ancestry=None):
    """`id`/`name`/`how` as shell assignments, for the two `.sh` spawners to `eval`.

    They are bash and this is Python, and duplicating the table into shell is how
    the two copies drift — which is the shape of the original bug at one remove.
    One resolver, two consumers."""
    h = resolve(env=env, ancestry=ancestry)
    return "\n".join("TS_TERM_%s=%s" % (k.upper(), shlex.quote(str(h[k])))
                     for k in ("id", "name", "how"))


def main(argv=None):
    """`python3 -m core.termhost [--shell|--json]` — the resolver as a command, so
    the bash spawners and a human asking "what am I in?" get the same answer from
    the same table."""
    import argparse
    import json as _json
    ap = argparse.ArgumentParser(prog="termhost",
                                 description="Identify the host terminal.")
    ap.add_argument("--shell", action="store_true",
                    help="emit TS_TERM_* shell assignments to eval")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    a = ap.parse_args(argv)
    if a.shell:
        print(shell_report())
        return 0
    h = resolve()
    if a.json:
        print(_json.dumps(h))
    else:
        print("%s  (%s)" % (h["name"], h["how"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
