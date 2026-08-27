# Changelog

All notable changes to Task Station are documented here. This project adheres to
[Semantic Versioning](https://semver.org).

## [3.21.0] — 2026-08-27

**A LONG PROSE VALUE NO LONGER HAS TO SURVIVE SHELL QUOTING, BECAUSE IT NO LONGER HAS TO
GO THROUGH IT.** Every prose-bearing flag had exactly one input path: a shell word. A
shell word is not a string — it is a string the shell has already rewritten. Backticks
inside a double-quoted argument run as command substitution, so

```sh
task-station update --task 12 --decision "the `turn` command found it"
```

stored `the  command found it`. The word and its backticks were gone before this process
started; a stray `turn: command not found` went to stderr, and the write then reported
**success** and exited 0. That last part is why it went unnoticed for so long: there was no
corruption for anything downstream to detect, only a shorter sentence that parses fine.

### Added
- **`-` (stdin) and `@PATH` (a file) on every prose-bearing flag (`lib/board/prose_input.py`).**
  `--decision`, `--note`, `--summary`, `--append-summary`, `--state`, `--goal`, `--ask`,
  `--why`, `memo send --text`, `--title`, `--log`, `--pr-desc`, `--story-desc`,
  `decompose --into`, and the prose flags on `channel`, `add-event`, `add-ledger` and
  `capture-artifacts` — **31 flag definitions across 13 subcommands**. Neither path passes
  through the shell, so backticks, `$(...)`, `$VAR`, quotes and newlines arrive verbatim.

  **The set came from sweeping every free-text option in the parser tree**, not from the
  list the task started with. That sweep is what found `update --log` — the README's own
  pair to `--decision` for the dated-milestone half of the trail — and `--title`, which is
  a sentence like any other. `PROSE_FLAGS` also records, in comments, the flags left out
  and why: `exit-add --cmd`/`--expect` (a shell command and a match substring, where `-`
  and `@` are plausible literal values), `channel --by`, and `glossary --def` /
  `brains --description` — genuinely prose, but each dispatched two ways, so covering one
  path would be the quiet inconsistency this module exists to prevent.

  **`-` follows the convention this codebase already had.** `cmd_post_compact` reads the
  compaction summary from stdin the same way; a second spelling for the same idea would
  have been a second thing to keep correct.

  **ONE file spelling, deliberately.** There is no `--decision-file` alongside `@PATH`.
  `@PATH` composes with a repeatable flag — `update --decision` is `action="append"`, so
  each element resolves independently — where a paired `--<flag>-file` cannot, and a test
  asserts no such twin exists.

  **`@@` escapes a literal leading `@`**, so `@@claude mentioned it` stores
  `@claude mentioned it`. It is the one ambiguity the sigil creates, and it is resolved by
  spelling rather than by guessing from whether a file happens to exist.

  **Every failure is LOUD, because the bug survived by being quiet.** A missing `@PATH`
  file, a `-` with no pipe behind it, a second `-` in one command, and an input that read
  zero bytes are all exit-2 refusals that write nothing. Storing `@/tmp/typo.md` as though
  it were the prose would be the same silent-success bug in a new costume.

- **`tests/test_prose_input.py` — 33 tests.** Each branch (plain string · stdin · file),
  a payload carrying backticks, `$(...)`, `$VAR`, single and double quotes and newlines
  round-tripping byte-exact, the ambiguous values ruled on explicitly (a literal lone `-`,
  a value beginning with a dash, a value containing an `@`), a tty-stdin call with no `-`
  proved not to read stdin at all, and every refusal. Two of them assert the table matches
  the real parser tree, so the convention cannot document a flag it does not resolve.

### Changed
- **The help text for all 31 flags is now generated from the same table that resolves them**
  (`annotate_prose_help`, called on the built parser tree before `parse_args`). The
  documented convention and the implemented one are driven by one source and cannot drift.
- **`heal`, `grade` and `brief` each carry one line** telling a model to use the stdin form
  for long prose. These three hand these commands to a model most often, and `grade --note`
  travels to the child inside the rejection memo — a note that quietly lost its key term is
  a rejection the child cannot act on.
- **README documents the convention** next to the existing content-hygiene rules, with the
  failing call and both replacements.

### Unchanged
- **The plain-string path, exactly.** Only the single character `-` is the stdin reference
  and only a *leading* `@` is the file sigil, so a value that starts with a dash (`-x`) or
  merely contains an `@` (`rnguyen@example.com`) is still used verbatim. stdin is read only
  when `-` was actually passed, so an interactive call with no `-` never blocks on a
  terminal that will never send EOF. The full suite passes unchanged (5313 tests).

### Fixed
- …

## [3.20.0] — 2026-08-27

**A LEADER AT A COMPANY THIS TOOLCHAIN HAS NEVER HEARD OF CAN NOW RUN ONE COMMAND AND GET A
VALID OrgProfile.** Everything org-specific here — the naming registry's domains, the
context-injection keywords, the tier labels, the forge that gates promotion — already read
its values from an OrgProfile at runtime rather than from a literal in this repo. What was
missing was the other half: the only way to *get* a profile was to write one by hand, so
adopting the toolchain meant learning a schema before learning anything else.

### Added
- **`task-station org-setup` — the org-setup wizard (`lib/brain/org_setup.py`).** Four
  read-only scans over systems an org already runs, plus the six answers no scan can
  discover, emitted as a schema-valid OrgProfile that `brain-init --profile` consumes
  directly. Full write-up: `docs/ORG-SETUP.md`.

  **The split the design turns on.** An org's vocabulary is already written down in four
  places; its *decisions* are written down nowhere. So the wizard scans for the words —
  `INFORMATION_SCHEMA` schema names and migration **header comments** → business domains;
  directory **group display names** → function words, departments, role tiers; repo and
  project names → system domains; the **leading segment** of wiki page names → the naming
  habits already in use — and it *asks* for the six choices: org slug, org brain repo,
  per-person mirror template, forge + its org URL, vertical pack, promotion approvers.
  Guessing an undiscoverable answer would be worse than asking, because a wrong guess is
  invisible.

  **Read-only is structural, not a promise in a docstring.** The module opens no connection
  and issues no write. The whole database side is one module constant with no interpolation
  and no parameters, so there is no argument that could turn it into a write; the other
  three scans take data a caller already fetched.

  **Signals are weighted, so prose does not become vocabulary.** A schema name, a project
  name and a wiki leading segment are deliberate acts of partitioning — one occurrence is
  evidence. A migration header word and a repo word are prose, and count only when they
  recur. Without that split, every adjective a developer ever typed becomes a business
  domain and every one-off repo becomes a system.

- **The directory scan is incapable of reading a user object, not merely well-behaved.** A
  directory holds people, so "does not look" is one refactor away from looking. The only
  door in is `read_group_display_names()`, which projects every entry down to a single
  display-name string and **raises** on any entry carrying a user attribute or declaring a
  Graph user type; below it, `scan_directory()` accepts `str` and raises `TypeError` on
  anything richer. The type at the boundary is what makes the crossing impossible: past the
  door there is a list of strings, so there is nothing left for a later change to
  accidentally start reading. A refusal at the door also beats a filter downstream — a
  filter is a thing that can be edited out, and a silent drop trains callers to keep handing
  over people's records.

- **…and where that guarantee stops is stated, counted, and put in front of a human.** The
  screen covers **objects**. A directory section may also carry **bare strings**, and a bare
  string is not inspectable — no attribute to refuse, no type to check — so a person's name
  typed into one (a distribution list named after somebody, in a hand-assembled bundle)
  reaches the profile. Detecting person-shaped names is deliberately NOT attempted: a
  person's name and a department's name are the same shape, and a guess wrong in either
  direction is worse than the gap, because a wrongly-refused group silently loses vocabulary
  and a wrongly-admitted person is the leak the guess was meant to prevent.

  So the rule is that a bare string must never pass **silently**. Every one is counted, and
  the count lands in `provenance.directory.unscreened_entries` and in the wizard's printed
  summary — the two places the person approving the profile actually looks. The schema
  **requires** it, even when zero, so a profile that does not state it fails validation; and
  `scan_directory()` defaults the tally to `None` rather than `0`, because a zero default
  would let a caller who skipped the screen claim everything was screened. Zero is a claim
  somebody made; missing is nobody having looked.

  Found by an independent verification pass attacking the screen rather than reading its
  docstring — the four object-shaped attacks all held, and the fifth shape was the one no
  test had. The provenance test could not have caught it: those words *did* trace to the
  supplied input, which is exactly what that test asserts.

- **Validate, THEN write.** `write_profile()` validates before it opens the file and raises
  rather than writing anything. The rule behind the ordering is that a config the platform
  refuses to parse does **not** degrade to default rules — it means *no* rules — and a
  half-written profile on disk reads as configured. Every finding is reported at once, too:
  a leader fixing one field per round trip re-runs four scans for each trip. The schema is
  DATA (`lib/brain/data/org-profile-schema.json`), for the same reason the naming contract
  is: the wizard, the validator and the docs cannot disagree about what a profile is.

- **The 2026-08-15 mirror ruling is mechanized, not documented.** The per-person mirror name
  is a TEMPLATE resolved from the host identity at init, never a literal an administrator
  types. `resolve_mirror_template()` resolves it — and the schema *requires* the template to
  contain its placeholder, so a literal fails validation and never reaches a profile. A
  ruling that lives only in prose is a ruling that gets typed around.

- **A domain with no justifiable area is listed, never guessed.** Two mappings and no third:
  a word that IS a generic area maps to itself, and a word a shipped generic-English hint
  recognises (`invoice` → `finance`) maps to the hinted area. Everything else lands in
  `vocabulary.unmapped_domains` for a person to assign. A wrong area is not a visible error
  — it is a filter that quietly stops matching — and the org half of the registry is
  PR-gated precisely so a human assigns it.

- **`tests/fixtures/fake-org/` — the wizard runs end to end with no live credentials.** A
  wholly invented organisation: a scan bundle, the six answers, and the emitted profile as a
  committed **golden**. The golden is committed rather than generated-and-discarded because
  a fingerprint scan over a file that was never written is trivially clean — that false
  green is exactly what the committed artifact closes. 32 tests
  (`tests/brain/test_org_setup.py`) cover the four scans, both sides of the group-only
  restriction, the six answers, the template ruling, the validate-then-write ordering, and
  the zero-foreign-fingerprint scan over the emitted profile.

  The fingerprint scan is written as a **provenance** check rather than a denylist of
  foreign org terms: a denylist can only catch the names somebody thought to list, and the
  list is itself a fingerprint (which is why this repo's push gate keeps its pattern list
  out of the tree). Every word in the emitted profile must trace to the fixture or to
  shipped vocabulary instead.

### Changed
- **`task-station org-setup` routes to the brain plane lazily.** The board must not depend
  on the brain plane being installed (the layer rule runs the other way), so the import is
  by name inside the handler and an install without it gets one clear line instead of an
  ImportError traceback. The board restates the wizard's four flags — argparse cannot
  capture a leading `--flag` into a REMAINDER positional, and `org-setup -- --scan-bundle …`
  is a UX nobody types correctly the first time — and because a restated flag set drifts
  *silently* (a forgotten flag is simply never forwarded and the wizard sees a default), a
  guard asserts the two flag sets are equal in both directions.

### Fixed
- **`INFORMATION_SCHEMA` no longer escapes the system-schema drop list.** Scanned names are
  compared as slugs, so `INFORMATION_SCHEMA` normalises to `information-schema` and a
  drop-list held in raw spelling let the most universal system schema there is straight into
  an org's registry. The list is now held in slug form. Found by running the wizard against
  the fixture and reading what it emitted, not by review.

## [3.19.0] — 2026-08-26

**A record is not a source.** Three bugs filed together from one session, and they are the
same bug three times: a signal was available, it was not read, and a plausible-looking
assumption was substituted for it. Each one is answered with a mechanism rather than a rule,
because in all three cases the wrong answer produced **no error** — so "success" is what got
reported and a person found the damage later.

### Added
- **`heal --probe-ado` — reconcile a task against the WORK ITEMS it claims, not only against
  its own log.** Every other check in `heal` reads one record and asks whether it is
  internally consistent. That cannot catch a record which is perfectly coherent and no longer
  resembles the thing it describes, and that has now happened twice. Story 3614 carries **33
  acceptance criteria**; criteria 2, 23, 24 and 28 specify a per-**row** converging applier
  with a row ledger. A task's record described it as *"seeds out of chain"* — criterion 29,
  one of 33 — and a relayed session read the record, designed a file-level checksum ledger
  from first principles over several hours, and shipped something strictly weaker than the
  specification that already existed. Separately, a story and its PR sat inside a Feature the
  task itself claimed for 25 days, unowned, because the story list is hand-maintained and the
  story was filed after the task was created.

  Five new checks, registered in `heal.CHECKS` so they sort, dedupe, count and **dismiss**
  through the same machinery as every other finding: `ado-criteria-unacknowledged` (criteria
  no current decision or live step reaches), `ado-criteria-conflict` (criteria the log HAS
  decided on but words differently), `ado-summary-lossy` (the task's own one-line description
  of a work item, measured against the source title), `ado-sibling-missing` (open, unowned
  children of a Feature this task claims) and `ado-unreachable` (a work item that would not
  read — **unverified**, never "confirmed").

  **Mechanical and judgement are separated on purpose.** One measured quantity does the
  mechanical work: *coverage*, the fraction of a criterion's vocabulary present in the task's
  text — asymmetric, unlike `heal.word_overlap`, because Jaccard scores a 20-word criterion
  inside a 300-word decision at ~0.07 and that under-report is the exact direction that hid
  3614. Whether a decision *contradicts* a criterion is a judgement, so the conflict band is
  reported as a candidate with the criterion printed beside the decision index that covers
  it, and `skills/heal` makes ruling on each one a required step. A check that guessed
  "contradicts" would be the same class of mistake as the truncated field below.

  **Off by default, and it says so.** Several authenticated round trips per work item, so a
  session start never pays. Without the probe those five rows read `not probed — N work
  item(s) unverified`, never `clean`.
- **`core/termhost.py` + `task-station terminal [--open CMD]` — one terminal-host resolver.**
  Ordered: `$TASK_STATION_TERMINAL`, `$LC_TERMINAL` (the only marker that survives ssh and
  tmux), `$TERM_PROGRAM`, each terminal's own variable (`$KITTY_WINDOW_ID`, `$WEZTERM_PANE`,
  `$ALACRITTY_SOCKET`, `$WT_SESSION`, `$KONSOLE_VERSION`, …), then the **process ancestry** —
  which is what still answers when the environment has been scrubbed by a detached re-exec or
  a login shell. Knows iTerm2, Terminal.app, WezTerm, Ghostty, kitty, Alacritty, VS Code,
  Hyper, Warp, Tabby, Rio, Windows Terminal, Konsole and GNOME Terminal.
- **`brain.ado_tree --no-clip`** — description and acceptance criteria in full, without the
  raw field bag `--full` carries.

### Changed
- **The digest's Stories block says the description is a SUMMARY, not the work item.** It
  rendered `<url> — <desc>` with nothing marking the desc as something a person typed on this
  task. Reading the digest is a relayed session's whole job, so the digest is where the
  distinction has to be drawn: the block now says a one-line summary of a 33-criterion story
  is a **pointer, not a scope**, and names both ways to reach the source. Five lines once per
  render, and nothing at all when the task claims no stories.
- **`skills/heal` opens with `--probe-ado`** whenever the task claims work items, adds the
  hard rule *a record is not a source*, and states that `not probed` is not `clean` — a stamp
  on an unprobed task records that the log agrees with itself, which was never in doubt.
- **`skills/ado` documents that the compact view clips**, and that criteria are read in full
  before anything a work item specifies is designed or built.
- **`_open_jump_window` is no longer darwin-gated** — the CLI spawners work wherever their
  terminals do, and the script decides.

### Fixed
- **`brain.ado_tree` returned a plausible wrong value for `acceptance_criteria`, and that is
  what caused the 3614 miss.** `--json` returned **604 characters** for story 3614 — and for
  3607, 2966, 3202 and 3510 too, all exactly 604, because the 600-character clip plus `" ..."`
  lands on the same length every time. 3614's real field is **9,237 characters and 33 numbered
  criteria**; the clip stopped inside criterion 4. The defect was never the truncation, it was
  that a truncated value was **indistinguishable from a complete one**. Now: when the text is
  clipped the plain field name is **absent** — the clip lands under `<field>_preview` beside
  `<field>_truncated`, `<field>_chars`, `<field>_criteria` and the flag that returns the rest,
  so a reader keying on `acceptance_criteria` gets the truth or nothing. `--full` no longer
  clips either (it meant "nothing dropped" while still carrying the clip under the plain
  name). The markdown view now prints `[33 criteria, 9237 chars, 604 shown — --no-clip for
  the rest]`.
- **HTML→text kept no ordered-list numbering, so "criterion 23" became an anonymous line.**
  ADO's editor writes criteria as `<ol><li>`; the tag strip threw the numbers away, which is
  why three of five real stories counted **zero** criteria. Counts now: 3614=33, 3202=24,
  3510=24, 3607=20, 2966=12. Bulleted (`<ul>`) criteria are recognised too and labelled
  *item n*, never *criterion n* — quoting a number ADO does not render sends a reader after a
  label that is not there.
- **"Open a new terminal" assumed Terminal.app.** A session running inside **iTerm** ran
  `osascript -e 'tell application "Terminal" to do script …'`; a stray window opened where the
  session could not see it, the session reported success, and a person had to close it. Both
  signals were in that session's own environment and process ancestry and neither was read.
  `open-session-window.sh` now asks the resolver, **says which host it chose and which signal
  it believed**, drives iTerm2/Terminal.app by Apple Event and WezTerm/Ghostty/kitty/Alacritty
  by their own CLIs — and on anything else **exits non-zero and hands back the command**. It
  never falls back to Terminal.app: that fallback *is* the incident.
- **`close-session-window.sh` carried a second copy of the iTerm2 test** which never learned
  the ancestry fallback, so a detached re-exec with a scrubbed environment could close the
  wrong app's window. Both scripts now ask the one resolver, and a test greps to keep the
  detection from creeping back in.
||||||| parent of 0d84d1b (feat: the org-setup wizard — four read-only scans write a valid OrgProfile (3.19.0))

## [3.18.0] — 2026-08-21

Track H's own checklist, closed out. Nothing here changes behaviour: it records a procedure
that existed only in a place nobody would look, and it adjudicates four checklist items that
had been open long enough to stop meaning anything.

### Added
- **`docs/PATCH-SURFACE.md` — the two-scan procedure for re-deriving the routed name set.**
  `lib/task-station.py` is the facade; a test that patches a name on it only rebinds it there,
  so every patched name is read late through `board._shared.g("NAME")`. The set of such names
  is the patch surface, and it was once derived with **one** scan when there are **two**: a
  direct `ts.<name> = …` assignment, and a `setattr(ts, …)` the first scan cannot see at all.
  Two names were missed that way, and the only place that explained why was a docstring inside
  the test that closed the hole — recorded exactly where a person starting a phase would not
  look.

  **The document is pinned to the code, both directions.** A procedure quoting a regex the
  guard does not run would send the next deriver back down the road that lost two names, so
  `tests/test_patch_surface.py` asserts that the pattern printed in the doc IS `_PATCH_RE`, and
  that the doc states the one-hop resolution limit and what is deliberately out of scope. Its
  measurements are written as a floor and a dated snapshot, never an equality — the count that
  must be exact is `ROUTED`, and the guard is what keeps that exact.

  The pointer guard reads this module's **docstring**, not its source: scanning the source
  would be satisfied by the literal inside the assertion itself, a guard that passes because
  it mentions the thing it is checking for.

## [3.17.0] — 2026-08-21

> Versioned 3.17.0 on the assumption that 3.16.0 lands first. The two are independent
> changes on independent branches; whichever merges second needs only its version bump
> rebased.

**THE LOOP COULD HAND WORK DOWN AND HAD NO WAY TO NOTICE IT COME BACK.** Found by running
the loop for real, and it is the first defect in the loop machinery that only a live run
could surface. A child finished, cut a release, opened a PR and filed its report as a memo
on its own task — exactly what the contract asked for — and the report sat unread for
**seven hours**. Nothing was broken and nothing was lost. The parent simply never looked
where the child had been told to write.

Three causes, and only the second was the loop's fault.

### Fixed
- **An unacked report now outranks session liveness (`child_state`).** The report check used
  to sit at the BOTTOM of the ordering, so a child that finished, filed its report and left
  its session idle in a worktree read as `running`, and the turn printed *"a live session is
  attached — the loop is working, not stuck"*. That sentence was true and the conclusion was
  wrong. Liveness cannot tell a child that is still thinking from one that is done, because
  both are alive; the REPORT can, since filing one is the child saying it is finished.

  **UNACKED, specifically.** An acked report has already been engaged, so a child that is
  live again after one is working rather than waiting — otherwise a graded-and-retried child
  would be gated forever on the report it filed last time round. A PARKED child is still
  never dragged back by its report: that rule predates this one and outranks it.

- **A child's report is surfaced to the ATTACHED PARENT, not only to sessions attached to
  the child.** Track A's rule 4 said a memo *"lands where the gate looks"* — the first half
  (durable, survives the session closing) was true, and the second half was never
  implemented: the awaiting-your-ack nag fires only for the task the READING session is
  attached to. The rule described an intention, which is the same class of defect as a fix
  agreed and never built.

  New `turn.child_reports` / `child_reports_brief`, wired into both nag surfaces
  (SessionStart and the per-prompt rail). Same shape and same bounds as the memo nag — a
  handful of lines, the body truncated rather than the line dropped, an overflow count, and
  the command that reads the full text, because a notice that says a report exists without
  saying how to read it is half a rail. Unacked only, and it fails open: a nag that raises
  is worse than a nag that is missing.

### Added
- **`exit-add --merge-gated` and the `done-pending-merge` state — the honest state that was
  previously unsayable.** Exit conditions run against the MAIN checkout, so a child's own
  work cannot turn them green until its PR lands there. That is by design — it is what stops
  a child grading its own unmerged branch — and it means a child can be genuinely FINISHED
  while every condition it registered is red. Observed: work done, release cut, PR open,
  report filed, gate reading 0 of 6 conditions met. The loop's vocabulary (running / ready /
  settled / parked) had no word for *"finished, waiting on a human to merge"*, so the honest
  state could not be recorded and the dishonest one — unfinished — was the only thing
  sayable.

  **DECLARED, NOT INFERRED.** `turn.landed` can probe whether a branch has landed, but it
  needs a branch name nobody stored and a repo nobody named, and `unprobed` is its common
  answer. A condition's AUTHOR knows at registration time whether it reads the merge target
  — they wrote `git show origin/main:…`. An inference whose usual answer is "unprobed" is not
  a state; a declaration is.

  **IT NEVER SOFTENS A VERDICT.** A merge-gated condition that is unmet is still unmet, still
  a gate finding, and still blocks the release — closing a task whose work has not landed
  would settle a predecessor that cannot yet release anything. The gate reports it as
  `merge-gated` / DONE PENDING MERGE rather than as a red the reader is left to explain, and
  such a child is `gradeable` (the work is finished and the report is there to judge) while
  never `clean`.

- **The turn names `SendMessage` as the way to reach a child that is alive right now.**
  `channel` reaches a child that is TAKING TURNS — transport is the turn boundary, and the
  Stop hook can refuse to let a turn pass until an order is read. That is right for a child
  mid-flight and it has one hole: an IDLE session never reaches a turn boundary, so it never
  reads anything. `channel` also offers reach / orders / stand-down / settle / deny — a
  parent can stand a child DOWN but cannot hand it WORK.

  The mechanism that actually worked, twice, was the harness's `SendMessage`. That is the
  BUILD-vs-ADOPT ruling confirmed by a second live case: the harness already owns cross-
  session delivery, an inbound socket here would be a second one, and the loop's job is to
  NAME the working path rather than re-implement it. So a gated child that is still live
  carries a `reach` line printed as a tool call, never as a shell command — the loop cannot
  run it; the reader can.
## [3.16.0] — 2026-08-21

A narrowing of 3.15.0, found by running it on a real task. 3.15.0 made a git prober degrade
a negative to UNKNOWN when a repo the task NAMES has no local clone to ask — correct, because
*"resolves in none of the task's repos"* would otherwise be claiming something nobody
checked. It was also **too blunt in one direction**: `projects` is append-only in practice
(`add-project` is its only writer and `delegate` its only caller), so ONE dead name silences
the cited-commit check for the LIFE of the task, and the report still printed `clean` for a
check that had not run.

Measured on the task that produced the original evidence, after its repo index was
refreshed: two of its four named repos resolve, and the other two are RENAMES — `claude-todo`
became `task-station`, and a second one moved the same way — so neither can ever resolve
under the name the task recorded. Two dead names, silence forever.

### Fixed
- **An unresolvable named repo is now a FINDING (`stale-project`), not a silence.** It is
  reported by name, with the consequence spelled out — *the cited-commit check reads UNKNOWN
  rather than reporting rot, for every sha on this task* — and with the exact command that
  fixes it. This check earns a row rather than a count, which is the bar this module has had
  to apply four times: a vanished scratchpad costs nothing, while a stale project name
  disables a whole check invisibly.

- **The report stops saying `clean` when it means `undetermined`.** `clean` means *this check
  looked and found nothing*; an unanswerable citation means *nobody looked*, and the two were
  indistinguishable on the row a reader actually scans. The cited-commit row now reads
  `undetermined` when the task names a repo with no local clone, and a footnote names the
  repos responsible — a reader told *"UNKNOWN because claude-todo has no local clone"* can
  act, one told `clean` cannot.

  Deliberately NOT extended to `link-rot`: its probe is opt-in and off by default, so every
  link is unknown on the cheap path, and printing `undetermined` at every session start would
  describe a design choice as a problem — which is how a report earns being ignored. Nothing
  about the record is wrong there; here something is.

### Added
- **`update --project-rm NAME` and `update --project-rename OLD=NEW`** — the repos a task
  names can finally be reconciled. This is what makes the 3.15.0 suppression **removable
  rather than permanent**: clear or rename the dead name and the check resumes on its own,
  with no widening of the claim.

  TWO VERBS, because conflating them loses the fact. `--project-rm` says the repo is GONE.
  `--project-rename` says it is STILL HERE UNDER A NEW NAME — what happened to this repo
  itself — and keeps the task pointing at the work rather than forgetting it. A rename onto a
  name the task already carries COLLAPSES to one entry: the task named one repo under two
  identities, and that is one repo. Both halves of `OLD=NEW` are required, because
  `--project-rename claude-todo` looks like a prune and is not one, and guessing which verb
  somebody meant is how a task silently loses a repo.

  **Dismissing the finding does NOT lift the suppression, deliberately.** *"I accept the
  UNKNOWN"* and *"the scope is complete again"* are different rulings, and only the second
  entitles a prober to say `False`. Dismissal silences the row; pruning the name restores the
  check.

## [3.15.0] — 2026-08-21

Three record-hygiene defects, each of which MANUFACTURES A FALSE FINDING — which is how a
scan trains its reader to stop reading it. All three were root-caused and reproduced on a
real task before any of this was written, and the numbers below are measurements rather
than estimates.

### Fixed
- **`heal`'s git probers asked the WRONG REPO after a folder rename, and answered a
  confident `False` instead of UNKNOWN.** `task_repos` derived the repos to probe from
  `recorded_paths` alone — the FILE and WORKTREE paths a task wrote down. Rename the folder
  those point into and every one of them dies at once, while the paths pointing somewhere
  UNRELATED (a notes vault, the installed plugin cache) survive. The list is then NON-EMPTY
  and holds the wrong repo, so `branch_prober` and `commit_prober` reported a branch and a
  commit that were sitting right there as gone.

  Measured on one real task after `claude-todo` was renamed to `task-station`: **16 of 27
  findings were false.** 15 of the 16 cited shas resolved in the renamed clone and none in
  the vault the prober actually asked; the branch it called gone resolved locally AND on
  origin. And the module's own docstring promise — "an empty list is what makes both probers
  return UNKNOWN rather than False" — was kept for the empty case and broken for the
  wrong-repo case, which is worse, because a list holding the wrong repo looks answered.

  TWO REPAIRS, because the promise had to be restored and not just the search widened.
  **The scope is wider**: the repos a task NAMES (`projects`) now resolve through the repo
  index (`repos.json`), which records where each repo IS NOW rather than where a months-old
  path said it was — a rename MOVES an index entry, it does not delete it. Recorded paths
  stay in the union, because they are the only thing that knows about a worktree or a
  checkout nobody indexed. **And the claim is narrower**: a prober saying `False` is
  asserting "this resolves in NONE of the task's repos", so when a named repo has no LOCAL
  clone to ask, that sentence would be covering repos nobody opened — it degrades to
  UNKNOWN. That was the 16th false finding exactly: a sha in an ADO repo with no clone on
  this machine, reported as rewritten history. Nothing is switched off; with every named
  repo reachable a negative is still a finding, because then the sentence is true. New
  `heal.repo_scope` / `named_repos` / `repo_index`; the index is read fail-OPEN, so an
  absent or unreadable one is simply one fewer repo to ask.

  On the task that produced the evidence this took the scan from **11 findings to 6**.

- **Identical findings were UNDISMISSABLE BY CONSTRUCTION.** `--dismiss` refuses an
  ambiguous selector rather than guessing, which is right — an adjudication written onto the
  wrong finding is silent, permanent, and only discovered when the finding it should have
  covered is missing from a later report. But two findings can be BYTE-IDENTICAL: five
  sessions that each recorded the same worktree cwd produce five identical drift rows, and
  `Name one exactly` is then an instruction nobody can follow. On one real task **7 findings
  were permanently unadjudicatable — 100% of its remaining mechanical issues.**

  They were never five things. One path is gone ONCE, and how many sessions happened to sit
  in that directory is a fact about the sessions. Identical rows now collapse to **one row
  carrying `occurrences`**, so the issue count stops being inflated by session bookkeeping
  and nothing is hidden — the count rides on the row. `occurrences` is deliberately OUTSIDE
  the dismissal fingerprint: a sixth session recording the same cwd must not expire a ruling
  somebody already made about that path. **And the report says a row was collapsed** —
  `• <ref> (recorded 5×) — <detail>` — because folding five rows into one without saying so
  silently loses the fact: "one worktree is gone" and "five sessions all sat in it" are
  different things about the same path.

  For the residual case dedupe cannot reach — the same ref reported with DIFFERENT details,
  which is what one path recorded both as an edited file and as a session cwd produces —
  `--dismiss`/`--undismiss` accept an **ordinal handle**, `<check>:<ref>#<n>`. It is tried
  LAST, never first: a link-rot ref is a URL and a URL fragment can be `#2`, so the whole
  ref resolves exactly as before and the trailing `#<n>` is re-read as a handle only when
  the ref as written landed on more than one row. A row whose ref is already unique among
  the matches is still named by its ref, because that is the honest answer — the selector
  was a substring spanning distinct refs, and the fix is to name the one meant, not to count.

- **`exit-add` now refuses `<path-test> && <command>` by name (`path-test-and`).** Observed
  live: a condition registered as `test -f skills/judge/SKILL.md && python3 -m unittest …`.
  The skill was later renamed `judge` → `grade`, the path test failed, `&&` short-circuited,
  **the tests never ran, and the command produced EMPTY OUTPUT** — so nothing in the
  transcript said which half failed or why. `scan --run` reported a closed, fully-graded,
  released track at 4 of 5 conditions met; unattended, the loop would have refused to
  release it and parked finished work. A false red with no diagnostic is the worst of the
  four ways a gate lies, because there is nothing to read.

  The check is deliberately NARROW. `cd <dir> && <cmd>` is the ordinary way to point a
  condition at a checkout and `cd` FAILS LOUDLY — it prints its own complaint, so the
  transcript says what happened. A file test prints nothing at all; it only sets an exit
  status, and that silence is the defect. So: file-test operators only, at a command
  position, joined by `&&`. String tests (`-z`, `-n`) are excluded — they read a variable
  the same command just set, which no rename can move — and `;` is excluded because it does
  not short-circuit. An addition to the existing registration self-check, not new machinery.

### Added
- **A worked template for writing a condition as a DIRECTION rather than a literal**
  (`tools/checker-template.sh`), pointed at from both registration surfaces
  (`exit-add --expect`, `claims --register`). `test COUNT = LITERAL` is falsified by any
  legitimate release: five of one task's seventeen claims went red in four days for that
  reason alone, and one of them hid three genuinely broken links behind its stale baseline —
  the claim was already red, so the new breakage changed nothing anybody could see.

  NO NEW CLAIM KIND, and that is the decision rather than an omission. A claim and an exit
  condition are both already "a command plus the substrings its output must contain", which
  is sufficient: put the FLOOR or CEILING inside the command, print a PASS token, expect the
  token. A comparison operator in the registration grammar would need a second evaluation
  path, a per-claim rule for extracting the number from output, and a mini-language — new
  surface for a failure that was authoring habit, not missing capability. The template
  carries the three rules each learned from a condition that lied: a floor or a ceiling
  never equality; print the MEASURED VALUE beside the verdict, because a gate one commit
  from red looks identical to one with room to spare; and print a verdict on every path
  including the broken ones, because a command that prints nothing goes red with no
  diagnostic. It is tested as a shipped artefact — it parses, it prints `FAIL` when pointed
  at a repo that is not there, and both help texts are asserted to still point at it, since
  a pointer that rots leaves a file nobody is told about.

## [3.14.0] — 2026-08-19

### Added
- **The driven turn: one command that runs a whole pass of the loop with no human between
  the steps.** `task-station turn --task <orchestrator>` reads the board and answers *what
  does the loop do now* — as an ORDERED AGENDA, each step carrying the exact command that
  performs it: gate what came back, grade it or park it, release what was accepted,
  re-launch a spawn that never came up, invoke one new child, wait on the rest. New engine
  module `lib/board/turn.py`, pure over task dicts like `loop` and `exits`: no model, no
  shell, and not one byte written. Halts with exit 3 and one of six named reasons
  (`complete` · `empty` · `working` · `budget` · `parked` · `blocked`), because "nothing to
  do" and "nothing I CAN do" call for opposite responses.

  Everything it composes already shipped — the exit-condition gate, the wave `scan`,
  `invoke`, `grade` with its A--per-dimension threshold and retry/park budget, the role
  table, the concurrency budgets, the memo ledger, the control channel. WHAT WAS MISSING
  WAS THE PARENT RUNNING THEM IN ORDER. The Q4 split is unchanged: the engine owns the
  deterministic primitives, the SKILL owns the judgement, and the commands the turn prints
  leave exactly those blanks (`--dim G1=?`, `--ask '<the request>'`).

  **THE AGENDA'S ORDER IS LOAD-BEARING.** What came back is gated FIRST, because grading a
  finished child can release a wave and hands its slot back to the budget — invoking first
  spends the slot the gate was about to return. And it invokes **one child per pass**: that
  is a stagger, not just a cap. Two children in flight in this repo means two version bumps
  and a rebase for whoever lands second; three means a three-way conflict. `loop_children_max`
  bounds how many may be live, counted from PROCESS LIVENESS rather than records, so a
  crashed child cannot hold a slot forever.

  **SILENT EXIT IS ITS OWN STATE, because "failed" and "unknown" are both wrong.** A child
  that finishes and exits saying nothing is the single most common thing that happened while
  this loop was driven by hand — three of seven children — and it happens for a structural
  reason: exit conditions run against the MAIN checkout, so a child's own work cannot turn
  them green until its PR merges. Reading that as failed retries work that may be complete;
  reading it as unknown stalls the loop. So `silent-exit` sits alongside `reported`,
  `running`, `spawn-failed`, `manual-pending`, `parked`, `settled` and `unstarted` — eight
  states, no two of them synonyms, each with a different next action.

  **SPAWN INTENT IS NOT LIVENESS.** A failed window-open still records the invoke and still
  mints a session, so a child can read as invoked having never taken a turn — and both the
  RUNNING column and the double-invoke guard can be wrong about it in opposite directions.
  The turn reconciles the launch trail against process liveness AND against evidence the
  child actually worked (an event, a memo, a grade, a condition run), so a dead spawn is
  RE-LAUNCHED and never graded. The `MANUAL LAUNCH` marker now has exactly one definition,
  in `turn`, read back by the CLI seam — writer and reader of a trail that decides whether a
  child is re-launched must not be two copies of a string.

- **`docs/specs/LOOP-GATE.md`** — the spec: the turn's shape, the seven findings it was
  built against, and an answer for every backlog item it owes one (B5/B7/B8/B9/B13/B14,
  each MECHANISED or DEFERRED *with its reason*). B7 and B9 and B13 are mechanised here; B8
  and B14 are mechanised in the half the gate needs and deferred in the half that is not the
  gate's; B5 is deferred because the board stays forge-agnostic — reading an ADO Feature's
  children belongs to the brain plane's work-item reader, and the dependency only ever
  points board → brain.

### Changed
- **A rejection now goes back down the rail the child can actually read.** `grade` sends its
  verdict to the child as a MEMO on the child's own task, naming the failed dimension, its
  grade, and the ungraded dimensions separately — because those two call for different work
  (a low grade is the child's to redo, an ungraded one is the judge's to finish). A park
  sends its reason and deliberately says nothing about another attempt. `--no-memo` opts
  out.

  **WHY A MEMO AND NOT THE CHANNEL.** A verdict recorded on the task and nowhere else is a
  verdict the child never reads: nobody types into an invoked child again, and by gate time
  it has usually stopped, so the channel has nothing to reach. A memo is durable, survives
  the window closing, and is on the record the child's own SessionStart reads — a retry
  therefore starts from the verdict instead of from nothing.

- **The report contract now NAMES the rail, because naming nothing made the compliant
  behaviour and the useless one identical.** The role contracts asked for a report and said
  nothing about where to put it, so a child that printed a perfect report to its own terminal
  was fully compliant and completely invisible: the parent cannot see that window, and the
  session ends. `invoke` appends the rail — `task-station memo send --task <ref> --text
  '<the report>'` — to every child's prompt, contract or no contract, and a missing report
  memo is now a gate FINDING rather than a silence somebody has to notice.

- **A routine notice no longer holds a turn hostage, and the discriminator is AUTHORSHIP
  rather than kind.** The control channel's Stop gate fires at every turn end, so an
  unsettled order costs a round trip every single time; holding an orchestrator's turn for
  "your child closed" — bookkeeping the loop minted itself, already durable on the memo
  ledger, already on the next prompt's rail — costs more than it delivers.

  So `memo_send(routine=True)` marks the memos a LIFECYCLE HOOK mints (a child reporting
  closed, a stand-down report handed back, a peer feed advancing, a link forming) and those
  ride the ledger: the Stop gate marks them delivered, settles them, and lets the turn end.
  A memo a SESSION WROTE still blocks, and that distinction is the whole fix — "stop
  rebasing, main moved" and "your child closed" are both memos and are not the same message.
  A **stand-down** and a **moved exit condition** block whoever wrote them; neither is
  bookkeeping, and DONE here is computed from those conditions.

- **`exit-add` now refuses to store a condition that cannot run or can be satisfied by
  something other than the work** (B7). Two checks, both STATIC — registering a condition
  must not have side effects — run before anything is written: the shell is asked to PARSE
  the command (`bash -n`, never execute), and the shape is linted for the three ways an
  assertion lies. `--force` registers anyway and prints what it overrode; a flagged
  condition stored silently is the one outcome nobody could debug.

  The three shapes, each observed on a real registered condition: a command ending in
  `tail -N` (one extra line of trailing stdout swallows the line the assertion is about,
  and the gate goes red for a reason having nothing to do with the work); a BARE COUNT as
  an expected substring (`5013` is inside `15013` and inside any line that happens to
  contain it); and an ABSENCE ASSERTION (`no failures`, `0 errors`) which nothing printed
  at all satisfies — so it passes hardest exactly when the command is broken.

### Fixed
- **Four ways the gate was reporting something other than the state of the work.** Each was
  measured while driving the loop by hand, and each now has a mechanism rather than a note:
  - a FALSE GREEN on unstarted work — a child that was never invoked is `gradeable: false`,
    because a grade on work nobody did is the cheapest possible lie;
  - a suite assertion satisfied by the ABSENCE of the test it protects — `unittest discover
    -k <a name nothing matches>` prints `Ran 0 tests`, then `OK`, and exits 0, and so does a
    renamed test class. `turn.suite_green` PINS A POSITIVE COUNT: `Ran N tests` with N ≥ 1,
    no `FAILED`, and an `OK`. Output with no count at all fails with its own reason —
    uncountable is never zero, and it is never green either;
  - a FALSE RED from a stale INSTALLED plugin — the suite exercises the repo while the hooks
    and the MCP server exercise whatever `/plugin update` last cached, and a gate reading the
    second while grading the first reports red about work that is correct. `turn` names it.
    (The probe compares versions NUMERICALLY: sixteen versions sit in a real plugin cache
    and `sorted()` puts 3.9.0 after 3.12.0 — a probe whose job is catching a stale install
    must not report the wrong version as the installed one);
  - **TREE, NOT ANCESTRY.** This repo squash-merges everything, so `git merge-base
    --is-ancestor` reports EVERY landed branch as unmerged. The failure direction is what
    makes it unacceptable in a driven turn: it re-opens work that already shipped. The probe
    is an empty `git diff <merge> <branch>`, and unmet conditions on a branch nobody has
    probed are reported as PRE-MERGE rather than as a failure.

- **A gate number with no command that measures it is now a finding** (B9). Phase 4's count
  went 58 → 81 in a plan nobody re-ran, and the drift was invisible because the number was
  prose. Identifiers that merely look numeric — `#444`, a year, a version part — are
  excluded: a lint that cried wolf on task refs would be switched off within a day, and a
  disabled lint still reads like a guarantee.

- **Pending-ack debt is now a loop INPUT rather than background noise** (B13). Twenty-two
  memos sat dispositioned by nobody on the day this was written, and the loop had no idea a
  fact handed to a task had never been engaged.

## [3.13.0] — 2026-08-19

### Fixed
- **A child that has stood down is no longer pointed back at its parent's task.** Closing a
  task DETACHES its session, so the child's very next prompt arrives unattached and meets
  the fold-don't-fork rule: fold into an existing task rather than fork a sibling, and —
  FOLD ON IDENTITY, NOT FLAVOR — when the prompt names a PR or work item, fold only into a
  task carrying that SAME key. The parent carries the child's PR as one of its own keys, so
  the PARENT was the identity match, listed first. The hook was routing every standing-down
  child straight onto its parent's ledger, by design.

  **Why it read as a one-off and is not.** Only one child was ever seen doing it, but the
  rule is unconditional — the other child of that wave simply never took a turn after its
  task closed. Human-driven, that gap is rare: a child finishes, goes quiet, the window
  closes. DRIVEN it is the standard case, because an orchestrator gates, grades, closes,
  and *then* messages the child in band — which is exactly the sequence that produced the
  four stray acks. The first driven run would have had every graded child writing to its
  parent's ledger, with nobody watching to notice that ledger had stopped having one owner.

  **The fix is a preference, not a mechanism.** `skip` already exists and is already the
  right answer for a session that has finished its task and is only speaking to hand back —
  what was missing is the guidance choosing it. So the parent of a task this session just
  closed is dropped from the fold candidates, and when that exclusion empties the list, the
  guidance prefers SKIP over both attaching and forking. The alternative — "a session whose
  task closed just stays unattached" — was REJECTED: it leaves the next prompt with no
  target at all, and the fold rule finds the parent again on the turn after that. Excluding
  the parent is what survives a driven loop.

  **Derived, not stored**: `task["sessions"]` already records every session that worked a
  task and `closed_ts` already records when it closed, so `stood_down_parents()` reads what
  close already wrote — no new state, no new lifecycle hook. It is latest-only, so once a
  session closes some later task, the older exclusion lapses. The guard holds on EVERY
  remaining turn, not just the first miss, because a standing-down child speaks for several
  (the incident was four acks); and it runs ahead of opt-in guaranteed-tracking, the one
  path that folds with no model in the loop. Precision is pinned: a non-parent task
  carrying the same key is still a fold target, another session sees the board unchanged,
  an OPEN child excludes nothing, and a keyed prompt matching nothing on the board still
  gets the ordinary create-bias.

## [3.12.0] — 2026-08-19

### Added
- **The role table is CONFIGURATION, and it now carries what a role is actually for.**
  3.2.0 shipped the four roles as data in `lib/loop.py` and taught `invoke --role` to read
  them. Data in a source file is not configuration: a station could not retune a role, and
  nobody could see what the roles were without reading Python. `roles()` is now the
  EFFECTIVE table — the shipped defaults with per-role, per-FIELD overrides from
  `config.json` merged over them — and it renders on the config board as `--roles`, model,
  permission mode, effort, grant and contract per role. A station can also declare a role
  of its own.

  **Per field, not per role**, because retuning one model must not mean restating the
  grant and the contract; a restatement drifts, and what it drifts on is a child's
  permissions. **And every override is validated**, for the same reason: a config table
  that cannot be checked is worse than a constant. A field name that does not exist, a
  permission mode or effort level Claude Code would reject, a grant that is not a list of
  tool names — each REFUSES the override whole (the shipped role stands, or a
  station-declared role is dropped) and each is REPORTED on the board. Half an override
  applied is the one outcome nobody could debug: `permision_mode: plan` would look
  applied and change nothing.

- **Each role carries a TOOL GRANT and a REPORT CONTRACT, and both reach the child.** The
  grant is a DENY list, not an allow list: `--tools` / `--allowed-tools` would REPLACE the
  human's tool set and drop the MCP servers they configured, while `--disallowed-tools`
  narrows it. That is 3.7.0's "a role may restrict and may never replace" rule — settled
  there for the permission mode — applied to the other flag a role sets, so a scout and a
  reviewer cannot edit while the implementer, which denies nothing, emits no flag at all.
  The contract is what the child owes back, appended to its prompt: a contract the child is
  never told about is decoration. The RECORDED ask stays the human's request, so
  boilerplate can never push the real one out of the event text. The role's `effort` is
  emitted too — the table has always carried it, `claude --effort` takes it, and a field
  the config board shows while nothing applies it would be a lie told on every render.
  `invoke --effort` overrides it, like every other role field.

- **`loop_children_max` is enforced at invoke time.** It was a config key nothing read,
  which is a comment with a default value. `invoke` now counts the orchestrator's children
  that hold a RUNNING session — process liveness, the same derivation the scan's RUNNING
  column uses, so a crashed child never spends a slot forever — and over the cap it
  refuses. **Exit 3, not 2**: 2 means "you asked wrong" and asking again will not help,
  3 means the budget is full and this is worth retrying when a child finishes. The refusal
  happens before a session is minted, an event written or a window opened, so it leaves
  nothing behind that looks invoked, and `--force` launches over it and records
  `FORCED over the cap` on the orchestrator — a deliberate override is sometimes right, an
  invisible one never is.

- **`loop_builds_max` is a real machine-wide lock.** A suite run IS a build, and the
  default of 1 is not timidity: this machine OOMs on concurrent builds, and this repo's
  load-dependent flakes made a parallel suite run a source of FALSE RED — a gate that goes
  red for a reason having nothing to do with the work is worse than no gate. So the lock
  lives in the DATA DIR, never on a task: two orchestrators share one machine, and a
  per-task cap would let them sum to a load neither one asked for. Its critical section is
  an `fcntl.flock` over a dedicated lockfile beside the slot file, the same shape
  `lib/delegate` already uses for its registry. `exit-tick` and `scan --run` — the two
  verbs that execute somebody's test command — take a slot and WAIT for it
  (`--build-wait`, default the exit-command timeout) before refusing, because a contended
  slot usually frees when the other suite finishes and a slower loop beats a red nobody
  caused. A refusal names the holder and exits 3: nothing ran, so nothing was refuted and
  no tick moved. **A holder whose process is gone is reclaimed** — a lock that survives a
  crash is a machine nobody can build on again.

### Changed
- **The role's grant, contract and effort are answered by `workspace.resolve_spawn`, not by
  the spawner.** 3.11.0 moved the model and the permission mode into one resolver because
  the two copies of that rule had already drifted, and the copy that drifted was handing
  unattended workers a mode that hangs them. The three fields this release adds are
  role-derived too, so they are answered in the same place rather than read from the table
  a second time inside `invoke` — a second reader is exactly how the first pair drifted.
  `resolve_spawn` now returns `effort`, `deny_tools` and `report` alongside the model and
  the mode, and takes `effort` as an explicit override like the other two.

  The bg path states a LIMIT rather than keeping a silent gap: it consumes the model and
  the mode and emits neither the grant nor the contract, because its live caller passes no
  role and because it is the one path already sending `--allowedTools`, where a deny list
  arriving beside an allow list raises a precedence question nobody has settled. That is
  written down in `delegate._build_worker_cmd` and pinned by a test, so the day it changes
  is a deliberate edit and not a surprise.
- **`loop.ROLES` is now `loop.ROLE_DEFAULTS`, and callers read `roles()`.** The rename is
  the point: a module-level dict named `ROLES` that is no longer the table the station
  runs is a trap, and the invoke refusal that lists the available roles has to list the
  EFFECTIVE ones or a station's own role would be invisible in the very message that says
  what the roles are.
- **A factory reset clears a retuned role table** (`roles` joins `RESET_KEYS`). A role left
  behind by a reset would keep deciding what every invoked child may DO on a station the
  user had just cleared back to defaults.
## [3.11.0] — 2026-08-19

### Added
- **Both spawn paths now resolve model, context window and permission mode through ONE
  function.** There were two spawners and two answers. `invoke` grew a considered rule in
  3.7.0 — a role may RESTRICT and may never REPLACE, so a mode that merely *replaces* the
  human's default is omitted instead; and a bare alias reclaims the parent's `[1m]` window,
  because handing a child one fifth of the context is a downgrade nobody asked for.
  `delegate` never heard about any of it and kept its own hardcoded pair from before that
  rule existed: `--permission-mode acceptEdits` and a bare `sonnet`.

  This is not a tidiness complaint. The copies had **already drifted**, and the copy that
  drifted was handing unattended workers `acceptEdits` — the exact mode `delegate`'s own
  `--bg` design had ruled out, because it auto-approves edits and then stops dead on the
  first non-edit prompt with nobody there to answer it. One rule in two places is a
  correctness bug with a clock on it.

  So the rule moves into `board.workspace.resolve_spawn`, beside the two halves 3.7.0
  already shipped there, and both paths ask it. It answers model, window and mode
  together, and reports in `notes` when it discarded what a role asked for — a design that
  silently throws away a role's stated mode is indistinguishable from a bug the first time
  somebody wonders why their scout was not in plan mode.

- **The `--bg` design WINS where it and the role table disagree, and the disagreement is
  now enumerated rather than accidental.** The ROLES table says an implementer runs
  `acceptEdits` and a scout runs `plan`. Both are right for a human-facing window and
  wrong for an unattended worker, because both end at a prompt: `acceptEdits` on the first
  non-edit tool, `plan` at ExitPlanMode. A bg spawn therefore runs `dontAsk` — fail-closed,
  so a non-allowlisted tool is DENIED rather than queued behind a prompt nobody will
  answer — and `bypassPermissions` only when the human turned it on once (carrying the
  disclaimer) **and** the target is inside a `-worktrees/` sandbox.

### Changed
- **A delegated worker no longer inherits the parent session's identity.** Measured
  2026-08-18: a window opened by the Apple Event inherits the parent's whole `CLAUDE_*`
  set. `CLAUDE_CODE_CHILD_SESSION` turns transcript saving **off**, and the parent's
  session id and messaging socket come along with it — so the child answers to the
  parent's identity, never appears in `sessions --task`, never appears in ListAgents, and
  the memo ledger is the only channel it has left. It fires only when Terminal.app is
  **cold** and the Apple Event is what launches it, which is why it stayed latent for
  anyone whose daily driver is iTerm.

  The fix is transport-shaped, because the leak is a property of the transport rather
  than of the command. `env=` on `subprocess.Popen` sets the environment of the
  `osascript` *process*, and Terminal.app is not that process — it receives an Apple
  Event — so for the window path the unset has to ride **inside** the `do script` string.
  A delegated worker is a direct child, where `env=` does reach it, so that path scrubs
  the mapping instead. One closed list, two transports, and the scrub sits at the window
  opener itself so a new caller cannot forget it.

  The list names only session **identity and transport**. `CLAUDE_CONFIG_DIR` is
  deliberately untouched — unsetting it would silently repoint the child at a different
  store, which is a worse bug than the one being fixed — and every other `CLAUDE_*` name
  observed in a live session is written down in the module with the reason it was left,
  so a later reader can tell "classified and excluded" from "never looked".

- **A delegated worker's `--allowedTools` now rides with `dontAsk` on the print-mode path
  too**, matching what `harness.ClaudeAdapter.spawn_cmd` already did under `--bg`.
  `dontAsk`, unlike `acceptEdits`, does not auto-approve edits, so the author-only toolset
  has to be granted by name or the worker cannot do the one job it has. git, network and
  arbitrary Bash stay absent and therefore denied.

### Fixed
- **`bypassPermissions` could be granted for a directory nobody named.** The worktree half
  of the gate resolved an empty path through `os.path.abspath("")`, which is the *process*
  working directory — so with the opt-in on, a spawn given no directory inherited the
  hub's own location, and the hub is usually itself inside a worktree. A gate with no
  input now fails closed.

## [3.9.0] — 2026-08-19

### Added
- **A parent can now REACH a running child, not merely observe one.** `invoke` starts
  one and `scan` watches one; neither could say a single word to one that was already
  going. A memo lands on the record and is surfaced by the prompt rail — which fires
  when somebody **types** — and an invoked child is handed exactly one prompt and then
  works. Nobody types again. So every mid-flight fact was undeliverable: main moved, the
  spec changed, stop and hand back what you have. The loop had no control plane at all,
  and the parent's only option was to sit still until the child stopped on its own.

  **The transport is the turn boundary**, because the end of a turn is the one moment a
  running session arrives at by itself with no human in the loop — and the Stop hook can
  refuse to let it pass. An order queued for a session is read at that session's next
  Stop, and the turn does not end until it is settled. This is a deliberately modest
  claim: the channel does not interrupt a turn in flight and does not pretend to. What it
  removes is the human from the delivery path, which was the whole of the gap.

  Four verbs (`task-station channel reach|orders|stand-down|settle|deny`), and three
  producers that now ride the rail automatically:

  - **A memo to a task with a live session is delivered.** A task nobody is working on
    queues nothing and behaves exactly as before — the memo is still a memo.
  - **A stand-down gets back what the child wrote.** Settling one *requires* `--report`,
    and the report goes back to whoever ordered it as a memo. A stand-down that needs
    nothing back is a kill wearing a politer name: it discards everything the child had
    not yet written down, which is the one thing standing it down exists to recover.
  - **A moved spec reaches the child.** DONE on this board is COMPUTED from the exit
    conditions, so editing them mid-flight moves the target under a working child — and
    the child cannot notice, because it read the checklist once at session start. The
    silence was on both sides: the parent thought it had retargeted the work, the child
    finished something that no longer counted.

  An order blocks at most three turn-ends. Past that it stays pending and fully visible
  (`channel orders`, every count, its settle command) and simply stops holding the turn
  hostage — the same anti-wedge rule the edit gate has always had, for the same reason: a
  gate that can trap a session is a gate people switch off. Both halves of the Stop gate
  now ride ONE block document when both fire, because the harness reads a single object
  from that hook and dropping one of two live reasons to fit the shape would silently
  lose whichever lost the coin toss.

- **Liveness now comes from process state, not from a hook having fired.** The existing
  answer joins a running session to its task through `store.links` — and a link is
  written by an **attach**. So the link-joined view is blind to any running session that
  has not attached yet, which is precisely the child a parent most needs to reach: the
  one sitting on a first-run dialog, the one three seconds old, the one whose link a
  later `detach` cleared. (3.7.0 named this symptom without fixing the derivation: a
  child at the trust dialog "waits *invisibly*".)

  So the channel's join runs the other way. The **task's own roster** (`session_meta`,
  which `invoke` writes *before* the child process exists) names the sessions that belong
  to it, and the harness's per-PID record says which of those are up — a live pid, plus
  the control socket the harness opened for that session. Neither needs a task-station
  hook to have run. The link store is kept as a SECOND source, never the only one, so a
  session that walked in and attached itself is still found: adding a source must never
  remove one. Every row says which one found it.

  **RUNNING and REACHABLE are reported separately.** Running means the process exists;
  reachable means it exists *and* its control socket is still there. Orders queue to
  everything running, because the Stop hook needs no socket — but a live session whose
  socket has gone is a different fact from a dead one, and `channel reach` says which.

- **The permission boundary is enforced at the channel, and this is the load-bearing
  part.** Permissions in Claude Code are per-session, and a control channel is exactly
  where that breaks: the moment a parent can send work to a child, a session denied an
  action has an obvious workaround — ask the child — and the natural failure mode of the
  whole loop is that privilege flows to whoever is least constrained. This is not
  hypothetical. On 2026-08-16 a child session's kill of seven runaway agents was refused
  by the permission classifier; it recognised that routing the kill through a peer would
  be LAUNDERING, and it stopped and asked the human. That was the right call, and it must
  not depend on each session making it.

  So: **a session that was DENIED an action may not ask a peer to perform it.** Refused
  at the channel, with a line naming the denial and what is actually available instead —
  never left to the receiver's conscience.

  Note what the rule is **not**. It is not "a restricted session may not order a wider
  one", which would refuse the review loop: a plan-mode reviewer handing findings to an
  implementer is the loop working, and that reviewer was never *denied* an edit — it was
  never granted one, by design. The trigger is a refusal that actually happened.

  `channel deny --action '<what was refused>'` is how a refusal becomes durable, because
  the harness's classifier refuses the SESSION and task-station cannot observe it.
  Self-reporting sounds weak until you notice what it buys: the record binds the session
  **and its task**, so one honest report binds every later session on that task,
  including the one that would have forgotten. It is a ratchet, not a cage. Matching is
  deliberately broad — normalized substring, or every token of the denied action present
  — and the asymmetry is the reason: a false refusal costs one printed line, after which
  the human does it themselves, which a refused action was always going to require; a
  false pass costs a laundered privilege, silently. Those are not the same size of
  mistake.

- **What the channel does NOT reach, said plainly.** The transport is the Stop hook, so
  it reaches anything whose Stop hook runs: an interactive session, and a child `invoke`
  opened. It does **not** reach a `delegate`-spawned worker, because `on_stop.sh` exits
  immediately when `TASK_STATION_SUPPRESS` is set — task tracking there is the hub's job,
  and that suppression predates this and is not quietly reversed by it. A memo to such a
  worker's task is still recorded and still surfaced to the hub. Reaching a suppressed
  worker needs its own decision about what a worker's Stop hook is allowed to do, and
  making that decision here would be smuggling it in.

  The gate also had to stay cheap: it runs on every turn end of every session, and
  answering "does this session have orders?" for a session with no link would otherwise
  mean reading every task in the store, every turn, on machines that never use the
  channel. So orders keep an addressee index (`<data>/channel-orders.json`) — a CACHE,
  never the truth: an unused channel costs one absent-file stat, an indexed hit costs one
  task load, and the full scan survives as the correctness backstop, gated on the index
  being non-empty so it can only ever cost anything where the channel is in use.

### Changed
- **A pending stand-down now silences 3.8.0's relay nudge, and that ordering is a ruling
  rather than a tidy-up.** Both fire at a turn end and both are legitimate, so the
  question is which authority wins. A stand-down is an ORDER FROM THE PARENT — external
  authority the child does not get to weigh against its own housekeeping. A relay is a
  SELF-assessment about context. If both speak and the relay proceeds, the child spawns a
  successor to carry on work the parent has just cancelled: not a confusing pair of
  messages but a child **disobeying a stop by proxy**, and burning a fresh full-window
  session to do it. So stand-down pending → the nudge is silent, no exceptions. The
  reverse needs no rule; a relay with nothing pending proceeds as normal.

  **Suppressed is not consumed.** The check returns before `pressure_nudged` is set, so
  the nudge is deferred rather than spent — it speaks on the next Stop once the order is
  settled. A suppression that quietly burned the one-shot flag would mean a session
  genuinely out of room never heard about it. Only a **stand-down** outranks the nudge; a
  memo order is information, not a stop, and suppressing on any pending order would let a
  stray memo mute a real relay. That predicate is also the one channel path that fails
  **closed** — everywhere else the cost of being wrong is an undelivered message, but here
  it is a child continuing cancelled work, and one silent turn is the cheap error.

  This also gave `succession_reserve` a second job nobody designed it for, recorded in
  `succession.py` where the next reader will meet it: a stand-down cannot be settled
  without a report, and a session at the relay trigger is by definition the one with least
  room to write one. The reserve being **absolute** rather than a percentage is what
  guarantees the same room to answer a stand-down on a 200k window as on a 1M one. The two
  mechanisms compose by accident, and the accident is load-bearing.

## [3.8.0] — 2026-08-19

### Added
- **`relay` — a long session hands itself off before it degrades, and the successor
  loses nothing.** What happens today when a session fills up is the harness's own
  auto-compaction: a model-authored summary nobody audited, landing when the window says
  so rather than when the work has a clean seam. That is a reasonable default for a chat
  and a poor one for a task with a durable, task-shaped record sitting right beside it.
  The 3.0.0 migration did the alternative by hand once per phase — stop deliberately,
  write the record properly, hand off to a fresh session carrying a self-written
  HANDOFF-PROMPT. This is that move made mechanical, and most of it is substrate that
  already existed: occupancy measured from the transcript's own `usage` block, a
  harness-authoritative 1M-aware window, the gap report and the mechanical cold-read, and
  `invoke`'s pre-attached session. Three things are new — the decision, the prompt, and
  the ledger the gate grades.

  **The policy is two numbers, and both of them are printed.** A TRIGGER, as a share of
  the window (`succession_pct`, default 65 — the same point the checkpoint nudge fires
  at, deliberately: the moment to write a real checkpoint is the moment to consider
  relaying, and two defaults for one threshold would be two answers to one question). And
  a RESERVE, in absolute tokens (`succession_reserve`, default 40,000): what the handoff
  sequence itself costs, since reconciling the record, checkpointing from full context,
  closing the gaps that names and generating the prompt is real work done inside the
  session that is running out of room. The reserve is absolute rather than a second
  percentage because that work costs what it costs — 20% of a 200k window is 4% of a 1M
  one, and a percentage would make the affordable band mean two different things on two
  models.

  So three verdicts: `keep-going` below the trigger, `relay` above it with the reserve
  intact, and `compact` above it with the reserve spent — a checkpoint written with no
  headroom left is thinner than the compaction it was meant to beat, so the honest answer
  there is to let the generic one land and take the seam on the far side of it. THE ORDER
  OF THE TWO TESTS IS THE WHOLE POLICY: the trigger is asked first and the reserve only
  afterwards, because reversed, any window smaller than the reserve would report
  `compact` from its very first token — a session at 12% told to compact.

  **And a fourth value that is not a decision.** `unknown` is what a session with no
  transcript, or no usage block in it yet, reports — and it is deliberately NOT
  `keep-going`. `keep-going` on an unmeasured session is indistinguishable from
  `keep-going` on a measured one with room to spare, so a caller cannot tell the policy
  never ran. `lib/exits.py` draws the same line between UNMET and UNKNOWN for the same
  reason: a check that did not execute has not passed. `--spawn` refuses on it.

  **Due and ready are separate facts, and are reported separately.** The verdict answers
  "should this session hand off". `ready` / `blockers` answer "can its record survive
  one" — every named slot filled, a state line leading with `NEXT:`, and a checkpoint
  both TAKEN and CURRENT, the last from the same `save.since_checkpoint` numbers the
  `[SAVE]` block already prints, so the two can never disagree about how much has landed
  since. A record with gaps does not make the relay less due; it makes it lossy. `--spawn`
  refuses until they close, and `--force` overrides — at 95% a degraded handoff beats no
  handoff — with the gaps then travelling INSIDE the continuation prompt, because a
  successor that cannot tell what it is missing is the one version of this that is worse
  than compacting.

  **Bare `relay` is the report, and the report costs nothing:** no session minted, no
  event recorded, no field touched. 3.7.0 had to add a `--dry-run` flag to give `invoke`
  that; here the preview is the DEFAULT and the flag is what opens a window, which is the
  right way round for a verb whose job is to end the session that typed it.

  **The continuation prompt is generated from the RECORD, never from the transcript** —
  structurally, not by convention: the generator takes a task dict and no session, so the
  predecessor's conversation is not reachable from it and the rule cannot rot. The
  successor's SessionStart already injects the task's digest, so the prompt carries the
  request only, which is the rule `invoke --ask` is built on. It is bounded to 1,600
  characters with every variable section capped, because an unbounded generator would
  reintroduce the context dump this design exists to remove.

  **The successor is attached to the SAME task.** That one line is the entire difference
  from `invoke`, which spawns a child onto a different record; everything else is
  deliberately the same substrate — the pre-bound session id, the workspace trust pass,
  the window opener, the MANUAL LAUNCH distinction — because a second spawner would be a
  second set of the bugs 3.7.0 has just finished fixing in the first one. It runs the
  predecessor's own model selection, `[1m]` marker and all: handing a successor a 200k
  window to finish work started in a 1M one is the same unasked-for downgrade `invoke`
  already refuses to make.

  **And the handoff is graded by the parent like any other child work.** A relay happens
  inside one task's life and creates no child, which is exactly why nothing about it had
  ever reached the gate — a thin handoff was invisible to every surface an orchestrator
  looks at. Each one is now a ledger entry carrying only measurements: who handed to
  whom, at what occupancy, against which window, under which verdict, and whether it was
  FORCED past a failed cold-read. It is graded through the same `grade` verb, the same
  six dimensions and the same per-dimension threshold, via `grade --handoff N`. The link
  is what makes the claim checkable: without it a task that relayed three times could not
  say which verdict judged which handoff, so the second would inherit the first one's
  grade for free. The evidence prints WITH the grade, so a forced handoff cannot be
  scored without being seen — and the engine deliberately does NOT auto-fail one, because
  that would be the engine making the judgement call the whole loop keeps on the other
  side of the line. The scan row carries the handoff count and how many are still
  ungraded, APPENDED rather than replacing the tail: owing the gate a verdict is
  orthogonal to being startable, and a tail showing one instead of the other would hide
  whichever it dropped.

### Changed
- **The proactive checkpoint nudge now names `relay` instead of describing it.** Its
  closing line already told you to "open a fresh session and `/todo <n>` to resume from
  the digest" — which is the manual version of exactly this command, minus the verdict,
  the readiness check and the generated prompt. It now points at `relay --task <n>`, so
  the moment the nudge fires is the moment the mechanism is reachable.

## [3.7.1] — 2026-08-16

### Fixed
- **Two surfaces computed "still blocked" independently, and only one of them was
  right.** `scan` gates on `loop.settled_fn` — closed, or every exit condition met,
  recursively over the subtree — so a finished child reads READY the moment its own
  work (and its children's) is done, whether or not anyone closes the record. The
  HTML board's `waits on` chip never asked that question at all: `_board_related`
  built the out side of a relation from the raw stored edge and nothing else, so a
  depends-on target read as blocking for as long as the edge existed — closed or
  not, settled or not. A predecessor could satisfy every exit condition an
  orchestrator will ever check, and the board would still swear it was in the way.

  `status == closed` is not the fix, because it is not the scan's actual question:
  a predecessor that is open with every exit condition met is the ordinary shape of
  a finished child here, and gating the chip on `status` alone would just rebuild
  the same divergence one level narrower. So `_board_related` now carries BOTH
  `settled` (the scan's own verdict, via a `loop.settled_fn(tasks)` built ONCE by
  `write_board` and threaded down — reused, never re-derived, which is what
  inherits the 3.4.0 rule that an orchestrator with an unbuilt child is not settled
  even once its own checklist is green) and `status` (so a closed target keeps its
  `✕`). Both are additive — a caller that never threads `is_settled` sees the exact
  same dict it always did.

  The `waits on` chip partitions on `settled` instead of listing every depends-on
  edge: an unsettled predecessor still reads `⇠ waits on #N`, title `must land
  first: #N`; a settled one moves to its own `⇠ gates met #N` chip (title `gates
  met: #N`, class `relgates`) so the gate stays visible — you can still see what
  satisfied it — without making a waiting claim nobody can act on. A settled
  predecessor that is also closed keeps its `✕`. A row with nothing left unsettled
  emits no `waits on` chip at all.

## [3.7.0] — 2026-08-16

### Added
- **`invoke --dry-run` — a preview that costs nothing.** It prints the command it
  *would* run and writes nothing at all: no session minted, no event on either
  task, no trust file touched, no window. The session id it shows is an all-zero
  placeholder, never a registered one. This is the flag that did not exist, and
  its absence is why previewing was being done with `--print-command`.

### Fixed
- **A launch now records which kind it was.** `--print-command` is a *real*
  launch — the human runs the printed line, so it legitimately pre-attaches the
  session — but it wrote the identical trail entry as a window launch. One child
  previewed once and launched once therefore read as **two invokes**, and the
  3.6.0 RUNNING column exists precisely to stop a double-invoke: it cannot do
  that on a log that already double-counts. A hand-off to a human is now a
  `MANUAL LAUNCH` on both tasks, and only a window that actually opened is an
  `invoked #…`. That includes the fallback path — a window opener that **fails**
  is a manual launch in every respect that matters, so the trail no longer claims
  a window nobody got. The events are also written *after* the launch decision
  rather than before it, which is what makes the record match what happened.

- **A fresh worktree no longer stalls the loop on first-run paperwork.** A
  worktree created moments ago is a stranger to Claude Code: the child opens onto
  the **trust dialog** and waits at a keystroke that is not a decision — and it
  waits *invisibly*, because liveness is derived from SessionStart and
  SessionStart has not fired yet. Clearing only that one stops again on the
  project-scoped **MCP approval** for the workspace's own `.mcp.json`, so both
  gates are cleared together, enumerated from the workspace itself rather than
  patched one at a time.

  **The guard matters more than the fix.** Pre-seeding trust for any `--cwd`
  would turn a safety prompt into a no-op for arbitrary directories — a security
  regression wearing a convenience costume. So trust is only ever **inherited**:
  a linked git worktree may be pre-trusted when its *own* main checkout is
  already a trusted project on this machine, and nothing else may be, ever.
  Known is not trusted; a main checkout has no parent to inherit from; a missing
  `~/.claude.json` is reported, never invented. Every refusal prints its reason
  and the invoke **continues** — the human answers one dialog, which is what a
  safety prompt is for.

- **`invoke`'s window opener is reachable by the test that stubs it — and the
  guard now says so.** `cmd_invoke` called `_open_jump_window(cmd)` as a bare
  module-local name (star-imported from `cmds/view.py`), while the suite patches
  the FACADE (`ts._open_jump_window`). The stub therefore never bound, and the
  routed-name guard in `tests/test_patch_surface.py` could not catch it because
  `cmds/loop.py` was missing from its `SEAM_FILES` list. This is the failure that
  list exists to prevent, in its own words: *"the test would pass while testing
  nothing."* It failed OPEN, into the real macOS window opener — so running the
  suite spawned live `claude` sessions into the developer's own worktree, each
  carrying the test fixture's ask. The call now goes through
  `g("_open_jump_window")`, and `cmds/loop.py` is in `SEAM_FILES`, so a future
  seam that reads a patchable name bare fails loudly here instead.

### Changed
- **A role may restrict and may never replace.** `--role` no longer emits
  `--permission-mode` unless that mode genuinely *narrows* what the child may do
  (`plan`, and only `plan` — a closed list, never a guess). `acceptEdits` is not
  a restriction of an `auto` default, it is a strictly less autonomous
  replacement of one, and no role should hand a child that unasked. Omitting the
  flag inherits the human's configured default for free. An explicit
  `--permission-mode` always wins, widening as well as narrowing.

- **A child no longer silently loses four fifths of its context.** Roles name
  models by bare alias (`opus`) so they follow the current generation rather than
  freezing one release — but a bare alias is also a 200k window, and Claude Code
  carries the 1M marker in the *selection* string (`opus[1m]`). An orchestrator
  running at 1M was handing its implementer a fifth of its own context. The
  marker is now inherited, and only the marker: same family, strictly larger
  window. A `sonnet` scout under an `opus[1m]` parent stays a 200k sonnet,
  because a window belongs to the model actually chosen and borrowing one across
  families would invent a variant that may not exist.

## [3.6.2] — 2026-08-16

### Fixed
- **An orchestrator can now finish.** It registers no exit conditions because it
  **holds no work**, and `exits.satisfied` correctly refuses to call an empty
  registration met — so requiring its own conditions left it permanently
  unsettled. A whole programme could complete and the node above it would still
  read unfinished, forever: **the loop could never terminate at any orchestrator
  node**, and nothing queued behind a finished track would ever be released.

  The children **are** the evidence. A task with children is settled when every
  child is settled and nothing of its OWN is outstanding — no condition refuted or
  unrun, no live step left both uncovered and unticked. A **leaf is unchanged**:
  the empty-registration rule still holds exactly where it was aimed, so a task
  with nothing checked and nothing underneath can only be finished by closing it.

## [3.6.1] — 2026-08-16

### Fixed
- **The proactive checkpoint nudge no longer nags when there is nothing to
  capture.** Its trigger is a context percentage, and a percentage only ever goes
  UP. The one-shot flag is cleared the moment the `/todo save` block is *read* —
  correctly, because the nudge was delivered — but reading a block does not lower
  the percentage, so a session that crossed the threshold and kept working
  re-armed and re-fired on **every** subsequent Stop. Observed three times running
  on one task whose own gap report said `+0 decisions, +0 steps, +0 log entries`
  since a checkpoint seconds old.

  The fix is on the FIRE side, not the clear side: the nudge now asks what the
  clear cannot — has anything accrued since the stamp? It reads the **same**
  accrual numbers the save block's gap report prints, so the nudge and the report
  can never disagree about whether there is work to capture. Never checkpointed at
  all still always fires (that is the case it exists for), and it **fails open** —
  an unreadable or baseline-less stamp speaks up rather than staying silent,
  because a missed checkpoint costs more than a redundant line.

  A nag that fires with nothing to do is the cry-wolf failure `heal` has already
  paid for four separate times, and it costs the same thing: the next real one
  gets skipped.

## [3.6.0] — 2026-08-16

Three of the four gaps that kept the loop from running on its own. What is left
after this is the turn itself.

### Added
- **The scan reports what is RUNNING, not only what is ready.** A node with a
  live Claude session attached reads `RUNNING` and is **excluded from `ready`** —
  because "ready" answers *what should I start*, and something already under way
  is not an answer to it. A planner that keeps offering work in flight is how the
  same child gets invoked twice. Liveness is process state (the derivation the
  HTML board already used), and it is **fail-open**: a sessions-dir hiccup
  degrades the scan to "nothing reported running" rather than refusing to answer.
- **A fifth stopping condition, `working`.** Without it, a wave whose children
  are *all running* and nothing else startable reported `blocked` — which reads
  as "somebody must intervene" when the honest answer is "the loop is working,
  wait". Telling those apart is the difference between a planner you trust and
  one you learn to ignore.
- **A child reports upward when it reaches a terminal state.** Closing it, or the
  `exit-tick` that crosses into fully-satisfied, writes one memo onto its parent —
  so the parent is told at the top of its next turn instead of learning only when
  somebody thought to run a scan. The channel is the existing memo ledger and its
  ack rail, not a new mechanism: nothing new to learn, nothing new to keep alive,
  and the notice is a durable record rather than a notification that scrolls away.
  Only on the **transition**, never on every green tick — a memo per run would
  train the reader to ignore the rail, which costs more than the signal is worth.
- **`decompose`** — split a task into children, parent them, optionally `--chain`
  them with `depends-on`, and flag the task orchestrator-only, in one command
  instead of four. Four commands is enough friction that the honest move loses to
  carrying on, and carrying on is how a flat list of steps drifts. A task that
  already has children is **refused** without `--add`: decomposing twice by
  accident is quiet, and surfaces only as duplicated work in the scan.
- **`too-large` joins the park reasons.** The gate is where the judgement already
  is — somebody is looking hard at the work at exactly that moment, which is
  cheaper and more accurate than a heuristic on effort or step count that fires on
  plenty of tasks that are merely detailed. Parking rather than rejecting is the
  point: iterating will not help, so the loop must stop asking.

## [3.5.0] — 2026-08-16

Opening an orchestrator described its goal and its own checklist, and said
nothing about its children's state. It had the answer and did not volunteer it.

### Added
- **The task detail reports the computed plan for any task that has children** —
  waves over `depends-on`, what is READY, what is blocking the rest, and which
  children register no exit condition and therefore cannot report themselves
  done. It sits directly under `State (next)`, because for an orchestrator the
  children *are* the outstanding work and its own steps are the part it already
  finished or handed away. One store scan, **no model call** — the same
  computation `scan` prints, rendered tighter. A task with no children renders
  exactly as before; a closed one is skipped, since its plan is history rather
  than a next step. The block is fail-open: a derived section must never be the
  reason a resume digest fails.

### Changed
- **The `judge` skill is now `grade`, and the `judge` role is `grader`** — the
  engine verb has always been `grade`, and one word for one thing beats two.
  `heal` and its subcommand already coexist this way.

## [3.4.0] — 2026-08-15

The loop went one level deep the first time a track was decomposed, and both
halves of "how deep does this go" turned out to be wrong.

### Fixed
- **A parent whose children are unbuilt is no longer "settled".** `settled()`
  asked only whether the task's *own* exit conditions were met — so a task that
  finished its five steps, retired the three that had become child tasks, and
  registered nothing further read as **satisfied while three children sat
  unbuilt**. It would have released every dependent wave on the strength of work
  it had handed to somebody else. That is the empty-registration failure one
  level up: a parent's own checklist stops being evidence the moment the work
  moves to its children. `settled_fn` now walks the subtree — own conditions met
  **and** every child settled, recursively. **Closing still wins outright**,
  because closing is a human's assertion and is allowed to end the argument, and
  a childless task is unchanged (an `all()` over no children is True), so this
  costs nothing on a flat board.
- **`scan` walks the whole subtree, not the child row.** An orchestrator whose
  child had itself become an orchestrator reported *that child* as the startable
  unit — when the thing anybody could actually pick up was two levels down. A
  scan stopping at depth one is the unread-Open-tail problem again: correct,
  current, and not where the work is. `--depth N` caps the walk; rows print their
  distance from the scanned root.
- **An orchestrator is never offered as READY.** It plans and grades and holds no
  work, so naming it as the next thing to start sent you to the one task that
  then *refuses* to do any — `delegate run` would reject it. A loop with no exit.

### Changed
- **Shipped files no longer cite documents the plugin does not ship.** The `judge`
  skill named a private note as its calibration set: a reader following the skill
  would look for a document that cannot be obtained, in the middle of a procedure
  whose whole point is that judgement must be calibrated. Worse than omitting the
  sentence, because it reads like a resource. Removed there and everywhere else
  it had crept in, along with private board numbers used as user-facing examples
  and a by-name attribution — README examples now use neutral task numbers, and
  the evidence behind each design note is kept while the unresolvable identifier
  is dropped ("measured on one real board, a parent sat between two of its own
  children").

### Added
- `tests/test_oss_fingerprints.py` — a guard so that class cannot recur: no
  shipped file may cite a document the plugin does not ship, and none may hardcode
  somebody's home directory. Deliberately narrow, and it says so: `CHANGELOG.md`'s
  `task #N` provenance refs are this repo's own convention, test fixtures are data
  rather than references, and the demo peers are named fake people on purpose.
  **It carries no organisation-identifier rule, and that omission is the point** —
  that class belongs to a pre-push hook which lives in `.git/hooks` and never in
  the tree, because the pattern list *is itself* the fingerprint. Restating those
  patterns here would publish the thing they exist to keep out, and would not even
  work: the hook scans every pushed blob including this one, so a literal list
  blocks its own push. Assembling the needles from fragments to slip past it is an
  exemption in disguise, and exempting the scanner is how a guard quietly stops
  guarding. The hook owns the identifiers; this file owns what is safe to state in
  the open.

## [3.3.0] — 2026-08-15

The board could not show you that one task owned another. Measured on a real
board: an orchestrator sat **between two of its own children**, with unrelated
tasks interleaved among the rest, and the only marker that any of them were
related read `↳ from #N`.

### Added
- **Families nest.** A task with a `parent` edge renders directly under its
  parent, indented, with a connector column (`│  └─ `). The family is placed by
  its **most recent member**, so recency still orders the board — a family at a
  time, rather than a child leaping to the top alone and leaving its parent
  behind. Positions, not timestamps: the row list is already activity-sorted, so
  a member's index *is* its recency and nothing here parses a date, which is what
  stops the two orderings from ever disagreeing.
- **A `group families` toggle** in the filter bar switches back to strict
  activity order and persists the choice (`ts-board-nest`), like the theme. One
  render serves both: every row carries `data-nest` and `data-flat`, so the
  switch re-sorts DOM that is already there.

### Fixed
- **Relation chips said the same four words for every edge kind.** `↳ from #N`
  was printed for *parent*, *depends-on*, *spawned-from* and *related* alike, so
  one task read `↳ from #a, #b` where #a merely **gated** it and #b **owned** it —
  two entirely different relationships, rendered identically. Another read
  `↳ from #c` and looked like a child while being a **dependent**. Each kind now gets its own word: `⤶ N children` ·
  `⤷ parent #N` · `⇠ waits on #N`, with the generic `↳ from #N` kept for any
  kind the table does not know, so a store written by a newer version still
  renders.
- **A parent advertised nothing at all.** The chip read only the *outgoing* side
  of the relation, so a task with six children showed no sign of having any. It
  now reports them (listing the seqs for a run of three or fewer, a count plus a
  full tooltip beyond that).

### Changed
- `BOARD-BEHAVIOR.md` gains **B20**, including the three things the layout pass
  refuses to do: follow a parent edge into a cycle (single-valued edges *should*
  make that impossible — that is not a rendering guarantee, and the failure mode
  is the whole board rather than one row), reach outside the section it is laying
  out (a closed parent leaves its open child a root, keeping the `parent #N`
  chip), and move a task that has no family.

## [3.2.1] — 2026-08-15

### Fixed
- **Partial instrumentation no longer buys a green.** A task is exit-satisfied
  only when every registered condition is met **and** no live step is left both
  uncovered and unticked. Before this, a task with eight steps could register
  one condition, pass it, and report itself finished — releasing every dependent
  wave on the strength of an eighth of its plan. It is the empty-registration
  rule in weaker form, and the same reasoning: the incentive must never be to
  instrument the easy step and stop. An uncovered step that is **ticked** is
  tolerated, because refusing to proceed past a hand-ticked one would make the
  whole feature unadoptable on any plan that predates it; the rule bites on what
  is genuinely unanswered. `exit-show` names the uncovered steps and `scan`
  marks them `+N uncovered`.

## [3.2.0] — 2026-08-15

A plan's items used to *assert* they were done. They can now **prove** it — and
once items settle themselves, "what can start next" becomes a computation rather
than a list somebody maintains.

The evidence this is built on came from one document that carried both kinds of
statement at once. Its seventeen registered **claims** stayed honest for a year,
because something *ran* them. Its prose **steps** drifted: thirteen silently
became true and nobody noticed for weeks. `heal` cannot close that gap — it
reconciles the record against itself, never against reality.

### Added
- **Exit conditions on a checklist step** — `exit-add` / `exit-rm` /
  `exit-show` / `exit-tick`. A step carries the shell command that settles it
  plus every substring that must appear in the output, in the shape `claims`
  already uses. `exit-tick` **runs** them and ticks what passed, so DONE is
  computed rather than asserted, and it **exits 1 when anything is not met** so
  it can gate a release rather than only inform a reader. Three rules make that
  safe: a condition with no `--expect` is refused (it would pass forever
  whatever the command printed); a condition that did **not run** — timeout or
  launch failure — is `unknown`, refutes nothing, and moves no tick in either
  direction; and a failing condition on already-ticked work is reported as a
  **regression** and left alone unless `--untick` is passed, because a real
  regression and a moved file present identically.
- **`scan` — the zero-token wave planner.** Computes waves over the
  `depends-on` edges that shipped in 2.24.0 and that nothing had ever computed
  over, rolls up each node's exit conditions, and prints the stopping
  condition (`ready` · `complete` · `blocked` · `empty` — four values, because
  "nothing to do" and "nothing I *can* do" are opposite situations). Calls no
  model, and without `--run` no shell either: it reads the verdicts `exit-tick`
  stored. A predecessor releases its dependents when it is **closed** or when
  **every exit condition it registered is met** — and a task registering *none*
  is never settled, so an empty checklist cannot release work by having checked
  nothing. Cycles are reported, never traversed; a dependency on a deleted task
  does not deadlock but is named. `--json` emits the same object the text view
  renders.
- **`invoke` — spawn a child pre-attached to its own task.** The child's
  session is linked before it launches, so SessionStart injects *that task's*
  digest and the ask carries the **request only**. This removes the lossy-brief
  boundary by construction rather than by discipline — there is no brief to get
  wrong. `--role` (scout · implementer · reviewer · judge) sets the child's
  model and permission mode from a small role table; models are named by alias,
  never by a pinned id.
- **`grade` + the `judge` skill — the graded acceptance gate.** One pass scores
  six rubric dimensions and accepts only at `A-` on **every** one: acceptance is
  per-dimension, never an average, because an average lets a failed
  gate-integrity dimension hide behind five strong ones. A dimension nobody
  graded is not a pass either. Exit codes let a driver branch — `0` accepted ·
  `1` rejected with retries left · `3` retry budget spent · `4` parked · `2` bad
  command — and a **parked child is never retried**, which is how a human gate
  halts the loop cleanly instead of being re-asked with a better prompt. The
  engine owns the arithmetic and the recording; the skill owns the judgment and
  runs the mechanical gate *first*, because a report is not evidence.
- **The orchestrator flag** — `update --task <n> --orchestrator on`. A task
  flagged orchestrator-only plans and grades; it does not hold work.
  `delegate run --seq <it>` now **refuses and names the ready child** that
  should own the work, with the exact command to run there. Explicit rather
  than inferred from "has children": plenty of parents legitimately hold their
  own work, and a guard that fires on every parent is switched off within a day
  — while a disabled guard still reads like a guarantee. `delegate run --force`
  overrides it deliberately and writes the override onto the task.
- Five config keys, all in `RESET_KEYS`: `exit_command_timeout` (120s — a fifth
  of the claim timeout, since a claim runs a suite and an exit condition settles
  one item), `loop_accept_threshold` (`A-`), `loop_retry_max` (2),
  `loop_children_max` (3) and `loop_builds_max` (1, **machine-wide** — two
  orchestrators must share it, because this machine OOMs on concurrent builds).

### Changed
- `guidance` (the model-facing command reference) documents all seven new
  subcommands and the `--orchestrator` flag.
- `checker` exposes its command runner and output-tail helpers under public
  names, so exit conditions share one definition of what `ran` / `timeout` /
  `error` mean instead of growing a second copy that could drift.

## [3.1.2] — 2026-08-15

`brain-init` is documented as safe, idempotent and reversible. On any install
whose layout is not the stock one, it was none of the three. Both faults below
share one cause: it read the PRIMARY config file — which is a one-line pointer —
and never the config at the far end of it.

### Fixed
- **Re-running `/brain-init` no longer destroys a customised config.** It
  rebuilt from stock defaults every time, silently repointing `vault`, `memory`
  and `org_brain_clone` at the stock paths and dropping any key it does not know
  about (`publish_mirror`, `tasks_db`, …) — while backing up only the pointer,
  the one file that is trivially reconstructible. Precedence is now defaults →
  existing config → OrgProfile, so your own values always survive a re-run, and
  the config is backed up to `config.json.bak` before any rewrite (a run that
  would change nothing now writes nothing).
- **The team-rules `@import` follows the configured org-brain clone.** It was a
  hardcoded stock path, so any install with a clone elsewhere got an `@import`
  of a file that does not exist — and an unresolvable `@import` is inert, so the
  team rules simply never loaded and nothing reported it. The path is now derived
  from `org_brain_clone`, the same key every other reader in the brain plane
  uses, and a block left pointing somewhere stale is repointed rather than
  skipped (surrounding CLAUDE.md content is preserved byte-for-byte; a block
  missing its end marker is left alone).

## [3.1.1] — 2026-08-14

Packaging only — no behaviour change over 3.1.0's intent.

### Fixed
- Re-publish so the project-MCP pre-approval actually ships. 3.1.0's artifact was
  cut one commit early, and because the follow-up commit kept the same version
  string the marketplace refresh had nothing new to pull — the published tree was
  missing `approve_project_mcp` while `main` had it. Caught by scanning the built
  artifact rather than trusting the publish.

## [3.1.0] — 2026-08-14

Worker truth: delegate now judges background workers by what the harness actually
reports, preflight-repairs what silently parks them, and never loses their report.

### Added
- `delegate reap-parked` — sweep task-station background agents parked in a stalled
  agents state (blocked / stalled / needs-input): remove the supervisor's
  session-store file (legacy flat layout and the nested `claude-code-sessions`
  store, matched on `cliSessionId`), kill the pty-host process group resolved from
  `ps` (background rows carry no pid), and flip the harness job record
  (`<config>/jobs/<short-sid>/state.json`) to `done` — the file the agents list
  actually renders. `--min-age-mins` (default 360), `--dry-run`, `--all-names`.
- `delegate grants [--worktree] [--json]` — probe the trust state and the merged
  `permissions.allow`/`deny` a worker in a repo/worktree will actually get
  (user + project + local settings), so a brief can carry the real toolset
  instead of a guess.
- Trust + grant preflight before **every** launch: verify/repair the
  `~/.claude.json` trust entry (an untrusted dir under `--bg` parks the worker in
  `blocked` with no prompt anywhere), alert when a previously-verified entry has
  been wiped, and print the probed grant set once per worker slot.
- Project-MCP pre-approval in the same preflight: a repo carrying its own
  `.mcp.json` puts a headless worker in front of an *"approve 1 new project MCP
  server — attach to respond"* dialog it can never answer, so it parks in
  `blocked` forever. Spawning a worker in a repo is the approval intent, so
  delegate writes `enableAllProjectMcpServers` into that tree's
  `.claude/settings.local.json` — and leaves an existing value of its own alone,
  in either direction.
- Durable child report: every brief is contracted to end by writing
  `HANDOFF-REPORT-<slug>.md` at the worktree root (fixed sections, mandatory
  *Unverified* list); when the worker doesn't, delegate harvests its final
  message — stdout result, else the job record's `output.result`, else the
  transcript tail — into the same file, so backgrounding can never lose the
  report. The path lands in the registry, `status`, and the run footer.
- `--stall-grace <secs>` on `run` (default 45): how long a `--bg` worker may sit
  in a parked agents state before delegate fails fast with the diagnosis line
  (agents state, transcript existence, and the job record's `needs`/`detail`).

### Fixed
- Lying liveness for background workers: the agents list emits two row shapes —
  interactive rows carry `status`+`pid`, `kind: background` rows carry `state`
  and no pid — and only `status` was read, so every parked bg worker rendered as
  `running` while the poll loop refreshed its own heartbeat (the 3h × 5-launch
  tax). Both keys are read now; the heartbeat only advances on progress states;
  a parked state is classified `stalled` and *said*; `status` re-probes the live
  agents state for background entries instead of trusting pid or a poll-touched
  timestamp.
- The task-close reaper skipped any worker without a pid ("Claude Code prunes
  it" — refuted: 40 pid-less parked agents had accumulated, oldest 16 days). It
  now reads both row shapes and reaps via store-file removal + job-record flip,
  killing a process group only when one exists.

## [3.0.0] — 2026-08-14

Task Station becomes **one plugin carrying two planes**. The task board — the episodic
plane, the record of *work* — is joined by **brain-station**, the knowledge plane: a
personal Markdown wiki (the **private brain**) with search, capture, self-healing, an
optional PR-gated **org brain** tier, and hook-driven context injection. One install, one
version stream, one MCP server, one hook manifest. The board behaves exactly as before
whether or not a brain is configured — every brain surface stays silent until one exists.

### Added
- **The knowledge plane (`lib/brain/`).** A vault of atomic Markdown notes with
  frontmatter (`name`/`description`/`type`/`scope`/`verified`), scaffolded on first run
  from the bundled `lib/brain/vault-scaffold/`. Naming is owned by one module
  (`lib/brain/naming.py`, contract data in `lib/brain/data/naming-contract.json`,
  spec in [docs/brain-naming.md](docs/brain-naming.md)); notes, references and search
  ride the same stdlib-only, no-pip rule as the board. The plane includes: episodic
  **ingest/distill** (the board's own store is the brain's episodic source), a
  three-part **heal** pass (tier re-filing, lint, daily dirty-gated cadence),
  **promote/publish** to an org tier, **org-pull/peers** federation, and an
  Azure DevOps work-item reader (`python3 -m brain.ado_tree <id>`) so briefs can read a
  work-item tree in one zero-token call. Configuration resolves
  `~/.claude/brain-station.json` → `~/brains/config.json`, with per-key
  `TASK_STATION_BRAIN_*` environment overrides; org values (labels, keywords, forge
  coordinates) arrive at runtime from an org profile (`-m brain.init_home --profile`),
  never from code.
- **Six new skills** — `/brain` (query with citations), `/brain-save` (capture),
  `/brain-heal` (self-healing pass), `/brain-init` (first-run scaffold, idempotent and
  reversible), `/brain-promote` (note → org-brain PR), `/ado` (work-item tree reader) —
  plus `system-instructions.md` at the plugin root, the natural-language routing doc
  that maps plain phrases ("search the brain for X", "save this to the brain") to the
  right skill.
- **Five brain MCP tools on the same bridge** — `brain_search`, `brain_status`,
  `brain_save`, `brain_log`, `brain_recent_tasks`. The Desktop bridge now advertises
  **16 tools** (11 board + 5 brain); brain tools mount lazily, and any brain-side
  failure leaves the board serving alone. A new **`.mcp.json`** registers the same
  server for Claude Code sessions, so CLI and Desktop share one server and one store.
- **The hook mux (`lib/hookmux.py`).** Both planes want the same three events, and the
  harness runs same-event hooks in parallel — two JSON docs on one stdout don't merge.
  The manifest therefore registers **one command per shared event**
  (SessionStart / UserPromptSubmit / Stop) and the mux runs the children **in order**
  (board first — tint and attach resolve before context injection), fans the same stdin
  to each, concatenates `additionalContext`, resolves other keys first-writer-wins, and
  always exits 0 — a failing child leaves a stderr breadcrumb and never breaks the
  session.
- **PreToolUse(Bash) secret guard.** The brain plane's guard denies a Bash command that
  would write a secret into the transcript (an opaque token as a literal flag value, or
  a secret-reading command whose output is neither suppressed nor captured). Brain-only
  event, registered directly (no mux), and it **fails open** — any parse or logic error
  allows the command.

### Changed
- **Self-identity now comes from runtime config, never from code.** The feeds/graph
  plane resolves *who you are* at call time: `TASK_STATION_SELF_ALIAS` env var, else
  `self_alias` in the station's `config.json`, else the OS username. Previous releases
  shipped a hardcoded default alias; if your board showed it, set
  `"self_alias": "<your-handle>"` in the station config (the same `config.json` the
  rest of the board reads). The identity-keyed colour entry is replaced by a
  `SELF_COLOR` default; demo peers keep their colours.
- **Internal layout: the engine is now packages.** `lib/` is split into `lib/core/`
  (shared plumbing), `lib/board/` (the engine in ten seam modules plus the moved flat
  modules), and `lib/brain/` (the knowledge plane) behind the same `lib/task-station.py`
  facade. Every entry point (`/todo` and friends, the `task-station` CLI, hooks, the
  Desktop bridge), the on-disk data formats, and the store are **unchanged**; moved
  modules keep import-compatible shims. If you scripted against `lib/` internals,
  the flat module names still resolve.

## [2.27.0] — 2026-08-12

The hook surface goes from **5 wired events to 8, plus one installable**. Every one of
them answers a question the station could previously only guess at — what happened at the
*end* of a session, whether a config still points at things that exist, whether the
checker's cached answer is still about the current config, and what a brand-new worktree
is missing.

### Added
- **SessionEnd — the EXACT reaper (`session-end`).** A session ending cleanly knows its
  own id, so it no longer has to be discovered as an orphan at the START of the next
  session: it stamps its roster row with `ended_ts` + `end_reason` (additive fields), puts
  one `session-end` event on the attached task's feed, and stops the delegate workers
  **it** spawned — through the same airtight predicate the close path uses (registry-
  registered for that seq, `role == worker` in the roster, task-station-named, not busy,
  not itself). **This amends decision 36's W2**, which was taken when SessionEnd could not
  be relied on: the SessionStart orphan sweep is **untouched** and stays as the crash
  backstop, because SessionEnd is not guaranteed to fire on a crash or a kill. The pass is
  idempotent, always exits 0, and does **not** bump `updated_ts` — a session ending is not
  work on the task, and letting /clear reorder the board would make "recently updated"
  mean "recently closed a terminal". All SessionEnd hooks share a **1.5-second** budget,
  so the manifest entry carries `"timeout": 10` as a ceiling, the store work runs
  unconditionally, and a subprocess is spent only when the registry says this session
  actually spawned a worker (bounded to 5s, against harness's own 20s).
- **ConfigChange — the config-path validator (`config-change`), WARN by default.** Before
  a settings change takes effect, the paths it declares are checked for still resolving —
  a `statusLine.command` naming a script that moved, a hook command pointing into a
  plugin-cache dir a `/plugin update` replaced. None of those fail loudly; the feature
  just silently stops working. Default is one hook-health record and exit 0, so the next
  session start names the bad path. `--config-change-enforce on` turns it into a **block**
  (exit 2) — and the record is written **first** either way, because a blocked config
  change surfaces **no transcript message at all** and that record is the only trace.
  Wired for `user_settings|project_settings|local_settings` only: `policy_settings` cannot
  be blocked, so wiring it would only be a way to look like we might.
  **What counts as a path is deliberately narrow** — an absolute or `~/` value, or the
  first such token of a `…command` string (so `bash /abs/host.sh` is checked at the
  script, not at `bash`). A relative path, a `$VAR`, a glob, and a bare command name are
  each excluded, because each is a class of false positive, and a check that cries wolf is
  worse than no check. It **never** blocks on a file we cannot parse.
- **FileChanged — the pointer/drift re-arm (`file-changed`).** The checker's nags are
  self-capping: each stays silent until the state it fingerprinted changes. When the
  station config they were evaluated against changes on disk (external editors included),
  that cap is guarding a stale answer — so the task's checker gate is dropped and both
  nags re-evaluate at the next session start. There is nothing to say to the model here
  (FileChanged cannot inject context), so the hook prints nothing: **the re-armed gate is
  the mechanism.** The manifest matcher is a **literal filename list**
  (`config.json|categories.json|repos.json|brains.json|workers.json`) — a hyphen, space or
  comma anywhere in it silently flips Claude Code to regex parsing, after which the hook
  stops firing and nothing reports it. It is basename-level, so every project's
  `config.json` reaches the hook and the engine filters on the full path being inside the
  data dir.
- **WorktreeCreate — the provisioner, as an OPT-IN INSTALLER (`config --worktree-hook
  on|off`, default off).** A new worktree inherits none of the main checkout's local
  setup, and two consequences bite silently: `.claude/settings.local.json` is gitignored,
  so a delegated worker hits "tool not granted" on grants the main checkout already made
  and cannot prompt for them; and the path has no trust entry, so a background session
  stops on a dialog it cannot answer. The hook creates the worktree itself (`git worktree
  add` — local branch → checkout, `origin/<branch>` → tracking branch, else a new branch
  from `base_ref`/`HEAD`, and **never a fetch**), prints its absolute path as the first
  stdout line, then provisions best-effort. **It is not in the plugin manifest and never
  will be**: a WorktreeCreate hook REPLACES creation, so a bug in it breaks every worktree
  on the machine, including Claude's own subagent isolation. `on` writes exactly one entry
  into your own `settings.json` (at the stable engine path, not the versioned plugin
  cache); `off` removes exactly that entry; a foreign WorktreeCreate entry is a refusal,
  because two hooks racing to create a worktree and print a path is not a composition.
  The only non-zero exit is a genuine creation failure — no provisioning step can change
  the exit code or touch stdout.

### Changed
- **Hook-health exit code 0 now means INFORMATIONAL.** `file-changed` and
  `worktree-create` record what they *did*, and `hook_health.nag()` skips code-0 records
  so a routine config edit can never announce itself at the next session start as
  "N hook failure(s)". They still appear in the log and in `task-station hook-health` —
  the full record, not the alarm.

## [2.26.0] — 2026-08-12

Six changes to `heal`, all of them paid for by measurements on one real task: a scan
standing at **17 findings** of which **8 were false positives of a single shape** and **9
were dead paths a human had already ruled on**. A report that is half wrong and half
already-answered is one nobody opens, which makes every finding in it worthless — the
cry-wolf failure this subsystem has now fixed five times.

### Added
- **A dismissal ledger — `heal --dismiss` / `--undismiss` / `--dismissals`.** A finding you
  have read and judged wrong can be adjudicated away instead of reappearing every pass. The
  additive `heal_dismissals` field records the check, the ref, a **mandatory `--why`**, the
  moment and the session. A dismissed finding leaves the findings, the issue count and the
  due calculus entirely; one informational `Dismissed N` line says how many are silenced,
  because a silenced finding must never become an invisible one. The fingerprint covers the
  finding's **matched text**, so a ruling adjudicates one exact state and never a category:
  edit the entry it names and the finding **re-reports**. Nothing is deleted —
  `--undismiss` marks the entry retired and the listing flags a ruling whose text has since
  changed as EXPIRED. A dismissal is its own invocation and **never stamps a heal**:
  adjudicating a false positive is not reconciling a task.
- **Merge at scale.** The shape tier knew neither whether the work was *finished* nor what
  the entries were *about*. Four additions, all proposal-tier except the last:
  - **A completion signal.** A decision whose subject **steps** are all ticked or superseded
    is tagged `completed-subject` — the checklist itself saying the work it records is done.
    Step references are read in explicit form only (`step 29`, `steps 3-6`, `steps 3, 4 and
    5`); a bare number is never one, because a decision's prose is full of bare numbers.
  - **Subject grouping**, ahead of the shape tier: overlapping step references, a shared
    release version, a shared PR/story number. Transitive, so one subject is one group.
    **Two** members are enough where the shape tier needs three — a shared subject is direct
    evidence, a shared opening phrase is not. The shape tier stays as the secondary one; it
    catches the process-error and scrub-iteration families that name no subject at all.
  - **A size objective.** The heal stamp additionally snapshots the digest's decision-char
    total (additive `chars_at_last_heal`), and the scan reports `chars now · at last heal ·
    delta`. A char total with nothing to compare it against cannot tell a digest 40k down
    from last week from one 40k up, and down is the whole point of the pass.
  - **`heal --candidates`** — the cheap read: the goal line, the pinned decisions and each
    candidate group's members **in full**, and nothing else. The full dry run is ~47,000
    chars on a real task and 94% of it is the corpus.
- **The first OUTWARD check — cited commits.** Every other check cross-references the record
  with itself, so a rebase or force-push that erased a cited commit left nothing to find.
  Commit shas the record **declares** (`commit <sha>`, `merged <sha>`, `main @ <sha>`) are
  probed against the task's own repos with `git cat-file -e <sha>^{commit}`. **Bare hex is
  never matched** — a task id, a memo id8 and a heal fingerprint are all hex — and a
  seven-letter hex-only English word (`defaced`, `acceded`) is excluded by a digit gate.
  The prober is **injected** exactly like `branch_prober`, so the SessionStart path still
  spawns no subprocess, and UNKNOWN is never reported.
- **`heal --scan --probe-links`** — the opt-in HTTP HEAD for stored PR/story links, wiring
  the probe seam `link_states` has always accepted and never been given. Off by default; only
  an explicit **404/410** counts as dead, so a private ADO PR answering 401 stays UNKNOWN.
- **`heal --goal-reviewed`** and a fifth `due()` limb. Past `heal_goal_review_due` decisions
  since the goal line was last written **or re-read**, a heal is due, worded as the count.
  Re-reading is the service, so the new verb records the re-read and resets the count
  **without rewriting a sentence that is still true** — and `--mark-healed` deliberately
  does not reset it, because "I read the record" is not "I ruled on this line". The goal
  review remains a **proposal**: never an issue, and its row now says exactly that instead
  of the claim it can never make a heal due.
- **One config threshold** — `heal_goal_review_due` (25), with a
  `TASK_STATION_HEAL_GOAL_REVIEW_DUE` env escape. Positive-only, like the checker's three: a
  zero would put every task carrying a goal permanently into the nag.

### Changed
- **The declare-vs-describe guard gained a third discriminator: REPORTS-ANOTHER-DECISION.**
  The guard reads the word standing in *front* of a match, which cannot answer who the
  sentence says *did* it — so `corrected by decision 184` (the reference is the agent),
  `decision 173 investigated` (the subject of a reporting verb) and `why decision 150 is NOT
  superseded` (an outright denial) all read as unlinked supersessions. Those were **8 of one
  task's 17 findings**. Three readings, scoped to the clause, each a vocabulary brought to the
  existing reader rather than a fourth heuristic. A form of *to be* after the reference is
  deliberately not a reporting verb: `decision 4 was wrong` is the finding worth having.
- **The oversized threshold now derives from the write advisory.** It was a flat 4,000 — 2.4×
  an advisory it never referenced, on a task whose decisions *average* ~1,400 chars, so it
  reported clean on almost everything. Both tiers now come from
  `decisions.LONG_DECISION_CHARS` (600): **>2×** is a proposal, worst-first, capped at five
  with a `+N more`, never an issue and never due-making; **>6×** is a finding, which is where
  an entry stops being supersedable a piece at a time. Only the finding tier is ever planned
  as a `--split`.
- **`grew-with-candidates-outstanding` is a finding** — the digest is larger than at the last
  heal *and* at least one merge candidate group is outstanding. Neither half is a defect
  alone; together they say the record is getting more expensive to brief in exactly the place
  a named verb was waiting. It needs a baseline, so a never-healed task is silent.

## [2.25.0] — 2026-08-12

### Added
- **The checker — two cheap SessionStart checks (`lib/checker.py`).** Every check in
  `heal` works by cross-referencing two things a task already holds, which leaves one
  structural blind spot: a goal condition **nobody has worked on** contradicts nothing, so
  there is no inconsistency to find. Measured on one real task, a release condition sat
  with nothing completed against it for **fifteen days** while every surface reported the
  task healthy. Two checks close that, both at session start, both silent when healthy:
  - **Goal drift.** A goal line may carry numbered DONE conditions (`DONE = (1) …; (2) …`).
    Completed checklist steps are attributed to those conditions by word overlap
    (`heal.word_overlap`, the same tokenizer), and a condition nothing has completed
    against for 3 days is reported, 7 days escalated. The `(n)` marker is a **hard
    structure gate** — no marker means total silence, because a condition guessed out of
    free prose would drift on a schedule of its own.
  - **The local pointer check.** Every recorded path, symlink target, linked-worktree
    `gitdir:`/`HEAD`/**checked-out branch**, and bound claims document still resolves.
    Plain `stat` and file reads — **no git subprocess**, so `packed-refs` and `commondir`
    are parsed by hand rather than asked about.

  Both nags are **self-capping** (own gate dir, `<data_dir>/checker/`, fingerprinted like
  `heal._signature`) and **fail open** at the check and again at the call site. The
  unattached session-start listing runs goal drift across ACTIVE tasks and names the single
  worst offender — the half that catches a plan nobody has opened.
- **`claims` — a plan that can be checked instead of believed.** `task-station claims`
  binds a document to a task and registers the shell commands that settle what it asserts
  (`--register 'C1|<cmd>|<expected substring>…'`, upsert by id, `\|` for a literal pipe).
  `claims verify` runs them, stores a bounded output tail, and **exits non-zero** when a
  claim fails, so it can gate a step rather than only inform a reader. A registration with
  no expected substring is refused — it would pass forever while proving nothing. Claims
  are **never** run at session start; the pointer check only stats the bound document.
- **Step completion stamps.** `steps.set_done` now stamps `done_ts` on a tick and drops it
  on an untick (additive key; an unticked step is still stored byte-identically to before).
  A ticked step *without* a stamp means "completed at an unknown time" and makes its
  condition **UNCOUNTABLE, never zero** — `heal.goal_review`'s rule, and the reason no task
  that predates this reads as twenty-thousand-day-old drift.
- **Three config thresholds** — `checker_report_days` (3), `checker_escalate_days` (7),
  `checker_claim_timeout` (600s), each with a `TASK_STATION_CHECKER_*` env escape.
  Positive-only: a zero or negative override is refused back to the default rather than
  putting the whole board into the nag at once.

## [2.24.0] — 2026-08-12

`related` was doing two jobs and neither well. It was the only edge the CLI could write,
so every relationship — "this blocks that", "this is part of that", "these are the same
thing" — arrived flattened into one undirected kind that computes nothing. The board could
not answer *what do I do next*, because nothing in the store said what waited on what.

This release replaces it with a typed vocabulary, and then uses it: the graph lays tasks
out by what they belong to rather than by sequence number, and a second plane appears above
the task plane holding the knowledge vault, reached by a camera pan.

### Added
- **Typed edges, with a write surface.** Six flags on `update`: `--depends-on`, `--parent`,
  `--absorbed-by`, `--replaces`, `--duplicates`, and `--unrelate`. One rule governs all of
  them — **the subordinate side stores the edge, and inverses are always derived**, so
  there is exactly one place a relationship lives. `--parent` reports the parent it
  replaces (a task has at most one). `--absorbed-by` writes the edge *and* closes the task
  it is called on; `--replaces` closes the target instead. `--duplicates` is symmetric.
  Cycles are stored and warned about, never refused.
- **The knowledge plane.** `lib/knowledge.py` is new and is the single owner of vault
  *reading*, the way `lib/feeds.py` owns the feed format (`lib/obsidian_sync.py` stays
  write-only and untouched). Gated by `config.knowledge_plane_mode()` — `on`/`off`/`auto`,
  default `auto`, env `TASK_STATION_KNOWLEDGE_PLANE`. This is **not**
  `config.knowledge_graph_enabled()`, which still defaults off and gates a vault *write*
  path; the two must not be conflated.
- **The two-plane view.** Two literal stacked planes moved between by a camera pan, not a
  zoom. Only three edge kinds may cross the gap — `cites`, `distilled-from`, `references` —
  and that is enforced twice, independently: server-side before serialisation and
  client-side while building the edge list, each deriving a node's plane from its
  structural type so a foreign Interbrain node is still classified correctly. Measured on a
  real board: 615 nodes (385 task, 178 note, 52 hub), 874 edges, 17 crossing, **zero**
  illegal crossings.

### Changed
- **The graph is laid out by containment.** The zoom level is a *path*, not a three-value
  enum, so drill-down nests to any depth instead of stopping at three. A grouped task is
  positioned by its group alone; a repo is a boundary, a story is a magnet; in 3D a
  category is a patch on the sphere and radius means entanglement alone. Layout is pinned
  by default.
- **Signal-hub filters are per KIND, not per hub.** Three toggles — story, repo, pr — each
  hiding every hub of that kind along with its spokes. This **retires the one-row-per-hub
  filter** added in 1.93.0, which grew without bound as repos and PRs accumulated until the
  filter panel was longer than the graph it filtered.
- **Category hubs follow their category.** The standalone `Category hubs` control is gone; a
  hub's visibility derives from its category's own filter, so hiding a category hides its
  hub with it.
- **The shared-file edge is gone from the graph.** Two tasks editing one file is not a
  relationship worth drawing, and it was the noisiest kind on the canvas.
- `related` is no longer a writable kind. It was the container, never a linkage. `mentions`
  replaces it, derived from an explicit `[[task:502]]` marker — a bare `#N` scan was
  rejected on measurement (625 references, only 235 of which resolve).

### Fixed
- **A related task printed twice.** `related_edges` returned a reciprocal pair in *both* the
  out- and in-lists and `_related_line` walked both without dedup. Measured against a real
  store: 8 tasks, 7 reciprocal pairs, 14 duplicate rows. Fixed at the source in
  `related_edges` — a reciprocal pair is one undirected relationship the out/in split
  misrepresented as two — and the graph builder was repointed at the same canonical
  resolution rather than keeping its own. Dedup keys on **the other task**, not on
  `(other, kind)`, because the mixed-label case is live: a pair storing both `spawned-from`
  and `related` would otherwise still print twice under two different labels.

## [2.23.0] — 2026-08-10

One contended writer could kill the whole process. `_connect` opens SQLite with
`timeout=5.0` and `PRAGMA busy_timeout=5000`, and writes take `BEGIN IMMEDIATE` — so a
writer that can't get the lock waits exactly 5 seconds and then sqlite3 raises
`OperationalError("database is locked")`. **Nothing in `lib/` caught it.** It propagated
out of the write, out of the command, and the process exited 1.

What made this hide is that the concurrency guard *looks* present: `store.mutate()` has a
reload-and-retry loop, `retries=5`. But it only catches `RevConflict` — the optimistic-rev
race between two writers on the same revision. Lock contention is a different exception
class and was never in the loop, so the code read as concurrency-safe while the lock path
had no handling at all.

The visible symptom was a flaky test — `test_multiprocess_append_is_gapless` failing its
first assertion, `p.wait(timeout=60)` returning 1 — roughly 1-in-3 during a full-suite run
and 0-in-10 on an idle machine. That test spawns 4 processes × 20 events against one task,
so all 80 writes serialize; idle, each takes milliseconds and nothing approaches the 5s
budget. It was never a test bug: the same `mutate()` path runs from the Stop and
SessionStart hooks on every turn, across concurrent sessions, on the same 5 seconds with
the same missing retry.

### Fixed
- **Writer contention now retries instead of raising.** A `_retry_locked` decorator on the
  `SqliteBackend` write methods (`save_task`, `create_with_seq`, `delete_task`, the link and
  counter/marker mutators, `upsert_session_usage`, `upsert_prompt`) rolls back any dangling
  transaction, backs off exponentially with jitter, and retries.
- **Bounded by wall-clock, not attempt count.** `LOCK_RETRY_BUDGET_S = 10.0`. Each attempt
  can itself burn the full 5s `busy_timeout`, so an attempts-only cap would leave an
  unbounded worst case — and 2.21.0 had just cut a 22s turn-end to ~5s. Once the budget
  elapses the **original** exception is re-raised, so a genuinely stuck store still fails
  loudly rather than hanging.
- **Only contention is retried.** `_is_lock_contention` requires "locked" or "busy" in the
  message; any other `OperationalError` — a schema or SQL bug — propagates on the first
  attempt with no delay. Retrying those would convert a clear error into a timeout.
- **`mutate()` is deliberately not the retry point.** The lock retry sits *under* it, so
  waiting out a lock doesn't consume one of the five `RevConflict` attempts. The two retry
  loops are for different failures and now compose instead of masking each other.

### Note
- Nothing about durability changed, and nothing was at risk: the append-only stream uses a
  **blocking** `fcntl.flock(LOCK_EX)` with no timeout, so it waits rather than failing.
  SQLite was the only timeout-bounded path in the write, which is why the flake always hit
  the exit-code assertion and never the gapless-sequence one.

## [2.22.0] — 2026-08-10

Every Azure DevOps action since late July was filed under the wrong work phase, and nothing
surfaced it. Microsoft's `@azure-devops/mcp` **2.9.0** (published 2026-07-29) collapsed ~90
verb-named tools into **37** action-dispatched ones — `repo_*` alone went from 22 down to 9 —
and `phases.py` classifies MCP tools by **substring**. None of its ADO keys matched the new
names, so the classifier silently fell through to its generic verb heuristics:

| tool (2.9.0) | was filed as | should be |
|---|---|---|
| `repo_pull_request_write` (create/update/vote) | implementation — matched `_MCP_IMPL` on `"write"` | **delivery** |
| `repo_pull_request_thread_write` (post a review thread) | implementation | **delivery** |
| `wit_work_item_write` (create/update a story) | implementation | **delivery** |
| `repo_pull_request` (get/list) | **other** — matched nothing at all | research |
| `repo_file` (read a file at a ref) | **other** | research |
| `repo_branch`, `repo_repository`, `wit_work_item`, `pipelines_definition` | **other** | research |

Shipping a PR read as writing code, and reading a PR read as nothing. The rename reached this
machine with no action on our side because the server runs as `npx -y @azure-devops/mcp` with
no version pin, so npx resolves latest at each launch.

### Fixed
- **`phases.py` now classifies the 2.9.0 action-dispatched ADO tools.** Under action dispatch the
  *resource name* carries the phase and the `_write` suffix is what separates a delivery write
  from a research read, so `_ADO_DELIVERY` gained `pull_request_write`, `thread_write`,
  `work_item_write` and `comment_write`, and `_MCP_RESEARCH` gained the bare resource names
  (`pull_request`, `work_item`, `repo_file`, `repo_branch`, `repo_repository`, `definition`).
  `_phase_for_mcp` still tests delivery **before** research, which is what keeps
  `repo_pull_request_write` → delivery while `repo_pull_request` → research.
- **Both tool-name generations are matched, deliberately.** The old verb-named keys are kept
  rather than replaced: historical session transcripts on disk still contain them and the usage
  scan re-reads those files, so dropping the old keys would have re-broken every past session
  while fixing the new ones. Guarded by a dedicated regression test.
- **`PHASES_VERSION` → 5**, so `usage._phases_stale` forces a full rescan and already-stored
  `session_usage.phases` blobs recompute under the corrected logic instead of keeping their
  mis-filed split.

### Note
- `thread_write` needs its own key: `pull_request_write` does **not** substring-match
  `repo_pull_request_thread_write` — `_thread` breaks it. Easy one to get wrong when adding the
  next resource.

## [2.21.0] — 2026-08-10

The Stop hook blocked the end of every turn for **~22 seconds**. Not because it was doing
22 seconds of work: because the board regen re-parsed every session transcript on disk, many
times over, on every single turn. On a real store (375 tasks, 458 session transcripts),
`board --refresh-if-live` alone took **19.8s** of the 21.7–22.8s the hook spent, and a profile
put 17.0s of that inside `_session_msgcount` — **4072 calls over 458 distinct files**, driving
**2,372,260 `json.loads`** (10.75s in `raw_decode` alone). One transcript was re-read **120
times** in a single render. Nothing about the answers changed between those reads.

### Fixed
- **Transcript-derived values are now cached on `(st_mtime_ns, st_size)`.** A transcript is
  append-only and everything derived from one is a pure function of its bytes, so that pair is
  a COMPLETE cache key — any change to the file changes one of them, which means a hit can
  never be stale and there is no invalidation window to reason about. Memoizing
  `_session_msgcount` alone took the board command from **19.43s to 5.98s (−13.5s)** with
  byte-identical `board.html` output. `_prompt_replies` had the same shape (571 calls, each
  re-reading a whole transcript for one task's subset of prompts) and now parses each
  transcript **once**, caching the WHOLE prompt→reply map and filtering per call — equivalent
  because a reply is bounded by its own turn, so it never depends on which prompts were asked
  for. `_find_session_path` memoizes a resolved path and re-verifies it with one `os.path.exists`
  instead of re-scanning every project bucket, which is what the residual `listdir`/`stat`
  churn actually was (4758 listdirs, 308k stats).
- **A persistent count cache under `<data_dir>/cache/msgcounts.json`,** so an unchanged
  transcript is not re-parsed on a LATER turn either — the in-process layer only ever fixed
  the repeats inside one render. Counts only; reply text is prompt content and is never
  written to disk. Corruption-tolerant by design: a malformed, truncated, foreign, or
  unreadable cache file (and a single bad row inside a good file) is ignored and the value
  recomputed. This code runs inside the Stop hook, where an exception would block the user's
  turn, so a cache is never allowed to be a correctness dependency. Deleting the directory
  costs one slow render and nothing else.
- **The Stop hook's seven best-effort steps now run in ONE interpreter** (`lib/stop_steps.py`):
  stop-nudge, board refresh, obsidian flush, usage flush, subscriptions check, recap, cost HUD.
  They were seven `python3` invocations, paying ~90ms of start-up each (~0.6s per turn) and —
  worse — starting cold seven times, so the transcript parsing the board had just done was
  thrown away and redone. **`stop-gate` deliberately keeps its own process, position, and
  exact output:** the harness reads its stdout for the `{"decision":"block"}` contract.
  Per-step failure isolation survives the merge — a step that raises is caught, recorded to
  `logs/hook-health.log` under the same label `ts_run` used, and the remaining steps still run.

### Added
- **`hook_health.record()`** — the python-side twin of `_ts_lib.sh::ts_health_record`, same
  wire format and same `TS_HOOK_LOG_MAX` cap, because the steps that used to be logged by the
  shell now fail on the python side of the boundary.
- **`tests/test_transcript_cache.py`** — cache hit vs. miss on an append AND on an mtime-only
  change, the count surviving a turn without re-parsing, seven flavours of corrupt persistent
  cache, reply-map equivalence across uuid subsets from one parse, and the on_stop.sh
  consolidation leaving `stop-gate` unwrapped, unmoved, and unredirected.

### Note
- `board --refresh-if-live` is now expected under **1.0s warm** / **7s cold**, and the hook
  end-to-end under **2s warm**. The 19.8s → 5.98s figure above is measured; the final
  warm-cache numbers are the thresholds this change was written against and should be
  confirmed with a timing run against a real `~/.task-station` store.

## [2.20.0] — 2026-08-06

`/brief` filled a frozen template: a fixed section list, two parametrized diagram shapes, a blue
accent on rounded cards, and no dark theme at all. A real design doc was produced with it and then
rewritten by hand across eight rounds of feedback, because the template's output was not usable.
Every one of those rounds was a rule the skill did not know. `/brief` now **authors the HTML
directly** against a shipped stylesheet and a diagram catalogue, and derives its sections from the
material instead of from a list.

### Added
- **`skills/brief/assets/brief.css` — the validated house style, shipped as a file.** Cool grey
  ground; three semantic hues that each carry meaning (green promotes, amber stays or wants
  attention, oxblood is broken or a limit); three type roles (a system grotesque for body, mono for
  every path, command and count, a serif italic used **once** for the thesis line). Both themes come
  from redefining the same tokens, and `data-theme` overrides the media query in **both**
  directions — the previous template had no `prefers-color-scheme` block at all, so a brief read in
  dark mode was a white page.
- **`skills/brief/references/diagrams.md` — four diagram patterns with copyable inline-SVG
  skeletons.** Promotion ladder (one thing crosses a boundary, another stops at it), lifetime
  timeline (persistent against disposable), pipeline flow (a sequence with the one box a human
  authors marked), breadth vs depth (wide-and-shallow against narrow-and-deep). The fourth exists
  because a table said the thing and the reader still did not feel it. Every skeleton themes through
  `var(--…)` / `currentColor` with **no hardcoded hex inside the SVG**, carries `role="img"` plus a
  titled `<desc>`, and sits in a `<figure>` with a `min-width` so a wide diagram degrades to a
  scroll instead of an illegible squish. It also documents **when not to draw**: if a two-column
  table says it completely, don't.
- **A `Limits` section is now mandatory** — two things the design does not solve, each with a
  concrete example. This is not a hedge slot. A reader's question exposed a real hole one draft had
  papered over; naming the limits is what earns trust in the rest of the document, and it is where a
  missing constraint surfaces while it is still cheap to add.
- **Collapsible implementation detail.** `<details>` closed by default with a `+` / `−` marker, on
  one rule: **open is what and why, collapsed is how.** Code, route tables and collision mechanics
  go inside; workflow steps and rules stay out. The exception is a file that *is* the answer to "how
  do I define this?" — that one is shown open, with the line that links it onward annotated.
- **Persona badges.** Two audiences means two how-to sections, one each, not one section with "if
  you're QA…" branches inside it. `<span class="who dev">` / `<span class="who qa">` renders a small
  mono badge in the heading, colored to the palette, so a reader can skip what is not theirs.
- **`task-station brief path --task <n>`.** Resolves the task, creates the artifact directory,
  records `brief_path` so the brief stays findable, and prints the absolute path. Reads no spec and
  never touches stdin. This is what the authoring flow calls before writing its HTML.

### Changed
- **`skills/brief/SKILL.md` is rewritten around the fourteen corrections that had to be made by
  hand.** The former hard rule — *never write HTML or CSS* — is gone; it was the reason the output
  could not improve. In its place: sections are the reader's questions and are derived every time
  (there is no fixed list, and **`/brief` is not one template** — material whose natural shape is
  not a decision one-pager is not forced into one); the change is named with the reader's own
  before→after tokens (`seeds → test-data`, not "the restructure"); one or two plain sentences per
  section, then a table, diagram or code block carries the detail; every claim about current state
  carries its file and line numbers **in the same row**, not in a prose provenance paragraph at the
  end; no single overloaded verb carries a permission distinction (name each verb, give it a
  column); load-bearing rules get their own numbered box **in the section they govern**; the plan is
  a full-width three-column ladder with a line explaining what the amber grouping means; and only
  commands that actually exist may appear — grep the repo for the script names first, because one
  invented command costs more trust than the document buys.
- **The prose budget is stated as a shape, not a word count.** The reference brief is ~1,280 visible
  words across 9 sections and 4 diagrams, but the number is not the lever: the first hand-written
  version was ~2,600 words and read as "too verbose", a hard cut to ~1,540 was "0% verbose" and
  unreadable. Cutting words out of paragraphs produces dense paragraphs. Moving the detail into a
  table produces short prose *and* scannable detail, and the word count falls out of that.
- **An explicit do-not list for voice.** Cut rhetorical setups, cadence tricks, section tag labels
  used as decoration, self-congratulating summaries, stacked em-dash asides, any sentence whose only
  job is to introduce the next one, and trailing "next steps" editorializing the reader did not ask
  for.
- **The skill ends in a fifteen-line self-check** it must run before writing the file.
- **`brief render --spec <file>` is retained and unchanged** for back-compat. `lib/brief.py`,
  `lib/brief_template.html` and the golden fixture all stay; `render` is still the default action,
  so anything already scripted against it keeps working. It is simply no longer the preferred path.
- The `brief` command's `action` argument now documents both values: `render | path`.

## [2.19.0] — 2026-08-06

`heal` reconciled the decision log and nothing else — yet a task's **goal** and its **checklist**
are what a cold session reads first to decide what to *do*, while decisions mostly explain *why*.
One real task scanned clean on every check while its goal described a mission already accomplished
and five of its steps named work that had since been proved unnecessary. Nothing was internally
inconsistent, so nothing could see it. This release gives the pass eyes for both, and stops it
pausing in the middle to ask permission.

### Added
- **`/heal <n>` works at all.** The command passes its argument positionally while the `heal`
  subparser accepted only `--task`, so the natural form exited with `unrecognized arguments` and
  the skill failed before doing anything. There is now an optional positional, resolved through
  the same resolver `--task` already uses rather than a second parser. `--task`, `--all` and the
  bare attached-task form are unchanged. A positional beside `--all`, or beside a `--task` naming
  a different task, is **refused and reads nothing** — there is deliberately no precedence rule,
  because a silently guessed scope reconciles a record you did not mean.
- **A tenth check: a live step that restates a decision the task has already superseded.** Scored
  by shared significant vocabulary against each superseded decision. It skips already-superseded
  steps, exactly as the stale-step check does and for the same reason — re-reporting a step that
  was just retired makes a freshly-healed task read as dirty.
- **A goal review, which is a PROPOSAL and never a finding.** It reports how many decisions have
  landed since the goal line was last written. An untouched goal is not a defect — a goal is meant
  to outlive the decisions that pursue it — so it is never counted as an issue and can never make
  a heal due, the same contract merge candidates and the pinned set already keep. With no recorded
  baseline it says **"cannot be counted"**, never zero: zero reads as "nothing happened" when the
  truth is "nobody recorded the baseline".
- **The verdict is three rows instead of one.** `Mechanical` says what the checks found;
  `Judgment` says whether the half no check can do has actually been done, quoting the last
  `--mark-healed` note and how many decisions have landed since; `Heal due?` stays the combined
  line. `Heal due? no` was doing real damage alone — it reads as "this record is complete" when it
  only ever meant "eight cross-referencing checks found nothing".
- **The dry run now briefs the goal and every live step**, beside the newest decisions, with the
  one question the pass must answer of each: does the newest evidence retire this?

### Changed
- **`/heal` is one uninterrupted pass and no longer stops for approval.** Scan, judge, apply,
  verify, report. The gate was removed rather than annotated: the flow went from seven steps to
  five, and both the skill and the command frontmatter were rewritten, since both still advertised
  "asks once before changing anything".
- **Every write now names the one command that reverses it** — this is what replaces the gate.
  `--apply` prints a per-operation undo, warns that `--restore-decision` repeats rather than
  taking a list, and names the pre-heal task blob as a whole-task fallback. Nothing was widened:
  `--apply` still performs only the narrow signature-matched merge clusters, and merge
  **candidates** remain read by a human and by nothing else.

### Note
- Check 10's threshold is **unvalidated against the incident that motivated it**, and is marked as
  such in the code. The task that prompted it had already retired the offending steps by hand
  before the check existed, and the check skips superseded steps by design — so the original
  evidence is gone. It is verified to fire on a synthetic reproduction and to stay silent on two
  real tasks; it has *not* been shown to catch the real case. Expect to tune it on the next one.

## [2.18.0] — 2026-08-01

Every identifying name belonging to the maintainer's employer is gone from the tree, replaced by
literal placeholders. The interesting part was not the renaming — it was the two places where a
name was **load-bearing**, and the one place where a name should never have been in the source at
all.

### Changed
- **A task's artifact folder now comes from its active category `[TAG]`, not from a hardcoded
  map.** `_COLOR_PROJECT` mapped a category colour to a folder name and shipped one of the
  maintainer's own project names inside it — while duplicating what the taxonomy already carried.
  `_project_slug` now slugs the resolved tag through `categories.hub_slug`, so the folder follows
  every layer that shapes the taxonomy: the discipline pack, an org pack, and a per-slot override
  in `config.json`'s `categories` key. Rename a slot and you get the renamed folder, **from your
  config, never from ours**. A task's artifact folder and its category-hub slug are now the same
  token. Under shipped defaults `silver` artifacts move `task-station/` → `tooling/`; every other
  slot keeps the folder its tag already implied.
- **Placeholder vocabulary is deliberately literal** — `companyname`, `Projectname`,
  `projectname`, `OtherProj`, `LEGACY`, `PROJECT`, `Company Brain`. A plausible-looking invented
  brand reads like a real company; a literal placeholder reads like what it is.
- **The demo org feed `pe.js` is now `org.js`**, since the old name was the company's initials.
  The demo feeds stay in the tree: they are the only way to see Interbrain federation without a
  second machine.

### Fixed
- **Two placeholders had to keep their shape, or two tests would have passed vacuously.**
  `OtherProj` keeps its internal capital so `extract_identity_keys` still exercises
  `[A-Z][A-Za-z0-9]*-\d+` rather than a name that `[A-Z][a-z]*` would also match; `LEGACY` stays
  all-caps so `_slug` still tests uppercase→lowercase collapsing. Both would have "passed" after a
  tidier substitution while testing nothing.
- **`BriefPathHelpersTest` is hermetic again.** Making the artifact folder config-driven was
  correct, and it quietly turned a hermetic test into one that read whatever taxonomy the
  developer happened to have. `categories` merges user overrides over the shipped taxonomy **once
  at import** and caches the result, and the test module imports `task-station` before `setUp` can
  redirect `TASK_STATION_HOME` — so the new assertions read the real `~/.task-station/config.json`:
  green on a clean machine, red on the maintainer's. `setUp` now rebuilds the taxonomy against the
  temp home, and `tearDown` reloads **before** dropping the env var, because popping it first
  rebinds to the developer's config. The lesson generalises: the moment behaviour starts reading
  user config, every test asserting that behaviour needs to pin it.
- **The prefix-boundary comment in `brains.py` explains its rule again.** It had illustrated the
  `-` boundary with a pun on the old project name; the replacement keeps a real example of a short
  brain name that must not swallow a longer repo starting with the same letters.

### Note
- **`dev.azure.com` stays.** It is a public hostname and parsing Azure DevOps URLs is a product
  feature — removing it would have deleted functionality, not a fingerprint. Only the `<org>` and
  `<project>` segments were placeholders to begin with.

## [2.17.0] — 2026-08-01

A real task scanned **clean on all eight checks**, and the judgement pass then found two things
the scan had missed. Only one of them is detectable — and pretending otherwise would have been
the fifth confidently-wrong check this module has shipped.

### Added
- **`heal --scan` detects a consolidation that has come undone.** One decision had declared
  itself a consolidation — *"replaces the five per-release records"* — and over the next day four
  more release-shaped decisions accreted after it. The consolidation was undone by accretion and
  nothing noticed, leaving the record asserting two contradictory things about how many entries
  that subject has. This is a **finding**, not a proposal: a consolidation coming apart is a
  defect, not an opportunity. It only recognises a consolidation where the text *declares* itself
  one — via the same discriminator every keyword check now shares — so a decision merely
  mentioning the word isn't mistaken for one. It never merges anything; naming the surviving
  summary is still judgement.
- **Accrual counts since the last heal** — decisions, log entries, PR/story links and steps —
  reported as information, never as findings.

### Changed
- **The flow now names the gap the deterministic layer cannot cover.** The second thing that pass
  found was a release that had shipped and was recorded *nowhere* — no decision, no log entry, no
  PR link. Nothing existed to cross-reference, because the work happened outside anything a scan
  can read. There is no detector for that, so `/heal` asks explicitly whether everything that
  actually shipped has a decision, alongside the existing instruction to re-read pinned
  decisions.
- The docs now say plainly which gaps are machine-checkable and which aren't: **a clean scan means
  the record is internally *consistent*, not that it's *complete*.** Those are different claims.

## [2.16.0] — 2026-07-31

`/save` gets the treatment `/heal` got in 2.15.0 — it had three of the same problems and one
that was worse.

### Changed
- **`/save` emits a gap report, not the digest.** It was reprinting everything: 71,516 characters
  on a real task, of which **71,271 was the digest dump** including all 62 decisions — 99.7% of
  the output spent telling a session the state it had spent the whole session building. Now it
  names what's *missing*: which slots are empty, which look stale (a `state` that doesn't begin
  with `NEXT:` says where things *stand* rather than what to *do*), what's landed since the last
  checkpoint so the summary knows what to cover, and the digest size a fresh session will load.
  Same shape measured at **7,051 characters — a 90% cut**. `--verbose` keeps the full dump.
- **`commands/save.md` orchestrates instead of handing over a checklist**, and the cold-read
  check is now mechanical: after the write, every slot is verified non-empty and the state
  verified to lead with `NEXT:`, with anything failing named. A save is an amendment, not a
  rewrite — only what's missing or stale gets touched.

### Fixed
- **A replaced summary is recoverable.** Every other reconcile verb here is non-destructive —
  supersede, split, merge, step-supersede all keep the original and offer a restore — but the
  summary, the *first* thing a resuming session reads, could be silently destroyed by one thin
  save. Replacing it now preserves the previous version and names `--restore-summary` in the
  output.
- **The checkpoint stamp means "captured", not "started".** Running `/save` and writing nothing
  recorded a full structured checkpoint while the summary was still empty — the same bug a bare
  `heal --apply` had, and worse here, because the stamp exists precisely to distinguish a real
  checkpoint from a lighter `--state` refresh. Emitting the block no longer stamps; the update
  that writes a summary and a state does.

## [2.15.0] — 2026-07-31

`/heal` is now one conversation instead of a sequence of flags you had to pick between.

### Changed
- **The `heal` skill orchestrates; you never type a flag.** It runs the free `--scan` first and
  stops there if the task is clean, reads the dry run **at most once**, presents a compact
  numbered plan, asks once for the whole plan, then executes and verifies. You should never need
  `--apply`, `--merge`, `--split`, `--dispose-acks` or `--mark-healed` yourself — the skill
  chooses. The CLI keeps dry-run-as-default because scripts need a safe default; a person gets a
  conversation.

### Fixed
- **`--apply` no longer reprints the dry run, so a heal costs once instead of twice.** It was
  re-rendering the scan block, the judgment list and every current decision — about 94% of which
  is the decision list. On a 40-decision task that's ~47,000 characters, so the obvious two-step
  paid for it twice for one heal. `--apply` now prints only what it *did*; `--apply --verbose`
  restores the full dump. Measured on a small task: 5,477 chars → 114.
- **A bare `--apply` refuses instead of stamping a heal that did nothing.** It used to perform
  zero operations and stamp anyway, so the record claimed a task was reconciled when it hadn't
  been. It now names the two honest moves. A stamp that lies makes every other stamp unreadable.
- **`--split` and `--merge` now stamp the heal.** That path returned before the generic `--apply`
  path where the stamp lived — which is why seventeen merges on a real task still left it reading
  `last heal never`, and why the stamp added in 2.13.0 appeared not to work: it went on the path
  that wasn't being used.
- **An ephemeral path is not drift.** All seven findings on a real task were worker briefs under a
  session `scratchpad/` directory that task-station had auto-captured. A scratchpad is erased
  when its session ends *by design*, so reporting it as "the digest points a resumed session
  somewhere it cannot go" was true and useless. They're now counted on one informational line,
  and that task reports no heal due.

## [2.14.0] — 2026-07-31

### Fixed
- **One declare-vs-describe rule, one implementation — the same bug had shipped four times.** A
  keyword check reported that a word was present and said nothing about what the word was
  *about*, and a task's own record talks about staleness and supersession constantly, because
  that's precisely what a reconcile pass writes down. The drift check scraped branch names out
  of prose (2.12.0), the supersession check fired on decisions *explaining* supersession
  (2.13.0), the stale-step check fired on a step written to *fix* staleness (2.13.1), and the
  memo backstop warned on a memo describing an upstream library's withdrawal (this release).
  All four now route through one discriminator that reads the word standing in front of the
  match — that's what says whether the keyword is aimed at the entry itself or at something the
  entry merely mentions. The rule is documented as a rule rather than four patches, so the next
  check inherits it.

### Added
- **`heal --scan` proposes merge candidates.** Current decisions sharing a leading shape are
  grouped and named — which is how all sixteen were found by hand on a real 99-decision task.
- **`heal --scan` lists pinned decisions with their ages.** They brief *every* session, so stale
  content in one is the most expensive kind there is: on that same task a pinned decision named
  two codenames a later decision had retired, and had been briefing them for days with nothing
  to surface it.
- Both are **proposals, not findings** — neither is counted as an issue and neither can make a
  heal due on its own. Choosing a surviving summary is judgment, a wrong merge writes a false
  consolidation into the record, and being pinned is not a defect. A task with four release
  records isn't broken.

## [2.13.1] — 2026-07-31

### Fixed
- **`heal --scan` no longer reports a step that merely *describes* staleness.** The
  unlinked-supersession-prose check learned this distinction in 2.13.0; the stale-step check
  didn't, and it was the last thing keeping a fully reconciled task reporting as
  under-reconciled — the same false-alarm problem the heal timestamp was added to cure.
  On a real healed task both remaining findings were wrong: one step matched `STALE` inside
  *"delete stale tracked BRIEF-x.md"* (it names a file to delete), and the other matched
  `superseded` inside *"the names in the superseded ancestor are REJECTED"* — a step that was
  itself the corrected replacement written during that heal, describing the supersession it had
  just performed. A step is now reported only when it plausibly declares **itself** obsolete,
  and an already-superseded step is never reported.

## [2.13.0] — 2026-07-31

Four gaps that only showed up once `heal` was run on a real, badly-accumulated task. Two were
correctness bugs in code shipped the day before.

### Fixed
- **The heal was never stamped.** After seventeen merges, five supersedes and a split on one
  task, the scan still reported `last heal never` and counted every decision as "new" — so
  `heal due?` was permanently yes and the count was meaningless. Every session opened with a
  false "under-reconciled" alarm, which is precisely how a signal built to be trusted becomes
  noise. A successful `--apply` now records the heal, `--scan` still never does (read-only is
  its contract), and `N new decision(s) since` counts from the stamp. A stamped task still
  reports genuine findings — the stamp suppresses nothing.
- **The unlinked-supersession-language check stopped crying wolf.** Four of its five findings on
  a real task were decisions merely *describing* supersession — the feature itself, a rule
  replacing a rule, a correction to a memory note. It now fires only when the prose plausibly
  names a decision-shaped target. Same fix the drift check needed in 2.12.0, for the same
  reason: a check that's 80% wrong teaches you to skip it.

### Added
- **`heal --mark-healed [--note]`** — for the judgement-only heal, where you read everything and
  concluded nothing needed changing. That has to be recordable, or the timestamp lies in the
  other direction.
- **`heal --apply --dispose-acks <id8>|all`** — retro-dispose acks made before dispositions
  existed, taking the same three dispositions a live ack requires. `all` is legitimate rather
  than lazy: those acks came from sessions that no longer exist and whose intent is
  unrecoverable, so a bulk `--noop` with an honest reason *is* the correct disposition. A
  retro-fill is recorded **as retroactive** — who, when, why — and never overwrites the original
  acker or timestamp. History isn't forged.
- **`update --step-supersede N` / `--step-restore N`** — steps had only add/done/undone, so a
  step gone stale could be ticked (a lie) or worked around with a warning step (an
  anti-pattern). Superseding follows the decision pattern: off the checklist, kept in `history`
  marked, reversible. It leaves **both** sides of the `n/m` counter, so a stale step no longer
  sits in the denominator making a task look permanently unfinished.

## [2.12.0] — 2026-07-31

Truncation and supersession were two answers to one question — *what should brief a fresh
session?* Truncation answered by **age**, a proxy, and a wrong one: it hid valid old decisions
and showed invalid recent ones. Supersession answers by **validity**. Now that supersede, split
and merge all exist, the proxy isn't just unnecessary — it's harmful.

### Changed
- **Every current decision renders in the digest.** The recency limit and the
  `+N earlier decision(s)` pointer are gone; with nothing hidden there's nothing to point at.
  Superseded, merged and split entries stay out of the digest and stay in `history`, marked,
  exactly as before.
- **`--pin` now controls ORDER, not visibility.** Pinned decisions sort first as the
  architecture spine, then everything else oldest-first; the `★` still marks them. Nothing is
  hidden either way, so pinning no longer rescues anything from being dropped — it just puts
  the spine where you read it first.

### Added
- **A decision-length advisory.** Past 600 characters you get one line naming the length and
  pointing at `heal --split`. **The write always succeeds and stores the text in full.** It
  never refuses — a refusal would push you to drop a fact or split one decision into two fake
  ones to get under a limit, and the remedy for a long decision is to split it deliberately,
  which a warning points at and a refusal would stop you from even recording.

### Fixed
- **`heal --scan`'s drift check no longer scrapes branch names out of prose.** It was reporting
  the English word following "branch" — five of seven findings on a real task — and resolving
  every bogus token made the scan slow enough to time out completely on a 94-decision task. It
  now finishes in about a second and reports only genuinely missing paths and refs.

## [2.11.0] — 2026-07-30

task-station could capture (`save`) but never reconcile. A task's decision log is append-only,
so a fresh session got *history* and had to reconstruct the present from it. `heal` is the
missing half.

### Added
- **`/todo heal`** (bare alias **`/heal`**) — reconcile a task's decision log into current
  state. **Dry run by default**: a bare `heal` prints the plan and changes nothing.
- **Two more verbs, joining `supersede`.** **`split`** replaces one compound decision with
  atomic ones — for entries that mix a still-valid ruling with a refuted one, which
  supersession can't fix without destroying the good half. **`merge`** collapses several
  decisions that are *true but no longer load-bearing* — release records, iteration steps —
  which supersession also can't touch, because nothing about them is wrong.
- **Nothing is ever deleted.** Each verb marks the original, drops it from the digest, and
  keeps it in `history` labelled with what replaced it. `update --restore-decision <n>` undoes
  any of them. `split` refuses without `--into`, so replacements must exist before an original
  is retired.
- **`heal --scan`** — the deterministic layer, zero tokens, never mutates: undispositioned
  acks, memos whose `--corrects` target was never applied, prose that claims "superseded"
  while linking to nothing, oversized decisions, path/branch drift, link rot, a health metric,
  and stale steps (reported only — steps have no supersede primitive yet).
- **A `heal` skill** and a SessionStart nag when a heal is due.

### Changed
- `save` now says **"heal first"** when a heal is due — it replaces the summary wholesale, so
  writing one from refuted decisions bakes the drift into the first field anyone reads.
  `done` warns before closing a task with a self-contradictory record. Both warn, neither
  blocks.

### Safety
- `--apply` backs the task blob up first and **refuses outright if that backup cannot be
  written**. The `--log` milestone trail and `history` are never touched. One task at a time
  unless `--all`, which announces its scope before doing anything.
- `--split` and `--merge` are refused in the same invocation: `--into` can only be given once,
  so combining them would silently link one verb to the wrong target.

## [2.10.0] — 2026-07-30

### Fixed
- **Pinned and recent decisions no longer compete for one budget.** 2.9.0 gave them a shared
  quota, so on a task with 72 decisions, pinning six showed six pinned entries and *zero*
  recent ones — a fresh session got the architecture spine and lost everything that had just
  happened. Unpinning didn't help, because the budget itself was the constraint.
  They answer different questions and shouldn't have competed: pinned means *"always brief
  this"*, recent means *"here is what just happened"*. Now **every** pinned still-current
  decision renders unconditionally and untruncated, and the last N unpinned render on top of
  that — pinned block first, oldest-first within each block. The `+N earlier` pointer counts
  only unpinned still-current decisions that didn't fit, so it stays honest.

### Removed
- **The pin cap**, along with the shared quota it existed to protect. Keeping the pinned set
  curated is a judgement about whether each pin is still load-bearing, which belongs to a
  reconcile pass that reads the whole decision set — not to a limit picked in advance.

## [2.9.0] — 2026-07-30

A board's decision log and its memo ledger are both append-only, and both were being read
as if they were current state. Nothing could say *"this replaces that"*, so every fresh
session got history and had to reconstruct the present. One task here holds 69 decisions
while the digest shows 6 — and some of the hidden ones refute the visible ones.

### Added
- **Decisions can supersede decisions.** `update --decision "…" --supersedes <n>`
  (repeatable; `<n>` is the number shown in `/todo <n> history`) marks the earlier one
  replaced. A superseded decision leaves the default digest **entirely** — it isn't merely
  old, it's wrong — and appears in `history` as `⊘ … — SUPERSEDED by decision <n>`, so the
  trail stays complete while the briefing stops repeating refuted reasoning.
- **Decisions can be pinned.** `--decision "…" --pin`, plus `--pin-decision <n>` /
  `--unpin-decision <n>`. A pinned decision stays in the digest regardless of age, so
  truncation becomes importance-first rather than recency-first. The pinned set is capped,
  so the digest can't quietly bloat back to where it started.
- **Memos can declare what they correct** — `memo send --corrects <memory-slug|decision:N|
  memo-id8>`, repeatable. Such a memo can't be acked without engaging its target, and the
  pending-memo nag shows that it carries corrections before you open it.
- Correction-shaped wording (`correction`, `supersede`, `retraction`, `withdrawn`,
  `no longer`, `stop doing`) with **no** declared target now warns at send time and reminds
  again at ack — because `--corrects` only helps when the sender remembers it.

### Changed
- **An ack must say what it did with the memo.** Exactly one of `--decision [TEXT]`,
  `--memory <slug>`, or `--noop "<reason>"` (reason mandatory). A bare ack is refused: an
  ack is a receipt, and treating a receipt as an integration is how a correction gets
  acknowledged and then never reaches the store that auto-loads next session. It also
  removes the incentive to batch-ack, which is how a batch of memos goes unread.

### Compatibility
- A decision is now **either** a legacy plain string **or** a rich record, and every reader
  accepts both. Only a decision you actually touch becomes rich; legacy task blobs
  round-trip byte-identically. No migration, no action needed.

## [2.8.0] — 2026-07-30

Two blind spots, both found the hard way: a delegated worker authored three features and
57 tests, finished cleanly, committed nothing, and `status` still read `finished (ok)`.
Separately, every call the hooks made was masked, so a permanently-broken one was also
permanently invisible.

### Added
- **Uncommitted work is reported when a worker finishes cleanly.** A clean exit that left
  a dirty worktree now shows the count everywhere it matters: `status` renders
  `finished (ok — 6 UNCOMMITTED)`, `delegate run` relays a `!!` banner naming the directory
  and the command to inspect it, and the task feed records it. It reports only — nothing is
  auto-committed, since a brief may legitimately tell a worker not to commit. Only clean
  exits render a count; an abnormal exit has already been checkpointed, and a stale count
  can't resurface on a later crash. Main checkouts are exempt.
  This is expected behaviour rather than a worker bug: workers spawn fail-closed with an
  author-only toolset, so `git add`/`commit`/`push` are auto-denied and a headless worker
  can't prompt to change that. The hub commits afterwards. `docs/ARCHITECTURE.md` now
  states this outright, because the silence is what made it hard to spot.
- **Hook health tracking.** `task-station hook-health [--clear]` shows recent hook-call
  failures, and SessionStart emits one self-capping nag while failures from the last 24h
  remain.

### Changed
- All five hooks now route their maskable calls through `hooks/_ts_lib.sh` —
  `ts_run <label>` (stdout discarded) or `ts_capture <label>` (stdout passed through, for
  `x=$(…)` sites). Both keep the old non-fatal contract, so a hook still can never fail or
  slow a session, but a non-zero exit is now recorded to `<data_dir>/logs/hook-health.log`
  (capped at 200 entries) instead of vanishing. Sites deliberately left masked — heredoc
  and TTY writes, plus predicates whose non-zero exit is normal control flow — say so
  inline.

## [2.7.0] — 2026-07-30

### Added
- `/todo <seq>-<ordinal>` jumps straight into a specific hub session — `/todo 444-3`
  opens the window for that task's third hub session instead of making you read a recap
  and pick. `/todo <seq> -s` is unchanged, comma-separated multi-jump included. Ordinal
  `0` is valid; an unknown ordinal tells you which ones exist; a session that can no
  longer be resumed offers the fresh-start form instead.
- SessionStart now sweeps **orphaned background workers** — workers whose spawning hub
  session is gone. "Gone" means the hub's pid no longer exists, never merely that it is
  idle, so a hub waiting on your input keeps its workers. Unreadable session state or a
  failed agent-list lookup reaps nothing rather than guessing; very new workers get a
  grace period; every reap is logged; and any failure is swallowed so the sweep can
  never slow or fail session start.

### Changed
- Worker display names carry the spawning hub's ordinal:
  `task-station-<seq>-<ordinal>-<project>[-<label>]`, e.g.
  `task-station-444-3-claude-todo-packs`. You can now tell at a glance which of several
  hub sessions owns each background worker. The ordinal goes *after* the seq so the
  `task-station-` prefix stays intact and worker reaping keeps recognising its own
  workers. A worker's name records where it came from and does not change when a
  different hub session later resumes it.

## [2.6.0] — 2026-07-25

### Removed
- **The board3 preview engine is retired.** `lib/board3.py` and `tools/board3_shell.py`
  are gone, along with the `board3` subcommand, the `board --v2` / `--classic` / `--demo`
  flags, `--board-engine` / `config.board_engine()`, and the `_write_board_v2` writer.
  `/todo board` is now unconditional: **one board** (`tools/render_board.py`), no engine
  choice. The preview had not been the default since 2.0.1's revert, its federation UI had
  already been ported into the shipping board by F1/F2/F3, and it had drifted behind —
  it never received 2.4.x Agent View. Dropped with the shell: the 3-view UI, the staging
  tray, the mounts rail, and the brains manager panel (the `task-station brains` CLI was
  always the only write path). Full record: `docs/specs/BOARD-RETIREMENT.md`.
- A persisted `board_engine` key in an existing config is **inert** — nothing reads it, and
  it never warns or crashes. Passing `--board-engine` prints a one-line retirement notice
  and exits 0.

### Added
- **`lib/feeds.py` — the single owner of the feed format.** Extracted from the retired
  preview plus the DUPLICATE implementation that had grown inside `lib/task-station.py`:
  serialization (`_feed_js`) and its exact inverse (`parse_feed_file`), the
  peers-then-demo load order (`peer_feed_files`), the per-task read-only view-model, the
  `sync_safe` gate (`strip_local_only` + `trail_visibility`), the frozen F6 join key
  (`_pr_signal_id`), and the F5 content rev. There is now exactly ONE implementation — this
  is the seam the two-machine sync transport (J-track) will consume.
- `/todo board` now exports the self feed on every write (`<data_dir>/feeds/self.js`, plus
  `self-archive.js` past 50 closed tasks), so the feed root is always current. A refused
  downgrade writes nothing at all — board, rev sidecar, and feeds alike.
- **The board now says when federation is off.** `--interbrain auto` resolves to off on a
  single brain with no peer feeds, and the board used to say nothing — which is why shipped
  federation work was undiscoverable. One dim line in the Help panel names the state and
  `/todo config --interbrain on`. Enumerated as the sole intentional interbrain-off parity
  exception in `docs/specs/BOARD-BEHAVIOR.md` B14; the parity test is unchanged and still
  passes, because the line does not depend on *why* federation is off.

### Fixed
- **Demo federation renders again.** The four fixtures in `fixtures/demo-feeds/` are now in
  the CANONICAL wire form (`window.__TSFEED_<alias> = {json};` on one line + the __TSFEEDS
  registration, pure data). They were IIFE-wrapped — written for the retired preview's
  *client-side* rendering, where a browser evaluated them — so the server-side path skipped
  all four and `seed_demo.py` produced federation-switched-on with **zero peer rows**. Two
  further blockers went with it: `rnguyen-demo.js` was `kind: "self"`, which
  `foreign_view_models` deliberately drops as "the local brain" (now `kind: "peer"`, with
  its tasks on `brain: "demo"`), and the category objects lacked `key`, which
  `_foreign_view_model` reads for the row/graph accent (derived from each fixture's own hex).
  Latent before this release — on 2.5.0 the same check also yields zero peers, because the
  old seeder wrote `<data_dir>/board3/feeds/demo/` while the board read
  `<data_dir>/feeds/demo/`; a path bug was hiding a wire-form bug. Retiring the only
  client-side consumer is what made the canonical form mandatory. New guards:
  `tests/test_feeds.py:ShippedFixtureTest` (every shipped fixture parses to a dict with
  tasks, is pure data, isn't `kind: self`) and
  `tests/test_board_behavior.py:DemoFeedFederationTest` (the real seeder, end to end).
  Every pre-existing peer test built its feed in-process, which is precisely why none of
  them caught this.

### Changed
- **Feed root unified to `<data_dir>/feeds/`** (`self.js`, `self-archive.js`, `peers/*.js`,
  `demo/*.js`). `<data_dir>/board3/` is gone. This closes a real divergence: the preview
  wrote demo feeds to `<data_dir>/board3/feeds/demo/` while the board read
  `<data_dir>/feeds/demo/`, so the two halves disagreed on where feeds live.
- `tools/seed_demo.py` seeds into the unified root and no longer carries its own copy of
  the feed parser. It stays: demo feeds are how peer rendering is exercised until the sync
  transport lands. `--clean` removes `<data_dir>/feeds/demo/` and leaves the self feed.
- **Docs reconciled — they had been inverted since 2.0.1.** `docs/ARCHITECTURE.md` §(a2)
  rewritten (it still described the preview as "v2 (default, 2.0)" and the shipping board
  as a "one-release fallback"); the old parity ledger replaced by a retirement record;
  `SHARING-NOTES.md` (the J-track seam doc) repointed at `lib/feeds.py`;
  `BOARD-BEHAVIOR.md` now says "the board" rather than "classic". The board2/board3
  prototype specs are kept as history under `SUPERSEDED` headers.
- `tests/test_feeds.py` (new) carries every feed-layer test ported off the retired module;
  `tests/test_brains.py` (new) carries its `brains.py` module + CLI coverage. Shell-only
  tests were dropped with the shell.

## [2.5.0] — 2026-07-24

### Added
- **Category packs (F8):** the 12 shipped categories are now the **dev pack** — one of
  several: seeded `finance` (CLOSE/REPORTING/AUDIT/VENDOR/BUDGET/TAX…), `hr`
  (RECRUITING/ONBOARDING/REVIEWS/POLICY/BENEFITS…), `exec`, and a lean `general` pack,
  all riding the same palette/tint machinery, GENERAL permanent in every pack. Select
  with `config --category-pack <name>` (`packs list` enumerates); per-slot overrides in
  config.json still win over any pack; orgs can add/override packs via a
  `category_packs.json` data file. Self-categorization guidance now renders the ACTIVE
  pack's slots, so tasks self-categorize in any discipline — the default (dev) is
  byte-comparable to 2.4.3, law-tested.

## [2.4.3] — 2026-07-23

### Fixed
- **Resume one-liner no longer misclassifies live interactive sessions as `--bg`
  workers (task #464).** `_is_live_bg()` treated *any* `claude agents` row with a pid
  as a background agent, but `claude agents --json` lists every running claude process
  — interactive hub windows included — so a live hub session's resume collapsed to a
  bare `claude agents` picker instead of `cd <dir> && claude --resume <sid>`. It now
  requires `kind == "background"` (present on every row via `agents_index`); interactive
  sessions resume with a plain `--resume`, background workers still attach via `claude
  agents`. Regression tests cover both directions (2.4.1 shipped the original
  bg-aware-resume in #464; this closes the classification hole).

## [2.4.2] — 2026-07-22

### Fixed
- **Worker reap on `/done` now actually stops workers + reclaims RAM (task #465).**
  2.4.1 killed the pid only, which the ClaudeCode.app supervisor respawned from the
  session-store file (reclaiming nothing). Reap now removes the `~/.claude/sessions/
  <pid>.json` file *then* `killpg` — verified against a real `--bg` agent (~465 MB
  freed, no respawn). **Airtight safety:** a session is reaped ONLY if it is a
  delegate-registered worker for this task (`workers.json` seq match) ∩ `role==worker`
  in the roster, with a task-station worker name, NOT busy, and not the closing
  session — so a mis-attached hub / working / stray session is NEVER killed, on any
  close path. New `reap_workers_on_done` config (default on) disables it.
- **`-s` / resume attaches the live session (task #465).** `bg_aware_resume()` now emits
  `claude agents` (ATTACH the exact live `--bg` session) instead of a `--fork-session`
  copy; forking is mentioned only as an aside. Non-live targets keep plain `claude --resume`.

## [2.4.1] — 2026-07-22

### Added
- **Reap live `--bg` workers on task close (task #464).** `/done` now stops a task's
  still-live background-agent workers (registry- + roster-sourced, killed by pid via
  `delegate.reap_task_workers`) so finished workers no longer linger in Agent View.
  Workers stay alive *while the task is open* (still attach-inspectable); zombie
  (no-pid) and hub sessions are never touched; each reap posts a `stopped` ledger
  entry. Wholly best-effort — closing a task never fails because reaping failed.

### Fixed
- **Bg-aware resume one-liners (task #464).** Every `/todo <n> -s` / resume command now
  routes through `bg_aware_resume()`: a bare `claude --resume <sid>` is refused by
  Claude Code when `<sid>` is a currently-live `--bg`/background session, so for a live
  target we emit a `--fork-session` copy and surface the attach path (`claude agents`).
  Non-live targets keep the plain `claude --resume`.

## [2.4.0] — 2026-07-22

### Added
- **Workers as Agent View background agents (task #463).** Delegated `claude` workers
  now spawn as `claude --bg` detached agents: they survive the hub dying, appear as
  rows in Claude Code's **Agent View**, and are attach-inspectable (left-arrow → select
  → attach). task-station derives their liveness/exit from `claude agents --json` +
  the transcript (no stdout stream under `--bg`).
- **Hub session ordinals `<seq>-<n>`.** Every hub session that touches a task gets a
  stable, monotonic, never-reused number (`-0` = the session that created the task);
  surfaced in `whoami --porcelain`, the statusline, `/todo` detail + board, the HUD,
  the brief, and the SessionStart/Stop hook lines. Workers keep their full descriptive
  slug (never ordinal-numbered).
- **Append-only hub↔worker interaction ledger** on the task record — `{ts, action,
  worker, actor, actor_ordinal, detail}`, `action ∈ spawn|resume|iterate|modify|stop|
  adopt|finish|crash|timeout|stalled`. Unbounded (complete provenance); rendered on
  detail, history, and the brief. New quiet CLIs: `add-ledger`, `register-worker-session`.
- **Accurate per-task / per-hub-ordinal / per-worker cost + token accounting**, priced
  per-model incl. cache-read/write, from the transcript (derived channel) — works for
  `--bg` unchanged. A **separate `wasted`/`crashed` cost category** records tokens burned
  by crashed/timed-out/failed runs *distinctly* from the real-work total (which stays
  historically comparable), shown in the stats line, `task_usage`, and per-run records.
- **`HarnessAdapter` seam + `--harness {claude,codex}` flag.** A **real CodexAdapter**
  runs `codex exec --json` as a detached fallback (no Agent View — caps `False`, so
  task-station renders its own board; tracking intact, display-loss only). Codex tokens
  are recorded UNPRICED (no codex rate sheet). *(Codex exec flags + NDJSON event names
  are UNVERIFIED pending a live capture.)*
- **Fail-closed `--bg` workers (default, no dangerous-skip).** Workers spawn with
  `--permission-mode dontAsk` + `--allowedTools` for the author-only edit toolset:
  non-allowlisted tools (git, network, arbitrary Bash) are auto-denied so an unattended
  worker never hangs on a prompt — exactly like the old `-p` workers, with NO
  `--dangerously-skip-permissions`. `delegate_bypass_permissions` config is **opt-in
  (default OFF)**: turning it on spawns worktree workers with `bypassPermissions`
  (needs a one-time `claude --dangerously-skip-permissions` acceptance) for users who
  want workers to run anything unattended.

### Changed
- `delegate` worker lifecycle re-architected around the adapter: launch → capture the
  launch-printed session id → poll `claude agents --json` → status-based exit
  classification (`idle`=ok, unlisted-before-idle=crash, watchdog=timeout), with the
  wall-clock watchdog killing the agents-row process group. The legacy `-p`/`codex exec`
  detached streaming path is preserved for non-bg adapters.

## [2.3.0] — 2026-07-20

### Added
- **Private weekly recap (F7):** `task-station recap` — a strictly-local, self-contained
  HTML digest of the week: headline tiles, model mix, where tokens went, work patterns,
  then guidance that leads with UNIVERSAL LLM strategy (prompting, context building,
  repeatability, token economy) before any tooling; a versioned model-role matrix
  (delegation/planning/research/review × model tier + effort) rendered against the
  observed week; deterministic flags (observation → suggestion → exact next action);
  optional curator over aggregates only. Costs shown as API-list-price EQUIVALENCE
  (never "spend" — team subscriptions) plus directional eco equivalence (kWh/CO₂/water,
  assumptions cited inline). An `invoked_by: human|model|both` feature registry
  guarantees model-invoked features are never recommended to humans. Opt-in weekly
  auto-generation (`config --recap on`), throttled, fail-open, excluded from every sync
  boundary.

## [2.2.1] — 2026-07-20

### Fixed
- **Identity-keyed fold-in (F9)** — fixes the live wrong-attach class (a PR-1115 review
  attaching to a "PR 1111" task on category flavor): the fold-in nudge extracts strong
  identity keys (PR#, story/work-item ids, AB#, forge URLs) and lists ONLY same-key
  candidates when the prompt carries a key; an unmatched key biases CREATE explicitly;
  candidate lines render their own keys ("#442 → PR 1111"); `attach` soft-blocks on a
  key mismatch (`--force-key` overrides). Keyless flows are byte-identical to 2.2.0.
  Bare "#N" is the lowest-precedence PR form — numbers claimed by work-item patterns
  ("story #3166") never misread as PRs.

## [2.2.0] — 2026-07-20

### Added
- **Brains are definable structures (F4):** `brains.json` schema v2 — per brain
  `description`, `purpose`, `keywords`, `repos`, `category_affinity` (core structure valid
  even when empty) + a derived block computed at read (open/active counts, recent focus,
  dominant categories, top signals, last activity) shown by `brains show` and the board.
- **Auto-attach (F4):** tasks are assigned to a brain automatically at create/edit —
  repo/cwd match (incl. worktree-stem `projectname-2704-x` → `projectname`) > title/summary keywords >
  category affinity > session skill; below threshold falls back to `main`; `brains suggest
  --task <n>` prints the full scoring table; `brains assign` pins a manual override the
  scorer respects forever. The user never names a brain.
- **Correspondence (F5):** `link` (pair my node with a peer node), `fork --from
  <alias>-<ref>` (new local task downloading the peer node's public digest, with
  provenance), `subscribe --on checkpoint,decision,trail` (peer feed updates mint memos,
  idempotent per feed rev), and per-node `trail_visibility: private|checkpoints|full`
  enforced at export by the sync_safe stripper (private is the default — trails never
  leave the machine unless deliberately published per node).
- **Artifact capture + cross-person auto-link (F6):** PR/work-item URLs in tool results
  are captured into the attached task's `prs[]`/`stories[]` (deduped, fail-open,
  worker-suppressed); when a captured signal matches a peer task in a mounted feed, the
  pair auto-links with a two-way memo — a review task finds the producing task by itself.

### Fixed
- Single-brain + interbrain-off boards remain behavior-identical to classic (law-tested).

## [2.1.0] — 2026-07-20

### Added
- **Graph drill-down (F3, interbrain only):** per-focus-level gravity wells — each brain a
  galaxy, a person's system centered on its brains' centroid, "Everything" centered at the
  origin; hover draws a smooth rounded clickable outline around the hovered person/brain/node,
  click zooms + recenters one level down (interbrain → person → brain → node), empty-canvas
  click ascends; navigation stays in sync with the focus strip both ways.
- **Smooth boundary blobs** (rounded Catmull-Rom hulls, owner-tinted) at person + brain levels
  with a graph-controls toggle; degenerate groups (<3 nodes) fall back to circles — the blob
  render is regression-tested with blobs on AND off, on 1/2/3/N-node groups.

### Changed
- Cross-galaxy edges curve between wells; within-galaxy edges stay near-straight. Node
  sizing (log tokens), memo pulses, perf toggle, and the 2D/3D toggle are unchanged; the
  3D camera reuses the existing fit-view/zoom-cap model at every navigation level (starts
  framed, never inside the field).

### Fixed
- With interbrain off the graph (and the whole board) renders exactly as classic — the
  drill-down/wells/blobs code paths are dormant (behavioral law, asserted in tests).

## [2.0.1] — 2026-07-20

**Repair release — the classic board is the shipping board again.** 2.0.0 made the v2
shell+feed engine the `/todo board` default, and that default shipped real behavioural
regressions: an empty board on first load until a button click, the closed list's
5-newest cap + "show more" gone, galaxy blobs crashing the graph, categories/settings
sections diverged from classic, the title hover-marquee gone, per-session expand/resume
gone, and the sidebar eating horizontal space. The strategy is now **inverted**: the
classic renderer (`tools/render_board.py`) evolves in place and IS the board; the v2
shell is demoted to an opt-in dev preview. The law going forward — **interbrain off ⇒
the board is behavior-identical to classic** — is pinned by
`docs/specs/BOARD-BEHAVIOR.md` and its automated asserts.

### Changed
- **`board_engine` default reverted to `classic`** (`config.board_engine()`). The v2
  engine is now reachable ONLY via the explicit `board --v2` flag (with `--classic` kept
  for symmetry). `board3` is a deprecated alias that maps to `board --v2`.
- Interbrain federation now renders **server-side through the classic pipeline** (see
  Added), not only in the v2 shell.

### Added
- **`docs/specs/BOARD-BEHAVIOR.md`** — the behavioral gate: a checklist of observable
  board behaviors any engine must satisfy (first-load rows, closed 5-cap + "show more",
  title marquee, per-session resume, categories/settings parity, graph blobs both ways,
  refuse-downgrade, rev auto-reload), each with a manual-verify recipe + whether a cheap
  automated assert backs it.
- **Server-side peer/org rendering (Interbrain, classic board)**: when
  `interbrain` resolves ON and peer feeds exist (`<data_dir>/feeds/{peers,demo}/*.js`,
  canonical `window.__TSFEED_… = {json};` form), foreign tasks are parsed and mapped into
  the classic view-model and rendered through the SAME row/section builders — an owner
  dot + alias chip before the title, a 🔒 read-only marker, memo-only actions (open/resume
  disabled with a "read-only · foreign brain" tooltip), and no sessions/prompts/resume
  sections. Foreign tasks NEVER touch the store. Foreign nodes + dashed cross-brain
  shared-signal edges also render in the existing task graph, coloured by owner.
  **Interbrain off ⇒ zero foreign view-models and a byte-parity render** (asserted).
- **Focus strip** — a compact top-bar chip row (`Everything · my brains · <peer> · <org>`)
  replacing any sidebar: focus exactly one brain or person (or none = Everything),
  filtering both the table and the graph, persisted in `localStorage`. Self tasks carry a
  display-only `handle` chip (`<owner>-<seq>`) next to the seq.

## [2.0.0] — 2026-07-19

**The visual board is now the shell + feed "Interbrain" (v2) engine.** `/todo board`
writes the same `<data_dir>/board.html` (+ `<data_dir>/feeds/` + `board.rev.js`), so
hooks, autorefresh, and the rev auto-reload survive untouched — but the renderer is the
new house-style, same-page table + 2D/3D galaxy graph with optional Interbrain
federation. The classic renderer (`tools/render_board.py`) is untouched and remains the
one-release fallback.

### Added
- **v2 board engine** (`lib/board3.py` + `tools/board3_shell.py`): real-board house style
  (imports `render_board._css()`), table + galaxy graph on one page, full parity with the
  classic board (sessions/hub cards, history, work-mix, glossary, prompt trail, categories
  panel, help panel, live-now strip, open-state persistence, refuse-downgrade, rev reload).
- **Interbrain federation**: peer/org feeds, mounts rail, brains & sharing model
  (`brains.json` sidecar + `task-station brains` CLI — never touches `tasks.db`), a brains
  manager panel whose Apply emits copyable CLI (a `file://` page can't write), owner
  galaxies / boundary blobs / curved cross-galaxy edges / memo pulses.
- **Config**: `board_engine` (`v2` default · `classic`), `interbrain` (`on`/`off`/`auto`),
  `org_label` (default "Org brain") — via `task-station config` + `--board-engine` /
  `--interbrain` / `--org-label` flags (and `--classic` / `--demo` on `board`).
- **`sync_safe`** contract: feed fields marked `local_only` (prompt trails, resume paths)
  are stripped by `board3.strip_local_only()` on any future share/sync export path.
- `docs/specs/BOARD3-PARITY.md` (the switchover gate) + `docs/specs/SHARING-NOTES.md`.

### Changed
- `write_board` routes by `board_engine`; `/todo board` defaults to v2. `board3` is now a
  deprecated alias for `board` (writes `board.html`).

## [1.97.0] — 2026-07-18

### Added
- `docs/specs/2026-07-18-two-machine-sync-design.md` — accepted v1 design for two-machine
  board sync: git-backed owner-partitioned full-state task JSON exchange, local SQLite
  authoritative, write-once `<owner>-<origin-seq>` cross-machine handle, Tasktrail untouched.

### Changed
- Memo bodies are no longer capped (was 4000 chars): a memo is inter-session correspondence
  and must arrive whole — a truncation once cut a design spec mid-sentence. The injection
  surfaces stay budgeted via the existing preview caps (feed preview at `EVENT_TEXT_MAX`,
  pending-brief lines at `MEMO_LINE_MAX` × `MEMO_PENDING_MAX`).

## [1.96.0] — 2026-07-17

### Added
- Board toolbar: a **session-state filter** (running / resumable / no session) beside the
  status dropdown — rows carry `data-sess` (best session state: running > resumable > none).
- Graph rail: a **"Tasks · status"** lifecycle filter group (open / active / closed, with
  counts), and a default-off **"unlinked tasks"** filter that pulls every undrawn task
  (relation-free, capped-out, or pruned) into the canvas as static outer-ring nodes —
  zero physics cost while hidden; "View in graph" now works for every task and
  auto-enables the filter; Reset returns it to off.
- Prompts: **per-prompt task attribution** — each captured prompt files under the task
  whose activity span contains it, so a session shared across tasks no longer dumps its
  whole trail on one owner (`PHASES_VERSION` 3→4 forces the one-time reattributing rescan).

### Changed
- **Vocabulary reconciled — one word, one meaning.** Task pill: `active` → **`live`**
  (a session is running now; green breathing) so "active" only ever means the stored
  lifecycle. Sessions: canonical **running / resumable / linked** everywhere — the
  Sessions header reads `2 hubs · 1 running · 1 resumable` (was `2 hubs (2 live)`), the
  terminal Sessions block shows `● running / ◐ resumable / ○ gone` (was live/gone), and
  the live-strip dot tooltips read working/idle. Old view-model values still render.
- Graph: node hover no longer jumps the canvas — the node-details panel is fixed-height
  with internal scroll, and a ResizeObserver keeps the drawing buffer synced to any
  layout resize. Zoom-out cap tightened slightly.
- Work-mix bar/legend colours are keyed by **phase label** (planning is always the same
  colour, in every bar), not bar position.
- Prompt trail: harness/skill noise (`<tag>` wrappers, `Base directory for this skill:`,
  `[Request interrupted…]`) no longer counts as human; each prompt's reply now shows
  Claude's **entire response tail from its last bullet** (or last paragraph), with line
  breaks preserved on the board.

## [1.95.0] — 2026-07-16

### Changed
- **The 5-hour and Week cost rows are now stddev-colored, like the Session row.** They
  were banded against fixed thresholds ($0.01/$0.05 for 5-hour, $50/$150 for Week) —
  so low that any real usage pegged both rows permanently RED. Each row now colors its
  `$` against μ / μ+σ of its **own** population of historical *closed-window* totals,
  reconstructed from the ledger and anchored to the same rate-limit resets the `% left`
  figures use. `usage.ledger_totals` buckets each priced session's cost into
  anchor-aligned 5-hour and weekly bins (attribution by last activity, current
  in-progress window excluded) and returns `five_hour_bucket_costs` /
  `week_bucket_costs`; `hud._dist_thresholds` (generalizing `_session_thresholds`)
  computes the bands, falling back to the old fixed pairs below 3 closed windows.
  Accepted approximations, documented in-code: rolling 5-hour boundaries are projected
  from the current anchor, and a session straddling a boundary counts wholly in its
  last-activity bin.

## [1.94.0] — 2026-07-16

### Fixed
- **The board refuses to downgrade.** `board.html` is stamped with the plugin version that
  rendered it (`<meta name="ts-board-version">`), and the Stop-hook auto-refresh
  (`write_board(guard_downgrade=True)`) now skips the write when an existing board was
  rendered by a *newer* version — so a stale, older-version session running in parallel can
  no longer clobber a current board back to old markup. An explicit `board` / `/todo board`
  always writes.
- **Task Graph fit frames every node.** The fit now recenters the layout's centroid to the
  origin and fits both axes independently, fixing the 3D vertical bias (top nodes clipped by
  the initial zoom) and the 2D "scattered / off-centre after Reset" symptom. Stronger
  centering gravity keeps weakly-connected nodes from drifting far out, so Reset settles into
  a compact, balanced equilibrium instead of flinging nodes apart.

### Changed
- **Graph canvas matches the filter rail height** (no longer a short fixed box).
- **Smart show/hide-all filter toggles.** Each filter group's toggle-all label follows the
  actual row states (all on → "hide all", otherwise → "show all"), and Reset (↻) now also
  resets the filters + search back to all-on.
- **Wording:** the header toggle is spelled out **"performance"**; the graph hint drops
  "(fling to spin)" and reads "click empty space to toggle rotate".

### Added
- **`scripts/ship.sh`** release helper + **`docs/RELEASING.md`** (from 1.93.x): wraps
  ff-merge → test → version bump → CHANGELOG stop → commit/push, and now runs the tests
  *after* the merge so they validate exactly what ships.

## [1.93.0] — 2026-07-16

### Changed
- **Live is folded into a 4-state status.** The separate `live` activity column is gone;
  the running-session signal now folds into one status field — **new · paused** (in
  progress, no live session — yellow) **· active** (a session is running now — green)
  **· closed**. Computed server-side from the running-session set, so the status pill,
  the whole-row breathing (an active task breathes green across its summary + expanded
  detail), the category counts, and the status filter all agree. The Live-sessions strip's
  active dot uses the same green.
- **Rows expand independently.** Any number of task rows can be open at once (no more
  accordion), with a quick slide-in animation; collapsing a row resets its inner sections
  to their defaults, and a manual/browser refresh resets all transient view state (theme
  and the performance mode persist). The Task Graph and each row's Overview default open.
- **Digest tidied.** Steps / PRs / Files collapse (collapsed by default) when there are
  many; the session-tree counts moved onto the Sessions section header; "View in graph"
  is a small chip (no boxed section, no ↗ glyph); "Open the task" is content-sized, not
  full-width; related `#N` references are clickable and open the counterpart's row.

### Added
- **Task Graph interaction overhaul.** 2D drag-to-pan; an orbit fling gives rotate
  momentum that decays then resumes auto-rotate; Reset is a hard, immediate re-settle to
  equilibrium + re-fit; the initial zoom fits all nodes and the zoom caps scale to graph
  size (you can zoom out much further); one filter row per signal hub (each repo / PR /
  story) with a per-group show/hide-all toggle.
- **Performance toggle (high / low).** A persisted header toggle that disables animations
  and switches the graph to on-demand rendering for slower machines.

## [1.92.0] — 2026-07-15

### Changed
- **Lifecycle status reads new / in progress / closed.** The middle state (stored `active`)
  now displays as **"in progress"** everywhere user-facing — freeing "active" for the
  live-session activity concept. Stored values, the `status` CLI, and `--active` are
  unchanged; legends read new → in progress → closed.
- **The activity column has a name.** The per-task live-session dot column is now labeled
  **"live"** (was a bare dot) with an **active / inactive** tooltip; a task with a running
  session shows the breathing "active" dot.
- **Task Graph spacing scales with complexity.** The force layout grows rest lengths,
  repulsion, and the hub ring with node count (and zooms to fit), so dense graphs spread
  out instead of overlapping.

## [1.91.0] — 2026-07-15

### Added
- **Task Graph — a clustered, interactive board relations graph.** The old flat-circle
  "Task relations" mini-graph is now **Task Graph** (expanded by default): tasks cluster
  around **category hubs** and the **PR / repo / story** they share (a signal becomes a
  hub only when ≥2 tasks share it, so singletons stay per-task attributes). A deterministic
  server-rendered SVG is the no-JS/accessibility fallback; on top of it a self-contained
  `<canvas>` layer adds a **2D⇄3D toggle** (3D default) with a force-directed layout,
  orbit/drag, hover-isolate, click-to-center, click-empty-canvas to toggle auto-rotate,
  multi-select filters per node type (with counts), and a per-task **"View in graph"** button.
- **Activity indicators.** A new `live` column shows a per-task activity dot that breathes
  (staggered "waterfall") when a task has a running session; the Live-sessions strip shares
  the same single dot. Session state vocabulary unified as **running / resumable / attached**
  (session cards) vs **active / idle** (live activity), each with hover descriptions.

### Changed
- **Board columns.** Removed the top-level **Steps** column; **Cost** and **Story** moved
  into each task's **Overview** (cost keeps its interactive model-mix bar).
- **Live sessions** are now a collapsible, count-labeled (`Live sessions (N)`) fixed list —
  expanding a session no longer displaces its neighbors.
- Displayed timestamps render in **local time** (storage stays UTC).

### Fixed
- Configs panel options no longer overflow their container.

## [1.90.0] — 2026-07-15

### Fixed
- **1M context-window detection no longer depends on the cost HUD.** 1.89.0 captured
  the harness-authoritative `context_window_size` only in `hud.observe()`, so with the
  HUD off (`config --hud off`) a 1M session was still mis-sized to 200k and the
  auto-checkpoint nudge fired ~5× too early. The statusline payload is the ONLY channel
  Claude Code exposes `context_window_size` on (no hook receives it — verified against
  the docs), so capture now also happens in the always-on `--statusline` provider path.

### Added
- **`persist_harness_context_window()` + `_read_statusline_stdin()`** — `cmd_whoami
  --statusline` now reads the piped statusline payload and merges `context_window_size`
  into the per-session snapshot (the same file `harness_context_window` reads), gated to
  the HUD-**off** case so it never races the HUD provider that already captures it. The
  generated `50-task-station.sh` provider now pipes the payload through to `whoami`.

### Changed
- Context-window capture now depends on the **statusline** being on rather than the cost
  HUD specifically — the lowest achievable floor, since no hook channel carries the size.
  With the statusline fully off, task-station falls back to model-id derivation (which
  still misses a runtime `/model` 1M pick — set `context_window` explicitly to override).

## [1.89.0] — 2026-07-15

### Fixed
- **Auto-checkpoint now honors the 1M context window** — the proactive `/todo save`
  Stop nudge no longer fires ~5× too early on a 1M-context Opus session. The window
  the `checkpoint_pct` trigger measures against was derived from the transcript model
  id (the harness strips the `[1m]` marker from `message.model`) and from
  `claude_code_model_selection()` (env / `settings.json` only) — both blind to a
  runtime `/model` 1M pick, so a 1M session was sized at 200k and read ~5× over-full.

### Added
- **`harness_context_window(session)`** — reads the authoritative window size the
  harness itself reports (`context_window.context_window_size` from the statusline
  payload, `1000000` vs `200000`), persisted per-session by `hud.observe()` into the
  HUD snapshot. `effective_context_window` now consults it right after an explicit
  user override and before any model-id derivation, so a 1M session is sized at 1M
  even when the marker never reaches the transcript, `settings.json`, or the env.

### Changed
- **Auto-save keys strictly on session context %, never the 5-hour / weekly windows** —
  the checkpoint trigger reads only the transcript's resident context tokens against
  the real window; `rate_limits` (five_hour / seven_day) never enter the decision.
  Locked in by test (`StopNudgeUsesSnapshotWindow.test_rate_limits_never_trigger_save`).

## [1.88.0] — 2026-07-14

### Changed
- **Story hubs: threshold >=2 → >=1** — every cited story gets a hub page + board/Related links, so a story referenced by even a single task materializes its `stories/<id>.md` page and `[[stories/<id>]]` link instead of waiting for a second reference.

## [1.87.0] — 2026-07-15

### Added
- **Story groups**: every story referenced by >=2 tasks gets a machine-maintained hub page (`task-station/stories/<id>.md`, ADO link when known), and member task notes add `[[stories/<id>|Story <id>]]` to `## Related` alongside their category link — an orthogonal, cross-category axis, derived from the structured `story:` data (never title text). Regenerate/dissolve per sync; `config --obsidian-story-groups on|off` (default ON within category hubs).
- **STORY column on the HTML board**: per-row story ids (max 3 then `+N`), linked to ADO when the stored entry carries a URL. Terminal board deliberately unchanged (width contract).

## [1.86.1] — 2026-07-15

### Fixed
- Sub-group detection: a category's own name/tag tokens can no longer form a sub-group inside that category (`migration/migration`), and a token only clusters in the single category where it's most frequent — ties cluster nowhere (`story` no longer groups in two categories at once).

## [1.86.0] — 2026-07-15

### Added
- **Emergent sub-groups inside category hubs**: a distinctive token appearing in >=3 of a category's task titles (and rarely elsewhere) automatically becomes a nested sub-hub — `categories/<cat>/<token>.md` — with members linking it instead of the bare category (task -> group -> category tree in graph views). Deterministic (no LLM), regenerated every sync (backfill is inherent), dissolves below 3 members, never touches user-created files. `config --obsidian-subgroups on|off` (default ON, nested inside category hubs).

## [1.85.0] — 2026-07-14

### Added
- **Category hub pages** for the Obsidian mirror / generic export: every task note links its category (`[[categories/<slug>|<TAG>]]` in `## Related`) and machine-maintained hub pages at `<target>/task-station/categories/<slug>.md` list their tasks back — graph views cluster by the task-station taxonomy instead of floating orphan task nodes. Regenerated on every sync (incl. single-task syncs), pruned when a category empties, owner-scoped under shared vaults. `config --obsidian-category-hubs on|off` — **default ON** (the mirror itself is already opt-in); turning it off drops the links and prunes the hubs on next sync.

## [1.84.1] — 2026-07-14

### Fixed
- `task.snapshot` / `task.checkpoint` digest now carries `title`, `status`, and `closed` — a stream-only consumer bootstrapping from `--backfill` snapshots could never learn task identity/state (titles folded to None).

## [1.84.0] — 2026-07-14

### Added
- **`/glossary`** (`/task-station:glossary`, `/todo glossary`, bare alias): per-task canonical vocabulary — `{name, layer, state, def}` terms, case-insensitive unique names, add/edit/rm/list + `--rename`; terms auto-inject into every attached session's context so plans, ADO items, and dialogue reuse the same words; `glossary-context` subcommand as the host-agnostic adapter hook; glossary text is FTS-searchable and changes emit Tasktrail `task.updated` events.
- **`/brief`** (`/task-station:brief`, `/todo brief`, bare alias, `brief` skill): deterministic house-style HTML one-pager per task — model supplies a brief-spec JSON, never HTML; `lib/brief.py` (pure stdlib) renders decision/transition/glossary/one-rule/plan/ADO-tree/diagrams/provenance sections through the frozen `lib/brief_template.html` style (contract-pinned by test to its committed source); output lands at `<artifacts_root>/<project>/<seq>-<slug>/brief.html` and persists `brief_path` on the task (carried into note frontmatter + checkpoint digests).
- `config --artifacts-root`: root for rendered artifacts, derived from the data dir by default (`TASK_STATION_ARTIFACTS_ROOT` env override).
- Golden-file test locks the brief house style byte-for-byte; +90 tests total (1718).

### Fixed
- `--bare-cmds` help, installed-check, and uninstall-leftovers now list the full alias set (incl. `/unpin`, `/prompts`, `/glossary`, `/brief`); README version badge synced.

## [1.83.0] — 2026-07-14

### Added
- **Tasktrail event ledger** (`lib/stream.py`): durable, append-only JSONL event log at `<data_dir>/stream/events/YYYY-MM.jsonl` with a versioned manifest — every CLI + MCP mutation emits a typed event (`task.created/updated/status/checkpoint/event/relation/deleted/redacted/snapshot`); per-task monotonic counters make gaps detectable; default ON, local-only, zero egress; `config --stream on|off`, external tee via `config --stream-dir` (default OFF).
- `task-station stream --since <cursor>|--tail|--backfill|--verify` and first-class `task-station redact --task N` (rewrites shards to stubs, bumps manifest generation).
- **Tasktrail spec v1.0** (`docs/spec/TASKTRAIL.md`): the published episodic contract — file convention, envelope, ordering, bootstrap, tombstones, redaction/generation, governance; JSON Schemas (2020-12) + stdlib validator (`spec/validate.py`) + golden conformance fixtures; producer self-conformance test in CI. (Named Tasktrail after a TaskStream collision check.)
- **Second reference consumer** `spec/consumers/taskstream-digest.py` → plain `digest.md`, proving the contract needs no Obsidian; byte-identical fixture replay in CI.
- **Note/export contract v2**: frontmatter gains `schema-version: 2`, `uuid` (in-band identity), real `closed` timestamp, and `glossary`/`brief_path` when present; generic export now carries the relation graph; `## Related` links resolve (`[[stem|title]]`).
- **Owner scoping for shared vaults**: `config --owner <handle>` nests notes under `<target>/<owner>/`, stamps frontmatter/manifest/daily lines; unset = byte-identical legacy layout.
- `export --prune --dir` reconciles orphaned notes; task deletion purges vault/export notes + sidecar entries and always emits a tombstone.
- Store: stable in-band task `uuid` (== task id) + real `closed_ts` with idempotent migrations; transactional unique seq allocation (UNIQUE index + `create_with_seq`, dedup migration); optimistic concurrency (`rev` column + `store.mutate()`), 7 high-collision mutation paths converted — multi-session lost updates fixed.

### Fixed
- `export --since/--status` no longer clobbers `index.md` to the delta — merges into the full index.
- Concurrent task creation can no longer mint duplicate seqs.

### Removed
- Dead code: `JsonBackend`, legacy JSON-store migration (pre-SQLite installs must upgrade through <=1.82 first), obsidian legacy-path migration + `--migrate-path`, `_OPUS47_FAST` pricing sheet, vestigial `tint_escape` mode param, two stale internal docs; `stack_map.py` regenerated self-contained (1102 -> 112 lines).

## [1.82.2] — 2026-07-11

### Fixed
- **`/done` no longer auto-closes your terminal window by default.** The no-arg
  `/done` used to close the session's window ~1s later — but a model can invoke
  `/done` via the Skill tool (Claude Code's `disable-model-invocation` does not gate
  the Skill tool), and there is no reliable signal to tell a human-typed `/done`
  from a model one. So the window auto-close is now **opt-in, default off**: `/done`
  closes the task and detaches but leaves the window open. Enable the old behavior
  with `config --done-closes-window on` (env override `TASK_STATION_DONE_CLOSES_WINDOW`).
  The `--task N` form never closed a window and is unchanged.

## [1.82.1] — 2026-07-11

### Fixed
- **HTML board no longer shows stale task status after a close/reopen/status change.**
  `board.html` previously regenerated only at turn-end (the Stop hook) or on an
  explicit `/todo board`, so closing a task left the persisted board showing it as
  active until the next turn — and `/done` compounded it by detaching the session
  and auto-closing the window, which can truncate that Stop-hook refresh. A new
  `maybe_refresh_board()` helper (the same autorefresh-on + board-exists gate the
  Stop hook uses) now runs synchronously at every status transition — close (single,
  batch, and provisional-discard), reopen, and open⇄active — so the board reflects
  the change immediately, independent of Stop-hook timing.

## [1.82.0] — 2026-07-11

### Added
- **Live worker progress observability for `delegate`.** A delegated worker now
  streams a compact activity feed as it works instead of going dark until the end.
  `run_worker` moved from a blocking `subprocess.run` to a `Popen` over `claude -p
  --output-format stream-json --verbose`: each meaningful event (tool calls, text,
  errors) prints one progress line to **stderr** — so the stdout stays byte-for-byte
  equal to the relayed result — and the terminal `result` event is round-tripped
  through the unchanged result parser (cost/model/usage/session relay is untouched).
  Launch a delegation with `run_in_background: true` to watch the feed live in the
  background-task inspector.
- **`delegate status` + liveness in `delegate list`.** `delegate status
  [--seq/--project/--repo] [--label] [--all]` reports true liveness per worker —
  `● running` (pid alive, verified by `ps comm` to survive pid reuse) vs `○ not
  running — session resumable` vs `○ finished (ok|timeout|crash)`, plus the
  worktree's git state (branch, last commit + age, dirty/untracked counts), current
  phase, and the resume command. `list` gains the same liveness glyph per row
  (legacy pre-upgrade entries render `?`). A quiet-but-alive worker is never
  mis-reported as dead.
- **`scripts/release.py` — single-source version bumper.** `release.py --set X.Y.Z`
  or `--bump {major|minor|patch}` rewrites all four version-bearing files
  consistently (plugin.json, marketplace.json, the README badge, and a new CHANGELOG
  entry scaffold) with targeted minimal-diff edits, then cross-validates them. Ends
  the manual four-file bump that let the README badge silently drift.

### Changed
- **Registry heartbeat.** Worker registry entries now carry `pid`, `started_ts`,
  `last_event_ts`, `phase`, and `exit`, updated during the run under an `fcntl`
  lock (with a per-process temp file), so concurrent `--label` workers can't clobber
  each other's pre-registered session id.

### Fixed
- **Abnormal-exit worker edits are auto-checkpointed.** On a worker timeout or crash,
  `delegate` now WIP-commits the worktree's in-progress edits (`--no-verify`, never
  pushes, never touches the main checkout) so completed work never needs a manual
  rescue; the commit sha is surfaced in the task feed and the error message. A worker
  timeout is now enforced by a watchdog that kills the whole process group, so
  `claude`'s grandchildren can't keep writing the tree after the checkpoint.
- **README version badge** is now guarded by `tests/test_manifest.py` (badge ==
  plugin.json version, and CHANGELOG has an entry for the current version), so a
  release can no longer ship with a drifted badge. Badge corrected from a stale
  1.59.0.

## [1.81.0] — 2026-07-11

### Fixed
- **Context-pressure nudge now sizes to the real window on 1M-context sessions.** On an
  Opus-1M session the transcript records `message.model` with the `[1m]` marker stripped
  (Claude Code removes it before the API call), so the checkpoint math divided by 200k and
  read ~5× over-full — firing "84% used" when the session was really ~16% used / 84% left.
  The window is now derived from the Claude Code model *selection* (`ANTHROPIC_MODEL` env →
  `~/.claude/settings.local.json` → `settings.json`), which retains the marker, and upgraded
  to 1M only when that selection is genuinely larger **and** of the same model family as the
  transcript model (so a `--model sonnet` session under an `opus[1m]` default is not
  inflated). An explicit `TASK_STATION_CONTEXT_WINDOW` / `context_window` override still wins.

### Changed
- **Board: Decisions and Summary sections are collapsed by default.** Each is now a closed
  `<details>` per task (the Overview stays open), so long decision logs no longer dominate the
  board; click to expand.
- **Board prompt trail: human prompts only, each paired with Claude's last-bullet reply.** The
  board now reuses the same human-filter + last-bullet-reply machinery as the markdown/CLI
  views (`↳ reply` under each prompt), and renders the **full** human trail rather than a
  truncated preview.

## [1.80.0] — 2026-07-11

### Added
- **Deterministic task-reference resolver in the prompt hook.** When a prompt names a task
  by `#seq`, `task N`, `todo N`, `seq N`, or `[hex-id]`, the `UserPromptSubmit` hook now
  resolves it (via `resolve_ref`) and injects the bound row
  `#<seq> [<id8>] "<title>" · <status> — digest: task-station search --detail <seq>` — so an
  agent is handed the mapping instead of scanning the board or guessing a seq. Silent when
  nothing resolves; fires in every branch (tracked, untracked, skipped, explicit-intent).

### Changed
- **Injected board rows now lead with `#seq`.** SessionStart's "You have N open task(s)",
  both attach/nudge lists in `cmd_prompt_context`, the edit-nudge brief (`_open_tasks_brief`),
  and the "while you were away" head all render `#<seq> [<id8>] <title>` — the human task
  number the user actually speaks is now visible in context, not just the internal hex
  short-id. Fixes the failure mode where a spoken "task 387" couldn't be mapped to its task.

## [1.79.1] — 2026-07-11

### Fixed
- **Memo pending-brief truncated the ack ledger for long bodies.** The per-memo line
  truncated the WHOLE line (body + ledger) to `MEMO_LINE_MAX`, so a long pasted body could
  chop off the "acked by: …" ledger — the anti-double-implement signal — in the primary
  real-world use case. Now only the body is truncated, against a reserved budget, so the
  ledger always survives.
- **Ack hint now points to `/todo memo ack`.** The pending-brief footer told the model to
  run `task-station memo ack … --session <your-session-id>`, but a model reading injected
  context doesn't know its own session id. It now points to `/todo memo ack <id8>`, which
  auto-resolves the current session.
- **`_trim_memos` no longer drops unacked memos on a session-less task.** The soft cap
  (`MEMOS_KEEP`) treated a memo as fully-acked whenever no session was registered on the
  task, so unacked memos on a cross-task target nobody had attached to yet could be
  silently trimmed. An empty `sessions` list now means "nobody could have acked" and never
  soft-trims; the hard cap (`MEMOS_HARD_CAP`) still bounds a pathological pile-up.

## [1.79.0] — 2026-07-11

### Added
- **Memo correspondence — hand a fact/decision to a task's working session(s) without
  copy-paste.** `task-station memo send --task <ref> --text <body>` posts a memo onto any
  task (attached or not — cross-task via `resolve_ref`). Every Claude Code session attached
  to that task passively sees a `[task-station] N memo(s) awaiting YOUR ack` block on its
  next turn (UserPromptSubmit + SessionStart injection), and each session explicitly acks —
  signed with its session id — on a **shared, visible ack ledger**, so multiple sessions on
  one task never double-implement the same change. Acking is idempotent and MAY optionally
  promote the memo to a decision (`memo ack … --decision [TEXT]`). Memo bodies (≤ 4000
  chars) live in `task["memos"]`, not the event feed; the feed carries only a capped
  preview event whose id is shared with the memo. Surfaces: the `memo send|ack|show` CLI,
  `/todo memo <n> <text>` · `/todo memo ack <id8> [TEXT]` · `/todo memo show [<n>] [<id8>]`,
  the MCP `send_memo` / `list_memos` / `ack_memo` tools (Desktop signs with a free-text
  signature), plus new "Memos" sections in the `/todo <n>` detail and `/todo <n> history`.
  The pending brief is ack-gated, not seen_ts-gated — it re-surfaces every turn until you
  ack, so a handed-over fact is never silently missed.

### Changed
- **Every event now carries a stable 32-char hex `id`** (`add_event`), displayed as
  `id[:8]`; `add_event` returns the appended event. Legacy events without `id`/`sid` render
  exactly as before (all readers use `.get`).
- **Reliable event attribution.** `session` is threaded end-to-end so authored events carry
  a `sid`: `append_decision`, `append_history`, `set_status`/`promote_active`, `cmd_status`
  (new `--session`), `cmd_post_compact`, and `_update_one` (the `scope updated:` log event).
  `touch` gains a `register=False` mode so a cross-task scope update attributes its log
  event without enrolling the acting session as a worker on the target task.

### Fixed
- **Unattributed decision / milestone / scope / status events.** These previously landed on
  the event feed with no `sid`, so a resumed session's delta brief couldn't tell its own
  work from another session's; they are now correctly attributed to their author.

## [1.78.0] — 2026-07-09

### Changed
- **The prompt trail is now the curated view by default: your prompts + Claude's reply.**
  `prompts`, `/todo <n> prompts`, `--md`, and the MCP `get_prompts` tool now show ONLY
  genuine human-typed prompts, each followed by Claude's last-bullet reply (`↳ …`) read
  from the transcript — instead of the full firehose that buried real prompts under slash
  commands, compaction rows, and the expanded bodies of `/todo`/`/save` invocations. The
  reply is the last bullet (or last line) of the assistant turn that followed each prompt,
  found across any tool round-trips in that turn. `--all` restores the complete raw trail
  (every kind, no replies); `--json` is unchanged (raw rows for tooling).
- **`_prompt_is_human` also filters expanded slash-command bodies.** A slash command is
  recorded as a user turn whose text is the command file's markdown (bare aliases lead
  with a `<!-- task-station-managed: … -->` marker); those `<!--`-led rows are now
  excluded, so they no longer masquerade as human prompts here or on the board.

## [1.77.0] — 2026-07-09

### Changed
- **Board file links auto-detect your editor (no more hardcoded vscode).** `--editor-scheme`
  now defaults to auto-detection when unset: a `$VISUAL`/`$EDITOR` GUI-editor hint, then
  the first known editor app found in /Applications (Cursor, Zed, VS Code, VS Code
  Insiders, Sublime, PyCharm, IntelliJ), else the neutral `file` scheme (a plain
  `file://<abspath>` the browser/OS handles). A `file` scheme renders `file://…` rather
  than the editor `<scheme>://file/…` form. `TASK_STATION_EDITOR_SCHEME` / the config key
  still win; `--editor-scheme` with no value restores auto-detect. (Note: macOS has no url
  scheme that opens an arbitrary file in its per-type default app from a static page, so
  this targets the code editor board file links most often want.)
- **Board "live" is now three honest states, not one green dot.** The board conflated
  three different notions of "live" under a single "● live" badge — including a
  transcript-derived hub badge that *survived a crash*. Each hub/worker card now shows a
  distinct badge: **● running** (a real live Claude process — true pid liveness, threaded
  in from `live_sessions.running()`), **◑ resumable** (a transcript exists so resume
  works, but nothing is running), or **○ attached** (linked to the task, no live
  transcript). The strict `live` bit now means *running*, so it no longer reads as live
  after a crash. To make a session not-live without `/done`: `detach` (removes it) or
  `skip` (keeps it attached but never a resume target).

## [1.76.0] — 2026-07-09

### Added
- **`/unpin` (and `/task-station:unpin`, `/todo unpin`).** The inverse of `/pin`: drop a
  task's pinned resume session so `/todo` reverts to the most-recent-substantive
  heuristic. Bare `/unpin` unpins the task THIS session is attached to (symmetric with
  bare `/pin`); `/unpin 13` or `/unpin 1,2,5` unpins by number. The engine `unpin`
  command previously required `--task` and had no bare-session path, `/todo` route, or
  command file — all now wired, including the bare-alias generation.
- **`--knowledge-graph` is now a first-class, visible setting.** The second-brain tier
  (task↔note co-citation edges in the board mini-graph, the per-task "Related knowledge"
  panel, and `## Related` wikilink emission into the Obsidian mirror) had an accessor and
  an env override but **no CLI setter, no config-board row, and no reset key** — it was
  effectively invisible. `task-station config --knowledge-graph on|off` (+ `-get`), a
  config-board row explaining it, and inclusion in `--reset` now expose it. Still default
  off and inert without an `--obsidian-vault`; after enabling, run
  `task-station obsidian --sync-all` to backfill `## Related`.

## [1.75.0] — 2026-07-08

### Fixed
- **Numeric lookup resolves a task.** `search <n>` and `/todo search <n>` now treat a
  bare all-digit query as a lookup by the task's display number (`#seq`) and print that
  task's read-only digest, instead of running a text search that falsely reported "no
  match". Falls through to text search when the number isn't a seq, so a PR/story number
  still matches by content.
- **Model-aware context window (checkpoint no longer over-fires).** The auto-checkpoint
  pressure trigger sized the context percentage against a fixed 200,000-token window
  regardless of model. On a 1M-context model (e.g. Opus 4.8 `[1m]`) that read the window
  as ~5× over-full and prompted a `/todo save` on almost every Stop, with a "% used"
  figure that looked inverted next to Claude's native "% left". The window is now derived
  from the model actually in use (`pricing.context_window_for`): 1,000,000 for a 1M
  variant, 200,000 for Haiku/Sonnet. An explicit `context_window` config / the
  `TASK_STATION_CONTEXT_WINDOW` env still wins.
- **Board: full session resume on small tasks.** A task with no per-hub cards (small
  tasks whose hub session was never recorded) showed only the `/todo <seq>` recap and no
  real session resume. The board now falls back to the computed `resume_main`
  (`cd … && claude --resume …`) so every task with a recorded session surfaces a
  clickable-to-copy resume command.

### Changed
- **Board links open in a new tab.** External PR/story URLs now render with
  `target="_blank" rel="noopener noreferrer"` so a default-browser router (Safari +
  BrowserFairy / basicurlrouter) handles them, instead of navigating the board page
  away. Internal `#task-<seq>` anchors and `vscode://` file links are unchanged.

## [1.74.0] — 2026-07-08

### Added
- **A richer task graph — semantic relations, universal.** The task graph now derives
  `touches-same` edges automatically from signals it already records: two tasks that
  share a PR, a story, an overlapping file, or a repo/project are linked, weighted by
  how strong the shared signal is (a shared PR outranks a shared repo). These are
  derived on the fly, never stored, and cost nothing — every user gets the better
  graph with no configuration.
- **Board mini-graph.** `/todo board` now renders a small, collapsible "Task relations"
  panel: an inline SVG that lays your related tasks on a ring and draws their edges —
  lineage (spawned-from / related), semantic `touches-same` (dashed), and shared
  knowledge (dotted, when enabled). Self-contained (no script, no external assets),
  theme-aware, with native hover tooltips. Omitted entirely when a board has no
  relations, so a fresh or unrelated board looks exactly as before.

### Added (opt-in — second brain)
- **Knowledge graph (default OFF).** A new `knowledge_graph` flag
  (`TASK_STATION_KNOWLEDGE_GRAPH=on` or `config`) turns on the second-brain tier, which
  additionally requires a configured Obsidian vault. When on it adds: task↔task
  **co-citation** edges (two tasks citing the same `[[note]]`) to the mini-graph, a
  per-task **"Related knowledge"** panel listing the notes a task cites, and a
  `## Related` **wikilink section** in each exported Obsidian note so the vault's graph
  view wires task↔task↔knowledge. With the flag off — the default — none of this runs
  and the board, notes, and graph are byte-identical to before.

## [1.73.0] — 2026-07-05

### Changed
- **Board: breakdown-bar hover is now informative.** Hovering a model-mix or work-mix
  segment shows a styled tooltip (label · share · output tokens · derived $ where known)
  instead of only recolouring. The bars are non-clipping with rounded ends preserved.

## [1.72.0] — 2026-07-05

### Changed
- **Board: the expanded task panel is now PER-HUB.** The standalone "Usage & Cost" and
  "Prompts" sections are gone; instead each hub session renders its own card that bakes
  in — its prompts (all of them, untruncated, with human-typed vs Claude/slash-generated
  styled distinctly), its cost + model-mix + work-mix, and its nested worker sessions
  (each worker a sub-card with its own resume command, cost, and work-mix drill-down).
  The "main" hub (the one `/todo <n>` resumes) floats to the top with a distinct green
  highlight; the pinned hub keeps its accent highlight. The redundant "Resume the hub
  session" block is removed (all hubs are shown). Ledger sessions with no recorded hub
  fall into a trailing "unattributed" pseudo-hub so nothing is lost.
- **Board: cost figures match the HUD costbar** — token counts in muted blue, `$`
  figures banded green/amber/orange by a μ/σ (stdev) calculation over the task's
  per-session derived cost.
- **Board: the collapsed row's Cost column carries a compact model-mix bar** again,
  above the derived-$ text.
- **Work-mix "other" is shrunk and drillable.** `phases.py` (v3) classifies more
  Bash/MCP signals into real phases, and the scan now captures which tool/command names
  fall into "other" so the board shows the top contributors. (Bumping the phase version
  triggers a one-time rescan.)

## [1.71.0] — 2026-07-05

### Changed
- **Board: live-session chips are colour-coded by role** (hub / worker / other) and
  **hovering a chip highlights its linked task row** in the table below (and the chip
  itself lifts).
- **Board: normalized every scrollable box** (Summary + all `.longlist` accumulation
  boxes) — one contained-overscroll behaviour and one thin, theme-coloured scrollbar.

## [1.70.0] — 2026-07-05

### Changed
- **Board: uniform main-table header.** The column headings
  (STATUS · # · TASK · CATEGORY · EFFORT · STEPS · COST · ACTIVITY) reused the per-column
  data classes, whose font-size/weight/colour bled in and made each heading look
  different. They now share one style.
- **Board: steps column is colour-coded by completion** — the mini progress bar + N/M
  tint green (done) / amber (≥50%) / orange, matching the HUD costbar bands.
- **Board: stories render above PRs**, and **Decisions is grouped directly above the
  Summary** (the two long-accumulation boxes sit together, reasoning trail first).
- **Board: fixed the title hover-marquee.** Long titles now slide left↔right on hover
  via a robust `text-indent` transition (the old `scrollLeft`/`overflow-x` approach
  didn't move), snapping back instantly on mouse-out.

### Added
- **`tools/deploy.sh`** — one-shot merge-current-branch → push → reload-installed-plugin
  (marketplace reset → cache/<version> → engine symlink → installed_plugins.json →
  import smoke-test). Refuses on main / dirty tree / un-bumped version.

## [1.69.0] — 2026-07-04

### Changed
- **Cost HUD: the Turn row is gone.** The Session row already carries the live session
  cost; the standalone per-turn `$` line was redundant. `turn` is dropped from
  `--hud-rows` (a persisted `turn` is silently ignored).
- **HUD header separator.** A dim `│` now separates the `⏺ <model>` badge from the
  inline task segment on the first line.
- **HUD Task row shows the task number** (`Task #<seq>`) in its label, and its derived-`$`
  colour now uses the same ledger μ/σ (stdev) bands as the Session row — not a fixed band.
- **HUD 5-hour row shows a `$` figure.** The derived cost + output tokens spent in the
  current 5-hour window (from the usage ledger, `[reset−5h, now]`) render on the 5-hour
  row alongside the `% left (↺reset)`.
- **Auto-checkpoint nudge wording.** The context-pressure nudge now reads
  `~83% used · ~17% left (~166k/200k tokens)` so the figure can't be misread against
  Claude's native "% left" indicator. The trigger is unchanged — it reports % USED and
  fires as the window fills (used ≥ `--checkpoint-pct`).

## [1.68.0] — 2026-07-04

### Changed
- **Cost HUD: the 5-hour and week rows always render — including their reset
  timestamps — even on a fresh session.** Rate limits are account-level and persist
  across sessions, but a new session's first status-line payloads omit `rate_limits`.
  The HUD now caches the last-seen used% + reset for BOTH windows
  (`resolve_rate_limits`, superseding the week-only anchor) and folds them back in on a
  miss, so the rows appear immediately instead of costbar's `—` / no-reset placeholder.
  The reset timestamp now renders independently of the utilization %.
- **Eco footprint on every token-bearing row.** The per-task row now carries an
  `≈ <comparison>` eco suffix like the Session/Week/Total rows (previously suppressed).
- **Model badge fully violet.** The status-line header tints the whole `⏺ <model>`
  badge, not just the dot.
- **Turn row label no longer colour-shifts.** Only the `$` figure carries the cost
  colour band; the `Turn` label is the plain bold label like every other row (the
  `cost_label_color` shade and its `*_L` palette constants are gone).

## [1.67.0] — 2026-07-04

### Added
- **`task-station obsidian --migrate-path`** relocates a legacy
  `<vault>/Claude/task-station` export folder to the new `<vault>/task-station` layout —
  notes, the `.task-station-index.json` sidecar, and the managed `Task Board.base` view
  move together, and the empty `Claude/` parent is pruned. Safe and idempotent: a
  second run is a no-op, and it refuses (exit 0, with a manual-merge hint) when both
  folders already hold data. `obsidian --flush` / `--sync-all` auto-heal the
  unambiguous case, so the Stop / SessionStart hooks migrate an older vault in place.
- **New board view-model keys (WS7 → board render).** Each task now carries
  `stats_cost` (`{text, kind, usd}`, never n/a), `steps_cell`/`cost_cell` (promoted grid
  columns), `hub_sessions` (per-hub one-liner + pinned/live/msgs/age/cost/out/resume for
  the merged Sessions section), 3-tuple `files` (`base, dir, abspath` for clickable
  editor links), and `editor_scheme`.
- **`--editor-scheme` config (default `vscode`).** Chooses the URI scheme the board
  uses to open file paths (`vscode://file/<abspath>`); set another editor's scheme
  (`vscode-insiders`, `cursor`, …) or clear it to restore the default.
- **Delta-injection context routing (WS5).** A session that resumes or prompts on a
  task whose event feed advanced while it was away now receives ONE bounded
  "While you were away" brief — the events that OTHER sessions, workers, and child
  tasks recorded since this session last looked.
  - New `delta_brief(task, session)` selects `task["events"]` newer than the
    session's per-session `session_meta[sid]["seen_ts"]` high-water mark (default:
    the session's attach `ts`, else 0) and NOT authored by this session (a
    missing/None `sid` counts as another session). Rendered newest-last, hard-capped
    to `DELTA_MAX_ITEMS` (6) events and `DELTA_MAX_CHARS` (700) with a `(+N earlier)`
    note when trimmed.
  - New `mark_seen(task, session)` advances the watermark so a brief is shown
    exactly once; injection points call it and persist.
  - Wired into **SessionStart** (attached branch) and **UserPromptSubmit** (the
    attached-and-open path — the only line that path may emit; silence stays the
    default when there's no news), and the `/todo <n>` **detail render** marks the
    task seen so freshly-read news isn't re-injected on the next turn.
  - Bare/legacy tasks (no `events` feed) inject nothing — every helper degrades to
    None/no-op when the field is absent.
- **Board — session counts + relation edges (WS4).** Each expanded task card's brief
  detail now shows a `sessions` row (`N hubs (M live) · K workers`) and a `related`
  row (`from #363 · spawned #365 (closed) · related #341`), and a collapsed row with
  outgoing edges gets a muted `↳ from #N` marker beside its title. Relation seq
  numbers are folded into each row's search blob so a linked task is findable by
  typing the counterpart's number. The reverse ("spawned"/"related ←") edges are
  derived from a reverse-edge index built once per board render, keeping the board
  O(N). Tasks with no sessions and no relations render exactly as before.
- **Session tree + task relations on `/todo <n>` (WS3).** The task detail now shows a
  `Sessions:` block — the hub sessions that worked the task (each classified `main`
  vs `side-quest`, marked ● live / ○ gone with message count, age and cwd, pinned hub
  flagged), with the in-project workers each hub spawned nested under it (`↳`), read
  live from the delegate registry via its `spawner` field; workers whose spawner is
  unknown are listed unnested as `↳ (unlinked)`. A `Related:` line joins the Artifacts
  block when the task has relation edges — its own `spawned-from`/`related` edges plus
  reverse edges (`spawned #N`, closed targets marked `✕`) derived by scanning other
  tasks. New helpers `session_tree(task)` and `related_edges(task, tasks=None)` back
  both this view and the HTML board. Bare tasks (no sessions, no relations) render
  exactly as before; the worker listing that used to sit under the Resume block is now
  absorbed into the Sessions tree (no duplication).
- **Delegate: spawner capture + worker/child event write-back (WS2).** Every
  `workers.json` registry entry now records the `spawner` hub session id (from
  `CLAUDE_CODE_SESSION_ID`) so the session tree can nest a worker under the hub
  that launched it; a resume refresh preserves an already-recorded spawner when
  the env var is absent. On finishing (or failing/timing out), a delegated run
  fires a best-effort `worker` event onto its `/todo` task's feed via the
  `add-event` CLI — the same swallow-on-failure pattern as the add-cost
  write-back, so a tree without that subcommand degrades cleanly. Closing a task
  that was `spawned-from` a parent now mirrors a `child` event onto each parent's
  feed (`_close_one` and the `/done` session path), so a session on the parent
  hears its child wrapped up. Bare tasks with no relations are untouched.
- **Per-task event feed (`task["events"]`).** A bounded (`EVENTS_KEEP=100`), append-only,
  session-attributed feed of what happened to a task — the single source the delta-brief
  will diff against a session's high-water mark. Each entry is
  `{ts (epoch float), kind, sid, text}` with `text` capped at `EVENT_TEXT_MAX=160`
  (privacy: never a full worker result body). `add_log`/`append_decision`/`append_history`/
  `record_run`/`set_status` and `update --summary` now emit matching-kind events
  (`log`/`decision`/`milestone`/`run`/`status`/`summary`); `add_log`/`touch` thread the
  originating session so a resumed session can tell its own work from others'.
- **Task-to-task relations (`task["related"]`).** An edge list `{id, seq, kind, ts}` stored
  on the child (spawned-from) or the task `--relate` ran on; reverse edges are derived by
  scan, never stored bidirectionally. New `append_related(task, other, kind)` helper
  (idempotent).
- **`update --relate <ref>` (repeatable).** Records an explicit `related` edge to another
  task by seq/id and posts a `child` event on the related task so both feeds hear about the
  link. Idempotent — a repeat is a no-op. Optional `update --session <sid>` attributes the
  `--relate`/`--summary` events.
- **Automatic spawn-edge capture.** Creating a task from a session already attached to
  another (substantive) task now records a `spawned-from` edge on the new task and posts a
  `child` ("spawned #N") event to the parent's feed — the 363→365 silent-spawn case — plus a
  `↳ spawned-from #N` line on the create warning.
- **`add-event` subcommand.** Quiet bookkeeping (`add-event --task <ref> --kind <k>
  --text <t> [--session <sid>]`) that appends one feed entry with no attach/touch — used by
  the delegate to post worker/child milestones onto the linked task.

### Changed
- **HTML board — expanded-row UX overhaul (WS6).** The expanded task row is now grouped
  into **five collapsible, colour-headed sections** — **overview** (open by default:
  full title + digest + summary), **usage & cost**, **prompts**, **sessions**, and
  **history** — each with a subtle, mutually-distinct header hue (coloured text + a 2px
  left border) that re-tints with the light/dark toggle. Every section's open/closed
  state persists across the change-driven refresh (overview stays open); a section is
  omitted entirely when it has no content, so bare tasks render exactly as before.
  - **Steps and Cost are now real grid columns** (before Activity) instead of chips
    folded into the Task cell, so the title regains its full width; the marquee title
    auto-scroll now triggers on hovering **anywhere in the row** and re-measures after a
    layout tick (Safari). All eight grid headers stay lowercase.
  - **Task cost never shows `$n/a`** — it falls back derived → reported → priced-floor
    (`≥$…`) → empty; the model-mix legend/tooltips say "unpriced" instead of `$n/a`.
  - **Hub vs worker prompts** are visually distinguished: the user's own (hub) prompts
    read as primary, worker prompts are dimmed and collapsed behind a "worker prompts
    (N)" toggle. Long-accumulation controls (Decisions, History, full prompts) scroll in
    their own `.longlist` box, siblings of the existing Summary box.
  - **Live-session chips** are now actionable: a task-linked chip is a link that jumps
    to and opens that task's row; a task-less chip expands in place to show its pid,
    session id, cwd, label, and resume command so an unidentified process is inspectable.
  - **Files open in your editor** via a configurable URL scheme link
    (`editor_scheme`, default `vscode` → `vscode://file/<abspath>`) with a copy-path
    button; the usage & work-mix bar segments respond to hover. Each hub session shows a
    one-liner + pinned marker in the merged Sessions section (when the data is present).
- **Usage/data layer: `$n/a` killed, work-mix cleaned up, richer board keys (WS7).**
  - **No more `$n/a`.** The task-cost display follows a never-blank fallback chain —
    fully-priced **derived** total → delegate-**reported** $ → a `≥` priced-subtotal
    **floor** → `$0.00`. The stats-line segment, the `usage --task` per-model mix
    (now `(unpriced)`), its per-session rows (now `—`), its totals, and the derivation
    note no longer emit the `$n/a` literal.
  - **Work-mix `__v` leak fixed.** The board's phase bar skips the stored `__v`
    version stamp (and any non-phase key), so a `__v` segment can never appear.
  - **Phase classifier v2 (`PHASES_VERSION = 2`).** MCP tools map by verb heuristic
    (find/read/search/get → research; replace/insert/edit/write/update → implementation;
    ADO PR/work-item create·update·vote·link → delivery), `Task`/`SendMessage` → research,
    `TodoWrite`/`TaskCreate`/`TaskUpdate`/`EnterWorktree` → planning, and read-only Bash
    (`ls`/`cat`/`rg`/`grep`/`git status|diff|log|…`) → research — so the `other` bucket
    shrinks to genuinely-unknown signals. The version bump forces stale rollups to rescan.
- **Cost HUD — exact parity with the archived costbar (WS8).** The status-bar rows now
  match `costbar.sh` to the character:
  - **5-hour** and **Week** rows show the budget as remaining `% left` (`100 − used`)
    instead of raw utilization — still colored by the *used* percentage, so `0% left`
    reads red. The 5-hour row renders a DIM `—` when no rate-limit data is available.
  - The **Session** row appends the context window's `<n>% left` from
    `context_window.remaining_percentage`, banded by `rem_color` (≤20 red, ≤50 yellow,
    else green).
  - The **Week** label is now `Week <n>` — the calendar-week counter (weeks elapsed
    since the first-seen session), ported from costbar's `cal_week`.
  - Rate-limit **reset times** render in costbar's exact format: ` (↺4:32 PM)` for the
    5-hour reset (`%I:%M %p`, leading-zero-stripped) and ` (↺Wed 4:30 PM)` for the week
    reset (`%a %-I:%M %p`), in `LABEL_GRAY` with the `↺` glyph and parentheses.
  - The **Turn** label is tinted with costbar's muted label-shade
    (`GREEN_L`/`YELLOW_L`/`RED_L` via `cost_label_color`), keyed on the turn cost.
  - The **Total** row now carries the eco-footprint `≈` suffix (costbar's `total_eco`).
  - The inline between-field separator is costbar's plain ` · ` (the dimmed dot stays
    reserved for the eco suffix).
  - The header (`⏺ <model>` + inline task segment) is unchanged.
- **Obsidian export moved to `<vault>/task-station/`** (was `<vault>/Claude/task-station/`)
  — one top-level, plugin-namespaced folder, easier to find and delete. Daily notes at
  the vault root are unaffected.

## [1.66.0] — 2026-07-04

### Changed
- **Cost HUD — visual parity with the old costbar + full-ledger data (WS7b).** The
  HUD now owns the whole status bar and renders costbar's layout:
  - A **header line** carries costbar's violet `⏺ <model display_name>` badge plus the
    task-station whoami segment (`#<seq>  <emoji> [TAG]  <title>`) inline. While the HUD
    is on, the separate `50-task-station` provider is suppressed (`whoami --statusline`
    emits nothing) so the task never renders twice; toggling `--hud off` restores it.
  - The **Task** row sits directly under the header — `$<derived> derived · $<reported>
    reported · out <tokens>`, with no repeated task number and no `(+unknown)` marker
    (that unpriced-model caveat now lives only in `usage --task`'s derivation note).
  - **Turn** moved to its own line (cost only). The old two-line **limits** block is
    gone: the **5-hour** row shows `utilization% · resets <local time>`, and the
    **Week** row merges the weekly limit — `$<wk> out <tok> · <util>% · resets <local>`
    + the SMTWTFS week dots, all anchored to `rate_limits.seven_day.resets_at − 7d`
    (the last-seen reset is cached under the data dir so payload-less renders keep the
    anchor). All reset times render in **local** time.
  - The **eco-footprint** column (`--hud-eco`) now defaults **on**.
  - `--hud-rows` row set is now `task,turn,session,fivehour,week,total`; the old names
    (`limits`, `5-hour`, …) are accepted as aliases so existing config keeps working.

### Added
- **Full-ledger usage data.** `task-station usage scan-all` scans EVERY session
  transcript under `~/.claude/projects/*` into the ledger (byte-offset incremental,
  `task_id` NULL for sessions attached to no task), so the HUD's Week/Total no longer
  understate. The Stop/SessionStart hook flush and each HUD render now trigger this
  sweep incrementally.
- **`task-station usage import-costbar [--path P]`** — a one-time import of costbar's
  `~/.claude/cache/session_totals.json` rollup for history whose transcripts are gone.
  Inserts `source='costbar-import'` ledger rows (cost + output tokens only) for session
  ids not already in the ledger and with no transcript on disk; idempotent; the Total
  row's "since" date includes the imported dates.

## [1.65.1] — 2026-07-04

### Fixed
- `config --hud/--hud-rows/--hud-eco` flags were unreachable from the CLI — the WS7 merge omitted the parser wiring in `lib/task-station.py`.

## [1.65.0] — 2026-07-04

### Added
- **Cost HUD — costbar's status-bar rows, folded into task-station's status-line host
  (WS7).** `task-station config --hud on` installs a cost-HUD segment provider
  (`statusline.d/60-cost-hud.sh`) on the same compositing host as `--statusline`
  (opt-in, default off, non-destructive — never clobbers a foreign `statusLine`). The
  HUD renders costbar's rows plus a new **per-task** row, every figure priced by the
  **shared usage ledger + `lib/pricing.py` rate table** — one scanner, one rate sheet,
  so costbar's divergent engines and stale-rate mispricing (fable priced as sonnet,
  current Opus over-counted 3×) die by deletion:
  - **Turn / Session / 5-hour / weekly-limit** rows read the status-line stdin JSON
    (`cost.total_cost_usd`, `context_window`, `rate_limits.*`) — Anthropic-authoritative,
    zero extra compute. The per-turn `$` delta comes from a baseline snapshot taken at
    UserPromptSubmit and frozen at Stop, relocated from costbar's `/tmp/claude_*` scatter
    into one JSON per session under `<data_dir>/hud/`.
  - **Week / Total** rows aggregate the WS1 `session_usage` ledger (replacing costbar's
    `compute_jsonl_totals.py` + its cache), with the SMTWTFS week-dot strip and μ/μ+σ
    session-cost threshold coloring recomputed from the ledger.
  - **Task** row — the attached task's cumulative derived `$` + reported worker `$`
    (`$X.XX derived · $Y.YY reported`), the per-task attribution costbar couldn't do.
  - `--hud-rows turn,session,limits,week,total,task` toggles which rows show (and their
    order); `--hud-eco on` appends costbar's rotating eco-footprint column (default off).
  - Respects `--usage-billing-mode`: in `subscription` mode the Total row is labelled
    API-equivalent value.

## [1.64.0] — 2026-07-04

### Added
- **Tasks-by-prompt view — the exact prompt trail that drove a task (WS6).** A new
  `task-station prompts --task <ref>` command renders the chronological, timestamped,
  session-attributed list of the precise user prompts (and slash commands) behind a
  task — hub sessions and every delegated worker (a worker brief shows up as that
  worker session's first prompt), oldest first, each line `<time>  [<role> <sid>]
  <prompt>`. Flags:
  - `--md` — the **shareable Markdown artifact** ("show others exactly what I prompted
    to get the end result, with timestamps"): full (un-clipped) prompt text, commands as
    code spans, prose prompts as blockquotes, under `**<time>** · <session>` headers.
  - `--json` — the raw prompt rows (uuid/ts/session/role/label/kind/text).
  - `--all` — include compaction-summary rows (omitted by default; `command` rows always
    render as the slash command).
- **`/todo <n> prompts` (and the first-class `/task-station:prompts` command)** route the
  same read-only way `/todo <n> history` does — they render the prompt trail without
  attaching, reopening, or mutating the task. Bare `/prompts` targets the current
  session's task.
- **MCP `get_prompts` tool** — the Desktop bridge analog of `prompts --md`: the task's
  prompt trail as Markdown, with an optional `include_compact`.
- **Board full-prompts block** — beside the existing Recent-prompts preview (last 5), an
  expandable **All prompts (N)** `<details>` lists the complete session-attributed trail
  on the task row. Both are config-gated by the new **`board_prompts`** setting (default
  **on** — the board is local-only; prompt *export* stays opt-in). `board_prompts` is
  independent of `usage_prompts` (capture): with capture on but display off, prompts are
  stored yet kept off the board. Toggle with `config --board-prompts on|off` (env escape
  `TASK_STATION_BOARD_PROMPTS`).
- **Second-brain export bridge — task-station as a generic episodic-memory layer (WS8).**
  A new `task-station export --dir <path>` command writes a self-sufficient markdown
  snapshot of your tasks into ANY directory — no vault config required — so an external
  second brain can ingest it. Each task becomes the same note the Obsidian bridge
  produces (shared `render_note`), plus a wikilinked `index.md`. Selectors:
  `--task <ref>` (one task), `--status open|active|closed`, or `--all` (default);
  `--include usage,prompts,history` picks the sections (default `usage,history` —
  prompt export stays opt-in, a snapshot may leave the machine); `--since <ISO date>`
  filters to recently-updated tasks. The interchange contract is plain markdown +
  Obsidian-compatible `[[wikilinks]]` + flat YAML frontmatter, one-way (task-station
  exports; a consumer reads). New module `lib/export.py`, stdlib only.
- **Obsidian notes now carry derived usage + (opt-in) the prompt trail.** Every exported
  note gains a `## Usage` block — model mix %, token totals, `$ derived · $ reported`,
  and the work-phase mix (sourced from the WS1 usage ledger) — and three new flat
  frontmatter keys, `models` (list of model ids), `cost-usd`, and `time-spent`
  (active minutes), all Bases/Dataview-queryable. A `## Prompts` block (the full
  timestamped prompt trail) is written only when the new opt-in
  `config --obsidian-prompts on` is set — a synced vault may reach third-party
  services, so prompt export defaults off even though prompt capture is on.
- **MCP: documented the live episodic query surface.** `search_tasks` / `get_task` /
  `get_prompts` are the up-to-the-moment query complement to the pull-based export —
  noted in the bridge's module docstring for external-brain integrators.

## [1.63.0] — 2026-07-04

### Added
- **HTML board usage panels — model mix, per-session cost, work mix, and derivation on
  the board itself (WS4).** `/todo board` now surfaces the usage ledger visually. Each
  task row gains a compact stacked **model-mix bar** (one coloured segment per model
  family, by derived-cost share) plus the `$X.XX derived` figure, right in the collapsed
  summary. Expanding a row reveals, after the digest:
  - **Usage & Cost** — the task-level mix bar + legend (`fable 80% · opus 20%`), token/$
    totals with the delegate-reported cross-check (`$X.XX derived · $Y.YY reported`), and
    a **per-session table** breaking the hub session apart from each delegated worker
    (short sid, `hub`/`worker:<label>` role, a per-session mini model-mix bar, in/out/
    cache tokens human-formatted, and `$ derived · $ reported` per session).
  - **Work mix** — a stacked phase bar + legend (rendered only when phase data is present).
  - **How these numbers are derived** — a collapsed panel showing the derivation note and
    the `$/MTok` rate rows actually used, so every figure's provenance is on the board.
  - **Recent prompts** — a collapsed preview of the last few captured prompts (local
    display only; capture stays config-gated and export opt-in).
  A **Live now** header strip lists running sessions and links to their task rows when
  live-session data is present. All panels are pure inline CSS/HTML (stacked `<div>`s, no
  external assets) and degrade gracefully — absent ledger, phase, or live data simply
  omits the corresponding block, so the board stays self-contained and offline.
- **Live-session viewer — see every actually-running Claude session (WS5).** A new
  `task-station sessions [--task <ref>] [--json]` command reads Claude Code's own
  per-process state files (`~/.claude/sessions/<PID>.json`, one per running
  interactive hub AND `claude -p` delegated worker), keeps only rows whose pid is
  genuinely alive (`os.kill(pid, 0)` liveness — dead/crashed sessions are skipped,
  never deleted), and joins each session to its task (via `store.links`) and to a
  worker label (via `workers.json`). Each row shows `● pid · task N · role ·
  busy/idle · age · cwd` plus a ready **`cd … && claude --resume …`** one-liner —
  this replaces the old "I only see python3 shell details" gap with a
  one-copy-paste resume.
  - **New module `lib/live_sessions.py`** (`running()` + `pid_alive()`), stdlib only.
    The sessions dir is env-overridable (`TASK_STATION_SESSIONS_DIR`, else
    `CLAUDE_CONFIG_DIR`) for testing.
  - **`/todo <n>` detail annotations.** The existing Hub-resume and in-project-worker
    resume lines now carry a `● busy/idle · age` marker when that session is actually
    running.
  - **Board strip.** The HTML board renders a compact strip of the live sessions above
    the task columns (self-contained, no external assets).
  - **MCP `list_sessions` tool.** The Desktop bridge gains a `list_sessions` tool
    (optional `task` filter) that surfaces the same running-session list as Markdown.
- **Work-phase classification for the usage panel (WS3).** `task-station usage --task`
  now shows a heuristic split of a task's work across phases — e.g.
  `Planning 47% · Implementation 31% · Verification 14% · Research 6% · Delivery 2%` —
  alongside the existing model mix, weighted the same way (share of derived cost, falling
  back to output-token share when a model is unpriced) so the two panels are comparable.
  - **Deterministic, no model calls (`lib/phases.py`).** Each assistant transcript
    message is classified from its `tool_use` block names + the envelope
    `attributionSkill`: brainstorming/writing-plans/plan skills and plan-mode tools →
    planning; code-review/verify/review skills and `Bash` test/build commands →
    verification; `Bash` ship commands (`git push|commit|merge`, `gh pr`, `az repos`,
    `release`) → delivery; `Edit`/`Write`/`NotebookEdit` → implementation;
    `Read`/`Grep`/`Glob`/`WebSearch`/`WebFetch`/`Agent`/`Explore` → research; a
    mixed-tool message takes the highest-precedence hit
    (implementation > verification > delivery > planning > research); no signal → other.
  - **Wired into the incremental scanner.** The per-message phase is folded into a
    `session_usage.phases` roll-up during the existing usage scan; a `PHASES_VERSION`
    stamp in that blob forces a full rescan when the classifier logic changes. The
    derivation note calls the split out as heuristic (tool-mix per message, cost-weighted).

## [1.62.0] — 2026-07-04

### Added
- **Usage ledger — per-task model mix + derived $ from your local transcripts (WS1).**
  A new `task-station usage` subcommand and a compact stats-line segment surface, per
  task, which models did the work (`fable 79% / opus 21%`), the derived dollar cost, and
  a per-session breakdown that distinguishes the hub session from each delegated worker.
  - **Derivation, not guesswork.** There is no per-message `costUSD` in a transcript, so
    cost is derived from the raw `message.usage` token counts × the published $/MTok rate
    sheet (`lib/pricing.py`, verified 2026-07-04). The rate lookup keys on **(model,
    speed)** — fast mode is a separate Opus 4.7/4.8 price sheet, not a separate model —
    resolves Sonnet 5's date-dependent intro window by message timestamp, folds in the
    uniform cache-write TTL multipliers (5m = 1.25×, 1h = 2×, read = 0.1× input) and the
    `inference_geo: "us"` 1.1× uplift, and returns `$n/a` for an unknown model rather than
    silently pricing it at another tier. The delegate-reported `workers $X` stays as the
    cross-check figure next to the derived number.
  - **Incremental scanner (`lib/usage.py`).** Byte-offset incremental parse of each
    session JSONL and its `<sid>/subagents/agent-*.jsonl` (subagent traffic is bucketed
    separately so it isn't double-counted), tolerant of a truncated trailing line and of
    file rotation. Rolls per-model tokens/cost into two additive SQLite tables
    (`session_usage`, `prompts`) and auto-flushes from the unsandboxed Stop/SessionStart
    hooks — local-only, self-gating on `usage_tracking`, and never able to crash a hook.
  - **Privacy + billing framing.** New config flags: `--usage-tracking` (default on),
    `--usage-prompts` (default on — gates capturing prompt text into `tasks.db`;
    same-machine only, export stays opt-in), and `--usage-billing-mode api|subscription`
    (default `api`) which relabels the derived $ as "API-equivalent value (flat-rate seat
    — not billed per token; overage bills at these rates)" for subscription seats.
- **Delegate runs now persist the worker model and per-run token usage.** A
  `delegate.py run` parses the model id and `usage` out of the worker's
  `claude -p --output-format json` result (graceful when older CLIs omit them),
  records the model on the `workers.json` registry entry, and appends a per-run
  record to the linked `/todo` task — `runs[]` = `{ts, seq_label, session_id,
  model, cost_usd, usage:{in,out,cache_read,cache_creation}}`, append-only and
  capped at 50 most-recent. `task-station add-cost` grows optional `--model`,
  `--session`, `--seq-label`, and `--usage-json` to carry that detail; the
  existing running-total `cost` field is unchanged. The delegate stderr footer
  and `delegate list` now print the model next to the cost, and `/todo <n>`
  Worker lines show the model each worker ran.

## [1.61.0] — 2026-07-04

### Changed
- **Opt-in auto-checkpoint now triggers on best-practice signals, not a fixed token
  count.** The proactive "checkpoint before compaction" nudge previously fired off a
  crude transcript-*byte* estimate against a fixed absolute threshold (`--checkpoint-at`,
  default `150000`). It now fires off a **percentage of your real context window**,
  measured from the transcript's actual `usage` block:
  - **Real context measurement.** A new `measure_context_tokens()` helper reads the
    session transcript's *tail* (last 256 KB) and reverse-scans for the most recent
    `usage` block, summing `input_tokens + cache_read_input_tokens +
    cache_creation_input_tokens` (output tokens are *not* resident context). It tolerates
    sliced/partial and malformed lines and returns `0` when nothing usable is found, so
    callers fall back to the byte-size estimate.
  - **Percentage trigger (new default).** `--checkpoint-pct` (default `65`, valid `1–95`,
    `0`/`off` disables) fires the proactive nudge once the measured context reaches that
    share of `--context-window` (default `200000`; raise it for a larger window, e.g.
    `1000000`). The nudge names the measured pressure as both percent and tokens — e.g.
    *context ~68% (~136k/200k tokens)*.
  - **`--checkpoint-at` default changed `150000` → `0` (off).** It is now the LEGACY /
    fallback absolute trigger — kept for back-compat, so an explicitly stored value (or
    the `TASK_STATION_CHECKPOINT_AT` env var) keeps firing the old estimate-based path,
    used whenever a real usage measurement isn't available. The percentage trigger is the
    new default path.
  - **Milestone staleness nudge.** The light "digest looks stale" nudge is now
    activity-gated: it holds until `--checkpoint-milestone-edits` meaningful events (file
    edits / status promotions — the substantive-work signals that already mark the digest
    stale) have accrued since the last digest refresh (default `5`). `0`/`off` restores
    the previous fire-on-any-staleness behaviour. Backed by a per-task `digest_events`
    counter that `clear_digest_dirty` resets on every refresh.
  - The once-per-episode semantics (`pressure_nudged` set on emit, cleared by
    `/todo save`) and the never-fires-when-off / no-task-attached guards are unchanged.
    New env overrides `TASK_STATION_CHECKPOINT_PCT`, `TASK_STATION_CONTEXT_WINDOW`,
    `TASK_STATION_CHECKPOINT_MILESTONE_EDITS` mirror the existing pattern; all three keys
    are on the config board and cleared by `--reset`.

## [1.60.1] — 2026-07-03

### Fixed
- **Obsidian vault in a protected folder (`~/Documents`, iCloud, …) now Just Works.**
  Claude Code's Bash-tool sandbox only allows writes under the session cwd + `$TMPDIR`,
  so a vault under a TCC-protected root is unwritable from a project session — the
  mid-turn atomic write is denied (`os.replace` → `EPERM`) and the vault silently drifts
  stale. Two-layer fix:
  - **Auto-flush from unsandboxed hooks (zero-config, always-on).** A denied in-session
    export now *silently* marks the task **pending-resync** (`obsidian_dirty`, mirroring
    the existing `digest_dirty` design — persisted via a direct `save_task` that does not
    re-trigger the export hook; a later success clears it). Because hooks run
    **unsandboxed**, the Stop and SessionStart hooks invoke `obsidian --flush --quiet`,
    which re-exports the pending tasks from outside the sandbox and succeeds where the
    hot path couldn't — healing the vault within seconds, no configuration needed. The
    Stop-hook flush is independent of the stop-gate (never blocks/delays the turn),
    suppressed in delegate workers, and a silent, self-gating no-op when export is off or
    nothing is pending.
  - **Optional instant inline exports.** `task-station config --obsidian-sandbox on` adds
    the configured vault to `sandbox.filesystem.allowWrite` in your own
    `~/.claude/settings.json` (atomic read-merge-write, backed up; creates only the
    needed structure; never touches `sandbox.enabled` or other keys; records the exact
    entry in `setup-manifest.json` for a precise `--obsidian-sandbox off` reverse), so the
    sandboxed hot-path export writes immediately. Degrades gracefully — without it, the
    hook auto-flush alone keeps the vault correct.

  The loud, actionable hint (grant Full Disk access · relocate the vault ·
  `--obsidian-sandbox on` · `--flush`/`--sync-all`) is now reserved for a **persistent**
  failure — a task still dirty after a hook flush also failed (vault gone, or even the
  unsandboxed hook denied) — gated on a per-task `obsidian_flush_failed` signal and
  deduped per episode via a `.obsidian-perm-warned` marker (cleared on the next success).
  `task-station obsidian --flush` re-exports only the pending tasks (cheaper than
  `--sync-all`, which also clears the flags); `obsidian --status` reports the
  pending-resync count. Setting `--obsidian-vault` to a protected path prints a one-line
  non-fatal heads-up. New helpers: `is_protected_vault_path`,
  `setup.install_sandbox_allowwrite` / `remove_sandbox_allowwrite`. All bookkeeping is
  exception-safe — it never breaks a mutation, a hook, or delegation.

## [1.60.0] — 2026-07-03

### Added
- **Claude Code native Tasks interop (read-only).** New `lib/native_tasks.py` reads
  Claude Code 2.1+'s in-session Tasks (`~/.claude/tasks/<list-uuid>/<n>.json`) without
  ever writing them. `task-station native` / `/todo native` lists recent native task
  lists (recent = dir touched in the last 14 days **or** still has an open item), one
  section per list with items as `✓ / ◐ / ○ <id> <subject>`. The terminal board footer
  gains a one-line **NATIVE** pointer only when a recent list has open items (the full
  dump stays behind `/todo native`, keeping the board lean). `task-station adopt --native
  <list-prefix>:<id>` (`/todo adopt …`) promotes a native task into a durable station task
  from its subject/description (colour GENERAL, effort S), recording provenance in the
  summary and activity log. Parsing is fully defensive — malformed/missing files are
  skipped silently and an unreadable store never breaks tracking. Root overridable via
  `TASK_STATION_NATIVE_TASKS_DIR`. Positioning added to `guidance`: native Tasks =
  in-session orchestration; Task Station = the durable cross-session console.
- **Worker lifecycle notifications (opt-in).** A delegated worker run now fires a
  best-effort notification when it **finishes** (`worker_finished`) or **fails/times out**
  (`worker_failed`), on two independent channels: a macOS `osascript` banner
  (`config --notify on`, darwin only) reading `<repo>/<label>: finished|failed` under a
  `task-station worker` title, and a webhook `POST` (`config --notify-webhook <url>`) with
  a JSON body `{event, task_seq, repo, label, worktree, cost_usd, ts}` (3s timeout) that any
  plain-JSON receiver accepts (Slack/Teams/ntfy.sh topic URLs). New config keys `notify`
  (default off) and `notify_webhook` (default unset), with env overrides
  `TASK_STATION_NOTIFY` / `TASK_STATION_NOTIFY_WEBHOOK`; both appear on the config board.
  Every channel is separately guarded — a missing `osascript` or a dead webhook can never
  break a delegation.
- **Cross-task search — `task-station search <terms>` (and `/todo search <terms>`).**
  A ranked, token-economical tier-1 hit list — `#seq`, status dot, title, and a
  one-line match-context snippet each — over every task's text (title, summary,
  goal, next-step, decisions, checklist steps, activity log, dated history, and
  linked repos/PRs/stories). `--open` / `--closed` / `--all` (default all) filter by
  status; `--detail <seq>` prints one task's full read-only digest. Backed by an
  **FTS5** index kept in sync in the store write path and backfilled once for
  existing DBs (schema-version bump); on sqlite3 builds without FTS5 it degrades
  transparently to a ranked LIKE scan. Also exposed on the Claude Desktop bridge as
  a `search_tasks` tool mirroring the tier-1 output.
- **Per-task time & cost stats.** The task detail view (and the HTML board's row
  expansion, and the Desktop `get_task` detail) now show a `Stats:` line —
  `time ~Xh Ym across N sessions · workers $X.XX`. Time is derived from
  idle-gap-capped activity spans (a bump after a >30-minute gap starts a new span,
  so idle time isn't counted); worker cost accumulates per delegate run —
  `delegate.py` records each run's `total_cost_usd` onto the linked task via the
  engine, not just `workers.json`.
- **Cross-platform terminal tinting.** `term.detect()` now recognises any
  xterm-compatible terminal that honours OSC 11 — iTerm2, Apple Terminal, WezTerm,
  VS Code, Ghostty, Windows Terminal (`$WT_SESSION`), kitty (`$KITTY_WINDOW_ID`),
  Alacritty (`$ALACRITTY_WINDOW_ID`/`$ALACRITTY_SOCKET`), and an `xterm*` /
  `*-256color` `$TERM` fallback — and emits the standard OSC palette to all of them
  (the iTerm-only `SetColors=bold` extra still gates on iTerm2). `$TASK_STATION_TERM`
  gains an `osc` value.
- **tmux passthrough.** When `$TMUX` is set, tint escapes are wrapped in tmux's DCS
  passthrough (`term.tmux_wrap`) so they reach the real terminal (needs
  `tmux set -g allow-passthrough on`).
- **Platform support matrix** in the README documenting tint / statusline / window
  jump / board behaviour per macOS / Linux / Windows.

### Changed
- **Dropped the hard `jq` dependency.** Hooks parse their JSON stdin via the new
  `lib/hookjson.py` (a `jq -r '.path // default'` equivalent, silent no-op on
  malformed input) instead of `jq`; the `SessionStart` output JSON is built with an
  inline `python3` one-liner. `python3` is now the only hard requirement.
- **Window open/close degrades cleanly off macOS.** The `-s` jump now prints a
  one-line "run this in a new terminal" hint on non-macOS platforms instead of
  silently doing nothing; auto-close on `/done` stays a no-op. Terminal tint and
  window-title OSC now work on any detected terminal, not just macOS.
- **Obsidian export (opt-in, one-way).** Mirror tasks into an Obsidian vault as
  Markdown notes. Turn it on with `config --obsidian-vault "<path>"` (empty = off).
  Everything the plugin owns lives under `<vault>/Claude/task-station/`: one note per
  task (`<seq>-<slug>.md`, flat YAML frontmatter + `## Goal`/`## State`/`## Summary`/
  `## Decisions`/`## History` body) plus a managed `Task Board.base` Bases view
  (written once, never clobbers your edits). Notes are re-exported on create / update /
  done / `/todo save` (plain bumps and attaches are excluded as too noisy).
  - New `task-station obsidian` subcommand: `--sync-all` (full resync) and `--status`.
  - New settings: `--obsidian-daily-note` (on/off, default off) appends a dated line to
    the vault's daily note on task close and each `/todo save` checkpoint, under
    `--obsidian-daily-heading` (default `## Claude sessions`).
  - Atomic writes, filenames stable across a title change (sidecar index), and graceful
    degradation — a missing vault is skipped with a stderr note, never crashing the engine.
  - New module `lib/obsidian_sync.py` (stdlib only); see the README "Obsidian export"
    section and `docs/ARCHITECTURE.md` §(j). iCloud/Obsidian-Sync double-sync is
    last-writer-wins — keep the vault under one sync mechanism; `--sync-all` repairs it.
- **GitHub Actions CI** (`.github/workflows/ci.yml`) — runs on every push and PR:
  the test suite across `ubuntu-latest` + `macos-latest` × Python 3.9/3.11/3.13
  (`python -m pytest tests/ -q`, no deps beyond `pytest`); `shellcheck` at
  severity `warning` over `hooks/*.sh`, `lib/*.sh`, and `lib/delegate/*.sh`; a
  manifest-consistency check asserting `plugin.json` version == `marketplace.json`
  plugin version == the topmost `CHANGELOG.md` release heading (an `Unreleased`
  heading above it is allowed); and a non-blocking `claude plugin validate .`.
- **`bin/task-station` CLI entrypoint** — a `bash` wrapper that resolves its own
  real path through symlinks and `exec`s `python3 <plugin-root>/lib/task-station.py`.
  Claude Code adds an enabled plugin's `bin/` to the Bash tool PATH, so
  `task-station <command>` replaces the long, version-pinned
  `python3 <plugin-cache>/lib/task-station.py <command>` invocation. Guidance and
  help text now prefer the short `task-station <command>` form, keeping the
  absolute `python3` form as a parenthetical fallback for shells without `bin/` on
  PATH. Hooks are unchanged — they use absolute paths and must not depend on PATH.

## [1.59.0] — 2026-07-03

### Changed
- **Reworded the bare-cmds helpnote to say "bare-cmds is on/off" and moved the
  `/task-station:` prefix note to its own line** (board Commands panel + terminal
  `/todo` footer). Previously the on-state note buried the prefix reminder in a
  parenthetical ("Short aliases are on — … work directly (the /task-station: prefix
  also always works)."); it now reads as two separate lines on both surfaces.

## [1.58.0] — 2026-07-02

### Changed
- **The terminal `/todo` footer is now bare-aware, like the HTML board.** With
  `--bare-cmds off` (the default), `commands_footer()`/`commands_footer_md()` now
  rewrite each command label to its `/task-station:` form (e.g. `/task-station:todo
  save`) instead of always printing the bare `/todo …` forms that don't work in
  that state, and append a note pointing at `/task-station:config --bare-cmds on`.
  With bare on, the note confirms the short aliases are live. The HTML board's
  Commands helpnote now names the full aliasable set (`/todo`, `/done`, `/save`,
  `/pin`, `/history`, `/repos`), not just `/todo`/`/done`/`/pin`. `guidance` gained
  a short caveat stating the same rule.

## [1.57.0] — 2026-07-02

### Added
- **`/task-station:save` and `/task-station:history` are now first-class commands** —
  previously `save` and `history` were reachable only as `/todo` subcommands
  (`/todo save`, `/todo <n> history`). They now appear in the command palette like
  `/task-station:pin`/`done`/`config`. `/task-station:history` with no argument shows
  the current session's attached task (read-only, same as `/todo <n> history` for a
  specific task). Bare `/save` and `/history` join the opt-in `--bare-cmds` set.

## [1.56.0] — 2026-07-02

### Added
- **Proactive context-pressure checkpoint (with `--auto-checkpoint` on).** As a session
  fills, a non-blocking `Stop` nudge now prompts a full structured `/todo save` from full
  context — *before* the harness auto-compacts — reframing `/todo save` as the task-shaped
  compaction you resume from (fresh session + `/todo <n>`), a better digest than the generic
  auto-summary. The trigger is a tunable heuristic: `config --checkpoint-at <tokens>` (default
  150000; `0`/`off` disables the proactive trigger while the PostCompact stash fallback still
  runs; env override `TASK_STATION_CHECKPOINT_AT`), compared against a rough transcript-size
  token estimate. It fires **once per pressure episode** — held until a `/todo save` acts on it,
  so an ignored nudge isn't re-spammed — and takes precedence over the lighter staleness nudge.
  Hooks can't read the exact context % and auto-compaction can't be reliably prevented; the win
  is keeping the durable digest current so the *resume* is structured, not blocking compaction.
  A `/todo save` records `last_full_save_ts` and clears both the `digest_dirty` and
  `pressure_nudged` markers. **The PostCompact summary stash remains the fallback.**

## [1.55.0] — 2026-07-02

### Added
- **Opt-in automatic checkpointing (`--auto-checkpoint`, default off).** A compaction-safe,
  token-efficient way to keep an attached task resumable across a context compaction. When
  on: a **PostCompact** hook stashes the harness's compaction summary into the task's history
  (zero model tokens, read back via `/todo <n> history`); a **SessionStart-on-compact** nudge
  asks the model to refresh the structured digest post-compaction (its source of truth for
  continuing); and a **staleness-gated Stop nudge** — non-blocking — asks the model to refresh
  `--state` when real work has happened since the last digest refresh. No full `/todo save`
  every turn. Toggle with `config --auto-checkpoint on|off` (env override
  `TASK_STATION_AUTO_CHECKPOINT`); when off, nothing above fires. A `digest_dirty` flag tracks
  staleness — set on a real file edit or a delegate/worktree promotion, cleared on any digest
  refresh (`update --goal/--state/--step-*/--decision/--log`) or `/todo save`.

## [1.54.0] — 2026-07-02

### Changed
- **State label simplified to "next".** The digest's `State (next / standing):` header — in the
  terminal detail, `/todo save`, and the HTML board's briefing row — is now just `State (next):`
  (board: `next`). The `/todo save` guidance still asks for `NEXT: <concrete first move>` followed
  by the current standing narrative; only the label lost "/ standing".
- **Displayed timestamps now render in the system local timezone**, not UTC. Task Created (in the
  terminal detail and the Claude Desktop bridge's task view) and the board's on-demand HISTORY
  trail convert their stored UTC ISO timestamps to local time at display time — old UTC-stored
  records render correctly too. Relative times ("2h ago") and the board's "refreshed …" kicker
  were already local and are unchanged.
- **Deduped the Commands help.** `pin`, `done`, and `config` were each listed twice in
  `_COMMANDS_HELP` — once as a `/todo` subcommand and once as a standalone row. Collapsed to one
  canonical `/todo`-form entry per action (`/todo pin`, `/todo done [n,…]`, `/todo config [flags]`)
  in both the terminal `/todo` footer and the HTML board's Commands panel. The standalone commands
  (`/done`, `/pin`, `/task-station:config`) still work — this only decluttered the help list.

## [1.53.0] — 2026-07-02

### Added
- **The HTML board now renders the on-demand HISTORY trail** in each task's expansion — the
  `--log` dated milestones as a COLLAPSED `<details class="cathistory">` at the bottom (after the
  current-snapshot digest + summary), so the snapshot stays primary and the trail is one click
  away, mirroring `/todo <n> history`. Its open/closed state persists across the change-driven
  refresh (`data-key="hist:<seq>"`); every entry is HTML-escaped; still no external assets. The
  board view-model now carries the task's `history` list (`[{ts, text}]`).

### Changed
- **`guidance` (the model-facing full command reference) now surfaces the checkpoint/history
  capability.** The digest-maintenance guidance documents `--log '<dated milestone/finding>'`
  alongside `--decision` as the append-only HISTORY trail that does NOT load on a normal resume
  (retrieve via `/todo <n> history`), and reframes `summary` as the CURRENT snapshot (rewrite to
  present truth, keep lean) with the WHY/WHEN trail in `--decision` + `--log`. A new
  CHECKPOINT / RESUME line documents `/todo save` (full handoff snapshot, no pin), `/todo <n>`
  (lean recap), `/todo <n> history` (full trail), and `/todo <n> -s` (transcript resume).
- **README highlight** — added a benefit-led "Checkpoint anytime; resumes stay cheap" bullet
  (a resume costs about the same on day 30 as on day 1); trimmed the overlap it introduced with
  the existing "briefing, not a transcript" bullet.

## [1.52.0] — 2026-07-02

### Added
- **`update --log '<entry>'`** appends a dated milestone/finding (`{ts, text}`) to a new
  append-only `log` field — model-facing history (what shipped, findings, "why" notes) that is
  DELIBERATELY kept **off** the default resume path.
- **`/todo <n> history`** (also `history <n>`) — an on-demand, read-only time-machine that
  renders a task's FULL record: the complete decisions log, the complete dated `log`, and the
  full activity log, clearly sectioned. It does not attach, reopen, or mutate the task.

### Changed
- **Token-efficient resumes: the digest now splits a lean CURRENT SNAPSHOT from an append-only
  HISTORY.** The default detail view (`/todo <n>` + the resume recap) renders only the current
  snapshot — goal · state · steps · artifacts · summary — with decisions capped to the most
  recent few (a `… +K earlier — /todo <n> history` pointer when more exist) and recent activity
  capped to a short tail. The growing `decisions` + new `log` trail no longer loads into context
  on every resume; pull it explicitly with `/todo <n> history`. `summary` is now framed as the
  **current truth** (a lean snapshot via `--summary`), not a running log.
- **`/todo save` playbook updated for the split:** write `summary` as the current snapshot via
  `--summary` (REPLACE — do not dump the history into it), record new choices with `--decision`,
  and append one dated `--log` milestone. The hardened named-slot checklist, cold-read check, and
  transcript backstop are unchanged; the capture checklist gains a `5b. LOG (--log)` line.

## [1.51.0] — 2026-07-02

### Changed
- **Hardened `/todo save` so a fresh session resuming from the saved digest alone misses as
  little as possible.** The `[SAVE]` block is now a named-slot capture checklist that forces
  specifics into every field — the concrete next action (state leads with `NEXT: …`),
  approaches tried and rejected, files/paths, branch/worktree/environment, build-test-run
  commands, gotchas, open questions, and the user's latest intent — followed by a cold-read
  self-verification pass (re-read the digest as if you have no memory of the conversation and
  patch anything ambiguous or missing). `/todo save` still pins nothing and mints no session;
  it now records this session's cwd so the full transcript stays recoverable via `/todo <n> -s`
  as a backstop for anything the digest misses.

## [1.50.0] — 2026-07-02

### Added
- **`/todo save` checkpoints the current task's full context into its digest** (no auto-pin)
  for a seamless resume in a fresh session — it prints a `[SAVE]` handoff playbook (the current
  digest + the exact `update` templates) so the model amends the digest to a complete handoff
  (goal · state = the exact next step · full steps checklist · decisions + why · links · a rich
  summary of gotchas/structure). It mints no session and pins nothing — capture only.
- **`pin`, `done`, and `config` are now also reachable as `/todo` subcommands** — `/todo pin`
  pins this session as the task's resume target, `/todo done [n,…]` closes the current task (or
  by number), and `/todo config [flags]` opens the settings console. The standalone `/pin`,
  `/done`, and `/task-station:config` commands still work unchanged.

## [1.49.0] — 2026-06-30

### Changed
- **Synced marketplace manifest version** — `marketplace.json` `plugins[0].version` was stale
  at `1.9.1`; updated to match `plugin.json` at `1.49.0`. Added a test (`tests/test_manifest.py`)
  to catch this drift in CI going forward.
- **Updated both manifests' descriptions to benefit-led positioning** — `plugin.json` and
  `marketplace.json` (plugin entry + owner description) now match the README's benefit-led copy:
  leading with "never lose your place", not with internal mechanism detail.

### Fixed
- No guardrail existed for marketplace/plugin version consistency; `test_manifest.py` now
  asserts `marketplace.json plugins[0].version == plugin.json version` on every CI run.

## [1.48.0] — 2026-06-30

### Docs
- **Reworded the README tagline to remove per-task ambiguity.** "one board for every task"
  read as one board *per* task (1:1); changed to "every task on one board" to make it
  unmistakably one board holding all tasks.

## [1.47.0] — 2026-06-30

### Docs
- **Added a FAQ section to the README.** Three Q&As in the README's voice: running Claude
  from any directory (the board is global), where to run the hub for a multi-repo project
  (a neutral directory outside any single repo), and how delegation picks the right repo
  (`--repo <abs-path>` vs `--project <name>` resolved against `--workspace-dirs`). Placed
  after "How it works"; no other sections changed.

## [1.46.0] — 2026-06-30

### Docs
- **Re-angled the README to benefit-led positioning.** The tagline, intro paragraph, and
  highlights now lead with the reader's payoff — never losing your place, picking up
  exactly where you left off — with the mechanism in support, in the value-forward style
  of widely-installed Claude Code plugins. The reference sections (commands, HTML board,
  digest, delegation, configuration, privacy, troubleshooting) stay factual and accurate,
  and the corrected resume semantics are preserved: `/todo <n>` reopens in the current
  session with a recap + the resume one-liner, `/todo <n> -s` makes the jump into the
  working session in a new window.

## [1.45.0] — 2026-06-30

### Docs
- **Corrected the README's resume description.** `/todo <n>` reopens a task with a full
  recap from its digest and surfaces the `cd … && claude --resume …` one-liner back into
  the session that holds its context — it does not itself move you into that session.
  `/todo <n> -s` is what makes the jump, opening the working session (right directory +
  conversation) in a new window. Fixed the intro paragraph, the "One-command resume"
  highlight, and the commands table, which previously conflated the two.

## [1.44.0] — 2026-06-30

### Docs
- **The README has been rewritten** — professional, scannable, and current. It leads
  with a crisp tagline + the badges (version bumped to 1.44.0), replaces the dense
  bold-callout prose with tight bullet lists and tables, and covers the present feature
  set: every command (incl. `/pin` and `/todo board`), the self-contained HTML board
  (change-driven refresh, auto light/dark theme, `--board-browser`), sonnet-default
  worker delegation (`--model` to override), the configuration flags from `config`, and
  categories/themes. Inline version numbers were removed from the body — version history
  lives here in the changelog, not in the README prose.

## [1.43.0] — 2026-06-30

### Changed
- **The per-turn tracking nudge no longer re-dumps the command templates or colour
  legend on every escalated turn.** Turn 1 still shows the full education block
  (open-task list + attach/create templates + colour legend + guidance pointer).
  Turns 2–3 remain a single compact line. Turn 4 onwards shows the open-task list
  + a firm ⚠ warning + the skip command + a one-line `guidance` pointer — cutting
  per-turn input-token overhead on long untracked sessions.

## [1.42.0] — 2026-06-30

### Changed
- **Delegated workers now default to the `sonnet` model** (author-only mechanical edits)
  instead of inheriting the account default (e.g. opus) — a per-launch token/cost lever.
  Override with `delegate run --model <name>` (e.g. `--model opus` for genuinely hard work);
  an empty model falls back to the account default.

## [1.41.0] — 2026-06-30

### Fixed
- **The "📋 Tracking" line is now emitted by the tool, not self-authored.** `create` and
  `attach` now prefix their own success confirmations with `📋 ` (e.g.
  `📋 Created task [..] <title>`, `📋 Attached to task [..] <title>`), so the friendly
  tracking line IS the command's stdout. The per-prompt nudge and `guidance` no longer tell
  Claude to "tell the user in one short line (📋 Tracking: <title>)"; they now instruct it to
  RUN create/attach and RELAY the tool's own result line verbatim, with an explicit warning
  that fabricating a "📋 Tracking" line without running the command leaves the session
  untracked on the board while telling the user otherwise — closing a gap where a session
  could display "Tracking" without ever being recorded on the board.

## [1.40.0] — 2026-06-29

### Changed
- **HTML board: the footer GitHub link now opens in a new tab** (`target="_blank"` with
  `rel="noopener noreferrer"`), so clicking it no longer navigates the board away. It is
  still a plain hyperlink, not a loaded asset.
- **HTML board: refresh-on-return-from-inactivity (autorefresh on).** The board now
  refreshes the moment you return after being inactive — a window refocus, the tab
  becoming visible again, or the first input after an idle gap each immediately check for
  changes and reload **only if a task changed** — on top of the existing gentle ~10s idle
  poll. The change-check is factored into a reusable `loadRev()`; the reload still happens
  only on a real revision change (and restores open rows / scroll / filters via `ts-auto`).

## [1.39.0] — 2026-06-29

### Added
- **HTML board: the footer now links to the GitHub repo.** Below the
  `task-station v<version> · updated <time>` line, the footer shows the project's GitHub
  URL on its **own line** as a clickable, accent-coloured link (e.g.
  `github.com/ryanconmeo/task-station`). The link text drops the `https://` scheme; the
  `href` is the full URL. The URL is sourced from `plugin.json` (`homepage`, falling back
  to `repository`). It is a plain hyperlink the user clicks — **not** a loaded asset, so the
  board stays fully self-contained (no `src`/`<link>`/`@import`/font/fetch).

### Changed
- **HTML board: the config panel heading is now "Configs"** (was "Current config").

## [1.38.0] — 2026-06-29

### Fixed
- **HTML board: the task-title hover-scroll now works in Safari.** On hover a long task
  title scrolls to reveal its full text. Previously it measured overflow
  (`scrollWidth - clientWidth`) *while* `text-overflow:ellipsis` was active — under which
  Safari reports no overflow, so the animation bailed and the title never scrolled. The fix
  adds the `.scrolling` class (switching to `text-overflow:clip` + `overflow-x:auto`)
  **before** measuring, and makes the title genuinely scrollable so Safari honours the
  programmatic `scrollLeft` animation. The scrollbar stays hidden (`scrollbar-width:none`
  + the existing `::-webkit-scrollbar` rule). Works in Safari and Chrome.

### Removed
- **HTML board: the auto-refresh footer note is gone.** When `board-autorefresh` is on the
  footer no longer shows "updates automatically when a task changes ·
  `/task-station:config --board-autorefresh off` to stop." — the Refresh button and the
  `refreshed <ts>` timestamp in the top kicker already cover it. The footer is now just
  `task-station v1.38.0 · updated <time>` (no dangling separator). The static-snapshot
  note is unchanged for the autorefresh-off case.

---

## [1.37.0] — 2026-06-29

### Changed
- **HTML board: the Refresh button moved up into the top kicker.** The `↻ refresh` button
  is no longer beside the theme toggle in the header — it now lives in the top kicker, just
  to the **right of the `refreshed <ts>` timestamp**, as a light kicker-styled text button
  (`.krefresh`) that blends into the small dim kicker line. The header's right side is once
  again just the theme toggle. The button is always present (even with no timestamp) and its
  click wiring is unchanged: it sets the one-shot `ts-auto` flag then reloads, preserving your
  open rows / scroll position / filters.

---

## [1.36.0] — 2026-06-29

### Changed
- **HTML board: the change-poll is now GENTLE.** The `board.rev.js` change-detection poll
  previously ran every 2s and loaded the sidecar unconditionally, so on `file://` Safari —
  which shows its address-bar loading bar for *every* local resource load — the bar flashed
  every couple of seconds even while you were reading or scrolling. The poll now runs on a
  slow **~10s** cadence and **does nothing on a tick** (no `<script>` load → no loading bar)
  while the tab is **hidden**, the window is **unfocused**, or you **interacted within the
  last ~2500ms** (passive mousemove/scroll/keydown/touchstart/wheel listeners track
  activity); it also no longer polls immediately on load (the page is already current as of
  the render). So the bar appears only occasionally, when the board is idle **and** focused —
  never while you're interacting — and the page still reloads **only when a task actually
  changes**. No server, no dependencies, no remote assets.
- **HTML board: the header timestamp now reads "refreshed".** The top-right kicker label
  changed from `generated <ts>` to `refreshed <ts>` — the value is the board's last write
  time, so "refreshed" is the accurate word.

### Added
- **HTML board: an explicit Refresh button.** The header now carries a `↻ refresh` button
  (beside the theme toggle) that forces a reload on demand — handy when the browser's own
  reload doesn't pick up a change. It preserves your open rows / scroll position / filters
  across the reload (it takes the same restore path as the change-driven auto-reload).

---

## [1.35.0] — 2026-06-29

### Changed
- **HTML board: change-detection now uses a local `board.rev.js` script sidecar instead
  of `fetch`.** `file://` browsers (Safari **and** Chrome) block a local page from
  `fetch`-ing local files, so the 1.33/1.34 poll fell back to a blind periodic full
  reload — the address-bar loading bar every few seconds plus a scroll reset. The poll
  now loads a sibling **`board.rev.js`** `<script>` (which sets `window.__TSREV`) and
  reloads **only when that value differs** from the embedded revision: `file://` pages do
  load local `<script>` resources, and a dynamically-loaded subresource does **not**
  trigger the top-level loading bar. So the board reloads **only when a task actually
  changes** — no more periodic full reload / address-bar bar; the steady state never
  reloads. If a browser 404s the cache-busting query, it falls back once to a no-query
  load and otherwise degrades silently to a static snapshot (never a blind reload). No
  server, no dependencies, no remote assets (the sidecar is a generated local sibling).
- **HTML board: the on-change reload reliably preserves scroll position.** The restore is
  now **re-applied across layout settle** (immediately + on the next animation frame +
  at 60ms and 200ms) so the position survives late height changes, and is **cancelled the
  moment you actually scroll** (wheel / touch / keyboard). Scroll is also saved on
  `pagehide` (Safari fires it more reliably than `beforeunload` on a reload).

---

## [1.34.0] — 2026-06-29

### Added
- **HTML board footer shows the version and when it was installed.** The board footer
  now leads with `task-station v<version> · updated <date time>` before the existing
  snapshot note, for both the auto-refresh and static states. The version comes from the
  plugin's `plugin.json`; the "updated" time is that file's modification time (when this
  installed version was written). Both degrade cleanly to nothing when unavailable.

---

## [1.33.0] — 2026-06-29

### Changed
- **HTML board: refresh when a task actually changes, not on a fixed timer.** With
  auto-refresh on, the board now **polls a tiny `board.rev` revision file** (written next
  to `board.html` — a stable hash of the raw task data) every ~2s and **reloads only on a
  real data change**, instead of reloading every 5s. Time passing no longer triggers a
  reload; creating or modifying a task does. Browsers that block local-file `fetch`
  (Chrome on `file://`) **fall back to a 5s timed reload**. A data-change reload still
  counts as "auto", so your **open rows, scroll position, and search/filters** are
  restored across it; a manual reload starts clean.
- **HTML board: inner scroll boxes contain the wheel.** The summary box (and the
  Open/Resume command boxes) now use `overscroll-behavior:contain`, so scrolling to a
  box's top/bottom no longer chains the scroll to the whole page.

### Added
- **HTML board: an "auto" theme that follows the OS.** The light/dark toggle is now a
  **three-way cycle — auto → light → dark** — defaulting to **auto**, which follows the
  OS appearance **live** (via `prefers-color-scheme`, re-resolving the instant your system
  flips). The button label shows the active mode (`◑ auto` / `○ light` / `● dark`), and
  the chosen mode persists to `localStorage` across reloads.

## [1.32.0] — 2026-06-29

### Changed
- **HTML board: auto-refresh keeps your place; a manual reload starts clean.** The opt-in
  5s refresh is now a **JS-timed reload** (no `<meta http-equiv="refresh">`) that tags
  itself so the next load restores your **open rows, scroll position, and search/filters**
  — a seamless live tab. A **manual** browser reload, a **freshly-opened** `/todo board`,
  or the **reset** button now starts **clean**: all rows collapsed, top of page, empty
  search/filters (the stored open set is cleared too). With auto-refresh off there's no
  timer, so every load is clean.
- **HTML board polish.** Expanded task titles now show in the **accent colour** (reverting
  on collapse); the config expansion's **usage command + default value `<code>` are
  accent-coloured** to stand out from the dim description; the **Category column is
  narrower** (168→120px, Effort 132→118px) so the task title gets more room and Category
  sits closer to Effort; the **Categories note moved beside the header**
  ("auto assigned by best fit"), replacing the standalone line under the heading.
- **Clearer, more human config descriptions.** Rewrote every `config` board description in
  plain language — `--categories`/`--category-overrides` now explain the toggle/edit usage
  and the categories.json structure (and drop their options cell), `--bare-cmds` lists all
  four aliases (`/todo`, `/done`, `/pin`, `/repos`), `--auto-categories` says "category"
  (not "slot"), and `--update-check`/`--board-autorefresh` are reworded.

## [1.31.0] — 2026-06-28

### Changed
- **HTML board: tidier "Current config" expanded rows.** Dropped the
  "bold + underline = current" hint from the `flag · options` header. Inside each
  expanded row, the usage command is now just a bare `<code>` block (the "Set with:"
  label is gone), the default value is shown in a `<code>` block with no trailing
  period, and that default line now sits **directly below the usage block** (before
  the description). Escaping is unchanged and the board stays inline with no external
  assets. The terminal config board and the Commands panel are untouched.

## [1.30.0] — 2026-06-28

### Changed
- **HTML board: the opt-in auto-refresh is now flicker-free.** The board preserves and
  restores your **scroll position** across the 5s reload (saved continuously to
  `sessionStorage` — a throttled scroll listener plus `beforeunload`/`visibilitychange`,
  restored before the first paint *after* the open sections are re-expanded, with
  `history.scrollRestoration="manual"` so the browser doesn't fight it) and **paints the
  themed page background on the very first frame** (the `<head>` pre-paint init sets the
  resolved-variant page colour on `documentElement`), so there's no longer a jump-to-top
  or a brief white flash on each refresh. Open sections already persisted; the meta-refresh
  mechanism and the 5s interval are unchanged, and everything stays inline with no external
  assets. The static (non-autorefresh) board is unaffected.

## [1.29.0] — 2026-06-28

### Added
- **Configurable board browser (`--board-browser`).** `/task-station:config --board-browser
  "Google Chrome"` (or `Firefox` / `Safari` / `Arc`, …) makes `task-station board --open`
  launch `board.html` in that browser via `open -a "<App>"`; the env var
  `TASK_STATION_BROWSER` takes precedence, and `--board-browser` with no value clears it
  back to your system default browser. macOS-only, best-effort, never raises.

### Changed
- **HTML board: category alignment, trimmed expansions, and a clearer config panel.**
  - **Category counts align in a fixed column.** The per-state counts (`N new · N active ·
    N closed`) now begin at the same x on every category row — the pill·CUSTOM·description
    group sits in a fixed-width `.catleft` (widest tag+label + 4ch), so counts line up just
    past the longest description instead of floating to the page edge.
  - **Category expansion trimmed.** The repeated per-row auto-assignment note and the
    “Customized (CUSTOM)” line are gone; auto-assignment is now explained once by the
    section-level note under **Categories**. An overridden row now shows a
    **`Default: <dot> [TAG] label`** line with the shipped default for that slot.
  - **Config expansion is clearer.** Every setting’s expanded body now leads with a
    **“Set with:”** command line (always first), state explanations render multi-line (e.g.
    `--statusline` on / provider-only / off, each on its own line, plus a live “Current:”),
    and non-current option tokens are **grayed** while the current one stays bold +
    underlined. The **theme** options drop the trailing “· …” — there is just one theme
    (`sands`). `--category-overrides` shows an exact count (`none` / `1 override` / `N
    overrides`).
  - **Open task row reads as one card.** The expanded row’s header no longer has the
    internal 2px accent divider/`panel2` background — it shares the detail’s background so
    header and content read as one card (distinct via the left accent stripe + disclosure
    triangle), while the whole expanded row is set apart from its neighbours by a top +
    bottom accent boundary.
- **`config.board_rows()` rows are now 6-tuples** `(flag, value, options, description,
  extra_lines, set_with)`, consumed identically by the terminal `config` board and the HTML
  help panel so the two never drift.

## [1.28.0] — 2026-06-28

### Changed
- **HTML board: config + categories polish, and a generic expand-persistence fix.**
  - **Current value is bold _and underlined_.** The options-column marker for the current
    value now adds an **underline** to the accent-bold (`.copts strong`); the config header
    hint reads **“bold + underline = current”**.
  - **ALL expandable rows survive the auto-refresh.** The open-state persistence is now
    **generic**: every persistable `<details>` carries a stable, namespaced `data-key`
    (`row:<seq>` task rows, `cfg:<flag>` config rows, `cat:<color>` category rows,
    `wk:<seq>` worker sub-details) and a single `details[data-key]` handler mirrors the open
    set to `localStorage`, so config dropdowns and category expansions — not just task rows
    — stay open across the opt-in 5s meta-refresh. The closed “see more” is excluded (the
    filter JS manages it).
  - **Bottom note fully-qualifies the non-bare command.** Since `config` has no bare alias,
    the auto-refresh note always writes **`/task-station:config --board-autorefresh off`**;
    the static-snapshot note’s **`/todo board`** reference is bare-aware (`/todo board` when
    bare aliases are on, else `/task-station:todo board`).
  - **`--statusline` & `--desktop-bridge` show on/off.** Both columns simplify to **on/off**
    (statusline `installed (host)`/`provider-only`→`on`, `off`→`off`; desktop-bridge
    `installed`→`on`); the underlying state + a plain-English explanation of each state move
    into the expanded body.
  - **`--category-overrides` shows the exact count.** `none` / `1 override` / `N overrides`
    (correct singular/plural — the old `(s)` is gone).
  - **`--categories` explains edit vs toggle** in its expansion.
  - **Category rows are expandable.** Each Categories row is now a `<details>` whose summary
    is the inline row (pill · CUSTOM · description · counts) and whose body shows the
    category’s **“when to use”** guidance plus a note on how auto-assignment works. The
    per-state **counts moved left** (right after the description, no longer floated to the
    page edge), and a dim note under the **Categories** heading invites expanding a row.
  - `config.board_rows()` rows are now **5-tuples** `(flag, value, options, description, extra)`,
    wired into BOTH the terminal config board and the HTML config panel; when `extra` is
    present it carries the accurate usage and the generic “Set with” line is skipped.

Still a single local file: inline `<style>`/`<script>` only, no external assets, all
injected text HTML‑escaped.

## [1.27.0] — 2026-06-28

### Changed
- **HTML board: five fixes.**
  - **Config options drop the `*`.** The current value in each options cell is now marked
    by **bold only** (e.g. `on` · off) — the trailing asterisk is gone; the header hint
    reads **“bold = current”** instead of “* = current”.
  - **Theme toggle aligned with the header.** The header row is `align-items:center`, so the
    light/dark toggle sits **in line with the big `/todo board` `<h1>`** rather than floating
    above its baseline.
  - **Effort colour‑coded by tier.** The effort cell is tinted by tier — **xl red · l orange ·
    m yellow · s green · xs white** — reusing the per‑variant **category highlight palette**, so
    each tier is vivid AND visible in both light/dark (including white in light mode). Unknown/
    empty effort stays uncoloured.
  - **Copy buttons on the commands.** The **Open the task** and **Resume the hub session**
    commands each gain a small **copy** button that copies the command to the clipboard
    (async clipboard API with a `textarea` + `execCommand('copy')` fallback for `file://`).
  - **Expanded rows survive the auto‑refresh.** A row's open/closed state is mirrored to
    `localStorage` (keyed by seq) and restored on load, so an expanded task **stays expanded**
    across the opt‑in 5s meta‑refresh.

Still a single local file: inline `<style>`/`<script>` only, no external assets, all
injected text HTML‑escaped.

## [1.26.0] — 2026-06-28

### Changed
- **HTML board Current config panel redesigned into expandable rows.** Each setting is
  now an **expandable row** (`<details>`/`<summary>`, same spirit as the task rows)
  under a `flag · options` header — `flag` replaces the old `setting` label. A single
  **“options”** column shows all the choices with the **current value bold + asterisked**
  (e.g. `on*` · `off`); a reported‑STATE value that isn't one of the choices (e.g.
  statusline `provider‑only`, categories `3/12 (CORE)`) is marked, then the settable
  options follow dimmed. The match is computed from the **raw** value, before the
  `--tint‑theme` “value → variant” concat, so an enum like `auto` still matches its
  token. The **description, default, and per‑flag usage** (`Set with:
  /task-station:config --<flag> <hint>`) move into the **expanded** section, and the
  `--tint‑theme` row notes its resolved variant there. The old bottom legend is
  **removed**; path rows (no options) render their value plain (no asterisk/bold).

Still a single local file: inline `<style>`/`<script>` only, no external assets, all
injected text HTML‑escaped. The terminal `config` board and the Commands panel are
unchanged.

## [1.25.0] — 2026-06-28

### Changed
- **Config panels distinguish current STATE from settable inputs.** The “options”
  column was overloaded: for a pure toggle the current value *is* one of the choices
  (`on`/`off`), but integration rows report install **state** (`--statusline`
  “provider‑only”, “installed (host)”) and action rows report a **status summary**
  (`--categories` “3/12 (CORE)”, `--category‑overrides` “none”) — so the current value
  was sometimes *not* one of the choices, which confused readers.
  - **HTML board (Current config table):** gained a **`setting · current · set with`**
    header row (scoped to the config table — the Commands table is untouched). A
    current value that is a **reported state** (not one of the option tokens) now
    renders in a distinct **dim/italic** style (`.cstate`), while a real enum/toggle
    value renders plain; the state test is computed from the **raw** value, before the
    `--tint‑theme` “value → variant” display concat, so an enum like `auto` still reads
    as a value. The footer legend now reads **“current = present state · set with =
    what you pass to `config --<flag> <value>`.”**
  - **Terminal `config` board:** gained a matching **columns legend**
    (`<flag> · current value/state · choices = what you pass`) above the set/reset hint.

Still a single local file: inline `<style>`/`<script>` only, no external assets, all
injected text HTML‑escaped.

## [1.24.0] — 2026-06-28

### Added
- **New `/pin` command.** `/pin` pins **THIS** session as the current task's canonical
  resume target, so `/todo` always resumes here — it resolves the session's attached
  task (`get_link`) and pins it; if nothing is attached it says so. Installed as a
  **bare alias** (`/pin`) when `--bare-cmds` is on, alongside `/todo` + `/done`; the
  `/task-station:pin` form always works. `pin --task …` (pin a session across listed
  tasks) is unchanged.

### Changed
- **Category rows are now an inline flow (HTML board).** Each Categories row reads
  left‑to‑right: a **colour pill wrapping ONLY the `[TAG]`** (sized to its own content,
  no longer stretched to a uniform width), then a **CUSTOM** marker **only when the
  category is overridden** (content‑sized; the old empty‑placeholder column is gone),
  then the **description** as plain uncoloured text (never truncated), with the
  **per‑state counts pushed to the far right**. Replaces the previous 3‑column grid.
- **Generated timestamp moved to the top.** The `generated <ts>` now sits at the
  **top‑right of the header** (in the kicker), space‑between from the
  “claude code • task‑station” left group. The bottom note keeps only the
  static‑snapshot / auto‑refresh line — no more “generated …” prefix there.
- **Current config panel shows possible values + defaults + how‑to‑set.** The config
  table gained a third column listing each flag's **options** and its **default**
  (parsed from the description's trailing `(default: X)`, shown dimmed); path rows show
  none. A single compact footer line documents `config --<flag> <value>`.
- **Commands panel reflects the bare‑cmds state.** When bare aliases are **off** the
  panel shows the `/task-station:` prefixed labels (`/task-station:todo`, `…:pin`, …);
  when **on** it shows the bare `/todo`, `/done`, `/pin`. A helptext line under the
  panel states the current state and how to switch.

Still a single local file: inline `<style>`/`<script>` only, no external assets, all
injected text HTML‑escaped.

## [1.23.0] — 2026-06-28

### Changed
- **Header kicker now reads “claude code • task-station” together.** The two words
  were split to opposite corners (`justify-content:space-between` threw
  “task‑station” into the far top‑right). They are now **left‑aligned and sit side by
  side** with a small gap and a dimmed **“•”** separator between them — no longer
  corner‑split.
- **Category pills are a uniform width (HTML board).** Every chip in the Categories
  list now **stretches to fill** the shared `max-content` column (the widest chip), so
  all pills are **identically sized** instead of each sizing to its own text. Readable
  text over the highlight is preserved; the label still never truncates.
- **CUSTOM marker aligned in its own column.** The Categories list became a **3‑column
  grid** (chip | counts | marker). The marker moved out of the counts cell into its
  own column, so every **CUSTOM** marker lines up **flush‑left** across rows (a
  non‑overridden row emits an empty placeholder cell to keep the columns aligned). The
  per‑state counts stay flush‑left in column 2.
- **Board lede trimmed.** Dropped the “the same Open / Closed grid as the terminal
  task board;” clause; the lede now reads “… expand any row for its full title,
  summary, open/resume commands, **and** briefing.” (Oxford comma.) The README board
  paragraph drops the same terminal‑comparison and now describes an **Open / Closed
  grid** directly.

Still a single local file: inline `<style>`/`<script>` only, no external assets, all
injected text HTML‑escaped.

## [1.22.0] — 2026-06-28

### Added
- **Stories links (`stories`).** A new task field — a list of `{url, desc}` work‑item /
  story links, **mirroring `prs` exactly** (upsert by url, optional per‑story
  description, back‑compat bare‑string load) but with **no** auto‑extraction.
  - CLI: `update --story '<url>' [--story-desc '<text>']` — upserts by url; a
    `--story-desc` with no `--story` updates the most‑recent stored story.
  - Helpers `add_story` / `set_story_desc` / `merged_stories` mirror the PR ones; the
    field rides along in the serialized task blob (no schema migration).
  - Renders a **Stories** block in **both** the terminal task detail (in Artifacts,
    near PRs) and the HTML expanded briefing — **each story on its own line**: the
    linked url then its description when present (same layout as PRs).

### Changed
- **Category labels no longer truncate (HTML board).** The Categories list dropped the
  ellipsis/clip on the label, and the chip column is now **uniform**: the list is a
  grid whose first column is `max-content` (the widest chip) with each row dissolved in
  via `display:contents`, so every chip cell shares one width while the per‑state
  counts stay flush‑left‑aligned in column 2. The chip background still sizes to its
  own text.
- **Header (HTML board).** The `<h1>` is now the command **“/todo board”** (was “task
  board”). The top kicker became a **strip** — **“claude code” flush‑left, “task‑station”
  flush‑right** — and no longer carries the command. The theme toggle stays in its
  corner; `<title>` stays “task-station — board”.
- **Expanded row header is distinct from its contents.** An open row’s summary (the
  clickable header) now uses a different background (`--panel2`, not the detail’s
  `--open`) and a stronger **2px accent bottom border**, so it reads as the header of
  the expanded card rather than blending into the detail body.
- **Task number more pronounced.** The board’s `#` cell (`.c-seq`) uses `--ink` (not the
  faded `--dim`) and a slightly heavier weight — modestly clearer, not loud.

Still a single local file: inline `<style>`/`<script>` only, no external assets, all
injected text HTML‑escaped.

## [1.21.1] — 2026-06-27

### Changed
- **Categories panel refinement (HTML board).** Still a single local file, inline
  `<style>`/`<script>` only, no external assets, all text HTML-escaped.
  - **The category colour is now an inline CHIP** that wraps **only the tag + label**,
    sized to its own text — the full-width row background is gone (no swatch either).
  - **Per-state counts move to a second column** rendered as **“N new · N active · N
    closed”**, each word tinted by its **STATUS** colour (new = `--so`/blue,
    active = `--sa`/green, closed = `--sc`/grey).
  - **Counts are flush-left-aligned across rows** via a **fixed first grid column**
    (`.catitem{display:grid;grid-template-columns:240px auto}`), so col 2 starts at the
    same x in every row.
  - **The override marker reads `CUSTOM`** (renamed from `OVERRIDDEN`/`overridden`).

## [1.21.0] — 2026-06-27

### Changed
- **Status relabel: `open` displays as “new.”** The three per-task states now read
  **New (○) · Active (●) · Closed (✕)** everywhere user-facing — the HTML status pill,
  the HTML status filter option, the terminal board status column/legend
  (`● active · ○ new · ✕ closed`), and the guidance/help text. The **stored value stays
  `open`** (no migration, full back-compat) and the not-closed board **section keeps the
  name “Open”** (it groups New + Active); only the per-task label changed. **`new` is
  accepted as an input alias** for `open` wherever `open` was (`status --task <ref>
  new|active`, `new_task` on create).
- **Third HTML task board UX pass** — still a single local file with **inline
  `<script>`/`<style>` only**, **no external assets**, all injected text **HTML-escaped**.
  - **Curated category highlight palette.** The uniform `brighten()` for the
    stripe/category colour is replaced by a **hand-tuned per-category, per-variant
    highlight** (`--cat-stripe`) that is clearly **distinct** and **true to its name**:
    **blue vs silver** are now obviously different, **white = design** (white in dark,
    nearest visible light shade in light), **black = general** (black in light, visible
    near-black in dark), **brown = data** reads brown, **gold = docs** reads gold;
    red/orange/yellow/green/purple/pink stay vivid + distinct. Used by the **left
    stripe**, the **category tag**, and the **categories-list rows**.
  - **Top kicker → `task-station · /todo board`** (names the command). The `<h1>` stays
    **“task board”** and the `<title>` stays `task-station — board`.
  - **Categories panel redesign.** Now a **single-column list**; each **row is filled
    with the category highlight itself** (the separate swatch is gone — the row's colour
    IS the indicator, with readable text on top), showing its `[TAG]`, (override-aware)
    label, an **“overridden” marker** for user-customized categories, and a **per-state
    count** (`N new · N active · N closed`).
  - **Reactive closed “see more.”** An active search/filter now applies across **every**
    closed row (not just the first 5): the see-more **force-opens** and updates its count
    to reveal matching closed tasks from the hidden batch; clearing restores
    `see more (N more)`.
  - **Reset button.** A **reset** control next to the search/filters clears the search box
    + category + status filters and restores the default view (re-collapsing the closed
    see-more). Inline JS, no external assets.

## [1.20.0] — 2026-06-27

### Changed
- **Second HTML task board UX pass.** Still a single local file with **inline
  `<script>`/`<style>` only** — **no external assets**, all injected text stays
  **HTML‑escaped/inert**.
  - **Brighter category highlight.** A pure‑stdlib `brighten()` (hex → HLS → raise
    lightness into a vivid mid range + boost saturation → hex) tints the row's **left
    stripe** (and the categories‑panel swatch) in the category's own **hue**, so
    categories are distinguishable at a glance in **both** the dark and light variants.
  - **Hover title scroll fixed.** Hovering a row now scrolls the **full** title (no
    ellipsis/clip) horizontally at a **linear, constant speed** and **snaps back
    instantly** to the start on mouse‑out — no easing, no animated return.
  - **Resume label → “Resume the hub session.”** The primary session is named the hub;
    a pin is indicated inline (`Resume the hub session 📌`).
  - **Worker subsection → “Worker sessions.”** The “you usually don't resume these” note
    is replaced with a short **“(for debugging)”** tag; still collapsed/secondary.
  - **Naming reverted to “task board”** on the page heading/kicker/lede and in the
    `[BOARD]` CLI message (it's redundant since you invoke it via `/todo board`). The
    `<title>` stays `task-station — board`.

### Added
- **Search + filters.** A top‑of‑board **search box** (live‑filters by title / goal /
  state / summary) plus **category** and **status** (all / open / active / closed)
  filters, driven by inline JS reading `data-title` / `data-cat` / `data-status` /
  `data-search` on each row — works with the expandable rows and the closed “see more”.
- **Categories panel** at the bottom: each in‑use category as dot + `[TAG]` + label
  (reflecting your config **overrides**) with its **task count**, sorted by count.
- **Closed “see more.”** The Closed section renders the first **5** tasks and folds the
  rest into a native `<details>` (“see more (N more)”); no expander when ≤ 5.

## [1.19.0] — 2026-06-27

### Added
- **HTML `/todo board` UX overhaul.** The board is a local file opened in a browser, so it
  now uses **inline `<script>`/`<style>`** — still **no external assets** (no `src="http"`,
  `<link>`, `@import`, `url(http)`, or remote fonts) and all injected text stays
  **HTML-escaped/inert**. Highlights:
  - **Light/dark toggle** (top-right) that flips **both** embedded palettes (page chrome +
    per-category colours, per variant) in place and **persists to `localStorage`**, so the
    opt-in auto-refresh never resets your choice.
  - **Hover auto-scroll** of an overflowing collapsed title (smooth, no layout shift),
    resetting on mouse-out.
  - **Full, untruncated title** leads the expanded detail.
  - **Open vs. Resume**, two clearly-labeled commands: **Open the task** (`/todo <n>` —
    attaches it to *this* session, the recap) above **Resume the session**
    (`cd … && claude --resume …` — jumps back into the original working session).
  - **Pin merged into the resume area** — the resume label becomes *“Resume the session
    (pinned 📌)”*; the separate pinned banner is gone.
  - **PRs each on their own line**, the linked url then an optional **description**.
  - **Distinct background** on an expanded row so its bounds are obvious (both variants).
  - **Left stripe is the category's background colour** (not the bold/accent colour).
- **Per-PR descriptions.** `prs` entries are now `{url, desc}` (a plain url string still
  loads, `desc=""`). `update --pr '<url>' [--pr-desc '<text>']` **upserts by url**; a
  `--pr-desc` with no `--pr` updates the most-recent stored PR. Auto-extracted PRs get
  `desc=""`. `add_pr`/`merged_prs` and the 1.18 dedup all key on url.

### Changed
- **“task board” → “/todo board”** on the page (heading + kicker) and in the `[BOARD]` CLI
  message.

## [1.18.0] — 2026-06-27

### Added
- **Structured, stored task digest — resume loads a briefing, the board is a real tracker.**
  The digest is **first-class stored data** written when the work is summarised (by the
  model, via CLI flags) — **not derived at render time** — and rides the existing task JSON
  blob (no schema migration). Five fields:
  - **`goal`** — one line, *what "done" looks like*. `create --goal '…'` · `update --goal '…'`.
  - **`state`** — current standing + next step (since 1.15). `update --state '…'`.
  - **`steps`** — a granular checklist of `{text, done}` with **stable 1-based indices**.
    Seed at create with repeatable `--step '…'`; maintain with `update --step-add '…'`,
    `--step-done N`, `--step-undone N` (an out-of-range `N` warns, never crashes).
  - **`decisions`** — append-only log of choices. `update --decision '…'`.
  - **`prs`** — PR URLs **now stored** (`update --pr '<url>'`), **merged** with the 1.15
    auto-extraction from the log/summary/state (deduped, stored-first then first-seen).
- **Progress rollup on BOTH boards.** A new `step_progress(task)` → `(done, total)` helper
  drives a compact **`✓N/M`** appended to the Task cell of the terminal `/todo` list
  (ASCII **and** Markdown — no new column, the grid + verbatim contract are intact) and a
  **mini progress bar + `N/M`** on each HTML board row that has steps, with the full
  checklist (✓/☐ + rollup) in the expand.

### Changed
- **Terminal task detail (`/todo <n>`) is digest-first.** It now leads with
  **Goal → State (next/standing) → Steps (checklist + `N/M` rollup) → Decisions →
  Artifacts (files · PRs · repos)**, then the resume one-liner(s), and moves the full
  **Summary last**. Supersedes (and extends) the 1.15 *Briefing* block — no duplication.
- **HTML board expand** shows the same digest-first content (goal · steps checklist with
  rollup · decisions · stored PRs) above the resume block and the scroll-capped Markdown
  summary. `goal`/`state`/`decisions` render through `mdlite` (HTML-escaped first, so the
  board stays self-contained).
- **Guidance steers digest upkeep + content hygiene.** `guidance` now nudges keeping
  `state` fresh, ticking `steps` (`--step-done N`), and recording `--decision` as you work
  — *this is how a task stays resumable* — and documents the new flags. It also clarifies
  that **`summary` is the stable description**: put the running record (progress/ship
  notes/decisions) in `--state` / `--decision` / the activity log, **not** in
  `--append-summary` (which stays available, just no longer recommended for progress notes).

## [1.17.0] — 2026-06-27

### Added
- **Opt-in board auto-refresh (`config --board-autorefresh on`, default off).** An open
  `/todo board` tab can now stay live without a manual re-run. When enabled, the board
  injects a single `<meta http-equiv="refresh" content="5">` tag (the **only** non-static
  element — still **no JavaScript**, no network, no external assets) and the **Stop hook
  quietly regenerates `board.html`** so the page reloads onto current state. Strictly
  gated and best-effort: it regenerates **only when the flag is on AND `board.html`
  already exists** (i.e. you've opened the board at least once) — it never creates the
  file for someone who doesn't use the board, and swallows all output/errors so the Stop
  hook is never disrupted. The snapshot note switches to *"auto-refreshing every 5s ·
  `config --board-autorefresh off` to stop"* when on. `TASK_STATION_BOARD_AUTOREFRESH`
  (on/off/1/0/true/false) overrides the config setting.

### Changed
- **Expanded task detail is now digestible without an LLM.** The expand **leads with the
  at-a-glance digest** — briefing (next/standing · files · PRs · repos), then the resume
  one-liner(s) — and moves the **full summary last**, so the eye hits the structured
  digest before the wall of text.
- **The summary renders as light Markdown** (new pure-stdlib `tools/mdlite.py`): `#`/`##`/
  `###` headings, `-`/`*` bullet lists, blank-line paragraphs, `**bold**`, `` `code` ``,
  `[text](url)` + bare http(s) URLs as links, and `---` rules. The text is **HTML-escaped
  first**, so a literal `<script>` (or any raw HTML) in a summary is rendered inert as
  text — the board stays self-contained / no-injection. Unknown syntax passes through as
  escaped text. The rendered summary sits in a **scroll-capped container** (`max-height`
  ≈ 16em, `overflow-y:auto`) so a huge blob scrolls in place rather than dominating the
  card.

## [1.16.0] — 2026-06-27

### Changed
- **Visual board redesigned to mirror the terminal `/todo` board.** `/todo board` now
  renders two sections (Open, then Closed), each a **grid with the same columns as the
  terminal board** — status · # · task · category · effort · activity — and **every row is
  expandable** via native `<details>`/`<summary>` (still **no JavaScript**, still one
  self-contained file with no external assets).
  - **Labeled status pills** — `open` / `active` / `closed` as colored, clearly
    non-interactive badges (the bare `✕` that read as a clickable close button is gone).
  - **Expanded rows lead with the task summary**, then a **prominent hub/pinned resume
    one-liner with its last-activity time** (reusing the *exact* resume computation the
    terminal task-detail uses, cwd self-corrected from the transcript). A **Pinned**
    indicator shows when the task is pinned; in-project **workers** move to a separate,
    de-emphasised collapsed subsection.
  - **Resume/worker commands never wrap** — they scroll within their own box
    (`white-space:nowrap; overflow-x:auto`); the page body never scrolls horizontally.
  - **Colours match the terminal exactly** — per-category background/foreground/accent
    come from the active theme's resolved (light/dark) palette, and the page's own
    light/dark chrome tracks the resolved variant.
  - **Help panel** at the bottom lists the `/todo` commands (shared `_COMMANDS_HELP`
    source) and the **current config** (`config.board_rows()`), plus a **snapshot note**
    making clear the board is static — re-run `/todo board` to refresh.
- Internal: `resume_command()` is now a thin wrapper over a new structured
  `_resume_target()` (single source of truth for the resume line + its timestamp), and
  `worker_lines()` over a structured `worker_targets()`, so the HTML board and terminal
  detail can never drift. `config.board_rows()` is the shared data source for the config
  board (terminal) and the board's config panel (HTML).

## [1.15.2] — 2026-06-24

### Fixed
- **`/todo board` (and `config --theme preview`) failed via the engine symlink** with
  `ModuleNotFoundError: No module named 'render_board'`. Both located `tools/` from the
  script dir without dereferencing the `~/.claude/task-station-engine` symlink, so they looked
  in `~/.claude/tools` instead of the plugin's `tools/`. Now resolved via `os.path.realpath`.

## [1.15.1] — 2026-06-24

### Added
- **`/todo board`** — open the visual HTML board from the slash command (previously only the
  `task-station board` CLI subcommand existed, with no obvious way in). `/todo board` renders
  the board and opens it in your browser by default; listed in the `/todo` Commands help.

## [1.15.0] — 2026-06-24

### Added
- **Per-task context briefing — resume a briefing, not just a transcript.** Each task now
  carries a deterministic briefing rendered as a **Briefing** block in the task detail
  (just above the recent-activity log): recently-edited **files** captured deterministically
  by the PostToolUse hook (`touch-file`, deduped + capped at 15, most-recent-last); **PR
  links** *derived* on render by scanning the activity log/summary/state for GitHub
  (`…/pull/<n>`) and Azure DevOps (`…/pullrequest/<n>`) URLs (deduped, never stored); and a
  model-curated **state**/next-step line maintained with `update --state`. Distinct from the
  summary (what the task *is*) — state is where it *stands* + what's next. **No LLM, no
  network, stdlib-only**; the "intelligence" is the model already in the loop keeping `state`
  fresh. Extra task keys ride along in the existing serialized blob — no schema migration.
- **`task-station board` — a self-contained visual HTML board.** Renders every task (open +
  closed) as colour cards using the active theme's per-category palette — status glyph, #seq,
  title, `[TAG]` category, effort gauge, last activity — plus each task's briefing
  (state/next-step, repos, PR links, recent files) and its resume one-liner. Writes
  `<data_dir>/board.html` and prints the path. Reuses the theme-preview HTML approach
  (`tools/render_board.py`): inline CSS, dark, responsive, no horizontal scroll, **no server,
  no dependencies, no external assets, no LLM**. `--open` best-effort opens it (macOS).

## [1.14.5] — 2026-06-24

### Changed
- **Positioning corrected: stop calling native tasks "ephemeral."** Native Claude Code
  tasks have persisted across sessions since 2025-01-23 — the old README/hook claim was
  inaccurate. Re-framed around the real, demonstrable gap: native tasks are **siloed per
  session-list with no cross-session board and no link back to the session that holds their
  context.** Task Station is the cross-session console that **complements** native — one board
  of every task, each bound to its exact resumable session (cwd recovered). Updated the README
  intro and the UserPromptSubmit/guidance hook copy accordingly.

## [1.14.4] — 2026-06-24

### Fixed
- **`--strict-delegation` managed-block marker now names the current flag.** The
  `<!-- BEGIN task-station:delegation-policy … -->` comment still read
  `task-station config --policy` (the pre-rename name). It now reads
  `--strict-delegation`. Block detection matches on the stable marker prefix, so a
  block installed by an older version (old `--policy` text) is still found, replaced,
  and removed — never orphaned; re-running `--strict-delegation on` upgrades the marker.

## [1.14.3] — 2026-06-24

### Documentation
- **`delegating-work` skill: document the 1.14.2 resume semantics.** The bundled skill's
  "Resume and persistent workers" section now explains the separate worktree vs
  read-only/main-checkout registry slots, that a no-`--worktree` resume self-routes to the
  worktree worker, that a stale main-checkout entry is refused (rebind with `--worktree`, or
  `--fresh`), and that `--seq` auto-inherits for read-only delegations too. (Bumped so the
  doc reaches installs.)

## [1.14.2] — 2026-06-24

### Fixed
- **`delegate.py` never resumes a worker into the main checkout.** A resume of a
  tracked seq with no `--worktree` could land in the repo's MAIN checkout (on an
  unrelated branch) and spin a stale session, because a prior read-only (no-worktree)
  delegation had overwritten the `seq:project` registry entry — its saved `dir` was
  the main checkout. Now worktree workers and read-only/main-checkout workers live in
  **separate registry slots** (`seq:project` vs `seq:project@main`), so a read-only run
  can never clobber a worktree binding, and a no-`--worktree` resume **self-routes to
  the worktree worker**. A pre-fix `seq:project` entry still pointing at the main
  checkout is **refused** with guidance (pass `--worktree` to rebind, or `--fresh`)
  rather than resumed. `--seq` is now also inherited from the attached task for
  read-only delegations (not just `--worktree` ones), so `delegate --project X` from an
  attached session resolves to that task's worktree worker with zero flags. The whole
  fix removes the need to remember to pass `--worktree`/`--fresh` on resume.

## [1.14.1] — 2026-06-24

### Fixed
- **Window/tab title updates immediately on rename (and create/attach).** Renaming a
  task (`update --title`) previously only relabelled the terminal on the *next* prompt,
  when the UserPromptSubmit hook re-emitted the OSC title — the same lag the tint fix
  addressed. A new best-effort `_emit_title_to_origin` writes the `#<seq>: <title>`
  escape straight to the originating TTY the moment the title changes, mirroring
  `_emit_tint_to_origin`. Wired into rename, create, attach, auto-fold, and provisional
  auto-create. No-op (never raises / never writes stdout) when `--title` is off or the
  TTY can't be resolved — the prompt hook still relabels as before.

## [1.14.0] — 2026-06-24

### Added
- **ultracode fan-out hints** (opt-out via `config --ultracode-hints off`, default
  on). On **fan-out-worthy** tasks — effort **L/XL**, or **RESEARCH / REVIEW /
  DATA** at **M+** — Task Station surfaces a **human advisory** (on the task
  detail recap and the SessionStart attached-task line) suggesting Claude Code's
  `ultracode` multi-agent breadth for the task's **read/analyze/design/review**
  phases. When an **ultracode signal** is present that turn (the word-boundary
  token `ultracode` in the prompt — the harness's own opt-in trigger), it instead
  **steers the model** to keep every repo write on the delegation path (a worktree
  worker off the repo's base branch, with story + PR) and never in workflow
  subagents. Worthiness is **derived** from the task's effort + category (no new
  task state). Task Station **never fires orchestration itself** and **never
  suggests ultracode for repo writes**; trivial work (xs/s, plain questions) never
  triggers it. Design: [docs/superpowers/specs/2026-06-24-ultracode-fanout-hints-design.md](docs/superpowers/specs/2026-06-24-ultracode-fanout-hints-design.md).

### Changed
- **README rebalanced** to highlight the headline features and the full
  config-flag surface (every flag documented with its purpose + default).

## [1.13.1] — 2026-06-24

### Fixed
- **Dark Sands: separate REVIEW (orange) and DOCS (gold) backgrounds.** Their tints
  (`#34200d` / `#2a2210`) were near-identical muddy browns. Orange → `#3a1b08`
  (redder/warmer) and gold → `#2e2a0c` (greener/amber) so REVIEW reads red-orange
  (G ≪ R) and DOCS reads amber (G ≈ R) at the same dark lightness. Light Sands was
  already well-separated and is unchanged.

## [1.13.0] — 2026-06-24

### Added
- **Composable status-line convention ([docs/STATUSLINE.md](docs/STATUSLINE.md)).**
  A small, vendor-neutral convention for composing multiple segments under Claude
  Code's single `statusLine.command`: **providers** are executables in
  `${CLAUDE_CONFIG_DIR:-~/.claude}/statusline.d/` that speak the statusLine
  stdin-JSON contract (empty output / non-zero exit ⇒ skipped); **hosts** own
  `statusLine.command`, run every provider with the JSON on stdin, and join the
  non-empty segments. Reference-implemented here, intended for extraction into a
  neutral repo + an upstream feature request.
- **`config --statusline on` (opt-in, default off).** Installs a self-sufficient
  task-station status-bar **host** (it embeds the ~30-line compose routine —
  `lib/statusline-host.sh` — and needs no external conductor) when nothing else
  owns the bar, registers a segment **provider** (`statusline.d/50-task-station.sh`)
  either way, and **never clobbers** an existing/foreign `statusLine`. Writes to
  `settings.json` are backed up first and fully reversible (`--statusline off`
  removes only what we own). Provider + host honor `CLAUDE_STATUSLINE_WIDTH` /
  `CLAUDE_STATUSLINE_SEP`.

### Changed
- **The `statusline.d/` provider drop-in is now written only when `--statusline`
  is on** (was unconditional in the SessionStart hook), so task-station no longer
  writes into a user's `statusline.d/` unbidden.

## [1.12.0] — 2026-06-23

### Added
- **`guidance` now emits the full command reference.** Alongside the existing
  track/attach/skip how-to, `task-station guidance` prints a compact reference for
  every subcommand — purpose plus key flags, the lifecycle (open ○ → active ● →
  closed ✕), and the ref forms — so the model-facing guidance is the single source
  of truth for the command set instead of each session reinventing a command.
- **Hidden `delete --task <ref>` maintenance command.** A real hard-delete that
  removes a single task's record and detaches any session linked to it. Hidden from
  `--help`, the config board, and the README (documented only in `guidance`): the
  lifecycle is normally close-not-delete — prefer `done`/close.

### Fixed
- **Category tint now applies IMMEDIATELY on create/attach/recategorize.** Assigning
  a colour (via `create`, `attach`, `update --color`, or guaranteed-tracking
  auto-create) previously only tinted the terminal on the *following* prompt, since
  nothing emitted the escape at assign time. The colour is now emitted best-effort
  to the originating TTY the moment it is set; if the TTY can't be resolved or
  tinting is off it is a silent no-op and the per-prompt hook tints as before.

## [1.11.0] — 2026-06-23

### Added
- **`--tint [on|off]` (default on).** A persisted config flag for the full-palette
  terminal tint, so tinting can be controlled without an env var. The
  `TASK_STATION_TINT` env var still wins over the config setting (on/off/1/0/
  true/false). Every Python tint emitter now consults this flag.
- **`--reset` factory-reset action (confirm-gated).** Bare `task-station config
  --reset` explains what it will do and resets nothing; `--reset confirm` wipes the
  board-managed settings in `config.json` back to defaults. **Tasks are never
  touched** (`tasks.db` is a separate file), and externally-installed integrations
  (bare command files, the Desktop bridge entry, the `CLAUDE.md` delegation block)
  are *reported* with their off-commands rather than silently removed.

### Changed
- **`task-station config` board redesigned.** One stanza per setting — an aligned
  `<flag> <value> <options>` line, then the description on its own line with the
  factory default shown inline as `(default: X)` — replacing the old wrapped-text
  blob. The former separate `status`, `--workspace-dirs`, and `--data-dir` blocks
  are folded into the single list; `--tint-theme` now shows just the appearance
  mode (`auto`/`dark`/`light`), not the resolved theme name; the `category
  overrides` row is relabelled `--category-overrides`. No more `* = default`
  markers — the value column always shows the current value.

## [1.10.0] — 2026-06-23

### Added
- **`--guaranteed-tracking` (opt-in, default off).** Hook-side deterministic
  create+attach of a *provisional* task on a fresh, unattached, non-skipped
  session — the `UserPromptSubmit` hook tracks the topic itself instead of only
  nudging the model. **Fold-don't-fork**: a similar open task is attached to (with
  the prompt filed as a note) rather than forked into a sibling. **Auto-GC**: a
  provisional task that's never engaged is deleted when the session is skipped or
  closed, so pure Q&A leaves no litter. Engagement (update title/summary/colour,
  file edit, folded note) sheds the provisional flag. Default off → the
  conservative install behaves exactly as the firmer nudge.

### Changed
- **`--policy` renamed to `--strict-delegation`** (hidden `--policy` alias kept for
  back-compat; the managed `CLAUDE.md` block markers are unchanged so blocks
  installed under the old name remain detectable/removable). Config board and
  README clarify available-vs-enforced delegation.
- **Firmer untracked-session nudge.** The default nudge now directs tracking even
  for plain questions and no longer advertises `skip` as an easy out; the
  escalation block still offers `skip` for genuinely throwaway sessions.

### Fixed
- **Intent detector no longer false-positives "create" on meta-questions about
  tasks.** Added past-tense/perfect/existential interrogatives (`did`, `have you`,
  `has`, `is there`, `was`, `were`, `didn't`, …) to the question guard, so
  "did you open a new task for this?" is correctly read as a question, not a
  create imperative.

## [1.9.1] — 2026-06-22

### Fixed
- **`/todo <n> -s` no longer re-tints the invoking window.** A session-jump opens
  the task in a NEW window and must leave the window you typed it in untouched.
  `_jump_one` was attaching (`set_link`) the **invoking** session to the jumped
  task; combined with the 1.9.0 prompt-tint fallback (which repaints the current
  window to its attached task's colour on any non-skill prompt), the invoking
  window wrongly repainted to the jumped task's colour. Now `-s` attaches only the
  **target** session — the resumed recorded session or the freshly-minted one — so
  only the new window carries the jumped task's tint. Belt-and-suspenders:
  `cmd_prompt_tint` also skips the attached-task fallback for a `/todo … -s` /
  `--session` prompt, so even the immediate jump prompt never repaints the current
  window. Plain `/todo <n>` (non-jump) still attaches the invoking session and
  repaints the current window, unchanged.

### Documentation
- **Comprehensive README refresh.** Re-audited every section against the shipped
  code — the appearance-aware theme system, auto-enabling categories, the full
  config flag table, the `/todo` board render, and the MCP bridge tools. Corrected
  the version badge (now `1.9.1`), the `--theme` default (`sands`), and normalised
  the open-status glyph to `○` for consistency with the board.

## [1.9.0] — 2026-06-22

### Added
- **Appearance-aware theme system.** The 12-category taxonomy (dot/[TAG]/label) is
  unchanged; colour now comes from a **THEME**, and every theme has **two variants —
  `dark` and `light`** — each a full per-category palette (background, foreground,
  bold, cursor, selection + the 16 ANSI colours). One theme ships, **`sands`**, with a
  **Dark Sands** (muted) variant and a **Light Sands** (vibrant) variant. The **OS
  appearance picks the variant**, so out of the box the terminal follows the OS — dark
  mode → Dark Sands, light mode → Light Sands — re-resolved every prompt/attach.
  Variants display as `{Dark|Light} {Theme}`. Tinting uses standard OSC escapes (OSC
  11/10/12, OSC 4 for the 16 ANSI slots, OSC 17 for selection, plus an iTerm-only
  `SetColors=bold`).
- **`config --tint-theme auto|dark|light`** (default `auto`) — the appearance control:
  which variant renders. `auto` detects the OS (macOS `AppleInterfaceStyle`;
  non-macOS/failure → dark); `dark`/`light` force it.
- **`config --theme`** — verb-first grammar for the active theme (mainly for custom
  themes, since one ships):
  - `config --theme` (or `list`) lists themes + active + each theme's variant labels +
    the current tint-theme and resolved variant.
  - `config --theme <name>` selects a theme.
  - `config --theme save <name>` snapshots **both variants** (dark + light) of the
    active theme into `config.json` as a fully self-contained theme (independent of the
    current appearance); rejects reserved names `save·edit·preview·list·show·default`
    and names not matching `^[a-z0-9][a-z0-9_-]*$`.
  - `config --theme edit` prints the `config.json` path.
  - `config --theme preview` renders a self-contained HTML gallery — **both variants**
    of every theme — to `<data_dir>/themes-preview.html`.
  - `--theme`, `--tint-theme`, and the resolved variant (e.g. `auto → Dark Sands`) all
    appear on the `config` board.
- **User themes survive updates.** `config.json` `themes` deep-merge over the shipped
  THEMES, **variant-nested** (theme → `dark`|`light` → category → field); brand-new
  named themes are allowed (a missing variant falls back to `sands`) — so
  customisations persist across `/plugin update`.
- **`tools/render_palettes.py`** — the data-driven preview generator (HTML to stdout
  or `--out`), rendering both variants of each theme; backs `config --theme preview`.

### Changed
- **In-session re-tint.** When a prompt invokes no skill, `prompt-tint` now falls
  back to the **attached task's** category colour (like the on-attach tint), so a
  plain `/todo <n>` repaints the current window to the active task's tint. Honours
  `TASK_STATION_TINT=off` and `TINT_TERMINAL`.

## [1.8.0] — 2026-06-21

### Added
- **Auto-enable categories — the board grows itself.** The categoriser now always
  considers the **full 12-slot taxonomy**, so it can pick the most accurate
  category even if that slot isn't on the board. When `auto_categories` is on (the
  default) and a task is assigned (via `create --color`, `attach --color`,
  `update --color`, or the Desktop bridge's create tool) to a category not in the
  enabled set, that slot is **enabled automatically** — persisted to
  `enabled_categories` and surfaced with a one-line `enabled new category 🔵 [INFRA]`
  notice. The enabled set governs **display only**; assignment can target any slot.
- **`--auto-categories on|off`** (plus `--auto-categories-get`) and the env escape
  **`TASK_STATION_AUTO_CATEGORIES=off`** to freeze the enabled set. With it off,
  assignment no longer grows the board and the legend/picker restrict to enabled
  slots (the prior behaviour). Shown as a row on the `config` board.

### Changed
- **Lean CORE default.** When `enabled_categories` is unconfigured, the enabled set
  is now **CORE = 🔴 BUG · 🟢 FEATURE · ⚫ GENERAL** (was: all 12). A fresh board
  starts small and fills in via auto-enable as you categorise. `⚫ GENERAL` stays
  permanent. The `config` board summary reads `N/12 (default: CORE)` / `N/12
  (custom)`.

### Removed
- **Category presets are gone.** The `PRESETS` map, `preset_keys()`, the
  `config --categories preset <name>` subcommand (and its `minimal|web|data|ops|full`
  argument), and the preset listing on the `--categories` board were removed in
  favour of the lean default + auto-enable. `--categories` (show set), `--enable`,
  and `--disable` are unchanged.

## [1.7.0] — 2026-06-21

### Added
- **Full-palette escape tint — every category now tints the WHOLE terminal, not
  just the background.** Each of the 12 category slots bakes in a complete
  **Sands** palette (background, foreground, bold, cursor, selection, and all 16
  ANSI colors), shipped as the new defaults. `categories.tint_escape` emits it as
  standard OSC escapes — background (OSC 11), foreground (OSC 10), cursor
  (OSC 12), the 16 ANSI colors (OSC 4), selection (OSC 17) — plus one iTerm-only
  extra for the bold colour (`1337;SetColors=bold`). iTerm2 and Terminal.app both
  honor it; still zero-setup, no profiles or shell aliases. A category that
  defines only a background still emits just that (back-compat for minimal
  taxonomies), and a user override that sets only `{tag,label}` inherits the full
  palette from its slot.
- **Tint on attach/resume, not just first prompt.** The SessionStart hook now
  emits the tint escape for the attached task's category (new `session-tint`
  command), so a resumed/attached window tints immediately.
- **Width-aware, wrap-safe `task-station config` board (release prep).** The
  no-arg board is now a single unified view: short-valued settings render as a
  4-column aligned grid (SETTING / VALUE / OPTIONS / WHAT IT DOES) whose first
  three columns are sized to their widest cell per render, while the description
  column takes the remaining terminal width and wraps with a hanging indent under
  WHAT IT DOES — so long descriptions never break the grid. Long PATH-valued
  settings (`--workspace-dirs`, `--data-dir`) print as their own full-width
  two-line blocks below the grid, and the store path drops to its own line when
  it would overflow. Alignment holds at COLUMNS=60/80/120.
- **`term.width()`** — terminal columns via `shutil.get_terminal_size()` (honors
  `$COLUMNS`, falls back to 80, clamped to a minimum of 60). Pure stdlib.

### Changed
- **Category taxonomy rebalance (slots/keys/palettes unchanged).** Five category
  slots were renamed/clarified for everyday work — only `tag`/`label` (and one
  dot) changed; colour keys, hexes and palettes are untouched, so existing tasks
  and `config.json` overrides keep working: `purple` SPECIAL → **RESEARCH**
  ("spikes / investigation"); `gold` GOLD/reserved → **DOCS** 📖
  ("documentation, writing") — gold is now a real category, no longer a hidden
  "reserved" slot; `blue` DEVOPS → **INFRA** ("CI/CD, pipelines, cloud, deploy");
  `brown` DATABASE → **DATA** ("databases, schemas, ETL, migrations"); `silver`
  AI CONFIG → **TOOLING** ("dev/AI tooling, config, env"). Presets and the
  enabled-set default are unchanged (presets key on colour, not tag). The
  legend/picker no longer special-case a "reserved" label.
- **One config board, no duplication.** The separate `setup.status()` block
  printed after the no-arg board is gone; its facts (tint + terminal, policy,
  desktop-bridge) are folded into a compact `status` section at the bottom of the
  same board, keeping the actionable hints (`--policy on`, …). The tint line now
  reads `escape (full palette) · terminal <iterm|terminal|none>`. `setup.status()`
  itself is unchanged and still used by the install flow.

### Removed (breaking)
- **Profile-switching tint mode is gone.** `task-station config --tint-profiles`,
  the `tint_mode == "profile"` path, `setup.install_tint_profiles()`,
  `categories.tint_command()`, the bundled `lib/install-tint-profiles.sh`, and the
  `zsh -ic '<color>'` alias hints (resume-command prefix, task-detail line,
  prompt-context/guidance) are all removed. Tinting is now always the direct
  full-palette escape. No `~/.zshrc` aliases or Terminal.app profiles are written
  or referenced anymore. If you previously ran `--tint-profiles`, the generated
  aliases are now inert and can be deleted by hand.

## [1.6.4] — 2026-06-20

### Changed
- **Accurate Claude Desktop docs — plugin commands + connector tools, on-demand
  only.** The README now states the confirmed reality: Task Station works in
  Desktop two ways — as a **plugin** (slash commands like `/todo` in Chat) and
  as a **connector** (`config --desktop-bridge on` → conversational
  create/list/track tools + the `todo` prompt + task resources). Desktop runs
  plugin *commands* but **not** *hooks*, so Desktop tracking is **on-demand**
  (type `/todo` or say "track this"), **not** automatic; added a
  surface×capability matrix and noted Desktop Custom Instructions as the only
  proactive lever.

### Removed
- **The inert `initialize` `instructions` field (added in 1.6.3).** Claude
  Desktop silently drops MCP server `instructions`, so the 1.6.3 auto-track
  nudge never reached the model. Removed it; `capabilities` / `serverInfo` /
  `protocolVersion` and all tools/prompts/resources are unchanged.

## [1.6.3] — 2026-06-20

### Added
- **Claude Desktop now auto-tracks substantive topics as tasks — the Desktop
  analog of the CLI's prompt-context auto-track.** Desktop has no
  `UserPromptSubmit` hook, so the CLI's "track every substantive topic as an
  open(◦) task" can't fire there. The MCP `initialize` response now carries an
  **`instructions`** string (which clients fold into the model's context)
  telling Desktop's Claude: when the user raises substantive work, first
  `list_tasks` and `add_note` onto a matching open task (fold — don't
  duplicate), else `create_task` with a clear title, 1–3 sentence summary,
  category, and a `source` identifying the Desktop conversation; skip trivial
  one-offs and casual chat. It's a model-driven nudge, not a hard hook, and
  only fires on substantive work — so the board doesn't flood. Tools, prompts,
  and resources are unchanged.

## [1.6.2] — 2026-06-20

### Changed
- **In Claude Desktop Chat, the task board now renders verbatim as a table.**
  Previously, when `list_tasks` / the `todo` prompt returned the Markdown board,
  Chat paraphrased it into prose (nothing told it otherwise — unlike the CLI
  `/todo` skill, which says "print verbatim"). The `list_tasks` tool result and
  the `todo` prompt content now **prepend a short instruction line** — `Display
  this task board to the user EXACTLY as written below … render the tables
  verbatim, do not summarize, reword, or re-rank.` — ahead of the board, so Chat
  shows the same ◦/● tables as the CLI. The board BODY is unchanged: still
  byte-equal to the CLI `render --format md`.

### Added
- **The `todo` prompt is discoverable in Desktop's prompt picker.** `prompts/list`
  now gives `todo` a human title ("Task Station: todo") and description ("Show
  your task-station board (open · active · closed)"). Each tool also carries a
  crisp, action-leading description (`list_tasks` "Show the user's task board",
  `create_task` "Create / track a new open(◦) task", …) so Claude picks the right
  one from natural language.

## [1.6.1] — 2026-06-20

### Changed
- **The Desktop bridge now points Claude Desktop at a stable, self-resolving
  launcher instead of the volatile engine symlink.** Previously `--desktop-bridge
  on` wired Desktop to `~/.claude/task-station-engine/mcp_server.py`, but that
  symlink is re-pointed by *every* CLI session to *that session's* plugin version
  — so an older session (e.g. a 1.2.2 version with no `mcp_server.py`) could
  silently break Desktop. `on` now generates `<data_dir>/mcp-launcher.py` (a
  stable, version-independent path) and points Desktop at `python3
  <data_dir>/mcp-launcher.py`. At run time the launcher resolves the **installed**
  task-station version itself — reading `plugins/installed_plugins.json` →
  `task-station@ryanconmeo` `installPath` → `<installPath>/lib/mcp_server.py`,
  falling back to the **highest** `plugins/cache/ryanconmeo/task-station/*/lib/mcp_server.py`
  that exists — and `os.execv`s it with the same interpreter, passing stdio
  straight through. Robust across `/plugin update` and concurrent CLI sessions.
  The launcher is stdlib-only (system `python3` 3.9+) and is regenerated on every
  `on` (idempotent); `off` removes only our config entry and leaves the (inert)
  launcher file in place.

### Added
- **`TASK_STATION_DESKTOP_CONFIG` override.** When set, the `--desktop-bridge` CLI
  path resolves the Desktop config from that path instead of the real
  `~/Library/Application Support/Claude/claude_desktop_config.json` — so tests and
  safe manual checks never touch the live Desktop config.

## [1.6.0] — 2026-06-20

### Changed
- **The Desktop bridge is now DEPENDENCY-FREE and self-installing.** `lib/mcp_server.py`
  no longer needs the `mcp`/FastMCP SDK (which required Python 3.10+ and a
  `pip install`). The MCP protocol is hand-rolled in the standard library
  (`json` + `sys` only): a minimal **stdio JSON-RPC 2.0 server** that runs on the
  **system `python3` (3.9+)** with zero install. It handles `initialize`
  (advertising `tools`/`prompts`/`resources` + `protocolVersion` + `serverInfo`),
  `notifications/initialized`, `ping`, `tools/list`, `tools/call`, `prompts/list`
  + `prompts/get` (the `todo` board), and `resources/list` + `resources/read`
  (`task://<seq>` → full detail); unknown methods return JSON-RPC `-32601` and a
  malformed line never crashes the loop. The five stdlib logic fns
  (`_list_tasks`/`_create_task`/`_get_task`/`_set_status`/`_add_note`) are reused
  verbatim — only the FastMCP transport was replaced. There is **no `mcp` import
  anywhere** in the codebase.

### Added
- **`task-station config --desktop-bridge on|off`** — a self-installer that wires
  the bridge into Claude Desktop with no manual JSON. `on` locates (or creates)
  `~/Library/Application Support/Claude/claude_desktop_config.json`, backs it up
  (`.bak-desktop-bridge`), and **merges** a `task-station` server entry
  (`command: python3`, `args: [<~/.claude/task-station-engine/mcp_server.py>]`)
  without clobbering other servers — idempotent, then prompts to restart Desktop.
  `off` removes only our entry. The no-arg `config` view shows the bridge status
  (installed? path?).

## [1.5.0] — 2026-06-20

### Added
- **Desktop bridge — an MCP server over the SHARED store.** Claude Desktop (and
  any MCP client) can now create / read / update tasks in the *same* local
  `tasks.db` the CLI uses — one store, two front doors. New `lib/mcp_server.py`
  drives the existing engine (`paths.py` + `store.py` + `task-station.py`), so
  store paths, seq numbering, lifecycle rules, and the `--format md` render are
  reused verbatim — no forked logic. WAL is already on, so concurrent Desktop +
  CLI access is safe.
  - **Tools:** `list_tasks` (the Markdown board, byte-for-byte the CLI render),
    `create_task` (makes an `open (◦)` task; `category`/`effort`/`source`),
    `get_task` (full detail incl. the source link), `set_status`
    (open → active → closed), `add_note` (timestamped activity-log entry).
  - **Prompt:** `todo` — the rendered board, the Desktop analog of `/todo`.
  - **Resources:** each task at `task://<seq>` returns its full detail, so a task
    can be attached to a Desktop conversation via the + menu.
  - **Source-conversation link.** `create_task(..., source=…)` records the
    originating Desktop conversation ref/URL on the task; `get_task` surfaces it
    — the Desktop ↔ Code provenance link.
  - **`mcp` is an OPTIONAL, server-only dependency.** The tool logic is plain
    stdlib; the FastMCP wrapper is lazily imported only inside `main()`. The core
    plugin and the whole test suite stay stdlib-only — you only need
    `pip install mcp` to *run* the bridge. Wire it up via the stable
    `~/.claude/task-station-engine/mcp_server.py` symlink (survives
    `/plugin update`) — see the README "Desktop bridge (MCP)" section.

## [1.4.0] — 2026-06-20

### Added
- **Three-state task `status` — `open (◦)` → `active (●)` → `closed`.** The lifecycle
  is now ONE field: a topic you merely raise starts `open` and shows on the board
  immediately as `◦`; it graduates to `active` (`●`) when work actually starts; `/done`
  closes it. A leading single-width glyph renders at the very front of every not-closed
  `/todo` row — ASCII list, Markdown table (`#` cell), and the detail view — distinct
  from the category emoji, with a `◦ open · ● active` legend. Closed tasks keep their
  own section and mute the glyph. `sorted_tasks` lists not-closed (open + active) first
  by recent activity, then closed.
- **Auto-promote `open → active` when work begins** (idempotent; never resurrects a
  closed task), on any of:
  - `delegate … --worktree` for the task (write work starts);
  - a **file edit** in an attached session — `hooks/on_post_tool.sh` (PostToolUse)
    flips an attached open task to active;
  - manual **`status --task <ref> [open|active]`** (no value → report the status;
    closing is via `/done`);
  - **`create --active`** to start a task active.
- **Auto-track as `open` from the first prompt** — replaces the old "pure Q&A → stay
  silent" behaviour. For an unattached, non-skipped session the model now creates an
  `open` task for the topic (model-driven: good title + category). Skipped sessions
  still stay silent.
- **Grouping — "fold, don't fork".** Before creating a new task the model scans the
  board (open + active) and, if the prompt continues an existing task, **attaches and
  appends the prompt as a note** instead of spawning a sibling — so related questions
  across sessions accumulate under one task. New **`attach --note '<text>'`** appends a
  timestamped entry to the task's activity log.

### Changed
- **`status` is a single three-value field** (`open`/`active`/`closed`); everywhere the
  code treated `status == "open"` as "on the board / not done" now means "not closed"
  (`open` or `active`). `/done` closes from open or active; reopening a closed task
  returns it to `open`. Back-compat: pre-existing `open`/`closed` tasks read unchanged
  (a missing status reads as `open`); no data migration.
- `cmd_prompt_context` / `commands/todo.md` / `guidance` guidance rewritten around
  track-as-open + fold-don't-fork (was: attach only on concrete work, else silent).

## [1.3.0] — 2026-06-20

### Fixed
- **`-s` no longer resumes the wrong conversation.** When a task was spun off from a
  busy session — or you jumped into it from the very session it was created in — the
  most-recent-substantive heuristic could pick **the current conversation** as the
  resume target (the `cands = … or cands` fallback re-added the session that had just
  been excluded). The current session is now excluded **hard**: if no other live
  candidate remains, `-s` **fresh-starts** instead of tainting into the conversation
  you jumped from.
- **Skipped sessions are excluded from `-s` candidacy.** A session marked untracked
  (`skip`, link `__skip__`) is never offered as a resume target, even with a live
  transcript.

### Added
- **`-s` fresh-start auto-attaches.** When there's no valid session to resume, the
  jump path mints a brand-new session id, pre-binds it to the task (link + hub
  `session_meta`), and emits `cd <dir> && claude --session-id <uuid>` — so the new
  window **auto-attaches** to the task on launch (SessionStart sees the link) rather
  than opening a bare, untracked `claude`. `resume_command` stays **pure** for the
  `/todo <n>` display render (no uuid minted per render); the mint happens only in the
  jump / pin paths via `fresh_resume_command(task)`.
- **`create --no-attach`** — create a task with **empty `sessions[]`** and no
  session→task link (the clean "spin off a task for later" primitive). `/todo <n> -s`
  then fresh-starts a clean session. `--session` is now optional.
- **`create` from a substantive tracked session defaults to `--no-attach`.** Running
  `create` with a `--session` that is itself a real, tracked working conversation
  (≥ 3 messages) no longer binds that busy conversation as the new task's resume
  target — it defaults to no-attach and warns. Pass **`--attach`** to force the old
  bind-this-session behaviour.
- **`detach --session <s> [--task <t>]`** — remove a session from a task's
  `sessions[]`/`session_meta`, clear `pinned_session` if it pointed at it, and clear
  the session→task link. `--task` selects the task; without it, the session's linked
  task is used. Idempotent.
- **`pin --new [--task <t>]`** — pin an **unborn** session: mints a uuid, records it
  (and links it), and `/todo` emits `claude --session-id <uuid>` so opening it
  *becomes* the task's pinned session — bypassing the stale-pin "ignored when no live
  transcript" guard for this intentional case.

## [1.2.2] — 2026-06-20

### Added
- **Auto terminal title `#<seq>: <title>` on attach.** Once a session is attached to
  a task, the terminal tab/window title is set to `#<seq>: <title>` (e.g.
  `#214: task-station: token-efficiency + SQLite store`) — a literal `#`, no
  `task-station` prefix. A new `prompt-title` emitter (run by the `UserPromptSubmit`
  hook every prompt) writes the OSC title escape (`\033]0;…\007`) to the originating
  TTY via the same `origin-tty.sh` rail the tint uses, so the title sets on the first
  prompt after attaching and self-heals each prompt. Unattached / skipped sessions
  are left untouched.
- **`config --title on|off`** (default on) toggles the auto title, mirroring the tint's
  env escape — `TASK_STATION_TITLE=off` (or `config --title off`) suppresses it.

### Changed
- **SessionStart session name reformatted to `#<seq>: <title>`** (was
  `task-station-<seq> · <title>`), matching the new terminal title.

## [1.2.1] — 2026-06-20

### Changed
- **Swapped the `white` ↔ `silver` category slots.** 🎨 **DESIGN** now lives on the
  `white` slot (→ **White Sands** profile + white hex) and 🪩 **AI CONFIG** on the
  `silver` slot (→ **Silver Sands** profile + silver hex). Each slot keeps its own
  `key`/alias/profile and tint hex; only the dot/tag/label moved between them, so
  the two categories simply trade profiles — no `tint`-override field. `CORE` and
  the `web` preset were re-pointed (`white`↔`silver`) so AI CONFIG stays core and
  both AI CONFIG + DESIGN stay in `web`. The Claude-tooling `SKILL_COLORS` entry
  now maps to `silver`, so those skills keep tinting AI CONFIG (Silver Sands).
  - **Stored tasks are re-keyed `white`↔`silver` on upgrade** so they follow their
    category to the new slot (a live-data migration handled separately from this
    change, which ships only the new defaults, tests, and docs).

## [1.2.0] — 2026-06-20

### Changed
- **Redesigned shipped category defaults.** `yellow` tag `FIX PR` → **`FIX`**;
  `white` `SKILLS`/⚪ → **`AI CONFIG`/🪩** (disco ball), label "AI tooling & config";
  and the `pink`↔`silver` roles swapped so **`pink` = `PERSONAL`/🩷** and
  **`silver` = `DESIGN`/🎨** (palette). Tint hexes are kept *by slot* (pink keeps
  its pink tint, silver its neutral grey), so existing tints stay sensible.
  - **Stored tasks re-render with the new labels.** A task's stored `color` key is
    **unchanged** — only its *rendered* tag/label/emoji updates. So tasks coloured
    `pink` now show 🩷 `[PERSONAL]`, `silver` → 🎨 `[DESIGN]`, `white` → 🪩
    `[AI CONFIG]`, and `yellow` → 🟡 `[FIX]`. No data migration; nothing on disk
    changes.

### Added
- **Slot-determines-emoji.** The dot is now *canonical per colour slot* — you pick
  the colour, the colour determines the icon. A category override / new category
  needs only `tag` + `label`; the `dot` (and tint hexes) are inherited from the
  slot automatically. An explicit `dot` is still honoured for power users.
- **Seeded-but-removable enabled set.** A new `enabled_categories` config key
  controls which slots are "on" — the legend, auto-classification nudge, and picker
  consider only enabled categories. Unconfigured ⇒ the full set (back-compat).
  **⚫ GENERAL is permanent** — always enabled, cannot be disabled.
- **Category presets.** `config --categories preset <minimal|web|data|ops|full>`
  applies a named enabled-set. Every preset contains the universal core
  (`BUG`, `AI CONFIG`, `PERSONAL`, `GENERAL`). `config --categories` (no arg) shows
  the current enabled set + available presets. `config --enable <key>` /
  `--disable <key>` toggle individual slots (disabling `GENERAL` is refused).

## [1.1.0] — 2026-06-19

### Changed
- **Repo enrichment is now OPT-IN per repo (behavior change).** Previously
  `repos --refresh` sent each repo's README + file tree to a model (Haiku) by
  default. Now a repo's content reaches the model **only** when its manifest
  `enrich` flag is `true`, and that flag **defaults to `false`** for every repo.
  A normal `repos --refresh` therefore sends **nothing** off-machine — it fills
  `summary` deterministically (README first paragraph) plus the existing
  stack/status/path detection. `--no-llm` still forces the deterministic path even
  for `enrich:true` repos.
- **Deterministic refreshes preserve existing summaries.** A deterministic refresh
  no longer overwrites a non-empty `summary`/`keywords` (model- or override-derived)
  with the README paragraph; it only fills repos that lack one. Force regeneration
  with the new `--re-summarize`.

### Added
- **Auto-maintained include/exclude manifest** at `task-station-data/repos.config.json`,
  a map keyed by repo name of `{ index: bool=true, enrich: bool=false }`.
  `repos --refresh` reconciles it: newly-discovered repos are added with safe
  defaults; vanished repos are pruned. It is the single surface where every
  discovered repo name appears, so you never type a name from memory — just flip
  flags. Only `index:true` repos reach `repos.md`/`repos.json`; only `enrich:true`
  repos are eligible for model egress.
- **Toggle commands (no JSON editing):** `repos include <name>` / `repos exclude <name>`
  set `index`; `repos enrich <name> [on|off]` sets `enrich`; `repos config` prints the
  full manifest. Names or paths are accepted; unknown names get a clear message.
- **`.task-station-ignore` marker file** at a repo root fully excludes that repo from
  discovery/index (as if `index:false`), regardless of the manifest — a repo-owner
  self-exclude that travels with the repo.
- **First-run onboarding on `/repos`** (not `/todo`): `repos --detect-roots` proposes
  candidate roots (`~/Workspace`, `~/Workspace-Other`, plus any `~` dir with ≥2 git
  repos); `commands/repos.md` walks you through confirming and persisting them with
  `repos --set-roots <p1,p2,...>`, reassuring that enrichment is off by default.
  `commands/todo.md` gains a single subtle one-line pointer to `/repos`.
- **Egress transparency + hygiene:** `repos --refresh` prints exactly which repos are
  having content sent (`enriching (sending README+tree NAMES): …`); `--dry-run` reports
  what *would* be sent without sending. The enrichment input is bounded to repo name,
  ado_project, stack, README top (~80 lines), and a `git ls-files` **name** sketch —
  arbitrary file **contents** are never read, and a denylist guard keeps secret-bearing
  names (`.env`, `*.pem`, `*.key`, `secrets*`, `credentials*`, `.npmrc`, …) out of the
  prompt entirely.

## [1.0.11] — 2026-06-19

### Fixed
- **Exclude prose/markup-ambiguous extensions from stack detection.** The
  Linguist-derived `lib/stack_map.py` kept only `type: programming`, so `.md`
  (claimed by Markdown=prose AND GCC Machine Description=programming) mapped to
  `gcc-machine-description` — polluting every repo with a `README.md`. The
  generator now parses ALL languages with their `type` and drops any extension
  a prose/markup/data language also claims (`.md`/`.rst`/`.txt`/`.json`/`.yaml`/
  `.xml`/…), UNLESS a curated programming language owns it (so `.ts`/`.tsx`/`.rs`,
  which XML lists incidentally, survive). Remaining programming-only collisions
  resolve via a small tie-break dict (`.h`→`c`, `.m`→`objective-c`).
- **Collapse the TSX/JSX variants** onto the ergonomic labels via the alias
  overlay (`TSX`→`typescript`, `JSX`→`node`) so `tsx` no longer appears alongside
  `typescript`. Correct niche detections are kept (e.g. `.com`→
  `digital-command-language`). `EXT_TO_STACK` drops from 954 to 905 extensions;
  the generator stays deterministic and stdlib-only.

## [1.0.10] — 2026-06-19

### Changed
- **Stack detection is now GitHub-Linguist-derived.** The repo index's hand-rolled
  ~18-entry extension→stack list is replaced by `lib/stack_map.py`, a generated map
  (`EXT_TO_STACK` + `FILENAME_TO_STACK`, ~950 extensions) distilled from GitHub
  Linguist's `languages.yml` — the data behind GitHub's per-repo language bar.
  Coverage jumps from a handful of stacks to the full programming-language long tail
  (Swift, Kotlin, Ruby, PHP, Scala, …) while the ergonomic labels the tool already
  uses are preserved via an alias overlay (`python`/`node`/`dotnet`/`sql`/`typescript`/
  `go`/`rust`/`terraform`/`docker`). Swift repos (`.swift`) are now detected.
  - The combination logic is unchanged — the `git ls-files` histogram + threshold,
    the flyway / github-actions / terraform config signals, and root manifests all
    still apply; only the extension lookup got vastly wider.
  - `lib/stack_map.py` is committed and pure stdlib (plain dict literals, no runtime
    YAML, no imports). Regenerate with `python3 tools/gen_stack_map.py`. The source
    `languages.yml` is vendored locally but gitignored (MIT-licensed, not committed).

## [1.0.9] — 2026-06-19

### Added
- **Repo index for hub task routing** — a hub `claude` session launched from `~`
  can't auto-load anything inside a repo, so `/repos` gives it an on-demand, hub-side
  map of the repos under your workspace roots to route a fuzzy task to the right
  repo(s) at delegation time. `/repos` / `/repos show` print the index, `/repos
  <term>` ranks repos by token overlap (name/keywords/domain/stack/ado_project/path),
  `/repos --refresh [--force] [--quiet]` rescans, and `/repos --json` emits the
  structured list. Backed by new `lib/repo_index.py`.
  - Repo cards are **fully auto-filled** — no manual overrides needed (overrides remain an
    optional escape hatch).
  - Deterministic discovery (no model): per repo it derives name, abs path, `origin`
    remote, `ado_project` (Azure DevOps `…/_git/` project or GitHub `owner/repo`), and
    `status` (`active`/`stale`/`unknown` from the last commit vs `REPO_STALE_MONTHS`,
    default 6).
  - **Stack detected by CONTENT, not just root manifests**: a `git ls-files` extension
    histogram (`.py`→python, `.cs`→dotnet, `.sql`→sql, `.ts`→typescript, `.go`→go,
    `.tf`→terraform, …, kept above a small threshold or if dominant) **unioned** with
    config/tooling signals (`Dockerfile`→docker, `.github/workflows/`→github-actions,
    flyway config / `*__*.sql` migrations→flyway, `*.tf`→terraform) and the root manifests.
    SQL/Flyway and manifest-less repos now get a real stack (e.g. OtherProjLandingZone→`sql,
    flyway`; a `lib/`-only repo→`python,shell`).
  - **`summary` + `keywords` are auto-filled by a fingerprint-gated, best-effort model call**
    that **degrades gracefully**. Each repo carries a `fingerprint` =
    `sha1(remote + sorted top-level entries + sha1(README) + sha1(each root manifest))[:12]`
    that moves only on identity/structure change, not on ordinary commits. On `--refresh`
    the model (cheap Haiku via the headless `claude -p … --output-format json` CLI) is
    invoked **only** for a repo that is new or whose fingerprint changed **and** has no
    override summary; results are cached in `<data_dir>/.repos-cache.json`, so steady-state
    refreshes make **zero** model calls. If the call fails for any reason (CLI absent, no
    network, timeout, bad JSON), it falls back to a **deterministic** README-derived summary
    + keywords — the index always builds and never raises out of the command.
  - **`/repos --refresh --no-llm`** (and the `repo_enrich` config toggle / `TASK_STATION_REPO_ENRICH=off`,
    default ON) forces the deterministic-only path.
  - **Precedence for summary/keywords: override > model > deterministic-fallback.**
    Hand-authored prose (`summary`/`keywords`/`domain`, plus a `status` override) lives in
    `<data_dir>/repos.overrides.json` keyed by repo name — overrides **win** and **survive**
    every regeneration; discovery never writes them.
  - The index lives next to the task store at `<data_dir>/repos.{md,json}` (+ the
    `.repos-cache.json` enrichment cache) — **not** in `tasks.db` (repos aren't tasks) and
    **not** as per-repo committed files. Discovery roots come from `--workspace-dirs` /
    `TASK_STATION_WORKSPACE_DIRS`, defaulting to `~/Workspace` + `~/Workspace-Other`.
  - The `delegating-work` skill gains a "resolve the target repo" step that uses the
    index when the target repo is ambiguous — on-demand only, no SessionStart injection.
  - Forward-compatible for scale: `match()` already doubles as a stage-1 top-K pre-filter,
    and the fingerprint cache already avoids redundant model work (a future `--refresh`
    debounce is the only remaining additive piece).

## [1.0.8] — 2026-06-19

### Changed
- Storage is now a single indexed SQLite database (`store/tasks.db`, WAL mode) instead
  of one JSON file per task plus per-session link files. Listing, counting, and the
  per-prompt tracked-session check are indexed queries, so they stay fast as the board
  grows rather than scanning every task file on each hook invocation. Falls back to the
  JSON-file store if `sqlite3` is unavailable (stdlib, so effectively never).
- A fresh install starts directly on SQLite — there is no migration step baked into the
  plugin (new users have nothing to migrate).

## [1.0.7] — 2026-06-19

### Added
- `render --format md` emits the `/todo` list as GitHub-flavored Markdown tables
  (Open then Closed) directly, so the skill prints them verbatim instead of
  hand-transcribing the ASCII block (table cells are `|`/newline-escaped).
- Live attached-session marker: tasks with more than one currently-attached session
  show a ` ⧉N` count (sessions whose link still resolves to the task) in both ASCII
  and Markdown list output.

### Changed
- The per-message unattached-session nudge is collapsed: the full block (open-task
  list, attach/create syntax, colour legend) prints only on the first miss and at
  escalation; intermediate misses get a single compact line — a large recurring
  token saving. Per-prompt category detection is preserved in the compact line.
- `update`, `pin`, and `unpin` accept comma-separated task lists, mirroring `done`'s
  batch contract (one result line per ref; a bad ref is reported but doesn't abort).
- Skill docs: after a close/mutation, confirm with the result line(s) only — don't
  re-render the full `/todo` list unless asked.

## [1.0.6] — 2026-06-19

### Added
- prompt-context now detects explicit create/attach-a-task phrasing and hard-steers
  to task-station over the native TaskCreate tool. A new `task_intent()` detector in
  `categories.py` recognises imperatives like "make this a task" / "attach this to a
  task" (ignoring questions about the concept and negations); when one fires,
  `prompt-context` prints a hard directive — even in a skipped or already-attached
  session — telling Claude to use task-station's `create`/`attach` now and NOT the
  built-in/native (ephemeral session-todo) `TaskCreate` tool. `guidance` carries the
  same one-line warning.

## [1.0.5] — 2026-06-18

### Added
- OS-appearance-aware tinting: each category now ships a light **and** a dark
  palette, auto-detected on macOS (`defaults read -g AppleInterfaceStyle`). Use
  `config --tint-theme auto|dark|light` to override the auto-detection.

### Changed
- Darkened the white/neutral dark-mode tint (`#2b2b30` → `#202024`); it was too
  bright on dark backgrounds.
- README: documented that `/todo` output enters the session as context, giving Claude a
  cross-project big-picture view for large multi-domain work.

## [1.0.4] — 2026-06-18

### Added
- `/done` and `/todo … -s` accept comma-separated task numbers (multi-close /
  multi-jump): `/done 1,2,5` closes each task with one result line apiece, and
  `/todo 1,2,5 -s` attaches and opens a window per task. A bad ref in the list is
  reported but doesn't abort the others; a single number works as before.

### Fixed
- Bare `/todo`/`/done` now follow plugin updates without a restart: the engine
  symlink is re-pointed on every prompt (idempotent), not just at session start,
  so an in-session `/plugin update` no longer leaves them on stale code.

### Changed
- README reorganized — `/todo` table preview and a new **Key Features** section
  first, then a linked **Table of Contents**, with **Install** and a dedicated
  **Commands** section moved up.

## [1.0.3] — 2026-06-18

### Changed
- The `/todo` block now prints an authoritative `Commands:` footer (single source
  of truth) listing every command, and the command reminder is relayed from it
  rather than hardcoded in the command instructions.

## [1.0.2] — 2026-06-18

### Added
- Opt-in `/todo` update check (default **off**). Enable with
  `task-station config --update-check on`: the `/todo` list view shows a one-line
  footer when a newer Task Station version is published. When off there are zero
  network calls; when on it makes at most one `git ls-remote` version check to
  GitHub per day (cached locally under `task-station-data/update-check.json`),
  with a hard timeout. Offline or any failure is silent, and no task data is ever
  sent.
- The `/todo` list now also surfaces `/todo <n> -s` (jump to a task's pinned
  session) and `/task-station:config` in its command reminder, matching the README.

## [1.0.1] — 2026-06-18

### Added
- `/todo closed [N]` and `/todo all` listing modes. `/todo closed` shows the 20
  most recent closed tasks, `/todo closed N` shows N, and `/todo all` shows every
  task. The bare `/todo` list still shows only the most recent few closed; the
  "older closed hidden" footer now points at these commands.

### Changed
- Collapsed `/task-station:setup` into `/task-station:config` — `config` now owns
  `--policy` and `--tint-profiles` and shows a status view with no args; the
  `setup` command is removed.
- Default `brown` category is now `[DATABASE]` ("database"); data-migration tasks
  still auto-classify there.
- The "fixing PR review feedback" category moved from gold to **yellow**
  (`[FIX PR]`); gold is now a reserved slot.

### Fixed
- `/done` now closes **iTerm2** windows, not just Terminal.app.
- Command bodies fall back to `CLAUDE_CODE_SESSION_ID` when `CLAUDE_SESSION_ID` is
  unset.

## [1.0.0] — 2026-06-17

Initial public release as Task Station.

### Added
- `/todo` and `/done` slash commands (list, open+resume, close), plus the
  namespaced `/task-station:todo` / `:done` and `/task-station:config` / `:setup`.
- Persistent, cross-session task tracking with one JSON file per task under
  `${CLAUDE_CONFIG_DIR:-~/.claude}/todo-data/`. All state is local.
- Auto-attach nudging + an optional enforcement gate (PostToolUse + Stop hooks)
  that keeps real work from going untracked.
- Category colours with per-category terminal tinting: zero-setup **auto** mode
  (iTerm2 `SetColors` / Terminal.app OSC 11) or **profile** mode (named profiles).
  Tinting targets the originating window, focus-independently.
- `todo config` (settings) and `todo setup` (doctor + installers): a 100%-reversible
  delegation-policy block for your `CLAUDE.md`, and a Terminal.app tint-profile helper.
- In-project worker delegation (`lib/delegate/`) + a `delegating-work` skill.
- Opt-in bare `/todo` + `/done` aliases (`todo config --bare-cmds on`).
- Session pinning (`todo.py pin`/`unpin`) to re-pin a task to a fresh session and
  save tokens when a context window grows stale.
