"""brain.hooks — the brain plane's Claude Code hook surfaces, one module per hook.

Ported in 3.0.0 Phase 4 (chunk 5a). Four modules, one uniform namespace:

  * :mod:`brain.hooks.inject`  — SessionStart orientation + UserPromptSubmit
    keyword injection (from the source's ``scripts/context-inject.py``).
  * :mod:`brain.hooks.guard`   — PreToolUse(Bash) secret guard (from the
    source's ``hooks/guard.py``). Pure stdlib, no siblings.
  * :mod:`brain.hooks.gate`    — SessionStart heal-gate nag; a thin re-export of
    :mod:`brain.heal_gate`'s hook surface (chunk 4a owns the logic).
  * :mod:`brain.hooks.distill` — Stop-hook auto-distill; a thin re-export of
    :mod:`brain.distill`'s hook surface (chunk 3 owns the logic).

Every one of them is ``-m``-runnable (``python3 -m brain.hooks.<name>``, with
``lib/`` reachable) and every one holds the same contract: **a hook must never
break the session** — exit 0 on every path, swallow exceptions to the error log.
The two re-export modules exist so Phase 5 wires ONE namespace rather than
reaching into ``brain.heal_gate`` for one hook and ``brain.hooks`` for another;
they add no logic, and in particular they do not weaken that contract.

Registration itself (hooks.json / the board's hook mux) is Phase 5's, not this
package's — nothing here assumes it has been wired.
"""
