---
name: judge
description: Run the graded acceptance gate on a child task — verify mechanically FIRST, then score the six rubric dimensions G1-G6 and accept only at A- on every one, rejecting with the failed dimension named. Use when a child task reports done, on "grade this", "run the gate", "accept or reject the child's work", or when `scan` says a wave is ready to release. Also drives one turn of the loop: scan → invoke → gate → grade → release.
---

# The judge — the graded acceptance gate

A child task reports that it is done. **A report is not evidence.** This skill is what stands between that report and the plan believing it.

Measured, on the work this gate was distilled from: all thirteen worker sessions in the 3.0.0 migration shipped **unverified** (`python3` was denied in every one of them), and the hub's own gate caught breakage no worker could see — one chunk reported clean and the suite found 236 errors. A memo asserting a fact was refuted **within the hour**. That is the whole reason step 1 below is non-negotiable and comes first.

The split you are working inside: **the engine owns everything deterministic** (waves, exit conditions, grade arithmetic, recording), **you own the judgment** (what grade each dimension earns, and what the rejection should say). No flag can supply the second half, which is why this is a skill and not a subcommand.

---

## Run it in this order. Do not reorder steps 1 and 3.

### 1. The mechanical gate — RUN IT YOURSELF, before reading the report

Verify from outside the child's own account of itself:

```
python3 "$CLAUDE_PLUGIN_ROOT/lib/task-station.py" exit-tick --task <child>
python3 "$CLAUDE_PLUGIN_ROOT/lib/task-station.py" claims verify --task <child>
```

…plus whatever the work itself demands: the suite, the build, the scrub, an unauthenticated hit on the deployed URL. `exit-tick` exits **1** if anything is not met; `claims verify` exits **1** on a failing claim.

**Verify everything mechanical, not only what the child listed as unverified.** The child's `unverified` list is a statement about what it *knows* it did not check. The interesting failures are the ones it did not know about.

If the mechanical gate is red, you already have your G1 answer, and you can stop reading the report for grading purposes — but still read it, because *why* it is red is what the rejection has to say.

### 2. Read the child's report

In order of preference: the durable report file `delegate` wrote (worktree root, `HANDOFF-*.md` shape), then memos on the task, then the task's own digest (`/todo <n>` — the decisions and the checklist are the record). A report should carry: what changed · the fingerprints/numbers with the commands that produced them · claims registered · **the mandatory `unverified` list** · what the next chunk inherits.

A report with **no `unverified` section at all** is itself a G4 finding. Nobody's work is fully verified; a report claiming otherwise has not looked.

### 3. Grade the six dimensions — with the evidence, from what you just ran

| dim | what it asks |
|---|---|
| **G1 Gate integrity** | did verification run **from outside** — suite unmodified, fresh clone, live URL, hand-verified on the real system? |
| **G2 Measurement fidelity** | were the numbers re-derived **at execution time, with their measuring commands** — and staleness caught rather than trusted? |
| **G3 Contract preservation** | frozen surfaces untouched; behaviours covered by **behavioural** tests (never markup-presence)? |
| **G4 Finding capture** | deviations ledgered **with why**, corrections folded back the same session, claims registered/refreshed? |
| **G5 Scope & ask-gate discipline** | settled rulings honored, ask gates hit **before** the work, no creep? |
| **G6 Ops efficiency** | worker discipline held, wall-clock lost to infra bounded, recovery diagnosed from ground truth? |

Scale, best to worst: `A A- B+ B B- C+ C C- D+ D F`.
**A** exemplary, nothing unrecorded · **B** gate met, minor recorded gaps · **C** gate met but a material miss cost real time or a stale number shipped · **D** gate weakened or partly skipped · **F** gate failed or the work is invalidated.

The full rubric, with the evidence that set each grade on the migration itself, is the vault note `migration-rubric-improvement-loop` §2–3. Read it when a grade is close; it is the calibration set.

### 4. Record it

```
python3 "$CLAUDE_PLUGIN_ROOT/lib/task-station.py" grade --task <child> \
  --dim G1=A --dim G2=A- --dim G3=A --dim G4=B --dim G5=A --dim G6=A- \
  --note '<one line of judgment — what set the lowest grade>'
```

**Acceptance is per-dimension at `A-` (Ryan, 2026-08-14), never an average.** An average lets a failed gate-integrity dimension hide behind five strong ones, which is exactly what six separate dimensions exist to prevent. A dimension you did not grade is **not** a pass — it is work you have not done, and the command will say so.

Exit codes, so you can branch without parsing prose:

| code | meaning | what you do |
|---|---|---|
| 0 | accepted | step 5a |
| 1 | rejected, retries left | step 5b |
| 3 | rejected, retry budget spent | park it (step 5c) |
| 4 | parked | stop; a person owns it now |
| 2 | the command was wrong | fix the command |

### 5. Act on the verdict

**5a — Accepted.** Tick the parent's step for this child, then re-scan to see what the acceptance released:

```
python3 "$CLAUDE_PLUGIN_ROOT/lib/task-station.py" scan --task <orchestrator>
```

Anything newly `READY` is the next wave. Invoke it (step 6).

**5b — Rejected, retries left.** Send the failed dimension back **as a memo on the child task**, so the child's own session sees it at its next prompt:

```
python3 "$CLAUDE_PLUGIN_ROOT/lib/task-station.py" memo send --task <child> \
  --text 'GATE REJECTED — G4 (finding capture) at B: <the specific gap, and what would make it A->'
```

Name the dimension, the grade, and **the specific thing that would move it**. "Improve finding capture" is not actionable; "the three deviations in the report have no *why*, and D7 contradicts decision 12 with nothing recording that" is.

**5c — Park it.** A parked child never comes back to the loop:

```
python3 "$CLAUDE_PLUGIN_ROOT/lib/task-station.py" grade --task <child> \
  --park human-gate --why '<what a person has to decide>'
```

Reasons: `human-gate` (a person must rule) · `blocked-external` (something outside this machine) · `retries-exhausted` (the budget is spent and iterating is not working).

**A HUMAN GATE IS NEVER RETRIED.** Not once, not with a better prompt. Parking it and saying so is the correct terminal state — the loop's job at that point is to stop cleanly, not to keep trying.

### 6. Release the next wave

```
python3 "$CLAUDE_PLUGIN_ROOT/lib/task-station.py" invoke --task <child> --from <orchestrator> \
  --ask '<the request — and only the request>' [--role implementer]
```

The child spawns **already attached to its own task**, so its SessionStart injects its own digest. That is why the ask carries the request only: anything you write restating the child's context is a lossy copy of a record it is already reading. If your ask is running past a few sentences, you are writing a brief, and the brief is the thing this design removes.

Concurrency: at most `loop_children_max` (default 3) children open at once, and `loop_builds_max` (default 1) build or full-suite run **machine-wide** — two orchestrators share that one, because the machine OOMs on concurrent builds and this repo's flakes are load-dependent.

---

## What this skill must never do

- **Grade from the report alone.** Step 1 is not optional and does not become optional when the report is convincing. It was written *because* the convincing ones were wrong.
- **Average the dimensions**, or quietly accept with one ungraded.
- **Retry a human gate.**
- **Write the child's work for it.** If a rejection is easier to fix than to explain, the rejection is not specific enough yet — say the specific thing.
- **Grade its own work.** A reviewer is never the implementer's session; open a fresh one if you were the author.

## Where the pieces live

`lib/exits.py` — exit conditions (why DONE must be computed, at length) · `lib/loop.py` — waves, the rubric constants, grade arithmetic, the orchestrator guard · `lib/board/cmds/loop.py` — the command surface · the vault note `migration-rubric-improvement-loop` — the rubric and the calibration grades · `open-work-register` §0 — why every plan item carries a runnable exit condition.
