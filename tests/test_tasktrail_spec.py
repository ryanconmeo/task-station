"""Tasktrail spec v1.0 conformance (A-6).

Three guarantees:
  * the committed golden fixture bundle validates clean;
  * deliberately corrupted copies of it FAIL with actionable messages;
  * task-station's OWN freshly generated export+stream — assembled into the
    published tasktrail/ layout — passes its own published validator (producer
    self-conformance), including a checkpoint that carries the first-class
    glossary[] + brief_path digest fields.
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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "lib")
SPEC = os.path.join(ROOT, "spec")
FIXTURE = os.path.join(SPEC, "fixtures", "tasktrail")
sys.path.insert(0, LIB)
sys.path.insert(0, SPEC)

import validate as v          # noqa: E402  (spec/validate.py)
import store                  # noqa: E402
import stream                 # noqa: E402
import export as _export      # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class Fixtures(unittest.TestCase):
    def test_golden_bundle_validates(self):
        self.assertEqual(v.validate_dir(FIXTURE), [])

    def test_schema_files_are_valid_json(self):
        for name in ("tasktrail.event.schema.json", "task-note.frontmatter.schema.json"):
            with open(os.path.join(SPEC, name), encoding="utf-8") as f:
                doc = json.load(f)
            self.assertEqual(doc["$schema"],
                             "https://json-schema.org/draft/2020-12/schema")

    def test_manifest_declares_spec_version(self):
        with open(os.path.join(FIXTURE, "tasktrail.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["spec_version"], v.SPEC_VERSION)

    def _corrupt_copy(self):
        tmp = tempfile.mkdtemp()
        dst = os.path.join(tmp, "tasktrail")
        shutil.copytree(FIXTURE, dst)
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        return dst

    def test_corrupt_gap_in_n_fails(self):
        dst = self._corrupt_copy()
        p = os.path.join(dst, "events", "2025-06.jsonl")
        lines = _read(p).splitlines()
        env = json.loads(lines[0])
        env["n"] = 5                       # break t-0001 gaplessness (was 1)
        lines[0] = json.dumps(env, sort_keys=True)
        _write(p, "\n".join(lines) + "\n")
        errs = v.validate_dir(dst)
        self.assertTrue(any("not gapless" in e for e in errs), errs)

    def test_corrupt_bad_ts_fails(self):
        dst = self._corrupt_copy()
        p = os.path.join(dst, "events", "2025-07.jsonl")
        lines = _read(p).splitlines()
        env = json.loads(lines[0])
        env["ts"] = "2025-07-01 08:00:00"  # not ISO8601 UTC
        lines[0] = json.dumps(env, sort_keys=True)
        _write(p, "\n".join(lines) + "\n")
        errs = v.validate_dir(dst)
        self.assertTrue(any("ts must be UTC ISO8601" in e for e in errs), errs)

    def test_corrupt_note_schema_version_fails(self):
        dst = self._corrupt_copy()
        p = os.path.join(dst, "notes", "1-ship-stream-contract.md")
        _write(p, _read(p).replace("schema-version: 2", "schema-version: 1"))
        errs = v.validate_dir(dst)
        self.assertTrue(any("schema-version must be 2" in e for e in errs), errs)

    def test_corrupt_manifest_generation_fails(self):
        dst = self._corrupt_copy()
        p = os.path.join(dst, "tasktrail.json")
        m = json.loads(_read(p))
        m["generation"] = 0
        _write(p, json.dumps(m))
        errs = v.validate_dir(dst)
        self.assertTrue(any("generation must be an integer" in e for e in errs), errs)


class SelfConformance(unittest.TestCase):
    """Generate REAL export+stream output and run it through the published validator."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        os.environ["TASK_STATION_STREAM"] = "on"
        os.environ.pop("TASK_STATION_STREAM_DIR", None)
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.PROJECTS_ROOT = os.path.join(self.tmp, "projects")
        store.reset_cache()

    def tearDown(self):
        store.reset_cache()
        for k in ("TASK_STATION_HOME", "TASK_STATION_STREAM", "TASK_STATION_STREAM_DIR"):
            os.environ.pop(k, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _create(self, title, **kw):
        a = _Args(title=title, summary=kw.get("summary", ""), color=None,
                  effort=None, goal=kw.get("goal"), step=None, session=None,
                  no_attach=True, attach=False, active=False, force=True)
        with redirect_stdout(io.StringIO()):
            ts.cmd_create(a)
        return sorted(ts.all_tasks(), key=lambda t: t.get("seq"))[-1]

    def test_real_export_plus_stream_conforms(self):
        # --- drive real mutations across the choke points ------------------
        t1 = self._create("Ship stream contract", goal="Publish v1.0")
        t2 = self._create("Ongoing work", goal="Keep the lights on")

        base = dict(title=None, summary=None, append_summary=None, state=None,
                    goal=None, step_add=None, step_done=None, step_undone=None,
                    decision=None, log=None, relate=None, pr=None, pr_desc=None,
                    story=None, story_desc=None, color=None, effort=None, session=None)
        with redirect_stdout(io.StringIO()):
            ts.cmd_update(_Args(task=str(t1["seq"]),
                                **dict(base, state="drafting", summary="taking shape")))
            ts.cmd_add_event(_Args(task=str(t2["seq"]), kind="worker",
                                   text="picked it up", session="sess-1"))

        # Checkpoint carrying the first-class structured glossary[] + brief_path.
        ts.set_link("sess-1", t1["id"])

        def _seed(task):
            task["glossary"] = [{"name": "Shard", "layer": "raw", "state": "stable",
                                 "def": "A monthly YYYY-MM.jsonl file of events"}]
            task["brief_path"] = "briefs/ship-stream-contract.md"
        ts.mutate(t1["id"], _seed)
        with redirect_stdout(io.StringIO()):
            ts._todo_save(_Args(session="sess-1"), [])
            ts.cmd_done(_Args(task=str(t1["seq"]), session=None))   # task.status

        self.assertTrue(stream.verify()["ok"], stream.verify()["issues"])

        # --- assemble the published tasktrail/ bundle ---------------------
        bundle = os.path.join(self.tmp, "bundle")
        notes = os.path.join(bundle, "notes")
        os.makedirs(notes, exist_ok=True)
        # 1) stream manifest + shards (producer writes them under DATA/stream)
        src_stream = os.path.join(self.tmp, "stream")
        shutil.copy(os.path.join(src_stream, "tasktrail.json"),
                    os.path.join(bundle, "tasktrail.json"))
        shutil.copytree(os.path.join(src_stream, "events"),
                        os.path.join(bundle, "events"))
        # 2) real export → notes/*.md + index.md
        exp = os.path.join(self.tmp, "export")
        os.makedirs(exp, exist_ok=True)
        entries = _export.export_tasks(exp, ts.all_tasks(), ts._backend())
        self.assertTrue(entries)
        for fn in os.listdir(exp):
            if fn.endswith(".md") and fn != "index.md":
                shutil.copy(os.path.join(exp, fn), os.path.join(notes, fn))
        shutil.copy(os.path.join(exp, "index.md"), os.path.join(bundle, "index.md"))

        # --- the producer's own output passes its published validator ------
        errs = v.validate_dir(bundle)
        self.assertEqual(errs, [], "self-conformance failures:\n" + "\n".join(errs))

        # the checkpoint really carried the structured first-class fields
        cps = [e for e in stream.read_events() if e["event"] == "task.checkpoint"]
        self.assertEqual(len(cps), 1)
        g = cps[0]["data"]["glossary"][0]
        self.assertEqual(set(g), {"name", "layer", "state", "def"})
        self.assertEqual(cps[0]["data"]["brief_path"], "briefs/ship-stream-contract.md")

    def _assemble_bundle(self):
        """Copy the producer's REAL stream + export output into a published
        tasktrail/ bundle and return its dir."""
        bundle = os.path.join(self.tmp, "bundle")
        notes = os.path.join(bundle, "notes")
        os.makedirs(notes, exist_ok=True)
        src_stream = os.path.join(self.tmp, "stream")
        shutil.copy(os.path.join(src_stream, "tasktrail.json"),
                    os.path.join(bundle, "tasktrail.json"))
        shutil.copytree(os.path.join(src_stream, "events"),
                        os.path.join(bundle, "events"))
        exp = os.path.join(self.tmp, "export")
        os.makedirs(exp, exist_ok=True)
        entries = _export.export_tasks(exp, ts.all_tasks(), ts._backend())
        self.assertTrue(entries)
        for fn in os.listdir(exp):
            if fn.endswith(".md") and fn != "index.md":
                shutil.copy(os.path.join(exp, fn), os.path.join(notes, fn))
        shutil.copy(os.path.join(exp, "index.md"), os.path.join(bundle, "index.md"))
        return bundle

    def test_glossary_and_brief_commands_conform_end_to_end(self):
        # Populate glossary + brief_path through the REAL WS3 command paths, then
        # verify the producer's export+stream still validates against the published
        # schemas end-to-end (task-note.frontmatter + tasktrail.event).
        t = self._create("Collation strategy", goal="Rebuild + Centralize")
        ts.set_link("sess-g", t["id"])

        def _gargs(**kw):
            base = dict(action="list", args=[], task=str(t["seq"]), session="sess-g",
                        layer=None, state=None, definition=None, rename=None)
            base.update(kw)
            return _Args(**base)

        with redirect_stdout(io.StringIO()):
            ts.cmd_glossary(_gargs(action="add",
                                   args=["Binary-Default (BIN2) Store", "db", "target",
                                         "All text case-sensitive by default."]))
            ts.cmd_glossary(_gargs(action="add",
                                   args=["Collation Gate", "CI", "shipped",
                                         "A build check that blocks bypass."]))
            # render a real brief → sets task['brief_path'] via mutate
            spec_path = os.path.join(self.tmp, "spec.json")
            _write(spec_path, json.dumps({
                "title": "Collation — Strategy",
                "decision": {"label": "Decision", "body": "Rebuild + Centralize the store."},
                "glossary": "auto",
                "provenance": "PR 931.",
            }))
            ts.cmd_brief(_Args(action="render", task=str(t["seq"]),
                               session="sess-g", spec=spec_path))
            ts._todo_save(_Args(session="sess-g"), [])

        self.assertTrue(stream.verify()["ok"], stream.verify()["issues"])

        # the task carries real structured glossary data + a brief_path
        saved = ts.load_task(t["id"])
        self.assertEqual(len(saved["glossary"]), 2)
        self.assertEqual(saved["glossary"][0]["name"], "Binary-Default (BIN2) Store")
        self.assertTrue(saved["brief_path"].endswith("brief.html"))

        # the producer's own output passes its published validator end-to-end
        bundle = self._assemble_bundle()
        errs = v.validate_dir(bundle)
        self.assertEqual(errs, [], "self-conformance failures:\n" + "\n".join(errs))

        # the checkpoint digest carried the structured glossary + brief_path
        cps = [e for e in stream.read_events() if e["event"] == "task.checkpoint"]
        self.assertTrue(cps)
        data = cps[-1]["data"]
        self.assertEqual(len(data["glossary"]), 2)
        self.assertEqual(set(data["glossary"][0]), {"name", "layer", "state", "def"})
        self.assertTrue(data["brief_path"].endswith("brief.html"))


if __name__ == "__main__":
    unittest.main()
