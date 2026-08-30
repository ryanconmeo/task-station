"""Two defects that share one shape: the board knows something and the reader cannot get at it.

DEFECT 1 — A READ FAILURE THAT LOOKS EXACTLY LIKE AN EMPTY RESULT. `history` was
advertised in ten places and wired to no parser. argparse reported the unknown
subcommand on STDERR and exited 2, so every caller that reads stdout — the hook
wrappers, the MCP server, `$(task-station …)`, an agent reading a tool result —
got ZERO BYTES and a non-zero exit, which is what a command that ran fine and
found nothing looks like. Fixing only the missing subcommand would leave the trap
armed for the next typo, so the parser itself now guarantees: a usage error ALWAYS
writes a non-empty message to STDOUT and ALWAYS exits non-zero.

DEFECT 2 — A DECISION NUMBER NOBODY CAN SEE. `--supersedes N`, `--pin-decision N`,
`heal --split N`, `heal --merge N1,N2` and the contradiction check all name a
decision by its 1-based index in the append-only log. Every read surface printed
unnumbered bullets, so the number was known only to whoever had just written one
(it is echoed at write time) — every reconcile verb was reachable by the author,
for a few minutes, and by nobody else afterwards. The number now round-trips: it
is printed by `search --detail`, by `history`, and by the exported note, and the
number printed is the number the write accepts.

The numbers are the LOG's, not the rendered list's, so they SKIP where a decision
was replaced. That gap is load-bearing — renumbering to close it would silently
repoint every command a reader is already holding.
"""
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

import decisions as _dec  # noqa: E402
import store  # noqa: E402
from board import cliguard  # noqa: E402

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)

# A decision row, on either read surface. The index is rendered QUALIFIED —
# `586:12.` — so that a number from the decision log can never be mistaken for one from
# a numbered list in the task's own State prose, which renders as a bare `  12. ` and
# used to be indistinguishable from it. The bare form stays legal as an ARGUMENT, so this
# accepts the optional `<task>:` prefix and captures the log index either way.
_ROW = re.compile(r"^\s+(?:\d+:)?(\d+)\. ", re.M)


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-hist-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.PROJECTS_ROOT = os.path.join(self.tmp, "projects")
        store.reset_cache()

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self, title="Numbering probe", decisions=()):
        t = ts.new_task(title, "summary")
        t["decisions"] = list(decisions)
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _run(self, argv):
        """`main(argv)` with stdout captured and stderr swallowed → (out, exit_code).

        stderr is captured SEPARATELY and asserted on nowhere, because the whole
        contract under test is what lands on STDOUT — the stream every caller of
        this engine actually reads."""
        out, err = io.StringIO(), io.StringIO()
        code = 0
        try:
            with redirect_stdout(out), redirect_stderr(err):
                ts.main(argv)
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        return out.getvalue(), code


# ---------------------------------------------------------------- the parser ----

class LoudParserContract(_Base):
    """A usage error is non-empty on stdout and non-zero on exit. Every time."""

    def test_unknown_subcommand_is_not_silent(self):
        # THE EXACT REGRESSION: before the fix this produced zero bytes on stdout.
        out, code = self._run(["definitely-not-a-command"])
        self.assertNotEqual(code, 0)
        self.assertTrue(out.strip(), "an unknown subcommand printed NOTHING to stdout")
        self.assertIn("no such command", out)
        self.assertIn("definitely-not-a-command", out)

    def test_unknown_subcommand_points_at_the_command_list(self):
        out, _code = self._run(["definitely-not-a-command"])
        self.assertIn("--help", out)

    def test_a_typo_gets_the_command_it_meant(self):
        out, code = self._run(["histry", "--task", "1"])
        self.assertNotEqual(code, 0)
        self.assertIn("Did you mean", out)
        self.assertIn("history", out)

    def test_unknown_subcommand_skips_the_seventy_name_usage_blob(self):
        # The message lands on stdout now, and on a hook path stdout becomes session
        # context. Seventy command names answer nothing and would cost real tokens.
        out, _code = self._run(["definitely-not-a-command"])
        self.assertNotIn("{create,attach", out)
        self.assertLessEqual(len(out.splitlines()), 4)

    def test_a_bad_flag_on_a_subcommand_is_also_loud(self):
        out, code = self._run(["add-project", "--project", "x"])   # --task missing
        self.assertNotEqual(code, 0)
        self.assertTrue(out.strip())
        self.assertIn("--task", out)

    def test_no_subcommand_at_all_is_loud(self):
        out, code = self._run([])
        self.assertNotEqual(code, 0)
        self.assertTrue(out.strip())

    def test_help_is_not_an_error(self):
        out, code = self._run(["--help"])
        self.assertEqual(code, 0)
        self.assertIn("history", out)     # and the new command is IN the list

    def test_did_you_mean_prefers_a_prefix_over_edit_distance(self):
        near = cliguard.did_you_mean("hist", ["history", "list", "heal"])
        self.assertEqual(near[0], "history")

    def test_did_you_mean_finds_a_substring(self):
        self.assertIn("prompt-tint", cliguard.did_you_mean("tint", ["prompt-tint", "board"]))

    def test_did_you_mean_says_nothing_rather_than_guessing(self):
        self.assertEqual(cliguard.did_you_mean("zzzzzzzz", ["history", "search"]), [])


# --------------------------------------------------------------- the command ----

class HistoryCommand(_Base):
    def test_by_flag_prints_the_trail(self):
        t = self._seed(decisions=["first ruling"])
        out, code = self._run(["history", "--task", str(t["seq"])])
        self.assertEqual(code, 0)
        self.assertIn("History — Task #%s" % t["seq"], out)
        self.assertIn("first ruling", out)

    def test_positional_ref_matches_the_flag(self):
        t = self._seed(decisions=["first ruling"])
        by_flag, _ = self._run(["history", "--task", str(t["seq"])])
        by_pos, _ = self._run(["history", str(t["seq"])])
        self.assertIn("History — Task #%s" % t["seq"], by_pos)   # not two identical errors
        self.assertEqual(by_flag, by_pos)

    def test_an_id_prefix_resolves_too(self):
        t = self._seed(decisions=["first ruling"])
        out, code = self._run(["history", t["id"][:8]])
        self.assertEqual(code, 0)
        self.assertIn("first ruling", out)

    def test_unknown_ref_says_so_on_stdout_and_exits_one(self):
        # "could not look" and "looked, found nothing" must not be the same answer.
        out, code = self._run(["history", "--task", "99999"])
        self.assertEqual(code, 1)
        self.assertIn("No task matching '99999'", out)

    def test_no_ref_and_nothing_attached_exits_two_with_a_usage_line(self):
        out, code = self._run(["history"])
        self.assertEqual(code, 2)
        self.assertIn("usage: task-station history", out)

    def test_falls_back_to_the_sessions_attached_task(self):
        t = self._seed(decisions=["first ruling"])
        ts.set_link("sess-abc", t["id"])
        out, code = self._run(["history", "--session", "sess-abc"])
        self.assertEqual(code, 0)
        self.assertIn("History — Task #%s" % t["seq"], out)

    def test_is_read_only(self):
        # The slash form promises it does not attach, reopen or mutate; so does this.
        t = self._seed(decisions=["first ruling"])
        before = json.dumps(ts.load_task(t["id"]), sort_keys=True)
        self._run(["history", "--task", str(t["seq"]), "--session", "sess-xyz"])
        store.reset_cache()
        self.assertEqual(json.dumps(ts.load_task(t["id"]), sort_keys=True), before)
        self.assertIsNone(ts.get_link("sess-xyz"))

    def test_shows_the_replaced_decisions_the_digest_drops(self):
        t = self._seed(decisions=["wrong w1", "keep k2"])
        self._run(["update", "--task", str(t["seq"]),
                   "--decision", "the correction", "--supersedes", "1"])
        out, code = self._run(["history", "--task", str(t["seq"])])
        self.assertEqual(code, 0)
        self.assertIn("wrong w1", out)
        self.assertIn("SUPERSEDED by decision 3", out)


# --------------------------------------------------- the number, round-tripped ----

class DecisionNumberRoundTrip(_Base):
    def _detail(self, t):
        out, code = self._run(["search", "--detail", str(t["seq"])])
        self.assertEqual(code, 0)
        return out

    def _block(self, text, header="Decisions:"):
        return text[text.index(header):].split("\n\n")[0]

    def test_detail_numbers_every_current_decision(self):
        t = self._seed(decisions=["alpha", "beta", "gamma"])
        block = self._block(self._detail(t))
        self.assertEqual([int(n) for n in _ROW.findall(block)], [1, 2, 3])

    def test_the_number_read_from_detail_is_accepted_by_the_write(self):
        # THE ROUND TRIP, end to end and by parsing, not by knowing the answer: read a
        # decision's number out of the rendered digest, hand that exact number to
        # `--supersedes`, and watch the right decision leave the digest.
        t = self._seed(decisions=["alpha", "beta stale", "gamma"])
        block = self._block(self._detail(t))
        row = [ln for ln in block.splitlines() if "beta stale" in ln][0]
        n = int(_ROW.match(row).group(1))

        out, code = self._run(["update", "--task", str(t["seq"]),
                               "--decision", "beta was wrong", "--supersedes", str(n)])
        self.assertEqual(code, 0)

        after = self._block(self._detail(ts.load_task(t["id"])))
        self.assertNotIn("beta stale", after)
        self.assertIn("beta was wrong", after)

    def test_numbers_skip_a_replaced_decision_rather_than_renumbering(self):
        t = self._seed(decisions=["alpha", "beta stale", "gamma"])
        self._run(["update", "--task", str(t["seq"]),
                   "--decision", "beta was wrong", "--supersedes", "2"])
        block = self._block(self._detail(ts.load_task(t["id"])))
        self.assertEqual([int(n) for n in _ROW.findall(block)], [1, 3, 4])

    def test_detail_and_history_agree_on_the_number(self):
        # Two surfaces, one numbering. A reader who moves between them must not have to
        # re-derive which decision `3` is.
        t = self._seed(decisions=["alpha", "beta stale", "gamma"])
        self._run(["update", "--task", str(t["seq"]),
                   "--decision", "beta was wrong", "--supersedes", "2"])
        t = ts.load_task(t["id"])
        detail = self._block(self._detail(t))
        hist, _ = self._run(["history", "--task", str(t["seq"])])
        hblock = self._block(hist, "Decisions (")
        pairs = dict((int(n), ln) for n, ln in
                     ((_ROW.match(ln).group(1), ln)
                      for ln in hblock.splitlines() if _ROW.match(ln)))
        checked = 0
        for ln in detail.splitlines():
            m = _ROW.match(ln)
            if not m:
                continue
            n = int(m.group(1))
            self.assertIn(_dec.text(t["decisions"][n - 1]), pairs[n])
            checked += 1
        self.assertEqual(checked, 3)   # an unnumbered digest would match nothing and "pass"

    def test_detail_tells_the_reader_what_the_number_is_for(self):
        t = self._seed(decisions=["alpha"])
        self.assertIn("--supersedes <n>", self._detail(t))

    def test_the_exported_note_carries_the_same_number(self):
        t = self._seed(decisions=["alpha", "beta stale", "gamma"])
        self._run(["update", "--task", str(t["seq"]),
                   "--decision", "beta was wrong", "--supersedes", "2"])
        out_dir = os.path.join(self.tmp, "export")
        _out, code = self._run(["export", "--dir", out_dir, "--task", str(t["seq"])])
        self.assertEqual(code, 0)
        note = [os.path.join(dp, f)
                for dp, _dn, fn in os.walk(out_dir) for f in fn
                if f.endswith(".md") and f != "index.md" and "numbering" in f.lower()]
        self.assertTrue(note, "export wrote no per-task note")
        with io.open(note[0], encoding="utf-8") as f:
            text = f.read()
        body = text[text.index("## Decisions"):].split("\n## ")[0]
        self.assertIn("- [1] alpha", body)
        self.assertIn("- [3] gamma", body)
        self.assertIn("- [4] beta was wrong", body)
        self.assertNotIn("beta stale", body)     # replaced → off every present-tense view


if __name__ == "__main__":
    unittest.main()
