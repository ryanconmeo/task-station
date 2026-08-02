"""WS7/WS7b cost HUD: the toggleable status-line segment renderer (lib/hud.py) + its
config flags + the setup installer + the relocated turn-baseline snapshot + the
full-ledger data paths (scan-all / import-costbar).

The renderer is pure — it takes the parsed status-line stdin payload, an optional
seeded ledger (store) + attached task, and injected `now`/`turn` figures, and emits
exact ANSI rows. So tests seed a real SQLite ledger, build the payload dict, and
assert the rendered rows byte-for-byte against strings composed from hud's own
palette constants (robust to the exact escape values). No clock/file IO in the
render path; the snapshot dance + anchor cache are exercised separately.

Reset times render in LOCAL time; the classes that hardcode a local clock string pin
TZ=UTC via the _FixedTZ mixin (restored in tearDown so it never leaks to other test
modules), and LocalTimeTest flips TZ to a fixed offset to prove the conversion.
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

import config          # noqa: E402
import hud             # noqa: E402
import store           # noqa: E402
import usage           # noqa: E402
import setup as station_setup  # noqa: E402


class _FixedTZ:
    """setUp/tearDown mixin pinning the process to UTC so tests that hardcode a LOCAL
    reset-time string (e.g. `4:32pm`) are deterministic. Restored in tearDown so this
    never leaks into other test modules under `unittest discover` (obsidian day-file
    tests derive local dates)."""
    TZ = "UTC"

    def _tz_setup(self):
        self._saved_tz = os.environ.get("TZ")
        os.environ["TZ"] = self.TZ
        time.tzset()

    def _tz_teardown(self):
        if self._saved_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._saved_tz
        time.tzset()


def _epoch(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp()


def _iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def _model(out, cost, in_=0, msgs=1):
    """A stored per-model bucket (cost None ⇒ unpriced/unknown model)."""
    return {"in": in_, "out": out, "cache_read": 0, "cache_w5m": 0, "cache_w1h": 0,
            "web": 0, "msgs": msgs, "cost_usd": cost}


class _LedgerBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hud-test-")
        self.store_dir = os.path.join(self.tmp, "store")
        os.makedirs(self.store_dir, exist_ok=True)
        store.reset_cache()
        self.be = store.get_backend(self.store_dir)

    def tearDown(self):
        store.reset_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self, sid, models, task_id="t1", first_ts=1000, last_ts=1000, sidechain=None):
        self.be.upsert_session_usage({
            "session_id": sid, "task_id": task_id, "first_ts": first_ts,
            "last_ts": last_ts, "models": models, "sidechain": sidechain or {}})


# --------------------------------------------------------------- header ------

class HeaderTest(_LedgerBase):
    def test_header_badge_plus_inline_task_segment(self):
        task = {"id": "t1", "seq": 360, "color": "red",
                "title": "task-station competitive review"}
        out = hud.render({"model": {"display_name": "Fable 5"}}, rows=[], task=task,
                         eco=False)
        seg = hud._task_segment(task)
        # Whole badge violet: dot + model name (no RESET between), name bolded; then a
        # dim │ separator before the inline task segment.
        expected = (hud.VIOLET + "⏺ " + hud.LABEL + "Fable 5" + hud.RESET
                    + "  " + hud.DIM + "│" + hud.RESET + "  " + seg)
        self.assertEqual(out, expected)
        # The inline segment carries the number + title (the tag is category-colored).
        self.assertIn(hud.HDR_SEQ + "#360" + hud.RESET, seg)
        self.assertIn(hud.HDR_TITLE + "task-station competitive review" + hud.RESET, seg)

    def test_header_badge_only_without_task(self):
        out = hud.render({"model": {"display_name": "Opus 4.8"}}, rows=[], task=None,
                         eco=False)
        self.assertEqual(out, hud.VIOLET + "⏺ " + hud.LABEL + "Opus 4.8" + hud.RESET)

    def test_no_model_no_header(self):
        # Row-only payloads (no model.display_name) render no header line.
        out = hud.render({"cost": {"total_cost_usd": 0.0}}, rows=["session"],
                         session_out=0, eco=False)
        self.assertNotIn("⏺", out)

    def test_header_is_first_line(self):
        task = {"id": "t1", "seq": 7, "color": "red", "title": "x"}
        out = hud.render({"model": {"display_name": "Fable 5"},
                          "cost": {"total_cost_usd": 0.0}},
                         rows=["session"], task=task, session_out=0, eco=False)
        self.assertTrue(out.split("\n")[0].startswith(hud.VIOLET + "⏺ "))


# --------------------------------------------------------------- pure rows ---

class RowRenderTest(_FixedTZ, _LedgerBase):
    def setUp(self):
        super().setUp()
        self._tz_setup()

    def tearDown(self):
        self._tz_teardown()
        super().tearDown()

    def test_session_row_cost_tokens_and_context_left(self):
        # ctx 73% ⇒ GREEN (rem_color >50), appended as " · 73% left" (costbar parity).
        out = hud.render({"cost": {"total_cost_usd": 0.0},
                          "context_window": {"remaining_percentage": 73}},
                         rows=["session"], session_out=2400, eco=False)
        expected = (hud.LABEL + "Session".ljust(hud.LW) + hud.RESET + hud.SEP
                    + hud.GREEN + hud.LABEL + "$0.00" + hud.RESET
                    + " " + hud.TOK + "out 2k" + hud.RESET
                    + hud._sep() + hud.GREEN + hud.LABEL + "73% left" + hud.RESET)
        self.assertEqual(out, expected)

    def test_session_row_omits_context_when_absent(self):
        out = hud.render({"cost": {"total_cost_usd": 0.0}},
                         rows=["session"], session_out=2400, eco=False)
        self.assertNotIn("% left", out)

    def test_session_context_low_is_red(self):
        # ≤20% remaining ⇒ RED (rem_color).
        out = hud.render({"cost": {"total_cost_usd": 0.0},
                          "context_window": {"remaining_percentage": 12}},
                         rows=["session"], session_out=0, eco=False)
        self.assertIn(hud.RED + hud.LABEL + "12% left" + hud.RESET, out)

    def test_fivehour_row_left_and_local_reset(self):
        r5 = _epoch(2026, 7, 4, 16, 32)
        out = hud.render({"rate_limits": {
            "five_hour": {"used_percentage": 41, "resets_at": r5}}},
            rows=["fivehour"], eco=False)
        expected = (hud.LABEL + "5-hour".ljust(hud.LW) + hud.RESET + hud.SEP
                    + hud.GREEN + hud.LABEL + "59% left" + hud.RESET   # 100-41, used_color(41)=GREEN
                    + " " + hud.LABEL_GRAY + "(↺" + hud._fmt_reset_5h(r5) + ")" + hud.RESET)
        self.assertEqual(out, expected)
        self.assertIn("(↺4:32 PM)", out)         # local, %I:%M %p, leading-zero-stripped

    def test_fivehour_used_high_is_red_low_left(self):
        # used=74 ⇒ 26% left, YELLOW (used_color 74); used=100 ⇒ 0% left, RED.
        y = hud.render({"rate_limits": {"five_hour": {"used_percentage": 74}}},
                       rows=["fivehour"], eco=False)
        self.assertIn(hud.YELLOW + hud.LABEL + "26% left" + hud.RESET, y)
        r = hud.render({"rate_limits": {"five_hour": {"used_percentage": 100}}},
                       rows=["fivehour"], eco=False)
        self.assertIn(hud.RED + hud.LABEL + "0% left" + hud.RESET, r)

    def test_fivehour_dash_when_data_missing(self):
        # costbar renders the 5-hour row unconditionally; NEITHER field ⇒ a DIM em-dash.
        out = hud.render({"cost": {"total_cost_usd": 1.0}}, rows=["fivehour"], eco=False)
        expected = (hud.LABEL + "5-hour".ljust(hud.LW) + hud.RESET + hud.SEP
                    + hud.DIM + "—" + hud.RESET)
        self.assertEqual(out, expected)

    def test_fivehour_reset_shows_without_used(self):
        # reset present but used% absent (e.g. only the cached anchor survived): the
        # reset ts still renders — NO em-dash placeholder (it's not a fully-empty row).
        r5 = _epoch(2026, 7, 4, 16, 32)
        out = hud.render({"rate_limits": {"five_hour": {"resets_at": r5}}},
                         rows=["fivehour"], eco=False)
        self.assertIn("(↺" + hud._fmt_reset_5h(r5) + ")", out)
        self.assertNotIn("—", out)
        self.assertNotIn("% left", out)

    def test_fivehour_shows_window_cost_from_ledger(self):
        # With a store, the 5-hour row leads with the derived $ + out tokens for sessions
        # whose activity falls in the [reset-5h, now] window, then the % left + reset.
        now = _epoch(2026, 7, 4, 16, 0)
        r5 = now + 3600
        self._seed("recent", {"claude-opus-4-8": _model(2000, 0.08)},
                   first_ts=now - 1800, last_ts=now - 1800)      # inside the 5h window
        self._seed("old", {"claude-opus-4-8": _model(9000, 5.0)},
                   first_ts=now - 6 * 3600, last_ts=now - 6 * 3600)  # >5h ago, excluded
        out = hud.render({"rate_limits": {
            "five_hour": {"used_percentage": 20, "resets_at": r5}}},
            rows=["fivehour"], store=self.be, now=now, eco=False)
        self.assertIn("$0.08", out)               # recent session counted
        self.assertNotIn("$5.0", out)             # old session excluded
        self.assertIn("out 2k", out)
        self.assertIn("80% left", out)            # 100-20
        self.assertIn("(↺" + hud._fmt_reset_5h(r5) + ")", out)

    def test_week_row_merges_limit_util_reset_and_dots(self):
        now = _epoch(2026, 7, 4, 12, 0)
        wk_reset = now + 86400
        self._seed("s1", {"claude-opus-4-8": _model(1000, 0.05)},
                   first_ts=now - 3600, last_ts=now - 3600)
        out = hud.render({"rate_limits": {
            "seven_day": {"used_percentage": 63, "resets_at": wk_reset}}},
            rows=["week"], store=self.be, now=now, eco=False)
        dots = hud.week_dots(wk_reset, now)
        cal = hud._cal_week(now - 3600, now)      # first session this week ⇒ Week 1
        expected = (hud.LABEL + ("Week %d" % cal).ljust(hud.LW) + hud.RESET + hud.SEP
                    + hud.GREEN + hud.LABEL + "$0.05" + hud.RESET
                    + " " + hud.TOK + "out 1k" + hud.RESET
                    + hud._sep() + hud.YELLOW + hud.LABEL + "37% left" + hud.RESET
                    + " " + hud.LABEL_GRAY + "(↺" + hud._fmt_reset_week(wk_reset) + ")" + hud.RESET
                    + " " + dots)
        self.assertEqual(out, expected)
        self.assertEqual(cal, 1)
        self.assertIn("Week 1", out)

    def test_week_row_no_limit_util_when_payload_absent(self):
        # week_used absent (no seven_day.used_percentage) ⇒ no util %, but the ledger
        # $ + the reset ts + dots still render (reset is independent of util now).
        now = _epoch(2026, 7, 4, 12, 0)
        wk_reset = now + 86400
        self._seed("s1", {"claude-opus-4-8": _model(1000, 0.05)},
                   first_ts=now - 3600, last_ts=now - 3600)
        out = hud.render({"rate_limits": {"seven_day": {"resets_at": wk_reset}}},
                         rows=["week"], store=self.be, now=now, eco=False)
        self.assertIn("$0.05", out)
        self.assertNotIn("63%", out)
        self.assertIn("(↺" + hud._fmt_reset_week(wk_reset) + ")", out)   # reset shows

    def test_week_excludes_old_sessions(self):
        now = _epoch(2026, 7, 4, 12, 0)
        wk_reset = now + 86400
        self._seed("old", {"claude-opus-4-8": _model(1000, 9.0)},
                   first_ts=now - 30 * 86400, last_ts=now - 30 * 86400)
        self._seed("cur", {"claude-opus-4-8": _model(500, 0.02)},
                   first_ts=now - 3600, last_ts=now - 3600)
        out = hud.render({"rate_limits": {"seven_day": {"resets_at": wk_reset}}},
                         rows=["week"], store=self.be, now=now, eco=False)
        self.assertIn("$0.02", out)
        self.assertNotIn("$9.0", out)

    def test_total_row_since_and_api_mode(self):
        first = _epoch(2026, 7, 4, 11, 0)
        self._seed("s1", {"claude-opus-4-8": _model(1000, 0.05)},
                   first_ts=first, last_ts=first)
        out = hud.render({}, rows=["total"], store=self.be, now=first + 3600, eco=False)
        expected = (hud.LABEL + "Total".ljust(hud.LW) + hud.RESET + hud.SEP
                    + hud.LABEL + "$0.05" + hud.RESET + "  "
                    + hud.TOK + "out 1k tokens" + hud.RESET
                    + hud.DIM + " since " + hud._fmt_since(first) + hud.RESET)
        self.assertEqual(out, expected)

    def test_total_row_subscription_label(self):
        self._seed("s1", {"claude-opus-4-8": _model(1000, 0.05)})
        out = hud.render({}, rows=["total"], store=self.be, now=2000,
                         billing_mode="subscription", eco=False)
        self.assertIn("(API-equiv value)", out)

    def test_task_row_shows_seq_derived_reported_tokens(self):
        self._seed("s1", {"claude-opus-4-8": _model(1000, 0.02)}, task_id="t1")
        task = {"id": "t1", "seq": 7, "cost": {"total_usd": 5.0}}
        out = hud.render({}, rows=["task"], store=self.be, task=task, now=2000, eco=False)
        # Label now carries the task number ("Task #7"); derived-$ colour uses the ledger
        # session bands (here <3 sessions ⇒ fixed fallback, 0.02 ∈ [0.01,0.05) ⇒ YELLOW).
        expected = (hud.LABEL + "Task #7".ljust(hud.LW) + hud.RESET + hud.SEP
                    + hud.YELLOW + hud.LABEL + "$0.02" + hud.RESET
                    + hud.DIM + " derived" + hud.RESET
                    + hud.DIM + " · $5.00 reported" + hud.RESET
                    + hud.DIM + " · " + hud.RESET + hud.TOK + "out 1k" + hud.RESET)
        self.assertEqual(out, expected)
        self.assertIn("Task #7", out)            # the number is shown in the row now

    def test_task_cost_color_uses_ledger_stdev_bands(self):
        # ≥3 priced sessions ⇒ Task derived-$ colour keys off ledger μ/σ, like Session.
        for i, c in enumerate((0.10, 0.20, 0.30)):
            self._seed("s%d" % i, {"claude-opus-4-8": _model(100, c)}, task_id="t1")
        task = {"id": "t1", "seq": 4, "cost": {}}
        out = hud.render({}, rows=["task"], store=self.be, task=task, now=2000, eco=False)
        # derived total = 0.60; μ=0.20, μ+σ≈0.282 ⇒ 0.60 ≥ hi ⇒ RED.
        self.assertIn(hud.RED + hud.LABEL + "$0.60", out)

    def test_task_row_omits_reported_when_zero(self):
        self._seed("s1", {"claude-opus-4-8": _model(1000, 0.05)}, task_id="t1")
        task = {"id": "t1", "seq": 3, "cost": {}}
        out = hud.render({}, rows=["task"], store=self.be, task=task, now=2000, eco=False)
        self.assertIn(" derived", out)
        self.assertNotIn("reported", out)

    def test_unknown_model_no_unknown_marker_in_task_row(self):
        # The (+unknown) caveat is GONE from the HUD — it lives in usage --task now.
        self._seed("s1", {"claude-mystery-9": _model(1000, None)}, task_id="t1")
        task = {"id": "t1", "seq": 3, "cost": {}}
        out = hud.render({}, rows=["task"], store=self.be, task=task, now=2000, eco=False)
        self.assertNotIn("(+unknown)", out)


# ------------------------------------------------------------ local time -----

class LocalTimeTest(unittest.TestCase):
    def setUp(self):
        self._saved_tz = os.environ.get("TZ")

    def tearDown(self):
        if self._saved_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._saved_tz
        time.tzset()

    def test_reset_times_shift_with_local_zone(self):
        # Fixed UTC-5, no DST (POSIX west-positive offset): 16:00Z → 11:00 local → "11am".
        os.environ["TZ"] = "EST5"
        time.tzset()
        self.assertEqual(hud._fmt_reset_5h(_epoch(2026, 7, 4, 16, 0)), "11:00 AM")
        # Weekly reset: 2026-07-08 02:00Z → 2026-07-07 21:00 local → "Tue 9:00 PM".
        self.assertEqual(hud._fmt_reset_week(_epoch(2026, 7, 8, 2, 0)), "Tue 9:00 PM")


# ---------------------------------------------------------- toggles/degrade ---

class ToggleDegradeTest(_LedgerBase):
    def test_row_subset_order_is_honored(self):
        out = hud.render({"cost": {"total_cost_usd": 0.0},
                          "rate_limits": {"five_hour": {"used_percentage": 10}}},
                         rows=["fivehour", "session"], session_out=0, eco=False)
        lines = out.split("\n")
        self.assertEqual(len(lines), 2)
        self.assertIn("5-hour", lines[0])
        self.assertIn("Session", lines[1])

    def test_no_ledger_skips_ledger_rows(self):
        out = hud.render({"cost": {"total_cost_usd": 0.0}},
                         rows=hud.DEFAULT_ROWS, eco=False)
        self.assertIn("Session", out)
        self.assertNotIn("Week", out)
        self.assertNotIn("Total", out)
        self.assertNotIn("Task", out)

    def test_no_task_skips_task_row(self):
        self._seed("s1", {"claude-opus-4-8": _model(1000, 0.05)})
        out = hud.render({}, rows=["total", "task"], store=self.be, task=None,
                         now=2000, eco=False)
        self.assertIn("Total", out)
        self.assertNotIn("Task", out)

    def test_empty_renders_nothing(self):
        self.assertEqual(hud.render({}, rows=[], eco=False), "")
        self.assertEqual(hud.render({}, rows=["week", "total", "task"], store=None,
                                    eco=False), "")


class ThresholdTest(_LedgerBase):
    def test_session_threshold_from_ledger_mean_sd(self):
        for i, c in enumerate((0.10, 0.20, 0.30)):
            self._seed("s%d" % i, {"claude-opus-4-8": _model(100, c)}, task_id="t%d" % i)
        out = hud.render({"cost": {"total_cost_usd": 0.25}}, rows=["session"],
                         store=self.be, session_out=0, now=2000, eco=False)
        self.assertIn(hud.YELLOW + hud.LABEL + "$0.25", out)

    def test_session_threshold_fixed_fallback_under_three(self):
        self._seed("s1", {"claude-opus-4-8": _model(100, 0.02)})
        out = hud.render({"cost": {"total_cost_usd": 0.02}}, rows=["session"],
                         store=self.be, session_out=0, now=2000, eco=False)
        self.assertIn(hud.YELLOW + hud.LABEL + "$0.02", out)


# ------------------------------------------------ window bucket populations ---

class _FakeStore:
    """Minimal store stand-in: only `all_session_usage()`, returning canned rows."""

    def __init__(self, rows):
        self._rows = rows

    def all_session_usage(self):
        return self._rows


def _srow(last_ts, cost, first_ts=None):
    """A synthetic ledger row: one priced opus bucket, activity at `last_ts`
    (defaulting first_ts=last_ts unless a straddle is being modeled)."""
    return {"models": {"claude-opus-4-8": _model(100, cost)}, "sidechain": {},
            "first_ts": last_ts if first_ts is None else first_ts, "last_ts": last_ts}


class WindowBucketTest(unittest.TestCase):
    """usage.ledger_totals builds anchor-aligned CLOSED-window $ populations for μ/σ
    coloring — current partial window excluded, attribution by last-activity."""

    A5 = 1_000_000                     # a 5-hour window anchor (five_hour_start_ts)
    AW = 1_000_000                     # a weekly window anchor (week_start_ts)

    def test_five_hour_buckets_exclude_current_sum_and_attribute_by_last_ts(self):
        A5, W = self.A5, usage.FIVE_HOUR_SECS
        rows = [
            _srow(A5 + 100, 0.50),                       # bin 0 (current) ⇒ EXCLUDED
            _srow(A5 - 100, 0.10),                       # bin -1
            _srow(A5 - 200, 0.20),                       # bin -1 (⇒ summed with above)
            _srow(A5 - W - 1, 0.40),                     # bin -2
            _srow(A5 - 500, 0.05, first_ts=A5 - W - 500),  # straddles -2→-1, lands in -1
        ]
        t = usage.ledger_totals(_FakeStore(rows), week_start_ts=None,
                                now_ts=A5 + 10, five_hour_start_ts=A5)
        got = sorted(t["five_hour_bucket_costs"])
        # bin -1 = 0.10+0.20+0.05 = 0.35 (straddle attributed by last_ts, not to -2),
        # bin -2 = 0.40; current bin (0.50) excluded ⇒ two closed windows only.
        self.assertEqual(len(got), 2)
        self.assertAlmostEqual(got[0], 0.35)
        self.assertAlmostEqual(got[1], 0.40)
        # week buckets untouched when week_start_ts is None.
        self.assertEqual(t["week_bucket_costs"], [])

    def test_week_buckets_exclude_current_and_sum_same_window(self):
        AW, W = self.AW, usage.WEEK_SECS
        rows = [
            _srow(AW + 100, 5.0),                        # bin 0 (current) ⇒ EXCLUDED
            _srow(AW - 100, 10.0),                       # bin -1
            _srow(AW - 200, 20.0),                       # bin -1 (⇒ summed)
            _srow(AW - W - 100, 40.0),                   # bin -2
        ]
        t = usage.ledger_totals(_FakeStore(rows), week_start_ts=AW,
                                now_ts=AW + 10, five_hour_start_ts=None)
        got = sorted(t["week_bucket_costs"])
        self.assertEqual(len(got), 2)
        self.assertAlmostEqual(got[0], 30.0)             # -1: 10+20
        self.assertAlmostEqual(got[1], 40.0)             # -2
        self.assertEqual(t["five_hour_bucket_costs"], [])

    def test_unpriced_session_does_not_contribute_to_buckets(self):
        A5 = self.A5
        rows = [
            _srow(A5 - 100, 0.10),                                       # priced ⇒ counts
            {"models": {"mystery": _model(100, None)}, "sidechain": {},   # unpriced ⇒ skip
             "first_ts": A5 - 100, "last_ts": A5 - 100},
        ]
        t = usage.ledger_totals(_FakeStore(rows), week_start_ts=None,
                                now_ts=A5 + 10, five_hour_start_ts=A5)
        self.assertEqual(len(t["five_hour_bucket_costs"]), 1)
        self.assertAlmostEqual(t["five_hour_bucket_costs"][0], 0.10)
        self.assertTrue(t["any_unpriced"])


class WindowBandRenderTest(_LedgerBase):
    """End-to-end: the 5-hour row falls back to fixed bands below 3 closed windows and
    switches to μ/σ over its own closed-window population at ≥3."""

    def _seed_five_hour(self, closed_windows):
        # `now` sits inside the current 5h window; hour_reset = now+600 ⇒ anchor A5 =
        # reset-5h. One session in the current window ($1.00), plus one per requested
        # closed window (each $10.00) placed in bins -1, -2, ….
        now = _epoch(2026, 7, 4, 16, 0)
        hour_reset = now + 600
        a5 = hour_reset - hud._FIVE_HOUR_SECS
        self._seed("cur", {"claude-opus-4-8": _model(100, 1.0)},
                   first_ts=now - 100, last_ts=now - 100)          # current window $1.00
        for i in range(closed_windows):
            ts = a5 - 100 - i * hud._FIVE_HOUR_SECS                # bin -(i+1)
            self._seed("w%d" % i, {"claude-opus-4-8": _model(100, 10.0)},
                       task_id="t%d" % i, first_ts=ts, last_ts=ts)
        return now, hour_reset

    def test_fixed_fallback_below_three_closed_windows(self):
        now, hour_reset = self._seed_five_hour(2)                  # only 2 closed windows
        out = hud.render({"rate_limits": {"five_hour": {"resets_at": hour_reset}}},
                         rows=["fivehour"], store=self.be, now=now, eco=False)
        # <3 windows ⇒ fixed (0.01, 0.05); $1.00 ≥ 0.05 ⇒ RED.
        self.assertIn(hud.RED + hud.LABEL + "$1.00", out)

    def test_stddev_bands_at_three_closed_windows(self):
        now, hour_reset = self._seed_five_hour(3)                  # 3 closed windows @ $10
        out = hud.render({"rate_limits": {"five_hour": {"resets_at": hour_reset}}},
                         rows=["fivehour"], store=self.be, now=now, eco=False)
        # μ=10, σ=0 ⇒ bands (10, 10); current $1.00 < 10 ⇒ GREEN (would be RED on fixed).
        self.assertIn(hud.GREEN + hud.LABEL + "$1.00", out)


# --------------------------------------------------------------------- eco ---

class EcoTest(_LedgerBase):
    def test_eco_on_by_default(self):
        # eco defaults ON now (Ryan asked for it back) — no explicit eco kwarg.
        out = hud.render({"cost": {"total_cost_usd": 0.01}}, rows=["session"],
                         session_out=1000, now=1800000000.0)
        self.assertIn("≈ driving", out)          # env_idx 0 ⇒ driving comparison
        self.assertIn("(gas car)", out)

    def test_eco_explicit_off(self):
        out = hud.render({"cost": {"total_cost_usd": 0.01}}, rows=["session"],
                         session_out=1000, now=1800000000.0, eco=False)
        self.assertNotIn("≈", out)

    def test_eco_on_every_token_row_skips_fivehour(self):
        # Every row with output tokens (Task/Total) carries an eco suffix; the 5-hour
        # row (no token value) never does.
        self._seed("s1", {"claude-opus-4-8": _model(1000, 0.05)}, task_id="t1")
        task = {"id": "t1", "seq": 1, "cost": {}}
        out = hud.render({"rate_limits": {"five_hour": {"used_percentage": 10}}},
                         rows=["task", "fivehour", "total"], store=self.be, task=task,
                         now=1800000000.0)
        by_label = {ln.split("│")[0]: ln for ln in out.split("\n")}
        self.assertIn("≈", next(v for k, v in by_label.items() if "Task" in k))
        self.assertNotIn("≈", next(v for k, v in by_label.items() if "5-hour" in k))
        self.assertIn("≈", next(v for k, v in by_label.items() if "Total" in k))

    def test_task_row_no_eco_when_zero_tokens(self):
        # A task with no output tokens has no token value ⇒ no eco suffix.
        self._seed("s1", {"claude-opus-4-8": _model(0, 0.0)}, task_id="t1")
        task = {"id": "t1", "seq": 1, "cost": {}}
        out = hud.render({}, rows=["task"], store=self.be, task=task, now=1800000000.0)
        self.assertNotIn("≈", out)

    def test_total_eco_suffix_present(self):
        # Total's output tokens drive an eco comparison (env_idx 0 ⇒ driving).
        self._seed("s1", {"claude-opus-4-8": _model(100000, 0.5)}, task_id="t1")
        out = hud.render({}, rows=["total"], store=self.be, now=1800000000.0)
        self.assertIn("≈ driving", out)
        self.assertIn("(gas car)", out)

    def test_eco_comparison_rotation(self):
        self.assertIn("cups of coffee", hud.eco_comparison(100000, 3))
        self.assertEqual(hud.eco_comparison(0, 3), "")


# --------------------------------------------------------------- color bands ---

class ColorBandTest(unittest.TestCase):
    def test_rem_color_bands(self):
        # costbar rem_color: ≤20 RED, ≤50 YELLOW, else GREEN.
        self.assertEqual(hud._rem_color(20), hud.RED)
        self.assertEqual(hud._rem_color(21), hud.YELLOW)
        self.assertEqual(hud._rem_color(50), hud.YELLOW)
        self.assertEqual(hud._rem_color(51), hud.GREEN)

    def test_used_color_bands(self):
        # costbar used_color: ≥80 RED, ≥50 YELLOW, else GREEN.
        self.assertEqual(hud._used_color(80), hud.RED)
        self.assertEqual(hud._used_color(79), hud.YELLOW)
        self.assertEqual(hud._used_color(50), hud.YELLOW)
        self.assertEqual(hud._used_color(49), hud.GREEN)


class DistThresholdTest(unittest.TestCase):
    """The generic μ/μ+σ band helper shared by the Session / 5-hour / Week rows."""

    def test_below_min_returns_fallback_exactly(self):
        # <3 points ⇒ the fixed fallback pair, verbatim (including the empty case).
        self.assertEqual(hud._dist_thresholds([], 0.01, 0.05), (0.01, 0.05))
        self.assertEqual(hud._dist_thresholds([1.0, 2.0], 50.0, 150.0), (50.0, 150.0))

    def test_identical_points_give_zero_sigma(self):
        # ≥3 identical points ⇒ σ=0 ⇒ (μ, μ).
        lo, hi = hud._dist_thresholds([5.0, 5.0, 5.0], 0.01, 0.05)
        self.assertAlmostEqual(lo, 5.0)
        self.assertAlmostEqual(hi, 5.0)

    def test_known_set_matches_hand_computed_population_stats(self):
        # μ = 0.20; population var = (0.01 + 0 + 0.01)/3; σ = sqrt(that).
        import math
        lo, hi = hud._dist_thresholds([0.10, 0.20, 0.30], 0.01, 0.05)
        sigma = math.sqrt(0.02 / 3)
        self.assertAlmostEqual(lo, 0.20)
        self.assertAlmostEqual(hi, 0.20 + sigma)

    def test_min_n_is_configurable(self):
        # Same two points fall back at min_n=3 but compute at min_n=2.
        self.assertEqual(hud._dist_thresholds([2.0, 4.0], 0.0, 9.0, min_n=3), (0.0, 9.0))
        lo, hi = hud._dist_thresholds([2.0, 4.0], 0.0, 9.0, min_n=2)
        self.assertAlmostEqual(lo, 3.0)          # μ
        self.assertAlmostEqual(hi, 4.0)          # μ+σ, σ=1.0


# --------------------------------------------------------------- formatters ---

class FormatTest(_FixedTZ, unittest.TestCase):
    def setUp(self):
        self._tz_setup()

    def tearDown(self):
        self._tz_teardown()

    def test_fmt_cost(self):
        self.assertEqual(hud.fmt_cost(0.0), "0.00")
        self.assertEqual(hud.fmt_cost(0.0032), "0.0032")
        self.assertEqual(hud.fmt_cost(12.5), "12.50")

    def test_fmt_tok(self):
        self.assertEqual(hud.fmt_tok(950), "950")
        self.assertEqual(hud.fmt_tok(1200), "1k")
        self.assertEqual(hud.fmt_tok(2_400_000), "2.4m")

    def test_fmt_reset_5h_costbar_format(self):
        # costbar `%I:%M %p` w/ stripped leading zero — minutes ALWAYS shown, uppercase
        # meridiem, bare space before it.
        self.assertEqual(hud._fmt_reset_5h(_epoch(2026, 7, 4, 21, 0)), "9:00 PM")
        self.assertEqual(hud._fmt_reset_5h(_epoch(2026, 7, 4, 21, 5)), "9:05 PM")
        self.assertEqual(hud._fmt_reset_5h(_epoch(2026, 7, 4, 4, 32)), "4:32 AM")

    def test_fmt_reset_week_costbar_format(self):
        # costbar `%a %-I:%M %p`: weekday + stripped-zero clock, NO month/day.
        self.assertEqual(hud._fmt_reset_week(_epoch(2026, 7, 8, 16, 30)), "Wed 4:30 PM")
        self.assertEqual(hud._fmt_reset_week(_epoch(2026, 7, 4, 9, 5)), "Sat 9:05 AM")

    def test_cal_week_counts_weeks_since_first(self):
        now = _epoch(2026, 7, 4, 12, 0)               # Sat, week of Jun 28
        self.assertEqual(hud._cal_week(now - 3600, now), 1)          # same week
        self.assertEqual(hud._cal_week(now - 21 * 86400, now), 4)    # 3 weeks earlier
        self.assertEqual(hud._cal_week(None, now), 1)                # no first date

    def test_week_dots_marks_reset_day_green(self):
        now = _epoch(2026, 7, 4, 12, 0)
        reset = _epoch(2026, 6, 30, 9, 0)
        dots = hud.week_dots(reset, now)
        self.assertEqual(dots.count("●"), 7)
        self.assertIn(hud.DOT_GREEN + "●", dots)


# --------------------------------------------------------------- snapshot ----

class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hud-snap-")
        self._saved = os.environ.get("TASK_STATION_HOME")
        os.environ["TASK_STATION_HOME"] = self.tmp

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("TASK_STATION_HOME", None)
        else:
            os.environ["TASK_STATION_HOME"] = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_turn_delta_lifecycle(self):
        sid = "sess-1"
        hud.observe(sid, {"cost": {"total_cost_usd": 1.0},
                          "context_window": {"current_usage": {"output_tokens": 500}}})
        hud.turn_start(sid)
        acc, delta, tout = hud.observe(sid, {
            "cost": {"total_cost_usd": 1.5},
            "context_window": {"current_usage": {"output_tokens": 800}}})
        self.assertEqual(acc, 1300)
        self.assertAlmostEqual(delta, 0.5)
        self.assertEqual(tout, 800)
        hud.turn_end(sid)
        acc2, delta2, tout2 = hud.observe(sid, {
            "cost": {"total_cost_usd": 1.5},
            "context_window": {"current_usage": {"output_tokens": 800}}})
        self.assertAlmostEqual(delta2, 0.5)
        self.assertEqual(tout2, 800)

    def test_week_anchor_cached_and_reused(self):
        payload = {"rate_limits": {"seven_day": {"resets_at": 1_800_000_000}}}
        self.assertEqual(hud.resolve_week_anchor(payload), 1_800_000_000)
        # A later payload with NO rate_limits reuses the cached anchor.
        self.assertEqual(hud.resolve_week_anchor({}), 1_800_000_000)

    def test_week_anchor_none_when_never_seen(self):
        self.assertIsNone(hud.resolve_week_anchor({}))

    def test_rate_limits_cached_and_folded_back_on_fresh_session(self):
        # A live payload caches BOTH windows' used% + reset...
        live = {"rate_limits": {
            "five_hour": {"used_percentage": 40, "resets_at": 1_800_000_000},
            "seven_day": {"used_percentage": 63, "resets_at": 1_800_500_000}}}
        self.assertEqual(hud.resolve_rate_limits(live), live["rate_limits"])
        # ...so a subsequent rate-limit-less payload (fresh session) still resolves the
        # full 5-hour + week data, including both reset timestamps.
        merged = hud.resolve_rate_limits({})
        self.assertEqual(merged["five_hour"]["resets_at"], 1_800_000_000)
        self.assertEqual(merged["five_hour"]["used_percentage"], 40)
        self.assertEqual(merged["seven_day"]["resets_at"], 1_800_500_000)

    def test_rate_limits_payload_field_wins_and_refreshes_cache(self):
        hud.resolve_rate_limits({"rate_limits": {
            "five_hour": {"used_percentage": 40, "resets_at": 1_800_000_000}}})
        # A newer live used% overwrites the cached one; the absent reset falls back.
        merged = hud.resolve_rate_limits({"rate_limits": {
            "five_hour": {"used_percentage": 55}}})
        self.assertEqual(merged["five_hour"]["used_percentage"], 55)
        self.assertEqual(merged["five_hour"]["resets_at"], 1_800_000_000)
        # And the refresh persisted.
        self.assertEqual(hud.resolve_rate_limits({})["five_hour"]["used_percentage"], 55)

    def test_rate_limits_empty_when_never_seen(self):
        self.assertEqual(hud.resolve_rate_limits({}), {})


# --------------------------------------------------------------- config ------

class ConfigTest(unittest.TestCase):
    def setUp(self):
        for v in ("TASK_STATION_HUD", "TASK_STATION_HUD_ROWS", "TASK_STATION_HUD_ECO"):
            os.environ.pop(v, None)

    def tearDown(self):
        for v in ("TASK_STATION_HUD", "TASK_STATION_HUD_ROWS", "TASK_STATION_HUD_ECO"):
            os.environ.pop(v, None)

    def test_hud_default_off_env_on(self):
        os.environ["TASK_STATION_HUD"] = "on"
        self.assertTrue(config.hud_enabled())
        os.environ["TASK_STATION_HUD"] = "off"
        self.assertFalse(config.hud_enabled())

    def test_hud_rows_default_all(self):
        self.assertEqual(config.hud_rows(), list(config.HUD_ROW_KEYS))
        self.assertEqual(list(config.HUD_ROW_KEYS), list(hud.ROW_KEYS))   # kept in sync

    def test_hud_rows_env_subset_validated(self):
        # `turn` is no longer a valid row → dropped alongside the bogus token.
        os.environ["TASK_STATION_HUD_ROWS"] = "week, turn , bogus,session"
        self.assertEqual(config.hud_rows(), ["week", "session"])

    def test_hud_rows_legacy_aliases(self):
        # Old WS7 config keeps working: `limits`/`5-hour` → fivehour, dedup preserved;
        # the retired `turn` row is silently dropped.
        os.environ["TASK_STATION_HUD_ROWS"] = "turn,session,limits,week,total,task"
        self.assertEqual(config.hud_rows(),
                         ["session", "fivehour", "week", "total", "task"])
        os.environ["TASK_STATION_HUD_ROWS"] = "5-hour,fivehour"
        self.assertEqual(config.hud_rows(), ["fivehour"])

    def test_hud_rows_parse_empty_falls_back(self):
        self.assertEqual(config.hud_rows_parse("nope,,x"), list(config.HUD_ROW_KEYS))

    def test_hud_eco_default_on(self):
        # No persisted value + no env ⇒ ON by default now.
        self.assertTrue(config.hud_eco_enabled())
        os.environ["TASK_STATION_HUD_ECO"] = "off"
        self.assertFalse(config.hud_eco_enabled())


# --------------------------------------------------------------- installer ---

class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hud-install-")
        self._saved_cfg = os.environ.get("CLAUDE_CONFIG_DIR")
        self._saved_home = os.environ.get("TASK_STATION_HOME")
        os.environ["CLAUDE_CONFIG_DIR"] = self.tmp
        os.environ["TASK_STATION_HOME"] = self.tmp
        self.settings = os.path.join(self.tmp, "settings.json")

    def tearDown(self):
        for name, val in (("CLAUDE_CONFIG_DIR", self._saved_cfg),
                          ("TASK_STATION_HOME", self._saved_home)):
            if val is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = val
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _read(self):
        with open(self.settings) as f:
            return json.load(f)

    def test_install_becomes_host_and_provider(self):
        station_setup.install_hud()
        data = self._read()
        self.assertIn(station_setup.STATUSLINE_HOST_MARKER, data["statusLine"]["command"])
        prov = station_setup.hud_provider_path()
        self.assertTrue(os.path.exists(prov))
        self.assertTrue(os.access(prov, os.X_OK))
        with open(prov) as f:
            self.assertIn(station_setup.HUD_PROVIDER_MANAGED_MARKER, f.read())
        self.assertEqual(station_setup.hud_status(), "installed (host)")

    def test_remove_clears_host_when_alone(self):
        station_setup.install_hud()
        station_setup.remove_hud()
        self.assertFalse(os.path.exists(station_setup.hud_provider_path()))
        self.assertNotIn("statusLine", self._read())
        self.assertEqual(station_setup.hud_status(), "off")

    def test_host_kept_while_task_segment_present(self):
        station_setup.install_statusline()
        station_setup.install_hud()
        msg = station_setup.remove_hud()
        self.assertFalse(os.path.exists(station_setup.hud_provider_path()))
        self.assertIn(station_setup.STATUSLINE_HOST_MARKER, self._read()["statusLine"]["command"])
        self.assertIn("shared status-bar host", msg)

    def test_foreign_statusline_untouched(self):
        with open(self.settings, "w") as f:
            json.dump({"statusLine": {"type": "command", "command": "costbar --fancy"}}, f)
        station_setup.install_hud()
        self.assertEqual(self._read()["statusLine"]["command"], "costbar --fancy")
        self.assertEqual(station_setup.hud_status(), "provider-only")


# -------------------------------------------------- data completeness (WS7b) --

class DataCompletenessTest(unittest.TestCase):
    """scan-all (ledger EVERY transcript, task_id NULL for unattached) + the one-time
    costbar import (idempotent, filtered by ledger-presence + transcript-on-disk)."""
    OPUS = "claude-opus-4-8"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hud-data-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        self._saved_cfg = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = self.tmp
        self.projects = os.path.join(self.tmp, "projects")
        self.bucket = os.path.join(self.projects, "-proj")
        os.makedirs(self.bucket, exist_ok=True)
        usage.PROJECTS_ROOT = self.projects
        usage.WORKERS_REGISTRY = None
        store.reset_cache()
        self.be = store.get_backend(os.path.join(self.tmp, "store"))

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        if self._saved_cfg is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self._saved_cfg
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_session(self, sid, out=200):
        line = {"type": "assistant", "timestamp": _iso(1000), "cwd": "/proj",
                "entrypoint": "cli",
                "message": {"model": self.OPUS,
                            "usage": {"input_tokens": 1000, "output_tokens": out}}}
        with open(os.path.join(self.bucket, sid + ".jsonl"), "w") as f:
            f.write(json.dumps(line) + "\n")

    def test_scan_all_records_unattached_with_null_task(self):
        self._write_session("free", out=200)
        n = usage.scan_all(self.be)
        self.assertEqual(n, 1)
        row = self.be.get_session_usage("free")
        self.assertIsNone(row["task_id"])                 # belongs to no task ⇒ NULL
        self.assertEqual(row["models"][self.OPUS]["out"], 200)
        # And it now feeds the whole-ledger Total.
        totals = usage.ledger_totals(self.be)
        self.assertEqual(totals["grand_out"], 200)

    def test_scan_all_incremental_noop(self):
        self._write_session("s", out=200)
        usage.scan_all(self.be)
        usage.scan_all(self.be)                            # unchanged ⇒ no double count
        self.assertEqual(self.be.get_session_usage("s")["models"][self.OPUS]["out"], 200)

    def test_import_costbar_inserts_filters_and_is_idempotent(self):
        # a: no transcript, not in ledger → imported.
        # b: has a live transcript → skipped (scan wins).
        # c: already in the ledger → skipped.
        self._write_session("b", out=50)
        self.be.upsert_session_usage({"session_id": "c", "task_id": None,
                                      "models": {self.OPUS: _model(10, 0.01)},
                                      "first_ts": 1_900_000_000,
                                      "last_ts": 1_900_000_000})
        cache = {
            "a": {"cost": 4.20, "output_tok": 1000, "turns": 3, "date": "2026-05-05"},
            "b": {"cost": 9.99, "output_tok": 500, "turns": 2, "date": "2026-06-01"},
            "c": {"cost": 1.11, "output_tok": 100, "turns": 1, "date": "2026-06-02"},
        }
        path = os.path.join(self.tmp, "session_totals.json")
        with open(path, "w") as f:
            json.dump(cache, f)

        res = usage.import_costbar(self.be, path=path)
        self.assertEqual(res["imported"], 1)
        self.assertEqual(res["skipped_has_transcript"], 1)
        self.assertEqual(res["skipped_in_ledger"], 1)

        row = self.be.get_session_usage("a")
        self.assertEqual(row["source"], "costbar-import")
        self.assertIsNone(row["task_id"])
        self.assertEqual(row["models"]["costbar-import"]["out"], 1000)
        self.assertAlmostEqual(row["models"]["costbar-import"]["cost_usd"], 4.20)
        # since-date reflects the imported date (earliest activity).
        totals = usage.ledger_totals(self.be)
        self.assertAlmostEqual(totals["grand_cost"], round(4.20 + 0.01, 6))
        self.assertEqual(totals["first_ts"], usage._parse_costbar_date("2026-05-05"))

        # Re-run ⇒ nothing new (a+c already in ledger, b still has a live transcript).
        res2 = usage.import_costbar(self.be, path=path)
        self.assertEqual(res2["imported"], 0)
        self.assertEqual(res2["skipped_in_ledger"], 2)
        self.assertEqual(res2["skipped_has_transcript"], 1)

    def test_import_costbar_missing_cache_reports_error(self):
        res = usage.import_costbar(self.be, path=os.path.join(self.tmp, "nope.json"))
        self.assertIn("error", res)
        self.assertEqual(res["imported"], 0)


if __name__ == "__main__":
    unittest.main()
