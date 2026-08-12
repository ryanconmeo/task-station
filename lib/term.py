"""Moved to core.term in 3.0.0 Phase 1 — identity shim, same module object."""
import sys
import core.term as _mod
sys.modules[__name__] = _mod
