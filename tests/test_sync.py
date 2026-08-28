"""THE TRANSPORT'S CLAIM IS STRUCTURAL, SO THE TESTS HAVE TO BE TOO.

`sync.py` does not claim that merge conflicts are rare or well-handled. It claims
they are IMPOSSIBLE, because two stations never write the same path. A test that
merely asserts "the merge produced the right answer" would pass just as happily on a
design where both machines write one shared file and got lucky — so the load-bearing
test here (`NoSharedWritePathTest`) asserts the thing the claim is actually about:
the set of paths one station writes and the set the other writes have EMPTY
INTERSECTION, and the guard REFUSES a write aimed outside its own partition rather
than quietly relocating it.

STATION 0 GETS ITS OWN CLASS. Numbering starts at 0, so every `if station:` in the
codebase is a bug that drops the first real machine — the same trap hub ordinal 0
needed a dedicated test for. `StationZeroTest` is that test.

The round-trip tests drive the REAL CLI over REAL temp data dirs rather than calling
the merge directly: "a task created on one machine appears on the other" is the goal
clause, and it is only true if creation, export, import, seq allocation and the store
write all agree.
"""
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(_REPO_ROOT, "lib")
sys.path.insert(0, LIB)

_TMP_HOME = tempfile.mkdtemp(prefix="ts-sync-")
os.environ["TASK_STATION_HOME"] = _TMP_HOME

import station                                                          # noqa: E402
import sync                                                             # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)

CLI = os.path.join(LIB, "task-station.py")


class _Env(unittest.TestCase):
    """A temp exchange plus an identity, restored on teardown."""

    ALIAS = "kosei"
    NUMBER = 0

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-sync-case-")
        self.root = os.path.join(self.tmp, "exchange")
        self._env = {k: os.environ.get(k) for k in
                     ("TASK_STATION_SELF_ALIAS", "TASK_STATION_STATION",
                      "TASK_STATION_SYNC_DIR")}
        os.environ["TASK_STATION_SELF_ALIAS"] = self.ALIAS
        os.environ["TASK_STATION_STATION"] = str(self.NUMBER)
        os.environ["TASK_STATION_SYNC_DIR"] = self.root

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def as_station(self, alias, number):
        os.environ["TASK_STATION_SELF_ALIAS"] = alias
        os.environ["TASK_STATION_STATION"] = str(number)


# ---------------------------------------------------------------- station 0 ----

class StationZeroTest(_Env):
    """Station numbers start at 0, so NOTHING may treat a station as falsy."""

    def test_zero_is_a_real_station_everywhere(self):
        self.as_station("kosei", 0)
        self.assertEqual(station.number(), 0)
        self.assertEqual(station.dirname(0), "station-0")
        self.assertEqual(station.parse_dirname("station-0"), 0)
        self.assertIsNotNone(station.parse_dirname("station-0"))
        self.assertEqual(station.descriptor(0)["number"], 0)
        self.assertTrue(station.display(0, "box").startswith("0 · "))

    def test_station_zero_is_listed_as_a_partition(self):
        self.as_station("kosei", 0)
        sync.init_root(self.root)
        parts = sync.list_partitions(self.root)
        self.assertEqual([(p["owner"], p["number"]) for p in parts],
                         [("kosei", 0)])
        self.assertTrue(parts[0]["own"])

    def test_a_peers_station_zero_is_not_mistaken_for_absent(self):
        self.as_station("kosei", 0)
        sync.init_root(self.root)
        self.as_station("jpark", 0)
        sync.init_root(self.root)
        self.as_station("kosei", 0)
        foreign = sync.foreign_partitions(self.root)
        self.assertEqual([(p["owner"], p["number"]) for p in foreign], [("jpark", 0)])

    def test_default_number_is_zero_not_missing(self):
        os.environ.pop("TASK_STATION_STATION", None)
        self.assertEqual(station.number(), 0)


# ------------------------------------------------------- the partition guard ----

class PartitionGuardTest(_Env):
    """The one invariant everything else rests on."""

    def test_own_write_path_lands_inside_the_partition(self):
        p = sync.own_write_path(self.root, sync.TASKS_DIR, "x.json")
        self.assertTrue(p.startswith(sync.own_partition_dir(self.root) + os.sep))

    def test_a_write_aimed_at_a_peer_is_refused_not_relocated(self):
        peer = sync.partition_dir(self.root, "jpark", 0)
        with self.assertRaises(sync.PartitionViolation):
            sync.guard_own_path(self.root, os.path.join(peer, "tasks", "x.json"))

    def test_a_write_aimed_at_this_owners_other_station_is_refused(self):
        other = sync.partition_dir(self.root, self.ALIAS, 1)
        with self.assertRaises(sync.PartitionViolation):
            sync.guard_own_path(self.root, os.path.join(other, "tasks", "x.json"))

    def test_traversal_out_of_the_partition_is_refused(self):
        with self.assertRaises(sync.PartitionViolation):
            sync.own_write_path(self.root, sync.TASKS_DIR, "..", "..", "..", "evil")

    def test_an_alias_that_could_escape_is_rejected_at_the_boundary(self):
        for bad in ("../jpark", "a/b", "", ".", "..", "/etc"):
            self.assertFalse(station.valid_alias(bad), bad)
            with self.assertRaises(station.BadAlias):
                sync.partition_dir(self.root, bad, 0)

    def test_a_task_id_cannot_name_a_file_outside_the_partition(self):
        with self.assertRaises(sync.PartitionViolation):
            sync.own_payload_path(self.root, "../../../../etc/passwd")


class NoSharedWritePathTest(_Env):
    """THE STRUCTURAL CLAIM. Two stations export their own tasks; the paths they
    write must not intersect. This is what makes a git conflict impossible — not
    care, not ordering, not luck."""

    def _written(self, alias, number, tasks):
        self.as_station(alias, number)
        sync.ensure_own_partition(self.root)
        out = set()
        for t in tasks:
            sync.write_own_payload(self.root, t)
            out.add(os.path.relpath(sync.own_payload_path(self.root, t["id"]),
                                    self.root))
        out.add(os.path.relpath(
            os.path.join(sync.own_partition_dir(self.root), station.STATION_FILE),
            self.root))
        return out

    def test_two_owners_write_disjoint_paths(self):
        a = self._written("kosei", 0, [_task("aaaaaaaa-0000-0000-0000-000000000001")])
        b = self._written("jpark", 0, [_task("bbbbbbbb-0000-0000-0000-000000000002")])
        self.assertEqual(a & b, set())

    def test_two_stations_of_one_owner_write_disjoint_paths_even_for_the_SAME_task(self):
        same = "cccccccc-0000-0000-0000-000000000003"
        a = self._written("kosei", 0, [_task(same)])
        b = self._written("kosei", 1, [_task(same)])
        self.assertEqual(a & b, set())
        self.assertEqual(len(a), 2)


def _task(tid, **kw):
    t = {"id": tid, "uuid": tid, "title": "t", "status": "open",
         "created_ts": 1000.0, "updated_ts": 1000.0}
    t.update(kw)
    return t


# ------------------------------------------------------------------- payload ----

class PayloadTest(_Env):
    def test_machine_local_fields_never_reach_the_wire(self):
        t = _task("dddddddd-0000-0000-0000-000000000004", seq=7, stream_n=3,
                  files=["/Users/somebody/secret/path.py"], cost={"total_usd": 1})
        body = sync.export_payload(t)["task"]
        for k in ("seq", "stream_n", "files", "cost"):
            self.assertNotIn(k, body, k)

    def test_relation_edges_lose_the_origin_machines_seq(self):
        t = _task("eeeeeeee-0000-0000-0000-000000000005",
                  related=[{"id": "ffff", "seq": 444, "kind": "parent"}])
        edge = sync.export_payload(t)["task"]["related"][0]
        self.assertNotIn("seq", edge)
        self.assertEqual(edge["id"], "ffff")

    def test_the_importer_puts_THIS_machines_seq_back(self):
        t = _task("11111111-0000-0000-0000-000000000006",
                  related=[{"id": "22222222", "kind": "parent"}])
        by_id = {"22222222": {"id": "22222222", "seq": 9}}
        self.assertTrue(sync.rederive_related_seqs(t, by_id))
        self.assertEqual(t["related"][0]["seq"], 9)

    def test_an_edge_whose_target_is_not_here_carries_no_seq_at_all(self):
        t = _task("33333333-0000-0000-0000-000000000007",
                  related=[{"id": "nope", "seq": 444, "kind": "parent"}])
        sync.rederive_related_seqs(t, {})
        self.assertNotIn("seq", t["related"][0])

    def test_first_export_stamps_fields_at_the_tasks_own_age_not_at_now(self):
        t = _task("44444444-0000-0000-0000-000000000008")
        p = sync.export_payload(t, prev=None, now=9_999_999.0)
        self.assertEqual(p[sync.FIELD_TS]["title"], 1000.0)

    def test_only_the_changed_field_is_restamped(self):
        t = _task("55555555-0000-0000-0000-000000000009")
        first = sync.export_payload(t, prev=None, now=1000.0)
        t2 = dict(t, title="new")
        second = sync.export_payload(t2, prev=first, now=2000.0)
        self.assertEqual(second[sync.FIELD_TS]["title"], 2000.0)
        self.assertEqual(second[sync.FIELD_TS]["status"], 1000.0)


# --------------------------------------------------------------------- merge ----

class MergeTest(_Env):
    def _remote(self, task, stamps, owner="kosei", number=1):
        return {"schema": 1, "owner": owner, "station": number,
                sync.FIELD_TS: stamps, "task": task}

    def test_a_newer_scalar_wins_and_the_old_value_is_kept(self):
        local = _task("aaaa1111-0000-0000-0000-00000000000a", title="mine",
                      field_ts={"title": 100.0})
        remote = self._remote(_task("aaaa1111-0000-0000-0000-00000000000a",
                                    title="theirs"), {"title": 200.0})
        merged, rep = sync.merge_task(local, remote, now=300.0)
        self.assertEqual(merged["title"], "theirs")
        self.assertIn("title", rep["taken"])
        self.assertIn("title", rep["preserved"])
        self.assertEqual([h["text"] for h in merged.get("title_history") or []],
                         ["mine"])

    def test_an_older_scalar_loses_and_nothing_is_preserved(self):
        local = _task("aaaa2222-0000-0000-0000-00000000000b", title="mine",
                      field_ts={"title": 500.0})
        remote = self._remote(_task("aaaa2222-0000-0000-0000-00000000000b",
                                    title="theirs"), {"title": 200.0})
        merged, rep = sync.merge_task(local, remote, now=600.0)
        self.assertEqual(merged["title"], "mine")
        self.assertNotIn("title_history", merged)

    def test_one_updated_ts_cannot_decide_and_per_field_stamps_can(self):
        """Both records were touched at the same moment, but different FIELDS moved.
        Per-field stamps let each side keep the field it actually wrote."""
        local = _task("aaaa3333-0000-0000-0000-00000000000c", title="mine",
                      state="my state", updated_ts=900.0,
                      field_ts={"title": 900.0, "state": 100.0})
        remote = self._remote(
            _task("aaaa3333-0000-0000-0000-00000000000c", title="theirs",
                  state="their state", updated_ts=900.0),
            {"title": 100.0, "state": 900.0})
        merged, _ = sync.merge_task(local, remote, now=1000.0)
        self.assertEqual(merged["title"], "mine")
        self.assertEqual(merged["state"], "their state")

    def test_decisions_union_rather_than_one_side_winning(self):
        local = _task("aaaa4444-0000-0000-0000-00000000000d", decisions=["A"])
        remote = self._remote(_task("aaaa4444-0000-0000-0000-00000000000d",
                                    decisions=["B"]), {"decisions": 900.0})
        merged, _ = sync.merge_task(local, remote, now=1000.0)
        self.assertEqual([_text(d) for d in merged["decisions"]], ["A", "B"])

    def test_one_machine_superseding_while_the_other_pins_applies_BOTH(self):
        local = _task("aaaa5555-0000-0000-0000-00000000000e",
                      decisions=[{"text": "D", "pinned": True}])
        remote = self._remote(
            _task("aaaa5555-0000-0000-0000-00000000000e",
                  decisions=[{"text": "D", "superseded_by": 4}]), {"decisions": 900.0})
        merged, _ = sync.merge_task(local, remote, now=1000.0)
        self.assertEqual(len(merged["decisions"]), 1)
        self.assertTrue(merged["decisions"][0]["pinned"])
        self.assertEqual(merged["decisions"][0]["superseded_by"], 4)

    def test_a_step_ticked_on_either_machine_stays_ticked(self):
        local = _task("aaaa6666-0000-0000-0000-00000000000f",
                      steps=[{"text": "s", "done": False}])
        remote = self._remote(_task("aaaa6666-0000-0000-0000-00000000000f",
                                    steps=[{"text": "s", "done": True}]),
                              {"steps": 900.0})
        merged, _ = sync.merge_task(local, remote, now=1000.0)
        self.assertTrue(merged["steps"][0]["done"])

    def test_write_once_fields_are_never_changed_by_a_merge(self):
        local = _task("aaaa7777-0000-0000-0000-000000000010", handle="kosei-aaaa7777",
                      created_ts=1.0, field_ts={"handle": 1.0, "created_ts": 1.0})
        remote = self._remote(
            _task("aaaa7777-0000-0000-0000-000000000010", handle="jpark-zzzz",
                  created_ts=999.0),
            {"handle": 9999.0, "created_ts": 9999.0})
        merged, _ = sync.merge_task(local, remote, now=10000.0)
        self.assertEqual(merged["handle"], "kosei-aaaa7777")
        self.assertEqual(merged["created_ts"], 1.0)

    def test_a_missing_write_once_field_may_be_FILLED_once(self):
        local = _task("aaaa8888-0000-0000-0000-000000000011")
        remote = self._remote(_task("aaaa8888-0000-0000-0000-000000000011",
                                    handle="kosei-aaaa8888"), {"handle": 9.0})
        merged, rep = sync.merge_task(local, remote, now=10.0)
        self.assertEqual(merged["handle"], "kosei-aaaa8888")
        self.assertIn("handle", rep["filled"])

    def test_machine_local_fields_are_never_taken_from_a_peer(self):
        local = _task("aaaa9999-0000-0000-0000-000000000012", seq=5, stream_n=2,
                      files=["/local/only.py"])
        remote = self._remote(_task("aaaa9999-0000-0000-0000-000000000012", seq=99,
                                    stream_n=99, files=["/theirs.py"]),
                              {"seq": 9999.0, "stream_n": 9999.0, "files": 9999.0})
        merged, _ = sync.merge_task(local, remote, now=10000.0)
        self.assertEqual(merged["seq"], 5)
        self.assertEqual(merged["stream_n"], 2)
        self.assertEqual(merged["files"], ["/local/only.py"])

    def test_an_ordinal_allocator_takes_the_HIGH_WATER_MARK(self):
        local = _task("aaaaaaaa-0000-0000-0000-000000000013", hub_ordinal_next=3)
        remote = self._remote(_task("aaaaaaaa-0000-0000-0000-000000000013",
                                    hub_ordinal_next=7), {"hub_ordinal_next": 1.0})
        merged, _ = sync.merge_task(local, remote, now=10.0)
        self.assertEqual(merged["hub_ordinal_next"], 7)

    def test_the_merge_does_not_mutate_its_input(self):
        local = _task("aaaabbbb-0000-0000-0000-000000000014", title="mine",
                      decisions=["A"])
        remote = self._remote(_task("aaaabbbb-0000-0000-0000-000000000014",
                                    title="theirs", decisions=["B"]),
                              {"title": 9.0, "decisions": 9.0})
        sync.merge_task(local, remote, now=10.0)
        self.assertEqual(local["title"], "mine")
        self.assertEqual(local["decisions"], ["A"])

    def test_a_task_received_from_another_owner_is_not_republished(self):
        self.assertFalse(sync.exports_here({"origin_owner": "jpark"}))
        self.assertTrue(sync.exports_here({"origin_owner": "kosei"}))
        self.assertTrue(sync.exports_here({}))


def _text(d):
    return d if isinstance(d, str) else d.get("text")


# ---------------------------------------------------------------- round trip ----

class RoundTripTest(_Env):
    """The goal clause, end to end through the real CLI: a task created on one
    machine appears on the other, and a task edited on both keeps both edits."""

    def _run(self, home, alias, number, *args):
        env = dict(os.environ)
        env.update({"TASK_STATION_HOME": home, "CLAUDE_CONFIG_DIR": home,
                    "XDG_STATE_HOME": home, "TASK_STATION_SELF_ALIAS": alias,
                    "TASK_STATION_STATION": str(number),
                    "TASK_STATION_SYNC_DIR": self.root})
        r = subprocess.run([sys.executable, CLI] + list(args), capture_output=True,
                           text=True, env=env, timeout=180)
        return (r.stdout or "") + (r.stderr or "")

    def setUp(self):
        super(RoundTripTest, self).setUp()
        self.A = os.path.join(self.tmp, "homeA")
        self.B = os.path.join(self.tmp, "homeB")
        os.makedirs(self.A)
        os.makedirs(self.B)

    def a(self, *args):
        return self._run(self.A, "kosei", 0, *args)

    def b(self, *args):
        return self._run(self.B, "kosei", 1, *args)

    def test_a_task_created_on_one_machine_appears_on_the_other(self):
        self.a("sync", "--init")
        self.b("sync", "--init")
        self.a("create", "--title", "Made on station zero", "--no-attach")
        self.a("sync")
        out = self.b("sync")
        self.assertIn("1 created", out)
        self.assertIn("Made on station zero", self.b("search", "station zero"))

    def test_both_machines_edit_the_same_task_and_nothing_is_lost(self):
        self.a("sync", "--init")
        self.b("sync", "--init")
        self.a("create", "--title", "Shared", "--no-attach")
        self.a("sync")
        self.b("sync")
        self.a("update", "--task", "1", "--decision", "station-0 says ship")
        self.b("update", "--task", "1", "--decision", "station-1 says wait")
        self.b("sync")
        out = self.a("sync")
        self.assertIn("Judgment", out)
        detail = self.a("search", "--detail", "1")
        self.assertIn("station-0 says ship", detail)
        self.assertIn("station-1 says wait", detail)

    def test_a_union_makes_a_heal_due_rather_than_reporting_all_clear(self):
        self.a("sync", "--init")
        self.b("sync", "--init")
        self.a("create", "--title", "Shared", "--no-attach")
        self.a("sync")
        out = self.b("sync")
        self.assertIn("Heal-due", out)
        self.assertIn("run `/heal`", out)

    def test_the_report_always_carries_all_three_rows(self):
        self.a("sync", "--init")
        out = self.a("sync")
        for row in ("Mechanical", "Judgment", "Heal-due"):
            self.assertIn(row, out)

    def test_a_delete_propagates_and_the_task_does_not_come_back(self):
        self.a("sync", "--init")
        self.b("sync", "--init")
        self.a("create", "--title", "Doomed", "--no-attach")
        self.a("sync")
        self.b("sync")
        self.assertIn("Doomed", self.b("search", "Doomed"))
        self.a("delete", "--task", "1")
        self.a("sync")
        self.b("sync")
        self.assertNotIn("Doomed", self.b("search", "--detail", "1"))
        self.b("sync")
        self.a("sync")
        self.assertNotIn("Doomed", self.b("search", "--detail", "1"))

    def test_sync_is_off_until_it_is_configured(self):
        env = dict(os.environ)
        env.pop("TASK_STATION_SYNC_DIR", None)
        env.update({"TASK_STATION_HOME": self.A, "CLAUDE_CONFIG_DIR": self.A,
                    "XDG_STATE_HOME": self.A})
        r = subprocess.run([sys.executable, CLI, "sync"], capture_output=True,
                           text=True, env=env, timeout=120)
        self.assertIn("sync is OFF", (r.stdout or "") + (r.stderr or ""))

    def test_init_creates_no_remote(self):
        out = self.a("sync", "--init")
        self.assertIn("NO REMOTE", out)
        if sync.is_git_repo(self.root):
            r = subprocess.run(["git", "-C", self.root, "remote"],
                               capture_output=True, text=True)
            self.assertEqual((r.stdout or "").strip(), "")


if __name__ == "__main__":
    unittest.main()
