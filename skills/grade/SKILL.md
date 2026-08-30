---
name: grade
description: Run the graded acceptance gate on a child task — verify mechanically FIRST, then score the six rubric dimensions G1-G6 and accept only at A- on every one, rejecting with the failed dimension named. Use when a child task reports done, on "grade this", "run the gate", "accept or reject the child's work", or when `scan` says a wave is ready to release. Also drives one turn of the loop: scan → invoke → gate → grade → release.
---

# Grade — the acceptance gate

A child task reports that it is done. **A report is not evidence.** This skill is what stands between that report and the plan believing it.

Measured, on the migration this gate was distilled from: all thirteen worker sessions shipped **unverified** (`python3` was denied in every one of them), and the hub's own gate caught breakage no worker could see — one chunk reported clean and the suite found 236 errors. A reported fact was refuted **within the hour**. That is the whole reason step 1 below is non-negotiable and comes first.

The split you are working inside: **the engine owns everything deterministic** (waves, exit conditions, grade arithmetic, recording), **you own the judgment** (what grade each dimension earns, and what the rejection should say). No flag can supply the second half, which is why this is a skill and not a subcommand.

---

## Run it in this order. Do not reorder steps 1 and 3.

### 0. Ask for the turn — it tells you which step you are on

```
python3 "$CLAUDE_PLUGIN_ROOT/lib/task-station.py" turn --task <orchestrator>          # the agenda
python3 "$CLAUDE_PLUGIN_ROOT/lib/task-station.py" turn --task <orchestrator> --json   # the same object
```

`turn` composes the whole pass — gate what came back, grade or park it, release what was accepted, re-launch a spawn that never came up, invoke one new child, wait on the rest — and prints the exact command for each step, in the order to run them. It calls no model, runs no shell and writes nothing, so it is free to ask again after every step. Exit `3` means it halted, and `halt` names which of six reasons: `complete` · `empty` · `working` · `budget` · `parked` · `blocked`.

Two things it decides that you should not re-decide by hand:

- **What came back is gated first.** Grading a finished child can release a wave and hands its slot back to the budget; invoking first spends the slot the gate was about to return.
- **One invoke per pass.** Two children in flight in one repo means two version bumps and a rebase for whoever lands second; three means a three-way conflict. The turn spends the remaining budget one child at a time.

**Do not poll for a finished child, and do not trust `sessions` to tell you.** `sessions --task <child>` answers whether a *process* is up, and a child that finishes and leaves its window open is a live process with nothing to do — the harness's word for it is `busy`. Two children were left sitting on that word, one for about an hour and one for seven, with nothing broken either time. A child reaching a terminal state now files a **pickup** on this orchestrator, and your own Stop hook will not let a turn end while one is unclaimed:

```
python3 "$CLAUDE_PLUGIN_ROOT/lib/task-station.py" pickup list --task <orchestrator>
python3 "$CLAUDE_PLUGIN_ROOT/lib/task-station.py" pickup take --task <orchestrator> --id <id8>
```

`turn` no longer prints `WAIT` for a child whose report is filed **or** whose exit conditions have gone green since its launch, so a `WAIT` you do see means there is genuinely nothing yet. Go do something else; you will be told. Taking a pickup is not grading it — it retires the notice, and one retires itself the moment you **grade** or park that child (not when the child merely closes: `done` is what a finished child runs, so closure is the hand-back, not the answer to it).

`--json` carries the mechanical findings per child (`gates`), each tagged with the rubric dimension it lands on — so step 3 starts from evidence rather than from prose.

### 1. The mechanical gate — RUN IT YOURSELF, before reading the report

Verify from outside the child's own account of itself:

```
python3 "$CLAUDE_PLUGIN_ROOT/lib/task-station.py" exit-tick --task <child>
python3 "$CLAUDE_PLUGIN_ROOT/lib/task-station.py" claims verify --task <child>
```

…plus whatever the work itself demands: the suite, the build, the scrub, an unauthenticated hit on the deployed URL. `exit-tick` exits **1** if anything is not met.

**`claims verify` has three exit codes and they are three different verdicts.** **0** green · **1** a claim was refuted — something the child proved is no longer true · **3** *nothing ran*: the child registered no claims and gave no reason, so the gate has nothing to re-run and nothing was proved. Exit 3 is a **G4 finding**, not a red gate — grade the work on what it actually did and name the missing claims as the demerit. A task that deliberately registers none records why (`claims --task <n> --none '<reason>'`) and exits **0** with that reason printed; judge the reason, not the absence.

**Verify everything mechanical, not only what the child listed as unverified.** The child's `unverified` list is a statement about what it *knows* it did not check. The interesting failures are the ones it did not know about.

If the mechanical gate is red, you already have your G1 answer, and you can stop reading the report for grading purposes — but still read it, because *why* it is red is what the rejection has to say.

**RED IS NOT ALWAYS FAILED, AND FOUR OF THE WAYS IT LIES ARE MECHANISED.** Read the turn's findings before you conclude anything:

- **`pre-merge`** — the child's conditions run against the MAIN checkout, so its own work cannot turn them green until its PR merges. Probe with `git diff --stat <merge-target> <branch>`; **empty output means landed**. Never `git merge-base --is-ancestor`: this repo squash-merges, so ancestry calls every landed branch unmerged and the failure direction makes you re-open work that already shipped.
- **`unstarted`** — nothing was ever invoked, so there is nothing to grade. A grade here is a false green about work nobody did.
- **`spawn-unreconciled`** — an invoke is on the trail and no session ever took a turn. A failed window-open still records the invoke and still mints a session, so spawn intent is not liveness: **re-launch, do not grade**.
- **`stale-install`** — the suite exercises the repo while hooks and the MCP server exercise whatever `/plugin update` last cached. A red from those is the stale install talking.

And when you run a suite yourself, **pin a positive count**: `unittest discover -k <a name nothing matches>` prints `Ran 0 tests`, then `OK`, and exits 0 — so does a renamed test class. Assert `Ran N tests` with N ≥ 1, plus `OK`. Never assert an absence.

### 2. Read the child's report

**Look on the memo ledger of the child's own task first** — that is the rail `invoke` tells the child to use, and the only one that survives its window closing. A child that worked and left **no** report memo is `silent-exit`: it is neither "failed" nor "unknown", and the missing report is itself a G4 finding the turn already raised. Then the durable report file `delegate` wrote (worktree root, `HANDOFF-*.md` shape), then the task's own digest (`/todo <n>` — the decisions and the checklist are the record). A report should carry: what changed · the fingerprints/numbers with the commands that produced them · claims registered (or the one line saying why none) · **the mandatory `unverified` list** · what the next chunk inherits.

A report with **no `unverified` section at all** is itself a G4 finding. Nobody's work is fully verified; a report claiming otherwise has not looked.

### 3. Grade the six dimensions — with the evidence, from what you just ran

| dim | what it asks |
|---|---|
| **G1 Gate integrity** | did verification run **from outside** — suite unmodified, fresh clone, live URL, hand-verified on the real system? |
| **G2 Measurement fidelity** | were the numbers re-derived **at execution time, with their measuring commands** — and staleness caught rather than trusted? |
| **G3 Contract preservation** | frozen surfaces untouched; behaviours covered by **behavioural** tests (never markup-presence)? |
| **G4 Finding capture** | deviations ledgered **with why**, corrections folded back the same session, claims registered/refreshed? **Claims are the commands the child already ran, with the substring it already asserted on** — if its report quotes real output and `claims verify` exits 3, the claims were extractable and were not registered. If the child says why it registered none and the reason holds, that is not a demerit. |
| **G5 Scope & ask-gate discipline** | settled rulings honored, ask gates hit **before** the work, no creep? |
| **G6 Ops efficiency** | worker discipline held, wall-clock lost to infra bounded, recovery diagnosed from ground truth? |

Scale, best to worst: `A A- B+ B B- C+ C C- D+ D F`.
**A** exemplary, nothing unrecorded · **B** gate met, minor recorded gaps · **C** gate met but a material miss cost real time or a stale number shipped · **D** gate weakened or partly skipped · **F** gate failed or the work is invalidated.

Keep your own calibration set — a short record of what each grade actually earned on past work — and read it when a grade is close. Grades recorded by `grade` accumulate on each child task, so the board itself becomes that record over time.

### 4. Record it

```
python3 "$CLAUDE_PLUGIN_ROOT/lib/task-station.py" grade --task <child> \
  --dim G1=A --dim G2=A- --dim G3=A --dim G4=B --dim G5=A --dim G6=A- \
  --note '<one line of judgment — what set the lowest grade>'
```

**Acceptance is per-dimension at the configured threshold (`loop_accept_threshold`, default `A-`), never an average.** An average lets a failed gate-integrity dimension hide behind five strong ones, which is exactly what six separate dimensions exist to prevent. A dimension you did not grade is **not** a pass — it is work you have not done, and the command will say so.

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

**Pass a long `--note` or `--why` on stdin, never as a shell word.** Both take `-` (stdin) or `@PATH` (a file). A note quoted on the command line loses anything inside backticks — the shell runs it as a command — and `grade` still reports success, so the child reads a rejection with the term it needed cut out of it.

**5b — Rejected, retries left.** `grade` already sent the verdict back **as a memo on the child task**, naming each failed dimension with its grade and listing the ungraded ones separately. You do not send a second memo; you make the `--note` say the thing that matters, because that note travels in it:

```
python3 "$CLAUDE_PLUGIN_ROOT/lib/task-station.py" grade --task <child> --dim … \
  --note 'the three deviations in the report have no why, and D7 contradicts decision 12 with nothing recording that'
```

Name the dimension, the grade, and **the specific thing that would move it**. "Improve finding capture" is not actionable. (`--no-memo` suppresses the send, for a grading you are only recording.)

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

The child spawns **already attached to its own task**, and its launch prompt tells it to read that task (`search --detail <n>`). That is why the ask carries the request only: anything you write restating the child's context is a lossy copy of a record it is about to fetch in one command. Attaching a child delivers nothing on its own — the read is what gets it the record. If your ask is running past a few sentences, you are writing a brief, and the brief is the thing this design removes.

Add `--cwd <path>` when the child belongs in a worktree, and `invoke` clears the first-run gates you cannot answer for it — the trust dialog and the workspace's own `.mcp.json` approval. It does so **only for a worktree whose main checkout is already trusted**; anything else is refused with the reason printed and the launch proceeds anyway, so the child stops at one dialog instead of stalling invisibly (a session waiting on the trust prompt has not fired SessionStart, so the scan cannot see it at all).

**To preview, use `--dry-run` — never `--print-command`.** The dry run prints the command and writes nothing. `--print-command` is a *real* launch you finish by hand: it mints the child's session and records a `MANUAL LAUNCH` on both tasks. That distinction is what the RUNNING column counts, so previewing with the wrong flag makes one child read as two invokes and the double-invoke guard stops working.

**The two concurrency budgets are enforced, not advisory.** `loop_children_max` (default 3) is refused *at invoke time*: over the cap, `invoke` exits 3, writes nothing, and names the children that are running — counted from process liveness, so a crashed child never holds a slot. `invoke --force` launches over it and records that it did. `loop_builds_max` (default 1) is a real lock in the data dir, so it is **machine-wide**: `exit-tick` and `scan --run` take a slot and wait (`--build-wait`) before refusing, and two orchestrators contend for the same one — because the machine OOMs on concurrent builds and this repo's flakes are load-dependent. Both are on the config board (`--loop-children-max`, `--loop-builds-max`).

**The role table is configuration** (`config --roles` on the board, `"roles"` in `config.json`), and each role carries the model, permission mode, effort, a TOOL GRANT and a REPORT CONTRACT. The grant is a deny list — an allow list would replace the human's tool set instead of narrowing it — and the contract is appended to the child's prompt, so what the child owes you back is stated rather than assumed. A station may retune any field per role, or declare a role of its own; an override naming a field or a mode the CLI would reject is refused and reported on the board rather than half-applied.

---

## What this skill must never do

- **Grade from the report alone.** Step 1 is not optional and does not become optional when the report is convincing. It was written *because* the convincing ones were wrong.
- **Average the dimensions**, or quietly accept with one ungraded.
- **Retry a human gate.**
- **Write the child's work for it.** If a rejection is easier to fix than to explain, the rejection is not specific enough yet — say the specific thing.
- **Grade its own work.** A reviewer is never the implementer's session; open a fresh one if you were the author.

## Where the pieces live

`lib/exits.py` — exit conditions (why DONE must be computed, at length) · `lib/loop.py` — waves, the rubric constants, grade arithmetic, the orchestrator guard · `lib/turn.py` — the driven turn: child states, the mechanical gate, the retry/park decision · `lib/board/cmds/loop.py` — the command surface · `docs/specs/LOOP-GATE.md` — the turn's spec and the seven findings it was built against.
