"""`board.previews` under its bare name — identity shim, same module object.

Matches the 3.0.0 Phase 3 shims beside it: the leaf modules are imported bare
(`import previews as _prev`) from inside `lib/board/`, and by name from tests.
"""
import sys
import board.previews as _mod
sys.modules[__name__] = _mod
