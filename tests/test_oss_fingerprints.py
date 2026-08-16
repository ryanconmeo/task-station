"""THE SHIPPED TREE CARRIES NO PRIVATE FINGERPRINT.

task-station is a PUBLIC repo. Everything under `lib/`, `tools/`, `skills/`, `hooks/`,
`commands/` and the top-level docs is read by strangers and, in the case of a skill,
*executed as instruction* by their Claude. So a line that only resolves on the author's
machine is not a small blemish — it is an instruction a reader cannot follow.

WHAT THIS CAUGHT WHEN IT WAS WRITTEN. A shipped skill told the reader to consult a note
by slug for its calibration set. That note lives in a private personal wiki. A stranger
following the skill would look for a document that does not exist and cannot be obtained,
in the middle of a procedure whose whole point is that judgement must be calibrated. That
is worse than omitting the sentence: it reads like a resource, and it is a dead end.

The rules, narrow on purpose so this stays a guard rather than a nag:

  1. NO POINTERS TO DOCUMENTS THE PLUGIN DOES NOT SHIP. The list is explicit, because
     "does this document exist for the reader" is not a question a stdlib test can answer
     in general.
  2. NO ABSOLUTE HOME PATHS. `/Users/<someone>/…` in shipped code is never right — the
     data dir resolves through `paths.data_dir()`, and a skill addresses the plugin
     through `$CLAUDE_PLUGIN_ROOT` or the plugin-managed `~/.claude/…` handles.

WHY THERE IS NO ORG-IDENTIFIER RULE HERE, which is the interesting omission. That class
is guarded by a **pre-push hook that lives in `.git/hooks` and deliberately never in the
tree**, because the pattern list *is itself* the fingerprint — publishing the list would
publish the thing it exists to keep out. Duplicating those patterns in this file would
therefore defeat the reason the hook lives where it does. It would also not work: the
hook scans every pushed blob, including this one, so a literal list blocks its own push.
The first draft of this file learned that the honest way, and the fix is not to smuggle
the needles past the scanner — assembling them from fragments is an exemption wearing a
disguise, and exempting the scanner is how a guard quietly stops guarding. So the split
is deliberate: **the hook owns the identifier patterns, this file owns everything that is
safe to state in the open.**

WHAT IT DELIBERATELY DOES NOT COVER, so nobody reads a green run as more than it is:

  * `CHANGELOG.md` — its `task #N` provenance refs are an established convention of this
    repo's own history, and rewriting history to satisfy a lint is worse than the lint.
  * `tests/` — fixture numbers are arbitrary data, not references.
  * The demo peer fixtures, which are deliberately named fake people (see `seed_demo.py`).
  * Author attribution in `LICENSE` / the README's licence line, which is correct and
    required.
"""
import os
import re
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Trees a stranger reads or executes. `tests/` is deliberately absent (rule note above).
_SHIPPED_DIRS = ("lib", "tools", "skills", "hooks", "commands")
_SHIPPED_FILES = ("README.md", "CONTRIBUTING.md", "SECURITY.md", "PRIVACY.md",
                  "system-instructions.md", "CATEGORIES.md")
_SHIPPED_DOCS = ("docs/specs/BOARD-BEHAVIOR.md", "docs/ARCHITECTURE.md")

_SKIP_DIRS = {"__pycache__", ".git", "node_modules", "vault-scaffold"}
_TEXT_EXT = {".py", ".md", ".sh", ".json", ".html", ".css", ".js", ".txt", ""}

# Documents the plugin does NOT ship. Citing one sends a reader somewhere they cannot go.
# These are safe to state literally: they name planning notes, not the organisation — the
# org patterns are the pre-push hook's, and stay out of the tree (see the module note).
_PRIVATE_NOTES = ("migration-rubric-improvement-loop", "open-work-register",
                  "silo-migration", "silo-orchestration", "master-plan.md")

_HOME_PATH = re.compile(r"/(?:Users|home)/[a-z][a-z0-9._-]+/", re.I)
# The one legitimate home-shaped string: a doc showing what a resolved path looks like is
# fine only when it is clearly a placeholder.
_HOME_EXEMPT = re.compile(r"/(?:Users|home)/(?:me|you|user|username|<[^>]+>)/", re.I)


def _shipped_files():
    for rel in _SHIPPED_FILES + _SHIPPED_DOCS:
        p = os.path.join(_ROOT, rel)
        if os.path.exists(p):
            yield rel, p
    for d in _SHIPPED_DIRS:
        base = os.path.join(_ROOT, d)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [x for x in dirnames if x not in _SKIP_DIRS]
            for fn in filenames:
                if os.path.splitext(fn)[1].lower() not in _TEXT_EXT:
                    continue
                p = os.path.join(dirpath, fn)
                yield os.path.relpath(p, _ROOT), p


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except (UnicodeDecodeError, OSError):
        return ""


class FingerprintTest(unittest.TestCase):
    def test_no_shipped_file_cites_a_document_the_plugin_does_not_ship(self):
        """Rule 1 — the one this file was written for. A skill that names a private note
        as its calibration set reads like a resource and is a dead end."""
        hits = []
        for rel, path in _shipped_files():
            body = _read(path)
            for slug in _PRIVATE_NOTES:
                if slug in body:
                    hits.append("%s cites %r" % (rel, slug))
        self.assertEqual(hits, [], "shipped files cite documents the plugin does not "
                                   "ship:\n  " + "\n  ".join(hits))

    def test_no_shipped_file_hardcodes_somebodys_home_directory(self):
        """The data dir resolves through `paths.data_dir()` and a skill addresses the
        plugin through `$CLAUDE_PLUGIN_ROOT`; an absolute home path in shipped code is
        never right, and works on exactly one machine."""
        hits = []
        for rel, path in _shipped_files():
            for i, line in enumerate(_read(path).splitlines(), 1):
                if _HOME_PATH.search(line) and not _HOME_EXEMPT.search(line):
                    hits.append("%s:%d %s" % (rel, i, line.strip()[:90]))
        self.assertEqual(hits, [], "shipped files hardcode a home directory:\n  "
                                   + "\n  ".join(hits))

    def test_the_guard_actually_scans_the_shipped_trees(self):
        """A guard that silently walked nothing would pass forever — the failure mode
        every check in this codebase is written against."""
        rels = [rel for rel, _p in _shipped_files()]
        self.assertGreater(len(rels), 50)
        for expect in ("README.md", os.path.join("skills", "judge", "SKILL.md")):
            self.assertIn(expect, rels)


if __name__ == "__main__":
    unittest.main()
