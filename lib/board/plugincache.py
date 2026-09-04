"""plugincache.py — keep the plugin cache to the version in use plus a rollback or two.

WHAT THE CACHE IS. `/plugin update` does not replace an install, it adds one:
``~/.claude/plugins/cache/<owner>/<plugin>/<version>/`` gains a directory per release
and never loses one. ``installed_plugins.json`` registers exactly ONE of them as the
install. On the authoring machine that reached 36 directories and 325 MB for a plugin
whose single registered copy is ~9 MB — 35 of them dead weight, none of them ever read.

WHY IT PRUNES HERE AND NOT BY HAND. The engine symlink
(``~/.claude/task-station-engine``) is written by the SessionStart hook and points into
the ACTIVE version; hand-editing the cache underneath it is a known way to break
activation. So the prune runs where activation already happens — as one of
SessionStart's DETACHED steps, right after the hook has re-pointed that symlink — and
it is the flow's job, never a person's.

WHAT IT WILL NOT DELETE, and each of these is a veto on its own:

  * the version ``installed_plugins.json`` registers (the install itself),
  * the version ``$CLAUDE_PLUGIN_ROOT`` names (this very session's code),
  * the version the engine symlink resolves into,
  * :data:`KEEP_ROLLBACKS` further versions, newest first by semver, so a bad release
    can be rolled back to without a re-download,
  * any version a LIVE process still marks in ``.in_use/`` (the harness writes one
    ``<pid>`` file there per session using the directory). A stale marker whose pid is
    gone is ignored; a pid that has been recycled onto another process only ever
    RETAINS, which is the safe direction to be wrong in.

Everything else goes. A directory whose name is not a semver is never touched at all —
if it is not a release, this module has no opinion about it.

Stdlib only, python3.9+.
"""
import json
import os
import re
import shutil
import sys

# How many versions BESIDES the one in use are kept, newest first. Two is a rollback
# and a rollback's rollback; the number is here, once, because a retention policy
# buried in a loop is a policy nobody can find or change.
KEEP_ROLLBACKS = 2

_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _semver(name):
    """`(major, minor, patch)` for a release directory, or None when the name is not
    one — the sort key AND the membership test, so the two can never disagree."""
    m = _SEMVER.match(name or "")
    return tuple(int(g) for g in m.groups()) if m else None


def _alive(pid):
    """Whether `pid` is a live process. Signal 0 checks existence without touching it;
    EPERM means it exists and is someone else's, which still counts as alive."""
    try:
        os.kill(int(pid), 0)
    except PermissionError:
        return True
    except Exception:
        return False
    return True


def in_use_versions(cache_dir):
    """Versions under `cache_dir` that a LIVE process still marks in `.in_use/`."""
    held = set()
    for name in _versions(cache_dir):
        marks = os.path.join(cache_dir, name, ".in_use")
        try:
            pids = os.listdir(marks)
        except OSError:
            continue
        if any(_alive(p) for p in pids):
            held.add(name)
    return held


def _versions(cache_dir):
    """Every release directory under `cache_dir` (real directories, not symlinks)."""
    try:
        names = os.listdir(cache_dir)
    except OSError:
        return []
    return [n for n in names
            if _semver(n)
            and os.path.isdir(os.path.join(cache_dir, n))
            and not os.path.islink(os.path.join(cache_dir, n))]


def registered_version(plugin_root, config_dir=None):
    """The version ``installed_plugins.json`` registers for the plugin at
    `plugin_root`, or None.

    Matched on the install's CACHE DIRECTORY — the parent of both paths — rather
    than on the plugin's name or on the running path. On the name, because a name
    this module would have to guess is never guessed; on the running path, because
    the registered version is exactly the one that may DIFFER from the running one,
    and a check that only fires when they agree protects nothing."""
    cfg = config_dir or os.environ.get("CLAUDE_CONFIG_DIR") or \
        os.path.join(os.path.expanduser("~"), ".claude")
    path = os.path.join(cfg, "plugins", "installed_plugins.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except Exception:
        return None
    want = os.path.dirname(os.path.realpath(plugin_root))
    for installs in (doc.get("plugins") or {}).values():
        for install in installs or []:
            if not isinstance(install, dict):
                continue
            got = install.get("installPath")
            if got and os.path.dirname(os.path.realpath(got)) == want:
                return install.get("version") or os.path.basename(
                    os.path.realpath(got))
    return None


def plan(plugin_root, config_dir=None, keep=KEEP_ROLLBACKS, engine_link=None):
    """`(cache_dir, keep_set, prune_list)` — what a prune WOULD do, decided and
    returnable without deleting anything, because a destructive pass that cannot be
    inspected first is a destructive pass nobody can review."""
    root = os.path.realpath(plugin_root or "")
    cache_dir = os.path.dirname(root)
    versions = _versions(cache_dir)
    if not versions:
        return cache_dir, set(), []
    pinned = {os.path.basename(root)}
    reg = registered_version(root, config_dir=config_dir)
    if reg:
        pinned.add(reg)
    link = engine_link or os.path.join(
        os.environ.get("CLAUDE_CONFIG_DIR")
        or os.path.join(os.path.expanduser("~"), ".claude"),
        "task-station-engine")
    try:
        target = os.path.realpath(link)
        if target.startswith(cache_dir + os.sep):
            pinned.add(target[len(cache_dir) + 1:].split(os.sep)[0])
    except Exception:
        pass
    pinned |= in_use_versions(cache_dir)
    rest = sorted((v for v in versions if v not in pinned), key=_semver, reverse=True)
    keep = pinned | set(rest[:max(0, int(keep))])
    return cache_dir, keep, [v for v in versions if v not in keep]


def prune(plugin_root, config_dir=None, keep=KEEP_ROLLBACKS, dry_run=False,
          engine_link=None):
    """Apply :func:`plan`; return the versions actually removed.

    Best-effort by construction: a directory that will not delete is skipped and the
    others still go. Nothing here can fail a session — the caller is a detached
    housekeeping step."""
    cache_dir, _keep, doomed = plan(plugin_root, config_dir=config_dir, keep=keep,
                                    engine_link=engine_link)
    removed = []
    for name in doomed:
        target = os.path.join(cache_dir, name)
        if dry_run:
            removed.append(name)
            continue
        try:
            shutil.rmtree(target)
            removed.append(name)
        except Exception:
            continue
    return removed


def cmd_prune_cache(a):
    """`task-station hook prune-cache [--keep N] [--dry-run]` — the SessionStart step.

    Prints one line per removed version to STDERR (stdout belongs to the hook's JSON)
    and returns nothing to the harness. Silent when there is nothing to prune, which is
    the steady state after the first run."""
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if not root:
        return                              # not running as a plugin: no cache to own
    keep = getattr(a, "keep", None)
    keep = KEEP_ROLLBACKS if keep is None else keep
    if getattr(a, "dry_run", False):
        cache_dir, kept, doomed = plan(root, keep=keep)
        sys.stderr.write("[task-station] plugin cache %s: keep %s, prune %s\n"
                         % (cache_dir, ",".join(sorted(kept)) or "-",
                            ",".join(sorted(doomed)) or "-"))
        return
    for name in prune(root, keep=keep):
        sys.stderr.write("[task-station] pruned plugin cache %s (unregistered, "
                         "beyond %s rollbacks, not in use)\n" % (name, keep))
