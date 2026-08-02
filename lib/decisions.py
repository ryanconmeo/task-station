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
    needs a second audit of the readers. A legacy plain string is always current."""
    if not isinstance(entry, dict):
        return None
    for key, kind, many in _REPLACEMENTS:
        if key not in entry:
            continue
        targets = _index_list(entry.get(key), many)
        if targets:
            return kind, targets
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

LONG_DECISION_CHARS = 600   # past this, `length_warning` nudges — see below


def length_warning(entry, index1=None, limit=LONG_DECISION_CHARS):
    """One ADVISORY line when a decision runs past `limit` chars, else None.

    THIS NEVER REFUSES, and that is the whole design. A gate here would push the
    author to drop a fact, or to fake two decisions out of one to get under the
    number — and this project has the evidence: the pin cap DID refuse, and the
    refusal produced a workaround rather than a fix. So the write always succeeds,
    in full, byte-identical, and the author gets a suggestion instead of an error.

    Distinct from `heal.OVERSIZE_CHARS` (4000) by job, not by disagreement: this
    fires at WRITE time, when the author still has the context to split the entry
    cheaply; that one is the reconcile scan's threshold for an entry already in the
    log. A nudge can afford to be far more sensitive than a finding."""
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
