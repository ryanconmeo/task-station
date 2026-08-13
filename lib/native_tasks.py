"""Moved to board.native_tasks in 3.0.0 Phase 3 — identity shim, same module object."""
import sys
import board.native_tasks as _mod
sys.modules[__name__] = _mod
