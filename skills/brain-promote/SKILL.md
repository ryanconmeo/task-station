---
name: brain-promote
description: Promote a personal brain note to the org brain (the shared org wiki) — strip personal context, convert to the org-brain schema, land it as a PR that a lead one-click approves (or queue it while the org brain isn't linked locally).
---

# /brain-promote — personal note → org brain

**Input:** `$ARGUMENTS` = one or more note slugs from the vault's `notes/`.

The mechanics live in `brain.promote` — this skill only decides *whether*
to promote and then *invokes* it. (Codifying the flow is deliberate: prose that
duplicated the token/push/PR mechanics was the #1 drift source. Org/project/repo,
the forge auth, and the clone path all resolve from config, never from this doc.)

> **Command form.** Every `python3 -m brain.<module>` line below runs with task-station's
> `lib/` directory on `PYTHONPATH`:
> `PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/lib" python3 -m brain.promote …`.
> Written out in full the first time, abbreviated after.

For each slug:

1. **Gate.** Promote only team knowledge. If the note is `scope: team`, it is
   eligible as-is. If it is not, confirm with the user that it is genuinely
   org-relevant (never promote secrets, machine-specific tooling facts, or
   person-directed content) and pass `--non-team` to opt it in explicitly.

2. **Promote.** Run:
   ```bash
   PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/lib" python3 -m brain.promote <slug> [--non-team] [--extent minor|major]
   ```
   The module: finds the merge target in the org-brain clone (reconcile vs create);
   strips personal context to the org-brain core schema; writes to a branch in the
   clone; and — when the forge is configured — pushes and opens the PR, printing
   its URL. `--extent` marks your contribution on a reconcile (default `minor`).
   - **No org-brain clone / forge configured?** It queues the fully-prepared
     note to `notes/_org-brain-queue.md` and says so — nothing else to do until the
     clone is linked.
   - **Prose polish first.** The strip is leak-removal (paths/session-ids/local
     keys), not copy-editing. Before promoting, make sure the body reads in the
     third person with absolute dates — nothing rewrites prose for you.

3. **A lead approves** the PR in the forge's UI (there is no approval surface in
   Claude). Rejected → edit the note → re-promote.

4. **After the PR merges — collapse to a reference.** Run:
   ```bash
   python3 -m brain.promote <slug> --finalize
   ```
   This replaces the private note's body with a one-line reference stub pointing
   at the now-canonical org node (org revision, provenance/tasks carried over).
   The full private history stays in vault git. The vault's `LOG.md` line and the
   INDEX line are updated for you.

Naming, reconcile detection, and reference records are documented in
`docs/brain-naming.md`.
