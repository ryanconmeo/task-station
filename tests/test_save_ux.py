"""The CHECKPOINT pass — `save`: the gap report, the preserved summary, the honest stamp.

`save` is the capture half of the pair `heal` reconciles, and it carried three of that
module's measured failures plus one worse one of its own:

  * IT ECHOED THE DIGEST BACK. On one real task `/save` emitted 71,516 characters and
    71,271 of them — 99.7% — were a dump of the CURRENT DIGEST, all 62 decisions
    included, handed to the session that had just written every one of them. It is
    replaced by a GAP REPORT: what is EMPTY, what is STALE, what has landed since the
    last checkpoint, and what the digest costs. `--verbose` still dumps.
  * A REPLACED SUMMARY WAS DESTROYED. `--summary` replaces wholesale and nothing kept
    the old text, while every other replace verb here (supersede / split / merge /
    step-supersede) keeps the original and offers a restore — and the summary is the
    FIRST field a resuming session reads.
  * THE STAMP WAS WRITTEN WHEN THE BLOCK WAS PRINTED. `/save`, write nothing, and
    `last_full_save_ts` said the task had been fully checkpointed with an empty
    summary. That is heal's zero-operation `--apply` one layer earlier.

THE HARD RULES UNDER TEST: the block reports the GAP and never the digest; no write is
destructive, and every replacement is restorable; and a stamp is a claim about work, so
only work writes one — the block never stamps and `--check` never mutates.

Isolation copies the `_repoint` idiom from tests/test_heal.py.
"""
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(_REPO_ROOT, "lib")
sys.path.insert(0, LIB)

_TMP_HOME = tempfile.mkdtemp(prefix="ts-save-")
os.environ["TASK_STATION_HOME"] = _TMP_HOME

import save as sv        # noqa: E402
import store             # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


def _repoint(tmp):
    os.environ["TASK_STATION_HOME"] = tmp
    ts.DATA = tmp
    ts.STORE = os.path.join(tmp, "store")
    ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
    ts.LINKS_DIR = os.path.join(ts.STORE, "links")
    store.reset_cache()


# A summary distinctive enough that finding it in the output proves the digest was
# dumped — no fragment of it appears anywhere in the gap report's own wording.
GOOD_SUMMARY = ("lib/widget.py drives the projectname importer; branch `2707-rollup`; "
                "run `make check`; watch out for the OtherProj clock skew")
THIN_SUMMARY = "wip"


class _SaveArgs:
    """render-namespace stand-in for the `/todo save` handler: `.session` only."""
    def __init__(self, session=None):
        self.session = session


class _RenderArgs:
    def __init__(self, session=None, arg="", fmt=None):
        self.session = session
        self.arg = arg
        self.format = fmt


class _UpdateArgs:
    def __init__(self, **kw):
        defaults = dict(task=None, title=None, summary=None, append_summary=None,
                        restore_summary=None, state=None, goal=None, step_add=None,
                        step_done=None, step_undone=None, step_supersede=None,
                        step_restore=None, decision=None, supersedes=None, pin=False,
                        pin_decision=None, unpin_decision=None, restore_decision=None,
                        log=None, pr=None, pr_desc=None, story=None, story_desc=None,
                        color=None, effort=None, trail_visibility=None, relate=None,
                        session=None)
        defaults.update(kw)
        self.__dict__.update(defaults)


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _repoint(self.tmp)

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- fixtures --------------------------------------------------------------

    def _task(self, title="A task", summary="", attach="sess-save", **fields):
        t = ts.new_task(title, summary)
        t.update(fields)
        ts.save_task(t)
        ts.ensure_seqs()
        if attach:
            ts.set_link(attach, t["id"])
        return ts.load_task(t["id"])

    def _reload(self, t):
        return ts.load_task(t["id"])

    def _save(self, t=None, rest="", session="sess-save"):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts._todo_save(_SaveArgs(session=session), rest)
        return buf.getvalue()

    def _update(self, t, **kw):
        # The repeatable flags are argparse `action="append"`, so they are ALWAYS lists
        # on the namespace — a bare string would be iterated one character at a time.
        for flag in ("decision", "log", "step_add", "pr", "story", "supersedes"):
            if isinstance(kw.get(flag), str):
                kw[flag] = [kw[flag]]
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_update(_UpdateArgs(task=str(t["seq"]), **kw))
        return buf.getvalue()


# ---------------------------------------------------------------------------
# BUG 1 — the block reported the digest back to the session that wrote it.
# ---------------------------------------------------------------------------

class TestGapReportReplacesTheDump(_Base):
    def test_the_block_does_not_reprint_the_digest(self):
        t = self._task(summary=GOOD_SUMMARY, goal="ship the importer",
                       state="NEXT: wire the parser", decisions=["chose sqlite"])
        out = self._save(t)
        self.assertIn("[SAVE]", out)
        self.assertIn("GAP REPORT", out)
        self.assertNotIn(GOOD_SUMMARY, out)          # the summary is NOT echoed back
        self.assertNotIn("chose sqlite", out)        # nor is the decision set
        self.assertNotIn("CURRENT DIGEST", out)

    def test_verbose_restores_the_full_dump(self):
        t = self._task(summary=GOOD_SUMMARY, decisions=["chose sqlite"])
        out = self._save(t, rest="--verbose")
        self.assertIn("CURRENT DIGEST (--verbose)", out)
        self.assertIn(GOOD_SUMMARY, out)
        self.assertIn("chose sqlite", out)
        self.assertIn("GAP REPORT", out)             # …and the gap report is still there

    def test_the_default_block_is_a_fraction_of_the_verbose_one(self):
        """The whole point, measured: on a decision-heavy task the dump dwarfs the
        report, and the report is what the caller actually needed."""
        t = self._task(summary=GOOD_SUMMARY,
                       decisions=["decision %d: %s" % (i, "x" * 800) for i in range(30)])
        default = self._save(t)
        verbose = self._save(t, rest="--verbose")
        self.assertLess(len(default) * 2, len(verbose))

    def test_every_empty_named_slot_is_named(self):
        t = self._task()                              # nothing filled in at all
        out = self._save(t)
        self.assertIn("EMPTY (6 of 6 named slots)", out)
        for slot in ("goal", "state", "summary", "steps", "decisions", "links"):
            self.assertIn(slot, out)

    def test_a_filled_slot_is_not_reported_empty(self):
        t = self._task(summary=GOOD_SUMMARY, goal="ship it",
                       state="NEXT: wire the parser",
                       steps=[{"text": "write the parser", "done": False}],
                       decisions=["chose sqlite"],
                       prs=[{"url": "https://example.invalid/pr/1", "desc": ""}])
        report = sv.gap_report(self._reload(t))
        self.assertEqual(report["empty"], [])

    def test_a_task_with_nothing_missing_says_so_rather_than_going_quiet(self):
        t = self._task(summary=GOOD_SUMMARY, goal="ship it",
                       state="NEXT: wire the parser",
                       steps=[{"text": "write the parser", "done": False}],
                       decisions=["chose sqlite"],
                       stories=[{"url": "https://example.invalid/story/1", "desc": ""}])
        out = self._save(t)
        self.assertIn("EMPTY", out)
        self.assertIn("none — all 6 named slots carry something", out)

    def test_the_digest_size_is_reported(self):
        t = self._task(summary=GOOD_SUMMARY)
        out = self._save(t)
        self.assertIn("DIGEST SIZE", out)
        self.assertIn("a fresh session loads", out)
        self.assertIn("tokens", out)


class TestStalenessChecks(_Base):
    def test_a_state_not_leading_with_next_is_stale(self):
        t = self._task(state="the parser is half written")
        report = sv.gap_report(self._reload(t))
        self.assertEqual([g["slot"] for g in report["stale"]], ["state"])
        self.assertIn("NEXT:", report["stale"][0]["detail"])

    def test_a_state_leading_with_next_is_not_stale(self):
        t = self._task(state="NEXT: wire the parser — half written")
        self.assertEqual(sv.gap_report(self._reload(t))["stale"], [])

    def test_leads_with_next_is_case_and_space_tolerant(self):
        self.assertTrue(sv.leads_with_next("  next: do the thing"))
        self.assertTrue(sv.leads_with_next("NEXT: do the thing"))
        self.assertFalse(sv.leads_with_next("we will do the thing NEXT: soon"))

    def test_a_summary_older_than_the_decisions_it_must_cover_is_stale(self):
        t = self._task(summary=GOOD_SUMMARY, state="NEXT: keep going")
        self._update(t, summary=GOOD_SUMMARY + " (rewritten)")   # snapshot the baseline
        for i in range(4):
            self._update(self._reload(t), decision="decision %d" % i)
        report = sv.gap_report(self._reload(t))
        self.assertIn("summary", [g["slot"] for g in report["stale"]])
        self.assertIn("4 decision(s)", " ".join(g["detail"] for g in report["stale"]))

    def test_a_summary_written_alongside_its_own_decisions_is_not_stale(self):
        """THE ORDERING TRAP: the snapshot is taken AFTER this update's own appends, so
        a summary written in the same call as three decisions is the freshest the task
        has ever had — not three decisions out of date."""
        t = self._task(state="NEXT: keep going")
        self._update(t, summary=GOOD_SUMMARY,
                     decision=["one", "two", "three"], log=["shipped 1.0.0"])
        report = sv.gap_report(self._reload(t))
        self.assertNotIn("summary", [g["slot"] for g in report["stale"]])

    def test_a_state_nothing_moved_is_stale(self):
        t = self._task(summary=GOOD_SUMMARY)
        self._update(t, state="NEXT: wire the parser")
        for i in range(sv.STALE_STATE_ENTRIES):
            self._update(self._reload(t), decision="decision %d" % i)
        report = sv.gap_report(self._reload(t))
        stale = dict((g["slot"], g["detail"]) for g in report["stale"])
        self.assertIn("state", stale)
        self.assertIn("unchanged", stale["state"])

    def test_rewriting_the_same_state_text_does_not_refresh_it(self):
        """Copying a stale NEXT forward must not buy it another six entries."""
        t = self._task(summary=GOOD_SUMMARY)
        self._update(t, state="NEXT: wire the parser")
        for i in range(sv.STALE_STATE_ENTRIES):
            self._update(self._reload(t), decision="decision %d" % i)
        self._update(self._reload(t), state="NEXT: wire the parser")   # identical text
        report = sv.gap_report(self._reload(t))
        self.assertIn("state", [g["slot"] for g in report["stale"]])

    def test_an_unjudgeable_age_is_silent(self):
        """A task written before this version has no snapshot to compare against, so it
        must read as "cannot tell" — inventing staleness for every one of them would
        make the block cry wolf on its first run."""
        t = self._task(summary=GOOD_SUMMARY, state="NEXT: keep going",
                       decisions=["a", "b", "c", "d", "e", "f", "g", "h"])
        self.assertEqual(sv.gap_report(self._reload(t))["stale"], [])

    def test_an_empty_slot_is_not_also_reported_stale(self):
        t = self._task()
        report = sv.gap_report(self._reload(t))
        self.assertIn("state", [g["slot"] for g in report["empty"]])
        self.assertEqual(report["stale"], [])


class TestTheFirstMoveSentence(_Base):
    """`next_line` — the NEXT: SENTENCE alone, without the standing report after it.

    MOVED HERE FROM tests/test_handoff_whole_sentence.py in 3.61.0, with the assertions
    unchanged. It was written for the relay, which called it to fit the first move inside
    a 320-character launch argument; the relay now writes the whole handoff to a FILE and
    sends the state line entire, so it has no caller left in `lib/`. The coverage moves
    to the module that owns the function rather than being deleted with the cap it used
    to serve — the sentence boundary is the non-obvious part, and a future caller
    (a digest line, a board row) would want it correct rather than rediscovered.
    """

    def test_the_standing_report_after_the_move_is_dropped(self):
        state = ("NEXT: do the one thing that matters. Standing: everything else is "
                 "fine.")
        self.assertEqual(sv.next_line(state), "NEXT: do the one thing that matters.")

    def test_only_the_first_paragraph_is_considered(self):
        state = "NEXT: do the thing.\n\nA whole second paragraph of standing report."
        self.assertEqual(sv.next_line(state), "NEXT: do the thing.")

    def test_a_version_number_does_not_end_the_sentence(self):
        """A move cut at `3.` is worse than no fix at all — a terminator counts only
        when whitespace follows it."""
        self.assertEqual(sv.next_line("NEXT: ship 3.57.0 to main, then verify. "
                                      "Standing."),
                         "NEXT: ship 3.57.0 to main, then verify.")

    def test_a_filename_does_not_end_the_sentence(self):
        self.assertEqual(sv.next_line("NEXT: patch succession.py and re-run. Standing."),
                         "NEXT: patch succession.py and re-run.")

    def test_an_early_abbreviation_does_not_end_the_sentence(self):
        # `e.g.` DOES have a space after it, so the whitespace rule cannot catch it —
        # the length floor is what does.
        line = sv.next_line("NEXT: fix e.g. this one thing. Then everything else.")
        self.assertTrue(line.startswith("NEXT: fix e.g. this one thing"))

    def test_a_state_with_no_next_prefix_has_no_first_move(self):
        self.assertEqual(sv.next_line("Standing report with no move at all. " * 40), "")


class TestSinceTheLastCheckpoint(_Base):
    def test_never_checkpointed_says_so_and_does_not_call_the_log_new(self):
        t = self._task(summary=GOOD_SUMMARY, decisions=["a", "b", "c"])
        out = self._save(t)
        self.assertIn("never fully checkpointed", out)
        self.assertNotIn("+3 decisions", out)

    def test_what_landed_since_the_stamp_is_counted(self):
        t = self._task()
        self._update(t, summary=GOOD_SUMMARY, state="NEXT: wire the parser")  # stamps
        self._update(self._reload(t), decision=["one", "two"], step_add=["do a thing"],
                     log=["shipped 1.0.0"])
        out = self._save(self._reload(t))
        self.assertIn("+2 decisions", out)
        self.assertIn("+1 step", out)
        self.assertIn("+1 log entry", out)
        self.assertIn("last full checkpoint", out)

    def test_a_stamp_with_no_baseline_admits_it_cannot_count(self):
        """An older version stamped without recording counts. Reporting the totals as
        "since" would be a made-up number; saying so is the honest answer."""
        t = self._task(summary=GOOD_SUMMARY, decisions=["a", "b"])
        raw = self._reload(t)
        raw["last_full_save_ts"] = ts._now() - 3600
        ts.save_task(raw)
        line = sv.since_line(sv.since_checkpoint(self._reload(t)))
        self.assertIn("cannot be counted", line)


# ---------------------------------------------------------------------------
# BUG 2 — a replaced summary was destroyed, with no backup and no restore.
# ---------------------------------------------------------------------------

class TestSummaryIsNonDestructive(_Base):
    def test_replacing_a_summary_preserves_the_old_text(self):
        t = self._task(summary=GOOD_SUMMARY)
        self._update(t, summary=THIN_SUMMARY)
        after = self._reload(t)
        self.assertEqual(after["summary"], THIN_SUMMARY)
        self.assertEqual([v["text"] for v in sv.summary_versions(after)],
                         [GOOD_SUMMARY])

    def test_the_replaced_summary_is_retrievable_from_history(self):
        t = self._task(summary=GOOD_SUMMARY)
        self._update(t, summary=THIN_SUMMARY)
        view = ts._format_history(self._reload(t))
        self.assertIn("Summary versions (1 preserved", view)
        self.assertIn(GOOD_SUMMARY, view)

    def test_the_preserved_summary_never_reaches_the_resume_digest(self):
        t = self._task(summary=GOOD_SUMMARY)
        self._update(t, summary=THIN_SUMMARY)
        detail = ts._format_detail(self._reload(t), None)
        self.assertIn(THIN_SUMMARY, detail)
        self.assertNotIn(GOOD_SUMMARY, detail)

    def test_the_update_names_the_restore_path(self):
        t = self._task(summary=GOOD_SUMMARY)
        out = self._update(t, summary=THIN_SUMMARY)
        self.assertIn("PRESERVED as version 1", out)
        self.assertIn("--restore-summary", out)
        self.assertIn("Nothing was lost", out)

    def test_restore_brings_the_previous_summary_back(self):
        t = self._task(summary=GOOD_SUMMARY)
        self._update(t, summary=THIN_SUMMARY)
        out = self._update(self._reload(t), restore_summary="")
        self.assertEqual(self._reload(t)["summary"], GOOD_SUMMARY)
        self.assertIn("restored summary version 1", out)

    def test_restore_can_name_an_older_version(self):
        t = self._task(summary="v1")
        self._update(t, summary="v2")
        self._update(self._reload(t), summary="v3")
        self._update(self._reload(t), restore_summary="1")
        self.assertEqual(self._reload(t)["summary"], "v1")

    def test_restore_is_itself_reversible(self):
        """Nothing is destroyed by the restore either: the text it replaces is preserved
        too, so the restore can be undone the same way."""
        t = self._task(summary=GOOD_SUMMARY)
        self._update(t, summary=THIN_SUMMARY)
        self._update(self._reload(t), restore_summary="")
        after = self._reload(t)
        self.assertEqual(after["summary"], GOOD_SUMMARY)
        self.assertIn(THIN_SUMMARY, [v["text"] for v in sv.summary_versions(after)])

    def test_an_identical_rewrite_is_not_a_version(self):
        t = self._task(summary=GOOD_SUMMARY)
        self._update(t, summary=GOOD_SUMMARY)
        self.assertEqual(sv.summary_versions(self._reload(t)), [])

    def test_replacing_a_blank_summary_preserves_nothing(self):
        t = self._task(summary="")
        self._update(t, summary=GOOD_SUMMARY)
        self.assertEqual(sv.summary_versions(self._reload(t)), [])

    def test_restore_with_nothing_preserved_is_a_clear_error(self):
        t = self._task(summary=GOOD_SUMMARY)
        out = self._update(t, restore_summary="")
        self.assertIn("no preserved summary", out)
        self.assertEqual(self._reload(t)["summary"], GOOD_SUMMARY)

    def test_restore_out_of_range_is_a_clear_error(self):
        t = self._task(summary=GOOD_SUMMARY)
        self._update(t, summary=THIN_SUMMARY)
        out = self._update(self._reload(t), restore_summary="9")
        self.assertIn("no such version", out)
        self.assertEqual(self._reload(t)["summary"], THIN_SUMMARY)

    def test_the_save_block_names_the_restore_path(self):
        t = self._task(summary=GOOD_SUMMARY)
        self.assertIn("--restore-summary", self._save(t))


# ---------------------------------------------------------------------------
# BUG 3 — the stamp was written when the block was EMITTED, not when it was written.
# ---------------------------------------------------------------------------

class TestTheStampMeansSomething(_Base):
    def test_emitting_the_block_does_not_stamp(self):
        t = self._task()
        self._save(t)
        after = self._reload(t)
        self.assertIsNone(after.get("last_full_save_ts"))
        self.assertEqual((after.get("summary") or ""), "")    # nothing was captured

    def test_emitting_the_block_records_that_a_save_was_started(self):
        t = self._task()
        self._save(t)
        self.assertIsInstance(self._reload(t).get("save_started_ts"), (int, float))

    def test_the_block_says_it_did_not_stamp(self):
        t = self._task()
        out = self._save(t)
        self.assertIn("THIS BLOCK DID NOT STAMP A CHECKPOINT", out)

    def test_a_summary_and_a_state_together_stamp(self):
        t = self._task()
        self._update(t, summary=GOOD_SUMMARY, state="NEXT: wire the parser")
        self.assertIsInstance(self._reload(t).get("last_full_save_ts"), (int, float))

    def test_a_state_refresh_alone_does_not_stamp(self):
        t = self._task(summary=GOOD_SUMMARY)
        self._update(t, state="NEXT: wire the parser")
        self.assertIsNone(self._reload(t).get("last_full_save_ts"))

    def test_a_summary_alone_does_not_stamp(self):
        t = self._task(state="NEXT: wire the parser")
        self._update(t, summary=GOOD_SUMMARY)
        self.assertIsNone(self._reload(t).get("last_full_save_ts"))

    def test_append_summary_does_not_stamp(self):
        """Only the wholesale `--summary` asserts "this is the present truth", and that
        assertion is exactly what the stamp records."""
        t = self._task(summary=GOOD_SUMMARY)
        self._update(t, append_summary="…and a progress note",
                     state="NEXT: wire the parser")
        self.assertIsNone(self._reload(t).get("last_full_save_ts"))

    def test_a_blank_summary_does_not_stamp(self):
        t = self._task(summary=GOOD_SUMMARY)
        self._update(t, summary="", state="NEXT: wire the parser")
        self.assertIsNone(self._reload(t).get("last_full_save_ts"))

    def test_the_stamp_clears_the_started_marker(self):
        t = self._task()
        self._save(t)
        self._update(self._reload(t), summary=GOOD_SUMMARY, state="NEXT: go")
        self.assertNotIn("save_started_ts", self._reload(t))

    def test_the_stamp_records_the_baseline_it_counts_from(self):
        t = self._task(decisions=["a", "b"])
        self._update(t, summary=GOOD_SUMMARY, state="NEXT: go")
        counts = self._reload(t).get("saved_counts") or {}
        self.assertEqual(counts.get("decisions"), 2)
        self.assertIn("steps", counts)
        self.assertIn("history", counts)

    def test_the_stamping_update_runs_the_mechanical_cold_read_check(self):
        t = self._task()                              # goal/steps/decisions/links empty
        out = self._update(t, summary=GOOD_SUMMARY, state="NEXT: wire the parser")
        self.assertIn("CHECKPOINT STAMPED", out)
        self.assertIn("COLD-READ CHECK", out)
        self.assertIn("FAIL", out)
        self.assertIn("goal", out)

    def test_the_cold_read_check_passes_on_a_complete_task(self):
        t = self._task(goal="ship it",
                       steps=[{"text": "write the parser", "done": False}],
                       decisions=["chose sqlite"],
                       prs=[{"url": "https://example.invalid/pr/1", "desc": ""}])
        out = self._update(t, summary=GOOD_SUMMARY, state="NEXT: wire the parser")
        self.assertIn("COLD-READ CHECK: pass", out)

    def test_the_cold_read_check_catches_a_state_without_next(self):
        t = self._task(goal="ship it",
                       steps=[{"text": "write the parser", "done": False}],
                       decisions=["chose sqlite"],
                       prs=[{"url": "https://example.invalid/pr/1", "desc": ""}])
        out = self._update(t, summary=GOOD_SUMMARY, state="half written")
        self.assertIn("FAIL", out)
        self.assertIn("NEXT:", out)

    def test_a_non_checkpoint_update_says_nothing_about_checkpoints(self):
        t = self._task(summary=GOOD_SUMMARY)
        out = self._update(t, decision="chose sqlite")
        self.assertNotIn("CHECKPOINT STAMPED", out)
        self.assertNotIn("COLD-READ CHECK", out)


class TestCheckIsReadOnly(_Base):
    def test_check_prints_the_gap_report_and_nothing_else(self):
        t = self._task()
        out = self._save(t, rest="--check")
        self.assertIn("COLD-READ CHECK", out)
        self.assertIn("EMPTY (6 of 6 named slots)", out)
        self.assertNotIn("CAPTURE CHECKLIST", out)
        self.assertNotIn("Command templates", out)

    def test_check_changes_nothing(self):
        """A verification pass that mutates what it verifies is the bug this release
        exists to fix — it would print "nothing was changed" having changed something."""
        t = self._task(digest_dirty=True, pressure_nudged=True)
        before = self._reload(t)
        self._save(t, rest="--check")
        after = self._reload(t)
        self.assertIsNone(after.get("last_full_save_ts"))
        self.assertNotIn("save_started_ts", after)
        self.assertTrue(after.get("digest_dirty"))
        self.assertTrue(after.get("pressure_nudged"))
        self.assertEqual(after.get("updated_ts"), before.get("updated_ts"))

    def test_check_fails_a_task_with_gaps_and_passes_a_complete_one(self):
        t = self._task()
        self.assertIn("VERDICT: FAIL", self._save(t, rest="--check"))
        self._update(self._reload(t), goal="ship it", summary=GOOD_SUMMARY,
                     state="NEXT: wire the parser", step_add=["write the parser"],
                     decision="chose sqlite", pr="https://example.invalid/pr/1")
        self.assertIn("VERDICT: PASS", self._save(self._reload(t), rest="--check"))


class TestRoutingAndBackCompat(_Base):
    def test_the_flags_route_through_the_todo_dispatcher(self):
        t = self._task(summary=GOOD_SUMMARY)
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_render(_RenderArgs(session="sess-save", arg="save --verbose"))
        self.assertIn("CURRENT DIGEST (--verbose)", buf.getvalue())

    def test_trailing_free_text_is_still_ignored(self):
        """`/todo SAVE please checkpoint this` has always worked, which is why the flags
        are read by a token scan rather than by argparse."""
        t = self._task()
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_render(_RenderArgs(session="sess-save",
                                      arg="SAVE please checkpoint this"))
        out = buf.getvalue()
        self.assertIn("[SAVE]", out)
        self.assertIn("GAP REPORT", out)

    def test_a_task_written_by_an_older_version_reports_honestly(self):
        """No snapshots, no stamp, no version list — every one of them must read as
        "cannot tell" or "never", and none of them may crash the report."""
        t = self._task(summary=GOOD_SUMMARY, state="NEXT: go",
                       decisions=["a"], history=[{"ts": "2026-01-01T00:00:00+00:00",
                                                  "text": "shipped"}])
        report = sv.gap_report(self._reload(t))
        self.assertTrue(report["since"]["never"])
        self.assertEqual(report["stale"], [])
        self.assertEqual(sv.summary_versions(self._reload(t)), [])
        # …and the history view is byte-for-byte what it was before this section
        # existed: the block is data-gated, so a task with nothing preserved shows none.
        self.assertNotIn("Summary versions", ts._format_history(self._reload(t)))
        self.assertIn("GAP REPORT", self._save(t))


class TestSaveModulePrimitives(_Base):
    def test_is_checkpoint_write_needs_both_halves(self):
        self.assertTrue(sv.is_checkpoint_write("a summary", "NEXT: go"))
        self.assertFalse(sv.is_checkpoint_write("a summary", None))
        self.assertFalse(sv.is_checkpoint_write(None, "NEXT: go"))
        self.assertFalse(sv.is_checkpoint_write("   ", "NEXT: go"))
        self.assertFalse(sv.is_checkpoint_write("a summary", "   "))

    def test_push_summary_ignores_a_blank(self):
        task = {}
        self.assertIsNone(sv.push_summary(task, "   "))
        self.assertEqual(task.get(sv.VERSIONS_FIELD), None)

    def test_restore_defaults_to_the_most_recent(self):
        task = {"summary": "current"}
        sv.push_summary(task, "oldest")
        sv.push_summary(task, "newest")
        ok, err, n = sv.restore_summary(task)
        self.assertTrue(ok, err)
        self.assertEqual(n, 2)
        self.assertEqual(task["summary"], "newest")

    def test_the_gap_report_never_mutates_the_task(self):
        task = {"id": "abc", "seq": 1, "summary": GOOD_SUMMARY, "decisions": ["a"]}
        before = dict(task)
        sv.gap_report(task, digest_chars=100)
        self.assertEqual(task, before)


# ---------------------------------------------------------------------------
# THE OUTWARD IMPERATIVE — a state line that reads as an order to act outward.
#
# WHY IT IS CHECKED AT WRITE TIME. `relay --spawn` builds the successor's prompt out of
# this text, so a state line is not only a note to yourself — it is a sentence a session
# that was not there will read as an instruction. On 2026-08-29 one did: it woke holding
# `NEXT: WATCH PR 1615 AND MERGE IT`, was sent the same words again by peer message from
# the same predecessor, counted one voice as two agreeing, and merged another engineer's
# PR on a shared repo. The write is the last moment the session that knows WHO ASKED is
# still in the room, which is the same argument `memo send --corrects` rests on.
#
# THE HARD PART IS SILENCE, NOT DETECTION. Every state line on a PR-shaped task talks
# about merging; a check that fires on all of them is a check nobody reads. Measured
# over the 121 real state lines in the author's own store, the shipped rule flags 14% —
# and the discrimination that got it there is that only the BARE form of the verb, in a
# clause it opens, counts. `merged` is a report. `to merge` is an infinitive. `should
# merge` is an obligation. `merge-tree` is a filename.
# ---------------------------------------------------------------------------

class TestOutwardImperativeDetector(_Base):
    """The detector alone — `save.outward_imperatives`, no board, no CLI."""

    def test_the_incident_sentence_is_an_order(self):
        self.assertEqual(
            sv.outward_imperatives("NEXT: WATCH PR 1615 AND MERGE IT — it is the fix "
                                   "for the blocker and I have approved it (vote 10)."),
            ["merge"])

    def test_every_outward_verb_is_covered(self):
        for verb in ("merge", "approve", "abandon", "close", "delete", "revert",
                     "deploy", "force-push"):
            self.assertEqual(sv.outward_imperatives("NEXT: %s it" % verb), [verb], verb)

    def test_a_past_tense_report_is_silent(self):
        """The shape a GOOD state line uses, and the reason the check is believable:
        #444's and #532's own state lines report every merge in past tense and neither
        trips this."""
        for text in ("NEXT: merged PR 20; the CHANGELOG landed by hand.",
                     "NEXT: PR 19 is merged and PR 20 is approved — nothing outstanding.",
                     "NEXT: closing out; the branch was deleted after the deploy."):
            self.assertEqual(sv.outward_imperatives(text), [], text)

    def test_a_verb_somebody_else_owns_is_silent(self):
        for text in ("NEXT: wait for the reviewer to merge it.",
                     "NEXT: user to merge PR 1080 + delete orphan branch 3169.",
                     "NEXT: #444 should GATE and CLOSE it.",
                     "NEXT: PR 1413 needs review + merge.",
                     "NEXT: we merge only once Ryan says so.",
                     "NEXT: do not merge anything on this branch."):
            self.assertEqual(sv.outward_imperatives(text), [], text)

    def test_the_verb_as_a_noun_is_silent(self):
        for text in ("NEXT: resolve the merge conflict in CHANGELOG.md",
                     "NEXT: not a merge conflict you discover at merge time",
                     "NEXT: wait for PR 9 review/merge, then re-run the conditions",
                     "NEXT: run the merge-tree duplicate-V check",
                     "PR 1402 addressed (head 6603c66, merge=succeeded, no conflicts)",
                     "shipped via the 387 deploy flow"):
            self.assertEqual(sv.outward_imperatives(text), [], text)

    def test_an_order_strung_onto_another_still_counts(self):
        """The incident's exact grammar: the verb sits behind `AND`, not at the start.
        A rule that only looked at position 0 would have missed it."""
        self.assertEqual(sv.outward_imperatives("NEXT: rebase, then force-push it"),
                         ["force-push"])
        self.assertEqual(sv.outward_imperatives("NEXT: tag the release and deploy"),
                         ["deploy"])

    def test_it_never_raises_on_junk(self):
        for bad in (None, "", 17, "—", "\n\n"):
            self.assertEqual(sv.outward_imperatives(bad), [])


class TestOutwardImperativeWarnsAtWriteTime(_Base):
    """The lint on the WRITE — `update --state`, the bare path, no checkpoint needed."""

    INCIDENT = ("NEXT: WATCH PR 1615 AND MERGE IT — it is the fix for the blocker and "
                "I have approved it (vote 10).")

    def test_a_bare_state_write_warns_and_asks_who_authorised_it(self):
        """THE BARE PATH IS THE POINT. A `--state` with no `--summary` stamps no
        checkpoint and used to print nothing but `updated task N: state`, so the author
        of a sentence like this got no signal at all — and the first thing anybody
        noticed was a merged PR."""
        t = self._task(state="NEXT: keep going")
        out = self._update(t, state=self.INCIDENT)
        self.assertIn("OUTWARD IMPERATIVE", out)
        self.assertIn("`merge`", out)
        self.assertIn("WHO AUTHORISED", out)

    def test_it_warns_and_never_refuses(self):
        """Unlike `memo send --corrects`, which gates: a state line naming an outward
        action is often exactly right and merely needs its authority written down. The
        write must land either way."""
        t = self._task(state="NEXT: keep going")
        self._update(t, state=self.INCIDENT)
        self.assertEqual(self._reload(t)["state"], self.INCIDENT)

    def test_an_ordinary_next_move_is_silent(self):
        t = self._task(state="NEXT: keep going")
        out = self._update(t, state="NEXT: land the parser change in lib/x.py.")
        self.assertNotIn("OUTWARD IMPERATIVE", out)

    def test_rewriting_the_identical_line_does_not_ask_again(self):
        """The question is asked of the AUTHOR at the moment of authorship. Re-writing
        the same text is not a new claim, and re-asking would turn the check into the
        noise it is designed not to be."""
        t = self._task(state="NEXT: keep going")
        self._update(t, state=self.INCIDENT)
        out = self._update(self._reload(t), state=self.INCIDENT)
        self.assertNotIn("OUTWARD IMPERATIVE", out)

    def test_it_fires_on_the_checkpoint_path_too(self):
        """A `--summary` alongside is a checkpoint, not an exemption."""
        t = self._task(state="NEXT: keep going")
        out = self._update(t, state=self.INCIDENT, summary=GOOD_SUMMARY)
        self.assertIn("OUTWARD IMPERATIVE", out)
        self.assertIn("COLD-READ CHECK", out)      # both blocks, not one instead of the other


if __name__ == "__main__":
    unittest.main()
