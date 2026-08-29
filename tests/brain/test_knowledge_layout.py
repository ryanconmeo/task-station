"""The knowledge model, asserted from outside the code that implements it.

Two guards, both about the same 2026-08-27 redesign:

  * THE SHIPPED TREE — the bundled vault scaffold is the four folded folders and
    none of the five it replaced. `test_init_home` proves what a fresh init
    *creates*; this proves what the package *ships*, which is the input to that.
  * THE PROSE — no document still describes the retired ``~/brains`` home. A
    stale layout in a doc is worse than a stale comment: the doc is what a new
    install reads before it runs anything, so it is the one surface where being
    wrong actually misroutes someone's files.

CHANGELOG.md is exempt by design: it is a history, and history says what was
true then. The customised-install fixture in ``test_init_home`` is exempt for the
opposite reason — its whole point is that an install whose paths are its own
keeps them, and ``~/brains/acme/...`` is what such an install looks like.
"""
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent

#: The post-fold folders, and the genre folders they replaced.
FOLDED = ("notes", "docs", "inbox", "mirror")
PRE_FOLD = ("projects", "plans", "raw", "reports")

#: Where a reader could be misdirected by a stale layout. Sources of prose and of
#: shipped assets — not the test tree, which carries deliberate legacy fixtures.
PROSE_ROOTS = ("README.md", "system-instructions.md", "docs", "skills",
               "templates", "commands", "lib")
PROSE_SUFFIXES = {".md", ".py", ".json", ".sh"}
#: A history is allowed to describe history.
EXEMPT = {"CHANGELOG.md"}


def _scaffold():
    return REPO / "lib/brain/vault-scaffold"


def _prose_files():
    for rel in PROSE_ROOTS:
        p = REPO / rel
        if p.is_file():
            yield p
        elif p.is_dir():
            for f in sorted(p.rglob("*")):
                if (f.is_file() and f.suffix in PROSE_SUFFIXES
                        and "__pycache__" not in f.parts and f.name not in EXEMPT):
                    yield f


class ScaffoldShipsTheFoldedTree(unittest.TestCase):
    def test_the_four_folders_are_present(self):
        for d in FOLDED:
            self.assertTrue((_scaffold() / d).is_dir(),
                            f"vault scaffold is missing {d}/")

    def test_none_of_the_pre_fold_folders_survive(self):
        for d in PRE_FOLD:
            self.assertFalse((_scaffold() / d).exists(),
                             f"vault scaffold still ships the retired {d}/")

    def test_lint_output_lands_under_mirror(self):
        self.assertTrue((_scaffold() / "mirror/health").is_dir())

    def test_the_scaffold_carries_no_memory_dir(self):
        """Memory belongs to the person, not to a brain — so the template for a
        brain must not contain one."""
        self.assertEqual(list(_scaffold().rglob("memory")), [])


class NoDocumentDescribesTheOldHome(unittest.TestCase):
    def test_no_shipped_prose_names_the_retired_brains_home(self):
        offenders = []
        for f in _prose_files():
            try:
                text = f.read_text(errors="ignore")
            except OSError:
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if "~/brains" in line:
                    offenders.append(f"{f.relative_to(REPO)}:{n}: {line.strip()[:90]}")
        self.assertEqual(offenders, [],
                         "these still describe the pre-2026-08-27 home:\n"
                         + "\n".join(offenders))

    def test_the_container_is_named_where_a_new_install_reads_it(self):
        """The positive half: it is not enough that the old path is gone — the
        new one has to be stated where someone setting up will actually see it."""
        for rel in ("README.md", "docs/BRAIN.md", "skills/brain-init/SKILL.md"):
            self.assertIn("~/knowledge", (REPO / rel).read_text(),
                          f"{rel} never names the knowledge container")


if __name__ == "__main__":
    unittest.main()
