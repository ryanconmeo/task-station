"""Moved to board.steps in 3.0.0 Phase 3 — identity shim, same module object."""
import sys
import board.steps as _mod
sys.modules[__name__] = _mod
