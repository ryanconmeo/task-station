"""Moved to board.worktree_hook in 3.0.0 Phase 3 — identity shim, same module object."""
import sys
import board.worktree_hook as _mod
sys.modules[__name__] = _mod
