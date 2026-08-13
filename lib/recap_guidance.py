"""Moved to board.recap_guidance in 3.0.0 Phase 3 — identity shim, same module object."""
import sys
import board.recap_guidance as _mod
sys.modules[__name__] = _mod
