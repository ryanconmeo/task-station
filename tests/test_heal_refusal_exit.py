"""#606 — A HEAL VERB THAT REFUSES EXITS NON-ZERO, and one that performs its write exits 0.

THE DEFECT. Every heal path exited 0, so a refusal and a performed write were
indistinguishable to anything reading the status:

    heal --task 1 --merge 2,foo --into 5     REFUSES, changes nothing     exit 0
    heal --task 1 --merge 2,3   --into 5     SUCCEEDS, writes             exit 0

That was harmless while nothing read the status. 3.49.0 (#595) ended that: it made
`returncode == 0` a REQUIRED CONJUNCT for exit conditions and claims — "a green condition
means the command SUCCEEDED, not that its text appeared". The conjunct added to close
"the output looked right but the command failed" cannot close anything against a command
that fails QUIETLY AT STATUS 0, and a heal refusal names the verb and the decision
numbers, so a loosely-written expected substring matches the refusal it was meant to
catch. #595's own blast-radius reasoning says why its measurement does not cover this:
conditions are written `<probe> && echo <marker>`, so the marker and the status agree BY
CONSTRUCTION — for a probe that FAILS LOUDLY. These verbs failed quietly.

THE BOUNDARY IS WRITE-VS-READ, not "any path that can decline" (606:2), and both halves
are tested here because only one of them is the bug:

  * a WRITING verb that declines leaves 2 — the nine enumerated from the heal parser:
    --split, --merge, --reassign, --unassign, --dismiss/--undismiss, --apply,
    --mark-healed, --dispose-acks, --goal-reviewed;
  * a READ that RAN leaves 0 whatever it finds — --scan, --dismissals, --candidates and
    bare `heal`. A scan that finds problems has succeeded at scanning, and making a
    finding non-zero would break every caller that wraps one. THE SECOND HALF IS THE
    REGRESSION RISK: a blanket non-zero would be worse than the bug it replaces, so every
    succeeding write and every read is asserted at 0 beside its refusing twin.
  * an invocation REFUSED BEFORE IT RAN leaves 2 whichever flags it named, read ones
    included — nothing was read and nothing was written, so 0 would report success for a
    command that did not happen.

TESTED TWICE, IN PROCESS AND THROUGH THE REAL BINARY. In process is where the status is
decided (`cmd_heal` returns it); through `python3 lib/task-station.py` is what a stored
condition actually observes, and it is the only place that proves `cli.main` and the
entry point carry the value out. A test of only the first would pass with the dispatch
still throwing the return value away, which is precisely how this defect survived.
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

_TMP_HOME = tempfile.mkdtemp(prefix="ts-heal-exit-")
os.environ["TASK_STATION_HOME"] = _TMP_HOME

import store               # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "task_station_exit", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)

REFUSED = ts.HEAL_REFUSED


class _Args:
    """The heal namespace, with every flag the parser defines. Kept complete rather than
    leaning on `cmd_heal`'s `getattr` defaults: a flag this forgot would silently take the
    default branch and the test would assert the wrong path's status."""

    def __init__(self, **kw):
        defaults = dict(session=None, task=None, ref=None, scan=False, apply=False,
                        all=False, verbose=False, split=None, merge=None, into=None,
                        reassign=None, unassign=None, to=None, stub=None,
                        mark_healed=False, note=None, dispose_acks=None, decision=None,
                        memory=None, noop=None, dismiss=None, undismiss=None, why=None,
                        dismissals=False, candidates=False, goal_reviewed=False,
                        probe_links=False, probe_ado=False, dry_run=False)
        defaults.update(kw)
        self.__dict__.update(defaults)


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-heal-exit-case-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        store.reset_cache()

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _task(self, title="A task", decisions=None, **fields):
        t = ts.new_task(title, "summary")
        if decisions is not None:
            t["decisions"] = list(decisions)
        t.update(fields)
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _five(self, **fields):
        return self._task(decisions=["Ruling one. Body one.", "Ruling two. Body two.",
                                     "Ruling three. Body three.", "Ruling four. Body.",
                                     "Ruling five. Body five."], **fields)

    def _heal(self, t=None, **kw):
        """`(exit status, printed output)` for one `heal` invocation, in process."""
        if t is not None:
            kw.setdefault("task", str(t["seq"]))
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ts.cmd_heal(_Args(**kw))
        return rc, buf.getvalue()

    def _blob(self, t):
        """The whole stored task minus the one field any write bumps — so "nothing was
        changed" can be asserted as an identity rather than as the absence of one mark."""
        d = dict(ts.load_task(t["id"]))
        d.pop("updated_ts", None)
        return json.dumps(d, sort_keys=True, default=str)

    def assertRefused(self, rc, out, needle=None):
        self.assertEqual(REFUSED, rc,
                         "a refusal reported success (exit %r):\n%s" % (rc, out))
        if needle:
            self.assertIn(needle, out)

    def assertPerformed(self, rc, out, needle=None):
        self.assertIn(rc, (None, 0),
                      "a run that did its work reported failure (exit %r):\n%s"
                      % (rc, out))
        if needle:
            self.assertIn(needle, out)


# ---------------------------------------------------------------------------
# THE NINE WRITING VERBS — each one refusing, and each one performing.
# ---------------------------------------------------------------------------

class TheWritingVerbsReportTheirRefusals(_Base):
    """One test per verb, and every one of them asserts BOTH directions.

    A single-direction test is what makes a blanket non-zero look like a fix: a suite that
    only ever sees the refusals would go green on a `heal` that failed everything, and
    "nothing succeeds any more" is worse than the bug."""

    # -- --split ---------------------------------------------------------------

    def test_split_refusing_leaves_non_zero_and_split_writing_leaves_zero(self):
        t = self._five()
        before = self._blob(t)
        rc, out = self._heal(t, split="1", into="3,foo")
        self.assertRefused(rc, out, "not a decision number")
        self.assertEqual(before, self._blob(t), "the refusal wrote something")

        rc, out = self._heal(t, split="1", into="3,4")
        self.assertPerformed(rc, out, "split decision 1 into 3, 4")

    def test_split_naming_no_into_is_a_refusal(self):
        t = self._five()
        rc, out = self._heal(t, split="1")
        self.assertRefused(rc, out, "pass `--into")

    # -- --merge ---------------------------------------------------------------

    def test_merge_refusing_leaves_non_zero_and_merge_writing_leaves_zero(self):
        t = self._five()
        before = self._blob(t)
        rc, out = self._heal(t, merge="2,foo", into="5")
        self.assertRefused(rc, out, "not a decision number")
        self.assertEqual(before, self._blob(t), "the refusal wrote something")

        rc, out = self._heal(t, merge="2,3", into="5")
        self.assertPerformed(rc, out, "merged 2, 3 into 5")

    def test_merge_with_an_out_of_range_member_is_a_refusal(self):
        t = self._five()
        rc, out = self._heal(t, merge="2,99", into="5")
        self.assertRefused(rc, out, "no such decision")

    def test_split_and_merge_in_one_invocation_is_a_refusal(self):
        t = self._five()
        rc, out = self._heal(t, split="1", merge="2,3", into="5")
        self.assertRefused(rc, out, "each need their own --into")

    # -- --reassign / --unassign ------------------------------------------------

    def test_reassign_refusing_leaves_non_zero_and_reassign_writing_leaves_zero(self):
        src, dst = self._five(), self._task("owner")
        sid = "sess0606"
        ts.set_link(sid, src["id"])
        before = self._blob(src)
        rc, out = self._heal(src, reassign="1,foo", to=str(dst["seq"]), session=sid)
        self.assertRefused(rc, out, "not a decision number")
        self.assertEqual(before, self._blob(src), "the refusal wrote something")

        rc, out = self._heal(src, reassign="1", to=str(dst["seq"]), session=sid)
        self.assertPerformed(rc, out, "reassigned decision(s) 1")

    def test_reassign_with_no_to_is_a_refusal(self):
        t = self._five()
        sid = "sess0606"
        ts.set_link(sid, t["id"])
        rc, out = self._heal(t, reassign="1", session=sid)
        self.assertRefused(rc, out, "pass `--to <task>`")

    def test_unassign_refusing_leaves_non_zero_and_unassign_writing_leaves_zero(self):
        src, dst = self._five(), self._task("owner")
        sid = "sess0606"
        ts.set_link(sid, src["id"])
        rc, out = self._heal(src, reassign="1", to=str(dst["seq"]), session=sid)
        self.assertPerformed(rc, out)

        # Decision 2 was never reassigned, so bringing it "home" is a refusal…
        rc, out = self._heal(src, unassign="2", session=sid)
        self.assertRefused(rc, out, "already owns that decision")
        # …and the one that WAS reassigned comes home at 0.
        rc, out = self._heal(src, unassign="1", session=sid)
        self.assertPerformed(rc, out, "back to #")

    def test_reassign_combined_with_all_is_a_refusal(self):
        self._five()
        rc, out = self._heal(reassign="1", to="1", all=True)
        self.assertRefused(rc, out, "cannot be combined with --all")

    # -- --dismiss / --undismiss ------------------------------------------------

    def test_dismiss_with_no_why_is_a_refusal(self):
        t = self._five()
        rc, out = self._heal(t, apply=True, dismiss=["drift:branch x"])
        self.assertRefused(rc, out, "needs --why")

    def test_dismiss_without_apply_is_a_refusal(self):
        t = self._five()
        rc, out = self._heal(t, dismiss=["drift:branch x"], why="not a defect")
        self.assertRefused(rc, out, "would have silently done nothing")

    def test_undismiss_naming_no_ruling_is_a_refusal(self):
        t = self._five()
        rc, out = self._heal(t, apply=True, undismiss=["drift:branch x"])
        self.assertRefused(rc, out, "no active dismissal")

    def test_a_dismissal_that_lands_leaves_zero(self):
        """The succeeding half of the ledger's write path, driven off a REAL finding —
        the unlinked-prose pair the scan checks were built against — so the 0 is a
        dismissal that HAPPENED rather than one that was quietly declined."""
        t = self._task(decisions=["go with flat files",
                                  "decision 1 was wrong — sqlite instead, for the FTS "
                                  "index"])
        sel = self._first_finding(t)
        self.assertIsNotNone(sel, "the fixture produced no finding to dismiss")
        rc, out = self._heal(t, apply=True, dismiss=[sel], why="ruled on, not a defect")
        self.assertPerformed(rc, out, "DISMISSED")

    def _first_finding(self, t):
        import heal as _heal
        result = _heal.scan(ts.load_task(t["id"]))
        for f in (result.get("findings") or []):
            check, ref = f.get("check"), f.get("ref")
            if check and ref:
                return "%s:%s" % (check, ref)
        return None

    # -- --apply ---------------------------------------------------------------

    def test_apply_with_nothing_to_perform_is_a_refusal(self):
        t = self._task(decisions=["One ruling, and nothing mechanical to do about it."])
        rc, out = self._heal(t, apply=True)
        self.assertRefused(rc, out, "REFUSED: --apply performed no operation")

    def test_apply_combined_with_scan_is_a_refusal(self):
        t = self._five()
        rc, out = self._heal(t, apply=True, scan=True)
        self.assertRefused(rc, out, "read-only and applies nothing")

    # -- --mark-healed ----------------------------------------------------------

    def test_mark_healed_writing_leaves_zero_and_a_bad_combination_refuses(self):
        t = self._five()
        rc, out = self._heal(t, mark_healed=True, note="read every ruling; all still true")
        self.assertPerformed(rc, out, "MARKED HEALED")

        rc, out = self._heal(t, mark_healed=True, apply=True)
        self.assertRefused(rc, out, "cannot be combined with --scan, --apply")

    # -- --dispose-acks ---------------------------------------------------------

    def test_dispose_acks_with_nothing_to_fill_is_a_refusal(self):
        t = self._five()
        rc, out = self._heal(t, apply=True, dispose_acks="all",
                             noop="no durable change was needed")
        self.assertRefused(rc, out, "no undispositioned ack to retro-fill")

    def test_dispose_acks_combined_with_all_is_a_refusal(self):
        self._five()
        rc, out = self._heal(all=True, apply=True, dispose_acks="all", noop="none needed")
        self.assertRefused(rc, out, "cannot be combined with --all")

    # -- --goal-reviewed --------------------------------------------------------

    def test_goal_reviewed_on_a_task_with_no_goal_is_a_refusal(self):
        t = self._five()
        rc, out = self._heal(t, goal_reviewed=True)
        self.assertRefused(rc, out, "REFUSED: this task has no goal line")

    def test_goal_reviewed_on_a_task_with_a_goal_leaves_zero(self):
        t = self._five(goal="DONE = the record reads true")
        rc, out = self._heal(t, goal_reviewed=True)
        self.assertPerformed(rc, out, "GOAL REVIEW RECORDED")

    def test_a_refused_goal_review_refuses_the_mark_healed_it_was_paired_with(self):
        """`--goal-reviewed --mark-healed` is ONE pass in the caller's head. The stamp
        succeeds on a task with no goal, so without this the pair would report success
        for a half that was declined."""
        t = self._five()
        rc, out = self._heal(t, goal_reviewed=True, mark_healed=True, note="read it")
        self.assertRefused(rc, out, "REFUSED: this task has no goal line")
        self.assertIn("MARKED HEALED", out)      # …and the half that ran still reports


# ---------------------------------------------------------------------------
# --dry-run FOLLOWS ITS VERB.
# ---------------------------------------------------------------------------

class TheDryRunReportsTheStatusTheRealRunWouldGive(_Base):
    """A preview that reported success for a batch the real run would refuse is useless as
    a gate — which is the ONLY thing a preview is for. The batch is validated exactly as
    the real run validates it, so the status has to follow too."""

    def test_a_refusal_previewed_is_a_refusal_reported(self):
        t = self._five()
        rc, out = self._heal(t, merge="2,foo", into="5", dry_run=True)
        self.assertRefused(rc, out, "not a decision number")

    def test_a_legal_batch_previewed_leaves_zero_and_writes_nothing(self):
        t = self._five()
        before = self._blob(t)
        rc, out = self._heal(t, merge="2,3", into="5", dry_run=True)
        self.assertPerformed(rc, out, "--dry-run: nothing was changed. The batch is legal")
        self.assertEqual(before, self._blob(t), "the dry run wrote something")

    def test_an_out_of_range_member_previewed_is_a_refusal(self):
        t = self._five()
        rc, out = self._heal(t, split="1", into="3,99", dry_run=True)
        self.assertRefused(rc, out)

    def test_a_legal_reassign_previewed_leaves_zero(self):
        src, dst = self._five(), self._task("owner")
        sid = "sess0606"
        ts.set_link(sid, src["id"])
        rc, out = self._heal(src, reassign="1", to=str(dst["seq"]), session=sid,
                             dry_run=True)
        self.assertPerformed(rc, out, "--dry-run: nothing was changed")


# ---------------------------------------------------------------------------
# THE READS KEEP THEIR ZERO — the half that is NOT the bug.
# ---------------------------------------------------------------------------

class AReadThatRanLeavesZeroWhateverItFound(_Base):
    """606:2 draws this line and it is a rule, not an omission: a scan that FINDS problems
    has not failed, it has succeeded at scanning. Making a finding non-zero would break
    every caller that wraps a scan, and it is the mirror of the trap 606:1 warns about."""

    def _noisy(self):
        """A task the scan has something to say about — an unlinked-prose pair, which is
        the fixture the scan checks were built against."""
        return self._task(decisions=["go with flat files",
                                     "decision 1 was wrong — sqlite instead, for the FTS "
                                     "index"])

    def test_a_scan_that_finds_problems_leaves_zero(self):
        t = self._noisy()
        rc, out = self._heal(t, scan=True)
        self.assertPerformed(rc, out, "[HEAL-SCAN]")
        self.assertIn("prose", out.lower())

    def test_the_bare_dry_run_leaves_zero(self):
        rc, out = self._heal(self._noisy())
        self.assertPerformed(rc, out, "[HEAL]")

    def test_dismissals_leaves_zero(self):
        rc, out = self._heal(self._five(), dismissals=True)
        self.assertPerformed(rc, out, "DISMISSALS")

    def test_candidates_leaves_zero(self):
        rc, out = self._heal(self._five(), candidates=True)
        self.assertPerformed(rc, out, "[HEAL-CANDIDATES]")

    def test_a_sweep_over_an_empty_board_leaves_zero(self):
        """Nothing was asked for and nothing was withheld — an emptiness report, not a
        refusal, and the one place a heal that performed nothing still reads 0."""
        rc, out = self._heal(all=True)
        self.assertPerformed(rc, out, "No open tasks to heal.")


class AnInvocationRefusedBeforeItRanLeavesNonZero(_Base):
    """Read flags included. Nothing was read and nothing was written, so 0 would report
    success for a command that did not happen — the direction `loop.py` already refuses
    ("an unresolvable --task indistinguishable from every condition passing … the failure
    direction a release gate must never have")."""

    def test_two_reads_in_one_invocation_refuse(self):
        rc, out = self._heal(self._five(), dismissals=True, candidates=True)
        self.assertRefused(rc, out, "two different reads")

    def test_a_read_combined_with_a_write_refuses(self):
        rc, out = self._heal(self._five(), dismissals=True, apply=True)
        self.assertRefused(rc, out, "is a READ")

    def test_an_unresolvable_task_refuses(self):
        rc, out = self._heal(task="no-such-task-9999")
        self.assertRefused(rc, out, "No task matching")

    def test_no_attached_task_and_no_target_refuses(self):
        rc, out = self._heal()
        self.assertRefused(rc, out, "No task attached")

    def test_the_positional_fighting_all_refuses(self):
        rc, out = self._heal(ref="12", all=True)
        self.assertRefused(rc, out, "cannot be combined")

    def test_the_positional_fighting_a_different_task_refuses(self):
        rc, out = self._heal(ref="12", task="13")
        self.assertRefused(rc, out, "name different tasks")

    def test_scan_cannot_dispose_and_says_so_non_zero(self):
        rc, out = self._heal(self._five(), scan=True, dispose_acks="all", noop="none")
        self.assertRefused(rc, out, "read-only")


# ---------------------------------------------------------------------------
# THROUGH THE REAL BINARY — what a stored condition actually observes.
# ---------------------------------------------------------------------------

class TheStatusReachesTheProcess(unittest.TestCase):
    """THE TESTS ABOVE WOULD ALL PASS WITH THE DISPATCH STILL DISCARDING THE STATUS. That
    is not hypothetical — it is exactly how this defect survived: `cli.main` ended in
    `a.fn(a)` and the entry point ended in `main()`, so a handler's return value never
    reached the process at all. Only running the real binary proves the value is carried
    out, and the process status is the ONLY thing a stored exit condition ever sees."""

    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp(prefix="ts-heal-exit-proc-")
        cls.env = dict(os.environ)
        cls.env.update({"TASK_STATION_HOME": cls.home, "CLAUDE_CONFIG_DIR": cls.home,
                        "XDG_STATE_HOME": cls.home, "TASK_STATION_NO_AGENT_QUERY": "1"})
        cls.cli = os.path.join(LIB, "task-station.py")
        cls._run(["create", "--session", "s606", "--title", "exit-code fixture",
                  "--summary", "for #606"])
        for text in ("Ruling one. Body one.", "Ruling two. Body two.",
                     "Ruling three. Body three.", "Ruling four. Body four."):
            cls._run(["update", "--task", "1", "--session", "s606", "--decision", text])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.home, ignore_errors=True)

    @classmethod
    def _run(cls, argv):
        p = subprocess.run([sys.executable, cls.cli] + argv, capture_output=True,
                           text=True, env=cls.env, cwd=LIB)
        return p.returncode, (p.stdout or "") + (p.stderr or "")

    def test_a_refusing_merge_exits_non_zero_through_the_binary(self):
        rc, out = self._run(["heal", "--task", "1", "--merge", "2,foo", "--into", "4"])
        self.assertEqual(REFUSED, rc, out)
        self.assertIn("not a decision number", out)

    def test_a_refusing_split_exits_non_zero_through_the_binary(self):
        rc, out = self._run(["heal", "--task", "1", "--split", "1,2", "--into", "3"])
        self.assertEqual(REFUSED, rc, out)

    def test_a_refusing_apply_exits_non_zero_through_the_binary(self):
        rc, out = self._run(["heal", "--task", "1", "--apply"])
        self.assertEqual(REFUSED, rc, out)
        self.assertIn("REFUSED", out)

    def test_a_scan_exits_zero_through_the_binary(self):
        rc, out = self._run(["heal", "--task", "1", "--scan"])
        self.assertEqual(0, rc, out)
        self.assertIn("[HEAL-SCAN]", out)

    def test_a_legal_dry_run_exits_zero_through_the_binary(self):
        rc, out = self._run(["heal", "--task", "1", "--merge", "2,3", "--into", "4",
                             "--dry-run"])
        self.assertEqual(0, rc, out)
        self.assertIn("The batch is legal", out)

    def test_a_merge_that_writes_exits_zero_through_the_binary(self):
        rc, out = self._run(["heal", "--task", "1", "--merge", "2,3", "--into", "4"])
        self.assertEqual(0, rc, out)
        self.assertIn("merged 2, 3 into 4", out)

    def test_every_other_subcommand_still_exits_zero(self):
        """`cli.main` now returns the handler's value, and every handler but `heal`
        returns None. `sys.exit(None)` is 0 — asserted rather than assumed, because a
        handler that returned something truthy for its own reasons would start failing
        the moment the dispatch stopped discarding it."""
        for argv in (["render", "--session", "s606", "--arg", ""],
                     ["search", "--detail", "1"],
                     ["claims", "show", "--task", "1"]):
            rc, out = self._run(argv)
            self.assertEqual(0, rc, "%s exited %d:\n%s" % (" ".join(argv), rc, out))


if __name__ == "__main__":
    unittest.main()
