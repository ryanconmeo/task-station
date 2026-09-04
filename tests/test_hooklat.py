"""3.64.0: the per-command hook-latency reader, and the mux line it depends on.

/doctor reports hook latency per hook EVENT. Three plugins register SessionStart on
this machine, so a 78s SessionStart convicted nobody and the named cause sat on the
record as an INFERENCE for a month. The transcript underneath that report is finer:
each hook run is stored with the literal `command` and its `durationMs`.
tools/hooklat.py reads that, and lib/hookmux.py prints one stderr line per run so the
mux — one command, several programs — can be broken down too.

The failure mode worth testing is DRIFT: the two files are a producer and a parser in
different languages of the same one-line format, so a change to either that the other
does not follow turns every child row into silence, which reads exactly like "the
children cost nothing". So the parser is tested against a line the MUX ACTUALLY
PRINTS, not against a copy of it.

Stdlib-only unittest, no LLM, never reads the user's real transcripts.
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hooklat = _load("hooklat", os.path.join(_REPO_ROOT, "tools", "hooklat.py"))
hookmux = _load("hookmux", os.path.join(_REPO_ROOT, "lib", "hookmux.py"))


class _Args:
    root = None
    project = None
    event = None
    command = None
    children = False
    split_at = None
    before_label = None
    after_label = None
    since = None
    assert_p50_under = None


class TimingLineTest(unittest.TestCase):
    """The producer and the parser, pinned to each other."""

    def test_the_parser_reads_a_line_the_mux_really_prints(self):
        line = hookmux._timing_line("session-start",
                                    [("hooks/on_session_start.sh", 180.4),
                                     ("brain.hooks.inject", 34.0)])
        rows = list(hooklat.child_rows({"stderr": line}))
        self.assertEqual([(event, child, ms) for _v, event, child, ms in rows],
                         [("session-start", "hooks/on_session_start.sh", 180.0),
                          ("session-start", "brain.hooks.inject", 34.0)])

    def test_the_line_carries_a_version(self):
        line = hookmux._timing_line("stop", [("hooks/on_stop.sh", 12.0)])
        version = list(hooklat.child_rows({"stderr": line}))[0][0]
        self.assertTrue(version, "a timing line with no version is unusable")

    def test_the_total_is_the_sum_and_is_not_read_as_a_child(self):
        line = hookmux._timing_line("stop", [("a", 10.0), ("b", 5.0)])
        self.assertIn("total=15ms", line)
        children = [c for _v, _e, c, _ms in hooklat.child_rows({"stderr": line})]
        self.assertEqual(children, ["a", "b"])

    def test_other_stderr_around_the_line_is_ignored(self):
        line = ("hookmux: brain.hooks.gate: exit 1\n"
                + hookmux._timing_line("stop", [("a", 1.0)])
                + "some child's own diagnostics\n")
        self.assertEqual([c for _v, _e, c, _m in hooklat.child_rows({"stderr": line})],
                         ["a"])

    def test_a_run_with_no_timing_line_yields_nothing_rather_than_zero(self):
        self.assertEqual(list(hooklat.child_rows({"stderr": "boom\n"})), [])


class CommandNameTest(unittest.TestCase):
    def test_a_plugin_command_reduces_to_its_program_and_verb(self):
        self.assertEqual(
            hooklat._short('python3 "${CLAUDE_PLUGIN_ROOT}/lib/hookmux.py" stop'),
            "hookmux stop")

    def test_two_installs_of_one_command_land_in_the_same_bucket(self):
        a = hooklat._short("python3 /cache/plugin/3.63.0/lib/hookmux.py session-start")
        b = hooklat._short("python3 /cache/plugin/3.64.0/lib/hookmux.py session-start")
        self.assertEqual(a, b)

    def test_a_bare_command_survives_unchanged(self):
        self.assertEqual(hooklat._short("cwd-guard"), "cwd-guard")


class ReadTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ts-hooklat-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.proj = os.path.join(self.root, "proj")
        os.makedirs(self.proj)

    def _write(self, rows, name="s.jsonl"):
        with open(os.path.join(self.proj, name), "w", encoding="utf-8") as fh:
            for ts, command, ms, stderr in rows:
                fh.write(json.dumps({
                    "timestamp": ts,
                    "attachment": {"type": "hook_success", "hookEvent": "Stop",
                                   "command": command, "durationMs": ms,
                                   "stderr": stderr}}) + "\n")
            fh.write("{ this line is truncated\n")   # a live transcript's last line

    def _report(self, **kw):
        args = _Args()
        args.root = self.root
        for k, v in kw.items():
            setattr(args, k, v)
        return hooklat.report(args, out=io.StringIO())

    def test_a_truncated_last_line_is_skipped_not_fatal(self):
        self._write([("2026-09-01T00:00:00Z", "cwd-guard", 10, "")])
        self.assertEqual(self._report()["all"]["commands"], {"cwd-guard": [10.0]})

    def test_split_at_reports_both_sides_from_one_pass(self):
        self._write([("2026-09-01T00:00:00Z", "cwd-guard", 100, ""),
                     ("2026-09-05T00:00:00Z", "cwd-guard", 10, "")])
        got = self._report(split_at="2026-09-03T00:00:00Z",
                           before_label="3.63.0", after_label="3.64.0")
        self.assertEqual(got["3.63.0"]["commands"], {"cwd-guard": [100.0]})
        self.assertEqual(got["3.64.0"]["commands"], {"cwd-guard": [10.0]})

    def test_children_are_only_broken_out_when_asked(self):
        line = hookmux._timing_line("stop", [("hooks/on_stop.sh", 9.0)])
        self._write([("2026-09-05T00:00:00Z", "hookmux stop", 12, line)])
        self.assertEqual(self._report()["all"]["children"], {})
        got = self._report(children=True)["all"]["children"]
        self.assertEqual(list(got.values()), [[9.0]])

    def test_a_record_with_no_duration_is_not_a_zero(self):
        with open(os.path.join(self.proj, "s.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"timestamp": "2026-09-05T00:00:00Z", "attachment": {
                "type": "hook_success", "hookEvent": "Stop", "command": "x"}}) + "\n")
        self.assertEqual(self._report()["all"]["commands"], {})

    def test_since_drops_older_runs(self):
        self._write([("2026-09-01T00:00:00Z", "cwd-guard", 100, ""),
                     ("2026-09-05T00:00:00Z", "cwd-guard", 10, "")])
        got = self._report(since="2026-09-03T00:00:00Z")
        self.assertEqual(got["all"]["commands"], {"cwd-guard": [10.0]})

    def test_the_ceiling_passes_under_and_fails_over(self):
        self._write([("2026-09-05T00:00:00Z", "cwd-guard", 10, "")])
        self.assertTrue(self._report(assert_p50_under=3000)["all"]["passed"])
        self.assertFalse(self._report(assert_p50_under=5)["all"]["passed"])

    def test_an_empty_population_is_not_a_pass(self):
        """Nothing measured is nothing proved. A gate that goes green because it found
        no runs is the exact shape of a false green."""
        self._write([("2026-09-05T00:00:00Z", "cwd-guard", 10, "")])
        out = io.StringIO()
        args = _Args()
        args.root, args.command, args.assert_p50_under = self.root, "no-such", 3000
        got = hooklat.report(args, out=out)
        self.assertFalse(got["all"]["passed"])
        self.assertIn("NO RUNS", out.getvalue())

    def test_the_verdict_line_names_the_worst_command(self):
        self._write([("2026-09-05T00:00:00Z", "fast", 5, ""),
                     ("2026-09-05T00:00:01Z", "slow", 900, "")])
        out = io.StringIO()
        args = _Args()
        args.root, args.assert_p50_under = self.root, 100
        hooklat.report(args, out=out)
        self.assertIn("OVER 100ms", out.getvalue())
        self.assertIn("slow", out.getvalue())

    def test_percentiles_are_nearest_rank(self):
        self.assertEqual(hooklat._pct([1, 2, 3, 4, 5], 0.5), 3)
        self.assertEqual(hooklat._pct([1, 2, 3, 4, 5], 0.9), 5)
        self.assertIsNone(hooklat._pct([], 0.5))


if __name__ == "__main__":
    unittest.main()
