"""ROLES AS CONFIG, AND THE TWO BUDGETS THE LOOP ACTUALLY ENFORCES.

WHAT THIS COVERS. 3.2.0 shipped the role table as DATA in `lib/loop.py` and taught
`invoke --role` to read it. Data in a source file is not configuration: a station cannot
retune a role, cannot see what the roles are without reading Python, and — the part that
matters for a loop — the two concurrency knobs Q2 decided (`loop_children_max`,
`loop_builds_max`) existed as config keys nothing ever read. A cap nothing reads is a
comment.

  1. THE TABLE IS CONFIG. `roles()` is the effective table: the shipped defaults with
     per-role, per-field overrides from `config.json` merged over them, and it is
     rendered on the `config` board like every other setting. A config table that
     cannot be validated is worse than a constant, because the thing it silently gets
     wrong is a child's PERMISSIONS — so an override naming a field that does not
     exist, or a mode the CLI would reject, falls back to the shipped role and is
     REPORTED rather than half-applied.

  2. A ROLE CARRIES ITS TOOL GRANT AND ITS REPORT CONTRACT, and both reach the child.
     The grant is a DENY list, never an allow list: `--tools`/`--allowed-tools` would
     REPLACE the human's tool set (dropping the MCP servers they configured), and "a
     role may restrict and may never replace" is the same rule 3.7.0 settled for the
     permission mode. The report contract is appended to the child's prompt, because a
     contract nobody is told about is decoration — while the RECORDED ask stays the
     human's request, so the trail does not fill up with boilerplate.

  3. `loop_children_max` IS ENFORCED AT INVOKE TIME. The count is derived from process
     liveness over the orchestrator's own children — the same derivation the scan's
     RUNNING column uses — so a stale record cannot inflate it and a crashed child
     cannot hold a slot forever. Over the cap, `invoke` refuses with exit 3 (retry
     later), distinct from the exit 2 it uses for "you asked wrong", and writes nothing.

  4. `loop_builds_max` IS A MACHINE-WIDE LOCK. It lives in the DATA DIR, not on a task:
     two orchestrators share one machine, and a per-task cap would let them sum to a
     load neither one asked for. A holder whose process is gone is reclaimed, because a
     lock that survives a crash is a machine nobody can build on again.

  5. AND THE REWRITE KEEPS WHAT #541 PROVED. `PreservesInvokeInheritance` is the
     regression guard: the child still inherits the parent's 1M window and the human's
     permission default AFTER the table became config — including down the config path,
     which is the edit that could drop either.

The store, the config file, `~/.claude.json` and the sessions dir are all redirected into
a throwaway home, and the window opener records instead of opening one.
"""
import importlib.util
import io
import json
import os
import re
import shutil
import shlex
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(_REPO_ROOT, "lib")
sys.path.insert(0, LIB)

_TMP_HOME = tempfile.mkdtemp(prefix="ts-roles-")
os.environ["TASK_STATION_HOME"] = _TMP_HOME

import config                                                           # noqa: E402
import live_sessions                                                    # noqa: E402
import loop                                                             # noqa: E402
import pricing                                                          # noqa: E402
import store                                                            # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


class _Args:
    """invoke's argv. Every flag this file is about defaults OFF, so each test states
    the behaviour it means rather than inheriting it."""

    def __init__(self, **kw):
        defaults = dict(session=None, task=None, from_ref=None, ask="Land the port.",
                        role=None, model=None, permission_mode=None, effort=None,
                        cwd=None, print_command=False, dry_run=False, force=False)
        defaults.update(kw)
        self.__dict__.update(defaults)


class _TickArgs:
    """exit-tick's argv."""

    def __init__(self, **kw):
        defaults = dict(session=None, task=None, step=None, dry_run=False,
                        untick=False, timeout=None, build_wait=0)
        defaults.update(kw)
        self.__dict__.update(defaults)


class _RolesTest(unittest.TestCase):
    """A throwaway store + config.json + sessions dir per test, and a window opener
    that records instead of opening one."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="roles-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        store.reset_cache()
        self.sessions = os.path.join(self.tmp, "sessions")
        os.makedirs(self.sessions)
        self._orig_sdir = os.environ.get("TASK_STATION_SESSIONS_DIR")
        os.environ["TASK_STATION_SESSIONS_DIR"] = self.sessions
        # The parent's model SELECTION, pinned to "nothing configured" for every test
        # that is not about it — it is read from the real settings.json, so a developer
        # running a 1M session would otherwise see their own marker in the child's
        # --model. PreservesInvokeInheritance sets it deliberately.
        self._orig_sel = ts.claude_code_model_selection
        ts.claude_code_model_selection = lambda: ""
        self.opened = []
        self._orig_open = ts._open_jump_window
        ts._open_jump_window = lambda cmd: (self.opened.append(cmd) or True)

    def tearDown(self):
        ts._open_jump_window = self._orig_open
        ts.claude_code_model_selection = self._orig_sel
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        if self._orig_sdir is None:
            os.environ.pop("TASK_STATION_SESSIONS_DIR", None)
        else:
            os.environ["TASK_STATION_SESSIONS_DIR"] = self._orig_sdir
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- fixtures ----------------------------------------------------------------

    def _task(self, title, parent=None):
        t = ts.new_task(title, "summary")
        if parent:
            t.setdefault("related", []).append(
                {"kind": "parent", "id": parent["id"], "seq": parent.get("seq")})
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _pair(self):
        parent = self._task("orchestrator")
        return parent, self._task("child", parent=parent)

    def _live(self, task, pid=None):
        """Put a RUNNING session on `task` the way the machine reports one: a
        `<pid>.json` in the sessions dir plus the link that resolves it to the task.
        The real derivation, not a patched count — a fake here would test nothing."""
        pid = os.getpid() if pid is None else pid
        sid = "sid-%s-%s" % (task.get("seq"), pid)
        ts.set_link(sid, task["id"])
        with open(os.path.join(self.sessions, "%s.json" % sid), "w",
                  encoding="utf-8") as fh:
            json.dump({"pid": pid, "sessionId": sid, "cwd": self.tmp,
                       "kind": "hub", "status": "busy"}, fh)
        return sid

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

    def _cmd(self, **kw):
        """The command line one invoke WOULD hand a human."""
        parent, child = self._pair()
        out, _ = self._invoke(parent=parent, child=child, print_command=True, **kw)
        return out

    def _events(self, task):
        return [e.get("text") or ""
                for e in (ts.load_task(task["id"]) or {}).get("events") or []]


# -- (1) the table is CONFIG -----------------------------------------------------

class RolesAreConfig(_RolesTest):
    def test_the_shipped_table_is_what_an_unconfigured_station_gets(self):
        self.assertEqual(loop.roles(), loop.ROLE_DEFAULTS)
        self.assertEqual(loop.role_problems(), [])

    def test_an_override_changes_one_field_and_leaves_the_rest_shipped(self):
        """The merge is per FIELD. A whole-role replacement would make retuning one
        model mean restating the grant and the contract, and a restatement drifts."""
        config.set("roles", {"implementer": {"model": "sonnet"}})
        spec = loop.role_spec("implementer")
        self.assertEqual(spec["model"], "sonnet")
        self.assertEqual(spec["effort"],
                         loop.ROLE_DEFAULTS["implementer"]["effort"])
        self.assertEqual(spec["report"],
                         loop.ROLE_DEFAULTS["implementer"]["report"])

    def test_config_can_add_a_role_the_code_never_shipped(self):
        """The test of whether this is configuration at all: a station can name a role
        of its own without an edit to the plugin."""
        config.set("roles", {"auditor": {"model": "opus", "permission_mode": "plan",
                                         "effort": "high", "deny_tools": ["Write"],
                                         "report": "the findings, nothing else"}})
        self.assertIn("auditor", loop.roles())
        self.assertEqual(loop.role_spec("auditor")["model"], "opus")
        self.assertEqual(loop.role_problems(), [])

    def test_an_added_role_missing_a_required_field_is_dropped_and_said_so(self):
        """Dropped, not defaulted: a role with no permission_mode would inherit
        whatever the code guessed, which is the one thing a role must never do."""
        config.set("roles", {"auditor": {"model": "opus"}})
        self.assertNotIn("auditor", loop.roles())
        self.assertTrue(any("auditor" in p for p in loop.role_problems()))

    def test_an_override_naming_a_field_that_does_not_exist_falls_back_and_is_reported(self):
        """A typo'd field is the dangerous case: `permision_mode` would apply nothing
        and look applied. So the whole override is refused and the shipped role stands."""
        config.set("roles", {"scout": {"permision_mode": "acceptEdits"}})
        self.assertEqual(loop.role_spec("scout"), loop.ROLE_DEFAULTS["scout"])
        self.assertTrue(any("permision_mode" in p for p in loop.role_problems()))

    def test_a_mode_the_cli_would_reject_never_reaches_a_child(self):
        config.set("roles", {"scout": {"permission_mode": "readonly"}})
        self.assertEqual(loop.role_spec("scout")["permission_mode"],
                         loop.ROLE_DEFAULTS["scout"]["permission_mode"])
        self.assertTrue(any("readonly" in p for p in loop.role_problems()))

    def test_an_effort_outside_the_cli_vocabulary_is_refused_too(self):
        config.set("roles", {"scout": {"effort": "extreme"}})
        self.assertEqual(loop.role_spec("scout")["effort"],
                         loop.ROLE_DEFAULTS["scout"]["effort"])
        self.assertTrue(any("extreme" in p for p in loop.role_problems()))

    def test_a_grant_that_is_not_a_list_of_tool_names_is_refused(self):
        config.set("roles", {"scout": {"deny_tools": "Edit"}})
        self.assertEqual(loop.role_spec("scout")["deny_tools"],
                         loop.ROLE_DEFAULTS["scout"]["deny_tools"])
        self.assertTrue(any("deny_tools" in p for p in loop.role_problems()))

    def test_a_roles_key_that_is_not_a_table_leaves_the_shipped_one_standing(self):
        config.set("roles", ["scout"])
        self.assertEqual(loop.roles(), loop.ROLE_DEFAULTS)
        self.assertTrue(loop.role_problems())

    def test_an_unknown_role_is_still_none_rather_than_a_default(self):
        self.assertIsNone(loop.role_spec("implementor"))

    def test_invoke_names_the_configured_roles_when_it_refuses_a_typo(self):
        """The refusal has to list the EFFECTIVE table, or a station's own role would
        be invisible in the very message that tells you what the roles are."""
        config.set("roles", {"auditor": {"model": "opus", "permission_mode": "plan",
                                         "effort": "high", "deny_tools": [],
                                         "report": "the findings"}})
        parent, child = self._pair()
        out, code = self._invoke(parent=parent, child=child, role="auditer")
        self.assertEqual(code, 2)
        self.assertIn("auditor", out)

    def test_the_config_board_renders_the_role_table(self):
        """Step 1's exit condition in test form: `config` prints the roles, so the
        table is visible without reading Python."""
        board = config.render_board()
        self.assertIn("--roles", board)
        for name in loop.ROLE_DEFAULTS:
            self.assertIn(name, board)

    def test_the_board_shows_each_roles_model_grant_and_contract(self):
        """Short tokens only: the board WRAPS its prose to the terminal, so asserting a
        long sentence here would be asserting the width of whoever ran the suite."""
        board = config.render_board()
        self.assertIn(loop.ROLE_DEFAULTS["scout"]["model"], board)
        self.assertIn("denies", board)
        self.assertIn("Edit", board)                      # the grant it denies
        self.assertIn("reports", board)                   # the contract it carries

    def test_the_board_reports_a_broken_override_instead_of_hiding_it(self):
        config.set("roles", {"scout": {"permission_mode": "readonly"}})
        self.assertIn("readonly", config.render_board())

    def test_the_two_concurrency_budgets_are_on_the_board_too(self):
        """A cap that governs a loop and appears nowhere is a cap nobody can find."""
        board = config.render_board()
        self.assertIn("loop_children_max", board)
        self.assertIn("loop_builds_max", board)

    def test_a_reset_clears_a_retuned_role_table(self):
        """Every board-managed key is popped by a factory reset; a role table left
        behind would keep governing a station the user had just cleared."""
        self.assertIn("roles", config.RESET_KEYS)


# -- (2) the grant and the contract ----------------------------------------------

class RoleContract(_RolesTest):
    def test_every_role_carries_a_tool_grant_and_a_report_contract(self):
        for name in loop.roles():
            spec = loop.role_spec(name)
            self.assertIsInstance(spec["deny_tools"], list, name)
            self.assertTrue(str(spec["report"] or "").strip(), name)

    def test_a_read_only_role_denies_the_writing_tools_on_the_command_line(self):
        out = self._cmd(role="scout")
        self.assertIn("--disallowed-tools Edit,Write,NotebookEdit", out)

    def test_the_implementer_denies_nothing_and_so_emits_no_tool_flag(self):
        """The worker has to edit. A grant that fired here would be the role deciding
        against the whole reason it exists."""
        self.assertEqual(loop.role_spec("implementer")["deny_tools"], [])
        self.assertNotIn("--disallowed-tools", self._cmd(role="implementer"))

    def test_a_grant_never_replaces_the_humans_tool_set(self):
        """`--tools` / `--allowed-tools` would drop the MCP servers the human
        configured. A grant may narrow and may never replace, exactly as with the
        permission mode."""
        for role in loop.ROLE_DEFAULTS:
            out = self._cmd(role=role)
            self.assertNotIn("--tools", out)
            self.assertNotIn("--allowed-tools", out)

    def test_no_role_means_no_tool_flag_at_all(self):
        self.assertNotIn("--disallowed-tools", self._cmd())

    def test_a_configured_grant_is_the_one_that_is_emitted(self):
        config.set("roles", {"scout": {"deny_tools": ["Write"]}})
        out = self._cmd(role="scout")
        self.assertIn("--disallowed-tools Write", out)
        self.assertNotIn("Edit", out)

    def test_the_report_contract_reaches_the_child_in_its_prompt(self):
        out = self._cmd(role="reviewer")
        self.assertIn(loop.ROLE_DEFAULTS["reviewer"]["report"], out)

    def test_the_ask_survives_the_contract_verbatim(self):
        """The contract is added to the request, never instead of it."""
        self.assertIn("Land the port.", self._cmd(role="reviewer"))

    def test_no_role_means_no_contract(self):
        out = self._cmd()
        self.assertNotIn("REPORT BACK", out)

    def test_a_configured_contract_is_the_one_the_child_is_given(self):
        config.set("roles", {"scout": {"report": "one paragraph, no files"}})
        self.assertIn("one paragraph, no files", self._cmd(role="scout"))

    def test_the_recorded_ask_is_the_humans_request_not_the_contract(self):
        """The trail is a record of what was asked. Boilerplate in it would push the
        actual request out of the 160 characters the event keeps."""
        parent, child = self._pair()
        self._invoke(parent=parent, child=child, role="reviewer")
        texts = self._events(child)
        self.assertTrue(any("Land the port." in t for t in texts))
        self.assertFalse(any("REPORT BACK" in t for t in texts))

    def test_the_roles_effort_reaches_the_child(self):
        """The table carries an effort per role — cheap scout, high-effort worker — and
        the CLI takes one. A field the table shows and nothing applies is a lie the
        config board would tell every time it rendered."""
        self.assertIn("--effort medium", self._cmd(role="scout"))
        self.assertIn("--effort high", self._cmd(role="implementer"))

    def test_a_human_naming_the_effort_wins(self):
        self.assertIn("--effort low", self._cmd(role="scout", effort="low"))

    def test_no_role_names_no_effort(self):
        self.assertNotIn("--effort", self._cmd())


# -- (3) the children cap --------------------------------------------------------

class ChildrenCap(_RolesTest):
    def _budget(self, orch, tasks, live):
        return loop.children_budget(orch, tasks, live)

    def test_the_cap_is_the_configured_number(self):
        self.assertEqual(config.loop_children_max(), 3)
        config.set("loop_children_max", 1)
        self.assertEqual(config.loop_children_max(), 1)

    def test_the_budget_counts_only_the_orchestrators_own_running_children(self):
        parent = self._task("orchestrator")
        a = self._task("a", parent=parent)
        b = self._task("b", parent=parent)
        stranger = self._task("stranger")
        every = ts.all_tasks()
        report = self._budget(parent, every, {a["seq"], stranger["seq"]})
        self.assertEqual(report["running"], [a["seq"]])
        self.assertNotIn(b["seq"], report["running"])
        self.assertFalse(report["over"])

    def test_the_budget_is_over_once_the_cap_is_reached(self):
        config.set("loop_children_max", 2)
        parent = self._task("orchestrator")
        a = self._task("a", parent=parent)
        b = self._task("b", parent=parent)
        every = ts.all_tasks()
        self.assertFalse(self._budget(parent, every, {a["seq"]})["over"])
        self.assertTrue(self._budget(parent, every, {a["seq"], b["seq"]})["over"])

    def test_an_invoke_over_the_cap_is_refused_with_the_holders_named(self):
        config.set("loop_children_max", 1)
        parent = self._task("orchestrator")
        busy = self._task("busy", parent=parent)
        child = self._task("child", parent=parent)
        self._live(busy)
        out, code = self._invoke(parent=parent, child=child)
        self.assertEqual(code, 3)
        self.assertIn("#%s" % busy["seq"], out)
        self.assertIn("loop_children_max", out)

    def test_the_refusal_writes_nothing_at_all(self):
        """Not a session, not an event. A refused invoke that had already minted a
        session would leave a child looking invoked and running nowhere."""
        config.set("loop_children_max", 1)
        parent = self._task("orchestrator")
        busy = self._task("busy", parent=parent)
        child = self._task("child", parent=parent)
        self._live(busy)
        self._invoke(parent=parent, child=child)
        again = ts.load_task(child["id"])
        self.assertEqual(again.get("sessions") or [], [])
        self.assertEqual(self._events(child), [])
        self.assertEqual(self._events(parent), [])
        self.assertEqual(self.opened, [])

    def test_under_the_cap_the_invoke_proceeds(self):
        config.set("loop_children_max", 2)
        parent = self._task("orchestrator")
        busy = self._task("busy", parent=parent)
        child = self._task("child", parent=parent)
        self._live(busy)
        out, code = self._invoke(parent=parent, child=child)
        self.assertIsNone(code)
        self.assertEqual(len(self.opened), 1)

    def test_a_dead_child_session_never_holds_a_slot(self):
        """The count is process liveness, not a record — a crashed child that still
        looks attached would spend a slot nobody can get back."""
        config.set("loop_children_max", 1)
        parent = self._task("orchestrator")
        busy = self._task("busy", parent=parent)
        child = self._task("child", parent=parent)
        self._live(busy, pid=2 ** 30)          # a pid that cannot exist
        out, code = self._invoke(parent=parent, child=child)
        self.assertIsNone(code)

    def test_force_overrides_the_cap_and_records_that_it_did(self):
        """A deliberate override is sometimes right; an invisible one never is."""
        config.set("loop_children_max", 1)
        parent = self._task("orchestrator")
        busy = self._task("busy", parent=parent)
        child = self._task("child", parent=parent)
        self._live(busy)
        out, code = self._invoke(parent=parent, child=child, force=True)
        self.assertIsNone(code)
        self.assertIn("loop_children_max", out)
        self.assertTrue(any("over the cap" in t.lower() or "forced" in t.lower()
                            for t in self._events(parent)))

    def test_a_dry_run_reports_the_cap_instead_of_pretending_it_would_launch(self):
        config.set("loop_children_max", 1)
        parent = self._task("orchestrator")
        busy = self._task("busy", parent=parent)
        child = self._task("child", parent=parent)
        self._live(busy)
        out, code = self._invoke(parent=parent, child=child, dry_run=True)
        self.assertEqual(code, 3)
        self.assertEqual(self._events(child), [])

    def test_an_invoke_with_no_orchestrator_is_not_capped(self):
        """The cap is a property of a LOOP. With no `--from` there is no sibling set to
        count, and inventing one would refuse a bare invoke for a reason nobody set."""
        child = self._task("child")
        every = ts.all_tasks()
        self.assertFalse(self._budget(None, every, {child["seq"]})["over"])


# -- (4) the machine-wide build lock ---------------------------------------------

class MachineWideBuildLock(_RolesTest):
    def tearDown(self):
        for h in loop.build_slot_holders():
            loop.release_build_slot(h["token"])
        super().tearDown()

    def test_the_lock_lives_in_the_data_dir_not_on_a_task(self):
        """Two orchestrators share one machine. A per-task lock would let them sum to a
        load neither asked for, which is the failure this replaces."""
        path = loop.build_slots_path()
        self.assertTrue(path.startswith(self.tmp))
        self.assertNotIn("tasks", path)

    def test_two_orchestrators_contend_for_the_one_slot(self):
        first = loop.acquire_build_slot("suite A")
        self.assertTrue(first)
        self.assertIsNone(loop.acquire_build_slot("suite B"))

    def test_releasing_hands_the_slot_to_the_next_orchestrator(self):
        first = loop.acquire_build_slot("suite A")
        self.assertTrue(loop.release_build_slot(first))
        second = loop.acquire_build_slot("suite B")
        self.assertTrue(second)

    def test_the_capacity_is_the_configured_number(self):
        config.set("loop_builds_max", 2)
        self.assertTrue(loop.acquire_build_slot("one"))
        self.assertTrue(loop.acquire_build_slot("two"))
        self.assertIsNone(loop.acquire_build_slot("three"))

    def test_a_holder_whose_process_is_gone_is_reclaimed(self):
        """A lock that survives a crash is a machine nobody can build on again."""
        self.assertTrue(loop.acquire_build_slot("crashed", pid=2 ** 30))
        self.assertTrue(loop.acquire_build_slot("live one"))

    def test_a_holder_says_who_has_the_slot(self):
        loop.acquire_build_slot("tests.test_roles")
        holders = loop.build_slot_holders()
        self.assertEqual(len(holders), 1)
        self.assertEqual(holders[0]["label"], "tests.test_roles")
        self.assertEqual(holders[0]["pid"], os.getpid())

    def test_releasing_a_token_nobody_holds_is_harmless(self):
        self.assertFalse(loop.release_build_slot("not-a-token"))

    def test_the_slot_is_free_again_after_exit_tick_has_run(self):
        task = self._task("child")
        task["steps"] = [{"text": "prove it", "done": False,
                          "exit": {"cmd": "printf OK", "expect": ["OK"]}}]
        ts.save_task(task)
        out, code = self._out(ts.cmd_exit_tick, _TickArgs(task=str(task["seq"])))
        self.assertIn("1/1 met", out)
        self.assertEqual(loop.build_slot_holders(), [])

    def test_exit_tick_runs_nothing_while_another_build_holds_the_slot(self):
        """The suite is the build. Two of them on one machine is the OOM and the
        load-dependent flake this cap exists for, so the second one waits or says so —
        it never runs anyway and calls the result red."""
        loop.acquire_build_slot("the other orchestrator")
        task = self._task("child")
        task["steps"] = [{"text": "prove it", "done": False,
                          "exit": {"cmd": "printf OK", "expect": ["OK"]}}]
        ts.save_task(task)
        out, code = self._out(ts.cmd_exit_tick, _TickArgs(task=str(task["seq"])))
        self.assertEqual(code, 3)
        self.assertIn("the other orchestrator", out)
        again = ts.load_task(task["id"])
        self.assertNotIn("last", (again["steps"][0].get("exit") or {}))

    def test_a_scan_that_reruns_conditions_takes_the_slot_too(self):
        """`scan --run` is the loop's other build path. Locking one and not the other
        would leave the cap true only of the verb somebody remembered."""
        loop.acquire_build_slot("the other orchestrator")
        task = self._task("child")
        task["steps"] = [{"text": "prove it", "done": False,
                          "exit": {"cmd": "printf OK", "expect": ["OK"]}}]
        ts.save_task(task)
        out, code = self._out(ts.cmd_scan, _TickArgs(task=str(task["seq"]), run=True,
                                                    all=False, as_json=False,
                                                    depth=None))
        self.assertEqual(code, 3)
        again = ts.load_task(task["id"])
        self.assertNotIn("last", (again["steps"][0].get("exit") or {}))


# -- (6) the regression guard over the rewrite -----------------------------------

class PreservesInvokeInheritance(_RolesTest):
    """#541's two inheritance rules, re-asserted AFTER the table became config — and
    down the CONFIG path, which is the edit that could silently drop either.

    This lives here rather than in `test_invoke_hardening` deliberately: a guard that
    reused the existing file would have passed before this rewrite existed, and a guard
    that passes without the change it guards is not a guard.
    """

    ONE_M = "claude-opus-5[1m]"

    def _sel(self, value):
        ts.claude_code_model_selection = lambda: value

    def _flag(self, model):
        return "--model %s" % shlex.quote(model)

    def _model_of(self, out):
        """The model the printed command line actually names, unquoted."""
        m = re.search(r"--model (\S+)", out)
        self.assertIsNotNone(m, "no --model in:\n%s" % out)
        return shlex.split(m.group(1))[0]

    def test_a_role_still_inherits_the_parents_1m_window(self):
        self._sel(self.ONE_M)
        self.assertIn(self._flag(self.ONE_M), self._cmd(role="implementer"))

    def test_a_role_configured_at_the_station_inherits_it_too(self):
        """The config path is the new one, so it is the one that could lose the window.
        A station retuning the scout onto the parent's family must get the parent's
        window with it — the inheritance reads the EFFECTIVE model, not the shipped one."""
        self._sel(self.ONE_M)
        config.set("roles", {"scout": {"model": "opus"}})
        self.assertIn(self._flag(self.ONE_M), self._cmd(role="scout"))

    def test_a_different_family_is_still_never_inflated(self):
        self._sel(self.ONE_M)
        out = self._cmd(role="scout")
        self.assertIn("--model sonnet", out)
        self.assertNotIn(self._flag(self.ONE_M), out)

    def test_the_window_it_inherits_is_the_one_the_marker_means(self):
        """Pinned to the NUMBER, not the spelling of the marker, so a future id that
        carries 1M differently still has to be right. The role alias on its own is a
        200k window; what the child is given is the parent's."""
        self._sel(self.ONE_M)
        self.assertEqual(
            pricing.context_window_for(loop.role_spec("implementer")["model"]),
            pricing.DEFAULT_CONTEXT_WINDOW)
        self.assertEqual(pricing.context_window_for(self._model_of(
            self._cmd(role="implementer"))), pricing.LARGE_CONTEXT_WINDOW)

    def test_acceptEdits_is_still_never_emitted(self):
        """It REPLACES the human's default with something strictly less autonomous.
        Silence inherits, which is the point."""
        self.assertNotIn("--permission-mode", self._cmd(role="implementer"))

    def test_a_restricting_mode_is_still_emitted(self):
        self.assertIn("--permission-mode plan", self._cmd(role="scout"))

    def test_a_mode_that_became_restricting_by_config_is_emitted(self):
        """Proves the emission decision reads the EFFECTIVE table rather than a
        constant the rewrite left behind."""
        config.set("roles", {"implementer": {"permission_mode": "plan"}})
        self.assertIn("--permission-mode plan", self._cmd(role="implementer"))

    def test_a_config_override_cannot_widen_a_child_by_accident(self):
        """The rule is not "emit what config says" — it is "emit only a narrowing".
        A station setting acceptEdits on a scout gets silence, and silence inherits."""
        config.set("roles", {"scout": {"permission_mode": "acceptEdits"}})
        self.assertNotIn("--permission-mode", self._cmd(role="scout"))

    def test_a_human_flag_still_wins_over_the_configured_role(self):
        config.set("roles", {"scout": {"model": "haiku"}})
        out = self._cmd(role="scout", model="opus", permission_mode="bypassPermissions")
        self.assertIn("--model opus", out)
        self.assertIn("--permission-mode bypassPermissions", out)

    def test_no_role_and_no_model_still_names_no_model_at_all(self):
        self._sel(self.ONE_M)
        self.assertNotIn("--model", self._cmd())


if __name__ == "__main__":
    unittest.main()
