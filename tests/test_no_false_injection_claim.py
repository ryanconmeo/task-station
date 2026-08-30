"""NOTHING IS DELIVERED TO A RELAYED OR INVOKED SESSION, AND NO LINE MAY SAY IT WAS.

WHAT THIS IS ABOUT. Attaching a session to a task is a POINTER. At session start the
hook prints that task's title and status — nothing else, and in particular not the
digest. Seven lines across five files said otherwise: they told a fresh session its
SessionStart had already handed over the record, and then, on that basis, deliberately
withheld the record from the prompt. Both halves are individually reasonable. Together
they left a session with no context and a sentence explaining why it did not need any,
which is the single most expensive thing this system can tell a model.

WHY A REPO-WIDE SCAN AND NOT A LIST OF THE SEVEN. The first attempt to count these
greped a hardcoded list of paths, two of which were wrong (`lib/board/view.py` does not
exist; `lib/board/loop.py` exists but is role config — the invoke path is
`lib/board/cmds/loop.py`). A missing path and a clean file both produce zero, so the
count came back 2 when it was 7, and the two live `print()` calls — the ones an operator
sees on every single `invoke` and every `relay --spawn` — were the ones it could not
see. A list is the assumption that produced the miscount, so `ClaimsAreCountedOverTheWholeTree`
walks `lib/` and has no list to be incomplete.

WHY THE SCANNER IS TESTED IN BOTH DIRECTIONS. Every gate defect this task produced
passed a red check and was never tested for green. A scanner that finds the claims is
half of it; one that does not fire on a CORRECT fix is the other half, and this
repository documents its own past defects in comments beside the code — so "this line
used to say SessionStart injects the digest" is house style, is true, and must not
count as an offence. `ScannerControl` pins both directions on synthetic input, because
a scanner asserted only against a tree that already passes is asserting nothing.

WHY THE `NEXT:` GUARD IS IN THIS FILE. It is the same defect shape, one layer over: a
check that exists but fires where it cannot help. The cold-read check that requires a
state line to lead with `NEXT:` ran only on the CHECKPOINT path, so `update --state
'<no NEXT:>'` printed a success line and nothing else while the identical text written
with a `--summary` printed the failure. The author is told at the wrong moment — the
first thing to notice is a REFUSED handoff, by which point the session that knew the
answer is gone.

THE CAPS ARE NOT THE FIX. `NEXT_CHARS`, `STEP_CHARS`/`STEP_CAP` and `PROMPT_BUDGET` were
correct reasoning from a false premise; only the premise was wrong. Raising any of them
turns a relay prompt into the context dump the whole design avoids, so
`tests/test_succession.py::ContinuationPrompt` pins them and nothing here touches them.
"""
import io
import os
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(_REPO_ROOT, "lib")
sys.path.insert(0, LIB)

_TMP_HOME = tempfile.mkdtemp(prefix="ts-noinject-")
os.environ["TASK_STATION_HOME"] = _TMP_HOME

import importlib.util                                                   # noqa: E402

import store                                                            # noqa: E402
from board.cmds import loop as _loop_cmds                               # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


# ---------------------------------------------------------------------------
# The two patterns, TRANSCRIBED FROM THE GATE that scores this work
# (`checker/t583-no-false-injection-claim.sh`). They are copied rather than
# paraphrased on purpose: a test whose idea of "a claim" drifts from the gate's
# hands out a green the gate then contradicts, and the disagreement would be
# discovered at release rather than here.
# ---------------------------------------------------------------------------

# A line that asserts the record has been handed over.
CLAIM = re.compile(r"already injected"
                   r"|already has the context"
                   r"|SessionStart injects"
                   r"|inject[a-z]*[^.]{0,40}digest", re.I)

# ...unless it is stating the TRUTH about injection, or recording what a line used to
# say. The negation must sit within ~20 characters of the verb: a bare "not" would
# exempt the worst offender of the seven, whose line read "This is a handoff, not a
# fresh start. That task's digest is already injected" — the negation there belongs to
# a different clause entirely. `HISTORY` and `#583` are the explicit opt-outs for a
# comment that records a past defect, which is this codebase's documented house style.
NOT_A_CLAIM = re.compile(
    r"(nothing|never|not|no longer|does ?n.t|did ?n.t|used to|wrongly|falsely)"
    r" [a-z ]{0,20}inject"
    r"|inject[a-z]*[^.]{0,20}(is|was) (not|never)"
    r"|HISTORY"
    r"|#583", re.I)


def claims_in(text):
    """Every line of `text` that CLAIMS the record was delivered, as
    `[(lineno, line)]`. A line that merely mentions injection while stating the truth
    about it is not a claim and is not counted."""
    return [(n, line) for n, line in enumerate(text.splitlines(), 1)
            if CLAIM.search(line) and not NOT_A_CLAIM.search(line)]


def scan_tree(root):
    """`{relative path: [(lineno, line), …]}` for every file under `root` carrying a
    claim. NO PATH LIST — the walk is the point. An undecodable file is skipped rather
    than crashing the scan, and a directory that cannot be read is a real finding, so
    `walk` is left to raise on one."""
    found = {}
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in sorted(files):
            path = os.path.join(base, name)
            try:
                with io.open(path, encoding="utf-8") as f:
                    text = f.read()
            except (UnicodeDecodeError, OSError):
                continue                      # a binary or unreadable file makes no claim
            hits = claims_in(text)
            if hits:
                found[os.path.relpath(path, root)] = hits
    return found


# ================================================== 1 · the tree carries none ====

class ClaimsAreCountedOverTheWholeTree(unittest.TestCase):
    """`lib/**` contains no line telling a session it already holds the record.

    The assertion is over the WALK, not over a list of the seven known sites: an
    eighth one written next month has to fail this, and a file that is renamed or
    moved must not read as the claim having been removed.
    """

    def test_no_file_under_lib_claims_the_record_was_delivered(self):
        found = scan_tree(LIB)
        self.assertEqual(
            found, {},
            "these lines tell a session it already has context that nothing "
            "provides:\n" + "\n".join(
                "  %s:%d  %s" % (p, n, line.strip())
                for p, hits in sorted(found.items()) for n, line in hits))

    def test_the_scan_actually_read_the_files_it_walked(self):
        """A zero from a tree nobody read is not a pass — the exact trap the gate's
        own canary exists for. Two sentinels the shipped code definitely contains
        prove the walk reached both the module the claims lived in and the one that
        builds the prompt."""
        seen = []
        for base, dirs, files in os.walk(LIB):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            seen.extend(os.path.relpath(os.path.join(base, f), LIB) for f in files)
        self.assertIn(os.path.join("board", "cmds", "loop.py"), seen)
        self.assertIn(os.path.join("board", "succession.py"), seen)
        self.assertGreater(len(seen), 50)


# ============================================ 2 · the scanner, in both directions ====

class ScannerControl(unittest.TestCase):
    """The scanner sees a claim, and does not see a correct fix.

    Group 1 is only worth its green if this passes. Asserted on synthetic input
    because the real tree no longer contains an offence to detect, and a detector with
    nothing to detect is indistinguishable from a detector that is broken.
    """

    CLAIMS = (
        "its SessionStart injects THIS task's digest, so the ask carries the request",
        "That task's digest is already injected — this is a handoff",
        "the successor already has the context it needs",
        "the hooks inject that task's digest and the ask carries the REQUEST only",
    )

    # Three realistic post-fix wordings. All three MENTION injection; all three state
    # the truth. A scanner that counts these punishes the fixer for fixing it, goes
    # permanently red on correct work, and parks a finished task.
    CORRECT = (
        "Nothing injects the digest for you - read it with `search --detail`.",
        "Its SessionStart does not inject a digest, so fetch the record yourself.",
        "# HISTORY: this line used to say SessionStart injects the digest. It never did.",
    )

    def test_it_sees_every_phrasing_that_was_live(self):
        for line in self.CLAIMS:
            with self.subTest(line=line):
                self.assertEqual(len(claims_in(line)), 1)

    def test_it_does_not_count_the_truth_as_an_offence(self):
        for line in self.CORRECT:
            with self.subTest(line=line):
                self.assertEqual(claims_in(line), [])

    def test_a_claim_planted_in_a_walked_tree_is_found(self):
        """The walk, not just the regex: a scanner that matches correctly but never
        opens the file reports the same zero as a clean tree."""
        tmp = tempfile.mkdtemp(prefix="noinject-control-")
        try:
            deep = os.path.join(tmp, "board", "cmds")
            os.makedirs(deep)
            with io.open(os.path.join(deep, "planted.py"), "w", encoding="utf-8") as f:
                f.write("x = 1\n# its SessionStart injects that task's digest\n")
            found = scan_tree(tmp)
            self.assertEqual(list(found), [os.path.join("board", "cmds", "planted.py")])
            self.assertEqual(found[os.path.join("board", "cmds", "planted.py")][0][0], 2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ==================================================== 3 · the child is told to read ====

class TheChildPromptNamesTheRead(unittest.TestCase):
    """An invoked child is TOLD to fetch its record, in the launch prompt itself.

    Deleting the false sentence is not the whole fix. `invoke`'s ask deliberately
    carries the request ONLY, and that is safe exactly and only because something
    else points the child at the record — otherwise the reworded line describes a
    child with no context at all, which is the same correct reasoning from a false
    premise one step further along.

    It rides on the same `ref is not None` condition as the memo rail because they are
    two halves of one bargain: the record is where the work comes from and where it
    goes back.
    """

    def test_it_names_the_command_that_fetches_the_record(self):
        prompt = _loop_cmds._child_prompt("do the thing", None, None, ref=11)
        self.assertIn("task-station search --detail 11", prompt)

    def test_it_says_nothing_was_loaded_rather_than_implying_it_was(self):
        prompt = _loop_cmds._child_prompt("do the thing", None, None, ref=11)
        self.assertEqual(claims_in(prompt), [])
        self.assertIn("nothing was loaded", prompt.lower())

    def test_the_read_and_the_hand_back_travel_together(self):
        """One condition, one bargain. A child told where to report but not where to
        read is the lossy-brief boundary with extra steps."""
        prompt = _loop_cmds._child_prompt("do the thing", "implementer",
                                          "what changed file by file", ref=11)
        self.assertIn("task-station search --detail 11", prompt)
        self.assertIn("task-station memo send --task 11", prompt)

    def test_a_prompt_with_no_task_ref_still_carries_only_the_ask(self):
        """Pinned so the read line cannot leak onto the path that has no ref to name:
        a command it cannot resolve is worse than no command."""
        self.assertEqual(_loop_cmds._child_prompt("do the thing", None, None),
                         "do the thing")


# ================================================= 4 · the NEXT: guard, at write time ====

class _Args:
    """A stand-in argparse.Namespace covering every attribute `cmd_update` reads, so a
    test only sets what it means."""

    def __init__(self, **kw):
        defaults = dict(
            session=None, task=None, title=None, summary=None, append_summary=None,
            state=None, goal=None, color=None, effort=None, step_add=None,
            step_done=None, step_undone=None, decision=None, log=None, relate=None,
            pr=None, pr_desc=None, story=None, story_desc=None, depends_on=None,
            parent=None, absorbed_by=None, replaces=None, duplicates=None,
            unrelate=None)
        defaults.update(kw)
        self.__dict__.update(defaults)


class TheNextGuardFiresOnTheWriteThatWasSilent(unittest.TestCase):
    """A state line with no `NEXT:` is reported on the BARE `--state` write.

    It used to be reported only when a `--summary` came with it, so the two paths gave
    opposite answers about identical text. The write is the last moment the session
    that knows the answer is still in the room; a refused handoff, days later, is the
    wrong moment and the wrong session.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-nextguard-")
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

    def _task(self):
        t = ts.new_task("the work", "summary")
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _update(self, task, **flags):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_update(_Args(task=str(task["seq"]), **flags))
        return buf.getvalue()

    def test_a_bare_state_with_no_next_is_warned_about_at_write_time(self):
        out = self._update(self._task(), state="the parser branch is green")
        self.assertIn("COLD-READ CHECK", out)
        self.assertIn("NEXT:", out)

    def test_a_state_that_leads_with_next_stays_silent(self):
        """The guard has to be quiet on the correct case or it becomes noise on every
        write, and a warning nobody reads is worth less than no warning at all."""
        out = self._update(self._task(), state="NEXT: land the parser change in lib/x.py")
        self.assertNotIn("COLD-READ CHECK", out)

    def test_a_lower_case_next_is_accepted_like_everywhere_else(self):
        """`leads_with_next` is case- and whitespace-tolerant by design: the question
        is whether the line opens with a first move, not whether it was typed in
        capitals. One predicate, one answer, on every path that asks."""
        out = self._update(self._task(), state="  next: land the parser change")
        self.assertNotIn("COLD-READ CHECK", out)

    def test_clearing_the_state_is_not_this_check_s_finding(self):
        """An empty slot is `empty_slots`' finding. Two verbs answering for one gap is
        how a report starts contradicting itself, so this one mirrors
        `cold_read_failures` and says nothing about a cleared line."""
        t = self._task()
        self._update(t, state="NEXT: something")
        out = self._update(ts.load_task(t["id"]), state="")
        self.assertNotIn("COLD-READ CHECK", out)

    def test_the_checkpoint_path_reports_it_once_and_not_twice(self):
        """A `--summary` and a `--state` together stamp a checkpoint, and the
        checkpoint block already reports the same finding. One write must never print
        it twice."""
        out = self._update(self._task(), summary="the present truth",
                           state="the parser branch is green")
        self.assertEqual(out.count("COLD-READ CHECK"), 1)

    def test_an_unchanged_state_line_is_not_re_reported(self):
        """Re-writing the identical text does not make a stale line fresher, and it
        must not re-open a warning the author has already been given — the same
        `state_changed` rule every other write-time notice follows."""
        t = self._task()
        self._update(t, state="the parser branch is green")
        out = self._update(ts.load_task(t["id"]), state="the parser branch is green")
        self.assertNotIn("COLD-READ CHECK", out)


if __name__ == "__main__":
    unittest.main()
