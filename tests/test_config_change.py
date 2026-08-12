"""ConfigChange — the config-path validator (`task-station config-change`).

A settings file just changed and has NOT taken effect yet. The one class of mistake
worth catching there is a path that no longer resolves: the feature it configures
fails silently, and the user finds out days later.

Two halves are tested:

  * lib/config_change.py — WHAT counts as a declared path. Every exclusion here is a
    class of false positive (a relative path, a `$VAR`, a glob, a bare command name),
    and a check that cries wolf is worse than no check at all.
  * the CLI handler — WARN by default (hook-health record, exit 0), BLOCK only under
    `config_change_enforce`, and NEVER a block over our own inability to parse.

Isolated with a tmp TASK_STATION_HOME so the hook-health log is the tmp one.
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

import config_change  # noqa: E402
import hook_health    # noqa: E402

_spec = importlib.util.spec_from_file_location("task_station", os.path.join(LIB, "task-station.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _paths(findings):
    return [p for _label, p in findings]


# ================================================== what counts as a declared path ==
class DeclaredPathsTest(unittest.TestCase):
    def test_absolute_value_is_a_path(self):
        got = config_change.declared_paths({"env": {"TOOL": "/opt/tool/bin"}})
        self.assertEqual(got, [("env.TOOL", "/opt/tool/bin")])

    def test_home_rooted_value_is_a_path(self):
        got = config_change.declared_paths({"vault": "~/Documents/Obsidian Vault"})
        self.assertEqual(got, [("vault", "~/Documents/Obsidian Vault")])

    def test_a_path_with_spaces_is_kept_whole(self):
        """A plain value is checked WHOLE — real directories have spaces in them."""
        got = config_change.declared_paths({"dir": "/Users/x/My Files/notes"})
        self.assertEqual(_paths(got), ["/Users/x/My Files/notes"])

    def test_relative_paths_are_out_of_scope(self):
        """Resolved against a cwd we do not know — reporting one would be a guess."""
        self.assertEqual(config_change.declared_paths({"a": "./x.sh", "b": "lib/x.sh"}), [])

    def test_variables_are_out_of_scope(self):
        self.assertEqual(
            config_change.declared_paths({"a": "$HOME/x", "b": "${CLAUDE_PLUGIN_ROOT}/y"}), [])

    def test_globs_are_out_of_scope(self):
        """`Read(/etc/**)` and `~/x/*` are MATCH RULES, not files to stat."""
        self.assertEqual(
            config_change.declared_paths({"allow": ["/etc/**", "~/x/*.md", "/a/?b"]}), [])

    def test_a_path_list_is_split_per_entry(self):
        """`PATH`-shaped values are common in `env`; checking one whole would report a
        perfectly good PATH as a missing file."""
        got = config_change.declared_paths({"env": {"PATH": "/usr/local/bin:/usr/bin"}})
        self.assertEqual(_paths(got), ["/usr/local/bin", "/usr/bin"])

    def test_a_path_list_drops_its_relative_entries(self):
        got = config_change.declared_paths({"env": {"PATH": "./here:/usr/bin"}})
        self.assertEqual(_paths(got), ["/usr/bin"])

    def test_non_strings_are_ignored(self):
        self.assertEqual(config_change.declared_paths({"a": 3, "b": True, "c": None}), [])

    def test_list_elements_inherit_their_key(self):
        got = config_change.declared_paths(
            {"sandbox": {"filesystem": {"allowWrite": ["/a/b", "/c/d"]}}})
        self.assertEqual(got, [("sandbox.filesystem.allowWrite[0]", "/a/b"),
                               ("sandbox.filesystem.allowWrite[1]", "/c/d")])

    # -- command strings -------------------------------------------------------
    def test_command_takes_the_script_not_the_interpreter(self):
        """argv[0] here is `bash`; the interesting half is its argument."""
        self.assertEqual(config_change.command_path("bash /abs/host.sh --width 80"),
                         "/abs/host.sh")

    def test_command_that_is_itself_a_path(self):
        self.assertEqual(config_change.command_path("/abs/thing --flag"), "/abs/thing")

    def test_command_trailing_comment_is_not_an_argument(self):
        """Our own installed commands carry a `# task-station-managed…` marker."""
        self.assertEqual(
            config_change.command_path('bash "/abs/host.sh"  # claude-statusline-host:task-station'),
            "/abs/host.sh")

    def test_bare_command_name_is_out_of_scope(self):
        """A PATH lookup, not a file — checking it would report every `git` as missing."""
        self.assertIsNone(config_change.command_path("git status"))

    def test_command_with_a_variable_is_out_of_scope(self):
        self.assertIsNone(config_change.command_path('bash "${CLAUDE_PLUGIN_ROOT}/hooks/x.sh"'))

    def test_flags_are_skipped(self):
        self.assertEqual(config_change.command_path("bash -x /abs/x.sh"), "/abs/x.sh")

    def test_unbalanced_quotes_fall_back_to_a_dumb_split(self):
        self.assertEqual(config_change.command_path('bash "/abs/x.sh'), "/abs/x.sh")

    def test_hook_command_deep_in_the_tree_is_labelled(self):
        cfg = {"hooks": {"Stop": [{"hooks": [{"type": "command",
                                              "command": "bash /abs/on_stop.sh"}]}]}}
        self.assertEqual(config_change.declared_paths(cfg),
                         [("hooks.Stop[0].hooks[0].command", "/abs/on_stop.sh")])


# ============================================================== resolution + report ==
class UnresolvableTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-cfgchange-")
        self.real = os.path.join(self.tmp, "real.sh")
        with open(self.real, "w") as f:
            f.write("#!/bin/sh\n")
        self.cfg = os.path.join(self.tmp, "settings.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, obj):
        with open(self.cfg, "w") as f:
            json.dump(obj, f)
        return self.cfg

    def test_a_path_that_exists_is_not_reported(self):
        # Quoted, as the real installers write it — a tmp dir with a space in it must
        # not turn this into a test about shlex.
        self._write({"statusLine": {"command": 'bash "%s"' % self.real}})
        self.assertEqual(config_change.unresolvable(self.cfg), [])

    def test_a_path_that_is_gone_is_reported(self):
        self._write({"statusLine": {"command": 'bash "%s/gone.sh"' % self.tmp}})
        got = config_change.unresolvable(self.cfg)
        self.assertEqual(_paths(got), ["%s/gone.sh" % self.tmp])
        self.assertEqual(got[0][0], "statusLine.command")

    def test_home_paths_are_expanded_before_the_check(self):
        seen = []
        config_change.unresolvable(self.cfg, exists=lambda p: seen.append(p) or True,
                                   data={"vault": "~/nowhere"})
        self.assertEqual(seen, [os.path.expanduser("~/nowhere")])

    def test_a_malformed_file_reports_nothing(self):
        """FAIL-OPEN. A broken settings file is Claude Code's error to report, and
        blocking the save would trap the user's fix inside the file they are fixing."""
        with open(self.cfg, "w") as f:
            f.write("{not json")
        self.assertIsNone(config_change.load(self.cfg))
        self.assertEqual(config_change.unresolvable(self.cfg), [])

    def test_a_missing_file_reports_nothing(self):
        self.assertEqual(config_change.unresolvable(os.path.join(self.tmp, "nope.json")), [])

    def test_a_non_object_file_reports_nothing(self):
        self._write([1, 2, 3])
        self.assertEqual(config_change.unresolvable(self.cfg), [])

    # -- the one-line record --------------------------------------------------
    def test_detail_names_the_file_the_count_and_the_offenders(self):
        line = config_change.detail("/x/settings.json", [("statusLine.command", "/gone")])
        self.assertIn("settings.json", line)
        self.assertIn("1 unresolvable path(s)", line)
        self.assertIn("statusLine.command → /gone", line)

    def test_detail_rolls_up_past_the_cap(self):
        findings = [("k%d" % i, "/p%d" % i) for i in range(config_change.NAMED + 2)]
        line = config_change.detail("/x/settings.json", findings)
        self.assertIn("+2 more", line)

    def test_detail_of_nothing_is_none(self):
        self.assertIsNone(config_change.detail("/x/settings.json", []))


# ========================================================== the CLI: warn vs block ==
class CommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ts-cfgchange-cli-")
        self._env = {k: os.environ.get(k)
                     for k in ("TASK_STATION_HOME", "TASK_STATION_CONFIG_ENFORCE")}
        os.environ["TASK_STATION_HOME"] = self.tmp
        os.environ.pop("TASK_STATION_CONFIG_ENFORCE", None)
        self.cfg = os.path.join(self.tmp, "settings.json")

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, obj):
        with open(self.cfg, "w") as f:
            json.dump(obj, f)

    def _run(self, **kw):
        args = _Args(**dict({"session": "s1", "source": "user_settings",
                             "file": self.cfg}, **kw))
        err = io.StringIO()
        with redirect_stderr(err):
            ts.cmd_config_change(args)
        return err.getvalue()

    def _records(self):
        return hook_health.entries()

    def test_warn_records_and_does_not_block(self):
        self._write({"statusLine": {"command": 'bash "/nowhere/host.sh"'}})
        self._run()                                   # no SystemExit → the save lands
        recs = self._records()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["label"], "config-change")
        self.assertEqual(recs[0]["code"], 1)
        self.assertIn("/nowhere/host.sh", recs[0]["detail"])
        self.assertIn("user_settings", recs[0]["detail"])

    def test_a_healthy_file_says_nothing(self):
        self._write({"model": "claude-opus-5",
                     "statusLine": {"command": 'bash "%s"' % self.cfg}})
        self._run()
        self.assertEqual(self._records(), [])

    def test_enforce_blocks_with_exit_2_and_records_first(self):
        os.environ["TASK_STATION_CONFIG_ENFORCE"] = "on"
        self._write({"statusLine": {"command": 'bash "/nowhere/host.sh"'}})
        with self.assertRaises(SystemExit) as cm:
            self._run()
        self.assertEqual(cm.exception.code, 2)
        recs = self._records()
        self.assertEqual(len(recs), 1)              # written BEFORE the block…
        self.assertEqual(recs[0]["code"], 2)        # …and marked as the block it is

    def test_enforce_never_blocks_on_a_file_we_cannot_parse(self):
        os.environ["TASK_STATION_CONFIG_ENFORCE"] = "on"
        with open(self.cfg, "w") as f:
            f.write("{not json")
        self._run()                                   # no SystemExit
        self.assertEqual(self._records(), [])

    def test_enforce_never_blocks_on_a_missing_file(self):
        os.environ["TASK_STATION_CONFIG_ENFORCE"] = "on"
        self._run(file=os.path.join(self.tmp, "absent.json"))
        self.assertEqual(self._records(), [])

    def test_no_file_argument_is_a_noop(self):
        self._run(file=None)
        self.assertEqual(self._records(), [])

    def test_our_own_failure_never_blocks(self):
        """If the validator itself raises, the user's config save still lands."""
        os.environ["TASK_STATION_CONFIG_ENFORCE"] = "on"
        real = ts._config_change.unresolvable
        ts._config_change.unresolvable = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            self._run()                               # no SystemExit
        finally:
            ts._config_change.unresolvable = real
        self.assertEqual(self._records(), [])


if __name__ == "__main__":
    unittest.main()
