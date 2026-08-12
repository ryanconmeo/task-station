---
name: heal
description: Reconcile a task's append-only decision log into current state — supersede what is now wrong, split what is compound, merge what is true but no longer load-bearing, retro-dispose stale acks, adjudicate a false-positive finding away with a reason, and refresh or re-read a drifted goal. Use on `/heal`, `/todo heal`, "reconcile this task", or when the SessionStart nag says a task is under-reconciled. Runs as ONE uninterrupted pass — scan, judge, apply, verify — and reports the exact one-command undo for every write it made.
---

# Heal — the reconcile pass

`save` **captures**. This **reconciles**. They are different jobs, and until now only the first one existed.

A task's `decisions` list is **append-only**: it only grows, and the default digest **does not truncate it** — every still-current decision briefs every fresh session, however old. So a decision refuted a month ago goes on briefing every session until something marks it, and nothing in the record ever says *"these sixteen entries are now four"* unless this pass says it. Reconcile is the only thing keeping the digest honest.

Measured on one real task before this existed: **72 decisions, 68 still current, ~96,000 characters** — roughly 24k tokens of resume context, averaging 1,351 chars each. The longest single decision was **27,707 chars**, and it was that long *because* the digest used to truncate by age: its author front-loaded everything into one entry hoping it would land inside the visible window. **Truncation manufactured the bloat it then hid** — which is why it was removed, and why the number above is now the digest's real cost until you reconcile it.

---

## YOU drive this. Run it in this order, and do not deviate.

**A heal is one conversation, not a sequence of flags the user types** — and not a sequence of questions either. The CLI is one-shot and cannot ask anything, so dry-run-as-default is all the safety it can offer; the ordering, the judgement and the finishing are **yours**. `/heal` runs the whole pass and reports when it is done. Follow these five steps exactly.

**There is no approval gate, deliberately.** You do not present a plan and wait for a yes. What replaces the gate is the **undo trail**: the CLI prints the exact reversing command for every write it makes, and step 5 requires you to surface those lines verbatim. Removing the question is only defensible because taking a wrong call back now costs one paste — so never soften those commands into "this is reversible", which is the version nobody can act on.

### 1. Run `heal --scan` FIRST

```
python3 "$CLAUDE_PLUGIN_ROOT/lib/task-station.py" heal --scan --task <n>
```

It is deterministic, about **700 tokens**, and invokes no model at all. It tells you whether there is anything to do before you spend anything finding out. **Never open with the dry run** — that is the expensive block, and reaching for it first is how one heal came to cost two.

The task can be named positionally (`heal --scan 12`), with `--task 12`, with `--all` to sweep the board, or not at all to use the attached task. A positional ref combined with `--all`, or with a `--task` naming a *different* task, is refused rather than resolved — there is deliberately no precedence rule, because the cost of guessing wrong is a reconcile written onto the wrong record.

### 2. Scan clean? Check the two things it cannot see, then stamp it and stop.

Read the closing rows before you believe the verdict. The scan now ends on **three**: `Mechanical` (what the checks found), `Judgment` (whether the half no check can do has been *recorded* — a stamp carrying a `--note` — or `NOT RUN`), and only then `Heal due?`. `Heal due? no` on its own reads as *this task is a complete record*, and it has never meant that.

A clean scan means **the record does not contradict itself**. It does **not** mean the record is complete — see *the one gap the scan cannot cover* below — and it does not mean the record is still **true**: re-read the goal line and the live checklist against the newest decisions (step 4, and the section *what a clean scan still cannot see*). So before stamping, do the one judgement the deterministic layer structurally cannot: **verify that everything which actually shipped since the last heal has a decision** — a release, a merged PR, a document. The scan prints an `Accrued since last heal` line for exactly this; check it against what you know happened from the conversation and the repo, and record anything missing with `update --decision` (plus `--pr` / `--log`).

Then record the pass, in one command:

```
heal --mark-healed --task <n> --note '<what you checked>'
```

If re-reading the goal line was part of what you checked and it is still right, record that too — `heal --goal-reviewed --task <n>`, which may be given in the same call. The stamp deliberately does not imply it.

Then report in **one line** and stop. **Do not read the dry run at all.** A clean task has nothing in that block you need, and the stamp is what stops the next session opening on a false "under-reconciled" alarm.

### 3. Findings? Read the dry run — **ONCE**

```
python3 "$CLAUDE_PLUGIN_ROOT/lib/task-station.py" heal --task <n>
```

A bare `heal` is the dry run and changes nothing. It carries the full current decision set, which is what makes it expensive: on a real 40-decision task it runs to ~47,000 characters (~12,000 tokens) and **94% of that is the decision list**. That cost is the input your judgement actually needs, so it is unavoidable — but it is paid **once**. Never re-render it, and never run `heal` and then `heal --apply` expecting the second to tell you something new: `--apply` deliberately prints only what it did.

### 4. Do the judgement, then EXECUTE — straight through

Work the block: which decisions are refuted, which are compound, which are true but no longer load-bearing, which acks need retro-disposing, which steps are stale, whether `state` drifted, whether each pinned decision is still accurate, and — the ones the scan cannot raise — whether **the goal line and every live step have been overtaken**, and whether **everything which actually shipped since the last heal has a decision**.

**The goal and the checklist are printed in the dry run directly under the decision set**, and the question for each is the same: *does the newest evidence retire this?* They sit there because a cold session reads them **first** — the goal says what done looks like, the checklist says what to do, the decisions mostly say why. Rewrite a drifted goal with `update --task <n> --goal '<what done looks like now>'`; retire an overtaken step with `update --task <n> --step-add '<the corrected step>' --step-supersede <k>`.

**Working the merge candidates?** Run `heal --candidates --task <n>` instead of re-reading the dry run — it prints the goal, the pins and each group's members in full and nothing else. **A finding you read and judged to be wrong** gets adjudicated rather than left to reappear: `heal --apply --task <n> --dismiss '<check>:<ref>' --why '<why it is not a defect>'`.

Then **run the verbs** (below) — every one of them, in this same turn. Do not write the plan out and wait for a yes; the plan and its result are the same report now, and it is written in step 5 once the work is done.

Then make sure the pass is **recorded**: `--apply` stamps when it performed at least one operation, so if every operation you ran was a judgement verb (`update --decision … --supersedes`, `update --step-supersede`), finish with `heal --mark-healed --note '<what you did>'`. If you re-read the goal line and it is still right, add `heal --goal-reviewed` — nothing else records that, and without it the count keeps climbing towards a due heal for a line you have already checked.

**What executing does NOT widen.** MERGE CANDIDATES stay proposals. Removing the confirmation removed a *question*, not a *guardrail*: a candidate is a group of decisions that merely open the same way, and merging one from its shape alone writes a false consolidation into the record, where it then reads as reconciled fact. Read each group, decide, and write the surviving summary yourself — exactly as before. The narrow clusters `--apply` already performs are unchanged.

### 5. Verify, then report — with the undo trail

Run a final `heal --scan --task <n>`. Then one message:

- the health metric **before → after**;
- one line per operation, saying what it did;
- **the exact undo command the CLI printed for each one**, verbatim.

The CLI generates those commands as it writes — `update --task <n> --restore-decision <k>` for a supersede, split or merge, `update --task <n> --step-restore <k>` for a retired step, `update --task <n> --restore-summary [v]` for a replaced summary, and the pre-heal task-blob path for the one write with no verb of its own (a retro-disposition is never overwritten, so nothing can clear it). Copy them; do not paraphrase them. **"Every heal is reversible" is not an undo** — it is a fact the reader cannot act on without first working out which numbers moved, and that reconstruction is exactly what the approval gate used to make unnecessary.

Do not re-render the `/todo` list.

### The user should never need to type `--apply`, `--merge`, `--split`, `--dispose-acks`, `--mark-healed`, `--goal-reviewed`, `--candidates`, `--dismiss` or `--probe-links`

**You choose them.** They exist so this skill can act precisely and so a script can too; they are not a menu for a person to pick from. A user who types `heal --apply` on a hunch gets the worst of both worlds — they pay for the dry run they never read, and before this release they also got a heal stamped for a pass that did nothing. If the user does name a flag, honour it; otherwise never make them learn one.

---

## The two layers

**Layer 1 — the scan.** `heal --scan`. Deterministic, zero tokens, and it **never modifies the task**. Ten checks — nine findings plus the health metric — each reported clean or with findings:

| Check | What it finds |
|---|---|
| Undispositioned acks | Acks with no disposition — every ack recorded before 2.9.0 reads this way. Retro-dispose them with `--dispose-acks` (below) |
| Cited commits that resolve nowhere | **The first outward check.** A commit sha the record *declares* (`commit <sha>`, `merged <sha>`, `main @ <sha>`) that resolves in **none** of the task's repos — history was rewritten and the record now points at a commit nobody can read. Bare hex tokens are **never** matched: a task id, a memo id8 and a heal fingerprint are all hex too. UNKNOWN (no repo found, git error) is never reported, and with no prober wired nothing is probed at all — the SessionStart path spawns no subprocess |
| Digest grew with candidates outstanding | The digest is **larger** than at the last heal **and** at least one merge candidate group is outstanding. Neither half is a defect alone: a working task records work, and a group nobody has ruled on is not wrong. Together they say the record is getting more expensive to brief in exactly the place a named verb was waiting. Needs a baseline, so a never-healed task is silent |
| Corrections never applied | A memo declaring `--corrects` whose target was never updated |
| Unlinked supersession language | A decision *saying* "decision 4 was wrong" but linked to nothing — prose pretending to be structure. **Three** conditions, all required: the language, **and** a decision-shaped target (`decision N` / `entry N` / `#N`, naming an earlier decision that exists), **and** a sentence that *declares against* that decision rather than *reporting on* it. Prose that merely *describes* supersession — a rule superseding a rule, a corrected memory note, a memo chain, the supersede feature itself — is **not** reported, and neither is prose that attributes the action to the other decision: `corrected by decision 184`, `decision 173 investigated`, `why decision 150 is NOT superseded`. It fired 5 times on one task with 4 false, then 8 times on another with all 8 the reporting shape; a check that is 80% wrong is one the reader learns to skip |
| Oversized decisions | Past **6×** the 600-char write advisory (3,600) — the length at which an entry can no longer be superseded a piece at a time, because every ruling inside it is welded to every other one. Between **2×** and 6× it is a **proposal**, not a finding: a real task's decisions *average* ~1,400 chars, so reporting that band as a defect would put `Heal due? YES` on every healthy record on the board. Both numbers derive from the one write advisory (`decisions.LONG_DECISION_CHARS`) rather than being picked separately |
| Drift | Recorded paths and worktrees that no longer exist, plus branches named in the digest that resolve nowhere. A branch candidate must be **ref-shaped** (backticked, or carrying a `/`, `-`, `_` or digit, or a conventional name like `main`/`dev`) — otherwise the English word after "branch" gets reported. **Session scratchpads and system temp paths are excluded outright** — see below |
| Link rot | Stored PR/story URLs that no longer resolve |
| Health metric | Current decision count + total chars + how the last heal reads — the number that says a task is under-reconciled. Never healed says exactly that; it does **not** report the whole log as "new since the last heal" |
| Stale steps | Steps that **declare themselves** dead, and the verb that retires them: `update --step-supersede <n>`. Same two conditions as the row above, same guard: the language **and** a declaration — the keyword opens the step, a line or a clause, or is the predicate of one (`this step is stale`, `steps 3/4/5 above are STALE`, a bare `do not execute`). A step that merely *describes* staleness is **not** reported: a file to delete (`delete stale tracked BRIEF-x.md`), an ancestor a heal already superseded, a rejected dead end it warns you away from. Those three were all a fully-healed task had left, and they kept `Heal due?` on YES with nothing to do |
| Steps restating a superseded decision | A **live** step whose text restates a decision this task has **superseded** — the checklist still ordering work the log already ruled refuted. Compared by Jaccard overlap of significant words, reusing the same tokenizer the merge shapes use. Reported **provisionally**, in those words: text overlap cannot separate a step that still orders the retired work from the step written to *record* the retirement, so any step carrying correction vocabulary at all is skipped outright and what survives says READ THESE TOGETHER rather than claiming to know which is stale |
| Re-fragmented consolidations | A decision that **declares itself the one record of several** — `CONSOLIDATED — …`, `this decision consolidates 4, 9 and 17`, or a summary a `merge` wrote — standing in front of **newer** current decisions that share the shape it consolidated. The consolidation was undone by accretion, and the record now says two contradictory things about how many entries that subject has. A **finding**, not a proposal, because an earlier pass already ruled on that subject — but the merge is still never proposed for you. Same guard again: a decision that merely *mentions* consolidation is not one |

### The rule behind four of those rows: **declare vs describe**

Every keyword check here answers one question before it reports: **does this text DECLARE the condition, or merely DESCRIBE it?**

**And a fifth vocabulary answers a second question the guard cannot: WHO does the sentence say did it?** Reading the word in *front* of a keyword tells you what noun it is about; it says nothing about the actor. So `corrected by decision 184` (the reference is the **agent**), `decision 173 investigated` (the reference is the **subject** of a reporting verb) and `why decision 150 is NOT superseded` (the sentence **denies** the condition) all satisfied the two older conditions perfectly, and on one real task **8 of 17 findings** were that one shape. `heal.reports_another_decision` reads all three, scoped to the clause. A form of *to be* after the reference is deliberately **not** a reporting verb: `decision 4 was wrong` is the finding worth having.

That rule is written down because the same bug shipped **four separate times** — the drift check scraped branch names out of English prose, the unlinked-supersession check fired on decisions *explaining* supersession, the stale-step check fired on the step written to *fix* staleness, and the memo correction backstop fired on a release note mentioning "a superseded ancestor" and on a memo reporting that *someone else* had withdrawn a release. Each time the fix was the same: read the word standing **in front** of the keyword. There is now exactly one implementation of that (`heal.qualifier` → `heal.declaring_hits`), and **a new check brings a vocabulary to it, never a fifth heuristic.** The re-fragmentation row is the fifth vocabulary (`heal.CONSOLIDATION_QUALIFIERS`): `a consolidation of the release trail` and `a wrong merge writes a false consolidation into the record` both *describe* one, while `CONSOLIDATED — …`, `this decision consolidates 4, 9 and 17` and `one reconciled record of 5 decisions` each *declare* one. A false negative is the deliberate, cheaper failure every one of these chose: a missed finding costs one confused resume, a check that cries wolf costs every finding it would ever have made.

### When a shape match IS a finding

**Merge candidates are proposals; a re-fragmented consolidation is a defect.** The difference is not the evidence, it is whether anybody has already ruled. Four release records sitting side by side are fine — nobody has said they should be one. A decision that *declares itself* the single record of that subject, with newer entries of the same shape standing after it, is the record contradicting a ruling it already carries, and the digest hands both answers to every fresh session.

Measured: a real task carried `CONSOLIDATED — THE 2.7.0-2.11.0 RELEASE LINE … (replaces the five per-release records)` and then grew **four more** release-shaped decisions over the following day. Nobody undid the merge; the shape simply grew back. The scan reported nothing, because until now nothing looked.

The fix is the `merge` verb again, with the consolidation folded in: write **one** updated summary that covers the strays too, then `heal --merge <consolidation>,<strays…> --into <n>`. It is **not** proposed for you — naming the surviving summary is judgement, and a wrong merge writes a false consolidation into the record, which is the very thing this check catches.

### An ephemeral path is not drift

The drift check ignores anything under a **session scratchpad** or a **system temp root**, and counts them on one line as *expected-ephemeral* instead. On one real task all **seven** drift findings were worker briefs under `/private/tmp/…/<session-uuid>/scratchpad/` that task-station had auto-captured as artifacts. That directory is wiped when the session ends *by construction*, so "the digest points a resumed session at somewhere it cannot go" was true and useless — nobody resumes a task by opening a worker brief out of a deleted temp directory. Seven of them made a heal due on a task with nothing wrong with it. A vanished **repo** path is still reported, and that is the whole distinction: somewhere the system promised to keep, versus somewhere it promised to erase.

### The sections that are NOT findings

The scan also prints these, below the checks. None counts as an issue — **one** of them (the goal review) can make `Heal due?` true, and it says so in its own row; the rest never can. A task can carry plenty of all of them and be perfectly reconciled.

**SUBJECT CANDIDATES** — the stronger merge tier, rendered first. Current decisions grouped by what they are **about**: an overlapping **step** reference, a shared **release version** (`2.13.1`), a shared **PR/story number**. Grouping is transitive, so one subject is one group rather than three overlapping pairs. **Two** members are enough here where the shape tier needs three, because a shared subject is direct evidence and a shared opening phrase is not. A group tagged **COMPLETED-SUBJECT** has every step its members name already **done or superseded** — the closest the record ever comes to saying "true but no longer load-bearing" on its own, because the checklist says it rather than a reader inferring it. Step references are read only in explicit form (`step 29`, `steps 3-6`, `steps 3, 4 and 5`); a bare number is never one.

**MERGE CANDIDATES** — the secondary tier. Current decisions grouped by their **leading shape** — a version-like prefix plus the word after it (`<version> shipped`), or the first three significant words (`my process error`) — reported when three or more share one. The judgment list used to say "MERGE what is TRUE BUT NO LONGER LOAD-BEARING" and leave you to go and find them; on one real 99-decision task a human found all sixteen mechanically, by matching exactly these prefixes. It stays because it catches the process-error and scrub-iteration families that name no step, release or work item at all. **They are proposals, and nothing merges them for you**: read each group, decide whether it really is true-but-not-load-bearing, and write the one surviving summary yourself. A wrong merge is worse than a missed one — it puts a false consolidation in the record, where it reads as reconciled fact.

**Read the groups cheaply:**

```
heal --candidates --task <n>
```

prints the goal line, the pinned decisions, and **each candidate group's members in full — and nothing else**. The full dry run is ~47,000 chars on a real task and 94% of that is the decision corpus; if you have already decided to work the merges, this is the same reading with the corpus removed. Read-only, stamps nothing.

**DIGEST SIZE** — `chars now · at last heal · delta`. Always printed, including when there is no baseline (which reads *no baseline recorded*, never a false zero). It is the **objective**: a reconcile is supposed to make this number go down, and until the stamp snapshotted it nobody could tell a digest 40k down from last week from one 40k up. Growth *with candidates outstanding* is the one finding this measurement can raise.

**LONG DECISIONS** — the proposal tier of the size check: over 2× the write advisory, worst-first, capped at five with a `+N more`. Not defects — that band is the normal length of a working entry.

**PINNED DECISIONS**, each with its age. A pin puts that entry at the head of **every** session's digest, so a line that has quietly gone stale in a pinned decision costs more than the same line anywhere else: on one real task a pinned decision still named two codenames a later decision had retired, and it had been briefing every session with them for days. No check would ever have caught that — none of them asks whether a decision is still *accurate*. So re-read each pin and confirm it still is. Being pinned is not a defect. An `age unknown` means the append predates the bounded event feed, not that the decision is new.

**GOAL REVIEW** — the goal line, plus how many decisions have landed since it was last written **or re-read**. It exists because the goal is the one field that says what *done* looks like and **nothing on the task can contradict it**: there is no second field claiming the same thing, so no check will ever raise a goal that describes a mission already accomplished. On one real task that is exactly what happened, for days, while every check reported clean. The count comes from a baseline written when the goal is set; with no baseline it says **cannot be counted**, never a false zero, and every task that predates the measurement takes that path.

**It is never an ISSUE, and past 25 decisions it IS a reason a heal is due.** Those are not in tension: an untouched goal is not a defect (a goal is *supposed* to outlive the decisions that pursue it), but a goal nobody has *read* across twenty-five decisions is a sentence the next cold session will take as the plan on evidence nobody has checked it against. The threshold is `heal_goal_review_due` in config, `TASK_STATION_HEAL_GOAL_REVIEW_DUE` in the environment, positive-only.

**Re-reading is the service — so recording the re-read is a verb:**

```
heal --goal-reviewed --task <n>
```

That resets the count **without rewriting a sentence that is still true**, and it is the only thing that resets it. `--mark-healed` deliberately does **not**: a stamp saying somebody read the record is not a stamp saying they ruled on *this line*, and the difference is what keeps every other stamp readable. It may be combined with `--mark-healed` — that pair is the honest record of a judgement-only pass that included the goal. If the goal *has* drifted, this is the wrong verb: `update --task <n> --goal '<what done looks like now>'` re-baselines it by rewriting it.

**ACCRUED SINCE LAST HEAL** — `+N decisions · +N log entries · +N PR/story links · +N steps`, measured by exact subtraction from the counters the stamp snapshotted. On a never-healed task it says so and gives the totals; on a stamp written before the baseline existed it says *no baseline was recorded* rather than printing four zeros, because zeros read as "nothing happened" when the truth is "nobody measured". It exists to be checked against the section below.

### What a clean scan still cannot see

Every check works by cross-referencing two things the task itself holds. So a decision that was **well-formed and correct when written**, and that reality later refuted, leaves **nothing** internally inconsistent — and neither does the goal line or the checklist it quietly invalidated. Measured on one real task: `heal --scan` reported every check clean and `Heal due? no`, while that task held a goal describing a mission already accomplished and **five live steps naming the two largest work items on it**, both by then provably unnecessary. A cold session reading that checklist would have burned days on retired work.

Two of those are now mechanically visible: a step restating a **superseded** decision is a finding (provisional — read the pair), and the goal review counts what has landed since the goal was written. Neither replaces the reading. That is why the scan closes on `Mechanical` / `Judgment` / `Heal due?` instead of one line: the `Judgment` row cites a heal stamp with a `--note` when there is one and says `NOT RUN` when there is not, because *nothing recorded one* and *it was done* are different claims.

### The one gap the deterministic layer cannot cover

**Verify that everything which actually shipped since the last heal has a decision** — a release, a merged PR, a document. This is the second judgement no check will ever raise (the first is whether a pinned decision is still accurate), and unlike that one it is not a matter of tuning: it is **structural**.

Every check works by cross-referencing two things the task itself holds — prose against structure, a memo against its target, a recorded path against the filesystem. Work that is recorded **nowhere** on the task leaves nothing to cross-reference, so the scan cannot tell "no release happened" from "a release happened and nobody wrote it down". Measured, on the same task as the re-fragmentation above: a release had shipped and appeared in **no** decision, **no** log entry and **no** PR link, while every check reported clean.

A check for it would be the fifth confidently-wrong check this subsystem has shipped and then had to fix, so there is deliberately none. Instead: read the accrual counts, compare them against what you know happened from the conversation and the repo, and record whatever is missing — `update --decision '<what shipped + why>'`, plus `--pr <url>` / `--log '<vX.Y.Z shipped: what>'`. **A clean scan means the record does not contradict itself. It does not mean the record is complete.**

**Layer 2 — the pass.** `heal` prints the `[HEAL]` block: the findings, the four sections above, the health metric, the **full current decision set**, then the **goal line and the live checklist** beside it (the newest decisions are the evidence that retires them), the mechanical plan, and the judgment list. You work that list — once, per step 3.

## The three verbs

`supersede` shipped in 2.9.0. `split` and `merge` exist because supersession alone cannot express two real shapes.

**`supersede <n>`** — something **refuted** it. `update --task <n> --decision '<the correct call + why>' --supersedes <k>` (repeatable, so one decision may replace several).

**`split <n>`** — the decision is **compound**: it mixes still-valid rulings with refuted ones, so superseding it would destroy the good content and keeping it briefs the bad. Also the right verb for an entry simply too long to read, correct or not. Add the atomic parts first (one `update --decision` each), note their numbers, then:

```
heal --split <k> --into <n1,n2,…>
```

**`merge <n> <m> …`** — the decisions are **true but no longer load-bearing**. Supersession cannot touch this: nothing refuted them, they just stopped earning digest space, and marking them "wrong" would put a lie in the record. Measured clusters on one task: 4 release records, 7 scrub-iteration steps, 3 process-error corrections, 2 memo-chain acks — 16 decisions that reconcile to about 4. Start from the scan's **MERGE CANDIDATES** — they are the groups that share a leading shape, and they are proposals only. Add the one summary decision first, then:

```
heal --merge <n1,n2,…> --into <n>
```

## The step verb

Steps get one verb, not three: **`update --step-supersede <n>`** (repeatable). A step goes stale — the plan moved on, or it names a vocabulary that was retired — and before this there was no honest exit. Ticking it done is a lie, deleting it destroys the record, and adding a "do not execute step 3" warning step is the anti-pattern one real task already contained (three of its steps read as stale, and one of those was a warning about another).

```
update --task <n> --step-add '<the corrected step>' --step-supersede <k>
```

The `--step-add` in the same call is recorded as the replacement. The superseded step leaves the checklist and **both sides** of the `n/m` counter — a stale step must not sit in the denominator making the task look permanently unfinished — keeps its text in `/todo <n> history` marked `⊘ … — SUPERSEDED by step <n>`, and comes back with `--step-restore <n>`.

**There is no step edit, deliberately.** Rewriting a step in place mutates the record: the checklist would silently stop matching what was agreed. Supersede the stale one and add a corrected one.

## Retro-disposing old acks

Every ack recorded before 2.9.0 is a bare receipt: it says who saw the memo and nothing about what they did with it. Those sessions are gone and their intent is unrecoverable, so a reconciler fills the disposition in:

```
heal --apply --dispose-acks <id8>[,<id8>…]|all  --noop "<why nothing was needed>"
heal --apply --dispose-acks <id8>               --decision '<what it changed>'
heal --apply --dispose-acks <id8>               --memory <slug>
```

Exactly one disposition — the same three the live `memo ack` requires. **`all` is legitimate and expected here**, not a workaround: one bulk `--noop` with an honest reason is the correct disposition for a batch whose intent cannot be recovered. One difference from the live `memo ack`: `--decision` here *records* that the memo became a decision, and does not mint one — a decision dated to a session that no longer exists would be invented history.

**It does not forge history.** Every retro-fill is marked `retro` and records who filled it in, when, and why; the ledger renders it as `ab12cd34→noop (retro)`. The original ack's session id and timestamp are never rewritten, and a disposition the acking session actually chose is never overwritten. Naming a subset is surgical — the acks you did not name stay undispositioned, and the next scan still flags them.

## A finding that is genuinely wrong: adjudicate it away

```
heal --apply --task <n> --dismiss '<check>:<ref>' --why '<why it is not a defect>'
heal --apply --task <n> --undismiss '<check>:<ref>'
heal --dismissals --task <n>
```

**Why this exists.** On one real task the scan stood at **17 findings and 9 were dead paths a human had already read and ruled on**. The scan had no way to know that, so it reported all nine again on the next pass, and the next. A report that repeats what its reader has already answered is one they stop opening — the same cost as crying wolf, reached by a different route.

**A dismissal adjudicates ONE exact state, never a category.** The fingerprint covers the finding's *matched text* — the check, the ref, and the detail line the check generated (which is where each check records what it actually matched: the char count, the keywords, the overlap percentage, the paired index). So a ruling survives a re-scan and does **not** survive the matched text changing: edit the entry it names and the finding **re-reports**, because "this sentence is fine" was never a ruling about the next sentence.

- **The `--why` is mandatory.** A dismissal with no reason is indistinguishable six months later from a finding somebody buried, so one without it is refused and nothing is written.
- **A dismissed finding leaves the scan entirely** — the findings, the issue count and the due calculus. One informational `Dismissed N` line says how many are silenced, because a silenced finding must never become an invisible one.
- **The ref matches exactly, or as an unambiguous substring of one current finding's ref.** Ambiguity is refused *with the list*, and so is dismissing something the scan is not currently reporting.
- **Nothing is deleted.** `--undismiss` marks the entry retired and full reporting resumes; the ledger keeps it as the record that somebody once ruled that way and later changed their mind.
- **It never stamps a heal.** Adjudicating a false positive is not reconciling a task, and it is its own invocation — it cannot ride along with a split, a merge or a retro-disposition.

**Use it for a false positive you have READ.** It is not a way to quieten a report you have not.

## The one opt-in network call

```
heal --scan --probe-links --task <n>
```

One unauthenticated HTTP HEAD per stored PR/story link. **Off by default** — a session start must cost no network, which is why every stored link has always read UNKNOWN. Only an explicit **404/410** counts as dead; a 401 from a private ADO PR, a 403, a 405, a 5xx, a DNS failure and any exception all stay UNKNOWN and are never reported. "Your PR link is dead" about a live PR is the most expensive false positive this module could print.

## Recording that the heal happened

An `--apply` that performed **at least one operation** stamps the task. One that performed **none** is **refused** — it changes nothing and stamps nothing, and names the two honest moves instead. That refusal exists because the opposite shipped first: a bare `--apply` on an empty plan recorded `last heal <just now>` on a task nobody had reconciled, which is exactly what someone gets when they assume `--apply` *is* the heal. A stamp that is sometimes a lie makes every other stamp unreadable.

`--scan` never stamps: it is read-only, and that is its whole contract.

When the pass was pure judgement — you read everything and nothing needed changing:

```
heal --mark-healed --note "<what you checked>"
```

Without that, the record still reads `last heal never`. Measured on one real task: after **17 merges, 5 supersedes and a split**, the scan still said `last heal never` and `97 new decision(s) since the last heal`, so "heal due?" was permanently YES. An alarm that is always on is the one people learn to ignore.

## Hard rules

- **NON-DESTRUCTIVE, always.** No verb deletes a decision. Each one *marks* the original and drops it from the default digest while keeping it in `/todo <n> history`, labelled with what replaced it — `⊘ … — SUPERSEDED by decision 5` / `— SPLIT into decisions 6, 7` / `— MERGED into decision 8`. History's whole job is to stay complete.
- **Every verb is reversible, and the CLI says HOW, per write.** `update --restore-decision <n>` clears any of the three decision marks and returns it to the digest; `update --step-restore <n>` does the same for a step. Both `heal --apply` and the `update` result line now print the command with the real index filled in, at the moment of the write. **Surface them.** With no approval gate in front of a write, an undo the reader has to reconstruct is not an undo.
- **One write has no inverse: a retro-disposition.** A heal never overwrites a disposition, so nothing can clear one — the only way back is the pre-heal task blob, whose path `--apply` prints. The report says that in those words rather than letting the reader discover it at the moment they need it.
- **Nothing is rewritten in place.** No verb edits a decision, a step, or an ack that was already disposed. Supersede and add the corrected one; a retro-fill lands only where nothing was recorded.
- **`--dry-run` is the DEFAULT.** A bare `heal` changes nothing. `--apply` performs the mechanical plan and **backs the task blob up first** — and refuses outright if that backup cannot be written.
- **`--apply` reports only what it DID.** No scan block, no decision list, no judgment list: you just read those, and reprinting them charges the same tokens twice for one heal. `--apply --verbose` restores the full block if you genuinely want it.
- **Never touch the `--log` milestone trail or `history`.** Append-only and sacred.
- **Per-task by default.** `--all` sweeps the board and warns loudly about its scope first.
- **Do not invent history.** If you cannot tell which half of a compound decision was refuted, split it and leave both halves current — that is honest. Guessing is not.

## When it is due

The SessionStart nag fires one self-capping line when the scan found anything, **or** there are ≥10 new decisions since the last heal (on a never-healed task: ≥10 decisions on the log, said in those words), **or** any undispositioned ack exists, **or** it has been >7 days on an active task, **or** ≥25 decisions have landed since the goal line was last written or re-read (`heal_goal_review_due`; silent when nothing recorded that baseline).

Dismissed findings are **not** counted by any of those limbs — that is what a dismissal means.

Two gates warn without ever blocking:

- **`save`** — because `--summary` **replaces** the summary wholesale, writing one from an unreconciled decision set bakes the drift into the first field anyone reads.
- **`done`** — closing a task with a self-contradictory record makes that record the permanent one.

## Finishing

Confirm in one line: how many decisions were superseded, split and merged, how many steps were retired and acks retro-disposed, and what the digest went from and to. Then the **undo trail** — one line per write, each carrying the reversing command the CLI printed, verbatim. That list is not optional garnish: it is the whole of what replaced the approval gate, and a pass that omits it has removed a safeguard and put nothing in its place. Do not re-render the `/todo` list.

**Then record the heal.** If an `--apply` performed work, it is already stamped. If the pass was judgement alone, run `heal --mark-healed --note '<what you checked>'` — a reconcile nothing recorded is a reconcile the next session is told never happened.
