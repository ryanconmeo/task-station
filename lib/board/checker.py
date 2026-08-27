# checker.py
"""The CHECKER — two cheap SessionStart checks, plus the claims a plan makes about
itself.

WHY THIS EXISTS. A plan document can be perfectly written, bound to a task, and then
sit untouched while every surface reports the task healthy. Measured on one real task:
a release condition — "3.0.0 shipped" — sat with nothing completed against it for
FIFTEEN days, and nothing anywhere said so. `heal` could not catch it, and that is not
a gap in `heal`: every check there cross-references two things the task itself holds
(prose against structure, a memo against its target, a recorded path against the
filesystem), and a goal condition that nobody has worked on contradicts NOTHING. There
is no inconsistency to find. The record is silent because nothing happened, and
"nothing happened" is exactly what no cross-reference can see.

So this module asks a different question, twice:

  * GOAL DRIFT — the goal line, by convention, carries numbered DONE conditions
    (`DONE = (1) …; (2) …`). Map the completed checklist steps onto those conditions
    and report any condition that no completed step has touched in N days. That is the
    check that would have caught the fifteen days.
  * THE LOCAL POINTER CHECK — every path, symlink, linked-worktree branch and bound
    document the task declares still resolves. A digest that points a resumed session
    at somewhere it cannot go is worse than a digest that says nothing.

Plus the mechanism that lets a plan document be checked rather than believed:

  * CLAIMS — a document bound to the task, and a list of `(id, command, expected
    substrings)` assertions registered against it. Verification RUNS them. NEVER at
    session start (see below); on demand, from the CLI, so a plan that says "the
    scrub landed" can be made to prove it.

THE FOUR RULES THIS MODULE INHERITS, and they are not negotiable — `heal` shipped the
cry-wolf bug FOUR separate times and every one of them cost the whole check's
credibility:

  1. FAIL OPEN. Every entry point swallows every exception and returns silence. A
     broken check must never be worse than the silence it replaces, and it must never
     be the reason a session start crashed.
  2. SILENT WHEN HEALTHY. `goal_drift` returns `{}` and `pointers` returns `[]`. There
     is no "all clear" line, ever — a section that appears on every task regardless is
     one readers learn to skip.
  3. UNCOUNTABLE IS NEVER ZERO. This is `heal.goal_review`'s rule, and it is the rule
     that makes this module safe: a condition whose completed steps carry no timestamp,
     or a task with no `goal_touched` baseline, is reported as CANNOT BE COUNTED — never
     as "zero recent completions", which reads as drift and would be a lie. Every task
     that existed before the completion stamp shipped takes that path, so it is the
     COMMON case, not an edge one.
  4. CHEAP. Store reads, `stat` calls and plain file reads. NO git subprocess — the
     same contract `heal.drift` keeps for its default path — and NO claim command, ever,
     on the session-start path.

AND ONE RULE OF ITS OWN: THE NAG SAYS WHAT IT MEASURED, NOT WHAT IT CONCLUDED. Drift is
computed from ATTRIBUTION — which completed steps' words overlap which condition's words
— and attribution is a heuristic. So the line says "no completed step is attributed to
condition (2)", which is exactly true, and asks the reader to look. It does not say the
plan stalled, because this module cannot know that. `heal.goal_review` made the same
choice for the same reason: a goal is supposed to outlive the decisions that pursue it,
so an untouched one is a reason to LOOK and never proof of anything.

Stdlib only. Imports `heal`, `steps`, `config` and `paths` — task-station.py imports
THIS, never the other way around.
"""
import hashlib
import json
import os
import re
import subprocess
import time

import config as _config
import heal as _heal
import paths
import steps as _steps

# -- thresholds (module-level so one edit retunes the whole pass) -----------------

REPORT_DAYS = 3           # a countable condition idle this long is DRIFTED
ESCALATE_DAYS = 7         # …and idle longer than this is ESCALATED (from the plan)

# How much of a step's and a condition's significant vocabulary must agree before the
# step COUNTS as work on that condition.
#
# LOWER THAN `heal.STEP_RESTATEMENT_OVERLAP` (0.30) ON PURPOSE, AND THE ASYMMETRY POINTS
# THE OTHER WAY. There, a missed match costs one unreported finding — the quiet failure
# that module always chooses. HERE a missed match costs a FALSE DRIFT REPORT: a step that
# genuinely completed condition (2) but fails to attribute leaves (2) looking idle, and
# the nag then tells you a plan stalled when it did not. Under-attribution is the
# cry-wolf direction for this check, so the bar sits below the restatement check's.
#
# It is UNVALIDATED, like the constant it is modelled on. 0.20 wants about a fifth of the
# combined vocabulary to agree, which a step and the condition it completes clear easily
# (they name the same artefact) and two unrelated conditions on one task do not — they
# share a handful of project words against a union in the dozens. One constant to retune
# when real numbers exist.
STEP_CONDITION_OVERLAP = 0.20

# Both texts need this many DISTINCT significant words before their overlap means
# anything — `heal.STEP_RESTATEMENT_MIN_WORDS`'s reasoning, halved. Below three words the
# Jaccard ratio is noise. It is lower than heal's six because a DONE condition is
# routinely terse ("3.0.0 shipped"), and refusing to attribute against a short condition
# would manufacture the exact false drift the constant above exists to avoid.
CONDITION_MIN_WORDS = 3

GATE_DIR = "checker"                  # <data_dir>/checker/ — its OWN dir, not heal's
CONDITION_PREVIEW_CHARS = 80          # enough to recognise which condition a line means
NAG_ITEMS = 2                         # findings named inline; the rest roll up as "+N more"

CLAIMS_FIELD = "claims"               # the additive task field this module owns
CLAIM_TIMEOUT = 600                   # seconds per claim command (config-tunable)
CLAIM_OUTPUT_TAIL = 1000              # chars of combined output kept as `got`
CLAIM_ID_MAX = 32                     # a claim id is a label ("C1"), not a paragraph
CLAIM_NONE_MIN_WORDS = 3              # "no" is not a reason; a sentence is

# The finding checks, in the order every surface reports them — `(slug, title)`.
CHECKS = (
    ("pointer-path", "Recorded paths"),
    ("pointer-symlink", "Dangling symlinks"),
    ("pointer-worktree", "Linked worktrees"),
    ("pointer-branch", "Checked-out branches"),
    ("pointer-doc", "Bound claims document"),
    ("pointer-config", "Declared config paths"),
)
CHECK_ORDER = [c[0] for c in CHECKS]
CHECK_TITLES = dict(CHECKS)

# The three states a condition can be in, plus the healthy one.
FRESH = "fresh"                # countable and worked on recently
DRIFTED = "drifted"            # countable and idle >= REPORT_DAYS
ESCALATED = "escalated"        # countable and idle > ESCALATE_DAYS
UNCOUNTABLE = "uncountable"    # no baseline — CANNOT BE COUNTED, never zero
REPORTABLE = (DRIFTED, ESCALATED)

# Where a condition's idle clock started, so the report can say which it is. The two are
# NOT the same claim: "no step has completed since the last one did" is a measurement of
# work, "nothing has completed since the goal was written" is a measurement of silence.
FROM_STEP = "step"
FROM_GOAL = "goal"


def _days(seconds):
    """Whole days in `seconds`, floored — the unit every line here reports in."""
    try:
        return int(max(0.0, float(seconds)) // 86400)
    except (TypeError, ValueError):
        return 0


# -- (a) the DONE-condition grammar ----------------------------------------------
#
# A `DONE` MARKER PLUS `(n)`-NUMBERED SEGMENTS, AND NOTHING ELSE COUNTS. The numbered
# marker is the STRUCTURE GATE, exactly as `heal._ref_shaped` is the gate that stopped
# the drift check scraping branch names out of English prose. Without it this check would
# have to guess which clauses of a free-prose goal are conditions, and a guessed
# condition drifts on a schedule of its own — a permanent alarm about a requirement
# nobody wrote.
#
# So: no `DONE` marker → no conditions → the goal-drift check is SILENT for that task.
# That is the common case and it is correct. A goal with conditions the author never
# numbered is a missed check, which is the cheap failure; a condition this module
# invented is a false alarm, which is the expensive one.

# `DONE =` / `DONE:` — uppercase, and the separator is REQUIRED. Uppercase because the
# marker is a convention the author opts into, and a lowercase "done" is just the English
# word ("what done looks like…"). The separator because "the DONE conditions are" is
# prose ABOUT conditions, not a list of them.
_DONE_MARKER = re.compile(r"\bDONE\b\s*[=:]")

# `(1)` / `(12)` — the numbered segment marker.
_CONDITION_MARKER = re.compile(r"\((\d{1,3})\)")


def done_conditions(goal_text):
    """The goal's numbered DONE conditions as `[{"n": 1, "text": "…"}, …]`, or `[]`.

    `[]` — and therefore total silence from the drift check — whenever the shape is not
    there: no goal, no `DONE =` / `DONE:` marker, or a marker with no `(n)` segments
    after it. See the note above for why the numbered marker is a hard gate rather than
    a preference.

    SPLIT ON THE NUMBERED MARKERS, NOT ON SEMICOLONS. A condition routinely contains its
    own semicolons ("the scrub landed; nothing references the old names"), so splitting
    on the punctuation would shatter one condition into three and then report two
    fragments as separate drifting requirements. The `(n)` markers are the only
    boundaries the author actually declared.

    Segments whose text is EMPTY are dropped: a condition with no words can be neither
    attributed to nor reported in a way a reader could act on.

    Numbers are reported AS WRITTEN (`n`), never renumbered — they are the labels the
    reader sees in the goal line, and renumbering them would silently repoint every
    reference somebody had in hand. Duplicates and gaps are therefore both preserved;
    the result is a LIST, so a repeated number cannot collide with itself."""
    body = str(goal_text or "")
    m = _DONE_MARKER.search(body)
    if not m:
        return []
    tail = body[m.end():]
    marks = list(_CONDITION_MARKER.finditer(tail))
    if not marks:
        return []
    out = []
    for i, mark in enumerate(marks):
        start = mark.end()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(tail)
        text = " ".join(tail[start:end].split()).strip(" ;,.—-")
        if not text:
            continue
        try:
            n = int(mark.group(1))
        except (TypeError, ValueError):
            continue
        out.append({"n": n, "text": text})
    return out


# -- (b) step → condition attribution --------------------------------------------

def attribute(task, conditions=None, minimum=STEP_CONDITION_OVERLAP,
              min_words=CONDITION_MIN_WORDS):
    """Which COMPLETED steps count as work on which DONE condition.

    Returns `(per_condition, unattributed)` where `per_condition` is a list parallel to
    `conditions`, each entry `[{"index": 3, "text": "…", "ts": 1234.5 | None}, …]`, and
    `unattributed` is the same shape for the done steps that matched nothing.

    LIVE, DONE STEPS ONLY. `steps.live` already drops superseded steps, and that is the
    right population: a retired step describes work the plan withdrew, so counting it as
    progress against a condition would credit the goal for work nobody did — the exact
    lie `steps.mark_superseded` exists to avoid.

    ONE CONDITION PER STEP, THE SINGLE BEST OVERLAP. A step that plausibly touches three
    conditions is credited to the one it matches strongest, because crediting all three
    would let one step silence three requirements at once.

    NOTHING IS FORCE-ASSIGNED. A step clearing no condition's bar is UNATTRIBUTED and is
    returned as such — counted, visible in the result, and never rounded into the nearest
    condition. Forcing it would do the one unrecoverable thing this check must not do:
    mark a condition worked-on because a step existed, not because the step was about it.

    Ties go to the EARLIEST condition, deterministically (`>` not `>=`), so the same task
    always attributes the same way and the nag's fingerprint stays stable."""
    conditions = done_conditions(task.get("goal")) if conditions is None else conditions
    per = [[] for _c in conditions]
    unattributed = []
    if not conditions:
        return per, unattributed
    # Tokenize each condition ONCE and compare word SETS. `heal._jaccard` exists as a
    # separate function from `heal.word_overlap` for exactly this caller shape — "one
    # text against many, tokenize the many once" is what its docstring says it is for —
    # and reusing heal's tokenizer rather than writing a second one is the point: a
    # second stopword list would drift from the first the moment either was tuned.
    cond_words = [set(_heal._significant_words(c.get("text"))) for c in conditions]
    for i, s in _steps.live(task.get("steps")):
        if not _steps.is_done(s):
            continue
        body = _steps.text(s)
        row = {"index": i, "text": body, "ts": done_ts(s)}
        words = set(_heal._significant_words(body))
        best, score = None, 0.0
        if len(words) >= min_words:
            for j, other in enumerate(cond_words):
                if len(other) < min_words:
                    continue
                ratio = _heal._jaccard(words, other)
                if ratio > score:
                    best, score = j, ratio
        if best is None or score < minimum:
            unattributed.append(row)
            continue
        per[best].append(row)
    return per, unattributed


# -- (c) the completion stamp -----------------------------------------------------

def done_ts(step):
    """When this step was ticked, or None for "at an unknown time".

    None is a REAL answer and the surfaces must say so rather than substituting a
    number: every step ticked before `steps.set_done` began stamping has no stamp, and
    a missing stamp read as `0` would date that completion to 1970 and report its
    condition as drifted by twenty thousand days. See `condition_states` for how None
    propagates — it makes the condition UNCOUNTABLE, never stale."""
    if not isinstance(step, dict):
        return None
    raw = step.get(_steps.DONE_TS_FIELD)
    try:
        ts = float(raw)
    except (TypeError, ValueError):
        return None
    return ts if ts > 0 else None


# -- (d) goal drift ---------------------------------------------------------------

def report_days():
    """`REPORT_DAYS`, config-overridable. Read FRESH on every call — `config.get` reads a
    file, which tests repoint per-test and a tuning change lands in without a restart.

    The module constant is the fallback of last resort here rather than the only value,
    so an unreadable config degrades to the documented default instead of taking the
    check down with it."""
    return _tunable(_config.checker_report_days, REPORT_DAYS)


def escalate_days():
    """`ESCALATE_DAYS`, config-overridable."""
    return _tunable(_config.checker_escalate_days, ESCALATE_DAYS)


def claim_timeout():
    """`CLAIM_TIMEOUT`, config-overridable."""
    return _tunable(_config.checker_claim_timeout, CLAIM_TIMEOUT)


def _tunable(reader, default):
    """A config accessor's value, or `default` if reading it raised at all. The
    sanitising (positive-only, unparseable → default) lives in `config`; this is only the
    fail-open boundary, so a corrupt config file cannot break a session start."""
    try:
        return reader()
    except Exception:
        return default


def condition_states(task, now=None, report=None, escalate=None):
    """Every DONE condition with its idle clock and its state, as a list — the full
    evaluation, whatever the verdict, and `[]` only when the goal declares no
    conditions.

    Split out from `goal_drift` so the CLI and the tests can read the WHOLE picture
    (including the fresh and the uncountable conditions) while the nag path keeps its
    silent-when-healthy contract. Neither mutates the task.

    NOTE THAT THIS DOES NOT CHECK STATUS. An open or closed task still has readable
    conditions and a reader may legitimately want them; refusing to nag about a parked
    task is `goal_drift`'s job, and putting the gate here too would hide the evaluation
    from the surface that is allowed to show it.

    THE THREE BASELINES, in the order they are tried:

      * the newest STAMPED completion among the steps attributed to the condition —
        the strongest answer there is, because it dates real work;
      * with no attributed done step at all, `task["goal_touched"]["ts"]` — the moment
        the goal line was written (`heal.stamp_goal_touched`). "Nothing has completed
        against this condition since it was written" is the fifteen-day case, and it is
        the whole reason this check exists;
      * with attributed done steps that are ALL unstamped, nothing. The condition is
        UNCOUNTABLE: work demonstrably happened, and there is no honest way to say when.
        Falling back to the goal stamp here would be the worst of the three options — it
        would date the condition to before work that we can see happened.

    A MIX OF STAMPED AND UNSTAMPED steps is countable from the stamped ones: we know
    work landed at that moment, and the unstamped ones can only mean MORE work, never
    less. `unstamped` carries the count so the report can say so.

    `report`/`escalate` are day counts, injected for tests and defaulted from config.
    The comparison is `>=` for DRIFTED and `>` for ESCALATED, exactly as the plan words
    it: three days idle reports, and the eighth day escalates."""
    return _evaluate(task, now=now, report=report, escalate=escalate)[0]


def _evaluate(task, now=None, report=None, escalate=None):
    """`(condition_rows, unattributed_step_indices)` — the ONE evaluation both public
    surfaces read, so attribution runs once per call rather than once per surface."""
    now = time.time() if now is None else now
    report = report_days() if report is None else report
    escalate = escalate_days() if escalate is None else escalate
    conditions = done_conditions(task.get("goal"))
    if not conditions:
        return [], []
    per, unattributed = attribute(task, conditions=conditions)
    goal_base = _goal_touched_ts(task)
    out = []
    for cond, rows in zip(conditions, per):
        stamps = [r["ts"] for r in rows if r["ts"]]
        unstamped = len([r for r in rows if not r["ts"]])
        last, source = None, None
        if stamps:
            last, source = max(stamps), FROM_STEP
        elif not rows and goal_base:
            # No attributed work at all → the goal-write moment is the baseline. Note the
            # `not rows` guard: attributed-but-all-unstamped deliberately falls through
            # to UNCOUNTABLE rather than borrowing the goal stamp, because dating a
            # condition to BEFORE work we can see happened is the one answer that is
            # certainly wrong.
            last, source = goal_base, FROM_GOAL
        row = {"n": cond["n"], "text": cond["text"], "preview": _preview(cond["text"]),
               "done_steps": [r["index"] for r in rows], "unstamped": unstamped,
               "last_ts": last, "from": source, "idle": None, "days": 0,
               "state": UNCOUNTABLE}
        if last:
            idle = max(0.0, now - last)
            row["idle"] = idle
            row["days"] = _days(idle)
            if idle > escalate * 86400:
                row["state"] = ESCALATED
            elif idle >= report * 86400:
                row["state"] = DRIFTED
            else:
                row["state"] = FRESH
        out.append(row)
    return out, [r["index"] for r in unattributed]


def _goal_touched_ts(task):
    """The goal-write moment from `heal.stamp_goal_touched`'s snapshot, or None.

    None on an absent OR garbled snapshot, and None is what makes a condition with no
    attributed work UNCOUNTABLE rather than drifted. This is the whole no-baseline rule
    in one accessor: nothing is inferred from `updated_ts` (which moves for every field)
    or `created_ts` (which would date every pre-existing task's goal to its creation and
    report the entire board as drifted the day this shipped)."""
    snap = task.get(_heal.GOAL_TOUCHED_FIELD)
    if not isinstance(snap, dict):
        return None
    try:
        ts = float(snap.get("ts"))
    except (TypeError, ValueError):
        return None
    return ts if ts > 0 else None


def _preview(text):
    flat = " ".join(str(text or "").split())
    if len(flat) <= CONDITION_PREVIEW_CHARS:
        return flat
    return flat[:CONDITION_PREVIEW_CHARS - 1] + "…"


def goal_drift(task, now=None, report=None, escalate=None):
    """The drifting DONE conditions, or `{}` when there is nothing to report.

    `{}` — SILENT — in every one of these cases, and each is a deliberate refusal:

      * the task is not ACTIVE. `heal.due`'s rule: an open (parked) task is not being
        worked on, so "nothing completed in five days" is a description of parking, not
        of drift. A closed task is not evaluated at all;
      * the goal declares no numbered DONE conditions (`done_conditions`);
      * no condition is countable — every one is UNCOUNTABLE, which is silence by
        construction: unknown is never reported as drift;
      * every countable condition is FRESH.

    The non-empty result carries EVERY condition (`conditions`), not just the drifting
    ones, plus the unattributed done steps — so a reader who has been handed one line
    about condition (2) can see what the other conditions look like and which completed
    steps this module could not place. `drifted` is the reportable subset, worst first."""
    now = time.time() if now is None else now
    if not task or task.get("status") != "active":
        return {}
    report = report_days() if report is None else report
    escalate = escalate_days() if escalate is None else escalate
    rows, unattributed = _evaluate(task, now=now, report=report, escalate=escalate)
    if not rows:
        return {}
    drifted = sorted([r for r in rows if r["state"] in REPORTABLE],
                     key=lambda r: -(r["idle"] or 0))
    if not drifted:
        return {}
    return {"task": task.get("id"), "seq": task.get("seq"), "ts": now,
            "report_days": report, "escalate_days": escalate,
            "conditions": rows, "drifted": drifted,
            "unattributed": unattributed,
            "uncountable": [r["n"] for r in rows if r["state"] == UNCOUNTABLE]}


# -- (e) the local pointer check --------------------------------------------------
#
# PURE STAT AND PLAIN FILE READS. Not one git subprocess, which is why the branch half
# reads `.git`, `HEAD`, `commondir` and `packed-refs` by hand instead of asking
# `git rev-parse`: `heal.drift` documents the same contract for its default path, and a
# session start that shells out per recorded worktree is a session start that got slower
# for everyone to catch a rare failure.
#
# THE OVERLAP WITH `heal.drift` IS DELIBERATE AND NARROW. Both check that a recorded path
# exists, so a deleted worktree can produce a heal nag AND a pointer nag. They are not
# the same statement: heal says "your DECISION RECORD points somewhere unreachable, go
# reconcile it", the pointer check says "the thing this task DECLARES it is working in is
# gone". The second is actionable in this session; the first is a reconcile task. Rather
# than have one silently shadow the other, both speak, once each, self-capped.


def _finding(check, ref, detail):
    """One pointer finding, in `heal._finding`'s shape so every surface that already
    knows how to render a finding renders these too."""
    return {"check": check, "ref": str(ref), "detail": detail}


def _readable(path, limit=4096):
    """The first `limit` chars of a file, or None when it cannot be read.

    None means UNKNOWN, and unknown is never a finding — the asymmetry every check in
    this subsystem keeps. A permissions error on a git pointer file must not be
    reported as a broken worktree."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except (OSError, IOError):
        return None


_GITDIR_LINE = re.compile(r"^\s*gitdir\s*:\s*(.+?)\s*$", re.M)
_HEAD_REF_LINE = re.compile(r"^\s*ref\s*:\s*(refs/\S+)\s*$", re.M)


def _worktree_findings(cwd):
    """The linked-worktree pointer chain for one recorded working directory.

    A LINKED worktree carries a `.git` FILE holding `gitdir: <path>`; a main checkout
    carries a `.git` DIRECTORY and is skipped, because its branch is resolvable only by
    reading the repository proper and this check does not open repositories. So the
    population is exactly the delegated worktrees task-station itself creates — the ones
    that get deleted from under a task.

    The chain, each link a plain read:

      `.git` → `gitdir:` resolves → `<gitdir>/HEAD` → `ref: refs/heads/<b>` →
      the ref file exists under the COMMON git dir (via `commondir`), or `<b>` appears
      in that dir's `packed-refs`.

    Every unreadable link yields SILENCE, never a finding. A finding here means the file
    was read and what it named is positively not there."""
    out = []
    dot = os.path.join(cwd, ".git")
    if not os.path.isfile(dot):
        return out                      # a directory (main checkout) or absent: not ours
    raw = _readable(dot)
    if raw is None:
        return out
    m = _GITDIR_LINE.search(raw)
    if not m:
        return out                      # not the pointer shape we know — say nothing
    gitdir = m.group(1)
    if not os.path.isabs(gitdir):
        gitdir = os.path.join(cwd, gitdir)
    gitdir = os.path.normpath(gitdir)
    if not os.path.isdir(gitdir):
        out.append(_finding(
            "pointer-worktree", cwd,
            "its `.git` file points at %s, which no longer exists — this is a linked "
            "worktree whose administrative directory is gone, so git commands in it "
            "will fail" % gitdir))
        return out                      # nothing downstream can be checked
    head_path = os.path.join(gitdir, "HEAD")
    head = _readable(head_path)
    if head is None:
        if not os.path.exists(head_path):
            out.append(_finding(
                "pointer-worktree", cwd,
                "its git dir %s has no HEAD file, so nothing records which branch is "
                "checked out" % gitdir))
        return out
    ref = _HEAD_REF_LINE.search(head)
    if not ref:
        return out                      # detached HEAD (a raw sha): no branch to check
    refname = ref.group(1)
    common = _common_dir(gitdir)
    if _ref_exists(common, refname):
        return out
    branch = refname[len("refs/heads/"):] if refname.startswith("refs/heads/") else refname
    out.append(_finding(
        "pointer-branch", branch,
        "checked out in %s but %s resolves neither as a loose ref nor in packed-refs "
        "under %s — the branch was deleted while the worktree still sits on it"
        % (cwd, refname, common)))
    return out


def _common_dir(gitdir):
    """The COMMON git dir for a linked worktree — where refs actually live.

    A linked worktree's own git dir holds its HEAD and index; `refs/` belongs to the
    repository, named by the one-line `commondir` file (typically `../..`, relative to
    the git dir). No `commondir` means this IS the common dir."""
    raw = _readable(os.path.join(gitdir, "commondir"), limit=1024)
    if raw is None:
        return gitdir
    rel = (raw.strip().splitlines() or [""])[0].strip()
    if not rel:
        return gitdir
    if not os.path.isabs(rel):
        rel = os.path.join(gitdir, rel)
    return os.path.normpath(rel)


def _ref_exists(common, refname):
    """Whether `refname` resolves under `common` — a loose ref file, or a line in
    `packed-refs`. Both are plain reads.

    True on ANY doubt: an unreadable `packed-refs` returns True rather than reporting a
    branch as deleted on the strength of a file we could not open."""
    if os.path.exists(os.path.join(common, *refname.split("/"))):
        return True
    packed_path = os.path.join(common, "packed-refs")
    if not os.path.exists(packed_path):
        return False
    packed = _readable(packed_path, limit=1 << 20)
    if packed is None:
        return True                     # unreadable ⇒ unknown ⇒ never a finding
    for line in packed.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("^"):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1] == refname:
            return True
    return False


def pointers(task, config_paths=None, exists=os.path.exists):
    """Everything this task DECLARES, checked for still resolving — `[]` when healthy.

    Five families, all of them structured fields the task actually recorded (never prose
    scraped for path-shaped substrings — `heal.recorded_paths` documents why):

      * every non-ephemeral recorded path exists. `heal.ephemeral_path` excludes the
        session scratchpads and temp roots that are ERASED BY DESIGN; on one real task
        all seven of heal's drift findings were exactly those, and a check that cries
        wolf seven times out of seven is worse than no check;
      * a recorded path that IS a symlink resolves to something. Reported separately
        from a plain missing path because the fix is different — the link is there and
        lying, rather than gone;
      * each recorded worktree's linked-worktree pointer chain (`_worktree_findings`),
        ending at the CHECKED-OUT BRANCH, which is the one the plan asked for by name;
      * the bound claims document, if the task has one. A plan bound to a path that
        moved is a plan nothing can check;
      * `config_paths` — an injectable `[(label, path), …]` for station-config-declared
        paths. It is a SEAM, deliberately: an empty list is the honest v1 answer, and
        the call site wires only what it can afford to stat. Guessing which config
        values are paths would report a setting as a missing file.

    `exists` is injected for the same reason `heal.drift` injects it: so a test can
    describe a filesystem it did not have to build."""
    out = []
    for kind, p in _heal.recorded_paths(task):
        if _heal.ephemeral_path(p):
            continue
        if os.path.islink(p) and not exists(os.path.realpath(p)):
            out.append(_finding(
                "pointer-symlink", p,
                "recorded %s is a symlink whose target %s does not exist — the link is "
                "still there, which is why nothing else notices"
                % (kind, os.path.realpath(p))))
            continue
        if not exists(p):
            out.append(_finding(
                "pointer-path", p,
                "recorded %s no longer exists — this task declares it is working there"
                % kind))
            continue
        if kind == "worktree" and os.path.isdir(p):
            out.extend(_worktree_findings(p))
    doc = claims_doc(task)
    if doc and not exists(doc):
        out.append(_finding(
            "pointer-doc", doc,
            "the document this task's claims are bound to no longer exists, so nothing "
            "can verify them (`claims --bind` repoints it)"))
    for label, p in (config_paths or []):
        if not p or exists(p):
            continue
        out.append(_finding(
            "pointer-config", p, "declared %s does not exist" % label))
    out.sort(key=lambda f: CHECK_ORDER.index(f["check"])
             if f["check"] in CHECK_ORDER else len(CHECK_ORDER))
    return out


# -- (f) claims -------------------------------------------------------------------
#
# THE FIELD, ADDITIVE, ONE KEY:
#
#   task["claims"] = {"doc": "/abs/path/plan.md", "bound_ts": 1234.5,
#                     "items": [{"id": "C1", "cmd": "…", "expect": ["…"]}],
#                     "none": {"reason": "…", "ts": …},
#                     "last_verify": {"ts": …, "results": [{"id","ok","got"}]}}
#
# `none` IS THE THIRD STATE, and it exists because the other two could not tell each
# other apart. A task with no claims used to mean either "nobody registered any" or
# "there is deliberately nothing here to re-run", and `verify` answered PASS to both —
# an absence read as a success, which is the one shape this codebase keeps finding.
# `--none '<reason>'` writes the second reading down, with the reason, so `verify` can
# refuse the first. Registering a claim clears it: the two are contradictory statements
# about the same task, and a stale "deliberately none" sitting under three live claims
# would be a lie the store told on every read.
#
# WHY A PLAN NEEDS THESE. A plan document asserts things — the scrub landed, the release
# shipped, the suite is green — and a reader has no way to tell an assertion that is
# still true from one that was true when written. A claim is that assertion with the
# command that settles it attached, so the document can be checked instead of believed.
#
# NEVER AT SESSION START. Not once, not gated, not "only the cheap ones". These are
# arbitrary shell commands the user registered; running them on the session-start path
# would put an unbounded, user-defined cost in front of every session and could have
# side effects nobody asked for at that moment. The pointer check only STATS the bound
# document. Verification is on demand, from the CLI, and that is the whole contract.
#
# `shell=True` IS FINE HERE and is not a lapse: the commands are the user's own, typed
# by them into their own store on their own machine, and a claim like
# `grep -c foo x | wc -l` needs a shell to mean anything. There is no untrusted input
# path into this field.


def claims(task):
    """The task's claims block, or `{}`. Never raises on a garbled field."""
    raw = (task or {}).get(CLAIMS_FIELD)
    return raw if isinstance(raw, dict) else {}


def claims_doc(task):
    """The bound document's path, or None."""
    doc = claims(task).get("doc")
    doc = str(doc or "").strip()
    return doc or None


def claim_items(task):
    """The registered claims, as a list of well-formed `{"id","cmd","expect"}` dicts.

    Filters garbage rather than raising: a claim with no id or no command cannot be run,
    and a store this module did not write is not a reason to break a render."""
    out = []
    for raw in (claims(task).get("items") or []):
        if not isinstance(raw, dict):
            continue
        cid = str(raw.get("id") or "").strip()
        cmd = str(raw.get("cmd") or "").strip()
        if not cid or not cmd:
            continue
        expect = [str(e) for e in (raw.get("expect") or []) if str(e).strip()]
        out.append({"id": cid, "cmd": cmd, "expect": expect})
    return out


def last_verify(task):
    """The stored result of the last verification run, or `{}`."""
    raw = claims(task).get("last_verify")
    return raw if isinstance(raw, dict) else {}


def claims_none(task):
    """The recorded DELIBERATE absence — `{"reason", "ts"}` — or `{}`.

    Distinct from "no claims registered", which is what every task looks like before
    anybody has thought about it. This is the record that somebody DID think about it
    and concluded there is nothing here a later session could usefully re-run."""
    raw = claims(task).get("none")
    return raw if isinstance(raw, dict) and str(raw.get("reason") or "").strip() else {}


def declare_none(task, reason, now=None):
    """Record that this task deliberately registers no claims. `(ok, error)`.

    THE REASON IS MANDATORY AND IS A SENTENCE, for the same reason `memo ack --noop`'s
    is: an escape hatch with a one-word reason is an escape hatch everybody takes, and
    the next reader learns nothing from "n/a". Three words is a low bar that "no" and
    "none" still fail.

    REFUSES while claims are registered rather than dropping them. A caller typing both
    is contradicting themselves, and picking a winner silently — either deleting their
    command list or filing a reason that the very next read contradicts — is worse than
    saying so. Does NOT save; the caller persists."""
    text = " ".join(str(reason or "").split())
    if not text:
        return False, ("claims --none needs a reason — it is the line the next reader "
                       "gets instead of a command they can run.")
    if len(text.split()) < CLAIM_NONE_MIN_WORDS:
        return False, ("claims --none %r is not a reason. Say what a later session would "
                       "have re-run and why it cannot — %d words or more."
                       % (text, CLAIM_NONE_MIN_WORDS))
    if claim_items(task):
        return False, ("claims --none says there is nothing to re-run, but %d claim(s) "
                       "are registered. `--remove` them first if that is what you mean."
                       % len(claim_items(task)))
    block = _block(task)
    block["none"] = {"reason": text, "ts": time.time() if now is None else now}
    return True, None


def clear_none(task):
    """Drop the deliberate-absence record. Returns True when one was there.

    Called by `register` rather than exposed as its own flag: registering a claim IS the
    retraction, and a second command to type would be one people forget, leaving the
    store asserting both."""
    block = claims(task)
    if not block.get("none"):
        return False
    block.pop("none", None)
    if not block.get("doc") and not block.get("items") and not block.get("last_verify"):
        task.pop(CLAIMS_FIELD, None)
    return True


def _block(task):
    """The claims block, created in place if absent. The only writer of the key."""
    raw = task.get(CLAIMS_FIELD)
    if not isinstance(raw, dict):
        raw = {}
        task[CLAIMS_FIELD] = raw
    return raw


def bind_doc(task, path, now=None):
    """Bind (or re-bind) the document these claims are about. `(ok, error)`.

    ABSOLUTE PATHS ONLY. A relative path would resolve against whatever directory the
    session happened to start in, so the same stored value would name different files
    from different shells — and the pointer check would then report a document as missing
    because it was run from elsewhere. Does NOT save; the caller persists, the contract
    `heal.stamp_goal_touched` keeps."""
    p = str(path or "").strip()
    if not p:
        return False, "claims --bind needs a path."
    p = os.path.expanduser(p)
    if not os.path.isabs(p):
        return False, ("claims --bind takes an ABSOLUTE path (got %r) — a relative one "
                       "would mean a different file from every directory." % path)
    block = _block(task)
    block["doc"] = os.path.normpath(p)
    block["bound_ts"] = time.time() if now is None else now
    return True, None


def unbind(task):
    """Drop the bound document, KEEPING the registered claims. `(ok, error)`.

    The items are kept on purpose: unbinding says "this is no longer the document these
    claims are about", which is a common move when a plan is renamed or split, and
    discarding a hand-registered command list as a side effect of that would be
    destructive. `--remove` retires an item; this retires the binding. When neither is
    left the whole key goes, so the task reads exactly as it did before any claim
    existed."""
    block = claims(task)
    if not block.get("doc"):
        return False, "claims --unbind: this task has no bound document."
    block.pop("doc", None)
    block.pop("bound_ts", None)
    if (not block.get("items") and not block.get("last_verify")
            and not block.get("none")):
        task.pop(CLAIMS_FIELD, None)
    return True, None


# `|` is the field separator, and `\|` is a literal pipe. THE ESCAPE IS NOT OPTIONAL
# POLISH: a claim's whole point is to run a real command, and real commands pipe
# (`grep -c foo plan.md | tr -d ' '`). A bare split on `|` would silently truncate that
# command at the first pipe and then compare the expected substring against the output
# of half a command — a claim that fails for a reason having nothing to do with what it
# asserts. So the separator is escapable, and `--help` says so.
_UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")


def parse_registration(spec):
    """`'C1|<cmd>|<expect>[|<expect>…]'` → `({"id","cmd","expect"}, None)`, or
    `(None, error)`.

    THREE REFUSALS, each of them a claim that would otherwise be worse than absent:

      * no id, or no command — there is nothing to run;
      * NO EXPECTED SUBSTRING. A claim with nothing to assert PASSES FOREVER, whatever
        the command prints, which is the one failure mode a verification mechanism must
        not have: it would report green while proving nothing;
      * an id longer than CLAIM_ID_MAX. The id is a label the reader types back
        (`--id C1`), not a sentence.

    Field order is `id`, then command, then every remaining field is an expected
    substring. Whitespace around each field is stripped — a substring that must contain
    leading or trailing spaces is a level of precision this format does not carry, and
    saying so beats pretending."""
    raw = str(spec or "")
    if not raw.strip():
        return None, "claims --register needs 'ID|COMMAND|EXPECTED[|EXPECTED…]'."
    parts = [p.replace("\\|", "|").strip() for p in _UNESCAPED_PIPE.split(raw)]
    cid = parts[0] if parts else ""
    cmd = parts[1] if len(parts) > 1 else ""
    expect = [p for p in parts[2:] if p]
    if not cid or not cmd:
        return None, ("claims --register %r — expected 'ID|COMMAND|EXPECTED[|EXPECTED…]' "
                      "(a literal pipe inside the command is written `\\|`)." % spec)
    if len(cid) > CLAIM_ID_MAX:
        return None, ("claims --register: the id is a short label like C1, not %d "
                      "characters." % len(cid))
    if not expect:
        return None, ("claims --register %s — no expected substring, so the claim would "
                      "pass whatever the command printed. Name at least one string that "
                      "must appear in the output." % cid)
    return {"id": cid, "cmd": cmd, "expect": expect}, None


def register(task, specs, replace=False):
    """Register claims from `--register` specs. `(added, updated, errors)`.

    UPSERT BY ID by default: re-registering `C1` rewrites C1's command and expectations
    in place, keeping its position, and leaves every other claim alone. That is what
    makes the register flag safe to re-run — the shape of every other command in this
    codebase that takes a repeatable flag.

    `--replace` swaps the WHOLE list for what this invocation names, which is the only
    way to shrink it wholesale, and is why it is a separate flag rather than the default.
    A `--replace` whose specs ALL failed to parse changes nothing: emptying the list
    because the caller mistyped it would be the destructive reading of a typo.

    Does NOT save; the caller persists."""
    parsed, errors = [], []
    for spec in (specs or []):
        item, err = parse_registration(spec)
        if err:
            errors.append(err)
            continue
        parsed.append(item)
    if not parsed:
        return 0, 0, errors
    clear_none(task)          # a registered claim retracts "deliberately none"
    block = _block(task)
    if replace:
        block["items"] = []
    items = block.setdefault("items", [])
    if not isinstance(items, list):
        items = []
        block["items"] = items
    added, updated = 0, 0
    for item in parsed:
        for i, existing in enumerate(items):
            if isinstance(existing, dict) and str(existing.get("id")) == item["id"]:
                items[i] = item
                updated += 1
                break
        else:
            items.append(item)
            added += 1
    return added, updated, errors


def remove(task, ids):
    """Drop claims by id. `(removed_ids, missing_ids)`.

    A missing id is REPORTED, never silently ignored: "removed 0 claims" after a typo
    reads as success. Clears the whole key when nothing is left, so the task reads
    exactly as it did before any claim existed. Does NOT save."""
    wanted = [str(i).strip() for i in (ids or []) if str(i).strip()]
    if not wanted:
        return [], []
    block = claims(task)
    items = block.get("items") or []
    removed, kept = [], []
    for raw in items:
        cid = str(raw.get("id") or "").strip() if isinstance(raw, dict) else ""
        if cid and cid in wanted and cid not in removed:
            removed.append(cid)
            continue
        kept.append(raw)
    missing = [i for i in wanted if i not in removed]
    if removed:
        block["items"] = kept
        if (not kept and not block.get("doc") and not block.get("last_verify")
                and not block.get("none")):
            task.pop(CLAIMS_FIELD, None)
    return removed, missing


def _run_claim(cmd, timeout):
    """Run one claim command, returning `(combined_output, status)` where status is
    `"ran"`, `"timeout"` or `"error"`.

    stdout AND stderr, combined, because a claim's evidence lands in whichever one the
    tool chose and requiring the user to know which would make the format a puzzle.
    Never raises: a claim that could not be run is a claim that did not pass, and it
    says which of the two happened."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                           timeout=timeout)
        return ((r.stdout or "") + (r.stderr or "")), "ran"
    except subprocess.TimeoutExpired:
        return "", "timeout"
    except Exception as exc:                       # noqa: BLE001 — fail-open by design
        return str(exc), "error"


def _tail(text, limit=CLAIM_OUTPUT_TAIL):
    """The last `limit` chars of `text`, marked when truncated. THE TAIL, not the head:
    a command's verdict is at the end of its output. Bounded because this lands in the
    task blob and the store is not a log."""
    body = str(text or "")
    if len(body) <= limit:
        return body
    return "…" + body[-(limit - 1):]


# The two primitives above, under public names, because `exits.py` runs user commands
# too — a step's exit condition is the same `(command, expected substrings)` shape
# aimed at a checklist item instead of a document. It calls these rather than growing
# its own copy: what "ran" / "timeout" / "error" mean, and how much output is kept, are
# decisions that must be made in ONE place or the two surfaces will quietly disagree
# about whether a command that wrote to stderr and exited 0 had passed. Aliases rather
# than renames, because `verify(run=…)` defaults to `_run_claim` and the suite patches
# that name.
run_command = _run_claim
output_tail = _tail


def verify(task, only=None, timeout=None, now=None, run=None):
    """Run the registered claims and record the outcome. Returns the results list.

    A result is `{"id", "cmd", "ok", "status", "missing", "got"}`: `ok` is True only when
    EVERY expected substring appears in the combined output, `missing` names the ones
    that did not (so a failure says what was actually wrong rather than dumping output at
    the reader), and `status` distinguishes a command that RAN and disagreed from one that
    TIMED OUT or could not be launched at all. That distinction is the uncountable rule
    again: a claim that never ran has not been refuted.

    `only` restricts to one id (or a list). `run` is injected by the tests so the suite
    never spawns a shell. Stamps `task["claims"]["last_verify"]` and does NOT save — the
    caller persists."""
    now = time.time() if now is None else now
    timeout = claim_timeout() if timeout is None else timeout
    run = _run_claim if run is None else run
    wanted = None
    if only:
        wanted = {str(only)} if isinstance(only, str) else {str(o) for o in only}
    results = []
    for item in claim_items(task):
        if wanted is not None and item["id"] not in wanted:
            continue
        out, status = run(item["cmd"], timeout)
        missing = [e for e in item["expect"] if e not in out]
        if status == "timeout":
            got = "(no output — timed out after %ss)" % timeout
        else:
            got = _tail(out)
        results.append({"id": item["id"], "cmd": item["cmd"],
                        "ok": bool(status == "ran" and not missing),
                        "status": status, "missing": missing, "got": got})
    if results:
        _block(task)["last_verify"] = {"ts": now, "results": results}
    return results


# -- (g) the gate file and the two self-capping nags -------------------------------
#
# ONE JSON FILE PER TASK UNDER <data_dir>/checker/ — its OWN directory, NOT heal's. Two
# subsystems sharing a gate directory would mean `heal --apply`'s `clear_gate` re-arming
# this module's nags as a side effect, and a stale file from either would be ambiguous
# about which check wrote it.
#
# Every read and write fails OPEN: an unreadable gate means we may nag twice, which is
# harmless, and never a crash or a blocked session.


def gate_dir():
    """Resolved fresh on every call — `paths.data_dir()` reads the environment and tests
    repoint it per-test."""
    return os.path.join(paths.data_dir(), GATE_DIR)


def _safe_id(task_id):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(task_id or "unknown"))


def gate_path(task_id):
    return os.path.join(gate_dir(), "%s.json" % _safe_id(task_id))


def read_gate(task_id):
    """The stored gate dict, or `{}` when absent/unreadable/garbled."""
    try:
        with open(gate_path(task_id), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_gate(task_id, extra):
    """MERGE `extra` into the task's gate file. Returns the path, or None.

    A merge rather than a replace, unlike `heal.write_gate`: two independent nags plus
    the unattached listing keep their watermarks in ONE file, so a whole-file write from
    the pointer check would silently re-arm the drift nag every session."""
    data = read_gate(task_id)
    data.update(extra or {})
    path = gate_path(task_id)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        return path
    except Exception:
        return None


def clear_gate(task_id):
    """Drop a task's gate file, re-arming both nags. True when one went."""
    try:
        os.remove(gate_path(task_id))
        return True
    except Exception:
        return False


def _signature(parts):
    """A stable fingerprint of the reported state. hashlib — NOT `hash()`, whose string
    seed is randomised per process, which would make the self-cap silently useless
    (every session would look like a new state and nag again)."""
    blob = "\n".join(sorted(str(p) for p in (parts or [])))
    return hashlib.sha1(blob.encode("utf-8", "replace")).hexdigest()


def drift_signature(result):
    """The fingerprint of a drift report: each drifting condition and ITS TIER.

    THE TIER IS IN THE FINGERPRINT AND THE DAY COUNT IS NOT, and that is the whole
    self-capping design in one choice. Including the days would change the signature
    every midnight, so an unheeded nag would return every single day — the spam this
    pattern exists to prevent. Excluding the tier would mean a condition that drifted
    for three days and then crossed the seven-day escalation would stay silent through
    the crossing, which is the one moment the plan says to speak louder. So: once per
    condition set per tier."""
    return _signature("%s:%s" % (r["n"], r["state"]) for r in (result or {}).get("drifted") or [])


def pointer_signature(findings):
    """The fingerprint of a pointer report: each finding's check and target."""
    return _signature("%s:%s" % (f["check"], f["ref"]) for f in findings or [])


def drift_line(result):
    """The ONE line a drift report renders as, or None. Worst offender named in full,
    the rest rolled up — a nag is one thing to read, not a list."""
    drifted = (result or {}).get("drifted") or []
    if not drifted:
        return None
    seq = result.get("seq") or (str(result.get("task") or "")[:8])
    worst = drifted[0]
    tier = ("ESCALATED, past %d days" % result.get("escalate_days", ESCALATE_DAYS)
            if worst["state"] == ESCALATED else "%d days idle" % worst["days"])
    since = ("nothing has completed against it in the %d day(s) since the goal was written"
             % worst["days"] if worst["from"] == FROM_GOAL
             else "the newest completed step attributed to it finished %d day(s) ago"
             % worst["days"])
    more = (" %d other condition(s) are idle too." % (len(drifted) - 1)
            if len(drifted) > 1 else "")
    return ("[task-station] Task #%s — goal drift (%s): DONE condition (%s) \"%s\" — %s.%s "
            "Attribution is word overlap between completed steps and condition text, so "
            "this is a reason to LOOK at the goal against the checklist (`/todo %s`), "
            "not a verdict."
            % (seq, tier, worst["n"], worst["preview"], since, more, seq))


def pointer_line(task, findings):
    """The ONE line a pointer report renders as, or None."""
    if not findings:
        return None
    seq = task.get("seq") or (str(task.get("id") or "")[:8])
    shown = findings[:NAG_ITEMS]
    named = "; ".join("%s (%s)" % (f["ref"], CHECK_TITLES.get(f["check"], f["check"]))
                      for f in shown)
    more = len(findings) - len(shown)
    if more > 0:
        named += ", +%d more" % more
    return ("[task-station] Task #%s — %d declared pointer(s) no longer resolve: %s. "
            "Nothing was changed; a resumed session would be pointed somewhere it "
            "cannot go." % (seq, len(findings), named))


def drift_nag(task, now=None, persist=True):
    """ONE line for the SessionStart rail, or None.

    Self-capping exactly as `heal.nag` and `hook_health.nag` are: the gate file records
    the fingerprint of the state already reported and this returns None until that state
    CHANGES (see `drift_signature` for what counts as a change). A nag that fires every
    session is one you learn to ignore, and this module's whole value is that when it
    speaks, it is worth reading.

    Fails open at BOTH ends — here, and again at the call site — because a session start
    that crashed is strictly worse than a drift nobody was told about."""
    try:
        result = goal_drift(task, now=now)
        if not result:
            return None
        sig = drift_signature(result)
        if read_gate(task.get("id")).get("drift_sig") == sig:
            return None
        if persist:
            write_gate(task.get("id"),
                       {"drift_sig": sig, "drift_ts": result.get("ts"),
                        "drift": result})
        return drift_line(result)
    except Exception:
        return None


def pointer_nag(task, now=None, persist=True, config_paths=None):
    """ONE line for the SessionStart rail, or None. Self-capping and fail-open, exactly
    as `drift_nag` is.

    Runs on ANY non-closed task, unlike the drift nag: a parked task's declared worktree
    being deleted is a fact about the filesystem, not about whether anyone is working on
    it, and it is precisely what you need told BEFORE you resume."""
    try:
        if not task or task.get("status") == "closed":
            return None
        findings = pointers(task, config_paths=config_paths)
        if not findings:
            return None
        sig = pointer_signature(findings)
        if read_gate(task.get("id")).get("pointer_sig") == sig:
            return None
        if persist:
            write_gate(task.get("id"),
                       {"pointer_sig": sig,
                        "pointer_ts": time.time() if now is None else now,
                        "pointers": findings})
        return pointer_line(task, findings)
    except Exception:
        return None


def worst_drift(tasks, now=None):
    """The ACTIVE task whose goal has drifted longest, as `(task, result)`, or
    `(None, None)`.

    This is the UNATTACHED half of the check, and it is the half that catches the case
    the whole module was built for: a plan sitting untouched while NOBODY attaches to its
    task. The attached nag can only fire for someone who already opened the task; a task
    nobody opens never gets the message.

    Worst = most idle, ties broken by the LOWER seq. The tiebreak is not cosmetic: an
    unstable pick would name a different task each session, so each one would look like a
    fresh state, and the per-task gate would re-arm forever."""
    candidates = []
    for t in tasks or []:
        try:
            if t.get("status") != "active":
                continue
            result = goal_drift(t, now=now)
        except Exception:
            continue                       # one unreadable task must not blind the sweep
        if not result:
            continue
        candidates.append((-(result["drifted"][0]["idle"] or 0),
                           t.get("seq") or 0, t, result))
    if not candidates:
        return None, None
    candidates.sort(key=lambda c: (c[0], c[1]))
    return candidates[0][2], candidates[0][3]


def listing_nag(tasks, now=None, persist=True):
    """ONE line for the UNATTACHED session-start listing, or None.

    Names the single worst offender — never a list. The unattached rail is already an
    inventory of open tasks, and a second inventory underneath it would be read as
    decoration. One task, the one that has been ignored longest.

    Gated per-task on its OWN key (`listing_sig`), not the attached nag's. The two are
    different readers: `drift_sig` answers "has the person working on this task been told
    about this state", `listing_sig` answers "has a session that was NOT attached been
    told". Sharing one key would mean opening the task once permanently silenced the
    board-level warning for that state — the exact way this check would go quiet on the
    fifteen-day case it exists to catch."""
    try:
        task, result = worst_drift(tasks, now=now)
        if not result:
            return None
        sig = drift_signature(result)
        if read_gate(task.get("id")).get("listing_sig") == sig:
            return None
        if persist:
            write_gate(task.get("id"),
                       {"listing_sig": sig, "listing_ts": result.get("ts")})
        return drift_line(result)
    except Exception:
        return None
