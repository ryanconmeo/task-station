"""Auto-distill (``brain.distill``) — the Stop-hook continuous-ingestion tier.

PROVENANCE: the ported module comes from the brain source tree's
``scripts/auto-distill.py`` @ 0.14.0, which had **no dedicated test file**. The
source's ``tests/test_context_inject.py`` was checked first, per the chunk brief:
it exercises the *context-injection* hook only (``prompt_scan``, ``_gc_topics``)
and never calls a distill function, so nothing was ported from it — those cases
belong to chunk 5's hook port.

Every case below is therefore ADDED, and they cover the three decisions the
module actually makes:

  * WHAT GETS SKIPPED — the five-guard ladder in ``_decide``, in its precedence
    order. Each guard exists to make the Stop hook impossible to loop or spam,
    and each one is a silent ``exit 0`` in production, so a guard that stopped
    firing would look exactly like a quiet day.
  * WHAT GETS CAPTURED — ``_transcript_tail`` (both message content shapes, bad
    lines skipped) and ``main``'s refusal to write on an empty / ``NONE`` /
    heading-less model reply.
  * WHERE IT LANDS — ``<vault>/raw/<today>-auto-<session>.md``, with the
    provenance header, and the once-per-session stamp written BEFORE the model
    call so a failed distill can never be retried in a loop.

The ``claude -p`` subprocess is stubbed by swapping the module's ``subprocess``
reference (never the stdlib module itself), so no test shells out.

NOT COVERED HERE, deliberately: the hook wiring itself (the Stop-hook payload
contract, hooks.json) — that is chunk 5, with the rest of ``hooks/``.
"""
import contextlib
import io
import json
import os
import sys
import unittest

from tests.brain.base import BrainTestCase

import brain.config as bconfig
import brain.distill as distill
import brain.errorlog as errorlog


class _FakeCompleted:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


class _FakeSubprocess:
    """Stands in for the ``subprocess`` module inside ``brain.distill``.

    Records every call instead of asserting inside it: ``main`` catches
    ``Exception`` broadly, and an AssertionError raised in here would be
    swallowed into the error log and the test would pass on nothing.
    """

    def __init__(self, stdout="", raises=None):
        self.stdout = stdout
        self.raises = raises
        self.calls = []

    def run(self, argv, **kw):
        self.calls.append((argv, kw))
        if self.raises is not None:
            raise self.raises
        return _FakeCompleted(self.stdout)


class DistillTestCase(BrainTestCase):
    def setUp(self):
        super().setUp()
        # The recursion guard is NOT a config key, so base.ENV_KEYS does not
        # clear it (and must not — chunk 1 guards that list against ENV.values()).
        # A developer with it exported would otherwise silently skip every case.
        self._swap_env(distill.DISTILL_ENV, None)
        self.vault = self.make_vault(self.home / "vault")
        os.environ["TASK_STATION_BRAIN_VAULT"] = str(self.vault)

    # --- swapping helpers ---------------------------------------------------
    def _swap(self, obj, name, value):
        old = getattr(obj, name)
        setattr(obj, name, value)
        self.addCleanup(setattr, obj, name, old)
        return old

    def _swap_env(self, name, value):
        old = os.environ.get(name)

        def restore():
            if old is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old
        self.addCleanup(restore)
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value

    # --- fixtures -----------------------------------------------------------
    def transcript(self, lines=40, text="a durable fact about the ledger import"):
        """A Claude transcript file. Written COMPACT on purpose: ``_decide``
        counts lines containing the literal ``"type":"user"``, which only appears
        in the harness's own unspaced serialization.

        The filename carries the line count so a short transcript cannot be
        overwritten by the default one: ``payload(transcript_path=str(
        transcript(lines=4)))`` evaluates the argument FIRST and then builds the
        default payload, which would re-write a shared path with 40 lines.
        """
        p = self.home / f"transcript-{lines}.jsonl"
        rows = []
        for i in range(lines):
            role = "user" if i % 2 == 0 else "assistant"
            rows.append(json.dumps({"type": role, "message": {"content": f"{text} {i}"}},
                                   separators=(",", ":")))
        p.write_text("\n".join(rows) + "\n")
        return p

    def payload(self, **over):
        out = {"session_id": "sess1234abcd", "transcript_path": str(self.transcript())}
        out.update(over)
        return out

    def decide(self, payload=None, state_name="distill-sess1234.done"):
        cfg = bconfig.load()
        state = bconfig.state_dir() / state_name
        return distill._decide(payload if payload is not None else self.payload(),
                               cfg, state)

    def run_main(self, payload=None, stdout="### a-fact\nA durable fact.\n",
                 argv=None, raises=None):
        """Drive ``main()`` end to end with a stubbed model call. Returns
        ``(fake_subprocess, stdout_text)``."""
        fake = _FakeSubprocess(stdout, raises)
        self._swap(distill, "subprocess", fake)
        self._swap(sys, "stdin", io.StringIO(json.dumps(
            payload if payload is not None else self.payload())))
        self._swap(sys, "argv", argv or ["distill"])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with self.assertRaises(SystemExit) as caught:
                distill.main()
        self.assertEqual(caught.exception.code, 0, "a hook must always exit 0")
        return fake, buf.getvalue()

    def raw_files(self):
        return sorted((self.vault / "raw").glob("*.md"))


class DecisionTest(DistillTestCase):
    """The guard ladder — what gets skipped, and in what order."""

    def test_a_long_enough_session_distills(self):
        self.assertEqual(self.decide(), "distill")

    def test_recursion_guard_skips(self):
        self._swap_env(distill.DISTILL_ENV, "1")
        self.assertEqual(self.decide(),
                         f"skip: recursion guard ({distill.DISTILL_ENV}=1)")

    def test_the_recursion_guard_outranks_every_other_check(self):
        # ALL FIVE guards are tripped at once; the recursion guard is the one that
        # answers. That ordering is what stops a distill's own `claude -p` child
        # from ending a session that distills again.
        self._swap_env(distill.DISTILL_ENV, "1")
        os.environ["TASK_STATION_BRAIN_AUTO_DISTILL"] = "false"
        os.environ["TASK_STATION_BRAIN_VAULT"] = str(self.home / "no-such-vault")
        (bconfig.state_dir() / "distill-sess1234.done").touch()
        self.assertEqual(self.decide(self.payload(stop_hook_active=True, transcript_path="")),
                         f"skip: recursion guard ({distill.DISTILL_ENV}=1)")

    def test_stop_hook_active_skips(self):
        self.assertEqual(self.decide(self.payload(stop_hook_active=True)),
                         "skip: stop_hook_active")

    def test_auto_distill_disabled_in_config_skips(self):
        os.environ["TASK_STATION_BRAIN_AUTO_DISTILL"] = "false"
        self.assertEqual(self.decide(), "skip: auto_distill disabled in config")

    def test_missing_vault_skips(self):
        os.environ["TASK_STATION_BRAIN_VAULT"] = str(self.home / "no-such-vault")
        self.assertEqual(self.decide(), "skip: no vault")

    def test_missing_transcript_skips(self):
        self.assertEqual(self.decide(self.payload(transcript_path=str(self.home / "gone.jsonl"))),
                         "skip: no transcript")
        self.assertEqual(self.decide(self.payload(transcript_path="")),
                         "skip: no transcript")

    def test_already_distilled_this_session_skips(self):
        stamp = bconfig.state_dir() / "distill-sess1234.done"
        stamp.touch()
        self.assertEqual(self.decide(), "skip: already distilled this session")

    def test_a_short_transcript_skips_and_names_the_count(self):
        short = self.payload(transcript_path=str(self.transcript(lines=4)))
        self.assertEqual(self.decide(short),
                         f"skip: transcript too short (4 < {distill.MIN_MSGS} messages)")


class TranscriptTailTest(DistillTestCase):
    """What gets captured out of the transcript."""

    def _write(self, rows):
        p = self.home / "tail.jsonl"
        p.write_text("\n".join(rows) + "\n")
        return p

    def test_both_content_shapes_are_read(self):
        p = self._write([
            json.dumps({"message": {"content": "plain string content"}}),
            json.dumps({"message": {"content": [
                {"type": "text", "text": "block content"},
                {"type": "tool_use", "name": "Bash"},          # not text -> dropped
            ]}}),
            json.dumps({"content": "unwrapped content"}),       # no 'message' envelope
        ])
        tail = distill._transcript_tail(p)
        self.assertIn("plain string content", tail)
        self.assertIn("block content", tail)
        self.assertIn("unwrapped content", tail)
        self.assertNotIn("Bash", tail)

    def test_unparseable_lines_are_skipped_not_fatal(self):
        p = self._write(["{not json at all", json.dumps({"message": {"content": "kept"}})])
        self.assertEqual(distill._transcript_tail(p).strip(), "kept")

    def test_the_tail_is_capped(self):
        big = "x" * (distill.TAIL_CHARS + 5000)
        p = self._write([json.dumps({"message": {"content": big}})])
        self.assertEqual(len(distill._transcript_tail(p)), distill.TAIL_CHARS)


class CaptureTest(DistillTestCase):
    """Where it lands, and when nothing lands."""

    def test_a_capture_writes_one_provenance_headed_raw_file(self):
        fake, _ = self.run_main(stdout="### the-fact\nSomething durable happened.\n")
        found = self.raw_files()
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].name.endswith("-auto-sess1234.md"), found[0].name)
        text = found[0].read_text()
        self.assertIn("<!-- auto-distill: session sess1234", text)
        self.assertIn("Untrusted until distilled by /brain-heal.", text)
        self.assertIn("### the-fact", text)
        self.assertIn("Something durable happened.", text)
        self.assertTrue(text.endswith("\n"))
        # the capture is a raw drop, not a curated note
        self.assertEqual(list((self.vault / "notes").glob("*.md")), [])
        self.assertEqual(len(fake.calls), 1)

    def test_blank_run_collapses_to_one_blank_line(self):
        self.run_main(stdout="### a\nfirst\n\n\n\n### b\nsecond\n")
        self.assertNotIn("\n\n\n", self.raw_files()[0].read_text())

    def test_the_model_call_carries_the_recursion_guard_and_the_model(self):
        fake, _ = self.run_main()
        argv, kwargs = fake.calls[0]
        self.assertEqual(argv[0], "claude")
        self.assertEqual(argv[-2:], ["--model", distill.MODEL])
        self.assertIn("Extract 0-3 DURABLE atomic facts", argv[2])
        self.assertEqual(kwargs["env"][distill.DISTILL_ENV], "1")
        self.assertEqual(kwargs["timeout"], 180)

    def test_none_writes_nothing(self):
        self.run_main(stdout="NONE")
        self.assertEqual(self.raw_files(), [])

    def test_a_reply_without_a_heading_writes_nothing(self):
        self.run_main(stdout="I could not find anything durable in this session.")
        self.assertEqual(self.raw_files(), [])

    def test_a_skipped_session_never_calls_the_model(self):
        fake, _ = self.run_main(payload=self.payload(stop_hook_active=True))
        self.assertEqual(fake.calls, [])
        self.assertEqual(self.raw_files(), [])

    def test_the_session_is_stamped_before_the_call_so_a_failure_is_not_retried(self):
        fake, _ = self.run_main(raises=RuntimeError("model unreachable"))
        self.assertEqual(len(fake.calls), 1)
        self.assertTrue((bconfig.state_dir() / "distill-sess1234.done").exists())
        self.assertEqual(self.raw_files(), [])
        # and the swallowed failure left a breadcrumb rather than a traceback
        self.assertIn("auto-distill", errorlog.error_log_path().read_text())

    def test_a_second_run_is_a_no_op(self):
        self.run_main()
        self.assertEqual(len(self.raw_files()), 1)
        fake, _ = self.run_main()
        self.assertEqual(fake.calls, [])            # the stamp short-circuits it
        self.assertEqual(len(self.raw_files()), 1)

    def test_dry_run_prints_the_decision_and_changes_nothing(self):
        fake, out = self.run_main(argv=["distill", "--dry-run"])
        self.assertEqual(out.strip(), "auto-distill decision: distill")
        self.assertEqual(fake.calls, [])
        self.assertEqual(self.raw_files(), [])
        self.assertFalse((bconfig.state_dir() / "distill-sess1234.done").exists())


if __name__ == "__main__":
    unittest.main()
