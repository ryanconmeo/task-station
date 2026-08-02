#!/usr/bin/env python3
"""release.py — single-source version bumper for Task Station.

Task Station's version lives in FOUR files that must never drift:
  1. .claude-plugin/plugin.json         ("version")
  2. .claude-plugin/marketplace.json    (plugins[0].version — test_manifest guards ==1)
  3. README.md                          (shields.io badge: version-<sem>-blue)
  4. CHANGELOG.md                       (## [<sem>] — <date> heading)

'Deploy' is: bump the version + push main; version-gated autoUpdate pulls it and a
SessionStart hook repoints the ~/.claude/task-station-engine symlink. This tool does
the *bump* half — rewriting all four consistently — so a release can never ship with
a drifted README badge (which silently happened: badge stuck at 1.59.0 while the
plugin was 1.81.0).

Edit-only: it touches NO git. It is importable (set_version / bump_version / validate
+ main) so it's unit-tested against a temp fixture.

Usage:
  release.py --set X.Y.Z         [--date YYYY-MM-DD]
  release.py --bump {major|minor|patch}  [--date YYYY-MM-DD]
  # --root PATH overrides the repo root (defaults to this script's parent repo).

JSON edits are TARGETED (regex on the version token, then re-parsed to validate) so
the files' existing formatting and key order survive byte-for-byte — only the version
string changes. README/CHANGELOG get minimal-diff regex / line-splice edits too.
"""
import argparse
import datetime
import json
import os
import re
import sys

SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
# a "version": "X.Y.Z" token inside a JSON file (exactly one per manifest here)
_JSON_VER_RE = re.compile(r'("version"\s*:\s*")[^"]*(")')
# the shields.io version badge token: version-X.Y.Z-blue
_BADGE_RE = re.compile(r"(version-)\d+\.\d+\.\d+(-blue)")


def repo_root():
    """The repo root = the parent of this script's scripts/ dir."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _paths(root):
    return {
        "plugin.json": os.path.join(root, ".claude-plugin", "plugin.json"),
        "marketplace.json": os.path.join(root, ".claude-plugin", "marketplace.json"),
        "README.md": os.path.join(root, "README.md"),
        "CHANGELOG.md": os.path.join(root, "CHANGELOG.md"),
    }


def parse_semver(v):
    """(major, minor, patch) ints, or ValueError on anything not strict X.Y.Z."""
    m = SEMVER_RE.match(v or "")
    if not m:
        raise ValueError("not a valid semver (expected X.Y.Z): %r" % (v,))
    return tuple(int(g) for g in m.groups())


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def read_version(root):
    """Current version, read from plugin.json (the source of truth)."""
    with open(_paths(root)["plugin.json"], encoding="utf-8") as f:
        return json.load(f)["version"]


def _set_json_version(path, ver):
    """Replace ONLY the version token in a manifest, preserving all formatting and
    key order, then re-parse to guarantee the file is still valid JSON."""
    text = _read(path)
    new, n = _JSON_VER_RE.subn(r"\g<1>%s\g<2>" % ver, text)
    if n != 1:
        raise ValueError("%s: expected exactly one \"version\" token, found %d"
                         % (os.path.basename(path), n))
    json.loads(new)   # validate — raises on corruption
    _write(path, new)


def _set_readme_badge(path, ver):
    text = _read(path)
    new, n = _BADGE_RE.subn(r"\g<1>%s\g<2>" % ver, text)
    if n < 1:
        raise ValueError("README.md: shields version badge (version-X.Y.Z-blue) not found")
    _write(path, new)


def _changelog_scaffold(ver, date):
    return ("## [%s] — %s\n\n"
            "### Added\n- …\n\n"
            "### Changed\n- …\n\n"
            "### Fixed\n- …\n\n") % (ver, date)


def _ensure_changelog_entry(path, ver, date):
    """Prepend a new '## [ver] — date' scaffold above the newest entry, unless a
    heading for `ver` already exists (idempotent). Returns True if one was added."""
    text = _read(path)
    heading = "## [%s]" % ver
    for line in text.splitlines():
        if line.startswith(heading):
            return False
    lines = text.splitlines(keepends=True)
    insert_at = len(lines)
    for i, line in enumerate(lines):
        if line.startswith("## ["):
            insert_at = i
            break
    scaffold = _changelog_scaffold(ver, date)
    lines.insert(insert_at, scaffold)
    _write(path, "".join(lines))
    return True


def _badge_version(root):
    m = _BADGE_RE.search(_read(_paths(root)["README.md"]))
    return (m.group(0).split("-")[1]) if m else None


def set_version(root, ver, date=None):
    """Rewrite all four files to `ver`. Raises ValueError on bad semver / missing
    tokens. Returns a list of (file, message) summary lines."""
    parse_semver(ver)   # fail fast before touching anything
    date = date or datetime.date.today().isoformat()
    paths = _paths(root)
    summary = []
    _set_json_version(paths["plugin.json"], ver)
    summary.append(("plugin.json", "version → %s" % ver))
    _set_json_version(paths["marketplace.json"], ver)
    summary.append(("marketplace.json", "plugins[0].version → %s" % ver))
    _set_readme_badge(paths["README.md"], ver)
    summary.append(("README.md", "badge → version-%s-blue" % ver))
    added = _ensure_changelog_entry(paths["CHANGELOG.md"], ver, date)
    summary.append(("CHANGELOG.md",
                    "added '## [%s] — %s'" % (ver, date) if added
                    else "'## [%s]' already present (unchanged)" % ver))
    validate(root)      # re-read + cross-check all four agree
    return summary


def bump_version(root, part, date=None):
    """Bump the current plugin.json version by `part` (major/minor/patch)."""
    major, minor, patch = parse_semver(read_version(root))
    if part == "major":
        major, minor, patch = major + 1, 0, 0
    elif part == "minor":
        minor, patch = minor + 1, 0
    elif part == "patch":
        patch += 1
    else:
        raise ValueError("bump part must be major|minor|patch, got %r" % (part,))
    return set_version(root, "%d.%d.%d" % (major, minor, patch), date)


def validate(root):
    """Re-read all four and assert they agree. Returns the common version, or raises
    ValueError describing the inconsistency."""
    paths = _paths(root)
    pv = read_version(root)
    with open(paths["marketplace.json"], encoding="utf-8") as f:
        mv = json.load(f)["plugins"][0]["version"]
    bv = _badge_version(root)
    changelog = _read(paths["CHANGELOG.md"])
    problems = []
    if mv != pv:
        problems.append("marketplace.json version %s != plugin.json %s" % (mv, pv))
    if bv != pv:
        problems.append("README badge version %s != plugin.json %s" % (bv, pv))
    if ("## [%s]" % pv) not in changelog:
        problems.append("CHANGELOG.md has no '## [%s]' heading" % pv)
    if problems:
        raise ValueError("version inconsistency:\n  - " + "\n  - ".join(problems))
    return pv


def main(argv=None):
    ap = argparse.ArgumentParser(prog="release.py",
                                 description="Single-source version bumper.")
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--set", metavar="X.Y.Z", dest="set_ver",
                     help="set an explicit semver")
    grp.add_argument("--bump", choices=("major", "minor", "patch"),
                     help="bump the current version by this part")
    ap.add_argument("--date", default=None,
                    help="CHANGELOG date (YYYY-MM-DD); defaults to today")
    ap.add_argument("--root", default=None,
                    help="repo root (defaults to this script's parent repo)")
    a = ap.parse_args(argv)
    root = a.root or repo_root()
    try:
        summary = (set_version(root, a.set_ver, a.date) if a.set_ver
                   else bump_version(root, a.bump, a.date))
    except ValueError as e:
        sys.stderr.write("release.py: %s\n" % e)
        return 1
    ver = read_version(root)
    print("release.py: all four files now at %s" % ver)
    for name, msg in summary:
        print("  %-18s %s" % (name, msg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
