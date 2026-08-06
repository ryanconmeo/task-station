---
description: Reconcile a task's decision log into current state. One uninterrupted pass — scan, judge, apply, verify — that reports the exact one-command undo for every write it made.
argument-hint: "[<n> or --task <n> to target one task · --all to sweep the board · nothing at all to use the attached task — you never need to name an operation: /heal drives the whole pass itself]"
allowed-tools: Bash
disable-model-invocation: true
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/lib/task-station.py" heal --scan --session "${CLAUDE_SESSION_ID:-$CLAUDE_CODE_SESSION_ID}" $ARGUMENTS`

The `[HEAL-SCAN]` block above is **layer 1** — the deterministic scan. It is ~700 tokens, it invoked no model, and **it changed nothing**. It is the opening move on purpose: it says whether there is anything to do before anything expensive is spent finding out.

**Four ways to name the task, all of them parsed:** `/heal 12` (bare number, the common one), `/heal --task 12`, `/heal --all` to sweep the board, and `/heal` on its own for the attached task. A bare ref *and* `--all`, or a bare ref *and* a `--task` naming something else, are refused rather than silently resolved — guessing the scope is how the wrong record gets reconciled.

**The scan closes on three lines, not one.** `Mechanical` is what the deterministic checks found. `Judgment` is whether the half no check can do has been **recorded** — a heal stamp carrying a `--note` — and says `NOT RUN` otherwise. Only then `Heal due?`. A clean mechanical scan means *the record does not contradict itself*; it has never meant the record is right.

**You now drive the rest of the pass.** `save` captures; this reconciles. A task's `decisions` list is **append-only**, so it only grows, and the digest no longer truncates it — **every still-current decision briefs every fresh session**. A decision refuted a month ago goes on briefing every session until something marks it, and nothing ever says "these sixteen entries are now four" unless you do.

## The flow — one pass, no stops

`/heal` runs the whole reconcile and reports when it is done. It does **not** stop to ask before applying.

1. **Scan clean?** First do the one thing it cannot do: **verify that everything which actually shipped since the last heal has a decision** (see below). Then run `heal --mark-healed --task <n> --note '<what you checked>'`, report it in one line, and stop. Do **not** open the dry run — there is nothing in it you need, and without the stamp the record still says this task has never been healed.
2. **Findings?** Read the dry run **once** — `heal --task <n>`, which is the dry run and changes nothing. It carries the full current decision set, which is what makes it expensive: on a real 40-decision task it is ~47,000 characters and **94% of that is the decision list**. That cost is the input your judgement needs, so it is unavoidable — but it is paid once. Do not re-render it, and do not expect `--apply` to repeat it.
3. **Judge, then execute.** Decide what each finding needs, run every operation, and make sure the pass is recorded (`--apply` stamps when it performed work; a judgement-only pass needs `heal --mark-healed --note`). Do not present a plan and wait — the plan and its result are the same report now.
4. **Verify and report.** A final `heal --scan --task <n>`, then one message: the health metric **before → after**, one line per operation, and **the exact undo command the tool printed for each one**. Nothing else.

**What replaces the approval gate.** You are no longer asked before a write lands, so the report is where a wrong call gets caught — and it only works if taking one back is as cheap as approving it was. The CLI prints the reversal for every write it makes: `update --restore-decision <n>` for a supersede, split or merge, `update --step-restore <n>` for a retired step, `update --restore-summary [n]` for a replaced summary, and the pre-heal task-blob path for the one write that has no verb (a retro-disposition, which is never overwritten). **Surface those lines verbatim.** Do not paraphrase them into "this is reversible" — a number the reader has to reconstruct is not an undo.

Full detail, including every command form: the **`heal` skill** (`skills/heal/SKILL.md`).

## The verbs you choose from

The user should never have to name one of these — that is your job.

- **supersede** — `update --task <n> --decision '<the correct call + why>' --supersedes <k>`. Only when something genuinely **refuted** it.
- **split** — for a **compound** decision that mixes still-valid rulings with refuted ones (superseding it would destroy the good half) or one simply too long to read. Add the atomic parts with `--decision` (one call each), note their numbers, then `heal --split <k> --into <n1,n2,…>`.
- **merge** — for decisions that are **true but no longer load-bearing**: release records, iteration steps, process-error corrections. Add the one summary decision, then `heal --merge <n1,n2,…> --into <n>`. Do **not** supersede these — nothing refuted them, and marking them wrong puts a lie in the record.
- **retire a stale step** — `update --task <n> --step-add '<the corrected step>' --step-supersede <k>`. The `--step-add` in the same call is recorded as the replacement. Do **not** tick it done (nobody did it) and do **not** add a warning step about it.
- **retro-dispose** every undispositioned ack — `heal --apply --dispose-acks <id8,…|all> --noop '<why nothing was needed>'` (or `--decision '<what it changed>'` / `--memory <slug>`). `all` is legitimate: those acking sessions no longer exist. Each retro-fill is marked `retro` with who filled it and when, and the original ack is never rewritten.
- **rewrite the `state` line** if it drifted. Same for the **goal line** — `update --task <n> --goal '<what done looks like now>'` — when the record now shows that mission as accomplished, or as one the work moved past.
- **re-read every PINNED decision** the scan lists. A pin heads **every** session's digest, so a line that quietly went stale there costs more than the same line anywhere else. Being pinned is not a defect — the question is only whether it is still accurate.
- **re-read the goal line and every live step** in the dry run, which prints both directly under the decision set. One question for each: **does the newest evidence retire this?** They are what a cold session reads first — the goal says what done looks like, the checklist says what to do, the decisions mostly say why — and neither leaves anything inconsistent behind when reality overtakes it, so eight checks can report clean on a task holding both.

**MERGE CANDIDATES are proposals, not findings.** They are groups of decisions that merely *open the same way*; they are not counted as issues and never make a heal due. Read each group and decide — nothing merges them from their shape alone, because a wrong merge writes a false consolidation into the record. **Running without an approval gate does not widen this.** "Do everything" means do not stop to ask; it does not mean act on a shape match. `--apply` performs only the narrow, sure clusters it already performed; a candidate still becomes an operation only after you have read the group and chosen the surviving summary yourself.

**Re-fragmented consolidations ARE findings.** The difference is who has already ruled. A decision that *declares itself* the one record of several — `CONSOLIDATED — …`, `this decision consolidates 4, 9 and 17`, or a summary a `merge` wrote — with **newer** current decisions sharing the shape it consolidated means the consolidation was undone by accretion, and the record now says two contradictory things about how many entries that subject has. One real task carried `CONSOLIDATED — THE 2.7.0-2.11.0 RELEASE LINE …` and grew four more release-shaped decisions over the next day; nothing noticed. Fix it with `merge` again, folding the consolidation in: write **one** updated summary covering the strays, then `heal --merge <consolidation>,<strays…> --into <n>`. It is never proposed for you — naming the surviving summary is judgement.

**A step restating a SUPERSEDED decision IS a finding — and a provisional one.** A superseded decision is one this task marked *refuted*; a live step still ordering that work is the checklist contradicting the log, and it is handed to the next session as an instruction. It is reported when the two share enough of their significant vocabulary, and the finding says out loud that it is provisional: text overlap **cannot** tell a step that still orders retired work from the step written to *record* the retirement, because both name the same thing. Read the pair, then either retire the step (`update --step-add '<the corrected step>' --step-supersede <n>`) or leave it exactly where it is.

**The GOAL REVIEW is a proposal, not a finding.** It says how many decisions have landed since the goal line was last written — and *cannot be counted* when nothing recorded that baseline, never a false zero. A goal is supposed to outlive the decisions that pursue it, so an untouched one is a reason to look, never proof of anything.

**Expected-ephemeral paths are not findings either.** A recorded path under a session scratchpad or a system temp directory is erased by design, so its absence is not drift; the scan counts them on one line. A vanished **repo** path still is drift, and still gets reported.

## The one gap the scan cannot cover

**Verify that everything which actually shipped since the last heal has a decision** — a release, a merged PR, a document. Every check works by cross-referencing two things the task itself holds; work recorded **nowhere** on the task leaves nothing to cross-reference, so the scan cannot tell "no release happened" from "a release happened and nobody wrote it down". On one real task a shipped release appeared in no decision, no log entry and no PR link while every check reported clean. There is deliberately **no check** for it — a check that guessed would be confidently wrong — so the scan prints an `Accrued since last heal` count instead: compare it with what you know happened from the conversation and the repo, and record what is missing with `update --decision` (plus `--pr` / `--log`).

**A clean scan means the record does not contradict itself. It does not mean the record is complete.** This and re-reading the pinned set are the two judgements the deterministic layer will never make for you.

## Hard rules

- **Nothing is ever deleted, and nothing is rewritten in place.** Every verb *marks* the original and drops it from the default digest (or checklist) while keeping it in `/todo <n> history`, labelled with what replaced it. Reversible with `update --restore-decision <n>` / `--step-restore <n>`.
- **Never touch the `--log` milestone trail or `history`.** They are append-only and sacred.
- **A bare `heal` is a dry run** — it changes nothing. `--apply` performs the mechanical plan, backs the task blob up first, and prints **only what it did** (`--verbose` for the full block).
- **An `--apply` that would perform zero operations is REFUSED**, not stamped. A heal stamp says the task was reconciled; recording one for a pass that did nothing puts a lie in the record.
- **A judgement-only pass must still be recorded** — `heal --mark-healed --note '<what you checked>'`. Otherwise the record says this task has never been healed, and every session opens on a false alarm.

When you finish, confirm in one line what you reconciled — how many decisions were superseded, split, and merged, how many steps were retired and acks retro-disposed, and what the digest went from and to. Then list the **undo commands the tool printed**, one per write. Do **not** re-render the full `/todo` list.
