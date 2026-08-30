"""SESSION SUCCESSION — THE RELAY. A long session hands itself off before it degrades,
and the successor loses nothing.

WHAT THIS COVERS. A working session fills up. Today the only thing that happens at that
point is the harness's own auto-compaction: a model-authored summary nobody audited,
produced on the harness's schedule rather than the work's. The relay is the alternative —
stop at a chosen moment, write the record properly, and hand the task to a fresh session
that starts clean. Four things have to be true for that to be worth doing, and this file
pins one per class.

  1. THE SESSION KNOWS WHERE IT STANDS (`ContextReport`). Occupancy is MEASURED from the
     transcript's own usage block, not guessed from file size, and the verdict is one of
     three — keep going, relay, compact — decided by two numbers that are both printed.
     An occupancy nobody could measure is NOT "keep going": a policy that could not run
     reports that it could not run, exactly as an exit condition that did not execute is
     UNKNOWN rather than met.

  2. THE PROMPT COMES FROM THE RECORD (`ContinuationPrompt`). The prompt POINTS at the
     record and never copies it — a restatement is a lossy second copy of a thing the
     successor can fetch in one command, the rule `invoke --ask` was built on. It also
     never tells the successor that record was delivered for it, because nothing
     delivers one (#583). The prompt is generated from the task fields alone and is
     structurally incapable of reading the transcript, which the sentinel tests here
     prove rather than assert.

  2b. ONE VOICE CANNOT ARRIVE AS TWO (`AttributedStateLine`). The state line is the
     predecessor's sentence, written in the imperative voice its own template asks for,
     and it used to be interpolated bare into the successor's prompt. On 2026-08-29 a
     successor woke holding `NEXT: WATCH PR 1615 AND MERGE IT`, was sent the same words
     again by peer message from that same predecessor, counted one voice as two
     agreeing, and merged another engineer's PR on a shared repo. The line is now
     quoted under an attribution that names whose record it is, and these tests pin
     that an unattributed interpolation cannot come back.

  3. THE SUCCESSOR IS THE SAME TASK (`SuccessorSpawn`). This is the one line that
     separates a relay from an `invoke`: no child is created, no parent edge is drawn,
     and the minted session links to the task that is handing off. It also inherits the
     predecessor's model selection — a relay that silently dropped a 1M window to 200k
     would hand the successor one fifth of the context to finish the same work in.

  4. THE HANDOFF IS GRADEABLE (`GradedHandoff`). A relay happens inside one task's life,
     so nothing about it ever reached the parent's gate — a thin handoff was invisible.
     Each one is now a ledger entry carrying the mechanical evidence a grader needs, and
     it is graded through the SAME `grade` verb and the SAME six dimensions as any other
     child work, linked to the handoff it judged so two relays cannot be confused.

THE TRANSCRIPTS HERE ARE REAL FILES. `measure_context_tokens` reverse-scans a JSONL tail
for the newest `usage` block; a stubbed measurement would test the stub, so every test
that depends on occupancy writes an actual transcript and lets the shipped reader read
it.
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(_REPO_ROOT, "lib")
sys.path.insert(0, LIB)

_TMP_HOME = tempfile.mkdtemp(prefix="ts-succession-")
os.environ["TASK_STATION_HOME"] = _TMP_HOME

import store                                                            # noqa: E402
import succession as _succ                                              # noqa: E402
import loop as _loop                                                    # noqa: E402
# The scan's row RENDERER is seam-private (not in the seam's __all__, so not on the
# facade). Reached through the seam directly, the way test_invoke_hardening reaches
# `board.workspace` — the alternative is widening a public surface for one assertion.
from board.cmds import loop as _loop_cmds                                # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)

# The window every test sizes against unless it says otherwise. Pinned through the
# EXPLICIT override (`TASK_STATION_CONTEXT_WINDOW`), which `effective_context_window`
# honours ahead of everything else — so no test depends on the developer's own `/model`
# pick, and none of them has to fake the HUD snapshot.
WINDOW = 200000


class _Args:
    """`relay`'s argv, with every flag this file is about defaulting to OFF so each test
    states the behaviour it means."""

    def __init__(self, **kw):
        defaults = dict(session=None, task=None, spawn=False, force=False, cwd=None,
                        model=None, print_command=False, as_json=False)
        defaults.update(kw)
        self.__dict__.update(defaults)


class _GradeArgs:
    """`grade`'s argv — the existing verb, plus the `--handoff` link this task adds."""

    def __init__(self, **kw):
        defaults = dict(session=None, task=None, dim=None, threshold=None, note=None,
                        park=None, why=None, no_decision=True, as_json=False,
                        handoff=None)
        defaults.update(kw)
        self.__dict__.update(defaults)


class _SuccessionTest(unittest.TestCase):
    """A throwaway store, a pinned context window, real transcripts on disk, and a
    window opener that records instead of opening one."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="succession-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        store.reset_cache()
        self._orig_window = os.environ.get("TASK_STATION_CONTEXT_WINDOW")
        os.environ["TASK_STATION_CONTEXT_WINDOW"] = str(WINDOW)
        # The parent's model SELECTION, pinned to "nothing configured" for every test
        # that is not about inheritance. It is read from the real ~/.claude/settings.json,
        # so a developer on `opus[1m]` would otherwise see their own window marker turn
        # up in the successor's --model and fail the tests that assert its absence.
        self._orig_sel = ts.claude_code_model_selection
        ts.claude_code_model_selection = lambda: ""
        # Transcripts live here; `_find_session_path` is routed, so pointing it at this
        # map is what lets the SHIPPED measurement code read a real file.
        self.transcripts = {}
        self._orig_find = ts._find_session_path
        ts._find_session_path = lambda sid: self.transcripts.get(sid)
        self.opened = []
        self._orig_open = ts._open_jump_window
        ts._open_jump_window = lambda cmd: (self.opened.append(cmd) or True)

    def tearDown(self):
        ts._open_jump_window = self._orig_open
        ts._find_session_path = self._orig_find
        ts.claude_code_model_selection = self._orig_sel
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        if self._orig_window is None:
            os.environ.pop("TASK_STATION_CONTEXT_WINDOW", None)
        else:
            os.environ["TASK_STATION_CONTEXT_WINDOW"] = self._orig_window
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- fixtures ----------------------------------------------------------------

    def _transcript(self, sid, tokens, extra=None, usage=True):
        """A REAL transcript for `sid` whose newest `usage` block sums to `tokens`.

        `extra` is planted as free text on an earlier line — the sentinel the prompt
        tests look for, to prove the generator never reads this file. `usage=False`
        writes a transcript with no usage block at all, which is the unmeasurable case.
        """
        d = os.path.join(self.tmp, "transcripts")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "%s.jsonl" % sid)
        lines = [{"type": "user", "cwd": self.tmp,
                  "message": {"role": "user", "content": extra or "hello"}}]
        if usage:
            lines.append({"type": "assistant", "message": {
                "role": "assistant", "model": "claude-opus-5",
                "usage": {"input_tokens": tokens, "cache_read_input_tokens": 0,
                          "cache_creation_input_tokens": 0, "output_tokens": 7}}})
        with open(path, "w", encoding="utf-8") as f:
            for o in lines:
                f.write(json.dumps(o) + "\n")
        self.transcripts[sid] = path
        return path

    def _task(self, title="the work", ready=True):
        """A task with a session attached. `ready=True` gives it a record that can
        actually carry a handoff — every named slot filled, a `NEXT:`-led state, and a
        checkpoint stamped with its baseline counts, which is what a real `/todo save`
        leaves behind."""
        t = ts.new_task(title, "SUMMARY-SENTINEL: the present truth of the work.")
        ts.save_task(t)
        ts.ensure_seqs()
        t = ts.load_task(t["id"])
        sid = "1111aaaa-0000-0000-0000-000000000001"
        t.setdefault("sessions", []).append(sid)
        t.setdefault("session_meta", {})[sid] = {
            "cwd": self.tmp, "ts": 1000.0, "role": "hub", "ordinal": 0}
        t["hub_ordinal_next"] = 1
        if ready:
            t["goal"] = "GOAL-SENTINEL: done looks like this."
            t["state"] = "NEXT: land the parser change in lib/x.py — the branch is green."
            t["steps"] = [{"text": "wire the parser", "done": True},
                          {"text": "cover the edge case", "done": False},
                          {"text": "ship it", "done": False}]
            t["decisions"] = [{"text": "DECISION-SENTINEL: chose the table over a regex.",
                               "ts": 900.0}]
            t["prs"] = [{"url": "https://example.invalid/pr/1", "desc": "the PR"}]
            t["last_full_save_ts"] = 950.0
            t["saved_counts"] = {"decisions": 1, "history": 0, "steps": 3}
            t["history"] = []
        ts.save_task(t)
        ts.set_link(sid, t["id"])
        store.reset_cache()
        return ts.load_task(t["id"]), sid

    # -- drivers -----------------------------------------------------------------

    def _relay(self, **kw):
        """Run `relay` and return `(stdout, exit_code)`. `SystemExit` is caught so a
        refusal is an assertion about a code, not an aborted test."""
        buf = io.StringIO()
        code = 0
        try:
            with redirect_stdout(buf):
                ts.cmd_relay(_Args(**kw))
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
        return buf.getvalue(), code

    def _grade(self, **kw):
        buf = io.StringIO()
        code = 0
        try:
            with redirect_stdout(buf):
                ts.cmd_grade(_GradeArgs(**kw))
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
        return buf.getvalue(), code

    def _report(self, task, sid, tokens, ready=True):
        """The context report the engine computes for a session sitting at `tokens`."""
        self._transcript(sid, tokens)
        return _succ.report(task, ts.measure_context_tokens(sid),
                            ts.effective_context_window(sid), session=sid)


# ============================================================ 1 · the report ====

class ContextReport(_SuccessionTest):
    """A session reports its own context usage and whether a relay is due.

    THE POLICY IS TWO NUMBERS, both printed so the verdict can be audited: a TRIGGER
    (a percentage of the window, the same signal the checkpoint nudge already fires on)
    and a RESERVE (an absolute token count — what the handoff sequence itself costs).
    Below the trigger nothing is due. At or above it, the relay happens if the reserve is
    still available and gives way to compaction if it is not, because a checkpoint
    authored with no headroom left is thinner than the auto-summary it was meant to beat.

    The reserve is absolute rather than a second percentage because the handoff work
    costs what it costs: writing a summary and a continuation prompt is the same job on a
    200k window as on a 1M one, and 20% of the former is 4% of the latter.
    """

    def test_occupancy_is_measured_from_the_transcript(self):
        task, sid = self._task()
        rep = self._report(task, sid, 130000)
        self.assertEqual(rep["measured"], 130000)
        self.assertEqual(rep["window"], WINDOW)
        self.assertEqual(rep["remaining"], WINDOW - 130000)
        self.assertEqual(rep["used_pct"], 65)
        self.assertEqual(rep["left_pct"], 35)

    def test_below_the_trigger_is_keep_going(self):
        task, sid = self._task()
        rep = self._report(task, sid, 40000)
        self.assertEqual(rep["verdict"], _succ.KEEP_GOING)
        self.assertIn("%d%%" % rep["trigger_pct"], rep["why"])

    def test_at_the_trigger_a_relay_is_due(self):
        task, sid = self._task()
        rep = self._report(task, sid, 130000)
        self.assertEqual(rep["verdict"], _succ.RELAY)

    def test_one_token_below_the_trigger_is_still_keep_going(self):
        """The boundary is the trigger in TOKENS, and the report prints that number —
        a rounded percentage cannot be checked against a decision made on tokens."""
        task, sid = self._task()
        rep = self._report(task, sid, 129999)
        self.assertEqual(rep["trigger_tokens"], 130000)
        self.assertEqual(rep["verdict"], _succ.KEEP_GOING)

    def test_no_headroom_for_the_handoff_is_compact(self):
        task, sid = self._task()
        rep = self._report(task, sid, WINDOW - _succ.DEFAULT_RESERVE + 1)
        self.assertEqual(rep["verdict"], _succ.COMPACT)
        self.assertIn("reserve", rep["why"])

    def test_exactly_the_reserve_still_relays(self):
        task, sid = self._task()
        rep = self._report(task, sid, WINDOW - _succ.DEFAULT_RESERVE)
        self.assertEqual(rep["remaining"], _succ.DEFAULT_RESERVE)
        self.assertEqual(rep["verdict"], _succ.RELAY)

    def test_the_reserve_only_speaks_once_a_relay_is_due(self):
        """A window SMALLER than the reserve would otherwise report `compact` from the
        very first token — a session at 12% told to compact, which is nonsense. The
        reserve answers "can the handoff be afforded", and that question is only asked
        once the trigger says a handoff is wanted."""
        task, sid = self._task()
        self._transcript(sid, 6000)
        rep = _succ.report(task, 6000, 30000, session=sid)
        self.assertLess(rep["remaining"], _succ.DEFAULT_RESERVE)
        self.assertLess(rep["measured"], rep["trigger_tokens"])
        self.assertEqual(rep["verdict"], _succ.KEEP_GOING)

    def test_an_unmeasurable_session_is_never_keep_going(self):
        """A transcript with no usage block means the policy DID NOT RUN. Reporting
        `keep-going` there would be indistinguishable from measuring room to spare —
        the same reason an exit condition that failed to execute is UNKNOWN and not met.
        """
        task, sid = self._task()
        self._transcript(sid, 0, usage=False)
        rep = _succ.report(task, ts.measure_context_tokens(sid),
                           ts.effective_context_window(sid), session=sid)
        self.assertEqual(rep["measured"], 0)
        self.assertEqual(rep["verdict"], _succ.UNKNOWN)
        self.assertNotEqual(rep["verdict"], _succ.KEEP_GOING)
        self.assertIn("could not", rep["why"].lower())

    def test_a_relay_that_is_due_but_not_ready_says_both(self):
        """DUE and READY are separate facts. A record with gaps still needs the relay;
        what it cannot do yet is survive one, and the report names the gaps rather than
        folding them into the verdict."""
        task, sid = self._task(ready=False)
        rep = self._report(task, sid, 130000)
        self.assertEqual(rep["verdict"], _succ.RELAY)
        self.assertFalse(rep["ready"])
        self.assertTrue(rep["blockers"])
        joined = " ".join(rep["blockers"])
        self.assertIn("goal", joined)
        self.assertIn("state", joined)

    def test_a_full_record_is_ready(self):
        task, sid = self._task(ready=True)
        rep = self._report(task, sid, 130000)
        self.assertTrue(rep["ready"], rep["blockers"])
        self.assertEqual(rep["blockers"], [])

    def test_a_stale_checkpoint_blocks_the_handoff(self):
        """Six decisions landed after the last full checkpoint, so the digest the
        successor would load describes an older task. The relay is still due; the
        checkpoint has to be re-taken first."""
        task, sid = self._task(ready=True)
        task["decisions"] = task["decisions"] + [
            {"text": "later call %d" % i, "ts": 1000.0 + i} for i in range(6)]
        rep = self._report(task, sid, 130000)
        self.assertFalse(rep["ready"])
        self.assertTrue(any("checkpoint" in b for b in rep["blockers"]),
                        rep["blockers"])

    def test_never_checkpointed_blocks_the_handoff(self):
        task, sid = self._task(ready=True)
        task.pop("last_full_save_ts", None)
        task.pop("saved_counts", None)
        rep = self._report(task, sid, 130000)
        self.assertFalse(rep["ready"])
        self.assertTrue(any("never" in b for b in rep["blockers"]), rep["blockers"])

    def test_the_cli_prints_the_verdict_and_both_numbers(self):
        task, sid = self._task()
        self._transcript(sid, 130000)
        out, code = self._relay(task=str(task["seq"]), session=sid)
        self.assertEqual(code, 0, out)
        self.assertIn(_succ.RELAY, out)
        self.assertIn("65%", out)
        self.assertIn("130,000", out)

    def test_the_json_carries_the_same_computation(self):
        task, sid = self._task()
        self._transcript(sid, 130000)
        out, code = self._relay(task=str(task["seq"]), session=sid, as_json=True)
        self.assertEqual(code, 0, out)
        doc = json.loads(out)
        self.assertEqual(doc["verdict"], _succ.RELAY)
        self.assertEqual(doc["measured"], 130000)
        self.assertEqual(doc["window"], WINDOW)
        self.assertEqual(doc["trigger_tokens"], 130000)
        self.assertEqual(doc["reserve"], _succ.DEFAULT_RESERVE)

    def test_the_report_writes_nothing(self):
        """The bare verb is the preview, so it has to cost nothing — no session minted,
        no event, no field touched. `invoke` needed a flag for this; here it is the
        default, and the flag is what OPENS the window."""
        task, sid = self._task()
        self._transcript(sid, 130000)
        before = json.dumps(ts.load_task(task["id"]), sort_keys=True, default=str)
        out, code = self._relay(task=str(task["seq"]), session=sid)
        self.assertEqual(code, 0, out)
        after = json.dumps(ts.load_task(task["id"]), sort_keys=True, default=str)
        self.assertEqual(before, after)
        self.assertEqual(self.opened, [])

    def test_the_trigger_is_configurable_and_has_no_off_switch(self):
        """The trigger tunes; it does not disable. Unlike the checkpoint NAG — which
        interrupts and therefore needs an off switch — this is a report somebody asked
        for, and "off" would only mean the answer is always `keep-going`, which is a lie
        at 99%. So a non-positive or unparseable setting falls back to the default rather
        than turning the policy into a rubber stamp."""
        os.environ["TASK_STATION_SUCCESSION_PCT"] = "40"
        try:
            self.assertEqual(_succ.trigger_pct(), 40)
            for bad in ("0", "-5", "off", "nonsense", ""):
                os.environ["TASK_STATION_SUCCESSION_PCT"] = bad
                self.assertEqual(_succ.trigger_pct(), _succ.DEFAULT_TRIGGER_PCT, bad)
            os.environ["TASK_STATION_SUCCESSION_PCT"] = "999"
            self.assertEqual(_succ.trigger_pct(), 95)
        finally:
            os.environ.pop("TASK_STATION_SUCCESSION_PCT", None)

    def test_the_report_lines_name_the_next_move(self):
        """A verdict with no instruction is a status light. Each one says what to do."""
        task, sid = self._task()
        for tokens, needle in ((40000, "keep"), (130000, "relay"),
                               (WINDOW - 1000, "compact")):
            rep = self._report(task, sid, tokens)
            text = " ".join(_succ.report_lines(rep)).lower()
            self.assertIn(needle, text)


# =============================================== 2 · the continuation prompt ====

class ContinuationPrompt(_SuccessionTest):
    """The prompt is generated from the RECORD, never from the transcript.

    It carries the REQUEST only and names the command that fetches the rest, which is the
    rule `invoke --ask` is built on and the reason it warns when an ask grows past 800
    characters: anything restating the record is a lossy copy of something the successor
    can read for itself. Here it is not a warning but a bound, because nobody types this
    one.
    """

    def _prompt(self, task, blockers=None, rep=None):
        return _succ.continuation_prompt(task, rep=rep, blockers=blockers,
                                         predecessor="538-0", successor="538-1")

    def test_the_transcript_is_never_read(self):
        """A sentinel planted in the predecessor's transcript must not surface. The
        generator takes a task dict and no session, so it CANNOT read one — this pins
        that the signature stays that way."""
        task, sid = self._task()
        self._transcript(sid, 130000, extra="TRANSCRIPT-SENTINEL-do-not-copy-me")
        prompt = self._prompt(task)
        self.assertNotIn("TRANSCRIPT-SENTINEL", prompt)

    def test_the_digest_is_not_restated(self):
        """Summary, goal and decision bodies all reach the successor through the digest.
        Repeating them here would double the cost and create a second copy that drifts.
        """
        task, _sid = self._task()
        prompt = self._prompt(task)
        self.assertNotIn("SUMMARY-SENTINEL", prompt)
        self.assertNotIn("GOAL-SENTINEL", prompt)
        self.assertNotIn("DECISION-SENTINEL", prompt)

    def test_it_names_the_command_that_fetches_the_record(self):
        """A pointer is only a pointer if it says where. `search --detail <seq>` is the
        one command that returns the whole record, so the prompt names it with this
        task's own number rather than describing it."""
        task, _sid = self._task()
        prompt = self._prompt(task)
        self.assertIn("task-station search --detail %s" % task["seq"], prompt)

    def test_it_never_claims_the_record_was_delivered(self):
        """#583: this prompt used to open by telling the successor its SessionStart had
        already injected the digest. Nothing injects one — not for a relayed session and
        not for an invoked one — so the sentence talked a session out of the single read
        it most needed. A claim in EITHER of the phrasings that were live is a
        regression."""
        task, _sid = self._task()
        low = self._prompt(task).lower()
        for claim in ("already injected", "already has the context",
                      "sessionstart injects", "is already"):
            self.assertNotIn(claim, low)

    def test_the_caps_are_not_the_fix_and_stay_where_they_are(self):
        """Pinned because the obvious wrong repair for both #583 and #585 is to widen
        the clip so "more context" travels. The clip was never the defect: the false
        premise was, and #585's was an unattributed one. Raising either number makes a
        relay prompt into the context dump this module exists to avoid."""
        self.assertEqual(_succ.NEXT_CHARS, 320)
        self.assertEqual(_succ.PROMPT_BUDGET, 1600)
        self.assertEqual((_succ.STEP_CAP, _succ.STEP_CHARS), (5, 60))

    def test_it_carries_the_next_move(self):
        """The one thing the digest cannot supply as an instruction: the concrete first
        move. Without it the successor opens with standing and has to re-derive."""
        task, _sid = self._task()
        prompt = self._prompt(task)
        self.assertIn("land the parser change in lib/x.py", prompt)

    def test_it_names_the_open_steps_and_not_the_finished_ones(self):
        task, _sid = self._task()
        prompt = self._prompt(task)
        self.assertIn("cover the edge case", prompt)
        self.assertIn("ship it", prompt)
        self.assertNotIn("wire the parser", prompt)

    def test_it_names_the_task_and_both_sessions(self):
        task, _sid = self._task()
        prompt = self._prompt(task)
        self.assertIn("#%s" % task["seq"], prompt)
        self.assertIn("538-0", prompt)
        self.assertIn("538-1", prompt)

    def test_it_is_pure_over_the_record(self):
        """Same record in, same prompt out — twice, with nothing else supplied. A
        generator that reached for ambient state could not promise this."""
        task, _sid = self._task()
        self.assertEqual(self._prompt(task), self._prompt(task))

    def test_it_stays_within_budget_however_big_the_record(self):
        """Twenty open steps and a runaway state line still produce a bounded prompt:
        the step list is capped and says how many it dropped, and the NEXT line is
        previewed with a pointer to the digest that holds all of it. An unbounded
        generator would reintroduce the context dump this design exists to remove."""
        task, _sid = self._task()
        task["steps"] = [{"text": "step number %d with a long descriptive title" % i,
                          "done": False} for i in range(20)]
        task["state"] = "NEXT: " + ("x" * 5000)
        prompt = self._prompt(task)
        self.assertLessEqual(len(prompt), _succ.PROMPT_BUDGET, prompt)
        self.assertIn("more", prompt)

    def test_a_forced_handoff_carries_its_own_gaps(self):
        """Forcing past a failed cold-read is sometimes right — at 95% a degraded
        handoff beats none. What must never happen is a successor that cannot tell. The
        gaps travel IN THE PROMPT, not only in the ledger."""
        task, _sid = self._task(ready=False)
        blockers = _succ.handoff_blockers(task)
        self.assertTrue(blockers)
        prompt = self._prompt(task, blockers=blockers)
        self.assertIn("GAPS", prompt)
        self.assertIn("goal", prompt)

    def test_an_unforced_prompt_has_no_gaps_block(self):
        task, _sid = self._task(ready=True)
        self.assertEqual(_succ.handoff_blockers(task), [])
        self.assertNotIn("GAPS", self._prompt(task))

    def test_it_reports_where_the_predecessor_stopped(self):
        """The successor should know it is a relay and not a fresh start — including how
        full the session that handed off actually was."""
        task, sid = self._task()
        rep = self._report(task, sid, 130000)
        prompt = self._prompt(task, rep=rep)
        self.assertIn("65%", prompt)

    def test_a_state_with_no_next_is_not_dressed_up_as_one(self):
        """`state` that reports standing instead of a first move must not be printed
        under a `NEXT:` heading — that would invent an instruction the predecessor never
        left."""
        task, _sid = self._task()
        task["state"] = "the branch is green and review is pending"
        prompt = self._prompt(task, blockers=_succ.handoff_blockers(task))
        self.assertNotIn("NEXT: the branch is green", prompt)
        self.assertIn("no next move", prompt.lower())


# ============================================ 2b · one voice, arriving once ====

class AttributedStateLine(_SuccessionTest):
    """THE STATE LINE IS QUOTED, NOT ISSUED — the fix for the 2026-08-29 incident.

    THE MECHANISM IT CLOSES, in one line: there was ONE source and the successor
    perceived TWO agreeing. The relay built the successor's prompt out of the
    predecessor's state line and interpolated it bare, in the imperative voice the state
    format's own template asks for (`NEXT: <the concrete first move>`), directly under a
    sentence telling the successor what to do. The predecessor then sent the same words
    by peer message. The successor's own account: "Ryan's relay to me did say WATCH PR
    1615 AND MERGE IT, and yours said the same independently, so I treated it as
    authorized." A tool that turns one opinion into apparent consensus is worse than one
    that says nothing, because the recipient reasons MORE confidently from it.

    "JUST WRITE BETTER STATE LINES" IS NOT A FIX, and that is why the repair is here
    rather than in guidance. The field has two audiences with different needs — a
    RESUMING session reads it as orientation, a RELAYED session reads it as an order —
    and one string serves both with nothing in it saying which. So the marker is added
    by the thing that knows: the relay, at the moment it hands the string to somebody
    who was not there.
    """

    # The incident's own sentence, kept verbatim. A paraphrase would let a future edit
    # pass a test the real text fails.
    INCIDENT = ("NEXT: WATCH PR 1615 AND MERGE IT — it is the fix for the blocker and "
                "I have approved it (vote 10).")

    def _prompt(self, task, **kw):
        return _succ.continuation_prompt(task, predecessor="503-13", successor="503-14",
                                         **kw)

    def _with_state(self, state):
        task, _sid = self._task()
        task["state"] = state
        ts.save_task(task)
        store.reset_cache()
        return ts.load_task(task["id"])

    def test_the_state_line_is_introduced_as_the_predecessors_record(self):
        task, _sid = self._task()
        prompt = self._prompt(task)
        self.assertIn("YOUR PREDECESSOR'S STATE LINE", prompt)
        self.assertIn("not an order from your user", prompt)

    def test_an_unattributed_interpolation_cannot_regress(self):
        """THE REGRESSION GUARD, and it is the point of this class. It does not look for
        a phrase — it looks at POSITION: wherever the predecessor's words appear, an
        attribution must already have been read. Reword the frame however you like and
        this still holds; delete it, or move the state line above it, and this fails."""
        task = self._with_state(self.INCIDENT)
        prompt = self._prompt(task)
        at = prompt.find("WATCH PR 1615")
        self.assertNotEqual(at, -1, "the next move must still reach the successor")
        head = prompt[:at]
        self.assertIn("PREDECESSOR", head.upper())
        self.assertIn("not an order from your user", head)

    def test_an_outward_imperative_names_whose_authority_it_is(self):
        """The second half, and the half that engages the compounding: a peer message
        from the SPAWNING predecessor is the same voice, not a second opinion."""
        task = self._with_state(self.INCIDENT)
        prompt = self._prompt(task)
        self.assertIn("act outward (merge)", prompt)
        self.assertIn("not your user's", prompt)
        self.assertIn("one voice twice", prompt)

    def test_an_ordinary_next_move_gets_the_frame_and_no_warning(self):
        """A warning on every relay is a warning nobody reads. The attribution is
        unconditional; the authority sentence fires only on an outward verb."""
        task, _sid = self._task()          # NEXT: land the parser change in lib/x.py
        prompt = self._prompt(task)
        self.assertIn("not an order from your user", prompt)
        self.assertNotIn("act outward", prompt)

    def test_a_past_tense_report_of_the_same_action_is_not_an_order(self):
        """The shape a good state line actually uses. `merged` is a report of what
        landed; only the bare imperative is an instruction, and a check that could not
        tell them apart would fire on almost every state line this board has ever
        stored."""
        task = self._with_state("NEXT: PR 1615 was merged by Chris and dev is green — "
                                "pick up the parser change next.")
        self.assertNotIn("act outward", self._prompt(task))

    def test_the_frame_is_never_the_part_that_gets_trimmed(self):
        """A prompt long enough to hit PROMPT_BUDGET loses its TAIL. The framing, the
        attributed line and the authority sentence are first for that reason, so the
        clamp can never produce the exact prompt this task exists to prevent."""
        task = self._with_state("NEXT: " + ("merge and deploy and delete the branch, "
                                            "then close it. " * 20))
        task["goal"] = task["summary"] = ""
        task["decisions"], task["prs"], task["stories"] = [], [], []
        task["steps"] = [{"text": "x" * 200, "done": False} for _ in range(9)]
        ts.save_task(task)
        store.reset_cache()
        task = ts.load_task(task["id"])
        prompt = self._prompt(task, blockers=_succ.handoff_blockers(task),
                              rep={"window": 200000, "used_pct": 97})
        self.assertLessEqual(len(prompt), _succ.PROMPT_BUDGET)
        self.assertIn("(trimmed", prompt)        # this really is the clamped case
        self.assertIn("not an order from your user", prompt)
        self.assertIn("act outward", prompt)

    def test_a_state_with_no_next_still_says_whose_account_it_is(self):
        task = self._with_state("everything is fine, nothing to report")
        prompt = self._prompt(task)
        self.assertIn("Your predecessor left no next move", prompt)


# ================================================== 3 · spawning the successor ====

class SuccessorSpawn(_SuccessionTest):
    """The successor is attached to the SAME task.

    That is the whole difference from `invoke`, which spawns a child onto a different
    task record. Everything else is deliberately the same substrate — the pre-bound
    session id, the workspace trust pass, the window opener, the MANUAL LAUNCH
    distinction — because a second spawner would be a second set of bugs.
    """

    def _spawned_sid(self, task_id, before):
        after = set(ts.load_task(task_id).get("sessions") or [])
        new = sorted(after - set(before))
        self.assertEqual(len(new), 1, new)
        return new[0]

    def test_it_spawns_onto_the_same_task(self):
        task, sid = self._task()
        self._transcript(sid, 130000)
        before = list(task.get("sessions") or [])
        out, code = self._relay(task=str(task["seq"]), session=sid, spawn=True)
        self.assertEqual(code, 0, out)
        new = self._spawned_sid(task["id"], before)
        self.assertEqual(ts.get_link(new), task["id"])
        self.assertIn(new, (ts.load_task(task["id"]).get("session_meta") or {}))

    def test_it_creates_no_child_task(self):
        task, sid = self._task()
        self._transcript(sid, 130000)
        n_before = len(ts.all_tasks())
        out, code = self._relay(task=str(task["seq"]), session=sid, spawn=True)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(ts.all_tasks()), n_before)
        self.assertEqual(ts.load_task(task["id"]).get("related") or [], [])

    def test_the_window_carries_the_continuation_prompt(self):
        task, sid = self._task()
        self._transcript(sid, 130000)
        out, code = self._relay(task=str(task["seq"]), session=sid, spawn=True)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.opened), 1, self.opened)
        cmd = self.opened[0]
        self.assertIn("land the parser change in lib/x.py", cmd)
        self.assertIn("--session-id", cmd)

    def test_the_successor_inherits_the_1m_window(self):
        """A bare alias is a 200k window. A relay that dropped `[1m]` would hand the
        successor one fifth of the context to finish the same work in — the same
        unasked-for downgrade `invoke` already refuses to make."""
        ts.claude_code_model_selection = lambda: "claude-opus-5[1m]"
        task, sid = self._task()
        self._transcript(sid, 130000)
        out, code = self._relay(task=str(task["seq"]), session=sid, spawn=True)
        self.assertEqual(code, 0, out)
        self.assertIn("[1m]", self.opened[0])

    def test_no_model_flag_when_nothing_is_configured(self):
        """Silence inherits the harness default; emitting a guess would replace it."""
        task, sid = self._task()
        self._transcript(sid, 130000)
        out, code = self._relay(task=str(task["seq"]), session=sid, spawn=True)
        self.assertEqual(code, 0, out)
        self.assertNotIn("--model", self.opened[0])

    def test_an_explicit_model_wins(self):
        ts.claude_code_model_selection = lambda: "claude-opus-5[1m]"
        task, sid = self._task()
        self._transcript(sid, 130000)
        out, code = self._relay(task=str(task["seq"]), session=sid, spawn=True,
                                model="sonnet")
        self.assertEqual(code, 0, out)
        self.assertIn("--model sonnet", self.opened[0])

    def test_the_spawn_line_does_not_claim_the_digest_was_delivered(self):
        """#583. This line was one of the two that EXECUTE — a live `print()` on every
        `relay --spawn`, telling the operator "its SessionStart injects the same digest
        you have been working from". Nothing injects one, so the successor's own prompt
        (which correctly points at the record) was contradicted by the line printed
        directly above it."""
        task, sid = self._task()
        self._transcript(sid, 130000)
        out, code = self._relay(task=str(task["seq"]), session=sid, spawn=True)
        self.assertEqual(code, 0, out)
        low = out.lower()
        for claim in ("sessionstart injects", "already injected",
                      "already has the context"):
            self.assertNotIn(claim, low)

    def test_the_spawn_line_names_the_read_the_prompt_names(self):
        """The report and the prompt must say the same thing. They disagreed for
        longer than they agreed, and the reader believes whichever one they read
        first."""
        task, sid = self._task()
        self._transcript(sid, 130000)
        out, code = self._relay(task=str(task["seq"]), session=sid, spawn=True)
        self.assertEqual(code, 0, out)
        read = "task-station search --detail %s" % task["seq"]
        self.assertIn(read, out)
        self.assertIn(read, self.opened[0])

    def test_the_hub_ordinal_advances(self):
        task, sid = self._task()
        self._transcript(sid, 130000)
        before = list(task.get("sessions") or [])
        out, code = self._relay(task=str(task["seq"]), session=sid, spawn=True)
        self.assertEqual(code, 0, out)
        fresh = ts.load_task(task["id"])
        new = self._spawned_sid(task["id"], before)
        self.assertEqual(ts.hub_ordinal(fresh, new), 1)
        self.assertIn("%s-1" % task["seq"], out)

    def test_keep_going_is_refused(self):
        """A relay at 20% throws away warm context for nothing. The refusal names the
        numbers so it is arguable, and `--force` is how you argue."""
        task, sid = self._task()
        self._transcript(sid, 40000)
        out, code = self._relay(task=str(task["seq"]), session=sid, spawn=True)
        self.assertEqual(code, 3)
        self.assertIn(_succ.KEEP_GOING, out)
        self.assertEqual(self.opened, [])
        self.assertEqual(len(ts.load_task(task["id"]).get("sessions") or []), 1)

    def test_a_record_that_cannot_carry_it_is_refused(self):
        task, sid = self._task(ready=False)
        self._transcript(sid, 130000)
        out, code = self._relay(task=str(task["seq"]), session=sid, spawn=True)
        self.assertEqual(code, 3)
        self.assertIn("goal", out)
        self.assertEqual(self.opened, [])

    def test_an_unmeasurable_session_is_refused(self):
        """No measurement means no verdict, and a spawn on no verdict is a guess."""
        task, sid = self._task()
        self._transcript(sid, 0, usage=False)
        out, code = self._relay(task=str(task["seq"]), session=sid, spawn=True)
        self.assertEqual(code, 3)
        self.assertIn(_succ.UNKNOWN, out)
        self.assertEqual(self.opened, [])

    def test_force_overrides_both_refusals(self):
        task, sid = self._task(ready=False)
        self._transcript(sid, 40000)
        out, code = self._relay(task=str(task["seq"]), session=sid, spawn=True,
                                force=True)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.opened), 1)

    def test_print_command_is_a_manual_launch(self):
        """The human runs the printed line, so the session is still pre-attached — but
        the trail must not claim a window opened. Same rule `invoke` learned when one
        child read as two invokes."""
        task, sid = self._task()
        self._transcript(sid, 130000)
        out, code = self._relay(task=str(task["seq"]), session=sid, spawn=True,
                                print_command=True)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.opened, [])
        trail = " ".join(e.get("text", "")
                         for e in (ts.load_task(task["id"]).get("events") or []))
        self.assertIn(ts.MANUAL_LAUNCH, trail)

    def test_a_window_launch_is_not_a_manual_launch(self):
        task, sid = self._task()
        self._transcript(sid, 130000)
        out, code = self._relay(task=str(task["seq"]), session=sid, spawn=True)
        self.assertEqual(code, 0, out)
        trail = " ".join(e.get("text", "")
                         for e in (ts.load_task(task["id"]).get("events") or []))
        self.assertNotIn(ts.MANUAL_LAUNCH, trail)
        self.assertIn("relay", trail.lower())

    def test_the_trail_names_both_sides(self):
        task, sid = self._task()
        self._transcript(sid, 130000)
        before = list(task.get("sessions") or [])
        out, code = self._relay(task=str(task["seq"]), session=sid, spawn=True)
        self.assertEqual(code, 0, out)
        new = self._spawned_sid(task["id"], before)
        trail = " ".join(e.get("text", "")
                         for e in (ts.load_task(task["id"]).get("events") or []))
        self.assertIn("%s-0" % task["seq"], trail)
        self.assertIn("%s-1" % task["seq"], trail)
        self.assertIn(new[:8], trail)

    def test_a_closed_task_is_refused(self):
        task, sid = self._task()
        self._transcript(sid, 130000)
        task["status"] = "closed"
        ts.save_task(task)
        out, code = self._relay(task=str(task["seq"]), session=sid, spawn=True)
        self.assertEqual(code, 2)
        self.assertEqual(self.opened, [])


# ==================================================== 4 · grading the handoff ====

class GradedHandoff(_SuccessionTest):
    """The handoff is graded by the parent like any other child work.

    A relay happens INSIDE one task's life, which is why nothing about it ever reached
    the gate: the parent grades children, and a session succeeding itself creates no
    child. So each handoff becomes a ledger entry carrying the mechanical evidence — who
    handed to whom, at what occupancy, against which window, and whether it was forced
    past a failed cold-read — and it is graded through the SAME `grade` verb, the SAME
    six dimensions and the SAME per-dimension threshold as anything else, linked to the
    handoff it judged so a task with three relays has three separable verdicts.

    No new rubric, and no engine-side judgment: a forced handoff is not auto-failed here.
    The engine's job is to make the evidence impossible to miss; scoring it is the
    grader's, exactly as `lib/loop.py` splits every other part of the gate.
    """

    def _handoff(self, forced=False, ready=True, tokens=130000):
        task, sid = self._task(ready=ready)
        self._transcript(sid, tokens)
        out, code = self._relay(task=str(task["seq"]), session=sid, spawn=True,
                                force=forced)
        self.assertEqual(code, 0, out)
        return ts.load_task(task["id"]), sid, out

    def test_a_relay_records_a_gradeable_handoff(self):
        task, _sid, _out = self._handoff()
        ledger = _succ.handoffs(task)
        self.assertEqual(len(ledger), 1)

    def test_the_evidence_is_mechanical(self):
        """Every field a grader needs is a measurement, not a claim: no prose, nothing
        the relaying session got to characterise about itself."""
        task, sid, _out = self._handoff()
        entry = _succ.handoffs(task)[0]
        self.assertEqual(entry["from"], sid)
        self.assertTrue(entry["to"])
        self.assertNotEqual(entry["to"], sid)
        self.assertEqual(entry["measured"], 130000)
        self.assertEqual(entry["window"], WINDOW)
        self.assertEqual(entry["used_pct"], 65)
        self.assertEqual(entry["verdict"], _succ.RELAY)
        self.assertFalse(entry["forced"])
        self.assertEqual(entry["blockers"], [])

    def test_a_forced_handoff_is_visible_at_the_gate(self):
        """If forcing were silent, G1 could not be graded: the grader would have no way
        to know verification was skipped."""
        task, _sid, _out = self._handoff(forced=True, ready=False)
        entry = _succ.handoffs(task)[0]
        self.assertTrue(entry["forced"])
        self.assertTrue(entry["blockers"])
        lines = " ".join(_succ.handoff_evidence_lines(task, 1))
        self.assertIn("FORCED", lines)
        self.assertIn("goal", lines)

    def test_ungraded_until_it_is_graded(self):
        task, _sid, _out = self._handoff()
        self.assertEqual(_succ.ungraded_handoffs(task), [1])
        out, code = self._grade(task=str(task["seq"]), handoff=1,
                                dim=["G%d=A" % i for i in range(1, 7)])
        self.assertEqual(code, 0, out)
        self.assertEqual(_succ.ungraded_handoffs(ts.load_task(task["id"])), [])

    def test_the_grade_is_linked_to_the_handoff_it_judged(self):
        task, _sid, _out = self._handoff()
        out, code = self._grade(task=str(task["seq"]), handoff=1,
                                dim=["G%d=A" % i for i in range(1, 7)])
        self.assertEqual(code, 0, out)
        ledger = _loop.grades(ts.load_task(task["id"]))
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0]["handoff"], 1)

    def test_two_handoffs_grade_independently(self):
        task, sid, _out = self._handoff()
        second = sorted(set(task.get("sessions") or []) - {sid})[0]
        self._transcript(second, 130000)
        out, code = self._relay(task=str(task["seq"]), session=second, spawn=True)
        self.assertEqual(code, 0, out)
        task = ts.load_task(task["id"])
        self.assertEqual(len(_succ.handoffs(task)), 2)
        self.assertEqual(_succ.ungraded_handoffs(task), [1, 2])
        out, code = self._grade(task=str(task["seq"]), handoff=2,
                                dim=["G%d=A" % i for i in range(1, 7)])
        self.assertEqual(code, 0, out)
        self.assertEqual(_succ.ungraded_handoffs(ts.load_task(task["id"])), [1])

    def test_the_same_rubric_with_no_special_case(self):
        """An ungraded dimension is not an acceptance on a handoff either — the gate is
        the gate, and nothing about a relay softens it."""
        task, _sid, _out = self._handoff()
        out, code = self._grade(task=str(task["seq"]), handoff=1, dim=["G1=A", "G2=A"])
        self.assertEqual(code, 1, out)
        self.assertIn("ungraded", out.lower())
        self.assertEqual(_succ.ungraded_handoffs(ts.load_task(task["id"])), [])

    def test_a_below_threshold_dimension_rejects_the_handoff(self):
        task, _sid, _out = self._handoff()
        dims = ["G%d=A" % i for i in range(1, 6)] + ["G6=C"]
        out, code = self._grade(task=str(task["seq"]), handoff=1, dim=dims)
        self.assertEqual(code, 1, out)
        self.assertIn("G6", out)

    def test_an_index_that_does_not_exist_is_refused(self):
        task, _sid, _out = self._handoff()
        out, code = self._grade(task=str(task["seq"]), handoff=9,
                                dim=["G%d=A" % i for i in range(1, 7)])
        self.assertEqual(code, 2, out)
        self.assertEqual(len(_loop.grades(ts.load_task(task["id"]))), 0)

    def test_a_task_with_no_handoffs_refuses_the_flag(self):
        task, _sid = self._task()
        out, code = self._grade(task=str(task["seq"]), handoff=1,
                                dim=["G%d=A" % i for i in range(1, 7)])
        self.assertEqual(code, 2, out)

    def test_grading_without_the_flag_is_unchanged(self):
        """Back-compat: every existing caller grades work that is not a handoff, and
        their ledger entries must not grow a key that claims otherwise."""
        task, _sid = self._task()
        out, code = self._grade(task=str(task["seq"]),
                                dim=["G%d=A" % i for i in range(1, 7)])
        self.assertEqual(code, 0, out)
        entry = _loop.grades(ts.load_task(task["id"]))[0]
        self.assertNotIn("handoff", entry)

    def test_the_parent_can_see_it_in_the_scan(self):
        """"Graded BY THE PARENT" needs the parent to know a handoff happened. The scan
        row carries the count, so an ungraded handoff is visible where the orchestrator
        already looks."""
        task, _sid, _out = self._handoff()
        plan = _loop.waves([task], lambda i: None)
        row = _loop.node_report(task, plan)
        self.assertEqual(row["handoffs"], 1)
        self.assertEqual(row["handoffs_ungraded"], 1)

    def test_the_scan_row_says_so_in_text_too(self):
        """The orchestrator reads the TEXT scan, not the JSON. A count that only exists
        in `--json` is a count nobody sees. It is APPENDED rather than replacing the
        tail — owing the gate a verdict is orthogonal to being startable, and a tail
        showing one instead of the other would hide whichever it dropped."""
        task, _sid, _out = self._handoff()
        plan = _loop.waves([task], lambda i: None)
        row = _loop_cmds._scan_row(_loop.node_report(task, plan))
        self.assertIn("ungraded handoff", row)
        out, code = self._grade(task=str(task["seq"]), handoff=1,
                                dim=["G%d=A" % i for i in range(1, 7)])
        self.assertEqual(code, 0, out)
        task = ts.load_task(task["id"])
        plan = _loop.waves([task], lambda i: None)
        self.assertNotIn("ungraded handoff",
                         _loop_cmds._scan_row(_loop.node_report(task, plan)))


if __name__ == "__main__":
    unittest.main()
