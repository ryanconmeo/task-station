"""The hook mux — one registered command per shared event, several hook programs.

`lib/hookmux.py` is what `hooks/hooks.json` now runs for SessionStart,
UserPromptSubmit and Stop. It spawns the board's shell hook and the brain plane's
`-m brain.hooks.*` hooks on the SAME payload and merges what they print into one
document. These tests drive it BEHAVIOURALLY: every case here runs real child
processes (temporary scripts, or the real guard), because the whole value of the
mux is what happens across a process boundary — the payload arriving on a child's
stdin, `PYTHONPATH` arriving in a child's environment, a child's exit code not
taking the session with it.

THE REAL CHILDREN TABLE IS NEVER RUN HERE, deliberately: the board's
`on_session_start.sh` writes symlinks and a statusline provider into the config
dir, which is a machine-changing side effect, not a unit under test. What IS
pinned about the real table is that it and `hooks/hooks.json` still describe the
same wiring (`ManifestAgreesWithTheMuxTest`) — the two drift silently otherwise.
"""
import ast
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(_REPO_ROOT, "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

import hookmux  # noqa: E402

HOOKS_JSON = os.path.join(_REPO_ROOT, "hooks", "hooks.json")
GUARD = os.path.join(LIB, "brain", "hooks", "guard.py")

PAYLOAD = json.dumps({"session_id": "s-1", "source": "startup",
                      "prompt": "a prompt"}).encode()


def _manifest():
    with open(HOOKS_JSON, encoding="utf-8") as f:
        return json.load(f)["hooks"]


def _emit(event, **inner):
    """A child script line that prints one hook document."""
    doc = {"hookSpecificOutput": dict({"hookEventName": event}, **inner)}
    return "cat >/dev/null\nprintf '%%s\\n' %s\n" % _sh_quote(json.dumps(doc))


def _sh_quote(s):
    return "'" + s.replace("'", "'\\''") + "'"


class MuxTestCase(unittest.TestCase):
    """Fake children live in a temp dir that doubles as CLAUDE_PLUGIN_ROOT, so a
    child entry is written exactly like a real one: a plugin-root-relative path
    the mux resolves itself."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="task-station-hookmux-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self._env_backup = {k: os.environ.get(k)
                            for k in ("CLAUDE_PLUGIN_ROOT", "PYTHONPATH")}
        self.addCleanup(self._restore_env)
        os.environ["CLAUDE_PLUGIN_ROOT"] = self.tmp
        # The mux is the ONLY thing that may put lib/ on a child's PYTHONPATH —
        # otherwise the env case below would pass on an inherited value.
        os.environ.pop("PYTHONPATH", None)

    def _restore_env(self):
        for k, v in self._env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # --- fixtures ----------------------------------------------------------
    def child(self, name, body):
        """Write a fake child script and return its mux table entry."""
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write("#!/usr/bin/env bash\n" + body)
        os.chmod(path, 0o755)
        return (hookmux.SCRIPT, name)

    def run_mux(self, event, children, payload=PAYLOAD):
        """Run the mux in-process over `children`; return (document, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err):
            doc = hookmux.run(event, payload, children=children, stdout=out)
        return doc, out.getvalue(), err.getvalue()


class MergeTest(MuxTestCase):
    def test_context_concatenates_in_child_order_and_scalars_are_first_writer_wins(self):
        children = [
            self.child("a.sh", _emit("SessionStart", additionalContext="AAA",
                                     sessionTitle="from-a")),
            self.child("b.sh", _emit("SessionStart", additionalContext="BBB",
                                     sessionTitle="from-b")),
        ]
        doc, out, _ = self.run_mux("session-start", children)
        inner = doc["hookSpecificOutput"]
        self.assertEqual(inner["additionalContext"], "AAA\n\nBBB")
        self.assertEqual(inner["sessionTitle"], "from-a")     # the first child keeps it
        self.assertEqual(inner["hookEventName"], "SessionStart")
        # Exactly one document on stdout, newline-terminated.
        self.assertEqual(json.loads(out), doc)
        self.assertEqual(out.count("\n"), 1)

    def test_top_level_keys_merge_beside_the_hook_output(self):
        """The board's Stop hook prints a decision document AND (separately) a
        context document. Both survive the merge, in one object."""
        children = [
            self.child("gate.sh", "cat >/dev/null\n"
                                  "printf '%s\\n' '{\"decision\":\"block\",\"reason\":\"track it\"}'\n"
                       + _emit("Stop", additionalContext="nudge")),
            self.child("distill.sh", "cat >/dev/null\n"),
        ]
        doc, _, _ = self.run_mux("stop", children)
        self.assertEqual(doc["decision"], "block")
        self.assertEqual(doc["reason"], "track it")
        self.assertEqual(doc["hookSpecificOutput"]["additionalContext"], "nudge")

    def test_nothing_to_say_prints_nothing(self):
        children = [self.child("quiet.sh", "cat >/dev/null\n")]
        doc, out, err = self.run_mux("session-start", children)
        self.assertIsNone(doc)
        self.assertEqual(out, "")
        self.assertEqual(err, "")

    def test_a_childs_hook_event_name_never_overrides_the_events(self):
        """A child that names the wrong event (or an old spelling) cannot make the
        mux emit a document the harness will not recognise."""
        children = [self.child("wrong.sh",
                               _emit("SomethingElse", additionalContext="X"))]
        doc, _, _ = self.run_mux("user-prompt", children)
        self.assertEqual(doc["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")


class PlainStdoutTest(MuxTestCase):
    def test_plain_stdout_is_context_on_session_start(self):
        children = [self.child("plain.sh", "cat >/dev/null\nprintf 'just words\\n'\n")]
        doc, _, err = self.run_mux("session-start", children)
        self.assertEqual(doc["hookSpecificOutput"]["additionalContext"], "just words")
        self.assertEqual(err, "")

    def test_plain_stdout_is_context_on_user_prompt(self):
        """The board's UserPromptSubmit hook prints its guidance as bare text."""
        children = [self.child("plain.sh", "cat >/dev/null\nprintf 'attach a task\\n'\n")]
        doc, _, _ = self.run_mux("user-prompt", children)
        self.assertEqual(doc["hookSpecificOutput"]["additionalContext"], "attach a task")

    def test_plain_stdout_is_routed_to_stderr_on_stop(self):
        """Stop's output contract is a decision document — loose text there is
        diagnostics, and injecting it as context would be inventing a contract."""
        children = [self.child("chatty.sh", "cat >/dev/null\nprintf 'noise\\n'\n")]
        doc, out, err = self.run_mux("stop", children)
        self.assertIsNone(doc)
        self.assertEqual(out, "")
        self.assertIn("noise", err)
        self.assertIn("chatty.sh", err)


class BrokenChildTest(MuxTestCase):
    def test_a_failing_child_never_takes_the_others_document(self):
        children = [
            self.child("boom.sh", "cat >/dev/null\nprintf 'not json {\\n'\nexit 3\n"),
            self.child("ok.sh", _emit("SessionStart", additionalContext="survived")),
        ]
        doc, _, err = self.run_mux("session-start", children)
        self.assertIn("survived", doc["hookSpecificOutput"]["additionalContext"])
        self.assertIn("boom.sh: exit 3", err)

    def test_a_child_that_cannot_start_is_a_breadcrumb(self):
        """The spawn itself failing (no interpreter, a bad exec) is the one case a
        child cannot report for itself, so the mux reports it — and keeps going."""
        real_run = subprocess.run

        def fake_run(argv, **kwargs):
            if argv[-1].endswith("dead.sh"):
                raise OSError("no interpreter")
            return real_run(argv, **kwargs)

        subprocess.run = fake_run
        self.addCleanup(setattr, subprocess, "run", real_run)
        children = [(hookmux.SCRIPT, "dead.sh"),
                    self.child("ok.sh", _emit("SessionStart", additionalContext="still here"))]
        doc, _, err = self.run_mux("session-start", children)
        self.assertEqual(doc["hookSpecificOutput"]["additionalContext"], "still here")
        self.assertIn("dead.sh: did not run", err)
        self.assertIn("OSError", err)

    def test_json_that_is_not_an_object_is_text_not_a_document(self):
        """A JSON array (or number, or string) is not a hook document. Treating it
        as text is what stops a child's stray output from writing keys nobody
        meant to write; the exiting child is still only a breadcrumb."""
        children = [self.child("junk.sh", "cat >/dev/null\nprintf '[1,2,3]\\n'\nexit 1\n")]
        doc, _, err = self.run_mux("session-start", children)
        self.assertEqual(doc["hookSpecificOutput"]["additionalContext"], "[1,2,3]")
        self.assertEqual(set(doc), {"hookSpecificOutput"})
        self.assertIn("junk.sh: exit 1", err)

    def test_main_returns_zero_for_an_unusable_invocation(self):
        """`main` spawns nothing for an unknown event, so this exercises the
        always-exit-0 contract without running the real children."""
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(hookmux.main(["no-such-event"]), 0)
            self.assertEqual(hookmux.main([]), 0)
        self.assertIn("unknown event", err.getvalue())


class StdinFanOutTest(MuxTestCase):
    def test_every_child_receives_the_same_payload_bytes(self):
        a = os.path.join(self.tmp, "a.in")
        b = os.path.join(self.tmp, "b.in")
        children = [self.child("a.sh", "cat > %s\n" % _sh_quote(a)),
                    self.child("b.sh", "cat > %s\n" % _sh_quote(b))]
        self.run_mux("session-start", children)
        with open(a, "rb") as f:
            self.assertEqual(f.read(), PAYLOAD)
        with open(b, "rb") as f:
            self.assertEqual(f.read(), PAYLOAD)


class ChildEnvironmentTest(MuxTestCase):
    def test_a_child_can_import_the_brain_package(self):
        """The packaging answer, end to end: a child process — with PYTHONPATH
        unset in this test's own environment — can `import brain.*` because the
        mux put lib/ there. This is what makes `-m brain.hooks.<mod>` work."""
        probe = os.path.join(self.tmp, "probe.py")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("import sys\n"
                    "try:\n"
                    "    import brain.hooks.guard  # noqa: F401\n"
                    "    sys.stdout.write('brain-import-ok')\n"
                    "except Exception as e:\n"
                    "    sys.stdout.write('brain-import-failed: %s' % e)\n")
        children = [self.child("probe.sh", "cat >/dev/null\n%s %s\n"
                               % (_sh_quote(sys.executable), _sh_quote(probe)))]
        doc, _, _ = self.run_mux("session-start", children)
        self.assertEqual(doc["hookSpecificOutput"]["additionalContext"],
                         "brain-import-ok")

    def test_an_existing_pythonpath_is_kept_after_lib(self):
        os.environ["PYTHONPATH"] = "/already/here"
        env = hookmux._child_env()
        self.assertEqual(env["PYTHONPATH"].split(os.pathsep), [LIB, "/already/here"])

    def test_lib_is_the_muxs_own_directory(self):
        self.assertEqual(hookmux._child_env()["PYTHONPATH"], LIB)


class ChildOrderTest(MuxTestCase):
    def test_the_shipped_table_runs_the_board_first(self):
        for event, children in hookmux.CHILDREN.items():
            self.assertEqual(children[0][0], hookmux.SCRIPT,
                             "%s: the board hook runs first" % event)
            for child in children[1:]:
                self.assertEqual(child[0], hookmux.MODULE, event)
                self.assertTrue(child[1].startswith("brain.hooks."), event)

    def test_order_is_the_order_context_arrives_in(self):
        children = [self.child("first.sh", _emit("SessionStart", additionalContext="1")),
                    self.child("second.sh", _emit("SessionStart", additionalContext="2")),
                    self.child("third.sh", _emit("SessionStart", additionalContext="3"))]
        doc, _, _ = self.run_mux("session-start", children)
        self.assertEqual(doc["hookSpecificOutput"]["additionalContext"], "1\n\n2\n\n3")

    def test_module_children_run_through_the_m_entry_point(self):
        argv = hookmux._argv((hookmux.MODULE, "brain.hooks.inject", "--session-start"))
        self.assertEqual(argv, [sys.executable, "-m", "brain.hooks.inject",
                                "--session-start"])

    def test_script_children_run_bash_against_the_plugin_root(self):
        argv = hookmux._argv((hookmux.SCRIPT, "hooks/on_stop.sh"))
        self.assertEqual(argv, ["bash", os.path.join(self.tmp, "hooks/on_stop.sh")])


class ManifestAgreesWithTheMuxTest(unittest.TestCase):
    """The two halves of the wiring, pinned against each other: what hooks.json
    registers, and what the mux believes it will run."""

    EVENTS = {"SessionStart": "session-start", "UserPromptSubmit": "user-prompt",
              "Stop": "stop"}

    def setUp(self):
        self.hooks = _manifest()

    def test_each_shared_event_registers_exactly_one_mux_command(self):
        for event, arg in self.EVENTS.items():
            entries = self.hooks[event]
            self.assertEqual(len(entries), 1, event)
            commands = [h["command"] for h in entries[0]["hooks"]]
            self.assertEqual(len(commands), 1, event)
            self.assertEqual(
                commands[0],
                'python3 "${CLAUDE_PLUGIN_ROOT}/lib/hookmux.py" %s' % arg)

    def test_the_muxs_events_are_exactly_the_registered_ones(self):
        self.assertEqual(set(hookmux.CHILDREN), set(self.EVENTS.values()))
        self.assertEqual(set(hookmux.EVENT_NAMES), set(hookmux.CHILDREN))
        self.assertEqual(set(hookmux.EVENT_NAMES.values()), set(self.EVENTS))

    def test_every_child_in_the_table_names_a_file_that_exists(self):
        for event, children in hookmux.CHILDREN.items():
            for child in children:
                if child[0] == hookmux.SCRIPT:
                    path = os.path.join(_REPO_ROOT, child[1])
                else:
                    path = os.path.join(LIB, *child[1].split(".")) + ".py"
                self.assertTrue(os.path.isfile(path), "%s → %s" % (event, path))

    def test_the_mux_imports_no_plane(self):
        """It SPAWNS the two planes; importing either would make a hook failure a
        mux failure, and would put a board import inside a brain launch path."""
        with open(os.path.join(LIB, "hookmux.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    names.add(a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        self.assertEqual(names & {"brain", "board", "core"}, set())
        self.assertEqual(names, {"json", "os", "subprocess", "sys"})


class GuardRegistrationTest(unittest.TestCase):
    """PreToolUse(Bash) is a brain-only event, so the guard is registered DIRECTLY
    — no mux — and runs by path. (The source's own registration test, deferred in
    Phase 4 chunk 5a because this repo had no PreToolUse block to assert.)"""

    def setUp(self):
        self.hooks = _manifest()

    def test_hooks_json_registers_the_guard_on_bash(self):
        entries = self.hooks["PreToolUse"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["matcher"], "Bash")
        commands = [h["command"] for h in entries[0]["hooks"]]
        self.assertEqual(
            commands,
            ['python3 "${CLAUDE_PLUGIN_ROOT}/lib/brain/hooks/guard.py"'])
        self.assertTrue(os.path.isfile(GUARD))

    def test_the_registered_command_form_runs_by_path(self):
        """The registered spelling is a PATH, not `-m`. That only works while the
        guard imports nothing but the stdlib, so run it the way the manifest does."""
        benign = json.dumps({"tool_name": "Bash",
                             "tool_input": {"command": "git status && ls -la"}})
        r = subprocess.run([sys.executable, GUARD], input=benign,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "")        # allow = say nothing

    def test_the_registered_command_form_still_denies(self):
        payload = json.dumps({"tool_name": "Bash", "tool_input": {
            "command": "az account get-access-token --query accessToken -o tsv"}})
        r = subprocess.run([sys.executable, GUARD], input=payload,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        hso = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual(hso["hookEventName"], "PreToolUse")
        self.assertEqual(hso["permissionDecision"], "deny")

    def test_the_guard_is_not_a_mux_child(self):
        for children in hookmux.CHILDREN.values():
            self.assertNotIn("brain.hooks.guard", [c[1] for c in children])


if __name__ == "__main__":
    unittest.main()
