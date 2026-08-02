"""Read-only native-Tasks interop (lib/native_tasks.py) + the `adopt` round-trip.

The native store (~/.claude/tasks/<list-uuid>/<n>.json) is repointed at a tmp dir
via TASK_STATION_NATIVE_TASKS_DIR so nothing here touches a real store. Covers the
recency filter (dir mtime / open-item rule), malformed-file tolerance, ref
resolution, and `task-station adopt --native` creating a durable station task
WITHOUT ever writing the native store."""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)
import native_tasks  # noqa: E402

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


def _write_list(root, uuid, items, mtime=None):
    """Create <root>/<uuid>/ with one <id>.json per item dict; optionally backdate
    the dir mtime so the recency filter can be exercised."""
    d = os.path.join(root, uuid)
    os.makedirs(d, exist_ok=True)
    for it in items:
        with open(os.path.join(d, "%s.json" % it["id"]), "w") as f:
            json.dump(it, f)
    if mtime is not None:
        os.utime(d, (mtime, mtime))
    return d


def _item(i, subject="do it", status="pending", description=""):
    return {"id": str(i), "subject": subject, "description": description, "status": status}


class NativeListParsingTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="native-tasks-")
        os.environ["TASK_STATION_NATIVE_TASKS_DIR"] = self.root

    def tearDown(self):
        os.environ.pop("TASK_STATION_NATIVE_TASKS_DIR", None)
        shutil.rmtree(self.root, ignore_errors=True)

    def test_empty_root_returns_empty(self):
        self.assertEqual(native_tasks.list_native_lists(), [])

    def test_missing_root_returns_empty(self):
        shutil.rmtree(self.root, ignore_errors=True)
        self.assertEqual(native_tasks.list_native_lists(), [])

    def test_basic_list_parsed_and_sorted(self):
        _write_list(self.root, "aaaa1111bbbb", [
            _item(2, "second"), _item(10, "tenth"), _item(1, "first")])
        lists = native_tasks.list_native_lists()
        self.assertEqual(len(lists), 1)
        L = lists[0]
        self.assertEqual(L["uuid"], "aaaa1111bbbb")
        self.assertEqual(L["short"], "aaaa1111")
        # numeric ids sort ascending (1, 2, 10 — not lexical 1, 10, 2)
        self.assertEqual([it["id"] for it in L["items"]], ["1", "2", "10"])

    def test_open_count(self):
        _write_list(self.root, "list1", [
            _item(1, status="completed"), _item(2, status="pending"),
            _item(3, status="in_progress")])
        L = native_tasks.list_native_lists()[0]
        self.assertEqual(L["open_count"], 2)

    def test_recency_stale_all_completed_dropped(self):
        old = time.time() - 30 * 86400
        _write_list(self.root, "stale", [_item(1, status="completed")], mtime=old)
        self.assertEqual(native_tasks.list_native_lists(), [])

    def test_recency_stale_but_open_item_kept(self):
        old = time.time() - 30 * 86400
        _write_list(self.root, "stale-open", [_item(1, status="pending")], mtime=old)
        lists = native_tasks.list_native_lists()
        self.assertEqual(len(lists), 1)
        self.assertEqual(lists[0]["uuid"], "stale-open")

    def test_recency_recent_all_completed_kept(self):
        _write_list(self.root, "fresh-done", [_item(1, status="completed")])
        self.assertEqual(len(native_tasks.list_native_lists()), 1)

    def test_empty_list_dir_omitted(self):
        os.makedirs(os.path.join(self.root, "no-items"))
        self.assertEqual(native_tasks.list_native_lists(), [])

    def test_malformed_files_tolerated(self):
        d = _write_list(self.root, "mixed", [_item(1, "good")])
        with open(os.path.join(d, "2.json"), "w") as f:
            f.write("{ this is not json ")
        with open(os.path.join(d, "3.json"), "w") as f:
            f.write("[1,2,3]")   # valid JSON, wrong (non-dict) shape
        with open(os.path.join(d, "notjson.txt"), "w") as f:
            f.write("ignored")
        L = native_tasks.list_native_lists()[0]
        self.assertEqual([it["id"] for it in L["items"]], ["1"])

    def test_missing_keys_defaulted(self):
        d = os.path.join(self.root, "sparse")
        os.makedirs(d)
        with open(os.path.join(d, "1.json"), "w") as f:
            json.dump({"id": 1}, f)   # id as int, no subject/description/status
        L = native_tasks.list_native_lists()[0]
        it = L["items"][0]
        self.assertEqual(it["id"], "1")
        self.assertEqual(it["subject"], "")
        self.assertEqual(it["status"], "pending")   # missing status → open default

    def test_newest_list_first(self):
        _write_list(self.root, "older", [_item(1, status="pending")],
                    mtime=time.time() - 3600)
        _write_list(self.root, "newer", [_item(1, status="pending")])
        self.assertEqual([L["uuid"] for L in native_tasks.list_native_lists()],
                         ["newer", "older"])

    def test_explicit_root_arg_overrides_env(self):
        other = tempfile.mkdtemp(prefix="native-other-")
        try:
            _write_list(other, "only-here", [_item(1, status="pending")])
            lists = native_tasks.list_native_lists(root=other)
            self.assertEqual([L["uuid"] for L in lists], ["only-here"])
        finally:
            shutil.rmtree(other, ignore_errors=True)


class FindNativeItemTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="native-tasks-")
        os.environ["TASK_STATION_NATIVE_TASKS_DIR"] = self.root
        _write_list(self.root, "abcdef123456", [
            _item(1, "alpha", description="do alpha"), _item(2, "beta", status="pending")])

    def tearDown(self):
        os.environ.pop("TASK_STATION_NATIVE_TASKS_DIR", None)
        shutil.rmtree(self.root, ignore_errors=True)

    def test_resolve_by_prefix(self):
        L, item = native_tasks.find_native_item("abcdef:1")
        self.assertIsNotNone(item)
        self.assertEqual(item["subject"], "alpha")
        self.assertEqual(L["uuid"], "abcdef123456")

    def test_resolve_by_full_uuid(self):
        L, item = native_tasks.find_native_item("abcdef123456:2")
        self.assertEqual(item["subject"], "beta")

    def test_unknown_id_returns_none(self):
        self.assertEqual(native_tasks.find_native_item("abcdef:99"), (None, None))

    def test_unknown_prefix_returns_none(self):
        self.assertEqual(native_tasks.find_native_item("zzzz:1"), (None, None))

    def test_malformed_ref_returns_none(self):
        self.assertEqual(native_tasks.find_native_item("no-colon"), (None, None))
        self.assertEqual(native_tasks.find_native_item(""), (None, None))
        self.assertEqual(native_tasks.find_native_item(":1"), (None, None))


class AdoptRoundTripTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.store.reset_cache()
        self.native_root = tempfile.mkdtemp(prefix="native-tasks-")
        os.environ["TASK_STATION_NATIVE_TASKS_DIR"] = self.native_root

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        os.environ.pop("TASK_STATION_NATIVE_TASKS_DIR", None)
        ts.store.reset_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.native_root, ignore_errors=True)

    def _adopt(self, ref):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_adopt(type("A", (), {"native": ref})())
        return buf.getvalue()

    def test_adopt_creates_station_task_from_native(self):
        _write_list(self.native_root, "deadbeef0001", [
            _item(1, "Wire the widget", description="Connect A to B", status="pending")])
        out = self._adopt("deadbeef:1")
        self.assertIn("Adopted native task", out)
        tasks = ts.all_tasks()
        self.assertEqual(len(tasks), 1)
        t = tasks[0]
        self.assertEqual(t["title"], "Wire the widget")
        # description + provenance land in the summary
        self.assertIn("Connect A to B", t["summary"])
        self.assertIn("adopted from native task deadbeef:1", t["summary"])
        # defaults: GENERAL (black) colour, small effort
        self.assertEqual(t.get("effort"), "S")
        # provenance is also logged as activity
        self.assertTrue(any("adopted from native task" in e.get("note", "")
                            for e in t.get("log", [])))
        # adopt is a durable create -> it emits exactly one task.created event.
        import stream
        created = [e for e in stream.read_events() if e["event"] == "task.created"]
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["task"]["uuid"], t["uuid"])
        self.assertEqual(created[0]["data"]["title"], "Wire the widget")

    def test_adopt_does_not_write_native_store(self):
        d = _write_list(self.native_root, "cafe00001111", [
            _item(1, "Keep me", status="pending")])
        before = sorted(os.listdir(d))
        self._adopt("cafe:1")
        self.assertEqual(sorted(os.listdir(d)), before)   # native store untouched

    def test_adopt_unknown_ref_reports_and_creates_nothing(self):
        _write_list(self.native_root, "aaaa11112222", [_item(1, "x", status="pending")])
        out = self._adopt("nope:9")
        self.assertIn("No native task matching", out)
        self.assertEqual(ts.all_tasks(), [])

    def test_adopt_no_description_still_records_provenance(self):
        _write_list(self.native_root, "bbbb22223333", [
            _item(1, "No desc", description="", status="in_progress")])
        self._adopt("bbbb:1")
        t = ts.all_tasks()[0]
        # Provenance records the canonical 8-char list prefix, not whatever
        # shorter ref the caller happened to type.
        self.assertIn("adopted from native task bbbb2222:1", t["summary"])


if __name__ == "__main__":
    unittest.main()
