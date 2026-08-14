"""Audit guard: the shipped code surface carries no hardcoded home paths.

RE-DERIVED for this repo in 3.0.0 Phase 4 (chunk 5a) from the brain source tree's
``tests/test_no_hardcoded_paths.py`` @ 0.14.0 (34 lines). The CLAIM is the
source's — an install must work on any machine, so every path flows through
``brain.config`` / ``core.paths`` / ``expanduser`` — but the NEEDLES are not: the
source's list named two of its author's own vault directories and a retired
module path, and copying it here would have been both meaningless and, in one
case, wrong.

WHAT CHANGED, AND WHY (the whole derivation, so the next reader can re-check it):

  * ``"/Users/"`` — KEPT. An absolute macOS home path in shipped code is the
    original sin this test exists for.
  * ``"/home/"`` — ADDED. Same claim, Linux spelling; the source only ever ran on
    one platform.
  * ``str(Path.home())`` — ADDED, computed at run time. Catches a machine whose
    home is under neither prefix, and — deliberately — writes nobody's username
    into this file.
  * the source's two ``Documents/<vault>`` needles — DROPPED. The first named a
    directory that does not exist in this repo's world (it was the author's own
    old vault). The second, ``Documents/Obsidian Vault``, WOULD FIRE — five
    times, on ``lib/board/**`` help text that offers
    ``--obsidian-vault "~/Documents/Obsidian Vault"`` as an EXAMPLE — a
    tilde-relative string in a message, which is exactly the correct way to name
    a user path and not the defect this test hunts. A needle that flags correct
    code teaches people to add exemptions.
  * ``".claude/brain/"`` — DROPPED. It named the source's own retired module
    location; nothing in this repo can regress to it.

SCOPE: all of ``lib/`` (both planes, every file type — .py, .sh, .json, .md and
the shipped vault scaffold), not just this chunk's files. The source scanned its
whole executable surface too. ``__pycache__`` is EXCLUDED: compiled bytecode
embeds the absolute path of whatever machine imported the module (CI:
``/home/runner/...``), it is gitignored and never ships, and scanning it turned
this guard red on every CI runner while the real shipped surface was clean.

ALLOWLIST: exactly one file, ``lib/open-session-window.sh``, whose line 11 is a
usage COMMENT showing a terminal command (``cd /Users/me && claude --resume …``).
It is board-side, it is documentation, and it is the single known literal — named
here rather than pattern-exempted so that adding a second one takes a decision.
"""
import unittest
from pathlib import Path

from tests.brain.base import LIB


def forbidden():
    """Home-path literals. The third is resolved when the test RUNS, not at
    import: it must be the real home, and it must never be written down here."""
    return {"/Users/", "/home/", str(Path.home())}


# Relative to lib/. The one known board-side literal (chunk 4b §6.4 finding 6):
# a comment demonstrating a shell command, not a resolved path.
ALLOWLIST = {"open-session-window.sh"}


def _surface_files():
    return [f for f in sorted(LIB.rglob("*"))
            if f.is_file()
            and "__pycache__" not in f.parts
            and str(f.relative_to(LIB)) not in ALLOWLIST]


class NoHardcodedPathsTest(unittest.TestCase):
    def test_no_forbidden_literals_in_the_shipped_surface(self):
        hits = []
        needles = forbidden()
        for f in _surface_files():
            try:
                text = f.read_text(errors="ignore")
            except OSError:                      # unreadable ⇒ not a source file
                continue
            for i, line in enumerate(text.splitlines(), 1):
                for pat in needles:
                    if pat in line:
                        hits.append(f"{f.relative_to(LIB)}:{i}: {pat!r}  {line.strip()}")
        self.assertEqual(hits, [], "hardcoded home paths found:\n" + "\n".join(hits))

    def test_the_scan_actually_reached_the_tree(self):
        """A guard that silently scans nothing passes forever. Positive control:
        the sweep must see both planes and the shipped scaffold."""
        seen = {str(f.relative_to(LIB)) for f in _surface_files()}
        self.assertIn("brain/config.py", seen)
        self.assertIn("brain/hooks/inject.py", seen)
        self.assertIn("brain/vault-scaffold/CLAUDE.md", seen)
        self.assertIn("board/memos.py", seen)
        self.assertGreater(len(seen), 100)

    def test_the_allowlisted_file_is_the_reason_the_allowlist_exists(self):
        """If the one allowed literal ever leaves that file, the allowlist should
        go with it — this fails when it is no longer earning its place."""
        f = LIB / "open-session-window.sh"
        self.assertTrue(f.exists(), "the allowlisted file is gone; drop the allowlist")
        self.assertIn("/Users/", f.read_text(errors="ignore"))


if __name__ == "__main__":
    unittest.main()
