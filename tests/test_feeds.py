"""lib/feeds.py — the ONE owner of the feed format (task #444).

Ported from the retired preview engine's test module: every test that covered the FEED
layer (serialization + its inverse, strip_local_only / sync_safe, signal ids, content
rev, view-model, archive shard, peers-then-demo load order, demo seeding) lives here
now. The preview shell's own tests (HTML chrome, 3-view UI, staging tray, mounts rail)
went away with the shell — see docs/specs/BOARD-RETIREMENT.md.

The exporter is READ-ONLY: it renders the real store into read-only `.js` feed
view-models (file://-safe `<script src>` sidecars) and NEVER writes the store.
"""
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "lib")
TOOLS = os.path.join(ROOT, "tools")
for p in (LIB, TOOLS):
    if p not in sys.path:
        sys.path.insert(0, p)

import store  # noqa: E402
import feeds  # noqa: E402

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="feeds-tests-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        # self identity is runtime config now (feeds.self_alias()) — pin it so every
        # handle/alias assertion below keeps meaning what it meant.
        os.environ["TASK_STATION_SELF_ALIAS"] = "rnguyen"
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.DELEGATE_REGISTRY = os.path.join(self.tmp, "workers.json")
        store.reset_cache()

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        os.environ.pop("TASK_STATION_SELF_ALIAS", None)
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

    def _add_usage(self, task, sid="s-usage", models=None):
        row = {"session_id": sid, "task_id": task["id"], "role": "hub",
               "first_ts": "2026-07-01T00:00:00", "last_ts": "2026-07-01T01:00:00",
               "models": models or {"claude-opus-4-8":
                                    {"in": 100000, "out": 20000, "cache_read": 5000000,
                                     "cost_usd": 12.5}}}
        ts._backend().upsert_session_usage(row)
        t = ts.load_task(task["id"])
        t.setdefault("sessions", []).append(sid)
        ts.save_task(t)
        return ts.load_task(task["id"])

    def _export(self):
        return feeds.export_self_feed(ts, self.tmp)

    def _self_feed(self):
        """The self feed read back OFF DISK through the canonical parser."""
        return feeds.parse_feed_file(os.path.join(self.tmp, "feeds", "self.js"))

    def _db_hash(self):
        with open(os.path.join(ts.STORE, "tasks.db"), "rb") as f:
            return hashlib.sha1(f.read()).hexdigest()


# ---- the wire form: serialization and its exact inverse ---------------------------

class WireFormTest(_Base):
    def test_feed_js_round_trips_through_parse(self):
        """AC4: `_feed_js` → `parse_feed_file` is an identity round-trip. These two are
        the only implementations of the format, so they must agree exactly."""
        feed = {"schema": 3, "kind": "peer", "alias": "jpark",
                "tasks": [{"uuid8": "abcd1234", "title": "Peer task", "digest": {}}],
                "memos": []}
        path = os.path.join(self.tmp, "jpark.js")
        feeds._atomic_write(path, feeds._feed_js("jpark", feed))
        self.assertEqual(feeds.parse_feed_file(path), feed)

    def test_wire_form_is_a_js_assignment_plus_registry_push(self):
        """The `file://` contract: a `.js` sidecar assigning a namespaced global and
        registering it — NOT json, because file:// blocks fetch but loads scripts."""
        text = feeds._feed_js("jpark", {"tasks": []})
        self.assertTrue(text.startswith("window.__TSFEED_jpark = "))
        self.assertIn("window.__TSFEEDS = window.__TSFEEDS || []", text)
        self.assertIn("push(window.__TSFEED_jpark)", text)

    def test_alias_is_sanitized_to_a_js_identifier(self):
        """A hostile/odd alias must not be able to break out of the identifier slot."""
        text = feeds._feed_js("rnguyen-demo", {"tasks": []})
        self.assertIn("window.__TSFEED_rnguyen_demo = ", text)
        self.assertEqual(feeds._js_ident("a.b/c;d"), "a_b_c_d")
        self.assertEqual(feeds._js_ident(""), "feed")

    def test_payload_is_data_never_code(self):
        """json.dumps keeps the payload strictly data — a task title that looks like JS
        must arrive as a string, not execute."""
        feed = {"tasks": [{"uuid8": "1", "title": "</script><script>alert(1)"}]}
        path = os.path.join(self.tmp, "x.js")
        feeds._atomic_write(path, feeds._feed_js("x", feed))
        back = feeds.parse_feed_file(path)
        self.assertEqual(back["tasks"][0]["title"], "</script><script>alert(1)")

    def test_parse_skips_unparseable_and_never_raises(self):
        """An IIFE demo fixture (or any non-canonical file) is SKIPPED, not fatal."""
        p = os.path.join(self.tmp, "iife.js")
        with open(p, "w", encoding="utf-8") as f:
            f.write("(function(){window.__TSFEED_x={tasks:[]};})();\n")
        self.assertIsNone(feeds.parse_feed_file(p))
        self.assertIsNone(feeds.parse_feed_file(os.path.join(self.tmp, "absent.js")))

    def test_parse_ignores_the_trailing_registry_line(self):
        """The self feed's second line is a registry push — the parser scans every line
        and must still return the feed, not choke on the push."""
        self._seed("A task")
        self._export()
        self.assertEqual(self._self_feed()["kind"], "self")


# ---- the feed root: one place ------------------------------------------------------

class FeedRootTest(_Base):
    # the retired preview's second root — nothing may recreate it (see BOARD-RETIREMENT.md)
    _DEAD_ROOT = "board" + "3"

    def test_unified_root_and_self_feed_paths(self):
        """The root is `<data_dir>/feeds/` — the preview's second root is gone."""
        self._seed("A task")
        self._export()
        self.assertEqual(feeds.feeds_dir(self.tmp), os.path.join(self.tmp, "feeds"))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "feeds", "self.js")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, self._DEAD_ROOT)))

    def test_peers_then_demo_load_order(self):
        """AC4: peers load BEFORE demo (real received feeds outrank fixtures), each
        group sorted; self/archive feeds are excluded — they're the local brain."""
        for sub, names in (("peers", ["zeta.js", "alpha.js"]),
                           ("demo", ["kosei.js", "jpark.js"])):
            d = os.path.join(self.tmp, "feeds", sub)
            os.makedirs(d, exist_ok=True)
            for n in names:
                with open(os.path.join(d, n), "w", encoding="utf-8") as f:
                    f.write(feeds._feed_js(n[:-3], {"tasks": []}))
        # self feeds live in the root and in-group; neither may appear as a peer.
        os.makedirs(os.path.join(self.tmp, "feeds", "peers"), exist_ok=True)
        with open(os.path.join(self.tmp, "feeds", "peers", "self-mirror.js"), "w",
                  encoding="utf-8") as f:
            f.write(feeds._feed_js("self", {"tasks": []}))
        got = [os.path.relpath(p, self.tmp) for p in feeds.peer_feed_files(self.tmp)]
        self.assertEqual(got, [os.path.join("feeds", "peers", "alpha.js"),
                               os.path.join("feeds", "peers", "zeta.js"),
                               os.path.join("feeds", "demo", "jpark.js"),
                               os.path.join("feeds", "demo", "kosei.js")])

    def test_missing_dirs_are_not_an_error(self):
        self.assertEqual(feeds.peer_feed_files(self.tmp), [])

    def test_read_self_feed_round_trips_and_tolerates_absence(self):
        self.assertIsNone(feeds.read_self_feed(self.tmp))
        self._seed("A task")
        self._export()
        self.assertEqual(feeds.read_self_feed(self.tmp)["alias"], "rnguyen")


# ---- signal ids: the frozen join key ----------------------------------------------

class SignalIdTest(_Base):
    def test_pr_signal_id_stable_forms(self):
        """AC4: the join key format is FROZEN — changing any of these silently breaks F6
        cross-brain PR auto-linking."""
        cases = [
            ("https://github.com/acme/led/pull/321", "acme/led#321"),
            ("https://github.com/o/r/pull/3", "o/r#3"),
            ("https://dev.azure.com/org/proj/_git/repo/pullrequest/12", "ado!12"),
            ("https://x.visualstudio.com/p/_git/r/pullrequest/7", "ado!7"),
            ("", ""),
        ]
        for url, want in cases:
            self.assertEqual(feeds._pr_signal_id(url), want, url)

    def test_pr_signal_id_falls_back_to_last_segment(self):
        self.assertEqual(feeds._pr_signal_id("https://example.com/a/b/99"), "99")
        self.assertEqual(feeds._pr_signal_id("https://example.com/a/b/99/"), "99")

    def test_agrees_with_artifacts_on_the_shared_forms(self):
        """`lib/artifacts.py` documents that its `pr_signal_id` MUST agree with this one.
        Assert the agreement for the forms BOTH implement (GitHub + ADO)."""
        import artifacts
        for url in ("https://github.com/acme/led/pull/321",
                    "https://dev.azure.com/org/proj/_git/repo/pullrequest/12"):
            self.assertEqual(feeds._pr_signal_id(url), artifacts.pr_signal_id(url), url)

    def test_signals_land_on_the_view_model(self):
        self._seed("Task with a PR",
                   prs=[{"url": "https://github.com/o/r/pull/7", "desc": ""}])
        self._export()
        vm = [t for t in self._self_feed()["tasks"] if t["title"] == "Task with a PR"][0]
        self.assertEqual(vm["signals"]["prs"], ["o/r#7"])


# ---- sync_safe: the local_only contract -------------------------------------------

class SyncSafeTest(_Base):
    def _feed(self, visibility):
        return {"local_only": list(feeds.LOCAL_ONLY_FIELDS),
                "tasks": [{"uuid8": "abcd1234", "trail_visibility": visibility,
                           "prompts": [{"text": "secret prompt"}],
                           "resume": {"command": "claude --resume abc"},
                           "digest": {"goal": "G", "state": "mid-flight",
                                      "steps_done": 1, "steps_total": 3,
                                      "decisions_tail": ["chose X"]}}]}

    def test_local_only_stamped_and_stripped(self):
        """AC4: the self feed declares its machine-local fields, and the sync_safe gate
        removes them."""
        self._seed("x")
        self._export()
        feed = self._self_feed()
        self.assertEqual(feed["local_only"], ["prompts", "resume"])
        feed["tasks"][0]["prompts"] = [{"text": "secret"}]
        feed["tasks"][0]["resume"] = {"command": "claude --resume x"}
        safe = feeds.strip_local_only(feed)
        self.assertNotIn("prompts", safe["tasks"][0])
        self.assertNotIn("resume", safe["tasks"][0])
        self.assertEqual(safe["local_only"], [])
        self.assertIn("prompts", feed["tasks"][0])   # original untouched

    def test_resume_always_dropped_at_every_visibility(self):
        """`resume` is a MACHINE-LOCAL path — no visibility setting can export it."""
        for vis in ("private", "checkpoints", "full"):
            safe = feeds.strip_local_only(self._feed(vis))
            self.assertNotIn("resume", safe["tasks"][0], vis)

    def test_private_drops_prompts_and_blanks_the_trail(self):
        safe = feeds.strip_local_only(self._feed("private"))["tasks"][0]
        self.assertNotIn("prompts", safe)
        self.assertEqual(safe["digest"]["state"], "")
        self.assertEqual(safe["digest"]["decisions_tail"], [])
        self.assertEqual(safe["digest"]["goal"], "G")        # identity survives
        self.assertEqual(safe["digest"]["steps_total"], 3)

    def test_checkpoints_keeps_the_digest_but_not_prompts(self):
        safe = feeds.strip_local_only(self._feed("checkpoints"))["tasks"][0]
        self.assertNotIn("prompts", safe)
        self.assertEqual(safe["digest"]["state"], "mid-flight")
        self.assertEqual(safe["digest"]["decisions_tail"], ["chose X"])

    def test_full_keeps_the_prompt_trail(self):
        safe = feeds.strip_local_only(self._feed("full"))["tasks"][0]
        self.assertEqual(safe["prompts"], [{"text": "secret prompt"}])

    def test_missing_visibility_defaults_to_private(self):
        f = {"tasks": [{"uuid8": "a", "prompts": [{"text": "p"}],
                        "digest": {"state": "s", "decisions_tail": ["d"]}}]}
        safe = feeds.strip_local_only(f)["tasks"][0]
        self.assertNotIn("prompts", safe)
        self.assertEqual(safe["digest"]["state"], "")


# ---- content rev -------------------------------------------------------------------

class ContentRevTest(_Base):
    def test_rev_stable_for_same_data_changes_on_mutation(self):
        a = [{"uuid8": "1", "title": "one"}]
        self.assertEqual(feeds._feed_content_rev(a), feeds._feed_content_rev(list(a)))
        b = [{"uuid8": "1", "title": "one CHANGED"}]
        self.assertNotEqual(feeds._feed_content_rev(a), feeds._feed_content_rev(b))

    def test_self_feed_carries_its_own_rev(self):
        """F5 subscriptions: a peer mounting this feed diffs it by `rev`."""
        self._seed("x")
        self._export()
        feed = self._self_feed()
        self.assertTrue(feed["rev"])
        self.assertEqual(feed["rev"], feeds._feed_content_rev(feed["tasks"]))

    def test_empty_rev_is_not_an_error(self):
        self.assertEqual(feeds._feed_content_rev([]), feeds._feed_content_rev(None))


# ---- view-model --------------------------------------------------------------------

class ViewModelTest(_Base):
    def test_feed_schema(self):
        self._seed("A task")
        self._export()
        self.assertEqual(self._self_feed()["schema"], 3)

    def test_category_key_in_feed(self):
        self._seed("Blue task", color="blue")
        self._export()
        vm = [t for t in self._self_feed()["tasks"] if t["title"] == "Blue task"][0]
        self.assertEqual(vm["category"]["key"], "blue")

    def test_uuid8_handle_and_uuid_normalized_relations(self):
        """Relations are UUID-keyed, never seq — the join key that survives sync."""
        parent = self._seed("Parent")
        child = self._seed("Child")
        child["related"] = [{"id": parent["id"], "seq": parent["seq"],
                             "kind": "spawned-from", "ts": ts._now()}]
        ts.save_task(child)
        self._export()
        by_title = {t["title"]: t for t in self._self_feed()["tasks"]}
        c = by_title["Child"]
        self.assertEqual(len(c["uuid8"]), 8)
        self.assertEqual(c["handle"], "rnguyen-%s" % child["seq"])
        rel = c["relations"][0]
        self.assertEqual(rel["uuid8"], parent["id"][:8])
        self.assertNotIn("seq", rel)

    def test_handle_falls_back_to_uuid8_without_seq(self):
        t = ts.new_task("Seqless", "summary")
        t.pop("seq", None)
        ts.save_task(t)
        self._export()
        vm = [x for x in self._self_feed()["tasks"] if x["title"] == "Seqless"][0]
        self.assertEqual(vm["handle"], "rnguyen-%s" % t["id"][:8])

    def test_tokens_join_and_estimate_fallback(self):
        rich = self._seed("Rich")
        self._add_usage(rich)
        self._seed("Bare")
        self._export()
        by = {t["title"]: t for t in self._self_feed()["tasks"]}
        self.assertEqual(by["Rich"]["tokens"], 100000 + 20000 + 5000000)
        self.assertFalse(by["Rich"]["tokens_estimated"])
        self.assertIn("opus", by["Rich"]["models"])
        self.assertTrue(by["Bare"]["tokens_estimated"])

    def test_rich_self_fields(self):
        t = self._seed("Rich task", goal="G")
        t["history"] = [{"ts": "t1", "text": "did a thing"}]
        t["glossary"] = [{"name": "Widget", "layer": "domain", "state": "stable",
                          "def": "a thing"}]
        t["summary"] = "the summary"
        ts.save_task(t)
        self._export()
        vm = [x for x in self._self_feed()["tasks"] if x["title"] == "Rich task"][0]
        self.assertEqual(vm["history"], [{"ts": "t1", "text": "did a thing"}])
        self.assertEqual(vm["glossary"][0]["name"], "Widget")
        self.assertEqual(vm["summary"], "the summary")
        for k in ("session_tree", "work_mix", "sessions", "prompts", "open_command"):
            self.assertIn(k, vm)


# ---- self identity: runtime config, never a code literal ---------------------------

class SelfAliasTest(_Base):
    """3.0.0 ships publicly, so the self alias may not be baked into the module. It
    resolves at call time: env TASK_STATION_SELF_ALIAS > config `self_alias` > the OS
    username — and the SELF fill comes from SELF_COLOR, not an OWNER_COLORS entry."""

    def _pin(self, value):
        """Set (`value`) or clear (None) the env alias for ONE test. The cleanup pops
        it — exactly what _Base.tearDown does — so no alias leaks into the next test
        (the whole suite shares one process)."""
        self.addCleanup(os.environ.pop, "TASK_STATION_SELF_ALIAS", None)
        if value is None:
            os.environ.pop("TASK_STATION_SELF_ALIAS", None)
        else:
            os.environ["TASK_STATION_SELF_ALIAS"] = value

    def test_env_alias_owns_the_whole_self_feed(self):
        """The env alias reaches every identity field the feed carries — feed
        alias/owner, the per-task handle, participants — and the fill is SELF_COLOR."""
        self._pin("kdoe")
        t = self._seed("Aliased")
        self._export()
        feed = self._self_feed()
        self.assertEqual(feed["alias"], "kdoe")
        self.assertEqual(feed["owner"], "kdoe")
        self.assertEqual(feed["color"], feeds.SELF_COLOR["light"])
        self.assertEqual(feed["color_dark"], feeds.SELF_COLOR["dark"])
        vm = [x for x in feed["tasks"] if x["title"] == "Aliased"][0]
        self.assertEqual(vm["handle"], "kdoe-%s" % t["seq"])
        self.assertEqual(vm["participants"], ["kdoe"])
        self.assertEqual(vm["owner"], "kdoe")

    def test_config_self_alias_is_the_fallback(self):
        """No env → the runtime config key under TASK_STATION_HOME."""
        self._pin(None)
        import config
        config.set("self_alias", "cfguser")
        self.assertEqual(feeds.self_alias(), "cfguser")

    def test_default_is_the_os_username(self):
        """No env, no config key → the OS username, so a fresh install still has an
        identity without any setup."""
        self._pin(None)
        import getpass
        self.assertEqual(feeds.self_alias(), getpass.getuser())

    def test_no_identity_literal_left_in_the_module(self):
        self.assertNotIn("rnguyen", feeds.OWNER_COLORS)
        self.assertEqual(feeds.SELF_COLOR, {"light": "#cf8a22", "dark": "#b97e1f"})


# ---- archive shard -----------------------------------------------------------------

class ArchiveShardTest(_Base):
    def test_closed_beyond_50_split_to_archive(self):
        for i in range(55):
            t = self._seed("Closed %02d" % i, status="closed")
            t["updated_ts"] = 1000 + i
            ts.save_task(t)
        self._seed("Open live one")
        self._export()
        feed = self._self_feed()
        self.assertEqual(len([t for t in feed["tasks"] if t["status"] == "closed"]), 50)
        self.assertTrue(feed["has_archive"])
        arch = feeds.parse_feed_file(
            os.path.join(self.tmp, "feeds", "self-archive.js"))
        self.assertEqual(len(arch["tasks"]), 5)

    def test_no_archive_shard_when_nothing_is_old(self):
        self._seed("Just one")
        self._export()
        self.assertFalse(self._self_feed()["has_archive"])
        self.assertFalse(os.path.exists(
            os.path.join(self.tmp, "feeds", "self-archive.js")))


# ---- read-only guarantee -----------------------------------------------------------

class NoStoreWriteTest(_Base):
    def test_export_does_not_mutate_task_content(self):
        self._seed("Untouched", goal="keep me")
        before = self._task_content_hash()
        self._export()
        self.assertEqual(before, self._task_content_hash())

    def _task_content_hash(self):
        blob = json.dumps(sorted(
            json.dumps(store.strip_rev(t), sort_keys=True, default=str)
            for t in ts._backend().all_tasks()), default=str)
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()


# ---- brains resolution onto the feed ----------------------------------------------

class BrainsOnFeedTest(_Base):
    def test_brain_and_shares_in_vm(self):
        import brains
        t = self._seed("Feature", color="blue")              # blue → tag INFRA
        cfg = brains.load(self.tmp)
        brains.add(cfg, "work")
        brains.assign(cfg, t["id"], "work")
        brains.share(cfg, "work", "jpark")                   # untagged → always applies
        brains.share(cfg, "work", "org", tag="INFRA")        # matches blue
        brains.save(cfg, self.tmp)
        self._export()
        vm = [x for x in self._self_feed()["tasks"] if x["title"] == "Feature"][0]
        self.assertEqual(vm["brain"], "work")
        self.assertIn("jpark", vm["shares"])
        self.assertIn("org", vm["shares"])
        self.assertTrue(vm["shared_org"])

    def test_tag_scoped_share_filtered_by_category(self):
        import brains
        t = self._seed("Green feature", color="green")        # green → tag FEATURE
        cfg = brains.load(self.tmp)
        brains.add(cfg, "work")
        brains.assign(cfg, t["id"], "work")
        brains.share(cfg, "work", "org", tag="INFRA")        # INFRA-scoped, task is FEATURE
        brains.save(cfg, self.tmp)
        self._export()
        vm = [x for x in self._self_feed()["tasks"] if x["title"] == "Green feature"][0]
        self.assertNotIn("org", vm["shares"])
        self.assertFalse(vm["shared_org"])

    def test_brains_list_on_self_feed(self):
        import brains
        cfg = brains.load(self.tmp)
        brains.add(cfg, "work")
        brains.save(cfg, self.tmp)
        self._seed("x")
        self._export()
        names = [b["name"] for b in self._self_feed()["brains"]]
        self.assertIn("main", names)
        self.assertIn("work", names)


# ---- demo seeding ------------------------------------------------------------------

class SeedDemoTest(_Base):
    def test_seed_writes_into_the_unified_feed_root(self):
        self._seed("x")
        self._export()
        import seed_demo
        seed_demo.seed(self.tmp, feeds.read_self_feed(self.tmp))
        d = os.path.join(self.tmp, "feeds", "demo")
        for n in ("jpark.js", "kosei.js", "org.js", "rnguyen-demo.js"):
            self.assertTrue(os.path.exists(os.path.join(d, n)), n)
        self.assertFalse(os.path.exists(
            os.path.join(self.tmp, FeedRootTest._DEAD_ROOT)))

    def test_seed_rewrites_sentinels_to_real_signals(self):
        t = self._seed("Real feature",
                       prs=[{"url": "https://github.com/acme/led/pull/321", "desc": ""}])
        self._export()
        import seed_demo
        seed_demo.seed(self.tmp, feeds.read_self_feed(self.tmp))
        with open(os.path.join(self.tmp, "feeds", "demo", "jpark.js"),
                  encoding="utf-8") as f:
            txt = f.read()
        self.assertIn("acme/led#321", txt)               # the real PR signal id
        for s in ("__XREF_PR_1__", "__XREF_PR_2__", "__XREF_STORY_1__",
                  "__REAL_UUID8_1__", "__REAL_UUID8_2__"):
            self.assertNotIn(s, txt)
        self.assertIn(t["id"][:8], txt)                  # a real uuid8 memo target

    def test_seed_is_persistent_across_runs(self):
        self._seed("x")
        self._export()
        import seed_demo
        seed_demo.seed(self.tmp, feeds.read_self_feed(self.tmp))
        p = os.path.join(self.tmp, "feeds", "demo", "jpark.js")
        with open(p, "a", encoding="utf-8") as f:
            f.write("// hand-edited\n")
        seed_demo.seed(self.tmp, feeds.read_self_feed(self.tmp))
        with open(p, encoding="utf-8") as f:
            self.assertIn("// hand-edited", f.read())

    def test_seed_without_a_self_feed_still_rewrites_to_benign_fallbacks(self):
        """No self.js yet → benign demo fallbacks, no sentinels left, edges stay
        demo-internal (nothing crashes on an unseeded machine)."""
        import seed_demo
        seed_demo.seed(self.tmp, feeds.read_self_feed(self.tmp))
        for n in ("jpark.js", "kosei.js", "org.js"):
            with open(os.path.join(self.tmp, "feeds", "demo", n),
                      encoding="utf-8") as f:
                txt = f.read()
            for s in seed_demo._SENTINELS:
                self.assertNotIn(s, txt, "%s: %s" % (n, s))

    def test_seeded_demo_feeds_are_all_parseable(self):
        """The seeded copies must parse — this is the end-to-end version of
        ShippedFixtureTest below, through the seeder's rewrite step."""
        import seed_demo
        self._seed("Real one",
                   prs=[{"url": "https://github.com/acme/led/pull/321", "desc": ""}])
        self._export()
        seed_demo.seed(self.tmp, feeds.read_self_feed(self.tmp))
        paths_ = feeds.peer_feed_files(self.tmp)
        self.assertEqual(len(paths_), 4, "all 4 demo feeds must be discovered")
        for p in paths_:
            feed = feeds.parse_feed_file(p)
            self.assertIsNotNone(feed, "%s must parse AFTER seeding (sentinel rewrite "
                                       "must not break the JSON)" % os.path.basename(p))
            self.assertTrue(feed.get("tasks"), os.path.basename(p))

    def test_org_label_applied_to_the_org_feed(self):
        import seed_demo
        seed_demo.seed(self.tmp, None, org_label="Company Brain")
        with open(os.path.join(self.tmp, "feeds", "demo", "org.js"),
                  encoding="utf-8") as f:
            self.assertIn("Company Brain · org", f.read())

    def test_clean_removes_demo(self):
        import seed_demo
        seed_demo.seed(self.tmp, None)
        self.assertTrue(seed_demo.clean(self.tmp))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "feeds", "demo")))
        self.assertFalse(seed_demo.clean(self.tmp))       # second call is a no-op

    def test_clean_leaves_the_self_feed_alone(self):
        self._seed("x")
        self._export()
        import seed_demo
        seed_demo.seed(self.tmp, feeds.read_self_feed(self.tmp))
        seed_demo.clean(self.tmp)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "feeds", "self.js")))

    def test_demo_never_touches_store(self):
        self._seed("real", prs=[{"url": "https://github.com/a/b/pull/5", "desc": ""}])
        self._export()
        import seed_demo
        before = self._db_hash()
        seed_demo.seed(self.tmp, feeds.read_self_feed(self.tmp))
        self.assertEqual(before, self._db_hash())


# ---- the shipped fixtures must be CANONICAL ----------------------------------------

class ShippedFixtureTest(unittest.TestCase):
    """THE regression guard for the bug that shipped: the four fixtures in
    fixtures/demo-feeds/ were IIFE-wrapped (`window.__TSFEED_x = feed;` over a JS
    variable), which only a browser could evaluate. The server-side path skipped every one
    of them, so demo federation rendered NOWHERE once the client shell was retired. These
    asserts fail the moment a fixture stops being pure, parseable, canonical data.

    No tmpdir/store needed — this reads the committed fixtures straight off disk.
    """
    FIXTURES = ("jpark.js", "kosei.js", "org.js", "rnguyen-demo.js")

    def _path(self, name):
        return os.path.join(ROOT, "fixtures", "demo-feeds", name)

    def test_every_shipped_fixture_parses_to_a_dict_with_tasks(self):
        """AC2: each shipped fixture yields a dict with a non-empty tasks list."""
        for name in self.FIXTURES:
            feed = feeds.parse_feed_file(self._path(name))
            self.assertIsInstance(feed, dict, "%s must parse to a dict" % name)
            self.assertTrue(feed.get("tasks"), "%s must carry tasks" % name)
            self.assertIsInstance(feed["tasks"], list, name)

    def test_no_shipped_fixture_is_skipped_as_the_local_brain(self):
        """The second half of the same bug: a feed whose `kind` is self/archive is dropped
        by foreign_view_models as 'the local brain, already rendered from the store'.
        rnguyen-demo.js used to be kind=self, so it rendered zero rows even once
        parseable. No shipped demo fixture may claim to be the local brain."""
        for name in self.FIXTURES:
            kind = (feeds.parse_feed_file(self._path(name)).get("kind") or "")
            self.assertNotIn(kind, ("self", "archive"),
                             "%s kind=%r would be skipped server-side" % (name, kind))

    def test_fixtures_carry_what_the_foreign_row_and_graph_need(self):
        """Fields the row builder + graph actually read — `category.key` drives the accent
        (`_foreign_view_model` does `color = cat.get('key')`) and the graph node colour, so
        a category without it renders uncoloured."""
        for name in self.FIXTURES:
            feed = feeds.parse_feed_file(self._path(name))
            for key in ("alias", "owner", "kind", "color"):
                self.assertTrue(feed.get(key), "%s: feed.%s" % (name, key))
            for t in feed["tasks"]:
                for key in ("uuid8", "handle", "title", "status", "owner", "brain"):
                    self.assertTrue(t.get(key), "%s/%s: %s" % (name, t.get("handle"), key))
                cat = t.get("category")
                self.assertIsInstance(cat, dict, name)
                for key in ("key", "tag", "dot", "hex", "hex_dark"):
                    self.assertTrue(cat.get(key),
                                    "%s/%s: category.%s" % (name, t.get("handle"), key))
                self.assertIsInstance(t.get("digest"), dict, name)
                self.assertIsInstance(t.get("signals"), dict, name)

    def test_org_fixture_is_marked_as_the_org_brain(self):
        """org.js IS the org brain: kind=org and every task shared to org, which is what
        puts it in the org hull and the org galaxy blob."""
        feed = feeds.parse_feed_file(self._path("org.js"))
        self.assertEqual(feed["kind"], "org")
        for t in feed["tasks"]:
            self.assertTrue(t.get("shared_org"), t.get("handle"))
            self.assertIn("org", t.get("shares") or [], t.get("handle"))

    def test_sentinels_survive_verbatim_for_the_seeder_to_rewrite(self):
        """seed_demo rewrites these by TEXT substitution, so they must appear literally.
        Losing one silently unbinds demo edges from the user's real tasks."""
        import seed_demo
        blob = ""
        for name in self.FIXTURES:
            with open(self._path(name), encoding="utf-8") as f:
                blob += f.read()
        for s in seed_demo._SENTINELS:
            self.assertIn(s, blob, "sentinel %s vanished from the fixtures" % s)

    def test_fixtures_are_pure_data_with_no_executable_logic(self):
        """Canonical form is DATA. No IIFE, no functions, no variable indirection — those
        are exactly what made the old fixtures unreadable server-side."""
        for name in self.FIXTURES:
            with open(self._path(name), encoding="utf-8") as f:
                body = "".join(l for l in f if not l.lstrip().startswith(("/*", "*")))
            for needle in ("function", "(function", "var ", "=>"):
                self.assertNotIn(needle, body, "%s still contains %r" % (name, needle))

    def test_wire_form_matches_what_the_writer_emits(self):
        """The fixtures must be in the SAME shape `_feed_js` produces — one-line
        assignment + registry push — since that is what the parser and real sync assume."""
        for name in self.FIXTURES:
            with open(self._path(name), encoding="utf-8") as f:
                lines = [l.rstrip("\n") for l in f]
            assign = [l for l in lines if l.startswith("window.__TSFEED_")]
            push = [l for l in lines if l.startswith("(window.__TSFEEDS")]
            self.assertEqual(len(assign), 1, "%s: one assignment line" % name)
            self.assertEqual(len(push), 1, "%s: one registry push" % name)
            self.assertTrue(assign[0].endswith(";"), name)
            # the payload is inline on that ONE line — the parser reads no further
            self.assertIn('{"', assign[0], "%s: JSON must be inline" % name)


# ---- the board writes the feed root ------------------------------------------------

class BoardWritesFeedsTest(_Base):
    def test_board_write_exports_the_self_feed(self):
        """AC5: `/todo board` writes board.html + feeds/ + board.rev.js. The feed layer
        has exactly one producer now, so a board write must keep it current."""
        self._seed("Alpha")
        out = ts.write_board()
        self.assertTrue(out.endswith("board.html"))
        self.assertTrue(os.path.exists(out))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "board.rev.js")))
        feed = feeds.read_self_feed(self.tmp)
        self.assertIsNotNone(feed, "board write must export feeds/self.js")
        self.assertIn("Alpha", [t["title"] for t in feed["tasks"]])

    def test_board_feed_export_tracks_task_changes(self):
        self._seed("Alpha")
        ts.write_board()
        rev1 = feeds.read_self_feed(self.tmp)["rev"]
        self._seed("Beta")
        ts.write_board()
        feed2 = feeds.read_self_feed(self.tmp)
        self.assertNotEqual(rev1, feed2["rev"])
        self.assertIn("Beta", [t["title"] for t in feed2["tasks"]])

    def test_refused_downgrade_writes_nothing(self):
        """The refuse-downgrade guard covers the feed export too — a stale session's
        passive refresh must not touch the board OR the feeds."""
        self._seed("Alpha")
        out = ts.write_board()
        with open(out, "w", encoding="utf-8") as f:
            f.write('<meta name="ts-board-version" content="99.9.9"> sentinel-body')
        shutil.rmtree(os.path.join(self.tmp, "feeds"), ignore_errors=True)
        ts.write_board(guard_downgrade=True)             # passive path → refused
        with open(out, encoding="utf-8") as f:
            self.assertIn("sentinel-body", f.read())
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "feeds", "self.js")))


if __name__ == "__main__":
    unittest.main()
