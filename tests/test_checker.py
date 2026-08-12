"""The CHECKER — goal drift, the local pointer check, and claims.

WHAT THIS COVERS AND WHY IT IS SEPARATE FROM test_heal.py. Every check in `heal` works by
cross-referencing two things a task already holds: prose against structure, a memo against
its target, a recorded path against the filesystem. That model has a structural blind spot
— a goal condition NOBODY HAS WORKED ON contradicts nothing, so there is no inconsistency
to find. Measured on one real task: a release condition sat with nothing completed against
it for fifteen days while every surface reported the task healthy.

`lib/checker.py` asks the two questions that blind spot leaves open, both at session
start, both cheap:

  * GOAL DRIFT — map completed checklist steps onto the goal's numbered DONE conditions
    and report a condition nothing has touched in N days.
  * THE LOCAL POINTER CHECK — every path, symlink, linked-worktree branch and bound
    document the task DECLARES still resolves.

Plus CLAIMS: a document bound to the task and the shell commands that settle what it
asserts, so a plan can be checked instead of believed.

THE FOUR INVARIANTS EVERY TEST HERE IS ULTIMATELY ABOUT, because `heal` shipped the
cry-wolf bug four separate times and each one cost a whole check's credibility:

  1. FAIL OPEN — a raising check yields silence, never a broken session start.
  2. SILENT WHEN HEALTHY — no "all clear" line, ever.
  3. UNCOUNTABLE IS NEVER ZERO — no baseline means CANNOT BE COUNTED. A step ticked
     before completion stamps existed must never read as twenty-thousand-day-old drift.
  4. NO GIT SUBPROCESS ON THE SESSION-START PATH, and NO CLAIM COMMAND there ever.

The pointer fixtures build REAL files — a real dangling symlink, a real linked-worktree
`.git` file with a real `gitdir:`/`HEAD`/`commondir`/`packed-refs` chain — because the
whole point of that check is that it reads those files by hand instead of shelling out to
git, and a mocked filesystem would not prove the parsing.

Isolation copies the `_repoint` idiom from tests/test_heal.py.
"""
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(_REPO_ROOT, "lib")
sys.path.insert(0, LIB)

_TMP_HOME = tempfile.mkdtemp(prefix="ts-checker-")
os.environ["TASK_STATION_HOME"] = _TMP_HOME

import checker                # noqa: E402
import config as ts_config    # noqa: E402
import heal                   # noqa: E402
import steps                  # noqa: E402
import store                  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)

DAY = 86400

# The real-world goal shape this whole module keys off: one line, then numbered DONE
# conditions. Taken from the convention the live store actually uses.
GOAL = ("Land the checker so the plan checks itself. "
        "DONE = (1) the scrub landed and nothing references the retired names; "
        "(2) version 3.0.0 shipped to the marketplace; "
        "(3) the checker module reports goal drift at session start; "
        "(4) every claim in the plan document verifies green")


class _Args:
    def __init__(self, **kw):
        defaults = dict(session=None, source="startup", task=None, action="show",
                        bind=None, unbind=False, register=None, replace=False,
                        remove=None, id=None, timeout=None)
        defaults.update(kw)
        self.__dict__.update(defaults)


def _repoint(tmp):
    os.environ["TASK_STATION_HOME"] = tmp
    ts.DATA = tmp
    ts.STORE = os.path.join(tmp, "store")
    ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
    ts.LINKS_DIR = os.path.join(ts.STORE, "links")
    store.reset_cache()


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="checker-test-")
        _repoint(self.tmp)

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _task(self, title="A task", **fields):
        t = ts.new_task(title, "summary")
        t.update(fields)
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _active(self, goal=GOAL, **fields):
        """An ACTIVE task carrying the numbered-DONE goal — the only shape goal drift
        ever evaluates."""
        return self._task(status=ts.STATUS_ACTIVE, goal=goal, **fields)

    def _reload(self, t):
        return ts.load_task(t["id"])

    def _out(self, fn, args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            fn(args)
        return buf.getvalue()

    def _step(self, text, done=False, ts_=None, superseded=False):
        """A stored step element, with the completion stamp under our control."""
        s = {"text": text, "done": done}
        if done and ts_ is not None:
            s[steps.DONE_TS_FIELD] = ts_
        if superseded:
            s["superseded"] = True
        return s

    def _touched(self, age_days):
        """`goal_touched` as `heal.stamp_goal_touched` writes it, aged."""
        return {"ts": time.time() - age_days * DAY, "decisions": 0}


# ---------------------------------------------------------------------------
# (a) the DONE-condition grammar. A NUMBERED MARKER IS THE STRUCTURE GATE.
# ---------------------------------------------------------------------------

class TestDoneConditions(unittest.TestCase):
    def test_parses_the_equals_form(self):
        got = checker.done_conditions(GOAL)
        self.assertEqual([c["n"] for c in got], [1, 2, 3, 4])
        self.assertIn("3.0.0", got[1]["text"])

    def test_parses_the_colon_form(self):
        got = checker.done_conditions("Ship it. DONE: (1) the tests pass; (2) it deployed")
        self.assertEqual([c["n"] for c in got], [1, 2])
        self.assertEqual(got[0]["text"], "the tests pass")

    def test_no_done_marker_means_no_conditions(self):
        # Silence for the whole drift check on this task, and that is correct.
        self.assertEqual(checker.done_conditions("Ship the thing by Friday"), [])

    def test_a_done_marker_with_no_numbered_segments_means_no_conditions(self):
        # This is the discrimination that matters: the module must NEVER guess conditions
        # out of free prose. A guessed condition drifts on a schedule of its own.
        self.assertEqual(
            checker.done_conditions("DONE = the scrub landed and 3.0.0 shipped"), [])

    def test_prose_about_done_conditions_is_not_a_marker(self):
        self.assertEqual(
            checker.done_conditions("The DONE conditions are still being drafted"), [])

    def test_lowercase_done_is_just_the_english_word(self):
        self.assertEqual(checker.done_conditions("what done: looks like is unclear"), [])

    def test_semicolons_inside_a_condition_are_kept(self):
        # Splitting on punctuation would shatter one condition into three and then report
        # two fragments as separate drifting requirements.
        got = checker.done_conditions(
            "DONE = (1) the scrub landed; nothing references the old names; "
            "(2) it shipped")
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0]["text"],
                         "the scrub landed; nothing references the old names")

    def test_an_empty_segment_is_dropped(self):
        got = checker.done_conditions("DONE = (1) ; (2) it shipped")
        self.assertEqual([c["n"] for c in got], [2])

    def test_numbers_are_reported_as_written(self):
        # They are the labels the reader sees in the goal line. Renumbering would
        # silently repoint every reference somebody had in hand.
        got = checker.done_conditions("DONE = (2) second; (5) fifth")
        self.assertEqual([c["n"] for c in got], [2, 5])

    def test_a_repeated_number_does_not_collide(self):
        got = checker.done_conditions("DONE = (1) first; (1) also first")
        self.assertEqual(len(got), 2)

    def test_blank_and_missing_goals_are_silent(self):
        for value in (None, "", "   "):
            self.assertEqual(checker.done_conditions(value), [])

    def test_a_year_in_parentheses_is_not_a_condition_marker(self):
        got = checker.done_conditions("DONE = (1) shipped in (2026) as planned")
        self.assertEqual(len(got), 1)


# ---------------------------------------------------------------------------
# (b) attribution. NOTHING IS FORCE-ASSIGNED.
# ---------------------------------------------------------------------------

class TestAttribution(_Base):
    def test_a_step_lands_on_its_best_matching_condition(self):
        t = self._active(steps=[
            self._step("ship version 3.0.0 to the marketplace", done=True, ts_=1.0)])
        per, unattributed = checker.attribute(t)
        self.assertEqual(unattributed, [])
        self.assertEqual([len(rows) for rows in per], [0, 1, 0, 0])

    def test_a_step_is_credited_to_exactly_one_condition(self):
        # Crediting all plausible matches would let one step silence three requirements.
        t = self._active(steps=[
            self._step("the scrub landed and the retired names are gone",
                       done=True, ts_=1.0)])
        per, _un = checker.attribute(t)
        self.assertEqual(sum(len(rows) for rows in per), 1)

    def test_a_step_below_the_bar_is_unattributed_never_rounded_in(self):
        t = self._active(steps=[self._step("buy milk", done=True, ts_=1.0)])
        per, unattributed = checker.attribute(t)
        self.assertEqual([len(rows) for rows in per], [0, 0, 0, 0])
        self.assertEqual([r["index"] for r in unattributed], [1])

    def test_an_unticked_step_is_not_progress(self):
        t = self._active(steps=[
            self._step("ship version 3.0.0 to the marketplace", done=False)])
        per, unattributed = checker.attribute(t)
        self.assertEqual(sum(len(rows) for rows in per), 0)
        self.assertEqual(unattributed, [])

    def test_a_superseded_done_step_is_not_progress(self):
        # A retired step describes work the plan WITHDREW. Counting it would credit the
        # goal for work nobody did — the lie the supersede verb exists to avoid.
        t = self._active(steps=[
            self._step("ship version 3.0.0 to the marketplace", done=True, ts_=1.0,
                       superseded=True)])
        per, unattributed = checker.attribute(t)
        self.assertEqual(sum(len(rows) for rows in per), 0)
        self.assertEqual(unattributed, [])

    def test_the_step_indices_are_the_original_ones(self):
        t = self._active(steps=[
            self._step("retired", done=True, ts_=1.0, superseded=True),
            self._step("ship version 3.0.0 to the marketplace", done=True, ts_=2.0)])
        per, _un = checker.attribute(t)
        self.assertEqual(per[1][0]["index"], 2)

    def test_a_goal_with_no_conditions_attributes_nothing(self):
        t = self._active(goal="just ship it",
                         steps=[self._step("shipped", done=True, ts_=1.0)])
        per, unattributed = checker.attribute(t)
        self.assertEqual(per, [])
        self.assertEqual(unattributed, [])


# ---------------------------------------------------------------------------
# (c) the completion stamp — additive, symmetric, round-trips.
# ---------------------------------------------------------------------------

class TestCompletionStamp(_Base):
    def test_ticking_stamps_when(self):
        rows = [{"text": "a", "done": False}]
        ok, err = steps.set_done(rows, 1, True, now=1234.5)
        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertEqual(rows[0][steps.DONE_TS_FIELD], 1234.5)

    def test_unticking_drops_the_stamp(self):
        # Leaving it would have the record assert a completion moment for work that is no
        # longer claimed as done.
        rows = [{"text": "a", "done": False}]
        steps.set_done(rows, 1, True, now=1234.5)
        steps.set_done(rows, 1, False)
        self.assertNotIn(steps.DONE_TS_FIELD, rows[0])
        self.assertEqual(rows[0], {"text": "a", "done": False})

    def test_re_ticking_writes_a_fresh_stamp(self):
        rows = [{"text": "a", "done": False}]
        steps.set_done(rows, 1, True, now=100.0)
        steps.set_done(rows, 1, False)
        steps.set_done(rows, 1, True, now=200.0)
        self.assertEqual(rows[0][steps.DONE_TS_FIELD], 200.0)

    def test_an_unticked_step_is_stored_exactly_as_before(self):
        # The frozen-format guarantee: the additive key appears on a TICK and nowhere else.
        rows = [{"text": "a", "done": False}]
        self.assertEqual(steps.compact(steps.as_rich(rows[0])),
                         {"text": "a", "done": False})

    def test_the_stamp_survives_the_compact_round_trip(self):
        rows = [{"text": "a", "done": False}]
        steps.set_done(rows, 1, True, now=1234.5)
        again = steps.compact(steps.as_rich(rows[0]))
        self.assertEqual(again[steps.DONE_TS_FIELD], 1234.5)
        self.assertEqual(again, {"text": "a", "done": True, "done_ts": 1234.5})

    def test_the_stamp_survives_supersede_and_restore(self):
        rows = [{"text": "a", "done": False}]
        steps.set_done(rows, 1, True, now=1234.5)
        steps.mark_superseded(rows, 1)
        self.assertEqual(rows[0][steps.DONE_TS_FIELD], 1234.5)
        steps.restore(rows, 1)
        self.assertEqual(rows[0][steps.DONE_TS_FIELD], 1234.5)

    def test_the_stamp_survives_the_store(self):
        t = self._task(steps=[{"text": "write the checker", "done": False}])
        ts.set_step_done(t, 1, True)
        ts.save_task(t)
        stored = self._reload(t)["steps"][0]
        self.assertTrue(stored["done"])
        self.assertGreater(stored[steps.DONE_TS_FIELD], 0)

    def test_a_done_step_with_no_stamp_reads_as_unknown_not_as_1970(self):
        # The whole no-baseline rule in one accessor.
        self.assertIsNone(checker.done_ts({"text": "a", "done": True}))
        self.assertIsNone(checker.done_ts({"text": "a", "done": True, "done_ts": 0}))
        self.assertIsNone(checker.done_ts({"text": "a", "done": True, "done_ts": "soon"}))
        self.assertEqual(checker.done_ts({"text": "a", "done": True, "done_ts": 5.0}), 5.0)

    def test_refusing_a_superseded_step_still_refuses(self):
        rows = [{"text": "a", "done": False}]
        steps.mark_superseded(rows, 1)
        ok, err = steps.set_done(rows, 1, True)
        self.assertFalse(ok)
        self.assertIn("off the active checklist", err)
        self.assertNotIn(steps.DONE_TS_FIELD, rows[0])


# ---------------------------------------------------------------------------
# (d) goal drift. UNCOUNTABLE IS NEVER ZERO.
# ---------------------------------------------------------------------------

class TestGoalDrift(_Base):
    def test_no_goal_touched_and_no_steps_is_uncountable_and_silent(self):
        # THE COMMON CASE for every task that predates the baseline. Reporting it as
        # drift would put a false alarm on the whole board the day this shipped.
        t = self._active()
        self.assertEqual(checker.goal_drift(t), {})
        rows = checker.condition_states(t)
        self.assertEqual({r["state"] for r in rows}, {checker.UNCOUNTABLE})

    def test_an_untouched_condition_drifts_from_the_goal_write_moment(self):
        # The fifteen-day case: nothing has completed against it since it was written.
        t = self._active(goal_touched=self._touched(15))
        result = checker.goal_drift(t)
        self.assertTrue(result)
        self.assertEqual(len(result["drifted"]), 4)
        self.assertEqual(result["drifted"][0]["from"], checker.FROM_GOAL)
        self.assertEqual(result["drifted"][0]["days"], 15)
        self.assertEqual(result["drifted"][0]["state"], checker.ESCALATED)

    def test_a_condition_with_only_unstamped_done_steps_is_uncountable(self):
        # Work demonstrably happened and there is no honest way to say when. Borrowing
        # the goal stamp here would date the condition to BEFORE work we can see landed.
        t = self._active(goal_touched=self._touched(30),
                         steps=[self._step("ship version 3.0.0 to the marketplace",
                                           done=True)])
        rows = {r["n"]: r for r in checker.condition_states(t)}
        self.assertEqual(rows[2]["state"], checker.UNCOUNTABLE)
        self.assertEqual(rows[2]["unstamped"], 1)
        self.assertIsNone(rows[2]["last_ts"])
        # …and it is not in the drift report, while the untouched ones are.
        result = checker.goal_drift(t)
        self.assertNotIn(2, [r["n"] for r in result["drifted"]])
        self.assertIn(2, result["uncountable"])

    def test_a_stamped_recent_completion_is_fresh_and_silent(self):
        now = time.time()
        t = self._active(goal_touched=self._touched(30), steps=[
            self._step("the scrub landed and the retired names are gone",
                       done=True, ts_=now - 1 * DAY),
            self._step("ship version 3.0.0 to the marketplace",
                       done=True, ts_=now - 1 * DAY),
            self._step("the checker module reports goal drift at session start",
                       done=True, ts_=now - 1 * DAY),
            self._step("every claim in the plan document verifies green",
                       done=True, ts_=now - 1 * DAY)])
        self.assertEqual(checker.goal_drift(t, now=now), {})

    def test_a_mix_of_stamped_and_unstamped_counts_from_the_stamped_one(self):
        now = time.time()
        t = self._active(goal_touched=self._touched(30), steps=[
            self._step("ship version 3.0.0 to the marketplace", done=True),
            self._step("shipped version 3.0.0 to the marketplace again",
                       done=True, ts_=now - 1 * DAY)])
        rows = {r["n"]: r for r in checker.condition_states(t, now=now)}
        self.assertEqual(rows[2]["state"], checker.FRESH)
        self.assertEqual(rows[2]["from"], checker.FROM_STEP)
        self.assertEqual(rows[2]["unstamped"], 1)

    def test_the_report_threshold_is_inclusive_and_the_escalation_is_not(self):
        now = time.time()
        base = dict(goal="DONE = (1) the parser handles nested markers")
        two = self._active(goal_touched={"ts": now - 2 * DAY}, **base)
        three = self._active(goal_touched={"ts": now - 3 * DAY}, **base)
        seven = self._active(goal_touched={"ts": now - 7 * DAY}, **base)
        eight = self._active(goal_touched={"ts": now - 8 * DAY}, **base)
        self.assertEqual(checker.goal_drift(two, now=now), {})
        self.assertEqual(
            checker.condition_states(three, now=now)[0]["state"], checker.DRIFTED)
        self.assertEqual(
            checker.condition_states(seven, now=now)[0]["state"], checker.DRIFTED)
        self.assertEqual(
            checker.condition_states(eight, now=now)[0]["state"], checker.ESCALATED)

    def test_the_thresholds_are_injectable(self):
        now = time.time()
        t = self._active(goal="DONE = (1) the parser handles nested markers",
                         goal_touched={"ts": now - 2 * DAY})
        self.assertEqual(checker.goal_drift(t, now=now), {})
        result = checker.goal_drift(t, now=now, report=1, escalate=30)
        self.assertEqual(result["drifted"][0]["state"], checker.DRIFTED)
        self.assertEqual(result["report_days"], 1)

    def test_the_thresholds_are_config_tunable(self):
        now = time.time()
        t = self._active(goal="DONE = (1) the parser handles nested markers",
                         goal_touched={"ts": now - 2 * DAY})
        self.assertEqual(checker.goal_drift(t, now=now), {})
        ts_config.set("checker_report_days", 1)
        try:
            self.assertEqual(checker.report_days(), 1)
            self.assertTrue(checker.goal_drift(t, now=now))
        finally:
            ts_config.unset("checker_report_days")

    def test_a_nonsense_threshold_falls_back_to_the_default(self):
        # A zero would put every condition on every active task into the nag at once.
        for bad in (0, -3, "soon"):
            ts_config.set("checker_report_days", bad)
            try:
                self.assertEqual(checker.report_days(), checker.REPORT_DAYS)
            finally:
                ts_config.unset("checker_report_days")

    def test_the_env_escape_wins_over_config(self):
        ts_config.set("checker_escalate_days", 20)
        os.environ["TASK_STATION_CHECKER_ESCALATE_DAYS"] = "9"
        try:
            self.assertEqual(checker.escalate_days(), 9)
        finally:
            os.environ.pop("TASK_STATION_CHECKER_ESCALATE_DAYS", None)
            ts_config.unset("checker_escalate_days")

    def test_the_thresholds_are_cleared_by_a_config_reset(self):
        for key in ("checker_report_days", "checker_escalate_days",
                    "checker_claim_timeout"):
            self.assertIn(key, ts_config.RESET_KEYS)

    def test_the_defaults_are_the_documented_ones(self):
        self.assertEqual((checker.report_days(), checker.escalate_days(),
                          checker.claim_timeout()), (3, 7, 600))

    def test_only_an_active_task_is_evaluated(self):
        # heal.due's rule: an open (parked) task is not being worked on, so "nothing
        # completed in five days" describes parking, not drift.
        for status in (ts.STATUS_OPEN, ts.STATUS_CLOSED):
            t = self._task(status=status, goal=GOAL, goal_touched=self._touched(15))
            self.assertEqual(checker.goal_drift(t), {})

    def test_a_goal_with_no_numbered_conditions_is_silent(self):
        t = self._active(goal="ship the checker", goal_touched=self._touched(90))
        self.assertEqual(checker.goal_drift(t), {})
        self.assertEqual(checker.condition_states(t), [])

    def test_a_garbled_goal_touched_is_uncountable_not_drifted(self):
        for snap in ({"ts": "yesterday"}, {"ts": 0}, {}, "yes", None):
            t = self._active(goal_touched=snap)
            self.assertEqual(checker.goal_drift(t), {})

    def test_the_result_carries_every_condition_not_just_the_drifting_ones(self):
        now = time.time()
        t = self._active(goal_touched=self._touched(15), steps=[
            self._step("the scrub landed and the retired names are gone",
                       done=True, ts_=now - 1 * DAY)])
        result = checker.goal_drift(t, now=now)
        self.assertEqual(len(result["conditions"]), 4)
        self.assertEqual(len(result["drifted"]), 3)

    def test_the_unattributed_steps_are_reported(self):
        now = time.time()
        t = self._active(goal_touched=self._touched(15),
                         steps=[self._step("buy milk", done=True, ts_=now)])
        self.assertEqual(checker.goal_drift(t, now=now)["unattributed"], [1])

    def test_drift_never_mutates_the_task(self):
        t = self._active(goal_touched=self._touched(15))
        before = json.dumps(store.strip_rev(self._reload(t)), sort_keys=True, default=str)
        checker.goal_drift(self._reload(t))
        after = json.dumps(store.strip_rev(self._reload(t)), sort_keys=True, default=str)
        self.assertEqual(before, after)


# ---------------------------------------------------------------------------
# (e) the local pointer check. REAL FILES, NO GIT SUBPROCESS.
# ---------------------------------------------------------------------------

class _PointerBase(_Base):
    """Real on-disk fixtures for the pointer check — and one unavoidable stub.

    THE STUB, AND WHY IT IS NOT A CHEAT. `pointers` skips every recorded path that
    `heal.ephemeral_path` classifies as erase-by-design, which is correct and is the fix
    for a real incident (all seven of heal's drift findings on one task were auto-captured
    briefs under a wiped session scratchpad). But a unittest temp dir lives under
    `/var/folders` on macOS and `/tmp` on Linux — both of them documented TEMP_ROOTS — so
    EVERY real-file fixture built here would be skipped for a reason that has nothing to
    do with the parsing these tests exist to prove. There is nowhere else to put a real
    file: writing outside the temp dir would pollute the developer's disk.

    So the exclusion is neutralised for the fixture-based tests and proved SEPARATELY, on
    hardcoded temp paths with the real function restored, by
    `test_an_ephemeral_path_is_never_a_finding`. Splitting it that way is what keeps both
    halves honest: one test owns the policy, the rest own the parsing."""

    def setUp(self):
        super().setUp()
        self._real_ephemeral = heal.ephemeral_path
        heal.ephemeral_path = lambda p, tmpdir=None: False

    def tearDown(self):
        heal.ephemeral_path = self._real_ephemeral
        super().tearDown()

    def _file(self, name, body=""):
        p = os.path.join(self.tmp, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        return p

    def _dir(self, name):
        p = os.path.join(self.tmp, name)
        os.makedirs(p, exist_ok=True)
        return p

    def _worktree(self, name="wt", branch="track1-checker", loose=True,
                  packed=False, gitdir_exists=True, head="ref: refs/heads/%s\n"):
        """A REAL linked-worktree pointer chain: the `.git` FILE, the administrative
        git dir it names, its HEAD, its `commondir`, and the repository's refs.

        Built rather than mocked because the check's whole contract is that it reads
        these files by hand instead of shelling out to git."""
        wt = self._dir("trees/%s" % name)
        common = self._dir("repo/.git")
        admin = os.path.join(common, "worktrees", name)
        if gitdir_exists:
            os.makedirs(admin, exist_ok=True)
            with open(os.path.join(admin, "HEAD"), "w", encoding="utf-8") as f:
                f.write(head % branch if "%s" in head else head)
            with open(os.path.join(admin, "commondir"), "w", encoding="utf-8") as f:
                f.write("../..\n")
        if loose:
            ref = os.path.join(common, "refs", "heads", *branch.split("/"))
            os.makedirs(os.path.dirname(ref), exist_ok=True)
            with open(ref, "w", encoding="utf-8") as f:
                f.write("0" * 40 + "\n")
        if packed:
            with open(os.path.join(common, "packed-refs"), "w", encoding="utf-8") as f:
                f.write("# pack-refs with: peeled fully-peeled sorted \n")
                f.write("%s refs/heads/%s\n" % ("1" * 40, branch))
        with open(os.path.join(wt, ".git"), "w", encoding="utf-8") as f:
            f.write("gitdir: %s\n" % admin)
        return wt

    def _with_paths(self, files=(), worktrees=()):
        meta = {("sess%d" % i): {"cwd": p} for i, p in enumerate(worktrees)}
        return self._task(files=list(files), session_meta=meta)


class TestPointers(_PointerBase):
    def test_a_healthy_task_is_silent(self):
        t = self._with_paths(files=[self._file("lib/real.py", "x")])
        self.assertEqual(checker.pointers(t), [])

    def test_a_missing_recorded_path_is_reported(self):
        t = self._with_paths(files=["/Users/nobody/Workspace/proj/gone.py"])
        found = checker.pointers(t)
        self.assertEqual([f["check"] for f in found], ["pointer-path"])
        self.assertIn("no longer exists", found[0]["detail"])

    def test_an_ephemeral_path_is_never_a_finding(self):
        # A session scratchpad is ERASED BY DESIGN. On one real task all seven of heal's
        # drift findings were exactly these, and a check that cries wolf seven times out
        # of seven is worse than no check. THE REAL `ephemeral_path` runs here (see the
        # class docstring for why the other tests stub it), and neither of these paths
        # exists.
        heal.ephemeral_path = self._real_ephemeral
        t = self._with_paths(files=[
            "/private/tmp/abc-123/scratchpad/BRIEF-worker.md",
            "/tmp/ts-worker-9182/notes.md"])
        self.assertEqual(checker.pointers(t), [])

    def test_a_non_ephemeral_missing_path_still_reports_with_the_real_exclusion(self):
        # The other side of the same coin: the exclusion is narrow, so a repo-shaped path
        # is still drift when it vanishes.
        heal.ephemeral_path = self._real_ephemeral
        t = self._with_paths(files=["/Users/nobody/Workspace/proj/lib/gone.py"])
        self.assertEqual([f["check"] for f in checker.pointers(t)], ["pointer-path"])

    def test_a_dangling_symlink_is_its_own_finding(self):
        link = os.path.join(self.tmp, "link.py")
        os.symlink(os.path.join(self.tmp, "never-existed.py"), link)
        t = self._with_paths(files=[link])
        found = checker.pointers(t)
        self.assertEqual([f["check"] for f in found], ["pointer-symlink"])
        self.assertIn("still there", found[0]["detail"])

    def test_a_live_symlink_is_silent(self):
        target = self._file("target.py", "x")
        link = os.path.join(self.tmp, "live-link.py")
        os.symlink(target, link)
        t = self._with_paths(files=[link])
        self.assertEqual(checker.pointers(t), [])

    def test_a_worktree_whose_git_dir_vanished_is_reported(self):
        wt = self._worktree(gitdir_exists=False)
        t = self._with_paths(worktrees=[wt])
        found = checker.pointers(t)
        self.assertEqual([f["check"] for f in found], ["pointer-worktree"])
        self.assertIn("administrative directory is gone", found[0]["detail"])

    def test_a_worktree_on_a_deleted_branch_is_reported(self):
        wt = self._worktree(loose=False)
        t = self._with_paths(worktrees=[wt])
        found = checker.pointers(t)
        self.assertEqual([f["check"] for f in found], ["pointer-branch"])
        self.assertEqual(found[0]["ref"], "track1-checker")
        self.assertIn("deleted while the worktree still sits on it", found[0]["detail"])

    def test_a_loose_ref_resolves(self):
        t = self._with_paths(worktrees=[self._worktree(loose=True)])
        self.assertEqual(checker.pointers(t), [])

    def test_a_packed_ref_resolves(self):
        # Plain file reads only — `packed-refs` is parsed by hand.
        t = self._with_paths(worktrees=[self._worktree(loose=False, packed=True)])
        self.assertEqual(checker.pointers(t), [])

    def test_a_slashed_branch_name_resolves(self):
        t = self._with_paths(worktrees=[self._worktree(branch="feature/nested")])
        self.assertEqual(checker.pointers(t), [])

    def test_a_detached_head_names_no_branch_so_nothing_is_checked(self):
        t = self._with_paths(worktrees=[
            self._worktree(loose=False, head="%s\n" % ("a" * 40))])
        self.assertEqual(checker.pointers(t), [])

    def test_a_main_checkout_is_skipped(self):
        # `.git` is a DIRECTORY there, and resolving its branch would mean opening a
        # repository — which this check does not do.
        wt = self._dir("main-checkout")
        os.makedirs(os.path.join(wt, ".git"), exist_ok=True)
        t = self._with_paths(worktrees=[wt])
        self.assertEqual(checker.pointers(t), [])

    def test_a_git_file_of_an_unknown_shape_says_nothing(self):
        wt = self._dir("odd")
        with open(os.path.join(wt, ".git"), "w", encoding="utf-8") as f:
            f.write("something else entirely\n")
        t = self._with_paths(worktrees=[wt])
        self.assertEqual(checker.pointers(t), [])

    def test_the_bound_claims_document_is_checked(self):
        t = self._task()
        checker.bind_doc(t, "/Users/nobody/plans/gone.md")
        found = checker.pointers(t)
        self.assertEqual([f["check"] for f in found], ["pointer-doc"])
        self.assertIn("--bind", found[0]["detail"])

    def test_an_existing_bound_document_is_silent(self):
        t = self._task()
        checker.bind_doc(t, self._file("plan.md", "# plan"))
        self.assertEqual(checker.pointers(t), [])

    def test_injected_config_paths_are_checked(self):
        t = self._task()
        found = checker.pointers(t, config_paths=[("repo index", "/nope/repos.json")])
        self.assertEqual([f["check"] for f in found], ["pointer-config"])
        self.assertIn("repo index", found[0]["detail"])

    def test_an_empty_config_path_list_is_the_honest_default(self):
        self.assertEqual(checker.pointers(self._task(), config_paths=[]), [])

    def test_findings_are_ordered_by_check(self):
        t = self._with_paths(files=["/Users/nobody/gone.py"],
                             worktrees=[self._worktree(loose=False)])
        checker.bind_doc(t, "/Users/nobody/plans/gone.md")
        self.assertEqual([f["check"] for f in checker.pointers(t)],
                         ["pointer-path", "pointer-branch", "pointer-doc"])

    def test_not_one_git_subprocess_runs(self):
        # The SessionStart contract, asserted rather than assumed: heal.drift documents
        # the same rule for its default path, and a session start that shells out per
        # recorded worktree is a session start that got slower for everyone.
        t = self._with_paths(files=["/Users/nobody/gone.py"],
                             worktrees=[self._worktree(loose=False)])
        real = subprocess.run

        def _boom(*a, **kw):
            raise AssertionError("the pointer check must not shell out: %r" % (a,))

        subprocess.run = _boom
        try:
            self.assertTrue(checker.pointers(t))
        finally:
            subprocess.run = real


# ---------------------------------------------------------------------------
# (f) claims — the store side.
# ---------------------------------------------------------------------------

class TestClaimRegistration(_Base):
    def test_a_registration_parses(self):
        item, err = checker.parse_registration("C1|grep -c foo plan.md|3")
        self.assertIsNone(err)
        self.assertEqual(item, {"id": "C1", "cmd": "grep -c foo plan.md",
                                "expect": ["3"]})

    def test_several_expected_substrings(self):
        item, _err = checker.parse_registration("C2|make test|OK|0 failures")
        self.assertEqual(item["expect"], ["OK", "0 failures"])

    def test_an_escaped_pipe_stays_in_the_command(self):
        # A claim's whole point is to run a REAL command, and real commands pipe. A bare
        # split would truncate this at `grep` and then compare the expectation against
        # the output of half a command.
        item, err = checker.parse_registration(
            "C3|grep -c foo plan.md \\| tr -d ' '|3")
        self.assertIsNone(err)
        self.assertEqual(item["cmd"], "grep -c foo plan.md | tr -d ' '")
        self.assertEqual(item["expect"], ["3"])

    def test_a_claim_with_nothing_to_assert_is_refused(self):
        # It would pass forever, whatever the command printed — the one failure mode a
        # verification mechanism must not have.
        item, err = checker.parse_registration("C1|echo hi")
        self.assertIsNone(item)
        self.assertIn("no expected substring", err)

    def test_a_claim_with_no_command_is_refused(self):
        item, err = checker.parse_registration("C1")
        self.assertIsNone(item)
        self.assertIn("ID|COMMAND|EXPECTED", err)

    def test_an_empty_registration_is_refused(self):
        item, err = checker.parse_registration("   ")
        self.assertIsNone(item)
        self.assertIn("needs", err)

    def test_an_essay_is_not_a_claim_id(self):
        item, err = checker.parse_registration("%s|echo hi|hi" % ("C" * 40))
        self.assertIsNone(item)
        self.assertIn("short label", err)

    def test_register_upserts_by_id(self):
        t = self._task()
        checker.register(t, ["C1|echo one|one", "C2|echo two|two"])
        added, updated, errors = checker.register(t, ["C1|echo ONE|ONE"])
        self.assertEqual((added, updated, errors), (0, 1, []))
        items = checker.claim_items(t)
        self.assertEqual([i["id"] for i in items], ["C1", "C2"])
        self.assertEqual(items[0]["cmd"], "echo ONE")

    def test_replace_swaps_the_whole_list(self):
        t = self._task()
        checker.register(t, ["C1|echo one|one", "C2|echo two|two"])
        checker.register(t, ["C9|echo nine|nine"], replace=True)
        self.assertEqual([i["id"] for i in checker.claim_items(t)], ["C9"])

    def test_a_replace_whose_specs_all_failed_changes_nothing(self):
        # Emptying a hand-built list because the caller mistyped it is the destructive
        # reading of a typo.
        t = self._task()
        checker.register(t, ["C1|echo one|one"])
        added, updated, errors = checker.register(t, ["garbage"], replace=True)
        self.assertEqual((added, updated), (0, 0))
        self.assertTrue(errors)
        self.assertEqual([i["id"] for i in checker.claim_items(t)], ["C1"])

    def test_remove_reports_a_missing_id(self):
        t = self._task()
        checker.register(t, ["C1|echo one|one"])
        removed, missing = checker.remove(t, ["C1", "C7"])
        self.assertEqual(removed, ["C1"])
        self.assertEqual(missing, ["C7"])

    def test_removing_the_last_claim_leaves_the_task_as_it_was(self):
        t = self._task()
        checker.register(t, ["C1|echo one|one"])
        checker.remove(t, ["C1"])
        self.assertNotIn(checker.CLAIMS_FIELD, t)

    def test_garbled_items_are_filtered_not_raised_on(self):
        t = self._task(claims={"items": ["nope", {"id": "C1"}, {"cmd": "x"},
                                         {"id": "C2", "cmd": "echo hi",
                                          "expect": ["hi"]}]})
        self.assertEqual([i["id"] for i in checker.claim_items(t)], ["C2"])


class TestClaimBinding(_Base):
    def test_binding_records_the_document_and_the_moment(self):
        t = self._task()
        ok, err = checker.bind_doc(t, "/Users/somebody/plans/master.md", now=99.0)
        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertEqual(checker.claims_doc(t), "/Users/somebody/plans/master.md")
        self.assertEqual(checker.claims(t)["bound_ts"], 99.0)

    def test_a_relative_path_is_refused(self):
        # The same stored value would name different files from different shells.
        t = self._task()
        ok, err = checker.bind_doc(t, "plans/master.md")
        self.assertFalse(ok)
        self.assertIn("ABSOLUTE", err)
        self.assertEqual(checker.claims_doc(t), None)

    def test_unbinding_keeps_the_registered_claims(self):
        # A renamed or split plan is the common case, and discarding a hand-registered
        # command list as a side effect of that would be destructive.
        t = self._task()
        checker.bind_doc(t, "/Users/somebody/plans/master.md")
        checker.register(t, ["C1|echo one|one"])
        ok, err = checker.unbind(t)
        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertIsNone(checker.claims_doc(t))
        self.assertEqual([i["id"] for i in checker.claim_items(t)], ["C1"])

    def test_unbinding_nothing_says_so(self):
        ok, err = checker.unbind(self._task())
        self.assertFalse(ok)
        self.assertIn("no bound document", err)


class TestClaimVerification(_Base):
    def _fake(self, table):
        """A runner that answers from a table instead of spawning a shell."""
        def run(cmd, timeout):
            return table.get(cmd, ("", "ran"))
        return run

    def test_a_matching_claim_passes(self):
        t = self._task()
        checker.register(t, ["C1|echo scrub landed|landed"])
        results = checker.verify(
            t, run=self._fake({"echo scrub landed": ("scrub landed\n", "ran")}))
        self.assertEqual([r["ok"] for r in results], [True])
        self.assertEqual(results[0]["missing"], [])

    def test_a_missing_substring_fails_and_names_what_was_missing(self):
        t = self._task()
        checker.register(t, ["C1|make test|OK|0 failures"])
        results = checker.verify(t, run=self._fake({"make test": ("OK\n", "ran")}))
        self.assertFalse(results[0]["ok"])
        self.assertEqual(results[0]["missing"], ["0 failures"])

    def test_stderr_counts_as_output(self):
        t = self._task()
        checker.register(t, ["C1|thing|warning: fine"])
        results = checker.verify(t, run=self._fake({"thing": ("warning: fine", "ran")}))
        self.assertTrue(results[0]["ok"])

    def test_a_timeout_is_not_a_refutation(self):
        # The uncountable rule again: a claim that never ran has not been refuted, and
        # the stored status has to say which of the two happened.
        t = self._task()
        checker.register(t, ["C1|sleep forever|done"])
        results = checker.verify(t, timeout=5,
                                run=self._fake({"sleep forever": ("", "timeout")}))
        self.assertFalse(results[0]["ok"])
        self.assertEqual(results[0]["status"], "timeout")
        self.assertIn("timed out", results[0]["got"])

    def test_a_command_that_could_not_run_is_reported_as_such(self):
        t = self._task()
        checker.register(t, ["C1|nope|done"])
        results = checker.verify(t, run=self._fake({"nope": ("boom", "error")}))
        self.assertEqual(results[0]["status"], "error")
        self.assertFalse(results[0]["ok"])

    def test_output_is_stored_as_a_bounded_tail(self):
        # The store is not a log — and a command's verdict is at the END of its output.
        t = self._task()
        checker.register(t, ["C1|noisy|marker"])
        big = ("x" * 5000) + "marker"
        results = checker.verify(t, run=self._fake({"noisy": (big, "ran")}))
        self.assertLessEqual(len(results[0]["got"]), checker.CLAIM_OUTPUT_TAIL)
        self.assertTrue(results[0]["got"].endswith("marker"))
        self.assertTrue(results[0]["got"].startswith("…"))

    def test_only_one_claim_can_be_run(self):
        t = self._task()
        checker.register(t, ["C1|one|1", "C2|two|2"])
        results = checker.verify(t, only="C2", run=self._fake({"two": ("2", "ran")}))
        self.assertEqual([r["id"] for r in results], ["C2"])

    def test_the_last_verify_is_recorded_on_the_task(self):
        t = self._task()
        checker.register(t, ["C1|one|1"])
        checker.verify(t, now=42.0, run=self._fake({"one": ("1", "ran")}))
        self.assertEqual(checker.last_verify(t)["ts"], 42.0)
        self.assertEqual(checker.last_verify(t)["results"][0]["id"], "C1")

    def test_verifying_nothing_records_nothing(self):
        t = self._task()
        self.assertEqual(checker.verify(t, run=self._fake({})), [])
        self.assertEqual(checker.last_verify(t), {})

    def test_a_real_shell_command_runs(self):
        # ONE test that actually spawns a shell, so the plumbing is proved rather than
        # only the table-driven logic. `echo` is the cheapest possible claim.
        t = self._task()
        checker.register(t, ["C1|echo the scrub landed|scrub landed",
                             "C2|echo nothing here|impossible"])
        results = checker.verify(t, timeout=30)
        self.assertEqual([r["ok"] for r in results], [True, False])


# ---------------------------------------------------------------------------
# (g) the self-capping nags.
# ---------------------------------------------------------------------------

class TestNags(_PointerBase):
    def test_the_drift_nag_fires_once_per_state(self):
        t = self._active(goal_touched=self._touched(15))
        line = checker.drift_nag(self._reload(t))
        self.assertIsNotNone(line)
        self.assertIn("goal drift", line)
        self.assertIsNone(checker.drift_nag(self._reload(t)))
        self.assertIsNone(checker.drift_nag(self._reload(t)))

    def test_the_drift_nag_says_what_it_measured_not_what_it_concluded(self):
        # Attribution is a heuristic, so the line has to be honest about being a reason
        # to LOOK. This is the whole reason the module is allowed to speak at all.
        t = self._active(goal_touched=self._touched(15))
        line = checker.drift_nag(t)
        self.assertIn("word overlap", line)
        self.assertIn("not a verdict", line)

    def test_crossing_the_escalation_re_arms_the_nag_exactly_once(self):
        # The TIER is in the fingerprint and the DAY COUNT is not: including the days
        # would make an unheeded nag return every midnight.
        now = time.time()
        t = self._active(goal="DONE = (1) the parser handles nested markers",
                         goal_touched={"ts": now - 4 * DAY})
        self.assertIsNotNone(checker.drift_nag(t, now=now))
        self.assertIsNone(checker.drift_nag(t, now=now + 1 * DAY))    # still DRIFTED
        escalated = checker.drift_nag(t, now=now + 5 * DAY)           # crossed 7 days
        self.assertIsNotNone(escalated)
        self.assertIn("ESCALATED", escalated)
        self.assertIsNone(checker.drift_nag(t, now=now + 6 * DAY))    # once, not daily

    def test_a_healthy_task_never_nags(self):
        self.assertIsNone(checker.drift_nag(self._active()))
        self.assertIsNone(checker.pointer_nag(self._task()))

    def test_the_pointer_nag_fires_once_per_state(self):
        t = self._with_paths(files=["/Users/nobody/gone.py"])
        line = checker.pointer_nag(self._reload(t))
        self.assertIsNotNone(line)
        self.assertIn("no longer resolve", line)
        self.assertIsNone(checker.pointer_nag(self._reload(t)))

    def test_the_pointer_nag_re_arms_when_a_new_pointer_breaks(self):
        t = self._with_paths(files=["/Users/nobody/gone.py"])
        self.assertIsNotNone(checker.pointer_nag(self._reload(t)))
        t2 = self._reload(t)
        t2["files"].append("/Users/nobody/also-gone.py")
        ts.save_task(t2)
        self.assertIsNotNone(checker.pointer_nag(self._reload(t)))

    def test_the_two_nags_do_not_cancel_each_other(self):
        # One gate file, one key each. A whole-file write from the pointer check would
        # silently re-arm the drift nag every session.
        t = self._active(goal_touched=self._touched(15),
                         files=["/Users/nobody/gone.py"])
        self.assertIsNotNone(checker.drift_nag(self._reload(t)))
        self.assertIsNotNone(checker.pointer_nag(self._reload(t)))
        self.assertIsNone(checker.drift_nag(self._reload(t)))
        self.assertIsNone(checker.pointer_nag(self._reload(t)))

    def test_clearing_the_gate_re_arms_both(self):
        t = self._active(goal_touched=self._touched(15),
                         files=["/Users/nobody/gone.py"])
        checker.drift_nag(self._reload(t))
        checker.pointer_nag(self._reload(t))
        self.assertTrue(checker.clear_gate(t["id"]))
        self.assertIsNotNone(checker.drift_nag(self._reload(t)))
        self.assertIsNotNone(checker.pointer_nag(self._reload(t)))

    def test_the_gate_lives_in_its_own_directory(self):
        t = self._active(goal_touched=self._touched(15))
        checker.drift_nag(self._reload(t))
        self.assertTrue(os.path.exists(checker.gate_path(t["id"])))
        self.assertNotEqual(checker.gate_dir(), heal.gate_dir())
        self.assertFalse(os.path.exists(heal.gate_path(t["id"])))

    def test_an_unreadable_gate_reads_as_empty_and_nags_again(self):
        os.makedirs(checker.gate_dir(), exist_ok=True)
        with open(checker.gate_path("bogus"), "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(checker.read_gate("bogus"), {})

    def test_a_closed_task_never_nags(self):
        t = self._task(status=ts.STATUS_CLOSED, goal=GOAL,
                       goal_touched=self._touched(15),
                       files=["/Users/nobody/gone.py"])
        self.assertIsNone(checker.drift_nag(t))
        self.assertIsNone(checker.pointer_nag(t))

    def test_the_pointer_nag_still_fires_on_a_parked_task(self):
        # A deleted worktree is a fact about the filesystem, not about whether anyone is
        # working on it — and it is exactly what you need told BEFORE you resume.
        t = self._task(status=ts.STATUS_OPEN, files=["/Users/nobody/gone.py"])
        self.assertIsNotNone(checker.pointer_nag(t))

    def test_a_raising_check_yields_silence(self):
        # FAIL OPEN: a broken check must never be worse than the silence it replaces.
        t = self._active(goal_touched=self._touched(15))
        real_drift, real_pointers = checker.goal_drift, checker.pointers

        def _boom(*a, **kw):
            raise RuntimeError("kaboom")

        checker.goal_drift, checker.pointers = _boom, _boom
        try:
            self.assertIsNone(checker.drift_nag(t))
            self.assertIsNone(checker.pointer_nag(t))
            self.assertIsNone(checker.listing_nag([t]))
        finally:
            checker.goal_drift, checker.pointers = real_drift, real_pointers

    def test_a_nag_with_persist_off_does_not_cap_itself(self):
        t = self._active(goal_touched=self._touched(15))
        self.assertIsNotNone(checker.drift_nag(t, persist=False))
        self.assertIsNotNone(checker.drift_nag(t, persist=False))

    def test_the_pointer_line_rolls_up_beyond_two_findings(self):
        t = self._with_paths(files=["/Users/nobody/a.py", "/Users/nobody/b.py",
                                    "/Users/nobody/c.py"])
        line = checker.pointer_nag(self._reload(t))
        self.assertIn("3 declared pointer(s)", line)
        self.assertIn("+1 more", line)

    def test_the_drift_line_names_the_worst_and_counts_the_rest(self):
        now = time.time()
        t = self._active(goal_touched=self._touched(20), steps=[
            self._step("the scrub landed and the retired names are gone",
                       done=True, ts_=now)])
        line = checker.drift_nag(t, now=now)
        self.assertIn("(2)", line)                    # the worst offender, named
        self.assertIn("2 other condition(s)", line)   # the rest, counted


class TestTheUnattachedListing(_Base):
    def test_the_worst_offender_wins(self):
        now = time.time()
        mild = self._active(goal="DONE = (1) the parser handles nested markers",
                            goal_touched={"ts": now - 4 * DAY}, title="mild")
        bad = self._active(goal="DONE = (1) the exporter writes the manifest",
                           goal_touched={"ts": now - 40 * DAY}, title="bad")
        task, result = checker.worst_drift([mild, bad], now=now)
        self.assertEqual(task["id"], bad["id"])
        self.assertEqual(result["drifted"][0]["days"], 40)

    def test_only_active_tasks_are_swept(self):
        now = time.time()
        parked = self._task(status=ts.STATUS_OPEN, goal=GOAL,
                            goal_touched={"ts": now - 40 * DAY})
        self.assertEqual(checker.worst_drift([parked], now=now), (None, None))

    def test_the_listing_nag_is_one_line_and_self_caps(self):
        t = self._active(goal_touched=self._touched(15))
        line = checker.listing_nag([t])
        self.assertIsNotNone(line)
        self.assertEqual(len(line.splitlines()), 1)
        self.assertIsNone(checker.listing_nag([t]))

    def test_the_listing_gate_is_separate_from_the_attached_one(self):
        # Opening the task once must not permanently silence the board-level warning —
        # that is exactly how this check would go quiet on the case it exists to catch.
        t = self._active(goal_touched=self._touched(15))
        self.assertIsNotNone(checker.drift_nag(self._reload(t)))
        self.assertIsNotNone(checker.listing_nag([self._reload(t)]))

    def test_a_healthy_board_is_silent(self):
        self.assertIsNone(checker.listing_nag([self._active()]))
        self.assertIsNone(checker.listing_nag([]))


# ---------------------------------------------------------------------------
# session-start integration — the rail both checks join.
# ---------------------------------------------------------------------------

class TestSessionStart(_PointerBase):
    def _start(self, session="sess-checker"):
        return self._out(ts.cmd_session_start, _Args(session=session, source="startup"))

    def test_both_lines_join_the_attached_rail(self):
        t = self._active(goal_touched=self._touched(15),
                         files=["/Users/nobody/gone.py"])
        ts.set_link("sess-both", t["id"])
        out = self._start("sess-both")
        self.assertIn("attached to task", out)
        self.assertIn("no longer resolve", out)
        self.assertIn("goal drift", out)
        # Pointers above drift: the mechanical fact you can act on now goes first.
        self.assertLess(out.index("no longer resolve"), out.index("goal drift"))

    def test_a_healthy_attached_task_adds_nothing(self):
        t = self._active()
        ts.set_link("sess-clean", t["id"])
        out = self._start("sess-clean")
        self.assertIn("attached to task", out)
        self.assertNotIn("goal drift", out)
        self.assertNotIn("no longer resolve", out)

    def test_the_rail_self_caps_across_sessions(self):
        t = self._active(goal_touched=self._touched(15))
        ts.set_link("sess-cap", t["id"])
        self.assertIn("goal drift", self._start("sess-cap"))
        self.assertNotIn("goal drift", self._start("sess-cap"))

    def test_the_unattached_listing_names_a_drifting_plan(self):
        # THE HALF THAT CATCHES A PLAN NOBODY OPENED.
        t = self._active(goal_touched=self._touched(15), title="the master plan")
        out = self._start("sess-unattached")
        self.assertIn("open task(s)", out)
        self.assertIn("goal drift", out)
        self.assertIn(str(t.get("seq")), out)

    def test_the_unattached_listing_is_silent_on_a_healthy_board(self):
        self._active()
        out = self._start("sess-unattached-clean")
        self.assertIn("open task(s)", out)
        self.assertNotIn("goal drift", out)

    def test_the_unattached_listing_ignores_parked_tasks(self):
        self._task(status=ts.STATUS_OPEN, goal=GOAL, goal_touched=self._touched(40))
        self.assertNotIn("goal drift", self._start("sess-unattached-parked"))

    def test_a_raising_checker_never_breaks_the_attached_block(self):
        t = self._active(goal_touched=self._touched(15))
        ts.set_link("sess-boom", t["id"])
        real = (checker.pointer_nag, checker.drift_nag, checker.listing_nag)

        def _boom(*a, **kw):
            raise RuntimeError("kaboom")

        checker.pointer_nag = checker.drift_nag = checker.listing_nag = _boom
        try:
            out = self._start("sess-boom")
            self.assertIn("attached to task", out)
            self.assertNotIn("goal drift", out)
            # …and the unattached path survives it too.
            self.assertIn("open task(s)", self._start("sess-boom-unattached"))
        finally:
            (checker.pointer_nag, checker.drift_nag, checker.listing_nag) = real

    def test_a_skipped_session_stays_silent(self):
        self._active(goal_touched=self._touched(15))
        ts.set_link("sess-skip", ts.SKIP_SENTINEL)
        self.assertEqual(self._start("sess-skip").strip(), "")

    def test_no_claim_command_is_ever_run_at_session_start(self):
        # Arbitrary user shell commands in front of every session, with whatever side
        # effects they have, is not a trade this tool gets to make on the user's behalf.
        # The pointer check STATS the bound document; it never opens it and never runs a
        # claim. Asserted on the claim runner itself rather than on subprocess, so this
        # says exactly that and cannot pass or fail for an unrelated reason.
        t = self._active(goal_touched=self._touched(15))
        checker.register(t, ["C1|echo hi|hi"])
        checker.bind_doc(t, self._file("plan.md", "# plan"))
        ts.save_task(t)
        ts.set_link("sess-claims", t["id"])
        real = checker._run_claim

        def _boom(*a, **kw):
            raise AssertionError("session start must never run a claim: %r" % (a,))

        checker._run_claim = _boom
        try:
            out = self._start("sess-claims")
        finally:
            checker._run_claim = real
        self.assertIn("attached to task", out)
        self.assertEqual(checker.last_verify(self._reload(t)), {})


# ---------------------------------------------------------------------------
# the claims CLI, end to end.
# ---------------------------------------------------------------------------

class TestClaimsCli(_Base):
    def _claims(self, t, **kw):
        return self._out(ts.cmd_claims, _Args(task=str(t["seq"]), **kw))

    def _plan(self, body="the scrub landed\n"):
        p = os.path.join(self.tmp, "PLAN.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        return p

    def test_bind_register_show(self):
        t = self._task()
        plan = self._plan()
        out = self._claims(t, bind=plan)
        self.assertIn("bound →", out)
        self.assertIn(plan, out)
        out = self._claims(t, register=["C1|grep -c scrub %s|1" % plan])
        self.assertIn("registered 1 new claim(s)", out)
        out = self._claims(t)
        self.assertIn("C1", out)
        self.assertIn("expects: 1", out)
        self.assertIn("last verify: never", out)

    def test_the_writes_persist(self):
        t = self._task()
        self._claims(t, bind=self._plan(), register=["C1|echo hi|hi"])
        stored = self._reload(t)
        self.assertTrue(checker.claims_doc(stored))
        self.assertEqual([i["id"] for i in checker.claim_items(stored)], ["C1"])

    def test_verify_reports_and_persists(self):
        t = self._task()
        plan = self._plan()
        self._claims(t, register=["C1|grep -c scrub %s|1" % plan])
        out = self._claims(t, action="verify")
        self.assertIn("1/1 passed", out)
        self.assertEqual(len(checker.last_verify(self._reload(t))["results"]), 1)

    def test_a_failing_verify_exits_non_zero(self):
        # So it can GATE a release step rather than only inform a reader.
        t = self._task()
        self._claims(t, register=["C1|echo nothing|impossible"])
        with self.assertRaises(SystemExit) as caught:
            self._claims(t, action="verify")
        self.assertEqual(caught.exception.code, 1)
        self.assertFalse(checker.last_verify(self._reload(t))["results"][0]["ok"])

    def test_a_failing_verify_says_what_was_missing(self):
        t = self._task()
        self._claims(t, register=["C1|echo nothing|impossible"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            with self.assertRaises(SystemExit):
                ts.cmd_claims(_Args(task=str(t["seq"]), action="verify"))
        out = buf.getvalue()
        self.assertIn("FAIL", out)
        self.assertIn("missing from the output: impossible", out)

    def test_verify_can_target_one_claim(self):
        t = self._task()
        self._claims(t, register=["C1|echo one|one", "C2|echo two|two"])
        out = self._claims(t, action="verify", id="C2")
        self.assertIn("1/1 passed", out)
        self.assertIn("C2", out)
        self.assertNotIn("C1 ", out)

    def test_verify_with_no_claims_says_so_and_does_not_fail(self):
        out = self._claims(self._task(), action="verify")
        self.assertIn("no claims registered", out)

    def test_verify_refuses_to_be_combined_with_a_write(self):
        # Answering half of "register this and tell me whether it passes" — with a verify
        # that ran against the OLD list — is worse than saying run it twice.
        t = self._task()
        out = self._claims(t, action="verify", register=["C1|echo hi|hi"])
        self.assertIn("cannot be combined", out)
        self.assertEqual(checker.claim_items(self._reload(t)), [])

    def test_replace_without_register_is_refused(self):
        t = self._task()
        self._claims(t, register=["C1|echo one|one"])
        out = self._claims(t, replace=True)
        self.assertIn("needs at least one --register", out)
        self.assertEqual([i["id"] for i in checker.claim_items(self._reload(t))], ["C1"])

    def test_remove_and_unbind(self):
        t = self._task()
        self._claims(t, bind=self._plan(), register=["C1|echo one|one"])
        out = self._claims(t, remove=["C1"])
        self.assertIn("removed C1", out)
        out = self._claims(t, unbind=True)
        self.assertIn("unbound", out)
        stored = self._reload(t)
        self.assertIsNone(checker.claims_doc(stored))
        self.assertEqual(checker.claim_items(stored), [])

    def test_removing_an_unknown_id_says_so(self):
        t = self._task()
        out = self._claims(t, remove=["C9"])
        self.assertIn("no such claim: C9", out)

    def test_a_bad_registration_is_reported_without_killing_the_good_one(self):
        t = self._task()
        out = self._claims(t, register=["C1|echo one|one", "garbage"])
        self.assertIn("registered 1 new claim(s)", out)
        self.assertIn("ID|COMMAND|EXPECTED", out)
        self.assertEqual([i["id"] for i in checker.claim_items(self._reload(t))], ["C1"])

    def test_a_relative_bind_is_refused_at_the_cli(self):
        t = self._task()
        out = self._claims(t, bind="PLAN.md")
        self.assertIn("ABSOLUTE", out)

    def test_a_missing_bound_document_is_called_out(self):
        t = self._task()
        self._claims(t, bind="/Users/nobody/plans/gone.md")
        self.assertIn("MISSING", self._claims(t))

    def test_an_unknown_task_is_a_clear_error(self):
        out = self._out(ts.cmd_claims, _Args(task="99999"))
        self.assertIn("No task matching", out)

    def test_with_no_task_and_no_session_it_says_how_to_name_one(self):
        out = self._out(ts.cmd_claims, _Args())
        self.assertIn("--task", out)

    def test_the_attached_task_is_the_default(self):
        t = self._task()
        ts.set_link("sess-claims-cli", t["id"])
        out = self._out(ts.cmd_claims, _Args(session="sess-claims-cli"))
        self.assertIn("Claims — task #%s" % t.get("seq"), out)

    def test_an_unknown_action_says_what_the_two_are(self):
        # A bare positional, so `claims 12` (a task ref in the wrong place) has to get a
        # sentence rather than argparse's usage dump.
        out = self._out(ts.cmd_claims, _Args(action="12"))
        self.assertIn("unknown action", out)
        self.assertIn("--task", out)

    def test_the_real_parser_accepts_the_documented_shapes(self):
        # The subparser is wired and `verify` really is the positional action.
        t = self._task()
        plan = self._plan()
        with redirect_stdout(io.StringIO()):
            ts.main(["claims", "--task", str(t["seq"]), "--bind", plan,
                     "--register", "C1|grep -c scrub %s|1" % plan])
            ts.main(["claims", "verify", "--task", str(t["seq"])])
        stored = self._reload(t)
        self.assertEqual(checker.claims_doc(stored), plan)
        self.assertTrue(checker.last_verify(stored)["results"][0]["ok"])


if __name__ == "__main__":
    unittest.main()
