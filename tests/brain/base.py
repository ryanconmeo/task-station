"""Shared scaffolding for the brain-plane unittest suite.

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 1) from the brain source tree's
``tests/base.py`` @ 0.14.0 — MINIMALLY, i.e. only what the chunk-1 tests need
(config isolation + the vault/config/native-memory fixtures). Later chunks extend
it rather than starting a second base.

Every test runs against a throwaway ``$HOME`` temp tree with all
``TASK_STATION_BRAIN_*`` env vars cleared, so config resolution is fully isolated
from the developer's real machine. ``brain.config`` reads ``Path.home()`` and the
environment lazily (never at import), so overriding ``$HOME`` in ``setUp`` is
enough — no module reload required.

TWO EXTRA PINS the standalone source did not need. ``brain.config`` now resolves
its mutable-state defaults (tasks_db, state_dir) through ``core.paths.data_dir()``,
which reads ``TASK_STATION_HOME`` / ``CLAUDE_CONFIG_DIR`` — and the engine suite's
``tests/__init__.py`` pins both to a SHARED temp dir for the whole run. So setUp
re-points them INTO this test's own temp home (never clears them: an unset
TASK_STATION_HOME would let data_dir() fall through toward the real ~/.claude).
Both are restored on cleanup, so the engine suite's pins survive.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent.parent / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import brain.config as bconfig  # noqa: E402

# Every env override key the suite touches (cleared before each test).
ENV_KEYS = [
    "TASK_STATION_BRAIN_VAULT",
    "TASK_STATION_BRAIN_MEMORY",
    "TASK_STATION_BRAIN_ORG_BRAIN_CLONE",
    "TASK_STATION_BRAIN_TASKS_DB",
    "TASK_STATION_BRAIN_EPISODIC_STREAM",
    "TASK_STATION_BRAIN_STATE",
    "TASK_STATION_BRAIN_INJECT_CONTEXT",
    "TASK_STATION_BRAIN_AUTO_DISTILL",
    "TASK_STATION_BRAIN_INJECT_KEYWORDS",
    "TASK_STATION_BRAIN_PUBLISH_MIRROR",
    "TASK_STATION_BRAIN_PEERS_DIR",
    "TASK_STATION_BRAIN_ORG_LABEL",
    "TASK_STATION_BRAIN_ALIAS",
    "TASK_STATION_BRAIN_OWNER",
    "TASK_STATION_BRAIN_TASK",
    "TASK_STATION_BRAIN_FORGE_KIND",
    "TASK_STATION_BRAIN_FORGE_ORG",
    "TASK_STATION_BRAIN_FORGE_PROJECT",
    "TASK_STATION_BRAIN_FORGE_REPO",
    "TASK_STATION_BRAIN_FORGE_TARGET_BRANCH",
    "TASK_STATION_BRAIN_ADO_ORG",
    # No TASK_STATION_BRAIN_TASK_STATION_CLI: chunk 4c's direct board.memos
    # bridge retired the config key it named, and test_config's
    # `test_fixture_clears_exactly_the_module_env_keys` holds this list and
    # `brain.config.ENV` to the same set.
    "TASK_STATION_BRAIN_KNOWLEDGE_MEMOS",
]

# Env vars that are RE-POINTED into the temp home rather than cleared (see the
# module docstring): value = a path relative to the temp home.
PINNED_ENV = {
    "TASK_STATION_HOME": ".claude/task-station-data",
    "CLAUDE_CONFIG_DIR": ".claude",
}

CONTENT_DIRS = ("notes", "projects", "reports", "plans", "raw")

# The brain's primary config file, relative to $HOME (genericized from the
# source's org-branded filename). One spelling for the whole suite.
PRIMARY_CONFIG_REL = ".claude/brain-station.json"
HOME_CONFIG_REL = "brains/config.json"


class BrainTestCase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="brain-home-")).resolve()
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)

        backup_keys = ENV_KEYS + ["HOME"] + list(PINNED_ENV)
        self._env_backup = {k: os.environ.get(k) for k in backup_keys}
        self.addCleanup(self._restore_env)
        for k in ENV_KEYS:
            os.environ.pop(k, None)
        os.environ["HOME"] = str(self.home)
        for k, rel in PINNED_ENV.items():
            os.environ[k] = str(self.home / rel)
        (self.home / ".claude").mkdir(parents=True, exist_ok=True)
        bconfig._warned.clear()

    def _restore_env(self):
        for k, v in self._env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        bconfig._warned.clear()

    # --- fixtures -----------------------------------------------------------
    def make_vault(self, path):
        """Build a minimal but complete vault fixture at ``path``."""
        p = Path(path)
        for d in CONTENT_DIRS:
            (p / d).mkdir(parents=True, exist_ok=True)
        (p / "INDEX.md").write_text("# INDEX\n")
        (p / "LOG.md").write_text("# LOG\n")
        return p

    def write_primary_config(self, data):
        """Write ``~/.claude/brain-station.json`` (dict -> JSON, str -> verbatim)."""
        target = self.home / PRIMARY_CONFIG_REL
        target.write_text(data if isinstance(data, str) else json.dumps(data))
        return target

    def write_home_config(self, data):
        """Write ``~/brains/config.json`` (dict -> JSON, str -> verbatim)."""
        target = self.home / HOME_CONFIG_REL
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(data if isinstance(data, str) else json.dumps(data))
        return target

    def make_native_memory(self, cwd, memory_md="# MEMORY\n", mtime=None):
        """Create a fake Claude native-memory dir for ``cwd`` under the temp home.

        Mirrors Claude's encoding: the project dir name is the cwd with os.sep
        replaced by '-'. Optionally stamp MEMORY.md's mtime for ordering tests.
        """
        enc = str(cwd).replace(os.sep, "-")
        d = self.home / ".claude/projects" / enc / "memory"
        d.mkdir(parents=True, exist_ok=True)
        md = d / "MEMORY.md"
        md.write_text(memory_md)
        if mtime is not None:
            os.utime(md, (mtime, mtime))
        return d

    def data_home(self):
        """The task-station data home this test is pinned to (where the board's
        store/stream and the brain's state live)."""
        return self.home / PINNED_ENV["TASK_STATION_HOME"]
