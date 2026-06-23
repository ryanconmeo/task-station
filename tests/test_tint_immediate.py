"""Immediate-tint helper (1.12.0): `_emit_tint_to_origin` paints the originating
window the instant a colour is assigned (create/attach/recategorize) instead of
waiting for the next prompt. The TTY write itself is hard to unit-test, so these
assert the helper is a SILENT, NON-RAISING no-op on the best-effort branches —
tint disabled, no colour, and an unresolvable origin TTY — and never touches
stdout (which carries the model-visible command result)."""
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
import categories   # noqa: E402

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


class _FakeSub:
    """Stands in for the subprocess module: origin-tty.sh "resolves" to nothing."""
    DEVNULL = subprocess.DEVNULL

    @staticmethod
    def check_output(*a, **k):
        return b"\n"   # empty after .strip() → helper must bail without writing


class ImmediateTint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        store.reset_cache()
        os.environ["TASK_STATION_TERM"] = "iterm"     # deterministic escape
        os.environ.pop("TASK_STATION_TINT", None)
        importlib.reload(categories)                  # ensure TINT_TERMINAL True
        config.set("tint_theme", "dark")

    def tearDown(self):
        store.reset_cache()
        for k in ("TASK_STATION_HOME", "TASK_STATION_TERM", "TASK_STATION_TINT"):
            os.environ.pop(k, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _call(self, color):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts._emit_tint_to_origin(color)
        return buf.getvalue()

    def test_no_color_is_silent_noop(self):
        self.assertEqual(self._call(None), "")

    def test_tint_disabled_is_silent_noop(self):
        os.environ["TASK_STATION_TINT"] = "off"
        self.assertEqual(self._call("red"), "")

    def test_unresolvable_tty_is_silent_noop(self):
        """Tint on, real colour, but origin-tty.sh resolves nothing → no write,
        no raise, nothing on stdout."""
        saved = ts.subprocess
        ts.subprocess = _FakeSub
        try:
            self.assertEqual(self._call("red"), "")
        finally:
            ts.subprocess = saved

    def test_never_writes_escape_to_stdout(self):
        """Even when a colour resolves to a real escape, the bytes must go to the
        TTY rail, never stdout — assert stdout stays empty on the no-TTY path."""
        saved = ts.subprocess
        ts.subprocess = _FakeSub
        try:
            esc = categories.tint_escape("red", config.tint_mode(), "iterm")
            self.assertNotEqual(esc, "")     # sanity: there IS an escape to leak
            self.assertEqual(self._call("red"), "")
        finally:
            ts.subprocess = saved


if __name__ == "__main__":
    unittest.main()
