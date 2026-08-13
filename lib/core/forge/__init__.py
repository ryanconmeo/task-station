"""core.forge — the ONLY code that talks to the git host where a remote brain
repo lives. Two adapters ship, behind one interface, so the promote pipeline is
forge-agnostic:

  * ``ado``    — Azure DevOps, via the ``az`` CLI (bearer-token push + PR).
  * ``github`` — GitHub, via the ``gh`` CLI (repo create + PR) and ``git`` push.

Interface every adapter module implements::

    configured(cfg) -> bool                 # org/owner/repo present -> can push+PR
    push_branch(clone, branch, cfg) -> bool # auth is the adapter's job; raises ForgeError
    open_pr(cfg, branch, title) -> str|None # returns the PR url
    create_repo(cfg, name) -> str|None      # returns the repo url (setup/parity)

Plus, for no-network tests, each adapter exposes pure ``*_argv`` builders that
return the exact command lists its ``subprocess`` calls will run.

Selection is by ``cfg['forge_kind']`` (see ``brain.config``; default ``ado``).
Org/owner/project/repo all come from config — NEVER literals in code. Pure
stdlib; the only external processes are ``git`` and the per-adapter CLI
(``az`` / ``gh``).

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 1) from the brain source tree's
``scripts/forge/`` package @ 0.14.0 — a move, semantically identical, with the
config module renamed in prose (``pb_config`` -> ``brain.config``). It sits in
core rather than brain because it is plumbing over a git host with no note or
knowledge semantics in it, and core is the bottom layer: this package imports the
stdlib only (no lib/brain, no lib/board).
"""
import subprocess

from . import ado, github

ADAPTERS = {"ado": ado, "github": github}


class ForgeError(Exception):
    """A refusal or failure inside a forge adapter (push/PR/repo op)."""


def get_adapter(cfg):
    """Return the adapter module for ``cfg['forge_kind']`` (default ``ado``)."""
    kind = (cfg.get("forge_kind") or "ado")
    kind = str(kind).strip().lower()
    try:
        return ADAPTERS[kind]
    except KeyError:
        raise ForgeError(f"unknown forge_kind {kind!r}; known: {sorted(ADAPTERS)}")


def start_branch(clone, branch, base):
    """Create/reset ``branch`` off ``base`` in the clone (``-B`` is idempotent).
    Shared local git — forge-agnostic, so it lives on the interface, not per-adapter."""
    subprocess.run(["git", "-C", str(clone), "checkout", "-B", branch, base],
                   capture_output=True, text=True)
