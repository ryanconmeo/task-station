# Task Station — Architecture

The internals the [README](../README.md) defers here. Task Station is **stdlib-only**
Python (3.9+) plus a few POSIX `bash` hooks — no third-party dependencies, no build
step. The engine (`lib/task-station.py`) is the CLI every command and hook calls;
`categories.py` is an optional plugin it imports defensively (delete it and the tracker
degrades to a plain, colourless board).

| Area | Files |
|---|---|
| Engine + render/resume/dedup | `lib/task-station.py` |
| CLI entrypoint | `bin/task-station` |
| Storage backends | `lib/store.py`, `lib/paths.py` |
| Categories + tint | `lib/categories.py`, `lib/term.py`, `lib/origin-tty.sh` |
| Hooks | `hooks/hooks.json`, `hooks/on_*.sh` |
| Delegation | `lib/delegate/{delegate.py,worktree-up.sh,POLICY-TEMPLATE.md}` |
| Repo index | `lib/repo_index.py`, `lib/stack_map.py` (generated) |
| Desktop bridge | `lib/mcp_server.py`, `lib/mcp_launcher.py`, `lib/setup.py` |
| Tests + CI | `tests/`, `.github/workflows/ci.yml` |

**`bin/task-station`** is a thin `bash` wrapper: it resolves its own real path
through symlinks (no `readlink -f` — absent on stock macOS), derives the plugin
root, and `exec`s `python3 <root>/lib/task-station.py "$@"`. Claude Code adds an
enabled plugin's `bin/` to the Bash tool's PATH, so guidance/help text prefers the
short `task-station <command>` form over the long, version-pinned
`python3 <plugin-cache>/lib/task-station.py <command>` invocation (kept as a
parenthetical fallback for shells without `bin/` on PATH). The hooks still call the
absolute path directly — they run with `$CLAUDE_PLUGIN_ROOT` set and must not
depend on PATH.

**CI** (`.github/workflows/ci.yml`) runs on every push and PR: the `unittest`/`pytest`
suite across ubuntu + macOS × Python 3.9/3.11/3.13, `shellcheck` (severity warning)
over the shipped `hooks/*.sh` + `lib/*.sh` + `lib/delegate/*.sh`, a manifest-version
consistency check (`plugin.json` == `marketplace.json` == the topmost `CHANGELOG.md`
release heading), and a non-blocking `claude plugin validate .`.

---

## (a) Storage & data model

A task is **one JSON dict**. The default backend (`store.py::SqliteBackend`) keeps the
full dict in a `data TEXT NOT NULL` column of a single indexed `<data_dir>/store/tasks.db`;
the typed columns alongside it (`seq`, `status`, `updated_ts`, …) exist only to index and
sort, so no field is ever dropped. `all_tasks()` runs on **every** user message via the
hooks, so listing/sorting are indexed queries rather than hundreds of file reads.

```
PRAGMA journal_mode=WAL        # concurrent CLI + Desktop + hooks don't lock each other
PRAGMA busy_timeout=5000
PRAGMA synchronous=NORMAL
tasks(id PK, seq, title, summary, status, color, effort,
      created_ts, updated_ts, pinned, sessions, session_meta, log, data)
links(session PK, task_id, n, edited, blocked)   # one row folds every per-session sidecar
tasks_fts USING fts5(task_id UNINDEXED, content)  # full-text search index (see below)
```

The `links` table folds into one row per session what would otherwise be sidecar
files: the session→task pointer, the `.n` miss counter, and the `.edited`/`.blocked`
gate markers. `sqlite3` (stdlib) is a hard requirement — if it is somehow unavailable
`get_backend()` raises a clear `RuntimeError` rather than degrading. **The task data
is never migrated** — `SqliteBackend` uses an existing `tasks.db` or creates a fresh
empty one. The one schema evolution that *does* run is the search index: `PRAGMA
user_version` gates a one-time, idempotent FTS backfill (below), which builds the
index from existing rows without touching any task data.

**Full-text search (`tasks_fts`).** A standalone FTS5 table indexes each task's searchable
text — `task_search_text()` flattens the title, summary, goal, next-step, decisions,
checklist steps, activity log, dated history, and linked repos/PRs/stories into one blob.
It's a *standalone* (not external-content) table because contentless/external tables can't
return `snippet()`, which the tier-1 output needs; `task_id` is stored `UNINDEXED` purely to
map a hit back to its task. The index is kept in sync **in the write path** (`save_task`
delete-then-inserts the row inside the same transaction; `delete_task` removes it) rather
than via triggers, since the blob derives from the JSON `data`, not from columns a trigger
could see. `search(query)` builds a safe MATCH expression (alphanumeric tokens → `tok*`
prefix terms, AND-combined — no user input reaches FTS5 as an operator) and ranks by
`bm25()`. **FTS5 is feature-detected once** (a throwaway in-memory table) and cached; on a
build without it — or if an individual FTS query errors — `search()` degrades transparently
to `_like_search()`, a ranked substring scan over the same text blob. A search or index
failure is always swallowed: it must never break task tracking.

**Time & cost stats (derived, no new tables).** Active time is derived from append-capped
`spans` (`[[start, end], …]`) stored on the task dict: `record_activity_span()` (called from
`touch()`, i.e. on every activity bump) extends the current span when the bump is within a
30-minute idle cap and starts a new one past it, so idle gaps aren't counted; `time_in_task()`
sums the spans. Worker cost accumulates in a `cost` field (`{total_usd, runs}`) via
`add_cost()` — `delegate.py` calls the `add-cost` subcommand after parsing each worker's
`total_cost_usd`. `task_stats_line()` renders both into the detail/board/Desktop views.

**Lifecycle — one field, three states:** `open ◦ → active ● → closed`. `task_status()`
treats any missing/unknown value as `open` (back-compat with pre-lifecycle tasks).
`set_status()` only moves between the settable board states (`open ⇄ active`) and is
idempotent; **closing goes through `/done`, not `set_status`**, so a typo can't mislabel a
task. `promote_active()` lifts `open → active` when work starts (a file edit in an attached
session, a `--worktree` delegation, the manual `status` command, or `create --active`) and
**never resurrects a closed task**.

**`seq` vs `uuid`:** every task has a `uuid4` `id` (the stable internal key) and a `seq` —
a permanent, never-reused, creation-order integer that is the number you see in `/todo` and
type as `/todo <n>`. `ensure_seqs()` backfills missing seqs idempotently in creation order;
a task keeps its number as others are added, closed, or reordered by recent activity.

### Decision supersession & pins (`lib/decisions.py`)

**Validity replaced age as the visibility criterion.** Truncation and supersession were
two answers to one question — *what should brief a fresh session?* Truncation answered by
**age**, which is a proxy for load-bearing-ness, and a wrong one: it hid valid old
decisions and showed invalid recent ones. Supersession answers by **validity**, which is
the actual criterion. While supersession was the only verb, age was a crude backstop for
the cases it couldn't express; once `split` and `merge` landed, the proxy stopped being
merely unnecessary and became harmful.

The evidence is a real task, not a hypothetical: a naming law sat in the digest's hidden
tail, so it never briefed anyone — and its own author violated it hours after reading
that digest, writing two retired codenames into a spec. Truncation caused that. It also
manufactured the bloat it hid (see the 27,707-char decision below): when only the recent
entries render, an author front-loads everything into one entry to land inside the window.

So **there is no truncation**. `decisions.digest_order` returns **every still-current
decision** — no age limit, no count limit, no `… +N earlier` pointer, because nothing is
folded away to point at. The one thing that removes a decision from a present-tense
surface is no longer being true.

Every present-tense surface is untruncated now, and they agree: the default detail digest,
the feed's `decisions_tail` (the board's data — the field keeps its name because it is
schema-3 wire contract carried by live peer feeds, even though there is no tail left to
take), the `task.checkpoint` / `snapshot` stream digest, and the Obsidian export.

`lib/decisions.py` is the one owner of the element shape and gives the log two pieces of
state — one for validity, one for reading order:

- **Superseded** — `update --decision '<new>' --supersedes <n>` (repeatable, so one
  decision may replace several) marks decision `n` as replaced. A superseded decision is
  **wrong, not merely old**, so it is omitted from every present-tense surface: the
  default digest, the board view-model, the `task.checkpoint`/`snapshot` stream digest,
  the feed's `decisions_tail`, the Obsidian export, and the wikilink co-citation graph.
  It survives **only** in `/todo <n> history`, marked `⊘ … — SUPERSEDED by decision <n>`.
  History's job is to stay complete. (Full-text search still indexes the text, so you can
  still *find* the task by a retracted phrase — search is a locator, not a briefing.)
- **Pinned — ordering, not visibility.** `--pin` (on the decision being added) or
  `--pin-decision <n>` / `--unpin-decision <n>` sorts a decision **first** in the digest.
  It no longer controls whether a decision appears, because nothing current is hidden:
  `digest_order` returns pinned entries first (oldest-first among themselves — the
  **architecture spine**, the rules the rest of the work must obey), then every unpinned
  current decision oldest-first (the narrative, in the order it happened). Pins carry `★`
  so the spine stays tellable apart from the narrative. Unpinning does not hide anything;
  it just returns the entry to the narrative block. **There is no pin cap** — one existed
  only to protect a recency budget that no longer exists.
- **A write-time length advisory, never a gate.** A decision written past
  `decisions.LONG_DECISION_CHARS` (600) prints one line naming its length and suggesting
  `heal --split`; the write **always succeeds**, in full, byte-identical. This must never
  refuse, and the reason is on the record: the old pin cap *did* refuse, and the refusal
  produced a workaround rather than a fix. A limit on a decision's length would push the
  author to drop a fact, or to fake two decisions out of one to get under the number.
  **It is the ONE size number in the project**, and heal's two thresholds are multiples of
  it (`heal.OVERSIZE_PROPOSAL_CHARS` = 2× = 1,200, a proposal; `heal.OVERSIZE_CHARS` = 6× =
  3,600, a finding) rather than a separate opinion. They used to be one flat 4,000 —
  2.4× this and referencing nothing — so the write path and the reconcile path disagreed
  about what "too long" meant and neither could be retuned without the other silently
  drifting. 600 fires at write time, when the author still has the context to split
  cheaply; the multiples judge an entry already in the log, so they can afford to be far
  less sensitive, and 6× is where an entry stops being supersedable a piece at a time.

Pinning a **replaced** decision (superseded, split or merged) is an error; replacing a
pinned one clears the pin. A bad or already-replaced index is a **loud error, never a
silent no-op** — a dropped supersession leaves the wrong decision live, which is the whole
bug. Indices are 1-based and stable (the log is append-only), and they are exactly the
numbers `history` prints. Supersession is only the **capture-side** half of this: the
reconcile pass that goes back over an already-drifted log, and the other two verbs it
needs, are the next section.

**Dual element shape — permanently.** A decision element is EITHER a plain string
(legacy) OR `{"text", "superseded_by", "pinned"}`. Every reader must accept both, and
they do by going through `decisions.text()` / `is_superseded()` / `is_pinned()` rather
than touching an element directly. The write side holds the other half of the guarantee:
`decisions.compact()` collapses an element back to a plain string the moment it carries
no metadata, so a decision that is neither superseded nor pinned is stored **byte-
identically to how every older version stored it**. Only the entries actually carrying
state become dicts, and unknown keys from a newer writer are preserved rather than
dropped. Consequence: an older reader keeps working, and degrades on at most the handful
of decisions that carry metadata.

### The reconcile model — `heal` and the three verbs (`lib/heal.py`)

Supersession above is **capture-side**: it works only when the author of a new decision
remembers to link the old one. Nothing ever went back over a log that had already
drifted. task-station had `save` (capture) and no reconcile, so the log only grew, the
digest truncated it by **age**, and the record never said *"these sixteen entries are now
four."*

Measured on one real task before this existed: **72 decisions, 68 still current, ~96,000
chars** — about 24k tokens of resume context, averaging 1,351 chars each. The longest
single decision was **27,707 chars**, and it was that long *because* the digest truncated
by age: its author front-loaded everything into one entry hoping it would land inside the
visible window. **Truncation manufactured the bloat it then hid.**

Truncation has since been removed outright, and that **raises** this module's stakes
rather than lowering them. Reconcile used to compete with a recency window that hid the
mess; now the digest shows exactly what the log contains, so `heal` is the only thing
standing between a resumed session and all 96,000 characters. `--pin` survived the
removal with a narrower job — **reading order**, not visibility.

**Three verbs, one shape.** `decisions.py` owns all of them, and each MARKS the original
and drops it from `live()` — the single selection every present-tense surface goes
through — while keeping it in `history` labelled with what replaced it:

| Verb | Stored as | Means | History reads |
|---|---|---|---|
| supersede | `superseded_by: n` | something **refuted** it | `⊘ … — SUPERSEDED by decision 5` |
| **split** | `split_into: [n, …]` | it was **compound** | `⊘ … — SPLIT into decisions 6, 7` |
| **merge** | `merged_into: n` | **true but no longer load-bearing** | `⊘ … — MERGED into decision 8` |

`split` exists because supersession is too blunt for a decision that mixes still-valid
rulings with refuted ones — superseding it destroys the good content, keeping it briefs
the bad — and because a 27,707-char entry is unreadable regardless of correctness.
`merge` exists because supersession **cannot express "true but no longer load-bearing"**:
four release records, seven scrub-iteration steps, three process-error corrections, two
memo-chain acks — 16 decisions that reconcile to about 4. Nothing refuted them, and
marking them wrong would put a lie in the record.

**NON-DESTRUCTIVE IS THE HARD RULE.** No verb deletes a decision; history is the complete
trail and that is its entire job. All three are reversible through one inverse,
`decisions.restore` (`update --restore-decision <n>`), which clears whichever mark is
present. A decision carries **exactly one** replacement — `_check_unreplaced` refuses to
double-mark — which is what keeps the inverse unambiguous.

**Two layers, split by cost.**

- **Layer 1 — `heal.scan()`.** Deterministic, zero tokens, and it never mutates the task.
  Eleven findings plus the health metric, which is a measurement rather than a
  finding: undispositioned acks · corrections whose target was never updated · unlinked
  supersession language (prose pretending to be structure) · oversized decisions · drift
  (recorded paths/worktrees/branches that vanished) · link rot · the health metric · stale
  steps (which now **name the verb** that retires them, `update --step-supersede <n>`) ·
  **re-fragmented consolidations** · **steps restating a superseded decision** · **cited
  commits that resolve nowhere** · **the digest grew with merge candidates outstanding**.
  Results land in a per-task **gate file** under `<data_dir>/heal/`.
  Modelled on `hook_health.py`: everything fails open, an unreadable gate means no nag.
  All three outward probes default **off** — no git subprocess for branches OR cited
  commits, no HTTP — so the path the SessionStart nag runs is pure Python plus `stat`. Link
  rot degrades to **unknown** on any failure and never reports a live link as dead;
  `heal --scan --probe-links` is the opt-in HTTP HEAD, where only an explicit 404/410
  counts as dead.

  **The cited-commit check is the first OUTWARD one** (`heal.commit_citations` →
  `heal.commit_prober`), and it is the only check that asks a question of something the task
  does not contain: `git cat-file -e <sha>^{commit}` in each repo the task recorded. It
  exists because every other check has a structural ceiling — a rebase or force-push that
  erased a cited commit leaves the record perfectly self-consistent. It matches **declared**
  citations only (`commit <sha>`, `merged <sha>`, `sha <sha>`, `pushed <sha>`, `main @ <sha>`)
  plus a digit gate, because 7-40 hex characters is also a task id, a memo id8, a heal
  fingerprint and a tree hash — and `defaced` and `acceded` are hex-only English words. The
  prober is injected exactly like `branch_prober`, and UNKNOWN is never a finding.

  **The dismissal ledger** (`heal_dismissals`, additive) is how an adjudicated false positive
  stops reappearing. On one real task 17 findings stood and **9 were dead paths a human had
  already ruled on**, re-reported every pass — the cry-wolf failure arriving from a fifth
  direction. `heal --apply --dismiss '<check>:<ref>' --why '…'` records the check, the ref, a
  **mandatory** why, the moment and the session; `apply_dismissals` then drops the finding
  from `findings` into `dismissed`, so the issue count, `due()` and `plan()` all stop seeing
  it from ONE place. The fingerprint is sha1 over the finding's **matched text** (check + ref
  + detail — the detail is where each check records what it matched), so a ruling survives a
  re-scan and expires the moment that text changes: it adjudicates one state, never a
  category. `--undismiss` marks the entry retired rather than removing it, and the listing
  flags an active ruling whose text has since changed as **EXPIRED**. A dismissal is its own
  invocation and never stamps a heal — adjudicating a false positive is not reconciling.

  The drift check's branch half has to **earn** a finding. It reads branch names out of
  narrative prose (`goal` / `state` / `summary` — there is no structured branch field), and
  a bare `branch\s+(\w+)` match reported `branch prefix`, `branch off`, `branch while`,
  `branch names` and `branch with` on one real task: **5 of 7 findings were the English
  word after "branch"**, and a check that cries wolf 5 times out of 7 is worse than no
  check, because it trains the reader to skip the 2 that matter. A candidate now has to be
  **ref-shaped** — backticked (the author marked up a literal), or carrying a `/`, `-`,
  `_` or digit the way every real name does (`heal-wip`, `origin/dev`, `2707-rollup`), or
  one of the conventional bare names (`main`, `master`, `dev`, `develop`, `trunk`,
  `staging`, `production`, which English never puts *after* the word "branch"). The false
  negative is the deliberately cheaper failure, same asymmetry as the unknown-probe rule:
  a missed dead branch costs one confused resume, a false "your branch is gone" costs the
  check's credibility.

  The drift check's PATH half has to earn one too, from the opposite direction: some
  recorded paths were **never expected to survive**. task-station auto-captures edited
  files as artifacts, and a delegated worker writes its brief into the **session
  scratchpad**, which is wiped when the session ends *by construction*. On one real task
  that produced **7 drift findings, all 7 of them worker briefs** under
  `/private/tmp/…/<session-uuid>/scratchpad/`. Every one was literally true and
  practically useless — nobody resumes a task by opening a brief out of a deleted temp
  directory — and together they made a heal **due** on a task with nothing wrong with it.
  So `heal.ephemeral_path` excludes two shapes outright: a `scratchpad` path segment
  (session-scoped wherever it sits) and the known temp roots (`/tmp`, `/private/tmp`,
  `/var/tmp`, `/var/folders`, `$TMPDIR`). `heal.vanished_ephemeral` counts what it
  skipped and the report prints **one line**, not one bullet per file — seven bullets
  naming seven wiped files read exactly like seven defects. The asymmetry here points the
  other way from every other check in the module, and deliberately: a missed ephemeral
  costs one bogus finding, while misclassifying a repo path as ephemeral would silently
  drop the finding that matters. So nothing is inferred from a path merely *looking*
  temporary — only a named segment or a known root counts.

  The **unlinked-supersession** check earns its findings the same way, for the same
  measured reason. Matching the vocabulary alone (`was wrong`, `no longer`, `supersedes`,
  `corrected`) fired 5 times on one real task and **4 were false**: a decision describing
  the supersede *feature*, a rule superseding another rule, a correction to a memory note,
  a memo chain. So two conditions must both hold — the language, **and** a
  decision-shaped target: `decision N` / `entry N` / `#N`, naming a decision that is in
  range and **earlier** than the entry doing the talking (a decision can only refute one
  that already existed). A `#N` whose preceding word names something else — `memo #3`,
  `task #444`, `PR #12`, `step 2` — is dropped. A decision that already carries a
  `--supersedes` link is skipped, because there the prose is just describing what the link
  records.

  The **stale-step** check now shares that discrimination rather than repeating the
  mistake. It shipped matching the vocabulary alone, and after a full reconcile of the
  same task the only two findings left were both false — `delete stale tracked
  BRIEF-….md` (`stale` names a *file*) and `the names in the superseded ancestor are
  REJECTED` (`superseded` names an *ancestor*, and that step **is** the correction the
  heal wrote). Two false positives and nothing else put `Heal due?` back on **YES** for a
  task with nothing to do — the false alarm the heal stamp exists to prevent, re-created
  by the check reporting the reconcile's own output. So both checks read the word
  standing **in front** of the keyword (`heal.qualifier`, one helper, two vocabularies).
  A step is reported only when the keyword **declares**: it opens the step, a line or a
  clause, or it is the predicate of one (`this step is stale`, `steps 3/4/5 above are
  STALE`, a `READ-ME-FIRST` warning about other steps, a bare `do not execute`). A
  keyword sitting mid-sentence as an adjective on another noun is a description, not a
  declaration. Already-superseded steps are never reported at all.

  **DECLARE vs DESCRIBE is a stated rule of this subsystem, not a patch applied three
  times.** The same bug shipped **four** times: the drift check scraped branch names out
  of English prose, the unlinked-supersession check fired on decisions *explaining*
  supersession, the stale-step check fired on the step written to *fix* staleness, and
  the memo correction backstop (`correction_language`) fired on a release note that
  mentioned "a superseded ancestor" and on a memo reporting that *someone else* had
  withdrawn a release. Every keyword check here must therefore answer one question
  before it reports — **does this text DECLARE the condition, or merely DESCRIBE it?** —
  and there is exactly **one** implementation of that question: `heal.qualifier` reads
  the word standing in front of the match and `heal.declaring_hits` drops every hit that
  is only qualifying some other noun. A new check brings a **vocabulary** to that helper,
  never a fifth heuristic. Five vocabularies answer it in its front-word form:
  `NON_DECISION_QUALIFIERS` (unlinked
  supersession), `DECLARING_QUALIFIERS` (stale steps), `SELF_DECLARING_QUALIFIERS` +
  `NOUN_DECLARING_QUALIFIERS` for the memo backstop — which needs two because its
  patterns are not all the same part of speech — and `CONSOLIDATION_QUALIFIERS`, below. In
  front of the participle *superseded*, the article in "a superseded ancestor" is exactly
  what makes it describe another noun; in front of the noun *correction*, the identical
  article in "a correction" still declares one.

  **And a fifth time, because the front word cannot answer a second question: WHO does the
  sentence say did it?** `corrected by decision 184`, `decision 173 investigated` and `why
  decision 150 is NOT superseded` all satisfy the unlinked-supersession check's two older
  conditions perfectly, and on one real task **8 of 17 findings** were that one shape — each
  of them a decision *minuting* another decision's work rather than contradicting it.
  `heal.reports_another_decision` reads three things about the reference's own clause, each a
  vocabulary rather than a fourth heuristic: the word in **front** is `by`/`per`
  (`REPORTING_QUALIFIERS` — the reference is the agent), the word **after** is a reporting
  verb (`REPORTING_VERBS` — the reference is the subject), or a supersession keyword in the
  clause is **negated** (`NEGATING_QUALIFIERS` — the sentence denies the condition). A form
  of *to be* after the reference is deliberately not a reporting verb: `decision 4 was wrong`
  is the finding worth having. Eight vocabularies now exist, and the discriminator's own
  false negative — a number both declared against and reported on in one long entry — is the
  cheaper failure this module always chooses.

  The **re-fragmented-consolidation** check (`heal.refragmented_consolidations`) is the one
  place in this module where a leading-shape match becomes a **finding** rather than a
  proposal, and the reason is not the strength of the evidence — it is whether anybody has
  already ruled. Measured: a real task scanned clean on all eight checks, and the judgement
  half then found `CONSOLIDATED — THE 2.7.0-2.11.0 RELEASE LINE … (replaces the five
  per-release records)` with **four more** release-shaped decisions appended after it over
  the following day. Nobody undid the merge; the shape simply grew back around the entry
  that had just declared itself the single record of it. Four release records side by side
  are a *proposal* because nobody has ruled on them; this is a *defect* because an earlier
  pass wrote down that the subject is **one** entry and the log now contradicts that ruling
  — the digest hands two answers to the same question to every fresh session — so it counts
  as an issue and can make a heal due.

  It reads the shape a consolidation covers from **two** sources, each self-justifying.
  STRUCTURE: the decisions marked `merged_into` it — proof rather than inference, requiring
  no text at all, which is what covers a summary `--apply` wrote itself. PROSE: the earlier
  decisions the entry *names* (`decision_refs`), used only when the entry also **declares
  itself** a consolidation via `declaring_hits` + `CONSOLIDATION_QUALIFIERS` — `CONSOLIDATED
  — …`, `this decision consolidates 4, 9 and 17` and `one reconciled record of 5 decisions`
  declare one, while `a consolidation of the release trail` and `a wrong merge writes a
  false consolidation into the record` merely describe one. Both sides read through
  `live()`, so a stray that has since been merged in stops counting and the finding goes
  quiet on its own fix. What it deliberately does **not** do is propose the merge: naming
  the surviving summary is judgement, and a wrong merge writes a false consolidation into
  the record — the very thing this check exists to catch.
- **The checklist half of the record** (`heal.steps_restating_superseded`). All three
  reconcile verbs are DECISION verbs, and every other check reads two things on the SAME
  object — so nothing ever compared a **step** against a **decision**. On one real task
  that produced a scan reporting every check clean and `Heal due? no` while five live
  steps named work the task's own **superseded** decisions had retired, and a cold session
  reads the checklist *first*. Reported when a live step and a superseded decision share
  at least `STEP_RESTATEMENT_OVERLAP` of their significant vocabulary (Jaccard over
  `_significant_words`, the tokenizer the merge shapes already use; both sides need
  `STEP_RESTATEMENT_MIN_WORDS` distinct words before the ratio means anything). A
  **finding**, not a proposal — a superseded decision is one the task marked *refuted*, so
  a live step ordering that work contradicts a ruling the record already carries. But a
  **provisional** one, and it says so in the detail line: text overlap cannot separate a
  step that still orders the retired work from the step written to *record* the
  retirement, because both name the same thing. That is the declare-vs-describe problem
  in a form the guard cannot answer, so the answer is silence wherever either reading
  fits — any step carrying correction vocabulary at all is skipped, read with the
  over-eager `matched_language` on purpose, since here over-eagerness can only cost false
  negatives. Already-superseded steps are skipped exactly as in the stale-step check.
- **The report sections that are deliberately NOT checks.** `scan()` returns them
  beside `findings`, and `due()` counts `findings` alone — folding any of them in would
  put `Heal due? YES` on a healthy task, which is the failure mode this module has
  already had to fix four times. (The goal review is the single, deliberate exception, and
  only for `due()`: it is still never an *issue*. See it below.) One is the
  **expected-ephemeral count** (`heal.ephemeral`), described with the drift check above.
  Another is the **long-decision proposal tier** — over 2× the write advisory, worst-first,
  capped at five with a `+N more` — which is the normal length of a working entry and so must
  never be an issue; only the 6× finding tier is ever planned as a `--split`.

  **Merge candidates** (`heal.merge_candidates`) group current decisions by a **leading
  shape** — a version-like prefix plus the word after it (`<version> shipped`), or the
  first three significant words (`my process error`) — and propose any group of **three
  or more**. The judgment list had been telling the reconciler to "MERGE what is TRUE BUT
  NO LONGER LOAD-BEARING" and leaving them to find them; on one real 99-decision task a
  human found all sixteen mechanically, by matching exactly these prefixes. They are
  **proposals**: never findings, never ops, never applied. This is not `merge_clusters`
  (layer 2), which is narrow and sure because `--apply` performs it; this one is wide and
  unsure because a person reads it, and a wrong merge writes a false consolidation into
  the record.

  **Pinned decisions** (`heal.pinned_review`) are listed with each one's age, because a
  pin puts that entry at the head of **every** session's digest — so a line that has
  quietly gone stale in a pinned decision costs more than the same line anywhere else. On
  one real task a pinned decision still named two codenames a later decision had retired
  and had been briefing every session with them for days; no check would ever have said
  so, because none of them asks whether a decision is still *accurate*. Ages come from
  the `decision` events (`heal.decision_ages`) — the log is a list of strings with no
  timestamp of its own — so an append older than the bounded event feed reads as **age
  unknown**, never as new. Informational, exactly like the health metric: being pinned is
  not a defect.

  **Subject candidates** (`heal.subject_candidates`) are the stronger merge tier and render
  above the shape one. They group current decisions by what the entries are **about** —
  overlapping **step** references (explicit `step N` / `steps N-M` shapes only; a bare number
  is never one), a shared **release version**, a shared **PR/story number** — transitively, so
  one subject is one group rather than three overlapping pairs. **Two** members are enough
  here where the shape tier needs three, because a shared subject is direct evidence and a
  shared opening phrase is not. A group is tagged `completed-subject` when every step its
  members name is **done or superseded** (`heal.completed_subjects`): the checklist itself
  reporting that the work those entries record is finished, which is the closest the record
  ever comes to stating "true but no longer load-bearing" on its own. Still proposals —
  nothing here is ever an op. `heal --candidates` (`heal.candidate_lines`) prints the goal, the
  pins and every group's members **in full and nothing else**, because the full dry run is
  ~94% corpus and a reader working the merges does not need it.

  **The size objective** (`heal.size_objective`) reports `chars now / at last heal / delta`
  against `chars_at_last_heal`, an additive snapshot `stamp_healed` takes AFTER the pass's
  operations — so the baseline is the size the heal *left*. A char total with nothing to
  compare it against cannot tell a digest 40k down from last week from one 40k up, and down is
  the point of the pass. No baseline reads as *no baseline recorded*, never a zero delta. It is
  informational **except** in one conjunction: a digest that **grew** while **≥1 merge
  candidate group** was outstanding is a finding (`heal.grew_with_candidates`, one per task,
  ref `digest`). Neither half is a defect alone — a working task records work, and a group
  nobody has ruled on is not wrong — but together they say the record is getting more
  expensive to brief in exactly the place a named verb was waiting.

  **The goal review** (`heal.goal_review`) names the goal line and counts the decisions
  that have landed since it was last written **or re-read**. The goal is the one field that
  says what **done** looks like, and nothing else on the task claims to say it — so there is
  no second thing to cross-reference it against and no check can ever raise one that
  describes a mission already accomplished. The baseline is a write-time snapshot,
  `goal_touched` (`{"ts", "decisions"}`), written by `update --goal` when the text
  actually moves and by `create --goal`; with no snapshot the section says **cannot be
  counted** rather than zero, for the same reason `accrual` does, and every task predating
  the field takes that path. Never an **issue** — a goal is supposed to outlive the decisions
  that pursue it — but it is the one non-check section that reaches `due()`: past
  `config.heal_goal_review_due()` (25, positive-only, `TASK_STATION_HEAL_GOAL_REVIEW_DUE`)
  decisions since the last write or re-read, "N decision(s) since the goal line was last
  reviewed" is a due reason. **Re-reading is the service**, so `heal --goal-reviewed` records
  it in a SECOND additive field (`goal_reviewed`, same shape) and resets the count without
  rewriting a correct sentence; `goal_review` reads the LATER of the two baselines. It is kept
  apart from `goal_touched` because that field is the age of the goal *itself* and
  `checker.py` reads it as such — and because `--mark-healed` deliberately does **not** reset
  the count: a stamp saying the record was read is not one saying this line was ruled on.

  **What has accrued, and the one gap this layer structurally cannot cover**
  (`heal.accrual`). On the same task as the re-fragmentation above, a **release had shipped
  and was recorded nowhere** — no decision, no log entry, no PR link — while every check
  reported clean. Nothing contradicted anything, because the work had happened entirely
  *outside* what the record holds. That is not a tuning failure, it is structural: every
  check here works by cross-referencing two things the task itself holds, and work recorded
  nowhere leaves nothing to cross-reference, so the scan cannot tell "no release happened"
  from "a release happened and nobody wrote it down". A check that guessed would be the
  **fifth** confidently-wrong check this subsystem has shipped and then fixed, so there is
  deliberately none. Instead the layer does the two honest things it can: it **counts** what
  has been recorded since the stamp — decisions, dated log entries, PR/story links, steps —
  and the report **says the gap out loud**, instructing the reader to verify that
  **everything which actually shipped** since the last heal has a decision. The counts come
  from `healed_counts`, the four-counter snapshot `stamp_healed` writes, by exact
  subtraction — the same technique as `save`'s `saved_counts`, and for the same reason: a
  write-time snapshot cannot age out from under a busy task the way the bounded event feed
  can. A stamp with no snapshot (an older version wrote it) reads as *no baseline recorded*
  rather than as four zeros, since zeros read as "nothing happened" when the truth is
  "nobody measured". The section prints on **every** scan, including a clean one with all
  counts at zero: a clean scan is where the pass stops, so a gap named only when something
  happened to accrue is one nobody reads on the run where it mattered. **A clean scan means
  the record does not contradict itself; it does not mean the record is complete.**
- **The step verb.** `steps.py` (a leaf, exactly like `decisions.py`) owns the
  `{"text", "done"}` element shape and adds one reconcile verb: `superseded: true` plus an
  optional `superseded_by: n`, written by `update --step-supersede <n>` and cleared by
  `--step-restore <n>`. It exists because a stale step had no honest exit — ticking it done
  is a lie, deleting it destroys the record, and a "do not execute step 3" warning step is
  the anti-pattern one real task already contained (three of its steps read as stale and
  one named a vocabulary retired days earlier). A superseded step leaves the active
  checklist, `/todo <n> history` still shows it marked `⊘ … — SUPERSEDED by step <n>`, and
  `steps.progress()` excludes it from **both** sides of the `n/m` counter — a stale step
  left in the denominator makes a task read as permanently unfinished, which is exactly
  the pressure that gets a step nobody did ticked done. Indices stay **stable ids**, so
  the active checklist can show gaps; renumbering would silently repoint every command a
  reader had in hand. There is deliberately **no step edit**: rewriting a step in place
  mutates the record.
- **Layer 2 — `heal.plan()` + `apply()`.** The plan is the **mechanical subset** a machine
  gets right unaided: cut an oversized decision on *its own* paragraph/list/sentence
  boundaries, collapse a cluster the signatures actually matched, retro-dispose acks that
  predate dispositions. Cluster detection has two confidence tiers — a *named* signature
  (a release record) merges at 2, the weaker *stem* signature (four shared leading words)
  needs 3 — because a wrong merge is reversible but still costs someone a confused read.
  Everything needing judgment (which half of a compound decision was refuted, whether
  prose saying "no longer" supersedes entry 8, what `state` should now say) is **reported,
  never guessed**, and worked by the LLM pass.

**Safety.** `--dry-run` is the **default** — a bare `heal` prints the plan and mutates
nothing. `--apply` writes `<data_dir>/heal/<id>.bak-pre-heal.json` first and **refuses to
run** if that backup fails. The `--log` milestone trail and `history` are never touched.
Per-task by default; `--all` is the only multi-record path and warns about its scope
before acting. Because the log is append-only, every verb appends its replacement(s)
*before* marking the original, so indices never shift and ops planned together apply in
any order.

**A report must not reprint what its reader just read.** `--apply` used to re-render the
entire dry run — scan block, merge candidates, pinned set, every current decision, the
nine-item judgment list — with the applied lines bolted on. Measured on one small task
the two blocks were **4,021 and 4,174 characters**: the same block twice. On a real
40-decision task the dry run is **~47,000 characters (~12,000 tokens) and 94% of it is
the decision list**, so the obvious two-step — read `heal`, then run `heal --apply` —
paid that cost **twice for one heal**, and the second copy told the caller nothing it had
not just read. `--apply` now prints only what it did: the operations performed and
skipped, the backup path, the new health line, and whether anything is still outstanding
(`_heal_applied_block`). **`--apply --verbose`** restores the full render for anyone who
wants it.

**The flow belongs to the skill, not the CLI.** A CLI is one-shot and cannot hold a
conversation, so dry-run-as-default is the only safety it can offer and it stays that way
for scripting. Everything conversational — run the cheap `--scan` first, read the dry run
**at most once**, do the judgement, execute, stamp, re-scan, report — is prescribed by
`skills/heal/SKILL.md`, which
also states plainly that **the user should never need to type `--apply`, `--merge`,
`--split`, `--dispose-acks` or `--mark-healed`**: the skill chooses them. `commands/heal.md`
opens by running `heal --scan` rather than the dry run, so the expensive block is never
the first thing anyone pays for. Guard: `--scan` and `--apply` together are refused
rather than silently scanning, so an `--apply` typed alongside the command's own `--scan`
can never be swallowed by the read-only path.

**The approval gate is gone, and the undo trail is what replaced it.** `/heal` used to
stop after the plan and ask before applying; it now runs scan → judge → apply → verify in
one pass, because stopping between steps is the cost the user actually feels. That is only
defensible if reversing a wrong call is as cheap as approving one was, so **every write
now prints the exact command that reverses it, generated from the indices it really
touched**: `heal.apply` records `op["undo"]` on each op it performs and `heal.undo_lines`
renders them into the `--apply` report, while `_update_one` does the same for
`--supersedes` and `--step-supersede` — the two judgement verbs, which write immediately,
never pass through `--apply`, and therefore take no backup at all. `--restore-decision`
and `--step-restore` are `action="append"`, so the generated commands **repeat the flag**
rather than comma-joining (`heal._restore_flags`): an undo line argparse would reject is
the one failure mode an undo line must not have. One write has no inverse — a
retro-disposition, which a heal never overwrites — and its line says so and names the
pre-heal blob instead of inventing a command. **Removing the gate did not widen what gets
applied**: merge candidates are still proposals, never ops.

**The task may be named positionally.** `commands/heal.md` passes `$ARGUMENTS` straight
through, so `/heal 12` reaches the CLI as a bare `12` — and with no positional on the
subparser argparse exited with `unrecognized arguments: 12`, killing the one form a person
actually types. The fix cannot live in the command file, because `$ARGUMENTS` legitimately
carries `--task <n>`, `--all` or nothing. `_heal_positional_ref` folds the ref into
`--task` **before any task is loaded** and refuses two combinations rather than resolving
them: alongside `--all` (different scopes), and alongside a `--task` naming a different
task (a silent precedence rule would reconcile the wrong record; the *same* ref twice is
accepted, since `/todo heal 12` fills both slots itself). Nothing is resolved here —
`_heal_targets` still does every lookup, so both spellings behave identically.

**`Heal due?` is rendered as three rows, not one** (`heal.summary_lines`, shared by the
scan report, the dry-run brief and the zero-operation refusal). `Mechanical` is what the
checks found; `Judgment` is whether the half no check can do has been *recorded* — the
only evidence a task can hold for that is a stamp carrying a `--note`, so without one it
says **NOT RUN**, which is a statement about the record and not an accusation; then the
verdict, in its exact old wording. A lone `Heal due? no` reads as "this task is a complete
record", and that reading is what let both a shipped-but-unrecorded release and an
overtaken goal pass for healthy. Rendering only: `due()` keeps its signature and return
shape, because the nag, the gate file and `gate_line` all read it.

**The heal has to stamp itself.** On one real task, after **17 merges, 5 supersedes and a
split**, the scan still reported `last heal never` and `97 new decision(s) since the last
heal` — so "heal due?" was permanently YES and the count was meaningless, which trains the
reader to ignore the one signal built to be trusted. Additive keys on the task blob fix it
(`last_heal_ts`, `decisions_at_last_heal`, `last_heal_kind`, `healed_counts`,
`chars_at_last_heal`, plus an optional `last_heal_note`); a task written by an older version
simply carries none of them and reads as never healed, with its accrual counts and its size
delta reported as unmeasurable rather than as zero.

- An **`--apply` that performed at least one operation stamps.**
- An **`--apply` that performed none is REFUSED** — it changes nothing, writes no backup
  and stamps nothing, and names the two honest moves instead (pass operations, or
  `--mark-healed --note`). The opposite shipped first, on the theory that running
  `--apply` is itself the assertion that the task was reconciled; in practice it is the
  command someone runs when they assume `--apply` **is** the heal, and it wrote `last
  heal <just now>` onto a task nobody had touched. It never silenced the nag — real
  findings still make a heal due — but a stamp that is *sometimes* a lie is worse than
  the always-on alarm the stamp was added to fix, because it makes every other stamp
  unreadable.
- **`--scan` never stamps.** It is read-only, and that is its whole contract.
- **`heal --mark-healed [--note '<why>']`** records the judgement-only pass: the log was
  read and nothing needed changing. Without it, that pass leaves no trace at all.
- **`new_since_heal` counts from the stamp.** With no stamp it is `None`, not the total,
  and every surface says *never healed* in those words rather than reporting the whole log
  as new.

**Retro-dispositions must be visibly retroactive.** `heal --apply --dispose-acks
<id8,…|all>` with exactly one of `--decision [TEXT]` / `--memory <slug>` /
`--noop "<reason>"` fills in the dispositions of acks recorded before one was required —
`all` is legitimate, since those sessions no longer exist and their intent is
unrecoverable. Every retro-fill carries `retro: true`, `retro_by`, `retro_ts` and
`retro_why` alongside the ordinary `{kind, value}`, and renders as `ab12cd34→noop (retro)`
on the ledger. The ack's own `sid`/`ts` are **never** rewritten, and a disposition the
acking session chose is **never** overwritten. Naming a subset is surgical: it replaces the
blanket retro-noop the plan proposes, so the acks nobody named stay undispositioned and the
next scan still flags them.

**Cadence.** A self-capping SessionStart nag (the gate file fingerprints the state already
reported, so an unheeded nag doesn't fire every session) plus two gates that **warn and
never block**: `save`, because `--summary` replaces the summary wholesale and writing one
from an unreconciled set bakes the drift into the first field anyone reads; and `done`,
because closing makes that record the permanent one. A heal is due when the scan found
anything, **or** ≥10 new decisions since the last heal (never healed: ≥10 decisions on the
log, worded as such), **or** any undispositioned ack exists, **or** >7 days on an active
task.

### The checkpoint model — `save`, the gap report and the stamp (`lib/save.py`)

`save` is reconcile's other half: `heal` makes the record *consistent*, `save` makes it
*complete*. It carried three of the reconcile module's measured failures plus one worse
one of its own, and `lib/save.py` — a leaf over `decisions` and `steps`, imported by
`task-station.py` and never the reverse — is where all four are fixed.

**A report must not tell its reader what it already knows.** Measured on one real task,
`/save` emitted **71,516 characters (~17,900 tokens) and 71,271 of them — 99.7% — were a
dump of the CURRENT DIGEST**, all 62 decisions included, handed back to the session that
had just written every one of them. This is the same failure `heal --apply` had (a report
reprinting the dry run it had just charged for), and the same fix: the digest is now
**rendered but not printed** — its length is the "what a fresh session loads" number — and
what the block carries instead is the **GAP REPORT** (`save.gap_report`), a few hundred
tokens with four parts:

| Part | What it answers |
|---|---|
| **EMPTY** | Which of the six named slots — goal · state · summary · steps · decisions · links — carry nothing, each with what a resuming session loses by it |
| **STALE** | A `state` that does not begin with `NEXT:` (it reports standing, not a first move); a `summary` written before the decisions and log entries it must cover; a `state` nothing has moved while the record filled up around it |
| **SINCE THE LAST CHECKPOINT** | What has landed since the stamp, by count — the work the new summary has to cover |
| **DIGEST SIZE** | Characters and a labelled `≈` token estimate: what a fresh session will pay |

`/todo save --verbose` restores the full dump for the rare session that genuinely lacks
the state. `/todo save --check` prints the gap report **alone** — no checklist, no
templates — and is **read-only**, the same contract `heal --scan` keeps; it runs before
every write in `_todo_save` precisely so it cannot print "nothing was changed" having just
changed something.

**Exact arithmetic, not the event feed.** Two of those parts need to know what changed
since an earlier moment. Both *could* be answered from the bounded event feed (as
`heal.decision_ages` must, because a decision carries no timestamp of its own) and both are
answered **exactly** instead, by snapshotting the counts at write time the way `heal`
snapshots `decisions_at_last_heal`: `saved_counts` (at the stamp), `summary_counts` (after
every wholesale `--summary`) and `state_counts` (only when the state TEXT actually
changed — copying a stale `NEXT` forward must not buy it another six entries). Every
"N since" number is subtraction. The snapshots are taken **last** in `_update_one`, after
the same update's own `--decision`/`--log` appends have landed; taken earlier, a summary
written in one call with three decisions would immediately read as three decisions out of
date. A task with no snapshot — written by an older version — reads as **cannot tell** and
is reported as nothing at all, the same false-negative asymmetry every check in `heal.py`
chose, arriving from the capture side.

**The summary is non-destructive now too.** `--summary` REPLACES wholesale, and that
replacement was the one destructive write left in this codebase: supersede, split, merge
and step-supersede all keep the original in `history` and every one is reversible, while
the summary — **the first field a resuming session reads** — could be lost outright to a
thin save. `save.push_summary` preserves the previous text on `summary_history`,
append-only; `update --restore-summary [n]` (`save.restore_summary`) brings any version
back and pushes the text *it* replaces, so the restore is itself reversible and nothing is
ever removed. Version numbers are stable because appending never renumbers. The versions
render **only** in `/todo <n> history`, data-gated, so the resume digest is untouched — and
the `update` that replaces a summary names the restore command in its result line, because
a safety net nobody is told about is not one.

**The stamp belongs to the write, and is inferred from the work.** `last_full_save_ts` means
one thing — a full structured checkpoint was **captured** — and it is what tells a real
checkpoint apart from a lighter `--state` refresh. It was being written the moment the
`[SAVE]` block was **printed**, so a session that ran `/save` and then wrote nothing left a
task claiming a full checkpoint with an empty summary. That is `heal`'s zero-operation
`--apply` one layer earlier, and it matters more here because distinguishing the two kinds
of save is the stamp's entire job.

- Emitting the block records **`save_started_ts`** — true, and useful: a later reader can
  see a save that was begun and abandoned. It does **not** stamp.
- The stamp lands on the `update` that writes a **`--summary` AND a `--state` in one
  call** (`save.is_checkpoint_write`). A `--checkpoint` flag was the obvious mechanism and
  is the wrong one for the reason the zero-operation `--apply` already proved: a flag is a
  claim someone can type without doing anything. A summary and a state written together
  cannot be typed without capturing the checkpoint, because they **are** it.
  `--append-summary` deliberately does not count — only the wholesale `--summary` asserts
  "this is the present truth", which is the assertion the stamp records.
- The two staleness flags (`digest_dirty`, `pressure_nudged`) still clear when the block is
  emitted, and that is a different kind of claim: they gate **nudges**, and the nudge has
  been delivered the moment the block is read.

**The cold-read check is mechanical.** It used to be advice — *"re-read the digest as if
you have no memory of this conversation"* — which is unfalsifiable, since no output ever
said whether it was done. Two of its conditions are decidable (**every named slot
non-empty** and **`state` leads with `NEXT:`**), so `save.cold_read_failures` decides them
and the stamping `update` prints the result inline, costing nothing and needing no extra
call. The model is left with the half that genuinely needs judgement.

**The flow belongs to the command file, not the CLI** — the same split `heal` made.
`commands/save.md` prescribes the order: read the gap report (not the digest), fill only
what is missing or stale, write the summary as present truth, record new choices with
`--decision`/`--supersedes`/`--pin`, append exactly one `--log` milestone, run the
mechanical cold-read check, confirm in one line. It no longer hands the model a checklist
and leaves it to hand-assemble a multi-flag `update` regardless of what is already good.

### Memo dispositions (an ack is a receipt, not an integration)

The same "append-only log read as current state" bug bit correspondence too: a memo
announcing a permission-model change was acked, and the correction never reached the
durable layer that auto-loads each session — which still said the opposite. So a
**`memo ack` now requires exactly one disposition**: `--decision [TEXT]` (promote it to a
task decision), `--memory <slug>` (folded into that agent-memory note), or
`--noop "<reason>"` (no durable change needed — reason mandatory). A bare ack is an error
naming all three, and the chosen disposition is recorded on the ack ledger entry, so the
roster shows what each session *did*, not merely that it looked. Because every memo needs
its own disposition, the incentive to batch-ack is gone.

`memo send --corrects <target>` (repeatable — a memory-note slug, `decision:<n>`, or
another memo's id8) declares what a memo replaces. `memo show` prints it above the body,
the unacked-memo nag tags it `[CORRECTS: …]` so its weight is visible before it is opened,
and acking one without engaging the target is refused.

`--corrects` only helps when the sender remembers it, so `CORRECTION_PATTERNS` (a
module-level constant in `task-station.py`) is the backstop: a body matching
`correction` / `supersede` / `retraction` / `withdrawn` / `no longer` / `stop doing` with
**no** declared target warns the sender at send time (never blocks) and prints a prominent
"update the durable store" reminder to the acker.

The keyword alone was **not** enough, and shipping it that way made this the fourth check
in this codebase to cry wolf the same way. It warned on `"Shipped 2.13.1: … one that
merely mentions a superseded ancestor"` (a release note) and on `"the upstream library
withdrawn its 3.0 release"` (someone else's retraction). So it routes through the one
shared discrimination — `heal.declaring_hits` — and warns only when the memo plausibly
declares **itself** a correction: it is retracting or replacing something the reader is
expected to already believe. See *DECLARE vs DESCRIBE* under the reconcile model above;
the rule is stated there so the next keyword check does not repeat this a fifth time.

Enforcement lives at the CLI boundary (`cmd_memo`) — `memo_ack()` itself stays a
permissive mutator, so the MCP/Desktop ack tool and internal callers are unaffected.

**Data dir lives OUTSIDE the plugin cache.** `paths.data_dir()` resolves, in order:
`TASK_STATION_HOME` → `CLAUDE_CONFIG_DIR/task-station-data` → `XDG_STATE_HOME/task-station`
→ `~/.claude/task-station-data`. A plugin installs to a *versioned* cache dir that is
replaced on every `/plugin update`; anchoring state outside it is why updates never wipe
your board or history (see [README → Why Task Station](../README.md#why-task-station)).
Tests set `TASK_STATION_HOME` to a tmpdir for isolation.

**`<data_dir>/cache/` is derived, disposable state.** `msgcounts.json` memoizes the per
transcript user-message count against that file's `(st_mtime_ns, st_size)` — a complete key,
since a transcript is append-only, so a hit can never be stale. Deleting the directory costs
one slow render and nothing else: every read path recomputes on a missing, malformed, or
foreign cache rather than raising (this code runs inside the Stop hook, where an exception
would block the user's turn). It exists because the board asked the same ~460 transcripts the
same questions 4072 times per render — see the note above `_session_msgcount`.

## (a2) Board pipeline (one board · one feed layer)

There is **ONE board** and **ONE feed layer**. No engine flag, no preview, no fallback.

`/todo board` → `write_board()` → `tools/render_board.py`, which writes:

- `<data_dir>/board.html` — the whole board, rendered SERVER-SIDE (rows are present with
  no JS run; that first-load guarantee is law, `BOARD-BEHAVIOR.md` B1). Inline CSS/JS, no
  external assets, no server, no LLM.
- `<data_dir>/board.rev.js` — the change-poll sidecar (`window.__TSREV`), loaded as a
  `<script>` rather than fetched because `file://` blocks local fetch. Autorefresh
  (opt-in) reloads only when the rev changes.
- `<data_dir>/feeds/self.js` (+ `self-archive.js` past 50 closed) — this machine's tasks
  as a read-only view-model feed, exported by `lib/feeds.py` on every board write.

**`lib/feeds.py` is the single owner of the feed format** — writer, wire form, parser,
and loader. A feed is a `.js` sidecar (`window.__TSFEED_<alias> = {json};` + a registry
push), which is `file://`-safe and server-parseable from the same bytes. One root,
`<data_dir>/feeds/`: `self.js`, `self-archive.js`, `peers/*.js` (what the two-machine
sync transport will deliver — J-track, not built yet), `demo/*.js`
(`tools/seed_demo.py` fixtures, how peer rendering is exercised meanwhile; peers load
before demo). Machine-local fields (`prompts`, `resume`) are tagged `local_only` and
stripped by `feeds.strip_local_only()` on any share/sync export (`sync_safe`, plus
per-task `trail_visibility`). `feeds._pr_signal_id` is the frozen F6 cross-link join key
— `lib/artifacts.py` must agree with it.

**Interbrain federation is IN the board**, gated by `config --interbrain`
(`on` · `off` · `auto`; auto → on when brains.json has >1 brain or any peer feed exists).
When on, peer/org feeds are parsed server-side and rendered through the SAME row/section
builders as local tasks — read-only foreign rows (owner chip, 🔒, memo-only) plus foreign
graph nodes and dashed cross-brain signal edges. When off, the render is byte-parity with
the pre-federation board and the help panel carries one dim line saying federation is off
and how to enable it. Brains & sharing live in the `brains.json` sidecar
(`lib/brains.py`) — the store schema is never touched.

**The knowledge plane is also IN the board**, gated by `config --knowledge-plane`
(`on` · `off` · `auto`; auto → on when a vault is configured and it yields a note).
`lib/knowledge.py` is the single owner of vault *reading* — the twin of `obsidian_sync.py`,
which only ever writes — and hands the board ONE GLOBAL CORPUS of the whole vault on every
render, never a per-task slice. `build_render_graph(…, notes=…)` folds that corpus into the
same graph as a second set of nodes, and the 3D canvas draws it as a literal **second plane
stacked above the task sphere**, with a camera pan between the two (`BOARD-BEHAVIOR.md`
B15). Exactly three edge kinds may cross the gap — `cites`, `distilled-from`, `references` —
and both the server (`_crosses_gap` in `render_board.py`) and the canvas (`XPLANE` in
`_MG_ENHANCE_JS`) refuse any other. Placement is closed-form (`_knowledge_layout`), so the
notes are pinned and the frame cost stays flat. With no vault the switch resolves off and
the graph panel is byte-identical to the single-plane one; nothing on this path writes into
a vault (that is `--knowledge-graph`, a different switch entirely).

The board stamps `<meta name="ts-board-version">` and honors the refuse-downgrade guard
(`_semver_gt` on the passive/Stop-hook path; an explicit write always writes; a refused
write touches neither `board.html` nor the feeds).

`docs/specs/BOARD-BEHAVIOR.md` is the behavioral law; `SHARING-NOTES.md` documents the
sharing/sync seams; `BOARD-RETIREMENT.md` records the retired preview engine.

## (b) Hooks

Declared in `hooks/hooks.json`. Every hook no-ops outside plugin context
(`CLAUDE_PLUGIN_ROOT` unset) and **early-exits when `TASK_STATION_SUPPRESS` is set** —
delegate spawns workers with `TASK_STATION_SUPPRESS=1` because task tracking and tinting are
the *hub's* job, not the worker's.

The hooks depend only on `python3` (Task Station's sole hard requirement) — **no `jq`**. They
parse their JSON stdin via `lib/hookjson.py` (`<stdin> | python3 hookjson.py <dotted.path>
[default]`), a silent no-op on malformed input that mirrors `jq -r '.path // default'`; the
`SessionStart` output JSON is built with an inline `python3 -c … json.dumps` one-liner.

| Hook | Script | What it does |
|---|---|---|
| `SessionStart` | `on_session_start.sh` | Refresh the `~/.claude/task-station-engine` symlink → the active `lib/`; self-register the status-line segment; (opt-in) install bare `/todo` `/done` `/repos` aliases; emit the open-tasks / attached-task context + one-time setup nudge; set the session title; **tint the originating window** to the attached task's category (`session-tint`). |
| `UserPromptSubmit` | `on_user_prompt.sh` | Re-point the engine symlink (so bare aliases track an in-session `/plugin update`); **tint instantly when a known skill runs** (`prompt-tint` → escape written to the origin TTY); auto-title the tab `#<seq>: <title>`; inject the compact track-or-fold guidance. |
| `PostToolUse` (`Write\|Edit\|NotebookEdit`) | `on_post_tool.sh` | Attached session → auto-promote `open → active`; untracked session → a **one-shot** reminder the first time it edits a file (gated by the `edited` marker, ~one injection per session). |
| `Stop` | `on_stop.sh` | Refuse to end the turn while the session has edited files but tracked no task (`{"decision":"block"}`). Self-healing (attach/create/skip/`/done` clears it) and **capped at two blocks** so a non-complying loop can't wedge the session. Then `lib/stop_steps.py` runs the seven best-effort turn-end steps (nudge, board refresh, obsidian/usage flush, subscriptions, recap, cost HUD) in **one** interpreter — `stop-gate` keeps its own process because the harness reads its stdout for the block contract. |
| `SessionEnd` | `on_session_end.sh` | The **exact** end-of-session pass (`session-end`): stamp this session's roster row with `ended_ts` + `end_reason`, put one `session-end` event on the attached task's feed, and stop the delegate workers **this** session spawned. Idempotent, always exits 0. |
| `ConfigChange` (`user_settings\|project_settings\|local_settings`) | `on_config_change.sh` | Before a settings change takes effect, check the **paths it declares** (`config-change`). WARN by default — one hook-health record, exit 0; `config_change_enforce` turns it into a **block** (exit 2). |
| `FileChanged` (`config.json\|categories.json\|repos.json\|brains.json\|workers.json`) | `on_file_changed.sh` | A station config file changed on disk → drop the attached task's **checker gate** (`file-changed`) so the pointer/drift nags re-evaluate against the new config at the next session start. Prints nothing. |

**Not in the manifest: `WorktreeCreate`** (`hooks/on_worktree_create.sh`, CLI
`worktree-create`) — installed only on request, into the user's own `settings.json`, by
`task-station config --worktree-hook on`. That hook **replaces** worktree creation, so it
ships opt-in; see [Worktree provisioner](#worktree-provisioner-opt-in) below.

The `PostToolUse` + `Stop` pair is the optional enforcement gate; the others are the
advisory rail. See [README → Commands & components](../README.md#commands--components).

### The four event contracts the newer hooks depend on

Each is a property of Claude Code, not of this plugin, and each shapes the design above:

- **`SessionEnd`** — fires on a clean end; the matcher is *why*
  (`clear|resume|logout|prompt_input_exit|bypass_permissions_disabled|other`), carried in
  `session_end_reason` (some builds send `reason`; the hook reads both). It **cannot
  block**, and **all SessionEnd hooks share a 1.5-second budget** — ours raises the
  ceiling with a per-hook `"timeout": 10` and still aims to finish in under 2s. It is
  **not guaranteed on a crash or kill**, which is exactly why the SessionStart orphan
  sweep stays as the crash backstop (see below).
- **`ConfigChange`** — fires *before* the change takes effect; the matcher is the source
  (`user_settings|project_settings|local_settings|policy_settings|skills`).
  `policy_settings` **cannot be blocked** and is deliberately not wired; `skills` is out
  of scope. A block (**exit 2**) surfaces **no transcript message at all**, so the
  hook-health record is written *first* — it is the only trace the user gets.
- **`FileChanged`** — fires *after* a watched file changes, external editors included,
  once per file, and **cannot block**. Its matcher is a **literal filename list, not a
  regex**: letters, digits, `_`, `.` and `|` keep it literal, while a hyphen, space or
  comma silently flips it to regex parsing and the hook stops matching. It is
  basename-level, so every project's `config.json` fires it — `cmd_file_changed` filters
  on the full path being inside `paths.data_dir()`.
- **`WorktreeCreate`** — **replaces** creation: the hook must create the worktree itself
  and print its **absolute path as the first stdout line**, and any non-zero exit fails
  the creation. No matcher.

(`TaskCompleted` is the harness's own task list, not task-station's steps — recorded here
so nobody re-proposes wiring it.)

### SessionEnd + the orphan sweep

The `SessionEnd` reaper and the `SessionStart` orphan sweep are one pair, and this
**amends decision 36's W2**, which was taken when SessionEnd could not be relied on:

- **SessionEnd is the exact path.** The session is ending cleanly and knows its own id,
  so it reaps the workers *it* spawned (`spawner == this session` in the delegate
  registry) through the same airtight `delegate.reap_task_workers` predicate the close
  path uses — registry-registered for that seq, `role == worker` in the roster,
  task-station-named, not busy, not this session.
- **The SessionStart sweep is the crash backstop, untouched.** SessionEnd does not fire
  on a crash or a kill, so removing the sweep would drop the only case it exists for.

They are idempotent against each other by construction: the sweep only considers a worker
whose spawner is *not* live, and a worker the exact pass already reaped is gone from
`claude agents --json` before the sweep looks.

### Worktree provisioner (opt-in)

`config --worktree-hook on` writes **one** `WorktreeCreate` entry into the user's
`settings.json`, pointing at the stable engine path
(`<config>/task-station-engine/../hooks/on_worktree_create.sh` — the `..` must survive
un-normalised, since `task-station-engine` symlinks to the active `lib/`). `off` removes
exactly that entry and only the scaffolding the install created; a **foreign**
`WorktreeCreate` entry is a refusal, because two hooks racing to create a worktree and
print a path is not a composition. Same consented-installer discipline as the statusline
and sandbox installers (backup, atomic write, manifest-recorded).

`lib/worktree_hook.py` then does the work: `git worktree add` (local branch → checkout,
`origin/<branch>` → tracking branch, else a new branch from `base_ref`/`HEAD` — **never a
fetch**, this module makes no network calls), print the path, then **best-effort**
provisioning: copy the main checkout's gitignored `.claude/settings.local.json` (the
worker tool grants a headless worker cannot prompt for) and add the worktree's
`hasTrustDialogAccepted` entry to `~/.claude.json` via read-modify-write + atomic replace.
The **only** non-zero exit is a genuine `git worktree add` failure; no provisioning step
may change the exit code or touch stdout.

**Hook health.** A hook must never fail or slow a session, so every call it makes is
masked — which used to mean a permanently-broken call was also invisible. The hooks
source `hooks/_ts_lib.sh` and route their maskable calls through `ts_run <label> …`
(stdout discarded) or `ts_capture <label> …` (stdout passed through, for `x=$(…)` sites).
Both keep the old non-fatal contract — they always return success — but a non-zero exit
appends one line (`<iso utc>\t<label>\t<exit code>\t<last stderr line>`) to
`<data_dir>/logs/hook-health.log`, capped at `TS_HOOK_LOG_MAX` (200) lines. `lib/hook_health.py`
reads it: `SessionStart` emits one self-capping nag line while the log holds failures from
the last 24h, and `task-station hook-health [--clear]` is the human view. A few sites stay
masked on purpose and say so inline — heredoc/TTY writes that can't be an argv command, and
predicates like `readlink` or `origin-tty.sh` whose non-zero exit is normal control flow.

Two hooks are deliberately **not** fully masked, and each says so inline:
`on_config_change.sh` (its exit code **is** the contract — only `2` is honoured as a
block, anything else is recorded and swallowed) and `on_worktree_create.sh` (it `exec`s
the engine, so stdout and the exit status pass through untouched).

There are two WRITERS for that one log, in the same format and under the same cap:
`_ts_lib.sh::ts_health_record` for call sites that really are separate processes, and
`hook_health.record()` for the Stop steps that now run inside `lib/stop_steps.py`. A step
there that raises is caught, recorded under the SAME label `ts_run` used, and the remaining
steps still run — the shell's per-step isolation, kept after the calls were merged.

**Exit code 0 is the INFORMATIONAL record.** `file-changed` and `worktree-create` report
what they *did*, not that they broke. Those lines stay in the log and in
`task-station hook-health`, and `hook_health.nag()` alone skips them — a line whose whole
sentence is "N hook failure(s)" must never be triggered by a routine config edit.

## (c) Resume logic

`resume_command(task, current_session)` hands back a `cd <dir> && claude --resume <sid>`
one-liner for the session that actually holds the task's context. The guarantees:

- **Only this task's own sessions.** Every hub shares the `~/.claude/projects` bucket, so a
  whole-bucket fallback or `claude --continue` could resume an *unrelated* task — we never
  do that. Resume only ever targets a session recorded on this task.
- **cwd self-corrected from the transcript.** The resume directory is read from the
  transcript itself (`_session_cwd` after locating it via `_find_session_path`), not from
  whatever cwd `/todo` happened to capture — so a session launched from `~` but worked in a
  worktree still resumes in the right place.
- **Never taint the current session.** The conversation you jumped *from* is excluded hard;
  resuming it is the tainting bug we avoid.
- **Substance floor.** Among live transcripts it prefers the most recent with
  `≥ SUBSTANCE_FLOOR (3)` user messages, so a 1–2 message `/todo <n>` peek never displaces
  the real working session; only if none clear the floor does it take the most recent of any.
- **Pinning.** A `pinned_session` wins PK-style (resume that exact session, cwd
  self-corrected); a `pin --new` preborn pin with no transcript yet is honoured by emitting
  `--session-id <pin>` so the window that opens *becomes* that session.
- **Fresh fallback.** No findable live transcript → `cd <cwd> && claude` (fresh), **never
  `--continue`**.

**`-s` jump window** (`_open_jump_window` → `open-session-window.sh`): macOS/Terminal.app-only
and best-effort — it opens a **new** window running the resume one-liner and leaves the
current window untouched; any failure (not darwin, missing `osascript`, absent script) just
prints the command for you to run by hand.

## (d) "Fold don't fork" dedup

`create` resists spawning a near-duplicate of an existing **open** task. For each open
candidate it scores the normalised title tokens of the new title against the candidate:

```
score = max( jaccard = |A∩B| / |A∪B|,
             containment = |A∩B| / |A|   (only when |A∩B| ≥ 2) )
match when score ≥ 0.6
```

A **numeric-identity guard** runs first: if the new title carries numbers (a PR/bug/story #
via `_norm_nums`) and a candidate shares *none* of them, they're different work items and the
candidate is skipped — so "Auto-review PR 697" can't collide with an unrelated "Auto-review
PR 412" on the process words alone. On a match, `create` points you at `attach` instead;
`--force` overrides.

## (e) Categories & full-palette tint

Twelve slots in `categories.py`, each `{dot, tag, label}` plus a baked **Sands** palette:
`hex`/`hex_light`, `fg`, `bold`, `cursor`, `selbg`, and a 16-element `ansi` list.
`PERMANENT = black` (GENERAL can never be disabled). Presets `minimal/web/data/ops/full`
trim the enabled set; `SKILL_COLORS` maps a skill name → category for instant tinting.

**Terminal detection** (`term.detect()`): env-based, no tty round-trip. Returns `iterm`
(iTerm2 — standard OSC **plus** its OSC-1337 `SetColors` bold and a tab tint), `terminal`
(Apple Terminal.app — standard OSC), `osc` (any other xterm-compatible terminal that honours
OSC 11 — WezTerm, VS Code, Ghostty, Windows Terminal `$WT_SESSION`, kitty `$KITTY_WINDOW_ID`,
Alacritty `$ALACRITTY_WINDOW_ID`/`$ALACRITTY_SOCKET`, and an `xterm*`/`*-256color` `$TERM`
fallback), or `none` (no positive signal → stay quiet rather than spray OSC at a dumb pipe).
`$TASK_STATION_TERM` (`iterm`/`terminal`/`osc`/`none`) overrides detection.

`tint_escape(color, term)` emits standard OSC for **every** tinting terminal — the
`SetColors=bold` extra is iTerm-only, and `mode` is vestigial (profile mode was removed in
1.7.0) and ignored:

| Element | Escape |
|---|---|
| background | `ESC ] 11 ; <hex> BEL` |
| foreground | `ESC ] 10 ; <hex> BEL` |
| cursor | `ESC ] 12 ; <hex> BEL` |
| ANSI 0–15 | `ESC ] 4 ; <n> ; <hex> BEL` |
| selection | `ESC ] 17 ; <hex> BEL` |
| bold (iTerm only) | `ESC ] 1337 ; SetColors=bold=<hexNoHash> BEL` |

`term == "none"` or an unknown colour yields `""`; a slot that defines only a background
still emits just the background (back-compat for minimal taxonomies). When `$TMUX` is set the
whole escape is wrapped in tmux's DCS passthrough (`term.tmux_wrap`: `ESC P tmux ; <body,
each ESC doubled> ESC \`) so it reaches the real terminal instead of being eaten by the pane
— requires `tmux set -g allow-passthrough on`.

**Window open/close is macOS-only.** The `-s` jump window (`_open_jump_window` →
`open-session-window.sh`) and the `/done` auto-close (`close-session-window.sh`) drive
Terminal.app / iTerm2 via AppleScript and are `darwin`-gated; off macOS they degrade to a
one-line "run this yourself" hint (jump) or a silent no-op (close), never an error. Tint and
window-title OSC are plain escapes and work anywhere a terminal is detected.

**Targeting the right window.** The hooks don't print escapes to stdout — they resolve the
*originating* TTY via `origin-tty.sh` and write there, so tinting is focus-proof.
Resolution order: `$CLAUDE_TTY` (export it in your shell rc — the most reliable) → on iTerm,
the session UUID in `$TERM_SESSION_ID` mapped to its `tty` via `osascript`.

**Overrides survive updates.** `_apply_overrides()` merges `config.json`'s `categories` over
the shipped defaults at import, so customisations outlive `/plugin update`. An override needs
only `{tag, label}` — the `dot` and the **full palette** (`fg`/`bold`/`cursor`/`ansi`/…) are
inherited from the slot; an explicit `dot` still wins; a brand-new key with no slot falls
back to the GENERAL dot. **Dark/light:** `hex_light` + `resolve_theme()` (`auto` follows the
OS via `defaults read -g AppleInterfaceStyle`, else a forced `dark`/`light`); the shipped
Sands palettes are theme-independent (`hex == hex_light`), so the setting mainly affects your
own `hex_light` overrides. See [README → Categories & terminal tint](../README.md#categories--terminal-tint).

## (f) In-project delegation

A hub session launched from `~` can't load a repo's `CLAUDE.md`, hooks, MCP servers, or
permissions. `lib/delegate/delegate.py` spawns a `claude` worker *inside* the target repo:

```
delegate.py run --repo <path>|--project <name> --task "<instructions>" \
  [--worktree NAME] [--branch BR] [--base REF] [--seq N] [--solo] [--label L] [--fresh] [--timeout S] [--harness claude|codex]
```

Spawn is behind a `HarnessAdapter` (`lib/delegate/harness.py`) with capability flags
(`supports_bg` / `supports_agent_view` / `supports_named_sessions`); everything
Agent-View-specific is gated on them, and the registry + task record stay authoritative
regardless of harness (task #463).

- **Agent-View background agent (claude, `supports_bg=True`).** A `claude` worker spawns as
  `claude --bg`: a **detached** agent that survives the hub dying AND appears as a row in
  Claude Code's Agent View (attach to inspect it live). `--bg` mints + prints its own session
  id (`--session-id` is ignored), and gives no stdout stream, so liveness/exit are polled from
  `claude agents --json` (`run_worker_bg` / `_classify_exit_bg`: `idle`=ok, unlisted-before-idle
  =crash, wall-clock watchdog=timeout, killing the agents-row process group). A worker spawns
  fail-closed with `--permission-mode dontAsk` + `--allowedTools` (author-only edit toolset):
  non-allowlisted tools are auto-denied so it never blocks, with no `--dangerously-skip-permissions`
  (like the old `-p` workers). `bypassPermissions` is opt-in only (config
  `delegate_bypass_permissions`, default off; needs the one-time disclaimer), worktree-enforced.
- **Author-only, so git is the hub's job.** Because the toolset above is author-only and
  fail-closed, a worker **cannot** run `git add`/`commit`/`push` — those are auto-denied, and
  a headless worker can't raise an approval prompt to change that. So a worker finishing a
  worktree task normally leaves its work **uncommitted**: that is the intended handoff, not a
  worker malfunction, and briefing a worker to "commit your work" asks for something it is
  structurally unable to do. The orchestrating hub commits (and pushes) afterwards. Because
  that hand-off is silent by nature, a **clean** finish that left a dirty worktree reports the
  count on every surface — `status` renders `finished (ok — <n> UNCOMMITTED)`, `delegate run`
  relays a `!!` banner naming the directory, and the task feed records it. It only *reports*:
  nothing is auto-committed on a clean finish, since a brief may legitimately forbid
  committing. (An *abnormal* exit is different — `_wip_commit` has already checkpointed it.)
- **Detached fallback (codex, `supports_bg=False`).** `--harness codex` runs `codex exec --json`
  as a detached NDJSON-streaming worker (the pre-bg spawn model) — no Agent-View row, so
  task-station renders its own board; tokens are recorded **unpriced** (no codex rate sheet).
- **Worktree-isolated.** With `--worktree NAME` the worker runs in
  `<repo>-worktrees/<NAME>`, resolved-or-created on the fly by `worktree-up.sh` off the
  repo's **default branch** (`git symbolic-ref refs/remotes/origin/HEAD`, else `origin/main`).
  Mutations never touch your main checkout; `--project` deliberately refuses to resolve into
  a `*-worktrees` dir.
- **Crash-safe.** The worker's session id is **registered in `workers.json` right after the
  launch print** (before real work), so a timeout or kill never loses the conversation — the
  next call resumes the same session via the same `--seq`/`--project`, and another hub can
  re-adopt a still-running/finished worker it finds in Agent View.
- **Roster + ledger.** Each hub session gets an ordinal `<seq>-<n>` on the task record; every
  spawn/resume/finish/crash/timeout is appended to an unbounded provenance **ledger** so any
  hub sees the full history of every worker (`add-ledger` / `register-worker-session` CLIs).
- **One worker per (task, repo).** The registry is keyed so a re-delegation resumes rather
  than forks; resume one-liners + the worker roster surface in the task's detail view.
- **Lifecycle notifications (opt-in).** `notify_event()` fires once at the end of a run —
  `worker_finished` on success, `worker_failed` on a timeout or non-zero exit — on two
  independent channels: a macOS `osascript` banner (gated by `config --notify on`, darwin
  only, spawned detached via `subprocess.Popen`) and a webhook `POST` (whenever
  `config --notify-webhook <url>` is set) with a JSON body
  `{event, task_seq, repo, label, worktree, cost_usd, ts}` via `urllib.request` (3s timeout).
  **Wholly best-effort:** each channel is separately guarded and every failure is swallowed
  (webhook errors go to stderr), so a missing `osascript`, a broken config, or a dead webhook
  can never break a delegation. Config lives in `lib/config.py`
  (`notify_enabled()` / `notify_webhook()`, env overrides `TASK_STATION_NOTIFY` /
  `TASK_STATION_NOTIFY_WEBHOOK`).

## (g) Repo index

`/repos` (`repo_index.py`) builds a hub-side index used by delegate's `--project` shorthand.
Discovery and stack detection are **fully offline and deterministic**: a content-based scan
maps file extensions/filenames → stack labels via `stack_map.py` (a generated, import-free
module distilled from the curated top-~40-stack table in `tools/gen_stack_map.py`).
`summary` + `keywords` are computed on-device by default.

LLM enrichment is the **only** egress path and is **off by default per repo**
(`manifest[name] = {index: true, enrich: false}`). Even when you opt a repo in with
`/repos enrich <name>` it is tightly bounded:

- **Fingerprint-gated** — `sha1(remote + sorted top-level entries + …)`; re-sent only when
  new or structurally changed, not on ordinary commits.
- **Name-only** — a bounded README excerpt + top-level file/dir **names**; arbitrary file
  *contents* are never read, and a **secret-name denylist** guards what's sent.
- **Kill-switches** — `--no-llm` (force deterministic), `--dry-run` (report, send nothing),
  and `TASK_STATION_REPO_ENRICH=off` / `repo_enrich:false` (hard-disable all egress). The
  index **always** builds deterministically; enrichment is a layer on top that never errors
  the build. See [README → Data & privacy](../README.md#data--privacy) and
  [PRIVACY.md](../PRIVACY.md).

## (h) Desktop MCP bridge

`lib/mcp_server.py` is a **dependency-free, hand-rolled MCP server** — stdio JSON-RPC built
from `json` + `sys` only (no SDK, no `pip`) — that exposes the task store to Claude Desktop
over the **same local `tasks.db`** the CLI uses (WAL makes concurrent Desktop + CLI access
safe). Six tools, plus a `todo` prompt and `task://<seq>` resources:

| Tool | Purpose |
|---|---|
| `list_tasks` | The board Chat sees (also drives the `todo` prompt). |
| `search_tasks` | Ranked full-text search (tier-1 hit list); reuses the engine's `_search_core`/`_format_search`. |
| `create_task` | Create from a Desktop chat (stores the conversation ref). |
| `get_task` | Full detail for a `seq`/id (includes the time/cost stats line). |
| `set_status` | Move `open ⇄ active`/close. |
| `add_note` | Append to the activity log. |

`lib/mcp_launcher.py` is the **stable, self-resolving launcher** copied to
`<data_dir>/mcp-launcher.py` by `config --desktop-bridge on`. Desktop is pointed at *this*
version-independent path — never the volatile engine symlink — and on every launch it
resolves the installed `mcp_server.py` (`installed_plugins.json → installPath`, else the
highest cache version) and `os.execv`s it, passing stdio straight through. That's what keeps
Desktop working across `/plugin update` and concurrent sessions. `setup.py` merges a single
entry into Claude Desktop's config (backed up first) and is fully reversible
(`--desktop-bridge off`). See [README → Claude Desktop bridge](../README.md#claude-desktop-bridge).

## (i) Reversible `CLAUDE.md` policy block

`config --policy on` (`setup.py`) writes a delegation-policy block into your global
`~/.claude/CLAUDE.md`, fenced by sentinel comments:

```
<!-- BEGIN task-station:delegation-policy (managed — task-station config --policy) -->
…policy text…
<!-- END task-station:delegation-policy -->
```

The write is **add-or-replace and idempotent**, always takes a `.bak` first, and records the
exact inserted substring plus its `sha256` in the setup manifest. `--policy off` removes
**exactly** that span and restores the file — but it is **hash-verified**: if the block was
hand-edited since install (hash mismatch) removal refuses (a no-op that warns), so the tool
never clobbers your edits. 100% reversible by design.

## (k) Native Tasks interop (read-only)

Claude Code 2.1+ persists in-session **Tasks** as one JSON file per item under
`~/.claude/tasks/<list-uuid>/<n>.json` (shape: `id`, `subject`, `description`,
`status ∈ {completed, pending, in_progress}`, plus `blocks`/`blockedBy` we don't render).
`lib/native_tasks.py` is a **read-only** bridge — Task Station never writes that store, so
native in-session orchestration and the durable cross-session board coexist.

- **Defensive parsing.** `list_native_lists()` walks the root (overridable via
  `TASK_STATION_NATIVE_TASKS_DIR`, which the tests use), skipping malformed/non-JSON/non-dict
  files silently and defaulting missing keys — a single bad file never aborts a listing, and
  an unreadable root just yields `[]`. Nothing here raises into a board render.
- **Recency filter.** A list is surfaced when its dir mtime is within 14 days **or** it still
  holds a non-completed item (so a stale-but-unfinished list stays visible while an ancient
  all-done one drops off). Empty lists are omitted; lists sort newest-first, items by numeric
  id.
- **Surfaces.** `task-station native` / `/todo native` renders one section per recent list
  (short uuid + relative mtime, items as `✓ / ◐ / ○ <id> <subject>`). The terminal board
  footer gains a one-line **NATIVE** pointer **only** when a recent list has open items — a
  pointer, not the dump, to keep the board lean.
- **Adopt.** `task-station adopt --native <list-prefix>:<id>` (`/todo adopt …`) resolves the
  ref against the recent lists and creates a durable station task from the item's
  subject/description (colour `black`/GENERAL, effort `S`), recording provenance
  (`adopted from native task <short>:<id>`) in the summary and activity log. One-way: the
  native store is never mutated.

Positioning: native Tasks are ephemeral, per-session orchestration; Task Station is the
durable console. `adopt` is the deliberate promotion of work worth tracking across sessions.
See [README → Native Tasks](../README.md#native-tasks).
## (j) Obsidian export

`lib/obsidian_sync.py` is an **opt-in, one-way** mirror of tasks into an Obsidian vault. The
store stays authoritative; the module only ever *writes* derived Markdown — it never reads a
note back. It's gated on a single config key, `obsidian_vault` (empty ⇒ off), and every call
from the engine is wrapped so an export failure degrades to a stderr warning and never breaks
the mutation that triggered it.

**Layout.** All plugin-owned files live under one namespaced folder — `<vault>/Claude/task-station/` —
so uninstalling is a folder delete. Per task: `<seq>-<slug>.md` with flat YAML frontmatter
(`managed-by`, `seq`, `status`, `category` label, `effort`, `repos`, `story`, `pr`,
`created`/`updated`/`done` as ISO-local dates, wikilink-safe `title`) and a body of `## Goal`,
`## State`, `## Summary`, `## Decisions`, `## History`.

**Where it hooks.** The engine calls `_obsidian_sync(task)` after the save in `create`,
`_update_one`, and `_todo_save`, and `_obsidian_event(task, "closed")` in the two close paths
(`_close_one`, `cmd_done`). Plain bumps/attaches are deliberately **excluded** as too noisy.
`task-station obsidian --sync-all` rewrites every task; `--status` reports configuration.

**Sandbox drift + `obsidian_dirty` (mirrors `digest_dirty`).** Most mutations run inside a
*sandboxed* Claude Code session; when the vault sits under a macOS TCC-protected root
(`~/Documents`, `~/Desktop`, `~/Downloads`, `~/Library/Mobile Documents`), the atomic write is
denied (`os.replace` → `EPERM`) even though an unsandboxed shell can write there. Left
unhandled the vault silently drifts stale. So `_obsidian_sync` now, while a vault **is**
configured, mirrors the digest-staleness design exactly: a **failed** export sets
`obsidian_dirty` on the task and persists it (via a direct `save_task`, which does *not*
re-trigger the export hook — same non-looping discipline as `clear_digest_dirty`); a
**successful** export clears it. `mark_obsidian_dirty` / `clear_obsidian_dirty` /
`obsidian_dirty` sit beside their `*_digest_dirty` twins and share their back-compat rule (no
field ⇒ not dirty). `obsidian --status` reports the pending-resync count; `obsidian --flush`
re-exports **only** the dirty tasks and clears their flags (cheaper than `--sync-all`, which
also clears them). `is_protected_vault_path` (in `obsidian_sync.py`) backs both the config-time
warning and the hint's "protected folder" framing.

**Unsandboxed hook auto-flush (Fix B — the zero-config heart).** Claude Code's Bash-tool
sandbox only permits writes under the session cwd + `$TMPDIR`, so a vault in `~/Documents` is
unwritable from a project session — but **hooks run UNSANDBOXED** (same trust level as
monitors). So `on_stop.sh` and `on_session_start.sh` each invoke `task-station obsidian --flush
--quiet` (routed to `/dev/null`; on the Stop side that call now runs inside `lib/stop_steps.py`
with the other turn-end steps, one interpreter instead of seven), which re-exports the dirty
tasks from outside the sandbox and succeeds where the hot path couldn't. This is what makes a protected-folder vault work with no
configuration. The Stop-hook flush is **independent** of the stop-gate — it never touches the
gate decision or delays the turn — and both are suppressed inside delegate workers by the
existing `TASK_STATION_SUPPRESS` early-exit. `--quiet` self-gates (no vault / nothing dirty ⇒
instant silent no-op, no work spawned) and stays silent on success so the happy path is quiet.

**Hint reconciliation.** Because Fix B auto-heals a sandbox-denied export seconds later, the
mid-turn hot-path failure is now **silent** (it only marks dirty). The loud, actionable hint
fires **only** on a *persistent* failure — a task STILL dirty after a hook flush also failed —
gated on a per-task `obsidian_flush_failed` signal set by `_flush_obsidian_dirty` (and by the
vault-gone branch). The next mutation on such a task emits **one** deduped hint (via the
`paths.data_dir()/.obsidian-perm-warned` marker, cleared on the next success so a future
breakage re-warns); a manual `obsidian --flush` prints the same remedy text inline. Remedies:
grant Full Disk access, relocate the vault, `--obsidian-sandbox on`, or `--flush`/`--sync-all`.

**Instant inline exports (Fix A — `setup.install_sandbox_allowwrite`).** A plugin cannot ship
sandbox config (Claude Code honours only `agent`/`subagentStatusLine` from a plugin), so the
user's own `~/.claude/settings.json` must carry it. `task-station config --obsidian-sandbox on`
adds the configured vault to `sandbox.filesystem.allowWrite` (read-merge-write **atomically**
via `setup._write_settings`, backed up first), creating the `sandbox`/`filesystem`/`allowWrite`
scaffolding only as needed and never touching other keys — crucially **not** `sandbox.enabled`
(we add a path, we don't force the sandbox on). The `setup-manifest.json` records the exact
entry AND which nested keys we created (`{"entry", "created"}`), so `--obsidian-sandbox off`
strips precisely our entry and only the empty scaffolding we introduced, leaving a user's
`enabled`/other `allowWrite` paths intact. With the path allowlisted the sandboxed hot-path
export writes instantly; without it, Fix B alone still keeps the vault correct (graceful
degradation). The config board shows an `--obsidian-sandbox` row and `config --reset` lists the
allowlist entry as a deliberate-removal leftover.

**Stability + safety invariants.**
- *Atomic writes* — temp file in the same dir + `os.replace`, so a reader never sees a partial
  note (mirrors the store's `_atomic_write`).
- *Stable filenames* — a sidecar `.task-station-index.json` (task id → filename) is consulted
  first, so renaming a task keeps its original note file instead of orphaning it.
- *Managed Bases view* — `Task Board.base` is written **once** and never overwritten; an
  existing file is treated as user-owned, so edits survive a resync.
- *Daily note (opt-in)* — on close and `/todo save`, `append_daily_note` inserts one dated line
  under a configurable heading in `<vault>/<YYYY-MM-DD>.md`, creating the file/heading if
  absent. The read-modify-write window is short and ends in an atomic replace; the insert is
  **idempotent per event** (an identical line already present is not duplicated). Not
  conflict-aware — a vault double-synced (iCloud + Obsidian Sync) can still lose a write, but
  `--sync-all` repairs the vault from the authoritative store.
