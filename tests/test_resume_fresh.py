"""The `-s` resume must never taint into the wrong conversation.

Covers the spun-off-task bug and its fixes (BRIEF.md):
  #3 skipped sessions excluded from `-s` candidacy
  #4 `-s` fresh-starts (mints + binds a `--session-id`) instead of resuming the
     current/only session; `resume_command` stays PURE on the display path
  #1 `create --no-attach` → empty sessions → `-s` is a fresh-start
  #6 `create` from a substantive tracked session defaults to no-attach + warns
  #2 `detach --session`
  #5 `pin --new` (pre-bind an unborn uuid)

Synthetic fixtures only: real temp transcript files (so getmtime works) plus a
monkeypatched `_session_msgcount`. Never touches live data.
"""
import importlib.util
import io
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


class _Args:
    def __init__(self, **kw):
        defaults = dict(task=None, session=None, title=None, summary="",
                        color=None, effort=None, force=False,
                        no_attach=False, attach=False, new=False)
        defaults.update(kw)
        self.__dict__.update(defaults)


class ResumeFreshTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        self.proj = os.path.join(self.tmp, "projects")
        os.makedirs(self.proj, exist_ok=True)
        ts.PROJECTS_ROOT = self.proj

        # Synthetic transcript registry: sid -> (real path, msgcount).
        self.paths = {}
        self.msgs = {}
        self._orig_find = ts._find_session_path
        self._orig_count = ts._session_msgcount
        self._orig_open = ts._open_jump_window
        ts._find_session_path = lambda sid: self.paths.get(sid)
        ts._session_msgcount = lambda path: self.msgs.get(path, 0)
        # Never actually open a Terminal window; capture the command instead.
        self.opened = []
        ts._open_jump_window = lambda cmd: (self.opened.append(cmd) or True)

    def tearDown(self):
        ts._find_session_path = self._orig_find
        ts._session_msgcount = self._orig_count
        ts._open_jump_window = self._orig_open
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers ---------------------------------------------------------------
    def _seed(self, title):
        t = ts.new_task(title, "summary")
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _register(self, task, sid, msgs, mtime=None):
        """Give `sid` a real (empty) transcript file + a faked message count, and
        record it on `task`'s session_meta as a hub session."""
        path = os.path.join(self.proj, sid + ".jsonl")
        with open(path, "w") as f:
            f.write("{}\n")            # parseable, no cwd → falls back to meta cwd
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        self.paths[sid] = path
        self.msgs[path] = msgs
        task.setdefault("session_meta", {})[sid] = {
            "cwd": "/work/" + sid, "ts": ts._now(), "role": "hub"}
        if sid not in task.setdefault("sessions", []):
            task["sessions"].append(sid)

    def _out(self, fn, args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            fn(args)
        return buf.getvalue()

    # -- #4 headline -----------------------------------------------------------
    def test_headline_only_candidate_is_current_session_fresh_starts(self):
        t = self._seed("Spun-off")
        cur = "current-session-uuid"
        self._register(t, cur, msgs=8)        # the ONLY recorded session, substantive
        ts.save_task(t)
        t = ts.load_task(t["id"])

        # Display path stays PURE and must NOT resume the current session.
        disp = ts.resume_command(t, cur) or ""
        self.assertNotIn("--resume", disp)
        self.assertNotIn("--session-id", disp)   # pure: no uuid minted on display

        # Jump path mints a fresh session and emits --session-id (never --resume cur).
        sid, cmd = ts.fresh_resume_command(t)
        self.assertNotEqual(sid, cur)               # a brand-new uuid, not the current one
        self.assertIn("--session-id %s" % sid, cmd)
        self.assertNotIn("--session-id %s" % cur, cmd)
        self.assertNotIn("--resume", cmd)

    def test_jump_from_current_session_opens_fresh_window(self):
        t = self._seed("Spun-off")
        cur = "typist-uuid"
        self._register(t, cur, msgs=8)
        ts.save_task(t)
        ts.set_link(cur, t["id"])
        ts._jump_one(str(t["seq"]), cur)
        self.assertTrue(self.opened, "jump should have opened a window")
        cmd = self.opened[-1]
        self.assertIn("--session-id", cmd)
        self.assertNotIn("--resume %s" % cur, cmd)

    # -- #3 skip exclusion -----------------------------------------------------
    def test_skipped_session_excluded_even_with_live_transcript(self):
        t = self._seed("Skip-aware")
        good = "good-sess"
        bad = "skipped-sess"
        self._register(t, good, msgs=5, mtime=1000)
        self._register(t, bad, msgs=9, mtime=9000)   # newer + bigger, but skipped
        ts.save_task(t)
        ts.set_link(bad, ts.SKIP_SENTINEL)
        t = ts.load_task(t["id"])
        disp = ts.resume_command(t, current_session="someone-else")
        self.assertIn("--resume %s" % good, disp)
        self.assertNotIn(bad, disp)

    # -- regression: a real OTHER session still resumes; display stays pure -----
    def test_real_other_session_still_resumes_and_display_is_pure(self):
        t = self._seed("Normal")
        other = "other-working-sess"
        self._register(t, other, msgs=6)
        ts.save_task(t)
        t = ts.load_task(t["id"])
        before = dict(t.get("session_meta", {}))
        disp1 = ts.resume_command(t, current_session="the-typist")
        disp2 = ts.resume_command(t, current_session="the-typist")
        self.assertIn("--resume %s" % other, disp1)
        self.assertEqual(disp1, disp2)                       # deterministic
        self.assertEqual(t.get("session_meta", {}), before)  # no uuid minted

    # -- #1 create --no-attach -------------------------------------------------
    def test_create_no_attach_leaves_empty_sessions(self):
        out = self._out(ts.cmd_create,
                        _Args(title="Later", summary="s", session=None, no_attach=True))
        self.assertIn("Created", out)
        t = [x for x in ts.sorted_tasks() if x["title"] == "Later"][0]
        self.assertEqual(t.get("sessions", []), [])
        self.assertEqual(t.get("session_meta", {}), {})
        self.assertIsNone(ts.resume_command(t))     # no sessions → -s fresh-starts

    # -- #6 create from a substantive tracked session --------------------------
    def test_create_from_substantive_session_defaults_no_attach_and_warns(self):
        parent = self._seed("Parent")
        s = "busy-session"
        self._register(parent, s, msgs=8)
        ts.save_task(parent)
        ts.set_link(s, parent["id"])
        out = self._out(ts.cmd_create,
                        _Args(title="Child", summary="x", session=s))
        new = [x for x in ts.sorted_tasks() if x["title"] == "Child"][0]
        self.assertEqual(new.get("sessions", []), [])          # NOT attached
        self.assertIn("--attach", out)                          # warning names the override
        self.assertEqual(ts.get_link(s), parent["id"])          # parent link untouched

    def test_create_attach_overrides_substantive_default(self):
        parent = self._seed("Parent")
        s = "busy-session-2"
        self._register(parent, s, msgs=8)
        ts.save_task(parent)
        ts.set_link(s, parent["id"])
        self._out(ts.cmd_create,
                  _Args(title="Forced", summary="x", session=s, attach=True))
        new = [x for x in ts.sorted_tasks() if x["title"] == "Forced"][0]
        self.assertIn(s, new.get("sessions", []))               # attached
        self.assertEqual(ts.get_link(s), new["id"])             # link moved to new task

    # -- #2 detach -------------------------------------------------------------
    def test_detach_removes_session_and_clears_pin_and_link(self):
        t = self._seed("Detachable")
        s = "sess-d"
        ts.touch(t, session=s)
        t["pinned_session"] = s
        ts.save_task(t)
        ts.set_link(s, t["id"])
        out = self._out(ts.cmd_detach, _Args(session=s, task=str(t["seq"])))
        self.assertIn("Detached", out)
        t2 = ts.load_task(t["id"])
        self.assertNotIn(s, t2.get("sessions", []))
        self.assertNotIn(s, t2.get("session_meta", {}))
        self.assertNotIn("pinned_session", t2)
        self.assertNotEqual(ts.get_link(s), t2["id"])

    def test_detach_is_idempotent_on_missing_ref(self):
        t = self._seed("Detachable2")
        out = self._out(ts.cmd_detach, _Args(session="never-attached", task=str(t["seq"])))
        self.assertIn("nothing to detach", out.lower())

    # -- #5 pin --new ----------------------------------------------------------
    def test_pin_new_mints_and_jump_emits_that_session_id(self):
        t = self._seed("Prebind")
        out = self._out(ts.cmd_pin, _Args(task=str(t["seq"]), session=None, new=True))
        t2 = ts.load_task(t["id"])
        pin = t2.get("pinned_session")
        self.assertTrue(pin)
        self.assertIn("--session-id %s" % pin, out)

        # Display path honours the preborn pin (emits --session-id, no transcript yet).
        self.assertIn("--session-id %s" % pin, ts.resume_command(t2))

        # The jump uses THAT pinned uuid — it must not mint a second session.
        self.opened.clear()
        ts._jump_one(str(t["seq"]), "typist")
        cmd = self.opened[-1]
        self.assertIn("--session-id %s" % pin, cmd)


if __name__ == "__main__":
    unittest.main()
