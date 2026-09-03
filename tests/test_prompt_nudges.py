"""The PROMPT RAIL's two nudges — `lib/board/nudges.py` — and the two smaller changes
that ship with them.

WHAT IS BEING TESTED AND WHY IT IS ITS OWN FILE. Both signals already existed, and both
could only speak at a moment the session could no longer act on:

  * `heal.nag` fires at SESSION START, so it reports the state a session INHERITED. But a
    long working session is the thing that MAKES its own task heal-due — it writes the
    decisions, it leaves the acks undispositioned — and by then that rail has run.
  * `save.gap_report` reports a stale checkpoint accurately and reports it INSIDE `/todo
    save`, i.e. after the user already decided to checkpoint. The case where nobody thinks
    to run it is exactly the case it was needed for.

So the invariants here are about TIMING and RESTRAINT, in that order. The rail under test
runs on EVERY prompt a user types, which makes it the one place in this codebase where a
nag that cries wolf is unforgivable — so most of what follows is proof of SILENCE:

  1. FAIL OPEN — a garbled gate or a raising config yields silence, never a broken prompt.
  2. SILENT WHEN HEALTHY — no "all clear" line, ever, on either nudge.
  3. ONCE PER (TASK, SESSION) — and the fingerprint is the LIMB, never the worded reason.
     Every reason either nudge can give carries a number that moves on its own (a decision
     lands, an hour passes), so hashing the sentence would re-arm on nearly every prompt
     and the once-per-session nudge would fire forever. This is `checker.drift_signature`'s
     lesson and the single most load-bearing assertion in this file.
  4. NEVER CHECKPOINTED IS NOT STALE — a task minutes old has an empty digest by
     definition, and nudging it would be the always-on alarm every check in `heal` has had
     to learn not to be.
  5. NO SCAN ON THIS RAIL — `heal.cheap_limbs`, not `heal.due`. Asserted by wording
     equality against the full pass rather than by timing.

Isolation copies the `_repoint` idiom from tests/test_heal.py and tests/test_checker.py.
The engine patch surface is deliberately limited to DATA / STORE / TASKS_DIR / LINKS_DIR
(all already routed) — see tests/test_patch_surface.py, which fails if this file patches
anything else on `ts`.
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(_REPO_ROOT, "lib")
sys.path.insert(0, LIB)

_TMP_HOME = tempfile.mkdtemp(prefix="ts-nudges-")
os.environ["TASK_STATION_HOME"] = _TMP_HOME

import board.nudges as nudges    # noqa: E402
import config as ts_config       # noqa: E402
import heal                      # noqa: E402
import previews                  # noqa: E402
import save as ts_save           # noqa: E402
import store                     # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)

HOUR = 3600
DAY = 86400

# Every env escape this file touches, cleared in tearDown so one test can never leak a
# threshold into the next.
_ENV_KEYS = ("TASK_STATION_HEAL_PROMPT_NAG", "TASK_STATION_SAVE_NUDGE",
             "TASK_STATION_SAVE_NUDGE_DECISIONS", "TASK_STATION_SAVE_NUDGE_HOURS")
_CFG_KEYS = ("heal_prompt_nag", "save_nudge", "save_nudge_decisions", "save_nudge_hours")


class _Args:
    def __init__(self, **kw):
        defaults = dict(session=None, source="startup", arg="", prompt="")
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
        self.tmp = tempfile.mkdtemp(prefix="nudges-test-")
        _repoint(self.tmp)

    def tearDown(self):
        store.reset_cache()
        for k in _ENV_KEYS:
            os.environ.pop(k, None)
        for k in _CFG_KEYS:
            ts_config.unset(k)
        os.environ.pop("TASK_STATION_PROMPT", None)
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- fixtures ---------------------------------------------------------------

    def _task(self, title="A task", **fields):
        t = ts.new_task(title, "summary")
        t.update(fields)
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _decisions(self, n, prefix="decision"):
        return [{"text": "%s %d" % (prefix, i)} for i in range(n)]

    def _heal_due(self, **fields):
        """A task the CHEAP limbs call due, via the never-healed limb: no heal stamp and
        `DUE_NEW_DECISIONS` decisions on the log. Left at the default (open) status so the
        AGE limb cannot also fire — these tests need ONE limb at a time."""
        return self._task(decisions=self._decisions(heal.DUE_NEW_DECISIONS), **fields)

    def _healthy(self, **fields):
        """A task no limb reports: no heal stamp, but well under the decision floor."""
        return self._task(decisions=self._decisions(2), **fields)

    def _undispositioned_ack(self):
        """A memo carrying an ack with no disposition — the ACKS limb's shape."""
        return [{"id": "abcd1234ef", "acks": [{"sid": "sess-old"}]}]

    def _checkpointed(self, age_hours=1, decisions=0, history=0, baseline=True,
                      **fields):
        """A task with a full-checkpoint stamp `age_hours` old, carrying `decisions`
        decisions and `history` log entries recorded SINCE it.

        `baseline=False` drops `saved_counts`, which is how a stamp written by an older
        version reads: the age is known but what has landed since it CANNOT BE COUNTED."""
        now = time.time()
        t = dict(fields)
        t["last_full_save_ts"] = now - age_hours * HOUR
        if baseline:
            t["saved_counts"] = {"decisions": 0, "history": 0, "steps": 0}
        t["decisions"] = self._decisions(decisions)
        # `{"ts": <iso>, "text": …}` — the shape `model.log_milestone` actually writes, so
        # anything that parses a stamp here sees a real one rather than a bare float.
        t["history"] = [{"ts": ts._iso(now), "text": "entry %d" % i}
                        for i in range(history)]
        return self._task(**t)

    def _gate(self, task):
        return nudges.read_gate(task["id"])


# ---------------------------------------------------------------------------
# (a) heal.cheap_limbs — the same verdict as `due`, without the scan
# ---------------------------------------------------------------------------

class TestCheapLimbs(_Base):
    def test_the_cheap_reasons_are_word_for_word_the_full_pass_reasons(self):
        """THE GUARANTEE THAT MAKES THE CHEAP PATH SAFE. Both go through `_blob_limbs`, so
        the prompt rail can never word a reason differently from the one `/todo heal` and
        the SessionStart nag print. Compared on a task with NO findings, so the full pass's
        one extra limb is absent and the two lists must be equal."""
        t = self._heal_due()
        # Stated as a precondition rather than assumed: if a check ever starts firing on
        # this fixture the equality below would fail for a reason that has nothing to do
        # with the wording guarantee, and this line says so directly.
        self.assertEqual(heal.scan(t)["findings"], [])
        _due, full = heal.due(t)
        _cheap_due, cheap = heal.due_cheap(t)
        self.assertEqual(full, cheap)
        self.assertTrue(any("decision(s) are on the log" in r for r in cheap))

    def test_the_cheap_path_gives_up_exactly_the_findings_limb(self):
        # A task with an undispositioned ack has BOTH a finding (the ack) and the ack limb.
        # The full pass therefore leads with "the scan found N issue(s)"; the cheap path
        # never says that, and still reports the ack limb itself.
        t = self._task(memos=self._undispositioned_ack())
        _d, full = heal.due(t)
        _d2, cheap = heal.due_cheap(t)
        self.assertTrue(any("the scan found" in r for r in full))
        self.assertFalse(any("the scan found" in r for r in cheap))
        self.assertTrue(any("carry no disposition" in r for r in cheap))

    def test_limbs_are_tagged_with_their_kind(self):
        t = self._heal_due()
        self.assertEqual([limb for limb, _text in heal.cheap_limbs(t)],
                         [heal.LIMB_NEW_DECISIONS])

    def test_a_healthy_task_has_no_limbs(self):
        self.assertEqual(heal.cheap_limbs(self._healthy()), [])
        self.assertEqual(heal.due_cheap(self._healthy()), (False, []))

    def test_a_dismissed_ack_stays_silent_on_the_cheap_path_too(self):
        """The dismissal ledger is applied here exactly as `scan` applies it — otherwise an
        ack a reconciler adjudicated away would keep nudging on every prompt while the full
        pass reported the task clean."""
        t = self._task(memos=self._undispositioned_ack())
        finding = heal.undispositioned_acks(t)[0]
        t[heal.DISMISSALS_FIELD] = [
            heal.dismissal_entry(finding, "adjudicated: nothing to do")]
        ts.save_task(t)
        t = ts.load_task(t["id"])
        self.assertEqual(heal.cheap_limbs(t), [])

    def test_the_age_limb_needs_an_active_task(self):
        old = time.time() - 30 * DAY
        self.assertEqual(heal.cheap_limbs(self._task(created_ts=old)), [])
        limbs = heal.cheap_limbs(
            self._task(status=ts.STATUS_ACTIVE, created_ts=old))
        self.assertIn(heal.LIMB_AGE, [limb for limb, _t in limbs])


# ---------------------------------------------------------------------------
# (b) the heal nudge
# ---------------------------------------------------------------------------

class TestHealNudge(_Base):
    def test_it_fires_when_a_heal_is_due(self):
        t = self._heal_due()
        line = nudges.heal_line(t, "sess-1")
        self.assertIsNotNone(line)
        self.assertIn("heal is due on #%s" % t["seq"], line)
        self.assertIn("decision(s) are on the log", line)   # the REASON is named
        self.assertIn("/todo heal", line)

    def test_it_stays_silent_on_a_healthy_task(self):
        self.assertIsNone(nudges.heal_line(self._healthy(), "sess-1"))

    def test_it_stays_silent_on_a_closed_task(self):
        t = self._heal_due(status=ts.STATUS_CLOSED)
        self.assertIsNone(nudges.heal_line(t, "sess-1"))

    def test_it_fires_once_per_session(self):
        t = self._heal_due()
        self.assertIsNotNone(nudges.heal_line(t, "sess-1"))
        self.assertIsNone(nudges.heal_line(t, "sess-1"))
        self.assertIsNone(nudges.heal_line(t, "sess-1"))

    def test_each_session_is_told_once_of_its_own(self):
        """The gate is per (task, SESSION): a second session working the same task has not
        been told anything, and silencing it would mean only the first session that
        happened to prompt ever hears about a due heal."""
        t = self._heal_due()
        self.assertIsNotNone(nudges.heal_line(t, "sess-1"))
        self.assertIsNotNone(nudges.heal_line(t, "sess-2"))
        self.assertIsNone(nudges.heal_line(t, "sess-2"))

    def test_a_growing_count_does_NOT_re_arm_it(self):
        """THE ASSERTION THIS WHOLE THROTTLE EXISTS FOR. The reason reads "N new
        decision(s)", and N grows with every decision the session writes. If the
        fingerprint covered the worded reason, the nudge would return on almost every
        prompt — so it covers the LIMB, and more of the same limb is not news."""
        t = self._heal_due()
        self.assertIsNotNone(nudges.heal_line(t, "sess-1"))
        for extra in range(1, 6):
            t["decisions"] = self._decisions(heal.DUE_NEW_DECISIONS + extra)
            ts.save_task(t)
            self.assertIsNone(nudges.heal_line(ts.load_task(t["id"]), "sess-1"))

    def test_a_new_limb_DOES_re_arm_it(self):
        """A different KIND of problem is news, so it speaks again — and the new line names
        both limbs, not just the new one."""
        t = self._heal_due()
        first = nudges.heal_line(t, "sess-1")
        self.assertIsNotNone(first)
        self.assertNotIn("carry no disposition", first)
        t["memos"] = self._undispositioned_ack()
        ts.save_task(t)
        second = nudges.heal_line(ts.load_task(t["id"]), "sess-1")
        self.assertIsNotNone(second)
        self.assertIn("carry no disposition", second)
        self.assertIn("decision(s) are on the log", second)
        # …and the re-armed state then throttles on its own.
        self.assertIsNone(nudges.heal_line(ts.load_task(t["id"]), "sess-1"))

    def test_config_off_silences_it(self):
        t = self._heal_due()
        ts_config.set("heal_prompt_nag", False)
        self.assertIsNone(nudges.heal_line(t, "sess-1"))

    def test_the_env_escape_wins_over_config(self):
        t = self._heal_due()
        ts_config.set("heal_prompt_nag", True)
        os.environ["TASK_STATION_HEAL_PROMPT_NAG"] = "off"
        self.assertIsNone(nudges.heal_line(t, "sess-1"))

    def test_persist_false_writes_no_watermark(self):
        """The read-only form every nag in this codebase offers, so a surface can ask
        "would this fire?" without spending the session's one line."""
        t = self._heal_due()
        self.assertIsNotNone(nudges.heal_line(t, "sess-1", persist=False))
        self.assertEqual(self._gate(t), {})
        self.assertIsNotNone(nudges.heal_line(t, "sess-1"))

    def test_it_does_not_mutate_the_task(self):
        t = self._heal_due()
        before = json.dumps(t, sort_keys=True, default=str)
        nudges.heal_line(t, "sess-1")
        self.assertEqual(before, json.dumps(t, sort_keys=True, default=str))


# ---------------------------------------------------------------------------
# (c) the save-staleness nudge
# ---------------------------------------------------------------------------

class TestSaveNudge(_Base):
    def test_a_never_checkpointed_task_is_never_nudged(self):
        """A brand-new task is NOT stale — its digest is empty by definition, and nudging
        it would put a line on every task the moment it was created. `/todo save`'s own gap
        report already covers this case, worded as the fact it is."""
        t = self._task(decisions=self._decisions(40),
                       history=[{"ts": ts._iso(time.time()), "text": "entry %d" % i}
                                for i in range(40)])
        self.assertTrue(ts_save.since_checkpoint(t)["never"])
        self.assertEqual(nudges.save_staleness(t), (None, None))
        self.assertIsNone(nudges.save_line(t, "sess-1"))

    def test_it_fires_on_volume(self):
        t = self._checkpointed(age_hours=1, decisions=4, history=2)   # 6 >= 6
        limb, _reason = nudges.save_staleness(t)
        self.assertEqual(limb, nudges.LIMB_VOLUME)
        line = nudges.save_line(t, "sess-1")
        self.assertIn("#%s's checkpoint is going stale" % t["seq"], line)
        self.assertIn("4 decision(s) and 2 log entries", line)
        self.assertIn("/todo save", line)

    def test_it_stays_silent_just_under_the_volume_threshold(self):
        t = self._checkpointed(age_hours=1, decisions=3, history=2)   # 5 < 6
        self.assertEqual(nudges.save_staleness(t), (None, None))
        self.assertIsNone(nudges.save_line(t, "sess-1"))

    def test_it_fires_on_age_with_one_decision(self):
        t = self._checkpointed(age_hours=13, decisions=1)
        limb, _reason = nudges.save_staleness(t)
        self.assertEqual(limb, nudges.LIMB_AGE)
        self.assertIn("13h ago and 1 decision(s)", nudges.save_line(t, "sess-1"))

    def test_age_alone_never_nudges(self):
        """The conjunction is the point: hours measure the CLOCK, not the work. A task left
        open over a weekend with NOTHING recorded against it is not a stale digest, and
        nudging it would be nagging the user for having gone home."""
        t = self._checkpointed(age_hours=400, decisions=0, history=0)
        self.assertEqual(nudges.save_staleness(t), (None, None))
        self.assertIsNone(nudges.save_line(t, "sess-1"))

    def test_log_entries_alone_still_count_as_volume(self):
        """The volume limb counts decisions AND dated log entries, so a long-parked task
        whose log filled up is stale even with no new decisions — the digest does not cover
        what the log recorded. Only the AGE limb demands a decision, and only because hours
        on their own measure nothing."""
        t = self._checkpointed(age_hours=400, decisions=0, history=9)
        limb, reason = nudges.save_staleness(t)
        self.assertEqual(limb, nudges.LIMB_VOLUME)
        self.assertIn("0 decision(s) and 9 log entries", reason)

    def test_it_stays_silent_under_the_age_threshold(self):
        t = self._checkpointed(age_hours=3, decisions=1)
        self.assertEqual(nudges.save_staleness(t), (None, None))

    def test_an_uncountable_baseline_is_never_stale(self):
        """`heal.goal_review`'s rule and `save`'s own: no baseline means CANNOT BE COUNTED,
        never zero and never stale. Every task stamped by a version older than
        `saved_counts` takes this path, so it is the common case, not an edge one."""
        t = self._checkpointed(age_hours=500, decisions=30, baseline=False)
        self.assertFalse(ts_save.since_checkpoint(t)["known"])
        self.assertEqual(nudges.save_staleness(t), (None, None))
        self.assertIsNone(nudges.save_line(t, "sess-1"))

    def test_it_stays_silent_on_a_closed_task(self):
        t = self._checkpointed(age_hours=1, decisions=9, status=ts.STATUS_CLOSED)
        self.assertIsNone(nudges.save_line(t, "sess-1"))

    def test_it_fires_once_per_session(self):
        t = self._checkpointed(age_hours=1, decisions=9)
        self.assertIsNotNone(nudges.save_line(t, "sess-1"))
        self.assertIsNone(nudges.save_line(t, "sess-1"))

    def test_more_of_the_same_limb_does_not_re_arm_it(self):
        t = self._checkpointed(age_hours=1, decisions=9)
        self.assertIsNotNone(nudges.save_line(t, "sess-1"))
        t["decisions"] = self._decisions(40)
        ts.save_task(t)
        self.assertIsNone(nudges.save_line(ts.load_task(t["id"]), "sess-1"))

    def test_the_two_nudges_do_not_silence_each_other(self):
        """Separate gate keys, because they answer separate questions. Sharing one would
        mean hearing about a due heal permanently silenced the staleness warning."""
        t = self._checkpointed(age_hours=1, decisions=heal.DUE_NEW_DECISIONS)
        self.assertIsNotNone(nudges.heal_line(t, "sess-1"))
        self.assertIsNotNone(nudges.save_line(t, "sess-1"))

    def test_config_off_silences_it(self):
        t = self._checkpointed(age_hours=1, decisions=9)
        ts_config.set("save_nudge", False)
        self.assertIsNone(nudges.save_line(t, "sess-1"))

    def test_the_env_escape_wins_over_config(self):
        t = self._checkpointed(age_hours=1, decisions=9)
        ts_config.set("save_nudge", True)
        os.environ["TASK_STATION_SAVE_NUDGE"] = "off"
        self.assertIsNone(nudges.save_line(t, "sess-1"))

    def test_it_does_not_mutate_the_task(self):
        t = self._checkpointed(age_hours=1, decisions=9)
        before = json.dumps(t, sort_keys=True, default=str)
        nudges.save_line(t, "sess-1")
        self.assertEqual(before, json.dumps(t, sort_keys=True, default=str))


# ---------------------------------------------------------------------------
# (d) the thresholds — positive-only, env escape, reset
# ---------------------------------------------------------------------------

class TestThresholds(_Base):
    def test_the_defaults_are_the_documented_ones(self):
        self.assertEqual((nudges.save_nudge_decisions(), nudges.save_nudge_hours()),
                         (6, 12))

    def test_config_retunes_the_volume_threshold(self):
        ts_config.set("save_nudge_decisions", 3)
        self.assertEqual(nudges.save_nudge_decisions(), 3)
        t = self._checkpointed(age_hours=1, decisions=3)      # 3 >= 3 now
        self.assertEqual(nudges.save_staleness(t)[0], nudges.LIMB_VOLUME)

    def test_the_env_escape_wins_over_config_for_the_counts(self):
        ts_config.set("save_nudge_decisions", 20)
        os.environ["TASK_STATION_SAVE_NUDGE_DECISIONS"] = "2"
        self.assertEqual(nudges.save_nudge_decisions(), 2)
        ts_config.set("save_nudge_hours", 99)
        os.environ["TASK_STATION_SAVE_NUDGE_HOURS"] = "4"
        self.assertEqual(nudges.save_nudge_hours(), 4)

    def test_the_env_escape_retunes_the_age_threshold_end_to_end(self):
        os.environ["TASK_STATION_SAVE_NUDGE_HOURS"] = "2"
        t = self._checkpointed(age_hours=3, decisions=1)
        self.assertEqual(nudges.save_staleness(t)[0], nudges.LIMB_AGE)

    def test_a_zero_or_negative_override_is_refused_back_to_the_default(self):
        """A tunable whose extreme value breaks the feature is a footgun — the checker's
        rule. Zero decisions would nudge every attached task on every single prompt."""
        for bad in (0, -5, "nonsense", ""):
            ts_config.set("save_nudge_decisions", bad)
            self.assertEqual(nudges.save_nudge_decisions(), nudges.SAVE_NUDGE_DECISIONS)
            ts_config.set("save_nudge_hours", bad)
            self.assertEqual(nudges.save_nudge_hours(), nudges.SAVE_NUDGE_HOURS)

    def test_the_flags_are_cleared_by_a_config_reset(self):
        for key in _CFG_KEYS:
            self.assertIn(key, ts_config.RESET_KEYS)

    def test_the_flags_appear_on_the_config_board(self):
        flags = [r[0] for r in ts_config.board_rows()]
        for flag in ("--heal-prompt-nag", "--save-nudge",
                     "--save-nudge-decisions", "--save-nudge-hours"):
            self.assertIn(flag, flags)

    def test_the_board_rows_report_the_current_values(self):
        ts_config.set("save_nudge", False)
        ts_config.set("save_nudge_decisions", 4)
        rows = {r[0]: r for r in ts_config.board_rows()}
        self.assertEqual(rows["--save-nudge"][1], "off")
        self.assertEqual(rows["--save-nudge-decisions"][1], "4")

    def test_both_toggles_default_on(self):
        self.assertTrue(ts_config.heal_prompt_nag_enabled())
        self.assertTrue(ts_config.save_nudge_enabled())


# ---------------------------------------------------------------------------
# (e) fail-open — a broken nudge must never be worse than silence
# ---------------------------------------------------------------------------

class TestFailOpen(_Base):
    def test_a_garbled_gate_file_does_not_raise(self):
        t = self._heal_due()
        os.makedirs(nudges.gate_dir(), exist_ok=True)
        with open(nudges.gate_path(t["id"]), "w", encoding="utf-8") as f:
            f.write("{not json at all")
        self.assertEqual(nudges.read_gate(t["id"]), {})
        self.assertIsNotNone(nudges.heal_line(t, "sess-1"))   # unreadable → speak

    def test_a_gate_file_holding_the_wrong_shape_does_not_raise(self):
        t = self._heal_due()
        os.makedirs(nudges.gate_dir(), exist_ok=True)
        with open(nudges.gate_path(t["id"]), "w", encoding="utf-8") as f:
            json.dump({nudges.HEAL_KEY: "not-a-dict"}, f)
        self.assertIsNotNone(nudges.heal_line(t, "sess-1"))

    def test_an_unwritable_gate_dir_still_yields_a_line(self):
        """A gate we cannot write means the nudge may repeat, which is the harmless
        direction. It must never mean an exception on the prompt rail."""
        t = self._heal_due()
        with open(os.path.join(self.tmp, nudges.GATE_DIR), "w") as f:
            f.write("")           # a FILE where the gate dir should be → makedirs fails
        self.assertIsNotNone(nudges.heal_line(t, "sess-1"))

    def test_a_raising_config_yields_silence_not_an_exception(self):
        t = self._heal_due()
        broken = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        real = ts_config.heal_prompt_nag_enabled
        ts_config.heal_prompt_nag_enabled = broken
        try:
            self.assertIsNone(nudges.heal_line(t, "sess-1"))
        finally:
            ts_config.heal_prompt_nag_enabled = real

    def test_a_raising_threshold_reader_falls_back_to_the_default(self):
        real = ts_config.save_nudge_decisions
        ts_config.save_nudge_decisions = lambda: (_ for _ in ()).throw(RuntimeError("x"))
        try:
            self.assertEqual(nudges.save_nudge_decisions(),
                             nudges.SAVE_NUDGE_DECISIONS)
        finally:
            ts_config.save_nudge_decisions = real

    def test_a_none_task_is_silent_on_both(self):
        self.assertIsNone(nudges.heal_line(None, "sess-1"))
        self.assertIsNone(nudges.save_line(None, "sess-1"))

    def test_the_session_watermarks_are_bounded(self):
        """A long-lived task is touched by many sessions — hubs, workers, every resume —
        so the map is capped rather than growing a gate file forever."""
        t = self._heal_due()
        for i in range(nudges.GATE_SESSIONS_MAX + 12):
            nudges.heal_line(t, "sess-%03d" % i)
        seen = self._gate(t).get(nudges.HEAL_KEY)
        self.assertEqual(len(seen), nudges.GATE_SESSIONS_MAX)

    def test_clear_gate_re_arms_both_nudges(self):
        t = self._checkpointed(age_hours=1, decisions=heal.DUE_NEW_DECISIONS)
        self.assertIsNotNone(nudges.heal_line(t, "sess-1"))
        self.assertIsNotNone(nudges.save_line(t, "sess-1"))
        self.assertTrue(nudges.clear_gate(t["id"]))
        self.assertIsNotNone(nudges.heal_line(t, "sess-1"))
        self.assertIsNotNone(nudges.save_line(t, "sess-1"))

    def test_clear_gate_on_a_task_with_no_gate_is_false_not_an_error(self):
        self.assertFalse(nudges.clear_gate("never-had-one"))


# ---------------------------------------------------------------------------
# (f) the UserPromptSubmit rail — where the lines actually land
# ---------------------------------------------------------------------------

class TestPromptRail(_Base):
    def _prompt(self, session, prompt="keep going on the same thing"):
        os.environ["TASK_STATION_PROMPT"] = prompt
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_prompt_context(_Args(session=session, prompt=prompt))
        return buf.getvalue()

    def _attach(self, task, session):
        ts.set_link(session, task["id"])

    def test_the_heal_nudge_reaches_the_rail_once(self):
        t = self._heal_due()
        self._attach(t, "sess-rail-1")
        out = self._prompt("sess-rail-1")
        self.assertIn("heal is due on #%s" % t["seq"], out)
        self.assertNotIn("heal is due", self._prompt("sess-rail-1"))

    def test_the_save_nudge_reaches_the_rail_once(self):
        t = self._checkpointed(age_hours=1, decisions=4, history=2)
        self._attach(t, "sess-rail-2")
        out = self._prompt("sess-rail-2")
        self.assertIn("checkpoint is going stale", out)
        self.assertNotIn("checkpoint is going stale", self._prompt("sess-rail-2"))

    def test_a_healthy_attached_task_emits_nothing_at_all(self):
        """SILENT WHEN HEALTHY, asserted on the real rail rather than on the helper: this
        is the path that runs on every prompt, and the guarantee is that a healthy task
        costs it zero output."""
        t = self._healthy()
        self._attach(t, "sess-rail-3")
        self.assertEqual(self._prompt("sess-rail-3"), "")

    def test_heal_is_named_before_save_when_both_fire(self):
        """The order the save gate itself argues for: a summary written from an
        unreconciled decision set bakes the drift into the first field anyone reads."""
        t = self._checkpointed(age_hours=1, decisions=heal.DUE_NEW_DECISIONS)
        self._attach(t, "sess-rail-4")
        out = self._prompt("sess-rail-4")
        self.assertLess(out.index("heal is due"), out.index("checkpoint is going stale"))

    def test_both_off_leaves_the_rail_silent_on_a_due_task(self):
        t = self._checkpointed(age_hours=1, decisions=heal.DUE_NEW_DECISIONS)
        self._attach(t, "sess-rail-5")
        ts_config.set("heal_prompt_nag", False)
        ts_config.set("save_nudge", False)
        self.assertEqual(self._prompt("sess-rail-5"), "")

    def test_an_unattached_session_is_never_nudged(self):
        """Both nudges are about the ATTACHED task. The unattached rail is the
        attach/create nudge and nothing here may add to it."""
        out = self._prompt("sess-rail-none")
        self.assertNotIn("heal is due", out)
        self.assertNotIn("checkpoint is going stale", out)


# ---------------------------------------------------------------------------
# (g) the [SAVE] gate names its first finding
# ---------------------------------------------------------------------------

class TestSaveGateNamesTheFinding(_Base):
    def test_first_finding_line_is_none_on_a_clean_scan(self):
        self.assertIsNone(heal.first_finding_line(heal.scan(self._healthy())))
        self.assertIsNone(heal.first_finding_line({}))
        self.assertIsNone(heal.first_finding_line(None))

    def test_it_names_the_ref_and_the_detail(self):
        t = self._task(memos=self._undispositioned_ack())
        line = heal.first_finding_line(heal.scan(t))
        self.assertIsNotNone(line)
        self.assertIn("memo abcd1234", line)
        self.assertIn("acked with NO disposition", line)

    def test_it_is_cut_to_one_short_line(self):
        t = self._task(memos=self._undispositioned_ack())
        line = heal.first_finding_line(heal.scan(t))
        self.assertLessEqual(len(line), previews.RECOGNISE)
        self.assertNotIn("\n", line)
        self.assertTrue(line.endswith("…"))     # the fix command is NOT half-printed

    def test_a_shorter_finding_is_not_truncated(self):
        t = self._task(memos=self._undispositioned_ack())
        line = heal.first_finding_line(heal.scan(t), limit=4000)
        self.assertFalse(line.endswith("…"))

    def test_gate_line_reuses_a_supplied_scan_instead_of_running_its_own(self):
        t = self._task(memos=self._undispositioned_ack())
        result = heal.scan(t)
        self.assertEqual(heal.gate_line(t, result=result), heal.gate_line(t))

    def test_the_save_block_carries_the_named_finding_in_parentheses(self):
        t = self._task(memos=self._undispositioned_ack(),
                       decisions=self._decisions(3))
        ts.set_link("sess-save", t["id"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_render(_Args(session="sess-save", arg="save"))
        out = buf.getvalue()
        self.assertIn("heal first", out)
        self.assertIn("the scan found", out)
        self.assertIn("(first: memo abcd1234", out)

    def test_the_save_block_omits_the_parenthetical_when_nothing_was_found(self):
        """The gate can be due on a limb that is NOT a finding (a never-healed task with
        decisions on the log). There is then no finding to name, and inventing one would be
        the cry-wolf failure this module has already had to fix four times."""
        t = self._heal_due(status=ts.STATUS_ACTIVE)
        self.assertEqual(heal.scan(t)["findings"], [])
        ts.set_link("sess-save-2", t["id"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_render(_Args(session="sess-save-2", arg="save"))
        out = buf.getvalue()
        self.assertIn("heal first", out)
        self.assertNotIn("(first:", out)


if __name__ == "__main__":
    unittest.main()
