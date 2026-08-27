"""The session title carries the session's ROSTER LINE, not just the task number.

Two sessions on one task used to emit BYTE-IDENTICAL titles (`#444: <title>`),
so neither a human nor the harness could tell them apart — the harness fell back
to cwd-derived peer names and a session trying to address its successor could not
name the target. These lock the fix on every title surface: `session-title` (the
SessionStart hook), `prompt-title` (the UserPromptSubmit OSC escape), and
`_emit_title_to_origin` (the immediate create/attach/rename relabel).

The fallback is the other half: an unresolvable ln emits the OLD task-only label
and SAYS SO on stderr — never a wrong number, never a blank one, never silence.
"""
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

import store  # noqa: E402

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


def _repoint(tmp):
    os.environ["TASK_STATION_HOME"] = tmp
    ts.DATA = tmp
    ts.STORE = os.path.join(tmp, "store")
    ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
    ts.LINKS_DIR = os.path.join(ts.STORE, "links")
    ts.PROJECTS_ROOT = os.path.join(tmp, "projects")
    store.reset_cache()


class _Args:
    def __init__(self, session=None):
        self.session = session


class SessionTitleLn(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _repoint(self.tmp)
        os.environ.pop("TASK_STATION_TITLE", None)

    def tearDown(self):
        store.reset_cache()
        for k in ("TASK_STATION_HOME", "TASK_STATION_TITLE"):
            os.environ.pop(k, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- helpers ------------------------------------------------------------
    def _seed(self, title="World-tier rework: the roster"):
        t = ts.new_task(title, "x")
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _attach(self, task, sid):
        """Attach `sid` as a HUB session — touch() is what mints the roster ln."""
        t = ts.load_task(task["id"])
        ts.touch(t, session=sid, note="attached")
        ts.save_task(t)
        ts.set_link(sid, t["id"])
        return ts.load_task(t["id"])

    def _title(self, sid):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            ts.cmd_session_title(_Args(session=sid))
        return out.getvalue(), err.getvalue()

    def _prompt_title(self, sid):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            ts.cmd_prompt_title(_Args(session=sid))
        return out.getvalue(), err.getvalue()

    # ---- THE BUG: two sessions, one task ------------------------------------
    def test_two_sessions_on_one_task_get_different_titles(self):
        t = self._seed()
        t = self._attach(t, "sess-a")
        t = self._attach(t, "sess-b")
        a, err_a = self._title("sess-a")
        b, err_b = self._title("sess-b")
        self.assertNotEqual(a, b, "two sessions on one task must not share a title")
        # each carries ITS OWN ln, and both name the same task
        ln_a = ts.ordinal_label(ts.load_task(t["id"]), "sess-a")
        ln_b = ts.ordinal_label(ts.load_task(t["id"]), "sess-b")
        self.assertNotEqual(ln_a, ln_b)
        self.assertEqual(a.strip(), "#%s: %s" % (ln_a, t["title"]))
        self.assertEqual(b.strip(), "#%s: %s" % (ln_b, t["title"]))
        self.assertTrue(a.startswith("#%s-" % t["seq"]))
        self.assertTrue(b.startswith("#%s-" % t["seq"]))
        # the happy path is SILENT on stderr — the note is for the fallback only
        self.assertEqual(err_a, "")
        self.assertEqual(err_b, "")

    def test_positive_count_of_distinct_titles(self):
        """Pin the COUNT, not just pairwise inequality: N attached sessions
        produce N distinct titles, so a third session can't collide with a pair
        that already differs.

        The size is not invented. MEASURED 2026-08-27 against the real store,
        task #444 carries 28 rostered hub sessions; under the old code all 28
        collapsed to ONE title string, and under this one they yield 28. That is
        the acceptance narrative, so the fixture mirrors it at the same width."""
        t = self._seed()
        sids = ["sess-%d" % i for i in range(28)]
        for s in sids:
            t = self._attach(t, s)
        titles = [self._title(s)[0].strip() for s in sids]
        self.assertEqual(len(titles), 28)
        self.assertEqual(len(set(titles)), 28)
        for x in titles:
            self.assertRegex(x, r"^#%s-\d+: " % t["seq"])
        # …and the old, session-blind format collapses all 28 to one string —
        # the collapse this test exists to prevent regressing to.
        self.assertEqual(len({"#%s: %s" % (t["seq"], t["title"]) for _ in sids}), 1)

    def test_single_session_unchanged_apart_from_the_ln(self):
        t = self._seed("Only one session here")
        t = self._attach(t, "solo")
        out, err = self._title("solo")
        ln = ts.ordinal_label(ts.load_task(t["id"]), "solo")
        self.assertEqual(out.strip(), "#%s: %s" % (ln, t["title"]))
        # same shape as before, same title text — only the number grew a suffix
        self.assertEqual(out.strip().split(": ", 1)[1], t["title"])
        self.assertTrue(out.strip().startswith("#%s-" % t["seq"]))
        self.assertEqual(err, "")

    # ---- the fallback: visible, never silent --------------------------------
    def test_unattached_session_still_prints_nothing(self):
        out, err = self._title("nobody")
        self.assertEqual(out, "")
        self.assertEqual(err, "")

    def test_skipped_session_still_prints_nothing(self):
        ts.set_link("sess-skip", ts.SKIP_SENTINEL)
        out, err = self._title("sess-skip")
        self.assertEqual(out, "")
        self.assertEqual(err, "")

    def test_unresolvable_ln_falls_back_and_says_so(self):
        """A session LINKED to the task but never rostered as a hub (no
        session_meta entry) has no ln — emit the old format, and say which."""
        t = self._seed("No roster entry for me")
        ts.set_link("ghost", t["id"])          # link WITHOUT touch() → no ordinal
        out, err = self._title("ghost")
        self.assertEqual(out.strip(), "#%s: %s" % (t["seq"], t["title"]))
        self.assertIn("no roster ln", err)
        self.assertIn("ghost", err)
        self.assertNotEqual(err.strip(), "")

    def test_worker_session_falls_back_visibly(self):
        """Workers carry a descriptive name, never an ordinal — by design. The
        title must degrade to the task-only form and announce it."""
        t = self._seed("Has a worker")
        tk = ts.load_task(t["id"])
        ts.register_worker_session(tk, "wrk-1", name="task-station-1-0-repo")
        ts.save_task(tk)
        ts.set_link("wrk-1", t["id"])
        out, err = self._title("wrk-1")
        self.assertEqual(out.strip(), "#%s: %s" % (t["seq"], t["title"]))
        self.assertIn("no roster ln", err)

    # ---- the OTHER title surfaces -------------------------------------------
    def test_prompt_title_osc_carries_the_ln(self):
        t = self._seed("prompt-title surface")
        t = self._attach(t, "p-a")
        t = self._attach(t, "p-b")
        a, _ = self._prompt_title("p-a")
        b, _ = self._prompt_title("p-b")
        self.assertNotEqual(a, b)
        ln_a = ts.ordinal_label(ts.load_task(t["id"]), "p-a")
        self.assertEqual(a, "\033]0;#%s: %s\007" % (ln_a, t["title"]))
        self.assertTrue(a.startswith("\033]0;#"))
        self.assertTrue(a.endswith("\007"))

    def test_prompt_title_unresolvable_ln_falls_back_and_says_so(self):
        t = self._seed("prompt-title fallback")
        ts.set_link("p-ghost", t["id"])
        out, err = self._prompt_title("p-ghost")
        self.assertEqual(out, "\033]0;#%s: %s\007" % (t["seq"], t["title"]))
        self.assertIn("no roster ln", err)

    def test_prompt_title_disabled_still_emits_nothing(self):
        t = self._seed("off")
        self._attach(t, "p-off")
        os.environ["TASK_STATION_TITLE"] = "off"
        out, err = self._prompt_title("p-off")
        self.assertEqual(out, "")
        self.assertEqual(err, "")

    def test_immediate_relabel_takes_the_session(self):
        """_emit_title_to_origin builds the SAME label; two sessions on one task
        must not paint their windows the same string. The TTY write is
        best-effort and unreachable here, so assert on the shared builder the
        helper uses, plus that the helper accepts the session argument."""
        t = self._seed("immediate relabel")
        t = self._attach(t, "i-a")
        t = self._attach(t, "i-b")
        tk = ts.load_task(t["id"])
        la, ln_a = ts.session_title_label(tk, "i-a")
        lb, ln_b = ts.session_title_label(tk, "i-b")
        self.assertNotEqual(la, lb)
        self.assertIsNotNone(ln_a)
        self.assertIsNotNone(ln_b)
        # accepts the session and never raises on the best-effort TTY path
        ts._emit_title_to_origin(tk, "i-a")
        ts._emit_title_to_origin(tk)          # omitted session → fallback, no raise

    def test_label_helper_is_the_single_ln_lookup(self):
        """session_title_label resolves through ordinal_label — the same lookup
        `whoami --porcelain` field 2 serves — so there is exactly one way to
        compute an ln."""
        t = self._seed("one lookup")
        t = self._attach(t, "one")
        tk = ts.load_task(t["id"])
        label, ln = ts.session_title_label(tk, "one")
        self.assertEqual(ln, ts.ordinal_label(tk, "one"))
        self.assertEqual(label, "#%s: %s" % (ln, tk["title"]))


if __name__ == "__main__":
    unittest.main()
