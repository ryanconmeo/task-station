""""STATION X IS AT REV Y" IS THE WHOLE RELAY PROTOCOL, AND IT IS A FILE.

After a sync each station writes the content revision of its own partition into
`rev.json` inside that partition. A subscriber compares the revs it can see against the
ones it has already pulled and syncs only when they differ. There is no daemon, no
socket and no service to run: git already delivers bytes between machines, already has
auth and already works offline, so the DURABLE part is built here — the rev, the
seen-ledger, the changed-detection — and delivery stays adopted.

Three tiers, and the floor is always available: `sync` (manual, always works),
`sync --if-changed` (the cheap poll a hook or timer calls), and a push relay feeding
this same rev signal, which is not built and is not needed for correctness.

`BoardMakesNoNetworkCallsTest` greps the RENDERED page, not the renderer's source. A
docstring mentioning `<script>` made an earlier probe of this pass while the board was
broken, which is the whole argument for scanning the artifact.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(_REPO_ROOT, "lib")
sys.path.insert(0, LIB)

_TMP_HOME = tempfile.mkdtemp(prefix="ts-relay-")
os.environ["TASK_STATION_HOME"] = _TMP_HOME

import sync                                                             # noqa: E402

CLI = os.path.join(LIB, "task-station.py")

# The APIs that actually reach the network. An <a href> is navigation the user chose,
# not a call the page made, so it is deliberately not in this list.
NETWORK_APIS = ("fetch(", "XMLHttpRequest", "WebSocket", "EventSource", "sendBeacon")


class RevTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-relay-rev-")
        self.part = os.path.join(self.tmp, "p")
        os.makedirs(os.path.join(self.part, sync.TASKS_DIR))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, body):
        with open(os.path.join(self.part, sync.TASKS_DIR, name), "w") as f:
            f.write(body)

    def test_an_EMPTY_partition_has_no_rev_at_all(self):
        """Not the hash of nothing. A station that has published nothing has nothing to
        pull, and reporting it as moved is how a cadence hook learns to be ignored."""
        self.assertEqual(sync.partition_rev(self.part), "")

    def test_the_rev_changes_when_the_data_changes(self):
        self._write("a.json", '{"task": {}}')
        first = sync.partition_rev(self.part)
        self._write("a.json", '{"task": {"title": "x"}}')
        self.assertNotEqual(sync.partition_rev(self.part), first)

    def test_the_rev_is_stable_when_nothing_changes(self):
        self._write("a.json", '{"task": {}}')
        self.assertEqual(sync.partition_rev(self.part), sync.partition_rev(self.part))

    def test_a_tombstone_moves_the_rev_too(self):
        self._write("a.json", '{"task": {}}')
        first = sync.partition_rev(self.part)
        self._write("a.tombstone", '{"deleted_ts": 1}')
        self.assertNotEqual(sync.partition_rev(self.part), first)

    def test_a_missing_rev_file_falls_back_to_computing_one(self):
        """A peer that has not published a ping is still readable — a missing ping must
        never be read as "nothing changed"."""
        self._write("a.json", '{"task": {}}')
        self.assertEqual(sync.read_partition_rev(self.part),
                         sync.partition_rev(self.part))

    def test_a_published_rev_file_is_preferred(self):
        self._write("a.json", '{"task": {}}')
        with open(os.path.join(self.part, sync.REV_FILE), "w") as f:
            json.dump({"rev": "deadbeef"}, f)
        self.assertEqual(sync.read_partition_rev(self.part), "deadbeef")


class _TwoStations(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-relay-two-")
        self.a = os.path.join(self.tmp, "a")
        self.b = os.path.join(self.tmp, "b")
        self.ex = os.path.join(self.tmp, "ex")
        os.makedirs(self.a)
        os.makedirs(self.b)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, home, number, *args):
        env = dict(os.environ)
        env.update({"TASK_STATION_HOME": home, "CLAUDE_CONFIG_DIR": home,
                    "XDG_STATE_HOME": home, "TASK_STATION_SELF_ALIAS": "kosei",
                    "TASK_STATION_STATION": str(number),
                    "TASK_STATION_SYNC_DIR": self.ex})
        r = subprocess.run([sys.executable, CLI] + list(args), capture_output=True,
                           text=True, env=env, timeout=180)
        return r.returncode, (r.stdout or "") + (r.stderr or "")

    def A(self, *args):
        return self._run(self.a, 0, *args)

    def B(self, *args):
        return self._run(self.b, 1, *args)


class CheckTest(_TwoStations):
    def test_a_fresh_exchange_reports_nothing_moved_and_exits_3(self):
        self.A("sync", "--init")
        self.B("sync", "--init")
        code, out = self.B("sync", "--check")
        self.assertEqual(code, 3)
        self.assertIn("nothing moved", out)

    def test_a_peers_sync_makes_it_show_as_moved(self):
        self.A("sync", "--init")
        self.B("sync", "--init")
        self.A("create", "--title", "RELAYPROBE", "--no-attach")
        self.A("sync")
        code, out = self.B("sync", "--check")
        self.assertEqual(code, 0)
        self.assertIn("changed: kosei/station-0", out)
        self.assertIn("last pulled never", out)

    def test_check_RECORDS_NOTHING_so_the_next_real_sync_still_does_the_work(self):
        """A check that marked things seen would make the sync after it skip work it
        never did."""
        self.A("sync", "--init")
        self.B("sync", "--init")
        self.A("create", "--title", "RELAYPROBE", "--no-attach")
        self.A("sync")
        self.B("sync", "--check")
        code, out = self.B("sync", "--check")
        self.assertEqual(code, 0)
        self.assertIn("changed:", out)

    def test_if_changed_pulls_once_and_then_goes_quiet(self):
        self.A("sync", "--init")
        self.B("sync", "--init")
        self.A("create", "--title", "RELAYPROBE", "--no-attach")
        self.A("sync")
        _, out = self.B("sync", "--if-changed")
        self.assertIn("1 created", out)
        _, second = self.B("sync", "--if-changed")
        self.assertIn("nothing moved", second)
        self.assertEqual(self.B("sync", "--check")[0], 3)

    def test_a_task_really_does_cross_on_the_pull(self):
        self.A("sync", "--init")
        self.B("sync", "--init")
        self.A("create", "--title", "RELAYPROBE", "--no-attach")
        self.A("sync")
        self.B("sync", "--if-changed")
        self.assertIn("RELAYPROBE", self.B("search", "RELAYPROBE")[1])

    def test_a_SECOND_change_reopens_the_ping(self):
        self.A("sync", "--init")
        self.B("sync", "--init")
        self.A("create", "--title", "RELAYPROBE", "--no-attach")
        self.A("sync")
        self.B("sync", "--if-changed")
        self.A("create", "--title", "RELAYPROBE2", "--no-attach")
        self.A("sync")
        code, out = self.B("sync", "--check")
        self.assertEqual(code, 0)
        self.assertIn("changed:", out)

    def test_a_sync_publishes_this_stations_rev(self):
        self.A("sync", "--init")
        self.A("create", "--title", "RELAYPROBE", "--no-attach")
        self.A("sync")
        path = os.path.join(self.ex, "owners", "kosei", "station-0", sync.REV_FILE)
        self.assertTrue(os.path.exists(path))
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        self.assertTrue(doc["rev"])
        self.assertEqual(doc["station"], 0)

    def test_the_seen_ledger_is_LOCAL_and_never_enters_the_exchange(self):
        """It says what THIS machine has pulled, so it is not shared state."""
        self.A("sync", "--init")
        self.A("create", "--title", "RELAYPROBE", "--no-attach")
        self.A("sync")
        for base, dirs, names in os.walk(self.ex):
            dirs[:] = [d for d in dirs if d != ".git"]
            self.assertNotIn(sync.SEEN_FILE, names)
        self.assertTrue(os.path.exists(os.path.join(self.a, sync.SEEN_FILE)))


class BoardMakesNoNetworkCallsTest(unittest.TestCase):
    """The board is a static file:// page and must stay one. Asserted against the
    RENDERED artifact — a mutation to the renderer's docstring once satisfied a
    source-level version of this check while the page itself was broken."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-relay-board-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _render(self):
        env = dict(os.environ)
        env.update({"TASK_STATION_HOME": self.tmp, "CLAUDE_CONFIG_DIR": self.tmp,
                    "XDG_STATE_HOME": self.tmp})
        subprocess.run([sys.executable, CLI, "create", "--title", "BOARDPROBE",
                        "--no-attach"], capture_output=True, env=env, timeout=180)
        subprocess.run([sys.executable, CLI, "board"], capture_output=True, env=env,
                       timeout=300)
        path = os.path.join(self.tmp, "board.html")
        self.assertTrue(os.path.exists(path), "board.html was not rendered")
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_the_rendered_board_calls_no_network_api(self):
        html = self._render()
        self.assertIn("BOARDPROBE", html)          # it really did render the board
        for api in NETWORK_APIS:
            self.assertNotIn(api, html, api)


if __name__ == "__main__":
    unittest.main()
