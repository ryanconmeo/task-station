"""Visual HTML board: `task-station board` writes a (mostly) self-contained HTML
file of all tasks (open + closed) — seqs, titles, briefing fields — with NO
server, NO deps, and NO EXTERNAL asset references. Inline <script>/<style> ARE
allowed (1.19 theme toggle + hover-scroll); only external assets are forbidden.
Empty store renders without crashing."""
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


# Inline <script>/<style> are allowed; these needles flag EXTERNAL assets only
# (a PR anchor's href="https://…" is legitimate CONTENT, not an external asset).
# `src=` is matched only when REMOTE (src="http / src=//) — the change-driven poll
# appends a <script s.src='board.rev.js'> for a SAME-DIRECTORY local sidecar, which is
# explicitly allowed and is NOT an external asset.
_EXTERNAL_NEEDLES = ('src="http', "src='http", 'src="//', "src='//",
                     "<link ", "@import", "url(http", "//fonts.")


class BoardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        # isolate the delegate registry per test (a stray module-global path would let
        # one test's workers.json leak into another's session_tree).
        ts.DELEGATE_REGISTRY = os.path.join(self.tmp, "workers.json")
        ts.store.reset_cache()

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        ts.store.reset_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self, title, color="green", effort="m", closed=False, status=None):
        t = ts.new_task(title, "summary for " + title, color=color, effort=effort)
        ts.save_task(t)
        ts.ensure_seqs()
        t = ts.load_task(t["id"])
        if closed:
            t["status"] = "closed"
        elif status:
            t["status"] = status
        ts.save_task(t)
        return ts.load_task(t["id"])

    def _run_board(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_board(_Args(open=False))
        path = buf.getvalue().strip().splitlines()[-1]
        with open(path, encoding="utf-8") as f:
            return path, f.read()

    def _attach_hub(self, t, sid="hub-sess", **extra):
        """Give a task a recorded hub session (no live transcript → resume_command
        falls back to the labeled fresh-start one-liner, which is all the board needs)."""
        meta = {"cwd": "/work/repo", "ts": ts._now(), "role": "hub"}
        meta.update(extra)
        t.setdefault("session_meta", {})[sid] = meta
        t.setdefault("sessions", []).append(sid)
        ts.save_task(t)
        return ts.load_task(t["id"])

    def test_board_writes_self_contained_html(self):
        a = self._seed("Open feature task")
        b = self._seed("Done thing", closed=True)
        path, html = self._run_board()
        self.assertTrue(path.endswith("board.html"))
        self.assertTrue(os.path.exists(path))
        # valid-ish document shell + inline style/script, no external assets
        self.assertIn("<!doctype html>", html.lower())
        self.assertIn("<style>", html)
        self.assertIn("<script", html)        # inline JS is now allowed (toggle + scroll)
        for needle in _EXTERNAL_NEEDLES:
            self.assertNotIn(needle, html,
                             "board must have no external assets (found %r)" % needle)
        # both tasks present by seq + title; both sections shown
        self.assertIn(str(a["seq"]), html)
        self.assertIn("Open feature task", html)
        self.assertIn("Done thing", html)
        self.assertIn("Open", html)
        self.assertIn("Closed", html)

    def test_board_no_http_when_no_pr_urls(self):
        # With no PR URLs anywhere, a self-contained board LOADS nothing remote. (The
        # footer GitHub <a href> is a hyperlink the user clicks, NOT a loaded asset, so
        # check the asset-loading forms — src=/<link/@import/url(http/fonts — rather than
        # a blanket "http" which would catch that link.)
        self._seed("Plain task")
        _, html = self._run_board()
        for needle in _EXTERNAL_NEEDLES:
            self.assertNotIn(needle, html)

    def test_board_surfaces_briefing_fields(self):
        t = self._seed("Briefed task")
        t["state"] = "next: ship the board"
        t["files"] = ["/repo/lib/render_board.py"]
        t["projects"] = ["task-station"]
        t["log"] = [{"ts": "t1", "note": "PR https://github.com/o/r/pull/3"}]
        ts.save_task(t)
        _, html = self._run_board()
        self.assertIn("next: ship the board", html)
        self.assertIn("render_board.py", html)
        self.assertIn("task-station", html)
        # PR link rendered as a real anchor (content, not an external asset)
        self.assertIn('href="https://github.com/o/r/pull/3"', html)

    def test_board_renders_on_demand_history_collapsed(self):
        # The `--log` trail (stored as `history`, entries {ts,text}) renders as a
        # COLLAPSED section. WS6 made history its OWN collapsible section
        # (<details class="sec sec-history">, keeping the hist:<seq> data-key), all escaped.
        t = self._seed("History task")
        t["history"] = [
            {"ts": "2026-07-02T10:00:00", "text": "v1.52.0 shipped: digest split"},
            {"ts": "2026-07-02T11:00:00", "text": "found <edge> & case"},
        ]
        ts.save_task(t)
        _, html = self._run_board()
        self.assertIn('<details class="sec sec-history"', html)
        self.assertIn('data-key="hist:%s"' % t["seq"], html)   # persisted across refresh
        self.assertIn("history (2)", html)                      # count in the summary
        self.assertIn("v1.52.0 shipped: digest split", html)
        # HTML-escaped — the raw markup never survives
        self.assertIn("found &lt;edge&gt; &amp; case", html)
        self.assertNotIn("found <edge> & case", html)
        # still no external assets
        for needle in _EXTERNAL_NEEDLES:
            self.assertNotIn(needle, html)

    def test_board_history_ts_renders_in_local_tz(self):
        # History ts is stored UTC; the board must display it converted to the
        # system local timezone, not the raw stored UTC string (America/New_York
        # is never at +00:00, so this holds regardless of the host's own tz).
        old_tz = os.environ.get("TZ")
        os.environ["TZ"] = "America/New_York"
        time.tzset()
        try:
            t = self._seed("TZ history task")
            t["history"] = [{"ts": "2026-07-02T15:00:00+00:00", "text": "shipped"}]
            ts.save_task(t)
            _, html = self._run_board()
            self.assertNotIn("2026-07-02T15:00:00+00:00", html)
            self.assertIn("2026-07-02T11:00:00-04:00", html)
        finally:
            if old_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old_tz
            time.tzset()

    def test_board_omits_history_when_none(self):
        # A task with no `--log` history renders no history section element (WS6:
        # sec-history is gated on there being history; bare tasks show only Overview).
        self._seed("No history task")
        _, html = self._run_board()
        self.assertNotIn('<details class="sec sec-history"', html)
        self.assertNotIn('<details class="cathistory"', html)

    def test_view_model_carries_history(self):
        # _board_view_model must expose the store's `history` list as [{ts,text}].
        t = self._seed("VM history")
        t["history"] = [{"ts": "t1", "text": "milestone one"}]
        ts.save_task(t)
        vm = ts._board_view_model(ts.load_task(t["id"]))
        self.assertEqual(vm["history"], [{"ts": "t1", "text": "milestone one"}])

    # ----- WS4: board session counts + relation edges -----------------------

    def _seed_related_pair(self):
        """A parent (#363) and a child (#365) spawned from it. WS1's append_related
        is a sibling tree, so the child's `related` edge is built by hand here (the
        exact shape from the plan's New Data Shapes)."""
        parent = self._seed("Parent feature", color="green")
        child = self._seed("Child checkpoint", color="blue")
        parent["seq"] = 363
        child["seq"] = 365
        child["related"] = [{"id": parent["id"], "seq": 363,
                             "kind": "spawned-from", "ts": ts._now()}]
        ts.save_task(parent)
        ts.save_task(child)
        return ts.load_task(parent["id"]), ts.load_task(child["id"])

    def _row_open_tag(self, html, title):
        """The opening <details class="row" …> tag for the row whose data-title
        matches `title` (lowercased) — where data-search lives."""
        m = re.search(r'<details class="row[^>]*data-title="%s"[^>]*>'
                      % re.escape(title.lower()), html)
        return m.group(0) if m else ""

    def test_board_shows_relation_edge_and_session_counts(self):
        # A spawned-from pair: the CHILD row carries `↳ from #363`, and the PARENT's
        # brief detail derives the reverse edge `spawned #365`. A separate task with
        # two hub sessions + one delegate worker shows `2 hubs · 1 worker`.
        self._seed_related_pair()
        s = self._seed("Delegated task with sessions")
        s["seq"] = 500
        s["projects"] = ["someproj"]
        s = self._attach_hub(s, sid="hub-a")
        s = self._attach_hub(s, sid="hub-b")
        reg_prev = ts.DELEGATE_REGISTRY
        ts.DELEGATE_REGISTRY = os.path.join(self.tmp, "workers.json")
        try:
            import json as _json
            with open(ts.DELEGATE_REGISTRY, "w", encoding="utf-8") as f:
                _json.dump({"500:someproj": {"dir": "/tmp/wt", "session_id": "wk-1",
                                             "seq": 500, "model": "opus",
                                             "label": "someproj"}}, f)
            _, html = self._run_board()
        finally:
            ts.DELEGATE_REGISTRY = reg_prev
        # relation refs are now clickable links that open the counterpart's row.
        self.assertIn('↳ from <a class="rellink" href="#task-363">#363</a>', html)
        self.assertIn('spawned <a class="rellink" href="#task-365">#365</a>', html)
        self.assertIn("2 hubs · 1 worker", html)  # now on the Sessions section header
        for needle in _EXTERNAL_NEEDLES:
            self.assertNotIn(needle, html)

    def test_board_relations_searchable(self):
        # The child row's data-search blob folds in the counterpart seq (363) so a
        # relation edge is findable by typing the other task's number.
        parent, child = self._seed_related_pair()
        _, html = self._run_board()
        child_tag = self._row_open_tag(html, "Child checkpoint")
        self.assertTrue(child_tag, "child row must be present")
        m = re.search(r'data-search="([^"]*)"', child_tag)
        self.assertIsNotNone(m)
        self.assertIn("363", m.group(1))
        # the parent's reverse edge (#365) likewise lands in ITS search blob.
        parent_tag = self._row_open_tag(html, "Parent feature")
        pm = re.search(r'data-search="([^"]*)"', parent_tag)
        self.assertIsNotNone(pm)
        self.assertIn("365", pm.group(1))

    def test_board_unchanged_for_bare_tasks(self):
        # A task with no sessions and no relations emits neither the relfrom marker
        # nor the sessions/related brief rows — byte-for-byte the pre-WS4 output.
        self._seed("Plain bare task")
        _, html = self._run_board()
        self.assertNotIn('class="relfrom"', html)
        self.assertNotIn('<span class="k">sessions</span>', html)
        self.assertNotIn('<span class="k">related</span>', html)
        for needle in _EXTERNAL_NEEDLES:
            self.assertNotIn(needle, html)

    def test_view_model_carries_session_tree_and_related(self):
        # _board_view_model exposes the two new keys with the exact shapes; the parent
        # gets a derived incoming edge, the child an outgoing one. Both sides also
        # carry the counterpart's `id` (the key canonical_relations dedups on).
        parent, child = self._seed_related_pair()
        raw = ts.sorted_tasks()
        rev_map = {}
        for t in raw:
            st = ts.task_status(t)
            for r in (t.get("related") or []):
                rev_map.setdefault(r.get("id"), []).append(
                    {"seq": t.get("seq"), "id": t.get("id"), "kind": r.get("kind"),
                     "status": st})
        pvm = ts._board_view_model(parent, rev_map=rev_map)
        cvm = ts._board_view_model(child, rev_map=rev_map)
        self.assertEqual(cvm["related"]["from"],
                         [{"seq": 363, "kind": "spawned-from", "id": parent["id"]}])
        self.assertEqual(cvm["related"]["in"], [])
        self.assertEqual(pvm["related"]["from"], [])
        self.assertEqual(pvm["related"]["in"],
                         [{"seq": 365, "kind": "spawned-from", "status": child["status"],
                           "id": child["id"]}])
        # bare-task counts are all-zero (renderer omits the row).
        self.assertEqual(pvm["session_tree"], {"hubs": 0, "workers": 0, "live_hubs": 0,
                                               "running": 0, "resumable": 0})

    def test_board_related_reverse_scan_without_rev_map(self):
        # Called off the board path (no rev_map), _board_related derives the reverse
        # edge by scanning the supplied task list — the detail/CLI cadence.
        parent, child = self._seed_related_pair()
        rel = ts._board_related(parent, tasks=[parent, child])
        self.assertEqual(rel["from"], [])
        self.assertEqual(rel["in"],
                         [{"seq": 365, "kind": "spawned-from", "status": child["status"],
                           "id": child["id"]}])

    def test_board_empty_store(self):
        path, html = self._run_board()
        self.assertTrue(os.path.exists(path))
        self.assertIn("No tasks yet", html)
        # still no external assets (inline script is fine)
        for needle in _EXTERNAL_NEEDLES:
            self.assertNotIn(needle, html)

    def test_todo_board_routes_through_render(self):
        # `/todo board` → cmd_render writes board.html and announces it with [BOARD].
        import re
        self._seed("Routed via /todo board")
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_render(_Args(arg="board", format="md", session="s1"))
        out = buf.getvalue()
        self.assertIn("[BOARD]", out)
        m = re.search(r"(\S+board\.html)", out)
        self.assertTrue(m and os.path.exists(m.group(1)), "board.html should be written")

    def test_todo_board_listed_in_commands_help(self):
        self.assertTrue(any("/todo board" in c for c, _ in ts._COMMANDS_HELP))

    # ----- redesign (1.16.0): grid · labeled status · summary · resume · help -----

    def test_status_rendered_with_word_labels(self):
        self._seed("An open one")
        self._seed("An in-progress one", status="active")
        self._seed("A finished one", closed=True)
        _, html = self._run_board()
        # Labeled status pills carry the WORD, not a lone glyph. With no live sessions in
        # this render, a stored-open task shows as `new` and a stored-active task shows as
        # `paused` (in progress, no live session); `active` (green) requires a live session.
        self.assertIn('class="pill new"', html)
        self.assertIn('class="pill paused"', html)
        self.assertIn('class="pill closed"', html)
        for word in (">○ new<", ">◐ paused<", ">✕ closed<"):
            self.assertIn(word, html)
        self.assertNotIn(">○ open<", html)            # the stored value is not shown

    def test_summary_in_expanded_detail(self):
        self._seed("Task with a summary")   # _seed writes summary "summary for <title>"
        _, html = self._run_board()
        self.assertIn('class="summary"', html)
        self.assertIn("summary for Task with a summary", html)

    def _attach_worker(self, t, hub_sid="hub-sess", wk_sid="wk-sess",
                       label="acme-repo", dir="/work/repo"):
        """Register a delegate worker for `t` spawned by `hub_sid`, so session_tree
        nests it under that hub (board B12)."""
        ts.DELEGATE_REGISTRY = os.path.join(self.tmp, "workers.json")
        t.setdefault("projects", []).append(label)
        ts.save_task(t)
        with open(ts.DELEGATE_REGISTRY, "w") as f:
            json.dump({"%s:%s" % (t["seq"], label): {
                "seq": t["seq"], "project": label, "label": label, "dir": dir,
                "model": "claude-opus-4-8", "session_id": wk_sid, "ts": ts._now(),
                "spawner": hub_sid}}, f)
        return ts.load_task(t["id"])

    def test_hub_card_with_nested_worker(self):
        # board B12: the hub renders a card; its workers nest under it as a
        # "worker sessions (N)" expandable, each with a nowrap resume command.
        t = self._seed("Delegated task")
        t = self._attach_hub(t)                       # → a hub card
        t = self._attach_worker(t)                    # → a nested worker
        _, html = self._run_board()
        self.assertIn('class="hubcard', html)         # the per-hub card
        self.assertIn("worker sessions (1)", html)    # nested workers section
        self.assertIn('class="wcard"', html)
        self.assertIn("worker:acme-repo", html)
        # resume commands sit on nowrap, scroll-in-place elements.
        self.assertIn('class="cmd" style="white-space:nowrap;overflow-x:auto"', html)

    def test_pinned_hub_badge_not_separate_banner(self):
        # board B11: the pin shows as a distinct pinned badge/highlight on the hub card
        # (no separate banner, no "Resume the hub session" action block, B10).
        t = self._seed("Pinned task")
        t = self._attach_hub(t, sid="pin-sess", preborn=True)
        t["pinned_session"] = "pin-sess"
        ts.save_task(t)
        _, html = self._run_board()
        self.assertNotIn("Resume the hub session", html)       # B10: block dropped
        self.assertNotIn("resumes its pinned session", html)   # old banner copy gone
        self.assertIn('class="hbadge b-pin"', html)            # pinned badge on the card
        self.assertIn("\U0001F4CC pinned", html)               # 📌 pinned
        self.assertIn("hubcard", html)
        self.assertIn("--session-id pin-sess", html)           # preborn resume form

    def test_commands_help_present(self):
        self._seed("Any task")
        _, html = self._run_board()
        self.assertIn("Commands", html)
        self.assertIn("/todo board", html)            # reuses _COMMANDS_HELP
        self.assertIn("/done", html)

    def test_config_help_present(self):
        self._seed("Any task")
        _, html = self._run_board()
        self.assertIn("<h3>Configs</h3>", html)        # 1.39.0: renamed from "Current config"
        self.assertNotIn("Current config", html)
        self.assertIn("theme", html)                  # the --theme row label
        self.assertIn("sands", html)                  # the active theme name (value)

    def test_branding_is_lowercase_task_station(self):
        self._seed("Any task")
        _, html = self._run_board()
        self.assertIn("task-station", html)           # real package name, lowercase
        self.assertNotIn("TASK STATION", html)         # never the shouty form
        self.assertNotIn("Task Station", html)
        self.assertIn("<title>task-station — board</title>", html)

    def test_snapshot_note_present(self):
        self._seed("Any task")
        _, html = self._run_board()
        self.assertIn("snapshot", html.lower())
        self.assertIn("re-run", html)
        self.assertIn("/todo board", html)

    def test_redesigned_board_still_self_contained(self):
        # The richer board (help panel, config table, resume blocks) uses inline
        # JS/CSS but loads NO external assets.
        t = self._seed("Rich task")
        t["projects"] = ["acme-repo"]
        ts.save_task(t)
        self._attach_hub(t)
        _, html = self._run_board()
        for needle in _EXTERNAL_NEEDLES:
            self.assertNotIn(needle, html,
                             "board must have no external assets (found %r)" % needle)

    def test_render_html_directly_on_empty_list(self):
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
        import render_board
        html = render_board.render_html([])
        self.assertIn("No tasks yet", html)
        self.assertIn("</body></html>", html)

    # ----- auto-refresh opt-in (1.17.0) -------------------------------------

    def _render_board_module(self):
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
        import render_board
        return render_board

    def test_autorefresh_off_has_no_meta_and_static_note(self):
        rb = self._render_board_module()
        html = rb.render_html([], board_autorefresh=False)
        self.assertNotIn('http-equiv="refresh"', html)
        self.assertIn("static snapshot", html)
        self.assertIn("re-run", html)

    def test_autorefresh_on_polls_board_rev_no_meta(self):
        # 1.35.0: the opt-in refresh is CHANGE-DRIVEN (no <meta http-equiv>). It loads the
        # board.rev.js <script> sidecar (NOT fetch — file:// Safari/Chrome block local fetch
        # but DO load local scripts) and reloads ONLY when window.__TSREV differs from
        # BOARD_REV (a real data change), setting the ts-auto flag first. A query-404 browser
        # falls back ONCE to a no-query src, then clearInterval. There is NO fetch and NO
        # unconditional timed reload. The live note stays.
        rb = self._render_board_module()
        html = rb.render_html([], board_autorefresh=True, rev="deadbeefcafe0001")
        self.assertNotIn('http-equiv="refresh"', html)         # meta-refresh is GONE
        # the embedded revision const + the <script> sidecar poll comparing __TSREV to it.
        self.assertIn('var BOARD_REV="deadbeefcafe0001";', html)
        self.assertIn("createElement('script')", html)         # a <script> is appended
        self.assertIn("board.rev.js", html)                    # the local sidecar src
        self.assertIn("'board.rev.js?t='+(rc++)", html)        # cache-busted query form
        self.assertIn("window.__TSREV", html)                  # compares the sidecar value
        self.assertIn("v!==BOARD_REV", html)
        self.assertIn("sessionStorage.setItem('ts-auto','1')", html)
        self.assertIn("location.reload()", html)               # reload ON mismatch
        # the no-query fallback then give-up: triedPlain switches the src, a second error
        # clearInterval-s (degrade to static snapshot, NEVER a blind reload).
        self.assertIn("triedPlain", html)
        self.assertIn("clearInterval(pollId)", html)

    def test_autorefresh_poll_is_gentle(self):
        # 1.36.0: the poll is GENTLE so Safari's local-file loading bar stops flashing.
        # The interval is now ~10s (NOT 2s), the page does NOT poll immediately on load,
        # and a tick is SKIPPED (does nothing) while the tab is hidden, the window is
        # unfocused, or the user interacted within the last ~2500ms (passive activity
        # listeners stamp lastAct). The reload-on-real-change behaviour is unchanged.
        rb = self._render_board_module()
        html = rb.render_html([], board_autorefresh=True, rev="deadbeefcafe0001")
        # slow cadence — 10s, NOT the old 2s.
        self.assertIn("setInterval(poll,10000)", html)
        self.assertNotIn("setInterval(poll,2000)", html)
        # does NOT poll immediately on load (no trailing poll() after setInterval).
        self.assertNotIn("setInterval(poll,10000);poll()", html)
        # the gate that skips a tick: hidden / unfocused / recent activity.
        self.assertIn("document.hidden", html)
        self.assertIn("document.hasFocus", html)
        self.assertIn("Date.now()-lastAct<2500", html)
        # activity tracking: lastAct stamped by passive listeners. 1.40.0: lastAct now
        # INITIALISES to Date.now() (not 0) so the first input after load isn't mistaken
        # for a return-from-idle.
        self.assertNotIn("lastAct=0", html)
        self.assertIn("lastAct=Date.now()", html)
        for evt in ("'mousemove'", "'scroll'", "'keydown'", "'touchstart'", "'wheel'"):
            self.assertIn(evt, html)
        # NO fetch, NO unconditional timed reload (the old blind-reload fallback is gone).
        self.assertNotIn("fetch(", html)
        self.assertNotIn("},5000)", html)
        self.assertNotIn("fellBack", html)
        # 1.38.0: the autorefresh-ON footer note was removed entirely.
        self.assertNotIn("--board-autorefresh off", html)
        self.assertNotIn("static snapshot", html)
        # still NO external assets (inline JS + the local board.rev.js sidecar are ok).
        for needle in _EXTERNAL_NEEDLES:
            self.assertNotIn(needle, html)

    def test_autorefresh_off_has_no_poll_or_timer(self):
        # 1.35.0: with autorefresh OFF there is no poll, no script sidecar load → no
        # AUTOMATIC reload. BOARD_REV is still embedded (harmless). (The explicit Refresh
        # button — a manual user action — is always present and is asserted separately.)
        rb = self._render_board_module()
        html = rb.render_html([], board_autorefresh=False, rev="abc123")
        self.assertNotIn("fetch(", html)
        self.assertNotIn("setInterval(poll", html)
        self.assertNotIn("board.rev.js", html)
        self.assertIn('var BOARD_REV="abc123";', html)

    def test_autorefresh_off_has_no_js_reload_timer(self):
        # 1.32.0 A: with autorefresh OFF there is no TIMER → no automatic reload (the only
        # reload is the user-driven Refresh button, asserted in the refresh-button test).
        rb = self._render_board_module()
        html = rb.render_html([], board_autorefresh=False)
        self.assertNotIn('http-equiv="refresh"', html)
        self.assertNotIn("setInterval(poll", html)

    def test_autorefresh_on_refreshes_on_return_from_inactivity(self):
        # 1.40.0: the change-check is factored into a reusable no-guard loadRev(); coming
        # back to the board after being inactive checks + reloads-if-changed immediately.
        rb = self._render_board_module()
        html = rb.render_html([], board_autorefresh=True, rev="deadbeefcafe0001")
        # loadRev() is defined and is the function that appends the rev <script>.
        self.assertIn("function loadRev(){", html)
        self.assertIn("createElement('script')", html)
        # poll() (the gentle interval tick) keeps the SAME hold gate, then calls loadRev().
        self.assertIn("Date.now()-lastAct<2500))return;loadRev();}", html)
        # bump() is now a return-from-idle detector: first input after an idle gap > IDLE_MS
        # (and not hidden) triggers loadRev(); continuous activity (small `was`) does not.
        self.assertIn("IDLE_MS=10000", html)
        self.assertIn("function bump(){var was=Date.now()-lastAct;lastAct=Date.now();", html)
        self.assertIn("if(was>IDLE_MS&&!document.hidden){loadRev();}}", html)
        # lastAct INITIALISES to now (not 0) so first post-load input isn't a "return".
        self.assertIn("lastAct=Date.now(),IDLE_MS=10000", html)
        self.assertNotIn("lastAct=0", html)
        # return-to-tab/window: visibilitychange→visible and window focus each loadRev().
        self.assertIn("addEventListener('visibilitychange'", html)
        self.assertIn("document.visibilityState==='visible'){lastAct=Date.now();loadRev();}", html)
        self.assertIn("window.addEventListener('focus',function(){lastAct=Date.now();loadRev();})", html)
        # gentle idle poll stays as the fallback.
        self.assertIn("setInterval(poll,10000)", html)
        # reload is STILL gated on a real rev change + sets ts-auto for state restore.
        self.assertIn("v!==BOARD_REV", html)
        self.assertIn("sessionStorage.setItem('ts-auto','1')", html)
        self.assertIn("location.reload()", html)
        # still NO external assets.
        for needle in _EXTERNAL_NEEDLES:
            self.assertNotIn(needle, html)

    def test_autorefresh_off_has_no_return_refresh_wiring(self):
        # 1.40.0: with autorefresh OFF none of the loadRev/return-from-idle/visibility/focus
        # wiring is emitted (no poll, no return-refresh at all).
        rb = self._render_board_module()
        html = rb.render_html([], board_autorefresh=False, rev="abc123")
        self.assertNotIn("loadRev", html)
        self.assertNotIn("IDLE_MS", html)
        # the always-present scroll-save visibilitychange handler (visibilityState==='hidden')
        # stays; only the refresh-specific visible→loadRev variant must be absent.
        self.assertNotIn("visibilityState==='visible'", html)
        self.assertNotIn("addEventListener('focus'", html)
        self.assertNotIn("setInterval(poll", html)
        self.assertNotIn("board.rev.js", html)

    # ----- footer version / last-updated (1.34.0) ---------------------------

    def test_footer_shows_version_and_updated_before_note(self):
        # render_html prefixes the .snapshot note with "task-station v<v> · updated <t> · ".
        rb = self._render_board_module()
        html = rb.render_html([], version="1.34.0", updated="2026-06-29 14:30")
        self.assertIn("task-station v1.34.0", html)
        self.assertIn("updated 2026-06-29 14:30", html)
        # 1.39.0: the footer is stacked <div>s; the version/updated (+ note) is the FIRST
        # line (line1). It still leads with the version/updated prefix, BEFORE the note.
        snap = html.split('<div class="snapshot"><div>', 1)[1].split("</div>", 1)[0]
        self.assertTrue(snap.startswith("task-station v1.34.0 · updated 2026-06-29 14:30 · "))
        self.assertLess(snap.index("task-station v1.34.0"), snap.index("snapshot"))

    def test_footer_version_on_both_refresh_states(self):
        # version/updated show in BOTH refresh states. 1.38.0: the autorefresh-ON note was
        # removed, so the live footer is JUST the version/updated with NO trailing " · ";
        # the static (off) footer keeps the version/updated prefix + the static note.
        rb = self._render_board_module()
        static = rb.render_html([], version="1.34.0", updated="2026-06-29 14:30",
                                board_autorefresh=False)
        live = rb.render_html([], version="1.34.0", updated="2026-06-29 14:30",
                              board_autorefresh=True, rev="abc")
        static_snap = static.split('<div class="snapshot"><div>', 1)[1].split("</div>", 1)[0]
        live_snap = live.split('<div class="snapshot"><div>', 1)[1].split("</div>", 1)[0]
        # static: prefix + the static note follows.
        self.assertTrue(static_snap.startswith("task-station v1.34.0 · updated 2026-06-29 14:30 · "))
        self.assertIn("static snapshot", static_snap)
        # live: version/updated ONLY, no trailing separator, no note.
        self.assertEqual(live_snap, "task-station v1.34.0 · updated 2026-06-29 14:30")
        self.assertNotIn("updates automatically when a task changes", live)

    def test_footer_no_prefix_when_empty(self):
        # empty version/updated → no prefix; the footer is the note alone.
        rb = self._render_board_module()
        html = rb.render_html([], version="", updated="")
        snap = html.split('<div class="snapshot"><div>', 1)[1].split("</div>", 1)[0]
        self.assertNotIn("task-station v", snap)
        self.assertNotIn("updated ", snap)
        self.assertTrue(snap.startswith("this board is a static snapshot"))

    def test_footer_version_escaped_note_markup_intact(self):
        # the version/updated text is _e-escaped; the note's intended <code> markup is NOT
        # double-escaped (still renders as a real tag), and no external assets sneak in.
        rb = self._render_board_module()
        html = rb.render_html([], version="<x>", updated="2026 & on",
                              board_autorefresh=False)
        snap = html.split('<div class="snapshot"><div>', 1)[1].split("</div>", 1)[0]
        self.assertIn("task-station v&lt;x&gt;", snap)        # version escaped
        self.assertIn("updated 2026 &amp; on", snap)          # updated escaped
        self.assertIn("<code>", snap)                         # note markup preserved
        for needle in _EXTERNAL_NEEDLES:
            self.assertNotIn(needle, html)

    def test_footer_autorefresh_on_has_no_note(self):
        # 1.38.0: with autorefresh ON the footer carries NO note text — the Refresh button
        # + "refreshed" timestamp cover it. Just version + updated, no trailing " · ".
        rb = self._render_board_module()
        html = rb.render_html([], version="1.38.0", updated="2026-06-29 14:30",
                              board_autorefresh=True, rev="abc")
        snap = html.split('<div class="snapshot"><div>', 1)[1].split("</div>", 1)[0]
        self.assertNotIn("updates automatically when a task changes", snap)
        self.assertNotIn("--board-autorefresh off", snap)
        self.assertIn("task-station v1.38.0", snap)
        self.assertIn("updated 2026-06-29 14:30", snap)
        self.assertEqual(snap, "task-station v1.38.0 · updated 2026-06-29 14:30")
        self.assertFalse(snap.endswith(" · "))                 # no dangling separator

    def test_footer_autorefresh_off_keeps_static_note(self):
        # 1.38.0: the static (autorefresh OFF) note is unchanged.
        rb = self._render_board_module()
        html = rb.render_html([], version="1.38.0", updated="2026-06-29 14:30",
                              board_autorefresh=False)
        snap = html.split('<div class="snapshot"><div>', 1)[1].split("</div>", 1)[0]
        self.assertIn("this board is a static snapshot", snap)
        self.assertIn("<code>", snap)
        self.assertTrue(snap.startswith("task-station v1.38.0 · updated 2026-06-29 14:30 · "))

    def test_footer_repo_link_on_own_line(self):
        # 1.39.0: the footer shows the GitHub repo URL on its OWN line, BELOW the
        # version/updated line. href is the full url; the visible text drops the scheme.
        rb = self._render_board_module()
        url = "https://github.com/ryanconmeo/task-station"
        # autorefresh on → line1 is just version/updated (no static-snapshot note appended).
        html = rb.render_html([], version="1.39.0", updated="t", repo_url=url, board_autorefresh=True)
        # version/updated is the first stacked <div>; the repo link is a SEPARATE div after.
        self.assertIn('<div class="snapshot"><div>task-station v1.39.0 · updated t</div>', html)
        self.assertIn('<div class="repo"><a href="%s" target="_blank" rel="noopener noreferrer">'
                      'github.com/ryanconmeo/task-station</a></div>' % url, html)
        # the repo line sits BELOW (after) the version/updated line.
        self.assertLess(html.index("task-station v1.39.0 · updated t"),
                        html.index('<div class="repo">'))
        # href is the full url; the visible link TEXT carries no scheme.
        self.assertIn('href="%s"' % url, html)
        self.assertNotIn(">https://github.com/ryanconmeo/task-station<", html)
        # CSS: link on its own line + accent-coloured.
        self.assertIn(".snapshot .repo{margin-top:4px}", html)
        self.assertIn(".snapshot a{color:var(--accent)}", html)

    def test_footer_no_repo_line_when_empty(self):
        # repo_url="" → no repo line at all.
        rb = self._render_board_module()
        html = rb.render_html([], version="1.39.0", updated="t", repo_url="")
        self.assertNotIn('<div class="repo">', html)
        self.assertNotIn("github.com/ryanconmeo", html)

    def test_footer_repo_link_is_not_an_external_asset(self):
        # The GitHub <a href="https://…"> is a hyperlink the user clicks, NOT a loaded
        # asset — it must not trip the no-external-assets needles.
        rb = self._render_board_module()
        html = rb.render_html([], version="1.39.0", updated="t",
                              repo_url="https://github.com/ryanconmeo/task-station")
        for needle in _EXTERNAL_NEEDLES:
            self.assertNotIn(needle, html,
                             "repo link must not be an external asset (found %r)" % needle)

    def test_footer_repo_link_opens_in_new_tab(self):
        # 1.40.0: the footer GitHub link opens in a NEW TAB so clicking it does not
        # navigate the board away — target="_blank" + rel="noopener noreferrer".
        rb = self._render_board_module()
        url = "https://github.com/ryanconmeo/task-station"
        html = rb.render_html([], version="1.40.0", updated="t", repo_url=url)
        self.assertIn('<div class="repo"><a href="%s" target="_blank" '
                      'rel="noopener noreferrer">' % url, html)
        # still scheme-stripped visible text + full-url href; still NOT an external asset.
        self.assertIn('>github.com/ryanconmeo/task-station</a>', html)
        for needle in _EXTERNAL_NEEDLES:
            self.assertNotIn(needle, html)

    def test_write_board_footer_links_to_repo(self):
        # write_board reads the repo URL from plugin.json (homepage, else repository)
        # and renders it as the footer GitHub link.
        import json as _json
        pj = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          ".claude-plugin", "plugin.json")
        with open(pj, encoding="utf-8") as f:
            data = _json.load(f)
        url = data.get("homepage") or data.get("repository")
        text = url.replace("https://", "").replace("http://", "")
        self._seed("Repo-linked board task")
        _, html = self._run_board()
        self.assertIn('<div class="repo"><a href="%s" target="_blank" rel="noopener noreferrer">'
                      '%s</a></div>' % (url, text), html)

    def test_write_board_footer_carries_plugin_version(self):
        # write_board reads the real plugin.json version + mtime and renders them.
        import json as _json
        pj = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          ".claude-plugin", "plugin.json")
        with open(pj, encoding="utf-8") as f:
            ver = _json.load(f)["version"]
        self._seed("Versioned board task")
        _, html = self._run_board()
        self.assertIn("task-station v%s" % ver, html)
        self.assertIn("updated ", html)

    def test_write_board_picks_up_autorefresh_config(self):
        self._seed("Live board task")
        os.environ["TASK_STATION_BOARD_AUTOREFRESH"] = "on"
        try:
            _, html = self._run_board()
        finally:
            os.environ.pop("TASK_STATION_BOARD_AUTOREFRESH", None)
        self.assertNotIn('http-equiv="refresh"', html)
        # change-driven: loads the board.rev.js <script> sidecar; no fetch, no timed reload.
        self.assertIn("board.rev.js", html)
        self.assertIn("createElement('script')", html)
        self.assertNotIn("fetch(", html)
        # 1.38.0: the autorefresh-ON footer note was removed entirely.
        self.assertNotIn("updates automatically when a task changes", html)
        self.assertNotIn("auto-refreshing every 5s", html)

    def test_refresh_if_live_no_flag_does_nothing(self):
        # Flag OFF + no board.html → the Stop-hook path must NOT create the file.
        os.environ.pop("TASK_STATION_BOARD_AUTOREFRESH", None)
        self._seed("Quiet task")
        ts.cmd_board(_Args(refresh_if_live=True))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "board.html")))

    def test_refresh_if_live_flag_on_but_no_existing_file_does_nothing(self):
        # Flag ON but the user never opened the board → do NOT create it.
        self._seed("Never-opened task")
        os.environ["TASK_STATION_BOARD_AUTOREFRESH"] = "on"
        try:
            ts.cmd_board(_Args(refresh_if_live=True))
            self.assertFalse(os.path.exists(os.path.join(self.tmp, "board.html")))
        finally:
            os.environ.pop("TASK_STATION_BOARD_AUTOREFRESH", None)

    def test_refresh_if_live_regens_existing_when_on(self):
        self._seed("Opened task")
        self._run_board()  # creates board.html (snapshot, flag still off)
        path = os.path.join(self.tmp, "board.html")
        self.assertTrue(os.path.exists(path))
        os.environ["TASK_STATION_BOARD_AUTOREFRESH"] = "on"
        try:
            ts.cmd_board(_Args(refresh_if_live=True))
            with open(path, encoding="utf-8") as f:
                html = f.read()
        finally:
            os.environ.pop("TASK_STATION_BOARD_AUTOREFRESH", None)
        # regenerated WITH the change-driven script-sidecar poll now that the flag is on
        # (no meta-refresh, no fetch, no timed reload).
        self.assertNotIn('http-equiv="refresh"', html)
        self.assertIn("board.rev.js", html)
        self.assertIn("createElement('script')", html)

    # ----- digestible summary (1.17.0) --------------------------------------

    def test_digest_appears_before_summary(self):
        t = self._seed("Briefing-first task")
        t["state"] = "next: ship the digest"
        ts.save_task(t)
        _, html = self._run_board()
        self.assertIn('class="brief"', html)
        self.assertIn('class="summary"', html)
        self.assertLess(html.index('class="brief"'), html.index('class="summary"'),
                        "the at-a-glance digest must come before the full summary")

    def test_summary_has_scroll_capped_container(self):
        self._seed("Long summary task")
        _, html = self._run_board()
        self.assertIn("max-height:16em", html)
        self.assertIn("overflow-y:auto", html)

    def test_summary_rendered_as_markdown(self):
        t = self._seed("Markdown summary task")
        t["summary"] = "## Heading\n\n- one\n- two\n\nsee **bold** and `code` and https://ex.com/p"
        ts.save_task(t)
        _, html = self._run_board()
        self.assertIn("<h2>Heading</h2>", html)
        self.assertIn("<li>one</li>", html)
        self.assertIn("<strong>bold</strong>", html)
        self.assertIn("<code>code</code>", html)
        self.assertIn('<a href="https://ex.com/p">', html)

    # ----- board UX overhaul (1.19.0) ---------------------------------------

    def test_full_title_in_expanded_detail(self):
        # req 1: the expanded detail shows the FULL, untruncated title prominently.
        long_title = "A very long task title that the collapsed row would truncate hard"
        self._seed(long_title)
        _, html = self._run_board()
        self.assertIn('<div class="fulltitle">' + long_title + "</div>", html)

    def test_theme_toggle_persists_with_both_palettes(self):
        # req 4: a visible toggle, BOTH palettes embedded, persisted to localStorage.
        self._seed("Themed task")
        _, html = self._run_board()
        self.assertIn('id="theme-toggle"', html)               # visible toggle control
        self.assertIn("localStorage", html)                    # persistence
        self.assertIn("ts-board-theme", html)                  # the persisted key
        # both palettes embedded as CSS-variable sets switchable via data-theme
        self.assertIn('html[data-theme="dark"]{', html)
        self.assertIn('html[data-theme="light"]{', html)
        self.assertIn("#0d0e11", html)                         # dark page bg
        self.assertIn("#f3efe7", html)                         # light page bg
        # no external assets despite the inline JS/CSS
        for needle in _EXTERNAL_NEEDLES:
            self.assertNotIn(needle, html)

    def test_refresh_button_in_kicker_right_group_and_wired(self):
        # 1.37.0: the explicit Refresh button moved UP into the top kicker, sitting to the
        # RIGHT of the "refreshed <ts>" label inside the .kright group (no longer beside the
        # theme toggle in the header). It is a light kicker-styled text button (.krefresh),
        # always present, and its click still sets the ts-auto flag (so the reload restores
        # open rows / scroll / filters via the isAuto path) then location.reload().
        rb = self._render_board_module()
        html = rb.render_html([], generated="GEN_TS")
        self.assertIn('id="board-refresh"', html)              # the button exists
        self.assertIn("↻ refresh", html)                  # the ↻ refresh label
        self.assertIn('aria-label="Refresh the board"', html)
        # the button uses the light kicker text-button class, NOT the boxy .toggle chrome.
        self.assertIn('id="board-refresh" class="krefresh"', html)
        self.assertIn(".krefresh{font-family:var(--mono);font-size:11px;", html)  # the CSS rule
        self.assertIn(".kright{display:flex;align-items:center;gap:10px}", html)  # the group CSS
        # the .kright group holds BOTH the "refreshed <ts>" label AND the Refresh button,
        # in that order (label on the left, button on its right).
        kr = re.search(r'<span class="kright">(.*?)</span></div>', html, re.S)
        self.assertIsNotNone(kr)
        group = kr.group(1)
        self.assertIn('<span class="kgen">refreshed GEN_TS</span>', group)
        self.assertIn('id="board-refresh"', group)
        self.assertLess(group.index("kgen"), group.index("board-refresh"))  # label before button
        # the Refresh button is NOT in the header — it lives in the kicker. The header's
        # .hdrbtns group holds the perf + theme toggles (the board's two global prefs).
        hdr = re.search(r'<div class="hdr">.*?<p class="lede"', html, re.S)
        self.assertIsNotNone(hdr)
        self.assertNotIn("board-refresh", hdr.group(0))
        self.assertIn('id="theme-toggle"', hdr.group(0))       # theme toggle stays in .hdr
        self.assertIn('id="perf-toggle"', hdr.group(0))        # perf toggle sits beside it
        # wired: grab #board-refresh, set ts-auto, reload.
        self.assertIn("getElementById('board-refresh')", html)
        self.assertIn("sessionStorage.setItem('ts-auto','1')", html)
        self.assertIn("location.reload()", html)
        for needle in _EXTERNAL_NEEDLES:
            self.assertNotIn(needle, html)

    def test_refresh_button_present_even_without_timestamp(self):
        # 1.37.0: the Refresh button is ALWAYS present, even when there's no "refreshed <ts>"
        # label (generated empty → no .kgen, but the button still sits in the .kright group).
        rb = self._render_board_module()
        html = rb.render_html([], generated="")
        self.assertNotIn('class="kgen"', html)                 # no timestamp label
        self.assertIn('<span class="kright">', html)           # group still rendered
        self.assertIn('id="board-refresh" class="krefresh"', html)

    def test_expanded_row_has_distinct_background(self):
        # req 5: details.row[open] gets a distinct background AND a top+bottom accent
        # boundary so the whole expanded card is set apart from its neighbours.
        self._seed("Expandable task")
        _, html = self._run_board()
        self.assertIn("details.row[open]{background:var(--open);"
                      "border-top:1px solid var(--accent);"
                      "border-bottom:1px solid var(--accent)}", html)
        self.assertIn("--open:#23272f", html)                  # dark variant open bg
        self.assertIn("--open:#e3dccb", html)                  # light variant open bg
        self.assertNotIn("--open:#0d0e11", html)               # not the (dark) page colour

    def test_left_border_is_curated_category_highlight(self):
        # 1.21.0 req B: the left stripe is the CURATED category highlight (--cat-stripe),
        # distinct + true-to-name; the raw bg is kept (--cat-bg) for back-compat.
        rb = self._render_board_module()
        self._seed("Green task", color="green")
        _, html = self._run_board()
        self.assertIn("border-left-color:var(--cat-stripe", html)   # driven by --cat-stripe
        # the curated green highlight for each variant appears in that category's rule.
        self.assertIn(".cat-green{--cat-bg:#1c2a16;--cat-stripe:%s"
                      % rb.category_highlight("green", "dark"), html)
        self.assertIn(".cat-green{--cat-bg:#233a2b;--cat-stripe:%s"
                      % rb.category_highlight("green", "light"), html)
        self.assertIn("--cat-accent:#b6e85a", html)                 # bold is the ACCENT

    def test_prs_each_on_own_line_with_desc(self):
        # req 5: each PR on its own line; the linked url then its description.
        t = self._seed("PR task")
        ts.add_pr(t, "https://github.com/o/r/pull/1", "first fix")
        ts.add_pr(t, "https://github.com/o/r/pull/2", "second fix")
        ts.save_task(t)
        _, html = self._run_board()
        self.assertIn('class="prs"', html)
        self.assertEqual(html.count('<div class="pr">'), 2)    # one line per PR
        self.assertIn('href="https://github.com/o/r/pull/1"', html)
        self.assertIn('href="https://github.com/o/r/pull/2"', html)
        self.assertIn("first fix", html)
        self.assertIn("second fix", html)
        self.assertIn('<span class="d">— first fix</span>', html)

    def test_stories_each_on_own_line_with_desc(self):
        # req 2: the Stories block mirrors PRs — each story on its own line, the
        # linked url then its description when present.
        t = self._seed("Story task")
        ts.add_story(t, "https://dev.azure.com/Org/Proj/_workitems/edit/11", "first story")
        ts.add_story(t, "https://dev.azure.com/Org/Proj/_workitems/edit/22")
        ts.save_task(t)
        _, html = self._run_board()
        self.assertIn('class="stories"', html)
        # two stories → two lines (the inner line class is reused from PRs)
        self.assertIn('href="https://dev.azure.com/Org/Proj/_workitems/edit/11"', html)
        self.assertIn('href="https://dev.azure.com/Org/Proj/_workitems/edit/22"', html)
        self.assertIn('<span class="d">— first story</span>', html)
        # the Stories block sits after the PRs block (near PRs), before files
        self.assertIn('<span class="k">stories</span>', html)

    def test_open_summary_header_distinct_not_separated_from_detail(self):
        # req 5: an expanded row's header is distinct but NOT separated from its detail —
        # the open summary shares the detail's --open background, with NO internal 2px
        # accent divider and NO panel2 background (header + content read as one card).
        self._seed("Any task")
        _, html = self._run_board()
        self.assertIn("details.row[open]>summary.rowsum{background:var(--open)}", html)
        # the old panel2 background + 2px accent divider on the open summary are GONE.
        self.assertNotIn("details.row[open]>summary.rowsum{background:var(--panel2);"
                         "border-bottom:2px solid var(--accent)}", html)
        self.assertNotIn("border-bottom:2px solid var(--accent)", html)
        # the detail body keeps the --open background — the header now matches it.
        self.assertIn(".detail{padding:14px 16px 16px;display:grid;gap:13px;"
                      "background:var(--open)}", html)

    def test_task_number_more_pronounced(self):
        # req 4: the # cell uses --ink (not the faded --dim) + a touch heavier.
        self._seed("Any task")
        _, html = self._run_board()
        self.assertIn(".c-seq{font-family:var(--mono);font-size:12px;"
                      "color:var(--ink);font-weight:600}", html)
        self.assertNotIn(".c-seq{font-family:var(--mono);font-size:12px;color:var(--dim)}", html)

    def test_category_rows_inline_flow_pill_content_sized(self):
        # 1.24.0: each Categories row is an INLINE FLOW — the pill is sized to its OWN
        # content (no uniform-width stretch), the description is plain text never
        # truncated, and the counts are pushed to the far right.
        self._seed("Green one", color="green")
        self._seed("A bug", color="red")
        _, html = self._run_board()
        # vertical flex list; each row a flex row (no grid / display:contents).
        self.assertIn(".catlist{display:flex;flex-direction:column;gap:8px", html)
        self.assertIn(".catitem{display:flex;align-items:center;gap:8px}", html)
        self.assertNotIn("grid-template-columns:max-content max-content auto", html)
        self.assertNotIn(".catitem{display:contents}", html)
        self.assertNotIn("justify-self:stretch", html)         # no stretch anymore
        # the pill is content-sized (inline-flex), not stretched.
        self.assertIn(".catitem .cchip{display:inline-flex;align-items:center;", html)
        # the description (label) is plain UNCOLOURED text, never clipped.
        self.assertIn(".catitem .clabel{min-width:0;color:var(--ink);opacity:.94}", html)
        self.assertNotIn("text-overflow:ellipsis;white-space:nowrap;opacity:.94", html)
        # 1.29.0 (req 2): counts follow a FIXED-width .catleft so they align across rows
        # (no margin-left float — neither :auto nor :14px).
        self.assertIn(".catitem .ccount{font-family:var(--mono);font-size:12px;", html)
        self.assertNotIn(".catitem .ccount{margin-left:auto;", html)
        self.assertNotIn(".catitem .ccount{margin-left:14px;", html)
        # the fixed-width left group is present.
        self.assertIn(".catitem .catleft{flex:none;display:flex;gap:8px;"
                      "align-items:center;white-space:nowrap}", html)

    def test_open_command_present_above_hub_cards(self):
        # board B10: the /todo <seq> OPEN action stays (the redundant resume-hub block
        # was dropped); it sits above the per-hub cards, which carry the resume commands.
        t = self._seed("Open vs resume task")
        self._attach_hub(t)
        _, html = self._run_board()
        self.assertIn("/todo %s" % t["seq"], html)             # the open command
        self.assertIn("Open the task", html)                   # labeled
        self.assertNotIn("Resume the hub session", html)       # B10: dropped
        self.assertIn('class="hubcard', html)                  # the hub card (with resume cmd)
        self.assertIn('class="cmd" style="white-space:nowrap;overflow-x:auto"', html)
        # the open action sits before the hub cards.
        self.assertLess(html.index("Open the task"), html.index('class="hubcard'))

    def test_heading_and_kicker(self):
        # 1.24.0: the <h1> is the COMMAND ("/todo board"); the kicker keeps
        # "claude code • task-station" on the LEFT (in .kleft, with a dimmed "•"
        # separator) but is now SPACE-BETWEEN so the generated timestamp sits at the
        # top-RIGHT (.kgen). <title> unchanged.
        self._seed("Any task")
        _, html = self._run_board()
        self.assertIn("<h1>/todo board</h1>", html)            # heading is the command now
        self.assertNotIn("<h1>task board</h1>", html)          # old heading gone
        # the left group holds both words with the separator span between them.
        self.assertIn('<div class="kicker"><span class="kleft"><span>claude code</span>'
                      '<span class="ksep">•</span><span>task-station</span></span>', html)
        self.assertIn("claude code", html)
        self.assertIn("task-station", html)
        # space-between now (was flex-start), with the .kleft / .kgen helpers.
        self.assertIn(".kicker{display:flex;justify-content:space-between", html)
        self.assertNotIn(".kicker{display:flex;justify-content:flex-start", html)
        self.assertIn(".kleft{display:flex;gap:8px;align-items:center}", html)
        self.assertIn(".kgen{color:var(--dim)}", html)
        self.assertIn(".ksep{opacity:.5}", html)               # dimmed separator
        self.assertNotIn("task-station · /todo board", html)   # old kicker form gone
        # _run_board passes a real timestamp → it shows top-right in the kicker, now
        # labeled "refreshed" (1.36.0 — the value is the write-board time).
        self.assertIn('class="kgen">refreshed ', html)
        self.assertNotIn('class="kgen">generated ', html)      # old "generated" label gone
        # <title> stays the package form, unaffected.
        self.assertIn("<title>task-station — board</title>", html)

    def test_lede_wording_trimmed_with_oxford_comma(self):
        # req 4/5: the lede drops the "same Open / Closed grid as the terminal task
        # board;" clause and ends with an OXFORD COMMA ("commands, and briefing").
        self._seed("Any task")
        _, html = self._run_board()
        self.assertNotIn("same Open / Closed grid", html)      # comparison clause gone
        self.assertIn("commands, and briefing", html)          # oxford comma present
        self.assertNotIn("commands and briefing", html)        # un-oxford form gone
        self.assertIn("expand any row for its full title, summary, "
                      "open/resume commands, and briefing.", html)

    def test_board_message_says_task_board(self):
        # 1.20.0 req 5: the [BOARD] CLI message references "task board".
        self._seed("Routed task")
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_render(_Args(arg="board", format="md", session="s1"))
        out = buf.getvalue()
        self.assertIn("[BOARD]", out)
        self.assertIn("task board", out)
        self.assertNotIn("/todo board", out)

    def test_hover_autoscroll_script_present(self):
        # req 2: an inline script wires hover auto-scroll on the title cells.
        self._seed("Scrolling title task")
        _, html = self._run_board()
        self.assertIn("mouseenter", html)
        self.assertIn("mouseleave", html)
        self.assertIn("scrollLeft", html)
        self.assertIn(".c-task .ttl", html)                    # the scroll target

    def test_summary_raw_html_is_escaped(self):
        t = self._seed("Injection attempt")
        t["summary"] = "danger <script>alert(1)</script> end"
        ts.save_task(t)
        _, html = self._run_board()
        # the INJECTED markup must be escaped (inert), even though the page now
        # carries its own inline <script> for the toggle/scroll.
        self.assertNotIn("<script>alert(1)", html)        # not a live injected tag
        self.assertNotIn("alert(1)", html.replace("&lt;script&gt;alert(1)&lt;/script&gt;", ""))
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)   # inert as text

    # ----- second board UX pass (1.20.0) ------------------------------------

    @staticmethod
    def _lightness(hexstr):
        import colorsys
        h = hexstr.lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
        return colorsys.rgb_to_hls(r, g, b)[1]

    def test_brighten_returns_lighter_valid_hex(self):
        # req 1: brighten() raises a dark category colour into a lighter, valid hex.
        rb = self._render_board_module()
        for src in ("#1c2a16", "#141d2e", "#2c1518", "#0d1b4b"):
            out = rb.brighten(src)
            self.assertRegex(out, r"^#[0-9a-f]{6}$")           # valid 6-digit hex
            self.assertGreater(self._lightness(out), self._lightness(src))  # lighter

    def test_stripe_uses_curated_highlight_both_variants(self):
        # req B: the left stripe is driven by --cat-stripe (the curated highlight),
        # emitted for BOTH variants; the raw bg is still kept (--cat-bg).
        rb = self._render_board_module()
        self._seed("Green task", color="green")
        _, html = self._run_board()
        self.assertIn("border-left-color:var(--cat-stripe", html)
        self.assertIn(".cat-green{--cat-bg:#1c2a16;--cat-stripe:%s"
                      % rb.category_highlight("green", "dark"), html)
        self.assertIn(".cat-green{--cat-bg:#233a2b;--cat-stripe:%s"
                      % rb.category_highlight("green", "light"), html)

    def test_hover_scroll_is_linear_and_resets(self):
        # B1 (rewrite): hover marquee is a LINEAR text-indent transition (no easing) and
        # snaps back to the start INSTANTLY on mouse-out; the full title shows while it
        # slides. The text-indent mechanism avoids the scrollLeft/overflow-x quirks.
        self._seed("A really long scrolling title that overflows the task column hard")
        _, html = self._run_board()
        self.assertIn("mouseenter", html)
        self.assertIn("mouseleave", html)
        self.assertIn("linear", html)                # linear transition, no easing curve
        self.assertNotIn("Math.pow", html)           # NO easing curve
        self.assertIn("el.style.textIndent=(-d)+'px'", html)   # slide left by the overflow
        self.assertIn("el.style.textIndent='0px'", html)       # instant snap-back on leave
        self.assertIn("transitionDuration='0s'", html)         # ...with no return animation
        self.assertIn("text-overflow:clip", html)    # ellipsis dropped while scrolling
        self.assertIn("'scrolling'", html)

    def test_hover_scroll_measures_overflow_and_binds_on_row(self):
        # B1: startScroll measures the overflow (scrollWidth-clientWidth) and bails when
        # the title fits; the enter/leave binding attaches on the WHOLE row summary so
        # hovering ANYWHERE in the row slides the title.
        self._seed("A really long scrolling title that overflows the task column hard")
        _, html = self._run_board()
        self.assertIn("var d=el.scrollWidth-el.clientWidth;if(d<=2)return;", html)
        self.assertIn("document.querySelectorAll('.c-task .ttl')", html)
        self.assertIn("el.closest('summary.rowsum')", html)
        self.assertIn("host.addEventListener('mouseenter'", html)
        self.assertIn("host.addEventListener('mouseleave'", html)

    def test_hub_card_carries_resume_command(self):
        # board B10/B12: the hub's resume command lives on its per-hub card (the standalone
        # "Resume the hub session" action was dropped).
        t = self._seed("Hub task")
        self._attach_hub(t)
        _, html = self._run_board()
        self.assertNotIn("Resume the hub session", html)
        self.assertIn('class="hubcard', html)
        self.assertIn("claude", html)                 # a resume command on the card

    def test_worker_sessions_nested_under_hub(self):
        # board B12: workers nest under their spawning hub as a "worker sessions (N)"
        # section; the old standalone "Worker sessions (for debugging)" subsection is gone.
        t = self._seed("Delegated task")
        t = self._attach_hub(t)
        t = self._attach_worker(t)
        _, html = self._run_board()
        self.assertIn("worker sessions (1)", html)    # nested under the hub
        self.assertIn('class="wcard"', html)
        self.assertNotIn("(for debugging)", html)     # old subsection copy gone
        self.assertNotIn(">Workers (", html)          # old header removed

    def test_categories_panel_counts_and_overrides(self):
        # req D: bottom Categories panel lists each category that has ≥1 task with its
        # PER-STATE count, reflecting the user's OVERRIDDEN tag/label (same source the
        # terminal uses — the view-model's tag/label come from `categories`).
        orig = dict(ts.cats.CATEGORIES["green"])
        ts.cats.CATEGORIES["green"] = {"dot": "🟢", "tag": "SHIP", "label": "shipping work"}
        try:
            self._seed("Green one", color="green")
            self._seed("Green two", color="green")
            self._seed("A bug", color="red")
            _, html = self._run_board()
        finally:
            ts.cats.CATEGORIES["green"] = orig
        self.assertIn('class="cats"', html)
        self.assertIn("Categories", html)
        self.assertIn('class="catitem', html)
        self.assertIn("[SHIP]", html)                          # overridden tag
        self.assertIn("shipping work", html)                   # overridden label
        # green has 2 new tasks → per-state counts (new/paused/live/closed), each in its
        # STATUS-coloured span.
        self.assertIn('<span class="cn">2 new</span>', html)
        self.assertIn('<span class="cp">0 paused</span>', html)
        self.assertIn('<span class="ca">0 live</span>', html)
        self.assertIn('<span class="cc">0 closed</span>', html)

    def test_categories_panel_chip_wraps_only_tag_label_outside(self):
        # 1.24.0: the colour pill wraps ONLY the [TAG]; the description (label) renders
        # OUTSIDE the pill as plain text. No swatch, no full-row fill.
        orig = dict(ts.cats.CATEGORIES["green"])
        ts.cats.CATEGORIES["green"] = {"dot": "🟢", "tag": "SHIP", "label": "shipping work"}
        try:
            self._seed("Green one", color="green")
            _, html = self._run_board()
        finally:
            ts.cats.CATEGORIES["green"] = orig
        self.assertNotIn('class="swatch"', html)               # swatch removed
        # the highlight fills the inline pill, which wraps the tag ONLY…
        self.assertIn('class="cchip" style="background:var(--cat-stripe', html)
        self.assertIn('<span class="ctag">🟢 [SHIP]</span></span>', html)  # chip closes right after tag (dot+tag)
        # …and the label is a SIBLING outside the chip (the chip closes BEFORE clabel).
        self.assertIn('<span class="clabel">shipping work</span>', html)
        self.assertIn('[SHIP]</span></span><span class="clabel">', html)  # chip ends, then label
        # the pill+CUSTOM+label group is wrapped in the fixed-width .catleft (req 2).
        self.assertIn('class="catleft" style="width:', html)
        # the row is an inline flex flow (no grid / display:contents).
        self.assertIn(".catitem{display:flex;align-items:center;gap:8px}", html)
        self.assertNotIn(".catitem{display:contents}", html)
        self.assertNotIn('class="cmeta"', html)

    def test_categories_panel_per_state_counts_split_by_status_colour(self):
        # counts are split new/paused/active/closed, each tinted by its STATUS colour
        # (new=--so, paused=--sp, active=--sa, closed=--sc). With no live sessions the
        # stored-active task counts as `paused`; `active` needs a running session.
        self._seed("g new", color="green")
        self._seed("g inprog", color="green", status="active")
        self._seed("g closed", color="green", closed=True)
        _, html = self._run_board()
        self.assertIn('<span class="cn">1 new</span>', html)
        self.assertIn('<span class="cp">1 paused</span>', html)
        self.assertIn('<span class="ca">0 live</span>', html)
        self.assertIn('<span class="cc">1 closed</span>', html)
        # the four status colours are wired to the count spans.
        self.assertIn('.catitem .ccount .cn{color:var(--so)}', html)
        self.assertIn('.catitem .ccount .cp{color:var(--sp)}', html)
        self.assertIn('.catitem .ccount .ca{color:var(--sa)}', html)
        self.assertIn('.catitem .ccount .cc{color:var(--sc)}', html)

    def test_categories_panel_custom_marker_only_when_overridden(self):
        # 1.24.0: the CUSTOM marker renders ONLY for an overridden category (right of the
        # pill, before the description); a non-overridden row simply OMITS it — the old
        # empty-placeholder span + its CSS rule are gone (inline flow, no column to align).
        import config as cfg
        cfg.set("categories", {"green": {"tag": "SHIP", "label": "shipping work"}})
        try:
            self._seed("Green one", color="green")
            self._seed("A bug", color="red")          # a default (not overridden)
            _, html = self._run_board()
        finally:
            cfg.set("categories", {})
        self.assertIn('<span class="cmark">CUSTOM</span>', html)   # overridden → CUSTOM
        self.assertNotIn(">overridden<", html)
        self.assertNotIn(">OVERRIDDEN<", html)
        # exactly one CUSTOM marker — only green is overridden, red is a default.
        self.assertEqual(html.count('<span class="cmark">'), 1)
        # the empty-placeholder span + its CSS rule are gone entirely.
        self.assertNotIn("cmark-empty", html)

    def test_closed_see_more_expander(self):
        # req 7: first 5 closed tasks shown; the rest behind a "see more (N more)".
        for i in range(8):
            self._seed("Closed %d" % i, closed=True)
        _, html = self._run_board()
        self.assertIn('id="closed-extra"', html)
        self.assertIn('class="seemore"', html)
        self.assertIn("see more (3 more)", html)     # 8 − 5 = 3

    def test_closed_no_expander_when_five_or_fewer(self):
        for i in range(4):
            self._seed("Closed %d" % i, closed=True)
        _, html = self._run_board()
        self.assertNotIn('class="seemore"', html)   # the expander ELEMENT (the CSS rule/comment always exists)
        self.assertNotIn('id="closed-extra"', html)

    def test_search_and_filter_controls_present(self):
        # req 8: search box + category + status filters, with data-* on each row.
        self._seed("Open feature", color="green")
        self._seed("A closed bug", color="red", closed=True)
        self._seed("A live one", color="green", status="active")
        _, html = self._run_board()
        # the three controls
        self.assertIn('id="board-search"', html)
        self.assertIn('id="filter-cat"', html)
        self.assertIn('id="filter-status"', html)
        # status filter VALUES are the 4-state display status (new/paused/live/closed),
        # matching each row's data-status so the dropdown filters by what the pill shows.
        self.assertIn('<option value="new">new</option>', html)
        self.assertIn('<option value="paused">paused</option>', html)
        self.assertIn('<option value="live">live</option>', html)
        self.assertIn('<option value="closed">closed</option>', html)
        # the session-state dropdown filters rows by data-sess (running/resumable/none).
        self.assertIn('<option value="running">running</option>', html)
        self.assertIn('<option value="resumable">resumable</option>', html)
        self.assertIn('<option value="none">no session</option>', html)
        self.assertIn('<option value="green">', html)            # category options
        self.assertIn('<option value="red">', html)
        # rows carry the filter data attributes the script reads
        self.assertIn('data-title="open feature"', html)
        self.assertIn('data-cat="green"', html)
        self.assertIn('data-status="new"', html)       # display status (open → new)
        self.assertIn('data-status="closed"', html)
        self.assertIn("data-search=", html)
        # the inline filter logic is present + self-contained
        self.assertIn("getElementById('board-search')", html)

    def test_120_board_still_self_contained_and_escaped(self):
        t = self._seed("Full task", color="green")
        t["projects"] = ["acme-repo"]
        t["state"] = "next: <b>do</b> the thing"
        ts.save_task(t)
        self._attach_hub(t)
        for i in range(7):
            self._seed("Closed %d" % i, color="red", closed=True)
        _, html = self._run_board()
        for needle in _EXTERNAL_NEEDLES:
            self.assertNotIn(needle, html,
                             "board must have no external assets (found %r)" % needle)
        # injected markup in a digest field stays inert (escaped).
        self.assertNotIn("<b>do</b>", html)
        self.assertIn("&lt;b&gt;do&lt;/b&gt;", html)

    # ----- third board UX pass + status relabel (1.21.0) --------------------

    def test_performance_toggle_present_and_persisted(self):
        # a header perf toggle (high⇄low) that disables animations on slow machines; it's a
        # persisted GLOBAL pref (localStorage ts-board-perf) set on data-perf BEFORE paint,
        # and the animation-disabling CSS keys off html[data-perf="low"].
        rb = self._render_board_module()
        html = rb.render_html([])
        self.assertIn('id="perf-toggle"', html)
        self.assertIn("ts-board-perf", html)                         # the persistence key
        self.assertIn('data-perf', html)                             # the attribute
        self.assertIn('html[data-perf="low"] details[open]>*{animation:none', html)  # anims off

    def test_semver_helpers(self):
        self.assertTrue(ts._semver_gt("1.93.0", "1.92.0"))
        self.assertTrue(ts._semver_gt("2.0.0", "1.99.9"))
        self.assertFalse(ts._semver_gt("1.92.0", "1.93.0"))
        self.assertFalse(ts._semver_gt("1.93.0", "1.93.0"))
        self.assertFalse(ts._semver_gt("bogus", "1.0.0"))   # unparseable → never blocks
        self.assertFalse(ts._semver_gt("1.0.0", None))

    def test_board_stamps_version(self):
        rb = self._render_board_module()
        html = rb.render_html([], version="1.93.0")
        self.assertIn('<meta name="ts-board-version" content="1.93.0">', html)

    def test_board_refuses_downgrade_but_explicit_render_overwrites(self):
        # a stale, older-version session's PASSIVE refresh must not clobber a board that a
        # NEWER version already rendered; an explicit render always writes.
        self._seed("Guarded task")
        board = os.path.join(ts.paths.data_dir(), "board.html")
        os.makedirs(ts.paths.data_dir(), exist_ok=True)
        with open(board, "w", encoding="utf-8") as f:
            f.write('<meta name="ts-board-version" content="99.0.0"> future-board')
        ts.write_board(guard_downgrade=True)                 # would downgrade → skip
        with open(board, encoding="utf-8") as f:
            self.assertIn("future-board", f.read())          # untouched
        ts.write_board()                                     # explicit → overwrites
        with open(board, encoding="utf-8") as f:
            self.assertNotIn('content="99.0.0"', f.read())

    def test_status_display_folds_live_session_into_status(self):
        # the 4-state board status: a running session → `live` (green) regardless of the
        # stored status; an in-progress task with NO running session → `paused` (yellow);
        # a stored-open task → `new`; closed stays `closed` even with a running session.
        t = self._seed("Livecheck", status="active")
        vm_live = ts._board_view_model(t, live_seqs={t["seq"]})
        self.assertEqual(vm_live["status_display"], "live")
        self.assertTrue(vm_live["live"])
        vm_paused = ts._board_view_model(t, live_seqs=set())
        self.assertEqual(vm_paused["status_display"], "paused")
        self.assertFalse(vm_paused["live"])
        n = self._seed("Newcheck", status="open")
        self.assertEqual(ts._board_view_model(n, live_seqs=set())["status_display"], "new")
        # an OPEN task with a running session still counts as live (live wins over new).
        self.assertEqual(ts._board_view_model(n, live_seqs={n["seq"]})["status_display"], "live")
        c = self._seed("Closedcheck", closed=True)
        self.assertEqual(ts._board_view_model(c, live_seqs={c["seq"]})["status_display"], "closed")

    def test_status_pill_displays_new_not_open(self):
        # the 4-state DISPLAY status: a stored-open task with no live session shows as
        # "new" — the pill class + data-status are the DISPLAY value `new` (not `open`),
        # so the status filter matches exactly what the pill shows.
        self._seed("A pending one")              # status open, no live session → new
        _, html = self._run_board()
        self.assertIn('class="pill new"', html)         # display-status class
        self.assertIn(">○ new<", html)                 # the word shown is "new"
        self.assertNotIn(">○ open<", html)
        self.assertNotIn('class="pill open"', html)     # the stored value is not a pill class
        self.assertIn('data-status="new"', html)        # filter attr = display value

    def test_open_section_name_kept(self):
        # req A: the not-closed SECTION stays named "Open" (it groups New + Active);
        # only the per-task state label changes.
        self._seed("A pending one")
        self._seed("A live one", status="active")
        _, html = self._run_board()
        self.assertIn("<h2>Open</h2>", html)

    def test_curated_highlights_distinct_and_true_to_name(self):
        # req B: a curated per-variant highlight palette — blue≠silver, design≈white,
        # general≈black, brown reads brown, gold reads gold; all 12 slots defined.
        rb = self._render_board_module()
        import colorsys

        def rgb(h):
            h = h.lstrip("#")
            return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

        keys = ("red", "orange", "yellow", "green", "blue", "purple",
                "black", "pink", "white", "silver", "gold", "brown")
        for variant in ("dark", "light"):
            for k in keys:
                hx = rb.category_highlight(k, variant)
                self.assertRegex(hx, r"^#[0-9a-f]{6}$", "%s/%s must be a hex" % (k, variant))
            # blue vs silver — clearly distinct (different hex, and blue more saturated)
            blue, silver = rb.category_highlight("blue", variant), rb.category_highlight("silver", variant)
            self.assertNotEqual(blue, silver)
            bsat = colorsys.rgb_to_hls(*[c / 255 for c in rgb(blue)])[2]
            ssat = colorsys.rgb_to_hls(*[c / 255 for c in rgb(silver)])[2]
            self.assertGreater(bsat, ssat, "blue should read more saturated than silver (%s)" % variant)
        # design = white: white in dark, a visible light shade in light (both very light)
        self.assertEqual(rb.category_highlight("white", "dark"), "#ffffff")
        self.assertGreater(min(rgb(rb.category_highlight("white", "light"))), 180)  # light shade
        # general = black: black in light, a visible near-black in dark (both very dark)
        self.assertEqual(rb.category_highlight("black", "light"), "#000000")
        self.assertLess(max(rgb(rb.category_highlight("black", "dark"))), 90)        # near-black
        # brown reads brown: R > G > B (warm, descending) in both variants
        for variant in ("dark", "light"):
            r, g, b = rgb(rb.category_highlight("brown", variant))
            self.assertTrue(r > g > b, "brown should read brown in %s (got %d,%d,%d)" % (variant, r, g, b))
        # gold reads gold/amber: a yellow-orange hue (R>G>B, hue in the amber band)
        for variant in ("dark", "light"):
            r, g, b = rgb(rb.category_highlight("gold", variant))
            hue = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)[0] * 360
            self.assertTrue(r > g > b and 35 <= hue <= 60,
                            "gold should read amber in %s (hue %.0f)" % (variant, hue))

    def test_curated_highlight_used_by_tag(self):
        # req B/D: the category tag is filled with the highlight (--cat-stripe) too.
        self._seed("Green task", color="green")
        _, html = self._run_board()
        self.assertIn('class="tag" style="color:var(--cat-fg', html)
        self.assertIn("background:var(--cat-stripe", html)

    def test_closed_see_more_reactive_to_filters(self):
        # req E: filtering runs over EVERY closed row (incl. those inside see-more);
        # the see-more force-opens + updates its count, driven by inline JS.
        for i in range(8):
            self._seed("Closed %d" % i, color="red", closed=True)
        _, html = self._run_board()
        # the see-more carries the collapsed count + an id'd summary the JS rewrites.
        self.assertIn('id="closed-extra" data-more="3"', html)
        self.assertIn('id="closed-extra-sum"', html)
        # the filter JS opens the see-more when a filter is active and counts matches
        # inside it (operating over every closed row, not just the first 5).
        self.assertIn("extra.open=active", html)
        self.assertIn("matching closed", html)
        self.assertIn("see more (", html)              # restores the default summary
        self.assertIn("getAttribute('data-more')", html)

    def test_reset_button_present_and_clears(self):
        # req F: a reset control next to the filters, wired to clear search + filters.
        self._seed("Any task")
        _, html = self._run_board()
        self.assertIn('id="filter-reset"', html)               # the control
        self.assertIn(">reset<", html)
        # the handler clears the search box + both selects (then, 1.32.0 C, also collapses
        # the rows + clears the stored open set / filters before re-applying).
        self.assertIn("getElementById('filter-reset')", html)
        self.assertIn("if(q)q.value='';if(fc)fc.value='';if(fs)fs.value='';", html)
        self.assertIn("apply();});", html)

    def test_121_board_still_self_contained_and_escaped(self):
        t = self._seed("Full task", color="brown")
        t["state"] = "next: <i>migrate</i> the schema"
        ts.save_task(t)
        for i in range(7):
            self._seed("Closed %d" % i, color="gold", closed=True)
        _, html = self._run_board()
        for needle in _EXTERNAL_NEEDLES:
            self.assertNotIn(needle, html,
                             "board must have no external assets (found %r)" % needle)
        self.assertNotIn("<i>migrate</i>", html)
        self.assertIn("&lt;i&gt;migrate&lt;/i&gt;", html)

    # ----- generated timestamp to the top + config/bare panels (1.24.0) ------

    def test_generated_timestamp_in_kicker_not_snapshot(self):
        # 1.24.0: the timestamp moves to the top kicker (.kgen); the bottom snapshot note
        # no longer carries any prefix. 1.36.0: the visible label reads "refreshed" (the
        # `generated` kwarg still feeds the value).
        rb = self._render_board_module()
        html = rb.render_html([], generated="GEN_TS")
        self.assertIn('<span class="kgen">refreshed GEN_TS</span>', html)
        self.assertNotIn('<span class="kgen">generated GEN_TS</span>', html)
        snap = html.index('class="snapshot"')
        self.assertLess(html.index('class="kicker"'), snap)
        self.assertNotIn("refreshed GEN_TS", html[snap:])      # not in the bottom note
        self.assertNotIn("refreshed GEN_TS · ", html)          # old prefix form gone
        # the snapshot keeps the static-snapshot wording.
        self.assertIn("static snapshot", html)
        self.assertIn("re-run", html)

    def test_generated_empty_emits_no_kgen(self):
        rb = self._render_board_module()
        html = rb.render_html([], generated="")
        self.assertNotIn('class="kgen"', html)                  # no kgen SPAN emitted
        self.assertNotIn(">generated ", html)                   # no VISIBLE "generated …" text (a CSS comment may mention it)
        # the kicker CSS is space-between with the kleft/kgen helpers regardless.
        self.assertIn(".kicker{display:flex;justify-content:space-between", html)
        self.assertIn(".kleft{display:flex;gap:8px;align-items:center}", html)
        self.assertIn(".kgen{color:var(--dim)}", html)

    def test_config_panel_is_expandable_rows_with_header(self):
        # 1.26.0: the Current config panel is a list of EXPANDABLE rows under a
        # flag · options header; the old setting/current/set-with table header AND the
        # bottom legend are gone.
        rb = self._render_board_module()
        rows = [
            ("--tint", "on", "on · off", "full tint (default: on)"),
            ("--data-dir", "/x/y", None, "where data lives"),
        ]
        html = rb.render_html([], config_rows=rows, commands=[("/todo", "show the board")])
        # the two-column header: "flag" + "options" — the "bold + underline = current"
        # hint was dropped in 1.31.0.
        self.assertIn('<div class="cfg-head"><span>flag</span><span>options</span></div>', html)
        self.assertNotIn("bold + underline = current", html)
        self.assertNotIn("<th>setting</th>", html)
        # each row is an expandable <details class="crow"> (now with a namespaced
        # data-key="cfg:<flag>", 1.28.0) carrying a <summary class="crowsum">.
        self.assertIn('<details class="crow" data-key="cfg:tint"><summary class="crowsum">', html)
        self.assertIn('<span class="cflag">tint</span>', html)
        # the OLD bottom config legend is GONE.
        self.assertNotIn("current = present state", html)
        self.assertNotIn("set with = what you", html)
        # the new CSS is present; the now-unused .cstate / .kv th rules are removed.
        self.assertIn(".cfg-head{display:flex;gap:12px;font-size:11px;font-weight:600;", html)
        self.assertIn("summary.crowsum{display:flex;gap:12px;align-items:baseline;cursor:pointer;", html)
        self.assertIn(".cfg-head>span:first-child,.crowsum .cflag{flex:none;min-width:185px}", html)
        # 1.28.0 A: the current-value marker is accent-bold AND underlined.
        self.assertIn(".crowsum .copts strong{color:var(--accent);text-decoration:underline}", html)
        self.assertNotIn(".cstate{color:var(--dim);font-style:italic}", html)
        self.assertNotIn(".kv th{text-align:left", html)

    def test_config_panel_enum_row_marks_current_and_expands(self):
        # 1.26.0: an ENUM/toggle row whose current value IS one of the tokens renders
        # the matching token bold (no asterisk, 1.27.0) and the rest plain; the
        # description (default stripped), a Default line, and a per-flag usage line live
        # in the body.
        rb = self._render_board_module()
        rows = [("--tint", "on", "on · off", "full tint (default: on)")]
        html = rb.render_html([], config_rows=rows, commands=[("/todo", "show the board")])
        # the single options cell: on bold, off GRAYED (cdim) — NO trailing asterisk.
        self.assertIn('<span class="copts"><strong>on</strong> · '
                      '<span class="cdim">off</span></span>', html)
        self.assertNotIn('<strong>on*</strong>', html)
        # the expanded body (1.31.0): a bare <code> usage block FIRST (no "Set with:"),
        # then a "Default: <code>X</code>" line (no trailing period) directly below it,
        # then the description WITHOUT "(default: on)".
        self.assertIn("<div>full tint</div>", html)
        self.assertNotIn("full tint (default: on)", html)
        self.assertIn("<div>Default: <code>on</code></div>", html)
        self.assertNotIn("<div>Default: on.</div>", html)
        self.assertNotIn("Set with:", html)
        self.assertIn('<div><code>/task-station:config --tint &lt;on | off&gt;</code></div>', html)
        # ordering: the Default <code> line sits BEFORE the description in the .cdetail.
        cdetail = html[html.index('class="cdetail"'):]
        cdetail = cdetail[:cdetail.index("</details>")]
        self.assertLess(cdetail.index("Default: <code>on</code>"),
                        cdetail.index("<div>full tint</div>"))

    def test_config_panel_state_row_marks_state_then_dim_options(self):
        # 1.26.0: a reported-STATE row (current value is NOT one of the tokens) marks the
        # state bold (no asterisk, 1.27.0), then shows the settable options dimmed.
        rb = self._render_board_module()
        rows = [("--statusline", "provider-only", "on · off", "status bar (default: off)")]
        html = rb.render_html([], config_rows=rows, commands=[("/todo", "show the board")])
        self.assertIn('<span class="copts"><strong>provider-only</strong> '
                      '<span class="cdim">· on · off</span></span>', html)
        self.assertIn("<div>Default: <code>off</code></div>", html)
        self.assertNotIn("Set with:", html)
        self.assertIn('<div><code>/task-station:config --statusline &lt;on | off&gt;</code></div>', html)

    def test_config_panel_path_row_plain_value_and_path_hint(self):
        # 1.26.0: a PATH row (options=None) renders its value plain — no <strong>, no
        # asterisk — with a derived "<value>" usage hint and NO Default line.
        rb = self._render_board_module()
        rows = [("--data-dir", "/x/y", None, "where data lives")]
        html = rb.render_html([], config_rows=rows, commands=[("/todo", "show the board")])
        self.assertIn('<span class="copts">/x/y</span>', html)
        self.assertNotIn("<strong>/x/y", html)
        self.assertNotIn("/x/y*", html)
        self.assertNotIn("Set with:", html)
        self.assertIn('<div><code>/task-station:config --data-dir &lt;value&gt;</code></div>', html)
        self.assertNotIn("Default:", html)
        # the Commands panel is UNCHANGED — it still carries its bare-cmds helpnote.
        self.assertIn('class="helpnote"', html)

    def test_config_panel_tint_theme_raw_match_then_resolving_line(self):
        # 1.26.0: --tint-theme matches its token from the RAW value (before the
        # "value → variant" concat) and mentions the resolved variant in the body only.
        rb = self._render_board_module()
        rows = [("--tint-theme", "auto", "auto · dark · light",
                 "appearance: which variant renders (default: auto)")]
        html = rb.render_html([], config_rows=rows, variant_label="Light Sands",
                              commands=[("/todo", "show the board")])
        # raw "auto" still matches its token in the summary cell (bold); dark/light grayed.
        self.assertIn('<span class="copts"><strong>auto</strong> · '
                      '<span class="cdim">dark</span> · '
                      '<span class="cdim">light</span></span>', html)
        # the resolved variant appears in the expanded body, NOT the summary.
        self.assertIn("<div>Resolving to: Light Sands</div>", html)

    def test_commands_panel_namespaced_when_bare_off(self):
        # 1.24.0: bare OFF → each command label's leading bare token is rewritten to the
        # /task-station: form (trailing args preserved); the off-state helpnote shows.
        rb = self._render_board_module()
        cmds = [("/todo", "show the board"), ("/todo <n>", "open & resume"),
                ("/done", "close the current task"),
                ("/pin", "pin this session as the task's resume target"),
                ("/task-station:config", "open settings")]
        html = rb.render_html([], commands=cmds, bare=False)
        self.assertIn("/task-station:todo", html)
        self.assertIn("/task-station:todo &lt;n&gt;", html)    # trailing arg preserved
        self.assertIn("/task-station:done", html)
        self.assertIn("/task-station:pin", html)
        self.assertIn("/task-station:config", html)            # already namespaced, unchanged
        self.assertIn("--bare-cmds on", html)                  # the off-state helpnote
        self.assertIn('class="helpnote"', html)
        # 1.59.0: "bare-cmds is off" on its own helpnote line, the enable-line
        # (naming the full aliasable set, incl. save/history) on a second line.
        self.assertIn(
            '<div class="helpnote">bare-cmds is off — use the '
            '<code>/task-station:</code> prefix (shown).</div>', html)
        self.assertIn(
            '<div class="helpnote">Enable the short <code>/todo</code>, '
            '<code>/done</code>, <code>/save</code>, <code>/pin</code>, '
            '<code>/history</code>, <code>/repos</code> aliases with '
            '<code>/task-station:config --bare-cmds on</code>.</div>', html)
        self.assertIn(">/save<", html)
        self.assertIn(">/history<", html)
        self.assertIn(">/repos<", html)

    def test_commands_panel_bare_when_bare_on(self):
        # 1.24.0: bare ON → labels shown as-is; the on-state helpnote shows.
        rb = self._render_board_module()
        cmds = [("/todo", "show the board"), ("/done", "close the current task"),
                ("/pin", "pin this session as the task's resume target"),
                ("/task-station:config", "open settings")]
        html = rb.render_html([], commands=cmds, bare=True)
        self.assertIn(">/todo<", html)                         # bare label, not rewritten
        self.assertIn(">/pin<", html)
        self.assertNotIn("/task-station:todo", html)
        self.assertNotIn("/task-station:pin", html)
        self.assertNotIn("Short aliases are on", html)         # old wording is gone
        # 1.59.0: "bare-cmds is on" on its own helpnote line, the /task-station:
        # prefix statement on a second, separate helpnote line.
        self.assertIn(
            '<div class="helpnote">bare-cmds is on — <code>/todo</code>, '
            '<code>/done</code>, <code>/save</code>, <code>/pin</code>, '
            '<code>/history</code>, <code>/repos</code> work directly.</div>', html)
        self.assertIn(
            '<div class="helpnote">The <code>/task-station:</code> prefix also '
            'always works.</div>', html)
        # the on-state helpnote names the full aliasable set, incl. save/history.
        self.assertIn(">/save<", html)
        self.assertIn(">/history<", html)
        self.assertIn(">/repos<", html)

    def test_commands_help_contains_pin_entry(self):
        # 1.54.0: pin is listed once, as the consolidated /todo subcommand (the
        # redundant standalone /pin row was deduped out; the command still works).
        self.assertTrue(any(c == "/todo pin" for c, _ in ts._COMMANDS_HELP))
        self.assertFalse(any(c == "/pin" for c, _ in ts._COMMANDS_HELP))

    # ----- board fixes (1.27.0) ---------------------------------------------

    def test_config_current_value_bold_not_asterisked(self):
        # 1.27.0 A: the current value in an options cell is bold ONLY — no trailing "*".
        # 1.28.0 A: the marker also gains an UNDERLINE (the header hint that spelled this
        # out was dropped in 1.31.0).
        rb = self._render_board_module()
        rows = [("--tint", "on", "on · off", "full tint (default: on)")]
        html = rb.render_html([], config_rows=rows, commands=[("/todo", "show the board")])
        self.assertIn("<strong>on</strong>", html)             # bold marks current
        self.assertNotIn("<strong>on*</strong>", html)         # no trailing asterisk
        self.assertNotIn("bold + underline = current", html)   # 1.31.0: header hint gone
        self.assertNotIn("· bold = current", html)             # old hint gone
        self.assertNotIn("· * = current", html)
        # the underline is wired on the marker CSS.
        self.assertIn("text-decoration:underline", html)

    def test_header_row_centered_with_toggle(self):
        # 1.27.0 B: .hdr is align-items:center so the theme toggle lines up with the
        # big "/todo board" <h1>; the old flex-start is gone.
        self._seed("Any task")
        _, html = self._run_board()
        self.assertIn(".hdr{display:flex;justify-content:space-between;align-items:center;gap:16px}",
                      html)
        self.assertNotIn(".hdr{display:flex;justify-content:space-between;align-items:flex-start",
                         html)

    def test_effort_cell_colour_coded_by_tier(self):
        # 1.27.0 C: the effort cell is tinted by tier (xl=red, l=orange, m=yellow,
        # s=green, xs=white), reusing the per-variant category highlight palette so it
        # is visible in BOTH variants (incl. white in light). Empty effort stays plain.
        rb = self._render_board_module()
        for variant in ("dark", "light"):
            xl = rb._effort_cell({"effort": "xl", "effort_label": "extra-large"},
                                 "sands", variant)
            self.assertIn('class="c-eff" style="color:%s"'
                          % rb._highlight_fb("red", "sands", variant), xl)
            xs = rb._effort_cell({"effort": "xs", "effort_label": "tiny"},
                                 "sands", variant)
            self.assertIn("color:%s" % rb._highlight_fb("white", "sands", variant), xs)
            # empty effort → uncoloured, exactly as before (early return).
            empty = rb._effort_cell({"effort": ""}, "sands", variant)
            self.assertEqual(empty, '<span class="c-eff"></span>')
            self.assertNotIn("color:", empty)
            # an unknown tier is also left uncoloured.
            unk = rb._effort_cell({"effort": "huge"}, "sands", variant)
            self.assertNotIn("color:", unk)

    def test_effort_colour_in_rendered_board(self):
        # 1.27.0 C: a seeded xl task renders the red highlight inline on its c-eff cell.
        rb = self._render_board_module()
        self._seed("Big effort task", effort="xl")
        _, html = self._run_board()
        # the resolved-variant red highlight shows up (dark OR light), inline on c-eff.
        self.assertTrue(
            ('class="c-eff" style="color:%s"' % rb._highlight_fb("red", "sands", "dark")) in html
            or ('class="c-eff" style="color:%s"' % rb._highlight_fb("red", "sands", "light")) in html,
            "the xl effort cell should carry the red highlight inline")

    def test_copy_buttons_on_open_and_resume(self):
        # 1.27.0 D: the Open + Resume commands each sit in a .cmdwrap with a .copybtn;
        # the behavior script wires the copy (clipboard + execCommand fallback); CSS exists.
        t = self._seed("Copyable task")
        self._attach_hub(t)
        _, html = self._run_board()
        # both commands wrapped with a copy button (Open + Resume → at least two).
        self.assertIn('class="cmdwrap"', html)
        self.assertIn('class="copybtn"', html)
        self.assertGreaterEqual(html.count('class="copybtn"'), 2)
        # the button sits inside the wrap next to the .cmd code element.
        self.assertIn('<button class="copybtn" type="button" aria-label="Copy command">copy</button>',
                      html)
        # the CSS for the wrap + button.
        self.assertIn(".cmdwrap{display:flex;gap:8px;align-items:center}", html)
        self.assertIn(".cmdwrap .cmd{min-width:0}", html)
        self.assertIn(".copybtn{flex:none;font-family:var(--mono)", html)
        self.assertIn(".copybtn.copied{color:var(--accent);border-color:var(--accent)}", html)
        # the inline handler: clipboard API + execCommand fallback.
        self.assertIn("copybtn", html)
        self.assertIn("clipboard", html)
        self.assertIn("execCommand", html)
        # the .cmd still keeps its nowrap/scroll so long commands scroll, not wrap.
        self.assertIn('class="cmd" style="white-space:nowrap;overflow-x:auto"', html)

    def test_expanded_rows_persist_to_localstorage(self):
        # 1.28.0 B: persistence is GENERIC — task rows carry a NAMESPACED data-key
        # ("row:<seq>") and a single details[data-key] handler mirrors the open set to
        # localStorage (ts-board-open), restoring d.open on load and updating on toggle.
        t = self._seed("Persisting task")
        _, html = self._run_board()
        # the row carries a stable NAMESPACED data-key ("row:<seq>", not bare "<seq>").
        self.assertIn('data-key="row:%s"' % t["seq"], html)
        self.assertNotIn('data-key="%s"' % t["seq"], html)     # the bare key is gone
        # the GENERIC persistence wiring in the inline script.
        self.assertIn("querySelectorAll('details[data-key]')", html)
        self.assertIn("ts-board-open", html)               # the localStorage key
        self.assertIn("'toggle'", html)                    # listens for the toggle event
        # restore gated on isAuto; the Task Graph defaults open, everything else restores
        # only on an auto-refresh (manual/fresh load defaults collapsed).
        self.assertIn("function defOpen(k){if(k==='minigraph')return true;return isAuto?!!oset[k]:false;}", html)
        self.assertIn("d.open=defOpen(k)", html)
        # rows are NO LONGER an accordion — multiple can be open at once (no collapse-others).
        self.assertNotIn("others[oj].open=false", html)
        # collapsing a row resets its children to their server defaults (data-defopen).
        self.assertIn("kd.hasAttribute('data-defopen')", html)
        self.assertIn("localStorage", html)
        self.assertIn("data-key", html)

    def test_127_board_still_self_contained_and_escaped(self):
        # 1.27.0: the copy buttons + persistence add only inline JS/CSS — still no
        # external assets, and injected markup stays escaped.
        t = self._seed("Full task", color="green", effort="xl")
        t["state"] = "next: <b>ship</b> it"
        ts.save_task(t)
        self._attach_hub(t)
        _, html = self._run_board()
        for needle in _EXTERNAL_NEEDLES:
            self.assertNotIn(needle, html,
                             "board must have no external assets (found %r)" % needle)
        self.assertNotIn("<b>ship</b>", html)
        self.assertIn("&lt;b&gt;ship&lt;/b&gt;", html)

    # ----- board 1.28.0: generic persistence, config + category polish -------

    def test_generic_details_persistence_keys_and_handler(self):
        # 1.28.0 B: EVERY persistable <details> carries a namespaced data-key — task
        # rows (row:<seq>), config rows (cfg:<flag>), category rows (cat:<color>),
        # worker sub-details (wk:<seq>) — and a single details[data-key] handler
        # read/writes ts-board-open with a toggle listener. The closed see-more
        # (#closed-extra) is EXCLUDED (no data-key — managed by the filter JS).
        t = self._seed("Persisting task", color="green")
        t = self._attach_hub(t)                  # → a hub card in the row
        t = self._attach_worker(t)               # → a nested worker sub-section
        t = ts.load_task(t["id"])
        for i in range(8):                       # >5 closed → a see-more is emitted
            self._seed("Closed %d" % i, color="red", closed=True)
        # _run_board passes config_rows + builds the real view-models, so every key type
        # shows up in one render.
        _, board = self._run_board()
        # the generic handler + key, not the old row-specific selector.
        self.assertIn("querySelectorAll('details[data-key]')", board)
        self.assertNotIn("querySelectorAll('details.row[data-key]')", board)
        self.assertIn("ts-board-open", board)
        self.assertIn("addEventListener('toggle'", board)
        # config rows carry cfg:<flag> keys (the flag without leading dashes).
        self.assertIn('data-key="cfg:tint"', board)
        self.assertIn('data-key="cfg:statusline"', board)
        # category rows carry cat:<color> keys.
        self.assertIn('data-key="cat:green"', board)
        # task rows + the sessions section + per-hub worker sub-details carry keys.
        self.assertIn('data-key="row:%s"' % t["seq"], board)
        self.assertIn('data-key="sec-sessions:%s"' % t["seq"], board)
        self.assertIn('data-key="hw:', board)
        # the closed see-more carries NO data-key (excluded from persistence).
        self.assertIn('<details class="seemore" id="closed-extra" data-more=', board)
        seg = board[board.index('id="closed-extra"') - 40:board.index('id="closed-extra"') + 60]
        self.assertNotIn("data-key", seg)

    def test_head_init_sets_scroll_restoration_and_bg_flash_reducer(self):
        # 1.30.0/1.33.0: the <head> pre-paint init makes the reload flicker-free —
        # history.scrollRestoration="manual" (so we own the scroll restore) and the
        # RESOLVED-variant page background is painted on documentElement so the FIRST
        # frame is themed, not white. With the 3-way auto theme, BOTH page hexes are
        # embedded and the resolved variant (incl. auto's OS pick) chooses inline.
        rb = self._render_board_module()
        html = rb.render_html([], variant="dark")
        self.assertIn('history.scrollRestoration="manual"', html)
        # the flash-reducer sets the root background to the resolved page colour inline,
        # selecting between the light + dark page hexes by the resolved variant.
        self.assertIn('document.documentElement.style.backgroundColor', html)
        self.assertIn("backgroundColor=(v===\"light\")?'#f3efe7':'#0d0e11'", html)
        self.assertIn("'#0d0e11'", html)                        # dark page bg embedded
        self.assertIn("'#f3efe7'", html)                        # light page bg embedded
        # both are best-effort (try/caught) and the data-theme set still happens.
        self.assertIn('setAttribute("data-theme"', html)
        # the root also paints the themed colour via CSS (so non-first frames match).
        self.assertIn("html{background:var(--page)}", html)
        for needle in _EXTERNAL_NEEDLES:                        # still no external assets
            self.assertNotIn(needle, html)

    def test_scroll_position_persisted_and_restored_after_details(self):
        # 1.30.0 / 1.35.0: the behavior script SAVES window.scrollY to sessionStorage
        # (ts-board-scroll) — throttled scroll listener + beforeunload + pagehide +
        # visibilitychange — and RESTORES it via window.scrollTo, LAST: AFTER the details
        # open-state restore (re-opening rows changes layout height), before first paint.
        # The restore is RE-APPLIED across layout settle and CANCELLED on real user scroll.
        rb = self._render_board_module()
        html = rb.render_html([], board_autorefresh=True)
        self.assertIn("ts-board-scroll", html)                  # the sessionStorage key
        # save: a scroll listener that writes the position to sessionStorage.
        self.assertIn("addEventListener('scroll'", html)
        self.assertIn("sessionStorage.setItem", html)
        # save on tab teardown / hide so the value is fresh when the reload fires; Safari
        # fires pagehide more reliably than beforeunload on a reload, so save there too.
        self.assertIn("addEventListener('beforeunload'", html)
        self.assertIn("addEventListener('pagehide'", html)
        self.assertIn("visibilitychange", html)
        # restore reads the key and scrolls the window.
        self.assertIn("sessionStorage.getItem", html)
        self.assertIn("window.scrollTo(0,", html)
        # 1.35.0 ROBUST restore: re-applied immediately + rAF + setTimeout(60/200) to beat
        # late layout/height changes, and CANCELLED the moment the user really scrolls.
        self.assertIn("requestAnimationFrame(reapply)", html)
        self.assertIn("setTimeout(reapply,60)", html)
        self.assertIn("setTimeout(reapply,200)", html)
        self.assertIn("lock=false", html)                       # user-scroll cancels reapply
        for ev in ("'wheel'", "'touchmove'", "'keydown'"):      # the cancelling events
            self.assertIn(ev, html)
        # ORDERING: the details open-state restore (ts-board-open / d.open=) must run
        # BEFORE the scroll read + scrollTo, so the height is final when we restore.
        self.assertLess(html.index("ts-board-open"), html.index("ts-board-scroll"))
        self.assertLess(html.index("d.open=defOpen(k)"), html.index("window.scrollTo(0,"))
        for needle in _EXTERNAL_NEEDLES:                        # still no external assets
            self.assertNotIn(needle, html)

    def test_bottom_note_config_fully_qualified_and_todo_bare_aware(self):
        # 1.28.0 C: the static note's /todo board is bare-aware.
        # 1.38.0: the autorefresh-ON note (which carried /task-station:config) was removed,
        # so the live footer no longer mentions --board-autorefresh at all.
        rb = self._render_board_module()
        on = rb.render_html([], board_autorefresh=True)
        on_snap = on.split('<div class="snapshot">', 1)[1].split("</div>", 1)[0]
        self.assertNotIn("--board-autorefresh off", on_snap)
        # static note, bare OFF → /task-station:todo board.
        off_ns = rb.render_html([], board_autorefresh=False, bare=False)
        self.assertIn("re-run <code>/task-station:todo board</code>", off_ns)
        # static note, bare ON → /todo board.
        off_bare = rb.render_html([], board_autorefresh=False, bare=True)
        self.assertIn("re-run <code>/todo board</code>", off_bare)
        self.assertNotIn("/task-station:todo board", off_bare)

    def test_config_row_usage_code_block_first_then_extra_lines(self):
        # 1.29.0 (req 4) + 1.31.0: EVERY config row's body leads with a bare <code> usage
        # block (the explicit set_with when given, NO "Set with:" label), then the
        # Default/description, then each extra_lines entry on its OWN line.
        rb = self._render_board_module()
        rows = [("--statusline", "on", "on · off", "status bar (default: off)",
                 ["States:", "on — task-station owns the status bar",
                  "provider-only — provides a segment; another tool owns the bar",
                  "off — not installed", "Current: provider-only"],
                 "/task-station:config --statusline on | off")]
        html = rb.render_html([], config_rows=rows, commands=[("/todo", "show the board")])
        cdetail = html[html.index('class="cdetail"'):]
        cdetail = cdetail[:cdetail.index("</details>")]
        # the bare <code> usage block is the FIRST line of the body — no "Set with:".
        first = cdetail.index("<div>")
        self.assertIn("<code>", cdetail[first:first + 80])
        self.assertNotIn("Set with:", html)
        self.assertIn('<div><code>/task-station:config --statusline on | off</code></div>', html)
        # each extra_lines entry is its OWN <div> line.
        self.assertIn("<div>States:</div>", html)
        self.assertIn("<div>on — task-station owns the status bar</div>", html)
        self.assertIn("<div>provider-only — provides a segment; another tool owns the bar</div>", html)
        self.assertIn("<div>off — not installed</div>", html)
        self.assertIn("<div>Current: provider-only</div>", html)

    def test_config_row_derives_usage_when_none(self):
        # 1.29.0 (req 4) + 1.31.0: a row with NO set_with (5th/6th None) derives a generic
        # usage line — options joined by " | ", as a bare <code> block (no "Set with:") —
        # as the FIRST body line.
        rb = self._render_board_module()
        rows = [("--tint", "on", "on · off", "full tint (default: on)", None, None)]
        html = rb.render_html([], config_rows=rows, commands=[("/todo", "show the board")])
        self.assertNotIn("Set with:", html)
        self.assertIn('<div><code>/task-station:config --tint &lt;on | off&gt;</code></div>', html)

    def test_every_config_row_has_usage_code_block_first(self):
        # 1.29.0 (req 4) + 1.31.0: for EVERY rendered config row (the real board_rows()),
        # the .cdetail's FIRST line is a bare <code> usage block — never a "Set with:" one.
        import config as cfg
        rb = self._render_board_module()
        rows = cfg.board_rows()
        html = rb.render_html([], config_rows=rows, commands=[("/todo", "show the board")])
        self.assertNotIn("Set with:", html)
        # walk every .cdetail block; each must START with a "<div><code>" usage line.
        idx, seen = 0, 0
        while True:
            j = html.find('<div class="cdetail">', idx)
            if j < 0:
                break
            body = html[j:html.index("</details>", j)]
            first = body.index("<div>")
            self.assertTrue(body[first:].startswith("<div><code>"),
                            "config row .cdetail must lead with a <code> usage block")
            seen += 1
            idx = j + 1
        self.assertGreaterEqual(seen, 10)

    def test_category_rows_expandable_with_guidance(self):
        # 1.29.0 (req 3): each Categories row is a <details class="catrow" data-key="cat:…">
        # whose body shows ONLY the "when to use" guide (the repeated per-row auto-assign
        # note is GONE — auto-assignment is explained once at the section level).
        self._seed("Green one", color="green")
        self._seed("A bug", color="red")
        _, html = self._run_board()
        # the expandable row + its namespaced persistence key.
        self.assertIn('<details class="catrow cat-green" data-key="cat:green">', html)
        self.assertIn('<summary class="catitem catsum cat-green">', html)
        self.assertIn('class="catdetail"', html)
        # the green guide sentence appears in the body.
        self.assertIn("Feature / product coding.", html)
        # the per-row auto-assignment note is GONE (only the section-level note remains).
        self.assertNotIn("Auto-assigned by best fit to this category", html)
        # 1.32.0 G: the section note moved BESIDE the header ("auto assigned by best fit");
        # the old standalone helpnote line under the heading is gone.
        self.assertIn("auto assigned by best fit", html)
        self.assertNotIn("expand a row for what belongs here", html)
        # the CSS for the expandable category rows is unchanged.
        self.assertIn("details.catrow{border-bottom:1px solid var(--line)}", html)
        self.assertIn("summary.catsum{display:flex;align-items:center;gap:8px;cursor:pointer;"
                      "list-style:none;padding:6px 0}", html)
        self.assertIn(".catdetail{padding:2px 0 8px;color:var(--dim);font-size:11.5px;"
                      "display:grid;gap:3px}", html)

    def test_category_row_overridden_shows_shipped_default(self):
        # 1.29.0 (req 3): an overridden category's body shows a "Default: <dot> [TAG]
        # label" line with the SHIPPED default; the old "Customized (CUSTOM)" line is
        # gone. A non-overridden row shows NO Default line.
        import config as cfg
        cfg.set("categories", {"green": {"tag": "SHIP", "label": "shipping work"}})
        try:
            self._seed("Green one", color="green")
            self._seed("A bug", color="red")       # default (not overridden)
            _, html = self._run_board()
        finally:
            cfg.set("categories", {})
        # the shipped default for the green slot, NOT the override.
        self.assertIn("Default: 🟢 [FEATURE] feature work", html)
        # the old customization note is gone.
        self.assertNotIn("Customized (CUSTOM): tag/label overridden from the default.", html)
        # the non-overridden red row carries NO Default line in its catdetail.
        self.assertNotIn("Default: 🔴 [BUG] bug", html)

    def test_category_counts_align_via_identical_catleft_width(self):
        # 1.29.0 (req 2): every .catleft reserves the IDENTICAL width so the counts that
        # follow line up across rows. The width is (widest tag+label + 4)ch.
        import re as _re
        self._seed("Green one", color="green")
        self._seed("A bug", color="red")
        self._seed("A spike", color="purple")
        _, html = self._run_board()
        widths = _re.findall(r'class="catleft" style="width:(\d+)ch"', html)
        self.assertGreaterEqual(len(widths), 3)            # one per shown category
        self.assertEqual(len(set(widths)), 1, "every .catleft must share one width")

    # ----- board 1.32.0: auto vs manual reload, accent polish, column widths -----

    def test_behavior_script_detects_and_clears_ts_auto(self):
        # 1.32.0 A: the script reads the one-shot ts-auto flag into isAuto and clears it
        # immediately, so a manual reload (no flag) is distinguishable from the auto one.
        rb = self._render_board_module()
        html = rb.render_html([])
        self.assertIn("var isAuto=false;", html)
        self.assertIn("isAuto=sessionStorage.getItem('ts-auto')==='1'", html)
        self.assertIn("sessionStorage.removeItem('ts-auto')", html)

    def test_state_restore_gated_on_isauto(self):
        # 1.32.0 A/B: open-state, filters, and scroll all restore ONLY under isAuto.
        rb = self._render_board_module()
        html = rb.render_html([], board_autorefresh=True)
        # open-state: restore set built only under isAuto; defOpen gates d.open.
        self.assertIn("if(isAuto){var oarr=rdOpen();", html)
        self.assertIn("return isAuto?!!oset[k]:false;", html)    # defOpen gates d.open under isAuto
        # filters: read+set only under isAuto.
        self.assertIn("if(isAuto){var fr=JSON.parse(sessionStorage.getItem('ts-board-filters')", html)
        # scroll: restore wrapped in if(isAuto) (1.35.0 robust multi-apply form).
        self.assertIn("try{if(isAuto){var want=parseInt(sessionStorage.getItem(SK),10)", html)

    def test_non_auto_clears_open_set_and_filters(self):
        # 1.32.0 A/B: a manual / fresh load clears the stored open set (wrOpen([])) and
        # the saved filters, so nothing reopens and the controls stay empty.
        rb = self._render_board_module()
        html = rb.render_html([])
        self.assertIn("else{wrOpen([]);}", html)                       # open set cleared
        self.assertIn("else{sessionStorage.removeItem('ts-board-filters');}", html)

    def test_filters_persisted_to_sessionstorage(self):
        # 1.32.0 B: apply() writes the search + filters to ts-board-filters as JSON.
        rb = self._render_board_module()
        html = rb.render_html([], board_autorefresh=True)
        self.assertIn("sessionStorage.setItem('ts-board-filters',", html)
        self.assertIn("JSON.stringify({s:(q&&q.value||''),c:c,st:st,sx:sx})", html)

    def test_reset_collapses_details_and_clears_open_and_filters(self):
        # 1.32.0 C: the reset handler ALSO collapses every open details[data-key], clears
        # the stored open set, and clears the saved filters — a fully-clean view.
        rb = self._render_board_module()
        html = rb.render_html([])
        self.assertIn("for(var ri=0;ri<ds.length;ri++){ds[ri].open=false;}wrOpen([]);", html)
        # the reset handler removes the saved filters too.
        self.assertIn("sessionStorage.removeItem('ts-board-filters')", html)
        # the search + both controls are still cleared first.
        self.assertIn("if(q)q.value='';if(fc)fc.value='';if(fs)fs.value='';", html)

    def test_expanded_task_title_accent_colour(self):
        # 1.32.0 D: an expanded row's task title shows in the accent colour.
        self._seed("Accent title task")
        _, html = self._run_board()
        self.assertIn("details.row[open] .c-task .ttl{color:var(--accent)}", html)

    def test_config_usage_code_accent_colour(self):
        # 1.32.0 E: the .cdetail usage/Default code stands out in the accent colour.
        self._seed("Any task")
        _, html = self._run_board()
        self.assertIn(".cdetail code{color:var(--accent)}", html)

    def test_category_column_narrowed(self):
        # the Category column is narrowed (120px, Effort 118px). The separate live column
        # is GONE (folded into the status pill) → 6 tracks: status # task category effort
        # activity, status widened to 100px for the widest word ("paused").
        self._seed("Any task")
        _, html = self._run_board()
        self.assertIn("--cols:100px 52px minmax(0,1fr) 120px 118px 96px", html)
        self.assertNotIn("168px", html)
        self.assertNotIn("42px 94px 52px minmax(0,1fr)", html)   # old 7-track w/ live column
        # the old 9-track template (with steps/cost/story columns) is gone.
        self.assertNotIn("--cols:94px 52px minmax(0,1fr) 120px 118px 64px 84px 110px 96px", html)

    def test_categories_note_beside_header(self):
        # 1.32.0 G: the "auto assigned by best fit" note sits inside the Categories
        # section header (.sec), and the old standalone helpnote line is gone.
        self._seed("Green one", color="green")
        _, html = self._run_board()
        self.assertIn('<div class="sec"><h2>Categories</h2><span class="count">1</span>'
                      '<span class="count">auto assigned by best fit</span></div>', html)
        self.assertNotIn("Each task's category is picked by best fit", html)
        self.assertNotIn("expand a row for what belongs here", html)

    # ----- board 1.33.0: change-driven refresh, scroll containment, auto theme -----

    def test_render_embeds_passed_rev(self):
        # 1.33.0: render_html embeds the passed rev as the BOARD_REV JS const.
        rb = self._render_board_module()
        html = rb.render_html([], rev="abc123")
        self.assertIn('var BOARD_REV="abc123";', html)

    def test_bottom_note_change_driven_no_5s(self):
        # 1.38.0: the autorefresh-ON footer note is GONE entirely — no "updates
        # automatically", no "every 5s", no --board-autorefresh off. The static (off)
        # note is unchanged.
        rb = self._render_board_module()
        on = rb.render_html([], board_autorefresh=True)
        self.assertNotIn("updates automatically when a task changes", on)
        self.assertNotIn("every 5s", on)
        self.assertNotIn("--board-autorefresh off", on)
        off = rb.render_html([], board_autorefresh=False)
        self.assertIn("static snapshot", off)

    def test_summary_box_contains_overscroll(self):
        # 1.33.0: the inner scroll boxes use overscroll-behavior:contain so wheeling to
        # their edge does not chain to the page. .summary (cited) + .cmd (horizontal).
        self._seed("Any task")
        _, html = self._run_board()
        # the .summary rule (max-height:16em scroll box) carries the contain.
        self.assertIn("max-height:16em;overflow-y:auto;", html)
        self.assertIn("overscroll-behavior:contain}", html)        # on .summary
        self.assertIn("overscroll-behavior-x:contain}", html)      # on .cmd

    def test_theme_init_defaults_to_auto_and_uses_matchmedia(self):
        # 1.33.0: the pre-paint init defaults the stored MODE to 'auto' and resolves
        # 'auto' via matchMedia('(prefers-color-scheme: dark)').
        rb = self._render_board_module()
        html = rb.render_html([], variant="dark")
        self.assertIn('var mode=(s==="dark"||s==="light"||s==="auto")?s:"auto";', html)
        self.assertIn('window.matchMedia("(prefers-color-scheme: dark)")', html)

    def test_theme_toggle_cycles_auto_light_dark_live(self):
        # 1.33.0: the toggle is a THREE-way cycle auto→light→dark→auto persisting the
        # MODE, with a live matchMedia 'change' listener that re-applies while auto.
        rb = self._render_board_module()
        html = rb.render_html([])
        # the three modes + the cycle order.
        self.assertIn("var nx=cur==='auto'?'light':(cur==='light'?'dark':'auto');", html)
        # the mode reader defaults to 'auto' and accepts all three.
        self.assertIn("return (s==='dark'||s==='light'||s==='auto')?s:'auto';", html)
        # the button label shows the active mode (incl. the word "auto").
        self.assertIn("'\\u25D1 auto'", html)
        self.assertIn(" auto'", html)
        # the live OS listener: matchMedia + re-apply only while mode is auto.
        self.assertIn("window.matchMedia('(prefers-color-scheme: dark)')", html)
        self.assertIn("if(readMode()==='auto')applyMode('auto')", html)
        self.assertIn("MM.addEventListener('change',onMM)", html)
        # still no external assets.
        for needle in _EXTERNAL_NEEDLES:
            self.assertNotIn(needle, html)

    def test_write_board_writes_board_rev_js_matching_embedded(self):
        # 1.35.0: write_board writes a board.rev.js <script> sidecar next to board.html —
        # `window.__TSREV="<rev>";` — and that rev equals the BOARD_REV embedded in the
        # page. A <script> sidecar (not the old plain board.rev) so file:// browsers can
        # load it without fetch.
        import re
        self._seed("Rev task")
        path, html = self._run_board()
        revpath = os.path.join(os.path.dirname(path), "board.rev.js")
        self.assertTrue(os.path.exists(revpath), "board.rev.js should sit next to board.html")
        with open(revpath, encoding="utf-8") as f:
            sidecar = f.read().strip()
        # the sidecar assigns the rev to window.__TSREV, json-quoted.
        self.assertTrue(sidecar.startswith("window.__TSREV="),
                        "sidecar must set window.__TSREV, got %r" % sidecar)
        sm = re.search(r'window\.__TSREV="([0-9a-f]{16})";', sidecar)
        self.assertTrue(sm, "sidecar must assign the 16-hex rev to window.__TSREV")
        rev = sm.group(1)
        m = re.search(r'var BOARD_REV="([0-9a-f]{16})";', html)
        self.assertTrue(m, "the page must embed BOARD_REV")
        self.assertEqual(rev, m.group(1))                      # sidecar == embedded

    def test_write_board_rev_stable_over_time_changes_on_mutation(self):
        # 1.35.0: the rev is a hash of the RAW task data — re-rendering the SAME data
        # yields the SAME rev (so time passing won't reload), and mutating a task changes
        # it (so a real change does reload). Read it from the board.rev.js sidecar.
        import re

        def read_rev(revpath):
            with open(revpath, encoding="utf-8") as f:
                return re.search(r'window\.__TSREV="([0-9a-f]{16})";', f.read()).group(1)

        t = self._seed("Stable task")
        path, _ = self._run_board()
        revpath = os.path.join(os.path.dirname(path), "board.rev.js")
        rev1 = read_rev(revpath)
        self._run_board()                                       # same data → same rev
        rev2 = read_rev(revpath)
        self.assertEqual(rev1, rev2)
        t["state"] = "next: mutate"                             # a real data change
        ts.save_task(t)
        self._run_board()
        rev3 = read_rev(revpath)
        self.assertNotEqual(rev1, rev3)

    # ----- board 1.29.0: configurable board browser (_open_argv) -------------
    def test_open_argv_default_is_plain_open(self):
        import config as cfg
        os.environ.pop("TASK_STATION_BROWSER", None)
        cfg.unset("board_browser")
        self.assertEqual(ts._open_argv("/x/board.html"), ["open", "/x/board.html"])

    def test_open_argv_uses_config_board_browser(self):
        import config as cfg
        os.environ.pop("TASK_STATION_BROWSER", None)
        cfg.set("board_browser", "Google Chrome")
        try:
            self.assertEqual(ts._open_argv("/x/board.html"),
                             ["open", "-a", "Google Chrome", "/x/board.html"])
        finally:
            cfg.unset("board_browser")

    def test_open_argv_env_takes_precedence(self):
        import config as cfg
        cfg.set("board_browser", "Google Chrome")
        os.environ["TASK_STATION_BROWSER"] = "Firefox"
        try:
            self.assertEqual(ts._open_argv("/x/board.html"),
                             ["open", "-a", "Firefox", "/x/board.html"])
        finally:
            os.environ.pop("TASK_STATION_BROWSER", None)
            cfg.unset("board_browser")


class BoardRefreshOnMutationTest(unittest.TestCase):
    """#399: a status mutation (close / reopen / open⇄active) must refresh the
    persisted board.html SYNCHRONOUSLY — not wait for the turn-end Stop hook — so
    the board never shows stale status between the mutation and the next turn.
    The refresh is gated exactly like `board --refresh-if-live` (autorefresh on AND
    board.html already exists), extracted into maybe_refresh_board()."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        os.environ.pop("TASK_STATION_BOARD_AUTOREFRESH", None)
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        ts.DELEGATE_REGISTRY = os.path.join(self.tmp, "workers.json")
        ts.store.reset_cache()

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        os.environ.pop("TASK_STATION_BOARD_AUTOREFRESH", None)
        ts.store.reset_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- helpers ------------------------------------------------------------
    def _seed(self, title, status=None):
        t = ts.new_task(title, "summary for " + title, color="green")
        ts.save_task(t)
        ts.ensure_seqs()
        t = ts.load_task(t["id"])
        if status:
            t["status"] = status
            ts.save_task(t)
        return ts.load_task(t["id"])

    def _board_path(self):
        return os.path.join(self.tmp, "board.html")

    def _write_initial_board(self):
        """Render board.html once (autorefresh still off) — the 'user has opened the
        board' precondition the gate keys on."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_board(_Args(open=False))
        self.assertTrue(os.path.exists(self._board_path()))

    def _read_board(self):
        with open(self._board_path(), encoding="utf-8") as f:
            return f.read()

    def _row_status(self, html, title):
        """The data-status of the board row whose data-title matches `title`."""
        m = re.search(r'<details class="row[^>]*data-title="%s"[^>]*>'
                      % re.escape(title.lower()), html)
        self.assertTrue(m, "row for %r must be present in the board" % title)
        sm = re.search(r'data-status="([^"]*)"', m.group(0))
        self.assertIsNotNone(sm)
        return sm.group(1)

    def _autorefresh_on(self):
        os.environ["TASK_STATION_BOARD_AUTOREFRESH"] = "on"

    # --- maybe_refresh_board() gate ----------------------------------------
    def test_helper_noop_when_autorefresh_off(self):
        # off → never regenerate, even if board.html exists (leave it byte-untouched).
        self._seed("Gate task")
        with open(self._board_path(), "w", encoding="utf-8") as f:
            f.write("SENTINEL")
        ts.maybe_refresh_board()   # autorefresh env unset → off
        self.assertEqual(self._read_board(), "SENTINEL")

    def test_helper_noop_when_board_absent(self):
        # on but the user never opened the board → do NOT create it.
        self._seed("Gate task")
        self._autorefresh_on()
        ts.maybe_refresh_board()
        self.assertFalse(os.path.exists(self._board_path()))

    def test_helper_regenerates_when_both_on(self):
        # on + board.html exists → rewrite it with the real, current board.
        self._seed("Gate task")
        with open(self._board_path(), "w", encoding="utf-8") as f:
            f.write("SENTINEL")
        self._autorefresh_on()
        ts.maybe_refresh_board()
        html = self._read_board()
        self.assertNotEqual(html, "SENTINEL")
        self.assertIn("<!doctype html>", html.lower())
        self.assertIn("Gate task", html)

    # --- regression: close refreshes the board (#399) ----------------------
    def test_done_task_close_refreshes_board_to_closed(self):
        # THE bug: closing a task must rewrite board.html so it shows the task closed,
        # not the stale active row it showed before /done.
        t = self._seed("Closable via done", status="active")
        self._write_initial_board()
        self.assertEqual(self._row_status(self._read_board(), "Closable via done"),
                         "paused")   # stale-state precondition: in progress, no live session
        self._autorefresh_on()
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_done(_Args(task=str(t["seq"]), session=None))
        # store is closed AND the persisted board now reflects that (no longer stale).
        self.assertEqual(ts.load_task(t["id"])["status"], "closed")
        self.assertEqual(self._row_status(self._read_board(), "Closable via done"),
                         "closed")

    def test_done_task_batch_close_refreshes_once(self):
        # a /done 1,2 batch: after closing all, the board shows BOTH closed.
        a = self._seed("Batch one", status="active")
        b = self._seed("Batch two", status="active")
        self._write_initial_board()
        self._autorefresh_on()
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_done(_Args(task="%d,%d" % (a["seq"], b["seq"]), session=None))
        html = self._read_board()
        self.assertEqual(self._row_status(html, "Batch one"), "closed")
        self.assertEqual(self._row_status(html, "Batch two"), "closed")

    def test_done_session_close_refreshes_board_to_closed(self):
        # the /done path (close the session's attached task) also refreshes the board.
        t = self._seed("Session-closable", status="active")
        ts.set_link("sess-1", t["id"])
        self._write_initial_board()
        self._autorefresh_on()
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_done(_Args(task=None, session="sess-1"))
        self.assertEqual(ts.load_task(t["id"])["status"], "closed")
        self.assertEqual(self._row_status(self._read_board(), "Session-closable"),
                         "closed")

    def test_done_provisional_discard_refreshes_board(self):
        # discarding an untouched provisional task removes its row from the board.
        t = self._seed("Provisional throwaway")
        t["provisional"] = True
        ts.save_task(t)
        ts.set_link("sess-p", t["id"])
        self._write_initial_board()
        self.assertIn("Provisional throwaway", self._read_board())
        self._autorefresh_on()
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_done(_Args(task=None, session="sess-p"))
        self.assertIsNone(ts.load_task(t["id"]))       # GC'd
        self.assertNotIn("Provisional throwaway", self._read_board())

    # --- regression: reopen + open⇄active refresh the board ------------------
    def test_reopen_refreshes_board_to_open(self):
        # reopening a closed task (attach) flips its board row back to open.
        t = self._seed("Reopenable", status="closed")
        self._write_initial_board()
        self.assertEqual(self._row_status(self._read_board(), "Reopenable"), "closed")
        self._autorefresh_on()
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_attach(_Args(task=str(t["seq"]), session="sess-r", color=None, note=None))
        self.assertEqual(ts.load_task(t["id"])["status"], "open")
        self.assertEqual(self._row_status(self._read_board(), "Reopenable"), "new")

    def test_status_open_to_active_refreshes_board(self):
        t = self._seed("Promotable", status="open")
        self._write_initial_board()
        self.assertEqual(self._row_status(self._read_board(), "Promotable"), "new")
        self._autorefresh_on()
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_status(_Args(task=str(t["seq"]), value="active", session=None))
        self.assertEqual(ts.load_task(t["id"])["status"], "active")
        # no live session → the board shows the in-progress task as `paused`.
        self.assertEqual(self._row_status(self._read_board(), "Promotable"), "paused")

    def test_status_active_to_open_refreshes_board(self):
        t = self._seed("Demotable", status="active")
        self._write_initial_board()
        self.assertEqual(self._row_status(self._read_board(), "Demotable"), "paused")
        self._autorefresh_on()
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_status(_Args(task=str(t["seq"]), value="new", session=None))
        self.assertEqual(ts.load_task(t["id"])["status"], "open")
        self.assertEqual(self._row_status(self._read_board(), "Demotable"), "new")

    def test_stop_hook_refresh_if_live_uses_shared_helper(self):
        # the Stop-hook path (board --refresh-if-live) regenerates via the SAME gate:
        # on + existing board.html → rewrite; still no meta-refresh / fetch.
        self._seed("Stop-hook task")
        self._write_initial_board()
        self._autorefresh_on()
        ts.cmd_board(_Args(refresh_if_live=True))
        html = self._read_board()
        self.assertIn("Stop-hook task", html)
        self.assertIn("board.rev.js", html)


if __name__ == "__main__":
    unittest.main()
