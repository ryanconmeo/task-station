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
        # Curated top-~40 stacks contribute dozens of extensions.
        self.assertGreater(len(stack_map.EXT_TO_STACK), 40)

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
            ".swift": "swift",
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

    def test_more_extensions(self):
        for ext, want in {".kt": "kotlin", ".rb": "ruby", ".php": "php",
                          ".scala": "scala", ".java": "java"}.items():
            self.assertEqual(stack_map.EXT_TO_STACK.get(ext), want, "%s mapping" % ext)

    def test_filenames(self):
        self.assertEqual(stack_map.FILENAME_TO_STACK.get("Dockerfile"), "docker")
        self.assertEqual(stack_map.FILENAME_TO_STACK.get("Makefile"), "make")

    def test_swift_present(self):
        self.assertIn(".swift", stack_map.EXT_TO_STACK)
        self.assertEqual(stack_map.EXT_TO_STACK[".swift"], "swift")


class ExcludedAndGraceful(unittest.TestCase):
    """Prose/data/markup formats are absent, and unknown extensions map to nothing
    so repo_index degrades gracefully."""

    def test_md_excluded(self):
        self.assertNotIn(".md", stack_map.EXT_TO_STACK)

    def test_no_prose_noise_values(self):
        values = set(stack_map.EXT_TO_STACK.values())
        self.assertNotIn("markdown", values)

    def test_doc_data_extensions_excluded(self):
        for ext in (".md", ".rst", ".txt", ".json", ".yaml", ".xml", ".csv"):
            self.assertNotIn(ext, stack_map.EXT_TO_STACK, "%s should be excluded" % ext)

    def test_extension_collision_curated_owner_wins(self):
        # C owns .h (listed before cpp); Objective-C owns .m.
        self.assertEqual(stack_map.EXT_TO_STACK.get(".h"), "c")
        self.assertEqual(stack_map.EXT_TO_STACK.get(".m"), "objective-c")

    def test_unknown_extension_is_none(self):
        # An extension not in the curated set has no stack — repo_index skips it.
        self.assertIsNone(stack_map.EXT_TO_STACK.get(".wat"))
        self.assertIsNone(stack_map.EXT_TO_STACK.get(".com"))


class GeneratorDeterminism(unittest.TestCase):
    """The generator is self-contained (no external input), so these always run."""

    def test_running_twice_is_byte_identical(self):
        import gen_stack_map
        self.assertEqual(gen_stack_map.generate(), gen_stack_map.generate())

    def test_committed_module_matches_generator(self):
        """The committed lib/stack_map.py is exactly what the generator emits, so
        it never drifts silently."""
        import gen_stack_map
        self.assertEqual(gen_stack_map.generate(), _read(stack_map.__file__))


if __name__ == "__main__":
    unittest.main()
