"""brain-station heal gate — episodic-aware dirty-gate for the heal pass.

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 4a) from the brain source tree's
``scripts/reconcile-gate.py`` @ 0.14.0. The source was mid-rename: its skill was
already ``/brain-heal`` while the module, its stamp file and its prose still said
"reconcile". The port completes the rename (module ``heal_gate``, stamp
``.last-heal``, "heal" throughout) — nothing else about the gate changed.

A heal is DUE when BOTH hold:
  1. more than 24h have passed since the last *completed* heal (the completion
     stamp records the timestamp), and
  2. the vault is dirty — something changed since that stamp: the vault git
     HEAD moved, OR a file is newer than the stamp in any of inbox/ (captures),
     mirror/health/ (lint), the task-station/ mirror, or the task-station
     tasks.db. A missing source is simply "not dirty" (graceful, never an error).

Clean days — and days inside the 24h window — skip at $0; that is the whole
cost model.

  <entry point>                 -> {"due": bool, "reasons": [...]}
  <entry point> --session-start -> SessionStart hook JSON: a one-line nag when
                                   due (EVERY session until the stamp updates),
                                   else nothing
  <entry point> --mark-done     -> record HEAD + completion time

Layer rule: brain may import core and its own siblings, never board. Stdlib +
the sibling ``config`` / ``errorlog`` modules only. Python 3.9+.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

from . import config
from . import errorlog
from . import notes as _notes

DUE_AFTER_SECONDS = 24 * 3600
STAMP_NAME = ".last-heal"


def _head(vault):
    try:
        r = subprocess.run(["git", "-C", str(vault), "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _newest_mtime(path):
    """Newest file mtime under ``path`` (a dir) or ``path``'s own mtime (a file).
    Returns None when the path is missing or holds no files — i.e. "not dirty"."""
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    if p.is_file():
        try:
            return p.stat().st_mtime
        except OSError:
            return None
    times = [f.stat().st_mtime for f in p.rglob("*") if f.is_file()]
    return max(times) if times else None


def _stamp_path():
    return config.state_dir() / STAMP_NAME


def compute(cfg, now=None):
    """Return ``{"due": bool, "reasons": [...]}`` for the given config."""
    now = time.time() if now is None else now
    vault = cfg["vault"]
    stamp = _stamp_path()

    if not vault.exists():
        return {"due": False, "reasons": ["no vault — run /brain-init"]}
    if not stamp.exists():
        return {"due": True, "reasons": ["no previous heal recorded"]}

    prev = {}
    try:
        prev = json.loads(stamp.read_text())
    except (json.JSONDecodeError, OSError):
        prev = {}
    last_ts = prev.get("ts")
    if last_ts is None:  # legacy stamp without a recorded ts -> fall back to file mtime
        try:
            last_ts = stamp.stat().st_mtime
        except OSError:
            last_ts = 0

    reasons = []
    head = _head(vault)
    if head and prev.get("head") and head != prev["head"]:
        reasons.append(f"vault HEAD moved ({str(prev['head'])[:8]} -> {head[:8]})")
    for label, path in (
        ("new inbox/ captures", vault / _notes.INBOX_DIR),
        ("new raw/ captures", vault / "raw"),                    # pre-fold vaults
        ("newer lint report", vault / _notes.HEALTH_DIR),
        ("newer lint report", vault / _notes.LEGACY_HEALTH_DIR),  # pre-fold vaults
        ("task-station mirror changed", vault / "task-station"),
        ("tasks_db changed", cfg.get("tasks_db")),
        ("episodic stream changed", cfg.get("episodic_stream")),
    ):
        m = _newest_mtime(path)
        if m is not None and m > last_ts:
            reasons.append(label)

    if not reasons:
        return {"due": False, "reasons": ["clean since last heal"]}
    age = now - last_ts
    if age <= DUE_AFTER_SECONDS:
        return {"due": False,
                "reasons": [f"dirty but only {int(age // 3600)}h since last heal (<24h gate)"]}
    return {"due": True, "reasons": reasons}


def mark_done(cfg, now=None):
    """Write the completion stamp (HEAD + timestamp). Called by the skill at the
    end of a heal pass."""
    config.require_valid()  # a heal pass writes the vault; refuse if config is broken
    now = time.time() if now is None else now
    _stamp_path().write_text(json.dumps({"head": _head(cfg["vault"]), "ts": now}))


def _nag_line(status):
    reason = status["reasons"][0] if status.get("reasons") else "changes since last heal"
    return f"brain-station: a heal is due ({reason}). Run /brain-heal."


def main():
    if "--mark-done" in sys.argv:
        mark_done(config.load())
        print("marked")
        return

    if "--session-start" in sys.argv:
        # Async SessionStart hook: nag EVERY session while due (no silent throttle);
        # must never break the session.
        try:
            status = compute(config.load())
            if status.get("due"):
                print(json.dumps({"hookSpecificOutput": {
                    "hookEventName": "SessionStart", "additionalContext": _nag_line(status)}}))
        except Exception as e:
            errorlog.record("heal-gate:session-start", e)
        sys.exit(0)

    print(json.dumps(compute(config.load())))


if __name__ == "__main__":  # pragma: no cover — Phase 5 owns the entry point
    main()
