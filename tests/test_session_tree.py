"""WS3: session TREE (hubs classified main/side-quest, workers nested under their
spawning hub) + task-to-task relation edges, surfaced on the `/todo <n>` detail.

Exercises the data helpers `session_tree` / `related_edges` and the `_format_detail`
rendering (Sessions block + Related line), plus graceful degradation on bare tasks.
Uses per-test temp-home isolation with real (tiny) transcripts under a repointed
PROJECTS_ROOT and a repointed delegate registry."""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


class SessionTreeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        self.proj = os.path.join(self.tmp, "projects")
        os.makedirs(self.proj, exist_ok=True)
        ts.PROJECTS_ROOT = self.proj
        ts.DELEGATE_REGISTRY = os.path.join(self.tmp, "workers.json")
        ts.store.reset_cache()

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        ts.store.reset_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers ---------------------------------------------------------------
    def _seed(self, title, **fields):
        t = ts.new_task(title, "summary for " + title)
        ts.save_task(t)
        ts.ensure_seqs()
        t = ts.load_task(t["id"])
        for k, v in fields.items():
            t[k] = v
        ts.save_task(t)
        return ts.load_task(t["id"])

    def _transcript(self, sid, nmsgs, cwd="/work/repo"):
        """Write a real transcript with `nmsgs` counted user messages so the shared
        _find_session_path / _session_msgcount liveness code runs for real. The file
        lives inside a project bucket subdir (`<PROJECTS_ROOT>/<cwd-encoded>/`), which
        is where _find_session_path searches — not directly under PROJECTS_ROOT."""
        bucket = os.path.join(self.proj, cwd.replace("/", "-"))
        os.makedirs(bucket, exist_ok=True)
        path = os.path.join(bucket, sid + ".jsonl")
        with open(path, "w") as f:
            for _ in range(nmsgs):
                f.write(json.dumps({"cwd": cwd,
                                    "message": {"role": "user",
                                                "content": "real work here"}}) + "\n")
        return path

    def _hub(self, task, sid, cwd="/work/repo", ts_val=None, preborn=False):
        m = {"cwd": cwd, "ts": ts_val if ts_val is not None else ts._now(), "role": "hub"}
        if preborn:
            m["preborn"] = True
        task.setdefault("session_meta", {})[sid] = m
        if sid not in task.setdefault("sessions", []):
            task["sessions"].append(sid)

    def _registry(self, entries):
        with open(ts.DELEGATE_REGISTRY, "w") as f:
            json.dump(entries, f)

    # -- session_tree ----------------------------------------------------------
    def test_session_tree_classifies_main_and_nests_workers(self):
        t = self._seed("Tree")
        now = ts._now()
        self._hub(t, "hub-main", cwd="/work/main", ts_val=now)
        self._hub(t, "hub-stale", cwd="/work/stale", ts_val=now - 100000)
        t["pinned_session"] = "hub-main"
        ts.save_task(t)
        self._transcript("hub-main", 5, cwd="/work/main")   # live + substantive
        # hub-stale has NO transcript → gone
        self._registry({
            "%d:claude-todo:ws1" % t["seq"]: {
                "project": "claude-todo", "seq": str(t["seq"]), "label": "ws1",
                "dir": "/w/ws1", "session_id": "wk-sid", "ts": now,
                "model": "opus", "spawner": "hub-main"},
            "%d:otherrepo" % t["seq"]: {
                "project": "otherrepo", "seq": str(t["seq"]), "label": None,
                "dir": "/w/other", "session_id": "wk-orphan", "ts": now,
                "model": "sonnet", "spawner": "ghost-hub"},   # spawner is no known hub
        })
        tree = ts.session_tree(ts.load_task(t["id"]))
        # Pinned hub sorts first (newest ts) and is BOTH pinned and main.
        self.assertEqual(tree["hubs"][0]["sid"], "hub-main")
        self.assertTrue(tree["hubs"][0]["pinned"] and tree["hubs"][0]["main"])
        self.assertTrue(tree["hubs"][0]["live"])
        self.assertEqual(tree["hubs"][0]["workers"][0]["sid"], "wk-sid")
        self.assertEqual([w["sid"] for w in tree["orphan_workers"]], ["wk-orphan"])
        # The stale hub is a side-quest and gone.
        stale = [h for h in tree["hubs"] if h["sid"] == "hub-stale"][0]
        self.assertFalse(stale["main"])
        self.assertFalse(stale["live"])
        self.assertEqual(stale["msgs"], 0)

    def test_session_tree_main_falls_back_to_newest_live_when_unpinned(self):
        t = self._seed("NoPin")
        now = ts._now()
        self._hub(t, "hub-old", cwd="/o", ts_val=now - 5000)
        self._hub(t, "hub-new", cwd="/n", ts_val=now)
        ts.save_task(t)
        self._transcript("hub-old", ts.SUBSTANCE_FLOOR + 2, cwd="/o")
        self._transcript("hub-new", ts.SUBSTANCE_FLOOR + 2, cwd="/n")
        tree = ts.session_tree(ts.load_task(t["id"]))
        main = [h for h in tree["hubs"] if h["main"]]
        self.assertEqual(len(main), 1)
        self.assertEqual(main[0]["sid"], "hub-new")   # newest live substantive wins

    def test_session_tree_empty_for_bare_task(self):
        t = self._seed("Bare")
        tree = ts.session_tree(t)
        self.assertEqual(tree["hubs"], [])
        self.assertEqual(tree["orphan_workers"], [])

    # -- related_edges ---------------------------------------------------------
    def test_related_edges_bidirectional(self):
        parent = self._seed("Parent")
        child = self._seed("Child")
        child["related"] = [{"id": parent["id"], "seq": parent["seq"],
                             "kind": "spawned-from", "ts": ts._now()}]
        ts.save_task(child)
        scan = [ts.load_task(parent["id"]), ts.load_task(child["id"])]
        edges = ts.related_edges(ts.load_task(parent["id"]), tasks=scan)
        self.assertEqual(len(edges["in"]), 1)
        self.assertEqual(edges["in"][0]["seq"], child["seq"])
        self.assertEqual(edges["in"][0]["kind"], "spawned-from")
        self.assertEqual(edges["in"][0]["status"],
                         ts.task_status(ts.load_task(child["id"])))
        self.assertEqual(edges["out"], [])
        # The child sees the mirror out-edge to the parent.
        cedges = ts.related_edges(ts.load_task(child["id"]), tasks=scan)
        self.assertEqual(cedges["out"][0]["seq"], parent["seq"])
        self.assertEqual(cedges["out"][0]["kind"], "spawned-from")
        self.assertEqual(cedges["in"], [])

    def test_related_edges_default_scan_uses_all_tasks(self):
        parent = self._seed("P")
        child = self._seed("C")
        child["related"] = [{"id": parent["id"], "seq": parent["seq"],
                             "kind": "related", "ts": ts._now()}]
        ts.save_task(child)
        edges = ts.related_edges(ts.load_task(parent["id"]))   # tasks=None → all_tasks()
        self.assertEqual([e["seq"] for e in edges["in"]], [child["seq"]])

    # -- detail render ---------------------------------------------------------
    def test_detail_renders_sessions_and_related_and_degrades(self):
        parent = self._seed("Parent")
        t = self._seed("Rich")
        now = ts._now()
        self._hub(t, "hub-a", cwd="/work/rich", ts_val=now)
        t["related"] = [{"id": parent["id"], "seq": parent["seq"],
                         "kind": "spawned-from", "ts": now}]
        ts.save_task(t)
        self._transcript("hub-a", 4, cwd="/work/rich")
        self._registry({
            "%d:claude-todo:ws1" % t["seq"]: {
                "project": "claude-todo", "seq": str(t["seq"]), "label": "ws1",
                "dir": "/w/ws1", "session_id": "wk-sid", "ts": now,
                "model": "opus", "spawner": "hub-a"}})
        out = ts._format_detail(ts.load_task(t["id"]), "cur-sid")
        self.assertIn("Sessions:", out)
        self.assertIn("↳", out)                       # nested worker marker
        self.assertIn("worker (claude-todo:ws1)", out)
        self.assertIn("Related:", out)
        self.assertIn("from #%s (spawned-from)" % parent["seq"], out)
        # Bare task: neither block appears.
        bare = ts._format_detail(ts.new_task("bare", ""), "cur-sid")
        self.assertNotIn("Sessions:", bare)
        self.assertNotIn("Related:", bare)

    def test_detail_shows_reverse_spawned_edge(self):
        parent = self._seed("Parent")
        child = self._seed("Child")
        child["related"] = [{"id": parent["id"], "seq": parent["seq"],
                             "kind": "spawned-from", "ts": ts._now()}]
        ts.save_task(child)
        out = ts._format_detail(ts.load_task(parent["id"]), "cur")
        self.assertIn("Related:", out)
        self.assertIn("spawned #%s" % child["seq"], out)

    def test_related_line_marks_closed_target(self):
        parent = self._seed("Parent")
        child = self._seed("Child")
        child["status"] = "closed"
        child["related"] = [{"id": parent["id"], "seq": parent["seq"],
                             "kind": "spawned-from", "ts": ts._now()}]
        ts.save_task(child)
        line = ts._related_line(ts.load_task(parent["id"]))
        self.assertIn("spawned #%s ✕" % child["seq"], line)

    def test_orphan_worker_listed_unnested_when_spawner_absent(self):
        t = self._seed("Orphans")
        now = ts._now()
        self._hub(t, "hub-a", cwd="/work", ts_val=now)
        ts.save_task(t)
        self._transcript("hub-a", 4, cwd="/work")
        self._registry({
            "%d:repo" % t["seq"]: {
                "project": "repo", "seq": str(t["seq"]), "label": None,
                "dir": "/w/r", "session_id": "wk-x", "ts": now,
                "model": "sonnet"}})   # NO spawner field
        tree = ts.session_tree(ts.load_task(t["id"]))
        self.assertEqual(tree["hubs"][0]["workers"], [])
        self.assertEqual([w["sid"] for w in tree["orphan_workers"]], ["wk-x"])
        out = ts._format_detail(ts.load_task(t["id"]), "cur")
        self.assertIn("(unlinked)", out)


if __name__ == "__main__":
    unittest.main()
