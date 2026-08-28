"""THE HANDLE IS THE ONE NAME THAT MEANS THE SAME THING ON EVERY MACHINE.

`seq` is handed out machine-locally, so `#512` names a different task on the laptop
than on the desktop, and the OLD handle — `<owner>-<seq>` — inherited that exactly.
These tests hold the three properties the replacement has to have, and the first is
the one the old form failed:

  1. NO COORDINATION. Two stations create tasks while disconnected, and the handles
     cannot collide — because neither station is choosing from a shared space.
  2. WRITE-ONCE, including against a handle somebody ELSE minted. A task arriving over
     sync keeps its origin owner's handle, or the name stops meaning one thing.
  3. COLLISION-DRIVEN DISPLAY. The width used to be hardcoded at 8. It now starts at 8
     and LENGTHENS exactly as far as ambiguity forces — measured on a real store, a
     4-hex prefix already had collisions, so a fixed width is a bug waiting for the
     store to grow.

`AmbiguityTest` is the one to read: an abbreviated handle naming two tasks must
resolve to NOTHING. Picking the first would hand back a different task from the one
the caller meant, which is strictly worse than "no such task" — abbreviation exists to
make that visible, not to hide it.
"""
import importlib.util
import os
import shutil
import sys
import tempfile
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(_REPO_ROOT, "lib")
sys.path.insert(0, LIB)

_TMP_HOME = tempfile.mkdtemp(prefix="ts-handles-")
os.environ["TASK_STATION_HOME"] = _TMP_HOME

import handles                                                          # noqa: E402
import station                                                          # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)

U1 = "e6440959-b7f1-4066-8d21-cd7512f4e9fd"
U2 = "e6440959-b7f1-9999-8d21-cd7512f4e9fd"   # shares the first 14 chars, then diverges
U3 = "77777777-aaaa-bbbb-cccc-dddddddddddd"


class MintTest(unittest.TestCase):
    def test_a_handle_is_owner_plus_the_FULL_uuid(self):
        self.assertEqual(handles.mint("kosei", U1), "kosei-" + U1)

    def test_split_recovers_both_halves(self):
        self.assertEqual(handles.split("kosei-" + U1), ("kosei", U1))

    def test_an_owner_alias_may_itself_contain_a_hyphen(self):
        """Splitting on the FIRST hyphen would have called the owner `mary`."""
        self.assertEqual(handles.split("mary-jane-" + U1), ("mary-jane", U1))

    def test_a_bare_32_hex_uuid_is_recognised_too(self):
        self.assertEqual(handles.split("kosei-" + "a" * 32), ("kosei", "a" * 32))

    def test_something_that_is_not_a_handle_splits_to_nothing(self):
        for bad in ("", "kosei", "kosei-444", "#444", "-" + U1, U1):
            self.assertEqual(handles.split(bad), (None, None), bad)

    def test_a_handle_is_not_mistakable_for_a_work_item_number(self):
        """The point of the shape, not an incidental property: `kosei-444` reads like
        a ticket; `kosei-e6440959` cannot."""
        h = handles.display(handles.mint("kosei", U1))
        self.assertFalse(h.rsplit("-", 1)[-1].isdigit())


class DisplayWidthTest(unittest.TestCase):
    def test_the_floor_is_eight_when_nothing_collides(self):
        d = handles.display_map(["kosei-" + U1, "kosei-" + U3])
        self.assertEqual(d["kosei-" + U1], "kosei-e6440959")
        self.assertEqual(d["kosei-" + U3], "kosei-77777777")

    def test_a_collision_at_the_floor_LENGTHENS_the_display(self):
        d = handles.display_map(["kosei-" + U1, "kosei-" + U2])
        self.assertNotEqual(d["kosei-" + U1], d["kosei-" + U2])
        for v in d.values():
            self.assertGreater(len(v), len("kosei-") + handles.MIN_WIDTH)

    def test_it_lengthens_only_as_far_as_it_must(self):
        """Not to the full uuid — the whole point is the SHORTEST unambiguous form."""
        d = handles.display_map(["kosei-" + U1, "kosei-" + U2])
        for v in d.values():
            self.assertLess(len(v), len("kosei-" + U1))

    def test_two_OWNERS_never_lengthen_each_other(self):
        """Same uuid prefix, different owners — already distinct at the floor."""
        d = handles.display_map(["kosei-" + U1, "jpark-" + U1])
        self.assertEqual(d["kosei-" + U1], "kosei-e6440959")
        self.assertEqual(d["jpark-" + U1], "jpark-e6440959")

    def test_a_display_never_ends_on_the_separator(self):
        """A name trailing a dash reads as truncated by accident."""
        a = "kosei-" + "a" * 8 + "-1111-2222-3333-444444444444"
        b = "kosei-" + "a" * 8 + "-1111-2222-3333-444444444445"
        for v in handles.display_map([a, b]).values():
            self.assertFalse(v.endswith("-"), v)

    def test_the_width_is_not_hardcoded_which_is_the_defect_being_fixed(self):
        wide = handles.display_map(["kosei-" + U1, "kosei-" + U2])
        narrow = handles.display_map(["kosei-" + U1])
        self.assertNotEqual(len(wide["kosei-" + U1]), len(narrow["kosei-" + U1]))


class AmbiguityTest(unittest.TestCase):
    POOL = ["kosei-" + U1, "kosei-" + U2, "jpark-" + U3]

    def test_an_exact_handle_resolves(self):
        self.assertEqual(handles.resolve("kosei-" + U1, self.POOL), ["kosei-" + U1])

    def test_an_unambiguous_prefix_resolves(self):
        self.assertEqual(handles.resolve("jpark-7777", self.POOL), ["jpark-" + U3])

    def test_an_AMBIGUOUS_prefix_returns_both_rather_than_guessing(self):
        self.assertEqual(len(handles.resolve("kosei-e6440959", self.POOL)), 2)

    def test_a_ref_that_is_not_handle_shaped_matches_nothing(self):
        for bad in ("444", "", "kosei", "e6440959"):
            self.assertEqual(handles.resolve(bad, self.POOL), [], bad)


class WriteOnceTest(unittest.TestCase):
    def test_a_task_without_a_handle_gets_one(self):
        t = {"id": U1, "uuid": U1}
        self.assertTrue(handles.ensure(t, "kosei"))
        self.assertEqual(t["handle"], "kosei-" + U1)

    def test_a_task_that_has_one_keeps_it(self):
        t = {"id": U1, "uuid": U1, "handle": "kosei-" + U1}
        self.assertFalse(handles.ensure(t, "kosei"))
        self.assertEqual(t["handle"], "kosei-" + U1)

    def test_ANOTHER_OWNERS_handle_is_never_rewritten(self):
        """A task received over sync carries its origin owner's name. Re-minting it
        here would make the same task two different things on two machines."""
        t = {"id": U1, "uuid": U1, "handle": "jpark-" + U1}
        self.assertFalse(handles.ensure(t, "kosei"))
        self.assertEqual(t["handle"], "jpark-" + U1)


class StoreIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-handles-store-")
        self._store = ts.STORE
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.set_g("STORE", ts.STORE)
        self._alias = os.environ.get("TASK_STATION_SELF_ALIAS")
        os.environ["TASK_STATION_SELF_ALIAS"] = "kosei"

    def tearDown(self):
        ts.STORE = self._store
        ts.set_g("STORE", self._store)
        if self._alias is None:
            os.environ.pop("TASK_STATION_SELF_ALIAS", None)
        else:
            os.environ["TASK_STATION_SELF_ALIAS"] = self._alias
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_new_task_is_stamped_at_creation(self):
        t = ts.new_task("Fresh", "")
        self.assertEqual(t["handle"], "kosei-" + t["id"])

    def test_two_stations_creating_offline_cannot_collide(self):
        """The property `<owner>-<seq>` could not provide. No allocator is consulted,
        so 'both machines are offline' is not even a case."""
        a = ts.new_task("On the laptop", "")
        os.environ["TASK_STATION_SELF_ALIAS"] = "kosei"
        b = ts.new_task("On the desktop", "")
        a["seq"] = b["seq"] = 512          # the SAME local number on both machines
        self.assertNotEqual(a["handle"], b["handle"])

    def test_an_old_task_is_backfilled_exactly_once(self):
        t = ts.new_task("Legacy", "")
        t.pop("handle", None)
        ts.save_task(t)
        self.assertEqual(ts.ensure_handles(), 1)
        self.assertEqual(ts.ensure_handles(), 0)
        self.assertEqual(ts.load_task(t["id"])["handle"], "kosei-" + t["id"])

    def test_a_task_resolves_by_its_abbreviated_handle(self):
        t = ts.new_task("Findable", "")
        ts.save_task(t)
        ts.ensure_handles()
        got = ts.resolve_ref("kosei-" + t["id"][:8])
        self.assertIsNotNone(got)
        self.assertEqual(got["id"], t["id"])

    def test_resolving_by_seq_and_by_id_prefix_still_works_unchanged(self):
        t = ts.new_task("Untouched", "")
        ts.save_task(t)
        ts.ensure_seqs()
        seq = ts.load_task(t["id"])["seq"]
        self.assertEqual(ts.resolve_ref(str(seq))["id"], t["id"])
        self.assertEqual(ts.resolve_ref(t["id"][:8])["id"], t["id"])

    def test_an_AMBIGUOUS_handle_ref_resolves_to_NOTHING_not_to_a_guess(self):
        """Two tasks whose handles share the 8-character floor. Returning the first
        would hand the caller a DIFFERENT task from the one they meant — the failure
        abbreviation exists to make visible, not to hide."""
        a, b = ts.new_task("Twin A", ""), ts.new_task("Twin B", "")
        a["id"] = a["uuid"] = "abcdef01-1111-2222-3333-444444444444"
        b["id"] = b["uuid"] = "abcdef01-1111-2222-3333-555555555555"
        a["handle"], b["handle"] = "kosei-" + a["id"], "kosei-" + b["id"]
        ts.save_task(a)
        ts.save_task(b)
        self.assertIsNone(ts.resolve_ref("kosei-abcdef01"))
        # …and the unambiguous forms of the very same pair still resolve.
        self.assertEqual(ts.resolve_ref("kosei-" + a["id"])["id"], a["id"])
        self.assertEqual(ts.resolve_ref("kosei-abcdef01-1111-2222-3333-5")["id"], b["id"])

    def test_a_handle_ref_naming_no_task_resolves_to_nothing(self):
        ts.new_task("Present", "")
        self.assertIsNone(ts.resolve_ref("kosei-deadbeef"))


if __name__ == "__main__":
    unittest.main()
