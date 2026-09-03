"""The 22 plumbing verbs moved under `hook`, and this is what stops that from breaking.

WHAT MOVED AND WHY. 81 top-level subcommands, 22 of which no person types: they are
invoked by a script in `hooks/`, by the Stop hook's step runner, by the worktree hook the
installer writes into `settings.json`, or by a `delegate` worker writing back. They were
21 of the 81 names `--help` listed before a reader reached a command they could use.

THE RISK THIS GUARD ANSWERS. `hooks/*.sh` and the machinery invoke these verbs as
SUBPROCESS ARGV — strings no import graph carries and no mocked dispatch exercises. A
rename that lands in the CLI and misses one call site does not fail loudly; it fails at
the NEXT SessionStart on a machine that is the only install. So this test reads the real
call sites out of the real files and runs every one of them through the REAL parser.

Four things are pinned:

1. Every one of the 22 resolves under its new `hook <verb>` spelling.
2. Every LEGACY top-level spelling still resolves. That compatibility is the reason the
   rename could be made at all on a single-install machine: an installed settings.json
   WorktreeCreate entry names the old form and nothing in this repo can edit it.
3. Every task-station invocation written in `hooks/*.sh` parses — not "looks like a known
   name", parses, through the parser that will actually reject it.
4. `hooks/hooks.json` names no task-station subcommand. It registers `hookmux.py` and the
   shell scripts, which is WHY this move did not have to touch it — worth pinning,
   because the next person to group commands will want to know whether it does.

Stdlib + unittest. It imports the CLI (that is the point) and reads the scripts as text.
"""
import json
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

import board.cli as cli                                        # noqa: E402

HOOKS_DIR = os.path.join(ROOT, "hooks")

# A call site in a shell script: `…/task-station.py" <verb>`. Enough to recover the verb;
# whether it is a real one is settled by parsing, not by this regex.
#
# `[ \t]`, NOT `\s` — `\s` crosses newlines, so `engine="…/task-station.py"` followed by an
# `if` on the next line read as an invocation of `if`. The verb is always on the same line
# as the path.
_CALL = re.compile(r'task-station\.py"?[ \t]+([a-z][a-z0-9-]*)')


def _hook_scripts():
    return [os.path.join(HOOKS_DIR, f) for f in sorted(os.listdir(HOOKS_DIR))
            if f.endswith(".sh")]


def _code_lines(path):
    """The script's CODE. Comment lines are skipped, because these files explain
    themselves in prose that names the CLI ("run task-station.py and …") and a scan that
    read those would report an invocation of the word `and`."""
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if line and not line.startswith("#"):
                yield raw


class HookNamespace(unittest.TestCase):

    def _resolves(self, argv, why):
        """`--help` on a real command exits 0; an unknown one exits non-zero."""
        with self.assertRaises(SystemExit) as cm:
            cli.main(argv)
        self.assertEqual(0, cm.exception.code, why)

    def test_every_moved_verb_is_reachable_under_hook(self):
        """The grouped spelling every shipped call site now uses."""
        for verb in cli.HOOK_VERBS:
            self._resolves(["hook", verb, "--help"],
                           "`hook %s` did not resolve" % verb)

    def test_every_legacy_spelling_still_resolves(self):
        """The compatibility promise. An installed settings.json WorktreeCreate entry, and
        any script not yet updated, name the OLD form; each must still reach the parser
        rather than dying as an unknown command."""
        for verb in cli.HOOK_VERBS:
            self._resolves([verb, "--help"],
                           "legacy spelling %r stopped resolving" % verb)

    def test_the_normaliser_only_rewrites_the_FIRST_token(self):
        """A moved verb appearing as a VALUE must never be rewritten — `--title
        session-start` is a task title, not an invocation."""
        self.assertEqual(["hook", "session-start", "--session", "x"],
                         cli._normalise_hook_argv(["session-start", "--session", "x"]))
        self.assertEqual(["create", "--title", "session-start"],
                         cli._normalise_hook_argv(["create", "--title", "session-start"]))
        self.assertEqual(["board"], cli._normalise_hook_argv(["board"]))
        self.assertEqual([], cli._normalise_hook_argv([]))

    def test_every_invocation_in_hooks_parses(self):
        """The call sites themselves, read out of the shipped scripts. This is the
        assertion that would have caught a half-moved rename."""
        seen = 0
        for path in _hook_scripts():
            body = "".join(_code_lines(path))
            for verb in _CALL.findall(body):
                if verb == "hook":
                    continue            # the grouped form; the verb after it is checked
                seen += 1
                self._resolves([verb, "--help"],
                               "%s invokes `%s`, which the parser rejects"
                               % (os.path.basename(path), verb))
        self.assertGreater(seen, 3, "the call-site scan found almost nothing — every "
                                    "assertion in this method just went vacuous")

    def test_the_grouped_call_sites_parse_too(self):
        """`task-station.py" hook <verb>` — the form the scripts now carry."""
        pat = re.compile(r'task-station\.py"?[ \t]+hook[ \t]+([a-z][a-z0-9-]*)')
        seen = 0
        for path in _hook_scripts():
            for verb in pat.findall("".join(_code_lines(path))):
                seen += 1
                self.assertIn(verb, cli.HOOK_VERBS,
                              "%s calls `hook %s`, which is not a moved verb"
                              % (os.path.basename(path), verb))
                self._resolves(["hook", verb, "--help"], "`hook %s` rejected" % verb)
        self.assertGreater(seen, 10, "no grouped call sites found — the scripts were "
                                     "not updated, or this scan is broken")

    def test_hooks_json_names_no_subcommand(self):
        """WHY THE MOVE DID NOT TOUCH hooks.json: it registers the mux and the scripts,
        never a task-station verb. If that ever changes, this fails and the next grouping
        learns it now has a second file to keep in step."""
        with open(os.path.join(HOOKS_DIR, "hooks.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        cmds = []
        for entries in doc.get("hooks", {}).values():
            for entry in entries:
                for h in entry.get("hooks", []):
                    cmds.append(h.get("command", ""))
        self.assertTrue(cmds, "hooks.json declares no commands — this scan is vacuous")
        for c in cmds:
            self.assertNotIn("task-station.py", c,
                             "hooks.json now invokes the CLI directly (%s); a verb "
                             "rename must update this file too" % c)

    def test_the_moved_set_is_not_silently_empty(self):
        self.assertEqual(22, len(cli.HOOK_VERBS))
        self.assertEqual(len(set(cli.HOOK_VERBS)), len(cli.HOOK_VERBS))


if __name__ == "__main__":
    unittest.main()
