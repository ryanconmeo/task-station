"""station.py — WHO wrote a task and WHICH MACHINE wrote it. The one owner of
station identity (task #532, J-track step 1).

TWO NOUNS, and they are not the same thing:

  OWNER    a PERSON, named by an alias (an org identity's local-part where there is
           one — `kosei`). Aliases are not free choice and they do not collide:
           the registry is a git file-claim, so a duplicate claim conflict-rejects.
  STATION  ONE MACHINE belonging to that owner, numbered from 0 (`station-0`).

The distinction is the whole reason this module exists. `seq` is handed out
machine-locally, so ONE OWNER ON TWO STATIONS is where identity actually breaks: an
unsynced laptop and desktop both hand out the same next number and two different
tasks both become `kosei-512`. Partitioning by (owner, station) is what makes that
impossible — see `sync.py`, which puts each station's writes in its own directory.

NUMBERS START AT 0, exactly like hub ordinals where `444-0` is real. That inherits a
known bug class: NOTHING MAY TREAT STATION 0 AS FALSY. Test for `is None`, never for
truthiness — `tests/test_sync.py:StationZeroTest` exists because of it.

THE LABEL IS DECORATION AND MUST STAY THAT WAY. The number is the identity: the
folder is the number, handles carry no station component, sync partitions by number,
and cross-task references are by uuid. So renaming a station is a one-field edit in
one file written by the only station that writes that folder — it cannot even
conflict, and there is nothing to migrate. THE INVARIANT THAT KEEPS IT FREE:
NOTHING MAY EVER COMPUTE ON THE LABEL — not a filter key, not a path, not a cache
key. The moment something does, renaming stops being free.

Label default = macOS `LocalHostName`, not `ComputerName`. Measured on this machine:
a ComputerName like "Sam's MacBook Pro" carries a CURLY apostrophe (U+2019), which survives
JSON silently and then breaks a grep, a shell one-liner or an HTML attribute later.
its LocalHostName is "Sams-MacBook-Pro" — already ASCII, no spaces, still recognisable.
Fallback chain: LocalHostName → hostname minus `.local` → `station-<n>`.

THE THROUGH-LINE, load-bearing in three places: STORE THE THING THAT MUST NEVER
CHANGE, DISPLAY THE THING HUMANS WANT TO CHANGE. Which task — uuid stored, seq typed.
Naming it across machines — full uuid stored, shortest unambiguous prefix displayed.
Which machine — number stored from 0, device-name label displayed.
"""
import json
import os
import re
import subprocess

# An alias names a DIRECTORY in the sync repo, so it is constrained at the source
# rather than escaped at every use: letters, digits, dot, dash, underscore. A `..`
# or a `/` in an alias would let one owner write outside their own partition, which
# is the single invariant the whole transport rests on.
ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

DEFAULT_NUMBER = 0

STATION_FILE = "station.json"


class BadAlias(ValueError):
    """An owner alias that cannot safely name a directory."""


def valid_alias(alias):
    """True when `alias` is safe to use as a partition directory name."""
    a = str(alias or "")
    return bool(ALIAS_RE.match(a)) and ".." not in a


def require_alias(alias):
    """`alias` if it is safe, else BadAlias. Every path-building call goes through
    this — a rejected alias must fail loudly at the boundary, never be silently
    sanitized into a DIFFERENT owner's partition."""
    if not valid_alias(alias):
        raise BadAlias(
            "owner alias %r is not usable as a partition name — allowed: a letter or "
            "digit, then letters, digits, dot, dash, underscore" % (alias,))
    return str(alias)


def owner():
    """This station's OWNER alias. Runtime config, never code:
    env TASK_STATION_SELF_ALIAS > config `self_alias` > the OS username.

    `feeds.self_alias()` delegates here, so the feed identity and the sync partition
    are the same name by construction rather than by coincidence."""
    env = os.environ.get("TASK_STATION_SELF_ALIAS")
    if env and env.strip():
        return env.strip()
    try:
        import config as _config
        v = _config.get("self_alias")
        if v and str(v).strip():
            return str(v).strip()
    except Exception:
        pass
    try:
        import getpass
        return getpass.getuser() or "self"
    except Exception:
        return "self"


def number():
    """This station's NUMBER, an int from 0. env TASK_STATION_STATION > config
    `station_number` > 0.

    Returns 0 — a real, valid station — when nothing is configured, so callers must
    never test it for truthiness."""
    raw = os.environ.get("TASK_STATION_STATION")
    if raw is None or str(raw).strip() == "":
        try:
            import config as _config
            raw = _config.get("station_number")
        except Exception:
            raw = None
    if raw is None or str(raw).strip() == "":
        return DEFAULT_NUMBER
    try:
        n = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_NUMBER
    return n if n >= 0 else DEFAULT_NUMBER


def _local_host_name():
    """macOS LocalHostName, or None. Never raises and never blocks for long."""
    try:
        out = subprocess.run(["scutil", "--get", "LocalHostName"],
                             capture_output=True, text=True, timeout=3)
        name = (out.stdout or "").strip()
        return name or None
    except Exception:
        return None


def label(n=None):
    """The FRIENDLY name for a station — display only, and nothing may compute on it.
    config `station_label` > LocalHostName > hostname minus `.local` > `station-<n>`.

    Labels MAY collide (two MacBook Pros default to near-identical names) and that is
    harmless, because the label is not the identity. The board must render NUMBER
    PLUS LABEL ("0 · Sams-MacBook-Pro") so a duplicate label is never ambiguous."""
    n = number() if n is None else n
    try:
        import config as _config
        v = _config.get("station_label")
        if v and str(v).strip():
            return str(v).strip()
    except Exception:
        pass
    name = _local_host_name()
    if name:
        return name
    try:
        import socket
        host = (socket.gethostname() or "").strip()
        if host:
            return host[:-6] if host.endswith(".local") else host
    except Exception:
        pass
    return dirname(n)


def dirname(n=None):
    """The partition directory name for station `n` — `station-0`, `station-1`, …
    THIS is the identity that paths are built from; the label never is."""
    n = number() if n is None else n
    return "station-%d" % int(n)


def parse_dirname(name):
    """The station number encoded in a `station-<n>` directory name, or None. Returns
    0 for `station-0` — a caller testing the result for truthiness would drop the
    first real station, so test `is None`."""
    m = re.match(r"^station-(\d+)$", str(name or ""))
    return int(m.group(1)) if m else None


def descriptor(n=None):
    """`{"number", "label"}` for a station — exactly what `station.json` holds. The
    number is written too (rather than left implicit in the folder name) so a
    hand-moved folder is detectable rather than silently re-identified."""
    n = number() if n is None else n
    return {"number": int(n), "label": label(n)}


def display(n=None, lbl=None):
    """`0 · Sams-MacBook-Pro` — number first, because the number is the identity."""
    n = number() if n is None else n
    return "%d · %s" % (int(n), lbl or label(n))


def read_descriptor(path):
    """The `station.json` in directory `path` as a dict, or None. Never raises."""
    try:
        with open(os.path.join(path, STATION_FILE), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except Exception:
        return None
