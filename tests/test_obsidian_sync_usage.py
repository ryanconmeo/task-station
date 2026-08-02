"""WS8 additions to the Obsidian note: the derived ## Usage block, the opt-in
## Prompts trail, and the new flat frontmatter keys (models / cost-usd /
time-spent). Renderer-level tests — no store needed; a usage dict shaped like
usage.task_usage()'s output and a list of prompt rows are passed in directly.
Runs against a throwaway vault so nothing touches a real one.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import obsidian_sync


def _task(**over):
    t = {
        "id": "abcdef1234",
        "seq": 12,
        "title": "Fix the login bug",
        "summary": "Users can't log in.",
        "status": "open",
        "color": "red",
        "effort": "m",
        "created_ts": 1_700_000_000.0,
        "updated_ts": 1_700_100_000.0,
        "projects": ["projectname"],
        "goal": "Login works again.",
        "state": "NEXT: reproduce.",
        "decisions": [],
        "history": [],
    }
    t.update(over)
    return t


def _usage(**over):
    """A representative usage.task_usage() dict."""
    u = {
        "models": {
            "claude-fable-5": {"pct": 0.8, "cost_usd": 4.0, "in": 100, "out": 400,
                               "cache_read": 30, "cache_w5m": 0, "cache_w1h": 0,
                               "web": 0, "msgs": 4},
            "claude-opus-4-8": {"pct": 0.2, "cost_usd": 1.0, "in": 50, "out": 100,
                                "cache_read": 10, "cache_w5m": 0, "cache_w1h": 0,
                                "web": 0, "msgs": 1},
        },
        "phases": {"implement": {"pct": 0.6, "out": 300, "msgs": 3, "cost_usd": 3.0},
                   "debug": {"pct": 0.4, "out": 200, "msgs": 2, "cost_usd": 2.0}},
        "sessions": [],
        "total_cost_usd": 5.0,
        "total_in": 150,
        "total_out": 500,
        "reported_cost_usd": 6.0,
        "any_unpriced": False,
        "derived_note": "…",
    }
    u.update(over)
    return u


class UsageFrontmatter(unittest.TestCase):
    def test_usage_frontmatter_keys_flat(self):
        # spans → 90 minutes of active time (5400s)
        text = obsidian_sync.render_note(
            _task(spans=[[1000.0, 4600.0], [5000.0, 6800.0]]), usage=_usage())
        # models is a flat YAML list, ranked by pct (fable 80% first)
        self.assertIn('models:\n  - "claude-fable-5"\n  - "claude-opus-4-8"', text)
        # cost-usd is a bare numeric scalar (Dataview-queryable), not quoted
        self.assertIn("cost-usd: 5.0", text)
        # time-spent is integer minutes from spans (3600 + 1800 = 5400s = 90m)
        self.assertIn("time-spent: 90", text)

    def test_usage_frontmatter_empty_when_no_usage(self):
        text = obsidian_sync.render_note(_task())     # no usage, no spans
        self.assertIn("models: []", text)
        self.assertIn('cost-usd: ""', text)
        self.assertIn("time-spent: 0", text)

    def test_usage_section_always_present(self):
        text = obsidian_sync.render_note(_task(), usage=_usage())
        self.assertIn("## Usage", text)
        self.assertIn("fable 80%", text)
        self.assertIn("opus 20%", text)
        self.assertIn("- Tokens: in 150 · out 500 · cache-read 40", text)
        self.assertIn("$5.00 derived", text)
        self.assertIn("$6.00 reported", text)
        # phase mix rendered
        self.assertIn("Implement 60%", text)
        self.assertIn("Debug 40%", text)

    def test_usage_section_placeholder_when_no_data(self):
        text = obsidian_sync.render_note(_task())
        self.assertIn("## Usage", text)
        self.assertIn("_(no usage tracked yet)_", text)

    def test_unpriced_model_marked(self):
        u = _usage(models={"weird-model-9": {"pct": 1.0, "cost_usd": None, "in": 1,
                                             "out": 2, "cache_read": 0}},
                   total_cost_usd=0.0, any_unpriced=True, reported_cost_usd=0.0)
        text = obsidian_sync.render_note(_task(), usage=u)
        self.assertIn("($n/a)", text)
        self.assertIn("excludes unknown model(s)", text)


class PromptsSection(unittest.TestCase):
    def _prompts(self):
        return [
            {"ts": 1_700_000_100.0, "kind": "prompt", "text": "Please fix the login bug"},
            {"ts": 1_700_000_200.0, "kind": "command", "text": "/todo save"},
            {"ts": 1_700_000_300.0, "kind": "prompt", "text": "line one\nline two"},
        ]

    def test_prompts_section_omitted_by_default(self):
        text = obsidian_sync.render_note(_task(), usage=_usage())   # prompts=None
        self.assertNotIn("## Prompts", text)

    def test_prompts_section_when_provided(self):
        text = obsidian_sync.render_note(_task(), usage=_usage(), prompts=self._prompts())
        self.assertIn("## Prompts", text)
        self.assertIn("Please fix the login bug", text)
        self.assertIn("[command] /todo save", text)
        # newlines folded to a single line
        self.assertIn("line one line two", text)

    def test_prompts_section_empty_list_renders_none(self):
        text = obsidian_sync.render_note(_task(), usage=_usage(), prompts=[])
        self.assertIn("## Prompts", text)
        self.assertIn("_(none)_", text)


class IncludeToggles(unittest.TestCase):
    def test_include_usage_false_omits_section(self):
        text = obsidian_sync.render_note(_task(), usage=_usage(), include_usage=False)
        self.assertNotIn("## Usage", text)
        # but the frontmatter usage keys stay (they're cheap flat scalars)
        self.assertIn("cost-usd: 5.0", text)

    def test_include_history_false_omits_section(self):
        text = obsidian_sync.render_note(
            _task(history=[{"ts": "2026-07-01T10:00:00+00:00", "text": "did a thing"}]),
            include_history=False)
        self.assertNotIn("## History", text)
        self.assertNotIn("did a thing", text)


class ExportTaskForwards(unittest.TestCase):
    def setUp(self):
        self.vault = tempfile.mkdtemp(prefix="obs-usage-")
        self.pdir = obsidian_sync.plugin_dir(self.vault)

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)

    def test_export_task_writes_usage_and_prompts(self):
        fname = obsidian_sync.export_task(
            _task(), self.vault, usage=_usage(),
            prompts=[{"ts": 1_700_000_100.0, "kind": "prompt", "text": "hi there"}])
        with open(os.path.join(self.pdir, fname), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("## Usage", text)
        self.assertIn("fable 80%", text)
        self.assertIn("## Prompts", text)
        self.assertIn("hi there", text)

    def test_export_task_without_usage_still_valid(self):
        # a bare export (tests / callers that pass nothing) still writes a note
        fname = obsidian_sync.export_task(_task(), self.vault)
        with open(os.path.join(self.pdir, fname), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("## Usage", text)
        self.assertIn("_(no usage tracked yet)_", text)
        self.assertNotIn("## Prompts", text)


if __name__ == "__main__":
    unittest.main()
