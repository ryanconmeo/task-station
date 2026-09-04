#!/usr/bin/env python3
# hooklat.py
"""What each hook COMMAND actually cost, read back off the sessions that paid it.

WHY THIS EXISTS. ``/doctor`` reports hook latency aggregated per hook EVENT, so a
SessionStart spike is attributed to "SessionStart" and not to the command inside
it that spent the time. Every plugin registered on that event is then equally
suspect and none is convicted. The transcript the harness writes is finer than
the report built from it: each hook run lands as a ``hook_success`` /
``hook_error`` attachment carrying the literal ``command`` and its
``durationMs``. This reads THAT, so a number names a command.

TWO GRANULARITIES, ONE PARSER.

  * **Command** — the harness's own view: one row per (event, command).
  * **Child** — inside :mod:`hookmux`, which is ONE command to the harness and
    several programs to us. The mux prints a ``hookmux: timing`` line on stderr,
    the harness stores that stderr on the same attachment, and the line carries
    the PLUGIN VERSION it ran as. So a child row is version-stamped by the run
    that produced it, not by when someone thinks the install happened.

BEFORE AND AFTER COME OUT OF ONE RUN. ``--split-at <iso8601>`` cuts the same
population in two and reports both sides side by side. That is the point: a
latency delta assembled from two invocations on two days is two measurements of
two things, and the confounder is invisible. Label the sides with
``--before-label`` / ``--after-label`` so the report names versions rather than
"before".

USAGE

    python3 tools/hooklat.py                       # every command, all sessions
    python3 tools/hooklat.py --command hookmux --children
    python3 tools/hooklat.py --split-at 2026-09-04T02:00:00Z \
        --before-label 3.63.0 --after-label 3.64.0

Stdlib only, python3.9+.
"""
import argparse
import glob
import json
import os
import re
import sys

DEFAULT_ROOT = os.path.expanduser("~/.claude/projects")

# The mux's own stderr line. Kept in sync with lib/hookmux.py's _timing_line;
# tests/test_hooklat.py pins the two against each other.
TIMING = re.compile(r"hookmux: timing (?P<event>\S+) v(?P<version>\S+) (?P<pairs>.*)")
PAIR = re.compile(r"(?P<name>[^\s=]+)=(?P<ms>\d+(?:\.\d+)?)ms")


def _pct(values, q):
    """The ``q``-quantile of ``values`` (nearest-rank, the shape /doctor reports).
    Returns None for an empty sample rather than inventing a number."""
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[idx]


def _short(command):
    """A command's reporting name: the script or module it runs, not its path.

    ``python3 "${CLAUDE_PLUGIN_ROOT}/lib/hookmux.py" session-start`` → ``hookmux
    session-start``. Paths differ per install and per plugin version; the name
    does not, so rows from two versions land in the same bucket and can be
    compared at all."""
    text = command.strip()
    tokens = re.findall(r"[^\s\"']+", text)
    parts = []
    for token in tokens:
        if token in ("python3", "python", "bash", "sh", "node", "-m"):
            continue
        if "/" in token:
            token = token.rsplit("/", 1)[-1]
        if token.endswith(".py") or token.endswith(".sh") or token.endswith(".cmd"):
            token = token.rsplit(".", 1)[0]
        parts.append(token)
    return " ".join(parts) or text


def records(root=DEFAULT_ROOT, project=None):
    """Every timed hook run under ``root``, as dicts.

    A transcript is append-only JSONL written by a live session, so a truncated
    last line is normal and never fatal: an unparseable line is skipped, not
    raised."""
    pattern = os.path.join(root, project or "*", "*.jsonl")
    for path in sorted(glob.glob(pattern)):
        try:
            handle = open(path, "r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for line in handle:
                try:
                    doc = json.loads(line)
                except ValueError:
                    continue
                att = doc.get("attachment")
                if not isinstance(att, dict):
                    continue
                if att.get("type") not in ("hook_success", "hook_error"):
                    continue
                if not isinstance(att.get("durationMs"), (int, float)):
                    continue
                yield {
                    "ts": doc.get("timestamp") or "",
                    "event": att.get("hookEvent") or att.get("hookName") or "?",
                    "command": _short(att.get("command") or "?"),
                    "ms": float(att["durationMs"]),
                    "stderr": att.get("stderr") or "",
                    "ok": att.get("type") == "hook_success",
                    "path": path,
                }


# `total=` is the mux's own sum, printed first so a human reading the raw line gets
# the headline before the breakdown. It is not a child and must never be counted as
# one, or every mux run would report a phantom program that cost as much as all the
# real ones put together.
TOTAL = "total"


def child_rows(rec):
    """The per-child rows a mux run left on its own stderr, or nothing.

    Yields ``(version, event, child, ms)``. A run by a version that predates the
    instrument yields nothing at all — which is the honest answer, not a zero."""
    for line in rec["stderr"].splitlines():
        match = TIMING.search(line)
        if not match:
            continue
        for pair in PAIR.finditer(match.group("pairs")):
            if pair.group("name") == TOTAL:
                continue
            yield (match.group("version"), match.group("event"),
                   pair.group("name"), float(pair.group("ms")))


def _table(title, buckets, out):
    """One block: a row per bucket, widest-first by p50 so the cost leads."""
    out.write("%s\n" % title)
    if not buckets:
        out.write("  (no runs)\n\n")
        return
    width = max(len(k) for k in buckets)
    out.write("  %-*s %6s %9s %9s %9s\n" % (width, "command", "n", "p50", "p90", "max"))
    for key in sorted(buckets, key=lambda k: -(_pct(buckets[k], 0.5) or 0)):
        vals = buckets[key]
        out.write("  %-*s %6d %8.0fms %8.0fms %8.0fms\n"
                  % (width, key, len(vals), _pct(vals, 0.5),
                     _pct(vals, 0.9), max(vals)))
    out.write("\n")


def assert_under(buckets, ceiling, out):
    """Check every command bucket against a p50 CEILING and print one verdict line.

    THE CEILING GOES IN THE COMMAND, and the caller expects the pass token. That is
    the only shape of latency check that survives its own success: an expectation
    like "740ms" is falsified by the next honest improvement, while "under 3000ms"
    stays true until something regresses, which is exactly when it should go red.

    Prints ``hooklat: UNDER <ceiling>ms`` when every bucket is under it (an empty
    population is NOT under it — nothing measured is nothing proved), else
    ``hooklat: OVER ...`` naming the worst offender. Returns True on pass."""
    worst, worst_ms = None, -1.0
    for key, values in buckets.items():
        p50 = _pct(values, 0.5) or 0.0
        if p50 > worst_ms:
            worst, worst_ms = key, p50
    if worst is None:
        out.write("hooklat: NO RUNS — nothing measured is nothing proved\n")
        return False
    if worst_ms < ceiling:
        out.write("hooklat: UNDER %dms (worst: %s at %.0fms over %d runs)\n"
                  % (ceiling, worst, worst_ms, len(buckets[worst])))
        return True
    out.write("hooklat: OVER %dms — %s at %.0fms over %d runs\n"
              % (ceiling, worst, worst_ms, len(buckets[worst])))
    return False


def report(args, out=sys.stdout):
    """Print the tables and return the buckets, which is what the tests read."""
    sides = [("all", None, None)]
    if args.split_at:
        sides = [(args.before_label or "before", None, args.split_at),
                 (args.after_label or "after", args.split_at, None)]
    result = {}
    since = getattr(args, "since", None)
    for label, lo, hi in sides:
        lo = max(x for x in (lo, since) if x) if (lo or since) else None
        commands, children = {}, {}
        for rec in records(args.root, args.project):
            if lo and rec["ts"] < lo:
                continue
            if hi and rec["ts"] >= hi:
                continue
            if args.event and args.event.lower() not in rec["event"].lower():
                continue
            if args.command and args.command.lower() not in rec["command"].lower():
                continue
            commands.setdefault(rec["command"], []).append(rec["ms"])
            if args.children:
                for version, event, child, ms in child_rows(rec):
                    children.setdefault("v%s %s %s" % (version, event, child),
                                        []).append(ms)
        _table("== %s :: per command ==" % label, commands, out)
        if args.children:
            _table("== %s :: per hookmux child ==" % label, children, out)
        result[label] = {"commands": commands, "children": children}
        ceiling = getattr(args, "assert_p50_under", None)
        if ceiling:
            result[label]["passed"] = assert_under(commands, int(ceiling), out)
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--project", help="one project dir under --root")
    ap.add_argument("--event", help="substring filter on the hook event")
    ap.add_argument("--command", help="substring filter on the command name")
    ap.add_argument("--children", action="store_true",
                    help="also break hookmux runs down per child")
    ap.add_argument("--split-at", metavar="ISO8601",
                    help="report before/after this timestamp, from ONE pass")
    ap.add_argument("--before-label")
    ap.add_argument("--after-label")
    ap.add_argument("--since", metavar="ISO8601",
                    help="ignore runs before this timestamp")
    ap.add_argument("--assert-p50-under", type=int, metavar="MS",
                    help="print a PASS/FAIL verdict line against this p50 ceiling, "
                         "and exit non-zero when any command is over it")
    result = report(ap.parse_args(argv))
    if any("passed" in side for side in result.values()):
        return 0 if all(side.get("passed", True) for side in result.values()) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
