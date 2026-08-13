"""Task-model core: status/category/effort, F9 identity, briefing + typed-edge writes."""
from board import _shared
from board._shared import *          # constants + utilities (explicit __all__)
from board.state import *
import re
import uuid

import heal as _heal
import steps as _steps

g, set_g = _shared.g, _shared.set_g

__all__ = [
    "_cli_fallback",
    "correction_language",
    "status_display", "normalize_status_input",
    "cat_color", "cat_tag", "is_on_board",
    "status_glyph", "status_legend", "statusline_segment",
    "task_oneline", "cat_lines",
    "_auto_checkpoint_enabled", "auto_enable_category",
    "normalize_effort", "effort_cell", "effort_legend",
    "extract_identity_keys", "render_identity_keys", "task_identity_keys",
    "similar_open_task", "seed_title", "clear_provisional",
    "add_log", "add_event",
    "record_activity_span", "time_in_task", "_fmt_duration",
    "task_cost", "task_wasted_cost", "add_cost",
    "_run_cost", "record_run", "task_stats_line",
    "_usage_engine", "_stats_cost", "_usage_stats_segment",
    "stamp_closed", "clear_closed",
    "new_task",
    "extract_prs",
    "append_step", "set_step_done", "step_progress",
    "append_decision", "append_history", "append_related",
    "_looks_foreign_ref", "_resolve_edge_ref", "_relation_cycle_path",
    "_parent_children", "remove_related",
    "mark_digest_dirty", "clear_digest_dirty", "bump_digest_events",
    "digest_events", "digest_stale",
    "mark_obsidian_dirty", "clear_obsidian_dirty", "obsidian_dirty",
    "mark_pressure_nudged", "clear_pressure_nudged",
    "_pr_entry", "_normalize_prs", "add_pr", "set_pr_desc", "merged_prs",
    "_story_entry", "_normalize_stories", "add_story", "set_story_desc",
    "merged_stories", "_story_refs",
    "append_edited_file",
    "_glossary_entry", "_normalize_glossary", "add_glossary_term",
    "edit_glossary_term", "remove_glossary_term",
    "_glossary_pill", "_format_glossary", "glossary_context",
]


def _cli_fallback():
    """Parenthetical fallback for the short `task-station <cmd>` form we now show
    in model-facing guidance/help. Claude Code puts the plugin's bin/ on the Bash
    tool PATH while the plugin is enabled, so `task-station` runs this engine; this
    names the absolute python3 form for shells where bin/ isn't on PATH."""
    return ("(`task-station` resolves via the plugin's bin/ on PATH; if it isn't, "
            "run: python3 %s/task-station.py …)" % g("BASE"))


def correction_language(body):
    """The CORRECTION_PATTERNS this memo uses to DECLARE ITSELF a correction, in list
    order. Empty when the body reads like ordinary correspondence.

    THE KEYWORD ALONE WAS NOT ENOUGH, and shipping it that way made this the FOURTH
    check in this subsystem to cry wolf the same way (see the guard's own notes in
    heal.py). Plain substring matching warned on both of these, neither of which is
    retracting anything:

        "Shipped 2.13.1: heal now distinguishes a step that declares itself stale
         from one that merely mentions a superseded ancestor"    → a release note
        "FYI the upstream library withdrawn its 3.0 release"      → someone ELSE's
                                                                     retraction

    So it routes through the ONE discrimination the other language checks share
    (`heal.declaring_hits`), which reads the word standing in front of each match: the
    memo has to be retracting or replacing something the reader is expected to already
    believe. Two vocabularies, because the patterns are not all the same part of speech
    — a determiner in front of the NOUN "correction" still declares one ("a
    correction"), while the same determiner in front of the participle "superseded"
    is exactly what makes it describe some other noun.

    Still a backstop that warns and never gates, and still deliberately biased toward
    silence: a missed nudge costs one reminder, a warning nobody believes costs all of
    them."""
    verbs = [p for p in CORRECTION_PATTERNS if p not in CORRECTION_NOUN_PATTERNS]
    hits = set(_heal.declaring_hits(body, verbs, _heal.SELF_DECLARING_QUALIFIERS))
    hits |= set(_heal.declaring_hits(body, CORRECTION_NOUN_PATTERNS,
                                     _heal.NOUN_DECLARING_QUALIFIERS))
    return [p for p in CORRECTION_PATTERNS if p in hits]


def status_display(status):
    """User-facing label for a stored status — `open` shows as 'new'. Unknown
    values fall through unchanged. The board SECTION 'Open' is unaffected (that
    name is set independently); this only relabels the per-task state."""
    return STATUS_DISPLAY.get(status, status)


def normalize_status_input(value):
    """Map a typed status word to its stored value: `new` → the stored `open`
    (the input alias for the relabelled per-task state). Everything else is
    lowercased/stripped and returned unchanged."""
    v = (value or "").strip().lower()
    return STATUS_INPUT_ALIASES.get(v, v)


def cat_color(color):
    """Normalised colour to store on a task, or None when categories are off."""
    return cats.normalize(color) if cats else None


def cat_tag(color, pad=False):
    """`<emoji> [TAG]` for the list, or "" when categories are off."""
    return cats.tag(color, pad=pad) if cats else ""


def is_on_board(task):
    """True iff the task is still on the board (not closed — open or active)."""
    return not is_closed(task)


def status_glyph(task, muted_closed=True):
    """Leading lifecycle glyph for a row: `○` open / `●` active. Single-width,
    ASCII-safe. Closed tasks mute to a blank placeholder (single space) so the
    column still aligns — closed tasks live in their own section."""
    if muted_closed and is_closed(task):
        return " "
    s = task_status(task)
    return STATUS_GLYPH.get(s, STATUS_GLYPH[STATUS_OPEN])


def status_legend():
    """One-line legend explaining the per-task states (closed shown separately)."""
    return "Status:  %s new · %s in progress · %s closed" % (
        STATUS_GLYPH[STATUS_OPEN], STATUS_GLYPH[STATUS_ACTIVE], STATUS_GLYPH_CLOSED)


def statusline_segment(task, width=0, ordinal=None):
    """A ready-to-display, ANSI-colored one-line segment for a status bar:
    '#<seq>  <dot> [TAG]  <title>'. Self-contained — it carries its own colors
    and knows nothing about the bar that renders it. When width > 0 the title is
    truncated (with an ellipsis) so the whole visible segment fits that many
    columns; width 0 means no limit. When `ordinal` is given (a hub session's
    number for this task), the number renders '#<seq>-<n>' (#463)."""
    RESET = "\033[0m"
    C_SEQ   = "\033[38;2;235;215;120m"   # task number
    C_TAG   = "\033[38;2;150;150;160m"   # [CATEGORY] tag text
    C_TITLE = "\033[38;2;215;215;220m"   # title
    seq = str(task.get("seq", "") or "")
    if ordinal is not None:
        seq = "%s-%s" % (seq, ordinal)
    title = task.get("title", "") or ""
    tag = cat_tag(task.get("color"))     # '<emoji> [TAG]' — emoji is self-colored
    # Color only the bracketed tag text, leaving the emoji dot untouched.
    if tag and "[" in tag:
        dot, _, rest = tag.partition("[")
        tag_disp = "%s%s[%s%s" % (dot, C_TAG, rest, RESET)
    else:
        tag_disp = tag
    prefix_plain = "#%s  %s%s" % (seq, (tag + "  ") if tag else "", "")
    if width and width > 0:
        avail = width - len(prefix_plain)
        if avail < 1:
            avail = 1
        if len(title) > avail:
            title = title[: max(1, avail - 1)] + "…"
    parts = ["%s#%s%s" % (C_SEQ, seq, RESET)]
    if tag:
        parts.append(tag_disp)
    parts.append("%s%s%s" % (C_TITLE, title, RESET))
    return "  ".join(parts)


def task_oneline(task):
    """One-line task summary matching the /todo list row's content: number,
    title, category tag, effort gauge. Used by the -s jump confirmation so it
    reads the same as the list. No fixed-width padding (it stands alone, not in
    a column) and no activity timestamp."""
    parts = ["%s  %s" % (task.get("seq", task["id"][:8]), task["title"])]
    tag = cat_tag(task.get("color"))
    if tag:
        parts.append(tag)
    parts.append(effort_cell(task.get("effort")))
    return "  ".join(parts)


def cat_lines(color):
    """Category summary line(s), or [] when categories are off. The terminal is
    tinted automatically by the hooks (tint_escape) — nothing to run by hand."""
    if not cats:
        return []
    return [cats.summary(color)]


def _auto_checkpoint_enabled():
    """Best-effort read of the opt-in auto-checkpoint master switch (config +
    TASK_STATION_AUTO_CHECKPOINT env). Never raises — a missing/broken config reads
    as off, so callers can gate cheaply without a try/except of their own."""
    try:
        import config
        return config.auto_checkpoint_enabled()
    except Exception:
        return False


def auto_enable_category(color):
    """If `color` is a real category not yet on the board and auto_categories is on,
    enable it and print a one-line notice (so the board grows as tasks are
    categorised). No-op when categories are off / the helper is unavailable."""
    if not cats or not color or not hasattr(cats, "auto_enable"):
        return
    msg = cats.auto_enable(color)
    if msg:
        print(msg)


def normalize_effort(val):
    """Map an agent/user-supplied effort token to a canonical size, or None.

    Accepts the sizes themselves (xs/s/m/l/xl), words (small/large/…) and the
    numeric 1–5 scale. Unknown input returns None so a typo never mislabels a
    task — the caller leaves the field unset rather than guessing."""
    if not val:
        return None
    return _EFFORT_ALIASES.get(str(val).strip().lower())


def effort_cell(effort):
    """Fixed-width `<gauge> <size>` cell for the list, or a neutral placeholder.

    The gauge is a fixed 5-segment bar; the size label is padded to 2 so XS/XL
    line up with S/M/L. Unknown effort renders an all-empty bar + `--` so the
    column stays aligned."""
    if effort in EFFORT_GAUGE:
        return "%s %-2s" % (EFFORT_GAUGE[effort], effort)
    return "%s --" % EFFORT_GAUGE_EMPTY


def effort_legend():
    return "Effort:  " + "  ".join("%s %s" % (EFFORT_GAUGE[s], s) for s in EFFORT_ORDER)


# --- F9 identity-keyed fold-in -------------------------------------------------
# Strong identity keys let the attach/nudge path join tasks on IDENTITY (the PR or
# work-item the prompt names) instead of FLAVOR (shared process words). This is the
# same principle create-dedup already applies via _norm_nums (see similar_open_task),
# lifted to a typed, shared extractor so a PR number can never join a story number.
# A key is a typed string: "pr:<n>" | "wi:<n>". Fail-open — bad/None input → set().


def extract_identity_keys(text):
    """Strong identity keys from free text OR a url — typed so identities never
    cross-join. Recognizes PR refs ("PR 1115", "PR-1115", "PR#1115", "pr/1115",
    "pull/1115", "pullrequest/1115", bare "#1115", ".../pull(request)/1115") and
    work-item refs ("AB#3166", project-prefixed "Projectname-3166", "story 3166",
    "workitem 3166", "work item 3166", ".../_workitems/edit/3166"). Returns a set
    of canonical keys ("pr:1115", "wi:3166"); empty for keyless text (fail-open)."""
    if not text:
        return set()
    s = str(text)
    low = s.lower()
    keys = set()
    # PR references (keyword-anchored, any of space/hyphen/#/slash separators).
    for m in re.finditer(r"(?i)\b(?:pr|pull|pullrequest)[\s\-#/]*(\d{1,7})\b", s):
        keys.add("pr:" + m.group(1))
    # PR/pull urls (…/pull/1115, …/pullrequest/1115) — also covered above, kept for
    # urls where the keyword isn't on a \b boundary.
    for m in re.finditer(r"/pull(?:request)?/(\d{1,7})\b", low):
        keys.add("pr:" + m.group(1))
    # Work items: "AB#3166".
    for m in re.finditer(r"(?i)\bAB#(\d{1,7})\b", s):
        keys.add("wi:" + m.group(1))
    # Project-prefixed work items ("Projectname-3166", "OtherProj-3166"). Prefix must start
    # uppercase (project names are capitalized) so "utf-8"/"sha-256" don't read as
    # ids; PR keywords are excluded so "PR-1115" stays a PR, not a work item.
    for m in re.finditer(r"\b([A-Z][A-Za-z0-9]*)-(\d{1,7})\b", s):
        if m.group(1).lower() in _PR_WORDS:
            continue
        keys.add("wi:" + m.group(2))
    # Keyword-anchored work items ("story 3166", "workitem 3166", "work item 3166").
    for m in re.finditer(r"(?i)\b(?:story|work\s?item)\s*#?\s*(\d{1,7})\b", s):
        keys.add("wi:" + m.group(1))
    # Work-item urls (…/_workitems/edit/3166).
    for m in re.finditer(r"_workitems/edit/(\d{1,7})\b", low):
        keys.add("wi:" + m.group(1))
    # Bare "#1115" — a PR form per the fold-in spec, LOWEST precedence: a number a
    # work-item pattern already claimed ("story #3166") stays a work item, never a
    # PR. (AB#N is excluded by the word-char lookbehind.) NOTE: a bare "#N" meant
    # as a task seq will read as a PR key; the guard is fail-open (soft-block,
    # --force-key), so this is an accepted edge.
    for m in re.finditer(r"(?<!\w)#(\d{1,7})\b", s):
        if ("wi:" + m.group(1)) not in keys:
            keys.add("pr:" + m.group(1))
    return keys


def render_identity_keys(keys):
    """Canonical keys → a glanceable human string, PRs first then work items,
    numerically sorted: {'pr:1115','wi:3166'} → 'PR 1115, story 3166'. '' for the
    empty set."""
    prs = sorted((int(k[3:]) for k in keys if k.startswith("pr:")))
    wis = sorted((int(k[3:]) for k in keys if k.startswith("wi:")))
    parts = ["PR %d" % n for n in prs] + ["story %d" % n for n in wis]
    return ", ".join(parts)


def task_identity_keys(task):
    """The identity keys a task's payload carries — extracted from its title,
    summary, and stored/derived PR + story urls. Same typed key space as
    extract_identity_keys, so a prompt's keys can be intersected with a task's."""
    if not task:
        return set()
    keys = extract_identity_keys(task.get("title"))
    keys |= extract_identity_keys(task.get("summary"))
    for ent in merged_prs(task):
        keys |= extract_identity_keys(ent.get("url"))
    for ent in merged_stories(task):
        keys |= extract_identity_keys(ent.get("url"))
    return keys


def similar_open_task(title):
    """Return the most similar OPEN task if its title strongly overlaps `title`.

    Scores the larger of Jaccard overlap and containment (share of the new
    title's tokens already covered by an existing one). Containment only counts
    when at least two tokens are shared, so a one-word title can't match
    everything. Containment handles noise like a trailing "(PROJ-1234)" tag and
    singular/plural drift that would otherwise sink a pure-Jaccard score.

    Numeric IDs are treated as identity: if the new title carries number(s)
    (a PR/bug/story #) and a candidate shares none of them, they are different
    work items and the candidate is skipped — this stops short, generic titles
    ("Auto-review PR 697") from colliding on process words ("auto", "review")
    alone with an unrelated open task.
    """
    want = _norm_tokens(title)
    if not want:
        return None
    want_nums = _norm_nums(title)
    best, best_score = None, 0.0
    for t in sorted_tasks():
        if is_closed(t):
            continue
        cand_title = t.get("title", "")
        # A numbered new title only matches a candidate sharing one of its numbers.
        if want_nums and not (want_nums & _norm_nums(cand_title)):
            continue
        have = _norm_tokens(cand_title)
        if not have:
            continue
        inter = len(want & have)
        jaccard = inter / len(want | have)
        containment = inter / len(want) if inter >= 2 else 0.0
        score = max(jaccard, containment)
        if score > best_score:
            best, best_score = t, score
    return best if best_score >= 0.6 else None


def seed_title(prompt):
    """A provisional title from a free-typed prompt: the first sentence, else the
    first ~60 chars, whitespace-collapsed and trimmed. Falls back to "Untitled
    task" when the prompt is empty. Used by guaranteed-tracking auto-create."""
    text = re.sub(r"\s+", " ", (prompt or "")).strip()
    if not text:
        return "Untitled task"
    first = re.split(r"[.;\n?!]", text, maxsplit=1)[0].strip()
    seed = first or text
    if len(seed) > 60:
        seed = seed[:60].rstrip()
    return seed or "Untitled task"


def clear_provisional(task):
    """Drop the `provisional` flag once a task gets genuine engagement (a real
    update, promotion to active, a folded note, or a linked worker). A task is
    provisional ONLY while auto-created-and-untouched — clearing it protects it
    from the skip/close GC that reaps untouched provisional auto-tasks."""
    if task is not None and task.get("provisional"):
        task["provisional"] = False


def add_log(task, note, session=None):
    note = (note or "").strip()
    if not note:
        return
    task.setdefault("log", []).append({"ts": _iso(_now()), "note": note[:NUDGE_PROMPT_MAX]})
    task["log"] = task["log"][-LOG_KEEP:]
    add_event(task, "log", note, session)


def add_event(task, kind, text, session=None):
    """Append to the bounded, session-attributed event feed — THE source the
    delta-brief (WS5) diffs against a session's `seen_ts` high-water mark. Epoch-float
    `ts` (matches session_meta, unlike the activity log's ISO). Session-attributed so a
    resumed session can tell its OWN work from what other sessions/workers/child tasks
    did. Text is capped at EVENT_TEXT_MAX (privacy: never a full worker result body).
    Feed is trimmed to EVENTS_KEEP most-recent. Does NOT save — the caller persists.
    Back-compat: the `events` field is created on first append; a task without it
    renders exactly as before."""
    ev = {"ts": _now(), "kind": kind, "sid": session,
          "id": uuid.uuid4().hex,
          "text": (text or "")[:EVENT_TEXT_MAX]}
    evs = task.setdefault("events", [])
    evs.append(ev)
    if len(evs) > EVENTS_KEEP:
        del evs[: len(evs) - EVENTS_KEEP]
    return ev


def record_activity_span(task, ts=None):
    """Fold an activity moment at `ts` into the task's `spans` — the append-capped
    [start, end] segments the time-in-task stat sums. A bump within IDLE_GAP_CAP of
    the last one EXTENDS the current span; a longer gap starts a NEW span (so idle
    gaps between work sessions are never counted). Cheap, derived-at-write, and
    defensive — a bad ts is ignored rather than corrupting the record."""
    try:
        ts = float(ts if ts is not None else _now())
    except (TypeError, ValueError):
        return
    spans = task.setdefault("spans", [])
    if spans and 0 <= ts - spans[-1][1] <= IDLE_GAP_CAP:
        spans[-1][1] = ts                     # same working session → extend the span
    elif spans and ts < spans[-1][1]:
        return                                # out-of-order/duplicate ts → ignore
    else:
        spans.append([ts, ts])               # first activity, or past the idle cap
    if len(spans) > SPANS_KEEP:
        del spans[:-SPANS_KEEP]               # keep the most-recent SPANS_KEEP segments


def time_in_task(task):
    """Total active seconds spent on a task — the sum of its recorded activity
    spans (each capped so a >30-min idle gap doesn't inflate it). 0 when no spans
    have been recorded yet (older tasks predating span tracking)."""
    total = 0.0
    for span in task.get("spans") or []:
        try:
            start, end = float(span[0]), float(span[1])
        except (TypeError, ValueError, IndexError):
            continue
        if end > start:
            total += end - start
    return total


def _fmt_duration(seconds):
    """A compact human duration: '~Xh Ym', '~Ym', or '<1m'. Used by the stats line."""
    m = int(seconds // 60)
    if m <= 0:
        return "<1m"
    h, m = divmod(m, 60)
    if h:
        return "~%dh %dm" % (h, m) if m else "~%dh" % h
    return "~%dm" % m


def task_cost(task):
    """Accumulated worker cost (USD) recorded on a task via delegate runs, as
    (total_usd, run_count) — the REAL-WORK figure only (crashed/timed-out spend is
    the separate `wasted` bucket, see task_wasted_cost). Back-compat: absent `cost`
    reads as zero, and a pre-#463 `total_usd` is unchanged (it never held wasted)."""
    cost = task.get("cost") or {}
    try:
        return float(cost.get("total_usd") or 0.0), int(cost.get("runs") or 0)
    except (TypeError, ValueError):
        return 0.0, 0


def task_wasted_cost(task):
    """Worker cost (USD) spent by runs that CRASHED / TIMED-OUT / FAILED, as
    (wasted_usd, wasted_run_count) — recorded distinctly from the real-work total
    (RESOLVED #4: tokens burned by failed runs are neither skipped nor folded into
    real spend). Zero when nothing was wasted / on a pre-#463 task."""
    cost = task.get("cost") or {}
    try:
        return float(cost.get("wasted_usd") or 0.0), int(cost.get("wasted_runs") or 0)
    except (TypeError, ValueError):
        return 0.0, 0


def add_cost(task, usd, category="real"):
    """Accumulate one delegate run's cost onto the task. `category="real"` (default)
    feeds the real-work total (total_usd + run count); `category="wasted"` feeds the
    SEPARATE crashed/timed-out bucket (wasted_usd + wasted_runs) so the two never
    mix (RESOLVED #4). Blank/non-positive amounts are a no-op. Returns True when a
    stored total changed."""
    try:
        usd = float(usd)
    except (TypeError, ValueError):
        return False
    if usd <= 0:
        return False
    cost = task.setdefault("cost", {})
    if category == "wasted":
        cost["wasted_usd"] = round(float(cost.get("wasted_usd") or 0.0) + usd, 6)
        cost["wasted_runs"] = int(cost.get("wasted_runs") or 0) + 1
    else:
        cost["total_usd"] = round(float(cost.get("total_usd") or 0.0) + usd, 6)
        cost["runs"] = int(cost.get("runs") or 0) + 1
    return True


def _run_cost(usd):
    """A positive float for a run record's cost_usd, else None (a blank/zero/garbage
    amount records no cost rather than a misleading $0)."""
    try:
        v = float(usd)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def record_run(task, *, seq_label=None, session_id=None, model=None, cost_usd=None,
               usage=None, category="real"):
    """Append one delegate run's per-run record to task['runs'] (append-only, capped
    at RUNS_CAP most-recent). Complements add_cost (the running total) with a
    per-invocation breakdown — model + token usage + cost + `category`
    (real|wasted, so a crashed run's spend is attributable) — so /todo and the board
    can attribute spend to a specific worker run. Always appends (returns True);
    callers decide when a run is worth recording."""
    rec = {"ts": _now(), "seq_label": seq_label, "session_id": session_id,
           "model": model, "cost_usd": _run_cost(cost_usd), "usage": usage or None,
           "category": category or "real"}
    runs = task.setdefault("runs", [])
    runs.append(rec)
    if len(runs) > RUNS_CAP:
        del runs[:-RUNS_CAP]
    add_event(task, "run", "run recorded" + ((" · " + model) if model else ""), session_id)
    return True


def task_stats_line(task):
    """The compact time/cost stat: 'time ~Xh Ym across N sessions · workers $X.XX'.
    Only the parts with data are shown; returns '' when there's nothing to report,
    so the detail/board render simply omits the line for a brand-new task."""
    parts = []
    secs = time_in_task(task)
    if secs > 0:
        n = len(task.get("sessions") or [])
        sess = "%d session%s" % (n, "" if n == 1 else "s")
        parts.append("time %s across %s" % (_fmt_duration(secs), sess))
    total_usd, runs = task_cost(task)
    if total_usd > 0:
        parts.append("workers $%.2f" % total_usd)
    # Crashed/timed-out spend, shown DISTINCTLY from the real-work figure so the
    # latter stays historically comparable (RESOLVED #4) — never folded in.
    wasted_usd, wasted_runs = task_wasted_cost(task)
    if wasted_usd > 0:
        parts.append("wasted $%.2f (%d run%s)"
                     % (wasted_usd, wasted_runs, "" if wasted_runs == 1 else "s"))
    # Derived per-model mix + $ from the usage ledger (WS1) when it has data — the
    # delegate-reported `workers $X` above stays as the cross-check figure. Reads
    # the persisted ledger only (no transcript IO); silent + inert when the ledger
    # is empty or usage tracking is off, so a brand-new task still renders ''.
    seg = _usage_stats_segment(task)
    if seg:
        parts.append(seg)
    return "  ·  ".join(parts)


def _usage_engine():
    """Import lib/usage.py and point its resolved-path globals at this engine's
    frozen paths (so a test repointing PROJECTS_ROOT/DELEGATE_REGISTRY flows through)."""
    import usage
    usage.PROJECTS_ROOT = g("PROJECTS_ROOT")
    usage.WORKERS_REGISTRY = g("DELEGATE_REGISTRY")
    return usage


def _stats_cost(data):
    """The never-`n/a` cost display for a task, from a `task_usage`-shaped dict.
    A fallback chain so an unknown (unpriced) model never blanks the figure out:

      • fully priced, derived total > 0  → {'text': '$X.XX',          'kind': 'derived'}
      • else a delegate-reported total   → {'text': '$X.XX reported', 'kind': 'reported'}
      • else a priced subtotal > 0       → {'text': '≥$X.XX',         'kind': 'floor'}
      • else                             → {'text': '$0.00',          'kind': 'derived'}

    `usd` is the numeric figure behind `text` (0.0 for the $0.00 floor). `total_cost_usd`
    is already the priced subtotal when some model is unpriced, so it doubles as the
    floor. Pure — no IO; callers pass the ledger dict they already have."""
    data = data or {}
    any_unpriced = bool(data.get("any_unpriced"))
    try:
        total = float(data.get("total_cost_usd") or 0.0)
    except (TypeError, ValueError):
        total = 0.0
    try:
        reported = float(data.get("reported_cost_usd") or 0.0)
    except (TypeError, ValueError):
        reported = 0.0
    if not any_unpriced and total > 0:
        return {"text": "$%.2f" % total, "kind": "derived", "usd": round(total, 6)}
    if reported > 0:
        return {"text": "$%.2f reported" % reported, "kind": "reported",
                "usd": round(reported, 6)}
    if total > 0:                       # some models priced, some unknown → a floor
        return {"text": "≥$%.2f" % total, "kind": "floor", "usd": round(total, 6)}
    return {"text": "$0.00", "kind": "derived", "usd": 0.0}


def _usage_stats_segment(task):
    """The compact ledger segment for the stats line — e.g.
    `fable 79% / opus 21% · $23.87 derived`. '' when the ledger is empty, usage
    tracking is off, or anything goes wrong (never breaks a render). Never emits
    `$n/a`: an unknown-model task shows the reported $ or a `≥` floor instead."""
    try:
        import config as _cfg
        if not _cfg.usage_tracking_enabled():
            return ""
        import pricing
        data = _usage_engine().task_usage(_backend(), task)
        models = data.get("models") or {}
        if not models:
            return ""
        ranked = sorted(models.items(), key=lambda kv: -kv[1].get("pct", 0))
        mix = " / ".join("%s %d%%" % (pricing.model_family(m), round(d.get("pct", 0) * 100))
                         for m, d in ranked[:3])
        sc = _stats_cost(data)
        if sc["usd"] <= 0:              # nothing priced or reported → mix only
            return mix
        if sc["kind"] == "reported":
            return mix + " · " + sc["text"]            # "$X.XX reported"
        return mix + " · %s derived" % sc["text"]      # "$X.XX derived" / "≥$X.XX derived"
    except Exception:
        return ""


def stamp_closed(task):
    """Record the real moment a task entered a closed status. Called ONLY on the
    genuine open/active → closed transition (every close path) — a later ordinary
    update must not re-stamp it, so this is never called from touch/save."""
    task["closed_ts"] = _now()


def clear_closed(task):
    """Drop the closed timestamp — the task is being reopened, so it is no longer
    closed. Safe when never set."""
    task.pop("closed_ts", None)


def new_task(title, summary, color=None, effort=None, status=STATUS_DEFAULT):
    ts = _now()
    status = normalize_status_input(status)   # accept `new` as the alias for `open`
    tid = str(uuid.uuid4())
    t = {
        "id": tid,
        # The id has always been a uuid4; `uuid` surfaces it as an explicit,
        # immutable, in-band identifier (uuid == id, never mutated). Existing
        # tasks get it backfilled by SqliteBackend._ensure_task_meta.
        "uuid": tid,
        "title": title.strip() or "Untitled task",
        "summary": summary.strip(),
        "status": status if status in STATUS_BOARD else STATUS_DEFAULT,
        "created_ts": ts,
        "created_at": _iso(ts),
        "updated_ts": ts,
        "updated_at": _iso(ts),
        "sessions": [],
        "log": [],
    }
    c = cat_color(color)
    if c is not None:
        t["color"] = c
    e = normalize_effort(effort)
    if e is not None:
        t["effort"] = e
    return t


# ---------------------------------------------------------------- briefing ----
# A task's "briefing" is derived/curated context that makes a resume load where
# the work STANDS — never via an LLM. Three sources:
#   • files  — recently-edited paths, captured deterministically by the
#     PostToolUse hook (touch-file), deduped + capped, most-recent-last.
#   • state  — a short MODEL-curated "where it stands / next step" string,
#     maintained with `update --state` (the model is already in the loop).
#   • prs    — DERIVED on render by scanning the activity log/summary/state for
#     PR URLs (GitHub + Azure DevOps); never stored.

def extract_prs(task):
    """PR URLs mentioned anywhere in a task's text (activity-log notes, summary,
    state), de-duplicated in first-seen order. Derived on render — never stored.
    Ignores non-PR URLs (only `/pull/<n>` and `/pullrequest/<n>` forms match)."""
    parts = [e.get("note", "") for e in task.get("log", [])]
    parts.append(task.get("summary") or "")
    parts.append(task.get("state") or "")
    text = "\n".join(p for p in parts if p)
    seen, out = set(), []
    for m in _PR_URL_RE.finditer(text):
        u = m.group(0)
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def append_step(task, text):
    """Append a checklist step {"text":…, "done":False} to the task's `steps` — the
    granular, stable-indexed digest field. Blank text is a silent no-op. Returns
    True when a step was added so callers can report it."""
    text = (text or "").strip()
    if not text:
        return False
    task.setdefault("steps", []).append({"text": text, "done": False})
    return True


def set_step_done(task, n, done):
    """Tick/untick the 1-based step `n` (done=True/False). Out-of-range, a non-int `n`,
    or a SUPERSEDED step are all a safe no-op (returns False) — a bad index never
    crashes, so a stray `--step-done 99` just warns rather than blowing up the update.
    Callers that want to say WHY it refused read `steps.set_done` directly."""
    ok, _err = _steps.set_done(task.get("steps") or [], n, done)
    return ok


def step_progress(task):
    """(done, total) over the task's ACTIVE checklist steps; (0, 0) when it has none.
    The single source of truth for the progress rollup on both boards + detail.

    A SUPERSEDED step counts in neither number (see `steps.progress`): a stale step left
    in the denominator makes a task read as permanently unfinished, which is exactly the
    pressure that gets a step nobody did ticked done."""
    return _steps.progress(task.get("steps"))


def append_decision(task, text, session=None):
    """Append to the task's append-only `decisions` log (choices made as the work
    progressed). Blank is a no-op; returns True when appended. `session` attributes
    the emitted decision event to its author."""
    text = (text or "").strip()
    if not text:
        return False
    task.setdefault("decisions", []).append(text)
    add_event(task, "decision", text, session)
    return True


def append_history(task, text, session=None):
    """Append a dated milestone/finding to the task's append-only `history` (the
    model-facing trail: what shipped, findings, "why" notes worth keeping). Stored
    as `{"ts","text"}`; blank is a no-op; returns True when appended. Written via
    `update --log`. This is DELIBERATELY separate from the activity log (`log`,
    per-prompt entries) and is NOT rendered on the default resume path — it loads
    into context only via `/todo <n> history`, so the ever-growing trail never
    bloats a resume. Back-compat: the field is created on first append."""
    text = (text or "").strip()
    if not text:
        return False
    task.setdefault("history", []).append({"ts": _iso(_now()), "text": text})
    add_event(task, "milestone", text, session)
    return True


def append_related(task, other, kind):
    """Record a task→task relation edge on `task` (the child, for spawned-from; or the
    task `--relate` ran on). Idempotent — a no-op returning False when an edge with the
    same target `id` AND `kind` already exists, so re-running never duplicates. Reverse
    edges ("spawned #N", "related ← #N") are DERIVED by scanning other tasks' `related`
    lists, never stored bidirectionally (no consistency drift). Does NOT save — the
    caller persists. Back-compat: `related` is created on first append."""
    rel = task.setdefault("related", [])
    if any(r.get("id") == other.get("id") and r.get("kind") == kind for r in rel):
        return False
    rel.append({"id": other.get("id"), "seq": other.get("seq"), "kind": kind, "ts": _now()})
    return True


# --- the typed-edge write surface --------------------------------------------
#
# THE UNIFYING RULE, so there is one thing to remember rather than five: the
# SUBORDINATE side stores the edge — the dependent, the child, the absorbed task.
# That is already `spawned-from`'s child→origin convention, and it is what keeps
# every one of these a single-task write with a derived reverse direction.
#
# `--relate` and the `related` kind are untouched: 23 live edges still need their
# writer until a separate migration converts them.

def _looks_foreign_ref(ref):
    """Whether `ref` is shaped like another brain's task handle rather than a local
    seq/id. See the ownership rule above — this is the hook sync extends."""
    return bool(_FOREIGN_HANDLE_RE.match((ref or "").strip()))


def _resolve_edge_ref(ref, self_id, flag, kind):
    """Resolve one `<flag> <ref>` to a target task, or explain why not.

    Returns `(task, None)` on success and `(None, reason)` otherwise. The three
    refusals, all non-fatal so a batch keeps going: no such task; a SELF-edge (a task
    depending on / parenting / duplicating itself is meaningless, not a judgement
    call — `_add_undirected` already drops `a == b`); and a foreign ref for a
    local-only kind."""
    other = resolve_ref(ref) or load_task(ref)
    if not other:
        if kind in _LOCAL_ONLY_KINDS and _looks_foreign_ref(ref):
            return None, ("ignoring %s %s — %s accepts LOCAL tasks only: it is computed "
                          "over, and compute needs freshness no foreign edge can promise"
                          % (flag, ref, flag))
        return None, "ignoring %s %s (no such task)" % (flag, ref)
    if other.get("id") == self_id:
        return None, ("ignoring %s %s (a task can't point %s at itself)"
                      % (flag, ref, flag))
    return other, None


def _relation_cycle_path(task, other, kind, tasks=None):
    """The cycle that adding `task --kind--> other` would close, as a list of seqs
    reading `[task, other, …, task]`, or None when it closes none.

    Walks only STORED edges of the same `kind`, starting at `other` and looking for a
    way back to `task`. Cheap by construction — the whole store holds a few dozen
    relation entries — and it is why the write can warn without refusing.

    STRUCTURALLY INCOMPLETE, deliberately: this sees only the edge being written now,
    so a cycle closed by a later write on a DIFFERENT task goes unnoticed here. The
    complete answer is a topological sort over the whole graph, which does not exist
    yet; when it does, it — not this — becomes the authority.

    CYCLE-SAFE BY CONSTRUCTION: the `seen` set is load-bearing, not an optimisation.
    A `parent` cycle is allowed to exist in the store (the write always succeeds), so
    naive recursion over a parent chain does not terminate. Every future walker of
    these edges needs the same guard."""
    scan = tasks if tasks is not None else all_tasks()
    by_id = {t.get("id"): t for t in scan}
    start, goal = other.get("id"), task.get("id")
    if not start or not goal:
        return None
    stack, seen = [(start, [start])], set()
    while stack:
        cur, path = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        node = by_id.get(cur)
        if node is None:
            continue
        for e in (node.get("related") or []):
            if e.get("kind") != kind:
                continue
            nxt = e.get("id")
            if not nxt:
                continue
            if nxt == goal:
                seqs = [(by_id.get(i) or {}).get("seq") for i in path]
                return [task.get("seq")] + seqs + [task.get("seq")]
            stack.append((nxt, path + [nxt]))
    return None


def _parent_children(task, tasks=None):
    """Tasks whose stored `parent` edge points at `task` — its children in the tree,
    derived like every other reverse direction. Sorted by seq for a stable notice."""
    tid = task.get("id")
    kids = [t for t in (tasks if tasks is not None else all_tasks())
            if t.get("id") != tid
            and any(r.get("kind") == "parent" and r.get("id") == tid
                    for r in (t.get("related") or []))]
    return sorted(kids, key=lambda t: t.get("seq") if t.get("seq") is not None else 1 << 30)


def remove_related(task, other_id):
    """Drop EVERY stored edge from `task` to `other_id`, whatever its kind, and return
    the sorted list of kinds removed ([] when there was none). The one removal verb:
    an edge is a statement of PRESENT STRUCTURE, not a historical belief, so unlike a
    decision it must be correctable rather than superseded.

    Only ever touches this task's own list. It cannot reach the DERIVED reverse
    direction and must not try — that edge is owned by the task that stored it."""
    rel = task.get("related") or []
    kinds = sorted({r.get("kind") or "related" for r in rel if r.get("id") == other_id})
    if kinds:
        task["related"] = [r for r in rel if r.get("id") != other_id]
    return kinds


# -- digest staleness: cheap dirty-tracking for opt-in auto-checkpointing ------
# `digest_dirty` marks that SUBSTANTIVE work (a real file edit, a delegate/worktree
# promotion) happened since the structured digest was last refreshed. It's SET on
# that work and CLEARED whenever the digest is refreshed (an `update` that touches
# goal/state/steps/decisions/log, or `/todo save`). Only the opt-in Stop nudge
# reads it (see cmd_stop_nudge); a stale digest is otherwise inert. Back-compat: a
# task with no `digest_dirty` field reads as NOT stale.

def mark_digest_dirty(task):
    """Flag the task's digest as stale relative to real work. Returns True only
    when it FLIPS a previously-clean task to dirty, so callers can skip a needless
    save when it was already marked."""
    if task is None or task.get("digest_dirty"):
        return False
    task["digest_dirty"] = True
    return True


def clear_digest_dirty(task):
    """Clear the staleness flag AND the milestone event counter once the digest is
    refreshed — a refresh makes the digest current again, so both the boolean-stale
    signal and the "events since last refresh" tally reset together. Returns True when
    it cleared either (so callers still know to persist)."""
    if task is None:
        return False
    changed = False
    if task.get("digest_dirty"):
        task["digest_dirty"] = False
        changed = True
    if task.get("digest_events"):
        task["digest_events"] = 0
        changed = True
    return changed


def bump_digest_events(task):
    """Count one meaningful event (a file edit, or a status promotion) toward the
    milestone staleness nudge — the "N events since the last digest refresh" tally that
    checkpoint_milestone_edits gates on. Paired with mark_digest_dirty at every
    substantive-work site; reset to 0 by clear_digest_dirty on a refresh. Best-effort:
    a None task is a no-op."""
    if task is None:
        return
    task["digest_events"] = digest_events(task) + 1


def digest_events(task):
    """How many meaningful events have accrued since the digest was last refreshed.
    Back-compat: a missing `digest_events` field (older tasks) reads as 0."""
    try:
        return int((task or {}).get("digest_events") or 0)
    except (TypeError, ValueError):
        return 0


def digest_stale(task):
    """True when the task's digest looks stale relative to real work. Back-compat:
    a missing `digest_dirty` field reads as not stale."""
    return bool(task and task.get("digest_dirty"))


# -- Obsidian export staleness: mirrors digest_dirty ---------------------------
# `obsidian_dirty` marks that a task's Obsidian note FAILED to write — most often a
# sandboxed in-session export denied by macOS TCC when the vault sits under a
# protected root (os.replace → EPERM). Without it the vault silently drifts stale
# with no record of which tasks need re-syncing. SET by the export hook
# (_obsidian_sync) on ANY export failure while a vault IS configured; CLEARED on a
# later successful export (the hook, `obsidian --sync-all`, or the cheaper
# `obsidian --flush`). Back-compat: a task with no `obsidian_dirty` field reads as
# NOT dirty. Written via a direct save_task (which does NOT re-trigger export), so
# the bookkeeping never loops — exactly as clear_digest_dirty avoids re-triggering.

def mark_obsidian_dirty(task):
    """Flag the task's Obsidian note as pending-resync after a failed export.
    Returns True only when it FLIPS a previously-clean task to dirty, so callers
    can skip a needless save when it was already marked."""
    if task is None or task.get("obsidian_dirty"):
        return False
    task["obsidian_dirty"] = True
    return True


def clear_obsidian_dirty(task):
    """Clear the pending-resync flag once the note exports cleanly. Returns True
    only when it actually cleared a set flag."""
    if task is not None and task.get("obsidian_dirty"):
        task["obsidian_dirty"] = False
        return True
    return False


def obsidian_dirty(task):
    """True when the task's Obsidian note is pending resync. Back-compat: a missing
    `obsidian_dirty` field reads as NOT dirty."""
    return bool(task and task.get("obsidian_dirty"))


# -- proactive context-pressure marker -----------------------------------------
# `pressure_nudged` gates the PROACTIVE checkpoint nudge (cmd_stop_nudge): SET when we
# emit the "run /todo save NOW" nudge (session grew past --checkpoint-at), CLEARED by a
# `/todo save`. Held set until a save clears it so one pressure nudge STANDS per episode
# — the model isn't spammed every turn if it defers. `/todo save` also records
# `last_full_save_ts` so a full structured checkpoint is distinguishable from a lighter
# `--state` refresh. Back-compat: absent fields read as "not yet nudged".

def mark_pressure_nudged(task):
    """Flag that a proactive context-pressure nudge has been emitted this episode.
    Returns True only when it FLIPS a task that wasn't already nudged (so the caller
    can skip a needless save)."""
    if task is None or task.get("pressure_nudged"):
        return False
    task["pressure_nudged"] = True
    return True


def clear_pressure_nudged(task):
    """Clear the pressure-nudge marker (a `/todo save` acted on the nudge). Returns
    True only when it actually cleared a set flag."""
    if task is not None and task.get("pressure_nudged"):
        task["pressure_nudged"] = False
        return True
    return False


def _pr_entry(item):
    """Normalize ONE stored pr entry to `{"url","desc"}`. Back-compat (1.18 and
    earlier stored bare url strings): a plain string loads as `{url, desc:""}`."""
    if isinstance(item, dict):
        return {"url": (item.get("url") or "").strip(),
                "desc": (item.get("desc") or "").strip()}
    return {"url": (str(item).strip() if item else ""), "desc": ""}


def _normalize_prs(prs):
    """A stored `prs` list (mix of strings and {url,desc} dicts) → a clean
    `[{url,desc}, …]` list, deduped by url in first-seen order, blanks dropped."""
    out, seen = [], set()
    for e in (prs or []):
        ent = _pr_entry(e)
        if ent["url"] and ent["url"] not in seen:
            seen.add(ent["url"])
            out.append(ent)
    return out


def add_pr(task, url, desc=""):
    """Upsert a PR on the task, keyed by url, stored as `{"url","desc"}`. Unlike
    extract_prs — which DERIVES urls from the log/summary/state at render time —
    this is an EXPLICIT stored entry (`update --pr [--pr-desc]`). Adds the url if
    new; if it already exists and a non-empty `desc` is given, updates the desc.
    Normalizes any legacy bare-string entries in place. Blank url is a no-op;
    returns True when the stored list changed."""
    url = (url or "").strip()
    if not url:
        return False
    desc = (desc or "").strip()
    prs = _normalize_prs(task.get("prs"))
    changed = False
    for ent in prs:
        if ent["url"] == url:
            if desc and ent["desc"] != desc:
                ent["desc"] = desc
                changed = True
            break
    else:
        prs.append({"url": url, "desc": desc})
        changed = True
    task["prs"] = prs
    return changed


def set_pr_desc(task, desc):
    """Set `desc` on the MOST-RECENT stored pr (the `--pr-desc` without `--pr`
    case). No-op (returns False) when the task has no stored pr or the desc is
    unchanged. Normalizes legacy entries in place."""
    prs = _normalize_prs(task.get("prs"))
    if not prs:
        return False
    desc = (desc or "").strip()
    if prs[-1]["desc"] == desc:
        return False
    prs[-1]["desc"] = desc
    task["prs"] = prs
    return True


def merged_prs(task):
    """The task's PRs for rendering as `[{"url","desc"}, …]`: the STORED `prs`
    (from `update --pr`) merged with the DERIVED urls extract_prs() finds in the
    log/summary/state — deduped by url, stored-first then first-seen. Derived
    urls carry desc="". The 1.15 auto-extraction is preserved but folded into the
    stored list instead of deriving a fresh list each render."""
    out, seen = [], set()
    for ent in _normalize_prs(task.get("prs")):
        seen.add(ent["url"])
        out.append(ent)
    for u in extract_prs(task):
        if u not in seen:
            seen.add(u)
            out.append({"url": u, "desc": ""})
    return out


def _story_entry(item):
    """Normalize ONE stored story entry to `{"url","desc"}`. Back-compat: a plain
    string loads as `{url, desc:""}` (mirrors _pr_entry)."""
    if isinstance(item, dict):
        return {"url": (item.get("url") or "").strip(),
                "desc": (item.get("desc") or "").strip()}
    return {"url": (str(item).strip() if item else ""), "desc": ""}


def _normalize_stories(stories):
    """A stored `stories` list (mix of strings and {url,desc} dicts) → a clean
    `[{url,desc}, …]` list, deduped by url in first-seen order, blanks dropped
    (mirrors _normalize_prs)."""
    out, seen = [], set()
    for e in (stories or []):
        ent = _story_entry(e)
        if ent["url"] and ent["url"] not in seen:
            seen.add(ent["url"])
            out.append(ent)
    return out


def add_story(task, url, desc=""):
    """Upsert a story/work-item link on the task, keyed by url, stored as
    `{"url","desc"}` (mirrors add_pr). Adds the url if new; if it already exists and
    a non-empty `desc` is given, updates the desc. Normalizes any legacy bare-string
    entries in place. Blank url is a no-op; returns True when the stored list changed."""
    url = (url or "").strip()
    if not url:
        return False
    desc = (desc or "").strip()
    stories = _normalize_stories(task.get("stories"))
    changed = False
    for ent in stories:
        if ent["url"] == url:
            if desc and ent["desc"] != desc:
                ent["desc"] = desc
                changed = True
            break
    else:
        stories.append({"url": url, "desc": desc})
        changed = True
    task["stories"] = stories
    return changed


def set_story_desc(task, desc):
    """Set `desc` on the MOST-RECENT stored story (the `--story-desc` without
    `--story` case; mirrors set_pr_desc). No-op (returns False) when the task has no
    stored story or the desc is unchanged. Normalizes legacy entries in place."""
    stories = _normalize_stories(task.get("stories"))
    if not stories:
        return False
    desc = (desc or "").strip()
    if stories[-1]["desc"] == desc:
        return False
    stories[-1]["desc"] = desc
    task["stories"] = stories
    return True


def merged_stories(task):
    """The task's stories for rendering as `[{"url","desc"}, …]` — the STORED
    `stories` (from `update --story`), normalized + deduped by url. Unlike merged_prs
    there is NO derived/auto-extracted source (stories carry no URL pattern to scrape);
    auto-extraction is intentionally skipped."""
    return _normalize_stories(task.get("stories"))


def _story_refs(task):
    """The task's stories as `[{"id","url"}, …]` for the board STORY column — the
    derived story id + its ADO url (None when the entry is a bare id) per stored story,
    deduped by id in first-seen order. Uses obsidian_sync.story_ref so the board id
    matches the story-hub id. Guarded — a missing obsidian_sync degrades to []."""
    try:
        import obsidian_sync
    except Exception:
        return []
    out, seen = [], set()
    for ent in merged_stories(task):
        sid, url = obsidian_sync.story_ref(ent)
        if not sid or sid in seen:
            continue
        seen.add(sid)
        out.append({"id": sid, "url": url})
    return out


def append_edited_file(task, path):
    """Record `path` as a recently-edited file on the task: dedup (move an
    existing entry to most-recent-last) and cap at FILES_KEEP. Returns True if the
    files list changed. Best-effort — a blank path is a silent no-op."""
    path = (path or "").strip()
    if not path:
        return False
    files = task.setdefault("files", [])
    if files and files[-1] == path:
        return False                       # already the most-recent — nothing to do
    try:
        files.remove(path)                 # de-dup: drop the older mention…
    except ValueError:
        pass
    files.append(path)                     # …and re-add as most-recent-last
    del files[:-FILES_KEEP]                # cap to the last FILES_KEEP
    return True


# ------------------------------------------------------------- glossary ---------
# A per-task canonical vocabulary. Each term is {name, layer, state, def}; `name` is
# unique per task case-insensitively. The list rides the task JSON (no migration) —
# mutations go through mutate() and flow into the Tasktrail stream as task.updated,
# and the flattened terms feed task_search_text (see store.task_search_text).

def _glossary_entry(item):
    """Normalize ONE stored term to `{name, layer, state, def}`. A bare string loads
    as a name-only term (mirrors _pr_entry / _story_entry)."""
    if isinstance(item, dict):
        return {"name": (item.get("name") or "").strip(),
                "layer": (item.get("layer") or "").strip(),
                "state": (item.get("state") or "").strip(),
                "def": (item.get("def") or "").strip()}
    return {"name": (str(item).strip() if item else ""), "layer": "", "state": "", "def": ""}


def _normalize_glossary(glossary):
    """A stored `glossary` list → a clean `[{name,layer,state,def}, …]`, deduped by
    case-insensitive name in first-seen order, name-less entries dropped."""
    out, seen = [], set()
    for e in (glossary or []):
        ent = _glossary_entry(e)
        key = ent["name"].lower()
        if ent["name"] and key not in seen:
            seen.add(key)
            out.append(ent)
    return out


def add_glossary_term(task, name, layer="", state="", definition=""):
    """Upsert a term keyed by case-insensitive `name`. New name → append; existing
    name → overwrite its layer/state/def (canonical casing preserved — use
    edit_glossary_term(rename=…) to recase). Blank name is a no-op. Returns True when
    the stored list changed."""
    name = (name or "").strip()
    if not name:
        return False
    layer = (layer or "").strip(); state = (state or "").strip()
    definition = (definition or "").strip()
    gloss = _normalize_glossary(task.get("glossary"))
    key = name.lower()
    changed = False
    for ent in gloss:
        if ent["name"].lower() == key:
            for f, v in (("layer", layer), ("state", state), ("def", definition)):
                if ent[f] != v:
                    ent[f] = v; changed = True
            break
    else:
        gloss.append({"name": name, "layer": layer, "state": state, "def": definition})
        changed = True
    task["glossary"] = gloss
    return changed


def edit_glossary_term(task, name, layer=None, state=None, definition=None, rename=None):
    """Edit an EXISTING term (matched case-insensitively by `name`). Only non-None
    fields change. `rename` sets a new canonical name — rejected (ValueError) if it
    would collide with a DIFFERENT existing term. Returns None when no such term,
    else True/False for changed/unchanged."""
    name = (name or "").strip()
    gloss = _normalize_glossary(task.get("glossary"))
    key = name.lower()
    target = next((e for e in gloss if e["name"].lower() == key), None)
    if target is None:
        return None
    changed = False
    if rename is not None:
        newname = rename.strip()
        if newname and newname != target["name"]:
            if newname.lower() != key and any(
                    e is not target and e["name"].lower() == newname.lower() for e in gloss):
                raise ValueError("a term named %r already exists" % newname)
            target["name"] = newname; changed = True
    for f, v in (("layer", layer), ("state", state), ("def", definition)):
        if v is not None and target[f] != v.strip():
            target[f] = v.strip(); changed = True
    task["glossary"] = gloss
    return changed


def remove_glossary_term(task, name):
    """Remove a term by case-insensitive name. Returns True when one was removed."""
    key = (name or "").strip().lower()
    gloss = _normalize_glossary(task.get("glossary"))
    kept = [e for e in gloss if e["name"].lower() != key]
    if len(kept) == len(gloss):
        return False
    task["glossary"] = kept
    return True


def _glossary_pill(ent):
    """The '[layer·state]' pill for a term (either half may be absent)."""
    tag = "·".join([p for p in (ent["layer"], ent["state"]) if p])
    return " [%s]" % tag if tag else ""


def _format_glossary(task):
    """The `glossary list` view: a header + one `• name [layer·state] — def` line per
    term (empty case names the add command)."""
    gloss = _normalize_glossary(task.get("glossary"))
    seq = task.get("seq", task["id"][:8])
    head = 'Glossary — task #%s "%s"' % (seq, task.get("title") or "")
    if not gloss:
        return ('%s: (empty)\n'
                'Add one: task-station glossary add "<name>" <layer> <state> "<def>" --task %s'
                % (head, seq))
    lines = ["%s (%d term%s):" % (head, len(gloss), "" if len(gloss) == 1 else "s")]
    for e in gloss:
        d = (" — " + e["def"]) if e["def"] else ""
        lines.append("  • %s%s%s" % (e["name"], _glossary_pill(e), d))
    return "\n".join(lines)


def glossary_context(task):
    """The injectable glossary block for `task` — '' when the task is None or has no
    terms. A header + one `• name [layer·state] — def` line per term + a one-line
    capture instruction so the model coins new terms through `glossary add`. Host-
    agnostic: Claude appends it in cmd_prompt_context; other hosts emit it via the
    `glossary-context` adapter hook through their own prompt channel."""
    gloss = _normalize_glossary(task.get("glossary")) if task else []
    if not gloss:
        return ""
    seq = task.get("seq", task["id"][:8])
    lines = ["GLOSSARY (task %s) — use these canonical terms verbatim in plans, "
             "ADO items, and dialogue:" % seq]
    for e in gloss:
        d = (" — " + e["def"]) if e["def"] else ""
        lines.append("• %s%s%s" % (e["name"], _glossary_pill(e), d))
    lines.append('New canonical concept coined? Add it:  '
                 'task-station glossary add "<name>" <layer> <state> "<def>"')
    return "\n".join(lines)
