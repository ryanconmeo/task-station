"""Moved to board.hook_health in 3.0.0 Phase 3 — identity shim, same module object."""
import sys
import board.hook_health as _mod
sys.modules[__name__] = _mod
