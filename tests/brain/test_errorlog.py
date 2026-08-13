"""The size-capped swallowed-exception log (``brain.errorlog``).

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 3) from the brain source tree's
``tests/test_errorlog.py`` @ 0.14.0 — three cases 1:1, plus one ADDED case for
the log's LOCATION, which moved with chunk 1's D2 (the state dir is now anchored
to the task-station data home instead of an org-branded ``~/.claude`` path).
"""
import os
import unittest

from tests.brain.base import BrainTestCase

import brain.errorlog as errorlog


class ErrorLogTest(BrainTestCase):
    def test_record_writes_one_line(self):
        errorlog.record("here", ValueError("boom: with colon\nand newline"))
        text = errorlog.error_log_path().read_text()
        self.assertEqual(len(text.strip().splitlines()), 1)  # newlines flattened
        self.assertIn("here", text)
        self.assertIn("ValueError", text)
        self.assertIn("boom: with colon | and newline", text)

    def test_record_never_raises(self):
        # a bogus argument must not propagate out of the logger
        class Bad:
            def __str__(self):
                raise RuntimeError("nope")
        try:
            errorlog.record("x", Bad())
        except Exception as e:  # pragma: no cover
            self.fail(f"errorlog.record raised: {e}")

    def test_truncates_to_half_when_oversized(self):
        p = errorlog.error_log_path()
        # 400 KB of lines, over the 256 KB cap
        p.write_text("".join(f"line-{i}\n" for i in range(50000)))
        self.assertGreater(p.stat().st_size, errorlog.MAX_BYTES)
        errorlog.record("trigger", ValueError("x"))
        self.assertLessEqual(p.stat().st_size, errorlog.MAX_BYTES)
        # the most recent content is retained, oldest dropped
        text = p.read_text()
        self.assertIn("trigger", text)
        self.assertNotIn("line-0\n", text)


class LogLocationTest(BrainTestCase):
    """ADDED — the log follows the CONFIGURED state dir.

    The source hard-coded an org-branded ``~/.claude/<orgbrand>-state``; here the
    state dir resolves through ``brain.config`` (chunk-1 D2), so a relocated
    state dir must take the error log with it. Without this, a wrong anchor
    writes breadcrumbs somewhere nobody looks and nothing fails.
    """

    def test_default_log_is_under_the_data_home_state_dir(self):
        self.assertEqual(errorlog.error_log_path(),
                         self.data_home() / "brain-state" / "error.log")

    def test_record_follows_a_relocated_state_dir(self):
        elsewhere = self.home / "somewhere-else"
        os.environ["TASK_STATION_BRAIN_STATE"] = str(elsewhere)
        errorlog.record("relocated", ValueError("x"))
        self.assertEqual(errorlog.error_log_path(), elsewhere / "error.log")
        self.assertIn("relocated", (elsewhere / "error.log").read_text())
        self.assertFalse((self.data_home() / "brain-state" / "error.log").exists())


if __name__ == "__main__":
    unittest.main()
