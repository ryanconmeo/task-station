"""Bare-name import path for board.sync — identity shim, same module object."""
import sys
import board.sync as _mod
sys.modules[__name__] = _mod
