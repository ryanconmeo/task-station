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
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(_Args(workspace_dirs=None, reset="confirm"))
        self.assertIn("Reset", buf.getvalue())
        for key in ("theme", "title", "tint", "workspace_dirs"):
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
        self.assertIn("edit · toggle", board)
        self.assertIn("(action)", board)              # --reset options cell

    def test_defaults_shown_in_description_parens(self):
        os.environ["COLUMNS"] = "120"
        board = config.render_board()
        for d in ("(default: CORE)", "(default: on)", "(default: off)",
                  "(default: sands)", "(default: auto)", "(default: unset)",
                  "(default: —)", "(default: none)"):
            self.assertIn(d, board)
        # data-dir is read-only — no default paren, just the env note.
        self.assertIn("(read-only · $TASK_STATION_HOME)", board)
        # no asterisk default markers anywhere.
        self.assertNotIn("(*)", board)

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
        idx = next(i for i, l in enumerate(lines) if "wire the dependency-free" in l)
        col = lines[idx].index("wire the dependency-free")
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

if __name__=="__main__": unittest.main()
