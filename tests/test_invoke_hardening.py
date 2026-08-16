"""INVOKE HARDENING — a preview that costs nothing, a launch that says which kind it
was, and a child that never stalls on a dialog nobody chose to answer.

WHAT THIS COVERS. `invoke` spawns the child. Dogfooding the loop on a real board on
2026-08-16 found three ways it lies or stops, and this file pins the fix for each.

  1. A PREVIEW MUST COST NOTHING. `--print-command` is a REAL launch path — the human
     runs the printed line — so it legitimately mints the pre-attached session. What
     was missing is a way to LOOK: `--dry-run` prints the command it would run and
     writes nothing at all. No minted session, no event on the child, no event on the
     orchestrator, no trust file touched. A preview that mutates is not a preview.

  2. A LAUNCH RECORDS WHICH KIND IT WAS. The parent trail showed the same child
     invoked twice when it had been previewed once and launched once, because both
     wrote the identical event. A trail that miscounts invokes is worse than one that
     records nothing: the 3.6.0 RUNNING column exists to stop a double-invoke, and it
     cannot do that on a log that already double-counts. So a hand-off to a human
     reads as a MANUAL LAUNCH, and only a window launch reads as an invoke — including
     when the window opener FAILS, which is a manual launch in every respect that
     matters.

  3. A FRESH WORKSPACE HAS FIRST-RUN GATES, AND A LOOP CANNOT ANSWER THEM. A worktree
     created moments ago has no trust entry, so the child opens onto the trust dialog
     and waits at a keystroke that is not a decision — invisible to the scan, because
     liveness is derived from SessionStart and SessionStart has not fired yet. The
     second gate is the project-scoped MCP approval for the workspace's own .mcp.json.
     Clearing one and not the other buys nothing, so both are cleared together.

  4. THE GUARD MATTERS MORE THAN THE FIX. Blanket-trusting any `--cwd` turns a safety
     prompt into a no-op for arbitrary directories, which is a security regression
     wearing a convenience costume. Trust is only ever INHERITED: a git worktree may
     be pre-trusted when its own main checkout is already a trusted project on this
     machine, and nothing else may be. A refusal is printed with its reason and the
     invoke proceeds — the human answers the dialog once, which is exactly what a
     safety prompt is for.

  5. THE CHILD INHERITS THE PARENT'S PERMISSIONS. A role may RESTRICT and may never
     REPLACE. Passing no `--permission-mode` inherits the human's configured default
     for free, so the role table only speaks when it is narrowing.

The git fixtures here build a REAL repository and a REAL linked worktree, because the
guard's whole question — "is this a worktree of that main checkout?" — is answered by
git and a faked answer would test nothing.
"""
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(_REPO_ROOT, "lib")
sys.path.insert(0, LIB)

_TMP_HOME = tempfile.mkdtemp(prefix="ts-invoke-")
os.environ["TASK_STATION_HOME"] = _TMP_HOME

import store                                                            # noqa: E402
from board import workspace as ws                                       # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


class _Args:
    """invoke's argv, with the flags this file is about defaulting to OFF so every
    test states the behaviour it means."""

    def __init__(self, **kw):
        defaults = dict(session=None, task=None, from_ref=None, ask="Land the port.",
                        role=None, model=None, permission_mode=None, cwd=None,
                        print_command=False, dry_run=False)
        defaults.update(kw)
        self.__dict__.update(defaults)


def _git(*args, cwd=None):
    """A git call that never raises — the fixture skips rather than errors when git
    is missing, because a machine without git cannot answer the guard's question."""
    try:
        return subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True,
                              text=True, timeout=60)
    except Exception:
        return None


def _have_git():
    r = _git("--version")
    return bool(r and r.returncode == 0)


class _InvokeTest(unittest.TestCase):
    """A throwaway store, a throwaway `~/.claude.json`, a real repo + real worktree,
    and a window opener that records instead of opening one."""

    def setUp(self):
        if not _have_git():
            self.skipTest("git is unavailable")
        self.tmp = tempfile.mkdtemp(prefix="invoke-hardening-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        store.reset_cache()
        # Claude Code's own config file, redirected so no test can read or write the
        # real one. `claude_json_path` honours CLAUDE_CONFIG_DIR.
        self.cfg = os.path.join(self.tmp, "cfg")
        os.makedirs(self.cfg)
        self._orig_cfg = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = self.cfg
        self.claude_json = os.path.join(self.cfg, ".claude.json")
        self.opened = []
        self._orig_open = ts._open_jump_window
        ts._open_jump_window = lambda cmd: (self.opened.append(cmd) or True)

    def tearDown(self):
        ts._open_jump_window = self._orig_open
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        if self._orig_cfg is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self._orig_cfg
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- fixtures ----------------------------------------------------------------

    def _task(self, title):
        t = ts.new_task(title, "summary")
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _pair(self):
        """An orchestrator and its child."""
        parent = self._task("parent")
        child = self._task("child")
        return parent, child

    def _repo(self, name="main"):
        """A real git repository with one commit, so `worktree add` has a HEAD."""
        path = os.path.join(self.tmp, name)
        os.makedirs(path)
        _git("init", "-q", cwd=path)
        _git("config", "user.email", "t@example.invalid", cwd=path)
        _git("config", "user.name", "Test", cwd=path)
        with open(os.path.join(path, "README"), "w", encoding="utf-8") as f:
            f.write("x\n")
        _git("add", "-A", cwd=path)
        _git("commit", "-qm", "init", cwd=path)
        return os.path.realpath(path)

    def _worktree(self, main, name="wt", branch="feat"):
        """A real LINKED worktree of `main` — what a loop creates moments before it
        invokes into it."""
        path = os.path.join(self.tmp, name)
        _git("worktree", "add", "-q", "-b", branch, path, cwd=main)
        return os.path.realpath(path)

    def _known(self, *paths, trusted=True):
        """Write `~/.claude.json` marking `paths` as projects this machine knows."""
        doc = {"projects": {p: {"hasTrustDialogAccepted": bool(trusted)} for p in paths}}
        with open(self.claude_json, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        return doc

    def _mcp(self, path, *names):
        with open(os.path.join(path, ".mcp.json"), "w", encoding="utf-8") as f:
            json.dump({"mcpServers": {n: {"command": "x"} for n in names}}, f)

    def _local_settings(self, path):
        p = os.path.join(path, ".claude", "settings.local.json")
        if not os.path.exists(p):
            return None
        with open(p, encoding="utf-8") as f:
            return json.load(f)

    def _projects(self):
        if not os.path.exists(self.claude_json):
            return {}
        with open(self.claude_json, encoding="utf-8") as f:
            return (json.load(f) or {}).get("projects") or {}

    # -- running -----------------------------------------------------------------

    def _out(self, fn, args):
        buf = io.StringIO()
        code = None
        with redirect_stdout(buf):
            try:
                fn(args)
            except SystemExit as exc:
                code = exc.code
        return buf.getvalue(), code

    def _invoke(self, **kw):
        parent = kw.pop("parent", None)
        child = kw.pop("child", None)
        args = _Args(task=str(child["seq"]),
                     from_ref=str(parent["seq"]) if parent else None, **kw)
        return self._out(ts.cmd_invoke, args)

    def _events(self, task):
        return [e.get("text") or "" for e in (ts.load_task(task["id"]) or {}).get("events") or []]


# -- (1) a preview costs nothing -------------------------------------------------

class DryRunIsPure(_InvokeTest):
    def test_it_prints_the_command_it_would_run(self):
        parent, child = self._pair()
        main = self._repo()
        wt = self._worktree(main)
        out, code = self._invoke(parent=parent, child=child, cwd=wt, dry_run=True)
        self.assertIsNone(code)
        self.assertIn("cd %s && claude" % wt, out)
        self.assertIn("Land the port.", out)
        self.assertIn("DRY RUN", out)

    def test_it_mints_no_session(self):
        """The bug in one assertion: previewing an invoke used to cost a phantom
        session that would never exist."""
        parent, child = self._pair()
        self._invoke(parent=parent, child=child, dry_run=True)
        again = ts.load_task(child["id"])
        self.assertEqual(again.get("sessions") or [], [])
        self.assertEqual(again.get("session_meta") or {}, {})

    def test_the_printed_session_id_is_a_placeholder_not_a_registered_one(self):
        parent, child = self._pair()
        out, _ = self._invoke(parent=parent, child=child, dry_run=True)
        self.assertIn(ts.DRY_RUN_SID, out)
        self.assertIsNone(ts.get_link(ts.DRY_RUN_SID))

    def test_it_writes_no_event_on_the_child_or_the_orchestrator(self):
        parent, child = self._pair()
        self._invoke(parent=parent, child=child, dry_run=True)
        self.assertEqual(self._events(child), [])
        self.assertEqual(self._events(parent), [])

    def test_it_opens_no_window(self):
        parent, child = self._pair()
        self._invoke(parent=parent, child=child, dry_run=True)
        self.assertEqual(self.opened, [])

    def test_it_touches_neither_the_trust_file_nor_the_workspace_settings(self):
        """A dry run that pre-trusted a directory would be the loudest possible way to
        get 'mutates nothing' wrong."""
        parent, child = self._pair()
        main = self._repo()
        wt = self._worktree(main)
        self._mcp(wt, "task-station")
        self._known(main)
        before = json.dumps(self._projects(), sort_keys=True)
        self._invoke(parent=parent, child=child, cwd=wt, dry_run=True)
        self.assertEqual(json.dumps(self._projects(), sort_keys=True), before)
        self.assertIsNone(self._local_settings(wt))

    def test_it_still_reports_the_workspace_verdict_it_would_act_on(self):
        """Mutating nothing is not the same as saying nothing — the preview's whole
        job is to show what the real invoke would do."""
        parent, child = self._pair()
        main = self._repo()
        wt = self._worktree(main)
        self._mcp(wt, "task-station")
        self._known(main)
        out, _ = self._invoke(parent=parent, child=child, cwd=wt, dry_run=True)
        self.assertIn("would", out.lower())
        self.assertIn("trust", out.lower())


# -- (2) a launch records which kind it was --------------------------------------

class ManualLaunchDistinct(_InvokeTest):
    MANUAL = "MANUAL LAUNCH"

    def test_print_command_records_a_manual_launch_on_both_tasks(self):
        parent, child = self._pair()
        out, _ = self._invoke(parent=parent, child=child, print_command=True)
        self.assertIn("run it yourself", out)
        self.assertTrue(any(self.MANUAL in t for t in self._events(child)))
        self.assertTrue(any(self.MANUAL in t for t in self._events(parent)))

    def test_a_window_launch_records_an_invoke_and_not_a_manual_launch(self):
        parent, child = self._pair()
        self._invoke(parent=parent, child=child)
        self.assertEqual(len(self.opened), 1)
        texts = self._events(parent)
        self.assertTrue(any(t.startswith("invoked #") for t in texts))
        self.assertFalse(any(self.MANUAL in t for t in texts))

    def test_one_preview_and_one_launch_read_as_one_of_each_not_two_invokes(self):
        """The exact trail that was misread on a real board: a child previewed once and
        launched once must never count as two invokes."""
        parent, child = self._pair()
        self._invoke(parent=parent, child=child, print_command=True)
        self._invoke(parent=parent, child=child)
        texts = self._events(parent)
        self.assertEqual(sum(1 for t in texts if t.startswith("invoked #")), 1)
        self.assertEqual(sum(1 for t in texts if self.MANUAL in t), 1)

    def test_a_failed_window_open_records_the_manual_launch_it_actually_is(self):
        """The fallback prints the command for the human to run — that is a manual
        launch by every property that matters, so the trail must not claim a window."""
        ts._open_jump_window = lambda cmd: False
        parent, child = self._pair()
        out, _ = self._invoke(parent=parent, child=child)
        self.assertIn("could not open a window", out)
        texts = self._events(parent)
        self.assertTrue(any(self.MANUAL in t for t in texts))
        self.assertFalse(any(t.startswith("invoked #") for t in texts))

    def test_a_manual_launch_still_pre_attaches_the_session(self):
        """--print-command is a REAL launch path: the human runs the printed line, so
        the session must already be bound to the child or the ask arrives contextless."""
        parent, child = self._pair()
        out, _ = self._invoke(parent=parent, child=child, print_command=True)
        self.assertIn("--session-id", out)
        sid = (ts.load_task(child["id"]).get("sessions") or [None])[0]
        self.assertTrue(sid)
        self.assertEqual(ts.get_link(sid), child["id"])


# -- (3) the first-run gates are cleared -----------------------------------------

class TrustPreseed(_InvokeTest):
    def test_a_fresh_worktree_of_a_known_project_is_pre_trusted(self):
        parent, child = self._pair()
        main = self._repo()
        wt = self._worktree(main)
        self._known(main)
        self.assertNotIn(wt, self._projects())
        self._invoke(parent=parent, child=child, cwd=wt)
        self.assertIs(self._projects().get(wt, {}).get("hasTrustDialogAccepted"), True)

    def test_it_says_so_rather_than_doing_it_silently(self):
        parent, child = self._pair()
        main = self._repo()
        wt = self._worktree(main)
        self._known(main)
        out, _ = self._invoke(parent=parent, child=child, cwd=wt)
        self.assertIn("trust", out.lower())
        self.assertIn(wt, out)

    def test_the_trusted_path_is_the_one_the_child_will_open(self):
        """Trust is keyed by directory: an entry for any other path clears no dialog."""
        parent, child = self._pair()
        main = self._repo()
        wt = self._worktree(main)
        self._known(main)
        out, _ = self._invoke(parent=parent, child=child, cwd=wt)
        self.assertIn("cd %s && claude" % wt, out)
        self.assertIn(wt, self._projects())

    def test_an_already_trusted_workspace_is_left_exactly_as_it_is(self):
        parent, child = self._pair()
        main = self._repo()
        wt = self._worktree(main)
        self._known(main)
        projects = self._projects()
        projects[wt] = {"hasTrustDialogAccepted": True, "history": ["keep me"]}
        with open(self.claude_json, "w", encoding="utf-8") as f:
            json.dump({"projects": projects}, f)
        self._invoke(parent=parent, child=child, cwd=wt)
        self.assertEqual(self._projects()[wt].get("history"), ["keep me"])

    def test_a_missing_claude_json_is_reported_not_invented(self):
        """Its absence means Claude Code has not written one; inventing the file would
        be this plugin guessing at another application's schema."""
        parent, child = self._pair()
        main = self._repo()
        wt = self._worktree(main)
        out, _ = self._invoke(parent=parent, child=child, cwd=wt)
        self.assertFalse(os.path.exists(self.claude_json))
        self.assertIn("did not", out.lower())


# -- (4) and only ever inherited -------------------------------------------------

class TrustGuardRefuses(_InvokeTest):
    def test_a_directory_that_is_not_a_git_repository_is_refused_with_the_reason(self):
        parent, child = self._pair()
        main = self._repo()
        self._known(main)
        plain = os.path.join(self.tmp, "plain")
        os.makedirs(plain)
        out, _ = self._invoke(parent=parent, child=child, cwd=plain)
        self.assertNotIn(plain, self._projects())
        self.assertIn("did not", out.lower())
        self.assertIn("git", out.lower())

    def test_a_worktree_of_an_unknown_project_is_refused_with_the_reason(self):
        """The whole guard in one test: a real worktree, but of a repository this
        machine has never trusted. Inheriting from nothing is not inheriting."""
        parent, child = self._pair()
        known = self._repo("known")
        stranger = self._repo("stranger")
        wt = self._worktree(stranger, name="stranger-wt")
        self._known(known)
        out, _ = self._invoke(parent=parent, child=child, cwd=wt)
        self.assertNotIn(wt, self._projects())
        self.assertIn("did not", out.lower())
        self.assertIn(stranger, out)

    def test_a_main_checkout_is_not_a_worktree_and_is_refused(self):
        """A main checkout has no one to inherit from — the human accepts its dialog
        once, which is what a trust prompt is for."""
        parent, child = self._pair()
        main = self._repo()
        other = self._repo("other")
        self._known(other)
        out, _ = self._invoke(parent=parent, child=child, cwd=main)
        self.assertNotIn(main, self._projects())
        self.assertIn("did not", out.lower())

    def test_a_worktree_whose_main_checkout_is_known_but_untrusted_is_refused(self):
        """Known is not trusted. An entry the human never accepted cannot hand out a
        grant it does not itself hold."""
        parent, child = self._pair()
        main = self._repo()
        wt = self._worktree(main)
        self._known(main, trusted=False)
        self._invoke(parent=parent, child=child, cwd=wt)
        self.assertNotIn(wt, self._projects())

    def test_a_refusal_writes_nothing_at_all(self):
        parent, child = self._pair()
        stranger = self._repo("stranger")
        wt = self._worktree(stranger, name="stranger-wt")
        self._mcp(wt, "task-station")
        self._known(self._repo("known"))
        before = json.dumps(self._projects(), sort_keys=True)
        self._invoke(parent=parent, child=child, cwd=wt)
        self.assertEqual(json.dumps(self._projects(), sort_keys=True), before)
        self.assertIsNone(self._local_settings(wt))

    def test_a_refusal_does_not_stop_the_invoke(self):
        """The guard's answer is 'not automatically', never 'not at all' — a refusal
        that blocked the launch would trade a dialog for a dead end."""
        parent, child = self._pair()
        plain = os.path.join(self.tmp, "plain")
        os.makedirs(plain)
        out, code = self._invoke(parent=parent, child=child, cwd=plain)
        self.assertIsNone(code)
        self.assertEqual(len(self.opened), 1)
        self.assertTrue(any(t.startswith("invoked #") for t in self._events(parent)))


# -- (5) the second gate ---------------------------------------------------------

class McpPreapproval(_InvokeTest):
    def test_the_workspaces_own_servers_are_enabled_in_its_local_settings(self):
        parent, child = self._pair()
        main = self._repo()
        wt = self._worktree(main)
        self._mcp(wt, "task-station", "serena")
        self._known(main)
        self._invoke(parent=parent, child=child, cwd=wt)
        self.assertEqual(self._local_settings(wt).get("enabledMcpjsonServers"),
                         ["serena", "task-station"])

    def test_the_list_is_derived_from_the_workspace_not_copied_from_elsewhere(self):
        """`enumerate the gates from the workspace itself` — a copied list goes stale
        the moment the two repositories differ."""
        parent, child = self._pair()
        main = self._repo()
        self._mcp(main, "something-else")
        wt = self._worktree(main)
        self._mcp(wt, "task-station")
        self._known(main)
        self._invoke(parent=parent, child=child, cwd=wt)
        self.assertEqual(self._local_settings(wt).get("enabledMcpjsonServers"),
                         ["task-station"])

    def test_it_says_which_servers_it_approved(self):
        parent, child = self._pair()
        main = self._repo()
        wt = self._worktree(main)
        self._mcp(wt, "task-station")
        self._known(main)
        out, _ = self._invoke(parent=parent, child=child, cwd=wt)
        self.assertIn("task-station", out)
        self.assertIn("mcp", out.lower())

    def test_a_server_the_human_explicitly_disabled_is_never_re_enabled(self):
        parent, child = self._pair()
        main = self._repo()
        wt = self._worktree(main)
        self._mcp(wt, "task-station", "sketchy")
        self._known(main)
        os.makedirs(os.path.join(wt, ".claude"))
        with open(os.path.join(wt, ".claude", "settings.local.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"disabledMcpjsonServers": ["sketchy"]}, f)
        self._invoke(parent=parent, child=child, cwd=wt)
        settings = self._local_settings(wt)
        self.assertEqual(settings.get("enabledMcpjsonServers"), ["task-station"])
        self.assertEqual(settings.get("disabledMcpjsonServers"), ["sketchy"])

    def test_the_rest_of_an_existing_settings_file_survives(self):
        parent, child = self._pair()
        main = self._repo()
        wt = self._worktree(main)
        self._mcp(wt, "task-station")
        self._known(main)
        os.makedirs(os.path.join(wt, ".claude"))
        with open(os.path.join(wt, ".claude", "settings.local.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"permissions": {"allow": ["Bash(git status:*)"]}}, f)
        self._invoke(parent=parent, child=child, cwd=wt)
        settings = self._local_settings(wt)
        self.assertEqual(settings.get("permissions"), {"allow": ["Bash(git status:*)"]})
        self.assertEqual(settings.get("enabledMcpjsonServers"), ["task-station"])

    def test_a_workspace_declaring_no_servers_gets_no_file(self):
        parent, child = self._pair()
        main = self._repo()
        wt = self._worktree(main)
        self._known(main)
        self._invoke(parent=parent, child=child, cwd=wt)
        self.assertIsNone(self._local_settings(wt))

    def test_it_runs_under_the_same_guard_as_the_trust_preseed(self):
        """Both gates, one guard. An unrelated directory gets neither."""
        parent, child = self._pair()
        stranger = self._repo("stranger")
        wt = self._worktree(stranger, name="stranger-wt")
        self._mcp(wt, "task-station")
        self._known(self._repo("known"))
        self._invoke(parent=parent, child=child, cwd=wt)
        self.assertIsNone(self._local_settings(wt))


# -- (6) the child inherits the parent's permissions -----------------------------

class PermissionInheritance(_InvokeTest):
    def _cmd(self, **kw):
        parent, child = self._pair()
        out, _ = self._invoke(parent=parent, child=child, print_command=True, **kw)
        return out

    def test_an_implementer_is_given_no_permission_mode_at_all(self):
        """acceptEdits REPLACES the human's configured default with something strictly
        less autonomous for commands. Omitting the flag inherits it for free."""
        self.assertNotIn("--permission-mode", self._cmd(role="implementer"))

    def test_the_implementers_model_is_still_applied(self):
        """The deletion is about the permission mode only — a role that stopped
        choosing the model would have lost its reason to exist."""
        self.assertIn("--model opus", self._cmd(role="implementer"))

    def test_a_grader_is_given_no_permission_mode_either(self):
        """`default` is the harness's baseline, which is the absence of an opinion, not
        a restriction."""
        self.assertNotIn("--permission-mode", self._cmd(role="grader"))

    def test_a_restricting_role_still_names_its_mode(self):
        """`plan` is a genuine narrowing — a scout that could edit is not a scout."""
        self.assertIn("--permission-mode plan", self._cmd(role="scout"))
        self.assertIn("--permission-mode plan", self._cmd(role="reviewer"))

    def test_a_human_passing_the_flag_explicitly_always_wins(self):
        out = self._cmd(role="implementer", permission_mode="acceptEdits")
        self.assertIn("--permission-mode acceptEdits", out)

    def test_a_human_can_widen_as_well_as_narrow(self):
        out = self._cmd(role="scout", permission_mode="bypassPermissions")
        self.assertIn("--permission-mode bypassPermissions", out)
        self.assertNotIn("--permission-mode plan", out)

    def test_the_restricting_set_is_a_closed_list_not_a_guess(self):
        self.assertTrue(ws.restricts("plan"))
        for mode in ("acceptEdits", "default", "bypassPermissions", "", None, "nonsense"):
            self.assertFalse(ws.restricts(mode))


if __name__ == "__main__":
    unittest.main()
