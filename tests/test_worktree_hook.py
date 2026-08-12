"""WorktreeCreate — the opt-in provisioner and its installer.

This hook REPLACES worktree creation: it must create the worktree itself, print the
absolute path as the FIRST stdout line, and any non-zero exit fails the operation.
That is why it never ships in hooks/hooks.json and why these tests are mostly about
the contract rather than the feature:

  * the path is on stdout line 1, and nothing else is ever on stdout;
  * exit is non-zero ONLY when `git worktree add` genuinely failed;
  * every provisioning step is best-effort — an error there can change neither the
    exit code nor stdout;
  * the installer writes exactly ONE entry, `off` removes exactly it, double-`on` is
    idempotent, and a foreign WorktreeCreate entry is a refusal (two hooks racing to
    create a worktree and print a path is not a composition).

No real git and no real ~/.claude.json: a scripted runner records the argv it was
asked for, and the trust file is a fixture passed in.
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

import hook_health      # noqa: E402
import setup            # noqa: E402
import worktree_hook    # noqa: E402


class FakeGit:
    """A scripted `git`. Records every argv, answers the four commands the module
    issues, and CREATES the directory on a successful `worktree add` so provisioning
    has somewhere real to write."""

    def __init__(self, repo, main=None, refs=(), fail=None):
        self.repo = repo
        self.main = main or repo
        self.refs = set(refs)
        self.fail = fail                    # stderr text → `worktree add` fails
        self.calls = []

    def __call__(self, cmd, cwd=None):
        self.calls.append(list(cmd))
        if "rev-parse" in cmd:
            return (0, self.repo + "\n", "")
        if "list" in cmd:
            return (0, "worktree %s\nHEAD abc123\nbranch refs/heads/main\n\n" % self.main, "")
        if "show-ref" in cmd:
            return (0 if cmd[-1] in self.refs else 1, "", "")
        if "add" in cmd:
            if self.fail:
                return (128, "", self.fail)
            for tok in cmd[cmd.index("add") + 1:]:
                if tok.startswith("/"):
                    os.makedirs(tok, exist_ok=True)
                    break
            return (0, "", "")
        return (0, "", "")

    def add_argv(self):
        for c in self.calls:
            if "add" in c:
                return c
        return None


# ================================================================ name handling ===
class SafeNameTest(unittest.TestCase):
    def test_a_slashed_branch_becomes_one_segment(self):
        self.assertEqual(worktree_hook.safe_name("feature/new-thing"), "feature-new-thing")

    def test_traversal_cannot_survive(self):
        """A name from the payload must never be able to invent a path separator."""
        self.assertNotIn("/", worktree_hook.safe_name("../../etc/passwd"))
        self.assertNotIn("..", worktree_hook.safe_name("../../etc/passwd").strip("-"))

    def test_empty_is_empty(self):
        self.assertEqual(worktree_hook.safe_name(""), "")
        self.assertEqual(worktree_hook.safe_name(None), "")


# ================================================================== creation ======
class CreateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-wt-create-")
        self.repo = os.path.join(self.tmp, "repo")
        self.parent = os.path.join(self.tmp, "worktrees")
        os.makedirs(self.repo)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _create(self, payload=None, **kw):
        git = FakeGit(self.repo, **kw)
        payload = dict({"cwd": self.repo, "parent_dir": self.parent}, **(payload or {}))
        return worktree_hook.create(payload, run=git), git

    def test_a_new_branch_is_created_from_head(self):
        """No fetch, ever — branching from an unfetched remote ref would silently base
        the work on a stale snapshot, and this hook makes no network calls."""
        res, git = self._create({"branch": "wip-thing"})
        self.assertIsNone(res["error"])
        self.assertEqual(res["path"], os.path.join(self.parent, "wip-thing"))
        self.assertEqual(git.add_argv()[-4:],
                         ["-b", "wip-thing", res["path"], "HEAD"])

    def test_an_existing_local_branch_is_checked_out(self):
        res, git = self._create({"branch": "main"}, refs=["refs/heads/main"])
        self.assertEqual(git.add_argv()[-2:], [res["path"], "main"])

    def test_a_remote_only_branch_becomes_a_tracking_branch(self):
        res, git = self._create({"branch": "shipped"}, refs=["refs/remotes/origin/shipped"])
        self.assertEqual(git.add_argv()[-5:],
                         ["--track", "-b", "shipped", res["path"], "origin/shipped"])

    def test_an_explicit_base_ref_is_honoured(self):
        res, git = self._create({"branch": "x", "base_ref": "origin/dev"})
        self.assertEqual(git.add_argv()[-1], "origin/dev")

    def test_no_branch_gets_a_generated_name(self):
        res, _ = self._create()
        self.assertTrue(os.path.basename(res["path"]).startswith("wt-"))

    def test_a_colliding_name_is_suffixed(self):
        first, _ = self._create({"branch": "dup"})
        second, _ = self._create({"branch": "dup"})
        self.assertEqual(os.path.basename(first["path"]), "dup")
        self.assertEqual(os.path.basename(second["path"]), "dup-2")

    def test_parent_dir_defaults_under_the_main_checkout(self):
        git = FakeGit(self.repo)
        res = worktree_hook.create({"cwd": self.repo, "branch": "x"}, run=git)
        self.assertEqual(os.path.dirname(res["path"]),
                         os.path.join(self.repo, ".claude", "worktrees"))

    def test_the_main_checkout_is_read_from_worktree_list(self):
        """A session ALREADY inside a worktree must copy settings from the MAIN
        checkout, not from its own (probably empty) .claude/."""
        main = os.path.join(self.tmp, "mainco")
        git = FakeGit(self.repo, main=main)
        res = worktree_hook.create({"cwd": self.repo, "parent_dir": self.parent}, run=git)
        self.assertEqual(res["main"], main)

    def test_not_a_repo_is_an_error(self):
        def norepo(cmd, cwd=None):
            return (128, "", "fatal: not a git repository")
        res = worktree_hook.create({"cwd": self.tmp}, run=norepo)
        self.assertIsNone(res["path"])
        self.assertIn("not a git repository", res["error"])

    def test_a_failing_git_add_is_an_error(self):
        res, _ = self._create({"branch": "x"}, fail="fatal: 'x' is already checked out")
        self.assertIsNone(res["path"])
        self.assertIn("already checked out", res["error"])


# ================================================================ provisioning ====
class ProvisionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-wt-prov-")
        self.main = os.path.join(self.tmp, "main")
        self.wt = os.path.join(self.tmp, "wt")
        os.makedirs(os.path.join(self.main, ".claude"))
        os.makedirs(self.wt)
        self.claude_json = os.path.join(self.tmp, "claude.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _grants(self, body='{"permissions": {"allow": ["Bash(pytest:*)"]}}'):
        p = os.path.join(self.main, ".claude", "settings.local.json")
        with open(p, "w") as f:
            f.write(body)
        return p

    def _trust_file(self, data):
        with open(self.claude_json, "w") as f:
            json.dump(data, f)

    # -- (a) the worker tool grants -------------------------------------------

    def test_settings_local_is_copied_from_the_main_checkout(self):
        """Gitignored, so a new worktree has none — and a headless worker that hits a
        missing grant cannot prompt for it."""
        self._grants()
        notes = worktree_hook.provision(self.wt, self.main, claude_json=self.claude_json)
        dst = os.path.join(self.wt, ".claude", "settings.local.json")
        self.assertTrue(os.path.isfile(dst))
        self.assertIn("Bash(pytest:*)", open(dst).read())
        self.assertIn("copied .claude/settings.local.json", notes)

    def test_an_existing_destination_is_never_overwritten(self):
        self._grants()
        os.makedirs(os.path.join(self.wt, ".claude"))
        dst = os.path.join(self.wt, ".claude", "settings.local.json")
        with open(dst, "w") as f:
            f.write("MINE")
        worktree_hook.provision(self.wt, self.main, claude_json=self.claude_json)
        self.assertEqual(open(dst).read(), "MINE")

    def test_no_source_file_is_silent(self):
        notes = worktree_hook.provision(self.wt, self.main, claude_json=self.claude_json)
        self.assertNotIn("copied .claude/settings.local.json", notes)

    # -- (b) the trust entry ---------------------------------------------------

    def test_trust_entry_is_added_and_other_keys_survive(self):
        self._trust_file({"numStartups": 12,
                          "projects": {"/other": {"hasTrustDialogAccepted": True,
                                                  "history": [1, 2]}}})
        self.assertTrue(worktree_hook.add_trust_entry(self.wt, path=self.claude_json))
        data = json.load(open(self.claude_json))
        self.assertIs(data["projects"][self.wt]["hasTrustDialogAccepted"], True)
        self.assertEqual(data["numStartups"], 12)                    # untouched
        self.assertEqual(data["projects"]["/other"]["history"], [1, 2])

    def test_trust_entry_is_idempotent(self):
        self._trust_file({"projects": {}})
        self.assertTrue(worktree_hook.add_trust_entry(self.wt, path=self.claude_json))
        before = open(self.claude_json).read()
        self.assertFalse(worktree_hook.add_trust_entry(self.wt, path=self.claude_json))
        self.assertEqual(open(self.claude_json).read(), before)      # no rewrite at all

    def test_an_existing_entry_keeps_its_other_fields(self):
        self._trust_file({"projects": {self.wt: {"history": ["a"]}}})
        worktree_hook.add_trust_entry(self.wt, path=self.claude_json)
        entry = json.load(open(self.claude_json))["projects"][self.wt]
        self.assertIs(entry["hasTrustDialogAccepted"], True)
        self.assertEqual(entry["history"], ["a"])

    def test_a_missing_trust_file_is_not_created(self):
        """Its absence means Claude Code has not written one; inventing it would be us
        guessing at another app's schema."""
        self.assertFalse(worktree_hook.add_trust_entry(self.wt, path=self.claude_json))
        self.assertFalse(os.path.exists(self.claude_json))

    def test_a_non_object_trust_file_is_left_alone(self):
        self._trust_file([1, 2, 3])
        self.assertFalse(worktree_hook.add_trust_entry(self.wt, path=self.claude_json))

    def test_a_broken_trust_file_never_raises_out_of_provision(self):
        with open(self.claude_json, "w") as f:
            f.write("{not json")
        notes = worktree_hook.provision(self.wt, self.main, claude_json=self.claude_json)
        self.assertTrue(any("trust entry failed" in n for n in notes))

    def test_claude_json_path_follows_the_config_dir(self):
        had = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = "/somewhere/cfg"
        try:
            self.assertEqual(worktree_hook.claude_json_path(), "/somewhere/cfg/.claude.json")
        finally:
            if had is None:
                os.environ.pop("CLAUDE_CONFIG_DIR", None)
            else:
                os.environ["CLAUDE_CONFIG_DIR"] = had


# ============================================================== the hook contract ==
class HandleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-wt-handle-")
        self._home = os.environ.get("TASK_STATION_HOME")
        os.environ["TASK_STATION_HOME"] = self.tmp        # hook-health log lands here
        self.repo = os.path.join(self.tmp, "repo")
        self.parent = os.path.join(self.tmp, "worktrees")
        os.makedirs(os.path.join(self.repo, ".claude"))
        with open(os.path.join(self.repo, ".claude", "settings.local.json"), "w") as f:
            f.write("{}")
        self.claude_json = os.path.join(self.tmp, "claude.json")
        with open(self.claude_json, "w") as f:
            json.dump({"projects": {}}, f)

    def tearDown(self):
        if self._home is None:
            os.environ.pop("TASK_STATION_HOME", None)
        else:
            os.environ["TASK_STATION_HOME"] = self._home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _handle(self, payload=None, **kw):
        git = FakeGit(self.repo, **kw)
        out, err = io.StringIO(), io.StringIO()
        rc = worktree_hook.handle(
            dict({"cwd": self.repo, "parent_dir": self.parent, "branch": "wip"},
                 **(payload or {})),
            out, err, run=git, claude_json=self.claude_json)
        return rc, out.getvalue(), err.getvalue()

    def test_stdout_is_the_path_and_only_the_path(self):
        rc, out, err = self._handle()
        self.assertEqual(rc, 0)
        lines = out.splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0], os.path.join(self.parent, "wip"))
        self.assertTrue(os.path.isabs(lines[0]))

    def test_it_provisions_what_it_created(self):
        rc, out, _ = self._handle()
        wt = out.strip()
        self.assertTrue(os.path.isfile(os.path.join(wt, ".claude", "settings.local.json")))
        self.assertIs(json.load(open(self.claude_json))["projects"][wt]["hasTrustDialogAccepted"],
                      True)

    def test_a_creation_failure_exits_non_zero_and_prints_no_path(self):
        """The ONE sanctioned failure: there is no worktree, so the harness operation
        SHOULD fail."""
        rc, out, err = self._handle(fail="fatal: could not create worktree")
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertIn("could not create worktree", err)

    def test_a_provisioning_error_changes_neither_exit_code_nor_stdout(self):
        real = worktree_hook.provision
        worktree_hook.provision = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            rc, out, err = self._handle()
        finally:
            worktree_hook.provision = real
        self.assertEqual(rc, 0)
        self.assertEqual(out.splitlines(), [os.path.join(self.parent, "wip")])

    def test_it_records_what_it_provisioned_informationally(self):
        self._handle()
        recs = hook_health.entries()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["label"], "worktree-create")
        self.assertEqual(recs[0]["code"], 0)          # a report, not a failure
        self.assertIsNone(hook_health.nag())          # …so it never nags

    def test_an_empty_payload_still_creates_something(self):
        """The event's field names are the harness's, not ours: an unrecognised or
        absent field must never be the thing that breaks creation."""
        git = FakeGit(self.repo)
        out, err = io.StringIO(), io.StringIO()
        cwd = os.getcwd()
        os.chdir(self.repo)
        try:
            rc = worktree_hook.handle({}, out, err, run=git, claude_json=self.claude_json)
        finally:
            os.chdir(cwd)
        self.assertEqual(rc, 0)
        self.assertTrue(out.getvalue().strip().startswith(
            os.path.join(self.repo, ".claude", "worktrees")))


# ============================================================== the installer =====
class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-wt-install-")
        self._env = {k: os.environ.get(k) for k in ("CLAUDE_CONFIG_DIR", "TASK_STATION_HOME")}
        os.environ["CLAUDE_CONFIG_DIR"] = self.tmp
        os.environ["TASK_STATION_HOME"] = self.tmp
        self.settings = setup.settings_path()

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _read(self):
        with open(self.settings) as f:
            return json.load(f)

    def _entries(self):
        return self._read()["hooks"]["WorktreeCreate"]

    def test_on_writes_exactly_one_marked_entry(self):
        msg = setup.install_worktree_hook()
        entries = self._entries()
        self.assertEqual(len(entries), 1)
        cmd = entries[0]["hooks"][0]["command"]
        self.assertIn(setup.WORKTREE_HOOK_MARKER, cmd)
        self.assertIn("on_worktree_create.sh", cmd)
        self.assertIn("--worktree-hook off", msg)          # the reverse is always named

    def test_the_command_uses_the_stable_engine_path(self):
        """NOT the versioned plugin-cache dir: a /plugin update must not leave
        settings.json pointing at a directory that no longer exists. The literal `..`
        must survive — task-station-engine is a symlink to lib/, so its parent is the
        plugin root."""
        setup.install_worktree_hook()
        cmd = self._entries()[0]["hooks"][0]["command"]
        self.assertIn(os.path.join(self.tmp, "task-station-engine", "..", "hooks"), cmd)

    def test_double_on_is_idempotent(self):
        setup.install_worktree_hook()
        msg = setup.install_worktree_hook()
        self.assertEqual(len(self._entries()), 1)
        self.assertIn("already", msg)

    def test_off_removes_exactly_what_on_wrote(self):
        before = {"model": "claude-opus-5"}
        with open(self.settings, "w") as f:
            json.dump(before, f)
        setup.install_worktree_hook()
        setup.remove_worktree_hook()
        data = self._read()
        self.assertEqual(data, before)                     # byte-for-byte round trip
        self.assertNotIn(setup.WORKTREE_HOOK_MANIFEST_KEY, setup._manifest())

    def test_off_keeps_a_users_other_hooks(self):
        with open(self.settings, "w") as f:
            json.dump({"hooks": {"Stop": [{"hooks": [{"type": "command",
                                                      "command": "echo hi"}]}]}}, f)
        setup.install_worktree_hook()
        setup.remove_worktree_hook()
        data = self._read()
        self.assertEqual(data["hooks"]["Stop"][0]["hooks"][0]["command"], "echo hi")
        self.assertNotIn("WorktreeCreate", data["hooks"])

    def test_a_foreign_entry_is_a_refusal(self):
        """Two WorktreeCreate hooks both create the worktree and both print a path —
        that is a race, not a composition."""
        foreign = {"hooks": {"WorktreeCreate": [
            {"hooks": [{"type": "command", "command": "bash /their/creator.sh"}]}]}}
        with open(self.settings, "w") as f:
            json.dump(foreign, f)
        msg = setup.install_worktree_hook()
        self.assertEqual(self._read(), foreign)            # nothing written
        self.assertIn("Another WorktreeCreate hook", msg)
        self.assertEqual(setup.worktree_hook_status(), "off")

    def test_off_never_removes_a_foreign_entry(self):
        foreign = {"hooks": {"WorktreeCreate": [
            {"hooks": [{"type": "command", "command": "bash /their/creator.sh"}]}]}}
        with open(self.settings, "w") as f:
            json.dump(foreign, f)
        msg = setup.remove_worktree_hook()
        self.assertEqual(self._read(), foreign)
        self.assertIn("nothing to remove", msg)

    def test_off_when_never_installed_writes_nothing(self):
        msg = setup.remove_worktree_hook()
        self.assertIn("nothing to remove", msg)
        self.assertFalse(os.path.exists(self.settings))

    def test_status_reflects_settings_not_the_flag(self):
        self.assertEqual(setup.worktree_hook_status(), "off")
        setup.install_worktree_hook()
        self.assertEqual(setup.worktree_hook_status(), "installed")
        setup.remove_worktree_hook()
        self.assertEqual(setup.worktree_hook_status(), "off")

    def test_settings_are_backed_up_before_any_modification(self):
        with open(self.settings, "w") as f:
            json.dump({"model": "claude-opus-5"}, f)
        setup.install_worktree_hook()
        self.assertTrue(os.path.exists(self.settings + setup.SETTINGS_BACKUP_WORKTREE))


if __name__ == "__main__":
    unittest.main()
