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

    # (a) the board no longer carries the separate setup.status() block.
    def test_no_status_duplicate_header(self):
        os.environ["COLUMNS"] = "120"
        self.assertNotIn("— status", config.render_board())

    def test_cmd_config_no_arg_renders_single_board(self):
        os.environ["COLUMNS"] = "120"
        buf = io.StringIO()
        with redirect_stdout(buf):
            config.cmd_config(_Args(workspace_dirs=None))
        out = buf.getvalue()
        self.assertNotIn("— status", out)        # no second status board
        self.assertEqual(out.count("store:"), 1)  # exactly one board header

    # (b) the OPTIONS column values appear.
    def test_options_values_present(self):
        os.environ["COLUMNS"] = "120"
        board = config.render_board()
        self.assertIn("OPTIONS", board)
        self.assertIn("auto · dark · light", board)   # --tint-theme options
        self.assertIn("default", board)               # --theme value/options
        self.assertIn("on · off", board)
        self.assertIn("edit·toggle", board)

    def test_header_row_present(self):
        os.environ["COLUMNS"] = "120"
        board = config.render_board()
        for h in ("SETTING", "VALUE", "OPTIONS", "WHAT IT DOES"):
            self.assertIn(h, board)

    # (c) long descriptions wrap with a hanging indent aligned under WHAT IT DOES.
    def test_wrap_hangs_under_what_it_does_at_narrow_width(self):
        os.environ["COLUMNS"] = "60"
        lines = config.render_board().splitlines()
        header = next(l for l in lines if "WHAT IT DOES" in l)
        col = header.index("WHAT IT DOES")
        # the desktop-bridge description is the longest → guaranteed to wrap.
        idx = next(i for i, l in enumerate(lines) if "wire the dependency-free" in l)
        # first description line starts exactly under the WHAT IT DOES column.
        self.assertEqual(lines[idx].index("wire the dependency-free"), col)
        # the continuation line hangs to the same column (never under flag/value).
        cont = lines[idx + 1]
        self.assertTrue(cont.startswith(" " * col))
        self.assertNotEqual(cont.strip(), "")
        self.assertEqual(len(cont) - len(cont.lstrip()), col)

    # (d) a long workspace path prints on its own line, not inside the grid.
    def test_long_workspace_path_on_own_line(self):
        os.environ["COLUMNS"] = "80"
        longpath = "/Users/somebody/very/long/workspace/path/that/exceeds/the/grid/width/repos"
        config.set("workspace_dirs", [longpath])
        lines = config.render_board().splitlines()
        self.assertTrue(any("--workspace-dirs" in l for l in lines))
        match = [l for l in lines if longpath in l]
        self.assertTrue(match)
        self.assertEqual(match[0].strip(), longpath)  # nothing else shares the line

    def test_status_footer_facts_present_with_hints(self):
        os.environ["COLUMNS"] = "120"
        board = config.render_board()
        self.assertIn("status", board)
        self.assertIn("policy", board)
        self.assertIn("desktop-bridge", board)
        self.assertIn("tint", board)
        # actionable hints survive the fold-in.
        self.assertIn("--policy on", board)
        # tint now describes the full-palette escape; no profile mechanism remains.
        self.assertIn("escape (full palette)", board)
        self.assertNotIn("tint-profiles", board)

    def test_columns_align_across_widths(self):
        # The WHAT IT DOES column starts at the same offset for the header and
        # every first-line data row, at any width.
        for cols in ("60", "80", "120"):
            os.environ["COLUMNS"] = cols
            lines = config.render_board().splitlines()
            header = next(l for l in lines if "WHAT IT DOES" in l)
            col = header.index("WHAT IT DOES")
            # prefixes that survive textwrap at every width (each starts its desc).
            for needle in ("enabled set", "install bare",
                           "active color theme", "wire the dependency-free"):
                row = next(l for l in lines if needle in l)
                self.assertEqual(row.index(needle), col,
                                 "misaligned at COLUMNS=%s: %r" % (cols, needle))

if __name__=="__main__": unittest.main()
