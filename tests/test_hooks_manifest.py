"""The hook manifest — what task-station wires, and the two things about it that are
easy to get silently wrong.

1. THE FileChanged MATCHER IS A LITERAL FILENAME LIST, NOT A REGEX — but only while it
   stays inside the character set that keeps it literal. A hyphen, a space or a comma
   anywhere in that string flips Claude Code to regex parsing, and the matcher then
   quietly stops matching the files it names. Nothing fails; the hook simply never
   fires again. So the character set is a test.
2. WorktreeCreate IS NOT IN THE MANIFEST, and must never be. That hook REPLACES
   worktree creation, so shipping it in the plugin would put our script in front of
   every worktree — including Claude's own subagent isolation — on every install. It
   ships as an opt-in installer instead (`config --worktree-hook on`).
"""
import json
import os
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HOOKS_DIR = os.path.join(_REPO_ROOT, "hooks")


def _manifest():
    with open(os.path.join(_HOOKS_DIR, "hooks.json"), encoding="utf-8") as f:
        return json.load(f)["hooks"]


def _commands(entries):
    return [h.get("command", "") for e in entries for h in (e.get("hooks") or [])]


def _read(name):
    with open(os.path.join(_HOOKS_DIR, name), encoding="utf-8") as f:
        return f.read()


class ManifestShape(unittest.TestCase):
    def setUp(self):
        self.hooks = _manifest()

    def test_eight_events_are_wired(self):
        self.assertEqual(set(self.hooks), {
            "SessionStart", "UserPromptSubmit", "PostToolUse", "Stop", "PostCompact",
            "SessionEnd", "ConfigChange", "FileChanged"})

    def test_every_command_points_at_a_script_that_exists(self):
        for event, entries in self.hooks.items():
            for cmd in _commands(entries):
                self.assertIn("${CLAUDE_PLUGIN_ROOT}/hooks/", cmd, event)
                name = cmd.rsplit("/", 1)[-1].rstrip('"')
                self.assertTrue(os.path.isfile(os.path.join(_HOOKS_DIR, name)),
                                "%s → %s" % (event, name))

    def test_worktree_create_is_never_shipped_in_the_manifest(self):
        self.assertNotIn("WorktreeCreate", self.hooks)
        self.assertTrue(os.path.isfile(os.path.join(_HOOKS_DIR, "on_worktree_create.sh")),
                        "the script still ships — the installer points at it")


class SessionEndEntry(unittest.TestCase):
    def setUp(self):
        self.entries = _manifest()["SessionEnd"]

    def test_one_unmatched_entry_so_every_reason_fires(self):
        self.assertEqual(len(self.entries), 1)
        self.assertNotIn("matcher", self.entries[0])

    def test_it_raises_the_shared_budget(self):
        """All SessionEnd hooks SHARE 1.5s; a per-hook timeout raises it (cap 60)."""
        hook = self.entries[0]["hooks"][0]
        self.assertEqual(hook.get("timeout"), 10)
        self.assertIn("on_session_end.sh", hook["command"])


class ConfigChangeEntries(unittest.TestCase):
    def setUp(self):
        self.entries = _manifest()["ConfigChange"]

    def test_exactly_the_three_blockable_sources(self):
        self.assertEqual({e.get("matcher") for e in self.entries},
                         {"user_settings", "project_settings", "local_settings"})

    def test_policy_settings_is_never_wired(self):
        """It cannot be blocked, so wiring it would only be a way to look like we
        might."""
        matchers = {e.get("matcher") for e in self.entries}
        self.assertNotIn("policy_settings", matchers)
        self.assertNotIn("skills", matchers)

    def test_all_three_run_the_same_script(self):
        self.assertEqual({c.rsplit("/", 1)[-1].rstrip('"') for c in _commands(self.entries)},
                         {"on_config_change.sh"})


class FileChangedMatcher(unittest.TestCase):
    def setUp(self):
        self.entries = _manifest()["FileChanged"]
        self.matcher = self.entries[0]["matcher"]

    def test_it_names_the_station_config_files(self):
        self.assertEqual(set(self.matcher.split("|")),
                         {"config.json", "categories.json", "repos.json",
                          "brains.json", "workers.json"})

    def test_it_stays_a_literal_filename_list(self):
        """A hyphen, space or comma silently flips this to regex parsing — after which
        the hook stops firing and nothing reports it."""
        for bad in ("-", " ", ","):
            self.assertNotIn(bad, self.matcher)

    def test_the_matcher_names_match_the_engine_side_list(self):
        import importlib.util
        import sys
        lib = os.path.join(_REPO_ROOT, "lib")
        sys.path.insert(0, lib)
        spec = importlib.util.spec_from_file_location(
            "task_station_manifest_check", os.path.join(lib, "task-station.py"))
        ts = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ts)
        self.assertEqual(set(self.matcher.split("|")), set(ts.STATION_WATCHED_FILES))


class ScriptDiscipline(unittest.TestCase):
    """The house idiom every manifest-shipped hook keeps — and the one script that
    deliberately breaks it."""

    MANIFEST_SCRIPTS = ("on_session_end.sh", "on_config_change.sh", "on_file_changed.sh")

    def test_manifest_hooks_early_exit_inside_a_worker(self):
        for name in self.MANIFEST_SCRIPTS:
            self.assertIn('[ -n "$TASK_STATION_SUPPRESS" ] && exit 0', _read(name), name)

    def test_manifest_hooks_source_the_shared_lib(self):
        for name in self.MANIFEST_SCRIPTS:
            self.assertIn("_ts_lib.sh", _read(name), name)

    def test_manifest_hooks_parse_stdin_with_hookjson(self):
        """python3 is the sole hard requirement; `jq` is deliberately not one."""
        for name in self.MANIFEST_SCRIPTS:
            self.assertIn("hookjson.py", _read(name), name)

    @staticmethod
    def _code(name):
        """The script MINUS its comment lines — the script's own header comments
        explain the absent guard and the absent CLAUDE_PLUGIN_ROOT by NAME, so a
        bare-token assertNotIn over the full body fails on the documentation of
        exactly the property it checks."""
        return "\n".join(l for l in _read(name).splitlines()
                         if not l.strip().startswith("#"))

    def test_the_worktree_hook_has_no_suppress_guard(self):
        """Exiting early there would print NO path, which IS a failed worktree
        creation — and a suppressed worker asking for a worktree still needs one."""
        self.assertNotIn("TASK_STATION_SUPPRESS", self._code("on_worktree_create.sh"))

    def test_the_worktree_hook_resolves_the_engine_relative_to_itself(self):
        """It runs from the user's settings.json, where CLAUDE_PLUGIN_ROOT is unset."""
        code = self._code("on_worktree_create.sh")
        self.assertNotIn("CLAUDE_PLUGIN_ROOT", code)
        self.assertIn("BASH_SOURCE", code)
        self.assertIn("worktree-create", code)

    def test_the_worktree_hook_never_cds(self):
        """The installed path contains `task-station-engine/..`, and bash's logical
        `cd` collapses `..` TEXTUALLY — it would land on `<config>/hooks`, which does
        not exist, and every worktree creation would fail. Left as a plain string, the
        kernel resolves the symlink first and `..` second."""
        for line in _read("on_worktree_create.sh").splitlines():
            if line.strip().startswith("#"):
                continue
            self.assertNotIn("cd ", line)

    def test_the_worktree_hook_does_not_consume_stdin(self):
        """`exec` must hand the payload to the engine — a `cat` here would eat it."""
        body = _read("on_worktree_create.sh")
        self.assertIn("exec python3", body)
        self.assertNotIn("input=$(cat)", body)

    def test_every_hook_script_is_a_bash_script(self):
        for name in sorted(os.listdir(_HOOKS_DIR)):
            if not name.endswith(".sh") or name.startswith("_"):
                continue
            self.assertTrue(_read(name).startswith("#!/usr/bin/env bash"), name)


if __name__ == "__main__":
    unittest.main()
