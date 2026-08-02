"""live_session_count(task): counts only sessions whose link STILL resolves to
the task (append-only `sessions` over-reports), and the ` ⧉N` list marker /
`Live sessions:` detail line that surface it.

Plus (WS5) the process-state live-session viewer: lib/live_sessions.running()
over ~/.claude/sessions/<PID>.json + pid liveness, the `sessions` CLI, the
/todo detail annotations, and the MCP list_sessions tool."""
import importlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

# task-station.py has a hyphen, so it can't be a normal import — load it by path.
_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)

import live_sessions          # clean module name — WS5 process-state viewer
import store as store_mod


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class LiveSessionCountTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        # Repoint the module's import-frozen path globals at this test's tmpdir
        # so each test gets a pristine, isolated store.
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self, title):
        t = ts.new_task(title, "summary")
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def test_counts_only_live_links(self):
        a = self._seed("Alpha")
        b = self._seed("Beta")
        # Two sessions touch A (recorded in sessions[] and linked to A).
        a.setdefault("sessions", []).extend(["s1", "s2"])
        ts.save_task(a)
        ts.set_link("s1", a["id"])
        ts.set_link("s2", a["id"])
        self.assertEqual(ts.live_session_count(ts.load_task(a["id"])), 2)

        # s2 re-attaches elsewhere: it stays in A.sessions (append-only) but its
        # link now points at B, so A's live count drops to 1.
        ts.set_link("s2", b["id"])
        self.assertEqual(ts.live_session_count(ts.load_task(a["id"])), 1)

        # s1 detaches entirely → 0 live, even though sessions[] still lists both.
        ts.clear_link("s1")
        reloaded = ts.load_task(a["id"])
        self.assertEqual(ts.live_session_count(reloaded), 0)
        self.assertEqual(len(reloaded["sessions"]), 2)  # append-only, unchanged

    def test_no_sessions_is_zero(self):
        a = self._seed("Lonely")
        self.assertEqual(ts.live_session_count(a), 0)

    def test_list_marker_only_when_more_than_one(self):
        a = self._seed("Multi")
        b = self._seed("Single")
        a.setdefault("sessions", []).extend(["x1", "x2"])
        ts.save_task(a)
        ts.set_link("x1", a["id"])
        ts.set_link("x2", a["id"])
        b.setdefault("sessions", []).append("y1")
        ts.save_task(b)
        ts.set_link("y1", b["id"])
        out = ts._format_list()
        self.assertIn("⧉2", out)          # the multi-session task is marked
        # The single-session task line carries no marker.
        single_line = [ln for ln in out.splitlines() if "Single" in ln][0]
        self.assertNotIn("⧉", single_line)

    def test_detail_shows_live_sessions_line(self):
        a = self._seed("Detailed")
        a.setdefault("sessions", []).extend(["d1", "d2"])
        ts.save_task(a)
        ts.set_link("d1", a["id"])
        # d2 is stale (never linked) — live should read 1, total 2.
        detail = ts._format_detail(ts.load_task(a["id"]), "d1")
        self.assertIn("Live sessions: 1", detail)
        self.assertIn("of 2 ever attached", detail)


class DetachTest(unittest.TestCase):
    """cmd_detach resolves its target task (the parenthesized
    `(resolve_ref(a.task) or load_task(a.task)) if a.task else None`) and drops the
    session from the task's resume candidates."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self, title):
        t = ts.new_task(title, "summary")
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def test_detach_by_task_ref(self):
        # --task given → resolve it by seq, detach the session from THAT task.
        a = self._seed("Detachable")
        a.setdefault("sessions", []).append("sX")
        ts.save_task(a)
        ts.set_link("sX", a["id"])
        with redirect_stdout(io.StringIO()):
            ts.cmd_detach(_Args(session="sX", task=str(a["seq"])))
        reloaded = ts.load_task(a["id"])
        self.assertNotIn("sX", reloaded.get("sessions", []))
        self.assertIsNone(ts.get_link("sX"))

    def test_detach_without_task_uses_linked(self):
        # No --task → fall back to the session's currently-linked task.
        a = self._seed("Linked")
        a.setdefault("sessions", []).append("sY")
        ts.save_task(a)
        ts.set_link("sY", a["id"])
        with redirect_stdout(io.StringIO()):
            ts.cmd_detach(_Args(session="sY", task=None))
        self.assertNotIn("sY", ts.load_task(a["id"]).get("sessions", []))

    def test_detach_nothing_attached_is_graceful(self):
        a = self._seed("Untouched")
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_detach(_Args(session="ghost", task=str(a["seq"])))
        self.assertIn("nothing to detach", buf.getvalue())


class _LiveBase(unittest.TestCase):
    """Shared fixture for the WS5 process-state viewer tests: an isolated store +
    a fake ~/.claude/sessions dir (env-overridden), with pid liveness stubbed so
    tests never depend on real running processes."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        self.sessions = os.path.join(self.tmp, "sessions")
        os.makedirs(self.sessions, exist_ok=True)
        os.environ["TASK_STATION_SESSIONS_DIR"] = self.sessions
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        self._orig_projects_root = ts.PROJECTS_ROOT
        ts.PROJECTS_ROOT = os.path.join(self.tmp, "projects")
        store_mod.reset_cache()
        # Fake liveness: only pids in this set are "alive". running() calls the
        # module-level name, so patching the attribute is enough.
        self.alive = set()
        self._real_alive = live_sessions.pid_alive
        live_sessions.pid_alive = lambda pid: pid in self.alive

    def tearDown(self):
        live_sessions.pid_alive = self._real_alive
        ts.PROJECTS_ROOT = self._orig_projects_root
        store_mod.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        os.environ.pop("TASK_STATION_SESSIONS_DIR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_session(self, name, **fields):
        with open(os.path.join(self.sessions, name), "w") as f:
            json.dump(fields, f)

    def _seed(self, title):
        t = ts.new_task(title, "summary")
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])


class LiveSessionsRunningTest(_LiveBase):
    def test_filters_dead_pids_and_never_deletes(self):
        self._write_session("101.json", pid=101, sessionId="s-alive",
                            cwd="/tmp/work", kind="hub", status="busy",
                            updatedAt=1_700_000_000)
        self._write_session("102.json", pid=102, sessionId="s-dead",
                            cwd="/tmp/other", kind="hub", status="idle",
                            updatedAt=1_700_000_100)
        self.alive = {101}                     # 102 is a crashed/stale leftover
        rows = live_sessions.running()
        self.assertEqual([r["session_id"] for r in rows], ["s-alive"])
        # The stale file is TOLERATED, never removed.
        self.assertTrue(os.path.exists(os.path.join(self.sessions, "102.json")))

    def test_joins_link_to_task(self):
        a = self._seed("Alpha")
        ts.set_link("s-live", a["id"])
        self._write_session("200.json", pid=200, sessionId="s-live",
                            cwd="/tmp/work", kind="hub", status="busy",
                            updatedAt=1_700_000_000)
        self.alive = {200}
        (row,) = live_sessions.running()
        self.assertEqual(row["task_seq"], a["seq"])
        self.assertEqual(row["task_title"], "Alpha")
        self.assertEqual(row["role"], "hub")
        self.assertEqual(row["status"], "busy")
        self.assertEqual(row["resume_command"],
                         "cd /tmp/work && claude --resume s-live")

    def test_unlinked_session_has_no_task(self):
        self._write_session("201.json", pid=201, sessionId="orphan",
                            cwd="/tmp/x", entrypoint="cli", status="idle",
                            updatedAt=1)
        self.alive = {201}
        (row,) = live_sessions.running()
        self.assertIsNone(row["task_seq"])
        self.assertIsNone(row["task_title"])
        self.assertEqual(row["role"], "hub")          # entrypoint cli → hub

    def test_worker_label_from_registry(self):
        # A delegate worker: role from entrypoint sdk-cli, label from workers.json.
        with open(os.path.join(self.tmp, "workers.json"), "w") as f:
            json.dump({"360:myrepo:api": {"project": "myrepo", "seq": 360,
                                          "label": "api", "dir": "/repo",
                                          "session_id": "w-1"}}, f)
        self._write_session("300.json", pid=300, sessionId="w-1", cwd="/repo",
                            entrypoint="sdk-cli", status="busy", updatedAt=5)
        self.alive = {300}
        (row,) = live_sessions.running()
        self.assertEqual(row["role"], "worker")
        self.assertEqual(row["label"], "api")

    def test_malformed_json_is_skipped(self):
        with open(os.path.join(self.sessions, "bad.json"), "w") as f:
            f.write("{not valid json")
        self._write_session("400.json", pid=400, sessionId="ok", cwd="/tmp",
                            kind="hub", status="idle", updatedAt=1)
        self.alive = {400}
        rows = live_sessions.running()
        self.assertEqual([r["session_id"] for r in rows], ["ok"])

    def test_missing_sessions_dir_is_empty(self):
        shutil.rmtree(self.sessions)
        self.assertEqual(live_sessions.running(), [])

    def test_sorted_most_recent_first(self):
        self._write_session("1.json", pid=1, sessionId="old", cwd="/a",
                            kind="hub", status="idle", updatedAt=100)
        self._write_session("2.json", pid=2, sessionId="new", cwd="/b",
                            kind="hub", status="busy", updatedAt=999)
        self.alive = {1, 2}
        rows = live_sessions.running()
        self.assertEqual([r["session_id"] for r in rows], ["new", "old"])

    def test_iso_timestamp_parsed(self):
        self._write_session("5.json", pid=5, sessionId="iso", cwd="/a",
                            kind="hub", status="busy",
                            updatedAt="2026-07-04T12:00:00Z")
        self.alive = {5}
        (row,) = live_sessions.running()
        self.assertIsInstance(row["updated_ts"], float)


class PidAliveTest(unittest.TestCase):
    def test_own_process_is_alive(self):
        self.assertTrue(live_sessions.pid_alive(os.getpid()))

    def test_bogus_pid_is_dead(self):
        self.assertFalse(live_sessions.pid_alive(2_000_000_000))

    def test_non_int_and_zero_are_dead(self):
        self.assertFalse(live_sessions.pid_alive(None))
        self.assertFalse(live_sessions.pid_alive("nope"))
        self.assertFalse(live_sessions.pid_alive(0))


class SessionsCliTest(_LiveBase):
    def test_table_lists_live_rows(self):
        a = self._seed("Alpha")
        ts.set_link("s-live", a["id"])
        self._write_session("200.json", pid=18521696, sessionId="s-live",
                            cwd=os.path.expanduser("~"), kind="hub", status="busy",
                            updatedAt=ts._now())
        self.alive = {18521696}
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_sessions(_Args(task=None, as_json=False))
        out = buf.getvalue()
        self.assertIn("● 18521696", out)
        self.assertIn("task %d" % a["seq"], out)
        self.assertIn("busy", out)
        self.assertIn("claude --resume s-live", out)
        self.assertIn(" ~ ", out)              # cwd tildified to ~

    def test_json_output(self):
        self._write_session("1.json", pid=7, sessionId="j", cwd="/x",
                            kind="hub", status="idle", updatedAt=1)
        self.alive = {7}
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_sessions(_Args(task=None, as_json=True))
        data = json.loads(buf.getvalue())
        self.assertEqual(data[0]["session_id"], "j")

    def test_task_filter(self):
        a = self._seed("Alpha")
        b = self._seed("Beta")
        ts.set_link("sa", a["id"])
        ts.set_link("sb", b["id"])
        self._write_session("1.json", pid=1, sessionId="sa", cwd="/a",
                            kind="hub", status="busy", updatedAt=1)
        self._write_session("2.json", pid=2, sessionId="sb", cwd="/b",
                            kind="hub", status="busy", updatedAt=2)
        self.alive = {1, 2}
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_sessions(_Args(task=str(a["seq"]), as_json=True))
        data = json.loads(buf.getvalue())
        self.assertEqual([r["session_id"] for r in data], ["sa"])

    def test_empty_message(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_sessions(_Args(task=None, as_json=False))
        self.assertIn("No live Claude sessions", buf.getvalue())


class DetailAnnotationTest(_LiveBase):
    def _write_transcript(self, sid, cwd):
        """A minimal findable transcript so _resume_target resolves `sid`."""
        bucket = os.path.join(ts.PROJECTS_ROOT, "bucket")
        os.makedirs(bucket, exist_ok=True)
        with open(os.path.join(bucket, sid + ".jsonl"), "w") as f:
            for _ in range(ts.SUBSTANCE_FLOOR + 1):
                f.write(json.dumps({"cwd": cwd,
                                    "message": {"role": "user",
                                                "content": "real work here"}}) + "\n")

    def test_hub_resume_line_gets_live_marker(self):
        a = self._seed("Detailed")
        sid = "hub-sid"
        cwd = "/tmp/work"
        a["sessions"] = [sid]
        a["session_meta"] = {sid: {"cwd": cwd, "ts": ts._now(), "role": "hub"}}
        ts.save_task(a)
        ts.set_link(sid, a["id"])
        self._write_transcript(sid, cwd)
        self._write_session("500.json", pid=500, sessionId=sid, cwd=cwd,
                            kind="hub", status="busy", updatedAt=ts._now())
        self.alive = {500}
        detail = ts._format_detail(ts.load_task(a["id"]), None)
        self.assertIn("--resume %s" % sid, detail)
        # The live marker rides the Hub resume line.
        hub_line = [ln for ln in detail.splitlines() if "Hub" in ln and "--resume" in ln][0]
        self.assertIn("● busy", hub_line)

    def test_worker_line_gets_live_marker(self):
        a = self._seed("WithWorker")
        a["projects"] = ["myrepo"]
        ts.save_task(a)
        ts.DELEGATE_REGISTRY = os.path.join(self.tmp, "workers.json")
        with open(os.path.join(self.tmp, "workers.json"), "w") as f:
            json.dump({"%d:myrepo" % a["seq"]: {"project": "myrepo",
                        "seq": a["seq"], "label": None, "dir": "/repo",
                        "session_id": "w-9"}}, f)
        self._write_session("600.json", pid=600, sessionId="w-9", cwd="/repo",
                            entrypoint="sdk-cli", status="idle", updatedAt=ts._now())
        self.alive = {600}
        live = ts._live_session_index()
        lines = ts.worker_lines(ts.load_task(a["id"]), live)
        joined = "\n".join(lines)
        self.assertIn("--resume w-9", joined)
        self.assertIn("● idle", joined)

    def test_live_note_empty_for_missing_row(self):
        self.assertEqual(ts._live_note(None), "")


class McpListSessionsTest(_LiveBase):
    def setUp(self):
        super().setUp()
        sys.modules.pop("mcp_server", None)
        self.mcp = importlib.import_module("mcp_server")
        # Point the engine the bridge drives at this test's throwaway store.
        eng = self.mcp._engine()
        eng.DATA = self.tmp
        eng.STORE = ts.STORE
        eng.PROJECTS_ROOT = ts.PROJECTS_ROOT

    def test_list_sessions_markdown(self):
        a = self._seed("Bridged")
        ts.set_link("m-1", a["id"])
        self._write_session("700.json", pid=700, sessionId="m-1", cwd="/w",
                            kind="hub", status="busy", updatedAt=ts._now())
        self.alive = {700}
        md = self.mcp._list_sessions()
        self.assertIn("Live Claude sessions", md)
        self.assertIn("task #%s" % a["seq"], md)
        self.assertIn("Bridged", md)
        self.assertIn("cd /w && claude --resume m-1", md)

    def test_list_sessions_empty(self):
        self.assertIn("No live Claude sessions", self.mcp._list_sessions())

    def test_list_sessions_tool_registered(self):
        self.assertIn("list_sessions", self.mcp._TOOLS_BY_NAME)
        out = self.mcp._tool_list_sessions({})
        self.assertIsInstance(out, str)


if __name__ == "__main__":
    unittest.main()
