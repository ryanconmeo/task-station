"""brain-station — the one place swallowed exceptions get recorded.

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 3) from the brain source tree's
``scripts/errorlog.py`` @ 0.14.0. Body behaviour is unchanged; the module's
``sys.path`` self-bootstrap is gone (it is a package module now) and the config
import is the relative sibling.

Hooks, the gate and the MCP server must never crash a session, so they catch
broadly. Historically that meant bare ``except: pass`` — errors vanished with
no trace. :func:`record` gives every such site a single-line breadcrumb in a
size-capped log without ever raising itself (a logging failure must not defeat
the very ``except`` that called it).

Location: ``<state_dir>/error.log`` — by default ``brain-state/`` under the
task-station data home (``brain.config.DEFAULT_STATE``), so relocating the data
home moves the log with everything else.
Cap: when the file exceeds 256 KB it is truncated to its newest quarter-meg (half the cap) — bounded
disk, recent history retained.

Layer rule: brain may import core and its own siblings, never board. Stdlib +
``brain.config`` only.

Pure stdlib, Python 3.9+.
"""
import datetime

from . import config

MAX_BYTES = 256 * 1024


def error_log_path():
    return config.state_dir() / "error.log"


def _truncate_half(path):
    try:
        data = path.read_bytes()
        if len(data) <= MAX_BYTES:
            return
        half = data[-(MAX_BYTES // 2):]
        nl = half.find(b"\n")  # start at a clean line boundary
        path.write_bytes(half[nl + 1:] if nl != -1 else half)
    except OSError:
        pass


def record(where, err):
    """Append one line ``<iso-ts>\\t<where>\\t<message>`` for a swallowed error.

    Never raises: any failure to log is itself swallowed silently, because the
    caller is inside an ``except`` whose job is to keep the session alive.
    """
    try:
        msg = str(err).replace("\n", " | ").replace("\r", " ")
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        line = f"{stamp}\t{where}\t{type(err).__name__}: {msg}\n"
        path = error_log_path()
        if path.exists() and path.stat().st_size > MAX_BYTES:
            _truncate_half(path)
        with open(path, "a") as fh:
            fh.write(line)
    except Exception:
        pass  # logging must never defeat the except: it protects
