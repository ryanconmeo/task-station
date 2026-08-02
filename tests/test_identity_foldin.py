"""F9 identity-keyed fold-in — nudge filtering + attach soft-guard.

The live bug (memo 7ffa25ca): a PR-1115 session folded into task "Revise PR 1111"
because the fold-don't-fork nudge listed it on shared FLAVOR words. These tests pin
the fix: when the prompt carries an identity key, nudge candidates are limited to
tasks sharing that key; an unmatched key biases create; and `attach` soft-blocks a
key-mismatched fold unless --force-key. Keyless flows are unchanged (the law)."""
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

import store  # noqa: E402

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        os.environ.pop("TASK_STATION_GATE", None)
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.PROJECTS_ROOT = os.path.join(self.tmp, "projects")
        store.reset_cache()

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        os.environ.pop("TASK_STATION_PROMPT", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self, title, summary=""):
        t = ts.new_task(title, summary)
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])


class NudgeFilter(_Base):
    def _nudge(self, session, prompt=""):
        os.environ["TASK_STATION_PROMPT"] = prompt
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_prompt_context(_Args(session=session))
        return buf.getvalue()

    def test_keyed_prompt_excludes_flavor_only_candidate(self):
        self._seed("Revise PR 1111")        # pr:1111 — different identity
        self._seed("Auto-review PR 1115")   # pr:1115 — the match
        self._seed("Add dark mode")         # keyless
        out = self._nudge("s1", "please review PR 1115")
        self.assertIn("Auto-review PR 1115", out)     # same-key candidate shown
        self.assertNotIn("Revise PR 1111", out)       # flavor-only PR 1111 excluded
        self.assertNotIn("Add dark mode", out)        # keyless task excluded
        self.assertIn("→ keys: PR 1115", out)         # candidate renders its own keys

    def test_unmatched_key_biases_create(self):
        self._seed("Revise PR 1111")
        self._seed("Auto-review PR 1115")
        out = self._nudge("s1", "start on PR 9999")
        self.assertIn("No open task carries PR 9999", out)
        self.assertIn("create a task", out)
        self.assertNotIn("Revise PR 1111", out)
        self.assertNotIn("Auto-review PR 1115", out)

    def test_keyless_prompt_lists_all_and_renders_keys(self):
        self._seed("Revise PR 1111")
        self._seed("Add dark mode")
        out = self._nudge("s1", "how does the theme system work?")
        self.assertIn("Revise PR 1111", out)
        self.assertIn("Add dark mode", out)
        self.assertIn("→ keys: PR 1111", out)         # keyed task shows its key...
        # ...and the keyless task's line has NO key suffix.
        line = next(l for l in out.splitlines() if "Add dark mode" in l)
        self.assertNotIn("→ keys:", line)

    def test_keyless_everywhere_is_unchanged(self):
        # The law: no keys on prompt OR any task ⇒ the pre-F9 block, byte-for-byte.
        t = self._seed("Add dark mode")
        out = self._nudge("s1", "how does the theme system work?")
        self.assertNotIn("→ keys:", out)
        self.assertNotIn("No open task carries", out)
        self.assertIn("  - #%s [%s] Add dark mode (" % (t.get("seq") or "?", t["id"][:8]), out)


class AttachGuard(_Base):
    def _attach(self, session, task_ref, **kw):
        buf = io.StringIO()
        exited = None
        with redirect_stdout(buf):
            try:
                ts.cmd_attach(_Args(session=session, task=task_ref, color=None, **kw))
            except SystemExit as e:
                exited = e.code
        return buf.getvalue(), exited

    def test_mismatch_blocks_and_exits_nonzero(self):
        t = self._seed("Revise PR 1111")   # pr:1111
        out, code = self._attach("sess", str(t["seq"]), note="please review PR 1115")
        self.assertEqual(code, 1)
        self.assertIn("key mismatch", out)
        self.assertIn("PR 1115", out)      # prompt's key
        self.assertIn("PR 1111", out)      # task's key
        self.assertIn("--force-key", out)
        self.assertNotIn("📋 Attached", out)      # did not attach
        self.assertIsNone(ts.get_link("sess"))   # NOT attached

    def test_force_key_overrides_mismatch(self):
        t = self._seed("Revise PR 1111")
        out, code = self._attach("sess", str(t["seq"]),
                                 note="please review PR 1115", force_key=True)
        self.assertIsNone(code)
        self.assertIn("📋 Attached", out)
        self.assertEqual(ts.get_link("sess"), t["id"])

    def test_matching_keys_pass_silently(self):
        t = self._seed("Revise PR 1111")
        out, code = self._attach("sess", str(t["seq"]), note="more on PR 1111 here")
        self.assertIsNone(code)
        self.assertNotIn("key mismatch", out)
        self.assertIn("📋 Attached", out)
        self.assertEqual(ts.get_link("sess"), t["id"])

    def test_keyless_note_on_keyed_task_proceeds(self):
        t = self._seed("Revise PR 1111")
        out, code = self._attach("sess", str(t["seq"]), note="a quick follow-up question")
        self.assertIsNone(code)
        self.assertNotIn("key mismatch", out)
        self.assertEqual(ts.get_link("sess"), t["id"])

    def test_keyed_note_on_keyless_task_proceeds(self):
        t = self._seed("Add dark mode")   # keyless task
        out, code = self._attach("sess", str(t["seq"]), note="also see PR 1115")
        self.assertIsNone(code)
        self.assertNotIn("key mismatch", out)
        self.assertEqual(ts.get_link("sess"), t["id"])

    def test_keyless_both_sides_proceeds(self):
        # The law: keyless on both sides ⇒ attaches exactly as pre-F9.
        t = self._seed("Add dark mode")
        out, code = self._attach("sess", str(t["seq"]), note=None)
        self.assertIsNone(code)
        self.assertNotIn("key mismatch", out)
        self.assertEqual(ts.get_link("sess"), t["id"])


class AutoTrackGuard(_Base):
    """guaranteed-tracking auto-fold: a flavor-similar OPEN task must NOT be a fold
    target when the prompt names a different PR — it creates a sibling instead.

    Titles here share a trailing number (…release 2024) so the candidate PASSES the
    pre-existing untyped _norm_nums guard in similar_open_task — this isolates F9's
    TYPED contribution (pr:1115 ≠ pr:1111) as the thing that blocks the fold."""

    def test_auto_fold_skips_key_mismatch(self):
        existing = self._seed("Review PR 1111 for release 2024")
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts._auto_track_provisional(_Args(session="sess"), "Review PR 1115 for release 2024")
        out = buf.getvalue()
        # Did NOT fold into the PR-1111 task...
        self.assertNotIn("folded into open task", out)
        link = ts.get_link("sess")
        self.assertIsNotNone(link)
        self.assertNotEqual(link, existing["id"])   # a fresh sibling, not the mismatch

    def test_auto_fold_matching_key_folds(self):
        existing = self._seed("Review PR 1111 for release 2024")
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts._auto_track_provisional(_Args(session="sess"), "more on PR 1111 for release 2024")
        self.assertEqual(ts.get_link("sess"), existing["id"])   # folded into the match


if __name__ == "__main__":
    unittest.main()
