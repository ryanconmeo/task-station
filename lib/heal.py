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

SAFETY — this mutates the decision record on a board holding real work:

  * `--dry-run` IS THE DEFAULT. A bare `heal` prints the plan and changes nothing.
  * `--apply` BACKS UP the task blob first, and refuses to run if the backup fails.
  * The `--log` milestone trail and `history` are NEVER touched. They are append-only
    and sacred — history's whole job is to stay complete.
  * NO verb deletes a decision. Every one of them MARKS the original and drops it from
    the default digest while keeping it in `history`, labelled with what replaced it.
    All three are reversible via `decisions.restore`.

Stdlib only. Imports `decisions`, `steps` and `paths` (all leaves) — task-station.py
imports THIS, never the other way around.
"""
import hashlib
import json
import os
import re
import subprocess
import time

import decisions as _dec
import paths
import steps as _steps

# -- thresholds (module-level so one edit retunes the whole pass) ----------------

OVERSIZE_CHARS = 4000     # a decision longer than this is a split candidate
SPLIT_MAX_PARTS = 8       # cap on mechanically-derived parts; the tail merges into the last
MERGE_MIN_NAMED = 2       # cluster size that merges when a NAMED signature matched
MERGE_MIN_STEM = 3        # cluster size required for the weaker stem signature
STEM_WORDS = 4            # significant leading words that form a stem signature

DUE_NEW_DECISIONS = 10    # new decisions since the last heal that make one due
DUE_AGE_DAYS = 7          # days on an ACTIVE task without a heal that make one due
DUE_AGE = DUE_AGE_DAYS * 86400

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

# The FINDING checks, in the order every surface reports them — `(slug, title)`. These
# are nine of the ten; the tenth is the HEALTH METRIC, which is a measurement rather
# than a finding (a task can be perfectly consistent and still be far too big to brief),
# so it lives in `health()` and renders through `health_line()` above the findings.
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
# than a new heuristic. FIVE vocabularies exist so far — `NON_DECISION_QUALIFIERS`
# (check 3), `DECLARING_QUALIFIERS` (check 8), the two memo sets below, and
# `CONSOLIDATION_QUALIFIERS` (check 9, which asks the same question of a decision
# claiming to be the one record of several: does it DECLARE that, or describe someone
# else's consolidation?).

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
    for m in _DECISION_WORD_REF.finditer(text or ""):
        found.extend(int(n) for n in _NUMBER.findall(m.group(0)))
    for m in _HASH_REF.finditer(text or ""):
        if qualifier(text, m.start()) in NON_DECISION_QUALIFIERS:
            continue
        found.append(int(m.group("n")))
    out = []
    for n in found:
        if own_index is not None and n >= own_index:
            continue
        if total is not None and n > total:
            continue
        if n not in out:
            out.append(n)
    return sorted(out)


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

    So TWO conditions must BOTH hold, not one:

      1. the prose carries supersession language, and
      2. it names a DECISION-SHAPED target — `decision N`, `entry N` or `#N`, pointing
         at an earlier decision that exists on this task (see `decision_refs`).

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
        refs = [n for n in decision_refs(body, own_index=i, total=len(entries))
                if n in still_live]
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

def oversized(task, limit=OVERSIZE_CHARS):
    """Still-current decisions past `limit` chars — split candidates. Only live ones:
    a replaced monster already costs the digest nothing."""
    out = []
    for i, e in _dec.live(task.get("decisions")):
        n = len(_dec.text(e))
        if n > limit:
            out.append(_finding(
                "oversized", "decision %d" % i,
                "%d chars (over the %d limit) — split it into atomic decisions; one "
                "entry this long is unreadable whether or not any of it is wrong"
                % (n, limit)))
    return out


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


def branch_prober(task, exists=os.path.exists, run=_run_git):
    """A `probe(name) -> True | False | None` over the git repos this task touched.

    None means UNKNOWN — no usable repo was found — and unknown is never reported as
    drift. That asymmetry is the point: a false "your branch is gone" is far worse
    than a missed one."""
    dirs, seen = [], set()
    for _kind, p in recorded_paths(task):
        for d in (p, os.path.dirname(p)):
            if d and d not in seen and exists(d) and os.path.isdir(d):
                seen.add(d)
                dirs.append(d)
    repos = [d for d in dirs if _is_repo(d, run)]

    def probe(name):
        if not repos:
            return None
        for d in repos:
            if _has_branch(d, name, run):
                return True
        return False
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

GOAL_TOUCHED_FIELD = "goal_touched"    # {"ts": …, "decisions": N} — the write-time baseline
GOAL_PREVIEW_CHARS = 200               # enough to read the goal, not enough to reprint a page


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
    usable timestamp — unknown, which is not the same claim as recent."""
    body = str(task.get("goal") or "").strip()
    if not body:
        return {}
    now = time.time() if now is None else now
    snap = task.get(GOAL_TOUCHED_FIELD)
    since, known, stamped = None, False, None
    if isinstance(snap, dict):
        try:
            since = max(0, len(task.get("decisions") or []) - int(snap.get("decisions")))
            known = True
        except (TypeError, ValueError):
            since, known = None, False
        try:
            stamped = float(snap.get("ts")) or None
        except (TypeError, ValueError):
            stamped = None
    flat = " ".join(body.split())
    return {"chars": len(body), "since": since, "known": known, "ts": stamped,
            "age": (max(0.0, now - stamped) if stamped else None),
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


# -- the scan --------------------------------------------------------------------

def scan(task, now=None, exists=os.path.exists, branch_probe=None, link_probe=None):
    """Run all ten checks, plus the five sections that are deliberately NOT checks.
    NEVER mutates the task — not one field.

    `findings` is the only key that means "something is wrong", and it is the only one
    `due()` counts. `merge_candidates`, `pinned_review`, `goal_review`, `ephemeral` and
    `accrual` ride alongside it as PROPOSALS, INFORMATION and COUNTS: a task can carry
    plenty of all five and still be perfectly reconciled, so folding any of them into the
    issue count would put `Heal due? YES` on a healthy task — the exact failure this
    module has already had to fix four times.

    `accrual` is the one section that is not about anything the scan FOUND: it is what has
    been recorded since the last heal, printed next to the plain statement that a scan
    reads the record ONLY and cannot know whether something that actually shipped is
    missing from it. See the note above `accrual` for the release that was recorded
    nowhere while every check reported clean.

    Both network-ish probes default to OFF: `branch_probe=None` means no git
    subprocess and `link_probe=None` means no HTTP, so the default scan is pure
    Python plus filesystem stats and is cheap enough for every session start.
    `heal --scan` wires the git prober; the link probe stays opt-in."""
    now = time.time() if now is None else now
    # Probed ONCE and reused for both the findings and the report — calling
    # link_states twice would double every network round-trip.
    links = link_states(task, probe=link_probe)
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
    findings.sort(key=lambda f: CHECK_ORDER.index(f["check"])
                  if f["check"] in CHECK_ORDER else len(CHECK_ORDER))
    return {
        "task": task.get("id"),
        "seq": task.get("seq"),
        "ts": now,
        "findings": findings,
        "health": health(task, now=now),
        "links": [{"kind": k, "url": u, "state": s} for k, u, s in links],
        "merge_candidates": merge_candidates(task),
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
    """`(is_due, [reasons])` — the four independent limbs from the spec, each named in
    plain words so the nag can say WHY:

      * the scan found anything at all,
      * ≥ DUE_NEW_DECISIONS decisions the last heal has never seen,
      * any undispositioned ack exists,
      * more than DUE_AGE_DAYS days without a heal on an ACTIVE task.

    The second limb is worded from the STAMP. With a stamp, "N new decision(s) since the
    last heal" is a true and actionable count. WITHOUT one it used to report the task's
    ENTIRE decision total as "new since the last heal", which is false twice over —
    nothing is new, and there was no last heal — and on a task carrying 97 decisions it
    made the one signal built to be trusted read like a permanent false alarm. So a
    never-healed task says exactly that instead.

    Reasons are returned even when not due, so callers can report the near-misses."""
    now = time.time() if now is None else now
    result = result if result is not None else scan(task, now=now)
    h = result.get("health") or {}
    reasons = []
    n = len(result.get("findings") or [])
    if n:
        reasons.append("the scan found %d issue(s)" % n)
    if h.get("never_healed"):
        total = h.get("decisions_total") or 0
        if total >= DUE_NEW_DECISIONS:
            reasons.append("no heal has ever been recorded, and %d decision(s) are on "
                           "the log" % total)
    else:
        new = h.get("new_since_heal") or 0
        if new >= DUE_NEW_DECISIONS:
            reasons.append("%d new decision(s) since the last heal" % new)
    acks = counts(result).get("ack-undispositioned", 0)
    if acks:
        reasons.append("%d ack(s) carry no disposition" % acks)
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
            reasons.append("%d days since the %s on an active task"
                           % (int(since // 86400), "last heal" if healed else "task was created"))
    return bool(reasons), reasons


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


def gate_line(task, now=None):
    """The one-line "heal first" warning for the `save` and `done` gates, or None.

    Reads the SAME `due` logic as the nag but is NOT self-capping and NEVER persists:
    these gates fire at a decision point (you are about to overwrite the summary, or
    close the task), so they must warn every time. Neither gate blocks, and neither
    runs the heal."""
    if not task:
        return None
    is_due, reasons = due(task, now=now)
    if not is_due:
        return None
    return "heal first — %s. `/todo heal` is a dry run by default." % "; ".join(reasons)


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
    how a frozen format stops being frozen."""
    now = time.time() if now is None else now
    task["last_heal_ts"] = now
    task["decisions_at_last_heal"] = len(task.get("decisions") or [])
    task[ACCRUAL_COUNTS_FIELD] = _recorded_counts(task)
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


def merge_candidate_lines(result):
    """The merge candidates as display rows, or [] when there are none.

    Worded to be unmistakable about their status. They sit next to the findings and must
    never be read as more of them: nothing here is counted as an issue, nothing here
    makes a heal due, and nothing here is ever performed by `--apply`."""
    cands = (result or {}).get("merge_candidates") or []
    if not cands:
        return []
    out = ["  %-28s %d  (PROPOSALS — not findings: they are not counted as issues and "
           "never make a heal due)" % ("Merge candidates", len(cands))]
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
    saying "nobody recorded the baseline" would read as two different problems."""
    g = (result or {}).get("goal_review") or {}
    if not g:
        return []
    if g.get("known"):
        n = g.get("since") or 0
        head = ("%d decision(s) since it was last written%s"
                % (n, (" (%s)" % _fmt_age(g.get("age"))) if g.get("age") is not None
                   else ""))
    else:
        head = ("no baseline was recorded, so what has landed since it was written CANNOT "
                "BE COUNTED — which is not the same claim as nothing")
    seq = (result or {}).get("seq")
    out = ["  %-28s %s  (PROPOSAL — not a finding: never counted as an issue, and it can "
           "never make a heal due)" % ("Goal review", head)]
    out.append("      • %s" % (g.get("preview") or ""))
    out.append("      • READ IT AGAINST THE NEWEST DECISIONS: does this still say what "
               "DONE looks like, or does the record now show that mission as already "
               "accomplished — or as one the work has moved past? A cold session reads "
               "this line FIRST, and no check can raise it: nothing else on the task "
               "claims to say what done looks like, so there is nothing to "
               "cross-reference it against. An untouched goal is NOT a defect — a goal is "
               "supposed to outlive the decisions that pursue it — so this is a reason to "
               "look, never proof of anything. If it has drifted: `update %s--goal '<what "
               "done looks like now>'`."
               % ("--task %s " % seq if seq else ""))
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
