"""brain.hooks.gate — the SessionStart heal-gate hook surface.

A THIN RE-EXPORT of :mod:`brain.heal_gate`'s hook surface, added in 3.0.0 Phase 4
(chunk 5a) so Phase 5 wires one uniform ``brain.hooks.*`` namespace instead of
reaching into ``brain.heal_gate`` for this hook and ``brain.hooks`` for the
others. NO LOGIC LIVES HERE — chunk 4a owns the gate, its stamp
(``heal_gate.STAMP_NAME``, read never re-spelled) and its 24h window.

``heal_gate.main()`` is hook-shaped already: ``--session-start`` swallows every
exception to the error log and ends in ``sys.exit(0)``, so a due-gate failure can
never break a session. Re-exporting must not weaken that, so this module adds no
wrapper of its own — the name below IS the function.

  python3 -m brain.hooks.gate --session-start   # nag when due, else silence
  python3 -m brain.hooks.gate --mark-done       # record HEAD + completion time
  python3 -m brain.hooks.gate                   # print the {"due": …} decision
"""
from ..heal_gate import main  # noqa: F401 — re-export IS this module's purpose

__all__ = ["main"]


if __name__ == "__main__":
    main()
