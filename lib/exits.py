"""Lives at board.exits — identity shim, same module object (the flat-name import path every engine module uses)."""
import sys
import board.exits as _mod
sys.modules[__name__] = _mod
