"""task-station 3.0.0 cmds package — the command seams, re-exported in package order (maintain → manage → view → sub → surface → loop)."""
from . import maintain as _maintain               # noqa: F401
from . import manage as _manage                   # noqa: F401
from . import view as _view                       # noqa: F401
from . import sub as _sub                         # noqa: F401
from . import surface as _surface                 # noqa: F401
from . import loop as _loop_cmds                  # noqa: F401
from board.cmds.maintain import *                 # noqa: F401,F403
from board.cmds.manage import *                   # noqa: F401,F403
from board.cmds.view import *                     # noqa: F401,F403
from board.cmds.sub import *                      # noqa: F401,F403
from board.cmds.surface import *                  # noqa: F401,F403
from board.cmds.loop import *                     # noqa: F401,F403

__all__ = [*_maintain.__all__, *_manage.__all__, *_view.__all__, *_sub.__all__,
           *_surface.__all__, *_loop_cmds.__all__]
