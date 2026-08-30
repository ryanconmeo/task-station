"""Maintenance command seam: the /heal surface (scan/plan/apply/mark/dismiss/dispose), the claims surface, and SessionStart."""
from board import _shared
from board._shared import *          # constants + utilities (explicit __all__)
from board.state import *
from board.model import *
from board.memos import *
from board.sessions import *
from board.render import *
from board.graph import *
from board.boardio import *
import os
import sys

import checker as _checker
import decisions as _dec
import station as _station
import sync as _sync
import heal as _heal
from board import heal_ado as _heal_ado
import loop as _loop
import turn as _turn
import hook_health
import steps as _steps
import store

g, set_g = _shared.g, _shared.set_g

__all__ = [
    "_heal_positional_ref", "_heal_targets", "_heal_scan_one",
    "_heal_scan_report", "_heal_block", "_heal_applied_block",
    "_heal_no_operations_block", "_heal_verb",
    "_split_str_list", "_split_int_list",
    "_heal_mark", "_heal_dismissals_report", "_heal_dismiss_writes",
    "_heal_goal_reviewed", "_heal_candidates_report", "_heal_dispose",
    "_CLAIMS_ACTIONS",
    "_claims_target", "_claims_writes", "_claims_show", "_claims_verify",
    "VERIFY_PASSED", "VERIFY_FAILED", "VERIFY_NOTHING",
    "cmd_claims", "cmd_heal", "cmd_session_start", "cmd_sync", "_sync_targets",
]


def _heal_positional_ref(a):
    """Fold a POSITIONAL task ref into `--task`, or return the refusal saying why it
    cannot be folded. None when there is nothing to fold.

    WHY THE POSITIONAL EXISTS. `commands/heal.md` runs
    `task-station.py heal --scan --session <sid> $ARGUMENTS`, so a user typing `/heal 12`
    hands this CLI a bare `12`. The heal subparser had no positional, so argparse exited
    with `unrecognized arguments: 12` and the command died before the scan ever ran — the
    ONE form a person actually types was the one form that could not work. The fix cannot
    live in the command file, either: `$ARGUMENTS` legitimately carries `--task <n>`,
    `--all`, or nothing at all, and there is no word to insert there that parses all four.

    ONE RESOLVER, NOT TWO. This only ever COPIES the ref into `--task`; `_heal_targets`
    still does every lookup, so a seq, a hub ordinal and an id prefix behave identically
    whichever way they were typed and there is no second resolution path to drift.

    TWO REFUSALS, and both of them are "do not guess which record you meant":

      * WITH `--all`. One names a single task and the other sweeps the board. Silently
        picking either would reconcile a scope nobody asked for, and dropping the ref
        without a word is how someone comes to believe they healed one task when they
        swept the whole board.
      * WITH A `--task` NAMING SOMETHING ELSE. A precedence rule would be invisible at
        the call site and the cost of it being wrong is a reconcile written onto the
        wrong task. The SAME ref twice is accepted instead of refused: `/todo heal 12`
        fills both slots from one word, and two spellings of one task is not a conflict.
        Compared case-insensitively, because an id prefix typed in either case still
        resolves to exactly the same record."""
    ref = str(getattr(a, "ref", None) or "").strip()
    if not ref:
        return None
    if getattr(a, "all", False):
        return ("heal: `%s` names ONE task and `--all` sweeps every open task, so the two "
                "cannot be combined — guessing the scope is how the wrong record gets "
                "reconciled. Pass one or the other. Nothing was read." % ref)
    named = str(getattr(a, "task", None) or "").strip()
    if named and named.casefold() != ref.casefold():
        return ("heal: `--task %s` and the positional `%s` name different tasks, and "
                "there is deliberately no precedence rule between them — a silent winner "
                "would reconcile a record you did not mean. Pass ONE of them. Nothing was "
                "read." % (named, ref))
    a.task = named or ref
    return None


def _heal_targets(a):
    """The tasks a `heal` invocation acts on, as `(tasks, error_line)`.

    Per-task by DEFAULT — `--task <ref>`, else the attached task. `--all` sweeps every
    open/active task on the board and is the only path that can touch more than one
    record, which is why it warns loudly about its scope before doing anything."""
    if getattr(a, "all", False):
        return [t for t in sorted_tasks() if is_on_board(t)], None
    ref = getattr(a, "task", None)
    if ref:
        task = resolve_ref(ref) or load_task(ref)
        if not task:
            return [], "No task matching '%s'.\n\n%s" % (ref, _format_list())
        return [task], None
    task = _session_task(getattr(a, "session", None))
    if not task:
        return [], ("No task attached — `heal --task <n>` for a specific task, or "
                    "`heal --all` to sweep the board.")
    return [task], None


def _heal_ado_owned(exclude=()):
    """Every work-item id claimed by a task on the board, for the sibling check to
    subtract. `exclude` drops the tasks being healed, so a task never reads its own
    claims as somebody else's ownership.

    Fails OPEN to the empty set: an unreadable board must make the check NOISIER,
    never quieter — a silently-empty ownership map would hide the orphan this check
    exists to find, and hiding is the failure mode under repair here."""
    skip = {t.get("id") for t in (exclude or ())}
    owned = set()
    try:
        for t in all_tasks():
            if t.get("id") in skip:
                continue
            owned |= {i["id"] for i in _heal_ado.claimed_items(t)}
    except Exception:
        return set()
    return owned


def _heal_scan_one(task, probe_branches=True, link_probe=None, ado_probe=None,
                   ado_owned=None):
    """Run the layer-1 scan for one task and persist its gate file. Never mutates the
    task.

    `probe_branches` wires BOTH git probers — the branch one and the cited-commit one. They
    are one switch because they are one cost: the same `git -C <repo> rev-parse` discovery
    walk, and the same rule that the cheap SessionStart path must spawn no subprocess at all
    (`_heal.nag` calls `scan` with no probers, and that is what keeps a session start
    free).

    `link_probe` stays separate and stays OFF unless `--probe-links` asks: git is local and
    bounded, HTTP is neither. `ado_probe` is the same bargain for `--probe-ado`: it reads
    every work item the task claims, which is several authenticated round trips."""
    bp = _heal.branch_prober(task) if probe_branches else None
    cp = _heal.commit_prober(task) if probe_branches else None
    result = _heal.scan(task, branch_probe=bp, link_probe=link_probe, commit_probe=cp,
                        ado_probe=ado_probe, ado_owned=ado_owned)
    _heal.write_gate(result)
    return result


def _heal_scan_report(task, result):
    """`heal --scan` output for one task: the nine finding checks, each reported clean
    or with its hits, plus the health metric that is the tenth. Zero tokens of
    judgment — this is the deterministic layer, printed verbatim.

    Below the checks sit the sections that are NOT checks: the MERGE CANDIDATES proposed
    from their leading shape, the expected-ephemeral count, the PINNED set to re-read, the
    GOAL LINE with what has landed since it was written, and what has ACCRUED since the
    last heal. None is a defect, so none reaches `Heal due?` — that line is computed from
    the findings alone, and mixing these in would put YES on a perfectly reconciled task.

    THE ACCRUAL SECTION IS PRINTED HERE ON PURPOSE, not only in the dry run. A clean scan
    is where the skill STOPS (stamp it and report), so a gap named only in the dry run is
    one nobody sees on a clean task — and the incident that added it was a task that
    scanned clean on every check while a shipped release sat recorded nowhere on it.

    IT CLOSES ON THREE ROWS, NOT ONE (`heal.summary_lines`): what the machine checked,
    whether the half it cannot check has been recorded, and only then the verdict. A lone
    `Heal due? no` reads as "this task is a complete record", which is the reading that
    let both of the incidents above pass for healthy."""
    seq = task.get("seq", task["id"][:8])
    out = ["[HEAL-SCAN] Task #%s [%s] — %s" % (seq, task["id"][:8], task["title"])]
    out.append("  %-28s %s" % ("Health", _heal.health_line(result.get("health") or {})))
    out.extend(_heal.scan_lines(result))
    out.extend(_heal.dismissed_line(result))
    # Proposals, information and counts, BELOW the findings and never mixed into them:
    # none of these is a defect, and `Heal due?` below is computed from the findings
    # alone (plus the one goal-review limb, which says so in its own row).
    out.extend(_heal.size_lines(result))
    out.extend(_heal.subject_candidate_lines(result))
    out.extend(_heal.merge_candidate_lines(result))
    out.extend(_heal.oversized_proposal_lines(result))
    out.extend(_heal.ephemeral_lines(result))
    out.extend(_heal.pinned_lines(result))
    out.extend(_heal.goal_review_lines(result))
    out.extend(_heal.ado_lines(result))
    out.extend(_heal.accrual_lines(result))
    out.extend(_heal.summary_lines(task, result))
    return "\n".join(out)


def _heal_block(task, result, ops, applied=None, backup=None, before=None):
    """The model-facing [HEAL] block — layer 2's brief.

    Carries the scan findings, the health metric, the FULL current decision set (the
    pass cannot reconcile what it cannot see), the mechanical plan, and the exact verb
    commands for the judgment calls a machine must not make on its own. `applied` is
    None on a dry run and the `(lines, n, skipped)` triple after an --apply.

    IT ALSO CARRIES THE GOAL LINE AND THE LIVE CHECKLIST, printed together right after
    the decision set. They were absent for the same reason the checks never looked at
    them: all three verbs here are DECISION verbs. But the goal says what DONE looks
    like and the checklist says what to DO, so they are what a cold session reads FIRST,
    while the decisions mostly say WHY — and on one real task both had been overtaken
    while every check reported clean. They sit BESIDE the newest decisions on purpose:
    those decisions are the evidence that retires them, and the one question this pass
    must ask of each is whether it does."""
    seq = task.get("seq", task["id"][:8])
    h = result.get("health") or {}
    out = []
    out.append("[HEAL] Task #%s [%s] — %s" % (seq, task["id"][:8], task["title"]))
    out.append("%s. Reconcile this task's APPEND-ONLY decision log into CURRENT STATE."
               % ("APPLIED — the mechanical plan below has been performed"
                  if applied else "DRY RUN — nothing has been changed"))
    out.append("")
    if before:
        out.append("HEALTH BEFORE: %s" % _heal.health_line(before))
    out.append("HEALTH: %s" % _heal.health_line(h))
    out.append("")
    out.append("SCAN (layer 1 — deterministic, no judgment):")
    out.extend(_heal.scan_lines(result))
    out.extend(_heal.dismissed_line(result))
    out.extend(_heal.size_lines(result))
    out.extend(_heal.subject_candidate_lines(result))
    out.extend(_heal.merge_candidate_lines(result))
    out.extend(_heal.oversized_proposal_lines(result))
    out.extend(_heal.ephemeral_lines(result))
    out.extend(_heal.pinned_lines(result))
    out.extend(_heal.goal_review_lines(result))
    out.extend(_heal.ado_lines(result))
    out.extend(_heal.accrual_lines(result))
    out.extend(_heal.summary_lines(task, result))
    out.append("")
    out.append("MECHANICAL PLAN (what --apply does on its own):")
    out.extend(_heal.plan_lines(ops))
    if applied:
        lines, n, skipped = applied
        out.append("")
        out.append("APPLIED %d operation(s), skipped %d:" % (n, skipped))
        out.extend("  • %s" % ln for ln in (lines or ["(none)"]))
        if backup:
            out.append("  Backup of the pre-heal task blob: %s" % backup)
        out.append("  STAMPED: this task now records that a heal happened (%s), so the "
                   "next scan counts new decisions from HERE instead of reporting the "
                   "whole log as unreconciled." % _heal.heal_stamp_line(h))
        out.append("  NOTHING was deleted. Every original is still in "
                   "`/todo %s history`, marked with what replaced it." % seq)
        undo = _heal.undo_lines(ops)
        if undo:
            out.append("")
            out.extend(undo)
            if backup:
                out.append("  • WHOLE-TASK FALLBACK, for anything above with no verb of "
                           "its own: the pre-heal blob at %s." % backup)
    out.append("")
    out.append("CURRENT DECISIONS (%d) — reconcile THESE:" % h.get("decisions_current", 0))
    for i, d in _dec.live(task.get("decisions")):
        txt = _dec.text(d)
        out.append("  %2d. %s%s" % (i, DECISION_PIN_MARK if _dec.is_pinned(d) else "", txt))
    if not h.get("decisions_current"):
        out.append("  (none current)")
    # The GOAL and the CHECKLIST, immediately below the decisions and immediately below
    # the NEWEST of them, because the newest evidence is what retires them. One question
    # for both, and it is the question no check can ask.
    live_steps = _steps.live(task.get("steps"))
    goal = str(task.get("goal") or "").strip()
    out.append("")
    out.append("THE GOAL LINE — does the newest evidence above retire this?")
    out.append("  %s" % (goal or "(none set)"))
    out.append("")
    out.append("THE LIVE CHECKLIST (%d step(s)) — for EACH ONE: does the newest evidence "
               "above retire this?" % len(live_steps))
    for i, s in live_steps:
        out.append("  %s %2d. %s" % ("✓" if _steps.is_done(s) else "☐", i,
                                     _steps.text(s)))
    if not live_steps:
        out.append("  (no steps)")
    out.append("  WHY THESE TWO ARE HERE. A cold session reads the goal and the checklist "
               "FIRST — they say what DONE looks like and what to DO, while the decisions "
               "above mostly say WHY. A decision that was right when written and was "
               "later refuted by REALITY leaves nothing inconsistent behind, so no check "
               "can see that the goal describes a mission already accomplished or that a "
               "step names work a superseded decision retired. One real task held both "
               "while all its checks reported clean, and the checklist went on reading as "
               "the plan. Where the wording overlapped enough to spot mechanically, the "
               "scan lists it above as a STEP RESTATING A SUPERSEDED DECISION — a "
               "PROVISIONAL finding that still needs your reading.")
    out.append("")
    out.append("NOW DO THE JUDGMENT WORK — the part a machine must not guess at. For "
               "each item below, decide and act; do NOT invent history, and NEVER "
               "delete a decision (no verb can):")
    out.append("  1. SUPERSEDE what is now WRONG — `update --task %s --decision "
               "'<the correct call + why>' --supersedes <n>` (repeatable). Use this "
               "only when something refuted it." % seq)
    out.append("  2. SPLIT what is COMPOUND — a decision mixing still-valid rulings "
               "with refuted ones cannot be superseded without destroying the good "
               "half. Add the atomic parts with `--decision` (one call each), note "
               "their numbers, then run `heal --split <n> --into <n1,n2,…> --task %s`." % seq)
    out.append("  3. MERGE what is TRUE BUT NO LONGER LOAD-BEARING — release records, "
               "iteration steps, process-error corrections. Two tiers of candidate are "
               "listed in the scan above and both are proposals, not findings: SUBJECT "
               "candidates (they name the same step, release or PR/story — the stronger "
               "evidence, and a group tagged COMPLETED-SUBJECT has every step it names "
               "already done or superseded, so the work it records is finished) and MERGE "
               "candidates (they merely open the same way). `heal --candidates --task %s` "
               "prints every group's members IN FULL and nothing else, which is the cheap "
               "way to read them without the whole corpus. Add the one summary decision, "
               "then `heal --merge <n1,n2,…> --into <n> --task %s`. Do NOT supersede these: "
               "nothing refuted them, and "
               "saying so would put a lie in the record. A RE-FRAGMENTED CONSOLIDATION "
               "(check 9, and a FINDING rather than a proposal) is the same verb with the "
               "consolidation itself folded in: an earlier pass already ruled that "
               "subject was one entry, so write ONE updated summary covering the strays "
               "too and merge the old consolidation in with them." % (seq, seq))
    out.append("  4. RETRO-DISPOSE every undispositioned ack — `heal --apply --task %s "
               "--dispose-acks <id8,…|all> --noop '<why nothing was needed>'` (or "
               "--decision '<what it changed>' / --memory <slug>). `all` is legitimate "
               "here: those acks were made by sessions that no longer exist, so one bulk "
               "--noop with an honest reason IS the correct disposition. Every "
               "retro-fill is marked RETRO with who filled it and when — the original "
               "ack's session and timestamp are never rewritten." % seq)
    out.append("  5. RETIRE any STALE STEP — `update --task %s --step-add '<the "
               "corrected step>' --step-supersede <n>`. The stale step leaves the "
               "checklist and BOTH sides of the n/m count, keeps its text in history "
               "marked with what replaced it, and `--step-restore <n>` undoes it. Do NOT "
               "tick it done (nobody did it) and do NOT add a warning step about it." % seq)
    out.append("  6. REWRITE the `state` line if it drifted — `update --task %s "
               "--state 'NEXT: <concrete first move> — <current standing>'`. Fix a "
               "drifted `--summary` the same way." % seq)
    out.append("  7. RE-READ every PINNED decision listed in the scan above. A pin puts "
               "that entry at the head of EVERY session's digest, so a line that has "
               "quietly gone stale in one costs more than the same line anywhere else — "
               "one real task briefed sessions for days with two codenames a later "
               "decision had already retired. Nothing here is a defect on its own; the "
               "check is whether it is still ACCURATE. If it is not, supersede it (1) or "
               "split it (2); if it no longer needs to lead, `update --task %s "
               "--unpin-decision <n>`." % seq)
    out.append("  8. RE-READ THE GOAL LINE AND EVERY LIVE STEP — both are printed below "
               "the decision set, and the question for each is the same: DOES THE NEWEST "
               "EVIDENCE RETIRE THIS? A goal describing a mission the record now shows as "
               "accomplished, and a step naming work a superseded decision retired, are "
               "read as INSTRUCTIONS by the next cold session, and neither leaves anything "
               "inconsistent behind for a check to catch. Rewrite a drifted goal with "
               "`update --task %s --goal '<what done looks like now>'`; retire an "
               "overtaken step with `update --task %s --step-add '<the corrected step>' "
               "--step-supersede <n>`. The GOAL REVIEW line in the scan says how many "
               "decisions have landed since the goal was last written — a count, not an "
               "accusation. Any STEP RESTATING A SUPERSEDED DECISION listed above IS a "
               "finding, but a PROVISIONAL one: read the step and the decision together, "
               "because the step written to RECORD a retirement uses the same words as "
               "the step that still orders it." % (seq, seq))
    out.append("  9. Prose that CLAIMS a supersession must become structure — the "
               "digest cannot act on a sentence saying \"decision 4 was wrong\".")
    out.append(" 10. VERIFY THAT EVERYTHING WHICH ACTUALLY SHIPPED SINCE THE LAST HEAL "
               "HAS A DECISION — a release, a merged PR, a document. This is the ONE gap "
               "the deterministic layer STRUCTURALLY cannot cover, and the third thing "
               "on this list that no check will ever raise (the others are 7, whether a "
               "pinned decision is still accurate, and 8, whether the goal and the "
               "checklist have been overtaken). Every check works by "
               "cross-referencing two things the task itself holds; work that is recorded "
               "NOWHERE on it leaves nothing to cross-reference, so the scan cannot tell "
               "that apart from nothing having happened. The ACCRUED line above says how "
               "much has been recorded since the stamp — check it against what you know "
               "shipped, from the conversation and the repo, and record whatever is "
               "missing with `update --task %s --decision '<what shipped + why>'` (and "
               "`--pr <url>` / `--log '<vX.Y.Z shipped: what>'`). A clean scan means the "
               "record does not contradict itself; it does NOT mean the record is "
               "complete." % seq)
    out.append(" 11. RE-READ THE GOAL LINE EVEN WHEN IT IS RIGHT, and record that you did: "
               "`heal --goal-reviewed --task %s`. That resets the goal-review count without "
               "rewriting a sentence that is still true — and it is the ONLY thing that "
               "does. A `--mark-healed --note` deliberately does not, because a stamp "
               "saying somebody read the record is not a stamp saying they ruled on THIS "
               "LINE, and the difference is what makes every other stamp readable." % seq)
    out.append(" 12. A FINDING THAT IS GENUINELY WRONG can be ADJUDICATED AWAY rather than "
               "left to reappear every pass: `heal --apply --task %s --dismiss "
               "'<check>:<ref>' --why '<why it is not a defect>'`. The why is mandatory. It "
               "silences ONE exact finding text — edit the entry it names and the finding "
               "re-reports, because the ruling covered the sentence that was there, not the "
               "category. `heal --dismissals --task %s` lists every ruling with its why, "
               "and `--undismiss` retires one. Use it for a false positive you have READ, "
               "never as a way to quieten a report you have not." % (seq, seq))
    out.append("")
    out.append("OUT OF SCOPE: do NOT touch the `--log` milestone trail or `history` — "
               "they are append-only and sacred. No verb rewrites a step or a decision "
               "in place: supersede it and add the corrected one.")
    if applied is None:
        out.append("")
        if [o for o in ops if not o.get("manual")]:
            out.append("This was a DRY RUN — the default. Nothing changed. Run `heal "
                       "--apply --task %s` to perform the mechanical plan (it backs the "
                       "task blob up first), or just work the judgment list above by "
                       "hand. `--apply` prints ONLY what it did and does NOT reprint "
                       "this block — reprinting it is what used to make one heal cost "
                       "two." % seq)
        else:
            out.append("This was a DRY RUN — the default, and there is nothing mechanical "
                       "to apply. A bare `heal --apply --task %s` would perform zero "
                       "operations, so it REFUSES rather than stamping a heal that never "
                       "happened. Work the judgment list above, then record the pass "
                       "below." % seq)
        out.append("Reconciled it by JUDGEMENT alone and nothing needed changing? Record "
                   "that with `heal --mark-healed --task %s --note '<what you checked>'` "
                   "— otherwise the record still says this task has never been healed, "
                   "and every session opens on a false alarm." % seq)
    out.append("    " + _cli_fallback())
    return "\n".join(out)


def _heal_applied_block(task, result, applied, backup, ops=None, before=None):
    """What `--apply` prints: ONLY what it did.

    THE COST THIS EXISTS TO STOP. `--apply` used to re-render the entire dry run — the
    scan block, the merge candidates, the pinned set, every current decision, and the
    nine-item judgment list — with the applied lines bolted on. Measured on one small
    task the two blocks were 4,021 and 4,174 characters, i.e. the same block twice; on a
    real 40-decision task the dry run is ~47,000 characters and **94% of it is the
    decision list**. So the obvious two-step — read `heal`, then run `heal --apply` —
    paid ~12,000 tokens TWICE for ONE heal, and the second copy told the caller nothing
    it had not just read.

    So this carries the things only the apply knows: what was performed, what was
    skipped, where the backup went, HOW TO TAKE EACH WRITE BACK, and what the task looks
    like now against what it looked like before. The scan, the decision list and the
    judgment list are deliberately absent — `--verbose` renders the full `_heal_block`
    for anyone who wants them.

    THE UNDO SECTION IS WHAT REPLACES THE APPROVAL GATE. `/heal` no longer stops to ask
    before applying, so this report is the only place a wrong call can be caught — and
    "every heal is reversible" is not enough to catch one with, because acting on it
    means first working out which decision numbers moved. `heal.undo_lines` prints the
    exact command per performed op instead, and says so plainly for the retro-disposition,
    which has no inverse verb at all.

    `before` is the pre-apply health dict; with it the report reads BEFORE → NOW rather
    than leaving the reader to remember a number from the previous block."""
    seq = task.get("seq", task["id"][:8])
    h = result.get("health") or {}
    lines, n, skipped = applied
    out = []
    out.append("[HEAL] Task #%s [%s] — %s" % (seq, task["id"][:8], task["title"]))
    out.append("APPLIED %d operation(s), skipped %d. This report is ONLY what the apply "
               "did — the scan, the current decisions and the judgment list are not "
               "reprinted, because you just read them in the dry run and the decision "
               "list alone is ~94%% of that block. `heal --apply --verbose --task %s` "
               "prints the whole thing." % (n, skipped, seq))
    out.append("")
    out.append("WHAT IT DID:")
    out.extend("  • %s" % ln for ln in (lines or ["(none)"]))
    if backup:
        out.append("  Backup of the pre-heal task blob: %s" % backup)
    out.append("  STAMPED: this task now records that a heal happened (%s), so the next "
               "scan counts new decisions from HERE instead of reporting the whole log "
               "as unreconciled." % _heal.heal_stamp_line(h))
    out.append("  NOTHING was deleted. Every original is still in `/todo %s history`, "
               "marked with what replaced it." % seq)
    undo = _heal.undo_lines(ops)
    if undo:
        out.append("")
        out.extend(undo)
        if backup:
            out.append("  • WHOLE-TASK FALLBACK, for anything above with no verb of its "
                       "own: the pre-heal blob at %s is the task exactly as it stood "
                       "before this run." % backup)
    out.append("")
    if before:
        out.append("HEALTH BEFORE: %s" % _heal.health_line(before))
    out.append("HEALTH NOW: %s" % _heal.health_line(h))
    is_due, reasons = _heal.due(task, result=result)
    out.append("STILL OUTSTANDING: %s"
               % (("%s — the judgment work is not done; `heal --scan --task %s` shows it"
                   % ("; ".join(reasons), seq)) if is_due
                  else "nothing the scan can see — no finding, no heal due. It reads the "
                       "RECORD only, so confirm separately that everything which actually "
                       "shipped since the last heal has a decision: that is the one gap "
                       "no check here can cover"))
    return "\n".join(out)


def _heal_no_operations_block(task, result, ops, attempted=None):
    """What a `--apply` that has NOTHING to perform prints instead of a stamp.

    THE BUG THIS CLOSES. A bare `--apply` on a task with an empty mechanical plan
    performed zero operations, honestly said `APPLIED 0 operation(s)` — and then stamped
    the heal anyway, so the record claimed the task had been reconciled when nothing had
    happened. It did not silence the nag (real findings still make a heal due), but the
    timestamp was a lie, and it is exactly the command someone runs when they assume
    `--apply` IS the heal rather than the mechanical subset of one.

    A stamp that is sometimes false is worse than the always-on alarm the stamp was
    added to fix, because it makes every OTHER stamp unreadable. So this refuses, changes
    nothing, writes no backup, and names the two moves that are actually true.

    `attempted` is the run's own report lines when the plan HAD operations and every one
    of them failed — a different story from an empty plan, and the reader needs to see
    which one happened."""
    seq = task.get("seq", task["id"][:8])
    h = result.get("health") or {}
    out = []
    out.append("[HEAL] Task #%s [%s] — %s" % (seq, task["id"][:8], task["title"]))
    out.append("REFUSED: --apply performed no operation, so nothing was changed and "
               "NO heal was stamped. A stamp says this task WAS reconciled; writing one "
               "for a pass that did nothing would put a false `last heal just now` in the "
               "record, and one stamp that lies makes every other stamp unreadable.")
    out.append("")
    if attempted:
        out.append("EVERY PLANNED OPERATION FAILED — nothing was written:")
        out.extend("  • %s" % ln for ln in attempted)
    else:
        out.append("MECHANICAL PLAN (what --apply would have performed):")
        out.extend(_heal.plan_lines(ops))
    out.append("")
    out.append("TWO REAL OPTIONS — take whichever is true:")
    out.append("  1. THERE IS WORK, so name it. `heal --apply --task %s --dispose-acks "
               "<id8,…|all> --noop '<why nothing was needed>'` retro-fills old acks; "
               "`heal --split <n> --into <n1,n2,…> --task %s` and `heal --merge "
               "<n1,n2,…> --into <n> --task %s` record a split or a merge (add the "
               "replacement decisions with `update --decision` first). The judgment "
               "verbs need no --apply at all — `update --task %s --decision '<the "
               "correct call>' --supersedes <n>` and `update --task %s --step-add "
               "'<the corrected step>' --step-supersede <n>` write immediately."
               % (seq, seq, seq, seq, seq))
    out.append("  2. YOU RECONCILED IT BY JUDGEMENT and nothing needed changing: `heal "
               "--mark-healed --task %s --note '<what you checked>'`. That is the honest "
               "record of this pass, and it is what stamps." % seq)
    out.append("")
    out.append("HEALTH: %s" % _heal.health_line(h))
    out.extend(_heal.summary_lines(task, result))
    return "\n".join(out)


def _heal_verb(a):
    """The explicit `heal --split N --into …` / `heal --merge N,M --into N` verbs.

    These exist so the LLM pass can act on ITS OWN reading of the content — it adds the
    atomic parts (or the one summary) with `update --decision`, then names the mapping
    here. Returns a result line. Non-destructive and reversible like every other path:
    the original is marked, never removed."""
    if getattr(a, "all", False):
        return ("heal: --split/--merge name decision numbers on ONE task, so they cannot "
                "be combined with --all. Target it with `--task <n>`.")
    tasks, err = _heal_targets(a)
    if err:
        return err
    task = tasks[0]
    entries = task.get("decisions") or []
    if not entries:
        return "heal: task #%s has no decisions to reconcile." % task.get("seq")
    into = _split_int_list(getattr(a, "into", None))
    msgs = []
    split_ref = getattr(a, "split", None)
    merge_ref = getattr(a, "merge", None)
    if split_ref is not None:
        if not into:
            return ("heal --split %s: pass `--into <n1,n2,…>` naming the decisions it "
                    "became (add them first with `update --decision`)." % split_ref)
        ok, e = _dec.mark_split(entries, split_ref, into)
        msgs.append(("split decision %s into %s — the original is kept in history, "
                     "marked, and `update --restore-decision %s` undoes it"
                     % (split_ref, ", ".join(str(n) for n in into), split_ref))
                    if ok else "heal: %s" % e)
    if merge_ref is not None:
        members = _split_int_list(merge_ref)
        if len(into) != 1:
            return ("heal --merge %s: pass `--into <n>` naming the ONE decision that "
                    "absorbed them (add it first with `update --decision`)." % merge_ref)
        if not members:
            return "heal --merge: name the decisions to merge, e.g. `--merge 3,7,9`."
        done = []
        for n in members:
            ok, e = _dec.mark_merged(entries, n, into[0])
            if ok:
                done.append(n)
            else:
                msgs.append("heal: %s" % e)
        if done:
            # The indices are known HERE, so the undo names them. This path writes
            # immediately and takes no backup, so a generic `<n>` would leave the reader
            # reconstructing which numbers moved at the one moment they need them.
            msgs.append("merged %s into %d — each original is kept in history, marked, "
                        "and `update --task %s %s` undoes it (the flag repeats; it does "
                        "not take a list)"
                        % (", ".join(str(n) for n in done), into[0],
                           _heal._task_ref(task), _heal._restore_flags(done)))
    if not msgs:
        return "heal: nothing to do (pass --split or --merge with --into)."
    task["decisions"] = entries
    # A split or a merge IS reconciliation, so it stamps — same rule as any other
    # operation-performing --apply. This path used to return without stamping, which is
    # why seventeen merges on a real task still left it reading `last heal never`: the
    # stamp lived only on the generic --apply path, and the verb path returns before it.
    _heal.stamp_healed(task, kind=_heal.HEAL_KIND_APPLY)
    task["updated_ts"] = _now()
    save_task(task)
    maybe_refresh_board()
    return "\n".join(msgs)


def _split_str_list(raw):
    """`"ab12cd34, 9e01f2aa"` / `"all"` / `["ab12cd34"]` → a list of trimmed strings.
    The string counterpart of `_split_int_list`, for `--dispose-acks` (memo id8s, or the
    single word `all`)."""
    if raw is None:
        return []
    items = raw if isinstance(raw, (list, tuple)) else str(raw).replace(" ", ",").split(",")
    return [str(v).strip() for v in items if str(v).strip()]


def _split_int_list(raw):
    """`"3,7, 9"` / `3` / `[3,7]` → `[3, 7, 9]`, dropping anything uncoercible."""
    if raw is None:
        return []
    items = raw if isinstance(raw, (list, tuple)) else str(raw).replace(" ", ",").split(",")
    out = []
    for v in items:
        try:
            out.append(int(str(v).strip()))
        except (TypeError, ValueError):
            continue
    return out


def _heal_mark(a, tasks):
    """`heal --mark-healed [--note '<why>']` — record a JUDGEMENT-ONLY reconcile.

    The gap this closes: the mechanical plan is often empty ("nothing mechanical to do —
    any remaining work needs judgment"), so a reconciler who read every decision and
    concluded nothing needed changing had no way to say so. The record then still said
    `last heal never`, every session opened on a false "under-reconciled" alarm, and the
    one signal built to be trusted became the one people learned to ignore.

    This is a WRITE, so it obeys the same rule as `--apply`: back up first, refuse
    without a backup. It does NOT run the mechanical plan — that is exactly the point."""
    out = []
    note = (getattr(a, "note", None) or "").strip()
    for task in tasks:
        path = _heal.backup(task, strip=store.strip_rev)
        if not path:
            out.append("[HEAL] Task #%s — REFUSED: could not write the pre-heal backup "
                       "under %s, so nothing was recorded."
                       % (task.get("seq"), _heal.gate_dir()))
            continue
        _heal.stamp_healed(task, kind=_heal.HEAL_KIND_MARK, note=note)
        task["updated_ts"] = _now()
        save_task(task)
        _heal.clear_gate(task["id"])
        fresh = _heal_scan_one(task, probe_branches=True)
        h = fresh.get("health") or {}
        is_due, reasons = _heal.due(task, result=fresh)
        out.append("[HEAL] Task #%s — MARKED HEALED at %d decision(s)%s. No operation "
                   "was performed: this records that the log was read and reconciled by "
                   "judgement, which is what `--apply` could not say when its plan was "
                   "empty.\n  %s\n  Heal due? %s"
                   % (task.get("seq"), h.get("decisions_total", 0),
                      (" — why: %s" % note) if note else
                      " (no --note given; a why is worth one line)",
                      _heal.health_line(h),
                      ("YES — %s" % "; ".join(reasons)) if is_due else "no"))
    return "\n\n".join(out)


def _heal_dismissals_report(task):
    """`heal --dismissals` — the read-only ledger view.

    It runs a scan purely so it can say which rulings are still SILENCING something and which
    have EXPIRED because the text they covered changed. That distinction is the whole reason
    the list is worth printing.

    THE GIT PROBERS ARE WIRED, and that is not incidental: a dismissal of a `drift:branch x`
    or a `cited-commit` finding only matches when the check that produced it actually RAN, so
    a cheap probe-less scan here would report those rulings as expired when nothing had
    changed at all. It does NOT write the gate file — this is a read of the ledger, not the
    scan of record."""
    result = _heal.scan(task, branch_probe=_heal.branch_prober(task),
                        commit_probe=_heal.commit_prober(task))
    return "\n".join(_heal.dismissal_lines(task, result=result))


def _heal_dismiss_writes(a, task):
    """`--dismiss` / `--undismiss` for one task — the adjudication ledger's write path.

    ITS OWN INVOCATION, NOT PART OF THE MECHANICAL PLAN, and that is deliberate. A dismissal
    changes what the scan REPORTS; a split or a merge changes what the record SAYS. Folding
    them into one run would produce a report telling two unrelated stories, and — worse — a
    dismissal would then pass through the "performed at least one operation" test and STAMP a
    heal. Adjudicating a false positive is not reconciling a task, and a stamp that claims
    otherwise is the exact lie the zero-operation refusal exists to prevent. So this path
    writes the ledger, stamps NOTHING, and says so.

    The scan it matches against is the UNFILTERED one (`findings` + `dismissed`), so a
    `--dismiss` naming something already dismissed gets told that rather than "no such
    finding" — the two are different answers and only one of them is true."""
    seq = task.get("seq", task["id"][:8])
    dismiss = [s for s in (getattr(a, "dismiss", None) or []) if str(s).strip()]
    undismiss = [s for s in (getattr(a, "undismiss", None) or []) if str(s).strip()]
    why = getattr(a, "why", None)
    sid = getattr(a, "session", None)
    # The git probers are wired: a dismissal names a finding, and the two probed checks
    # (branches, cited commits) produce findings too — matching against a scan that did not
    # run them would refuse a legitimate `--dismiss drift:branch x` as "no such finding".
    result = _heal_scan_one(task, probe_branches=True)
    everything = (result.get("findings") or []) + (result.get("dismissed") or [])
    lines, changed = [], False
    for sel in undismiss:
        entry, err = _heal.undismiss(task, sel, sid=sid)
        if err:
            lines.append(err)
            continue
        changed = True
        lines.append("UNDISMISSED %s:%s — the ruling is marked retired (nothing was "
                     "deleted; the ledger keeps it as the record that somebody once ruled "
                     "this way) and that finding is reported in full again."
                     % (entry.get("check"), entry.get("ref")))
    for sel in dismiss:
        entry, err = _heal.dismiss(task, everything, sel, why, sid=sid)
        if err:
            lines.append(err)
            continue
        changed = True
        lines.append("DISMISSED %s:%s — out of the findings, the issue count and the due "
                     "calculus. why: %s"
                     % (entry.get("check"), entry.get("ref"), entry.get("why")))
        lines.append("    It covers THIS EXACT TEXT (fingerprint %s…): edit the entry it "
                     "names and the finding re-reports, because the ruling was about the "
                     "sentence that was there. Undo: `heal --apply --task %s --undismiss "
                     "'%s:%s'`"
                     % (str(entry.get("fingerprint"))[:12], seq,
                        entry.get("check"), entry.get("ref")))
    if changed:
        task["updated_ts"] = _now()
        save_task(task)
        # The reported state changed, so the nag must re-arm — the same reason `--apply`
        # clears it. A dismissal that left a stale gate would keep nagging about a finding
        # that is no longer counted.
        _heal.clear_gate(task["id"])
        fresh = _heal_scan_one(task, probe_branches=True)
        is_due, reasons = _heal.due(task, result=fresh)
        lines.append("")
        lines.append("NO HEAL WAS STAMPED. Adjudicating a false positive is not reconciling "
                     "the task, and a stamp that said otherwise would make every other "
                     "stamp unreadable.")
        lines.append("  %-28s %s" % ("Mechanical", _heal.mechanical_line(fresh)))
        lines.append("  %-28s %s" % ("Heal due?",
                                     ("YES — %s" % "; ".join(reasons)) if is_due else "no"))
        lines.append("  %d dismissal(s) in force · `heal --dismissals --task %s` lists them "
                     "with their whys." % (len(_heal.active_dismissals(task)), seq))
    return "[HEAL] Task #%s [%s] — %s\n%s" % (seq, task["id"][:8], task["title"],
                                              "\n".join("  " + ln if ln else ""
                                                        for ln in lines))


def _heal_goal_reviewed(a, tasks):
    """`heal --goal-reviewed` — record that the GOAL LINE was re-read and is still true.

    THE VERB EXISTS BECAUSE THE ALTERNATIVE WAS A GUESS. The goal-review count needed a way
    to reset that did not require rewriting a correct sentence, and the tempting shortcut —
    treat any `--mark-healed --note` as proof the goal was re-read — would have written a
    claim nobody made. So re-reading gets its own verb and its own field, and it is the ONLY
    thing that resets the count.

    REFUSED on a task with no goal: there is nothing to have reviewed, and stamping one
    would put a baseline on a field that does not exist."""
    out = []
    for task in tasks:
        seq = task.get("seq", task["id"][:8])
        if not str(task.get("goal") or "").strip():
            out.append("[HEAL] Task #%s — REFUSED: this task has no goal line, so there is "
                       "nothing to record a review of. Set one with `update --task %s "
                       "--goal '<what done looks like>'`." % (seq, seq))
            continue
        before = _heal.goal_review(task)
        _heal.stamp_goal_reviewed(task)
        task["updated_ts"] = _now()
        save_task(task)
        _heal.clear_gate(task["id"])       # the due state changed; re-arm the nag
        out.append("[HEAL] Task #%s — GOAL REVIEW RECORDED at %d decision(s). The count "
                   "resets%s; the goal itself is untouched, which is the point: re-reading a "
                   "goal that is still right IS the service, and rewriting it to prove you "
                   "read it would put a false edit in the record.\n"
                   "  the goal: %s\n"
                   "  If it HAS drifted, this is the wrong verb — `update --task %s --goal "
                   "'<what done looks like now>'` re-baselines it by rewriting it."
                   % (seq, len(task.get("decisions") or []),
                      (" from %d" % before.get("since_review"))
                      if before.get("since_review") else "",
                      str(task.get("goal") or "").strip(), seq))
    return "\n\n".join(out)


def _heal_candidates_report(task):
    """`heal --candidates` — the cheap merge-only dry run (`heal.candidate_lines`).

    Read-only, and it wires NO probers: the candidate view reads decisions against each
    other, so a git or HTTP round trip would buy it nothing. It does not write the gate file
    either — this is not a scan of record, it is a reading aid."""
    return "\n".join(_heal.candidate_lines(task, result=_heal.scan(task)))


def _heal_dispose(a, task, result):
    """The explicit `--dispose-acks <id8,…|all>` ops for one task, as `(ops, error)`.

    Exactly ONE disposition is required, and they are the same three the live `memo ack`
    takes — `--decision [TEXT]` / `--memory <slug>` / `--noop "<reason>"` — because the
    vocabulary a retro-fill records has to be the vocabulary everything else reads.

    ONE deliberate difference from `memo ack --decision`: this does NOT append a
    decision to the task. A heal records what an ack DID; minting a decision now and
    dating it to a session that no longer exists would be inventing history, which is
    the one thing this pass must never do."""
    disp, err = memo_ack_disposition(decision=getattr(a, "decision", None),
                                     memory=getattr(a, "memory", None),
                                     noop=getattr(a, "noop", None))
    if err:
        return [], ("heal --dispose-acks: %s"
                    % err.split("memo ack: ", 1)[-1])
    pairs, err = _heal.select_acks(task, _split_str_list(getattr(a, "dispose_acks", None)))
    if err:
        return [], err
    if not pairs:
        return [], ("heal --dispose-acks: task #%s has no undispositioned ack to "
                    "retro-fill — the scan reports %d."
                    % (task.get("seq"),
                       _heal.counts(result).get("ack-undispositioned", 0)))
    return _heal.disposition_ops(task, kind=disp["kind"], value=disp["value"],
                                 sid=getattr(a, "session", None), only=pairs), None


# --------------------------------------------------------------- claims ------
#
# A plan document asserts things — the scrub landed, the release shipped, the suite is
# green — and a reader has no way to tell an assertion that is STILL true from one that
# was true when it was written. `claims` is that assertion plus the command that settles
# it, stored on the task, so the document can be CHECKED rather than believed. The store
# side and the verification live in lib/checker.py; this is the surface.
#
# `verify` is the only action that RUNS anything, and it is on demand only. Session start
# never runs a claim (see the note in lib/checker.py): these are arbitrary user shell
# commands, and putting an unbounded user-defined cost in front of every session — with
# whatever side effects those commands have — is not a trade this tool gets to make on
# the user's behalf.

# `verify` HAS THREE OUTCOMES, NOT TWO, and they get three exit codes. Green is 0 and a
# refuted claim is 1, as they always were. NOTHING RAN is 3 — a task with no claims
# registered used to print "has no claims registered" and exit 0, which is a PASS handed
# out for the absence of the very thing being checked, and three children in a row were
# graded down for a gap that the gate had just told them was fine. 3 rather than 1
# because it is not a red: nothing was refuted, there was nothing to refute, and a caller
# gating on this wants to tell "your claim broke" from "you registered none". The way
# OUT of 3 is `--none '<reason>'`, not an invented claim.
_CLAIMS_ACTIONS = ("show", "verify")

VERIFY_PASSED = "passed"      # every claim that ran, passed
VERIFY_FAILED = "failed"      # at least one claim was refuted, timed out, or errored
VERIFY_NOTHING = "nothing"    # nothing ran and no reason was recorded for that


def _claims_target(a):
    """The ONE task a `claims` invocation acts on, as `(task, error_line)`. `--task <ref>`
    by seq/id, else the attached task — the resolution every neighbouring command uses.

    There is deliberately no `--all`: a claim is an assertion about ONE document, and a
    board-wide sweep that ran every registered command on every task would be an
    unbounded amount of shell nobody asked for in one keystroke."""
    ref = getattr(a, "task", None)
    if ref:
        task = resolve_ref(ref) or load_task(ref)
        if not task:
            return None, "No task matching '%s'.\n\n%s" % (ref, _format_list())
        return task, None
    task = _session_task(getattr(a, "session", None))
    if not task:
        return None, ("No task attached — name one with `claims --task <n>`.")
    return task, None


def _claims_writes(a, task):
    """Apply the mutating flags in a fixed order, as `(lines, changed)`.

    ORDER IS BIND → UNBIND → REGISTER → REMOVE → NONE, and it is fixed so one invocation reads
    the same way every time. Each flag reports its own outcome line, including its own
    refusal: a mistyped `--register` must not silently take the rest of the invocation
    down with it, and a `--remove` naming an id that is not there says so rather than
    reporting "removed" and leaving the caller believing a claim is gone."""
    lines, changed = [], False
    if getattr(a, "bind", None):
        ok, err = _checker.bind_doc(task, a.bind)
        lines.append(err if err else "bound → %s" % _checker.claims_doc(task))
        changed = changed or ok
    if getattr(a, "unbind", False):
        ok, err = _checker.unbind(task)
        lines.append(err if err else "unbound (the registered claims were kept)")
        changed = changed or ok
    if getattr(a, "register", None):
        added, updated, errors = _checker.register(
            task, a.register, replace=getattr(a, "replace", False))
        lines.extend(errors)
        if added or updated:
            lines.append("registered %d new claim(s), rewrote %d%s"
                         % (added, updated,
                            " (--replace: the previous list was dropped)"
                            if getattr(a, "replace", False) else ""))
            changed = True
    if getattr(a, "remove", None):
        removed, missing = _checker.remove(task, a.remove)
        if removed:
            lines.append("removed %s" % ", ".join(removed))
            changed = True
        if missing:
            lines.append("no such claim: %s — nothing was removed for %s"
                         % (", ".join(missing),
                            "it" if len(missing) == 1 else "those"))
    if getattr(a, "none", None):
        # LAST, so `--remove C1 --none '…'` works in one invocation: the removal has
        # already happened by the time the refusal checks whether any claim is left.
        ok, err = _checker.declare_none(task, a.none)
        lines.append(err if err else
                     "recorded: this task deliberately registers no claims — "
                     "`verify` now passes and says why, instead of reporting nothing")
        changed = changed or ok
    return lines, changed


def _claims_show(task):
    """The read-only view: the bound document, the registered claims, and how the last
    verification went. Says out loud when a bound document is MISSING — the pointer check
    reports it too, but somebody typing `claims` is asking about exactly this."""
    out = []
    doc = _checker.claims_doc(task)
    if doc:
        state = "" if os.path.exists(doc) else "   ← MISSING"
        bound = _checker.claims(task).get("bound_ts")
        out.append("  doc: %s (bound %s)%s"
                   % (doc, rel_time(bound) if bound else "at an unknown time", state))
    else:
        out.append("  doc: none — `claims --task %s --bind <abs path>` binds one"
                   % (task.get("seq") or task["id"][:8]))
    items = _checker.claim_items(task)
    declared = _checker.claims_none(task)
    last = {r.get("id"): r for r in (_checker.last_verify(task).get("results") or [])
            if isinstance(r, dict)}
    if not items and declared:
        out.append("  claims: NONE, deliberately (%s) — %s"
                   % (rel_time(declared.get("ts")) if declared.get("ts")
                      else "at an unknown time", declared["reason"]))
    elif not items:
        out.append("  claims: none registered — "
                   "`--register 'C1|<command>|<expected substring>'`, or "
                   "`--none '<why there is nothing to re-run>'` if that is the answer")
    else:
        out.append("  %d claim(s):" % len(items))
        for item in items:
            prev = last.get(item["id"])
            mark = "     " if prev is None else ("  ok " if prev.get("ok") else " FAIL")
            out.append("   %s %-8s %s" % (mark, item["id"], item["cmd"]))
            out.append("            expects: %s" % " · ".join(item["expect"]))
    verified = _checker.last_verify(task)
    if verified.get("ts"):
        results = verified.get("results") or []
        passed = len([r for r in results if isinstance(r, dict) and r.get("ok")])
        out.append("  last verify: %s — %d/%d passed"
                   % (rel_time(verified.get("ts")), passed, len(results)))
    elif not declared:
        out.append("  last verify: never — `claims verify --task %s`"
                   % (task.get("seq") or task["id"][:8]))
    return out


def _claims_verify(a, task):
    """Run the claims and report. Returns one of VERIFY_PASSED / VERIFY_FAILED /
    VERIFY_NOTHING.

    PERSISTS `last_verify` EVEN WHEN CLAIMS FAILED, which is the point: a stored red
    result is what makes the failure visible to the next reader of the task, and
    discarding it would leave the record saying only that a verification once passed.

    NOTHING-RAN IS ITS OWN VERDICT, not a pass. This used to return the same True a
    green run returns, so a gate that shelled out here read "no claims registered" as
    success and moved on — the absence of the check reported as the check passing. It is
    a pass only when somebody has written down WHY there is nothing to run (`--none`),
    and then the reason is what gets printed, because a reader is owed the reason and
    not merely the silence.

    The exit code is the caller's job (`cmd_claims`), so this can be reused by a surface
    that wants the verdict without ending the process."""
    only = getattr(a, "id", None)
    results = _checker.verify(task, only=only,
                             timeout=getattr(a, "timeout", None) or None)
    ref = task.get("seq") or task["id"][:8]
    if not results:
        declared = _checker.claims_none(task)
        if only:
            # A typo'd id is the same absence wearing a worse disguise: `verify --id C9`
            # against a task whose claim is C1 ran nothing at all, and passing it would
            # let one wrong character green a gate.
            print("claims verify: task #%s has no claim %s registered — nothing ran, so "
                  "nothing was proved. Registered: %s"
                  % (ref, only,
                     ", ".join(i["id"] for i in _checker.claim_items(task)) or "none"))
            return VERIFY_NOTHING
        if declared:
            print("claims verify: task #%s deliberately registers no claims — %s"
                  % (ref, declared["reason"]))
            return VERIFY_PASSED
        print("claims verify: task #%s has NO CLAIMS REGISTERED, so nothing ran and "
              "nothing was proved — this is a finding, not a pass.\n"
              "  Register the commands you already ran to verify the work, each with "
              "the output substring you already asserted on:\n"
              "    claims --task %s --register 'C1|<command>|<expected substring>'\n"
              "  If there is genuinely nothing a later session could re-run, say so and "
              "why — that is a pass:\n"
              "    claims --task %s --none '<why>'"
              % (ref, ref, ref))
        return VERIFY_NOTHING
    task["updated_ts"] = _now()
    save_task(task)
    ok = [r for r in results if r["ok"]]
    print("Claims — task #%s %s: %d/%d passed."
          % (task.get("seq") or task["id"][:8], task.get("title"), len(ok), len(results)))
    for r in results:
        print("  %s %-8s %s" % ("ok  " if r["ok"] else "FAIL", r["id"], r["cmd"]))
        if r["status"] == "timeout":
            print("         timed out — nothing was proved either way, so this is NOT "
                  "a refutation")
        elif r["status"] == "error":
            print("         could not be run: %s" % r["got"])
        elif r["missing"]:
            print("         missing from the output: %s" % " · ".join(r["missing"]))
        if r["got"] and r["status"] == "ran" and not r["ok"]:
            print("         got (tail): %s" % " ".join(r["got"].split())[-200:])
    return (VERIFY_PASSED if not [r for r in results if not r["ok"]]
            else VERIFY_FAILED)


def cmd_claims(a):
    """`task-station claims [verify] [--task REF] [--bind …] [--register …] …`

    THE PLAN CHECKS ITSELF FROM HERE ON. Bind a document to a task, register the shell
    commands that settle what it asserts, and `verify` runs them.

    `verify` EXITS 0 GREEN, 1 ON A REFUTED CLAIM, 3 WHEN NOTHING RAN — so it can gate a
    release step rather than only inform a reader, and so a gate can tell a broken claim
    from a task that registered none. 3 is cleared by registering a claim, or by
    `--none '<reason>'` when there is genuinely nothing a later session could re-run.

    Bare `claims` is a READ — the bound document, the registered claims, the last
    verification — and reads nothing else and runs nothing. That default matters: `verify`
    is the only action that executes a command, and it has to be typed."""
    action = (getattr(a, "action", None) or "show").strip().lower()
    if action not in _CLAIMS_ACTIONS:
        print("claims: unknown action %r. Bare `claims` shows what is registered and "
              "`claims verify` runs it; the task is named with `--task <n>`, not "
              "positionally." % action)
        return
    task, err = _claims_target(a)
    if err:
        print(err)
        return
    mutating = [f for f in ("bind", "unbind", "register", "remove", "replace", "none")
                if getattr(a, f, None)]
    if action == "verify" and mutating:
        # Refuse rather than pick an order. Somebody typing both is asking "register this
        # and tell me whether it passes", and answering half of that silently — with a
        # verify that ran against the OLD list — is worse than saying run it twice.
        print("claims verify runs what is already registered, so it cannot be combined "
              "with %s. Register first, then verify."
              % ", ".join("--" + f for f in mutating))
        return
    if getattr(a, "replace", False) and not getattr(a, "register", None):
        print("claims --replace says what the new list IS, so it needs at least one "
              "--register. To empty the list, `--remove <id>` each claim.")
        return
    if action == "verify":
        verdict = _claims_verify(a, task)
        if verdict == VERIFY_FAILED:
            sys.exit(1)      # a failing claim must be able to gate, not just inform
        if verdict == VERIFY_NOTHING:
            sys.exit(3)      # nothing ran: not a red, but never a pass either
        return
    lines, changed = _claims_writes(a, task)
    if changed:
        task["updated_ts"] = _now()
        save_task(task)
    head = "Claims — task #%s %s" % (task.get("seq") or task["id"][:8], task.get("title"))
    print("\n".join([head] + ["  " + ln for ln in lines] + _claims_show(task)))


def _sync_targets(root):
    """The destinations one `sync` invocation acts on.

    `--dir` means THIS ONE ONLY, and its kind comes from the directory's own
    declaration rather than from the flag — so aiming `--dir` at a share exchange runs
    the filtered path, which is the whole point of the exchange declaring itself."""
    if root:
        return [{"root": root, "kind": _sync.exchange_kind(root)}]
    return _sync.destinations()


def cmd_sync(a):
    """`task-station sync [--init [DIR]] [--dir DIR] [--dry-run] [--no-net] [--status]`

    The two-machine transport. Each station writes ONE directory in the exchange — its
    own `owners/<owner>/station-<n>/` — and reads every other, so two machines can
    never write the same path and a merge conflict is not possible rather than merely
    unlikely. Inside a task the merge is PER FIELD: lists union, element flags merge,
    and a scalar takes the newest by that field's own timestamp while preserving what
    it replaced.

    SYNC DOES MECHANICS; HEAL DOES MEANING. Two machines can each add a decision that
    contradicts the other and the union keeps both — so the report is a THREE-ROW
    VERDICT and the third row says which tasks now need reconciling. "0 conflicts" is
    not a statement about whether the record makes sense.

    NETWORK IS OPT-IN. With no git remote on the exchange this commits locally and
    sends nothing; `--init` creates a LOCAL repo and never a remote."""
    root = getattr(a, "dir", None)
    root = os.path.expanduser(root) if root else None
    for flag, kind, key in (("init", _sync.KIND_BACKUP, "sync_dir"),
                            ("init_share", _sync.KIND_SHARE, "share_dir")):
        if getattr(a, flag, None) is None:
            continue
        target = getattr(a, flag) or root or (
            _sync.sync_root() if kind == _sync.KIND_BACKUP else _sync.share_root())
        if not target:
            print("sync --%s needs a directory (or set `%s` in config first)."
                  % (flag.replace("_", "-"), key))
            return
        target = os.path.expanduser(target)
        try:
            info = _sync.init_root(target, kind=kind)
        except _sync.DestinationMismatch as e:
            print("sync: %s" % e)
            return
        if info["kind"] != kind:
            print("sync: %s already exists as a %s exchange and was left alone. An "
                  "exchange keeps the identity it has — converting one is a decision "
                  "with a blast radius, not a flag." % (target, info["kind"]))
            return
        if getattr(a, flag):
            _config.set(key, target)
        label = "SHARE" if kind == _sync.KIND_SHARE else "backup"
        print("sync: %s exchange ready at %s" % (label, info["root"]))
        print("  this station: %s / %s   (%s)"
              % (_station.owner(), _station.dirname(), _station.display()))
        print("  partition:    %s" % info["partition"])
        print("  git:          %s" % ("repo present" if info["git"] else "none (plain dir)"))
        if kind == _sync.KIND_SHARE:
            print("  SHARES NOTHING YET — a task reaches a share exchange only when a "
                  "sharing rule on its brain names an audience. `task-station brains "
                  "share <brain> --with <alias>` is the only thing that widens it.")
        else:
            print("  EVERY task is backed up here, unfiltered — that is the durability "
                  "guarantee, and filtering it would kill it.")
        print("  NO REMOTE is configured and none is created here — adding one is a "
              "deliberate step (docs/SYNC.md).")
        return
    if getattr(a, "status", False):
        try:
            dests = _sync_targets(root)
        except _sync.DestinationMismatch as e:
            print("sync: %s" % e)
            return
        if not dests:
            print("sync is OFF — no exchange configured. `task-station sync --init <dir>`.")
            return
        print("this station: %s / %s   (%s)"
              % (_station.owner(), _station.dirname(), _station.display()))
        for d in dests:
            r = d["root"]
            print("")
            print("%s: %s" % (d["kind"], r))
            print("  git: %s · remote: %s"
                  % (_sync.is_git_repo(r), _sync.has_remote(r)))
            for p in _sync.list_partitions(r):
                print("  %s %s/%s  %s" % ("*" if p["own"] else " ", p["owner"],
                                          _station.dirname(p["number"]), p["label"]))
        return
    try:
        dests = _sync_targets(root)
    except _sync.DestinationMismatch as e:
        print("sync: %s" % e)
        return
    if not dests:
        print("sync is OFF — no exchange configured. `task-station sync --init <dir>`.")
        return
    self_mod = sys.modules.get(g("__name__"))
    if self_mod is None:
        import types as _types
        self_mod = _types.SimpleNamespace(**_shared._G)
    if getattr(a, "check", False) or getattr(a, "if_changed", False):
        try:
            dests = _sync_targets(root)
        except _sync.DestinationMismatch as e:
            print("sync: %s" % e)
            return
        if not dests:
            print("sync is OFF — no exchange configured. "
                  "`task-station sync --init <dir>`.")
            sys.exit(3)
        moved = []
        for d in dests:
            for c in _sync.check_changes(d["root"]):
                c["root"] = d["root"]
                c["kind"] = d["kind"]
                moved.append(c)
        if not moved:
            print("sync --check: nothing moved — every peer partition is at the rev "
                  "this machine already pulled.")
            if getattr(a, "check", False):
                sys.exit(3)      # a branchable "no work", not a failure
            return
        for c in moved:
            print("changed: %s/%s  %s  rev %s (last pulled %s)"
                  % (c["owner"], _station.dirname(c["number"]), c["label"], c["rev"],
                     c["seen"] or "never"))
        print("%d partition(s) moved." % len(moved))
        if getattr(a, "check", False):
            return
        reps = [_sync.run_sync(self_mod, root=d["root"], kind=d["kind"],
                               dry_run=getattr(a, "dry_run", False),
                               no_net=getattr(a, "no_net", False),
                               confirm_share=getattr(a, "confirm_share", False))
                for d in dests]
        print("\n\n".join(_sync.format_report(r) for r in reps))
        return
    if getattr(a, "preview", False):
        shares = [d for d in dests if d["kind"] == _sync.KIND_SHARE]
        if not shares:
            print("sync --preview shows what would become VISIBLE, and no share "
                  "exchange is configured — so nothing would. "
                  "`task-station sync --init-share <dir>` creates one.")
            return
        for d in shares:
            plan = _sync.share_plan(self_mod, d["root"])
            print(_sync.format_share_plan(plan, d["root"]))
        return
    reps = [_sync.run_sync(self_mod, root=d["root"], kind=d["kind"],
                           dry_run=getattr(a, "dry_run", False),
                           no_net=getattr(a, "no_net", False),
                           confirm_share=getattr(a, "confirm_share", False))
            for d in dests]
    print("\n\n".join(_sync.format_report(r) for r in reps))


def cmd_heal(a):
    """`task-station heal [--task REF] [--scan] [--apply] [--all]` — the reconcile pass.

    task-station has always had capture (`save`) and no reconcile: the decision log only
    grows and nothing ever says "these sixteen entries are now four". The digest no
    longer truncates by age, so this pass is the only thing that keeps it honest — a
    refuted decision briefs every session until a verb marks it. This closes that.

    TWO LAYERS. `--scan` is layer 1: deterministic, zero tokens, and it NEVER mutates
    the task (so it never stamps a heal either — read-only is its whole contract). Bare
    `heal` is layer 2's brief — the findings plus the full decision set plus the exact
    verb commands — and it is a DRY RUN, the default, which changes NOTHING. `--apply`
    performs the mechanical subset of the plan, backs the task blob up first, and
    refuses outright if that backup cannot be written.

    TWO THINGS `--apply` DELIBERATELY DOES NOT DO, both of them measured mistakes:

      * it does not REPRINT the dry run. It reports what it performed, what it skipped,
        where the backup went and how the task reads now — nothing else, because the
        caller just read the rest and ~94% of that block is the decision list. `--apply
        --verbose` restores the full render.
      * it does not STAMP a pass that performed zero operations. That case is REFUSED
        (`_heal_no_operations_block`), naming the two honest moves instead: pass
        operations, or record a judgement-only pass with `--mark-healed --note`.

    THE FLOW IS THE SKILL'S JOB, NOT THE CLI'S. A CLI is one-shot and cannot hold a
    conversation, so dry-run-as-default stays for scripting safety while
    `skills/heal/SKILL.md` orchestrates: scan first, read the dry run at most once,
    judge, execute, stamp, re-scan, report. It no longer stops to ask before applying —
    the approval gate is gone, and what replaces it is the UNDO TRAIL this CLI prints
    (`heal.undo_lines`, and the `update` result line for the two judgement verbs, which
    write immediately and take no backup). A gate is only safe to remove if reversing a
    wrong call costs what approving one did, so every write now names the one command
    that takes it back, with the index it actually touched.

    `--mark-healed` is the judgement-only counterpart, and `--dispose-acks` retro-fills
    the dispositions of acks recorded before dispositions were required.

    FOUR MORE MODES, each its own invocation and each refusing the combinations it cannot
    honestly serve (`_others` builds every one of those refusals from one list, so a flag
    added later cannot be silently swallowed by an older mode):

      * `--dismissals` / `--candidates` — READS. The adjudication ledger, and the cheap
        merge-only view. Neither writes, neither stamps, and neither combines with anything.
      * `--apply --dismiss '<check>:<ref>' --why '…'` / `--undismiss` — the ledger's WRITE
        path. It changes what the scan REPORTS rather than what the record SAYS, so it never
        rides along with the mechanical plan and never stamps a heal.
      * `--goal-reviewed` — records that the GOAL LINE was re-read and is still true. The
        only thing that resets the goal-review count; combinable with `--mark-healed`,
        which is the honest pair for a judgement-only pass that included the goal.
      * `--probe-links` — the one opt-in network call, off by default.

    THE TASK MAY BE NAMED POSITIONALLY — `heal --scan 12` is `heal --scan --task 12`,
    because `/heal 12` passes that 12 straight through as a positional. The fold and its
    two refusals live in `_heal_positional_ref`, and they run FIRST: a refusal there must
    change nothing, so it has to be answered before any task is loaded."""
    ref_error = _heal_positional_ref(a)
    if ref_error:
        print(ref_error)
        return
    tasks, err = _heal_targets(a)
    if err:
        print(err)
        return
    if not tasks:
        print("No open tasks to heal.")
        return
    scan_only = getattr(a, "scan", False)
    apply_it = getattr(a, "apply", False)
    sweeping = getattr(a, "all", False)
    dispose = _split_str_list(getattr(a, "dispose_acks", None))
    dismiss = [s for s in (getattr(a, "dismiss", None) or []) if str(s).strip()]
    undismiss = [s for s in (getattr(a, "undismiss", None) or []) if str(s).strip()]
    listing = getattr(a, "dismissals", False)
    candidates = getattr(a, "candidates", False)
    goal_reviewed = getattr(a, "goal_reviewed", False)
    marking = getattr(a, "mark_healed", False)
    verbs = (getattr(a, "split", None) is not None
             or getattr(a, "merge", None) is not None)

    def _others(*allowed):
        """The mode flags present that this invocation is NOT one of. One reader, so every
        refusal below names the same set and none of them can forget a flag that was added
        later — the way a mode silently starts swallowing another one."""
        modes = (("--scan", scan_only), ("--apply", apply_it),
                 ("--mark-healed", marking), ("--goal-reviewed", goal_reviewed),
                 ("--dismissals", listing), ("--candidates", candidates),
                 ("--dismiss", bool(dismiss)), ("--undismiss", bool(undismiss)),
                 ("--dispose-acks", bool(dispose)),
                 ("--split/--merge", verbs))
        return [name for name, on in modes if on and name not in allowed]

    if scan_only and apply_it:
        # `/heal` opens with `--scan` on the caller's behalf (the SKILL drives the rest),
        # so an `--apply` typed alongside it would be silently swallowed by the read-only
        # path and the caller would believe a heal had run. Refuse rather than pick one.
        print("heal --scan is read-only and applies nothing, so the two cannot be "
              "combined. Scan first, then run the operations you decided on — and note "
              "that the `heal` SKILL drives that whole sequence, so you should not need "
              "to type either flag yourself.")
        return
    # THE TWO READ-ONLY VIEWS, first because they write nothing and can therefore refuse
    # early and cheaply. Neither is combinable with anything else: somebody who typed a view
    # AND a write is owed a refusal rather than half of each, and a view that quietly ran
    # alongside an --apply would print a state the apply had already changed.
    if listing or candidates:
        if listing and candidates:
            print("heal --dismissals lists the adjudication ledger and --candidates prints "
                  "the merge groups — two different reads, so run them as two commands.")
            return
        flag = "--dismissals" if listing else "--candidates"
        clash = _others(flag)
        if clash:
            print("heal %s is a READ: it changes nothing, performs nothing and stamps "
                  "nothing, so it cannot be combined with %s. Run it on its own."
                  % (flag, ", ".join(clash)))
            return
        view = _heal_dismissals_report if listing else _heal_candidates_report
        print("\n\n".join(view(t) for t in tasks))
        return
    # THE ADJUDICATION LEDGER'S WRITE PATH. Its own invocation on purpose — see
    # `_heal_dismiss_writes` for why it must not ride along with the mechanical plan.
    if dismiss or undismiss:
        if scan_only:
            print("heal --scan is read-only, so it cannot dismiss anything. Use `heal "
                  "--apply --dismiss '<check>:<ref>' --why '<reason>'` to record the "
                  "adjudication, or `heal --dismissals` to read the ledger.")
            return
        clash = _others("--dismiss", "--undismiss", "--apply")
        if clash:
            print("heal --dismiss/--undismiss adjudicate what the scan REPORTS; %s "
                  "change(s) what the record SAYS. One report cannot honestly tell both "
                  "stories, and a dismissal must never stamp a heal — run them separately."
                  % ", ".join(clash))
            return
        if sweeping:
            print("heal --dismiss/--undismiss name a finding on ONE task — a ref means "
                  "nothing board-wide, and one shared --why across unrelated work is not an "
                  "adjudication. Target it with `--task <n>`.")
            return
        if not apply_it:
            print("heal --dismiss/--undismiss WRITE the ledger, and a bare `heal` is a dry "
                  "run that changes nothing — so this would have silently done nothing. "
                  "Re-run it as `heal --apply --dismiss '<check>:<ref>' --why '<reason>' "
                  "--task <n>`. Nothing was changed.")
            return
        print(_heal_dismiss_writes(a, tasks[0]))
        maybe_refresh_board()
        return
    # RECORDING A GOAL RE-READ. Allowed alongside --mark-healed and nothing else: those two
    # are the honest pair for "I read everything, including the goal, and it is all still
    # true", and requiring two commands for one pass would be ceremony. Every other
    # combination is refused, because this stamps a claim about one specific line.
    if goal_reviewed:
        clash = _others("--goal-reviewed", "--mark-healed")
        if clash:
            print("heal --goal-reviewed records that the GOAL LINE was re-read and is "
                  "still true. It performs no operation, so it cannot be combined with %s. "
                  "It may be combined with --mark-healed, which is the honest pair for a "
                  "judgement-only pass that included the goal." % ", ".join(clash))
            return
        print(_heal_goal_reviewed(a, tasks))
        if not marking:
            maybe_refresh_board()
            return
        print("")            # …and the judgement-only stamp follows, explicitly asked for
    if marking:
        # A stamp is not a scan and not a plan — refuse the combinations rather than
        # silently picking one, so nobody can think they applied a plan they didn't.
        if (scan_only or apply_it or dispose
                or getattr(a, "split", None) is not None
                or getattr(a, "merge", None) is not None):
            print("heal --mark-healed records a judgement-only heal and performs no "
                  "operation, so it cannot be combined with --scan, --apply, "
                  "--dispose-acks, --split or --merge. Run it on its own, after the "
                  "verbs.")
            return
        if sweeping:
            print("[HEAL] SCOPE: --all covers %d open/active task(s) — %s. Each will be "
                  "BACKED UP and stamped as healed; no decision is touched."
                  % (len(tasks), ", ".join("#%s" % t.get("seq") for t in tasks)))
            print("")
        print(_heal_mark(a, tasks))
        maybe_refresh_board()
        return
    if getattr(a, "split", None) is not None or getattr(a, "merge", None) is not None:
        # `--into` is ONE option, so passing it twice silently keeps only the last value
        # and the other verb links to the wrong target. Refuse rather than mislink: each
        # verb needs its own `--into`, so each needs its own invocation.
        if getattr(a, "split", None) is not None and getattr(a, "merge", None) is not None:
            print("heal: --split and --merge each need their own --into, and --into can only "
                  "be given once — run them as two separate commands.")
            return
        print(_heal_verb(a))
        return
    if dispose and sweeping:
        # An id8 names an ack on ONE task, and `all` means "every undispositioned ack on
        # THIS task" — sweeping the board with one disposition reason would put the same
        # sentence on acks from unrelated work.
        print("heal --dispose-acks names acks on ONE task, so it cannot be combined with "
              "--all. Target it with `--task <n>`.")
        return
    if dispose and scan_only:
        print("heal --scan is read-only, so it cannot dispose of anything. Drop --scan "
              "for the dry run, or add --apply to record the dispositions.")
        return
    # --all is the ONLY path that can touch more than one record. Say so loudly, and
    # say it BEFORE anything happens — including before a mutating --apply.
    if sweeping:
        print("[HEAL] SCOPE: --all covers %d open/active task(s) — %s. %s"
              % (len(tasks), ", ".join("#%s" % t.get("seq") for t in tasks),
                 "Each will be BACKED UP and its mechanical plan APPLIED."
                 if apply_it else "Nothing will be changed (dry run)."))
        print("")
    # NETWORK STAYS OPT-IN. `link_states` has always taken a probe and has never been given
    # one, so every stored link has always read UNKNOWN — the right default for a check that
    # can only ever confirm what is already there, at one HTTP round trip per link. With
    # `--probe-links` the real prober is wired, and only an explicit 404/410 counts as dead:
    # a private PR answering 401 is not evidence, and "your PR link is dead" about a live PR
    # would send a reader hunting for work that is sitting exactly where it was.
    link_probe = _heal.link_prober() if getattr(a, "probe_links", False) else None
    if link_probe is not None:
        print("[HEAL] --probe-links: making ONE unauthenticated HTTP HEAD request per stored "
              "PR/story link. Only a 404/410 counts as dead; every other answer, including "
              "any error, stays UNKNOWN and is never reported.")
        print("")
    # THE RECONCILE AGAINST THE SOURCE, same opt-in bargain and a louder one. It reads
    # every work item the task claims — several authenticated round trips each — so it
    # cannot ride the session-start path. What it must never do is stay INVISIBLE when
    # it is off: `scan_lines` prints `not probed` for its five checks rather than
    # `clean`, because this whole module exists because a record was trusted as if it
    # were the source.
    ado_probe = _heal_ado.ado_prober() if getattr(a, "probe_ado", False) else None
    # WHAT EVERY OTHER TASK ALREADY OWNS. A Feature child another task claims is not an
    # orphan — that is the board working — so the sibling check subtracts it. Computed
    # ONCE for the whole invocation rather than per task.
    ado_owned = _heal_ado_owned(tasks) if ado_probe is not None else None
    if ado_probe is not None:
        print("[HEAL] --probe-ado: reading the REAL AcceptanceCriteria and Description of "
              "every work item this task claims, plus each parent Feature's children. "
              "Criteria the log never acknowledges, criteria it words differently, "
              "descriptions that miss the source and unlisted Feature children are "
              "reported as findings.")
        print("")
    blocks = []
    for task in tasks:
        result = _heal_scan_one(task, probe_branches=True, link_probe=link_probe,
                                ado_probe=ado_probe, ado_owned=ado_owned)
        if scan_only:
            blocks.append(_heal_scan_report(task, result))
            continue
        ops = _heal.plan(task, result=result)
        if dispose:
            # An EXPLICIT selection REPLACES the blanket retro-noop the plan proposes for
            # every undispositioned ack. That is what keeps a subset surgical: the acks
            # nobody named stay undispositioned, so the next scan still flags them.
            explicit, derr = _heal_dispose(a, task, result)
            if derr:
                blocks.append(derr)
                continue
            ops = _heal.with_dispositions(ops, explicit)
        if not apply_it:
            blocks.append(_heal_block(task, result, ops))
            continue
        # --apply with NOTHING to perform must REFUSE, not stamp. Checked BEFORE the
        # backup, because a pass that will change nothing has nothing to back up. See
        # `_heal_no_operations_block` for the false `last heal just now` this closes.
        if not [o for o in ops if not o.get("manual")]:
            blocks.append(_heal_no_operations_block(task, result, ops))
            continue
        # --apply: back up FIRST. No backup, no mutation — a reconcile without a
        # backup is the one shape of this feature that could lose work.
        path = _heal.backup(task, strip=store.strip_rev)
        if not path:
            blocks.append("[HEAL] Task #%s — REFUSED: could not write the pre-heal "
                          "backup under %s, so nothing was changed."
                          % (task.get("seq"), _heal.gate_dir()))
            continue
        session = getattr(a, "session", None)

        def _append(text, _t=task, _s=session):
            # Returns the new decision's 1-based index, or None when nothing was
            # appended (append_decision no-ops on blank text). None makes the
            # subsequent mark fail LOUDLY rather than pointing at the wrong entry.
            if not append_decision(_t, text, _s):
                return None
            return len(_t.get("decisions") or [])

        applied = _heal.apply(task, ops, append=_append)
        if not applied[1]:
            # Every planned operation failed at execution — so nothing was performed,
            # and the same rule applies: no stamp. Not saving is what discards the
            # in-memory appends a half-done op left behind (`load_task` rebuilds from
            # the row, so nothing partial survives).
            blocks.append(_heal_no_operations_block(task, result, ops,
                                                    attempted=applied[0]))
            continue
        _heal.stamp_healed(task)
        task["updated_ts"] = _now()
        save_task(task)
        # Re-scan AFTER the mutation so the report and the gate show the healed state,
        # and clear the nag watermark — the state changed, so the nag should re-arm.
        _heal.clear_gate(task["id"])
        fresh = _heal_scan_one(task, probe_branches=True, link_probe=link_probe,
                               ado_probe=ado_probe, ado_owned=ado_owned)
        _stream_emit("task.checkpoint", task, _stream_digest(task), session)
        if getattr(a, "verbose", False):
            blocks.append(_heal_block(task, fresh, ops, applied=applied, backup=path,
                                      before=result.get("health")))
        else:
            # `result` is the PRE-apply scan and `fresh` the post-apply one, so the
            # report can show health before → after without the reader holding a number
            # in their head from the previous block. `ops` carries the per-write undo
            # commands `_heal.apply` recorded as it performed them.
            blocks.append(_heal_applied_block(task, fresh, applied, path, ops=ops,
                                              before=result.get("health")))
    print("\n\n".join(blocks))
    if apply_it and not scan_only:
        maybe_refresh_board()


def cmd_session_start(a):
    task_id = get_link(a.session)
    if task_id == SKIP_SENTINEL:
        return  # session intentionally untracked: stay silent
    # Recent hook failures get ONE line, in the same rail as the memo nag. Self-
    # capping: hook_health stamps the newest failure it reported, so this stays
    # silent until a newer one lands (or `hook-health --clear` re-arms it).
    hh_nag = hook_health.nag()
    task = load_task(task_id) if task_id else None
    if task:
        attach_line = ("[task-station] This session is attached to task [%s] %s (%s). "
                       "Continue it; /done to close."
                       % (task["id"][:8], task["title"], task["status"]))
        olabel = ordinal_label(task, a.session)       # hub '<seq>-<n>' (#463)
        if olabel:
            attach_line += " You are hub session %s." % olabel
        msg = [attach_line]
        msg.extend(cat_lines(task.get("color")))
        adv = ultracode_advisory(task)
        if adv:
            msg.append(adv)
        # Delta-injection: a resuming/attaching session learns what OTHER sessions,
        # workers, and child tasks did to this task while it was away. Bounded to
        # one block; advance the watermark + persist so it's shown exactly once.
        delta = delta_brief(task, a.session)
        if delta:
            msg.append(delta)
            mark_seen(task, a.session)
            save_task(task)
        # Pending memos awaiting THIS session's ack — ack-gated (re-surfaces until
        # acked), so appended alongside the delta but NOT watermark-cleared.
        pending = memo_pending_brief(task, a.session)
        if pending:
            msg.append(pending)
        # A CHILD's report lands on the CHILD's task, so the block above — which is scoped
        # to the task THIS session is attached to — would never mention it. A parent sitting
        # on the orchestrator therefore had no surface at all that said a child had handed
        # work back, and one real report sat unread for seven hours. Fails open: this is a
        # nag, and a nag that raises is worse than one that is missing.
        try:
            kids = _turn.child_reports_brief(task, _loop.children(task, all_tasks()))
            if kids:
                msg.append(kids)
        except Exception:                                       # noqa: BLE001
            pass
        # Opt-in auto-checkpoint: on a POST-COMPACTION session start, send the model to
        # FETCH the durable digest and nudge a refresh if the plan advanced. SessionStart
        # additionalContext reliably lands before the model's next turn — the sanctioned
        # model-facing post-compaction instruction. Gated: auto-checkpoint on +
        # source==compact + attached, else unchanged.
        #
        # THE LINE NAMES THE COMMAND BECAUSE THE LINE IS ALL THE MODEL GETS — #583. It
        # used to call the digest the model's own standing source of truth while carrying
        # none of it, to a session that had just lost its context: the one moment where
        # being told you already hold the record does the most damage. (The old phrasing
        # is deliberately NOT quoted here — the gate for #583 counts that exact string
        # with no exemption for a comment recording it, so quoting it would hold the
        # gate red on a correct fix.)
        if getattr(a, "source", "") == "compact" and _auto_checkpoint_enabled():
            seq = task.get("seq", task["id"][:8])
            msg.append("[task-station] Context was just compacted. Task %s's durable "
                       "digest is the record to continue from — and this line does not "
                       "carry it, so read it first: `task-station search --detail %s`. "
                       "If the plan advanced since the last checkpoint, run `/todo save` "
                       "(or at least refresh `--state`) so a future resume stays "
                       "current; the compaction summary is stashed in `/todo %s "
                       "history`." % (seq, seq, seq))
        # Under-reconciled digest gets ONE line, in the same rail as the hook-health
        # and memo nags. Self-capping: the gate file fingerprints the state already
        # reported, so this stays silent until it CHANGES (or an --apply re-arms it) —
        # a nag that fires every session is one you learn to ignore. Fail-open: a
        # broken heal scan must never break a session start.
        try:
            heal_nag = _heal.nag(task)
        except Exception:
            heal_nag = None
        if heal_nag:
            msg.append(heal_nag)
        # The CHECKER's two lines, in the same rail and self-capping the same way. Both
        # answer a question no other check here can: `heal` cross-references things the
        # task holds, and a goal condition nobody has worked on contradicts NOTHING —
        # there is no inconsistency to find, only silence. See lib/checker.py.
        #
        # POINTERS FIRST, DRIFT SECOND, deliberately. A vanished worktree changes what
        # you can do in this session; a drifting condition changes what you should be
        # doing this week. The urgent, mechanical fact goes above the judgement call.
        #
        # Each wrapped separately so one failing check cannot silence the other, and both
        # fail open — the same shape as the heal nag above, and for the same reason: a
        # session start that crashed is strictly worse than an unreported drift.
        #
        # `config_paths` IS LEFT EMPTY, and that is a decision rather than a TODO. The
        # seam exists so station-config-declared paths CAN be checked, but every candidate
        # is station-scoped (one Obsidian vault, one repo index) while this nag is
        # per-task and gated per-task — so a single broken station path would be reported
        # once for every task you open, which is the spam the self-cap exists to prevent.
        # A station-scoped surface is where that belongs; wiring it here would be worse
        # than the silence.
        try:
            pointer_nag = _checker.pointer_nag(task, config_paths=None)
        except Exception:
            pointer_nag = None
        if pointer_nag:
            msg.append(pointer_nag)
        try:
            drift_nag = _checker.drift_nag(task)
        except Exception:
            drift_nag = None
        if drift_nag:
            msg.append(drift_nag)
        if hh_nag:
            msg.append(hh_nag)
        print("\n".join(msg))
        return
    opens = [t for t in sorted_tasks() if is_on_board(t)]
    lines = []
    if opens:
        lines.append("[task-station] You have %d open task(s). If the user's request matches one, attach to it "
                     "(full how-to: task-station guidance); otherwise a new task will be tracked "
                     "once the work is clear:" % len(opens))
        for t in opens[:8]:
            lines.append("  - #%s [%s] %s (%s)" % (t.get("seq") or "?", t["id"][:8], t["title"], rel_time(t.get("updated_ts"))))
    # Goal drift on the UNATTACHED path, over the ACTIVE tasks already loaded above — no
    # extra store query, and no `stat` at all (the pointer check is attached-only, since
    # its findings are about the session's own working directory).
    #
    # THIS IS THE HALF THAT CATCHES A PLAN NOBODY OPENED. The attached nag can only reach
    # someone who already opened the task; a task nobody attaches to never gets the
    # message, which is exactly the shape of the fifteen-day case this check exists for.
    # ONE line, naming the single worst offender — the rail above is already an inventory,
    # and a second one under it reads as decoration.
    try:
        listing_nag = _checker.listing_nag([t for t in opens
                                            if t.get("status") == STATUS_ACTIVE])
    except Exception:
        listing_nag = None
    if listing_nag:
        lines.append(listing_nag)
    if hh_nag:
        lines.append(hh_nag)
    if lines:
        print("\n".join(lines))
