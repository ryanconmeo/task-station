# hook_health.py
"""Read side of the hook-health log that `hooks/_ts_lib.sh::ts_run` writes.

Every task-station hook call is masked so a hook can never fail or slow a session.
That is correct, and it stays. What it used to cost was visibility: a masked call
could be permanently broken and nobody would ever find out. `ts_run` now records
each non-zero exit as one tab-separated line under <data_dir>/logs/:

    <iso-8601 utc>\t<label>\t<exit code>\t<last non-blank line of stderr>

This module turns that file into the one-line SessionStart nag, into
`task-station hook-health`, and provides the clear. Bounding the file is the
WRITER's job (it keeps the newest TS_HOOK_LOG_MAX lines), so nothing here can let
it grow: the only writes are the nag stamp and `clear()`.

Everything is fail-open. A garbled line is skipped, an unreadable log means no
nag — a broken health report must never be worse than the silence it replaced.
"""
import calendar
import os
import time

import paths

LOG_NAME = "hook-health.log"
STAMP_NAME = ".hook-health-nagged"
RECENT_WINDOW = 86400        # a session start only reports the last 24h
NAG_LABELS = 3               # distinct labels named inline; the rest roll up as "+N more"
SUMMARY_LIMIT = 20           # rows `task-station hook-health` prints by default
_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


def logs_dir():
    return os.path.join(paths.data_dir(), "logs")


def log_path():
    """Resolved fresh on every call — paths.data_dir() reads the environment, and
    tests repoint it per-test."""
    return os.path.join(logs_dir(), LOG_NAME)


def stamp_path():
    return os.path.join(logs_dir(), STAMP_NAME)


def iso(epoch):
    """`ts_run`'s timestamp format, for the same instant. Kept here so the reader
    and its tests agree with the writer on one wire format."""
    return time.strftime(_TS_FMT, time.gmtime(epoch))


def _epoch(text):
    try:
        return calendar.timegm(time.strptime(text, _TS_FMT))
    except (ValueError, TypeError):
        return None


def parse_line(line):
    """One log line → {ts, iso, label, code, detail}, or None when it isn't one.

    Strict on the three fields that carry meaning (timestamp, label, numeric exit
    code) and lenient on the detail, which is free-form stderr. Extra tabs would
    mean the writer failed to flatten them, so the detail keeps whatever follows."""
    if not line or not line.strip():
        return None
    parts = line.rstrip("\n").split("\t")
    if len(parts) < 4:
        return None
    stamp, label, code = parts[0].strip(), parts[1].strip(), parts[2].strip()
    ts = _epoch(stamp)
    if ts is None or not label:
        return None
    try:
        code = int(code)
    except (TypeError, ValueError):
        return None
    return {"ts": ts, "iso": stamp, "label": label, "code": code,
            "detail": "\t".join(parts[3:]).strip()}


def entries(path=None):
    """Every parseable record, oldest first. Empty on a missing/unreadable log."""
    path = path or log_path()
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            raw = f.read().splitlines()
    except (OSError, IOError):
        return []
    return [e for e in (parse_line(ln) for ln in raw) if e]


def recent(now=None, window=RECENT_WINDOW, path=None):
    """Records inside `window` seconds of `now`, oldest first."""
    now = time.time() if now is None else now
    return [e for e in entries(path) if e["ts"] >= now - window]


def _read_stamp(path=None):
    try:
        with open(path or stamp_path(), encoding="utf-8") as f:
            return float((f.read() or "").strip())
    except (OSError, IOError, ValueError):
        return None


def _write_stamp(value, path=None):
    path = path or stamp_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("%d" % int(value))
    except (OSError, IOError):
        pass                 # a stamp we can't write means we may nag twice — harmless


def nag(now=None, path=None, stamp=None):
    """One line for the SessionStart context, or None.

    Capped two ways so it cannot spam: only the last RECENT_WINDOW counts, and a
    stamp records the newest failure already reported — the nag returns None until
    a NEWER one lands (or `clear()` resets it). Naming the stamp as a side effect
    of rendering mirrors how the delta brief advances its watermark."""
    hits = recent(now=now, path=path)
    if not hits:
        return None
    newest = max(e["ts"] for e in hits)
    seen = _read_stamp(stamp)
    if seen is not None and newest <= seen:
        return None                                   # already reported this state
    _write_stamp(newest, stamp)
    # Newest-first, one entry per label, so the roll-up names what broke last.
    per_label = {}
    for e in reversed(hits):
        per_label.setdefault(e["label"], e)
    shown = list(per_label.values())[:NAG_LABELS]
    named = ", ".join("%s (exit %d)" % (e["label"], e["code"]) for e in shown)
    more = len(per_label) - len(shown)
    if more > 0:
        named += ", +%d more" % more
    return ("[task-station] %d hook failure(s) recorded in the last %dh: %s. "
            "Hooks stayed non-fatal — see `task-station hook-health` "
            "(clear with `--clear`)." % (len(hits), RECENT_WINDOW // 3600, named))


def summary(limit=SUMMARY_LIMIT, path=None):
    """The newest `limit` records as display rows, oldest first."""
    rows = entries(path)[-limit:] if limit else entries(path)
    return ["%s  %-24s exit %-4s %s" % (e["iso"], e["label"], e["code"], e["detail"])
            for e in rows]


def clear(path=None, stamp=None):
    """Empty the log and drop the nag stamp. Returns how many records went."""
    path = path or log_path()
    n = len(entries(path))
    try:
        if os.path.exists(path):
            with open(path, "w", encoding="utf-8"):
                pass
    except (OSError, IOError):
        pass
    try:
        os.remove(stamp or stamp_path())
    except (OSError, IOError):
        pass
    return n
