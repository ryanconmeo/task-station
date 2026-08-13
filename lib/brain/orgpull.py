"""brain-station — throttled org brain auto-pull (SessionStart, async).

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 4a) from the brain source tree's
``scripts/org-brain-pull.py`` @ 0.14.0 (hyphen → underscore, per the plan's
module-name rule).

Keeps the org rules surface fresh without manual pulls: once every 24h, a
``git pull --ff-only`` in the org-brain clone. Launched detached from the
SessionStart hook so it never blocks a session; silent on every path; a failed
pull is recorded to the error log, not surfaced.

Guards:
  * no ``org_brain_clone`` configured, or it is not a git repo -> no-op
  * a pull within the last 24h (throttle stamp) -> skip
  * the stamp is touched BEFORE the pull, so a failing pull cannot retry-spam

The error-log labels and the stamp filename keep their source spellings
(``org-brain-pull*``, ``.last-org-brain-pull``) — they name the org-brain pull,
not an org, and chunk 5's team-rules suite asserts on them.

Layer rule: brain may import core and its own siblings, never board. Stdlib +
the sibling ``config`` / ``errorlog`` modules only. Python 3.9+.
"""
import subprocess
import sys
import time
from pathlib import Path

from . import config
from . import errorlog

DUE_AFTER_SECONDS = 24 * 3600
PULL_TIMEOUT = 60
STAMP_NAME = ".last-org-brain-pull"


def _clone_head(cfg):
    """Current HEAD sha of the org-brain clone, or ``None`` (absent/error)."""
    org_brain = cfg.get("org_brain_clone")
    if not org_brain or not (Path(org_brain) / ".git").exists():
        return None
    try:
        r = subprocess.run(["git", "-C", str(org_brain), "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() or None if r.returncode == 0 else None


def _spawn_check():
    """Launch the subscription-memo check DETACHED — fire-and-forget, fully
    isolated from this process (own session, no stdio), so it can never block or
    break the SessionStart path. Fail-open: a spawn error is a breadcrumb only.

    The target is the sibling ``subscribe`` module (chunk 4c). Running a package
    module by PATH cannot work while its imports are relative, so this spawn is
    inert until Phase 5 wires a real entry point — re-point this one line then.
    """
    script = Path(__file__).resolve().parent / "subscribe.py"
    try:
        subprocess.Popen(
            [sys.executable, str(script), "check"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
    except (OSError, subprocess.SubprocessError) as e:
        errorlog.record("org-brain-pull:spawn", e)


def maybe_notify_subscriptions(before, after):
    """Spawn the detached subscription-memo check IFF the clone HEAD actually
    moved across the pull. Returns whether it moved (and spawned). Gating here is
    the whole cost story: an up-to-date pull triggers nothing."""
    if after and after != before:
        _spawn_check()
        return True
    return False


def _stamp():
    return config.state_dir() / STAMP_NAME


def is_due(now=None):
    now = time.time() if now is None else now
    s = _stamp()
    if not s.exists():
        return True
    try:
        return (now - s.stat().st_mtime) > DUE_AFTER_SECONDS
    except OSError:
        return True


def _touch_stamp():
    _stamp().write_text("")  # mtime is the throttle signal


def pull(cfg):
    """Attempt the pull. Returns a short status string; never raises."""
    org_brain = cfg.get("org_brain_clone")
    if not org_brain or not (Path(org_brain) / ".git").exists():
        return "no org_brain git clone"
    try:
        r = subprocess.run(["git", "-C", str(org_brain), "pull", "--ff-only"],
                           capture_output=True, text=True, timeout=PULL_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        errorlog.record("org-brain-pull", e)
        return "pull error (logged)"
    if r.returncode != 0:
        errorlog.record("org-brain-pull", f"git pull exit {r.returncode}: {(r.stderr or r.stdout).strip()}")
        return "pull failed (logged)"
    return "pulled"


def main():
    try:
        cfg = config.load()
        if not is_due():
            return
        _touch_stamp()  # before the pull: even a failed pull counts (no retry-spam)
        before = _clone_head(cfg)
        pull(cfg)
        # If the org surface moved, tell every referencing task — detached, gated
        # on a real HEAD move, fail-open (a memo hiccup never breaks a session).
        maybe_notify_subscriptions(before, _clone_head(cfg))
    except Exception as e:
        errorlog.record("org-brain-pull:main", e)  # a hook must never break the session
    sys.exit(0)


if __name__ == "__main__":  # pragma: no cover — Phase 5 owns the entry point
    main()
