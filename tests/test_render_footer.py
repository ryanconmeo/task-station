"""_format_list() prints the authoritative Commands: footer (single source of truth)."""
import importlib.util
import os
import re
import shutil
import sys
import tempfile
import unittest

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

import store  # noqa: E402  (normal import — store.py has no hyphen)

# task-station.py has a hyphen, so it can't be a normal import — load it by path.
_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


def _repoint(tmp):
    """Point task-station.py's import-frozen path globals at a fresh tmp store so
    writes can NEVER reach the real ~/.claude store, regardless of how the test is
    invoked (flat-module discovery skips tests/__init__.py)."""
    os.environ["TASK_STATION_HOME"] = tmp
    ts.DATA = tmp
    ts.STORE = os.path.join(tmp, "store")
    ts.TASKS_DIR = os.path.join(ts.STORE, "tasks")
    ts.LINKS_DIR = os.path.join(ts.STORE, "links")
    ts.PROJECTS_ROOT = os.path.join(tmp, "projects")
    store.reset_cache()


class RenderFooterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _repoint(self.tmp)

    def tearDown(self):
        store.reset_cache()
        os.environ.pop("TASK_STATION_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _set_bare(self, value):
        # 1.58.0: the footer is bare-aware — force the state deterministically
        # instead of depending on the config file's default.
        orig = ts._bare_commands
        ts._bare_commands = lambda: value
        self.addCleanup(setattr, ts, "_bare_commands", orig)

    def test_footer_bare_on_lists_bare_forms_plus_on_note(self):
        self._set_bare(True)
        ts.save_task(ts.new_task("First task", "do the thing"))
        ts.save_task(ts.new_task("Second task", "do another thing"))
        out = ts._format_list()
        self.assertIn("/todo                   show the board", out)
        self.assertIn("/todo <n1, n2, …> -s    jump into task session(s), in a new window", out)
        self.assertIn("/todo done [n,…]        close the current task (or by number)", out)
        self.assertIn("/todo config [flags]    open settings", out)
        self.assertIn("<n> a task number  ·  <n1, n2, …> one or more  ·  [N] optional count", out)
        # each action appears once — no standalone dupes of the /todo subcommand forms
        self.assertNotIn("/done                   close the current task", out)
        self.assertNotIn("/pin                    pin this session", out)
        # no command label is rewritten (the note may still mention the prefix by name).
        self.assertNotIn("/task-station:todo", out)
        # the on-state note names the full aliasable set, as two separate lines —
        # the /task-station: prefix statement stands on its own line.
        self.assertIn(
            "bare-cmds is on — /todo, /done, /save, /heal, /pin, /history, /repos work directly.\n"
            "The /task-station: prefix also always works.", out)

    def _assert_row(self, out, label, desc):
        # Padding width shifts with the /task-station: rewrite, so match the
        # label immediately followed by >=2 spaces then the exact description,
        # rather than hardcoding the recomputed column width.
        self.assertRegex(out, re.escape(label) + r" {2,}" + re.escape(desc))

    def test_footer_bare_off_lists_namespaced_forms_plus_off_note(self):
        self._set_bare(False)
        ts.save_task(ts.new_task("First task", "do the thing"))
        out = ts._format_list()
        self._assert_row(out, "/task-station:todo", "show the board")
        self._assert_row(out, "/task-station:todo done [n,…]", "close the current task (or by number)")
        self._assert_row(out, "/task-station:todo config [flags]", "open settings")
        self.assertIn("<n> a task number  ·  <n1, n2, …> one or more  ·  [N] optional count", out)
        # every bare token is rewritten — no un-namespaced /todo left in the footer.
        self.assertNotIn("\n/todo", out)
        # the off-state note names the full aliasable set + how to enable it, as two
        # separate lines — the enable-line stands on its own line.
        self.assertIn(
            "bare-cmds is off — use the /task-station: prefix (shown).\n"
            "Enable the short /todo, /done, /save, /heal, /pin, /history, /repos aliases with "
            "/task-station:config --bare-cmds on.", out)

    def test_commands_help_lists_each_action_once(self):
        # pin/done/config each used to appear TWICE — once as a /todo subcommand,
        # once as a standalone row. Only the /todo forms should remain.
        cmds = [cmd for cmd, _ in ts._COMMANDS_HELP]
        self.assertNotIn("/done", cmds)
        self.assertNotIn("/done <n1, n2, …>", cmds)
        self.assertNotIn("/pin", cmds)
        self.assertNotIn("/task-station:config", cmds)
        self.assertIn("/todo pin", cmds)
        self.assertIn("/todo done [n,…]", cmds)
        self.assertIn("/todo config [flags]", cmds)
        actions = ["pin", "done", "config"]
        for action in actions:
            matches = [c for c in cmds if action in c]
            self.assertEqual(len(matches), 1,
                              "%r should appear exactly once in _COMMANDS_HELP, got %r"
                              % (action, matches))

    def test_commands_footer_md_is_verbatim_fenced_block_bare_on(self):
        # The Markdown footer is the aligned help block under a **Commands**
        # heading, wrapped in a ``` fence so it renders monospace verbatim; the
        # bare-state note trails outside the fence.
        self._set_bare(True)
        expected = (
            "**Commands**\n"
            "\n"
            "```\n"
            "/todo                   show the board\n"
            "/todo <n>               open & resume a task\n"
            "/todo <n> history       full trace: decisions + log + activity\n"
            "/todo <n> prompts       exact prompt trail (timestamped, per session)\n"
            "/todo <n1, n2, …> -s    jump into task session(s), in a new window\n"
            "/todo closed [N]        list recent closed (default 20)\n"
            "/todo all               show every task (all open + closed)\n"
            "/todo search <terms>    search all tasks (add --open/--closed)\n"
            "/todo board             open the visual HTML board\n"
            "/todo save              checkpoint the current task for a seamless resume\n"
            "/todo heal              reconcile the decision log into current state (dry run)\n"
            "/todo pin               pin this session as the task's resume target\n"
            "/todo done [n,…]        close the current task (or by number)\n"
            "/todo config [flags]    open settings\n"
            "\n"
            "<n> a task number  ·  <n1, n2, …> one or more  ·  [N] optional count\n"
            "```\n"
            "\n"
            "bare-cmds is on — /todo, /done, /save, /heal, /pin, /history, /repos work directly.\n"
            "The /task-station: prefix also always works."
        )
        self.assertEqual(ts.commands_footer_md(), expected)

    def test_commands_footer_md_bare_off_uses_namespaced_forms(self):
        self._set_bare(False)
        md = ts.commands_footer_md()
        self.assertRegex(md, r"```\n/task-station:todo {2,}show the board")
        self._assert_row(md, "/task-station:todo config [flags]", "open settings")
        self.assertIn(
            "bare-cmds is off — use the /task-station: prefix (shown).\n"
            "Enable the short /todo, /done, /save, /heal, /pin, /history, /repos aliases with "
            "/task-station:config --bare-cmds on.",
            md)

    def test_commands_footer_md_decoupled_and_consistent(self):
        self._set_bare(True)
        md = ts.commands_footer_md()
        # No bullets, no old dense one-liner.
        self.assertNotIn("\n- ", md)
        self.assertNotIn("Commands:  /todo", md)
        # The fenced body is exactly the ASCII footer's command+legend block —
        # both surfaces build the note via the same helper, appended after.
        body = md.split("```\n", 1)[1].rsplit("\n```", 1)[0]
        ascii_body = ts.commands_footer().rsplit("\n\n", 1)[0]
        self.assertEqual(body, ascii_body)


if __name__ == "__main__":
    unittest.main()
