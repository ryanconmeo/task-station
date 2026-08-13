# save.py
"""The CHECKPOINT pass — what a task's digest is MISSING, and the stamp that means
a real checkpoint was captured.

WHY THIS EXISTS. `save` is the capture half of the pair `heal` reconciles (see
`heal.py`), and it had three of that module's four measured failures plus one worse
one of its own. All four are about the same thing: a report that tells its reader
what they already know, and a record that claims work nobody did.

  * IT ECHOED THE WHOLE DIGEST BACK. Measured on one real task, `/save` emitted
    71,516 characters (~17,900 tokens) and 71,271 of them — 99.7% — were a dump of
    the CURRENT DIGEST, all 62 decisions included. The caller is a session that has
    been working that task all along: it already HAS that state. What it does not
    have is the list of what is MISSING. So the dump is gone and this module
    computes a GAP REPORT instead — a few hundred tokens naming the empty slots, the
    stale ones, what has landed since the last checkpoint, and what the digest now
    costs a fresh session to load. `--verbose` still dumps the digest for the rare
    session that genuinely lacks it.

  * A REPLACED SUMMARY WAS DESTROYED. `update --summary` REPLACES the summary
    wholesale and nothing kept the old one. Every other reconcile verb in this
    codebase is non-destructive — supersede, split, merge and step-supersede all keep
    the original in `history` and offer a restore — and the summary is the FIRST
    field a resuming session reads, so it is the one that could be silently lost to a
    thin save. `push_summary` preserves it, append-only, and `restore_summary`
    (`update --restore-summary [n]`) brings any version back.

  * THE STAMP WAS WRITTEN WHEN THE BLOCK WAS PRINTED. Run `/save`, write nothing, and
    `last_full_save_ts` was set while the summary was still empty — the task then
    claimed it had been fully checkpointed when nothing was captured. This is exactly
    the bug `heal` had, where a bare `--apply` stamped a pass that performed no
    operation, and it matters MORE here: the whole job of this stamp is to tell a
    real structured checkpoint apart from a lighter `--state` refresh. So emitting
    the block records only that a save was STARTED (`save_started_ts`), and the stamp
    belongs to the WRITE — see `is_checkpoint_write`.

THE STAMP IS INFERRED FROM THE WORK, NEVER DECLARED BY A FLAG. A `--checkpoint` flag
would have been the obvious mechanism and it is the wrong one, for the reason
`heal`'s zero-operation `--apply` already proved: a flag is a claim someone can type
without doing anything, and a stamp that is sometimes a lie makes every other stamp
unreadable. A summary AND a state, written together in one call, cannot be typed
without capturing the checkpoint — they ARE the checkpoint. So the pair is the
signal, and `--append-summary` deliberately does not count: only the wholesale
`--summary` asserts "this is the present truth", which is what the stamp claims.

EXACT ARITHMETIC, NOT THE EVENT FEED. Two questions here need to know what changed
since some earlier moment — what has landed since the last checkpoint, and whether
the summary predates the decisions it is supposed to cover. Both could be answered
from the bounded event feed (as `heal.decision_ages` must, because a decision carries
no timestamp of its own), but both can be answered EXACTLY instead by recording the
counts at write time, the way `heal` records `decisions_at_last_heal`. So
`saved_counts` / `summary_counts` / `state_counts` are snapshots of `len(decisions)`
and `len(history)`, and every "N since" number here is subtraction rather than a
feed scan that a busy task can age out from under.

BACK-COMPAT is the frozen-format rule this project already follows: every field this
module writes is an ADDITIVE key on the task blob. A task written by an older version
carries none of them, and reads as never checkpointed, summary-age unknown,
state-age unknown — an honest "cannot tell", never a false finding.

A GAP THAT CANNOT BE JUDGED IS NOT REPORTED. That is the same asymmetry every check
in `heal.py` chose, arriving from the capture side: a missed stale summary costs one
confused resume, while a report that invents staleness on every old task costs the
credibility of the whole block, and this one is read on every single save.

Stdlib only. Imports `decisions` and `steps` (both leaves) — task-station.py imports
THIS, never the other way around.
"""
import time

import decisions as _dec
import steps as _steps

# -- thresholds (module-level so one edit retunes the whole report) ---------------

NEXT_PREFIX = "NEXT:"        # what a state line must LEAD with to be a next action
STALE_SUMMARY_ENTRIES = 3    # decisions + log entries recorded after the summary before it reads stale
STALE_STATE_ENTRIES = 6      # the same currency, for a state line nothing has moved
PREVIEW_CHARS = 90           # enough to recognise which line a finding is about
TOKENS_PER_CHAR = 4          # the conventional rough chars→tokens estimate, always labelled ≈

# The six NAMED SLOTS a checkpoint fills, in the order the report walks them —
# roughly the order a resuming session reads them in.
SLOTS = ("goal", "state", "summary", "steps", "decisions", "links")


def _preview(text, limit=PREVIEW_CHARS):
    """`text` whitespace-collapsed to one line and cut to `limit`, ellipsis included."""
    flat = " ".join(str(text or "").split())
    return (flat[:limit - 1] + "…") if len(flat) > limit else flat


def _gap(slot, detail):
    """One gap. `slot` names WHICH of the six named slots, `detail` says what is wrong
    with it in ordinary words — and, where there is one, the flag that fixes it."""
    return {"slot": slot, "detail": detail}


def leads_with_next(state):
    """True when the state line LEADS with `NEXT:` — the one thing that makes it a
    next ACTION rather than a standing report. Case-insensitive and
    leading-whitespace-tolerant, because the distinction being drawn is "does this
    open with a concrete first move", not "was it typed in capitals"."""
    return str(state or "").strip().upper().startswith(NEXT_PREFIX)


def link_count(task):
    """How many PR/story links the task stores. Both fields take either shape the
    store has ever used — `{"url","desc"}` or a legacy bare string."""
    n = 0
    for field in ("prs", "stories"):
        for e in (task.get(field) or []):
            url = (e.get("url") if isinstance(e, dict) else e) or ""
            if str(url).strip():
                n += 1
    return n


# -- what is EMPTY ----------------------------------------------------------------

def empty_slots(task):
    """Every named slot carrying NOTHING, as `[{"slot", "detail"}, …]` in SLOTS order.

    Each detail says what a resuming session loses by the slot being blank, not merely
    that it is blank — "no summary" is a fact, "the first thing a fresh session reads
    is missing" is the reason to fill it in."""
    out = []
    if not (task.get("goal") or "").strip():
        out.append(_gap("goal", "nothing recorded — a fresh session cannot tell what "
                                "\"done\" looks like. `--goal '<what done looks like>'`"))
    if not (task.get("state") or "").strip():
        out.append(_gap("state", "nothing recorded — a fresh session has no next move at "
                                 "all. `--state 'NEXT: <concrete first move> — <current "
                                 "standing>'`"))
    if not (task.get("summary") or "").strip():
        out.append(_gap("summary", "nothing recorded — and this is the FIRST field a "
                                   "resuming session reads. `--summary '<present truth: "
                                   "paths · branch/env · commands · gotchas · open "
                                   "questions · latest intent>'`"))
    if not _steps.live(task.get("steps")):
        out.append(_gap("steps", "no ACTIVE checklist — nothing says what the plan is or "
                                 "how far it got. `--step-add '<step>'` (repeatable)"))
    if not _dec.live(task.get("decisions")):
        out.append(_gap("decisions", "none current — a resume will re-explore choices "
                                     "this session already made. `--decision '<the call "
                                     "+ why>'`"))
    if not link_count(task):
        out.append(_gap("links", "no PR and no story recorded. `--pr <url>` / `--story "
                                 "<url>` — omit only if this work genuinely has neither"))
    return out


# -- what looks STALE -------------------------------------------------------------
#
# ONE FINDING PER SLOT, first match wins. A state line that is both missing its `NEXT:`
# AND six decisions old is one problem — rewrite it — and reporting it twice would
# inflate the count that tells the reader how much is left to do.
#
# A slot that is EMPTY is never also reported as stale: `empty_slots` already named it,
# and "your summary is missing" plus "your summary is out of date" is the same
# double-count wearing two hats.

def _written_counts(task):
    """The append-only counters a write-time snapshot is taken of: how many decisions
    and how many dated log entries the task carried at that moment.

    These two are the currency of every "how much has happened since" number in this
    module. They are counted from the FULL lists, not the live ones, because a
    reconcile that later supersedes a decision does not un-happen it."""
    return {"decisions": len(task.get("decisions") or []),
            "history": len(task.get("history") or [])}


def _since_counts(task, key):
    """`(new decisions, new log entries)` since the snapshot stored under `key`, or
    None when there is no snapshot to compare against.

    None means CANNOT TELL — an older writer, or a field this version has never
    written — and every caller treats that as silence rather than as a finding."""
    snap = task.get(key)
    if not isinstance(snap, dict):
        return None
    now = _written_counts(task)
    try:
        return (max(0, now["decisions"] - int(snap.get("decisions") or 0)),
                max(0, now["history"] - int(snap.get("history") or 0)))
    except (TypeError, ValueError):
        return None


def _entries_phrase(decisions, entries):
    """`4 decision(s) and 2 log entry/entries` — the shared wording for a since-count."""
    return "%d decision(s) and %d log entr%s" % (decisions, entries,
                                                 "y" if entries == 1 else "ies")


def stale_slots(task):
    """Every named slot that looks DRIFTED, as `[{"slot", "detail"}, …]`.

    Three checks, and each one names the specific thing that is wrong:

      * a `state` line that does not begin with `NEXT:` — it reports where the work
        stands and leaves the resumed session to work out what to do first, which is
        the one thing the field exists to answer.
      * a `summary` written before the decisions and log entries it is supposed to
        cover. Exact, from the write-time snapshot: no event feed, no guessing.
      * a `state` nothing has moved while the record filled up around it.

    An unknown age is SILENT. A task written before this version has no snapshot to
    compare against, and inventing staleness for every one of them would make this
    block cry wolf on its very first run."""
    out = []
    state = (task.get("state") or "").strip()
    if state:
        if not leads_with_next(state):
            out.append(_gap("state",
                            "does not begin with `%s`, so it says where things STAND "
                            "rather than what to DO first: %r"
                            % (NEXT_PREFIX, _preview(state))))
        else:
            since = _since_counts(task, "state_counts")
            if since and sum(since) >= STALE_STATE_ENTRIES:
                out.append(_gap("state",
                                "unchanged while %s were recorded — check the NEXT it "
                                "names is still the next thing"
                                % _entries_phrase(*since)))
    if (task.get("summary") or "").strip():
        since = _since_counts(task, "summary_counts")
        if since and sum(since) >= STALE_SUMMARY_ENTRIES:
            out.append(_gap("summary",
                            "written BEFORE %s — the replacement must now cover them"
                            % _entries_phrase(*since)))
    return out


# -- what has landed since the last real checkpoint --------------------------------

def since_checkpoint(task, now=None):
    """What has been recorded since `last_full_save_ts`, as a dict.

    `never` is the important case and it is worded as a FACT rather than as a count of
    "new" work: on a task nobody has ever checkpointed, every decision on the log is
    what the summary must cover, and calling all of them "new since the last
    checkpoint" would be false twice over — nothing is new, and there was no last
    checkpoint. (`heal.health` learned the same lesson from the other direction.)"""
    now = time.time() if now is None else now
    try:
        ts = float(task.get("last_full_save_ts") or 0) or None
    except (TypeError, ValueError):
        ts = None
    totals = _written_counts(task)
    totals["steps"] = len(task.get("steps") or [])
    # All three deltas come from the SAME snapshot, so they are all known or all
    # unknown. A stamp written by an older version carries no counts, and reporting
    # "+4 steps" beside a blank decision count would read as "no decisions landed"
    # when the truth is "nobody recorded the baseline".
    since = _since_counts(task, "saved_counts") if ts else None
    steps_new = None
    if since is not None:
        try:
            steps_new = max(0, totals["steps"]
                            - int((task.get("saved_counts") or {}).get("steps") or 0))
        except (TypeError, ValueError):
            steps_new = None
    return {
        "ts": ts,
        "never": ts is None,
        "age": (max(0.0, now - ts) if ts else None),
        "started_ts": task.get("save_started_ts"),
        "known": since is not None,
        "decisions": (since[0] if since else None),
        "history": (since[1] if since else None),
        "steps": steps_new,
        "totals": totals,
    }


# -- what the digest COSTS ---------------------------------------------------------

def size(task, digest_chars=None):
    """What a fresh session pays to load this task. `digest_chars` is the rendered
    length of the digest itself, measured by the caller (only task-station.py can
    render one) — None when nobody measured it."""
    live = _dec.live(task.get("decisions"))
    return {
        "digest_chars": digest_chars,
        "digest_tokens": (int(digest_chars // TOKENS_PER_CHAR)
                          if isinstance(digest_chars, int) else None),
        "summary_chars": len((task.get("summary") or "")),
        "decisions_current": len(live),
        "decision_chars": _dec.total_chars(task.get("decisions")),
    }


# -- the report --------------------------------------------------------------------

def gap_report(task, now=None, digest_chars=None):
    """Everything the `[SAVE]` block needs, computed deterministically and with ZERO
    tokens: which named slots are empty, which look stale, what has landed since the
    last real checkpoint, and what the digest now costs.

    NEVER mutates the task — not one field. The mutations live in `mark_save_started`
    and `stamp_checkpoint`, which the caller runs deliberately."""
    now = time.time() if now is None else now
    return {
        "task": task.get("id"),
        "seq": task.get("seq"),
        "ts": now,
        "empty": empty_slots(task),
        "stale": stale_slots(task),
        "since": since_checkpoint(task, now=now),
        "size": size(task, digest_chars=digest_chars),
    }


def is_clean(report):
    """True when nothing is empty and nothing reads as stale — the whole point of the
    report being small enough to check at a glance."""
    return not ((report or {}).get("empty") or (report or {}).get("stale"))


def _fmt_age(seconds):
    """`2d ago` / `3h ago` / `just now` / `never`. Matches `heal._fmt_age` deliberately
    — the two reports sit next to each other and must not measure time differently."""
    if seconds is None:
        return "never"
    d = int(seconds // 86400)
    if d >= 1:
        return "%dd ago" % d
    h = int(seconds // 3600)
    return ("%dh ago" % h) if h >= 1 else "just now"


def since_line(since):
    """The one-line answer to "what must this save's summary now cover?"."""
    totals = since.get("totals") or {}
    if since.get("never"):
        return ("never fully checkpointed — so the summary has to cover ALL of it: "
                "%d decision(s), %d step(s), %d dated log entr%s on the record"
                % (totals.get("decisions", 0), totals.get("steps", 0),
                   totals.get("history", 0),
                   "y" if totals.get("history", 0) == 1 else "ies"))
    if not since.get("known"):
        return ("last full checkpoint %s — but it recorded no baseline (an older "
                "version wrote it), so what has landed since cannot be counted"
                % _fmt_age(since.get("age")))
    parts = []
    for label, key in (("decision", "decisions"), ("step", "steps"),
                       ("log entry", "history")):
        n = since.get(key) or 0
        parts.append("+%d %s%s" % (n, label, "" if n == 1 else "s"))
    return ("%s (last full checkpoint %s) — this is what the summary must now cover"
            % (", ".join(parts), _fmt_age(since.get("age"))))


def size_line(sz):
    """What a fresh session loads, in characters and a labelled token estimate."""
    chars = sz.get("digest_chars")
    head = ("%s chars (≈%s tokens)" % ("{:,}".format(chars),
                                       "{:,}".format(sz.get("digest_tokens") or 0))
            if isinstance(chars, int) else "not measured")
    return ("%s a fresh session loads · summary %s chars · %d current decision(s), "
            "%s chars"
            % (head, "{:,}".format(sz.get("summary_chars") or 0),
               sz.get("decisions_current") or 0,
               "{:,}".format(sz.get("decision_chars") or 0)))


# The two row shapes, so the four sections line up without hand-counted spaces —
# `heal.scan_lines` uses the same `%-28s` column for the same reason.
_LABEL = "  %-27s %s"
_ROW = "      • %-10s %s"


def gap_lines(report):
    """The GAP REPORT as display rows — a few hundred tokens that replaced a 71,271-char
    digest dump.

    A CLEAN SECTION IS STILL PRINTED, in one line, exactly as `heal.scan_lines` names a
    clean check: the report has to prove it ran, or "nothing about the summary" reads
    identically to "the summary was not looked at"."""
    empty = (report or {}).get("empty") or []
    stale = (report or {}).get("stale") or []
    out = []
    if empty:
        out.append("  EMPTY (%d of %d named slots):" % (len(empty), len(SLOTS)))
        out.extend(_ROW % (g["slot"], g["detail"]) for g in empty)
    else:
        out.append(_LABEL % ("EMPTY", "none — all %d named slots carry something"
                             % len(SLOTS)))
    if stale:
        out.append("  STALE (%d):" % len(stale))
        out.extend(_ROW % (g["slot"], g["detail"]) for g in stale)
    else:
        out.append(_LABEL % ("STALE", "none reads as drifted"))
    out.append(_LABEL % ("SINCE THE LAST CHECKPOINT",
                         since_line(report.get("since") or {})))
    out.append(_LABEL % ("DIGEST SIZE", size_line(report.get("size") or {})))
    return out


# -- the MECHANICAL cold-read check ------------------------------------------------
#
# The cold-read check used to be advice — "re-read the digest as if you have no memory
# of this conversation, and patch anything ambiguous". Advice is unfalsifiable: there
# is no output that says whether it was done. These two conditions are the part of it a
# machine can decide, so the machine decides them and the model is left with the part
# that genuinely needs judgement.

def cold_read_failures(task):
    """The mechanical cold-read conditions this task FAILS, as display strings; `[]`
    when it passes. Every named slot must be non-empty and the state must lead with
    `NEXT:` — nothing here is a matter of opinion."""
    out = ["%-10s %s" % (g["slot"], g["detail"]) for g in empty_slots(task)]
    state = (task.get("state") or "").strip()
    if state and not leads_with_next(state):
        out.append("%-10s does not begin with `%s` — a resumed session gets standing, "
                   "not a first move" % ("state", NEXT_PREFIX))
    return out


# -- the summary is non-destructive too --------------------------------------------
#
# `--summary` REPLACES the summary wholesale, and until now nothing anywhere kept the
# old one. Every other reconcile verb in this codebase is non-destructive — supersede,
# split, merge and step-supersede all keep the original in `history` and every one of
# them is reversible — and the summary is the FIRST field a resuming session reads, so
# a thin save silently overwriting a good one is the most expensive loss of the set.
#
# APPEND-ONLY, exactly like `history`: a replaced summary is pushed onto
# `summary_history` and NOTHING ever removes it. Restoring does not pop, either — it
# pushes the current text first and then copies the chosen version forward, so the
# restore is itself reversible and the version numbers a reader was given stay valid.

VERSIONS_FIELD = "summary_history"


def summary_versions(task):
    """Every preserved previous summary, oldest first, as
    `[{"text", "ts", "sid"}, …]`. Blank entries are skipped defensively; the 1-based
    position in THIS list is the number `--restore-summary <n>` takes."""
    return [e for e in (task.get(VERSIONS_FIELD) or [])
            if isinstance(e, dict) and str(e.get("text") or "").strip()]


def push_summary(task, text, sid=None, now=None):
    """Preserve the summary `text` that is about to be replaced. Returns its 1-based
    version number, or None when there was nothing worth keeping (a blank summary is
    not a version — replacing nothing destroys nothing).

    Does NOT save — the caller persists, as everything else on this path does."""
    text = str(text or "").strip()
    if not text:
        return None
    task.setdefault(VERSIONS_FIELD, []).append(
        {"text": text, "ts": (time.time() if now is None else now), "sid": sid})
    # Numbered off the SAME filtered list `--restore-summary <n>` indexes into, so the
    # number this hands the caller is the number that command will take.
    return len(summary_versions(task))


def restore_summary(task, index1=None, sid=None, now=None):
    """Bring a preserved summary back, as `(ok, error, version number)`.

    `index1` defaults to the MOST RECENT version — the overwhelmingly common case is
    "that last save wrote a thin summary, put the good one back", and making someone
    look the number up first is a step between them and undoing a loss.

    The current summary is pushed onto the history FIRST, so a restore is itself
    reversible and nothing this function touches is ever destroyed. Errors — never a
    silent no-op — on a bad index or on a task that has no preserved summary at all."""
    versions = summary_versions(task)
    if not versions:
        return False, ("--restore-summary — this task has no preserved summary: nothing "
                       "has ever replaced one. (A summary is preserved the moment "
                       "`--summary` overwrites a non-empty one.)"), None
    if index1 is None:
        index1 = len(versions)
    try:
        i = int(index1)
    except (TypeError, ValueError):
        return False, ("--restore-summary expects a 1-based version number (as listed by "
                       "`/todo <n> history`), got %r" % (index1,)), None
    if i < 1 or i > len(versions):
        return False, ("--restore-summary %d — no such version; this task has %d "
                       "(see `/todo <n> history` for the numbered list)"
                       % (i, len(versions))), None
    wanted = str(versions[i - 1].get("text") or "")
    # Push BEFORE overwriting, so the text being replaced by the restore survives too.
    # Appending never renumbers what came before it, so the version the caller just
    # named — and every number this task has ever printed — still means the same entry.
    push_summary(task, task.get("summary"), sid=sid, now=now)
    task["summary"] = wanted
    return True, None, i


# -- the stamp ---------------------------------------------------------------------
#
# `last_full_save_ts` means ONE thing: a full structured checkpoint was CAPTURED on
# this task. It is what tells a real checkpoint apart from a lighter `--state` refresh,
# and it was being written the moment the `[SAVE]` block was PRINTED — so a session
# that ran `/save` and then wrote nothing left a task claiming it had been fully
# checkpointed with an empty summary.
#
# That is `heal`'s zero-operation `--apply` bug, one layer earlier: a stamp is a claim
# about work, so only work may write one. Printing a prompt is not work. Emitting the
# block records `save_started_ts` — which is true, and which lets a later reader see a
# save that was begun and abandoned — and the stamp waits for the write.

def mark_save_started(task, now=None):
    """Record that a save was STARTED (the `[SAVE]` block was emitted). This is NOT a
    checkpoint and must never be read as one: it says a prompt was printed, nothing
    more. Does NOT save — the caller persists."""
    task["save_started_ts"] = time.time() if now is None else now


def is_checkpoint_write(summary, state):
    """True when this update IS a full checkpoint: a wholesale `--summary` AND a
    `--state`, both non-empty, in ONE call.

    THE PAIR IS THE SIGNAL, and it is inferred rather than declared. A `--checkpoint`
    flag would be a claim someone can type without capturing anything, which is the
    zero-operation `--apply` all over again; a summary and a state written together
    cannot be typed without capturing the checkpoint, because they ARE it.

    ONE CALL, deliberately. A summary describing a state that a later call then changes
    is not one snapshot, and the block's own template writes both in a single `update`.

    `--append-summary` does NOT count. Only the wholesale `--summary` asserts "this is
    the present truth", and that assertion is exactly what the stamp records."""
    return bool(str(summary or "").strip()) and bool(str(state or "").strip())


def stamp_checkpoint(task, now=None):
    """Record that a FULL CHECKPOINT was captured: the moment, and the counts it left
    behind (the baseline every "since the last checkpoint" number subtracts from).

    Clears `save_started_ts` — the start marker has been answered. Does NOT save.

    STORAGE is additive keys (`last_full_save_ts`, `saved_counts`), which is what the
    frozen-format rule allows: a task written by an older version has neither and reads
    as never checkpointed."""
    now = time.time() if now is None else now
    task["last_full_save_ts"] = now
    counts = _written_counts(task)
    counts["steps"] = len(task.get("steps") or [])
    task["saved_counts"] = counts
    task.pop("save_started_ts", None)


def mark_summary_written(task, now=None):
    """Snapshot the counters the summary's age is measured against. Called after every
    wholesale `--summary`, and AFTER the same update's appends have landed — taking it
    earlier would count this update's own `--decision` as arriving after the summary and
    report a brand-new summary as stale."""
    task["summary_ts"] = time.time() if now is None else now
    task["summary_counts"] = _written_counts(task)


def mark_state_written(task, now=None):
    """The same snapshot for the state line, taken only when its TEXT actually changed
    — re-writing the identical line does not make it fresher, and treating it as if it
    did would let a task keep a stale NEXT alive by copying it forward."""
    task["state_ts"] = time.time() if now is None else now
    task["state_counts"] = _written_counts(task)
