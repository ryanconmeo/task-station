"""Tests for delegate._workspace_roots() — pure env-parsing, no subprocess calls."""
import os
import sys
import tempfile
import types
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
    ENV_KEY = "TASK_STATION_WORKSPACE_DIRS"

    def setUp(self):
        # Isolate config.json: point the data dir at an empty tmp so
        # config.workspace_dirs() can't read a real ~/.claude/task-station-data/config.json
        # and the env-var fallback path is what's exercised.
        import shutil  # noqa: F401 (used in tearDown)
        self._saved = {k: os.environ.get(k) for k in (self.ENV_KEY, "TASK_STATION_HOME")}
        self._tmphome = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self._tmphome
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
        """TASK_STATION_WORKSPACE_DIRS unset → empty list."""
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


def _args(**kw):
    d = dict(seq=None, label=None, worktree=None, fresh=False, solo=False)
    d.update(kw)
    return types.SimpleNamespace(**d)


class IsMainCheckoutTest(unittest.TestCase):
    def test_repo_root_is_main(self):
        with tempfile.TemporaryDirectory() as repo:
            self.assertTrue(_delegate_mod._is_main_checkout(repo, repo))

    def test_worktree_is_not_main(self):
        with tempfile.TemporaryDirectory() as repo:
            wt = os.path.join(_delegate_mod.worktrees_parent(repo), "2725")
            self.assertFalse(_delegate_mod._is_main_checkout(wt, repo))

    def test_empty_is_not_main(self):
        with tempfile.TemporaryDirectory() as repo:
            self.assertFalse(_delegate_mod._is_main_checkout(None, repo))
            self.assertFalse(_delegate_mod._is_main_checkout("", repo))


class MaybeInheritSeqTest(unittest.TestCase):
    def setUp(self):
        self._saved = _delegate_mod._attached_seq

    def tearDown(self):
        _delegate_mod._attached_seq = self._saved

    def test_inherits_when_no_seq_no_solo(self):
        _delegate_mod._attached_seq = lambda: 324
        a = _args()
        _delegate_mod._maybe_inherit_seq(a)
        self.assertEqual(a.seq, 324)

    def test_inherits_even_without_worktree(self):
        # The fix: inheritance is no longer gated on --worktree, so a no-flag
        # `delegate --project X` from an attached session self-routes to its task.
        _delegate_mod._attached_seq = lambda: 99
        a = _args(worktree=None)
        _delegate_mod._maybe_inherit_seq(a)
        self.assertEqual(a.seq, 99)

    def test_solo_opts_out(self):
        _delegate_mod._attached_seq = lambda: 324
        a = _args(solo=True)
        _delegate_mod._maybe_inherit_seq(a)
        self.assertIsNone(a.seq)

    def test_explicit_seq_kept(self):
        _delegate_mod._attached_seq = lambda: 324
        a = _args(seq=7)
        _delegate_mod._maybe_inherit_seq(a)
        self.assertEqual(a.seq, 7)


class SelectSlotTest(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp()
        self.wt = os.path.join(_delegate_mod.worktrees_parent(self.repo), "2725")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.repo, ignore_errors=True)

    def test_worktree_create_uses_seq_slot(self):
        key, _ = _delegate_mod._select_slot(_args(seq=324, worktree="2725"), "Volt", self.repo, {})
        self.assertEqual(key, "324:Volt")

    def test_readonly_create_uses_main_slot_not_seq_slot(self):
        # A no-worktree run must NOT take the seq slot — else it clobbers the
        # worktree binding (the task-324 bug).
        key, _ = _delegate_mod._select_slot(_args(seq=324), "Volt", self.repo, {})
        self.assertEqual(key, "324:Volt@main")

    def test_no_worktree_resume_prefers_worktree_worker(self):
        reg = {"324:Volt": {"session_id": "abc", "dir": self.wt}}
        key, entry = _delegate_mod._select_slot(_args(seq=324), "Volt", self.repo, reg)
        self.assertEqual(key, "324:Volt")
        self.assertEqual(entry["dir"], self.wt)

    def test_legacy_main_checkout_entry_is_refused(self):
        # The exact bug: the seq slot was left pointing at the main checkout → refuse.
        reg = {"324:Volt": {"session_id": "stale", "dir": self.repo}}
        with self.assertRaises(SystemExit):
            _delegate_mod._select_slot(_args(seq=324), "Volt", self.repo, reg)

    def test_main_worker_coexists_when_no_worktree_worker(self):
        reg = {"324:Volt@main": {"session_id": "ro", "dir": self.repo}}
        key, entry = _delegate_mod._select_slot(_args(seq=324), "Volt", self.repo, reg)
        self.assertEqual(key, "324:Volt@main")
        self.assertEqual(entry["session_id"], "ro")

    def test_worktree_flag_routes_to_seq_slot_even_with_main_entry(self):
        reg = {"324:Volt@main": {"session_id": "ro", "dir": self.repo}}
        key, _ = _delegate_mod._select_slot(_args(seq=324, worktree="2725"), "Volt", self.repo, reg)
        self.assertEqual(key, "324:Volt")

    def test_label_suffixes_both_slots(self):
        k1, _ = _delegate_mod._select_slot(_args(seq=324, worktree="2725", label="x"), "Volt", self.repo, {})
        self.assertEqual(k1, "324:Volt:x")
        k2, _ = _delegate_mod._select_slot(_args(seq=324, label="x"), "Volt", self.repo, {})
        self.assertEqual(k2, "324:Volt@main:x")

    def test_fresh_no_worktree_uses_main_slot(self):
        reg = {"324:Volt": {"session_id": "abc", "dir": self.wt}}
        key, _ = _delegate_mod._select_slot(_args(seq=324, fresh=True), "Volt", self.repo, reg)
        self.assertEqual(key, "324:Volt@main")

    def test_untracked_keeps_original_keying(self):
        self.assertEqual(_delegate_mod._select_slot(_args(worktree="wt"), "Volt", self.repo, {})[0], "Volt@wt")
        self.assertEqual(_delegate_mod._select_slot(_args(), "Volt", self.repo, {})[0], "Volt")


if __name__ == "__main__":
    unittest.main()
