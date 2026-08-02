"""Best-practice auto-checkpoint triggers (1.61.0). Replaces the fixed-token
proactive threshold with (A) a PERCENTAGE-of-real-context nudge measured from the
transcript's most-recent `usage` block, and (B) an activity/milestone staleness
nudge that fires after N meaningful events. Covers: reverse-tail usage parsing,
the pct boundary, the byte-estimate fallback, the three new config keys, the
absolute back-compat trigger, and milestone counting. Stdlib-only, no LLM."""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)
import config  # noqa: E402  (shares TASK_STATION_HOME)
import hud  # noqa: E402


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _update_args(seq, **kw):
    base = dict(task=str(seq), title=None, summary=None, append_summary=None,
                state=None, goal=None, step_add=None, step_done=None,
                step_undone=None, decision=None, log=None, pr=None, pr_desc=None,
                story=None, story_desc=None, color=None, effort=None)
    base.update(kw)
    return _Args(**base)


# =============================================================== config layer ==
class _ConfigBase(unittest.TestCase):
    ENV = []

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        for e in self.ENV:
            os.environ.pop(e, None)

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        for e in self.ENV:
            os.environ.pop(e, None)
        shutil.rmtree(self.tmp, ignore_errors=True)


class ConfigCheckpointPct(_ConfigBase):
    ENV = ["TASK_STATION_CHECKPOINT_PCT"]

    def test_default_is_65(self):
        self.assertEqual(config.checkpoint_pct(), 65)

    def test_set_and_get(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(_Args(workspace_dirs=None, checkpoint_pct="80"))
        self.assertIn("checkpoint_pct = 80", buf.getvalue())
        self.assertEqual(config.checkpoint_pct(), 80)
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(_Args(workspace_dirs=None, checkpoint_pct=None,
                                    checkpoint_pct_get=True))
        self.assertEqual(buf.getvalue().strip(), "80")

    def test_off_and_zero_disable(self):
        for tok in ("off", "0"):
            buf = io.StringIO()
            with redirect_stdout(buf):
                config.cmd_config(_Args(workspace_dirs=None, checkpoint_pct=tok))
            self.assertIn("checkpoint_pct = off", buf.getvalue())
            self.assertEqual(config.checkpoint_pct(), 0)

    def test_clamped_to_range(self):
        config.set("checkpoint_pct", 200)
        self.assertEqual(config.checkpoint_pct(), 95)   # clamp high end

    def test_env_overrides_config(self):
        config.set("checkpoint_pct", 40)
        os.environ["TASK_STATION_CHECKPOINT_PCT"] = "70"
        self.assertEqual(config.checkpoint_pct(), 70)
        os.environ["TASK_STATION_CHECKPOINT_PCT"] = "off"
        self.assertEqual(config.checkpoint_pct(), 0)

    def test_reset_key(self):
        self.assertIn("checkpoint_pct", config.RESET_KEYS)
        config.set("checkpoint_pct", 90)
        config.reset_settings()
        self.assertIsNone(config.get("checkpoint_pct"))
        self.assertEqual(config.checkpoint_pct(), 65)

    def test_board_row_present(self):
        rows = {r[0]: r for r in config.board_rows()}
        self.assertIn("--checkpoint-pct", rows)
        self.assertEqual(len(rows["--checkpoint-pct"]), 6)


class ConfigContextWindow(_ConfigBase):
    ENV = ["TASK_STATION_CONTEXT_WINDOW"]

    def test_default_is_200000(self):
        self.assertEqual(config.context_window(), 200000)

    def test_set_and_get(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(_Args(workspace_dirs=None, context_window="1000000"))
        self.assertIn("context_window = 1000000", buf.getvalue())
        self.assertEqual(config.context_window(), 1000000)
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(_Args(workspace_dirs=None, context_window=None,
                                    context_window_get=True))
        self.assertEqual(buf.getvalue().strip(), "1000000")

    def test_junk_falls_back_to_default(self):
        config.set("context_window", "banana")
        self.assertEqual(config.context_window(), 200000)

    def test_env_overrides_config(self):
        config.set("context_window", 300000)
        os.environ["TASK_STATION_CONTEXT_WINDOW"] = "500000"
        self.assertEqual(config.context_window(), 500000)

    def test_reset_key(self):
        self.assertIn("context_window", config.RESET_KEYS)

    def test_model_derives_window_when_unset(self):
        # No explicit config → window follows the model actually in use.
        self.assertEqual(config.context_window("claude-opus-4-8[1m]"), 1000000)
        self.assertEqual(config.context_window("claude-haiku-4-5-20251001"), 200000)
        self.assertEqual(config.context_window("claude-sonnet-5"), 200000)
        # Unknown/empty model → the 200k default.
        self.assertEqual(config.context_window(""), 200000)

    def test_explicit_config_beats_model(self):
        config.set("context_window", 300000)
        self.assertEqual(config.context_window("claude-opus-4-8[1m]"), 300000)

    def test_env_beats_model(self):
        os.environ["TASK_STATION_CONTEXT_WINDOW"] = "500000"
        self.assertEqual(config.context_window("claude-opus-4-8[1m]"), 500000)


class ConfigMilestoneEdits(_ConfigBase):
    ENV = ["TASK_STATION_CHECKPOINT_MILESTONE_EDITS"]

    def test_default_is_5(self):
        self.assertEqual(config.checkpoint_milestone_edits(), 5)

    def test_set_and_get(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(_Args(workspace_dirs=None, checkpoint_milestone_edits="8"))
        self.assertIn("checkpoint_milestone_edits = 8", buf.getvalue())
        self.assertEqual(config.checkpoint_milestone_edits(), 8)
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(_Args(workspace_dirs=None, checkpoint_milestone_edits=None,
                                    checkpoint_milestone_edits_get=True))
        self.assertEqual(buf.getvalue().strip(), "8")

    def test_off_and_zero_disable(self):
        for tok in ("off", "0"):
            buf = io.StringIO()
            with redirect_stdout(buf):
                config.cmd_config(_Args(workspace_dirs=None, checkpoint_milestone_edits=tok))
            self.assertIn("checkpoint_milestone_edits = off", buf.getvalue())
            self.assertEqual(config.checkpoint_milestone_edits(), 0)

    def test_reset_key(self):
        self.assertIn("checkpoint_milestone_edits", config.RESET_KEYS)


class ConfigCheckpointAtDefaultOff(_ConfigBase):
    ENV = ["TASK_STATION_CHECKPOINT_AT"]

    def test_default_is_now_off(self):
        # The default changed from 150000 to 0 (off) — the pct mechanism is the
        # new default path; an explicitly stored value still works.
        self.assertEqual(config.checkpoint_at(), 0)

    def test_explicit_value_still_honoured(self):
        config.set("checkpoint_at", 120000)
        self.assertEqual(config.checkpoint_at(), 120000)


# ========================================= transcript usage measurement =======
class _StoreBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        for e in ("TASK_STATION_AUTO_CHECKPOINT", "TASK_STATION_GATE",
                  "TASK_STATION_CHECKPOINT_AT", "TASK_STATION_CHECKPOINT_PCT",
                  "TASK_STATION_CONTEXT_WINDOW", "TASK_STATION_CHECKPOINT_MILESTONE_EDITS"):
            os.environ.pop(e, None)
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.store.reset_cache()
        self.proj = os.path.join(self.tmp, "projects")
        self._orig_proot = ts.PROJECTS_ROOT
        ts.PROJECTS_ROOT = self.proj

    def tearDown(self):
        ts.PROJECTS_ROOT = self._orig_proot
        for e in ("TASK_STATION_HOME", "TASK_STATION_AUTO_CHECKPOINT",
                  "TASK_STATION_CHECKPOINT_AT", "TASK_STATION_CHECKPOINT_PCT",
                  "TASK_STATION_CONTEXT_WINDOW", "TASK_STATION_CHECKPOINT_MILESTONE_EDITS"):
            os.environ.pop(e, None)
        ts.store.reset_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _task(self, title="A task", status="open", **kw):
        t = ts.new_task(title, "summary for " + title, **kw)
        if status != "open":
            t["status"] = status
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _attach(self, session, task):
        ts.set_link(session, task["id"])
        return ts.load_task(task["id"])

    def _write_transcript(self, session, lines, prefix_junk=b""):
        """Write a fake JSONL transcript: `lines` are dicts (JSON-encoded one per
        line); `prefix_junk` is raw bytes prepended (to fake a sliced/partial tail)."""
        bucket = os.path.join(self.proj, "-fake-bucket")
        os.makedirs(bucket, exist_ok=True)
        body = b"".join((json.dumps(o) + "\n").encode() for o in lines)
        with open(os.path.join(bucket, session + ".jsonl"), "wb") as f:
            f.write(prefix_junk + body)

    @staticmethod
    def _usage_msg(inp, cache_read=0, cache_creation=0, output=999999):
        """An assistant message entry carrying a `usage` block (output is huge on
        purpose to prove it is NOT counted toward resident context)."""
        return {"type": "assistant", "message": {"role": "assistant", "usage": {
            "input_tokens": inp, "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_creation, "output_tokens": output}}}


class MeasureContextTokens(_StoreBase):
    def test_sums_input_and_cache_from_last_usage(self):
        self._write_transcript("s1", [
            self._usage_msg(100, cache_read=200, cache_creation=50),   # older
            {"type": "user", "message": {"role": "user", "content": "hi"}},
            self._usage_msg(1000, cache_read=120000, cache_creation=15000),  # newest usage
            {"type": "user", "message": {"role": "user", "content": "trailing, no usage"}},
        ])
        # newest usage: 1000 + 120000 + 15000 = 136000 (output NOT counted)
        self.assertEqual(ts.measure_context_tokens("s1"), 136000)

    def test_skips_trailing_non_usage_and_malformed_lines(self):
        self._write_transcript("s2", [
            self._usage_msg(5000, cache_read=60000),
            {"type": "user", "message": {"role": "user", "content": "x"}},
        ])
        # Append a malformed/partial line after the good data.
        path = ts._find_session_path("s2")
        with open(path, "ab") as f:
            f.write(b'{"type": "assistant", "message": {"usage": {"input_toke')
        self.assertEqual(ts.measure_context_tokens("s2"), 65000)

    def test_zero_when_no_usage_present(self):
        self._write_transcript("s3", [
            {"type": "user", "message": {"role": "user", "content": "no usage here"}},
        ])
        self.assertEqual(ts.measure_context_tokens("s3"), 0)

    def test_zero_when_not_found(self):
        self.assertEqual(ts.measure_context_tokens("missing"), 0)
        self.assertEqual(ts.measure_context_tokens(""), 0)

    def test_reads_only_the_tail(self):
        # A partial first line (as a mid-file slice would produce) is tolerated,
        # and the most-recent usage still wins.
        self._write_transcript("s4", [self._usage_msg(2000, cache_read=40000)],
                               prefix_junk=b'sabc": 1}}\n')
        self.assertEqual(ts.measure_context_tokens("s4"), 42000)


class SessionModel(_StoreBase):
    def test_reads_most_recent_model(self):
        self._write_transcript("m1", [
            {"type": "assistant", "message": {"role": "assistant",
                                              "model": "claude-sonnet-5", "usage": {}}},
            {"type": "user", "message": {"role": "user", "content": "hi"}},
            {"type": "assistant", "message": {"role": "assistant",
                                              "model": "claude-opus-4-8[1m]", "usage": {}}},
        ])
        self.assertEqual(ts.session_model("m1"), "claude-opus-4-8[1m]")

    def test_empty_when_no_model(self):
        self._write_transcript("m2", [
            {"type": "user", "message": {"role": "user", "content": "no model"}},
        ])
        self.assertEqual(ts.session_model("m2"), "")

    def test_empty_when_not_found(self):
        self.assertEqual(ts.session_model("missing"), "")
        self.assertEqual(ts.session_model(""), "")


# ============================= 1M-context window sizing (Opus[1m]) ============
class ClaudeCodeModelSelection(unittest.TestCase):
    """The Claude Code model SELECTION string — carries the `[1m]` marker the transcript
    drops. Env ANTHROPIC_MODEL → ~/.claude/settings.local.json → ~/.claude/settings.json."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._home = os.environ.get("HOME")
        os.environ["HOME"] = self.tmp
        os.environ.pop("ANTHROPIC_MODEL", None)
        os.makedirs(os.path.join(self.tmp, ".claude"))

    def tearDown(self):
        if self._home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._home
        os.environ.pop("ANTHROPIC_MODEL", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, obj):
        with open(os.path.join(self.tmp, ".claude", name), "w") as f:
            json.dump(obj, f)

    def test_env_wins(self):
        os.environ["ANTHROPIC_MODEL"] = "opus[1m]"
        self._write("settings.json", {"model": "claude-sonnet-5"})
        self.assertEqual(ts.claude_code_model_selection(), "opus[1m]")

    def test_local_settings_beats_user(self):
        self._write("settings.local.json", {"model": "opus[1m]"})
        self._write("settings.json", {"model": "claude-sonnet-5"})
        self.assertEqual(ts.claude_code_model_selection(), "opus[1m]")

    def test_user_settings_fallback(self):
        self._write("settings.json", {"model": "claude-opus-4-8[1m]"})
        self.assertEqual(ts.claude_code_model_selection(), "claude-opus-4-8[1m]")

    def test_empty_when_nothing_set(self):
        self.assertEqual(ts.claude_code_model_selection(), "")

    def test_malformed_settings_skipped(self):
        with open(os.path.join(self.tmp, ".claude", "settings.json"), "w") as f:
            f.write("{ not valid json")
        self.assertEqual(ts.claude_code_model_selection(), "")


class SameModelFamily(unittest.TestCase):
    def test_same_family_across_marker(self):
        self.assertTrue(ts._same_model_family("claude-opus-4-8", "opus[1m]"))

    def test_cross_family_is_false(self):
        self.assertFalse(ts._same_model_family("claude-sonnet-5", "opus[1m]"))

    def test_unknown_id_is_conservative(self):
        self.assertFalse(ts._same_model_family("mystery-model", "opus[1m]"))
        self.assertFalse(ts._same_model_family("claude-opus-4-8", "mystery"))


class EffectiveContextWindow(_StoreBase):
    """effective_context_window: transcript window, upgraded to the selection's 1M window
    only when same-family, with the explicit override always winning."""
    def setUp(self):
        super().setUp()
        self._orig_sm = ts.session_model
        self._orig_sel = ts.claude_code_model_selection

    def tearDown(self):
        ts.session_model = self._orig_sm
        ts.claude_code_model_selection = self._orig_sel
        os.environ.pop("ANTHROPIC_MODEL", None)
        super().tearDown()

    def _wire(self, transcript_model, selection):
        ts.session_model = lambda s: transcript_model
        ts.claude_code_model_selection = lambda: selection

    def test_opus_1m_selection_upgrades_window(self):
        # transcript records the marker-stripped id; selection still carries [1m].
        self._wire("claude-opus-4-8", "opus[1m]")
        self.assertEqual(ts.effective_context_window("s"), 1_000_000)

    def test_family_guard_blocks_sonnet_under_opus1m(self):
        # a --model sonnet session under an opus[1m] default must NOT be upgraded.
        self._wire("claude-sonnet-5", "opus[1m]")
        self.assertEqual(ts.effective_context_window("s"), 200_000)

    def test_no_selection_uses_transcript_default(self):
        self._wire("claude-haiku-4-5", "")
        self.assertEqual(ts.effective_context_window("s"), 200_000)

    def test_transcript_marker_alone_upgrades(self):
        # even with no selection, a transcript that kept the marker → 1M via config.
        self._wire("claude-opus-4-8[1m]", "")
        self.assertEqual(ts.effective_context_window("s"), 1_000_000)

    def test_env_override_wins_over_selection(self):
        os.environ["TASK_STATION_CONTEXT_WINDOW"] = "123456"
        self._wire("claude-opus-4-8", "opus[1m]")
        self.assertEqual(ts.effective_context_window("s"), 123456)

    def test_config_override_wins_over_selection(self):
        config.set("context_window", 300000)
        self._wire("claude-opus-4-8", "opus[1m]")
        self.assertEqual(ts.effective_context_window("s"), 300000)

    def test_selection_not_larger_no_change(self):
        self._wire("claude-opus-4-8", "claude-opus-4-8")
        self.assertEqual(ts.effective_context_window("s"), 200_000)


class HarnessContextWindow(_StoreBase):
    """harness_context_window reads context_window_size from the HUD snapshot."""
    def _snap(self, sid, obj):
        import hud, os as _os
        d = _os.path.join(self.tmp, "hud"); _os.makedirs(d, exist_ok=True)
        hud._write_snap(sid, obj)
    def test_reads_size_from_snapshot(self):
        self._snap("h1", {"context_window_size": 1000000})
        self.assertEqual(ts.harness_context_window("h1"), 1000000)
    def test_none_when_no_snapshot(self):
        self.assertIsNone(ts.harness_context_window("missing"))
        self.assertIsNone(ts.harness_context_window(""))
    def test_none_when_key_absent_or_junk(self):
        self._snap("h2", {"out_acc": 5})
        self.assertIsNone(ts.harness_context_window("h2"))
        self._snap("h3", {"context_window_size": "banana"})
        self.assertIsNone(ts.harness_context_window("h3"))
        self._snap("h4", {"context_window_size": 0})
        self.assertIsNone(ts.harness_context_window("h4"))


class ObserveCapturesWindowSize(_StoreBase):
    """hud.observe persists the payload's context_window_size (and nothing from rate_limits)."""
    def test_captures_1m_size(self):
        import hud
        hud.observe("o1", {"context_window": {"context_window_size": 1000000},
                             "rate_limits": {"five_hour": {"used_percentage": 99}}})
        self.assertEqual(ts.harness_context_window("o1"), 1000000)
    def test_absent_size_leaves_snapshot_clean(self):
        import hud
        hud.observe("o2", {"context_window": {"used_percentage": 8}})
        self.assertIsNone(ts.harness_context_window("o2"))


class PersistHarnessContextWindow(_StoreBase):
    """persist_harness_context_window merges size into the snapshot, HUD-independent."""
    def _snap(self, sid):
        return ts.harness_context_window(sid)
    def test_writes_size(self):
        ts.persist_harness_context_window("p1", 1000000)
        self.assertEqual(self._snap("p1"), 1000000)
    def test_merges_preserving_other_keys(self):
        import hud
        hud._write_snap("p2", {"out_acc": 42})
        ts.persist_harness_context_window("p2", 1000000)
        self.assertEqual(self._snap("p2"), 1000000)
        # existing key survives the merge
        import json as _j, os as _o, paths as _p
        d = _j.load(open(_o.path.join(_p.data_dir(), "hud", "p2.json")))
        self.assertEqual(d.get("out_acc"), 42)
    def test_noop_on_junk_or_nonpositive(self):
        ts.persist_harness_context_window("p3", "banana")
        ts.persist_harness_context_window("p3", 0)
        ts.persist_harness_context_window("p3", -5)
        ts.persist_harness_context_window("", 1000000)
        self.assertIsNone(self._snap("p3"))
    def test_noop_when_unchanged_leaves_file_untouched(self):
        import os as _o, paths as _p
        ts.persist_harness_context_window("p4", 1000000)
        f = _o.path.join(_p.data_dir(), "hud", "p4.json")
        m0 = _o.path.getmtime(f)
        ts.persist_harness_context_window("p4", 1000000)  # same value -> no rewrite
        self.assertEqual(_o.path.getmtime(f), m0)


class WhoamiStatuslineCaptures(_StoreBase):
    """cmd_whoami --statusline captures context_window_size from piped stdin when the
    HUD is OFF (HUD-independent), and defers to hud.observe when the HUD is ON."""
    def setUp(self):
        super().setUp()
        self._stdin = sys.stdin
    def tearDown(self):
        sys.stdin = self._stdin
        super().tearDown()
    def _args(self, session):
        return _Args(session=session, statusline=True, width=0, porcelain=False)
    def _pipe(self, obj):
        sys.stdin = io.StringIO(json.dumps(obj))
    def test_captures_when_hud_off(self):
        config.set("hud", False)
        self._pipe({"session_id": "w1", "context_window": {"context_window_size": 1000000}})
        with redirect_stdout(io.StringIO()):
            ts.cmd_whoami(self._args("w1"))
        self.assertEqual(ts.harness_context_window("w1"), 1000000)
    def test_skips_capture_when_hud_on(self):
        config.set("hud", True)
        self._pipe({"session_id": "w2", "context_window": {"context_window_size": 1000000}})
        with redirect_stdout(io.StringIO()):
            ts.cmd_whoami(self._args("w2"))
        # HUD path is responsible (hud.observe); whoami must not have written it.
        self.assertIsNone(ts.harness_context_window("w2"))
    def test_tolerates_missing_context_window(self):
        config.set("hud", False)
        self._pipe({"session_id": "w3"})
        with redirect_stdout(io.StringIO()):
            ts.cmd_whoami(self._args("w3"))
        self.assertIsNone(ts.harness_context_window("w3"))


class EffectiveWindowFromSnapshot(_StoreBase):
    """effective_context_window prefers the harness snapshot over model-id derivation,
    but an explicit user override still wins."""
    def setUp(self):
        super().setUp()
        self._orig_sm = ts.session_model
        self._orig_sel = ts.claude_code_model_selection
        ts.session_model = lambda s: "claude-opus-4-8"   # marker-stripped
        ts.claude_code_model_selection = lambda: ""        # nothing in env/settings
    def tearDown(self):
        ts.session_model = self._orig_sm
        ts.claude_code_model_selection = self._orig_sel
        os.environ.pop("TASK_STATION_CONTEXT_WINDOW", None)
        super().tearDown()
    def _snap(self, sid, size):
        import hud; hud._write_snap(sid, {"context_window_size": size})
    def test_snapshot_1m_used_despite_stripped_transcript(self):
        self._snap("e1", 1000000)
        self.assertEqual(ts.effective_context_window("e1"), 1000000)
    def test_env_override_beats_snapshot(self):
        self._snap("e2", 1000000)
        os.environ["TASK_STATION_CONTEXT_WINDOW"] = "123456"
        self.assertEqual(ts.effective_context_window("e2"), 123456)
    def test_config_override_beats_snapshot(self):
        self._snap("e3", 1000000)
        config.set("context_window", 300000)
        self.assertEqual(ts.effective_context_window("e3"), 300000)
    def test_no_snapshot_falls_back_to_transcript_default(self):
        self.assertEqual(ts.effective_context_window("e4"), 200000)


class StopNudgeUsesSnapshotWindow(_StoreBase):
    """Integration: the Stop nudge sizes % against the SNAPSHOT's 1M window, and is
    driven ONLY by session context — rate-limit (5h/week) figures never affect it."""
    def setUp(self):
        super().setUp()
        config.set("auto_checkpoint", True)
        self._orig_sel = ts.claude_code_model_selection
        ts.claude_code_model_selection = lambda: ""        # force reliance on the snapshot
    def tearDown(self):
        ts.claude_code_model_selection = self._orig_sel
        super().tearDown()
    def _snap(self, sid, size):
        import hud; hud._write_snap(sid, {"context_window_size": size})
    def _run(self, session):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_stop_nudge(_Args(session=session))
        return buf.getvalue()
    def _transcript(self, session, input_tokens):
        self._write_transcript(session, [
            {"type": "assistant", "message": {"role": "assistant",
             "model": "claude-opus-4-8", "usage": {"input_tokens": input_tokens}}}])
    def test_silent_at_140k_on_1m_snapshot(self):
        t = self._task(status="active"); self._attach("n1", t)
        self._snap("n1", 1000000); self._transcript("n1", 140000)   # 14% < 65%
        self.assertEqual(self._run("n1").strip(), "")
    def test_fires_over_pct_with_1m_denominator(self):
        t = self._task(status="active"); self._attach("n2", t)
        self._snap("n2", 1000000); self._transcript("n2", 700000)   # 70% of 1M
        ac = json.loads(self._run("n2"))["hookSpecificOutput"]["additionalContext"]
        self.assertIn("700k/1000k", ac); self.assertIn("70% used", ac)
    def test_rate_limits_never_trigger_save(self):
        # A payload with 5-hour/weekly at 99% but only 14% CONTEXT used must stay silent:
        # auto-save keys on session context %, NOT rate-limit windows.
        import hud
        t = self._task(status="active"); self._attach("n3", t)
        hud.observe("n3", {"context_window": {"context_window_size": 1000000},
                            "rate_limits": {"five_hour": {"used_percentage": 99},
                                             "seven_day": {"used_percentage": 99}}})
        self._transcript("n3", 140000)
        self.assertEqual(self._run("n3").strip(), "")


class StopNudgeUsesSelectionWindow(_StoreBase):
    """Integration: the Stop nudge sizes the % against the SELECTION's 1M window, so an
    Opus-1M session reads ~16% used (not the ~84% the old 200k denominator produced)."""
    def setUp(self):
        super().setUp()
        config.set("auto_checkpoint", True)
        self._orig_sel = ts.claude_code_model_selection
        ts.claude_code_model_selection = lambda: "opus[1m]"

    def tearDown(self):
        ts.claude_code_model_selection = self._orig_sel
        os.environ.pop("ANTHROPIC_MODEL", None)
        super().tearDown()

    def _run(self, session):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_stop_nudge(_Args(session=session))
        return buf.getvalue()

    def _transcript(self, session, model, input_tokens):
        self._write_transcript(session, [
            {"type": "assistant", "message": {"role": "assistant", "model": model,
             "usage": {"input_tokens": input_tokens}}}])

    def test_1m_denominator_silences_at_140k(self):
        t = self._task(status="active"); self._attach("s1m", t)
        # transcript id is marker-stripped; 140k of 1M = 14% < 65% → silent.
        self._transcript("s1m", "claude-opus-4-8", 140000)
        self.assertEqual(self._run("s1m").strip(), "")

    def test_shows_1m_denominator_when_over(self):
        t = self._task(status="active"); self._attach("s1m2", t)
        self._transcript("s1m2", "claude-opus-4-8", 700000)   # 70% of 1M
        ac = json.loads(self._run("s1m2"))["hookSpecificOutput"]["additionalContext"]
        self.assertIn("700k/1000k", ac)
        self.assertIn("70% used", ac)


# ================================================= pct pressure nudge =========
class PctPressureNudge(_StoreBase):
    def setUp(self):
        super().setUp()
        config.set("auto_checkpoint", True)
        # default checkpoint_pct=65, context_window=200000; checkpoint_at off.

    def _run(self, session):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_stop_nudge(_Args(session=session))
        return buf.getvalue()

    def _ctx(self, session, tokens):
        # one usage line whose input_tokens == the measured context size
        self._write_transcript(session, [self._usage_msg(tokens)])

    def test_fires_at_or_above_pct(self):
        t = self._task(status="active"); self._attach("sess-p", t)
        self._ctx("sess-p", 140000)                 # 70% of 200k >= 65%
        obj = json.loads(self._run("sess-p").strip())
        ac = obj["hookSpecificOutput"]["additionalContext"]
        self.assertIn("/todo save", ac)
        self.assertIn("STRUCTURED", ac)
        self.assertIn("70% used", ac)              # disambiguated: used, not remaining
        self.assertIn("30% left", ac)              # remaining shown alongside (matches native)
        self.assertIn("140k/200k", ac)
        self.assertTrue(ts.load_task(t["id"]).get("pressure_nudged"))

    def test_silent_below_pct(self):
        t = self._task(status="active"); self._attach("sess-p", t)
        self._ctx("sess-p", 120000)                 # 60% < 65%
        self.assertEqual(self._run("sess-p").strip(), "")

    def test_exact_boundary_fires(self):
        t = self._task(status="active"); self._attach("sess-p", t)
        self._ctx("sess-p", 130000)                 # exactly 65%
        self.assertIn("/todo save", self._run("sess-p"))

    def test_silent_when_pct_off(self):
        config.set("checkpoint_pct", 0)
        t = self._task(status="active"); self._attach("sess-p", t)
        self._ctx("sess-p", 199000)
        self.assertEqual(self._run("sess-p").strip(), "")

    def test_bigger_window_raises_bar(self):
        config.set("context_window", 1000000)
        t = self._task(status="active"); self._attach("sess-p", t)
        self._ctx("sess-p", 140000)                 # 14% of 1M — well below 65%
        self.assertEqual(self._run("sess-p").strip(), "")

    def test_fires_once_until_save_resets(self):
        t = self._task(status="active"); self._attach("sess-p", t)
        self._ctx("sess-p", 150000)
        self.assertIn("/todo save", self._run("sess-p"))
        self.assertEqual(self._run("sess-p").strip(), "")
        with redirect_stdout(io.StringIO()):
            ts.cmd_render(_Args(session="sess-p", arg="save", format=None))
        self.assertFalse(ts.load_task(t["id"]).get("pressure_nudged"))
        self.assertIn("/todo save", self._run("sess-p"))


class AbsoluteFallbackTrigger(_StoreBase):
    """When no usage measurement is available, the absolute checkpoint_at token
    estimate (byte-size heuristic) still fires when explicitly configured."""
    def setUp(self):
        super().setUp()
        config.set("auto_checkpoint", True)
        config.set("checkpoint_pct", 0)             # pct path off
        config.set("checkpoint_at", 100000)

    def _run(self, session):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_stop_nudge(_Args(session=session))
        return buf.getvalue()

    def _bytes(self, session, nbytes):
        bucket = os.path.join(self.proj, "-fake-bucket")
        os.makedirs(bucket, exist_ok=True)
        with open(os.path.join(bucket, session + ".jsonl"), "w") as f:
            f.write("x" * nbytes)                   # not JSON → no usage measurement

    def test_absolute_estimate_fires(self):
        t = self._task(status="active"); self._attach("sess-a", t)
        self._bytes("sess-a", 480000)               # 120k est >= 100k
        ac = json.loads(self._run("sess-a"))["hookSpecificOutput"]["additionalContext"]
        self.assertIn("/todo save", ac)
        self.assertIn("120k tokens", ac)

    def test_silent_below_absolute(self):
        t = self._task(status="active"); self._attach("sess-a", t)
        self._bytes("sess-a", 200000)               # 50k est < 100k
        self.assertEqual(self._run("sess-a").strip(), "")


# ================================================= milestone staleness ========
class MilestoneStaleness(_StoreBase):
    def setUp(self):
        super().setUp()
        config.set("auto_checkpoint", True)
        config.set("checkpoint_pct", 0)             # keep the pressure path out of it
        config.set("checkpoint_at", 0)

    def _run(self, session):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_stop_nudge(_Args(session=session))
        return buf.getvalue()

    def test_counter_increments_on_edit_and_resets_on_refresh(self):
        t = self._task(status="active"); self._attach("sess-m", t)
        for _ in range(3):
            with redirect_stdout(io.StringIO()):
                ts.cmd_mark_edited(_Args(session="sess-m"))
        self.assertEqual(ts.digest_events(ts.load_task(t["id"])), 3)
        with redirect_stdout(io.StringIO()):
            ts.cmd_update(_update_args(t["seq"], state="refreshed"))
        self.assertEqual(ts.digest_events(ts.load_task(t["id"])), 0)

    def test_silent_below_milestone(self):
        config.set("checkpoint_milestone_edits", 5)
        t = self._task(status="active"); self._attach("sess-m", t)
        for _ in range(3):                          # 3 < 5
            with redirect_stdout(io.StringIO()):
                ts.cmd_mark_edited(_Args(session="sess-m"))
        self.assertEqual(self._run("sess-m").strip(), "")

    def test_fires_at_milestone(self):
        config.set("checkpoint_milestone_edits", 5)
        t = self._task(status="active"); self._attach("sess-m", t)
        for _ in range(5):
            with redirect_stdout(io.StringIO()):
                ts.cmd_mark_edited(_Args(session="sess-m"))
        ac = json.loads(self._run("sess-m"))["hookSpecificOutput"]["additionalContext"]
        self.assertIn("digest looks stale", ac)

    def test_off_restores_fire_on_any_staleness(self):
        config.set("checkpoint_milestone_edits", 0)
        t = self._task(status="active"); self._attach("sess-m", t)
        with redirect_stdout(io.StringIO()):        # a single edit → dirty
            ts.cmd_mark_edited(_Args(session="sess-m"))
        ac = json.loads(self._run("sess-m"))["hookSpecificOutput"]["additionalContext"]
        self.assertIn("digest looks stale", ac)

    def test_silent_when_not_stale(self):
        config.set("checkpoint_milestone_edits", 5)
        t = self._task(status="active"); self._attach("sess-m", t)
        self.assertEqual(self._run("sess-m").strip(), "")


if __name__ == "__main__":
    unittest.main()
