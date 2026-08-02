"""ts-fixes-398 board surfaces:
  #2 — the expanded card's Decisions + Summary each collapse by DEFAULT (their own
       <details> with no `open`), while the Overview section itself stays open.
  #3 — the board prompt trail shows ONLY human-typed prompts, each paired with
       Claude's last-bullet reply (`↳ …`), reusing the markdown/CLI machinery.
  #4 — the FULL human trail flows into the per-hub cards (not a 5-row preview).
Stdlib-only unittest; the view-model tests drive the SQLite ledger + a fixture
transcript (for reply attribution) the same way task-station does at runtime."""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(_ROOT, "lib")
TOOLS = os.path.join(_ROOT, "tools")
sys.path.insert(0, LIB)
sys.path.insert(0, TOOLS)

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)

import render_board  # noqa: E402


def _vm(**kw):
    """A minimal board view-model dict (every renderer field is `.get`-guarded)."""
    base = {
        "seq": 1, "title": "A task", "full_title": "A task",
        "status": "open", "status_label": "open",
        "usage": None, "sessions": [], "phases": [],
        "prompts_preview": [], "hubs": [], "cost_thresholds": [0.01, 0.05],
    }
    base.update(kw)
    return base


def _hub(prompts):
    return {"sid8": "hub12345", "session_id": "hub12345aaaa", "role": "hub",
            "msgs": 1, "age": "1h ago", "oneliner": "x", "pinned": False,
            "main": False, "live": False, "own": {}, "agg": {}, "reported": 0.0,
            "workers": [], "prompts": prompts, "prompt_count": len(prompts)}


# ============================================ fix #2: collapsed Decisions/Summary
class DecisionsSummaryCollapsed(unittest.TestCase):
    def _html(self):
        return render_board.render_html([_vm(
            decisions=["chose per-hub cards", "kept stdlib-only"],
            summary="the running summary prose")])

    def test_decisions_wrapped_and_collapsed(self):
        html = self._html()
        self.assertIn('<details class="ovsec ovsec-decisions">', html)
        # collapsed by default → NO open attribute on the Decisions sub-section.
        self.assertNotIn('<details class="ovsec ovsec-decisions" open', html)

    def test_summary_wrapped_and_collapsed(self):
        html = self._html()
        self.assertIn('<details class="ovsec ovsec-summary">', html)
        self.assertNotIn('<details class="ovsec ovsec-summary" open', html)

    def test_content_and_backcompat_classes_preserved(self):
        html = self._html()
        self.assertIn('class="summary"', html)             # existing contract
        self.assertIn('class="decisions longlist"', html)  # existing contract
        self.assertIn("chose per-hub cards", html)
        self.assertIn("the running summary prose", html)

    def test_overview_section_still_open(self):
        # the sections collapse PER-section; the Overview wrapper stays open.
        self.assertIn('<details class="sec sec-overview" open', self._html())


# ============================================= fix #3: reply pairing in render
class PromptReplyRendering(unittest.TestCase):
    def test_reply_rendered_as_arrow(self):
        html = render_board.render_html([_vm(hubs=[_hub([
            {"ts": 1, "kind": "prompt", "text": "make it collapse", "role": "hub",
             "human": True, "reply": "done — sections now fold"}])])])
        self.assertIn('class="preply"', html)
        self.assertIn("↳ done — sections now fold", html)

    def test_empty_reply_omits_arrow(self):
        html = render_board.render_html([_vm(hubs=[_hub([
            {"ts": 1, "kind": "prompt", "text": "no reply here", "role": "hub",
             "human": True, "reply": ""}])])])
        self.assertNotIn('class="preply"', html)

    def test_reply_html_escaped(self):
        html = render_board.render_html([_vm(hubs=[_hub([
            {"ts": 1, "kind": "prompt", "text": "x", "role": "hub", "human": True,
             "reply": "<b>bad</b>"}])])])
        self.assertNotIn("<b>bad</b>", html)
        self.assertIn("&lt;b&gt;bad&lt;/b&gt;", html)


# ================================ fix #3 + #4: board prompt-trail view-model ===
class BoardPromptTrailViewModel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        for k in ("TASK_STATION_USAGE_TRACKING", "TASK_STATION_BOARD_PROMPTS"):
            os.environ.pop(k, None)
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.store.reset_cache()
        self.proj = os.path.join(self.tmp, "projects")
        self._orig_proot = ts.PROJECTS_ROOT
        ts.PROJECTS_ROOT = self.proj

    def tearDown(self):
        ts.PROJECTS_ROOT = self._orig_proot
        os.environ.pop("TASK_STATION_HOME", None)
        for k in ("TASK_STATION_USAGE_TRACKING", "TASK_STATION_BOARD_PROMPTS"):
            os.environ.pop(k, None)
        ts.store.reset_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _task(self):
        t = ts.new_task("board trail", "summary", color="green", effort="m")
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _hub_session(self, t, sid):
        ts._backend().upsert_session_usage({
            "session_id": sid, "task_id": t["id"], "role": "hub", "label": None,
            "entrypoint": "cli", "first_ts": 1, "last_ts": 99,
            "models": {}, "sidechain": {}, "phases": {}})

    def _prompt(self, t, sid, uuid, ts_v, kind, text):
        ts._backend().upsert_prompt({"uuid": uuid, "session_id": sid,
                                     "task_id": t["id"], "ts": ts_v,
                                     "kind": kind, "text": text})

    def _write_transcript(self, session, lines):
        bucket = os.path.join(self.proj, "-fake-bucket")
        os.makedirs(bucket, exist_ok=True)
        with open(os.path.join(bucket, session + ".jsonl"), "w") as f:
            for o in lines:
                f.write(json.dumps(o) + "\n")

    def _all_hub_prompts(self, t):
        vm = ts._board_view_model(ts.load_task(t["id"]))
        return [p for h in vm["hubs"] for p in h["prompts"]]

    def test_human_only_with_last_bullet_reply(self):
        t = self._task()
        hub = "hubtrail00001"
        self._hub_session(t, hub)
        self._prompt(t, hub, "h1", 100, "prompt", "build the board")
        self._prompt(t, hub, "c1", 200, "command", "/todo save")   # generated → dropped
        self._write_transcript(hub, [
            {"type": "user", "uuid": "h1",
             "message": {"role": "user", "content": "build the board"}},
            {"type": "assistant",
             "message": {"role": "assistant", "content": [
                 {"type": "text", "text": "- wrote it\n- board is live now"}]}},
            {"type": "user", "uuid": "c1",
             "message": {"role": "user", "content": "/todo save"}},
        ])
        prompts = self._all_hub_prompts(t)
        texts = [p["text"] for p in prompts]
        self.assertIn("build the board", texts)
        self.assertNotIn("/todo save", texts)              # non-human excluded (fix #3)
        human = next(p for p in prompts if p["text"] == "build the board")
        self.assertTrue(human["human"])
        self.assertEqual(human["reply"], "board is live now")   # Claude's last bullet

    def test_reply_empty_when_no_transcript(self):
        # graceful: no transcript → the human prompt still shows, with an empty reply.
        t = self._task()
        hub = "hubtrail00003"
        self._hub_session(t, hub)
        self._prompt(t, hub, "h1", 100, "prompt", "no transcript here")
        human = next(p for p in self._all_hub_prompts(t)
                     if p["text"] == "no transcript here")
        self.assertEqual(human["reply"], "")

    def test_full_human_trail_not_capped(self):
        t = self._task()
        hub = "hubtrail00002"
        self._hub_session(t, hub)
        for i in range(8):                                  # >5 human prompts
            self._prompt(t, hub, "p%d" % i, 1000 + i, "prompt", "human prompt %d" % i)
        prompts = self._all_hub_prompts(t)
        texts = [p["text"] for p in prompts]
        for i in range(8):
            self.assertIn("human prompt %d" % i, texts)     # ALL of them (fix #4)
        self.assertEqual(len(prompts), 8)
        # and the rendered board shows the full count, not a 5-row preview.
        html = render_board.render_html([ts._board_view_model(ts.load_task(t["id"]))])
        self.assertIn("prompts (8)", html)


if __name__ == "__main__":
    unittest.main()
