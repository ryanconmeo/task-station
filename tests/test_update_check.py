import os, sys, json, time, tempfile, shutil, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import config
import paths
import update_check


def _installed():
    return update_check._parse_semver(update_check._load_plugin().get("version"))


class UpdateCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); os.environ["TASK_STATION_HOME"] = self.tmp
        # Tripwire: any network probe during these tests fails loudly.
        self._orig_fetch = update_check._fetch_latest
        def _no_net(repo):
            raise AssertionError("network call attempted in test (git ls-remote)")
        update_check._fetch_latest = _no_net

    def tearDown(self):
        update_check._fetch_latest = self._orig_fetch
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed_cache(self, latest, checked_at=None):
        if checked_at is None:
            checked_at = int(time.time())
        with open(os.path.join(paths.data_dir(), update_check.CACHE_NAME), "w") as f:
            json.dump({"checked_at": checked_at, "latest": latest}, f)

    def test_off_returns_empty_and_no_network(self):
        # Default off: returns "" before any subprocess — tripwire would fire otherwise.
        self.assertFalse(config.update_check_enabled())
        self.assertEqual(update_check.nudge_line(), "")

    def test_on_with_newer_cached_version_nudges(self):
        config.set("update_check", True)
        maj, minor, patch = _installed()
        newer = "%d.%d.%d" % (maj, minor, patch + 1)
        self._seed_cache(newer)
        line = update_check.nudge_line()
        self.assertIn(newer, line)
        self.assertIn("/plugin update task-station@ryanconmeo", line)

    def test_on_with_same_version_no_nudge(self):
        config.set("update_check", True)
        self._seed_cache("%d.%d.%d" % _installed())
        self.assertEqual(update_check.nudge_line(), "")

    def test_on_with_older_version_no_nudge(self):
        config.set("update_check", True)
        maj, minor, patch = _installed()
        older = "%d.%d.%d" % (maj, minor, max(0, patch - 1)) if patch else "%d.%d.%d" % (max(0, maj - 1), minor, patch)
        self._seed_cache(older)
        self.assertEqual(update_check.nudge_line(), "")


if __name__ == "__main__":
    unittest.main()
