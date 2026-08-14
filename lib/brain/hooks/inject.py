"""brain.hooks.inject — zero-token context injection (the retrieval hooks).

PROVENANCE: ported in 3.0.0 Phase 4 (chunk 5a) from the brain source tree's
``scripts/context-inject.py`` @ 0.14.0 (hyphen -> underscore AND a move into the
``brain.hooks`` package, per the plan's port map). The keyword scan, the
starvation fix, the state file, the GC window and the emitted hook JSON are
unchanged.

Two modes (one module, both hook events):
  --session-start  SessionStart: inject a compact brain orientation + the
                   natural-language routing rules, fire the throttled weekly
                   lint and the throttled org-brain pull asynchronously, and GC
                   stale per-session inject state. (A SessionStart hook cannot
                   see the first user message — keyword work happens in
                   --prompt.)
  --prompt         UserPromptSubmit: word-boundary scan of the prompt for the
                   configured keywords; on a hit, rank the vault via the shared
                   ``search.search_hits()`` and inject the top 3 note
                   descriptions with a visible "[Brain: found N note(s) …]" line.
                   A keyword is only marked seen once a note it actually
                   surfaced is served, so a second keyword in the same prompt is
                   never starved.

Config: inject_context (bool, default true) disables --prompt injection;
inject_keywords IS the complete keyword list (set = replace, empty = disable).
The keyword list is written into the user's config by brain-init, never baked
into this shipped code.
Always exits 0 — a broken hook must never break a session.

THE PEERS TIER IS NEVER INJECTED. :func:`_inject_roots` builds its own curated
root list and never calls ``search.default_roots``, so ``include_peers`` has no
path to reach it: peers are explicit opt-in (CLI ``--peers`` / MCP ``peers:
true``) and a teammate's notes must never arrive in someone's context unasked.
``tests/brain/test_peers.py::test_injection_never_includes_peers`` is the guard.

TWO SPAWNS ARE INERT (see :func:`_spawn_weekly_lint` / :func:`_spawn_orgpull`).
The source launched both by ``__file__``-relative path; a package module with
relative imports cannot be run that way, and reaching back out of the package to
find ``lib/`` would be a fourth ``__file__`` anchor. Both are one-line seams,
patchable by tests, that Phase 5 re-points at real entry points.

THE ENTRY POINT IS ``-m``: ``python3 -m brain.hooks.inject --session-start`` /
``--prompt`` with ``lib/`` on ``PYTHONPATH``.

Layer rule: brain may import core and its own siblings, never board. Stdlib +
``brain.config`` / ``brain.search`` / ``brain.errorlog`` only.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

from .. import config
from .. import search
from .. import errorlog

TOPICS_TTL_SECONDS = 7 * 24 * 3600  # GC inject-*.topics older than this

# How a human re-runs the search this hook just did. The source printed a literal
# filesystem path to its ``scripts/brain.py``; the CLI is now a package module, so
# the honest spelling is the ``-m`` form with ``lib/`` reachable. Phase 5 may
# re-point this at whatever wrapper it ships (a ``bin/brain``, say) — one string.
SEARCH_HINT = "PYTHONPATH=<lib> python3 -m brain.search search <terms>"


def _emit(event, text):
    print(json.dumps({
        "hookSpecificOutput": {"hookEventName": event, "additionalContext": text}
    }))


def _routing_text():
    """The natural-language routing rules injected at SessionStart.

    The source read ``system-instructions.md`` from its plugin root, found by
    ``__file__`` math. That document is an ASSET (chunk 5b decides where the
    brain's shipped markdown lands), and the brain plane's ``__file__`` budget is
    spent, so this reads the plugin root from the environment — the same variable
    the harness sets for every hook it runs — and injects nothing when it is
    unset or the file is absent. Never raises."""
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if not root:
        return ""
    doc = Path(root) / "system-instructions.md"
    try:
        return doc.read_text(errors="ignore") if doc.exists() else ""
    except OSError as e:
        errorlog.record("inject:routing-doc", e)
        return ""


def _inject_roots(cfg):
    """Curated surfaces for prompt injection (notes/projects/reports), all one
    tier — search_hits handles ranking + dedup. NEVER the peers tier: see the
    module docstring."""
    roots = [(search.TIER_NOTES, cfg["vault"] / d) for d in ("notes", "projects", "reports")]
    return [(t, p) for t, p in roots if p.exists()]


def _gc_topics():
    """Session-start GC: delete inject-*.topics state files older than 7 days."""
    try:
        now = time.time()
        for f in config.state_dir().glob("inject-*.topics"):
            try:
                if now - f.stat().st_mtime > TOPICS_TTL_SECONDS:
                    f.unlink()
            except OSError as e:
                errorlog.record("inject:gc-unlink", e)
    except Exception as e:
        errorlog.record("inject:gc", e)


def _spawn_weekly_lint():
    """Fire the throttled weekly lint in the background (session context has the
    file-access grants a background daemon does not).

    INERT until Phase 5. The source spawned a sibling shell script
    (``scripts/lint-weekly.sh``) that owned BOTH the 7-day throttle stamp and the
    ``lint.py --notify --quiet`` invocation; that script is not part of this port,
    so there is nothing to launch yet. The engine it drove exists
    (:func:`brain.heal_lint.main` takes ``--notify``/``--quiet``) — what is
    missing is the throttle, which Phase 5 either re-ships as a script or folds
    into ``heal_lint`` (~10 lines: a ``.last-lint`` stamp under
    ``config.state_dir()`` and a 6-day window).

    Kept as a named seam rather than deleted so the SessionStart sequence — and
    the test that pins it — still says what is supposed to happen here."""
    return False


def _spawn_orgpull():
    """Fire the throttled org-brain pull, detached (keeps org team-rules fresh;
    self-throttles 24h).

    INERT until Phase 5, exactly like :func:`brain.orgpull._spawn_check` (D34).
    ``brain.orgpull.main()`` is the target and it is hook-shaped already (exits 0,
    swallows everything). The fix is one line —
    ``subprocess.Popen([sys.executable, "-m", "brain.orgpull"], start_new_session=True,
    stdout=DEVNULL, stderr=DEVNULL, env={**os.environ, "PYTHONPATH": <lib>})`` —
    but it needs ``lib/`` reachable in the CHILD env, which is a Phase-5 packaging
    decision, not a porting one. Spawning by path cannot work (relative imports)
    and finding ``lib/`` from here would mean new ``__file__`` math.

    Returns whether a pull was actually launched, so a caller can never mistake
    the inert seam for a fired one."""
    return False


def session_start(payload, cfg):
    _spawn_weekly_lint()
    _spawn_orgpull()
    _gc_topics()
    if not cfg["vault"].exists():
        return  # no vault yet — stay silent (brain-init scaffolds it)
    _emit("SessionStart",
          "brain-station is installed. Personal wiki (private brain): "
          f"\"{cfg['vault']}\" — search it with the brain skill or "
          f"`{SEARCH_HINT}` (zero tokens).\n\n"
          + _routing_text())


def prompt_scan(payload, cfg):
    if not cfg["inject_context"] or not cfg["vault"].exists():
        return
    keywords = [k for k in cfg["inject_keywords"] if isinstance(k, str) and k]
    if not keywords:  # empty list = injection disabled (config is the complete list)
        return
    prompt = payload.get("prompt") or ""
    if not prompt or prompt.startswith("/"):
        return
    present = [k for k in keywords if re.search(rf"\b{re.escape(k)}\b", prompt, re.I)]
    if not present:
        return

    session_id = payload.get("session_id") or "unknown"
    state = config.state_dir() / f"inject-{session_id}.topics"
    seen = set(state.read_text().split()) if state.exists() else set()
    fresh = [k for k in present if k.lower() not in seen]
    if not fresh:
        return

    roots = _inject_roots(cfg)
    if not roots:
        return
    # ONE search_hits call over ALL fresh keywords — merged ranking, no per-keyword
    # rg storm. Top 3 across the whole set (fixes the old "first keyword only" starvation).
    hits = search.search_hits(fresh, roots, limit=3)
    if not hits:
        return

    # A keyword is "served" only if a note it actually matched made the top 3. We
    # read just those ≤3 files (cheap, not an rg storm) to attribute accurately —
    # keywords whose notes were crowded out stay unseen and inject on a later prompt.
    served = set()
    for path, _score, _desc in hits:
        try:
            text = Path(path).read_text(errors="ignore")
        except OSError:
            text = Path(path).stem
        for k in fresh:
            if re.search(rf"\b{re.escape(k)}\b", text, re.I):
                served.add(k.lower())

    lines = [f"[Brain: found {len(hits)} note(s) on {', '.join(fresh)} — top {len(hits)}]"]
    for path, _score, desc in hits:
        lines.append(f"- [[{Path(path).stem}]]{' — ' + desc if desc else ''}  ({path})")
    # The config path is READ from config, never re-spelled here: the source
    # hard-coded its filename in this string and would have drifted the moment
    # the file was renamed (which the port did rename).
    lines.append("(From the user's private brain vault. Cite [[slugs]] if used. Injection is "
                 "throttled once per topic per session; disable via inject_context:false in "
                 f"{config._primary_config_path()}.)")

    if served:
        state.parent.mkdir(parents=True, exist_ok=True)
        with open(state, "a") as fh:
            fh.write("".join(k + "\n" for k in sorted(served)))
    _emit("UserPromptSubmit", "\n".join(lines))


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        payload = {}
    try:
        cfg = config.load()
        if mode == "--session-start":
            session_start(payload, cfg)
        elif mode == "--prompt":
            prompt_scan(payload, cfg)
    except Exception as e:
        # a hook must never break the session
        errorlog.record(f"inject:{mode.lstrip('-') or 'main'}", e)
    sys.exit(0)


if __name__ == "__main__":
    main()
