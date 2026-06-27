"""Visual HTML board: `task-station board` writes a (mostly) self-contained HTML
file of all tasks (open + closed) — seqs, titles, briefing fields — with NO
server, NO deps, and NO EXTERNAL asset references. Inline <script>/<style> ARE
allowed (1.19 theme toggle + hover-scroll); only external assets are forbidden.
Empty store renders without crashing."""
import importlib.util
import io
import os
import shutil
import sys
import tempfile
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
_EXTERNAL_NEEDLES = ("src=", "<link ", "@import", "url(http", "//fonts.")


class BoardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
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
        # With no PR URLs anywhere, a self-contained board references nothing remote.
        self._seed("Plain task")
        _, html = self._run_board()
        self.assertNotIn("http", html.lower())

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
        self._seed("A live one", status="active")
        self._seed("A finished one", closed=True)
        _, html = self._run_board()
        # Labeled status pills carry the WORD, not a lone glyph (req 2).
        self.assertIn('class="pill open"', html)
        self.assertIn('class="pill active"', html)
        self.assertIn('class="pill closed"', html)
        for word in (">○ open<", ">● active<", ">✕ closed<"):
            self.assertIn(word, html)

    def test_summary_in_expanded_detail(self):
        self._seed("Task with a summary")   # _seed writes summary "summary for <title>"
        _, html = self._run_board()
        self.assertIn('class="summary"', html)
        self.assertIn("summary for Task with a summary", html)

    def test_main_resume_labeled_and_workers_separate(self):
        t = self._seed("Delegated task")
        t["projects"] = ["acme-repo"]       # → a Workers subsection
        ts.save_task(t)
        self._attach_hub(t)                 # → a hub resume one-liner
        _, html = self._run_board()
        # Main hub resume present + clearly labeled, on a nowrap element.
        self.assertIn("Resume the session", html)
        self.assertIn("claude", html)
        # Workers live in their own de-emphasised subsection, distinct from the hub line.
        self.assertIn('class="workers"', html)
        self.assertIn("acme-repo", html)
        # The resume command sits on a nowrap, scroll-in-place element.
        self.assertIn('class="cmd" style="white-space:nowrap;overflow-x:auto"', html)

    def test_pin_merged_into_resume_no_separate_banner(self):
        # req 3: no separate "📌 Pinned" banner — the pin is folded INTO the resume
        # label ("Resume the session (pinned 📌)").
        t = self._seed("Pinned task")
        t = self._attach_hub(t, sid="pin-sess", preborn=True)
        t["pinned_session"] = "pin-sess"
        ts.save_task(t)
        _, html = self._run_board()
        self.assertNotIn('class="pinned"', html)               # banner removed
        self.assertNotIn("resumes its pinned session", html)   # old banner copy gone
        self.assertIn("Resume the session (pinned \U0001F4CC)", html)   # pinned in the label
        self.assertIn("--session-id pin-sess", html)

    def test_commands_help_present(self):
        self._seed("Any task")
        _, html = self._run_board()
        self.assertIn("Commands", html)
        self.assertIn("/todo board", html)            # reuses _COMMANDS_HELP
        self.assertIn("/done", html)

    def test_config_help_present(self):
        self._seed("Any task")
        _, html = self._run_board()
        self.assertIn("Current config", html)
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

    def test_autorefresh_on_injects_meta_and_live_note(self):
        rb = self._render_board_module()
        html = rb.render_html([], board_autorefresh=True)
        self.assertIn('<meta http-equiv="refresh" content="5">', html)
        self.assertIn("auto-refreshing every 5s", html)
        self.assertIn("--board-autorefresh off", html)
        self.assertNotIn("static snapshot", html)
        # auto-refresh adds a meta-refresh; still NO external assets (inline JS ok).
        for needle in _EXTERNAL_NEEDLES:
            self.assertNotIn(needle, html)

    def test_write_board_picks_up_autorefresh_config(self):
        self._seed("Live board task")
        os.environ["TASK_STATION_BOARD_AUTOREFRESH"] = "on"
        try:
            _, html = self._run_board()
        finally:
            os.environ.pop("TASK_STATION_BOARD_AUTOREFRESH", None)
        self.assertIn('<meta http-equiv="refresh" content="5">', html)
        self.assertIn("auto-refreshing every 5s", html)

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
        # regenerated WITH the meta-refresh now that the flag is on.
        self.assertIn('<meta http-equiv="refresh" content="5">', html)

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

    def test_expanded_row_has_distinct_background(self):
        # req 6: details.row[open] gets a background clearly different from the page.
        self._seed("Expandable task")
        _, html = self._run_board()
        self.assertIn("details.row[open]{background:var(--open)}", html)
        self.assertIn("--open:#23272f", html)                  # dark variant open bg
        self.assertIn("--open:#e3dccb", html)                  # light variant open bg
        self.assertNotIn("--open:#0d0e11", html)               # not the (dark) page colour

    def test_left_border_is_category_bg_color(self):
        # req 7: the left accent stripe is the category's BACKGROUND colour, not bold.
        self._seed("Green task", color="green")
        _, html = self._run_board()
        self.assertIn("border-left-color:var(--cat-bg", html)  # driven by --cat-bg
        self.assertIn(".cat-green{--cat-bg:#1c2a16", html)     # sands dark green BG
        self.assertIn(".cat-green{--cat-bg:#233a2b", html)     # sands light green BG
        self.assertIn("--cat-accent:#b6e85a", html)            # bold is the ACCENT, not the stripe

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

    def test_open_command_distinct_from_resume(self):
        # req 8: the /todo <seq> OPEN command, labeled distinctly from RESUME.
        t = self._seed("Open vs resume task")
        self._attach_hub(t)
        _, html = self._run_board()
        self.assertIn("/todo %s" % t["seq"], html)             # the open command
        self.assertIn("Open the task", html)                   # labeled
        self.assertIn("Resume the session", html)              # the other, distinct action
        # both are single-line/no-wrap commands
        self.assertIn('class="cmd" style="white-space:nowrap;overflow-x:auto"', html)
        # the open label sits before the resume label (open above resume)
        self.assertLess(html.index("Open the task"), html.index("Resume the session"))

    def test_heading_is_todo_board_not_task_board(self):
        # req 9: rename "task board" → "/todo board" on the page.
        self._seed("Any task")
        _, html = self._run_board()
        self.assertIn("/todo board", html)
        self.assertNotIn("task board", html)                   # the old heading is gone
        self.assertIn("<h1>/todo board</h1>", html)

    def test_board_message_says_todo_board(self):
        # req 9: the [BOARD] CLI message references "/todo board".
        self._seed("Routed task")
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_render(_Args(arg="board", format="md", session="s1"))
        out = buf.getvalue()
        self.assertIn("/todo board", out)
        self.assertNotIn("visual task board", out)

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


if __name__ == "__main__":
    unittest.main()
