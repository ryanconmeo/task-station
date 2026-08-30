# timing.py
"""THE MAINTENANCE SCHEDULER — when checkpoint, heal and handoff should happen, and
which of them a machine may perform unattended.

WHAT WAS WRONG. Three maintenance actions, wired three different ways, and all three fired
on a COUNT. A count says an action is OWED. It says nothing at all about whether NOW is a
good time, and the two moments this product picked were the two worst available:

  * SESSION START asks for a heal before any work exists to reconcile, and charges the cold
    session the digest read AND the heal in the same breath. On the record that built this
    module, the digest is ~82,000 tokens and one session spent its first ~14 minutes reading
    before it wrote anything.
  * MID-TASK lands in the middle of a thought, so it is dismissed reflexively — which is how
    a nag becomes furniture, and how the next real one gets skipped.

And the third action had no trigger at all. A handoff was a hand judgement every single
time; one relay went at ~66% of a 1M window because a human guessed well.

THE RIGHT MOMENT IS A WORK BOUNDARY: a turn ending with nothing in flight. At a boundary the
summary a checkpoint needs is still in context and costs almost nothing to write; an hour
later the same summary costs a re-read of everything that produced it. Same action, same
record, an order of magnitude apart in price. MOVING WORK FROM THE EXPENSIVE MOMENT TO THE
CHEAP ONE IS THIS MODULE'S ENTIRE JOB.

NOTHING NEW WAS INVENTED TO DETECT THE BOUNDARY. Two mechanisms already shipped and each
knows half of it: the Stop hook knows a turn is ending, and the pickup rail (3.40.0) knows
whether anything is outstanding. `boundary()` composes them, plus the two other ways a unit
of work can be visibly unfinished — an undelivered control-channel order, and a git merge or
rebase left half-done in the session's own working tree.

THREE INPUTS MUST AGREE BEFORE ANYTHING FIRES:

  DUE         what the record says is owed — heal limbs, decisions since the checkpoint,
              budget consumed.
  AFFORDABLE  what the remaining context can pay for, measured against the DETECTED window
              (`sessions.window_resolution` — never a hardcoded constant; see the note
              there for the 5x denominator this cost).
  SAFE        is a unit of work complete — nothing in flight.

TWO CLASSES, AND THE LINE BETWEEN THEM IS NOT A MATTER OF TASTE.

  AUTO    deterministic AND reversible with a named undo. It runs at a safe boundary and
          REPORTS what it did. The mechanical heal pass qualifies today: it backs the task
          blob up first and prints one exact undo per operation.
  PROMPT  needs judgement, or ends the session. Heal's judgement half and the handoff.

NEVER AUTO, whatever the numbers say — and this is enforced in code, not in a comment:

  * ANYTHING OUTWARD. A push, a PR, a message to another party. Reversing an outward action
    means asking somebody else to un-see it.
  * ANYTHING THAT ENDS A SESSION. A handoff is prompted forever. The session being ended is
    the one holding the context, and no threshold is worth spending it unasked.
  * ANY MERGE, in either sense of the word. A `heal` merge writes a false consolidation into
    the record and is NOT reversible the way a split is — a split's parts are each still
    true, while a merge's summary has to be true of all its members at once, and when it is
    not there is no verb that unsays it. On the task that produced this module the single
    merge group has been refused six times by six different readings, every one correct.

Stdlib only, and PURE: every function takes what it needs and returns a value. Nothing here
loads the store, reads a transcript, spawns anything or prints — the command seam does all
of that, which is what lets the whole policy be tested against hand-built dicts. The one
impure helper is `git_operation_in_progress`, which takes a PATH and stats files under it.
"""
import os
import time

# ------------------------------------------------------------------ the classes ----

AUTO = "auto"          # deterministic + reversible with a named undo — runs, then reports
PROMPT = "prompt"      # needs judgement, or ends the session — asks, always
NEVER = "never"        # outward, session-ending, or a merge — no threshold reaches it

# The actions this scheduler knows about, and what each one is. The mapping is DATA rather
# than scattered `if`s so a reader can see the whole policy at once and a test can assert
# over it — including the negative half, which is the half that matters.
ACTION_HEAL_MECHANICAL = "heal-mechanical"
ACTION_HEAL_JUDGEMENT = "heal-judgement"
ACTION_CHECKPOINT_MARK = "checkpoint-mark"
ACTION_CHECKPOINT_FULL = "checkpoint-full"
ACTION_HANDOFF = "handoff"
ACTION_MERGE = "merge"

CLASSES = {
    ACTION_HEAL_MECHANICAL: (AUTO,
        "every op it performs is derived from the entry's own structure, the task blob is "
        "backed up before the first write, and each write prints the exact command that "
        "takes it back"),
    ACTION_CHECKPOINT_MARK: (AUTO,
        "it appends ONE history entry recording the measured occupancy and what has accrued "
        "since the last full checkpoint — facts, not prose. It writes no checkpoint stamp, "
        "because a stamp is a claim that work was captured and only work may make it"),
    ACTION_HEAL_JUDGEMENT: (PROMPT,
        "which half of a compound entry was refuted, and what the goal line should say now, "
        "are readings of content — a machine that guesses writes a confident wrong record"),
    ACTION_CHECKPOINT_FULL: (PROMPT,
        "the digest a successor reads is authored prose. No machine can write it, so the "
        "most the scheduler can do is ASK at the cheap moment instead of the expensive one"),
    ACTION_HANDOFF: (NEVER,
        "it ends the session that is holding the context. Prompted forever, whatever the "
        "budget says"),
    ACTION_MERGE: (NEVER,
        "a merge summary has to be true of all its members at once, and when it is not there "
        "is no verb that unsays it — unlike a split, whose parts each stay true"),
}


def classify(action):
    """`(class, why)` for one action. An action this module has never heard of is NEVER,
    not AUTO: an unclassified write is exactly the write nobody decided to allow."""
    return CLASSES.get(action, (NEVER, "unknown action — nothing unclassified runs unattended"))


def may_run_unattended(action):
    """True only for the AUTO class. The one predicate the performing seam is allowed to
    ask, so `NEVER AUTO` cannot be defeated by a caller reading the table its own way."""
    return classify(action)[0] == AUTO


# ------------------------------------------------------------ SAFE: the boundary ----
#
# WHAT COUNTS AS "IN FLIGHT", and why each one is here rather than a judgement call:
#
#   ORDER    a parent reached this session and the words have not been delivered. Somebody
#            is waiting on an answer; housekeeping is not the reply.
#   PICKUP   a child handed work back and nobody has taken it. Note this uses PENDING, not
#            the narrower `pickups_blocking` the Stop gate blocks on: a pickup past the
#            anti-wedge cap has stopped being allowed to hold the turn, but it has NOT
#            stopped being outstanding work, and the question here is the second one.
#   MERGE    a git merge, rebase, cherry-pick or bisect is half-done in the working tree.
#            The most literal possible reading of "a unit of work is incomplete", and the
#            one moment when rewriting the task record is most likely to be describing a
#            state that is about to change again.
#   EDITS    the session edited files and never tracked a task. The Stop gate is already
#            blocking on this; a maintenance pass on top of a block is noise over an order.

IN_FLIGHT_ORDER = "order"
IN_FLIGHT_PICKUP = "pickup"
IN_FLIGHT_MERGE = "merge"
IN_FLIGHT_EDITS = "untracked-edits"

# The files git leaves behind mid-operation, and what each one means in one word. Checked by
# name because that is git's own contract for them; a directory entry counts too (an
# interrupted rebase is a DIRECTORY, and statting it as a file would report the tree clean).
_GIT_IN_PROGRESS = (
    ("MERGE_HEAD", "a merge"),
    ("rebase-merge", "a rebase"),
    ("rebase-apply", "a rebase"),
    ("CHERRY_PICK_HEAD", "a cherry-pick"),
    ("REVERT_HEAD", "a revert"),
    ("BISECT_LOG", "a bisect"),
)


def git_dir(start):
    """The `.git` DIRECTORY for the tree containing `start`, or None.

    Walks up, and resolves the one-line `gitdir:` pointer a WORKTREE keeps instead of a
    directory — which is not an edge case here at all, since the branch that built this
    module was itself developed in a worktree. Never runs git: this is on the Stop path,
    where an unbounded subprocess per turn end is the cost the whole module exists to avoid."""
    path = os.path.abspath(start or ".")
    while True:
        candidate = os.path.join(path, ".git")
        if os.path.isdir(candidate):
            return candidate
        if os.path.isfile(candidate):
            try:
                with open(candidate) as f:
                    line = f.read().strip()
            except OSError:
                return None
            if line.startswith("gitdir:"):
                target = line.split(":", 1)[1].strip()
                if not os.path.isabs(target):
                    target = os.path.join(path, target)
                return target if os.path.isdir(target) else None
            return None
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent


def git_operation_in_progress(cwd):
    """The half-finished git operation in `cwd`'s tree, in words, or None.

    FAILS TO None, deliberately and in one direction only. An unreadable tree reports
    "nothing in flight", which lets maintenance run — because the alternative is a scheduler
    that goes permanently silent the first time it is pointed somewhere odd, and a silent
    scheduler is indistinguishable from an absent one. The write it enables is itself backed
    up and reversible, so the cost of being wrong here is bounded."""
    try:
        gd = git_dir(cwd)
        if not gd:
            return None
        for name, what in _GIT_IN_PROGRESS:
            if os.path.exists(os.path.join(gd, name)):
                return what
    except Exception:                                   # noqa: BLE001
        return None
    return None


def boundary(orders=0, pickups=0, git_op=None, untracked_edits=False):
    """`{"safe": bool, "in_flight": [(kind, why), …]}` — is this turn end a work boundary?

    EVERY INPUT IS PASSED IN. This function cannot reach a task, a session or a filesystem,
    which is what makes "the boundary is safe" a claim a test can construct rather than a
    claim about the machine it happened to run on.

    SAFE MEANS NOTHING IS IN FLIGHT — it does NOT mean anything is due. Due-ness is a
    separate question with a separate answer, and folding them together is how a scheduler
    ends up firing because the coast was clear rather than because there was work."""
    in_flight = []
    if int(orders or 0) > 0:
        in_flight.append((IN_FLIGHT_ORDER,
                          "%d control-channel order(s) waiting to be delivered — somebody "
                          "is owed an answer before this session tidies up" % int(orders)))
    if int(pickups or 0) > 0:
        in_flight.append((IN_FLIGHT_PICKUP,
                          "%d pickup(s) nobody has taken — a child's finished work is "
                          "waiting on this session" % int(pickups)))
    if git_op:
        in_flight.append((IN_FLIGHT_MERGE,
                          "%s is half-done in the working tree — the record is about to "
                          "change again" % git_op))
    if untracked_edits:
        in_flight.append((IN_FLIGHT_EDITS,
                          "this session edited files without tracking a task, and the Stop "
                          "gate is already blocking on it"))
    return {"safe": not in_flight, "in_flight": in_flight}


# ------------------------------------------------- AFFORDABLE: the handoff floor ----
#
# THE HANDOFF IS THE ONE ACTION WITH A HARD DEADLINE. Checkpoint and heal can slip a session
# — they cost more later, but they still work. A handoff cannot slip, because past a certain
# budget there is not enough room left to WRITE one, and a handoff written at 95% is the one
# that loses the state it exists to carry. The continuation brief this programme runs on is
# 14KB and cost real tokens to author.
#
# SO THE TRIGGER IS A FLOOR, NOT A CEILING: fire while enough budget REMAINS to do it well.
# `succession.band` already computes exactly that from two numbers — a percentage trigger and
# an absolute reserve — and this module does not recompute it. What this adds is the two
# refusals a bare verdict cannot express.

HANDOFF_HOLD = "hold"        # not due, or the moment is wrong — say nothing
HANDOFF_PROMPT = "prompt"    # due, affordable, safe, and the record can carry it
HANDOFF_BLOCKED = "blocked"  # due, but prompting now would hand over a stale record
HANDOFF_MISSED = "missed"    # due, and the reserve is spent — the seam is behind us


def handoff_due(verdict, ready, blockers=None, boundary_safe=True, relay="relay",
                compact="compact"):
    """`(state, why)` — should a handoff be PROMPTED right now?

    Four states, and the two that are not obvious are the point of the function:

      BLOCKED. Due, affordable, at a safe boundary — and the record is stale. IT REFUSES TO
      PROMPT. Handing over a stale record is worse than handing over late: the successor
      inherits a confident summary of a state that has moved, believes it, and works from it.
      Late costs one session boundary. Stale costs the thing the handoff existed to carry. So
      the refusal names the checkpoint instead, which is the move that actually unblocks it.

      MISSED. Due, and the reserve is already spent. There is no useful prompt here — a
      handoff attempted from under the reserve produces a thinner record than the generic
      compaction it was meant to beat — so it says so and points at the compaction.

    `relay`/`compact` are the verdict strings, injected rather than imported, so this stays
    a pure function over values and `succession` remains free of a dependency on its caller."""
    if not boundary_safe:
        return HANDOFF_HOLD, ("the moment is wrong — something is still in flight, and a "
                              "handoff proposed mid-flight is one more thing to dismiss")
    if verdict == compact:
        return HANDOFF_MISSED, ("past the trigger with the handoff reserve already spent — "
                                "the relay seam was missed. Let the generic compaction land "
                                "and take the seam on the far side of it.")
    if verdict != relay:
        return HANDOFF_HOLD, "under the trigger — nothing is due"
    if not ready:
        rows = list(blockers or [])
        return HANDOFF_BLOCKED, (
            "a handoff IS due and affordable, and this will not prompt for one while the "
            "record is stale: %d gap(s) would be handed to the successor as if they were "
            "settled. Checkpoint first (`/todo save`) and the prompt follows on the next "
            "boundary." % len(rows))
    return HANDOFF_PROMPT, ("due, affordable, at a work boundary, and the record reads cold "
                            "— this is the cheapest handoff that will be available")


# --------------------------------------------------------------- the whole verdict ----

def schedule(boundary_state, heal_limbs=None, checkpoint_gap=None, handoff=None,
             auto_ops=0):
    """The whole scheduler answer for one turn end, as one dict — the object the report and
    the Stop rail both render, so the two can never disagree about what was computed.

    `heal_limbs` is `heal.cheap_limbs`'s `[(limb, text), …]`; `checkpoint_gap` is
    `save.since_checkpoint`; `handoff` is `handoff_due`'s `(state, why)`; `auto_ops` is how
    many AUTO-eligible operations the mechanical plan actually holds.

    THE AUTO LIMB REQUIRES BOTH A SAFE BOUNDARY AND SOMETHING TO DO. Firing a pass that
    performs zero operations is the false `last heal just now` this codebase has already paid
    for once: a stamp is a claim about work, and a pass with no work makes it falsely."""
    safe = bool((boundary_state or {}).get("safe"))
    limbs = list(heal_limbs or [])
    gap = dict(checkpoint_gap or {})
    hstate, hwhy = handoff or (HANDOFF_HOLD, "not evaluated")
    accrued = sum(int(gap.get(k) or 0) for k in ("decisions", "steps", "history")) \
        if gap.get("known") else 0
    return {
        "ts": time.time(),
        "safe": safe,
        "in_flight": list((boundary_state or {}).get("in_flight") or []),
        "heal_limbs": limbs,
        "heal_due": bool(limbs),
        "auto_ops": int(auto_ops or 0),
        "auto_fires": bool(safe and int(auto_ops or 0) > 0),
        "checkpoint_accrued": accrued,
        "checkpoint_never": bool(gap.get("never")),
        "handoff": hstate,
        "handoff_why": hwhy,
    }


_HANDOFF_ACTION = {
    HANDOFF_HOLD: "nothing to do",
    HANDOFF_PROMPT: "`task-station relay` reports it; `relay --spawn` performs it",
    HANDOFF_BLOCKED: "`/todo save` first — then the prompt follows",
    HANDOFF_MISSED: "let the compaction land, then take the seam after it",
}


def schedule_lines(sched):
    """The scheduler verdict as display rows. A SAFE BOUNDARY WITH NOTHING DUE IS STILL
    PRINTED, in one line — the same rule `save.gap_lines`, `heal.scan_lines` and
    `succession.report_lines` all follow, because silence reads identically to never having
    looked."""
    sched = sched or {}
    out = []
    if sched.get("safe"):
        out.append("  %-13s yes — nothing in flight, so this is the cheap moment" % "boundary")
    else:
        out.append("  %-13s no — %d thing(s) in flight:"
                   % ("boundary", len(sched.get("in_flight") or [])))
        out.extend("      · %s" % why for _kind, why in (sched.get("in_flight") or []))
    limbs = sched.get("heal_limbs") or []
    out.append("  %-13s %s" % ("heal",
               "; ".join(t for _l, t in limbs) if limbs else "nothing owed"))
    ops = sched.get("auto_ops") or 0
    out.append("  %-13s %d op(s) eligible%s — merges are excluded by class, never by count"
               % ("auto class", ops,
                  ", and this boundary would run them" if sched.get("auto_fires")
                  else ", held" if ops else ""))
    if sched.get("checkpoint_never"):
        out.append("  %-13s never taken" % "checkpoint")
    else:
        out.append("  %-13s %d record change(s) since the last full one"
                   % ("checkpoint", sched.get("checkpoint_accrued") or 0))
    out.append("  %-13s %s — %s" % ("handoff", sched.get("handoff"),
                                    _HANDOFF_ACTION.get(sched.get("handoff"), "")))
    out.append("  %-13s %s" % ("why", sched.get("handoff_why") or ""))
    return out


# ------------------------------------------------- THE RULES, AS PURE PREDICATES ----
#
# WHY THESE THREE LIVE HERE RATHER THAN WHERE THEY ARE USED. An exit condition has to be able
# to ask a rule of `origin/main` — that is the only way a checklist tick can mean "this landed
# where everyone can see it" rather than "this works in the branch that says so". Reading a
# rule out of the merge target means IMPORTING the module that holds it with nothing but the
# standard library available, so a rule that matters has to sit in a leaf.
#
# 3.43.0 learned this the same way and wrote it down: `ownership.may_reassign_out` was moved
# out of the command seam and into a pure predicate over a stored record precisely "because an
# exit condition has to be able to ask it of origin/main". These are the same move for this
# release's three rules. Each one has exactly one definition; `sessions` and `heal` gather the
# inputs and delegate, so the rule and its wiring can never say two different things.

def resolve_window(detected, detected_source, override=None, override_source=None,
                   detected_sources=None):
    """THE WINDOW PRECEDENCE RULE, over values — no session, no config, no transcript.

    An explicit override WINS, because a number the user typed is user intent and a detector
    does not get to overrule it. What the override no longer does is HIDE what it overruled:
    `detected` is carried through whether or not it won, and `diverges` says when the two
    disagree. That boolean is the whole defect made visible — a 200,000 override on a 1M
    session and a 1,000,000 override on a 200k one are the same finding with the sign
    flipped, and the second is the expensive one, because losing a checkpoint costs a record
    while nudging early costs a line of text."""
    detected_sources = tuple(detected_sources or ())
    real = detected_source in detected_sources
    if override:
        diverges = bool(real and detected != override)
        why = "%s is set to %s tokens, so it wins" % (
            override_source or "the stored override", "{:,}".format(override))
        if diverges:
            why += ("; this session DETECTS %s tokens (%s), so the stored value is %s the "
                    "real window and every %%-of-window trigger is measured against the "
                    "wrong denominator"
                    % ("{:,}".format(detected), detected_source,
                       "below" if override < detected else "above"))
        elif real:
            why += " — and it agrees with what this session detects (%s)" % detected_source
        else:
            why += " — nothing could be detected to check it against"
        return {"window": override, "detected": detected,
                "detected_source": detected_source, "override": override,
                "diverges": diverges, "why": why}
    return {"window": detected, "detected": detected, "detected_source": detected_source,
            "override": None, "diverges": False, "why": None}


# What a live ruling does to a finding the scan is reporting now.
DISMISSAL_SILENCED = "silenced"      # same subject, same wording — settled, nothing to say
DISMISSAL_MOVED = "moved"            # same subject, different wording — settled, but re-read
DISMISSAL_NOT_COVERED = "not-covered"  # a different subject — this ruling does not reach it


def dismissal_identity(check, ref):
    """THE KEYING RULE: a dismissal is about (the check that fired, the entry it fired about).

    NOT the rendered sentence. Measured on the real record on 2026-08-29, keying on the
    sentence meant `grew-with-candidates:digest` was ruled the same way FIVE times because a
    character count moved, and one ruling expired inside TEN MINUTES when its finding text
    went from "about decision 436, 503" to "about decision 436" after a split — the ruling did
    not survive its own subject being edited, and editing the subject is what acting on a
    finding looks like.

    hashlib, NOT hash(): hash()'s string seed is randomised per process, so a stored key would
    stop matching after a restart and every dismissal would silently expire."""
    import hashlib
    blob = "\n".join([str(check or ""), str(ref or "")])
    return hashlib.sha1(blob.encode("utf-8", "replace")).hexdigest()


def dismissal_state(entry_check, entry_ref, entry_fingerprint,
                    finding_check, finding_ref, finding_fingerprint):
    """What one live ruling does to one current finding — the three states above.

    MOVED IS THE STATE THAT MAKES THE TRADE HONEST. Keying on the subject means a ruling now
    outlives an edit to the entry it is about. That is the intended trade, and it is the
    smaller risk — a stale ruling on a subject somebody already read costs one missed re-read,
    while the old behaviour cost the credibility of the whole scan. It is not given up
    SILENTLY: when the wording moves under a live ruling the finding is reported as MOVED, so
    a reader knows the sentence is no longer the one that was ruled on."""
    if dismissal_identity(entry_check, entry_ref) != \
            dismissal_identity(finding_check, finding_ref):
        return DISMISSAL_NOT_COVERED
    if entry_fingerprint and finding_fingerprint and entry_fingerprint != finding_fingerprint:
        return DISMISSAL_MOVED
    return DISMISSAL_SILENCED


# The mechanical verbs a machine may perform unattended. A MERGE IS EXCLUDED BY VERB — there
# is no count, no confidence score and no configuration that promotes one into this tuple.
AUTO_VERBS = ("split", "disposition")


def auto_ops(ops):
    """The subset of a mechanical heal plan that may run UNATTENDED at a work boundary.

    Two filters, both hard: the verb is in `AUTO_VERBS`, and the op is not `manual`. An op
    whose parts could not be derived mechanically needs authoring by hand and `apply` would
    skip it anyway; counting it would make an unattended pass claim work and then do none,
    which is the false `last heal just now` this codebase already refuses once."""
    return [o for o in (ops or [])
            if (o or {}).get("verb") in AUTO_VERBS and not (o or {}).get("manual")]


def held_ops(ops):
    """The non-manual ops an unattended pass deliberately LEAVES — `auto_ops`'s complement.
    Reported rather than dropped: an operation that silently vanishes from a pass is
    indistinguishable from one the planner never found."""
    return [o for o in (ops or [])
            if not (o or {}).get("manual") and (o or {}).get("verb") not in AUTO_VERBS]
