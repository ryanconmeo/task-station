"""handles.py — the WRITE-ONCE cross-machine name for a task (#532, J-track step 2).

    stored     kosei-e6440959-b7f1-4066-8d21-cd7512f4e9fd
    displayed  kosei-e6440959

THREE IDENTIFIERS, THREE JOBS, and conflating any two of them is how this went wrong
before:

  `seq` (#444)   PURELY LOCAL ergonomics. It is what you type, it is small and
                 chronological, and it NEVER leaves the machine as an identifier.
  `uuid`         the join key. Every cross-machine reference resolves through it.
  `handle`       `<owner>-<uuid>` — the human cross-machine name. Stamped ONCE at
                 creation, immutable thereafter, stored in full.

WHY NOT `<owner>-<seq>`, which is what the earlier design said. It does not meet "there
cannot be a conflict", and the reason is not where that design looked. Two OWNERS
colliding is already handled — aliases lock to an org identity and the registry is a
file-claim, so a duplicate conflict-rejects. THE REAL HOLE IS ONE OWNER ON TWO
MACHINES: `seq` is assigned machine-locally, so an unsynced laptop and desktop both
hand out the same next number and two different tasks both become `kosei-512`.

WHY NOT HI-LO (block-allocated ranges), which was the ruled mechanism until it was
tested against this environment: Hi-Lo needs a station to CLAIM a range before it can
create a task. A coordinate-before-you-create scheme fails exactly when you are
offline on the second machine — which is precisely when that machine is assigning
numbers. Hi-Lo delivers "unique" but not "cannot conflict", and it destroys the
small-friendly-number property on the way by making seqs non-contiguous.

`<owner>-<uuid>` needs ZERO coordination: no allocator, no block claim, no bootstrap,
no exhaustion policy. Two stations can create tasks simultaneously while disconnected
with no possibility of collision, because neither is choosing from a shared space.

DISPLAY IS COLLISION-DRIVEN, WHICH IS THE PART THAT WAS MISSING. The width used to be
HARDCODED AT 8. Eight is fine until it isn't: measured on a real 371-task store, a
4-hex prefix ALREADY had 2 collision groups while 6 and 8 had none — so collisions at
short lengths are a real phenomenon and a fixed width is a bug waiting for the store
to grow. `display_map` therefore does what git does with abbreviated commit hashes:
start at a floor and lengthen ONLY as far as ambiguity forces.

THE FLOOR IS 8 AND THAT IS A DELIBERATE CHOICE, not the shortest possible. Git keeps a
floor too (7) for the same two reasons: a name that changes length every time the
store grows is unpleasant to live with, and 8 is ALREADY THE HOUSE CONVENTION — the
board prints `[6cfbcab2]` for a task and `cef507de` for a session. So today's rendering
is byte-identical to yesterday's, and what changed is that it can now GROW instead of
silently becoming ambiguous.

BONUS THAT MATTERS: `kosei-6cfbcab2` cannot be mistaken for a work-item number, which
is the exact hazard the ADO numbering rule exists to prevent — whereas `kosei-444`
still reads like one.
"""
import re

SEP = "-"

# The floor, not the minimum possible. See THE FLOOR IS 8 above.
MIN_WIDTH = 8

# The uuid half of a handle: canonical uuid4 (8-4-4-4-12) or bare 32-hex, anchored to
# the END. Matching the TAIL rather than splitting on the first separator is what lets
# an owner alias contain a hyphen — `mary-jane-e6440959-…` splits correctly, and
# splitting on the first `-` would have handed back the owner `mary`.
_UUID_TAIL = re.compile(
    r"(?:[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}|[0-9a-fA-F]{32})$")


def mint(owner, uuid):
    """The stored handle for a task. Called ONCE, at creation."""
    o = str(owner or "").strip()
    u = str(uuid or "").strip()
    if not o or not u:
        return ""
    return o + SEP + u


def split(handle):
    """`(owner, uuid)` for a full handle, or `(None, None)` when it is not one."""
    h = str(handle or "").strip()
    m = _UUID_TAIL.search(h)
    if not m or m.start() < 2 or h[m.start() - 1] != SEP:
        return (None, None)
    return (h[:m.start() - 1], h[m.start():])


def owner_of(handle):
    return split(handle)[0]


def uuid_of(handle):
    return split(handle)[1]


def _abbrev(handle, width):
    """`handle` cut to `width` uuid characters, never ending on a separator — a name
    that trails a dash reads as truncated-by-accident, and the next character is free."""
    owner, uuid = split(handle)
    if uuid is None:
        return handle
    w = min(max(int(width), 1), len(uuid))
    if w < len(uuid) and uuid[w - 1] == SEP:
        w += 1
    return owner + SEP + uuid[:w]


# The last display_map answer, keyed by exactly what it was computed from. A board
# render asks the same question hundreds of times — 491 calls over the same handle
# set in one `board --refresh-if-live`, MEASURED on 3.63.0, which is 8.0s of a 16.4s
# refresh, because the ambiguity search is quadratic in the pool. The pool is a
# value, the answer is a pure function of it, so one slot is enough: consecutive
# callers share a pool, and a caller with a different pool simply misses and pays
# what it always paid. Never invalidated because nothing can go stale — a different
# input is a different key.
_DISPLAY_MEMO = (None, None)


def display_map(handles, min_width=MIN_WIDTH):
    """`{full handle: displayed handle}` — each cut to the SHORTEST prefix that is
    unambiguous among `handles`, floored at `min_width` uuid characters.

    Ambiguity is judged on the WHOLE handle, so two owners never lengthen each other:
    `kosei-aaaa…` and `jpark-aaaa…` are already distinct at the floor."""
    global _DISPLAY_MEMO
    hs = sorted({str(h) for h in handles if h})
    key = (tuple(hs), min_width)
    memo_key, memo_val = _DISPLAY_MEMO
    if memo_key == key:
        return dict(memo_val)                # a copy: the caller may mutate its map
    out = {}
    for h in hs:
        owner, uuid = split(h)
        if uuid is None:
            out[h] = h
            continue
        w = min_width
        while w < len(uuid):
            cand = _abbrev(h, w)
            if sum(1 for o in hs if o.startswith(cand)) == 1:
                break
            w += 1
        out[h] = _abbrev(h, w)
    _DISPLAY_MEMO = (key, dict(out))
    return out


def display(handle, others=(), min_width=MIN_WIDTH):
    """One handle's display form, disambiguated against `others`."""
    if not handle:
        return ""
    pool = set(others or ())
    pool.add(handle)
    return display_map(pool, min_width=min_width).get(handle, handle)


def matches(ref, handle):
    """True when `ref` names `handle` — exactly, or as a prefix, exactly the way an
    abbreviated commit hash names a commit. Case-insensitive on the hex half only in
    the sense that the whole comparison is lowercased; aliases are already lowercase
    by construction."""
    r = str(ref or "").strip().lower()
    h = str(handle or "").strip().lower()
    if not r or not h:
        return False
    return h == r or h.startswith(r)


def resolve(ref, handles):
    """Every handle `ref` could name, sorted. Zero hits means no such task; MORE THAN
    ONE means the ref is ambiguous and the caller must say so rather than picking —
    silently choosing one of two tasks is the failure mode abbreviation exists to make
    visible, not to hide."""
    r = str(ref or "").strip()
    if not r or SEP not in r:
        return []
    exact = [h for h in handles if str(h).strip().lower() == r.lower()]
    if exact:
        return sorted(set(exact))
    return sorted({h for h in handles if matches(r, h)})


def ensure(task, owner):
    """Stamp `task["handle"]` if it has none. WRITE-ONCE: a task that already carries
    one keeps it, always — including one minted by a DIFFERENT owner, which is exactly
    what a task received over sync carries and exactly what must not be rewritten.
    Returns True when it wrote."""
    if not isinstance(task, dict) or task.get("handle"):
        return False
    uid = task.get("uuid") or task.get("id")
    h = mint(owner, uid)
    if not h:
        return False
    task["handle"] = h
    return True
