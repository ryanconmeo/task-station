"""Bare-name import path for board.handles — identity shim, same module object."""
import sys
import board.handles as _mod
sys.modules[__name__] = _mod
