"""Tasktrail reference consumer #2 (A-7): spec/consumers/tasktrail-digest.py.

Guarantees: fixture replay is byte-identical to the committed expected digest;
tombstones drop the task; a manifest generation bump forces a full re-sync that
excludes redacted content; double-runs are byte-identical; the consumer writes
only inside its target dir; it is stdlib-only.
"""
import importlib.util
import json
import os
import shutil
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = os.path.join(ROOT, "spec")
FIXTURE = os.path.join(SPEC, "fixtures", "tasktrail")
EXPECTED = os.path.join(SPEC, "fixtures", "expected-digest.md")

_spec = importlib.util.spec_from_file_location(
    "tasktrail_digest", os.path.join(SPEC, "consumers", "tasktrail-digest.py"))
dig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dig)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class FixtureReplay(unittest.TestCase):
    def setUp(self):
        self.out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.out, ignore_errors=True)

    def test_replay_byte_identical_to_expected(self):
        dig.sync(FIXTURE, self.out)
        self.assertEqual(_read(os.path.join(self.out, "digest.md")), _read(EXPECTED))

    def test_double_run_byte_identical(self):
        dig.sync(FIXTURE, self.out)
        first = _read(os.path.join(self.out, "digest.md"))
        dig.sync(FIXTURE, self.out)                    # resume from cursor
        self.assertEqual(_read(os.path.join(self.out, "digest.md")), first)

    def test_tombstone_and_redaction_absent(self):
        dig.sync(FIXTURE, self.out)
        text = _read(os.path.join(self.out, "digest.md"))
        self.assertNotIn("Scratch experiment", text)   # t-0003 deleted
        state = json.loads(_read(os.path.join(self.out, ".tasktrail-digest-state.json")))
        uuids = {t["uuid"] for t in state["tasks"]}
        self.assertNotIn("t-0003", uuids)              # tombstone dropped it
        self.assertNotIn("t-0002", uuids)              # redacted dropped it

    def test_only_writes_target_dir(self):
        dig.sync(FIXTURE, self.out)
        got = set(os.listdir(self.out))
        self.assertEqual(got, {"digest.md", ".tasktrail-digest-state.json"})

    def test_stdlib_only(self):
        src = _read(os.path.join(SPEC, "consumers", "tasktrail-digest.py"))
        for banned in ("import yaml", "jsonschema", "import requests"):
            self.assertNotIn(banned, src)


class GenerationBumpResync(unittest.TestCase):
    """A task synced at generation 1, then redacted (generation 2), must vanish from
    the digest on the next run via full re-sync."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.stream = os.path.join(self.tmp, "tasktrail")
        self.out = os.path.join(self.tmp, "out")
        os.makedirs(os.path.join(self.stream, "events"))
        os.makedirs(os.path.join(self.stream, "notes"))

    def _manifest(self, gen):
        _write(os.path.join(self.stream, "tasktrail.json"),
               json.dumps({"spec_version": "1.0", "producer": "task-station/test",
                           "generation": gen}, indent=2, sort_keys=True) + "\n")

    def _note(self):
        _write(os.path.join(self.stream, "notes", "7-secret.md"),
               '---\nmanaged-by: task-station\nschema-version: 2\nuuid: "t-X"\n'
               'seq: 7\nstatus: "open"\ncreated: 2025-01-01\nupdated: 2025-01-01\n'
               'closed: ""\ntitle: "Secret task"\n---\n\n## Goal\n_(none)_\n\n'
               '## State\nsensitive detail\n')

    def _events(self, lines):
        _write(os.path.join(self.stream, "events", "2025-01.jsonl"),
               "".join(json.dumps(e, sort_keys=True) + "\n" for e in lines))

    def test_generation_bump_resyncs_out_redacted_task(self):
        created = {"v": 1, "ts": "2025-01-01T00:00:00+00:00", "n": 1,
                   "event": "task.created", "task": {"uuid": "t-X", "seq": 7},
                   "actor": {"session": None},
                   "data": {"title": "Secret task", "status": "open"}}
        # --- generation 1: task present ---
        self._manifest(1)
        self._note()
        self._events([created])
        dig.sync(self.stream, self.out)
        self.assertIn("Secret task", _read(os.path.join(self.out, "digest.md")))

        # --- redact: stub payload, drop note, bump generation, add marker ---
        self._manifest(2)
        os.remove(os.path.join(self.stream, "notes", "7-secret.md"))
        stub = dict(created, data={"redacted": True})
        marker = {"v": 1, "ts": "2025-01-02T00:00:00+00:00", "n": 2,
                  "event": "task.redacted", "task": {"uuid": "t-X", "seq": 7},
                  "actor": {"session": None}, "data": {"generation": 2}}
        self._events([stub, marker])
        dig.sync(self.stream, self.out)                # same out dir → detects bump

        text = _read(os.path.join(self.out, "digest.md"))
        self.assertNotIn("Secret task", text)
        self.assertNotIn("sensitive detail", text)
        state = json.loads(_read(os.path.join(self.out, ".tasktrail-digest-state.json")))
        self.assertEqual(state["generation"], 2)
        self.assertEqual([t for t in state["tasks"] if t["uuid"] == "t-X"], [])


if __name__ == "__main__":
    unittest.main()
