"""Lives at board.channel — identity shim, same module object (the flat-name import path every engine module uses)."""
import sys
import board.channel as _mod
sys.modules[__name__] = _mod
