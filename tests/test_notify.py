"""Worker-lifecycle notification dispatch (delegate.notify_event + channels).

No real network / no real banner: the macOS channel is exercised by monkeypatching
subprocess.Popen and forcing sys.platform, and the webhook channel by monkeypatching
urllib.request.urlopen. Config is driven purely through the TASK_STATION_NOTIFY /
TASK_STATION_NOTIFY_WEBHOOK env overrides (which win over the config file), so no
real ~/.claude store is read."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "lib", "delegate"))
import delegate as dg  # noqa: E402


class _FakeResp:
    def close(self):
        pass


class NotifySettingsTest(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("TASK_STATION_NOTIFY", "TASK_STATION_NOTIFY_WEBHOOK", "TASK_STATION_HOME")}
        self._tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self._tmp
        for k in ("TASK_STATION_NOTIFY", "TASK_STATION_NOTIFY_WEBHOOK"):
            os.environ.pop(k, None)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_defaults_off_and_no_webhook(self):
        on, webhook = dg._notify_settings()
        self.assertFalse(on)
        self.assertIsNone(webhook)

    def test_env_enables(self):
        os.environ["TASK_STATION_NOTIFY"] = "on"
        os.environ["TASK_STATION_NOTIFY_WEBHOOK"] = "https://example.test/hook"
        on, webhook = dg._notify_settings()
        self.assertTrue(on)
        self.assertEqual(webhook, "https://example.test/hook")


class OsaQuoteTest(unittest.TestCase):
    def test_escapes_quotes_and_backslashes(self):
        self.assertEqual(dg._osa_quote('a"b'), '"a\\"b"')
        self.assertEqual(dg._osa_quote("a\\b"), '"a\\\\b"')
        self.assertEqual(dg._osa_quote(""), '""')


class MacNotifyTest(unittest.TestCase):
    def test_darwin_finished_spawns_banner(self):
        calls = []
        with mock.patch.object(dg.sys, "platform", "darwin"), \
             mock.patch.object(dg.subprocess, "Popen",
                               lambda *a, **k: calls.append(a[0])):
            dg._macos_notify("worker_finished", "Projectname", "review")
        self.assertEqual(len(calls), 1)
        argv = calls[0]
        self.assertEqual(argv[0], "osascript")
        script = argv[2]
        self.assertIn("Projectname/review: finished", script)
        self.assertIn("task-station worker", script)

    def test_darwin_failed_verb(self):
        calls = []
        with mock.patch.object(dg.sys, "platform", "darwin"), \
             mock.patch.object(dg.subprocess, "Popen",
                               lambda *a, **k: calls.append(a[0])):
            dg._macos_notify("worker_failed", "Projectname", None)
        self.assertIn("Projectname: failed", calls[0][2])   # no label → bare repo

    def test_non_darwin_skips(self):
        calls = []
        with mock.patch.object(dg.sys, "platform", "linux"), \
             mock.patch.object(dg.subprocess, "Popen",
                               lambda *a, **k: calls.append(a[0])):
            dg._macos_notify("worker_finished", "Projectname", "x")
        self.assertEqual(calls, [])

    def test_popen_error_swallowed(self):
        def boom(*a, **k):
            raise OSError("no osascript")
        with mock.patch.object(dg.sys, "platform", "darwin"), \
             mock.patch.object(dg.subprocess, "Popen", boom):
            dg._macos_notify("worker_finished", "Projectname", "x")   # must not raise


class WebhookNotifyTest(unittest.TestCase):
    def test_posts_expected_json(self):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["data"] = json.loads(req.data.decode("utf-8"))
            captured["ctype"] = req.headers.get("Content-type")
            captured["timeout"] = timeout
            return _FakeResp()

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            dg._webhook_notify("https://example.test/hook", "worker_finished",
                               324, "Projectname", "review", "/w/Projectname-worktrees/324", 0.0125)
        self.assertEqual(captured["url"], "https://example.test/hook")
        self.assertEqual(captured["timeout"], 3)
        self.assertEqual(captured["ctype"], "application/json")
        d = captured["data"]
        self.assertEqual(d["event"], "worker_finished")
        self.assertEqual(d["task_seq"], 324)
        self.assertEqual(d["repo"], "Projectname")
        self.assertEqual(d["label"], "review")
        self.assertEqual(d["worktree"], "/w/Projectname-worktrees/324")
        self.assertEqual(d["cost_usd"], 0.0125)
        self.assertIn("ts", d)

    def test_failure_swallowed(self):
        def boom(req, timeout=None):
            raise OSError("connection refused")
        with mock.patch("urllib.request.urlopen", boom):
            # must not raise — a down webhook can't break delegation
            dg._webhook_notify("https://example.test/hook", "worker_failed",
                               1, "Projectname", None, "/w", None)


class NotifyEventDispatchTest(unittest.TestCase):
    """notify_event honours the two config switches independently."""

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("TASK_STATION_NOTIFY", "TASK_STATION_NOTIFY_WEBHOOK", "TASK_STATION_HOME")}
        self._tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self._tmp
        for k in ("TASK_STATION_NOTIFY", "TASK_STATION_NOTIFY_WEBHOOK"):
            os.environ.pop(k, None)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_all_off_dispatches_nothing(self):
        mac, wh = [], []
        with mock.patch.object(dg, "_macos_notify", lambda *a: mac.append(a)), \
             mock.patch.object(dg, "_webhook_notify", lambda *a: wh.append(a)):
            dg.notify_event("worker_finished", 1, "Projectname", None, "/w", 0.1)
        self.assertEqual((mac, wh), ([], []))

    def test_notify_on_fires_macos_only(self):
        os.environ["TASK_STATION_NOTIFY"] = "on"
        mac, wh = [], []
        with mock.patch.object(dg, "_macos_notify", lambda *a: mac.append(a)), \
             mock.patch.object(dg, "_webhook_notify", lambda *a: wh.append(a)):
            dg.notify_event("worker_finished", 1, "Projectname", None, "/w", 0.1)
        self.assertEqual(len(mac), 1)
        self.assertEqual(wh, [])

    def test_webhook_set_fires_webhook_only(self):
        os.environ["TASK_STATION_NOTIFY_WEBHOOK"] = "https://example.test/hook"
        mac, wh = [], []
        with mock.patch.object(dg, "_macos_notify", lambda *a: mac.append(a)), \
             mock.patch.object(dg, "_webhook_notify", lambda *a: wh.append(a)):
            dg.notify_event("worker_failed", 2, "Projectname", "lbl", "/w", None)
        self.assertEqual(mac, [])
        self.assertEqual(len(wh), 1)
        # webhook args forwarded: (url, event, seq, repo, label, worktree, cost)
        self.assertEqual(wh[0][1], "worker_failed")
        self.assertEqual(wh[0][4], "lbl")

    def test_channel_error_does_not_break_dispatch(self):
        os.environ["TASK_STATION_NOTIFY"] = "on"
        os.environ["TASK_STATION_NOTIFY_WEBHOOK"] = "https://example.test/hook"

        def boom(*a, **k):
            raise RuntimeError("boom")
        wh = []
        with mock.patch.object(dg, "_macos_notify", boom), \
             mock.patch.object(dg, "_webhook_notify", lambda *a: wh.append(a)):
            dg.notify_event("worker_finished", 1, "Projectname", None, "/w", 0.1)
        # macOS channel exploded but the webhook still fired
        self.assertEqual(len(wh), 1)


if __name__ == "__main__":
    unittest.main()
