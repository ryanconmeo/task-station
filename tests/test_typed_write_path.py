"""TYPED DECISIONS, INCREMENT 2 — the ASK-SITE and the three MACHINE WRITERS.

Increment 1 built the element: `kind`, `subject`, the closed vocabularies and the guarded
setters, with NO caller. This is the write path that reaches them, and it is exactly two
halves.

THE ASK-SITE. `--kind` and `--subject` on `update` attach to the LAST `--decision` of the
call, which is `--pin`'s rule. THIS IS THE THIRD FLAG TO CARRY THAT RULE AND THE FIRST TWO
HAVE BOTH BEEN GOT WRONG IN PRACTICE — a call passing several `--decision` flags and one
`--supersedes` credits the wrong entry — so the binding is under test here rather than
merely documented, and it is tested by writing TWO decisions and asserting which one got
typed. `--kind-decision` / `--subject-decision` / `--clear-kind` / `--clear-subject` are
the same primitive's other two shapes, copied from `--pin-decision` / `--unpin-decision`:
one that targets an existing entry, one that undoes it. Correction has to stay a single
command, because the design forbids any check from contradicting a declared value, which
makes the AUTHOR the only correction mechanism that exists.

THE ADVISORY IS ADVISORY. It inherits `length_warning`'s law to the letter: it may nag, it
may NEVER refuse a write. An untyped decision is a permanent citizen of the record —
hundreds already exist, backfill cannot be automated at ~1/3 inference precision — so
"declares nothing" must stay writable forever, not for a migration window. A refusal on a
MISSING declaration would be strictly worse than the pin cap's was: it teaches the author
to type SOMETHING, and a guessed kind is the inference this field exists to remove.

THE MACHINE WRITERS DECLARE, AND IT IS INFERENCE-FREE BY CONSTRUCTION, because a writer
knows what it is writing without reading a word of it:
  * `grade`'s gate decision is a MEASUREMENT — scores against a threshold at one moment.
  * heal's merge summary is a RELEASE-RECORD (a consolidation record). This is #596's
    sharpest exhibit dying: `_merge_summary`'s own docstring says it is "WORDED TO AVOID
    SUPERSESSION_LANGUAGE", because a generated decision that tripped the scan that
    generated it made a freshly-healed task read as dirty. The tool wrote prose to dodge
    its own parser; a declared kind is the structural answer that wording stood in for.
  * a memo promoted on ack is a PROCESS-NOTE — how the work is RUN, not what it decided.

NO READER IS TOUCHED. Increment 3 (the two-tier digest render, heal's declared-subject
union, the skip tiers) is GATED on ~50 declared decisions spot-checked for precision, so
nothing here asserts anything about how a declaration RENDERS.

The subprocess isolation copies tests/test_decision_ownership.py's `TheVerbEndToEnd`.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(_REPO_ROOT, "lib")
ENGINE = os.path.join(LIB, "task-station.py")
sys.path.insert(0, LIB)

import decisions as dec      # noqa: E402

# Over the 600-char advisory, so the declaration nag has its interrupt to ride.
LONG = ("The station gossips its board over the channel and the child owns the wire "
        "format, which is why a schema change lands on one side at a time. ") * 6
SHORT = "chose sqlite over flat files"


class _Engine(unittest.TestCase):
    """One throwaway TASK_STATION_HOME per test, driven through the real CLI."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-typed-")
        self._prev = os.environ.get("TASK_STATION_HOME")
        os.environ["TASK_STATION_HOME"] = self.tmp
        self.cli("create", "--title", "PROBE", "--session", "s1")

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("TASK_STATION_HOME", None)
        else:
            os.environ["TASK_STATION_HOME"] = self._prev
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cli(self, *args):
        """(stdout, returncode) — the code matters as much as the text: an advisory that
        quietly became a gate would still print its line."""
        p = subprocess.run([sys.executable, ENGINE] + list(args),
                           capture_output=True, text=True,
                           env=dict(os.environ, TASK_STATION_HOME=self.tmp))
        return p.stdout + p.stderr, p.returncode

    def out(self, *args):
        return self.cli(*args)[0]

    def update(self, *args):
        return self.cli("update", "--task", "1", "--session", "s1", *args)

    def task(self, seq="1"):
        """The stored task, WHOLE, straight out of the store."""
        code = (
            "import json,os,sys;sys.path.insert(0,%r);import store;"
            "b=store.get_backend(os.path.join(os.environ['TASK_STATION_HOME'],'store'));"
            "print(json.dumps([t for t in b.all_tasks() if str(t.get('seq'))==%r][0],"
            "sort_keys=True))" % (LIB, str(seq)))
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           env=dict(os.environ, TASK_STATION_HOME=self.tmp))
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def blob(self, seq="1"):
        """The WHOLE stored task as canonical JSON. The only comparison that separates
        "nothing happened" from "something small happened" — a field-by-field check
        passes on a write it did not think to look at."""
        return json.dumps(self.task(seq), sort_keys=True)

    def decisions(self, seq="1"):
        return self.task(seq).get("decisions") or []


# ------------------------------------------------------------------ the attach rule ---

class TheAttachRuleIsPinsAndItIsTested(_Engine):
    """`--supersedes` is the cautionary tale: the rule that it binds the LAST `--decision`
    is on the record precisely because passing more than one per call credits the wrong
    entry. So these flags are tested by writing TWO decisions and asking which got typed."""

    def test_kind_types_the_last_decision_of_the_call_and_not_the_first(self):
        self.update("--decision", "FIRST", "--decision", "SECOND", "--kind", "ruling")
        entries = self.decisions()
        self.assertEqual(dec.text(entries[0]), "FIRST")
        self.assertIsNone(dec.kind(entries[0]))          # untouched, as --pin leaves it
        self.assertEqual(dec.text(entries[1]), "SECOND")
        self.assertEqual(dec.kind(entries[1]), "ruling")

    def test_subject_lands_on_the_last_decision_of_the_call_and_not_the_first(self):
        self.update("--decision", "FIRST", "--decision", "SECOND",
                    "--subject", "task:596")
        entries = self.decisions()
        self.assertEqual(dec.subject(entries[0]), [])
        self.assertEqual(dec.subject(entries[1]), ["task:596"])

    def test_every_subject_in_one_update_is_ONE_subject_on_ONE_decision(self):
        # `set_subject` REPLACES, so applying the refs one at a time would silently keep
        # only the last — the exact half-write the wholesale refusal exists to prevent.
        self.update("--decision", "D", "--subject", "task:596",
                    "--subject", "pr:task-station#43", "--subject", "release:3.53.0")
        self.assertEqual(dec.subject(self.decisions()[0]),
                         ["task:596", "pr:task-station#43", "release:3.53.0"])

    def test_the_help_text_states_the_binding_on_every_flag_that_carries_it(self):
        # The rule has been got wrong twice in practice, so it is written where the
        # caller reads it and not only where the implementer does.
        help_text = self.out("update", "--help")
        for flag in ("--kind", "--subject"):
            self.assertIn(flag, help_text)
        self.assertIn("LAST --decision", help_text)

    def test_kind_without_a_decision_is_a_loud_message_that_names_the_other_flag(self):
        out, _ = self.update("--kind", "ruling")
        self.assertIn("--kind needs a --decision in the same update", out)
        self.assertIn("--kind-decision", out)

    def test_subject_without_a_decision_is_a_loud_message_not_a_silent_noop(self):
        out, _ = self.update("--subject", "task:596")
        self.assertIn("--subject needs a --decision in the same update", out)
        self.assertIn("--subject-decision", out)

    def test_an_unattached_declaration_writes_NOTHING_and_the_whole_blob_proves_it(self):
        self.update("--decision", SHORT)
        before = self.blob()
        out, code = self.update("--kind", "ruling", "--subject", "task:596")
        self.assertIn("needs a --decision", out)
        self.assertEqual(code, 0)
        # Whole-blob, not a field check: the claim is that NOTHING was written, and only
        # comparing everything can tell that apart from something small happening.
        self.assertEqual(self.blob(), before)


# ------------------------------------------------------------------- advisory, never ---

class TheAdvisoryNeverRefuses(_Engine):
    """`length_warning`'s law, inherited: nag, never gate. A decision that declares
    nothing must stay writable forever — that is the migration contract, and untyped is a
    permanent citizen of the record rather than a transitional one."""

    def test_a_long_undeclared_decision_is_nagged_and_stored_in_full(self):
        out, code = self.update("--decision", LONG)
        self.assertIn("declares no kind", out)
        self.assertEqual(code, 0)
        entries = self.decisions()
        self.assertEqual(len(entries), 1)
        self.assertEqual(dec.text(entries[0]), LONG.strip())

    def test_the_nag_names_the_whole_vocabulary_and_says_it_is_advisory(self):
        out, _ = self.update("--decision", LONG)
        for word in dec.KINDS:
            self.assertIn(word, out)
        self.assertIn("ADVISORY ONLY", out)
        self.assertIn("--subject", out)

    def test_an_untyped_decision_is_stored_as_the_SAME_PLAIN_STRING_it_always_was(self):
        self.update("--decision", SHORT)
        stored = self.decisions()[0]
        self.assertEqual(stored, SHORT)
        self.assertIsInstance(stored, str)      # not a dict — byte-identical to legacy

    def test_a_declared_decision_is_not_nagged(self):
        out, _ = self.update("--decision", LONG, "--kind", "ruling")
        self.assertNotIn("declares no kind", out)
        # ...and the LENGTH advisory is untouched by any of this.
        self.assertIn("over the 600-char advisory", out)

    def test_a_short_undeclared_decision_rides_no_interrupt_and_is_not_nagged(self):
        # The nag rides the EXISTING 600-char interrupt, which is what #596's goal asks
        # for in its own words. A one-line decision costs a reader one line.
        out, _ = self.update("--decision", SHORT)
        self.assertNotIn("declares no kind", out)

    def test_the_advisory_fires_per_decision_and_names_the_right_index(self):
        self.update("--decision", SHORT)
        out, _ = self.update("--decision", LONG)
        self.assertIn("decision 2 declares no kind", out)

    def test_a_refused_subject_still_keeps_the_decision_and_stores_no_half_subject(self):
        # Wholesale refusal: a partially accepted subject is the worst outcome available,
        # because it is structural, trusted and silently incomplete. But losing the
        # DECISION over a bad ref would be worse still, so the write survives.
        out, code = self.update("--decision", "D", "--subject", "task:596",
                                "--subject", "27")
        self.assertEqual(code, 0)
        self.assertIn("is not a qualified ref", out)
        entries = self.decisions()
        self.assertEqual(len(entries), 1)
        self.assertEqual(dec.text(entries[0]), "D")
        self.assertEqual(dec.subject(entries[0]), [])    # nothing, not "task:596"

    def test_the_refusal_says_the_collision_is_LATENT_and_not_live(self):
        # #601 measured ZERO work-item numbers carrying more than one noun on the largest
        # task on this board, and the parent re-measured the same zero. The field exists
        # for the CAPACITY to collide; claiming a live collision overstates the evidence.
        out, _ = self.update("--decision", "D", "--subject", "27")
        self.assertIn("emit one signal and CAN collide", out)
        self.assertNotIn("collide today", out)


# --------------------------------------------------------- declaring an existing one ---

class DeclaringAnEntryThatAlreadyExists(_Engine):
    """The hand-classification path — ONE ENTRY AT A TIME with a human-named value. There
    is no batch form and there will not be one: subject inference measured ~1/3 precision
    on this record, and `kind` is refused a batch for its own reason (nobody has measured
    whether it is cheaper to classify, and an unmeasured batch is the bet that got the
    placement tier demoted)."""

    def setUp(self):
        super().setUp()
        self.update("--decision", "ONE")
        self.update("--decision", "TWO")

    def test_kind_decision_types_the_entry_it_names(self):
        self.update("--kind-decision", "1=ruling")
        entries = self.decisions()
        self.assertEqual(dec.kind(entries[0]), "ruling")
        self.assertIsNone(dec.kind(entries[1]))

    def test_subject_decision_takes_a_comma_separated_set_as_one_subject(self):
        self.update("--subject-decision", "2=task:600,pr:task-station#44")
        self.assertEqual(dec.subject(self.decisions()[1]),
                         ["task:600", "pr:task-station#44"])

    def test_re_declaring_a_kind_overwrites_because_the_author_is_the_only_corrector(self):
        self.update("--kind-decision", "1=ruling")
        self.update("--kind-decision", "1=incident")
        self.assertEqual(dec.kind(self.decisions()[0]), "incident")

    def test_clear_kind_is_the_one_command_inverse(self):
        self.update("--kind-decision", "1=ruling")
        self.update("--clear-kind", "1")
        self.assertIsNone(dec.kind(self.decisions()[0]))

    def test_clear_subject_is_the_one_command_inverse(self):
        self.update("--subject-decision", "1=task:600")
        self.update("--clear-subject", "1")
        self.assertEqual(dec.subject(self.decisions()[0]), [])

    def test_clearing_what_declares_nothing_errors_rather_than_reading_as_success(self):
        out, _ = self.update("--clear-kind", "1")
        self.assertIn("declares no kind", out)
        out, _ = self.update("--clear-subject", "1")
        self.assertIn("declares no subject", out)

    def test_a_malformed_pair_is_a_loud_message_naming_the_shape_wanted(self):
        before = self.blob()
        out, code = self.update("--kind-decision", "ruling")
        self.assertIn("expected `<n>=<kind>`", out)
        self.assertEqual(code, 0)
        self.assertEqual(self.blob(), before)

    def test_an_invented_kind_is_refused_with_the_closed_vocabulary_named(self):
        out, code = self.update("--kind-decision", "1=architecture")
        self.assertIn("is not a kind", out)
        self.assertIn("CLOSED", out)
        self.assertEqual(code, 0)
        self.assertIsNone(dec.kind(self.decisions()[0]))

    def test_a_bad_index_is_refused_and_writes_nothing(self):
        before = self.blob()
        out, _ = self.update("--kind-decision", "99=ruling")
        self.assertIn("99", out)
        self.assertEqual(self.blob(), before)


# ------------------------------------------------------------- the two `--kind` flags ---

class TheOtherKindFlagIsUntouched(_Engine):
    """`add-event --kind` is a DIFFERENT flag on a different subcommand with a different
    vocabulary (log|decision|milestone|…). The two share a spelling and nothing else, and
    neither may read the other's values."""

    def test_add_event_still_takes_its_own_vocabulary(self):
        out, code = self.cli("add-event", "--task", "1", "--kind", "milestone",
                             "--text", "shipped")
        self.assertEqual(code, 0)
        kinds = [e.get("kind") for e in (self.task().get("events") or [])]
        self.assertIn("milestone", kinds)

    def test_update_refuses_add_events_vocabulary(self):
        # The decision vocabulary is CLOSED, so `milestone` is not a kind of decision —
        # which is the guard against the two sets quietly becoming one.
        out, code = self.update("--decision", "D", "--kind", "milestone")
        self.assertNotEqual(code, 0)     # argparse `choices` — refused at the boundary
        self.assertIn("milestone", out)

    def test_add_event_does_not_accept_a_decision_kind_as_a_decision(self):
        # And in the other direction: `add-event --kind ruling` writes an EVENT. It must
        # never reach the decisions log or the declaration field.
        self.cli("add-event", "--task", "1", "--kind", "ruling", "--text", "x")
        self.assertEqual(self.decisions(), [])


# ---------------------------------------------------------------- the machine writers ---

class TheMachineWritersDeclareTheirOwnKind(_Engine):
    """Inference-free by construction — each writer knows what it is writing without
    reading a word of the text, which is what makes a machine allowed to declare at all."""

    def test_a_gate_grade_is_a_MEASUREMENT(self):
        self.cli("create", "--title", "CHILD", "--session", "sc")
        self.cli("update", "--task", "2", "--session", "sc", "--parent", "1")
        self.cli("grade", "--task", "2", "--session", "s1",
                 "--dim", "G1=A", "--dim", "G2=A", "--dim", "G3=A",
                 "--dim", "G4=A", "--dim", "G5=A", "--dim", "G6=A")
        entries = self.decisions("2")
        self.assertEqual(len(entries), 1)
        self.assertTrue(dec.text(entries[0]).startswith("Gate "))
        self.assertEqual(dec.kind(entries[0]), dec.KIND_MEASUREMENT)

    def test_a_memo_promoted_on_ack_is_a_PROCESS_NOTE(self):
        self.cli("memo", "send", "--task", "1", "--text", "a memo worth promoting",
                 "--session", "s1")
        mid = (self.task().get("memos") or [])[0]["id"]
        self.cli("memo", "ack", "--task", "1", "--id", mid, "--session", "s2",
                 "--decision")
        entries = self.decisions()
        self.assertEqual(len(entries), 1)
        self.assertEqual(dec.kind(entries[0]), dec.KIND_PROCESS_NOTE)

    def test_heals_merge_summary_is_a_RELEASE_RECORD(self):
        # THE SHARPEST EXHIBIT ON #596's LIST, and the one that dies outright:
        # `_merge_summary` is worded to dodge heal's own SUPERSESSION_LANGUAGE scanner,
        # in its own docstring's words. A declared kind is the structural answer.
        for i in range(1, 6):
            self.update("--decision",
                        "Sync transport: the station gossips over the channel, note %d" % i)
        self.cli("heal", "--task", "1", "--session", "s1", "--apply")
        entries = self.decisions()
        summary = entries[-1]
        self.assertIn("one reconciled record of 5 decisions", dec.text(summary))
        self.assertEqual(dec.kind(summary), dec.KIND_RELEASE_RECORD)

    def test_the_absorbed_originals_are_NOT_typed_by_the_merge(self):
        # Only the record the machine WROTE may be declared. Typing what it merely READ
        # would be inference, and a replaced entry cannot be classified at all.
        for i in range(1, 6):
            self.update("--decision",
                        "Sync transport: the station gossips over the channel, note %d" % i)
        self.cli("heal", "--task", "1", "--session", "s1", "--apply")
        for entry in self.decisions()[:5]:
            self.assertIsNone(dec.kind(entry))

    def test_a_grade_with_no_decision_writes_no_decision_and_therefore_no_kind(self):
        self.cli("create", "--title", "CHILD", "--session", "sc")
        self.cli("update", "--task", "2", "--session", "sc", "--parent", "1")
        self.cli("grade", "--task", "2", "--session", "s1", "--no-decision",
                 "--dim", "G1=A", "--dim", "G2=A", "--dim", "G3=A",
                 "--dim", "G4=A", "--dim", "G5=A", "--dim", "G6=A")
        self.assertEqual(self.decisions("2"), [])


class AMachineWriterNeverLosesItsDecisionOverAClassification(unittest.TestCase):
    """`append_decision`'s advisory law, at the one place it could quietly bite. A caller
    passing a kind this version does not know — a rename, a newer writer — must not lose
    its decision: the record is the serious thing and the type is the trivial one."""

    def test_an_unknown_kind_still_appends_the_decision_untyped(self):
        code = (
            "import sys,os;sys.path.insert(0,%r);"
            "os.environ.setdefault('TASK_STATION_HOME','%s');"
            "from board.model import append_decision;import decisions as d;"
            "t={};append_decision(t,'the ruling','s',kind='not-a-kind');"
            "print(repr(t['decisions'][0]));print(d.kind(t['decisions'][0]))"
            % (LIB, tempfile.mkdtemp(prefix="ts-typed-unit-")))
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("'the ruling'", p.stdout)     # stored, and stored as a plain string
        self.assertIn("None", p.stdout)             # declaring nothing


if __name__ == "__main__":
    unittest.main()
