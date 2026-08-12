"""Moved to core.pricing in 3.0.0 Phase 1 — identity shim, same module object."""
import sys
import core.pricing as _mod
sys.modules[__name__] = _mod
