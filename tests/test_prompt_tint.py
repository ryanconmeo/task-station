"""prompt-tint's in-session re-tint fallback (v1.9.0 group 4): when a prompt
invokes NO skill, cmd_prompt_tint falls back to the ATTACHED task's category colour
and emits that theme tint — so a plain `/todo <n>` repaints the current window.
A skill prompt still wins; tinting honours TASK_STATION_TINT=off."""
import importlib
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

import store      # noqa: E402
import config     # noqa: E402
import categories  # noqa: E402

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


def _repoint(tmp):
    os.environ["TASK_STATION_HOME"] = tmp
    ts.DATA = tmp
    ts.STORE = os.path.join(tmp, "store")
    ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
    ts.LINKS_DIR = os.path.join(ts.STORE, "links")
    ts.PROJECTS_ROOT = os.path.join(tmp, "projects")
    store.reset_cache()


class _Args:
    def __init__(self, session=None, prompt=None):
        self.session = session
        self.prompt = prompt


class PromptTintFallback(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _repoint(self.tmp)
        os.environ["TASK_STATION_TERM"] = "iterm"     # force terminal detection
        os.environ.pop("TASK_STATION_TINT", None)
        # Other test modules reload `categories` with overrides (e.g. tint_terminal:
        # false) and don't restore it — single-process discovery would leak that
        # state here. Reload against the clean tmp config so TINT_TERMINAL is True.
        importlib.reload(categories)

    def tearDown(self):
        store.reset_cache()
        for k in ("TASK_STATION_HOME", "TASK_STATION_TERM", "TASK_STATION_TINT"):
            os.environ.pop(k, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self, color):
        t = ts.new_task("a task", "x", color=color)
        ts.save_task(t)
        ts.ensure_seqs()
        return ts.load_task(t["id"])

    def _run(self, session=None, prompt=None):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ts.cmd_prompt_tint(_Args(session=session, prompt=prompt))
        return buf.getvalue()

    def test_non_skill_prompt_falls_back_to_task_color(self):
        t = self._seed("red")
        ts.set_link("sess-1", t["id"])
        out = self._run(session="sess-1", prompt="/todo 1")   # not a skill mapping
        self.assertEqual(out, categories.tint_escape("red", "auto", "iterm"))
        self.assertIn("\033]11;%s\007" % categories.THEMES["dusk"]["red"]["bg"], out)

    def test_fallback_follows_active_theme(self):
        t = self._seed("red")
        ts.set_link("sess-th", t["id"])
        config.set("theme", "sands")
        out = self._run(session="sess-th", prompt="repaint please")
        self.assertIn("\033]11;%s\007" % categories.THEMES["sands"]["red"]["bg"], out)

    def test_skill_prompt_wins_over_task(self):
        t = self._seed("red")
        ts.set_link("sess-2", t["id"])
        out = self._run(session="sess-2", prompt="/review")   # skill → orange
        self.assertIn("\033]11;%s\007" % categories.THEMES["dusk"]["orange"]["bg"], out)
        self.assertNotIn("\033]11;%s\007" % categories.THEMES["dusk"]["red"]["bg"], out)

    def test_unattached_session_emits_nothing(self):
        self.assertEqual(self._run(session="nobody", prompt="hello world"), "")

    def test_attached_task_without_color_emits_nothing(self):
        t = self._seed("red")
        t.pop("color", None); ts.save_task(t)     # a task carrying no colour
        ts.set_link("sess-3", t["id"])
        self.assertEqual(self._run(session="sess-3", prompt="hello world"), "")

    def test_tint_off_emits_nothing(self):
        t = self._seed("red")
        ts.set_link("sess-4", t["id"])
        os.environ["TASK_STATION_TINT"] = "off"
        self.assertEqual(self._run(session="sess-4", prompt="/todo 1"), "")


if __name__ == "__main__":
    unittest.main()
