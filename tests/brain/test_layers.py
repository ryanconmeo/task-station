"""Layer guard for the 3.0.0 split: core is the bottom layer.

ADDED in Phase 4 chunk 1 — the porting rule "lib/core must not import lib/brain
or lib/board; lib/brain may import core only" was a manual grep in the chunk
briefs. This turns it into a test, because the rule is invisible at review time
the moment a file grows a lazy import inside a function.

Imports are read with ``ast`` rather than a regex, so prose in a docstring cannot
false-positive and a function-local import cannot hide.
"""
import ast
import unittest

from tests.brain.base import LIB

# The stdlib modules the ported bottom-layer files are allowed to reach for. A new
# name here should be a deliberate decision, which is the point of the list.
STDLIB_OK = {
    "argparse", "ast", "collections", "contextlib", "datetime", "difflib",
    "functools", "glob", "hashlib", "io", "itertools", "json", "math", "os",
    "pathlib", "re", "shutil", "sqlite3", "string", "subprocess", "sys",
    "tempfile", "textwrap", "time", "typing", "unicodedata", "urllib", "uuid",
}

# Files this chunk added to the bottom layer — held to stdlib-only.
CHUNK1_CORE_FILES = ("core/frontmatter.py", "core/forge/__init__.py",
                     "core/forge/ado.py", "core/forge/github.py")


def top_level_imports(path):
    """Every top-level module name imported anywhere in ``path`` (module scope or
    inside a function). Relative imports (``from . import x``) resolve inside the
    package and are ignored."""
    tree = ast.parse(path.read_text(), filename=str(path))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:          # relative — same package, not a layer crossing
                continue
            if node.module:
                names.add(node.module.split(".")[0])
    return names


class CoreIsTheBottomLayer(unittest.TestCase):
    def test_core_never_imports_brain_or_board(self):
        offenders = []
        for f in sorted((LIB / "core").rglob("*.py")):
            bad = top_level_imports(f) & {"brain", "board"}
            if bad:
                offenders.append(f"{f.relative_to(LIB)} imports {sorted(bad)}")
        self.assertEqual(offenders, [], "core must not import an upper layer:\n"
                                        + "\n".join(offenders))

    def test_chunk1_core_files_are_stdlib_only(self):
        for rel in CHUNK1_CORE_FILES:
            path = LIB / rel
            self.assertTrue(path.exists(), f"{rel} missing")
            extra = top_level_imports(path) - STDLIB_OK
            self.assertEqual(extra, set(), f"{rel} reaches outside the stdlib: {sorted(extra)}")


class BrainImportsCoreOnly(unittest.TestCase):
    def test_brain_config_never_imports_board(self):
        self.assertNotIn("board", top_level_imports(LIB / "brain/config.py"))

    def test_brain_config_reaches_only_core_and_stdlib(self):
        extra = top_level_imports(LIB / "brain/config.py") - STDLIB_OK - {"core"}
        self.assertEqual(extra, set(), f"unexpected imports: {sorted(extra)}")


if __name__ == "__main__":
    unittest.main()
