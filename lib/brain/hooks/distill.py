"""brain.hooks.distill — the Stop-hook auto-distill surface.

A THIN RE-EXPORT of :mod:`brain.distill`'s hook surface, added in 3.0.0 Phase 4
(chunk 5a) for the same reason as :mod:`brain.hooks.gate`: Phase 5 should wire
one ``brain.hooks.*`` namespace. NO LOGIC LIVES HERE — chunk 3 owns the distill
pass and all five of its guards (recursion env, stop_hook_active, the config
toggle, the transcript-length floor, the once-per-session state file).

``distill.main()`` is hook-shaped already: every path ends in ``sys.exit(0)`` and
the whole body is wrapped in an ``except`` that records a breadcrumb, so a failed
distill can never break a session. Re-exporting must not weaken that, so this
module adds no wrapper of its own — the name below IS the function.

  python3 -m brain.hooks.distill              # the real Stop-hook invocation
  python3 -m brain.hooks.distill --dry-run    # print the decision, spawn nothing
"""
from ..distill import main  # noqa: F401 — re-export IS this module's purpose

__all__ = ["main"]


if __name__ == "__main__":
    main()
