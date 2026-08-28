"""Bare-name import path for board.station — identity shim, same module object."""
import sys
import board.station as _mod
sys.modules[__name__] = _mod
