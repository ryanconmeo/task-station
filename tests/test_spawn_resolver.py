"""THE SPAWN RESOLVER — one function answers model, context window and permission mode
for every path that starts a session, and the env a child inherits is scrubbed at the
one place that can actually scrub it.

WHY THIS FILE EXISTS. There were two spawners and two answers. `invoke` grew a
considered rule in 3.7.0 — a role may RESTRICT and may never REPLACE, and a bare alias
reclaims the parent's `[1m]` window — and `delegate` kept its own hardcoded pair from
before that rule existed: `--permission-mode acceptEdits` and a bare `sonnet`. Two
copies of one rule is not a duplication smell, it is a correctness bug with a clock on
it: the copies had ALREADY drifted, and `acceptEdits` is the exact mode delegate's own
`--bg` design had ruled out because it HANGS an unattended worker on the first non-edit
prompt. So the rule moves into `board.workspace` beside the two halves 3.7.0 already
shipped there, and both paths ask it.

THE FIVE THINGS PINNED HERE, one class each:

  1. DelegateUsesTheResolver — `_build_worker_cmd` no longer holds its own answer. Its
     permission mode and its model both come back from the resolver, which is proved
     the only way it can be proved: move the resolver's policy and watch delegate's
     output move with it. A test that merely asserts "dontAsk" would still pass against
     a second hardcoded copy that happened to agree today.

  2. BgDesignWins — where the two designs disagree, `--bg`'s wins. The role table says
     an implementer runs `acceptEdits` and a scout runs `plan`; both are wrong for an
     UNATTENDED worker, because both stop and wait for a human who is not there —
     acceptEdits on the first non-edit permission prompt, plan at ExitPlanMode. So a bg
     spawn is `dontAsk` (fail-closed: a non-allowlisted tool is denied, never queued),
     and `bypassPermissions` only when the human turned it on AND the target is inside
     a `-worktrees/` sandbox. The override is RECORDED rather than silent — a design
     that quietly discards a role's stated mode teaches nobody why.

  3. BothPathsAgree — the same role and the same parent produce the same model and the
     same window from both spawners. The permission mode agrees too for the same kind
     of spawn; it differs only across kinds, and that difference is (2) — enumerated,
     not drift.

  4. EnvScrubbedAtSpawn — MEASURED 2026-08-18 on task 549. A window opened by the Apple
     Event inherits the parent session's whole CLAUDE_* set. `CLAUDE_CODE_CHILD_SESSION`
     turns transcript saving OFF, and the parent's session id and messaging socket come
     along, so the child answers to the parent's identity and vanishes from
     `sessions --task` and from ListAgents — leaving the memo ledger as its only
     channel. It fires only when Terminal.app is COLD and the Apple Event launches it,
     which is why it stayed latent on an iTerm daily driver.

     THE DETAIL THAT DECIDES THE FIX: `env=` on `subprocess.Popen` sets the env of the
     `osascript` PROCESS, and Terminal.app is not that process — it receives an Apple
     Event and inherits from whoever launched it. So the unset MUST live INSIDE the
     do-script string, and the tests below assert it in the string that reaches the
     window opener, not in any env mapping. For `delegate`, whose worker IS a direct
     child, `env=` does reach it, so that path scrubs the mapping — same list, same
     function, two transports.

     The list is CLOSED and names only what the harness injects. `CLAUDE_CONFIG_DIR` is
     the human's own config choice and is never touched; scrubbing it would silently
     repoint the child at a different store, which is a worse bug than the one being
     fixed.

  5. TheRoleTableIsReadOnlyHere — 3.12.0 made the role table CONFIG and gave every role
     two more things that decide what a child is: a TOOL GRANT and a REPORT CONTRACT,
     plus the effort the table always carried. Those are role-derived, so by this file's
     own rule they are answered in the resolver and nowhere else — the failure this
     class exists to catch is a second reader of the table growing back inside a
     spawner, which is how the model and the mode drifted the first time. It also pins
     the bg path's STATED LIMIT: it consumes the model and the mode and deliberately
     emits neither the grant nor the contract, which is a documented boundary in
     `_build_worker_cmd` rather than a silent omission.
"""
import importlib.util
import os
import re
import shlex
import sys
import unittest
from unittest import mock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(_REPO_ROOT, "lib")
sys.path.insert(0, LIB)
sys.path.insert(0, os.path.join(LIB, "delegate"))

import pricing                                                          # noqa: E402
from board import loop as _loop                                         # noqa: E402
from board import workspace as ws                                       # noqa: E402
import delegate as dg                                                   # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


ONE_M = "claude-opus-5[1m]"


def _flag(argv, name):
    """The value of `--name` in an argv LIST, or None when the flag is absent."""
    for i, tok in enumerate(argv):
        if tok == name and i + 1 < len(argv):
            return argv[i + 1]
    return None


def _flag_str(cmd, name):
    """The value of `--name` in a shell command STRING (`invoke` builds a string, not
    an argv), or None. Split with shlex so a quoted value survives."""
    return _flag(shlex.split(cmd), name)


class _ResolverTest(unittest.TestCase):
    """Pins the parent's model SELECTION, since every model answer here is relative to
    it. `claude_code_model_selection` reads ANTHROPIC_MODEL first, so setting it is the
    real read path rather than a stub over it."""

    PARENT = ONE_M

    def setUp(self):
        self._saved = os.environ.get("ANTHROPIC_MODEL")
        os.environ["ANTHROPIC_MODEL"] = self.PARENT

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("ANTHROPIC_MODEL", None)
        else:
            os.environ["ANTHROPIC_MODEL"] = self._saved


# ---------------------------------------------------------------------- step 1 ----

class DelegateUsesTheResolver(_ResolverTest):
    """`_build_worker_cmd` asks `board.workspace` instead of answering for itself."""

    def test_the_hardcoded_acceptEdits_is_gone(self):
        """acceptEdits was never right for a headless worker: it auto-approves EDITS
        and then stops dead on the first non-edit prompt, with nobody there to answer."""
        mode = _flag(dg._build_worker_cmd("do the thing"), "--permission-mode")
        self.assertNotEqual(mode, "acceptEdits")
        self.assertEqual(mode, ws.resolve_spawn(ws.SPAWN_BG)["permission_mode"])

    def test_the_bare_alias_reclaims_the_parents_window(self):
        """A worker under a 1M parent used to get a 200k window without anyone asking —
        the same unasked-for downgrade `invoke` already refuses to make."""
        argv = dg._build_worker_cmd("t", model="opus")
        self.assertEqual(_flag(argv, "--model"), ONE_M)

    def test_a_different_family_is_never_inflated(self):
        """A window belongs to the model actually chosen. Borrowing one across families
        would invent a variant that may not exist.

        `sonnet` is also what delegate hardcoded, so asserting the string alone would
        pass against the old code and prove nothing. The claim is that the RESOLVER is
        the one saying it."""
        argv = dg._build_worker_cmd("t", model="sonnet")
        self.assertEqual(_flag(argv, "--model"),
                         ws.resolve_spawn(ws.SPAWN_BG, model="sonnet",
                                          parent_selection=self.PARENT)["model"])
        self.assertEqual(_flag(argv, "--model"), "sonnet")

    def test_no_model_still_omits_the_flag(self):
        """An empty model inherits the account default — the flag's ABSENCE is the
        answer, so the resolver must not invent one."""
        self.assertIsNone(ws.resolve_spawn(ws.SPAWN_BG, model=None)["model"])
        self.assertIsNone(ws.resolve_spawn(ws.SPAWN_BG, model=None)["window"])
        self.assertIsNone(_flag(dg._build_worker_cmd("t", model=None), "--model"))

    def test_a_role_supplies_the_model_when_no_explicit_one_is_given(self):
        spec = _loop.role_spec("scout")
        argv = dg._build_worker_cmd("t", model=None, role="scout")
        self.assertEqual(_flag(argv, "--model"), spec["model"])

    def test_an_explicit_model_still_beats_the_role(self):
        argv = dg._build_worker_cmd("t", model="haiku", role="implementer")
        self.assertEqual(_flag(argv, "--model"), "haiku")

    def test_the_answer_MOVES_when_the_resolver_MOVES(self):
        """The only assertion that can tell one shared rule from two copies that agree
        today. Move the resolver's bg default and delegate must follow it; a surviving
        private copy fails here and nowhere else."""
        with mock.patch.object(ws, "BG_DEFAULT_MODE", "plan"):
            self.assertEqual(
                _flag(dg._build_worker_cmd("t"), "--permission-mode"), "plan")


# ---------------------------------------------------------------------- step 2 ----

class BgDesignWins(_ResolverTest):
    """`--bg`'s permission design outranks the role table wherever they disagree."""

    def test_an_implementers_acceptEdits_is_refused_for_a_bg_spawn(self):
        self.assertEqual(_loop.role_spec("implementer")["permission_mode"], "acceptEdits")
        r = ws.resolve_spawn(ws.SPAWN_BG, role="implementer")
        self.assertEqual(r["permission_mode"], "dontAsk")

    def test_a_scouts_plan_mode_is_refused_too(self):
        """plan RESTRICTS, so `invoke` emits it — but it ends at ExitPlanMode, which is
        a prompt, and an unattended worker parks there forever."""
        self.assertEqual(_loop.role_spec("scout")["permission_mode"], "plan")
        self.assertEqual(
            ws.resolve_spawn(ws.SPAWN_BG, role="scout")["permission_mode"], "dontAsk")

    def test_the_override_is_recorded_not_silent(self):
        r = ws.resolve_spawn(ws.SPAWN_BG, role="implementer")
        self.assertTrue(any("acceptEdits" in n for n in r["notes"]),
                        "the discarded role mode should be named in notes: %r" % (r["notes"],))

    def test_bypass_needs_the_optin_AND_a_worktree(self, ):
        wt = "/Users/x/Repo-worktrees/feature"
        self.assertEqual(
            ws.resolve_spawn(ws.SPAWN_BG, cwd=wt, bypass_allowed=True)["permission_mode"],
            "bypassPermissions")

    def test_bypass_is_refused_outside_a_worktree(self):
        self.assertEqual(
            ws.resolve_spawn(ws.SPAWN_BG, cwd="/Users/x/Repo",
                             bypass_allowed=True)["permission_mode"], "dontAsk")

    def test_bypass_is_refused_when_the_human_has_not_opted_in(self):
        wt = "/Users/x/Repo-worktrees/feature"
        self.assertEqual(
            ws.resolve_spawn(ws.SPAWN_BG, cwd=wt, bypass_allowed=False)["permission_mode"],
            "dontAsk")

    def test_an_unnamed_directory_never_satisfies_the_worktree_gate(self):
        """`cwd=None` means NOBODY NAMED a directory — not 'use whichever directory this
        process happens to be in'. `os.path.abspath("")` is the process cwd, so the
        permissive reading hands `bypassPermissions` to a spawn nobody scoped whenever
        the hub itself is running from inside a worktree, which is most of the time.
        A gate with no input fails closed."""
        self.assertFalse(ws.under_worktrees(None))
        self.assertFalse(ws.under_worktrees(""))
        self.assertEqual(
            ws.resolve_spawn(ws.SPAWN_BG, cwd=None, bypass_allowed=True)["permission_mode"],
            "dontAsk")

    def test_a_bg_spawn_NEVER_omits_the_mode(self):
        """Silence inherits the human's configured default, which for an unattended
        worker could be anything at all. bg always states its mode."""
        for role in [None] + sorted(_loop.roles()):
            self.assertIn(ws.resolve_spawn(ws.SPAWN_BG, role=role)["permission_mode"],
                          ("dontAsk", "bypassPermissions"))

    def test_the_window_path_is_untouched_by_the_bg_rule(self):
        """`invoke`'s answer must not move: acceptEdits does not RESTRICT, so the flag
        is omitted and the human's default inherits."""
        self.assertIsNone(
            ws.resolve_spawn(ws.SPAWN_WINDOW, role="implementer")["permission_mode"])
        self.assertEqual(
            ws.resolve_spawn(ws.SPAWN_WINDOW, role="scout")["permission_mode"], "plan")

    def test_delegates_bg_mode_helper_defers_to_the_resolver(self):
        wt = "/Users/x/Repo-worktrees/feature"
        with mock.patch.object(dg, "_bypass_allowed", return_value=True):
            self.assertEqual(dg._bg_permission_mode(wt), "bypassPermissions")
            self.assertEqual(dg._bg_permission_mode("/Users/x/Repo"), "dontAsk")
        with mock.patch.object(dg, "_bypass_allowed", return_value=False):
            self.assertEqual(dg._bg_permission_mode(wt), "dontAsk")


# ---------------------------------------------------------------------- step 3 ----

class BothPathsAgree(_ResolverTest):
    """The same role and the same parent give the same answer on both spawners."""

    def _invoke_flags(self, role):
        cmd = ts._invoke_command("cd /w && claude --session-id abc", role,
                                 None, None, "go")
        return _flag_str(cmd, "--model"), _flag_str(cmd, "--permission-mode")

    def _delegate_flags(self, role):
        argv = dg._build_worker_cmd("go", model=None, role=role)
        return _flag(argv, "--model"), _flag(argv, "--permission-mode")

    def test_every_role_resolves_to_the_same_model_on_both_paths(self):
        for role in sorted(_loop.roles()):
            with self.subTest(role=role):
                self.assertEqual(self._invoke_flags(role)[0],
                                 self._delegate_flags(role)[0])

    def test_every_role_resolves_to_the_same_window_on_both_paths(self):
        """The window is what the `[1m]` inheritance is FOR, so it is asserted as a
        number rather than left implied by the model string."""
        for role in sorted(_loop.roles()):
            with self.subTest(role=role):
                inv, dele = self._invoke_flags(role)[0], self._delegate_flags(role)[0]
                self.assertEqual(pricing.context_window_for(inv),
                                 pricing.context_window_for(dele))

    def test_each_path_emits_exactly_the_resolvers_answer_for_its_own_kind(self):
        """Neither spawner adjusts the answer after asking for it — an adjustment is
        how the two drifted apart the first time."""
        for role in sorted(_loop.roles()):
            with self.subTest(role=role):
                self.assertEqual(
                    self._invoke_flags(role)[1],
                    ws.resolve_spawn(ws.SPAWN_WINDOW, role=role,
                                     parent_selection=self.PARENT)["permission_mode"])
                self.assertEqual(
                    self._delegate_flags(role)[1],
                    ws.resolve_spawn(ws.SPAWN_BG, role=role,
                                     parent_selection=self.PARENT)["permission_mode"])

    def test_the_only_mode_disagreement_left_is_the_bg_rule(self):
        """Named explicitly so a future third answer cannot hide as 'they differ'."""
        for role in sorted(_loop.roles()):
            with self.subTest(role=role):
                win = ws.resolve_spawn(ws.SPAWN_WINDOW, role=role)["permission_mode"]
                bg = ws.resolve_spawn(ws.SPAWN_BG, role=role)["permission_mode"]
                if win != bg:
                    self.assertIn(bg, ("dontAsk", "bypassPermissions"))

    def test_moving_the_shared_rule_moves_BOTH_paths(self):
        """The structural claim of this task: one copy of the rule, so one edit changes
        every spawner. Widen the restricting set and both must emit the new mode."""
        with mock.patch.object(ws, "RESTRICTING_MODES", frozenset({"plan", "acceptEdits"})):
            self.assertEqual(self._invoke_flags("implementer")[1], "acceptEdits")
            self.assertEqual(
                ws.resolve_spawn(ws.SPAWN_WINDOW, role="implementer")["permission_mode"],
                "acceptEdits")

    def test_neither_path_answers_for_itself(self):
        """Both spawners are routed through `resolve_spawn`; stubbing it must be enough
        to change what each of them emits."""
        fake = {"kind": "x", "role": None, "model": "haiku", "window": 200000,
                "permission_mode": "plan", "notes": []}
        with mock.patch.object(ws, "resolve_spawn", return_value=fake):
            self.assertEqual(self._invoke_flags("implementer"), ("haiku", "plan"))
            self.assertEqual(self._delegate_flags("implementer"), ("haiku", "plan"))


# ---------------------------------------------------------------------- step 4 ----

class EnvScrubbedAtSpawn(_ResolverTest):
    """The child does not inherit the parent session's identity."""

    LEAKED = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION",
              "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN",
              "CLAUDE_CODE_ENTRYPOINT", "CLAUDECODE")

    def test_the_measured_culprit_is_on_the_list(self):
        """CLAUDE_CODE_CHILD_SESSION is the one that turns transcript saving OFF —
        named here so a future trim of the list cannot drop it quietly."""
        for name in self.LEAKED:
            self.assertIn(name, ws.LEAKED_SESSION_ENV)

    def test_the_humans_own_config_is_NOT_scrubbed(self):
        """CLAUDE_CONFIG_DIR is a choice, not a leak. Unsetting it would repoint the
        child at a different store — a worse bug than the one being fixed."""
        self.assertNotIn("CLAUDE_CONFIG_DIR", ws.LEAKED_SESSION_ENV)
        self.assertNotIn("CLAUDE_TTY", ws.LEAKED_SESSION_ENV)

    def test_the_prefix_unsets_every_leaked_name(self):
        prefix = ws.scrub_prefix()
        self.assertTrue(prefix.startswith("unset "))
        for name in ws.LEAKED_SESSION_ENV:
            self.assertIn(name, prefix)

    def test_a_scrubbed_command_still_runs_the_original(self):
        cmd = "cd /w && claude --session-id abc --model opus 'go'"
        out = ws.scrubbed_command(cmd)
        self.assertTrue(out.endswith(cmd))
        self.assertIn("unset ", out)
        self.assertIn("CLAUDE_CODE_CHILD_SESSION", out.split(cmd)[0])

    def test_scrubbing_an_empty_command_is_a_no_op(self):
        self.assertEqual(ws.scrubbed_command(""), "")

    def test_the_unset_reaches_the_window_INSIDE_the_do_script_string(self):
        """THE ASSERTION THIS WHOLE STEP TURNS ON. The string handed to the window
        opener becomes `theCmd` and then `do script theCmd`, so an unset in it runs in
        the new Terminal shell. An env mapping on the osascript process would not, and
        this test would not see one."""
        seen = {}

        class _FakeProc:
            returncode = 0
            stdout = "opened"
            stderr = ""

        class _FakeSub:
            @staticmethod
            def run(argv, **kw):
                seen["argv"] = argv
                seen["env"] = kw.get("env")
                return _FakeProc()

        orig_sub, orig_base = ts.subprocess, ts.BASE
        ts.set_g("subprocess", _FakeSub)
        ts.set_g("BASE", LIB)
        try:
            with mock.patch.object(sys, "platform", "darwin"):
                self.assertTrue(ts._open_jump_window("cd /w && claude --resume abc"))
        finally:
            ts.set_g("subprocess", orig_sub)
            ts.set_g("BASE", orig_base)

        handed = seen["argv"][-1]
        self.assertIn("claude --resume abc", handed)
        for name in ws.LEAKED_SESSION_ENV:
            self.assertIn(name, handed.split("claude --resume")[0])

    def test_scrubbed_env_drops_the_leak_and_keeps_everything_else(self):
        """The OTHER transport: `delegate`'s worker is a direct child, so `env=` does
        reach it and the mapping is what gets scrubbed. Same list, one function."""
        src = {"PATH": "/bin", "CLAUDE_CONFIG_DIR": "/cfg", "HOME": "/h"}
        for name in ws.LEAKED_SESSION_ENV:
            src[name] = "leaked"
        out = ws.scrubbed_env(src)
        for name in ws.LEAKED_SESSION_ENV:
            self.assertNotIn(name, out)
        self.assertEqual(out["PATH"], "/bin")
        self.assertEqual(out["CLAUDE_CONFIG_DIR"], "/cfg")
        self.assertEqual(out["HOME"], "/h")
        self.assertIn("CLAUDE_CODE_SESSION_ID", src,
                      "scrubbed_env must not mutate its argument")

    def test_delegate_hands_its_worker_a_scrubbed_env(self):
        env = dg._worker_env()
        for name in ws.LEAKED_SESSION_ENV:
            self.assertNotIn(name, env)
        self.assertEqual(env.get("TASK_STATION_SUPPRESS"), "1")

    def test_the_scrub_survives_a_command_carrying_quotes(self):
        """A resume one-liner routinely contains a quoted ask with an apostrophe; the
        prefix is plain shell and must not disturb it."""
        cmd = "cd /w && claude --session-id abc 'don'\"'\"'t break'"
        self.assertTrue(ws.scrubbed_command(cmd).endswith(cmd))


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------- step 5 ----

class TheRoleTableIsReadOnlyHere(_ResolverTest):
    """Every role-derived answer comes from `resolve_spawn`, including the three that
    3.12.0 added. A spawner that re-reads the role table is the drift this file exists
    to prevent, so these assert the SOURCE, not just the value."""

    def _invoke(self, role, **kw):
        return ts._invoke_command("cd /w && claude --session-id abc", role,
                                  kw.get("model"), kw.get("permission_mode"), "go",
                                  kw.get("effort"))

    def test_the_resolver_answers_all_three_new_fields(self):
        """A scout is the role that exercises all three: it denies the edit tools, runs
        cheap on purpose, and owes a read-only report."""
        r = ws.resolve_spawn(ws.SPAWN_WINDOW, role="scout")
        self.assertEqual(r["effort"], _loop.roles()["scout"]["effort"])
        self.assertEqual(r["deny_tools"], _loop.roles()["scout"]["deny_tools"])
        self.assertIn("no edits", r["report"])

    def test_no_role_answers_none_rather_than_a_default(self):
        """A spawn with no role emits no `--effort` and no grant. `None`/empty, never an
        invented default — the resolver never decides what a role did not say."""
        r = ws.resolve_spawn(ws.SPAWN_WINDOW)
        self.assertIsNone(r["effort"])
        self.assertIsNone(r["report"])
        self.assertEqual(r["deny_tools"], [])
        self.assertNotIn("--effort", self._invoke(None))
        self.assertNotIn("--disallowed-tools", self._invoke(None))

    def test_invoke_emits_exactly_the_resolvers_answer(self):
        """Same rule as `test_each_path_emits_exactly_the_resolvers_answer_for_its_own
        _kind`, extended to the three new fields: the spawner does not adjust them."""
        for role in sorted(_loop.roles()):
            with self.subTest(role=role):
                r = ws.resolve_spawn(ws.SPAWN_WINDOW, role=role,
                                     parent_selection=self.PARENT)
                cmd = self._invoke(role)
                self.assertEqual(_flag_str(cmd, "--effort"), r["effort"])
                self.assertEqual(_flag_str(cmd, "--disallowed-tools"),
                                 ",".join(r["deny_tools"]) or None)
                self.assertIn(r["report"], cmd)

    def test_moving_the_tables_grant_moves_what_invoke_emits(self):
        """The SOURCE test — retune the effective table and the emitted flag follows. A
        spawner holding its own copy of the grant passes every assertion above and fails
        this one, which is the whole point of writing it this way."""
        retuned = {name: dict(spec) for name, spec in _loop.roles().items()}
        retuned["scout"] = dict(retuned["scout"], deny_tools=["Bash"], effort="low")
        with mock.patch.object(_loop, "roles", return_value=retuned):
            self.assertEqual(_flag_str(self._invoke("scout"), "--disallowed-tools"),
                             "Bash")
            self.assertEqual(_flag_str(self._invoke("scout"), "--effort"), "low")

    def test_an_explicit_effort_beats_the_roles(self):
        """Same precedence as the model and the mode: a human passing the flag has made
        the decision the role was only guessing at."""
        self.assertEqual(_flag_str(self._invoke("scout", effort="max"), "--effort"),
                         "max")

    def test_the_contract_is_appended_and_never_replaces_the_ask(self):
        """The ask is the one thing the orchestrator has to say that the child's own
        record cannot tell it, so a boilerplate sentence must never push it out."""
        cmd = self._invoke("scout")
        self.assertIn("go", shlex.split(cmd)[-1])
        self.assertIn("REPORT BACK", shlex.split(cmd)[-1])

    def test_the_bg_path_consumes_model_and_mode_and_states_its_limit(self):
        """The bg path's documented boundary. It emits the model and the mode; it emits
        NEITHER the grant nor the contract, because its live caller passes no role and
        because it is the one path already sending `--allowedTools`, where a deny list
        raises a precedence question nobody has settled. Pinned so the day that changes
        is a deliberate edit to this test rather than a surprise."""
        argv = dg._build_worker_cmd("go", model=None, role="scout")
        r = ws.resolve_spawn(ws.SPAWN_BG, role="scout", parent_selection=self.PARENT)
        self.assertEqual(_flag(argv, "--model"), r["model"])
        self.assertEqual(_flag(argv, "--permission-mode"), r["permission_mode"])
        self.assertIsNone(_flag(argv, "--disallowed-tools"))
        self.assertNotIn("REPORT BACK", " ".join(argv))
        self.assertIn("deliberately does not emit yet",
                      ws.resolve_spawn.__doc__ or "")
        self.assertIn("emits NEITHER", dg._build_worker_cmd.__doc__ or "")


if __name__ == "__main__":
    unittest.main()
