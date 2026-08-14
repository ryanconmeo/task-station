"""A CLEAN worker finish that leaves the worktree uncommitted is its own, loudly
reported terminal state.

The incident this pins: a delegated worker authored three features and 57 tests,
exited cleanly, committed NOTHING, and `delegate status` reported
`○ finished (ok) 8m ago`. The work was found only because a human ran
`git log origin/main..HEAD` by hand — the terminal-state string carried no hint.

Covered here:
  • the `status` terminal-state string reports the count
  • `delegate run`'s relayed result carries a loud banner (both the legacy `-p`
    streaming path and the `--bg` path)
  • the task's `worker finished` feed event carries it too — as a PREFIX, so the
    receiving 160-char snippet trim can never drop it
  • a CLEAN worktree's output stays byte-identical to before the fix
  • NOTHING is auto-committed on a clean exit (a brief may legitimately tell a
    worker not to commit — the bug is the silence, not the missing commit), while
    the abnormal-exit `_wip_commit` auto-checkpoint is untouched
  • a worktree that no longer exists degrades to None and never raises

Stdlib-only, no LLM; real git repos under a temp dir.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "lib", "delegate"
))
import delegate as dg  # noqa: E402

SID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
BG_ID = "11111111-2222-3333-4444-555555555555"

# Fake `claude` for the --bg path: print the backgrounded line and write an `idle`
# agents row so the first poll sees a completed turn → ok. (Mirrors
# tests/test_delegate_bg_cmdrun.py's shim.)
SHIM = r'''#!/usr/bin/env bash
case "$1" in
  --bg)
    echo "backgrounded · 11111111-2222-3333-4444-555555555555 · wk-test"
    echo '[{"sessionId":"11111111-2222-3333-4444-555555555555","status":"idle","pid":4242,"name":"wk-test","cwd":"'"$FAKE_CLAUDE_DIR"'","kind":"background"}]' > "$FAKE_CLAUDE_DIR/agents.json"
    ;;
  agents) cat "$FAKE_CLAUDE_DIR/agents.json" 2>/dev/null || echo "[]";;
  *) exit 64;;
esac
'''


def _git(repo, *args):
    return subprocess.run(["git", "-C", repo] + list(args),
                          capture_output=True, text=True)


def _init_repo():
    repo = tempfile.mkdtemp()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "t@t.local")
    with open(os.path.join(repo, "seed.txt"), "w") as f:
        f.write("seed\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")
    return repo


def _dirty(repo, tracked=1, untracked=1):
    """Leave `tracked` modified files and `untracked` brand-new ones behind."""
    if tracked:
        with open(os.path.join(repo, "seed.txt"), "a") as f:
            f.write("more\n")
    for i in range(untracked):
        with open(os.path.join(repo, "new%d.py" % i), "w") as f:
            f.write("x = %d\n" % i)


def _head_count(repo):
    return int(_git(repo, "rev-list", "--count", "HEAD").stdout.strip())


# ------------------------------------------------------- the shared counter ----
class DirtyCountsTest(unittest.TestCase):
    """_worktree_dirty_counts is THE one implementation of the porcelain tally —
    both the status one-liner and the clean-finish report read it."""

    def setUp(self):
        self.repo = _init_repo()

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def test_clean_tree_is_zero_zero(self):
        self.assertEqual(dg._worktree_dirty_counts(self.repo), (0, 0))

    def test_counts_dirty_and_untracked_separately(self):
        _dirty(self.repo, tracked=1, untracked=2)
        self.assertEqual(dg._worktree_dirty_counts(self.repo), (1, 2))

    def test_staged_file_counts_as_dirty(self):
        with open(os.path.join(self.repo, "added.py"), "w") as f:
            f.write("1\n")
        _git(self.repo, "add", "added.py")
        self.assertEqual(dg._worktree_dirty_counts(self.repo), (1, 0))

    def test_missing_dir_returns_none(self):
        self.assertIsNone(dg._worktree_dirty_counts("/no/such/dir/here"))

    def test_non_git_dir_returns_none(self):
        plain = tempfile.mkdtemp()
        try:
            self.assertIsNone(dg._worktree_dirty_counts(plain))
        finally:
            shutil.rmtree(plain, ignore_errors=True)

    def test_empty_dirpath_returns_none(self):
        self.assertIsNone(dg._worktree_dirty_counts(""))
        self.assertIsNone(dg._worktree_dirty_counts(None))


class UncommittedTotalTest(unittest.TestCase):
    def setUp(self):
        self.repo = _init_repo()

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def test_clean_tree_is_zero(self):
        self.assertEqual(dg._uncommitted_total(self.repo), 0)

    def test_sums_dirty_and_untracked(self):
        _dirty(self.repo, tracked=1, untracked=5)
        self.assertEqual(dg._uncommitted_total(self.repo), 6)

    def test_vanished_worktree_is_none_not_raise(self):
        gone = os.path.join(self.repo, "..", "definitely-gone-%d" % os.getpid())
        self.assertIsNone(dg._uncommitted_total(gone))

    def test_probing_never_commits(self):
        # Report, do not act: the probe must leave the tree exactly as it found it.
        _dirty(self.repo, tracked=1, untracked=1)
        before = _head_count(self.repo)
        dg._uncommitted_total(self.repo)
        self.assertEqual(_head_count(self.repo), before)
        self.assertEqual(dg._worktree_dirty_counts(self.repo), (1, 1))


class WorktreeGitStateUnchangedTest(unittest.TestCase):
    """The extraction of the tally must not change the status one-liner."""

    def setUp(self):
        self.repo = _init_repo()

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def test_clean_worktree(self):
        s = dg._worktree_git_state(self.repo)
        self.assertIn("0 dirty", s)
        self.assertIn("0 untracked", s)
        self.assertIn("seed", s)

    def test_dirty_and_untracked_counts(self):
        _dirty(self.repo, tracked=1, untracked=1)
        s = dg._worktree_git_state(self.repo)
        self.assertIn("1 dirty", s)
        self.assertIn("1 untracked", s)

    def test_missing_dir_returns_none(self):
        self.assertIsNone(dg._worktree_git_state("/no/such/dir/here"))


# --------------------------------------------------------------- rendering ----
class UncommittedPhraseTest(unittest.TestCase):
    def test_positive_count(self):
        self.assertEqual(dg._uncommitted_phrase(6), "6 UNCOMMITTED")

    def test_zero_and_none_are_empty(self):
        self.assertEqual(dg._uncommitted_phrase(0), "")
        self.assertEqual(dg._uncommitted_phrase(None), "")

    def test_garbage_is_empty_never_raises(self):
        self.assertEqual(dg._uncommitted_phrase("lots"), "")
        self.assertEqual(dg._uncommitted_phrase(-3), "")
        self.assertEqual(dg._uncommitted_phrase([]), "")

    def test_string_digits_still_render(self):
        # The count round-trips through the registry JSON; tolerate a legacy string.
        self.assertEqual(dg._uncommitted_phrase("4"), "4 UNCOMMITTED")


class LivenessUncommittedTest(unittest.TestCase):
    def _text(self, **entry):
        now = dg._now()                  # one clock read — the age must be exact
        e = {"pid": None, "ts": now - 5}
        e.update(entry)
        return dg._liveness(e, now=now)

    def test_clean_exit_with_dirty_worktree_reports_count(self):
        glyph, text = self._text(exit="ok", uncommitted=6)
        self.assertEqual(glyph, "○")
        self.assertEqual(text, "finished (ok — 6 UNCOMMITTED) 5s ago")

    def test_clean_exit_clean_worktree_is_byte_identical(self):
        _, text = self._text(exit="ok", uncommitted=0)
        self.assertEqual(text, "finished (ok) 5s ago")

    def test_no_field_at_all_is_byte_identical(self):
        _, text = self._text(exit="ok")
        self.assertEqual(text, "finished (ok) 5s ago")

    def test_abnormal_exit_never_renders_the_tag(self):
        # Only a CLEAN exit reports uncommitted work — an abnormal exit has already
        # been auto-checkpointed by _wip_commit, and a stale count must not leak.
        for label in ("crash", "timeout", "stalled"):
            _, text = self._text(exit=label, uncommitted=9)
            self.assertEqual(text, "finished (%s) 5s ago" % label)

    def test_running_entry_unaffected(self):
        saved = dg._pid_alive
        dg._pid_alive = lambda pid: True
        try:
            now = dg._now()
            glyph, text = dg._liveness(
                {"pid": 4242, "exit": None, "uncommitted": 6, "last_event_ts": now - 3},
                now=now)
            self.assertEqual(glyph, "●")
            self.assertNotIn("UNCOMMITTED", text)
        finally:
            dg._pid_alive = saved


class UncommittedBannerTest(unittest.TestCase):
    def test_empty_when_nothing_uncommitted(self):
        self.assertEqual(dg._uncommitted_banner("/w/t", 0), "")
        self.assertEqual(dg._uncommitted_banner("/w/t", None), "")

    def test_names_count_dir_and_the_no_autocommit_promise(self):
        b = dg._uncommitted_banner("/w/t", 6)
        self.assertIn("6 UNCOMMITTED", b)
        self.assertIn("/w/t", b)
        self.assertIn("git -C /w/t status", b)         # actionable
        self.assertIn("!!", b)                         # loud
        self.assertIn("nothing", b.lower())            # ...committed nothing


# ----------------------------------------------------------- the feed event ----
class FeedEventTest(unittest.TestCase):
    """The `worker finished` event must carry the count where the receiving
    160-char snippet trim cannot drop it — i.e. as a prefix."""

    def setUp(self):
        self._run = dg.subprocess.run
        self.calls = []

        def rec(cmd, *a, **k):
            self.calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        dg.subprocess.run = rec

    def tearDown(self):
        dg.subprocess.run = self._run

    def _text(self):
        cmd = self.calls[0]
        return cmd[cmd.index("--text") + 1]

    def test_prefix_survives_a_long_result(self):
        long_result = "x" * 400
        dg._post_worker_event("9", "projectname", None, SID, True,
                              "%s — %s" % (dg._uncommitted_phrase(6), long_result))
        self.assertIn("6 UNCOMMITTED", self._text())

    def test_clean_finish_text_has_no_tag(self):
        dg._post_worker_event("9", "projectname", None, SID, True, "all done")
        self.assertNotIn("UNCOMMITTED", self._text())


# ------------------------------------- abnormal exit: _wip_commit unchanged ----
class WipCommitStillCheckpointsTest(unittest.TestCase):
    def setUp(self):
        self.repo = _init_repo()

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def test_dirty_tree_still_auto_checkpoints_on_crash(self):
        _dirty(self.repo, tracked=1, untracked=1)
        sha = dg._wip_commit(self.repo, "crash", "implement the thing")
        self.assertIsNotNone(sha)
        self.assertEqual(_head_count(self.repo), 2)
        subject = _git(self.repo, "log", "-1", "--pretty=%s").stdout.strip()
        self.assertIn("auto-checkpoint on crash", subject)

    def test_clean_tree_still_a_noop(self):
        self.assertIsNone(dg._wip_commit(self.repo, "timeout", "task"))
        self.assertEqual(_head_count(self.repo), 1)


# ------------------------------------------------------- cmd_run end-to-end ----
class _CmdRunMixin:
    """Drive cmd_run against a real temp worktree with the python3 write-backs
    recorded. Mirrors tests/test_delegate_bg_cmdrun.py's harness."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-uncommitted-")
        bindir = os.path.join(self.tmp, "bin")
        os.makedirs(bindir)
        shim = os.path.join(bindir, "claude")
        with open(shim, "w") as f:
            f.write(SHIM)
        os.chmod(shim, 0o755)
        self._path = os.environ.get("PATH", "")
        os.environ["PATH"] = bindir + os.pathsep + self._path
        os.environ["FAKE_CLAUDE_DIR"] = self.tmp
        self._hub = os.environ.get("CLAUDE_CODE_SESSION_ID")
        os.environ["CLAUDE_CODE_SESSION_ID"] = "hub-abc"
        self._proj_root = dg.PROJECTS_ROOT
        dg.PROJECTS_ROOT = os.path.join(self.tmp, "projects")
        os.makedirs(dg.PROJECTS_ROOT)
        self._reg = (dg.REG_DIR, dg.REG)
        dg.REG_DIR = self.tmp
        dg.REG = os.path.join(self.tmp, "workers.json")
        self.repo = _init_repo()
        self.wt = _init_repo()
        self._saved = {
            "resolve": dg._resolve_dir_from_args,
            "wt": dg.resolve_worktree,
            "attached": dg._attached_seq,
            "notify": dg.notify_event,
            "sleep": dg.time.sleep,
            "run": dg.subprocess.run,
            "worker": dg.run_worker,
        }
        dg._resolve_dir_from_args = lambda a: self.repo
        dg.resolve_worktree = lambda *a, **k: self.wt
        dg._attached_seq = lambda: None
        dg.notify_event = lambda *a, **k: None
        dg.time.sleep = lambda *a, **k: None
        # The legacy `-p` streaming path: a clean run that reports a result event.
        dg.run_worker = lambda *a, **k: (
            0, json.dumps({"result": "authored three features and 57 tests",
                           "session_id": SID, "total_cost_usd": 0.02}), "", False)
        self.calls = []
        real_run = self._saved["run"]

        def rec(cmd, *a, **k):
            if isinstance(cmd, list) and cmd[:1] == ["python3"]:
                self.calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            return real_run(cmd, *a, **k)      # git + the claude shim run for real

        dg.subprocess.run = rec

    def tearDown(self):
        os.environ["PATH"] = self._path
        os.environ.pop("FAKE_CLAUDE_DIR", None)
        if self._hub is None:
            os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        else:
            os.environ["CLAUDE_CODE_SESSION_ID"] = self._hub
        dg.REG_DIR, dg.REG = self._reg
        dg._resolve_dir_from_args = self._saved["resolve"]
        dg.resolve_worktree = self._saved["wt"]
        dg._attached_seq = self._saved["attached"]
        dg.notify_event = self._saved["notify"]
        dg.time.sleep = self._saved["sleep"]
        dg.subprocess.run = self._saved["run"]
        dg.run_worker = self._saved["worker"]
        dg.PROJECTS_ROOT = self._proj_root
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.repo, ignore_errors=True)
        shutil.rmtree(self.wt, ignore_errors=True)

    def _args(self, **kw):
        d = dict(seq="9", label=None, worktree="9-fix", branch=None, base=None,
                 fresh=False, solo=False, task="do it", timeout=None,
                 model="sonnet", repo=self.repo, project=None, harness=self.HARNESS)
        d.update(kw)
        return types.SimpleNamespace(**d)

    def _run(self, **kw):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stderr(err), redirect_stdout(out):
            dg.cmd_run(self._args(**kw))
        return out.getvalue(), err.getvalue()

    def _entry(self):
        return next(iter(dg.load_reg().values()))

    def _event_texts(self):
        return [c[c.index("--text") + 1] for c in self.calls
                if "add-event" in c and "--text" in c]


class LegacyPathUncommittedTest(_CmdRunMixin, unittest.TestCase):
    HARNESS = "codex"          # supports_bg False → the legacy `-p` streaming path

    def test_dirty_finish_records_the_count_on_the_entry(self):
        _dirty(self.wt, tracked=1, untracked=5)
        self._run()
        entry = self._entry()
        self.assertEqual(entry["exit"], "ok")
        self.assertEqual(entry["uncommitted"], 6)
        self.assertEqual(dg._liveness(entry)[1].split(")")[0],
                         "finished (ok — 6 UNCOMMITTED")

    def test_dirty_finish_relays_a_loud_banner(self):
        _dirty(self.wt, tracked=1, untracked=1)
        out, err = self._run()
        self.assertIn("2 UNCOMMITTED", out)
        self.assertIn("!!", out)
        self.assertIn(self.wt, out)
        self.assertIn("2 UNCOMMITTED", err)        # …and in the footer too

    def test_dirty_finish_tags_the_feed_event(self):
        _dirty(self.wt, tracked=1, untracked=1)
        self._run()
        texts = self._event_texts()
        self.assertTrue(texts)
        self.assertTrue(any("2 UNCOMMITTED" in t for t in texts))

    def test_dirty_finish_never_auto_commits(self):
        _dirty(self.wt, tracked=1, untracked=1)
        before = _head_count(self.wt)
        self._run()
        self.assertEqual(_head_count(self.wt), before)
        # (1, 2): the seeded dirty+untracked pair, plus the HANDOFF-REPORT-*.md
        # the delegate harvests into the worktree root (B3) — an intentional
        # artifact, not an auto-commit. Measured BEFORE the harvest, the count
        # the entry reports stays the worker's own dirt.
        self.assertEqual(dg._worktree_dirty_counts(self.wt), (1, 2))

    def test_clean_finish_is_unchanged(self):
        out, err = self._run()
        self.assertNotIn("UNCOMMITTED", out)
        self.assertNotIn("UNCOMMITTED", err)
        self.assertNotIn("!!", out)
        self.assertNotIn("uncommitted", self._entry())
        self.assertEqual(dg._liveness(self._entry())[1].split(")")[0], "finished (ok")
        self.assertFalse(any("UNCOMMITTED" in t for t in self._event_texts()))

    def test_main_checkout_run_never_reports_the_users_own_dirt(self):
        # A read-only run in the main checkout (no --worktree → dirpath is the repo
        # root): the dirt is the USER's own work, not a worker's missing commit —
        # the same guard _wip_commit uses on the abnormal path.
        _dirty(self.repo, tracked=1, untracked=1)
        out, _ = self._run(worktree=None)
        self.assertNotIn("UNCOMMITTED", out)
        self.assertNotIn("uncommitted", self._entry())

    def test_vanished_worktree_finishes_without_raising(self):
        gone = os.path.join(self.tmp, "vanished")
        dg.resolve_worktree = lambda *a, **k: gone
        out, _ = self._run()
        self.assertNotIn("UNCOMMITTED", out)
        self.assertNotIn("uncommitted", self._entry())


class BgPathUncommittedTest(_CmdRunMixin, unittest.TestCase):
    HARNESS = "claude"         # supports_bg True → the `--bg` lifecycle

    def test_dirty_finish_records_the_count_on_the_entry(self):
        _dirty(self.wt, tracked=1, untracked=2)
        self._run()
        entry = self._entry()
        self.assertEqual(entry["session_id"], BG_ID)
        self.assertEqual(entry["exit"], "ok")
        self.assertEqual(entry["uncommitted"], 3)
        self.assertIn("3 UNCOMMITTED", dg._liveness(entry)[1])

    def test_dirty_finish_relays_a_loud_banner_on_stderr(self):
        _dirty(self.wt, tracked=1, untracked=2)
        _, err = self._run()
        self.assertIn("3 UNCOMMITTED", err)
        self.assertIn("!!", err)

    def test_dirty_finish_tags_the_feed_event(self):
        _dirty(self.wt, tracked=1, untracked=2)
        self._run()
        self.assertTrue(any("3 UNCOMMITTED" in t for t in self._event_texts()))

    def test_dirty_finish_never_auto_commits(self):
        _dirty(self.wt, tracked=1, untracked=2)
        before = _head_count(self.wt)
        self._run()
        self.assertEqual(_head_count(self.wt), before)

    def test_clean_finish_is_unchanged(self):
        _, err = self._run()
        self.assertNotIn("UNCOMMITTED", err)
        self.assertNotIn("uncommitted", self._entry())
        self.assertFalse(any("UNCOMMITTED" in t for t in self._event_texts()))


if __name__ == "__main__":
    unittest.main()
