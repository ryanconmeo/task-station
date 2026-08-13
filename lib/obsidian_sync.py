"""Moved to board.obsidian_sync in 3.0.0 Phase 3 — identity shim, same module object."""
import sys
import board.obsidian_sync as _mod
sys.modules[__name__] = _mod
