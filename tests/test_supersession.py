"""Decision supersession + pins (C1/C2) and the memo disposition contract (M1/M2/M4).

The bug all of this fixes is one bug seen twice: an append-only log read as current
state. A task's decision log grows without bound, the digest used to truncate it by
RECENCY, and a decision that REFUTES an earlier plan renders identically to the plan it
refuted — so the refuted one keeps briefing fresh sessions. Separately, a memo announcing
a correction was acked, and the ack was treated as an integration: the durable layer that
auto-loads each session still said the opposite.

So: decisions can now be SUPERSEDED (gone from every present-tense surface, kept and
marked in `history`) and PINNED, and a memo ack must declare what it DID with the memo.

TRUNCATION IS GONE. Every still-current decision renders — no age limit, no count limit,
no `+N earlier` pointer — because validity, not age, decides what should brief a fresh
session. `--pin` therefore controls READING ORDER only: pinned first (the architecture
spine), then everything else oldest-first. The tests below that used to assert the
recency limit say so in a comment naming this change.

BACK-COMPAT is the hard constraint — a decision is EITHER a legacy plain string OR a
rich dict, every reader takes both, and a task written by an older version must render
byte-identically. `test_legacy_task_blob_renders_unchanged` is the guard.

Isolation copies the `_repoint` idiom from tests/test_memo.py.
"""
import importlib.util
import io
import os
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

_TMP_HOME = tempfile.mkdtemp(prefix="ts-supersede-")
os.environ["TASK_STATION_HOME"] = _TMP_HOME

import decisions as dec  # noqa: E402
import heal  # noqa: E402
import store  # noqa: E402

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


def _repoint(tmp):
    os.environ["TASK_STATION_HOME"] = tmp
    ts.DATA = tmp
    ts.STORE = os.path.join(tmp, "store")
    ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
    ts.LINKS_DIR = os.path.join(ts.STORE, "links")
    store.reset_cache()


class _Args:
    def __init__(self, **kw):
        defaults = dict(task=None, text=None, id=None, session=None, sub=None,
                        decision=None, memory=None, noop=None, corrects=None,
                        title=None, summary=None, append_summary=None, state=None,
                        goal=None, step_add=None, step_done=None, step_undone=None,
                        supersedes=None, pin=False, pin_decision=None,
                        unpin_decision=None, log=None, pr=None, pr_desc=None,
                        story=None, story_desc=None, color=None, effort=None,
                        trail_visibility=None, relate=None)
        defaults.update(kw)
        self.__dict__.update(defaults)


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _repoint(self.tmp)

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _task(self, title="A task", decisions=None):
        t = ts.new_task(title, "summary")
        if decisions is not None:
            t["decisions"] = list(decisions)
        ts.save_task(t)
        ts.ensure_seqs()                 # `update --task <seq>` needs the seq assigned
        return ts.load_task(t["id"])

    def _out(self, fn, args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            fn(args)
        return buf.getvalue()

    def _update(self, t, **kw):
        return self._out(ts.cmd_update, _Args(task=str(t["seq"]), **kw))


# ---------------------------------------------------------------------------
# The primitive itself (lib/decisions.py) — dual shape, selection, mutation.
# ---------------------------------------------------------------------------

class TestDecisionPrimitive(_Base):

    def test_accessors_accept_both_shapes(self):
        # The whole back-compat contract in one place: a legacy string and a rich dict
        # answer every accessor, and a legacy string is always current + unpinned.
        self.assertEqual(dec.text("chose sqlite"), "chose sqlite")
        self.assertEqual(dec.text({"text": "chose sqlite"}), "chose sqlite")
        self.assertFalse(dec.is_superseded("chose sqlite"))
        self.assertFalse(dec.is_pinned("chose sqlite"))
        self.assertTrue(dec.is_superseded({"text": "x", "superseded_by": 3}))
        self.assertEqual(dec.superseded_by({"text": "x", "superseded_by": 3}), 3)
        self.assertTrue(dec.is_pinned({"text": "x", "pinned": True}))

    def test_compact_collapses_metadata_free_entries_to_plain_strings(self):
        # The write-side invariant that keeps older readers working: a decision carrying
        # no metadata is STORED exactly as an older version would store it.
        self.assertEqual(dec.compact({"text": "plain"}), "plain")
        self.assertEqual(dec.compact("plain"), "plain")
        self.assertEqual(dec.compact({"text": "p", "pinned": False}), "p")
        self.assertEqual(dec.compact({"text": "p", "pinned": True}),
                         {"text": "p", "pinned": True})

    def test_compact_preserves_unknown_keys_from_a_newer_writer(self):
        # Forward-compat: a key this version doesn't understand is carried, not dropped.
        out = dec.compact({"text": "p", "future_flag": "keep-me"})
        self.assertEqual(out, {"text": "p", "future_flag": "keep-me"})

    def test_live_drops_superseded_and_keeps_indices_one_based(self):
        entries = ["a", {"text": "b", "superseded_by": 3}, "c"]
        self.assertEqual(dec.live(entries), [(1, "a"), (3, "c")])
        self.assertEqual(dec.live_texts(entries), ["a", "c"])

    # REPLACES the six `digest_selection(entries, limit)` tests, which asserted the
    # recency limit and the `omitted` count behind the "+N earlier" pointer. TRUNCATION
    # IS REMOVED: `digest_order(entries)` takes no limit, returns EVERY still-current
    # decision, and there is no `omitted` — nothing is folded away, so nothing counts it.
    # The pin behaviour those tests guarded (unbounded, never crowded out) survives as
    # the stronger claim that everything renders regardless of pinning.
    def test_digest_order_is_pins_first_then_everything_else_oldest_first(self):
        entries = ["d1", "d2", "d3", {"text": "d4", "pinned": True}, "d5", "d6", "d7"]
        shown = dec.digest_order(entries)
        self.assertEqual([dec.text(d) for _i, d in shown],
                         ["d4", "d1", "d2", "d3", "d5", "d6", "d7"])

    def test_digest_order_keeps_pins_oldest_first_among_themselves(self):
        entries = [{"text": "p1", "pinned": True}, "u2",
                   {"text": "p3", "pinned": True}, "u4",
                   {"text": "p5", "pinned": True}]
        self.assertEqual([dec.text(d) for _i, d in dec.digest_order(entries)],
                         ["p1", "p3", "p5", "u2", "u4"])

    def test_digest_order_renders_every_current_decision_however_many(self):
        # The old limit was 6. A hundred current decisions must all come back.
        entries = ["d%d" % i for i in range(1, 101)]
        shown = dec.digest_order(entries)
        self.assertEqual(len(shown), 100)
        self.assertEqual([dec.text(d) for _i, d in shown],
                         ["d%d" % i for i in range(1, 101)])

    def test_digest_order_shows_the_oldest_decision_with_no_pin_at_all(self):
        # The concrete harm truncation caused: a load-bearing OLD decision that nobody
        # thought to pin sat in the hidden tail and never briefed anyone.
        entries = ["THE NAMING LAW"] + ["d%d" % i for i in range(2, 41)]
        texts = [dec.text(d) for _i, d in dec.digest_order(entries)]
        self.assertEqual(texts[0], "THE NAMING LAW")
        self.assertEqual(len(texts), 40)

    def test_digest_order_keeps_one_based_indices(self):
        entries = ["d1", {"text": "d2", "pinned": True}, "d3"]
        self.assertEqual([i for i, _d in dec.digest_order(entries)], [2, 1, 3])

    def test_digest_order_returns_nothing_for_an_empty_or_missing_log(self):
        self.assertEqual(dec.digest_order([]), [])
        self.assertEqual(dec.digest_order(None), [])

    def test_digest_order_excludes_superseded_split_and_merged(self):
        # The ONLY thing that keeps a decision off this surface is no longer being true.
        entries = ["keep1",
                   {"text": "gone-superseded", "superseded_by": 6},
                   {"text": "gone-split", "split_into": [6, 7]},
                   {"text": "gone-merged", "merged_into": 7},
                   {"text": "keep-pinned", "pinned": True},
                   "keep6", "keep7"]
        self.assertEqual([dec.text(d) for _i, d in dec.digest_order(entries)],
                         ["keep-pinned", "keep1", "keep6", "keep7"])

    def test_a_replaced_decision_is_absent_not_folded_behind_a_pointer(self):
        entries = ["a", {"text": "b", "superseded_by": 3}, "c"]
        self.assertEqual([dec.text(d) for _i, d in dec.digest_order(entries)], ["a", "c"])


class TestDecisionLengthAdvisory(unittest.TestCase):
    """The write-time length nudge. It must NEVER refuse — a refusal would push the
    author to drop a fact or fake two decisions out of one, which is exactly what the
    old pin cap's refusal produced."""

    def test_a_long_decision_gets_a_warning_naming_its_length(self):
        warn = dec.length_warning("x" * 900, 7)
        self.assertIsNotNone(warn)
        self.assertIn("900", warn)
        self.assertIn("decision 7", warn)
        self.assertIn("heal --split", warn)
        self.assertIn("IN FULL", warn)          # says the write succeeded

    def test_a_short_decision_warns_nothing(self):
        self.assertIsNone(dec.length_warning("chose sqlite over flat files", 1))
        self.assertIsNone(dec.length_warning("x" * dec.LONG_DECISION_CHARS, 1))

    def test_the_threshold_is_exclusive_so_one_char_over_warns(self):
        self.assertIsNone(dec.length_warning("x" * dec.LONG_DECISION_CHARS))
        self.assertIsNotNone(dec.length_warning("x" * (dec.LONG_DECISION_CHARS + 1)))

    def test_the_warning_reads_the_rich_shape_too(self):
        warn = dec.length_warning({"text": "y" * 700, "pinned": True}, 3)
        self.assertIsNotNone(warn)
        self.assertIn("700", warn)


class TestLongDecisionThroughTheCLI(_Base):
    """The advisory end-to-end. The one thing that must never happen is a refusal: it
    would push the author to drop a fact, or to fake two decisions out of one to get
    under the number."""

    def test_a_900_char_decision_is_stored_in_full_and_only_warned_about(self):
        body = "A" * 900
        t = self._task()
        out = self._update(t, decision=[body])
        r = ts.load_task(t["id"])
        # STORED IN FULL, byte-identical, and still the plain-string legacy shape.
        self.assertEqual(r["decisions"], [body])
        self.assertEqual(len(dec.text(r["decisions"][0])), 900)
        # …and it renders in full on the resume path.
        self.assertIn(body, ts._format_detail(r, "sess"))
        # The write REPORTED SUCCESS and the advisory rode alongside it.
        self.assertIn("updated task", out)
        self.assertIn("decision", out)
        self.assertIn("900", out)
        self.assertIn("heal --split", out)
        # Not a refusal, by any of the words this codebase refuses with.
        for refusal in ("REFUSED", "ignoring", "no such", "too long", "over the limit"):
            self.assertNotIn(refusal, out)

    def test_a_short_decision_warns_nothing(self):
        t = self._task()
        out = self._update(t, decision=["chose sqlite over flat files, one writer"])
        self.assertIn("updated task", out)
        self.assertNotIn("heal --split", out)
        self.assertNotIn("advisory", out)

    def test_an_over_length_decision_still_takes_its_supersedes_and_pin(self):
        # The advisory must not interfere with the rest of the update.
        t = self._task(decisions=["the old plan"])
        out = self._update(t, decision=["B" * 900], supersedes=[1], pin=True)
        r = ts.load_task(t["id"])
        self.assertIn("heal --split", out)
        self.assertTrue(dec.is_superseded(r["decisions"][0]))
        self.assertTrue(dec.is_pinned(r["decisions"][1]))
        self.assertEqual(dec.text(r["decisions"][1]), "B" * 900)


# ---------------------------------------------------------------------------
# C1 — supersession through the `update` CLI + the two render surfaces.
# ---------------------------------------------------------------------------

class TestSupersession(_Base):

    def test_supersedes_marks_target_and_names_its_replacement(self):
        t = self._task(decisions=["use LEGACY sync", "keep polling"])
        self._update(t, decision=["switch to push, polling missed events"], supersedes=[1])
        r = ts.load_task(t["id"])
        self.assertEqual(dec.superseded_by(r["decisions"][0]), 3)
        self.assertEqual(dec.text(r["decisions"][0]), "use LEGACY sync")
        self.assertFalse(dec.is_superseded(r["decisions"][1]))

    def test_one_decision_may_supersede_several(self):
        t = self._task(decisions=["plan A", "plan B", "plan C"])
        self._update(t, decision=["plan D replaces A and B"], supersedes=[1, 2])
        r = ts.load_task(t["id"])
        self.assertEqual(dec.superseded_by(r["decisions"][0]), 4)
        self.assertEqual(dec.superseded_by(r["decisions"][1]), 4)
        self.assertFalse(dec.is_superseded(r["decisions"][2]))

    def test_superseded_decision_is_absent_from_the_default_digest(self):
        t = self._task(decisions=["use LEGACY sync"])
        self._update(t, decision=["switch to push"], supersedes=[1])
        detail = ts._format_detail(ts.load_task(t["id"]), "sess")
        self.assertNotIn("use LEGACY sync", detail)
        self.assertIn("switch to push", detail)

    def test_superseded_decision_is_present_and_marked_in_history(self):
        t = self._task(decisions=["use LEGACY sync"])
        self._update(t, decision=["switch to push"], supersedes=[1])
        view = ts._format_history(ts.load_task(t["id"]))
        self.assertIn("use LEGACY sync", view)               # history stays COMPLETE
        self.assertIn("SUPERSEDED by decision 2", view)      # …and names the replacement
        self.assertIn("1 superseded", view)

    def test_history_numbers_decisions_so_supersedes_n_is_discoverable(self):
        # `--supersedes <n>` takes the number this view prints; if it didn't print them
        # the feature would be unusable.
        t = self._task(decisions=["alpha", "beta"])
        view = ts._format_history(ts.load_task(t["id"]))
        self.assertIn("1:1. alpha", view)      # qualified: whose log the number is on
        self.assertIn("1:2. beta", view)

    def test_superseding_a_nonexistent_index_is_a_loud_error(self):
        t = self._task(decisions=["only one"])
        out = self._update(t, decision=["new"], supersedes=[9])
        self.assertIn("no such decision", out)
        r = ts.load_task(t["id"])
        self.assertFalse(dec.is_superseded(r["decisions"][0]))

    def test_superseding_an_already_superseded_index_is_a_loud_error(self):
        t = self._task(decisions=["first"])
        self._update(t, decision=["second"], supersedes=[1])
        out = self._update(t, decision=["third"], supersedes=[1])
        self.assertIn("already superseded", out)
        r = ts.load_task(t["id"])
        self.assertEqual(dec.superseded_by(r["decisions"][0]), 2)   # unchanged

    def test_supersedes_without_a_decision_is_refused(self):
        t = self._task(decisions=["a", "b"])
        out = self._update(t, supersedes=[1])
        self.assertIn("--supersedes needs a --decision", out)
        self.assertFalse(dec.is_superseded(ts.load_task(t["id"])["decisions"][0]))

    def test_supersedes_on_a_task_with_no_decisions_does_not_create_the_field(self):
        t = self._task()
        self.assertFalse(ts.load_task(t["id"]).get("decisions"))
        out = self._update(t, supersedes=[1])
        self.assertIn("--supersedes needs a --decision", out)
        self.assertFalse(ts.load_task(t["id"]).get("decisions"))

    def test_superseded_decision_never_reaches_the_other_consumers(self):
        # C1's "must never resurface through any other surface" — checked against every
        # decision consumer found in the tree, not just the two render paths.
        t = self._task(decisions=["WRONG-MARKER stale plan"])
        self._update(t, decision=["RIGHT-MARKER current plan"], supersedes=[1])
        r = ts.load_task(t["id"])

        # the machine-readable checkpoint/snapshot digest (task.checkpoint / .snapshot)
        self.assertEqual(ts._stream_digest(r)["decisions"], ["RIGHT-MARKER current plan"])

        # the HTML board's view-model
        vm = ts._board_view_model(r)
        self.assertEqual(vm["decisions"], ["RIGHT-MARKER current plan"])

        # the obsidian export body
        import obsidian_sync
        note = obsidian_sync.render_note(r)
        self.assertNotIn("WRONG-MARKER", note)
        self.assertIn("RIGHT-MARKER current plan", note)

        # the wikilink co-citation graph source
        self.assertEqual(dec.live_texts(r["decisions"]), ["RIGHT-MARKER current plan"])

    def test_feed_wire_form_carries_plain_strings_without_superseded(self):
        # The feed is the seam between machines: its `decisions_tail` must stay a list
        # of plain strings (every existing reader depends on that) and must never carry
        # a superseded decision off this box. Exercised end-to-end, through the real
        # writer and the canonical parser.
        import feeds
        t = self._task(decisions=["WRONG-MARKER stale"])
        self._update(t, decision=["RIGHT-MARKER current"], supersedes=[1])
        feeds.export_self_feed(ts, self.tmp)
        feed = feeds.parse_feed_file(os.path.join(self.tmp, "feeds", "self.js"))
        mine = [x for x in feed["tasks"] if x["uuid8"] == t["id"][:8]][0]
        tail = mine["digest"]["decisions_tail"]
        self.assertEqual(tail, ["RIGHT-MARKER current"])
        self.assertTrue(all(isinstance(x, str) for x in tail))


# ---------------------------------------------------------------------------
# C2 — pinned decisions.
# ---------------------------------------------------------------------------

class TestPinnedDecisions(_Base):

    # CHANGED from test_pinned_decision_survives_truncation_by_age, which asserted that
    # `routine d2` (old + unpinned) was FOLDED AWAY. There is no folding any more — the
    # unpinned old entry renders too, and that is the point of removing truncation. What
    # the pin still buys is position: it leads.
    def test_a_pinned_decision_leads_and_nothing_old_is_dropped(self):
        t = self._task(decisions=["LOADBEARING the schema is append-only"])
        self._update(t, pin_decision=[1])
        for i in range(2, 12):
            self._update(t, decision=["routine d%d" % i])
        detail = ts._format_detail(ts.load_task(t["id"]), "sess")
        body = detail[detail.index("Decisions:"):]
        self.assertIn("LOADBEARING the schema is append-only", body)
        for i in range(2, 12):
            self.assertIn("routine d%d" % i, body)            # every one of them
        self.assertLess(body.index("LOADBEARING"), body.index("routine d2"))
        self.assertNotIn("earlier decision", body)            # no pointer, nothing hidden

    def test_pin_flag_pins_the_decision_added_in_the_same_update(self):
        t = self._task()
        self._update(t, decision=["pin me"], pin=True)
        r = ts.load_task(t["id"])
        self.assertTrue(dec.is_pinned(r["decisions"][0]))

    # Renamed from ..._render_first_then_recency with the truncation removal: the second
    # block is no longer "the recent ones", it is everything else, oldest-first.
    def test_pinned_decisions_render_first_then_the_rest_oldest_first(self):
        t = self._task(decisions=["old pinned one"])
        self._update(t, pin_decision=[1])
        for i in range(2, 5):
            self._update(t, decision=["later d%d" % i])
        detail = ts._format_detail(ts.load_task(t["id"]), "sess")
        body = detail[detail.index("Decisions:"):]
        self.assertLess(body.index("old pinned one"), body.index("later d2"))
        # and the unpinned block itself is oldest-first, not newest-first
        self.assertLess(body.index("later d2"), body.index("later d3"))
        self.assertLess(body.index("later d3"), body.index("later d4"))

    def test_pin_without_a_decision_is_refused_and_points_at_pin_decision(self):
        t = self._task(decisions=["a"])
        out = self._update(t, pin=True)
        self.assertIn("--pin needs a --decision", out)
        self.assertIn("--pin-decision", out)

    def test_unpin_removes_the_pin_and_recompacts_to_a_plain_string(self):
        t = self._task(decisions=["a"])
        self._update(t, pin_decision=[1])
        self.assertTrue(dec.is_pinned(ts.load_task(t["id"])["decisions"][0]))
        self._update(t, unpin_decision=[1])
        r = ts.load_task(t["id"])
        self.assertEqual(r["decisions"][0], "a")     # back to the legacy shape exactly

    # -- A REPLACEMENT VERB MUST NOT THROW METADATA AWAY (3.54.0) ---------------
    # `mark_split`'s docstring always claimed "the parts carry the load now", but
    # nothing handed them the load: the pin was popped and given to nobody, and
    # kind/subject were never copied (the auto path appends parts as bare text).
    # A pinned ruling split at an unattended turn boundary left the spine silently.

    def test_split_moves_the_pin_to_the_first_part_and_only_the_first(self):
        entries = [{"text": "compound ruling", "pinned": True},
                   {"text": "part one"}, {"text": "part two"}]
        ok, err = dec.mark_split(entries, 1, [2, 3])
        self.assertEqual((ok, err), (True, None))
        self.assertTrue(dec.is_pinned(entries[1]))
        self.assertFalse(dec.is_pinned(entries[2]))
        self.assertFalse(dec.is_pinned(entries[0]))

    def test_split_copies_kind_and_subject_to_every_part(self):
        subj = [{"task": "532"}]
        entries = [{"text": "orig", "kind": "ruling", "subject": subj},
                   {"text": "p1"}, {"text": "p2"}]
        dec.mark_split(entries, 1, [2, 3])
        for i in (1, 2):
            self.assertEqual(entries[i].get("kind"), "ruling")
            self.assertEqual(entries[i].get("subject"), subj)

    def test_split_never_overwrites_a_part_that_declares_its_own(self):
        entries = [{"text": "orig", "kind": "ruling"},
                   {"text": "p1", "kind": "measurement"}]
        dec.mark_split(entries, 1, [2])
        self.assertEqual(entries[1].get("kind"), "measurement")

    def test_split_reports_what_it_moved(self):
        carried = {}
        entries = [{"text": "o", "pinned": True, "kind": "ruling"},
                   {"text": "p1"}, {"text": "p2"}]
        dec.mark_split(entries, 1, [2, 3], carried=carried)
        self.assertEqual(carried["pin_to"], 2)
        self.assertEqual(carried["kind_to"], [2, 3])

    def test_restore_gives_back_the_pin_a_replacement_verb_took(self):
        # `restore` advertises itself as the ONE inverse of all three verbs. It used to
        # return the text UNPINNED, so undoing a heal silently cost a spine entry.
        entries = [{"text": "pinned ruling", "pinned": True}, {"text": "replacement"}]
        dec.mark_superseded(entries, 1, 2)
        self.assertFalse(dec.is_pinned(entries[0]))
        ok, err = dec.restore(entries, 1)
        self.assertEqual((ok, err), (True, None))
        self.assertTrue(dec.is_pinned(entries[0]))

    def test_restore_does_not_invent_a_pin_that_never_existed(self):
        entries = [{"text": "unpinned"}, {"text": "replacement"}]
        dec.mark_superseded(entries, 1, 2)
        dec.restore(entries, 1)
        self.assertFalse(dec.is_pinned(entries[0]))

    def test_pinning_a_superseded_decision_is_an_error(self):
        t = self._task(decisions=["stale"])
        self._update(t, decision=["fresh"], supersedes=[1])
        out = self._update(t, pin_decision=[1])
        self.assertIn("superseded", out)
        self.assertIn("cannot be pinned", out)
        self.assertFalse(dec.is_pinned(ts.load_task(t["id"])["decisions"][0]))

    def test_superseding_a_pinned_decision_clears_its_pin(self):
        t = self._task(decisions=["pinned but wrong"])
        self._update(t, pin_decision=[1])
        self._update(t, decision=["the correction"], supersedes=[1])
        r = ts.load_task(t["id"])
        self.assertTrue(dec.is_superseded(r["decisions"][0]))
        self.assertFalse(dec.is_pinned(r["decisions"][0]))

    # REPLACES test_pin_cap_refuses_past_the_limit / test_unpinning_makes_room_again_
    # after_the_cap, which asserted the removed pin cap. The cap only ever existed to
    # protect the digest's recency budget from pins — and there is no recency budget at
    # all now, so there is nothing to protect and nothing to refuse. That refusal is
    # also the cautionary tale behind `length_warning`: it produced a workaround, not a
    # fix, which is why the length advisory warns and never gates.
    def test_pinning_is_unbounded_and_never_refuses(self):
        t = self._task(decisions=["d%d" % i for i in range(1, 21)])
        for i in range(1, 21):
            out = self._update(t, pin_decision=[i])
            self.assertIn("pin", out)                     # it took effect
            self.assertNotIn("pin cap", out)
            self.assertNotIn("unpin one first", out)
        r = ts.load_task(t["id"])
        self.assertEqual(dec.pinned_count(r["decisions"]), 20)
        self.assertTrue(all(dec.is_pinned(e) for e in r["decisions"]))
        # And all 20 reach the digest. (Was `digest_selection(…, ts.DECISIONS_TAIL)` with
        # an `omitted` assertion; the limit and the count are both gone.)
        self.assertEqual(len(dec.digest_order(r["decisions"])), 20)

    def test_repinning_an_already_pinned_decision_is_a_no_op(self):
        # Renamed from ..._does_not_consume_cap with the pin-cap removal; the behaviour
        # it guards (a repeat pin neither errors nor double-counts) is unchanged.
        t = self._task(decisions=["a", "b"])
        self._update(t, pin_decision=[1])
        out = self._update(t, pin_decision=[1])
        self.assertNotIn("pin cap", out)
        self.assertEqual(dec.pinned_count(ts.load_task(t["id"])["decisions"]), 1)

    # Renamed from ..._pins_and_recent_decisions_render_together_in_the_detail, whose
    # comment described the pin-vs-recency-budget fight. There is no recency budget left
    # to fight over; the claim is now simply that all twelve render.
    def test_pins_and_the_rest_all_render_in_the_detail(self):
        t = self._task(decisions=["arch a%d" % i for i in range(1, 7)]
                                + ["recent r%d" % i for i in range(1, 7)])
        for i in range(1, 7):
            self._update(t, pin_decision=[i])
        detail = ts._format_detail(ts.load_task(t["id"]), "sess")
        for i in range(1, 7):
            self.assertIn("arch a%d" % i, detail)        # every pin
        for i in range(1, 7):
            self.assertIn("recent r%d" % i, detail)      # and everything unpinned
        self.assertEqual(detail.count(ts.DECISION_PIN_MARK), 6)
        self.assertNotIn("earlier decision", detail)     # nothing left to fold

    # REPLACES test_earlier_pointer_counts_only_unpinned_unshown_current_decisions.
    # There IS no "+N earlier" pointer any more — the same 13-decision fixture that used
    # to fold three entries behind it now renders all 12 current ones. What is still
    # absent is the SUPERSEDED one, which is the criterion that survived.
    def test_no_earlier_pointer_exists_and_every_current_decision_renders(self):
        t = self._task(decisions=["stale s1", "stale s2", "wrong w3"]
                                + ["arch a%d" % i for i in range(1, 4)]
                                + ["recent r%d" % i for i in range(1, 7)])
        self._update(t, decision=["the correction"], supersedes=[3])
        for i in (4, 5, 6):
            self._update(t, pin_decision=[i])
        detail = ts._format_detail(ts.load_task(t["id"]), "sess")
        self.assertNotIn("earlier decision", detail)
        self.assertNotIn("+", detail[detail.index("Decisions:"):].split("\n\n")[0])
        for marker in (["stale s1", "stale s2", "the correction"]
                       + ["arch a%d" % i for i in range(1, 4)]
                       + ["recent r%d" % i for i in range(1, 7)]):
            self.assertIn(marker, detail)
        self.assertNotIn("wrong w3", detail)             # superseded → still gone
        # 13 stored, 1 superseded → 12 rendered rows, 3 of them pinned.
        block = detail[detail.index("Decisions:"):].split("\n\n")[0]
        numbers = [int(m) for m in re.findall(r"^\s+\d+:(\d+)\. ", block, re.M)]
        self.assertEqual(len(numbers), 12)
        self.assertEqual(block.count(ts.DECISION_PIN_MARK), 3)
        # The numbers are the LOG's, so they SKIP the superseded entry (3) rather than
        # closing the gap — renumbering would repoint a command a reader already holds.
        self.assertNotIn(3, numbers)
        self.assertEqual(sorted(numbers), [1, 2] + list(range(4, 14)))
        # Pinned sort first, so reading order is not numeric order; the number a row
        # carries still has to be its own.
        rows = [ln for ln in block.splitlines() if re.match(r"^\s+\d+:\d+\. ", ln)]
        self.assertIn("the correction", rows[numbers.index(13)])

    def test_history_still_carries_the_retired_decisions_the_digest_drops(self):
        # The other half of the contract: removing truncation must not have quietly
        # moved superseded/split/merged entries back onto the resume path, and they must
        # still be findable, marked, in `history`.
        t = self._task(decisions=["WRONG w1", "COMPOUND c2", "STALE-RECORD m3", "keep k4"])
        self._update(t, decision=["the correction"], supersedes=[1])   # → w1 dies at 5
        r = ts.load_task(t["id"])
        entries = r["decisions"]
        ok, err = dec.mark_split(entries, 2, [5])
        self.assertTrue(ok, err)
        ok, err = dec.mark_merged(entries, 3, 5)
        self.assertTrue(ok, err)
        ts.save_task(r)

        detail = ts._format_detail(ts.load_task(t["id"]), "sess")
        for gone in ("WRONG w1", "COMPOUND c2", "STALE-RECORD m3"):
            self.assertNotIn(gone, detail)
        self.assertIn("keep k4", detail)
        self.assertIn("the correction", detail)

        view = ts._format_history(ts.load_task(t["id"]))
        for kept in ("WRONG w1", "COMPOUND c2", "STALE-RECORD m3", "keep k4"):
            self.assertIn(kept, view)
        self.assertIn("SUPERSEDED by decision 5", view)
        self.assertIn("SPLIT into decision 5", view)
        self.assertIn("MERGED into decision 5", view)


# ---------------------------------------------------------------------------
# BACK-COMPAT — the 276-live-task constraint.
# ---------------------------------------------------------------------------

class TestLegacyCompat(_Base):

    # CHANGED with the truncation removal: this used to assert the 6-deep tail, the
    # hidden `d1`, and the "+4 earlier decision" pointer. A legacy blob now renders ALL
    # TEN — which is still "unchanged" in the sense that matters here, that nothing about
    # the new element shape leaks into a task that carries none of it.
    def test_legacy_task_blob_renders_with_nothing_new_leaking_in(self):
        # A task blob exactly as an OLDER version wrote it: decisions are plain strings,
        # no supersession/pin keys anywhere.
        t = self._task(decisions=["d%d" % i for i in range(1, 11)])
        detail = ts._format_detail(ts.load_task(t["id"]), "sess")
        for i in range(1, 11):
            self.assertIn("d%d" % i, detail)
        self.assertNotIn("earlier decision", detail)
        self.assertNotIn(ts.DECISION_PIN_MARK, detail)
        self.assertNotIn("SUPERSEDED", detail)

        view = ts._format_history(ts.load_task(t["id"]))
        for i in range(1, 11):
            self.assertIn("d%d" % i, view)
        self.assertNotIn("SUPERSEDED", view)
        self.assertNotIn("superseded", view)

    def test_legacy_decisions_survive_a_round_trip_byte_identically(self):
        # Loading + saving a legacy task must not silently rewrite its decisions into
        # the rich shape — otherwise an older reader starts seeing dicts it can't render.
        t = self._task(decisions=["plain one", "plain two"])
        ts.save_task(ts.load_task(t["id"]))
        self.assertEqual(ts.load_task(t["id"])["decisions"], ["plain one", "plain two"])

    def test_appending_to_a_legacy_task_keeps_the_plain_string_shape(self):
        t = self._task(decisions=["legacy"])
        self._update(t, decision=["newly added"])
        self.assertEqual(ts.load_task(t["id"])["decisions"], ["legacy", "newly added"])

    def test_only_the_touched_decision_becomes_rich(self):
        # Supersede one of three: the other two stay plain strings, so an older reader
        # degrades on exactly one entry rather than the whole log.
        t = self._task(decisions=["a", "b", "c"])
        self._update(t, decision=["d"], supersedes=[2])
        r = ts.load_task(t["id"])
        self.assertEqual(r["decisions"][0], "a")
        self.assertIsInstance(r["decisions"][1], dict)
        self.assertEqual(r["decisions"][2], "c")
        self.assertEqual(r["decisions"][3], "d")

    def test_a_task_written_by_a_newer_version_still_reads(self):
        # The reverse direction: unknown keys and an odd shape must not break a render.
        t = self._task(decisions=[{"text": "rich one", "pinned": True, "future": 1},
                                  "legacy one"])
        detail = ts._format_detail(ts.load_task(t["id"]), "sess")
        self.assertIn("rich one", detail)
        self.assertIn("legacy one", detail)


# ---------------------------------------------------------------------------
# M1 — `memo ack` requires a disposition.
# ---------------------------------------------------------------------------

class TestMemoDisposition(_Base):

    def _memo(self, t, body="a durable fact", corrects=None):
        m = ts.memo_send(t, body, from_sid="sender", corrects=corrects)
        ts.save_task(t)
        return m

    def test_bare_ack_fails_and_names_all_three_options(self):
        t = self._task()
        m = self._memo(t)
        out = self._out(ts.cmd_memo, _Args(sub="ack", task=str(t["seq"]),
                                           id=m["id"][:8], session="acker"))
        self.assertIn("--decision", out)
        self.assertIn("--memory", out)
        self.assertIn("--noop", out)
        # …and nothing was acked.
        self.assertEqual(ts.load_task(t["id"])["memos"][-1].get("acks"), [])

    def test_ack_with_decision_promotes_and_records_the_disposition(self):
        t = self._task()
        m = self._memo(t, "promote me")
        out = self._out(ts.cmd_memo, _Args(sub="ack", task=str(t["seq"]), id=m["id"][:8],
                                           session="acker", decision=True))
        self.assertIn("promoted to a decision", out)
        r = ts.load_task(t["id"])
        # By TEXT rather than by raw membership: an ack promotion now declares itself a
        # process-note, so the entry is a rich element. The projection is what this test
        # was ever about, and `live_texts` is the seam that owns it.
        self.assertIn("promote me", dec.live_texts(r["decisions"]))
        self.assertEqual(r["memos"][-1]["acks"][0]["disposition"]["kind"], "decision")

    def test_ack_with_memory_records_the_note_slug(self):
        t = self._task()
        m = self._memo(t)
        out = self._out(ts.cmd_memo, _Args(sub="ack", task=str(t["seq"]), id=m["id"][:8],
                                           session="acker", memory="some-note-name"))
        self.assertIn("some-note-name", out)
        disp = ts.load_task(t["id"])["memos"][-1]["acks"][0]["disposition"]
        self.assertEqual(disp, {"kind": "memory", "value": "some-note-name"})
        # a --memory ack must NOT invent a decision
        self.assertFalse(ts.load_task(t["id"]).get("decisions"))

    def test_ack_with_noop_requires_a_reason(self):
        t = self._task()
        m = self._memo(t)
        out = self._out(ts.cmd_memo, _Args(sub="ack", task=str(t["seq"]), id=m["id"][:8],
                                           session="acker", noop="   "))
        self.assertIn("--noop requires a reason", out)
        self.assertEqual(ts.load_task(t["id"])["memos"][-1].get("acks"), [])

    def test_ack_with_noop_and_a_reason_is_recorded(self):
        t = self._task()
        m = self._memo(t)
        self._out(ts.cmd_memo, _Args(sub="ack", task=str(t["seq"]), id=m["id"][:8],
                                     session="acker", noop="already true in the store"))
        disp = ts.load_task(t["id"])["memos"][-1]["acks"][0]["disposition"]
        self.assertEqual(disp, {"kind": "noop", "value": "already true in the store"})

    def test_two_dispositions_at_once_are_refused(self):
        t = self._task()
        m = self._memo(t)
        out = self._out(ts.cmd_memo, _Args(sub="ack", task=str(t["seq"]), id=m["id"][:8],
                                           session="acker", decision=True, noop="also this"))
        self.assertIn("exactly ONE disposition", out)
        self.assertEqual(ts.load_task(t["id"])["memos"][-1].get("acks"), [])

    def test_disposition_shows_in_the_ack_ledger(self):
        t = self._task()
        m = self._memo(t)
        self._out(ts.cmd_memo, _Args(sub="ack", task=str(t["seq"]), id=m["id"][:8],
                                     session="acker", memory="some-note-name"))
        listing = self._out(ts.cmd_memo, _Args(sub="show", task=str(t["seq"]), id=None))
        self.assertIn("acker→memory", listing)

    def test_slash_surface_routes_memory_and_noop_prefixes(self):
        t = self._task()
        ts.set_link("acker", t["id"])
        m = self._memo(t)
        self._out(lambda a: ts._todo_memo(a, "ack %s memory:some-note-name" % m["id"][:8]),
                  _Args(session="acker"))
        disp = ts.load_task(t["id"])["memos"][-1]["acks"][0]["disposition"]
        self.assertEqual(disp["kind"], "memory")

    def test_slash_surface_bare_ack_fails(self):
        t = self._task()
        ts.set_link("acker", t["id"])
        m = self._memo(t)
        out = self._out(lambda a: ts._todo_memo(a, "ack %s" % m["id"][:8]),
                        _Args(session="acker"))
        self.assertIn("--noop", out)
        self.assertEqual(ts.load_task(t["id"])["memos"][-1].get("acks"), [])


# ---------------------------------------------------------------------------
# M2 — memos declare what they correct.
# ---------------------------------------------------------------------------

class TestMemoCorrects(_Base):

    def test_corrects_is_stored_and_absent_when_not_declared(self):
        t = self._task()
        m = ts.memo_send(t, "plain", from_sid="s")
        self.assertNotIn("corrects", m)
        m2 = ts.memo_send(t, "fix", from_sid="s",
                          corrects=["some-note-name", "decision:2", "ab12cd34"])
        self.assertEqual(m2["corrects"], ["some-note-name", "decision:2", "ab12cd34"])

    def test_memo_show_displays_the_targets_prominently(self):
        t = self._task()
        m = ts.memo_send(t, "the body", from_sid="s", corrects=["some-note-name"])
        ts.save_task(t)
        full = self._out(ts.cmd_memo, _Args(sub="show", task=str(t["seq"]), id=m["id"][:8]))
        self.assertIn("CORRECTS: some-note-name", full)
        # …above the body, so it can't be missed
        self.assertLess(full.index("CORRECTS"), full.index("the body"))

    def test_corrects_memo_refuses_a_disposition_free_ack_and_names_the_target(self):
        t = self._task()
        m = ts.memo_send(t, "the body", from_sid="s", corrects=["some-note-name"])
        ts.save_task(t)
        out = self._out(ts.cmd_memo, _Args(sub="ack", task=str(t["seq"]), id=m["id"][:8],
                                           session="acker"))
        self.assertIn("CORRECTS some-note-name", out)
        self.assertEqual(ts.load_task(t["id"])["memos"][-1].get("acks"), [])

    def test_corrects_memo_accepts_an_explicit_noop(self):
        # --noop is allowed against a --corrects memo, but only said out loud + with a
        # reason. That is the difference between a receipt and a decision.
        t = self._task()
        m = ts.memo_send(t, "the body", from_sid="s", corrects=["some-note-name"])
        ts.save_task(t)
        self._out(ts.cmd_memo, _Args(sub="ack", task=str(t["seq"]), id=m["id"][:8],
                                     session="acker", noop="that note was already fixed"))
        disp = ts.load_task(t["id"])["memos"][-1]["acks"][0]["disposition"]
        self.assertEqual(disp["kind"], "noop")

    def test_unacked_nag_flags_that_a_memo_carries_corrections(self):
        t = self._task()
        ts.memo_send(t, "the body", from_sid="sender", corrects=["some-note-name"])
        ts.save_task(t)
        nag = ts.memo_pending_brief(ts.load_task(t["id"]), "viewer")
        self.assertIn("CORRECTS: some-note-name", nag)

    def test_send_reports_the_declared_corrections(self):
        t = self._task()
        out = self._out(ts.cmd_memo, _Args(sub="send", task=str(t["seq"]),
                                           text="the fix", session="me",
                                           corrects=["some-note-name"]))
        self.assertIn("corrects: some-note-name", out)


# ---------------------------------------------------------------------------
# M4 — correction-language safety net.
# ---------------------------------------------------------------------------

class TestCorrectionLanguage(_Base):

    def test_pattern_list_is_a_module_level_constant(self):
        self.assertIn("correction", ts.CORRECTION_PATTERNS)
        self.assertIn("no longer", ts.CORRECTION_PATTERNS)
        self.assertIn("stop doing", ts.CORRECTION_PATTERNS)

    def test_matching_is_case_insensitive(self):
        self.assertEqual(ts.correction_language("A CORRECTION to the plan"), ["correction"])
        self.assertEqual(ts.correction_language("nothing to see"), [])
        self.assertIn("no longer", ts.correction_language("that is No Longer true"))

    def test_send_warns_when_correction_language_has_no_declared_target(self):
        t = self._task()
        out = self._out(ts.cmd_memo, _Args(sub="send", task=str(t["seq"]),
                                           text="Correction: the old plan is withdrawn",
                                           session="me"))
        self.assertIn("reads like a correction", out)
        self.assertIn("--corrects", out)
        # warns, never blocks — the memo is still posted
        self.assertEqual(len(ts.load_task(t["id"])["memos"]), 1)

    def test_send_does_not_warn_when_corrects_is_declared(self):
        t = self._task()
        out = self._out(ts.cmd_memo, _Args(sub="send", task=str(t["seq"]),
                                           text="Correction: the old plan is withdrawn",
                                           session="me", corrects=["some-note-name"]))
        self.assertNotIn("reads like a correction", out)

    def test_send_stays_quiet_on_ordinary_correspondence(self):
        t = self._task()
        out = self._out(ts.cmd_memo, _Args(sub="send", task=str(t["seq"]),
                                           text="the cache warms on first read",
                                           session="me"))
        self.assertNotIn("reads like a correction", out)

    def test_ack_prints_a_prominent_durable_store_reminder(self):
        t = self._task()
        m = ts.memo_send(t, "This supersedes the earlier permission note", from_sid="s")
        ts.save_task(t)
        out = self._out(ts.cmd_memo, _Args(sub="ack", task=str(t["seq"]), id=m["id"][:8],
                                           session="acker", noop="noted"))
        self.assertIn("REMINDER", out)
        self.assertIn("receipt, not an integration", out)

    def test_ack_reminder_is_silent_when_corrects_was_declared(self):
        t = self._task()
        m = ts.memo_send(t, "This supersedes the earlier note", from_sid="s",
                         corrects=["some-note-name"])
        ts.save_task(t)
        out = self._out(ts.cmd_memo, _Args(sub="ack", task=str(t["seq"]), id=m["id"][:8],
                                           session="acker", memory="some-note-name"))
        self.assertNotIn("REMINDER", out)

    def test_nag_flags_correction_language_without_a_declared_target(self):
        t = self._task()
        ts.memo_send(t, "the earlier guidance is withdrawn", from_sid="sender")
        ts.save_task(t)
        nag = ts.memo_pending_brief(ts.load_task(t["id"]), "viewer")
        self.assertIn("reads as a correction", nag)


# ---------------------------------------------------------------------------
# DECLARE vs DESCRIBE — the fourth and last check in this subsystem to learn it.
#
# The keyword alone says a word is PRESENT and nothing about what it is ABOUT, and this
# backstop shipped matching the vocabulary alone. Three checks had already been fixed
# the same way (drift 2.12.0, unlinked supersession 2.13.0, stale steps 2.13.1), so
# this one now routes through the SAME guard — `heal.declaring_hits` — rather than
# growing a fourth heuristic for one problem. Only the vocabulary is its own.
# ---------------------------------------------------------------------------

class TestCorrectionDeclareVsDescribe(_Base):

    def test_a_memo_announcing_its_own_correction_still_warns(self):
        # The shape the backstop exists for: it is retracting something the reader is
        # expected to already believe.
        self.assertEqual(
            ts.correction_language(
                "Correction to my earlier memo: the permission model changed"),
            ["correction"])

    def test_a_release_note_mentioning_a_superseded_ancestor_is_not_a_correction(self):
        # 'supersede' here is an adjective on ANOTHER noun (an ancestor), in a memo
        # that describes a feature. It retracts nothing.
        self.assertEqual(
            ts.correction_language(
                "Shipped 2.13.1: heal now distinguishes a step that declares itself "
                "stale from one that merely mentions a superseded ancestor"),
            [])

    def test_somebody_elses_retraction_is_not_this_memos_correction(self):
        # The subject standing in front of 'withdrawn' is a third party, so the memo is
        # REPORTING a retraction, not making one.
        self.assertEqual(
            ts.correction_language(
                "FYI the upstream library withdrawn its 3.0 release, so we stay on 2.x"),
            [])

    def test_the_guard_is_the_one_the_other_checks_share(self):
        # Not a fourth heuristic: the same `declaring_hits` reading of the word standing
        # in front of the match, with this check's own vocabulary.
        self.assertEqual(
            heal.declaring_hits("a superseded ancestor", ["supersede"],
                                heal.SELF_DECLARING_QUALIFIERS), [])
        self.assertEqual(
            heal.declaring_hits("this supersedes the earlier note", ["supersede"],
                                heal.SELF_DECLARING_QUALIFIERS), ["supersede"])
        # …and the NOUN vocabulary is what keeps "a correction" a declaration while the
        # identical article in front of a participle is not.
        self.assertEqual(
            heal.declaring_hits("a correction to the plan", ["correction"],
                                heal.NOUN_DECLARING_QUALIFIERS), ["correction"])

    def test_a_declared_correction_that_passes_corrects_stays_silent(self):
        # It already engaged its target: the ack cannot be given without a disposition
        # that names what happened to it, so the backstop has nothing left to add.
        t = self._task()
        out = self._out(ts.cmd_memo, _Args(
            sub="send", task=str(t["seq"]), session="me",
            text="Correction to my earlier memo: the permission model changed",
            corrects=["decision:1"]))
        self.assertNotIn("reads like a correction", out)
        memo = ts.load_task(t["id"])["memos"][0]
        acked = self._out(ts.cmd_memo, _Args(
            sub="ack", task=str(t["seq"]), id=memo["id"][:8], session="acker",
            memory="the-permission-note"))
        self.assertNotIn("REMINDER", acked)
        # …while the same body WITHOUT a target is exactly what it does warn about.
        self.assertEqual(
            ts.correction_language(
                "Correction to my earlier memo: the permission model changed"),
            ["correction"])


if __name__ == "__main__":
    unittest.main()
