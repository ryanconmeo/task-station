# succession.py
"""SESSION SUCCESSION — THE RELAY: a long session hands itself off before it degrades,
and the successor loses nothing.

THE PROBLEM. A working session fills up. What happens today is the harness's own
auto-compaction: at some point near the ceiling it replaces the conversation with a
model-authored summary nobody audited, on its schedule rather than the work's. That is a
reasonable default for a chat and a poor one for a task that has a durable record
sitting right next to it — the summary is generic where the record is task-shaped, and
it lands when the window says so rather than when the work has a clean seam.

THE RELAY IS THE ALTERNATIVE. Stop at a chosen moment, make sure the record is complete,
and hand the task to a FRESH session that starts clean and reads the digest. The 3.0.0
migration did exactly this by hand once per phase, with a HANDOFF-PROMPT the session
rewrote for its own successor. This module is that move made mechanical.

WHAT WAS ALREADY BUILT, and is only used here. Occupancy is measured from the
transcript's own `usage` block (`sessions.measure_context_tokens`); the window size is
harness-authoritative and 1M-aware (`sessions.effective_context_window`); the gap report
and the mechanical cold-read already say whether a record can be read cold
(`save.gap_report` / `save.cold_read_failures`); and `invoke` already knows how to mint a
session that is pre-attached to a task before its window opens. THREE THINGS ARE NEW: the
decision policy, the prompt generator, and the handoff ledger the gate grades.

THE POLICY IS TWO NUMBERS, and both of them are printed.

  * A TRIGGER, as a percentage of the window — the same signal the checkpoint nudge
    already fires on, for the same reason: past that point the session is close enough to
    the ceiling that a deliberate seam beats an involuntary one.
  * A RESERVE, in absolute tokens — what the handoff sequence itself costs. Reconciling
    the record, writing the checkpoint from full context, closing the gaps it names and
    generating the prompt is real work done inside the session that is running out of
    room. A relay attempted with no reserve left produces a thin checkpoint, and a thin
    checkpoint is WORSE than the auto-summary it was meant to beat.

The reserve is absolute rather than a second percentage because the handoff work costs
what it costs: authoring a summary and a continuation prompt is the same job on a 200k
window as on a 1M one, and 20% of the former is 4% of the latter. Percentages would make
the affordable band mean two different things on two models.

3.9.0 gave that choice a second job nobody designed it for, and it is worth knowing about
before anyone converts the reserve to a percentage. The control channel's STAND-DOWN
cannot be settled without a report, and a session that has hit the relay trigger is by
definition the one with least room left to write one. So the absolute reserve is also what
guarantees a stand-down can be ANSWERED rather than dying mid-report — and it guarantees
the same number of tokens for that on every model, which a percentage would not. The two
mechanisms compose here by accident, and the accident is load-bearing.

So three verdicts. Below the trigger, KEEP_GOING — nothing is due, and relaying at 30%
throws away warm context to buy nothing. At or above it with the reserve intact, RELAY.
At or above it with the reserve spent, COMPACT — the relay window was missed, so let the
generic compaction happen and take the seam on the other side of it.

AND A FOURTH VALUE THAT IS NOT A DECISION. `UNKNOWN` is what an unmeasurable session
reports: no transcript, or no usage block in it. It exists because the alternative is
worse in exactly the way this codebase has already paid for — `keep-going` on an
unmeasured session is indistinguishable from `keep-going` on a measured one with room to
spare, and a caller cannot tell that the policy never ran. `lib/exits.py` draws the same
line between UNMET and UNKNOWN, and for the same reason: a check that did not execute has
not passed.

DUE AND READY ARE SEPARATE FACTS, reported separately. The verdict answers "should this
session hand off". `ready`/`blockers` answer "can its record survive one" — every named
slot filled, a state line that leads with `NEXT:`, and a checkpoint that is both taken
and current. A record with gaps does not make the relay less due; it makes it lossy, and
the honest report says both instead of folding one into the other.

PURE, and stdlib only: every function takes what it needs and returns a value. Nothing
here loads the store, reads a transcript, spawns a session or prints — the command seam
does all of that, which is what lets the whole policy be tested against hand-built dicts.
The one exception is the two tunables, which read `config` through the same fail-open
`_tunable` boundary `nudges` and `checker` use.
"""
import time

import config as _config
import loop as _loop
import save as _save
import steps as _steps

# ------------------------------------------------------------------ the verdicts ----

KEEP_GOING = "keep-going"   # under the trigger — nothing is due
RELAY = "relay"             # due, and the reserve to do it properly is still there
COMPACT = "compact"         # due, but the reserve is spent — the seam was missed
UNKNOWN = "unknown"         # occupancy could not be measured, so the policy did not run

# The default trigger matches `checkpoint_pct`'s default deliberately: the moment a
# session should write a structured checkpoint is the moment it should consider handing
# off, and two different defaults for the same threshold would be two answers to one
# question.
DEFAULT_TRIGGER_PCT = 65

# What the handoff sequence costs, in tokens: a reconcile pass if one is due, a full
# checkpoint written from full context, closing the gaps its cold-read names, and the
# continuation prompt. 40k is deliberately generous — the failure this guards against is
# a checkpoint truncated halfway through, and being early costs one session boundary
# while being late costs the record.
DEFAULT_RESERVE = 40000


def _tunable(reader, default):
    """A config accessor's value, or `default` if reading it raised at all. Sanitising
    lives in `config`; this is only the fail-open boundary, so a corrupt config file
    cannot take the report down with it. `nudges._tunable`'s contract, deliberately the
    same three lines."""
    try:
        return reader()
    except Exception:
        return default


def trigger_pct():
    """The relay trigger, as a percentage of the window (1–95).

    THIS TUNABLE HAS NO OFF SWITCH, unlike `checkpoint_pct`. That one gates a NAG, which
    interrupts and therefore has to be silenceable. This gates a report somebody asked
    for, and "off" could only mean "always answer keep-going" — which is a lie at 99% and
    a rubber stamp everywhere else. A non-positive or unparseable value therefore falls
    back to the default rather than disabling the policy."""
    return _tunable(_config.succession_pct, DEFAULT_TRIGGER_PCT)


def reserve():
    """The tokens the handoff itself needs, absolute. See DEFAULT_RESERVE."""
    return _tunable(_config.succession_reserve, DEFAULT_RESERVE)


# --------------------------------------------------------------- the decision ----

def band(measured, window, trigger, keep):
    """The verdict for an occupancy, as `(verdict, why)`.

    THE ORDER OF THE TWO TESTS IS THE WHOLE POLICY. The trigger is asked FIRST, and the
    reserve only afterwards. Reversed, a window smaller than the reserve would report
    `compact` from its very first token — a session at 12% told to compact, which is
    nonsense. The reserve answers "can the handoff be afforded", and that question only
    arises once the trigger says a handoff is wanted."""
    if measured <= 0 or window <= 0:
        return UNKNOWN, ("occupancy could not be measured — no transcript for this "
                         "session, or no usage block in it yet. A policy that did not "
                         "run has not decided anything, so this is not a keep-going.")
    used = round(measured * 100.0 / window)
    threshold = (trigger * window) // 100
    if measured < threshold:
        return KEEP_GOING, ("~%d%% used, under the %d%% trigger (%s tokens) — nothing is "
                            "due. Relaying from here would throw away warm context to "
                            "buy nothing." % (used, trigger, "{:,}".format(threshold)))
    remaining = window - measured
    if remaining < keep:
        return COMPACT, ("~%d%% used and only %s tokens left, under the %s-token reserve "
                         "the handoff itself needs — the relay seam was missed. A "
                         "checkpoint written from here is thinner than the compaction it "
                         "was meant to beat, so compact and take the seam after it."
                         % (used, "{:,}".format(remaining), "{:,}".format(keep)))
    return RELAY, ("~%d%% used, past the %d%% trigger, with %s tokens still in hand — "
                   "enough to checkpoint properly and hand off."
                   % (used, trigger, "{:,}".format(remaining)))


# ----------------------------------------------------------- can it carry one? ----

def handoff_blockers(task):
    """What would be LOST if this task handed off right now, as display strings; `[]`
    when nothing would be.

    Three conditions, none of them a matter of opinion:

      * THE MECHANICAL COLD-READ (`save.cold_read_failures`) — every named slot carries
        something, and the state line leads with `NEXT:`. A successor handed standing
        instead of a first move has to re-derive it, which is the exact loss the relay
        exists to prevent.
      * A CHECKPOINT EXISTS AT ALL. On a task nobody ever checkpointed, the digest was
        never authored for a cold reader; the successor inherits notes written for
        somebody who was there.
      * THE CHECKPOINT IS CURRENT. Decisions, steps and log entries recorded AFTER the
        last full save are, by definition, not in the summary the successor will read.
        The numbers come from `save.since_checkpoint`, so this and the `[SAVE]` block's
        own gap report can never disagree about how much has landed.

    An UNKNOWN age is silent: a task written by an older version carries no baseline, and
    inventing staleness for every one of them would make this cry wolf on first run."""
    out = list(_save.cold_read_failures(task))
    since = _save.since_checkpoint(task)
    if since.get("never"):
        out.append("%-10s never taken — the digest a successor loads was never authored "
                   "for a cold reader. Run a full `/todo save` first."
                   % "checkpoint")
    elif since.get("known"):
        accrued = sum(int(since.get(k) or 0)
                      for k in ("decisions", "steps", "history"))
        if accrued:
            out.append("%-10s %d record change(s) landed after the last full checkpoint, "
                       "so the summary a successor reads describes an older task. "
                       "Re-take it before handing off." % ("checkpoint", accrued))
    return out


# ------------------------------------------------------------------ the report ----

def report(task, measured, window, session=None, trigger=None, keep=None, now=None):
    """The whole answer to "where does this session stand and what should it do", as one
    dict — the object the text view and `--json` both render, so the two can never
    disagree about what was computed.

    `trigger_tokens` is in here because the DECISION is made on tokens while the display
    is a rounded percentage, and those two can straddle the boundary: 129,999 of 200,000
    displays as 65% and is still under a 65% trigger. Printing only the percentage would
    make that read as a bug in the policy instead of the rounding it is."""
    trigger = trigger_pct() if trigger is None else int(trigger)
    keep = reserve() if keep is None else int(keep)
    measured = max(0, int(measured or 0))
    window = max(0, int(window or 0))
    verdict, why = band(measured, window, trigger, keep)
    used = round(measured * 100.0 / window) if window else 0
    blockers = handoff_blockers(task)
    return {
        "ts": time.time() if now is None else now,
        "task": task.get("id"), "seq": task.get("seq"), "session": session,
        "measured": measured, "window": window, "remaining": max(0, window - measured),
        "used_pct": used, "left_pct": max(0, 100 - used),
        "trigger_pct": trigger, "trigger_tokens": (trigger * window) // 100,
        "reserve": keep,
        "verdict": verdict, "why": why,
        "ready": not blockers, "blockers": blockers,
    }


# What each verdict tells the session to DO. A verdict with no instruction is a status
# light, and nobody acts on a status light.
_ACTION = {
    KEEP_GOING: "nothing to do; check again once the trigger is crossed",
    RELAY: "checkpoint from full context, then `relay --spawn` to hand off",
    COMPACT: "let the generic compaction land, then take the seam on the far side of it",
    UNKNOWN: "measure first — no verdict is reachable without an occupancy",
}


def report_lines(rep):
    """The report as display rows: the occupancy, the two numbers the decision was made
    on, the verdict with its action, and what still blocks the handoff.

    A READY RECORD IS STILL PRINTED, in one line — the same rule `save.gap_lines` and
    `heal.scan_lines` follow. Silence about the blockers reads identically to never
    having checked them."""
    rep = rep or {}
    out = ["  %-13s %s used · %s left  (%s of %s tokens)"
           % ("occupancy", "~%d%%" % rep.get("used_pct", 0),
              "~%d%%" % rep.get("left_pct", 0),
              "{:,}".format(rep.get("measured", 0)),
              "{:,}".format(rep.get("window", 0))),
           "  %-13s %d%% of the window = %s tokens · handoff reserve %s tokens"
           % ("policy", rep.get("trigger_pct", 0),
              "{:,}".format(rep.get("trigger_tokens", 0)),
              "{:,}".format(rep.get("reserve", 0))),
           "  %-13s %s — %s" % ("verdict", rep.get("verdict", UNKNOWN),
                                _ACTION.get(rep.get("verdict"), "")),
           "  %-13s %s" % ("why", rep.get("why", ""))]
    blockers = rep.get("blockers") or []
    if blockers:
        out.append("  %-13s %d — the handoff would lose these:"
                   % ("not ready", len(blockers)))
        out.extend("      · %s" % b for b in blockers)
    else:
        out.append("  %-13s the record reads cold and the checkpoint is current"
                   % "ready")
    return out


# ------------------------------------------------------- the continuation prompt ----
#
# THE SUCCESSOR ALREADY HAS THE CONTEXT. Its SessionStart injects this task's digest —
# goal, summary, live decisions, checklist, links — before it reads a word of the prompt.
# So the prompt carries the REQUEST only, which is the rule `invoke --ask` is built on and
# the reason it warns when an ask grows past 800 characters: anything restating the record
# is a lossy second copy of something already present, and a second copy is a thing that
# can drift.
#
# What the digest genuinely CANNOT supply is the framing — that this is a relay and not a
# fresh start — and the NEXT MOVE as an instruction rather than as a field. Those two, the
# open checklist, and how full the predecessor was: that is the whole prompt.
#
# NOTHING HERE CAN READ THE TRANSCRIPT. Not "does not" — cannot: the signature takes a
# task dict and never a session or a path, so the predecessor's conversation is not
# reachable from this function. That is the structural version of the rule, and it is why
# the rule cannot rot.

# The hard cap on the generated prompt. Twice `invoke`'s 800-character ask hint, because a
# relay legitimately carries the predecessor's next move AND its open checklist and
# neither of those is context — but still one to two orders of magnitude under any digest,
# which is the ratio that actually matters.
PROMPT_BUDGET = 1600

STEP_CAP = 5            # open steps named before the list says how many it dropped
STEP_CHARS = 60         # per step
NEXT_CHARS = 320        # the NEXT line's preview; the digest holds all of it
BLOCKER_CAP = 5
BLOCKER_CHARS = 70


def _clip(text, limit):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else (text[:limit - 1].rstrip() + "…")


def open_steps(task):
    """The ACTIVE, NOT-DONE checklist items as `(index1, text)`. Superseded steps are
    excluded by `steps.live`, and finished ones are excluded because a successor being
    handed work it has already done is the same wasted re-derivation as a missing NEXT."""
    return [(i, _steps.text(s)) for i, s in _steps.live(task.get("steps"))
            if not _steps.is_done(s)]


def continuation_prompt(task, rep=None, blockers=None, predecessor=None, successor=None):
    """The prompt the successor is launched with — generated from the RECORD.

    `blockers` is the forced path, and it is the reason this function does not simply
    refuse an incomplete record. Forcing past a failed cold-read is sometimes the right
    call: at 95% a degraded handoff beats no handoff. What must never happen is a
    successor that cannot TELL, so the gaps travel inside the prompt itself and not only
    in the ledger the parent grades later.

    BOUNDED BY CONSTRUCTION: every variable section has a cap, so the worst case is well
    inside PROMPT_BUDGET. A final clamp makes the bound unconditional rather than
    arithmetical — if some future section escapes its cap, the reader sees a truncation
    marker instead of a prompt that quietly became a context dump."""
    seq = task.get("seq") or (task.get("id") or "")[:8]
    who = " — you are session %s" % successor if successor else ""
    from_who = ", succeeding %s" % predecessor if predecessor else ""
    out = ["RELAY on task #%s%s%s." % (seq, who, from_who),
           "",
           "This is a handoff, not a fresh start. That task's digest is already injected "
           "for you: the goal, the summary, the live decisions and the checklist are "
           "THERE. Read it. Do not ask for context and do not re-derive it — everything "
           "the predecessor knew that outlives it is in that record.",
           ""]
    state = (task.get("state") or "").strip()
    if _save.leads_with_next(state):
        out.append(_clip(state, NEXT_CHARS))
    else:
        out.append("The predecessor left no next move — its state line reports standing, "
                   "not a first action. Work the first move out of the digest before you "
                   "start anything.")
    steps = open_steps(task)
    if steps:
        shown = ["%d %s" % (i, _clip(t, STEP_CHARS)) for i, t in steps[:STEP_CAP]]
        tail = ("  (+%d more on the checklist)" % (len(steps) - STEP_CAP)
                if len(steps) > STEP_CAP else "")
        out += ["", "OPEN: " + " · ".join(shown) + tail]
    if blockers:
        out += ["", "GAPS the predecessor left in the record — close these first, because "
                    "the digest you just read is incomplete by exactly this much:"]
        out += ["  · %s" % _clip(b, BLOCKER_CHARS) for b in blockers[:BLOCKER_CAP]]
        if len(blockers) > BLOCKER_CAP:
            out.append("  · (+%d more — `/todo save` names them all)"
                       % (len(blockers) - BLOCKER_CAP))
    if rep and rep.get("window"):
        out += ["", "The predecessor stopped at ~%d%% of a %dk-token window."
                % (rep.get("used_pct", 0), rep["window"] // 1000)]
    text = "\n".join(out)
    if len(text) > PROMPT_BUDGET:
        text = text[:PROMPT_BUDGET - 30].rstrip() + "\n… (trimmed — read the digest)"
    return text


# ------------------------------------------------------------- the handoff ledger ----
#
# A RELAY HAPPENS INSIDE ONE TASK'S LIFE, which is why nothing about it had ever reached
# the gate. The parent grades CHILDREN; a session succeeding itself creates no child, so a
# handoff that lost half the record was invisible to every surface an orchestrator looks
# at. The fix is not a new rubric — it is making each handoff a unit the EXISTING gate can
# grade, carrying the mechanical evidence a grader needs and linked to the grade that
# judged it.
#
# EVERY FIELD IS A MEASUREMENT. Nothing here is the relaying session's characterisation of
# its own handoff: who handed to whom, at what occupancy, against which window, under
# which verdict, and whether it was forced past a failed cold-read. A grader can check all
# of it without taking anyone's word.
#
# `forced` is the one that matters most and the one the engine deliberately does NOT act
# on. Auto-failing a forced handoff would be this module making the judgment call, and the
# split this codebase runs on (lib/loop.py's docstring) puts every judgment on the other
# side of the line. The engine's job is to make the evidence impossible to miss.

HANDOFFS_FIELD = "handoffs"


def handoffs(task):
    """The handoff ledger, oldest first. Never raises on a garbled field. Append-only and
    bounded by nothing — a task that has relayed nine times is telling you something, and
    truncating it would hide exactly that."""
    raw = (task or {}).get(HANDOFFS_FIELD)
    return [h for h in raw if isinstance(h, dict)] if isinstance(raw, list) else []


def record_handoff(task, predecessor, successor, rep, forced=False, blockers=None,
                   now=None):
    """Append one handoff and return `(entry, index1)`. Does NOT save; the caller
    persists — the same contract `loop.record` keeps for a grade."""
    rep = rep or {}
    entry = {"ts": time.time() if now is None else now,
             "from": predecessor or "", "to": successor or "",
             "measured": rep.get("measured"), "window": rep.get("window"),
             "used_pct": rep.get("used_pct"), "verdict": rep.get("verdict"),
             "forced": bool(forced), "blockers": list(blockers or [])}
    ledger = task.get(HANDOFFS_FIELD)
    if not isinstance(ledger, list):
        ledger = []
        task[HANDOFFS_FIELD] = ledger
    ledger.append(entry)
    return entry, len(ledger)


def ungraded_handoffs(task):
    """The 1-based indices of handoffs no grading has been recorded against.

    THE LINK IS WHAT MAKES "graded like any other child work" CHECKABLE. Without it the
    grade ledger would hold anonymous entries and a task that relayed three times could
    not say which verdict belonged to which handoff — so a second handoff would inherit
    the first one's grade for free. A REJECTION COUNTS AS GRADED: the question here is
    whether anybody has judged it, not whether they liked it."""
    total = len(handoffs(task))
    judged = set()
    for g in _loop.grades(task):
        n = g.get("handoff")
        if isinstance(n, int) and not isinstance(n, bool):
            judged.add(n)
    return [i for i in range(1, total + 1) if i not in judged]


def handoff_evidence_lines(task, index1):
    """One handoff's evidence as display rows, for the grader who has to score it. `[]`
    when the index names nothing.

    A FORCED HANDOFF LEADS. G1 asks whether verification ran, and a handoff that skipped
    the cold-read is the answer to that question — buried three lines down it would be
    read past."""
    ledger = handoffs(task)
    if not (1 <= int(index1 or 0) <= len(ledger)):
        return []
    h = ledger[int(index1) - 1]
    out = []
    if h.get("forced"):
        out.append("  %-13s yes — FORCED past %d unclosed gap(s) in the record"
                   % ("forced", len(h.get("blockers") or [])))
        out.extend("      · %s" % b for b in (h.get("blockers") or []))
    else:
        out.append("  %-13s no — the record read cold and the checkpoint was current"
                   % "forced")
    out.append("  %-13s %s → %s" % ("sessions", h.get("from") or "?",
                                    h.get("to") or "?"))
    out.append("  %-13s ~%s%% of a %s-token window (%s measured), verdict %s"
               % ("occupancy", h.get("used_pct"),
                  "{:,}".format(h.get("window") or 0),
                  "{:,}".format(h.get("measured") or 0), h.get("verdict")))
    return out
