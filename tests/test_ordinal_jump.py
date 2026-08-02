"""`/todo <seq>-<ordinal>` — jump straight into ONE named hub session.

A task's hub sessions are numbered `<seq>-<n>` by the session roster (`-0` is the
session that created the task). This adds `<seq>-<ordinal>` as a ref grammar and
makes it IMPLY a jump: `/todo 4-0` behaves like `/todo 4 -s` aimed at that one
session instead of the heuristic "newest substantive session" pick.

The four cases:
  (a) ordinal 0 is a REAL target — nothing may treat it as falsy
  (b) an unknown ordinal reports the ordinals that DO exist, not a bare no-match
  (c) an unresumable session says so and degrades to the fresh `--session-id`
      start, exactly like the existing `-s` fallback
  (d) a `<seq>` matching no task falls through to id-prefix resolution unchanged

Plain `<seq> -s` — single AND comma-list — must stay behaviourally identical.

Synthetic fixtures only: real temp transcript files (so getmtime works) plus a
monkeypatched `_session_msgcount`, and the jump window is captured, never opened.
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
    """render-namespace stand-in: cmd_render reads .session / .arg / .format."""
    def __init__(self, session=None, arg="", fmt=None):
        self.session = session
        self.arg = arg
        self.format = fmt


class OrdinalJumpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.store.reset_cache()
        self.proj = os.path.join(self.tmp, "projects")
        os.makedirs(self.proj, exist_ok=True)
        ts.PROJECTS_ROOT = self.proj

        # Synthetic transcript registry: sid -> (real path, faked msgcount).
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
        ts.store.reset_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers ---------------------------------------------------------------

    def _seed(self, title):
        t = ts.new_task(title, "summary")
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _register(self, task, sid, msgs, ordinal, mtime=None, transcript=True):
        """Record `sid` on `task` as a hub session carrying `ordinal`.

        `transcript=False` models a DEAD session — recorded on the roster but with
        no findable transcript, so it cannot be resumed.

        Advances `hub_ordinal_next` past `ordinal` the way the real allocator
        (`_next_hub_ordinal`) would, so a later fresh session gets a NEW number
        instead of colliding with a fixture one."""
        if transcript:
            path = os.path.join(self.proj, sid + ".jsonl")
            with open(path, "w") as f:
                f.write("{}\n")        # parseable, no cwd → falls back to the meta cwd
            if mtime is not None:
                os.utime(path, (mtime, mtime))
            self.paths[sid] = path
            self.msgs[path] = msgs
        task.setdefault("session_meta", {})[sid] = {
            "cwd": "/work/" + sid, "ts": ts._now(), "role": "hub", "ordinal": ordinal}
        task["hub_ordinal_next"] = max(int(task.get("hub_ordinal_next") or 0),
                                       ordinal + 1)
        if sid not in task.setdefault("sessions", []):
            task["sessions"].append(sid)

    def _render(self, session, arg):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_render(_Args(session=session, arg=arg))
        return buf.getvalue()

    # -- the grammar -----------------------------------------------------------

    def test_resolve_ref_ordinal_resolves_to_its_task(self):
        """`<seq>-<n>` resolves to the task carrying <seq> — the ordinal is consumed
        by the jump path, not by task resolution."""
        t = self._seed("Numbered")
        self.assertEqual(ts.resolve_ref("%s-0" % t["seq"])["id"], t["id"])
        self.assertEqual(ts.resolve_ref("%s-7" % t["seq"])["id"], t["id"])

    # -- (a) ordinal 0 is a real target ---------------------------------------

    def test_ordinal_zero_is_a_real_target_not_a_falsy_one(self):
        t = self._seed("Two hubs")
        old, new = "hub-zero", "hub-one"
        self._register(t, old, msgs=6, ordinal=0, mtime=1000)
        self._register(t, new, msgs=9, ordinal=1, mtime=9000)
        ts.save_task(t)

        # Baseline: plain `-s` prefers the NEWEST substantive session (hub-one).
        self.opened.clear()
        ts._jump_one(str(t["seq"]), "typist")
        self.assertIn("--resume %s" % new, self.opened[-1])

        # `<seq>-0` overrides that heuristic and lands on the creator session.
        # If 0 were treated as falsy this would fall back to the heuristic pick.
        self.opened.clear()
        ts._jump_one("%s-0" % t["seq"], "typist")
        self.assertIn("--resume %s" % old, self.opened[-1])
        self.assertNotIn(new, self.opened[-1])

    def test_ordinal_targets_the_named_session_not_the_newest(self):
        """The non-zero ordinal case: `-1` lands on -1 even when -0 is newer."""
        t = self._seed("Two hubs")
        zero, one = "hub-zero", "hub-one"
        self._register(t, zero, msgs=9, ordinal=0, mtime=9000)   # newest + biggest
        self._register(t, one, msgs=6, ordinal=1, mtime=1000)
        ts.save_task(t)
        self.opened.clear()
        ts._jump_one("%s-1" % t["seq"], "typist")
        self.assertIn("--resume %s" % one, self.opened[-1])
        self.assertNotIn(zero, self.opened[-1])

    # -- (b) unknown ordinal --------------------------------------------------

    def test_unknown_ordinal_lists_the_ordinals_that_exist(self):
        t = self._seed("Two hubs")
        self._register(t, "hub-zero", msgs=6, ordinal=0)
        self._register(t, "hub-one", msgs=9, ordinal=1)
        ts.save_task(t)
        self.opened.clear()
        out = ts._jump_one("%s-5" % t["seq"], "typist")
        self.assertNotIn("No task matching", out)        # NOT the bare no-match line
        self.assertIn("%s-0" % t["seq"], out)            # the ordinals that DO exist
        self.assertIn("%s-1" % t["seq"], out)
        self.assertFalse(self.opened)                    # a bad ordinal opens nothing

    def test_unknown_ordinal_on_a_task_with_no_hubs_says_so(self):
        """No numbered hub sessions at all → an explanation, still not a no-match."""
        t = self._seed("Never opened")
        self.opened.clear()
        out = ts._jump_one("%s-0" % t["seq"], "typist")
        self.assertNotIn("No task matching", out)
        self.assertFalse(self.opened)

    # -- (c) dead / unresumable session ---------------------------------------

    def test_unresumable_ordinal_offers_the_fresh_session_id_start(self):
        t = self._seed("Dead session")
        dead = "dead-hub"
        self._register(t, dead, msgs=0, ordinal=0, transcript=False)
        ts.save_task(t)
        self.opened.clear()
        out = ts._jump_one("%s-0" % t["seq"], "typist")
        cmd = self.opened[-1]
        self.assertIn("--session-id", cmd)               # the existing -s fallback form
        self.assertNotIn("--resume %s" % dead, cmd)
        self.assertIn("%s-0" % t["seq"], out)            # names WHICH session was dead
        self.assertIn("fresh", out.lower())

    def test_naming_your_own_session_by_ordinal_starts_fresh(self):
        """Resuming the conversation you jumped FROM is the tainting bug `-s` guards
        against, so `<seq>-<n>` pointing at the invoking session degrades the same way."""
        t = self._seed("Self target")
        me = "my-own-session"
        self._register(t, me, msgs=8, ordinal=0)
        ts.save_task(t)
        self.opened.clear()
        out = ts._jump_one("%s-0" % t["seq"], me)
        cmd = self.opened[-1]
        self.assertIn("--session-id", cmd)
        self.assertNotIn("--resume %s" % me, cmd)
        self.assertIn("%s-0" % t["seq"], out)

    # -- (d) a <seq> matching no task -----------------------------------------

    def test_unknown_seq_falls_through_to_id_prefix(self):
        t = self._seed("Only task")
        # No task carries seq 9999 → the ordinal branch declines and the id-prefix
        # branch finds nothing either.
        self.assertIsNone(ts.resolve_ref("9999-3"))
        # …and ordinary id-prefix resolution is untouched.
        self.assertEqual(ts.resolve_ref(t["id"][:8])["id"], t["id"])
        self.opened.clear()
        out = self._render("typist", "9999-3")
        self.assertIn("No task matching '9999-3'", out)   # unchanged no-match behaviour
        self.assertFalse(self.opened)

    def test_hyphenated_id_prefix_still_resolves(self):
        """A task id is a uuid4 STRING, so an id prefix past 8 chars CONTAINS a hyphen
        and can look like the session grammar ('03471986-1234'). Resolution order —
        ordinal branch only claims a ref whose seq exists — keeps such a prefix
        resolving to its task."""
        t = self._seed("Only task")
        self.assertEqual(ts.resolve_ref(t["id"][:13])["id"], t["id"])
        self.assertIn("-", t["id"][:13])                  # the prefix really is hyphenated

    # -- the jump is IMPLIED (no -s needed) -----------------------------------

    def test_bare_ordinal_ref_implies_a_jump(self):
        t = self._seed("Jumpable")
        hub = "hub-zero"
        self._register(t, hub, msgs=6, ordinal=0)
        ts.save_task(t)
        self.opened.clear()
        out = self._render("typist", "%s-0" % t["seq"])          # NOTE: no -s
        self.assertIn("[SESSION-JUMP]", out)
        self.assertIn("--resume %s" % hub, self.opened[-1])

    def test_explicit_dash_s_on_an_ordinal_ref_is_the_same_jump(self):
        t = self._seed("Jumpable")
        hub = "hub-zero"
        self._register(t, hub, msgs=6, ordinal=0)
        ts.save_task(t)
        self.opened.clear()
        out = self._render("typist", "%s-0 -s" % t["seq"])
        self.assertIn("[SESSION-JUMP]", out)
        self.assertIn("--resume %s" % hub, self.opened[-1])

    def test_ordinal_jump_leaves_the_invoking_session_unattached(self):
        """Same guarantee as `-s`: the window you typed in is never re-attached
        (the re-tint bug) — only the TARGET session carries the task."""
        a = self._seed("Owner")
        b = self._seed("Target")
        typist = "busy-typist"
        ts.set_link(typist, a["id"])            # invoking window is working on task A
        self._register(b, "b-hub", msgs=6, ordinal=0)
        ts.save_task(b)
        self.opened.clear()
        ts._jump_one("%s-0" % b["seq"], typist)
        self.assertEqual(ts.get_link(typist), a["id"])      # still A, NOT b

    # -- plain `<seq> -s` must be unchanged -----------------------------------

    def test_plain_seq_jump_is_unchanged(self):
        t = self._seed("HasSession")
        other = "working-sess"
        self._register(t, other, msgs=6, ordinal=0)
        ts.set_link(other, t["id"])
        ts.save_task(t)
        self.opened.clear()
        out = self._render("typist", "%s -s" % t["seq"])
        self.assertIn("[SESSION-JUMP]", out)
        self.assertIn("--resume %s" % other, self.opened[-1])
        self.assertEqual(ts.get_link(other), t["id"])

    def test_comma_multi_jump_is_unchanged(self):
        a = self._seed("First")
        b = self._seed("Second")
        self._register(a, "a-hub", msgs=6, ordinal=0)
        self._register(b, "b-hub", msgs=6, ordinal=0)
        ts.save_task(a)
        ts.save_task(b)
        self.opened.clear()
        out = self._render("typist", "%s,%s -s" % (a["seq"], b["seq"]))
        self.assertEqual(out.count("[SESSION-JUMP]"), 2)     # one block per task
        self.assertEqual(len(self.opened), 2)                # one window per task
        self.assertIn("--resume a-hub", " ".join(self.opened))
        self.assertIn("--resume b-hub", " ".join(self.opened))

    def test_comma_list_of_ordinal_refs_jumps_each_named_session(self):
        """The comma form composes with the new grammar for free."""
        a = self._seed("First")
        b = self._seed("Second")
        self._register(a, "a-hub", msgs=6, ordinal=0)
        self._register(b, "b-hub", msgs=6, ordinal=0)
        ts.save_task(a)
        ts.save_task(b)
        self.opened.clear()
        out = self._render("typist", "%s-0,%s-0 -s" % (a["seq"], b["seq"]))
        self.assertEqual(out.count("[SESSION-JUMP]"), 2)
        self.assertIn("--resume a-hub", " ".join(self.opened))
        self.assertIn("--resume b-hub", " ".join(self.opened))


if __name__ == "__main__":
    unittest.main()
