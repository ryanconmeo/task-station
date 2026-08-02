"""A-3 — note/export contract v2.

Drives the generic `task-station export` end-to-end and asserts the v2 contract:
  * frontmatter carries schema-version: 2, uuid, and a real `closed` date (closed_ts);
  * ## Related links are resolvable [[stem|title]] whose stem is a file in the SAME
    export dir (no dangling links);
  * the universal graph (touches-same / lineage) ships with the knowledge gate OFF,
    while ZERO co-citation/knowledge links leak anywhere;
  * exported ## Prompts lines carry the session attribution the MCP get_prompts view
    shows;
  * byte-parity between the generic export and the Obsidian vault mirror (one
    renderer, same inputs).

Temp-home isolation + a synthetic transcript through the real ledger, mirroring
tests/test_export_bridge.py.
"""
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
from datetime import datetime, timezone

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

import store        # noqa: E402
import config       # noqa: E402
import obsidian_sync  # noqa: E402

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
        d = dict(dir=None, task=None, all=False, status=None, include=None, since=None,
                 sync_all=False, flush=False, quiet=False)
        d.update(kw)
        self.__dict__.update(d)


class _Base(unittest.TestCase):
    def setUp(self):
        for v in ("TASK_STATION_USAGE_TRACKING", "TASK_STATION_USAGE_PROMPTS",
                  "TASK_STATION_OBSIDIAN_PROMPTS", "TASK_STATION_KNOWLEDGE_GRAPH"):
            os.environ.pop(v, None)
        self.tmp = tempfile.mkdtemp(prefix="export-v2-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.PROJECTS_ROOT = os.path.join(self.tmp, "projects")
        ts.DELEGATE_REGISTRY = os.path.join(self.tmp, "workers.json")
        self.bucket = os.path.join(ts.PROJECTS_ROOT, "-proj")
        os.makedirs(self.bucket, exist_ok=True)
        self.out = os.path.join(self.tmp, "brain")
        store.reset_cache()

    def tearDown(self):
        store.reset_cache()
        for v in ("TASK_STATION_HOME", "TASK_STATION_OBSIDIAN_PROMPTS",
                  "TASK_STATION_KNOWLEDGE_GRAPH"):
            os.environ.pop(v, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_session(self, sid, lines):
        with open(os.path.join(self.bucket, sid + ".jsonl"), "w") as f:
            for o in lines:
                f.write(json.dumps(o) + "\n")

    def _seed(self, title, sid, *, prompt=None, prs=None, decisions=None, status="open"):
        t = ts.new_task(title, "summary of " + title)
        t["sessions"] = [sid]
        if prs:
            t["prs"] = prs
        if decisions:
            t["decisions"] = decisions
        if status != "open":
            t["status"] = status
        ts.save_task(t)
        ts.ensure_seqs()
        lines = [_asst(OPUS, out=200), _asst(FABLE, out=800)]
        if prompt:
            lines.append(_user(prompt, "u-" + sid))
        self._write_session(sid, lines)
        ts._usage_engine().refresh_task(ts._backend(), ts.load_task(t["id"]))
        return ts.load_task(t["id"])

    def _export(self, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_export(_Args(dir=self.out, **kw))
        return buf.getvalue()

    def _read(self, d, fname):
        with open(os.path.join(d, fname), encoding="utf-8") as f:
            return f.read()


class FrontmatterV2(_Base):
    def test_schema_uuid_and_closed(self):
        a = self._seed("Open task", "s1")
        b = self._seed("Closed task", "s2", status="closed")
        # give the closed task a real close stamp the way the engine does
        bt = ts.load_task(b["id"])
        bt["closed_ts"] = 1_700_200_000.0
        ts.save_task(bt)
        self._export(all=True, include="usage")
        atext = self._read(self.out, "%s-open-task.md" % a["seq"])
        self.assertIn("schema-version: 2", atext)
        self.assertIn('uuid: "%s"' % a["uuid"], atext)
        self.assertIn('closed: ""', atext)           # open ⇒ empty
        btext = self._read(self.out, "%s-closed-task.md" % b["seq"])
        self.assertRegex(btext, r"closed: \d{4}-\d{2}-\d{2}")
        self.assertNotIn('closed: ""', btext)


class RelatedResolves(_Base):
    def _wikilink_stems(self, text):
        rel = text.split("## Related", 1)
        if len(rel) == 1:
            return []
        return re.findall(r"\[\[([^\]|#]+)", rel[1])

    def test_related_present_and_every_stem_resolves(self):
        a = self._seed("Alpha", "s1", prs=[{"url": "https://x/pr/7"}])
        b = self._seed("Beta", "s2", prs=[{"url": "https://x/pr/7"}])
        self._export(all=True, include="usage")
        atext = self._read(self.out, "%s-alpha.md" % a["seq"])
        self.assertIn("## Related", atext)
        # resolvable [[stem|title]] — the stem is B's note file in this dir
        self.assertIn("[[%s-beta|Beta]] — touches same" % b["seq"], atext)
        # EVERY related wikilink stem resolves to a file in the SAME export dir
        for note in (atext, self._read(self.out, "%s-beta.md" % b["seq"])):
            for stem in self._wikilink_stems(note):
                self.assertTrue(os.path.exists(os.path.join(self.out, stem + ".md")),
                                "dangling Related stem: %s" % stem)

    def test_gate_off_zero_knowledge_leak(self):
        # both tasks cite the same [[shared-note]] AND share a PR
        self._seed("Alpha", "s1", prs=[{"url": "p9"}],
                   decisions=["per [[shared-note]]"])
        self._seed("Beta", "s2", prs=[{"url": "p9"}],
                   decisions=["also [[shared-note]]"])
        self.assertFalse(config.knowledge_graph_enabled())
        self._export(all=True, include="usage")
        for f in os.listdir(self.out):
            if not f.endswith(".md"):
                continue
            text = self._read(self.out, f)
            # touches-same universal edge SHOWS; co-citation knowledge leaks nowhere
            if "## Related" in f or "## Related" in text:
                self.assertNotIn("[[shared-note]]", text.split("## Related", 1)[-1]
                                 if "## Related" in text else "")
            self.assertNotIn("knowledge", text.split("## Related", 1)[-1]
                             if "## Related" in text else "")

    def test_gate_on_adds_knowledge(self):
        os.environ["TASK_STATION_KNOWLEDGE_GRAPH"] = "on"
        self._seed("Alpha", "s1", decisions=["per [[shared-note]]"])
        self._seed("Beta", "s2", decisions=["also [[shared-note]]"])
        self._export(all=True, include="usage")
        atext = self._read(self.out, "1-alpha.md")
        self.assertIn("## Related", atext)
        self.assertIn("[[shared-note]] — knowledge", atext)


class PromptsSessionAttribution(_Base):
    def test_prompts_carry_session_id(self):
        t = self._seed("Prompted", "sessABCDEF01", prompt="Do the thing")
        self._export(all=True, include="usage,prompts")
        text = self._read(self.out, "%s-prompted.md" % t["seq"])
        self.assertIn("## Prompts", text)
        self.assertIn("Do the thing", text)
        # the short 8-char sid appears in the attribution (matches get_prompts view)
        self.assertIn("sessABCD", text)


class ByteParityVaultVsExport(_Base):
    def test_export_note_byte_identical_to_vault_mirror(self):
        vault = os.path.join(self.tmp, "vault")
        os.makedirs(vault, exist_ok=True)
        config.set("obsidian_vault", vault)
        config.set("obsidian_prompts", True)     # match export's include prompts
        try:
            a = self._seed("Alpha", "s1", prs=[{"url": "p1"}], prompt="hello world")
            b = self._seed("Beta", "s2", prs=[{"url": "p1"}])
            # generic export (usage+history+prompts) → out dir
            self._export(all=True, include="usage,prompts,history")
            # vault mirror of every task
            buf = io.StringIO()
            with redirect_stdout(buf):
                ts.cmd_obsidian(_Args(sync_all=True))
            pdir = obsidian_sync.plugin_dir(vault)
            for t in (a, b):
                stem = "%s-%s" % (t["seq"], obsidian_sync.slugify(t["title"]))
                exp = self._read(self.out, stem + ".md")
                vlt = self._read(pdir, stem + ".md")
                self.assertEqual(exp, vlt, "export/vault drift for %s" % stem)
                self.assertIn("## Related", exp)   # both carry the graph
        finally:
            config.unset("obsidian_vault")
            config.unset("obsidian_prompts")


if __name__ == "__main__":
    unittest.main()
