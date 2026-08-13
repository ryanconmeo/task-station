"""Moved to board.usage in 3.0.0 Phase 3 — identity shim, same module object."""
import sys
import board.usage as _mod
sys.modules[__name__] = _mod
