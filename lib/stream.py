"""Moved to board.stream in 3.0.0 Phase 3 — identity shim, same module object."""
import sys
import board.stream as _mod
sys.modules[__name__] = _mod
