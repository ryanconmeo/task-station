"""THE CLAIMS CONTRACT — three verdicts, a reason instead of a checkbox, and the clause
that tells a child what a claim IS.

WHAT THIS COVERS AND WHY IT IS SEPARATE FROM test_checker.py. That module is about the
checker's own mechanics: does the store round-trip, does a bad registration get filtered,
does `verify` compare the right substrings. This one is about the CONTRACT the claims
mechanism makes with the two parties either side of it — the GATE that reads a verdict,
and the CHILD that is told to register one. Both of those failed in measured ways, and
each failure is one of the four groups below.

THE FOUR THINGS EVERY TEST HERE IS ULTIMATELY ABOUT:

  1. AN ABSENCE IS NOT A PASS, AND IT IS NOT A FAILURE EITHER. `verify` used to answer
     the same 0 to "every claim passed" and "nobody registered one", so a gate shelling
     out to it read the absence of the check as the check passing — three children in a
     row were graded down for a gap the gate had just called fine. There are three
     verdicts now and they get three exit codes: 0 green, 1 refuted, 3 nothing ran. A
     TYPO'D ID takes the same 3: `verify --id C9` against a task whose claim is C1 ran
     nothing, and one wrong character must not green a gate.
  2. THE WAY OUT OF 3 IS A REASON, NOT A CHECKBOX. `--none` is a pass only because
     somebody wrote down what a later session would have re-run and why it cannot, so the
     reason is mandatory, is a sentence (`CLAIM_NONE_MIN_WORDS`), and is what `verify`
     PRINTS. An escape hatch that takes "n/a" is an escape hatch everybody takes.
  3. A REFUSAL MUST NOT DELETE ANYTHING, AND NO WRITER MAY POP THE KEY OUT FROM UNDER A
     RECORDED `none`. `--none` while claims are registered is a contradiction, and the
     answer is to say so with the claims still there — never to silently pick a winner.
  4. A CONTRACT THE CHILD IS NEVER TOLD ABOUT IS DECORATION, and one carrying a
     placeholder the child has to resolve for itself is one more thing to get wrong.
     `CLAIMS_CONTRACT` rides in the implementer's report contract and its `<n>` becomes
     the child's own task ref at prompt time.

WHY GROUP F EXISTS. Group D asserts that the implementer's report contract says the three
things it has to say, by asserting on substrings. Those assertions would pass against any
string that happens to contain those words today, which is the vacuity a "does the clause
ship" test usually has. So F takes the shipped implementer spec, strips the clause out of
its report, and asserts THE SAME PROBES now fail — the one control that proves D is
measuring the clause and not the English language. The probe list (`CLAIM_MARKERS`) is
shared between the two on purpose; splitting it would let them drift and quietly re-open
the hole.

Harness style, `_Args`/`_task`/`_claims`/`_reload` and the data-dir isolation, is
tests/test_checker.py's `TestClaimsCli` — mirrored rather than imported, so this file
states the shape it depends on instead of inheriting one that can move underneath it. The
`<n>` substitution reaches `board.cmds.loop` directly, the way tests/test_succession.py
does: `CHILD_REF_TOKEN` is seam-private (not in that seam's `__all__`), and widening a
public surface for one assertion is the worse trade.
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

_TMP_HOME = tempfile.mkdtemp(prefix="ts-claims-contract-")
os.environ["TASK_STATION_HOME"] = _TMP_HOME

import checker                                                          # noqa: E402
import loop                                                             # noqa: E402
import store                                                            # noqa: E402
# Seam-private: `CHILD_REF_TOKEN` and `_child_prompt` are not both on the facade, and the
# substitution is what groups E and F are about. Reached through the seam directly.
from board.cmds import loop as _loop_cmds                               # noqa: E402
from board.cmds import maintain as _maintain                            # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


# The substrings that carry the MEANING of the claims clause — one per thing the contract
# has to say, and nothing that is merely present in it. Group D asserts each of these is
# in the implementer's report; group F asserts each is GONE once the clause is stripped
# out, which is what makes group D a measurement rather than a tautology.
#
#   * the definition — a claim is a command plus the output it must print. A child that
#     does not know this cannot tell a claim from a summary;
#   * the default shape's verb (`--register`), which is the floor;
#   * the way out (`--none`), because a contract that only ever says MORE gets padded;
#   * `SKIP`, the sentence that says when NOT to register one.
CLAIM_MARKERS = (
    "A COMMAND PLUS THE OUTPUT SUBSTRING IT MUST PRINT",
    "--register",
    "--none",
    "SKIP",
)

# A reason that clears the three-word floor and reads like something a next session could
# act on — the shape `--none` exists to collect.
REASON = "nothing here can be re-run without a human merge"


class _Args:
    """`claims`' argv. Every flag defaults to OFF so each test states the invocation it
    means — and `none` is defaulted here rather than relied on from `getattr`, because a
    missing attribute and an unpassed flag must not be the same test."""

    def __init__(self, **kw):
        defaults = dict(session=None, source="startup", task=None, action="show",
                        bind=None, unbind=False, register=None, replace=False,
                        remove=None, none=None, id=None, timeout=None)
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
        self.tmp = tempfile.mkdtemp(prefix="claims-contract-test-")
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

    def _reload(self, t):
        return ts.load_task(t["id"])

    def _claims(self, t, **kw):
        """One `claims` invocation against `t`, returning its output. Raises through, so a
        test that does not expect a SystemExit fails loudly rather than swallowing one."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_claims(_Args(task=str(t["seq"]), **kw))
        return buf.getvalue()

    def _claims_exit(self, t, **kw):
        """`(exit_code, output)` for an invocation expected to end the process."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            with self.assertRaises(SystemExit) as caught:
                ts.cmd_claims(_Args(task=str(t["seq"]), **kw))
        return caught.exception.code, buf.getvalue()


# ---------------------------------------------------------------------------
# (A) THE EXIT CODES ARE THREE VERDICTS. A gate reads the code, not the prose.
# ---------------------------------------------------------------------------

class TestVerifyVerdicts(_Base):
    def test_nothing_registered_and_no_reason_exits_three(self):
        """The measured bug. `verify` printed "has no claims registered" and exited 0 — a
        PASS handed out for the absence of the very thing being checked, which is how
        three children in a row were graded down for a gap their gate had just called
        fine. 3 is its own verdict, and the message has to name BOTH ways out, because a
        reader told only "register one" will invent a claim to satisfy the gate, and an
        invented claim is worse than none."""
        code, out = self._claims_exit(self._task(), action="verify")
        self.assertEqual(code, 3)
        self.assertIn("NO CLAIMS REGISTERED", out)
        self.assertIn("--register", out)
        self.assertIn("--none", out)

    def test_a_passing_claim_is_green_and_says_how_many(self):
        """The verdict that must stay boring: a registered claim whose command prints what
        it said it would ends the invocation normally, with the count, so a gate reading
        exit 0 and a human reading the line agree about what happened."""
        t = self._task()
        self._claims(t, register=["C1|echo ready|ready"])
        out = self._claims(t, action="verify")          # no SystemExit: green is a return
        self.assertIn("1/1 passed", out)
        self.assertTrue(checker.last_verify(self._reload(t))["results"][0]["ok"])

    def test_a_refuted_claim_exits_one_and_not_three(self):
        """A REFUTED CLAIM AND AN EMPTY TASK ARE DIFFERENT VERDICTS and a gate must tell
        them apart: "your claim broke" is a red that stops a release, "you registered
        none" is a finding about the record. Collapsing them onto one code would make the
        gate's only lever the wrong one in half the cases."""
        t = self._task()
        self._claims(t, register=["C1|echo nothing|impossible"])
        code, out = self._claims_exit(t, action="verify")
        self.assertEqual(code, 1)
        self.assertNotEqual(code, 3)
        self.assertIn("FAIL", out)

    def test_a_typod_id_exits_three_and_names_what_is_registered(self):
        """`verify --id C9` against a task whose claim is C1 RAN NOTHING, so it proved
        nothing — and passing it would let one wrong character green a gate. Naming what
        IS registered turns the refusal into the fix."""
        t = self._task()
        self._claims(t, register=["C1|echo ready|ready"])
        code, out = self._claims_exit(t, action="verify", id="C9")
        self.assertEqual(code, 3)
        self.assertIn("C9", out)
        self.assertIn("Registered: C1", out)

    def test_a_recorded_none_is_a_pass_that_prints_the_reason(self):
        """`--none` is a pass ONLY because somebody wrote down why, so the reason is what
        the reader gets — a silent 0 here would be indistinguishable from the green run
        that actually re-ran something."""
        t = self._task()
        self._claims(t, none=REASON)
        try:
            out = self._claims(t, action="verify")
        except SystemExit as exc:                      # pragma: no cover - failure path
            self.fail("verify exited %r on a recorded --none; it must be a pass"
                      % (exc.code,))
        self.assertIn("deliberately registers no claims", out)
        self.assertIn(REASON, out)


# ---------------------------------------------------------------------------
# (B) `--none` IS A REASON, NOT A CHECKBOX.
# ---------------------------------------------------------------------------

class TestNoneIsAReason(_Base):
    def test_a_blank_reason_is_refused(self):
        """An escape hatch you can take without saying anything is one everybody takes,
        and the next reader learns nothing. Note what the CLI can and cannot carry: a
        whitespace-only `--none` reaches `declare_none` and is refused out loud, while a
        genuinely EMPTY string is falsy and the flag is simply not applied — so the store
        side is where the empty case has to be pinned."""
        t = self._task()
        for blank in ("", None, "   ", "\t\n"):
            ok, err = checker.declare_none(dict(t), blank)
            self.assertFalse(ok, "declare_none accepted %r as a reason" % (blank,))
            self.assertIn("needs a reason", err)
        self.assertEqual(checker.claims_none(self._reload(t)), {})
        self.assertIn("needs a reason", self._claims(t, none="   "))

    def test_a_one_or_two_word_reason_is_refused_and_the_floor_is_named(self):
        """The reason this floor exists is `n/a` — it passes any is-it-empty check and
        tells a later session nothing. The refusal names the word count because a refusal
        that does not say what would satisfy it is one you retry blind."""
        t = self._task()
        for stub in ("n/a", "not applicable"):
            out = self._claims(t, none=stub)
            self.assertIn("is not a reason", out)
            self.assertIn("%d words or more" % checker.CLAIM_NONE_MIN_WORDS, out)
        self.assertEqual(checker.claims_none(self._reload(t)), {})

    def test_none_is_refused_while_claims_are_registered_and_deletes_nothing(self):
        """A caller typing both is contradicting themselves, and picking a winner
        silently — deleting their command list, or filing a reason the very next read
        contradicts — is worse than saying so. A REFUSAL MUST NOT DELETE ANYTHING, so the
        claims are still there afterwards."""
        t = self._task()
        self._claims(t, register=["C1|echo ready|ready"])
        out = self._claims(t, none=REASON)
        self.assertIn("claim(s) are registered", out)
        stored = self._reload(t)
        self.assertEqual([i["id"] for i in checker.claim_items(stored)], ["C1"])
        self.assertEqual(checker.claims_none(stored), {})

    def test_remove_then_none_in_one_invocation_works(self):
        """`--none` is applied LAST for exactly this: "the last claim is gone and here is
        why there is nothing to re-run" is one thought, and making the caller type it as
        two invocations invites them to type only the first. The reason landing is the
        proof of the order — applied before the removal it would have hit the refusal
        above."""
        t = self._task()
        self._claims(t, register=["C1|echo ready|ready"])
        out = self._claims(t, remove=["C1"], none=REASON)
        self.assertIn("removed C1", out)
        self.assertNotIn("claim(s) are registered", out)
        stored = self._reload(t)
        self.assertEqual(checker.claim_items(stored), [])
        self.assertEqual(checker.claims_none(stored)["reason"], REASON)

    def test_registering_a_claim_retracts_a_recorded_none(self):
        """The two are contradictory statements about the same task, so a stale
        "deliberately none" sitting under a live claim would be a lie the store told on
        every read. The retraction is automatic because a second command to type is one
        people forget."""
        t = self._task()
        self._claims(t, none=REASON)
        self.assertEqual(checker.claims_none(self._reload(t))["reason"], REASON)
        self._claims(t, register=["C1|echo ready|ready"])
        stored = self._reload(t)
        self.assertEqual(checker.claims_none(stored), {})
        self.assertEqual([i["id"] for i in checker.claim_items(stored)], ["C1"])

    def test_a_blank_reason_already_in_the_store_does_not_read_as_a_declaration(self):
        """The reader is the last line of defence. A `none` record whose reason is blank
        did not come from `declare_none` — it is garbage, a hand-edit or a half-written
        write — and reading it as a declaration would turn `verify` green on a task
        nobody ever reasoned about. Same rule the rest of this module keeps: filter
        garbage, never raise on it."""
        for junk in ({"reason": "", "ts": 1.0}, {"reason": "   ", "ts": 1.0},
                     {"ts": 1.0}, {}, "deliberately none", None):
            self.assertEqual(
                checker.claims_none({checker.CLAIMS_FIELD: {"none": junk}}), {},
                "a none record of %r read as a declaration" % (junk,))
        # And the control: a well-formed record DOES read as one, so the loop above is
        # rejecting the garbage rather than rejecting everything.
        good = {checker.CLAIMS_FIELD: {"none": {"reason": REASON, "ts": 1.0}}}
        self.assertEqual(checker.claims_none(good)["reason"], REASON)


# ---------------------------------------------------------------------------
# (C) THE STORE STAYS CLEAN — no writer pops the key out from under a `none`.
# ---------------------------------------------------------------------------

class TestStoreStaysClean(_Base):
    def test_removing_the_last_claim_leaves_a_recorded_none_intact(self):
        """`remove` clears the whole `claims` key when nothing is left, so the task reads
        exactly as it did before any claim existed — and that sweep must not take a
        recorded `none` with it. A declaration silently deleted as a side effect of
        tidying up would put the task straight back to exit 3 with nobody told why.

        The starting state — items AND a `none` together — is not reachable through the
        CLI (each retracts the other), so it is built in the store and round-tripped: it
        IS reachable by a hand-edit or an older write, which is precisely when a guard
        earns its keep."""
        t = self._task(claims={"items": [{"id": "C1", "cmd": "echo ready",
                                          "expect": ["ready"]}],
                               "none": {"reason": REASON, "ts": 1.0}})
        removed, missing = checker.remove(t, ["C1"])
        self.assertEqual((removed, missing), (["C1"], []))
        self.assertIn(checker.CLAIMS_FIELD, t)
        self.assertEqual(checker.claim_items(t), [])
        self.assertEqual(checker.claims_none(t)["reason"], REASON)

    def test_unbinding_the_document_leaves_a_recorded_none_intact(self):
        """`unbind` runs the same "nothing left, drop the key" sweep as `remove`, so it
        needs the same guard — and two writers sharing one invariant is exactly where a
        codebase keeps only one of the two checks."""
        t = self._task(claims={"doc": "/nowhere/PLAN.md", "bound_ts": 1.0,
                               "none": {"reason": REASON, "ts": 1.0}})
        ok, err = checker.unbind(t)
        self.assertTrue(ok, err)
        self.assertIn(checker.CLAIMS_FIELD, t)
        self.assertIsNone(checker.claims_doc(t))
        self.assertEqual(checker.claims_none(t)["reason"], REASON)

    def test_a_none_only_task_round_trips_through_save_and_reload(self):
        """The commonest shape this feature creates: a task whose entire claims block is
        one recorded reason. If that does not survive a save and a reload, `--none` is a
        message printed once rather than a fact on the record, and the next `verify`
        exits 3 having been told."""
        t = self._task()
        self._claims(t, none=REASON)
        stored = self._reload(t)
        block = checker.claims(stored)
        self.assertEqual(sorted(block), ["none"])      # nothing else was invented
        self.assertEqual(checker.claims_none(stored)["reason"], REASON)
        self.assertGreater(checker.claims_none(stored)["ts"], 0)
        self.assertEqual(checker.claim_items(stored), [])
        self.assertIsNone(checker.claims_doc(stored))


# ---------------------------------------------------------------------------
# (D) THE CONTRACT ITSELF — what the implementer is actually told.
# ---------------------------------------------------------------------------

class TestClaimsContractClause(unittest.TestCase):
    def test_the_implementer_contract_says_the_three_things_it_has_to_say(self):
        """#567, #569 and #570 each did A-grade work, each ran real commands, and each was
        held to A- on the same dimension for the same single reason: no claims registered.
        #570 named the defect outright — "I did not want to guess at the shape mid-flight.
        If the report contract wants them, tell me what to register and I will." Three
        identical misses is a CONTRACT defect, so the contract now says what a claim is,
        what shape to register, and when to skip one.

        Asserted through `CLAIM_MARKERS` — substrings that carry the meaning — and not
        through the whole blob, because a test that asserts a string equals itself proves
        nothing and is worse than none. Group F is the control that proves these probes
        would actually fail without the clause."""
        report = loop.ROLE_DEFAULTS["implementer"]["report"]
        self.assertIn(loop.CLAIMS_CONTRACT, report)
        for marker in CLAIM_MARKERS:
            self.assertIn(marker, report, "the contract no longer says %r" % (marker,))

    def test_no_other_shipped_role_carries_the_claims_clause(self):
        """The contract is the IMPLEMENTER'S. A scout is read-only and a reviewer and a
        grader produce findings and grades, not changes — none of them has a command it
        could register, so a claims clause on their prompt is pure padding, and padding is
        what a child learns to skim past on the way to the part that matters."""
        for name, spec in sorted(loop.ROLE_DEFAULTS.items()):
            if name == "implementer":
                continue
            report = spec["report"]
            self.assertNotIn(loop.CLAIMS_CONTRACT, report,
                             "role %r carries the whole claims clause" % name)
            for marker in CLAIM_MARKERS:
                self.assertNotIn(marker, report,
                                 "role %r has picked up %r" % (name, marker))

    def test_the_contract_carries_the_token_the_substitution_replaces(self):
        """The two halves of one feature live in two modules — the clause writes `<n>`,
        `cmds/loop` substitutes it — and nothing else couples them. If the clause is
        reworded without the token, the substitution below still passes against a string
        that no longer needs it, and every child gets `claims --task <n>` with no `<n>` to
        fix. This is the guard against that drift."""
        self.assertIn("<n>", loop.CLAIMS_CONTRACT)
        self.assertIn(_loop_cmds.CHILD_REF_TOKEN, loop.CLAIMS_CONTRACT)
        self.assertEqual(_loop_cmds.CHILD_REF_TOKEN, "<n>")


# ---------------------------------------------------------------------------
# (E) THE `<n>` SUBSTITUTION — a runnable command, or a visible placeholder.
# ---------------------------------------------------------------------------

class TestChildRefSubstitution(unittest.TestCase):
    REPORT = loop.ROLE_DEFAULTS["implementer"]["report"]

    def test_a_known_ref_becomes_a_command_the_child_can_paste(self):
        """A contract that tells a child to run `claims --task <n>` and leaves it to
        resolve `<n>` is one more thing to get wrong on the way to doing what was asked —
        and the failure is silent, because a child that drops the flag gets a different
        task's claims or an error it reads as "not for me"."""
        prompt = _loop_cmds._child_prompt("do the thing", "implementer",
                                          self.REPORT, ref=573)
        self.assertIn("--task 573", prompt)
        self.assertNotIn("--task <n>", prompt)
        self.assertNotIn("<n>", prompt)

    def test_an_unknown_ref_leaves_the_token_standing(self):
        """A VISIBLE PLACEHOLDER BEATS A WRONG NUMBER, and it beats a sentence that
        quietly drops the flag it was telling you to pass. With no ref known there is no
        honest substitution to make, so the token stays and the reader can see that
        something is unresolved."""
        prompt = _loop_cmds._child_prompt("do the thing", "implementer", self.REPORT)
        self.assertIn("<n>", prompt)
        self.assertIn("--task <n>", prompt)

    def test_the_substitution_touches_the_contract_and_never_the_ask(self):
        """The ask is the one thing the orchestrator has to say that the child's own
        record cannot tell it, so it is APPENDED to, never rewritten. An `<n>` inside the
        request is the requester's text — a filename, a placeholder in a snippet they want
        changed — and rewriting it to a task number would corrupt the instruction while
        looking like a helpful fix."""
        ask = "rename the <n> placeholder in the exit-condition parser"
        prompt = _loop_cmds._child_prompt(ask, "implementer", self.REPORT, ref=573)
        self.assertIn(ask, prompt)                     # verbatim, token and all
        self.assertIn("--task 573", prompt)            # the contract still resolved

    def test_a_contract_with_no_token_is_passed_through_unchanged(self):
        """Three of the four shipped roles carry no token, so the substitution has to be a
        no-op on them rather than a step that has to be skipped. A `str.replace` that
        found nothing must leave the contract byte-identical — anything else would mean
        the formatter is editing prose it was only asked to carry."""
        report = "what changed file by file, and the verification you ran with its output"
        prompt = _loop_cmds._child_prompt("do the thing", "scout", report, ref=7)
        self.assertIn(report, prompt)
        self.assertNotIn("<n>", prompt)


# ---------------------------------------------------------------------------
# (F) THE ANTI-VACUITY CONTROL. Group D is only a measurement if this passes.
# ---------------------------------------------------------------------------

class TestContractProbesAreNotVacuous(unittest.TestCase):
    def test_the_group_d_probes_fail_once_the_clause_is_stripped(self):
        """WITHOUT THIS TEST, GROUP D PASSES AGAINST ANY STRING THAT HAPPENS TO CONTAIN
        THOSE WORDS TODAY. So: take the shipped implementer spec, remove `CLAIMS_CONTRACT`
        from its report, and run the SAME probes — every one of them has to fail. That is
        the only thing that establishes group D is measuring the clause rather than the
        English language, and it is why both tests read `CLAIM_MARKERS` instead of each
        keeping a list of its own.

        The length assertion is not padding either: a `replace` that silently matched
        nothing would leave the report intact and hand this test a free pass, which is the
        same vacuity one level up."""
        spec = dict(loop.ROLE_DEFAULTS["implementer"])
        stripped = spec["report"].replace(loop.CLAIMS_CONTRACT, "").strip()
        self.assertLess(len(stripped), len(spec["report"]),
                        "the strip matched nothing, so this control proves nothing")
        self.assertNotIn(loop.CLAIMS_CONTRACT, stripped)
        for marker in CLAIM_MARKERS:
            self.assertNotIn(marker, stripped,
                             "%r survives the strip, so group D would pass without the "
                             "clause" % (marker,))
        # And the shipped report — the one group D actually asserts on — is untouched by
        # having been copied and edited here.
        for marker in CLAIM_MARKERS:
            self.assertIn(marker, loop.ROLE_DEFAULTS["implementer"]["report"])


# ---------------------------------------------------------------------------
# the verdict vocabulary itself, so the three names cannot quietly become two.
# ---------------------------------------------------------------------------

class TestVerdictVocabulary(unittest.TestCase):
    def test_the_three_verdicts_are_three_distinct_values(self):
        """`_claims_verify` returns one of three names and `cmd_claims` maps them to three
        exit codes. Two of them collapsing onto one value is the original bug expressed as
        a typo, and it would make every test in group A pass except the one that noticed."""
        verdicts = (_maintain.VERIFY_PASSED, _maintain.VERIFY_FAILED,
                    _maintain.VERIFY_NOTHING)
        self.assertEqual(len(set(verdicts)), 3)


if __name__ == "__main__":
    unittest.main()
