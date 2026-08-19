"""Tests for delegate._workspace_roots() — pure env-parsing, no subprocess calls."""
import json
import os
import sys
import tempfile
import threading
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
import harness


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
        key, _ = _delegate_mod._select_slot(_args(seq=324, worktree="2725"), "Projectname", self.repo, {})
        self.assertEqual(key, "324:Projectname")

    def test_readonly_create_uses_main_slot_not_seq_slot(self):
        # A no-worktree run must NOT take the seq slot — else it clobbers the
        # worktree binding (the task-324 bug).
        key, _ = _delegate_mod._select_slot(_args(seq=324), "Projectname", self.repo, {})
        self.assertEqual(key, "324:Projectname@main")

    def test_no_worktree_resume_prefers_worktree_worker(self):
        reg = {"324:Projectname": {"session_id": "abc", "dir": self.wt}}
        key, entry = _delegate_mod._select_slot(_args(seq=324), "Projectname", self.repo, reg)
        self.assertEqual(key, "324:Projectname")
        self.assertEqual(entry["dir"], self.wt)

    def test_legacy_main_checkout_entry_is_refused(self):
        # The exact bug: the seq slot was left pointing at the main checkout → refuse.
        reg = {"324:Projectname": {"session_id": "stale", "dir": self.repo}}
        with self.assertRaises(SystemExit):
            _delegate_mod._select_slot(_args(seq=324), "Projectname", self.repo, reg)

    def test_main_worker_coexists_when_no_worktree_worker(self):
        reg = {"324:Projectname@main": {"session_id": "ro", "dir": self.repo}}
        key, entry = _delegate_mod._select_slot(_args(seq=324), "Projectname", self.repo, reg)
        self.assertEqual(key, "324:Projectname@main")
        self.assertEqual(entry["session_id"], "ro")

    def test_worktree_flag_routes_to_seq_slot_even_with_main_entry(self):
        reg = {"324:Projectname@main": {"session_id": "ro", "dir": self.repo}}
        key, _ = _delegate_mod._select_slot(_args(seq=324, worktree="2725"), "Projectname", self.repo, reg)
        self.assertEqual(key, "324:Projectname")

    def test_label_suffixes_both_slots(self):
        k1, _ = _delegate_mod._select_slot(_args(seq=324, worktree="2725", label="x"), "Projectname", self.repo, {})
        self.assertEqual(k1, "324:Projectname:x")
        k2, _ = _delegate_mod._select_slot(_args(seq=324, label="x"), "Projectname", self.repo, {})
        self.assertEqual(k2, "324:Projectname@main:x")

    def test_fresh_no_worktree_uses_main_slot(self):
        reg = {"324:Projectname": {"session_id": "abc", "dir": self.wt}}
        key, _ = _delegate_mod._select_slot(_args(seq=324, fresh=True), "Projectname", self.repo, reg)
        self.assertEqual(key, "324:Projectname@main")

    def test_untracked_keeps_original_keying(self):
        self.assertEqual(_delegate_mod._select_slot(_args(worktree="wt"), "Projectname", self.repo, {})[0], "Projectname@wt")
        self.assertEqual(_delegate_mod._select_slot(_args(), "Projectname", self.repo, {})[0], "Projectname")


class BuildWorkerCmdTest(unittest.TestCase):
    """_build_worker_cmd is the pure base of the worker command — model goes here.

    Model and permission mode now come back from `board.workspace.resolve_spawn`, the
    same resolver `invoke` asks, so the pair this file used to pin by hand
    (`acceptEdits` + a bare alias) is gone. The rule itself is tested in
    tests/test_spawn_resolver.py; what is pinned HERE is the argv delegate assembles
    from the resolver's answer.

    ANTHROPIC_MODEL is pinned because the resolver inherits the parent's `[1m]` window,
    and the parent selection falls through to the developer's real
    ~/.claude/settings.json — so without this the expected argv would differ between a
    machine set to `opus[1m]` and one set to plain `opus`."""

    def setUp(self):
        self._saved_model = os.environ.get("ANTHROPIC_MODEL")
        os.environ["ANTHROPIC_MODEL"] = "sonnet"

    def tearDown(self):
        if self._saved_model is None:
            os.environ.pop("ANTHROPIC_MODEL", None)
        else:
            os.environ["ANTHROPIC_MODEL"] = self._saved_model

    def test_defaults_to_sonnet_streaming(self):
        # Streaming mode: stream-json + --verbose (CLI requires --verbose for
        # stream-json print mode) replace the old plain --output-format json.
        # dontAsk replaces acceptEdits — an unattended worker must fail closed rather
        # than park on the first non-edit prompt — and so the author-only toolset is
        # granted by name, exactly as harness.ClaudeAdapter.spawn_cmd does under --bg.
        cmd = _delegate_mod._build_worker_cmd("do the thing")
        self.assertEqual(cmd, ["claude", "-p", "do the thing",
                               "--output-format", "stream-json", "--verbose",
                               "--permission-mode", "dontAsk",
                               "--allowedTools",
                               *harness.ClaudeAdapter.DONTASK_ALLOW,
                               "--model", "sonnet"])

    def test_explicit_opus_overrides(self):
        cmd = _delegate_mod._build_worker_cmd("hard work", model="opus")
        self.assertEqual(cmd[-2:], ["--model", "opus"])
        self.assertIn("opus", cmd)

    def test_an_explicit_model_reclaims_the_parents_window(self):
        # A same-family parent lends its `[1m]` marker: handing a worker one fifth of
        # the parent's context is the downgrade nobody asked for.
        os.environ["ANTHROPIC_MODEL"] = "claude-opus-5[1m]"
        cmd = _delegate_mod._build_worker_cmd("hard work", model="opus")
        self.assertEqual(cmd[-2:], ["--model", "claude-opus-5[1m]"])

    def test_empty_model_omits_flag(self):
        # Falsy model → no --model, so the worker inherits the account default.
        cmd = _delegate_mod._build_worker_cmd("t", model="")
        self.assertNotIn("--model", cmd)
        cmd = _delegate_mod._build_worker_cmd("t", model=None)
        self.assertNotIn("--model", cmd)


class ClassifyExitTest(unittest.TestCase):
    """_classify_exit implements the B4 rule: abnormal = timed_out OR rc!=0 OR
    result_event is None. Returns (exit_label, is_abnormal)."""

    def test_clean_finish(self):
        self.assertEqual(_delegate_mod._classify_exit(0, '{"type":"result"}', False),
                         ("ok", False))

    def test_timeout_is_abnormal(self):
        label, abn = _delegate_mod._classify_exit(0, '{"type":"result"}', True)
        self.assertEqual(label, "timeout")
        self.assertTrue(abn)

    def test_nonzero_rc_is_crash(self):
        label, abn = _delegate_mod._classify_exit(1, '{"type":"result"}', False)
        self.assertEqual(label, "crash")
        self.assertTrue(abn)

    def test_missing_result_event_is_crash(self):
        # The pre-existing `not out` bug: a worker that streamed events then died
        # (no terminal result) must be abnormal, NOT a green finish.
        label, abn = _delegate_mod._classify_exit(0, None, False)
        self.assertEqual(label, "crash")
        self.assertTrue(abn)

    def test_nonzero_with_result_still_crash(self):
        # rc!=0 WITH output (S5) is abnormal, not a green finish.
        label, abn = _delegate_mod._classify_exit(2, '{"type":"result"}', False)
        self.assertEqual(label, "crash")
        self.assertTrue(abn)

    def test_timeout_takes_precedence_over_crash_label(self):
        label, abn = _delegate_mod._classify_exit(-9, None, True)
        self.assertEqual(label, "timeout")
        self.assertTrue(abn)


class RunSubparserModelTest(unittest.TestCase):
    """The `run` subparser exposes --model, defaulting to sonnet, overridable."""

    def _parse_run(self, *extra):
        captured = {}
        saved_run, saved_argv = _delegate_mod.cmd_run, sys.argv
        _delegate_mod.cmd_run = lambda a: captured.update(a=a)
        try:
            sys.argv = ["delegate", "run", "--repo", "/tmp/x",
                        "--task", "t", *extra]
            _delegate_mod.main()
        finally:
            _delegate_mod.cmd_run, sys.argv = saved_run, saved_argv
        return captured["a"]

    def test_default_is_sonnet(self):
        self.assertEqual(self._parse_run().model, "sonnet")

    def test_override_to_opus(self):
        self.assertEqual(self._parse_run("--model", "opus").model, "opus")


# --------------------------------------------------------------- streaming ----


class IterStreamEventsTest(unittest.TestCase):
    """_iter_stream_events parses NDJSON lines into dict events, tolerating junk."""

    def test_parses_valid_ndjson(self):
        lines = ['{"type":"system"}', '{"type":"assistant"}', '{"type":"result"}']
        evs = list(_delegate_mod._iter_stream_events(lines))
        self.assertEqual([e["type"] for e in evs], ["system", "assistant", "result"])

    def test_skips_blank_and_nonjson_lines(self):
        lines = ['', '   ', 'not json at all', '{"type":"assistant"}',
                 '{partial', '{"type":"result"}']
        evs = list(_delegate_mod._iter_stream_events(lines))
        self.assertEqual([e["type"] for e in evs], ["assistant", "result"])

    def test_skips_json_that_is_not_an_object(self):
        lines = ['[1,2,3]', '"a string"', '42', '{"type":"assistant"}']
        evs = list(_delegate_mod._iter_stream_events(lines))
        self.assertEqual([e["type"] for e in evs], ["assistant"])

    def test_strips_trailing_newline(self):
        evs = list(_delegate_mod._iter_stream_events(['{"type":"assistant"}\n']))
        self.assertEqual(evs[0]["type"], "assistant")


class ProgressLineTest(unittest.TestCase):
    """_progress_line maps ONE stream event → one compact line or None (whitelist)."""

    def _assistant(self, *blocks):
        return {"type": "assistant", "message": {"content": list(blocks)}}

    def test_tool_use_block_renders_arrow_and_name(self):
        ev = self._assistant({"type": "tool_use", "name": "Edit",
                              "input": {"file_path": "/a/b.py"}})
        line = _delegate_mod._progress_line(ev)
        self.assertTrue(line.startswith("→ Edit:"))
        self.assertIn("/a/b.py", line)

    def test_text_block_renders_dot_and_text(self):
        ev = self._assistant({"type": "text", "text": "Working on the fix now."})
        line = _delegate_mod._progress_line(ev)
        self.assertTrue(line.startswith("· "))
        self.assertIn("Working on the fix", line)

    def test_tool_use_preferred_over_text_in_same_message(self):
        ev = self._assistant({"type": "text", "text": "Let me edit."},
                             {"type": "tool_use", "name": "Bash",
                              "input": {"command": "ls"}})
        line = _delegate_mod._progress_line(ev)
        self.assertTrue(line.startswith("→ Bash:"))

    def test_thinking_block_alone_is_none(self):
        ev = self._assistant({"type": "thinking", "thinking": "hmm"})
        self.assertIsNone(_delegate_mod._progress_line(ev))

    def test_text_is_truncated_and_flattened(self):
        ev = self._assistant({"type": "text", "text": "line one\nline two\n" + "x" * 200})
        line = _delegate_mod._progress_line(ev)
        self.assertNotIn("\n", line.rstrip("\n") if False else line)
        self.assertLess(len(line), 120)

    def test_user_tool_result_error_renders_cross(self):
        ev = {"type": "user", "message": {"content": [
            {"type": "tool_result", "is_error": True, "content": "boom"}]}}
        line = _delegate_mod._progress_line(ev)
        self.assertIsNotNone(line)
        self.assertIn("✗", line)

    def test_user_tool_result_success_is_none(self):
        ev = {"type": "user", "message": {"content": [
            {"type": "tool_result", "content": "ok"}]}}
        self.assertIsNone(_delegate_mod._progress_line(ev))

    def test_system_init_is_none_noise(self):
        # Assumptions A: every system event is skippable noise (no message.content).
        self.assertIsNone(_delegate_mod._progress_line({"type": "system", "subtype": "init"}))
        self.assertIsNone(_delegate_mod._progress_line({"type": "system", "subtype": "hook_started"}))

    def test_result_event_is_none(self):
        # Terminal result is captured, never rendered as a progress line.
        self.assertIsNone(_delegate_mod._progress_line({"type": "result", "result": "done"}))

    def test_rate_limit_and_unknown_are_none(self):
        self.assertIsNone(_delegate_mod._progress_line({"type": "rate_limit_event"}))
        self.assertIsNone(_delegate_mod._progress_line({"type": "whatever"}))

    def test_malformed_assistant_no_content_is_none(self):
        self.assertIsNone(_delegate_mod._progress_line({"type": "assistant"}))
        self.assertIsNone(_delegate_mod._progress_line({"type": "assistant", "message": {}}))


class SummarizeToolInputTest(unittest.TestCase):
    def test_prefers_salient_key(self):
        s = _delegate_mod._summarize_tool_input({"file_path": "/x/y.py", "content": "z" * 999})
        self.assertIn("/x/y.py", s)
        self.assertNotIn("z" * 50, s)

    def test_flattens_newlines_and_truncates(self):
        s = _delegate_mod._summarize_tool_input({"command": "a\nb\nc" + "d" * 200})
        self.assertNotIn("\n", s)
        self.assertLessEqual(len(s), 80)

    def test_non_dict_input(self):
        s = _delegate_mod._summarize_tool_input("just a string")
        self.assertIn("just a string", s)


# ----------------------------------------------------- registry / heartbeat ----


class _RegTmpMixin:
    """Redirect delegate.REG / REG_DIR at a fresh temp file (REG is captured at
    import from paths.data_dir(), so env isolation alone isn't enough)."""

    def setUp(self):
        self._reg_dir = tempfile.mkdtemp()
        self._saved = (_delegate_mod.REG, _delegate_mod.REG_DIR)
        _delegate_mod.REG_DIR = self._reg_dir
        _delegate_mod.REG = os.path.join(self._reg_dir, "workers.json")

    def tearDown(self):
        import shutil
        _delegate_mod.REG, _delegate_mod.REG_DIR = self._saved
        shutil.rmtree(self._reg_dir, ignore_errors=True)


class SaveRegTmpTest(_RegTmpMixin, unittest.TestCase):
    def test_tmp_has_per_process_suffix(self):
        # Two concurrent workers must not clobber a single shared .tmp (B3).
        captured = {}
        real_replace = os.replace

        def fake_replace(src, dst):
            captured["src"] = src
            return real_replace(src, dst)

        _delegate_mod.os.replace = fake_replace
        try:
            _delegate_mod.save_reg({"x": {"session_id": "s"}})
        finally:
            _delegate_mod.os.replace = real_replace
        self.assertIn(str(os.getpid()), captured["src"])
        self.assertTrue(captured["src"].endswith(".tmp"))
        self.assertEqual(_delegate_mod.load_reg(), {"x": {"session_id": "s"}})


class TouchHeartbeatTest(_RegTmpMixin, unittest.TestCase):
    def test_merges_single_key(self):
        _delegate_mod.save_reg({"A": {"session_id": "sa", "project": "P"}})
        _delegate_mod._touch_heartbeat("A", last_event_ts=123, phase="Edit")
        e = _delegate_mod.load_reg()["A"]
        self.assertEqual(e["last_event_ts"], 123)
        self.assertEqual(e["phase"], "Edit")
        self.assertEqual(e["session_id"], "sa")   # untouched fields preserved
        self.assertEqual(e["project"], "P")

    def test_missing_key_is_noop(self):
        _delegate_mod.save_reg({"A": {"session_id": "sa"}})
        _delegate_mod._touch_heartbeat("GHOST", phase="x")
        self.assertEqual(_delegate_mod.load_reg(), {"A": {"session_id": "sa"}})

    def test_heartbeat_does_not_erase_concurrent_preregister(self):
        # B3 regression: worker A holds a STALE snapshot (only A) while worker B
        # pre-registers to disk. A's reload-merge heartbeat must NOT erase B.
        _delegate_mod.save_reg({"A": {"session_id": "sa"}})
        # Worker A loads its stale snapshot (simulated: it never re-reads).
        _stale = _delegate_mod.load_reg()          # {"A": ...}
        # Worker B pre-registers B directly to disk.
        d = _delegate_mod.load_reg()
        d["B"] = {"session_id": "sb"}
        _delegate_mod.save_reg(d)
        # A now heartbeats — reload-merge means it re-reads disk (sees A+B).
        _delegate_mod._touch_heartbeat("A", last_event_ts=999)
        final = _delegate_mod.load_reg()
        self.assertIn("B", final)
        self.assertEqual(final["B"]["session_id"], "sb")   # NOT erased
        self.assertEqual(final["A"]["last_event_ts"], 999)
        self.assertEqual(_stale, {"A": {"session_id": "sa"}})  # snapshot unchanged

    def test_terminal_write_sets_pid_null_and_exit(self):
        _delegate_mod.save_reg({"A": {"session_id": "sa", "pid": 4242, "exit": None}})
        _delegate_mod._touch_heartbeat("A", pid=None, exit="ok", phase="done")
        e = _delegate_mod.load_reg()["A"]
        self.assertIsNone(e["pid"])
        self.assertEqual(e["exit"], "ok")


class SaveEntryStreamFieldsTest(_RegTmpMixin, unittest.TestCase):
    def test_preregister_records_pid_started_exit(self):
        reg = {}
        _delegate_mod._save_entry(reg, "324:P", "P", 324, None, "/wt", "sid",
                                  model="sonnet", pid=4242, started_ts=100, exit=None)
        e = _delegate_mod.load_reg()["324:P"]
        self.assertEqual(e["pid"], 4242)
        self.assertEqual(e["started_ts"], 100)
        self.assertIn("exit", e)
        self.assertIsNone(e["exit"])
        self.assertEqual(e["session_id"], "sid")

    def test_refresh_carries_forward_stream_fields(self):
        # Pre-register with streaming state...
        _delegate_mod._save_entry({}, "k", "P", 1, None, "/wt", "sid",
                                  pid=42, started_ts=100, last_event_ts=150,
                                  phase="Edit", exit=None)
        # ...post-run refresh (no stream extras passed) must NOT drop them.
        _delegate_mod._save_entry({}, "k", "P", 1, None, "/wt", "sid", model="opus")
        e = _delegate_mod.load_reg()["k"]
        self.assertEqual(e["started_ts"], 100)
        self.assertEqual(e["last_event_ts"], 150)
        self.assertEqual(e["phase"], "Edit")
        self.assertEqual(e["model"], "opus")


# ------------------------------------------------------------- wip commit ----


def _git(repo, *args, check=True):
    import subprocess as _sp
    r = _sp.run(["git", "-C", repo, *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise AssertionError("git %s failed: %s" % (args, r.stderr))
    return r


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


class WipCommitTest(unittest.TestCase):
    def setUp(self):
        self.repo = _init_repo()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.repo, ignore_errors=True)

    def _head_count(self):
        return int(_git(self.repo, "rev-list", "--count", "HEAD").stdout.strip())

    def test_clean_tree_is_noop(self):
        self.assertIsNone(_delegate_mod._wip_commit(self.repo, "timeout", "task"))
        self.assertEqual(self._head_count(), 1)

    def test_dirty_tree_creates_commit(self):
        with open(os.path.join(self.repo, "new.py"), "w") as f:
            f.write("x = 1\n")
        sha = _delegate_mod._wip_commit(self.repo, "timeout", "implement the thing")
        self.assertIsNotNone(sha)
        self.assertEqual(self._head_count(), 2)
        subject = _git(self.repo, "log", "-1", "--pretty=%s").stdout.strip()
        self.assertIn("auto-checkpoint on timeout", subject)
        self.assertIn("implement the thing", subject)

    def test_task_snippet_truncated_to_80(self):
        with open(os.path.join(self.repo, "n.py"), "w") as f:
            f.write("1\n")
        _delegate_mod._wip_commit(self.repo, "crash", "z" * 300)
        subject = _git(self.repo, "log", "-1", "--pretty=%s").stdout.strip()
        # reason prefix + <=80 chars of task; not the full 300.
        self.assertNotIn("z" * 90, subject)

    def test_crash_reason_also_commits(self):
        with open(os.path.join(self.repo, "n.py"), "w") as f:
            f.write("1\n")
        sha = _delegate_mod._wip_commit(self.repo, "crash", "t")
        self.assertIsNotNone(sha)
        self.assertIn("crash", _git(self.repo, "log", "-1", "--pretty=%s").stdout)

    def test_no_verify_bypasses_failing_hook(self):
        hooks = _git(self.repo, "rev-parse", "--git-path", "hooks").stdout.strip()
        hookdir = os.path.join(self.repo, hooks) if not os.path.isabs(hooks) else hooks
        os.makedirs(hookdir, exist_ok=True)
        hookpath = os.path.join(hookdir, "pre-commit")
        with open(hookpath, "w") as f:
            f.write("#!/bin/sh\necho 'hook says no'\nexit 1\n")
        os.chmod(hookpath, 0o755)
        with open(os.path.join(self.repo, "n.py"), "w") as f:
            f.write("1\n")
        sha = _delegate_mod._wip_commit(self.repo, "timeout", "t")
        self.assertIsNotNone(sha)   # --no-verify → hook can't block the checkpoint
        self.assertEqual(self._head_count(), 2)

    def test_never_pushes(self):
        import subprocess as _sp
        real = _delegate_mod.subprocess.run
        seen = []

        def rec(cmd, *a, **k):
            seen.append(cmd)
            return real(cmd, *a, **k)

        with open(os.path.join(self.repo, "n.py"), "w") as f:
            f.write("1\n")
        _delegate_mod.subprocess.run = rec
        try:
            _delegate_mod._wip_commit(self.repo, "crash", "t")
        finally:
            _delegate_mod.subprocess.run = real
        for cmd in seen:
            if isinstance(cmd, list):
                self.assertNotIn("push", cmd)

    def test_commit_failure_unstages_tree(self):
        real = _delegate_mod.subprocess.run

        def fail_commit(cmd, *a, **k):
            if isinstance(cmd, list) and "commit" in cmd:
                return types.SimpleNamespace(returncode=1, stdout="", stderr="boom")
            return real(cmd, *a, **k)

        with open(os.path.join(self.repo, "n.py"), "w") as f:
            f.write("1\n")
        _delegate_mod.subprocess.run = fail_commit
        try:
            sha = _delegate_mod._wip_commit(self.repo, "crash", "t")
        finally:
            _delegate_mod.subprocess.run = real
        self.assertIsNone(sha)
        # nothing left staged (reset ran) and no new commit
        staged = _git(self.repo, "diff", "--cached", "--name-only").stdout.strip()
        self.assertEqual(staged, "")
        self.assertEqual(self._head_count(), 1)


# --------------------------------------------------------------- liveness ----


class PidAliveTest(unittest.TestCase):
    def setUp(self):
        self._kill, self._run = _delegate_mod.os.kill, _delegate_mod.subprocess.run

    def tearDown(self):
        _delegate_mod.os.kill, _delegate_mod.subprocess.run = self._kill, self._run

    def _ps(self, comm):
        return lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=comm, stderr="")

    def test_none_pid_is_dead(self):
        self.assertFalse(_delegate_mod._pid_alive(None))
        self.assertFalse(_delegate_mod._pid_alive(0))

    def test_process_lookup_error_is_dead(self):
        def boom(pid, sig):
            raise ProcessLookupError()
        _delegate_mod.os.kill = boom
        self.assertFalse(_delegate_mod._pid_alive(12345))

    def test_alive_and_claude_comm_is_alive(self):
        _delegate_mod.os.kill = lambda pid, sig: None
        _delegate_mod.subprocess.run = self._ps("claude\n")
        self.assertTrue(_delegate_mod._pid_alive(12345))

    def test_alive_but_not_claude_is_dead(self):
        # macOS reuses low pids after OOM/reboot — a live non-claude pid is stale.
        _delegate_mod.os.kill = lambda pid, sig: None
        _delegate_mod.subprocess.run = self._ps("Python\n")
        self.assertFalse(_delegate_mod._pid_alive(12345))

    def test_permission_error_with_claude_is_alive(self):
        def eperm(pid, sig):
            raise PermissionError()
        _delegate_mod.os.kill = eperm
        _delegate_mod.subprocess.run = self._ps("claude\n")
        self.assertTrue(_delegate_mod._pid_alive(12345))


class LivenessTest(unittest.TestCase):
    def setUp(self):
        self._alive = _delegate_mod._pid_alive

    def tearDown(self):
        _delegate_mod._pid_alive = self._alive

    def test_finished_entry(self):
        glyph, text = _delegate_mod._liveness(
            {"pid": None, "exit": "ok", "ts": _delegate_mod._now() - 5}, now=_delegate_mod._now())
        self.assertEqual(glyph, "○")
        self.assertIn("finished (ok)", text)

    def test_running_when_pid_alive(self):
        _delegate_mod._pid_alive = lambda pid: True
        now = _delegate_mod._now()
        glyph, text = _delegate_mod._liveness(
            {"pid": 4242, "exit": None, "last_event_ts": now - 3}, now=now)
        self.assertEqual(glyph, "●")
        self.assertIn("running", text)
        self.assertIn("quiet", text)

    def test_not_running_when_pid_gone(self):
        _delegate_mod._pid_alive = lambda pid: False
        glyph, text = _delegate_mod._liveness({"pid": 4242, "exit": None})
        self.assertEqual(glyph, "○")
        self.assertIn("not running", text)
        self.assertIn("resumable", text)

    def test_legacy_no_pid_is_unknown(self):
        glyph, text = _delegate_mod._liveness({"session_id": "s", "dir": "/x"})
        self.assertEqual(glyph, "?")
        self.assertIn("unknown", text)


class FmtAgeTest(unittest.TestCase):
    def test_units(self):
        self.assertTrue(_delegate_mod._fmt_age(5).endswith("s"))
        self.assertTrue(_delegate_mod._fmt_age(65).endswith("m"))
        self.assertTrue(_delegate_mod._fmt_age(3700).endswith("h"))
        self.assertTrue(_delegate_mod._fmt_age(90000).endswith("d"))


class WorktreeGitStateTest(unittest.TestCase):
    def setUp(self):
        self.repo = _init_repo()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.repo, ignore_errors=True)

    def test_clean_worktree(self):
        s = _delegate_mod._worktree_git_state(self.repo)
        self.assertIn("0 dirty", s)
        self.assertIn("seed", s)   # last commit subject

    def test_dirty_and_untracked_counts(self):
        with open(os.path.join(self.repo, "seed.txt"), "a") as f:
            f.write("more\n")          # modify tracked
        with open(os.path.join(self.repo, "new.py"), "w") as f:
            f.write("1\n")             # untracked
        s = _delegate_mod._worktree_git_state(self.repo)
        self.assertIn("1 dirty", s)
        self.assertIn("1 untracked", s)

    def test_missing_dir_returns_none(self):
        self.assertIsNone(_delegate_mod._worktree_git_state("/no/such/dir/here"))


class CmdStatusTest(_RegTmpMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self._alive = _delegate_mod._pid_alive
        self.repo = _init_repo()

    def tearDown(self):
        import shutil
        _delegate_mod._pid_alive = self._alive
        shutil.rmtree(self.repo, ignore_errors=True)
        super().tearDown()

    def _capture(self, a):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _delegate_mod.cmd_status(a)
        return buf.getvalue()

    def test_running_worker_status(self):
        _delegate_mod._pid_alive = lambda pid: True
        now = _delegate_mod._now()
        _delegate_mod.save_reg({"324:Projectname": {
            "project": "Projectname", "seq": "324", "label": None, "dir": self.repo,
            "session_id": "sid-abc", "ts": now, "pid": 4242, "exit": None,
            "last_event_ts": now - 2, "phase": "→ Edit: x.py"}})
        out = self._capture(types.SimpleNamespace(seq="324", project=None, repo=None,
                                                  label=None, all=False))
        self.assertIn("●", out)
        self.assertIn("running", out)
        self.assertIn("sid-abc", out)          # resume line
        self.assertIn("claude --resume", out)
        self.assertIn("Edit", out)             # phase

    def test_finished_and_resumable_rendered(self):
        _delegate_mod._pid_alive = lambda pid: False
        now = _delegate_mod._now()
        _delegate_mod.save_reg({
            "1:P": {"project": "P", "seq": "1", "label": None, "dir": self.repo,
                    "session_id": "s1", "ts": now, "pid": None, "exit": "timeout"},
            "2:P": {"project": "P", "seq": "2", "label": None, "dir": self.repo,
                    "session_id": "s2", "ts": now, "pid": 999, "exit": None}})
        out = self._capture(types.SimpleNamespace(seq=None, project=None, repo=None,
                                                  label=None, all=True))
        self.assertIn("finished (timeout)", out)
        self.assertIn("not running", out)

    def test_legacy_entry_unknown_glyph(self):
        _delegate_mod.save_reg({"old:P": {"project": "P", "seq": "old", "dir": self.repo,
                                          "session_id": "s"}})
        out = self._capture(types.SimpleNamespace(seq=None, project=None, repo=None,
                                                  label=None, all=True))
        self.assertIn("?", out)
        self.assertIn("unknown", out)

    def test_empty_registry(self):
        out = self._capture(types.SimpleNamespace(seq=None, project=None, repo=None,
                                                  label=None, all=True))
        self.assertIn("no workers", out.lower())


class StatusSubparserTest(unittest.TestCase):
    def _parse(self, *extra):
        captured = {}
        saved_fn, saved_argv = _delegate_mod.cmd_status, sys.argv
        _delegate_mod.cmd_status = lambda a: captured.update(a=a)
        try:
            sys.argv = ["delegate", "status", *extra]
            _delegate_mod.main()
        finally:
            _delegate_mod.cmd_status, sys.argv = saved_fn, saved_argv
        return captured["a"]

    def test_bare_status_has_no_filters(self):
        a = self._parse()
        self.assertIsNone(a.seq)
        self.assertIsNone(a.project)
        self.assertFalse(a.all)

    def test_seq_and_label_filters(self):
        a = self._parse("--seq", "324", "--label", "api")
        self.assertEqual(a.seq, "324")
        self.assertEqual(a.label, "api")

    def test_all_flag(self):
        self.assertTrue(self._parse("--all").all)


class CmdListGlyphTest(_RegTmpMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self._alive = _delegate_mod._pid_alive

    def tearDown(self):
        _delegate_mod._pid_alive = self._alive
        super().tearDown()

    def _capture(self):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _delegate_mod.cmd_list(types.SimpleNamespace())
        return buf.getvalue()

    def test_glyph_prepended_and_legacy_question(self):
        _delegate_mod._pid_alive = lambda pid: False
        now = _delegate_mod._now()
        _delegate_mod.save_reg({
            "324:Projectname": {"project": "Projectname", "dir": "/wt", "session_id": "sid1",
                         "ts": now, "pid": 999, "exit": None},
            "old:P": {"project": "P", "dir": "/x", "session_id": "sid2", "ts": now - 1}})
        out = self._capture()
        self.assertIn("○", out)   # pid gone → not running glyph
        self.assertIn("?", out)   # legacy no-pid row
        self.assertIn("sid1", out)


# ------------------------------------------------------- run_worker / cmd_run ----


class _FakePopen:
    def __init__(self, lines, returncode=0):
        self.stdout = iter(lines)
        self.pid = 4242
        self._rc = returncode
        self.returncode = None

    def wait(self):
        self.returncode = self._rc
        return self._rc

    def poll(self):
        return self.returncode


class _BlockingStdout:
    """Yields seed lines then blocks (like a quiet worker's readline) until the
    process is 'killed' — the watchdog test unblocks it via a monkeypatched
    _kill_group that sets `.killed`."""
    def __init__(self, seed, killed):
        self.seed, self.killed = seed, killed

    def __iter__(self):
        for line in self.seed:
            yield line
        while not self.killed.wait(0.02):
            pass


class _BlockingPopen:
    def __init__(self, seed, killed):
        self.stdout = _BlockingStdout(seed, killed)
        self.pid = 4242
        self.returncode = None
        self._killed = killed

    def wait(self):
        self.returncode = -15
        return -15

    def poll(self):
        return self.returncode


def _popen_factory(proc, stderr_write=""):
    def _factory(cmd, **kw):
        fh = kw.get("stderr")
        if fh is not None and stderr_write:
            fh.write(stderr_write)
            fh.flush()
        return proc
    return _factory


class KillGroupTest(unittest.TestCase):
    def setUp(self):
        self._getpgid, self._killpg = _delegate_mod.os.getpgid, _delegate_mod.os.killpg

    def tearDown(self):
        _delegate_mod.os.getpgid, _delegate_mod.os.killpg = self._getpgid, self._killpg

    def test_sends_sigterm_then_sigkill_to_group(self):
        sigs = []
        _delegate_mod.os.getpgid = lambda pid: 777
        _delegate_mod.os.killpg = lambda pgid, sig: sigs.append((pgid, sig))
        proc = types.SimpleNamespace(pid=4242, poll=lambda: None)   # never exits
        _delegate_mod._kill_group(proc, grace=0.05)
        self.assertEqual(sigs[0], (777, _delegate_mod.signal.SIGTERM))
        self.assertIn((777, _delegate_mod.signal.SIGKILL), sigs)

    def test_no_sigkill_when_process_exits_after_term(self):
        sigs = []
        _delegate_mod.os.getpgid = lambda pid: 777
        _delegate_mod.os.killpg = lambda pgid, sig: sigs.append((pgid, sig))
        proc = types.SimpleNamespace(pid=4242, poll=lambda: 0)      # exits immediately
        _delegate_mod._kill_group(proc, grace=0.05)
        self.assertEqual(sigs, [(777, _delegate_mod.signal.SIGTERM)])

    def test_missing_group_is_swallowed(self):
        def boom(pid):
            raise ProcessLookupError()
        _delegate_mod.os.getpgid = boom
        _delegate_mod._kill_group(types.SimpleNamespace(pid=1), grace=0.05)  # no raise


class RunWorkerStreamTest(_RegTmpMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self._popen = _delegate_mod.subprocess.Popen

    def tearDown(self):
        _delegate_mod.subprocess.Popen = self._popen
        super().tearDown()

    def _run(self, proc, stderr_write="", **kw):
        import io
        import contextlib
        _delegate_mod.subprocess.Popen = _popen_factory(proc, stderr_write)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            res = _delegate_mod.run_worker("/tmp", "task", **kw)
        return res, buf.getvalue()

    def test_returns_contract_and_streams_progress(self):
        lines = [
            '{"type":"system","subtype":"init","model":"sonnet"}',
            '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Edit","input":{"file_path":"a.py"}}]}}',
            '{"type":"result","subtype":"success","result":"done","session_id":"sid-z","total_cost_usd":0.02}',
        ]
        (rc, result_json, stderr_text, timed_out), err = self._run(
            _FakePopen(lines, 0), stderr_write="some stderr\n")
        self.assertEqual(rc, 0)
        self.assertFalse(timed_out)
        self.assertIn("some stderr", stderr_text)
        # result event round-trips through the EXISTING _parse_result unchanged
        rt, sid, cost, model, usage = _delegate_mod._parse_result(result_json)
        self.assertEqual(rt, "done")
        self.assertEqual(sid, "sid-z")
        self.assertEqual(cost, 0.02)
        # progress feed went to stderr, incl. the §1 start header
        self.assertIn("worker started", err)
        self.assertIn("→ Edit", err)

    def test_missing_result_returns_none(self):
        lines = ['{"type":"assistant","message":{"content":[{"type":"text","text":"hi"}]}}']
        (rc, result_json, stderr_text, timed_out), err = self._run(_FakePopen(lines, 0))
        self.assertIsNone(result_json)

    def test_error_subtype_result_relayed(self):
        lines = ['{"type":"result","subtype":"error_max_turns","session_id":"sid-e","is_error":true}']
        (rc, result_json, _st, _to), _err = self._run(_FakePopen(lines, 0))
        self.assertIsNotNone(result_json)
        rt, sid, cost, model, usage = _delegate_mod._parse_result(result_json)
        self.assertEqual(sid, "sid-e")   # relayed verbatim; no `result` field → None result path

    def test_heartbeat_written_with_key(self):
        _delegate_mod.save_reg({"K": {"session_id": "s", "project": "P"}})
        lines = [
            '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash","input":{"command":"ls"}}]}}',
            '{"type":"result","result":"ok","session_id":"s"}',
        ]
        self._run(_FakePopen(lines, 0), key="K")
        e = _delegate_mod.load_reg()["K"]
        self.assertEqual(e["pid"], 4242)          # real worker pid recorded at launch
        self.assertIn("started_ts", e)
        self.assertIn("phase", e)

    def test_timeout_watchdog_sets_flag(self):
        killed = threading.Event()
        proc = _BlockingPopen(['{"type":"assistant","message":{"content":[{"type":"text","text":"go"}]}}'],
                              killed)
        saved = _delegate_mod._kill_group
        _delegate_mod._kill_group = lambda p, **k: p._killed.set()
        try:
            (rc, result_json, _st, timed_out), _err = self._run(proc, timeout=0.3)
        finally:
            _delegate_mod._kill_group = saved
        self.assertTrue(timed_out)


class CmdRunIntegrationTest(_RegTmpMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.repo = _init_repo()
        self.wt = _init_repo()        # stand-in worktree tree (a distinct git repo)
        self._run_worker = _delegate_mod.run_worker
        self._resolve_wt = _delegate_mod.resolve_worktree
        self._attached = _delegate_mod._attached_seq
        _delegate_mod._attached_seq = lambda: None                 # never inherit in tests
        _delegate_mod.resolve_worktree = lambda *a, **k: self.wt   # dirpath → the worktree

    def tearDown(self):
        import shutil
        _delegate_mod.run_worker = self._run_worker
        _delegate_mod.resolve_worktree = self._resolve_wt
        _delegate_mod._attached_seq = self._attached
        shutil.rmtree(self.repo, ignore_errors=True)
        shutil.rmtree(self.wt, ignore_errors=True)
        super().tearDown()

    def _args(self, **kw):
        # Default to a WRITE delegation (--worktree) so dirpath is the worktree,
        # not the main checkout (auto-WIP must never touch the main checkout).
        # harness="codex" pins the NON-bg (legacy `-p`) cmd_run path these tests
        # drive via a monkeypatched run_worker; the claude harness now goes --bg
        # (see tests/test_delegate_bg_cmdrun.py). (#463)
        d = dict(repo=self.repo, project=None, task="implement the feature",
                 worktree="wt-slug", branch=None, base=None, seq=None, label=None,
                 solo=True, fresh=False, model="sonnet", timeout=None,
                 harness="codex")
        d.update(kw)
        return types.SimpleNamespace(**d)

    def _dirty(self, tree):
        with open(os.path.join(tree, "changed.py"), "w") as f:
            f.write("x = 1\n")

    def _head_count(self, tree):
        return int(_git(tree, "rev-list", "--count", "HEAD").stdout.strip())

    def _key(self, a):
        reg = _delegate_mod.load_reg()
        return next(iter(reg))   # single worker registered in these tests

    def test_abnormal_crash_wip_commits_and_exits(self):
        self._dirty(self.wt)
        _delegate_mod.run_worker = lambda *a, **k: (1, None, "boom stderr", False)
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            with self.assertRaises(SystemExit) as cm:
                _delegate_mod.cmd_run(self._args())
        self.assertEqual(self._head_count(self.wt), 2)     # auto-WIP checkpoint in worktree
        subject = _git(self.wt, "log", "-1", "--pretty=%s").stdout
        self.assertIn("auto-checkpoint on crash", subject)
        e = _delegate_mod.load_reg()[self._key(None)]
        self.assertEqual(e["exit"], "crash")
        self.assertIsNone(e["pid"])                        # terminal write nulled pid
        sha = _git(self.wt, "rev-parse", "--short", "HEAD").stdout.strip()
        self.assertIn(sha, str(cm.exception))              # sha threaded into SystemExit

    def test_abnormal_timeout_wip_reason_timeout(self):
        self._dirty(self.wt)
        _delegate_mod.run_worker = lambda *a, **k: (0, None, "", True)  # timed_out
        with self.assertRaises(SystemExit):
            import io
            import contextlib
            with contextlib.redirect_stderr(io.StringIO()):
                _delegate_mod.cmd_run(self._args(timeout=30))
        self.assertIn("auto-checkpoint on timeout",
                      _git(self.wt, "log", "-1", "--pretty=%s").stdout)
        self.assertEqual(_delegate_mod.load_reg()[self._key(None)]["exit"], "timeout")

    def test_abnormal_on_main_checkout_does_not_wip(self):
        # A read-only (no --worktree) run crashes in the MAIN checkout — auto-WIP
        # must NOT sweep the user's uncommitted main-checkout work into a commit.
        self._dirty(self.repo)
        _delegate_mod.run_worker = lambda *a, **k: (1, None, "boom", False)
        import io
        import contextlib
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                _delegate_mod.cmd_run(self._args(worktree=None))
        self.assertEqual(self._head_count(self.repo), 1)   # NO commit on main checkout
        self.assertEqual(_delegate_mod.load_reg()[self._key(None)]["exit"], "crash")

    def test_clean_finish_no_wip_even_if_dirty(self):
        self._dirty(self.wt)   # dirty, but a CLEAN finish must never auto-commit
        rj = json.dumps({"type": "result", "result": "all done",
                         "session_id": "sid-ok", "total_cost_usd": 0.0})
        _delegate_mod.run_worker = lambda *a, **k: (0, rj, "", False)
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with contextlib.redirect_stderr(io.StringIO()):
                _delegate_mod.cmd_run(self._args())
        self.assertEqual(self._head_count(self.wt), 1)     # NO wip commit
        self.assertIn("all done", buf.getvalue())          # result_text relayed to stdout
        e = _delegate_mod.load_reg()[self._key(None)]
        self.assertEqual(e["exit"], "ok")
        self.assertIsNone(e["pid"])

    def test_missing_result_is_abnormal_crash(self):
        # B4: rc 0 but no terminal result (streamed then died) → abnormal crash.
        self._dirty(self.wt)
        _delegate_mod.run_worker = lambda *a, **k: (0, None, "", False)
        with self.assertRaises(SystemExit):
            import io
            import contextlib
            with contextlib.redirect_stderr(io.StringIO()):
                _delegate_mod.cmd_run(self._args())
        self.assertEqual(_delegate_mod.load_reg()[self._key(None)]["exit"], "crash")

    def test_success_with_seq_fires_writebacks(self):
        # Regression guard: add-project / add-cost / worker-event still fire on a
        # successful seq'd run (against the new run_worker return shape).
        rj = json.dumps({"type": "result", "result": "ok done",
                         "session_id": "sid-s", "total_cost_usd": 0.05,
                         "modelUsage": {"claude-sonnet-4": {"inputTokens": 10, "outputTokens": 5}}})
        _delegate_mod.run_worker = lambda *a, **k: (0, rj, "", False)
        real = _delegate_mod.subprocess.run
        seen = []

        def rec(cmd, *a, **k):
            if isinstance(cmd, list) and cmd[:1] == ["python3"]:
                seen.append(cmd)
                return types.SimpleNamespace(returncode=0, stdout="", stderr="")
            return real(cmd, *a, **k)

        _delegate_mod.subprocess.run = rec
        import io
        import contextlib
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                with contextlib.redirect_stderr(io.StringIO()):
                    _delegate_mod.cmd_run(self._args(seq="777"))
        finally:
            _delegate_mod.subprocess.run = real
        joined = [" ".join(c) for c in seen]
        self.assertTrue(any("add-project" in j for j in joined))
        self.assertTrue(any("add-cost" in j for j in joined))
        self.assertTrue(any("add-event" in j for j in joined))


if __name__ == "__main__":
    unittest.main()
