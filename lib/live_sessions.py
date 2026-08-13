"""Moved to board.live_sessions in 3.0.0 Phase 3 — identity shim, same module object."""
import sys
import board.live_sessions as _mod
sys.modules[__name__] = _mod
