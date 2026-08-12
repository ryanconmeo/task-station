"""The step reconcile verb — `update --step-supersede N` / `--step-restore N`.

WHY THIS EXISTS. Decisions got three reconcile verbs; steps had only add / done /
undone, so a step that had gone STALE had no honest exit. Measured on one real task:
three steps read as stale and one still named a vocabulary that had been retired days
earlier. Every available move was a lie or a mess —

  * ticking it done claims work nobody did (and inflates the progress rollup),
  * deleting it destroys the record of what was once agreed,
  * adding a "do not execute step 3" warning step is the anti-pattern that task
    already contained.

THE HARD RULES UNDER TEST THROUGHOUT, mirroring the decision verbs:
  * NON-DESTRUCTIVE — the step keeps its text and its tick, leaves the ACTIVE
    checklist, and stays in `/todo <n> history` marked with what replaced it.
  * REVERSIBLE — `--step-restore N` puts it back, unchanged.
  * OUT OF BOTH SIDES OF THE COUNTER — a superseded step is not outstanding work, so
    leaving it in the denominator would make the task read as permanently unfinished.
  * NO EDITING — there is deliberately no `--step-edit`; rewriting a step in place
    mutates the record.
  * STABLE INDICES — a superseded step keeps its slot, so `--step-done 4` still means
    step 4 afterwards and the active checklist may show gaps.

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

_TMP_HOME = tempfile.mkdtemp(prefix="ts-steps-")
os.environ["TASK_STATION_HOME"] = _TMP_HOME

import heal               # noqa: E402
import steps              # noqa: E402
import store              # noqa: E402

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


class _UpdateArgs:
    def __init__(self, **kw):
        defaults = dict(task=None, title=None, summary=None, append_summary=None,
                        state=None, goal=None, step_add=None, step_done=None,
                        step_undone=None, step_supersede=None, step_restore=None,
                        decision=None, supersedes=None, pin=False, pin_decision=None,
                        unpin_decision=None, restore_decision=None, log=None, pr=None,
                        pr_desc=None, story=None, story_desc=None, color=None,
                        effort=None, trail_visibility=None, relate=None, session=None)
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

    def _task(self, title="A task", step_texts=None, **fields):
        t = ts.new_task(title, "summary")
        if step_texts:
            t["steps"] = [{"text": s, "done": False} for s in step_texts]
        t.update(fields)
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _reload(self, t):
        return ts.load_task(t["id"])

    def _update(self, t, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_update(_UpdateArgs(task=str(t["seq"]), **kw))
        return buf.getvalue()


# ---------------------------------------------------------------------------
# The primitive (lib/steps.py).
# ---------------------------------------------------------------------------

class TestStepPrimitive(unittest.TestCase):
    def test_accessors_read_both_element_shapes(self):
        self.assertEqual(steps.text({"text": "a", "done": True}), "a")
        self.assertTrue(steps.is_done({"text": "a", "done": True}))
        self.assertEqual(steps.text("legacy bare string"), "legacy bare string")
        self.assertFalse(steps.is_done("legacy bare string"))
        self.assertFalse(steps.is_superseded("legacy bare string"))

    def test_mark_superseded_names_the_replacement(self):
        rows = [{"text": "old plan", "done": False}, {"text": "new plan", "done": False}]
        ok, err = steps.mark_superseded(rows, 1, 2)
        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertTrue(steps.is_superseded(rows[0]))
        self.assertEqual(steps.superseded_by(rows[0]), 2)
        self.assertEqual(steps.replacement_label(rows[0]), "SUPERSEDED by step 2")

    def test_a_step_may_be_retired_with_nothing_replacing_it(self):
        # The plan simply dropped it. Inventing a replacement would be inventing history.
        rows = [{"text": "old plan", "done": False}]
        ok, _err = steps.mark_superseded(rows, 1)
        self.assertTrue(ok)
        self.assertTrue(steps.is_superseded(rows[0]))
        self.assertIsNone(steps.superseded_by(rows[0]))
        self.assertEqual(steps.replacement_label(rows[0]),
                         "SUPERSEDED (nothing replaced it)")

    def test_the_text_and_the_tick_are_never_destroyed(self):
        rows = [{"text": "the stale step", "done": True}]
        steps.mark_superseded(rows, 1)
        self.assertEqual(steps.text(rows[0]), "the stale step")
        self.assertTrue(steps.is_done(rows[0]))

    def test_live_keeps_the_original_indices(self):
        rows = [{"text": "a", "done": False}, {"text": "b", "done": False},
                {"text": "c", "done": False}]
        steps.mark_superseded(rows, 2)
        self.assertEqual([i for i, _s in steps.live(rows)], [1, 3])
        self.assertEqual([i for i, _s in steps.superseded(rows)], [2])

    def test_progress_excludes_a_superseded_step_from_both_numbers(self):
        rows = [{"text": "a", "done": True}, {"text": "stale", "done": False},
                {"text": "c", "done": False}]
        self.assertEqual(steps.progress(rows), (1, 3))
        steps.mark_superseded(rows, 2)
        self.assertEqual(steps.progress(rows), (1, 2))

    def test_a_superseded_step_that_was_done_leaves_the_numerator_too(self):
        rows = [{"text": "a", "done": True}, {"text": "b", "done": True}]
        steps.mark_superseded(rows, 2)
        self.assertEqual(steps.progress(rows), (1, 1))

    def test_counts_reports_both_populations(self):
        rows = [{"text": "a", "done": False}, {"text": "b", "done": False}]
        steps.mark_superseded(rows, 1)
        self.assertEqual(steps.counts(rows), (1, 1))

    def test_restore_returns_it_unchanged(self):
        rows = [{"text": "the step", "done": True}, {"text": "b", "done": False}]
        steps.mark_superseded(rows, 1, 2)
        ok, err = steps.restore(rows, 1)
        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertFalse(steps.is_superseded(rows[0]))
        self.assertEqual(rows[0], {"text": "the step", "done": True})

    def test_restoring_an_active_step_is_a_loud_error(self):
        rows = [{"text": "a", "done": False}]
        ok, err = steps.restore(rows, 1)
        self.assertFalse(ok)
        self.assertIn("not superseded", err)

    def test_superseding_an_already_superseded_step_is_a_loud_error(self):
        rows = [{"text": "a", "done": False}, {"text": "b", "done": False}]
        steps.mark_superseded(rows, 1, 2)
        ok, err = steps.mark_superseded(rows, 1)
        self.assertFalse(ok)
        self.assertIn("already superseded by step 2", err)
        self.assertIn("--step-restore 1", err)

    def test_a_step_cannot_supersede_itself(self):
        rows = [{"text": "a", "done": False}]
        ok, err = steps.mark_superseded(rows, 1, 1)
        self.assertFalse(ok)
        self.assertIn("cannot supersede itself", err)

    def test_an_out_of_range_index_is_a_loud_error_not_a_no_op(self):
        rows = [{"text": "a", "done": False}]
        for bad in (0, 2, 99, "x", None):
            ok, err = steps.mark_superseded(rows, bad)
            self.assertFalse(ok)
            self.assertTrue(err)
        self.assertFalse(steps.is_superseded(rows[0]))

    def test_set_done_refuses_on_a_superseded_step(self):
        # Ticking a retired step would record a completion for work that was dropped —
        # the exact lie the verb exists to avoid.
        rows = [{"text": "stale", "done": False}]
        steps.mark_superseded(rows, 1)
        ok, err = steps.set_done(rows, 1, True)
        self.assertFalse(ok)
        self.assertIn("off the active checklist", err)
        self.assertFalse(steps.is_done(rows[0]))

    def test_an_ordinary_step_round_trips_byte_identically(self):
        # The frozen format: an ordinary step must be stored exactly as every older
        # version stored it, so the reconcile keys are strictly ADDITIVE.
        self.assertEqual(steps.compact({"text": "a", "done": False}),
                         {"text": "a", "done": False})
        rows = [{"text": "a", "done": False}, {"text": "b", "done": False}]
        steps.mark_superseded(rows, 1, 2)
        steps.restore(rows, 1)
        self.assertEqual(rows, [{"text": "a", "done": False},
                                {"text": "b", "done": False}])

    def test_a_legacy_bare_string_step_can_be_retired(self):
        rows = ["a bare string step", {"text": "b", "done": False}]
        ok, _err = steps.mark_superseded(rows, 1, 2)
        self.assertTrue(ok)
        self.assertEqual(steps.text(rows[0]), "a bare string step")
        self.assertTrue(steps.is_superseded(rows[0]))

    def test_a_garbled_replacement_still_reads_as_superseded(self):
        step = {"text": "a", "done": False, "superseded": True, "superseded_by": "nope"}
        self.assertTrue(steps.is_superseded(step))
        self.assertIsNone(steps.superseded_by(step))
        self.assertEqual(steps.replacement_label(step),
                         "SUPERSEDED (nothing replaced it)")

    def test_unknown_keys_from_a_newer_version_are_preserved(self):
        step = {"text": "a", "done": False, "future_flag": "keep me"}
        self.assertEqual(steps.compact(step)["future_flag"], "keep me")


# ---------------------------------------------------------------------------
# The verb through the CLI (`update`).
# ---------------------------------------------------------------------------

class TestStepSupersedeThroughUpdate(_Base):
    def test_a_superseded_step_leaves_the_active_checklist(self):
        t = self._task(step_texts=["write the tests", "STALE: use the old vocabulary"])
        out = self._update(t, step_supersede=[2])
        self.assertIn("step⊘", out)
        after = self._reload(t)
        self.assertEqual([steps.text(s) for _i, s in steps.live(after["steps"])],
                         ["write the tests"])
        self.assertEqual(len(after["steps"]), 2)          # nothing was deleted

    def test_a_step_added_in_the_same_call_is_recorded_as_the_replacement(self):
        t = self._task(step_texts=["STALE: use the old vocabulary"])
        self._update(t, step_add=["use the current vocabulary"], step_supersede=[1])
        after = self._reload(t)
        self.assertEqual(steps.superseded_by(after["steps"][0]), 2)
        self.assertEqual(steps.replacement_label(after["steps"][0]),
                         "SUPERSEDED by step 2")

    def test_the_counter_excludes_it_from_both_numbers(self):
        t = self._task(step_texts=["done one", "stale one", "still to do"])
        self._update(t, step_done=[1])
        self.assertEqual(ts.step_progress(self._reload(t)), (1, 3))
        self._update(t, step_supersede=[2])
        self.assertEqual(ts.step_progress(self._reload(t)), (1, 2))

    def test_the_detail_view_shows_the_active_checklist_and_says_how_many_left_it(self):
        t = self._task(step_texts=["write the tests", "STALE: old vocabulary"])
        self._update(t, step_supersede=[2])
        view = ts._format_detail(self._reload(t), None)
        self.assertIn("Steps (0/1 done)", view)
        self.assertIn("write the tests", view)
        self.assertNotIn("STALE: old vocabulary", view)
        self.assertIn("1 superseded step(s)", view)

    def test_the_active_checklist_keeps_stable_numbers(self):
        # Step 3 is still step 3 after step 2 was retired — renumbering would silently
        # repoint every `--step-done N` a reader had in hand.
        t = self._task(step_texts=["a", "b", "c"])
        self._update(t, step_supersede=[2])
        view = ts._format_detail(self._reload(t), None)
        self.assertIn(" 3. c", view)
        self._update(t, step_done=[3])
        self.assertTrue(steps.is_done(self._reload(t)["steps"][2]))

    def test_history_still_shows_every_step_marked(self):
        t = self._task(step_texts=["STALE: old vocabulary", "the corrected step"])
        self._update(t, step_supersede=[1])
        view = ts._format_history(self._reload(t))
        self.assertIn("STALE: old vocabulary", view)
        self.assertIn("SUPERSEDED", view)
        self.assertIn("1 superseded", view)
        self.assertIn("the corrected step", view)

    def test_the_verb_is_reversible(self):
        t = self._task(step_texts=["a", "stale"])
        self._update(t, step_supersede=[2])
        self.assertEqual(ts.step_progress(self._reload(t)), (0, 1))
        out = self._update(t, step_restore=[2])
        self.assertIn("step↺", out)
        after = self._reload(t)
        self.assertFalse(steps.is_superseded(after["steps"][1]))
        self.assertEqual(ts.step_progress(after), (0, 2))

    def test_a_bad_index_is_reported_and_nothing_changes(self):
        t = self._task(step_texts=["a"])
        out = self._update(t, step_supersede=[9])
        self.assertIn("no such step", out)
        self.assertFalse(steps.is_superseded(self._reload(t)["steps"][0]))

    def test_ticking_a_superseded_step_is_refused_with_a_reason(self):
        t = self._task(step_texts=["stale"])
        self._update(t, step_supersede=[1])
        out = self._update(t, step_done=[1])
        self.assertIn("off the active checklist", out)
        self.assertFalse(steps.is_done(self._reload(t)["steps"][0]))

    def test_restoring_an_active_step_reports_loudly(self):
        t = self._task(step_texts=["a"])
        out = self._update(t, step_restore=[1])
        self.assertIn("not superseded", out)

    def test_the_board_view_model_carries_only_active_steps(self):
        t = self._task(step_texts=["active one", "stale one"])
        self._update(t, step_supersede=[2])
        vm = ts._board_view_model(self._reload(t))
        self.assertEqual([s["text"] for s in vm["steps"]], ["active one"])
        self.assertEqual(vm["progress"], [0, 1])
        self.assertEqual(vm["steps_cell"], "0/1")

    def test_the_checkpoint_snapshot_carries_only_active_steps(self):
        t = self._task(step_texts=["active one", "stale one"])
        self._update(t, step_supersede=[2])
        snap = ts._stream_digest(self._reload(t))
        self.assertEqual([steps.text(s) for s in snap["steps"]], ["active one"])

    def test_a_task_with_no_superseded_step_is_stored_exactly_as_before(self):
        # The frozen-format guarantee, and it moved by exactly one ADDITIVE key when the
        # completion stamp shipped (`steps.DONE_TS_FIELD`, for lib/checker.py's goal-drift
        # check — nothing else on a task could answer WHEN a step was ticked). So the
        # assertion is now: an UNTICKED step is still byte-identical to what every older
        # version wrote, and a TICKED one adds `done_ts` and nothing else. No reconcile
        # key appears on either.
        t = self._task(step_texts=["a", "b"])
        self._update(t, step_done=[1])
        after = self._reload(t)
        self.assertEqual(after["steps"][1], {"text": "b", "done": False})
        self.assertEqual(sorted(after["steps"][0]), ["done", steps.DONE_TS_FIELD, "text"])
        self.assertEqual(after["steps"][0]["text"], "a")
        self.assertTrue(after["steps"][0]["done"])
        self.assertGreater(after["steps"][0][steps.DONE_TS_FIELD], 0)

    def test_unticking_a_step_returns_it_to_the_pre_stamp_shape(self):
        # The stamp is symmetric: an unticked step carries no completion moment, because
        # the record must not assert one for work that is no longer claimed as done.
        t = self._task(step_texts=["a"])
        self._update(t, step_done=[1])
        self._update(t, step_undone=[1])
        self.assertEqual(self._reload(t)["steps"], [{"text": "a", "done": False}])

    def test_there_is_no_step_edit_verb(self):
        # Superseding plus adding a corrected step is the honest path. An edit would
        # rewrite the record in place, which is what this whole model refuses to do.
        # (The engine MENTIONS `--step-edit` — in the help that explains why it is
        # absent — so this asserts no flag and no handler, not the absence of the word.)
        with open(os.path.join(LIB, "task-station.py"), encoding="utf-8") as f:
            engine = f.read()
        self.assertNotIn('add_argument("--step-edit"', engine)
        self.assertNotIn("step_edit", engine)
        self.assertNotIn("def edit_step", engine)

    def test_the_stale_step_finding_stops_firing_once_the_step_is_retired(self):
        t = self._task(step_texts=["STALE — do not execute", "the corrected step"])
        self.assertEqual(len(heal.stale_steps(self._reload(t))), 1)
        self._update(t, step_supersede=[1])
        self.assertEqual(heal.stale_steps(self._reload(t)), [])

    def test_the_search_index_still_finds_a_retired_step(self):
        # History's job is to stay complete: a retired step must remain discoverable.
        t = self._task(step_texts=["a distinctive retired phrase"])
        self._update(t, step_supersede=[1])
        blob = store.task_search_text(self._reload(t))
        self.assertIn("distinctive retired phrase", blob)


if __name__ == "__main__":
    unittest.main()
