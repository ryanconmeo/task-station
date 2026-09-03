"""A BAD cwd MUST NOT OUTLIVE THE SPAWN THAT MADE IT.

WHAT THIS COVERS. `invoke` opens the child's window in a directory it usually INFERS:
"where the task's most recent session ran". MEASURED 2026-08-27 on task #569, that
inference has two failure modes and they compound:

  1. A SESSION THAT DIED AT ZERO TURNS NEVER RAN ANYWHERE. Its roster entry records
     where a spawn was AIMED, not where work happened. Inheriting it hands the retry
     the exact condition that killed its predecessor, so retrying can never recover.
     Two sessions on #569's roster (e8b15efb, fa5f4956) are that same spawn twice.

  2. THE INFERENCE WAS NEVER CHECKED. An invoke run from a scratchpad directory
     defaulted the child into it; a plain directory has no trust to inherit, so the
     child stopped on a first-run dialog no loop can answer and exited at 0 turns.

  3. AND THE ROSTER COULD NOT TELL ANYONE. "gone" is the word for a session that was
     here and left. A spawn that never produced a session at all got the same word, so
     one spawn bug hit twice read as two children failing — which is a different
     diagnosis leading to a different fix.

The fixtures build REAL git repositories and REAL linked worktrees, because "can a
child start here" is answered by git plus `~/.claude.json` and a faked answer would
test nothing.
"""
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(_REPO_ROOT, "lib")
sys.path.insert(0, LIB)

_TMP_HOME = tempfile.mkdtemp(prefix="ts-spawncwd-")
os.environ["TASK_STATION_HOME"] = _TMP_HOME

import pricing                                                          # noqa: E402
import store                                                            # noqa: E402
from board import workspace as ws                                       # noqa: E402

# Everything else is reached through the FACADE, not through `board.*` directly: the
# board modules read their routed globals late-bound out of `task-station.py`'s
# namespace, so a module imported around the facade sees an unbound one and dies on the
# first `g(...)`. The facade is the supported entry point and the suite uses it.
_spec = importlib.util.spec_from_file_location(
    "task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)

# Fixed and in the PAST, deliberately: `spawn_failed` compares against the real clock
# unless a `now` is passed, and a future timestamp would put every fixture inside the
# grace period and quietly assert nothing.
NOW = 1_700_000_000.0


class _Args:
    def __init__(self, **kw):
        defaults = dict(session=None, task=None, from_ref=None, ask="Land the port.",
                        role=None, model=None, permission_mode=None, cwd=None,
                        print_command=False, dry_run=False)
        defaults.update(kw)
        self.__dict__.update(defaults)


def _git(*args, cwd=None):
    try:
        return subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True,
                              text=True, timeout=60)
    except Exception:
        return None


def _have_git():
    r = _git("--version")
    return bool(r and r.returncode == 0)


# -- (1) the classifier: did this session ever actually run? ---------------------

class SessionRan(unittest.TestCase):
    """`session_ran` is the whole propagation fix in one predicate, so each of its
    three signals is pinned separately rather than through the caller."""

    def test_first_turn_at_is_the_authoritative_signal(self):
        m = {"cwd": "/w", "ts": NOW, "spawned_at": NOW, "first_turn_at": NOW}
        self.assertTrue(ts.session_ran("s", m))

    def test_a_spawn_that_never_reported_for_duty_did_not_run(self):
        """The exact shape `fresh_resume_command` writes and nothing else touches:
        ts and spawned_at from two `_now()` calls microseconds apart."""
        m = {"cwd": "/scratch", "ts": NOW, "spawned_at": NOW + 0.000_001,
             "preborn": True, "role": "hub"}
        self.assertFalse(ts.session_ran("s", m))

    def test_ts_advanced_past_spawned_at_means_it_reported_for_duty(self):
        """The signal that reads entries predating `first_turn_at`: `touch` rewrites
        `ts` when a session attaches, so a real gap means it came up."""
        m = {"cwd": "/w", "ts": NOW + 29.0, "spawned_at": NOW, "preborn": True}
        self.assertTrue(ts.session_ran("s", m))

    def test_microseconds_of_drift_is_not_a_turn(self):
        m = {"cwd": "/w", "ts": NOW + 0.5, "spawned_at": NOW, "preborn": True}
        self.assertFalse(ts.session_ran("s", m))

    def test_an_entry_with_no_recorded_spawn_is_taken_at_its_word(self):
        """Nothing minted it, so there is no phantom to suspect — refusing to believe
        an ordinary attach would be the guard firing on the case it was never about."""
        m = {"cwd": "/w", "ts": NOW}
        self.assertTrue(ts.session_ran("s", m))


# -- (2) a spawn failure is not a child failure ----------------------------------

class SpawnFailedIsItsOwnState(unittest.TestCase):
    def _dead(self, **kw):
        m = {"cwd": "/scratch", "ts": NOW, "spawned_at": NOW, "preborn": True,
             "role": "hub"}
        m.update(kw)
        return m

    def test_minted_never_ran_and_past_the_grace_period(self):
        self.assertTrue(ts.spawn_failed("s", self._dead(),
                                               now=NOW + ts.SPAWN_GRACE_S + 1))

    def test_a_window_still_coming_up_is_not_a_failure(self):
        """"Not yet" is not "never" — a window takes seconds, and calling that a
        failure would put a red mark on every healthy spawn for two minutes."""
        self.assertFalse(ts.spawn_failed("s", self._dead(), now=NOW + 5))

    def test_a_session_that_ran_is_never_a_spawn_failure_however_it_ended(self):
        m = self._dead(first_turn_at=NOW + 3)
        self.assertFalse(ts.spawn_failed("s", m, now=NOW + 9_999))

    def test_an_entry_the_board_did_not_mint_is_not_judged_at_all(self):
        """Only the paths that open a window set `preborn`. Anything else has no spawn
        for this to be a failure OF."""
        m = self._dead()
        m.pop("preborn")
        self.assertFalse(ts.spawn_failed("s", m, now=NOW + 9_999))


# -- (3) the default never inherits a directory nothing ran in -------------------

class FreshSessionCwd(unittest.TestCase):
    def test_a_no_turn_session_is_skipped_for_the_one_that_actually_ran(self):
        """THE PROPAGATION FIX. Newest-first would pick the dead scratchpad; the fix
        falls through to the session that took a turn."""
        meta = {
            "ran": {"cwd": "/repo/worktree", "ts": NOW, "spawned_at": NOW - 30,
                    "first_turn_at": NOW - 25, "preborn": True},
            "dead": {"cwd": "/scratch", "ts": NOW + 10, "spawned_at": NOW + 10.000_001,
                     "preborn": True},
        }
        self.assertEqual(ts._fresh_session_cwd(meta), "/repo/worktree")

    def test_two_dead_spawns_in_a_row_still_do_not_poison_the_third(self):
        """The measured shape: one real session, then the SAME bad directory recorded
        twice by two failed spawns."""
        meta = {
            "ran": {"cwd": "/repo/worktree", "ts": NOW, "first_turn_at": NOW},
            "dead1": {"cwd": "/scratch", "ts": NOW + 10, "spawned_at": NOW + 10.000_001,
                      "preborn": True},
            "dead2": {"cwd": "/scratch", "ts": NOW + 20, "spawned_at": NOW + 20.000_001,
                      "preborn": True},
        }
        self.assertEqual(ts._fresh_session_cwd(meta), "/repo/worktree")

    def test_the_measured_569_roster_never_hands_back_the_scratchpad(self):
        """Verbatim from #569's session_meta on 2026-08-27, timestamps and all — the
        regression this task exists for, asserted against the real bytes."""
        scratch = ("/private/tmp/claude-501/-Users-ryannguyen/"
                   "d17d2df5-7760-4a8a-8f91-b6b65cff6ded/scratchpad")
        meta = {
            "e8b15efb": {"cwd": scratch, "ts": 1787850896.892965, "role": "hub",
                         "ordinal": 0, "spawned_at": 1787850896.8929658,
                         "preborn": True},
            "fa5f4956": {"cwd": scratch, "ts": 1787850915.381876, "role": "hub",
                         "ordinal": 1, "spawned_at": 1787850915.381877,
                         "preborn": True},
            "fb8af410": {"cwd": "/Users/ryannguyen", "ts": 1787850974.082915,
                         "role": "hub", "spawned_at": 1787850945.165241,
                         "ordinal": 2, "preborn": True},
        }
        self.assertEqual(ts._fresh_session_cwd(meta), "/Users/ryannguyen")

    def test_when_nothing_ever_ran_it_falls_back_to_the_process_cwd(self):
        """And does NOT hand back a dead entry as a last resort — the process cwd is at
        least somewhere a human is standing, and the guard checks it either way."""
        meta = {"dead": {"cwd": "/scratch", "ts": NOW, "spawned_at": NOW + 0.000_001,
                         "preborn": True}}
        self.assertEqual(ts._fresh_session_cwd(meta), os.getcwd())

    def test_an_empty_roster_is_the_process_cwd(self):
        self.assertEqual(ts._fresh_session_cwd({}), os.getcwd())


# -- (4) the roster says which kind of failure it was ----------------------------

class RosterNamesTheSpawnFailure(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="spawn-roster-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        self.proj = os.path.join(self.tmp, "projects")
        os.makedirs(self.proj, exist_ok=True)
        ts.PROJECTS_ROOT = self.proj
        ts.DELEGATE_REGISTRY = os.path.join(self.tmp, "workers.json")

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _task(self, meta):
        return {"id": "t", "seq": 570, "title": "t", "session_meta": meta,
                "sessions": list(meta)}

    def _lines(self, meta):
        return "\n".join(ts._session_block_lines(self._task(meta)))

    def test_a_dead_spawn_reads_as_a_spawn_failure_and_not_as_gone(self):
        out = self._lines({"e8b15efb": {
            "cwd": "/scratch", "ts": NOW, "spawned_at": NOW, "preborn": True,
            "role": "hub", "ordinal": 0}})
        self.assertIn("SPAWN FAILED", out)
        self.assertIn("never ran", out)
        self.assertNotIn("gone", out)

    def test_an_ordinary_missing_transcript_still_reads_as_gone(self):
        """The distinction only pays if the OTHER state survives it: a session that was
        here and left is still `gone`, and calling that a spawn failure would trade one
        wrong diagnosis for another."""
        out = self._lines({"aaaaaaaa": {
            "cwd": "/repo", "ts": NOW, "first_turn_at": NOW, "role": "hub",
            "ordinal": 0}})
        self.assertIn("gone", out)
        self.assertNotIn("SPAWN FAILED", out)

    def test_the_two_states_are_distinguishable_on_one_roster(self):
        out = self._lines({
            "aaaaaaaa": {"cwd": "/repo", "ts": NOW + 5, "first_turn_at": NOW + 5,
                         "role": "hub", "ordinal": 0},
            "e8b15efb": {"cwd": "/scratch", "ts": NOW, "spawned_at": NOW,
                         "preborn": True, "role": "hub", "ordinal": 1},
        })
        self.assertIn("SPAWN FAILED", out)
        self.assertIn("gone", out)
        self.assertEqual(out.count("SPAWN FAILED"), 1)


# -- (5) can a child actually start here? ----------------------------------------

class _WorkspaceTest(unittest.TestCase):
    def setUp(self):
        if not _have_git():
            self.skipTest("git is unavailable")
        self.tmp = tempfile.mkdtemp(prefix="spawn-cwd-")
        self.cfg = os.path.join(self.tmp, "cfg")
        os.makedirs(self.cfg)
        self._orig_cfg = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = self.cfg
        self.claude_json = os.path.join(self.cfg, ".claude.json")

    def tearDown(self):
        if self._orig_cfg is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self._orig_cfg
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _repo(self, name="main"):
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
        path = os.path.join(self.tmp, name)
        _git("worktree", "add", "-q", "-b", branch, path, cwd=main)
        return os.path.realpath(path)

    def _known(self, *paths, trusted=True):
        doc = {"projects": {}}
        if os.path.exists(self.claude_json):
            with open(self.claude_json, encoding="utf-8") as f:
                doc = json.load(f) or {"projects": {}}
        for p in paths:
            doc.setdefault("projects", {})[p] = {"hasTrustDialogAccepted": bool(trusted)}
        with open(self.claude_json, "w", encoding="utf-8") as f:
            json.dump(doc, f)


class Spawnable(_WorkspaceTest):
    def test_an_already_trusted_directory_passes_without_needing_a_repo(self):
        """`assess` says no to a plain directory and is right to — it is about what may
        be PRE-SEEDED. A directory the human already accepted needs no seeding, and
        conflating the two questions would refuse every trusted home directory."""
        plain = os.path.join(self.tmp, "home")
        os.makedirs(plain)
        self._known(os.path.realpath(plain))
        r = ws.spawnable(plain)
        self.assertTrue(r["ok"])
        self.assertEqual(r["how"], ws.SPAWN_TRUSTED)

    def test_a_worktree_of_a_trusted_main_checkout_passes_as_inheritable(self):
        main = self._repo()
        wt = self._worktree(main)
        self._known(main)
        r = ws.spawnable(wt)
        self.assertTrue(r["ok"])
        self.assertEqual(r["how"], ws.SPAWN_INHERITABLE)

    def test_a_scratchpad_directory_is_refused_with_the_reason(self):
        """The measured cause: a plain directory has nothing to inherit trust from and
        is not trusted itself, so a child dies there at zero turns."""
        self._known(self._repo())
        scratch = os.path.join(self.tmp, "scratchpad")
        os.makedirs(scratch)
        r = ws.spawnable(scratch)
        self.assertFalse(r["ok"])
        self.assertIsNone(r["how"])
        self.assertIn("git", r["reason"].lower())

    def test_an_untrusted_main_checkout_is_refused(self):
        self._known(self._repo("other"))
        r = ws.spawnable(self._repo("stranger"))
        self.assertFalse(r["ok"])

    def test_no_directory_at_all_is_a_no_and_not_a_silent_yes(self):
        self.assertFalse(ws.spawnable(None)["ok"])

    def test_the_refusal_names_the_flag_that_fixes_it(self):
        scratch = os.path.join(self.tmp, "scratchpad")
        os.makedirs(scratch)
        text = "\n".join(ws.spawn_refusal_lines(ws.spawnable(scratch)))
        self.assertIn("--cwd", text)
        self.assertIn("REFUSED", text)
        self.assertIn(os.path.realpath(scratch), text)

    def test_the_refusal_says_when_the_directory_was_inherited(self):
        scratch = os.path.join(self.tmp, "scratchpad")
        os.makedirs(scratch)
        r = ws.spawnable(scratch)
        first = ws.spawn_refusal_lines(r, inherited=True)[0]
        self.assertIn("inherited from an earlier session", first)
        self.assertIn("the directory this command was run from",
                      ws.spawn_refusal_lines(r, inherited=False)[0])


# -- (6) invoke refuses rather than silently defaulting --------------------------

class _InvokeTest(_WorkspaceTest):
    def setUp(self):
        super(_InvokeTest, self).setUp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        store.reset_cache()
        self._orig_sel = ts.claude_code_model_selection
        ts.claude_code_model_selection = lambda: ""
        self.opened = []
        self._orig_open = ts._open_jump_window
        # SINCE #605 `invoke` WAITS for the launched session to register before it claims
        # a window opened, so a fake opener that only records the command would turn every
        # launch here into an UNCONFIRMED one (a manual launch on the trail) and make each
        # pay the 60s production wait. These tests are not about the confirmation: the fake
        # registers the session a real `claude --session-id <sid>` would, and the wait is
        # cut to something a test can afford.
        self.sessions_dir = os.path.join(self.tmp, "sessions")
        os.makedirs(self.sessions_dir, exist_ok=True)
        self._orig_sdir = os.environ.get("TASK_STATION_SESSIONS_DIR")
        self._orig_confirm = os.environ.get("TASK_STATION_SPAWN_CONFIRM_S")
        os.environ["TASK_STATION_SESSIONS_DIR"] = self.sessions_dir
        os.environ["TASK_STATION_SPAWN_CONFIRM_S"] = "0.2"
        ts._open_jump_window = self._fake_open

    def _fake_open(self, cmd):
        """Record the command AND register the session it launches (#605)."""
        self.opened.append(cmd)
        m = re.search(r"--session-id[= ]([0-9a-fA-F-]{8,})", cmd or "")
        if m:
            pid = os.getpid()
            with open(os.path.join(self.sessions_dir, "%d.json" % pid), "w") as f:
                json.dump({"pid": pid, "sessionId": m.group(1), "cwd": self.tmp,
                           "kind": "interactive", "entrypoint": "cli",
                           "status": "busy", "startedAt": 1000, "updatedAt": 1000}, f)
        return True

    def tearDown(self):
        ts._open_jump_window = self._orig_open
        ts.claude_code_model_selection = self._orig_sel
        for _n, _o in (("TASK_STATION_SESSIONS_DIR", self._orig_sdir),
                       ("TASK_STATION_SPAWN_CONFIRM_S", self._orig_confirm)):
            if _o is None:
                os.environ.pop(_n, None)
            else:
                os.environ[_n] = _o
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        super(_InvokeTest, self).tearDown()

    def _task(self, title):
        t = ts.new_task(title, "summary")
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _pair(self):
        return self._task("parent"), self._task("child")

    def _invoke(self, parent, child, **kw):
        args = _Args(task=str(child["seq"]), from_ref=str(parent["seq"]), **kw)
        buf, code = io.StringIO(), None
        with redirect_stdout(buf):
            try:
                ts.cmd_invoke(args)
            except SystemExit as exc:
                code = exc.code
        return buf.getvalue(), code

    def _events(self, task):
        return [e.get("text") or ""
                for e in (ts.load_task(task["id"]) or {}).get("events") or []]

    def _roster(self, task, cwd, **kw):
        """Put one session on `task`'s roster in `cwd`, dead or alive."""
        t = ts.load_task(task["id"])
        m = {"cwd": cwd, "ts": NOW, "role": "hub", "spawned_at": NOW - 30,
             "first_turn_at": NOW - 25, "preborn": True, "ordinal": 0}
        m.update(kw)
        t.setdefault("session_meta", {})["deadbeef-0000"] = m
        t.setdefault("sessions", []).append("deadbeef-0000")
        ts.save_task(t)
        return ts.load_task(task["id"])


class InvokeRefusesABadDefault(_InvokeTest):
    def test_an_untrusted_default_is_refused_rather_than_launched_into(self):
        parent, child = self._pair()
        scratch = os.path.join(self.tmp, "scratchpad")
        os.makedirs(scratch)
        self._roster(child, scratch)
        out, code = self._invoke(parent, child)
        self.assertEqual(code, 3)
        self.assertIn("REFUSED", out)
        self.assertIn(os.path.realpath(scratch), out)
        self.assertIn("inherited from an earlier session", out)

    def test_running_invoke_FROM_a_scratchpad_is_refused_too(self):
        """The first half of the measured incident: no roster to inherit from at all,
        so the default fell through to the process cwd — which was a scratchpad, and
        the child died there at zero turns."""
        parent, child = self._pair()
        self._known(self._repo())
        scratch = os.path.join(self.tmp, "scratchpad")
        os.makedirs(scratch)
        here = os.getcwd()
        try:
            os.chdir(scratch)
            out, code = self._invoke(parent, child)
        finally:
            os.chdir(here)
        self.assertEqual(code, 3)
        self.assertIn("REFUSED", out)
        self.assertIn("the directory this command was run from", out)
        self.assertEqual(self.opened, [])

    def test_the_refusal_writes_nothing_and_opens_nothing(self):
        """A refusal that minted the session anyway would leave the roster claiming a
        child that was never launched — the same phantom, one layer down."""
        parent, child = self._pair()
        scratch = os.path.join(self.tmp, "scratchpad")
        os.makedirs(scratch)
        self._roster(child, scratch)
        before = sorted((ts.load_task(child["id"]).get("session_meta") or {}))
        self._invoke(parent, child)
        after = ts.load_task(child["id"])
        self.assertEqual(sorted(after.get("session_meta") or {}), before)
        self.assertEqual(self.opened, [])
        self.assertEqual(self._events(parent), [])
        self.assertEqual(self._events(child), [])

    def test_a_trusted_default_still_launches(self):
        """The guard has to be narrow or it is just an outage: the ordinary case is a
        directory the human already trusts, and it must be untouched."""
        parent, child = self._pair()
        main = self._repo()
        wt = self._worktree(main)
        self._known(main, wt)
        self._roster(child, wt, first_turn_at=NOW)
        out, code = self._invoke(parent, child)
        self.assertIsNone(code)
        self.assertEqual(len(self.opened), 1)
        self.assertIn(wt, out)

    def test_an_explicit_cwd_is_a_human_decision_and_is_not_refused(self):
        """The existing contract, pinned here because the new guard must not quietly
        take it away: `--cwd` names a directory, the trust verdict is REPORTED, and the
        launch proceeds."""
        parent, child = self._pair()
        self._known(self._repo())
        plain = os.path.join(self.tmp, "plain")
        os.makedirs(plain)
        out, code = self._invoke(parent, child, cwd=plain)
        self.assertIsNone(code)
        self.assertEqual(len(self.opened), 1)
        self.assertIn("did not", out.lower())

    def test_a_dry_run_reaches_the_same_verdict_and_still_writes_nothing(self):
        parent, child = self._pair()
        scratch = os.path.join(self.tmp, "scratchpad")
        os.makedirs(scratch)
        self._roster(child, scratch)
        before = sorted((ts.load_task(child["id"]).get("session_meta") or {}))
        out, code = self._invoke(parent, child, dry_run=True)
        self.assertEqual(code, 3)
        self.assertIn("REFUSED", out)
        self.assertEqual(sorted((ts.load_task(child["id"]).get("session_meta") or {})),
                         before)
        self.assertEqual(self.opened, [])


class InvokeDoesNotInheritADeadDirectory(_InvokeTest):
    def test_the_whole_bug_end_to_end(self):
        """#570 in one test. The task's NEWEST session is a spawn that never ran, in a
        directory no child can start in; an older session actually worked in a trusted
        worktree. Before the fix the retry inherited the scratchpad and died the same
        way. After it, the invoke opens where the work is."""
        parent, child = self._pair()
        main = self._repo()
        wt = self._worktree(main)
        self._known(main, wt)
        scratch = os.path.join(self.tmp, "scratchpad")
        os.makedirs(scratch)
        t = ts.load_task(child["id"])
        t["session_meta"] = {
            "worked-0000": {"cwd": wt, "ts": NOW, "spawned_at": NOW - 30,
                            "first_turn_at": NOW - 25, "preborn": True, "role": "hub",
                            "ordinal": 0},
            "deadone-0000": {"cwd": scratch, "ts": NOW + 10,
                             "spawned_at": NOW + 10.000_001, "preborn": True,
                             "role": "hub", "ordinal": 1},
        }
        t["sessions"] = ["worked-0000", "deadone-0000"]
        ts.save_task(t)
        out, code = self._invoke(parent, child)
        self.assertIsNone(code)
        self.assertIn("cd %s && claude" % wt, out)
        self.assertNotIn(scratch, out)

    def test_the_roster_entry_records_where_the_window_actually_opens(self):
        """An entry that recorded one directory while the window opened in another is
        how a bad cwd gets manufactured out of a GOOD launch — the next spawn reads
        this field."""
        parent, child = self._pair()
        main = self._repo()
        wt = self._worktree(main)
        self._known(main, wt)
        self._invoke(parent, child, cwd=wt)
        meta = ts.load_task(child["id"]).get("session_meta") or {}
        self.assertEqual([m.get("cwd") for m in meta.values()], [wt])


if __name__ == "__main__":
    unittest.main()
