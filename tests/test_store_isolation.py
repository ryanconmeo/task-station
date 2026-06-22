"""Regression guard: the test harness must keep paths.data_dir() OUT of the real
~/.claude store even when TASK_STATION_HOME is popped from the environment.

Several test tearDowns do os.environ.pop('TASK_STATION_HOME'); if only that var
were pinned, data_dir() would fall through to CLAUDE_CONFIG_DIR/XDG and reach the
real ~/.claude/task-station-data, leaking fixture tasks. tests/__init__.py +
conftest.py pin all three resolution inputs to one throwaway tmp dir; this locks
that in."""
import os
import sys
import tempfile
import unittest

# Belt-and-suspenders isolation that works for EVERY runner. tests/__init__.py and
# conftest.py pin the store, but `python3 -m unittest discover -s tests` (no -t .)
# in Python 3.9 imports test modules top-level and SKIPS the package __init__, so
# its pins never apply. unittest imports every test_*.py at collection — before any
# test runs — so pinning here (module import time) covers the bare-discover command
# too. setdefault → an explicit CI/dev override still wins; idempotent with the
# package/conftest pins. All three inputs because tearDowns pop TASK_STATION_HOME.
_tsd = tempfile.mkdtemp(prefix="task-station-tests-")
os.environ.setdefault("TASK_STATION_HOME", _tsd)
os.environ.setdefault("CLAUDE_CONFIG_DIR", _tsd)
os.environ.setdefault("XDG_STATE_HOME", _tsd)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import paths


class StoreIsolationGuard(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("TASK_STATION_HOME", "CLAUDE_CONFIG_DIR", "XDG_STATE_HOME")}

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_harness_pins_fallback_inputs_to_tmp(self):
        # The package __init__ / conftest pins the FALLBACK inputs
        # (CLAUDE_CONFIG_DIR, XDG_STATE_HOME) to a tmp dir, and — crucially — those
        # are the ones that must hold the line: the leaking tearDowns pop only
        # TASK_STATION_HOME, so by the time arbitrary later tests run it may already
        # be gone (we deliberately do NOT assert it's still set). The fallbacks must
        # be present and must not point at the real ~/.claude.
        real = os.path.expanduser("~/.claude")
        for k in ("CLAUDE_CONFIG_DIR", "XDG_STATE_HOME"):
            self.assertIsNotNone(os.environ.get(k),
                                 "%s should be pinned by the test harness" % k)
            self.assertFalse(os.environ[k] == real or os.environ[k].startswith(real + os.sep),
                             "%s must not point at the real ~/.claude" % k)

    def test_popped_task_station_home_does_not_leak_to_real_store(self):
        # The exact failure mode: a tearDown pops TASK_STATION_HOME, so data_dir()
        # falls through to the CLAUDE_CONFIG_DIR/XDG fallbacks. With the harness
        # pins in place, that fallback must NOT be the real store.
        os.environ.pop("TASK_STATION_HOME", None)
        dd = paths.data_dir()
        real = os.path.expanduser("~/.claude")
        self.assertFalse(dd == real or dd.startswith(real + os.sep),
                         "data_dir() leaked to the real store after TASK_STATION_HOME pop: %s" % dd)

    def test_popped_all_but_xdg_still_safe(self):
        # Even if TASK_STATION_HOME and CLAUDE_CONFIG_DIR are both gone, the XDG pin
        # keeps data_dir() in the tmp dir, never ~/.claude.
        os.environ.pop("TASK_STATION_HOME", None)
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
        dd = paths.data_dir()
        real = os.path.expanduser("~/.claude")
        self.assertFalse(dd == real or dd.startswith(real + os.sep),
                         "data_dir() leaked to the real store with only XDG pinned: %s" % dd)


if __name__ == "__main__":
    unittest.main()
