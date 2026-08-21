# heal.py
"""The RECONCILE pass — turning a task's append-only decision log into current state.

WHY THIS EXISTS. task-station has always had capture (`save`) and no reconcile. The
log only ever grew, the digest truncated it by AGE, and nothing ever said "these
sixteen entries are now four". Measured on one real task: 72 decisions, 68 of them
still current, ~96,000 characters — about 24k tokens of resume context, average 1,351
chars per decision. The longest single decision was 27,707 chars, and it was that long
BECAUSE the digest truncated by age: its author front-loaded everything into one entry
hoping it would land inside the visible window. Truncation manufactured the bloat it
then hid.

Truncation is now GONE — every still-current decision renders (`decisions.digest_order`)
— which RAISES the stakes for this module rather than lowering them. Reconcile used to
compete with a recency window that hid the mess; now the digest shows exactly what the
log contains, so keeping it honest is the only thing standing between a resumed session
and 96,000 characters. The write path nudges toward that with an advisory at 600 chars
(`decisions.length_warning`) that never refuses.

Supersession (2.9.0) fixes only the case where something was WRONG. A manual sweep of
that task found two more shapes it cannot express, which is why `decisions.py` now has
three verbs and this module drives them:

  * SPLIT — a COMPOUND decision mixing still-valid rulings with refuted ones. It
    cannot be superseded without destroying the good content, and a 27,707-char entry
    is unreadable regardless of correctness.
  * MERGE — decisions that are TRUE but no longer load-bearing: four separate release
    records, seven steps of one scrub, a chain of process-error corrections. Nothing
    refuted them; they simply stopped earning digest space, and calling them "wrong"
    would put a lie in the record.

TWO LAYERS, deliberately split by cost:

  LAYER 1 — `scan()`. Deterministic, zero tokens, and it NEVER mutates the task. TEN
  checks (see CHECKS — nine findings plus the health metric, which is a measurement
  rather than a finding) that find what a reconcile would have to look at. `heal --scan`
  and the SessionStart nag both run it and write the result to a per-task GATE FILE.
  Modelled on `hook_health.py`: everything fails open, an unreadable gate means no
  nag, and a broken report must never be worse than the silence it replaced.

  LAYER 2 — `plan()` + `apply()`. The plan is the MECHANICAL subset a machine can do
  correctly on its own: split an oversized decision on its own paragraph boundaries,
  collapse a detected cluster, retro-dispose an ack recorded before dispositions
  existed. Everything needing judgment (which half of a compound decision was wrong,
  whether prose that says "no longer" really supersedes entry 8, what the `state` line
  should now say) is REPORTED for the LLM pass and never guessed at.

WHAT A REAL RECONCILE RUN EXPOSED (and this module now covers):

  * A HEAL HAS TO STAMP ITSELF. A task reconciled by 17 merges, 5 supersedes and a
    split still reported `last heal never`, because nothing recorded that a pass had
    happened. So "heal due?" was permanently YES and "N new decisions since the last
    heal" was the task's whole decision total — an alarm that is always on, which
    trains the reader to ignore the one signal built to be trusted. An `--apply` that
    performed work now stamps (`stamp_healed`), and `--mark-healed` records the
    judgement-only pass where nothing needed changing. `--scan` never stamps: it is
    read-only, and that is its whole contract.
  * …BUT ONLY A HEAL THAT HAPPENED. The first version stamped a bare `--apply` that
    performed ZERO operations, reasoning that running it asserts the task was
    reconciled. It is instead the exact command someone types when they assume
    `--apply` IS the heal, and it wrote `last heal <just now>` onto a task nobody had
    touched. A stamp that is sometimes a lie is worse than an alarm that is always on,
    because it makes every OTHER stamp unreadable too. A zero-operation `--apply` is now
    REFUSED, naming the two honest moves: pass operations, or `--mark-healed --note`.
  * AN EPHEMERAL PATH IS NOT DRIFT. On one real task the drift check reported seven
    findings and all seven were worker briefs under a session `scratchpad/` directory
    that task-station had auto-captured. The scratchpad is wiped when the session ends
    BY DESIGN, so "the digest points a resumed session somewhere it cannot go" was true
    and useless — nobody resumes by opening a brief out of a deleted temp directory.
    `ephemeral_path` excludes those; `vanished_ephemeral` counts them on one line.
  * A REPORT MUST NOT REPRINT WHAT THE READER JUST READ. `--apply` re-rendered the whole
    dry run — scan block, judgment list, every current decision — of which ~94% is the
    decision list. On a 40-decision task that is ~47,000 characters, so the obvious
    two-step (`heal`, then `heal --apply`) paid for it TWICE for one heal. `--apply` now
    prints only what it DID; `--apply --verbose` restores the full dump.
  * ACKS RECORDED BEFORE 2.9.0 NEED A RETRO-DISPOSITION, and it must be VISIBLY
    retroactive — see `retro_disposition`. The original ack's `sid` and `ts` are never
    touched; the reconciler's name, the moment, and the why go alongside, marked.
  * STEPS NEEDED A VERB TOO. `steps.mark_superseded` (`update --step-supersede <n>`)
    is the honest exit for a stale step; the stale-step check names it instead of
    saying "report only".
  * A KEYWORD IS NOT A FINDING. After the full reconcile above, the ONLY findings left
    were two false ones — a step saying "delete stale tracked BRIEF-….md" and the
    CORRECTED step saying "the names in the superseded ancestor are REJECTED". Both
    describe something else; neither is stale. That put `Heal due? YES` back on a task
    with nothing to do, so the stale-step check now shares check 3's discrimination
    (`qualifier` / `declaring_hits`) instead of matching the vocabulary alone.
  * DECLARE vs DESCRIBE IS THE RULE, NOT A PATCH. The same bug shipped FOUR times in
    this subsystem — the drift check scraped branch names out of English prose, the
    prose-supersession check fired on decisions explaining supersession, the stale-step
    check fired on the step written to FIX staleness, and the memo correction backstop
    fired on a release note and on somebody else's retraction. Every keyword check here
    must therefore answer ONE question before it reports: does this text DECLARE the
    condition, or merely DESCRIBE it? There is one implementation of that question
    (`qualifier` → `declaring_hits`) and every check routes through it; a fifth check
    adds VOCABULARY to it, never a fifth heuristic.
  * THE PASS MUST NAME THE MERGE CANDIDATES, not just ask for them. The judgment list
    says "MERGE what is TRUE BUT NO LONGER LOAD-BEARING" and then left the reconciler to
    find them; on a real 99-decision task a human found all sixteen mechanically, by
    matching leading shapes. `merge_candidates` now offers those groups — as PROPOSALS
    that never count as issues, because choosing the surviving summary is judgment.
  * PINNED DECISIONS BRIEF EVERY SESSION, so stale content in one is the most expensive
    kind there is — and the scan treated them like any other decision. On a real task a
    pinned decision still named two retired codenames and had been briefing every
    session with them for days. `pinned_review` names the pinned set with each one's
    age; informational, like the health metric, because being pinned is not a defect.
  * A CONSOLIDATION CAN COME UNDONE BY ACCRETION. A real task scanned CLEAN on all eight
    checks, and the judgement half then found a decision reading "CONSOLIDATED — THE
    2.7.0-2.11.0 RELEASE LINE … (replaces the five per-release records)" with FOUR more
    release-shaped decisions appended after it over the following day. Nobody undid the
    merge; the shape simply grew back around it, and the record then said two
    contradictory things about how many entries that subject has.
    `refragmented_consolidations` reports it — a FINDING, not a proposal, because a
    previous pass already ruled that the subject has ONE entry and the log now
    contradicts that ruling. It still does not propose the merge: naming the surviving
    summary is judgement, and a wrong merge writes a false consolidation into the record,
    which is the very thing this check exists to catch.
  * AND THE ONE GAP NO SCAN CAN COVER. On that same task a RELEASE had shipped and was
    recorded NOWHERE — no decision, no log entry, no PR link. Nothing contradicted
    anything, because the work had happened entirely OUTSIDE what the record holds, so
    there was nothing to cross-reference. A check for that would be the fifth
    confidently-wrong check this subsystem has shipped, so there is none. Instead
    `accrual` COUNTS what has been recorded since the last heal, and the report says out
    loud, in words, that whether everything which SHIPPED is in there is judgement the
    deterministic layer structurally cannot do. Informational, like the health metric:
    never a finding, never an issue, and it can never make a heal due.
  * THE GOAL AND THE CHECKLIST DRIFT WHERE NOTHING WAS LOOKING. All three verbs here are
    DECISION verbs, and every check reads a decision, a memo, a path or a step's own
    words. Nothing read the GOAL LINE or compared a STEP against a DECISION — so on one
    real task `--scan` reported every check clean and `Heal due? no` while the task held
    a goal describing a mission already accomplished AND five live steps naming the two
    largest work items on it, both by then retired by decisions the task itself had
    SUPERSEDED. Nothing was internally inconsistent, because a decision that was right
    when written and was later refuted by reality leaves nothing to cross-reference; and
    a cold session reads the goal and the checklist FIRST, because that is where the next
    move lives, while the decisions mostly say WHY. So a step restating a superseded
    decision is now a FINDING (`steps_restating_superseded`) — the log already ruled that
    work refuted, and a live step still ordering it is a contradiction — while the goal
    is a PROPOSAL (`goal_review`), because a goal is supposed to outlive the decisions
    that pursue it and an untouched one is a reason to LOOK, never proof of anything.
  * AND `Heal due? no` WAS SAYING MORE THAN IT KNEW. On its own it reads as "this task is
    a complete record" when all it means is "the cross-referencing checks found nothing" —
    the reading that let both incidents above pass for healthy. Every surface now closes
    on `mechanical_line` / `judgment_line` / the verdict, so the half a machine did and
    the half only a reader can do are two separate answers. RENDERING only: `due()` is
    untouched, because the nag, the gate file and `gate_line` all read it.

WHAT THE NEXT PASS ADDED, and the measured cost each one answers:

  * A DISMISSAL LEDGER (`heal --dismiss` / `--undismiss`). The scan re-reported
    adjudicated false positives on EVERY pass. On one real task 17 findings stood, and 9
    of them were dead paths a human had already ruled on — so the report repeated what
    its reader had already answered, which is the cry-wolf failure arriving from a fifth
    direction. A dismissal adjudicates ONE EXACT STATE, never a category: the fingerprint
    covers the finding's matched TEXT (check + ref + detail), so the moment that text
    changes the finding RE-REPORTS. It refuses to record one without a `--why`, because
    an adjudication with no reason is how a real finding gets buried. Nothing is deleted —
    `--undismiss` marks the entry retired and full reporting resumes.
  * MERGE AT SCALE (`heal --candidates`, subject-tier grouping, the size objective). The
    shape tier proposes groups by how each entry OPENS, which is how a human found
    sixteen on one task — and it says nothing about whether the work is FINISHED or what
    the entries are ABOUT. Three additions: a decision whose SUBJECT STEPS are all ticked
    or superseded is tagged `completed-subject` (the work it records is done, so it has
    stopped earning digest space); candidates group by SHARED SUBJECT — overlapping step
    references, a shared release version, a shared PR/story number — which crosses
    entries that open differently and say the same thing; and the heal stamp snapshots the
    digest's char total, so the scan can report `chars now / at last heal / delta` instead
    of a number with nothing to compare it against. A digest that GREW while merge
    candidates sat outstanding is the one new finding in that work; everything else in it
    is proposal-tier, because choosing the surviving summary is judgement.
  * A GOAL REVIEW THAT CAN MAKE A HEAL DUE. `goal_review` counted what had landed since
    the goal was written and could never act on it, so a goal nobody had re-read since
    forty decisions ago produced the same silence as one written this morning. Past
    `heal_goal_review_due` decisions that count IS the due reason, worded as the count.
    RE-READING is the service, so `heal --goal-reviewed` records the re-read and resets
    it WITHOUT requiring a rewrite — and `--mark-healed` deliberately does NOT reset it,
    because a stamp that silently claims the goal was re-read is a stamp that lies.
  * THE OVERSIZED THRESHOLD WAS 2.4× THE ADVISORY IT ANSWERS TO. The write path nudges at
    600 chars and this check reported clean up to 4,000, on a task whose decisions AVERAGE
    ~1,400 — so the number was neither the advisory nor a measurement, just a third
    opinion. Both now derive from ONE constant (`decisions.LONG_DECISION_CHARS`): >2× is a
    PROPOSAL (worst-first, capped, never an issue and never due-making) and >6× is a
    FINDING, which is roughly where the longest real entry sits and is the length past
    which an entry cannot be superseded a piece at a time.
  * THE FIRST OUTWARD CHECK. Every other check cross-references the record with ITSELF,
    which is why a rewritten history leaves nothing to find. Cited commits are now probed
    against the task's own repos — DECLARED citations only (`commit <sha>`, `main @ <sha>`),
    never a bare hex token, because task ids and fingerprints are hex too and a false
    "your commit vanished" costs more than every finding it would ever have made. The
    probe is INJECTED, so the SessionStart path still spawns no subprocess; the link probe
    stays opt-in behind `--probe-links`.
  * AND A THIRD DISCRIMINATOR, because declare-vs-describe shipped incomplete AGAIN. The
    guard reads the word standing in FRONT of a match; it never read WHO the sentence says
    did it. So `corrected by decision 184`, `decision 173 investigated` and `why decision
    150 is NOT superseded` all read as unlinked supersessions — 8 of one real task's 17
    findings were that one shape. See `reports_another_decision`.

SAFETY — this mutates the decision record on a board holding real work:

  * `--dry-run` IS THE DEFAULT. A bare `heal` prints the plan and changes nothing.
  * `--apply` BACKS UP the task blob first, and refuses to run if the backup fails.
  * The `--log` milestone trail and `history` are NEVER touched. They are append-only
    and sacred — history's whole job is to stay complete.
  * NO verb deletes a decision. Every one of them MARKS the original and drops it from
    the default digest while keeping it in `history`, labelled with what replaced it.
    All three are reversible via `decisions.restore`.

Stdlib only. Imports `decisions`, `steps`, `config` and `paths` (all leaves) —
task-station.py imports THIS, never the other way around. `urllib` is imported INSIDE the
link prober rather than at module scope: this module loads on the SessionStart path, and
an import nobody on the cheap path uses is a cost every session pays for nothing.
"""
import hashlib
import json
import os
import re
import subprocess
import time

import config as _config
import decisions as _dec
import paths
import steps as _steps

# -- thresholds (module-level so one edit retunes the whole pass) ----------------

# THE SIZE TIERS, ALL DERIVED FROM ONE NUMBER. `decisions.LONG_DECISION_CHARS` (600) is the
# advisory the WRITE path nudges with, and it is the single source of truth: two constants
# that both answer "how long is too long" drift the first time either is tuned, and this
# check had drifted to 4,000 — 2.4× an advisory it never referenced, on a task whose
# decisions average ~1,400 chars, so it reported clean on almost everything.
#
# TWO TIERS, because one number cannot carry both jobs. Past 2× the advisory an entry is
# worth LOOKING at, and that is all: a real task's average sits between the two, so
# reporting every one of them as a defect would put `Heal due? YES` on a healthy record —
# the exact failure this module has already fixed four times. Past 6× it is a FINDING, and
# not because 6 is a round number: that is roughly where the longest entry on a real task
# sits, and it is the length at which an entry can no longer be superseded a piece at a
# time, because every ruling inside it is welded to every other one.
WRITE_ADVISORY_CHARS = _dec.LONG_DECISION_CHARS     # 600 — the write-time nudge
OVERSIZE_PROPOSAL_CHARS = 2 * WRITE_ADVISORY_CHARS  # 1200 — worth reading, never an issue
OVERSIZE_CHARS = 6 * WRITE_ADVISORY_CHARS           # 3600 — a real finding
OVERSIZE_TOP_N = 5        # proposals shown worst-first; the rest collapse into "+N more"

SPLIT_MAX_PARTS = 8       # cap on mechanically-derived parts; the tail merges into the last
MERGE_MIN_NAMED = 2       # cluster size that merges when a NAMED signature matched
MERGE_MIN_STEM = 3        # cluster size required for the weaker stem signature
STEM_WORDS = 4            # significant leading words that form a stem signature

DUE_NEW_DECISIONS = 10    # new decisions since the last heal that make one due
DUE_AGE_DAYS = 7          # days on an ACTIVE task without a heal that make one due
DUE_AGE = DUE_AGE_DAYS * 86400

# Decisions that may land after the goal line was last WRITTEN or RE-READ before that
# alone makes a heal due (`heal_goal_review_due` overrides it; see `goal_review_due`).
# 25 is deliberately well above DUE_NEW_DECISIONS: ten new decisions mean the log has
# moved, which is a reason to reconcile the LOG; this limb is about one sentence that
# nobody has looked at in a long time, and a goal is SUPPOSED to outlive the decisions
# pursuing it. Firing at ten would make the goal review indistinguishable from the
# accrual limb it sits beside.
GOAL_REVIEW_DUE = 25

GATE_DIR = "heal"                        # <data_dir>/heal/
BACKUP_SUFFIX = ".bak-pre-heal.json"     # sibling-file backup, per this project's `.bak-*` convention
GIT_TIMEOUT = 5                          # seconds; a slow repo probe must never hang a scan

# Prose that CLAIMS a supersession the structure doesn't record. Being present is only
# the FIRST of two conditions — see `prose_supersession`, which also requires the prose
# to name a decision-shaped target. Edit the list HERE.
SUPERSESSION_LANGUAGE = ("SUPERSEDED", "supersedes", "corrected", "was wrong",
                         "no longer", "retracted")

# Staleness language on a STEP. Steps now HAVE a supersede verb
# (`update --step-supersede <n>`), so this names it instead of shrugging. Being present
# is only the FIRST of two conditions — see `stale_steps`, which also requires the step
# to DECLARE the staleness rather than describe someone else's. Edit the list HERE.
STALE_STEP_LANGUAGE = ("STALE", "do not execute", "READ-ME-FIRST", "superseded")

# The FINDING checks, in the order every surface reports them — `(slug, title)`. The
# HEALTH METRIC is deliberately not among them: it is a measurement rather than a finding
# (a task can be perfectly consistent and still be far too big to brief), so it lives in
# `health()` and renders through `health_line()` above the findings.
#
# The last two are the newest, and each one is a different KIND of check from everything
# above it. `cited-commit` is the first OUTWARD one — it asks a question of a git repo
# rather than of the record — and `grew-with-candidates` is the first one about the record
# as a WHOLE rather than about one entry in it.
CHECKS = (
    ("ack-undispositioned", "Undispositioned acks"),
    ("correction-unfulfilled", "Corrections never applied"),
    ("prose-supersession", "Unlinked supersession language"),
    ("oversized", "Oversized decisions"),
    ("drift", "Drift (paths / branches)"),
    ("link-rot", "Link rot"),
    ("stale-step", "Stale steps"),
    ("refragmented", "Re-fragmented consolidations"),
    ("step-restates-superseded", "Steps restating a superseded decision"),
    ("cited-commit", "Cited commits that resolve nowhere"),
    ("grew-with-candidates", "Digest grew with candidates outstanding"),
)
CHECK_ORDER = [c[0] for c in CHECKS]
CHECK_TITLES = dict(CHECKS)


def _finding(check, ref, detail):
    """One scan finding. `ref` names WHERE (decision 8, memo ab12cd34, step 3) and
    `detail` says what is wrong in ordinary words."""
    return {"check": check, "ref": str(ref), "detail": detail}


def matched_language(text, patterns):
    """The `patterns` present in `text`, in list order. Case-insensitive substring
    match — cheap and deliberately over-eager.

    THE FIRST HALF of a language check and never a finding on its own: presence says
    nothing about what the word is ABOUT. Every caller pairs it with a second condition
    (see the guard below), which is the lesson four separate checks here had to learn."""
    low = (text or "").lower()
    return [p for p in patterns if p.lower() in low]


# -- the precision guard EVERY language check SHARES ------------------------------
#
# THE ONE RULE: DOES THE TEXT DECLARE THE CONDITION, OR MERELY DESCRIBE IT?
#
# A KEYWORD ON ITS OWN IS WORTHLESS. `matched_language` says a word is present and
# nothing about what the word is ABOUT, and a task's own record talks about staleness
# and supersession constantly — that is what a reconcile pass writes down — without any
# of those entries being stale. Check 3 learned it on one real task (5 findings, 4
# false) and gained a second condition: read the word standing IN FRONT of the match,
# because that is what says whether the keyword is aimed at the entry itself or at
# something the entry merely mentions.
#
# CHECK 8 SHIPPED WITHOUT THAT CONDITION AND REPRODUCED THE BUG EXACTLY. On the same
# task, after a full heal, the only two findings left were both false:
#
#   step 30  "delete stale tracked BRIEF-….md"                — 'stale' names a FILE
#   step 39  "the names in the superseded ancestor are        — 'superseded' names an
#             REJECTED"                                          ANCESTOR, and this step
#                                                                IS the correction
#
# So `Heal due?` stayed YES on a task with nothing left to do — the false alarm the heal
# stamp was added to kill, re-created by the check that reports the reconcile's own
# output. Both checks therefore go through the ONE discrimination below rather than two
# heuristics for the same problem that drift apart; only the vocabulary differs
# (`NON_DECISION_QUALIFIERS` for check 3, `DECLARING_QUALIFIERS` for check 8).
#
# AND THEN IT HAPPENED A FOURTH TIME. The memo correction-language backstop
# (`task-station.correction_language`) asks this identical question of a memo body — is
# this memo announcing a correction, or talking about one? — and shipped matching the
# vocabulary alone, so it warned on both of these:
#
#   "Shipped 2.13.1: heal now distinguishes a step that     — 'supersede' names an
#    declares itself stale from one that merely mentions       ANCESTOR in a release
#    a superseded ancestor"                                    note, not this memo
#   "FYI the upstream library withdrawn its 3.0 release"    — somebody ELSE's retraction
#
# It now routes through `declaring_hits` too. That is the whole point of this section:
# the question is asked in ONE place, and a new check brings a VOCABULARY to it rather
# than a new heuristic. EIGHT vocabularies exist so far — `NON_DECISION_QUALIFIERS`
# (check 3), `DECLARING_QUALIFIERS` (check 8), the two memo sets below,
# `CONSOLIDATION_QUALIFIERS` (check 9, which asks the same question of a decision
# claiming to be the one record of several: does it DECLARE that, or describe someone
# else's consolidation?), and the three the REPORTS-ANOTHER-DECISION discriminator brings
# (`REPORTING_QUALIFIERS`, `REPORTING_VERBS`, `NEGATING_QUALIFIERS`).
#
# AND THEN A FIFTH TIME, which is why that discriminator exists at all. Reading the word in
# FRONT of a match answers "what noun is this keyword about" and CANNOT answer "who does
# this sentence say did it". `corrected by decision 184` passes the front-word test with
# nothing in front of the keyword at all, and it is a MINUTE of another decision's work
# rather than a claim about this one. Same rule, second question, one implementation:
# `reports_another_decision`, below `decision_refs`.

_WORD_BEFORE = re.compile(r"([A-Za-z][\w.-]*)\s*$")


def qualifier(text, start):
    """The word standing immediately IN FRONT of a match at offset `start`, lowercased
    and stripped of trailing punctuation.

    `""` when nothing qualifies the match — it opens the text, a LINE, or a clause
    (anything that is not a word sits in front of it: a dash, a colon, a comma, a
    bullet, a number). That empty answer is the meaningful one for both callers: a bare
    `#8` is a decision reference, and a `STALE` that opens a step is a declaration."""
    m = _WORD_BEFORE.search((text or "")[:start].rsplit("\n", 1)[-1])
    return m.group(1).lower().strip(".,:;") if m else ""


# Words that may stand in front of a staleness keyword and still leave it a DECLARATION
# about the step itself: a form of "to be" — the keyword is then the PREDICATE, which is
# the step saying something about itself ("this step is stale", "steps 3/4/5 above are
# STALE") — or one of the adverbs that lead one. Anything else is a determiner or a verb
# taking an object, which makes the keyword an ADJECTIVE ON THE NEXT NOUN and so a
# description of that noun: `delete stale tracked BRIEF-x.md`, `the superseded
# ancestor`, `a REJECTED dead end`. Edit the list HERE.
DECLARING_QUALIFIERS = frozenset((
    "is", "was", "are", "were", "be", "been", "being", "now", "still", "already"))


def declaring_hits(text, patterns, qualifiers=DECLARING_QUALIFIERS):
    """The `patterns` `text` uses to DECLARE something, in list order — the over-eager
    `matched_language` minus every hit that is only qualifying another noun.

    A pattern counts on its FIRST DECLARING occurrence rather than its first occurrence,
    so naming a stale file in one clause does not buy the step immunity in the next.

    A false negative is the deliberate, cheaper failure, the same asymmetry checks 3 and
    5 already chose: a missed stale step costs one confused resume, while a check that
    fires on a freshly-healed task costs every finding it would ever have made."""
    low = (text or "").lower()
    out = []
    for p in patterns:
        needle = p.lower()
        at = low.find(needle)
        while at != -1:
            # `low`, not `text`: the offsets are into the lowercased copy, and a couple
            # of Unicode letters change LENGTH when lowercased. `qualifier` lowercases
            # its answer anyway, so the two are otherwise the same read.
            qual = qualifier(low, at)
            if not qual or qual in qualifiers:
                out.append(p)
                break
            at = low.find(needle, at + 1)
    return out


# The memo backstop's two vocabularies. It needs two because its patterns are not all
# the same part of speech, and the word in front means something different for each.
#
#   * A VERB-ish pattern (`supersede`, `withdrawn`, `no longer`, `stop doing`) has its
#     SUBJECT standing in front of it. The memo is declaring only when that subject is
#     the memo ITSELF — "this supersedes the earlier note" — or when the keyword is the
#     predicate: "is withdrawn", "no longer true". A third-party subject is a report
#     about someone else: "the upstream LIBRARY withdrawn its 3.0 release".
#   * A NOUN pattern (`correction`, `retraction`) IS the thing being announced, so
#     there is no following noun for an article to attach it to. "A correction",
#     "this retraction" both declare one — while the identical article in front of a
#     participle ("a superseded ancestor") is precisely what makes THAT a description.
#
# Edit the lists HERE. Adjectives are open-ended, so "an unexpected correction" is a
# deliberate MISS — the same asymmetry every check here chose: a missed nudge costs one
# reminder, a backstop that cries wolf costs every warning it would ever have given.
SELF_DECLARING_QUALIFIERS = DECLARING_QUALIFIERS | frozenset(("this", "these"))

NOUN_DECLARING_QUALIFIERS = SELF_DECLARING_QUALIFIERS | frozenset((
    "a", "an", "one", "important", "urgent", "minor", "major", "quick"))


# -- check 1: undispositioned acks -----------------------------------------------

def undispositioned_acks(task):
    """Every ack carrying no disposition. Before 2.9.0 an ack was a bare receipt, so
    EVERY ack recorded then reads this way — and that is the exact failure this whole
    line of work came from: a correction acknowledged, then never integrated, while
    the durable layer that auto-loads each session kept saying the opposite."""
    out = []
    for m in (task.get("memos") or []):
        mid = (m.get("id") or "")[:8] or "?"
        for a in (m.get("acks") or []):
            if not (a.get("disposition") or {}).get("kind"):
                out.append(_finding(
                    "ack-undispositioned",
                    "memo %s / session %s" % (mid, (a.get("sid") or "?")[:8]),
                    "acked with NO disposition, so nothing records what the ack did "
                    "with it — retro-dispose it: `heal --apply --dispose-acks %s "
                    "--noop \"<why nothing was needed>\"` (or --decision / --memory)"
                    % mid))
    return out


# -- retro-disposition: a disposition that is VISIBLY retroactive -----------------
#
# An ack recorded before 2.9.0 says WHO saw the memo and nothing about what they did
# with it, and the session that made it is long gone — its intent is unrecoverable. So
# a reconciler has to be able to fill one in. The one hard rule: DO NOT FORGE HISTORY.
# A retro-filled disposition must be tellable apart from one the original acker chose,
# it must name who filled it in and when, and it must NEVER touch the ack's own `sid`
# or `ts`. Those two fields are the original session's testimony; everything this adds
# is the reconciler's, and it is labelled as such.

RETRO_NOOP_REASON = ("retro-dispositioned by heal: this ack predates the disposition "
                     "requirement, so no record exists of what it did with the memo")

RETRO_WHY = ("filled in by a later reconcile pass — the acking session no longer "
             "exists, so what it actually did with the memo is unrecoverable")

RETRO_ACTOR_UNKNOWN = "heal"      # when no --session was given to attribute it to


def retro_disposition(kind, value, sid=None, now=None, why=None):
    """A disposition dict marked as RETROACTIVE: the same `{"kind", "value"}` the live
    `memo ack` records, plus who retro-filled it, when, and why.

    The extra keys are additive, so an older reader sees an ordinary disposition of the
    right kind and simply doesn't render the provenance — it degrades to less
    information, never to WRONG information."""
    return {"kind": kind, "value": value, "retro": True,
            "retro_ts": time.time() if now is None else now,
            "retro_by": sid or RETRO_ACTOR_UNKNOWN,
            "retro_why": why or RETRO_WHY}


def is_retro(disposition):
    """True iff this disposition was retro-filled by a heal rather than chosen by the
    session that acked. The one bit every surface needs to tell the two apart."""
    return bool((disposition or {}).get("retro"))


# -- check 2: corrections whose target was never updated -------------------------

_DECISION_TARGET = re.compile(r"^decision:(\d+)$", re.I)


def _decision_target(target):
    """The decision index a `--corrects decision:<n>` target names, else None."""
    m = _DECISION_TARGET.match((target or "").strip())
    return int(m.group(1)) if m else None


def unfulfilled_corrections(task):
    """Memos that declared `--corrects <target>` where the target was never updated.

    Checked as concretely as each target shape allows, so this never cries wolf:
      * `decision:<n>` — verifiable outright. Flagged when decision n is STILL
        CURRENT, i.e. nothing replaced it, so the correction never landed.
      * a memory slug / another memo's id8 — not verifiable from here, so it falls
        back to the ack ledger: flagged only when NO ack disposed of it with
        `--decision` or `--memory`. A `--noop` is a legitimate answer, but it means
        the target was deliberately not updated, which is exactly what to surface."""
    entries = task.get("decisions") or []
    out = []
    for m in (task.get("memos") or []):
        targets = [str(c).strip() for c in (m.get("corrects") or []) if str(c).strip()]
        if not targets:
            continue
        mid = (m.get("id") or "")[:8] or "?"
        kinds = set()
        for a in (m.get("acks") or []):
            kind = (a.get("disposition") or {}).get("kind")
            if kind:
                kinds.add(kind)
        engaged = bool(kinds & {"decision", "memory"})
        for t in targets:
            n = _decision_target(t)
            if n is not None:
                if 1 <= n <= len(entries) and not _dec.is_replaced(entries[n - 1]):
                    out.append(_finding(
                        "correction-unfulfilled", "memo %s → decision %d" % (mid, n),
                        "declares it corrects decision %d, but that decision is still "
                        "current — supersede or split it, or the record still says both"
                        % n))
                continue
            if not engaged:
                out.append(_finding(
                    "correction-unfulfilled", "memo %s → %s" % (mid, t),
                    "declares it corrects %s, but no ack disposed of it with "
                    "--decision or --memory" % t))
    return out


# -- check 3: prose pretending to be structure -----------------------------------

# A DECISION-SHAPED reference in prose: `decision 8`, `decisions 3, 4 and 5`, `entry 12`,
# or a bare `#8`. The number is what makes it actionable — a supersession the digest could
# act on has to say WHICH entry it replaces. The trailing group takes a LIST, because one
# decision may replace several and the prose says so in one breath.
_DECISION_WORD_REF = re.compile(
    r"\b(?:decisions?|entr(?:y|ies))\s+#?\d+(?:\s*(?:,|and|&)\s*#?\d+)*", re.I)
_NUMBER = re.compile(r"\d+")
_HASH_REF = re.compile(r"#(?P<n>\d+)\b")

# Nouns that, read by `qualifier` off the front of a `#N`, make it name something OTHER
# than a decision on this task. `memo #3`, `task #444`, `PR #12`, `step 2` are all
# numbered, and none of them is a decision — so a supersession claim about one of them
# is not a missing link. Edit the list HERE.
NON_DECISION_QUALIFIERS = frozenset((
    "memo", "memos", "task", "tasks", "story", "stories", "pr", "prs", "issue",
    "issues", "step", "steps", "note", "notes", "rule", "rules", "spec", "specs",
    "doc", "docs", "memory", "section", "line", "lines", "file", "config", "version",
    "release", "commit", "branch", "ticket", "skill", "test", "check", "item"))


def _decision_ref_spans(text):
    """Every decision-shaped reference OCCURRENCE as `(numbers, start, end)`, in the order
    the two shapes are matched.

    Split out of `decision_refs` so the reporting discriminator below can read WHERE each
    reference sat — the word in front of it, the word after it, the clause around it — and
    so there stays exactly ONE place that knows what a decision reference looks like. Two
    readers with two copies of these regexes would answer differently the first time
    either was tuned, which is the same failure the declare-vs-describe guard exists to
    prevent one level up."""
    spans = []
    for m in _DECISION_WORD_REF.finditer(text or ""):
        spans.append(([int(n) for n in _NUMBER.findall(m.group(0))], m.start(), m.end()))
    for m in _HASH_REF.finditer(text or ""):
        if qualifier(text, m.start()) in NON_DECISION_QUALIFIERS:
            continue
        spans.append(([int(m.group("n"))], m.start(), m.end()))
    return spans


def decision_refs(text, own_index=None, total=None):
    """The decision indices this prose plausibly names, as ints, oldest-first.

    THREE gates, each one there to kill a shape that was measured as a false positive:

      * a NUMBER is required. `decision 8` is a target the digest could act on;
        "this rule supersedes the earlier one" is not.
      * a bare `#N` is dropped when the word in front of it names something else —
        `memo #3`, `task #444`, `PR #12`. The word in front is read by `qualifier`,
        the guard check 8 shares.
      * the index must be IN RANGE and EARLIER than the decision doing the talking.
        A decision can only refute one that already existed, so a forward or
        out-of-range number (a task number, a version, a line number) is not a
        decision reference on this task."""
    found = []
    for nums, _start, _end in _decision_ref_spans(text):
        found.extend(nums)
    out = []
    for n in found:
        if own_index is not None and n >= own_index:
            continue
        if total is not None and n > total:
            continue
        if n not in out:
            out.append(n)
    return sorted(out)


# -- the THIRD discriminator: WHO does the sentence say did it? --------------------
#
# THE FIFTH COSTUME OF THE SAME BUG. `qualifier` reads the word standing in FRONT of a
# match, which answers "is this keyword aimed at this entry or at some other noun". It
# cannot answer a second question the same sentence raises: WHO does the sentence say
# performed the correction? On one real task 8 of 17 findings were that shape, and every
# one of them was a decision REPORTING on another decision's work:
#
#   "corrected by decision 184"        — decision 184 did it. This entry is the report.
#   "decision 173 investigated"        — 173 is the ACTOR of a reporting verb.
#   "why decision 150 is NOT superseded" — the sentence DENIES the condition outright.
#
# All three carry supersession vocabulary AND name an earlier decision, so check 3's two
# conditions both hold and it fired. The finding it printed — "the digest cannot act on
# prose, so what this contradicts is still briefing every session" — was flatly wrong:
# nothing was being contradicted, the entry was minuting a decision that had already been
# taken. Eight of those in one report is how a reader learns to skip the other nine.
#
# THREE READINGS, ONE PER SHAPE, and every one of them is a VOCABULARY brought to an
# existing reader rather than a fourth heuristic — the rule stated above `qualifier`:
#
#   1. AGENT-BY. The word in front of the reference is `by` / `per`, so the decision is
#      what DID the correcting. Read with `qualifier`, exactly as check 3 reads its own
#      `NON_DECISION_QUALIFIERS`.
#   2. ACTOR-SUBJECT. The word AFTER the reference is a reporting verb (`investigated`,
#      `said`, `found`), so the decision is the SUBJECT of the sentence and the sentence
#      is minuting what it did. `decision 4 was wrong` survives this: `was` is not a
#      reporting verb, it is the predicate that makes the sentence a DECLARATION about
#      decision 4.
#   3. DENIAL. A supersession keyword in the same clause carries a negator immediately in
#      front of it (`is NOT superseded`, `was never wrong`). A sentence explaining why
#      something is NOT superseded is the opposite of a claim that it was.
#
# THE ASYMMETRY IS THE SAME ONE EVERY CHECK HERE CHOSE, and it points the same way: a
# reference that is BOTH declared against and reported on in one long entry is dropped,
# which costs a missed finding. A missed unlinked supersession costs one confused resume.
# The alternative — eight false ones per report — costs the whole check.

# Prepositions that, standing in front of a decision reference, make that decision the
# AGENT of the action rather than its target. Edit the list HERE.
REPORTING_QUALIFIERS = frozenset(("by", "per"))

# Verbs that, standing immediately AFTER a decision reference, make it the SUBJECT of a
# report. Deliberately all past/present ACTION verbs and deliberately NOT a form of "to
# be": `decision 4 was wrong` and `decision 12 is superseded` are declarations ABOUT the
# named decision, which is exactly what check 3 exists to find.
#
# AND DELIBERATELY NONE OF THE SUPERSESSION VOCABULARY ITSELF — no `corrected`, `superseded`,
# `replaced`. Those words are ambiguous between active and passive at exactly this position,
# and the passive reading is the FINDING: `decision 4 superseded by this one` is prose
# claiming a supersession the structure does not record, while `decision 4 superseded the
# flat-file rule` is a minute. One word cannot tell them apart, and guessing would trade the
# check's best true positive for one more silenced false one. The passive form is already
# covered from the other side: `corrected BY decision 184` puts `by` in FRONT of the
# reference, which is reading 1. Edit the list HERE.
REPORTING_VERBS = frozenset((
    "said", "says", "saying", "investigated", "investigates", "found", "finds", "ruled",
    "rules", "recorded", "records", "noted", "notes", "decided", "decides", "chose",
    "chooses", "established", "establishes", "explained", "explains", "documented",
    "documents", "described", "describes", "covered", "covers", "concluded", "concludes",
    "reported", "reports", "proposed", "proposes", "asked", "asks", "landed", "lands",
    "shipped", "ships", "added", "adds", "introduced", "introduces", "called", "calls"))

# Words that, standing immediately in front of a supersession keyword, DENY it. Edit HERE.
#
# NO CONTRACTIONS, and that is a stated miss rather than an oversight: `qualifier` matches
# `[A-Za-z][\w.-]*`, so the word it hands back for "isn't superseded" is `t` — the tail after
# the apostrophe. Listing `isn't` here would look like coverage and buy none, and widening the
# one shared reader to chase contractions would change what every OTHER vocabulary sees. The
# cost of the miss is one false positive on "it isn't superseded", which is the cheap
# direction this module always takes.
NEGATING_QUALIFIERS = frozenset(("not", "never", "nor", "neither", "without", "no"))

# How far either side of a decision reference counts as "the same sentence" for the denial
# reading. Bounded on purpose: a decision is often a page long, and a negated keyword
# eleven sentences away says nothing about this one.
REPORTING_WINDOW = 120

# A relative pronoun standing between the reference and its verb — `decision 173, WHICH
# corrected the store`. The clause still says 173 did it, so the reading has to look one word
# further; two is the whole depth, because a third would start guessing at grammar.
RELATIVE_PRONOUNS = frozenset(("which", "that", "who"))

_WORDS_AFTER = re.compile(r"[A-Za-z][\w.'-]*")
_CLAUSE_BREAK = re.compile(r"[.!?;\n]")


def _words_after(text, end, count=2):
    """The first `count` words AFTER a match ending at `end`, lowercased and stripped of
    trailing punctuation — the mirror of `qualifier`, which reads the one word in front.

    Two rather than one, because a relative pronoun can sit between a decision reference and
    the verb that says what it did. Stops at the end of the LINE for the same reason
    `qualifier` starts at one: a line break is a stronger boundary than a space."""
    tail = (text or "")[end:].split("\n", 1)[0]
    return [w.lower().strip(".,:;") for w in _WORDS_AFTER.findall(tail)[:max(1, count)]]


def _clause_at(text, start, end, window=REPORTING_WINDOW):
    """The text around `[start, end)`, cut at the nearest sentence or line boundary on
    each side and capped at `window` chars — "the same sentence", cheaply and without a
    sentence tokenizer this module would then have to keep in step with the other one."""
    body = text or ""
    left = max(0, start - window)
    head = body[left:start]
    breaks = list(_CLAUSE_BREAK.finditer(head))
    if breaks:
        head = head[breaks[-1].end():]
    tail = body[end:end + window]
    m = _CLAUSE_BREAK.search(tail)
    if m:
        tail = tail[:m.start()]
    return head + body[start:end] + tail


def _denies_correction(clause, patterns=None):
    """True when a supersession keyword inside `clause` is NEGATED — the word in front of
    it is one of `NEGATING_QUALIFIERS`.

    `qualifier` is reused rather than reimplemented: the question ("what word stands in
    front of this match?") is the module's one shared reader, and only the vocabulary
    differs. Here a hit means STAY SILENT, so an over-eager read can only cost a missed
    finding — the cheap direction, as everywhere else in this module."""
    low = (clause or "").lower()
    for p in (patterns or SUPERSESSION_LANGUAGE):
        needle = p.lower()
        at = low.find(needle)
        while at != -1:
            if qualifier(low, at) in NEGATING_QUALIFIERS:
                return True
            at = low.find(needle, at + 1)
    return False


def reports_another_decision(text, start, end, window=REPORTING_WINDOW):
    """True when the reference at `[start, end)` is one this text REPORTS ON rather than
    declares against — the three readings documented above, in cost order (two word
    lookups before the clause scan)."""
    if qualifier(text, start) in REPORTING_QUALIFIERS:
        return True
    after = _words_after(text, end, 2)
    if after and after[0] in REPORTING_VERBS:
        return True
    if len(after) > 1 and after[0] in RELATIVE_PRONOUNS and after[1] in REPORTING_VERBS:
        return True
    return _denies_correction(_clause_at(text, start, end, window))


def reported_decision_refs(text, window=REPORTING_WINDOW):
    """The decision numbers this text merely REPORTS ON, as a set.

    Returned UNFILTERED by range or direction — the caller already has `decision_refs`
    for that, and this answers a different question about the same occurrences. A number
    named twice, once as a target and once as an actor, lands in here: see the asymmetry
    note above for why that deliberate false negative is the cheaper failure."""
    out = set()
    for nums, start, end in _decision_ref_spans(text):
        if reports_another_decision(text, start, end, window):
            out.update(nums)
    return out


def prose_supersession(task):
    """Decisions whose TEXT claims a supersession of ANOTHER DECISION that the structure
    doesn't record.

    A decision saying "decision 4 was wrong" is only readable by a human; the digest
    cannot act on it, so entry 4 keeps briefing fresh sessions. That is the finding
    worth having. What this check must NOT do is report every decision that happens to
    use the vocabulary of supersession, and the first version did exactly that: on one
    real task it produced 5 findings of which 4 were false — a decision describing the
    supersede FEATURE, a rule that supersedes another rule, a correction to a memory
    note, a memo chain. A check that is 80% wrong trains the reader to skip it, which is
    worse than not having the check at all. (The drift check had this same problem and
    was fixed the same way; see `_ref_shaped`.)

    So THREE conditions must ALL hold, not one:

      1. the prose carries supersession language, and
      2. it names a DECISION-SHAPED target — `decision N`, `entry N` or `#N`, pointing
         at an earlier decision that exists on this task (see `decision_refs`), and
      3. the sentence DECLARES against that decision rather than REPORTING on it
         (`reported_decision_refs`). Condition 3 shipped four releases late: "corrected by
         decision 184", "decision 173 investigated" and "why decision 150 is NOT
         superseded" satisfy 1 and 2 perfectly, and on one real task eight findings of
         that exact shape stood beside nine real ones.

    Skipped, as before, when the decision is already replaced (the mark IS the
    structure) or when another decision names it as ITS replacement — i.e. it already
    carries a `--supersedes` link and the prose is just describing what the link
    records. Also skipped when the decision it names is ALREADY replaced: the whole
    complaint is that "whatever this contradicts is still briefing every session", and a
    replaced decision briefs nobody, so there is nothing left to link.

    A false negative is the deliberate, cheaper failure: a missed prose supersession
    costs one confused resume, while a check nobody reads costs every finding it would
    ever have made."""
    entries = task.get("decisions") or []
    pointed_at = set()
    for e in entries:
        rep = _dec.replacement(e)
        if rep is not None:
            pointed_at.update(rep[1])
    still_live = set(i for i, _e in _dec.live(entries))
    out = []
    for i, e in enumerate(entries, 1):
        if _dec.is_replaced(e) or i in pointed_at:
            continue
        body = _dec.text(e)
        hits = matched_language(body, SUPERSESSION_LANGUAGE)
        if not hits:
            continue
        reported = reported_decision_refs(body)
        refs = [n for n in decision_refs(body, own_index=i, total=len(entries))
                if n in still_live and n not in reported]
        if not refs:
            continue
        out.append(_finding(
            "prose-supersession", "decision %d" % i,
            "says %s about decision %s in prose, but no --supersedes link records it — "
            "the digest cannot act on prose, so what this contradicts is still briefing "
            "every session"
            % (", ".join("%r" % h for h in hits),
               ", ".join(str(n) for n in refs))))
    return out


# -- check 4: oversized decisions ------------------------------------------------
#
# TWO TIERS OVER ONE ADVISORY — see the constants at the top of this file for why 4,000
# was neither. The split between them is the same one that separates a finding from a
# proposal everywhere else in this module: has anything gone WRONG, or is there merely
# something worth reading?
#
#   * >2× the advisory (1,200) is a PROPOSAL. A real task's decisions AVERAGE ~1,400
#     chars, so this tier describes the NORMAL length of a decision on a working task.
#     Reporting it as an issue would put `Heal due? YES` on every healthy record on the
#     board — the cry-wolf failure this module has fixed four times — so it never counts
#     as an issue, never makes a heal due, and is capped at the worst OVERSIZE_TOP_N with
#     the remainder collapsed into one "+N more". A list of forty is not a proposal, it is
#     a wall.
#   * >6× the advisory (3,600) is a FINDING. That is roughly where the longest entry on a
#     real task sat, and it is the length at which the entry stops being supersedable a
#     piece at a time: every ruling inside it is welded to every other one, so the only
#     honest verb left is `heal --split`. Both tiers name that verb, because "this is long"
#     with no move attached is a complaint rather than a report.

def oversized(task, limit=OVERSIZE_CHARS):
    """Still-current decisions past the FINDING threshold — the ones a split is the only
    honest answer to. Only live ones: a replaced monster already costs the digest nothing.

    `limit` stays a parameter (and `plan` passes it through) so one caller can retune the
    tier without the module having two opinions about it."""
    out = []
    for i, e in _dec.live(task.get("decisions")):
        n = len(_dec.text(e))
        if n > limit:
            out.append(_finding(
                "oversized", "decision %d" % i,
                "%d chars — %.1f× the %d-char write advisory, and past the point where an "
                "entry can be superseded a piece at a time: every ruling in it is welded "
                "to every other one. Split it with `heal --split %d --into <n1,n2,…>` "
                "(add the atomic parts with `update --decision` first)"
                % (n, float(n) / max(1, WRITE_ADVISORY_CHARS), WRITE_ADVISORY_CHARS, i)))
    return out


def oversized_proposals(task, limit=OVERSIZE_PROPOSAL_CHARS,
                        finding_limit=OVERSIZE_CHARS, cap=OVERSIZE_TOP_N):
    """Live decisions in the PROPOSAL band, worst-first, as
    `{"shown": [{"index", "chars"}, …], "more": N, "total": N}`.

    Entries already reported as FINDINGS are excluded rather than listed twice: the same
    decision appearing in both an issue list and a proposal list reads as two problems, and
    a reader who fixes one is then told the other is still outstanding.

    `more` is the count the cap dropped, and it is reported rather than silently truncated
    — a list that quietly stops at five reads as "there were five"."""
    rows = []
    for i, e in _dec.live(task.get("decisions")):
        n = len(_dec.text(e))
        if limit < n <= finding_limit:
            rows.append({"index": i, "chars": n})
    rows.sort(key=lambda r: (-r["chars"], r["index"]))
    cap = max(1, int(cap or 1))
    return {"shown": rows[:cap], "more": max(0, len(rows) - cap), "total": len(rows)}


# -- check 5: drift (paths, worktrees, branches) ---------------------------------

# A `branch <x>` / `worktree <x>` mention. Two alternatives, deliberately: a BACKTICKED
# value is the author marking up a literal name, and is trusted as-is; a BARE word has
# to earn it (see `_ref_shaped`).
_BRANCH_MENTION = re.compile(
    r"\b(?:branch|worktree)\s+(?:`(?P<quoted>[^`\s]{1,60})`|(?P<bare>[\w][\w./-]{1,59}))",
    re.I)

# What makes a bare word look like a git ref rather than the next English word: a
# separator or a digit. No English word carries `/`, `-`, `_` or a number; every real
# ref name in practice carries at least one (`heal-wip`, `origin/dev`, `2707-rollup`).
_REF_SHAPED = re.compile(r"[-_/\d]")

# The exceptions — conventional bare branch names, which are single alphabetic words and
# so would fail `_REF_SHAPED`. Safe to allow because English never puts these AFTER the
# word "branch": prose says "the production branch", not "branch production".
_CONVENTIONAL_REFS = frozenset(("main", "master", "dev", "develop", "trunk",
                                "staging", "production"))


def _ref_shaped(name, quoted):
    """Whether `name` is plausibly a git ref, as opposed to the English word that
    happened to follow "branch".

    WHY THIS GUARD EXISTS. The bare `branch\\s+(\\w+)` match it replaces reported
    `branch prefix`, `branch off`, `branch while`, `branch names` and `branch with` on
    one real task — 5 of the drift check's 7 findings were English, and only 2 were
    genuinely dead paths. A check that cries wolf 5 times out of 7 is worse than no
    check, because it trains the reader to skip the 2 that matter. So the bar is now
    "looks like a ref", and a false negative is the deliberate cheaper failure: a
    missed dead branch costs one confused resume, a false "your branch is gone" costs
    the whole check's credibility."""
    if quoted:                  # the author wrote it as a literal — trust that
        return True
    if name.lower() in _CONVENTIONAL_REFS:
        return True
    return bool(_REF_SHAPED.search(name))


def _run_git(args, timeout=GIT_TIMEOUT):
    """Run a git command, returning (ok, stdout). Swallows EVERYTHING — a missing git,
    a timeout, a non-repo — because an unavailable probe must read as "unknown", never
    as "gone"."""
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, (r.stdout or "").strip()
    except Exception:
        return False, ""


def _is_repo(d, run=_run_git):
    ok, _ = run(["git", "-C", d, "rev-parse", "--git-dir"])
    return ok


def _has_branch(d, name, run=_run_git):
    """True when `name` resolves as a branch under any of the three namespaces a
    written-down name can mean: a local branch (`dev`), a remote-tracking branch named
    in full (`origin/dev`), or a bare name that only exists on the remote (`dev` after
    the local copy was deleted). Checking all three is what stops a pushed-then-
    locally-deleted branch — and the very common `origin/<x>` phrasing — from being
    reported as gone."""
    for ref in ("refs/heads/%s" % name,
                "refs/remotes/%s" % name,
                "refs/remotes/origin/%s" % name):
        ok, _ = run(["git", "-C", d, "rev-parse", "--verify", "--quiet", ref])
        if ok:
            return True
    return False


def recorded_paths(task):
    """The paths this task actually RECORDED, as `(kind, path)` — edited files and
    session working directories (a delegated worktree shows up here).

    Deliberately structured-fields-only. Scraping prose for path-shaped substrings
    would report "and/or" as a missing directory, and a drift report that cries wolf
    is one nobody reads."""
    out = []
    for p in (task.get("files") or []):
        if isinstance(p, str) and p.startswith("/"):
            out.append(("file", p))
    for sid, meta in sorted((task.get("session_meta") or {}).items()):
        cwd = (meta or {}).get("cwd")
        if isinstance(cwd, str) and cwd.startswith("/"):
            out.append(("worktree", cwd))
    return out


# -- what is NOT drift: a path that was never expected to survive ------------------
#
# THE SESSION SCRATCHPAD IS EPHEMERAL BY CONSTRUCTION. It is wiped when the session
# ends — that is the whole point of it — and task-station auto-captures the worker
# briefs written there as edited files. So on one real task the drift check reported
# SEVEN findings and every single one was a brief under
# `/private/tmp/…/<session-uuid>/scratchpad/`.
#
# Each was literally true and practically useless. "The digest points a resumed session
# at somewhere it cannot go" is the right complaint about a deleted worktree and the
# wrong one about a temp file: nobody resumes a task by opening a worker brief out of a
# wiped temp directory. Seven of them made a heal DUE on a task that had nothing wrong
# with it — the cry-wolf failure this module has already fixed four times, arriving from
# a new direction. This time the discrimination is not about English (see `qualifier`)
# but about WHERE: is this somewhere the system promises to keep, or somewhere it
# promises to erase?
#
# Ephemeral paths are COUNTED, never reported one by one and never as findings. "3
# recorded paths were session scratchpads" is worth one line; seven bullets telling you
# to go and look at files that were designed to disappear is worth less than nothing.

# Roots the operating system is free to wipe. The /private-prefixed forms are listed
# alongside the bare ones because on macOS `/tmp` and `/var` are symlinks into
# `/private`, and a recorded path is matched here as a STRING — resolving it would need
# the path to still exist, which is precisely what is not true of the ones that matter.
TEMP_ROOTS = ("/tmp", "/private/tmp", "/var/tmp", "/private/var/tmp",
              "/var/folders", "/private/var/folders")

# A path SEGMENT that makes everything under it session-scoped, wherever it lives.
EPHEMERAL_SEGMENTS = frozenset(("scratchpad", "scratchpads"))


def ephemeral_path(p, tmpdir=None):
    """True when `p` lives somewhere that is EXPECTED to vanish — a session scratchpad
    or a system temp root — so its absence is the design working, not drift.

    `tmpdir` defaults to `$TMPDIR`, read fresh on every call, which is what catches the
    per-user temp root on platforms that do not use a fixed one.

    A false NEGATIVE is the deliberate, cheaper failure here, the same asymmetry every
    check in this module chose — but note that it points the other way for this one.
    Missing an ephemeral path costs ONE bogus finding; misclassifying a real repo path
    as ephemeral would silently drop the finding that matters. So the test is narrow and
    literal: a named scratchpad segment, or a known temp root. Nothing is inferred from
    a path merely LOOKING temporary."""
    s = str(p or "").strip()
    if not s:
        return False
    s = os.path.normpath(s)
    if any(seg in EPHEMERAL_SEGMENTS for seg in s.split("/") if seg):
        return True
    roots = list(TEMP_ROOTS)
    tmpdir = os.environ.get("TMPDIR") if tmpdir is None else tmpdir
    if tmpdir:
        roots.append(str(tmpdir))
    for root in roots:
        root = os.path.normpath(str(root).strip()).rstrip("/")
        if root and root != "/" and (s == root or s.startswith(root + "/")):
            return True
    return False


def vanished_ephemeral(task, exists=os.path.exists):
    """Recorded paths that are gone AND were never expected to survive, as
    `[{"kind", "path"}, …]`.

    Counted next to the findings and never among them: these can never be a defect, so
    they must never make a heal due. See the note above for the seven-finding run that
    produced this."""
    return [{"kind": kind, "path": p} for kind, p in recorded_paths(task)
            if ephemeral_path(p) and not exists(p)]


def mentioned_branches(task):
    """Branch/worktree names named EXPLICITLY as `branch <x>` / `worktree <x>` in the
    goal, state or summary — and only the ones that are actually REF-SHAPED.

    These fields are narrative prose, so the explicit phrasing alone was not enough:
    `branch off`, `branch names`, `branch with` all matched it. `_ref_shaped` is the
    second gate, and it is what makes this check worth reading."""
    names, seen = [], set()
    for field in ("goal", "state", "summary"):
        for m in _BRANCH_MENTION.finditer(str(task.get(field) or "")):
            quoted = m.group("quoted")
            # Slashes are KEPT — `feature/x` and `origin/dev` are both real names, and
            # `_has_branch` resolves each against every namespace it could mean. A
            # trailing sentence period is not part of the name, so strip it FIRST and
            # judge the shape of what is left.
            name = (quoted if quoted is not None else m.group("bare")).strip("./")
            if not name or not _ref_shaped(name, quoted is not None):
                continue
            if name not in seen:
                seen.add(name)
                names.append(name)
    return names


# -- WHICH REPOS A PROBER MAY ASK, and the folder rename that made the answer wrong ---
#
# The first version of `task_repos` derived its list from `recorded_paths` alone — the FILE
# and WORKTREE paths a task wrote down. That reads as reasonable and is wrong in one very
# ordinary situation: RENAME THE FOLDER. Every recorded path pointing into it dies at once,
# while the ones pointing somewhere UNRELATED — a notes vault, the installed plugin cache —
# survive untouched. The list is then NON-EMPTY and holds the wrong repo, so both probers
# answer a confident False about a branch and a commit that are sitting right there.
#
# Measured on one real task after `claude-todo` was renamed to `task-station`: 16 of 27
# findings were false. 15 of the 16 cited shas resolved in the renamed clone and none in the
# vault the prober actually asked. The branch it called gone resolved locally AND on origin.
#
# AND THE DOCSTRING'S OWN PROMISE WAS BROKEN. "An empty list is what makes both probers
# return UNKNOWN rather than False" was kept for the empty case and broken for the
# wrong-repo case — which is worse, because a list holding the wrong repo looks answered.
# The fix has to restore the promise, not just widen the search, so it comes in two halves:
#
#   * WIDEN THE SCOPE. The repos a task NAMES (`projects`) are resolved through the repo
#     index (`repos.json`), which records where each repo IS NOW rather than where a path
#     recorded months ago said it was. A rename MOVES an index entry; it does not delete it.
#     Recorded paths stay in the union, because they are the only thing that knows about a
#     worktree, a submodule or a checkout that was never indexed.
#
#   * NARROW THE CLAIM. A prober saying False is claiming "this resolves in NONE of the
#     task's repos". When a repo the task named has no LOCAL clone to ask, that claim covers
#     repos the prober never opened, so it degrades to UNKNOWN. This is the 16th false
#     finding exactly: a sha in an ADO repo nobody had cloned on this machine, reported as
#     rewritten history. Nothing about the check is switched off — with every named repo
#     reachable, a negative is still a finding, because then the sentence is true.
#
# The index is read fail-OPEN, like every other outward probe here: no index, an unreadable
# one, or a name it does not carry is simply one fewer repo to ask. An index that cannot be
# read must never be worse than the silence it replaced.

REPO_INDEX_FILE = "repos.json"


def repo_index(data_dir=None):
    """The repo index as `[{name, path, …}, …]`, or `[]` when there is nothing readable.

    `repos.json` is written by `repos --refresh` next to the task store. Reading it here is
    a one-way dependency on purpose — heal never builds or refreshes the index, it only asks
    what the index already says, and an absent index degrades this check rather than
    breaking it."""
    try:
        with open(os.path.join(data_dir or paths.data_dir(), REPO_INDEX_FILE)) as fh:
            raw = json.load(fh)
    except Exception:                                        # noqa: BLE001
        return []
    if isinstance(raw, dict):
        raw = raw.get("repos")
    if not isinstance(raw, list):
        return []
    return [r for r in raw if isinstance(r, dict) and r.get("name")]


def named_repos(task, index=None, exists=os.path.exists):
    """`(dirs, unresolved)` for the repos this task NAMES in `projects`.

    `dirs` are the ones with a local clone the probers can ask. `unresolved` are the NAMES
    that resolved to nothing here — no index entry, or an entry whose path is gone — and
    they are the reason a negative has to read UNKNOWN: they are exactly the repos a "it
    resolves nowhere" sentence would be covering without having looked."""
    names = [str(n).strip() for n in (task.get("projects") or []) if str(n or "").strip()]
    if not names:
        return [], []
    by_name = {}
    for r in (repo_index() if index is None else index):
        nm = str(r.get("name") or "").strip().casefold()
        path = str(r.get("path") or "").strip()
        if nm and path:
            by_name.setdefault(nm, path)
    dirs, unresolved, seen = [], [], set()
    for name in names:
        path = by_name.get(name.casefold())
        if path and exists(path) and os.path.isdir(path):
            if path not in seen:
                seen.add(path)
                dirs.append(path)
        elif name not in unresolved:
            unresolved.append(name)
    return dirs, unresolved


def repo_scope(task, exists=os.path.exists, run=_run_git, index=None):
    """`(repos, unresolved)` — every git repo a prober may ask, and the repo NAMES it could
    not ask because there is no local clone.

    ONE reader for both git probers. They ask different questions of the same set — does
    this branch resolve, does this commit resolve — and two copies of "which repos does this
    task mean" would answer differently the first time either was tuned. An empty `repos` is
    what makes both probers return UNKNOWN rather than False; a non-empty `unresolved` is
    what makes them return UNKNOWN rather than False even when they DID find repos to ask."""
    dirs, seen = [], set()
    for _kind, p in recorded_paths(task):
        for d in (p, os.path.dirname(p)):
            if d and d not in seen and exists(d) and os.path.isdir(d):
                seen.add(d)
                dirs.append(d)
    named, unresolved = named_repos(task, index=index, exists=exists)
    for d in named:
        if d not in seen:
            seen.add(d)
            dirs.append(d)
    return [d for d in dirs if _is_repo(d, run)], unresolved


def task_repos(task, exists=os.path.exists, run=_run_git, index=None):
    """The git repositories this task touched or named, as directory paths. The `repos` half
    of `repo_scope` — kept as its own name because that is the question most readers have."""
    return repo_scope(task, exists=exists, run=run, index=index)[0]


def branch_prober(task, exists=os.path.exists, run=_run_git, index=None):
    """A `probe(name) -> True | False | None` over the git repos this task touched or named.

    None means UNKNOWN — no usable repo was found, or a named repo could not be asked — and
    unknown is never reported as drift. That asymmetry is the point: a false "your branch is
    gone" is far worse than a missed one."""
    repos, unresolved = repo_scope(task, exists=exists, run=run, index=index)

    def probe(name):
        if not repos:
            return None
        for d in repos:
            if _has_branch(d, name, run):
                return True
        return None if unresolved else False
    return probe


def drift(task, exists=os.path.exists, branch_probe=None):
    """Recorded paths, worktrees and branches that no longer exist AND were supposed to.

    Session scratchpads and system temp paths are skipped outright (`ephemeral_path`):
    they are erased by design, so their absence is not drift. They are counted instead,
    by `vanished_ephemeral`.

    `branch_probe` is injected (see `branch_prober`) and defaults to NONE — no git
    subprocess at all — so the cheap path used by the SessionStart nag stays pure
    filesystem stats. `heal --scan` wires the real prober."""
    out = []
    for kind, p in recorded_paths(task):
        if ephemeral_path(p):
            continue
        if not exists(p):
            out.append(_finding(
                "drift", p,
                "recorded %s no longer exists — the digest points a resumed session at "
                "somewhere it cannot go" % kind))
    for name in mentioned_branches(task):
        state = None
        if branch_probe is not None:
            try:
                state = branch_probe(name)
            except Exception:
                state = None
        if state is False:
            out.append(_finding(
                "drift", "branch %s" % name,
                "named in the digest but resolves neither locally nor on origin"))
    return out


# -- check 6: link rot -----------------------------------------------------------

def stored_links(task):
    """The task's stored PR/story links as `(kind, url)`. Takes both element shapes —
    `{"url","desc"}` and the legacy bare string."""
    out = []
    for kind, field in (("PR", "prs"), ("story", "stories")):
        for e in (task.get(field) or []):
            url = (e.get("url") if isinstance(e, dict) else e) or ""
            url = str(url).strip()
            if url:
                out.append((kind, url))
    return out


def link_states(task, probe=None):
    """Every stored link as `(kind, url, state)` where state is True (resolves),
    False (does not) or None (UNKNOWN).

    Network-dependent, so it degrades to unknown on ANY failure and NEVER reports a
    live link as dead. With no `probe` wired — the default, and what `heal --scan`
    does unless asked otherwise — every link is unknown and nothing is reported."""
    out = []
    for kind, url in stored_links(task):
        state = None
        if probe is not None:
            try:
                state = probe(url)
            except Exception:
                state = None
            if state is not True and state is not False:
                state = None
        out.append((kind, url, state))
    return out


def link_rot(task, probe=None):
    """Stored links the probe positively reports as unresolvable. Unknown is silent."""
    return [_finding("link-rot", url, "%s link does not resolve" % kind)
            for kind, url, state in link_states(task, probe) if state is False]


# -- the OPT-IN network probe for the check above -----------------------------------
#
# `link_states` has always taken a probe and has never been given one, so every link has
# always read UNKNOWN. That default is right for the SessionStart path — an HTTP round trip
# per stored link, at every session start, for a check that can only ever say "still there"
# is not a cost this tool gets to spend on a user's behalf. So the prober exists and is
# wired only when `--probe-links` asks for it.
#
# EVERY FAILURE IS UNKNOWN, NOT DEAD. A private Azure DevOps PR answers 401 or 203 to an
# unauthenticated HEAD; a corporate proxy answers 407; a flaky DNS answers nothing at all.
# None of those is evidence the PR is gone, and "your PR link is dead" about a live PR is
# the most expensive false positive this module could print — it points a reader at work
# they would then go looking for. So ONLY an explicit 404/410 counts as dead, and
# everything else — including any exception — degrades to unknown.

LINK_PROBE_TIMEOUT = 4          # seconds per link; a slow host must never hang a scan
LINK_DEAD_CODES = (404, 410)    # the only two answers that mean GONE


def link_prober(timeout=LINK_PROBE_TIMEOUT):
    """A `probe(url) -> True | False | None` doing one HTTP HEAD per link.

    `urllib` is imported HERE rather than at module scope: this module loads on the
    SessionStart path, which never probes, and an import that path cannot use is a cost
    every session pays for nothing."""
    import urllib.error
    import urllib.request

    def probe(url):
        u = str(url or "").strip()
        if not (u.startswith("http://") or u.startswith("https://")):
            return None            # not something HTTP can answer for
        req = urllib.request.Request(u, method="HEAD")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return 200 <= int(getattr(r, "status", 0) or 200) < 400
        except urllib.error.HTTPError as e:
            code = int(getattr(e, "code", 0) or 0)
            if code in LINK_DEAD_CODES:
                return False
            return None            # 401/403/405/5xx say nothing about whether it exists
        except Exception:
            return None
    return probe


# -- check 11: the FIRST OUTWARD check — cited commits ------------------------------
#
# EVERY OTHER CHECK CROSS-REFERENCES THE RECORD WITH ITSELF, and that is a structural
# ceiling rather than a gap in the vocabulary: the decision log can be perfectly consistent
# and still cite a commit that a rebase, a squash-merge or a force-push erased. Nothing
# inside the task contradicts anything, because the thing that changed is outside it. So
# this one asks a question of a git repo.
#
# DECLARED CITATIONS ONLY, and this is the whole reason the check is safe to ship. A
# sha-shaped token is 7-40 hex characters, and so is a task id prefix, a memo id8, a heal
# fingerprint, an ANSI colour, a git tree hash and the CSS in a pasted snippet. Matching
# bare hex would report "your commit vanished" about a task id — a finding that is not
# merely wrong but unfixable, because there is no commit to go and look for. So a citation
# must be INTRODUCED: `commit <sha>`, `merged <sha>`, `sha <sha>`, `pushed <sha>`, or an
# `@` immediately in front (`main @ 4412760`, the form the release notes here use). This is
# `mentioned_branches`' discipline — explicit phrasing plus a shape gate — applied to the
# other kind of git ref.
#
# AND THE SHAPE GATE IS A DIGIT. Seven hex-only letters spell real English words —
# `defaced`, `acceded`, `beefed` — so "the commit defaced the config" would otherwise
# match. Requiring at least one digit costs essentially nothing (a real sha without a digit
# in its first seven characters is about a 1-in-1000 event) and kills that whole class.
#
# UNKNOWN IS NEVER REPORTED, twice over: with no prober wired nothing is probed at all (the
# cheap path spawns no subprocess), and a probe that finds no usable repo, or that errors,
# returns None. Only a sha that positively resolves in NONE of the task's repos is a
# finding. The asymmetry is the one every check here chose, and it is starker for this one:
# a missed rewritten commit costs one confused reader, while a false "history was rewritten"
# sends somebody hunting through reflogs for a commit that is sitting right there.
#
# THE FALSE POSITIVE THIS CHECK USED TO EMIT — a commit cited from a repo the task never
# RECORDED — DID show up in practice, 16 times on one task, and the fix was NOT to loosen the
# check. The scope was widened to the repos the task NAMES (see `repo_scope`), and the CLAIM
# was narrowed: when a named repo has no local clone to ask, a negative is UNKNOWN, because
# "resolves in none of the task's repos" would then be covering repos nobody opened. The
# finding is still worded as exactly what was measured — "resolves in none of the TASK'S
# repos", and "history MAY have been rewritten" — because a hedge in the sentence is cheaper
# than a check that is silently wrong about which repos it looked in.

# The declaring words, then the token. `@\s*` is the second introducer — `main @ 4412760`.
# Case-insensitive because a decision opens sentences: `Commit 4412760 …`. Edit the list
# HERE; a word added to it is a promise that what follows it is a git object.
_SHA_MENTION = re.compile(
    r"(?:\b(?:commits?|merged?|sha|revisions?|rev|pushed|push)\s+|@\s*)"
    r"`?(?P<sha>[0-9a-f]{7,40})`?\b", re.I)
_HAS_DIGIT = re.compile(r"\d")


def _sha_shaped(sha):
    """Whether a hex-looking token is plausibly a commit sha rather than an English word
    that happens to spell in hex. See the digit gate above."""
    return bool(_HAS_DIGIT.search(sha or ""))


def commit_citations(task):
    """Commit shas this task DECLARES, as `[(sha, where), …]` — first mention wins, so one
    sha cited five times is one thing to check.

    Read from the decision log and the `--log` milestone trail (`history`), which are where
    a task records what shipped. Deliberately NOT from `state`/`summary`/`goal`: those are
    rewritten in place, so a sha in one is a snapshot of a moment rather than a record of
    it, and the narrative fields are exactly where prose density is highest."""
    out, seen = [], set()
    sources = [("decision %d" % i, _dec.text(e))
               for i, e in _dec.live(task.get("decisions"))]
    for n, h in enumerate((task.get("history") or []), 1):
        if isinstance(h, dict):
            sources.append(("log entry %d" % n, str(h.get("text") or "")))
    for where, body in sources:
        for m in _SHA_MENTION.finditer(body or ""):
            sha = m.group("sha").lower()
            if not _sha_shaped(sha) or sha in seen:
                continue
            seen.add(sha)
            out.append((sha, where))
    return out


def _has_commit(d, sha, run=_run_git):
    """True when `sha` names a COMMIT object in the repo at `d`. `^{commit}` is what makes
    it a commit rather than any object: a blob whose hash happens to be cited is not a
    commit, and `cat-file -e` alone would call it one."""
    ok, _ = run(["git", "-C", d, "cat-file", "-e", "%s^{commit}" % sha])
    return ok


def commit_prober(task, exists=os.path.exists, run=_run_git, index=None):
    """A `probe(sha) -> True | False | None` over the git repos this task touched or named —
    the same injected-prober shape as `branch_prober`, and None means UNKNOWN for the same
    two reasons: no usable repo was found, or a repo the task named has no local clone here.
    Neither is evidence about the commit."""
    repos, unresolved = repo_scope(task, exists=exists, run=run, index=index)

    def probe(sha):
        if not repos:
            return None
        for d in repos:
            if _has_commit(d, sha, run):
                return True
        return None if unresolved else False
    return probe


def commit_states(task, probe=None):
    """Every cited commit as `(sha, where, state)` where state is True (resolves), False
    (resolves in none of the task's repos) or None (UNKNOWN).

    With no `probe` wired — the default, and what the SessionStart path does — every
    citation is unknown and nothing is reported."""
    out = []
    for sha, where in commit_citations(task):
        state = None
        if probe is not None:
            try:
                state = probe(sha)
            except Exception:
                state = None
            if state is not True and state is not False:
                state = None
        out.append((sha, where, state))
    return out


def commit_rot(task, probe=None, states=None):
    """Cited commits that positively resolve in NONE of the task's repos. Unknown is
    silent.

    `states` lets a caller that has already probed pass the answers in — `scan` needs the
    same states for its report, and probing twice would double every subprocess for a
    check whose whole justification is that the cheap path spawns none."""
    states = commit_states(task, probe=probe) if states is None else states
    return [_finding("cited-commit", "%s (%s)" % (sha[:12], where),
                     "cited commit %s resolves in none of the task's repos — history may "
                     "have been rewritten (a rebase, a squash-merge or a force-push), so "
                     "the record points at a commit nobody can now read. Re-cite it from "
                     "the surviving history, or supersede the entry that names it"
                     % sha[:12])
            for sha, where, state in states if state is False]


# -- check 7: the health metric --------------------------------------------------

def health(task, now=None):
    """The numbers that say whether this task is UNDER-RECONCILED: how many decisions
    are current, how many characters they cost the digest, the longest single one, and
    how long since the last heal.

    `new_since_heal` counts decisions appended since the last heal — decisions the
    stamp has never seen. It is None, NOT the total, when the task has never been
    healed: reporting all 97 decisions of a never-reconciled task as "97 new since the
    last heal" is a number that cannot be acted on and reads as an alarm about the last
    week's work. `never_healed` says which of the two you are looking at, and the
    surfaces word themselves accordingly."""
    now = time.time() if now is None else now
    entries = task.get("decisions") or []
    live = _dec.live(entries)
    lengths = [len(_dec.text(e)) for _i, e in live]
    last = task.get("last_heal_ts")
    try:
        last = float(last) if last else None
    except (TypeError, ValueError):
        last = None
    at_last = task.get("decisions_at_last_heal")
    try:
        at_last = int(at_last)
    except (TypeError, ValueError):
        at_last = 0
    return {
        "decisions_total": len(entries),
        "decisions_current": len(live),
        "decisions_replaced": len(entries) - len(live),
        "chars": sum(lengths),
        "longest": max(lengths) if lengths else 0,
        "average": (sum(lengths) // len(lengths)) if lengths else 0,
        "pinned": _dec.pinned_count(entries),
        "last_heal_ts": last,
        "last_heal_kind": task.get("last_heal_kind"),
        "last_heal_note": task.get("last_heal_note"),
        "never_healed": last is None,
        "since_heal": (now - last) if last else None,
        "new_since_heal": (None if last is None
                           else max(0, len(entries) - at_last)),
    }


# -- check 8: stale steps --------------------------------------------------------

def stale_steps(task):
    """ACTIVE steps that DECLARE THEMSELVES dead, and the verb that retires them.

    This used to say "REPORT ONLY: steps have no supersede verb", which named a real
    gap and left the reader with no honest move: ticking a stale step done is a lie,
    deleting it destroys the record, and adding a warning step about it is the
    anti-pattern one real task already contained. `update --step-supersede <n>` closed
    that gap, so the finding names it.

    DESCRIBING STALENESS IS NOT DECLARING IT, and the language alone cannot tell the two
    apart. So TWO conditions must both hold, exactly as in check 3, through the same
    guard (`declaring_hits`):

      1. the step carries staleness language, and
      2. the language DECLARES — it opens the step, a line or a clause, or it is the
         predicate of one ("this step is stale", "steps 3/4/5 above are STALE", a
         READ-ME-FIRST warning about other steps, a bare "do not execute").

    A keyword sitting mid-sentence as an adjective on some other noun is the step
    talking about something else and is NOT a finding: a file to delete (`delete stale
    tracked BRIEF-x.md`), the ancestor a heal already superseded (`the names in the
    superseded ancestor are REJECTED` — that step IS the correction), a dead end it is
    warning you away from (`WS-S3 was a REJECTED dead end`). Those three were what a
    fully-healed task had left, and they kept `Heal due?` on YES with nothing to do.

    Already-superseded steps are SKIPPED — they are off the active checklist, and
    re-reporting a step that was just retired would make a freshly-healed task read as
    dirty (the same lesson as the merge summary that used to re-trip check 3)."""
    out = []
    for i, s in _steps.live(task.get("steps")):
        hits = declaring_hits(_steps.text(s), STALE_STEP_LANGUAGE)
        if hits:
            out.append(_finding(
                "stale-step", "step %d" % i,
                "reads as stale (%s) — retire it with `update --step-add '<the "
                "corrected step>' --step-supersede %d` (the step added in the same call "
                "is recorded as its replacement). It leaves the checklist and BOTH sides "
                "of the n/m counter, keeps its text in history, and `--step-restore %d` "
                "undoes it. Do not tick it done: nobody did it"
                % (", ".join("%r" % h for h in hits), i, i)))
    return out


# -- proposals, not findings: merge candidates ------------------------------------
#
# The dry run tells the reconciler to "MERGE what is TRUE BUT NO LONGER LOAD-BEARING"
# and then leaves them to go and find them. On one real 99-decision task a human found
# all sixteen — and found them MECHANICALLY, by matching how each entry opens:
# `<x.y.z> SHIPPED`, `MY PROCESS ERROR …`, `Acked the …`, `SCRUB EXECUTION …`. Anything
# a reader spots by matching a prefix is something this scan can offer for free.
#
# OFFER, NEVER PERFORM. Which decisions really collapse — and what the one surviving
# summary should SAY — is judgment, and a wrong merge is worse than a missed one: it
# writes a false consolidation into the record, where it reads as reconciled fact. So
# nothing here ever becomes an op: `merge_candidates` output is read by a human and by
# nothing else.
#
# NOT THE SAME THING AS `merge_clusters` (layer 2), and deliberately so. That one is
# narrow and SURE — a named signature, or four shared leading words — because `--apply`
# performs it; this one is wide and UNSURE, because a person reads it. A version-shaped
# group will often show up in both, which is fine: the plan says what the machine will
# do, this says what is worth looking at. Neither claims the other's authority.
#
# AND A CANDIDATE IS NOT A DEFECT. These never join `findings`, never touch the issue
# count, and can never make `Heal due?` true on their own. A well-reconciled task
# carrying four release records is not broken, and a scan that said it was would be the
# cry-wolf failure this module has already made four times over.

SHAPE_WORDS = 3            # significant leading words that make a leading-shape signature
MERGE_CANDIDATE_MIN = 3    # current decisions sharing a shape before it is worth proposing

# Words too common to carry meaning in a leading-shape or stem signature.
_STOPWORDS = frozenset((
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from", "in", "is",
    "it", "its", "of", "on", "or", "so", "that", "the", "then", "this", "to", "was",
    "were", "will", "with"))

# A version-like OPENING: `2.13.1 SHIPPED …`, `v2.13 …`. The number itself is dropped
# from the signature on purpose — it is the one thing every release record has a
# DIFFERENT value of, and the shape is what they share.
_VERSION_LEAD = re.compile(r"^\W*v?\d+\.\d+(?:\.\d+)?\b")


def _significant_words(text):
    """The lowercase words of `text` that carry meaning — stopwords and one-letter
    fragments dropped. Digits never survive `[a-z]+` at all, which is deliberate: an
    iteration NUMBER must not split a cluster that is otherwise one shape."""
    return [w for w in re.findall(r"[a-z]+", (text or "").lower())
            if w not in _STOPWORDS and len(w) > 1]


def leading_shape(text):
    """The SHAPE a decision's opening takes, as a short label, or None when the text is
    too thin to fingerprint safely.

    TWO shapes, both of them what the human eye actually matched on the real task:

      * a VERSION-LIKE PREFIX plus the first word after it — `<version> shipped`. One
        word is enough there, because opening with a release number is already a strong
        signal on its own that the entry is a release record.
      * otherwise the first SHAPE_WORDS significant words — `my process error`, `scrub
        execution recorded`. With no version to lean on, more words have to agree
        before two decisions sharing an opening means anything."""
    body = (text or "").strip()
    if not body:
        return None
    m = _VERSION_LEAD.match(body)
    if m:
        words = _significant_words(body[m.end():])
        return ("<version> " + words[0]) if words else None
    words = _significant_words(body)
    if len(words) < SHAPE_WORDS:
        return None
    return " ".join(words[:SHAPE_WORDS])


def merge_candidates(task, minimum=MERGE_CANDIDATE_MIN):
    """CURRENT decisions that share a leading shape, as `[{"shape", "indices"}, …]` in
    first-appearance order. PROPOSALS — see the note above; nothing here is a finding
    and nothing here is ever applied.

    `minimum` or more before a shape counts: two decisions can open the same way and
    still be genuinely distinct. REPLACED decisions are excluded by `live()`, which is
    what stops a group from being proposed twice — once a reconciler merges it, the
    members carry `merged_into` and only the one summary is left standing."""
    groups, order = {}, []
    for i, e in _dec.live(task.get("decisions")):
        shape = leading_shape(_dec.text(e))
        if not shape:
            continue
        if shape not in groups:
            groups[shape] = []
            order.append(shape)
        groups[shape].append(i)
    return [{"shape": s, "indices": groups[s]}
            for s in order if len(groups[s]) >= minimum]


# -- MERGE AT SCALE: by SUBJECT, and by whether the work is FINISHED ----------------
#
# THE SHAPE TIER'S TWO BLIND SPOTS. `leading_shape` matches how an entry OPENS, which is
# what a human's eye did on a 99-decision task — and it therefore knows nothing about two
# things that decide whether a merge is right:
#
#   * WHETHER THE WORK IS DONE. "True but no longer load-bearing" is a statement about the
#     WORK, not about the words. A decision that says "step 29: hold the rename until the
#     export lands" is load-bearing right up to the moment step 29 is ticked, and the shape
#     tier cannot see that moment arrive. So a decision whose SUBJECT STEPS are all ticked
#     or superseded is tagged `completed-subject`: the checklist itself says the work it
#     records is finished.
#   * WHAT THE ENTRY IS ABOUT. Four decisions about release 2.13.1 that open four different
#     ways share nothing the shape tier can match, while two unrelated `MY PROCESS ERROR`
#     entries about different mistakes match perfectly. Subject signals invert that:
#     overlapping STEP references, a shared RELEASE VERSION, a shared PR or story number.
#     Each of those is a thing the entries are ABOUT rather than a way they are phrased.
#
# THE SHAPE TIER STAYS, as the secondary one. It catches the process-error and scrub-
# iteration families that carry no version, no PR and no step reference at all, and on the
# task where it was measured it found all sixteen. Subject groups render FIRST because they
# are the stronger evidence; a group that appears in both tiers is not a contradiction — one
# says "these are about the same thing", the other "these are phrased the same way".
#
# AND NOTHING HERE IS EVER PERFORMED. Same rule as the shape tier, for the same reason: a
# wrong merge writes a false consolidation into the record, where it then reads as
# reconciled fact. TWO members are enough to PROPOSE (unlike the shape tier's three),
# because a shared subject is direct evidence and a shared opening phrase is not.

SUBJECT_CANDIDATE_MIN = 2      # decisions about one subject before it is worth proposing
SUBJECT_LABEL_MAX = 3          # signals named in a group's label; the rest are "+N more"
STEP_RANGE_MAX = 50            # widest `steps N-M` span expanded; see `step_refs`

# A STEP-shaped reference in prose: `step 29`, `steps 3-6`, `steps 3, 4 and 5`. Same
# discipline as `_DECISION_WORD_REF` and for the same reason — the explicit word is
# required, so a bare number is never a step reference. A decision's prose is full of bare
# numbers (chars, versions, percentages, dates), and reading one of them as "step 4" would
# make a decision's merge candidacy depend on a character count.
_STEP_WORD_REF = re.compile(
    r"\b(?:steps?)\s+#?\d+(?:\s*(?:-|–|—|to|,|and|&)\s*#?\d+)*", re.I)

# Inside one matched run: a number, optionally introduced by a RANGE separator. `steps 3-6`
# means four steps; `steps 3, 6` means two.
_STEP_PIECE = re.compile(r"(?P<range>-|–|—|\bto\b)?\s*#?(?P<n>\d+)", re.I)

# The subject signals, each one a thing an entry is ABOUT.
_VERSION_TOKEN = re.compile(r"\b\d+\.\d+\.\d+\b")
_WORK_ITEM_REF = re.compile(
    r"\b(?:pull request|pr|story|stories|issue|work item)\s*#?(?P<n>\d{1,7})\b", re.I)


def step_refs(text, total=None):
    """The step indices this prose EXPLICITLY names, as ints, ascending.

    `steps 3-6` expands to 3, 4, 5, 6 — that is the shape a decision actually uses to name
    a run of checklist items. The expansion is capped at STEP_RANGE_MAX: `steps 1-9999` is
    a typo or a page reference, and expanding it would hand the caller ten thousand indices
    to look up. Out-of-range numbers are dropped when `total` is known, which is the same
    gate `decision_refs` applies — a number past the end of the checklist is not a step."""
    out = []
    for m in _STEP_WORD_REF.finditer(text or ""):
        prev = None
        for piece in _STEP_PIECE.finditer(m.group(0)):
            n = int(piece.group("n"))
            if (piece.group("range") and prev is not None
                    and prev < n <= prev + STEP_RANGE_MAX):
                out.extend(range(prev + 1, n + 1))
            else:
                out.append(n)
            prev = n
    seen, keep = set(), []
    for n in out:
        if n < 1 or n in seen:
            continue
        if total is not None and n > total:
            continue
        seen.add(n)
        keep.append(n)
    return sorted(keep)


def completed_subjects(task):
    """Live decisions whose SUBJECT STEPS are ALL finished, as `{index1: [step refs]}`.

    "Finished" is DONE **or** SUPERSEDED, and both belong: a ticked step is work that
    happened, a superseded one is work the checklist has retired, and in neither case is
    the decision that recorded the plan for it still load-bearing.

    A decision naming NO step is absent from this — never present-with-an-empty-list. The
    signal is "the work this entry is about is finished", and a decision that names no work
    supports no such claim; treating "nothing referenced" as "everything referenced is
    done" would tag the whole log."""
    steps = task.get("steps") or []
    if not steps:
        return {}
    finished = set()
    for i, s in enumerate(steps, 1):
        if _steps.is_superseded(s) or _steps.is_done(s):
            finished.add(i)
    out = {}
    for i, e in _dec.live(task.get("decisions")):
        refs = step_refs(_dec.text(e), total=len(steps))
        if refs and all(n in finished for n in refs):
            out[i] = refs
    return out


def subject_signals(text, total_steps=None):
    """What a decision is ABOUT, as a set of short labels — `step 29`, `version 2.13.1`,
    `PR 1234`.

    THREE signal families, and each one is a thing the record names explicitly rather than
    a similarity between two texts. Nothing is inferred from wording: two entries share a
    signal only when they name the same step, the same release or the same work item."""
    body = text or ""
    out = set()
    for n in step_refs(body, total=total_steps):
        out.add("step %d" % n)
    for m in _VERSION_TOKEN.finditer(body):
        out.add("version %s" % m.group(0))
    for m in _WORK_ITEM_REF.finditer(body):
        out.add("PR/story %s" % m.group("n"))
    return out


def subject_candidates(task, minimum=SUBJECT_CANDIDATE_MIN):
    """CURRENT decisions grouped by a SHARED SUBJECT, as
    `[{"signal", "signals", "indices", "tags"}, …]`, lowest member first.

    Grouping is TRANSITIVE — a decision naming version 2.13.1 and step 4, a second naming
    2.13.1, and a third naming step 4 are one group. That is deliberate and it is what the
    shape tier cannot do: they are three entries about one piece of work, and proposing
    them as three groups of two would leave a reader merging the same subject twice.

    `tags` carries `completed-subject` when every member that names a step has ALL of its
    steps finished — the strongest form of "true but no longer load-bearing" the record can
    state on its own, because the checklist says so rather than a reader inferring it."""
    live = _dec.live(task.get("decisions"))
    total_steps = len(task.get("steps") or [])
    sigs = {}
    for i, e in live:
        found = subject_signals(_dec.text(e), total_steps=total_steps)
        if found:
            sigs[i] = found
    if len(sigs) < 2:
        return []
    by_signal = {}
    for i in sorted(sigs):
        for sig in sigs[i]:
            by_signal.setdefault(sig, []).append(i)
    parent = dict((i, i) for i in sigs)

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for members in by_signal.values():
        for other in members[1:]:
            # The LOWEST index wins the root, so a group's identity does not depend on
            # which signal happened to be visited first.
            ra, rb = find(members[0]), find(other)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)
    grouped = {}
    for i in sorted(sigs):
        grouped.setdefault(find(i), []).append(i)
    finished = completed_subjects(task)
    out = []
    for root in sorted(grouped):
        idxs = grouped[root]
        if len(idxs) < minimum:
            continue
        # The signals that actually LINK the group — one carried by two or more members.
        # A member's private signals are why it is worth reading, not why it is grouped.
        shared = sorted(s for s, members in by_signal.items()
                        if len([i for i in members if i in idxs]) >= 2)
        stepped = [i for i in idxs
                   if any(s.startswith("step ") for s in sigs[i])]
        tags = []
        if stepped and all(i in finished for i in stepped):
            tags.append("completed-subject")
        label = ", ".join(shared[:SUBJECT_LABEL_MAX])
        if len(shared) > SUBJECT_LABEL_MAX:
            label += " (+%d more)" % (len(shared) - SUBJECT_LABEL_MAX)
        out.append({"signal": label or "shared subject", "signals": shared,
                    "indices": idxs, "tags": tags})
    return out


def candidate_groups(task, subject=None, shape=None):
    """EVERY merge candidate group — subject tier first, shape tier second — normalised to
    `{"tier", "label", "indices", "tags"}`.

    One reader, because three surfaces now need "is there anything outstanding to merge?":
    the `--candidates` view, the grew-with-candidates check, and the scan's own rendering.
    Three answers to that question would eventually disagree, and the one that made a heal
    DUE would be the one nobody could reproduce.

    `subject` / `shape` let a caller that has already grouped pass the answers in — `scan`
    needs both tiers for its report AND for the check, and grouping twice is the same waste
    the `links` and `commits` reuse above avoids."""
    subject = subject_candidates(task) if subject is None else subject
    shape = merge_candidates(task) if shape is None else shape
    out = []
    for g in subject:
        out.append({"tier": "subject", "label": g.get("signal"),
                    "indices": g.get("indices") or [], "tags": g.get("tags") or []})
    for g in shape:
        out.append({"tier": "shape", "label": g.get("shape"),
                    "indices": g.get("indices") or [], "tags": []})
    return out


# -- the SIZE OBJECTIVE, and the one finding this work can make -----------------------
#
# THE NUMBER WITH NOTHING TO COMPARE IT AGAINST. The health line has always printed the
# digest's char total, and a reader has never been able to tell 96,000 chars that are
# 40,000 down from last week apart from 96,000 that are 40,000 up. Both read as one large
# number. So the stamp now snapshots it, and the scan reports `now / at last heal / delta`.
#
# GREW-WITH-CANDIDATES-OUTSTANDING IS A FINDING, and it is the ONLY thing in the merge work
# that can make a heal due — everything else here is a proposal, because choosing a
# surviving summary is judgement. This one is a finding for the same reason a re-fragmented
# consolidation is: it is not about what any single entry says, it is about the record as a
# whole moving the wrong way while a named, mechanical way to move it the right way sat
# unused. Growth on its own is NOT a defect (a working task records work), and candidates on
# their own are NOT a defect (nobody has ruled on them). The conjunction is the signal.
#
# IT NEEDS A BASELINE, and a task without one is UNKNOWN rather than zero — `accrual`'s rule
# and for the same reason: every task written before this shipped has no snapshot, so a zero
# delta would read as "it has not grown" on precisely the tasks nobody has measured.

CHARS_AT_LAST_HEAL = "chars_at_last_heal"     # additive stamp key; see `stamp_healed`


def size_objective(task):
    """The digest's char total now, at the last heal, and the delta — as
    `{"now", "at_last_heal", "delta", "known"}`.

    `known` is False when no stamp recorded a baseline, in which case both `at_last_heal`
    and `delta` are None. Never zero: see the note above."""
    now = _dec.total_chars(task.get("decisions"))
    raw = task.get(CHARS_AT_LAST_HEAL)
    try:
        base = int(raw)
    except (TypeError, ValueError):
        return {"now": now, "at_last_heal": None, "delta": None, "known": False}
    return {"now": now, "at_last_heal": base, "delta": now - base, "known": True}


def grew_with_candidates(task, groups=None, size=None):
    """The ONE finding in the merge work: the digest GREW since the last heal while merge
    candidates sat outstanding.

    Silent without a baseline (unknown is never a finding), silent when the digest did not
    grow, and silent when there is nothing to merge. One finding per task, not per group:
    the defect is the direction the whole record moved."""
    size = size if size is not None else size_objective(task)
    if not size.get("known") or (size.get("delta") or 0) <= 0:
        return []
    groups = groups if groups is not None else candidate_groups(task)
    if not groups:
        return []
    members = sorted(set(i for g in groups for i in (g.get("indices") or [])))
    return [_finding(
        "grew-with-candidates", "digest",
        "the digest has GROWN by %d chars since the last heal (%d → %d) while %d merge "
        "candidate group(s) sat outstanding — decisions %s. Neither half is a defect on "
        "its own: a working task records work, and a group nobody has ruled on is not "
        "wrong. Together they say the record is getting more expensive to brief in exactly "
        "the place a named verb was waiting. Read the groups with `heal --candidates`, "
        "write the ONE surviving summary yourself, then `heal --merge <n1,n2,…> --into "
        "<n>`. It is not proposed for you: a wrong merge writes a false consolidation into "
        "the record"
        % (size.get("delta"), size.get("at_last_heal"), size.get("now"), len(groups),
           ", ".join(str(i) for i in members)))]


# -- check 9: a consolidation that has RE-FRAGMENTED -------------------------------
#
# This EXTENDS the shape grouping above, and it is the one place in this module where a
# shape match becomes a FINDING rather than a proposal. The reason is precise.
#
# THE INCIDENT. A real task scanned CLEAN on all eight checks. The judgement half then
# found decision N reading "CONSOLIDATED — THE 2.7.0-2.11.0 RELEASE LINE … (replaces the
# five per-release records)" — and FOUR more release-shaped decisions appended after it
# over the following day. Nobody undid the merge. The shape simply grew back around the
# entry that had just declared itself the single record of it, and nothing noticed.
#
# WHY THIS IS A DEFECT AND FOUR RELEASE RECORDS ARE NOT. `merge_candidates` offers a
# group of four release records as a PROPOSAL, because a task carrying four of them is
# not broken — nobody has ruled on them yet. Here somebody HAS: a previous pass wrote
# down that this subject is one entry, and the log now contradicts that ruling. The
# record says two different things about how many entries the subject has, which is the
# same class of wrong as an unlinked supersession — prose and structure disagreeing —
# and it is exactly what the digest hands the next fresh session. So it counts as an
# issue and it may make a heal due.
#
# WHAT IT STILL DOES NOT DO IS PROPOSE THE MERGE. Which entries collapse and what the one
# surviving summary SAYS is judgement, and a wrong merge writes a false consolidation
# into the record — the very thing this check exists to catch. The finding names the
# numbers and the command; a human (or the model driving the pass) chooses the words.

# What a decision says when it claims to BE the consolidation of several records. Mixed
# parts of speech on purpose — `consolidated` opens one, `replaces` is what one does,
# `reconciled record` is what one IS (and is the wording `_merge_summary` generates, so a
# summary `--apply` itself wrote is recognisable as the consolidation it is). Edit HERE.
CONSOLIDATION_LANGUAGE = ("consolidated", "consolidates", "consolidation",
                          "reconciled record", "replaces", "replacing", "in place of")

# Words that may stand in front of that vocabulary and leave it a DECLARATION about this
# entry: the self-declaring set (a form of "to be", `this`/`these`) plus the nouns an
# entry uses for ITSELF — `one reconciled record of 5 decisions`, `this decision
# consolidates 4, 9 and 17`. Articles are deliberately EXCLUDED, unlike the memo noun
# set: "a consolidation of the release trail" is a decision TALKING ABOUT one, and
# telling those two apart is this vocabulary's whole job. Edit the list HERE.
CONSOLIDATION_QUALIFIERS = SELF_DECLARING_QUALIFIERS | frozenset((
    "one", "decision", "entry", "record", "summary"))


def declares_consolidation(text):
    """True when a decision's own text says IT is the one record of several others —
    not merely that consolidation is a thing that happened somewhere.

    The declare-vs-describe rule with a fifth VOCABULARY rather than a fifth heuristic
    (see the guard section above). `a consolidation of the release trail` and `a wrong
    merge writes a false consolidation into the record` both DESCRIBE one — the word in
    front makes the keyword an adjective on the next noun — while `CONSOLIDATED — …`,
    `this decision consolidates 4, 9 and 17` and `one reconciled record of 5 decisions`
    each DECLARE one."""
    return bool(declaring_hits(text, CONSOLIDATION_LANGUAGE,
                               qualifiers=CONSOLIDATION_QUALIFIERS))


def consolidated_shapes(entries, index):
    """The leading shapes the consolidation at `index` COVERS, as a set. Empty when
    `index` is not a consolidation at all, which is what makes it the gate.

    TWO sources, and each one justifies itself separately:

      * STRUCTURE — every decision MARKED as merged into `index`. That is proof rather
        than inference: a previous pass folded those entries into this one, so their
        shapes are precisely what it folded, and no reading of any text is required.
      * PROSE — the earlier decisions this entry NAMES (`decision_refs`), and only when
        the entry also DECLARES itself a consolidation. A hand-written "replaces
        decisions 4, 9, 17, 23 and 30" makes the same claim without the marks; requiring
        the declaration is what stops a decision that merely MENTIONS consolidation
        beside a couple of decision numbers from being read as one.

    A shape of None (an entry too thin to fingerprint) is dropped rather than stored, so
    a thin decision can never match a thin decision by both having no shape."""
    shapes = set()
    for e in (entries or []):
        rep = _dec.replacement(e)
        if rep and rep[0] == _dec.REPLACED_MERGED and index in rep[1]:
            shapes.add(leading_shape(_dec.text(e)))
    body = _dec.text(entries[index - 1]) if 1 <= index <= len(entries or []) else ""
    if declares_consolidation(body):
        for n in decision_refs(body, own_index=index, total=len(entries or [])):
            shapes.add(leading_shape(_dec.text(entries[n - 1])))
    shapes.discard(None)
    return shapes


def refragmented_consolidations(task):
    """Consolidations the log has grown back around, as findings.

    Reported when a CURRENT decision consolidates a shape (`consolidated_shapes`) and any
    CURRENT decision added AFTER it opens with that same shape. Both sides read through
    `_dec.live`, so an already-merged sibling does not count — once a reconciler folds
    the strays in, this goes quiet, which is the whole point: a finding that survives its
    own fix is a finding nobody trusts.

    It fires on the FIRST newer sibling, deliberately. There is no "one is fine, two is a
    pattern" threshold here because the defect is not the volume — it is that the record
    contradicts a ruling it already recorded, and one contradiction is enough to hand the
    next session two answers to the same question."""
    entries = task.get("decisions") or []
    live = _dec.live(entries)
    out = []
    for i, _e in live:
        shapes = consolidated_shapes(entries, i)
        if not shapes:
            continue
        newer = [j for j, other in live
                 if j > i and leading_shape(_dec.text(other)) in shapes]
        if not newer:
            continue
        idxs = ",".join(str(n) for n in [i] + newer)
        out.append(_finding(
            "refragmented", "decision %d" % i,
            "the consolidation at %d has re-fragmented: %d newer decision(s) share the "
            "shape it consolidated (%s) — decision(s) %s. It was written as the ONE "
            "record of that subject, so the log now says two contradictory things about "
            "how many entries that subject has, and the digest hands both to every fresh "
            "session. READ them: either write ONE updated summary and `heal --merge %s "
            "--into <n>`, or supersede the consolidation, which no longer describes the "
            "log. The merge is NOT proposed for you — naming the surviving summary is "
            "judgement, and a wrong merge writes a false consolidation into the record"
            % (i, len(newer), ", ".join("%r" % s for s in sorted(shapes)),
               ", ".join(str(n) for n in newer), idxs)))
    return out


# -- check 10: a LIVE STEP that restates a SUPERSEDED decision ---------------------
#
# THE INCIDENT. A real task scanned CLEAN on every check and printed `Heal due? no`,
# while its checklist still carried five LIVE steps naming the two largest work items on
# the task — both of them retired by decisions that same task had already SUPERSEDED. A
# cold session reads the CHECKLIST first, because that is where the next move lives; the
# decision log is where the REASONS live. So the record was internally consistent and
# actively misleading at the same instant, and days of retired work sat there reading as
# the plan.
#
# WHY NO EXISTING CHECK COULD SEE IT. Every other check cross-references two things ON
# THE SAME OBJECT — a decision's prose against its own marks, a memo against its declared
# target, a step against its own staleness language. Nothing ever compared a STEP against
# a DECISION, so a step could go on instructing work that a superseded decision had
# retired without contradicting anything a check reads. "Stale steps clean" (check 8)
# means STRUCTURALLY stale — the step says so about itself — and a step that is merely
# WRONG NOW says nothing about itself at all.
#
# WHY THIS IS A FINDING AND THE GOAL REVIEW BELOW IS NOT. `merge_candidates`,
# `pinned_review` and `goal_review` are proposals because nothing on the record is wrong:
# four release records are not a defect, a pin is not a defect, an untouched goal is not a
# defect. Here the log ALREADY RULED. A superseded decision is one the task itself marked
# REFUTED, and a live step ordering the refuted work is the record contradicting its own
# ruling — the same class as an unlinked supersession, and it is handed to the next
# session as an INSTRUCTION rather than as background. So it counts as an issue.

# How much of their significant vocabulary a step and a superseded decision must SHARE
# before the step reads as a restatement of it — Jaccard (see `word_overlap`).
#
# UNVALIDATED: REASONED, NOT MEASURED. Every other threshold in this module was tuned
# against a real task; this one could not be, so it is stated as a starting value and
# labelled as one rather than dressed up as a measurement. The reasoning: Jaccard is
# SYMMETRIC, so a short step inside a long decision scores |step| / |decision| even when
# every word of the step is present — 0.5 when the decision is twice the step, 0.33 at
# three times, 0.25 at four. 0.30 therefore fires on a step restating a decision up to
# roughly three times its length, and needs about a third of the combined vocabulary to
# agree in every other case, which unrelated entries on one task do not reach (they share
# a handful of project words against a union in the hundreds). It is deliberately on the
# QUIET side of the trade every check here makes: the failure it accepts is a long
# superseded decision whose restating step goes unreported, and the failure it refuses is
# firing on a checklist nobody needs to touch.
STEP_RESTATEMENT_OVERLAP = 0.30

# Both texts must carry at least this many DISTINCT significant words before their
# overlap means anything. Below that the ratio is noise: two four-word fragments sharing
# three words score 0.6 while telling you nothing, and `_stem` already refuses to
# fingerprint on fewer than STEM_WORDS for the same reason. Six is above the longest
# fragment that can score high by accident and below any real checklist step, and the
# skipped short step is the cheap failure — one missed finding, not a false one.
STEP_RESTATEMENT_MIN_WORDS = 6

# The vocabulary that makes this check STAY SILENT — the union of the two correction
# sets, deliberately read with the OVER-EAGER `matched_language` rather than the
# declare-vs-describe guard. See `steps_restating_superseded` for why the guard is not
# what this check needs.
RESTATEMENT_SILENCE_LANGUAGE = SUPERSESSION_LANGUAGE + STALE_STEP_LANGUAGE


def _jaccard(a, b):
    """`|A ∩ B| / |A ∪ B|` for two word SETS, 0.0 when either side is empty. Split out
    from `word_overlap` so a caller comparing one text against many can tokenize the many
    ONCE — the check below does exactly that."""
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a | b))


def word_overlap(a_text, b_text):
    """How much of two texts' SIGNIFICANT vocabulary is shared, as a Jaccard ratio in
    0..1.

    `_significant_words` is reused rather than reimplemented, and that is the whole point:
    it is already the tokenizer `leading_shape` and `_stem` fingerprint decisions with,
    stopwords and one-letter fragments dropped, digits never matched at all. A second
    tokenizer or a second stopword list would be a second thing to keep in step with the
    first, and the two would drift the first time either was tuned.

    SYMMETRIC, and that cuts both ways. Two texts of similar length that say the same
    thing score high; a SHORT text fully contained in a LONG one scores only the ratio of
    their lengths, so this systematically under-reports a one-line step against a
    paragraph-long decision. That is a known false NEGATIVE and it is the direction this
    module always errs in — see STEP_RESTATEMENT_OVERLAP."""
    return _jaccard(set(_significant_words(a_text)), set(_significant_words(b_text)))


def steps_restating_superseded(task, minimum=STEP_RESTATEMENT_OVERLAP,
                               min_words=STEP_RESTATEMENT_MIN_WORDS):
    """LIVE steps that restate a decision this task has SUPERSEDED — the checklist still
    ordering work the log already retired.

    SUPERSEDED SPECIFICALLY, not merely replaced (`decisions.is_superseded`, the narrow
    accessor). Supersession is the one verb that means "this was WRONG"; a SPLIT decision
    was only reshaped and a MERGED one is still true, so a step restating either is not
    ordering refuted work and must not be reported.

    THE THRESHOLD IS 0.30 JACCARD (STEP_RESTATEMENT_OVERLAP) AND IT IS UNVALIDATED — read
    the constant for the full reasoning, which is the honest short version of "this could
    not be measured". Jaccard is symmetric, so perfect containment of a step inside a
    decision scores the ratio of their lengths: 0.30 catches a step restating a decision
    up to roughly three times its length, and otherwise wants about a third of the
    combined vocabulary to agree — far above the handful of shared project words that
    unrelated entries on one task manage against a union in the hundreds. It errs QUIET,
    which is the trade every check in this module makes, and it is one constant to retune
    when real numbers exist.

    ALREADY-SUPERSEDED STEPS ARE SKIPPED, exactly as `stale_steps` skips them and for the
    same reason: a retired step has left the checklist, so re-reporting it would make a
    freshly-healed task read as dirty.

    HOW THIS AVOIDS THE DECLARE-vs-DESCRIBE FAILURE — and it does NOT do it by asking the
    guard's question. That question ("does this text DECLARE the condition, or merely
    DESCRIBE it?") cannot be asked here, because the condition is not a word in the text
    at all: the two cases this check must tell apart are a step that RESTATES retired work
    and the step written to RECORD that the work was retired, and both legitimately share
    the retired work's whole vocabulary. Text overlap CANNOT separate them — a corrected
    step naming what it replaced looks exactly like the thing it replaced.

    So the answer is to stay silent whenever either reading is possible. Any step carrying
    correction vocabulary at all (RESTATEMENT_SILENCE_LANGUAGE, read with the over-eager
    `matched_language` on purpose — declaring or merely describing, both mean "this step
    is talking about a retirement") is skipped outright. The over-eagerness that made
    `matched_language` useless as a finding condition is exactly what makes it a good
    SILENCER: there it manufactured false positives, here it can only cost false
    negatives, which is the trade every check in this module already chose.

    And what survives that filter is still reported as PROVISIONAL, in words, in the
    finding itself. This check earns its keep by pointing at two entries and saying READ
    THESE TOGETHER; it does not claim to know which of them is the stale one. Being
    honest about that in the detail line is what stops it becoming the fifth
    confidently-wrong check this subsystem has shipped.

    One finding per step, naming the STRONGEST match: a step overlapping three superseded
    decisions is one thing to read, not three."""
    entries = task.get("decisions") or []
    dead = []
    for i, e in enumerate(entries, 1):
        if not _dec.is_superseded(e):
            continue
        words = set(_significant_words(_dec.text(e)))
        if len(words) >= min_words:
            dead.append((i, words))
    if not dead:
        return []
    out = []
    for i, s in _steps.live(task.get("steps")):
        body = _steps.text(s)
        words = set(_significant_words(body))
        if len(words) < min_words:
            continue
        if matched_language(body, RESTATEMENT_SILENCE_LANGUAGE):
            continue
        best, score = None, 0.0
        for n, other in dead:
            ratio = _jaccard(words, other)
            if ratio > score:
                best, score = n, ratio
        if best is None or score < minimum:
            continue
        out.append(_finding(
            "step-restates-superseded", "step %d" % i,
            "restates decision %d, which this task has already SUPERSEDED — they share "
            "%d%% of their significant vocabulary, and nothing else on the record "
            "contradicts either, so every other check reads this task as clean. "
            "PROVISIONAL, and it says so on purpose: text overlap cannot tell a step that "
            "still ORDERS the retired work from the step written to RECORD that it was "
            "retired, because both name the same thing. READ THE TWO TOGETHER. If the "
            "step really does still order refuted work, retire it with `update "
            "--step-add '<the corrected step>' --step-supersede %d` (the step added in "
            "the same call is recorded as its replacement, and `--step-restore %d` undoes "
            "it). If the step IS the correction, leave it exactly where it is — it is "
            "doing its job"
            % (best, int(round(score * 100)), i, i)))
    return out


# -- informational: the pinned set, for re-review ---------------------------------
#
# A PINNED decision leads the digest of EVERY session (`decisions.digest_order`), so
# stale content in one is the most expensive kind this record can hold: it is re-read on
# every resume, by every session, until a human happens to notice. On one real task a
# pinned decision still named two codenames a later decision had retired, and it had
# been briefing every session with them for days. Nothing in the scan would ever have
# surfaced that — no check looks at whether a decision is still ACCURATE, only at
# whether something else contradicts it structurally.
#
# So the scan names the pinned set and says why it matters. INFORMATIONAL, exactly like
# the health metric: being pinned is not a defect, so this never inflates the issue
# count and never makes `Heal due?` true on its own.

PINNED_PREVIEW_CHARS = 120     # enough to recognise which decision a line is about


def decision_ages(task, now=None):
    """`{index1: age in seconds}` for every decision the EVENT FEED can date.

    A decision carries no timestamp of its own — the log is a list of strings and that
    shape is frozen — so the only honest source is the `decision` event each append
    posted. That feed is bounded and its stored text is capped, so a decision older than
    the window (or written by a path that posted no event) simply has NO age here, and
    the surfaces say so rather than inventing one.

    Events are paired to decisions oldest-first by text prefix, ONE apiece, so two
    entries that open the same way cannot both claim the same event."""
    now = time.time() if now is None else now
    pending = [(i, _dec.text(e).strip())
               for i, e in enumerate(task.get("decisions") or [], 1)]
    ages = {}
    for ev in (task.get("events") or []):
        if (ev.get("kind") or "") != "decision":
            continue
        et = (ev.get("text") or "").strip()
        if not et:
            continue
        for pos, (i, body) in enumerate(pending):
            if not body or not (body.startswith(et) or et.startswith(body)):
                continue
            try:
                ages[i] = max(0.0, now - float(ev.get("ts")))
            except (TypeError, ValueError):
                pass
            pending.pop(pos)
            break
    return ages


def pinned_review(task, now=None):
    """The PINNED current decisions as `[{"index", "age", "chars", "preview"}, …]`,
    oldest-first. `age` is None when the event feed can no longer date the append —
    unknown, which is not the same claim as new.

    Returns [] when nothing is pinned, which is the common and perfectly healthy case:
    this is a prompt to RE-READ, not a finding. That case also short-circuits the event
    scan entirely — this runs on the SessionStart nag path, which must stay cheap."""
    pins = [(i, e) for i, e in _dec.live(task.get("decisions")) if _dec.is_pinned(e)]
    if not pins:
        return []
    now = time.time() if now is None else now
    ages = decision_ages(task, now=now)
    out = []
    for i, e in pins:
        body = _dec.text(e)
        flat = " ".join(body.split())
        out.append({"index": i, "age": ages.get(i), "chars": len(body),
                    "preview": (flat[:PINNED_PREVIEW_CHARS - 1] + "…"
                                if len(flat) > PINNED_PREVIEW_CHARS else flat)})
    return out


# -- a PROPOSAL, not a finding: the goal line and what has landed since ------------
#
# THE INCIDENT, the same clean scan as check 10. The task's GOAL LINE — the one field
# that says what DONE looks like — described a mission that had already been accomplished,
# and had said so for days while every check reported clean. It could not have been
# otherwise: the goal contradicted nothing, because nothing else on the task claims to say
# what done looks like, so there is no second thing to cross-reference it against. The
# only evidence that a goal has been overtaken lives in the DECISIONS written after it,
# and reading those is judgement.
#
# SO THIS COUNTS, AND NOTHING ELSE. How many decisions have landed since the goal was last
# written. That is a measure of how much evidence the goal has NOT been checked against,
# and it is the only honest number available.
#
# A CANDIDATE IS NOT A DEFECT — the rule stated above `merge_candidates`, and this section
# obeys it to the letter: never a finding, never counted as an issue, and it can NEVER
# make `Heal due?` true on its own. A goal is SUPPOSED to outlive the decisions that
# pursue it; a task can carry one unchanged across forty of them and be exactly right. An
# untouched goal is a reason to LOOK. Modelled on `pinned_review`, which is the same
# shape and the same instruction: re-read this and confirm it is still true.
#
# NO BASELINE MEANS "CANNOT BE COUNTED", NEVER ZERO. This is `accrual`'s own rule and it
# is here for the same reason: a zero reads as "nothing has happened since", while the
# truth is "nobody recorded when the goal was written". Every task that existed before
# this shipped takes that path, so it is the COMMON case, not an edge one.
#
# AND THEN THE COUNT WAS GIVEN TEETH. Counting and never acting made this section a line a
# reader could skip forever: a goal nobody had looked at across forty decisions printed the
# same shape of row as one written this morning, and the row said in its own heading that it
# could never make a heal due. So `due()` now has a limb for it (`goal_review_due`,
# default 25) — worded as the count, because the count IS the evidence.
#
# IT IS STILL NOT A FINDING. The row stays a PROPOSAL: nothing on the record is WRONG, and
# `mechanical_line` must not inflate. What changed is only that a large enough count is a
# reason to run the pass, which is what "due" has always meant.
#
# RE-READING IS THE SERVICE, NOT REWRITING. The obvious cheap reset — treat a `--mark-healed
# --note` as proof the goal was re-read — was deliberately REFUSED: a mark-healed note says
# somebody read the record, not that they read THIS LINE and ruled it still true, and a
# stamp that silently claims the second is exactly the class of lie the heal stamp itself was
# added to kill. So there is an explicit verb, `heal --goal-reviewed`, and the mark-healed
# path leaves this count alone.

GOAL_TOUCHED_FIELD = "goal_touched"    # {"ts": …, "decisions": N} — the write-time baseline
GOAL_REVIEWED_FIELD = "goal_reviewed"  # {"ts": …, "decisions": N} — the RE-READ baseline
GOAL_PREVIEW_CHARS = 200               # enough to read the goal, not enough to reprint a page


def goal_review_due():
    """How many decisions may land after the goal was last written or re-read before that
    alone makes a heal due. `heal_goal_review_due` in config, `TASK_STATION_HEAL_GOAL_
    REVIEW_DUE` in the environment, else GOAL_REVIEW_DUE.

    POSITIVE-ONLY, and fail-open on anything unreadable — the contract every checker
    tunable keeps (`config._positive_number`). A zero would make every task with a goal
    permanently due, which is the always-on alarm this module exists to stop."""
    try:
        n = int(_config.heal_goal_review_due())
        return n if n > 0 else GOAL_REVIEW_DUE
    except Exception:
        return GOAL_REVIEW_DUE


def stamp_goal_touched(task, now=None):
    """Record that the GOAL LINE was just rewritten: the moment, and how many decisions
    the log held at that moment. Does NOT save — the caller persists, the same contract
    `stamp_healed` keeps.

    WHY A WRITE-TIME SNAPSHOT AND NOT A SEARCH. Nothing else on a task can answer this.
    `updated_ts` moves for every field, so it cannot say when the GOAL moved; the event
    feed is bounded, so on a busy task the goal's own write ages out of it; and the goal
    is a single overwritten string with no history of its own. Exact arithmetic against a
    snapshot is the rule `_recorded_counts` and `save._written_counts` already follow, and
    for the same reason.

    ADDITIVE KEY, which is what the frozen-format rule allows: a task written by an older
    version simply has none, and `goal_review` reports it as uncountable rather than
    inventing a zero."""
    task[GOAL_TOUCHED_FIELD] = {"ts": time.time() if now is None else now,
                                "decisions": len(task.get("decisions") or [])}


def stamp_goal_reviewed(task, now=None):
    """Record that the GOAL LINE was just RE-READ and found still true — the moment, and how
    many decisions the log held at that moment. Does NOT save; the caller persists.

    WHY THIS IS A SEPARATE FIELD FROM `goal_touched`. Rewriting the goal and re-reading it
    are both reviews, and they are not the same event: one changes the sentence, the other
    ratifies it. Writing the review into `goal_touched` would make the record say the goal
    was REWRITTEN when it was not, and that field is read by the checker's goal-drift check
    as the age of the goal ITSELF. So the two are stored apart and `goal_review` takes the
    LATER of them: whichever happened last is when a human last had eyes on that line.

    ADDITIVE KEY, like every other stamp here — an older task has none and reads as
    never-reviewed."""
    task[GOAL_REVIEWED_FIELD] = {"ts": time.time() if now is None else now,
                                 "decisions": len(task.get("decisions") or [])}


def _goal_snapshot(task, field):
    """One goal baseline as `(decisions, ts)`, each None when absent or garbled. Shared by
    the two fields so a junk value in either degrades the same way."""
    snap = task.get(field)
    if not isinstance(snap, dict):
        return None, None
    try:
        count = int(snap.get("decisions"))
    except (TypeError, ValueError):
        count = None
    try:
        stamped = float(snap.get("ts")) or None
    except (TypeError, ValueError):
        stamped = None
    return count, stamped


def goal_review(task, now=None):
    """The goal line and what has accrued against it, as a dict — `{}` when there is
    nothing honest to say.

    THREE cases, each worded as what it actually is:

      * NO GOAL (never set, or cleared) — `{}`, and the surfaces print nothing. "0
        decisions since the goal was last written" about a goal nobody wrote is a number
        about nothing, and a section that appears on every task regardless is one readers
        learn to skip.
      * KNOWN — a baseline exists, so `since` is exact subtraction.
      * UNKNOWN — no baseline, or a garbled one. `since` is None and `known` is False, so
        the surfaces say CANNOT BE COUNTED. Never zero: see the note above.

    `age` is how long ago the goal was written, or None when the snapshot carries no
    usable timestamp — unknown, which is not the same claim as recent.

    THE REVIEW KEYS ANSWER A SECOND QUESTION: when did a human last have EYES on this line,
    written or not (`since_review`, `review_known`, `review_ts`, `review_age`,
    `reviewed_only`). They read the LATER of the two baselines, and they are what `due()`
    counts — because the service this section asks for is a re-read, and a goal re-read
    yesterday is current whether or not one word of it changed."""
    body = str(task.get("goal") or "").strip()
    if not body:
        return {}
    now = time.time() if now is None else now
    total = len(task.get("decisions") or [])
    written_at, written_ts = _goal_snapshot(task, GOAL_TOUCHED_FIELD)
    reviewed_at, reviewed_ts = _goal_snapshot(task, GOAL_REVIEWED_FIELD)
    since = None if written_at is None else max(0, total - written_at)
    known = written_at is not None
    # THE LATER BASELINE WINS, measured in decisions rather than clock time: the count is
    # what the limb reads, and a re-read recorded at decision 40 is newer evidence than a
    # rewrite recorded at decision 12 regardless of which wall clock they carry.
    baseline = written_at
    reviewed_only = False
    if reviewed_at is not None and (baseline is None or reviewed_at > baseline):
        baseline, reviewed_only = reviewed_at, True
    review_ts = reviewed_ts if reviewed_only else written_ts
    flat = " ".join(body.split())
    return {"chars": len(body), "since": since, "known": known, "ts": written_ts,
            "age": (max(0.0, now - written_ts) if written_ts else None),
            "since_review": (None if baseline is None else max(0, total - baseline)),
            "review_known": baseline is not None,
            "review_ts": review_ts,
            "review_age": (max(0.0, now - review_ts) if review_ts else None),
            "reviewed_only": reviewed_only,
            "preview": (flat[:GOAL_PREVIEW_CHARS - 1] + "…"
                        if len(flat) > GOAL_PREVIEW_CHARS else flat)}


# -- informational: what has ACCRUED, and the gap no scan can cover ----------------
#
# THE INCIDENT THIS ANSWERS HONESTLY. A real task scanned CLEAN on every check, and the
# judgement half then found that a RELEASE had shipped and was recorded NOWHERE on the
# task: no decision, no log entry, no PR link. Nothing on the record contradicted
# anything, because the work had happened entirely OUTSIDE what the record holds.
#
# THERE IS NO CHECK FOR THAT, AND THERE MUST NOT BE ONE. Every check here works by
# cross-referencing two things the task itself holds — prose against structure, a memo
# against its target, a recorded path against the filesystem. A release that nothing
# mentions leaves NOTHING to cross-reference: the scan cannot distinguish "no release
# happened" from "a release happened and nobody wrote it down", and a check that guessed
# would be the fifth confidently-wrong check this subsystem has shipped and fixed.
#
# SO THE DETERMINISTIC LAYER DOES THE TWO HONEST THINGS IT CAN. It COUNTS what has been
# recorded since the last heal — decisions, dated log entries, PR/story links, steps —
# which is the measure of how much has happened that a reader can check against what they
# know actually shipped. And the report NAMES THE GAP in words, as the one thing this
# layer structurally cannot cover, so a clean scan can never be read as "everything that
# happened is in here". The counts are the smaller half; the sentence is the point.
#
# INFORMATIONAL, exactly like the health metric and the pinned set: never a finding,
# never counted as an issue, and it can NEVER make a heal due. A task can have accrued
# forty decisions since its last heal and be perfectly reconciled.

ACCRUAL_COUNTS_FIELD = "healed_counts"   # the stamp's snapshot of the four counters

# The four counters, as `(singular, plural, key)` in the order the line reads them.
ACCRUAL_PARTS = (("decision", "decisions", "decisions"),
                 ("log entry", "log entries", "history"),
                 ("PR/story link", "PR/story links", "links"),
                 ("step", "steps", "steps"))


def _recorded_counts(task):
    """The four append-only counters a heal stamp snapshots: how many decisions, dated
    log entries (`history`, the `--log` milestone trail), steps and PR/story links the
    task carried at that moment.

    EXACT ARITHMETIC, NOT THE EVENT FEED — the rule `save._written_counts` already
    follows, and for the same reason: a snapshot taken at write time cannot age out from
    under a busy task the way the bounded feed can. Counted from the FULL lists rather
    than the live ones, because a later reconcile that supersedes a decision does not
    un-happen it."""
    return {"decisions": len(task.get("decisions") or []),
            "history": len(task.get("history") or []),
            "steps": len(task.get("steps") or []),
            "links": len(stored_links(task))}


def accrual(task, now=None):
    """What has been RECORDED since the last heal, as a dict. Never a finding.

    THREE cases, and each is worded as what it actually is:

      * NEVER HEALED — the totals ARE the accrual, and `never` is what makes the surfaces
        say "since the task was created" instead of calling the whole log new since a heal
        that never ran. (`health.new_since_heal` refuses to give a number at all in this
        case, because IT feeds `due()`; this one is informational, so the honest totals
        are useful rather than an alarm.)
      * KNOWN — a stamp exists and it snapshotted the counters, so every number is exact
        subtraction.
      * UNKNOWN — a stamp exists but recorded no snapshot (an older version wrote it).
        Reported as "cannot be counted", never as four zeros: zeros would read as
        "nothing has happened" when the truth is "nobody recorded the baseline". All four
        are known or all four are unknown, so a real count never sits beside a blank one
        and gets read as "and nothing else landed"."""
    now = time.time() if now is None else now
    try:
        ts = float(task.get("last_heal_ts") or 0) or None
    except (TypeError, ValueError):
        ts = None
    totals = _recorded_counts(task)
    unknown = dict((k, None) for k in totals)
    if ts is None:
        counts, known = dict(totals), True
    else:
        snap = task.get(ACCRUAL_COUNTS_FIELD)
        counts, known = unknown, False
        if isinstance(snap, dict):
            try:
                counts = dict((k, max(0, v - int(snap.get(k) or 0)))
                              for k, v in totals.items())
                known = True
            except (TypeError, ValueError):
                counts, known = unknown, False
    out = {"ts": ts, "never": ts is None, "known": known,
           "age": (max(0.0, now - ts) if ts else None), "totals": totals}
    out.update(counts)
    return out


# -- the DISMISSAL LEDGER: adjudicating ONE state, never a category -----------------
#
# THE FAILURE THIS FIXES, and it is the fifth face of the same one. On one real task the
# scan stood at 17 findings; 9 of them were dead paths a human had already read, ruled on
# and moved past. The scan had no way to know that, so it reported all 9 again on the next
# pass, and the next. A report that repeats what its reader has already answered is a report
# its reader stops opening — the identical cost as a report that cries wolf, arriving by a
# different route. Nine of seventeen is worse than five of seven.
#
# SO A DISMISSAL IS AN ADJUDICATION OF ONE EXACT STATE, and the fingerprint is what makes
# that true rather than aspirational. It covers the finding's MATCHED TEXT — check, ref and
# the detail line the check generated — so it survives a re-scan and it does NOT survive the
# underlying text changing. Edit the decision, and the detail line changes with it, the
# fingerprint stops matching, and the finding RE-REPORTS. That is the design and not a
# limitation: "decision 12's supersession prose is fine" is a ruling about the sentence that
# is there now, and the next sentence has not been ruled on by anybody.
#
# A WHY IS MANDATORY. A dismissal with no reason is indistinguishable from a finding
# somebody buried, and six months later nobody — including its author — can tell which it
# was. So `--dismiss` with no `--why` is REFUSED, and the why is stored on the ledger where
# `heal --dismissals` prints it beside the date.
#
# NOTHING IS DELETED, EVER — the rule every verb in this module already keeps. `--undismiss`
# marks the entry RETIRED and full reporting resumes; the entry stays on the ledger as the
# record that somebody once ruled this way and later changed their mind. Re-dismissing
# appends a new entry rather than reviving the old one, because the second ruling was made
# at a different moment, by a different session, possibly for a different reason.
#
# AND A DISMISSED FINDING LEAVES THE SCAN ENTIRELY — `findings`, the issue count and the due
# calculus. That is the only version worth having: a "dismissed" section that still counted
# towards `Heal due?` would leave the alarm on for a state the reader has already
# adjudicated, which is the whole complaint. One informational line says how many are
# silenced and how to read them, because a silenced finding must never become an invisible
# one.

DISMISSALS_FIELD = "heal_dismissals"     # additive: the append-only adjudication ledger


def finding_fingerprint(finding):
    """A stable sha1 over one finding's MATCHED TEXT — check, ref and detail.

    THE DETAIL LINE IS IN THE HASH ON PURPOSE. It is where every check writes what it
    actually matched: the char count for an oversized entry, the keywords for a stale step,
    the overlap percentage and the paired decision for a restatement, the shapes for a
    re-fragmentation. So the hash changes when the underlying text changes, which is exactly
    when the ruling should expire.

    hashlib, NOT hash() — the same reason `_signature` gives: hash()'s string seed is
    randomised per process, so a stored fingerprint would stop matching after a restart and
    every dismissal would silently expire."""
    blob = "\n".join([str((finding or {}).get("check") or ""),
                      str((finding or {}).get("ref") or ""),
                      str((finding or {}).get("detail") or "")])
    return hashlib.sha1(blob.encode("utf-8", "replace")).hexdigest()


def dismissal_ledger(task):
    """Every dismissal entry ever recorded on this task, oldest first — retired ones
    included. The ledger is history; `active_dismissals` is the part that silences."""
    raw = (task or {}).get(DISMISSALS_FIELD)
    return [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []


def active_dismissals(task):
    """The dismissals still in force — every ledger entry not marked retired."""
    return [e for e in dismissal_ledger(task) if not e.get("retired")]


def dismissed_fingerprints(task):
    """The fingerprints currently silenced, as a set."""
    return set(str(e.get("fingerprint") or "") for e in active_dismissals(task)
               if e.get("fingerprint"))


# -- AN IDENTICAL FINDING IS ONE FINDING -----------------------------------------
#
# `--dismiss` refuses an ambiguous selector rather than guessing which row it meant, and
# that rule is right: an adjudication written onto the wrong finding is silent, permanent,
# and only discovered when the finding it should have covered is missing from a later
# report. But two findings can be BYTE-IDENTICAL, and then "Name one exactly" is an
# instruction nobody can follow. Five sessions that each recorded the same worktree cwd
# produce FIVE identical drift rows; on one real task 7 findings were undismissable this
# way, which was 100% of its remaining mechanical issues — a scan whose whole remaining
# content could never be adjudicated away.
#
# They were never five things. One path is gone ONCE, and how many sessions happened to sit
# in that directory is a fact about the sessions, not about the path. So identical rows
# collapse to ONE, carrying `occurrences`, and the issue count stops being inflated by
# session bookkeeping. Nothing is hidden: the count rides on the row.
#
# `occurrences` IS DELIBERATELY OUTSIDE THE FINGERPRINT (which hashes check + ref + detail).
# A sixth session recording the same cwd must not expire a ruling somebody already made
# about that path — the fingerprint exists to expire a ruling when the TEXT changes, and
# "how many times" is not the text.
#
# Dedupe cannot reach one residual case: the same ref reported with DIFFERENT details, which
# is what a path recorded both as a file and as a session cwd produces. `_match_rows` grows
# an ordinal handle for that — see the note there.

def dedupe_findings(findings):
    """Identical findings — same check, same ref, same detail — collapsed to one row
    carrying `occurrences`. Order is the order the first of each arrived."""
    out, index = [], {}
    for f in (findings or []):
        row = dict(f)
        key = (str(row.get("check") or ""), str(row.get("ref") or ""),
               str(row.get("detail") or ""))
        hit = index.get(key)
        if hit is None:
            row["occurrences"] = 1
            index[key] = row
            out.append(row)
        else:
            hit["occurrences"] = hit.get("occurrences", 1) + 1
    return out


def apply_dismissals(findings, task):
    """`(kept, dismissed)` — the findings split by the ledger, each side in the order it
    arrived. Both halves keep their `fingerprint`, which is what lets a later `--undismiss`
    and the `--dismissals` listing talk about the same rows the scan matched."""
    silenced = dismissed_fingerprints(task)
    kept, dropped = [], []
    for f in (findings or []):
        row = dict(f)
        row["fingerprint"] = finding_fingerprint(f)
        (dropped if row["fingerprint"] in silenced else kept).append(row)
    return kept, dropped


def dismissal_entry(finding, why, sid=None, now=None):
    """One ledger row for `finding`. `sid` is the session that adjudicated it, or None when
    nothing was attributed — never a fabricated actor."""
    return {"check": str((finding or {}).get("check") or ""),
            "ref": str((finding or {}).get("ref") or ""),
            "fingerprint": finding_fingerprint(finding),
            "why": str(why or "").strip(),
            "ts": time.time() if now is None else now,
            "sid": sid or None}


def parse_dismiss_selector(raw):
    """`'<check>:<ref>'` → `(check, ref, error)`.

    Split on the FIRST colon only, because refs legitimately contain them — a link-rot ref
    is a URL. An input with no colon is refused rather than guessed at: a bare `decision 2`
    is ambiguous across three different checks that all report decision refs, and silently
    picking one would dismiss a finding nobody named."""
    s = str(raw or "").strip()
    if ":" not in s:
        return None, None, ("heal --dismiss/--undismiss takes '<check>:<ref>' — e.g. "
                            "'prose-supersession:decision 12'. %r names no check, and the "
                            "same ref is reported by several of them, so there is nothing "
                            "to pick between. The check keys are: %s"
                            % (s, ", ".join(CHECK_ORDER)))
    check, ref = s.split(":", 1)
    check, ref = check.strip(), ref.strip()
    if not check or not ref:
        return None, None, ("heal --dismiss/--undismiss: '<check>:<ref>' needs both halves "
                            "— got %r" % s)
    return check, ref, None


def _row_label(row):
    """How one finding or ledger row is named back to the reader — the exact selector that
    would match it, so an error message doubles as the command to retype."""
    return "%s:%s" % (row.get("check"), row.get("ref"))


# The ORDINAL HANDLE: `<check>:<ref>#<n>` names the nth row among the ones a ref matches.
#
# It exists for the case dedupe cannot collapse — the same ref carrying DIFFERENT details,
# which is what one path recorded both as an edited FILE and as a session CWD produces. Two
# rows, one selector, and before this the reader was told to "name one exactly" with no
# exact name available.
#
# TRIED LAST, NEVER FIRST. A link-rot ref is a URL, and a URL fragment can be `#2`. So the
# WHOLE ref is resolved first, exactly as before, and the trailing `#<n>` is re-read as a
# handle only when the ref as written did not land on one row. A real ref ending in `#2`
# therefore keeps winning, and nothing that worked before resolves differently now.
_ORDINAL_HANDLE = re.compile(r"^(?P<ref>.+)#(?P<n>\d+)$")


def _ref_hits(rows, ref):
    """The rows a bare ref matches — EXACT first, then substring. Ordering is the caller's
    row order, which is what makes an ordinal handle mean the same thing twice."""
    want = str(ref or "").casefold()
    exact = [r for r in rows if str(r.get("ref") or "").casefold() == want]
    return exact or [r for r in rows if want and want in str(r.get("ref") or "").casefold()]


def _handles(rows):
    """The selectors that WOULD name each of `rows` unambiguously, as text — the ambiguity
    refusal prints these so it doubles as the command to retype.

    A row whose REF IS ALREADY UNIQUE among the matches is named by its ref, because that is
    the honest answer: the selector was a substring that spanned several distinct refs, and
    the fix is to name the one meant, not to count. Only rows SHARING a ref get an ordinal —
    those are the ones no exact name can separate."""
    shared = {}
    for r in rows:
        ref = str(r.get("ref") or "")
        shared[ref] = shared.get(ref, 0) + 1
    nth, out = {}, []
    for r in rows:
        ref = str(r.get("ref") or "")
        if shared[ref] > 1:
            nth[ref] = nth.get(ref, 0) + 1
            out.append("%s:%s#%d" % (r.get("check"), ref, nth[ref]))
        else:
            out.append("%s:%s" % (r.get("check"), ref))
    return "; ".join(out)


def _match_rows(rows, check, ref, what):
    """`(row, error)` — the ONE row a `<check>:<ref>` selector names.

    EXACT FIRST, then unambiguous substring, then the ordinal handle, and a still-ambiguous
    or missing ref is REFUSED with the list rather than resolved. That is the same rule
    `select_acks` keeps and for the same reason: an adjudication written onto the wrong
    finding is silent, permanent, and only discovered when the finding it should have
    covered is missing from a later report."""
    same = [r for r in rows if str(r.get("check") or "") == check]
    if not same:
        return None, ("heal: no %s for check %r. %s"
                      % (what, check, _listing(rows, what)))
    hits = _ref_hits(same, ref)
    if len(hits) == 1:
        return hits[0], None
    m = _ORDINAL_HANDLE.match(str(ref or ""))
    if m:
        base = _ref_hits(same, m.group("ref"))
        n = int(m.group("n"))
        if base and 1 <= n <= len(base):
            return base[n - 1], None
        if base:
            return None, ("heal: %r names row %d, and %r matches %d %s(s). Name one of: %s. "
                          "Nothing was changed."
                          % (_row_label({"check": check, "ref": ref}), n, m.group("ref"),
                             len(base), what, _handles(base)))
    if not hits:
        return None, ("heal: %r matches no %s. %s"
                      % (_row_label({"check": check, "ref": ref}), what, _listing(rows, what)))
    return None, ("heal: %r is ambiguous — it matches %d. Name one exactly: %s. Rows sharing "
                  "one ref are separated by an ordinal handle, because no exact ref can tell "
                  "them apart. Nothing was changed."
                  % (_row_label({"check": check, "ref": ref}), len(hits), _handles(hits)))


def _listing(rows, what):
    """The rows a refusal offers instead of guessing — every selector that WOULD work."""
    if not rows:
        return "There is no %s on this task at all." % what
    return ("The %s currently on this task: %s"
            % (what, "; ".join(_row_label(r) for r in rows)))


def dismiss(task, findings, selector, why, sid=None, now=None):
    """Record ONE dismissal, as `(entry, error)`. Does NOT save — the caller persists.

    THREE REFUSALS, and each one changes nothing at all:

      * NO WHY. An adjudication without a reason is how a real finding gets buried.
      * NOT A CURRENT FINDING. Dismissing something the scan is not reporting would write a
        ruling that silences nothing now and might silence something else later, when a
        finding with that fingerprint eventually appears.
      * ALREADY DISMISSED. Saying so is the honest answer; appending a duplicate would make
        the ledger read as two independent rulings by two readers."""
    if not str(why or "").strip():
        return None, ("heal --dismiss needs --why '<reason>'. A dismissal is an "
                      "adjudication: it takes a finding out of the scan, the issue count "
                      "and the due calculus, and one recorded with no reason is "
                      "indistinguishable six months later from a finding somebody buried. "
                      "Nothing was changed.")
    check, ref, err = parse_dismiss_selector(selector)
    if err:
        return None, err
    row, err = _match_rows(findings, check, ref, "finding")
    if err:
        return None, err
    fp = finding_fingerprint(row)
    if fp in dismissed_fingerprints(task):
        return None, ("heal --dismiss %s: already dismissed, and the ledger already carries "
                      "the why — `heal --dismissals` prints it. Nothing was changed."
                      % _row_label(row))
    entry = dismissal_entry(row, why, sid=sid, now=now)
    task.setdefault(DISMISSALS_FIELD, []).append(entry)
    return entry, None


def undismiss(task, selector, sid=None, now=None):
    """RETIRE one active dismissal, as `(entry, error)`. Does NOT save.

    Matched against the LEDGER rather than the current findings, deliberately: the finding
    it silences is by definition absent from the scan, so matching against findings would
    make every dismissal un-undismissable. Nothing is removed — the entry is marked retired,
    with who did it and when, so the record shows both rulings."""
    check, ref, err = parse_dismiss_selector(selector)
    if err:
        return None, err
    active = active_dismissals(task)
    row, err = _match_rows(active, check, ref, "active dismissal")
    if err:
        return None, err
    row["retired"] = True
    row["retired_ts"] = time.time() if now is None else now
    row["retired_by"] = sid or None
    return row, None


def dismissal_rows(task, result=None):
    """The ledger for display, as `[{entry…, "silencing": bool}, …]`, newest first.

    `silencing` answers the one question a reader of this list actually has: is this ruling
    still doing anything? An ACTIVE entry whose fingerprint matches nothing in the current
    scan means the underlying text has CHANGED, so the finding is being reported again and
    the old ruling no longer covers it. That is the fingerprint design working, and it has to
    be visible — otherwise the list reads as "9 findings are silenced" when the true answer
    is "6 are, and 3 rulings have expired". Needs a scan `result` to say so; without one the
    key is None, meaning unknown rather than False."""
    seen = None
    if result is not None:
        seen = set()
        for f in ((result.get("findings") or []) + (result.get("dismissed") or [])):
            seen.add(f.get("fingerprint") or finding_fingerprint(f))
    out = []
    for e in reversed(dismissal_ledger(task)):
        row = dict(e)
        row["silencing"] = (None if seen is None else
                            (not e.get("retired")
                             and str(e.get("fingerprint") or "") in seen))
        out.append(row)
    return out


# -- the scan --------------------------------------------------------------------

def scan(task, now=None, exists=os.path.exists, branch_probe=None, link_probe=None,
         commit_probe=None):
    """Run all eleven checks, plus the sections that are deliberately NOT checks. NEVER
    mutates the task — not one field.

    `findings` is the only key that means "something is wrong", and it is the only one
    `due()` counts. `merge_candidates`, `subject_candidates`, `oversized_proposals`,
    `pinned_review`, `goal_review`, `ephemeral`, `size` and `accrual` ride alongside it as
    PROPOSALS, INFORMATION and COUNTS: a task can carry plenty of all of them and still be
    perfectly reconciled, so folding any into the issue count would put `Heal due? YES` on a
    healthy task — the exact failure this module has already had to fix four times. The one
    exception is deliberate and narrow: past `goal_review_due` decisions the GOAL REVIEW can
    make a heal due, while still never counting as an issue (see the note above it).

    `accrual` is the one section that is not about anything the scan FOUND: it is what has
    been recorded since the last heal, printed next to the plain statement that a scan
    reads the record ONLY and cannot know whether something that actually shipped is
    missing from it. See the note above `accrual` for the release that was recorded
    nowhere while every check reported clean.

    ALL THREE OUTWARD PROBES DEFAULT TO OFF: `branch_probe=None` and `commit_probe=None`
    mean no git subprocess, `link_probe=None` means no HTTP. So the default scan is pure
    Python plus filesystem stats and is cheap enough for every session start. `heal --scan`
    and the dry run wire the two git probers; the link probe stays opt-in behind
    `--probe-links`.

    DISMISSED FINDINGS LEAVE `findings` ENTIRELY and land in `dismissed` — so the issue
    count, `due()` and `plan()` all stop seeing them from one place, rather than each
    learning about the ledger separately."""
    now = time.time() if now is None else now
    # Probed ONCE and reused for both the findings and the report — calling
    # link_states twice would double every network round-trip.
    links = link_states(task, probe=link_probe)
    commits = commit_states(task, probe=commit_probe)      # probed ONCE, for the same reason
    size = size_objective(task)
    # Grouped ONCE and reused three ways — the check below, the report's two tiers, and
    # anything a caller does with the result. The grouping is pure string work rather than a
    # round trip, but this scan runs at EVERY session start.
    subject = subject_candidates(task)
    shape = merge_candidates(task)
    groups = candidate_groups(task, subject=subject, shape=shape)
    findings = []
    findings.extend(undispositioned_acks(task))
    findings.extend(unfulfilled_corrections(task))
    findings.extend(prose_supersession(task))
    findings.extend(oversized(task))
    findings.extend(drift(task, exists=exists, branch_probe=branch_probe))
    findings.extend(_finding("link-rot", url, "%s link does not resolve" % kind)
                    for kind, url, state in links if state is False)
    findings.extend(stale_steps(task))
    findings.extend(refragmented_consolidations(task))
    findings.extend(steps_restating_superseded(task))
    findings.extend(commit_rot(task, states=commits))
    findings.extend(grew_with_candidates(task, groups=groups, size=size))
    findings = dedupe_findings(findings)
    findings.sort(key=lambda f: CHECK_ORDER.index(f["check"])
                  if f["check"] in CHECK_ORDER else len(CHECK_ORDER))
    findings, dismissed = apply_dismissals(findings, task)
    return {
        "task": task.get("id"),
        "seq": task.get("seq"),
        "ts": now,
        "findings": findings,
        "dismissed": dismissed,
        "health": health(task, now=now),
        "size": size,
        "links": [{"kind": k, "url": u, "state": s} for k, u, s in links],
        "commits": [{"sha": s, "where": w, "state": st} for s, w, st in commits],
        "merge_candidates": shape,
        "subject_candidates": subject,
        "completed_subjects": sorted(completed_subjects(task)),
        "oversized_proposals": oversized_proposals(task),
        "pinned_review": pinned_review(task, now=now),
        "goal_review": goal_review(task, now=now),
        "ephemeral": vanished_ephemeral(task, exists=exists),
        "accrual": accrual(task, now=now),
    }


def counts(result):
    """Findings per check slug, for the one-line summaries."""
    out = {}
    for f in (result or {}).get("findings") or []:
        out[f["check"]] = out.get(f["check"], 0) + 1
    return out


# -- the gate file ---------------------------------------------------------------
#
# One JSON file per task under <data_dir>/heal/. It holds the last scan result plus
# the nag watermark. Every read and write fails OPEN: an unreadable gate means no nag,
# never a crash and never a blocked session.

def gate_dir():
    """Resolved fresh on every call — paths.data_dir() reads the environment and tests
    repoint it per-test."""
    return os.path.join(paths.data_dir(), GATE_DIR)


def _safe_id(task_id):
    """A task id reduced to filename-safe characters."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(task_id or "unknown"))


def gate_path(task_id):
    return os.path.join(gate_dir(), "%s.json" % _safe_id(task_id))


def backup_path(task_id):
    return os.path.join(gate_dir(), "%s%s" % (_safe_id(task_id), BACKUP_SUFFIX))


def read_gate(task_id):
    """The stored gate dict, or {} when absent/unreadable/garbled."""
    try:
        with open(gate_path(task_id), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_gate(result, extra=None):
    """Persist a scan result (plus optional extra keys) as the task's gate file.
    Returns the path, or None when it could not be written — a gate we cannot write
    means we may nag twice, which is harmless."""
    data = dict(result or {})
    if extra:
        data.update(extra)
    path = gate_path(data.get("task"))
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        return path
    except Exception:
        return None


def clear_gate(task_id):
    """Drop a task's gate file, re-arming the nag. True when one went."""
    try:
        os.remove(gate_path(task_id))
        return True
    except Exception:
        return False


# -- is a heal due? --------------------------------------------------------------

def due(task, result=None, now=None):
    """`(is_due, [reasons])` — the five independent limbs, each named in plain words so the
    nag can say WHY:

      * the scan found anything at all,
      * ≥ DUE_NEW_DECISIONS decisions the last heal has never seen,
      * any undispositioned ack exists,
      * more than DUE_AGE_DAYS days without a heal on an ACTIVE task,
      * ≥ `goal_review_due()` decisions since the GOAL LINE was last written or re-read.

    THE GOAL LIMB IS THE ONE THING HERE THAT IS NOT A FINDING, and that is not a
    contradiction — "due" means "run the pass", not "something is wrong". A goal is supposed
    to outlive the decisions that pursue it, so an untouched one is never a defect; but a
    goal that has not been READ across twenty-five decisions is a sentence the next cold
    session will take as the plan on evidence nobody has checked it against. It needs a
    BASELINE, so every task written before the baseline existed is silent here rather than
    permanently due, and `heal --goal-reviewed` clears it without requiring a rewrite.

    The second limb is worded from the STAMP. With a stamp, "N new decision(s) since the
    last heal" is a true and actionable count. WITHOUT one it used to report the task's
    ENTIRE decision total as "new since the last heal", which is false twice over —
    nothing is new, and there was no last heal — and on a task carrying 97 decisions it
    made the one signal built to be trusted read like a permanent false alarm. So a
    never-healed task says exactly that instead.

    Reasons are returned even when not due, so callers can report the near-misses.

    FOUR OF THE FIVE LIMBS NEED NO SCAN — see `cheap_limbs`, which is the same code and
    the same wording, reached without one."""
    now = time.time() if now is None else now
    result = result if result is not None else scan(task, now=now)
    h = result.get("health") or {}
    reasons = []
    n = len(result.get("findings") or [])
    if n:
        reasons.append("the scan found %d issue(s)" % n)
    # Both inputs come off the SCAN RESULT rather than being recomputed: the ack count
    # from its findings (so an ack a reconciler adjudicated away stays silenced by the
    # dismissal ledger), and `goal_review` from the section scan already built. Reading
    # them back is also what keeps a caller-supplied `result` authoritative — this
    # function's contract has always been that a passed-in result IS the scan.
    reasons.extend(text for _limb, text in _blob_limbs(
        task, h, counts(result).get("ack-undispositioned", 0),
        result.get("goal_review") or {}, now))
    return bool(reasons), reasons


# The four limbs above that a caller can evaluate WITHOUT a scan, tagged so a caller can
# fingerprint the LIMB rather than its count. That distinction is the whole self-cap
# design, and `checker.drift_signature` learned it first: every one of these reasons
# carries a number that moves on its own (a decision lands, a day passes), so hashing the
# WORDED reason would re-arm the throttle on every prompt and the nudge built to fire once
# would fire forever.
LIMB_NEW_DECISIONS = "new-decisions"   # decisions the last heal has never seen
LIMB_ACKS = "acks"                     # an ack carrying no disposition
LIMB_GOAL_REVIEW = "goal-review"       # decisions since the goal line was last read
LIMB_AGE = "age"                       # days without a heal on an ACTIVE task


def _blob_limbs(task, h, acks, g, now):
    """`[(limb, reason), …]` for the four limbs that are pure BLOB READS — no scan, no
    filesystem, no git.

    Every input is PASSED IN — `h` a `health()` dict, `acks` the post-dismissal
    undispositioned-ack count, `g` a `goal_review()` dict — so the caller decides how it
    got them. `due` reads all three off its scan result (a caller-supplied result stays
    authoritative, which is that function's long-standing contract) and `cheap_limbs`
    computes them straight off the blob. Neither can word a reason differently from the
    other, which is the whole reason this is one function."""
    out = []
    if h.get("never_healed"):
        total = h.get("decisions_total") or 0
        if total >= DUE_NEW_DECISIONS:
            out.append((LIMB_NEW_DECISIONS,
                        "no heal has ever been recorded, and %d decision(s) are on "
                        "the log" % total))
    else:
        new = h.get("new_since_heal") or 0
        if new >= DUE_NEW_DECISIONS:
            out.append((LIMB_NEW_DECISIONS,
                        "%d new decision(s) since the last heal" % new))
    if acks:
        out.append((LIMB_ACKS, "%d ack(s) carry no disposition" % acks))
    if (g or {}).get("review_known"):
        stale = g.get("since_review") or 0
        threshold = goal_review_due()
        if stale >= threshold:
            out.append((LIMB_GOAL_REVIEW,
                        "%d decision(s) since the goal line was last reviewed" % stale))
    if task.get("status") == "active":
        since = h.get("since_heal")
        if since is None:
            # h["last_heal_ts"] is the SANITISED stamp (None when absent or garbled), so
            # a junk value can never reach the arithmetic here.
            base = h.get("last_heal_ts") or task.get("created_ts") or now
            try:
                base = float(base)
            except (TypeError, ValueError):
                base = now
            since = max(0, now - base)
            healed = False
        else:
            healed = True
        if since > DUE_AGE:
            out.append((LIMB_AGE, "%d days since the %s on an active task"
                        % (int(since // 86400),
                           "last heal" if healed else "task was created")))
    return out


def cheap_limbs(task, now=None):
    """`[(limb, reason), …]` — every due limb that can be evaluated from the task BLOB
    alone, in `due`'s order and `due`'s exact wording.

    WHY THIS EXISTS SEPARATELY FROM `due`. `due` runs `scan`, which is cheap enough for a
    SESSION START (no git, no network — see `scan`) but is eleven checks over every
    decision, memo and step plus a `stat` per declared path. The UserPromptSubmit rail
    fires on EVERY prompt, so it needs the same verdict at blob-read cost. What it gives
    up is exactly one limb — "the scan found N issue(s)" — and it never claims otherwise:
    a caller here reports the limbs it actually measured.

    The dismissal ledger is applied, so an ack a reconciler adjudicated away stays
    silent here just as it does in the full pass."""
    now = time.time() if now is None else now
    kept, _dismissed = apply_dismissals(undispositioned_acks(task), task)
    return _blob_limbs(task, health(task, now=now), len(kept),
                       goal_review(task, now=now), now)


def due_cheap(task, now=None):
    """`(is_due, [reasons])` from `cheap_limbs` — `due`'s shape, without the scan."""
    limbs = cheap_limbs(task, now=now)
    return bool(limbs), [text for _limb, text in limbs]


def _signature(reasons):
    """A stable fingerprint of the reported state, for the nag watermark. hashlib —
    NOT hash(), whose string seed is randomised per process, which would make the
    self-cap silently useless."""
    blob = "\n".join(sorted(reasons or []))
    return hashlib.sha1(blob.encode("utf-8", "replace")).hexdigest()


def nag(task, now=None, persist=True):
    """ONE line for the SessionStart context, or None.

    Self-capping the same way `hook_health.nag` is: the gate file records the
    fingerprint of the state already reported, and this returns None until that state
    CHANGES (or `--apply` / `clear_gate` re-arms it). So a task that stays
    under-reconciled nags once, not every session — an unheeded nag every session
    trains you to ignore it."""
    now = time.time() if now is None else now
    if not task or task.get("status") == "closed":
        return None
    result = scan(task, now=now)
    is_due, reasons = due(task, result=result, now=now)
    if not is_due:
        return None
    sig = _signature(reasons)
    gate = read_gate(task.get("id"))
    if gate.get("nagged_sig") == sig:
        return None                       # already reported exactly this state
    if persist:
        write_gate(result, {"nagged_sig": sig, "nagged_ts": now})
    h = result.get("health") or {}
    seq = task.get("seq") or (str(task.get("id") or "")[:8])
    return ("[task-station] Task #%s is under-reconciled: %s. Its digest carries %d "
            "current decision(s) / %d chars. Run `/todo heal` (or `/heal`) — it is a "
            "DRY RUN by default and changes nothing."
            % (seq, "; ".join(reasons), h.get("decisions_current", 0), h.get("chars", 0)))


def gate_line(task, now=None, result=None):
    """The one-line "heal first" warning for the `save` and `done` gates, or None.

    Reads the SAME `due` logic as the nag but is NOT self-capping and NEVER persists:
    these gates fire at a decision point (you are about to overwrite the summary, or
    close the task), so they must warn every time. Neither gate blocks, and neither
    runs the heal.

    `result` lets a caller that ALREADY holds a scan hand it over instead of paying for a
    second one. The save gate does exactly that, because it also wants to NAME the first
    finding (`first_finding_line`) and running the corpus twice to print one extra clause
    would be the expensive way to say the same thing."""
    if not task:
        return None
    is_due, reasons = due(task, result=result, now=now)
    if not is_due:
        return None
    return "heal first — %s. `/todo heal` is a dry run by default." % "; ".join(reasons)


FINDING_PREVIEW_CHARS = 110    # enough to recognise WHICH finding, not to reprint its fix


def first_finding_line(result, limit=FINDING_PREVIEW_CHARS):
    """The worst finding as ONE short `<ref> — <detail>` line, or None when the scan found
    nothing.

    WHY THE SAVE GATE NAMES ONE. "the scan found 3 issue(s)" tells a reader that something
    fired and nothing about what, so the only way to learn whether it matters was to go
    run the scan — at the exact moment they had decided to checkpoint instead. One named
    finding turns that into a judgement they can make in place.

    WORST = FIRST, and it is already sorted: `scan` orders findings by `CHECK_ORDER`, so
    `findings[0]` is the highest-priority check that fired rather than an arbitrary pick.
    Naming one and counting the rest is `checker`'s `NAG_ITEMS` reasoning at N=1 — the
    gate line is a clause inside a bigger block here, not a report of its own.

    The detail is CUT, never summarised: a finding's detail ends with the flag that fixes
    it, which is a command a truncated line must not pretend to be complete."""
    findings = (result or {}).get("findings") or []
    if not findings:
        return None
    f = findings[0]
    line = "%s — %s" % (f.get("ref") or "?", f.get("detail") or "")
    flat = " ".join(line.split())
    return (flat[:limit - 1] + "…") if len(flat) > limit else flat


# -- the backup ------------------------------------------------------------------

def backup(task, strip=None):
    """Write the task blob to `<data_dir>/heal/<id>.bak-pre-heal.json` BEFORE any
    `--apply`, mirroring this project's existing `.bak-*` sibling-file convention.

    Returns the path, or None on failure — and the caller MUST refuse to apply on
    None. A reconcile without a backup is the one shape of this feature that could
    lose work, so failing to back up fails the whole operation."""
    if not task:
        return None
    blob = strip(task) if strip else task
    path = backup_path(task.get("id"))
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(blob, f, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        return path
    except Exception:
        return None


# How a heal came to be recorded. Both are real reconciles; they differ in what the
# machine did, not in whether the pass happened.
HEAL_KIND_APPLY = "apply"     # `--apply` ran the mechanical plan (even a plan of zero ops)
HEAL_KIND_MARK = "marked"     # `--mark-healed`: read everything, nothing needed changing


def stamp_healed(task, now=None, kind=HEAL_KIND_APPLY, note=None):
    """Record that this task was reconciled: the moment, the decision count it left
    behind (the baseline `new_since_heal` counts from), how the heal was recorded, and
    optionally WHY. Does NOT save — the caller persists.

    A STAMP IS A CLAIM ABOUT WORK, so only work may write one. Two callers qualify: an
    `--apply` that performed at least ONE operation, and `--mark-healed`, where the
    operation was a human reading the whole log and concluding nothing needed changing.

    A bare `--apply` that performed ZERO operations must NOT reach this. It briefly did,
    on the theory that running `--apply` is itself an assertion the task was reconciled
    — but it is the exact command someone runs when they assume `--apply` IS the heal,
    and it wrote `last heal <just now>` onto a task nobody had reconciled. That is worse
    than the alarm it was meant to fix: an alarm that is always on gets ignored, while a
    stamp that is sometimes a lie makes every stamp unreadable. `cmd_heal` refuses that
    case and names `--mark-healed --note` instead, which records the same "I read it and
    it was fine" honestly.

    `--scan` must NEVER reach this — it is read-only, and that is its whole contract.

    STORAGE is additive keys on the task blob (`last_heal_ts`,
    `decisions_at_last_heal`, `last_heal_kind`, `last_heal_note`, `healed_counts`), which
    is what the frozen-format rule allows: a task written by an older version simply has
    none of them and reads as never healed.

    `healed_counts` is the four-counter snapshot `accrual` subtracts from, taken the same
    way `save.stamp_checkpoint` takes `saved_counts` — exact arithmetic rather than a scan
    of the bounded event feed. `decisions_at_last_heal` stays alongside it, unchanged:
    `health` reads that one, and rewriting a stamp field older releases already write is
    how a frozen format stops being frozen.

    `chars_at_last_heal` is the SIZE OBJECTIVE's baseline (`size_objective`), and it is a
    different measure from all of those on purpose: the counters say how much was RECORDED,
    this says how much the digest COSTS. A heal can add four decisions and still leave the
    digest smaller, which is the whole point of the pass — and until this was snapshotted
    nothing could tell that outcome from the opposite one. Taken from the LIVE decisions
    (`decisions.total_chars`), because a replaced entry costs the digest nothing.

    IT IS WRITTEN LAST-ISH ON PURPOSE: every caller stamps AFTER performing its operations,
    so the baseline is the size the pass LEFT, not the size it found."""
    now = time.time() if now is None else now
    task["last_heal_ts"] = now
    task["decisions_at_last_heal"] = len(task.get("decisions") or [])
    task[ACCRUAL_COUNTS_FIELD] = _recorded_counts(task)
    task[CHARS_AT_LAST_HEAL] = _dec.total_chars(task.get("decisions"))
    task["last_heal_kind"] = kind
    note = (note or "").strip()
    if note:
        task["last_heal_note"] = note
    else:
        task.pop("last_heal_note", None)     # a fresh heal must not inherit an old why


# -- layer 2: the mechanical plan ------------------------------------------------

_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _split_bullets(body):
    """Group lines into chunks that each start at a bullet/numbered-list marker."""
    chunks, cur = [], []
    for line in body.splitlines():
        if _BULLET.match(line) and cur:
            chunks.append("\n".join(cur).strip())
            cur = [line]
        else:
            cur.append(line)
    if cur:
        chunks.append("\n".join(cur).strip())
    return [c for c in chunks if c]


def _split_sentences(body, limit):
    """Greedily group sentences into chunks no longer than `limit`."""
    chunks, cur = [], ""
    for s in _SENTENCE.split(body):
        s = s.strip()
        if not s:
            continue
        if cur and len(cur) + 1 + len(s) > limit:
            chunks.append(cur)
            cur = s
        else:
            cur = (cur + " " + s).strip()
    if cur:
        chunks.append(cur)
    return chunks


def split_parts(text, max_parts=SPLIT_MAX_PARTS, limit=OVERSIZE_CHARS):
    """A compound decision's own structure, as the parts a mechanical split produces:
    paragraph boundaries first, then list markers, then sentence groups.

    Returns [] when the text has no internal structure to cut on — in which case the
    split is left to the LLM pass rather than guessed at. Beyond `max_parts` the tail
    collapses into the last part, so one pathological entry can't fan out to fifty."""
    body = (text or "").strip()
    if not body:
        return []
    chunks = []
    for chunker in (lambda b: [c.strip() for c in re.split(r"\n\s*\n", b) if c.strip()],
                    _split_bullets,
                    lambda b: _split_sentences(b, limit)):
        chunks = chunker(body)
        if len(chunks) >= 2:
            break
    if len(chunks) < 2:
        return []                  # no internal structure — leave it to the LLM pass
    if len(chunks) > max_parts:
        chunks = chunks[:max_parts - 1] + ["\n\n".join(chunks[max_parts - 1:])]
    return chunks


# Named merge signatures — clusters crisp enough to detect outright. A release record
# is the canonical case: `2.9.0 SHIPPED: …` four times over is one release trail.
MERGE_SIGNATURES = (
    ("release record", re.compile(r"\b\d+\.\d+\.\d+\b.{0,80}?\bshipped\b", re.I | re.S)),
)

def _stem(text):
    """A `stem:<w1 w2 w3 w4>` signature from the first STEM_WORDS significant words —
    what makes "scrub iteration 3 …" and "scrub iteration 4 …" one cluster. Digits are
    dropped so the iteration NUMBER doesn't split the group. None when the text has
    too few significant words to fingerprint safely.

    Longer than `leading_shape`'s signature and used for a different job: this one gates
    an op `--apply` will PERFORM, so it has to be surer than a proposal a human reads."""
    words = _significant_words(text)
    if len(words) < STEM_WORDS:
        return None
    return "stem:" + " ".join(words[:STEM_WORDS])


def merge_clusters(task):
    """Live decisions that reconcile to one, as `[(label, [index1, …]), …]`.

    TWO confidence tiers, because the evidence differs. A NAMED signature (a release
    record) is unambiguous, so two are enough to merge. The weaker STEM signature —
    four shared leading words — needs MERGE_MIN_STEM before it counts, since two
    decisions can share an opening phrase and still be genuinely distinct. Being
    conservative here is deliberate: a wrong merge is reversible, but it still costs
    someone a confused read."""
    named, stems = {}, {}
    for i, e in _dec.live(task.get("decisions")):
        txt = _dec.text(e)
        for label, rx in MERGE_SIGNATURES:
            if rx.search(txt):
                named.setdefault(label, []).append(i)
                break
        else:
            s = _stem(txt)
            if s:
                stems.setdefault(s, []).append(i)
    out = []
    for label, idxs in sorted(named.items()):
        if len(idxs) >= MERGE_MIN_NAMED:
            out.append((label, idxs))
    for s, idxs in sorted(stems.items()):
        if len(idxs) >= MERGE_MIN_STEM:
            out.append((s[len("stem:"):], idxs))
    return out


def _merge_summary(label, idxs, task):
    """The absorbing decision's text. Names the cluster, says how many it reconciles,
    and points at `history` for the originals — so the summary is honest about being a
    summary and the trail is one command away.

    WORDED TO AVOID SUPERSESSION_LANGUAGE. An earlier draft said "no longer
    load-bearing", which is itself one of the phrases check 3 looks for — so every
    summary this generated re-tripped the prose check on the very next scan, making a
    freshly-healed task read as dirty. A generated decision must not trip the scan that
    generated it."""
    return ("%s — one reconciled record of %d decisions (%s); each was true on its own "
            "but stopped earning digest space. Full text of every original: "
            "`/todo %s history`."
            % (label[:1].upper() + label[1:], len(idxs),
               ", ".join(str(i) for i in idxs),
               task.get("seq") or str(task.get("id") or "")[:8]))


def plan(task, result=None, limit=OVERSIZE_CHARS):
    """The MECHANICAL operations `--apply` would perform, oldest-target first.

    This is deliberately the conservative subset — the part a machine can get right
    with no judgment: cut an oversized decision on ITS OWN paragraph boundaries,
    collapse a cluster the signatures actually matched, and retro-dispose acks that
    predate dispositions. Anything needing a reading of the content (which half of a
    compound decision was refuted, whether prose that says "no longer" supersedes
    entry 8, what the `state` line should say now) is NOT planned here — it is
    reported for the LLM pass, which is layer 2's whole reason to exist.

    Each op is a dict with a `verb` and a `why`. An op whose parts could not be
    derived mechanically is marked `manual` and is skipped by `apply`."""
    result = result if result is not None else scan(task)
    entries = task.get("decisions") or []
    ops = []
    for f in result.get("findings") or []:
        if f["check"] != "oversized":
            continue
        try:
            i = int(f["ref"].split()[-1])
        except (TypeError, ValueError, IndexError):
            continue
        body = _dec.text(entries[i - 1]) if 1 <= i <= len(entries) else ""
        parts = split_parts(body, limit=limit)
        ops.append({
            "verb": "split", "index": i, "parts": parts, "manual": not parts,
            "why": ("%d chars — %s" % (
                len(body),
                ("splits cleanly into %d parts on its own structure" % len(parts))
                if parts else
                "NO internal structure to cut on; the parts need authoring by hand")),
        })
    for label, idxs in merge_clusters(task):
        ops.append({"verb": "merge", "indices": idxs, "label": label,
                    "into": _merge_summary(label, idxs, task), "manual": False,
                    "why": "%d decisions share the %r signature" % (len(idxs), label)})
    ops.extend(disposition_ops(task))
    return ops


def undispositioned(task):
    """Every ack still carrying no disposition, as `(memo, ack)` pairs in record order.
    One reader for the check, the plan and the explicit `--dispose-acks` selection, so
    the three can never disagree about which acks are outstanding."""
    out = []
    for m in (task.get("memos") or []):
        for a in (m.get("acks") or []):
            if not (a.get("disposition") or {}).get("kind"):
                out.append((m, a))
    return out


def disposition_ops(task, kind="noop", value=None, sid=None, now=None, only=None):
    """Retro-disposition ops for undispositioned acks, one per ack.

    `only` — when given, an iterable of (memo, ack) pairs to act on instead of all of
    them. That is what makes an EXPLICIT selection surgical: with `--dispose-acks <id8>`
    the acks nobody named must stay undispositioned, so the next scan still flags them.

    The default `noop` + RETRO_NOOP_REASON is the machine's honest fallback for the
    blanket case: it says out loud that the record of what the ack did is gone, rather
    than inventing one."""
    pairs = list(only) if only is not None else undispositioned(task)
    recorded = (value if value is not None else RETRO_NOOP_REASON)
    ops = []
    for m, a in pairs:
        ops.append({"verb": "disposition", "memo": m.get("id"), "sid": a.get("sid"),
                    "kind": kind, "value": recorded,
                    "disposition": retro_disposition(kind, recorded, sid=sid, now=now),
                    "manual": False,
                    "why": "ack carries no disposition (retro-filled, and marked as such)"})
    return ops


def select_acks(task, selectors):
    """The `(memo, ack)` pairs an explicit `--dispose-acks` selection names, as
    `(pairs, error)`.

    `selectors` is a list of memo id prefixes, or the single word `all`. `all` is
    LEGITIMATE and expected here, not a workaround: these acks were made by sessions
    that no longer exist and whose intent is unrecoverable, so one bulk disposition with
    an honest reason is the correct answer for the whole batch.

    An id that matches no memo is an ERROR naming it — silently disposing of nothing
    would report a reconcile that never happened."""
    items = [str(s).strip() for s in (selectors or []) if str(s).strip()]
    if not items:
        return [], ("heal --dispose-acks: name the memo id8(s) to retro-dispose, or "
                    "`all` for every undispositioned ack on the task")
    pending = undispositioned(task)
    if any(s.lower() == "all" for s in items):
        return pending, None
    pairs, missing = [], []
    for s in items:
        hits = [(m, a) for m, a in pending if (m.get("id") or "").startswith(s)]
        if hits:
            pairs.extend(hits)
            continue
        known = any((m.get("id") or "").startswith(s) for m in (task.get("memos") or []))
        missing.append("%s — %s" % (s, "every ack on it already carries a disposition"
                                    if known else "no memo with that id on this task"))
    if missing:
        # ALL-OR-NOTHING: a selection with a bad id changes nothing, so a typo can never
        # dispose of a different memo's ack than the one that was meant.
        return [], ("heal --dispose-acks: %s. Nothing was changed."
                    % "; ".join(missing))
    return pairs, None


def with_dispositions(ops, explicit):
    """`ops` with every blanket retro-disposition REPLACED by the explicitly-chosen
    ones. Splits and merges are untouched — naming an ack disposition says what to do
    with the acks, not that the rest of the plan should stop."""
    return [o for o in (ops or []) if o.get("verb") != "disposition"] + list(explicit or [])


# -- the undo trail: what replaces an approval gate ---------------------------------
#
# `/heal` USED TO STOP AND ASK before it applied anything, and that question was where a
# wrong call got caught. It is gone — the pass now runs scan → judge → apply → verify in
# one command, because stopping between steps is the cost the user actually feels.
#
# REMOVING A GATE IS ONLY DEFENSIBLE IF REVERSING A WRONG CALL IS AS CHEAP AS APPROVING
# ONE WAS. So every write this module performs now carries the exact command that undoes
# it, generated at the moment it happens, from the indices it actually touched. Not
# "every heal is reversible" — the paragraph that was already printed, and which nobody
# can act on without first working out which decision numbers moved — but
# `update --task 7 --restore-decision 4`, ready to paste.
#
# IT IS GENERATED, NOT WRITTEN DOWN. A guarantee the tool prints is one the pass cannot
# forget on a long turn; a guarantee in skill prose is one it can skip. `apply()` records
# `op["undo"]` on the ops it actually PERFORMED, so a skipped or failed op never claims a
# reversal that would do nothing.
#
# AND IT SAYS SO WHEN THERE IS NO VERB. A retro-disposition has no inverse: `heal` never
# overwrites a disposition, so nothing can clear one. The honest line for that names the
# pre-heal backup instead of inventing a command — a reversal that does not exist is
# worse than an admitted gap, because the reader only finds out at the moment they need it.


def _task_ref(task):
    """The shortest ref that names this task on a command line — its `seq`, else an id
    prefix. One reader, so every generated undo command is addressed the same way."""
    seq = (task or {}).get("seq")
    return str(seq) if seq else str((task or {}).get("id") or "")[:8]


def _restore_flags(indices, flag="--restore-decision"):
    """`--restore-decision 3 --restore-decision 7` — REPEATED, not comma-joined, because
    that is the only form the flag takes (`action="append", type=int`). A comma list
    would print an undo command that argparse rejects, which is the one failure mode an
    undo line must not have."""
    return " ".join("%s %d" % (flag, int(n)) for n in indices)


def undo_lines(ops, heading=True):
    """The reversal for every op that was actually PERFORMED, as display rows.

    Reads `op["undo"]`, which `apply()` writes only on success — so this can never offer
    a command for an operation that did not happen. Returns [] when nothing was
    performed, which keeps a no-op report silent rather than printing an empty promise."""
    rows = [o.get("undo") for o in (ops or []) if o.get("undo")]
    if not rows:
        return []
    out = []
    if heading:
        out.append("UNDO — every write above, and the ONE command that reverses it. This "
                   "is what replaces the approval gate: nothing here was confirmed before "
                   "it landed, so each line names how to take it back.")
    out.extend("  • %s" % r for r in rows)
    return out


def _default_append(task, text):
    """Append one decision and return its 1-based index. The test/no-CLI path;
    task-station injects a wrapper around `append_decision` so the event feed stays
    authoritative."""
    entries = task.setdefault("decisions", [])
    entries.append(text)
    return len(entries)


def apply(task, ops, append=None):
    """Perform `ops` on `task` in place. Returns `(lines, applied, skipped)` — one
    human line per operation, and the two counts.

    APPEND-ONLY ORDER: every verb appends its replacement(s) FIRST, then marks the
    original(s) — the mark has to be able to name what replaced it. Because the log
    only ever grows, an index computed before an op stays valid after it, so ops
    planned together can be applied in any order without renumbering.

    NOTHING is deleted, `history` and the `--log` trail are not touched, and an op
    marked `manual` is skipped with a line saying why.

    EACH PERFORMED OP GETS ITS `undo` (see the note above `undo_lines`): the exact
    command that reverses THAT write, with the indices it really touched, recorded here
    at the moment of the write and only on success. The return shape is unchanged —
    callers still unpack `(lines, applied, skipped)` — because the undo rides on the op
    dicts the caller already holds."""
    append = append or (lambda text: _default_append(task, text))
    ref = _task_ref(task)
    entries = task.setdefault("decisions", [])
    lines, applied, skipped = [], 0, 0
    for op in (ops or []):
        verb = op.get("verb")
        if op.get("manual"):
            skipped += 1
            lines.append("skipped %s of decision %s — %s"
                         % (verb, op.get("index"), op.get("why")))
            continue
        if verb == "split":
            new_idx = [append(p) for p in op.get("parts") or []]
            ok, err = _dec.mark_split(entries, op.get("index"), new_idx)
            if ok:
                applied += 1
                op["undo"] = ("SPLIT of decision %s → `update --task %s %s` (the parts it "
                              "became stay on the log; the restore puts the original back "
                              "beside them, and nothing was deleted either way)"
                              % (op.get("index"), ref,
                                 _restore_flags([op.get("index")])))
                lines.append("split decision %s into %s (original kept in history)"
                             % (op.get("index"), ", ".join(str(n) for n in new_idx)))
            else:
                skipped += 1
                lines.append("could not split decision %s — %s" % (op.get("index"), err))
        elif verb == "merge":
            into = append(op.get("into") or "")
            done = []
            for i in op.get("indices") or []:
                ok, err = _dec.mark_merged(entries, i, into)
                if ok:
                    done.append(i)
                else:
                    lines.append("could not merge decision %s — %s" % (i, err))
            if done:
                applied += 1
                op["undo"] = ("MERGE of decision(s) %s → `update --task %s %s` (the "
                              "summary at %d stays; each restore puts one original back "
                              "beside it — the flag repeats, it does not take a list)"
                              % (", ".join(str(i) for i in done), ref,
                                 _restore_flags(done), into))
                lines.append("merged %s into %d (each original kept in history)"
                             % (", ".join(str(i) for i in done), into))
            else:
                skipped += 1
        elif verb == "disposition":
            disp = op.get("disposition") or retro_disposition(op.get("kind"),
                                                              op.get("value"))
            sid8 = (op.get("sid") or "?")[:8]
            mid8 = (op.get("memo") or "?")[:8]
            status = _set_disposition(task, op.get("memo"), op.get("sid"), disp)
            if status == DISPOSITION_SET:
                applied += 1
                op["undo"] = ("RETRO-DISPOSITION of ack %s on memo %s → NO VERB REVERSES "
                              "THIS ONE. A heal never overwrites a disposition, so there "
                              "is nothing that can clear it; the only way back is the "
                              "pre-heal task blob named below. Said plainly here rather "
                              "than left to be discovered at the moment it is needed"
                              % (sid8, mid8))
                lines.append("retro-dispositioned ack %s on memo %s as %s — marked "
                             "RETRO (filled in by this pass, not by the acking session, "
                             "whose sid and timestamp are untouched)"
                             % (sid8, mid8, disp.get("kind")))
            elif status == DISPOSITION_ALREADY:
                skipped += 1
                lines.append("left ack %s on memo %s exactly as it was — it already "
                             "carries a disposition, and a heal never overwrites one the "
                             "acking session chose" % (sid8, mid8))
            else:
                skipped += 1
                lines.append("could not find ack %s on memo %s" % (sid8, mid8))
        else:
            skipped += 1
            lines.append("unknown operation %r — skipped" % verb)
    return lines, applied, skipped


# What `_set_disposition` did, in one word each.
DISPOSITION_SET = "set"           # the retro disposition was recorded
DISPOSITION_ALREADY = "already"   # the ack already had one; nothing was touched
DISPOSITION_MISSING = "missing"   # no such ack — nothing was invented


def _set_disposition(task, memo_id, sid, disposition):
    """Record a disposition on one EXISTING ack, returning what it did.

    TWO refusals, and both are the "do not forge history" rule in code:

      * it never CREATES an ack — a heal documents what happened, it does not invent an
        acknowledgement that never was;
      * it never OVERWRITES a disposition the acking session chose. Only an ack with
        nothing recorded can be retro-filled.

    The ack's own `sid` and `ts` are never written — the original session's testimony
    about who saw the memo and when is left exactly as it was, and everything this adds
    lives under the `disposition` key, marked retro."""
    for m in (task.get("memos") or []):
        if m.get("id") != memo_id:
            continue
        for a in (m.get("acks") or []):
            if a.get("sid") == sid:
                if (a.get("disposition") or {}).get("kind"):
                    return DISPOSITION_ALREADY
                a["disposition"] = dict(disposition or {})
                return DISPOSITION_SET
    return DISPOSITION_MISSING


# -- rendering the model-facing report -------------------------------------------

def _fmt_age(seconds):
    if seconds is None:
        return "never"
    d = int(seconds // 86400)
    if d >= 1:
        return "%dd ago" % d
    h = int(seconds // 3600)
    return ("%dh ago" % h) if h >= 1 else "just now"


def heal_stamp_line(h):
    """How the last heal reads: `never (nothing has been recorded)` or
    `2d ago (marked) · 3 new decision(s) since · why: …`.

    Never-healed is stated as a FACT about the record rather than as a count of "new"
    decisions, because on a task that has never been reconciled every decision would
    otherwise be reported as new — a number that is both false and un-actionable."""
    if h.get("never_healed"):
        return "never — nothing has ever recorded a heal on this task"
    bits = [_fmt_age(h.get("since_heal"))]
    kind = h.get("last_heal_kind")
    if kind:
        bits.append("(%s)" % kind)
    new = h.get("new_since_heal")
    if new is not None:
        bits.append("· %d new decision(s) since" % new)
    note = (h.get("last_heal_note") or "").strip()
    if note:
        bits.append("· why: %s" % note)
    return " ".join(bits)


def health_line(h):
    """The one-line health metric — the number that says a task is under-reconciled."""
    return ("%d current decision(s) of %d (%d replaced) · %d chars · longest %d · "
            "average %d · %d pinned · last heal %s"
            % (h.get("decisions_current", 0), h.get("decisions_total", 0),
               h.get("decisions_replaced", 0), h.get("chars", 0), h.get("longest", 0),
               h.get("average", 0), h.get("pinned", 0), heal_stamp_line(h)))


def scan_lines(result):
    """The findings as display rows, grouped by check in CHECKS order. A check with
    nothing to say is named as clean, so the report proves it ran."""
    per = {}
    for f in result.get("findings") or []:
        per.setdefault(f["check"], []).append(f)
    out = []
    for slug, title in CHECKS:
        hits = per.get(slug) or []
        if not hits:
            out.append("  %-28s clean" % title)
            continue
        out.append("  %-28s %d" % (title, len(hits)))
        for f in hits:
            out.append("      • %s — %s" % (f["ref"], f["detail"]))
    unknown = sum(1 for l in (result.get("links") or []) if l.get("state") is None)
    if unknown:
        out.append("  (%d link(s) UNKNOWN — no network probe was run; a link is never "
                   "reported dead on a failed check)" % unknown)
    return out


def dismissed_line(result):
    """ONE row saying how many findings the ledger is silencing, or [] when none.

    A COUNT AND A POINTER, never a list. The whole point of a dismissal is that the reader
    has already answered that finding, so re-printing the nine of them would put the cost
    straight back. But it must not be INVISIBLE either — a silenced finding nobody can see
    is indistinguishable from a check that stopped running — so the count is always there
    with the command that expands it."""
    n = len((result or {}).get("dismissed") or [])
    if not n:
        return []
    return ["  %-28s %d  (adjudicated and silenced: out of the findings, the issue count "
            "and the due calculus. `heal --dismissals` lists each one with its why and its "
            "date; a dismissal covers ONE exact text, so an edit to the underlying entry "
            "makes the finding re-report)" % ("Dismissed", n)]


def size_line(size):
    """The digest's size against its baseline: `12,400 chars · at last heal 15,900 · -3,500`.

    THE DELTA IS THE POINT. A char total on its own cannot distinguish a task that is 40k
    down from last week from one that is 40k up — both read as one large number — and the
    direction is the only thing that says whether reconciling is working. `not baselined`
    when no stamp recorded one, never a zero delta: see `size_objective`."""
    now = (size or {}).get("now") or 0
    if not (size or {}).get("known"):
        return ("%d chars · no baseline recorded, so the change since the last heal cannot "
                "be counted" % now)
    delta = size.get("delta") or 0
    return ("%d chars · at last heal %d · %s%d"
            % (now, size.get("at_last_heal") or 0, "+" if delta > 0 else "", delta))


def size_lines(result):
    """The size objective as ONE display row, always printed.

    Always, including when it cannot be counted — the rule `accrual_lines` already keeps: a
    measurement that appears only when it has something to say is one the reader never sees
    on the run where it mattered."""
    return ["  %-28s %s  (the OBJECTIVE — a reconcile is supposed to make this number go "
            "DOWN)" % ("Digest size", size_line((result or {}).get("size") or {}))]


def oversized_proposal_lines(result):
    """The proposal-tier oversized decisions as display rows, worst-first, or [].

    Capped and SAYING SO. A real task's decisions average ~1,400 chars, so this tier is the
    normal length of a working entry: forty rows of it is a wall rather than a proposal, and
    a list that silently stopped at five would read as "there were five"."""
    p = (result or {}).get("oversized_proposals") or {}
    rows = p.get("shown") or []
    if not rows:
        return []
    out = ["  %-28s %d  (PROPOSALS — not findings: over %d chars is worth READING, not a "
           "defect. Never counted as an issue and they never make a heal due; past %d "
           "chars one becomes a finding, because that is where an entry stops being "
           "supersedable a piece at a time)"
           % ("Long decisions", p.get("total") or len(rows),
              OVERSIZE_PROPOSAL_CHARS, OVERSIZE_CHARS)]
    for r in rows:
        out.append("      • decision %s · %d chars (%.1f× the %d-char write advisory) — if "
                   "it holds several separate rulings, `heal --split %s --into <n1,n2,…>` "
                   "cuts it; if it is one ruling that needs every word, leave it"
                   % (r.get("index"), r.get("chars") or 0,
                      float(r.get("chars") or 0) / max(1, WRITE_ADVISORY_CHARS),
                      WRITE_ADVISORY_CHARS, r.get("index")))
    if p.get("more"):
        out.append("      • +%d more over %d chars — not listed, because a wall of them is "
                   "not a proposal. `heal --candidates` and the dry run carry the full "
                   "decision set" % (p.get("more"), OVERSIZE_PROPOSAL_CHARS))
    return out


def subject_candidate_lines(result):
    """The SUBJECT-tier merge candidates as display rows, or [].

    Rendered ABOVE the shape tier because they are the stronger evidence: these entries name
    the same step, the same release or the same work item, while a shape group merely opens
    the same way. A group tagged `completed-subject` says the checklist itself reports the
    work finished, which is the closest the record ever comes to stating "true but no longer
    load-bearing" on its own — and it is still a PROPOSAL, because the surviving summary has
    to be written by someone who read the group."""
    cands = (result or {}).get("subject_candidates") or []
    if not cands:
        return []
    done = (result or {}).get("completed_subjects") or []
    head = ("  %-28s %d  (PROPOSALS — not findings: not counted as issues. Grouped by what "
            "the entries are ABOUT — a shared step, release or PR/story — which is why two "
            "members are enough here where the shape tier needs three%s)"
            % ("Subject candidates", len(cands),
               (" · %d decision(s) on this task have ALL their subject steps finished"
                % len(done)) if done else ""))
    out = [head]
    for c in cands:
        idxs = [str(i) for i in (c.get("indices") or [])]
        tags = c.get("tags") or []
        out.append("      • decisions %s share %s%s — READ them: if they are all TRUE BUT "
                   "NO LONGER LOAD-BEARING, write ONE summary decision and `heal --merge "
                   "%s --into <n>`. `heal --candidates` prints these members in full and "
                   "nothing else. Never merged from the grouping alone: choosing what the "
                   "surviving summary says is judgement, and a wrong merge writes a false "
                   "consolidation into the record."
                   % (", ".join(idxs), c.get("signal"),
                      "  [COMPLETED-SUBJECT — every step these name is done or superseded, "
                      "so the work they record is finished]" if "completed-subject" in tags
                      else "",
                      ",".join(idxs)))
    return out


def merge_candidate_lines(result):
    """The merge candidates as display rows, or [] when there are none.

    Worded to be unmistakable about their status. They sit next to the findings and must
    never be read as more of them: nothing here is counted as an issue, nothing here
    makes a heal due, and nothing here is ever performed by `--apply`.

    THE SECONDARY TIER since subject grouping arrived, and still worth having: it catches the
    process-error and scrub-iteration families that name no step, no release and no work
    item, so there is no subject for the stronger tier to match on. A group appearing in both
    is not a contradiction — one says these are ABOUT the same thing, the other says they are
    PHRASED the same way."""
    cands = (result or {}).get("merge_candidates") or []
    if not cands:
        return []
    out = ["  %-28s %d  (PROPOSALS — not findings: they are not counted as issues and "
           "never make a heal due. The SECONDARY tier: grouped by how each entry OPENS, "
           "which is weaker evidence than a shared subject, hence three before it counts)"
           % ("Merge candidates", len(cands))]
    for c in cands:
        idxs = [str(i) for i in (c.get("indices") or [])]
        out.append("      • decisions %s open with the same shape (%r) — READ them: if "
                   "they are all TRUE BUT NO LONGER LOAD-BEARING, write ONE summary "
                   "decision and `heal --merge %s --into <n>`. Proposed from the shape "
                   "ALONE, and never acted on from the shape alone: choosing what the "
                   "surviving summary says is judgment, and a wrong merge writes a false "
                   "consolidation into the record."
                   % (", ".join(idxs), c.get("shape"), ",".join(idxs)))
    return out


def ephemeral_lines(result):
    """The expected-ephemeral count as ONE display row, or [] when there are none.

    Deliberately a count and not a list. The whole reason this exists is that seven
    bullets naming seven wiped scratchpad files read exactly like seven defects; the
    honest report is a single line saying how many recorded paths were somewhere the
    system erases on purpose, so a reader can tell "nothing is wrong" apart from
    "nothing was checked"."""
    gone = (result or {}).get("ephemeral") or []
    if not gone:
        return []
    return ["  %-28s %d  (EXPECTED — session scratchpads and system temp paths are "
            "erased by design, so these are NOT findings, are not counted as issues, "
            "and never make a heal due)" % ("Expected-ephemeral paths", len(gone))]


def pinned_lines(result):
    """The pinned set as display rows, or [] when nothing is pinned.

    INFORMATIONAL, like the health metric. The heading has to carry the WHY, because
    "here are your pinned decisions" on its own reads as trivia — the point is that
    these are the entries a stale line costs the most in."""
    pins = (result or {}).get("pinned_review") or []
    if not pins:
        return []
    out = ["  %-28s %d  (INFORMATIONAL — these brief EVERY session, so stale content "
           "here is the most expensive kind there is: re-read each one and confirm it "
           "is still accurate. Being pinned is not a defect and none of this makes a "
           "heal due. An UNKNOWN age means the append is older than the bounded event "
           "feed, not that the decision is new.)" % ("Pinned decisions", len(pins))]
    for p in pins:
        age = p.get("age")
        out.append("      • decision %s · %s · %d chars — %s"
                   % (p.get("index"),
                      "age unknown" if age is None else _fmt_age(age),
                      p.get("chars") or 0, p.get("preview") or ""))
    return out


def goal_review_lines(result):
    """The goal line as display rows, or [] when the task has no goal.

    A PROPOSAL, and the heading has to say so in the same breath as the count, because a
    number rendered next to nine checks reads as a tenth finding unless something stops
    it. The wording deliberately matches `accrual_line`'s "cannot be counted": the two
    sections answer the same shape of question minutes apart, and two different ways of
    saying "nobody recorded the baseline" would read as two different problems.

    IT NO LONGER CLAIMS IT CAN NEVER MAKE A HEAL DUE, because that stopped being true when
    `due()` gained the goal limb — and a heading that says one thing while the verdict says
    another is worse than either. It says the precise thing instead: never an ISSUE (it is
    not a defect, and `mechanical_line` does not count it), and past the threshold it IS a
    reason to run the pass. The row names the count that would do it, so the number a reader
    sees is the number the verdict used."""
    g = (result or {}).get("goal_review") or {}
    if not g:
        return []
    threshold = goal_review_due()
    if g.get("known"):
        n = g.get("since") or 0
        head = ("%d decision(s) since it was last written%s"
                % (n, (" (%s)" % _fmt_age(g.get("age"))) if g.get("age") is not None
                   else ""))
    else:
        head = ("no baseline was recorded, so what has landed since it was written CANNOT "
                "BE COUNTED — which is not the same claim as nothing")
    if g.get("review_known") and g.get("reviewed_only"):
        head += (" · re-read (not rewritten) %s, %d decision(s) ago"
                 % (_fmt_age(g.get("review_age")), g.get("since_review") or 0))
    seq = (result or {}).get("seq")
    out = ["  %-28s %s  (PROPOSAL — never counted as an ISSUE: an untouched goal is not a "
           "defect. It DOES make a heal due at %d decision(s) since it was last written or "
           "re-read, because at that point the next cold session is taking this sentence as "
           "the plan on evidence nobody has checked it against)"
           % ("Goal review", head, threshold)]
    out.append("      • %s" % (g.get("preview") or ""))
    out.append("      • READ IT AGAINST THE NEWEST DECISIONS: does this still say what "
               "DONE looks like, or does the record now show that mission as already "
               "accomplished — or as one the work has moved past? A cold session reads "
               "this line FIRST, and no check can raise it: nothing else on the task "
               "claims to say what done looks like, so there is nothing to "
               "cross-reference it against. An untouched goal is NOT a defect — a goal is "
               "supposed to outlive the decisions that pursue it — so this is a reason to "
               "look, never proof of anything. If it has drifted: `update %s--goal '<what "
               "done looks like now>'`. If it is STILL RIGHT, say so and reset the count "
               "without touching it: `heal --goal-reviewed%s`. Those are the only two "
               "honest endings — rewriting a correct goal to prove you read it puts a false "
               "edit in the record, and leaving it unrecorded means the next session is told "
               "nobody has looked."
               % ("--task %s " % seq if seq else "",
                  " --task %s" % seq if seq else ""))
    return out


def _accrual_phrase(src, plus=False):
    """`+3 decisions · +1 log entry · +0 PR/story links · +2 steps` — one wording, used
    for both the since-a-stamp deltas and the never-healed totals."""
    out = []
    for one, many, key in ACCRUAL_PARTS:
        n = src.get(key) or 0
        out.append("%s%d %s" % ("+" if plus else "", n, one if n == 1 else many))
    return " · ".join(out)


def accrual_line(a):
    """The one-line answer to "how much has been recorded since the last heal?".

    Deliberately shaped like `save.since_line` — the two reports are read by the same
    people minutes apart, and two different ways of saying "nothing was baselined" would
    read as two different problems."""
    if a.get("never"):
        return ("no heal has ever been recorded, so all of it has accrued since the task "
                "was created: %s" % _accrual_phrase(a.get("totals") or {}))
    if not a.get("known"):
        return ("last heal %s — but it recorded no baseline (an older version wrote it), "
                "so what has accrued since cannot be counted" % _fmt_age(a.get("age")))
    return "%s (last heal %s)" % (_accrual_phrase(a, plus=True),
                                  _fmt_age(a.get("age")))


def accrual_lines(result):
    """The accrual count and THE ONE GAP THIS LAYER CANNOT COVER, as display rows.

    ALWAYS printed — including when every count is zero, and including on a scan that
    found nothing. That is the whole design: the incident behind this section was a task
    that scanned CLEAN on all eight checks while a shipped release sat recorded nowhere on
    it, and a gap named only when something happens to have accrued is a gap the reader
    never sees on the run where it mattered. A clean scan must not be readable as
    "everything that happened is in here", because the scan cannot know that."""
    a = (result or {}).get("accrual") or {}
    return [
        "  %-28s %s  (INFORMATIONAL — never a finding)"
        % ("Accrued since last heal", accrual_line(a)),
        "      • VERIFY WHAT ACTUALLY SHIPPED — the ONE gap this scan STRUCTURALLY cannot "
        "cover. Every check above works by cross-referencing two things the task itself "
        "holds; a release that shipped, a PR that merged or a document that was written "
        "with NOTHING on this task mentioning it leaves nothing to cross-reference, so no "
        "check can tell that apart from nothing having happened. Confirm from OUTSIDE the "
        "record — the conversation, the repo, the PR list — that everything which actually "
        "shipped since the last heal has a decision. This is judgement, like re-reading the "
        "pinned set, and it is the reason a clean scan is not the same as a complete "
        "record.",
    ]


# -- the closing verdict: MECHANICAL and JUDGMENT are two different questions ------
#
# `Heal due? no` was the last line every heal surface printed, and on its own it reads as
# "this task is a complete record". It never meant that. It means the cross-referencing
# checks found nothing — and this module's own history is what proves the two are not the
# same: one real task scanned clean on every check while a shipped release sat recorded
# NOWHERE on it, and another scanned clean while its goal described a mission already
# accomplished and five live steps named work its own superseded decisions had retired. In
# both, `Heal due? no` was literally true and was read as a verdict on the task.
#
# So the verdict is rendered as TWO measurements and then the verdict: what the machine
# checked, whether the half no machine can check has been RECORDED, and only then whether
# a heal is due. The Judgment row cites the one piece of evidence a task can actually hold
# about that — a heal stamp carrying a `--note` — and says NOT RUN otherwise, because
# "nothing records one" and "it was not done" are different claims and only the first is
# supported by the record.
#
# RENDERING ONLY. `due()` keeps its signature and its return shape untouched: the
# SessionStart nag, the gate file and `gate_line` all read it, and a change there would
# ripple well past a report.

SUMMARY_LABEL_WIDTH = 28      # the column every heal surface already aligns its rows on


def mechanical_line(result):
    """`clean` or `3 issue(s)` — what the deterministic checks found, and nothing more.

    On its own row so it cannot be read as a verdict on the whole task, which is exactly
    what the single `Heal due?` line was being read as."""
    n = len((result or {}).get("findings") or [])
    return "clean" if not n else "%d issue(s)" % n


def judgment_line(task, result=None):
    """Whether the half no check can do has been RECORDED, and — when it has not — what
    the scan structurally cannot see.

    A `--mark-healed --note '<what you checked>'` is the only thing a task can hold that
    says somebody read the record and ruled on it: `--apply` stamps for performing
    mechanical operations, which is not the same claim, and neither is a bare timestamp.
    So a note is the one piece of evidence this line will cite, and without one it says
    NOT RUN — not as an accusation, but because the record supports "nothing recorded one"
    and does not support "it was done".

    A recorded pass STILL gets the caveat when decisions have landed since it. That pass
    ruled on the record as it stood THEN, and the newest evidence is precisely what
    retires a goal, a step or a pinned line — the incident behind this whole section was a
    task whose goal had been overtaken by decisions written after the last heal."""
    h = (result or {}).get("health") or health(task)
    note = (h.get("last_heal_note") or "").strip()
    if not note:
        return ("NOT RUN — nothing records one. The checks above cross-reference the "
                "record against ITSELF; none of them can see whether the GOAL, the LIVE "
                "STEPS or a PINNED decision are still TRUE, or whether work that shipped "
                "was recorded at all.")
    since = h.get("new_since_heal") or 0
    return ("last recorded %s — \"%s\"%s"
            % (_fmt_age(h.get("since_heal")), note,
               ("" if not since else
                " · %d decision(s) have landed since, and the newest evidence is what "
                "retires a goal, a step or a pinned line" % since)))


def summary_lines(task, result, now=None, label_width=SUMMARY_LABEL_WIDTH):
    """The three rows every heal surface closes on — MECHANICAL, JUDGMENT, then the
    verdict.

    ONE implementation, used by the scan report, the dry-run brief and the
    zero-operation refusal, so the three surfaces cannot drift into telling a reader
    three different stories about the same task. The verdict keeps its exact old wording
    (`YES — <reasons>` / `no`), because the nag, both gates and every existing reader
    quote it."""
    is_due, reasons = due(task, result=result, now=now)
    row = "  %-" + str(int(label_width)) + "s %s"
    return [row % ("Mechanical", mechanical_line(result)),
            row % ("Judgment", judgment_line(task, result)),
            row % ("Heal due?",
                   ("YES — %s" % "; ".join(reasons)) if is_due else "no")]


def plan_lines(ops):
    """The mechanical plan as display rows."""
    if not ops:
        return ["  (nothing mechanical to do — any remaining work needs judgment, "
                "see below)"]
    out = []
    for op in ops:
        verb = op.get("verb")
        if verb == "split":
            out.append("  split  decision %s → %d part(s)%s   [%s]"
                       % (op.get("index"), len(op.get("parts") or []),
                          "  (MANUAL — skipped by --apply)" if op.get("manual") else "",
                          op.get("why")))
        elif verb == "merge":
            out.append("  merge  decisions %s → 1   [%s]"
                       % (", ".join(str(i) for i in op.get("indices") or []),
                          op.get("why")))
        elif verb == "disposition":
            out.append("  ack    memo %s / %s → %s (RETRO)   [%s]"
                       % ((op.get("memo") or "?")[:8], (op.get("sid") or "?")[:8],
                          op.get("kind"), op.get("why")))
    return out


# -- the two READ-ONLY views: the ledger, and the candidates ------------------------


def _fmt_when(ts_value):
    """A ledger timestamp as a date plus an age — `2026-08-12 (3d ago)`. A date alone makes
    a reader do arithmetic; an age alone loses the date they need to match it against a
    conversation."""
    try:
        t = float(ts_value)
    except (TypeError, ValueError):
        return "at an unknown time"
    return "%s (%s)" % (time.strftime("%Y-%m-%d", time.localtime(t)),
                        _fmt_age(max(0.0, time.time() - t)))


def dismissal_lines(task, rows=None, result=None):
    """The dismissal ledger as display rows — every entry, newest first, with its why.

    THE WHY IS THE POINT OF THE LIST. A dismissal takes a finding out of the report, so the
    only thing standing between that and a buried defect is a reason somebody can now read
    and disagree with. An EXPIRED ruling (active, but its fingerprint matches nothing the
    scan found) is called out in those words: that is the fingerprint design working — the
    text changed, so the finding is being reported again — and it would otherwise look
    exactly like a ruling still in force."""
    rows = dismissal_rows(task, result=result) if rows is None else rows
    seq = task.get("seq") or str(task.get("id") or "")[:8]
    if not rows:
        return ["DISMISSALS — task #%s: none. Nothing on this task has been adjudicated "
                "away, so every finding the scan reports is being counted." % seq]
    active = [r for r in rows if not r.get("retired")]
    out = ["DISMISSALS — task #%s: %d entr%s, %d still in force. A dismissal covers ONE "
           "exact finding text, so editing the entry it names makes the finding re-report; "
           "`heal --apply --undismiss '<check>:<ref>'` retires one and restores full "
           "reporting." % (seq, len(rows), "y" if len(rows) == 1 else "ies", len(active))]
    for r in rows:
        state = "RETIRED" if r.get("retired") else (
            "in force" if r.get("silencing") is not False else
            "EXPIRED — the text it covered has changed, so that finding is being reported "
            "again")
        out.append("  • %s   [%s]" % (_row_label(r), state))
        out.append("      why: %s" % (r.get("why") or "(none recorded)"))
        out.append("      dismissed %s%s%s"
                   % (_fmt_when(r.get("ts")),
                      (" by session %s" % str(r.get("sid"))[:8]) if r.get("sid") else "",
                      (" · retired %s" % _fmt_when(r.get("retired_ts")))
                      if r.get("retired") else ""))
    return out


def candidate_lines(task, result=None):
    """`heal --candidates` — the CHEAP dry run: the goal line, the pinned decisions, and
    each candidate group's members IN FULL. Nothing else.

    WHY THIS EXISTS. The full dry run is ~47,000 chars on a real task and 94% of that is the
    decision list, which is the right price for the judgement it feeds — but a reader who has
    already decided to work the MERGE candidates needs four groups of text, not the corpus.
    This is the same reading with the corpus removed.

    THE GOAL AND THE PINS STAY, because a merge summary is written against them: the goal
    says which of these subjects still matters, and a pinned decision is the one place a
    stale summary would cost the most. Everything else is deliberately absent."""
    result = result if result is not None else scan(task)
    seq = task.get("seq") or str(task.get("id") or "")[:8]
    entries = task.get("decisions") or []
    # Reuse the scan's own grouping when it has one (a gate file written by an older version
    # has neither key, and then this regroups rather than showing nothing).
    groups = candidate_groups(task, subject=result.get("subject_candidates"),
                              shape=result.get("merge_candidates"))
    out = ["[HEAL-CANDIDATES] Task #%s [%s] — %s"
           % (seq, str(task.get("id") or "")[:8], task.get("title"))]
    out.append("The MERGE candidates in full, and nothing else — no findings, no plan, no "
               "corpus. `heal --task %s` is the full dry run when you need the rest." % seq)
    out.append("")
    out.append("THE GOAL LINE — which of these subjects still matters is measured against it:")
    out.append("  %s" % (str(task.get("goal") or "").strip() or "(none set)"))
    pins = (result.get("pinned_review") or [])
    out.append("")
    out.append("PINNED DECISIONS (%d) — these brief EVERY session, so a summary that "
               "contradicts one is the most expensive kind of stale there is:" % len(pins))
    for p in pins:
        out.append("  • decision %s · %d chars — %s"
                   % (p.get("index"), p.get("chars") or 0, p.get("preview") or ""))
    if not pins:
        out.append("  (none pinned)")
    out.append("")
    out.append("%s" % size_line(result.get("size") or {}))
    out.append("")
    if not groups:
        out.append("NO CANDIDATE GROUPS — nothing shares a subject (a step, a release, a "
                   "PR/story) or a leading shape on this task, so there is nothing here to "
                   "merge. That is a perfectly healthy answer.")
        return out
    out.append("CANDIDATE GROUPS (%d) — subject tier first, then shape. For EACH group: are "
               "these all TRUE BUT NO LONGER LOAD-BEARING? If so write ONE summary decision "
               "carrying every lasting rule they hold, then `heal --merge <n1,n2,…> --into "
               "<n>`. The scan proposes, YOU write the survivor, the verb executes — and a "
               "wrong merge writes a false consolidation into the record, which is why "
               "nothing here is ever automatic." % len(groups))
    for g in groups:
        idxs = g.get("indices") or []
        out.append("")
        out.append("  ── %s tier · %s%s · decisions %s"
                   % (g.get("tier"), g.get("label"),
                      "  [COMPLETED-SUBJECT: every step these name is done or superseded]"
                      if "completed-subject" in (g.get("tags") or []) else "",
                      ", ".join(str(i) for i in idxs)))
        for i in idxs:
            if 1 <= i <= len(entries):
                # The same mark every other surface uses for a pin (task-station's
                # DECISION_PIN_MARK). Spelled out here rather than imported, because this
                # module is the one task-station imports and never the other way around.
                out.append("  %2d. %s%s" % (i,
                                            "★ " if _dec.is_pinned(entries[i - 1]) else "",
                                            _dec.text(entries[i - 1])))
        out.append("     → `heal --merge %s --into <n>` once the summary decision exists"
                   % ",".join(str(i) for i in idxs))
    return out
