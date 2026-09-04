"""3.64.0: the plugin cache prunes itself, and what it refuses to delete.

`/plugin update` ADDS an install directory and never removes one. On the authoring
machine that reached 36 versions and 325 MB while installed_plugins.json registered
exactly one of them. board/plugincache.py prunes, inside the activate flow — a
DETACHED SessionStart step, right after the hook re-points the engine symlink.

The interesting half of a deleter is what it will not delete, so that is what these
tests are about: the registered version, the running version, the symlink target,
the rollback window, and any version a LIVE process still marks in `.in_use/`. A
stale marker must NOT pin a version forever, or the cache never shrinks again.

Stdlib-only unittest, no LLM, never touches the real cache.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO_ROOT, "lib"))

from board import plugincache  # noqa: E402


class _Cache:
    """A throwaway ~/.claude with a plugin cache in it."""

    def __init__(self, versions, registered=None, owner="acme", plugin="widget"):
        self.root = tempfile.mkdtemp(prefix="ts-cache-")
        self.cache = os.path.join(self.root, "plugins", "cache", owner, plugin)
        for v in versions:
            os.makedirs(os.path.join(self.cache, v))
        doc = {"version": 2, "plugins": {}}
        if registered:
            doc["plugins"]["%s@%s" % (plugin, owner)] = [
                {"scope": "user", "version": registered,
                 "installPath": os.path.join(self.cache, registered)}]
        os.makedirs(os.path.join(self.root, "plugins"), exist_ok=True)
        with open(os.path.join(self.root, "plugins", "installed_plugins.json"),
                  "w", encoding="utf-8") as fh:
            json.dump(doc, fh)

    def version(self, v):
        return os.path.join(self.cache, v)

    def mark(self, v, pid):
        d = os.path.join(self.cache, v, ".in_use")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, str(pid)), "w", encoding="utf-8") as fh:
            fh.write('{"pid": %d}' % pid)

    def names(self):
        return sorted(n for n in os.listdir(self.cache))

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


class PruneTest(unittest.TestCase):
    VERSIONS = ["3.1.0", "3.2.0", "3.3.0", "3.4.0", "3.5.0", "3.10.0"]

    def _cache(self, **kw):
        c = _Cache(self.VERSIONS, **kw)
        self.addCleanup(c.close)
        return c

    def _plan(self, c, active, **kw):
        kw.setdefault("engine_link", os.path.join(c.root, "no-such-link"))
        return plugincache.plan(c.version(active), config_dir=c.root, **kw)

    def test_it_keeps_the_active_version_and_two_rollbacks(self):
        c = self._cache(registered="3.10.0")
        _dir, keep, doomed = self._plan(c, "3.10.0")
        self.assertEqual(sorted(keep), ["3.10.0", "3.4.0", "3.5.0"])
        self.assertEqual(sorted(doomed), ["3.1.0", "3.2.0", "3.3.0"])

    def test_versions_sort_by_semver_not_by_string(self):
        """"3.10.0" < "3.2.0" as text. A prune that believed that would delete the
        newest release and keep two ancient ones."""
        c = self._cache(registered="3.1.0")
        _dir, keep, _doomed = self._plan(c, "3.1.0")
        self.assertIn("3.10.0", keep)
        self.assertIn("3.5.0", keep)

    def test_the_registered_version_is_never_deleted(self):
        """Even when it is old enough to fall outside the rollback window."""
        c = self._cache(registered="3.1.0")
        _dir, keep, doomed = self._plan(c, "3.10.0")
        self.assertIn("3.1.0", keep)
        self.assertNotIn("3.1.0", doomed)

    def test_a_live_in_use_marker_pins_its_version(self):
        c = self._cache(registered="3.10.0")
        c.mark("3.1.0", os.getpid())
        _dir, keep, doomed = self._plan(c, "3.10.0")
        self.assertIn("3.1.0", keep)
        self.assertNotIn("3.1.0", doomed)

    def test_a_stale_in_use_marker_does_not_pin_anything(self):
        """Every dead session leaves its marker behind. If those counted, the cache
        would be pinned by its own history and could never shrink."""
        c = self._cache(registered="3.10.0")
        c.mark("3.1.0", 999999999)           # a pid that cannot be running
        _dir, _keep, doomed = self._plan(c, "3.10.0")
        self.assertIn("3.1.0", doomed)

    def test_the_engine_symlink_target_is_pinned(self):
        c = self._cache(registered="3.10.0")
        link = os.path.join(c.root, "task-station-engine")
        os.symlink(os.path.join(c.version("3.1.0"), "lib"), link)
        _dir, keep, doomed = plugincache.plan(
            c.version("3.10.0"), config_dir=c.root, engine_link=link)
        self.assertIn("3.1.0", keep)
        self.assertNotIn("3.1.0", doomed)

    def test_a_non_release_directory_is_never_touched(self):
        c = self._cache(registered="3.10.0")
        os.makedirs(os.path.join(c.cache, "scratch"))
        _dir, keep, doomed = self._plan(c, "3.10.0")
        self.assertNotIn("scratch", doomed)
        self.assertNotIn("scratch", keep)

    def test_keep_zero_still_keeps_what_is_pinned(self):
        c = self._cache(registered="3.10.0")
        _dir, keep, doomed = self._plan(c, "3.10.0", keep=0)
        self.assertEqual(sorted(keep), ["3.10.0"])
        self.assertNotIn("3.10.0", doomed)

    def test_prune_actually_removes_and_leaves_the_rest(self):
        c = self._cache(registered="3.10.0")
        removed = plugincache.prune(
            c.version("3.10.0"), config_dir=c.root,
            engine_link=os.path.join(c.root, "no-such-link"))
        self.assertEqual(sorted(removed), ["3.1.0", "3.2.0", "3.3.0"])
        self.assertEqual(c.names(), ["3.10.0", "3.4.0", "3.5.0"])

    def test_a_dry_run_deletes_nothing(self):
        c = self._cache(registered="3.10.0")
        before = c.names()
        plugincache.prune(c.version("3.10.0"), config_dir=c.root, dry_run=True,
                          engine_link=os.path.join(c.root, "no-such-link"))
        self.assertEqual(c.names(), before)

    def test_a_second_run_is_a_no_op(self):
        c = self._cache(registered="3.10.0")
        link = os.path.join(c.root, "no-such-link")
        plugincache.prune(c.version("3.10.0"), config_dir=c.root, engine_link=link)
        self.assertEqual(plugincache.prune(c.version("3.10.0"), config_dir=c.root,
                                           engine_link=link), [])

    def test_an_unreadable_registry_still_keeps_the_running_version(self):
        c = self._cache(registered=None)
        _dir, keep, doomed = self._plan(c, "3.5.0")
        self.assertIn("3.5.0", keep)
        self.assertNotIn("3.5.0", doomed)

    def test_the_retention_number_is_a_named_constant(self):
        self.assertEqual(plugincache.KEEP_ROLLBACKS, 2)


if __name__ == "__main__":
    unittest.main()
