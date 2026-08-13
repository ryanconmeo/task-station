"""Moved to board.decisions in 3.0.0 Phase 3 — identity shim, same module object."""
import sys
import board.decisions as _mod
sys.modules[__name__] = _mod
