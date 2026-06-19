import ast
import os
import sys
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "lib"))
sys.path.insert(0, os.path.join(_REPO, "tools"))

import stack_map


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class StackMapModule(unittest.TestCase):
    def test_importable_and_nonempty(self):
        self.assertTrue(stack_map.EXT_TO_STACK, "EXT_TO_STACK is empty")
        self.assertTrue(stack_map.FILENAME_TO_STACK, "FILENAME_TO_STACK is empty")
        # comprehensive: the Linguist-derived map should dwarf the old ~18-entry list
        self.assertGreater(len(stack_map.EXT_TO_STACK), 200)

    def test_stdlib_only_no_imports(self):
        """The generated module must be pure data: no import statements at all,
        so it can never pull in a third-party dependency at runtime."""
        src = _read(stack_map.__file__)
        tree = ast.parse(src)
        imports = [n for n in ast.walk(tree)
                   if isinstance(n, (ast.Import, ast.ImportFrom))]
        self.assertEqual(imports, [], "stack_map.py must contain no imports")

    def test_only_dict_literals_at_module_level(self):
        """Top level is just the two dict assignments (+ docstring) — no code."""
        src = _read(stack_map.__file__)
        body = [n for n in ast.parse(src).body if not isinstance(n, ast.Expr)]
        self.assertTrue(all(isinstance(n, ast.Assign) for n in body))
        targets = {t.id for n in body for t in n.targets}
        self.assertEqual(targets, {"EXT_TO_STACK", "FILENAME_TO_STACK"})


class KnownMappings(unittest.TestCase):
    def test_extensions(self):
        cases = {
            ".swift": "swift",      # the headline new coverage
            ".py": "python",
            ".cs": "dotnet",
            ".sql": "sql",
            ".ts": "typescript",
            ".tsx": "typescript",   # TSX variant collapses onto typescript
            ".js": "node",
            ".go": "go",
            ".rs": "rust",
            ".tf": "terraform",
        }
        for ext, want in cases.items():
            self.assertEqual(stack_map.EXT_TO_STACK.get(ext), want, "%s mapping" % ext)

    def test_long_tail_slugged(self):
        # Auto-covered long tail falls through to slugified Linguist names.
        for ext, want in {".kt": "kotlin", ".rb": "ruby", ".php": "php",
                          ".scala": "scala"}.items():
            self.assertEqual(stack_map.EXT_TO_STACK.get(ext), want, "%s mapping" % ext)

    def test_filenames(self):
        self.assertEqual(stack_map.FILENAME_TO_STACK.get("Dockerfile"), "docker")
        self.assertEqual(stack_map.FILENAME_TO_STACK.get("Makefile"), "make")

    def test_swift_present(self):
        self.assertIn(".swift", stack_map.EXT_TO_STACK)
        self.assertEqual(stack_map.EXT_TO_STACK[".swift"], "swift")


class AmbiguousExtensions(unittest.TestCase):
    """Prose/markup/data-ambiguous extensions are excluded from the programming
    map; curated programming languages still win incidental data claims."""

    def test_md_excluded(self):
        # `.md` is owned by Markdown (prose); the obscure GCC Machine Description
        # claim must NOT leak through as a stack.
        self.assertNotIn(".md", stack_map.EXT_TO_STACK)

    def test_no_prose_noise_values(self):
        values = set(stack_map.EXT_TO_STACK.values())
        self.assertNotIn("gcc-machine-description", values)
        self.assertNotIn("markdown", values)

    def test_other_doc_data_extensions_excluded(self):
        for ext in (".rst", ".txt", ".json", ".yaml", ".xml", ".csv"):
            self.assertNotIn(ext, stack_map.EXT_TO_STACK, "%s should be excluded" % ext)

    def test_curated_survives_incidental_data_claim(self):
        # XML lists .ts/.tsx/.rs incidentally, but the curated programming owner wins.
        self.assertEqual(stack_map.EXT_TO_STACK.get(".ts"), "typescript")
        self.assertEqual(stack_map.EXT_TO_STACK.get(".tsx"), "typescript")
        self.assertEqual(stack_map.EXT_TO_STACK.get(".rs"), "rust")

    def test_programming_collision_tiebreak(self):
        # Programming-only collisions resolve to the popular mainstream owner.
        self.assertEqual(stack_map.EXT_TO_STACK.get(".h"), "c")
        self.assertEqual(stack_map.EXT_TO_STACK.get(".m"), "objective-c")

    def test_niche_detection_kept(self):
        # Correct niche detections (no doc/data claimant) must survive.
        self.assertEqual(stack_map.EXT_TO_STACK.get(".com"), "digital-command-language")


# languages.yml is vendored but gitignored (not committed), so the generator
# tests only run where it's present — they stay green here and skip on a fresh
# clone rather than failing.
_YML = os.path.join(_REPO, "languages.yml")


@unittest.skipUnless(os.path.isfile(_YML), "languages.yml not vendored")
class GeneratorDeterminism(unittest.TestCase):
    def test_running_twice_is_byte_identical(self):
        import gen_stack_map
        yml = _read(_YML)
        self.assertEqual(gen_stack_map.generate(yml), gen_stack_map.generate(yml))

    def test_committed_module_matches_generator(self):
        """The committed lib/stack_map.py is exactly what the generator emits for
        the vendored languages.yml (so it never drifts silently)."""
        import gen_stack_map
        yml = _read(_YML)
        committed = _read(stack_map.__file__)
        self.assertEqual(gen_stack_map.generate(yml), committed)


if __name__ == "__main__":
    unittest.main()
