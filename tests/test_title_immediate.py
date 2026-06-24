"""Immediate-title helper (1.14.1): `_emit_title_to_origin` relabels the
originating window `#<seq>: <title>` the instant a task is created / attached /
renamed, instead of waiting for the next UserPromptSubmit. The TTY write itself
is hard to unit-test, so these assert the helper is a SILENT, NON-RAISING no-op
on the best-effort branches (title disabled, no task, unresolvable TTY) and never
touches stdout — plus that a `update --title` RENAME triggers it while an
effort-only update does not."""
import importlib
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

import store        # noqa: E402
import config       # noqa: E402

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


class _Args:
    def __init__(self, **kw):
        self.__dict__.setdefault("title", None)
        self.__dict__.setdefault("summary", None)
        self.__dict__.setdefault("append_summary", None)
        self.__dict__.setdefault("color", None)
        self.__dict__.setdefault("effort", None)
        self.__dict__.update(kw)


class _FakeSub:
    """origin-tty.sh "resolves" to nothing → helper must bail without writing."""
    DEVNULL = subprocess.DEVNULL

    @staticmethod
    def check_output(*a, **k):
        return b"\n"


class ImmediateTitle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        store.reset_cache()
        os.environ.pop("TASK_STATION_TITLE", None)

    def tearDown(self):
        store.reset_cache()
        for k in ("TASK_STATION_HOME", "TASK_STATION_TITLE"):
            os.environ.pop(k, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _emit(self, task):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts._emit_title_to_origin(task)
        return buf.getvalue()

    def _make(self, title="old title"):
        task = ts.new_task(title, "", color="red", status=ts.STATUS_OPEN)
        ts.ensure_seqs()
        task["seq"] = ts._max_seq() + 1
        ts.save_task(task)
        return task

    def test_no_task_is_silent_noop(self):
        self.assertEqual(self._emit(None), "")

    def test_title_disabled_is_silent_noop(self):
        os.environ["TASK_STATION_TITLE"] = "off"
        self.assertEqual(self._emit(self._make()), "")

    def test_unresolvable_tty_is_silent_noop(self):
        saved = ts.subprocess
        ts.subprocess = _FakeSub
        try:
            self.assertEqual(self._emit(self._make()), "")
        finally:
            ts.subprocess = saved

    def test_rename_triggers_emit(self):
        task = self._make("old title")
        seen = []
        saved = ts._emit_title_to_origin
        ts._emit_title_to_origin = lambda t: seen.append(t)
        try:
            ts._update_one(str(task["seq"]), _Args(title="new title"))
        finally:
            ts._emit_title_to_origin = saved
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["title"], "new title")

    def test_effort_only_update_does_not_emit_title(self):
        task = self._make("keep this title")
        seen = []
        saved = ts._emit_title_to_origin
        ts._emit_title_to_origin = lambda t: seen.append(t)
        try:
            ts._update_one(str(task["seq"]), _Args(effort="l"))
        finally:
            ts._emit_title_to_origin = saved
        self.assertEqual(seen, [])


if __name__ == "__main__":
    unittest.main()
