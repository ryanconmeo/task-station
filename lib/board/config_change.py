# config_change.py
"""The ConfigChange hook's path validator — does this settings file still point at
things that EXIST?

WHAT THE EVENT IS. Claude Code fires `ConfigChange` when one of its own config
files changes mid-session, BEFORE the change takes effect, and a hook may block the
save (exit 2). The one class of mistake worth catching there is a path that does not
resolve: a `statusLine.command` naming a script that moved, a hook command pointing
into a plugin-cache dir that a `/plugin update` replaced, an `env` value naming a
directory that was deleted. None of those fail loudly — the feature just silently
stops working, and the user finds out days later.

WHY IT WARNS BY DEFAULT. Blocking a config save is a big hammer, and a block here is
TRANSCRIPT-SILENT: the user sees nothing at all. So the default is WARN — write one
hook-health record and let the save through, which surfaces as the next session
start's one-line nag. Enforce (`config_change_enforce`) is opt-in, and even then the
record is written FIRST, because the record is the ONLY trace a block leaves.

WHAT COUNTS AS A PATH, and why the rules are this narrow. Guessing which config
values are paths reports settings as missing files, so only two shapes qualify:

  * a string value that is ABSOLUTE (`/…`) or HOME-ROOTED (`~/…`) — checked whole,
    because a real path may well contain spaces;
  * for a value under a `…command` key, the first absolute/home-rooted token of the
    command string — so `bash /abs/host.sh  # marker` is checked at the script, not
    at `bash`.

Everything else is deliberately out of scope: a RELATIVE path (resolved against a cwd
we do not know), a bare command name (a PATH lookup, not a file), anything carrying a
`$VAR` (unexpanded), and anything carrying a glob character (`*`/`?` — a permission
rule, not a file). Each of those exclusions is one class of false positive that would
have made the check untrustworthy, and an untrustworthy check is worse than none.

Everything fails OPEN: an unparseable file is Claude Code's problem to report, not
ours, and we NEVER block on our own inability to read something.
"""
import json
import os
import shlex

# Unresolvable paths named inline in the one-line record; the rest roll up as
# "+N more" — the same shape hook_health.nag uses, and for the same reason: a
# record is one line to read, not a list.
NAMED = 3
# Glob characters. A value carrying one is a MATCH RULE (`Read(/etc/**)`,
# `allowWrite: ~/x/*`), never a file we can stat.
GLOB_CHARS = "*?"


def rooted(value):
    """True for the only two shapes we can resolve without guessing a cwd: an
    absolute path, or a `~/`-rooted one."""
    return isinstance(value, str) and (value.startswith("/") or value.startswith("~/"))


def _checkable(value):
    return rooted(value) and "$" not in value and not any(c in value for c in GLOB_CHARS)


def command_path(cmd):
    """The script/program a command string names, or None.

    The FIRST absolute/home-rooted token wins, not literally `argv[0]`: the shipped
    shape is `bash /abs/path/on_hook.sh`, whose argv[0] is the interpreter and whose
    interesting half is the argument. Flags are skipped; a bare `bash` alone yields
    None (a PATH lookup is out of scope). `shlex` with comments=True so the trailing
    `# task-station-managed…` marker on our own installed commands is not mistaken
    for an argument."""
    if not isinstance(cmd, str) or not cmd.strip():
        return None
    try:
        tokens = shlex.split(cmd, comments=True)
    except ValueError:                       # unbalanced quotes → do the dumb split,
        tokens = [t.strip("\"'") for t in cmd.split()]   # peeling the quotes ourselves
    for t in tokens:
        if t.startswith("-"):
            continue
        if _checkable(t):
            return t
    return None


def _candidates(key, value):
    """The paths this (key, string) pair DECLARES, as a list. `key` decides which rule
    applies — a `…command` key is a command string, anything else is a plain value.

    A plain value carrying `os.pathsep` is a path LIST (`PATH`, `PYTHONPATH`), split
    and checked per entry. Checking `"/usr/local/bin:/usr/bin"` whole would report a
    perfectly good `PATH` as a missing file — one of the false positives that would
    have made this check untrustworthy."""
    if not isinstance(value, str):
        return []
    v = value.strip()
    if not v:
        return []
    if str(key).lower().endswith("command"):
        p = command_path(v)
        return [p] if p else []
    if os.pathsep in v:
        return [seg for seg in v.split(os.pathsep) if _checkable(seg)]
    return [v] if _checkable(v) else []


def declared_paths(node, prefix=""):
    """Every path-shaped string in a parsed config, as `[(label, path), …]`.

    `label` is the dotted/indexed location (`hooks.Stop[0].hooks[0].command`) so a
    report can name WHERE the bad value lives, which is the only part of it a human
    can act on. A list element inherits its LIST's key for the command test, so
    `hooks[].command` entries are read as commands wherever they sit."""
    out = []
    if isinstance(node, dict):
        for k, v in node.items():
            label = "%s.%s" % (prefix, k) if prefix else str(k)
            if isinstance(v, (dict, list)):
                out.extend(declared_paths(v, label))
            else:
                out.extend((label, p) for p in _candidates(k, v))
    elif isinstance(node, list):
        key = prefix.rsplit(".", 1)[-1]
        for i, v in enumerate(node):
            label = "%s[%d]" % (prefix, i)
            if isinstance(v, (dict, list)):
                out.extend(declared_paths(v, label))
            else:
                out.extend((label, p) for p in _candidates(key, v))
    return out


def load(path):
    """The parsed config, or None when it cannot be read as a JSON object.

    None is the FAIL-OPEN signal and every caller must treat it as "say nothing":
    a malformed settings file is Claude Code's own error to report, and blocking a
    save because we could not parse it would trap the user's fix inside the file
    they are trying to fix."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def unresolvable(path, exists=os.path.exists, data=None):
    """`[(label, path), …]` for declared paths that do NOT resolve — `[]` when the
    file is healthy OR unreadable (fail-open; see `load`).

    `exists` is injected for the same reason `checker.pointers` injects it: a test
    can describe a filesystem it did not have to build."""
    data = load(path) if data is None else data
    if data is None:
        return []
    out = []
    for label, p in declared_paths(data):
        if not exists(os.path.expanduser(p)):
            out.append((label, p))
    return out


def detail(changed_file, findings):
    """The ONE line the hook-health record carries, or None when nothing is wrong.

    Names the file, the count, and up to NAMED offenders with their location, because
    "3 unresolvable paths" with no names is a report nobody can act on."""
    if not findings:
        return None
    shown = findings[:NAMED]
    named = "; ".join("%s → %s" % (label, p) for label, p in shown)
    more = len(findings) - len(shown)
    if more > 0:
        named += ", +%d more" % more
    return "%s: %d unresolvable path(s): %s" % (
        os.path.basename(changed_file) or changed_file, len(findings), named)
