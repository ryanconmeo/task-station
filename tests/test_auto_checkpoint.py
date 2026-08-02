"""Opt-in automatic checkpointing (1.55.0). Master switch `--auto-checkpoint`
(default OFF, env override TASK_STATION_AUTO_CHECKPOINT); a `digest_dirty` staleness
flag set on real work + cleared on a digest refresh; a PostCompact history stash
(zero model tokens, reads compact_summary from stdin); a SessionStart(compact) digest
nudge; and a non-blocking, staleness-gated Stop nudge. All behaviours are inert when
the switch is off. Stdlib-only, no LLM."""
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

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


# ---------------------------------------------------------------- config layer
class ConfigSwitch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        os.environ.pop("TASK_STATION_AUTO_CHECKPOINT", None)

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        os.environ.pop("TASK_STATION_AUTO_CHECKPOINT", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_off(self):
        self.assertFalse(config.auto_checkpoint_enabled())

    def test_persists_on(self):
        config.set("auto_checkpoint", True)
        self.assertTrue(config.auto_checkpoint_enabled())

    def test_env_on_overrides_config_off(self):
        config.set("auto_checkpoint", False)
        os.environ["TASK_STATION_AUTO_CHECKPOINT"] = "on"
        self.assertTrue(config.auto_checkpoint_enabled())

    def test_env_off_overrides_config_on(self):
        config.set("auto_checkpoint", True)
        os.environ["TASK_STATION_AUTO_CHECKPOINT"] = "off"
        self.assertFalse(config.auto_checkpoint_enabled())

    def test_cmd_config_on_off_and_get(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(_Args(workspace_dirs=None, auto_checkpoint="on"))
        self.assertIn("auto_checkpoint = on", buf.getvalue())
        self.assertTrue(config.auto_checkpoint_enabled())
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(_Args(workspace_dirs=None, auto_checkpoint=None,
                                    auto_checkpoint_get=True))
        self.assertEqual(buf.getvalue().strip(), "on")
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(_Args(workspace_dirs=None, auto_checkpoint="off"))
        self.assertIn("auto_checkpoint = off", buf.getvalue())
        self.assertFalse(config.auto_checkpoint_enabled())

    def test_reset_clears_it(self):
        self.assertIn("auto_checkpoint", config.RESET_KEYS)
        config.set("auto_checkpoint", True)
        config.reset_settings()
        self.assertIsNone(config.get("auto_checkpoint"))
        self.assertFalse(config.auto_checkpoint_enabled())

    def test_board_row_present(self):
        rows = {r[0]: r for r in config.board_rows()}
        self.assertIn("--auto-checkpoint", rows)
        row = rows["--auto-checkpoint"]
        self.assertEqual(len(row), 6)                      # a valid 6-tuple
        self.assertEqual(row[2], "on · off")
        self.assertIn("(default: off)", row[3])
        self.assertIn("checkpoint", row[3])


# ---------------------------------------------------------------- store-backed
class _StoreBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        os.environ.pop("TASK_STATION_AUTO_CHECKPOINT", None)
        os.environ.pop("TASK_STATION_GATE", None)
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.store.reset_cache()
        self._stdin = sys.stdin

    def tearDown(self):
        sys.stdin = self._stdin
        os.environ.pop("TASK_STATION_HOME", None)
        os.environ.pop("TASK_STATION_AUTO_CHECKPOINT", None)
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


class StalenessFlag(_StoreBase):
    def test_missing_field_reads_not_stale(self):
        t = self._task()
        self.assertNotIn("digest_dirty", t)          # back-compat: absent
        self.assertFalse(ts.digest_stale(t))

    def test_mark_edited_sets_dirty(self):
        t = self._task(status="active")
        self._attach("sess-e", t)
        with redirect_stdout(io.StringIO()):
            ts.cmd_mark_edited(_Args(session="sess-e"))
        self.assertTrue(ts.digest_stale(ts.load_task(t["id"])))

    def test_promotion_to_active_sets_dirty(self):
        # A delegate/worktree promotion routes through `status active` → set_status.
        t = self._task(status="open")
        self.assertFalse(ts.digest_stale(t))
        with redirect_stdout(io.StringIO()):
            ts.cmd_status(_Args(task=str(t["seq"]), value="active"))
        self.assertTrue(ts.digest_stale(ts.load_task(t["id"])))

    def test_update_state_clears_dirty(self):
        t = self._task(status="active")
        self._attach("sess-e", t)
        with redirect_stdout(io.StringIO()):
            ts.cmd_mark_edited(_Args(session="sess-e"))
        self.assertTrue(ts.digest_stale(ts.load_task(t["id"])))
        with redirect_stdout(io.StringIO()):
            ts.cmd_update(_update_args(t["seq"], state="NEXT: keep going"))
        self.assertFalse(ts.digest_stale(ts.load_task(t["id"])))

    def test_update_non_digest_field_does_not_clear(self):
        # An update that touches only e.g. --title is NOT a digest refresh.
        t = self._task(status="active")
        t["digest_dirty"] = True; ts.save_task(t)
        with redirect_stdout(io.StringIO()):
            ts.cmd_update(_update_args(t["seq"], title="Renamed"))
        self.assertTrue(ts.digest_stale(ts.load_task(t["id"])))

    def test_todo_save_clears_dirty(self):
        t = self._task(status="active")
        t["digest_dirty"] = True; ts.save_task(t)
        self._attach("sess-s", t)
        with redirect_stdout(io.StringIO()):
            ts.cmd_render(_Args(session="sess-s", arg="save", format=None))
        self.assertFalse(ts.digest_stale(ts.load_task(t["id"])))


class PostCompact(_StoreBase):
    def _run(self, session, trigger, summary):
        sys.stdin = io.StringIO(summary)
        with redirect_stdout(io.StringIO()):
            ts.cmd_post_compact(_Args(session=session, trigger=trigger))

    def test_stashes_summary_to_history_when_on_and_attached(self):
        config.set("auto_checkpoint", True)
        t = self._task(); self._attach("sess-c", t)
        self._run("sess-c", "auto", "compacted things happened here")
        hist = ts.load_task(t["id"]).get("history") or []
        self.assertEqual(len(hist), 1)
        self.assertIn("context compacted (auto):", hist[0]["text"])
        self.assertIn("compacted things happened here", hist[0]["text"])
        # a backup record only — summary/state are NOT touched.
        after = ts.load_task(t["id"])
        self.assertEqual(after.get("summary"), t.get("summary"))
        self.assertFalse(after.get("state"))

    def test_trims_and_collapses_whitespace(self):
        config.set("auto_checkpoint", True)
        t = self._task(); self._attach("sess-c", t)
        raw = "line one\n\n   line two\t\tspaced   " + ("x " * 2000)
        self._run("sess-c", "manual", raw)
        text = (ts.load_task(t["id"])["history"])[0]["text"]
        self.assertNotIn("\n", text)
        self.assertNotIn("  ", text)                 # runs of whitespace collapsed
        self.assertTrue(text.endswith("…"))          # capped ~1200 chars
        self.assertLess(len(text), 1300)

    def test_noop_when_off(self):
        config.set("auto_checkpoint", False)
        t = self._task(); self._attach("sess-c", t)
        self._run("sess-c", "auto", "should not be stashed")
        self.assertEqual(ts.load_task(t["id"]).get("history") or [], [])

    def test_noop_when_unattached(self):
        config.set("auto_checkpoint", True)
        self._run("sess-none", "auto", "nobody is listening")
        # nothing attached → silent no-op, no task mutated (and no crash).
        self.assertEqual(len(ts.all_tasks()), 0)

    def test_noop_when_skipped(self):
        config.set("auto_checkpoint", True)
        t = self._task()
        ts.set_link("sess-skip", ts.SKIP_SENTINEL)
        self._run("sess-skip", "auto", "skipped session")
        self.assertEqual(ts.load_task(t["id"]).get("history") or [], [])


class SessionStartNudge(_StoreBase):
    def _ctx(self, session, source):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_session_start(_Args(session=session, source=source))
        return buf.getvalue()

    def test_nudge_when_on_compact_and_attached(self):
        config.set("auto_checkpoint", True)
        t = self._task(); self._attach("sess-a", t)
        out = self._ctx("sess-a", "compact")
        self.assertIn("Context was just compacted", out)
        self.assertIn("%s history" % t["seq"], out)

    def test_no_nudge_when_off(self):
        config.set("auto_checkpoint", False)
        t = self._task(); self._attach("sess-a", t)
        self.assertNotIn("Context was just compacted", self._ctx("sess-a", "compact"))

    def test_no_nudge_when_not_compact_source(self):
        config.set("auto_checkpoint", True)
        t = self._task(); self._attach("sess-a", t)
        self.assertNotIn("Context was just compacted", self._ctx("sess-a", "startup"))

    def test_no_nudge_when_unattached(self):
        config.set("auto_checkpoint", True)
        self._task()   # exists but this session is not attached to it
        self.assertNotIn("Context was just compacted", self._ctx("sess-free", "compact"))


class StopNudge(_StoreBase):
    def _run(self, session):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_stop_nudge(_Args(session=session))
        return buf.getvalue()

    def test_prints_additional_context_when_on_attached_stale(self):
        config.set("auto_checkpoint", True)
        t = self._task(status="active")
        # dirty + past the milestone (default 5) so the light nudge fires
        t["digest_dirty"] = True; t["digest_events"] = 5; ts.save_task(t)
        self._attach("sess-x", t)
        out = self._run("sess-x").strip()
        obj = json.loads(out)
        hso = obj["hookSpecificOutput"]
        self.assertEqual(hso["hookEventName"], "Stop")
        self.assertIn("digest looks stale", hso["additionalContext"])
        # non-blocking: never a decision:block
        self.assertNotIn("decision", obj)

    def test_silent_when_off(self):
        config.set("auto_checkpoint", False)
        t = self._task(); t["digest_dirty"] = True; ts.save_task(t)
        self._attach("sess-x", t)
        self.assertEqual(self._run("sess-x").strip(), "")

    def test_silent_when_not_stale(self):
        config.set("auto_checkpoint", True)
        t = self._task(); self._attach("sess-x", t)   # digest_dirty unset → not stale
        self.assertEqual(self._run("sess-x").strip(), "")

    def test_silent_when_unattached(self):
        config.set("auto_checkpoint", True)
        self.assertEqual(self._run("sess-free").strip(), "")


# ---------------------------------------------------------- config: --checkpoint-at
class ConfigCheckpointAt(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        os.environ.pop("TASK_STATION_CHECKPOINT_AT", None)

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        os.environ.pop("TASK_STATION_CHECKPOINT_AT", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_is_now_off(self):
        # Default changed 150000 → 0 (off) in 1.61.0: checkpoint_pct is the new
        # default proactive path; checkpoint_at stays as an explicit fallback.
        self.assertEqual(config.checkpoint_at(), 0)

    def test_set_and_get(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(_Args(workspace_dirs=None, checkpoint_at="120000"))
        self.assertIn("checkpoint_at = 120000", buf.getvalue())
        self.assertEqual(config.checkpoint_at(), 120000)
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(_Args(workspace_dirs=None, checkpoint_at=None,
                                    checkpoint_at_get=True))
        self.assertEqual(buf.getvalue().strip(), "120000")

    def test_off_and_zero_disable(self):
        for tok in ("off", "0"):
            buf = io.StringIO()
            with redirect_stdout(buf):
                config.cmd_config(_Args(workspace_dirs=None, checkpoint_at=tok))
            self.assertIn("checkpoint_at = off", buf.getvalue())
            self.assertEqual(config.checkpoint_at(), 0)
            buf = io.StringIO()
            with redirect_stdout(buf):
                config.cmd_config(_Args(workspace_dirs=None, checkpoint_at=None,
                                        checkpoint_at_get=True))
            self.assertEqual(buf.getvalue().strip(), "off")

    def test_env_overrides_config(self):
        config.set("checkpoint_at", 100000)
        os.environ["TASK_STATION_CHECKPOINT_AT"] = "50000"
        self.assertEqual(config.checkpoint_at(), 50000)
        os.environ["TASK_STATION_CHECKPOINT_AT"] = "off"
        self.assertEqual(config.checkpoint_at(), 0)

    def test_reset_key(self):
        self.assertIn("checkpoint_at", config.RESET_KEYS)
        config.set("checkpoint_at", 99999)
        config.reset_settings()
        self.assertIsNone(config.get("checkpoint_at"))
        self.assertEqual(config.checkpoint_at(), 0)         # back to default (off)

    def test_board_row_present(self):
        rows = {r[0]: r for r in config.board_rows()}
        self.assertIn("--checkpoint-at", rows)
        row = rows["--checkpoint-at"]
        self.assertEqual(len(row), 6)                      # a valid 6-tuple
        self.assertEqual(row[1], "off")                    # current value = default (off)
        self.assertEqual(row[2], "<tokens>")
        self.assertIn("0 = off", row[3])
        config.set("checkpoint_at", 120000)
        rows = {r[0]: r for r in config.board_rows()}
        self.assertEqual(rows["--checkpoint-at"][1], "120000")


# ------------------------------------------------- transcript token estimate + pressure
class _ProjectsBase(_StoreBase):
    """_StoreBase plus a redirected PROJECTS_ROOT so tests can fake a session
    transcript of a known size for estimate_session_tokens / the pressure nudge."""
    def setUp(self):
        super().setUp()
        self.proj = os.path.join(self.tmp, "projects")
        self._orig_proot = ts.PROJECTS_ROOT
        ts.PROJECTS_ROOT = self.proj
        os.environ.pop("TASK_STATION_CHECKPOINT_AT", None)

    def tearDown(self):
        ts.PROJECTS_ROOT = self._orig_proot
        os.environ.pop("TASK_STATION_CHECKPOINT_AT", None)
        super().tearDown()

    def _transcript(self, session, nbytes):
        bucket = os.path.join(self.proj, "-fake-bucket")
        os.makedirs(bucket, exist_ok=True)
        with open(os.path.join(bucket, session + ".jsonl"), "w") as f:
            f.write("x" * nbytes)


class EstimateSessionTokens(_ProjectsBase):
    def test_returns_bytes_over_4(self):
        self._transcript("sess-big", 400000)
        self.assertEqual(ts.estimate_session_tokens("sess-big"), 100000)

    def test_zero_when_not_found(self):
        self.assertEqual(ts.estimate_session_tokens("nope"), 0)

    def test_zero_for_empty_session(self):
        self.assertEqual(ts.estimate_session_tokens(""), 0)


class PressureNudge(_ProjectsBase):
    def setUp(self):
        super().setUp()
        config.set("auto_checkpoint", True)
        config.set("checkpoint_at", 100000)

    def _run(self, session):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_stop_nudge(_Args(session=session))
        return buf.getvalue()

    def _big(self, session, tokens):
        self._transcript(session, tokens * 4)              # bytes // 4 ≈ tokens

    def test_fires_full_save_nudge_when_large(self):
        t = self._task(status="active"); self._attach("sess-p", t)
        self._big("sess-p", 120000)                        # >= 100000 threshold
        obj = json.loads(self._run("sess-p").strip())
        hso = obj["hookSpecificOutput"]
        self.assertEqual(hso["hookEventName"], "Stop")
        ac = hso["additionalContext"]
        self.assertIn("/todo save", ac)
        self.assertIn("STRUCTURED", ac)
        self.assertIn("~120k tokens", ac)
        self.assertNotIn("decision", obj)                  # non-blocking
        self.assertTrue(ts.load_task(t["id"]).get("pressure_nudged"))

    def test_silent_below_threshold(self):
        t = self._task(status="active"); self._attach("sess-p", t)
        self._big("sess-p", 50000)
        self.assertEqual(self._run("sess-p").strip(), "")

    def test_silent_when_threshold_off(self):
        config.set("checkpoint_at", 0)
        t = self._task(status="active"); self._attach("sess-p", t)
        self._big("sess-p", 500000)
        self.assertEqual(self._run("sess-p").strip(), "")

    def test_silent_when_auto_off(self):
        config.set("auto_checkpoint", False)
        t = self._task(status="active"); self._attach("sess-p", t)
        self._big("sess-p", 500000)
        self.assertEqual(self._run("sess-p").strip(), "")

    def test_fires_once_until_a_save_resets(self):
        t = self._task(status="active"); self._attach("sess-p", t)
        self._big("sess-p", 120000)
        self.assertIn("/todo save", self._run("sess-p"))   # first Stop → nudges
        self.assertEqual(self._run("sess-p").strip(), "")  # still large but already nudged → silent
        with redirect_stdout(io.StringIO()):               # a /todo save resets the episode
            ts.cmd_render(_Args(session="sess-p", arg="save", format=None))
        self.assertFalse(ts.load_task(t["id"]).get("pressure_nudged"))
        self.assertIn("/todo save", self._run("sess-p"))   # can fire again now

    def test_precedence_over_staleness(self):
        t = self._task(status="active")
        t["digest_dirty"] = True; ts.save_task(t)
        self._attach("sess-p", t)
        self._big("sess-p", 120000)
        ac = json.loads(self._run("sess-p"))["hookSpecificOutput"]["additionalContext"]
        self.assertIn("STRUCTURED", ac)                    # pressure wins
        self.assertNotIn("digest looks stale", ac)

    def test_falls_back_to_staleness_when_small(self):
        t = self._task(status="active")
        t["digest_dirty"] = True; t["digest_events"] = 5; ts.save_task(t)   # past milestone
        self._attach("sess-p", t)
        self._big("sess-p", 10000)                         # below threshold
        ac = json.loads(self._run("sess-p"))["hookSpecificOutput"]["additionalContext"]
        self.assertIn("digest looks stale", ac)            # the light nudge


class TodoSaveMarkers(_StoreBase):
    def test_save_clears_both_nudge_flags_but_does_not_stamp(self):
        """2.16.0 split the two claims apart. The flags gate NUDGES ("your digest looks
        stale", "run /todo save NOW") and the nudge has been acted on the moment the
        block is read, so they still clear here. `last_full_save_ts` claims a full
        checkpoint was CAPTURED, and printing a prompt captures nothing — it used to be
        written anyway, leaving tasks that claimed a checkpoint with an empty summary."""
        t = self._task(status="active")
        t["digest_dirty"] = True
        t["pressure_nudged"] = True
        ts.save_task(t)
        self._attach("sess-s", t)
        with redirect_stdout(io.StringIO()):
            ts.cmd_render(_Args(session="sess-s", arg="save", format=None))
        after = ts.load_task(t["id"])
        self.assertFalse(after.get("digest_dirty"))
        self.assertFalse(after.get("pressure_nudged"))
        self.assertIsNone(after.get("last_full_save_ts"))
        # …but that a save was STARTED is true, and is recorded.
        self.assertIsInstance(after.get("save_started_ts"), (int, float))

    def test_the_write_is_what_stamps(self):
        t = self._task(status="active")
        self._attach("sess-w", t)
        with redirect_stdout(io.StringIO()):
            ts.cmd_render(_Args(session="sess-w", arg="save", format=None))
            ts.cmd_update(_update_args(t["seq"], summary="the present truth",
                                       state="NEXT: wire the parser"))
        after = ts.load_task(t["id"])
        self.assertIsInstance(after.get("last_full_save_ts"), (int, float))


class HooksRegistration(unittest.TestCase):
    def test_hooks_json_registers_postcompact(self):
        with open(os.path.join(_REPO_ROOT, "hooks", "hooks.json"), encoding="utf-8") as f:
            hooks = json.load(f)["hooks"]
        self.assertIn("PostCompact", hooks)
        entries = hooks["PostCompact"]
        matchers = {e.get("matcher") for e in entries}
        self.assertEqual(matchers, {"manual", "auto"})
        cmds = " ".join(h["command"] for e in entries for h in e["hooks"])
        self.assertIn("on_post_compact.sh", cmds)


if __name__ == "__main__":
    unittest.main()
