"""Task 444 — the private weekly usage recap (lib/recap.py + the `recap` subcommand
+ the `recap` / `recap_curator_cmd` config toggles).

Covers week-bound resolution, aggregate collection over a seeded ledger, the
deterministic heuristics, the optional curator (incl. its privacy allowlist), the
self-contained HTML contract (single <style>, no external assets, both themes,
owner-amber accent, tabular numerals), graceful empty-week rendering, the config
toggle, the CLI, the throttled auto-generate, and — load-bearing — the PRIVACY
assertions: no raw prompt text and no task summary ever reach the output.

In-process (_Args + redirect_stdout) under temp-home isolation, matching
tests/test_usage_cli.py. The ledger is seeded DIRECTLY through the backend so the
week window is deterministic without transcript IO."""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

import store        # noqa: E402
import config       # noqa: E402
import recap        # noqa: E402
import recap_guidance as guidance  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)

OPUS = "claude-opus-4-8"
FABLE = "claude-fable-5"
SONNET = "claude-sonnet-5"
HAIKU = "claude-haiku-4-5-20251001"

WEEK = "2026-W28"      # a complete past ISO week; bounds are stable regardless of now


def _mb(out, cost, msgs=1, inp=0, cache=0):
    """One stored per-model bucket (the shape usage._dump_bucket writes)."""
    return {"in": inp, "out": out, "cache_read": cache, "cache_w5m": 0,
            "cache_w1h": 0, "web": 0, "msgs": msgs, "cost_usd": cost}


def _models(*pairs):
    """Build a stored models blob from (model_id, bucket) pairs."""
    return {m: b for m, b in pairs}


class _Args:
    def __init__(self, **kw):
        d = dict(week=None, open=False, as_json=False, no_scan=True,
                 auto_if_due=False, quiet=False)
        d.update(kw)
        self.__dict__.update(d)


class _Base(unittest.TestCase):
    def setUp(self):
        for v in ("TASK_STATION_RECAP", "TASK_STATION_RECAP_CURATOR_CMD",
                  "TASK_STATION_USAGE_TRACKING"):
            os.environ.pop(v, None)
        self.tmp = tempfile.mkdtemp(prefix="recap-test-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.PROJECTS_ROOT = os.path.join(self.tmp, "projects")
        ts.DELEGATE_REGISTRY = os.path.join(self.tmp, "workers.json")
        os.makedirs(ts.PROJECTS_ROOT, exist_ok=True)
        store.reset_cache()
        self.store = ts._backend()
        wk = recap.resolve_week(WEEK)
        self.start = wk["start_ts"]
        self.end = wk["end_ts"]
        self.mid = self.start + 3 * 3600        # comfortably inside the week

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- seeding helpers --
    def _task(self, title, summary="", color=None):
        t = ts.new_task(title, summary, color=color)
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _urow(self, sid, task_id, models, act_ts=None, phases=None, role="hub",
              first_ts=None, dur=1800):
        act = self.mid if act_ts is None else act_ts
        self.store.upsert_session_usage({
            "session_id": sid, "task_id": task_id, "role": role,
            "first_ts": (act - dur) if first_ts is None else first_ts,
            "last_ts": act, "models": models, "sidechain": {}, "phases": phases or {},
        })

    def _prompt(self, uuid, sid, ts_, kind, text, task_id=None):
        self.store.upsert_prompt({"uuid": uuid, "session_id": sid, "task_id": task_id,
                                  "ts": ts_, "kind": kind, "text": text})

    def _collect(self):
        wk = recap.resolve_week(WEEK)
        return recap.collect(self.store, wk["start_ts"], wk["end_ts"], wk["label"])


# --------------------------------------------------------------- week bounds ---

class WeekBoundsTest(unittest.TestCase):
    def test_parse_and_bounds(self):
        wk = recap.resolve_week("2026-W28")
        self.assertEqual(wk["label"], "2026-W28")
        self.assertEqual(wk["end_ts"] - wk["start_ts"], 7 * 86400)

    def test_normalizes_spacing_and_case(self):
        self.assertEqual(recap.resolve_week(" 2026-w28 ")["label"], "2026-W28")

    def test_default_is_current_week(self):
        wk = recap.resolve_week(now_ts=recap.resolve_week("2026-W28")["start_ts"] + 100)
        self.assertEqual(wk["label"], "2026-W28")

    def test_bad_label_raises(self):
        with self.assertRaises(ValueError):
            recap.resolve_week("not-a-week")

    def test_previous_complete_week(self):
        cur = recap.resolve_week("2026-W28")
        prev = recap.previous_complete_week_label(now_ts=cur["start_ts"] + 100)
        self.assertEqual(prev, "2026-W27")


# ------------------------------------------------------------------- collect ---

class CollectTest(_Base):
    def test_headline_and_models(self):
        t = self._task("Alpha")
        self._urow("s1", t["id"],
                   _models((OPUS, _mb(800, 1.20, msgs=6)), (FABLE, _mb(200, 0.10, msgs=2))))
        agg = self._collect()
        tot = agg["totals"]
        self.assertEqual(tot["tokens_out"], 1000)
        self.assertEqual(tot["sessions"], 1)
        self.assertAlmostEqual(tot["cost_usd"], 1.30, places=2)
        fams = {m["family"] for m in agg["models"]}
        self.assertEqual(fams, {"opus", "fable"})

    def test_out_of_week_session_excluded(self):
        t = self._task("Alpha")
        self._urow("s1", t["id"], _models((OPUS, _mb(500, 0.5))), act_ts=self.start - 3600)
        self.assertEqual(self._collect()["totals"]["sessions"], 0)

    def test_top_tasks_by_tokens_with_handle(self):
        a = self._task("Refactor engine")
        b = self._task("Docs pass")
        self._urow("s1", a["id"], _models((OPUS, _mb(900, 1.0))))
        self._urow("s2", b["id"], _models((SONNET, _mb(300, 0.2))))
        tasks = self._collect()["tasks"]
        self.assertEqual(tasks[0]["title"], "Refactor engine")
        self.assertTrue(tasks[0]["handle"].startswith("#"))
        self.assertGreater(tasks[0]["out"], tasks[1]["out"])

    def test_tasks_closed_counted_in_week(self):
        t = self._task("Alpha")
        self._urow("s1", t["id"], _models((OPUS, _mb(100, 0.1))))
        t = ts.load_task(t["id"])
        t["closed_ts"] = self.mid
        ts.save_task(t)
        self.assertEqual(self._collect()["totals"]["tasks_closed"], 1)

    def test_delegation_split(self):
        t = self._task("Alpha")
        self._urow("hub", t["id"], _models((OPUS, _mb(100, 1.0))), role="hub")
        self._urow("wk", t["id"], _models((SONNET, _mb(100, 1.0))), role="worker")
        p = self._collect()["patterns"]
        self.assertAlmostEqual(p["delegation_pct"], 0.5, places=2)


# --------------------------------------------------------------- heuristics ----

class HeuristicsTest(unittest.TestCase):
    """deterministic_tips is pure over the aggregate dict — exercise each trigger.
    Every tip's action is now HUMAN-recommendable (directive #2); model-invoked
    features never surface here."""

    def _agg(self, **over):
        base = {"totals": {"tasks_touched": 5, "sessions": 3},
                "patterns": {"delegation_pct": 0.5},
                "features": {"search": True, "memo": True, "brief": True,
                             "reexplain": 0},
                "work_types": {},
                "sessions_meta": []}
        base.update(over)
        return base

    def _cmds(self, tips):
        return " ".join(t.get("command", "") for t in tips)

    def test_model_fit_over_powered_from_matrix(self):
        # ran the strongest tier on mechanical work → matrix flags over-powered.
        wt = {"mechanical": {"out": 5000, "sessions": 3, "observed_class": "strong"}}
        tips = recap.deterministic_tips(self._agg(work_types=wt))
        self.assertIn("/model", self._cmds(tips))
        self.assertTrue(any("mechanical" in t["observation"].lower() for t in tips))

    def test_on_target_work_type_no_flag(self):
        wt = {"mechanical": {"out": 5000, "sessions": 2, "observed_class": "cheap"}}
        tips = recap.deterministic_tips(self._agg(work_types=wt))
        self.assertNotIn("/model", self._cmds(tips))

    def test_cost_outlier_uses_equivalence_language(self):
        metas = [{"cost": 0.2}, {"cost": 0.2}, {"cost": 0.2}, {"cost": 3.0}]
        tips = recap.deterministic_tips(self._agg(sessions_meta=metas))
        self.assertTrue(any("API list prices" in t["observation"] for t in tips))

    def test_long_session_no_save_recommends_save(self):
        metas = [{"msgs": 60, "has_save": False}]
        tips = recap.deterministic_tips(self._agg(sessions_meta=metas))
        self.assertIn("/todo save", self._cmds(tips))

    def test_reexplain_after_compaction(self):
        tips = recap.deterministic_tips(self._agg(
            features={"search": True, "memo": True, "brief": True, "reexplain": 2}))
        self.assertTrue(any("compaction" in t["observation"] for t in tips))

    def test_unused_feature_recommends_human_tool_only(self):
        # memo + brief unused; search unused too — but search is MODEL-invoked and must
        # NEVER be recommended to a human.
        tips = recap.deterministic_tips(self._agg(
            features={"search": False, "memo": False, "brief": False, "reexplain": 0}))
        self.assertTrue(any("didn't use" in t["observation"] for t in tips))
        blob = json.dumps(tips)
        self.assertNotIn("task-station search", blob)
        self.assertFalse(any(t.get("feature") == "search" for t in tips))

    def test_no_delegation(self):
        tips = recap.deterministic_tips(self._agg(
            totals={"tasks_touched": 5, "sessions": 10},
            patterns={"delegation_pct": 0.0}))
        self.assertTrue(any("delegate" in t.get("command", "") for t in tips))

    def test_clean_week_few_tips(self):
        self.assertEqual(recap.deterministic_tips(self._agg()), [])

    def test_no_model_invoked_feature_in_any_tip(self):
        # Exhaustive guard: across a maximally-triggering week, NO tip cites a
        # model-invoked feature as a human action.
        wt = {"mechanical": {"out": 9000, "sessions": 3, "observed_class": "strong"}}
        metas = [{"cost": 0.2}, {"cost": 0.2}, {"cost": 5.0},
                 {"msgs": 80, "has_save": False}]
        tips = recap.deterministic_tips(self._agg(
            work_types=wt, sessions_meta=metas,
            features={"search": False, "memo": False, "brief": False, "reexplain": 3},
            totals={"tasks_touched": 6, "sessions": 12},
            patterns={"delegation_pct": 0.0}))
        for t in tips:
            feat = t.get("feature")
            if feat:
                self.assertTrue(recap.guidance.is_human_recommendable(feat),
                                "model-invoked feature %r leaked into a human tip" % feat)


# ------------------------------------------------------------ render contract --

class RenderContractTest(_Base):
    def _html(self):
        t = self._task("Engine work")
        self._urow("s1", t["id"], _models((OPUS, _mb(900, 1.5, msgs=10)),
                                          (SONNET, _mb(300, 0.2, msgs=4))),
                   phases={"__v": 4, "implementation": {"out": 800, "msgs": 8, "cost_usd": 1.2},
                           "planning": {"out": 100, "msgs": 2, "cost_usd": 0.3}})
        agg = self._collect()
        return recap.render(agg, recap.deterministic_tips(agg))

    def test_single_style_block_no_external_assets(self):
        h = self._html()
        self.assertEqual(h.count("<style>"), 1)
        self.assertEqual(h.count("</style>"), 1)
        self.assertNotIn("<link", h)
        self.assertNotIn("src=", h)
        self.assertNotIn("http://", h)
        self.assertNotIn("https://", h)

    def test_both_themes_present(self):
        h = self._html()
        self.assertIn("prefers-color-scheme: dark", h)
        self.assertIn('data-theme="dark"', h)
        self.assertIn('data-theme="light"', h)

    def test_owner_amber_accent(self):
        h = self._html()
        self.assertIn("#cf8a22", h)      # light accent
        self.assertIn("#b97e1f", h)      # dark accent

    def test_tabular_numerals(self):
        self.assertIn("tabular-nums", self._html())

    def test_footer_privacy_line(self):
        self.assertIn("Private to this machine", self._html())


# ----------------------------------------------------------------- privacy -----

class PrivacyTest(_Base):
    SECRET_PROMPT = "SECRET_PROMPT_TEXT_XYZZY"
    SECRET_SUMMARY = "SUMMARY_SECRET_QWER"

    def _seed(self):
        t = self._task("Visible Title", summary=self.SECRET_SUMMARY)
        self._urow("s1", t["id"], _models((OPUS, _mb(900, 1.0, msgs=5))))
        self._prompt("u1", "s1", self.mid, "prompt", self.SECRET_PROMPT, task_id=t["id"])
        return t

    def test_no_prompt_text_in_html(self):
        self._seed()
        agg = self._collect()
        h = recap.render(agg, recap.deterministic_tips(agg))
        self.assertNotIn(self.SECRET_PROMPT, h)

    def test_no_task_summary_in_html_but_title_is(self):
        self._seed()
        agg = self._collect()
        h = recap.render(agg, recap.deterministic_tips(agg))
        self.assertNotIn(self.SECRET_SUMMARY, h)
        self.assertIn("Visible Title", h)

    def test_aggregates_carry_no_prompt_text(self):
        self._seed()
        blob = json.dumps(self._collect(), ensure_ascii=False)
        self.assertNotIn(self.SECRET_PROMPT, blob)
        self.assertNotIn(self.SECRET_SUMMARY, blob)

    def test_sessions_meta_has_no_ids_or_text(self):
        self._seed()
        for m in self._collect()["sessions_meta"]:
            self.assertNotIn("session_id", m)
            self.assertNotIn("text", m)

    def test_curator_payload_excludes_session_meta(self):
        self._seed()
        payload = recap._curator_payload(self._collect())
        self.assertNotIn("sessions_meta", payload)
        self.assertNotIn(self.SECRET_PROMPT, json.dumps(payload))


# --------------------------------------------------------------- empty week ----

class EmptyWeekTest(_Base):
    def test_renders_gracefully(self):
        agg = self._collect()               # nothing seeded
        self.assertFalse(recap.has_data(agg))
        h = recap.render(agg, [])
        self.assertIn("quiet week", h)
        self.assertIn("Private to this machine", h)   # footer still present
        self.assertEqual(h.count("<style>"), 1)

    def test_generate_writes_file_for_empty_week(self):
        res = recap.generate(self.store, week=WEEK, run_curator=False)
        self.assertTrue(os.path.exists(res["path"]))


# ------------------------------------------------------------------ curator ----

class CuratorTest(_Base):
    def test_parses_tips_from_stdout(self):
        prog = ("import sys,json; json.load(sys.stdin); "
                "print(json.dumps([{'observation':'o','suggestion':'s','command':'c'}]))")
        tips = recap.curator_tips('python3 -c "%s"' % prog, {"totals": {}})
        self.assertEqual(len(tips), 1)
        self.assertEqual(tips[0]["command"], "c")

    def test_caps_at_three(self):
        prog = ("import sys,json; sys.stdin.read(); "
                "print(json.dumps([{'observation':'o%d'%i} for i in range(9)]))")
        self.assertEqual(len(recap.curator_tips('python3 -c "%s"' % prog, {})), 3)

    def test_malformed_output_is_swallowed(self):
        self.assertEqual(recap.curator_tips('echo not-json', {}), [])

    def test_failing_command_is_swallowed(self):
        self.assertEqual(recap.curator_tips('exit 3', {}), [])

    def test_empty_cmd_returns_nothing(self):
        self.assertEqual(recap.curator_tips(None, {}), [])


# ------------------------------------------------------------- config toggle ---

class ConfigToggleTest(_Base):
    def test_recap_default_off(self):
        self.assertFalse(config.recap_enabled())

    def test_recap_settable(self):
        config.set("recap", True)
        self.assertTrue(config.recap_enabled())

    def test_recap_env_override(self):
        config.set("recap", True)
        os.environ["TASK_STATION_RECAP"] = "off"
        try:
            self.assertFalse(config.recap_enabled())
        finally:
            os.environ.pop("TASK_STATION_RECAP", None)

    def test_curator_cmd_default_off(self):
        self.assertIsNone(config.recap_curator_cmd())

    def test_curator_cmd_settable(self):
        config.set("recap_curator_cmd", "my-curator")
        self.assertEqual(config.recap_curator_cmd(), "my-curator")

    def test_cmd_config_recap_on_off(self):
        # cmd_config reads flags via getattr-with-default but accesses workspace_dirs
        # directly, so the stand-in must set it (matches tests/test_config.py).
        args = type("A", (), {})()
        args.workspace_dirs = None
        args.recap = "on"
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(args)
        self.assertTrue(config.recap_enabled())


# ---------------------------------------------------------------------- CLI ----

class CliTest(_Base):
    def _run(self, args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_recap(args)
        return buf.getvalue()

    def _seed(self):
        t = self._task("Alpha")
        self._urow("s1", t["id"], _models((OPUS, _mb(900, 1.0, msgs=5))))
        return t

    def test_writes_file_and_prints_path(self):
        self._seed()
        out = self._run(_Args(week=WEEK))
        path = out.strip()
        self.assertTrue(path.endswith("%s.html" % WEEK))
        self.assertTrue(os.path.exists(path))
        self.assertIn(os.path.join("recaps"), path)

    def test_json_mode(self):
        self._seed()
        out = self._run(_Args(week=WEEK, as_json=True))
        data = json.loads(out)
        self.assertEqual(data["week"], WEEK)
        self.assertIn("totals", data)

    def test_off_when_tracking_disabled(self):
        self._seed()
        os.environ["TASK_STATION_USAGE_TRACKING"] = "off"
        try:
            out = self._run(_Args(week=WEEK))
            self.assertIn("usage tracking is off", out)
        finally:
            os.environ.pop("TASK_STATION_USAGE_TRACKING", None)

    def test_bad_week(self):
        self.assertIn("recap:", self._run(_Args(week="bogus")))


# --------------------------------------------------------------- auto-due ------

class AutoDueTest(_Base):
    def _now_in(self, week):
        return recap.resolve_week(week)["start_ts"] + 100

    def test_off_by_default(self):
        self.assertIsNone(recap.auto_generate_if_due(self.store, now_ts=self._now_in("2026-W29")))

    def test_generates_previous_week_when_on(self):
        config.set("recap", True)
        path = recap.auto_generate_if_due(self.store, now_ts=self._now_in("2026-W29"))
        self.assertIsNotNone(path)
        self.assertTrue(path.endswith("2026-W28.html"))     # previous complete week
        self.assertTrue(os.path.exists(path))

    def test_idempotent_within_week(self):
        config.set("recap", True)
        now = self._now_in("2026-W29")
        self.assertIsNotNone(recap.auto_generate_if_due(self.store, now_ts=now))
        self.assertIsNone(recap.auto_generate_if_due(self.store, now_ts=now))   # stamped

    def test_cli_auto_if_due_silent_when_off(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_recap(_Args(auto_if_due=True, quiet=True))
        self.assertEqual(buf.getvalue(), "")


# -------------------------------------------------------- invoked_by registry --

class GuidanceRegistryTest(unittest.TestCase):
    """The feature-invocation registry + its filter (directive #2)."""

    def test_known_invokers(self):
        self.assertEqual(guidance.feature_invoker("search"), "model")
        self.assertEqual(guidance.feature_invoker("memo"), "both")
        self.assertEqual(guidance.feature_invoker("save"), "human")
        self.assertEqual(guidance.feature_invoker("brief"), "both")

    def test_human_recommendable_filter(self):
        self.assertFalse(guidance.is_human_recommendable("search"))   # model-invoked
        for f in ("memo", "save", "brief", "delegate", "auto_checkpoint"):
            self.assertTrue(guidance.is_human_recommendable(f), f)

    def test_model_features_have_no_human_action(self):
        self.assertIsNone(guidance.human_action("search"))
        self.assertEqual(guidance.human_action("save"), "/todo save")

    def test_registry_invariants(self):
        valid = {"human", "both", "model", "human-audit"}
        for feat, e in guidance.INVOKED_BY.items():
            self.assertIn(e["invoked_by"], valid, feat)
            if e["invoked_by"] == "model":
                self.assertIsNone(e.get("human_action"), feat)
            else:
                self.assertTrue(e.get("human_action"), feat)


# --------------------------------------------------------------- model matrix --

class MatrixTest(unittest.TestCase):
    def test_version_and_generation_note(self):
        self.assertTrue(guidance.MATRIX_VERSION)
        self.assertIn("class", guidance.MODEL_GENERATION_NOTE.lower())

    def test_matrix_row_shape(self):
        classes = {"cheap", "mid", "strong"}
        efforts = {"low", "medium", "high"}
        self.assertTrue(guidance.MODEL_ROLE_MATRIX)
        for row in guidance.MODEL_ROLE_MATRIX:
            for k in ("work_type", "title", "examples", "classes", "effort", "why"):
                self.assertIn(k, row)
            self.assertTrue(row["classes"])
            self.assertTrue(set(row["classes"]) <= classes, row["work_type"])
            self.assertIn(row["effort"], efforts)

    def test_fit_delta(self):
        mech = guidance.matrix_row("mechanical")
        hard = guidance.matrix_row("hard_logic")
        self.assertEqual(guidance.fit_delta("strong", mech)[0], "over")
        self.assertEqual(guidance.fit_delta("cheap", mech)[0], "on")
        self.assertEqual(guidance.fit_delta("cheap", hard)[0], "under")
        self.assertEqual(guidance.fit_delta("strong", hard)[0], "on")

    def test_class_of_family(self):
        self.assertEqual(guidance.class_of_family("opus"), "strong")
        self.assertEqual(guidance.class_of_family("fable"), "strong")
        self.assertEqual(guidance.class_of_family("sonnet"), "mid")
        self.assertEqual(guidance.class_of_family("haiku"), "cheap")


class MatrixRenderTest(_Base):
    def test_observed_row_marked_with_delta(self):
        t = self._task("Bulk rename")
        self._urow("s1", t["id"], _models((OPUS, _mb(4000, 3.0, msgs=12))),
                   phases={"__v": 4, "implementation": {"out": 4000, "msgs": 12, "cost_usd": 3.0}})
        agg = self._collect()
        rows = recap.build_matrix_rows(agg)
        mech = [r for r in rows if r["row"]["work_type"] == "mechanical"][0]
        self.assertIsNotNone(mech["observed"])
        self.assertEqual(mech["observed"]["verdict"], "over")
        html = recap.render(agg, recap.deterministic_tips(agg))
        self.assertIn("over-powered", html)
        self.assertIn("Match the model to the work", html)


# ---------------------------------------------------- equivalence + eco --------

class EquivalenceEcoTest(_Base):
    def test_cost_equivalence_matches_derived(self):
        t = self._task("Alpha")
        self._urow("s1", t["id"], _models((OPUS, _mb(900, 1.25, msgs=5))))
        agg = self._collect()
        self.assertAlmostEqual(agg["cost_equivalence"]["api_list_usd"],
                               agg["totals"]["cost_usd"], places=2)

    def test_eco_ranges_present_and_ordered(self):
        t = self._task("Alpha")
        self._urow("s1", t["id"], _models((OPUS, _mb(500000, 2.0, msgs=5, inp=200000, cache=100000))))
        eco = self._collect()["eco"]
        for k in ("kwh", "co2_kg", "water_l"):
            lo, hi = eco[k]
            self.assertLessEqual(lo, hi)
            self.assertGreater(hi, 0)
        self.assertTrue(eco["processed_tokens"] >= 800000)

    def test_eco_estimate_scales_with_class(self):
        cheap = guidance.eco_estimate({"cheap": 1_000_000})
        strong = guidance.eco_estimate({"strong": 1_000_000})
        self.assertGreater(strong["kwh"][1], cheap["kwh"][1])

    def test_render_shows_equivalence_and_directional_eco(self):
        t = self._task("Alpha")
        self._urow("s1", t["id"], _models((OPUS, _mb(900, 1.5, msgs=5))))
        agg = self._collect()
        h = recap.render(agg, recap.deterministic_tips(agg))
        self.assertIn("API list prices", h)
        self.assertIn("DIRECTIONAL", h)
        self.assertIn("kWh", h)
        self.assertIn("CO₂e", h)
        self.assertIn("–", h)                       # a range, not a point value
        self.assertIn(guidance.ECO_VERSION, h)

    def test_assumptions_render_with_citations(self):
        t = self._task("Alpha")
        self._urow("s1", t["id"], _models((OPUS, _mb(900, 1.5, msgs=5))))
        h = recap.render(self._collect(), [])
        for c in guidance.ECO_CITATIONS:
            self.assertIn(c["factor"], h)


# ------------------------------------------------------------- guidance order --

class StrategyOrderingTest(_Base):
    def _html(self):
        t = self._task("Bulk edits")
        self._urow("s1", t["id"], _models((OPUS, _mb(5000, 3.0, msgs=12))),
                   phases={"__v": 4, "implementation": {"out": 5000, "msgs": 12, "cost_usd": 3.0}})
        agg = self._collect()
        # Guarantee ≥1 flag so the flags section renders (it self-skips on a clean
        # week) — the ordering assertions need all three sections present.
        tips = recap.deterministic_tips(agg) or [{
            "observation": "opus used for mechanical bulk edits",
            "suggestion": "delegate bulk edits to a cheaper tier",
            "command": "task-station brains suggest --task 1"}]
        return recap.render(agg, tips)

    def test_strategy_leads_then_matrix_then_flags(self):
        h = self._html()
        i_strategy = h.index("Getting more from any LLM")
        i_matrix = h.index("Match the model to the work")
        i_flags = h.index("This week&#x27;s flags")   # _h2 HTML-escapes the apostrophe
        self.assertLess(i_strategy, i_matrix)
        self.assertLess(i_matrix, i_flags)

    def test_strategy_precedes_any_flag_command(self):
        h = self._html()
        i_strategy = h.index("Getting more from any LLM")
        self.assertIn("/model", h)                       # the over-powered flag fired
        self.assertLess(i_strategy, h.index("/model"))

    def test_model_feature_practice_has_no_command(self):
        strat = recap.build_strategy()
        search_p = [p for p in strat if p.get("feature") == "search"][0]
        self.assertIsNone(search_p["command"])
        self.assertTrue(search_p["via_assistant"])
        save_p = [p for p in strat if p.get("feature") == "save"][0]
        self.assertEqual(save_p["command"], "/todo save")
        self.assertFalse(save_p["via_assistant"])

    def test_no_model_command_in_strategy_html(self):
        # the search practice must not print a "run this" command for a model feature.
        h = self._html()
        i_strategy = h.index("Getting more from any LLM")
        i_matrix = h.index("Match the model to the work")
        strat_block = h[i_strategy:i_matrix]
        self.assertNotIn("task-station search", strat_block)


class CollectV2Test(_Base):
    def test_work_types_observed(self):
        t = self._task("Alpha")
        self._urow("s1", t["id"], _models((OPUS, _mb(900, 1.0, msgs=5))),
                   phases={"__v": 4, "implementation": {"out": 900, "msgs": 5, "cost_usd": 1.0}})
        wt = self._collect()["work_types"]
        self.assertIn("mechanical", wt)
        self.assertEqual(wt["mechanical"]["observed_class"], "strong")

    def test_worker_implementation_is_delegated(self):
        t = self._task("Alpha")
        self._urow("wk", t["id"], _models((SONNET, _mb(900, 1.0, msgs=5))), role="worker",
                   phases={"__v": 4, "implementation": {"out": 900, "msgs": 5, "cost_usd": 1.0}})
        self.assertIn("delegated_impl", self._collect()["work_types"])

    def test_matrix_version_and_eco_in_aggregates(self):
        t = self._task("Alpha")
        self._urow("s1", t["id"], _models((OPUS, _mb(900, 1.0, msgs=5))))
        agg = self._collect()
        self.assertEqual(agg["matrix_version"], guidance.MATRIX_VERSION)
        self.assertIn("token_by_class", agg["eco"])


if __name__ == "__main__":
    unittest.main()
