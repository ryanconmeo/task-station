"""Task 444 A1 — worker truth (B1–B4).

B1 liveness truth: the agents list emits TWO row shapes (interactive rows carry
`status`+`pid`; `kind: background` rows carry `state` and NO pid — 444-17 ground
truth, 40/42 rows). worker_status must read both; the --bg poll loop must fail
FAST on a parked state with a diagnosis line instead of manufacturing freshness
by touching the heartbeat on every poll; status renders a parked bg worker as
STALLED, never "running".

B2 trust + grant preflight: verify/repair the ~/.claude.json trust entry before
every launch, ALERT on a wipe, and probe the effective grant set once.

B3 durable child report: the worker's final report becomes a worktree-root
HANDOFF-REPORT file — worker-authored honored, else harvested from the stdout
result or the transcript tail, so backgrounding can never lose it.

B4 parked-agent reaper: `delegate reap-parked` sweeps task-station bg agents
parked in a stalled state; the task-close reaper no longer requires a pid.

No real `claude`, no real processes: fake adapters, repointed module globals.
"""
import io
import json
import os
import shutil
import sys
import tempfile
import time
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)
sys.path.insert(0, os.path.join(LIB, "delegate"))

import delegate as dg  # noqa: E402
import harness  # noqa: E402


BG_ROW = {"sessionId": "bg-full-sid", "state": "blocked", "kind": "background",
          "name": "task-station-444-proj", "cwd": "/tmp/x",
          "startedAt": 1755100000000, "id": "bg-full-"}
INTERACTIVE_ROW = {"sessionId": "hub-sid", "status": "busy", "pid": 4242,
                   "kind": "interactive", "name": "hub", "cwd": "/tmp/y",
                   "startedAt": 1755100000000}


class WorkerStatusShapeTest(unittest.TestCase):
    """B1 root cause: worker_status read only `status`, so every background row
    (which carries `state`) fell through to the 'running' default."""

    def _adapter(self, rows):
        ad = harness.ClaudeAdapter()
        ad.agents_index = lambda cwd=None: {r["sessionId"]: r for r in rows}
        return ad

    def test_bg_row_state_key_is_read(self):
        st = self._adapter([BG_ROW]).worker_status("bg-full-sid")
        self.assertEqual(st["state"], "blocked")
        self.assertIsNone(st["pid"])
        self.assertEqual(st["kind"], "background")

    def test_interactive_row_status_key_still_wins(self):
        st = self._adapter([INTERACTIVE_ROW]).worker_status("hub-sid")
        self.assertEqual(st["state"], "busy")
        self.assertEqual(st["pid"], 4242)

    def test_unlisted_is_gone(self):
        st = self._adapter([BG_ROW]).worker_status("nope")
        self.assertEqual(st["state"], "gone")


class ClassifyExitBgTest(unittest.TestCase):
    def test_blocked_is_stalled(self):
        self.assertEqual(dg._classify_exit_bg("blocked", False), ("stalled", True))

    def test_needs_input_is_stalled(self):
        self.assertEqual(dg._classify_exit_bg("needs-input", False), ("stalled", True))

    def test_idle_is_ok_and_timeout_wins(self):
        self.assertEqual(dg._classify_exit_bg("idle", False), ("ok", False))
        self.assertEqual(dg._classify_exit_bg("idle", True), ("timeout", True))

    def test_gone_is_crash(self):
        self.assertEqual(dg._classify_exit_bg("gone", False), ("crash", True))


class _ScriptedAdapter:
    """Scripted worker_status sequence; the last state sticks."""
    def __init__(self, states):
        self.states = list(states)

    def spawn_worker(self, brief, worktree, **kw):
        return "sid-bg-1"

    def worker_status(self, wid):
        s = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return {"state": s, "pid": None, "kind": "background", "raw": None}


class RunWorkerBgStallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-truth-")
        self.saved = (dg.REG_DIR, dg.REG, dg.PROJECTS_ROOT)
        dg.REG_DIR = self.tmp
        dg.REG = os.path.join(self.tmp, "workers.json")
        dg.PROJECTS_ROOT = os.path.join(self.tmp, "projects")
        os.makedirs(dg.PROJECTS_ROOT)

    def tearDown(self):
        dg.REG_DIR, dg.REG, dg.PROJECTS_ROOT = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, ad, **kw):
        err = io.StringIO()
        with redirect_stderr(err):
            out = dg.run_worker_bg(ad, self.tmp, "brief", poll_secs=0.01, **kw)
        return out, err.getvalue()

    def test_blocked_fails_fast_with_diagnosis(self):
        (sid, state, to), err = self._run(_ScriptedAdapter(["blocked"]),
                                          stall_grace=0)
        self.assertEqual((sid, state, to), ("sid-bg-1", "blocked", False))
        self.assertIn("PARKED", err)
        self.assertIn("'blocked'", err)
        self.assertIn("transcript ABSENT", err)         # no transcript was seeded

    def test_diagnosis_names_existing_transcript(self):
        bucket = os.path.join(dg.PROJECTS_ROOT, "b1")
        os.makedirs(bucket)
        tpath = os.path.join(bucket, "sid-bg-1.jsonl")
        with open(tpath, "w") as f:
            f.write("{}\n")
        _, err = self._run(_ScriptedAdapter(["blocked"]), stall_grace=0)
        self.assertIn("transcript exists", err)
        self.assertIn(tpath, err)

    def test_recovered_state_resets_the_grace(self):
        # blocked → busy → idle must complete OK: a transient parked state during
        # init is not a stall.
        (sid, state, to), err = self._run(
            _ScriptedAdapter(["blocked", "busy", "idle"]), stall_grace=3600)
        self.assertEqual((sid, state, to), ("sid-bg-1", "idle", False))
        self.assertNotIn("PARKED", err)

    def test_parked_polls_do_not_touch_last_event_ts(self):
        # THE 444-17 lie: every poll refreshed last_event_ts, so a blocked worker
        # rendered "running (quiet 0s)" for hours. Seed an entry, run a stalled
        # loop, and require last_event_ts untouched while agent_state tells truth.
        dg.save_reg({"k": {"seq": 444, "session_id": "sid-bg-1",
                           "last_event_ts": 123, "bg": True}})
        self._run(_ScriptedAdapter(["blocked"]), key="k", stall_grace=0)
        e = dg.load_reg()["k"]
        self.assertEqual(e["last_event_ts"], 123)
        self.assertEqual(e["agent_state"], "blocked")

    def test_progress_polls_do_touch_last_event_ts(self):
        dg.save_reg({"k": {"seq": 444, "session_id": "sid-bg-1",
                           "last_event_ts": 123, "bg": True}})
        self._run(_ScriptedAdapter(["busy", "idle"]), key="k")
        e = dg.load_reg()["k"]
        self.assertGreater(e["last_event_ts"], 123)
        self.assertEqual(e["agent_state"], "idle")


class LivenessBgTruthTest(unittest.TestCase):
    def test_stalled_bg_entry_is_named_stalled(self):
        e = {"bg": True, "agent_state": "blocked", "session_id": "s",
             "last_event_ts": 100, "ts": 100, "pid": None}
        glyph, text = dg._liveness(e, now=200)
        self.assertEqual(glyph, "○")
        self.assertIn("STALLED", text)
        self.assertIn("'blocked'", text)

    def test_live_state_probe_overrides_recorded_heartbeat(self):
        # Entry froze at 'busy' when its polling hub died; the live probe says
        # blocked NOW — the probe must win.
        e = {"bg": True, "agent_state": "busy", "session_id": "s",
             "last_event_ts": 100, "ts": 100, "pid": None}
        glyph, text = dg._liveness(e, now=200, live_state="blocked")
        self.assertEqual(glyph, "○")
        self.assertIn("STALLED", text)

    def test_busy_bg_entry_is_running_despite_no_pid(self):
        e = {"bg": True, "agent_state": "busy", "session_id": "s",
             "last_event_ts": 150, "ts": 100, "pid": None}
        glyph, text = dg._liveness(e, now=200)
        self.assertEqual(glyph, "●")
        self.assertIn("running [bg busy]", text)

    def test_exit_still_wins_over_agent_state(self):
        e = {"bg": True, "agent_state": "blocked", "exit": "stalled",
             "last_event_ts": 100, "ts": 100}
        glyph, text = dg._liveness(e, now=200)
        self.assertEqual(glyph, "○")
        self.assertIn("finished (stalled)", text)


class PreflightTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-preflight-")
        self._cfg = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = self.tmp
        self.cj = os.path.join(self.tmp, ".claude.json")
        self.wt = os.path.join(self.tmp, "repo-worktrees", "w1")
        os.makedirs(self.wt)

    def tearDown(self):
        if self._cfg is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self._cfg
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_cj(self, projects):
        with open(self.cj, "w") as f:
            json.dump({"projects": projects}, f)

    def _preflight(self, entry=None):
        err = io.StringIO()
        with redirect_stderr(err):
            fields = dg._preflight_launch(self.wt, entry or {})
        return fields, err.getvalue()

    def test_missing_trust_entry_is_repaired(self):
        self._write_cj({})
        fields, err = self._preflight()
        self.assertIn("trust_ok_ts", fields)
        self.assertIn("added the ~/.claude.json trust entry", err)
        with open(self.cj) as f:
            data = json.load(f)
        self.assertTrue(data["projects"][self.wt]["hasTrustDialogAccepted"])

    def test_wipe_is_alerted(self):
        # Entry verified before (trust_ok_ts on the registry entry) but gone from
        # ~/.claude.json now → repaired AND the wipe is SAID.
        self._write_cj({})
        fields, err = self._preflight(entry={"trust_ok_ts": dg._now() - 60})
        self.assertIn("TRUST WIPE", err)
        self.assertIn("trust_ok_ts", fields)

    def test_already_trusted_is_silent(self):
        self._write_cj({self.wt: {"hasTrustDialogAccepted": True}})
        fields, err = self._preflight(entry={"grants_probed": dg._now()})
        self.assertNotIn("TRUST", err)
        self.assertIn("trust_ok_ts", fields)

    def test_unrepairable_warns_loudly(self):
        # No ~/.claude.json at all: add_trust_entry refuses to invent one.
        fields, err = self._preflight()
        self.assertNotIn("trust_ok_ts", fields)
        self.assertIn("UNTRUSTED", err)
        self.assertIn("blocked", err)

    def _settings_local(self):
        return os.path.join(self.wt, ".claude", "settings.local.json")

    def _seed_mcp_json(self):
        with open(os.path.join(self.wt, ".mcp.json"), "w") as f:
            json.dump({"mcpServers": {"task-station": {"command": "python3"}}}, f)

    def test_project_mcp_is_preapproved(self):
        # A headless worker parks forever on the "approve 1 new project MCP
        # server — attach to respond" dialog (the 444-17 blocked class, root
        # -caused via the job record's `needs`). MEASURED 2026-08-14: the flag
        # that clears it is enableAllProjectMcpServers in the tree's OWN
        # .claude/settings.local.json — writing enabledMcpjsonServers on the
        # ~/.claude.json project entry does NOT (both spawn-tested).
        self._write_cj({self.wt: {"hasTrustDialogAccepted": True}})
        self._seed_mcp_json()
        _, err = self._preflight(entry={"grants_probed": dg._now()})
        self.assertIn("pre-approved", err)
        self.assertIn("task-station", err)
        with open(self._settings_local()) as f:
            self.assertIs(json.load(f)["enableAllProjectMcpServers"], True)

    def test_explicit_refusal_is_respected(self):
        # enableAllProjectMcpServers already present = the user's own choice,
        # either way. False must NOT be flipped to true behind their back.
        self._write_cj({self.wt: {"hasTrustDialogAccepted": True}})
        self._seed_mcp_json()
        os.makedirs(os.path.dirname(self._settings_local()), exist_ok=True)
        with open(self._settings_local(), "w") as f:
            json.dump({"enableAllProjectMcpServers": False}, f)
        _, err = self._preflight(entry={"grants_probed": dg._now()})
        self.assertNotIn("pre-approved", err)
        with open(self._settings_local()) as f:
            self.assertIs(json.load(f)["enableAllProjectMcpServers"], False)

    def test_no_mcp_json_writes_nothing(self):
        self._write_cj({self.wt: {"hasTrustDialogAccepted": True}})
        _, err = self._preflight(entry={"grants_probed": dg._now()})
        self.assertNotIn("pre-approved", err)
        self.assertFalse(os.path.exists(self._settings_local()))

    def test_existing_grants_are_preserved_by_the_approval_write(self):
        # The approval must not clobber the tool allowlist living in the same file.
        self._write_cj({self.wt: {"hasTrustDialogAccepted": True}})
        self._seed_mcp_json()
        os.makedirs(os.path.dirname(self._settings_local()), exist_ok=True)
        with open(self._settings_local(), "w") as f:
            json.dump({"permissions": {"allow": ["Bash(git:*)"]}}, f)
        self._preflight(entry={"grants_probed": dg._now()})
        with open(self._settings_local()) as f:
            doc = json.load(f)
        self.assertIs(doc["enableAllProjectMcpServers"], True)
        self.assertEqual(doc["permissions"]["allow"], ["Bash(git:*)"])

    def test_grants_probe_once(self):
        self._write_cj({self.wt: {"hasTrustDialogAccepted": True}})
        os.makedirs(os.path.join(self.wt, ".claude"))
        with open(os.path.join(self.wt, ".claude", "settings.local.json"), "w") as f:
            json.dump({"permissions": {"allow": ["Bash(git:*)"]}}, f)
        fields, err = self._preflight()
        self.assertIn("grants_probed", fields)
        self.assertIn("Bash(git:*)", err)
        # Second launch with the probe recorded → silent.
        fields2, err2 = self._preflight(entry={"grants_probed": fields["grants_probed"]})
        self.assertNotIn("grants probe", err2)


class EffectiveGrantsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-grants-")
        self._cfg = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(self.tmp, "cfg")
        os.makedirs(os.environ["CLAUDE_CONFIG_DIR"])
        self.dir = os.path.join(self.tmp, "wt")
        os.makedirs(os.path.join(self.dir, ".claude"))

    def tearDown(self):
        if self._cfg is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self._cfg
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_merge_dedupes_and_orders_user_project_local(self):
        with open(os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "settings.json"), "w") as f:
            json.dump({"permissions": {"allow": ["U1"]}}, f)
        with open(os.path.join(self.dir, ".claude", "settings.json"), "w") as f:
            json.dump({"permissions": {"allow": ["P1", "U1"], "deny": ["D1"]}}, f)
        with open(os.path.join(self.dir, ".claude", "settings.local.json"), "w") as f:
            json.dump({"permissions": {"allow": ["L1", "P1"]}}, f)
        g = dg._effective_grants(self.dir)
        self.assertEqual(g["allow"], ["U1", "P1", "L1"])
        self.assertEqual(g["deny"], ["D1"])
        self.assertEqual([s[0] for s in g["sources"]], ["user", "project", "local"])
        self.assertEqual(g["missing"], [])

    def test_missing_files_are_reported_not_fatal(self):
        g = dg._effective_grants(self.dir)
        self.assertEqual(g["allow"], [])
        self.assertEqual(len(g["missing"]), 3)


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-report-")
        self.saved = (dg.REG_DIR, dg.REG, dg.PROJECTS_ROOT, dg.JOBS_ROOT)
        dg.REG_DIR = os.path.join(self.tmp, "data")
        os.makedirs(dg.REG_DIR)
        dg.REG = os.path.join(dg.REG_DIR, "workers.json")
        dg.PROJECTS_ROOT = os.path.join(self.tmp, "projects")
        os.makedirs(dg.PROJECTS_ROOT)
        dg.JOBS_ROOT = os.path.join(self.tmp, "jobs")
        os.makedirs(dg.JOBS_ROOT)
        self.repo = os.path.join(self.tmp, "repo")
        self.wt = os.path.join(self.tmp, "repo-worktrees", "w1")
        os.makedirs(self.repo)
        os.makedirs(self.wt)

    def tearDown(self):
        dg.REG_DIR, dg.REG, dg.PROJECTS_ROOT, dg.JOBS_ROOT = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_report_path_worktree_root(self):
        p = dg._report_path(self.wt, self.repo, 444, "fixups")
        self.assertEqual(p, os.path.join(self.wt, "HANDOFF-REPORT-fixups.md"))

    def test_report_path_main_checkout_goes_to_data_dir(self):
        p = dg._report_path(self.repo, self.repo, 444, None, project="repo")
        self.assertTrue(p.startswith(os.path.join(dg.REG_DIR, "reports")))
        self.assertNotIn(self.repo, p)          # never inside the user's checkout

    def test_contract_names_path_and_unverified(self):
        t = dg._with_report_contract("do the thing", "/x/HANDOFF-REPORT-444.md")
        self.assertTrue(t.startswith("do the thing"))
        self.assertIn("/x/HANDOFF-REPORT-444.md", t)
        self.assertIn("Unverified", t)
        self.assertIn("Gates run vs NOT run", t)

    def test_worker_authored_file_wins_untouched(self):
        rp = os.path.join(self.wt, "HANDOFF-REPORT-444.md")
        with open(rp, "w") as f:
            f.write("## What was done\nworker wrote this\n")
        path, how = dg._persist_report(rp, dg._now() - 5, "sid-x",
                                       result_text="final message")
        self.assertEqual((path, how), (rp, "worker-authored"))
        with open(rp) as f:
            self.assertNotIn("delegate-harvested", f.read())

    def test_stale_file_is_overwritten_by_harvest(self):
        rp = os.path.join(self.wt, "HANDOFF-REPORT-444.md")
        with open(rp, "w") as f:
            f.write("previous run's report\n")
        old = time.time() - 3600
        os.utime(rp, (old, old))
        path, how = dg._persist_report(rp, dg._now() - 5, "sid-x",
                                       result_text="this run's final message")
        self.assertEqual((path, how), (rp, "harvested"))
        with open(rp) as f:
            body = f.read()
        self.assertIn("delegate-harvested", body)
        self.assertIn("this run's final message", body)

    def test_bg_harvest_reads_transcript_tail(self):
        bucket = os.path.join(dg.PROJECTS_ROOT, "b")
        os.makedirs(bucket)
        with open(os.path.join(bucket, "sid-bg.jsonl"), "w") as f:
            f.write(json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {"content": [
                {"type": "text", "text": "early answer"}]}}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "X"},
                {"type": "text", "text": "THE FINAL REPORT"}]}}) + "\n")
            f.write(json.dumps({"type": "last-prompt"}) + "\n")
        rp = os.path.join(self.wt, "HANDOFF-REPORT-444.md")
        path, how = dg._persist_report(rp, dg._now(), "sid-bg")
        self.assertEqual((path, how), (rp, "harvested"))
        with open(rp) as f:
            body = f.read()
        self.assertIn("THE FINAL REPORT", body)
        self.assertIn("session transcript", body)
        self.assertNotIn("early answer", body)

    def test_job_record_result_beats_transcript_tail(self):
        # Harvest priority: stdout result > job record output.result > transcript.
        sid = "0a623186-77be-4846-9e4e-222485b92871"
        jd = os.path.join(dg.JOBS_ROOT, "0a623186")
        os.makedirs(jd)
        with open(os.path.join(jd, "state.json"), "w") as f:
            json.dump({"state": "done", "output": {"result": "JOB RESULT"}}, f)
        bucket = os.path.join(dg.PROJECTS_ROOT, "b")
        os.makedirs(bucket)
        with open(os.path.join(bucket, sid + ".jsonl"), "w") as f:
            f.write(json.dumps({"type": "assistant", "message": {"content": [
                {"type": "text", "text": "transcript text"}]}}) + "\n")
        rp = os.path.join(self.wt, "HANDOFF-REPORT-444.md")
        path, how = dg._persist_report(rp, dg._now(), sid)
        self.assertEqual((path, how), (rp, "harvested"))
        with open(rp) as f:
            body = f.read()
        self.assertIn("JOB RESULT", body)
        self.assertIn("job record", body)
        self.assertNotIn("transcript text", body)

    def test_nothing_to_harvest_is_none(self):
        rp = os.path.join(self.wt, "HANDOFF-REPORT-444.md")
        self.assertEqual(dg._persist_report(rp, dg._now(), "sid-none"),
                         (None, "none"))


class ReapParkedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-reappk-")
        self.sessdir = os.path.join(self.tmp, "sessions")
        os.makedirs(self.sessdir)
        self.storeroot = os.path.join(self.tmp, "store", "org", "user")
        os.makedirs(self.storeroot)
        self.jobsroot = os.path.join(self.tmp, "jobs")
        os.makedirs(self.jobsroot)
        self.saved = (dg.SESSIONS_DIR, dg.SESSIONS_STORE_ROOT, dg.JOBS_ROOT,
                      dg._kill_pid_group, harness.get_adapter)
        dg.SESSIONS_DIR = self.sessdir
        dg.SESSIONS_STORE_ROOT = os.path.join(self.tmp, "store")
        dg.JOBS_ROOT = self.jobsroot
        self.killed = []
        dg._kill_pid_group = lambda pid, **k: self.killed.append(pid)
        self._env_sid = os.environ.pop("CLAUDE_CODE_SESSION_ID", None)

    def tearDown(self):
        (dg.SESSIONS_DIR, dg.SESSIONS_STORE_ROOT, dg.JOBS_ROOT,
         dg._kill_pid_group, harness.get_adapter) = self.saved
        if self._env_sid is not None:
            os.environ["CLAUDE_CODE_SESSION_ID"] = self._env_sid
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed_session_file(self, sid):
        path = os.path.join(self.sessdir, "store-%s.json" % sid)
        with open(path, "w") as f:
            json.dump({"sessionId": sid}, f)
        return path

    def _row(self, sid, state="blocked", kind="background",
             name="task-station-444-proj", age_days=10, pid=None):
        row = {"sessionId": sid, "kind": kind, "name": name,
               "startedAt": int((time.time() - age_days * 86400) * 1000)}
        if kind == "background":
            row["state"] = state
        else:
            row["status"] = state
            row["pid"] = pid or 999
        return row

    def _run(self, rows, **kw):
        class _Ad:
            def agents_index(self, cwd=None):
                return {r["sessionId"]: r for r in rows}
        harness.get_adapter = lambda name=None: _Ad()
        a = types.SimpleNamespace(min_age_mins=kw.pop("min_age_mins", 360),
                                  dry_run=kw.pop("dry_run", False),
                                  all_names=kw.pop("all_names", False),
                                  harness="claude")
        out = io.StringIO()
        with redirect_stdout(out):
            dg.cmd_reap_parked(a)
        return out.getvalue()

    def test_old_blocked_ts_worker_is_reaped(self):
        sf = self._seed_session_file("old-blocked")
        out = self._run([self._row("old-blocked")])
        self.assertIn("reaped", out)
        self.assertFalse(os.path.exists(sf))
        self.assertEqual(self.killed, [])       # parked bg rows carry no pid

    def test_young_blocked_is_kept(self):
        sf = self._seed_session_file("young")
        out = self._run([self._row("young", age_days=0)])
        self.assertIn("kept", out)
        self.assertTrue(os.path.exists(sf))

    def test_busy_interactive_and_foreign_names_are_spared(self):
        keep = [self._row("busy1", state="busy"),
                self._row("hub1", kind="interactive"),
                self._row("foreign", name="somebody-elses-agent")]
        files = [self._seed_session_file(r["sessionId"]) for r in keep]
        out = self._run(keep)
        self.assertIn("reaped 0", out)
        for f in files:
            self.assertTrue(os.path.exists(f))

    def test_current_session_is_never_reaped(self):
        os.environ["CLAUDE_CODE_SESSION_ID"] = "me-blocked"
        try:
            sf = self._seed_session_file("me-blocked")
            out = self._run([self._row("me-blocked")])
            self.assertIn("reaped 0", out)
            self.assertTrue(os.path.exists(sf))
        finally:
            os.environ.pop("CLAUDE_CODE_SESSION_ID", None)

    def test_dry_run_changes_nothing(self):
        sf = self._seed_session_file("old-blocked")
        out = self._run([self._row("old-blocked")], dry_run=True)
        self.assertIn("would reap", out)
        self.assertTrue(os.path.exists(sf))

    def _seed_job(self, sid_short, state="blocked", **extra):
        d = os.path.join(self.jobsroot, sid_short)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "state.json")
        doc = {"state": state, "tempo": state}
        doc.update(extra)
        with open(p, "w") as f:
            json.dump(doc, f)
        return p

    def test_reap_marks_the_job_record_done(self):
        # THE row `claude agents --json` renders (measured 2026-08-14): the job
        # record. Killing processes + removing store files left a ghost row until
        # the job state flipped to a terminal value.
        sid = "0a623186-77be-4846-9e4e-222485b92871"
        jp = self._seed_job("0a623186", detail="waiting", needs="git push")
        self._run([self._row(sid)])
        with open(jp) as f:
            self.assertEqual(json.load(f)["state"], "done")

    def test_job_result_and_diagnosis_read_the_record(self):
        sid = "0a623186-77be-4846-9e4e-222485b92871"
        self._seed_job("0a623186", detail="cwd-guard denial",
                       needs="run: git rm", output={"result": "REPORT BODY"})
        self.assertEqual(dg._job_result(sid), "REPORT BODY")
        jd = dg._job_diagnosis(sid)
        self.assertIn("cwd-guard denial", jd)
        self.assertIn("run: git rm", jd)

    def test_mark_job_done_skips_terminal_records(self):
        sid = "0a623186-77be-4846-9e4e-222485b92871"
        jp = self._seed_job("0a623186", state="failed")
        self.assertFalse(dg._mark_job_done(sid))
        with open(jp) as f:
            self.assertEqual(json.load(f)["state"], "failed")

    def test_nested_store_matched_on_cli_session_id(self):
        # The CURRENT store layout: <root>/<org>/<user>/local_<uuid>.json whose
        # `cliSessionId` (not `sessionId`) is the agents-list id — the miss that
        # made the first live reap report 40 reaped while removing nothing.
        sid = "0a623186-77be-4846-9e4e-222485b92871"
        nf = os.path.join(self.storeroot, "local_dead-beef.json")
        with open(nf, "w") as f:
            json.dump({"sessionId": "local_dead-beef", "cliSessionId": sid}, f)
        out = self._run([self._row(sid)])
        self.assertIn("reaped 1", out)
        self.assertFalse(os.path.exists(nf))


class ReapTaskWorkersBgShapeTest(unittest.TestCase):
    """The task-close reaper must handle the background row shape: state in
    `state`, NO pid — previously `if not pid: continue` made every parked bg
    agent unreapable (how 40 accumulated)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-reapbg-")
        self.sessdir = os.path.join(self.tmp, "sessions")
        os.makedirs(self.sessdir)
        self.saved = (dg.REG_DIR, dg.REG, dg._kill_pid_group, dg.SESSIONS_DIR)
        dg.REG_DIR = self.tmp
        dg.REG = os.path.join(self.tmp, "workers.json")
        dg.SESSIONS_DIR = self.sessdir
        self.killed = []
        dg._kill_pid_group = lambda pid, **k: self.killed.append(pid)
        os.environ["TASK_STATION_REAP_WORKERS_ON_DONE"] = "on"

    def tearDown(self):
        dg.REG_DIR, dg.REG, dg._kill_pid_group, dg.SESSIONS_DIR = self.saved
        os.environ.pop("TASK_STATION_REAP_WORKERS_ON_DONE", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_bg_shape_row_without_pid_is_reaped_via_store_file(self):
        with open(dg.REG, "w") as f:
            json.dump({"7:proj": {"seq": 7, "session_id": "w1"}}, f)
        sf = os.path.join(self.sessdir, "s.json")
        with open(sf, "w") as f:
            json.dump({"sessionId": "w1"}, f)

        class _Ad:
            def agents_index(self, cwd=None):
                return {"w1": {"sessionId": "w1", "state": "blocked",
                               "kind": "background",
                               "name": "task-station-7-proj"}}
        roster = {"w1": {"role": "worker", "name": "task-station-7-proj"}}
        self.assertEqual(dg.reap_task_workers(7, adapter=_Ad(), roster=roster),
                         ["w1"])
        self.assertFalse(os.path.exists(sf))    # store file removed
        self.assertEqual(self.killed, [])       # no pid → nothing to kill

    def test_bg_shape_busy_worker_is_spared(self):
        with open(dg.REG, "w") as f:
            json.dump({"7:proj": {"seq": 7, "session_id": "w2"}}, f)

        class _Ad:
            def agents_index(self, cwd=None):
                return {"w2": {"sessionId": "w2", "state": "busy",
                               "kind": "background",
                               "name": "task-station-7-proj"}}
        roster = {"w2": {"role": "worker", "name": "task-station-7-proj"}}
        self.assertEqual(dg.reap_task_workers(7, adapter=_Ad(), roster=roster), [])


if __name__ == "__main__":
    unittest.main()
