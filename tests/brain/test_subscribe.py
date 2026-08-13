"""brain.subscribe — subscription memos (org-node change → memos to referencing
tasks), including THE ONE SANCTIONED brain→board edge.

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 4c) from the brain source tree's
``tests/test_subscriptions.py`` @ 0.14.0. All 16 source cases port; five of them
are REWRITTEN around the bridge, because the thing they drove no longer exists.

The source delivered memos by spawning task-station's published CLI, so its
fixture was a fake CLI script capturing ``argv`` to a file, and its assertions
were about argv. 3.0.0 calls ``board.memos.memo_send`` DIRECTLY, so the fixture
here is a REAL board store (the engine facade + a real task, built the way the
board's own suites build one) and the assertions read the memo back out of the
task through the same module that wrote it. That is a strictly stronger check:
the source proved the right process was invoked with the right arguments; this
proves a memo actually LANDED, readable by the board's own reader.

Renamed/rewritten with the bridge (each named in the chunk-4c handoff):
  * ``test_dirty_delivers_correct_memo_argv`` -> ``…delivers_a_readable_memo_to_each_task``
  * ``test_cli_absent_silent_skip_and_breadcrumb`` -> ``test_board_absent_…``
  * ``MemosEnabledTest::test_auto_on_when_cli_present`` -> ``…when_the_board_store_is_present``
    (the CLI limb of the detection is gone; the store limb is the source's own
    second limb and the one that survives)
  * the ``make_cli_stub`` / ``_calls`` helpers -> ``make_board`` / ``memos_on``
  * ``report_lines`` flags ``board_absent``, not ``cli_absent``

Env names come from chunk 1's ``TASK_STATION_BRAIN_*`` namespace, asserted against
``base.ENV_KEYS``.
"""
import ast
import importlib.util
import io
import json
import os
import subprocess
import unittest
from contextlib import redirect_stdout

from tests.brain.base import BrainTestCase, ENV_KEYS, LIB
from tests.brain.test_layers import STDLIB_OK, top_level_imports

import brain.errorlog as errorlog
import brain.notes as notes
import brain.references as references
import brain.search as search
import brain.subscribe as subscribe


NODE_V1 = """---
name: {slug}
description: d
verified: 2026-01-01
---

## Overview
first section.

## Filing cadence
quarterly.
"""

NODE_V2 = """---
name: {slug}
description: d
verified: 2026-07-24
---

## Overview
first section.

## Filing cadence — REVISED
monthly now.
"""


class SubsFixtureMixin(BrainTestCase):
    def setUp(self):
        super().setUp()
        self.vault = self.make_vault(self.home / "vault")
        self.clone = self.home / "org_brain"
        self.state = self.home / "state"
        self.cfg = {
            "vault": self.vault,
            "org_brain_clone": self.clone,
            "state_dir": self.state,
            "org_label": "org brain",
            # knowledge_memos / tasks_db filled in per test
        }

    def _git(self, *args):
        return subprocess.run(["git", "-C", str(self.clone), *args],
                              capture_output=True, text=True)

    def _init_clone(self, nodes):
        (self.clone / "notes").mkdir(parents=True, exist_ok=True)
        for slug, body in nodes.items():
            (self.clone / "notes" / f"{slug}.md").write_text(body.format(slug=slug))
        self._git("init")
        self._git("config", "user.email", "t@e.com")
        self._git("config", "user.name", "T")
        self._git("add", "-A")
        self._git("commit", "-m", "init")

    def _commit(self, msg):
        self._git("add", "-A")
        self._git("commit", "-m", msg)

    def _bump_node(self, slug, body):
        (self.clone / "notes" / f"{slug}.md").write_text(body.format(slug=slug))
        self._commit(f"update {slug}")

    # --- the board side -----------------------------------------------------
    def make_board(self, titles=("A task",)):
        """A REAL board store under this test's temp data home, with one task per
        title. Returns the loaded task dicts (seq assigned).

        The engine facade is loaded by literal path, which is how all 77 of the
        board's own test files load it — and it is loaded INSIDE the test rather
        than at module import ON PURPOSE. Each facade copy REGENERATES the
        ``board.*`` seam modules and binds them to its own globals, so the copy
        loaded last owns ``board.memos``; loading ours here makes it the live
        generation at the moment ``subscribe`` reaches for it.
        """
        spec = importlib.util.spec_from_file_location(
            "task_station_for_brain_tests", str(LIB / "task-station.py"))
        ts = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ts)
        data = self.data_home()
        ts.DATA = str(data)
        ts.STORE = str(data / "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        import store as _store
        _store.reset_cache()
        self.addCleanup(_store.reset_cache)
        self.ts = ts
        out = []
        for title in titles:
            t = ts.new_task(title, "summary")
            ts.save_task(t)
            out.append(t["id"])
        ts.ensure_seqs()
        self.cfg["tasks_db"] = data / "store/tasks.db"
        return [ts.load_task(i) for i in out]

    def memos_on(self, task_id):
        """Every memo on a task, read back through the board's own store."""
        return self.ts.load_task(task_id).get("memos") or []


class DetectionTest(SubsFixtureMixin):
    def test_check_reports_only_dirty_nodes(self):
        self._init_clone({"node-a": NODE_V1, "node-b": NODE_V1})
        references.ref_add(self.cfg, "node-a", "task-station:42", today="2026-07-01")
        references.ref_add(self.cfg, "node-b", "task-station:9", today="2026-07-01")
        self._bump_node("node-a", NODE_V2)  # node-a moves on; node-b stays

        rep = subscribe.check(self.cfg, deliver=False)
        dirty = {d["node"] for d in rep["dirty"]}
        self.assertEqual(dirty, {"node-a"})
        entry = rep["dirty"][0]
        self.assertEqual(entry["tasks"], ["task-station:42"])
        self.assertTrue(entry["org_rev"])

    def test_change_summary_names_verified_and_first_changed_heading(self):
        self._init_clone({"node-a": NODE_V1})
        references.ref_add(self.cfg, "node-a", "t", today="2026-07-01")
        old_rev = notes.parse_note(
            (self.vault / "references/node-a.md").read_text())[0]["org_rev"]
        self._bump_node("node-a", NODE_V2)
        summary = subscribe.change_summary(self.cfg, "node-a", old_rev)
        self.assertIn("2026-07-24", summary)                 # new verified date
        self.assertIn("Filing cadence", summary)             # first changed heading


class DeliveryTest(SubsFixtureMixin):
    """The SEND half of memo-on-org-update, against a real board store."""

    def _setup_dirty(self, n_tasks=1):
        """A dirty node referenced by ``n_tasks`` real board tasks. Returns the
        task dicts; their handles are the tasks' own seq numbers, which is what a
        reference stub records and what the board's ``resolve_ref`` accepts."""
        tasks = self.make_board(tuple(f"Task {i}" for i in range(n_tasks)))
        self._init_clone({"node-a": NODE_V1})
        for t in tasks:
            references.ref_add(self.cfg, "node-a", str(t["seq"]), today="2026-07-01")
        self._bump_node("node-a", NODE_V2)
        self.cfg["knowledge_memos"] = True
        return tasks

    def test_dirty_delivers_a_readable_memo_to_each_task(self):
        tasks = self._setup_dirty(2)
        rep = subscribe.check(self.cfg)
        self.assertEqual(rep["delivered"], 2)
        self.assertEqual(sorted(rep["dirty"][0]["delivered"]),
                         sorted(str(t["seq"]) for t in tasks))
        for t in tasks:
            memos = self.memos_on(t["id"])
            self.assertEqual(len(memos), 1)
            text = memos[0]["text"]
            self.assertIn("org knowledge updated: node-a", text)
            self.assertIn("Filing cadence", text)          # summary carried
            self.assertIn("2026-07-24", text)              # new verified date
            self.assertIn("ref refresh node-a", text)      # names the re-fetch command
            # the CLI's own send shape: no session, no corrections, ack ledger open
            self.assertIsNone(memos[0]["from_sid"])
            self.assertEqual(memos[0]["acks"], [])
            self.assertNotIn("corrects", memos[0])

    def test_the_memo_rides_the_task_event_feed_too(self):
        """ADDED — ``memo_send`` posts a capped preview event whose id IS the
        memo's. A memo the board's feed never saw would be invisible to every
        surface a task owner actually reads."""
        task = self._setup_dirty(1)[0]
        subscribe.check(self.cfg)
        t = self.ts.load_task(task["id"])
        memo = t["memos"][-1]
        ev = [e for e in t["events"] if e.get("kind") == "memo"][-1]
        self.assertEqual(ev["id"], memo["id"])
        self.assertIn(memo["id"][:8], ev["text"])

    def test_idempotent_once_per_node_rev(self):
        task = self._setup_dirty(1)[0]
        first = subscribe.check(self.cfg)
        self.assertEqual(first["delivered"], 1)
        # still dirty (stub not refreshed), same node-rev -> a re-check fires nothing
        second = subscribe.check(self.cfg)
        self.assertEqual(second["delivered"], 0)
        self.assertEqual(second["dirty"][0]["delivered"], [])
        self.assertEqual(len(self.memos_on(task["id"])), 1)  # exactly one memo, ever, for this rev

    def test_new_node_rev_refires(self):
        task = self._setup_dirty(1)[0]
        subscribe.check(self.cfg)
        self.assertEqual(len(self.memos_on(task["id"])), 1)
        # the node moves AGAIN -> a new node-rev -> one more memo
        self._bump_node("node-a", NODE_V1)  # any further change = new commit sha
        rep = subscribe.check(self.cfg)
        self.assertEqual(rep["delivered"], 1)
        self.assertEqual(len(self.memos_on(task["id"])), 2)

    def test_the_delivered_stamp_is_keyed_by_node_rev_and_task(self):
        """ADDED — the write-once record itself. The stamp file is what makes a
        re-check silent, so its KEYS are the contract: one per (node, node-rev,
        task), appended and never rewritten."""
        task = self._setup_dirty(1)[0]
        subscribe.check(self.cfg)
        stamped = json.loads((self.state / subscribe.STATE_NAME).read_text())["delivered"]
        node_rev = subscribe.current_node_rev(self.cfg, "node-a")
        self.assertEqual(stamped, [f"node-a\t{node_rev}\t{task['seq']}"])
        self._bump_node("node-a", NODE_V1)
        subscribe.check(self.cfg)
        after = json.loads((self.state / subscribe.STATE_NAME).read_text())["delivered"]
        self.assertEqual(len(after), 2)                       # appended...
        self.assertIn(stamped[0], after)                      # ...never rewritten

    def test_board_absent_silent_skip_and_breadcrumb(self):
        self._init_clone({"node-a": NODE_V1})
        references.ref_add(self.cfg, "node-a", "42", today="2026-07-01")
        self._bump_node("node-a", NODE_V2)
        self.cfg["knowledge_memos"] = True                      # forced on...
        self.cfg["tasks_db"] = self.home / "nope/tasks.db"      # ...but no board store
        rep = subscribe.check(self.cfg)                         # must NOT raise
        self.assertEqual(rep["delivered"], 0)
        self.assertTrue(rep["board_absent"])
        self.assertEqual(rep["dirty"][0]["delivered"], [])
        self.assertFalse((self.home / "nope").exists())         # and nothing was created
        log = errorlog.error_log_path()
        self.assertTrue(log.exists() and "subscribe" in log.read_text())

    def test_an_unresolvable_task_handle_is_logged_and_never_stops_the_run(self):
        """ADDED — the board is present but one handle names no task (a stale
        reference stub, a task deleted since). The source's CLI bridge got this
        for free (a non-zero exit from the child); a direct call must not turn it
        into a raise, and must not cost the OTHER task its memo."""
        task = self.make_board()[0]
        self._init_clone({"node-a": NODE_V1})
        # both handles recorded BEFORE the bump, so the stub's org_rev stays behind
        references.ref_add(self.cfg, "node-a", "no-such-task", today="2026-07-01")
        references.ref_add(self.cfg, "node-a", str(task["seq"]), today="2026-07-01")
        self._bump_node("node-a", NODE_V2)
        self.cfg["knowledge_memos"] = True

        rep = subscribe.check(self.cfg)
        self.assertIn("no-such-task", rep["dirty"][0]["tasks"])
        self.assertEqual(rep["dirty"][0]["delivered"], [str(task["seq"])])
        self.assertEqual(len(self.memos_on(task["id"])), 1)
        self.assertIn("subscribe:memo", errorlog.error_log_path().read_text())

    def test_disabled_delivers_nothing(self):
        task = self._setup_dirty(1)[0]
        self.cfg["knowledge_memos"] = False                     # explicit opt-out
        rep = subscribe.check(self.cfg)
        self.assertEqual(rep["delivered"], 0)
        self.assertEqual(self.memos_on(task["id"]), [])


class HealReportTest(SubsFixtureMixin):
    """The report-lines the /brain-heal pass (and the manual CLI) surface:
    dirty references + delivered-memo count."""

    def test_report_lines_list_dirty_and_delivered(self):
        task = self._make_dirty_and_deliver()
        self.assertTrue(self.memos_on(task["id"]))  # a memo really went out
        rep = subscribe.check(self.cfg)      # re-check: idempotent, still lists dirty
        lines = subscribe.report_lines(rep)
        blob = "\n".join(lines)
        self.assertIn("dirty references: 1", blob)
        self.assertIn("memos delivered:", blob)
        self.assertIn("node-a", blob)
        self.assertIn(str(task["seq"]), blob)

    def test_report_lines_flag_an_absent_board(self):
        rep = {"dirty": [], "delivered": 0, "enabled": True, "board_absent": True}
        self.assertTrue(any("board absent" in l for l in subscribe.report_lines(rep)))

    def _make_dirty_and_deliver(self):
        task = self.make_board()[0]
        self._init_clone({"node-a": NODE_V1})
        references.ref_add(self.cfg, "node-a", str(task["seq"]), today="2026-07-01")
        self._bump_node("node-a", NODE_V2)
        self.cfg["knowledge_memos"] = True
        subscribe.check(self.cfg)
        return task


class MemosEnabledTest(SubsFixtureMixin):
    def test_auto_off_without_task_station(self):
        # no board store -> memos default OFF
        self.cfg["knowledge_memos"] = None
        self.assertFalse(subscribe.memos_enabled(self.cfg))

    def test_auto_on_when_the_board_store_is_present(self):
        self.make_board()
        self.cfg["knowledge_memos"] = None
        self.assertTrue(subscribe.memos_enabled(self.cfg))

    def test_a_configured_but_missing_store_is_not_detected(self):
        """The detection is "is the store THERE", not "is a path configured" —
        every install has the latter (config always resolves a default)."""
        self.cfg["knowledge_memos"] = None
        self.cfg["tasks_db"] = self.home / "elsewhere/store/tasks.db"
        self.assertFalse(subscribe.board_present(self.cfg))
        self.assertFalse(subscribe.memos_enabled(self.cfg))

    def test_explicit_false_beats_detection(self):
        self.make_board()
        self.cfg["knowledge_memos"] = False
        self.assertFalse(subscribe.memos_enabled(self.cfg))

    def test_explicit_true_without_detection(self):
        self.cfg["knowledge_memos"] = True
        self.assertTrue(subscribe.memos_enabled(self.cfg))


class ManualCliTest(SubsFixtureMixin):
    """``brain-subscribe check`` — the manual entry point, resolving config from
    the environment end to end."""

    def test_check_cli_reports_dirty(self):
        self._init_clone({"node-a": NODE_V1})
        references.ref_add(self.cfg, "node-a", "task-station:42", today="2026-07-01")
        self._bump_node("node-a", NODE_V2)
        for key in ("TASK_STATION_BRAIN_VAULT", "TASK_STATION_BRAIN_ORG_BRAIN_CLONE",
                    "TASK_STATION_BRAIN_STATE", "TASK_STATION_BRAIN_KNOWLEDGE_MEMOS"):
            self.assertIn(key, ENV_KEYS)
        os.environ["TASK_STATION_BRAIN_VAULT"] = str(self.vault)
        os.environ["TASK_STATION_BRAIN_ORG_BRAIN_CLONE"] = str(self.clone)
        os.environ["TASK_STATION_BRAIN_STATE"] = str(self.state)
        os.environ["TASK_STATION_BRAIN_KNOWLEDGE_MEMOS"] = "0"   # report only, no board needed
        buf = io.StringIO()
        with redirect_stdout(buf):
            subscribe.main(["check"])
        out = buf.getvalue()
        self.assertIn("dirty references: 1", out)
        self.assertIn("node-a", out)


class BrainSubcommandTest(BrainTestCase):
    """``brain subscriptions check`` dispatches to subscribe.check."""

    def test_dispatch(self):
        calls = {}

        def fake_check(cfg, *, deliver=True, today=None):
            calls["deliver"] = deliver
            return {"dirty": [], "delivered": 0, "enabled": False, "board_absent": False}

        orig = subscribe.check
        subscribe.check = fake_check
        self.addCleanup(setattr, subscribe, "check", orig)
        buf = io.StringIO()
        with redirect_stdout(buf):
            search.main(["subscriptions", "check", "--no-deliver"])
        self.assertEqual(calls.get("deliver"), False)


def imported_modules(path):
    """Every module named by an import in ``path``, DOTTED and in full
    (``board.memos``, not ``board``). ``test_layers.top_level_imports`` answers
    "which package", which is the right question for a layer rule and the wrong
    one for an exception carved out at module granularity."""
    tree = ast.parse(path.read_text(), filename=str(path))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:          # relative — same package, not a layer crossing
                continue
            if node.module:
                names.add(node.module)
    return names


class FederationLayeringTest(unittest.TestCase):
    """ADDED — the layer rule for the three modules this chunk adds, AND the one
    sanctioned exception to it, read with ``ast`` (chunk 1's reader, which also
    sees function-local imports — and subscribe's board import IS function-local).

    The rule everywhere else in the brain plane: core + stdlib + siblings, never
    board. The exception, decided and recorded: ``brain/subscribe.py`` may import
    ``board.memos`` — the memo bridge that replaced the source's CLI subprocess —
    and nothing else board-side. Both halves are asserted, because an exception
    nobody bounded is just a hole.
    """

    FILES = ("brain/peers.py", "brain/subscribe.py", "brain/search.py")
    BRIDGE = "brain/subscribe.py"

    def test_only_subscribe_reaches_the_board(self):
        for rel in self.FILES:
            reaches = "board" in top_level_imports(LIB / rel)
            self.assertEqual(reaches, rel == self.BRIDGE,
                             f"{rel}: board reach should be {rel == self.BRIDGE}")

    def test_the_bridge_names_exactly_one_board_module(self):
        board_imports = {m for m in imported_modules(LIB / self.BRIDGE)
                         if m == "board" or m.startswith("board.")}
        self.assertEqual(board_imports, {"board.memos"})

    def test_each_module_reaches_only_core_and_stdlib_otherwise(self):
        for rel in self.FILES:
            path = LIB / rel
            self.assertTrue(path.exists(), f"{rel} missing")
            extra = top_level_imports(path) - STDLIB_OK - {"core"}
            if rel == self.BRIDGE:
                extra -= {"board"}
            self.assertEqual(extra, set(), f"{rel} reaches outside core+stdlib: {sorted(extra)}")

    def test_no_other_brain_module_imports_the_board(self):
        """The exception is ONE module wide. Any future brain file that reaches
        for the board fails here, which is the whole point of writing it down."""
        offenders = sorted(
            str(f.relative_to(LIB)) for f in (LIB / "brain").rglob("*.py")
            if "board" in top_level_imports(f))
        self.assertEqual(offenders, [self.BRIDGE])


if __name__ == "__main__":
    unittest.main()
