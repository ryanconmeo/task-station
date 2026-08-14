import os, sys, json, io, tempfile, shutil, unittest
from contextlib import redirect_stdout
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import config


class _Args:
    """Minimal argparse-result stand-in: cmd_config reads its flags via getattr
    with defaults, so a no-flag board render only needs workspace_dirs=None."""
    def __init__(self, **kw):
        self.__dict__.update(kw)

class Config(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); os.environ["TASK_STATION_HOME"] = self.tmp
    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None); shutil.rmtree(self.tmp, ignore_errors=True)

    def test_set_get_roundtrip(self):
        config.set("tint_theme", "dark")
        self.assertEqual(config.get("tint_theme"), "dark")
        with open(os.path.join(self.tmp, "config.json")) as f:
            self.assertEqual(json.load(f)["tint_theme"], "dark")

    def test_get_default_when_absent(self):
        self.assertEqual(config.get("tint_mode", "auto"), "auto")

    def test_workspace_dirs_parsed(self):
        config.set("workspace_dirs", ["~/a", "~/b"])
        self.assertEqual(config.workspace_dirs(),
                         [os.path.expanduser("~/a"), os.path.expanduser("~/b")])

    def test_unset_restores_default(self):
        config.set("tint_theme", "dark"); config.unset("tint_theme")
        self.assertEqual(config.get("tint_theme", "auto"), "auto")

    def test_title_enabled_default_on(self):
        os.environ.pop("TASK_STATION_TITLE", None)
        self.assertTrue(config.title_enabled())

    def test_title_disabled_via_config(self):
        config.set("title", False)
        self.assertFalse(config.title_enabled())

    def test_title_disabled_via_env(self):
        config.set("title", True)
        os.environ["TASK_STATION_TITLE"] = "off"
        try:
            self.assertFalse(config.title_enabled())
        finally:
            os.environ.pop("TASK_STATION_TITLE", None)

    # --- --editor-scheme (WS7; auto-detect default as of 1.77.0) -------------
    _KNOWN_SCHEMES = {"cursor", "zed", "vscode", "vscode-insiders", "subl",
                      "pycharm", "idea", "file"}

    def test_editor_scheme_default_autodetects(self):
        # No config, no env → a detected scheme (never a hardcoded assumption).
        os.environ.pop("TASK_STATION_EDITOR_SCHEME", None)
        for v in ("VISUAL", "EDITOR"):
            os.environ.pop(v, None)
        self.assertIn(config.editor_scheme(), self._KNOWN_SCHEMES)

    def test_editor_scheme_editor_env_hint(self):
        os.environ.pop("TASK_STATION_EDITOR_SCHEME", None)
        os.environ.pop("VISUAL", None)
        try:
            os.environ["EDITOR"] = "cursor --wait"
            self.assertEqual(config.detect_editor_scheme(), "cursor")
            os.environ["EDITOR"] = "code -w"
            self.assertEqual(config.detect_editor_scheme(), "vscode")
        finally:
            os.environ.pop("EDITOR", None)

    def test_editor_scheme_terminal_editor_falls_through(self):
        # A terminal editor has no scheme → detection ignores the hint (result is the
        # app-probe or `file`, never "vim").
        os.environ.pop("TASK_STATION_EDITOR_SCHEME", None)
        os.environ.pop("VISUAL", None)
        try:
            os.environ["EDITOR"] = "vim"
            self.assertIn(config.detect_editor_scheme(), self._KNOWN_SCHEMES)
            self.assertNotEqual(config.detect_editor_scheme(), "vim")
        finally:
            os.environ.pop("EDITOR", None)

    def test_editor_scheme_set_and_clear(self):
        os.environ.pop("TASK_STATION_EDITOR_SCHEME", None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(_Args(workspace_dirs=None, editor_scheme="cursor"))
        self.assertEqual(config.editor_scheme(), "cursor")
        with redirect_stdout(io.StringIO()):
            config.cmd_config(_Args(workspace_dirs=None, editor_scheme=""))  # no value → auto-detect
        self.assertIn(config.editor_scheme(), self._KNOWN_SCHEMES)

    def test_editor_scheme_env_wins(self):
        config.set("editor_scheme", "cursor")
        os.environ["TASK_STATION_EDITOR_SCHEME"] = "vscode-insiders"
        try:
            self.assertEqual(config.editor_scheme(), "vscode-insiders")
        finally:
            os.environ.pop("TASK_STATION_EDITOR_SCHEME", None)

    def test_editor_scheme_in_board_rows(self):
        keys = {r[0] for r in config.board_rows()}
        self.assertIn("--editor-scheme", keys)

    def test_reset_clears_editor_scheme(self):
        os.environ.pop("TASK_STATION_EDITOR_SCHEME", None)
        config.set("editor_scheme", "cursor")
        config.reset_settings()
        # Reset clears the stored override and resolution falls back to
        # auto-detection, whose answer is machine-dependent (vscode on a dev
        # box, file on a bare CI runner) — assert the fallback happened, never
        # one machine's editor.
        self.assertIn(config.get("editor_scheme"), (None, ""))
        self.assertEqual(config.editor_scheme(), config.detect_editor_scheme())

    # --- --tint flag --------------------------------------------------------
    def test_tint_enabled_default_on(self):
        os.environ.pop("TASK_STATION_TINT", None)
        self.assertTrue(config.tint_enabled())

    def test_tint_off_persists_and_disables(self):
        os.environ.pop("TASK_STATION_TINT", None)
        config.set("tint", False)
        self.assertFalse(config.tint_enabled())
        with open(os.path.join(self.tmp, "config.json")) as f:
            self.assertFalse(json.load(f)["tint"])

    def test_tint_env_on_overrides_config_off(self):
        config.set("tint", False)
        os.environ["TASK_STATION_TINT"] = "on"
        try:
            self.assertTrue(config.tint_enabled())   # env wins over config
        finally:
            os.environ.pop("TASK_STATION_TINT", None)

    def test_tint_env_off_overrides_config_on(self):
        config.set("tint", True)
        os.environ["TASK_STATION_TINT"] = "off"
        try:
            self.assertFalse(config.tint_enabled())
        finally:
            os.environ.pop("TASK_STATION_TINT", None)

    def test_cmd_config_tint_off_persists(self):
        os.environ.pop("TASK_STATION_TINT", None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(_Args(workspace_dirs=None, tint="off"))
        self.assertIn("tint = off", buf.getvalue())
        self.assertFalse(config.tint_enabled())

    # --- --delegate-bypass-permissions flag (#463) --------------------------
    def test_delegate_bypass_default_off(self):
        # #463: default OFF — workers spawn with dontAsk (fail-closed, no
        # --dangerously-skip-permissions); bypassPermissions is opt-in only.
        os.environ.pop("TASK_STATION_DELEGATE_BYPASS", None)
        self.assertFalse(config.delegate_bypass_permissions())

    def test_cmd_config_delegate_bypass_off_persists(self):
        os.environ.pop("TASK_STATION_DELEGATE_BYPASS", None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(_Args(workspace_dirs=None,
                                    delegate_bypass_permissions="off"))
        self.assertIn("delegate_bypass_permissions = off", buf.getvalue())
        self.assertFalse(config.delegate_bypass_permissions())

    def test_delegate_bypass_env_overrides(self):
        config.set("delegate_bypass_permissions", False)
        os.environ["TASK_STATION_DELEGATE_BYPASS"] = "on"
        try:
            self.assertTrue(config.delegate_bypass_permissions())   # env wins over config
        finally:
            os.environ.pop("TASK_STATION_DELEGATE_BYPASS", None)

    def test_delegate_bypass_in_board_rows(self):
        flags = [r[0] for r in config.board_rows()]
        self.assertIn("--delegate-bypass-permissions", flags)

    # --- --obsidian-category-hubs flag (WS10) -------------------------------
    def test_obsidian_category_hubs_default_on(self):
        self.assertTrue(config.obsidian_category_hubs_enabled())

    def test_obsidian_category_hubs_off_persists(self):
        config.set("obsidian_category_hubs", False)
        self.assertFalse(config.obsidian_category_hubs_enabled())

    def test_obsidian_category_hubs_back_on(self):
        config.set("obsidian_category_hubs", False)
        config.set("obsidian_category_hubs", True)
        self.assertTrue(config.obsidian_category_hubs_enabled())

    def test_cmd_config_obsidian_category_hubs_off_persists(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(_Args(workspace_dirs=None, obsidian_category_hubs="off"))
        self.assertIn("obsidian_category_hubs = off", buf.getvalue())
        self.assertFalse(config.obsidian_category_hubs_enabled())

    def test_reset_restores_category_hubs_default(self):
        config.set("obsidian_category_hubs", False)
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(_Args(workspace_dirs=None, reset="confirm"))
        self.assertTrue(config.obsidian_category_hubs_enabled())

    # --- --obsidian-subgroups flag (WS11) -----------------------------------
    def test_obsidian_subgroups_default_on(self):
        self.assertTrue(config.obsidian_subgroups_enabled())

    def test_obsidian_subgroups_off_persists(self):
        config.set("obsidian_subgroups", False)
        self.assertFalse(config.obsidian_subgroups_enabled())

    def test_obsidian_subgroups_back_on(self):
        config.set("obsidian_subgroups", False)
        config.set("obsidian_subgroups", True)
        self.assertTrue(config.obsidian_subgroups_enabled())

    def test_cmd_config_obsidian_subgroups_off_persists(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(_Args(workspace_dirs=None, obsidian_subgroups="off"))
        self.assertIn("obsidian_subgroups = off", buf.getvalue())
        self.assertFalse(config.obsidian_subgroups_enabled())

    def test_reset_restores_subgroups_default(self):
        config.set("obsidian_subgroups", False)
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(_Args(workspace_dirs=None, reset="confirm"))
        self.assertTrue(config.obsidian_subgroups_enabled())

    def test_obsidian_subgroups_env_override(self):
        os.environ["TASK_STATION_OBSIDIAN_SUBGROUPS"] = "off"
        try:
            config.set("obsidian_subgroups", True)
            self.assertFalse(config.obsidian_subgroups_enabled())
        finally:
            os.environ.pop("TASK_STATION_OBSIDIAN_SUBGROUPS", None)

    # --- --obsidian-story-groups flag (WS13) --------------------------------
    def test_obsidian_story_groups_default_on(self):
        self.assertTrue(config.obsidian_story_groups_enabled())

    def test_obsidian_story_groups_off_persists(self):
        config.set("obsidian_story_groups", False)
        self.assertFalse(config.obsidian_story_groups_enabled())

    def test_obsidian_story_groups_back_on(self):
        config.set("obsidian_story_groups", False)
        config.set("obsidian_story_groups", True)
        self.assertTrue(config.obsidian_story_groups_enabled())

    def test_cmd_config_obsidian_story_groups_off_persists(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(_Args(workspace_dirs=None, obsidian_story_groups="off"))
        self.assertIn("obsidian_story_groups = off", buf.getvalue())
        self.assertFalse(config.obsidian_story_groups_enabled())

    def test_reset_restores_story_groups_default(self):
        config.set("obsidian_story_groups", False)
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(_Args(workspace_dirs=None, reset="confirm"))
        self.assertTrue(config.obsidian_story_groups_enabled())

    def test_obsidian_story_groups_env_override(self):
        os.environ["TASK_STATION_OBSIDIAN_STORY_GROUPS"] = "off"
        try:
            config.set("obsidian_story_groups", True)
            self.assertFalse(config.obsidian_story_groups_enabled())
        finally:
            os.environ.pop("TASK_STATION_OBSIDIAN_STORY_GROUPS", None)

    # --- --board-autorefresh flag (1.17.0) ----------------------------------
    def test_board_autorefresh_default_off(self):
        os.environ.pop("TASK_STATION_BOARD_AUTOREFRESH", None)
        self.assertFalse(config.board_autorefresh_enabled())

    def test_board_autorefresh_persists(self):
        os.environ.pop("TASK_STATION_BOARD_AUTOREFRESH", None)
        config.set("board_autorefresh", True)
        self.assertTrue(config.board_autorefresh_enabled())

    def test_board_autorefresh_env_overrides_config(self):
        config.set("board_autorefresh", False)
        os.environ["TASK_STATION_BOARD_AUTOREFRESH"] = "on"
        try:
            self.assertTrue(config.board_autorefresh_enabled())
        finally:
            os.environ.pop("TASK_STATION_BOARD_AUTOREFRESH", None)
        os.environ["TASK_STATION_BOARD_AUTOREFRESH"] = "off"
        config.set("board_autorefresh", True)
        try:
            self.assertFalse(config.board_autorefresh_enabled())
        finally:
            os.environ.pop("TASK_STATION_BOARD_AUTOREFRESH", None)

    def test_cmd_config_board_autorefresh_on_persists(self):
        os.environ.pop("TASK_STATION_BOARD_AUTOREFRESH", None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(_Args(workspace_dirs=None, board_autorefresh="on"))
        self.assertIn("board_autorefresh = on", buf.getvalue())
        self.assertTrue(config.board_autorefresh_enabled())

    def test_board_rows_includes_autorefresh(self):
        flags = [r[0] for r in config.board_rows()]
        self.assertIn("--board-autorefresh", flags)

    # --- --done-closes-window flag (opt-in window-close) --------------------
    def test_done_closes_window_default_off(self):
        os.environ.pop("TASK_STATION_DONE_CLOSES_WINDOW", None)
        self.assertFalse(config.done_closes_window_enabled())

    def test_done_closes_window_persists(self):
        os.environ.pop("TASK_STATION_DONE_CLOSES_WINDOW", None)
        config.set("done_closes_window", True)
        self.assertTrue(config.done_closes_window_enabled())
        with open(os.path.join(self.tmp, "config.json")) as f:
            self.assertTrue(json.load(f)["done_closes_window"])

    def test_done_closes_window_toggle_roundtrip(self):
        os.environ.pop("TASK_STATION_DONE_CLOSES_WINDOW", None)
        config.set("done_closes_window", True)
        self.assertTrue(config.done_closes_window_enabled())
        config.set("done_closes_window", False)
        self.assertFalse(config.done_closes_window_enabled())

    def test_cmd_config_done_closes_window_on_persists(self):
        os.environ.pop("TASK_STATION_DONE_CLOSES_WINDOW", None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(_Args(workspace_dirs=None, done_closes_window="on"))
        self.assertIn("done_closes_window = on", buf.getvalue())
        self.assertTrue(config.done_closes_window_enabled())

    def test_board_rows_includes_done_closes_window(self):
        flags = [r[0] for r in config.board_rows()]
        self.assertIn("--done-closes-window", flags)

    # --- --board-browser flag (1.29.0) --------------------------------------
    def test_board_browser_default_none(self):
        self.assertIsNone(config.board_browser())

    def test_board_browser_persists_and_reads(self):
        config.set("board_browser", "Google Chrome")
        self.assertEqual(config.board_browser(), "Google Chrome")

    def test_cmd_config_board_browser_set_and_clear(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(_Args(workspace_dirs=None, board_browser="Firefox"))
        self.assertIn("board_browser = Firefox", buf.getvalue())
        self.assertEqual(config.board_browser(), "Firefox")
        # empty string (the const for `--board-browser` with no value) clears it.
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(_Args(workspace_dirs=None, board_browser=""))
        self.assertIn("(system default)", buf.getvalue())
        self.assertIsNone(config.board_browser())

    def test_cmd_config_board_browser_get(self):
        config.set("board_browser", "Arc")
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(_Args(workspace_dirs=None, board_browser=None,
                                    board_browser_get=True))
        self.assertEqual(buf.getvalue().strip(), "Arc")

    # --- 1.29.0: board_rows are 6-tuples (extra_lines list + set_with) -------
    def test_board_rows_are_six_tuples(self):
        for r in config.board_rows():
            self.assertEqual(len(r), 6, "every board row is a 6-tuple now: %r" % (r,))
            # extra_lines (5th) is None or a list; set_with (6th) is None or a string.
            self.assertTrue(r[4] is None or isinstance(r[4], list))
            self.assertTrue(r[5] is None or isinstance(r[5], str))

    def test_theme_options_is_sands_only_no_ellipsis(self):
        # 1.29.0 (req 4): there is just one theme — the --theme options drop the "· …".
        rows = {r[0]: r for r in config.board_rows()}
        self.assertEqual(rows["--theme"][2], "sands")
        self.assertNotIn("…", rows["--theme"][2])

    def test_board_browser_row_present_with_examples(self):
        # 1.29.0 (part 1): a --board-browser row exists; its extra_lines list carries
        # browser examples and its value is the app name or "default".
        rows = {r[0]: r for r in config.board_rows()}
        self.assertIn("--board-browser", rows)
        row = rows["--board-browser"]
        self.assertEqual(row[1], "default")        # unset → "default"
        self.assertIsNone(row[2])                   # free-form, no options cell
        joined = " ".join(row[4])
        self.assertIn("Google Chrome", joined)
        self.assertIn("Firefox", joined)
        config.set("board_browser", "Arc")
        rows = {r[0]: r for r in config.board_rows()}
        self.assertEqual(rows["--board-browser"][1], "Arc")

    def test_statusline_and_desktop_bridge_values_on_off(self):
        rows = {r[0]: r for r in config.board_rows()}
        # 1.28.0 E/F: both simplify their COLUMN value to on/off.
        self.assertIn(rows["--statusline"][1], ("on", "off"))
        self.assertIn(rows["--desktop-bridge"][1], ("on", "off"))
        # 1.29.0 (req 4): the extra_lines are multi-line state explanations.
        sl = rows["--statusline"][4]
        self.assertIn("States:", sl)
        self.assertTrue(any(l.startswith("provider-only") for l in sl))
        self.assertTrue(any(l.startswith("Current:") for l in sl))
        db = rows["--desktop-bridge"][4]
        self.assertIn("States:", db)
        self.assertTrue(any("wired into" in l for l in db))

    def test_category_overrides_count_singular_plural(self):
        # 1.28.0 G: exact count with correct singular/plural — no "(s)".
        rows = {r[0]: r for r in config.board_rows()}
        self.assertEqual(rows["--category-overrides"][1], "none")
        config.set("categories", {"green": {"tag": "X", "label": "y"}})
        rows = {r[0]: r for r in config.board_rows()}
        self.assertEqual(rows["--category-overrides"][1], "1 override")
        config.set("categories", {"green": {"tag": "X", "label": "y"},
                                  "red": {"tag": "B", "label": "z"}})
        rows = {r[0]: r for r in config.board_rows()}
        self.assertEqual(rows["--category-overrides"][1], "2 overrides")
        self.assertNotIn("(s)", rows["--category-overrides"][1])

    def test_categories_set_with_and_extra_explain_edit_vs_toggle(self):
        # 1.29.0 (req 4): the --categories set_with carries the enable/disable commands;
        # its extra_lines explain edit vs toggle.
        rows = {r[0]: r for r in config.board_rows()}
        self.assertIn("--enable", rows["--categories"][5])
        self.assertIn("--disable", rows["--categories"][5])
        extra = " ".join(rows["--categories"][4])
        self.assertIn("edit", extra)
        self.assertIn("toggle", extra)

    # --- 1.32.0: rewritten, human config descriptions ------------------------
    def test_auto_categories_says_category_not_slot(self):
        rows = {r[0]: r for r in config.board_rows()}
        desc = rows["--auto-categories"][3]
        self.assertIn("category", desc)
        self.assertNotIn("slot", desc)
        self.assertEqual(
            desc,
            "Auto-enable a category the first time a task is assigned to it (default: on)")

    def test_bare_cmds_lists_all_aliases(self):
        rows = {r[0]: r for r in config.board_rows()}
        desc = rows["--bare-cmds"][3]
        for alias in ("/todo", "/done", "/pin", "/unpin", "/repos", "/save",
                      "/history", "/prompts", "/glossary", "/brief"):
            self.assertIn(alias, desc)
        # the namespaced fallback is stated generically.
        self.assertIn("/task-station:<name>", desc)

    def test_update_check_reworded(self):
        rows = {r[0]: r for r in config.board_rows()}
        self.assertEqual(
            rows["--update-check"][3],
            "Notifies you when a new version is available (checks once per day) (default: off)")

    def test_board_autorefresh_reworded(self):
        rows = {r[0]: r for r in config.board_rows()}
        self.assertEqual(rows["--board-autorefresh"][3],
                         "Enables auto-refresh of the /todo board (default: off)")

    def test_categories_and_overrides_options_none(self):
        # 1.32.0 H: --categories + --category-overrides drop their OPTIONS cell.
        rows = {r[0]: r for r in config.board_rows()}
        self.assertIsNone(rows["--categories"][2])
        self.assertIsNone(rows["--category-overrides"][2])

    def test_category_overrides_extra_lines_include_json_example(self):
        # 1.32.0 H: the --category-overrides expansion shows the categories.json structure.
        rows = {r[0]: r for r in config.board_rows()}
        row = rows["--category-overrides"]
        self.assertEqual(row[3],
                         "Your custom category tags/labels, saved in categories.json (default: none)")
        joined = " ".join(row[4])
        self.assertIn('{ "green": { "tag": "PROJECT", "label": "project work" } }', joined)
        self.assertIn("one entry per colour slot", joined)

    def test_category_overrides_json_example_escaped_in_html(self):
        # 1.32.0 H: the JSON example goes through _e in the HTML board (inert, escaped).
        import importlib.util as _ilu
        tools = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
        sys.path.insert(0, tools)
        import render_board as rb
        html = rb.render_html([], config_rows=config.board_rows(),
                              commands=[("/todo", "show the board")])
        # the raw quotes/braces are HTML-escaped, never emitted as live markup.
        self.assertIn("{ &quot;green&quot;: { &quot;tag&quot;: &quot;PROJECT&quot;", html)

class Notify(unittest.TestCase):
    """Worker-notification config: `notify` (macOS banner) + `notify_webhook`."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); os.environ["TASK_STATION_HOME"] = self.tmp
        for k in ("TASK_STATION_NOTIFY", "TASK_STATION_NOTIFY_WEBHOOK"):
            os.environ.pop(k, None)

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        for k in ("TASK_STATION_NOTIFY", "TASK_STATION_NOTIFY_WEBHOOK"):
            os.environ.pop(k, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_notify_default_off(self):
        self.assertFalse(config.notify_enabled())

    def test_notify_config_on(self):
        config.set("notify", True)
        self.assertTrue(config.notify_enabled())

    def test_notify_env_wins(self):
        config.set("notify", True)
        os.environ["TASK_STATION_NOTIFY"] = "off"
        self.assertFalse(config.notify_enabled())

    def test_webhook_default_none(self):
        self.assertIsNone(config.notify_webhook())

    def test_webhook_set_and_stripped(self):
        config.set("notify_webhook", "  https://example.test/hook  ")
        self.assertEqual(config.notify_webhook(), "https://example.test/hook")

    def test_webhook_empty_is_none(self):
        config.set("notify_webhook", "")
        self.assertIsNone(config.notify_webhook())

    def test_webhook_env_wins(self):
        config.set("notify_webhook", "https://config.test/hook")
        os.environ["TASK_STATION_NOTIFY_WEBHOOK"] = "https://env.test/hook"
        self.assertEqual(config.notify_webhook(), "https://env.test/hook")

    def test_board_rows_include_notify(self):
        keys = {r[0] for r in config.board_rows()}
        self.assertIn("--notify", keys)
        self.assertIn("--notify-webhook", keys)

    def test_reset_clears_notify(self):
        config.set("notify", True)
        config.set("notify_webhook", "https://example.test/hook")
        config.reset_settings()
        self.assertFalse(config.notify_enabled())
        self.assertIsNone(config.notify_webhook())


class Reset(unittest.TestCase):
    """`config --reset` factory reset: confirm-gated, preserves tasks.db."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # Save the harness-pinned values so tearDown RESTORES them rather than
        # popping — popping CLAUDE_CONFIG_DIR destroys the isolation pin and trips
        # test_store_isolation's guard in later tests (env leak across the suite).
        self._saved = {k: os.environ.get(k) for k in
                       ("TASK_STATION_HOME", "TASK_STATION_DESKTOP_CONFIG", "CLAUDE_CONFIG_DIR")}
        os.environ["TASK_STATION_HOME"] = self.tmp
        # Isolate the integration probes from this machine's real config.
        os.environ["TASK_STATION_DESKTOP_CONFIG"] = os.path.join(self.tmp, "no-desktop.json")
        os.environ["CLAUDE_CONFIG_DIR"] = self.tmp   # no bare command files here

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_bare_reset_does_not_reset(self):
        config.set("theme", "midnight")
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(_Args(workspace_dirs=None, reset="ask"))
        out = buf.getvalue()
        self.assertIn("--reset confirm", out)     # instructs how to proceed
        self.assertEqual(config.get("theme"), "midnight")   # nothing reset

    def test_reset_confirm_clears_settings(self):
        config.set("theme", "midnight"); config.set("title", False)
        config.set("tint", False); config.set("workspace_dirs", ["~/x"])
        config.set("board_browser", "Google Chrome")   # 1.29.0 board-managed key
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(_Args(workspace_dirs=None, reset="confirm"))
        self.assertIn("Reset", buf.getvalue())
        for key in ("theme", "title", "tint", "workspace_dirs", "board_browser"):
            self.assertIsNone(config.get(key))

    def test_reset_confirm_preserves_tasks_db(self):
        # Seed a real task through the storage backend, then reset and confirm it
        # survives — reset must never touch tasks.db.
        import importlib.util
        lib = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
        spec = importlib.util.spec_from_file_location("task_station_r", os.path.join(lib, "task-station.py"))
        ts = importlib.util.module_from_spec(spec); spec.loader.exec_module(ts)
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        import store; store.reset_cache()
        t = ts.new_task("survive the reset", "x"); ts.save_task(t); ts.ensure_seqs()
        tid = t["id"]
        config.set("theme", "midnight")
        with redirect_stdout(io.StringIO()):
            config.cmd_config(_Args(workspace_dirs=None, reset="confirm"))
        self.assertIsNone(config.get("theme"))            # settings cleared
        store.reset_cache()
        self.assertIsNotNone(ts.load_task(tid))           # task survives

class Board(unittest.TestCase):
    """The no-arg `task-station config` unified board (render_board)."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TASK_STATION_HOME"] = self.tmp
        # Point the desktop-bridge probe at a non-existent temp file so the board
        # never reads (or depends on) this machine's real Claude Desktop config.
        os.environ["TASK_STATION_DESKTOP_CONFIG"] = os.path.join(self.tmp, "no-desktop.json")
        self._cols = os.environ.get("COLUMNS")

    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None)
        os.environ.pop("TASK_STATION_DESKTOP_CONFIG", None)
        if self._cols is None: os.environ.pop("COLUMNS", None)
        else: os.environ["COLUMNS"] = self._cols
        shutil.rmtree(self.tmp, ignore_errors=True)

    # (a) one board, no separate status / path / header-row blocks.
    def test_single_board_no_legacy_sections(self):
        os.environ["COLUMNS"] = "120"
        board = config.render_board()
        self.assertEqual(board.count("store:"), 1)        # exactly one header
        # the redesign drops the column-header row and the trailing status block.
        for legacy in ("SETTING", "VALUE", "OPTIONS", "WHAT IT DOES",
                       "escape (full palette)", "* = default"):
            self.assertNotIn(legacy, board)

    def test_cmd_config_no_arg_renders_single_board(self):
        os.environ["COLUMNS"] = "120"
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(_Args(workspace_dirs=None))
        out = buf.getvalue()
        self.assertEqual(out.count("store:"), 1)
        self.assertIn("--reset", out)

    # (b) every setting renders as a stanza: flag + value + options line, then an
    #     indented description ending with the default in parens.
    def test_stanzas_present_with_values_and_options(self):
        os.environ["COLUMNS"] = "120"
        board = config.render_board()
        for flag in ("--categories", "--auto-categories", "--category-overrides",
                     "--bare-cmds", "--update-check", "--theme", "--tint-theme",
                     "--tint", "--title", "--guaranteed-tracking",
                     "--strict-delegation", "--desktop-bridge", "--workspace-dirs",
                     "--data-dir", "--reset"):
            self.assertIn(flag, board)
        self.assertIn("auto · dark · light", board)   # --tint-theme options
        self.assertIn("sands", board)                 # --theme active value
        self.assertIn("on · off", board)
        self.assertIn("(action)", board)              # --reset options cell

    def test_defaults_shown_as_separate_default_line(self):
        # 1.29.0 (req 4): the terminal board now STRIPS the trailing "(default: X)" from
        # the description and renders it as its own "Default: X." line.
        os.environ["COLUMNS"] = "120"
        board = config.render_board()
        for d in ("Default: CORE.", "Default: on.", "Default: off.",
                  "Default: sands.", "Default: auto.", "Default: unset.",
                  "Default: —.", "Default: none."):
            self.assertIn(d, board)
        # the trailing description parens are gone (the default moved to its own line).
        self.assertNotIn("(default: sands)", board)
        # data-dir is read-only — no default paren; the env note is in the description.
        self.assertIn("$TASK_STATION_HOME", board)
        self.assertIn("read-only", board)
        # no asterisk default markers anywhere.
        self.assertNotIn("(*)", board)

    def test_every_terminal_stanza_leads_with_set_with(self):
        # 1.29.0 (req 4): each stanza's first body line (under the flag/value/options
        # line) is a "Set with:" line.
        os.environ["COLUMNS"] = "200"
        lines = config.render_board().splitlines()
        flags = [r[0] for r in config.board_rows()]
        for flag in flags:
            i = next(i for i, l in enumerate(lines) if l.lstrip().startswith(flag + " ")
                     or l.strip() == flag)
            self.assertTrue(lines[i + 1].lstrip().startswith("Set with:"),
                            "stanza %s must lead with a Set with: line" % flag)

    # (c) --tint-theme value is just the appearance mode, not the resolved theme.
    def test_tint_theme_value_is_appearance_only(self):
        os.environ["COLUMNS"] = "120"
        config.set("tint_theme", "dark")
        lines = config.render_board().splitlines()
        row = next(l for l in lines if l.lstrip().startswith("--tint-theme"))
        self.assertIn("dark", row)
        self.assertNotIn("→", row)        # no "sands · auto → Light Sands" blob
        self.assertNotIn("Sands", row)

    # (d) blank line separates every stanza.
    def test_blank_line_between_stanzas(self):
        os.environ["COLUMNS"] = "120"
        lines = config.render_board().splitlines()
        i = next(i for i, l in enumerate(lines) if l.lstrip().startswith("--auto-categories"))
        # the line above a flag line (after its predecessor's description) is blank.
        self.assertEqual(lines[i - 1], "")

    # (e) long descriptions wrap with a hanging indent under themselves.
    def test_wrap_hangs_under_description_at_narrow_width(self):
        os.environ["COLUMNS"] = "50"
        lines = config.render_board().splitlines()
        idx = next(i for i, l in enumerate(lines) if "Let Claude Desktop read your tasks" in l)
        col = lines[idx].index("Let Claude Desktop read your tasks")
        cont = lines[idx + 1]
        self.assertNotEqual(cont.strip(), "")
        self.assertEqual(len(cont) - len(cont.lstrip()), col)  # continuation hangs

    # (f) flag/value/options columns stay aligned across widths.
    def test_columns_align_across_widths(self):
        for cols in ("60", "80", "120"):
            os.environ["COLUMNS"] = cols
            lines = config.render_board().splitlines()
            # the value cells line up: --tint and --title both show on/off at the
            # same column (flag column padded to the widest flag).
            tint = next(l for l in lines if l.lstrip().startswith("--tint "))
            title = next(l for l in lines if l.lstrip().startswith("--title "))
            self.assertEqual(tint.index(" on") if " on" in tint else tint.index(" off"),
                             title.index(" on") if " on" in title else title.index(" off"),
                             "value column misaligned at COLUMNS=%s" % cols)

    # --- columns legend distinguishes value/state from choices (1.25.0) ------
    def test_columns_legend_line_present(self):
        # 1.25.0: a legend names the middle column the current value/STATE (so e.g.
        # --statusline "provider-only" vs on·off reads as reported state, not a
        # settable input), above the existing set/reset hint.
        os.environ["COLUMNS"] = "120"
        board = config.render_board()
        self.assertIn("columns:  <flag>   ·   current value/state   ·   choices = what you pass",
                      board)
        self.assertIn("set a flag: task-station config --<flag> <value>", board)
        self.assertIn("reset a flag: --<flag> default", board)

    # --- 1.29.0: terminal board unpacks 6-tuples + renders extra_lines -------
    def test_terminal_board_renders_extra_lines(self):
        # the 6-tuple extra_lines (statusline / desktop-bridge state explanations) render
        # as wrapped lines under the stanza; the value column is on/off, so the single
        # token "provider-only" appears ONLY because the extra_lines carry it.
        os.environ["COLUMNS"] = "120"
        board = config.render_board()
        self.assertIn("provider-only", board)                  # from the statusline extra
        self.assertIn("--statusline", board)
        self.assertIn("MCP bridge wired into Claude Desktop", board)  # desktop-bridge extra

    # --- bare_commands_installed recognises a managed pin.md (1.24.0) --------
    def test_bare_commands_installed_recognises_managed_pin(self):
        # RESTORE (don't pop) CLAUDE_CONFIG_DIR — popping destroys the test harness's
        # isolation pin and trips test_store_isolation under full-suite ordering.
        saved = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = self.tmp
        try:
            cdir = os.path.join(self.tmp, "commands")
            os.makedirs(cdir, exist_ok=True)
            self.assertFalse(config.bare_commands_installed())   # nothing yet
            with open(os.path.join(cdir, "pin.md"), "w") as f:
                f.write("<!-- task-station-managed: bare alias for /task-station:pin -->\n")
            self.assertTrue(config.bare_commands_installed())     # managed pin.md counts
        finally:
            if saved is None:
                os.environ.pop("CLAUDE_CONFIG_DIR", None)
            else:
                os.environ["CLAUDE_CONFIG_DIR"] = saved

    # --- bare_commands_installed recognises managed save.md/history.md (1.57.0) --
    def test_bare_commands_installed_recognises_managed_save(self):
        saved = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = self.tmp
        try:
            cdir = os.path.join(self.tmp, "commands")
            os.makedirs(cdir, exist_ok=True)
            self.assertFalse(config.bare_commands_installed())   # nothing yet
            with open(os.path.join(cdir, "save.md"), "w") as f:
                f.write("<!-- task-station-managed: bare alias for /task-station:save -->\n")
            self.assertTrue(config.bare_commands_installed())     # managed save.md counts
        finally:
            if saved is None:
                os.environ.pop("CLAUDE_CONFIG_DIR", None)
            else:
                os.environ["CLAUDE_CONFIG_DIR"] = saved

    def test_bare_commands_installed_recognises_managed_history(self):
        saved = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = self.tmp
        try:
            cdir = os.path.join(self.tmp, "commands")
            os.makedirs(cdir, exist_ok=True)
            self.assertFalse(config.bare_commands_installed())   # nothing yet
            with open(os.path.join(cdir, "history.md"), "w") as f:
                f.write("<!-- task-station-managed: bare alias for /task-station:history -->\n")
            self.assertTrue(config.bare_commands_installed())     # managed history.md counts
        finally:
            if saved is None:
                os.environ.pop("CLAUDE_CONFIG_DIR", None)
            else:
                os.environ["CLAUDE_CONFIG_DIR"] = saved

class Obsidian(unittest.TestCase):
    """The opt-in Obsidian export settings (vault / daily-note / heading)."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); os.environ["TASK_STATION_HOME"] = self.tmp
    def tearDown(self):
        os.environ.pop("TASK_STATION_HOME", None); shutil.rmtree(self.tmp, ignore_errors=True)

    def test_vault_off_by_default(self):
        self.assertEqual(config.obsidian_vault(), "")

    def test_vault_set_expands_and_get(self):
        config.cmd_config(_Args(workspace_dirs=None, obsidian_vault="~/Documents/Vault"))
        self.assertEqual(config.obsidian_vault(), os.path.expanduser("~/Documents/Vault"))
        # stored un-expanded so a home move survives
        self.assertEqual(config.get("obsidian_vault"), "~/Documents/Vault")

    def test_vault_empty_value_turns_off(self):
        config.set("obsidian_vault", "~/x")
        config.cmd_config(_Args(workspace_dirs=None, obsidian_vault=""))   # nargs='?' const='' → off
        self.assertEqual(config.obsidian_vault(), "")
        self.assertIsNone(config.get("obsidian_vault"))

    def test_daily_note_default_off_and_toggle(self):
        self.assertFalse(config.obsidian_daily_note_enabled())
        config.cmd_config(_Args(workspace_dirs=None, obsidian_daily_note="on"))
        self.assertTrue(config.obsidian_daily_note_enabled())

    def test_daily_heading_default_and_set(self):
        self.assertEqual(config.obsidian_daily_heading(), "## Claude sessions")
        config.cmd_config(_Args(workspace_dirs=None, obsidian_daily_heading="## Work log"))
        self.assertEqual(config.obsidian_daily_heading(), "## Work log")
        config.cmd_config(_Args(workspace_dirs=None, obsidian_daily_heading=""))   # empty restores default
        self.assertEqual(config.obsidian_daily_heading(), "## Claude sessions")

    def test_board_shows_obsidian_rows(self):
        os.environ["COLUMNS"] = "120"
        board = config.render_board()
        self.assertIn("--obsidian-vault", board)
        self.assertIn("--obsidian-daily-note", board)
        self.assertIn("--obsidian-daily-heading", board)

    def test_reset_clears_obsidian_keys(self):
        config.set("obsidian_vault", "~/x")
        config.set("obsidian_daily_note", True)
        config.set("obsidian_daily_heading", "## X")
        config.reset_settings()
        for k in ("obsidian_vault", "obsidian_daily_note", "obsidian_daily_heading"):
            self.assertIsNone(config.get(k))

    # --- Interbrain federation (ported from the retired preview engine's tests) ---

    def test_interbrain_and_org_label_defaults(self):
        os.environ.pop("TASK_STATION_INTERBRAIN", None)
        self.assertEqual(config.interbrain_mode(), "auto")
        self.assertEqual(config.org_label(), "Org brain")

    def test_interbrain_env_overrides_config(self):
        config.set("interbrain", "on")
        os.environ["TASK_STATION_INTERBRAIN"] = "off"
        try:
            self.assertEqual(config.interbrain_mode(), "off")
        finally:
            os.environ.pop("TASK_STATION_INTERBRAIN", None)
        self.assertEqual(config.interbrain_mode(), "on")

    def test_interbrain_garbage_value_falls_back_to_auto(self):
        config.set("interbrain", "sideways")
        self.assertEqual(config.interbrain_mode(), "auto")

    def test_board_shows_interbrain_row_but_not_board_engine(self):
        os.environ["COLUMNS"] = "120"
        board = config.render_board()
        self.assertIn("--interbrain", board)
        self.assertIn("--org-label", board)
        self.assertNotIn("--board-engine", board)     # retired (#444): one board now

    # --- --board-engine is RETIRED (#444) -------------------------------------

    def test_board_engine_getter_is_gone(self):
        """There is one board, so there is no engine to report."""
        self.assertFalse(hasattr(config, "board_engine"))

    def test_retired_flag_prints_a_notice_and_does_not_persist(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(_Args(workspace_dirs=None, board_engine="v2"))
        self.assertIn("retired", buf.getvalue().lower())
        self.assertIsNone(config.get("board_engine"))   # never written

    def test_retired_get_flag_also_answers(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(_Args(workspace_dirs=None, board_engine_get=True))
        self.assertIn("retired", buf.getvalue().lower())

    def test_persisted_board_engine_is_ignored_harmlessly(self):
        """An existing config carrying the dead key must never crash or warn — nothing
        reads it, and every other setting still resolves."""
        config.set("board_engine", "v2")
        self.assertEqual(config.interbrain_mode(), "auto")
        os.environ["COLUMNS"] = "120"
        self.assertIn("--interbrain", config.render_board())
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(_Args(workspace_dirs=None))
        self.assertNotIn("board_engine", buf.getvalue())


class HookFlags(unittest.TestCase):
    """The two flags the new hook events added: `--config-change-enforce` (ConfigChange
    warns by default, blocks only on request) and `--worktree-hook` (the opt-in
    WorktreeCreate provisioner). Both default OFF, both take an env escape, both reset."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._env = {k: os.environ.get(k) for k in
                     ("TASK_STATION_HOME", "CLAUDE_CONFIG_DIR",
                      "TASK_STATION_CONFIG_ENFORCE", "TASK_STATION_WORKTREE_HOOK")}
        os.environ["TASK_STATION_HOME"] = self.tmp
        os.environ["CLAUDE_CONFIG_DIR"] = self.tmp
        for k in ("TASK_STATION_CONFIG_ENFORCE", "TASK_STATION_WORKTREE_HOOK"):
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- --config-change-enforce ---------------------------------------------

    def test_enforce_defaults_off(self):
        """A block is transcript-silent, so it is never the default."""
        self.assertFalse(config.config_change_enforce())

    def test_enforce_via_config_and_env(self):
        config.set("config_change_enforce", True)
        self.assertTrue(config.config_change_enforce())
        os.environ["TASK_STATION_CONFIG_ENFORCE"] = "off"
        self.assertFalse(config.config_change_enforce())      # env wins

    def test_enforce_setter_and_getter(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(_Args(workspace_dirs=None, config_change_enforce="on"))
        self.assertIn("config_change_enforce = on", buf.getvalue())
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(_Args(workspace_dirs=None, config_change_enforce_get=True))
        self.assertEqual(buf.getvalue().strip(), "on")

    def test_enforce_resets(self):
        self.assertIn("config_change_enforce", config.RESET_KEYS)
        config.set("config_change_enforce", True)
        config.reset_settings()
        self.assertFalse(config.config_change_enforce())

    # --- --worktree-hook ------------------------------------------------------

    def test_worktree_hook_defaults_off(self):
        self.assertFalse(config.worktree_hook_enabled())

    def test_worktree_hook_via_config_and_env(self):
        config.set("worktree_hook", True)
        self.assertTrue(config.worktree_hook_enabled())
        os.environ["TASK_STATION_WORKTREE_HOOK"] = "off"
        self.assertFalse(config.worktree_hook_enabled())      # env wins

    def test_worktree_hook_resets(self):
        self.assertIn("worktree_hook", config.RESET_KEYS)
        config.set("worktree_hook", True)
        config.reset_settings()
        self.assertFalse(config.worktree_hook_enabled())

    def test_worktree_hook_on_installs_and_off_removes(self):
        import setup
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(_Args(workspace_dirs=None, worktree_hook="on"))
        self.assertEqual(setup.worktree_hook_status(), "installed")
        self.assertIn("Reverse", buf.getvalue())
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(_Args(workspace_dirs=None, worktree_hook="off"))
        self.assertEqual(setup.worktree_hook_status(), "off")
        self.assertFalse(config.worktree_hook_enabled())

    def test_reset_reports_the_settings_json_leftover(self):
        """A settings reset pops the flag but cannot remove what lives outside
        config.json — so it names it with its off-command instead."""
        import setup
        setup.install_worktree_hook()
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_reset("confirm")
        self.assertIn("--worktree-hook off", buf.getvalue())

    # --- both rows are on the board ------------------------------------------

    def test_board_rows_present_and_well_formed(self):
        rows = {r[0]: r for r in config.board_rows()}
        for flag in ("--worktree-hook", "--config-change-enforce"):
            self.assertIn(flag, rows)
            self.assertEqual(len(rows[flag]), 6)          # a valid 6-tuple
            self.assertEqual(rows[flag][2], "on · off")
            self.assertIn("(default: off)", rows[flag][3])
        self.assertEqual(rows["--worktree-hook"][1], "off")
        self.assertEqual(rows["--config-change-enforce"][1], "off")

    def test_board_renders_both(self):
        os.environ["COLUMNS"] = "120"
        board = config.render_board()
        self.assertIn("--worktree-hook", board)
        self.assertIn("--config-change-enforce", board)


if __name__=="__main__": unittest.main()
