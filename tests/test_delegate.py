"""Tests for delegate._workspace_roots() — pure env-parsing, no subprocess calls."""
import os
import sys
import tempfile
import unittest

# delegate.py lives in lib/delegate/, two levels below the repo root.
# Add lib/delegate to sys.path so it can be imported directly; it inserts
# its own parent (lib/) for the `import paths` it needs at module load.
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "lib", "delegate"
))
import delegate as _delegate_mod


class WorkspaceRootsTest(unittest.TestCase):
    ENV_KEY = "CLAUDE_TODO_WORKSPACE_DIRS"

    def setUp(self):
        # Isolate config.json: point the data dir at an empty tmp so
        # config.workspace_dirs() can't read a real ~/.claude/todo-data/config.json
        # and the env-var fallback path is what's exercised.
        import shutil  # noqa: F401 (used in tearDown)
        self._saved = {k: os.environ.get(k) for k in (self.ENV_KEY, "CLAUDE_TODO_HOME")}
        self._tmphome = tempfile.mkdtemp()
        os.environ["CLAUDE_TODO_HOME"] = self._tmphome
        os.environ.pop(self.ENV_KEY, None)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmphome, ignore_errors=True)
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_unset_returns_empty_list(self):
        """CLAUDE_TODO_WORKSPACE_DIRS unset → empty list."""
        self.assertNotIn(self.ENV_KEY, os.environ)
        result = _delegate_mod._workspace_roots()
        self.assertEqual(result, [])

    def test_two_real_dirs_both_returned(self):
        """A pathsep-joined pair of existing dirs → both expanded paths returned."""
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            os.environ[self.ENV_KEY] = os.pathsep.join([d1, d2])
            result = _delegate_mod._workspace_roots()
            self.assertEqual(result, [d1, d2])

    def test_nonexistent_dir_is_dropped(self):
        """A nonexistent dir in the list is silently dropped; real dirs are kept."""
        with tempfile.TemporaryDirectory() as real_dir:
            nonexistent = os.path.join(real_dir, "does-not-exist")
            os.environ[self.ENV_KEY] = os.pathsep.join([nonexistent, real_dir])
            result = _delegate_mod._workspace_roots()
            self.assertNotIn(nonexistent, result)
            self.assertIn(real_dir, result)
            self.assertEqual(len(result), 1)


if __name__ == "__main__":
    unittest.main()
