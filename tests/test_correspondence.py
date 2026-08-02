"""F4/F5/F6 (task #444, run 3) — brains auto-attach + correspondence + artifact capture.

  F4  brains schema v2 migrate · auto-attach scoring (each weight class + threshold +
      'main' fallback + pinned respect) · derived block · suggest.
  F5  link/fork/subscribe round-trips on canonical peer feeds · fork copies digest +
      provenance · subscription check mints once per rev (idempotent) · trail_visibility
      enforcement at export (private strips / checkpoints partial / full includes).
  F6  artifact URL patterns (ADO + GitHub PR/workitem) · capture dedup · cross-person
      auto-link + memo dedup · suppressed in workers.
  LAW single-brain + interbrain off ⇒ the board carries no new correspondence markup
      (parity), and a second brain with interbrain OFF stays parity.

Correspondence reads CANONICAL peer feeds (`window.__TSFEED_<alias> = {json};`) — the form
real sync produces, and (as of #444) the form the shipped demo fixtures use too.
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
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "lib")
TOOLS = os.path.join(ROOT, "tools")
for p in (LIB, TOOLS):
    if p not in sys.path:
        sys.path.insert(0, p)

import store            # noqa: E402
import brains           # noqa: E402
import feeds            # noqa: E402
import artifacts        # noqa: E402
import render_board     # noqa: E402

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


def _feed_js(alias, feed):
    return ("window.__TSFEED_%s = %s;\n"
            "(window.__TSFEEDS = window.__TSFEEDS || []).push(window.__TSFEED_%s);\n"
            % (alias, json.dumps(feed), alias))


def _peer_task(uuid8="7f3a2b10", handle="jpark-201", title="Balance-sheet rollup",
               status="active", prs=None, stories=None, goal="serve rollups",
               state="wire cache", decisions=None, prompts=None):
    t = {
        "uuid8": uuid8, "handle": handle, "title": title, "status": status, "live": False,
        "category": {"key": "green", "tag": "FEATURE", "dot": "🟢",
                     "hex": "#3f9e2f", "hex_dark": "#6fe05a"},
        "effort": "m", "brain": "main", "shares": [], "tokens": 100,
        "tokens_estimated": False, "cost_usd": 1.0, "models": ["sonnet"],
        "updated_ts": 1752810000, "relations": [],
        "signals": {"prs": list(prs or []), "stories": list(stories or [])},
        "digest": {"goal": goal, "state": state, "steps_done": 4, "steps_total": 6,
                   "decisions_tail": list(decisions or ["cache per period"])},
        "participants": ["jpark"], "owner": "jpark", "shared_org": False,
    }
    if prompts is not None:
        t["prompts"] = list(prompts)
    return t


def _peer_feed(tasks, alias="jpark", rev="r1"):
    return {"schema": 3, "kind": "peer", "alias": alias, "owner": alias, "label": alias,
            "editable": False, "color": "#4f8fe6", "color_dark": "#4f8fe6",
            "rev": rev, "tasks": tasks, "memos": []}


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="corr-tests-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.DELEGATE_REGISTRY = os.path.join(self.tmp, "workers.json")
        self._ib_saved = os.environ.get("TASK_STATION_INTERBRAIN")
        store.reset_cache()

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        os.environ.pop("TASK_STATION_SUPPRESS", None)
        if self._ib_saved is None:
            os.environ.pop("TASK_STATION_INTERBRAIN", None)
        else:
            os.environ["TASK_STATION_INTERBRAIN"] = self._ib_saved
        store.reset_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self, title, color="green", effort="m", status=None, **fields):
        t = ts.new_task(title, "summary for " + title, color=color, effort=effort)
        ts.save_task(t)
        ts.ensure_seqs()
        t = ts.load_task(t["id"])
        if status:
            t["status"] = status
        for k, v in fields.items():
            t[k] = v
        ts.save_task(t)
        return ts.load_task(t["id"])

    def _write_peer(self, feed, alias="jpark"):
        d = os.path.join(self.tmp, "feeds", "peers")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, alias + ".js"), "w", encoding="utf-8") as f:
            f.write(_feed_js(alias, feed))

    def _find(self, title):
        for t in ts.sorted_tasks():
            if t.get("title") == title:
                return t
        return None


# ------------------------------------------------------------------ F4 --------

class SchemaMigrateTest(_Base):
    def test_v1_migrates_in_place(self):
        v1 = {"version": 1,
              "brains": {"main": {"archived": False, "shares": []},
                         "work": {"archived": False,
                                  "shares": [{"with": "jpark", "tag": None}]}},
              "assign": {"u1": "work"}}
        with open(os.path.join(self.tmp, "brains.json"), "w", encoding="utf-8") as f:
            json.dump(v1, f)
        cfg = brains.load(self.tmp)
        self.assertEqual(cfg["version"], 2)
        w = cfg["brains"]["work"]
        self.assertEqual(w["keywords"], [])
        self.assertEqual(w["repos"], [])
        self.assertEqual(w["category_affinity"], [])
        self.assertEqual(w["name"], "work")
        self.assertIn("created_ts", w)
        self.assertEqual(w["shares"], [{"with": "jpark", "tag": None}])  # preserved
        self.assertEqual(brains.brain_for(cfg, "u1"), "work")            # assign preserved


class ScoringTest(_Base):
    def _cfg(self):
        cfg = brains._default()
        brains.add(cfg, "projectname", repos="Projectname", keywords="balance,ledger,rollup",
                   category_affinity="FEATURE")
        return cfg

    def test_repo_match_weight_4(self):
        res = brains.score_brains(self._cfg(), {"repos": ["/Users/x/Projectname"]})
        row = [r for r in res["scores"] if r["name"] == "projectname"][0]
        self.assertEqual(row["breakdown"]["repo"], 4)
        self.assertEqual(res["winner"], "projectname")

    def test_cwd_match_weight_4(self):
        res = brains.score_brains(self._cfg(), {"cwd": "/Users/x/Projectname-2704-x"})
        row = [r for r in res["scores"] if r["name"] == "projectname"][0]
        self.assertEqual(row["breakdown"]["repo"], 4)

    def test_keyword_hits_capped_at_6(self):
        res = brains.score_brains(self._cfg(),
                                  {"text": "balance sheet ledger rollup reconcile"})
        row = [r for r in res["scores"] if r["name"] == "projectname"][0]
        self.assertEqual(row["breakdown"]["keyword"], 6)   # 3 hits*2=6 (cap)

    def test_category_affinity_weight_2(self):
        res = brains.score_brains(self._cfg(), {"category": "FEATURE"})
        row = [r for r in res["scores"] if r["name"] == "projectname"][0]
        self.assertEqual(row["breakdown"]["category"], 2)

    def test_skill_hint_weight_1(self):
        res = brains.score_brains(self._cfg(), {"skill": "ledger"})
        row = [r for r in res["scores"] if r["name"] == "projectname"][0]
        self.assertEqual(row["breakdown"]["skill"], 1)

    def test_below_threshold_falls_back_to_main(self):
        # only category (2) < threshold 3 → main
        res = brains.score_brains(self._cfg(), {"category": "FEATURE"})
        self.assertEqual(res["winner"], "main")

    def test_archived_brain_never_wins(self):
        cfg = self._cfg()
        brains.archive(cfg, "projectname")
        res = brains.score_brains(cfg, {"repos": ["Projectname"]})
        self.assertEqual(res["winner"], "main")


class AutoAttachTest(_Base):
    def _cfg_with_projectname(self):
        cfg = brains.load(self.tmp)
        brains.add(cfg, "projectname", repos="Projectname", keywords="balance,ledger")
        brains.save(cfg, self.tmp)

    def test_auto_assign_only_while_on_main(self):
        cfg = brains._default()
        brains.add(cfg, "a")
        brains.add(cfg, "b")
        self.assertTrue(brains.auto_assign(cfg, "u1", "a"))
        self.assertEqual(brains.brain_for(cfg, "u1"), "a")
        # already off main → never yanked into another brain
        self.assertFalse(brains.auto_assign(cfg, "u1", "b"))
        self.assertEqual(brains.brain_for(cfg, "u1"), "a")

    def test_pinned_respected(self):
        cfg = brains._default()
        brains.add(cfg, "a")
        brains.assign(cfg, "u1", "main", pinned=True)   # pin to main
        self.assertTrue(brains.is_pinned(cfg, "u1"))
        self.assertFalse(brains.auto_assign(cfg, "u1", "a"))
        self.assertEqual(brains.brain_for(cfg, "u1"), "main")

    def test_auto_attach_brain_files_task(self):
        self._cfg_with_projectname()
        t = self._seed("Balance ledger rollup work")
        brain = ts.auto_attach_brain(t)
        self.assertEqual(brain, "projectname")
        cfg = brains.load(self.tmp)
        self.assertEqual(brains.brain_for(cfg, t["id"]), "projectname")

    def test_single_brain_stays_main(self):
        t = self._seed("Anything at all")
        self.assertEqual(ts.auto_attach_brain(t), "main")

    def test_create_auto_attaches(self):
        self._cfg_with_projectname()
        a = SimpleNamespace(session=None, title="Ledger balance work", summary="",
                            color="green", effort="m", goal=None, step=None,
                            force=True, no_attach=True, attach=False, active=False)
        with redirect_stdout(io.StringIO()):
            ts.cmd_create(a)
        t = self._find("Ledger balance work")
        self.assertIsNotNone(t)
        cfg = brains.load(self.tmp)
        self.assertEqual(brains.brain_for(cfg, t["id"]), "projectname")


class DeriveTest(_Base):
    def test_derive_block(self):
        cfg = brains._default()
        brains.add(cfg, "work")
        cfg["assign"] = {"u1": "work", "u2": "work"}
        views = [
            {"uuid": "u1", "status": "active", "title": "T1", "category": "FEATURE",
             "signals": {"prs": ["p1"], "stories": []}, "updated_ts": 100},
            {"uuid": "u2", "status": "open", "title": "T2", "category": "INFRA",
             "signals": {"prs": ["p1"], "stories": []}, "updated_ts": 200},
            {"uuid": "u3", "status": "active", "title": "OTHER", "category": "DOCS",
             "signals": {}, "updated_ts": 999},   # main — excluded
        ]
        d = brains.derive(cfg, "work", views)
        self.assertEqual(d["open_count"], 2)
        self.assertEqual(d["active_count"], 1)
        self.assertEqual(d["recent_focus"], ["T1"])
        self.assertEqual(d["last_activity_ts"], 200)
        self.assertIn("FEATURE", d["dominant_categories"])
        self.assertEqual(d["top_signals"], ["p1"])


# ------------------------------------------------------------------ F5 --------

class LinkTest(_Base):
    def test_link_round_trip_and_dedup(self):
        self._write_peer(_peer_feed([_peer_task()]))
        t = self._seed("My review task")
        a = SimpleNamespace(task=str(t["seq"]), peer="jpark-201", session=None)
        with redirect_stdout(io.StringIO()):
            ts.cmd_link(a)
        t = ts.load_task(t["id"])
        self.assertEqual(len(t["links"]), 1)
        self.assertEqual(t["links"][0]["alias"], "jpark")
        self.assertEqual(t["links"][0]["uuid8"], "7f3a2b10")
        # dedup — a second identical link is a no-op
        with redirect_stdout(io.StringIO()):
            ts.cmd_link(a)
        self.assertEqual(len(ts.load_task(t["id"])["links"]), 1)


class ForkTest(_Base):
    def test_fork_copies_digest_and_provenance(self):
        self._write_peer(_peer_feed(
            [_peer_task(goal="serve rollups", state="wire cache",
                        decisions=["cache per period", "expose deltas"])], rev="rX"))
        a = SimpleNamespace(from_ref="jpark-201", title=None, session=None)
        with redirect_stdout(io.StringIO()):
            ts.cmd_fork(a)
        t = self._find("Balance-sheet rollup")
        self.assertIsNotNone(t)
        self.assertEqual(t["goal"], "serve rollups")
        self.assertEqual(t["state"], "wire cache")
        self.assertEqual(t["decisions"], ["cache per period", "expose deltas"])
        self.assertEqual(t["forked_from"]["alias"], "jpark")
        self.assertEqual(t["forked_from"]["uuid8"], "7f3a2b10")
        self.assertEqual(t["forked_from"]["at_rev"], "rX")
        # auto-linked to the source
        self.assertTrue(any(l["uuid8"] == "7f3a2b10" for l in t["links"]))


class SubscribeTest(_Base):
    def test_check_mints_once_per_rev(self):
        self._write_peer(_peer_feed([_peer_task(state="v1")], rev="r1"))
        t = self._seed("Track jpark")
        with redirect_stdout(io.StringIO()):
            ts.cmd_subscribe(SimpleNamespace(task=str(t["seq"]), peer="jpark-201",
                                             on="checkpoint,decision", session=None))
        # baseline rev == current → no mint
        with redirect_stdout(io.StringIO()):
            ts.cmd_subscriptions(SimpleNamespace(sub="check", throttle=False, session=None))
        self.assertEqual(len(ts.load_task(t["id"]).get("memos") or []), 0)
        # feed advances → exactly one memo
        self._write_peer(_peer_feed([_peer_task(state="v2")], rev="r2"))
        with redirect_stdout(io.StringIO()):
            ts.cmd_subscriptions(SimpleNamespace(sub="check", throttle=False, session=None))
        memos = ts.load_task(t["id"]).get("memos") or []
        self.assertEqual(len(memos), 1)
        self.assertIn("jpark-201", memos[0]["text"])
        # re-check at the same rev → idempotent, still one
        with redirect_stdout(io.StringIO()):
            ts.cmd_subscriptions(SimpleNamespace(sub="check", throttle=False, session=None))
        self.assertEqual(len(ts.load_task(t["id"]).get("memos") or []), 1)


class TrailVisibilityTest(_Base):
    def _feed(self, vis):
        return {"tasks": [{
            "uuid8": "aa11bb22", "trail_visibility": vis,
            "prompts": [{"text": "secret prompt"}],
            "resume": {"command": "claude --resume x"},
            "digest": {"goal": "g", "state": "current state",
                       "steps_done": 1, "steps_total": 2,
                       "decisions_tail": ["chose X"]}}],
            "local_only": ["prompts", "resume"]}

    def test_private_strips_trail(self):
        safe = feeds.strip_local_only(self._feed("private"))
        task = safe["tasks"][0]
        self.assertNotIn("prompts", task)
        self.assertNotIn("resume", task)
        self.assertEqual(task["digest"]["state"], "")
        self.assertEqual(task["digest"]["decisions_tail"], [])

    def test_checkpoints_keeps_digest_drops_prompts(self):
        safe = feeds.strip_local_only(self._feed("checkpoints"))
        task = safe["tasks"][0]
        self.assertNotIn("prompts", task)
        self.assertEqual(task["digest"]["state"], "current state")
        self.assertEqual(task["digest"]["decisions_tail"], ["chose X"])

    def test_full_includes_prompt_trail(self):
        safe = feeds.strip_local_only(self._feed("full"))
        task = safe["tasks"][0]
        self.assertIn("prompts", task)
        self.assertNotIn("resume", task)             # machine-local path always stripped
        self.assertEqual(task["digest"]["state"], "current state")

    def test_default_is_private(self):
        f = self._feed("private")
        del f["tasks"][0]["trail_visibility"]        # unset → default private
        safe = feeds.strip_local_only(f)
        self.assertNotIn("prompts", safe["tasks"][0])
        self.assertEqual(safe["tasks"][0]["digest"]["decisions_tail"], [])


# ------------------------------------------------------------------ F6 --------

class ArtifactPatternTest(_Base):
    def test_github_pr(self):
        hits = artifacts.scan("see https://github.com/o/r/pull/9 please")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["kind"], "pr")
        self.assertEqual(hits[0]["id"], "o/r#9")

    def test_ado_pullrequest(self):
        hits = artifacts.scan("https://dev.azure.com/companyname/Projectname/_git/repo/pullrequest/1234")
        self.assertEqual(hits[0]["kind"], "pr")
        self.assertEqual(hits[0]["id"], "ado!1234")

    def test_ado_workitem(self):
        hits = artifacts.scan("story https://dev.azure.com/companyname/Projectname/_workitems/edit/2704")
        self.assertEqual(hits[0]["kind"], "story")
        self.assertEqual(hits[0]["id"], "ado#2704")

    def test_github_issue_is_story(self):
        hits = artifacts.scan("https://github.com/o/r/issues/42")
        self.assertEqual(hits[0]["kind"], "story")
        self.assertEqual(hits[0]["id"], "o/r#42")

    def test_dedup_and_mixed(self):
        text = ("https://github.com/o/r/pull/9 https://github.com/o/r/pull/9 "
                "https://dev.azure.com/x/y/_workitems/edit/5")
        hits = artifacts.scan(text)
        self.assertEqual(len(hits), 2)


class CaptureTest(_Base):
    def _attach(self, title):
        t = self._seed(title)
        ts.set_link("sess-1", t["id"])
        return t

    def test_capture_records_and_dedups(self):
        t = self._attach("Work with a PR")
        ns = SimpleNamespace(session="sess-1",
                             text="opened https://github.com/o/r/pull/9")
        ts.cmd_capture_artifacts(ns)
        t = ts.load_task(t["id"])
        self.assertTrue(any("pull/9" in p.get("url", "") for p in (t.get("prs") or [])))
        # dedup: re-capture the same url adds nothing
        ts.cmd_capture_artifacts(ns)
        self.assertEqual(len(ts.load_task(t["id"])["prs"]), 1)

    def test_capture_story(self):
        t = self._attach("Work with a story")
        ts.cmd_capture_artifacts(SimpleNamespace(
            session="sess-1",
            text="filed https://dev.azure.com/x/y/_workitems/edit/2704"))
        t = ts.load_task(t["id"])
        self.assertTrue(any("2704" in s.get("url", "") for s in (t.get("stories") or [])))

    def test_suppressed_in_workers(self):
        t = self._attach("Worker task")
        os.environ["TASK_STATION_SUPPRESS"] = "1"
        ts.cmd_capture_artifacts(SimpleNamespace(
            session="sess-1", text="https://github.com/o/r/pull/9"))
        self.assertEqual(ts.load_task(t["id"]).get("prs") or [], [])


class CrossPersonLinkTest(_Base):
    def test_autolink_and_memo_dedup(self):
        # peer task references the SAME PR signal id (o/r#9) my task will carry.
        self._write_peer(_peer_feed([_peer_task(prs=["o/r#9"])]))
        t = self._seed("My PR review")
        ts.set_link("sess-1", t["id"])
        # capture the matching PR onto my task → triggers cross-person auto-link
        ts.cmd_capture_artifacts(SimpleNamespace(
            session="sess-1", text="https://github.com/o/r/pull/9"))
        t = ts.load_task(t["id"])
        self.assertTrue(any(l["alias"] == "jpark" and l.get("kind") == "signal"
                            for l in (t.get("links") or [])))
        memos = t.get("memos") or []
        self.assertEqual(len(memos), 1)
        self.assertIn("o/r#9", memos[0]["text"])
        # re-capture → link + memo dedup (no second pair, no second memo)
        ts.cmd_capture_artifacts(SimpleNamespace(
            session="sess-1", text="https://github.com/o/r/pull/9"))
        t = ts.load_task(t["id"])
        self.assertEqual(len([l for l in t["links"] if l["alias"] == "jpark"]), 1)
        self.assertEqual(len(t.get("memos") or []), 1)

    def test_manual_pr_triggers_autolink(self):
        self._write_peer(_peer_feed([_peer_task(prs=["o/r#7"])]))
        t = self._seed("Manual PR task")
        out = ts._update_one(str(t["seq"]),
                             SimpleNamespace(pr=["https://github.com/o/r/pull/7"],
                                             pr_desc=None, story=None, story_desc=None,
                                             title=None, summary=None, append_summary=None,
                                             state=None, goal=None, step_add=None,
                                             step_done=None, step_undone=None, decision=None,
                                             log=None, color=None, effort=None,
                                             trail_visibility=None, relate=None, session=None))
        t = ts.load_task(t["id"])
        self.assertTrue(any(l["alias"] == "jpark" for l in (t.get("links") or [])))


# ------------------------------------------------------------------ LAW -------

class ParityLawTest(_Base):
    def _render(self):
        os.environ["TASK_STATION_INTERBRAIN"] = "off"
        out = ts.write_board()
        with open(out, encoding="utf-8") as f:
            return f.read()

    def test_single_brain_off_no_new_markup(self):
        self._seed("Alpha")
        self._seed("Beta")
        html = self._render()
        for needle in ('class="bchip"', 'class="focusstrip"', 'class="fcmd"',
                       'class="lchip"', ' data-brain="'):
            self.assertNotIn(needle, html,
                             "interbrain-off + single-brain must not emit %r" % needle)

    def test_second_brain_but_interbrain_off_still_parity(self):
        self._seed("Alpha")
        baseline = self._render()
        # add a second brain + assign a task — but interbrain OFF gates all of it.
        cfg = brains.load(self.tmp)
        brains.add(cfg, "work")
        for t in ts.sorted_tasks():
            brains.assign(cfg, t["id"], "work")
            break
        brains.save(cfg, self.tmp)
        again = self._render()
        self.assertNotIn('class="bchip"', again)
        self.assertNotIn(' data-brain="', again)


if __name__ == "__main__":
    unittest.main()
