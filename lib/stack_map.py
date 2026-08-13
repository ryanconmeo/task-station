"""Moved to board.stack_map in 3.0.0 Phase 3 — identity shim, same module object."""
import sys
import board.stack_map as _mod
sys.modules[__name__] = _mod
