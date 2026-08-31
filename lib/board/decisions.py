# decisions.py
"""The decision reconcile + pin primitive — one owner of the `task["decisions"]`
element shape, shared by task-station.py, heal.py, feeds.py and obsidian_sync.py.

WHY THIS EXISTS. `decisions` is an append-only log. Read naively it forces every
consumer to reconstruct the present from the whole history, and the default digest
used to truncate by RECENCY — but load-bearing-ness is not recency. A decision that
refutes an earlier plan is critical; the refuted one is actively misleading; both
rendered identically.

TRUNCATION IS GONE — VALIDITY REPLACED AGE. Truncation and supersession were two
answers to one question ("what should brief a fresh session?"). Truncation answered by
AGE, a proxy, and a wrong one: it hid valid old decisions and showed invalid recent
ones. Supersession answers by VALIDITY, the real criterion. Now that supersede / split
/ merge all exist, the proxy is not merely unnecessary, it is harmful — on a real task
a naming law sat in the hidden tail, so it never briefed anyone, and its own author
violated it hours after reading the digest. So EVERY still-current decision renders:
no age limit, no count limit, no "+N earlier" pointer. What a decision has to earn is
not recency, it is still being true.

So this module gives the log two pieces of state — one saying "this replaces that", one
saying "read this first":

  * REPLACEMENT — a decision may be marked as replaced by later ones in one of THREE
    ways (the reconcile verbs). A replaced decision is WRONG or NO LONGER
    LOAD-BEARING, not merely old, so it is omitted from every present-tense surface
    (digest, board, feeds, obsidian export, checkpoint) and survives only in
    `/todo <n> history`, clearly marked and NAMING what replaced it:

      - `superseded_by: <n>`  — one decision refuted it (`--supersedes`).
      - `split_into: [<n>, …]` — it was a COMPOUND decision, broken into N atomic
        ones. Needed because a decision that mixes still-valid rulings with refuted
        ones cannot be superseded without destroying the good content.
      - `merged_into: <n>`    — it is TRUE but no longer load-bearing, and was
        absorbed with its siblings into one summary decision. Supersession cannot
        express this: nothing refuted it, it just stopped earning digest space.

    NON-DESTRUCTIVE IS THE HARD RULE: no verb ever deletes a decision. History is
    the complete trail — that is its entire job — and every verb is reversible via
    `restore`.

  * PINNING — ORDERING, NOT VISIBILITY. A decision marked `pinned: true` sorts FIRST
    in the digest: the architecture spine, read before the narrative. It does not
    control whether a decision renders, because nothing current is hidden any more —
    a pin is now "read this first", not "don't lose this". Unbounded; the `★` marker
    keeps the spine tellable apart from the rest. See `digest_order`.

  * OWNERSHIP — WHERE A RULING RENDERS, which is a different question from where it
    was typed. A decision may name an `owner` task; the decision itself NEVER MOVES,
    so there is one copy and one store. What moves is which task renders it in full:
    the task that holds it renders a one-line REFERENCE STUB, the owner renders the
    prose. `board.ownership` owns the verbs and the task-level index; this module owns
    the element keys, because every other key on an element is owned here and a second
    owner of the same dict is how two readers start disagreeing about one entry.

  * DECLARATION — WHAT A RULING IS AND WHAT IT IS ABOUT, written by its author and never
    inferred. A decision may declare a `kind` from a CLOSED vocabulary (ruling /
    measurement / incident / release-record / process-note) and a `subject` of qualified
    refs. Both are optional and stay optional: an UNTYPED decision behaves byte-identically
    to how it behaved before these fields existed, permanently and not for a migration
    window, because hundreds already exist and backfill cannot be automated at the ~1/3
    precision that got the keyword placement tier demoted. Untyped is not a kind and never
    defaults to one. See the DECLARATION section below for the whole argument.

ELEMENT SHAPE — dual, and permanently so. An element is EITHER:

    "chose sqlite over flat files"                      (legacy: a plain string)
    {"text": "…", "superseded_by": 5, "pinned": true}   (rich: carries metadata)

Every reader MUST accept both — there are hundreds of live tasks written by older
versions, and a task written by an older version must render identically. That is
what `text()` / `is_replaced()` / `is_pinned()` are for: never touch an element
directly, always go through them.

WRITE-SIDE INVARIANT: `compact()` collapses a rich dict back to a plain string the
moment it carries no metadata. So a decision that is neither superseded nor pinned
is stored EXACTLY as an older version would store it, and an older reader keeps
working unchanged. Only decisions that actually carry supersession/pin state become
dicts — and an older reader degrades to showing `{'text': …}` for those alone,
rather than breaking.

Indices are 1-BASED throughout — they are the numbers `/todo <n> history` prints,
and they are stable because the log is append-only.

Stdlib only, no imports — this module is a leaf.
"""


# -- element accessors: the ONLY sanctioned way to read an element ---------------

def text(entry):
    """The human text of a decision element, for either shape. Never raises — an odd
    element is coerced to str rather than blowing up a render."""
    if isinstance(entry, dict):
        return str(entry.get("text") or "")
    return "" if entry is None else str(entry)


def superseded_by(entry):
    """The 1-based index of the decision that SUPERSEDED `entry`, or None. Narrow by
    design: it answers only about the supersede verb, never about split/merge — use
    `replacement()` for "was this replaced at all". A legacy string is always
    current."""
    if not isinstance(entry, dict):
        return None
    raw = entry.get("superseded_by")
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def is_superseded(entry):
    """True iff this decision was replaced by the SUPERSEDE verb specifically."""
    return superseded_by(entry) is not None


# The three reconcile verbs, as stored. Each maps a storage key to a kind label and
# says whether the key holds one index or a list of them. Order is the precedence a
# (malformed) element carrying several marks resolves in — supersession first,
# because it is the only one that means "this was WRONG".
REPLACED_SUPERSEDED = "superseded"
REPLACED_SPLIT = "split"
REPLACED_MERGED = "merged"

_REPLACEMENTS = (("superseded_by", REPLACED_SUPERSEDED, False),
                 ("split_into", REPLACED_SPLIT, True),
                 ("merged_into", REPLACED_MERGED, False))

# -- the FOURTH replacement, and why it is not a fourth `_REPLACEMENTS` row ----------
#
# Once ownership can cross tasks, a CHILD's ruling can refute a PARENT's. The three keys
# above all hold a 1-BASED INDEX INTO THIS TASK'S OWN LOG, and that number is meaningless
# on another task's log — decision numbers are per-task, so a bare `4` names a different
# ruling on every task in the store. A cross-task supersession therefore has to carry the
# TASK as well as the number, which is a different value shape (a dict, not an int) and
# so a different key: `_index_list` would silently drop a dict, and a garbled replacement
# mark that "fails open" would leave a refuted ruling briefing every session as current.
#
# BOTH DIRECTIONS, ALWAYS. The source entry gets `superseded_across` (naming what refuted
# it) and the refuting entry gets `supersedes_across` (naming what it refuted). One
# direction alone makes the contradiction invisible from the other side, which is exactly
# the defect the prose-supersession check exists to catch — and here the two sides are on
# two different tasks, so no reader can stumble on the other half by accident.
SUPERSEDED_ACROSS = "superseded_across"     # on the SOURCE: the ref that refuted it
SUPERSEDES_ACROSS = "supersedes_across"     # on the REFUTER: the refs it refuted


def _clean_ref(raw):
    """Coerce a stored cross-task reference to `{"task", "seq", "n"}`, or None.

    Requires BOTH a task id and a positive index: a ref missing either cannot be
    resolved to one ruling, and a half-ref is exactly the thing that must not be
    treated as a live pointer. Defensive, never raises — a store written by a newer
    version degrades to "not a ref" instead of breaking a render."""
    if not isinstance(raw, dict):
        return None
    task = str(raw.get("task") or "").strip()
    try:
        n = int(raw.get("n"))
    except (TypeError, ValueError):
        return None
    if not task or n < 1:
        return None
    out = {"task": task, "n": n}
    try:
        if raw.get("seq") is not None:
            out["seq"] = int(raw["seq"])
    except (TypeError, ValueError):
        pass
    return out


def ref_label(ref):
    """`#532 decision 14` — how a cross-task reference reads in prose. Falls back to the
    id prefix when the ref carries no seq, so a label is always addressable."""
    ref = _clean_ref(ref)
    if ref is None:
        return ""
    who = ("#%d" % ref["seq"]) if ref.get("seq") is not None else ref["task"][:8]
    return "%s decision %d" % (who, ref["n"])


def ref_handle(ref):
    """`532:14` — the COMMAND-LINE form of a cross-task reference, and the one a
    `--supersedes` / `--reassign` argument takes. Bare-number refs stay legal for the
    same-task case; the colon form is what makes a number unambiguous once ownership can
    cross tasks."""
    ref = _clean_ref(ref)
    if ref is None:
        return ""
    who = str(ref["seq"]) if ref.get("seq") is not None else ref["task"][:8]
    return "%s:%d" % (who, ref["n"])


def superseded_across(entry):
    """The cross-task reference that refuted this decision, or None."""
    if not isinstance(entry, dict):
        return None
    return _clean_ref(entry.get(SUPERSEDED_ACROSS))


def supersedes_across(entry):
    """Every cross-task ruling this decision refutes, oldest first. The BACK pointer —
    what makes the contradiction visible from the refuting side too."""
    if not isinstance(entry, dict):
        return []
    raw = entry.get(SUPERSEDES_ACROSS)
    items = raw if isinstance(raw, (list, tuple)) else [raw]
    out = []
    for r in items:
        ref = _clean_ref(r)
        if ref is not None and ref not in out:
            out.append(ref)
    return out


def _index_list(raw, many):
    """Coerce a stored replacement target to a list of ints, dropping anything
    uncoercible. Returns [] when nothing usable is there, which reads as "not
    replaced" — a garbled mark must never hide a decision, it must fail open."""
    items = (raw if isinstance(raw, (list, tuple)) else [raw]) if many else [raw]
    out = []
    for v in items:
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if n > 0:
            out.append(n)
    return out


def replacement(entry):
    """How this decision was replaced, as `(kind, [target index1, …])`, or None when
    it is still current.

    `kind` is one of REPLACED_SUPERSEDED / REPLACED_SPLIT / REPLACED_MERGED, and the
    targets are the 1-based decisions that replaced it. This is the ONE accessor
    every present-tense surface goes through (via `live`), so adding a verb never
    needs a second audit of the readers. A legacy plain string is always current.

    A CROSS-TASK supersession returns targets that are `#532 decision 14` STRINGS
    rather than ints, because the number alone does not name the ruling once ownership
    can cross tasks. Every consumer either joins them into a label or tests them for
    membership in a set of local indices — a string never collides with an int there,
    so a cross-task target can only ever read as "not one of mine", which is true."""
    if not isinstance(entry, dict):
        return None
    for key, kind, many in _REPLACEMENTS:
        if key not in entry:
            continue
        targets = _index_list(entry.get(key), many)
        if targets:
            return kind, targets
    across = superseded_across(entry)
    if across is not None:
        return REPLACED_SUPERSEDED, [ref_label(across)]
    return None


def is_replaced(entry):
    """True iff this decision was replaced by ANY reconcile verb — superseded, split
    or merged. What every present-tense surface omits."""
    return replacement(entry) is not None


# The `— …` tail every history render appends to a replaced decision. Owned here so
# the history view, the heal plan and the tests all agree on ONE wording, and so the
# supersede phrasing stays byte-identical to what 2.9.0 shipped.
_LABELS = {
    REPLACED_SUPERSEDED: ("SUPERSEDED by decision", "SUPERSEDED by decisions"),
    REPLACED_SPLIT: ("SPLIT into decision", "SPLIT into decisions"),
    REPLACED_MERGED: ("MERGED into decision", "MERGED into decisions"),
}


def replacement_label(entry):
    """`SUPERSEDED by decision 5` / `SPLIT into decisions 6, 7, 8` /
    `MERGED into decision 9`, or None when the decision is still current. The verb is
    named in full so `history` says WHAT replaced the entry, not merely that
    something did."""
    rep = replacement(entry)
    if rep is None:
        return None
    across = superseded_across(entry)
    if across is not None and rep[1] == [ref_label(across)]:
        # `SUPERSEDED by #532 decision 14` — the ref already carries the word
        # "decision", so the local wording would read "by decision #532 decision 14".
        return "SUPERSEDED by %s" % ref_label(across)
    kind, targets = rep
    one, many = _LABELS.get(kind, ("REPLACED by decision", "REPLACED by decisions"))
    return "%s %s" % ((many if len(targets) > 1 else one),
                      ", ".join(str(n) for n in targets))


def is_pinned(entry):
    """True iff this decision is pinned to the default digest."""
    return bool(isinstance(entry, dict) and entry.get("pinned"))


# -- shape conversion ------------------------------------------------------------

def as_rich(entry):
    """A mutable dict view of an element (legacy string → `{"text": …}`). Returns a
    COPY, so the caller mutates it and writes it back via `compact()`."""
    if isinstance(entry, dict):
        return dict(entry)
    return {"text": text(entry)}


def compact(entry):
    """The storage form of an element: a plain string when it carries NO metadata,
    otherwise the dict. This is the back-compat guarantee — an ordinary decision is
    stored byte-identically to how every older version stored it, so older readers
    and existing golden fixtures are untouched. Unknown keys written by a NEWER
    version are preserved, never dropped."""
    if not isinstance(entry, dict):
        return entry
    extra = dict((k, v) for k, v in entry.items()
                 if k != "text" and v not in (None, False, 0, "", []))
    if not extra:
        return text(entry)
    out = {"text": text(entry)}
    out.update(extra)
    return out


# -- selection -------------------------------------------------------------------

def live(entries):
    """Every still-current decision as `(index1, entry)` pairs, oldest first.
    REPLACED decisions — superseded, split or merged — are dropped entirely. This is
    the ONE selection every present-tense surface goes through (digest, board,
    feeds, obsidian export, checkpoint), which is why a new reconcile verb needs no
    change in any of them."""
    return [(i, e) for i, e in enumerate(entries or [], 1) if not is_replaced(e)]


def replaced(entries):
    """The inverse of `live`: every REPLACED decision as `(index1, entry)` pairs,
    oldest first. Nothing renders this on the resume path — it exists so `history`
    and the heal report can prove the trail is still complete."""
    return [(i, e) for i, e in enumerate(entries or [], 1) if is_replaced(e)]


def live_texts(entries):
    """Just the text of every still-current decision, oldest first. The plain-string
    projection the feed wire form and the board view-model carry."""
    return [text(e) for _i, e in live(entries)]


def pinned_count(entries):
    """How many still-current decisions are pinned (a replaced one can't be)."""
    return sum(1 for _i, e in live(entries) if is_pinned(e))


def total_chars(entries):
    """Total characters across every STILL-CURRENT decision — the number that says
    whether a task is under-reconciled. Replaced decisions are excluded because they
    no longer cost the digest anything; they cost only `history`, which is on demand."""
    return sum(len(text(e)) for _i, e in live(entries))


def digest_order(entries):
    """EVERY still-current decision, as `(index1, entry)` pairs, in READING ORDER.

    NOTHING CURRENT IS HIDDEN. There is no age limit and no count limit — this returns
    the whole of `live()`, just reordered. It replaced `digest_selection(entries,
    limit)`, which kept only the last `limit` unpinned entries and reported an
    `omitted` count for a "… +N earlier" pointer. That truncation selected by AGE,
    which is a proxy for load-bearing-ness and a wrong one: it hid valid old decisions
    while showing invalid recent ones. Validity is the real criterion and the three
    reconcile verbs already express it, so age has nothing left to decide.

    ORDER is the only thing left to choose, and pinning is what chooses it:

      * PINNED first, oldest-first among themselves — the architecture spine, the
        rules that constrain everything below them, read before the narrative.
      * THEN every unpinned current decision, oldest-first — the narrative in the
        order it happened.

    Pinned entries carry `★` at every render site, so the spine stays tellable apart
    from the narrative even though they run together in one list.

    REPLACED decisions (superseded, split, merged) are absent — the ONLY thing that
    keeps a decision out of this surface is no longer being true. They survive in
    `/todo <n> history`, marked with what replaced them."""
    current = live(entries)
    return ([p for p in current if is_pinned(p[1])]
            + [p for p in current if not is_pinned(p[1])])


# -- write-time length advisory --------------------------------------------------

# THE ONE SIZE NUMBER IN THIS PROJECT. `length_warning` nudges past it at write time, and
# `heal` DERIVES both of its thresholds from it (`heal.OVERSIZE_PROPOSAL_CHARS` = 2×,
# `heal.OVERSIZE_CHARS` = 6×) rather than carrying an opinion of its own. It used to carry
# one — a flat 4,000, which was 2.4× this and referenced nothing — so the write path and the
# reconcile path disagreed about what "too long" meant and neither could be retuned without
# the other silently drifting. It lives HERE because heal imports decisions and never the
# reverse; edit it in this one place.
LONG_DECISION_CHARS = 600   # past this, `length_warning` nudges — see below


def length_warning(entry, index1=None, limit=LONG_DECISION_CHARS):
    """One ADVISORY line when a decision runs past `limit` chars, else None.

    THIS NEVER REFUSES, and that is the whole design. A gate here would push the
    author to drop a fact, or to fake two decisions out of one to get under the
    number — and this project has the evidence: the pin cap DID refuse, and the
    refusal produced a workaround rather than a fix. So the write always succeeds,
    in full, byte-identical, and the author gets a suggestion instead of an error.

    Distinct from heal's two thresholds by job, not by disagreement — and they are now
    DERIVED from this constant rather than picked separately (2× is a proposal, 6× a
    finding). This fires at WRITE time, when the author still has the context to split the
    entry cheaply; those judge an entry already in the log. A nudge can afford to be far
    more sensitive than a finding, which is exactly what a multiplier expresses and what two
    unrelated numbers could not."""
    n = len(text(entry))
    if n <= limit:
        return None
    where = ("decision %d" % index1) if index1 else "that decision"
    return ("%s is %d chars (over the %d-char advisory) — stored IN FULL, nothing was "
            "dropped. Consider `heal --split` to cut it into atomic decisions: one long "
            "entry is hard to read and cannot be superseded a piece at a time."
            % (where, n, limit))


# -- mutation (each returns `(ok, error_message)`) --------------------------------

def _check_index(entries, index1, flag):
    """Validate a 1-based decision index against the log. Returns (int, None) on a
    hit, (None, error-line) otherwise — a bad index is a CLEAR ERROR, never a silent
    no-op, because silently dropping a supersession leaves the wrong decision live."""
    try:
        i = int(index1)
    except (TypeError, ValueError):
        return None, ("%s expects a 1-based decision number (as shown by "
                      "`/todo <n> history`), got %r" % (flag, index1))
    total = len(entries or [])
    if i < 1 or i > total:
        return None, ("%s %d — no such decision; this task has %d "
                      "(see `/todo <n> history` for the numbered list)" % (flag, i, total))
    return i, None


# Per-kind refusal wording for an already-replaced decision. Kept kind-specific (not
# one generic "already replaced") because the error's job is to say where the content
# WENT, so the caller can act on the right entry: the replacement for a supersede, the
# parts for a split, the absorber for a merge. The supersede line is byte-identical to
# what 2.9.0 shipped — it is a documented, tested string.
_ALREADY = {
    REPLACED_SUPERSEDED: ("already superseded (by decision %s)",
                          "supersede the current one instead"),
    REPLACED_SPLIT: ("already split (into decision(s) %s)",
                     "act on the parts instead"),
    REPLACED_MERGED: ("already merged (into decision %s)",
                      "act on the absorbing decision instead"),
}


def _already_phrase(entry):
    """`already superseded (by decision 2)` — the lowercase mid-sentence phrase naming
    how `entry` was replaced, or None when it is current. Shared by the replace guard
    and the pin guard so both refusals name the verb the same way."""
    rep = replacement(entry)
    if rep is None:
        return None
    across = superseded_across(entry)
    if across is not None and rep[1] == [ref_label(across)]:
        return "already superseded (by %s)" % ref_label(across)
    kind, targets = rep
    how, _advice = _ALREADY.get(kind, ("already replaced (by decision %s)", ""))
    return how % ", ".join(str(n) for n in targets)


def _check_unreplaced(entries, i, flag):
    """Refuse to replace an ALREADY-replaced decision, naming how it was replaced and
    where its content went. Shared by all three verbs so double-marking is impossible:
    a decision carries exactly ONE replacement, which is what keeps each verb cleanly
    reversible by `restore`. Returns None when the decision is current."""
    entry = entries[i - 1]
    how = _already_phrase(entry)
    if how is None:
        return None
    kind, _targets = replacement(entry)
    _how, advice = _ALREADY.get(kind, ("", "act on the current one instead"))
    return "%s %d — that decision is %s; %s" % (flag, i, how, advice)


def _check_targets(entries, i, targets, flag, verb):
    """Validate the decisions a verb points AT: at least one, all in range, none of
    them the entry being marked. Returns (ints, None) or (None, error-line)."""
    out = []
    for raw in (targets or []):
        n, err = _check_index(entries, raw, flag)
        if err:
            return None, err
        out.append(n)
    if not out:
        return None, ("%s %d — name at least one decision it %s (the replacement is "
                      "what makes the original droppable)" % (flag, i, verb))
    if i in out:
        return None, "%s %d — a decision cannot %s itself" % (flag, i, verb)
    return out, None


def mark_superseded(entries, index1, by_index1, flag="--supersedes"):
    """Mark decision `index1` as replaced by decision `by_index1`. Superseding a
    PINNED decision clears its pin — a wrong decision must not keep a guaranteed
    digest slot. Errors (never silently no-ops) on a missing index, a self-reference,
    or an already-replaced target."""
    i, err = _check_index(entries, index1, flag)
    if err:
        return False, err
    err = _check_unreplaced(entries, i, flag)
    if err:
        return False, err
    if i == by_index1:
        return False, "%s %d — a decision cannot supersede itself" % (flag, i)
    rich = as_rich(entries[i - 1])
    rich["superseded_by"] = int(by_index1)
    rich.pop("pinned", None)                 # a superseded decision loses its pin
    entries[i - 1] = compact(rich)
    return True, None


def mark_split(entries, index1, into_index1s, flag="--split"):
    """Mark decision `index1` as SPLIT INTO the decisions `into_index1s`.

    The split verb exists because supersession is too blunt for a COMPOUND decision:
    when one entry mixes still-valid rulings with refuted ones, superseding it
    destroys the good content and keeping it briefs the bad. Splitting replaces it
    with N atomic decisions, each of which can then be superseded or pinned on its
    own merits — and a decision too long to read is a split candidate regardless of
    whether any of it is wrong.

    NON-DESTRUCTIVE: the original keeps its full text and stays in `history`, marked
    `SPLIT into decisions …` so the trail names exactly what it became. Reversible via
    `restore`. The pin is cleared — the parts carry the load now, and a decision that
    no longer renders must not hold a guaranteed digest slot."""
    i, err = _check_index(entries, index1, flag)
    if err:
        return False, err
    err = _check_unreplaced(entries, i, flag)
    if err:
        return False, err
    parts, err = _check_targets(entries, i, into_index1s, flag, "split into")
    if err:
        return False, err
    rich = as_rich(entries[i - 1])
    rich["split_into"] = parts
    rich.pop("pinned", None)
    entries[i - 1] = compact(rich)
    return True, None


def mark_merged(entries, index1, into_index1, flag="--merge"):
    """Mark decision `index1` as MERGED INTO decision `into_index1`.

    The merge verb exists for decisions that are TRUE but no longer load-bearing —
    four separate `X.Y.Z SHIPPED` release records, seven steps of one scrub, a chain
    of process-error corrections. Supersession cannot express this: nothing refuted
    them, they simply stopped earning digest space, and marking them "wrong" would be
    a lie in the record. Merging collapses the cluster into one summary decision.

    NON-DESTRUCTIVE: each original keeps its full text and stays in `history`, marked
    `MERGED into decision <n>`. Reversible via `restore`. Call once per original —
    they all name the same absorbing decision."""
    i, err = _check_index(entries, index1, flag)
    if err:
        return False, err
    err = _check_unreplaced(entries, i, flag)
    if err:
        return False, err
    into, err = _check_targets(entries, i, [into_index1], flag, "merge into")
    if err:
        return False, err
    rich = as_rich(entries[i - 1])
    rich["merged_into"] = into[0]
    rich.pop("pinned", None)
    entries[i - 1] = compact(rich)
    return True, None


def restore(entries, index1, flag="--restore-decision"):
    """Clear whatever replacement mark decision `index1` carries, returning it to the
    present tense. The ONE inverse of all three verbs — which is what makes each of
    them reversible, and why a heal can be undone without reaching for the backup.

    Errors (never a silent no-op) on a missing index or on a decision that was not
    replaced at all: silently "restoring" a current decision would report a reconcile
    that never happened. Keeps this module's `(ok, error)` contract — a caller that
    wants to name what it undid reads `replacement_label()` BEFORE calling."""
    i, err = _check_index(entries, index1, flag)
    if err:
        return False, err
    if replacement_label(entries[i - 1]) is None:
        return False, ("%s %d — that decision is not replaced; there is nothing to "
                       "restore" % (flag, i))
    rich = as_rich(entries[i - 1])
    for key, _kind, _many in _REPLACEMENTS:
        rich.pop(key, None)
    # The cross-task mark is a replacement like any other, so the ONE inverse clears it
    # too. The `supersedes_across` back-pointer on the OTHER task's refuting entry is NOT
    # reachable from here — `restore` takes one log — so the caller that undoes a
    # cross-task supersession has to clear both sides; `ownership.restore_across` is
    # that caller, and it is the only sanctioned way to undo one.
    rich.pop(SUPERSEDED_ACROSS, None)
    entries[i - 1] = compact(rich)
    return True, None


def set_pin(entries, index1, pinned, flag=None):
    """Pin or unpin decision `index1`. A pin now controls READING ORDER, not
    visibility — every current decision renders either way (see `digest_order`), and a
    pin sorts it into the spine block that leads the digest. There is NO limit on how
    many a task may pin; the cap only ever guarded a recency budget that no longer
    exists. Pinning a REPLACED decision — superseded, split or merged — is still an
    error: it does not render at all, so there is no position to sort it into.
    Re-pinning an already-pinned decision, and unpinning, are both no-op-safe."""
    if flag is None:
        flag = "--pin-decision" if pinned else "--unpin-decision"
    i, err = _check_index(entries, index1, flag)
    if err:
        return False, err
    entry = entries[i - 1]
    if pinned:
        how = _already_phrase(entry)
        if how is not None:
            return False, ("%s %d — that decision is %s and cannot be pinned"
                           % (flag, i, how))
    rich = as_rich(entry)
    if pinned:
        rich["pinned"] = True
    else:
        rich.pop("pinned", None)
    entries[i - 1] = compact(rich)
    return True, None


# -- OWNERSHIP: which task RENDERS a ruling, as data rather than as convention --------
#
# THE PROBLEM, MEASURED. A ruling lives on the task where a session happened to type it,
# not on the task whose goal it constrains. On one real programme that put 31,072 chars
# of one child's subject, 12,612 of another's and 3,737 of a third's on the PARENT — and
# a reconcile pass that split eight oversized entries and cut the longest from 8,095 to
# 3,581 chars barely moved the total, because the content is load-bearing. It is not
# redundant, it is in the WRONG PLACE, and no amount of consolidation fixes a place.
#
# SO OWNERSHIP MOVES AND THE DECISION DOES NOT. One copy, one store, no duplication: the
# entry stays exactly where it was written, in full, at its original index, and gains an
# `owner` naming the task that RENDERS it. The holder renders a one-line reference stub
# instead of the prose. That is the same shape the knowledge plane already ships — upload,
# reconcile, approve, and the private node collapses to reference — applied to the task
# plane.
#
# WHY THE STUB IS NOT OPTIONAL, and is enforced at this level: a reassign that left no
# stub would be a DELETE with extra steps. The holder must never lose the knowledge that
# a ruling exists and where it went; only the prose leaves its render.
#
# WHY `owner` IS AN ID AND `owner_seq` IS DECORATION. A seq is a display number that a
# store can renumber and that never crosses machines; the uuid is the identity. Both are
# stored so a render can print `#532` without a load, but every comparison in this
# codebase resolves on the id.

OWNER_FIELD = "owner"           # task id that RENDERS this ruling in full
OWNER_SEQ_FIELD = "owner_seq"   # that task's display seq, for a load-free stub render
STUB_FIELD = "stub"             # the one-line reference the HOLDER renders instead

# How long a derived stub runs before it is cut. One line in a digest, and long enough to
# say which ruling it is — a stub that does not identify the ruling is not a reference.
STUB_CHARS = 120


def owner(entry):
    """The task id that OWNS (renders in full) this decision, or None when the task
    holding it owns it — which is the case for every decision ever written before this
    field existed, and for the overwhelming majority after."""
    if not isinstance(entry, dict):
        return None
    val = str(entry.get(OWNER_FIELD) or "").strip()
    return val or None


def owner_seq(entry):
    """The owner's display seq, or None. Decoration for a load-free render; `owner` is
    what any comparison uses."""
    if not isinstance(entry, dict):
        return None
    try:
        return int(entry.get(OWNER_SEQ_FIELD))
    except (TypeError, ValueError):
        return None


def owner_label(entry):
    """`#532` — how the owner reads in a stub, falling back to an id prefix so a stub
    written before seqs were stamped is still addressable."""
    seq = owner_seq(entry)
    if seq is not None:
        return "#%d" % seq
    own = owner(entry)
    return own[:8] if own else ""


def derive_stub(body, limit=STUB_CHARS):
    """A one-line reference for `body` — its first sentence or line, cut to `limit`.

    Deliberately dumb and deterministic: the first sentence of a ruling is where its
    subject is stated, and a derived stub that surprises the author is worse than one
    that is merely terse. `--stub` overrides it whenever the first sentence is not the
    subject. Returns "" for empty text, which is what makes the no-stub refusal
    reachable rather than theoretical."""
    text_ = " ".join(str(body or "").split())
    if not text_:
        return ""
    for cut in (". ", " — ", "; "):
        head = text_.split(cut, 1)[0]
        if 0 < len(head) <= limit:
            return head
    if len(text_) <= limit:
        return text_
    clipped = text_[:limit].rsplit(" ", 1)[0] or text_[:limit]
    return clipped + "…"


def stub(entry):
    """The reference line the HOLDER renders for a decision it no longer owns: the
    stored `stub`, else one derived from the text. Never empty for a non-empty
    decision — the stub is mandatory, so this cannot be the thing that loses it."""
    stored = ""
    if isinstance(entry, dict):
        stored = " ".join(str(entry.get(STUB_FIELD) or "").split())
    return stored or derive_stub(text(entry))


def is_owned(entry):
    """True iff this decision names an owner at all."""
    return owner(entry) is not None


def renders_full(entry, task_id):
    """Does the task holding this entry render it IN FULL? True when nothing claims it,
    or when the claimant IS this task. The ONE predicate every render goes through, so
    a surface can never disagree with another about who prints the prose.

    A task_id of None means "no identity to compare with" — a foreign feed, a fixture, a
    test blob — and that renders IN FULL. Failing open is the correct direction here: an
    unrenderable ruling is invisible, and invisible is the failure this whole mechanism
    exists to prevent."""
    own = owner(entry)
    if own is None or not task_id:
        return True
    return own == task_id


def set_owner(entries, index1, task_id, seq=None, stub_text=None, flag="--reassign"):
    """Stamp `index1` as OWNED BY `task_id`, leaving the ruling exactly where it is.
    `(ok, error)`.

    FOUR REFUSALS, each because something breaks without it:

      * a REPLACED decision — superseded, split or merged. It does not render anywhere,
        so there is no render to move, and stamping an owner on a dead ruling would put
        it back on a surface as somebody else's live constraint.
      * a PINNED decision. A pin briefs every session of the task that set it; a ruling
        that binds the programme belongs to the programme. Unpin it first if it really
        is one child's.
      * an EMPTY STUB — a decision with no text to reference. Without a stub the holder
        loses the knowledge that the ruling exists, which makes the reassign a delete.
      * an owner it ALREADY has, or one already held by a THIRD task. Re-pointing an
        owned ruling in place would leave the previous owner's index naming a decision
        it no longer owns, and the honest sequence (`--unassign`, then reassign) is one
        extra command rather than a silently half-updated pair of tasks.

    Does NOT touch the owner task's index — `ownership.reassign` does both halves, and
    is the only sanctioned caller."""
    i, err = _check_index(entries, index1, flag)
    if err:
        return False, err
    entry = entries[i - 1]
    how = _already_phrase(entry)
    if how is not None:
        return False, ("%s %d — that decision is %s, so it renders nowhere and there is "
                       "no render to move" % (flag, i, how))
    if is_pinned(entry):
        return False, ("%s %d — that decision is PINNED. A pin briefs every session of "
                       "this task, so a ruling that binds the programme belongs to the "
                       "programme; `update --unpin-decision %d` first if it really is one "
                       "child's." % (flag, i, i))
    tid = str(task_id or "").strip()
    if not tid:
        return False, "%s %d — no owner task given" % (flag, i)
    existing = owner(entry)
    if existing:
        return False, ("%s %d — that decision is already owned by %s; `heal --unassign %d` "
                       "brings it back here first, so the previous owner's index is never "
                       "left naming a ruling it no longer owns"
                       % (flag, i, owner_label(entry) or existing[:8], i))
    line = " ".join(str(stub_text or "").split()) or derive_stub(text(entry))
    if not line:
        return False, ("%s %d — that decision has no text, so no reference stub can be "
                       "written for it. A reassign that leaves no stub is a delete with "
                       "extra steps, so this is refused rather than performed."
                       % (flag, i))
    rich = as_rich(entry)
    rich[OWNER_FIELD] = tid
    if seq is not None:
        try:
            rich[OWNER_SEQ_FIELD] = int(seq)
        except (TypeError, ValueError):
            pass
    rich[STUB_FIELD] = line
    entries[i - 1] = compact(rich)
    return True, None


def clear_owner(entries, index1, flag="--unassign"):
    """Return `index1` to the task that holds it — the ONE inverse of `set_owner`, and
    what makes a reassign reversible by a single command. `(ok, error)`.

    Errors rather than silently succeeding on a decision nobody owns: "unassigned" after
    a typo reads as success, and the caller then believes a render moved when it did not.
    Drops the stub with the owner — the holder renders the prose again, so a stale
    reference line would be a second, quietly diverging copy of the first sentence."""
    i, err = _check_index(entries, index1, flag)
    if err:
        return False, err
    if not is_owned(entries[i - 1]):
        return False, ("%s %d — this task already owns that decision; there is nothing "
                       "to bring back" % (flag, i))
    rich = as_rich(entries[i - 1])
    rich.pop(OWNER_FIELD, None)
    rich.pop(OWNER_SEQ_FIELD, None)
    rich.pop(STUB_FIELD, None)
    entries[i - 1] = compact(rich)
    return True, None


# -- DECLARATION: what a ruling IS and what it is ABOUT, written by its author --------
#
# THE PROBLEM, MEASURED. An element carried `{text, superseded_by, pinned, owner}` and
# nothing else, so every consumer re-derived structure from ENGLISH. `heal.py` is 4,940
# lines against this module's 905 — the module that GUESSES at the record is 5.5x the
# module that IS the record — and it grows one vocabulary per prose shape rather than one
# per feature. The ceiling on guessing is already measured, and it was earned by trying:
# a pass over 82 decisions classified by subject keyword spot-checked at roughly ONE IN
# THREE right, which is why the keyword placement tier was demoted to an informational
# re-read list. A ruling MENTIONS a subject far more often than it is ABOUT one, and the
# mentions outnumber the ownership about two to one.
#
# SO THE AUTHOR DECLARES IT AND NOTHING EVER INFERS IT. Information that exists at WRITE
# time stops being expensively and unreliably reconstructed at READ time. Two fields, both
# optional, both author-written: `kind` (what class of record this is) and `subject` (the
# refs it is about).
#
# UNTYPED IS NOT A KIND, AND NOT FOR A MIGRATION WINDOW — PERMANENTLY. Hundreds of
# decisions already exist carrying neither field, and backfill cannot be automated at 1/3
# precision, so an untyped decision is a permanent citizen of the record rather than a
# transitional one. `kind()` returns None for one, `subject()` returns [], `compact()`
# still stores it as a plain STRING, and EVERY consumer must behave byte-identically to
# how it behaved before these fields existed. Untyped is not a default kind and never
# "probably a process-note". ONE rule for every consumer rather than per-consumer
# defaults, because per-consumer defaults are exactly how two surfaces begin disagreeing
# about one entry.
#
# THE KIND VOCABULARY IS CLOSED AND VALIDATED AT THE SETTER, never at the reader. A
# free-text kind would be a second prose field, which is the defect this closes rather
# than a smaller instance of it. Adding a kind is a deliberate one-line change here.
#
# A SUBJECT IS A QUALIFIED REF AND NEVER A BARE INTEGER, and this one is not taste.
# `heal.subject_signals` already labels every work item `PR/story <n>` from one bare
# number, so PR 27 and story 27 emit the IDENTICAL signal and collide TODAY — two
# decisions about unrelated things can be grouped as sharing a subject. That is a scraping
# accident. If a DECLARED subject stored a bare integer the same collision would become
# STRUCTURAL: trusted, fed to the merge grouper, and undoubtable by anything downstream,
# because "never infer" forbids the only correction mechanism a wrong value could have. A
# wrong subject is therefore strictly WORSE than none. So a subject ref carries its TYPE,
# and a work-item ref carries its repo or project as well.
#
# WHY NOT `_clean_ref`'s DICT SHAPE. `_clean_ref` names one DECISION on one task (a task
# id plus a 1-based index) — one subject class out of five, and the rarest. A heterogeneous
# subject list needs ONE shape for all five, and a `type:value` token gives three things
# the dict does not: it compares by equality, it renders WITHOUT A LOAD (the thing
# `owner_seq` had to be invented for, so declaration needs no companion field of its own),
# and it cannot degrade to a bare number.

KIND_FIELD = "kind"             # what class of record this decision IS
SUBJECT_FIELD = "subject"       # the qualified refs it is ABOUT

# The CLOSED kind vocabulary. Five classes, and the split that matters is the first
# against the rest: a RULING is the constitution, true until refuted, and must brief every
# session. The other four are case law — true forever, relevant only when touching the
# subject — which is what makes a two-tier render possible without hiding anything by AGE.
KIND_RULING = "ruling"                    # a binding decision; what the work must obey
KIND_MEASUREMENT = "measurement"          # a number taken at a moment, true forever
KIND_INCIDENT = "incident"                # what went wrong once, and why
KIND_RELEASE_RECORD = "release-record"    # X.Y.Z shipped, or a consolidation summary
KIND_PROCESS_NOTE = "process-note"        # how the work is RUN, not what it decided

KINDS = (KIND_RULING, KIND_MEASUREMENT, KIND_INCIDENT,
         KIND_RELEASE_RECORD, KIND_PROCESS_NOTE)

# The CLOSED set of subject ref types. Closed for the same reason `KINDS` is: an
# unvalidated type is free text wearing a colon, and two surfaces reading `PR:27` and
# `pr:27` as different subjects is the disagreement this whole field exists to end.
SUBJECT_TASK = "task"           # `task:596` — another task on this board
SUBJECT_STEP = "step"           # `step:29` — a numbered step on the holding task
SUBJECT_PR = "pr"               # `pr:task-station#27` — a pull request, repo-qualified
SUBJECT_STORY = "story"         # `story:atlas#2704` — a work item, project-qualified
SUBJECT_RELEASE = "release"     # `release:3.44.0` — a shipped version

SUBJECT_TYPES = (SUBJECT_TASK, SUBJECT_STEP, SUBJECT_PR,
                 SUBJECT_STORY, SUBJECT_RELEASE)

# The two types whose bare number is KNOWN to collide, so the qualifier is mandatory
# rather than encouraged. `pr:27` and `story:27` no longer collide with each other once
# the type is declared — but PR 27 on two repos still does, and this record has already
# been bitten by exactly one unqualified work-item number.
_SUBJECT_QUALIFIED = (SUBJECT_PR, SUBJECT_STORY)

# How long one subject ref may run. Not a budget and not a cap on how many refs a
# decision may declare — a shape assertion. A token longer than this is prose, and prose
# in the subject field is the thing being replaced, not a longer version of it.
SUBJECT_REF_CHARS = 80


def _clean_subject_ref(raw):
    """Canonicalise ONE declared subject ref to its `type:value` token, or say why it is
    not one. Returns `(token, None)` or `(None, reason)`.

    The reason string is half the point: this is the only gate on the field, and a
    refusal that does not say what shape was wanted just gets worked around."""
    token = " ".join(str(raw or "").split())
    if not token:
        return None, "a subject ref cannot be empty"
    if len(token) > SUBJECT_REF_CHARS:
        return None, ("%r is %d chars — a subject is a REF, not prose; keep it under %d"
                      % (token, len(token), SUBJECT_REF_CHARS))
    if ":" not in token:
        return None, ("%r is not a qualified ref. A subject names its TYPE as "
                      "`<type>:<value>`, one of %s. A bare number is REFUSED: heal's "
                      "subject tier already labels every work item `PR/story <n>` from "
                      "one bare number, so PR 27 and story 27 collide today — and a "
                      "DECLARED bare number would make that collision structural instead "
                      "of accidental, in a field nothing downstream is allowed to doubt."
                      % (token, "/".join(SUBJECT_TYPES)))
    stype, value = token.split(":", 1)
    stype = stype.strip().lower()
    value = value.strip()
    if stype not in SUBJECT_TYPES:
        return None, ("%r names no known subject type; the closed set is %s"
                      % (token, ", ".join(SUBJECT_TYPES)))
    if not value:
        return None, "%r names a %s with no value" % (token, stype)
    if " " in value:
        return None, "%r — a subject ref carries no spaces" % token
    if stype == SUBJECT_STEP and not value.isdigit():
        return None, ("%r — a step subject is the 1-based step number on the task that "
                      "HOLDS the decision, as `/todo <n>` prints it" % token)
    if stype in _SUBJECT_QUALIFIED:
        repo, sep, num = value.rpartition("#")
        if not sep or not repo or not num or repo.isdigit():
            return None, ("%r — a %s subject must be REPO-QUALIFIED, as "
                          "`%s:<repo>#<number>`. %s 27 in two repos is two different "
                          "things, and an unqualified number is the collision this field "
                          "exists to remove rather than to inherit."
                          % (token, stype, stype, stype.upper()))
    return "%s:%s" % (stype, value), None


def kind(entry):
    """The DECLARED kind of this decision, or None when it declares none — which is the
    case for every decision ever written before this field existed, and stays the normal
    case permanently.

    An unrecognised stored value also reads as None, deliberately. A kind written by a
    NEWER version must never be mistaken for one this version knows, and untyped is the
    one behaviour that is safe forever; `compact()` keeps the stored key either way, so
    reading it as absent loses nothing on disk."""
    if not isinstance(entry, dict):
        return None
    val = str(entry.get(KIND_FIELD) or "").strip().lower()
    return val if val in KINDS else None


def subject(entry):
    """Every qualified ref this decision DECLARES as its subject, in declared order and
    de-duplicated. `[]` for a legacy entry and for one that declares none.

    Tolerates a lone string as well as a list, because a subject of one is the common
    case and some writer will eventually pass it unwrapped. Silently DROPS a ref it
    cannot parse: the setter is the gate, and a reader that met an unparseable ref and
    guessed at it would reintroduce exactly the inference this field removes."""
    if not isinstance(entry, dict):
        return []
    raw = entry.get(SUBJECT_FIELD)
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    out = []
    for item in raw:
        ref, _why = _clean_subject_ref(item)
        if ref is not None and ref not in out:
            out.append(ref)
    return out


def is_typed(entry):
    """True iff this decision declares a kind."""
    return kind(entry) is not None


def has_subject(entry):
    """True iff this decision declares at least one parseable subject ref."""
    return bool(subject(entry))


def kind_label(entry):
    """`MEASUREMENT` — how a declared kind reads in a render, or "" when undeclared. The
    display form lives HERE and not per-surface: five text-only consumers project this
    log, and a kind that reads one way in the digest and another in a feed is "what a
    cold session believes" diverging by surface."""
    val = kind(entry)
    return val.upper() if val else ""


def subject_label(entry):
    """`pr:task-station#27, task:596` — how a declared subject reads in a render, or ""
    when undeclared. Load-free by construction: the ref token carries its own display
    form, which is why declaration needs no companion field."""
    return ", ".join(subject(entry))


def set_kind(entries, index1, value, flag="--kind"):
    """Declare decision `index1` to be of kind `value`. `(ok, error)`.

    TWO REFUSALS, each because something breaks without it:

      * a REPLACED decision — superseded, split or merged. It renders on no present-tense
        surface, so typing it declares the kind of a record nobody reads, and a two-tier
        render would then have a classified entry it must never show.
      * a value OUTSIDE THE CLOSED VOCABULARY, with the vocabulary named in the error.
        Validated HERE and never at the reader: a reader that accepted anything would
        make `kind` a second prose field, which is the defect being closed.

    RE-DECLARING IS ALLOWED, and this is the one place the `owner` precedent deliberately
    does NOT carry. `set_owner` refuses to re-point because an owner has a SECOND store —
    the owner task's index — and re-pointing in place would leave that index naming a
    ruling it no longer owns. A kind has no second side. It has to stay correctable: the
    design forbids any check from ever contradicting a declared value, so the AUTHOR is
    the only correction mechanism that exists, and making a correction awkward is how a
    misdeclaration becomes permanent."""
    i, err = _check_index(entries, index1, flag)
    if err:
        return False, err
    entry = entries[i - 1]
    how = _already_phrase(entry)
    if how is not None:
        return False, ("%s %d — that decision is %s, so it renders nowhere; there is no "
                       "record left to classify" % (flag, i, how))
    val = " ".join(str(value or "").split()).lower()
    if not val:
        return False, ("%s %d — no kind given; the vocabulary is %s, and `clear_kind` "
                       "removes a declaration" % (flag, i, ", ".join(KINDS)))
    if val not in KINDS:
        return False, ("%s %d — %r is not a kind. The vocabulary is CLOSED: %s. It is "
                       "closed on purpose — a free-text kind is a second prose field, "
                       "which is the thing this replaces." % (flag, i, val,
                                                              ", ".join(KINDS)))
    rich = as_rich(entry)
    rich[KIND_FIELD] = val
    entries[i - 1] = compact(rich)
    return True, None


def clear_kind(entries, index1, flag="--clear-kind"):
    """Remove the kind declaration from `index1` — the ONE inverse of `set_kind`, so a
    declaration is undone by a single command. `(ok, error)`.

    Errors rather than silently succeeding on an already-untyped decision, for the same
    reason `clear_owner` does: "cleared" after a typo reads as success, and the caller
    then believes it retracted a classification it never touched."""
    i, err = _check_index(entries, index1, flag)
    if err:
        return False, err
    if not is_typed(entries[i - 1]):
        return False, ("%s %d — that decision declares no kind; there is nothing to "
                       "clear" % (flag, i))
    rich = as_rich(entries[i - 1])
    rich.pop(KIND_FIELD, None)
    entries[i - 1] = compact(rich)
    return True, None


def set_subject(entries, index1, refs, flag="--subject"):
    """Declare what decision `index1` is ABOUT, as qualified refs. `(ok, error)`.

    Takes one ref or an iterable of them, and REPLACES whatever was declared before —
    a subject is the whole answer to "what is this about", not a growing pile, and an
    append-only subject would make a correction impossible.

    THREE REFUSALS:

      * a REPLACED decision, for the same reason `set_kind` refuses one.
      * NO refs at all — use `clear_subject` to retract, so a typo that produced an
        empty list cannot read as a successful retraction.
      * ANY unqualified or unparseable ref, and the refusal is WHOLESALE rather than
        per-ref. A partially accepted subject is the worst possible outcome here: it is
        structural, it is trusted, nothing downstream may doubt it, and it is silently
        incomplete. The refusal names the offending token and the shape wanted."""
    i, err = _check_index(entries, index1, flag)
    if err:
        return False, err
    entry = entries[i - 1]
    how = _already_phrase(entry)
    if how is not None:
        return False, ("%s %d — that decision is %s, so it renders nowhere; there is no "
                       "record left to give a subject" % (flag, i, how))
    if refs is None:
        items = []
    elif isinstance(refs, str):
        items = [refs]
    else:
        items = list(refs)
    clean = []
    for item in items:
        ref, why = _clean_subject_ref(item)
        if ref is None:
            return False, "%s %d — %s" % (flag, i, why)
        if ref not in clean:
            clean.append(ref)
    if not clean:
        return False, ("%s %d — no subject refs given; `clear_subject` retracts a "
                       "declared subject" % (flag, i))
    rich = as_rich(entry)
    rich[SUBJECT_FIELD] = clean
    entries[i - 1] = compact(rich)
    return True, None


def clear_subject(entries, index1, flag="--clear-subject"):
    """Retract the declared subject of `index1` — the ONE inverse of `set_subject`.
    `(ok, error)`. Errors on a decision that declares none, exactly as `clear_kind`
    does."""
    i, err = _check_index(entries, index1, flag)
    if err:
        return False, err
    rich = as_rich(entries[i - 1])
    if SUBJECT_FIELD not in rich:
        return False, ("%s %d — that decision declares no subject; there is nothing to "
                       "retract" % (flag, i))
    rich.pop(SUBJECT_FIELD, None)
    entries[i - 1] = compact(rich)
    return True, None


# -- cross-task supersession (the write half) ------------------------------------

def mark_superseded_across(entries, index1, ref, flag="--supersedes"):
    """Mark `index1` as refuted by the ruling `ref` names ON ANOTHER TASK. `(ok, error)`.

    Same guards as the local verb — a bad index errors, an already-replaced decision is
    refused, a pin is cleared — because a decision refuted from across a task boundary is
    exactly as wrong as one refuted from within it. `ownership.supersede_across` writes
    the matching `supersedes_across` back-pointer on the refuting entry; ONE side alone
    is the invisible-contradiction bug this exists to close, so nothing else should call
    this directly."""
    i, err = _check_index(entries, index1, flag)
    if err:
        return False, err
    err = _check_unreplaced(entries, i, flag)
    if err:
        return False, err
    clean = _clean_ref(ref)
    if clean is None:
        return False, ("%s %d — a cross-task supersession needs BOTH the task and the "
                       "decision number (`<task>:<n>`); decision numbers are per-task, so "
                       "a bare number names a different ruling on every task"
                       % (flag, i))
    rich = as_rich(entries[i - 1])
    rich[SUPERSEDED_ACROSS] = clean
    rich.pop("pinned", None)                 # a refuted decision loses its pin
    entries[i - 1] = compact(rich)
    return True, None


def add_supersedes_across(entries, index1, ref, flag="--supersedes"):
    """Record on `index1` that it refutes the ruling `ref` names on another task — the
    BACK half of a cross-task supersession, so the contradiction is visible from the
    refuting side too. Idempotent: naming the same ref twice stores it once."""
    i, err = _check_index(entries, index1, flag)
    if err:
        return False, err
    clean = _clean_ref(ref)
    if clean is None:
        return False, "%s — cross-task reference must be `<task>:<n>`" % flag
    rich = as_rich(entries[i - 1])
    refs = supersedes_across(rich)
    if clean not in refs:
        refs.append(clean)
    rich[SUPERSEDES_ACROSS] = refs
    entries[i - 1] = compact(rich)
    return True, None


def remove_supersedes_across(entries, index1, ref, flag="--restore-decision"):
    """Drop one cross-task back-pointer from `index1`. `(ok, error)`.

    The other half of undoing a cross-task supersession: `restore` clears the mark on the
    source, and this clears the claim on the refuter. Both, or the record keeps saying a
    ruling was refuted when it is live again — an over-claim that is invisible from the
    source side, which is the same one-sided blindness the pair exists to prevent."""
    i, err = _check_index(entries, index1, flag)
    if err:
        return False, err
    clean = _clean_ref(ref)
    if clean is None:
        return False, "%s — cross-task reference must name a task and a number" % flag
    rich = as_rich(entries[i - 1])
    refs = [r for r in supersedes_across(rich) if r != clean]
    if refs:
        rich[SUPERSEDES_ACROSS] = refs
    else:
        rich.pop(SUPERSEDES_ACROSS, None)
    entries[i - 1] = compact(rich)
    return True, None
