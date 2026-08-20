# The composed graded gate — one driven turn

**Status:** shipped 3.14.0 (2026-08-19). Track A4, the finish line of TRACK A.
**Engine:** `lib/board/turn.py` · **CLI:** `task-station turn` · **Tests:** `tests/test_turn.py`

Everything this composes already existed and was merged: `scan` (waves over `depends-on`
with a RUNNING column), `invoke` (a child pre-attached to its own task), `exit-tick` (DONE
computed from a step's exit condition), `grade` (G1–G6 at `A-` per dimension, with a retry
and park budget), `memo` (durable correspondence with an ack ledger), `channel` (reaching a
child that is still running), `relay`, the role table, the concurrency budgets. What was
missing was the **parent actually running them in order, with nobody deciding what comes
next**.

That is the whole of A4: one command that reads the board and answers *what does the loop
do now*, in a vocabulary a driver can act on without a person translating it.

---

## 1 · The turn

    task-station turn --task <orchestrator> [--ask '<request>'] [--json]

Zero-token and zero-write, exactly like `scan`, and for the same reason: a planner a
driver has to be afraid of is a planner nobody leaves running. It reads stored condition
results rather than re-running them — `exit-tick` is one of the steps it *emits* — touches
nothing, and calls no model.

**The split is Q4's, unchanged.** The engine owns the deterministic primitives: which child
may start, what state a child that stopped is in, whether the mechanical gate is clean,
whether a rejection may be handed back or must park. The **skill** owns the judgement:
what grade each dimension earns, and what to ask a child for. The commands the turn prints
leave exactly those blanks (`--dim G1=?`, `--ask '<the request>'`).

### The agenda, and why it is ordered this way

| # | action | what it means |
|---|---|---|
| 1 | `gate` | a child came back — run its conditions, read the findings |
| 2 | `grade` / `park` | the judgement, or the refusal to ask again |
| 3 | `release` | accepted → close it, and its dependents unblock |
| 4 | `relaunch` | spawn intent with no liveness — the window never opened |
| 5 | `invoke` | one unblocked, unstarted child |
| 6 | `wait` | a live session is attached; the loop is working, not stuck |

**What came back is gated first.** Grading a finished child can release a wave and hands
its slot back to the budget; invoking first spends the slot the gate was about to return.

**One `invoke` per pass** — this is a stagger, not just a cap. See finding 6.

### Halts

`halt` is set only when the turn emitted no *progress* action, and it names which of six
reasons: `complete` (every child settled), `empty` (no children — the plan is unbuilt),
`working` (children running), `budget` (work is ready and the children cap refuses it),
`parked` (what remains waits for a person), `blocked` (unsettled and none startable).
`complete` and `blocked` are opposite situations and a driver must not confuse them; exit
code 3 says "halted", `halt` says why.

---

## 2 · The seven findings this was built against

Every one was observed live on 2026-08-19 while the loop was driven by hand across seven
children. Each is a lie an unattended turn can act on, so each has a mechanism rather than
a paragraph.

**1 · Silent exit.** A child finishes its work and exits saying nothing, because its exit
conditions run against the MAIN checkout and cannot pass until its own PR merges. Three of
seven children did this. `failed` retries work that may be complete; `unknown` stalls the
loop. → `SILENT_EXIT` is its own state (`turn.child_state`), and its action is to gate it
*with* the missing-report finding.

**2 · The hand-back rail is a memo, not the channel.** The role report contract asked for
a report and named no channel, so the compliant behaviour and the useless one were
identical — a good report written into a window the parent cannot see. A memo is durable,
survives the session ending, and lands on the record the gate already loads; the channel
needs the child alive. → `invoke` now names the rail in the child's own prompt
(`memo send --task <ref>`), a missing report memo is the gate finding `no-report`, and
`grade` sends its verdict back down the same rail.

**3 · Tree, not ancestry.** This repo squash-merges everything, so
`git merge-base --is-ancestor` reports every landed branch as unmerged. The failure
direction is what makes it unacceptable: a driven turn re-opens work already on main. →
`turn.landed` / `turn.landed_probe` — an empty `git diff <merge> <branch>` is the whole
answer, and unmet conditions on a branch that has not been probed are reported as
`pre-merge`, never as a failure.

**4 · Four ways a gate lies.** A false green on unstarted work; an assertion satisfied by
something else (`unittest discover -k <missing>` prints `Ran 0 tests` then `OK` and exits
0; a `tail -3` swallowed by trailing stdout; a bare count substring-matching a bigger
number); a false red from a stale installed plugin or a renamed test; and the squash case
above. → **Pin a positive count, never an absence.** `turn.suite_green` requires
`Ran N tests` with `N ≥ 1`; `turn.condition_lint` refuses the `tail`, bare-count and
absence shapes at registration; `turn.stale_install` names the false-red source; an
unstarted child is `gradeable: false`.

**5 · Spawn intent is not liveness.** A failed window-open still records the invoke and
still mints a session, so a child can read as invoked having never taken a turn — and the
RUNNING column and the double-invoke guard can both be wrong. → `SPAWN_FAILED` and
`MANUAL` are distinct states, derived by reconciling the launch trail against process
liveness plus evidence the child actually worked; their action is `relaunch`, never
`grade`.

**6 · Concurrency is expensive here specifically.** Two children in flight means two
version bumps and a rebase for whoever lands second; three means a three-way conflict. →
`loop_children_max` bounds how many children may be live (counted from process liveness,
never from records, so a crashed child cannot hold a slot forever), and the turn spends
what is left **one child per pass**. `loop_builds_max` remains the machine-wide build lock.

**7 · An order held on a routine notice is expensive.** The channel's Stop gate fires at
every turn end, so an unsettled order costs a round trip every time. Holding an
orchestrator's turn hostage for "your child closed" — bookkeeping the loop minted itself,
already durable on the memo ledger — costs more than it delivers. → The discriminator is
**authorship, not kind**: a memo a session *wrote* still blocks (nobody types into an
invoked child again, which is why the channel exists), and a memo a lifecycle hook minted
rides the ledger (`channel.blocks_turn`, `memo_send(routine=True)`). A **stand-down** and a
moved exit condition block whoever wrote them — neither is bookkeeping.

---

## 3 · The backlog items (task 444's numbering)

Each one is mechanised or explicitly deferred with a reason. The numbering is task 444's
and the definitions are its rubric note's; nothing here was renumbered.

B5 Feature-children reconcile — **DEFERRED**, because the board stays forge-agnostic. The
item is "diff a task's story list against its ADO Feature's ACTUAL children", and the
work-item reader belongs to the brain plane (`brain.ado_tree`), which already owns every
ADO read; a gate that shelled into one forge's API would bind the loop to that forge, and
the dependency only ever points board → brain. What *is* the board's half is already
mechanised: `HALT_EMPTY` reports an orchestrator whose plan has not been built, and the
deep-settled rule refuses to settle a parent whose children are unbuilt however green its
own checklist is. The external diff lands as a brain-side check.

B7 Claims registration self-check — **MECHANISED**. `exit-add` now refuses to store a
condition before it stores it: `turn.shell_syntax_error` asks the shell to *parse* the
command (`bash -n`, never execute — registering a condition must not have side effects),
and `turn.condition_lint` names the shapes that can be satisfied by something other than
the work. `--force` registers anyway and says so. This is the P7A truncation's exact
failure: a command stored broken looks registered and can never run.

B8 Known-flake ledger — **MECHANISED in part, DEFERRED in part**. The half that matters to
the gate is the expected count, and it is mechanised: `turn.ran_count` and
`turn.suite_green` pin a positive count, so a suite that ran nothing is not green and
neither is one whose output never said. The per-test flake registry is DEFERRED, because
the flake that motivated it was root-caused and fixed at ship (`store.mutate` CAS backoff,
`2a7f476`) and the load-dependence that produced the rest is already removed by the
machine-wide build slot (`loop_builds_max`, default 1) — a registry with no observed flakes
is a table nothing writes to, and an empty one still reads like a guarantee. It gets built
the next time a flake recurs, with that flake as its first row.

B9 Number-with-command everywhere — **MECHANISED**. `turn.number_without_command` reports
any step recording a gate number with no command that measures it, as a `G2` finding on the
mechanical gate. Identifiers that merely look numeric — `#444`, a year, a version — are
excluded, because a lint that cried wolf on task refs would be switched off in a day. This
is the Phase-4 58→81 drift: the same number with its measuring command says so the next
time anybody looks.

B13 Memo-ack SLA in the loop — **MECHANISED**. `turn.unacked` counts memos on a child that
nobody has dispositioned, and the mechanical gate reports them as a `G4` finding, so
pending-ack debt is a **loop input** rather than background noise. Twenty-two were
outstanding on the day this was written and the loop had no idea.

B14 Packaging-surface guard — **MECHANISED in part, DEFERRED in part**. The half the gate
needs is mechanised: `turn.stale_install` compares the tree under test against the version
in the plugin cache and names it as a FALSE-RED source, which is finding 4's most expensive
diagnosis — the suite exercises the repo while the hooks and MCP server exercise whatever
`/plugin update` last cached. Asserting the non-`.py` asset trees inside the plugin cache
is DEFERRED, because the cache is a machine artifact written by `/plugin update` and not a
property of the tree under test: the repo half is already asserted (P8A), and the cache
half is a post-update probe — a different verb, on a different trigger, from a gate that
grades a child's work.

---

## 4 · What the turn does not do

* It does not grade. No flag here can supply a dimension's grade; that is the judge's, and
  the loop's quality is bounded by it.
* It does not write. Not one byte — every step it names is a separate, recorded command.
* It does not retry a park. A parked child is never handed back, whatever its budget says;
  a human gate is a park *with* budget left, which is the entire reason the taxonomy
  exists.
* It does not fan out. `Workflow` orchestrates ephemeral subagents inside one session
  lifetime; this orchestrates durable peer sessions across time. Use `Workflow` *inside* a
  step, never as a second engine.
