"""Work-phase classifier (lib/phases.py) + its wiring into the usage scan (WS3).

The classifier is deterministic (no model calls): it reads one parsed assistant
transcript line's tool_use block names + the envelope `attributionSkill` and
returns planning|research|implementation|verification|delivery|other. These tests
pin every rule, the mixed-tool precedence, the attributionSkill override, and then
drive a real SqliteBackend to prove the scanner fills `session_usage.phases`, that
a PHASES_VERSION bump forces a rescan, and that the aggregated pct sums to ~100."""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

import phases  # noqa: E402
import store   # noqa: E402
import usage   # noqa: E402

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
tsmod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tsmod)

OPUS = "claude-opus-4-8"


def _iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def _asst(tools=None, skill=None, out=100, model=OPUS, ts=1000):
    """Assistant transcript line. `tools` items are either a bare tool name or a
    (name, input_dict) pair (for Bash commands)."""
    content = []
    for t in (tools or []):
        if isinstance(t, str):
            content.append({"type": "tool_use", "name": t})
        else:
            content.append({"type": "tool_use", "name": t[0], "input": t[1]})
    line = {"type": "assistant", "timestamp": _iso(ts), "cwd": "/proj",
            "entrypoint": "cli",
            "message": {"model": model, "content": content,
                        "usage": {"input_tokens": 1000, "output_tokens": out,
                                  "cache_read_input_tokens": 0,
                                  "cache_creation_input_tokens": 0}}}
    if skill:
        line["attributionSkill"] = skill
    return line


def _bash(cmd):
    return ("Bash", {"command": cmd})


class ClassifyToolTest(unittest.TestCase):
    def test_edit_write_notebook_are_implementation(self):
        for tool in ("Edit", "Write", "NotebookEdit"):
            self.assertEqual(phases.classify_message(_asst(tools=[tool])),
                             "implementation", tool)

    def test_bash_test_build_commands_are_verification(self):
        for cmd in ("python3 -m unittest discover", "pytest tests/",
                    "npm test", "npm run build", "tsc --noEmit",
                    "go test ./...", "cargo test", "cargo build", "make"):
            self.assertEqual(phases.classify_message(_asst(tools=[_bash(cmd)])),
                             "verification", cmd)

    def test_bash_delivery_commands_are_delivery(self):
        for cmd in ("git push -u origin br", "git commit -m x", "git merge main",
                    "gh pr create", "az repos pr create", "release the build"):
            self.assertEqual(phases.classify_message(_asst(tools=[_bash(cmd)])),
                             "delivery", cmd)

    def test_file_mutation_and_redirect_bash_are_implementation(self):
        # v3 (B5): file-mutation commands + file redirects are implementation work,
        # shrinking the "other" slice.
        self.assertEqual(phases.classify_message(_asst(tools=[_bash("mkdir -p build")])),
                         "implementation")
        self.assertEqual(phases.classify_message(_asst(tools=[_bash("echo hi > f")])),
                         "implementation")
        self.assertEqual(phases.classify_message(_asst(tools=[_bash("mv a b")])),
                         "implementation")

    def test_run_script_bash_is_verification(self):
        # v3 (B5): running code / package managers reads as verification.
        for cmd in ("python3 script.py", "node app.js", "./run.sh", "npm install"):
            self.assertEqual(phases.classify_message(_asst(tools=[_bash(cmd)])),
                             "verification", cmd)

    def test_genuinely_neutral_bash_has_no_phase_signal(self):
        # A bash command matching none of the regexes still carries no signal → other.
        self.assertEqual(phases.classify_message(_asst(tools=[_bash("curl https://x")])), "other")
        self.assertEqual(phases.classify_message(_asst(tools=[_bash("pbcopy")])), "other")

    def test_plan_mode_tools_are_planning(self):
        for tool in ("EnterPlanMode", "ExitPlanMode"):
            self.assertEqual(phases.classify_message(_asst(tools=[tool])), "planning", tool)

    def test_read_search_agent_tools_are_research(self):
        for tool in ("Read", "Grep", "Glob", "WebSearch", "WebFetch", "Agent", "Explore"):
            self.assertEqual(phases.classify_message(_asst(tools=[tool])), "research", tool)

    def test_no_tools_is_other(self):
        self.assertEqual(phases.classify_message(_asst(tools=[])), "other")


class ClassifyPrecedenceTest(unittest.TestCase):
    def test_implementation_beats_research(self):
        self.assertEqual(phases.classify_message(_asst(tools=["Read", "Edit"])),
                         "implementation")

    def test_verification_beats_research_and_delivery(self):
        self.assertEqual(
            phases.classify_message(_asst(tools=["Read", _bash("pytest")])),
            "verification")
        self.assertEqual(
            phases.classify_message(_asst(tools=[_bash("git push"), _bash("pytest")])),
            "verification")

    def test_delivery_beats_planning(self):
        self.assertEqual(
            phases.classify_message(_asst(tools=["ExitPlanMode", _bash("git push")])),
            "delivery")

    def test_planning_beats_research(self):
        self.assertEqual(phases.classify_message(_asst(tools=["Read", "ExitPlanMode"])),
                         "planning")

    def test_implementation_is_top_of_precedence(self):
        self.assertEqual(
            phases.classify_message(
                _asst(tools=["Edit", _bash("pytest"), _bash("git push"), "ExitPlanMode", "Read"])),
            "implementation")


class ClassifySkillTest(unittest.TestCase):
    def test_skill_maps_to_planning(self):
        for s in ("superpowers:brainstorming", "superpowers:writing-plans", "Plan"):
            self.assertEqual(phases.classify_message(_asst(skill=s)), "planning", s)

    def test_skill_maps_to_verification(self):
        for s in ("code-review", "verify", "requesting-code-review"):
            self.assertEqual(phases.classify_message(_asst(skill=s)), "verification", s)

    def test_skill_maps_to_research(self):
        self.assertEqual(phases.classify_message(_asst(skill="deep-research")), "research")

    def test_attribution_skill_overrides_tools(self):
        # brainstorming skill wins even over an Edit (implementation) tool.
        self.assertEqual(
            phases.classify_message(_asst(skill="superpowers:brainstorming", tools=["Edit"])),
            "planning")

    def test_unknown_skill_falls_through_to_tools(self):
        self.assertEqual(
            phases.classify_message(_asst(skill="some-random-thing", tools=["Edit"])),
            "implementation")


class ClassifyV2Test(unittest.TestCase):
    """WS7 v2 recategorization: MCP tools, Task/SendMessage, TodoWrite/TaskCreate, and
    read-only Bash get real buckets instead of falling into 'other'."""

    def test_mcp_read_tools_are_research(self):
        for name in ("mcp__serena__find_symbol", "mcp__serena__get_symbols_overview",
                     "mcp__ado__search_workitem", "mcp__ado__repo_get_file_content",
                     "mcp__mssql__DiscoverData", "mcp__serena__list_memories"):
            self.assertEqual(phases.classify_message(_asst(tools=[name])), "research", name)

    def test_mcp_write_tools_are_implementation(self):
        for name in ("mcp__serena__replace_symbol_body", "mcp__serena__insert_after_symbol",
                     "mcp__serena__rename_symbol", "mcp__mssql__UpdateRecords"):
            self.assertEqual(phases.classify_message(_asst(tools=[name])),
                             "implementation", name)

    def test_mcp_ado_pr_and_workitem_ops_are_delivery(self):
        for name in ("mcp__ado__repo_create_pull_request",
                     "mcp__ado__repo_update_pull_request",
                     "mcp__ado__repo_vote_pull_request",
                     "mcp__ado__wit_create_work_item",
                     "mcp__ado__wit_link_work_item_to_pull_request"):
            self.assertEqual(phases.classify_message(_asst(tools=[name])), "delivery", name)

    def test_task_and_todo_tools(self):
        self.assertEqual(phases.classify_message(_asst(tools=["TodoWrite"])), "planning")
        self.assertEqual(phases.classify_message(_asst(tools=["TaskCreate"])), "planning")
        self.assertEqual(phases.classify_message(_asst(tools=["EnterWorktree"])), "planning")
        self.assertEqual(phases.classify_message(_asst(tools=["Task"])), "research")
        self.assertEqual(phases.classify_message(_asst(tools=["SendMessage"])), "research")

    def test_readonly_bash_is_research(self):
        for cmd in ("git status", "git diff HEAD", "git log --oneline", "rg -n foo",
                    "ls -la", "cat file.py", "grep -r x .", "head -20 f"):
            self.assertEqual(phases.classify_message(_asst(tools=[_bash(cmd)])),
                             "research", cmd)

    def test_write_bash_still_wins_over_readonly(self):
        # commit is delivery even though the read-only regex has git verbs.
        self.assertEqual(phases.classify_message(_asst(tools=[_bash("git commit -m x")])),
                         "delivery")

    def test_unknown_tool_still_other(self):
        self.assertEqual(phases.classify_message(_asst(tools=["SomeRandomTool"])), "other")
        # an MCP tool whose verb matches nothing also falls through to other.
        self.assertEqual(phases.classify_message(_asst(tools=["mcp__x__zzz"])), "other")

    def test_phases_version_is_four(self):
        # v4 = classification unchanged; bumped to force the one-time rescan that
        # re-files stored prompts under their span-matched task.
        self.assertEqual(phases.PHASES_VERSION, 4)


class VersionTest(unittest.TestCase):
    def test_phases_version_is_a_positive_int(self):
        self.assertIsInstance(phases.PHASES_VERSION, int)
        self.assertGreaterEqual(phases.PHASES_VERSION, 1)

    def test_phases_tuple_covers_all_six(self):
        self.assertEqual(set(phases.PHASES),
                         {"planning", "research", "implementation",
                          "verification", "delivery", "other"})


class _ScanBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="phases-scan-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        self.projects = os.path.join(self.tmp, "projects")
        self.bucket = os.path.join(self.projects, "-proj")
        os.makedirs(self.bucket, exist_ok=True)
        usage.PROJECTS_ROOT = self.projects
        usage.WORKERS_REGISTRY = None
        self.store = store.SqliteBackend(os.path.join(self.tmp, "store"))
        self.store.ensure()

    def tearDown(self):
        self.store.close()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_session(self, sid, lines):
        with open(os.path.join(self.bucket, sid + ".jsonl"), "w") as f:
            for o in lines:
                f.write(json.dumps(o) + "\n")


class ScanWiringTest(_ScanBase):
    def test_scan_fills_session_phases(self):
        self._write_session("s1", [
            _asst(tools=["Edit"], out=300),
            _asst(tools=["Read"], out=100),
            _asst(tools=[], out=50),
        ])
        usage.scan_session(self.store, "s1", "T1")
        ph = self.store.get_session_usage("s1")["phases"]
        self.assertEqual(ph["__v"], phases.PHASES_VERSION)   # version stamp present
        self.assertEqual(ph["implementation"]["out"], 300)
        self.assertEqual(ph["implementation"]["msgs"], 1)
        self.assertEqual(ph["research"]["out"], 100)
        self.assertEqual(ph["other"]["out"], 50)
        self.assertGreater(ph["implementation"]["cost_usd"], 0)

    def test_incremental_scan_accumulates_phases(self):
        path = os.path.join(self.bucket, "s2.jsonl")
        with open(path, "w") as f:
            f.write(json.dumps(_asst(tools=["Edit"], out=100)) + "\n")
        usage.scan_session(self.store, "s2", "T1")
        with open(path, "a") as f:
            f.write(json.dumps(_asst(tools=["Edit"], out=200)) + "\n")
        usage.scan_session(self.store, "s2", "T1")
        ph = self.store.get_session_usage("s2")["phases"]
        self.assertEqual(ph["implementation"]["out"], 300)   # 100 + 200 accumulated
        self.assertEqual(ph["implementation"]["msgs"], 2)

    def test_version_bump_forces_rescan_even_when_file_unchanged(self):
        self._write_session("s3", [_asst(tools=["Edit"], out=100)])
        usage.scan_session(self.store, "s3", "T1")
        self.assertEqual(
            self.store.get_session_usage("s3")["phases"]["__v"], phases.PHASES_VERSION)
        bumped = phases.PHASES_VERSION + 1
        orig = phases.PHASES_VERSION
        phases.PHASES_VERSION = bumped
        try:
            usage.scan_session(self.store, "s3", "T1")     # file unchanged, version stale
            ph = self.store.get_session_usage("s3")["phases"]
            self.assertEqual(ph["__v"], bumped)            # rescanned, not a stale no-op
            self.assertEqual(ph["implementation"]["out"], 100)   # full recompute, not doubled
        finally:
            phases.PHASES_VERSION = orig

    def test_other_bucket_captures_drilldown_names(self):
        # B5: an unrecognised tool + a neutral bash command are tallied under
        # other["names"] so the board can show the top "other" contributors.
        self._write_session("sn", [
            _asst(tools=["SomeRandomTool"], out=10),
            _asst(tools=[_bash("curl https://x")], out=10),
            _asst(tools=[_bash("curl https://y")], out=10),
        ])
        usage.scan_session(self.store, "sn", "T1")
        names = self.store.get_session_usage("sn")["phases"]["other"]["names"]
        self.assertEqual(names.get("SomeRandomTool"), 1)
        self.assertEqual(names.get("$ curl"), 2)

    def test_task_usage_phase_pct_sums_to_100(self):
        self.store.save_task({"id": "T1", "updated_ts": 1, "sessions": ["s1"],
                              "spans": [], "title": "t", "status": "open"})
        self._write_session("s1", [
            _asst(tools=["Edit"], out=300),
            _asst(tools=["Read"], out=100),
            _asst(tools=[], out=100),
        ])
        usage.scan_session(self.store, "s1", "T1")
        data = usage.task_usage(self.store, {"id": "T1", "cost": {}})
        ph = data["phases"]
        self.assertIn("implementation", ph)
        self.assertIn("pct", ph["implementation"])
        self.assertAlmostEqual(sum(d["pct"] for d in ph.values()), 1.0, places=6)

    def test_render_shows_phases_line(self):
        self.store.save_task({"id": "T1", "updated_ts": 1, "sessions": ["s1"],
                              "spans": [], "title": "demo", "status": "open"})
        self._write_session("s1", [
            _asst(tools=["Edit"], out=300),
            _asst(tools=["Read"], out=100),
        ])
        usage.scan_session(self.store, "s1", "T1")
        data = usage.task_usage(self.store, {"id": "T1", "cost": {}})
        txt = tsmod._render_usage({"id": "T1", "seq": 1, "title": "demo"}, data)
        self.assertIn("Phases:", txt)
        self.assertIn("Implementation", txt)
        self.assertIn("Research", txt)


if __name__ == "__main__":
    unittest.main()
