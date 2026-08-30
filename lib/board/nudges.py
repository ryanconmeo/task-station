"""The PROMPT RAIL's two nudges — a heal that came due mid-session, and a checkpoint
going stale — plus the per-(task, session) gate that keeps each to ONE line.

WHY THIS EXISTS. Both signals already existed and both could only speak at the wrong
moment.

  * A HEAL THAT COMES DUE MID-SESSION WAS NEVER MENTIONED. `heal.nag` fires on the
    SessionStart rail, so it reports the state a session INHERITED. But a long working
    session is the thing that makes its own task heal-due: it writes the twelve decisions,
    it leaves the acks undispositioned. By the time that state exists the only rail that
    would have named it has already run, and the next surface that mentions it is the
    `save` gate — which the session reaches only if it decides to checkpoint, which is the
    very decision the reconcile was supposed to inform.

  * NOTHING NUDGED A STALE CHECKPOINT AT ALL. `save.gap_report` reports staleness
    accurately and it reports it INSIDE `/todo save` — i.e. after the user already chose
    to save. A digest that has not been refreshed across a dozen decisions is exactly the
    case where nobody thinks to run it, so the one surface that knew stayed silent until
    it was redundant.

So this module speaks on UserPromptSubmit, where the session actually is, for the ATTACHED
task only.

THE FOUR RULES IT INHERITS FROM `checker` AND `heal`, and they matter more here than
anywhere else in the codebase, because this is the ONE rail that runs on every single
prompt a user types:

  1. FAIL OPEN. Every entry point swallows every exception and returns None. A broken
     nudge must never be worse than the silence it replaces, and must never be the reason
     a prompt failed to submit.
  2. SILENT WHEN HEALTHY. There is no "all clear" line, ever.
  3. ONE LINE, ONCE PER (TASK, SESSION). See the gate below for why the fingerprint is
     the LIMB and not the count.
  4. CHEAP. Blob reads and one small JSON file. `heal.cheap_limbs` rather than
     `heal.due` — no corpus scan, no `stat` per declared path, no git, no network. The
     per-prompt cost of a healthy task is one file read that misses.

AND ONE OF ITS OWN: A NUDGE IS NOT A GATE. Neither line blocks anything, neither runs the
work it names, and neither writes to the task. The only thing either one persists is its
own watermark.

Stdlib only. Imports `config`, `heal`, `save` and `paths` — nothing imports THIS except
the prompt-context rail.
"""
import hashlib
import json
import os
import re
import time

import config as _config
import heal as _heal
import paths
import save as _save

# -- thresholds (module-level so one edit retunes both nudges) --------------------

SAVE_NUDGE_DECISIONS = 6   # decisions + log entries since the checkpoint that make it stale
SAVE_NUDGE_HOURS = 12      # hours since the checkpoint that do, given ANY decision since

GATE_DIR = "nudge"            # <data_dir>/nudge/ — its OWN dir, not heal's or checker's
GATE_SESSIONS_MAX = 32        # watermarks kept per task; see `_record`

# The two gate keys. Separate because they are separate questions, and a session told
# about one has not been told about the other.
HEAL_KEY = "heal_prompt"
SAVE_KEY = "save_nudge"
# The third key, and the only one whose watermark guards a WRITE rather than a line: the
# work-boundary maintenance pass. Same gate, same file, same eviction — because "have I
# already acted on exactly this state" and "have I already said this" are the same question
# asked by two callers, and giving them two mechanisms is how they drift.
BOUNDARY_KEY = "boundary"
# And the one beside it: "the corpus has been scanned at this record shape". Separate from
# BOUNDARY_KEY because they answer different questions — one gates the COST of looking, the
# other gates the ACT of doing something about what was found — and a single watermark would
# make a turn that found nothing owed re-scan the whole corpus at every subsequent turn end.
SHAPE_KEY = "boundary_shape"

# Which limb of the staleness test fired. Fingerprinted INSTEAD of the worded reason, for
# the reason spelled out at `_signature`.
LIMB_VOLUME = "volume"     # enough has landed since the checkpoint
LIMB_AGE = "age"           # long enough since the checkpoint, with work to show for it


def _tunable(reader, default):
    """A config accessor's value, or `default` if reading it raised at all. The sanitising
    (positive-only, unparseable → default) lives in `config`; this is only the fail-open
    boundary, so a corrupt config file cannot break a prompt. `checker._tunable`'s
    contract, and deliberately the same three lines."""
    try:
        return reader()
    except Exception:
        return default


def save_nudge_decisions():
    """`SAVE_NUDGE_DECISIONS`, config-overridable. Read FRESH on every call — `config.get`
    reads a file, which tests repoint per-test and a tuning change lands in without a
    restart."""
    return _tunable(_config.save_nudge_decisions, SAVE_NUDGE_DECISIONS)


def save_nudge_hours():
    """`SAVE_NUDGE_HOURS`, config-overridable."""
    return _tunable(_config.save_nudge_hours, SAVE_NUDGE_HOURS)


# -- the gate file ---------------------------------------------------------------
#
# One JSON file per task under <data_dir>/nudge/, holding a watermark per (key, session).
# Every read and write fails OPEN: an unreadable gate means the nudge may repeat, which is
# the harmless direction — a gate that raised would take the prompt down with it.

def gate_dir():
    """Resolved fresh on every call — `paths.data_dir()` reads the environment and tests
    repoint it per-test."""
    return os.path.join(paths.data_dir(), GATE_DIR)


def _safe_id(task_id):
    """A task id reduced to filename-safe characters."""
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

    A merge rather than a replace, exactly as `checker.write_gate` is and for the same
    reason: two independent nudges keep their watermarks in ONE file, so a whole-file write
    from either would silently re-arm the other on every prompt."""
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
    """Drop a task's gate file, re-arming both nudges for every session. True when one
    went."""
    try:
        os.remove(gate_path(task_id))
        return True
    except Exception:
        return False


def _signature(parts):
    """A stable fingerprint of WHICH limbs fired.

    hashlib, NOT `hash()` — whose string seed is randomised per process, so a stored
    fingerprint would stop matching after a restart and the throttle would be silently
    useless (the bug `heal._signature` and `checker._signature` both call out).

    Sorted, so a reordering is not a change.

    IT HASHES THE LIMB NAMES AND NEVER THE WORDED REASON. This is the whole self-cap, and
    it is `checker.drift_signature`'s lesson restated: every reason either nudge can give
    carries a number that moves on its own — one more decision lands, one more hour
    passes. Hashing the sentence would mint a fresh fingerprint on almost every prompt, so
    the nudge designed to fire once per session would fire on nearly all of them. Hashing
    the limb means it re-arms when the KIND of problem changes, which is the only change a
    reader learns anything from."""
    blob = "\n".join(sorted(str(p) for p in (parts or [])))
    return hashlib.sha1(blob.encode("utf-8", "replace")).hexdigest()


def _already_told(task_id, key, session, sig):
    """True when this (task, session) has already been given exactly `sig` for `key`."""
    seen = read_gate(task_id).get(key)
    if not isinstance(seen, dict):
        return False
    entry = seen.get(str(session or ""))
    return isinstance(entry, dict) and entry.get("sig") == sig


def _record(task_id, key, session, sig, now):
    """Stamp `sig` as told to this (task, session), keeping the newest
    `GATE_SESSIONS_MAX` watermarks.

    BOUNDED because a long-lived task is touched by many sessions — hubs, workers, every
    resume — and an unbounded map would grow a gate file forever to hold watermarks for
    sessions that no longer exist. Oldest-first eviction by the stored stamp; a pruned
    session simply gets told once more, which is the harmless direction.

    THE SESSION BEING RECORDED ALWAYS SURVIVES ITS OWN WRITE. Eviction sorts on `ts`, and
    two watermarks written inside the same clock tick compare EQUAL — so a stable sort
    could otherwise drop the entry this call just made and hand the line straight back to
    the session that was already told. Re-inserting after the trim costs one dict write and
    removes the tie from the design instead of trusting the clock's resolution."""
    sid = str(session or "")
    stored = read_gate(task_id).get(key)
    stored = dict(stored) if isinstance(stored, dict) else {}
    stored.pop(sid, None)                        # this session's slot is reserved below
    if len(stored) > GATE_SESSIONS_MAX - 1:
        ranked = sorted(stored.items(),
                        key=lambda kv: (kv[1] or {}).get("ts") or 0, reverse=True)
        stored = dict(ranked[:GATE_SESSIONS_MAX - 1])
    stored[sid] = {"sig": sig, "ts": now}
    return write_gate(task_id, {key: stored})


# -- (a) a heal that came due mid-session ----------------------------------------

def heal_line(task, session, now=None, persist=True):
    """ONE line naming why a heal is due on the ATTACHED task, or None.

    Reads `heal.cheap_limbs` — the same limbs, the same wording, and the same threshold
    constants as the SessionStart nag, minus the one limb that would need a corpus scan.
    So this can say "12 new decision(s) since the last heal" but never "the scan found 3
    issue(s)": it reports what it measured, and a session that wants the eleven checks
    runs the pass the line points at.

    Throttled per (task, session) on the LIMB fingerprint, so a session that writes
    decisions all afternoon is told once — and told again only if a DIFFERENT limb comes
    due (an ack goes undispositioned, the goal review ages out). `heal_prompt_nag off`
    silences it entirely.

    Fails open: any exception at all yields silence."""
    try:
        if not _config.heal_prompt_nag_enabled():
            return None
        if not task or task.get("status") == "closed":
            return None
        now = time.time() if now is None else now
        limbs = _heal.cheap_limbs(task, now=now)
        if not limbs:
            return None
        sig = _signature([limb for limb, _text in limbs])
        tid = task.get("id")
        if _already_told(tid, HEAL_KEY, session, sig):
            return None
        if persist:
            _record(tid, HEAL_KEY, session, sig, now)
        seq = task.get("seq") or (str(tid or "")[:8])
        return ("[task-station] heal is due on #%s — %s. `/todo heal` is a dry run by "
                "default and changes nothing."
                % (seq, "; ".join(text for _limb, text in limbs)))
    except Exception:
        return None


# -- (b) a checkpoint going stale ------------------------------------------------

def save_staleness(task, now=None):
    """`(limb, reason)` when the last checkpoint reads stale, else `(None, None)`.

    TWO LIMBS, and the second one needs corroboration:

      * VOLUME — `save_nudge_decisions` decisions and log entries have landed since the
        checkpoint. Work has accumulated; the digest does not cover it.
      * AGE — `save_nudge_hours` have passed AND AT LEAST ONE DECISION has landed. The
        conjunction is the point: hours alone measure the clock, not the work, and a task
        left open over a weekend would otherwise be nudged for having been left open.
        One decision is the cheapest possible evidence that the digest is now behind
        something.

    NEVER CHECKPOINTED IS NOT STALE, and that is the first thing this returns on. A task
    minutes old has an empty digest by definition; calling that stale would put a nudge on
    every task the moment it was created, which is the always-on alarm every check in
    `heal` and `checker` has had to learn not to be. `/todo save`'s own gap report already
    covers the never-checkpointed case, worded as the fact it is.

    UNCOUNTABLE IS NEVER STALE either — `heal.goal_review`'s rule and `save`'s own. A stamp
    written by an older version carries no `saved_counts` baseline, so what has landed
    since it cannot be counted; both limbs need that count, so this is silent rather than
    guessing. Every task stamped before the baseline shipped takes that path."""
    since = _save.since_checkpoint(task, now=now)
    if since.get("never"):
        return None, None
    if not since.get("known"):
        return None, None
    decisions = since.get("decisions") or 0
    entries = since.get("history") or 0
    landed = decisions + entries
    threshold = save_nudge_decisions()
    if landed >= threshold:
        return LIMB_VOLUME, ("%d decision(s) and %d log entr%s have landed since the last "
                             "full checkpoint" % (decisions, entries,
                                                  "y" if entries == 1 else "ies"))
    hours = int((since.get("age") or 0) // 3600)
    if hours >= save_nudge_hours() and decisions >= 1:
        return LIMB_AGE, ("the last full checkpoint was %dh ago and %d decision(s) have "
                          "landed since" % (hours, decisions))
    return None, None


def save_line(task, session, now=None, persist=True):
    """ONE line saying the attached task's checkpoint is going stale, or None.

    Same throttle as `heal_line` — once per (task, session), re-armed only when the LIMB
    changes — and the same fail-open contract. `save_nudge off` silences it entirely.

    It names `/todo save` and stops there. It does NOT stamp, dirty, or otherwise touch
    the task: `save`'s stamp is a claim that a checkpoint was CAPTURED, and a nudge
    captures nothing."""
    try:
        if not _config.save_nudge_enabled():
            return None
        if not task or task.get("status") == "closed":
            return None
        now = time.time() if now is None else now
        limb, reason = save_staleness(task, now=now)
        if not limb:
            return None
        sig = _signature([limb])
        tid = task.get("id")
        if _already_told(tid, SAVE_KEY, session, sig):
            return None
        if persist:
            _record(tid, SAVE_KEY, session, sig, now)
        seq = task.get("seq") or (str(tid or "")[:8])
        return ("[task-station] #%s's checkpoint is going stale — %s. `/todo save` "
                "refreshes the digest a fresh session would resume from." % (seq, reason))
    except Exception:
        return None


# -- the gate, for a caller that is not a nudge ----------------------------------
#
# The boundary maintenance pass in the command seam needs exactly this throttle: act once per
# (task, session) per STATE, and re-arm when the state changes. Rather than grow a second gate
# beside this one — which is how two throttles end up disagreeing about whether something has
# happened — these two names expose the one that already works. The module's own rule (a nudge
# never writes to the task) is untouched: the CALLER writes, this still only records its own
# watermark.

def acted(task_id, key, session, sig):
    """True when (task, key, session) has already been acted on at exactly this state."""
    return _already_told(task_id, key, session, sig)


def record_acted(task_id, key, session, sig, now=None):
    """Record that it has. Fails open like every other write here."""
    return _record(task_id, key, session, sig,
                   time.time() if now is None else now)


def signature(parts):
    """The stable fingerprint this module throttles on, exposed for the same caller."""
    return _signature(parts)
