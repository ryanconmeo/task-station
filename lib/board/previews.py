"""ONE preview helper, and the two tiers every preview in this tree picks from.

WHY THIS EXISTS. A 2026-09-03 scan found eighteen hand-tuned character constants across
the tree, four of them 120, three 200, two 80 — each with its own comment saying nearly
the same sentence ("enough to recognise which X a line is about"). None of them was
wrong. What was wrong is that every session that needed a preview tuned a number inside
its own file without asking whether the number should be its own, so the answer to one
question was written down eighteen times and could drift eighteen ways.

THE RULE, AND IT IS WHY THIS MODULE IS SMALL RATHER THAN LARGE.
Before folding a constant in, ask ONE question about the number:

    Does it bound what is RENDERED, or does it guard what is STORED or ACCEPTED?

  RENDERED — a preview. The reader only needs to recognise WHICH thing the line names,
  the full value is one command away, and changing the number changes nothing but the
  shape of some output. These belong here.

  STORED or ACCEPTED — a guard. The value is truncated away for good, or refused. It
  exists for privacy, for a schema, or to stop garbage, so changing the number changes
  the RECORD or changes what the system will take. These do NOT belong here, and folding
  one in would silently convert a guard into a display setting.

Apply the question to the USE SITE, never to the constant's name or its comment. Four of
the eighteen read exactly like previews and are not: the near-misses are named below so
a future session reaching for "one more cap to tidy" meets the rule instead of the
temptation.

CONSIDERED AND CORRECTLY EXCLUDED — do not fold these in later:

  EVENT_TEXT_MAX 160 (board/_shared.py)
      A PRIVACY cap on a context-injected feed — "never a full worker result body"
      (model.py). The text past it is destroyed, not hidden.
  NUDGE_PROMPT_MAX 120 (board/_shared.py)
      `add_log` writes `note[:NUDGE_PROMPT_MAX]` into `task["log"]`. Stored, and the
      note is a user PROMPT — the same privacy shape as EVENT_TEXT_MAX.
  PICKUP_HEADLINE_MAX 300 (board/channel.py)
      The cut headline is stored on the pickup row AND compared for equality to decide
      whether a re-file re-arms the block count. It bounds a record and steers control
      flow.
  STUB_CHARS 120 (board/decisions.py)
      `derive_stub`'s output is WRITTEN to `entry["stub"]` when a decision is reassigned.
      A render path and a write path share the number, which is exactly why tuning it
      for display would be a change nobody could see.
  MEMO_LINE_MAX 200 (board/_shared.py)
      Renders the memo nag, but ALSO caps the stored body of a subscription memo
      (memos._subscription_memo_text). Same dual role as STUB_CHARS.
  SESSION_END_REASON_MAX 40 (board/_shared.py)
      Caps a STORED value "so a garbage value can never bloat the record".
  SUBJECT_REF_CHARS 80 (board/decisions.py)
      Does not truncate at all — it REFUSES ("a subject is a REF, not prose").
  PROMPT_CAP 40 (board/feeds.py)
      A COUNT, not a length: `prompts[-PROMPT_CAP:]` keeps forty prompts.

EXCLUDED FOR A SECOND, DIFFERENT REASON — the plane boundary, not the rule:

  _TOOL_ARG_MAX 60 / _TEXT_MAX 80 (lib/delegate/delegate.py)
      Both also fail the rule (the line is written into the worker registry as `phase`),
      and `lib/delegate` imports neither `board` nor anything under it.
  PREVIEW_CHARS 600 (lib/brain/ado_tree.py)
      A true preview (`--no-clip` prints the full field), but the dependency between the
      planes points board → brain and only that way. A brain module importing a board
      helper would invert it for eight characters of tidiness.

THE TWO TIERS. They are named for what the READER has to do with the line, because that
is the only thing that decides how long it should be:

  RECOGNISE — the reader must identify WHICH thing this line names; the full text is one
              command away. A fragment sharing a line with a prefix.
  READ      — the reader must take in the sentence itself without opening the record.

Nothing else is on offer. A site that believes it needs a third number should say why in
a decision first — a third tier is a design change, not a tuning.

Stdlib only, and it imports nothing from this package: a leaf every module may depend on,
including `turn.py`, whose comment refuses a dependency that would invert its direction.
"""

# The reader must recognise WHICH thing the line names. 80 is the smallest value the
# eight folded sites carried, and it is the one whose own comment ("enough to recognise
# which condition a line means") states this tier's job exactly — so the consolidation
# cannot make any surface LONGER than it already was, which is the direction 444:615's
# growth bound asks a tie to break in.
RECOGNISE = 80

# The reader must take in the sentence. Held at what the goal and child-report previews
# already used: enough to read the line, not enough to reprint the page.
READ = 200


def line(text, limit=RECOGNISE, prefix=""):
    """`text` collapsed to one line and cut to `limit`, with an ellipsis when cut.

    `prefix` is the label the caller will print in FRONT of this text; its length comes
    out of the budget, so `limit` bounds the whole rendered line rather than just the
    part the caller happened to pass in. A pathological prefix cannot squeeze the text
    to nothing — 12 chars always survive, because a line with no content is worse than
    a line that overruns.

    The cut lands on a word boundary when there is one, so a preview ends at a word and
    not mid-token. A single unbroken token (a path, a command) is cut where it must be.
    """
    flat = " ".join(str(text or "").split())
    budget = max(12, int(limit) - len(prefix or ""))
    if len(flat) <= budget:
        return flat
    head = flat[:budget - 1]
    return (head.rsplit(" ", 1)[0] or head).rstrip() + "…"
