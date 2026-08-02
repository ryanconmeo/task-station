"""WS8 export bridge — the generic `task-station export --dir` command + the
lib/export.py core. Drives cmd_export end-to-end (in-process _Args +
redirect_stdout, temp-home isolation, matching the existing CLI tests) and
unit-tests export.parse_include / filter_since. Usage + prompts are seeded through
the real ledger (write a synthetic transcript, refresh_task), so the exported
notes carry live-shaped data.
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

import store        # noqa: E402
import config       # noqa: E402
import export       # noqa: E402

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)

OPUS = "claude-opus-4-8"
FABLE = "claude-fable-5"


def _iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def _asst(model, out, inp=1000, ts_=1000):
    return {"type": "assistant", "timestamp": _iso(ts_), "cwd": "/proj",
            "entrypoint": "cli",
            "message": {"model": model,
                        "usage": {"input_tokens": inp, "output_tokens": out,
                                  "cache_read_input_tokens": 0,
                                  "cache_creation_input_tokens": 0}}}


def _user(text, uid, ts_=1005):
    return {"type": "user", "uuid": uid, "timestamp": _iso(ts_),
            "message": {"content": text}}


class _Args:
    def __init__(self, **kw):
        d = dict(dir=None, task=None, all=False, status=None, include=None, since=None)
        d.update(kw)
        self.__dict__.update(d)


class _Base(unittest.TestCase):
    def setUp(self):
        for v in ("TASK_STATION_USAGE_TRACKING", "TASK_STATION_USAGE_PROMPTS",
                  "TASK_STATION_OBSIDIAN_PROMPTS", "TASK_STATION_USAGE_BILLING_MODE"):
            os.environ.pop(v, None)
        self.tmp = tempfile.mkdtemp(prefix="export-bridge-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.PROJECTS_ROOT = os.path.join(self.tmp, "projects")
        ts.DELEGATE_REGISTRY = os.path.join(self.tmp, "workers.json")
        self.bucket = os.path.join(ts.PROJECTS_ROOT, "-proj")
        os.makedirs(self.bucket, exist_ok=True)
        self.out = os.path.join(self.tmp, "brain")     # export destination
        store.reset_cache()

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_session(self, sid, lines):
        with open(os.path.join(self.bucket, sid + ".jsonl"), "w") as f:
            for o in lines:
                f.write(json.dumps(o) + "\n")

    def _seed(self, title, sid, *, prompt=None, status="open", updated_ts=None):
        t = ts.new_task(title, "summary of " + title)
        t["sessions"] = [sid]
        t["cost"] = {"total_usd": 6.0, "runs": 1}
        if status != "open":
            t["status"] = status
        if updated_ts is not None:
            t["updated_ts"] = updated_ts
        ts.save_task(t)
        ts.ensure_seqs()
        lines = [_asst(OPUS, out=200), _asst(FABLE, out=800)]
        if prompt:
            lines.append(_user(prompt, "u-" + sid))
        self._write_session(sid, lines)
        ts._usage_engine().refresh_task(ts._backend(), ts.load_task(t["id"]))
        return ts.load_task(t["id"])

    def _run(self, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_export(_Args(dir=self.out, **kw))
        return buf.getvalue()

    def _notes(self):
        return sorted(f for f in os.listdir(self.out)
                      if f.endswith(".md") and f != "index.md")

    def _read(self, fname):
        with open(os.path.join(self.out, fname), encoding="utf-8") as f:
            return f.read()


class ExportCommand(_Base):
    def test_all_writes_notes_and_index_with_wikilinks(self):
        a = self._seed("Fix the login bug", "s1")
        b = self._seed("Add export command", "s2")
        out = self._run(all=True, include="usage")
        notes = self._notes()
        self.assertEqual(len(notes), 2)
        self.assertIn("Exported 2 tasks", out)
        # index.md exists and wikilinks each note by its stem, with #seq · title alias
        idx = self._read("index.md")
        self.assertIn("managed-by: task-station", idx)
        self.assertIn("count: 2", idx)
        self.assertIn("[[%s-fix-the-login-bug|#%s · Fix the login bug]]"
                      % (a["seq"], a["seq"]), idx)
        self.assertIn("[[%s-add-export-command|#%s · Add export command]]"
                      % (b["seq"], b["seq"]), idx)

    def test_no_vault_config_required(self):
        self.assertEqual(config.obsidian_vault(), "")     # export is fully independent
        self._seed("Standalone", "s1")
        self._run(all=True, include="usage")
        self.assertEqual(len(self._notes()), 1)

    def test_usage_section_populated(self):
        t = self._seed("With usage", "s1")
        self._run(all=True, include="usage")
        text = self._read("%s-with-usage.md" % t["seq"])
        self.assertIn("## Usage", text)
        self.assertIn("fable", text)          # model family from the ledger
        self.assertIn("derived", text)
        self.assertIn("$6.00 reported", text)  # delegate cross-check
        # usage frontmatter keys present + flat
        self.assertIn('models:\n  - "%s"' % FABLE, text)   # fable dominates (800 out)
        self.assertRegex(text, r"cost-usd: [0-9]")

    def test_prompts_only_with_include(self):
        t = self._seed("Prompted task", "s1", prompt="Please implement the thing")
        # without prompts in --include → no ## Prompts
        self._run(all=True, include="usage")
        self.assertNotIn("## Prompts", self._read("%s-prompted-task.md" % t["seq"]))
        # with it → the captured prompt trail appears
        self._run(all=True, include="usage,prompts")
        text = self._read("%s-prompted-task.md" % t["seq"])
        self.assertIn("## Prompts", text)
        self.assertIn("Please implement the thing", text)

    def test_since_filters(self):
        old = self._seed("Old task", "s1", updated_ts=1_600_000_000.0)   # 2020
        new = self._seed("New task", "s2", updated_ts=1_800_000_000.0)   # 2027
        self._run(all=True, include="usage", since="2026-01-01")
        notes = self._notes()
        self.assertIn("%s-new-task.md" % new["seq"], notes)
        self.assertNotIn("%s-old-task.md" % old["seq"], notes)

    def test_single_task(self):
        a = self._seed("First", "s1")
        self._seed("Second", "s2")
        self._run(task=str(a["seq"]), include="usage")
        self.assertEqual(self._notes(), ["%s-first.md" % a["seq"]])

    def test_status_filter(self):
        op = self._seed("Open one", "s1", status="open")
        self._seed("Closed one", "s2", status="closed")
        self._run(status="open", include="usage")
        self.assertEqual(self._notes(), ["%s-open-one.md" % op["seq"]])

    def test_default_include_is_usage_history_not_prompts(self):
        t = self._seed("Defaulted", "s1", prompt="secret prompt text")
        self._run(all=True)     # no --include → usage + history, NOT prompts
        text = self._read("%s-defaulted.md" % t["seq"])
        self.assertIn("## Usage", text)
        self.assertIn("## History", text)
        self.assertNotIn("## Prompts", text)
        self.assertNotIn("secret prompt text", text)

    def test_missing_dir_prints_usage(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_export(_Args(dir=None, all=True))
        self.assertIn("usage: task-station export", buf.getvalue())


class ExportHelpers(unittest.TestCase):
    def test_parse_include_default(self):
        self.assertEqual(export.parse_include(None), {"usage", "history"})
        self.assertEqual(export.parse_include(""), {"usage", "history"})

    def test_parse_include_explicit(self):
        self.assertEqual(export.parse_include("usage,prompts"), {"usage", "prompts"})
        self.assertEqual(export.parse_include("prompts"), {"prompts"})
        # unknown tokens ignored
        self.assertEqual(export.parse_include("usage,bogus"), {"usage"})

    def test_filter_since(self):
        tasks = [{"updated_ts": 1_600_000_000.0}, {"updated_ts": 1_800_000_000.0},
                 {"updated_ts": None}]
        kept = export.filter_since(tasks, "2026-01-01")
        self.assertEqual(len(kept), 2)   # the 2027 one + the ts-less one
        # unparseable/empty since ⇒ everything passes
        self.assertEqual(len(export.filter_since(tasks, "")), 3)
        self.assertEqual(len(export.filter_since(tasks, None)), 3)


if __name__ == "__main__":
    unittest.main()
