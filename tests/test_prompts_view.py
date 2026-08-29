"""WS6 — tasks-by-prompt view. The exact user prompts (and slash commands) that
drove a task, chronological + timestamped + session-attributed (hub vs each
delegated worker), surfaced four ways: the `prompts` CLI (--json/--md/--all), the
read-only `/todo <n> prompts` render route, the MCP `get_prompts` tool, and the
board's full-prompts <details> (config-gated `board_prompts`, default on).

Seeds the SQLite ledger directly (no transcripts — the view reads the persisted
`prompts` + `session_usage` rows). Stdlib-only, dual unittest/pytest compatible."""
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(_ROOT, "lib")
TOOLS = os.path.join(_ROOT, "tools")
COMMANDS = os.path.join(_ROOT, "commands")
sys.path.insert(0, LIB)
sys.path.insert(0, TOOLS)

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)

import config  # noqa: E402
import usage  # noqa: E402
import render_board  # noqa: E402
import mcp_server  # noqa: E402


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _PromptsBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.store.reset_cache()
        for k in ("TASK_STATION_USAGE_TRACKING", "TASK_STATION_BOARD_PROMPTS"):
            os.environ.pop(k, None)

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        for k in ("TASK_STATION_USAGE_TRACKING", "TASK_STATION_BOARD_PROMPTS"):
            os.environ.pop(k, None)
        ts.store.reset_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _task(self, title="Prompt task"):
        t = ts.new_task(title, "summary for " + title, color="green", effort="m")
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _seed(self, t, hub="18521696aaaa", wk="workersid0002"):
        """Two sessions (hub + worker) and a chronological set of prompts:
        two hub prompts, a hub slash command, a worker brief, a compaction row, and
        an empty-text row (which must be dropped)."""
        store = ts._backend()
        store.upsert_session_usage({
            "session_id": hub, "task_id": t["id"], "role": "hub", "label": None,
            "entrypoint": "cli", "first_ts": 100, "last_ts": 500,
            "models": {}, "sidechain": {}, "phases": {}})
        store.upsert_session_usage({
            "session_id": wk, "task_id": t["id"], "role": "worker", "label": "ws6",
            "entrypoint": "sdk-cli", "first_ts": 300, "last_ts": 400,
            "models": {}, "sidechain": {}, "phases": {}})
        rows = [
            ("u1", hub, 1751600000, "prompt", "review our plugin against the top plugins"),
            ("u2", hub, 1751603000, "command", "/todo save"),
            ("u3", wk, 1751606000, "prompt", "worker brief: build the prompts view"),
            ("u4", hub, 1751609000, "prompt", "modify our plugin so it tracks prompts"),
            ("u5", hub, 1751610000, "compact", "compaction summary of the session"),
            ("u6", hub, 1751611000, "prompt", "   "),   # empty → dropped
        ]
        for uuid_, sid, tsv, kind, text in rows:
            store.upsert_prompt({"uuid": uuid_, "session_id": sid, "task_id": t["id"],
                                 "ts": tsv, "kind": kind, "text": text})
        return ts.load_task(t["id"])


# ---------------------------------------------------------- usage.task_prompts ---

class TaskPromptsEngineTest(_PromptsBase):
    def test_chronological_order_and_empty_dropped(self):
        t = self._seed(self._task())
        rows = usage.task_prompts(ts._backend(), t)
        # empty (u6) + compact (u5) excluded by default → 4 rows, oldest first.
        self.assertEqual([r["uuid"] for r in rows], ["u1", "u2", "u3", "u4"])
        self.assertTrue(all(r["ts"] is not None for r in rows))
        tss = [r["ts"] for r in rows]
        self.assertEqual(tss, sorted(tss))

    def test_session_attribution_hub_vs_worker(self):
        t = self._seed(self._task())
        by = {r["uuid"]: r for r in usage.task_prompts(ts._backend(), t)}
        self.assertEqual(by["u1"]["role"], "hub")
        self.assertIsNone(by["u1"]["label"])
        self.assertEqual(by["u1"]["sid"], "18521696")           # short (8-char) sid
        self.assertEqual(by["u3"]["role"], "worker")
        self.assertEqual(by["u3"]["label"], "ws6")

    def test_kind_filtering_command_and_compact(self):
        t = self._seed(self._task())
        default_kinds = [r["kind"] for r in usage.task_prompts(ts._backend(), t)]
        self.assertIn("command", default_kinds)
        self.assertNotIn("compact", default_kinds)              # omitted by default
        allrows = usage.task_prompts(ts._backend(), t, include_compact=True)
        self.assertIn("compact", [r["kind"] for r in allrows])  # --all includes it
        self.assertEqual(len(allrows), 5)                       # + the compact row

    def test_unattributed_session_degrades_to_unknown(self):
        t = self._task()
        store = ts._backend()
        store.upsert_prompt({"uuid": "x1", "session_id": "orphansession",
                             "task_id": t["id"], "ts": 10, "kind": "prompt",
                             "text": "no ledger row for this session"})
        rows = usage.task_prompts(store, ts.load_task(t["id"]))
        self.assertEqual(rows[0]["role"], "unknown")

    def test_empty_when_usage_tracking_off(self):
        t = self._seed(self._task())
        os.environ["TASK_STATION_USAGE_TRACKING"] = "off"
        self.assertEqual(usage.task_prompts(ts._backend(), t), [])


# ----------------------------------------------------------- terminal / md render ---

class FormatPromptsTest(_PromptsBase):
    def test_terminal_header_rows_and_session_tag(self):
        t = self._seed(self._task("competitive review"))
        rows = usage.task_prompts(ts._backend(), t)
        out = ts._format_prompts(t, rows)                       # default = human-only
        self.assertTrue(out.startswith("# Prompts — task %s · competitive review" % t.get("seq")))
        self.assertIn("[hub 18521696]", out)
        self.assertIn("[worker:ws6 workersi]", out)             # role:label + short sid
        self.assertIn("review our plugin against the top plugins", out)  # human prompt
        self.assertNotIn("/todo save", out)                     # command filtered from default
        self.assertRegex(out, r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")  # local timestamp column

    def test_terminal_all_shows_raw_trail(self):
        t = self._seed(self._task("competitive review"))
        rows = usage.task_prompts(ts._backend(), t, include_compact=True)
        out = ts._format_prompts(t, rows, include_compact=True)  # --all = raw trail
        self.assertIn("/todo save", out)                         # command shown as-is

    def test_terminal_empty_task_message(self):
        t = self._task("bare")
        out = ts._format_prompts(t, usage.task_prompts(ts._backend(), t))
        self.assertIn("# Prompts — task", out)
        self.assertIn("No prompts captured", out)

    def test_md_default_is_human_only(self):
        t = self._seed(self._task())
        rows = usage.task_prompts(ts._backend(), t)
        md = ts._format_prompts_md(t, rows)                     # default = human-only
        self.assertTrue(md.startswith("# Prompts — task"))
        self.assertIn("3 prompts · 2 sessions · human prompts", md)   # u1,u3,u4 (not the command)
        self.assertIn("> review our plugin against the top plugins", md)   # blockquote prose
        self.assertNotIn("`/todo save`", md)                    # command filtered from default
        self.assertNotIn("compaction summary of the session", md)  # compact omitted

    def test_md_all_shows_command_codespan(self):
        t = self._seed(self._task())
        rows = usage.task_prompts(ts._backend(), t, include_compact=True)
        md = ts._format_prompts_md(t, rows, include_compact=True)  # --all = raw trail
        self.assertIn("`/todo save`", md)                       # command as code span
        self.assertIn("· command", md)

    def test_md_include_compact_via_all(self):
        t = self._seed(self._task())
        rows = usage.task_prompts(ts._backend(), t, include_compact=True)
        md = ts._format_prompts_md(t, rows, include_compact=True)
        self.assertIn("compaction summary of the session", md)
        self.assertIn("· compaction summary", md)

    def test_md_empty_task_message(self):
        t = self._task("bare")
        md = ts._format_prompts_md(t, usage.task_prompts(ts._backend(), t))
        self.assertIn("No prompts captured", md)


# ------------------------------------------------------------------- CLI command ---

class CmdPromptsTest(_PromptsBase):
    def _run(self, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_prompts(_Args(**{"task": None, "as_json": False, "as_md": False,
                                    "all": False, **kw}))
        return buf.getvalue()

    def test_default_terminal(self):
        t = self._seed(self._task())
        out = self._run(task=str(t.get("seq")))
        self.assertIn("# Prompts — task", out)
        self.assertIn("[hub 18521696]", out)

    def test_json_output(self):
        t = self._seed(self._task())
        out = self._run(task=str(t.get("seq")), as_json=True)
        data = json.loads(out)
        self.assertEqual([r["uuid"] for r in data], ["u1", "u2", "u3", "u4"])
        self.assertEqual(data[0]["role"], "hub")

    def test_md_output(self):
        t = self._seed(self._task())
        out = self._run(task=str(t.get("seq")), as_md=True)     # default = human-only
        self.assertIn("> review our plugin against the top plugins", out)
        self.assertNotIn("`/todo save`", out)                   # command filtered
        # --all restores the raw trail incl. the command code span.
        out_all = self._run(task=str(t.get("seq")), as_md=True, all=True)
        self.assertIn("`/todo save`", out_all)

    def test_all_includes_compact_json(self):
        t = self._seed(self._task())
        out = self._run(task=str(t.get("seq")), as_json=True, all=True)
        self.assertIn("compact", [r["kind"] for r in json.loads(out)])

    def test_missing_task_usage_line(self):
        out = self._run(task=None)
        self.assertIn("usage: task-station prompts --task", out)

    def test_bad_ref(self):
        out = self._run(task="99999")
        self.assertIn("No task matching", out)


# ------------------------------------------------ /todo <n> prompts render route ---

class TodoPromptsRouteTest(_PromptsBase):
    def _render(self, arg, session="sessroute01"):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_render(_Args(arg=arg, session=session, format="ascii"))
        return buf.getvalue()

    def test_numbered_route_renders_trail(self):
        t = self._seed(self._task())
        out = self._render("%s prompts" % t.get("seq"))
        self.assertIn("# Prompts — task", out)
        self.assertIn("[hub 18521696]", out)

    def test_route_is_read_only(self):
        t = self._seed(self._task())
        self._render("%s prompts" % t.get("seq"), session="ro-sess")
        # read-only: must NOT attach/link the session (unlike a bare /todo <n>).
        self.assertIsNone(ts.get_link("ro-sess"))

    def test_keyword_first_ordering(self):
        t = self._seed(self._task())
        out = self._render("prompts %s" % t.get("seq"))
        self.assertIn("# Prompts — task", out)

    def test_bare_prompts_no_task_attached(self):
        out = self._render("prompts", session="unattached-sess")
        self.assertIn("No task attached", out)

    def test_bad_ref_falls_back_to_list(self):
        self._seed(self._task())
        out = self._render("99999 prompts")
        self.assertIn("No task matching", out)


# ---------------------------------------------------------------- board tie-in ---

class BoardPromptsViewModelTest(_PromptsBase):
    def _seed_many(self, t, sid="hubsession01", n=8):
        store = ts._backend()
        store.upsert_session_usage({
            "session_id": sid, "task_id": t["id"], "role": "hub", "label": None,
            "entrypoint": "cli", "first_ts": 1, "last_ts": n,
            "models": {}, "sidechain": {}, "phases": {}})
        for i in range(n):
            store.upsert_prompt({"uuid": "p%d" % i, "session_id": sid,
                                 "task_id": t["id"], "ts": 1751600000 + i,
                                 "kind": "prompt", "text": "prompt number %d" % i})
        return ts.load_task(t["id"])

    def test_view_model_carries_full_and_preview(self):
        t = self._seed_many(self._task(), n=8)
        vm = ts._board_view_model(ts.load_task(t["id"]))
        self.assertEqual(len(vm["prompts_full"]), 8)            # complete trail
        self.assertEqual(len(vm["prompts_preview"]), 5)         # last 5
        # preview is the tail of the full trail.
        self.assertEqual(vm["prompts_preview"][-1]["text"], "prompt number 7")
        self.assertEqual(vm["prompts_full"][0]["role"], "hub")  # session-attributed

    def test_view_model_prompts_gated_off(self):
        t = self._seed_many(self._task(), n=8)
        os.environ["TASK_STATION_BOARD_PROMPTS"] = "off"
        vm = ts._board_view_model(ts.load_task(t["id"]))
        self.assertEqual(vm["prompts_full"], [])
        self.assertEqual(vm["prompts_preview"], [])

    def test_per_hub_prompt_breakdown_shows_all(self):
        # board B6/B13: prompts render per hub (all of them, not a recency-capped
        # preview). With no session_meta the ledger prompts surface under an
        # 'unattributed' pseudo-hub card.
        t = self._seed_many(self._task(), n=8)
        vm = ts._board_view_model(ts.load_task(t["id"]))
        html = render_board.render_html([vm])
        self.assertIn("prompts (8)", html)                     # ALL prompts, per hub
        self.assertIn('data-key="hp:', html)                   # per-hub prompt breakdown key
        self.assertIn("prompt number 0", html)                 # full trail, oldest first

    def test_per_hub_prompt_count_small(self):
        t = self._seed_many(self._task(), n=3)
        vm = ts._board_view_model(ts.load_task(t["id"]))
        html = render_board.render_html([vm])
        self.assertNotIn("All prompts", html)                  # no standalone full panel
        self.assertNotIn("Recent prompts", html)               # no standalone preview
        self.assertIn("prompts (3)", html)

    def test_hub_prompt_escapes_and_shows_session(self):
        vm = {"seq": 4, "title": "x", "full_title": "x", "status": "open",
              "status_label": "open", "usage": None, "sessions": [], "phases": [],
              "hubs": [{"sid8": "abcd1234", "role": "hub", "main": True, "pinned": False,
                        "live": False, "msgs": 1, "age": "1h", "oneliner": "",
                        "resume_command": None, "own": {}, "agg": {}, "reported": 0.0,
                        "workers": [], "prompt_count": 1,
                        "prompts": [{"ts": 1751600000, "kind": "prompt", "role": "worker",
                                     "label": "ws6", "sid": "abcd1234", "human": True,
                                     "text": "danger <script>x</script>"}]}],
              "cost_thresholds": [0.01, 0.05]}
        html = render_board.render_html([vm])
        self.assertIn("prompts (1)", html)
        self.assertIn("worker:ws6 abcd1234", html)             # session attribution
        self.assertNotIn("<script>x</script>", html)
        self.assertIn("&lt;script&gt;x&lt;/script&gt;", html)


# ------------------------------------------------------------------- MCP tool ---

class McpGetPromptsTest(_PromptsBase):
    def setUp(self):
        super().setUp()
        # The bridge drives its OWN engine module instance; repoint ITS frozen path
        # globals at this test's store (same dir as `ts`) so both read one tasks.db —
        # exactly how test_mcp_server.py repoints the bridge engine.
        eng = mcp_server._engine()
        eng.DATA = ts.DATA
        eng.STORE = ts.STORE
        eng.TASKS_DIR = ts.TASKS_DIR
        eng.LINKS_DIR = ts.LINKS_DIR
        ts.store.reset_cache()

    def test_tool_registered(self):
        self.assertIn("get_prompts", mcp_server._TOOLS_BY_NAME)
        listed = {t["name"] for t in mcp_server.dispatch("tools/list", {})["tools"]}
        self.assertIn("get_prompts", listed)

    def test_returns_markdown_trail(self):
        t = self._seed(self._task())
        text = mcp_server._tool_get_prompts({"ref": str(t.get("seq"))})
        self.assertIn("# Prompts — task", text)
        self.assertIn("> review our plugin against the top plugins", text)  # human prompt
        self.assertNotIn("`/todo save`", text)                        # command filtered (default)
        self.assertNotIn("compaction summary of the session", text)   # default off

    def test_include_compact(self):
        t = self._seed(self._task())
        text = mcp_server._tool_get_prompts({"ref": str(t.get("seq")),
                                             "include_compact": True})
        self.assertIn("compaction summary of the session", text)

    def test_unknown_ref(self):
        text = mcp_server._tool_get_prompts({"ref": "99999"})
        self.assertIn("No task matching", text)


# ------------------------------------------------------- command file / alias ---

class PromptsCommandFileTest(unittest.TestCase):
    def _read(self, name):
        with open(os.path.join(COMMANDS, name), encoding="utf-8") as f:
            return f.read()

    def test_prompts_command_file_shape(self):
        text = self._read("prompts.md")
        self.assertIn("description:", text)
        self.assertIn("allowed-tools: Bash", text)
        self.assertIn("disable-model-invocation: true", text)
        self.assertIn("<<'TS_ARGV_END'", text)                    # args are data
        self.assertIn('${TS_ARGV:+$TS_ARGV }prompts', text)       # routes like history
        self.assertIn("task-station.py", text)
        self.assertIn("READ-ONLY", text)
        self.assertIn("verbatim", text)

    def test_todo_command_documents_prompts(self):
        text = self._read("todo.md")
        self.assertIn("# Prompts —", text)                        # handling bullet
        self.assertIn("/todo <n> prompts", text)

    def test_bare_alias_loop_includes_prompts(self):
        with open(os.path.join(_ROOT, "hooks", "on_session_start.sh"),
                  encoding="utf-8") as f:
            hook = f.read()
        import re
        m = re.search(r"for c in ([^;]+); do", hook)
        self.assertIsNotNone(m)
        members = [w.strip("'\"") for w in m.group(1).split()]
        self.assertIn("prompts", members)


# --------------------------------------------------------------------- config ---

class BoardPromptsConfigTest(_PromptsBase):
    def test_default_on(self):
        self.assertTrue(config.board_prompts_enabled())

    def test_config_set_and_get(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(_Args(workspace_dirs=None, board_prompts="off"))
        self.assertIn("board_prompts = off", buf.getvalue())
        self.assertFalse(config.board_prompts_enabled())

    def test_env_overrides_config(self):
        config.set("board_prompts", False)
        os.environ["TASK_STATION_BOARD_PROMPTS"] = "on"
        self.assertTrue(config.board_prompts_enabled())


# --------------------------------------------- human filter + Claude replies ---

class PromptRepliesTest(_PromptsBase):
    """The curated view: human-only prompts, each with Claude's last-bullet reply read
    from the transcript. Repoints PROJECTS_ROOT so a written transcript is discoverable."""

    def setUp(self):
        super().setUp()
        self.proj = os.path.join(self.tmp, "projects")
        self._orig_proot = ts.PROJECTS_ROOT
        ts.PROJECTS_ROOT = self.proj

    def tearDown(self):
        ts.PROJECTS_ROOT = self._orig_proot
        super().tearDown()

    def _write_transcript(self, sid, lines):
        bucket = os.path.join(self.proj, "-bucket")
        os.makedirs(bucket, exist_ok=True)
        with open(os.path.join(bucket, sid + ".jsonl"), "w", encoding="utf-8") as f:
            for o in lines:
                f.write(json.dumps(o) + "\n")

    @staticmethod
    def _u(uuid_, text):
        return {"type": "user", "uuid": uuid_,
                "message": {"role": "user", "content": text}}

    @staticmethod
    def _a(text):
        return {"type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}

    @staticmethod
    def _tool_result():
        return {"type": "user", "message": {"role": "user",
                "content": [{"type": "tool_result", "content": "ok"}]}}

    def test_last_bullet_reply_variants(self):
        self.assertEqual(ts._last_bullet_reply("intro\n- one\n- final bullet"), "final bullet")
        # no bullets → the whole LAST PARAGRAPH (not just the last line)
        self.assertEqual(ts._last_bullet_reply("just a line\nlast prose line"),
                         "just a line\nlast prose line")
        self.assertEqual(ts._last_bullet_reply("intro para\n\nfinal para line 1\nline 2"),
                         "final para line 1\nline 2")
        self.assertEqual(ts._last_bullet_reply("1. a\n2. **bold** done"), "bold done")
        self.assertEqual(ts._last_bullet_reply(""), "")

    def test_last_bullet_reply_keeps_tail_after_last_bullet(self):
        # the ENTIRE response tail from the last bullet onward is kept, line breaks intact.
        text = "prose intro\n- first bullet\n- last bullet\ncontinuation line\n\nclosing prose"
        self.assertEqual(ts._last_bullet_reply(text),
                         "last bullet\ncontinuation line\nclosing prose")

    def test_assistant_text_joins_text_blocks_only(self):
        msg = {"content": [{"type": "text", "text": "hi"},
                           {"type": "tool_use", "name": "x"},
                           {"type": "text", "text": "bye"}]}
        self.assertEqual(ts._assistant_text(msg), "hi\nbye")

    def test_prompt_replies_last_block_across_tool_roundtrip(self):
        hub = "18521696aaaa"
        self._write_transcript(hub, [
            self._u("u1", "review our plugin"),
            self._a("looking...\n- interim note"),
            self._tool_result(),                                # a tool round-trip mid-turn
            self._a("done\n- FIRST real conclusion\n- LAST real conclusion"),
            self._u("u4", "modify our plugin"),
            self._a("changed it\n- second-turn conclusion"),
        ])
        rep = ts._prompt_replies(hub, {"u1", "u4"})
        self.assertEqual(rep["u1"], "LAST real conclusion")     # last block, last bullet
        self.assertEqual(rep["u4"], "second-turn conclusion")

    def test_format_prompts_shows_human_and_reply(self):
        t = self._seed(self._task())                            # u1/u4 hub, u3 worker (+cmd/compact)
        self._write_transcript("18521696aaaa", [
            self._u("u1", "review our plugin against the top plugins"),
            self._a("analysis\n- ship the prompts view first"),
            self._u("u4", "modify our plugin so it tracks prompts"),
            self._a("wired it up\n- prompts now persisted"),
        ])
        self._write_transcript("workersid0002", [
            self._u("u3", "worker brief: build the prompts view"),
            self._a("built it\n- view renders"),
        ])
        out = ts._format_prompts(t, usage.task_prompts(ts._backend(), t))
        self.assertIn("review our plugin against the top plugins", out)
        self.assertIn("↳ ship the prompts view first", out)     # Claude's last bullet
        self.assertIn("↳ prompts now persisted", out)
        self.assertIn("↳ view renders", out)
        self.assertNotIn("/todo save", out)                     # command still filtered

    def test_human_filter_drops_commands_and_compact(self):
        t = self._seed(self._task())
        rows = ts._human_prompts_with_replies(usage.task_prompts(
            ts._backend(), t, include_compact=True))
        self.assertEqual([r["uuid"] for r in rows], ["u1", "u3", "u4"])  # no u2(cmd)/u5(compact)

    def test_prompt_is_human_filters_command_body(self):
        # The expanded body of a slash command is recorded as a user turn (kind=prompt)
        # and leads with an HTML comment / managed marker — must NOT count as human.
        self.assertFalse(ts._prompt_is_human(
            "prompt", "<!-- task-station-managed: bare alias for /task-station:todo -->\n---\n..."))
        self.assertFalse(ts._prompt_is_human("prompt", "/todo 362"))
        self.assertFalse(ts._prompt_is_human("prompt", "<command-name>/save</command-name>"))
        self.assertTrue(ts._prompt_is_human("prompt", "please build the prompts view"))

    def test_prompt_is_human_filters_harness_and_skill_noise(self):
        # generated user-role turns that no human typed — all must read as generated.
        self.assertFalse(ts._prompt_is_human(
            "prompt", "<task-notification> <task-id>abc</task-id> done"))
        self.assertFalse(ts._prompt_is_human(
            "prompt", "<system-reminder>background stuff</system-reminder>"))
        self.assertFalse(ts._prompt_is_human(
            "prompt", "<SUBAGENT-STOP>\nIf you were dispatched…"))
        self.assertFalse(ts._prompt_is_human(
            "prompt", "Base directory for this skill: /Users/x/.claude/plugins/cache/superpowers"))
        self.assertFalse(ts._prompt_is_human("prompt", "[Request interrupted by user]"))
        # …but ordinary human text is untouched, including math like `a<b`.
        self.assertTrue(ts._prompt_is_human("prompt", "why is a<b here, fix the compare"))


if __name__ == "__main__":
    unittest.main()
