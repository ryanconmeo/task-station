"""2.21.0 perf: the transcript-derived caches, and the Stop-hook consolidation.

The board re-parsed every session transcript on disk, many times over, on every
turn: 4072 `_session_msgcount` calls and 571 `_prompt_replies` calls over 458
files, 2.37M json.loads, a Stop hook that blocked turn end for ~22s. Both
functions are pure functions of a transcript's bytes, so they are now cached on
(st_mtime_ns, st_size) in memory, and the counts also persist under
<data_dir>/cache/msgcounts.json so a later turn doesn't redo the work.

What these tests pin down:
  • a hit is a hit, and a CHANGED file is a miss (the cache can never go stale)
  • the persistent cache actually spares the next process the parse
  • a corrupt/foreign/unreadable cache file degrades to recomputation, silently —
    it runs inside a Stop hook, so raising would block the user's turn
  • `_prompt_replies` returns exactly what the uncached per-call parse returned,
    for any subset of uuids, from ONE parse
  • the on_stop.sh consolidation left `stop-gate` alone: same process, same
    position, same stdout — the harness reads it for the block contract
  • one failing step inside stop_steps.py doesn't stop the others, and is logged

Stdlib-only unittest, no LLM, never touches the real store.
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
HOOKS = os.path.join(_REPO_ROOT, "hooks")
sys.path.insert(0, LIB)

_spec = importlib.util.spec_from_file_location(
    "task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)

import hook_health  # noqa: E402
import stop_steps  # noqa: E402


class _CacheMixin:
    """A throwaway data home + projects root, and EMPTY caches per test.

    The in-process caches are module globals keyed by absolute path, so a fresh
    tmpdir already isolates one test from the next; clearing them as well keeps a
    hit/miss count meaningful inside a single test."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-cache-")
        self._saved_env = {k: os.environ.get(k) for k in
                           ("TASK_STATION_HOME", "CLAUDE_CONFIG_DIR", "XDG_STATE_HOME")}
        os.environ["TASK_STATION_HOME"] = self.tmp
        self._saved = (ts.DATA, ts.PROJECTS_ROOT)
        ts.DATA = self.tmp
        self.proj = os.path.join(self.tmp, "projects")
        ts.PROJECTS_ROOT = self.proj
        os.makedirs(os.path.join(self.proj, "-bucket"), exist_ok=True)
        self._reset_caches()

    def tearDown(self):
        ts.DATA, ts.PROJECTS_ROOT = self._saved
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._reset_caches()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _reset_caches(self):
        ts._MSGCOUNT_MEM.clear()
        ts._REPLIES_MEM.clear()
        ts._SESSION_PATH_MEM.clear()
        ts._MSGCOUNT_DISK = None

    # -- fixtures -------------------------------------------------------------
    def _transcript(self, sid, lines):
        """Write <projects>/-bucket/<sid>.jsonl and return its path."""
        path = os.path.join(self.proj, "-bucket", sid + ".jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for o in lines:
                f.write(json.dumps(o) + "\n")
        return path

    def _user(self, text, uuid=None):
        o = {"type": "user", "message": {"role": "user", "content": text}}
        if uuid:
            o["uuid"] = uuid
        return o

    def _assistant(self, text):
        return {"type": "assistant",
                "message": {"role": "assistant",
                            "content": [{"type": "text", "text": text}]}}

    def _count_parses(self, attr="_session_msgcount_uncached"):
        """Swap the real parser for a counting wrapper. Returns the counter list;
        the wrapper delegates, so values stay real."""
        real = getattr(ts, attr)
        calls = []

        def spy(path):
            calls.append(path)
            return real(path)

        setattr(ts, attr, spy)
        self.addCleanup(setattr, ts, attr, real)
        return calls

    def _cache_file(self):
        return os.path.join(self.tmp, "cache", "msgcounts.json")


# ==================================================== the in-process count cache
class MsgcountCacheTest(_CacheMixin, unittest.TestCase):
    def test_repeat_calls_parse_once(self):
        p = self._transcript("s1", [self._user("hello"), self._user("again")])
        calls = self._count_parses()
        first = ts._session_msgcount(p)
        for _ in range(20):
            self.assertEqual(ts._session_msgcount(p), first)
        self.assertEqual(first, 2)
        self.assertEqual(len(calls), 1, "cache hit still re-parsed the transcript")

    def test_a_changed_transcript_is_a_miss(self):
        """The whole point of keying on (mtime, size): an APPEND must be seen."""
        p = self._transcript("s1", [self._user("hello")])
        calls = self._count_parses()
        self.assertEqual(ts._session_msgcount(p), 1)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(self._user("second")) + "\n")
        self.assertEqual(ts._session_msgcount(p), 2)
        self.assertEqual(len(calls), 2)

    def test_mtime_change_alone_is_a_miss(self):
        """Same byte count, different mtime — a same-size rewrite must not be
        served from the cache, so mtime is part of the key, not just size."""
        p = self._transcript("s1", [self._user("aaaa")])
        calls = self._count_parses()
        self.assertEqual(ts._session_msgcount(p), 1)
        size = os.path.getsize(p)
        st = os.stat(p)
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
        self.assertEqual(os.path.getsize(p), size)     # size genuinely unchanged
        ts._session_msgcount(p)
        self.assertEqual(len(calls), 2, "an mtime bump did not invalidate the entry")

    def test_system_and_empty_messages_still_excluded(self):
        """The cached value is the SAME value: '<...>' system wrappers and blank
        content are not user messages, cache or no cache."""
        p = self._transcript("s1", [
            self._user("real one"),
            self._user("<system-reminder>ignore me</system-reminder>"),
            self._user("   "),
            self._assistant("not a user message"),
        ])
        self.assertEqual(ts._session_msgcount(p), 1)
        self.assertEqual(ts._session_msgcount(p), 1)

    def test_unreadable_file_still_returns_zero(self):
        missing = os.path.join(self.proj, "-bucket", "nope.jsonl")
        self.assertEqual(ts._session_msgcount(missing), 0)

    def test_directory_path_returns_zero(self):
        """A stat-able non-file: keyed fine, parsed as unreadable → 0, as before."""
        self.assertEqual(ts._session_msgcount(os.path.join(self.proj, "-bucket")), 0)

    def test_memory_cache_is_bounded(self):
        real_cap = ts.MSGCOUNT_MEM_MAX
        ts.MSGCOUNT_MEM_MAX = 4
        self.addCleanup(setattr, ts, "MSGCOUNT_MEM_MAX", real_cap)
        for i in range(12):
            ts._session_msgcount(self._transcript("s%d" % i, [self._user("x")]))
        self.assertLessEqual(len(ts._MSGCOUNT_MEM), 4)


# ======================================================= the persistent cache ==
class PersistentCacheTest(_CacheMixin, unittest.TestCase):
    def _turn(self):
        """End the 'turn': flush to disk and drop the in-process layer, which is
        what a new process would see."""
        ts._msgcount_flush()
        ts._MSGCOUNT_MEM.clear()
        ts._MSGCOUNT_DISK = None

    def test_count_survives_a_turn_without_reparsing(self):
        p = self._transcript("s1", [self._user("a"), self._user("b")])
        ts._session_msgcount(p)
        self._turn()
        calls = self._count_parses()
        self.assertEqual(ts._session_msgcount(p), 2)
        self.assertEqual(calls, [], "an unchanged transcript was re-parsed next turn")

    def test_cache_file_lands_under_the_data_dir(self):
        ts._session_msgcount(self._transcript("s1", [self._user("a")]))
        ts._msgcount_flush()
        self.assertTrue(os.path.exists(self._cache_file()))
        with open(self._cache_file(), encoding="utf-8") as f:
            blob = json.load(f)
        self.assertEqual(blob["v"], 1)
        self.assertEqual(len(blob["entries"]), 1)

    def test_a_changed_transcript_reparses_across_turns(self):
        p = self._transcript("s1", [self._user("a")])
        ts._session_msgcount(p)
        self._turn()
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(self._user("b")) + "\n")
        calls = self._count_parses()
        self.assertEqual(ts._session_msgcount(p), 2)
        self.assertEqual(len(calls), 1)

    def test_nothing_computed_writes_no_file(self):
        ts._msgcount_flush()
        self.assertFalse(os.path.exists(self._cache_file()))

    def test_flush_does_not_resurrect_a_deleted_data_dir(self):
        """A cache flush is not what brings a store into being — an atexit flush
        after a test tore its tmpdir down must not recreate it."""
        ts._session_msgcount(self._transcript("s1", [self._user("a")]))
        shutil.rmtree(self.tmp, ignore_errors=True)
        ts._msgcount_flush()                          # must not raise
        self.assertFalse(os.path.exists(self.tmp))

    def test_repointing_the_data_dir_flushes_the_old_cache(self):
        p = self._transcript("s1", [self._user("a")])
        ts._session_msgcount(p)                       # dirties the cache under self.tmp
        other = tempfile.mkdtemp(prefix="ts-cache-other-")
        self.addCleanup(shutil.rmtree, other, True)
        ts.DATA = other
        ts._msgcount_disk()                           # notices the move
        ts.DATA = self.tmp
        self.assertTrue(os.path.exists(self._cache_file()),
                        "work done under the old data dir was dropped on the floor")


# ============================================ corruption tolerance (Stop hook) =
class CorruptCacheTest(_CacheMixin, unittest.TestCase):
    """A cache is an optimisation, never a correctness dependency. Every one of
    these inputs must yield the right count with no exception — this code runs
    inside the Stop hook, where raising would block the user's turn."""

    def _poison(self, text):
        os.makedirs(os.path.dirname(self._cache_file()), exist_ok=True)
        with open(self._cache_file(), "w", encoding="utf-8") as f:
            f.write(text)
        # Drop BOTH layers, or an in-memory hit from an earlier assertion would
        # answer the question and the poisoned file would never be read.
        ts._MSGCOUNT_MEM.clear()
        ts._MSGCOUNT_DISK = None

    def test_truncated_json_falls_back(self):
        p = self._transcript("s1", [self._user("a"), self._user("b")])
        self._poison('{"v": 1, "entries": {"' + p + '": [12345, 6')
        self.assertEqual(ts._session_msgcount(p), 2)

    def test_not_json_at_all_falls_back(self):
        p = self._transcript("s1", [self._user("a")])
        self._poison("this is not json, it is a haiku\nabout cache invalidation\n")
        self.assertEqual(ts._session_msgcount(p), 1)

    def test_empty_file_falls_back(self):
        p = self._transcript("s1", [self._user("a")])
        self._poison("")
        self.assertEqual(ts._session_msgcount(p), 1)

    def test_foreign_shapes_fall_back(self):
        p = self._transcript("s1", [self._user("a")])
        for blob in ('[]', '"a string"', 'null', '42',
                     '{"entries": []}', '{"entries": "nope"}',
                     '{"v": 99, "no_entries_key": {}}'):
            self._poison(blob)
            self.assertEqual(ts._session_msgcount(p), 1, blob)

    def test_partial_rows_are_dropped_not_trusted(self):
        """One malformed row must not poison the file — and must never be read as
        a valid (mtime, size, count) triple."""
        p = self._transcript("s1", [self._user("a")])
        good = self._transcript("s2", [self._user("a"), self._user("b")])
        st = os.stat(good)
        self._poison(json.dumps({"v": 1, "entries": {
            p: [1, 2],                                        # too short
            "/x/y": [1, 2, "three", 4],                       # wrong type
            "/x/z": None,                                     # not a list
            good: [st.st_mtime_ns, st.st_size, 2, 0],         # valid, must survive
        }}))
        calls = self._count_parses()
        self.assertEqual(ts._session_msgcount(p), 1)          # recomputed
        self.assertEqual(ts._session_msgcount(good), 2)       # served from cache
        self.assertEqual(calls, [p])

    def test_a_stale_entry_is_ignored(self):
        """Right path, wrong version — the recorded (mtime, size) no longer match,
        so the count must be recomputed rather than believed."""
        p = self._transcript("s1", [self._user("a"), self._user("b")])
        self._poison(json.dumps({"v": 1, "entries": {p: [1, 1, 999, 0]}}))
        self.assertEqual(ts._session_msgcount(p), 2)

    def test_unwritable_cache_dir_does_not_raise(self):
        """<data_dir>/cache blocked by a regular FILE of the same name: the flush
        has nowhere to go and must still return quietly."""
        with open(os.path.join(self.tmp, "cache"), "w", encoding="utf-8") as f:
            f.write("not a directory")
        p = self._transcript("s1", [self._user("a")])
        self.assertEqual(ts._session_msgcount(p), 1)
        ts._msgcount_flush()                          # must not raise


# ==================================================== the prompt→reply cache ===
class PromptRepliesCacheTest(_CacheMixin, unittest.TestCase):
    LINES_SID = "hub1"

    def _convo(self):
        """Two human turns, the second split by a tool round-trip, plus a sidechain
        line — the shapes the reply attribution has to survive."""
        return [
            self._user("first question", uuid="u1"),
            self._assistant("thinking"),
            self._assistant("- the first answer"),
            self._user("second question", uuid="u2"),
            self._assistant("working on it"),
            {"type": "user", "uuid": "tool1",
             "message": {"role": "user",
                         "content": [{"type": "tool_result", "content": "ok"}]}},
            {"type": "user", "uuid": "side1", "isSidechain": True,
             "message": {"role": "user", "content": "subagent noise"}},
            self._assistant("- the second answer"),
        ]

    def test_subsets_agree_with_the_whole_and_parse_once(self):
        self._transcript(self.LINES_SID, self._convo())
        calls = self._count_parses("_prompt_replies_all")
        both = ts._prompt_replies(self.LINES_SID, {"u1", "u2"})
        self.assertEqual(both, {"u1": "the first answer",
                                "u2": "the second answer"})
        # Each subset must match the whole-map answer, from the SAME single parse.
        self.assertEqual(ts._prompt_replies(self.LINES_SID, {"u1"}),
                         {"u1": both["u1"]})
        self.assertEqual(ts._prompt_replies(self.LINES_SID, {"u2"}),
                         {"u2": both["u2"]})
        self.assertEqual(ts._prompt_replies(self.LINES_SID, {"u2", "unknown"}),
                         {"u2": both["u2"]})
        self.assertEqual(len(calls), 1, "the transcript was parsed more than once")

    def test_reply_is_bounded_by_its_own_turn(self):
        """Caching the whole map only works because a reply cannot leak across a
        real user turn. Asking for ONLY the later prompt must not hand back the
        earlier turn's answer."""
        self._transcript(self.LINES_SID, self._convo())
        self.assertEqual(ts._prompt_replies(self.LINES_SID, {"u2"}),
                         {"u2": "the second answer"})

    def test_changed_transcript_reparses(self):
        self._transcript(self.LINES_SID, self._convo())
        calls = self._count_parses("_prompt_replies_all")
        ts._prompt_replies(self.LINES_SID, {"u1"})
        path = os.path.join(self.proj, "-bucket", self.LINES_SID + ".jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(self._user("third", uuid="u3")) + "\n")
            f.write(json.dumps(self._assistant("- the third answer")) + "\n")
        self.assertEqual(ts._prompt_replies(self.LINES_SID, {"u3"}),
                         {"u3": "the third answer"})
        self.assertEqual(len(calls), 2)

    def test_empty_request_never_reads_the_disk(self):
        self._transcript(self.LINES_SID, self._convo())
        calls = self._count_parses("_prompt_replies_all")
        self.assertEqual(ts._prompt_replies(self.LINES_SID, set()), {})
        self.assertEqual(ts._prompt_replies(None, {"u1"}), {})
        self.assertEqual(ts._prompt_replies(self.LINES_SID, {None, ""}), {})
        self.assertEqual(calls, [])

    def test_missing_transcript_is_empty_not_an_error(self):
        self.assertEqual(ts._prompt_replies("no-such-session", {"u1"}), {})

    def test_empty_map_is_a_real_cache_hit(self):
        """A transcript with no attributable replies caches as `{}` — falsy, but a
        HIT, so it must not be re-parsed on every call."""
        self._transcript("quiet", [self._user("no reply came", uuid="q1")])
        calls = self._count_parses("_prompt_replies_all")
        for _ in range(3):
            self.assertEqual(ts._prompt_replies("quiet", {"q1"}), {})
        self.assertEqual(len(calls), 1)

    def test_a_malformed_assistant_line_does_not_raise(self):
        """The whole-map parse sets `cur` for every prompt, so it reads assistant
        lines the per-call version skipped. A transcript with a non-dict `message`
        must degrade to 'no reply', not blow up inside a Stop hook."""
        self._transcript("bad", [
            self._user("q", uuid="b1"),
            {"type": "assistant", "message": ["not", "a", "dict"]},
            self._user("q2", uuid="b2"),
            self._assistant("- a real answer"),
        ])
        self.assertEqual(ts._prompt_replies("bad", {"b1", "b2"}),
                         {"b2": "a real answer"})

    def test_reply_cache_is_bounded(self):
        real_cap = ts.REPLIES_CACHE_MAX
        ts.REPLIES_CACHE_MAX = 3
        self.addCleanup(setattr, ts, "REPLIES_CACHE_MAX", real_cap)
        for i in range(10):
            sid = "s%d" % i
            self._transcript(sid, [self._user("q", uuid="u%d" % i),
                                   self._assistant("- a")])
            ts._prompt_replies(sid, {"u%d" % i})
        self.assertLessEqual(len(ts._REPLIES_MEM), 3)


# ==================================================== the transcript-path memo ==
class SessionPathMemoTest(_CacheMixin, unittest.TestCase):
    def test_resolution_is_memoized_but_re_verified(self):
        p = self._transcript("s1", [self._user("a")])
        self.assertEqual(ts._find_session_path("s1"), p)
        self.assertEqual(ts._find_session_path("s1"), p)
        os.remove(p)
        # The memo re-checks existence, so a vanished transcript is not handed back.
        self.assertIsNone(ts._find_session_path("s1"))

    def test_a_miss_is_not_cached(self):
        """Only successes are memoized — a transcript written later must be found."""
        self.assertIsNone(ts._find_session_path("later"))
        p = self._transcript("later", [self._user("a")])
        self.assertEqual(ts._find_session_path("later"), p)

    def test_memo_is_keyed_by_projects_root(self):
        """Tests (and a moved CLAUDE_CONFIG_DIR) repoint PROJECTS_ROOT; the same
        session id under a different root must not resolve to the old path."""
        p = self._transcript("s1", [self._user("a")])
        self.assertEqual(ts._find_session_path("s1"), p)
        other = tempfile.mkdtemp(prefix="ts-proj-")
        self.addCleanup(shutil.rmtree, other, True)
        os.makedirs(os.path.join(other, "-bucket"), exist_ok=True)
        ts.PROJECTS_ROOT = other
        self.assertIsNone(ts._find_session_path("s1"))


# ================================================= the on_stop.sh consolidation =
class StopGateUntouchedTest(unittest.TestCase):
    """stop-gate is the one call whose stdout the harness parses for
    {"decision":"block"}. Folding the seven best-effort steps into one interpreter
    must not have merged it, moved it, wrapped it, or changed what it prints."""

    def setUp(self):
        with open(os.path.join(HOOKS, "on_stop.sh"), encoding="utf-8") as f:
            self.text = f.read()
        # Assert against CODE, not comments — the comment block above the call
        # names the steps it replaced, and a prose mention is not an invocation.
        self.lines = [ln for ln in self.text.splitlines()
                      if ln.strip() and not ln.lstrip().startswith("#")]
        self.body = "\n".join(self.lines)

    def _line_index(self, needle):
        for i, ln in enumerate(self.lines):
            if needle in ln:
                return i
        self.fail("no line containing %r in on_stop.sh" % needle)

    def test_stop_gate_is_still_its_own_bare_call(self):
        gate = self.lines[self._line_index("stop-gate")]
        self.assertIn('python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" stop-gate', gate)
        # NOT wrapped: ts_run would eat the decision JSON, ts_capture would still
        # relabel a non-zero exit as success before the harness sees it.
        self.assertFalse(gate.strip().startswith(("ts_run", "ts_capture")), gate)
        # And nothing redirects or pipes its stdout away.
        for token in (">/dev/null", "> /dev/null", "| ", ">>"):
            self.assertNotIn(token, gate, gate)

    def test_stop_gate_runs_before_the_consolidated_steps(self):
        self.assertLess(self._line_index("stop-gate"),
                        self._line_index("stop_steps.py"))

    def test_the_seven_steps_are_one_call_now(self):
        self.assertEqual(self.body.count("stop_steps.py"), 1)
        # The old per-step python3 invocations are gone from the shell.
        for old in ("board --refresh-if-live", "obsidian --flush",
                    "usage --flush", "subscriptions check", "recap --auto-if-due",
                    "hud.py\" turn-end"):
            self.assertNotIn(old, self.body, old)

    def test_the_runner_is_masked_like_every_other_call_site(self):
        """A failure of the runner ITSELF still has to be recorded, and its stdout
        still has to reach the harness — so ts_capture, not ts_run, not bare."""
        runner = self.lines[self._line_index("stop_steps.py")]
        self.assertTrue(runner.strip().startswith("ts_capture stop-steps "), runner)

    def test_stop_gate_is_not_one_of_the_consolidated_steps(self):
        for label, _target, argv in stop_steps.STEPS:
            self.assertNotEqual(label, "stop-gate")
            self.assertNotIn("stop-gate", argv)

    def test_worker_suppression_still_short_circuits_first(self):
        self.assertLess(self._line_index("TASK_STATION_SUPPRESS"),
                        self._line_index("stop-gate"))


class StopStepsRunnerTest(unittest.TestCase):
    """Per-step failure isolation is what ts_run gave us for free across seven
    processes. Inside one interpreter it has to be written down — so it is tested."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-steps-")
        self._saved_env = {k: os.environ.get(k) for k in
                           ("TASK_STATION_HOME", "CLAUDE_CONFIG_DIR", "XDG_STATE_HOME")}
        os.environ["TASK_STATION_HOME"] = self.tmp
        self.log = os.path.join(self.tmp, "logs", "hook-health.log")
        self._saved_steps = stop_steps.STEPS
        self.ran = []

    def tearDown(self):
        stop_steps.STEPS = self._saved_steps
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_steps(self, *specs):
        """Install fake steps. Each spec is (label, behaviour) where behaviour is
        'ok', 'raise', 'exit', or a string to print."""
        ran = self.ran

        class _FakeTarget:
            @staticmethod
            def main(argv):
                label, behaviour = argv[0], argv[1]
                ran.append(label)
                if behaviour == "raise":
                    raise ValueError("step %s exploded" % label)
                if behaviour == "exit":
                    sys.exit(7)
                if behaviour != "ok":
                    sys.stdout.write(behaviour)
                return 0

        real_engine = stop_steps._engine
        stop_steps._engine = lambda: _FakeTarget
        self.addCleanup(setattr, stop_steps, "_engine", real_engine)
        stop_steps.STEPS = tuple(
            (label, "engine", [label, behaviour]) for label, behaviour in specs)

    def _log_lines(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding="utf-8") as f:
            return [ln for ln in f.read().splitlines() if ln.strip()]

    def test_a_failing_step_does_not_stop_the_others(self):
        self._fake_steps(("first", "ok"), ("boom", "raise"), ("third", "ok"))
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = stop_steps.main(["--session", "sid1"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.ran, ["first", "boom", "third"])

    def test_a_failing_step_is_recorded_with_its_label(self):
        self._fake_steps(("boom", "raise"))
        with redirect_stdout(io.StringIO()):
            stop_steps.main(["--session", "sid1"])
        entries = [hook_health.parse_line(ln) for ln in self._log_lines()]
        entries = [e for e in entries if e]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["label"], "boom")
        self.assertEqual(entries[0]["code"], 1)
        self.assertIn("exploded", entries[0]["detail"])

    def test_a_sys_exit_is_recorded_with_its_code(self):
        self._fake_steps(("bye", "exit"))
        with redirect_stdout(io.StringIO()):
            stop_steps.main(["--session", "sid1"])
        entries = [e for e in (hook_health.parse_line(ln) for ln in self._log_lines()) if e]
        self.assertEqual([(e["label"], e["code"]) for e in entries], [("bye", 7)])

    def test_a_clean_run_logs_nothing(self):
        self._fake_steps(("a", "ok"), ("b", "ok"))
        with redirect_stdout(io.StringIO()):
            stop_steps.main(["--session", "sid1"])
        self.assertEqual(self._log_lines(), [])

    def test_only_the_nudge_reaches_stdout(self):
        """ts_run discarded stdout for six steps and ts_capture passed it through
        for stop-nudge alone. That asymmetry is the harness contract."""
        self._fake_steps(("stop-nudge", "NUDGE-TEXT"),
                         ("board-refresh", "BOARD-NOISE"))
        buf = io.StringIO()
        with redirect_stdout(buf):
            stop_steps.main(["--session", "sid1"])
        out = buf.getvalue()
        self.assertIn("NUDGE-TEXT", out)
        self.assertNotIn("BOARD-NOISE", out)

    def test_session_id_is_substituted_verbatim(self):
        seen = {}

        class _Target:
            @staticmethod
            def main(argv):
                seen["argv"] = list(argv)
                return 0

        real_engine = stop_steps._engine
        stop_steps._engine = lambda: _Target
        self.addCleanup(setattr, stop_steps, "_engine", real_engine)
        stop_steps.STEPS = (("s", "engine", ["cmd", "--session", stop_steps.SESSION]),)
        with redirect_stdout(io.StringIO()):
            stop_steps.main(["--session", "100%-not-a-format-string"])
        self.assertEqual(seen["argv"],
                         ["cmd", "--session", "100%-not-a-format-string"])

    def test_missing_session_arg_still_runs(self):
        self._fake_steps(("a", "ok"))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(stop_steps.main([]), 0)
        self.assertEqual(self.ran, ["a"])


# ============================================ the python-side hook-health writer
class HookHealthRecordTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-hh-")
        self.log = os.path.join(self.tmp, "hook-health.log")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_format_matches_the_shell_writer(self):
        hook_health.record("usage-flush", 9, "bad things", path=self.log)
        with open(self.log, encoding="utf-8") as f:
            line = f.read().rstrip("\n")
        e = hook_health.parse_line(line)
        self.assertEqual((e["label"], e["code"], e["detail"]),
                         ("usage-flush", 9, "bad things"))
        self.assertEqual(len(line.split("\t")), 4)

    def test_only_the_last_non_blank_stderr_line_is_kept(self):
        hook_health.record("x", 1, "first\n\nsecond\nlast\n\n", path=self.log)
        e = hook_health.parse_line(open(self.log, encoding="utf-8").read())
        self.assertEqual(e["detail"], "last")

    def test_tabs_cannot_forge_a_field(self):
        hook_health.record("x", 1, "a\tb", path=self.log)
        line = open(self.log, encoding="utf-8").read().rstrip("\n")
        self.assertEqual(len(line.split("\t")), 4)
        self.assertEqual(hook_health.parse_line(line)["detail"], "a b")

    def test_one_failure_is_always_one_line(self):
        hook_health.record("x", 1, "multi\nline\ndetail", path=self.log)
        self.assertEqual(len(open(self.log, encoding="utf-8").read().splitlines()), 1)

    def test_log_is_bounded(self):
        saved = os.environ.get("TS_HOOK_LOG_MAX")
        os.environ["TS_HOOK_LOG_MAX"] = "5"
        try:
            for i in range(20):
                hook_health.record("label%d" % i, 1, "d", path=self.log)
        finally:
            if saved is None:
                os.environ.pop("TS_HOOK_LOG_MAX", None)
            else:
                os.environ["TS_HOOK_LOG_MAX"] = saved
        lines = open(self.log, encoding="utf-8").read().splitlines()
        self.assertEqual(len(lines), 5)
        self.assertIn("label19", lines[-1])           # newest kept

    def test_an_unwritable_log_is_not_an_error(self):
        """A regular file where the log's DIRECTORY should be: nowhere to write, and
        the caller is a hook, so this must return quietly rather than raise."""
        blocked = os.path.join(self.tmp, "blocked")
        with open(blocked, "w", encoding="utf-8") as f:
            f.write("not a directory")
        hook_health.record("x", 1, "d", path=os.path.join(blocked, "hook-health.log"))

    def test_non_numeric_code_is_still_loggable(self):
        hook_health.record("x", "boom", "d", path=self.log)
        self.assertEqual(hook_health.parse_line(
            open(self.log, encoding="utf-8").read())["code"], 1)


if __name__ == "__main__":
    unittest.main()
