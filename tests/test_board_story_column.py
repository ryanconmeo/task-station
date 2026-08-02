# tests/test_board_story_column.py
"""Story on the HTML /todo board. Story was briefly a top-level STORY column (WS13);
it has since been REMOVED as a column — each task's story id(s) now surface only inside
the expanded Overview digest (the `stories` row, from the structured `stories` field,
each linked to its ADO url when known). These assertions prove there is NO story column
in the output and that stories still render in the Overview, plus an end-to-end check
that the view-model still populates story_refs."""
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
sys.path.insert(0, LIB)
sys.path.insert(0, TOOLS)

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)

import render_board  # noqa: E402


def _vm(**kw):
    base = {
        "seq": kw.pop("seq", 1), "title": "A task", "full_title": "A task",
        "status": "open", "status_label": "open", "usage": None, "sessions": [],
        "phases": [], "prompts_preview": [], "hubs": [], "cost_thresholds": [0.01, 0.05],
    }
    base.update(kw)
    return base


_URL = "https://dev.azure.com/Org/Proj/_workitems/edit/1234"


class NoStoryColumn(unittest.TestCase):
    def test_no_story_column_class_anywhere(self):
        # Story is no longer a grid column — neither the header cell nor a row cell.
        html = render_board.render_html([_vm(stories=[{"url": _URL, "desc": "d"}])])
        self.assertNotIn('class="c-story"', html)

    def test_no_story_header_label(self):
        html = render_board.render_html([_vm()])
        # the header row now ends effort → activity, with no story heading between them
        self.assertNotIn('<span class="c-story">story</span>', html)


class StoryInOverview(unittest.TestCase):
    def test_story_with_url_renders_link_in_overview(self):
        html = render_board.render_html([_vm(stories=[{"url": _URL, "desc": "first story"}])])
        self.assertIn('<span class="k">stories</span>', html)   # the Overview stories row
        self.assertIn('<a href="%s"' % _URL, html)              # linked story url
        self.assertIn("first story", html)                      # its description

    def test_storyless_task_has_no_stories_row(self):
        html = render_board.render_html([_vm()])
        self.assertNotIn('<span class="k">stories</span>', html)


class BoardViewModel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="board-story-vm-")
        os.environ["TASK_STATION_HOME"] = self.tmp
        ts.DATA = self.tmp
        ts.STORE = os.path.join(self.tmp, "store")
        ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
        ts.LINKS_DIR = os.path.join(ts.STORE, "links")
        import store
        store.reset_cache()

    def tearDown(self):
        import store
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_view_model_carries_story_refs(self):
        t = ts.new_task("Wire the API", "s", color="green")
        t["stories"] = [{"url": _URL, "desc": "d"}, {"url": "387", "desc": ""}]
        ts.save_task(t)
        ts.ensure_seqs()
        vm = ts._board_view_model(ts.load_task(t["id"]))
        refs = vm.get("story_refs")
        self.assertEqual(refs, [{"id": "1234", "url": _URL}, {"id": "387", "url": None}])


if __name__ == "__main__":
    unittest.main()
