"""brain.hooks.inject — context injection (starvation fix, keyword config, GC,
one search) plus the SessionStart half the source never covered.

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 5a) from the brain source tree's
``tests/test_context_inject.py`` @ 0.14.0. All 5 source cases port. Two
mechanical differences:

  * the module imports normally (``import brain.hooks.inject``) — the source
    loaded ``context-inject.py`` through ``importlib.util.spec_from_file_location``
    because a hyphenated filename is not importable. That trick dies with the
    rename, here and in ``test_peers.py``;
  * the keyword fixture's two org product names are genericized (``Ledger`` /
    ``Beacon``); the arithmetic that makes the starvation case work — three notes
    matching the first keyword, one matching the second, the second crowded out
    of the top 3 — is unchanged.

ADDED: the SessionStart path (the source tested only ``--prompt`` and the GC),
the two INERT spawn seams, the never-break-the-session contract, and the
``-m brain.hooks.*`` entry points that Phase 5 will wire.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import time
import unittest

from tests.brain.base import BrainTestCase, LIB, PINNED_ENV

import brain.config as bconfig
import brain.hooks.inject as inject


def _run_prompt(cfg, prompt, session_id):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        inject.prompt_scan({"prompt": prompt, "session_id": session_id}, cfg)
    return buf.getvalue()


def _run_session_start(cfg):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        inject.session_start({}, cfg)
    return buf.getvalue()


@unittest.skipIf(shutil.which("rg") is None, "ripgrep not installed")
class StarvationTest(BrainTestCase):
    def setUp(self):
        super().setUp()
        self.vault = self.make_vault(self.home / "vault")
        notes = self.vault / "notes"
        for slug in ("ledger-a", "ledger-b", "ledger-c"):
            (notes / f"{slug}.md").write_text(
                f"---\nname: {slug}\ndescription: about Ledger\nverified: 2026-06-01\n---\n\nLedger content\n")
        (notes / "beacon-x.md").write_text(
            "---\nname: beacon-x\ndescription: about Beacon\nverified: 2020-01-01\n---\n\nBeacon content\n")
        self.cfg = {"inject_context": True, "vault": self.vault,
                    "inject_keywords": ["Ledger", "Beacon"]}

    def test_second_keyword_not_starved(self):
        # prompt 1 mentions both; the 3 Ledger notes crowd out Beacon from the top 3
        out1 = _run_prompt(self.cfg, "working on Ledger and Beacon today", "s1")
        self.assertIn("ledger-a", out1)
        state = bconfig.state_dir() / "inject-s1.topics"
        served = set(state.read_text().split())
        self.assertIn("ledger", served)
        self.assertNotIn("beacon", served)      # Beacon was crowded out -> still unseen

        # prompt 2 mentions only Beacon -> still injects (starvation gone)
        out2 = _run_prompt(self.cfg, "now just Beacon", "s1")
        self.assertIn("beacon-x", out2)

    def test_keyword_not_in_config_is_ignored(self):
        self.cfg["inject_keywords"] = ["Ledger"]  # Beacon removed from the list
        out = _run_prompt(self.cfg, "talking about Beacon only", "s2")
        self.assertEqual(out.strip(), "")       # Beacon not a configured keyword

    def test_empty_keyword_list_disables(self):
        self.cfg["inject_keywords"] = []
        out = _run_prompt(self.cfg, "Ledger Ledger Ledger", "s3")
        self.assertEqual(out.strip(), "")

    def test_single_search_hits_call(self):
        calls = {"n": 0}
        real = inject.search.search_hits

        def counting(*a, **k):
            calls["n"] += 1
            return real(*a, **k)

        inject.search.search_hits = counting
        self.addCleanup(setattr, inject.search, "search_hits", real)
        _run_prompt(self.cfg, "both Ledger and Beacon here", "s4")
        self.assertEqual(calls["n"], 1)          # merged, not one rg per keyword

    def test_injected_block_names_the_config_it_is_disabled_from(self):
        """ADDED — the disable hint is read from ``config``, not re-spelled. The
        source hard-coded its config filename in this string; the port renamed the
        file, so a literal would have shipped a lie."""
        out = _run_prompt(self.cfg, "about Ledger", "s5")
        self.assertIn("inject_context:false", out)
        self.assertIn(str(bconfig._primary_config_path()), out)


class GCTest(BrainTestCase):
    def test_gc_removes_only_old_topics(self):
        d = bconfig.state_dir()
        old = d / "inject-old.topics"
        new = d / "inject-new.topics"
        old.write_text("ledger\n")
        new.write_text("beacon\n")
        eight_days = time.time() - 8 * 24 * 3600
        os.utime(old, (eight_days, eight_days))
        inject._gc_topics()
        self.assertFalse(old.exists())           # >7d removed
        self.assertTrue(new.exists())            # fresh kept


class SessionStartTest(BrainTestCase):
    """ADDED — the SessionStart half. The source shipped it untested; it is the
    surface that tells every session the brain exists, and it now carries a
    rewritten command hint (the ``-m`` entry point) that nothing else pins."""

    def setUp(self):
        super().setUp()
        self.vault = self.make_vault(self.home / "vault")
        self.cfg = {"inject_context": True, "vault": self.vault, "inject_keywords": []}
        # CLAUDE_PLUGIN_ROOT is not a brain config key, so base.setUp neither
        # clears nor restores it (D24). This class does both — a bare pop here
        # would leak out of the class and into every later test in the run.
        self._plugin_root = os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
        self.addCleanup(self._restore_plugin_root)

    def _restore_plugin_root(self):
        if self._plugin_root is None:
            os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
        else:
            os.environ["CLAUDE_PLUGIN_ROOT"] = self._plugin_root

    def _emitted(self, out):
        return json.loads(out)["hookSpecificOutput"]

    def test_emits_orientation_with_the_vault_and_the_m_entry_point(self):
        hso = self._emitted(_run_session_start(self.cfg))
        self.assertEqual(hso["hookEventName"], "SessionStart")
        text = hso["additionalContext"]
        self.assertIn(str(self.vault), text)
        self.assertIn("python3 -m brain.search search", text)
        self.assertNotIn("scripts/brain.py", text)   # the retired path is gone

    def test_silent_when_there_is_no_vault(self):
        cfg = dict(self.cfg, vault=self.home / "nope")
        self.assertEqual(_run_session_start(cfg), "")

    def test_routing_text_comes_from_the_plugin_root_when_present(self):
        root = self.home / "plugin-root"
        root.mkdir()
        (root / "system-instructions.md").write_text("ROUTING-RULES-MARKER\n")
        os.environ["CLAUDE_PLUGIN_ROOT"] = str(root)
        self.assertIn("ROUTING-RULES-MARKER",
                      self._emitted(_run_session_start(self.cfg))["additionalContext"])

    def test_a_plugin_root_without_the_document_is_not_an_error(self):
        os.environ["CLAUDE_PLUGIN_ROOT"] = str(self.home / "empty-root")
        self.assertEqual(inject._routing_text(), "")

    def test_no_plugin_root_injects_no_routing_text_and_does_not_fail(self):
        self.assertEqual(inject._routing_text(), "")   # setUp already cleared it
        self.assertIn(str(self.vault),
                      self._emitted(_run_session_start(self.cfg))["additionalContext"])


class SpawnSeamTest(BrainTestCase):
    """ADDED — the two spawns are INERT until Phase 5 wires entry points (the
    weekly lint, whose throttling shell script is not part of this port, and the
    org-brain pull, D34's shape). Both halves are asserted: SessionStart still
    CALLS them in order, and the shipped seams start no process."""

    def setUp(self):
        super().setUp()
        self.vault = self.make_vault(self.home / "vault")
        self.cfg = {"inject_context": True, "vault": self.vault, "inject_keywords": []}
        self.calls = []
        self.shipped = {}
        for name in ("_spawn_weekly_lint", "_spawn_orgpull"):
            self.shipped[name] = getattr(inject, name)
            self.addCleanup(setattr, inject, name, self.shipped[name])
            setattr(inject, name, (lambda n: lambda: self.calls.append(n))(name))

    def test_session_start_fires_both_seams_once_each(self):
        _run_session_start(self.cfg)
        self.assertEqual(self.calls, ["_spawn_weekly_lint", "_spawn_orgpull"])

    def test_the_seams_run_before_the_vault_check(self):
        # no vault -> the orientation stays silent, but the throttled background
        # work still gets its chance (source ordering, kept deliberately)
        out = _run_session_start(dict(self.cfg, vault=self.home / "nope"))
        self.assertEqual(out, "")
        self.assertEqual(self.calls, ["_spawn_weekly_lint", "_spawn_orgpull"])

    def test_the_shipped_seams_are_inert(self):
        """Called for real (not the stubs): each returns False and starts nothing.
        ``False`` is the contract — a caller can never read the inert seam as a
        fired one."""
        self.assertIs(self.shipped["_spawn_weekly_lint"](), False)
        self.assertIs(self.shipped["_spawn_orgpull"](), False)


class NeverBreaksTheSessionTest(BrainTestCase):
    """ADDED — the hook contract. Whatever arrives on stdin, and whatever the
    vault is doing, ``main()`` exits 0 and writes a breadcrumb rather than a
    traceback."""

    def test_main_exits_zero_on_garbage_stdin(self):
        argv, stdin = sys.argv, sys.stdin
        sys.argv = ["inject", "--prompt"]
        sys.stdin = io.StringIO("{ not json")
        self.addCleanup(setattr, sys, "argv", argv)
        self.addCleanup(setattr, sys, "stdin", stdin)
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                inject.main()
        self.assertEqual(cm.exception.code, 0)

    def test_an_exploding_scan_is_logged_not_raised(self):
        boom = inject.prompt_scan

        def explode(payload, cfg):
            raise RuntimeError("scan blew up")

        inject.prompt_scan = explode
        self.addCleanup(setattr, inject, "prompt_scan", boom)
        argv, stdin = sys.argv, sys.stdin
        sys.argv = ["inject", "--prompt"]
        sys.stdin = io.StringIO(json.dumps({"prompt": "x", "session_id": "s"}))
        self.addCleanup(setattr, sys, "argv", argv)
        self.addCleanup(setattr, sys, "stdin", stdin)
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                inject.main()
        self.assertEqual(cm.exception.code, 0)
        log = bconfig.state_dir() / "error.log"
        self.assertTrue(log.exists())
        self.assertIn("inject:prompt", log.read_text())


class HookEntryPointTest(BrainTestCase):
    """ADDED — ``python3 -m brain.hooks.<name>`` is the uniform entry point Phase 5
    wires. Four modules, one contract: run against a temp home, exit 0, emit no
    traceback. A package module with relative imports cannot be run by path, so if
    this stops working every hook stops working at once — which is why it fails
    here, by name, rather than in whatever hooks.json Phase 5 writes."""

    def _run(self, module, *args, stdin=""):
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["PYTHONPATH"] = str(LIB)
        for k, rel in PINNED_ENV.items():
            env[k] = str(self.home / rel)
        return subprocess.run([sys.executable, "-m", module, *args],
                              input=stdin, capture_output=True, text=True, env=env)

    def test_inject_session_start_runs(self):
        r = self._run("brain.hooks.inject", "--session-start")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_gate_runs_and_prints_a_decision(self):
        r = self._run("brain.hooks.gate")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("due", json.loads(r.stdout))

    def test_distill_dry_run_decides_without_spawning(self):
        r = self._run("brain.hooks.distill", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("auto-distill decision:", r.stdout)

    def test_guard_denies_through_the_module_entry_point(self):
        payload = json.dumps({"tool_name": "Bash", "tool_input": {
            "command": "az account get-access-token --query accessToken -o tsv"}})
        r = self._run("brain.hooks.guard", stdin=payload)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")


if __name__ == "__main__":
    unittest.main()
