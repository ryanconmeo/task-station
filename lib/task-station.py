#!/usr/bin/env python3
"""Task Station — persistent, cross-session task tracking for Claude Code.

Engine for the /todo and /done commands and the SessionStart / UserPromptSubmit
hooks. Tasks live as one JSON file per task under store/tasks/. A session is
"attached" to at most one task via a link file under store/links/<session_id>.

Subcommands:
  create  --session ID --title T --summary S   create a task, attach the session
  attach  --session ID --task REF              attach session to an existing task
  bump    --session ID                          touch the attached task's activity
  skip    --session ID                          mark session intentionally untracked (silences nudge)
  done    --session ID                          close the attached task
  render  --session ID --arg STR                /todo entrypoint (list | detail+attach)
  prompt-context --session ID                   UserPromptSubmit hook context
  session-start  --session ID --source SRC      SessionStart hook context
  guidance                                      full attach/create how-to (on demand)

REF is a 1-based index from the most recent `render` listing, or a task id /
id-prefix. All writes are atomic (temp file + os.replace).
"""

import os
import subprocess
import sys

import config_change as _config_change
import paths
import store

BASE = os.path.dirname(os.path.abspath(__file__))  # code location only (self-invocation)
DATA = paths.data_dir()                             # mutable state — survives /plugin update
STORE = os.path.join(DATA, "store")
TASKS_DIR = os.path.join(STORE, "tasks")
LINKS_DIR = os.path.join(STORE, "links")
PENDING_BRIEFS = os.path.join(DATA, "pending-briefs")
DELEGATE_REGISTRY = os.path.join(DATA, "workers.json")
_LIVE_BG_INDEX = None   # per-process snapshot of `claude agents --json` (bg-aware resume); None = not yet queried
PROJECTS_ROOT = os.path.join(
    os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")), "projects")

# ----------------------------------------------------------------- facade ----
# 3.0.0 splits the engine into lib/board/*. THIS file stays the FACADE: the config
# block above (the routed constants — DATA, STORE, TASKS_DIR, …), and a star-import
# of every seam in layer order — so every historical `ts.<name>` still resolves
# here. 77 test files load this module as a fresh copy by literal path and patch
# `ts.<name>` directly; the seams read those names back through
# `_shared.g("NAME")`.
#
# THE PURGE REGENERATES THE SEAMS, AND ONLY THE SEAMS. Each seam calls
# `bind(globals())` against the facade copy that imported it, so a copy reusing the
# previous copy's seam modules would read the PREVIOUS copy's namespace and every
# `ts.<name>` patch would silently miss. Dropping exactly the seam entries is what
# makes each fresh copy import its OWN generation, bound to THIS module's globals.
#
# MOVED FLAT MODULES ARE DELIBERATELY NOT PURGED — `board.categories`, `board.heal`,
# `board.store` and the rest of the Phase-3 movers. They bind nothing, so they need
# no generation of their own: they are PROCESS-SHARED, which is exactly the semantics
# they had at `lib/` root before the move, and their `lib/<name>.py` shims alias the
# same module object either way. Purging them broke two things at once — a reimport
# per engine copy for modules that never needed one, and `importlib.reload()`, which
# resolves a module by its `__name__` (`board.categories` after the move) and raises
# ImportError when that key is gone. The tests reload `categories` 30 times.
#
# So the purge is an EXPLICIT LIST rather than a `board.*` prefix sweep: a prefix
# sweep cannot tell a seam from a mover, and `lib/board/` now holds both. `board`
# itself also stays cached — it is a namespace package with no bound state.
_SEAM_MODULES = (
    "board._shared", "board.state", "board.model", "board.memos",
    "board.sessions", "board.render", "board.graph", "board.boardio",
    "board.cmds", "board.cmds.maintain", "board.cmds.manage",
    "board.cmds.view", "board.cmds.sub", "board.cmds.surface",
    "board.cmds.loop", "board.cli",
)
for _m in [m for m in _SEAM_MODULES if m in sys.modules]:
    del sys.modules[_m]

import board._shared as _bshared                    # noqa: E402
_bshared.bind(globals())

from board._shared import *      # noqa: F401,F403,E402
from board.state import *        # noqa: F401,F403,E402
from board.model import *        # noqa: F401,F403,E402
from board.memos import *        # noqa: F401,F403,E402
from board.sessions import *     # noqa: F401,F403,E402
from board.render import *       # noqa: F401,F403,E402
from board.graph import *        # noqa: F401,F403,E402
from board.boardio import *      # noqa: F401,F403,E402
from board.cmds import *         # noqa: F401,F403,E402
from board.cli import *          # noqa: F401,F403,E402


# ---------------------------------------------------- transcript-derived caches ----
# A session transcript is APPEND-ONLY, and everything we derive from one — the user
# message count, the prompt→reply map — is a pure function of its bytes. So
# (st_mtime_ns, st_size) is a COMPLETE cache key: any change to the file changes one
# of them, which means a cache hit can never be stale. There is no invalidation
# window to reason about, and no need to guess whether a transcript is "done".
#
# This matters because the board asks the same questions over and over. Rendering
# 375 tasks over 458 transcripts called _session_msgcount 4072 times (one file was
# re-parsed 120 times) and _prompt_replies 571 times, each one re-reading a whole
# transcript: 2.37M json.loads for ~460 files' worth of information, and a Stop hook
# that blocked turn end for ~22s.
#
# Two layers:
#   • in-process — collapses the repeats WITHIN one render (4072 parses → 458).
#   • on-disk    — <data_dir>/cache/msgcounts.json, so a transcript that has not
#     changed is not re-parsed on the NEXT turn either. Counts ONLY: reply text is
#     prompt content and is never persisted, and the in-process layer already covers
#     the single render that needs it.
#
# Every layer is fail-open. This code runs inside the Stop hook, where an exception
# would block the user's turn, so a missing, malformed, or foreign cache file is
# ignored and the value simply recomputed. A cache is never a correctness dependency.

REPLIES_CACHE_MAX = 256              # reply maps held in memory at once (bounds a big render)
MSGCOUNT_MEM_MAX = 8192              # in-memory counts kept — a growing transcript mints a
                                     # new key per append, and the MCP server is long-lived

_MSGCOUNT_DISK = None    # {"file", "entries": {path: [mtime_ns, size, count, used]}, "dirty"}


if __name__ == "__main__":
    main()
